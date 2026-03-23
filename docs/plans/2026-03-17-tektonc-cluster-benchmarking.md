# Tektonc Cluster Benchmarking Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the two "OPERATOR ACTION REQUIRED" manual steps in Stage 5 with automated Tekton pipelines driven by new `transfer_cli.py` subcommands.

**Architecture:** Three focused Tekton pipelines (noise/baseline/treatment) submit against a real cluster; `workspace/benchmark_state.json` tracks per-phase completion so Stage 5 is resumable after cluster failures. Results are extracted from a shared PVC via extractor pod, converted locally by `convert-trace`, and fed into a reworked `benchmark` command that computes T_eff internally.

**Tech Stack:** Python 3.10+ stdlib + jinja2 + PyYAML (new); pytest; Tekton Pipelines v1; tektonc Jinja2 template compiler (`tektonc-data-collection/tektonc/tektonc.py`).

**Spec:** `docs/plans/2026-03-17-tektonc-cluster-benchmarking-design.md`

## Deviations from CLAUDE.md Constraints

| # | Constraint Broken | Scope | Rationale |
|---|---|---|---|
| D-1 | `stdlib-only` Python | `compile-pipeline` and `preflight` subcommands only | Jinja2 and PyYAML are required to compile Tekton YAML templates. No stdlib-only alternative for Jinja2 template rendering exists. Impact is isolated to the two new subcommands; all other `transfer_cli.py` subcommands remain stdlib-only. Install via `pip install -r requirements.txt`. |

---

## File Map

### New files
| File | Purpose |
|---|---|
| `requirements.txt` | jinja2, PyYAML deps for compile-pipeline and preflight |
| `tools/schemas/noise_results.schema.json` | Validates convert-trace noise output (per-run structure) |
| `tools/schemas/baseline_results.schema.json` | Validates convert-trace baseline output (single-value) |
| `tools/schemas/treatment_results.schema.json` | Validates convert-trace treatment output (identical to baseline) |
| `tools/schemas/benchmark_output.schema.json` | Validates benchmark --out JSON |
| `tools/workload_signal_mapping.json` | Maps workload YAML fields to sim signal names for classification |
| `tektonc-data-collection/tekton/tasks/run-workload-blis-observe.yaml` | Tekton task wrapping `blis observe` |
| `tektonc-data-collection/tekton/tasks/collect-results.yaml` | Synchronization barrier task |
| `tektonc-data-collection/tekton/noise-pipeline.yaml.j2` | Noise pipeline template (5×N workloads cartesian) |
| `tektonc-data-collection/tekton/baseline-pipeline.yaml.j2` | Baseline pipeline template |
| `tektonc-data-collection/tekton/treatment-pipeline.yaml.j2` | Treatment pipeline template |

### Modified files
| File | Change |
|---|---|
| `tools/transfer_cli.py` | Add subcommands: benchmark-state, preflight, convert-trace, compile-pipeline, render-pipelinerun, generate-evidence; rewrite benchmark interface; remove noise-characterize |
| `tools/test_transfer_cli.py` | Add test classes for all new subcommands |
| `prompts/validate.md` | Replace Steps 1 and 5 (OPERATOR ACTION REQUIRED blocks) |
| `prompts/extract.md` | Add Stage 1 prerequisite check for blis observe availability |
| `prompts/generate.md` | Add Stage 3 workspace/tekton/ artifact generation |
| `CLAUDE.md` | Add jinja2/PyYAML note; remove noise-characterize; update benchmark CLI docs |
| `docs/transfer/blis_to_llmd_mapping.md` | Add ## Submodule Prerequisites section |

### Already exists — verify only, do not recreate
- `tools/schemas/benchmark_state.schema.json`
- `tools/schemas/stage3_output.schema.json` (already has `tekton_artifacts` property)
- `tektonc-data-collection/tektonc/tektonc.py` (Jinja2 template compiler; requires submodule init)

---

## Chunk 1: Foundation

### Task 1: requirements.txt + JSON schemas + workload signal mapping

**Files:**
- Create: `requirements.txt`
- Create: `tools/schemas/noise_results.schema.json`
- Create: `tools/schemas/baseline_results.schema.json`
- Create: `tools/schemas/treatment_results.schema.json`
- Create: `tools/schemas/benchmark_output.schema.json`
- Create: `tools/workload_signal_mapping.json`

- [ ] **Step 1.1: Create requirements.txt**

```
jinja2>=3.1.0
PyYAML>=6.0
```

- [ ] **Step 1.2: Install into .venv and verify**

```bash
source .venv/bin/activate && pip install -r requirements.txt
python -c "import jinja2, yaml; print('OK')"
```
Expected: `OK`

- [ ] **Step 1.3: Verify existing schemas are intact**

```bash
python -c "
import json, pathlib
s = json.load(open('tools/schemas/benchmark_state.schema.json'))
assert 'phases' in s['properties']
s2 = json.load(open('tools/schemas/stage3_output.schema.json'))
assert 'tekton_artifacts' in s2['properties']
# Verify sub-schema permits pipeline_stubs as an array (plan depends on this structure)
ta = s2['properties']['tekton_artifacts']
ta_props = ta.get('properties', {})
assert 'pipeline_stubs' in ta_props, 'stage3_output.schema.json missing pipeline_stubs in tekton_artifacts'
assert ta_props['pipeline_stubs'].get('type') == 'array', 'pipeline_stubs must be type array'
assert 'values_yaml' in ta_props, 'stage3_output.schema.json missing values_yaml in tekton_artifacts'
# Verify tektonc.py exists (required by compile-pipeline; fails early if submodule not initialized)
tektonc = pathlib.Path('tektonc-data-collection/tektonc/tektonc.py')
assert tektonc.exists(), f'tektonc not found at {tektonc} — run: git submodule update --init tektonc-data-collection'
print('OK')
"
```
Expected: `OK`

- [ ] **Step 1.4: Create tools/schemas/noise_results.schema.json**

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "Noise Results",
  "type": "object",
  "required": ["workloads"],
  "additionalProperties": false,
  "properties": {
    "workloads": {
      "type": "array", "minItems": 1,
      "items": {
        "type": "object",
        "required": ["name", "runs"],
        "additionalProperties": false,
        "properties": {
          "name": {"type": "string"},
          "runs": {
            "type": "array", "minItems": 1,
            "items": {
              "type": "object", "required": ["metrics"], "additionalProperties": false,
              "properties": {"metrics": {"$ref": "#/$defs/metrics"}}
            }
          }
        }
      }
    }
  },
  "$defs": {
    "metrics": {
      "type": "object",
      "required": ["ttft_p50", "ttft_p99", "tpot_p50", "tpot_p99"],
      "additionalProperties": false,
      "properties": {
        "ttft_p50": {"type": "number"}, "ttft_p99": {"type": "number"},
        "tpot_p50": {"type": "number"}, "tpot_p99": {"type": "number"}
      }
    }
  }
}
```

- [ ] **Step 1.5: Create tools/schemas/baseline_results.schema.json**

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "Baseline Results",
  "type": "object",
  "required": ["workloads"],
  "additionalProperties": false,
  "properties": {
    "workloads": {
      "type": "array", "minItems": 1,
      "items": {
        "type": "object",
        "required": ["name", "metrics"],
        "additionalProperties": false,
        "properties": {
          "name": {"type": "string"},
          "metrics": {
            "type": "object",
            "required": ["ttft_p50", "ttft_p99", "tpot_p50", "tpot_p99"],
            "additionalProperties": false,
            "properties": {
              "ttft_p50": {"type": "number"}, "ttft_p99": {"type": "number"},
              "tpot_p50": {"type": "number"}, "tpot_p99": {"type": "number"}
            }
          }
        }
      }
    }
  }
}
```

- [ ] **Step 1.6: Create tools/schemas/treatment_results.schema.json** — identical to baseline but `"title": "Treatment Results"`.

- [ ] **Step 1.7: Create tools/schemas/benchmark_output.schema.json**

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "Benchmark Output",
  "type": "object",
  "required": ["t_eff", "mechanism_check_verdict", "passed", "workload_classification", "specificity_notes", "noise_cv"],
  "additionalProperties": false,
  "properties": {
    "t_eff": {"type": "number", "minimum": 0},
    "noise_cv": {"type": "number", "minimum": 0},
    "mechanism_check_verdict": {"type": "string", "enum": ["PASS", "FAIL", "INCONCLUSIVE", "ERROR"]},
    "passed": {"type": "boolean"},
    "workload_classification": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["workload", "classification", "improvement", "matched_signals"],
        "additionalProperties": false,
        "properties": {
          "workload": {"type": "string"},
          "classification": {"type": "string", "enum": ["matched", "unmatched"]},
          "improvement": {"type": "number"},
          "matched_signals": {"type": "array", "items": {"type": "string"}}
        }
      }
    },
    "specificity_notes": {"type": "array", "items": {"type": "string"}}
  }
}
```

- [ ] **Step 1.8: Create tools/workload_signal_mapping.json**

```json
{
  "mappings": [
    {"workload_field": "queue_depth_range", "signals": ["QueueDepth"], "description": "Queue depth range exercises QueueDepth signal"},
    {"workload_field": "kv_util_range", "signals": ["KVUtilization"], "description": "KV utilization range exercises KVUtilization signal"},
    {"workload_field": "kv_utilization", "signals": ["KVUtilization"], "description": "KV utilization value exercises KVUtilization signal"},
    {"workload_field": "in_flight_range", "signals": ["InFlightRequests"], "description": "In-flight range exercises InFlightRequests signal"},
    {"workload_field": "in_flight_requests", "signals": ["InFlightRequests"], "description": "In-flight requests exercises InFlightRequests signal"},
    {"workload_field": "cache_hit_rate", "signals": ["CacheHitRate"], "description": "Cache hit rate exercises CacheHitRate signal"},
    {"workload_field": "prefix_group", "signals": ["KVUtilization"], "description": "Prefix groups drive KV block reuse (shared prefix caching occupies KV cache), exercising KVUtilization"}
  ]
}
```

- [ ] **Step 1.9: Validate all schemas parse**

```bash
python -c "
import json, pathlib
for p in pathlib.Path('tools/schemas').glob('*.json'):
    json.load(open(p)); print('OK:', p.name)
"
```
Expected: `OK:` for every file.

- [ ] **Step 1.9b: Verify validate-schema command supports new result file types**

`validate-schema` uses the artifact stem to locate the schema (e.g., `noise_results.json` → `tools/schemas/noise_results.schema.json`). Confirm the naming convention resolves correctly for all three new result types:

```bash
# Create minimal valid fixtures and confirm validate-schema returns 0
python -c "
import json, pathlib
# noise_results: requires workloads array with name+runs+metrics
pathlib.Path('/tmp/noise_results.json').write_text(json.dumps({
    'workloads': [{'name': 'glia-40qps', 'runs': [{'metrics': {
        'ttft_p50': 1.0, 'ttft_p99': 2.0, 'tpot_p50': 0.5, 'tpot_p99': 1.0}}]}]
}))
# baseline_results: requires workloads array with name+metrics
pathlib.Path('/tmp/baseline_results.json').write_text(json.dumps({
    'workloads': [{'name': 'glia-40qps', 'metrics': {
        'ttft_p50': 1.0, 'ttft_p99': 2.0, 'tpot_p50': 0.5, 'tpot_p99': 1.0}}]
}))
# treatment_results: same schema as baseline
pathlib.Path('/tmp/treatment_results.json').write_text(
    pathlib.Path('/tmp/baseline_results.json').read_text())
"
for f in noise_results baseline_results treatment_results; do
  cp /tmp/${f}.json workspace/${f}.json
  .venv/bin/python tools/transfer_cli.py validate-schema workspace/${f}.json \
    && echo "OK: validate-schema $f" \
    || echo "FAIL: validate-schema $f — check schema file name matches artifact stem"
  rm workspace/${f}.json
done
```
Expected: `OK: validate-schema` for all three.

- [ ] **Step 1.10: Commit**

```bash
git add requirements.txt tools/schemas/noise_results.schema.json \
  tools/schemas/baseline_results.schema.json \
  tools/schemas/treatment_results.schema.json \
  tools/schemas/benchmark_output.schema.json \
  tools/workload_signal_mapping.json
git commit -m "feat(schemas): add result schemas and workload signal mapping for tektonc benchmarking"
```

---

## Chunk 2: State Management

### Task 2: `benchmark-state` subcommand

**Files:**
- Modify: `tools/transfer_cli.py` — add `cmd_benchmark_state` function and register parser
- Modify: `tools/test_transfer_cli.py` — add `class TestBenchmarkState`

- [ ] **Step 2.1: Write failing tests**

Add to `tools/test_transfer_cli.py`:

```python
class TestBenchmarkState:
    def _alg_summary(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        # Use schema-valid fields only (algorithm_summary.schema.json has
        # additionalProperties: false). Fields like scope_verdict, signals_used,
        # per_workload_results, branch_count are NOT in the schema and would fail
        # validate-schema. cmd_benchmark_state only reads algorithm_name, so this
        # minimal subset is sufficient for the command under test.
        (ws / "algorithm_summary.json").write_text(
            '{"algorithm_name": "test-algo", "scope_validation_passed": true,'
            ' "fidelity_checked": true, "evolve_block_source": "blis_router/best/best_program.go:1-10",'
            ' "evolve_block_content_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
            ' "signals": [{"name": "KVUtilization", "type": "float64", "access_path": "kv"}],'
            ' "composite_signals": [], "metrics": {"combined_score": 1.5},'
            ' "mapping_artifact_version": "1.0"}'
        )
        return ws

    def test_creates_state_file_when_absent(self, tmp_path):
        ws = self._alg_summary(tmp_path)
        from tools.transfer_cli import cmd_benchmark_state
        import argparse
        args = argparse.Namespace(workspace=str(ws), namespace="test-ns",
                                  set_phase=None, force=False)
        rc = cmd_benchmark_state(args)
        assert rc == 0
        import json
        state = json.loads((ws / "benchmark_state.json").read_text())
        assert state["algorithm_name"] == "test-algo"
        assert state["namespace"] == "test-ns"
        assert state["phases"]["noise"]["status"] == "pending"
        assert state["phases"]["baseline"]["status"] == "pending"
        assert state["phases"]["treatment"]["status"] == "pending"

    def test_context_guard_warns_on_mismatch(self, tmp_path, monkeypatch):
        ws = self._alg_summary(tmp_path)
        import json
        state = {
            "schema_version": 1, "algorithm_name": "test-algo",
            "created_at": "2026-01-01T00:00:00Z",
            "cluster_context": "original-cluster", "namespace": "test-ns",
            "phases": {
                "noise":     {"status": "pending", "pipelinerun_name": None,
                              "submitted_at": None, "completed_at": None,
                              "results_pvc_path": "noise/", "results_local_path": None,
                              "failure_reason": None},
                "baseline":  {"status": "pending", "pipelinerun_name": None,
                              "submitted_at": None, "completed_at": None,
                              "results_pvc_path": "baseline/", "results_local_path": None,
                              "failure_reason": None},
                "treatment": {"status": "pending", "pipelinerun_name": None,
                              "submitted_at": None, "completed_at": None,
                              "results_pvc_path": "treatment/", "results_local_path": None,
                              "failure_reason": None},
            }
        }
        (ws / "benchmark_state.json").write_text(json.dumps(state))
        monkeypatch.setattr("tools.transfer_cli._kubectl_current_context",
                            lambda: "different-cluster")
        from tools.transfer_cli import cmd_benchmark_state
        import argparse
        args = argparse.Namespace(workspace=str(ws), namespace=None,
                                  set_phase=None, force=False)
        rc = cmd_benchmark_state(args)
        assert rc == 1

    def test_set_phase_updates_status(self, tmp_path):
        ws = self._alg_summary(tmp_path)
        import json, argparse
        from tools.transfer_cli import cmd_benchmark_state
        # create
        args = argparse.Namespace(workspace=str(ws), namespace="ns",
                                  set_phase=None, force=False)
        cmd_benchmark_state(args)
        # set noise to done
        args2 = argparse.Namespace(workspace=str(ws), namespace=None,
                                   set_phase="noise", status="done",
                                   pipelinerun=None, results=None,
                                   failure_reason=None, force=False)
        rc = cmd_benchmark_state(args2)
        assert rc == 0
        state = json.loads((ws / "benchmark_state.json").read_text())
        assert state["phases"]["noise"]["status"] == "done"

    def test_ordering_guard_blocks_baseline_before_noise(self, tmp_path):
        ws = self._alg_summary(tmp_path)
        import argparse
        from tools.transfer_cli import cmd_benchmark_state
        cmd_benchmark_state(argparse.Namespace(workspace=str(ws), namespace="ns",
                                               set_phase=None, force=False))
        args = argparse.Namespace(workspace=str(ws), namespace=None,
                                  set_phase="baseline", status="running",
                                  pipelinerun="pr-1", results=None,
                                  failure_reason=None, force=False)
        rc = cmd_benchmark_state(args)
        assert rc == 1  # ordering violation: noise not done yet (exit 1 = workflow error, not infrastructure error)

    def test_missing_algorithm_summary_exits_2(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        import argparse
        from tools.transfer_cli import cmd_benchmark_state
        args = argparse.Namespace(workspace=str(ws), namespace="ns",
                                  set_phase=None, force=False)
        rc = cmd_benchmark_state(args)
        assert rc == 2
```

- [ ] **Step 2.2: Run tests to confirm they fail**

```bash
python -m pytest tools/test_transfer_cli.py::TestBenchmarkState -v 2>&1 | tail -15
```
Expected: all 5 tests fail with `ImportError` or `AttributeError` (function doesn't exist yet).

- [ ] **Step 2.3: Implement `cmd_benchmark_state` in transfer_cli.py**

Add a helper function and the command function before `main()`:

```python
def _kubectl_current_context() -> str:
    """Return current kubectl context, or empty string on error."""
    import subprocess
    try:
        r = subprocess.run(
            ["kubectl", "config", "current-context"],
            capture_output=True, text=True, timeout=10
        )
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def _default_benchmark_state(algorithm_name: str, namespace: str, context: str) -> dict:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    phase_template = {
        "status": "pending", "pipelinerun_name": None,
        "submitted_at": None, "completed_at": None,
        "results_local_path": None, "failure_reason": None,
    }
    return {
        "schema_version": 1,
        "algorithm_name": algorithm_name,
        "created_at": now,
        "cluster_context": context,
        "namespace": namespace,
        "phases": {
            "noise":     {**phase_template, "results_pvc_path": "noise/"},
            "baseline":  {**phase_template, "results_pvc_path": "baseline/"},
            "treatment": {**phase_template, "results_pvc_path": "treatment/"},
        }
    }


_PHASE_ORDER = ["noise", "baseline", "treatment"]


def cmd_benchmark_state(args: "argparse.Namespace") -> int:
    import json
    from datetime import datetime, timezone
    from pathlib import Path

    ws = Path(args.workspace)
    state_path = ws / "benchmark_state.json"

    # ---- read or create ----
    if not state_path.exists():
        alg_path = ws / "algorithm_summary.json"
        if not alg_path.exists():
            print(f"ERROR: {alg_path} not found — run Stage 1 extract first.",
                  file=sys.stderr)
            return 2
        try:
            alg = json.loads(alg_path.read_text())
            alg_name = alg["algorithm_name"]
        except Exception as e:
            print(f"ERROR: cannot read algorithm_name from {alg_path}: {e}",
                  file=sys.stderr)
            return 2
        if not getattr(args, "namespace", None):
            print("ERROR: --namespace required on first invocation.", file=sys.stderr)
            return 2
        ctx = _kubectl_current_context()
        state = _default_benchmark_state(alg_name, args.namespace, ctx)
        state_path.write_text(json.dumps(state, indent=2))
    else:
        try:
            state = json.loads(state_path.read_text())
        except Exception as e:
            print(f"ERROR: cannot parse {state_path}: {e}", file=sys.stderr)
            return 2
        if not isinstance(state, dict) or "phases" not in state:
            print(
                f"ERROR: {state_path} is missing 'phases' key or is not a dict — "
                "file may be corrupted. Delete it to start fresh.",
                file=sys.stderr,
            )
            return 2
        for expected_phase in _PHASE_ORDER:
            if expected_phase not in state["phases"]:
                print(
                    f"ERROR: {state_path} 'phases' dict is missing phase '{expected_phase}' — "
                    "file may be corrupted. Delete it to start fresh.",
                    file=sys.stderr,
                )
                return 2

    # ---- context guard (read-only calls) ----
    if not getattr(args, "set_phase", None):
        current_ctx = _kubectl_current_context()
        recorded_ctx = state.get("cluster_context", "")
        if recorded_ctx and current_ctx and current_ctx != recorded_ctx:
            print(
                f"ERROR: State was recorded against cluster '{recorded_ctx}' "
                f"but current context is '{current_ctx}'. "
                "Delete workspace/benchmark_state.json to start fresh against "
                "the new cluster, or switch back to the original context.",
                file=sys.stderr,
            )
            return 1
        print(json.dumps(state, indent=2))
        return 0

    # ---- set-phase update ----
    phase = args.set_phase
    if phase not in _PHASE_ORDER:
        print(f"ERROR: unknown phase '{phase}'. Must be one of {_PHASE_ORDER}.",
              file=sys.stderr)
        return 2

    new_status = args.status
    if new_status is None:
        print("ERROR: --status is required when --set-phase is used.", file=sys.stderr)
        return 2
    current_status = state["phases"][phase]["status"]

    # status regression guard
    if not getattr(args, "force", False):
        if current_status == "done" and new_status in ("pending", "running"):
            print(
                f"ERROR: cannot regress phase '{phase}' from 'done' to '{new_status}'. "
                "Use --force to override.", file=sys.stderr
            )
            return 2

    # ordering guard: when setting to 'running', previous phase must be 'done'
    # Returns exit 1 (workflow/user error) to distinguish from exit 2 (infrastructure error).
    # validate.md Step 1 treats exit 2 as missing algorithm_summary.json, not ordering violation.
    if new_status == "running" and not getattr(args, "force", False):
        idx = _PHASE_ORDER.index(phase)
        if idx > 0:
            prev = _PHASE_ORDER[idx - 1]
            if state["phases"][prev]["status"] != "done":
                print(
                    f"ERROR: cannot set '{phase}' to running — "
                    f"previous phase '{prev}' is not done (status: "
                    f"'{state['phases'][prev]['status']}'). "
                    "Use --force to bypass.", file=sys.stderr
                )
                return 1

    state["phases"][phase]["status"] = new_status
    if getattr(args, "pipelinerun", None):
        state["phases"][phase]["pipelinerun_name"] = args.pipelinerun
        state["phases"][phase]["submitted_at"] = (
            datetime.now(timezone.utc).isoformat(timespec="seconds")
        )
    if getattr(args, "results", None):
        state["phases"][phase]["results_local_path"] = args.results
        state["phases"][phase]["completed_at"] = (
            datetime.now(timezone.utc).isoformat(timespec="seconds")
        )
    if getattr(args, "failure_reason", None):
        state["phases"][phase]["failure_reason"] = args.failure_reason

    state_path.write_text(json.dumps(state, indent=2))
    return 0
```

- [ ] **Step 2.4: Register parser in `main()`** (add before the final `return main()` in the parser block)

```python
p_bstate = subparsers.add_parser("benchmark-state",
    help="Read/write workspace/benchmark_state.json phase tracking")
p_bstate.add_argument("--workspace", required=True)
p_bstate.add_argument("--namespace")
p_bstate.add_argument("--set-phase", dest="set_phase",
                       choices=["noise", "baseline", "treatment"])
p_bstate.add_argument("--status",
                       choices=["pending", "running", "done", "failed"])
p_bstate.add_argument("--pipelinerun")
p_bstate.add_argument("--results")
p_bstate.add_argument("--failure-reason", dest="failure_reason")
p_bstate.add_argument("--force", action="store_true")
p_bstate.set_defaults(func=cmd_benchmark_state)
```

- [ ] **Step 2.5: Run tests — all 5 must pass**

```bash
python -m pytest tools/test_transfer_cli.py::TestBenchmarkState -v
```
Expected: 5 passed.

- [ ] **Step 2.6: Run full test suite — no regressions**

```bash
python -m pytest tools/ -v 2>&1 | tail -5
```
Expected: same pass count as before (no new failures).

- [ ] **Step 2.7: Commit**

```bash
git add tools/transfer_cli.py tools/test_transfer_cli.py
git commit -m "feat(cli): add benchmark-state subcommand with context guard and ordering guard"
```

---

## Chunk 3: Data Conversion

### Task 3: `convert-trace` subcommand

**Files:**
- Modify: `tools/transfer_cli.py` — add `cmd_convert_trace` and register parser
- Modify: `tools/test_transfer_cli.py` — add `class TestConvertTrace`

TraceV2 format: `blis observe` writes `trace_header.yaml` + `trace_data.csv` per workload run.
CSV columns used: `send_time_us`, `first_chunk_time_us`, `last_chunk_time_us`, `num_chunks`, `status`.
Derived: `ttft = (first_chunk_time_us - send_time_us) / 1000.0` ms; `tpot = (last_chunk_time_us - first_chunk_time_us) / max(num_chunks-1, 1) / 1000.0` ms.
Auto-detect: if `run-*/` subdirectories exist → noise (per-run output); else → baseline/treatment (single-value output).

- [ ] **Step 3.1: Write failing tests**

Add to `tools/test_transfer_cli.py`:

```python
import csv, textwrap

def _write_tracev2(directory, rows):
    """Write minimal TraceV2 files. rows = list of dicts with CSV fields."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "trace_header.yaml").write_text(
        "trace_version: 2\ntime_unit: microseconds\nmode: real\n"
    )
    fieldnames = ["request_id", "send_time_us", "first_chunk_time_us",
                  "last_chunk_time_us", "num_chunks", "status", "error_message"]
    with open(directory / "trace_data.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            row = {k: "" for k in fieldnames}
            row.update(r)
            w.writerow(row)


class TestConvertTrace:
    def test_baseline_single_workload(self, tmp_path):
        wl_dir = tmp_path / "baseline" / "glia-40qps"
        _write_tracev2(wl_dir, [
            {"send_time_us": "0", "first_chunk_time_us": "100000",
             "last_chunk_time_us": "200000", "num_chunks": "5", "status": "ok"},
            {"send_time_us": "0", "first_chunk_time_us": "120000",
             "last_chunk_time_us": "220000", "num_chunks": "5", "status": "ok"},
        ])
        out = tmp_path / "baseline_results.json"
        from tools.transfer_cli import cmd_convert_trace
        import argparse
        args = argparse.Namespace(input_dir=str(tmp_path / "baseline"),
                                  output=str(out))
        rc = cmd_convert_trace(args)
        assert rc == 0
        import json
        result = json.loads(out.read_text())
        assert result["workloads"][0]["name"] == "glia-40qps"
        m = result["workloads"][0]["metrics"]
        assert "ttft_p50" in m and "ttft_p99" in m
        assert m["ttft_p50"] == 100.0   # 100000 us / 1000

    def test_noise_per_run_structure(self, tmp_path):
        for i in range(3):
            wl_dir = tmp_path / "noise" / "glia-40qps" / f"run-{i}"
            _write_tracev2(wl_dir, [
                {"send_time_us": "0", "first_chunk_time_us": str(100000 + i*1000),
                 "last_chunk_time_us": str(200000 + i*1000),
                 "num_chunks": "4", "status": "ok"},
            ])
        out = tmp_path / "noise_results.json"
        from tools.transfer_cli import cmd_convert_trace
        import argparse
        args = argparse.Namespace(input_dir=str(tmp_path / "noise"),
                                  output=str(out))
        rc = cmd_convert_trace(args)
        assert rc == 0
        import json
        result = json.loads(out.read_text())
        wl = result["workloads"][0]
        assert wl["name"] == "glia-40qps"
        assert "runs" in wl
        assert len(wl["runs"]) == 3

    def test_all_failed_rows_exits_1(self, tmp_path):
        wl_dir = tmp_path / "baseline" / "broken-workload"
        _write_tracev2(wl_dir, [
            {"send_time_us": "0", "first_chunk_time_us": "0",
             "last_chunk_time_us": "0", "num_chunks": "0", "status": "timeout"},
        ])
        from tools.transfer_cli import cmd_convert_trace
        import argparse
        args = argparse.Namespace(input_dir=str(tmp_path / "baseline"),
                                  output=str(tmp_path / "out.json"))
        rc = cmd_convert_trace(args)
        assert rc == 1

    def test_missing_csv_exits_1(self, tmp_path):
        wl_dir = tmp_path / "baseline" / "glia-40qps"
        wl_dir.mkdir(parents=True)
        (wl_dir / "trace_header.yaml").write_text("trace_version: 2\n")
        # no trace_data.csv
        from tools.transfer_cli import cmd_convert_trace
        import argparse
        args = argparse.Namespace(input_dir=str(tmp_path / "baseline"),
                                  output=str(tmp_path / "out.json"))
        rc = cmd_convert_trace(args)
        assert rc == 1
```

- [ ] **Step 3.2: Run tests — confirm all fail**

```bash
python -m pytest tools/test_transfer_cli.py::TestConvertTrace -v 2>&1 | tail -10
```
Expected: 4 tests fail with `AttributeError`.

- [ ] **Step 3.3: Implement `cmd_convert_trace` in transfer_cli.py**

```python
def _percentile(values: list, p: int) -> float:
    """Compute p-th percentile of a sorted list (nearest-rank, ceiling method).

    Uses ceil(N*p/100) - 1 to avoid systematic underestimation for small N.
    For N=2, p=99: ceil(2*0.99) - 1 = 1 → returns values[1] (correct).
    The floor-based formula max(0, int(N*p/100)-1) returns index 0 for N=2,p=99 (wrong).
    """
    import math
    if not values:
        return 0.0
    values = sorted(values)
    idx = min(len(values) - 1, max(0, math.ceil(len(values) * p / 100) - 1))
    return values[idx]


def _parse_tracev2_dir(directory: "Path") -> dict:
    """Parse a single TraceV2 directory → metrics dict. Raises on error."""
    import csv as csv_mod
    header_path = directory / "trace_header.yaml"
    data_path = directory / "trace_data.csv"
    if not header_path.exists():
        raise FileNotFoundError(
            f"missing trace_header.yaml in {directory} — blis observe may have crashed mid-write."
        )
    if not data_path.exists():
        raise FileNotFoundError(
            f"missing trace_data.csv in {directory} — blis observe may have crashed mid-write."
        )
    ttft_vals, tpot_vals = [], []
    try:
        with open(data_path, newline="") as f:
            reader = csv_mod.DictReader(f)
            for row in reader:
                if row.get("status") != "ok":
                    continue
                send = int(row["send_time_us"])
                first = int(row["first_chunk_time_us"])
                last = int(row["last_chunk_time_us"])
                chunks = max(int(row["num_chunks"]) - 1, 1)
                ttft_vals.append((first - send) / 1000.0)
                tpot_vals.append((last - first) / chunks / 1000.0)
    except (ValueError, KeyError) as e:
        raise ValueError(
            f"malformed CSV in {data_path}: {e} — check that all numeric columns "
            "(send_time_us, first_chunk_time_us, last_chunk_time_us, num_chunks) "
            "contain valid integers for rows with status='ok'"
        ) from e
    return {
        "ttft_p50": _percentile(ttft_vals, 50),
        "ttft_p99": _percentile(ttft_vals, 99),
        "tpot_p50": _percentile(tpot_vals, 50),
        "tpot_p99": _percentile(tpot_vals, 99),
        "_valid_rows": len(ttft_vals),
    }


def cmd_convert_trace(args: "argparse.Namespace") -> int:
    import json
    from pathlib import Path

    input_dir = Path(args.input_dir)
    output = Path(args.output)

    if not input_dir.is_dir():
        print(f"ERROR: input directory '{input_dir}' does not exist.", file=sys.stderr)
        return 2

    workloads = []
    for wl_dir in sorted(input_dir.iterdir()):
        if not wl_dir.is_dir():
            continue
        wl_name = wl_dir.name
        # auto-detect noise (has run-* subdirs) vs baseline/treatment
        run_dirs = sorted(wl_dir.glob("run-*"))
        if run_dirs:
            # noise per-run structure
            runs = []
            for run_dir in run_dirs:
                try:
                    metrics = _parse_tracev2_dir(run_dir)
                except (FileNotFoundError, ValueError) as e:
                    print(f"ERROR: {e}", file=sys.stderr)
                    return 1
                if metrics["_valid_rows"] == 0:
                    print(
                        f"ERROR: workload '{wl_name}' run '{run_dir.name}' has 0 rows "
                        f"with status 'ok' in {run_dir}/trace_data.csv — "
                        "all requests failed or timed out.",
                        file=sys.stderr,
                    )
                    return 1
                del metrics["_valid_rows"]
                runs.append({"metrics": metrics})
            workloads.append({"name": wl_name, "runs": runs})
        else:
            # baseline/treatment single-value structure
            try:
                metrics = _parse_tracev2_dir(wl_dir)
            except (FileNotFoundError, ValueError) as e:
                print(f"ERROR: {e}", file=sys.stderr)
                return 1
            if metrics["_valid_rows"] == 0:
                print(
                    f"ERROR: workload '{wl_name}' has 0 rows with status 'ok' "
                    f"in {wl_dir}/trace_data.csv — all requests failed or timed out.",
                    file=sys.stderr,
                )
                return 1
            del metrics["_valid_rows"]
            workloads.append({"name": wl_name, "metrics": metrics})

    if not workloads:
        print(f"ERROR: no workload directories found in '{input_dir}'.", file=sys.stderr)
        return 2

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"workloads": workloads}, indent=2))
    return 0
```

- [ ] **Step 3.4: Register parser in `main()`**

```python
p_ct = subparsers.add_parser("convert-trace",
    help="Convert blis observe TraceV2 output to metrics JSON")
p_ct.add_argument("--input-dir", required=True,
                   dest="input_dir",
                   help="Phase directory containing per-workload TraceV2 subdirs")
p_ct.add_argument("--output", required=True,
                   help="Output metrics JSON file path")
p_ct.set_defaults(func=cmd_convert_trace)
```

- [ ] **Step 3.5: Run tests — all 4 must pass**

```bash
python -m pytest tools/test_transfer_cli.py::TestConvertTrace -v
```
Expected: 4 passed.

- [ ] **Step 3.6: Run full suite — no regressions**

```bash
python -m pytest tools/ -v 2>&1 | tail -5
```

- [ ] **Step 3.7: Commit**

```bash
git add tools/transfer_cli.py tools/test_transfer_cli.py
git commit -m "feat(cli): add convert-trace subcommand for TraceV2 → metrics JSON conversion"
```

---

## Chunk 4: Pipeline Compilation and Pre-flight

### Task 4: `compile-pipeline` and `render-pipelinerun`

**Files:**
- Modify: `tools/transfer_cli.py` — add `cmd_compile_pipeline`, `cmd_render_pipelinerun`
- Modify: `tools/test_transfer_cli.py` — add `class TestCompilePipeline`, `class TestRenderPipelinerun`

- [ ] **Step 4.1: Write failing tests**

```python
class TestRenderPipelinerun:
    def test_substitutes_variables(self, tmp_path):
        stub = tmp_path / "stub.yaml"
        stub.write_text(
            "metadata:\n  name: $PIPELINERUN_NAME\n  namespace: ${NAMESPACE}\n"
        )
        out = tmp_path / "rendered.yaml"
        from tools.transfer_cli import cmd_render_pipelinerun
        import argparse
        args = argparse.Namespace(
            template=str(stub),
            vars=["PIPELINERUN_NAME=pr-123", "NAMESPACE=test-ns"],
            out=str(out),
        )
        rc = cmd_render_pipelinerun(args)
        assert rc == 0
        content = out.read_text()
        assert "pr-123" in content
        assert "test-ns" in content

    def test_exits_1_on_unresolved_placeholder(self, tmp_path):
        stub = tmp_path / "stub.yaml"
        stub.write_text("name: $PIPELINERUN_NAME\nns: $NAMESPACE\n")
        out = tmp_path / "rendered.yaml"
        from tools.transfer_cli import cmd_render_pipelinerun
        import argparse
        # Only supply one of two required vars
        args = argparse.Namespace(
            template=str(stub),
            vars=["PIPELINERUN_NAME=pr-456"],
            out=str(out),
        )
        rc = cmd_render_pipelinerun(args)
        assert rc == 1  # $NAMESPACE unresolved


class TestCompilePipeline:
    def test_exits_2_on_missing_template_dir(self, tmp_path):
        from tools.transfer_cli import cmd_compile_pipeline
        import argparse
        args = argparse.Namespace(
            template_dir=str(tmp_path / "nonexistent"),
            values=str(tmp_path / "values.yaml"),
            phase="baseline",
            out=str(tmp_path / "out"),
        )
        rc = cmd_compile_pipeline(args)
        assert rc == 2

    def test_exits_2_on_missing_values_file(self, tmp_path):
        from tools.transfer_cli import cmd_compile_pipeline
        import argparse
        tdir = tmp_path / "tekton"
        tdir.mkdir()
        (tdir / "baseline-pipeline.yaml.j2").write_text("{{ phase }}")
        args = argparse.Namespace(
            template_dir=str(tdir),
            values=str(tmp_path / "nonexistent_values.yaml"),
            phase="baseline",
            out=str(tmp_path / "out"),
        )
        rc = cmd_compile_pipeline(args)
        assert rc == 2

    def test_success_path_produces_output_file(self, tmp_path):
        """compile-pipeline exit 0 and produces output file when template + values present."""
        import argparse, unittest.mock as mock
        from tools.transfer_cli import cmd_compile_pipeline
        tdir = tmp_path / "tekton"
        tdir.mkdir()
        (tdir / "baseline-pipeline.yaml.j2").write_text("phase: {{ phase }}\n")
        vf = tmp_path / "values.yaml"
        vf.write_text("phase: baseline\n")
        out = tmp_path / "out"
        out.mkdir()
        args = argparse.Namespace(
            template_dir=str(tdir),
            values=str(vf),
            phase="baseline",
            out=str(out),
        )
        # cmd_compile_pipeline calls tektonc.py via subprocess — mock subprocess.run
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            rc = cmd_compile_pipeline(args)
        assert rc == 0
```

- [ ] **Step 4.2: Run tests — confirm they fail**

```bash
python -m pytest tools/test_transfer_cli.py::TestRenderPipelinerun tools/test_transfer_cli.py::TestCompilePipeline -v 2>&1 | tail -10
```
Expected: 5 tests fail.

- [ ] **Step 4.3: Implement `cmd_render_pipelinerun`**

```python
import re as _re

def cmd_render_pipelinerun(args: "argparse.Namespace") -> int:
    from pathlib import Path

    template = Path(args.template)
    out = Path(args.out)

    if not template.exists():
        print(f"ERROR: template file '{template}' not found.", file=sys.stderr)
        return 2

    # Parse KEY=VAL pairs
    var_map = {}
    for item in (args.vars or []):
        if "=" not in item:
            print(f"ERROR: --vars entry '{item}' is not KEY=VAL format.", file=sys.stderr)
            return 2
        k, v = item.split("=", 1)
        var_map[k.strip()] = v.strip()

    content = template.read_text()

    # Substitute ${VAR} and $VAR patterns
    def replacer(m):
        name = m.group(1) or m.group(2)
        return var_map[name] if name in var_map else m.group(0)

    rendered = _re.sub(r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)',
                        replacer, content)

    # Check for unresolved placeholders
    remaining = _re.findall(r'\$\{?[A-Za-z_][A-Za-z0-9_]*\}?', rendered)
    if remaining:
        print(
            f"ERROR: unresolved placeholders in rendered output: {remaining}. "
            "Provide all required --vars.", file=sys.stderr
        )
        return 1

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(rendered)
    return 0
```

- [ ] **Step 4.4: Implement `cmd_compile_pipeline`**

```python
def cmd_compile_pipeline(args: "argparse.Namespace") -> int:
    import subprocess
    from pathlib import Path

    template_dir = Path(args.template_dir)
    values_file = Path(args.values)
    phase = args.phase
    out_dir = Path(args.out)

    if not template_dir.is_dir():
        print(f"ERROR: template directory '{template_dir}' not found.", file=sys.stderr)
        return 2
    if not values_file.exists():
        print(f"ERROR: values file '{values_file}' not found.", file=sys.stderr)
        return 2

    template_file = template_dir / f"{phase}-pipeline.yaml.j2"
    if not template_file.exists():
        print(f"ERROR: pipeline template '{template_file}' not found.", file=sys.stderr)
        return 2

    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{phase}-pipeline.yaml"

    tektonc = Path("tektonc-data-collection/tektonc/tektonc.py")
    if not tektonc.exists():
        print(f"ERROR: tektonc not found at '{tektonc}'.", file=sys.stderr)
        return 2

    result = subprocess.run(
        [sys.executable, str(tektonc),
         "-t", str(template_file),
         "-f", str(values_file),
         "-o", str(out_file)],
        capture_output=True, text=True, shell=False, timeout=120
    )
    if result.returncode != 0:
        print(f"ERROR: tektonc compilation failed:\n{result.stderr}", file=sys.stderr)
        return 1

    return 0
```

- [ ] **Step 4.5: Register both parsers in `main()`**

```python
p_cp = subparsers.add_parser("compile-pipeline",
    help="Compile a tektonc pipeline template for a given phase")
p_cp.add_argument("--template-dir", required=True, dest="template_dir")
p_cp.add_argument("--values", required=True)
p_cp.add_argument("--phase", required=True, choices=["noise", "baseline", "treatment"])
p_cp.add_argument("--out", required=True)
p_cp.set_defaults(func=cmd_compile_pipeline)

p_rpr = subparsers.add_parser("render-pipelinerun",
    help="Substitute variables in a PipelineRun stub")
p_rpr.add_argument("--template", required=True)
p_rpr.add_argument("--vars", nargs="+", metavar="KEY=VAL")
p_rpr.add_argument("--out", required=True)
p_rpr.set_defaults(func=cmd_render_pipelinerun)
```

- [ ] **Step 4.6: Run tests — all 5 pass**

```bash
python -m pytest tools/test_transfer_cli.py::TestRenderPipelinerun tools/test_transfer_cli.py::TestCompilePipeline -v
```
Expected: 5 passed.

- [ ] **Step 4.7: Commit**

```bash
git add tools/transfer_cli.py tools/test_transfer_cli.py
git commit -m "feat(cli): add compile-pipeline and render-pipelinerun subcommands"
```

---

### Task 5: `preflight` subcommand

**Files:**
- Modify: `tools/transfer_cli.py` — add `cmd_preflight`
- Modify: `tools/test_transfer_cli.py` — add `class TestPreflight`

The preflight command runs subprocess checks (`kubectl`, `tkn`) and parses `values.yaml`. Tests mock the subprocess calls.

- [ ] **Step 5.1: Write failing tests**

```python
class TestPreflight:
    def _values(self, tmp_path):
        import yaml
        v = {
            "stack": {
                "model": {
                    "helmValues": {
                        "decode": {
                            "replicas": 2,
                            "acceleratorTypes": {
                                "labelKey": "nvidia.com/gpu.product",
                                "labelValues": ["NVIDIA-H100-80GB-HBM3"],
                            }
                        }
                    }
                },
                "scorer": {
                    "baseline": {"configContent": "apiVersion: v1"},
                    "treatment": {"configContent": "apiVersion: v1"},
                }
            },
            "observe": {
                "image": "ghcr.io/inference-sim/blis:v1.0.0",
                "workloads": [{"name": "glia-40qps"}],
                "noise_runs": 5,
            }
        }
        ws = tmp_path / "workspace" / "tekton"
        ws.mkdir(parents=True)
        vf = ws / "values.yaml"
        vf.write_text(yaml.dump(v))
        return vf

    def test_unresolved_tag_fails(self, tmp_path):
        import yaml
        vf = self._values(tmp_path)
        data = yaml.safe_load(vf.read_text())
        data["observe"]["image"] = "ghcr.io/inference-sim/blis:<TAG>"
        vf.write_text(yaml.dump(data))
        from tools.transfer_cli import _preflight_check_values
        errors = _preflight_check_values(vf, "test-ns", "noise")
        assert any("<TAG>" in e for e in errors)

    def test_missing_treatment_config_fails_for_treatment(self, tmp_path):
        import yaml
        vf = self._values(tmp_path)
        data = yaml.safe_load(vf.read_text())
        data["stack"]["scorer"]["treatment"]["configContent"] = ""
        vf.write_text(yaml.dump(data))
        from tools.transfer_cli import _preflight_check_values
        errors = _preflight_check_values(vf, "test-ns", "treatment")
        assert any("treatment" in e.lower() for e in errors)

    def test_noise_phase_skips_treatment_check(self, tmp_path):
        import yaml
        vf = self._values(tmp_path)
        data = yaml.safe_load(vf.read_text())
        data["stack"]["scorer"]["treatment"]["configContent"] = ""
        vf.write_text(yaml.dump(data))
        from tools.transfer_cli import _preflight_check_values
        errors = _preflight_check_values(vf, "test-ns", "noise")
        # treatment check not run for noise phase
        assert not any("treatment" in e.lower() for e in errors)
```

- [ ] **Step 5.2: Run tests — confirm they fail**

```bash
python -m pytest tools/test_transfer_cli.py::TestPreflight -v 2>&1 | tail -10
```
Expected: 3 tests fail.

- [ ] **Step 5.3: Implement `_preflight_check_values` and `cmd_preflight`**

```python
def _preflight_check_values(values_path: "Path", namespace: str, phase: str) -> list:
    """Check values.yaml for issues that don't need kubectl. Returns list of error strings."""
    import yaml
    errors = []
    try:
        data = yaml.safe_load(values_path.read_text())
    except Exception as e:
        return [f"Cannot parse values.yaml: {e}"]

    if not isinstance(data, dict):
        return [f"values.yaml is empty or not a mapping (got {type(data).__name__})"]

    image = (data.get("observe") or {}).get("image", "")
    if "<TAG>" in image:
        errors.append(
            f"FAIL: observe.image contains unresolved <TAG> placeholder '{image}' — "
            "re-run Stage 3 generate to resolve."
        )

    if phase == "treatment":
        cfg = ((data.get("stack") or {}).get("scorer") or {}).get("treatment", {})
        if not cfg.get("configContent", "").strip():
            errors.append(
                "FAIL: scorer.treatment.configContent is empty — "
                "treatment scorer config must be generated by Stage 3."
            )

    return errors


def cmd_preflight(args: "argparse.Namespace") -> int:
    import subprocess
    from pathlib import Path
    import yaml

    phase = args.phase
    values_path = Path(args.values)
    namespace = args.namespace

    checks = []  # list of (label, passed, detail)

    def run(label: str, cmd: list) -> bool:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=30,
                                shell=False)
            ok = r.returncode == 0
        except Exception as e:
            ok = False
        checks.append((label, ok, ""))
        return ok

    # --- values-only checks (no kubectl) ---
    val_errors = _preflight_check_values(values_path, namespace, phase)
    for e in val_errors:
        checks.append((e, False, ""))

    # --- kubectl checks ---
    run("kubectl reachable", ["kubectl", "cluster-info"])
    run("Tekton CRD installed",
        ["kubectl", "get", "crd", "pipelines.tekton.dev"])
    run(f"Namespace '{namespace}' exists",
        ["kubectl", "get", "ns", namespace])
    run("hf-secret present",
        ["kubectl", "get", "secret", "hf-secret", "-n", namespace])
    run("model-pvc present",
        ["kubectl", "get", "pvc", "model-pvc", "-n", namespace])
    run("data-pvc present",
        ["kubectl", "get", "pvc", "data-pvc", "-n", namespace])
    run("tkn CLI present", ["tkn", "version"])

    # GPU nodes check
    try:
        data = yaml.safe_load(values_path.read_text())
        acc = (data.get("stack", {}).get("model", {})
               .get("helmValues", {}).get("decode", {})
               .get("acceleratorTypes", {}))
        label_key = acc.get("labelKey", "")
        label_vals = acc.get("labelValues", [])
        replicas = (data.get("stack", {}).get("model", {})
                    .get("helmValues", {}).get("decode", {})
                    .get("replicas", 1))
        if label_key and label_vals:
            selector = f"{label_key}={label_vals[0]}"
            r = subprocess.run(
                ["kubectl", "get", "nodes", "-l", selector,
                 "--field-selector=status.conditions[?(@.type==\"Ready\")].status=True",
                 "-o", "name"],
                capture_output=True, text=True, timeout=30, shell=False
            )
            count = len([l for l in r.stdout.strip().splitlines() if l])
            ok = count >= replicas
            checks.append((
                f"GPU nodes (≥{replicas} with {selector})", ok,
                f"found {count}"
            ))
    except Exception:
        checks.append(("GPU nodes check", False, "error reading values.yaml"))

    if phase == "treatment":
        # re-build scorer
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

    # Print checklist to stderr (human-readable); output JSON to stdout per module contract
    any_fail = False
    check_results = []
    for label, passed, detail in checks:
        mark = "✓" if passed else "✗"
        suffix = f" ({detail})" if detail else ""
        print(f"  [{mark}] {label}{suffix}", file=sys.stderr)
        if not passed:
            any_fail = True
        check_results.append({"label": label, "passed": passed, "detail": detail})

    rc = 1 if any_fail else 0
    _output("ok" if rc == 0 else "fail", rc,
            **{"phase": phase, "checks": check_results, "passed": not any_fail})
    return rc
```

- [ ] **Step 5.4: Register parser in `main()`**

```python
p_pf = subparsers.add_parser("preflight",
    help="Run pre-flight cluster checks before submitting a pipeline phase")
p_pf.add_argument("--phase", required=True, choices=["noise", "baseline", "treatment"])
p_pf.add_argument("--values", required=True)
p_pf.add_argument("--namespace", required=True)
p_pf.set_defaults(func=cmd_preflight)
```

- [ ] **Step 5.5: Run tests — all 3 pass**

```bash
python -m pytest tools/test_transfer_cli.py::TestPreflight -v
```
Expected: 3 passed.

- [ ] **Step 5.6: Commit**

```bash
git add tools/transfer_cli.py tools/test_transfer_cli.py
git commit -m "feat(cli): add preflight subcommand with values and kubectl checks"
```

---

## Chunk 5: Computation and Evidence

### Task 6: Rewrite `benchmark` subcommand

**Files:**
- Modify: `tools/transfer_cli.py` — replace `_cmd_benchmark` with new implementation, update parser
- Modify: `tools/test_transfer_cli.py` — add `class TestBenchmarkNew`, mark old benchmark tests as legacy

The old interface (`--results`, `--t-eff`) is removed. New interface: `--noise`, `--baseline`, `--treatment`, `--signal-coverage`, `--workloads-dir`, `--out`. T_eff computed internally. Workload classification reads from `signal_coverage.json` + `workload_signal_mapping.json`.

- [ ] **Step 6.1: Write failing tests for new interface**

```python
class TestBenchmarkNew:
    def _make_noise(self, tmp_path, cv=0.05):
        """noise_results.json with controllable CV."""
        import json, math
        # 5 runs, 2 workloads; vary ttft_p99 to produce desired CV
        base = 100.0
        runs = [{"metrics": {"ttft_p50": base, "ttft_p99": base * (1 + cv * (i - 2) / 2),
                              "tpot_p50": 10.0, "tpot_p99": 15.0}}
                for i in range(5)]
        data = {"workloads": [
            {"name": "glia-40qps", "runs": runs},
            {"name": "prefix-heavy", "runs": runs},
        ]}
        p = tmp_path / "noise_results.json"
        p.write_text(json.dumps(data))
        return p

    def _make_baseline_treatment(self, tmp_path, baseline_p99=100.0, treatment_p99=85.0):
        import json
        bl = {"workloads": [
            {"name": "glia-40qps",
             "metrics": {"ttft_p50": 50.0, "ttft_p99": baseline_p99,
                         "tpot_p50": 10.0, "tpot_p99": 15.0}},
            {"name": "prefix-heavy",
             "metrics": {"ttft_p50": 55.0, "ttft_p99": baseline_p99,
                         "tpot_p50": 10.0, "tpot_p99": 15.0}},
        ]}
        tr = {"workloads": [
            {"name": "glia-40qps",
             "metrics": {"ttft_p50": 45.0, "ttft_p99": treatment_p99,
                         "tpot_p50": 9.0, "tpot_p99": 13.0}},
            {"name": "prefix-heavy",
             "metrics": {"ttft_p50": 52.0, "ttft_p99": baseline_p99,  # no improvement
                         "tpot_p50": 10.0, "tpot_p99": 15.0}},
        ]}
        bp = tmp_path / "baseline_results.json"
        tp = tmp_path / "treatment_results.json"
        bp.write_text(json.dumps(bl))
        tp.write_text(json.dumps(tr))
        return bp, tp

    def _make_signal_coverage(self, tmp_path):
        import json
        sc = {"signals": [
            {"sim_name": "KVUtilization", "prod_name": "kvUtil",
             "prod_access_path": "node.status.kv_utilization",
             "fidelity_rating": "high", "staleness_window_ms": 0, "mapped": True},
            {"sim_name": "InFlightRequests", "prod_name": "inFlight",
             "prod_access_path": "node.status.in_flight_requests",
             "fidelity_rating": "high", "staleness_window_ms": 0, "mapped": True},
        ], "unmapped_signals": [], "commit_hash": "abc123", "coverage_complete": True}
        p = tmp_path / "signal_coverage.json"
        p.write_text(json.dumps(sc))
        return p

    def _make_workloads_dir(self, tmp_path):
        """Workload YAMLs that exercise mapped signals."""
        import yaml
        wd = tmp_path / "workloads"
        wd.mkdir()
        # glia-40qps exercises kv_utilization → KVUtilization (mapped)
        (wd / "workload_glia-40qps.yaml").write_text(
            yaml.dump({"version": "1", "kv_utilization": 0.5, "aggregate_rate": 40})
        )
        # prefix-heavy does not exercise any mapped signals
        (wd / "workload_prefix-heavy.yaml").write_text(
            yaml.dump({"version": "1", "aggregate_rate": 85})
        )
        return wd

    def test_pass_verdict_with_clear_improvement(self, tmp_path):
        import json, argparse
        noise = self._make_noise(tmp_path, cv=0.02)
        bl, tr = self._make_baseline_treatment(tmp_path, 100.0, 80.0)  # 20% improvement
        sc = self._make_signal_coverage(tmp_path)
        wd = self._make_workloads_dir(tmp_path)
        out = tmp_path / "bench_out.json"
        from tools.transfer_cli import cmd_benchmark_new
        args = argparse.Namespace(noise=str(noise), baseline=str(bl), treatment=str(tr),
                                  signal_coverage=str(sc), workloads_dir=str(wd),
                                  out=str(out))
        rc = cmd_benchmark_new(args)
        assert rc == 0
        result = json.loads(out.read_text())
        assert result["mechanism_check_verdict"] == "PASS"
        assert result["passed"] is True

    def test_fail_verdict_no_improvement(self, tmp_path):
        import json, argparse
        noise = self._make_noise(tmp_path, cv=0.02)
        bl, tr = self._make_baseline_treatment(tmp_path, 100.0, 100.0)  # 0% improvement
        sc = self._make_signal_coverage(tmp_path)
        wd = self._make_workloads_dir(tmp_path)
        out = tmp_path / "bench_out.json"
        from tools.transfer_cli import cmd_benchmark_new
        args = argparse.Namespace(noise=str(noise), baseline=str(bl), treatment=str(tr),
                                  signal_coverage=str(sc), workloads_dir=str(wd),
                                  out=str(out))
        rc = cmd_benchmark_new(args)
        assert rc == 1  # FAIL
        result = json.loads(out.read_text())
        assert result["mechanism_check_verdict"] == "FAIL"

    def test_output_written_on_fail(self, tmp_path):
        """benchmark --out is always written regardless of verdict."""
        import json, argparse
        noise = self._make_noise(tmp_path)
        bl, tr = self._make_baseline_treatment(tmp_path, 100.0, 100.0)
        sc = self._make_signal_coverage(tmp_path)
        wd = self._make_workloads_dir(tmp_path)
        out = tmp_path / "bench_out.json"
        from tools.transfer_cli import cmd_benchmark_new
        args = argparse.Namespace(noise=str(noise), baseline=str(bl), treatment=str(tr),
                                  signal_coverage=str(sc), workloads_dir=str(wd),
                                  out=str(out))
        cmd_benchmark_new(args)
        assert out.exists()
```

- [ ] **Step 6.2: Run tests — confirm they fail**

**Prerequisite guard (Chunk 1 must be complete before Chunk 5 tests run):**

```bash
test -f tools/workload_signal_mapping.json \
  || { echo "HALT: tools/workload_signal_mapping.json not found — complete Chunk 1 (Task 1) before running Chunk 5 tests."; exit 2; }
```

```bash
python -m pytest tools/test_transfer_cli.py::TestBenchmarkNew -v 2>&1 | tail -10
```

- [ ] **Step 6.3: Implement `cmd_benchmark_new`**

Add `cmd_benchmark_new` to `transfer_cli.py`:

```python
def _compute_cv(values: list) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    if mean == 0:
        return 0.0
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return (variance ** 0.5) / mean


def _classify_workloads(workloads_dir: "Path", signal_coverage_path: "Path",
                         mapping_path: "Path | None" = None) -> dict:
    """Return {workload_name: {"classification": "matched"|"unmatched", "matched_signals": [...]}}"""
    import json, yaml
    if mapping_path is None:
        mapping_path = Path(__file__).parent / "workload_signal_mapping.json"
    if not mapping_path.exists():
        raise FileNotFoundError(
            f"workload signal mapping not found at '{mapping_path}' — "
            "ensure Task 1 (Step 1.8) was completed to create this file"
        )
    try:
        mapping = json.loads(mapping_path.read_text())["mappings"]
    except json.JSONDecodeError as e:
        raise ValueError(f"workload signal mapping '{mapping_path}' is malformed JSON: {e}") from e
    try:
        sc = json.loads(signal_coverage_path.read_text())
    except json.JSONDecodeError as e:
        raise ValueError(f"signal coverage file '{signal_coverage_path}' is malformed JSON: {e}") from e
    mapped_signals = {s["sim_name"] for s in sc.get("signals", []) if s.get("mapped")}

    result = {}
    for wf in sorted(workloads_dir.iterdir()):
        if not wf.suffix in (".yaml", ".yml"):
            continue
        # Normalize underscores to hyphens so that workload_glia_40qps.yaml → glia-40qps,
        # matching the hyphenated names in values.yaml observe.workloads[].name and
        # the PVC directory names produced by blis observe.
        wl_name = wf.stem.removeprefix("workload_").replace("_", "-")
        try:
            wl_data = yaml.safe_load(wf.read_text()) or {}
        except Exception:
            wl_data = {}
        # Collect all keys present at top level AND within clients[] items
        all_keys = set(wl_data.keys())
        for client in wl_data.get("clients", []):
            if isinstance(client, dict):
                all_keys.update(client.keys())
        matched = []
        for entry in mapping:
            if entry["workload_field"] in all_keys:
                for sig in entry["signals"]:
                    if sig in mapped_signals:
                        matched.append(sig)
        result[wl_name] = {
            "classification": "matched" if matched else "unmatched",
            "matched_signals": list(set(matched)),
        }
    return result


def cmd_benchmark_new(args: "argparse.Namespace") -> int:
    import json
    from pathlib import Path

    noise_path = Path(args.noise)
    baseline_path = Path(args.baseline)
    treatment_path = Path(args.treatment)
    sc_path = Path(args.signal_coverage)
    wd_path = Path(args.workloads_dir)

    mapping_path = Path(__file__).parent / "workload_signal_mapping.json"
    for p in [noise_path, baseline_path, treatment_path, sc_path, wd_path, mapping_path]:
        if not p.exists():
            print(f"ERROR: required input '{p}' not found.", file=sys.stderr)
            return 2

    try:
        noise = json.loads(noise_path.read_text())
        baseline = json.loads(baseline_path.read_text())
        treatment = json.loads(treatment_path.read_text())
    except Exception as e:
        print(f"ERROR: cannot parse input JSON: {e}", file=sys.stderr)
        return 2

    for label, data in [("noise", noise), ("baseline", baseline), ("treatment", treatment)]:
        if not isinstance(data, dict) or "workloads" not in data:
            print(
                f"ERROR: {label} results file is missing 'workloads' key or is not a dict — "
                "run convert-trace to regenerate.",
                file=sys.stderr,
            )
            return 2

    # Compute T_eff from noise (pool all workloads per metric)
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

    # Build lookup maps
    try:
        bl_map = {w["name"]: w["metrics"]["ttft_p99"] for w in baseline["workloads"]}
        tr_map = {w["name"]: w["metrics"]["ttft_p99"] for w in treatment["workloads"]}
    except (KeyError, TypeError) as e:
        print(
            f"ERROR: malformed baseline or treatment results — missing expected field: {e}. "
            "Each workload entry must have 'name' and 'metrics.ttft_p99'.",
            file=sys.stderr,
        )
        return 2

    # Classify workloads (pass mapping_path explicitly to avoid CWD sensitivity)
    try:
        classification = _classify_workloads(wd_path, sc_path, mapping_path)
    except Exception as e:
        print(f"ERROR: workload classification failed: {e}", file=sys.stderr)
        return 2

    workload_classification = []
    matched_improvements = []
    unmatched_above_teff = []
    skipped_workloads = []

    for wl_name, cls_info in sorted(classification.items()):
        bl_p99 = bl_map.get(wl_name)
        tr_p99 = tr_map.get(wl_name)
        if bl_p99 is None or tr_p99 is None:
            # Emit diagnostic to help debug name mismatches
            print(
                f"WARNING: workload '{wl_name}' from workloads_dir not found in "
                f"{'baseline' if bl_p99 is None else 'treatment'} results "
                f"(available: {sorted(bl_map.keys())}). Skipping.",
                file=sys.stderr,
            )
            skipped_workloads.append(wl_name)
            continue
        improvement = (bl_p99 - tr_p99) / bl_p99 if bl_p99 else 0.0
        entry = {
            "workload": wl_name,
            "classification": cls_info["classification"],
            "improvement": round(improvement, 4),
            "matched_signals": cls_info["matched_signals"],
        }
        workload_classification.append(entry)
        if cls_info["classification"] == "matched":
            matched_improvements.append(improvement)
        elif improvement >= t_eff:
            unmatched_above_teff.append(
                f"workload {wl_name}: improvement={improvement:.2%} >= T_eff={t_eff:.2%}"
            )

    if skipped_workloads and not workload_classification:
        # All workloads were skipped — likely a name normalization mismatch.
        # Print to stdout as well so automated pipelines capture it even if stderr is lost.
        msg = (
            f"ERROR: all {len(skipped_workloads)} workload(s) were skipped due to name "
            f"mismatch between workloads_dir and result files. "
            f"Skipped: {skipped_workloads}. "
            f"Baseline result names: {sorted(bl_map.keys())}."
        )
        print(msg, file=sys.stderr)
        print(msg)

    # Mechanism check
    # INCONCLUSIVE = matched workloads found, some positive improvement, but below T_eff
    # FAIL = matched workloads found, no positive improvement (regression or flat)
    # ERROR = no matched workloads found at all (configuration/classification error)
    if not matched_improvements:
        verdict = "ERROR"
        passed = False
    elif max(matched_improvements) >= t_eff:
        verdict = "PASS"
        passed = True
    elif max(matched_improvements) > 0:
        verdict = "INCONCLUSIVE"
        passed = False
    else:
        verdict = "FAIL"
        passed = False

    output = {
        "t_eff": round(t_eff, 4),
        "noise_cv": round(noise_cv, 4),
        "mechanism_check_verdict": verdict,
        "passed": passed,
        "workload_classification": workload_classification,
        "specificity_notes": unmatched_above_teff,
    }

    if getattr(args, "out", None):
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(output, indent=2))
    else:
        print(json.dumps(output, indent=2))

    return 2 if verdict == "ERROR" else (1 if verdict == "FAIL" else 0)
```

Note: `ERROR` (no matched workloads found — configuration/classification error) maps to exit 2 (infrastructure error), not exit 0. This matches CLAUDE.md exit code contract and the old `_cmd_benchmark` behavior. The validate.md Step 5c HALT conditions (`HALT if exit 1`, `HALT if exit 2`) will correctly halt on ERROR verdict.

- [ ] **Step 6.4: Update the `benchmark` parser in `main()` — replace old parser**

Remove the old `p_bench` block and replace with:

```python
p_bench = subparsers.add_parser("benchmark",
    help="Compute T_eff and mechanism check from noise/baseline/treatment results")
p_bench.add_argument("--noise", required=True)
p_bench.add_argument("--baseline", required=True)
p_bench.add_argument("--treatment", required=True)
p_bench.add_argument("--signal-coverage", required=True, dest="signal_coverage")
p_bench.add_argument("--workloads-dir", required=True, dest="workloads_dir")
p_bench.add_argument("--out")
p_bench.set_defaults(func=cmd_benchmark_new)
```

Also remove `_cmd_noise_characterize` function and its parser (`p_noise`) from `main()`.

- [ ] **Step 6.5: Run new tests — all 3 pass**

```bash
python -m pytest tools/test_transfer_cli.py::TestBenchmarkNew -v
```
Expected: 3 passed.

- [ ] **Step 6.6: Run full suite — mark old benchmark/noise tests as expected-skip or update**

In `tools/test_transfer_cli.py`, locate all top-level functions to skip (there is no `TestBenchmarkMechanism` class — the old benchmark and noise tests are module-level functions):

```bash
# Find exact line numbers before editing
grep -n "^def test_benchmark_\|^def test_noise_characterize_" tools/test_transfer_cli.py
```

Add `@pytest.mark.skip(reason="superseded by TestBenchmarkNew")` on the line immediately before each of these top-level functions:
- `def test_benchmark_mechanism_check_pass`
- `def test_benchmark_mechanism_check_inconclusive`
- `def test_benchmark_mechanism_check_fail`
- `def test_benchmark_requires_t_eff`
- `def test_benchmark_malformed_input`
- `def test_benchmark_missing_workloads_key`
- `def test_benchmark_no_matched_workloads`
- `def test_noise_characterize_halts_on_high_cv`
- `def test_noise_characterize_malformed_input`
- `def test_noise_characterize_empty_runs`
- `def test_noise_characterize_t_eff_computation`

Example for a top-level function:
```python
@pytest.mark.skip(reason="superseded by TestBenchmarkNew")
def test_benchmark_mechanism_check_pass(tmp_path):
    ...
```

```bash
python -m pytest tools/ -v 2>&1 | tail -10
```

- [ ] **Step 6.7: Commit**

```bash
git add tools/transfer_cli.py tools/test_transfer_cli.py
git commit -m "feat(cli): rewrite benchmark subcommand; remove noise-characterize; add workload classification"
```

---

### Task 7: `generate-evidence` subcommand

**Files:**
- Modify: `tools/transfer_cli.py` — add `cmd_generate_evidence`
- Modify: `tools/test_transfer_cli.py` — add `class TestGenerateEvidence`

- [ ] **Step 7.1: Write failing tests**

```python
class TestGenerateEvidence:
    def _make_workspace(self, tmp_path):
        import json
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "algorithm_summary.json").write_text(json.dumps({
            "algorithm_name": "blis-routing-v1",
            "evolve_block_source": "routing/",
            # Note: algorithm_summary.schema.json has additionalProperties: false.
            # Only schema-defined fields are written here. matched_workload is
            # NOT a schema field — it is derived by generate-evidence from
            # validation_results.json benchmark.workload_classification.
        }))
        (ws / "validation_results.json").write_text(json.dumps({
            "suite_a": {"passed": True, "kendall_tau": 0.92,
                        "max_abs_error": 0.0001, "tuple_count": 150},
            "suite_b": {"passed": True, "rank_stability_tau": 1.0,
                        "threshold_crossing_pct": 0.0, "informational_only": True},
            "suite_c": {"passed": True, "deterministic": True,
                        "max_pile_on_ratio": 1.1},
            "benchmark": {
                "passed": True,
                "mechanism_check_verdict": "PASS",
                "t_eff": 0.05,
                "workload_classification": [
                    {"workload": "glia-40qps", "classification": "matched",
                     "improvement": 0.15, "matched_signals": ["KVUtilization"]},
                    {"workload": "prefix-heavy", "classification": "unmatched",
                     "improvement": 0.02, "matched_signals": []},
                ],
                "specificity_notes": [],
            },
            "overall_verdict": "PASS",
            "noise_cv": 0.03,
        }))
        return ws

    def test_generates_evidence_file(self, tmp_path):
        ws = self._make_workspace(tmp_path)
        out = tmp_path / "transfer_evidence.md"
        from tools.transfer_cli import cmd_generate_evidence
        import argparse
        args = argparse.Namespace(workspace=str(ws), out=str(out),
                                  calibration_log="docs/transfer/calibration_log.md")
        rc = cmd_generate_evidence(args)
        assert rc == 0
        content = out.read_text()
        assert "blis-routing-v1" in content
        assert "PASS" in content
        assert "glia-40qps" in content
        assert "0.92" in content  # suite_a tau

    def test_missing_validation_results_exits_1(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        import json
        (ws / "algorithm_summary.json").write_text(json.dumps(
            {"algorithm_name": "x", "evolve_block_source": "routing/"}
        ))
        # no validation_results.json
        from tools.transfer_cli import cmd_generate_evidence
        import argparse
        args = argparse.Namespace(workspace=str(ws), out=str(tmp_path / "out.md"),
                                  calibration_log="docs/transfer/calibration_log.md")
        rc = cmd_generate_evidence(args)
        assert rc == 1
```

- [ ] **Step 7.2: Run tests — confirm they fail**

```bash
python -m pytest tools/test_transfer_cli.py::TestGenerateEvidence -v 2>&1 | tail -5
```

- [ ] **Step 7.3: Implement `cmd_generate_evidence`**

```python
def cmd_generate_evidence(args: "argparse.Namespace") -> int:
    import json
    from datetime import date
    from pathlib import Path

    ws = Path(args.workspace)
    out_path = Path(args.out)
    cal_log = Path(getattr(args, "calibration_log", "docs/transfer/calibration_log.md"))

    alg_path = ws / "algorithm_summary.json"
    val_path = ws / "validation_results.json"

    for p, label in [(alg_path, "algorithm_summary.json"),
                     (val_path, "validation_results.json")]:
        if not p.exists():
            print(f"ERROR: generate-evidence requires '{p}' — "
                  f"{label} not found.", file=sys.stderr)
            return 1

    alg = json.loads(alg_path.read_text())
    val = json.loads(val_path.read_text())

    bench = val.get("benchmark", {})
    if not bench:
        print("ERROR: 'benchmark' key missing from validation_results.json — "
              "run Step 5c (benchmark) first.", file=sys.stderr)
        return 1

    # Calibration count
    calib_n = 1
    if cal_log.exists():
        calib_n = cal_log.read_text().count("### Transfer:") + 1

    # Extract fields
    alg_name = alg.get("algorithm_name", "unknown")
    overall = val.get("overall_verdict", "UNKNOWN")
    tau = val.get("suite_a", {}).get("kendall_tau", "N/A")
    err = val.get("suite_a", {}).get("max_abs_error", "N/A")
    suite_a_pass = val.get("suite_a", {}).get("passed", False)
    suite_c_pass = val.get("suite_c", {}).get("passed", False)
    pile_on = val.get("suite_c", {}).get("max_pile_on_ratio", "N/A")
    t_eff_pct = round(bench.get("t_eff", 0) * 100, 1)
    mech = bench.get("mechanism_check_verdict", "UNKNOWN")

    wc = bench.get("workload_classification", [])
    matched_entry = next((w for w in wc if w.get("classification") == "matched"), None)
    # Derive matched workload name from benchmark classification data.
    # algorithm_summary.schema.json has additionalProperties: false and does not
    # include a matched_workload field — reading it via alg.get() would always
    # return the default. Use the first matched workload from the benchmark instead.
    matched_wl = (matched_entry or {}).get("workload", alg_name)
    unmatched_entries = [w for w in wc if w.get("classification") == "unmatched"]
    matched_pct = round((matched_entry or {}).get("improvement", 0) * 100, 1)
    unmatched_mean_pct = (
        round(sum(w.get("improvement", 0) for w in unmatched_entries) /
              len(unmatched_entries) * 100, 1)
        if unmatched_entries else 0.0
    )

    narrative = {
        "PASS": f"Simulation-predicted benefit transferred to production.",
        "FAIL": f"Transfer failed — production improvement did not exceed noise floor.",
        "INCONCLUSIVE": f"Transfer result is inconclusive — see operator notes.",
    }.get(overall, f"Transfer verdict: {overall}.")

    evidence = f"""## Evidence: {alg_name} sim-to-real transfer

**Date:** {date.today().isoformat()}
**Verdict:** {overall}

### Claim
The evolved routing algorithm improves performance on {matched_wl}
in production with improvement above noise floor (T_eff={t_eff_pct}%).

### Evidence Chain

**1. Algorithm source**
- Algorithm: {alg_name}
- Source: {alg.get('evolve_block_source', 'N/A')}

**2. Translation fidelity verified**
- Suite A Kendall-tau: {tau} (threshold: 0.8) — {"PASS" if suite_a_pass else "FAIL"}
- Suite A max absolute error: {err}
- Suite C concurrent safety: {"PASS" if suite_c_pass else "FAIL"}, pile-on ratio: {pile_on}
- Interpretation: The production plugin reproduces the simulation
  algorithm's ranking behavior within measured tolerance.

**3. Production result**
- Observed improvement: {matched_pct}% on {matched_wl}
- Noise floor (T_eff): {t_eff_pct}%

**4. Mechanism specificity**
- Matched workload improvement: {matched_pct}%
- Mean unmatched workload improvement: {unmatched_mean_pct}%
- Mechanism check: {mech}

**5. Calibration**
- Running calibration: transfer {calib_n} of 3 (uncalibrated period)

### Summary
{narrative}
"""

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(evidence)
    return 0
```

- [ ] **Step 7.4: Register parser in `main()`**

```python
p_ge = subparsers.add_parser("generate-evidence",
    help="Generate workspace/transfer_evidence.md from workspace artifacts")
p_ge.add_argument("--workspace", required=True)
p_ge.add_argument("--out", required=True)
p_ge.add_argument("--calibration-log", dest="calibration_log",
                   default="docs/transfer/calibration_log.md")
p_ge.set_defaults(func=cmd_generate_evidence)
```

- [ ] **Step 7.5: Run tests — both pass**

```bash
python -m pytest tools/test_transfer_cli.py::TestGenerateEvidence -v
```
Expected: 2 passed.

- [ ] **Step 7.6: Run full suite**

```bash
python -m pytest tools/ -v 2>&1 | tail -5
```

- [ ] **Step 7.7: Commit**

```bash
git add tools/transfer_cli.py tools/test_transfer_cli.py
git commit -m "feat(cli): add generate-evidence subcommand producing transfer_evidence.md"
```

---

## Chunk 6: Tekton Tasks

### Task 8: `run-workload-blis-observe` and `collect-results` Tekton tasks

**Files:**
- Create: `tektonc-data-collection/tekton/tasks/run-workload-blis-observe.yaml`
- Create: `tektonc-data-collection/tekton/tasks/collect-results.yaml`

No unit tests for YAML tasks. Validation: `kubectl apply --dry-run=client -f <file>` once a cluster is available. Check YAML parses and schema is valid.

- [ ] **Step 8.1: Create `run-workload-blis-observe.yaml`**

```yaml
# tektonc-data-collection/tekton/tasks/run-workload-blis-observe.yaml
apiVersion: tekton.dev/v1
kind: Task
metadata:
  name: run-workload-blis-observe
spec:
  description: >-
    Run blis observe against a live inference endpoint using a workload spec.
    Writes TraceV2 output (trace_header.yaml + trace_data.csv) to the data PVC.
    NOTE: blis observe CLI flags (--endpoint, --workload-spec, --output-dir, --timeout)
    are provisional pending inference-sim PR #704 merge. Verify flag names
    against the merged observeCmd registration before deploying.

  params:
    - name: endpoint
      type: string
      description: "Inference endpoint URL, e.g. http://gateway-svc.$(params.namespace):8000/v1"
    - name: workloadSpec
      type: string
      description: "Inline YAML content of the workload spec file"
    - name: resultsDir
      type: string
      description: "Path under /workspace/data to write TraceV2 output (e.g. baseline/glia-40qps)"
    - name: blisImage
      type: string
      description: "Container image with blis observe command (from values.yaml observe.image)"
    - name: timeout
      type: string
      default: "30m"
      description: "Workload run timeout (passed to blis observe --timeout)"

  workspaces:
    - name: data
      description: "Shared PVC for storing TraceV2 results (data-pvc)"

  steps:
    - name: write-workload-spec
      image: alpine:3.19
      script: |
        #!/bin/sh
        set -e
        mkdir -p /workspace/data/$(params.resultsDir)
        # Use a single-quoted heredoc sentinel to write the workload spec safely.
        # Tekton substitutes $(params.workloadSpec) before the shell runs, replacing it
        # with the actual YAML content. Single-quoting the sentinel disables shell
        # expansion of any $, backtick, or special characters in the substituted content.
        # The sentinel __WORKLOAD_SPEC_END__ cannot appear in any YAML workload file.
        cat > /workspace/workload.yaml << '__WORKLOAD_SPEC_END__'
        $(params.workloadSpec)
        __WORKLOAD_SPEC_END__
        echo "Workload spec written to /workspace/workload.yaml"
        head -5 /workspace/workload.yaml

    - name: run-observe
      image: $(params.blisImage)
      script: |
        #!/bin/sh
        set -e
        mkdir -p /workspace/data/$(params.resultsDir)
        blis observe \
          --endpoint $(params.endpoint) \
          --workload-spec /workspace/workload.yaml \
          --output-dir /workspace/data/$(params.resultsDir) \
          --timeout $(params.timeout)
        echo "blis observe complete. Output in /workspace/data/$(params.resultsDir)"
        ls /workspace/data/$(params.resultsDir)
```

- [ ] **Step 8.2: Create `collect-results.yaml`**

```yaml
# tektonc-data-collection/tekton/tasks/collect-results.yaml
apiVersion: tekton.dev/v1
kind: Task
metadata:
  name: collect-results
spec:
  description: >-
    Synchronization barrier task. Runs after all run-workload-blis-observe tasks
    to ensure data integrity before the pipeline reports success.
    Directory structure on the PVC is established by each workload task's resultsDir
    parameter. This task does not move or transform data.

  workspaces:
    - name: data
      description: "Shared PVC (data-pvc) — verified as mounted"

  steps:
    - name: barrier
      image: alpine:3.19
      script: |
        #!/bin/sh
        echo "All workload tasks complete."
        echo "Results available on data-pvc."
```

- [ ] **Step 8.3: Validate YAML is parseable**

```bash
python -c "
import yaml
for f in [
  'tektonc-data-collection/tekton/tasks/run-workload-blis-observe.yaml',
  'tektonc-data-collection/tekton/tasks/collect-results.yaml',
]:
    yaml.safe_load(open(f).read())
    print('OK:', f)
"
```
Expected: `OK:` for both files.

- [ ] **Step 8.4: Commit**

```bash
git add tektonc-data-collection/tekton/tasks/run-workload-blis-observe.yaml \
        tektonc-data-collection/tekton/tasks/collect-results.yaml
git commit -m "feat(tekton): add run-workload-blis-observe and collect-results tasks"
```

---

## Chunk 7: Pipeline Templates

### Task 9: Three pipeline templates

**Files:**
- Create: `tektonc-data-collection/tekton/baseline-pipeline.yaml.j2`
- Create: `tektonc-data-collection/tekton/treatment-pipeline.yaml.j2`
- Create: `tektonc-data-collection/tekton/noise-pipeline.yaml.j2`

Validation: compile each template against a minimal `values.yaml` using `tektonc.py --explain`.

- [ ] **Step 9.1: Create minimal test values.yaml for template validation**

```bash
cat > /tmp/test-values.yaml << 'EOF'
stack:
  model:
    helmValues:
      modelName: "Qwen/Qwen2.5-7B-Instruct"
      decode:
        replicas: 4
        acceleratorTypes:
          labelKey: nvidia.com/gpu.product
          labelValues:
            - NVIDIA-H100-80GB-HBM3
  scorer:
    baseline:
      configContent: |
        apiVersion: inference.networking.x-k8s.io/v1alpha1
        kind: EndpointPickerConfig
    treatment:
      configContent: |
        apiVersion: inference.networking.x-k8s.io/v1alpha1
        kind: EndpointPickerConfig
observe:
  image: "ghcr.io/inference-sim/blis:v1.0.0"
  workloads:
    - name: glia-40qps
      spec: |
        version: "1"
        aggregate_rate: 40
    - name: prefix-heavy
      spec: |
        version: "1"
        aggregate_rate: 85
  noise_runs: 3
EOF
```

- [ ] **Step 9.2: Create `baseline-pipeline.yaml.j2`**

```yaml
# tektonc-data-collection/tekton/baseline-pipeline.yaml.j2
{% set scorer_config_content = stack.scorer.baseline.configContent %}
apiVersion: tekton.dev/v1
kind: Pipeline
metadata:
  name: sim2real-baseline
spec:
  params:
    - name: experimentId
      type: string
    - name: namespace
      type: string
  workspaces:
    - name: model-cache
    - name: hf-credentials
    - name: data-storage
  tasks:
    - name: download-model
      taskRef:
        name: download-model
      workspaces:
        - name: model-cache
          workspace: model-cache
        - name: hf-credentials
          workspace: hf-credentials
      params:
        - name: model
          value: "{{ stack.model.helmValues.modelName }}"

    - name: deploy-gaie
      taskRef:
        name: deploy-gaie
      params:
        - name: modelLabel
          value: "sim2real-$(params.experimentId)"
        - name: config
          value: "{{ scorer_config_content }}"
        - name: namespace
          value: "$(params.namespace)"

    - name: deploy-model
      runAfter: ["download-model", "deploy-gaie"]
      taskRef:
        name: deploy-model
      workspaces:
        - name: model-cache
          workspace: model-cache
      params:
        - name: model
          value: "{{ stack.model.helmValues.modelName }}"
        - name: modelLabel
          value: "sim2real-$(params.experimentId)"
        - name: namespace
          value: "$(params.namespace)"
        - name: config
          value: {{ stack.model.helmValues | tojson }}

    {% for workload in observe.workloads %}
    - name: run-workload-{{ workload.name | dns }}
      runAfter: ["deploy-model"]
      taskRef:
        name: run-workload-blis-observe
      workspaces:
        - name: data
          workspace: data-storage
      params:
        - name: endpoint
          value: "http://gateway-svc.$(params.namespace):8000/v1"
        - name: workloadSpec
          value: |
            {{ workload.spec | indent(12) }}
        - name: blisImage
          value: "{{ observe.image }}"
        - name: timeout
          value: "30m"
        - name: resultsDir
          value: "baseline/{{ workload.name }}"
    {% endfor %}

    - name: collect-results
      runAfter:
        {% for workload in observe.workloads %}
        - run-workload-{{ workload.name | dns }}
        {% endfor %}
      taskRef:
        name: collect-results
      workspaces:
        - name: data
          workspace: data-storage

  finally:
    - name: delete-model
      taskRef:
        name: delete-model
      params:
        - name: modelLabel
          value: "sim2real-$(params.experimentId)"
        - name: namespace
          value: "$(params.namespace)"
    - name: delete-gaie
      taskRef:
        name: delete-gaie
      params:
        - name: modelLabel
          value: "sim2real-$(params.experimentId)"
        - name: namespace
          value: "$(params.namespace)"
```

- [ ] **Step 9.3: Create `treatment-pipeline.yaml.j2`**

Copy `baseline-pipeline.yaml.j2`, change:
1. `name: sim2real-baseline` → `name: sim2real-treatment`
2. `{% set scorer_config_content = stack.scorer.baseline.configContent %}` → `{% set scorer_config_content = stack.scorer.treatment.configContent %}`
3. `value: "baseline/{{ workload.name }}"` → `value: "treatment/{{ workload.name }}"`

Note: The baseline template already uses `tojson` (not `toyaml`) for `stack.model.helmValues`. The copied treatment template inherits this correctly — no change needed for that line.

- [ ] **Step 9.4: Create `noise-pipeline.yaml.j2`**

The noise pipeline uses tektonc's `loopName`/`foreach`/`domain` to produce a cartesian product of `run_index × workload`:

```yaml
# tektonc-data-collection/tekton/noise-pipeline.yaml.j2
{% set scorer_config_content = stack.scorer.baseline.configContent %}
apiVersion: tekton.dev/v1
kind: Pipeline
metadata:
  name: sim2real-noise
spec:
  params:
    - name: experimentId
      type: string
    - name: namespace
      type: string
  workspaces:
    - name: model-cache
    - name: hf-credentials
    - name: data-storage
  tasks:
    - name: download-model
      taskRef:
        name: download-model
      workspaces:
        - name: model-cache
          workspace: model-cache
        - name: hf-credentials
          workspace: hf-credentials
      params:
        - name: model
          value: "{{ stack.model.helmValues.modelName }}"

    - name: deploy-gaie
      taskRef:
        name: deploy-gaie
      params:
        - name: modelLabel
          value: "sim2real-$(params.experimentId)"
        - name: config
          value: "{{ scorer_config_content }}"
        - name: namespace
          value: "$(params.namespace)"

    - name: deploy-model
      runAfter: ["download-model", "deploy-gaie"]
      taskRef:
        name: deploy-model
      workspaces:
        - name: model-cache
          workspace: model-cache
      params:
        - name: model
          value: "{{ stack.model.helmValues.modelName }}"
        - name: modelLabel
          value: "sim2real-$(params.experimentId)"
        - name: namespace
          value: "$(params.namespace)"
        - name: config
          value: {{ stack.model.helmValues | tojson }}

    - loopName: noise-runs
      foreach:
        domain:
          run_index: {{ range(observe.noise_runs) | list }}
          workload_name: {{ observe.workloads | map(attribute='name') | list }}
      vars:
        taskId: "{{ workload_name | dns }}-run-{{ run_index }}"

      tasks:
        - name: run-workload-{{ taskId }}
          runAfter: ["deploy-model"]
          taskRef:
            name: run-workload-blis-observe
          workspaces:
            - name: data
              workspace: data-storage
          params:
            - name: endpoint
              value: "http://gateway-svc.$(params.namespace):8000/v1"
            - name: workloadSpec
              value: |
                {%- for w in observe.workloads if w.name == workload_name -%}
                {{ w.spec }}
                {%- endfor %}
            - name: blisImage
              value: "{{ observe.image }}"
            - name: timeout
              value: "30m"
            - name: resultsDir
              value: "noise/{{ workload_name }}/run-{{ run_index }}"

    - name: collect-results
      runAfter:
        {% for run_index in range(observe.noise_runs) %}
        {% for workload in observe.workloads %}
        - run-workload-{{ workload.name | dns }}-run-{{ run_index }}
        {% endfor %}
        {% endfor %}
      taskRef:
        name: collect-results
      workspaces:
        - name: data
          workspace: data-storage

  finally:
    - name: delete-model
      taskRef:
        name: delete-model
      params:
        - name: modelLabel
          value: "sim2real-$(params.experimentId)"
        - name: namespace
          value: "$(params.namespace)"
    - name: delete-gaie
      taskRef:
        name: delete-gaie
      params:
        - name: modelLabel
          value: "sim2real-$(params.experimentId)"
        - name: namespace
          value: "$(params.namespace)"
```

- [ ] **Step 9.5: Compile each template against test values**

```bash
source .venv/bin/activate
for phase in baseline treatment noise; do
  python tektonc-data-collection/tektonc/tektonc.py \
    -t tektonc-data-collection/tekton/${phase}-pipeline.yaml.j2 \
    -f /tmp/test-values.yaml \
    --explain && echo "OK: $phase"
done
```
Expected: `OK: baseline`, `OK: treatment`, `OK: noise`.

- [ ] **Step 9.6: Verify baseline compiled YAML is valid Tekton Pipeline**

```bash
python tektonc-data-collection/tektonc/tektonc.py \
  -t tektonc-data-collection/tekton/baseline-pipeline.yaml.j2 \
  -f /tmp/test-values.yaml \
  -o /tmp/baseline-pipeline.yaml && \
python -c "
import yaml
p = yaml.safe_load(open('/tmp/baseline-pipeline.yaml'))
assert p['kind'] == 'Pipeline'
assert p['metadata']['name'] == 'sim2real-baseline'
tasks = p['spec']['tasks']
task_names = [t['name'] for t in tasks]
print('tasks:', task_names)
assert 'collect-results' in task_names
print('OK')
"
```
Expected: task names printed, `OK`.

- [ ] **Step 9.7: Commit**

```bash
git add tektonc-data-collection/tekton/baseline-pipeline.yaml.j2 \
        tektonc-data-collection/tekton/treatment-pipeline.yaml.j2 \
        tektonc-data-collection/tekton/noise-pipeline.yaml.j2
git commit -m "feat(tekton): add baseline, treatment, and noise pipeline templates"
```

---

## Chunk 8: Prompt Updates

### Task 10: Update `prompts/extract.md` (Stage 1)

**Files:**
- Modify: `prompts/extract.md` — add prerequisite check for blis observe availability
- Modify: `docs/transfer/blis_to_llmd_mapping.md` — add `## Submodule Prerequisites` section

- [ ] **Step 10.1: Read current extract.md to find the prerequisites section**

```bash
grep -n "prerequisite\|Prerequisite\|## " prompts/extract.md | head -20
```

- [ ] **Step 10.2: Add submodule prerequisite check to `prompts/extract.md`**

Find the "## Prerequisites" section in `prompts/extract.md` and append after the existing `LoadEvolvedBlock` check:

```markdown
**Stage 5 cluster prerequisite check (warning only — Stage 1 can complete without blis observe):**

```bash
# Check if inference-sim submodule includes blis observe (PR #704)
if ! grep -q "AddCommand(observeCmd)" inference-sim/cmd/root.go; then
  echo "WARNING: inference-sim submodule does not yet include \`blis observe\` (PR #704 not merged)."
  echo "Stage 1 extract and Stages 2-4 will complete normally."
  echo "Stage 5 cluster benchmarks will fail until the submodule is bumped."
  echo "See docs/transfer/blis_to_llmd_mapping.md § Submodule Prerequisites for how to bump."
  echo "CONTINUE: proceeding with Stage 1 extract."
fi
```

Do **not** HALT here. `blis observe` is only required for Stage 5 cluster pipeline submission (Step 5b). Stages 1-4 work with the current submodule. The real enforcement gate for `blis observe` is the `preflight --phase noise` call in Stage 5 Step 5b, which checks the compiled task image and will fail if `blis observe` is not available.

Note: `cmd/observe.go` exists in the current submodule but only contains HTTP client utilities — the `observeCmd` cobra.Command is **not registered** until PR #704 merges. File existence is not sufficient; the grep confirms actual command registration.
```

- [ ] **Step 10.3: Add `## Submodule Prerequisites` to `docs/transfer/blis_to_llmd_mapping.md`**

Append to the end of the file:

```markdown
## Submodule Prerequisites

### inference-sim: minimum commit for `blis observe` (PR #704)

Stage 5 cluster benchmarking requires `blis observe` CLI to be present in the
inference-sim submodule. This command is added by PR #704
(`feat(cmd): add blis observe command for real-server latency collection`).

**Minimum commit hash:** `<fill in after PR #704 merges>`

**How to verify:** `grep -q "AddCommand(observeCmd)" inference-sim/cmd/root.go`

**How to bump the submodule:**
```bash
cd inference-sim
git fetch origin
git checkout <minimum_commit_hash>
cd ..
git add inference-sim
git commit -m "chore: bump inference-sim to include blis observe (#704)"
```

After bumping, rebuild the blis container image and update `observe.image` in `values.yaml`.
```

- [ ] **Step 10.4: Commit**

```bash
git add prompts/extract.md docs/transfer/blis_to_llmd_mapping.md
git commit -m "docs: add blis observe prerequisite check to Stage 1 and submodule prereqs to mapping artifact"
```

---

### Task 11: Update `prompts/validate.md` (Stage 5 — Steps 1 and 5)

**Files:**
- Modify: `prompts/validate.md` — replace Step 1 (noise characterization) and Step 5 (cluster benchmarks)

- [ ] **Step 11.1: Locate Step 1 and Step 5 boundaries in current validate.md**

```bash
grep -n "^## Step\|OPERATOR ACTION REQUIRED" prompts/validate.md
```

- [ ] **Step 11.2: Replace Step 1 content**

Find the `## Step 1: Noise Characterization` block (up to the next `## Step 2`) and replace with:

```markdown
## Step 1: Noise Characterization Gate

Noise characterization runs as the `noise` phase of the cluster pipeline (Step 5a/5b).
This step verifies noise is done before proceeding to Suites A/B/C.

**Routing preamble — run first:**

```bash
# Resolve NAMESPACE (operator-set env var or prompt)
# Initialize state and check noise status
BENCH_STATE_OUTPUT=$(.venv/bin/python tools/transfer_cli.py \
  benchmark-state --workspace workspace/ --namespace ${NAMESPACE:?NAMESPACE must be set})
BENCH_STATE_EXIT=$?

if [ $BENCH_STATE_EXIT -eq 2 ]; then
  echo "HALT: benchmark-state failed — missing workspace/algorithm_summary.json. Run Stage 1 extract first."
  exit 1
elif [ $BENCH_STATE_EXIT -ne 0 ]; then
  echo "HALT: benchmark-state failed (exit $BENCH_STATE_EXIT). Check cluster context."
  echo "$BENCH_STATE_OUTPUT"
  exit 1
fi

NOISE_STATUS=$(echo "$BENCH_STATE_OUTPUT" \
  | .venv/bin/python -c "import sys,json; print(json.load(sys.stdin)['phases']['noise']['status'])")

if [ "$NOISE_STATUS" != "done" ]; then
  echo "REENTER: Noise phase is '$NOISE_STATUS' — jump to Step 5 (5a and 5b for noise phase only)."
  echo "After noise phase completes: re-run validate.md from Step 1 (do NOT fall through to Suite A/B/C now)."
  # Signal to automated harnesses: this is a planned re-entry pause, not a success completion.
  # Exit 3 = REENTER (distinct from exit 0 = complete, exit 1 = error, exit 2 = infrastructure error).
  exit 3
fi
# If noise_status == "done": fall through to Step 2 (Suite A) below.
```

**If noise is `done`:** proceed to Step 2 (Suite A).
**If noise is not `done`:** script exits 3 (REENTER). Jump to Step 5 now, run the noise phase pipeline,
then re-enter Stage 5 from the top for Pass 2 (Suites A/B/C + baseline/treatment).
Automated harnesses should treat exit 3 as a planned re-entry pause (not an error and not a completion).

T_eff is computed internally by `transfer_cli.py benchmark` from `workspace/noise_results.json`.
The old `baseline_runs.json` format and `noise-characterize` subcommand are superseded.
```

- [ ] **Step 11.3: Replace Step 5 content**

Find the `## Step 5: Cluster Benchmarks` block (marked `[OPERATOR ACTION REQUIRED]`) and replace with the automated flow from the spec. Key commands to include:

```markdown
## Step 5: Cluster Benchmarks

### 5a. Initialize state
```bash
.venv/bin/python tools/transfer_cli.py benchmark-state \
  --workspace workspace/ --namespace $NAMESPACE
```
**HALT if exit 1** (cluster context mismatch). **HALT if exit 2** (missing algorithm_summary.json).

### 5b. For each non-done phase in order: noise → baseline → treatment

Check phase status from state file. For each pending or failed phase (run this block once per phase, substituting `phase` for each of `noise`, `baseline`, `treatment` in order):

```bash
# Bind phase variable — repeat this block for each of: noise, baseline, treatment
phase=noise   # change to baseline or treatment for subsequent iterations
```

**Pre-flight:**
```bash
.venv/bin/python tools/transfer_cli.py preflight \
  --phase $phase --values workspace/tekton/values.yaml --namespace $NAMESPACE
```
**HALT if exit 1.**

**Compile:**
```bash
.venv/bin/python tools/transfer_cli.py compile-pipeline \
  --template-dir tektonc-data-collection/tekton \
  --values workspace/tekton/values.yaml --phase $phase \
  --out workspace/tekton/compiled/
```

**Submit:**
```bash
kubectl apply -f workspace/tekton/compiled/${phase}-pipeline.yaml \
  || { echo "HALT: kubectl apply pipeline failed for $phase"; exit 1; }
PIPELINERUN_NAME=sim2real-${phase}-$(date +%s)
.venv/bin/python tools/transfer_cli.py render-pipelinerun \
  --template workspace/tekton/pipelinerun-${phase}.yaml \
  --vars PIPELINERUN_NAME=$PIPELINERUN_NAME NAMESPACE=$NAMESPACE PHASE=$phase \
  --out /tmp/pipelinerun-${phase}.yaml \
  || { echo "HALT: render-pipelinerun failed for $phase"; exit 1; }
kubectl apply -f /tmp/pipelinerun-${phase}.yaml \
  || { echo "HALT: kubectl apply pipelinerun failed for $phase"; exit 1; }
.venv/bin/python tools/transfer_cli.py benchmark-state --workspace workspace/ \
  --set-phase $phase --status running --pipelinerun $PIPELINERUN_NAME
```

**Wait (4h timeout):**
```bash
TIMEOUT_SECS=14400; ELAPSED=0
while true; do
  REASON=$(tkn pr describe $PIPELINERUN_NAME \
    -o jsonpath='{.status.conditions[0].reason}' 2>/dev/null)
  echo "$REASON" | grep -qE 'Succeeded|Failed|PipelineRunCancelled|CouldntGetTask' && break
  sleep 30; ELAPSED=$((ELAPSED+30))
  if [ $ELAPSED -ge $TIMEOUT_SECS ]; then
    .venv/bin/python tools/transfer_cli.py benchmark-state --workspace workspace/ \
      --set-phase $phase --status failed \
      --failure-reason "Polling timeout after ${TIMEOUT_SECS}s"
    echo "HALT: $phase pipeline timed out."; exit 1
  fi
done
```

**On Failed:**
```bash
FAIL_REASON=$(tkn pr describe $PIPELINERUN_NAME \
  -o jsonpath='{.status.conditions[0].message}' 2>/dev/null || echo "PipelineRun failed")
.venv/bin/python tools/transfer_cli.py benchmark-state --workspace workspace/ \
  --set-phase $phase --status failed \
  --failure-reason "$FAIL_REASON"
echo "HALT: $phase pipeline failed — $FAIL_REASON"; exit 1
```

**On Succeeded — extract via extractor pod:**
```bash
trap "kubectl delete pod sim2real-extract-${phase} -n $NAMESPACE --ignore-not-found 2>/dev/null" EXIT ERR
kubectl run sim2real-extract-${phase} --image=alpine:3.19 --restart=Never \
  --overrides='{"spec":{"volumes":[{"name":"data","persistentVolumeClaim":{"claimName":"data-pvc"}}],"containers":[{"name":"e","image":"alpine:3.19","command":["sleep","600"],"volumeMounts":[{"name":"data","mountPath":"/data"}]}]}}' \
  -n $NAMESPACE
kubectl wait pod/sim2real-extract-${phase} --for=condition=Ready --timeout=60s -n $NAMESPACE \
  || { echo "HALT: extractor pod not ready"; exit 1; }
kubectl cp $NAMESPACE/sim2real-extract-${phase}:/data/${phase}/ workspace/${phase}_raw/ --retries=3 \
  || { echo "HALT: kubectl cp failed"; exit 1; }
.venv/bin/python tools/transfer_cli.py convert-trace \
  --input-dir workspace/${phase}_raw/ --output workspace/${phase}_results.json \
  || { echo "HALT: convert-trace failed"; exit 1; }
.venv/bin/python tools/transfer_cli.py validate-schema workspace/${phase}_results.json \
  || { echo "HALT: schema validation failed for workspace/${phase}_results.json — results file is malformed, do not mark phase done"; exit 1; }
.venv/bin/python tools/transfer_cli.py benchmark-state --workspace workspace/ \
  --set-phase $phase --status done --results workspace/${phase}_results.json
```

### 5c. Mechanism check
```bash
.venv/bin/python tools/transfer_cli.py benchmark \
  --noise    workspace/noise_results.json \
  --baseline workspace/baseline_results.json \
  --treatment workspace/treatment_results.json \
  --signal-coverage workspace/signal_coverage.json \
  --workloads-dir blis_router/workloads/ \
  --out workspace/benchmark_output.json
.venv/bin/python tools/transfer_cli.py validate-schema workspace/benchmark_output.json \
  || { echo "HALT: benchmark_output.json failed schema validation"; exit 1; }
```
Exit 0 = PASS or INCONCLUSIVE (parse `mechanism_check_verdict` from JSON).
**HALT if exit 1** (FAIL). **HALT if exit 2** (infrastructure error).

```bash
MECH_VERDICT=$(python -c "import json; print(json.load(open('workspace/benchmark_output.json'))['mechanism_check_verdict'])")
if [ "$MECH_VERDICT" = "INCONCLUSIVE" ]; then
  echo "OPERATOR REVIEW REQUIRED: mechanism_check_verdict is INCONCLUSIVE."
  echo "Inspect workspace/benchmark_output.json, resolve ambiguity, then re-run or override manually."
  echo "Do NOT proceed to generate-evidence or Stage 6 without explicit operator sign-off."
  exit 1
fi
```

### 5c-merge. Merge benchmark output into validation_results.json

`generate-evidence` (Step 5d) reads `benchmark` from `workspace/validation_results.json`. This step merges `workspace/benchmark_output.json` into it.

Note: `noise_cv` from `benchmark_output.json` must be placed at the top level of `validation_results.json` (not inside `benchmark`), because `validation_results.schema.json` places `noise_cv` at top level and has `additionalProperties: false` on the `benchmark` sub-object.

```bash
test -f workspace/validation_results.json \
  || { echo "HALT: workspace/validation_results.json not found — ensure Suites A/B/C have run before Step 5c-merge"; exit 1; }
.venv/bin/python - <<'EOF'
import json, sys

bench = json.loads(open('workspace/benchmark_output.json').read())
val_path = 'workspace/validation_results.json'
val = json.loads(open(val_path).read())

# Copy all benchmark fields except noise_cv into val["benchmark"]
val['benchmark'] = {k: v for k, v in bench.items() if k != 'noise_cv'}
# noise_cv goes to top-level (per validation_results.schema.json)
val['noise_cv'] = bench['noise_cv']

mech = bench.get('mechanism_check_verdict', 'ERROR')
if mech == 'PASS' and val.get('suite_a', {}).get('passed') and val.get('suite_c', {}).get('passed'):
    val['overall_verdict'] = 'PASS'
elif mech == 'INCONCLUSIVE':
    val['overall_verdict'] = 'INCONCLUSIVE'
else:
    val['overall_verdict'] = 'FAIL'

open(val_path, 'w').write(json.dumps(val, indent=2))
print('Merged benchmark into validation_results.json — overall_verdict:', val['overall_verdict'])
EOF
```
**HALT if exit non-zero.**

```bash
.venv/bin/python tools/transfer_cli.py validate-schema workspace/validation_results.json \
  || { echo "HALT: validation_results.json failed schema validation after benchmark merge"; exit 1; }
```

### 5d. Generate evidence document
```bash
.venv/bin/python tools/transfer_cli.py generate-evidence \
  --workspace workspace/ --out workspace/transfer_evidence.md
```
**HALT if exit 1.**
```

- [ ] **Step 11.3b: Insert Step 4b — partial write of validation_results.json**

In `prompts/validate.md`, find `## Step 5: Cluster Benchmarks` (the newly replaced step from Step 11.3) and insert the following **immediately before it** (between Step 4 and Step 5):

```markdown
## Step 4b: Write partial validation_results.json (suites A/B/C)

Write `workspace/validation_results.json` with suite_a, suite_b, and suite_c results collected in Steps 2–4. Step 5c-merge will add `benchmark`, `noise_cv`, and `overall_verdict` after cluster pipelines complete.

**Do not call validate-schema yet** — the partial file intentionally omits `benchmark`, `overall_verdict`, and `noise_cv` (all required by the schema). Schema validation will fail on the partial file; run it only after Step 5c-merge.

```json
{
  "suite_a": {
    "passed": <true|false>,
    "kendall_tau": <mean_tau>,
    "max_abs_error": <max_abs_err>,
    "tuple_count": <tuple_count from Step 2 test output (may be < 200 if tuples were skipped)>
  },
  "suite_b": {
    "passed": true,
    "rank_stability_tau": <tau>,
    "threshold_crossing_pct": 0.0,
    "informational_only": true
  },
  "suite_c": {
    "passed": <true|false>,
    "deterministic": true,
    "max_pile_on_ratio": <ratio>
  }
}
```

Save this (with actual values substituted) to `workspace/validation_results.json`.
```

- [ ] **Step 11.3c: Replace Step 6 in validate.md**

Find `## Step 6: Write validation_results.json` (the entire block up to the next `## Step 7`) and replace with:

```markdown
## Step 6: Final artifact validation

`workspace/validation_results.json` was completed by Step 5c-merge (which added `benchmark`, `noise_cv`, and `overall_verdict`). Run a final schema check:

```bash
.venv/bin/python tools/transfer_cli.py validate-schema workspace/validation_results.json
```

**HALT if validate-schema exits non-zero.**

**Manual verification (required — the lightweight validator cannot enforce `if/then` conditionals):**
If `overall_verdict` is `"INCONCLUSIVE"`, verify that `operator_notes` is present and non-empty in `workspace/validation_results.json`. This is the audit trail for the Option 4 soft-pass path. **HALT if `overall_verdict` is `"INCONCLUSIVE"` and `operator_notes` is absent or empty.** Message: "HALT: operator_notes required for INCONCLUSIVE verdict (Option 4 soft-pass audit trail)."
```

- [ ] **Step 11.4: Commit**

```bash
git add prompts/validate.md
git commit -m "docs(prompts): update Stage 5 validate.md — automate noise and cluster benchmark steps"
```

---

### Task 12: Update `prompts/generate.md` and `CLAUDE.md`

**Files:**
- Modify: `prompts/generate.md` — add Stage 3 workspace/tekton/ output generation
- Modify: `CLAUDE.md` — add jinja2/PyYAML note, remove noise-characterize, update benchmark CLI

- [ ] **Step 12.1: Add tekton artifact generation to `prompts/generate.md`**

First, determine the next step number and locate the insertion point:

```bash
# Find existing step headings to determine the next step number
grep -n "^## Step" prompts/generate.md
# Identify the final validation step (insertion is BEFORE it)
grep -n "^## Final\|^## Verification\|^## Step.*[Vv]alidat" prompts/generate.md
```

The new section heading is `## Step <N>` where `<N>` is the next integer after the last existing `## Step` in the file. Insert it immediately before the final validation step (or at end of Stage 3 steps if no validation step exists).

```markdown
## Step <N>: Generate tekton artifacts

After generating the scorer plugin, generate the tekton benchmarking artifacts.

**Prerequisites check:**
```bash
.venv/bin/python tools/transfer_cli.py validate-schema workspace/algorithm_summary.json
.venv/bin/python tools/transfer_cli.py validate-schema workspace/signal_coverage.json
```
**HALT if either exits non-zero.**

**Resolve inference-sim image tag:**
```bash
BLIS_IMAGE_TAG=$(cd inference-sim && git describe --tags 2>/dev/null)
if [ -z "$BLIS_IMAGE_TAG" ]; then
  echo "HALT: git describe --tags returned empty — no tags on inference-sim submodule"; exit 1
fi
if echo "$BLIS_IMAGE_TAG" | grep -qE -- '-g[0-9a-f]+$'; then
  echo "HALT: '$BLIS_IMAGE_TAG' is an un-tagged commit (contains -g suffix). Bump the submodule to a release tag first."; exit 1
fi
if ! echo "$BLIS_IMAGE_TAG" | grep -qE '^v?[0-9]+\.[0-9]+'; then
  echo "HALT: '$BLIS_IMAGE_TAG' does not match expected release-tag pattern v?N.N"; exit 1
fi
echo "Resolved tag: $BLIS_IMAGE_TAG"
```
Record `$BLIS_IMAGE_TAG` for use in `observe.image` below.

**Generate `workspace/tekton/values.yaml`:**

Using the translation rules document (`docs/transfer/blis_to_llmd_mapping.md` and
`blis_router/llm_config.yaml` + `blis_router/hardware_config.json`), generate
`workspace/tekton/values.yaml` following the schema in the design spec. Key required fields:
- `stack.model.helmValues` — from `blis_router/llm_config.yaml` via translation rules
- `stack.scorer.baseline.configContent` — from `docs/transfer/blis_to_llmd_mapping.md` baseline scorer config
- `stack.scorer.treatment.configContent` — the generated plugin's scorer config (from Stage 3 scorer output)
- `observe.image` — `"ghcr.io/inference-sim/blis:$BLIS_IMAGE_TAG"` (resolved above)
- `observe.workloads` — embed full content of `blis_router/workloads/workload_*.yaml` files
- `observe.noise_runs: 5`

**Validate generated values.yaml required keys:**
```bash
python -c "
import yaml
v = yaml.safe_load(open('workspace/tekton/values.yaml'))
required = ['stack', 'observe']
for k in required:
    assert k in v, f'missing key: {k}'
assert 'image' in v['observe'], 'missing observe.image'
assert '<TAG>' not in v['observe']['image'], 'unresolved <TAG> in observe.image'
assert v['observe'].get('noise_runs'), 'missing observe.noise_runs'
wl = v['observe'].get('workloads', [])
assert len(wl) > 0, 'observe.workloads must be non-empty (no workload files found in blis_router/workloads/)'
print('OK')
"
```
**HALT if any assertion fails.**

**Generate PipelineRun stubs** (`workspace/tekton/pipelinerun-{noise,baseline,treatment}.yaml`):

Each stub must include:
```yaml
metadata:
  name: $PIPELINERUN_NAME
  namespace: $NAMESPACE
  labels:
    sim2real-phase: $PHASE
spec:
  pipelineRef:
    name: sim2real-<phase>
  params:
    - name: experimentId
      value: $PIPELINERUN_NAME
    - name: namespace
      value: $NAMESPACE
  workspaces:
    - name: model-cache
      persistentVolumeClaim:
        claimName: model-pvc
    - name: hf-credentials
      secret:
        secretName: hf-secret
    - name: data-storage
      persistentVolumeClaim:
        claimName: data-pvc
```

**Update `workspace/stage3_output.json`** to include `tekton_artifacts` key:
```json
{
  "tekton_artifacts": {
    "values_yaml": "workspace/tekton/values.yaml",
    "pipeline_stubs": [
      "workspace/tekton/pipelinerun-noise.yaml",
      "workspace/tekton/pipelinerun-baseline.yaml",
      "workspace/tekton/pipelinerun-treatment.yaml"
    ]
  }
}
```

Note: `stage3_output.schema.json` defines `tekton_artifacts` with `additionalProperties: false` and only allows `values_yaml` (string) and `pipeline_stubs` (array). Individual keys per phase (`pipelinerun_noise`, etc.) are not permitted — use `pipeline_stubs` array. Stage 5 reads the stub paths from this array in phase order (noise, baseline, treatment).

**Validate stage3_output.json after updating:**
```bash
.venv/bin/python tools/transfer_cli.py validate-schema workspace/stage3_output.json \
  || { echo "HALT: stage3_output.json failed schema validation after tekton_artifacts update"; exit 1; }
```
```

- [ ] **Step 12.2: Update `CLAUDE.md`**

First, locate the relevant sections:

```bash
# Find noise-characterize row and benchmark entry in CLI commands table
grep -n "noise-characterize\|benchmark\|## Development\|## Notes\|## CLI" CLAUDE.md | head -30
```

Four changes:

1. In the CLI commands table, replace `noise-characterize` row with a note: "(removed — superseded by noise pipeline in Stage 5)"

2. Update `benchmark` entry to new interface:
```
# Compute mechanism check from noise/baseline/treatment results
python tools/transfer_cli.py benchmark \
  --noise workspace/noise_results.json \
  --baseline workspace/baseline_results.json \
  --treatment workspace/treatment_results.json \
  --signal-coverage workspace/signal_coverage.json \
  --workloads-dir blis_router/workloads/ \
  --out workspace/benchmark_output.json
```

3. Add note under `## Development`:
```
## Notes
- `compile-pipeline` and `preflight` require `jinja2` and `PyYAML`.
  These are installed via `pip install -r requirements.txt`.
  The stdlib-only constraint does not apply to these subcommands.
```

4. Add Stage 5 exit-code table under the `## Notes` section (or append to it):
```
- Stage 5 validate.md exit codes (shell script level, not CLI):
  - `0` = complete (all phases done, all suites passed)
  - `1` = error/halt (context mismatch, ordering violation, suite failure)
  - `2` = infrastructure error (missing artifact, parse failure)
  - `3` = REENTER (noise phase not yet done; operator must jump to Step 5 and re-enter validate.md after noise completes). Automated harnesses must NOT treat exit 3 as a generic failure — it is a planned re-entry pause.
```

- [ ] **Step 12.3: Commit**

```bash
git add prompts/generate.md CLAUDE.md
git commit -m "docs: update generate.md Stage 3 for tekton artifacts; update CLAUDE.md CLI docs"
```

---

## Chunk 9: Integration and Final Validation

### Task 13: End-to-end integration test (local)

**Files:**
- Modify: `tools/test_transfer_cli.py` — add `class TestEndToEndLocal`

This integration test exercises the full local Python pipeline (no cluster needed): convert-trace → benchmark → generate-evidence, verifying that all three produce valid output and chain correctly.

- [ ] **Step 13.1: Write the integration test**

```python
class TestEndToEndLocal:
    """Full local pipeline: convert-trace → benchmark → generate-evidence."""

    def _setup_workspace(self, tmp_path):
        import csv, json, yaml
        ws = tmp_path / "workspace"
        ws.mkdir()

        # algorithm_summary — only schema-valid fields (additionalProperties: false)
        # matched_workload is NOT in algorithm_summary.schema.json; generate-evidence
        # derives the matched workload from validation_results.json benchmark data.
        (ws / "algorithm_summary.json").write_text(json.dumps({
            "algorithm_name": "blis-routing-v1",
            "evolve_block_source": "routing/",
        }))

        # signal_coverage (prod_access_path required by signal_coverage.schema.json)
        (ws / "signal_coverage.json").write_text(json.dumps({
            "signals": [{"sim_name": "KVUtilization", "prod_name": "kv",
                         "prod_access_path": "node.status.kv_utilization",
                         "fidelity_rating": "high", "staleness_window_ms": 0,
                         "mapped": True}],
            "unmapped_signals": [], "commit_hash": "abc", "coverage_complete": True,
        }))

        # validation_results (from Suites A/B/C — pre-existing)
        (ws / "validation_results.json").write_text(json.dumps({
            "suite_a": {"passed": True, "kendall_tau": 0.93,
                        "max_abs_error": 0.0001, "tuple_count": 120},
            "suite_b": {"passed": True, "rank_stability_tau": 1.0,
                        "threshold_crossing_pct": 0.0, "informational_only": True},
            "suite_c": {"passed": True, "deterministic": True,
                        "max_pile_on_ratio": 1.05},
        }))

        # workloads dir
        wd = tmp_path / "workloads"
        wd.mkdir()
        (wd / "workload_glia-40qps.yaml").write_text(
            yaml.dump({"version": "1", "kv_utilization": 0.5})
        )
        (wd / "workload_prefix-heavy.yaml").write_text(
            yaml.dump({"version": "1", "aggregate_rate": 85})
        )

        # Simulate TraceV2 directories for noise (5 runs × 2 workloads)
        def write_tv2(d, ttft_us=100000, chunks=5):
            d.mkdir(parents=True)
            (d / "trace_header.yaml").write_text("trace_version: 2\n")
            with open(d / "trace_data.csv", "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["request_id","send_time_us","first_chunk_time_us",
                             "last_chunk_time_us","num_chunks","status","error_message"])
                for i in range(10):
                    w.writerow([i, 0, ttft_us+i*1000, ttft_us+50000+i*1000, chunks, "ok", ""])

        raw_noise = tmp_path / "noise_raw"
        for wl in ["glia-40qps", "prefix-heavy"]:
            for r in range(5):
                write_tv2(raw_noise / wl / f"run-{r}", ttft_us=100000+r*500)

        raw_bl = tmp_path / "baseline_raw"
        raw_tr = tmp_path / "treatment_raw"
        for wl, base_ttft in [("glia-40qps", 100000), ("prefix-heavy", 120000)]:
            write_tv2(raw_bl / wl, ttft_us=base_ttft)
            write_tv2(raw_tr / wl, ttft_us=int(base_ttft * 0.82))  # ~18% improvement

        return ws, wd, raw_noise, raw_bl, raw_tr

    def test_full_local_pipeline(self, tmp_path):
        import json, argparse
        from tools.transfer_cli import (cmd_convert_trace, cmd_benchmark_new,
                                         cmd_generate_evidence)

        ws, wd, raw_noise, raw_bl, raw_tr = self._setup_workspace(tmp_path)

        # convert-trace for all three phases
        for phase, raw_dir in [("noise", raw_noise),
                                ("baseline", raw_bl),
                                ("treatment", raw_tr)]:
            args = argparse.Namespace(
                input_dir=str(raw_dir),
                output=str(ws / f"{phase}_results.json"),
            )
            rc = cmd_convert_trace(args)
            assert rc == 0, f"convert-trace failed for {phase}"

        # benchmark
        args = argparse.Namespace(
            noise=str(ws / "noise_results.json"),
            baseline=str(ws / "baseline_results.json"),
            treatment=str(ws / "treatment_results.json"),
            signal_coverage=str(ws / "signal_coverage.json"),
            workloads_dir=str(wd),
            out=str(tmp_path / "benchmark_output.json"),
        )
        rc = cmd_benchmark_new(args)
        assert rc == 0, "benchmark failed"
        bench_out = json.loads((tmp_path / "benchmark_output.json").read_text())
        assert bench_out["mechanism_check_verdict"] == "PASS"

        # Merge benchmark output into validation_results
        # (matches Step 5c-merge: noise_cv at top level, not inside benchmark sub-object)
        val = json.loads((ws / "validation_results.json").read_text())
        val["benchmark"] = {k: v for k, v in bench_out.items() if k != "noise_cv"}
        val["overall_verdict"] = "PASS"
        val["noise_cv"] = bench_out["noise_cv"]
        (ws / "validation_results.json").write_text(json.dumps(val))

        # generate-evidence
        args_ev = argparse.Namespace(
            workspace=str(ws),
            out=str(ws / "transfer_evidence.md"),
            calibration_log="docs/transfer/calibration_log.md",
        )
        rc = cmd_generate_evidence(args_ev)
        assert rc == 0, "generate-evidence failed"
        evidence = (ws / "transfer_evidence.md").read_text()
        assert "PASS" in evidence
        assert "blis-routing-v1" in evidence
```

- [ ] **Step 13.2: Run the integration test**

```bash
python -m pytest tools/test_transfer_cli.py::TestEndToEndLocal -v
```
Expected: 1 passed.

- [ ] **Step 13.3: Run full suite — confirm no regressions**

```bash
python -m pytest tools/ -v
```
Note the final pass/fail count.

- [ ] **Step 13.4: Commit**

```bash
git add tools/test_transfer_cli.py
git commit -m "test: add end-to-end local integration test for convert-trace → benchmark → generate-evidence"
```

---

### Task 14: Final cleanup and PR prep

- [ ] **Step 14.1: Validate all schemas still parse after all changes**

```bash
python -c "
import json, pathlib
for p in pathlib.Path('tools/schemas').glob('*.json'):
    json.load(open(p)); print('OK:', p.name)
"
```

- [ ] **Step 14.2: Run full test suite one last time**

```bash
python -m pytest tools/ -v 2>&1 | tee /tmp/test_results.txt
grep -E "passed|failed|error" /tmp/test_results.txt | tail -3
```
Expected: no failures, no errors.

- [ ] **Step 14.2b: Run Go verification (required by pr-workflow.md Validation PR gate)**

```bash
go build ./tools/harness/...
go test ./tools/harness/...
```
Expected: both exit 0 with no errors.

- [ ] **Step 14.3: Compile all three pipeline templates against test values**

Note: `/tmp/test-values.yaml` was written in Step 9.1 but may not persist across shell sessions (Tasks execute in separate commits). Recreate it if needed:
```bash
# Recreate test-values.yaml if it was lost between sessions
test -f /tmp/test-values.yaml || python -c "
import pathlib
# Copy the content from Step 9.1 into /tmp/test-values.yaml
content = pathlib.Path('docs/superpowers/plans/2026-03-17-tektonc-cluster-benchmarking.md').read_text()
import re
m = re.search(r\"cat > /tmp/test-values.yaml << 'EOF'\n(.*?)\nEOF\", content, re.DOTALL)
if m:
    pathlib.Path('/tmp/test-values.yaml').write_text(m.group(1))
    print('Recreated /tmp/test-values.yaml')
else:
    print('ERROR: could not find test-values.yaml content in plan file')
"
```

```bash
source .venv/bin/activate
for phase in baseline treatment noise; do
  python tektonc-data-collection/tektonc/tektonc.py \
    -t tektonc-data-collection/tekton/${phase}-pipeline.yaml.j2 \
    -f /tmp/test-values.yaml --explain > /dev/null && echo "OK: $phase"
done
```
Expected: `OK: baseline`, `OK: treatment`, `OK: noise`.

- [ ] **Step 14.4: Verify CLAUDE.md no longer mentions noise-characterize as active**

```bash
grep "noise-characterize" CLAUDE.md
```
Expected: either not found, or only appears in a "removed" note.

- [ ] **Step 14.4b: Verify no unfilled placeholders remain in mapping artifact**

```bash
grep "<fill in" docs/transfer/blis_to_llmd_mapping.md \
  && { echo "HALT: unfilled placeholder in blis_to_llmd_mapping.md — fill in the PR #704 minimum commit hash before merging this PR."; exit 1; } \
  || echo "OK: no unfilled placeholders"
```
Expected: `OK: no unfilled placeholders`. If PR #704 has not yet merged, skip this step and add a TODO comment; the submodule bump is a prerequisite for Stage 5 cluster runs, not for this PR's unit tests.

- [ ] **Step 14.4c: Confirm blis observe flags match merged PR #704**

```bash
# If PR #704 has merged and the submodule has been bumped, run:
if grep -q "AddCommand(observeCmd)" inference-sim/cmd/root.go 2>/dev/null; then
  echo "blis observe registered — verify CLI flags in run-workload-blis-observe.yaml:"
  echo "  Expected: --endpoint, --workload-spec, --output-dir, --timeout"
  echo "  Actual flags from binary:"
  # Locate the main package (may be at inference-sim/main.go or inference-sim/cmd/*/main.go)
  BLIS_MAIN=$(ls inference-sim/main.go inference-sim/cmd/blis/main.go 2>/dev/null | head -1)
  if [ -n "$BLIS_MAIN" ]; then
    go run "$BLIS_MAIN" observe --help 2>&1 | grep -E '^\s+--' \
      || { echo "  go run failed — building binary instead:"; \
           (cd inference-sim && go build -o /tmp/blis . 2>/dev/null || go build -o /tmp/blis ./cmd/blis/ 2>/dev/null) \
           && /tmp/blis observe --help 2>&1 | grep -E '^\s+--'; }
  else
    echo "  Cannot locate main package — run 'ls inference-sim/' to find entry point, then run '<binary> observe --help'"
  fi
  echo "Manually confirm all four flags match and update the task YAML if they differ."
else
  echo "INFO: observeCmd not yet registered (PR #704 pending). Flags are provisional — no action needed until submodule is bumped."
fi
```

- [ ] **Step 14.5: Commit final cleanup and open PR**

```bash
git status
# Stage any unstaged changes to tracked files
git add -u
git commit -m "chore: final cleanup before PR — verify schemas, tests, templates all pass"
```

---

## Execution Notes

- **Python tests:** Each task's tests can be run in isolation with `python -m pytest tools/test_transfer_cli.py::<ClassName> -v`
- **Template validation:** Requires `source .venv/bin/activate` (jinja2/PyYAML)
- **Cluster tests:** `preflight` and `compile-pipeline`→`kubectl apply` require a live cluster — these are tested manually after Task 5 and Task 9 respectively
- **blis observe flags:** The `--endpoint`, `--workload-spec`, `--output-dir`, `--timeout` flags in `run-workload-blis-observe.yaml` are provisional until inference-sim PR #704 merges. Verify flag names against the merged `observeCmd` before deploying to a cluster.
- **Ordering:** Complete Chunk 1 before Chunks 2–5 (schemas needed by tests). **Task 6 (benchmark subcommand) has a hard dependency on Chunk 1**: `tools/workload_signal_mapping.json` must exist before `TestBenchmarkNew` tests can pass. Step 6.2 includes an explicit guard. Chunks 6–7 (YAML/templates) are independent of Chunks 2–5. Chunk 8 (prompts) depends on all previous chunks.

