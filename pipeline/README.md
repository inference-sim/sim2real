# pipeline/

Four scripts that drive the sim2real transfer pipeline. Run from the repo root.

```
setup.py → prepare.py → [/sim2real-translate] → deploy.py
```

`run.py` manages runs independently of the main flow.

---

## Running with an Experiment Repo

When algorithm content lives in its own repo (peer directory), pass `--experiment-root`:

```bash
# From the sim2real/ directory:
python pipeline/setup.py   --experiment-root ../admission-control
python pipeline/prepare.py --experiment-root ../admission-control
python pipeline/deploy.py  --experiment-root ../admission-control
```

The experiment repo must contain:
- `transfer.yaml` (or `config/transfer.yaml` for backward compat) — v3 schema with `target`, `config`, `observe`, `build`, `epp_image` fields
- `baseline.yaml` — llmdbenchmark-style scenario file (required for Phase 4 assembly)
- `treatment.yaml` (optional) — merged into `baseline.yaml` for the treatment scenario
- `algorithm/` and `workloads/` directories as referenced in `transfer.yaml`
- `workspace/` in `.gitignore`

`pipeline/pipeline.yaml` is the static Tekton Pipeline definition (applied by `deploy.py run`; Phase 4 generates PipelineRuns that reference it).

Omitting `--experiment-root` defaults to the framework directory — backward compatible with the existing `config/transfer.yaml` layout.

---

## setup.py

One-time, idempotent cluster bootstrap. Safe to re-run.

```bash
python pipeline/setup.py [flags]
```

| Flag | Env var | Default |
|------|---------|---------|
| `--namespace NS` | `NAMESPACE` | interactive |
| `--namespaces NS1,NS2,...` | — | — |
| `--hf-token TOKEN` | `HF_TOKEN` | interactive |
| `--github-token TOKEN` | `GITHUB_TOKEN` | — |
| `--registry REG` | — | interactive |
| `--registry-user USER` | `REGISTRY_USER` | interactive |
| `--registry-token TOKEN` | `REGISTRY_TOKEN` | interactive |
| `--run NAME` | — | `sim2real-YYYY-MM-DD` |
| `--no-cluster` | — | false |
| `--redeploy-tasks` | — | false |

**`--namespaces NS1,NS2,...`** — provision multiple namespace slots for parallel pool execution. Each slot is bootstrapped identically to a single `--namespace`.

**`--no-cluster`** — generates `setup_config.json` without touching the cluster; useful when cluster access comes later.

**`--redeploy-tasks`** — fast path to re-apply Tekton step/task YAMLs only (requires `--namespace`).

Writes `workspace/setup_config.json` and `workspace/runs/<run>/run_metadata.json`. Subsequent scripts read namespace, registry, and run name from `setup_config.json`.

`setup_config.json` includes workspace bindings for two PVCs: `data-storage` (`data-pvc`) and `source` (`source-pvc`). `source-pvc` holds the BLIS binary built at pipeline runtime by the `install-blis` task. Both must be bound before `deploy.py run` accepts a namespace slot.

---

## prepare.py

6-phase state machine. Re-running skips completed phases.

```bash
python pipeline/prepare.py [--force] [--rebuild-context] [--manifest PATH] [--run NAME]
```

| Phase | Name | Skippable |
|-------|------|-----------|
| 1 | Init — validate manifest + prerequisites | on re-run |
| 2 | Context — assemble + cache context doc (SHA-256) | on cache hit |
| 3 | **Translate checkpoint** — write `skill_input.json`, wait | resumes on re-run |
| 4 | Assembly — resolved scenarios, cluster YAMLs, PipelineRuns | on re-run |
| 5 | Summary — write `run_summary.md` | on re-run |
| 6 | **Gate** — `[d]eploy / [e]dit / [q]uit` | on re-run |

**Phase 3 checkpoint**: writes `skill_input.json` and exits cleanly (exit 0). Run `/sim2real-translate` in Claude Code, then re-run `prepare.py` to continue from Phase 4.

**Phase 4 assembly** reads `baseline.yaml` and `treatment.yaml` from the experiment root, merges them with skill-generated overlay files (`generated/baseline_config.yaml`, `generated/treatment_config.yaml`), and writes resolved scenario files to `cluster/`. PipelineRuns are generated with a `scenarioContent` param containing the fully resolved scenario YAML. Workloads are loaded from the manifest and scaled by `observe.request_multiplier`.

**Phase 6 gate**: `d` marks the run `READY TO DEPLOY` (required by `deploy.py`). `e` drops you back to edit files, then re-displays the summary. `q` marks `abandoned` and exits.

**Subcommands:**

```bash
python pipeline/prepare.py status              # show phase state for current run
python pipeline/prepare.py context             # rebuild context cache only
python pipeline/prepare.py assemble            # re-run phases 4–6 (translation must exist)
python pipeline/prepare.py validate-assembly   # run assembly checks standalone
```

**`--force`** — ignores `.state.json` and regenerates all phases.

**`--rebuild-context`** — ignores SHA cache and re-assembles context.

Phase state is tracked per-run in `workspace/runs/<run>/.state.json`. Delete it (or use `--force`) to reset.

---

## deploy.py

Builds the EPP image, applies Tekton resources, and orchestrates PipelineRun execution across namespace slots.

```bash
python pipeline/deploy.py {run|status|collect} [flags]
```

Common flags (all subcommands):

| Flag | Default | Notes |
|------|---------|-------|
| `--run NAME` | from `setup_config.json` | override active run |
| `--experiment-root PATH` | cwd | path to experiment repo |
| `--skip-build-epp` | false | reuse `epp_image` from `run_metadata.json` |

**Pair discovery** — `deploy.py run` discovers `pipelinerun-*.yaml` files at the `cluster/` root. Each file's pair key is derived as `wl-` + filename stem minus the `pipelinerun-` prefix.

**Collection phases** — `deploy.py collect` operates on fixed phases (`baseline`, `treatment`). Use `--package` to filter: `--package baseline`, `--package treatment`, or `--package experiment` (both).

**`--skip-build-epp`** — skips the image build; use when resubmitting after a failed PipelineRun without changing the scorer.

**Subcommands:**

```bash
python pipeline/deploy.py run     [flags]   # orchestrate parallel pool execution across namespace slots
python pipeline/deploy.py status            # show progress snapshot of all (workload, package) pairs
python pipeline/deploy.py collect [--package NAME…]
```

**`deploy.py run`** — assigns `(workload, package)` pairs to free namespace slots, polls for completion, collects results inline, and retries pairs that time out. Reads `progress.json` to resume interrupted runs.

| Flag | Default | Description |
|------|---------|-------------|
| `--only PAIR` | — | Scope execution to one specific pair key |
| `--workload NAME` | — | Scope execution to pairs matching this workload |
| `--package NAME` | — | Scope execution to pairs matching this package |
| `--status STATE` | — | Scope execution to pairs with this status (e.g. `failed`, `timed-out`) |
| `--force` | — | Reset all non-pending pairs in scope back to `pending` (clears retries) |
| `--max-retries N` | 2 | Max retries for timed-out pairs |
| `--poll-interval N` | 30 | Seconds between status polls |

**`deploy.py status`** — prints the current state of all pairs from `workspace/runs/<run>/progress.json`.

| Flag | Description |
|------|-------------|
| `--workload NAME` | Filter by workload name |
| `--package NAME` | Filter by package name |

**`deploy.py collect`** — extracts results from the cluster PVC and writes to `workspace/runs/<run>/results/{phase}/<workload>/`.

---

## monitor.py

Watches active namespace slots while `deploy.py run` is running. Detects pod failures,
auto-remediates transient issues (tier 1), emits rules-based suggestions (tier 2), and
calls the Anthropic API for novel failures (tier 3). Writes all findings to
`workspace/runs/<run>/health_report.md`.

```bash
# Start in a second terminal alongside deploy.py run
python pipeline/monitor.py --experiment-root ../admission-control

# Or background it
python pipeline/monitor.py --experiment-root ../admission-control &
```

**Requires:** `ANTHROPIC_API_KEY` in the environment for tier-3 API diagnosis.
If unset, tier-3 findings are written with a placeholder and no API call is made.

| Flag | Default | Description |
|------|---------|-------------|
| `--experiment-root PATH` | cwd | Root of the experiment repo |
| `--run NAME` | `current_run` from setup_config.json | Run name |
| `--interval SECONDS` | 30 | Poll interval |
| `--log-lines N` | 200 | Tail depth for pod logs sent to API |

---

## run.py

Manage and switch between runs.

```bash
python pipeline/run.py --experiment-root ../admission-control list
python pipeline/run.py --experiment-root ../admission-control inspect <name>
python pipeline/run.py --experiment-root ../admission-control switch <name>
```

`--experiment-root` defaults to the current working directory; omit it when running from the experiment repo root.

**`switch`** copies files listed in `translation_output.json` (`files_created` + `files_modified`) into the experiment repo's `llm-d-inference-scheduler/` directory and updates `setup_config.json`. Prompts before overwriting uncommitted changes.

---

## config/transfer.yaml

Manifest consumed by `prepare.py`. Version 3 required.

```yaml
kind: sim2real-transfer
version: 3
scenario: <name>            # scenario name used in generated PipelineRun labels

algorithm:
  source: <path>            # sim algorithm implementation
  config: <path>            # sim algorithm config

baseline:
  sim:
    config: <path>          # baseline policy for sim
  real:
    config: <path>          # optional: baseline EPP config template
    notes: |                # optional: notes embedded in skill_input.json

workloads:
  - <path>                  # one or more workload YAMLs

hints:
  files: [<path>, ...]      # hint files passed to translation skill
  text: |                   # freeform hint text

context:
  files: [<path>, ...]      # files assembled into context document (Phase 2)

# v3 fields (required unless noted)
target:
  repo: <path>              # llm-d-inference-scheduler repo path
config:
  kind: <string>            # config kind (e.g. "gaie")
  helm_path: <path>         # Helm chart path within target repo
observe:                    # optional — defaults applied if absent
  request_multiplier: 1     # scales workload num_requests for real-cluster benchmarks (default: 1)
build:                      # optional — defaults applied if absent
  commands: []              # EPP build commands
epp_image:                  # optional
  upstream:
    hub: <registry>
    name: <repo>
    tag: <tag>
  build:                    # override for built EPP image coordinates
    hub: <registry>
    name: <repo>
    tag: <tag>
pipeline:                   # optional — defaults applied if absent
  name: sim2real            # Pipeline resource name referenced in PipelineRuns (default: "sim2real")
  yaml: pipeline/pipeline.yaml  # path relative to repo root (default: "pipeline/pipeline.yaml")
```

All paths are relative to the repo root and validated at Phase 1.

---

## Parallel Pool Execution

`setup.py --namespaces NS1,NS2,...` provisions N namespace slots, each bootstrapped identically. `prepare.py` generates one shared Tekton Pipeline plus one PipelineRun per `(workload, package)` pair. `deploy.py run` orchestrates execution by assigning pairs to free slots, polling for completion, collecting results inline, and retrying on timeout. `deploy.py status` reads `workspace/runs/<run>/progress.json` and prints the current state of every pair.

| Artifact | Written by | Read by |
|----------|-----------|---------|
| `runs/<run>/progress.json` | `deploy.py run` | `deploy.py status` |

---

## Scenario Overlay Format

The `/sim2real-translate` skill produces two overlay files in `workspace/runs/<run>/generated/`:

- `baseline_config.yaml` — overlay merged onto `baseline.yaml`
- `treatment_config.yaml` — overlay merged onto the already-resolved baseline

### Assembly formula

```python
baseline_resolved = deep_merge(baseline_bundle, baseline_overlay)
treatment_resolved = deep_merge(deep_merge(baseline_resolved, treatment_diffs), treatment_overlay)
```

Where `baseline_bundle` is the experiment's `baseline.yaml`, `treatment_diffs` is the experiment's optional `treatment.yaml`, and the overlays are the skill outputs.

### Deep merge semantics

- Dict keys merge recursively (overlay overrides base)
- Lists of dicts with a common `name` field merge by name
- Lists of dicts without a common key merge positionally
- Lists of scalars are replaced entirely
- Treatment overlay only needs the delta from baseline_resolved (shared config propagates automatically)

### Required structure

Both overlays are llmdbenchmark scenario overlays. They must be valid YAML with a top-level `scenario:` list containing a single dict:

```yaml
scenario:
  - name: "<scenario-name>"

    # Fields to add or override (only include what you're changing)
    extraObjects: [...]
    inferenceExtension: {...}
    images: {...}
```

### InferenceObjective requirements

InferenceObjectives go in `extraObjects`. Each must include `spec.poolRef.name` referencing the InferencePool created by the gaie Helm chart:

```yaml
extraObjects:
  - apiVersion: inference.networking.x-k8s.io/v1alpha2
    kind: InferenceObjective
    metadata:
      name: critical
    spec:
      poolRef:
        name: ${model.idLabel}-gaie
      priority: 100
```

`${model.idLabel}` is resolved by llm-d-benchmark at render time (requires llm-d-benchmark >= PR #1103).

### Plugin config

The EPP plugin configuration goes inside `inferenceExtension.pluginsCustomConfig` as a YAML-in-YAML string:

```yaml
inferenceExtension:
  pluginsConfigFile: custom-plugins.yaml
  pluginsCustomConfig:
    custom-plugins.yaml: |
      apiVersion: inference.networking.x-k8s.io/v1alpha1
      kind: EndpointPickerConfig
      plugins:
      - type: my-plugin
        name: my-plugin
        parameters:
          threshold: 5
      schedulingProfiles:
      - name: default
        plugins:
        - pluginRef: my-plugin
```

### Typical overlay content

**Baseline overlay** — adds InferenceObjectives and the baseline EPP plugin config:
- `extraObjects` (InferenceObjectives with `poolRef`)
- `inferenceExtension.pluginsCustomConfig` (baseline scorer config)

**Treatment overlay** — only the delta from baseline:
- `inferenceExtension.pluginsCustomConfig` (evolved scorer config)
- `images.inferenceScheduler` (custom EPP image — injected by `prepare.py`, not the skill)

If treatment uses the same InferenceObjectives as baseline, do NOT repeat them — they propagate from `baseline_resolved`.

---

## Common patterns

```bash
# Resume after translation
python pipeline/prepare.py

# Force full regeneration
python pipeline/prepare.py --force

# Resubmit without rebuilding EPP
python pipeline/deploy.py --skip-build-epp

# Dry-run deploy
python pipeline/deploy.py --dry-run

# Deploy individual packages
python pipeline/deploy.py --package baseline treatment

# Collect results for a specific package
python pipeline/deploy.py collect --package treatment
```
