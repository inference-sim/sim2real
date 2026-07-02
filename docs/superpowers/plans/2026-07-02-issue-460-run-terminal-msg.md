# Fix misleading "already done" message in deploy.py run Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single misleading "already done" message at `pipeline/deploy.py:2584` with a count-by-status breakdown that names both escape hatches (`reset --only <key>` and `run --force`), so operators can distinguish a legitimately finished scope from one containing failed / timed-out / stalled pairs.

**Architecture:** One-line message rewrite. The underlying `_work_remaining()` boundary logic is correct and stays untouched (per issue Non-goal). The fix is purely operator-facing wording. Add one test that drives `_cmd_run` through the early-exit branch with a single `failed` pair and asserts on the exact substrings the AC calls out (`"1 failed"`, `"reset --only"`).

**Tech Stack:** Python 3.10+, pytest.

## Global Constraints

- Base branch: `refactor/v2-step-1` (not `main`)
- Codebase's canonical status literal is **`timed-out`** (hyphen), not `timed_out`. The issue's exemplar message says `timed_out` but that is just prose — the implemented message must use `timed-out` so the operator sees the same token in `status --run` output and this warning.
- Use `deploy.py reset --only <key>` (fully qualified) — matches the existing warn() at `deploy.py:2561`.
- `info()` supports multi-line f-strings via implicit string concat (same pattern as `deploy.py:2559-2562`).
- Path discipline: all edits inside `.claude/worktrees/issue-460-run-terminal-msg/`.

---

## File Structure

| File | Change | Reason |
|------|--------|--------|
| `pipeline/deploy.py:2583-2585` | Rewrite the message; introduce a small local helper to compute the by-status counts | Actual fix |
| `pipeline/tests/test_deploy_run.py` | Append `test_cmd_run_all_terminal_message_enumerates_states` — drives `_cmd_run` through the early exit branch with one `failed` pair; asserts on substrings | Coverage |

Not touched:
- `_work_remaining()`, `_pending_pairs()`, `_is_pair_key`, `_load_pairs`, `_resolve_scope` — the terminal-detection logic is correct.
- Any test that already runs `_cmd_run` through the dispatch loop — the message change is on the pre-dispatch early-exit branch and doesn't affect those.

---

## Task 1 — Rewrite the message + add test

**Files:**
- Modify: `pipeline/deploy.py:2576-2585` (`_work_remaining` helper stays; message call is replaced)
- Modify: `pipeline/tests/test_deploy_run.py` (append one test)

**Interfaces:**
- Consumes: `progress` (dict in scope of `_cmd_run`), `_scope` (set of pair keys in scope), `_is_pair_key`, `discovered`, `info()`.
- Produces: no new public symbols. The message-building lives entirely inline (single call site; no reuse motivation).

- [ ] **Step 1: Write the failing test**

Append to `pipeline/tests/test_deploy_run.py` — reuses the `_cmd_run`-invocation shape from the existing `test_dispatch_sets_entry_running` at line 2840. The test primes the ConfigMap store with a single `failed` pair, drives `_cmd_run` past the `_check_slot_ready` gate, and asserts on the pre-dispatch early-exit output.

```python
def test_cmd_run_all_terminal_message_enumerates_states(tmp_path, monkeypatch, capsys):
    """When every scoped pair is in a terminal state, the run message must
    (a) enumerate states with counts (0 done, 1 failed, 0 timed-out, 0 stalled),
    (b) name both escape hatches ('reset --only' and '--force'). Regression
    guard for issue #460 — the prior message said 'already done' for any
    terminal state, hiding failures from the operator."""
    import argparse
    import yaml as _yaml
    import pipeline.deploy as mod
    from pipeline.lib.progress import ConfigMapProgressStore

    run_dir = tmp_path / "runs" / "test-run"
    cluster_dir = run_dir / "cluster"
    cluster_dir.mkdir(parents=True)

    # One PipelineRun; its pair key derives to "wl-a-baseline".
    pr = {
        "metadata": {"name": "pr-a-baseline", "namespace": "ns"},
        "spec": {"params": [
            {"name": "workloadName", "value": "wl-a"},
            {"name": "phase", "value": "baseline"},
        ]},
    }
    (cluster_dir / "pipelinerun-a-baseline.yaml").write_text(_yaml.dump(pr))
    (run_dir / "run_metadata.json").write_text(json.dumps({}))

    cluster_config = {"namespaces": ["sim2real-0"]}

    # Pre-existing progress: the one pair is 'failed'. This exercises the
    # exact scenario the issue calls out — status --run says 'failed',
    # `run` used to say 'already done', now must enumerate the terminal state.
    initial_progress = {
        "wl-a-baseline": {
            "workload": "wl-a", "package": "baseline",
            "status": "failed", "namespace": "sim2real-0", "retries": 0,
        },
    }
    monkeypatch.setattr(ConfigMapProgressStore, "load",
                        lambda self: json.loads(json.dumps(initial_progress)))
    monkeypatch.setattr(ConfigMapProgressStore, "save", lambda self, d: None)

    # Skip build + slot readiness so we hit the message before dispatch.
    monkeypatch.setattr(mod, "_cmd_build", lambda *a, **kw: "skip")
    monkeypatch.setattr(mod, "_check_slot_ready", lambda ns, **kw: (True, []))
    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(mod, "EXPERIMENT_ROOT", tmp_path)

    args = argparse.Namespace(
        skip_build=True, max_retries=0, poll_interval=1,
        pending_threshold=600, max_pending_stalls=10,
        default_gpu_cost=1, gpu_resource_type="nvidia.com/gpu",
        only=None, workload=None, package=None, status=None,
        force=False, skip_teardown=False, remote=False,
        preserve_pipelineruns=False, shadow_ttl=0,
    )

    mod._cmd_run(args, run_dir, cluster_config)

    out = capsys.readouterr().out
    # AC-required substrings:
    assert "1 failed" in out
    assert "reset --only" in out
    # The old misleading string must be gone:
    assert "already done" not in out
    # Regression-guard the by-status enumeration explicitly.
    assert "0 done" in out
    assert "0 timed-out" in out
    assert "0 stalled" in out
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `.venv/bin/python -m pytest pipeline/tests/test_deploy_run.py::test_cmd_run_all_terminal_message_enumerates_states -v`
Expected: FAIL on `assert "1 failed" in out` — the current message says only `"already done"`.

- [ ] **Step 3: Rewrite the message**

Edit `pipeline/deploy.py:2581-2585`. Change:

```python
    timeout_hours = 4
    info(f"Orchestrator: {len(_scope)} pairs in scope, {len(namespaces)} slot(s)")
    if not _work_remaining() and not slots_busy:
        info(f"All {len(_scope)} pairs in scope already done — nothing to dispatch (use --force to reset)")
        return
```

to:

```python
    timeout_hours = 4
    info(f"Orchestrator: {len(_scope)} pairs in scope, {len(namespaces)} slot(s)")
    if not _work_remaining() and not slots_busy:
        # Every pair in scope is in a terminal state. Count by status so
        # the operator can distinguish a legitimately finished scope from
        # one containing failed / timed-out / stalled pairs (issue #460).
        # Uses the canonical status tokens from the ConfigMap ("timed-out"
        # with a hyphen) so this line and `status --run` speak the same
        # language.
        _terminal_states = ("done", "failed", "timed-out", "stalled")
        _counts = {s: 0 for s in _terminal_states}
        for k, v in progress.items():
            if _is_pair_key(k) and k in _scope and k in discovered:
                _counts[v.get("status", "")] = _counts.get(v.get("status", ""), 0) + 1
        _breakdown = ", ".join(f"{_counts[s]} {s}" for s in _terminal_states)
        info(f"All {len(_scope)} pairs in scope are in terminal states "
             f"({_breakdown}). Nothing to dispatch. Use "
             f"`deploy.py reset --only <key>` to retry a specific pair, or "
             f"`deploy.py run --force` to reset all pairs in scope.")
        return
```

- [ ] **Step 4: Run test — expect PASS**

Run: `.venv/bin/python -m pytest pipeline/tests/test_deploy_run.py::test_cmd_run_all_terminal_message_enumerates_states -v`
Expected: PASS.

- [ ] **Step 5: Full suite regression check**

Run: `.venv/bin/python -m pytest pipeline/ 2>&1 | tail -5`
Expected: 1158 (baseline after #459) + 1 new = 1159 passed, 2 xfailed.

- [ ] **Step 6: Lint**

Run: `.venv/bin/ruff check pipeline/ .claude/skills/ --select F 2>&1 | tail -3`
Expected: `All checks passed!`

- [ ] **Step 7: Commit**

```bash
git add pipeline/deploy.py pipeline/tests/test_deploy_run.py
git commit -m "$(cat <<'EOF'
fix(deploy): enumerate terminal states in 'nothing to dispatch' message (#460)

Prior message said 'All N pairs in scope already done' regardless of whether
the terminal state was 'done', 'failed', 'timed-out', or 'stalled'. An
operator staring at a failed pair read 'done' and missed the actionable
cue — the status subcommand and the run subcommand disagreed on what
'done' meant.

New message counts by status and names both escape hatches:

  All 1 pairs in scope are in terminal states (0 done, 1 failed, 0 timed-out,
  0 stalled). Nothing to dispatch. Use `deploy.py reset --only <key>` to
  retry a specific pair, or `deploy.py run --force` to reset all pairs in
  scope.

Uses the canonical 'timed-out' status token (hyphen) so this line matches
what `status --run` emits, not the underscore variant in the issue prose.

_work_remaining() unchanged per issue Non-goal: the boundary logic was
right; only the wording was misleading.

Refs: #460
EOF
)"
```

---

## Task 2 — Sweep + PR

**Files:** none (verification + PR only).

- [ ] **Step 1: Grep for any doc/skill/test references to the old message text**

Run:
```bash
grep -rnE "already done|nothing to dispatch" pipeline/ docs/ .claude/skills/ CLAUDE.md README.md 2>&1 | head -20
```
Expected: 0 hits after the change lands. Any stray hits → update or note as historical.

- [ ] **Step 2: Confirm parent repo has no leaked changes**

Run:
```bash
git status --short && echo '---' && git -C /Users/kalantar/projects/go.workspace/src/github.com/inference-sim/sim2real status --short | head
```
Expected: worktree shows only the two files + this plan; parent-repo output is only the pre-existing session-start noise (`M tektonc-data-collection` / `?? …`).

- [ ] **Step 3: Push and open the PR**

```bash
git push -u origin refactor/v2-step-1-issue-460-run-terminal-msg
gh pr create --base refactor/v2-step-1 \
    --title "fix(deploy): enumerate terminal states in 'nothing to dispatch' message (#460)" \
    --body-file <PR body>
```

Body:
- `Closes #460.`
- Summary: single-line message rewrite; scope stays as-is per Non-goal.
- Files changed (short list).
- AC mapping with `[x]` per item (all four AC items are automatable and covered).
- Non-obvious choice: `timed-out` (hyphen) matches the codebase, not the issue exemplar's underscore.

If `gh pr create` fails with a token error, retry with `unset GITHUB_TOKEN GH_TOKEN`.

---

## Self-review

### Spec coverage against AC

| AC item | Task | Notes |
|---------|------|-------|
| Message enumerates terminal states with counts | Task 1 | New f-string builds "0 done, 1 failed, 0 timed-out, 0 stalled". |
| Names both escape hatches | Task 1 | Message text includes `deploy.py reset --only <key>` and `deploy.py run --force`. |
| Test asserts on "1 failed" and "reset --only" | Task 1 Step 1 | Test asserts both substrings plus explicit "0 done"/"0 timed-out"/"0 stalled" regression guards. |
| Existing tests continue to pass | Task 1 Step 5 | Full-suite check after the rewrite. |

### Placeholder scan

- No TBD / TODO placeholders.
- Every code block is complete.

### Type consistency

- Status tokens all use hyphenated form (`timed-out`), consistent with the codebase.
- No new symbol names introduced.

### Open items

None — the fix is a single-line rewrite. No design questions surfaced during vet.
