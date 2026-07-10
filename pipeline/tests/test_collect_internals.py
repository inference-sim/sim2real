"""Tests for the incremental-collect internals: ``_probe_remote_mtimes``,
``_is_iteration_up_to_date``, and the cross-slot preservation guarantee.

Traces live under ``<phase>/<workload>/i<N>/trace_data.csv``. Issue #564
proved that collapsing mtimes to a single per-workload max — and pairing
that with a wipe-and-recopy of the whole workload dir — lost iterations
whenever the two replicas of a ``(phase, workload)`` pair dispatched to
different cluster slots. Both mechanisms now key on iterations, not
workloads. These tests lock in that per-iteration granularity end-to-end.
"""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

from pipeline import deploy


# ── _probe_remote_mtimes ─────────────────────────────────────────────────────


def _fake_run(stdout: str, returncode: int = 0, stderr: str = ""):
    """Return a MagicMock stand-in for a completed subprocess.CompletedProcess."""
    class _R:
        pass
    r = _R()
    r.stdout = stdout
    r.stderr = stderr
    r.returncode = returncode
    return r


def test_probe_remote_mtimes_returns_nested_dict_keyed_by_workload_and_iteration():
    """Keys are ``(workload, iN)`` — both levels preserved. Previously the
    per-iN mtime was collapsed to a single per-workload max, hiding the
    cross-slot data-loss window described in issue #564."""
    stdout = (
        "1700000000 /data/r/baseline/wl-a/i1/trace_data.csv\n"
        "1700000500 /data/r/baseline/wl-a/i2/trace_data.csv\n"
        "1700000100 /data/r/baseline/wl-b/i1/trace_data.csv\n"
    )
    with patch.object(deploy, "run", return_value=_fake_run(stdout)):
        mtimes = deploy._probe_remote_mtimes("pod", "/data/r/baseline", "ns")
    assert mtimes == {
        "wl-a": {"i1": 1700000000.0, "i2": 1700000500.0},
        "wl-b": {"i1": 1700000100.0},
    }


def test_probe_remote_mtimes_preserves_each_iteration_independently():
    """Distinct iterations of the same workload keep their own mtimes — no
    max-collapse. This is the property the cross-slot fix depends on."""
    stdout = (
        "100 /data/r/p/wl/i1/trace_data.csv\n"
        "500 /data/r/p/wl/i2/trace_data.csv\n"
        "300 /data/r/p/wl/i3/trace_data.csv\n"
    )
    with patch.object(deploy, "run", return_value=_fake_run(stdout)):
        mtimes = deploy._probe_remote_mtimes("pod", "/data/r/p", "ns")
    assert mtimes == {"wl": {"i1": 100.0, "i2": 500.0, "i3": 300.0}}


def test_probe_remote_mtimes_probe_failure_returns_empty():
    with patch.object(deploy, "run",
                      return_value=_fake_run("", returncode=1, stderr="boom")):
        mtimes = deploy._probe_remote_mtimes("pod", "/data/r/p", "ns")
    assert mtimes == {}


def test_probe_remote_mtimes_no_traces_returns_empty():
    with patch.object(deploy, "run", return_value=_fake_run("")):
        mtimes = deploy._probe_remote_mtimes("pod", "/data/r/p", "ns")
    assert mtimes == {}


# ── _is_iteration_up_to_date ─────────────────────────────────────────────────


def test_is_iteration_up_to_date_false_when_remote_mtime_none():
    """None remote mtime means the probe failed or this iteration is absent
    from the current slot — cannot skip."""
    assert deploy._is_iteration_up_to_date(Path("/nonexistent"), None) is False


def test_is_iteration_up_to_date_false_when_iN_dir_missing(tmp_path):
    assert deploy._is_iteration_up_to_date(tmp_path / "i1", 100.0) is False


def test_is_iteration_up_to_date_false_when_trace_csv_missing(tmp_path):
    """Directory exists but no trace_data.csv — the iteration hasn't been
    collected yet."""
    iN = tmp_path / "i1"
    iN.mkdir()
    assert deploy._is_iteration_up_to_date(iN, 100.0) is False


def test_is_iteration_up_to_date_true_when_local_is_fresh(tmp_path):
    iN = tmp_path / "i1"
    iN.mkdir()
    csv = iN / "trace_data.csv"
    csv.write_text("data")
    os.utime(csv, (2000, 2000))
    assert deploy._is_iteration_up_to_date(iN, 500.0) is True


def test_is_iteration_up_to_date_false_when_local_is_stale(tmp_path):
    iN = tmp_path / "i1"
    iN.mkdir()
    csv = iN / "trace_data.csv"
    csv.write_text("data")
    os.utime(csv, (100, 100))
    assert deploy._is_iteration_up_to_date(iN, 500.0) is False


def test_is_iteration_up_to_date_true_when_local_exactly_matches_remote(tmp_path):
    """Boundary: local mtime equal to remote counts as up-to-date."""
    iN = tmp_path / "i1"
    iN.mkdir()
    csv = iN / "trace_data.csv"
    csv.write_text("data")
    os.utime(csv, (500, 500))
    assert deploy._is_iteration_up_to_date(iN, 500.0) is True


# ── Cross-slot preservation (issue #564) ─────────────────────────────────────


def test_copy_workload_iterations_preserves_prior_slot_iteration(tmp_path):
    """The direct #564 repro: slot A has already copied i1, slot B holds i2
    on its PVC. When ``_copy_workload_iterations_full`` runs against slot B,
    it must add i2 to disk WITHOUT wiping i1.
    """
    wl_dest = tmp_path / "wl-x"
    (wl_dest / "i1").mkdir(parents=True)
    prior = wl_dest / "i1" / "trace_data.csv"
    prior.write_text("iteration-1 payload from slot A")
    # Make i1 look plausibly older than slot B's remote copy of i2 (i2 hasn't
    # been fetched yet, so the up-to-date check should be neutral for i2).
    os.utime(prior, (500, 500))

    def mock_run(cmd, **kwargs):
        cmd_list = list(cmd)
        # `kubectl exec … sh -c 'ls /data/run/phase/wl-x/'` — slot B PVC
        # only holds i2, per the issue-report cluster-side verification.
        if "exec" in cmd_list and any("ls " in c for c in cmd_list):
            return MagicMock(returncode=0, stdout="i2\n", stderr="")
        # `kubectl cp … /wl-x/i2/ tmp_path/wl-x/i2` — fake the copy by
        # writing a trace file at the destination.
        if "cp" in cmd_list:
            dst = cmd_list[cmd_list.index("cp") + 2]
            Path(dst).mkdir(parents=True, exist_ok=True)
            (Path(dst) / "trace_data.csv").write_text("iteration-2 payload from slot B")
            return MagicMock(returncode=0, stdout="", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch.object(deploy, "run", side_effect=mock_run):
        errors = deploy._copy_workload_iterations_full(
            "pod-B", "run", "baseline", "wl-x", "ns-B",
            wl_dest, wl_remote_mtimes={"i2": 1000.0},
        )

    assert errors == []
    # Prior iteration must survive slot B's collect — the workload dir was
    # never wiped as a whole.
    assert prior.exists()
    assert prior.read_text() == "iteration-1 payload from slot A"
    # New iteration must land alongside it.
    i2 = wl_dest / "i2" / "trace_data.csv"
    assert i2.exists()
    assert i2.read_text() == "iteration-2 payload from slot B"


def test_copy_workload_iterations_skips_only_up_to_date_iteration(tmp_path):
    """When both iterations are on the current slot's PVC but one is already
    up-to-date locally, only the stale one is re-copied. The other is left
    untouched (no wipe, no re-cp)."""
    wl_dest = tmp_path / "wl-x"
    (wl_dest / "i1").mkdir(parents=True)
    (wl_dest / "i2").mkdir(parents=True)
    # i1 is fresh — should be skipped
    fresh = wl_dest / "i1" / "trace_data.csv"
    fresh.write_text("fresh")
    os.utime(fresh, (2000, 2000))
    # i2 is stale — should be re-copied
    stale = wl_dest / "i2" / "trace_data.csv"
    stale.write_text("stale")
    os.utime(stale, (100, 100))

    cp_calls: list[str] = []

    def mock_run(cmd, **kwargs):
        cmd_list = list(cmd)
        if "exec" in cmd_list and any("ls " in c for c in cmd_list):
            return MagicMock(returncode=0, stdout="i1\ni2\n", stderr="")
        if "cp" in cmd_list:
            src = cmd_list[cmd_list.index("cp") + 1]
            cp_calls.append(src)
            dst = cmd_list[cmd_list.index("cp") + 2]
            Path(dst).mkdir(parents=True, exist_ok=True)
            (Path(dst) / "trace_data.csv").write_text("refetched")
            return MagicMock(returncode=0, stdout="", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch.object(deploy, "run", side_effect=mock_run):
        errors = deploy._copy_workload_iterations_full(
            "pod", "run", "baseline", "wl-x", "ns",
            wl_dest, wl_remote_mtimes={"i1": 1000.0, "i2": 1000.0},
        )

    assert errors == []
    assert len(cp_calls) == 1, f"expected exactly one cp, got: {cp_calls}"
    assert "/wl-x/i2/" in cp_calls[0]
    # i1's original content must remain untouched (no wipe, no re-copy).
    assert fresh.read_text() == "fresh"


def test_copy_workload_iterations_returns_error_when_pvc_ls_fails(tmp_path):
    wl_dest = tmp_path / "wl-x"

    def mock_run(cmd, **kwargs):
        return MagicMock(returncode=1, stdout="", stderr="boom")

    with patch.object(deploy, "run", side_effect=mock_run):
        errors = deploy._copy_workload_iterations_full(
            "pod", "run", "baseline", "wl-x", "ns",
            wl_dest, wl_remote_mtimes={},
        )

    assert len(errors) == 1
    assert "failed to list iterations" in errors[0]


def test_copy_workload_iterations_returns_error_when_no_iN_on_pvc(tmp_path):
    wl_dest = tmp_path / "wl-x"

    def mock_run(cmd, **kwargs):
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch.object(deploy, "run", side_effect=mock_run):
        errors = deploy._copy_workload_iterations_full(
            "pod", "run", "baseline", "wl-x", "ns",
            wl_dest, wl_remote_mtimes={},
        )

    assert len(errors) == 1
    assert "no i<N>/ iteration subdirs" in errors[0]
