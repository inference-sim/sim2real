"""Tests for inventory-driven incremental collect (issue #885).

Issue #885 proved three things about the old whole-iteration ``kubectl cp``:
it died on a 120 s control-plane guard for iterations over ~420 MB, each
retry ``rmtree``d the destination so repetition could not converge, and a
truncated iteration was subsequently reported complete because
``_is_iteration_up_to_date`` trusted ``trace_data.csv`` alone. These tests
lock in the inventory diff, the two-pass copy, timeout containment, and the
``.collect_complete`` marker gate.
"""

import os
import subprocess
from pathlib import Path

from pipeline import deploy


def _fake_run(stdout: str = "", returncode: int = 0, stderr: str = ""):
    class _R:
        pass
    r = _R()
    r.stdout = stdout
    r.stderr = stderr
    r.returncode = returncode
    return r


# ── _remote_file_inventory ───────────────────────────────────────────────────


def test_remote_inventory_parses_size_and_relpath(monkeypatch):
    """``stat -c '%s|%n'`` lines become {relpath: size}, leading './' stripped."""
    stdout = (
        "1024|./trace_data.csv\n"
        "41497088|./epp_logs/epp_0010.log\n"
        "512|./metrics/raw/pod_a_metrics.log\n"
    )
    monkeypatch.setattr(deploy, "run", lambda *a, **k: _fake_run(stdout=stdout))
    inv, err = deploy._remote_file_inventory("pod", "ns", "/data/r/p/wl/i1")
    assert err is None
    assert inv == {
        "trace_data.csv": 1024,
        "epp_logs/epp_0010.log": 41497088,
        "metrics/raw/pod_a_metrics.log": 512,
    }


def test_remote_inventory_returns_error_on_nonzero_exit(monkeypatch):
    """A failed exec yields an error string, not a silent empty inventory."""
    monkeypatch.setattr(
        deploy, "run",
        lambda *a, **k: _fake_run(returncode=1, stderr="No such file or directory"))
    inv, err = deploy._remote_file_inventory("pod", "ns", "/data/r/p/wl/i9")
    assert inv == {}
    assert err is not None
    assert "No such file or directory" in err


def test_remote_inventory_returns_error_on_timeout(monkeypatch):
    """A TimeoutExpired during the probe is contained as an error string."""
    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd=["kubectl"], timeout=120)
    monkeypatch.setattr(deploy, "run", boom)
    inv, err = deploy._remote_file_inventory("pod", "ns", "/data/r/p/wl/i1")
    assert inv == {}
    assert err is not None
    assert "timed out" in err.lower()


def test_remote_inventory_warns_and_skips_unparseable_lines(monkeypatch):
    """A malformed line is skipped; the parseable ones still land."""
    stdout = "garbage-no-delimiter\nnotanumber|./a.log\n7|./b.log\n"
    monkeypatch.setattr(deploy, "run", lambda *a, **k: _fake_run(stdout=stdout))
    inv, err = deploy._remote_file_inventory("pod", "ns", "/d")
    assert err is None
    assert inv == {"b.log": 7}


def test_remote_inventory_empty_dir_is_success_not_error(monkeypatch):
    """An existing but empty directory is an empty inventory, error None."""
    monkeypatch.setattr(deploy, "run", lambda *a, **k: _fake_run(stdout="\n"))
    inv, err = deploy._remote_file_inventory("pod", "ns", "/d")
    assert inv == {}
    assert err is None


# ── _local_file_inventory ────────────────────────────────────────────────────


def test_local_inventory_walks_recursively_with_posix_relpaths(tmp_path):
    (tmp_path / "epp_logs").mkdir()
    (tmp_path / "trace_data.csv").write_bytes(b"x" * 10)
    (tmp_path / "epp_logs" / "a.log").write_bytes(b"y" * 3)
    assert deploy._local_file_inventory(tmp_path) == {
        "trace_data.csv": 10,
        "epp_logs/a.log": 3,
    }


def test_local_inventory_excludes_the_marker(tmp_path):
    """The marker is our own bookkeeping and must never enter the diff."""
    (tmp_path / "trace_data.csv").write_bytes(b"x")
    (tmp_path / deploy.COLLECT_MARKER).write_text("{}")
    assert deploy._local_file_inventory(tmp_path) == {"trace_data.csv": 1}


def test_local_inventory_of_missing_dir_is_empty(tmp_path):
    assert deploy._local_file_inventory(tmp_path / "nope") == {}


def test_local_inventory_treats_an_unstatable_file_as_missing(tmp_path, monkeypatch):
    """A file we cannot stat is left out of the inventory, so the delta treats
    it as missing and re-fetches it — the safe direction."""
    (tmp_path / "a.log").write_bytes(b"xx")
    real_stat = Path.stat
    seen = {"a.log": 0}

    def boom(self, *a, **k):
        # is_file() stats first; fail only the size lookup that follows it.
        if self.name == "a.log":
            seen["a.log"] += 1
            if seen["a.log"] > 1:
                raise OSError("permission denied")
        return real_stat(self, *a, **k)

    monkeypatch.setattr(Path, "stat", boom)
    assert deploy._local_file_inventory(tmp_path) == {}


# ── marker write / read ──────────────────────────────────────────────────────


def test_write_marker_records_counts_and_remote_mtime(tmp_path):
    inv = {"trace_data.csv": 10, "epp_logs/a.log": 5}
    deploy._write_collect_marker(tmp_path, inv, 1700000000.0)
    data = deploy._read_collect_marker(tmp_path)
    assert data["file_count"] == 2
    assert data["byte_count"] == 15
    assert data["remote_mtime"] == 1700000000.0
    assert "completed_at" in data
    assert len(data["inventory_sha256"]) == 64


def test_marker_inventory_hash_is_order_independent(tmp_path):
    """The hash is over canonically sorted entries, so dict order cannot
    make two identical inventories look different."""
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    deploy._write_collect_marker(a, {"x": 1, "y": 2}, None)
    deploy._write_collect_marker(b, {"y": 2, "x": 1}, None)
    assert (deploy._read_collect_marker(a)["inventory_sha256"]
            == deploy._read_collect_marker(b)["inventory_sha256"])


def test_read_marker_returns_none_when_absent(tmp_path):
    assert deploy._read_collect_marker(tmp_path) is None


def test_read_marker_returns_none_when_corrupt(tmp_path):
    (tmp_path / deploy.COLLECT_MARKER).write_text("{not json")
    assert deploy._read_collect_marker(tmp_path) is None


# ── _is_iteration_up_to_date: the marker gate ────────────────────────────────


def test_not_up_to_date_when_marker_absent_even_if_trace_csv_is_fresh(tmp_path):
    """Defect 3 of #885. A truncated iteration keeps a fresh trace_data.csv
    because it is copied early in the tar stream. Without a marker the
    iteration must be re-examined, never skipped."""
    iN = tmp_path / "i1"
    iN.mkdir()
    csv = iN / "trace_data.csv"
    csv.write_text("x")
    os.utime(csv, (2_000_000_000, 2_000_000_000))
    assert deploy._is_iteration_up_to_date(iN, 1_000_000_000.0) is False


def test_up_to_date_when_marker_present_and_not_stale(tmp_path):
    iN = tmp_path / "i1"
    iN.mkdir()
    deploy._write_collect_marker(iN, {"trace_data.csv": 1}, 1_000_000_000.0)
    assert deploy._is_iteration_up_to_date(iN, 1_000_000_000.0) is True


def test_not_up_to_date_when_marker_is_stale_relative_to_remote(tmp_path):
    """Remote grew after the marker was written — re-collect."""
    iN = tmp_path / "i1"
    iN.mkdir()
    deploy._write_collect_marker(iN, {"trace_data.csv": 1}, 1_000_000_000.0)
    assert deploy._is_iteration_up_to_date(iN, 1_500_000_000.0) is False


def test_not_up_to_date_when_remote_mtime_is_none(tmp_path):
    """Issue #564 invariant: no probe means we cannot skip."""
    iN = tmp_path / "i1"
    iN.mkdir()
    deploy._write_collect_marker(iN, {"trace_data.csv": 1}, 1_000_000_000.0)
    assert deploy._is_iteration_up_to_date(iN, None) is False


def test_up_to_date_when_marker_has_no_recorded_remote_mtime(tmp_path):
    """A marker written when the probe had failed records remote_mtime=None.
    It still proves the transfer completed, so trust it against any remote
    mtime rather than re-copying forever."""
    iN = tmp_path / "i1"
    iN.mkdir()
    deploy._write_collect_marker(iN, {"trace_data.csv": 1}, None)
    assert deploy._is_iteration_up_to_date(iN, 1_500_000_000.0) is True


def test_not_up_to_date_when_marker_mtime_is_not_a_number(tmp_path):
    """A hand-edited or corrupted marker must not be trusted."""
    iN = tmp_path / "i1"
    iN.mkdir()
    deploy._write_collect_marker(iN, {"trace_data.csv": 1}, 100.0)
    marker = iN / deploy.COLLECT_MARKER
    marker.write_text('{"remote_mtime": "not-a-number"}')
    assert deploy._is_iteration_up_to_date(iN, 100.0) is False


# ── _copy_iteration_incremental ──────────────────────────────────────────────


class _FakePVC:
    """A fake extractor pod: an in-memory remote tree plus a scripted
    ``kubectl`` responder that materializes files on 'cp'.

    ``fail_paths`` maps a remote-path fragment to a failure mode to inject
    ("timeout" or "error"); ``truncate`` maps a relpath to the byte count that
    should land instead of the full size, modelling a stream cut mid-file.
    """

    def __init__(self, tree, *, fail_paths=None, truncate=None):
        self.tree = dict(tree)              # relpath -> size
        self.fail_paths = dict(fail_paths or {})
        self.truncate = dict(truncate or {})
        self.calls = []

    def _match_fail(self, cmd_str):
        for frag, mode in self.fail_paths.items():
            if frag in cmd_str:
                return mode
        return None

    def __call__(self, cmd, **kwargs):
        cmd_str = " ".join(cmd)
        self.calls.append(cmd_str)
        if "find" in cmd_str and "stat" in cmd_str:
            lines = "".join(f"{sz}|./{rel}\n"
                            for rel, sz in sorted(self.tree.items()))
            return _fake_run(stdout=lines)
        if cmd[:2] == ["kubectl", "cp"]:
            mode = self._match_fail(cmd_str)
            if mode == "timeout":
                raise subprocess.TimeoutExpired(cmd=cmd, timeout=120)
            if mode == "error":
                return _fake_run(returncode=1, stderr="injected cp failure")
            self._materialize(cmd[2], Path(cmd[3]))
            return _fake_run()
        return _fake_run()

    def _materialize(self, remote, local):
        """Copy the remote subtree-or-file named by *remote* into *local*."""
        _, _, path = remote.partition(":")
        # Everything after the iteration directory is the relative root.
        head, _, tail = path.rstrip("/").rpartition("/i1")
        rel_root = tail.lstrip("/")
        if rel_root in self.tree:                       # single file
            self._write(rel_root, local)
            return
        prefix = f"{rel_root}/" if rel_root else ""
        for rel in self.tree:
            if rel.startswith(prefix):
                self._write(rel, local / rel[len(prefix):])

    def _write(self, rel, dest):
        size = self.truncate.get(rel, self.tree[rel])
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"\0" * size)


def test_incremental_copy_transfers_everything_and_writes_marker(tmp_path, monkeypatch):
    tree = {"trace_data.csv": 4, "epp_logs/a.log": 8, "metrics/raw/m.log": 2}
    monkeypatch.setattr(deploy, "run", _FakePVC(tree))
    dest = tmp_path / "i1"
    res = deploy._copy_iteration_incremental(
        "pod", "ns", "/data/r/p/wl/i1", dest, remote_mtime=100.0)
    assert res.complete is True
    assert res.errors == []
    assert deploy._local_file_inventory(dest) == tree
    assert deploy._read_collect_marker(dest)["file_count"] == 3


def test_incremental_copy_no_marker_when_a_file_times_out(tmp_path, monkeypatch):
    """Acceptance criterion: a partially-copied iteration is never marked."""
    tree = {"trace_data.csv": 4, "epp_logs/big.log": 64}
    monkeypatch.setattr(deploy, "run",
                        _FakePVC(tree, fail_paths={"epp_logs": "timeout"}))
    dest = tmp_path / "i1"
    res = deploy._copy_iteration_incremental(
        "pod", "ns", "/data/r/p/wl/i1", dest, remote_mtime=100.0)
    assert res.complete is False
    assert deploy._read_collect_marker(dest) is None
    assert res.missing == ["epp_logs/big.log"]
    assert any("timed out" in e.lower() for e in res.errors)
    assert res.trace_ok is True          # trace_data.csv landed


def test_a_timeout_never_propagates_out_of_the_helper(tmp_path, monkeypatch):
    """Defect 4 of #885: TimeoutExpired escapes `check=False`, so before this
    change a single oversized file unwound past every remaining iteration and
    workload to the slot-level handler. It must now be contained."""
    tree = {"a.log": 4}
    monkeypatch.setattr(deploy, "run",
                        _FakePVC(tree, fail_paths={"a.log": "timeout"}))
    res = deploy._copy_iteration_incremental(
        "pod", "ns", "/data/r/p/wl/i1", tmp_path / "i1", remote_mtime=100.0)
    assert res.complete is False
    assert res.errors


def test_cp_error_is_recorded_not_raised(tmp_path, monkeypatch):
    tree = {"a.log": 4}
    monkeypatch.setattr(deploy, "run",
                        _FakePVC(tree, fail_paths={"a.log": "error"}))
    res = deploy._copy_iteration_incremental(
        "pod", "ns", "/data/r/p/wl/i1", tmp_path / "i1", remote_mtime=100.0)
    assert res.complete is False
    assert any("injected cp failure" in e for e in res.errors)


def test_retry_transfers_only_the_delta_then_marks_complete(tmp_path, monkeypatch):
    """Acceptance criterion: a partial copy followed by a re-run converges,
    moving only the missing files. Defect 2 was that each attempt rmtree'd
    the destination, so every attempt re-streamed the whole directory."""
    tree = {"trace_data.csv": 4, "epp_logs/a.log": 8, "epp_logs/b.log": 8}
    dest = tmp_path / "i1"

    # First attempt: the bulk cp of epp_logs/ times out, so nothing under it
    # lands; trace_data.csv (a root-level entry) does.
    first = _FakePVC(tree, fail_paths={"epp_logs": "timeout"})
    monkeypatch.setattr(deploy, "run", first)
    r1 = deploy._copy_iteration_incremental(
        "pod", "ns", "/data/r/p/wl/i1", dest, remote_mtime=100.0)
    assert r1.complete is False
    assert sorted(deploy._local_file_inventory(dest)) == ["trace_data.csv"]

    # Second attempt: only the two missing files are fetched, one cp each.
    second = _FakePVC(tree)
    monkeypatch.setattr(deploy, "run", second)
    r2 = deploy._copy_iteration_incremental(
        "pod", "ns", "/data/r/p/wl/i1", dest, remote_mtime=100.0)
    assert r2.complete is True
    cps = [c for c in second.calls if c.startswith("kubectl cp")]
    assert len(cps) == 2, cps
    assert all("epp_logs/" in c for c in cps), cps
    assert deploy._read_collect_marker(dest) is not None


def test_incremental_copy_recopies_a_short_file(tmp_path, monkeypatch):
    """A file that landed truncated is re-copied whole — kubectl cp cannot
    resume mid-file, and a size mismatch is exactly the signal we have."""
    tree = {"epp_logs/a.log": 100}
    dest = tmp_path / "i1"
    (dest / "epp_logs").mkdir(parents=True)
    (dest / "epp_logs" / "a.log").write_bytes(b"\0" * 41)   # truncated
    monkeypatch.setattr(deploy, "run", _FakePVC(tree))
    res = deploy._copy_iteration_incremental(
        "pod", "ns", "/data/r/p/wl/i1", dest, remote_mtime=100.0)
    assert res.complete is True
    assert res.short == []
    assert (dest / "epp_logs" / "a.log").stat().st_size == 100


def test_short_file_is_reported_when_recopy_also_truncates(tmp_path, monkeypatch):
    tree = {"epp_logs/a.log": 100}
    monkeypatch.setattr(deploy, "run",
                        _FakePVC(tree, truncate={"epp_logs/a.log": 41}))
    dest = tmp_path / "i1"
    res = deploy._copy_iteration_incremental(
        "pod", "ns", "/data/r/p/wl/i1", dest, remote_mtime=100.0)
    assert res.complete is False
    assert res.short == [("epp_logs/a.log", 41, 100)]
    assert deploy._read_collect_marker(dest) is None


def test_zero_transfer_when_local_already_matches_remote(tmp_path, monkeypatch):
    """The pre-#885 migration path: a run collected before the marker existed
    has no marker, so it is re-examined — but the delta is empty, so nothing
    is transferred and the marker is simply written."""
    tree = {"trace_data.csv": 4, "epp_logs/a.log": 8}
    dest = tmp_path / "i1"
    (dest / "epp_logs").mkdir(parents=True)
    (dest / "trace_data.csv").write_bytes(b"\0" * 4)
    (dest / "epp_logs" / "a.log").write_bytes(b"\0" * 8)
    fake = _FakePVC(tree)
    monkeypatch.setattr(deploy, "run", fake)
    res = deploy._copy_iteration_incremental(
        "pod", "ns", "/data/r/p/wl/i1", dest, remote_mtime=100.0)
    assert res.complete is True
    assert [c for c in fake.calls if c.startswith("kubectl cp")] == []
    assert deploy._read_collect_marker(dest) is not None


def test_stale_marker_forces_a_full_refetch(tmp_path, monkeypatch):
    """A marker that the caller did not accept means the remote moved on.
    Sizes can match while contents differ, so re-fetch every file rather than
    trusting the size diff."""
    tree = {"trace_data.csv": 4, "epp_logs/a.log": 8}
    dest = tmp_path / "i1"
    (dest / "epp_logs").mkdir(parents=True)
    (dest / "trace_data.csv").write_bytes(b"\0" * 4)
    (dest / "epp_logs" / "a.log").write_bytes(b"\0" * 8)
    deploy._write_collect_marker(dest, tree, 100.0)          # stale marker
    fake = _FakePVC(tree)
    monkeypatch.setattr(deploy, "run", fake)
    res = deploy._copy_iteration_incremental(
        "pod", "ns", "/data/r/p/wl/i1", dest, remote_mtime=999.0)
    assert res.complete is True
    cps = [c for c in fake.calls if c.startswith("kubectl cp")]
    assert len(cps) == 2, cps
    assert deploy._read_collect_marker(dest)["remote_mtime"] == 999.0


def test_exclude_subdirs_drops_those_entries_from_the_contract(tmp_path, monkeypatch):
    """--skip-logs excludes server_logs. Excluded files must not appear in the
    inventory, must not be copied, and must not block the marker."""
    tree = {"trace_data.csv": 4, "server_logs/vllm.log": 999, "epp_logs/a.log": 8}
    fake = _FakePVC(tree)
    monkeypatch.setattr(deploy, "run", fake)
    dest = tmp_path / "i1"
    res = deploy._copy_iteration_incremental(
        "pod", "ns", "/data/r/p/wl/i1", dest,
        exclude_subdirs=frozenset({"server_logs"}), remote_mtime=100.0)
    assert res.complete is True
    assert res.files_total == 2
    assert not (dest / "server_logs").exists()
    assert all("server_logs" not in c
               for c in fake.calls if c.startswith("kubectl cp"))


def test_inventory_error_yields_no_copy_and_no_marker(tmp_path, monkeypatch):
    monkeypatch.setattr(
        deploy, "run",
        lambda *a, **k: _fake_run(returncode=1, stderr="No such file"))
    dest = tmp_path / "i1"
    res = deploy._copy_iteration_incremental(
        "pod", "ns", "/data/r/p/wl/i1", dest, remote_mtime=100.0)
    assert res.complete is False
    assert res.errors and "No such file" in res.errors[0]
    assert deploy._read_collect_marker(dest) is None


def test_empty_remote_iteration_is_complete(tmp_path, monkeypatch):
    """An iteration with no files is vacuously complete — mark it so a later
    collect does not re-probe it forever."""
    monkeypatch.setattr(deploy, "run", _FakePVC({}))
    dest = tmp_path / "i1"
    res = deploy._copy_iteration_incremental(
        "pod", "ns", "/data/r/p/wl/i1", dest, remote_mtime=100.0)
    assert res.complete is True
    assert res.files_total == 0
    assert deploy._read_collect_marker(dest)["file_count"] == 0


def test_bulk_pass_is_skipped_when_local_content_exists(tmp_path, monkeypatch):
    """Defect 1's retry trap: re-streaming the whole directory just burns
    another 120 s. With any local content present, go straight to per-file."""
    tree = {"trace_data.csv": 4, "epp_logs/a.log": 8, "epp_logs/b.log": 8}
    dest = tmp_path / "i1"
    (dest / "epp_logs").mkdir(parents=True)
    (dest / "trace_data.csv").write_bytes(b"\0" * 4)
    (dest / "epp_logs" / "a.log").write_bytes(b"\0" * 8)
    fake = _FakePVC(tree)
    monkeypatch.setattr(deploy, "run", fake)
    deploy._copy_iteration_incremental(
        "pod", "ns", "/data/r/p/wl/i1", dest, remote_mtime=100.0)
    cps = [c for c in fake.calls if c.startswith("kubectl cp")]
    assert len(cps) == 1 and "b.log" in cps[0], cps


def test_bulk_pass_uses_one_cp_per_top_level_entry(tmp_path, monkeypatch):
    """The fast path for a fresh iteration: one cp per root-level file and one
    per root-level directory, not one per file in the tree."""
    tree = {
        "trace_data.csv": 4, "trace_header.yaml": 2,
        "epp_logs/a.log": 8, "epp_logs/b.log": 8,
        "metrics/raw/m1.log": 1, "metrics/raw/m2.log": 1,
    }
    fake = _FakePVC(tree)
    monkeypatch.setattr(deploy, "run", fake)
    res = deploy._copy_iteration_incremental(
        "pod", "ns", "/data/r/p/wl/i1", tmp_path / "i1", remote_mtime=100.0)
    assert res.complete is True
    cps = [c for c in fake.calls if c.startswith("kubectl cp")]
    assert len(cps) == 4, cps       # 2 files + epp_logs/ + metrics/


# ── operator-facing reporting ────────────────────────────────────────────────


def test_human_bytes_formats_at_each_scale():
    assert deploy._human_bytes(512) == "512 B"
    assert deploy._human_bytes(2048) == "2.0 KB"
    assert deploy._human_bytes(41497088) == "39.6 MB"
    assert deploy._human_bytes(454033408).endswith(" MB")
    assert deploy._human_bytes(5 * 1024 ** 3) == "5.0 GB"
    assert deploy._human_bytes(3 * 1024 ** 4) == "3.0 TB"


def test_report_partial_names_every_thing_the_operator_needs(capsys):
    """Defect 4 of #885: the old warning printed the raw TimeoutExpired argv
    and nothing else. The operator's reasonable reading — 'most of it copied,
    I will just run it again' — led straight into defect 3."""
    res = deploy.IterationCopy(
        label="causalsloexternality/reasoning-single-turn/i1",
        complete=False, files_total=13, bytes_total=454033408,
        files_present=9, bytes_present=429916160,
        missing=["epp_logs/epp_0016.log", "metrics/raw/pod_a.log"],
        short=[("epp_logs/epp_0010.log", 41497088, 44000000)],
        errors=["epp_logs/epp_0010.log: timed out after 120s"],
    )
    deploy._report_partial(res, "try3", "causalsloexternality",
                           "reasoning-single-turn")
    out = capsys.readouterr().out
    assert "PARTIAL COPY" in out
    assert "9 of 13 files" in out
    assert "epp_logs/epp_0010.log" in out          # incomplete file named
    assert "epp_logs/epp_0016.log" in out          # missing file named
    assert "trace_data.csv" in out                 # trace verdict stated
    assert "deploy.py collect" in out              # resume command given
    assert "--run try3" in out
    assert "causalsloexternality" in out
    assert "resume" in out.lower()                 # says a re-run resumes


def test_report_partial_flags_a_lost_trace(capsys):
    res = deploy.IterationCopy(
        label="baseline/wl-a/i1", complete=False, files_total=2,
        bytes_total=10, missing=["trace_data.csv"])
    deploy._report_partial(res, "try3", "baseline", "wl-a")
    out = capsys.readouterr().out
    assert res.trace_ok is False
    assert "MISSING OR TRUNCATED" in out
    assert "is present and complete" not in out


def test_trace_ok_is_false_when_the_trace_is_short():
    res = deploy.IterationCopy(short=[("trace_data.csv", 10, 20)])
    assert res.trace_ok is False


def test_report_partial_truncates_long_file_lists(capsys):
    res = deploy.IterationCopy(
        label="baseline/wl-a/i1", complete=False, files_total=20,
        missing=[f"epp_logs/e_{i:04d}.log" for i in range(9)])
    deploy._report_partial(res, "try3", "baseline", "wl-a")
    out = capsys.readouterr().out
    assert "+4 more" in out


def test_partial_summary_lists_every_partial_cell(capsys):
    """A failure in cell 3 of 9 must not be lost in the scrollback."""
    partials = [
        deploy.IterationCopy(label="baseline/wl-a/i1", files_total=13,
                             files_present=9, bytes_total=100, bytes_present=90),
        deploy.IterationCopy(label="treatment/wl-b/i2", files_total=5,
                             files_present=1, bytes_total=50, bytes_present=10),
    ]
    deploy._print_partial_summary(partials, "try3")
    out = capsys.readouterr().out
    assert "baseline/wl-a/i1" in out
    assert "treatment/wl-b/i2" in out
    assert "2 iteration(s)" in out


def test_partial_summary_prints_nothing_when_all_complete(capsys):
    deploy._print_partial_summary([], "try3")
    assert capsys.readouterr().out == ""


# ── _copy_workload_iterations_full integration ───────────────────────────────


class _LsPVC(_FakePVC):
    """_FakePVC that also answers the ``ls`` iteration-discovery exec."""

    def __init__(self, tree, iterations="i1\n", **kw):
        super().__init__(tree, **kw)
        self.iterations = iterations

    def __call__(self, cmd, **kwargs):
        cmd_str = " ".join(cmd)
        if "exec" in cmd_str and "ls " in cmd_str and "find" not in cmd_str:
            self.calls.append(cmd_str)
            return _fake_run(stdout=self.iterations)
        return super().__call__(cmd, **kwargs)


def test_full_copy_records_partials_and_reports_them(tmp_path, monkeypatch):
    """A timeout is contained: the error is reported, the marker is withheld,
    and whatever landed before the cut survives on disk. Before #885 the
    exception unwound out of the loop to the slot-level handler."""
    tree = {"trace_data.csv": 4, "epp_logs/big.log": 64}
    fake = _LsPVC(tree, fail_paths={"epp_logs": "timeout"})
    monkeypatch.setattr(deploy, "run", fake)
    wl_dest = tmp_path / "baseline" / "wl-a"
    partials: list = []
    errors = deploy._copy_workload_iterations_full(
        "pod", "run-1", "baseline", "wl-a", "ns-0", wl_dest,
        {"i1": 100.0}, partials=partials)

    assert errors, "the timeout must be reported"
    assert deploy._read_collect_marker(wl_dest / "i1") is None
    assert [p.label for p in partials] == ["baseline/wl-a/i1"]
    assert (wl_dest / "i1" / "trace_data.csv").exists(), \
        "what landed before the timeout must survive"


def test_full_copy_continues_to_the_next_iteration_after_a_failure(tmp_path, monkeypatch):
    """i2 must still be collected when i1's copy fails."""
    tree = {"trace_data.csv": 4}

    class _TwoIter(_LsPVC):
        def _materialize(self, remote, local):
            # Model both i1 and i2 by keying off whichever appears.
            for tag in ("/i1", "/i2"):
                if tag in remote:
                    _, _, tail = remote.rstrip("/").rpartition(tag)
                    rel_root = tail.lstrip("/")
                    if rel_root in self.tree:
                        self._write(rel_root, local)
                    else:
                        for rel in self.tree:
                            self._write(rel, local / rel)
                    return

        def __call__(self, cmd, **kwargs):
            cmd_str = " ".join(cmd)
            if cmd[:2] == ["kubectl", "cp"] and "/i1/" in cmd_str:
                self.calls.append(cmd_str)
                raise subprocess.TimeoutExpired(cmd=cmd, timeout=120)
            return super().__call__(cmd, **kwargs)

    monkeypatch.setattr(deploy, "run", _TwoIter(tree, iterations="i1\ni2\n"))
    wl_dest = tmp_path / "baseline" / "wl-a"
    partials: list = []
    errors = deploy._copy_workload_iterations_full(
        "pod", "run-1", "baseline", "wl-a", "ns-0", wl_dest,
        {"i1": 100.0, "i2": 100.0}, partials=partials)

    assert errors
    assert deploy._read_collect_marker(wl_dest / "i1") is None
    assert deploy._read_collect_marker(wl_dest / "i2") is not None, \
        "i2 must still be collected after i1 failed"
    assert [p.label for p in partials] == ["baseline/wl-a/i1"]


def test_full_copy_skips_iterations_that_carry_a_current_marker(tmp_path, monkeypatch):
    tree = {"trace_data.csv": 4}
    wl_dest = tmp_path / "baseline" / "wl-a"
    (wl_dest / "i1").mkdir(parents=True)
    deploy._write_collect_marker(wl_dest / "i1", tree, 100.0)
    fake = _LsPVC(tree)
    monkeypatch.setattr(deploy, "run", fake)
    errors = deploy._copy_workload_iterations_full(
        "pod", "run-1", "baseline", "wl-a", "ns-0", wl_dest, {"i1": 100.0})
    assert errors == []
    assert [c for c in fake.calls if c.startswith("kubectl cp")] == []


def test_full_copy_never_wipes_already_landed_bytes(tmp_path, monkeypatch):
    """Defect 2: the old code rmtree'd iN_dest before every attempt, so each
    retry re-streamed the whole directory and died at the same place. A file
    the remote still holds must survive untouched."""
    tree = {"trace_data.csv": 4, "epp_logs/a.log": 8}
    wl_dest = tmp_path / "baseline" / "wl-a"
    (wl_dest / "i1").mkdir(parents=True)
    landed = wl_dest / "i1" / "trace_data.csv"
    landed.write_bytes(b"ABCD")
    fake = _LsPVC(tree)
    monkeypatch.setattr(deploy, "run", fake)
    deploy._copy_workload_iterations_full(
        "pod", "run-1", "baseline", "wl-a", "ns-0", wl_dest, {"i1": 100.0})
    assert landed.read_bytes() == b"ABCD", "already-landed bytes must survive"
    cps = [c for c in fake.calls if c.startswith("kubectl cp")]
    assert len(cps) == 1 and "epp_logs/a.log" in cps[0], cps


# ── stale-file pruning ───────────────────────────────────────────────────────


def test_prune_removes_locals_the_remote_no_longer_has(tmp_path, monkeypatch):
    """Wiping cleared stale files from an earlier collect; pruning gets that
    effect without destroying already-landed bytes."""
    tree = {"trace_data.csv": 4}
    dest = tmp_path / "i1"
    (dest / "epp_logs").mkdir(parents=True)
    stale = dest / "epp_logs" / "from_a_previous_run.log"
    stale.write_text("stale")
    (dest / "trace_data.csv").write_bytes(b"\0" * 4)
    monkeypatch.setattr(deploy, "run", _FakePVC(tree))
    res = deploy._copy_iteration_incremental(
        "pod", "ns", "/data/r/p/wl/i1", dest, remote_mtime=100.0)
    assert res.complete is True
    assert not stale.exists()
    assert not (dest / "epp_logs").exists(), "emptied directory is cleaned up"


def test_prune_keeps_excluded_subdir_files_the_remote_still_has(tmp_path, monkeypatch):
    """--skip-logs does not fetch server_logs, and must not delete a copy an
    earlier full collect already fetched. Pruning compares against the
    UNFILTERED remote inventory precisely so this holds."""
    tree = {"trace_data.csv": 4, "server_logs/vllm.log": 6}
    dest = tmp_path / "i1"
    (dest / "server_logs").mkdir(parents=True)
    kept = dest / "server_logs" / "vllm.log"
    kept.write_bytes(b"\0" * 6)
    monkeypatch.setattr(deploy, "run", _FakePVC(tree))
    res = deploy._copy_iteration_incremental(
        "pod", "ns", "/data/r/p/wl/i1", dest,
        exclude_subdirs=frozenset({"server_logs"}), remote_mtime=100.0)
    assert res.complete is True
    assert kept.exists(), "an excluded file the remote still has must survive"


def test_prune_clears_an_excluded_subdir_the_remote_dropped(tmp_path, monkeypatch):
    """The other half: under --skip-logs a stale server_logs/ that the remote
    no longer has is still cleared, matching the pre-#885 wipe."""
    tree = {"trace_data.csv": 4}
    dest = tmp_path / "i1"
    (dest / "server_logs").mkdir(parents=True)
    stale = dest / "server_logs" / "old.log"
    stale.write_text("stale")
    monkeypatch.setattr(deploy, "run", _FakePVC(tree))
    deploy._copy_iteration_incremental(
        "pod", "ns", "/data/r/p/wl/i1", dest,
        exclude_subdirs=frozenset({"server_logs"}), remote_mtime=100.0)
    assert not stale.exists()


def test_prune_warns_and_keeps_going_when_a_delete_fails(tmp_path, monkeypatch, capsys):
    """An undeletable stale file is warned about, not raised over — the copy of
    everything else must still proceed."""
    dest = tmp_path / "i1"
    dest.mkdir(parents=True)
    (dest / "stubborn.log").write_text("x")
    (dest / "also_stale.log").write_text("y")
    real_unlink = Path.unlink

    def boom(self, *a, **k):
        if self.name == "stubborn.log":
            raise OSError("device busy")
        return real_unlink(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", boom)
    removed = deploy._prune_absent_locals(
        dest, {"stubborn.log": 1, "also_stale.log": 1}, {})
    assert removed == ["also_stale.log"]
    assert "could not remove stale" in capsys.readouterr().out
    assert (dest / "stubborn.log").exists()


def test_prune_tolerates_a_directory_it_cannot_remove(tmp_path, monkeypatch):
    """The empty-directory cleanup is best-effort; a failure there must not
    surface as an error."""
    dest = tmp_path / "i1"
    (dest / "epp_logs").mkdir(parents=True)
    (dest / "epp_logs" / "a.log").write_text("x")
    monkeypatch.setattr(Path, "rmdir",
                        lambda self, *a, **k: (_ for _ in ()).throw(OSError("busy")))
    removed = deploy._prune_absent_locals(dest, {"epp_logs/a.log": 1}, {})
    assert removed == ["epp_logs/a.log"]


def test_prune_does_not_run_when_the_inventory_probe_fails(tmp_path, monkeypatch):
    """A failed probe is not evidence the remote dropped anything. Deleting on
    it would turn a transient error into data loss."""
    dest = tmp_path / "i1"
    dest.mkdir(parents=True)
    precious = dest / "trace_data.csv"
    precious.write_text("precious")
    monkeypatch.setattr(
        deploy, "run",
        lambda *a, **k: _fake_run(returncode=1, stderr="connection refused"))
    res = deploy._copy_iteration_incremental(
        "pod", "ns", "/data/r/p/wl/i1", dest, remote_mtime=100.0)
    assert res.complete is False
    assert precious.read_text() == "precious"


def test_full_copy_forwards_exclude_subdirs(tmp_path, monkeypatch):
    """--skip-logs routes through the same helper with server_logs excluded."""
    tree = {"trace_data.csv": 4, "server_logs/vllm.log": 99}
    fake = _LsPVC(tree)
    monkeypatch.setattr(deploy, "run", fake)
    wl_dest = tmp_path / "baseline" / "wl-a"
    errors = deploy._copy_workload_iterations_full(
        "pod", "run-1", "baseline", "wl-a", "ns-0", wl_dest, {"i1": 100.0},
        exclude_subdirs=frozenset({"server_logs"}))
    assert errors == []
    assert not (wl_dest / "i1" / "server_logs").exists()
    assert deploy._read_collect_marker(wl_dest / "i1")["file_count"] == 1


def test_redact_resources_runs_only_on_iterations_this_call_copied(tmp_path, monkeypatch):
    """--skip-logs redacts resources/ for the iterations it copies. It must not
    reach into an iteration it skipped — that one is another slot's or an
    earlier collect's, already redacted, and rewriting it is not this call's
    business."""
    tree = {"trace_data.csv": 4, "resources/pods.yaml": 6}
    wl_dest = tmp_path / "baseline" / "wl-a"
    # i9 is complete and current — skipped, and must not be redacted again.
    (wl_dest / "i9" / "resources").mkdir(parents=True)
    (wl_dest / "i9" / "resources" / "pods.yaml").write_text("xxxxxx")
    deploy._write_collect_marker(wl_dest / "i9", tree, 100.0)

    redacted: list = []
    monkeypatch.setattr(deploy, "redact_yaml_tree", redacted.append)
    monkeypatch.setattr(deploy, "run", _LsPVC(tree, iterations="i1\ni9\n"))
    deploy._copy_workload_iterations_full(
        "pod", "run-1", "baseline", "wl-a", "ns-0", wl_dest,
        {"i1": 100.0, "i9": 100.0},
        exclude_subdirs=frozenset({"server_logs"}), redact_resources=True)

    assert [p.parent.name for p in redacted] == ["i1"], redacted


def test_full_copy_does_not_redact_when_the_flag_is_off(tmp_path, monkeypatch):
    """The default full-copy path leaves resources/ unredacted — the pre-#885
    behavior, preserved deliberately and tracked as its own defect."""
    tree = {"resources/pods.yaml": 6}
    redacted: list = []
    monkeypatch.setattr(deploy, "redact_yaml_tree", redacted.append)
    monkeypatch.setattr(deploy, "run", _LsPVC(tree))
    deploy._copy_workload_iterations_full(
        "pod", "run-1", "baseline", "wl-a", "ns-0",
        tmp_path / "baseline" / "wl-a", {"i1": 100.0})
    assert redacted == []


def test_full_copy_surfaces_an_ls_error(tmp_path, monkeypatch):
    def mock_run(cmd, **kwargs):
        return _fake_run(returncode=1, stderr="pod not found")
    monkeypatch.setattr(deploy, "run", mock_run)
    errors = deploy._copy_workload_iterations_full(
        "pod", "run-1", "baseline", "wl-a", "ns-0",
        tmp_path / "wl-a", {"i1": 100.0})
    assert len(errors) == 1
    assert "pod not found" in errors[0]
