# Remove `llm-d-benchmark` Submodule Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the `llm-d-benchmark` submodule from sim2real and scrub all references from active prompts and docs.

**Architecture:** Pure deletion — unregister the git submodule, remove its working tree and cached objects, then edit four files to remove references. No new code. Verification is grep-based (confirm no references remain in active files).

**Tech Stack:** git, bash, text editing

**Spec:** `docs/superpowers/specs/2026-03-26-remove-llm-d-benchmark-submodule-design.md`

---

## Files Modified

| File | Change |
|------|--------|
| `.gitmodules` | `[submodule "llm-d-benchmark"]` block removed automatically by `git rm` |
| `CLAUDE.md` | Remove `llm-d-benchmark/` line from Submodules section |
| `prompts/pr.md` | Remove Step 6 block (lines 201–231); clean up Halt Conditions table; remove Expected Outputs entry |
| `docs/submodule-update-guide.md` | Remove `llm-d-benchmark` section (lines 67–77) and its table row (line 100) |
| `blis_router/README.md` | Remove `llm-d-benchmark` mention from comment at line 121 |

---

## Task 1: Remove the git submodule

**Files:**
- Modify: `.gitmodules` (automated by git)
- Delete: `llm-d-benchmark/` working tree

- [ ] **Step 1: Unregister the submodule**

```bash
git submodule deinit -f llm-d-benchmark
```

Expected output includes: `Cleared directory 'llm-d-benchmark'`

- [ ] **Step 2: Remove from git index and working tree**

```bash
git rm llm-d-benchmark
```

Expected output: `rm 'llm-d-benchmark'`
This also removes the `[submodule "llm-d-benchmark"]` block from `.gitmodules`.

- [ ] **Step 3: Remove cached git objects**

```bash
rm -rf .git/modules/llm-d-benchmark
```

No output expected.

- [ ] **Step 4: Verify submodule is gone**

```bash
grep -q "llm-d-benchmark" .gitmodules && echo "FAIL: still present" || echo "PASS: not found"
ls llm-d-benchmark 2>&1 && echo "FAIL: dir still exists" || echo "PASS: dir removed"
```

Expected: Both lines print `PASS`.

- [ ] **Step 5: Commit**

```bash
git add .gitmodules
git commit -m "chore: remove llm-d-benchmark submodule"
```

---

## Task 2: Update `prompts/pr.md`

**Files:**
- Modify: `prompts/pr.md`

Three changes needed in this file:

### Change A — Remove Step 6 block

- [ ] **Step 1: Remove the entire Step 6 section**

Delete lines 201–231 (from `## Step 6: llm-d-benchmark PR (conditional)` through the closing ` ``` ` of the bash block plus the trailing blank line). The section immediately after Step 6 is `## Halt Conditions Summary`.

The block to remove (verbatim):

```
## Step 6: llm-d-benchmark PR (conditional)

If this transfer involved benchmark config changes in the llm-d-benchmark submodule, push a branch and create a PR there too. **If no benchmark config changes exist, skip this step.**

```bash
# Check for uncommitted changes in llm-d-benchmark
if ! git -C llm-d-benchmark diff --quiet HEAD; then
  BENCH_BRANCH="transfer/${ALG_NAME}"
  if git -C llm-d-benchmark ls-remote --exit-code --heads origin "$BENCH_BRANCH" 2>/dev/null; then
    TIMESTAMP=$(date +%Y%m%d-%H%M%S)
    BENCH_BRANCH="${BENCH_BRANCH}-${TIMESTAMP}"
  fi
  git -C llm-d-benchmark checkout -b "$BENCH_BRANCH"
  git -C llm-d-benchmark push origin "$BENCH_BRANCH" \
    || { echo "HALT: git push failed for llm-d-benchmark branch $BENCH_BRANCH"; exit 1; }
  cd llm-d-benchmark
  gh pr create \
    --title "feat(benchmark): add ${ALG_NAME} benchmark configs" \
    --base main \
    --head "$BENCH_BRANCH" \
    --body "Benchmark configs for sim-to-production transfer: \`${ALG_NAME}\`. See llm-d-inference-scheduler PR: ${SCHEDULER_PR_URL}" \
    || { echo "HALT: gh pr create failed for llm-d-benchmark. Branch '$BENCH_BRANCH' is pushed."; cd ..; exit 1; }
  BENCHMARK_PR_URL=$(gh pr view --json url -q .url)
  echo "Created benchmark PR: $BENCHMARK_PR_URL"
  cd ..
else
  echo "No benchmark config changes — skipping llm-d-benchmark PR."
  BENCHMARK_PR_URL="(none)"
fi
```
```


### Change B — Clean up Halt Conditions table

- [ ] **Step 2: Update the two rows that reference "Step 6"**

Current rows (in the Halt Conditions table):
```
| git push fails | Step 3 or 6 | HALT: report branch name |
| gh pr create fails | Step 5 or 6 | HALT: report pushed branch for manual recovery |
```

Replace with:
```
| git push fails | Step 3 | HALT: report branch name |
| gh pr create fails | Step 5 | HALT: report pushed branch for manual recovery |
```

### Change C — Remove Expected Outputs entry

- [ ] **Step 3: Remove the `llm-d-benchmark` PR URL line from Expected Outputs**

Current Expected Outputs section:
```
## Expected Outputs

- `docs/transfer/calibration_log.md` entry: appended by Step 4
- `llm-d-inference-scheduler` PR URL: printed by Step 5
- `llm-d-benchmark` PR URL (or "none"): printed by Step 6
```

Replace with:
```
## Expected Outputs

- `docs/transfer/calibration_log.md` entry: appended by Step 4
- `llm-d-inference-scheduler` PR URL: printed by Step 5
```

- [ ] **Step 4: Verify no llm-d-benchmark references remain in prompts/pr.md**

```bash
grep -n "llm-d-benchmark" prompts/pr.md && echo "FAIL: references remain" || echo "PASS: no references"
```

Expected: `PASS: no references`

- [ ] **Step 5: Commit**

```bash
git add prompts/pr.md
git commit -m "chore(pr): remove llm-d-benchmark Step 6 and references"
```

---

## Task 3: Update `CLAUDE.md`

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Remove the `llm-d-benchmark/` line from the Submodules section**

Current Submodules section (lines 19–25):
```markdown
## Submodules

- `inference-sim/` — Discrete-event LLM inference simulator (source of evolved algorithms)
- `llm-d-inference-scheduler/` — Production scheduler with scorer plugin system (target)
- `llm-d-benchmark/` — Benchmark harness for cluster-level validation (target)
- `tektonc-data-collection/` — Tekton-based cluster data collection pipeline (used by `compile-pipeline` subcommand)
```

Replace with:
```markdown
## Submodules

- `inference-sim/` — Discrete-event LLM inference simulator (source of evolved algorithms)
- `llm-d-inference-scheduler/` — Production scheduler with scorer plugin system (target)
- `tektonc-data-collection/` — Tekton-based cluster data collection pipeline (used by `compile-pipeline` subcommand)
```

- [ ] **Step 2: Verify**

```bash
grep "llm-d-benchmark" CLAUDE.md && echo "FAIL: reference remains" || echo "PASS: no reference"
```

Expected: `PASS: no reference`

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(CLAUDE): remove llm-d-benchmark from submodules list"
```

---

## Task 4: Update `docs/submodule-update-guide.md`

**Files:**
- Modify: `docs/submodule-update-guide.md`

Two changes needed:

### Change A — Remove the `llm-d-benchmark` section

- [ ] **Step 1: Remove the `llm-d-benchmark` section**

Delete lines 67–77 (from `## \`llm-d-benchmark\` — no pinned artifacts` through `---`):

```markdown
## `llm-d-benchmark` — no pinned artifacts

### What pins it

Nothing. No commit hash in any artifact.

### Process

No artifact updates required. Verify Stage 5/6 benchmark tooling still works after the bump, particularly if CLI flag names for `noise-characterize` or `benchmark` commands changed.

---
```

### Change B — Remove the table row

- [ ] **Step 2: Remove the `llm-d-benchmark` row from the Relevant paths table**

Current table (near end of file):
```markdown
| Submodule | Relevant paths |
|-----------|---------------|
| `llm-d-inference-scheduler` | `pkg/plugins/ vendor/sigs.k8s.io/gateway-api-inference-extension/` |
| `inference-sim` | `sim/routing.go` |
| `llm-d-benchmark` | _(no pinned artifacts — verify tooling manually)_ |
```

Replace with:
```markdown
| Submodule | Relevant paths |
|-----------|---------------|
| `llm-d-inference-scheduler` | `pkg/plugins/ vendor/sigs.k8s.io/gateway-api-inference-extension/` |
| `inference-sim` | `sim/routing.go` |
```

- [ ] **Step 3: Verify**

```bash
grep "llm-d-benchmark" docs/submodule-update-guide.md && echo "FAIL: reference remains" || echo "PASS: no reference"
```

Expected: `PASS: no reference`

- [ ] **Step 4: Commit**

```bash
git add docs/submodule-update-guide.md
git commit -m "docs: remove llm-d-benchmark from submodule update guide"
```

---

## Task 5: Update `blis_router/README.md`

**Files:**
- Modify: `blis_router/README.md`

- [ ] **Step 1: Remove the `llm-d-benchmark` mention from the comment at line 121**

Current line 121:
```bash
# Use a load generator (e.g., llm-d-benchmark, vegeta, or custom client)
```

Replace with:
```bash
# Use a load generator (e.g., vegeta, or custom client)
```

- [ ] **Step 2: Verify**

```bash
grep "llm-d-benchmark" blis_router/README.md && echo "FAIL: reference remains" || echo "PASS: no reference"
```

Expected: `PASS: no reference`

- [ ] **Step 3: Commit**

```bash
git add blis_router/README.md
git commit -m "docs(blis_router): remove llm-d-benchmark mention"
```

---

## Task 6: Final verification

- [ ] **Step 1: Confirm no llm-d-benchmark references remain in active (non-archival) files**

Note: `docs/plans/` and `docs/contributing/` are intentionally excluded — they contain historical/archival references that the spec explicitly does not require removing.

```bash
grep -rn "llm-d-benchmark" \
  .gitmodules CLAUDE.md prompts/ docs/submodule-update-guide.md blis_router/README.md \
  && echo "FAIL: references remain" || echo "PASS: all clear"
```

Expected: `PASS: all clear`

- [ ] **Step 2: Confirm git status is clean**

```bash
git status
```

Expected: `nothing to commit, working tree clean`

- [ ] **Step 3: Confirm submodule working tree is gone**

```bash
ls llm-d-benchmark 2>&1
```

Expected: `ls: llm-d-benchmark: No such file or directory`

- [ ] **Step 4: Close the issue**

```bash
gh issue close 38 --comment "Removed llm-d-benchmark submodule and all active references."
```
