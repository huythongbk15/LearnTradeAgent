"""Exercise only an exact, verified inert stub; never execute the old wrapper."""
import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/run_wfo_b_phase.sh"
EXPECTED = """#!/bin/bash
# Retired compatibility stub. Never delete evidence or launch a campaign here.
set -eu
printf '%s\\n' \\
  'RETIRED_ENTRYPOINT: run_wfo_b_phase.sh cannot execute campaigns or overwrite packets.' \\
  'Use the reviewed calibration/research workflow with a fresh output directory and explicit approval.' >&2
exit 64
"""


def verified_stub():
    assert SCRIPT.read_text() == EXPECTED, "Do not run an unexpected shell program in this test"
    bash = shutil.which("bash")
    assert bash is not None, "bash is required to verify the compatibility stub"
    return bash


def test_retired_stub_shell_syntax():
    result = subprocess.run([verified_stub(), "--noprofile", "--norc", "-n", str(SCRIPT)], capture_output=True)
    assert result.returncode == 0


@pytest.mark.parametrize("flags", [[], ["--execute"], ["--force", "--resume"], ["--help"]])
def test_retired_stub_cannot_touch_packet_or_launch_commands(tmp_path, flags):
    bash = verified_stub()
    packet = tmp_path / "data/wfo_ma_adx_btc_4h_v2"
    packet.mkdir(parents=True)
    (packet / "evidence.json").write_text('{"immutable":true}')
    (tmp_path / "data_binding_btc_4h.json").write_text("do not read")
    trap_dir = tmp_path / "trap-bin"
    trap_dir.mkdir()
    # Even an accidentally added external command must not reach real rm/Python.
    for name in ("rm", "cat", "tr", "tail", "tee", "python", "python3"):
        path = trap_dir / name
        path.write_text("#!/bin/bash\nprintf invoked > external-command-ran\nexit 99\n")
        path.chmod(0o755)
    def snapshot():
        return {str(p.relative_to(tmp_path)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in tmp_path.rglob("*") if p.is_file()}
    before = snapshot()
    env = {"PATH": str(trap_dir), "HOME": str(tmp_path), "LANG": "C"}
    result = subprocess.run([bash, "--noprofile", "--norc", str(SCRIPT), *flags],
                            cwd=tmp_path, env=env, text=True, capture_output=True, timeout=5)
    assert result.returncode == 64
    assert "RETIRED_ENTRYPOINT" in result.stderr
    assert not result.stdout
    assert before == snapshot()
    assert not (tmp_path / "external-command-ran").exists()
