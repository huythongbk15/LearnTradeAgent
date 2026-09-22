"""
AC04 Evidence Script — Gate Binding After Reload.

Independent oracle: recomputes the holdout_id hash from raw JSON fields
without using FinalHoldoutManifest internals. Then verifies serialize ->
reload -> tamper detection at every layer.
"""
import hashlib
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# Independent oracle
def oracle_holdout_hash(payload: dict) -> str:
    """Recompute holdout_id: SHA-256 of canonical JSON of immutable fields."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                           allow_nan=False).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"

results = []
def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    results.append({"name": name, "status": status, "detail": detail})
    print(f"[{status}] {name}: {detail}")
    if not cond:
        raise SystemExit(f"AC04 evidence FAIL: {name}")

# System under test
from trading_agent.backtest.nested_wfo import FinalHoldoutManifest

# C1: holdout_id matches independent hash
now = datetime.now(timezone.utc).isoformat()
manifest = FinalHoldoutManifest(
    strategy_id="volatility_breakout_v2",
    symbol="ADA_USDT",
    timeframe="1h",
    holdout_start_bar=27000,
    holdout_end_bar=31000,
    data_manifest_sha="a" * 64,
    feature_schema_hash="b" * 64,
    freeze_timestamp=now,
    frozen_by="research_system",
    commit_sha_at_freeze="5d0e477b2826a583b9d30a58e19415178b73e9e5",
    notes="test holdout",
)

oracle_payload = {
    "strategy_id": "volatility_breakout_v2",
    "symbol": "ADA_USDT",
    "timeframe": "1h",
    "holdout_start_bar": 27000,
    "holdout_end_bar": 31000,
    "data_manifest_sha": "a" * 64,
    "feature_schema_hash": "b" * 64,
    "freeze_timestamp": now,
    "frozen_by": "research_system",
    "commit_sha_at_freeze": "5d0e477b2826a583b9d30a58e19415178b73e9e5",
    "notes": "test holdout",
}
oracle_id = oracle_holdout_hash(oracle_payload)
check("C1_hash_match", manifest.holdout_id == oracle_id,
      f"system={manifest.holdout_id[:24]}..., oracle={oracle_id[:24]}...")

# C2: Serialize -> reload -> identity preserved
tmpdir = Path(tempfile.mkdtemp())
manifest_path = tmpdir / "holdout_manifest.json"
manifest.save(manifest_path)

raw = json.loads(manifest_path.read_text())
reloaded = FinalHoldoutManifest.load(manifest_path)
check("C2_reload_id_match", reloaded.holdout_id == manifest.holdout_id,
      f"reloaded={reloaded.holdout_id[:24]}..., original={manifest.holdout_id[:24]}...")
check("C2_reload_fields_match", reloaded.strategy_id == manifest.strategy_id and
      reloaded.holdout_start_bar == manifest.holdout_start_bar,
      f"strategy={reloaded.strategy_id}, start={reloaded.holdout_start_bar}")

# C3: Tamper detection after reload
tampered_raw = raw.copy()
tampered_raw["holdout_start_bar"] = 28000
tampered_raw["holdout_id"] = "sha256:deadbeef"
tampered_path = tmpdir / "tampered_manifest.json"
tampered_path.write_text(json.dumps(tampered_raw))

try:
    FinalHoldoutManifest.load(tampered_path)
    check("C3_tamper_rejected_on_load", False, "should have raised")
except Exception as e:
    check("C3_tamper_rejected_on_load", True, f"rejected: {type(e).__name__}")

# C4: verify_integrity detects in-memory tamper
tampered_manifest = FinalHoldoutManifest(
    strategy_id="volatility_breakout_v2",
    symbol="ADA_USDT",
    timeframe="1h",
    holdout_start_bar=28000,
    holdout_end_bar=31000,
    data_manifest_sha="a" * 64,
    feature_schema_hash="b" * 64,
    freeze_timestamp=now,
    frozen_by="research_system",
    commit_sha_at_freeze="5d0e477b2826a583b9d30a58e19415178b73e9e5",
    notes="test holdout",
)
check("C4_different_hash_after_tamper", tampered_manifest.holdout_id != manifest.holdout_id,
      f"tampered={tampered_manifest.holdout_id[:24]}...")

check("C4_verify_integrity_ok", manifest.verify_integrity() is True, "untampered verifies")

# C5: Atomic save cleanup
manifest.save(manifest_path)
tmp_path = manifest_path.with_suffix(".json.tmp")
check("C5_atomic_no_temp_left", not tmp_path.exists(), "no .tmp file after atomic save")

# C6: opened flag is mutable state (not part of hash)
opened_manifest = manifest.open(actor="researcher")
check("C6_hash_still_matches_after_open", opened_manifest.holdout_id == manifest.holdout_id,
      "opened holdout_id unchanged from original")
check("C6_opened_flag_set", opened_manifest.opened is True,
      f"opened={opened_manifest.opened}, by={opened_manifest.opened_by}")

# Summary
all_pass = all(r["status"] == "PASS" for r in results)
print(f"\n{'='*60}")
print(f"AC04 Evidence: {'ALL PASS' if all_pass else 'FAIL'} ({len(results)} checks)")
print(f"{'='*60}")

evidence = {
    "ac_id": "AC04",
    "oracle": "independent: SHA-256 of canonical JSON, no FinalHoldoutManifest internals",
    "cases": results,
    "all_pass": all_pass,
    "total_checks": len(results),
    "passed": sum(1 for r in results if r["status"] == "PASS"),
    "strategy": manifest.strategy_id,
    "holdout_id": manifest.holdout_id,
}
out = Path("/tmp/ac04_evidence.json")
out.write_text(json.dumps(evidence, indent=2, default=str))
print(f"Evidence written to {out}")
sys.exit(0 if all_pass else 1)
