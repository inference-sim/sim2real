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
- `transfer.yaml` (or `config/transfer.yaml` for backward compat)
- `env_defaults.yaml` (or `config/env_defaults.yaml`)
- `algorithm/` and `workloads/` directories as referenced in `transfer.yaml`
- `workspace/` in `.gitignore`

`pipeline/templates/pipeline.yaml.j2` is the framework default Tekton template. Override it per-experiment by placing `pipeline.yaml.j2` in the experiment root, or pass `--pipeline-template PATH` to `prepare.py`.

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
| `--registry REG` | — | interactive |
| `--registry-user USER` | `QUAY_ROBOT_USERNAME` | interactive |
| `--registry-token TOKEN` | `QUAY_ROBOT_TOKEN` | interactive |
| `--run NAME` | — | `sim2real-YYYY-MM-DD` |
| `--no-cluster` | — | false |
| `--redeploy-tasks` | — | false |

**`--namespaces NS1,NS2,...`** — provision multiple namespace slots for parallel pool execution. Each slot is bootstrapped identically to a single `--namespace`.

**`--no-cluster`** — generates `setup_config.json` without touching the cluster; useful when cluster access comes later.

**`--redeploy-tasks`** — fast path to re-apply Tekton step/task YAMLs only (requires `--namespace`).

Writes `workspace/setup_config.json` and `workspace/runs/<run>/run_metadata.json`. Subsequent scripts read namespace, registry, and run name from `setup_config.json`.

---

## prepare.py

6-phase state machine. Re-running skips completed phases.

```bash
python pipeline/prepare.py [--force] [--rebuild-context] [--manifest PATH] [--run NAME] [--mode parallel|sequential]
```

| Phase | Name | Skippable |
|-------|------|-----------|
| 1 | Init — validate manifest + prerequisites | on re-run |
| 2 | Context — assemble + cache context doc (SHA-256) | on cache hit |
| 3 | **Translate checkpoint** — write `skill_input.json`, wait | resumes on re-run |
| 4 | Assembly — `algorithm_values.yaml`, `values.yaml`, cluster YAMLs | on re-run |
| 5 | Summary — write `run_summary.md` | on re-run |
| 6 | **Gate** — `[d]eploy / [e]dit / [q]uit` | on re-run |

**Phase 3 checkpoint**: writes `skill_input.json` and exits cleanly (exit 0). Run `/sim2real-translate` in Claude Code, then re-run `prepare.py` to continue from Phase 4.

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

**`--mode parallel`** (default) — generates one shared Pipeline + one PipelineRun per `(workload, package)` pair; required for `deploy.py run`.

**`--mode sequential`** — preserves legacy single-experiment pipeline behavior (one Pipeline + one PipelineRun for the whole run).

Phase state is tracked per-run in `workspace/runs/<run>/.state.json`. Delete it (or use `--force`) to reset.

---

## deploy.py

Builds the EPP image, applies Tekton resources, and submits PipelineRuns. Requires gate verdict `READY TO DEPLOY`.

```bash
python pipeline/deploy.py [flags]
```

| Flag | Default | Notes |
|------|---------|-------|
| `--run NAME` | from `setup_config.json` | override active run |
| `--package NAME…` | `experiment` | `baseline`, `treatment`, or `experiment` |
| `--skip-build-epp` | false | reuse `epp_image` from `run_metadata.json` |
| `--dry-run` | false | print kubectl commands without applying |

**Default package is `experiment`** — a sequential baseline-then-treatment pipeline. Pass `--package baseline treatment` to submit them independently.

**`--skip-build-epp`** — skips the image build; use when resubmitting after a failed PipelineRun without changing the scorer.

**Subcommands:**

```bash
python pipeline/deploy.py run     [flags]   # orchestrate parallel pool execution across namespace slots
python pipeline/deploy.py status            # show progress snapshot of all (workload, package) pairs
python pipeline/deploy.py collect [--package NAME…]
```

**`deploy.py run`** — assigns `(workload, package)` pairs to free namespace slots, polls for completion, collects results inline, and retries pairs that time out. Reads `progress.json` to resume interrupted runs. Requires `prepare.py --mode parallel`.

| Flag | Default | Description |
|------|---------|-------------|
| `--only PAIR` | — | Reset and run one specific pair key |
| `--workload NAME` | — | Reset pairs matching this workload |
| `--package NAME` | — | Reset pairs matching this package |
| `--status STATE` | — | Reset pairs with this status (e.g. `failed`, `timed-out`) |
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
scenario: <name>            # must match a scenario in config/env_defaults.yaml

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
```

All paths are relative to the repo root and validated at Phase 1.

---

## Parallel Pool Execution

`setup.py --namespaces NS1,NS2,...` provisions N namespace slots, each bootstrapped identically. `prepare.py` (default `--mode parallel`) generates one shared Tekton Pipeline plus one PipelineRun per `(workload, package)` pair. `deploy.py run` orchestrates execution by assigning pairs to free slots, polling for completion, collecting results inline, and retrying on timeout. `deploy.py status` reads `workspace/runs/<run>/progress.json` and prints the current state of every pair.

| Artifact | Written by | Read by |
|----------|-----------|---------|
| `runs/<run>/progress.json` | `deploy.py run` | `deploy.py status` |

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
