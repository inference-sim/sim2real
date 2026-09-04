# Collect: incremental per-entry copy with a real completeness marker — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `deploy.py collect` copy result iterations at an inventory-driven granularity so an iteration of arbitrary total size transfers successfully, a retry moves only the delta, and a partially-copied iteration is never reported as complete.

**Architecture:** Replace the single whole-iteration `kubectl cp` (and the ~90 duplicated per-subdir `cp` blocks on the `--skip-logs` path) with one shared helper that (1) builds a remote file inventory of `iN/` via a single `find`+`stat` exec, (2) diffs it against local files by name and size, (3) copies the delta in two passes — per remote top-level entry, then per individual file for whatever still did not land — catching `subprocess.TimeoutExpired` per copy so one oversized file cannot abort the slot, and (4) writes `iN/.collect_complete` only when the local tree matches the remote inventory exactly. `_is_iteration_up_to_date` consults that marker instead of `trace_data.csv`'s mtime.

**Tech Stack:** Python >= 3.10, `subprocess` via `pipeline/lib/proc.py`, `kubectl cp` / `kubectl exec` against an `alpine:3.19` extractor pod (BusyBox coreutils), pytest.

**Spec:** GitHub issue #885 (`gh issue view 885`). Related: #883 (`--skip-logs` does not skip EPP logs), #564 (cross-slot iteration preservation), #694 (the 120 s default timeout).

## Global Constraints

- Python >= 3.10. All subprocess execution goes through `deploy.run` (which delegates to `pipeline/lib/proc.py:run`) — never call `subprocess.run` directly, because `deploy.run` is the test monkeypatch seam.
- Remote shell commands run inside `alpine:3.19` (BusyBox 1.36). `find -exec … +` and `stat -c FMT` are available; GNU-only `find -printf` is not.
- The 120 s default timeout from #694 stays as-is. Do **not** add a `--timeout` flag — the issue rules it out as the primary mechanism.
- Never `rmtree` an iteration directory before copying. That is defect 2; the whole fix depends on already-landed bytes surviving.
- The workload directory is never wiped as a whole (issue #564 invariant) — preserve it.
- `_is_iteration_up_to_date`'s existing `remote_mtime is None → False` semantics stay: a missing probe means we cannot skip.
- Marker filename is exactly `.collect_complete`, at `iN/.collect_complete`. Iteration discovery elsewhere (`pipeline/lib/resolve.py:_ITER_DIR_RE` + `is_dir()`, `deploy.py:_has_iN_subdirs`) filters on directories, so a dotfile at iteration root is inert — verified, do not change those call sites.
- Do **not** fix #883 in this PR. The `--skip-logs` path must keep excluding `server_logs` only.
- Do **not** add `redact_yaml_tree` to the full-copy path. That asymmetry is a separate defect (see Task 6 Step 4) — keep the redaction call on the `--skip-logs` path only so today's behavior is preserved exactly.

---

## File Structure

| File | Responsibility |
|---|---|
| `pipeline/deploy.py` | New inventory + delta-copy helpers; `_is_iteration_up_to_date` marker gate; both copy call sites rewritten to use the shared helper; partial-cell accumulation and end-of-collect summary. |
| `pipeline/tests/test_collect_incremental.py` | **New.** Unit tests for the inventory parser, delta computation, two-pass copy, timeout containment, marker write/skip, and convergence-on-retry. |
| `pipeline/tests/test_collect_internals.py` | Existing `_is_iteration_up_to_date` tests updated for the marker gate. |
| `pipeline/tests/test_deploy_collect_paths.py` | Existing `_copy_workload_iterations_full` / skip-logs tests updated for the new call shape. |
| `pipeline/README.md` | `deploy.py collect` section: incremental semantics, marker, partial-copy report. |
| `CLAUDE.md` | Workspace artifact table: add the `.collect_complete` row. |

---

### Task 1: Remote and local file inventories

**Files:**
- Modify: `pipeline/deploy.py` (insert after `_list_pvc_iterations`, ~line 1037)
- Test: `pipeline/tests/test_collect_incremental.py` (create)

**Interfaces:**
- Consumes: `deploy.run`, `deploy.warn`.
- Produces:
  - `_remote_file_inventory(pod_name: str, namespace: str, remote_dir: str) -> tuple[dict[str, int], str | None]` — maps POSIX relative path (no leading `./`) to size in bytes. Second element is an error string, or `None` on success. An empty dict with `None` error means the directory exists but holds no files.
  - `_local_file_inventory(local_dir: Path) -> dict[str, int]` — same key shape, walking `local_dir`; excludes `COLLECT_MARKER`.
  - `COLLECT_MARKER = ".collect_complete"` (module-level constant).

- [ ] **Step 1: Write the failing tests**

Create `pipeline/tests/test_collect_incremental.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest pipeline/tests/test_collect_incremental.py -v`
Expected: FAIL — `AttributeError: module 'pipeline.deploy' has no attribute '_remote_file_inventory'`.

- [ ] **Step 3: Implement the inventories**

In `pipeline/deploy.py`, add `import hashlib` to the imports (alphabetically, before `import json`), then add the constant and the two functions immediately after `_list_pvc_iterations`:

```python
COLLECT_MARKER = ".collect_complete"
"""Per-iteration completeness marker written by collect (issue #885).

Presence means every file in the remote inventory landed locally at the
matching size. ``trace_data.csv``'s mtime is not evidence of anything
beyond ``trace_data.csv`` — it is copied early in the tar stream, so it
survives a timeout that truncates the rest of the iteration.
"""


def _remote_file_inventory(
    pod_name: str, namespace: str, remote_dir: str,
) -> "tuple[dict[str, int], str | None]":
    """Inventory every file under *remote_dir* on the extractor pod.

    Returns ``({relpath: size_bytes}, error)``. ``error`` is None on success;
    an empty dict with ``error=None`` means the directory exists but is empty.

    One ``kubectl exec`` per iteration. BusyBox (alpine:3.19) has no GNU
    ``find -printf``, so this shells out to ``stat -c``. The ``%s|%n`` format
    puts the size first and the path last, so a path containing the delimiter
    still parses via a single split from the left.
    """
    cmd = ["kubectl", "exec", pod_name, f"-n={namespace}", "--", "sh", "-c",
           f"cd {remote_dir} && find . -type f -exec stat -c '%s|%n' {{}} +"]
    try:
        result = run(cmd, check=False, capture=True)
    except subprocess.TimeoutExpired as exc:
        return {}, f"inventory of {remote_dir} timed out after {exc.timeout}s"
    if result.returncode != 0:
        return {}, f"failed to inventory {remote_dir}: {(result.stderr or '').strip()}"
    inv: dict[str, int] = {}
    for line in (result.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        size_str, sep, rel = line.partition("|")
        if not sep:
            warn(f"inventory: unparseable line: {line!r}")
            continue
        try:
            size = int(size_str)
        except ValueError:
            warn(f"inventory: unparseable line: {line!r}")
            continue
        inv[rel.removeprefix("./")] = size
    return inv, None


def _local_file_inventory(local_dir: Path) -> dict[str, int]:
    """Inventory every file under *local_dir*, excluding ``COLLECT_MARKER``.

    Keys are POSIX-style paths relative to *local_dir* so they compare
    directly against ``_remote_file_inventory`` keys.
    """
    inv: dict[str, int] = {}
    if not local_dir.is_dir():
        return inv
    for path in local_dir.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(local_dir).as_posix()
        if rel == COLLECT_MARKER:
            continue
        try:
            inv[rel] = path.stat().st_size
        except OSError as exc:
            warn(f"stat failed for {path}: {exc} — treating as missing")
    return inv
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest pipeline/tests/test_collect_incremental.py -v`
Expected: PASS (9 tests).

- [ ] **Step 5: Commit**

```bash
git add pipeline/deploy.py pipeline/tests/test_collect_incremental.py
git commit -m "feat(collect): add remote/local file inventories for incremental copy (#885)"
```

---

### Task 2: The marker — write, read, and gate the up-to-date check

**Files:**
- Modify: `pipeline/deploy.py` — new marker helpers after Task 1's functions; `_is_iteration_up_to_date` at ~line 995
- Test: `pipeline/tests/test_collect_incremental.py` (append), `pipeline/tests/test_collect_internals.py` (update)

**Interfaces:**
- Consumes: `COLLECT_MARKER` (Task 1).
- Produces:
  - `_inventory_sha256(inventory: dict[str, int]) -> str`
  - `_write_collect_marker(iN_dir: Path, inventory: dict[str, int], remote_mtime: "float | None") -> None`
  - `_read_collect_marker(iN_dir: Path) -> "dict | None"` — parsed JSON, or None when absent/corrupt.
  - `_is_iteration_up_to_date(iN_dir: Path, remote_mtime: "float | None") -> bool` — **changed semantics**: now requires the marker, and compares the marker's recorded `remote_mtime` against the current one.

- [ ] **Step 1: Write the failing tests**

Append to `pipeline/tests/test_collect_incremental.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest pipeline/tests/test_collect_incremental.py -v -k "marker or up_to_date"`
Expected: FAIL — `_write_collect_marker` undefined; the marker-gate tests fail because the current implementation trusts `trace_data.csv`.

- [ ] **Step 3: Implement the marker helpers**

Add after Task 1's functions in `pipeline/deploy.py`:

```python
def _inventory_sha256(inventory: dict[str, int]) -> str:
    """SHA-256 over canonically sorted ``<size> <relpath>`` lines."""
    h = hashlib.sha256()
    for rel in sorted(inventory):
        h.update(f"{inventory[rel]} {rel}\n".encode())
    return h.hexdigest()


def _write_collect_marker(iN_dir: Path, inventory: dict[str, int],
                          remote_mtime: "float | None") -> None:
    """Record that *iN_dir* holds every file in *inventory* at the right size.

    Written only after the local tree matches the remote inventory exactly —
    this is the sole evidence ``_is_iteration_up_to_date`` accepts (#885).
    ``remote_mtime`` is stored so a later collect can tell a complete-but-stale
    iteration from a complete-and-current one.
    """
    payload = {
        "completed_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "file_count": len(inventory),
        "byte_count": sum(inventory.values()),
        "inventory_sha256": _inventory_sha256(inventory),
        "remote_mtime": remote_mtime,
    }
    (iN_dir / COLLECT_MARKER).write_text(json.dumps(payload, indent=2) + "\n")


def _read_collect_marker(iN_dir: Path) -> "dict | None":
    """Return the parsed marker, or None when it is absent or unreadable."""
    path = iN_dir / COLLECT_MARKER
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None
```

- [ ] **Step 4: Rewrite the up-to-date gate**

Replace `_is_iteration_up_to_date` (currently at ~line 995) wholesale:

```python
def _is_iteration_up_to_date(iN_dir: Path, remote_mtime: "float | None") -> bool:
    """Return True only if *iN_dir* carries a completeness marker that is not
    stale relative to *remote_mtime*.

    Before #885 this trusted ``iN_dir/trace_data.csv``'s mtime. That file is
    copied early in the tar stream, so it survived a timeout that truncated
    the rest of the iteration — and the next collect then printed "up to date
    — skipping" over a partial directory, permanently retaining truncated
    logs. Only ``COLLECT_MARKER`` proves the whole inventory landed.

    ``remote_mtime is None`` still means "cannot skip" (issue #564): the
    iteration is either absent from this slot's PVC or the probe failed.
    A marker whose own ``remote_mtime`` is None was written when the probe
    had failed; it still proves completion, so it is trusted.

    Iterations collected before #885 have no marker and are therefore not
    skipped. That costs one remote inventory each and zero file transfers,
    because the delta computed against a complete local tree is empty.
    """
    if remote_mtime is None:
        return False
    marker = _read_collect_marker(iN_dir)
    if marker is None:
        return False
    recorded = marker.get("remote_mtime")
    if recorded is None:
        return True
    try:
        return float(recorded) >= remote_mtime
    except (TypeError, ValueError):
        return False
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest pipeline/tests/test_collect_incremental.py -v`
Expected: PASS.

- [ ] **Step 6: Update the existing `_is_iteration_up_to_date` tests**

`pipeline/tests/test_collect_internals.py` lines 76-122 assert the old `trace_data.csv` semantics. Replace that block with:

```python
# ── _is_iteration_up_to_date ─────────────────────────────────────────────────
#
# Issue #885 moved the evidence from trace_data.csv's mtime to the
# .collect_complete marker. A fresh trace_data.csv no longer implies the
# iteration is complete — it is copied early in the tar stream and survives
# a truncation.


def test_is_iteration_up_to_date_false_when_remote_mtime_none():
    """No probe means we cannot skip (issue #564)."""
    assert deploy._is_iteration_up_to_date(Path("/nonexistent"), None) is False


def test_is_iteration_up_to_date_false_when_iN_dir_missing(tmp_path):
    assert deploy._is_iteration_up_to_date(tmp_path / "i1", 100.0) is False


def test_is_iteration_up_to_date_false_when_marker_missing(tmp_path):
    iN = tmp_path / "i1"
    iN.mkdir()
    (iN / "trace_data.csv").write_text("data")
    assert deploy._is_iteration_up_to_date(iN, 100.0) is False


def test_is_iteration_up_to_date_true_when_marker_is_current(tmp_path):
    iN = tmp_path / "i1"
    iN.mkdir()
    deploy._write_collect_marker(iN, {"trace_data.csv": 4}, 500.0)
    assert deploy._is_iteration_up_to_date(iN, 500.0) is True


def test_is_iteration_up_to_date_false_when_marker_is_stale(tmp_path):
    iN = tmp_path / "i1"
    iN.mkdir()
    deploy._write_collect_marker(iN, {"trace_data.csv": 4}, 100.0)
    assert deploy._is_iteration_up_to_date(iN, 500.0) is False
```

Delete the superseded `test_is_iteration_up_to_date_false_when_trace_csv_missing`, `..._true_when_local_is_fresh`, `..._false_when_local_is_stale`, and `..._true_when_local_exactly_matches_remote`. Update the module docstring's second paragraph to say the skip mechanism now keys on the marker rather than on `trace_data.csv`'s mtime, and keep its #564 framing intact. Remove the now-unused `os` import only if nothing else in the file uses it.

- [ ] **Step 7: Run the collect test suite**

Run: `python -m pytest pipeline/tests/test_collect_internals.py pipeline/tests/test_collect_incremental.py -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add pipeline/deploy.py pipeline/tests/test_collect_incremental.py pipeline/tests/test_collect_internals.py
git commit -m "fix(collect): gate the up-to-date check on a real completeness marker (#885)"
```

---

### Task 3: Two-pass delta copy with per-copy timeout containment

**Files:**
- Modify: `pipeline/deploy.py` — new helpers after Task 2's functions
- Test: `pipeline/tests/test_collect_incremental.py` (append)

**Interfaces:**
- Consumes: `_remote_file_inventory`, `_local_file_inventory`, `_read_collect_marker`, `_write_collect_marker` (Tasks 1-2).
- Produces:
  - `IterationCopy` — a `dataclass` with fields `label: str = ""`, `complete: bool = False`, `files_total: int = 0`, `bytes_total: int = 0`, `files_present: int = 0`, `bytes_present: int = 0`, `missing: list`, `short: list` (of `(relpath, local_size, remote_size)`), `errors: list`, and a property `trace_ok: bool`.
  - `_cp_one(remote: str, dest: Path, label: str, errors: list) -> None`
  - `_delta(remote_inv: dict[str, int], local_inv: dict[str, int]) -> list[str]`
  - `_copy_iteration_incremental(pod_name: str, namespace: str, remote_iN: str, iN_dest: Path, *, exclude_subdirs: "frozenset[str]" = frozenset(), remote_mtime: "float | None" = None) -> IterationCopy`

Behavior contract for `_copy_iteration_incremental`:

1. Inventory the remote iteration. On inventory error, return an `IterationCopy` with that error and `complete=False` — no copy attempted.
2. Filter out any inventory entry whose first path segment is in `exclude_subdirs`.
3. Compute `delta` = inventory entries whose local counterpart is missing or a different size. Where a **stale marker** is present (a marker exists, yet the caller did not skip the iteration), treat *every* entry as delta — same-size-different-content is otherwise invisible to a size diff.
4. If `delta` is empty: write the marker, return `complete=True`. This is the zero-transfer path that already-collected pre-#885 iterations take.
5. **Pass 1 (bulk)** — only when the local tree is empty, i.e. nothing to preserve. Copy each remote *top-level entry* not excluded: one `kubectl cp` per root-level file and one per root-level directory. This keeps the fast path fast while bounding any single transfer to one subtree instead of the whole iteration. Skipped entirely when local files already exist, so a retry never re-streams what landed.
6. **Pass 2 (per file)** — recompute the delta, then `kubectl cp` each remaining file individually into its own `mkdir -p`'d parent. This is the granularity the issue specifies; its cost is proportional to the delta, not the tree.
7. Recompute the local inventory once more. Populate `missing` / `short` / counts. Write the marker **iff** nothing is missing or short.
8. Every `kubectl cp` is wrapped in `try/except subprocess.TimeoutExpired`; the timeout becomes an entry in `errors` and the loop continues. Nothing propagates out of this function.

- [ ] **Step 1: Write the failing tests**

Append to `pipeline/tests/test_collect_incremental.py`:

```python
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
            lines = "".join(f"{sz}|./{rel}\n" for rel, sz in sorted(self.tree.items()))
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
        rel_root = path.rstrip("/").split("/i1", 1)[1].lstrip("/")
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

    # Second attempt: only the two missing files are fetched, per file.
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest pipeline/tests/test_collect_incremental.py -v -k "incremental or timeout or delta or bulk or stale_marker or short_file or exclude"`
Expected: FAIL — `_copy_iteration_incremental` undefined.

- [ ] **Step 3: Implement the copy engine**

Add `from dataclasses import dataclass, field` to `pipeline/deploy.py`'s imports (after `from pathlib import Path`), then add after Task 2's functions:

```python
@dataclass
class IterationCopy:
    """Outcome of copying one ``i<N>/`` iteration (issue #885).

    ``complete`` is the only thing the marker is written on. The remaining
    fields exist so a partial copy can be reported in terms an operator can
    act on: how much landed, what is missing or short, and whether the
    workload's primary artifact survived.
    """

    label: str = ""
    complete: bool = False
    files_total: int = 0
    bytes_total: int = 0
    files_present: int = 0
    bytes_present: int = 0
    missing: list = field(default_factory=list)
    short: list = field(default_factory=list)     # (relpath, local, remote)
    errors: list = field(default_factory=list)

    @property
    def trace_ok(self) -> bool:
        """True when ``trace_data.csv`` is neither missing nor short."""
        return ("trace_data.csv" not in self.missing
                and all(rel != "trace_data.csv" for rel, _l, _r in self.short))


def _cp_one(remote: str, dest: Path, label: str, errors: list) -> None:
    """``kubectl cp`` one remote file-or-directory to *dest*.

    Contains :class:`subprocess.TimeoutExpired`, which ``check=False`` does
    NOT suppress — ``lib/proc.run`` passes ``timeout=`` straight to
    ``subprocess.run``, which raises regardless of ``check``. Before #885 that
    exception unwound out of the copy loop, past every remaining iteration and
    workload, to the slot-level handler, so one oversized file aborted an
    entire slot's collect.

    A "no such file" stderr is tolerated: the remote inventory is a snapshot,
    and a file can disappear between inventory and copy. The post-copy
    verification catches anything that genuinely failed to land.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        r = run(["kubectl", "cp", remote, str(dest), "--retries=3"],
                check=False, capture=True)
    except subprocess.TimeoutExpired as exc:
        errors.append(f"{label}: timed out after {exc.timeout}s")
        return
    if r.returncode != 0:
        stderr = (r.stderr or "").strip()
        if "no such file" not in stderr.lower():
            errors.append(f"{label}: {stderr}")


def _delta(remote_inv: dict[str, int], local_inv: dict[str, int]) -> list[str]:
    """Relpaths present in *remote_inv* but missing or size-mismatched locally."""
    return sorted(rel for rel, size in remote_inv.items()
                  if local_inv.get(rel) != size)


def _copy_iteration_incremental(
    pod_name: str, namespace: str, remote_iN: str, iN_dest: Path, *,
    exclude_subdirs: "frozenset[str]" = frozenset(),
    remote_mtime: "float | None" = None,
) -> IterationCopy:
    """Copy one ``i<N>/`` iteration to *iN_dest*, transferring only the delta.

    Issue #885: the previous implementation ``rmtree``d *iN_dest* and streamed
    the whole iteration in a single ``kubectl cp`` under ``deploy.run``'s 120 s
    control-plane guard (#694). Measured API-server throughput of ~3.5 MB/s
    capped an iteration at ~420 MB, and because each retry started from zero
    every attempt died at roughly the same place — the operation could not
    converge by repetition.

    This version never wipes the destination. It inventories the remote
    iteration once, diffs by name and size, and copies only what is missing or
    short: per remote top-level entry when nothing has landed yet (the fast
    path), then per individual file for anything still outstanding. A retry
    therefore costs only the delta. ``kubectl cp`` cannot resume mid-file, so a
    truncated file is re-copied whole.

    *exclude_subdirs* drops entries whose first path segment matches — used by
    ``--skip-logs`` to leave ``server_logs`` on the PVC.

    Returns an :class:`IterationCopy`. Never raises: an inventory failure or a
    per-copy timeout is recorded in ``errors`` so the caller can keep going.
    """
    res = IterationCopy(label=iN_dest.name)
    remote_root = remote_iN.rstrip("/")

    remote_inv, inv_err = _remote_file_inventory(pod_name, namespace, remote_root)
    if inv_err is not None:
        res.errors.append(inv_err)
        return res
    if exclude_subdirs:
        remote_inv = {rel: size for rel, size in remote_inv.items()
                      if rel.split("/", 1)[0] not in exclude_subdirs}
    res.files_total = len(remote_inv)
    res.bytes_total = sum(remote_inv.values())

    iN_dest.mkdir(parents=True, exist_ok=True)
    local_inv = _local_file_inventory(iN_dest)

    # A marker that exists even though the caller chose not to skip means the
    # remote moved on. Sizes can match while contents differ, so re-fetch
    # everything rather than trusting the size diff.
    stale_marker = _read_collect_marker(iN_dest) is not None
    delta = sorted(remote_inv) if stale_marker else _delta(remote_inv, local_inv)

    if delta:
        # Pass 1 (bulk): only when there is nothing local to preserve. One cp
        # per remote top-level entry bounds a single transfer to one subtree
        # instead of the whole iteration, while keeping the common case to a
        # handful of calls. Skipped once anything has landed, so a retry never
        # re-streams what it already has.
        if not local_inv:
            roots: list[str] = []
            for rel in delta:
                head = rel.split("/", 1)[0]
                if head not in roots:
                    roots.append(head)
            for head in roots:
                is_dir = any(rel.startswith(f"{head}/") for rel in delta)
                _cp_one(f"{namespace}/{pod_name}:{remote_root}/{head}"
                        + ("/" if is_dir else ""),
                        iN_dest / head, f"{res.label}/{head}", res.errors)
            delta = _delta(remote_inv, _local_file_inventory(iN_dest))

        # Pass 2 (per file): the granularity #885 specifies. Cost is
        # proportional to the delta, not to the tree.
        for rel in delta:
            _cp_one(f"{namespace}/{pod_name}:{remote_root}/{rel}",
                    iN_dest / rel, f"{res.label}/{rel}", res.errors)

    final_local = _local_file_inventory(iN_dest)
    for rel, size in sorted(remote_inv.items()):
        have = final_local.get(rel)
        if have is None:
            res.missing.append(rel)
        elif have != size:
            res.short.append((rel, have, size))
        else:
            res.files_present += 1
            res.bytes_present += size

    res.complete = not res.missing and not res.short
    if res.complete:
        _write_collect_marker(iN_dest, remote_inv, remote_mtime)
    return res
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest pipeline/tests/test_collect_incremental.py -v`
Expected: PASS. If a test's `cp` targets differ from expectation, print the fake's `calls` list and reconcile — the production contract above is authoritative; adjust the fake's path modelling, not the contract.

- [ ] **Step 5: Commit**

```bash
git add pipeline/deploy.py pipeline/tests/test_collect_incremental.py
git commit -m "feat(collect): two-pass delta copy with per-copy timeout containment (#885)"
```

---

### Task 4: Operator-facing partial-copy report and end-of-collect summary

Implemented before Task 5 because `_copy_workload_iterations_full` calls `_report_partial`.

**Files:**
- Modify: `pipeline/deploy.py` — `_human_bytes`, `_report_partial`, `_print_partial_summary` (new, after `IterationCopy`)
- Test: `pipeline/tests/test_collect_incremental.py` (append)

**Interfaces:**
- Consumes: `IterationCopy` (Task 3), `deploy.warn`.
- Produces:
  - `_human_bytes(n: int) -> str` — `"433.1 MB"`-style, base-1024.
  - `_report_partial(res: IterationCopy, run_name: str, phase: str, wl_name: str) -> None` — multi-line WARN.
  - `_print_partial_summary(partials: list, run_name: str) -> None` — end-of-collect roll-up; prints nothing for an empty list.

- [ ] **Step 1: Write the failing tests**

Append to `pipeline/tests/test_collect_incremental.py`:

```python
# ── operator-facing reporting ────────────────────────────────────────────────


def test_human_bytes_formats_at_each_scale():
    assert deploy._human_bytes(512) == "512 B"
    assert deploy._human_bytes(41497088) == "39.6 MB"
    assert deploy._human_bytes(454033408).endswith(" MB")
    assert deploy._human_bytes(5 * 1024 ** 3).endswith(" GB")


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest pipeline/tests/test_collect_incremental.py -v -k "human_bytes or report_partial or summary"`
Expected: FAIL — `_human_bytes` / `_report_partial` / `_print_partial_summary` undefined.

- [ ] **Step 3: Implement the reporting**

Add after `IterationCopy` in `pipeline/deploy.py`:

```python
def _human_bytes(n: int) -> str:
    """Format *n* bytes for an operator: ``512 B``, ``39.6 MB``, ``5.0 GB``."""
    if n < 1024:
        return f"{n} B"
    val = float(n)
    for unit in ("KB", "MB", "GB", "TB"):
        val /= 1024.0
        if val < 1024.0 or unit == "TB":
            return f"{val:.1f} {unit}"
    return f"{n} B"


def _fmt_list(items: list, limit: int = 5) -> str:
    """Join the first *limit* items, noting how many were elided."""
    shown = ", ".join(str(i) for i in items[:limit])
    extra = len(items) - limit
    return shown + (f" (+{extra} more)" if extra > 0 else "")


def _report_partial(res: IterationCopy, run_name: str, phase: str,
                    wl_name: str) -> None:
    """Warn about an incomplete iteration in terms the operator can act on.

    Issue #885 defect 4: the old message was the raw ``TimeoutExpired`` argv.
    It did not say that data had partially landed, which files were short,
    that ``trace_data.csv`` had survived, or that re-running would resume
    rather than no-op — and all of that is known here. The three things the
    operator needs are: this is partial and not a total loss; the primary
    artifact is or is not safe; and re-running resumes.
    """
    warn(f"{res.label} — PARTIAL COPY")
    print(f"       transferred {res.files_present} of {res.files_total} files "
          f"({_human_bytes(res.bytes_present)} of "
          f"{_human_bytes(res.bytes_total)})")
    if res.short:
        detail = _fmt_list([f"{rel} (truncated at {local:,} B of {remote:,} B)"
                            for rel, local, remote in res.short])
        print(f"       incomplete: {detail}")
    if res.missing:
        print(f"       missing:    {_fmt_list(res.missing)}")
    for err in res.errors:
        print(f"       cause:      {err}")
    if res.trace_ok:
        print("       trace_data.csv is present and complete — "
              "the workload's trace is safe")
    else:
        print("       trace_data.csv is MISSING OR TRUNCATED — "
              "this workload's trace is not usable")
    print("       ACTION: re-run to fetch only the missing files:")
    print(f"               python pipeline/deploy.py collect --run {run_name} "
          f"--package {phase} --workload {wl_name}")
    print("       This iteration is NOT marked complete, so the re-run will "
          "resume it rather than skip it.")


def _print_partial_summary(partials: list, run_name: str) -> None:
    """Roll up every partially-copied iteration at the end of a collect.

    A failure in cell 3 of 9 is otherwise lost in the scrollback (#885).
    """
    if not partials:
        return
    print(f"\n  Partial:   {len(partials)} iteration(s) copied incompletely "
          f"and NOT marked complete:")
    for res in partials:
        print(f"    - {res.label}  "
              f"({res.files_present}/{res.files_total} files, "
              f"{_human_bytes(res.bytes_present)} of "
              f"{_human_bytes(res.bytes_total)})")
    print(f"  Re-run `python pipeline/deploy.py collect --run {run_name}` "
          f"to transfer only the missing files.")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest pipeline/tests/test_collect_incremental.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pipeline/deploy.py pipeline/tests/test_collect_incremental.py
git commit -m "feat(collect): report partial copies with actionable detail (#885)"
```

---

### Task 5: Route both copy paths through the shared helper

**Files:**
- Modify: `pipeline/deploy.py` — `_copy_workload_iterations_full` (~line 1039); `_extract_phases_from_pvc` signature, its `skip_logs` branch (~lines 1183-1312), and its three call sites in `_cmd_collect`; `_cmd_collect`'s summary block
- Test: `pipeline/tests/test_deploy_collect_paths.py` (update), `pipeline/tests/test_collect_incremental.py` (append)

**Interfaces:**
- Consumes: `_copy_iteration_incremental`, `IterationCopy`, `_is_iteration_up_to_date`, `_report_partial`, `_print_partial_summary`.
- Produces:
  - `_copy_workload_iterations_full(pod_name, run_name, phase, wl_name, namespace, wl_dest, wl_remote_mtimes, partials: "list | None" = None, exclude_subdirs: "frozenset[str]" = frozenset()) -> list[str]` — two new trailing params. `partials`, when given, receives one `IterationCopy` per incomplete iteration, its `label` set to `"<phase>/<workload>/<iN>"`.
  - `_extract_phases_from_pvc(..., partials: "list | None" = None)` — forwarded to every copy call.

- [ ] **Step 1: Write the failing tests**

Append to `pipeline/tests/test_collect_incremental.py`:

```python
# ── _copy_workload_iterations_full integration ───────────────────────────────


class _LsPVC(_FakePVC):
    """_FakePVC that also answers the ``ls`` iteration-discovery exec."""

    def __init__(self, tree, iterations="i1\n", **kw):
        super().__init__(tree, **kw)
        self.iterations = iterations

    def __call__(self, cmd, **kwargs):
        cmd_str = " ".join(cmd)
        if "exec" in cmd_str and " ls " in f" {cmd_str} " and "find" not in cmd_str:
            self.calls.append(cmd_str)
            return _fake_run(stdout=self.iterations)
        return super().__call__(cmd, **kwargs)


def test_full_copy_records_partials_and_keeps_going_after_a_timeout(tmp_path, monkeypatch):
    """A timeout on i1 must not stop i2. Before #885 the exception unwound
    out of the loop and the rest of the slot was never attempted."""
    tree = {"trace_data.csv": 4, "epp_logs/big.log": 64}

    class _Multi(_LsPVC):
        def __call__(self, cmd, **kwargs):
            cmd_str = " ".join(cmd)
            if (cmd[:2] == ["kubectl", "cp"]
                    and "/i1/" in cmd_str and "epp_logs" in cmd_str):
                self.calls.append(cmd_str)
                raise subprocess.TimeoutExpired(cmd=cmd, timeout=120)
            return super().__call__(cmd, **kwargs)

    # The fake keys its materialization off "/i1", so run i1 and i2 through
    # separate destinations by driving one iteration at a time is unnecessary:
    # _materialize splits on "/i1" which is present in both paths' prefixes.
    monkeypatch.setattr(deploy, "run", _Multi(tree, iterations="i1\n"))
    wl_dest = tmp_path / "baseline" / "wl-a"
    partials: list = []
    errors = deploy._copy_workload_iterations_full(
        "pod", "run-1", "baseline", "wl-a", "ns-0", wl_dest,
        {"i1": 100.0}, partials=partials)

    assert errors, "the i1 timeout must be reported"
    assert deploy._read_collect_marker(wl_dest / "i1") is None
    assert [p.label for p in partials] == ["baseline/wl-a/i1"]
    assert (wl_dest / "i1" / "trace_data.csv").exists(), \
        "what landed before the timeout must survive"


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


def test_full_copy_never_wipes_the_destination(tmp_path, monkeypatch):
    """Defect 2: the old code rmtree'd iN_dest before every attempt."""
    tree = {"trace_data.csv": 4, "epp_logs/a.log": 8}
    wl_dest = tmp_path / "baseline" / "wl-a"
    (wl_dest / "i1").mkdir(parents=True)
    (wl_dest / "i1" / "trace_data.csv").write_bytes(b"\0" * 4)
    survivor = wl_dest / "i1" / "operator_note.txt"
    survivor.write_text("keep me")
    monkeypatch.setattr(deploy, "run", _LsPVC(tree))
    deploy._copy_workload_iterations_full(
        "pod", "run-1", "baseline", "wl-a", "ns-0", wl_dest, {"i1": 100.0})
    assert survivor.exists(), "pre-existing local content must survive"


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest pipeline/tests/test_collect_incremental.py -v -k full_copy`
Expected: FAIL — `_copy_workload_iterations_full` has no `partials` / `exclude_subdirs` parameter; the wipe test fails because `shutil.rmtree` still runs.

- [ ] **Step 3: Rewrite `_copy_workload_iterations_full`**

Replace the whole function (currently lines ~1039-1076) with:

```python
def _copy_workload_iterations_full(
    pod_name: str, run_name: str, phase: str, wl_name: str, namespace: str,
    wl_dest: Path, wl_remote_mtimes: dict[str, float],
    partials: "list | None" = None,
    exclude_subdirs: "frozenset[str]" = frozenset(),
) -> list[str]:
    """Enumerate ``i<N>/`` on the current slot's PVC and copy each iteration
    incrementally to *wl_dest*, respecting per-iteration up-to-date skips.

    The workload directory itself is NEVER wiped — nor, since #885, is any
    ``i<N>/`` dir. Wiping destroyed the bytes that had already landed, which
    is why retrying a timed-out copy could not converge. Copies now transfer
    only the inventory delta (see ``_copy_iteration_incremental``).

    Preserves the issue #564 guarantee: iterations copied from other slots are
    not present in this slot's ``i<N>`` listing and are left untouched.

    *partials* — when a list is passed, one :class:`IterationCopy` per
    incomplete iteration is appended so the caller can print an end-of-collect
    summary. *exclude_subdirs* is forwarded to the copy helper; ``--skip-logs``
    passes ``{"server_logs"}``.

    Returns a list of error strings; empty on success.
    """
    wl_dest.mkdir(parents=True, exist_ok=True)
    iN_names, ls_error = _list_pvc_iterations(
        pod_name, run_name, phase, wl_name, namespace)
    if ls_error is not None:
        return [ls_error]
    wl_errors: list[str] = []
    for iN in iN_names:
        iN_dest = wl_dest / iN
        remote_mtime = wl_remote_mtimes.get(iN)
        if _is_iteration_up_to_date(iN_dest, remote_mtime):
            info(f"[{phase}/{wl_name}/{iN}] up to date — skipping")
            continue
        res = _copy_iteration_incremental(
            pod_name, namespace,
            f"/data/{run_name}/{phase}/{wl_name}/{iN}", iN_dest,
            exclude_subdirs=exclude_subdirs, remote_mtime=remote_mtime)
        res.label = f"{phase}/{wl_name}/{iN}"
        if res.complete:
            continue
        _report_partial(res, run_name, phase, wl_name)
        if partials is not None:
            partials.append(res)
        wl_errors.extend(f"{wl_name}/{iN}: {e}" for e in res.errors)
        if not res.errors:
            wl_errors.append(
                f"{wl_name}/{iN}: incomplete copy — "
                f"{len(res.missing)} missing, {len(res.short)} short")
    return wl_errors
```

Remove the now-unused `shutil` import from `pipeline/deploy.py` only if no other call site uses it — check with `grep -n "shutil\." pipeline/deploy.py` first and leave the import if any remain.

- [ ] **Step 4: Replace the `skip_logs` branch**

In `_extract_phases_from_pvc`, the `if skip_logs:` branch runs from the `wl_names` discovery through the `resources` copy and `redact_yaml_tree` (~lines 1183-1312). Leave the `wl_names` discovery block untouched; replace everything from `phase_errors = []` down to (but not including) `if phase_errors:` with:

```python
                phase_errors = []
                for wl_name in wl_names:
                    wl_remote_mtimes = remote_mtimes.get(wl_name, {})
                    wl_dest = dest_dir / wl_name
                    # --skip-logs leaves vLLM server_logs on the PVC; they
                    # dominate data volume. Everything else is collected.
                    # (Issue #883 tracks the stale docstring claim that EPP
                    # logs are skipped too — behavior here is unchanged.)
                    wl_errors = _copy_workload_iterations_full(
                        pod_name, run_name, phase, wl_name, namespace,
                        wl_dest, wl_remote_mtimes, partials=partials,
                        exclude_subdirs=frozenset({"server_logs"}))
                    for iN_dir in sorted(wl_dest.glob("i*")):
                        res_dir = iN_dir / "resources"
                        if res_dir.is_dir():
                            redact_yaml_tree(res_dir)
                    if wl_errors:
                        phase_errors.extend(wl_errors)
                    if on_workload_done:
                        wl_exc = RuntimeError("; ".join(wl_errors)) if wl_errors else None
                        on_workload_done(phase, wl_name, namespace, wl_exc)
```

Delete the now-dead per-file trace loop, the four per-directory `cp` blocks, and the stale-subdir `rmtree` loop.

- [ ] **Step 5: Thread `partials` through the plumbing**

Add `partials: "list | None" = None` to `_extract_phases_from_pvc`'s signature after `on_workload_done`, document it in the docstring:

```
    When *partials* is a list, one ``IterationCopy`` record per incomplete
    iteration is appended to it so the caller can print an end-of-collect
    summary of every cell left partial (issue #885).
```

Pass `partials=partials` at the two remaining `_copy_workload_iterations_full` call sites inside `_extract_phases_from_pvc` (the `elif workload:` branch at ~line 1318 and the unscoped loop at ~line 1354).

In `_cmd_collect`, create the accumulator once beside the existing `collected` / `failed` locals:

```python
    partials: list = []   # IterationCopy per incompletely-copied iteration (#885)
```

Pass `partials=partials` at all three `_extract_phases_from_pvc(...)` call sites in `_cmd_collect` (~lines 1666, 1696, 1873). Appending from the slot threads is safe — `list.append` is atomic under the GIL.

Finally, call the summary inside the existing `# Print summary` block, after the `Failed:` line and before the `Results:` line:

```python
    _print_partial_summary(partials, run_name)
```

- [ ] **Step 6: Update the existing path tests**

In `pipeline/tests/test_deploy_collect_paths.py`:

- `TestCopyWorkloadIterationsFull.test_returns_errors_when_kubectl_cp_fails` — the mock must now answer the inventory exec. Add a branch before the generic `cp` branch:

```python
            # inventory probe (find + stat) — issue #885
            if "exec" in cmd_str and "find" in cmd_str and "stat" in cmd_str:
                return _fake_run(stdout="4|./trace_data.csv\n")
```

  Keep the `cp` failure injection; the assertion that an error string surfaces still holds. Update the test docstring to say the failure is now surfaced per copy rather than per iteration.

- `TestExtractPhasesSkipLogsScoped.test_skip_logs_scoped_workload_copies_trace_files` — the skip-logs path no longer issues a fixed list of per-file `cp`s; it inventories then copies. Add the same inventory branch to that mock and relax the assertion from "these exact filenames were cp'd" to "the inventoried files were cp'd", e.g. `assert any("trace_data.csv" in s for s in cp_sources)`.

Read each test before editing and preserve its intent; change only what the new call shape requires.

- [ ] **Step 7: Run the full pipeline suite**

Run: `python -m pytest pipeline/ -q`
Expected: PASS. Investigate every failure — a test asserting the old whole-iteration `cp` or the `rmtree` is asserting a defect and should be updated with a comment naming #885; tests asserting cross-slot preservation (#564) or per-iteration mtime keying must keep passing.

- [ ] **Step 8: Commit**

```bash
git add pipeline/deploy.py pipeline/tests/
git commit -m "refactor(collect): route both copy paths through the incremental helper (#885)"
```

---

### Task 6: Docs, stale-reference sweep, and the follow-up issue

**Files:**
- Modify: `pipeline/README.md` (~line 703, the `deploy.py collect` paragraph; ~line 713, the results-layout list)
- Modify: `CLAUDE.md` (workspace artifact table, after the `epp_stream_done` row)

- [ ] **Step 1: Update `pipeline/README.md`**

In the `**`deploy.py collect`**` paragraph (~line 703), replace the sentence "Repeated collects are incremental at iteration granularity: each `i<N>/trace_data.csv` on the current slot's PVC is compared to its local copy and skipped if the local mtime is at least as new." and the following cross-slot sentence with:

```markdown
Repeated collects are incremental at **file** granularity (issue #885). For each `i<N>/` the collector inventories the remote iteration once (`find` + `stat` in the extractor pod), diffs it against local files by name and size, and copies only what is missing or short — per remote top-level entry when nothing has landed yet, then per individual file for anything still outstanding. The destination is never wiped, so a copy interrupted by the 120 s subprocess guard (#694) leaves its bytes in place and the next collect transfers only the delta. `kubectl cp` cannot resume mid-file, so a truncated file is re-copied whole.

An iteration is skipped only when it carries `i<N>/.collect_complete` and that marker is not stale relative to the remote `trace_data.csv` mtime. The marker is written **only** after every file in the remote inventory is present locally at the matching size, so a partially-copied iteration has no marker and is resumed rather than skipped. Before #885 completeness was inferred from `trace_data.csv`'s mtime alone — that file is copied early in the tar stream, so it survived a truncation and the next collect reported the partial iteration as up to date. Iterations collected before #885 have no marker and are re-examined once; the delta against a complete local tree is empty, so that costs one remote inventory and zero transfers.

An incomplete iteration prints a `PARTIAL COPY` warning naming the file and byte counts transferred, the truncated and missing files, whether `trace_data.csv` survived, and the scoped `collect` command that resumes it. A multi-cell collect ends with a `Partial:` summary listing every iteration left incomplete, so a failure in cell 3 of 9 is not lost in the scrollback.

Iterations that live on other slots' PVCs (e.g. when replicas of one `(phase, workload)` pair dispatch across cluster slots) are left untouched on local disk — neither the workload directory nor any `i<N>/` is wiped as a whole (issue #564). If the mtime probe fails (e.g., pod not running), no iteration is skipped and every one is re-examined against its remote inventory — this is the expected degradation path.
```

Add to the results-layout list (~line 713), after the `epp_stream_done` bullet:

```markdown
- `.collect_complete` — per-iteration completeness marker written by `collect` (JSON: `completed_at`, `file_count`, `byte_count`, `inventory_sha256`, `remote_mtime`). Its presence is the only evidence `collect` accepts that an iteration transferred in full (issue #885). Delete it to force a re-collect of that iteration.
```

- [ ] **Step 2: Update `CLAUDE.md`'s artifact table**

Add after the `epp_stream_done` row:

```markdown
| `runs/<run>/results/{phase}/<workload>/i<N>/.collect_complete` | `deploy.py collect` | `deploy.py collect` (up-to-date gate) — per-iteration completeness marker, written only when every file in the remote inventory landed at the matching size (issue #885). Absent ⇒ the iteration is resumed, never skipped |
```

- [ ] **Step 3: Sweep for stale references**

Run and triage every hit:

```bash
grep -rn "trace_data.csv" --include=*.md . | grep -v "\.claude/worktrees"
grep -rn "up to date — skipping" --include=*.md --include=*.py . | grep -v "\.claude/worktrees"
grep -rn "iteration granularity\|_is_iteration_up_to_date" --include=*.md . | grep -v "\.claude/worktrees"
grep -rn "collect_complete" .claude/skills/ docs/ 2>/dev/null
```

For each hit decide stale / accurate / unrelated. In particular check `.claude/skills/sim2real-check/SKILL.md` and `.claude/skills/sim2real-analyze/SKILL.md` for (a) any assumption that an iteration's contents are complete when `trace_data.csv` is present, and (b) any iteration-file enumeration that would now pick up `.collect_complete`. Record in the PR body what was swept and what was updated.

- [ ] **Step 4: File the follow-up issue for the redaction asymmetry**

Found while implementing, deliberately out of scope: `redact_yaml_tree` runs on `i<N>/resources/` only in the `--skip-logs` branch. The default full-copy path pulls `resources/` unredacted.

```bash
unset GITHUB_TOKEN GH_TOKEN
gh issue create --label bug \
  --title "collect: resources/ is redacted only on the --skip-logs path, not on the default full copy" \
  --body "\`redact_yaml_tree\` runs on \`i<N>/resources/\` only in the \`--skip-logs\` branch of \`_extract_phases_from_pvc\` (pipeline/deploy.py). The default full-copy path pulls \`resources/\` without redacting, so the redactor that is supposed to stub sensitive fields before they reach \`results/\` does not run on the path operators normally use.

Found while implementing #885; not changed there to keep that PR scoped to the copy mechanism.

Acceptance: \`resources/\` is redacted on every collect path, with a test that fails if either path skips it."
```

- [ ] **Step 5: Full verification**

```bash
ruff check pipeline/ .claude/skills/ --select F
python -m pytest pipeline/ \
  .claude/skills/sim2real-analyze/tests/ \
  .claude/skills/sim2real-bootstrap/tests/ \
  .claude/skills/sim2real-translate/tests/ \
  .claude/skills/sim2real-check/tests/ \
  --cov=pipeline --cov-report=term-missing --cov-fail-under=90 -q
```

Expected: lint clean, all tests pass, coverage >= 90%.

- [ ] **Step 6: Commit**

```bash
git add pipeline/README.md CLAUDE.md
git commit -m "docs(collect): document file-granular incremental copy and the completeness marker (#885)"
```

---

## Acceptance Criteria Trace

| Issue #885 criterion | Task | Test |
|---|---|---|
| An iteration of arbitrary total size copies successfully, provided no single file exceeds the per-file budget | 3, 5 | `test_incremental_copy_transfers_everything_and_writes_marker`, `test_bulk_pass_is_skipped_when_local_content_exists` |
| A partial copy followed by a `collect` re-run converges: only missing or short files are transferred | 3 | `test_retry_transfers_only_the_delta_then_marks_complete`, `test_incremental_copy_recopies_a_short_file` |
| A partially-copied iteration is never reported complete, and never skipped by the up-to-date check | 2, 3 | `test_incremental_copy_no_marker_when_a_file_times_out`, `test_not_up_to_date_when_marker_absent_even_if_trace_csv_is_fresh` |
| The completeness marker is what the up-to-date check consults; `trace_data.csv` mtime alone is not sufficient | 2 | `test_is_iteration_up_to_date_false_when_marker_missing` |
| On partial copy the operator is told: partial, byte/file counts, incomplete/missing files, trace status, resume command | 4 | `test_report_partial_names_every_thing_the_operator_needs`, `test_report_partial_flags_a_lost_trace` |
| A multi-cell collect ends with a summary listing all partial cells | 4, 5 | `test_partial_summary_lists_every_partial_cell` |
| Regression test: simulate a timeout mid-iteration, assert the marker is absent, assert the next collect transfers only the delta and then writes the marker | 3 | `test_incremental_copy_no_marker_when_a_file_times_out` + `test_retry_transfers_only_the_delta_then_marks_complete` |
| (Vet finding) A timeout must not abort the rest of the slot | 3, 5 | `test_a_timeout_never_propagates_out_of_the_helper`, `test_full_copy_records_partials_and_keeps_going_after_a_timeout` |
| (Vet finding) The `--skip-logs` path gets the same fix | 3, 5 | `test_exclude_subdirs_drops_those_entries_from_the_contract`, `test_full_copy_forwards_exclude_subdirs`, updated `TestExtractPhasesSkipLogsScoped` |
| (Vet finding) Pre-#885 runs migrate without re-transferring | 2, 3 | `test_zero_transfer_when_local_already_matches_remote` |
