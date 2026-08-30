#!/usr/bin/env python3
"""Run deterministic pytest profiles with safe parallel/serial lanes.

The runner deliberately invokes pytest without a shell so worker count and
extra arguments cannot change command structure.  Tests marked ``serial`` are
always executed in a separate, single-process phase.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class Profile:
    marker: str
    durations: bool = False
    distribution: str = "loadscope"


PROFILES = {
    "p0": Profile("p0"),
    "fast": Profile("not slow"),
    "full": Profile(""),
    "slow": Profile("slow", durations=True, distribution="load"),
    "profile": Profile("", durations=True),
    # Non-overlapping CI shards.  Together with ``slow`` they cover every test
    # exactly once while allowing GitHub Actions to run all shards in parallel.
    "ci-p0": Profile("p0 and not slow"),
    "ci-fast": Profile("not p0 and not slow"),
}


def _combine(marker: str, lane: str) -> str:
    if not marker:
        return lane
    return f"({marker}) and {lane}"


def _run_phase(
    *,
    label: str,
    marker: str,
    workers: str,
    parallel: bool,
    distribution: str,
    durations: bool,
    extra_args: Sequence[str],
) -> int:
    command = [sys.executable, "-m", "pytest", "tests", "-m", marker]
    if parallel and workers != "0":
        command.extend(["-n", workers, "--dist", distribution])
    if durations:
        command.extend(["--durations=50", "--durations-min=0.5"])
    command.extend(extra_args)

    print(f"\n[test-suite] {label}: {' '.join(command)}", flush=True)
    completed = subprocess.run(command, check=False)
    # pytest exit code 5 means this lane selected no tests.  That is expected
    # for the serial lane until a test explicitly declares shared state.
    return 0 if completed.returncode == 5 else completed.returncode


def _fault_workers(workers: str) -> str:
    """Fault cells are isolated from WFO load and capped at two workers."""
    if workers == "0":
        return workers
    try:
        return str(min(int(workers), 2))
    except ValueError:
        return "2"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", choices=tuple(PROFILES))
    parser.add_argument(
        "--workers",
        default=os.getenv("PYTEST_WORKERS"),
        help="xdist worker count; use 0 to disable parallel execution",
    )
    args, extra_args = parser.parse_known_args(argv)
    profile = PROFILES[args.profile]
    # Four workers is the measured-safe default for ordinary tests on this WSL
    # workspace (12 vCPU / 8 GiB).  CPU-bound Polars/WFO cells already use
    # native threads, so three workers finish the slow profile with less
    # oversubscription.  An explicit flag/environment value always wins.
    workers = args.workers or ("3" if args.profile == "slow" else "4")
    extra_args = list(extra_args)
    if extra_args[:1] == ["--"]:
        extra_args = extra_args[1:]

    phases = (
        (
            "parallel",
            _combine(profile.marker, "not serial and not fault"),
            True,
            workers,
            profile.distribution,
        ),
        (
            "fault",
            _combine(profile.marker, "fault and not serial"),
            True,
            _fault_workers(workers),
            "load",
        ),
        ("serial", _combine(profile.marker, "serial"), False, "0", "loadscope"),
    )
    for lane, marker, parallel, phase_workers, distribution in phases:
        return_code = _run_phase(
            label=f"{args.profile}/{lane}",
            marker=marker,
            workers=phase_workers,
            parallel=parallel,
            distribution=distribution,
            durations=profile.durations,
            extra_args=extra_args,
        )
        if return_code:
            return return_code
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
