"""Frozen constants against the page that freezes them, and the path guards."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from tools.selection import config

PAGE = Path(config.__file__).resolve().parents[2] / "docs" / "operations" / "SELECTION_PAPER_RUN.md"


def test_reasons_follow_the_order_of_the_page() -> None:
    assert tuple(re.findall(r"^\| `([A-Z_]+)` \|", PAGE.read_text(encoding="utf8"), re.M)) == config.REASONS


def test_hold_reasons_are_the_three_the_page_names() -> None:
    text = PAGE.read_text(encoding="utf8")
    line = next(x for x in text.splitlines() if "giữ danh sách cũ" in x)
    assert set(re.findall(r"`([A-Z_]+)`", line)) == config.HOLD_REASONS


def test_masked_coins_are_the_twelve_of_the_page() -> None:
    text = PAGE.read_text(encoding="utf8")
    line = next(x for x in text.splitlines() if "bị che hoàn toàn" in x)
    names = line.split(":", 1)[1].split(".")[0]
    assert set(re.findall(r"[A-Z]{2,}", names)) == config.SEALED and len(config.SEALED) == 12 and config.BTC not in config.SEALED


def test_primary_is_the_registered_variant() -> None:
    assert (config.PRIMARY_LOOK, config.PRIMARY_K, config.PRIMARY_CADENCE_WEEKS) == (90, 3, 4)
    assert config.LIST_N == 18 and config.COHORT_WEEKS == 60 and config.TRIPWIRE_Z == -2.5 and config.TRIPWIRE_FROM_WEEK == 8
    assert config.TRIPWIRE_WEEKLY_SD_BPS == 510.0 and config.LATE_LIMIT_HOURS == 6 and config.POLICY_VALIDITY_DAYS == 8
    assert config.MAIN_COST_BASIS in config.COST_BASES and config.MAIN_FILL_CLOCK in config.FILL_CLOCKS


def test_phase_counts_weeks_from_the_first_grid_day() -> None:
    assert [config.phase_of(d) for d in ("2020-07-13", "2020-07-20", "2020-07-27", "2020-08-03", "2020-08-10")] == [0, 1, 2, 3, 0]
    assert config.weeks_since_first("2025-06-09") == 256 and config.phase_of("2025-06-09") == 0     # the study's last list
    with pytest.raises(ValueError):
        config.phase_of("2020-07-14")


def test_grid_constants_agree() -> None:
    assert config.hour_of("2020-01-02") == 0 and config.hour_of("2020-01-02T05:00:00") == 5
    assert config.hour_of(config.CANDIDATE_START) % 4 == 0
    assert config.hour_of(config.DISCOVERY_END) == 48168


@pytest.mark.parametrize("symbol", [
    "ETH", "eth", "ETHUSDT", "ETH/USDT", "ETH_USDT", "ETH-USDT", " eth ", "ETH USDT", "ETH:USDT", "ETH.USDT",
    "ETHBTC", "ETHUSDC", "ETHFDUSD", "ETHBUSD", "ETHTUSD", "ETHEUR",
    "1000ETH", "1000DOGE", "1000XRP", "1000XRPUSDT", "1MDOGE", "10000SOL",
    "\uff25\uff34\uff28", "ETH\u200b",
])
def test_every_spelling_of_a_masked_coin_is_sealed(symbol: str) -> None:
    assert config.is_sealed(symbol)
    with pytest.raises(config.SealedSymbolError):
        config.assert_open(symbol)


@pytest.mark.parametrize("symbol", [
    "BTC", "BTCUSDT", "WETH", "BETH", "ETHFI", "ADAUP", "USDT", "AAA", "WBTC", "EUR", "USDC", "BUSD", "FDUSD", "TUSD", "AEUR",
    "1MBABYDOGE", "BABYDOGE", "1000SATS", "1000CAT", "1000CHEEMS", "1INCH", "SOLV", "RESOLV", "\u5e01\u5b89\u4eba\u751f",
])
def test_open_names_pass(symbol: str) -> None:
    assert not config.is_sealed(symbol)
    config.assert_open(symbol)


def test_the_mask_is_the_twelve_of_the_ruling_and_the_named_proxy() -> None:
    """BNSOL carries SOL's price, so it is masked with SOL, and the page says so. The mask must not widen silently."""
    assert config.SEAL_PROXIES == frozenset({"BNSOL"}) and config.is_sealed("BNSOL") and "BNSOL" in PAGE.read_text(encoding="utf8")


def test_a_proxy_the_operator_names_is_masked_like_a_masked_coin(monkeypatch) -> None:
    monkeypatch.setattr(config, "SEAL_PROXIES", frozenset({"BNSOL"}))
    assert config.is_sealed("BNSOL") and config.is_sealed("bnsol/usdt") and config.sealed_hits(["AAA", "BNSOL"]) == ["BNSOL"]
    with pytest.raises(config.SealedSymbolError):
        config.assert_all_open(["AAA", "BNSOL"])


def test_the_seal_section_points_at_a_section_that_exists() -> None:
    assert "phần kết quả nghiệm thu" in PAGE.read_text(encoding="utf8") and "\n## Kết quả nghiệm thu\n" in PAGE.read_text(encoding="utf8")


def test_paths_come_from_the_environment(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SELECTION_ROOT", str(tmp_path / "r"))
    monkeypatch.setenv("SELECTION_SEED", str(tmp_path / "s"))
    assert config.selection_root() == tmp_path / "r" and config.seed_dir() == tmp_path / "s"
    monkeypatch.delenv("SELECTION_ROOT")
    monkeypatch.delenv("SELECTION_SEED")
    assert config.selection_root() == config.REPO.resolve() / "data" / "selection"
    assert config.seed_dir() == config.REPO.resolve() / "data" / "selector_20261101_01" / "seed"


@pytest.mark.parametrize("bad", ["data/promotion_store", "data/state", "data/promotion_store/x", "data/state/selection", "data/execution", "data/research/x"])
def test_the_root_cannot_sit_in_a_runner_store_of_any_checkout(monkeypatch, tmp_path, bad: str) -> None:
    monkeypatch.setenv("SELECTION_ROOT", str(tmp_path / bad))
    with pytest.raises(ValueError, match="runner store"):
        config.selection_root()


@pytest.mark.parametrize("inside", ["", "data", "logs", "src", "data/execution", "data/research", "data/selection/x", "data/selection_rehearsal", "data/selection/../state"])
def test_inside_the_repo_the_root_is_data_selection_and_nothing_else(monkeypatch, inside: str) -> None:
    monkeypatch.setenv("SELECTION_ROOT", str(config.REPO / inside))
    with pytest.raises(ValueError):
        config.selection_root()


def test_outside_the_repo_a_rehearsal_may_point_the_root_anywhere(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SELECTION_ROOT", str(tmp_path / "data" / "selection_rehearsal"))
    assert config.selection_root() == tmp_path / "data" / "selection_rehearsal"


def test_a_file_is_replaced_whole_and_its_folder_made(tmp_path) -> None:
    path = tmp_path / "a" / "b.json"
    config.write_text(path, "x")
    config.write_text(path, "y")
    assert path.read_text(encoding="utf8") == "y" and [p.name for p in path.parent.iterdir()] == ["b.json"]
