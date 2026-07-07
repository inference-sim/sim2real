# step-5 PR 6: Docs Update Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Document step-5's shipped behavior in `pipeline/README.md`, cross-check `.claude/skills/sim2real-check/SKILL.md` against the README, and audit `CLAUDE.md` for stale pair-key references.

**Architecture:** Pure documentation change. Read the shipped code (PRs 1–5: #509 pairkey.py, #510 assemble replicas, #511 pipeline.yaml threading + validator, #512 --iteration filter, #513 sim2real-check iN/ traversal) and reflect it in the README. SKILL.md was already updated by PR 5 — this PR verifies README ↔ SKILL agreement. CLAUDE.md grep-audit likely no-op except workspace-artifact table already touched in PR 3.

**Tech Stack:** Markdown. No code changes.

## Global Constraints

- No new docs. No `docs/refactor/replicas.md` or similar — the proposal (`docs/proposals/replicas-as-pair-keys.md`) remains the canonical model.
- Every new CLI flag/argument from PR 1–5 must have a matching entry in `pipeline/README.md`.
- README example snippets must execute against the merged PR 1–5 code (spot-check).
- No stale pair-key references remain in `CLAUDE.md` (grep audit passes).
- SKILL.md and README describe the check output format identically — if they diverge, README conforms to SKILL.md (SKILL.md is the canonical spec of the check output shape, written in PR 5).
- Base branch: `refactor/v2-step-5`. PR merges back to it, not `main`.

---

### Task 1: pipeline/README.md — "Assemble a run" grows for `--replicas`

**Files:**
- Modify: `pipeline/README.md:245-304` (the `## Assemble a run` section)

**Interfaces:**
- Consumes: Behavior of `sim2real assemble --replicas N` from `pipeline/lib/assemble_run.py:678-899` (main path), `pipeline/lib/assemble_run.py:743-808` (existing-run decision tree), `pipeline/lib/assemble_run.py:603-671` (`_additive_grow`), and the argparse definition at `pipeline/sim2real.py:473-479`.
- Produces: README reflects (a) `--replicas` flag row in the command signature; (b) new "Replica model + additive-merge semantics" subsection documenting grow-only, legacy-run guard, drift-check ordering, no-op invariant, `--force` scope; (c) updated `runs/<R>/` outputs table to reflect `manifest.assembly.yaml` carries `replicas: N`, `run_metadata.json` carries `replicas` field, and PipelineRun filenames use `|<package>|iN.yaml` shape.

- [ ] **Step 1: Update the command signature block (~line 250)**

Change:
```bash
python pipeline/sim2real.py assemble \
    --translation REF \
    --cluster CLUSTER_ID \
    --run RUN_NAME \
    [--force]
```
to:
```bash
python pipeline/sim2real.py assemble \
    --translation REF \
    --cluster CLUSTER_ID \
    --run RUN_NAME \
    [--replicas N] \
    [--force]
```

- [ ] **Step 2: Update the "Outputs written" table (~line 275)**

`manifest.assembly.yaml` row: add "with a leading `replicas: N` field" (the top-level `replicas` counter alongside the slice). `run_metadata.json` row: extend the schema list from `{version, run_name, translation_hash, cluster_id, params_hash, image_tag, assembled_at}` to include `replicas`. PipelineRun-manifest row: change filename shape from `pipelinerun-<workload>-<package>.yaml` to `pipelinerun-<workload>|<package>|iN.yaml` and change "One PipelineRun per (workload, package)" to "One PipelineRun per (workload, package, iteration) tuple".

- [ ] **Step 3: Insert new "Replica model + additive-merge semantics" subsection (immediately after the Assembly-formula block, ~line 292)**

Verbatim body to insert (headed by `**Replicas.**`):

```markdown
**Replicas.** `--replicas N` (default 1) is the number of iterations per (workload, package) pair. Each iteration gets its own PipelineRun (`{phase}-{workload}-{run}-i{N}`) and its own results subdirectory (`results/<phase>/<workload>/iN/`). `manifest.assembly.yaml` carries a top-level `replicas: N`, and `run_metadata.json` carries the same value as a schema field. Both are set on every assemble.

**Additive-merge (grow-only).** Re-assembling an existing run with `--replicas` interacts with the prior state as follows:

- `N == prior_replicas` — true no-op. No files rewritten; the assemble returns silently.
- `N > prior_replicas` — additive grow. Existing PipelineRun files (`i1..i{prior}`) are preserved byte-for-byte and by mtime; new files are emitted for `i{prior+1}..iN`. `manifest.assembly.yaml` and `run_metadata.json` are rewritten with the new `replicas` count; `params_hash` is preserved (drift check passed).
- `N < prior_replicas` — refused with `run '<name>' already has <prior> replicas; refusing to shrink to <N>. Replica shrink is tracked in #506.` This guard runs BEFORE the drift check, so `--force` does NOT bypass it.

Two invariants shape the grow-only path:

- **Drift check.** The current assembly-slice content hash is compared against the run's recorded `params_hash`. Any mismatch refuses the assemble unless `--force` is passed — with `--force`, the whole run directory is rebuilt from scratch (existing `iN/` files are lost). Without `--force`, matching hashes are required to reach the additive-grow branch.
- **Legacy-run guard.** A pre-step-5 run has no `replicas` field in its `manifest.assembly.yaml`. Assembling with `--replicas` refuses this shape unless `--force`. With `--force`, the run is rebuilt from scratch as a fresh replica-shaped run.

**PipelineRun name length.** `metadata.name` is `{phase}-{workload}-{run}-i{iteration}` (with `_` → `-` normalization). This is a Kubernetes DNS subdomain, so the 253-char RFC 1123 limit applies. `assemble` validates each generated PipelineRun name and exits 2 with `error: PipelineRun name '<name>' is <len> chars, exceeds the 253-char DNS subdomain limit` if any pair (phase × workload × run × iteration) would overflow. Fail-fast at assemble time is preferable to Tekton admission rejection at dispatch time.
```

- [ ] **Step 4: Commit**

```bash
git add pipeline/README.md
git commit -m "docs(readme): --replicas + additive-merge semantics on assemble"
```

---

### Task 2: pipeline/README.md — Pair-key grammar section

**Files:**
- Modify: `pipeline/README.md:322-326` (the current "Pair discovery" one-liner just below the `deploy.py` common-flags table)

**Interfaces:**
- Consumes: Grammar from `pipeline/lib/pairkey.py:1-25` module docstring + `pipeline/lib/pairkey.py:36-40` regex + `parse_iteration_spec` semantics from `pipeline/lib/pairkey.py:88-133`.
- Produces: A "Pair keys" subsection under `## deploy.py` that names the grammar, notes the legacy no-suffix shape, calls out metadata-key filtering, and cross-references `--only` / `--workload` / `--package` / `--iteration` scoping.

- [ ] **Step 1: Replace the single-line "Pair discovery" paragraph at line 324 with a "Pair keys" subsection**

Replace:
```markdown
**Pair discovery** — `deploy.py run` discovers `pipelinerun-*.yaml` files at the `cluster/` root. Each file's pair key is derived as `wl-` + filename stem minus the `pipelinerun-` prefix.
```

With:
```markdown
**Pair keys.** A pair key names a `(workload, package, iteration)` triple in the ConfigMap and on disk. Canonical grammar:

```
pair_key := "wl-" workload "|" package "|" iter
workload := [a-z0-9]([a-z0-9-]*[a-z0-9])?    # kebab-case, no leading/trailing hyphen
package  := [a-z0-9]([a-z0-9-]*[a-z0-9])?    # same shape as workload
iter     := "i" [1-9][0-9]*                  # positive decimal, no leading zeros; i0 is invalid
```

Example: `wl-chat-mid|baseline|i1`.

The parser accepts a legacy no-suffix form (`wl-<workload>|<package>`) and reads it as `iteration=1`; canonical renderings always include the `|iN` suffix. Metadata keys (`_meta`, `_notes`, anything starting with `_`) are filtered out upstream via `deploy._is_pair_key` and never reach the parser.

**Pair discovery.** `deploy.py run` discovers `pipelinerun-*.yaml` files at the `cluster/` root. Each file's pair key is derived as `wl-` + filename stem minus the `pipelinerun-` prefix — the assembler names files as `pipelinerun-<workload>|<package>|iN.yaml`, so the pair key falls out directly.

**Scoping flags on filter-aware subcommands** (`run`, `status`, `collect`, `reset`, `wipe`):

| Flag | Scope | Notes |
|------|-------|-------|
| `--only PAIR…` | Full pair keys (with or without `wl-` prefix) | Narrows both workload and package. Takes precedence over `--workload`. |
| `--workload NAME…` | Workload dimension | Multiple values are OR'd within the flag. |
| `--package NAME…` | Package dimension | Multiple values are OR'd within the flag. |
| `--iteration SPEC` | Iteration dimension | Grammar below. |
| `--status STATE` | Progress state (`pending` / `running` / `done` / `failed` / `timed-out` / `stalled`) | Not available on every subcommand — see per-subcommand tables. |

Different flags compose as AND: `--workload X --package baseline --iteration 1,3` narrows to iterations 1 and 3 of workload X's baseline package.
```

- [ ] **Step 2: Commit**

```bash
git add pipeline/README.md
git commit -m "docs(readme): pair-key grammar + scoping flag summary"
```

---

### Task 3: pipeline/README.md — `--iteration` on each filter-aware subcommand

**Files:**
- Modify: `pipeline/README.md:345-360` (deploy.py common-flags + run flag table, and each of status/collect/reset/wipe flag tables).

**Interfaces:**
- Consumes: `--iteration` argparse definition from `pipeline/deploy.py:3593-3610` (and matching entries on other subcommands), plus the `parse_iteration_spec` grammar in `pipeline/lib/pairkey.py:88-133`.
- Produces: An `--iteration SPEC` row in every filter-aware subcommand's flag table (run, status, collect, reset, wipe) plus a one-paragraph "Iteration filter spec" callout under the Pair-keys subsection.

- [ ] **Step 1: Add `--iteration SPEC` row to every filter-aware subcommand's flag table**

For each of the `deploy.py run`, `deploy.py status`, `deploy.py collect`, `deploy.py reset`, `deploy.py wipe` tables, add a row:

```markdown
| `--iteration SPEC` | Scope to iteration(s): `'2'`, `'1,3'`, `'1-3'`, `'1,3-5'` |
```

Place the row after `--package` and before `--status` (where present).

- [ ] **Step 2: Add "Iteration filter spec" paragraph under the Pair-keys subsection (from Task 2)**

Append immediately after the scoping-flags table:

```markdown
**Iteration filter spec.** The `--iteration` value is a comma-separated list of tokens; each token is either a positive integer (`3`) or an inclusive range (`1-3`). Whitespace around commas and hyphens is tolerated. Rejected: `0`, negatives, reversed ranges (`5-1`), non-integer tokens (`abc`), leading zeros (`01`), empty spec, empty token. Malformed specs fail with `malformed iteration spec '<spec>': <reason>` before any pair discovery runs.

Note: legacy pair keys (no `|iN` suffix) parse as iteration `1`, so `--iteration 1` matches them.
```

- [ ] **Step 3: Commit**

```bash
git add pipeline/README.md
git commit -m "docs(readme): --iteration flag entries + spec grammar"
```

---

### Task 4: pipeline/README.md — Sweep stale line-item references

**Files:**
- Modify: `pipeline/README.md` (various — see below).

**Interfaces:**
- Consumes: Post-step-5 filename and path shapes.
- Produces: Every remaining reference to old shapes (hyphen filenames, no-iN paths, or missing `--iteration` in run flag table) is aligned with the new model.

- [ ] **Step 1: Sweep and align — grep for stale substrings**

Run:
```bash
grep -nE "pipelinerun-<workload>-<package>|/<workload>/gpu_logs/|/<workload>/plans/" pipeline/README.md
```

For every hit not already handled by Tasks 1–3, update to the iN-aware shape. The parallel-pool example section (line 704 area, "Parallel Pool Execution") is expected to still enumerate per-pair execution — update its wording to say "one PipelineRun per (workload, package, iteration)" where relevant.

Specific known targets, based on preflight grep:
  - Line 279: `cluster/pipelinerun-<workload>-<package>.yaml` (Task 1 handles this).
  - "One PipelineRun per (workload, package)." (Task 1 handles this).
  - Search: `grep -n "One PipelineRun per\|per (workload, package)" pipeline/README.md` and normalize any residual copies.

- [ ] **Step 2: Cross-check `runs/<R>/results/` path shape**

Search `grep -n "results/<phase>/<workload>/" pipeline/README.md` and confirm every hit either already ends in `/iN/…` or is verbatim quoting a legacy shape in a deliberate contrast paragraph. Update remaining bare `results/<phase>/<workload>/` references to `results/<phase>/<workload>/iN/`.

- [ ] **Step 3: Commit**

```bash
git add pipeline/README.md
git commit -m "docs(readme): sweep stale filename + path shapes"
```

---

### Task 5: SKILL.md ↔ README consistency check

**Files:**
- Read: `.claude/skills/sim2real-check/SKILL.md`
- Modify (only if drift found): `pipeline/README.md` — the additive-merge / iN / pair-key sections written in Tasks 1–3.

**Interfaces:**
- Consumes: SKILL.md's canonical statements about check output format, run-level exit code (0/1/2 contract), iteration-spec grammar (if any), pair-key grammar.
- Produces: Either "no drift found — no changes" (report inline) or a small README patch that reconciles the drift. **README changes conform to SKILL.md**, never the other way — SKILL.md was written last (PR 5) and is the more thorough spec.

- [ ] **Step 1: Compare the pair-key grammar section**

Read SKILL.md around Step 0.5 (its enumerator invocation) and the run-level rollup at the bottom. Grep for `wl-`, `\|i`, `pair_key`, `pair key`, and `iteration` mentions. Compare each substantive statement against the README's Pair-keys subsection.

- [ ] **Step 2: Compare the exit-code / status vocabulary**

Grep SKILL.md for `PASS`, `FAIL`, `WARN`, `SKIP`, `MISSING` and check whether the README has any competing claim about `sim2real-check` output. (Typical result: README doesn't yet describe `sim2real-check` verdict vocabulary at all — no drift possible.)

- [ ] **Step 3: Record findings in the PR body**

If drift found: patch README, commit as `docs(readme): reconcile with sim2real-check SKILL.md`. If no drift: mention "no README/SKILL.md drift found in the consistency-check pass" in the PR body under "What I swept for".

---

### Task 6: CLAUDE.md audit

**Files:**
- Modify: `CLAUDE.md` (only if hits found).

**Interfaces:**
- Consumes: The grep audit's actual matches.
- Produces: Either no changes (audit passes) or narrow edits at each stale hit.

- [ ] **Step 1: Run the audit greps**

```bash
grep -nE "pair-key|pair_key|wl-|\|i[0-9]|replic|iteration" CLAUDE.md || echo "clean"
```

- [ ] **Step 2: Triage each hit**

For each hit, decide: stale (update it in this PR), still accurate (leave it), or unrelated (leave it). Two expected categories:
  - Test-file entries already added by PRs 1, 3, 5 — these are correct and stay.
  - Workspace-artifact table `results/…/iN/…` — already updated by PR 3, verify still correct.
  - Anything else that mentions the old pair-key shape or missing replica flow — update.

- [ ] **Step 3: Commit (if changes)**

```bash
git add CLAUDE.md
git commit -m "docs(claude): reconcile pair-key + replica references"
```

If no changes, note "CLAUDE.md audit clean" in the PR body under "What I swept for".

---

### Task 7: Verify + push + open PR

**Files:**
- None modified. Only shell operations.

**Interfaces:**
- Consumes: Docs edits from Tasks 1–6.
- Produces: A pushed branch and an open PR against `refactor/v2-step-5`.

- [ ] **Step 1: Lint**

```bash
ruff check pipeline/ .claude/skills/ --select F
```

Expected: no errors (this is a docs PR; nothing under `pipeline/` should have changed).

- [ ] **Step 2: Tests**

```bash
python -m pytest pipeline/ \
  pipeline/tests/test_layout.py \
  pipeline/tests/test_cluster_ops.py \
  pipeline/tests/test_cluster_py.py \
  pipeline/tests/test_slicer.py \
  pipeline/tests/test_sim2real.py \
  pipeline/tests/test_assemble_run.py \
  pipeline/tests/test_pairkey.py \
  pipeline/tests/test_load_pairs.py \
  .claude/skills/sim2real-analyze/tests/ \
  .claude/skills/sim2real-bootstrap/tests/ \
  .claude/skills/sim2real-translate/tests/ \
  .claude/skills/sim2real-check/tests/ \
  -v 2>&1 | tail -30
```

Expected: all green. (This PR touches only Markdown; test churn is not expected.)

- [ ] **Step 3: Spot-check one README example snippet**

Pick the assemble command from `## Assemble a run` and verify its flags map to argparse:
```bash
python pipeline/sim2real.py assemble --help 2>&1 | grep -E "replicas|force"
```

Expected: shows both `--replicas N` and `--force`.

- [ ] **Step 4: Confirm no leaks to parent repo**

```bash
git status
git -C /Users/kalantar/projects/go.workspace/src/github.com/inference-sim/sim2real status
```

The worktree should show the docs changes; the parent repo should show only untracked scratch files (`.claude/epic-body-draft.md`, `.claude/epic_state.json`) — no modifications.

- [ ] **Step 5: Push and open PR**

```bash
git push -u origin HEAD
gh pr create --base refactor/v2-step-5 --title "docs: step-5 pair-key + replicas + --iteration + name-validator" \
    --body-file .git/pr-body.md
```

PR body summarizes: what changed in each of the three files, what was swept for (with grep results), acceptance-criteria checklist mapped to the issue.

## Self-Review

**Spec coverage:**
- ✅ Pair-key shape section (Task 2).
- ✅ `--replicas` flag on assemble, grow-only, shrink at #506, legacy-run guard (Task 1).
- ✅ `--iteration` list+range on filter-aware subcommands (Task 3).
- ✅ PipelineRun name length constraint + validator behavior (Task 1, folded into the replica-model section).
- ✅ SKILL.md ↔ README consistency check (Task 5).
- ✅ CLAUDE.md stale-reference audit (Task 6).
- ✅ No new docs (constraint stated in Global Constraints; nothing in Tasks 1–7 creates a new doc file besides this plan).

**Placeholder scan:** None. Every task step has verbatim replacement text.

**Type consistency:** All flag names and argparse metavars match `sim2real.py:473-479` (`--replicas N`) and `deploy.py:3593-3594` (`--iteration SPEC`). Pair-key grammar quoted verbatim from `pairkey.py:1-25`.
