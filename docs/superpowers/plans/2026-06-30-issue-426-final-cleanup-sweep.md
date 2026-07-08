# Issue #426 — Final cleanup sweep, close Step 0 epic

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to walk this plan step by step.

**Goal:** Run the grep-based audit from the issue's scope against `refactor/v2-step-0`, document the findings, run the demo's `setup_config.json` jq check + ruff + pytest, open the PR that formally ends Step 0.

**Architecture:** Pure verification — earlier Step 0 PRs (#427–#437) appear to have already done the substantive cleanup. The deliverable is a PR consisting of the audit summary in its body, plus the plan document itself committed for the record. No source-code edits are expected; if the audit surfaces a real production hit, fix it in this PR.

**Tech Stack:** Bash, grep, jq, ruff, pytest.

## Global Constraints

- Worktree: `/Users/kalantar/projects/go.workspace/src/github.com/inference-sim/sim2real/.claude/worktrees/issue-426-final-cleanup-sweep/`. Every `Edit`/`Write` path must contain that substring. The epic worktree at `.claude/worktrees/step-0/` is read-only as far as this PR is concerned — do not touch it.
- Branch: `refactor/v2-step-0-issue-426-final-cleanup-sweep`. Base: `refactor/v2-step-0`. NOT main.
- Rule for hits: production code hit = missed cleanup (fix here); test-file hit = OK if it's an absence-asserting test; `docs/proposals/`, `docs/epics/`, `docs/superpowers/plans/` = historical record, leave alone.
- Per issue: `--test-push` / `--test-push-tag` references in `pipeline/setup.py` and `pipeline/README.md` are intentional and retained — `step_test_push` stays per design's "Stays IF the registry credential test stays in setup.py" decision (PR #436 confirmed it stays).
- PR body must include `Closes #416`. The keyword sits as a paper-trail link but will not auto-close the epic on this PR's merge (PR targets a non-default branch). The actual epic close happens via `/close-epic` after this PR merges.

---

## File map

- **Create:** `docs/superpowers/plans/2026-06-30-issue-426-final-cleanup-sweep.md` — this plan, committed for the record.
- **Modify:** none expected. If the audit surfaces a production hit, add it to this list and fix.

---

## Task 1: Run the audits and record findings

**Files:**
- Modify: none unless the audit surfaces a real hit.

- [ ] **Step 1: Capture the audits**

  Run each of the seven grep patterns from `cwd = worktree root`. The exclusion filter removes historical/design noise:

  ```bash
  EX='/docs/proposals/\|/docs/epics/\|/docs/superpowers/plans/\|/\.git/\|/llm-d-benchmark/\|/inference-sim/\|/tektonc-data-collection/'

  echo "=== 1: setup_config.get(\"namespace\", ==="
  grep -rn 'setup_config\.get("namespace",' --include='*.py' --include='*.md' . 2>/dev/null | grep -vE "$EX"

  echo "=== 2: tektonc_dir ==="
  grep -rn 'tektonc_dir' --include='*.py' --include='*.md' . 2>/dev/null | grep -vE "$EX"

  echo "=== 3: setup_timestamp ==="
  grep -rn 'setup_timestamp' --include='*.py' --include='*.md' . 2>/dev/null | grep -vE "$EX"

  echo "=== 4: container_runtime as persisted field ==="
  grep -rn '"container_runtime"' --include='*.py' . 2>/dev/null | grep -vE "$EX"

  echo "=== 5: --no-cluster ==="
  grep -rn 'no-cluster\|no_cluster' --include='*.py' --include='*.md' . 2>/dev/null | grep -vE "$EX"

  echo "=== 6: --redeploy-tasks ==="
  grep -rn 'redeploy.tasks\|redeploy_tasks' --include='*.py' --include='*.md' . 2>/dev/null | grep -vE "$EX"

  echo "=== 7: --test-push / --test-push-tag ==="
  grep -rn 'test.push\|test_push' --include='*.py' --include='*.md' . 2>/dev/null | grep -vE "$EX"
  ```

- [ ] **Step 2: Triage each hit**

  Apply the rule:
  - Production source under `pipeline/` (non-test) ⇒ missed cleanup. Fix in this PR.
  - Under `pipeline/tests/` ⇒ OK if the test asserts absence/removal. Confirm by reading the surrounding context.
  - In `pipeline/README.md` / `CLAUDE.md` ⇒ OK for `--test-push` and `--test-push-tag` since those flags are retained. Anything else needs to be checked.

  Vet pass on this branch already confirmed:
  - Audits 1, 4 (production), 5 (production), 6: zero non-test/non-design hits.
  - Audits 2, 3, 4 (test): only `test_setup_pipeline.py::test_removed_keys_absent` (parameterised) and `test_first_run_creates_metadata` (`for absent in (...) assert absent not in meta`).
  - Audit 5, 6 (test): `test_setup_pipeline.py::REMOVED_FLAGS` list (asserts these flags are NOT accepted by the parser); `test_cluster_py.py:258` asserts cluster.py rejects `--no-cluster`.
  - Audit 7: documented in `pipeline/README.md` and implemented in `pipeline/setup.py` (`step_test_push`, `--test-push`, `--test-push-tag`). All retained intentionally per design.

- [ ] **Step 3: Reproduce the verdict in this session**

  Re-run the audit commands from Step 1 in the worktree and confirm the output matches the vet's findings. If a new hit appears (e.g., something that landed since vet ran), add it to the triage. Capture the raw output in a comment or scratch for the PR body — but pare it down to a summary table for the PR description.

---

## Task 2: Run the Demo jq check + ruff + pytest gates

**Files:**
- None. This task is a verification gate.

- [ ] **Step 1: Generate a `setup_config.json` and run the jq check**

  The demo's jq check requires an actual `setup_config.json` to assert against. Generate one in a tmp workspace, then probe:

  ```bash
  TMP=$(mktemp -d)
  PYTHONPATH=. python3 pipeline/setup.py \
      --experiment-root "$TMP" \
      --registry ghcr.io/example \
      --repo-name example-scorer \
      --run sweep-test \
      --orchestrator-image ghcr.io/example/orch:latest
  ls "$TMP/workspace/"
  jq 'has("namespaces") or has("is_openshift") or has("storage_class") or has("hf_secret_name") or has("workspaces") or has("namespace") or has("tektonc_dir") or has("container_runtime") or has("setup_timestamp")' \
      "$TMP/workspace/setup_config.json"
  ```

  Expected: jq prints `false`.

  If `setup.py` is interactive (prompts when registry credentials are missing), you may need to pipe `</dev/null` or supply `REGISTRY_USER`/`REGISTRY_TOKEN`/`HF_TOKEN` env-blanks. The check below should still work since `--test-push` is not passed.

  If the live run is awkward in a non-cluster context, the existing pytest `test_removed_keys_absent` (parameterised over the same field set) is the moral equivalent. Cite it in the PR body if you fall back to that.

- [ ] **Step 2: ruff**

  ```bash
  ruff check pipeline/ .claude/skills/ --select F
  ```

  Expected: `All checks passed!`.

- [ ] **Step 3: pytest**

  ```bash
  PYTHONPATH=. python3 -m pytest pipeline/ \
      .claude/skills/sim2real-analyze/tests/ \
      .claude/skills/sim2real-bootstrap/tests/ \
      .claude/skills/sim2real-translate/tests/ \
      -v 2>&1 | tail -10
  ```

  Expected: all pass (current baseline: 1276 passed, 2 xfailed).

---

## Task 3: Commit the plan doc and open the PR

**Files:**
- Add: `docs/superpowers/plans/2026-06-30-issue-426-final-cleanup-sweep.md` (this plan).

- [ ] **Step 1: Verify worktree-only state**

  ```bash
  git status
  git -C /Users/kalantar/projects/go.workspace/src/github.com/inference-sim/sim2real status
  ```

  Expected: this worktree shows only the plan as untracked (plus whatever fixes Task 1 triage required). Parent repo unchanged.

- [ ] **Step 2: Commit the plan**

  ```bash
  git add docs/superpowers/plans/2026-06-30-issue-426-final-cleanup-sweep.md
  git commit -m "docs(plan): record issue #426 final-cleanup-sweep plan"
  ```

- [ ] **Step 3: Push**

  ```bash
  git push -u origin refactor/v2-step-0-issue-426-final-cleanup-sweep
  ```

- [ ] **Step 4: Open the PR**

  ```bash
  gh pr create --base refactor/v2-step-0 \
      --title "chore: final cleanup sweep — close Step 0 epic (#426)" \
      --body-file <(cat <<'EOF'
  Closes #426.

  Final cleanup-sweep child for the Step 0 epic (#416). Implements the audit specified in the issue's scope; no production-code fixes were needed.

  ## Audit summary

  Seven grep patterns from the issue's scope, run against `refactor/v2-step-0` (post-#437). Test-file hits asserting absence are intentional retention; `docs/proposals/`, `docs/epics/`, and `docs/superpowers/plans/` references are historical records and excluded.

  | # | Pattern | Production hits | Test-file hits (all absence-asserting) | Retained? |
  |---|---|---|---|---|
  | 1 | `setup_config.get("namespace",` | 0 | 0 | — |
  | 2 | `tektonc_dir` | 0 | `test_setup_pipeline.py::test_removed_keys_absent` | OK |
  | 3 | `setup_timestamp` | 0 | same | OK |
  | 4 | `"container_runtime"` (persisted field) | 0 | `test_removed_keys_absent`, `test_first_run_creates_metadata` (assert-not-in) | OK |
  | 5 | `--no-cluster` | 0 | `test_setup_pipeline.py::REMOVED_FLAGS`, `test_cluster_py.py:258` (asserts rejection) | OK |
  | 6 | `--redeploy-tasks` | 0 | `test_setup_pipeline.py::REMOVED_FLAGS` | OK |
  | 7 | `--test-push` / `--test-push-tag` | retained in `pipeline/setup.py` (`step_test_push`) and documented in `pipeline/README.md` | tested in `test_setup_pipeline.py::KEPT_FLAGS` | **Intentional** — per design, `step_test_push` stays in setup.py (registry credential test). Confirmed retained in PR #436. |

  All production-code hits are accounted for; no missed cleanups detected.

  ## Gate checks

  - **Demo jq check** (from `docs/epics/step-0/design.md:691`): generated a `setup_config.json` via `pipeline/setup.py` in a tmp workspace and verified `jq 'has("namespaces") or has("is_openshift") or has("storage_class") or has("hf_secret_name") or has("workspaces") or has("namespace") or has("tektonc_dir") or has("container_runtime") or has("setup_timestamp")'` returns `false`. (The same assertion is also enforced by `test_setup_pipeline.py::test_removed_keys_absent`, which runs as part of CI.)
  - **`ruff check pipeline/ .claude/skills/ --select F`** — clean.
  - **`pytest pipeline/ .claude/skills/sim2real-analyze/tests/ .claude/skills/sim2real-bootstrap/tests/ .claude/skills/sim2real-translate/tests/ -v`** — 1276 passed, 2 xfailed.

  ## Step 0 status

  Closes the implementation half of Step 0. Final close-out (merging `refactor/v2-step-0` into `refactor/v2`, closing the epic) happens via `/close-epic` after this PR merges.

  Closes #416.

  ## Base

  Targets `refactor/v2-step-0` (epic #416 child).
  EOF
  )
  ```

  If `gh` fails with a token-scope error, retry with `unset GITHUB_TOKEN GH_TOKEN; gh pr create …`.

- [ ] **Step 5: Surface the PR URL**

---

## Self-review

**Spec coverage:**
- ☑ Grep audit on the seven identifier patterns — Task 1
- ☑ For each hit, fix-or-justify — Task 1 Step 2
- ☑ Demo jq check passes — Task 2 Step 1
- ☑ `ruff check pipeline/ .claude/skills/ --select F` clean — Task 2 Step 2
- ☑ `pytest ...` clean — Task 2 Step 3
- ☑ PR description includes audit summary + retained references + `step_test_push` status — Task 3 Step 4
- ☑ PR body includes `Closes #416` — Task 3 Step 4
- ☑ `Closes #426` to auto-close this child issue on merge — Task 3 Step 4 (will auto-close because it's in the body even though target is non-default? No — same caveat applies. The issue closing happens via the GitHub linked-PR mechanism, which works regardless of target branch. The auto-close keyword `Closes #N` works fully when the PR merges into the default branch; for non-default branches, GitHub links the issue but does not auto-close. We include the keyword anyway for the cross-link.)

**Placeholder scan:** none — every step has exact commands and inputs.

**Type consistency:** N/A (no code changes expected).
