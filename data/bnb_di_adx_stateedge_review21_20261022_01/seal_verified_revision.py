"""One-shot mechanical evidence seal; does not approve scope or market runs."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess

from trading_agent.backtest.campaign_integrity import capture_campaign_source
from trading_agent.backtest.preregistration_binding import preregistration_fingerprint

ROOT = Path.cwd()
OUT = ROOT / "data/bnb_di_adx_stateedge_review21_20261022_01"
PACKETS = [ROOT / "data" / name for name in (
    "bnb_di_adx_stateedge_20261020_01",
    "bnb_di_adx_stateedge_prereg_20261020_01",
    "bnb_native_review_20261020_01",
)]
EXPECTED_A = "b3aa44f026939863018a15de44a57c5b81e1bde9176b2c7483c7d3b7a65db427"


def write_new(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write("\n")


def main():
    source = capture_campaign_source(ROOT)
    if source["tree_fingerprint"] != EXPECTED_A:
        raise RuntimeError("source moved after tests")
    receipts = {}
    for name in ("fast", "lint", "source_before", "control_plan", "ac", "launcher"):
        path = Path("/tmp") / f"trading_rev21_{name}_20261022_01.json"
        record = json.loads(path.read_text())
        if record["rc"] != 0:
            raise RuntimeError(f"required execution failed: {name}")
        destination = OUT / path.name
        if destination.exists():
            raise RuntimeError("one-shot seal already started; inspect before retry")
        shutil.copy2(path, destination)
        receipts[name] = {"path": str(destination.relative_to(ROOT)),
                          "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
                          "elapsed_sec": record["elapsed_sec"]}
    old_manifests = []
    for packet in PACKETS:
        archive = OUT / "before_seal" / packet.name
        archive.mkdir(parents=True, exist_ok=False)
        shutil.copy2(packet / "manifest.json", archive / "manifest.json")
        old_manifests.append(json.loads((packet / "manifest.json").read_text()))
        # Preserve exact pre-seal bytes, including changed payload and assertions.
        for entry in old_manifests[-1]["files"]:
            path = packet / entry["path"]
            if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
                target = archive / "drifted_current" / entry["path"]
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
        if (packet / "pre_approval_payload.json").exists():
            shutil.copy2(packet / "pre_approval_payload.json", archive / "pre_approval_payload.json")
    payload_path = PACKETS[1] / "preregistration_payload.json"
    payload = json.loads(payload_path.read_text())
    request = payload["data"]["scope_change_request"]
    if request["approval_state"] != "PENDING_OPERATOR_APPROVAL" or any(
        request.get(key) for key in ("approved_by", "approved_at", "approved_scope")
    ):
        raise RuntimeError("approval must remain empty and pending")
    payload["identity_A_source_fingerprint"] = EXPECTED_A
    payload["identity_A_dirty_diff_sha256"] = source["dirty_diff_sha256"]
    payload_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    for packet in PACKETS[:2]:
        shutil.copy2(payload_path, packet / "pre_approval_payload.json")
    b = preregistration_fingerprint(payload)
    prose_path = PACKETS[1] / "PREREGISTRATION.md"
    prose = prose_path.read_text().splitlines()
    for index, line in enumerate(prose):
        if line.startswith("| **A — source identity**"):
            cells = line.split("|")
            cells[-2] = f" `tree_fingerprint {EXPECTED_A}`, `dirty_diff_sha256 {source['dirty_diff_sha256']}` "
            prose[index] = "|".join(cells)
        elif line.startswith("| **B —"):
            cells = line.split("|")
            cells[-2] = f" `{b}` "
            prose[index] = "|".join(cells)
    prose_path.write_text("\n".join(prose) + "\n")
    pins_path = PACKETS[0] / "evidence/assertion_files.json"
    pins = json.loads(pins_path.read_text())
    for name in pins["files"]:
        pins["files"][name] = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
    pins["note"] = "rev21: refreshed after R20-1/R20-2 regressions, with prior record preserved in before_seal."
    pins_path.write_text(json.dumps(pins, indent=2) + "\n")
    source_map = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                  for directory in ("src", "scripts") for p in (ROOT / directory).rglob("*.py")}
    write_new(OUT / "source_files_sha256.json", source_map)
    with (OUT / "source_vs_HEAD.patch").open("xb") as stream:
        subprocess.run(["git", "diff", "HEAD", "--binary", "--", "src", "scripts"], check=True, stdout=stream)
    evidence = {"source": source, "B": b, "receipts": receipts,
                "scope_approval": "PENDING_OPERATOR_APPROVAL", "market_run": False,
                "closed_source_findings": ["R20-1", "R20-2"],
                "independent_review": "PENDING_FINAL_IDENTITIES"}
    for run in sorted((ROOT / "data/acceptance_runs").glob("ac01_15_*"), reverse=True):
        identity_file = run / "identity_before.json"
        if identity_file.exists() and json.loads(identity_file.read_text()).get("source") == source:
            shutil.copytree(run, OUT / "acceptance_run")
            evidence["acceptance_evidence"] = {
                str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in (OUT / "acceptance_run").rglob("*") if path.is_file()
            }
            break
    if "acceptance_evidence" not in evidence:
        raise RuntimeError("no complete AC evidence source identity matches verified tree")
    write_new(OUT / "verification_index.json", evidence)
    for packet, manifest in zip(PACKETS, old_manifests):
        proof = packet / "evidence/rev21_verification.json"
        write_new(proof, evidence)
        manifest["files"].append({"path": "evidence/rev21_verification.json"})
        manifest.update(revision="rev21", identity_A_source_fingerprint=EXPECTED_A,
                        identity_A_dirty_diff_sha256=source["dirty_diff_sha256"],
                        identity_B_preregistration_fingerprint=b,
                        review_status="VERIFIED_PENDING_FINAL_INDEPENDENT_REVIEW",
                        current_verification_index=str((OUT / "verification_index.json").relative_to(ROOT)),
                        revision_provenance=str((OUT / "before_seal").relative_to(ROOT)))
        manifest["historical_evidence_note"] = "Prior acceptance and review2 evidence remain historical; current source evidence is in evidence/rev21_verification.json. No old PASS is relabelled."
        for entry in manifest["files"]:
            raw = (packet / entry["path"]).read_bytes()
            entry.update(sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))
        manifest["file_count"] = len(manifest["files"])
        (packet / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    if capture_campaign_source(ROOT) != source:
        raise RuntimeError("source moved during seal")
    print(json.dumps({"A": EXPECTED_A, "B": b, "revision": "rev21", "approval": "PENDING"}))


if __name__ == "__main__":
    main()
