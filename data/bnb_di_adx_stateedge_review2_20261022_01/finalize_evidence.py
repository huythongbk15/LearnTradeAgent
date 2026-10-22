"""Preserve drift evidence and seal current, verified files; never approve a run."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess

from trading_agent.backtest.campaign_integrity import capture_campaign_source
from trading_agent.backtest.preregistration_binding import preregistration_fingerprint

ROOT = Path.cwd()
OUT = ROOT / "data/bnb_di_adx_stateedge_review2_20261022_01"
PACKETS = [ROOT / "data" / name for name in (
    "bnb_di_adx_stateedge_20261020_01",
    "bnb_di_adx_stateedge_prereg_20261020_01",
    "bnb_native_review_20261020_01",
)]
EXPECTED_A = "c4b7479bd88dee4352dec7d9d2c03ab2ea2be18f65a0630bb6e543f9980b4b74"
RECEIPTS = (
    "identity", "fasttests", "ac", "f9_decompose", "f9_episodes", "f9_layers",
    "source_after", "control_plan", "launcher", "lint",
)


def write_new(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write("\n")


def main() -> None:
    source = capture_campaign_source(ROOT)
    if source["tree_fingerprint"] != EXPECTED_A:
        raise RuntimeError("source drift after verification; do not seal")
    payload_path = PACKETS[1] / "preregistration_payload.json"
    payload = json.loads(payload_path.read_text())
    request = payload["data"]["scope_change_request"]
    if request["approval_state"] != "PENDING_OPERATOR_APPROVAL":
        raise RuntimeError("unexpected approval state")
    if any(request.get(field) for field in ("approved_by", "approved_at", "approved_scope")):
        raise RuntimeError("pending approval contains populated approval fields")
    if payload["identity_A_source_fingerprint"] != EXPECTED_A:
        raise RuntimeError("payload does not bind verified source")
    receipts = {}
    for name in RECEIPTS:
        filename = f"trading_review2_{name}_20261022_01.json"
        original = Path("/tmp") / filename
        record = json.loads(original.read_text())
        if record["rc"] != 0:
            raise RuntimeError(f"failed required receipt: {name}")
        target = OUT / filename
        if not target.exists():
            shutil.copy2(original, target)
        elif target.read_bytes() != original.read_bytes():
            raise RuntimeError(f"receipt conflict: {target}")
        receipts[name] = {"path": str(target.relative_to(ROOT)), "elapsed_sec": record["elapsed_sec"],
                          "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}

    # Preserve existing manifests AND current drifted bytes before any rewrite.
    audit = []
    manifests = []
    for packet in PACKETS:
        manifest = json.loads((packet / "manifest.json").read_text())
        manifests.append(manifest)
        archive = OUT / "pre_reseal" / packet.name
        write_new(archive / "manifest.json", manifest)
        for entry in manifest["files"]:
            path = packet / entry["path"]
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            if actual != entry["sha256"]:
                backup = archive / "drifted_current" / entry["path"]
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, backup)
                audit.append({"packet": packet.name, **entry, "observed_sha256": actual})
    write_new(OUT / "pre_reseal" / "drift.json", audit)
    write_new(OUT / "verified_source.json", source)
    source_map = {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for folder in ("src", "scripts") for path in (ROOT / folder).rglob("*.py")
    }
    write_new(OUT / "source_files_sha256.json", source_map)
    with (OUT / "source_vs_HEAD.patch").open("xb") as stream:
        subprocess.run(["git", "diff", "HEAD", "--binary", "--", "src", "scripts"],
                       check=True, stdout=stream)
    # No old source map exists: do not fabricate an exact rev19-source delta.
    b = preregistration_fingerprint(payload)
    verification = {
        "source": source, "B": b, "receipts": receipts,
        "scope_approval": "PENDING_OPERATOR_APPROVAL", "market_run": False,
        "history_limit": "Exact rev19 source delta unavailable: no sealed per-file source snapshot; current full source map and diff vs HEAD retained.",
    }
    for packet in PACKETS[:2]:
        snapshot = packet / "pre_approval_payload.json"
        if snapshot.read_bytes() != payload_path.read_bytes():
            raise RuntimeError("pre-approval snapshot is not current payload")
    for packet, manifest in zip(PACKETS, manifests):
        current_evidence = packet / "evidence" / "review2_verification.json"
        write_new(current_evidence, verification)
        manifest["files"].append({"path": "evidence/review2_verification.json"})
        manifest["file_count"] = len(manifest["files"])
        manifest.update({
            "revision": "rev20",
            "identity_A_source_fingerprint": EXPECTED_A,
            "identity_A_dirty_diff_sha256": source["dirty_diff_sha256"],
            "identity_B_preregistration_fingerprint": b,
            "revision_provenance": str((OUT / "pre_reseal").relative_to(ROOT)),
            "review_status": "VERIFIED_PENDING_EXTERNAL_REVIEW",
            "current_verification_index": str((OUT / "verification_index.json").relative_to(ROOT)),
            "historical_evidence_note": "Existing acceptance_run/ and prior receipts remain historical rev19 evidence. Current-source verification is separately recorded in current_verification_index; old PASS results are not relabelled.",
        })
        for entry in manifest["files"]:
            raw = (packet / entry["path"]).read_bytes()
            entry.update(sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))
        (packet / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    write_new(OUT / "verification_index.json", verification)
    if capture_campaign_source(ROOT) != source:
        raise RuntimeError("source changed while sealing")
    print(json.dumps({"status": "SEALED_PENDING_EXTERNAL_REVIEW", "A": EXPECTED_A, "B": b,
                      "preserved_mismatches": len(audit), "market_authorized": False}))


if __name__ == "__main__":
    main()
