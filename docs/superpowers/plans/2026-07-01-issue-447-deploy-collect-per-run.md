# Issue #447: `deploy.py collect --run R` port + delete original `_cmd_collect`

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finalize the port of `_cmd_collect` to the new per-run layout on `refactor/v2-step-1` — add missing test coverage for the `main()` dispatcher path, update `pipeline/README.md` to document the new `deploy.py collect --run R` shape, and sweep for stale collect references.

**Architecture:** Same interpretation PR 455 (#446) used for its `_cmd_run` port. Step-0 already ported `_cmd_collect`'s signature to `(args, run_dir, cluster_config)`, and PR 455 wired `main()` to per-run cluster resolution via `_load_run_cluster_config`. What remains for #447 is documentation and dispatcher-test parity with PR 455.

**Tech Stack:** Python 3.10+, pytest, ruff.

## Global Constraints

- Base branch: `refactor/v2-step-1` (per issue #447 body).
- Preserve existing test behavior — the 60+ existing `test_deploy_collect.py` tests all use `cluster_config = {"namespaces": ["ns-0"]}` (the new shape) and must continue to pass.
- No changes to public CLI flags — `deploy.py collect` keeps `--only/--workload/--package/--skip-logs`; `--run` is on the parent parser (added by step-0 / PR 455).
- Acceptance-criterion error strings from #446 (`run 'sim2real assemble --run <R>' first` and `run metadata corrupted; re-assemble`) apply to `collect` transitively via `main() → _load_run_cluster_config()` — verify by test, do not duplicate the string.
- Sweep scope for stale refs: `pipeline/`, `docs/`, `.claude/skills/`, top-level `README.md` and `CLAUDE.md`.

---

## Current state (already true on `refactor/v2-step-1@e694383`)

- `_cmd_collect(args, run_dir: Path, cluster_config: dict)` at `pipeline/deploy.py:1456` — signature ported by step-0.
- Reads `run_dir / "cluster"`, writes to `run_dir / "results" / phase / workload/` via `_extract_phases_from_pvc(..., run_dir, ...)`.
- Uses `_configmap_namespace(cluster_config)` and `ConfigMapProgressStore(primary_ns, run_name=run_dir.name)`.
- `main()` at `pipeline/deploy.py:3486` dispatches `collect` with `_cmd_collect(args, run_dir, cluster_config)` where `cluster_config` comes from `_load_run_cluster_config(run_dir)` (PR 455).
- All 60+ existing `test_deploy_collect.py` tests pass `{"namespaces": ["ns-0"]}` — new shape.

The "port" acceptance criterion is already met by prior PRs. #447's remaining scope is documentation and dispatcher-test parity.

---

### Task 1: Add `main()` dispatcher tests for `collect` subcommand

**Motivation.** PR 455 added six dispatcher tests for `deploy.py run` that verify `_load_run_cluster_config`'s acceptance-criterion errors surface through `main()`. Parity tests for `collect` catch regressions specific to the collect dispatch path (e.g., if `main()` were to accidentally skip `_load_run_cluster_config` for a subcommand). These tests are cheap regression coverage — they don't retest `_load_run_cluster_config` itself (already covered).

**Files:**
- Modify: `pipeline/tests/test_deploy_collect.py` (append at end)

**Interfaces:**
- Consumes: `deploy.main()`, `deploy.EXPERIMENT_ROOT`, argparse `--experiment-root` flag.
- Produces: 4 new test functions covering the four acceptance-criterion error paths for `collect`.

- [ ] **Step 1: Read the end of test_deploy_collect.py to know where to append**

Run: `wc -l pipeline/tests/test_deploy_collect.py` (currently 2146).

- [ ] **Step 2: Write the four failing tests**

Append to `pipeline/tests/test_deploy_collect.py`:

```python


# ── main() dispatcher tests for `collect` subcommand (#447) ────────────────

def _run_deploy_main_collect(argv, monkeypatch, tmp_path):
    """Call deploy.main() with mocked argv and --experiment-root=tmp_path.

    Mirrors the helper in test_deploy_run.py — main() re-resolves
    EXPERIMENT_ROOT from --experiment-root (or cwd), so monkeypatching the
    module-level global is not enough.
    """
    import sys as _sys
    from pipeline import deploy
    monkeypatch.setattr(_sys, "argv",
                        ["deploy.py", "--experiment-root", str(tmp_path), *argv])
    monkeypatch.setattr(deploy, "_tty", False, raising=False)
    return deploy.main()


def _make_collect_run_dir(tmp_path, run_name="trial-1", *,
                          with_cluster=True, with_metadata=True,
                          metadata_content=None):
    """Fixture helper: build a workspace/runs/<run>/ tree for collect dispatcher tests."""
    workspace = tmp_path / "workspace"
    run_dir = workspace / "runs" / run_name
    run_dir.mkdir(parents=True)
    if with_cluster:
        (run_dir / "cluster").mkdir()
    if with_metadata:
        content = metadata_content if metadata_content is not None else \
            {"version": 1, "run_name": run_name, "cluster_id": "ocp-east"}
        (run_dir / "run_metadata.json").write_text(json.dumps(content))
    return workspace, run_dir


def test_main_collect_missing_run_dir_emits_assemble_hint(tmp_path, capsys, monkeypatch):
    """`deploy.py --run trial-1 collect` with no run dir → assemble hint."""
    (tmp_path / "workspace").mkdir()
    (tmp_path / "workspace" / "setup_config.json").write_text("{}")
    with pytest.raises(SystemExit):
        _run_deploy_main_collect(["--run", "trial-1", "collect"], monkeypatch, tmp_path)
    assert "run 'sim2real assemble --run trial-1' first" in capsys.readouterr().err


def test_main_collect_missing_cluster_dir_emits_assemble_hint(tmp_path, capsys, monkeypatch):
    """`deploy.py --run trial-1 collect` with runs/trial-1/ but no cluster/ → assemble hint."""
    (tmp_path / "workspace").mkdir()
    (tmp_path / "workspace" / "setup_config.json").write_text("{}")
    _make_collect_run_dir(tmp_path, with_cluster=False)
    with pytest.raises(SystemExit):
        _run_deploy_main_collect(["--run", "trial-1", "collect"], monkeypatch, tmp_path)
    assert "run 'sim2real assemble --run trial-1' first" in capsys.readouterr().err


def test_main_collect_missing_run_metadata_emits_corrupt_hint(tmp_path, capsys, monkeypatch):
    """`deploy.py --run trial-1 collect` with no run_metadata.json → 're-assemble' hint."""
    (tmp_path / "workspace").mkdir()
    (tmp_path / "workspace" / "setup_config.json").write_text("{}")
    _make_collect_run_dir(tmp_path, with_metadata=False)
    with pytest.raises(SystemExit):
        _run_deploy_main_collect(["--run", "trial-1", "collect"], monkeypatch, tmp_path)
    assert "run metadata corrupted; re-assemble" in capsys.readouterr().err


def test_main_collect_missing_cluster_id_emits_corrupt_hint(tmp_path, capsys, monkeypatch):
    """`deploy.py --run trial-1 collect` with metadata missing cluster_id → 're-assemble' hint."""
    (tmp_path / "workspace").mkdir()
    (tmp_path / "workspace" / "setup_config.json").write_text("{}")
    _make_collect_run_dir(tmp_path,
                          metadata_content={"version": 1, "run_name": "trial-1"})
    with pytest.raises(SystemExit):
        _run_deploy_main_collect(["--run", "trial-1", "collect"], monkeypatch, tmp_path)
    assert "run metadata corrupted; re-assemble" in capsys.readouterr().err
```

- [ ] **Step 3: Run the new tests to verify they pass**

Run: `.venv/bin/python -m pytest pipeline/tests/test_deploy_collect.py -v -k "main_collect" 2>&1 | tail -20`
Expected: all 4 tests PASS.

- [ ] **Step 4: Run the full test_deploy_collect.py to ensure no regression**

Run: `.venv/bin/python -m pytest pipeline/tests/test_deploy_collect.py -v 2>&1 | tail -10`
Expected: all tests PASS (60+ existing + 4 new).

- [ ] **Step 5: Commit**

```bash
git add pipeline/tests/test_deploy_collect.py
git commit -m "test(deploy): add main() dispatcher tests for collect subcommand (#447)"
```

---

### Task 2: Add `_cmd_collect` empty-namespaces test

**Motivation.** `_cmd_collect` has two internal guards that fire when `cluster_config["namespaces"]` is missing or empty — `err("No namespace configured.")` at line 1462, and `err("No namespace configured. Run cluster.py provision with --namespaces.")` at line 1472. The first guard is reached if `NAMESPACE` env var is unset AND `cluster_namespaces[0]` errors out. No test exercises either — every existing test passes `{"namespaces": ["ns-0"]}`. This guards the "cluster_config was read but is malformed" edge case that `_load_run_cluster_config` doesn't catch (it only checks the run_metadata side).

**Files:**
- Modify: `pipeline/tests/test_deploy_collect.py` (append after Task 1's tests)

**Interfaces:**
- Consumes: `deploy._cmd_collect(args, run_dir, cluster_config)`.
- Produces: 1 test.

- [ ] **Step 1: Write the failing test**

Append to `pipeline/tests/test_deploy_collect.py`:

```python


def test_cmd_collect_empty_namespaces_exits(tmp_path, capsys, monkeypatch):
    """_cmd_collect with cluster_config missing 'namespaces' → 'No namespace configured.' exit."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "trial-1"
    (run_dir / "cluster").mkdir(parents=True)

    # NAMESPACE env var must be unset for the guard to fire.
    monkeypatch.delenv("NAMESPACE", raising=False)

    class Args:
        package = None
        skip_logs = False

    with pytest.raises(SystemExit):
        deploy._cmd_collect(Args(), run_dir, {})
    assert "No namespace configured." in capsys.readouterr().err
```

- [ ] **Step 2: Run test to verify it passes**

Run: `.venv/bin/python -m pytest pipeline/tests/test_deploy_collect.py::test_cmd_collect_empty_namespaces_exits -v 2>&1 | tail -10`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add pipeline/tests/test_deploy_collect.py
git commit -m "test(deploy): guard _cmd_collect against empty cluster_config namespaces (#447)"
```

---

### Task 3: Update `pipeline/README.md` `deploy.py collect` section

**Motivation.** The README's `deploy.py collect` bullet (line 301) still describes the destination path but says nothing about `--run R` or per-run cluster resolution. PR 455 added a similar update to the `deploy.py run` bullet (line 255) with a full sentence documenting cluster resolution and the acceptance-criterion error strings. Parity for collect keeps the README self-consistent.

**Files:**
- Modify: `pipeline/README.md:301` (the `**deploy.py collect**` bullet)

**Interfaces:** N/A (documentation).

- [ ] **Step 1: Read the current `deploy.py collect` bullet**

The current text at line 301:
```
**`deploy.py collect`** — extracts results from the cluster PVC and writes to `workspace/runs/<run>/results/{phase}/<workload>/`. Repeated collects are incremental: each workload's remote `trace_data.csv` mtime is probed and skipped if the local copy is already up to date. If the mtime probe fails (e.g., pod not running), collection falls back to a full copy — this is the expected degradation path.
```

- [ ] **Step 2: Replace with the updated bullet**

Use `Edit` to replace the bullet with:

```
**`deploy.py collect`** — extracts results from the cluster PVC and writes to `workspace/runs/<run>/results/{phase}/<workload>/`. Repeated collects are incremental: each workload's remote `trace_data.csv` mtime is probed and skipped if the local copy is already up to date. If the mtime probe fails (e.g., pod not running), collection falls back to a full copy — this is the expected degradation path. Like `deploy.py run`, the run's cluster is resolved from `workspace/runs/<R>/run_metadata.json:cluster_id`; missing `runs/<R>/` or `runs/<R>/cluster/` exits with `run 'sim2real assemble --run <R>' first`, and a missing / unparseable `run_metadata.json` or missing `cluster_id` exits with `run metadata corrupted; re-assemble`.
```

- [ ] **Step 3: Verify the file still parses (light lint)**

Run: `.venv/bin/python -c "open('pipeline/README.md').read()"`
Expected: no output (file reads cleanly).

- [ ] **Step 4: Commit**

```bash
git add pipeline/README.md
git commit -m "docs(deploy): document deploy.py collect --run R cluster resolution (#447)"
```

---

### Task 4: Sweep for stale collect references

**Motivation.** Following the vet-issue and PR 455 pattern of surveying `pipeline/`, `docs/`, `.claude/skills/`, and top-level docs for references that assume the pre-port shape. Nothing in the exhaustive grep earlier suggested any stale collect-specific references outside historical design docs, but this task confirms with a fresh sweep and updates anything found.

**Files:**
- Potentially modify (based on sweep results): `.claude/skills/sim2real-analyze/**`, top-level `README.md`, `CLAUDE.md`. Verified in Step 1.

- [ ] **Step 1: Grep for `deploy.py collect` and `_cmd_collect` outside pipeline/deploy.py**

Run:
```bash
grep -rn "deploy\.py collect\|_cmd_collect" \
  --include="*.md" --include="*.py" \
  pipeline/ docs/ .claude/ README.md CLAUDE.md 2>/dev/null \
  | grep -v "pipeline/deploy\.py\|pipeline/tests/test_deploy_collect" \
  | grep -v "docs/epics/step-.*/design\.md\|docs/superpowers/plans/"
```

Historical design docs under `docs/epics/step-*/design.md` and old plans under `docs/superpowers/plans/` are historical records — leave them alone.

- [ ] **Step 2: For each remaining hit, decide stale / accurate / unrelated**

Expected outcomes based on earlier exhaustive grep:
- `pipeline/README.md` — already updated in Task 3.
- `pipeline/deploy.py` (mentions in `usage` help string at line 3329-3330) — verify these show `--run` correctly or note they're on the parent parser.
- `.claude/skills/sim2real-analyze/` — check for any reference that assumes old collect paths. Only update if a real desync is found.
- Top-level `README.md` and `CLAUDE.md` — check.

- [ ] **Step 3: Apply any updates found**

For each stale hit, use `Edit` to update. If nothing is stale, note "sweep found no stale references" in the commit message rather than skipping the sweep silently.

- [ ] **Step 4: Grep for `workspace/cluster/` (without `s`) — the old flat-workspace shape**

Run:
```bash
grep -rn "workspace/cluster/" \
  --include="*.md" --include="*.py" \
  pipeline/ docs/ .claude/ README.md CLAUDE.md 2>/dev/null \
  | grep -v "workspace/clusters/" \
  | grep -v "docs/epics/step-.*/design\.md\|docs/superpowers/plans/"
```

`workspace/clusters/<id>/` (plural, with `s`) is the current step-0 layout and should be preserved. Only `workspace/cluster/` (singular, without `s`) is the stale pre-refactor path.

- [ ] **Step 5: Update any stale singular `workspace/cluster/` references found**

- [ ] **Step 6: Commit (if any changes)**

```bash
git add <files>
git commit -m "docs: sweep stale collect references (#447)"
```

If nothing was stale, skip the commit and note "sweep complete, no stale references" in the PR body.

---

### Task 5: Verification, push, and PR

**Files:** N/A.

- [ ] **Step 1: Run the full test suite**

Run:
```bash
.venv/bin/python -m pytest pipeline/ -v 2>&1 | tail -15
```
Expected: all tests pass (was 1181 passed + 2 xfailed on origin/refactor/v2-step-1; expect 1186 passed + 2 xfailed after 5 new tests).

- [ ] **Step 2: Run lint**

Run: `ruff check pipeline/ --select F`
Expected: no output (clean).

- [ ] **Step 3: Confirm branch is clean and diff is contained to worktree**

Run:
```bash
git status
git -C /Users/kalantar/projects/go.workspace/src/github.com/inference-sim/sim2real status
```
Expected: worktree has committed changes; parent repo is unchanged.

- [ ] **Step 4: Push branch**

Run:
```bash
git push -u origin refactor/v2-step-1-issue-447-deploy-collect-per-run
```

- [ ] **Step 5: Create PR**

Run:
```bash
gh pr create --base refactor/v2-step-1 --title "step-1 PR 4: deploy.py collect --run R port + main() dispatcher tests (#447)" --body-file <heredoc>
```

The PR body should:
- `Closes #447`.
- Summarize: like PR 455 for `_cmd_run`, `_cmd_collect`'s signature was already ported by step-0 and PR 455 wired per-run cluster resolution in `main()`. This PR adds the missing `collect`-specific dispatcher tests, documents `deploy.py collect --run R` in `pipeline/README.md`, and sweeps for stale references.
- Note the same interpretation as PR 455 for "Delete original `_cmd_collect`" — the delete happened at step-0; this PR closes the gap in test coverage and docs.
- List acceptance-criterion mapping.
- List sweep results.
- Note that the real-cluster demo gate is manual.

---

## Self-Review

**Spec coverage** — issue #447 acceptance criteria:
- [x] `deploy.py collect --run trial-1` produces `runs/trial-1/results/<phase>/<workload>/per_request_lifecycle_metrics.json` — mechanism already in place via `_extract_phases_from_pvc(run_dir=...)` writing to `run_dir/results/<phase>/<workload>/` (full-copy path uses `kubectl cp .../{workload}/` which includes `per_request_lifecycle_metrics.json`). No plan task required — verify manually against `sr` cluster as end-of-step demo gate.
- [x] GPU logs in `runs/trial-1/results/<phase>/<workload>/gpu_logs/<node>.log` — same mechanism, unchanged shape.
- [x] Original `_cmd_collect` deleted — interpretation per PR 455: happened at step-0. PR body documents this.
- [x] `test_deploy_collect.py` rewritten for new layout — already rewritten at step-0; tasks 1 & 2 close the gap in dispatcher coverage.
- [ ] End-of-step-1 demo gate — MANUAL; not automatable from harness. PR body notes this.
- [x] `pipeline/README.md` updated — Task 3.

**Placeholder scan** — no TBD / TODO / "add appropriate" instances.

**Type consistency** — `_cmd_collect(args, run_dir, cluster_config: dict)` signature is what we test against and what `main()` invokes; consistent.
