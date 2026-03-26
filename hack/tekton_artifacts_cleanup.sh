#!/usr/bin/env bash
# tekton_artifacts_cleanup.sh — Remove Stage 3 Step 8 (Tekton) and Stage 5 (benchmark data) artifacts.
# Safe to run before re-running prompts/transfer.md from Stage 3 Step 8 onward.
# Does NOT touch Stages 1–3 artifacts (algorithm_summary.json, stage3_output.json, etc.)
#
# Also invoked by transfer_pipeline_cleanup.sh as a delegate for Stage 3 Step 8 + Stage 5 cleanup.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

echo "=== Pipeline cleanup: Stage 3 Step 8 (Tekton) + Stage 5 (benchmark data) ==="

# --- Stage 3 Step 8: Tekton artifacts ---
echo
echo "-- Tekton artifacts (workspace/tekton/) --"

TEKTON_FILES=(
  workspace/tekton/algorithm_values.yaml
  workspace/tekton/values.yaml
  workspace/tekton/pipelinerun-noise.yaml
  workspace/tekton/pipelinerun-baseline.yaml
  workspace/tekton/pipelinerun-treatment.yaml
)

for f in "${TEKTON_FILES[@]}"; do
  if [[ -f "$f" ]]; then
    rm "$f" && echo "  removed $f"
  else
    echo "  (absent) $f"
  fi
done

COMPILED_DIR="workspace/tekton/compiled"
if [[ -d "$COMPILED_DIR" ]]; then
  rm -rf "$COMPILED_DIR" && echo "  removed $COMPILED_DIR/"
else
  echo "  (absent) $COMPILED_DIR/"
fi

# --- Stage 5: Benchmark state and collected data ---
echo
echo "-- Stage 5 state + collected data --"

STAGE5_FILES=(
  workspace/benchmark_state.json
  workspace/pipeline_commit.txt
  workspace/noise_results.json
  workspace/baseline_results.json
  workspace/treatment_results.json
  workspace/benchmark_output.json
  workspace/signal_coverage.json
  workspace/validation_results.json
  workspace/comparison_table.txt
  workspace/transfer_evidence.md
)

for f in "${STAGE5_FILES[@]}"; do
  if [[ -f "$f" ]]; then
    rm "$f" && echo "  removed $f"
  else
    echo "  (absent) $f"
  fi
done

RAW_DIRS=(
  workspace/noise_raw
  workspace/baseline_raw
  workspace/treatment_raw
)

for d in "${RAW_DIRS[@]}"; do
  if [[ -d "$d" ]]; then
    rm -rf "$d" && echo "  removed $d/"
  else
    echo "  (absent) $d/"
  fi
done

echo
echo "=== Done. Preserved: workspace/algorithm_summary.json, workspace/stage3_output.json (Stages 1-3) ==="
