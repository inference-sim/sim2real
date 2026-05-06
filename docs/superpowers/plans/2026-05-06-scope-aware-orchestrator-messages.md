# Scope-Aware Orchestrator Messages Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix `deploy.py run` orchestrator messages to report scoped pair counts (not total) when filters are active, and emit a clear message when all scoped pairs are already done.

**Architecture:** Three targeted changes to `_cmd_run()` in `pipeline/deploy.py`: (1) fix the orchestrator startup message, (2) add early-exit message when nothing to dispatch, (3) scope the final summary counts.

**Tech Stack:** Python, pytest

---

### Task 1: Fix orchestrator startup message to use scoped count

**Files:**
- Modify: `pipeline/deploy.py:754`
- Test: `pipeline/tests/test_deploy_run.py`

- [ ] **Step 1: Write the failing test**

```python
def test_orchestrator_message_uses_scope_count(tmp_path, capsys, monkeypatch):
    """When filters narrow scope, the orchestrator message should report scoped count."""
    import json
    from unittest.mock import patch
    from pipeline.deploy import _apply_run_filters

    progress = {
        "wl-a-baseline":  {"workload": "wl-a", "package": "baseline",  "status": "done", "namespace": None, "retries": 0},
        "wl-a-treatment": {"workload": "wl-a", "package": "treatment", "status": "done", "namespace": None, "retries": 0},
        "wl-b-baseline":  {"workload": "wl-b", "package": "baseline",  "status": "pending", "namespace": None, "retries": 0},
        "wl-b-treatment": {"workload": "wl-b", "package": "treatment", "status": "pending", "namespace": None, "retries": 0},
    }

    class _Args:
        only = None; workload = "wl-b"; package = None; status = None

    filtered = _apply_run_filters(progress, _Args())
    assert len(filtered) == 2
    # The orchestrator message must use the scoped count (2), not total (4)
```

- [ ] **Step 2: Run test to verify baseline passes (this is a unit assertion on existing filter logic)**

Run: `cd /Users/kalantar/projects/go.workspace/src/github.com/inference-sim/sim2real/.worktrees/fix-37-scope-messages && .venv/bin/python -m pytest pipeline/tests/test_deploy_run.py::test_orchestrator_message_uses_scope_count -v`

- [ ] **Step 3: Fix the orchestrator message**

In `pipeline/deploy.py`, change line 754 from:

```python
    info(f"Orchestrator: {len(discovered)} pairs, {len(namespaces)} slot(s)")
```

to:

```python
    info(f"Orchestrator: {len(_scope)} pairs in scope, {len(namespaces)} slot(s)")
```

- [ ] **Step 4: Run full test suite to verify no regressions**

Run: `cd /Users/kalantar/projects/go.workspace/src/github.com/inference-sim/sim2real/.worktrees/fix-37-scope-messages && .venv/bin/python -m pytest pipeline/ -v`
Expected: All 258 tests pass.

- [ ] **Step 5: Commit**

```bash
git add pipeline/deploy.py
git commit -m "fix(deploy): orchestrator message reports scoped pair count, not total"
```

---

### Task 2: Add early-exit message when all scoped pairs are already done

**Files:**
- Modify: `pipeline/deploy.py:754-756` (between orchestrator message and while loop)
- Test: `pipeline/tests/test_deploy_run.py`

- [ ] **Step 1: Write the failing test**

```python
def test_orchestrator_exits_with_message_when_scope_all_done(tmp_path, capsys, monkeypatch):
    """When all pairs in scope are already done, emit a message and skip the loop."""
    import json
    from pathlib import Path
    from pipeline.deploy import info

    progress = {
        "wl-a-baseline":  {"workload": "wl-a", "package": "baseline",  "status": "done", "namespace": None, "retries": 0},
        "wl-a-treatment": {"workload": "wl-a", "package": "treatment", "status": "done", "namespace": None, "retries": 0},
    }
    _scope = set(progress.keys())

    # Simulate _work_remaining() returning False for this scope
    def _work_remaining():
        return any(v["status"] in ("pending", "running", "collecting")
                   for k, v in progress.items() if k in _scope)

    assert _work_remaining() is False
```

- [ ] **Step 2: Run test to confirm assertion logic is correct**

Run: `cd /Users/kalantar/projects/go.workspace/src/github.com/inference-sim/sim2real/.worktrees/fix-37-scope-messages && .venv/bin/python -m pytest pipeline/tests/test_deploy_run.py::test_orchestrator_exits_with_message_when_scope_all_done -v`
Expected: PASS (this confirms the condition logic).

- [ ] **Step 3: Add early-exit message in deploy.py**

After the orchestrator startup message (line 754 after Task 1's fix), add:

```python
    if not _work_remaining() and not slots_busy:
        info(f"All {len(_scope)} pairs in scope already done — nothing to dispatch (use --force to reset)")
```

This goes between the `info(...)` line and the `while _work_remaining() or slots_busy:` loop.

- [ ] **Step 4: Run full test suite**

Run: `cd /Users/kalantar/projects/go.workspace/src/github.com/inference-sim/sim2real/.worktrees/fix-37-scope-messages && .venv/bin/python -m pytest pipeline/ -v`
Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add pipeline/deploy.py pipeline/tests/test_deploy_run.py
git commit -m "fix(deploy): emit message when all scoped pairs already done"
```

---

### Task 3: Scope the final summary to report only in-scope pairs

**Files:**
- Modify: `pipeline/deploy.py:870-877`
- Test: `pipeline/tests/test_deploy_run.py`

- [ ] **Step 1: Write the failing test**

```python
def test_final_summary_counts_only_scoped_pairs(tmp_path, capsys):
    """Final summary should only count pairs within scope."""
    progress = {
        "wl-a-baseline":  {"workload": "wl-a", "package": "baseline",  "status": "done", "namespace": None, "retries": 0},
        "wl-a-treatment": {"workload": "wl-a", "package": "treatment", "status": "done", "namespace": None, "retries": 0},
        "wl-b-baseline":  {"workload": "wl-b", "package": "baseline",  "status": "failed", "namespace": None, "retries": 0},
        "wl-b-treatment": {"workload": "wl-b", "package": "treatment", "status": "pending", "namespace": None, "retries": 0},
    }
    _scope = {"wl-a-baseline", "wl-a-treatment"}

    # Only count in-scope pairs
    counts: dict[str, int] = {}
    for k, v in progress.items():
        if k in _scope:
            counts[v["status"]] = counts.get(v["status"], 0) + 1

    assert counts == {"done": 2}
    assert "failed" not in counts
    assert "pending" not in counts
```

- [ ] **Step 2: Run test to verify assertion logic**

Run: `cd /Users/kalantar/projects/go.workspace/src/github.com/inference-sim/sim2real/.worktrees/fix-37-scope-messages && .venv/bin/python -m pytest pipeline/tests/test_deploy_run.py::test_final_summary_counts_only_scoped_pairs -v`
Expected: PASS

- [ ] **Step 3: Fix final summary to scope counts**

In `pipeline/deploy.py`, change the final summary block from:

```python
    # Final summary
    counts: dict[str, int] = {}
    for v in progress.values():
        counts[v["status"]] = counts.get(v["status"], 0) + 1
    print()
    ok("Run complete: " + "  ".join(f"{v} {k}" for k, v in sorted(counts.items())))
    print(f"  Progress: {progress_path}")
    print()
```

to:

```python
    # Final summary
    counts: dict[str, int] = {}
    for k, v in progress.items():
        if k in _scope:
            counts[v["status"]] = counts.get(v["status"], 0) + 1
    print()
    ok("Run complete: " + "  ".join(f"{v} {k}" for k, v in sorted(counts.items())))
    print(f"  Progress: {progress_path}")
    print()
```

- [ ] **Step 4: Run full test suite**

Run: `cd /Users/kalantar/projects/go.workspace/src/github.com/inference-sim/sim2real/.worktrees/fix-37-scope-messages && .venv/bin/python -m pytest pipeline/ -v`
Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add pipeline/deploy.py
git commit -m "fix(deploy): final summary counts only pairs in scope"
```

---

### Task 4: Run CI checks and verify

- [ ] **Step 1: Run lint**

Run: `cd /Users/kalantar/projects/go.workspace/src/github.com/inference-sim/sim2real/.worktrees/fix-37-scope-messages && .venv/bin/ruff check pipeline/ --select F`
Expected: No errors.

- [ ] **Step 2: Run full test suite**

Run: `cd /Users/kalantar/projects/go.workspace/src/github.com/inference-sim/sim2real/.worktrees/fix-37-scope-messages && .venv/bin/python -m pytest pipeline/ -v`
Expected: All tests pass.
