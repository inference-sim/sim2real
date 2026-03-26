# Pipeline Cleanup Scripts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate and rename the existing Tekton/Stage 5 cleanup script to `hack/`, and create a new full-pipeline reset script that also reverts Stage 3 submodule changes.

**Architecture:** Two sibling scripts in `hack/`. The full-reset script (`transfer_pipeline_cleanup.sh`) delegates to the partial script (`tekton_artifacts_cleanup.sh`) for Stage 3 Step 8 + Stage 5 work, avoiding duplication. Submodule paths are read from `workspace/stage3_output.json` (recorded by Stage 3) rather than hardcoded.

**Tech Stack:** Bash, python3 (stdlib json module for parsing stage3_output.json), git

---

## File Structure

- Create: `hack/tekton_artifacts_cleanup.sh` — renamed/moved from `tmp/pipeline_cleanup.sh`, header updated
- Create: `hack/transfer_pipeline_cleanup.sh` — new full pipeline reset script
- Delete: `tmp/pipeline_cleanup.sh`

---

### Task 1: Create `hack/tekton_artifacts_cleanup.sh`

**Files:**
- Create: `hack/tekton_artifacts_cleanup.sh`

- [ ] **Step 1: Create the `hack/` directory**

```bash
mkdir -p hack
```

- [ ] **Step 2: Write `hack/tekton_artifacts_cleanup.sh`**

Content is identical to `tmp/pipeline_cleanup.sh` except:
- Header comment updated to reflect new location, name, and the fact that `transfer_pipeline_cleanup.sh` delegates to it
- `REPO_ROOT` path adjusted: the script now lives one level deeper (`hack/` vs `tmp/`), so `"$(dirname "$0")/.."` still resolves to the repo root correctly — no change needed there

```bash
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
```

- [ ] **Step 3: Make executable**

```bash
chmod +x hack/tekton_artifacts_cleanup.sh
```

- [ ] **Step 4: Verify it runs cleanly against an empty workspace**

```bash
bash hack/tekton_artifacts_cleanup.sh
```

Expected output: all lines show `(absent)`, exits 0, no errors.

- [ ] **Step 5: Commit**

```bash
git add hack/tekton_artifacts_cleanup.sh
git commit -m "feat: add hack/tekton_artifacts_cleanup.sh (moved from tmp/)"
```

---

### Task 2: Create `hack/transfer_pipeline_cleanup.sh`

**Files:**
- Create: `hack/transfer_pipeline_cleanup.sh`

- [ ] **Step 1: Write `hack/transfer_pipeline_cleanup.sh`**

```bash
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
TEKTON_CLEANUP="$(dirname "$0")/tekton_artifacts_cleanup.sh"

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
```

- [ ] **Step 2: Make executable**

```bash
chmod +x hack/transfer_pipeline_cleanup.sh
```

- [ ] **Step 3: Verify it runs cleanly with no workspace artifacts and no stage3_output.json**

Remove `workspace/stage3_output.json` temporarily if present, then run:

```bash
bash hack/transfer_pipeline_cleanup.sh
```

Expected: warning about missing `stage3_output.json`, all artifact lines show `(absent)`, exits 0.

- [ ] **Step 4: Verify it runs with a real `workspace/stage3_output.json`**

If `workspace/stage3_output.json` exists (from a prior pipeline run), run:

```bash
bash hack/transfer_pipeline_cleanup.sh
```

Expected:
- Scorer files removed (or reported absent)
- `register.go` reverted in submodule (or git reports "already at HEAD")
- Stage 1–3 workspace files removed
- Tekton + Stage 5 files removed
- Exits 0

- [ ] **Step 5: Commit**

```bash
git add hack/transfer_pipeline_cleanup.sh
git commit -m "feat: add hack/transfer_pipeline_cleanup.sh (full pipeline reset)"
```

---

### Task 3: Remove `tmp/pipeline_cleanup.sh`

**Files:**
- Delete: `tmp/pipeline_cleanup.sh`

- [ ] **Step 1: Delete the old script**

```bash
git rm tmp/pipeline_cleanup.sh
```

- [ ] **Step 2: Commit**

```bash
git commit -m "chore: remove tmp/pipeline_cleanup.sh (superseded by hack/tekton_artifacts_cleanup.sh)"
```
