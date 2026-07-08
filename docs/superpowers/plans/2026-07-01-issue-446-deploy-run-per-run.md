# Issue #446: `deploy.py run --run R` port + delete original `_cmd_run` — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move `deploy.py` from single-cluster-global cluster resolution to per-run cluster resolution via `runs/<R>/run_metadata.json:cluster_id`, and replace stale `prepare.py`-era error messages / comments with the acceptance-criterion strings from issue #446.

**Architecture:** The dispatcher in `deploy.py:main()` currently loads a single global cluster via `_load_cluster_config()` (which asserts single-cluster and uses `layout.list_cluster_ids()`). After this PR, subcommands that need a run's cluster (`build`, `run`, `status`, `collect`, `reset`, `wipe`, `pairs`) load the cluster from `runs/<R>/run_metadata.json:cluster_id` and resolve via `cluster_ops.read_cluster_config(cluster_id)`. `stop` remains global (no `--run`). All error paths use the acceptance-criterion strings. Function signatures of downstream helpers (`_cmd_run`, `_cmd_collect`, etc.) stay unchanged — they already accept `cluster_config: dict`.

**Tech Stack:** Python 3.10+, pytest, argparse, `pipeline/lib/layout.py`, `pipeline/lib/cluster_ops.py`.

## Global Constraints

- **Base branch:** `refactor/v2-step-1`. All commits go on the worktree branch `refactor/v2-step-1-issue-446-deploy-run-per-run`.
- **`_is_pair_key` and `_load_pairs` MUST remain unchanged** (issue: structured parser deferred to step-4).
- **No new CLI flags** — `--run NAME` already exists at the top-level parser and is unchanged.
- **Exact error strings (issue acceptance criteria):**
  - Missing `runs/<R>/`: `"run 'sim2real assemble --run <R>' first"` (substitute the actual run name for `<R>`).
  - Missing `runs/<R>/cluster/`: same message.
  - Missing `run_metadata.json` OR `cluster_id` field absent: `"run metadata corrupted; re-assemble"`.
- **Tests run from repo root:** `python -m pytest pipeline/tests/test_deploy_run.py -v`.
- **Ruff:** must pass `ruff check pipeline/ --select F`.
- **Path discipline:** every `Read`/`Edit`/`Write`/`Bash` file op path must contain `.claude/worktrees/issue-446-deploy-run-per-run/`. Never target the parent repo.

---

## File Structure

**Modified:**
- `pipeline/deploy.py` — dispatcher (main), stale prepare.py refs, `_cmd_run`'s error string.
- `pipeline/tests/test_deploy_run.py` — new tests for dispatcher error paths and cluster resolution; preserve existing status/runtime tests.
- `pipeline/README.md` — `deploy.py run` section reflects new error messages.

**Not modified:**
- `pipeline/lib/cluster_ops.py:read_cluster_config` — already returns `{}` on missing file; callers hard-fail themselves.
- `pipeline/lib/layout.py` — `cluster_dir()` / `cluster_config_path()` already exist.
- `pipeline/lib/assemble_run.py` — already writes `cluster_id` to `run_metadata.json` (line ~519).
- Any `_cmd_*` handler signature — they already accept `cluster_config: dict`.

---

## Cross-System Contracts

- `runs/<R>/run_metadata.json` schema (writer: `pipeline/lib/assemble_run.py:write_run_metadata`, line ~167; call site line ~513): includes `cluster_id: str` field.
- `cluster_ops.read_cluster_config(cluster_id) -> dict`: returns `{}` if the config file is missing. Callers must check.
- `layout.cluster_dir(cluster_id) -> Path`: `workspace/clusters/<cluster_id>/`.
- `layout.cluster_config_path(cluster_id) -> Path`: `workspace/clusters/<cluster_id>/cluster_config.json`.
- ConfigMap `sim2real-progress-<run_name>` (unchanged; already keyed by run in `ConfigMapProgressStore`).

---

### Task 1: Add per-run cluster loader in `deploy.py`

**Files:**
- Modify: `pipeline/deploy.py` — add a private helper `_load_run_cluster_config(run_dir: Path) -> dict` near `_load_cluster_config` (around line 130).

**Interfaces:**
- Consumes: `run_dir: Path` (already computed by `main()`).
- Produces: `_load_run_cluster_config(run_dir) -> dict` — returns the resolved cluster_config dict; exits with acceptance-criterion strings on any failure.

**Behavior:**
- If `run_dir` does not exist → `err("run 'sim2real assemble --run <R>' first")` (substitute `run_dir.name`), `sys.exit(1)`.
- If `run_dir / "cluster"` does not exist → same message.
- If `run_dir / "run_metadata.json"` does not exist → `err("run metadata corrupted; re-assemble")`, `sys.exit(1)`.
- If `run_metadata.json` fails to parse as JSON → same "corrupted" error.
- If parsed metadata lacks `cluster_id` (missing key OR falsy value) → same "corrupted" error.
- On success: return `cluster_ops.read_cluster_config(cluster_id)`.

Note: `cluster_ops.read_cluster_config` returns `{}` when the cluster file itself is missing. That's the pre-existing "no namespaces configured" error path — leave it to the downstream handlers as today.

- [ ] **Step 1: Write the failing tests for the helper**

Append to `pipeline/tests/test_deploy_run.py`:

```python
# ── _load_run_cluster_config ────────────────────────────────────────────────

import json as _json


def _make_run_dir(tmp_path, run_name="trial-1", *, with_cluster=True,
                   with_metadata=True, metadata_content=None):
    """Fixture helper: build a workspace/runs/<run>/ tree for dispatcher tests."""
    workspace = tmp_path / "workspace"
    run_dir = workspace / "runs" / run_name
    run_dir.mkdir(parents=True)
    if with_cluster:
        (run_dir / "cluster").mkdir()
    if with_metadata:
        content = metadata_content if metadata_content is not None else \
            {"version": 1, "run_name": run_name, "cluster_id": "ocp-east"}
        (run_dir / "run_metadata.json").write_text(_json.dumps(content))
    return workspace, run_dir


def test_load_run_cluster_config_missing_run_dir(tmp_path, capsys, monkeypatch):
    """Missing runs/<R>/ → 'run 'sim2real assemble --run <R>' first'."""
    from pipeline import deploy
    monkeypatch.setattr(deploy, "EXPERIMENT_ROOT", tmp_path)
    run_dir = tmp_path / "workspace" / "runs" / "trial-1"
    with pytest.raises(SystemExit) as exc:
        deploy._load_run_cluster_config(run_dir)
    assert exc.value.code == 1
    assert "run 'sim2real assemble --run trial-1' first" in capsys.readouterr().err


def test_load_run_cluster_config_missing_cluster_dir(tmp_path, capsys, monkeypatch):
    """Missing runs/<R>/cluster/ → same acceptance-criterion message."""
    from pipeline import deploy
    monkeypatch.setattr(deploy, "EXPERIMENT_ROOT", tmp_path)
    _, run_dir = _make_run_dir(tmp_path, with_cluster=False)
    with pytest.raises(SystemExit) as exc:
        deploy._load_run_cluster_config(run_dir)
    assert exc.value.code == 1
    assert "run 'sim2real assemble --run trial-1' first" in capsys.readouterr().err


def test_load_run_cluster_config_missing_metadata(tmp_path, capsys, monkeypatch):
    """Missing run_metadata.json → 'run metadata corrupted; re-assemble'."""
    from pipeline import deploy
    monkeypatch.setattr(deploy, "EXPERIMENT_ROOT", tmp_path)
    _, run_dir = _make_run_dir(tmp_path, with_metadata=False)
    with pytest.raises(SystemExit) as exc:
        deploy._load_run_cluster_config(run_dir)
    assert exc.value.code == 1
    assert "run metadata corrupted; re-assemble" in capsys.readouterr().err


def test_load_run_cluster_config_malformed_metadata(tmp_path, capsys, monkeypatch):
    """Non-JSON run_metadata.json → 'run metadata corrupted; re-assemble'."""
    from pipeline import deploy
    monkeypatch.setattr(deploy, "EXPERIMENT_ROOT", tmp_path)
    _, run_dir = _make_run_dir(tmp_path)
    (run_dir / "run_metadata.json").write_text("this is { not json")
    with pytest.raises(SystemExit) as exc:
        deploy._load_run_cluster_config(run_dir)
    assert exc.value.code == 1
    assert "run metadata corrupted; re-assemble" in capsys.readouterr().err


def test_load_run_cluster_config_no_cluster_id(tmp_path, capsys, monkeypatch):
    """run_metadata.json without cluster_id → 'run metadata corrupted; re-assemble'."""
    from pipeline import deploy
    monkeypatch.setattr(deploy, "EXPERIMENT_ROOT", tmp_path)
    _make_run_dir(tmp_path, metadata_content={"version": 1, "run_name": "trial-1"})
    run_dir = tmp_path / "workspace" / "runs" / "trial-1"
    with pytest.raises(SystemExit) as exc:
        deploy._load_run_cluster_config(run_dir)
    assert exc.value.code == 1
    assert "run metadata corrupted; re-assemble" in capsys.readouterr().err


def test_load_run_cluster_config_empty_cluster_id(tmp_path, capsys, monkeypatch):
    """run_metadata.json with empty cluster_id → 'run metadata corrupted; re-assemble'."""
    from pipeline import deploy
    monkeypatch.setattr(deploy, "EXPERIMENT_ROOT", tmp_path)
    _make_run_dir(tmp_path,
                  metadata_content={"version": 1, "run_name": "trial-1", "cluster_id": ""})
    run_dir = tmp_path / "workspace" / "runs" / "trial-1"
    with pytest.raises(SystemExit) as exc:
        deploy._load_run_cluster_config(run_dir)
    assert exc.value.code == 1
    assert "run metadata corrupted; re-assemble" in capsys.readouterr().err


def test_load_run_cluster_config_reads_via_cluster_ops(tmp_path, monkeypatch):
    """Success path: cluster_id extracted from metadata, cluster_ops.read_cluster_config called with it."""
    from pipeline import deploy
    monkeypatch.setattr(deploy, "EXPERIMENT_ROOT", tmp_path)
    _make_run_dir(tmp_path)
    run_dir = tmp_path / "workspace" / "runs" / "trial-1"

    calls = []
    def fake_read(cid):
        calls.append(cid)
        return {"namespaces": ["ns-a", "ns-b"]}
    monkeypatch.setattr(deploy.cluster_ops, "read_cluster_config", fake_read)

    cfg = deploy._load_run_cluster_config(run_dir)
    assert calls == ["ocp-east"]
    assert cfg == {"namespaces": ["ns-a", "ns-b"]}
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `python -m pytest pipeline/tests/test_deploy_run.py -v -k "_load_run_cluster_config"`
Expected: 7 tests, all fail with `AttributeError: module 'pipeline.deploy' has no attribute '_load_run_cluster_config'`.

- [ ] **Step 3: Implement `_load_run_cluster_config`**

Locate the `_load_cluster_config` function in `pipeline/deploy.py` (currently at line 130). Add the following helper directly beneath it (before the `# ── Progress store loading ──` divider that starts at line 155):

```python
def _load_run_cluster_config(run_dir: Path) -> dict:
    """Load cluster_config for a specific run.

    Resolves the cluster via ``run_metadata.json:cluster_id`` (per-run) rather
    than the workspace's global cluster list. All error paths emit the exact
    acceptance-criterion strings from issue #446 and exit.
    """
    run_name = run_dir.name
    if not run_dir.exists() or not (run_dir / "cluster").exists():
        err(f"run 'sim2real assemble --run {run_name}' first")
        sys.exit(1)

    meta_path = run_dir / "run_metadata.json"
    if not meta_path.exists():
        err("run metadata corrupted; re-assemble")
        sys.exit(1)
    try:
        meta = json.loads(meta_path.read_text())
    except (json.JSONDecodeError, OSError):
        err("run metadata corrupted; re-assemble")
        sys.exit(1)

    cluster_id = meta.get("cluster_id") if isinstance(meta, dict) else None
    if not cluster_id:
        err("run metadata corrupted; re-assemble")
        sys.exit(1)

    return cluster_ops.read_cluster_config(cluster_id)
```

- [ ] **Step 4: Run the tests and verify they pass**

Run: `python -m pytest pipeline/tests/test_deploy_run.py -v -k "_load_run_cluster_config"`
Expected: 7 tests, all pass.

- [ ] **Step 5: Commit**

```bash
git add pipeline/deploy.py pipeline/tests/test_deploy_run.py
git commit -m "feat(deploy): resolve cluster per-run from run_metadata.json (#446)

Add _load_run_cluster_config helper that reads cluster_id from
runs/<R>/run_metadata.json and dispatches to cluster_ops.read_cluster_config,
replacing the global single-cluster discovery in _load_cluster_config for
run-scoped subcommands.

Emits the issue #446 acceptance-criterion strings:
- Missing runs/<R>/ or runs/<R>/cluster/:
    \"run 'sim2real assemble --run <R>' first\"
- Missing/malformed run_metadata.json or missing cluster_id field:
    \"run metadata corrupted; re-assemble\""
```

---

### Task 2: Wire the dispatcher to use the per-run loader

**Files:**
- Modify: `pipeline/deploy.py` — `main()` (line ~3391).

**Interfaces:**
- Consumes: `_load_run_cluster_config(run_dir) -> dict` from Task 1.
- Produces: dispatcher behavior — no signature change to `_cmd_run`, `_cmd_collect`, etc.

**Behavior:**
- `stop` continues to use `_load_cluster_config()` (global — no `--run` context).
- All other subcommands (`build`, `run`, `status`, `collect`, `reset`, `wipe`, `pairs`) resolve `cluster_config` via `_load_run_cluster_config(run_dir)` after `run_dir` is determined.
- The old "Run directory not found" error is replaced by `_load_run_cluster_config`'s acceptance-criterion string.

- [ ] **Step 1: Write the failing dispatcher tests**

Append to `pipeline/tests/test_deploy_run.py`:

```python
# ── main() dispatcher: per-run cluster resolution ──────────────────────────

def _run_deploy_main(argv, monkeypatch, tmp_path):
    """Call deploy.main() with a mocked argv and EXPERIMENT_ROOT."""
    import sys as _sys
    from pipeline import deploy
    monkeypatch.setattr(_sys, "argv", ["deploy.py", *argv])
    monkeypatch.setattr(deploy, "EXPERIMENT_ROOT", tmp_path)
    # Prevent argparse-level chdir shenanigans and avoid interactive TTY paths.
    monkeypatch.setattr(deploy, "_tty", False, raising=False)
    return deploy.main()


def test_main_missing_run_dir_emits_assemble_hint(tmp_path, capsys, monkeypatch):
    """`deploy.py run --run trial-1` with no run dir → assemble hint."""
    # Provide a setup_config.json so _load_setup_config succeeds cleanly.
    (tmp_path / "workspace").mkdir()
    (tmp_path / "workspace" / "setup_config.json").write_text("{}")
    with pytest.raises(SystemExit):
        _run_deploy_main(["run", "--run", "trial-1"], monkeypatch, tmp_path)
    assert "run 'sim2real assemble --run trial-1' first" in capsys.readouterr().err


def test_main_missing_cluster_dir_emits_assemble_hint(tmp_path, capsys, monkeypatch):
    """`deploy.py run --run trial-1` with runs/trial-1/ but no cluster/ → assemble hint."""
    (tmp_path / "workspace").mkdir()
    (tmp_path / "workspace" / "setup_config.json").write_text("{}")
    _make_run_dir(tmp_path, with_cluster=False)
    with pytest.raises(SystemExit):
        _run_deploy_main(["run", "--run", "trial-1"], monkeypatch, tmp_path)
    assert "run 'sim2real assemble --run trial-1' first" in capsys.readouterr().err


def test_main_missing_run_metadata_emits_corrupt_hint(tmp_path, capsys, monkeypatch):
    """`deploy.py run --run trial-1` with no run_metadata.json → 're-assemble' hint."""
    (tmp_path / "workspace").mkdir()
    (tmp_path / "workspace" / "setup_config.json").write_text("{}")
    _make_run_dir(tmp_path, with_metadata=False)
    with pytest.raises(SystemExit):
        _run_deploy_main(["run", "--run", "trial-1"], monkeypatch, tmp_path)
    assert "run metadata corrupted; re-assemble" in capsys.readouterr().err


def test_main_missing_cluster_id_emits_corrupt_hint(tmp_path, capsys, monkeypatch):
    """`deploy.py run --run trial-1` with metadata missing cluster_id → 're-assemble' hint."""
    (tmp_path / "workspace").mkdir()
    (tmp_path / "workspace" / "setup_config.json").write_text("{}")
    _make_run_dir(tmp_path,
                  metadata_content={"version": 1, "run_name": "trial-1"})
    with pytest.raises(SystemExit):
        _run_deploy_main(["run", "--run", "trial-1"], monkeypatch, tmp_path)
    assert "run metadata corrupted; re-assemble" in capsys.readouterr().err
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `python -m pytest pipeline/tests/test_deploy_run.py -v -k "test_main_"`
Expected: 4 tests. All fail — likely surfaces the old "Run directory not found" string on the first, and the others don't reach the per-run loader path yet.

- [ ] **Step 3: Wire main() to use `_load_run_cluster_config`**

Replace the block starting at line 3406 (`setup_config = _load_setup_config()` … through the final `elif cmd == "pairs"` branch) with:

```python
    setup_config = _load_setup_config()

    cmd = args.command

    if cmd == "stop":
        cluster_config = _load_cluster_config()
        namespaces = [ns for ns in (cluster_config.get("namespaces") or []) if ns]
        if not namespaces:
            err("No namespaces configured. Run cluster.py provision with --namespaces.")
            sys.exit(1)
        _cmd_stop(namespace=namespaces[0])
        return

    run_name = args.run or setup_config.get("current_run", "")
    if not run_name:
        err("No run name. Use --run NAME or set current_run in setup_config.json.")
        sys.exit(1)
    run_dir = EXPERIMENT_ROOT / "workspace" / "runs" / run_name

    cluster_config = _load_run_cluster_config(run_dir)

    if cmd == "build":
        namespaces = cluster_config.get("namespaces") or []
        if not namespaces or not namespaces[0]:
            err("No namespaces configured. Run cluster.py provision with --namespaces."); sys.exit(1)
        _cmd_build(run_dir, namespace=namespaces[0],
                   skip_build=getattr(args, "skip_build", False))
        return

    if cmd == "run":
        if getattr(args, "remote", False):
            _cmd_run_remote(args, run_dir, setup_config, cluster_config)
        else:
            _cmd_run(args, run_dir, cluster_config)
    elif cmd == "status":
        _cmd_status(args, run_dir, cluster_config=cluster_config)
    elif cmd == "collect":
        _cmd_collect(args, run_dir, cluster_config)
    elif cmd == "reset":
        cluster_dir = run_dir / "cluster"
        discovered = _load_pairs(cluster_dir)
        namespaces = [ns for ns in (cluster_config.get("namespaces") or []) if ns]
        if not namespaces:
            warn("No namespaces in cluster_config — PipelineRun deletion for done pairs may be incomplete")
        _cmd_reset(args, run_dir, discovered,
                   namespaces=namespaces or None,
                   cluster_config=cluster_config)
    elif cmd == "wipe":
        _cmd_wipe(args, run_dir, cluster_config=cluster_config)
    elif cmd == "pairs":
        cluster_dir = run_dir / "cluster"
        _cmd_pairs(cluster_dir, keys_only=args.keys_only,
                   workloads_only=args.workloads_only,
                   packages_only=args.packages_only)
    else:
        err("No subcommand specified. Use: deploy.py build | run | status | collect | stop | reset | wipe | pairs")
        sys.exit(1)
```

Key changes vs. the current code:
- Removed the top-level `cluster_config = _load_cluster_config()` call.
- `stop` now loads global cluster inline (only place that still uses it).
- Removed the old `if not run_dir.exists(): err(f"Run directory not found: {run_dir}"); sys.exit(1)` block — `_load_run_cluster_config(run_dir)` performs that check with the acceptance-criterion string.
- All other branches unchanged; each still passes `run_dir` and `cluster_config` to its handler.

- [ ] **Step 4: Run the dispatcher tests and verify they pass**

Run: `python -m pytest pipeline/tests/test_deploy_run.py -v -k "test_main_"`
Expected: 4 tests pass.

- [ ] **Step 5: Run the FULL test file to verify no regression**

Run: `python -m pytest pipeline/tests/test_deploy_run.py -v`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add pipeline/deploy.py pipeline/tests/test_deploy_run.py
git commit -m "feat(deploy): main() resolves cluster per-run via run_metadata.json (#446)

Route all run-scoped subcommands (build/run/status/collect/reset/wipe/pairs)
through _load_run_cluster_config so the cluster comes from
runs/<R>/run_metadata.json:cluster_id, not the workspace-global cluster list.

'stop' remains global (no --run context).

Removes the top-level _load_cluster_config() call from main() and the
'Run directory not found: <path>' error — the run-dir existence check now
lives in _load_run_cluster_config and emits the acceptance-criterion
'run 'sim2real assemble --run <R>' first' message."
```

---

### Task 3: Sweep stale `prepare.py` references from `_cmd_run`, `_cmd_run_remote`, and `_load_progress`

**Files:**
- Modify: `pipeline/deploy.py` — 6 stale mentions.

**Interfaces:**
- No signature changes. Purely stale-reference sweep.

**Behavior:** replace prepare.py references with `sim2real assemble` where the reference is still meaningful, or drop it where the reference is dead.

The 6 stale hits (confirmed via `grep -n "prepare\.py\|prepare first" pipeline/deploy.py`):

| Line | Kind | Fix |
|---|---|---|
| 186 | Error string in `_load_progress` corrupt-data path | Replace `"Re-run prepare.py"` with a neutral rebuild hint that doesn't name a non-existent script. |
| 2463 | Error string in `_cmd_run` when `cluster/` is empty | Replace with the issue's acceptance-criterion message. |
| 2507 | Comment above orphans block | Update to reference `sim2real assemble`. |
| 2518 | Warn message string interpolation `(likely from a prior prepare.py)` | Update to reference `sim2real assemble`. |
| 3198 | Comment inside `_cmd_run_remote` | Update to reference `sim2real assemble`. |
| 3233 | Error string in `_cmd_run_remote` | Update to reference `sim2real assemble`. |

- [ ] **Step 1: Write a failing test for the `_cmd_run` empty-cluster error string**

Append to `pipeline/tests/test_deploy_run.py`:

```python
# ── _cmd_run: no-pairs error message ───────────────────────────────────────

def test_cmd_run_empty_cluster_dir_emits_assemble_hint(tmp_path, capsys, monkeypatch):
    """_cmd_run with an empty runs/<R>/cluster/ → 'run 'sim2real assemble --run <R>' first'."""
    from pipeline import deploy
    _make_run_dir(tmp_path)  # creates workspace/runs/trial-1/{cluster/,run_metadata.json}
    run_dir = tmp_path / "workspace" / "runs" / "trial-1"

    # _cmd_run calls _cmd_build early on; monkeypatch it out to avoid image logic.
    monkeypatch.setattr(deploy, "_cmd_build", lambda *a, **kw: None)

    class _Args:
        skip_build = True
        gpu_resource_type = None
        default_gpu_cost = 1
        defaults_path = None
        max_retries = 2
        poll_interval = 30
        pending_threshold = 600
        max_pending_stalls = 10
        force = False
        preserve_pipelineruns = False
        skip_teardown = False
        only = None
        workload = None
        package = None
        status = None

    with pytest.raises(SystemExit):
        deploy._cmd_run(_Args(), run_dir, {"namespaces": ["ns-a"]})
    assert "run 'sim2real assemble --run trial-1' first" in capsys.readouterr().err
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `python -m pytest pipeline/tests/test_deploy_run.py -v -k "test_cmd_run_empty_cluster"`
Expected: FAIL — the current error string still says `"No pairs found in cluster/. Run prepare.py first."`

- [ ] **Step 3: Apply the 6 sweeps**

**Sweep 1 (line 186)** — in `_load_progress`, replace:
```python
        err("Re-run prepare.py, or fix the ConfigMap manually with "
            "`kubectl edit configmap <name> -n <namespace>`.")
```
with:
```python
        err("Re-assemble the run (sim2real assemble --run <R>), or fix the "
            "ConfigMap manually with `kubectl edit configmap <name> -n <namespace>`.")
```

**Sweep 2 (line 2463)** — in `_cmd_run`, replace:
```python
        err("No pairs found in cluster/. Run prepare.py first."); sys.exit(1)
```
with:
```python
        err(f"run 'sim2real assemble --run {run_dir.name}' first"); sys.exit(1)
```

**Sweep 3 (line 2507)** — in `_cmd_run`, replace the comment:
```python
    # Orphans: pair_keys in progress (in scope, still active) but absent from
    # cluster/. Happens when prepare.py is re-run with a different workload set
    # between stop and the next run. Without this guard, _pending_pairs would
    # surface them and the dispatch loop's pair_costs[pair_key] would KeyError
    # at startup (#408).
```
with:
```python
    # Orphans: pair_keys in progress (in scope, still active) but absent from
    # cluster/. Happens when sim2real assemble is re-run with a different
    # workload set between stop and the next run. Without this guard,
    # _pending_pairs would surface them and the dispatch loop's
    # pair_costs[pair_key] would KeyError at startup (#408).
```

**Sweep 4 (line 2518)** — in `_cmd_run`, replace the warn interpolation:
```python
        warn(f"{len(orphans)} progress entries have no PipelineRun in cluster/ "
             f"(likely from a prior prepare.py): {orphans}. Skipping. "
             f"Remove the entries manually or via `deploy.py reset --only <key>` "
             f"if they should not return.")
```
with:
```python
        warn(f"{len(orphans)} progress entries have no PipelineRun in cluster/ "
             f"(likely from a prior sim2real assemble): {orphans}. Skipping. "
             f"Remove the entries manually or via `deploy.py reset --only <key>` "
             f"if they should not return.")
```

**Sweep 5 (line 3198)** — in `_cmd_run_remote`, replace the comment:
```python
            # Mirror _cmd_run's init loop locally so the pre-flight validator
            # sees pair_keys that prepare.py added since the last run. The
            # in-cluster orchestrator independently does its own init from
            # the ConfigMap and persists; only `workload` and `package` need
            # to be populated here — those are the fields _apply_run_filters
            # reads when building valid_workloads / valid_packages (#414).
```
with:
```python
            # Mirror _cmd_run's init loop locally so the pre-flight validator
            # sees pair_keys that sim2real assemble added since the last run.
            # The in-cluster orchestrator independently does its own init from
            # the ConfigMap and persists; only `workload` and `package` need
            # to be populated here — those are the fields _apply_run_filters
            # reads when building valid_workloads / valid_packages (#414).
```

**Sweep 6 (line 3233)** — in `_cmd_run_remote`, replace:
```python
    except OSError as exc:
        err(f"{exc} — run setup.py and prepare.py first")
        sys.exit(1)
```
with:
```python
    except OSError as exc:
        err(f"{exc} — run setup.py and 'sim2real assemble --run {run_dir.name}' first")
        sys.exit(1)
```

- [ ] **Step 4: Run the test and verify it passes**

Run: `python -m pytest pipeline/tests/test_deploy_run.py -v -k "test_cmd_run_empty_cluster"`
Expected: PASS.

- [ ] **Step 5: Verify no `prepare.py` refs remain in `pipeline/deploy.py`**

Run: `grep -n "prepare\.py\|prepare first" pipeline/deploy.py`
Expected: empty output.

- [ ] **Step 6: Run the full pipeline test suite to catch regressions**

Run: `python -m pytest pipeline/tests/ -v`
Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add pipeline/deploy.py pipeline/tests/test_deploy_run.py
git commit -m "chore(deploy): sweep stale prepare.py refs from deploy.py (#446)

Replace prepare.py-era error strings and comments in _load_progress,
_cmd_run, and _cmd_run_remote with references to 'sim2real assemble'
(prepare.py was deleted by #453). Adopts the issue #446 acceptance-criterion
string 'run 'sim2real assemble --run <R>' first' for the empty-cluster
error in _cmd_run."
```

---

### Task 4: Update `pipeline/README.md`

**Files:**
- Modify: `pipeline/README.md` — deploy.py section.

**Interfaces:** Docs only.

**Behavior:** The README already references `--run NAME` and `sim2real assemble` in most places (grep confirmed). What needs adding: a note that `deploy.py run` errors out with the specific acceptance-criterion messages when the run isn't assembled. Nothing else structural changes.

- [ ] **Step 1: Read the current `deploy.py run` section**

Run: `grep -n "deploy\.py run\b" pipeline/README.md | head` to find anchor lines. Read the surrounding text (approximately line 245–260 based on the earlier grep).

- [ ] **Step 2: Add a "Prerequisites" note under the `deploy.py run` bullet**

Locate the line that reads (line 255 from grep):
```
**`deploy.py run`** — assigns `(workload, package)` pairs to free namespace slots, polls for completion, and retries pairs that time out. Reads progress from the run-scoped `sim2real-progress-{run}` ConfigMap to resume interrupted runs. Requires a configured namespace. Use `deploy.py collect` to pull results off-cluster after runs complete.
```

Append (as a NEW sentence at the end of that paragraph):

> The run's cluster is resolved from `workspace/runs/<R>/run_metadata.json:cluster_id`. If the run has not been assembled, `deploy.py run --run <R>` exits with `run 'sim2real assemble --run <R>' first`; if `run_metadata.json` is missing or malformed, it exits with `run metadata corrupted; re-assemble`.

- [ ] **Step 3: Verify README doesn't reintroduce any prepare.py mention**

Run: `grep -n "prepare\.py\|prepare first" pipeline/README.md`
Expected: empty output (was already empty per the plan pre-check).

- [ ] **Step 4: Commit**

```bash
git add pipeline/README.md
git commit -m "docs(README): describe deploy.py run's per-run cluster resolution (#446)

Document that deploy.py run reads cluster_id from
runs/<R>/run_metadata.json, and the two acceptance-criterion error strings
it emits when the run is not assembled or the metadata is corrupted."
```

---

### Task 5: Sweep for other stale references introduced or exposed by the change

**Files:**
- Grep-only across `.md`, `docs/`, `.claude/skills/`, `README*`. Modify any that surface stale claims.

**Interfaces:** N/A — sweep only.

- [ ] **Step 1: Grep for stale claims**

Run each of the following. Report every hit and classify as (stale — update) / (still accurate — leave) / (unrelated — leave):

```bash
grep -rn "prepare\.py" pipeline/ docs/ .claude/ CLAUDE.md README.md 2>/dev/null | grep -v ".pytest_cache" | grep -v "docs/superpowers/plans/2026-07-01"
grep -rn "Run directory not found" pipeline/ docs/ .claude/ CLAUDE.md README.md 2>/dev/null
grep -rn "No pairs found in cluster" pipeline/ docs/ .claude/ CLAUDE.md README.md 2>/dev/null | grep -v ".pytest_cache" | grep -v "docs/superpowers/plans/2026-07-01"
```

- [ ] **Step 2: For any stale hit, update it in-line**

If the file is code (`.py`), update the string / comment. If it's docs, update the wording.

- [ ] **Step 3: If any updates were made, commit them**

```bash
git add <paths>
git commit -m "chore: sweep stale prepare.py / 'Run directory not found' refs (#446)"
```

If no updates were made, no commit — skip Step 3.

---

### Task 6: Verification and push

**Files:** N/A.

**Interfaces:** N/A.

- [ ] **Step 1: Run the full test suite from repo root**

Run: `python -m pytest pipeline/ -v` (from the worktree root).
Expected: all tests pass. If any fail, stop and investigate — do NOT push.

- [ ] **Step 2: Run ruff**

Run: `ruff check pipeline/ --select F`
Expected: no output (clean).

- [ ] **Step 3: Confirm working-tree hygiene**

Run: `git status`
Expected: `nothing to commit, working tree clean`.

Run: `git -C /Users/kalantar/projects/go.workspace/src/github.com/inference-sim/sim2real status`
Expected: NO modifications to the parent repo's working tree (only untracked worktree metadata is acceptable).

- [ ] **Step 4: Push the branch**

Run: `git push -u origin refactor/v2-step-1-issue-446-deploy-run-per-run`

- [ ] **Step 5: Open the PR (base = refactor/v2-step-1)**

Use `gh pr create --base refactor/v2-step-1 --title "step-1 PR 3: deploy.py run --run R port + sweep stale prepare.py refs (#446)" --body-file <(...)`. Body outline:

- Ref: `Closes #446`.
- What changed (short): `deploy.py` now resolves the run's cluster from `runs/<R>/run_metadata.json:cluster_id` for all run-scoped subcommands. Emits the issue's acceptance-criterion error strings. Sweeps 6 stale `prepare.py` references.
- Why: `prepare.py` was deleted by #453; step-0 already ported `_cmd_run(args, run_dir, cluster_config)` to accept a `run_dir`, but the dispatcher still discovered the cluster via a global single-cluster assumption.
- What's NOT in this PR (explicit acceptance-criterion notes for the reviewer):
  - `_cmd_run` / `_cmd_run_remote` bodies otherwise unchanged; the "delete original `_cmd_run`" language in the issue was pre-empted by step-0.
  - Real-cluster gate against `kalantar-msb/sr` is NOT automatable from the harness — leave it for a manual run before merge.
- Sweep note: grepped for `prepare.py` / `Run directory not found` / `No pairs found in cluster` across `pipeline/`, `docs/`, `.claude/`, `README*`. Report each hit's disposition.

If `gh pr create` fails with `Resource not accessible by personal access token`, retry with `unset GITHUB_TOKEN GH_TOKEN; gh pr create ...`.

---

## Self-Review

**Spec coverage:**
- Missing `runs/<R>/` → assemble-hint string: Task 1 + Task 2.
- Missing `runs/<R>/cluster/` → assemble-hint string: Task 1 + Task 2 + Task 3 (also in `_cmd_run`'s belt-and-braces empty-cluster check).
- Missing `run_metadata.json` OR `cluster_id` absent → corrupt-hint string: Task 1 + Task 2.
- Original `_cmd_run` deleted (interpreted as "sweep prepare.py refs"): Task 3.
- `_is_pair_key` and `_load_pairs` unchanged: no task touches them; verify by grep during Task 6.
- `test_deploy_run.py` rewritten for new layout: Tasks 1, 2, 3 add coverage; existing tests preserved.
- README updated: Task 4.
- Real-cluster gate: called out as manual in Task 6 PR body.

**Placeholder scan:** none.

**Type consistency:** `_load_run_cluster_config(run_dir: Path) -> dict` is the only new signature and it's consistent across tasks. Existing `_cmd_*(args, run_dir, cluster_config)` signatures unchanged.
