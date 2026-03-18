# Design: Tektonc-Based Cluster Benchmarking for sim2real Stage 5

**Date:** 2026-03-17
**Status:** Draft
**Author:** Claude Code + kalantar
**Supersedes:** Stage 5 "OPERATOR ACTION REQUIRED" cluster benchmark steps in `prompts/validate.md`

---

## Problem Statement

Stage 5 (Validate) of the sim2real transfer pipeline currently requires two manual operator steps for cluster benchmarking: noise characterization and baseline/treatment benchmark runs. These steps use `llm-d-benchmark` and require the operator to manually run commands, collect results, and place them in `workspace/`. This is error-prone, non-resumable on failure, and does not enforce pre-flight checks before cluster work begins.

This design replaces those manual steps with an automated tektonc-based pipeline system that:
- Runs pre-flight checks before any cluster work begins
- Submits three focused Tekton pipelines (noise, baseline, treatment)
- Tracks per-phase state so Stage 5 is safely resumable after cluster failures
- Extracts results from a PVC into `workspace/` automatically
- Assembles a human-readable evidence document linking simulation predictions to production results

---

## Scope

**In scope:**
- Three Tekton pipeline templates for noise characterization, baseline benchmarking, and treatment benchmarking
- A new `run-workload-blis-observe` Tekton task using `blis observe` (inference-sim #704)
- New `transfer_cli.py` subcommands: `preflight`, `benchmark-state`, `convert-trace`, `compile-pipeline`, `generate-evidence`
- `workspace/benchmark_state.json` schema for resumable execution
- `workspace/transfer_evidence.md` artifact documenting the sim-to-real evidence chain
- Updated Stage 5 prompt replacing the "OPERATOR ACTION REQUIRED" sections (the full replacement text for `prompts/validate.md` Step 1 and Step 5 is a deliverable of the implementation PR)
- **Modifications to completed PR artifacts:** Stage 1 (`prompts/extract.md`, PR1) gains a prerequisite check for `blis observe` availability. Stage 3 (`prompts/generate.md`, PR3) gains new output artifacts (`workspace/tekton/values.yaml`, PipelineRun stubs). The full replacement text for `prompts/generate.md` (including prerequisites, expected outputs, validation steps, and halt conditions for tekton artifact generation) is a deliverable of the implementation PR, alongside the `prompts/validate.md` replacement text. These changes to already-completed prompts are explicitly in-scope for the implementation PR.
  - **Stage 3 `generate.md` minimum requirements:** The replacement text MUST include: (1) **Predecessor artifact validation** — verify `workspace/algorithm_summary.json` and `workspace/signal_coverage.json` exist and pass schema validation before generating tekton artifacts; (2) **TAG placeholder resolution** — run `git describe --tags` in the inference-sim submodule and substitute the result into `observe.image`; HALT if `git describe --tags` fails, returns an empty result, or returns a non-exact tag (i.e., output containing a `-g` suffix like `v1.0-5-gabcdef`, which indicates the commit is not directly tagged and would not be a valid Docker image tag). The tag must match the regex `^v?[0-9]+\.[0-9]+` (a semver-like prefix) to be accepted; (3) **Output validation** — validate generated `values.yaml` structure (required keys: `stack`, `observe`, `observe.workloads`, `observe.image`, `observe.noise_runs`, `scorer.baseline.configContent`, `scorer.treatment.configContent`); HALT if validation fails; (4) **PipelineRun stub generation halt conditions** — HALT if any PipelineRun stub cannot be generated (e.g., missing template variables). Each PipelineRun stub MUST include a `metadata.labels` block with `sim2real-phase: $PHASE` (envsubst placeholder) — this label is required by the Step 5b duplicate submission guard. A `values.yaml` schema is not a deliverable of this PR but may be added as a future extension.
- A separate translation-rules document (out of scope for this design) governs `llm_config.yaml` → `values.yaml`

**Out of scope:**
- The rules for translating `blis_router/llm_config.yaml` + `hardware_config.json` → `values.yaml` (separate document, iterated independently)
- S3 upload of benchmark results (not needed for sim2real pipeline)
- Multi-cluster or multi-hardware support (one generated `values.yaml` per hardware configuration)

---

## Mapping Decisions

| Submodule Concept | Treatment | Rationale |
|---|---|---|
| TraceV2 output format (`trace_header.yaml` + `trace_data.csv`) | **Mapped** — `convert-trace` consumes raw TraceV2 files and derives latency metrics (TTFT, TPOT percentiles) | Core data format from `blis observe`; must be consumed exactly as produced |
| Workload spec format (v1/v2 YAML) | **Mapped** — embedded verbatim in pipeline template via `values.yaml` `observe.workloads[].spec` | Same workload definitions used in simulation must be used in production benchmarks for fidelity |
| `blis observe` CLI (inference-sim PR #704) | **Mapped** — `run-workload-blis-observe` Tekton task wraps the CLI | Direct dependency; flag names are provisional pending PR merge |
| tektonc pipeline compilation (`tektonc.py`) | **Mapped** — `compile-pipeline` shells out to tektonc for template rendering | Existing pipeline compilation tool in `tektonc-data-collection` (at `tektonc-data-collection/tektonc/tektonc.py`) |
| Scorer plugin deployment (Helm-based via GAIE) | **Mapped** — `deploy-model` and `deploy-gaie` Tekton tasks handle deployment | Standard deployment path in `llm-d-benchmark` task library |
| Simulation fitness score (`combined_score`) | **Simplified** — used as a proxy for per-workload sim predictions in attenuation ratio | No per-workload sim predictions available yet; `combined_score` is an approximation (see Extension Points) |
| S3 result storage | **Deliberately omitted** — results stay on PVC + local `workspace/` | Not needed for sim2real pipeline; avoids S3 credential dependency |
| Multi-cluster benchmarking | **Deliberately omitted** — single `cluster_context` enforced | Unnecessary complexity for current single-hardware transfers (see Extension Points) |
| GPU memory headroom checks | **Deliberately omitted** — not in preflight | Requires runtime probing; hardware config in `values.yaml` is assumed correct |
| HuggingFace token validity | **Deliberately omitted** — not in preflight | Requires network call to HF API; `hf-secret` existence is checked instead |

---

## Architecture Overview

### Three Focused Pipelines

Each pipeline has a single responsibility and is independently retryable:

| Pipeline | Scorer config | Runs | PVC output (TraceV2 directories) |
|---|---|---|---|
| `sim2real-noise` | baseline | 5 × all workloads | `noise/{workload}/` (TraceV2: `trace_header.yaml` + `trace_data.csv` per workload, aggregated across 5 runs by `collect-results`) |
| `sim2real-baseline` | baseline | 1 × all workloads | `baseline/{workload}/` (TraceV2: `trace_header.yaml` + `trace_data.csv` per workload) |
| `sim2real-treatment` | treatment (generated plugin) | 1 × all workloads | `treatment/{workload}/` (TraceV2: `trace_header.yaml` + `trace_data.csv` per workload) |

The noise pipeline's `collect-results` task preserves the 5 per-run TraceV2 outputs as separate subdirectories under `noise/{workload}/run-{0..4}/` on the PVC (each containing `trace_header.yaml` + `trace_data.csv`). This per-run structure is required because `benchmark` computes `noise_cv = max(per_metric_cv.values())` from per-run variance — aggregating to a single value per workload would destroy this information.

For the noise phase, `convert-trace` produces **per-run metrics**: `{"workloads": [{"name": "<workload>", "runs": [{"metrics": {"ttft_p50": ..., "ttft_p99": ..., "tpot_p50": ..., "tpot_p99": ...}}, ...]}]}`. The `runs` array contains one entry per run directory. `benchmark` computes noise_cv by **pooling across all workloads per metric**: for each of the 4 metrics, collect all run values across all workloads into a single array (e.g., for `ttft_p99`: 5 runs × 2 workloads = 10 values), compute `cv = std(values) / mean(values)`, then `noise_cv = max(per_metric_cv.values())` and `T_eff = max(0.05, 2 * noise_cv)`. The 0.05 floor (5%) prevents T_eff from becoming trivially small on very-low-noise clusters (e.g., noise_cv=0.01 → T_eff would be 0.02 without the floor, making the mechanism check trivially passable). This floor is carried over from the existing `noise-characterize` implementation. This pooling strategy treats all workloads as replicate measurements of system noise, producing a single conservative noise floor.

For baseline and treatment, `collect-results` organizes single-run outputs into `{phase}/{workload}/` directories (no `run-*` subdirectories). `convert-trace` produces the standard single-value format: `{"workloads": [{"name": "<workload>", "metrics": {"ttft_p50": ..., ...}}]}`.

Conversion from TraceV2 to canonical metrics JSON happens locally after `kubectl cp` extraction via `convert-trace` — raw TraceV2 data is what resides on the PVC.

All three deploy their own model servers and GAIE instance — no shared state across pipelines. This makes each independently retryable without affecting the others.

### Task DAG (identical structure for all three pipelines, parameters vary)

```
download-model ──────────────────────┐
                                     │
deploy-gaie ─────────────────────────┤
                                     ▼
                               deploy-model
                                     │
                    ┌────────────────┴────────────────┐
                    ▼                                 ▼
          run-workload-blis-observe        run-workload-blis-observe
          (workload: glia-40qps)           (workload: prefix-heavy)
                    │                                 │
                    └────────────────┬────────────────┘
                                     ▼
                              collect-results
                    (organize per-workload TraceV2 output
                     directories on PVC — no conversion)

finally: (unconditional cleanup, uses static names from pipeline params)
  delete-model
  delete-gaie
```

`deploy-gaie` and `download-model` run in parallel (no dependency between them). Both must complete before `deploy-model`. Workloads run in parallel after `deploy-model`. `collect-results` runs after all workloads. Cleanup runs unconditionally in `finally:`, using static names composed from pipeline params (e.g., `sim2real-<experimentId>`) — not from task results — so they work in `finally:` context.

### Stage 5 as State Machine

Stage 5 drives the three pipelines via `workspace/benchmark_state.json`. For each pending phase:

1. Run pre-flight checks — halt before any cluster work if checks fail
2. Compile pipeline template via `tektonc`
3. Submit PipelineRun
4. Poll `tkn pr describe` with timeout until `Succeeded` or `Failed`
5. On success: mount `data-pvc` via extractor pod → extract results → `kubectl cp` → convert → update state to `done`
6. On failure: update state to `failed`, halt with diagnostic — resume on next Stage 5 entry

After all three phases are `done`, call `transfer_cli.py benchmark` and generate `workspace/transfer_evidence.md`.

---

## Generated Artifacts (Stage 3 Output)

Stage 3 generates tektonc artifacts alongside the scorer plugin, hardcoded to the hardware configuration derived from `blis_router/`:

```
workspace/tekton/
├── values.yaml                    # model, hardware, workloads, scorer configs
├── pipelinerun-noise.yaml         # PipelineRun stub (experimentId parameterized)
├── pipelinerun-baseline.yaml      # PipelineRun stub
└── pipelinerun-treatment.yaml     # PipelineRun stub
```

**Note:** The three pipeline templates (`noise-pipeline.yaml.j2`, `baseline-pipeline.yaml.j2`, `treatment-pipeline.yaml.j2`) are **static files** in `tektonc-data-collection/tekton/`. These templates **do not yet exist** and must be created as part of the implementation PR for this design. Only `values.yaml` and the PipelineRun stubs are generated per-transfer. Stage 3 generates `values.yaml` using the translation rules document (separate artifact, iterated independently).

**PipelineRun stub placeholder syntax:** PipelineRun stubs generated by Stage 3 MUST use envsubst-compatible `$VARIABLE` or `${VARIABLE}` placeholder syntax for runtime-resolved values (e.g., `$PIPELINERUN_NAME`, `$NAMESPACE`, `$PHASE`). This is the syntax consumed by `render-pipelinerun --vars`. Stage 3 and `render-pipelinerun` must agree on this convention. Example stub fragment: `metadata: {name: $PIPELINERUN_NAME, namespace: $NAMESPACE, labels: {sim2real-phase: $PHASE}}`. The `sim2real-phase` label is required for the duplicate PipelineRun submission guard in Step 5b (which uses `kubectl get pipelinerun -l "sim2real-phase=<phase>"` to detect active runs).

**Pipeline template skeleton** (all three share this structure; differences noted inline):

```yaml
# {{ phase }}-pipeline.yaml.j2  (phase = noise | baseline | treatment)
apiVersion: tekton.dev/v1
kind: Pipeline
metadata:
  name: sim2real-{{ phase }}
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
        - name: hf-credentials
      params:
        - name: model
          value: "{{ stack.model.helmValues.modelName }}"
        # ... model download params from values.yaml

    - name: deploy-gaie
      taskRef:
        name: deploy-gaie
      params:
        - name: modelLabel
          value: "sim2real-$(params.experimentId)"
        - name: config
          # Each template binds scorer_config_content at the top via:
          #   {% set scorer_config_content = scorer.baseline.configContent %}  (noise + baseline templates)
          #   {% set scorer_config_content = scorer.treatment.configContent %}  (treatment template)
          # This is the ONLY difference between the three template files for this parameter.
          # deploy-gaie.config receives the EndpointPickerConfig YAML for GAIE deployment.
          value: "{{ scorer_config_content }}"
        - name: namespace
          value: "$(params.namespace)"

    - name: deploy-model
      runAfter: ["download-model", "deploy-gaie"]
      taskRef:
        name: deploy-model
      workspaces:
        - name: model-cache
      params:
        - name: model
          value: "{{ stack.model.helmValues.modelName }}"
        - name: modelLabel
          value: "sim2real-$(params.experimentId)"
        - name: namespace
          value: "$(params.namespace)"
        - name: config
          # deploy-model.config receives modelservice Helm values (NOT scorer config).
          # deploy-model.yaml steps[apply-overrides] writes this to /tmp/values.yaml
          # and processes it as Helm values for the modelservice chart.
          # Scorer config is passed separately via deploy-gaie.config (see above).
          value: "{{ stack.model.helmValues | toyaml }}"

    # --- Baseline/Treatment templates: one task per workload ---
    # {% for workload in observe.workloads %}
    # - name: run-workload-{{ workload.name }}
    #   runAfter: ["deploy-model"]
    #   taskRef:
    #     name: run-workload-blis-observe
    #   params:
    #     - name: endpoint
    #       value: "http://gateway-svc.$(params.namespace):8000/v1"
    #     - name: workloadSpec
    #       value: |
    #         {{ workload.spec | indent(12) }}
    #     - name: blisImage
    #       value: "{{ observe.image }}"
    #     - name: timeout
    #       value: "30m"
    #     - name: resultsDir
    #       value: "{{ phase }}/{{ workload.name }}"
    #   workspaces:
    #     - name: data
    #       workspace: data-storage
    # {% endfor %}
    #
    # --- Noise template: uses tektonc loopName/foreach/domain for 5 runs × N workloads ---
    # The noise pipeline uses tektonc's loop construct to expand workload tasks
    # across observe.noise_runs (5) iterations and all workloads, producing a
    # cartesian product of (run_index × workload) task instances.
    #
    # loopName: noise-runs
    # foreach:
    #   domain:
    #     run_index: [0, 1, 2, 3, 4]          # observe.noise_runs entries
    #     workload_name: [glia-40qps, prefix-heavy]  # observe.workloads[*].name
    # tasks:
    #   - name: "run-workload-{{ workload_name }}-run-{{ run_index }}"
    #     runAfter: ["deploy-model"]
    #     taskRef:
    #       name: run-workload-blis-observe
    #     params:
    #       - name: endpoint
    #         value: "http://gateway-svc.$(params.namespace):8000/v1"
    #       - name: workloadSpec
    #         # Binding: iterate observe.workloads to find the entry matching workload_name
    #         value: |
    #           {%- for w in observe.workloads if w.name == workload_name -%}
    #           {{ w.spec }}
    #           {%- endfor -%}
    #       - name: blisImage
    #         value: "{{ observe.image }}"
    #       - name: timeout
    #         value: "30m"
    #       - name: resultsDir
    #         value: "noise/{{ workload_name }}/run-{{ run_index }}"
    #     workspaces:
    #       - name: data
    #         workspace: data-storage
    #
    # This expands to 10 task instances (5 runs × 2 workloads), each uniquely
    # named (e.g., run-workload-glia-40qps-run-0, run-workload-prefix-heavy-run-3).
    #
    # collect-results.runAfter must reference all expanded task names.
    # In the noise template, use a Jinja2 {% for %} block inside runAfter
    # (the pattern required by tektonc — see nested-loops sample):
    #
    # - name: collect-results
    #   runAfter:
    #     {% for workload in observe.workloads %}
    #     {% for run_idx in range(observe.noise_runs) %}
    #     - "run-workload-{{ workload.name }}-run-{{ run_idx }}"
    #     {% endfor %}
    #     {% endfor %}
    #
    # The actual template MUST use Jinja2 to derive values from
    # values.yaml (observe.noise_runs, observe.workloads) rather than
    # hardcoding them. The hardcoded values above are illustrative only.

    # Unified skeleton showing the workload task expansion point:
    {% for workload in observe.workloads %}
    - name: run-workload-{{ workload.name }}
      runAfter: ["deploy-model"]
      taskRef:
        name: run-workload-blis-observe
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
          value: "{{ phase }}/{{ workload.name }}"
      workspaces:
        - name: data
          workspace: data-storage
    {% endfor %}

    - name: collect-results
      runAfter:
        {% for workload in observe.workloads %}
        - run-workload-{{ workload.name }}
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

**Jinja2 conventions:** Templates use tektonc's `tektonc.py` for rendering. `values.yaml` is passed as the Jinja2 context. Pipeline params (`experimentId`, `namespace`) are Tekton runtime params resolved from PipelineRun stubs. Only **built-in Jinja2 filters** (e.g., `indent`) are used — no custom filters or functions are required. The `workload.spec` field (a multi-line YAML string from `values.yaml`) is embedded via `{{ workload.spec | indent(N) }}` to produce a properly-indented YAML literal block in the Tekton param value. **Jinja2 metacharacter safety:** If a workload YAML contains `{{`, `}}`, `{%`, or `%}` sequences (in comments, string values, or templated fields), Jinja2 will attempt to evaluate them during rendering. To prevent this, `compile-pipeline` MUST pre-escape `workload.spec` values in the Jinja2 context before rendering — either by replacing `{{`/`}}` with `{{ '{{' }}`/`{{ '}}' }}` in the spec strings, or by setting the Jinja2 environment's `variable_start_string`/`variable_end_string` to non-conflicting delimiters for the inner pass. The implementation must verify that workload YAML files in `blis_router/workloads/` do not contain unescaped Jinja2 metacharacters, or handle them transparently. **Noise pipeline difference:** The noise pipeline replaces the simple `{% for workload %}` loop with a tektonc `loopName`/`foreach`/`domain` construct that produces a cartesian product of `run_index` (0 through `observe.noise_runs - 1`) and `workload_name` (from `observe.workloads`). For 5 runs × 2 workloads, this expands to 10 uniquely-named task instances (e.g., `run-workload-glia-40qps-run-0`), each with `resultsDir` set to `noise/{workload_name}/run-{run_index}/`. The `collect-results.runAfter` block must reference all 10 expanded task names using a Jinja2 `{% for %}` block (the pattern required by tektonc — see the annotated noise template skeleton above). The baseline and treatment templates use a single `{% for workload %}` loop with `resultsDir` set to `{phase}/{workload.name}/`.

**PVC bindings in PipelineRun stubs:**
```yaml
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

**Deliverables for the implementation PR:**
- **Submodule registration:** `tektonc-data-collection/` must be registered as a git submodule in sim2real before any files inside it can be committed. Run `git submodule add <tektonc-data-collection-repo-url> tektonc-data-collection` and commit the resulting `.gitmodules` update. This is a prerequisite for all `tektonc-data-collection/` deliverables below.
- `tektonc-data-collection/tekton/noise-pipeline.yaml.j2`
- `tektonc-data-collection/tekton/baseline-pipeline.yaml.j2`
- `tektonc-data-collection/tekton/treatment-pipeline.yaml.j2`
- `tektonc-data-collection/tekton/tasks/run-workload-blis-observe.yaml` (the Tekton task referenced by all three pipeline templates)
- `tektonc-data-collection/tekton/tasks/collect-results.yaml` (Tekton task that acts as a pure synchronization barrier — it runs after all workload tasks complete to ensure data integrity before the pipeline reports success. Directory structure is established by each workload task's `resultsDir` parameter, not by `collect-results`. No `phase` parameter is needed. **Minimum step body:** `steps: [{name: barrier, image: "alpine:3.19", script: "echo 'All workload tasks complete.'"}]`. Workspace binding: `workspaces: [{name: data}]`.)
- `tektonc-data-collection/tekton/roles.yaml` (RBAC RoleBindings for cluster setup — must be tracked in the repository)
- `tools/schemas/noise_results.schema.json` (validates `convert-trace` output for noise phase — per-run structure with `runs` array)
- `tools/schemas/baseline_results.schema.json` (validates `convert-trace` output for baseline phase — single-value structure)
- `tools/schemas/treatment_results.schema.json` (validates `convert-trace` output for treatment phase — single-value structure, identical to baseline schema)
- `tools/schemas/benchmark_output.schema.json` (validates `benchmark --out` output — see schema content specification in the `benchmark` subcommand section below)
- `tools/schemas/benchmark_state.schema.json` (validates `workspace/benchmark_state.json` — already exists as untracked file; must be staged and committed)
- Updated `prompts/validate.md` replacement text (Stage 5 Steps 1 and 5 replacement — see Scope section)
- Updated `prompts/extract.md` (Stage 1 gains prerequisite check for `blis observe` availability — see Scope section)
- Updated `prompts/generate.md` replacement text (Stage 3 gains `workspace/tekton/values.yaml` and PipelineRun stub outputs — see "Modifications to completed PR artifacts" in the Scope section above)
- Updated `docs/transfer/blis_to_llmd_mapping.md` (new `## Submodule Prerequisites` section recording the minimum inference-sim commit hash for PR #704 — referenced by Stage 1 prerequisite check)
- Updated `CLAUDE.md` (three changes: (1) note jinja2/PyYAML dependency amendment for `compile-pipeline` and `preflight`; (2) remove `noise-characterize` subcommand documentation; (3) update `benchmark` CLI interface from `--results --t-eff` to `--noise --baseline --treatment --signal-coverage --workloads-dir --out`)
- `requirements.txt` (or equivalent dependency manifest adding `jinja2` and `PyYAML` — required by `compile-pipeline` and `preflight`; ensures reproducible `.venv` setup for CI and new developers)
- `tools/workload_signal_mapping.json` (shared data file mapping workload YAML fields to simulation signals — consumed by `benchmark` for workload classification and referenced by `prompts/validate.md` for documentation). **Format:** `{"mappings": [{"workload_field": "<YAML field path>", "signals": ["<sim_signal_name>", ...], "description": "<human-readable explanation>"}]}`. Each entry maps a workload YAML field (e.g., `"aggregate_rate"`, `"prefix_group"`) to one or more simulation signal names (matching `sim_name` in `signal_coverage.json`). `benchmark` loads this file, checks each workload's YAML fields against the mapping entries, and classifies a workload as "matched" if any of its exercised signals appear in `signal_coverage.json` with `mapped: true`. A JSON Schema (`tools/schemas/workload_signal_mapping.schema.json`) is NOT required but the format must be documented here for implementer reference.

### `values.yaml` structure (additions beyond blis sample)

```yaml
# Hardware config derived via translation rules document
stack:
  model:
    helmValues:
      decode:
        replicas: 4
        acceleratorTypes:
          labelKey: nvidia.com/gpu.product   # read by preflight GPU check
          labelValues:
            - NVIDIA-H100-80GB-HBM3
        # ... full model config

  scorer:
    baseline:
      configFile: "sim2real-baseline.yaml"
      configContent: |
        apiVersion: inference.networking.x-k8s.io/v1alpha1
        kind: EndpointPickerConfig
        # ... baseline scorer config (prefix-affinity + load-balance)
    treatment:
      configFile: "sim2real-treatment.yaml"
      configContent: |
        # ... generated plugin config (new plugin enabled, overlapping scorers disabled)

observe:
  image: "ghcr.io/inference-sim/blis:<TAG>"   # PLACEHOLDER — must be replaced with actual tag after inference-sim PR #704 merges. Tag derivation: use `git describe --tags` from the inference-sim commit that includes #704. The minimum commit hash will be recorded in docs/transfer/blis_to_llmd_mapping.md § Submodule Prerequisites. **Stage 3 prompt requirement:** The `prompts/generate.md` replacement text MUST include an explicit instruction for the LLM to resolve this placeholder by running `git describe --tags` in the inference-sim submodule and substituting the result. The literal string `<TAG>` must not appear in the generated `values.yaml`.
  workloads:
    - name: glia-40qps
      spec: |
        version: "1"
        aggregate_rate: 40
        seed: 42
        # ... full workload YAML (from blis_router/workloads/workload_glia_40qps.yaml)
    - name: prefix-heavy
      spec: |
        version: "1"
        aggregate_rate: 85
        seed: 42
        # ... full workload YAML (from blis_router/workloads/workload_glia_prefix_heavy.yaml)
  noise_runs: 5
```

---

## New Tekton Task: `run-workload-blis-observe`

Location: `tektonc-data-collection/tekton/tasks/run-workload-blis-observe.yaml`

```yaml
params:
  - name: endpoint       # e.g. http://gateway-svc:8000/v1
  - name: workloadSpec   # inline YAML content of workload file
  - name: resultsDir     # path under /workspace/data to write output
  - name: blisImage      # container image with blis observe (from values.yaml observe.image)
  - name: timeout        # default "30m"

steps:
  - name: write-workload-spec
    image: alpine:3.19
    script: |
      cat > /workspace/workload.yaml <<'EOF'
      $(params.workloadSpec)
      EOF

  - name: run-observe
    image: $(params.blisImage)
    script: |
      blis observe \
        --endpoint $(params.endpoint) \
        --workload-spec /workspace/workload.yaml \
        --output-dir /workspace/data/$(params.resultsDir) \
        --timeout $(params.timeout)
```

**Provisional flags:** The `--endpoint`, `--workload-spec`, `--output-dir`, and `--timeout` flags shown above are based on the expected interface from inference-sim PR #704, which is **not yet merged**. If the merged PR uses different flag names, this task definition and the `compile-pipeline` template must be updated to match. The implementation PR should verify flag names against the actual merged `observeCmd` registration.

**TraceV2 output format:** `blis observe` produces **two files** per run (TraceV2 format, defined in `inference-sim/sim/workload/tracev2.go`), written to `--output-dir`:

- **`trace_header.yaml`** — Run metadata (struct `TraceHeader`): `trace_version` (int, 2), `time_unit` (string, "microseconds"), `created_at` (string, omitempty), `mode` (string, "real" or "generated"), `warm_up_requests` (int), `workload_spec` (string, omitempty), `server` (object, omitempty: type, model, tensor_parallel, etc.), `network` (object, omitempty: measured_rtt_ms).
- **`trace_data.csv`** — Per-request measurements with raw microsecond timestamps. Columns (from `traceV2Columns`): `request_id`, `client_id`, `tenant_id`, `slo_class`, `session_id`, `round_index`, `prefix_group`, `streaming`, `input_tokens`, `output_tokens`, `text_tokens`, `image_tokens`, `audio_tokens`, `video_tokens`, `reason_ratio`, `model`, `deadline_us`, `server_input_tokens`, `arrival_time_us`, `send_time_us`, `first_chunk_time_us`, `last_chunk_time_us`, `num_chunks`, `status` (string, "ok", "error", or "timeout"), `error_message`. **No pre-computed latency columns exist** — latencies must be derived from timestamps.

`convert-trace` **derives** latency metrics from raw timestamps and converts microseconds to milliseconds:
- `ttft = (first_chunk_time_us - send_time_us) / 1000.0` (time-to-first-token, ms)
- `tpot = (last_chunk_time_us - first_chunk_time_us) / (max(num_chunks - 1, 1)) / 1000.0` (time-per-output-token, ms)
- `total_latency = (last_chunk_time_us - send_time_us) / 1000.0` (total latency, ms)

Percentile computation: `ttft_p50 = percentile(ttft, 50)`, `ttft_p99 = percentile(ttft, 99)`, `tpot_p50 = percentile(tpot, 50)`, `tpot_p99 = percentile(tpot, 99)`. Only rows with `status == "ok"` are included. **Zero valid rows:** If all rows in a workload's `trace_data.csv` have `status != "ok"` (e.g., all requests timed out during cluster instability), the latency arrays are empty and percentile computation is undefined. In this case, `convert-trace` MUST exit 1 with error message: `"ERROR: workload '<workload_name>' has 0 rows with status 'ok' in <path>/trace_data.csv — all requests failed or timed out."` This surfaces the problem clearly at the convert-trace step rather than producing NaN/zero metrics that propagate silently to `benchmark`.

The header YAML is read for `workload_spec` metadata. **Workload name validation** uses the subdirectory name (e.g., `workspace/noise_raw/glia-40qps/` → workload name `glia-40qps`); the header does not contain a `workload_name` field.

**Note:** The `blis observe` CLI flags (`--endpoint`, `--workload-spec`, `--output-dir`, `--timeout`) are provisional — they reflect the expected interface from inference-sim PR #704. If the merged PR uses different flag names, the task definition must be updated.

Both files are written to the shared `data-pvc`. Conversion to the canonical metrics format happens locally after `kubectl cp` extraction (see Step 5b below) — `transfer_cli.py` is not packaged into the task image.

**Image prerequisite:** The `blis` image must be built from inference-sim at a commit that includes PR #704 (`feat(cmd): add blis observe command`). The submodule at `afb92d4` predates #704. **Current state:** `cmd/observe.go` exists in the submodule but contains only HTTP client utilities (`RealClient`, `Recorder`) — the `blis observe` Cobra command is **not yet registered** in `rootCmd`. The submodule must be bumped to a commit where PR #704 is merged and `observeCmd` is wired into `rootCmd.AddCommand()`.

Stage 1 gains a prerequisite check: verify that the inference-sim submodule commit SHA is at or past the minimum commit hash where PR #704 was merged. The minimum commit hash will be recorded in `docs/transfer/blis_to_llmd_mapping.md` under a new `## Submodule Prerequisites` section (added in the implementation PR). File-presence checks (e.g., `cmd/observe.go` exists) are **not sufficient** because the file exists at the current submodule commit without a wired command registration. If the check fails: "HALT: inference-sim submodule does not include `blis observe` (#704). Bump the submodule before proceeding."

---

## Results Extraction

Completed Tekton task pods are subject to GC by the cluster's `completedTaskRunTTL`. Rather than `kubectl cp` from a completed task pod (which may be evicted), results are extracted using a short-lived extractor pod that mounts `data-pvc` directly:

```bash
cleanup_extract_pod() {
  kubectl delete pod sim2real-extract-<phase> -n <namespace> --ignore-not-found 2>/dev/null
}
trap cleanup_extract_pod EXIT ERR

kubectl run sim2real-extract-<phase> \
  --image=alpine:3.19 \
  --restart=Never \
  --overrides='{"spec":{"volumes":[{"name":"data","persistentVolumeClaim":{"claimName":"data-pvc"}}],"containers":[{"name":"extract","image":"alpine:3.19","command":["sleep","600"],"volumeMounts":[{"name":"data","mountPath":"/data"}]}]}}' \
  -n <namespace>

kubectl wait pod/sim2real-extract-<phase> --for=condition=Ready --timeout=60s -n <namespace>

# TraceV2 output: two files per workload directory
kubectl cp <namespace>/sim2real-extract-<phase>:/data/<phase>/ \
  workspace/<phase>_raw/ --retries=3
```

The `trap` ensures the extractor pod is deleted regardless of whether extraction succeeds or fails. Without this, a failed `kubectl wait` or `kubectl cp` leaves the pod running, and subsequent retries fail with `AlreadyExists`.

**Sleep duration:** The extractor pod runs `sleep 600` (10 minutes). This provides a window for `kubectl cp` to complete. If `kubectl cp` exceeds 10 minutes (possible for large TraceV2 outputs with many workloads), the pod exits and the copy fails. **Mitigation:** The `--retries=3` flag on `kubectl cp` handles transient failures. If the pod exits before copy completes, `convert-trace` will fail on missing/incomplete files, halting the pipeline with a clear error. The operator can increase the sleep duration in the `--overrides` JSON and re-run. A future improvement could use `sleep infinity` with an explicit `kubectl delete` after copy, but `sleep 600` is sufficient for the expected data volumes (2 workloads × 3 phases).

This approach is independent of task pod lifecycle and works regardless of cluster `completedTaskRunTTL` settings.

---

## Pre-flight Checks

Implemented as `transfer_cli.py preflight --phase <noise|baseline|treatment> --values <path> --namespace <ns>`.

Prints a ✓/✗ checklist. Exit 0 = all pass, 1 = one or more failed, 2 = CLI error.

### Universal checks (all phases)

| Check | How verified |
|---|---|
| `kubectl` reachable | `kubectl cluster-info` |
| Tekton Pipelines CRD installed | `kubectl get crd pipelines.tekton.dev` |
| Target namespace exists | `kubectl get ns <namespace>` |
| `hf-secret` present in namespace | `kubectl get secret hf-secret -n <ns>` |
| `model-pvc` (300Gi RWX) present | `kubectl get pvc model-pvc -n <ns>` |
| `data-pvc` (20Gi RWX) present | `kubectl get pvc data-pvc -n <ns>` |
| GPU nodes available | Read `stack.model.helmValues.decode.acceleratorTypes.labelKey` + `labelValues` from `values.yaml`; verify `≥ decode.replicas` nodes with that label are Ready |
| `tkn` CLI present | `tkn version` |
| RBAC roles applied | `kubectl get rolebinding helm-access-binding -n <ns> && kubectl get rolebinding helm-installer-restricted-scc -n <ns> && kubectl get clusterrolebinding helm-installer-crb-<ns>` (verifies both namespace-scoped RoleBindings and the cluster-scoped ClusterRoleBinding from `tektonc-data-collection/tekton/roles.yaml`; the ClusterRoleBinding `helm-installer-crb-${NAMESPACE}` binds `helm-installer` to `helm-installer-clusterrole` for cluster-scoped resources — if missing, `deploy-model` and `deploy-gaie` tasks fail with RBAC errors mid-pipeline) |
| `observe.image` TAG placeholder resolved | Read `observe.image` from `values.yaml`; fail if the value contains the literal string `<TAG>` (indicates Stage 3 failed to resolve the placeholder via `git describe --tags`). Error message: `"FAIL: observe.image contains unresolved <TAG> placeholder — re-run Stage 3 generate to resolve."` This check MUST run before the image pull check below. |
| `blis` image pullable | Cleanup via trap (same pattern as extractor pod): `cleanup_pull_test() { kubectl delete pod blis-pull-test -n <ns> --ignore-not-found 2>/dev/null; }; trap cleanup_pull_test EXIT ERR; kubectl run blis-pull-test --image=<observe.image> --restart=Never --command -- true -n <ns> && kubectl wait pod/blis-pull-test --for=jsonpath='{.status.phase}'=Succeeded --timeout=60s -n <ns>` (runs a real pod to verify image pull; uses `jsonpath` wait instead of `condition=Ready` because a `restartPolicy: Never` pod running `true` may reach `Succeeded` phase before the `Ready` condition is observed; `--dry-run=client` only validates schema locally and never contacts the registry). The trap ensures the pod is cleaned up even if `kubectl wait` times out or fails, preventing `AlreadyExists` on retry. |
| `gateway-svc` reachable in namespace | `kubectl get svc gateway-svc -n <ns>` (all workload tasks send traffic to `http://gateway-svc.<ns>:8000/v1`; if missing, all workloads fail with connection-refused after GPU resources are consumed by `deploy-model` and `deploy-gaie`) |
| `values.yaml` exists | `test -f workspace/tekton/values.yaml` (ensures Stage 3 produced the values file; this is the enforcement gate referenced by `stage3_output.schema.json`) |
| PipelineRun stub exists for phase | `test -f workspace/tekton/pipelinerun-<phase>.yaml` (ensures Stage 3 produced the stub for the phase being submitted) |
| Workload YAML files exist | For each workload name in `values.yaml` `observe.workloads[].name`, verify the corresponding workload YAML file exists in `blis_router/workloads/` (e.g., `blis_router/workloads/workload_<name>.yaml`). Fail if any referenced workload file is missing — this prevents expensive pipeline runs that would fail at the `benchmark` workload classification step after consuming cluster resources. |

### Treatment-only additional checks

| Check | How verified |
|---|---|
| Treatment scorer config present | `workspace/tekton/values.yaml` contains non-empty `scorer.treatment.configContent` |
| Stage 4 passed (scorer builds) | Re-run `go build ./pkg/plugins/scorer/... && go vet ./pkg/plugins/scorer/...` in `llm-d-inference-scheduler/`; non-zero exit = fail |

### Checks intentionally omitted
HuggingFace token validity (requires a network call), GPU memory headroom, S3 credentials.

---

## `workspace/benchmark_state.json` Schema

```json
{
  "schema_version": 1,
  "algorithm_name": "blis-routing-v1",
  "created_at": "2026-03-17T10:00:00Z",
  "namespace": "sim2real-bench",
  "cluster_context": "my-cluster",
  "phases": {
    "noise": {
      "status": "done",
      "pipelinerun_name": "sim2real-noise-1742000000",
      "submitted_at": "2026-03-17T10:05:00Z",
      "completed_at": "2026-03-17T10:45:00Z",
      "results_pvc_path": "noise/",
      "results_local_path": "workspace/noise_results.json",
      "failure_reason": null
    },
    "baseline": {
      "status": "pending",
      "pipelinerun_name": null,
      "submitted_at": null,
      "completed_at": null,
      "results_pvc_path": "baseline/",
      "results_local_path": null,
      "failure_reason": null
    },
    "treatment": {
      "status": "pending",
      "pipelinerun_name": null,
      "submitted_at": null,
      "completed_at": null,
      "results_pvc_path": "treatment/",
      "results_local_path": null,
      "failure_reason": null
    }
  }
}
```

**Status semantics:**
- `pending` — not yet submitted
- `running` — PipelineRun submitted; on re-entry, poll its current status before deciding to wait or re-submit
- `done` — results extracted to `results_local_path`; skip this phase on re-entry
- `failed` — previous attempt halted; show `failure_reason` and re-run pre-flight before re-submitting

**Cluster context guard:** On Stage 5 re-entry, if `kubectl config current-context` differs from `cluster_context` in the state file, `benchmark-state` exits 1 with message: *"ERROR: State was recorded against cluster `<recorded>` but current context is `<current>`. Delete workspace/benchmark_state.json to start fresh against the new cluster, or switch back to the original context."* Step 5a HALTs on this exit code (see INV-2).

---

## New `transfer_cli.py` Subcommands

**Note on `compile-pipeline` and `preflight`:** `compile-pipeline` shells out to `.venv/bin/python tektonc-data-collection/tektonc/tektonc.py`. **Subprocess safety requirement:** `compile-pipeline` MUST use `subprocess.run([...], shell=False)` with an explicit argument list (not f-string path concatenation or `shell=True`) to prevent shell metacharacter injection via `--template-dir` or `--values` path arguments. This is especially important because this CLI tool influences cluster resources via kubectl. `preflight` parses nested YAML keys from `values.yaml` (e.g., `stack.model.helmValues.decode.acceleratorTypes.labelKey`) for the GPU node check. Both require `jinja2` and `PyYAML` (third-party). These must be installed in `.venv`. The "stdlib only" constraint in `CLAUDE.md` applies to the sim2real pipeline tools but is amended here to allow these dependencies for the `compile-pipeline` and `preflight` paths. `CLAUDE.md` will be updated in the implementation PR to note this.

| Subcommand | Purpose | Exit codes |
|---|---|---|
| `preflight --phase <p> --values <f> --namespace <ns>` | Run phase-appropriate cluster checks, print ✓/✗ checklist | 0 = all pass, 1 = ≥1 failed, 2 = CLI error |
| `benchmark-state --workspace <dir> --namespace <ns>` | Print current phase statuses as JSON to stdout (full `benchmark_state.json` content). If the file does not exist, creates it with all phases `pending`, recording `--namespace` and `kubectl config current-context` as `cluster_context`. The required `algorithm_name` field is read from `workspace/algorithm_summary.json` (`.algorithm_name`); if that file is missing or lacks the field, exits 2 with an error message. Also checks cluster context guard (exits 1 if current context differs from recorded context). `--namespace` is required on first invocation (file creation); on subsequent invocations it is optional (ignored if file exists). | 0 = success, 1 = cluster context mismatch, 2 = error |
| `benchmark-state --workspace <dir> --set-phase <p> --status <s> [--pipelinerun <n>] [--results <path>] [--failure-reason <msg>] [--force]` | Update phase status in state file. **Phase ordering guard (INV-1):** when setting a phase to `running`, verifies the previous phase in the sequence (`noise → baseline → treatment`) has `status == "done"`. Exits 2 if the previous phase is not done (e.g., setting baseline to running when noise is still pending). `--force` bypasses this check. **Status regression guard (INV-3):** rejects `done→pending` and `done→running` transitions (exits 2 with error message) unless `--force` is specified. | 0 = success, 2 = error |
| `convert-trace --input-dir <dir> --output <metrics.json>` | Convert `blis observe` TraceV2 output to per-workload metrics JSON consumed by `benchmark`. All numeric CSV fields (`first_chunk_time_us`, `last_chunk_time_us`, `send_time_us`, `num_chunks`) MUST be cast to `int` or `float` before arithmetic operations (Python's `csv.DictReader` returns strings). Missing input files (`trace_header.yaml` or `trace_data.csv`) MUST produce an actionable error message: `"ERROR: missing <filename> in <workload_dir> — blis observe may have crashed mid-write."` (not an unhandled `FileNotFoundError` traceback). Input: a **phase directory** containing per-workload subdirectories (e.g., `workspace/noise_raw/glia-40qps/`, `workspace/noise_raw/prefix-heavy/`). For baseline/treatment phases, each workload subdirectory contains `trace_header.yaml` and `trace_data.csv` directly. For the noise phase, each workload subdirectory contains `run-{0..4}/` subdirectories, each with `trace_header.yaml` and `trace_data.csv`. `convert-trace` auto-detects the structure: if `run-*` subdirectories exist, it produces per-run output; otherwise single-value output. **Single-value output** (baseline/treatment): `{"workloads": [{"name": "<workload>", "metrics": {"ttft_p50": <float>, "ttft_p99": <float>, "tpot_p50": <float>, "tpot_p99": <float>}}]}`. **Per-run output** (noise): `{"workloads": [{"name": "<workload>", "runs": [{"metrics": {"ttft_p50": <float>, "ttft_p99": <float>, "tpot_p50": <float>, "tpot_p99": <float>}}, ...]}]}`. Workload names are derived from subdirectory names. **Schema:** Three schema files must be created as part of the implementation PR to validate `convert-trace` output: `tools/schemas/noise_results.schema.json`, `tools/schemas/baseline_results.schema.json`, and `tools/schemas/treatment_results.schema.json`. **The noise schema differs from the baseline/treatment schemas** because the noise phase produces per-run output (`{"workloads": [{"name": ..., "runs": [{"metrics": {...}}]}]}`) with an intermediate `runs` array, while baseline/treatment produce single-value output (`{"workloads": [{"name": ..., "metrics": {...}}]}`). The baseline and treatment schemas may be identical copies of each other. This is required because `transfer_cli.py validate-schema` resolves the schema file by the artifact's filename stem (e.g., `noise_results.json` → `noise_results.schema.json`). Step 5b calls `validate-schema workspace/<phase>_results.json` after `convert-trace` to enforce the Validation Strategy's "all workspace JSON artifacts are schema-validated" contract. | 0 = success, 1 = conversion error, 2 = CLI error (bad arguments, missing required flags, unreadable input paths) |
| `compile-pipeline --template-dir <d> --values <f> --phase <p> --out <dir>` | Shell out to `tektonc.py`; wrapper keeps Stage 5 prompt agnostic of tektonc invocation | 0 = success, 1 = compile error, 2 = CLI error (bad arguments, missing required flags, unreadable input paths) |
| `render-pipelinerun --template <f> --vars KEY=VAL ... --out <f>` | Substitute variables in a PipelineRun stub (fallback if `envsubst` unavailable). `--vars` uses `nargs='+'` (one or more space-separated `KEY=VAL` arguments). Placeholder syntax: `$VARIABLE` or `${VARIABLE}` (envsubst-compatible). After substitution, exit 1 if any unresolved `$VARIABLE`/`${VARIABLE}` placeholders remain in the output. | 0 = success, 1 = unresolved placeholders in output, 2 = CLI error |
| `generate-evidence --workspace <dir> --out <file> [--calibration-log <path>]` | Read workspace artifacts and write `transfer_evidence.md` (see Evidence Document section). `--calibration-log` defaults to `docs/transfer/calibration_log.md`. If the calibration log file is missing, `calibration_n` defaults to 1 (first transfer) with a warning on stderr — this is not a fatal error. | 0 = success, 1 = missing workspace inputs, 2 = CLI error (bad arguments, missing required flags) |
| `benchmark --noise <f> --baseline <f> --treatment <f> --signal-coverage <f> --workloads-dir <d> --out <f>` | Compute T_eff from noise results, classify workloads, run mechanism check. Replaces old `benchmark --results --t-eff` interface. When `--out` is specified, writes JSON to the output file **and suppresses stdout JSON** (only stderr diagnostics are emitted). Without `--out`, writes JSON to stdout (backward-compatible for callers expecting stdout). | 0 = PASS, INCONCLUSIVE, or ERROR (parse `mechanism_check_verdict` from JSON), 1 = FAIL, 2 = CLI error |

### Breaking change to `benchmark`

The existing `benchmark --results <file> --t-eff <float>` interface (recorded in `CLAUDE.md`) is **replaced** by:

```bash
transfer_cli.py benchmark \
  --noise    workspace/noise_results.json \
  --baseline workspace/baseline_results.json \
  --treatment workspace/treatment_results.json
```

The old `--results` and `--t-eff` flags are removed. `benchmark` now computes T_eff internally from the noise results file. `CLAUDE.md` CLI Commands section is updated in the implementation PR. The new inputs are the converted (post-`convert-trace`) per-phase metric files.

**Input contract for new interface:**

1. **Improvement metric:** `benchmark` uses `ttft_p99` from the convert-trace output for improvement computation. For each workload, `improvement = (baseline_ttft_p99 - treatment_ttft_p99) / baseline_ttft_p99`. This matches the semantics of the old `baseline_p99`/`transfer_p99` fields (which were P99 TTFT latency).

2. **Workload classification:** The new interface adds `--signal-coverage workspace/signal_coverage.json` and `--workloads-dir blis_router/workloads/`. `benchmark` automatically derives matched/unmatched classification using the same rules as validate.md Step 5: a workload is "matched" if any of its exercised signals (inferred from workload YAML fields per the mapping table in validate.md) appear in `signal_coverage.json` with `mapped: true`. This replaces the operator-supplied `classification` field in the old input format. **Implementation dependency:** The workload-to-signal mapping rules are extracted into a shared data file at `tools/workload_signal_mapping.json`. This file defines, for each workload YAML field, the simulation signal(s) it exercises. The `benchmark` Python implementation loads this file at runtime; the `prompts/validate.md` replacement text references the same file for human-readable documentation of the rules. The mapping file is a deliverable of the implementation PR. The `matched_signals` array in `benchmark_output.json` lists the signal names that matched for each workload.

   **`signal_coverage.json` format** (produced by Stage 2, `prompts/translate.md`; schema: `tools/schemas/signal_coverage.schema.json`): `{"signals": [{"sim_name": "<signal_name>", "prod_name": "<production_field>", "prod_access_path": "<Go_access_code>", "fidelity_rating": "high|medium|low", "staleness_window_ms": <int>, "mapped": <bool>}], "unmapped_signals": [...], "commit_hash": "<hex>", "coverage_complete": <bool>}`. Each entry describes a simulation signal and its production mapping. The `mapped` field is the only field consumed by `benchmark` for workload classification; other fields are informational.

3. **Output schema:** `benchmark --out` writes `{"t_eff": <float>, "mechanism_check_verdict": "<PASS|FAIL|INCONCLUSIVE|ERROR>", "passed": <bool>, "workload_classification": [{"workload": "<name>", "classification": "matched|unmatched", "improvement": <float>, "matched_signals": ["<signal>", ...]}], "specificity_notes": ["<string>", ...], "noise_cv": <float>}`. The `workload_classification` array matches the format required by `validation_results.schema.json` § `benchmark.workload_classification`. `specificity_notes` is an array of human-readable strings (e.g., `"workload glia-40qps: |change|/baseline = 0.12 >= T_eff"`) matching the `validation_results.schema.json` § `benchmark.specificity_notes` type (array of strings). Empty array if no unmatched workloads exceed T_eff.

   **`benchmark_output.schema.json` content specification:** The schema MUST enforce: all top-level fields (`t_eff`, `mechanism_check_verdict`, `passed`, `workload_classification`, `specificity_notes`, `noise_cv`) are **required**. `t_eff` and `noise_cv`: type `number`, minimum `0`. `mechanism_check_verdict`: type `string`, enum `["PASS", "FAIL", "INCONCLUSIVE", "ERROR"]`. `passed`: type `boolean`. `workload_classification`: type `array`, items are objects with required fields `workload` (string), `classification` (string, enum `["matched", "unmatched"]`), `improvement` (number), `matched_signals` (array of strings). `specificity_notes`: type `array`, items are strings. Top-level `additionalProperties: false`. `workload_classification` items: `additionalProperties: false`. When `--out` is specified, the output file is **always** written for **all exit codes** (exit 0: PASS/INCONCLUSIVE/ERROR, and exit 1: FAIL). This guarantees that `workspace/benchmark_output.json` exists regardless of verdict, allowing Step 5c HALT messages to reference it and subsequent `json.load(open(...))` calls to succeed.

---

## Updated Stage 5 Cluster Benchmark Flow

**What changes:** The existing `prompts/validate.md` Step 1 (noise characterization, "OPERATOR ACTION REQUIRED") and Step 5 (cluster benchmarks, "OPERATOR ACTION REQUIRED") are fully replaced by the automated flow below. Steps 2–4 (Suites A/B/C) are **unchanged**.

**What stays from old Step 5:** The workload classification logic (matched/unmatched determination) and the `benchmark` call with HALT conditions on FAIL/INCONCLUSIVE remain — only the mechanism for producing the input files changes.

**`prompts/validate.md` replacement text is a deliverable of the implementation PR.**

### Routing Preamble (first-invocation detection)

The `prompts/validate.md` replacement text MUST begin with a routing preamble that determines execution order. On first invocation, the noise phase has not run, so Steps 2-4 (Suites A/B/C) cannot proceed until noise is done. The preamble detects this and routes to Step 5 noise-first:

**`<NAMESPACE>` resolution:** Throughout the routing preamble and Steps 1–7, `<NAMESPACE>` is a placeholder that the LLM or operator must resolve before execution. On first invocation (no `benchmark_state.json` exists), the namespace MUST be provided explicitly — either from an operator-set environment variable (`$NAMESPACE`) or by prompting the operator. On re-entry (file exists), `benchmark-state` reads the namespace from the state file, so `--namespace` is optional. The Stage 5 prompt (`prompts/validate.md` replacement text) MUST include an explicit instruction to resolve `<NAMESPACE>` before any cluster commands are executed.

```bash
# --- Routing preamble: detect first invocation and run noise before Suites A/B/C ---
# Resolve NAMESPACE before any cluster commands (see resolution note above)
# Initialize state (creates benchmark_state.json if absent)
BENCH_STATE_OUTPUT=$(.venv/bin/python tools/transfer_cli.py benchmark-state --workspace workspace/ --namespace $NAMESPACE)
BENCH_STATE_EXIT=$?
if [ $BENCH_STATE_EXIT -eq 2 ]; then
  echo "HALT: benchmark-state failed (exit 2 — missing algorithm_summary.json or parse error). Ensure Stage 1 extract has been run."
  exit 1
elif [ $BENCH_STATE_EXIT -ne 0 ]; then
  echo "HALT: benchmark-state failed (exit $BENCH_STATE_EXIT). Check cluster context and workspace state."
  echo "$BENCH_STATE_OUTPUT"
  exit 1
fi

NOISE_STATUS=$(echo "$BENCH_STATE_OUTPUT" \
  | .venv/bin/python -c "import sys,json; print(json.load(sys.stdin)['phases']['noise']['status'])")
if [ "$NOISE_STATUS" != "done" ]; then
  echo "Noise phase is '$NOISE_STATUS' — must complete before Suites A/B/C."
  echo "Routing to Step 5a/5b for noise phase first."
  # Route to Step 5 for noise phase only, then signal re-entry.
  # The prompts/validate.md replacement text MUST implement this routing by:
  #   1. Defining a shell function `run_step5b_phase()` that encapsulates the
  #      Step 5b logic (preflight → compile → submit → poll → extract → convert).
  #   2. Calling `run_step5b_phase noise` here.
  #   3. After the function returns successfully, printing a re-entry instruction:
  #      "Noise phase complete. Re-entering Stage 5 from the top."
  #   4. After the function returns successfully, signaling re-entry:
  #      - **Script execution:** Use `exec "$0" "$@"` to restart the script.
  #      - **LLM agent execution:** Print a REENTER directive and exit 0.
  #        The LLM agent reads the directive and re-executes Stage 5 from the top.
  #      The prompts/validate.md replacement text MUST specify both mechanisms.
  #   If the function fails (non-zero exit), the HALT inside the function
  #   prevents re-entry.
  run_step5b_phase noise
  echo "Noise phase complete. Re-entering Stage 5 from the top."
  # For script execution: exec "$0" "$@"
  # For LLM agent execution: the agent reads this directive and re-enters Stage 5.
  echo "REENTER: Stage 5 — noise phase complete, re-run validate.md from the top."
  exit 0
fi
```

The replacement `prompts/validate.md` MUST implement this routing logic. On first invocation, the LLM executes Step 5 for the noise phase, then re-enters from the top. **Re-entry mechanism:** For standalone script execution, use `exec "$0" "$@"`. For LLM agent execution (where `$0` is not a script path and `exec` would replace the shell session), print a `REENTER:` directive and exit 0; the LLM agent reads this directive and re-executes `prompts/validate.md` from the top. The replacement text MUST document both mechanisms. On subsequent invocations (noise already `done`), execution proceeds directly to Step 1 → Steps 2–4 → Step 5 (baseline/treatment).

### Step 1: Noise Characterization Gate (automated — replaces manual Step 1)

Noise characterization now runs as the `noise` phase of the cluster pipeline (Step 5a/5b). Step 1 in the updated `validate.md` is a gate that verifies noise is done before proceeding to Suites A/B/C. The routing preamble above handles the first-invocation case. (See `<NAMESPACE>` resolution note in the Routing Preamble section above.)

```bash
# Verify benchmark_state.json noise phase is done before proceeding to Suites A/B/C
# (The routing preamble above already initialized state and checked noise status.
#  This check is a safety gate for the linear Step 1 → Step 2 flow.)
BENCH_STATE_OUTPUT=$(.venv/bin/python tools/transfer_cli.py benchmark-state --workspace workspace/ --namespace <NAMESPACE>)
BENCH_STATE_EXIT=$?
if [ $BENCH_STATE_EXIT -ne 0 ]; then
  echo "HALT: benchmark-state failed (exit $BENCH_STATE_EXIT). Check cluster context and workspace state."
  echo "$BENCH_STATE_OUTPUT"
  exit 1
fi
NOISE_STATUS=$(echo "$BENCH_STATE_OUTPUT" \
  | .venv/bin/python -c "import sys,json; print(json.load(sys.stdin)['phases']['noise']['status'])")
if [ "$NOISE_STATUS" != "done" ]; then
  echo "HALT: Noise characterization phase is '$NOISE_STATUS', not 'done'. Run Step 5a/5b for the noise phase before proceeding to Suites A/B/C."
  exit 1
fi
```

T_eff is computed from `workspace/noise_results.json` by `transfer_cli.py benchmark` (Step 5c), not by a separate `noise-characterize` call. The old `baseline_runs.json` format and the `noise-characterize` subcommand are superseded by the noise pipeline output. **The implementation PR must remove the `noise-characterize` subcommand from `transfer_cli.py`** (currently registered at line ~1217) and update `CLAUDE.md` to remove its documentation.

### Step 5: Cluster Benchmarks

#### 5a. Initialize state
```bash
.venv/bin/python tools/transfer_cli.py benchmark-state --workspace workspace/ --namespace <NAMESPACE>
```
**HALT if exit 1** (cluster context mismatch). Message: "HALT: Cluster context mismatch — state was recorded against a different cluster. Confirm the correct cluster before proceeding."
**HALT if exit 2** (missing `workspace/algorithm_summary.json` or missing `.algorithm_name` field). Message: "HALT: benchmark-state could not read algorithm_summary.json. Ensure Stage 1 extract has been run successfully."

Creates `workspace/benchmark_state.json` if absent (all phases `pending`, recording `--namespace` and current cluster context). Prints current state as JSON to stdout.

#### 5b. For each non-done phase in order: noise → baseline → treatment

For each phase, check its status:
- `done` → skip (already completed on a prior run)
- `pending` → run pre-flight, compile, submit, poll (full flow below)
- `running` → a prior Stage 5 invocation submitted a PipelineRun but crashed before recording the outcome. Read `pipelinerun_name` from the state file and **resume polling** (skip directly to the "Wait" step below):
  ```bash
  PIPELINERUN_NAME=$(.venv/bin/python -c "import json; print(json.load(open('workspace/benchmark_state.json'))['phases']['<phase>']['pipelinerun_name'])")
  ```
  If the PipelineRun no longer exists (`tkn pr describe $PIPELINERUN_NAME` returns not-found), set the phase to `failed` with reason "PipelineRun not found on re-entry" and HALT so the operator can re-submit.
- `failed` → re-run pre-flight and re-submit (full flow below, treating as `pending`)

**Pre-flight:**
```bash
.venv/bin/python tools/transfer_cli.py preflight \
  --phase <phase> --values workspace/tekton/values.yaml --namespace <NAMESPACE>
```
**HALT if exit 1.** Do not submit pipeline.

**Compile:**
```bash
.venv/bin/python tools/transfer_cli.py compile-pipeline \
  --template-dir tektonc-data-collection/tekton \
  --values workspace/tekton/values.yaml \
  --phase <phase> \
  --out workspace/tekton/compiled/
if [ $? -ne 0 ]; then
  echo "HALT: compile-pipeline failed for <phase>. Check template and values.yaml."
  exit 1
fi
```

**Submit:**
```bash
kubectl apply -f workspace/tekton/compiled/<phase>-pipeline.yaml
if [ $? -ne 0 ]; then
  echo "HALT: kubectl apply failed for <phase>-pipeline.yaml. Check RBAC, API version, and YAML syntax."
  exit 1
fi

# Guard against duplicate PipelineRuns: check if a PipelineRun for this phase
# is already active (Running/pending). This prevents double-submission when
# benchmark-state --set-phase running fails after kubectl apply succeeds.
EXISTING_PR=$(kubectl get pipelinerun -n $NAMESPACE \
  -l "sim2real-phase=<phase>" \
  -o jsonpath='{.items[?(@.status.conditions[0].reason!="Succeeded")].metadata.name}' 2>/dev/null | head -1)
if [ -n "$EXISTING_PR" ]; then
  echo "HALT: Active PipelineRun '$EXISTING_PR' already exists for <phase>. Check with 'tkn pr describe $EXISTING_PR' before re-submitting."
  exit 1
fi

PIPELINERUN_NAME=sim2real-<phase>-$(date +%s)
.venv/bin/python tools/transfer_cli.py render-pipelinerun \
  --template workspace/tekton/pipelinerun-<phase>.yaml \
  --vars PIPELINERUN_NAME=$PIPELINERUN_NAME NAMESPACE=<NAMESPACE> PHASE=<phase> \
  --out /tmp/pipelinerun-<phase>.yaml
kubectl apply -f /tmp/pipelinerun-<phase>.yaml
if [ $? -ne 0 ]; then
  echo "HALT: kubectl apply failed for PipelineRun /tmp/pipelinerun-<phase>.yaml. Pipeline definition was applied but PipelineRun was not created."
  exit 1
fi

.venv/bin/python tools/transfer_cli.py benchmark-state --workspace workspace/ \
  --set-phase <phase> --status running --pipelinerun $PIPELINERUN_NAME
if [ $? -ne 0 ]; then
  echo "HALT: failed to update benchmark state to 'running'. PipelineRun $PIPELINERUN_NAME may be running — check with 'tkn pr describe $PIPELINERUN_NAME' before re-submitting."
  exit 1
fi
```

**Wait (with timeout):**
```bash
TIMEOUT_SECS=14400  # 4 hours
ELAPSED=0
EMPTY_REASON_COUNT=0
while true; do
  REASON=$(tkn pr describe $PIPELINERUN_NAME \
      -o jsonpath='{.status.conditions[0].reason}' 2>/dev/null)
  if [ -z "$REASON" ]; then
    EMPTY_REASON_COUNT=$((EMPTY_REASON_COUNT + 1))
    if [ $EMPTY_REASON_COUNT -ge 3 ]; then
      .venv/bin/python tools/transfer_cli.py benchmark-state --workspace workspace/ \
        --set-phase <phase> --status failed \
        --failure-reason "PipelineRun $PIPELINERUN_NAME not found or not reporting status after 3 consecutive checks"
      echo "HALT: <phase> PipelineRun disappeared or is unreachable. Check if it was deleted or GC'd."
      exit 1
    fi
  else
    EMPTY_REASON_COUNT=0
    echo "$REASON" | grep -qE 'Succeeded|Failed' && break
  fi
  sleep 30
  ELAPSED=$((ELAPSED + 30))
  if [ $ELAPSED -ge $TIMEOUT_SECS ]; then
    .venv/bin/python tools/transfer_cli.py benchmark-state --workspace workspace/ \
      --set-phase <phase> --status failed \
      --failure-reason "Polling timeout after ${TIMEOUT_SECS}s — PipelineRun still Running"
    echo "HALT: <phase> pipeline timed out. Check cluster state and re-run Stage 5 to resume."
    exit 1
  fi
done
```

**On `Failed`:**
```bash
# Truncate failure reason to first 4096 bytes to prevent unbounded growth
# in benchmark_state.json (full output can span kilobytes to megabytes)
FAILURE_REASON=$(tkn pr describe $PIPELINERUN_NAME 2>&1 | head -c 4096)
.venv/bin/python tools/transfer_cli.py benchmark-state --workspace workspace/ \
  --set-phase <phase> --status failed \
  --failure-reason "$FAILURE_REASON"
```
**HALT.** Message: "HALT: `<phase>` pipeline failed. Inspect with `tkn pr describe $PIPELINERUN_NAME`. Fix the issue and re-run Stage 5 to resume from this phase."

**On `Succeeded` — extract results via extractor pod:**
```bash
# Unconditional cleanup — prevents AlreadyExists on retry
cleanup_extract_pod() {
  kubectl delete pod sim2real-extract-<phase> -n <NAMESPACE> --ignore-not-found 2>/dev/null
}
trap cleanup_extract_pod EXIT ERR

kubectl run sim2real-extract-<phase> \
  --image=alpine:3.19 --restart=Never \
  --overrides='{"spec":{"volumes":[{"name":"data","persistentVolumeClaim":{"claimName":"data-pvc"}}],"containers":[{"name":"e","image":"alpine:3.19","command":["sleep","600"],"volumeMounts":[{"name":"data","mountPath":"/data"}]}]}}' \
  -n <NAMESPACE>

kubectl wait pod/sim2real-extract-<phase> \
  --for=condition=Ready --timeout=60s -n <NAMESPACE>

# Clean stale data before extracting (prevents mix of stale and fresh data on re-entry)
rm -rf workspace/<phase>_raw/

# Extract TraceV2 two-file output (header YAML + data CSV) per workload
kubectl cp <NAMESPACE>/sim2real-extract-<phase>:/data/<phase>/ \
  workspace/<phase>_raw/ --retries=3
if [ $? -ne 0 ]; then
  .venv/bin/python tools/transfer_cli.py benchmark-state --workspace workspace/ \
    --set-phase <phase> --status failed \
    --failure-reason "kubectl cp failed for <phase> after 3 retries"
  echo "HALT: kubectl cp failed for <phase>. Check extractor pod and PVC contents."
  exit 1
fi

# Explicitly delete extractor pod after successful copy (don't rely solely on trap,
# which is overwritten per-phase in the loop — see INV-4)
kubectl delete pod sim2real-extract-<phase> -n <NAMESPACE> --ignore-not-found 2>/dev/null

# convert-trace: reads phase directory tree (per-workload subdirs with TraceV2 files),
# produces per-workload metrics JSON
.venv/bin/python tools/transfer_cli.py convert-trace \
  --input-dir workspace/<phase>_raw \
  --output workspace/<phase>_results.json
if [ $? -ne 0 ]; then
  .venv/bin/python tools/transfer_cli.py benchmark-state --workspace workspace/ \
    --set-phase <phase> --status failed \
    --failure-reason "convert-trace failed for <phase>"
  echo "HALT: convert-trace failed for <phase>. Check workspace/<phase>_raw/ contents."
  exit 1
fi

# Validate convert-trace output against schema
.venv/bin/python tools/transfer_cli.py validate-schema workspace/<phase>_results.json
if [ $? -ne 0 ]; then
  .venv/bin/python tools/transfer_cli.py benchmark-state --workspace workspace/ \
    --set-phase <phase> --status failed \
    --failure-reason "schema validation failed for <phase>_results.json"
  echo "HALT: <phase>_results.json schema validation failed. Check convert-trace output format."
  exit 1
fi

.venv/bin/python tools/transfer_cli.py benchmark-state --workspace workspace/ \
  --set-phase <phase> --status done \
  --results workspace/<phase>_results.json
```

#### 5c. Compute mechanism check and T_eff
```bash
# Validate signal_coverage.json before consuming it (Stage 2 output)
.venv/bin/python tools/transfer_cli.py validate-schema workspace/signal_coverage.json
if [ $? -ne 0 ]; then
  echo "HALT: signal_coverage.json schema validation failed. Re-run Stage 2 translate before proceeding."
  exit 1
fi

.venv/bin/python tools/transfer_cli.py benchmark \
  --noise    workspace/noise_results.json \
  --baseline workspace/baseline_results.json \
  --treatment workspace/treatment_results.json \
  --signal-coverage workspace/signal_coverage.json \
  --workloads-dir blis_router/workloads/ \
  --out      workspace/benchmark_output.json
BENCH_EXIT=$?
```

`benchmark` computes T_eff internally from the noise results (replaces the separate `--t-eff` argument) and also computes `noise_cv = max(per_metric_cv.values())` from the noise results. The `--out` flag writes the benchmark results (including `t_eff`, `mechanism_check_verdict`, `workload_classification`, `noise_cv`) to `workspace/benchmark_output.json`. Step 6 merges `benchmark_output.json` into `workspace/validation_results.json` under the `benchmark` key and copies `noise_cv` to the top level. (PR5 is responsible for producing the overall `validation_results.json` including `suite_a`, `suite_b`, `suite_c`, and `overall_verdict`; `benchmark` populates only the `benchmark` key and `noise_cv`.)

**HALT conditions:** The current `benchmark` CLI exits 0 for both PASS and INCONCLUSIVE (line 1165 of `transfer_cli.py`). In the automated flow, INCONCLUSIVE must also halt:
```bash
if [ $BENCH_EXIT -eq 2 ]; then
  echo "HALT: benchmark CLI error (exit 2). Check inputs and CLI configuration."
  exit 1
elif [ $BENCH_EXIT -eq 1 ]; then
  echo "HALT: benchmark verdict is FAIL. See workspace/benchmark_output.json for details."
  exit 1
elif [ $BENCH_EXIT -ne 0 ]; then
  echo "HALT: benchmark exited with unexpected code $BENCH_EXIT. See workspace/benchmark_output.json for details."
  exit 1
fi

# Validate benchmark output against schema
.venv/bin/python tools/transfer_cli.py validate-schema workspace/benchmark_output.json
if [ $? -ne 0 ]; then
  echo "HALT: benchmark_output.json schema validation failed. Check benchmark output format."
  exit 1
fi

# Check for INCONCLUSIVE or ERROR — benchmark exits 0 for both, so parse the verdict from JSON
VERDICT=$(.venv/bin/python -c "import json; print(json.load(open('workspace/benchmark_output.json'))['mechanism_check_verdict'])")
if [ "$VERDICT" = "ERROR" ]; then
  echo "HALT: benchmark verdict is ERROR (e.g., no matched workloads or internal computation failure). Operator must review workspace/benchmark_output.json and fix inputs before re-running."
  exit 1
elif [ "$VERDICT" = "INCONCLUSIVE" ]; then
  # Check if operator has already accepted INCONCLUSIVE in a prior invocation
  ACCEPTED=$(.venv/bin/python -c "import json,sys; vr='workspace/validation_results.json'; \
    import os; \
    d=json.load(open(vr)) if os.path.exists(vr) else {}; \
    print(d.get('operator_notes',''))" 2>/dev/null)
  if [ -n "$ACCEPTED" ]; then
    echo "INCONCLUSIVE verdict previously accepted by operator. Proceeding to Step 6."
  else
    echo "HALT: benchmark verdict is INCONCLUSIVE. Operator must review workspace/benchmark_output.json and decide:"
    echo "  Option A: Accept the result — write the decision rationale into workspace/validation_results.json"
    echo "            under the 'operator_notes' field (string), then re-run Stage 5 to continue to Step 6."
    echo "            Example: add '\"operator_notes\": \"Accepted because ...\"' to validation_results.json."
    echo "  Option B: Re-run the benchmarks after addressing noise/workload issues."
    echo ""
    echo "  WARNING: Do NOT write rationale into workspace/transfer_evidence.md — that file is"
    echo "  overwritten by generate-evidence in Step 7. Use 'operator_notes' in validation_results.json instead."
    exit 1
  fi
fi
```

**`benchmark` exit code contract (consolidated):** Exit 0 = PASS, INCONCLUSIVE, or ERROR (verdict determined by JSON parsing above). Exit 1 = FAIL. Exit 2 = CLI infrastructure error (bad arguments, missing files, parse failures). INCONCLUSIVE and ERROR are distinguished from PASS via the `mechanism_check_verdict` field in `workspace/benchmark_output.json`, not via exit codes. This avoids an exit code collision between INCONCLUSIVE and CLI errors. **`--out` file guarantee:** When `--out` is specified, the output file is written for **all exit codes** (0 and 1). Only exit 2 (CLI infrastructure error) may leave the file unwritten. **Implementation requirement:** All non-CLI computation failures (e.g., `StatisticsError` from empty sequences, `ZeroDivisionError`, `KeyError` from unexpected input structure) MUST be caught by a top-level `try/except` around the business logic and routed to an exit-0 `ERROR` verdict in the output file, not left as unhandled exceptions that bypass the `--out` write guarantee.

### Step 6: Write validation_results.json (updated)

**Note:** This step replaces the original Step 6 from `validate.md`. The overall structure is the same (compile results from all steps), but now includes merging `benchmark_output.json` and `noise_cv`.

Compile results from Steps 2–4 (suite results from `/tmp/*.json` files) and Step 5c (`workspace/benchmark_output.json`) into `workspace/validation_results.json`:

0. **Preserve `operator_notes`:** If `workspace/validation_results.json` already exists, read and preserve the `operator_notes` field (if present) before overwriting. This prevents Step 5c INCONCLUSIVE acceptance from being lost on re-entry when Step 6 rebuilds the file. Implementation: `existing_notes = json.load(open(path)).get('operator_notes', '')` before writing; write `operator_notes` back into the new file if non-empty.
1. **Validate suite output files exist:** Before reading suite results, verify that `/tmp/suite_a.json`, `/tmp/suite_b.json`, and `/tmp/suite_c.json` exist (`test -f`). HALT with message "HALT: Missing suite output file /tmp/suite_X.json — ensure Steps 2–4 completed successfully." if any file is missing. This prevents a confusing Python traceback if Step 6 is reached with incomplete suite runs.
2. Write `suite_a`, `suite_b`, `suite_c` from Steps 2–4 output (unchanged from current validate.md Step 6).
3. Read `workspace/benchmark_output.json` and copy **only** the following fields under the `benchmark` key: `passed`, `mechanism_check_verdict`, `t_eff`, `workload_classification`, `specificity_notes`. **Exclude `noise_cv`** from the `benchmark` sub-object — the schema defines `benchmark` with `additionalProperties: false` and `noise_cv` is not a benchmark property.
4. Copy `noise_cv` from `workspace/benchmark_output.json` to the **top-level** `noise_cv` field (a sibling of `suite_a`, `suite_b`, etc., not nested under `benchmark`).
5. Compute `overall_verdict` per the existing rules (PASS iff suite_a.passed AND suite_c.passed AND benchmark.mechanism_check_verdict == "PASS").
6. Validate: `.venv/bin/python tools/transfer_cli.py validate-schema workspace/validation_results.json`

**HALT if schema validation fails.** Message: "HALT: validation_results.json does not pass schema validation."

### Step 7: Generate evidence document

```bash
# Validate algorithm_summary.json before consuming it (guards against stale artifact from prior run)
.venv/bin/python tools/transfer_cli.py validate-schema workspace/algorithm_summary.json
if [ $? -ne 0 ]; then
  echo "HALT: algorithm_summary.json schema validation failed. Re-run Stage 1 extract before generating evidence."
  exit 1
fi

# Validate validation_results.json before consuming it (guards against partial/malformed file from interrupted Step 6)
.venv/bin/python tools/transfer_cli.py validate-schema workspace/validation_results.json
if [ $? -ne 0 ]; then
  echo "HALT: validation_results.json schema validation failed. Re-run Step 6 before generating evidence."
  exit 1
fi

.venv/bin/python tools/transfer_cli.py generate-evidence \
  --workspace workspace/ \
  --calibration-log docs/transfer/calibration_log.md \
  --out workspace/transfer_evidence.md
```

**HALT if exit 1** (missing input artifacts). Message: "HALT: generate-evidence failed — check that all workspace artifacts are present."

`generate-evidence` reads `workspace/validation_results.json` (which must be fully assembled by Step 6, including the `benchmark` key and `noise_cv`) and `workspace/algorithm_summary.json` to produce the evidence document.

---

## Evidence Document: `workspace/transfer_evidence.md`

Generated by `transfer_cli.py generate-evidence --workspace workspace/ --out workspace/transfer_evidence.md`.

**Input artifacts and field mappings:**

| Template variable | Source file | JSON key path |
|---|---|---|
| `algorithm_name` | `workspace/algorithm_summary.json` | `.algorithm_name` |
| `evolve_block_source` | `workspace/algorithm_summary.json` | `.evolve_block_source` |
| `matched_workload` | `workspace/validation_results.json` | `.benchmark.workload_classification[] | select(.classification == "matched") | .workload` (first matched entry) |
| `sim_improvement` | `workspace/algorithm_summary.json` | `.metrics.combined_score` (Note: sim predicted improvement is the fitness score from the evolutionary optimizer. A future schema version may add per-workload sim predictions — see Extension Points.) |
| `suite_a_tau` | `workspace/validation_results.json` | `.suite_a.kendall_tau` |
| `suite_a_error` | `workspace/validation_results.json` | `.suite_a.max_abs_error` |
| `suite_a_passed` | `workspace/validation_results.json` | `.suite_a.passed` |
| `suite_c_passed` | `workspace/validation_results.json` | `.suite_c.passed` |
| `suite_c_pile_on` | `workspace/validation_results.json` | `.suite_c.max_pile_on_ratio` |
| `t_eff` | `workspace/validation_results.json` | `.benchmark.t_eff` (decimal fraction, e.g. 0.10 = 10%). **Display:** `round(t_eff * 100, 1)` for percentage rendering. |
| `overall_verdict` | `workspace/validation_results.json` | `.overall_verdict` |
| `matched_pct` | `workspace/validation_results.json` | `.benchmark.workload_classification[] | select(.classification == "matched") | .improvement` (decimal fraction, e.g. 0.12 = 12% improvement). **Display:** `round(matched_pct * 100, 1)` for percentage rendering. |
| `unmatched_mean_pct` | `workspace/validation_results.json` | computed: mean of `.benchmark.workload_classification[] | select(.classification == "unmatched") | .improvement` (decimal fraction). **Display:** `round(unmatched_mean_pct * 100, 1)` for percentage rendering. |
| `mechanism_verdict` | `workspace/validation_results.json` | `.benchmark.mechanism_check_verdict` |
| `attenuation_ratio` | computed | `matched_pct / sim_improvement` (see guard below). **Note:** `sim_improvement` is `combined_score` — an arbitrary fitness value, not a percentage. This ratio is an approximation using the overall fitness score as a proxy for per-workload sim prediction. It should be interpreted as a rough scaling factor, not a dimensionally precise attenuation measure. A future schema version may add per-workload sim predictions for a more meaningful ratio (see Extension Points). |
| `label` | computed | Attenuation label derived from `attenuation_ratio`: ratio < 0.3 → "significant attenuation"; ratio > 2.0 → "unexpected amplification"; otherwise → "within expected range". Set to "N/A" when `attenuation_ratio` is `null`. |
| `calibration_n` | `docs/transfer/calibration_log.md` | count of `### Transfer:` headings + 1 (this transfer) |

**Missing `workload_classification` guard:** If the `workload_classification` key is absent from `validation_results.json` `.benchmark` (possible if schema validation was skipped or an older `benchmark` version produced the file), `generate-evidence` MUST treat it the same as an empty array: `matched_workload` = "N/A (workload_classification absent)", `matched_pct` = `null`, and emit a warning on stderr: `"WARNING: benchmark.workload_classification missing from validation_results.json — treating as empty."` This prevents an unhandled `KeyError`.

**Zero matched workloads guard:** If `workload_classification` is present but contains no entries with `classification == "matched"`, `matched_workload` is set to "N/A (no matched workloads)" and `matched_pct` is set to `null`. The Prediction accuracy section notes "No matched workloads — attenuation ratio undefined." `generate-evidence` exits 0 (the evidence document is still useful for documenting unmatched results).

**Attenuation ratio guard:** If `sim_improvement` (`combined_score`) is zero, `attenuation_ratio` is set to `null` and labelled "N/A (sim predicted no improvement)". If `sim_improvement` is negative, `attenuation_ratio` is set to `null` and labelled "N/A (sim predicted negative — attenuation ratio undefined)". `generate-evidence` exits 0 in both cases (the evidence document is still useful) but the Prediction accuracy section notes the limitation.

**Attenuation label** (when `sim_improvement > 0`): ratio < 0.3 → "significant attenuation"; ratio > 2.0 → "unexpected amplification"; otherwise → "within expected range".

**Template:**
```markdown
## Evidence: <algorithm_name> sim-to-real transfer

**Date:** <YYYY-MM-DD>
**Verdict:** <overall_verdict>

### Claim
The evolved routing algorithm improves performance on <matched_workload>
in production with improvement above noise floor (T_eff=<t_eff_display>%).

### Evidence Chain

**1. Simulation predicted improvement**
- Algorithm: <algorithm_name> (from <evolve_block_source>)
- Matched workload: <matched_workload>
- Sim fitness score: <sim_improvement>
- Source: workspace/algorithm_summary.json, workspace/validation_results.json

**2. Translation fidelity verified**
- Suite A Kendall-tau: <suite_a_tau> (threshold: 0.8) — <PASS|FAIL>
- Suite A max absolute error: <suite_a_error>
- Suite C concurrent safety: <suite_c_passed>, pile-on ratio: <suite_c_pile_on>
- Interpretation: The production plugin reproduces the simulation
  algorithm's ranking behavior within measured tolerance.

**3. Production result**
- Observed improvement: <matched_pct_display>% on <matched_workload>
- Noise floor (T_eff): <t_eff_display>%

**4. Mechanism specificity**
- Matched workload improvement: <matched_pct_display>%
- Mean unmatched workload improvement: <unmatched_mean_pct_display>%
- Mechanism check: <mechanism_verdict>

**5. Prediction accuracy**
- Sim fitness score: <sim_improvement>, Production observed: <matched_pct_display>%
- Attenuation ratio: <attenuation_ratio> (<label>) (approximate — uses overall fitness score as proxy)
- Running calibration: transfer <calibration_n> of 3 (uncalibrated period)

### Summary
<one-sentence narrative based on overall_verdict>
Attenuation factor <attenuation_ratio> recorded in calibration log.
```

Stage 6 embeds `workspace/transfer_evidence.md` in the PR description as a collapsible `<details>` block.

---

## Inter-Stage Artifact Additions

The Stage 5 → Stage 6 contract gains two entries:

| File | Format | Required before Stage 6 |
|---|---|---|
| `workspace/benchmark_state.json` | JSON | `phases.{noise,baseline,treatment}.status == "done"` |
| `workspace/transfer_evidence.md` | Markdown | Must exist and be non-empty |

---

## Prerequisites

### One-time setup (per cluster)
- Tekton Pipelines installed
- Namespace created with RBAC roles applied (`tekton/roles.yaml`)
- PVCs created: `model-pvc` (300Gi RWX), `data-pvc` (20Gi RWX)
- Secrets: `hf-secret` with `HF_TOKEN`
- `tkn` CLI installed locally

### One-time setup (per inference-sim version)
- inference-sim submodule bumped to include PR #704 (`blis observe` command)
- `blis` container image built and pushed to a registry accessible from the cluster
- Image tag recorded in `values.yaml` under `observe.image`
- `jinja2` and `PyYAML` installed in `.venv` (required by `compile-pipeline` and `preflight`)

### Per-transfer setup (Stage 3 output)
- `workspace/tekton/values.yaml` generated from `blis_router/` artifacts via translation rules
- PipelineRun stubs generated in `workspace/tekton/`

---

## Artifact Producers and Consumers

| Artifact | Producer | Consumer |
|---|---|---|
| `workspace/algorithm_summary.json` | Stage 1 (PR1, complete) | `generate-evidence` |
| `workspace/tekton/values.yaml` | Stage 3 (PR3, modified by this design) | `preflight`, `compile-pipeline` |
| `workspace/tekton/pipelinerun-*.yaml` | Stage 3 (PR3, modified by this design) | Step 5b Submit |
| `workspace/benchmark_state.json` | `benchmark-state` (this design) | Step 5b loop, Stage 6 |
| `workspace/signal_coverage.json` | Stage 2 (PR3, complete — `prompts/translate.md`) | `benchmark` (workload classification) |
| `workspace/<phase>_results.json` | `convert-trace` (this design) | `benchmark` |
| `workspace/benchmark_output.json` | `benchmark` (this design, new `--out` flag) | merged into `validation_results.json` |
| `workspace/validation_results.json` | **PR5 (Stage 5 — Not started)**. Schema exists at `tools/schemas/validation_results.schema.json`. PR5 is responsible for writing `suite_a`, `suite_b`, `suite_c`, `overall_verdict`. The `benchmark` key (including `noise_cv` at the top level — computed by `benchmark --out` from the noise results) is populated by `benchmark --out` and merged in by Step 6. | `generate-evidence` |
| `workspace/transfer_evidence.md` | `generate-evidence` (this design) | Stage 6 |

---

## Invariants

- **INV-1 (Phase ordering):** Noise must complete before baseline or treatment. `benchmark-state` enforces the phase order `noise → baseline → treatment` by checking that the previous phase is `done` before allowing the next phase to be set to `running`.
- **INV-2 (Cluster context):** All three phases must run against the same cluster. The `cluster_context` field in `benchmark_state.json` is set on creation and enforced on every re-entry via `benchmark-state` (exits 1 on mismatch; Step 5a HALTs).
- **INV-3 (Idempotent re-entry):** A `done` phase is never re-run. Stage 5 skips phases with `status == "done"` on re-entry.
- **INV-4 (Extractor pod cleanup):** Extractor pods are deleted via two mechanisms: (1) an explicit `kubectl delete` after successful `kubectl cp` (the primary cleanup path), and (2) a `trap EXIT ERR` handler as a fallback. **Limitation:** Because `trap` is re-registered per-phase inside the loop, only the *last* phase's trap handler is active at script EXIT. If the script fails mid-loop during an earlier phase (before the explicit delete runs), that phase's extractor pod may leak and require manual cleanup (`kubectl delete pod sim2real-extract-<phase>`) before retry.
- **INV-5 (Pre-flight before cluster work):** No PipelineRun is submitted until `preflight` exits 0 for the relevant phase.

---

## Verification Gate

The implementation PR must pass the following checks before merge:

1. **Unit tests:** `python -m pytest tools/ -v` — covers all new subcommands (`preflight`, `benchmark-state`, `convert-trace`, `compile-pipeline`, `render-pipelinerun`, `generate-evidence`) and the rewritten `benchmark`. Subcommands that shell out to cluster tools (`kubectl`, `tkn`, `tektonc.py`) must mock the subprocess boundary (not the cluster itself) to test argument construction, exit-code handling, and output parsing.
2. **Schema validation:** All new schema files (`noise_results.schema.json`, `baseline_results.schema.json`, `treatment_results.schema.json`, `benchmark_output.schema.json`, `benchmark_state.schema.json`) must be loadable by `validate-schema` and tested with at least one valid and one invalid fixture each.
3. **Go test:** `go test ./tools/harness/...` (required for Validation-category PRs per `docs/contributing/pr-workflow.md`).
4. **Go build:** `go build ./tools/harness/...` (unconditional — required for all Validation-category PRs per `docs/contributing/pr-workflow.md`).
5. **Lint:** `ruff check tools/` must pass.
6. **Integration smoke test (manual):** At least one full noise → baseline → treatment cycle on a test cluster, verifying `benchmark_state.json` transitions and `transfer_evidence.md` generation.

---

## Extension Points

- **Additional workloads:** Add entries to `observe.workloads` in `values.yaml` and add corresponding `run-workload-blis-observe` tasks in the pipeline template DAG.
- **Multi-cluster support:** Replace the single `cluster_context` with a per-phase cluster field in `benchmark_state.json`. Each phase could target a different cluster.
- **Per-workload sim predictions:** `algorithm_summary.json` schema can be extended with `per_workload_predictions` to provide per-workload improvement predictions (currently only `metrics.combined_score` is available).
- **S3 result upload:** Add an optional `upload-s3` task after `collect-results` in the pipeline DAG. Currently out of scope.
- **Alternative workload harnesses:** The `run-workload-blis-observe` task can be swapped for a different harness (e.g., `guidellm`) by changing the task reference in the pipeline template.

---

## Validation Strategy

1. **Schema validation:** All workspace JSON artifacts are validated against their schemas in `tools/schemas/` using `transfer_cli.py validate-schema`.
2. **Preflight checks:** `preflight` runs before every phase submission to verify cluster readiness.
3. **Exit-code-driven halts:** Every step that can fail (preflight, compile, submit, poll, extract, convert-trace, benchmark) has explicit exit-code checks with HALT messages.
4. **State file guards:** `benchmark_state.json` prevents re-running completed phases and detects cluster context changes.
5. **Evidence document completeness:** `generate-evidence` exits 1 if any input artifact is missing, preventing partial evidence.

---

## Cross-System Checklist

| System | What this design reads/writes | Interface contract |
|---|---|---|
| inference-sim | `blis observe` command (PR #704) | TraceV2 two-file output (YAML header + CSV data) |
| llm-d-inference-scheduler | Scorer plugin (Stage 4 output) | `go build ./pkg/plugins/scorer/...` must pass |
| tektonc-data-collection (tektonc) | Pipeline templates, `values.yaml` | `tektonc.py` (at `tektonc-data-collection/tektonc/tektonc.py`) compiles templates with values |
| Kubernetes cluster | PipelineRuns, PVCs, Secrets, RBAC | Standard Tekton Pipelines API |
| `transfer_cli.py` | New subcommands (preflight, benchmark-state, convert-trace, compile-pipeline, generate-evidence) | Exit codes documented per subcommand |

---

## Key Design Decisions

| Decision | Rationale | Alternatives Considered | What Breaks If Wrong |
|---|---|---|---|
| Three separate pipelines instead of one | Each phase independently retryable; noise characterization is cluster calibration, not per-algorithm work | Single pipeline with three stages: simpler template but a noise failure forces full re-run including model deploy | If three pipelines add too much template duplication, maintenance cost grows; mitigated by shared task definitions |
| Results on PVC, extracted via extractor pod | Avoids pod GC race (completed task pods subject to `completedTaskRunTTL`); no S3 dependency; fully automatable | (a) S3 upload from task: adds credentials dependency. (b) `kubectl cp` from completed task pod: races with GC | If PVC capacity is insufficient for large TraceV2 outputs, extraction fails silently; mitigated by preflight PVC existence check (note: preflight verifies PVC exists and is RWX but does not check free space — a full PVC surfaces as an opaque Tekton task failure) |
| `benchmark_state.json` per-phase tracking | Cluster failures are the weak link; state persistence enables resuming from the last successful phase | (a) No state file — re-run everything: wastes cluster time. (b) Check PipelineRun status directly: fragile if PipelineRun is GC'd | If state file becomes corrupted, manual deletion + re-run is needed; mitigated by schema validation on read |
| GPU check reads from `values.yaml` | No hardcoded GPU types; works for any hardware configuration | Hardcoded GPU list in preflight: simpler but breaks for new hardware | If `values.yaml` structure changes, preflight breaks; mitigated by schema validation of values.yaml (future) |
| `blis observe` as workload harness | Same workload format as simulation (v1 auto-upgraded to v2); true workload fidelity between sim and real cluster | (a) `guidellm`: different workload format, requires translation layer. (b) Custom harness: maintenance burden | If `blis observe` is unstable or slow, pipeline reliability suffers; mitigated by per-phase retry and timeout |
| `convert-trace` runs locally (not in task) | Keeps `transfer_cli.py` out of container images; format conversion is testable locally | Run conversion inside a Tekton task: avoids local Python dependency but complicates image builds | If TraceV2 files are very large, local conversion is slow; acceptable for expected data volumes |
| Polling timeout (4h) | Prevents infinite loop if PipelineRun hangs; state records timeout as `failed` so phase is retryable | (a) No timeout: risks infinite loop. (b) Shorter timeout (1h): may be insufficient for large models | If 4h is too short for large model deploys, phase fails prematurely; operator can increase and re-run |
| `render-pipelinerun` subcommand | Avoids `envsubst` dependency (not universally available); consistent with rest of `transfer_cli.py` flow | Use `envsubst` directly: simpler but not available on all platforms | If template substitution logic diverges from envsubst behavior, debugging is harder; mitigated by simple key=value semantics |
| `generate-evidence` explicit subcommand | Consistent with other `transfer_cli.py` subcommands; evidence document has defined inputs and is independently testable | Inline evidence generation in the prompt: simpler but not testable independently | If evidence template changes frequently, subcommand needs updating; mitigated by field mapping table in design |
| T_eff minimum floor of 0.05 (5%) | Prevents trivially passable mechanism checks on very-low-noise clusters; carried over from existing `noise-characterize` implementation (`max(0.05, 2.0 * max_cv)`) | No floor: T_eff = 2 * noise_cv directly — any treatment change above 2× noise would pass | On a cluster with noise_cv = 0.01, T_eff = 0.02 makes the check trivially passable; the 5% floor ensures a minimum meaningful threshold |
| Translation rules as separate document | `llm_config.yaml` → `values.yaml` mapping has quirks; independent iteration avoids coupling unrelated concerns | Inline rules in this design: single source of truth but couples iteration cycles | If translation rules and this design diverge, values.yaml may not match pipeline expectations; mitigated by preflight validation |
