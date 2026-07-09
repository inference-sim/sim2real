"""Shared test fixtures for sim2real-analyze catalog scripts.

Adds the `analyses/` directory to `sys.path` once per session so
`import ttft_cdf`, `import _common`, etc. resolve inside test modules
without every file having to prepend it locally.
"""
import csv
import sys
from pathlib import Path

import pytest

ANALYSES_DIR = Path(__file__).resolve().parent.parent / "analyses"
sys.path.insert(0, str(ANALYSES_DIR))


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("")
        return
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _row(send: int = 0, first: int = 1_000_000, last: int = 6_000_000,
         tokens: int = 10, status: str = "ok") -> dict:
    return {
        "send_time_us": str(send),
        "first_chunk_time_us": str(first),
        "last_chunk_time_us": str(last),
        "output_tokens": str(tokens),
        "status": status,
    }


@pytest.fixture
def make_row():
    """Factory returning a trace row with sensible defaults; override any field."""
    return _row


@pytest.fixture
def make_csv():
    """Factory writing a CSV from a list of row dicts."""
    return _write_csv


@pytest.fixture
def workspace(tmp_path, make_row, make_csv):
    """Create a workspace with baseline+treatment CSVs for a single workload.

    Returns a namespace-like dict with:
        `ws`        — the workspace/ dir
        `baseline`  — path to baseline log dir
        `treatment` — path to treatment log dir
        `run`       — the run name ("testrun")
        `workload`  — the sole workload dir name

    Individual tests can add more CSVs or workloads via the fixtures.
    """
    run = "testrun"
    wl = "workload_alpha"
    ws_dir = tmp_path / "workspace"
    run_root = ws_dir / "runs" / run
    baseline_log = run_root / "results" / "baseline"
    treatment_log = run_root / "results" / "treatment"

    base_rows = [make_row(send=i * 100_000, first=(i * 100_000) + 500_000,
                          last=(i * 100_000) + 1_500_000, tokens=10)
                 for i in range(20)]
    treat_rows = [make_row(send=i * 100_000, first=(i * 100_000) + 400_000,
                           last=(i * 100_000) + 1_300_000, tokens=10)
                  for i in range(20)]
    make_csv(baseline_log / wl / "trace_data.csv", base_rows)
    make_csv(treatment_log / wl / "trace_data.csv", treat_rows)

    return {
        "ws": ws_dir,
        "baseline": baseline_log,
        "treatment": treatment_log,
        "run": run,
        "workload": wl,
    }
