"""Tests for error_rate.py."""
import sys
from collections import Counter

import _common
import error_rate


def _patch_workspace(monkeypatch, ws):
    monkeypatch.setattr(_common, "WORKSPACE_DIR", ws)


# ── Unit: _format_pct ────────────────────────────────────────────────────────

def test_format_pct_zero_whole_returns_na():
    assert error_rate._format_pct(0, 0) == "N/A"


def test_format_pct_normal():
    assert error_rate._format_pct(1, 4) == "25.00%"


def test_format_pct_no_errors():
    assert error_rate._format_pct(0, 100) == "0.00%"


# ── Unit: _format_row ─────────────────────────────────────────────────────────

def test_format_row_reports_top_status():
    counts = Counter({"ok": 90, "timeout": 6, "500": 3, "429": 1})
    row = error_rate._format_row("baseline", 100, 10, counts)
    assert "baseline" in row
    assert "100" in row
    assert "10" in row
    assert "10.00%" in row
    assert "timeout=6" in row
    assert "500=3" in row


def test_format_row_no_errors_dash_status():
    row = error_rate._format_row("baseline", 100, 0, Counter({"ok": 100}))
    assert "—" in row


# ── Integration: main happy path ──────────────────────────────────────────────

def test_main_happy_path(workspace, monkeypatch, capsys, make_row, make_csv):
    """Mix of ok and error rows across phases."""
    _patch_workspace(monkeypatch, workspace["ws"])
    baseline_rows = [make_row(status="ok") for _ in range(90)] + [make_row(status="timeout") for _ in range(10)]
    treatment_rows = [make_row(status="ok") for _ in range(80)] + [make_row(status="500") for _ in range(20)]
    make_csv(workspace["baseline"] / workspace["workload"] / "trace_data.csv", baseline_rows)
    make_csv(workspace["treatment"] / workspace["workload"] / "trace_data.csv", treatment_rows)
    monkeypatch.setattr(sys, "argv", ["error_rate.py", "--run", workspace["run"]])
    error_rate.main()
    out = capsys.readouterr().out
    assert "alpha" in out
    assert "baseline" in out and "treatment" in out
    assert "10.00%" in out  # baseline error rate
    assert "20.00%" in out  # treatment error rate
    assert "timeout=10" in out
    assert "500=20" in out


def test_main_writes_txt_file(workspace, monkeypatch):
    _patch_workspace(monkeypatch, workspace["ws"])
    monkeypatch.setattr(sys, "argv", ["error_rate.py", "--run", workspace["run"]])
    error_rate.main()
    out_file = workspace["ws"] / "runs" / workspace["run"] / "error_rate.txt"
    assert out_file.exists()
    content = out_file.read_text()
    assert "alpha" in content
    assert content.endswith("\n")


def test_main_zero_errors_reports_zero(workspace, monkeypatch, capsys):
    """workspace fixture is all-ok — rate should be 0.00%."""
    _patch_workspace(monkeypatch, workspace["ws"])
    monkeypatch.setattr(sys, "argv", ["error_rate.py", "--run", workspace["run"]])
    error_rate.main()
    out = capsys.readouterr().out
    assert "0.00%" in out


def test_main_all_errors_reports_hundred_percent(workspace, monkeypatch, capsys, make_row, make_csv):
    _patch_workspace(monkeypatch, workspace["ws"])
    bad = [make_row(status="error") for _ in range(10)]
    make_csv(workspace["baseline"] / workspace["workload"] / "trace_data.csv", bad)
    make_csv(workspace["treatment"] / workspace["workload"] / "trace_data.csv", bad)
    monkeypatch.setattr(sys, "argv", ["error_rate.py", "--run", workspace["run"]])
    error_rate.main()
    out = capsys.readouterr().out
    assert "100.00%" in out


def test_main_reports_multiple_workloads(workspace, monkeypatch, capsys, make_csv):
    """Both alpha (from fixture) and beta (added here) appear in the output."""
    _patch_workspace(monkeypatch, workspace["ws"])
    make_csv(workspace["baseline"] / "workload_beta" / "trace_data.csv",
             [{"send_time_us": "0", "first_chunk_time_us": "0",
               "last_chunk_time_us": "0", "output_tokens": "0", "status": "ok"}])
    make_csv(workspace["treatment"] / "workload_beta" / "trace_data.csv",
             [{"send_time_us": "0", "first_chunk_time_us": "0",
               "last_chunk_time_us": "0", "output_tokens": "0", "status": "ok"}])
    monkeypatch.setattr(sys, "argv", ["error_rate.py", "--run", workspace["run"]])
    error_rate.main()
    out = capsys.readouterr().out
    assert "alpha" in out
    assert "beta" in out
