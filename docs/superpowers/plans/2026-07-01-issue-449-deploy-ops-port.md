# step-1 PR 6 — deploy.py ops subcommands port + docs/CI sweep Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close step-1 by porting `deploy.py stop` to per-run cluster resolution (matching every other run-scoped subcommand), filling test-coverage gaps for the ops subcommands (`status`, `pairs`, `reset`, `wipe`), and adding the End-of-step-1 BYO demo section to `pipeline/README.md`.

**Architecture:** PR #455 (`_cmd_run`) and PR #456 (`_cmd_collect`) already ported every run-scoped subcommand except `stop` to `_load_run_cluster_config(run_dir)`. `stop` still uses the workspace-heuristic `_load_cluster_config()` because it was carved out of #455's scope. This PR completes the port for `stop`, adds dispatcher-parity tests for the four ops subcommands, and creates the missing `test_deploy_status.py`. No behavior changes for `pairs`, `reset`, `wipe`, or `run --remote` — those were already ported.

**Tech Stack:** Python 3.10+, pytest, argparse. Repo uses `.venv/` for local runs. Ruff is the linter.

## Global Constraints

- Base branch: `refactor/v2-step-1` (not `main`)
- Python >= 3.10; tests run under `.venv/bin/python -m pytest`
- Lint: `ruff check pipeline/ .claude/skills/ --select F` — must be clean
- Test invocation order: `deploy.py --run <R> <subcommand>` — `--run` is on the parent parser, not per-subcommand
- No `AskUserQuestion`; ask plain-text questions if any
- No emoji, no ceremony comments; comments only where the WHY is non-obvious
- Path discipline: every edited file's absolute path must contain `.claude/worktrees/issue-449-deploy-ops-port/` — do not leak edits into the parent repo

---

## File Structure

| File | Change | Reason |
|------|--------|--------|
| `pipeline/deploy.py` | Modify `main()` dispatch for `stop`; drop `_load_setup_config` guard for stop | Port stop to per-run cluster resolution |
| `pipeline/tests/test_deploy_stop.py` | Update three `test_main_*` tests to reflect new `--run` requirement | Match new dispatcher behavior |
| `pipeline/tests/test_deploy_status.py` | Create — dispatcher + per-run cluster resolution tests | Satisfy AC's explicit `test_deploy_status.py` requirement |
| `pipeline/tests/test_deploy_pairs.py` | Append `test_main_dispatches_pairs`  | Fill main() dispatcher gap |
| `pipeline/tests/test_deploy_reset.py` | Append `test_main_dispatches_reset` | Fill main() dispatcher gap |
| `pipeline/tests/test_deploy_wipe.py` | Append `test_main_dispatches_wipe`  | Fill main() dispatcher gap |
| `pipeline/README.md` | Add "End-of-step-1 BYO demo" section | Epic-closing artifact per AC |
| `.github/workflows/test.yml` | (verify no change needed) | `pipeline/` catch-all already covers new test file |
| `CLAUDE.md` | (verify no stale refs) | Sweep — no writes expected |

The design intentionally does NOT include:
- Behavior expansion of `stop` to cancel in-flight PipelineRuns. `reset` remains the PipelineRun-cancellation command per the current architecture; the epic's "no orchestrator cleanup" scope note (in `docs/epics/step-1/design.md:20-63`) covers this deferral. This will be called out in the PR body so the reviewer can grade it.
- Moving the existing 29 status-focused tests out of `test_deploy_run.py` into `test_deploy_status.py`. That would be a large mechanical move for no behavior gain; the AC only requires the file to exist and cover the new layout. Existing status tests already pass against the new layout via `_mock_cm`; the new file adds dispatcher-level coverage on top.

---

## Task 1 — Port `_cmd_stop` to per-run cluster resolution

**Files:**
- Modify: `pipeline/deploy.py:3434-3505` (`main()` dispatch)
- Modify: `pipeline/tests/test_deploy_stop.py:104-161` (three `test_main_*` tests)

**Interfaces:**
- Consumes: `_load_run_cluster_config(run_dir: Path) -> dict` — introduced by PR #455; reads `runs/<R>/run_metadata.json:cluster_id` and calls `cluster_ops.read_cluster_config(cluster_id)`. Same helper already used by every other run-scoped subcommand.
- Produces: no new symbols. `_cmd_stop(namespace: str)` signature unchanged.

- [ ] **Step 1: Write the failing test** — new `test_main_stop_uses_per_run_cluster` in `pipeline/tests/test_deploy_stop.py`. Assert `main()` for `stop` calls `_load_run_cluster_config(run_dir)` and passes the resolved primary namespace to `_cmd_stop`.

```python
def test_main_stop_uses_per_run_cluster(tmp_path, monkeypatch):
    """main() routes 'stop' via _load_run_cluster_config (per-run cluster resolution)."""
    monkeypatch.setattr("sys.argv", [
        "deploy.py", "--experiment-root", str(tmp_path), "--run", "trial-1", "stop",
    ])
    monkeypatch.setattr(mod, "EXPERIMENT_ROOT", tmp_path)

    stop_calls = []
    per_run_calls = []

    def mock_stop(namespace):
        stop_calls.append(namespace)

    def mock_per_run(run_dir):
        per_run_calls.append(run_dir)
        return {"namespaces": ["sim2real-per-run"]}

    with patch.object(mod, "_cmd_stop", mock_stop), \
         patch.object(mod, "_load_run_cluster_config", mock_per_run), \
         patch.object(mod, "_load_setup_config", return_value={}):
        mod.main()

    assert stop_calls == ["sim2real-per-run"]
    assert len(per_run_calls) == 1
    assert per_run_calls[0].name == "trial-1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest pipeline/tests/test_deploy_stop.py::test_main_stop_uses_per_run_cluster -v`
Expected: FAIL — assertion on `namespaces == ["sim2real-per-run"]` fails because current `main()` uses `_load_cluster_config()`, which the test does not patch, so the real function returns `{}` and `main()` exits before `_cmd_stop`.

- [ ] **Step 3: Update three existing `test_main_stop_*` tests to reflect new contract**

The three tests currently patch `_load_cluster_config`; they must patch `_load_run_cluster_config` after the port. Two also need a `--run` in argv since stop now requires a run context (test_main_dispatches_stop passes `current_run` via setup_config, which is the equivalent).

For `test_main_dispatches_stop` (pipeline/tests/test_deploy_stop.py:104-124):

```python
def test_main_dispatches_stop(tmp_path, monkeypatch):
    """main() routes 'stop' to _cmd_stop with the primary namespace."""
    monkeypatch.setattr("sys.argv", [
        "deploy.py", "--experiment-root", str(tmp_path), "stop",
    ])
    monkeypatch.setattr(mod, "EXPERIMENT_ROOT", tmp_path)

    stop_calls = []

    def mock_stop(namespace):
        stop_calls.append(namespace)

    with patch.object(mod, "_cmd_stop", mock_stop):
        with patch.object(mod, "_load_setup_config", return_value={
            "current_run": "test-run",
        }), patch.object(mod, "_load_run_cluster_config", return_value={
            "namespaces": ["sim2real-0", "sim2real-1"],
        }):
            mod.main()

    assert stop_calls == ["sim2real-0"]
```

For `test_main_stop_no_namespace_exits` (pipeline/tests/test_deploy_stop.py:127-139):

```python
def test_main_stop_no_namespace_exits(tmp_path, monkeypatch):
    """stop exits with code 1 when no namespaces configured for the run."""
    monkeypatch.setattr("sys.argv", [
        "deploy.py", "--experiment-root", str(tmp_path), "stop",
    ])
    monkeypatch.setattr(mod, "EXPERIMENT_ROOT", tmp_path)

    with patch.object(mod, "_load_setup_config", return_value={
        "current_run": "test-run",
    }), patch.object(mod, "_load_run_cluster_config", return_value={}):
        with pytest.raises(SystemExit) as exc_info:
            mod.main()
        assert exc_info.value.code == 1
```

For `test_main_stop_does_not_require_run_dir` (pipeline/tests/test_deploy_stop.py:142-161) — rename and re-purpose. The old test asserted that stop works without a run configured; the new contract requires either `--run` or `current_run`. Replace with:

```python
def test_main_stop_requires_run(tmp_path, monkeypatch, capsys):
    """stop exits with code 1 when neither --run nor current_run is set."""
    monkeypatch.setattr("sys.argv", [
        "deploy.py", "--experiment-root", str(tmp_path), "stop",
    ])
    monkeypatch.setattr(mod, "EXPERIMENT_ROOT", tmp_path)

    with patch.object(mod, "_load_setup_config", return_value={}):
        with pytest.raises(SystemExit) as exc_info:
            mod.main()
        assert exc_info.value.code == 1

    err = capsys.readouterr().err
    assert "No run name" in err
```

- [ ] **Step 4: Implement — port `stop` dispatch in `main()`**

Change `pipeline/deploy.py:3453-3460` from:

```python
    if cmd == "stop":
        cluster_config = _load_cluster_config()
        namespaces = [ns for ns in (cluster_config.get("namespaces") or []) if ns]
        if not namespaces:
            err("No namespaces configured. Run cluster.py provision with --namespaces.")
            sys.exit(1)
        _cmd_stop(namespace=namespaces[0])
        return
```

to remove that block entirely, then add stop dispatch after `cluster_config = _load_run_cluster_config(run_dir)` alongside the other run-scoped subcommands. Concretely, the new block replaces `if cmd == "stop": ... return` and adds a `elif cmd == "stop":` inside the existing subcommand elif chain:

```python
    elif cmd == "stop":
        namespaces = [ns for ns in (cluster_config.get("namespaces") or []) if ns]
        if not namespaces:
            err("No namespaces configured. Run cluster.py provision with --namespaces.")
            sys.exit(1)
        _cmd_stop(namespace=namespaces[0])
```

Result: `stop` now flows through the same `run_name → run_dir → _load_run_cluster_config(run_dir)` path as `build`, `run`, `status`, `collect`, `reset`, `wipe`, `pairs`. Consequences:
- `stop` now requires `--run` or `current_run` set (matches every other subcommand)
- `stop` uses the run's cluster (safe for multi-cluster workspaces)

- [ ] **Step 5: Run all deploy_stop tests**

Run: `.venv/bin/python -m pytest pipeline/tests/test_deploy_stop.py -v`
Expected: PASS — including the new `test_main_stop_uses_per_run_cluster` and the three updated tests. 5 tests unchanged (`_cmd_stop` unit tests).

- [ ] **Step 6: Run full pipeline suite (regression check)**

Run: `.venv/bin/python -m pytest pipeline/ -v 2>&1 | tail -5`
Expected: PASS — 1144 → 1145 passing (net +1 from the new test), 2 xfailed. No new failures from other subcommand tests since none of them exercised the `stop` dispatch path.

- [ ] **Step 7: Commit**

```bash
git add pipeline/deploy.py pipeline/tests/test_deploy_stop.py
git commit -m "$(cat <<'EOF'
feat(deploy): port stop to per-run cluster resolution (#449)

Route deploy.py stop through the same _load_run_cluster_config(run_dir)
path every other run-scoped subcommand uses. PR #455 explicitly left
this port for the ops-subcommand PR; this closes that gap.

Consequences:
- stop now requires --run or current_run set (matches every other
  subcommand)
- stop uses the run's cluster (safe for multi-cluster workspaces)

_cmd_stop's signature (namespace: str) is unchanged; only the dispatcher
wiring moves.
EOF
)"
```

---

## Task 2 — Create `test_deploy_status.py` with dispatcher tests

**Files:**
- Create: `pipeline/tests/test_deploy_status.py`

**Interfaces:**
- Consumes: `pipeline.deploy.main`, `pipeline.deploy._cmd_status`, `pipeline.lib.progress.ConfigMapProgressStore`
- Produces: no new symbols. Adds 3 tests focused on the AC's `deploy.py status --run trial-1` shape.

**Rationale:** Existing per-run status behavior is covered by ~29 tests in `test_deploy_run.py:30-576` and 3 tests in `test_deploy_standalone.py:59-111`. This new file satisfies the AC's literal `pipeline/tests/test_deploy_status.py` requirement and adds a run-scoped dispatcher test. The existing tests stay where they are — a mechanical move would inflate diff size for no behavior gain.

- [ ] **Step 1: Write the new file**

```python
"""Tests for deploy.py status subcommand dispatch and per-run scoping (issue #449).

Existing per-run status behavior lives in test_deploy_run.py:30-576 (29
tests exercising _cmd_status directly) and test_deploy_standalone.py:59-111
(3 tests exercising main() dispatch with default status). This file adds
the run-scoped dispatcher tests explicitly enumerated in issue #449's
acceptance criteria for the deploy.py status subcommand.
"""

import json
from unittest.mock import patch

import pipeline.deploy as mod
from pipeline.lib.progress import ConfigMapProgressStore


def test_main_status_reads_from_per_run_cluster(tmp_path, monkeypatch):
    """main() dispatches 'status' with the per-run cluster_config (#449).

    Verifies the AC: deploy.py status --run trial-1 reads runs/trial-1/
    cluster/ context and the per-run ConfigMap. This test asserts that
    _load_run_cluster_config receives run_dir with name 'trial-1' and
    that _cmd_status receives the resolved run_dir + cluster_config
    verbatim.
    """
    monkeypatch.setattr("sys.argv", [
        "deploy.py", "--experiment-root", str(tmp_path),
        "--run", "trial-1", "status",
    ])
    monkeypatch.setattr(mod, "EXPERIMENT_ROOT", tmp_path)

    status_calls = []
    per_run_calls = []

    def mock_status(args, run_dir, cluster_config=None):
        status_calls.append((run_dir, cluster_config))

    def mock_per_run(run_dir):
        per_run_calls.append(run_dir)
        return {"namespaces": ["sim2real-per-run"]}

    with patch.object(mod, "_cmd_status", mock_status), \
         patch.object(mod, "_load_run_cluster_config", mock_per_run), \
         patch.object(mod, "_load_setup_config", return_value={}):
        mod.main()

    assert len(status_calls) == 1
    run_dir, cluster_config = status_calls[0]
    assert run_dir.name == "trial-1"
    assert cluster_config == {"namespaces": ["sim2real-per-run"]}
    assert len(per_run_calls) == 1
    assert per_run_calls[0].name == "trial-1"


def test_main_status_uses_current_run_when_no_flag(tmp_path, monkeypatch):
    """Omitting --run falls back to current_run from setup_config (#449)."""
    monkeypatch.setattr("sys.argv", [
        "deploy.py", "--experiment-root", str(tmp_path), "status",
    ])
    monkeypatch.setattr(mod, "EXPERIMENT_ROOT", tmp_path)

    status_calls = []

    def mock_status(args, run_dir, cluster_config=None):
        status_calls.append(run_dir)

    with patch.object(mod, "_cmd_status", mock_status), \
         patch.object(mod, "_load_run_cluster_config",
                      return_value={"namespaces": ["ns-0"]}), \
         patch.object(mod, "_load_setup_config",
                      return_value={"current_run": "default-run"}):
        mod.main()

    assert status_calls[0].name == "default-run"


def test_cmd_status_reads_run_scoped_configmap(tmp_path, monkeypatch, capsys):
    """_cmd_status constructs a ConfigMapProgressStore keyed by run_dir.name (#449).

    The store's run_name argument controls which ConfigMap the subcommand
    reads (sim2real-progress-<R>). Assert the store is built with the run
    directory's basename so status snapshots stay scoped to that run.
    """
    from pipeline.deploy import _cmd_status

    run_dir = tmp_path / "workspace" / "runs" / "trial-1"
    run_dir.mkdir(parents=True)

    store_kwargs = []

    def _fake_store_init(self, namespace, *, run_name=""):
        store_kwargs.append({"namespace": namespace, "run_name": run_name})
        self._data = {}

    def _fake_load(self):
        return {}

    monkeypatch.setattr(ConfigMapProgressStore, "__init__", _fake_store_init)
    monkeypatch.setattr(ConfigMapProgressStore, "load", _fake_load)

    class _Args:
        only = None
        workload = None
        package = None
        status = None
        silent = False

    _cmd_status(_Args(), run_dir, cluster_config={"namespaces": ["sim2real-ns"]})

    assert len(store_kwargs) == 1
    assert store_kwargs[0]["run_name"] == "trial-1"
    assert store_kwargs[0]["namespace"] == "sim2real-ns"
```

- [ ] **Step 2: Run the new file**

Run: `.venv/bin/python -m pytest pipeline/tests/test_deploy_status.py -v`
Expected: PASS — 3 tests.

- [ ] **Step 3: Commit**

```bash
git add pipeline/tests/test_deploy_status.py
git commit -m "$(cat <<'EOF'
test(deploy): add test_deploy_status.py with per-run dispatcher coverage (#449)

Satisfies the AC's literal requirement for a test_deploy_status.py file
and closes the main() dispatcher gap for the status subcommand. Existing
_cmd_status behavior tests stay in test_deploy_run.py (29 tests) and
test_deploy_standalone.py (3 tests) — this file adds run-scoped
dispatcher coverage on top.

Three tests:
- test_main_status_reads_from_per_run_cluster — asserts --run trial-1
  wires through _load_run_cluster_config to _cmd_status
- test_main_status_uses_current_run_when_no_flag — asserts current_run
  fallback still works
- test_cmd_status_reads_run_scoped_configmap — asserts the
  ConfigMapProgressStore is keyed by run_dir.name (sim2real-progress-<R>)
EOF
)"
```

---

## Task 3 — Add main() dispatcher tests for `pairs`, `reset`, `wipe`

**Files:**
- Modify: `pipeline/tests/test_deploy_pairs.py` (append 1 test)
- Modify: `pipeline/tests/test_deploy_reset.py` (append 1 test)
- Modify: `pipeline/tests/test_deploy_wipe.py` (append 1 test)

**Interfaces:**
- Consumes: `pipeline.deploy.main`, `_cmd_pairs`, `_cmd_reset`, `_cmd_wipe`, `_load_run_cluster_config`
- Produces: no new symbols. Three tests filling the AC dispatcher gap.

Each test asserts:
1. `main()` calls `_load_run_cluster_config` with the correct `run_dir` (whose `.name` is `"trial-1"`)
2. `main()` calls `_cmd_<X>` with the resolved `run_dir` and any cluster context passed through

- [ ] **Step 1: Append `test_main_dispatches_pairs` to `pipeline/tests/test_deploy_pairs.py`**

```python
def test_main_dispatches_pairs(tmp_path, monkeypatch):
    """main() routes 'pairs' with the per-run cluster_dir (#449)."""
    from unittest.mock import patch
    import pipeline.deploy as mod

    monkeypatch.setattr("sys.argv", [
        "deploy.py", "--experiment-root", str(tmp_path),
        "--run", "trial-1", "pairs",
    ])
    monkeypatch.setattr(mod, "EXPERIMENT_ROOT", tmp_path)

    pairs_calls = []

    def mock_pairs(cluster_dir, *, keys_only=False,
                  workloads_only=False, packages_only=False):
        pairs_calls.append(cluster_dir)

    with patch.object(mod, "_cmd_pairs", mock_pairs), \
         patch.object(mod, "_load_run_cluster_config",
                      return_value={"namespaces": ["ns-0"]}), \
         patch.object(mod, "_load_setup_config", return_value={}):
        mod.main()

    assert len(pairs_calls) == 1
    assert pairs_calls[0].name == "cluster"
    assert pairs_calls[0].parent.name == "trial-1"
```

- [ ] **Step 2: Append `test_main_dispatches_reset` to `pipeline/tests/test_deploy_reset.py`**

```python
def test_main_dispatches_reset(tmp_path, monkeypatch):
    """main() routes 'reset' with the per-run cluster_config (#449)."""
    from unittest.mock import patch
    import pipeline.deploy as mod

    (tmp_path / "workspace" / "runs" / "trial-1" / "cluster").mkdir(parents=True)

    monkeypatch.setattr("sys.argv", [
        "deploy.py", "--experiment-root", str(tmp_path),
        "--run", "trial-1", "reset",
    ])
    monkeypatch.setattr(mod, "EXPERIMENT_ROOT", tmp_path)

    reset_calls = []

    def mock_reset(args, run_dir, discovered, *,
                   namespaces=None, cluster_config=None):
        reset_calls.append((run_dir, cluster_config))

    with patch.object(mod, "_cmd_reset", mock_reset), \
         patch.object(mod, "_load_run_cluster_config",
                      return_value={"namespaces": ["ns-0"]}), \
         patch.object(mod, "_load_setup_config", return_value={}), \
         patch.object(mod, "_load_pairs", return_value={}):
        mod.main()

    assert len(reset_calls) == 1
    run_dir, cluster_config = reset_calls[0]
    assert run_dir.name == "trial-1"
    assert cluster_config == {"namespaces": ["ns-0"]}
```

- [ ] **Step 3: Append `test_main_dispatches_wipe` to `pipeline/tests/test_deploy_wipe.py`**

```python
def test_main_dispatches_wipe(tmp_path, monkeypatch):
    """main() routes 'wipe' with the per-run cluster_config (#449)."""
    from unittest.mock import patch
    import pipeline.deploy as mod

    monkeypatch.setattr("sys.argv", [
        "deploy.py", "--experiment-root", str(tmp_path),
        "--run", "trial-1", "wipe", "--yes",
    ])
    monkeypatch.setattr(mod, "EXPERIMENT_ROOT", tmp_path)

    wipe_calls = []

    def mock_wipe(args, run_dir, *, cluster_config=None):
        wipe_calls.append((run_dir, cluster_config))

    with patch.object(mod, "_cmd_wipe", mock_wipe), \
         patch.object(mod, "_load_run_cluster_config",
                      return_value={"namespaces": ["ns-0"]}), \
         patch.object(mod, "_load_setup_config", return_value={}):
        mod.main()

    assert len(wipe_calls) == 1
    run_dir, cluster_config = wipe_calls[0]
    assert run_dir.name == "trial-1"
    assert cluster_config == {"namespaces": ["ns-0"]}
```

- [ ] **Step 4: Run the three modified test files**

Run: `.venv/bin/python -m pytest pipeline/tests/test_deploy_pairs.py pipeline/tests/test_deploy_reset.py pipeline/tests/test_deploy_wipe.py -v 2>&1 | tail -15`
Expected: PASS — one new test in each file.

- [ ] **Step 5: Commit**

```bash
git add pipeline/tests/test_deploy_pairs.py pipeline/tests/test_deploy_reset.py pipeline/tests/test_deploy_wipe.py
git commit -m "$(cat <<'EOF'
test(deploy): add main() dispatcher tests for pairs/reset/wipe (#449)

Fills the main() dispatcher gap for the three ops subcommands that
already had _cmd_<X> tests but no main() → per-run cluster resolution →
_cmd_<X> path coverage.

Mirrors the test_main_dispatches_stop pattern in test_deploy_stop.py.
Each new test asserts:
- _load_run_cluster_config is called with the correct run_dir
- _cmd_<X> receives the resolved run_dir + cluster_config verbatim
EOF
)"
```

---

## Task 4 — Add "End-of-step-1 BYO demo" section to `pipeline/README.md`

**Files:**
- Modify: `pipeline/README.md` (append section between "Manage runs" at :353-368 and "Pipeline library" at :372)

**Interfaces:** documentation only.

**Rationale:** The AC explicitly requires this section. The full BYO flow (register → assemble → run → collect) is the epic's success criterion. This section documents it verbatim so an operator (or the next step-2 implementer) can reproduce the demo.

- [ ] **Step 1: Draft and insert the new section**

Between the last line of "Manage runs" (line 368) and the `---` before "Pipeline library" (line 372 area), add:

```markdown

---

## End-of-step-1 BYO demo

The full BYO flow. Every step is idempotent and re-runnable. Substitute
your own values for `<cluster_id>`, `<run-name>`, `<algorithm>`,
`<image-ref>`, and `<treatment-overlay-path>`.

### Prerequisites

- An experiment repo with `transfer.yaml`, `baselines/<name>.yaml`, and
  the workloads referenced in `transfer.yaml:workloads`.
- A cluster with kubectl / Tekton reachable.
- Registry credentials (HF, image registry) exported or provided via
  flags.

### 1. Provision the cluster (one-time)

```bash
python pipeline/cluster.py provision <cluster_id> --namespaces sim2real-0,sim2real-1
```

Writes `workspace/clusters/<cluster_id>/cluster_config.json`.
Idempotent — re-run when adding namespace slots.

### 2. Configure the workspace (one-time per experiment repo)

```bash
python pipeline/setup.py --experiment-root <experiment-root>
```

Writes `workspace/setup_config.json` with registry, orchestrator image,
and `current_run` defaults.

### 3. Register a translation (BYO)

```bash
python pipeline/sim2real.py translation register \
    --algorithm <algorithm> \
    --image <image-ref> \
    --config <treatment-overlay-path> \
    --experiment-root <experiment-root>
```

Writes `workspace/translations/<hash>/` with `translation_output.json`,
`registered.json`, and `generated/<algorithm>/<algorithm>_config.yaml`.
Prints the `<hash>` on success.

### 4. Assemble the run

```bash
python pipeline/sim2real.py assemble \
    --translation <hash> \
    --cluster <cluster_id> \
    --run <run-name> \
    --experiment-root <experiment-root>
```

Writes `workspace/runs/<run-name>/` with resolved scenario YAMLs,
`pipelinerun-*.yaml` manifests, `manifest.assembly.yaml`, and
`run_metadata.json`.

### 5. Deploy (orchestrate PipelineRuns)

```bash
python pipeline/deploy.py --experiment-root <experiment-root> \
    --run <run-name> run
```

Builds the treatment EPP image (if not current), dispatches PipelineRuns
across the namespace slots, and polls for completion. Progress lands in
the run-scoped `sim2real-progress-<run-name>` ConfigMap. Use
`deploy.py --run <run-name> status` to snapshot progress; use
`deploy.py --run <run-name> stop` to cancel the remote orchestrator Job
(if `--remote` was passed); use `deploy.py --run <run-name> reset` to
requeue non-pending pairs.

### 6. Collect results

```bash
python pipeline/deploy.py --experiment-root <experiment-root> \
    --run <run-name> collect
```

Pulls per-pair `per_request_lifecycle_metrics.json` and GPU logs from
the cluster PVC into `workspace/runs/<run-name>/results/<phase>/<workload>/`.
This is the epic's success gate — the demo is done when the JSON files
exist locally.

### Success criterion

For each `<workload>` in `transfer.yaml:workloads` and each `<phase>` in
`{baseline, <algorithm>}`:

```
workspace/runs/<run-name>/results/<phase>/<workload>/per_request_lifecycle_metrics.json
```

Once these files exist, step-1's BYO demo is complete. Downstream skills
(e.g. `/sim2real-analyze`) consume them for latency comparison and
report generation.
```

- [ ] **Step 2: Verify markdown structure**

Run: `grep -n "^## " pipeline/README.md 2>&1 | head -20`
Expected output includes both "Manage runs" and the new "End-of-step-1 BYO demo" section headings in order.

- [ ] **Step 3: Commit**

```bash
git add pipeline/README.md
git commit -m "$(cat <<'EOF'
docs(pipeline): add End-of-step-1 BYO demo section (#449)

The full BYO flow (register → assemble → run → collect) documented as a
single copy-pasteable sequence with a clear success criterion. This is
the epic's closing artifact — an operator (or the next step-2
implementer) can run through the whole flow from these instructions
alone.
EOF
)"
```

---

## Task 5 — Verify no stale references and no CI-workflow updates needed

**Files:**
- Read: `CLAUDE.md`
- Read: `pipeline/README.md`
- Read: `.github/workflows/test.yml`
- Read: `.claude/skills/` (any references to deleted modules)

**Interfaces:** documentation sweep only.

**Rationale:** The AC requires "no stale references to `prepare.py`, `state_machine.py`, `context_builder.py`, legacy `assemble.py`, or `run.py` in `CLAUDE.md` or `pipeline/README.md`". Prior PRs (#453 for `prepare.py`, #454 for `run.py`, #455 for the `prepare.py` sweep) cleaned most of these. This task verifies nothing regressed and notes any historical/frozen references (design docs, plan artifacts) that intentionally stay.

- [ ] **Step 1: Grep the two AC-mentioned files**

Run:
```bash
grep -nE "prepare\.py|state_machine\.py|context_builder\.py|(^|[^_])pipeline/lib/assemble\.py|run\.py" CLAUDE.md pipeline/README.md 2>&1
```
Expected: no output — the two files are already clean. If any output appears, remove the reference in this task.

- [ ] **Step 2: Verify test.yml paths cover all pipeline tests**

Run: `cat .github/workflows/test.yml`
Expected: `python -m pytest pipeline/ ...` at the top of the pytest invocation. `pipeline/` catches all files under `pipeline/tests/`, so `test_deploy_status.py` needs no explicit listing. No change to the workflow file.

- [ ] **Step 3: Grep `.claude/skills/` for the same terms — read-only**

Run:
```bash
grep -rnE "prepare\.py|state_machine\.py|context_builder\.py" .claude/skills/ 2>&1
```
Expected: two hits in `.claude/skills/sim2real-translate/prompts/agent-{writer,reviewer}.md`. These are intentional per PR #455's sweep notes — the skill is disabled for step-1 and will be restored (with updated prompts) in step-2. Do NOT touch them in this PR.

- [ ] **Step 4: Grep `docs/` — read-only**

Run:
```bash
grep -rnE "prepare\.py|state_machine\.py|context_builder\.py|(^|[^_])pipeline/lib/assemble\.py" docs/ 2>&1 | grep -v "docs/superpowers/plans/" | grep -v "docs/epics/" | head
```
Expected: no output outside `docs/superpowers/plans/` (frozen plan artifacts) and `docs/epics/` (design docs, historical records). If anything else surfaces, evaluate and fix.

- [ ] **Step 5: No commit — this is a verification-only task**

Nothing to commit if the sweep passes clean. The verification output is recorded in the PR body.

---

## Task 6 — Run the full test suite and lint gate

**Files:** none.

- [ ] **Step 1: Run the full pipeline suite**

Run: `.venv/bin/python -m pytest pipeline/ -v 2>&1 | tail -10`
Expected: 1144 (baseline) + new tests → 1150 (5 new tests added: 1 status file × 3 tests + 3 dispatcher tests) → 1149 passing + 1 renamed test in test_deploy_stop.py, 2 xfailed. Adjust the count expectation once actual counts are known during execution.

Concretely: after Tasks 1-3 land, expect:
- Task 1 net: +1 test (test_main_stop_uses_per_run_cluster added; three existing tests updated but count unchanged)
- Task 2 net: +3 tests (new test_deploy_status.py)
- Task 3 net: +3 tests (one per pairs/reset/wipe file)
- Total: +7 tests. Final count: 1144 + 7 = 1151 passed, 2 xfailed.

- [ ] **Step 2: Run the skill test suites**

Run: `.venv/bin/python -m pytest .claude/skills/sim2real-analyze/tests/ .claude/skills/sim2real-bootstrap/tests/ .claude/skills/sim2real-translate/tests/ -v 2>&1 | tail -5`
Expected: PASS — no changes to any of these paths.

- [ ] **Step 3: Run the linter**

Run: `.venv/bin/ruff check pipeline/ .claude/skills/ --select F 2>&1 | tail -5`
Expected: `All checks passed!`

- [ ] **Step 4: No commit — verification-only**

---

## Task 7 — Push and open the PR

**Files:** none (git-only).

- [ ] **Step 1: Push the branch**

```bash
git push -u origin refactor/v2-step-1-issue-449-deploy-ops-port
```

- [ ] **Step 2: Open PR against `refactor/v2-step-1`**

```bash
gh pr create --base refactor/v2-step-1 \
    --title "step-1 PR 6: deploy.py ops subcommands port + docs/CI sweep (#449)" \
    --body-file <PR body markdown>
```

Body includes:
- `Closes #449.`
- Summary: what changed, why, and the "no orchestrator cleanup" scope note for `stop`
- Files changed (short list)
- Acceptance criteria mapping with `[x]` / `[ ]` per item — flag the `stop` PipelineRun-cancellation criterion as consciously deferred per epic scope
- Sweep notes (what was grepped, what was left alone)
- Verification results (test counts, lint clean)
- Non-obvious design choices worth reviewer attention

If `gh pr create` fails with a token error, retry once with `unset GITHUB_TOKEN GH_TOKEN`.

---

## Self-review

### Spec coverage against AC

| AC item | Task | Notes |
|---------|------|-------|
| `deploy.py status --run trial-1` reports per-pair status from ConfigMap + `runs/<R>/cluster/` | Task 2 | Behavior pre-existing (PR #455); Task 2 adds explicit dispatcher tests. |
| `deploy.py pairs --run trial-1` lists pairs from `runs/<R>/cluster/` | Task 3 | Behavior pre-existing (PR #455); Task 3 adds dispatcher test. |
| `deploy.py reset --run trial-1` resets progress state | Task 3 | Behavior pre-existing (PR #455); Task 3 adds dispatcher test. |
| `deploy.py wipe --run trial-1` deletes `runs/<R>/results/` | Task 3 | Behavior pre-existing (PR #455); Task 3 adds dispatcher test. |
| `deploy.py stop --run trial-1` cancels in-flight PipelineRuns | Task 1 (partial) | Port completed for cluster resolution. PipelineRun cancellation intentionally deferred — that behavior overlaps with `reset` and expanding stop is beyond the epic's "no orchestrator cleanup" scope. Called out in PR body. |
| `deploy.py run-remote --run trial-1` still works | (no task — verified) | Already covered by existing `test_deploy_remote.py` (75 tests). |
| `test_deploy_{status,pairs,reset,wipe,stop,remote}.py` rewritten | Tasks 1-3 | `test_deploy_status.py` created; others updated/appended. |
| Any deploy.py helpers only servicing the old layout are deleted | (no task — verified) | No stale helpers found. `_load_setup_config`, `_load_cluster_config`, `_load_run_cluster_config` all actively used. Called out in PR body. |
| `pipeline/README.md` has End-of-step-1 BYO demo section | Task 4 | New section added. |
| No stale refs to old modules in CLAUDE.md or pipeline/README.md | Task 5 | Already clean; verified by grep. |
| `.github/workflows/test.yml` covers all new modules | Task 5 | `pipeline/` catch-all covers new test file. |

### Placeholder scan

- No "TBD", "TODO", or "implement later" outside of quoted historical context.
- No "similar to Task N" without repeating the code.
- All test code and diff blocks are complete.

### Type consistency

- `_cmd_stop(namespace: str)` signature unchanged across all references.
- `_load_run_cluster_config(run_dir: Path) -> dict` used consistently.
- `run_dir.name == "trial-1"` used consistently across dispatcher tests.

### Open items (surface to user before implementation)

1. `stop`'s "cancel in-flight PipelineRuns" AC item is being consciously deferred. If the reviewer disagrees, add a follow-up task that (a) enumerates in-flight PipelineRuns for the run via the ConfigMap, (b) `kubectl delete pipelinerun`s each one in the primary namespace, (c) then does the current Job-stop. Would add ~30 lines to `_cmd_stop` and 3-4 tests. Prefer to defer.
2. Existing 29 status tests remain in `test_deploy_run.py`. The AC literal reading (`test_deploy_status.py` rewritten) is satisfied by creating the file with dispatcher-focused tests, matching PR #456's minimal-invasion pattern for `test_deploy_collect.py`.
