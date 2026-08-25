---
name: sim2real-bootstrap
description: |
  Bootstrap an experiment repo into a sim2real transfer bundle. Two modes:
  (1) BLIS mode (default) — for BLIS-generated folders with algorithms/,
      workloads/, and config; derives transfer.yaml, baselines/, and a
      component submodule with user approval at each gate.
  (2) --byo mode — for operators arriving with pre-built EPP images plus a
      full baseline scenario and per-algorithm overlays; scaffolds
      transfer.yaml with `byo: true` markers and emits a copy-pasteable
      batched `sim2real translation register` command.
argument-hint: "[experiment-folder-path] [--byo ...]"
user-invocable: true
---

# sim2real-bootstrap

Turn an experiment folder into a pipeline-ready sim2real bundle. This skill has two modes:

- **BLIS mode (default)** — the operator has a BLIS-generated experiment folder (with `algorithms/*.go`, `workloads/*.yaml`, and either `config.md` or `top3_selection.json`). Bootstrap parses those inputs, derives a component submodule, generates baseline scenarios, and emits `transfer.yaml`. Documented in Tasks 0-6 below.
- **`--byo` mode** — the operator has a pre-built EPP image (or several), a full baseline scenario YAML, and per-algorithm overlay YAMLs, but no BLIS artifacts. Bootstrap copies those files into canonical experiment-repo locations, emits `transfer.yaml` with per-algorithm `byo: true` markers, and prints a batched `sim2real translation register` command. Documented in the BYO section below.

## Dispatch

If any of `--byo`, `--baseline`, `--algorithm`, `--algorithm-image`, `--algorithm-config`, `--scenario`, `--force`, or `--non-interactive` appears in `$ARGUMENTS`, dispatch to the BYO branch (see the `--byo` mode section). Otherwise dispatch to BLIS mode (Tasks 0-6).

## Arguments (BLIS mode)

`$ARGUMENTS` is the path to the experiment folder. Defaults to the current directory if omitted.

```bash
EXPERIMENT_ROOT=$(realpath "${ARGUMENTS:-.}")
SIM2REAL=$(realpath "$(dirname "$0")/../..")  # or detect from context
```

If no argument given, use the current working directory as the experiment folder.

## Prerequisites

Verify before starting:
```bash
test -d "$EXPERIMENT_ROOT/algorithms" || { echo "ERROR: no algorithms/ dir"; exit 1; }
test -d "$EXPERIMENT_ROOT/workloads"  || { echo "ERROR: no workloads/ dir"; exit 1; }
```

The experiment folder MUST contain:
- `algorithms/*.go` — simulation-evolved algorithm source
- `workloads/*.yaml` — benchmark workload definitions
- Configuration source (one of: `config.md`, `config.yaml`, structured JSON)

## Task Execution

Use TaskCreate to track progress. Execute tasks sequentially; each
derive-and-approve task MUST pause and ask the operator to confirm the
derived values before proceeding. Present the derived values, then ask
a plain-text numbered question — e.g. *"Proceed with these values? (1)
yes  (2) revise <field>  (3) abort"* — and wait for a reply. Do NOT use
`AskUserQuestion`.

---

### Task 0: Ensure .gitignore

**action:** create-if-missing

Check if `.gitignore` exists in experiment root. If missing, create:

```
# Temporary
tmp/

# Python
.venv/
__pycache__/
*.pyc
*.pyo
*.egg-info/
dist/
build/

# macOS
.DS_Store
._*

# temporary folder if one
tmp/
```

---

### Task 0b: Scaffold pre-commit secret scan

**action:** shell

Experiment repos track their `workspace/` tree (the `.gitignore` from Task 0
excludes only state files), so collected artifacts — and anything hand-copied
beside them — become committed, published surface. The collect-time redactor
(`pipeline/lib/redact.py`, issue #819) is a single point of failure: anything
it misses lands in git history the moment the operator commits. Scaffold an
independent second layer — a blocking, whole-repo `detect-secrets` pre-commit
hook (issue #822). Create-if-missing, so re-running bootstrap never clobbers
an operator-edited config or an audited baseline.

```bash
python "$SKILL_DIR/scaffold_precommit.py" --experiment-root "$EXPERIMENT_ROOT"
```

This writes `.pre-commit-config.yaml` and `.secrets.baseline` (empty results —
nothing pre-whitelisted) into the experiment root. It only installs the files;
tell the operator to activate the hook (see **After Bootstrap**). The `--byo`
flow runs this same step automatically.

---

### Task 1: Derive target component repository

**action:** derive-and-approve

Scan the experiment folder for references to the target component repo.

**Scan sources:**
- Go import paths in `algorithms/*.go` (pattern: `github.com/<org>/<repo>/...`)
- Code blocks in `README.md` with import paths
- Commit references in `config.md` tables (column: "Component" or "llm-d")
- Any existing `.gitmodules`

**Extract per candidate:**
- `repo_url`: full GitHub URL
- `repo_name`: last path segment (e.g., `llm-d-router`)
- `ref_from_folder`: pinned commit if found in docs
- `evidence`: list of file:line references

**Determine ref to pin:**

Prefer latest stable release tag. Fall back to main/commit if required interfaces
don't exist in any release:

```bash
cd /tmp && git clone --bare "$COMPONENT_URL" component-check.git && cd component-check.git
LATEST_RELEASE=$(git tag --sort=-version:refname | grep -v rc | head -1)
# Check if interfaces used by the algorithm exist at that tag
# If yes: COMPONENT_REF=$LATEST_RELEASE
# If no: use commit from folder docs or latest main
```

**Present to user with a plain-text numbered prompt** (do NOT use `AskUserQuestion`):
```
Derived target component:
  repo: <url>
  evidence: <file:line list>
  ref selection:
    - latest release: <tag> (interfaces present? yes/no)
    - folder-pinned commit: <ref> (interfaces present? yes/no)
    - recommended: <chosen ref> (<reason>)

Pick a ref:
  (1) latest release (<tag>)
  (2) folder-pinned commit (<ref>)
  (3) revise (specify a different ref)
  (4) abort
```
Wait for the operator's reply before proceeding.

If >1 candidate found, halt and report — bootstrap supports only one target.

**Output variables for subsequent tasks:**
```bash
COMPONENT_URL="https://github.com/<org>/<repo>"
COMPONENT_NAME="<repo>"
COMPONENT_REF="<chosen ref>"
```

---

### Task 2: Add component submodule

**action:** shell  
**depends:** task-1

```bash
cd "$EXPERIMENT_ROOT"
git submodule add "$COMPONENT_URL" "$COMPONENT_NAME"
cd "$COMPONENT_NAME"
git fetch --tags
git checkout "$COMPONENT_REF"
cd ..
git add .gitmodules "$COMPONENT_NAME"
```

Verify:
```bash
test -d "$COMPONENT_NAME/.git" && (cd "$COMPONENT_NAME" && git log --oneline -1)
```

---

### Task 3: Generate baseline scenario(s)

**action:** derive-and-approve

Generate llm-d-benchmark scenario YAML(s) for baseline arm(s).

**Bundled scripts** (in this skill directory — invoke in place, do NOT copy):
- `generate_from_config.py` — parses `config.md` markdown tables into scenario
  YAMLs with provenance comments. Preferred path for most experiments.
- `generate_scenarios.py` — converts JSON config (`top3_selection.json`) into
  scenario YAMLs. Use only when a JSON input file exists.
- `generate_scenarios.README.md` — documents field mappings, lookup tables,
  omission rules, and coverage gaps for the JSON-input path.
- `pd_plumbing.py` — the P/D and multi-GPU pod fragments (init container,
  `shared-config` / `dshm` volumes, `preprocessScript`, NIXL/NCCL/NVSHMEM env
  vars, `routing.connector`) plus the two gates that decide when they apply.
  Imported by BOTH generators so the emitted text cannot drift between paths.

**Process:**

1. Determine input format:
   - If `config.md` exists (most experiments): use `generate_from_config.py`
   - If `scenario_input.json` or `top3_selection.json` exists: use `generate_scenarios.py`

2. If using `generate_from_config.py` (preferred):
   ```bash
   SKILL_DIR="<this skill's directory>"
   python3 "$SKILL_DIR/generate_from_config.py" "$EXPERIMENT_ROOT/config.md" \
       -o "$EXPERIMENT_ROOT/baselines/"
   ```
   The output file is always `<experiment-root>/baselines/baseline.yaml`
   (issue #544 — the baseline identifier is hardcoded to the literal
   string `baseline` regardless of project). The inner `scenario: - name:`
   label inside the emitted YAML is still derived from the folder name
   (or `-n` override) and used as the benchmark-harness scenario label.

   The script will:
   - Parse the vLLM configuration table from config.md
   - Apply MODEL_METADATA and HARDWARE_LABELS lookups
   - Emit a single bare prefix-caching flag matching the user's intent: `--enable-prefix-caching` if explicitly enabled, `--no-enable-prefix-caching` if explicitly disabled, `--enable-prefix-caching` (with a `sim2real-bootstrap default` provenance source) if unspecified in config.md. The deployed vLLM version predates per-model default resolution, so silent → OFF rather than ON; defaulting to ON keeps caching enabled for our scenarios (see issue #295). Input accepts either the legacy keyed form (`enable_prefix_caching | true|false`) or a bare flag row label (`--enable-prefix-caching` / `--no-enable-prefix-caching` with an empty value column); contradictory specifications error.
   - Write YAML with inline provenance comments showing each value's source
   - Error on unknown models/hardware (update lookup tables in the script if needed)

3. If using `generate_scenarios.py` (JSON input):
   ```bash
   python3 "$SKILL_DIR/generate_scenarios.py" "$INPUT_FILE" "$EXPERIMENT_ROOT/baselines/"
   ```
   Review any "unknown fields" warnings from the script's output.

   `generate_scenarios.py` writes one file per candidate (e.g.
   `top3-1.yaml`, `top3-2.yaml`, `top3-3.yaml`). After the operator picks
   the winning candidate, rename it to `baseline.yaml` — the transfer.yaml
   emitted in task-5 always references `baselines/baseline.yaml` (issue
   #544):
   ```bash
   mv "$EXPERIMENT_ROOT/baselines/<chosen-candidate>.yaml" \
      "$EXPERIMENT_ROOT/baselines/baseline.yaml"
   ```

4. If neither input exists, flag to user — cannot generate baseline without config.

**Output schema per baseline:**
```yaml
scenario:
- name: <lowercase-alphanumeric, no hyphens, 1-20 chars>

  model:
    name: <model HuggingFace ID>
    shortName: <derived>
    path: <derived>
    huggingfaceId: <same as name>
    size: <storage estimate>
    maxModelLen: <from lookup or config>
    blockSize: <from vllm_args>
    gpuMemoryUtilization: <from vllm_args>

  decode:
    replicas: <from config>
    acceleratorType:
      labelKey: nvidia.com/gpu.product
      labelValue: <from hardware lookup>
    vllm:
      additionalFlags:
      - "--flag=value"

  # Emitted ONLY when config.md names a prefill pod count (issues #824, #830).
  # Without those rows the output is exactly the single-decode shape above.
  prefill:
    enabled: true                      # required — see note below
    replicas: <from config>
    acceleratorType:
      labelKey: nvidia.com/gpu.product
      labelValue: <prefill GPU, else the shared GPU>
    vllm:
      additionalFlags:
      - "--flag=value"                 # shared with decode, not per-role

  # Emitted only under the gates tagged per key below (issues #830, #848):
  # kvTransfer and preprocessScript need a prefill pool; volumes/volumeMounts are
  # written by BOTH gates and appear whenever either fires. See the note below.
  vllmCommon:
    kvTransfer:                        # prefill pool   (Gate 1)
      enabled: true
      connector: NixlConnector
      role: kv_both
    # Sources the env file the preprocess init container writes (#848).
    preprocessScript: |                # prefill pool   (Gate 1)
      export LD_LIBRARY_PATH=...libcuda.so.1 discovery...
      . /shared-config/llmdbench_env.sh
    volumes:
    - name: shared-config              # prefill pool   (Gate 1)
      type: emptyDir
      emptyDir: {}
    - name: dshm                       # >1 GPU per pod (Gate 2)
      type: emptyDir
      emptyDir:
        medium: Memory
        sizeLimit: 16Gi
    volumeMounts:
    - name: shared-config
      mountPath: /shared-config
    - name: dshm
      mountPath: /dev/shm

  # Per-role, on BOTH decode: and prefill: — each role is a separate pod, and
  # upstream has no vllmCommon form for either key.
  decode:                              # ...and the same under prefill:
    initContainers:                    # Gate 1
    - name: preprocess
      imageKey: benchmark
      command: ["set_llmdbench_environment.py", "-e", "/shared-config/llmdbench_env.sh", "-i"]
      volumeMounts:                    # explicit — init containers do not
      - name: shared-config            # inherit vllmCommon.volumeMounts
        mountPath: /shared-config
    extraEnvVars:
    - name: NIXL_LOG_LEVEL             # Gate 1
      value: debug
    - name: NCCL_DEBUG                 # Gate 2
      value: "INFO"
    - name: NVSHMEM_DEBUG              # Gate 2
      value: "INFO"

  routing:                             # Gate 1
    connector: nixlv2
```

**Disaggregation (issue #824).** To get a `prefill:` block, `config.md`'s vLLM
table needs a prefill pod-count row — `Number of prefill pods`, `Number of
prefill instances`, or `prefill replicas`. `Prefill GPU` and `Decode GPU` are
optional per-role accelerator overrides; without them both roles use the shared
`GPU` row.

- `enabled: true` is **required**, not decorative. `pipeline/lib/capacity.py`
  defaults prefill to disabled with 0 replicas, so a `prefill:` block lacking it
  reads as disaggregated while planning zero prefill GPUs.
- **`vllmCommon.kvTransfer` is equally required (issue #830)** — same failure
  shape, one layer down. `vllmCommon.kvTransfer.enabled` defaults to `false`
  upstream and vLLM's `--kv-transfer-config` flag is gated on it, so a prefill
  pool without it gets no KV connector: the prefill pod is never routed to and
  logs zero requests, while the decode pods prefill their own requests. Nothing
  errors — the run completes and the results are silently not P/D. Both
  generators emit it whenever they emit a prefill pool.
  `role: kv_both` is deprecated for NixlConnector, which wants `kv_producer` on
  prefill and `kv_consumer` on decode; `vllmCommon` is shared by both roles so
  per-role values are not expressible today (tracked as #845).
- **The plumbing that makes `kvTransfer` work is emitted on the same gate (issue
  #848).** `kvTransfer.enabled: true` on its own is *worse* than leaving it off:
  the worker dies during "Initializing NIXL wrapper" because nothing configured
  the network and GPU-routing state. Two gates, both additive, both derived from
  rows `config.md` already declares:
  - **Gate 1 — a prefill pool exists** (the same gate as `kvTransfer`): the
    `preprocess` init container on *both* roles, the `shared-config` emptyDir,
    `vllmCommon.preprocessScript`, `NIXL_LOG_LEVEL`, and `routing.connector`.
    The first three are one unit — the init container *computes* the values and
    writes them to `/shared-config/llmdbench_env.sh`, `preprocessScript`
    *sources* them, and the volume is the handoff. Any one missing makes the
    other two inert, which is why they are emitted together rather than split
    across the fragment layer where a `defaults.disable` entry could break the
    pair. `routing.connector` is the sidecar half of the connector decision
    `kvTransfer.connector` makes engine-side; both spellings come from
    `pd_plumbing.KV_CONNECTOR_*` so they cannot drift.
  - **Gate 2 — more than one GPU per pod** (`tensor × dataLocal > 1`, which is
    upstream's own accelerator-count arithmetic; `dataLocal` and not `data`,
    because `data` counts ranks across the whole deployment while only same-pod
    ranks share memory. Both are `data_parallel_size` today, since that is the
    only input either comes from): the `dshm` tmpfs at `/dev/shm` and `NCCL_DEBUG` /
    `NVSHMEM_DEBUG`. The K8s default 64 MB `/dev/shm` is a latency-jitter and
    hang risk for multi-GPU collectives. `NVSHMEM_DEBUG` is load-bearing rather
    than diagnostic: `set_llmdbench_environment.py` adds `NVSHMEM_HCA_LIST` to
    the generated env file only when it is not `"none"`.

  Neither gate fires for a single-GPU aggregated bundle, so existing bundles
  regenerate byte-identically. Both generators emit the fragments from
  `pd_plumbing.py`, so the two paths cannot drift.

  Cluster-specific pieces are deliberately *not* emitted and stay in the
  fragment layer (tracked on #840): pod capabilities, which need an SCC grant
  first; the NIC exclusion list, which names literal devices; and the RDMA
  device reservation.
- `parallelism` and `vllm.additionalFlags` are shared across roles — `config.md`
  has no per-role form for them.
- **One GPU type per role.** A GPU cell naming several types (`H100, A100`) emits
  the first and prints a `WARNING` naming every type found and the one used: a
  role is one Deployment, so it carries one node selector and its replicas cannot
  be split across types. The permissive `labelValues` plural form is deliberately
  never emitted — it would read as heterogeneity support while allowing a
  homogeneous placement. Heterogeneity *within* one role is an llm-d-side
  concern, not something bootstrap can express.
- A replica-count row the alias table does not recognize (`Number of sidecar
  pods`) is a **hard error**, not a silent skip — dropping it would discard a
  stated pod count.

**Constraints:**
- Scenario names MUST be lowercase alphanumeric only (1-20 chars, no hyphens).
  The manifest validator enforces this. Sanitize if generate script produces hyphens.

**Present to user:** Show generated scenario(s) with key fields (replicas, TP,
max-num-seqs, hardware) and ask for approval.

**Verify:**
```bash
# Post-rename, the operator's chosen baseline is at baselines/baseline.yaml.
python3 -c "import yaml; yaml.safe_load(open('baselines/baseline.yaml'))" \
  && echo "OK: baselines/baseline.yaml"
```

---

### Task 4: Select workloads

**action:** shell

Include all YAML files in `workloads/`. No filtering — the benchmark harness
handles dormant workloads gracefully, and including them confirms the algorithm
is inert under low load.

```bash
ls "$EXPERIMENT_ROOT"/workloads/*.yaml
```

**Output:** ordered list of all workload paths (alphabetical) for transfer.yaml.

---

### Task 4b: Copy framework defaults overlay

**action:** shell

Framework workarounds documented in `docs/troubleshooting.md` (request-id
preservation, EPP/vLLM verbosity, sidecar resource requests, model-PVC sizing,
topology and tokenizer wiring) are applied automatically by `sim2real assemble`
from `<experiment-root>/baselines/defaults/`. Copy the framework templates into
the experiment so each experiment is self-contained and reproducible.

```bash
mkdir -p "$EXPERIMENT_ROOT/baselines/defaults"
cp "$SKILL_DIR/templates/defaults/"*.yaml "$EXPERIMENT_ROOT/baselines/defaults/"
ls "$EXPERIMENT_ROOT/baselines/defaults/"
```

Each fragment is a partial scenario YAML — operators can edit individual
fragments in place to tweak them for the experiment, or list a fragment
stem under `defaults.disable` in `transfer.yaml` to opt out entirely.

**Not every fragment is universally correct.** `epponly` is a topology decision,
`model-pvc-size` is sized for a 70B-class model, and `tokenizer-sidecar` matters
only when the arms declare a `token-producer` plugin. Each fragment's header
comment states its own applicability and when to disable it. Making emission
conditional rather than leaving that to the operator is tracked as #840.

**Fragments that ship disabled (issue #853).** Four are copied but listed in
`defaults.disable` by Task 5, because none can be a correct always-on default.
Shipping them anyway means an operator has something to enable rather than
reinvent; enabling one is deleting its line from `defaults.disable`.

| Fragment | Why it cannot default to on |
|---|---|
| `pod-capabilities` | Cannot grant the permission it depends on. On OpenShift the pod will not admit until someone runs `oc adm policy add-scc-to-user privileged` out of band, so enabled-by-default yields unschedulable pods. |
| `nic-exclusion` | The value is a literal device list for one cluster's cards. A default naming another cluster's devices would exclude the wrong ones — worse than excluding none. |
| `rdma-device-reservation` | Does not work on the cluster it was written for: the pod netns has no RDMA-capable device, so it reserves an `rdma/roce_gdr` to advertise a capability the pod cannot use. |
| `preserve-request-id` | An Istio `EnvoyFilter` selecting on a hardcoded gateway name. Under `epponly` + `externallyManaged` no Gateway is deployed, so it matches nothing — and it is the only fragment using `extraObjects`, so it is the only one that can fail at *apply* time rather than being silently inert. |

Note the two bootstrap modes reach the same place by different routes: `--byo`
already lists **every** stem in `defaults.disable` (`byo.py`'s
`copy_framework_defaults`), so these four are disabled there without a special
case. Reconciling that broader BLIS/BYO difference is #840's, not this list's.

---

### Task 5: Create transfer.yaml

**action:** derive-and-approve  
**depends:** task-2, task-3, task-4, task-4b

Assemble transfer manifest from all prior task outputs.

**Derivation:**

1. `scenario`: from experiment folder name or README title
2. `component`: from task-1 ($COMPONENT_NAME, $COMPONENT_REF)
3. `component.kind`: scan algorithm Go source for interface references:
   - `UsageLimitPolicy` / flowcontrol -> `EndpointPickerConfig`
   - `Scorer` / scheduling -> `EndpointPickerConfig`
   - `Admission` -> `AdmissionPolicyConfig`
4. `component.build.commands`: standard Go build + test for relevant package
5. `baselines`: one entry, always `{ name: baseline, scenario: baselines/baseline.yaml }` (issue #544 — the baseline identifier is hardcoded)
6. `algorithms`: from `algorithms/*.go` — each file with a Factory function
   or plugin registration pattern is a candidate. Every entry's `defaults`
   field is always `baseline`.
7. `workloads`: from task-4 output
8. `context.files`: files in experiment root with deployment config or signal
   mapping (README.md, config.md, etc.)
9. `context.text`: summary of target interface and placement from algorithm source
10. `blis_observe`: obtained by invoking
    `python3 "$SKILL_DIR/generate_from_config.py" "$EXPERIMENT_ROOT/config.md" --emit-observe-yaml`
    and pasting its stdout verbatim between `workloads:` and `context:`. The
    script parses the `blis observe \ ... \` block in config.md and emits all
    5 keys with per-key `# source:` provenance comments; if `config.md` is
    absent or the block is missing, an all-defaults fragment is emitted. Do
    NOT hand-edit the fragment — regenerate by re-running the script.
    The block is validated against `blis observe`'s flag namespace (issue
    #602): recognized flags map to their key or `extraArgs`, while replay-only
    flags (e.g. `--session-mode`) and any non-observe flag are dropped with a
    `WARNING:` on stderr rather than transcribed. If a dropped flag was in fact
    intended, add it to `blis_observe.extraArgs` in `transfer.yaml` by hand.

**Output schema:**
```yaml
kind: sim2real-transfer
version: 3

scenario: <derived>

component:
  repo: <$COMPONENT_NAME>
  kind: <derived from algorithm interface>
  ref: <$COMPONENT_REF>
  base_image:
    hub: <derived from component repo org, e.g., ghcr.io/llm-d>
    name: <$COMPONENT_NAME>
  build:
    commands: <derived from algorithm imports>

algorithms:
  - name: <lowercase-alphanumeric>
    source: <relative path to algorithm .go file>
    defaults: baseline  # issue #544 — always the literal string 'baseline'

baselines:
  - name: baseline
    scenario: baselines/baseline.yaml

workloads: <list from task-4>

blis_observe:
  # Populated by `generate_from_config.py --emit-observe-yaml` (see Derivation
  # step 10 below). Each key carries a `# source:` comment indicating whether
  # it came from the `blis observe \ ... \` block in config.md or from the
  # sim2real-bootstrap default (which matches pipeline/pipeline.yaml).
  maxConcurrency: <value>  # source: config.md | sim2real-bootstrap default
  timeout: <value>         # source: config.md | sim2real-bootstrap default
  warmupRequests: <value>  # source: config.md | sim2real-bootstrap default
  prewarmDuration: <value> # source: config.md | sim2real-bootstrap default
  extraArgs: <value>       # source: config.md | sim2real-bootstrap default

context:
  text: <derived summary>
  files: <list of context files>

defaults:
  # These four ship DISABLED. Each is copied into baselines/defaults/ so an
  # operator has something to enable rather than reinvent, but none can be a
  # correct always-on default — see "Fragments that ship disabled" below.
  # Enabling one is deleting its line.
  disable:
    - nic-exclusion
    - pod-capabilities
    - preserve-request-id
    - rdma-device-reservation
  # Available fragments (filename stems in baselines/defaults/):
  #   - epp-verbosity
  #   - epponly
  #   - externally-managed-gateway
  #   - model-pvc-size
  #   - nic-exclusion               (ships disabled)
  #   - pod-capabilities            (ships disabled)
  #   - preserve-request-id         (ships disabled)
  #   - rdma-device-reservation     (ships disabled)
  #   - routing-proxy-resources
  #   - tokenizer-sidecar
  #   - vllm-keepalive
  #   - vllm-logging
```

The `# Available fragments` comment lists every YAML filename stem currently in
`<experiment-root>/baselines/defaults/` (populated by Task 4b from
`templates/defaults/`). Regenerate the list from what actually got copied — do
NOT copy the enumeration above verbatim if `templates/defaults/` has changed
since this SKILL.md was written:

```bash
ls "$EXPERIMENT_ROOT/baselines/defaults/"*.yaml \
    | xargs -n1 basename \
    | sed 's/\.yaml$//' \
    | sort
```

**Constraints:**
- `component.repo` must equal the submodule directory name
- All names (baselines, algorithms) must be lowercase alphanumeric only, 1-20 chars
- `context.files` paths resolved relative to experiment root
- `component` required when `algorithms` is non-empty
- `blis_observe` keys must match the schema in `pipeline/lib/manifest.py`:
  exactly the 5 keys `maxConcurrency`, `timeout`, `warmupRequests`,
  `prewarmDuration`, `extraArgs`. Values must be scalars (string or number,
  not bool). Do not add other keys — the manifest validator rejects them.

**Present to user:** Show summary (scenario, component@ref, algorithm count,
baseline count, workload count, context files) and ask for approval.

---

### Task 6: Verify pipeline can load manifest

**action:** shell  
**depends:** task-5

```bash
python3 -c "
import sys; sys.path.insert(0, '$SIM2REAL/pipeline/lib')
from manifest import load_manifest
m = load_manifest('$EXPERIMENT_ROOT/transfer.yaml')
print('Manifest valid')
print(f'  scenario: {m[\"scenario\"]}')
print(f'  component.repo: {m[\"component\"][\"repo\"]}')
print(f'  component.ref: {m[\"component\"].get(\"ref\", \"none\")}')
print(f'  baselines: {[b[\"name\"] for b in m[\"baselines\"]]}')
print(f'  algorithms: {[a[\"name\"] for a in m[\"algorithms\"]]}')
print(f'  workloads: {m[\"workloads\"]}')
print(f'  context.files: {m[\"context\"][\"files\"]}')
"
```

Exit code 0 and all fields printed = success.

---

## Final File Tree

```
<experiment-root>/
  transfer.yaml                  <- task-5
  baselines/
    <name>.yaml                  <- task-3
    defaults/                    <- task-4b
      epp-verbosity.yaml
      epponly.yaml
      externally-managed-gateway.yaml
      model-pvc-size.yaml
      nic-exclusion.yaml              <- in defaults.disable
      pod-capabilities.yaml           <- in defaults.disable
      preserve-request-id.yaml        <- in defaults.disable
      rdma-device-reservation.yaml    <- in defaults.disable
      routing-proxy-resources.yaml
      tokenizer-sidecar.yaml
      vllm-keepalive.yaml
      vllm-logging.yaml
  <component-name>/             <- task-2 (submodule)
  algorithms/
    <algorithm>.go               <- pre-existing
  workloads/
    <workload>.yaml              <- pre-existing
  .gitignore                     <- task-0
  .pre-commit-config.yaml        <- task-0b
  .secrets.baseline              <- task-0b
```

## After Bootstrap

Tell the user:
```
Bootstrap complete. Next steps (skill-driven translation flow — BLIS output has
algorithms[].source, so `sim2real translate` runs the /sim2real-translate skill
to produce the plugin sources, then `sim2real build` compiles them into images):

  1. Configure the workspace:
       python pipeline/setup.py --experiment-root $EXPERIMENT_ROOT
  2. Checkpoint the translation:
       python pipeline/sim2real.py translate
  3. Run the /sim2real-translate skill to produce plugin sources for each algorithm.
  4. Validate the skill's outputs:
       python pipeline/sim2real.py translate --resume
  5. Build per-algorithm images:
       python pipeline/sim2real.py build --translation <hash>
  6. Assemble a run:
       python pipeline/sim2real.py assemble \
         --translation <hash> --cluster <cluster_id> --run <run_name>
  7. Validate against a real cluster (after `deploy.py run` + `deploy.py collect`):
       /sim2real-check --run <run_name>
```

For pre-built EPP images (no skill-driven translation), see the [`--byo` mode](#--byo-mode) section below — the operator supplies baseline + overlays directly and bootstrap emits a batched `translation register` command that skips steps 2-5 above.

### Activate the secret-scan hook

Task 0b scaffolds `.pre-commit-config.yaml` + `.secrets.baseline` but cannot
install git hooks for the operator. Tell the user:

```
Activate the committed secret scan (blocking, whole-repo detect-secrets):
    pip install pre-commit detect-secrets
    pre-commit install

Every collaborator who clones must run `pre-commit install` once — git hooks
are not cloneable. For a known-clean commit the scanner false-positives on,
audit the finding into .secrets.baseline (preferred) or `git commit --no-verify`.

Retrofitting a repo that ALREADY committed secrets: rotate/scrub the live
tokens FIRST, then regenerate the baseline over the cleaned tree
(`detect-secrets scan --baseline .secrets.baseline`). Never regenerate over a
tree that still contains a live token — that audits it as "known" and
permanently whitelists it.
```

## `--byo` mode

Use `--byo` when the operator has a pre-built EPP image (or several), a full baseline scenario YAML, and per-algorithm overlay YAMLs, but no BLIS artifacts. Bootstrap copies the operator's files into canonical experiment-repo locations, emits `transfer.yaml` with `byo: true` per-algorithm markers (no `component:` — BYO has no submodule), and prints a batched `sim2real translation register` command.

### Invocation

```bash
python3 "$SKILL_DIR/byo.py" \
    --byo \
    --baseline <scenario-path> \
    --algorithm <name> [--algorithm <name>]... \
    --algorithm-image <name>=<image-ref> [--algorithm-image ...]... \
    --algorithm-config <name>=<overlay-path> [--algorithm-config ...]... \
    [--scenario <name>] [--force] [--non-interactive]
```

Note: `--baseline` takes only a path — the baseline identifier in
`transfer.yaml` is always the literal string `baseline` (issue #544).
The file lands at `<experiment-root>/baselines/baseline.yaml` regardless
of the source path.

The BYO branch is implemented in `byo.py` (bundled with this skill). SKILL.md's role is to accept operator input (structured args OR natural-language prose), collect any missing fields, and hand off to `byo.py`.

### Input collection (interactive TTY)

Operators may invoke the skill in either shape:

1. **Structured args** — the invocation above, verbatim.
2. **Natural language** — e.g., *"bootstrap --byo with baseline at /path/baseline.yaml, algorithm foo image ghcr.io/foo:tag config /path/foo.yaml, algorithm bar image ghcr.io/bar@sha256:... config /path/bar.yaml"*. Parse the description into structured args (algorithm/image/config triples + baseline path).

If any required field is missing after parsing, prompt with plain-text numbered prompts (one prompt per missing field). Example prompts:

```
Missing algorithm image for 'foo'. Enter the image reference:
> 
```

Do NOT use `AskUserQuestion` (project preference — see the persistent user memory on this repo).

If stdin is not a TTY OR the operator passed `--non-interactive`, do not prompt — invoke `byo.py` with whatever was supplied and let it fatal-error on missing fields.

### Reserved names

- `default`, `defaults` — reserved for any role.
- `baseline`, `baselines` — reserved as algorithm names. The baseline identifier is always `baseline` (issue #544), so operators do not choose it; the `baselines` reservation guards the `translations/<hash>/generated/baselines/` overlay umbrella from a collision with an algorithm dir.

### On-disk layout (produced by `byo.py`)

```
<experiment-root>/
  transfer.yaml                                  <- BYO transfer.yaml (byo: true per algo, no component:)
  baselines/
    baseline.yaml                                <- copy of operator's baseline scenario (always this name)
    defaults/*.yaml                              <- framework fragments (all listed under defaults.disable)
  algorithms/
    <algo-1>/<algo-1>_config.yaml                <- copy of operator's overlay
    <algo-2>/<algo-2>_config.yaml                <- copy of operator's overlay
  workloads/*.yaml                               <- pre-existing (operator brought)
  .pre-commit-config.yaml                        <- secret-scan hook (issue #822, create-if-missing)
  .secrets.baseline                              <- detect-secrets baseline (empty results)
```

Copies are atomic (write-to-temp + rename). Destinations validated to lie inside `<experiment-root>`; symlinks that escape are rejected. YAML inputs are parse-validated (single-doc, mapping root, non-empty) before any writes. The pre-commit secret scan is scaffolded create-if-missing (same as BLIS Task 0b); activate it per the [Activate the secret-scan hook](#activate-the-secret-scan-hook) instructions.

### Emitted register command

At success, `byo.py` prints:

```
cd '/absolute/path/to/exp-root'
sim2real translation register \
    --algorithm '<name1>=<image1>@algorithms/<name1>/<name1>_config.yaml' \
    --algorithm '<name2>=<image2>@algorithms/<name2>/<name2>_config.yaml'
```

All paths quoted with `shlex.quote`. Bootstrap does not invoke `register` itself — the operator retains control.

### After BYO bootstrap

```
1. Run the emitted register command above.
2. Assemble a run:
     sim2real assemble --translation <hash> --cluster <cluster_id> --run <run-name>
3. Deploy:
     python pipeline/deploy.py run
```

## Reference: Bundled Files

This skill ships with supporting files in its directory. Invoke in place — do NOT copy to experiment roots.

| File | Purpose |
|------|---------|
| `generate_from_config.py` | Parses `config.md` markdown tables → scenario YAMLs with provenance comments. Preferred for most BLIS experiments. Handles hardware normalization, bare-flag prefix-caching input/output, and unknown model/hardware detection. |
| `generate_scenarios.py` | Converts JSON config (`top3_selection.json`) → scenario YAMLs. Use when JSON input exists (BLIS). |
| `generate_scenarios.README.md` | Coverage map for the JSON-input path. Documents field mappings, omission rules, and gaps. |
| `pd_plumbing.py` | The P/D and multi-GPU pod plumbing (issue #848): one connector decision in two spellings (`kvTransfer.connector` engine-side, `routing.connector` sidecar-side), the two gate predicates, and one line-emitter per YAML fragment. Imported by both generators — the fragments are identical literal YAML, so a single copy is what keeps the two hand-rolled emitters in agreement. Asserted by `tests/test_pd_plumbing.py` and by the cross-generator check in `tests/test_generate_scenarios.py`. |
| `byo.py` | Implements the `--byo` branch — argument parsing, YAML validation, path-safe copy operations, `transfer.yaml` emission, batched `sim2real translation register` command generation. Invoked by SKILL.md's dispatch when `--byo` (or any BYO-only flag) is passed. |
| `templates/defaults/*.yaml` | Framework-owned baseline workaround fragments (request-id, verbosity, sidecar sizing, model-PVC size, topology, tokenizer) plus the P/D cluster-specific ones that ship disabled (pod capabilities, NIC exclusion, RDMA reservation — issue #853). Copied into `<experiment-root>/baselines/defaults/` at BLIS task-4b and at BYO run time, so every experiment is self-contained and reproducible. Shape and merge-safety are asserted by `tests/test_defaults_templates.py`. |
