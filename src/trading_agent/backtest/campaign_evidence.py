"""Schema-v2 campaign evidence: checked measurements, not status assertions.

This is not a promotion certificate. A structurally valid FAIL bundle remains
useful research evidence but cannot authorize policy construction.
"""

from datetime import UTC, datetime
import hashlib
import json
import math
import operator
from pathlib import Path
from statistics import median


COMPARISONS = {
    ">": operator.gt,
    ">=": operator.ge,
    "<": operator.lt,
    "<=": operator.le,
    "==": operator.eq,
    "!=": operator.ne,
}
NUMERIC_RESULTS = (
    "gross_pnl",
    "net_pnl",
    "fees",
    "slippage",
    "spread_cost",
    "return_pct",
    "sharpe",
    "max_dd_pct",
)


def content_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str
        ).encode()
    ).hexdigest()


def finite(value: object) -> bool:
    return type(value) in {int, float} and math.isfinite(value)


def hash_id(value: object, lengths=(64,)) -> bool:
    return (
        isinstance(value, str)
        and len(value) in lengths
        and all(c in "0123456789abcdef" for c in value)
    )


def window_hash(frame) -> str:
    return content_hash({"columns": frame.columns, "rows": frame.to_dicts()})


def utc_aligned(frame):
    """Give naive timestamps an explicit UTC zone before any comparison.

    Stored parquets commonly carry naive timestamps, and _time already reads
    those as UTC. Comparing them against a normalized boundary would raise a
    dtype error instead of matching, so the column is aligned on load. This
    changes no measurement -- it only makes the comparison well typed.
    """
    import polars as pl

    schema = frame.schema
    dtype = schema.get("timestamp") if "timestamp" in schema else None
    if dtype is not None and getattr(dtype, "time_zone", None) is None:
        return frame.with_columns(pl.col("timestamp").dt.replace_time_zone("UTC"))
    return frame


def read_ohlcv(path):
    """Read a parquet of bars and align its timestamp zone."""
    import polars as pl

    return utc_aligned(pl.read_parquet(path))


def _time(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    # Contract spans may be ISO dates; normalize them explicitly, not local time.
    return (
        parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    )


def _span(value) -> tuple[datetime, datetime]:
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError("span must contain two ISO boundaries")
    start, end = map(_time, value)
    if start >= end:
        raise ValueError("span boundaries must be increasing")
    return start, end


def validate_v2(
    bundle: dict,
    *,
    data_root: Path,
    expected_tree_fingerprint: str | None = None,
    expected_gate_set_fingerprint: str | None = None,
) -> list[str]:
    errors = []
    if bundle.get("schema_version") != 2:
        return ["schema_version must be 2; legacy assertions are diagnostic-only"]
    subject = bundle.get("subject")
    if (
        not isinstance(subject, dict)
        or any(
            not isinstance(subject.get(k), str) or not subject[k].strip()
            for k in ("strategy_id", "symbol", "market_type", "timeframe")
        )
        or not hash_id(subject.get("params_hash"))
    ):
        errors.append("missing/invalid subject and selected params_hash")
    if not hash_id(bundle.get("code_commit"), (40, 64)):
        errors.append("invalid code_commit")
    if not hash_id(bundle.get("tree_fingerprint")):
        errors.append("invalid tree_fingerprint")
    if (
        expected_tree_fingerprint is not None
        and bundle.get("tree_fingerprint") != expected_tree_fingerprint
    ):
        errors.append("tree_fingerprint does not match the source under review")
    dirty = bundle.get("worktree_dirty")
    if (
        type(dirty) is not bool
        or (dirty and not hash_id(bundle.get("dirty_diff_sha256")))
        or (dirty is False and bundle.get("dirty_diff_sha256") is not None)
    ):
        errors.append("worktree_dirty/dirty_diff_sha256 binding is invalid")
    data = bundle.get("data_manifest")
    if not isinstance(data, dict):
        errors.append("missing data_manifest")
    else:
        try:
            import polars as pl

            raw_path = data.get("input_path")
            if not isinstance(raw_path, str) or not raw_path.strip():
                raise ValueError("missing input_path")
            path = Path(raw_path)
            path = (path if path.is_absolute() else data_root / path).resolve()
            if not path.is_relative_to(data_root.resolve()):
                raise ValueError("input_path escapes the declared data root")
            if (
                not hash_id(data.get("file_sha256"))
                or hashlib.sha256(path.read_bytes()).hexdigest() != data["file_sha256"]
            ):
                raise ValueError("input_path file_sha256 does not match actual bytes")
            frame = read_ohlcv(path)
            if (
                type(data.get("input_rows")) is not int
                or data["input_rows"] != frame.height
            ):
                raise ValueError("input_rows does not match actual parquet rows")
            window = data.get("window")
            if not isinstance(window, dict) or not {
                "timestamp",
                "open",
                "high",
                "low",
                "close",
                "volume",
            }.issubset(frame.columns):
                raise ValueError("missing OHLCV/window")
            timestamps = frame["timestamp"].to_list()
            if (
                not timestamps
                or any(value is None for value in timestamps)
                or any(left >= right for left, right in zip(timestamps, timestamps[1:]))
            ):
                raise ValueError(
                    "OHLCV timestamps must be nonempty, unique and strictly increasing"
                )
            for opening, high, low, close, volume in frame.select(
                "open", "high", "low", "close", "volume"
            ).iter_rows():
                if (
                    not all(
                        finite(value) for value in (opening, high, low, close, volume)
                    )
                    or min(opening, high, low, close) <= 0
                    or volume < 0
                    or low > min(opening, close)
                    or high < max(opening, close)
                ):
                    raise ValueError(
                        "OHLCV contains nonfinite/invalid prices, range or volume"
                    )
            start, end = _time(window["start"]), _time(window["end"])
            if start > end:
                raise ValueError("window boundaries reversed")
            selected = frame.filter(
                (pl.col("timestamp") >= start) & (pl.col("timestamp") <= end)
            )
            if (
                selected.height == 0
                or type(window.get("bars")) is not int
                or window["bars"] != selected.height
            ):
                raise ValueError("window.bars disagrees with resolved OHLCV window")
            if window_hash(selected) != data.get("data_manifest"):
                raise ValueError("data_manifest does not match resolved OHLCV content")
            if window.get("timeframe") != subject.get("timeframe") or window.get(
                "market"
            ) != subject.get("market_type"):
                raise ValueError("window subject mismatch")
            if data.get("cutoff_policy") not in {
                "fixed",
                "as_of",
                "trailing",
            } or not isinstance(data.get("gaps"), list):
                raise ValueError("missing cutoff_policy/gaps classification")
        except (
            OSError,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            pl.exceptions.PolarsError,
        ) as exc:
            errors.append(f"data_manifest: {exc}")
    cost = bundle.get("cost_schedule")
    if not isinstance(cost, dict) or any(
        not finite(cost.get(k)) or cost[k] < 0
        for k in ("commission_bps", "slippage_bps", "spread_bps", "round_trip_bps")
    ):
        errors.append(
            "cost_schedule commission_bps/slippage_bps/spread_bps/round_trip_bps must all be finite, nonnegative numbers"
        )
    elif not math.isclose(
        cost["round_trip_bps"],
        2 * cost["commission_bps"] + 2 * cost["slippage_bps"] + cost["spread_bps"],
        abs_tol=1e-6,
    ):
        errors.append("cost_schedule.round_trip_bps does not recompute")
    folds, results = bundle.get("folds"), bundle.get("results")
    spans, fold_ids = [], []
    if not isinstance(folds, list) or not folds:
        errors.append("missing folds")
    else:
        for fold in folds:
            try:
                identifier = fold["fold_id"]
                if not isinstance(identifier, str) or not identifier:
                    raise ValueError("missing fold_id")
                fold_ids.append(identifier)
                train, val, test = (
                    _span(fold[key]) for key in ("train_span", "val_span", "test_span")
                )
                if train[1] > val[0] or val[1] > test[0]:
                    raise ValueError("train/validation/test leak into one another")
                if any(
                    type(fold.get(key)) is not int or fold[key] < 0
                    for key in ("purge_bars", "embargo_bars")
                ):
                    raise ValueError("missing purge_bars/embargo_bars")
                spans.append((test, fold.get("overlap_group")))
            except (TypeError, KeyError, ValueError) as exc:
                errors.append(f"fold: {exc}")
        if len(set(fold_ids)) != len(fold_ids) or bundle.get("fold_count") != len(
            folds
        ):
            errors.append("duplicate fold_id or fold_count mismatch")
        for i, (span, group) in enumerate(spans):
            for other, other_group in spans[i + 1 :]:
                if max(span[0], other[0]) < min(span[1], other[1]) and (
                    bundle.get("overlap_policy") != "overlapping"
                    or not group
                    or group != other_group
                ):
                    errors.append(
                        "actual overlapping test spans lack a shared overlap_group"
                    )
    result_ids, usable = [], []
    if not isinstance(results, list) or not results:
        errors.append("missing results")
    else:
        for row in results:
            if not isinstance(row, dict):
                errors.append("malformed result row")
                continue
            result_ids.append(row.get("fold_id"))
            if (
                any(not finite(row.get(key)) for key in NUMERIC_RESULTS)
                or type(row.get("trades")) is not int
                or row["trades"] < 0
            ):
                errors.append("result missing finite metrics/trades")
                continue
            usable.append(row)
            if any(row[key] < 0 for key in ("fees", "slippage", "spread_cost")):
                errors.append("result contains negative execution costs")
            if not math.isclose(
                row["net_pnl"],
                row["gross_pnl"] - row["fees"] - row["slippage"] - row["spread_cost"],
                abs_tol=1e-6,
            ):
                errors.append(
                    "result gross/cost/net accounting bridge does not reconcile"
                )
            if type(row.get("cost_cleared")) is not bool or row["cost_cleared"] != (
                row["net_pnl"] > 0
            ):
                errors.append("result cost_cleared disagrees with measured net PnL")
        if len(set(result_ids)) != len(result_ids) or set(result_ids) != set(fold_ids):
            errors.append(
                "each fold must have exactly one result; duplicate/missing/unknown fold result"
            )
    aggregate = bundle.get("aggregate")
    if not isinstance(aggregate, dict) or not usable:
        errors.append("missing recomputable aggregate")
    else:
        derived = {
            "median_return_pct": median(row["return_pct"] for row in usable),
            "median_sharpe": median(row["sharpe"] for row in usable),
            "total_trades": sum(row["trades"] for row in usable),
        }
        for key, value in derived.items():
            if not finite(aggregate.get(key)) or not math.isclose(
                aggregate[key], value, abs_tol=1e-9
            ):
                errors.append(f"aggregate.{key} does not recompute from fold results")
    gates = bundle.get("gate_set")
    failed = []
    if not isinstance(gates, list) or not gates:
        errors.append("missing frozen gate_set")
    else:
        try:
            if content_hash(gates) != bundle.get("gate_set_fingerprint") or (
                expected_gate_set_fingerprint is not None
                and bundle.get("gate_set_fingerprint") != expected_gate_set_fingerprint
            ):
                errors.append("gate_set_fingerprint does not match frozen thresholds")
            seen = set()
            for gate in gates:
                identifier, metric, comparison = (
                    gate["gate_id"],
                    gate["metric"],
                    gate["comparison"],
                )
                if (
                    not isinstance(identifier, str)
                    or not identifier
                    or identifier in seen
                ):
                    raise ValueError("missing/duplicate gate_id")
                seen.add(identifier)
                if (
                    comparison not in COMPARISONS
                    or not finite(gate["threshold"])
                    or not gate.get("unit")
                ):
                    raise ValueError("invalid gate comparator/threshold/unit")
                if not isinstance(metric, str) or not metric.startswith("aggregate."):
                    raise ValueError(
                        "gate metric must identify a recorded aggregate field"
                    )
                observed = (
                    aggregate.get(metric.removeprefix("aggregate."))
                    if isinstance(aggregate, dict)
                    else None
                )
                if not finite(observed):
                    raise ValueError(f"missing finite observed gate metric {metric}")
                if not COMPARISONS[comparison](observed, gate["threshold"]):
                    failed.append(
                        {
                            "gate_id": identifier,
                            "observed": observed,
                            "unit": gate["unit"],
                            "threshold": gate["threshold"],
                            "comparison": comparison,
                        }
                    )
            if bundle.get("failed_gates") != failed or bundle.get("verdict") != (
                "FAIL" if failed else "PASS"
            ):
                errors.append(
                    f"verdict {bundle.get('verdict')}/failed_gates does not recompute from recorded metrics and thresholds"
                )
        except (TypeError, ValueError, KeyError) as exc:
            errors.append(f"gate_set: {exc}")
    try:
        if bundle.get("campaign_id") != content_hash(
            {key: value for key, value in bundle.items() if key != "campaign_id"}
        ):
            errors.append("campaign_id does not bind bundle content")
    except ValueError:
        errors.append("campaign content contains non-finite numbers")
    return errors
