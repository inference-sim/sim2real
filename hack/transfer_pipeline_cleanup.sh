#!/usr/bin/env bash
# transfer_pipeline_cleanup.sh — Full pipeline reset.
# Removes ALL workspace artifacts (Stages 1–5) and reverts Stage 3 changes to
# the llm-d-inference-scheduler submodule.
#
# Submodule file paths are read from workspace/stage3_output.json (written by Stage 3).
# If that file is absent or malformed, submodule cleanup is skipped with a warning.
#
# Stage 3 Step 8 (Tekton) and Stage 5 cleanup is delegated to tekton_artifacts_cleanup.sh.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

STAGE3_JSON="workspace/stage3_output.json"
TEKTON_CLEANUP="$REPO_ROOT/hack/tekton_artifacts_cleanup.sh"

echo "=== Full transfer pipeline cleanup ==="

# --- Verify delegate script exists ---
if [[ ! -f "$TEKTON_CLEANUP" ]]; then
  echo "ERROR: delegate script not found: $TEKTON_CLEANUP" >&2
  exit 1
fi

# --- Step 1: Parse stage3_output.json ---
echo
echo "-- Parsing $STAGE3_JSON --"

SCORER_FILE=""
TEST_FILE=""
REGISTER_FILE=""
SKIP_SUBMODULE=0

if [[ ! -f "$STAGE3_JSON" ]]; then
  echo "  WARNING: $STAGE3_JSON not found — skipping submodule cleanup"
  SKIP_SUBMODULE=1
else
  SCORER_FILE=$(python3 -c "
import json, sys
try:
    d = json.load(open('$STAGE3_JSON'))
    print(d['scorer_file'])
except (KeyError, json.JSONDecodeError) as e:
    print('ERROR:' + str(e), file=sys.stderr)
    sys.exit(1)
" 2>/dev/null) || { echo "  WARNING: $STAGE3_JSON malformed or missing 'scorer_file' — skipping submodule cleanup"; SKIP_SUBMODULE=1; }

  if [[ "$SKIP_SUBMODULE" -eq 0 ]]; then
    TEST_FILE=$(python3 -c "
import json, sys
try:
    d = json.load(open('$STAGE3_JSON'))
    print(d['test_file'])
except (KeyError, json.JSONDecodeError) as e:
    print('ERROR:' + str(e), file=sys.stderr)
    sys.exit(1)
" 2>/dev/null) || { echo "  WARNING: $STAGE3_JSON missing 'test_file' — skipping submodule cleanup"; SKIP_SUBMODULE=1; }
  fi

  if [[ "$SKIP_SUBMODULE" -eq 0 ]]; then
    REGISTER_FILE=$(python3 -c "
import json, sys
try:
    d = json.load(open('$STAGE3_JSON'))
    print(d['register_file'])
except (KeyError, json.JSONDecodeError) as e:
    print('ERROR:' + str(e), file=sys.stderr)
    sys.exit(1)
" 2>/dev/null) || { echo "  WARNING: $STAGE3_JSON missing 'register_file' — skipping submodule cleanup"; SKIP_SUBMODULE=1; }
  fi
fi

# --- Step 2: Stage 1–3 workspace artifacts ---
echo
echo "-- Stage 1-3 workspace artifacts --"

STAGE13_FILES=(
  workspace/algorithm_summary.json
  workspace/stage3_output.json
  workspace/translation_validation.json
)

for f in "${STAGE13_FILES[@]}"; do
  if [[ -f "$f" ]]; then
    rm "$f" && echo "  removed $f"
  else
    echo "  (absent) $f"
  fi
done

# --- Step 3: Stage 3 submodule changes ---
echo
echo "-- Stage 3 submodule changes --"

if [[ "$SKIP_SUBMODULE" -eq 1 ]]; then
  echo "  (skipped)"
else
  # Delete untracked scorer files (paths are repo-root-relative)
  for f in "$SCORER_FILE" "$TEST_FILE"; do
    if [[ -f "$f" ]]; then
      rm "$f" && echo "  removed $f"
    else
      echo "  (absent) $f"
    fi
  done

  # Revert register_file: strip the submodule prefix to get the submodule-relative path.
  # REGISTER_FILE is repo-root-relative, e.g. "llm-d-inference-scheduler/pkg/plugins/register.go"
  # git -C runs in the submodule root; the argument must be submodule-relative.
  SUBMODULE_DIR="$(echo "$REGISTER_FILE" | cut -d'/' -f1)"
  SUBMODULE_REL_PATH="$(echo "$REGISTER_FILE" | cut -d'/' -f2-)"

  echo "  reverting $REGISTER_FILE via git checkout HEAD --"
  git -C "$SUBMODULE_DIR" checkout HEAD -- "$SUBMODULE_REL_PATH" \
    && echo "  reverted $REGISTER_FILE" \
    || { echo "ERROR: failed to revert $REGISTER_FILE" >&2; exit 1; }
fi

# --- Step 4: Delegate to tekton_artifacts_cleanup.sh ---
echo
echo "-- Delegating to tekton_artifacts_cleanup.sh --"
bash "$TEKTON_CLEANUP"
