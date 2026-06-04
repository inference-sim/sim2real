# Issue #140: Handle corrupt progress data gracefully in deploy.py Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop deploy.py subcommands from crashing with a raw traceback when the progress ConfigMap contains invalid JSON. Print a clear error and exit non-zero per issue #140.

**Architecture:** Add a single `_load_progress(store, *, allow_unreachable: bool = False)` helper in `pipeline/deploy.py`. It catches `ValueError` (the corrupt-data signal raised by `ConfigMapProgressStore.load`) and exits with a user-actionable error message; `RuntimeError` (kubectl-unreachable) propagates by default but is caught and warned-only when `allow_unreachable=True`. Replace all six `store.load()` call sites with the helper. This consolidates the error policy in one place per the user's "minimize duplication" preference and gives consistent UX for the corrupt-progress case across `_cmd_run`, `_cmd_reset`, `_cmd_wipe`, `_cmd_status`, `_cmd_collect`, and `_cmd_run_remote`.

**Tech Stack:** Python (deploy.py, pytest).

**Closes:** #140

---

## Inventory of `store.load()` Call Sites

| File:Line | Subcommand | Current handling | Target handling |
|-----------|-----------|------------------|-----------------|
| `deploy.py:418` | `_cmd_status` | `except (ValueError, RuntimeError)` → warn + `progress = {}` | `_load_progress(store, allow_unreachable=True)` (ValueError now exits with clear message; RuntimeError still falls through) |
| `deploy.py:1049` | `_cmd_collect` | `except (ValueError, RuntimeError)` → warn + `progress = None` | `_load_progress(store, allow_unreachable=True) or None` |
| `deploy.py:2009` | `_cmd_run` | bare — raw traceback on corrupt | `_load_progress(store)` |
| `deploy.py:2346` | `_cmd_reset` | bare — raw traceback on corrupt | `_load_progress(store)` |
| `deploy.py:2401` | `_cmd_wipe` | bare — raw traceback on corrupt | `_load_progress(store)` |
| `deploy.py:2704` | `_cmd_run_remote` | `except (RuntimeError, OSError)` → warn + `progress = None` (misses ValueError) | `_load_progress(store, allow_unreachable=True)` |

**Behavior change for `_cmd_status` and `_cmd_collect`:** Today, corrupt data is warn+continue (status shows "0 pairs", collect runs without progress). Per issue: corrupt data must exit non-zero. The helper makes both subcommands exit on corruption — a small but intentional UX shift, justified because "0 pairs" after corruption is misleading and silently hides data loss.

**Behavior change for `_cmd_run_remote`:** Currently does not catch `ValueError`, so corruption produces a raw traceback during pre-flight filter validation. Helper unifies this with the rest.

The historic `(RuntimeError, OSError)` catch in `_cmd_run_remote` collapses to just `RuntimeError` because `ConfigMapProgressStore.load` already converts `OSError` → `RuntimeError` (`progress.py:52-53`). The helper correctly handles only `RuntimeError`.

---

## File Structure

- Modify: `pipeline/deploy.py` — add `_load_progress` helper (near other module-level helpers around line 100), replace 6 call sites.
- Modify: `pipeline/tests/test_deploy_run.py` (or new `pipeline/tests/test_deploy_progress.py`) — unit tests for the helper.

I will add the new tests to `pipeline/tests/test_deploy_run.py` (existing test file for `deploy.py` orchestration concerns), keeping them under a new `TestLoadProgressHelper` class.

---

## Task 1: Add `_load_progress` helper with unit tests

**Files:**
- Modify: `pipeline/deploy.py` (add helper)
- Test: `pipeline/tests/test_deploy_run.py` (new test class)

**Acceptance criteria:**
- Helper returns `store.load()` result on success.
- Helper exits non-zero with clear error message on `ValueError`.
- Helper re-raises `RuntimeError` by default.
- Helper warns and returns `{}` on `RuntimeError` when `allow_unreachable=True`.

- [ ] **Step 1: Write the failing tests**

Append to `pipeline/tests/test_deploy_run.py`:

```python
class TestLoadProgressHelper:
    """Unit tests for _load_progress helper (issue #140)."""

    def _fake_store(self, behavior):
        """Return a stub store whose load() executes ``behavior`` (a callable)."""
        class _Store:
            configmap_name = "sim2real-progress-fake"
            def load(self_inner):
                return behavior()
        return _Store()

    def test_returns_load_result_on_success(self):
        from pipeline.deploy import _load_progress
        store = self._fake_store(lambda: {"a": 1})
        assert _load_progress(store) == {"a": 1}

    def test_exits_with_message_on_value_error(self, capsys):
        from pipeline.deploy import _load_progress
        def boom():
            raise ValueError("Corrupt ConfigMap sim2real-progress-fake in ns-x")
        store = self._fake_store(boom)
        with pytest.raises(SystemExit) as exc_info:
            _load_progress(store)
        assert exc_info.value.code != 0
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "Corrupt" in combined
        assert "sim2real-progress-fake" in combined
        # Suggests recovery path
        assert "prepare" in combined.lower() or "manually" in combined.lower()

    def test_propagates_runtime_error_by_default(self):
        from pipeline.deploy import _load_progress
        def boom():
            raise RuntimeError("kubectl unreachable")
        store = self._fake_store(boom)
        with pytest.raises(RuntimeError, match="kubectl unreachable"):
            _load_progress(store)

    def test_swallows_runtime_error_when_allow_unreachable(self, capsys):
        from pipeline.deploy import _load_progress
        def boom():
            raise RuntimeError("kubectl unreachable")
        store = self._fake_store(boom)
        result = _load_progress(store, allow_unreachable=True)
        assert result == {}
        combined = capsys.readouterr().out + capsys.readouterr().err
        # No additional output expected on second readouterr; behavior just shouldn't raise.

    def test_value_error_exits_even_when_allow_unreachable(self):
        from pipeline.deploy import _load_progress
        def boom():
            raise ValueError("Corrupt")
        store = self._fake_store(boom)
        with pytest.raises(SystemExit):
            _load_progress(store, allow_unreachable=True)
```

If the file does not already import `pytest`, add `import pytest` near the top (it almost certainly does — verify and skip if so).

- [ ] **Step 2: Verify tests fail**

Run: `python -m pytest pipeline/tests/test_deploy_run.py::TestLoadProgressHelper -v`
Expected: 5 ImportErrors for `_load_progress`.

- [ ] **Step 3: Add the helper**

In `pipeline/deploy.py`, after `_load_setup_config` and before the `# ── Image build ───` section comment (around line 113-115), insert:

```python
def _load_progress(store, *, allow_unreachable: bool = False) -> dict:
    """Load progress data with consistent corrupt/unreachable handling.

    Single entry point for ``store.load()`` across deploy.py subcommands so
    every command surfaces corrupt-data errors with the same UX (issue #140).

    Args:
        store: Any ProgressStore implementation.
        allow_unreachable: When True, transient ``RuntimeError`` (e.g. kubectl
            cannot reach the cluster) is logged as a warning and an empty dict
            is returned. When False (default), the RuntimeError propagates.

    Behavior:
        - Returns ``store.load()`` on success.
        - On ``ValueError`` (the corrupt-data signal from
          ``ConfigMapProgressStore.load``): prints a clear error pointing at
          the affected ConfigMap with recovery guidance, then ``sys.exit(1)``.
          This applies regardless of ``allow_unreachable`` — corrupt data is
          never recoverable by retrying.
    """
    try:
        return store.load()
    except ValueError as exc:
        err(f"Corrupt progress data: {exc}")
        err("Re-run prepare.py, or fix the ConfigMap manually with "
            "`kubectl edit configmap <name> -n <namespace>`.")
        sys.exit(1)
    except RuntimeError as exc:
        if allow_unreachable:
            warn(f"Failed to load progress: {exc}")
            return {}
        raise
```

- [ ] **Step 4: Verify helper tests pass**

Run: `python -m pytest pipeline/tests/test_deploy_run.py::TestLoadProgressHelper -v`
Expected: 5 passed.

- [ ] **Step 5: Replace call sites**

Replace each of these blocks in `pipeline/deploy.py`. Use `Edit` and verify each replacement.

**Site 1 — `_cmd_status` around lines 416-421:**

Replace:
```python
    store = ConfigMapProgressStore(primary_ns, run_name=run_dir.name)
    try:
        progress = store.load()
    except (ValueError, RuntimeError) as exc:
        err(f"Failed to load progress: {exc}")
        progress = {}
```

with:
```python
    store = ConfigMapProgressStore(primary_ns, run_name=run_dir.name)
    progress = _load_progress(store, allow_unreachable=True)
```

**Site 2 — `_cmd_collect` around lines 1047-1052:**

Replace:
```python
    store = ConfigMapProgressStore(primary_ns, run_name=run_dir.name)
    try:
        progress = store.load() or None
    except (ValueError, RuntimeError) as exc:
        warn(f"Failed to load progress: {exc}")
        progress = None
```

with:
```python
    store = ConfigMapProgressStore(primary_ns, run_name=run_dir.name)
    progress = _load_progress(store, allow_unreachable=True) or None
```

**Site 3 — `_cmd_run` line 2009:**

Replace:
```python
    # Load or initialize progress
    progress = store.load()
```

with:
```python
    # Load or initialize progress
    progress = _load_progress(store)
```

**Site 4 — `_cmd_reset` line 2346:**

Replace:
```python
    store = ConfigMapProgressStore(primary_ns, run_name=run_dir.name)
    progress = store.load()
```

with:
```python
    store = ConfigMapProgressStore(primary_ns, run_name=run_dir.name)
    progress = _load_progress(store)
```

**Site 5 — `_cmd_wipe` line 2401:**

Replace:
```python
    store = ConfigMapProgressStore(primary_ns, run_name=run_dir.name)
    progress = store.load()
```

with:
```python
    store = ConfigMapProgressStore(primary_ns, run_name=run_dir.name)
    progress = _load_progress(store)
```

**Site 6 — `_cmd_run_remote` around lines 2702-2707:**

Replace:
```python
        store = ConfigMapProgressStore(namespace, run_name=run_dir.name)
        try:
            progress = store.load()
        except (RuntimeError, OSError) as exc:
            warn(f"ConfigMap unreachable — skipping pre-flight filter validation: {exc}")
            progress = None
```

with:
```python
        store = ConfigMapProgressStore(namespace, run_name=run_dir.name)
        progress = _load_progress(store, allow_unreachable=True) or None
```

- [ ] **Step 6: Verify no remaining bare `store.load()` calls in deploy.py**

Run: `grep -n "store\.load(" pipeline/deploy.py`
Expected: only matches inside the `_load_progress` helper itself.

- [ ] **Step 7: Run full lint and test suite**

Run: `ruff check pipeline/ .claude/skills/ --select F`
Expected: clean.

Run: `python -m pytest pipeline/ .claude/skills/sim2real-analyze/tests/ .claude/skills/sim2real-translate/tests/`
Expected: all tests pass (5 new helper tests added).

- [ ] **Step 8: Commit**

```bash
git add pipeline/deploy.py pipeline/tests/test_deploy_run.py docs/superpowers/plans/2026-06-04-issue-140-progress-corrupt-handling.md
git commit -m "$(cat <<'EOF'
fix(deploy): handle corrupt progress ConfigMap gracefully across subcommands

Three deploy.py subcommands (`_cmd_run`, `_cmd_reset`, `_cmd_wipe`) called
`store.load()` without catching `ValueError`, surfacing corrupt-progress
data as a raw Python traceback. `_cmd_run_remote` had a similar gap (caught
RuntimeError/OSError but not ValueError). `_cmd_status` and `_cmd_collect`
caught ValueError but warned and continued — masking data corruption as a
silent "0 pairs" outcome.

Centralize the policy in a new `_load_progress(store, *, allow_unreachable)`
helper that exits non-zero with a clear, actionable message on ValueError
(corrupt data is never recoverable by retry) and either re-raises or warns
on RuntimeError depending on the caller's tolerance. Replace all six
`store.load()` call sites with the helper. Add 5 unit tests for the helper.

The store class moved from LocalProgressStore (file-based progress.json) to
ConfigMapProgressStore since #140 was filed; the ValueError contract is
unchanged so the issue's intent applies directly to the new layout.

Closes #140
EOF
)"
```

---

## Self-Review

**Spec coverage:**
- "Catch `ValueError` from `store.load()` in all subcommand handlers" ✓ (Step 5, 6 sites).
- "Print a clear error message: which file is corrupt, and suggest re-running the prepare phase or manually fixing the file" ✓ (Step 3 — error message names the ConfigMap via the propagated ValueError text and suggests `prepare.py` / `kubectl edit configmap`).
- "Exit with non-zero status" ✓ (`sys.exit(1)`).

**Placeholder scan:** No TBDs. Each step has full code blocks or full commands.

**Type consistency:** `_load_progress(store, *, allow_unreachable: bool = False) -> dict` is used identically in helper, tests, and all 6 call sites.

**Edge cases handled:**
- `_load_progress(store, allow_unreachable=True)` with `ValueError` still exits — corrupt data is not transient (test 5).
- `_load_progress(store)` with `RuntimeError` propagates so callers without a fallback policy crash visibly (test 3) — same effective behavior as today for run/reset/wipe but now ValueError gets a clean message instead of a traceback.
- Result of `store.load()` is `{}` for "no progress yet" (existing semantic) — passes through the helper unchanged (test 1).
- `_cmd_collect`'s `or None` idiom preserved at the call site so its downstream `if not progress` checks keep working.

**Cross-path parity:** Issue is `pipeline/deploy.py` only. Single change covers all 6 affected subcommands; no other process loads progress data.
