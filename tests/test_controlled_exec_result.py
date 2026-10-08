import json
from pathlib import Path
import subprocess
import sys


def test_timeout_persists_result_and_output(tmp_path):
    root = Path(__file__).resolve().parents[1]
    output = tmp_path / "timeout.json"
    result = subprocess.run([
        sys.executable, str(root / "scripts/qwenpaw_control/controlled_exec.py"),
        "--timeout", "1", "--heartbeat", "30", "--result-file", str(output),
        "--", sys.executable, "-c",
        "import time; print('before_timeout', flush=True); time.sleep(3)",
    ], cwd=root, capture_output=True, text=True, timeout=20)
    assert result.returncode != 0
    packet = json.loads(output.read_text())
    assert packet["status"] == "timeout"
    assert packet["rc"] == -1
    assert "before_timeout" in packet["stdout"]
