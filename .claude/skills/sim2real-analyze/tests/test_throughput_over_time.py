"""Tests for throughput_over_time.py."""
import sys

import _common
import throughput_over_time as tot


def _patch_workspace(monkeypatch, ws):
    monkeypatch.setattr(_common, "WORKSPACE_DIR", ws)


# ── Unit: _rate_series ────────────────────────────────────────────────────────

def test_rate_series_empty():
    xs, ys = tot._rate_series([])
    assert xs == []
    assert ys == []


def test_rate_series_buckets_by_second():
    # Three rows in sec 0, two in sec 2 → gap at sec 1 filled with 0.
    rows = [
        {"send_time_us": "0"},
        {"send_time_us": "500000"},
        {"send_time_us": "900000"},
        {"send_time_us": "2000000"},
        {"send_time_us": "2500000"},
    ]
    xs, ys = tot._rate_series(rows)
    assert xs == [0, 1, 2]
    assert ys == [3, 0, 2]


def test_rate_series_normalizes_to_first_send():
    """Time origin should be the earliest send, not epoch."""
    rows = [
        {"send_time_us": "1_000_000_000"},   # sec 1000 absolute
        {"send_time_us": "1_500_000_000"},   # sec 1500 absolute
    ]
    xs, ys = tot._rate_series(rows)
    assert xs[0] == 0
    assert xs[-1] == 500


# ── Integration: main happy path ──────────────────────────────────────────────

def test_main_produces_png(workspace, monkeypatch, capsys):
    _patch_workspace(monkeypatch, workspace["ws"])
    monkeypatch.setattr(sys, "argv", ["throughput_over_time.py", "--run", workspace["run"]])
    tot.main()
    out = capsys.readouterr().out
    assert "Saved:" in out
    png = workspace["ws"] / "runs" / workspace["run"] / "results_charts" / "throughput_over_time.png"
    assert png.exists()
    assert png.stat().st_size > 0


def test_main_all_error_rows_still_plots(workspace, monkeypatch, capsys, make_row, make_csv):
    """Offered-load semantics: an error row is still a send. Chart renders."""
    _patch_workspace(monkeypatch, workspace["ws"])
    bad = [make_row(send=i * 100_000, status="error") for i in range(10)]
    make_csv(workspace["baseline"] / workspace["workload"] / "trace_data.csv", bad)
    make_csv(workspace["treatment"] / workspace["workload"] / "trace_data.csv", bad)
    monkeypatch.setattr(sys, "argv", ["throughput_over_time.py", "--run", workspace["run"]])
    tot.main()
    out = capsys.readouterr().out
    assert "Saved:" in out
    png = workspace["ws"] / "runs" / workspace["run"] / "results_charts" / "throughput_over_time.png"
    assert png.exists()
    assert png.stat().st_size > 0
