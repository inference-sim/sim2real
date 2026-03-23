---
stage: 3
version: "1.0"
pipeline_commit: "set-at-runtime"
description: "Stage 3 — Generate production scorer plugin from evolved algorithm"
---

# Stage 3: Generate

Generate a production `scheduling.Scorer` plugin for llm-d-inference-scheduler
from the evolved algorithm. This is the most complex stage — it reads the scorer
template, algorithm summary, signal coverage, and mapping artifact to produce
Go source files implementing the evolved scoring logic.

## Prerequisites

Verify all required input artifacts exist and are valid. **HALT if any check fails.**

```bash
# algorithm_summary.json: exists + schema valid + scope passed
test -f workspace/algorithm_summary.json || { echo "HALT: missing algorithm_summary.json"; exit 1; }
.venv/bin/python tools/transfer_cli.py validate-schema workspace/algorithm_summary.json || { echo "HALT: algorithm_summary.json schema validation failed"; exit 1; }
.venv/bin/python -c "import json,sys; d=json.load(open('workspace/algorithm_summary.json')); sys.exit(0 if d.get('scope_validation_passed') is True else 1)" || { echo "HALT: algorithm_summary.json scope_validation_passed is not true"; exit 1; }

# signal_coverage.json: exists + schema valid + coverage complete
test -f workspace/signal_coverage.json || { echo "HALT: missing signal_coverage.json"; exit 1; }
.venv/bin/python tools/transfer_cli.py validate-schema workspace/signal_coverage.json || { echo "HALT: signal_coverage.json schema validation failed"; exit 1; }
.venv/bin/python -c "import json,sys; d=json.load(open('workspace/signal_coverage.json')); sys.exit(0 if d.get('coverage_complete') is True and len(d.get('unmapped_signals',[])) == 0 else 1)" || { echo "HALT: signal_coverage.json coverage_complete is not true or has unmapped signals"; exit 1; }

# Submodule staleness check: signal_coverage.json commit_hash must match HEAD
.venv/bin/python -c "
import json, subprocess, sys
d = json.load(open('workspace/signal_coverage.json'))
head = subprocess.check_output(['git', '-C', 'llm-d-inference-scheduler', 'rev-parse', 'HEAD']).decode().strip()
match = head.startswith(d['commit_hash']) or d['commit_hash'].startswith(head)
sys.exit(0 if match else 1)
" || { echo "HALT: signal_coverage.json commit_hash does not match llm-d-inference-scheduler HEAD"; exit 1; }

# Scorer template and mapping artifact
test -f docs/transfer/scorer_template.go.md || { echo "HALT: missing scorer template"; exit 1; }
test -f docs/transfer/blis_to_llmd_mapping.md || { echo "HALT: missing mapping artifact"; exit 1; }
```

On HALT, write `workspace/escalation.json` with the appropriate `halt_reason`:
- Missing signal_coverage.json → `"missing_signal_coverage"`
- Commit hash mismatch → `"stale_signal_coverage"`

## Stale Artifact Guard

Delete any existing output artifacts to prevent stale files from a prior run.

```bash
rm -f workspace/stage3_output.json
# Also remove any previously generated scorer files (read algorithm_name from summary)
ALGO_NAME=$(.venv/bin/python -c "import json; print(json.load(open('workspace/algorithm_summary.json'))['algorithm_name'])" 2>/dev/null || true)
if [ -n "$ALGO_NAME" ]; then
    SANITIZED=$(echo "$ALGO_NAME" | tr ' .-' '_' | tr -s '_' | tr '[:upper:]' '[:lower:]' | sed 's/^_//;s/_$//')
    rm -f "llm-d-inference-scheduler/pkg/plugins/scorer/${SANITIZED}.go"
    rm -f "llm-d-inference-scheduler/pkg/plugins/scorer/${SANITIZED}_test.go"
fi
```

## Step 1: EVOLVE-BLOCK Content Hash Verification

Re-verify the EVOLVE-BLOCK content hash independently (do not rely on Stage 1 verification — source may have changed between stages).

1. Read `evolve_block_content_hash` and `evolve_block_source` from `workspace/algorithm_summary.json`.
2. Read the source file at the specified path and line range.
3. Extract the EVOLVE-BLOCK lines, join with `\n` (no trailing newline).
4. Compute SHA-256 and compare to the stored hash.

**HALT on mismatch** with `halt_reason: "evolve_block_hash_mismatch_stage3"`.

## Step 2: UNVERIFIED Field Resolution

Read `docs/transfer/scorer_template.go.md` and identify fields marked `UNVERIFIED`.
Check the scorer template header for a HALT CONDITION specifying the threshold.

- If fewer than the threshold number of UNVERIFIED fields can be resolved, **HALT** with `halt_reason: "unverified_field_threshold_not_met"`.
- If no explicit threshold is specified, treat ANY unresolved UNVERIFIED field as a halt condition.

For each UNVERIFIED field:
1. Check if the field exists in the `sigs.k8s.io/gateway-api-inference-extension` Metrics type.
2. If found, mark as resolved. If not found, check for alternative field names.
3. Document resolution in the generated code comments.

## Step 3: Code Generation

Parse the EVOLVE-BLOCK and generate production scorer code:

### 3a: Parse Scoring Logic

Read the EVOLVE-BLOCK from `blis_router/best/best_program.go` and identify:
- Scoring weights applied to each signal
- Penalty functions (e.g., cubic load penalty, KV pressure penalty)
- Decision thresholds (e.g., score cutoffs, load thresholds)
- Composite signal computations (e.g., EffectiveLoad formula)
- Conditional branching logic (e.g., SessionID affinity check)

### 3b: Map Signals to Production Fields

For each signal, use `workspace/signal_coverage.json` to get:
- `prod_access_path` — the Go code to access this signal from the endpoint
- `normalization` — any required normalization (e.g., `divide_prod_by_100`)

### 3c: Apply Normalization

For each signal with a `normalization` field:
- `divide_prod_by_100`: divide production value by 100 (e.g., `KVUtilization`)
- `verify_and_normalize`: check scale and normalize if needed (e.g., `CacheHitRate`)
- `boolean_presence_check`: convert to boolean (e.g., `SessionID != ""`)

If a signal has no `normalization` field:
1. Check `algorithm_summary.json` `normalization_note` as a fallback source.
2. If neither source provides normalization, treat as identity (no scaling) and emit a `// WARNING: no normalization specified for <signal_name>` comment.

### 3d: Generate Scorer File

Use `docs/transfer/scorer_template.go.md` as the structural reference:
- Section 1: Package declaration and imports
- Section 2: Type definition and compile-time assertions
- Section 3: Factory function (follow existing scorer patterns)
- Section 4: `TypedName()` and `Category()` methods
- Section 5: `Score()` method with production signal access, normalization, and evolved scoring logic
- Section 6: Test patterns
- Section 7: Registration

Output file: `llm-d-inference-scheduler/pkg/plugins/scorer/<name>.go`
where `<name>` = `algorithm_name` from `algorithm_summary.json`, sanitized to snake_case.

**Snake_case rules:** replace spaces/hyphens/dots with underscores, collapse consecutive underscores, strip leading/trailing underscores, lowercase, prepend `scorer_` if starts with digit, strip non-ASCII.

### 3e: Generate Test File

Generate `<name>_test.go` following Section 6 patterns from the scorer template.

### 3f: Add Registration

Add the scorer factory registration to `llm-d-inference-scheduler/pkg/plugins/register.go`.

## Step 4: CacheHitRate Handling

**Skip this step entirely if `CacheHitRate` is not present in `workspace/signal_coverage.json` `signals[]` at all.** The current blis_router algorithm does not use CacheHitRate; generating dead `cacheHitRate` code when the signal is absent would add unreachable code and could mask missing-signal detection bugs. Always read the artifact to determine this — do not assume based on prior runs.

Check `workspace/signal_coverage.json` for the CacheHitRate signal:

- **If absent (not in `signals[]` at all):** skip this step entirely — do not emit any CacheHitRate code.
- **If mapped** (has a `prod_access_path`): use that path in generated code.
- **If unmapped or `prod_access_path: "UNVERIFIED"`**: emit `// CacheHitRate: production access path unavailable — using zero fallback` and assign `cacheHitRate = 0.0`.
- **HALT if CacheHitRate is used as a multiplier** in the EVOLVE-BLOCK scoring logic (a zero fallback would zero out the entire score). Use `halt_reason: "cache_hit_rate_unavailable_stage3"`.

## Step 5: F-10 Double-Counting Guard

**Skip this step entirely if `composite_signals` in `workspace/algorithm_summary.json` is empty.** An empty `composite_signals: []` means the EVOLVE-BLOCK contains no composite method calls (e.g., no `EffectiveLoad()`), so there is no composite to check for double-counting. Always read the artifact to determine this — do not assume based on prior runs.

Check if any two signals in the EffectiveLoad composite share the same `prod_access_path`:

- **If double-counting detected**: use `RunningRequestsSize` once with an adjusted coefficient (both composite inputs map to this single production field — keep the combined weight but emit the field access only once).
- **If neither alternative available**: emit a `// WARNING: F-10 double-counting risk` comment and use the single-count approach.
- **HALT only if** double-counting would affect >50% of the scoring weight.

## Step 6: Stage 3 Output Validation

Before writing `workspace/stage3_output.json`, verify:

1. **No PLACEHOLDER markers** remain in the generated scorer code.
2. **Structural invariants** from scorer_template.go.md Section 7:
   - Import paths unchanged
   - Type assertion present (`var _ scheduling.Scorer = &...{}`)
   - Factory function registered
   - UNVERIFIED fields remain commented-out if unresolved
3. **Do NOT compile** (`go build`) — compilation is deferred to Stage 4.

## Step 7: Write stage3_output.json

Construct and validate the output artifact:

```bash
.venv/bin/python tools/transfer_cli.py validate-schema workspace/stage3_output.json
```

**HALT if validation fails** with `halt_reason: "post_write_validation_failure_stage3"`.

## Step 8: Generate tekton artifacts

After writing `workspace/stage3_output.json`, generate the Tekton benchmarking artifacts for Stage 5.

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

- `stack.model.helmValues` — must match the `llm-d-modelservice` Helm chart schema.
  Use `tektonc-data-collection/tektoncsample/blis-inference-perf/values.yaml`
  (`stack.model.helmValues`) as the canonical reference structure. Do NOT use flat
  top-level keys (`modelName`, `hfRepo`, `image`, `tensorParallelSize`,
  `gpuMemoryUtilization`, `replicaCount`, `accelerator`, etc.) — the chart's
  `additionalProperties: false` schema rejects them at deploy time.

  Key translation rules from `blis_router/llm_config.yaml`:
  - `modelName` (top-level key, required by templates via `{{ stack.model.helmValues.modelName }}`):
    model HF repo ID (e.g. `Qwen/Qwen2.5-7B-Instruct`)
  - `modelArtifacts.name`: same model ID
  - `modelArtifacts.uri`: `pvc://model-pvc/models/<sanitized-model-name>`
  - `modelArtifacts.authSecretName`: `"hf-secret"`
  - `decode.replicas`: `cluster.num_instances`
  - `decode.parallelism.tensor`: `serving.tensor_parallel_size`
  - `decode.containers[0].image`: `vllm/vllm-openai:<serving.vllm_version>`
  - `decode.containers[0].extraConfig.vllm.gpuMemoryUtilization`: `serving.gpu_memory_utilization`
  - `decode.containers[0].extraConfig.vllm.maxNumSeqs`: `vllm_config.max_num_running_reqs`
  - `decode.containers[0].extraConfig.vllm.maxNumBatchedTokens`: `vllm_config.max_num_scheduled_tokens`
  - `decode.acceleratorTypes.labelKey`: `"nvidia.com/gpu.product"`
  - `decode.acceleratorTypes.labelValues`: map `hardware.gpu_type` to k8s label
    (e.g. `H100-80GB` → `["NVIDIA-H100-80GB-HBM3"]`)
  - `prefill.create: false`
  - `routing.servicePort: 8000`

- `stack.gateway.helmValues.gateway` — helm values for the `llm-d-infra/llm-d-infra` gateway chart.
  Use the llm-d standard istio configuration as defaults:
  ```yaml
  gateway:
    helmValues:
      gateway:
        # Source: https://github.com/llm-d/llm-d/blob/main/guides/prereq/gateway-provider/common-configurations/istio.yaml
        provider: istio
        gatewayClassName: istio
        gatewayParameters:
          resources:
            limits:
              cpu: "16"
              memory: 16Gi
            requests:
              cpu: "4"
              memory: 4Gi
          istio:
            accessLogging: false  # default false for production
        # kgateway alternative (deprecated — migration only):
        # provider: kgateway
        # gatewayClassName: agentgateway
  ```
  `gatewayClassName` determines the service name suffix in the `deploy-gateway` task:
  `istio` → service name `{release}-inference-gateway-istio`;
  `agentgateway`/`kgateway` → `{release}-inference-gateway`.

- `stack.gaie.baseline.helmValues` and `stack.gaie.treatment.helmValues` — complete helm
  values for `oci://registry.k8s.io/gateway-api-inference-extension/charts/inferencepool`.
  These are passed directly to the `deploy-gaie` task via `{{ stack.gaie.<phase>.helmValues | tojson }}`.
  The scorer `EndpointPickerConfig` goes under `inferenceExtension.pluginsCustomConfig`
  (a filename-keyed map). Common connection pool defaults from the llm-d standard config
  are duplicated in each phase because the scorer config differs per phase.
  ```yaml
  gaie:
    baseline:
      helmValues:
        inferenceExtension:
          pluginsConfigFile: "custom-plugins.yaml"
          pluginsCustomConfig:
            custom-plugins.yaml: |
              apiVersion: inference.networking.x-k8s.io/v1alpha1
              kind: EndpointPickerConfig
              plugins:
              - type: load-aware-scorer
              <...full baseline EndpointPickerConfig from docs/transfer/blis_to_llmd_mapping.md...>
          flags:
            - name: v
              value: 1
        provider:
          name: istio  # match stack.gateway.helmValues.gateway.provider
        istio:
          # Source: https://github.com/llm-d/llm-d/blob/main/guides/prereq/gateway-provider/common-configurations/istio.yaml
          destinationRule:
            trafficPolicy:
              connectionPool:
                http:
                  http1MaxPendingRequests: 256000
                  maxRequestsPerConnection: 256000
                  http2MaxRequests: 256000
                  idleTimeout: "900s"
                tcp:
                  maxConnections: 256000
                  maxConnectionDuration: "1800s"
                  connectTimeout: "900s"
    treatment:
      helmValues:
        inferenceExtension:
          pluginsConfigFile: "custom-plugins.yaml"
          pluginsCustomConfig:
            custom-plugins.yaml: |
              apiVersion: inference.networking.x-k8s.io/v1alpha1
              kind: EndpointPickerConfig
              plugins:
              - type: <generated-scorer-type>
              <...full treatment EndpointPickerConfig from Stage 3 scorer generation...>
          flags:
            - name: v
              value: 1
        provider:
          name: istio
        istio:
          destinationRule:
            trafficPolicy:
              connectionPool:
                http:
                  http1MaxPendingRequests: 256000
                  maxRequestsPerConnection: 256000
                  http2MaxRequests: 256000
                  idleTimeout: "900s"
                tcp:
                  maxConnections: 256000
                  maxConnectionDuration: "1800s"
                  connectTimeout: "900s"
  ```
  **Encoding note:** `pluginsCustomConfig` values are YAML block scalars (`|`) — no `\n`
  escaping required. The pipeline templates use `{{ stack.gaie.<phase>.helmValues | tojson }}`
  which serializes the full nested structure to JSON, correctly preserving multiline values.

- `observe.image` — `"ghcr.io/inference-sim/blis:$BLIS_IMAGE_TAG"` (resolved above)

- `observe.workloads` — one entry per `blis_router/workloads/workload_*.yaml` file.
  Each entry **must** use the field name `spec:` (not `content:` or any other name) —
  the pipeline templates access `workload.spec` directly:
  ```yaml
  workloads:
    - name: <workload-name>
      source: blis_router/workloads/workload_<name>.yaml
      spec: |
        <full file contents verbatim>
  ```

- `observe.noise_runs: 5`

**Validate generated values.yaml required keys:**
```bash
.venv/bin/python -c "
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
# gateway defaults
gw = v['stack'].get('gateway', {}).get('helmValues', {}).get('gateway', {})
assert gw.get('provider'), 'missing stack.gateway.helmValues.gateway.provider'
assert gw.get('gatewayClassName'), 'missing stack.gateway.helmValues.gateway.gatewayClassName'
# gaie helmValues with pluginsCustomConfig
for phase in ('baseline', 'treatment'):
    hv = v['stack'].get('gaie', {}).get(phase, {}).get('helmValues', {})
    assert hv, f'missing stack.gaie.{phase}.helmValues'
    pcc = hv.get('inferenceExtension', {}).get('pluginsCustomConfig', {})
    assert pcc, f'missing stack.gaie.{phase}.helmValues.inferenceExtension.pluginsCustomConfig'
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
  taskRunTemplate:
    serviceAccountName: helm-installer   # required: deploy-model/delete-model tasks need Helm RBAC
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

Note: `stage3_output.schema.json` defines `tekton_artifacts` with `additionalProperties: false` and only allows `values_yaml` (string) and `pipeline_stubs` (array). Individual keys per phase are not permitted — use `pipeline_stubs` array. Stage 5 reads the stub paths from this array in phase order (noise, baseline, treatment). Within `tekton_artifacts`, `values_yaml` is required; `pipeline_stubs` is optional per the schema, though Stage 5 expects it to be present.

**Validate stage3_output.json after updating:**
```bash
.venv/bin/python tools/transfer_cli.py validate-schema workspace/stage3_output.json \
  || { echo "HALT: stage3_output.json failed schema validation after tekton_artifacts update"; exit 1; }
```

## Halt Conditions

| Condition | halt_reason | Action |
|-----------|-------------|--------|
| Missing signal_coverage.json | `missing_signal_coverage` | Write escalation.json, HALT |
| Stale signal_coverage commit | `stale_signal_coverage` | Write escalation.json, HALT |
| EVOLVE-BLOCK hash mismatch | `evolve_block_hash_mismatch_stage3` | Write escalation.json, HALT |
| UNVERIFIED field threshold | `unverified_field_threshold_not_met` | Write escalation.json, HALT |
| CacheHitRate unavailable (multiplier) | `cache_hit_rate_unavailable_stage3` | Write escalation.json, HALT |
| Pre-write validation failure | `pre_write_validation_failure_stage3` | Write escalation.json, HALT |
| Post-write validation failure | `post_write_validation_failure_stage3` | Write escalation.json, HALT |
| inference-sim tag unresolved | `inference_sim_tag_unresolved_stage3` | HALT (Stage 8): write `workspace/escalation.json` with `"stage": 3` and this halt_reason |
| tekton_artifacts schema validation | `tekton_artifacts_validation_failure_stage3` | HALT (Stage 8): write `workspace/escalation.json` with `"stage": 3` and this halt_reason |

On any halt, write `workspace/escalation.json` per the escalation schema with `"stage": 3` and the appropriate `halt_reason`.

## Expected Outputs

- `llm-d-inference-scheduler/pkg/plugins/scorer/<name>.go` — generated scorer plugin
- `llm-d-inference-scheduler/pkg/plugins/scorer/<name>_test.go` — generated tests
- `llm-d-inference-scheduler/pkg/plugins/register.go` — modified with new registration
- `workspace/stage3_output.json` — stage output artifact with:
  - `scorer_file`: path to generated scorer
  - `test_file`: path to generated test file
  - `register_file`: path to register.go
  - `scorer_type`: the TypedName type string
  - `tekton_artifacts`: `values_yaml` path and `pipeline_stubs` array
- `workspace/tekton/values.yaml` — Tekton pipeline values
- `workspace/tekton/pipelinerun-{noise,baseline,treatment}.yaml` — PipelineRun stubs
