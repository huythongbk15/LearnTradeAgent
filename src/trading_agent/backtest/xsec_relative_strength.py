"""H-XSEC-01 D1: weekly relative strength across a frozen coin universe.

Implements the design frozen in docs/proposals/H_XSEC_01_RELATIVE_STRENGTH.md.
The candidate is a portfolio rule with its own small simulator, not a Strategy:
it is never registered, never promoted and unreachable from the runner.
portfolio_backtest is not reused because it evaluates promoted per-symbol
runtimes through the allocator, while this design compares one fixed rule with
fixed control arms.

Only the D1 screen can run here. The cutoff is a constant that ends before the
holdout, so nothing in this module can read the holdout or the forward cohort.

Pending packets cannot run. Approval is scope control, not authentication;
operator provenance must be reviewed outside these editable local files.
"""
from __future__ import annotations

import argparse
import bisect
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time as clock_time, timedelta
import hashlib
import json
import math
from pathlib import Path
import random
import statistics
import time

import polars as pl

from trading_agent.backtest.campaign_execution import _durable_json
from trading_agent.backtest.campaign_integrity import capture_campaign_source
from trading_agent.backtest.fill_cycle_development import digest

ROOT = Path(__file__).resolve().parents[3]
SCOPE = "h-xsec-01-d1-v1"
PROPOSAL = "docs/proposals/H_XSEC_01_RELATIVE_STRENGTH.md"
PROPOSAL_SHA256 = "2393194a36536c3db3f05dd8d3273c6bd13d9ed394b0eced41aa69e30d5b2de5"
RESEARCH_MANIFEST = "data/research_manifest.json"
RAW_DATA = "data/raw/binance"

UNIVERSE = tuple(f"{coin}/USDT" for coin in (
    "ADA", "AVAX", "BNB", "BTC", "DOGE", "DOT", "ETH",
    "LINK", "NEAR", "SOL", "TRX", "XRP", "ZEC",
))
LOOKBACK_DAYS = 21
TOP_K = 4
TOTAL_EXPOSURE = 0.25
INITIAL_EQUITY = 10_000.0
MIN_TRADE_USDT = 5.0
RANDOM_SEEDS = tuple(range(1, 21))
RANDOM_ARMS = tuple(f"random_{seed:02d}" for seed in RANDOM_SEEDS)
SUPPORT_CONTROLS = 18  # of the 20 random controls, at 2x

FIRST_REBALANCE = date(2023, 1, 23)
LAST_REBALANCE = date(2026, 2, 2)
REBALANCES = 159
FILL_HOUR = 1  # data through Sunday's close, skip one bar, fill at open: k+2
CUTOFF = datetime(2026, 2, 6, 12, tzinfo=UTC)  # end of the last D1 bar, before the holdout
MARK_BAR = CUTOFF - timedelta(hours=1)
STOPPED_AFTER = timedelta(hours=24)  # no bar for this long before the mark: not trading


class InvalidRun(ValueError):
    """The frozen design says this run has no result; it is not a strategy loss."""


@dataclass(frozen=True)
class Cost:
    fee: float
    slippage: float


COSTS = {
    "1x": Cost(0.0010, 0.0005),
    "2x": Cost(0.0020, 0.0010),
    "slip_stress": Cost(0.0010, 0.0025),
}


@dataclass(frozen=True)
class Series:
    """One coin's closed bars, already cut before CUTOFF."""

    daily: Mapping[date, float]  # UTC day -> close
    stamps: Sequence[datetime]  # 1h bar open times, strictly increasing
    opens: Sequence[float]
    closes: Sequence[float]

    def first_open(self, at: datetime, until: datetime) -> float | None:
        """Open of the first bar opening at or after ``at``, if not past ``until``."""
        index = bisect.bisect_left(self.stamps, at)
        if index == len(self.stamps) or self.stamps[index] > until:
            return None
        return self.opens[index]

    def last_close(self, at: datetime) -> float:
        """Close of the latest bar opening at or before ``at``."""
        index = bisect.bisect_right(self.stamps, at) - 1
        if index < 0:
            raise InvalidRun(f"no 1h bar at or before {at.isoformat()}")
        return self.closes[index]


@dataclass(frozen=True)
class Step:
    at: datetime
    universe: tuple[str, ...]
    ranked: tuple[str, ...]  # the candidate's coins this week


@dataclass(frozen=True)
class Fill:
    at: str
    symbol: str
    side: str
    quantity: float
    reference: float
    price: float
    fee: float

    @property
    def notional(self) -> float:
        return self.quantity * self.price


@dataclass(frozen=True)
class ArmResult:
    arm: str
    cost: str
    final_equity: float
    positions: Mapping[str, float]
    marks: Mapping[str, float]
    fills: tuple[Fill, ...]


def rebalance_days() -> list[date]:
    return [FIRST_REBALANCE + timedelta(weeks=week) for week in range(REBALANCES)]


def relative_strength(series: Series, sunday: date) -> float:
    start = sunday - timedelta(days=LOOKBACK_DAYS)
    if sunday not in series.daily or start not in series.daily:
        raise InvalidRun(f"a daily close needed for ranking is missing at {sunday}")
    now, then = series.daily[sunday], series.daily[start]
    if not all(math.isfinite(value) and value > 0 for value in (now, then)):
        raise InvalidRun(f"a daily close needed for ranking is invalid at {sunday}")
    return now / then - 1


def top_coins(market: Mapping[str, Series], universe: Sequence[str], monday: date) -> tuple[str, ...]:
    sunday = monday - timedelta(days=1)
    scores = {symbol: relative_strength(market[symbol], sunday) for symbol in universe}
    return tuple(sorted(universe, key=lambda symbol: (-scores[symbol], symbol))[:TOP_K])


def plan_steps(
    market: Mapping[str, Series],
    mondays: Sequence[date] | None = None,
    *,
    mark_bar: datetime = MARK_BAR,
) -> list[Step]:
    """The weekly decisions, shared by every arm and every cost."""
    universe = sorted(market)
    steps = []
    for monday in rebalance_days() if mondays is None else mondays:
        at = datetime.combine(monday, clock_time(FILL_HOUR), UTC)
        universe = [s for s in universe if market[s].first_open(at, mark_bar) is not None]
        if len(universe) < TOP_K:
            raise InvalidRun(f"fewer than {TOP_K} coins remain tradable at {monday}")
        steps.append(Step(at, tuple(universe), top_coins(market, universe, monday)))
    return steps


def arm_picks(steps: Sequence[Step]) -> dict[str, list[tuple[str, ...]]]:
    """Each arm's coins per step. A control draws from one generator, in time order."""
    picks = {
        "candidate": [step.ranked for step in steps],
        "basket": [step.universe for step in steps],
    }
    for seed, arm in zip(RANDOM_SEEDS, RANDOM_ARMS):
        generator = random.Random(seed)
        picks[arm] = [
            tuple(sorted(generator.sample(sorted(step.universe), TOP_K))) for step in steps
        ]
    return picks


def _fill(at: datetime, symbol: str, side: str, quantity: float, reference: float, cost: Cost) -> Fill:
    price = reference * (1 + cost.slippage if side == "BUY" else 1 - cost.slippage)
    return Fill(at.isoformat(), symbol, side, quantity, reference, price, quantity * price * cost.fee)


def _cash_change(fill: Fill) -> float:
    return -fill.notional - fill.fee if fill.side == "BUY" else fill.notional - fill.fee


def simulate(
    market: Mapping[str, Series],
    steps: Sequence[Step],
    picks: Sequence[tuple[str, ...]],
    cost: Cost,
    *,
    arm: str,
    cost_name: str,
    mark_bar: datetime = MARK_BAR,
) -> ArmResult:
    """Trade only the difference from each week's target, on one cash balance."""
    cash = INITIAL_EQUITY
    held: dict[str, float] = {}
    fills: list[Fill] = []

    def book(fill: Fill) -> None:
        nonlocal cash
        cash += _cash_change(fill)
        fills.append(fill)
        change = fill.quantity if fill.side == "BUY" else -fill.quantity
        remaining = held.get(fill.symbol, 0.0) + change
        if remaining <= 1e-12:
            held.pop(fill.symbol, None)
        else:
            held[fill.symbol] = remaining

    def close_out(at: datetime, symbol: str) -> None:
        book(_fill(at, symbol, "SELL", held[symbol], market[symbol].last_close(at), cost))

    for step, chosen in zip(steps, picks, strict=True):
        for symbol in sorted(set(held) - set(step.universe)):  # stopped trading
            close_out(step.at, symbol)
        prices = {symbol: market[symbol].first_open(step.at, mark_bar) for symbol in step.universe}
        equity = cash + sum(quantity * prices[symbol] for symbol, quantity in held.items())
        target = equity * TOTAL_EXPOSURE / len(chosen)
        changes = {
            symbol: (target if symbol in chosen else 0.0) - held.get(symbol, 0.0) * price
            for symbol, price in prices.items()
        }
        for symbol, change in sorted(changes.items()):
            if change <= -MIN_TRADE_USDT:
                quantity = held[symbol] if symbol not in chosen else -change / prices[symbol]
                book(_fill(step.at, symbol, "SELL", quantity, prices[symbol], cost))
        for symbol, change in sorted(changes.items()):
            if change >= MIN_TRADE_USDT:
                quantity = change / (prices[symbol] * (1 + cost.slippage))
                book(_fill(step.at, symbol, "BUY", quantity, prices[symbol], cost))

    for symbol in sorted(held):
        if market[symbol].stamps[-1] < mark_bar - STOPPED_AFTER:
            close_out(mark_bar, symbol)
    marks = {symbol: market[symbol].last_close(mark_bar) for symbol in held}
    final = cash + sum(quantity * marks[symbol] for symbol, quantity in held.items())
    return ArmResult(arm, cost_name, final, dict(held), marks, tuple(fills))


def reconcile(result: ArmResult) -> None:
    """Rebuild cash and inventory from the fills alone; fail on any difference."""
    cash = INITIAL_EQUITY
    positions: dict[str, float] = defaultdict(float)
    for fill in result.fills:
        cash += _cash_change(fill)
        positions[fill.symbol] += fill.quantity if fill.side == "BUY" else -fill.quantity
        if positions[fill.symbol] < -1e-9:
            raise ValueError(f"{result.arm}: {fill.symbol} sold more than was held")
    open_positions = {s: q for s, q in positions.items() if q > 1e-12}
    if set(open_positions) != set(result.positions):
        raise ValueError(f"{result.arm}: ledger and final positions hold different coins")
    for symbol, quantity in open_positions.items():
        if not math.isclose(quantity, result.positions[symbol], rel_tol=1e-9):
            raise ValueError(f"{result.arm}: {symbol} quantity differs from the ledger")
    equity = cash + sum(q * result.marks[s] for s, q in open_positions.items())
    if not math.isclose(equity, result.final_equity, rel_tol=1e-9):
        raise ValueError(f"{result.arm}: equity {result.final_equity} != ledger {equity}")


def run_arms(
    market: Mapping[str, Series],
    steps: Sequence[Step],
    *,
    mark_bar: datetime = MARK_BAR,
) -> dict[str, dict[str, ArmResult]]:
    picks = arm_picks(steps)
    results: dict[str, dict[str, ArmResult]] = {}
    for cost_name, cost in COSTS.items():
        results[cost_name] = {}
        for arm, chosen in picks.items():
            result = simulate(
                market, steps, chosen, cost, arm=arm, cost_name=cost_name, mark_bar=mark_bar
            )
            reconcile(result)
            results[cost_name][arm] = result
    return results


def decide(finals: Mapping[str, Mapping[str, float]]) -> dict:
    """The frozen D1 rule, on final equity per cost and per arm."""
    excess = {
        cost: (finals[cost]["candidate"] - finals[cost]["basket"]) / INITIAL_EQUITY
        for cost in COSTS
    }
    beaten = sum(finals["2x"]["candidate"] > finals["2x"][arm] for arm in RANDOM_ARMS)
    median_1x = statistics.median(finals["1x"][arm] for arm in RANDOM_ARMS)
    if excess["2x"] <= 0 or finals["1x"]["candidate"] < median_1x:
        outcome = "REJECT"
    elif all(value > 0 for value in excess.values()) and beaten >= SUPPORT_CONTROLS:
        outcome = "CONTINUE_TO_CONFIRMATION"
    else:
        outcome = "INCONCLUSIVE"
    return {
        "outcome": outcome,
        "excess": excess,
        "random_controls_beaten_at_2x": beaten,
        "random_median_at_1x": median_1x,
    }


def _raw_path(symbol: str, timeframe: str) -> Path:
    return ROOT / RAW_DATA / symbol.replace("/", "_") / f"{timeframe}.parquet"


def _require_increasing(path: Path, column: str) -> None:
    stamps = pl.scan_parquet(path).select(column).collect().to_series()
    if len(stamps) == 0 or not stamps.is_sorted() or stamps.n_unique() != len(stamps):
        raise InvalidRun(f"{path.name} is not strictly increasing in {column}")


def load_series(symbol: str, *, cutoff: datetime = CUTOFF) -> Series:
    """Load one coin. No row at or after the cutoff leaves the parquet reader."""
    hourly_path, daily_path = _raw_path(symbol, "1h"), _raw_path(symbol, "1d")
    naive_cutoff = cutoff.replace(tzinfo=None)
    last_full_day = (cutoff - timedelta(days=1)).date()
    _require_increasing(hourly_path, "timestamp")
    _require_increasing(daily_path, "date")
    hourly = (
        pl.scan_parquet(hourly_path)
        .filter(pl.col("timestamp") < naive_cutoff)
        .select("timestamp", "open", "close")
        .collect()
    )
    daily = (
        pl.scan_parquet(daily_path)
        .filter(pl.col("date") <= last_full_day)
        .select("date", "close")
        .collect()
    )
    return Series(
        daily=dict(zip(daily["date"].to_list(), daily["close"].to_list())),
        stamps=[stamp.replace(tzinfo=UTC) for stamp in hourly["timestamp"].to_list()],
        opens=hourly["open"].to_list(),
        closes=hourly["close"].to_list(),
    )


def require_daily_alignment(market: Mapping[str, Series], mondays: Sequence[date]) -> None:
    """A daily close must be the close of that day's 23:00 bar, or signals see the future."""
    for symbol, series in market.items():
        for monday in mondays:
            sunday = monday - timedelta(days=1)
            bar = datetime.combine(sunday, clock_time(23), UTC)
            index = bisect.bisect_left(series.stamps, bar)
            if index == len(series.stamps) or series.stamps[index] != bar:
                continue  # a missing bar has nothing to compare
            if sunday in series.daily and not math.isclose(
                series.daily[sunday], series.closes[index], rel_tol=1e-6
            ):
                raise InvalidRun(f"{symbol} daily close at {sunday} is not its 23:00 bar close")


def specification() -> dict:
    return {
        "universe": list(UNIVERSE), "lookback_days": LOOKBACK_DAYS, "top_k": TOP_K,
        "total_exposure": TOTAL_EXPOSURE, "initial_equity": INITIAL_EQUITY,
        "min_trade_usdt": MIN_TRADE_USDT, "random_seeds": list(RANDOM_SEEDS),
        "support_controls": SUPPORT_CONTROLS, "first_rebalance": FIRST_REBALANCE.isoformat(),
        "last_rebalance": LAST_REBALANCE.isoformat(), "rebalances": REBALANCES,
        "fill_hour_utc": FILL_HOUR, "cutoff": CUTOFF.isoformat(), "mark_bar": MARK_BAR.isoformat(),
        "costs": {name: asdict(cost) for name, cost in COSTS.items()},
    }


def input_bindings() -> dict:
    """Hash every input as bytes; no row is decoded."""
    manifest = json.loads((ROOT / RESEARCH_MANIFEST).read_bytes())["datasets"]
    bindings = {}
    for symbol in UNIVERSE:
        for timeframe in ("1d", "1h"):
            path = _raw_path(symbol, timeframe)
            sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
            key = f"{symbol.replace('/', '_')}/{timeframe}"
            bindings[str(path.relative_to(ROOT))] = {
                "sha256": sha256,
                "matches_research_manifest": manifest.get(key, {}).get("sha256") == sha256,
            }
    return bindings


def _proposal_sha256() -> str:
    return hashlib.sha256((ROOT / PROPOSAL).read_bytes()).hexdigest()


def command(path: Path) -> list[str]:
    return [".venv/bin/python", "-m", "trading_agent.backtest.xsec_relative_strength",
            "--run", str(path.resolve())]


def _plan_body(path: Path, *, source: dict, inputs: dict, approval: dict) -> dict:
    scope_id = digest({"spec": specification(), "inputs": inputs, "source": source})
    return {
        "scope": SCOPE, "proposal": {"path": PROPOSAL, "sha256": PROPOSAL_SHA256},
        "specification": specification(), "inputs": inputs, "source": source,
        "budget": {"hard_deadline_seconds": 600, "workers": 1, "retries": 0},
        "command": command(path), "expected_approved_scope": f"h-xsec-01-d1:{scope_id}",
        "approval": approval, "market_run_authorized": approval["approval_state"] == "APPROVED",
        "live_authorized": False, "qualified": False,
    }


def freeze(out: Path) -> Path:
    """Write a pending packet. It binds hashes only and cannot run."""
    if out.exists():
        raise ValueError("fresh packet required")
    if _proposal_sha256() != PROPOSAL_SHA256:
        raise ValueError("proposal differs from the frozen design")
    path = out / "protocol.json"
    plan = _plan_body(
        path, source=capture_campaign_source(ROOT), inputs=input_bindings(),
        approval={"approval_state": "PENDING_OPERATOR_APPROVAL", "approved_by": None,
                  "approved_at": None, "approved_scope": None},
    )
    plan["fingerprint"] = digest(plan)
    _durable_json(path, plan)
    _durable_json(out / "binding.json", {"protocol_sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    return path


def verify(path: Path, *, approved: bool = True) -> dict:
    path = path.resolve()
    plan = json.loads(path.read_bytes())
    body = {key: value for key, value in plan.items() if key != "fingerprint"}
    if plan.get("fingerprint") != digest(body):
        raise ValueError("packet fingerprint mismatch")
    binding = json.loads((path.parent / "binding.json").read_bytes())
    if binding != {"protocol_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}:
        raise ValueError("packet binding hash mismatch")
    approval = plan.get("approval", {})
    if set(approval) != {"approval_state", "approved_by", "approved_at", "approved_scope"}:
        raise ValueError("approval schema mismatch")
    state = approval["approval_state"]
    if approved and state != "APPROVED":
        raise PermissionError("NOT_AUTHORIZED_TO_RUN")
    if state == "APPROVED":
        if not isinstance(approval["approved_by"], str) or not approval["approved_by"].strip():
            raise ValueError("approved_by missing")
        if datetime.fromisoformat(approval["approved_at"]).tzinfo is None:
            raise ValueError("approval timestamp must carry timezone")
        if approval["approved_scope"] != plan.get("expected_approved_scope"):
            raise ValueError("approved_scope mismatch")
    elif state != "PENDING_OPERATOR_APPROVAL" or any(
        approval[key] is not None for key in ("approved_by", "approved_at", "approved_scope")
    ):
        raise ValueError("invalid pending approval")
    if _proposal_sha256() != PROPOSAL_SHA256:
        raise ValueError("proposal differs from the frozen design")
    if plan["source"]["tree_fingerprint"] != capture_campaign_source(ROOT)["tree_fingerprint"]:
        raise ValueError("packet source mismatch")
    inputs = input_bindings()
    if body != _plan_body(path, source=plan["source"], inputs=inputs, approval=approval):
        raise ValueError("packet differs from the frozen specification or its inputs")
    unproven = sorted(key for key, value in inputs.items() if not value["matches_research_manifest"])
    if approved and unproven:
        raise PermissionError(f"INPUT_NOT_PROVEN: {', '.join(unproven)}")
    return plan


def _measure() -> dict:
    market = {symbol: load_series(symbol) for symbol in UNIVERSE}
    mondays = rebalance_days()
    require_daily_alignment(market, mondays)
    steps = plan_steps(market, mondays)
    results = run_arms(market, steps)
    finals = {
        cost: {arm: result.final_equity for arm, result in arms.items()}
        for cost, arms in results.items()
    }
    newest = max(max(series.stamps) for series in market.values())
    if newest >= CUTOFF:
        raise InvalidRun("a bar at or after the cutoff reached the simulator")
    ledger = {
        cost: {arm: [asdict(fill) for fill in result.fills] for arm, result in arms.items()}
        for cost, arms in results.items()
    }
    summary = {"status": "COMPLETED", **decide(finals), "finals": finals,
               "rebalances": len(steps), "newest_bar_read": newest.isoformat()}
    return {"summary": summary, "ledger": ledger}


def run(path: Path) -> int:
    started = time.monotonic()
    plan = verify(path)
    root = path.parent
    if (root / "execution_started.json").exists():
        raise ValueError("no resume or retry")
    _durable_json(root / "execution_started.json",
                  {"fingerprint": plan["fingerprint"], "started_at_epoch": time.time()})
    try:
        measured = _measure()
        _durable_json(root / "ledger.json", measured["ledger"])
        summary = measured["summary"]
    except InvalidRun as exc:
        summary = {"status": "INVALID", "reason": str(exc)}
    summary.update(scope=SCOPE, qualified=False, live_authorized=False,
                   elapsed_seconds=time.monotonic() - started)
    if summary["elapsed_seconds"] > plan["budget"]["hard_deadline_seconds"]:
        summary["status"] = "INCOMPLETE"
        summary["budget_reason"] = "TOTAL_DEADLINE_EXCEEDED"
    _durable_json(root / "summary.json", summary)
    return 0 if summary["status"] == "COMPLETED" else 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--prepare", type=Path)
    group.add_argument("--run", type=Path)
    args = parser.parse_args()
    if args.prepare:
        path = freeze(args.prepare.resolve())
        plan = verify(path, approved=False)
        print(json.dumps({"status": "PENDING_OPERATOR_APPROVAL", "protocol": str(path),
                          "fingerprint": plan["fingerprint"]}))
        return 0
    return run(args.run.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
