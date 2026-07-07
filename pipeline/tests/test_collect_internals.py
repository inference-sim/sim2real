"""Tests for deploy._probe_remote_mtimes and deploy._is_workload_up_to_date under the
step-5 ``i<N>/`` results-path layout (issue #511).

The mtime-probe optimisation must key by workload (not by the immediate parent
of ``trace_data.csv``, which is now the iteration directory), and the
up-to-date check must inspect every local ``i<N>/trace_data.csv`` under a
workload's directory rather than a single flat CSV.
"""

from pathlib import Path
from unittest.mock import patch

from pipeline import deploy


# ── _probe_remote_mtimes ─────────────────────────────────────────────────────


def _fake_run(stdout: str, returncode: int = 0, stderr: str = ""):
    """Return a MagicMock stand-in for a completed subprocess.CompletedProcess."""
    class _R:  # minimal duck-typed shim
        pass
    r = _R()
    r.stdout = stdout
    r.stderr = stderr
    r.returncode = returncode
    return r


def test_probe_remote_mtimes_keys_by_workload_not_iteration():
    """The immediate parent of ``trace_data.csv`` is now ``i<N>``, so keying
    on ``parent.name`` would map every trace file to a bucket named ``i1``,
    ``i2``, etc. This test locks in workload-level keying (``parent.parent``)."""
    stdout = (
        "1700000000 /data/r/baseline/wl-a/i1/trace_data.csv\n"
        "1700000500 /data/r/baseline/wl-a/i2/trace_data.csv\n"
        "1700000100 /data/r/baseline/wl-b/i1/trace_data.csv\n"
    )
    with patch.object(deploy, "run", return_value=_fake_run(stdout)):
        mtimes = deploy._probe_remote_mtimes("pod", "/data/r/baseline", "ns")
    assert set(mtimes.keys()) == {"wl-a", "wl-b"}
    assert mtimes["wl-a"] == 1700000500  # newest across iterations
    assert mtimes["wl-b"] == 1700000100


def test_probe_remote_mtimes_takes_max_across_iterations():
    """Multiple iterations of the same workload collapse to a single max
    mtime — the up-to-date check compares this against the local minimum."""
    stdout = (
        "100 /data/r/p/wl/i1/trace_data.csv\n"
        "500 /data/r/p/wl/i2/trace_data.csv\n"
        "300 /data/r/p/wl/i3/trace_data.csv\n"
    )
    with patch.object(deploy, "run", return_value=_fake_run(stdout)):
        mtimes = deploy._probe_remote_mtimes("pod", "/data/r/p", "ns")
    assert mtimes == {"wl": 500.0}


def test_probe_remote_mtimes_probe_failure_returns_empty():
    with patch.object(deploy, "run",
                      return_value=_fake_run("", returncode=1, stderr="boom")):
        mtimes = deploy._probe_remote_mtimes("pod", "/data/r/p", "ns")
    assert mtimes == {}


def test_probe_remote_mtimes_no_traces_returns_empty():
    with patch.object(deploy, "run", return_value=_fake_run("")):
        mtimes = deploy._probe_remote_mtimes("pod", "/data/r/p", "ns")
    assert mtimes == {}


# ── _is_workload_up_to_date ──────────────────────────────────────────────────


def test_is_workload_up_to_date_false_when_remote_mtime_none():
    assert deploy._is_workload_up_to_date(Path("/nonexistent"), None) is False


def test_is_workload_up_to_date_false_when_wl_dir_missing(tmp_path):
    assert deploy._is_workload_up_to_date(tmp_path / "missing", 100.0) is False


def test_is_workload_up_to_date_false_when_no_iteration_dirs(tmp_path):
    """Empty workload directory — nothing has been collected yet."""
    wl = tmp_path / "wl"
    wl.mkdir()
    assert deploy._is_workload_up_to_date(wl, 100.0) is False


def test_is_workload_up_to_date_true_when_every_iteration_is_fresh(tmp_path):
    """Local: two iterations, both newer than remote → skip."""
    wl = tmp_path / "wl"
    (wl / "i1").mkdir(parents=True)
    (wl / "i2").mkdir(parents=True)
    (wl / "i1" / "trace_data.csv").write_text("i1")
    (wl / "i2" / "trace_data.csv").write_text("i2")
    import os as _os
    _os.utime(wl / "i1" / "trace_data.csv", (1000, 1000))
    _os.utime(wl / "i2" / "trace_data.csv", (2000, 2000))
    # Remote max is 500, so both local iterations are newer.
    assert deploy._is_workload_up_to_date(wl, 500.0) is True


def test_is_workload_up_to_date_false_when_oldest_local_is_stale(tmp_path):
    """One local iteration older than remote → workload is redone."""
    wl = tmp_path / "wl"
    (wl / "i1").mkdir(parents=True)
    (wl / "i2").mkdir(parents=True)
    (wl / "i1" / "trace_data.csv").write_text("i1")
    (wl / "i2" / "trace_data.csv").write_text("i2")
    import os as _os
    _os.utime(wl / "i1" / "trace_data.csv", (100, 100))     # stale
    _os.utime(wl / "i2" / "trace_data.csv", (2000, 2000))   # fresh
    # Any local iteration older than remote means we can't skip.
    assert deploy._is_workload_up_to_date(wl, 500.0) is False


def test_is_workload_up_to_date_true_when_local_exactly_matches_remote(tmp_path):
    """Boundary: local mtime equal to remote counts as up-to-date."""
    wl = tmp_path / "wl"
    (wl / "i1").mkdir(parents=True)
    (wl / "i1" / "trace_data.csv").write_text("i1")
    import os as _os
    _os.utime(wl / "i1" / "trace_data.csv", (500, 500))
    assert deploy._is_workload_up_to_date(wl, 500.0) is True
