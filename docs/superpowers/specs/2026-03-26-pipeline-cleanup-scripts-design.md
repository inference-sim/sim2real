# Pipeline Cleanup Scripts Design

**Date:** 2026-03-26
**Status:** Approved
**Author:** Claude Code + kalantar

## Problem

The repository has one partial cleanup script (`tmp/pipeline_cleanup.sh`) that removes Stage 3 Step 8 (Tekton) and Stage 5 (benchmark) artifacts. There is no script to reset the full pipeline — all workspace artifacts from all stages plus the Stage 3 changes made to the `llm-d-inference-scheduler` submodule.

Additionally, the existing script lives in `tmp/` (not version-controlled as a developer tool) and has a name (`pipeline_cleanup.sh`) that doesn't clearly describe its scope.

## Goals

- Provide a full-reset cleanup script that removes all pipeline artifacts and reverts submodule changes introduced by Stage 3.
- Keep the partial (Tekton + Stage 5) cleanup script, with a clearer name, so operators can re-run from Stage 3 Step 8 without wiping Stages 1–3 artifacts.
- Make both scripts discoverable to all developers by placing them in `hack/`.

## File Structure

```
hack/
  tekton_artifacts_cleanup.sh     # renamed from tmp/pipeline_cleanup.sh
  transfer_pipeline_cleanup.sh    # new: full pipeline reset
```

## `tekton_artifacts_cleanup.sh`

Identical functionality to the current `tmp/pipeline_cleanup.sh`. Removes:

- `workspace/tekton/` artifacts (algorithm_values.yaml, values.yaml, pipelinerun-*.yaml, compiled/)
- Stage 5 state and collected data (benchmark_state.json, pipeline_commit.txt, noise/baseline/treatment results, benchmark_output.json, signal_coverage.json, validation_results.json, comparison_table.txt, transfer_evidence.md)
- Stage 5 raw directories (workspace/noise_raw/, workspace/baseline_raw/, workspace/treatment_raw/)

Only the header comment is updated to reflect the new location and name.

## `transfer_pipeline_cleanup.sh`

Full pipeline reset. Steps in order:

### Step 1: Parse `workspace/stage3_output.json`

Extract `scorer_file`, `test_file`, and `register_file` paths using `python3 -c`. Python 3.10 is already a project development dependency, so no additional runtime dependency is introduced. Pure-bash JSON parsing and `jq` are both avoided.

- If the file is **absent**: emit a warning, skip submodule cleanup entirely, continue with workspace artifact removal.
- If the file is **malformed** (missing expected keys): emit a warning, skip submodule cleanup, continue.

### Step 2: Remove Stage 1–3 workspace artifacts

Delete if present (report absent without error):

- `workspace/algorithm_summary.json`
- `workspace/stage3_output.json`
- `workspace/translation_validation.json`

Note: `workspace/signal_coverage.json` is intentionally omitted here. It is listed in `tekton_artifacts_cleanup.sh`'s Stage 5 removal set and will be cleaned in Step 4. Removing it in both steps would be a harmless no-op, but omitting it here keeps the stage boundaries clean.

### Step 3: Revert Stage 3 submodule changes

Requires `stage3_output.json` to have been parsed successfully in Step 1. The script only touches files explicitly listed in `stage3_output.json` — no other tracked or untracked changes in the submodule are affected.

- **Delete untracked scorer files:** `rm` the repo-root-relative paths from `scorer_file` and `test_file` keys if they exist.
- **Revert `register_file`:** The `register_file` value in `stage3_output.json` is a repo-root-relative path (e.g. `llm-d-inference-scheduler/pkg/plugins/register.go`). To revert it, the script must:
  1. Determine the submodule root by stripping the filename from the path and walking up to the submodule boundary. In practice: `cd llm-d-inference-scheduler/` and pass the path suffix (`pkg/plugins/register.go`) to `git checkout HEAD --`.
  2. Run `git -C llm-d-inference-scheduler checkout HEAD -- pkg/plugins/register.go` (where the submodule-relative path is derived by stripping the `llm-d-inference-scheduler/` prefix from the `register_file` value).

No `git reset --hard` or `git clean -fd` — only targeted operations on known paths.

### Step 4: Delegate to `tekton_artifacts_cleanup.sh`

Invoke `bash "$(dirname "$0")/tekton_artifacts_cleanup.sh"` to handle Stage 3 Step 8 (Tekton) and Stage 5 (benchmark) artifacts. This ensures the two scripts don't diverge in function.

`transfer_pipeline_cleanup.sh` runs under `set -euo pipefail`. If `tekton_artifacts_cleanup.sh` exits non-zero, `transfer_pipeline_cleanup.sh` will abort. `tekton_artifacts_cleanup.sh` is expected to always exit 0 (it reports absent files but does not error on them).

## Error Handling

| Condition | Behavior |
|-----------|----------|
| `stage3_output.json` absent | Warn, skip submodule cleanup, proceed |
| `stage3_output.json` malformed | Warn, skip submodule cleanup, proceed |
| Scorer file already absent | Report `(absent)`, continue |
| `git checkout HEAD --` fails | Exit with error and message |
| `tekton_artifacts_cleanup.sh` not found | Exit with error |
| `tekton_artifacts_cleanup.sh` exits non-zero | Aborts (inherited `set -e`) |

## What Is NOT Cleaned

- `blis_router/` — input artifacts from BLIS; never generated by the pipeline
- `config/env_defaults.yaml` — version-controlled environment defaults
- `docs/transfer/` — mapping artifacts and calibration log
- All tracked modifications in the `llm-d-inference-scheduler` submodule **except** the file listed in `register_file`. The script only acts on paths explicitly listed in `stage3_output.json`. Other tracked changes in `pkg/plugins/` (e.g. `active_request.go`) and elsewhere in the submodule are preserved. Operators who see residual submodule modifications after running this script should expect this — those are pre-existing changes unrelated to Stage 3.

## Migration

`tmp/pipeline_cleanup.sh` is deleted as part of this change; `hack/tekton_artifacts_cleanup.sh` replaces it. No CI workflows or CLAUDE.md entries reference the `tmp/pipeline_cleanup.sh` path — it was a local utility script not wired into any automation.
