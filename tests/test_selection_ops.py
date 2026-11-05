"""The job around the run: the wrapper script and its Windows task. No network and no Windows: the wrapper runs in a checkout in tmp_path against a stand-in for the repo's Python."""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path

REPO =Path(__file__).resolve().parents[1]
WRAPPER = REPO / "scripts" / "ops" / "run_selection_weekly.sh"
TASKS = REPO / "scripts" / "ops" / "windows" / "register_tasks.ps1"
RECORD_CALL = 'printf "%s\\n" "$(nice)" "$PYTHONPATH" "$PWD" "$*" > seen\n'


def checkout(tmp_path: Path, python: str) -> Path:
    """A checkout holding the wrapper, with python as the body of .venv/bin/python."""
    script = tmp_path / "scripts" / "ops" / WRAPPER.name
    script.parent.mkdir(parents=True)
    script.write_text(WRAPPER.read_text(encoding="utf8"), encoding="utf8")
    script.chmod(0o755)
    stand_in = tmp_path / ".venv" / "bin" / "python"
    stand_in.parent.mkdir(parents=True)
    stand_in.write_text(f"#!/usr/bin/env bash\n{python}", encoding="utf8")
    stand_in.chmod(0o755)
    return script


def run(script: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([str(script), *args], capture_output=True, text=True, timeout=60, check=False)


def test_the_wrapper_can_be_started_by_the_task_without_a_shell() -> None:
    assert os.access(WRAPPER, os.X_OK)      # wsl.exe --exec runs the file itself
    assert WRAPPER.read_text(encoding="utf8").startswith("#!/usr/bin/env bash\n")


def test_the_wrapper_runs_the_weekly_module_at_nice_19_with_src_and_the_repo_on_the_path(tmp_path: Path) -> None:
    script = checkout(tmp_path, RECORD_CALL)
    assert run(script, "--as-of", "2026-11-02", "--no-fetch").returncode == 0
    nice, path, cwd, argv = (tmp_path / "seen").read_text(encoding="utf8").splitlines()
    assert nice == "19"
    assert path == f"{tmp_path}/src:{tmp_path}"
    assert cwd == str(tmp_path)
    assert argv == "-u -m tools.selection.run_weekly --as-of 2026-11-02 --no-fetch"


def test_the_wrapper_asks_for_the_lowest_priority_itself(tmp_path: Path) -> None:
    """Through a stand-in for nice: under a suite already at nice 19 any increment reads 19."""
    script = checkout(tmp_path, "")
    stand_in = tmp_path / "bin" / "nice"
    stand_in.parent.mkdir()
    stand_in.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$*" > "$(dirname "$0")/asked"\n', encoding="utf8")
    stand_in.chmod(0o755)
    subprocess.run([str(script)], env={**os.environ, "PATH": f"{stand_in.parent}:{os.environ['PATH']}"}, timeout=60, check=True)
    assert (stand_in.parent / "asked").read_text(encoding="utf8").startswith("-n 19 ")


def test_the_wrapper_sets_a_limit_of_its_own_inside_the_tasks_hour(tmp_path: Path) -> None:
    """A stopped Windows task may leave the Linux job running, and a stuck job would hold the lock and cost next Monday."""
    script = checkout(tmp_path, "")
    stand_in = tmp_path / "bin" / "timeout"
    stand_in.parent.mkdir()
    stand_in.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$*" > "$(dirname "$0")/asked"\n', encoding="utf8")
    stand_in.chmod(0o755)
    subprocess.run([str(script)], env={**os.environ, "PATH": f"{stand_in.parent}:{os.environ['PATH']}"}, timeout=60, check=True)
    limit = re.match(r"-k 60 (\d+) nice -n 19 ", (stand_in.parent / "asked").read_text(encoding="utf8"))
    hours = re.search(r"-ExecutionTimeLimit \(New-TimeSpan -Hours (\d+)\)", TASKS.read_text(encoding="utf8"))
    assert int(limit[1]) + 60 < int(hours[1]) * 3600


def test_a_run_past_its_limit_is_stopped_and_says_so_in_the_log(tmp_path: Path) -> None:
    script = checkout(tmp_path, "sleep 30\n")
    text = script.read_text(encoding="utf8").replace("timeout -k 60 3000", "timeout -k 1 1")
    script.write_text(text, encoding="utf8")
    assert run(script).returncode == 124
    assert (tmp_path / "logs" / "selection_weekly.log").read_text(encoding="utf8").endswith("selection end rc=124\n")


def test_the_log_holds_the_run_and_the_wrapper_says_how_it_ended(tmp_path: Path) -> None:
    script = checkout(tmp_path, "echo the run spoke; echo to stderr >&2\n")
    done = run(script)
    assert done.stdout == "" and done.stderr == ""
    lines = (tmp_path / "logs" / "selection_weekly.log").read_text(encoding="utf8").splitlines()
    assert re.fullmatch(r"=== \S+Z selection start pid=\d+", lines[0])
    assert lines[1:3] == ["the run spoke", "to stderr"]
    assert re.fullmatch(r"=== \S+Z selection end rc=0", lines[3])


def test_a_refused_run_keeps_its_exit_code(tmp_path: Path) -> None:
    script = checkout(tmp_path, "exit 2\n")
    assert run(script).returncode == 2
    assert (tmp_path / "logs" / "selection_weekly.log").read_text(encoding="utf8").endswith("selection end rc=2\n")


def test_a_signal_to_the_wrapper_leaves_a_line_and_the_real_end_of_the_job(tmp_path: Path) -> None:
    """Bash runs the trap after timeout returns: the job was not stopped, so saying it was would be false."""
    script = checkout(tmp_path, "touch started\nsleep 2\nexit 7\n")
    job = subprocess.Popen([str(script)])
    deadline = time.monotonic() + 30
    while not (tmp_path / "started").exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    job.terminate()
    assert job.wait(timeout=30) == 7
    text = (tmp_path / "logs" / "selection_weekly.log").read_text(encoding="utf8")
    assert "selection got SIGTERM, job goes on\n" in text and text.endswith("selection end rc=7\n")


def test_a_rehearsal_on_another_root_keeps_out_of_the_log_the_page_reads(tmp_path: Path) -> None:
    script = checkout(tmp_path, "exit 2\n")
    env = {**os.environ, "SELECTION_ROOT": str(tmp_path / "rehearsal")}
    assert subprocess.run([str(script)], env=env, timeout=60, check=False).returncode == 2
    assert not (tmp_path / "logs" / "selection_weekly.log").exists()
    assert (tmp_path / "logs" / "selection_rehearsal.log").read_text(encoding="utf8").endswith("selection end rc=2\n")


def test_the_wrapper_reaches_the_real_code_and_nothing_else(tmp_path: Path) -> None:
    script = checkout(tmp_path, f'exec "{sys.executable}" "$@"\n')
    (tmp_path / "src").symlink_to(REPO / "src")
    (tmp_path / "tools").symlink_to(REPO / "tools")
    assert run(script, "--help").returncode == 0     # imports the run, and with it trading_agent's policy module
    assert "--seed-only" in (tmp_path / "logs" / "selection_weekly.log").read_text(encoding="utf8")
    text = WRAPPER.read_text(encoding="utf8")
    for other_path in ("live_enhanced", "--execute", "TRADING_MODE", ".env", "API_KEY"):
        assert other_path not in text


def test_every_task_can_be_chosen_alone_and_printed() -> None:
    text = TASKS.read_text(encoding="utf8")
    choices = re.findall(r'"(\w+)"', re.search(r"ValidateSet\((.*?)\)", text)[1])
    default = re.findall(r'"(\w+)"', re.search(r"\$Only = @\((.*?)\)", text)[1])
    # FlowFlush (book FLOW01's 5-minute demo run) is registered only by name: -Only FlowFlush
    # (docs/operations/PERP_DEMO_RUNNER.md). Every other task is in the default list.
    opt_in = {"FlowFlush"}
    assert [c for c in choices if c not in opt_in] == default and "Selection" in choices
    what_if, register = text.split("exit 0")
    for name in choices:
        assert f'$Only -contains "{name}"' in what_if
        assert f'$Only -contains "{name}"' in register


def test_the_task_runs_the_wrapper_headless_on_monday_utc() -> None:
    text = TASKS.read_text(encoding="utf8")
    assert '-TaskName "TradingAgent-SelectionPaper" -Action $selection' in text
    assert "--headless wsl.exe -d $Distro -u $User --exec $Repo/scripts/ops/run_selection_weekly.sh" in text
    assert "-Weekly -DaysOfWeek $selectionAt.DayOfWeek -At $selectionAt" in text      # the local weekday of Monday 00:07 UTC
    assert "-Trigger $selectionTrigger -Settings $settings" in text
    assert "-StartWhenAvailable" in text


def test_the_task_date_lands_on_a_monday_from_every_weekday() -> None:
    days, cycle = re.search(r"AddDays\(\((\d+) - \[int\]\$nowUtc\.DayOfWeek\) % (\d+)\)", TASKS.read_text(encoding="utf8")).groups()
    for today in range(7):      # as .NET counts them: Sunday is 0, Monday is 1
        assert (today + (int(days) - today) % int(cycle)) % 7 == 1


def test_the_task_start_is_never_behind_the_registration() -> None:
    text = TASKS.read_text(encoding="utf8")
    moved = "if ($selectionAt -le $nowUtc) { $selectionAt = $selectionAt.AddDays(7) }"      # a past start could fire at once
    assert text.index("AddDays((8 - ") < text.index(moved) < text.index("$selectionAt.ToLocalTime()") < text.index("-Weekly -DaysOfWeek")


def test_the_task_file_stays_ascii_for_windows_powershell() -> None:
    assert TASKS.read_bytes().isascii()     # without a byte order mark 5.1 reads the file as ANSI
