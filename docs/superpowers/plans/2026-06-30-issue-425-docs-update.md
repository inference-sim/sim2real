# Issue #425 — Docs + CI update for Step 0 flow

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Bring `pipeline/README.md`, `CLAUDE.md`, and `.github/workflows/test.yml` into alignment with the post-Step-0 flow where `cluster.py provision` does cluster bootstrap and `setup.py` shrinks to a workspace-config writer.

**Architecture:** Pure documentation + CI patch. Three files. No code change. Patches are itemized in the epic-0 design doc at `docs/epics/step-0/design.md:577` ("Documentation updates") which is the source of truth — this plan executes the patches against the current text on `refactor/v2-step-0` (post-#436, where setup.py has already been trimmed).

**Tech Stack:** Markdown, YAML.

## Global Constraints

- Base branch: `refactor/v2-step-0`. PR targets that branch, not `main`.
- Edit only inside the worktree `/Users/kalantar/projects/go.workspace/src/github.com/inference-sim/sim2real/.claude/worktrees/issue-425-docs-update/`. Every `Edit`/`Write` path must contain this substring.
- The design's per-row patch table uses line numbers from commit `0d02352`. Many lines have shifted from PRs #427–#436 that already merged. Apply by **semantic intent** (the "Update" column), not line number.
- Several patches the design lists are already applied on the current branch (e.g. README line 31's "applied by `cluster.py provision`", line 60's `--no-cluster` removal, line 171's slot-add language). Skip rows that are already done; flag them as no-ops in commit message rather than re-touching.
- Do not edit `docs/troubleshooting.md` or `.claude/skills/sim2real-*/SKILL.md` — design explicitly says zero updates required.
- CI must still pass after the change. `python -m pytest pipeline/ -v` and `ruff check pipeline/ --select F` are the local gates; the workflow file change itself doesn't run until pushed.

---

## File map

- **Modify** `.github/workflows/test.yml` — extend pytest invocation with explicit test paths.
- **Modify** `CLAUDE.md` (project root) — 8 line-level patches per design + pipeline/lib table extension + workspace artifact table extension.
- **Modify** `pipeline/README.md` — pipeline flow diagram, invocation block, new `## cluster.py` section, deploy.py source-of-truth line, new pipeline/lib module table, new workspace artifacts table.

---

## Task 1: CI workflow — add explicit test paths

**Files:**
- Modify: `.github/workflows/test.yml:30`

**Interfaces:**
- Consumes: nothing from previous tasks.
- Produces: nothing structural — only affects CI.

**Why explicit paths when `pipeline/` already globs them.** Design's CI section says: "Add the three new test files to the pytest invocation … (`test_layout`, `test_cluster_ops`, `test_cluster_py`, `test_slicer` should be picked up by `pipeline/` already, but explicitly verify)." Verified via `ls pipeline/tests/` — all four files exist and pytest's default discovery (`test_*.py` under `pipeline/`) picks them up. Adding explicit paths makes the CI definition self-documenting about what Step 0 introduced; the design author wanted this.

- [ ] **Step 1: Verify current invocation**

  Read `.github/workflows/test.yml:30`. Current value (single line):

  ```yaml
  run: python -m pytest pipeline/ .claude/skills/sim2real-analyze/tests/ .claude/skills/sim2real-bootstrap/tests/ .claude/skills/sim2real-translate/tests/ -v
  ```

- [ ] **Step 2: Replace with explicit-paths multiline form**

  Edit the `Run tests` step to use a multiline command with the four new test files called out explicitly:

  ```yaml
        - name: Run tests
          run: |
            python -m pytest pipeline/ \
              pipeline/tests/test_layout.py \
              pipeline/tests/test_cluster_ops.py \
              pipeline/tests/test_cluster_py.py \
              pipeline/tests/test_slicer.py \
              .claude/skills/sim2real-analyze/tests/ \
              .claude/skills/sim2real-bootstrap/tests/ \
              .claude/skills/sim2real-translate/tests/ \
              -v
  ```

  Note: `pipeline/` is kept first so the new test files are also picked up by glob — explicit listing is redundant-but-loud per design intent. Pytest tolerates duplicates (won't run them twice; it dedupes by path).

- [ ] **Step 3: Run tests locally to confirm nothing breaks**

  Run from the worktree root:

  ```bash
  python3 -m pytest pipeline/tests/test_layout.py pipeline/tests/test_cluster_ops.py pipeline/tests/test_cluster_py.py pipeline/tests/test_slicer.py -v
  ```

  Expected: all pass (these tests were merged with their PRs #427–#430).

- [ ] **Step 4: Commit**

  ```bash
  git add .github/workflows/test.yml
  git commit -m "ci: pin Step 0 test files explicitly in pytest invocation"
  ```

---

## Task 2: CLAUDE.md — 8 line-level patches + tables

**Files:**
- Modify: `CLAUDE.md` (project root)

**Interfaces:**
- Consumes: nothing.
- Produces: nothing structural.

The design's patch table for CLAUDE.md is the source of truth. Line numbers below are from the **current** file (post-#436), not the design doc.

| Design row | Current line | Change |
|---|---|---|
| 11 | 11 | "applied by `setup.py`" → "applied by `cluster.py provision`" |
| 27 | 27 | Pipeline-flow code block — add `cluster.py provision` as one-time-per-cluster prerequisite before the per-run chain |
| 33 | 33 | Invocation example block — add `python pipeline/cluster.py provision <cluster_id> --namespaces NS1,NS2,...` as the cluster-bootstrap step |
| 42 | 42 | Revise `**pipeline/setup.py**` bullet — workspace-config writer; cluster.py provision does cluster bootstrap |
| 57 | 57 | Append "and `clusters/<id>/cluster_config.json`" to deploy.py's "driven by …" sentence |
| 81 (table) | 83 | Update setup_config.json row's data ("workspace fields only"); add new `clusters/<id>/cluster_config.json` row |
| 83 (table) | 85 | `run_metadata.json` row — verify writers list (setup.py, deploy.py) is still accurate; **no edit needed** (design says note that cluster.py provision is NOT a writer; the row already reflects this) |
| 134 | 136 | Second pipeline-flow mention in Stage Contracts — same treatment as line 27 |

Also (per design's bullets, not a numbered row):
- Update the `## Pipeline Library (pipeline/lib/)` table (currently lines 65–75) to add `cluster_ops.py`, `layout.py`, `slicer.py`. The current table is stale — these modules exist in `pipeline/lib/` but are missing from the table. This is upkeep, but enforces internal consistency.

The existing `**pipeline/cluster.py**` paragraph (currently at line 61) was added before this PR by a prior merge but describes Step 0 as in progress ("setup.py remains the legacy 8-step flow until its trim PR lands"). The trim has now happened (#436). Reword to describe the steady state.

- [ ] **Step 1: Apply patches by exact-text Edit**

  Use one `Edit` call per row. Don't rewrite the whole file. After all eight edits, confirm with `git diff CLAUDE.md`.

  Exact edits (old → new):

  1. **Line 11** — Static Tekton Pipeline definition:
     - OLD: `- `pipeline/pipeline.yaml` — Static Tekton Pipeline definition (applied by `setup.py`)`
     - NEW: `- `pipeline/pipeline.yaml` — Static Tekton Pipeline definition (applied by `cluster.py provision`)`

  2. **Line 27** — Transfer Pipeline flow block:
     - OLD:
       ```
       setup.py → prepare.py → [/sim2real-translate] → deploy.py
       ```
     - NEW:
       ```
       cluster.py provision  (one-time per cluster)
                          ↓
       setup.py → prepare.py → [/sim2real-translate] → deploy.py
       ```

  3. **Line 33** — Invocation example block:
     - OLD:
       ```bash
       python pipeline/setup.py   --experiment-root ../admission-control
       python pipeline/prepare.py --experiment-root ../admission-control
       python pipeline/deploy.py run --experiment-root ../admission-control
       python pipeline/run.py     --experiment-root ../admission-control list
       python pipeline/run.py     --experiment-root ../admission-control switch <run-name>
       ```
     - NEW:
       ```bash
       # One-time cluster bootstrap (re-run only when adding/changing slots):
       python pipeline/cluster.py provision <cluster_id> --namespaces NS1,NS2,...

       # Per-workspace + per-run cycle:
       python pipeline/setup.py    --experiment-root ../admission-control
       python pipeline/prepare.py  --experiment-root ../admission-control
       python pipeline/deploy.py run --experiment-root ../admission-control
       python pipeline/run.py      --experiment-root ../admission-control list
       python pipeline/run.py      --experiment-root ../admission-control switch <run-name>
       ```

  4. **Line 42** — setup.py description:
     - OLD: ``**`pipeline/setup.py`** — One-time cluster bootstrap (namespace, RBAC, secrets, PVCs, Tekton tasks, Pipeline definition). Idempotent — safe to re-run. Supports `--pipeline-yaml PATH` to override the default Pipeline definition.``
     - NEW: ``**`pipeline/setup.py`** — One-time workspace config writer. Writes `setup_config.json` and `run_metadata.json` with operator-side fields (registry, repo name, current run, orchestrator image, pipeline_yaml path, sim2real_root). Idempotent — safe to re-run. Cluster-side bootstrap (namespaces, RBAC, secrets, PVCs, Tekton tasks, Pipeline definition) lives in `cluster.py provision`.``

  5. **Line 57** — deploy.py description:
     - OLD: ``**`pipeline/deploy.py`** — Builds EPP image and orchestrates PipelineRun execution across namespace slots (`deploy.py run`). Use `deploy.py collect` to pull results from the cluster PVC after runs complete. Operates independently of `transfer.yaml` — driven by workspace files and `setup_config.json`.``
     - NEW: ``**`pipeline/deploy.py`** — Builds EPP image and orchestrates PipelineRun execution across namespace slots (`deploy.py run`). Use `deploy.py collect` to pull results from the cluster PVC after runs complete. Operates independently of `transfer.yaml` — driven by workspace files, `setup_config.json`, and `clusters/<id>/cluster_config.json`.``

  6. **Line 61** — cluster.py paragraph (reword to steady state):
     - OLD: ``**`pipeline/cluster.py`** — New Step-0 entry point (epic #416). Cluster-side bootstrap only: `cluster.py provision <cluster_id> --namespaces NS1,NS2,...` writes `workspace/clusters/<cluster_id>/cluster_config.json` and provisions namespaces, RBAC, Secrets, PVCs, and Tekton tasks. The cluster-side responsibilities of `setup.py` are being carved into `cluster.py`; `setup.py` remains the legacy 8-step flow until its trim PR lands (see epic #416).``
     - NEW: ``**`pipeline/cluster.py`** — Cluster-side bootstrap. `cluster.py provision <cluster_id> --namespaces NS1,NS2,...` provisions namespaces, RBAC, Secrets, PVCs, and Tekton tasks, and writes `workspace/clusters/<cluster_id>/cluster_config.json`. Idempotent — re-run when adding or changing namespace slots.``

  7. **pipeline/lib/ module table (lines 65–75)** — add three rows. Insert after the existing `remote.py` row (preserve alphabetical-ish ordering by appending):
     - INSERT BEFORE the closing of the table (after the `remote.py` row):
       ```
       | `cluster_ops.py` | Cluster-side primitives: `read_cluster_config`/`write_cluster_config`/`update_cluster_config`, `provision_namespace`, `apply_cluster_resources`, `detect_openshift` |
       | `layout.py` | Workspace path helpers (`workspace_dir`, `cluster_dir`, `cluster_config_path`, `runs_dir`, `setup_config_path`) — used by every pipeline script |
       | `slicer.py` | Splits `transfer.yaml` into translation-slice vs assembly-slice + computes `translation_hash` |
       ```

  8. **Workspace Artifacts table (around line 83)** — update `setup_config.json` row and add `cluster_config.json` row:
     - REPLACE row `| `setup_config.json` | `setup.py` | `prepare.py`, `deploy.py` |`
     - WITH:
       ```
       | `setup_config.json` (workspace fields: registry, repo_name, current_run, orchestrator_image, pipeline_yaml, sim2real_root) | `setup.py` | `prepare.py`, `deploy.py`, `run.py` |
       | `clusters/<id>/cluster_config.json` (cluster fields: namespaces, is_openshift, storage_class, hf_secret_name, workspaces, secret_names) | `cluster.py provision` | `deploy.py`, `prepare.py`, `lib/remote.py`, `lib/run_manager.py` |
       ```

  9. **Line 134/136** — Stage Contracts pipeline-flow block:
     - OLD:
       ```
       setup.py → prepare.py → [sim2real-translate skill] → deploy.py
       ```
     - NEW:
       ```
       cluster.py provision  (one-time per cluster)
                          ↓
       setup.py → prepare.py → [sim2real-translate skill] → deploy.py
       ```

- [ ] **Step 2: Verify the result**

  ```bash
  git diff CLAUDE.md | head -150
  grep -n "cluster.py\|cluster_config\|setup_config" CLAUDE.md
  ```

  Expected: every reference to `setup.py` doing cluster bootstrap is gone; `cluster.py provision` appears in both pipeline-flow diagrams; pipeline/lib table includes three new rows; workspace artifact table has the cluster_config.json row.

- [ ] **Step 3: Commit**

  ```bash
  git add CLAUDE.md
  git commit -m "docs(CLAUDE.md): align with Step 0 — cluster.py provision split + cluster_config.json"
  ```

---

## Task 3: pipeline/README.md — narrative + new sections + line patches

**Files:**
- Modify: `pipeline/README.md`

**Interfaces:**
- Consumes: nothing.
- Produces: nothing structural.

The current README (post-#436) has already addressed several rows from the design table (line 31's "applied by cluster.py provision", line 60's --no-cluster removal, line 171's slot-add wording, line 331's parallel-pool intro). What remains:

| Design row | Current line(s) | Change |
|---|---|---|
| 6 | 6 | Pipeline flow diagram — add `cluster.py provision` as one-time prep |
| 19 | 17–22 | Invocation example block — add cluster.py provision example |
| 31 | 31 | already done — no change |
| 35–66 | 35–62 | setup.py section already trimmed by #436 — no change |
| 60 | n/a | `--no-cluster` already removed — no change |
| 66 | 37 | "Writes … run_metadata.json" — setup.py still writes run_metadata.json (workspace fields); current text is accurate — no change |
| 68 | 39, 62 | setup_config.json workspace-bindings note — already covered by current §setup.py paragraph — no change |
| 110 (was 116) | 110 | deploy.py "driven by … `setup_config.json`" — append `cluster_config.json:namespaces` source |
| 126 | n/a | Not present in current text (was about a flag table reference; flag table has `--run NAME from setup_config.json` which is workspace-scoped and stays) — no change |
| 171 | 171 | already done by #436; design's exact wording adds an issue-#377 forward-pointer — apply that wording |
| 177 | n/a | not present in current text — no change |
| 185 | 179 | "Requires `orchestrator_image` in `setup_config.json`" — design says unchanged (workspace-scoped) — no change |
| 261 | 255 | `switch` updates setup_config.json — design says unchanged — no change |
| 337 | 331 | already done by #436 — no change |
| New | n/a | Add `## cluster.py` section between `## Running with an Experiment Repo` and `## setup.py` |
| New | n/a | Add `## Pipeline library` section with module table (post-`## run.py`) |
| New | n/a | Add `## Workspace artifacts` section (after the new pipeline/lib section) |

Also: the top-level header / intro pipeline-flow diagram (line 6) and invocation block (line 19) need a "Quick start" framing. The design says the narrative should be rewritten — but the README is already deliberately tight. A minimal rewrite that adds **Step A** (cluster.py provision) before **Step B** (setup.py) covers the spirit of the rewrite without bloating.

- [ ] **Step 1: Update pipeline-flow diagram (line 6) and add a brief Quick-start framing above the experiment-repo subheader**

  - Replace the top of the file (lines 1–11) so the "two-phase" flow is the first thing the reader sees:

    OLD (lines 1–11):
    ```markdown
    # pipeline/

    Four scripts that drive the sim2real transfer pipeline. Run from the repo root.

    ```
    setup.py → prepare.py → [/sim2real-translate] → deploy.py
    ```

    `run.py` manages runs independently of the main flow.

    ---
    ```

    NEW:
    ```markdown
    # pipeline/

    Scripts that drive the sim2real transfer pipeline. Run from the repo root.

    The pipeline has two phases:

    ```
    cluster.py provision  (one-time per cluster — bootstrap namespaces, RBAC, PVCs, Tekton tasks)
                       ↓
    setup.py → prepare.py → [/sim2real-translate] → deploy.py   (per-workspace + per-run)
    ```

    `run.py` manages runs independently of the main flow.

    ---
    ```

- [ ] **Step 2: Update the invocation example block (lines 17–22) to add cluster.py provision as Step A**

  OLD (lines 17–22):
  ```bash
  # From the sim2real/ directory:
  python pipeline/setup.py   --experiment-root ../admission-control
  python pipeline/prepare.py --experiment-root ../admission-control
  python pipeline/deploy.py  --experiment-root ../admission-control
  ```

  NEW:
  ```bash
  # From the sim2real/ directory:

  # One-time per cluster (idempotent; re-run when adding/changing slots):
  python pipeline/cluster.py provision <cluster_id> --namespaces NS1,NS2,...

  # Per-workspace + per-run cycle:
  python pipeline/setup.py   --experiment-root ../admission-control
  python pipeline/prepare.py --experiment-root ../admission-control
  python pipeline/deploy.py  --experiment-root ../admission-control
  ```

- [ ] **Step 3: Insert a new `## cluster.py` section between `## Running with an Experiment Repo` and `## setup.py`**

  After the line that reads `pipeline/pipeline.yaml is the static Tekton Pipeline definition...` (currently line 31), and before the `---` separator at line 33, insert:

  ```markdown

  ---

  ## cluster.py

  Cluster-side bootstrap. Run once per cluster, before any per-workspace or per-run commands. Idempotent — safe to re-run when adding namespace slots or rotating secrets.

  ```bash
  python pipeline/cluster.py provision <cluster_id> --namespaces NS1,NS2,... [flags]
  ```

  | Flag | Env var | Default |
  |------|---------|---------|
  | `--namespaces NS1,NS2,...` | — | required — slot namespaces to provision |
  | `--storage-class SC` | — | cluster default |
  | `--hf-token TOKEN` | `HF_TOKEN` | prompt |
  | `--github-token TOKEN` | `GITHUB_TOKEN` | optional |
  | `--registry-user USER` | `REGISTRY_USER` | prompt |
  | `--registry-token TOKEN` | `REGISTRY_TOKEN` | prompt |
  | `--dockerhub-user USER` | `DOCKERHUB_USER` | optional |
  | `--dockerhub-token TOKEN` | `DOCKERHUB_TOKEN` | optional |
  | `--experiment-root PATH` | — | cwd |

  **Output:** `workspace/clusters/<cluster_id>/cluster_config.json` records:

  - `namespaces` — the provisioned slot list
  - `is_openshift` — detected cluster flavor
  - `storage_class` — PVC storage class
  - `hf_secret_name` — name of the Secret holding the HF token
  - `workspaces` — PVC bindings (`data-pvc`, `source-pvc`)
  - `secret_names` — names of registry/github/dockerhub Secrets
  - `created_at` — first-write timestamp

  **What it provisions per namespace:** namespace, RBAC bindings, Secrets (HF, registry, GitHub, Docker Hub), PVCs (data, source), Tekton tasks, and the cluster-wide Pipeline definition. Re-runs reconcile via `kubectl apply` — drift is overwritten.

  **Boundary with `setup.py`:** anything operator-side (registry choice, repo name, current run, orchestrator image, pipeline_yaml path, sim2real_root) belongs in `setup.py` and lands in `setup_config.json`. Anything cluster-side (namespaces, RBAC, secrets, PVCs, Tekton tasks) belongs in `cluster.py provision` and lands in `cluster_config.json`. The two never write the same file.

  ```

- [ ] **Step 4: Update the deploy.py source-of-truth sentence (line 110)**

  OLD: `Ensures all scenario images exist and orchestrates PipelineRun execution across namespace slots. Operates independently of `transfer.yaml` — driven by workspace files and `setup_config.json`.`

  NEW: `Ensures all scenario images exist and orchestrates PipelineRun execution across namespace slots. Operates independently of `transfer.yaml` — driven by workspace files, `setup_config.json` (workspace-scoped), and `clusters/<id>/cluster_config.json` (namespaces, PVCs, secrets).`

- [ ] **Step 5: Apply design's wording for the slot-add line (line 171)**

  OLD: `The safe way to add a slot is `cluster.py provision <cluster_id> --namespaces NS1,NS2,NS3` (provisions before publishing the change).`

  NEW: `The safe way to add a slot is `cluster.py provision <cluster_id> --namespaces NS1,NS2,NS3` (re-run with the new full list; provisions before publishing the change). When issue #377 lands, `deploy.py slots add NS` will be the operator-friendly form.`

- [ ] **Step 6: Append a `## Pipeline library` section after `## run.py` (before `## <experiment-repo>/transfer.yaml`)**

  Find the line `---` immediately before `## <experiment-repo>/transfer.yaml` (currently around line 257). Insert before that separator:

  ```markdown

  ---

  ## Pipeline library (`pipeline/lib/`)

  | Module | Purpose |
  |--------|---------|
  | `manifest.py` | Loads and validates `transfer.yaml` (v3 schema) |
  | `state_machine.py` | Phase tracking with atomic JSON persistence (`.state.json`) |
  | `context_builder.py` | Assembles context document, caches by SHA-256 hash |
  | `values.py` | Deep-merge utility used by `assemble.py` |
  | `assemble.py` | Scenario assembly: deep-merges bundles + overlays into resolved scenarios |
  | `tekton.py` | Generates PipelineRun YAMLs |
  | `pod_pending.py` | Classifies pod scheduling failures (recoverable vs not) |
  | `run_manager.py` | `list_runs`, `inspect_run`, `switch_run` logic |
  | `remote.py` | ConfigMap + Job generation for `deploy.py run --remote` |
  | `capacity.py` | Cluster GPU capacity probe (taint/cordon/product filter) |
  | `cluster_ops.py` | Cluster-side primitives: read/write/update `cluster_config.json`, `provision_namespace`, `apply_cluster_resources`, `detect_openshift` |
  | `layout.py` | Workspace path helpers (`workspace_dir`, `cluster_dir`, `cluster_config_path`, `runs_dir`, `setup_config_path`) |
  | `slicer.py` | Splits `transfer.yaml` into translation-slice vs assembly-slice + computes `translation_hash` |

  ```

- [ ] **Step 7: Append a `## Workspace artifacts` section right after `## Pipeline library`**

  Continue the insertion from Step 6 (in the same block before the existing `---` / `## <experiment-repo>/transfer.yaml`):

  ```markdown

  ---

  ## Workspace artifacts

  All artifacts live under `<experiment-root>/workspace/` (gitignored). Key files:

  | File | Written by | Read by |
  |------|-----------|---------|
  | `setup_config.json` (workspace fields: registry, repo_name, current_run, orchestrator_image, pipeline_yaml, sim2real_root) | `setup.py` | `prepare.py`, `deploy.py`, `run.py` |
  | `clusters/<id>/cluster_config.json` (cluster fields: namespaces, is_openshift, storage_class, hf_secret_name, workspaces, secret_names) | `cluster.py provision` | `deploy.py`, `prepare.py`, `lib/remote.py`, `lib/run_manager.py` |
  | `runs/<run>/.state.json` | `prepare.py` | `prepare.py`, `deploy.py` |
  | `runs/<run>/run_metadata.json` | `setup.py`, `deploy.py` | `deploy.py`, `run.py` |
  | `runs/<run>/skill_input.json` | `prepare.py` Phase 3 | `/sim2real-translate` skill |
  | `runs/<run>/translation_output.json` | `prepare.py` Phase 3 (index) | `prepare.py` Phase 4, `deploy.py`, `run.py` |
  | `runs/<run>/generated/…` | `/sim2real-translate` skill | `prepare.py` Phase 4 |
  | `runs/<run>/cluster/…` | `prepare.py` Phase 4 | `deploy.py` |
  | `runs/<run>/run_summary.md` | `prepare.py` Phase 5 | human review |
  | `runs/<run>/results/{phase}/` | `deploy.py collect` | `/sim2real-analyze` skill, `deploy.py wipe` |
  | ConfigMap `sim2real-progress-{run}` | `deploy.py run`, `deploy.py reset` | all `deploy.py` subcommands |

  See `CLAUDE.md`'s Workspace Artifacts table for the per-file producer/consumer breakdown.

  ```

- [ ] **Step 8: Verify the result**

  ```bash
  git diff pipeline/README.md | wc -l
  grep -n "cluster.py\|cluster_config" pipeline/README.md
  ```

  Spot-check:
  - Top-of-file flow diagram shows both phases.
  - `## cluster.py` section exists between `## Running with an Experiment Repo` and `## setup.py`.
  - `## Pipeline library` and `## Workspace artifacts` sections exist before the transfer.yaml section.
  - Line about `setup_config.json` for deploy.py also names `cluster_config.json`.

- [ ] **Step 9: Commit**

  ```bash
  git add pipeline/README.md
  git commit -m "docs(pipeline/README): document cluster.py + Step 0 workspace layout"
  ```

---

## Task 4: Verify acceptance criteria + sweep

**Files:**
- No new modifications expected. This task is a verification pass.

- [ ] **Step 1: Run pytest locally**

  ```bash
  PYTHONPATH=. python3 -m pytest pipeline/ .claude/skills/sim2real-analyze/tests/ .claude/skills/sim2real-bootstrap/tests/ .claude/skills/sim2real-translate/tests/ -v 2>&1 | tail -30
  ```

  Expected: all pass.

- [ ] **Step 2: Lint**

  ```bash
  ruff check pipeline/ .claude/skills/ --select F 2>&1 | tail -5
  ```

  Expected: clean (no errors).

- [ ] **Step 3: Walk through the issue acceptance checklist**

  Verify each box from the issue's "Acceptance" section:
  - [x] `.github/workflows/test.yml` includes new test paths
  - [x] `pipeline/README.md` Getting-Started narrative reframed (top-of-file two-phase block)
  - [x] `pipeline/README.md` has new `## cluster.py` section
  - [x] `pipeline/README.md` `## setup.py` trimmed (was done by #436; verify still trim)
  - [x] All line-level patches per design's table applied (or marked no-op)
  - [x] Workspace artifact tables include `cluster_config.json` (both files)
  - [x] CI passes locally
  - [x] No updates to `docs/troubleshooting.md` or skill MD files (confirm via `git diff --name-only`)

- [ ] **Step 4: Stale-reference sweep**

  Grep for references that the docs change might invalidate:

  ```bash
  # Anywhere outside this PR's files that still claims setup.py bootstraps namespaces/RBAC/PVCs?
  grep -rn "setup.py.*namespaces\|setup.py.*--namespaces\|setup.py.*--no-cluster" \
    --include='*.md' --include='*.py' --include='*.yaml' --include='*.yml' \
    . 2>/dev/null | grep -v '^./docs/proposals/\|^./docs/epics/'

  # Anywhere claiming setup_config.json holds namespaces/is_openshift/storage_class?
  grep -rn "setup_config.json.*namespaces\|setup_config.*is_openshift\|setup_config.*storage_class" \
    --include='*.md' --include='*.py' . 2>/dev/null | grep -v '^./docs/proposals/\|^./docs/epics/'
  ```

  Triage: each hit is either updated in this PR, accurate (workspace-scoped reference), or in an out-of-scope source (`docs/proposals/`, `docs/epics/` — historical record of the design itself).

- [ ] **Step 5: Confirm worktree-only changes**

  ```bash
  git status
  git -C /Users/kalantar/projects/go.workspace/src/github.com/inference-sim/sim2real status
  ```

  Expected: worktree shows three modified files (`.github/workflows/test.yml`, `CLAUDE.md`, `pipeline/README.md`) plus one new file (this plan). Parent repo shows no unintended modifications.

---

## Task 5: Push + open PR

- [ ] **Step 1: Push the branch**

  ```bash
  git push -u origin refactor/v2-step-0-issue-425-docs-update
  ```

- [ ] **Step 2: Open PR against `refactor/v2-step-0`**

  ```bash
  gh pr create --base refactor/v2-step-0 --title "docs: update README, CLAUDE.md, CI for the new Step 0 flow (#425)" --body-file <(cat <<'EOF'
  Closes #425.

  Aligns `pipeline/README.md`, `CLAUDE.md`, and `.github/workflows/test.yml` with the post-Step-0 split between `cluster.py provision` (cluster bootstrap) and `setup.py` (workspace config).

  ## Summary

  - **`pipeline/README.md`** — two-phase flow at top of file; new `## cluster.py` section documenting flags, output, and the cluster/workspace boundary; new `## Pipeline library` and `## Workspace artifacts` tables (includes `cluster_config.json` row); `deploy.py` source-of-truth sentence now names `cluster_config.json:namespaces`; slot-add wording updated with the design's exact phrasing (incl. issue #377 forward-pointer).
  - **`CLAUDE.md`** — pipeline-flow diagrams (Project Overview + Stage Contracts) updated to show `cluster.py provision` as one-time-per-cluster prerequisite; invocation example adds cluster.py; setup.py and deploy.py descriptions revised; `pipeline/cluster.py` paragraph reworded to steady-state; pipeline/lib module table extended with `cluster_ops`, `layout`, `slicer`; workspace artifact table gains a `cluster_config.json` row and the `setup_config.json` row now names its workspace-only fields.
  - **`.github/workflows/test.yml`** — pytest invocation explicitly pins Step 0 test files (`test_layout.py`, `test_cluster_ops.py`, `test_cluster_py.py`, `test_slicer.py`) per design intent. `pipeline/` glob already covers them; this makes the introduction visible.

  Several rows in the design's per-row patch table were already applied by earlier Step 0 PRs (#427–#436) — README line 31's "applied by cluster.py provision", line 60's `--no-cluster` removal, line 171's slot-add language, line 331's parallel-pool intro. This PR skips those rows; the commit message and plan note them as no-ops.

  ## Out of scope

  Per the design's "Documentation Updates" section, this PR does not touch `docs/troubleshooting.md` (verified zero matches for cluster fields) or any `.claude/skills/sim2real-*/SKILL.md` (those rewrites are owned by Steps 2–4).

  ## Sweep

  Grepped `*.md`, `*.py`, `*.yaml`, `*.yml` for stale references to `setup.py --namespaces`, `setup.py --no-cluster`, and cluster fields in `setup_config.json`. Only hits outside this PR are in `docs/proposals/` and `docs/epics/step-0/` — historical design records, intentionally preserved.

  ## Base

  Targets `refactor/v2-step-0` (epic #416 child).
  EOF
  )
  ```

  If `gh` fails with token error, retry with `unset GITHUB_TOKEN GH_TOKEN; gh pr create …`.

- [ ] **Step 3: Surface the PR URL**

  Capture the URL printed by `gh pr create` and report it.

---

## Self-review

**Spec coverage:**
- ☑ CI test paths — Task 1
- ☑ README narrative rewrite — Task 3 step 1
- ☑ README ## cluster.py section — Task 3 step 3
- ☑ README ## setup.py trimmed — confirmed pre-applied; verification in Task 4
- ☑ Line-level patches per design — Task 2 (CLAUDE.md) + Task 3 (README)
- ☑ Workspace artifact tables include cluster_config.json — Task 2 step 1 #8 (CLAUDE.md) + Task 3 step 7 (README)
- ☑ CI passes locally — Task 4 step 1
- ☑ No update to troubleshooting.md / skill MD — Task 4 step 3

**Placeholder scan:** none. Every step has exact old/new text or commands.

**Type consistency:** N/A (no code).
