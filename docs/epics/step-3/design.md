# Epic step-3 — `sim2real-check` port design

## Overview

Port the `sim2real-check` skill (`.claude/skills/sim2real-check/SKILL.md`) to work against the three-dimensional workspace that steps 1 and 2 produce, instead of the pre-refactor single-bundle-root layout. The skill's job (validate a real deployment against its simulation counterpart across workloads, configs, signals, policies, runtime health) is preserved verbatim; only the input model and path derivation change.

Two layers:

- **Layer 1: `sim2real resolve` — new subcommand in `pipeline/sim2real.py`.** A code-side helper that reads a run's on-disk state and emits a hydrated JSON blob covering the run's *starting-point* view: metadata, translation refs, phase/workload roots, and cluster-scenario paths. Per-workload sub-artifact enumeration (individual log files, resource snapshots, per-phase config detail) remains the check skill's responsibility — resolve is not exhaustive, and downstream skills still walk. Reusable — future skill ports (bootstrap, analyze) or operator scripts can call it.
- **Layer 2: `sim2real-check` — prompt template rewrite.** Two input modes (`--run R` and `--real <path>`), one downstream. Step 0 branches once on input shape, populates a common set of named shell variables, then every check subsection references those variables. Check semantics stay verbatim; only Step 0 changes structurally.

## Sources

- `docs/proposals/incremental-implementation-plan.md#step-3` — `sim2real-check` port (canonical scope).
- `docs/proposals/incremental-implementation-plan-addendum.md#step-3` — scope reality, open questions, risks.

## Design decisions (resolved during intake)

| Decision | Choice | Rationale |
|---|---|---|
| Real-side input | `--run R` (primary) or `--real <path>` (legacy) — two spellings of the same abstract input | User picks either; the "path" is the axis, not the shape |
| Translation argument | Removed (`--translation T` was redundant) | `run_metadata.json:translation_hash` derives it |
| Algorithm argument | Removed (`--algorithm A` was unnecessary) | Skill enumerates from disk; narrows conversationally in-prompt |
| Workload argument | Removed (`--workload W` was unnecessary) | Same reasoning as algorithm |
| Sim-side input | `--sim <path>` unchanged (sim bundle is a separate axis) | Sim bundle is BLIS output, unrelated to real-cluster output |
| Experiment root | `--experiment-root <path>` (override, defaults to `.`) | Matches other pipeline commands' flag surface |
| Legacy bundle mode | Kept alongside `--run` mode | Pre-refactor bundles exist on disk; supporting them costs one adapter |
| Metadata handoff | New `sim2real resolve --run R` subcommand emitting hydrated JSON | Testable helper vs. inline Python; reusable across future skill ports |
| Skill-side consumption of resolve output | Approach 1: parse JSON into named shell variables at Step 0; downstream check text references variables by name | Preserves current SKILL.md structure; single adapter site; matches how `sim2real-translate` SKILL.md consumes its `skill_input.json` |
| Integration test | Extend `pipeline/tests/test_assemble_run.py` end-to-end tests with a `resolve` invocation on the assembled output | Same fixture drives both assemble writer and resolve reader — drift catches itself |

Argparse surface after intake:

- `--sim <path>` — sim bundle (unchanged, always separate)
- `--real <path>` **OR** `--run <name>` — exactly one of these spellings of the real-side input (both required unless Step 0 auto-detect finds one)
- `--experiment-root <path>` — override, defaults to `.` (workspace at `./workspace`)
- Removed: `--translation`, `--algorithm`, `--workload`

## Architecture

### Data flow — `--run R` mode

```
user prompt: /sim2real-check --run trial-3
    ↓
SKILL.md Step 0
    ↓
$ sim2real resolve --run trial-3 [--experiment-root .]
    ↓
JSON on stdout: { run_dir, translation:{...}, results:{...}, cluster_scenarios:{...}, ... }
    ↓
jq extractions populate shell vars:
  CONFIGS_DIR, RESULTS_DIR, PHASES, WORKLOADS_BY_PHASE,
  TRANSLATION_HASH, IMAGE_TAG, MANIFEST_ASSEMBLY_PATH, ...
    ↓
Existing Steps 1-N (workload parity, config parity, signals, policies, health)
run against those vars — mode-agnostic downstream
```

### Data flow — `--real <path>` mode

```
user prompt: /sim2real-check --real /path/to/bundle
    ↓
SKILL.md Step 0
    ↓
Bash+Python fallback synthesizes the same shell-var set
by scanning the bundle directly
    ↓
Same Steps 1-N run against those vars
```

## `sim2real resolve` subcommand

### CLI

```
sim2real resolve --run <name> [--experiment-root <path>]
```

- `--run` required.
- `--experiment-root` defaults to `.` (workspace at `./workspace`).
- Exits `0` on success with JSON on stdout.
- Exits `2` on: unknown run, corrupt/missing `run_metadata.json`, unresolvable `translation_hash`, missing workspace/. Error messages point at the actual missing file and (where applicable) the sim2real command that would produce it.

### Output JSON shape (v1 schema, pinned)

```json
{
  "version": 1,
  "run_name": "trial-3",
  "run_dir": "/abs/path/workspace/runs/trial-3",
  "cluster_id": "pokprod001",
  "cluster_config_path": "/abs/path/workspace/clusters/pokprod001/cluster_config.json",
  "params_hash": "b751df505d96...",
  "image_tag": "ghcr.io/kalantar/llm-d-router:43b4e8d749df-softreflective",
  "assembled_at": "2026-07-04T15:41:48Z",
  "experiment_root": "/abs/path/experiment-repo",
  "translation": {
    "hash": "43b4e8d749df6b7ae334fc47609514d86dfc5a2697eb450e6a3149dbda013319",
    "alias": "softreflective",
    "source": "skill",
    "translations_dir": "/abs/path/workspace/translations/43b4e8d749df.../",
    "generated_dir": "/abs/path/workspace/translations/43b4e8d749df.../generated/",
    "algorithms": [
      {
        "name": "softreflective",
        "source_path": "algorithms/softreflective.py",
        "source_sha256": "e3b0c44...",
        "image_ref": "ghcr.io/.../43b4e8d749df-softreflective",
        "image_digest": "sha256:...",
        "generated_dir": "/abs/.../generated/softreflective/",
        "config_path": "/abs/.../generated/softreflective/softreflective_config.yaml"
      }
    ],
    "baselines": [
      {
        "name": "baseline",
        "generated_overlay_path": "/abs/.../generated/baseline_baseline/baseline_config.yaml"
      }
    ]
  },
  "results": {
    "results_dir": "/abs/path/workspace/runs/trial-3/results",
    "phases_declared": ["baseline", "softreflective", "constantceiling"],
    "phases_with_data": ["softreflective"],
    "workloads_by_phase": {
      "softreflective": ["code_generation_4"]
    }
  },
  "cluster_scenarios": {
    "cluster_dir": "/abs/path/workspace/runs/trial-3/cluster",
    "baseline_yaml": "/abs/.../cluster/baseline.yaml",
    "treatment_yamls": {"softreflective": "/abs/.../cluster/softreflective.yaml"},
    "pipelinerun_yamls": [
      "/abs/.../cluster/pipelinerun-code-generation-4-baseline.yaml",
      "/abs/.../cluster/pipelinerun-code-generation-4-softreflective.yaml"
    ]
  },
  "manifest_assembly": {
    "path": "/abs/.../manifest.assembly.yaml",
    "scenario": "test-scenario",
    "workloads": ["workloads/code_generation_4.yaml", "workloads/code_generation_10.yaml"],
    "defaults_disable": [],
    "blis_observe": {"timeout": 3600, "warmupRequests": 50, "maxConcurrency": 10000, "prewarmDuration": "60s", "extraArgs": ""}
  }
}
```

**Schema stability**: `version: 1`. Future versions are additive-only — new fields may appear, but existing fields keep their name and type. Skills and other consumers should tolerate unknown top-level keys. A v2 schema (breaking) would require a separate migration; there's no v0.

**Note on `translation_hash`**: it appears only under `translation.hash` (single source of truth from `translation_output.json`). The `run_metadata.json`'s `translation_hash` is used to *locate* the translation dir but isn't re-exposed at the top level — consumers reading resolve JSON should always use `translation.hash`.

**Note on `image_tag`**: the value at the top level is `run_metadata.json`'s `image_tag` verbatim — a single-image convenience string that assemble picks as the first kept algorithm's image_ref. In multi-algorithm runs this is a lossy summary; consumers that care about per-algorithm images should read `translation.algorithms[i].image_ref`. Pre-existing product decision at the assemble layer.

**Note on `source_sha256`**: present when the translation carries it (skill-driven producer path in step-2 sets it; BYO producer path may leave it null). Emit as `null` when absent, never as a missing key.

### Field-source mapping

- Top-level (run_name, run_dir, cluster_id, params_hash, image_tag, assembled_at): `run_metadata.json` (written by `sim2real assemble`).
- `cluster_config_path`: composed as `<experiment-root>/workspace/clusters/<cluster_id>/cluster_config.json`. Exists check performed; emitted as-is if present, `null` if the file is absent (partial workspace).
- `translation.*`: `translation_output.json` (via `pipeline/lib/translation_ref.py:read_translation_output`) + filesystem probes for generated dirs.
- `results.phases_declared`: union of `baselines[].name` + `algorithms[].name` from `manifest.assembly.yaml`. Represents what SHOULD have run.
- `results.phases_with_data`: `phases_declared` filtered to entries whose subdir contains at least one workload with `trace_data.csv`. Predicate accepts either shape: `results/<phase>/<workload>/trace_data.csv` (legacy flat) OR `results/<phase>/<workload>/iN/trace_data.csv` where `iN` matches `^i[1-9][0-9]*$` (replica shape written by `sim2real assemble` when `replicas > 1`). See `pipeline/lib/resolve.py:_workload_has_data` (issue #572). Invariant: `phases_with_data ⊆ phases_declared`.
- `results.workloads_by_phase`: filesystem listing per-phase using the same predicate as `phases_with_data`.
- `cluster_scenarios.*`: filesystem listing of `cluster/*.yaml`.
- `manifest_assembly.*`: parsed `manifest.assembly.yaml`.

### Code location

- New function `pipeline/lib/resolve.py:resolve_run(experiment_root, run_name) -> dict`.
- `pipeline/sim2real.py:_cmd_resolve` is a thin CLI wrapper that calls it and dumps JSON.

## SKILL.md Step 0 rewrite

Current Step 0 does auto-detect + `AskUserQuestion` confirmation over hardcoded `experiments/` scanning. Post-refactor, Step 0 becomes a small dispatcher.

### Argparse (at top of Step 0)

SKILL.md hand-parses `$ARGUMENTS` (no real argparse — it's a prompt template). Parse `--sim`, `--real`, `--run`, `--experiment-root` from the invocation. Enforce: `--run` and `--real` are mutually exclusive; exactly one is required unless Step 0 auto-detect finds one. Violation: exit the skill prose with `--run and --real are mutually exclusive; --run resolves a workspace-registered run, --real reads a filesystem bundle path.`

**`--experiment-root` scope**: this flag is the discovery prefix for `--run` mode (composes `<experiment-root>/workspace/runs/<name>/` and `<experiment-root>/workspace/clusters/<cluster_id>/`) and for the auto-detect fallback (scans `<experiment-root>/workspace/runs/` + `<experiment-root>/experiments/`). It is **not** meaningful in `--real` mode — `--real` takes an absolute (or CWD-relative) path directly to the bundle. Silently ignored if passed alongside `--real`.

### Mode dispatch (single branch)

```bash
if [ -n "$RUN" ]; then
    # Resolve-mode: pull hydrated JSON from the workspace
    sim2real resolve --run "$RUN" \
        ${EXPERIMENT_ROOT:+--experiment-root "$EXPERIMENT_ROOT"} \
        > /tmp/sim2real_check_resolved.json
    CONFIGS_DIR=$(jq -r '.translation.generated_dir' /tmp/sim2real_check_resolved.json)
    RESULTS_DIR=$(jq -r '.results.results_dir' /tmp/sim2real_check_resolved.json)
    PHASES=$(jq -r '.results.phases_with_data | join(" ")' /tmp/sim2real_check_resolved.json)
    IMAGE_TAG=$(jq -r '.image_tag' /tmp/sim2real_check_resolved.json)
    TRANSLATION_HASH=$(jq -r '.translation.hash' /tmp/sim2real_check_resolved.json)
    MANIFEST_ASSEMBLY_PATH=$(jq -r '.manifest_assembly.path' /tmp/sim2real_check_resolved.json)
    # ...etc for the other named variables
elif [ -n "$REAL" ]; then
    # Legacy-mode: scan the bundle directly, populate the same variables.
    # Phase discovery matches the current SKILL.md's flexibility — the
    # current skill accepts arbitrarily named phase dirs (line 161 of
    # current SKILL.md: "e.g., baseline/, treatment/ or quartic/,
    # control/"). Legacy bundles observed on disk also vary in shape:
    # some have phase dirs as direct children of $REAL, others under
    # $REAL/results/. Adapter probes both.
    CONFIGS_DIR="$REAL/generated"
    if [ -d "$REAL/results" ]; then
        RESULTS_DIR="$REAL/results"
    else
        RESULTS_DIR="$REAL"
    fi
    # Discover phase dirs via denylist rather than allowlist — anything
    # under RESULTS_DIR that's a directory AND not one of the well-known
    # non-phase names is a candidate phase.
    PHASES=$(ls "$RESULTS_DIR" 2>/dev/null | while read d; do
        case "$d" in
            generated|workloads|results_charts|snapshots|review|cluster|plans) ;;
            *) [ -d "$RESULTS_DIR/$d" ] && echo "$d" ;;
        esac
    done | tr '\n' ' ')
    IMAGE_TAG=""               # not available in legacy bundle
    TRANSLATION_HASH=""        # not available in legacy bundle
    MANIFEST_ASSEMBLY_PATH=""  # not available in legacy bundle
    CLUSTER_CONFIG_PATH=""     # not available in legacy bundle
else
    # Neither flag provided → auto-detect flow + AskUserQuestion confirmation.
    # See "Auto-detect + confirmation UX" section below for the concrete
    # enumeration and selection logic.
fi
```

### Auto-detect + confirmation UX (when neither `--run` nor `--real` is provided)

The current SKILL.md's Step 0 auto-detect flow is preserved:

1. Scan `$EXPERIMENT_ROOT/workspace/runs/*/run_metadata.json` — collect workspace-registered runs.
2. Scan `$EXPERIMENT_ROOT/experiments/*/` — collect legacy-shaped bundles (directories containing `generated/` + at least one phase-shaped sibling).
3. Scan `$EXPERIMENT_ROOT` (or `..`) for the auxiliary codebases the current skill already discovers: `BLIS` (`sim/` + `go.mod`), `GAIE` (`gateway-api-inference-extension` under `tmp/`), `LLMD` (`llm-d-inference-scheduler` under `tmp/`). Same predicates the current SKILL.md uses. These populate `BLIS`, `GAIE`, `LLMD` variables verbatim.
4. Present findings to the user via `AskUserQuestion`. Format:
   - If exactly one workspace run OR one legacy bundle found, show it as the default with a confirmation prompt.
   - If multiple candidates, present a numbered list with one entry per candidate — each entry carries the flag it would resolve to (e.g., "1) --run trial-3", "2) --run trial-2", "3) --real /path/to/legacy/bundle-a"). User picks by number.
   - If nothing found, exit with `no workspace runs at <experiment-root>/workspace/runs/ and no legacy bundles at <experiment-root>/experiments/. Run 'sim2real assemble --run <name>' to create a run, or pass --real <path> to check a legacy bundle.`

### Confirmation prompt preservation

After the mode branch resolves the variables (whether from `--run`, `--real`, or the auto-detect flow above), the current `AskUserQuestion` confirmation prompt still fires — showing the resolved values as defaults so the operator can override any of them before checks run. Addendum guidance ("preserve muscle memory; args are defaults, not overrides") is honored.

### `PHASES` semantics

`PHASES` is the list of package names (baseline + per-algorithm) that actually have collected data on disk. Every downstream check iterates this list — `for phase in $PHASES; do ... done` replaces the current SKILL.md's implicit "baseline and treatment as siblings" walk.

### Variables added vs. current SKILL.md

- New: `PHASES`, `WORKLOADS_BY_PHASE`, `TRANSLATION_HASH`, `IMAGE_TAG`, `MANIFEST_ASSEMBLY_PATH`, `CLUSTER_CONFIG_PATH`.
- Kept: `SIM`, `REAL` (now one of two spellings of the real-side input), `WORKLOAD` (interactive-only; no longer a CLI flag), `BLIS`, `GAIE`, `LLMD` (populated by the auto-detect logic from the current SKILL.md, unchanged).

Empty values in legacy-mode (`IMAGE_TAG=""`, `TRANSLATION_HASH=""`, `MANIFEST_ASSEMBLY_PATH=""`, `CLUSTER_CONFIG_PATH=""`) mean the downstream checks that use those variables silently skip in legacy-mode. The current SKILL.md doesn't reference those variables today (they're new to the resolve-mode path), so legacy-mode behavior is unchanged from pre-refactor.

## Preserved check subsections

Steps 1-N of the current SKILL.md (workload parity, config parity, signals, policies, runtime health, evidence requirements, output format) stay verbatim except for two mechanical changes:

1. **Path references** — `{REAL}/generated/` becomes `$CONFIGS_DIR`, `{REAL}/baseline/<workload>/` becomes `$RESULTS_DIR/baseline/<workload>/`, `{REAL}/treatment/<workload>/` becomes `$RESULTS_DIR/treatment/<workload>/` (legacy-mode) or `$RESULTS_DIR/<phase>/<workload>/` (resolve-mode). Because the two modes populate `RESULTS_DIR` differently (legacy has direct `baseline/`+`treatment/` siblings; resolve has `<phase>/` subdirs), the prose is written to iterate `$PHASES` rather than hardcode names.

2. **Phase iteration** — where the current SKILL.md says "for each workload in the real bundle" (implying scanning `baseline/` and `treatment/` as fixed names), the rewrite says "for each phase in `$PHASES`, iterate its workloads via `$WORKLOADS_BY_PHASE`". Same semantics; explicit iteration.

No new checks added. No existing checks removed. Report structure, evidence-based verdicts (PASS/WARN/FAIL), operating constraints (read-only) all preserved.

Diff-size expectation: ~30-50 lines of prose changed across the check subsections (mostly path-reference substitutions). The Step 0 section is largely rewritten (~80-120 lines). Total SKILL.md diff: ~150-200 lines out of 507.

## Error handling and edge cases

- **Both `--run` and `--real` provided**: argparse-level error at Step 0. Exit with `--run and --real are mutually exclusive; --run resolves a workspace-registered run, --real reads a filesystem bundle path.`
- **`--run <name>` but no `workspace/runs/<name>/`**: `sim2real resolve` exits 2 with `run '<name>' not found under <experiment-root>/workspace/runs/. Run 'sim2real assemble --run <name>' to create it, or 'sim2real list runs' to see existing runs.`
- **`--run <name>` where `run_metadata.json` exists but is corrupt / missing `translation_hash`**: exit 2 with `run_metadata.json at <path> is missing/corrupt: <specific reason>. Re-run 'sim2real assemble --run <name>' to regenerate.`
- **`--run <name>` where translation dir referenced by `translation_hash` doesn't exist**: exit 2 with `translation <hash> referenced by run '<name>' not found. Rebuild the translation by running 'sim2real translate' (skill-driven) or 'sim2real translation register' (BYO) with the same inputs, then re-assemble.`
- **`--real <path>` where path doesn't exist or lacks `generated/`**: legacy-mode fails at Step 0's scanning phase with the current SKILL.md's existing error prose.
- **Missing `phases_with_data`** (partial collection — some phases have `trace_data.csv`, others don't): normal case. `PHASES` contains only those that do; check subsections iterate that list. No error; user sees which phases were validated.
- **No collected data at all** (all phases empty): `PHASES=""`. Downstream checks that expect at least one phase print `no phases with data — run 'deploy.py collect --run <name>' to pull results` and exit gracefully.
- **Missing `manifest.assembly.yaml`**: only affects downstream checks that use it (workloads parity, blis_observe verification). Those specific checks warn and skip; other checks continue.
- **Missing `experiment-root/workspace/`**: exits 2 immediately with `no workspace/ under <experiment-root>. Pass --experiment-root or cd into the experiment repo root.`

## Testing plan

### Unit tests for `pipeline/lib/resolve.py:resolve_run()`

New file `pipeline/tests/test_resolve.py`:

- Happy path: fake workspace with `run_metadata.json` + `translation_output.json` + `manifest.assembly.yaml` + populated results/ tree → asserts JSON output has expected shape and field values.
- Missing `run_metadata.json` → raises with clear message.
- Corrupt `run_metadata.json` → raises with parse-error detail.
- `translation_hash` in metadata doesn't resolve to a translation dir → raises.
- Partial results tree (only some phases populated) → `phases_with_data` correctly filtered.
- Multi-baseline manifest with multiple algorithms → all baselines and algorithms present in JSON.
- No collected data (results/ empty) → `phases_with_data == []`, valid JSON.
- Missing `manifest.assembly.yaml` → `manifest_assembly.path == null`, other fields populated.

Sized: ~15-20 tests. Uses the existing `pipeline/tests/test_assemble_run.py::_make_experiment` fixture pattern.

### Integration test: assemble → resolve parity + schema-completeness

Extend `pipeline/tests/test_assemble_run.py`:

- New test `test_resolve_reads_what_assemble_writes`: runs `assemble_run()` end-to-end via the existing E2E fixture, then invokes `resolve_run()` on the assembled output. Asserts every path in the resolve JSON exists on disk; asserts run_name / cluster_id / translation_hash / image_tag / params_hash / assembled_at values round-trip; asserts `manifest_assembly.workloads` matches what `_write_yaml` wrote to `transfer.yaml`.
- **Schema-completeness assertion** — same test also asserts `set(json.loads(run_meta_path.read_text()).keys()) ⊆ set(<known-keys-consumed-by-resolve>)`. If a future PR adds a new field to `run_metadata.json` without updating `resolve_run()` to expose it, this assertion fails — catches silent-drift of the class the adversarial review flagged. The set of "keys consumed by resolve" is small (7 fields today) and lives inline in the test; no new module needed.
- Assemble→resolve share one fixture, so schema divergence between writer and reader surfaces immediately.

Sized: ~1-2 new test methods.

### CLI wrapper tests

In `pipeline/tests/test_resolve.py` (or `test_sim2real.py`):

- `sim2real resolve --run <name>` exits 0 with parseable JSON on stdout for a valid fixture.
- `sim2real resolve --run does-not-exist` exits 2 with a specific error message to stderr.
- `sim2real resolve --run <name>` without a workspace exits 2 pointing at `--experiment-root`.

Sized: ~5 tests.

### SKILL.md changes

No unit tests (skill is a prompt template). Validated by the demo-evidence real-cluster smoke — running `/sim2real-check --run trial-N` against the `sr` experiment and confirming the report renders correctly for both resolve-mode (against a workspace-assembled run) and legacy-mode (against a pre-refactor bundle).

## Demo criteria

The skill-driven flow from step-2 landed a workspace-shaped run (`kalantar-msb/sr/workspace/runs/trial-3/`). `/sim2real-check --run trial-3` (in resolve-mode) produces a full validation report — PASS/WARN/FAIL verdicts across workloads / configs / signals / policies / runtime health for at least the softreflective phase's collected workloads. Additionally, `/sim2real-check --real <legacy-bundle>` produces a report against a pre-refactor bundle on disk (legacy-mode regression demo).

## Out of scope / deferred

- **Replica awareness**: step-5 territory. The ported skill assumes single-replica per package for now.
- **Validation-semantics changes**: no new checks, no dropped checks. Only input model changes.
- **Multi-baseline handling in the check subsections**: the current SKILL.md hardcodes a singular `baseline_config.yaml` reference throughout Steps 1-N (e.g., `<real>/generated/baseline_config.yaml`). That's a pre-existing single-baseline assumption; the resolve schema is multi-baseline-ready (plural `translation.baselines[]`) but the downstream check prose is not touched here. A run with two-or-more baselines would exercise the pre-existing assumption. Filed as a follow-up (see below).
- **Existing skill quality gaps** (silent-fallback predicates, partial-collection detection, hardware-parity plumbing, error message uniformity): the design's adversarial review pass surfaced several pre-existing issues in the check skill. They are NOT step-3 regressions; they existed before and are preserved as-is. Filed as **#487 — sim2real-check skill: review pass — consistency, efficiency, resilience**; the adversarial-review output is attached to that issue as input.
- **`sim2real-bootstrap` skill port**: step-4.
- **`sim2real-analyze` skill port + standard analysis library**: step-7 (new; see `docs/proposals/incremental-implementation-plan-addendum.md#step-7`).
- **Bundle-format conversions** (BYO → workspace, workspace → BYO): out of scope.
- **`sim2real list translations`**: already shipped in step-2 via issue #466.
- **`sim2real list runs`**: already shipped in step-1.

## Risks

- **Silent drift between step-2's assemble writes and step-3's check reads.** Mitigated by the integration test's schema-completeness assertion — any new key in `run_metadata.json` that resolve doesn't consume fails the test.
- **Skill muscle-memory regression.** Mitigated by preserving the `AskUserQuestion` confirmation prompt as a diagnostic even when args are supplied. Args are defaults, not silent overrides.
- **Two-mode Step 0 complexity.** Mitigated by pushing the mode branch into a single small block; downstream check subsections stay mode-agnostic.
- **Legacy `--real <path>` mode goes stale over time.** As pre-refactor bundles age out of use, the legacy adapter accumulates without exercising. Not a blocker for step-3 but worth revisiting in a future cleanup step.
- **Pre-existing check skill assumptions surfaced but not fixed.** The adversarial review pass identified several pre-existing issues in the check skill (singular-baseline hardcodes, silent-fallback predicates, partial-collection visibility). Step-3 explicitly preserves these behaviors — filing a follow-up review-pass issue captures them for later.
