"""Tests for the incremental-collect internals: ``_probe_remote_mtimes``,
``_is_iteration_up_to_date``, and the cross-slot preservation guarantee.

Traces live under ``<phase>/<workload>/i<N>/trace_data.csv``. Issue #564
proved that collapsing mtimes to a single per-workload max — and pairing
that with a wipe-and-recopy of the whole workload dir — lost iterations
whenever the two replicas of a ``(phase, workload)`` pair dispatched to
different cluster slots. Both mechanisms now key on iterations, not
workloads. These tests lock in that per-iteration granularity end-to-end.

Issue #885 then moved the skip decision's evidence from ``trace_data.csv``'s
mtime to the ``.collect_complete`` marker, and made the copy itself transfer
only the inventory delta instead of wiping and re-streaming. The per-iteration
granularity #564 established is unchanged; only what counts as proof that an
iteration is fully collected has changed. See ``test_collect_incremental.py``
for the marker and delta-copy mechanics themselves.
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
#
# Issue #885: the evidence is the .collect_complete marker, not
# trace_data.csv's mtime. That file is copied early in the tar stream, so a
# fresh trace_data.csv survived a truncation and made a partial iteration
# look complete.


def test_is_iteration_up_to_date_false_when_remote_mtime_none():
    """None remote mtime means the probe failed or this iteration is absent
    from the current slot — cannot skip."""
    assert deploy._is_iteration_up_to_date(Path("/nonexistent"), None) is False


def test_is_iteration_up_to_date_false_when_iN_dir_missing(tmp_path):
    assert deploy._is_iteration_up_to_date(tmp_path / "i1", 100.0) is False


def test_is_iteration_up_to_date_false_when_marker_missing(tmp_path):
    """A fresh trace_data.csv is not proof the iteration is complete (#885)."""
    iN = tmp_path / "i1"
    iN.mkdir()
    csv = iN / "trace_data.csv"
    csv.write_text("data")
    os.utime(csv, (2000, 2000))
    assert deploy._is_iteration_up_to_date(iN, 500.0) is False


def test_is_iteration_up_to_date_true_when_marker_is_current(tmp_path):
    iN = tmp_path / "i1"
    iN.mkdir()
    deploy._write_collect_marker(iN, {"trace_data.csv": 4}, 2000.0)
    assert deploy._is_iteration_up_to_date(iN, 500.0) is True


def test_is_iteration_up_to_date_false_when_marker_is_stale(tmp_path):
    iN = tmp_path / "i1"
    iN.mkdir()
    deploy._write_collect_marker(iN, {"trace_data.csv": 4}, 100.0)
    assert deploy._is_iteration_up_to_date(iN, 500.0) is False


def test_is_iteration_up_to_date_true_when_marker_exactly_matches_remote(tmp_path):
    """Boundary: a marker recorded at exactly the remote mtime counts as
    up-to-date."""
    iN = tmp_path / "i1"
    iN.mkdir()
    deploy._write_collect_marker(iN, {"trace_data.csv": 4}, 500.0)
    assert deploy._is_iteration_up_to_date(iN, 500.0) is True


# ── Cross-slot preservation (issue #564) ─────────────────────────────────────


def _pvc_mock(iterations: str, inventory: dict, payloads: dict,
              cp_calls: "list | None" = None):
    """Build a ``deploy.run`` stand-in for one slot's PVC.

    *iterations* is the ``ls`` output; *inventory* maps ``iN`` to
    ``{relpath: size}``; *payloads* maps ``(iN, relpath)`` to the text a copy
    should materialize. Sizes in *inventory* must match those payloads, or the
    copy is judged short and the iteration stays unmarked.
    """
    def mock_run(cmd, **kwargs):
        cmd_list = list(cmd)
        joined = " ".join(cmd_list)
        if "exec" in cmd_list and "find" in joined and "stat" in joined:
            iN = next((k for k in inventory if f"/{k}" in joined), None)
            lines = "".join(f"{sz}|./{rel}\n"
                            for rel, sz in sorted(inventory.get(iN, {}).items()))
            return MagicMock(returncode=0, stdout=lines, stderr="")
        if "exec" in cmd_list and "ls " in joined:
            return MagicMock(returncode=0, stdout=iterations, stderr="")
        if "cp" in cmd_list:
            src = cmd_list[cmd_list.index("cp") + 1]
            dst = Path(cmd_list[cmd_list.index("cp") + 2])
            if cp_calls is not None:
                cp_calls.append(src)
            iN = next((k for k in inventory if f"/{k}/" in src), None)
            # The source names either a single file or a directory; materialize
            # every payload whose relpath sits under it.
            _, _, rel_root = src.rstrip("/").rpartition(f"/{iN}/")
            for (p_iN, rel), body in payloads.items():
                if p_iN != iN:
                    continue
                if rel == rel_root:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    dst.write_text(body)
                elif not rel_root or rel.startswith(f"{rel_root}/"):
                    tail = rel[len(rel_root) + 1:] if rel_root else rel
                    out = dst / tail
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_text(body)
            return MagicMock(returncode=0, stdout="", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")
    return mock_run


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

    body = "iteration-2 payload from slot B"
    # slot B's PVC only holds i2, per the issue-report cluster-side verification.
    mock_run = _pvc_mock(
        iterations="i2\n",
        inventory={"i2": {"trace_data.csv": len(body)}},
        payloads={("i2", "trace_data.csv"): body},
    )

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
    assert i2.read_text() == body


def test_copy_workload_iterations_skips_only_up_to_date_iteration(tmp_path):
    """When both iterations are on the current slot's PVC but one already
    carries a current completeness marker, only the unmarked one is copied.
    The marked one is left untouched (no wipe, no re-cp)."""
    wl_dest = tmp_path / "wl-x"
    (wl_dest / "i1").mkdir(parents=True)
    (wl_dest / "i2").mkdir(parents=True)
    # i1 is complete and current — should be skipped.
    fresh = wl_dest / "i1" / "trace_data.csv"
    fresh.write_text("fresh")
    deploy._write_collect_marker(wl_dest / "i1", {"trace_data.csv": 5}, 2000.0)
    # i2 has content but no marker — a partial copy; must be resumed.
    (wl_dest / "i2" / "trace_data.csv").write_text("stal")

    cp_calls: list[str] = []
    body = "refetched"
    mock_run = _pvc_mock(
        iterations="i1\ni2\n",
        inventory={"i1": {"trace_data.csv": 5},
                   "i2": {"trace_data.csv": len(body)}},
        payloads={("i2", "trace_data.csv"): body},
        cp_calls=cp_calls,
    )

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
