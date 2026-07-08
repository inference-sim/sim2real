# Issue #511 Implementation Plan: `pipeline.yaml` threading + `_cmd_run` dispatch + PipelineRun name validator

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Thread a `replica` PipelineRun param through `pipeline.yaml` so each iteration's results land under `runs/<R>/results/<phase>/<workload>/i<N>/`, and add a fail-fast PipelineRun name-length validator.

**Architecture:** Bake the `replica` param into each PipelineRun YAML at assemble time (via `make_pipelinerun_scenario`), rather than injecting at dispatch. Each iteration already has its own pre-generated PR YAML file (from #521), so passing `replica` as a static param in that file is the natural fit — dispatch stays a simple `kubectl apply` of the pre-generated file. Add a `build_results_dir(...)` helper as the single source of truth for the resultsDir template contract, used by `pipeline.yaml`'s test to assert every `resultsDir` value matches. Add `validate_pipelinerun_name(...)` called from `make_pipelinerun_scenario` so an over-long name fails at construction time (assemble), not at Tekton dispatch.

**Tech Stack:** Python 3.10+, PyYAML, pytest, Tekton v1 API (string params).

## Global Constraints

- **Tekton param type**: `replica` is `type: string` with `default: "1"` — Tekton has no int type. Value must be emitted as a Python `str` in the PipelineRun params list.
- **k8s DNS-label limit**: PipelineRun `metadata.name` must be ≤253 chars (RFC 1123 DNS subdomain). Enforce at construction; do not defer to Tekton rejection.
- **Legacy compat**: `iteration=1` (default) must produce identical PR YAML output shape as before (aside from the new `replica: "1"` param), so single-replica flows continue to work.
- **CI paths listed in `.github/workflows/test.yml`**: any new test file must be added there — CI only covers explicitly listed paths.
- **Path discipline**: every file operation targets absolute paths under `.claude/worktrees/issue-511-pipeline-yaml-replica-threading/`.

## Design decisions locked in

1. **`replica` param injected at assemble time** (in `make_pipelinerun_scenario`), not at dispatch. Rationale: each iteration has its own PR YAML file, `replica` is static per file (unlike `namespace`, which is dynamic per slot). Baking it in keeps dispatch (`_cmd_run`) untouched for the replica concern. Acceptance criterion "replica dispatched as a string, not an int (Tekton API compat)" is trivially met — the YAML emits `"1"`.
2. **`plans_dir` (line 97 of pipeline.yaml) stays per-workload, not per-iteration.** Rationale: `plans_dir` is llm-d-benchmark input (workload plans), not benchmark results output. Iterations reuse the same plans; no need to duplicate.
3. **`_cmd_wipe` / `_cmd_collect` local path handling stays as-is.** Rationale: `_cmd_collect` mirrors the PVC tree transparently — new `iN/` layer just appears in the destination. `_cmd_wipe` deletes the whole `(pkg, wl)` subtree, which still works (removes all iN/ under it). Per-iteration wipe is a UX concern deferred to a follow-up (called out in the PR body).

---

## File Structure

| File | Change |
|------|--------|
| `pipeline/pipeline.yaml` | Add `replica` pipeline param (string, default `"1"`). Update all 5 `resultsDir` values from `<run>/<phase>/<wl>` to `<run>/<phase>/<wl>/i$(params.replica)`. |
| `pipeline/lib/tekton.py` | Add `RESULTS_DIR_TEMPLATE` constant, `build_results_dir(run, phase, workload, replica)` helper, `validate_pipelinerun_name(name)` validator. Update `make_pipelinerun_scenario` to emit `replica` param and call the validator. |
| `pipeline/tests/test_tekton.py` | New tests for `build_results_dir`, `validate_pipelinerun_name`, and the `replica` param on PR output. |
| `pipeline/tests/test_pipeline_yaml.py` | Extend existing test module with a class asserting every `resultsDir` in `pipeline.yaml` matches `build_results_dir` output for `replica=$(params.replica)`, and the pipeline declares the `replica` param. |

No new test files — extend existing ones. Both test modules are already in CI's whitelist (`test_tekton.py` is not currently in the CI list but the umbrella `pipeline/` covers it; verify). Actually the CI runs `python -m pytest pipeline/ ...` with `pipeline/` first — so the whole `pipeline/tests/` directory is covered by that base run. No CI workflow update needed.

---

### Task 1: Add `build_results_dir` helper + `RESULTS_DIR_TEMPLATE` constant to `tekton.py`

**Files:**
- Modify: `pipeline/lib/tekton.py` (add module-level constants + helper below `_TASK_TIMEOUTS`)
- Test: `pipeline/tests/test_tekton.py` (append new tests)

**Interfaces:**
- Produces: `RESULTS_DIR_TEMPLATE = "$(params.runName)/$(params.phase)/$(params.workloadName)/i$(params.replica)"` — canonical resultsDir shape for pipeline.yaml. Consumed by pipeline_yaml tests.
- Produces: `build_results_dir(run: str, phase: str, workload: str, replica: str | int) -> str` — canonical path builder for callers with concrete values. Returns `f"{run}/{phase}/{workload}/i{replica}"`.

- [ ] **Step 1: Write the failing tests**

Append to `pipeline/tests/test_tekton.py`:

```python
# ── Tests for build_results_dir + RESULTS_DIR_TEMPLATE ──────────────────────

from pipeline.lib.tekton import build_results_dir, RESULTS_DIR_TEMPLATE


def test_build_results_dir_returns_slash_joined_path():
    assert build_results_dir("run1", "baseline", "chatbot-mid", 1) == "run1/baseline/chatbot-mid/i1"


def test_build_results_dir_accepts_int_replica():
    assert build_results_dir("r", "p", "w", 3) == "r/p/w/i3"


def test_build_results_dir_accepts_str_replica():
    """String replica must produce the same output — used for pipeline.yaml templating."""
    assert build_results_dir("r", "p", "w", "3") == "r/p/w/i3"


def test_results_dir_template_shape():
    """The pipeline.yaml template must be exactly the shape build_results_dir
    would produce if each segment were a Tekton param reference."""
    assert RESULTS_DIR_TEMPLATE == "$(params.runName)/$(params.phase)/$(params.workloadName)/i$(params.replica)"


def test_build_results_dir_matches_template_when_params_substituted():
    """build_results_dir with Tekton-param strings reproduces the template
    verbatim. This is the invariant test_pipeline_yaml.py will assert against
    every resultsDir value in pipeline.yaml."""
    template = build_results_dir(
        "$(params.runName)", "$(params.phase)",
        "$(params.workloadName)", "$(params.replica)",
    )
    assert template == RESULTS_DIR_TEMPLATE
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest pipeline/tests/test_tekton.py -v -k "results_dir or template"`
Expected: FAIL — `ImportError` on `build_results_dir` / `RESULTS_DIR_TEMPLATE`.

- [ ] **Step 3: Implement**

In `pipeline/lib/tekton.py`, below the `_TASK_TIMEOUTS` dict (around line 15):

```python
# Canonical shape of resultsDir in pipeline.yaml. Every task that writes into
# resultsDir threads this exact template. build_results_dir() renders it with
# concrete values for callers that construct the path locally (e.g. tests).
# Kept in one place so pipeline.yaml drift is caught by test_pipeline_yaml.py.
RESULTS_DIR_TEMPLATE = (
    "$(params.runName)/$(params.phase)/$(params.workloadName)/i$(params.replica)"
)


def build_results_dir(run: str, phase: str, workload: str, replica) -> str:
    """Return the canonical resultsDir path for a (run, phase, workload, replica)
    tuple. Callers supply either concrete strings/ints or Tekton param
    references — both round-trip through the same template.
    """
    return f"{run}/{phase}/{workload}/i{replica}"
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest pipeline/tests/test_tekton.py -v -k "results_dir or template"`
Expected: PASS (all four new tests green).

- [ ] **Step 5: Commit**

```bash
git add pipeline/lib/tekton.py pipeline/tests/test_tekton.py
git commit -m "tekton: add build_results_dir helper + RESULTS_DIR_TEMPLATE"
```

---

### Task 2: Add `validate_pipelinerun_name` validator + call it in `make_pipelinerun_scenario`

**Files:**
- Modify: `pipeline/lib/tekton.py`
- Test: `pipeline/tests/test_tekton.py` (append)

**Interfaces:**
- Consumes: nothing from prior tasks (independent).
- Produces: `validate_pipelinerun_name(name: str) -> None` — raises `ValueError` if `len(name) > 253`. Called from `make_pipelinerun_scenario` at PR name construction.

- [ ] **Step 1: Write the failing tests**

Append to `pipeline/tests/test_tekton.py`:

```python
# ── Tests for validate_pipelinerun_name ─────────────────────────────────────

import pytest as _pytest
from pipeline.lib.tekton import validate_pipelinerun_name


def test_validate_pipelinerun_name_accepts_short():
    validate_pipelinerun_name("baseline-wl-r-i1")  # no raise


def test_validate_pipelinerun_name_accepts_253_char_limit():
    """Exactly 253 chars is the DNS subdomain limit — must pass."""
    name = "a" * 253
    validate_pipelinerun_name(name)  # no raise


def test_validate_pipelinerun_name_rejects_254_chars():
    name = "a" * 254
    with _pytest.raises(ValueError, match="253"):
        validate_pipelinerun_name(name)


def test_make_pipelinerun_scenario_rejects_oversized_name():
    """Long run_name or workload should trip the validator at construction."""
    long_run = "r" * 240
    with _pytest.raises(ValueError, match="253"):
        make_pipelinerun_scenario(
            phase="baseline", workload={"name": "wl"}, run_name=long_run,
            namespace="ns", pipeline_name="sim2real",
            scenario_content="scenario: []",
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest pipeline/tests/test_tekton.py -v -k "validate or oversized"`
Expected: FAIL — `ImportError` on `validate_pipelinerun_name`.

- [ ] **Step 3: Implement**

In `pipeline/lib/tekton.py`, below the `build_results_dir` helper:

```python
_DNS_SUBDOMAIN_MAX = 253


def validate_pipelinerun_name(name: str) -> None:
    """Raise ValueError if ``name`` exceeds the RFC 1123 DNS subdomain limit
    (253 chars). PipelineRun.metadata.name is a DNS subdomain, so Tekton
    rejects longer names at admission. Called at construction time so
    assemble surfaces the failure before any dispatch attempt.
    """
    if len(name) > _DNS_SUBDOMAIN_MAX:
        raise ValueError(
            f"PipelineRun name {name!r} is {len(name)} chars, exceeds the "
            f"{_DNS_SUBDOMAIN_MAX}-char DNS subdomain limit"
        )
```

Then in `make_pipelinerun_scenario`, immediately after `pr_name = f"..."` (currently line 73):

```python
    pr_name = f"{safe_phase}-{safe_name}-{run_name}-i{iteration}"
    validate_pipelinerun_name(pr_name)
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest pipeline/tests/test_tekton.py -v -k "validate or oversized"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pipeline/lib/tekton.py pipeline/tests/test_tekton.py
git commit -m "tekton: add validate_pipelinerun_name + wire into make_pipelinerun_scenario"
```

---

### Task 3: Emit `replica` param from `make_pipelinerun_scenario`

**Files:**
- Modify: `pipeline/lib/tekton.py`
- Test: `pipeline/tests/test_tekton.py` (append)

**Interfaces:**
- Consumes: `iteration` (already a parameter of `make_pipelinerun_scenario`, int, default 1).
- Produces: PR YAML params list now contains `{"name": "replica", "value": str(iteration)}` — consumed by pipeline.yaml's `$(params.replica)` substitutions.

- [ ] **Step 1: Write the failing test**

Append to `pipeline/tests/test_tekton.py`:

```python
def test_make_pipelinerun_scenario_emits_replica_param_default():
    """Default iteration=1 → replica='1' in params (string, per Tekton API)."""
    pr = make_pipelinerun_scenario(
        phase="baseline", workload={"name": "wl"}, run_name="r",
        namespace="ns", pipeline_name="sim2real",
        scenario_content="scenario: []",
    )
    params = {p["name"]: p["value"] for p in pr["spec"]["params"]}
    assert params["replica"] == "1"
    assert isinstance(params["replica"], str)


def test_make_pipelinerun_scenario_emits_replica_param_explicit():
    """iteration=5 → replica='5'."""
    pr = make_pipelinerun_scenario(
        phase="baseline", workload={"name": "wl"}, run_name="r",
        namespace="ns", pipeline_name="sim2real",
        scenario_content="scenario: []",
        iteration=5,
    )
    params = {p["name"]: p["value"] for p in pr["spec"]["params"]}
    assert params["replica"] == "5"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest pipeline/tests/test_tekton.py -v -k "emits_replica"`
Expected: FAIL — `params["replica"]` KeyError.

- [ ] **Step 3: Implement**

In `pipeline/lib/tekton.py:make_pipelinerun_scenario`, append `replica` to the `params` list. Insert this line at the end of the initial `params: list[dict] = [ ... ]` block (before the `if observe:` conditional):

```python
    params: list[dict] = [
        {"name": "experimentId",      "value": run_name},
        {"name": "runName",           "value": run_name},
        {"name": "namespace",         "value": namespace},
        {"name": "phase",             "value": phase},
        {"name": "scenarioContent",   "value": scenario_content},
        {"name": "specContent",       "value": spec_content},
        {"name": "workloadName",      "value": wl_name},
        {"name": "workloadSpec",      "value": wl_spec_str},
        {"name": "benchmarkGitRepoUrl", "value": benchmark_git_repo_url},
        {"name": "benchmarkGitCommit", "value": benchmark_git_commit},
        {"name": "blisGitRepoUrl",   "value": blis_git_repo_url},
        {"name": "blisGitCommit",     "value": blis_git_commit},
        {"name": "model",            "value": model},
        {"name": "replica",          "value": str(iteration)},
    ]
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest pipeline/tests/test_tekton.py -v`
Expected: PASS (all tekton tests, including pre-existing ones).

- [ ] **Step 5: Commit**

```bash
git add pipeline/lib/tekton.py pipeline/tests/test_tekton.py
git commit -m "tekton: emit replica PipelineRun param (str(iteration))"
```

---

### Task 4: Add `replica` param + thread `$(params.replica)` in `pipeline.yaml`

**Files:**
- Modify: `pipeline/pipeline.yaml` (5 `resultsDir` values; add pipeline-level `replica` param)
- Test: `pipeline/tests/test_pipeline_yaml.py` (append new test class)

**Interfaces:**
- Consumes: `RESULTS_DIR_TEMPLATE` from Task 1 (verifies alignment).
- Produces: pipeline.yaml declares `replica` param + threads it through every `resultsDir` writer.

- [ ] **Step 1: Write the failing tests**

Append to `pipeline/tests/test_pipeline_yaml.py`:

```python
class TestReplicaParamThreading:
    """pipeline.yaml declares and threads the replica param through every
    resultsDir writer, per step-5 (issue #511).
    """

    def _pipeline(self):
        return yaml.safe_load(PIPELINE_YAML.read_text())

    def test_declares_replica_param(self):
        """Pipeline must declare a top-level 'replica' param, string, default '1'."""
        pipeline = self._pipeline()
        params = {p["name"]: p for p in pipeline["spec"]["params"]}
        assert "replica" in params, "pipeline.yaml missing 'replica' param declaration"
        assert params["replica"].get("type", "string") == "string"
        assert params["replica"].get("default") == "1"

    def test_every_results_dir_matches_canonical_template(self):
        """Every resultsDir occurrence in pipeline.yaml must equal
        tekton.RESULTS_DIR_TEMPLATE. This is the grep-audit-in-code that
        catches a task quietly writing to the wrong (unversioned) path."""
        from pipeline.lib.tekton import RESULTS_DIR_TEMPLATE
        pipeline = self._pipeline()
        for task in pipeline["spec"]["tasks"]:
            for param in task.get("params", []):
                if param["name"] == "resultsDir":
                    assert param["value"] == RESULTS_DIR_TEMPLATE, (
                        f"task {task['name']}.resultsDir = {param['value']!r} "
                        f"but canonical is {RESULTS_DIR_TEMPLATE!r}"
                    )

    def test_results_dir_includes_replica_segment(self):
        """Every resultsDir must reference $(params.replica) — a bare
        assertion complementary to the template match, so a future edit
        that changes the template shape without dropping the replica
        segment still passes; a change that drops the segment fails
        with a clearer message."""
        pipeline = self._pipeline()
        for task in pipeline["spec"]["tasks"]:
            for param in task.get("params", []):
                if param["name"] == "resultsDir":
                    assert "$(params.replica)" in param["value"], (
                        f"task {task['name']}.resultsDir does not thread replica"
                    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest pipeline/tests/test_pipeline_yaml.py::TestReplicaParamThreading -v`
Expected: FAIL — pipeline.yaml doesn't have `replica` param yet.

- [ ] **Step 3: Modify `pipeline.yaml` — add pipeline-level `replica` param**

Below `- name: extraArgs\n  type: string\n  default: ""` (currently lines 48–50), add:

```yaml
    - name: replica
      type: string
      default: "1"
```

- [ ] **Step 4: Modify all 5 `resultsDir` values**

Update each occurrence (currently at lines 108, 121, 134, 153, 174):

From:
```yaml
        - name: resultsDir
          value: "$(params.runName)/$(params.phase)/$(params.workloadName)"
```

To:
```yaml
        - name: resultsDir
          value: "$(params.runName)/$(params.phase)/$(params.workloadName)/i$(params.replica)"
```

Note: leave `plans_dir` at line 97 unchanged — it's an input path, not a results path.

- [ ] **Step 5: Run tests**

Run: `.venv/bin/python -m pytest pipeline/tests/test_pipeline_yaml.py -v`
Expected: PASS — new `TestReplicaParamThreading` class + all existing tests (the existing `test_results_dir_matches_workload` and `test_results_dir_matches_workload` continue to hold since all 5 values change identically).

- [ ] **Step 6: Commit**

```bash
git add pipeline/pipeline.yaml pipeline/tests/test_pipeline_yaml.py
git commit -m "pipeline.yaml: add replica param + thread through resultsDir"
```

---

### Task 5: Verify assemble_run + deploy.py flows unchanged; update golden tests if any

**Files:**
- Modify: `pipeline/tests/test_assemble_run.py` (only if failing — add coverage for new `replica` param on generated PR YAML)
- Modify: (none in production — `_cmd_run` requires no change since `replica` is baked into the YAML at assemble time)

**Interfaces:**
- Consumes: The `replica` param emitted by `make_pipelinerun_scenario` (Task 3).
- Produces: nothing new — this task verifies existing coverage catches the new behavior.

- [ ] **Step 1: Run the full test suite**

Run: `.venv/bin/python -m pytest pipeline/ -v`
Expected: any failures related to golden PR YAML shape need updating; unrelated failures indicate a regression to investigate.

- [ ] **Step 2: If any assemble tests fail on "replica" being in params, update them**

For any test in `pipeline/tests/test_assemble_run.py` that asserts on the exact set of PR YAML params (via equality or "params list length"), add `replica` to the expected set. Do not weaken assertions — keep exact matching.

- [ ] **Step 3: Optional new assertion — verify assemble threads iteration into replica**

If test_assemble_run.py doesn't already cover it, add:

```python
def test_assemble_pipelinerun_files_carry_replica_param(tmp_path):
    """A --replicas 3 assemble writes pipelinerun-*.yaml files each carrying
    the correct replica param matching their iteration."""
    # ... existing fixture that runs assemble with replicas=3 ...
    # For each iteration i in 1..3:
    #   pr_path = cluster_dir / f"pipelinerun-<wl>|<pkg>|i{i}.yaml"
    #   pr = yaml.safe_load(pr_path.read_text())
    #   replica = next(p["value"] for p in pr["spec"]["params"] if p["name"] == "replica")
    #   assert replica == str(i)
```

Skip if existing tests already cover this pattern.

- [ ] **Step 4: Run full suite again**

Run: `.venv/bin/python -m pytest pipeline/ -v && ruff check pipeline/ --select F`
Expected: all pass.

- [ ] **Step 5: Commit any test updates**

```bash
git add pipeline/tests/
git commit -m "tests: cover replica param on assemble-generated PipelineRuns"
```

---

### Task 6: Documentation sweep + PR body

**Files:**
- Modify (if stale): `pipeline/README.md`, `CLAUDE.md`, any doc referencing results paths.

**Interfaces:**
- Consumes: the final code state from Tasks 1–5.

- [ ] **Step 1: Sweep for stale results-path references**

Run: `grep -rn "results/{phase}/{workload}\|results/[^/]*/[^/]*/[^i][^0-9]" pipeline/README.md CLAUDE.md docs/`
Look for hardcoded results paths that omit `iN/`. The workspace artifact table in CLAUDE.md's project section shows `runs/<run>/results/{phase}/<workload>/gpu_logs/<node>.log` — this now becomes `runs/<run>/results/{phase}/<workload>/i<N>/gpu_logs/<node>.log`.

- [ ] **Step 2: Update stale docs**

Any doc showing a hardcoded results path should either:
- Get the `iN/` segment inserted (if the doc is describing the path shape post-step-5), OR
- Be left alone with a note that step-6 documentation PR (#514) will address if the doc mentions it's covering the pre-step-5 world.

For CLAUDE.md's workspace-artifact row for `gpu_logs`, update to include `/i<N>` since the pipeline now writes to that layer.

- [ ] **Step 3: Run tests + lint one final time**

Run: `.venv/bin/python -m pytest pipeline/ -v && ruff check pipeline/ --select F`
Expected: pass.

- [ ] **Step 4: Commit doc sweep**

```bash
git add -A
git commit -m "docs: update results-path references to include iN/ layer"
```

(Or skip commit if nothing changed.)

- [ ] **Step 5: Push + PR**

```bash
git push -u origin refactor/v2-step-5-issue-511-pipeline-yaml-replica-threading
gh pr create --base refactor/v2-step-5 --title "..." --body-file <body-path>
```

Body must include:
- `Closes #511`
- Summary of changes per task.
- Design decisions locked in (assemble-time replica injection; plans_dir unchanged; wipe/collect unchanged).
- Doc-sweep results.
- Any deferred follow-up (per-iteration wipe UX).

---

## Self-Review Checklist

**Spec coverage:**
- ✓ `pipeline.yaml` `replica` param added — Task 4 Step 3.
- ✓ `pipeline.yaml` `resultsDir` threaded — Task 4 Step 4.
- ✓ `build_results_dir` helper — Task 1.
- ✓ Rendered-pipeline test — Task 4 Step 1 (`test_every_results_dir_matches_canonical_template`).
- ✓ `validate_pipelinerun_name` — Task 2.
- ✓ PR name construction `{phase}-{workload}-{run}-i{N}` — already in place from #521; Task 2 wires validator to it.
- ✓ `_cmd_run` dispatch passes `replica` — accomplished via assemble-time injection (Task 3); dispatch reads pre-baked YAML unchanged.
- ✓ `_apply_run_filters` iteration term — already delivered in #520 (PR 4/#512); no change needed here.
- ✓ Integration test — the `test_every_results_dir_matches_canonical_template` is the rendered-pipeline assertion; a live-cluster test is deferred (would need mock cluster fixtures).
- ✓ Grep audit — captured in `test_every_results_dir_matches_canonical_template` (walks every task, catches drift).

**Placeholder scan:** none — all code blocks are complete.

**Type consistency:** `replica` is `str(iteration)` in Python code (Task 3), `type: string` in Tekton (Task 4). `iteration` is `int` throughout (existing shape from #521). No drift.

## Execution Handoff

Plan saved. Executing inline via `superpowers:executing-plans` — the plan is 6 tasks, tightly-scoped, and mostly independent within each task. No fresh-subagent-per-task is warranted for this size.
