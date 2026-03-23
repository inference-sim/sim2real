# PR5 Review Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix all critical and important issues found in the PR5 (`pr5/tektonc-cluster-benchmarking`) review across correctness, error handling, tests, and documentation.

**Architecture:** All changes are in the worktree at `.worktrees/pr5-tektonc-benchmarking/`. The primary files are `tools/transfer_cli.py` (CLI implementation), `tools/test_transfer_cli.py` (tests), and three doc/schema files. Changes are grouped by category to produce clean, atomic commits.

**Tech Stack:** Python 3.10+ stdlib + PyYAML; pytest for tests; JSON Schema; Markdown for docs.

---

## File Map

| File | Changes |
|------|---------|
| `tools/transfer_cli.py` | Noise CV fix; schema violation fix; go build timeout+path; YAML silent reclassification; exception narrowing in cmd_benchmark_state; write_text guards; _preflight_check_values except; convert-trace name normalization |
| `tools/test_transfer_cli.py` | 9 new tests; 1 updated test |
| `tools/schemas/validation_results.schema.json` | Fix operator_notes description |
| `prompts/extract.md` | Fix false claim about preflight |
| `prompts/generate.md` | Fix halt table n/a values |
| `.worktrees/pr5-tektonc-benchmarking/CLAUDE.md` | Fix benchmark exit code documentation |
| `tools/transfer_cli.py` (module docstring) | Add missing subcommands to docstring |

**Working directory for all commands:** `.worktrees/pr5-tektonc-benchmarking/`

**Run tests with:** `python -m pytest tools/ -v` (requires `.venv/` — see CLAUDE.md)

---

## Task 1: Fix noise CV pooling (Critical correctness)

**Files:**
- Modify: `tools/transfer_cli.py:1534-1549`
- Test: `tools/test_transfer_cli.py` (class `TestBenchmarkNew`)

**Problem:** CV is computed by pooling runs from all workloads into one flat list. Two workloads with different mean latencies (e.g., 50ms vs 200ms) will produce an artificially high CV even if each workload has low run-to-run variance. This inflates T_eff and can produce wrong PASS/FAIL verdicts.

**Fix:** Compute CV per-workload per-metric, take the max. Also add a guard requiring ≥2 runs per workload metric (the old `_cmd_noise_characterize` had this; the rewrite lost it).

- [ ] **Step 1.1: Write the failing test**

Add to `class TestBenchmarkNew` in `tools/test_transfer_cli.py`:

```python
def test_noise_cv_computed_per_workload_not_pooled(self, tmp_path):
    """Two workloads with different mean latencies but identical low CVs
    should produce t_eff near 2*cv, NOT inflated by inter-workload variance."""
    import json, argparse
    # workload A: mean≈50ms, cv≈0.02; workload B: mean≈200ms, cv≈0.02
    # Pooled approach: huge CV from 50 vs 200ms difference
    # Per-workload approach: max CV ≈ 0.02 → t_eff = max(0.05, 0.04) = 0.05
    runs_a = [{"metrics": {"ttft_p50": v, "ttft_p99": v,
                            "tpot_p50": 10.0, "tpot_p99": 10.0}}
               for v in [49.0, 50.0, 51.0, 50.5, 49.5]]   # cv ≈ 0.015
    runs_b = [{"metrics": {"ttft_p50": v, "ttft_p99": v,
                            "tpot_p50": 10.0, "tpot_p99": 10.0}}
               for v in [196.0, 200.0, 204.0, 202.0, 198.0]]  # cv ≈ 0.015
    noise_data = {"workloads": [
        {"name": "fast-workload", "runs": runs_a},
        {"name": "slow-workload", "runs": runs_b},
    ]}
    noise = tmp_path / "noise_results.json"
    noise.write_text(json.dumps(noise_data))
    bl, tr = self._make_baseline_treatment(tmp_path, 100.0, 80.0)
    sc = self._make_signal_coverage(tmp_path)
    wd = self._make_workloads_dir(tmp_path)
    out = tmp_path / "bench_out.json"
    from tools.transfer_cli import cmd_benchmark_new
    args = argparse.Namespace(noise=str(noise), baseline=str(bl), treatment=str(tr),
                              signal_coverage=str(sc), workloads_dir=str(wd),
                              out=str(out))
    rc = cmd_benchmark_new(args)
    result = json.loads(out.read_text())
    # Per-workload CV ≈ 0.015 → t_eff = 0.05 (floor). Pooled CV would be >> 0.5.
    # If t_eff is > 0.5 the test will have got a wrong inflated t_eff.
    assert result["t_eff"] <= 0.10, (
        f"t_eff={result['t_eff']} is suspiciously large — noise CV is being "
        "inflated by pooling across workloads with different mean latencies"
    )

def test_insufficient_noise_runs_exits_2(self, tmp_path):
    """benchmark exits 2 when a noise workload has fewer than 2 runs."""
    import json, argparse
    noise_data = {"workloads": [
        {"name": "glia-40qps", "runs": [
            {"metrics": {"ttft_p50": 100.0, "ttft_p99": 100.0,
                         "tpot_p50": 10.0, "tpot_p99": 15.0}}
        ]},  # only 1 run — insufficient for CV
    ]}
    noise = tmp_path / "noise_results.json"
    noise.write_text(json.dumps(noise_data))
    bl, tr = self._make_baseline_treatment(tmp_path)
    sc = self._make_signal_coverage(tmp_path)
    wd = self._make_workloads_dir(tmp_path)
    out = tmp_path / "bench_out.json"
    from tools.transfer_cli import cmd_benchmark_new
    args = argparse.Namespace(noise=str(noise), baseline=str(bl), treatment=str(tr),
                              signal_coverage=str(sc), workloads_dir=str(wd),
                              out=str(out))
    rc = cmd_benchmark_new(args)
    assert rc == 2, f"Insufficient noise runs should exit 2, got {rc}"
```

- [ ] **Step 1.2: Run tests to verify they fail**

```bash
cd .worktrees/pr5-tektonc-benchmarking
python -m pytest tools/test_transfer_cli.py::TestBenchmarkNew::test_noise_cv_computed_per_workload_not_pooled tools/test_transfer_cli.py::TestBenchmarkNew::test_insufficient_noise_runs_exits_2 -v
```

Expected: both FAIL (or the CV test passes accidentally with the current pooled code if the workloads happen to align; but the insufficient-runs test definitely FAILs — the current code returns 0, not 2).

- [ ] **Step 1.3: Replace the noise CV computation block in `cmd_benchmark_new`**

In `tools/transfer_cli.py`, replace lines 1533–1549 (the "Compute T_eff from noise" block):

**Old:**
```python
    # Compute T_eff from noise
    metrics_keys = ["ttft_p50", "ttft_p99", "tpot_p50", "tpot_p99"]
    per_metric = {k: [] for k in metrics_keys}
    try:
        for wl in noise["workloads"]:
            for run in wl["runs"]:
                for k in metrics_keys:
                    per_metric[k].append(run["metrics"][k])
    except (KeyError, TypeError) as e:
        print(
            f"ERROR: malformed noise results — missing expected field: {e}. "
            "Each workload must have 'runs' with 'metrics' containing "
            "ttft_p50, ttft_p99, tpot_p50, tpot_p99.",
            file=sys.stderr,
        )
        return 2
    noise_cv = max(_compute_cv(per_metric[k]) for k in metrics_keys)
    t_eff = max(0.05, 2.0 * noise_cv)
```

**New:**
```python
    # Compute T_eff from noise — per-workload CV (not pooled across workloads)
    metrics_keys = ["ttft_p50", "ttft_p99", "tpot_p50", "tpot_p99"]
    noise_cv = 0.0
    try:
        for wl in noise["workloads"]:
            wl_name = wl.get("name", "?")
            per_metric = {k: [] for k in metrics_keys}
            for run in wl["runs"]:
                for k in metrics_keys:
                    per_metric[k].append(run["metrics"][k])
            for k in metrics_keys:
                if len(per_metric[k]) < 2:
                    print(
                        f"ERROR: noise workload '{wl_name}' has only "
                        f"{len(per_metric[k])} run(s) for metric '{k}' — "
                        "at least 2 runs are required to compute a noise estimate.",
                        file=sys.stderr,
                    )
                    return 2
            wl_cv = max(_compute_cv(per_metric[k]) for k in metrics_keys)
            noise_cv = max(noise_cv, wl_cv)
    except (KeyError, TypeError) as e:
        print(
            f"ERROR: malformed noise results — missing expected field: {e}. "
            "Each workload must have 'runs' with 'metrics' containing "
            "ttft_p50, ttft_p99, tpot_p50, tpot_p99.",
            file=sys.stderr,
        )
        return 2
    t_eff = max(0.05, 2.0 * noise_cv)
```

- [ ] **Step 1.4: Run tests to verify they pass**

```bash
python -m pytest tools/test_transfer_cli.py::TestBenchmarkNew -v
```

Expected: all TestBenchmarkNew tests pass including the two new ones.

- [ ] **Step 1.5: Commit**

```bash
git add tools/transfer_cli.py tools/test_transfer_cli.py
git commit -m "fix: compute noise CV per-workload (not pooled); enforce ≥2 runs"
```

---

## Task 2: Fix schema violation in error-path output (Critical correctness)

**Files:**
- Modify: `tools/transfer_cli.py:1603-1627`
- Test: `tools/test_transfer_cli.py::TestBenchmarkNew::test_error_verdict_on_workload_name_mismatch`

**Problem:** The error-path output dict (when all workloads are skipped due to name mismatch) adds `"error"` and `"skipped_workloads"` keys. The `benchmark_output.schema.json` has `additionalProperties: false` and doesn't declare these keys — the output fails schema validation.

**Fix:** Remove `"error"` and `"skipped_workloads"` from the output dict; encode the diagnostic info in `"specificity_notes"` (an array of strings that IS in the schema). Also update the existing test that was asserting `"skipped_workloads" in result`.

- [ ] **Step 2.1: Update the existing name-mismatch test to not assert `skipped_workloads`**

In `tools/test_transfer_cli.py`, find `test_error_verdict_on_workload_name_mismatch` and change the last assertion:

**Old:**
```python
    result = json.loads(out.read_text())
    assert result["mechanism_check_verdict"] == "ERROR"
    assert "skipped_workloads" in result
```

**New:**
```python
    result = json.loads(out.read_text())
    assert result["mechanism_check_verdict"] == "ERROR"
    assert "skipped_workloads" not in result, (
        "skipped_workloads key is not in benchmark_output.schema.json — "
        "use specificity_notes instead"
    )
    assert result["specificity_notes"], "name-mismatch error details should appear in specificity_notes"
    assert any("completely-different" in note or "skipped" in note.lower()
               for note in result["specificity_notes"])
```

- [ ] **Step 2.2: Add a schema-compliance test**

Add to `class TestBenchmarkNew` in `tools/test_transfer_cli.py`:

```python
def test_error_path_output_conforms_to_schema(self, tmp_path):
    """Error-path output (all workloads skipped) must not have extra keys
    beyond what benchmark_output.schema.json declares."""
    import json, argparse, yaml
    noise = self._make_noise(tmp_path, cv=0.02)
    bl, tr = self._make_baseline_treatment(tmp_path, 100.0, 80.0)
    sc = self._make_signal_coverage(tmp_path)
    wd = tmp_path / "workloads_mismatch"
    wd.mkdir()
    (wd / "workload_completely-different.yaml").write_text(
        yaml.dump({"version": "1", "kv_utilization": 0.5})
    )
    out = tmp_path / "bench_out.json"
    from tools.transfer_cli import cmd_benchmark_new
    args = argparse.Namespace(noise=str(noise), baseline=str(bl), treatment=str(tr),
                              signal_coverage=str(sc), workloads_dir=str(wd),
                              out=str(out))
    cmd_benchmark_new(args)
    result = json.loads(out.read_text())
    allowed_keys = {"t_eff", "noise_cv", "mechanism_check_verdict", "passed",
                    "workload_classification", "specificity_notes"}
    extra_keys = set(result.keys()) - allowed_keys
    assert not extra_keys, f"Output has extra keys not in schema: {extra_keys}"
```

- [ ] **Step 2.3: Run tests to verify they fail**

```bash
python -m pytest tools/test_transfer_cli.py::TestBenchmarkNew::test_error_verdict_on_workload_name_mismatch tools/test_transfer_cli.py::TestBenchmarkNew::test_error_path_output_conforms_to_schema -v
```

Expected: `test_error_verdict_on_workload_name_mismatch` FAILs (old assertion still there), `test_error_path_output_conforms_to_schema` FAILs (extra keys present).

- [ ] **Step 2.4: Fix the error_output dict in `cmd_benchmark_new`**

In `tools/transfer_cli.py`, replace lines 1611–1620 (the `error_output = {...}` block):

**Old:**
```python
        error_output = {
            "mechanism_check_verdict": "ERROR",
            "passed": False,
            "error": "all workloads skipped due to name mismatch between workloads_dir and result files",
            "skipped_workloads": skipped_workloads,
            "t_eff": round(t_eff, 4),
            "noise_cv": round(noise_cv, 4),
            "workload_classification": [],
            "specificity_notes": [],
        }
```

**New:**
```python
        error_output = {
            "mechanism_check_verdict": "ERROR",
            "passed": False,
            "t_eff": round(t_eff, 4),
            "noise_cv": round(noise_cv, 4),
            "workload_classification": [],
            "specificity_notes": [
                f"all {len(skipped_workloads)} workload(s) skipped due to name mismatch "
                f"between workloads_dir and result files; "
                f"skipped={skipped_workloads}; "
                f"baseline names={sorted(bl_map.keys())}"
            ],
        }
```

- [ ] **Step 2.5: Run tests to verify they pass**

```bash
python -m pytest tools/test_transfer_cli.py::TestBenchmarkNew -v
```

Expected: all pass.

- [ ] **Step 2.6: Commit**

```bash
git add tools/transfer_cli.py tools/test_transfer_cli.py
git commit -m "fix: remove non-schema keys from benchmark error-path output; use specificity_notes"
```

---

## Task 3: Fix go build — timeout, OSError handling, and relative path (Critical error + Important correctness)

**Files:**
- Modify: `tools/transfer_cli.py:1402-1413` (the `if phase == "treatment":` block in `cmd_preflight`)
- Test: `tools/test_transfer_cli.py::TestPreflight`

**Problem 1:** `subprocess.run(["go", "build", ...])` has no timeout; CI will hang indefinitely if Go toolchain stalls.
**Problem 2:** `scheduler_dir = "llm-d-inference-scheduler"` is a relative path; works only if CWD is repo root. Should use `REPO_ROOT` like the rest of the module.

- [ ] **Step 3.1: Write failing test for timeout handling**

Add to `class TestPreflight` in `tools/test_transfer_cli.py`:

```python
def test_treatment_scorer_build_timeout_marks_failed(self, tmp_path):
    """preflight exits 1 (not hangs) when go build times out.
    subprocess is imported locally in cmd_preflight, so mock subprocess.run globally."""
    import argparse, unittest.mock as mock, subprocess as subprocess_mod
    vf = self._values(tmp_path)
    # Create fake scheduler submodule dir so the go build branch is entered
    scheduler_dir = tmp_path / "llm-d-inference-scheduler"
    scheduler_dir.mkdir()

    def fake_run(cmd, **kwargs):
        # Raise TimeoutExpired for go commands; succeed for everything else (kubectl etc.)
        if cmd and "go" in str(cmd[0]):
            raise subprocess_mod.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout", 120))
        result = mock.Mock()
        result.returncode = 0
        result.stdout = "ok"
        result.stderr = ""
        return result

    with mock.patch("tools.transfer_cli.REPO_ROOT", tmp_path), \
         mock.patch("subprocess.run", side_effect=fake_run):
        from tools.transfer_cli import cmd_preflight
        args = argparse.Namespace(phase="treatment", values=str(vf),
                                  namespace="test-ns")
        rc = cmd_preflight(args)
    # preflight must still return (not hang), and some check must have failed
    assert rc == 1, (
        f"preflight should exit 1 when go build times out, got {rc}. "
        "If it hangs, timeout is not being set on the go build subprocess call."
    )
```

- [ ] **Step 3.2: Run test to verify it fails**

```bash
python -m pytest tools/test_transfer_cli.py::TestPreflight::test_treatment_scorer_build_timeout_marks_failed -v
```

Expected: FAIL (current code hangs or raises unhandled TimeoutExpired).

- [ ] **Step 3.3: Fix the treatment scorer build block in `cmd_preflight`**

In `tools/transfer_cli.py`, replace the entire `if phase == "treatment":` block at lines 1402–1413:

**Old:**
```python
    if phase == "treatment":
        import os
        scheduler_dir = "llm-d-inference-scheduler"
        if os.path.isdir(scheduler_dir):
            r = subprocess.run(
                ["go", "build", "./pkg/plugins/scorer/..."],
                capture_output=True, text=True, cwd=scheduler_dir, shell=False
            )
            ok = r.returncode == 0
        else:
            ok = False
        checks.append(("Stage 4 scorer builds", ok, ""))
```

**New:**
```python
    if phase == "treatment":
        scheduler_dir = REPO_ROOT / "llm-d-inference-scheduler"
        if scheduler_dir.is_dir():
            try:
                r = subprocess.run(
                    ["go", "build", "./pkg/plugins/scorer/..."],
                    capture_output=True, text=True, cwd=str(scheduler_dir),
                    shell=False, timeout=120
                )
                ok = r.returncode == 0
                detail = r.stderr.strip() if not ok else ""
            except subprocess.TimeoutExpired:
                ok = False
                detail = "go build timed out after 120s"
            except OSError as e:
                ok = False
                detail = f"failed to launch go: {e}"
        else:
            ok = False
            detail = f"scheduler submodule not found at {scheduler_dir}"
        checks.append(("Stage 4 scorer builds", ok, detail))
```

- [ ] **Step 3.4: Run tests to verify they pass**

```bash
python -m pytest tools/test_transfer_cli.py::TestPreflight -v
```

Expected: all pass.

- [ ] **Step 3.5: Commit**

```bash
git add tools/transfer_cli.py tools/test_transfer_cli.py
git commit -m "fix: add timeout+OSError handling to go build in preflight; use REPO_ROOT for scheduler path"
```

---

## Task 4: Fix YAML parse silent reclassification in `_classify_workloads` (Critical error handling)

**Files:**
- Modify: `tools/transfer_cli.py:1476-1481`
- Test: `tools/test_transfer_cli.py`

**Problem:** When a workload YAML file fails to parse, `_classify_workloads` logs a WARNING, silently classifies the workload as `"unmatched"`, and continues. This corrupts the benchmark verdict without surfacing a hard error.

**Fix:** Raise `ValueError` so `cmd_benchmark_new`'s caller chain propagates the failure as exit 2.

- [ ] **Step 4.1: Write failing test**

Add to `class TestBenchmarkNew` in `tools/test_transfer_cli.py`:

```python
def test_malformed_workload_yaml_exits_2(self, tmp_path):
    """benchmark exits 2 when a workload YAML file is malformed (not silently unmatched)."""
    import json, argparse
    noise = self._make_noise(tmp_path, cv=0.05)
    bl, tr = self._make_baseline_treatment(tmp_path)
    sc = self._make_signal_coverage(tmp_path)
    wd = tmp_path / "workloads_bad"
    wd.mkdir()
    # Valid YAML syntax but wrong type (list, not dict): safe_load returns a list
    # Alternatively write outright invalid YAML to trigger YAMLError
    (wd / "workload_glia-40qps.yaml").write_text(": {bad yaml: [unclosed")
    out = tmp_path / "bench_out.json"
    from tools.transfer_cli import cmd_benchmark_new
    args = argparse.Namespace(noise=str(noise), baseline=str(bl), treatment=str(tr),
                              signal_coverage=str(sc), workloads_dir=str(wd),
                              out=str(out))
    rc = cmd_benchmark_new(args)
    assert rc == 2, (
        f"Malformed workload YAML should exit 2 (infrastructure error), got {rc}. "
        "Silent reclassification to 'unmatched' is not acceptable — "
        "it would produce a wrong benchmark verdict."
    )
```

- [ ] **Step 4.2: Run test to verify it fails**

```bash
python -m pytest tools/test_transfer_cli.py::TestBenchmarkNew::test_malformed_workload_yaml_exits_2 -v
```

Expected: FAIL (current code returns 0 or some non-2 code because it silently continues).

- [ ] **Step 4.3: Fix `_classify_workloads` to raise on YAML parse failure**

In `tools/transfer_cli.py`, replace lines 1476–1481:

**Old:**
```python
        try:
            wl_data = yaml.safe_load(wf.read_text()) or {}
        except Exception as e:
            print(f"WARNING: failed to parse workload file {wf}: {e}", file=sys.stderr)
            result[wl_name] = {"classification": "unmatched", "matched_signals": []}
            continue
```

**New:**
```python
        try:
            raw = wf.read_text()
        except OSError as e:
            raise ValueError(f"cannot read workload file {wf}: {e}") from e
        try:
            wl_data = yaml.safe_load(raw) or {}
        except yaml.YAMLError as e:
            raise ValueError(f"cannot parse workload file {wf}: {e}") from e
```

- [ ] **Step 4.4: Also narrow the `except Exception` around `_classify_workloads` in `cmd_benchmark_new`**

In `tools/transfer_cli.py`, replace lines 1565–1569:

**Old:**
```python
    try:
        classification = _classify_workloads(wd_path, sc_path, mapping_path)
    except Exception as e:
        print(f"ERROR: workload classification failed: {e}", file=sys.stderr)
        return 2
```

**New:**
```python
    try:
        classification = _classify_workloads(wd_path, sc_path, mapping_path)
    except (FileNotFoundError, ValueError) as e:
        print(f"ERROR: workload classification failed: {e}", file=sys.stderr)
        return 2
```

- [ ] **Step 4.5: Run tests to verify they pass**

```bash
python -m pytest tools/test_transfer_cli.py::TestBenchmarkNew -v
```

Expected: all pass.

- [ ] **Step 4.6: Commit**

```bash
git add tools/transfer_cli.py tools/test_transfer_cli.py
git commit -m "fix: raise ValueError on workload YAML parse failure instead of silent 'unmatched' fallback"
```

---

## Task 5: Fix exception handling in `cmd_benchmark_state` (Critical + High)

**Files:**
- Modify: `tools/transfer_cli.py:987-1012, 1006, 1095`
- Test: `tools/test_transfer_cli.py::TestBenchmarkState`

**Problems:**
1. Creation path (`except Exception` at line 990) collapses OSError, JSONDecodeError, and KeyError into one message.
2. Read path (`except Exception` at line 1010) conflates TOCTOU I/O failure with JSON parse failure.
3. Both `write_text` calls (lines 1006 and 1095) are unguarded — disk-full/permissions produce a raw traceback with exit code 1 (misread as validation failure instead of infrastructure error).

- [ ] **Step 5.1: Write tests for specific error paths**

Add to `class TestBenchmarkState` in `tools/test_transfer_cli.py`:

```python
def test_missing_namespace_on_first_invocation_exits_2(self, tmp_path):
    """First invocation without --namespace must exit 2 (not create a broken state file)."""
    ws = self._alg_summary(tmp_path)
    import argparse
    from tools.transfer_cli import cmd_benchmark_state
    args = argparse.Namespace(workspace=str(ws), namespace=None,
                              set_phase=None, force=False)
    rc = cmd_benchmark_state(args)
    assert rc == 2, f"Missing --namespace on first invocation should exit 2, got {rc}"
    assert not (ws / "benchmark_state.json").exists(), (
        "No state file should be created when --namespace is absent"
    )

def test_set_phase_failed_persists_failure_reason(self, tmp_path):
    """--status failed with --failure-reason persists reason in state file."""
    ws = self._alg_summary(tmp_path)
    import json, argparse
    from tools.transfer_cli import cmd_benchmark_state
    # Create state
    cmd_benchmark_state(argparse.Namespace(workspace=str(ws), namespace="ns",
                                           set_phase=None, force=False))
    # Set noise to failed with a reason
    rc = cmd_benchmark_state(argparse.Namespace(
        workspace=str(ws), namespace=None,
        set_phase="noise", status="failed",
        pipelinerun="pr-xyz", results=None,
        failure_reason="OOMKilled after 2h", force=False,
    ))
    assert rc == 0, f"Setting phase to failed should succeed, got {rc}"
    state = json.loads((ws / "benchmark_state.json").read_text())
    assert state["phases"]["noise"]["status"] == "failed"
    assert state["phases"]["noise"]["failure_reason"] == "OOMKilled after 2h"

def test_corrupt_algorithm_summary_json_exits_2(self, tmp_path):
    """Corrupt algorithm_summary.json (invalid JSON) on first invocation exits 2."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "algorithm_summary.json").write_text("{not valid json")
    import argparse
    from tools.transfer_cli import cmd_benchmark_state
    args = argparse.Namespace(workspace=str(ws), namespace="ns",
                              set_phase=None, force=False)
    rc = cmd_benchmark_state(args)
    assert rc == 2, f"Corrupt algorithm_summary.json should exit 2, got {rc}"
```

- [ ] **Step 5.2: Run tests to verify they fail or verify existing behavior**

```bash
python -m pytest tools/test_transfer_cli.py::TestBenchmarkState::test_missing_namespace_on_first_invocation_exits_2 tools/test_transfer_cli.py::TestBenchmarkState::test_set_phase_failed_persists_failure_reason tools/test_transfer_cli.py::TestBenchmarkState::test_corrupt_algorithm_summary_json_exits_2 -v
```

Expected: `test_missing_namespace_on_first_invocation_exits_2` PASS (logic already correct), `test_set_phase_failed_persists_failure_reason` PASS (logic already correct — this is new coverage verifying existing behavior), `test_corrupt_algorithm_summary_json_exits_2` PASS (current broad `except` handles this — but we want to verify it still works after we narrow).

Note if any are already PASSING — that's fine, they're adding safety net coverage for the narrowing changes below.

- [ ] **Step 5.3: Fix creation-path exception handling in `cmd_benchmark_state`**

In `tools/transfer_cli.py`, replace lines 987–993:

**Old:**
```python
        try:
            alg = json.loads(alg_path.read_text())
            alg_name = alg["algorithm_name"]
        except Exception as e:
            print(f"ERROR: cannot read algorithm_name from {alg_path}: {e}",
                  file=sys.stderr)
            return 2
```

**New:**
```python
        try:
            raw = alg_path.read_text()
        except OSError as e:
            print(f"ERROR: cannot read {alg_path}: {e}", file=sys.stderr)
            return 2
        try:
            alg = json.loads(raw)
        except json.JSONDecodeError as e:
            print(f"ERROR: {alg_path} contains invalid JSON: {e}", file=sys.stderr)
            return 2
        if "algorithm_name" not in alg:
            print(f"ERROR: {alg_path} missing required 'algorithm_name' field.",
                  file=sys.stderr)
            return 2
        alg_name = alg["algorithm_name"]
```

- [ ] **Step 5.4: Fix read-path exception handling in `cmd_benchmark_state`**

In `tools/transfer_cli.py`, replace lines 1008–1012:

**Old:**
```python
        try:
            state = json.loads(state_path.read_text())
        except Exception as e:
            print(f"ERROR: cannot parse {state_path}: {e}", file=sys.stderr)
            return 2
```

**New:**
```python
        try:
            raw = state_path.read_text()
        except OSError as e:
            print(f"ERROR: cannot read {state_path}: {e}", file=sys.stderr)
            return 2
        try:
            state = json.loads(raw)
        except json.JSONDecodeError as e:
            print(f"ERROR: {state_path} contains invalid JSON: {e}", file=sys.stderr)
            return 2
```

- [ ] **Step 5.5: Wrap both `write_text` calls with OSError guards**

First write (line 1006 — initial creation, inside `if not state_path.exists():` block):

**Old (line 1006):**
```python
        state_path.write_text(json.dumps(state, indent=2))
    else:
```

**New:**
```python
        try:
            state_path.write_text(json.dumps(state, indent=2))
        except OSError as e:
            print(f"ERROR: cannot write {state_path}: {e}", file=sys.stderr)
            return 2
    else:
```

Second write (line 1095 — after set-phase update):

**Old (line 1095):**
```python
    state_path.write_text(json.dumps(state, indent=2))
    return 0
```

**New:**
```python
    try:
        state_path.write_text(json.dumps(state, indent=2))
    except OSError as e:
        print(f"ERROR: cannot write {state_path}: {e}", file=sys.stderr)
        return 2
    return 0
```

- [ ] **Step 5.6: Run tests to verify they pass**

```bash
python -m pytest tools/test_transfer_cli.py::TestBenchmarkState -v
```

Expected: all pass.

- [ ] **Step 5.7: Commit**

```bash
git add tools/transfer_cli.py tools/test_transfer_cli.py
git commit -m "fix: narrow exception handling in cmd_benchmark_state; guard write_text against OSError"
```

---

## Task 6: Guard `write_text` in `cmd_convert_trace` and `cmd_render_pipelinerun` (High)

**Files:**
- Modify: `tools/transfer_cli.py:1210, 1251`

**Problem:** `output.write_text(...)` and `out.write_text(...)` are unguarded. An `OSError` (disk full, permissions) produces a raw Python traceback with exit code 1, which callers interpret as a validation failure (documented meaning of exit 1) rather than an infrastructure error (exit 2).

- [ ] **Step 6.1: Wrap `write_text` in `cmd_convert_trace`**

In `tools/transfer_cli.py`, replace line 1210:

**Old:**
```python
    output.write_text(json.dumps({"workloads": workloads}, indent=2))
    return 0
```

**New:**
```python
    try:
        output.write_text(json.dumps({"workloads": workloads}, indent=2))
    except OSError as e:
        print(f"ERROR: cannot write output file '{output}': {e}", file=sys.stderr)
        return 2
    return 0
```

- [ ] **Step 6.2: Wrap `write_text` in `cmd_render_pipelinerun`**

In `tools/transfer_cli.py`, replace line 1251:

**Old:**
```python
    out.write_text(rendered)
    return 0
```

**New:**
```python
    try:
        out.write_text(rendered)
    except OSError as e:
        print(f"ERROR: cannot write output file '{out}': {e}", file=sys.stderr)
        return 2
    return 0
```

- [ ] **Step 6.3: Run all tests to verify no regressions**

```bash
python -m pytest tools/ -v
```

Expected: all pass.

- [ ] **Step 6.4: Commit**

```bash
git add tools/transfer_cli.py
git commit -m "fix: guard write_text against OSError in convert-trace and render-pipelinerun"
```

---

## Task 7: Fix `_preflight_check_values` broad exception handling (High)

**Files:**
- Modify: `tools/transfer_cli.py:1308-1311`
- Test: `tools/test_transfer_cli.py::TestPreflight`

**Problem:** `except Exception` catches `ImportError` (if PyYAML is missing), `UnicodeDecodeError`, `MemoryError`, etc., and reports all as "Cannot parse values.yaml" — a misleading message when the actual failure is a missing library or unreadable file.

- [ ] **Step 7.1: Write test for file-not-found vs parse-error distinction**

Add to `class TestPreflight` in `tools/test_transfer_cli.py`:

```python
def test_missing_values_file_returns_oserror_message(self, tmp_path):
    """_preflight_check_values returns an OS-level error message for a missing file,
    not a YAML parse error."""
    from tools.transfer_cli import _preflight_check_values
    from pathlib import Path
    errors = _preflight_check_values(Path(tmp_path / "nonexistent.yaml"), "ns", "noise")
    assert errors, "Missing file should produce at least one error"
    assert any("read" in e.lower() or "no such" in e.lower() or "errno" in e.lower()
               for e in errors), (
        f"Expected an OS/read error message for missing file, got: {errors}"
    )
    assert not any("parse" in e.lower() for e in errors), (
        f"Should NOT say 'parse' for a missing file — that implies a YAML syntax problem: {errors}"
    )
```

- [ ] **Step 7.2: Run test to verify it fails**

```bash
python -m pytest tools/test_transfer_cli.py::TestPreflight::test_missing_values_file_returns_oserror_message -v
```

Expected: FAIL (current code says "Cannot parse values.yaml: [Errno 2] No such file").

- [ ] **Step 7.3: Fix `_preflight_check_values` exception handling**

In `tools/transfer_cli.py`, replace lines 1308–1311:

**Old:**
```python
    try:
        data = yaml.safe_load(values_path.read_text())
    except Exception as e:
        return [f"Cannot parse values.yaml: {e}"]
```

**New:**
```python
    try:
        raw = values_path.read_text()
    except OSError as e:
        return [f"Cannot read values.yaml: {e}"]
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        return [f"Cannot parse values.yaml: {e}"]
```

- [ ] **Step 7.4: Run tests to verify they pass**

```bash
python -m pytest tools/test_transfer_cli.py::TestPreflight -v
```

Expected: all pass.

- [ ] **Step 7.5: Commit**

```bash
git add tools/transfer_cli.py tools/test_transfer_cli.py
git commit -m "fix: split OSError from YAMLError in _preflight_check_values; accurate error messages"
```

---

## Task 8: Fix `cmd_convert_trace` name normalization asymmetry (Important correctness)

**Files:**
- Modify: `tools/transfer_cli.py:1165`
- Test: `tools/test_transfer_cli.py::TestConvertTrace`

**Problem:** `_classify_workloads` normalizes workload names (`workload_glia_40qps.yaml` → `glia-40qps`) but `cmd_convert_trace` uses raw directory names (`glia_40qps` stays as-is). When trace directories on the PVC use underscores, the names in the results JSON won't match the classified workload names, causing all workloads to be skipped with an ERROR verdict.

- [ ] **Step 8.1: Write failing test**

Add to `class TestConvertTrace` in `tools/test_transfer_cli.py`:

```python
def test_underscore_directory_names_are_normalized(self, tmp_path):
    """convert-trace normalizes workload_ prefix and underscores to hyphens,
    matching _classify_workloads normalization."""
    # Directory name: glia_40qps (underscores, no workload_ prefix)
    wl_dir = tmp_path / "baseline" / "glia_40qps"
    _write_tracev2(wl_dir, [
        {"send_time_us": "0", "first_chunk_time_us": "100000",
         "last_chunk_time_us": "200000", "num_chunks": "5", "status": "ok"},
    ])
    out = tmp_path / "baseline_results.json"
    from tools.transfer_cli import cmd_convert_trace
    import argparse, json
    args = argparse.Namespace(input_dir=str(tmp_path / "baseline"), output=str(out))
    rc = cmd_convert_trace(args)
    assert rc == 0
    result = json.loads(out.read_text())
    # Name should be normalized: glia_40qps → glia-40qps
    assert result["workloads"][0]["name"] == "glia-40qps", (
        f"Expected 'glia-40qps' but got '{result['workloads'][0]['name']}'. "
        "convert-trace must normalize workload names to match _classify_workloads."
    )
```

- [ ] **Step 8.2: Run test to verify it fails**

```bash
python -m pytest tools/test_transfer_cli.py::TestConvertTrace::test_underscore_directory_names_are_normalized -v
```

Expected: FAIL (current code preserves `glia_40qps` as-is).

- [ ] **Step 8.3: Fix `cmd_convert_trace` to normalize workload names**

In `tools/transfer_cli.py`, replace line 1165:

**Old:**
```python
        wl_name = wl_dir.name
```

**New:**
```python
        # Normalize to match _classify_workloads: strip "workload_" prefix, underscores→hyphens
        wl_name = wl_dir.name.removeprefix("workload_").replace("_", "-")
```

- [ ] **Step 8.4: Run tests to verify they pass**

```bash
python -m pytest tools/test_transfer_cli.py::TestConvertTrace -v
```

Expected: all pass including the new test.

- [ ] **Step 8.5: Commit**

```bash
git add tools/transfer_cli.py tools/test_transfer_cli.py
git commit -m "fix: normalize workload directory names in convert-trace to match _classify_workloads"
```

---

## Task 9: Add missing critical test — all workloads unmatched by classification (Critical test)

**Files:**
- Test: `tools/test_transfer_cli.py::TestBenchmarkNew`

**Problem:** `test_error_verdict_on_workload_name_mismatch` tests the path where workloads aren't found in result files. The distinct path where workloads ARE in results but have no matched signals (line 1630–1631: `if not matched_improvements`) is untested. A regression here would produce an ERROR verdict silently.

- [ ] **Step 9.1: Write the test**

Add to `class TestBenchmarkNew`:

```python
def test_error_verdict_when_all_workloads_unmatched_by_classification(self, tmp_path):
    """ERROR exit 2 when all workloads resolve (no name mismatch) but none match
    any signal — distinct from the name-mismatch ERROR path."""
    import json, argparse, yaml
    noise = self._make_noise(tmp_path, cv=0.02)
    bl, tr = self._make_baseline_treatment(tmp_path, 100.0, 80.0)
    sc = self._make_signal_coverage(tmp_path)
    # Workloads present in result files but with no mapped signal fields
    wd = tmp_path / "workloads_no_signals"
    wd.mkdir()
    (wd / "workload_glia-40qps.yaml").write_text(
        yaml.dump({"version": "1", "aggregate_rate": 40})  # no kv_utilization/in_flight
    )
    (wd / "workload_prefix-heavy.yaml").write_text(
        yaml.dump({"version": "1", "aggregate_rate": 85})
    )
    out = tmp_path / "bench_out.json"
    from tools.transfer_cli import cmd_benchmark_new
    args = argparse.Namespace(noise=str(noise), baseline=str(bl), treatment=str(tr),
                              signal_coverage=str(sc), workloads_dir=str(wd),
                              out=str(out))
    rc = cmd_benchmark_new(args)
    assert rc == 2, f"All-unmatched classification should exit 2 (ERROR), got {rc}"
    result = json.loads(out.read_text())
    assert result["mechanism_check_verdict"] == "ERROR"
    # Confirm this is the classification-ERROR path (workload_classification is non-empty)
    # not the name-mismatch path (which has empty workload_classification)
    assert len(result["workload_classification"]) > 0, (
        "All-unmatched path should have workload entries (they were found, just not matched)"
    )
```

- [ ] **Step 9.2: Write the test for missing input files**

Add to `class TestBenchmarkNew`:

```python
def test_missing_noise_file_exits_2(self, tmp_path):
    """benchmark exits 2 when --noise file does not exist."""
    import argparse
    bl, tr = self._make_baseline_treatment(tmp_path)
    sc = self._make_signal_coverage(tmp_path)
    wd = self._make_workloads_dir(tmp_path)
    from tools.transfer_cli import cmd_benchmark_new
    args = argparse.Namespace(
        noise=str(tmp_path / "nonexistent_noise.json"),
        baseline=str(bl), treatment=str(tr),
        signal_coverage=str(sc), workloads_dir=str(wd),
        out=str(tmp_path / "out.json"),
    )
    rc = cmd_benchmark_new(args)
    assert rc == 2, f"Missing noise file should exit 2, got {rc}"

def test_malformed_json_input_exits_2(self, tmp_path):
    """benchmark exits 2 when an input JSON file is malformed."""
    import argparse
    noise = tmp_path / "bad_noise.json"
    noise.write_text("{broken json")
    bl, tr = self._make_baseline_treatment(tmp_path)
    sc = self._make_signal_coverage(tmp_path)
    wd = self._make_workloads_dir(tmp_path)
    from tools.transfer_cli import cmd_benchmark_new
    args = argparse.Namespace(
        noise=str(noise), baseline=str(bl), treatment=str(tr),
        signal_coverage=str(sc), workloads_dir=str(wd),
        out=str(tmp_path / "out.json"),
    )
    rc = cmd_benchmark_new(args)
    assert rc == 2, f"Malformed JSON input should exit 2, got {rc}"
```

- [ ] **Step 9.3: Run new tests to verify behavior**

```bash
python -m pytest tools/test_transfer_cli.py::TestBenchmarkNew::test_error_verdict_when_all_workloads_unmatched_by_classification tools/test_transfer_cli.py::TestBenchmarkNew::test_missing_noise_file_exits_2 tools/test_transfer_cli.py::TestBenchmarkNew::test_malformed_json_input_exits_2 -v
```

Expected: all PASS (the existing code handles these paths correctly — these are regression-guard tests).

- [ ] **Step 9.4: Commit**

```bash
git add tools/test_transfer_cli.py
git commit -m "test: add regression-guard tests for benchmark classification-ERROR and missing/malformed inputs"
```

---

## Task 10: Add important test — `tektonc` non-zero exit returns 1 (Important test)

**Files:**
- Test: `tools/test_transfer_cli.py::TestCompilePipeline`

**Problem:** No test verifies that `cmd_compile_pipeline` returns 1 (not 2) when tektonc runs but fails. The distinction matters: exit 1 = compilation failure, exit 2 = infrastructure problem.

- [ ] **Step 10.1: Write the test**

Add to `class TestCompilePipeline` in `tools/test_transfer_cli.py`:

```python
def test_tektonc_compilation_failure_returns_1(self, tmp_path):
    """compile-pipeline exits 1 (not 2) when tektonc runs but returns non-zero.
    Exit 1 = compilation failure; exit 2 = infrastructure failure (missing files)."""
    import argparse, unittest.mock as mock
    from tools.transfer_cli import cmd_compile_pipeline
    tdir = tmp_path / "tekton"
    tdir.mkdir()
    (tdir / "noise-pipeline.yaml.j2").write_text("{{ undefined_var }}\n")
    vf = tmp_path / "values.yaml"
    vf.write_text("some_key: value\n")
    args = argparse.Namespace(
        template_dir=str(tdir),
        values=str(vf),
        phase="noise",
        out=str(tmp_path / "out"),
    )
    with mock.patch("tools.transfer_cli.subprocess.run") as mock_run:
        mock_run.return_value = mock.Mock(returncode=1, stdout="", stderr="Jinja2 UndefinedError")
        rc = cmd_compile_pipeline(args)
    assert rc == 1, (
        f"tektonc compilation failure should exit 1, got {rc}. "
        "Infrastructure failures are exit 2; compilation failures are exit 1."
    )
```

- [ ] **Step 10.2: Run test to verify it passes**

```bash
python -m pytest tools/test_transfer_cli.py::TestCompilePipeline::test_tektonc_compilation_failure_returns_1 -v
```

Expected: PASS (the existing code `if r.returncode != 0: return 1` already handles this).

- [ ] **Step 10.3: Commit**

```bash
git add tools/test_transfer_cli.py
git commit -m "test: add regression-guard for tektonc compilation failure returning exit 1 vs exit 2"
```

---

## Task 11: Add important test — `clients[]` traversal in `_classify_workloads` (Important test)

**Files:**
- Test: `tools/test_transfer_cli.py::TestBenchmarkNew`

**Problem:** The `clients[]` nested key traversal in `_classify_workloads` (line 1484–1486) is never exercised in tests. If accidentally removed, nested workload formats would always classify as "unmatched".

- [ ] **Step 11.1: Write the test**

Add to `class TestBenchmarkNew`:

```python
def test_nested_clients_keys_are_used_for_classification(self, tmp_path):
    """_classify_workloads must match signals from clients[] nested keys,
    not just top-level workload YAML keys."""
    import json, argparse, yaml
    noise = self._make_noise(tmp_path, cv=0.02)
    bl, tr = self._make_baseline_treatment(tmp_path, 100.0, 80.0)
    sc = self._make_signal_coverage(tmp_path)
    wd = tmp_path / "workloads_nested"
    wd.mkdir()
    # kv_utilization is inside clients[], not at top level
    (wd / "workload_glia-40qps.yaml").write_text(
        yaml.dump({
            "version": "1",
            "aggregate_rate": 40,
            "clients": [{"kv_utilization": 0.5, "concurrency": 10}],
        })
    )
    (wd / "workload_prefix-heavy.yaml").write_text(
        yaml.dump({"version": "1", "aggregate_rate": 85})
    )
    out = tmp_path / "bench_out.json"
    from tools.transfer_cli import cmd_benchmark_new
    args = argparse.Namespace(noise=str(noise), baseline=str(bl), treatment=str(tr),
                              signal_coverage=str(sc), workloads_dir=str(wd),
                              out=str(out))
    rc = cmd_benchmark_new(args)
    result = json.loads(out.read_text())
    # glia-40qps has kv_utilization inside clients[] — should be "matched"
    glia = next(w for w in result["workload_classification"] if w["workload"] == "glia-40qps")
    assert glia["classification"] == "matched", (
        f"glia-40qps has kv_utilization in clients[] — should be 'matched', "
        f"got '{glia['classification']}'. clients[] keys must be included in signal matching."
    )
```

- [ ] **Step 11.2: Run test to verify it passes**

```bash
python -m pytest tools/test_transfer_cli.py::TestBenchmarkNew::test_nested_clients_keys_are_used_for_classification -v
```

Expected: PASS (the existing `clients[]` traversal code is correct — this is a regression guard).

- [ ] **Step 11.3: Commit**

```bash
git add tools/test_transfer_cli.py
git commit -m "test: add regression-guard for clients[] nested key traversal in _classify_workloads"
```

---

## Task 12: Fix documentation (Critical + Important docs)

**Files:**
- Modify: `prompts/extract.md` (line ~36)
- Modify: `CLAUDE.md` (line ~92)
- Modify: `prompts/validate.md` (Halt Conditions table, "Benchmark infrastructure error" row)
- Modify: `tools/schemas/validation_results.schema.json` (line ~85, operator_notes description)
- Modify: `prompts/generate.md` (Halt Conditions table, 2 rows with `n/a` halt_reason)
- Modify: `tools/transfer_cli.py` (module docstring, lines 4–10)

- [ ] **Step 12.1: Fix `extract.md` false claim about preflight checking compiled image**

In `prompts/extract.md`, find and replace the sentence at line ~36:

**Old:**
```
The real enforcement gate for `blis observe` is the `preflight --phase noise` call in Stage 5 Step 5b, which checks the compiled task image and will fail if `blis observe` is not available.
```

**New:**
```
The real enforcement gate for `blis observe` is the `preflight --phase noise` call in Stage 5 Step 5b, which verifies that `observe.image` in `workspace/tekton/values.yaml` has been resolved (no `<TAG>` placeholder) and that cluster prerequisites are in place. The `blis observe` command availability inside the image is not verified at preflight time — ensure the inference-sim submodule is bumped and the image is rebuilt before submitting the pipeline.
```

- [ ] **Step 12.2: Fix CLAUDE.md benchmark exit code documentation**

In `CLAUDE.md`, find and replace line ~92:

**Old:**
```
- `benchmark` exits 0 for both PASS and INCONCLUSIVE verdicts (pipeline should proceed); operators must review `mechanism_check_verdict` in the output JSON to distinguish the two cases.
```

**New:**
```
- `benchmark` exit codes: 0 = PASS or INCONCLUSIVE (pipeline should proceed); 1 = FAIL (no matched improvement ≥ T_eff); 2 = ERROR (all workloads skipped due to name mismatch or no matched classifications) or infrastructure failure (missing/malformed input files). Operators must always parse `mechanism_check_verdict` from the output JSON to distinguish PASS from INCONCLUSIVE.
```

- [ ] **Step 12.3: Fix `validate.md` halt condition table — split benchmark exit 2 into two rows**

In `prompts/validate.md`, find the "Benchmark infrastructure error" row in the Halt Conditions table:

**Old:**
```
| Benchmark infrastructure error | benchmark exit 2 (file missing or invalid JSON — check stderr; OR ERROR verdict meaning no matched workloads — check `mechanism_check_verdict` in output JSON) | HALT: "benchmark infrastructure error" |
```

**New:**
```
| Benchmark input failure | benchmark exit 2 with no JSON output (file missing or invalid JSON) — check stderr only | HALT: "benchmark infrastructure error — see stderr" |
| Benchmark ERROR verdict | benchmark exit 2 with JSON output written — `mechanism_check_verdict` is ERROR (all workloads skipped due to name mismatch, or no matched signal classifications) | HALT: "benchmark ERROR — check workload names and signal_coverage.json" |
```

- [ ] **Step 12.4: Fix `validation_results.schema.json` — update `operator_notes` description**

In `tools/schemas/validation_results.schema.json`, find and replace the `operator_notes` description at line ~85:

**Old:**
```json
      "description": "Optional. Required when overall_verdict is INCONCLUSIVE (operator sign-off via Step 5 Option 4). Documents the rationale for proceeding despite INCONCLUSIVE benchmark verdict."
```

**New:**
```json
      "description": "Optional. Required when overall_verdict is INCONCLUSIVE (operator sign-off via Step 5c option 3). Documents the rationale for proceeding despite INCONCLUSIVE benchmark verdict."
```

- [ ] **Step 12.5: Fix `generate.md` halt table — add halt_reason values for n/a rows**

In `prompts/generate.md`, find the two rows with `n/a` halt_reason in the Halt Conditions table:

**Old:**
```
| inference-sim tag unresolved | n/a | HALT (Stage 8) |
| tekton_artifacts schema validation | n/a | HALT (Stage 8) |
```

**New:**
```
| inference-sim tag unresolved | `inference_sim_tag_unresolved_stage3` | HALT (Stage 8): write `workspace/escalation.json` with `"stage": 3` and this halt_reason |
| tekton_artifacts schema validation | `tekton_artifacts_validation_failure_stage3` | HALT (Stage 8): write `workspace/escalation.json` with `"stage": 3` and this halt_reason |
```

- [ ] **Step 12.6: Fix module docstring in `transfer_cli.py`**

In `tools/transfer_cli.py`, replace lines 4–10:

**Old:**
```python
Commands:
    extract <routing_dir>    Parse EVOLVE-BLOCK, produce algorithm_summary.json
    validate-mapping         Check mapping artifact completeness
    validate-schema <path>   Validate workspace artifact against JSON Schema
    test-status              Classify go build/test output (stdin) into error classes
    benchmark                Compute T_eff and mechanism check from noise/baseline/treatment results
```

**New:**
```python
Commands:
    extract <routing_dir>    Parse EVOLVE-BLOCK, produce algorithm_summary.json
    validate-mapping         Check mapping artifact completeness
    validate-schema <path>   Validate workspace artifact against JSON Schema
    test-status              Classify go build/test output (stdin) into error classes
    benchmark                Compute T_eff and mechanism check from noise/baseline/treatment results
    convert-trace            Convert blis observe TraceV2 output to metrics JSON
    benchmark-state          Read/write workspace/benchmark_state.json phase tracking
    compile-pipeline         Compile a tektonc pipeline template for a given phase
    render-pipelinerun       Substitute variables in a PipelineRun stub
    preflight                Run pre-flight cluster checks before submitting a pipeline phase
    generate-evidence        Generate workspace/transfer_evidence.md from workspace artifacts
```

- [ ] **Step 12.7: Run full test suite to verify no regressions from doc changes**

```bash
python -m pytest tools/ -v
```

Expected: all pass.

- [ ] **Step 12.8: Commit**

```bash
git add prompts/extract.md CLAUDE.md prompts/validate.md tools/schemas/validation_results.schema.json prompts/generate.md tools/transfer_cli.py
git commit -m "docs: fix false preflight claim, benchmark exit codes, halt table, schema description, module docstring"
```

---

## Final Verification

- [ ] **Run full test suite one last time**

```bash
cd .worktrees/pr5-tektonc-benchmarking
python -m pytest tools/ -v 2>&1 | tail -20
```

Expected: all tests pass, no failures.

- [ ] **Quick sanity check: count new tests added**

```bash
git log main..HEAD --oneline | head -20
python -m pytest tools/ --collect-only -q 2>/dev/null | tail -5
```

All 12 tasks produce separate commits so the review history is clean.
