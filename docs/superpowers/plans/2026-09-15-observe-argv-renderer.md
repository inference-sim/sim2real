# Observe argv Renderer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render the whole `blis observe` command line at assemble time as one `observeArgs` PipelineRun param, so the effective command is readable from the generated PipelineRun and exists in exactly one place.

**Architecture:** A new `pipeline/lib/observe_argv.py` owns the argv: the flag table, the three mutual exclusions, and value validation. `tekton.py` calls it and emits one param. `pipeline.yaml` drops the seven forwarded scalars plus `model` and gains `observeArgs`. `corpus_schema.REPLAY_FIELDS` stops naming PipelineRun params and names CLI flags instead, because those params cease to exist.

**Tech Stack:** Python >= 3.12, PyYAML, pytest.

**Spec:** GitHub issue inference-sim/sim2real#900 **plus its two comments, which supersede parts of the body** — see Global Constraints. Paired with inference-sim/tektonc-data-collection#70 (PR #72).

## Global Constraints

- Python >= 3.12. CI: `ruff check pipeline/ .claude/skills/ --select F`, then pytest with `--cov=pipeline --cov-fail-under=90`.
- **The issue body's "Scope note — what this does NOT do" is SUPERSEDED** by its second comment. The four previously-unsettable flags ARE folded in as `blis_observe` keys. The author is editing the body; do not follow it.
- **Author rulings (running-traces), all binding:**
  - Key is named **`detectors`**, not `postHocDetector` — the flag `--post-hoc-detector` was renamed to `--detectors` upstream and no longer exists.
  - `detectors: none` is **rejected**. blis's vocabulary for off is *empty*; accepting `none` and translating creates a bundle dialect that diverges from the tool.
  - AC-1 means "reproduces today's **intended** command with `--post-hoc-detector` corrected to `--detectors`". The literal current argv is a parse failure and must not be encoded in a passing test.
  - Per-key type rules in the `blis_observe` allowlist; do not relax the bool guard globally.
  - **Three** exclusions centralized in the renderer, not one.
  - **Emit always** — do not suppress a flag at its default. Explicit emission risks flag-*name* drift, which fails loudly at parse; suppressing risks *default* drift, which changes measurements silently.
- **`--post-hoc-detector` is dead at the pin.** Evidence: zero `post-hoc` hits in inference-sim Go; `"detectors"` registered at `cmd/saturation.go:35` default `""`; `registerDetectorFlags(observeCmd)` at `cmd/observe_cmd.go:200`; roster `{composite, threshold, backlog-drift, peak-rate}` at `sim/saturation/bank.go:18`; rename `18c2c926` (2026-08-06) is an ancestor of pin `602ae462` (2026-09-14) but not of prior pin `583f7195` — so sim2real #904 introduced the break.
- **Task-side contract (tektonc PR #72), verified by reading the diff.** The Task declares exactly `endpoint`, `observeArgs`, `workloadSpec`, `resultsDir`. `observeArgs` has no default. `--server-url` is appended by the Task. `model` is gone from the Task entirely, so `--model` is ours. Two paths the Task owns and we must match: the `data` workspace mounts at `/workspace/data`, and `write-workload-spec` writes to `/workspace/workload.yaml`.
- **Value validation is ours alone**, because Tekton substitutes textually and the Task word-splits. Reject in any rendered scalar: whitespace and `" ' $ ` ; | & > < \` and newline. `extraArgs` is exempt from the whitespace rule (multi-word is its purpose) but NOT from the metacharacter rule.
- **Do NOT reject glob characters** `* ? [`. The Task adds `set -f`, which disables pathname expansion while keeping word-splitting — verified: unquoted `${A}` with `A="--spec globtest_*.yaml"` yields 3 args, and `set -f` yields 2 verbatim. Record the dependency in a comment so anyone removing `set -f` sees what relied on it.
- **Submodule bump is the FINAL commit**, made only after tektonc PR #72 merges. `pipeline.yaml` removing the forwarded scalars is valid only against the new Task; either half alone is a Tekton admission error at PipelineRun creation.

## File Structure

| File | Responsibility |
|---|---|
| **Create** `pipeline/lib/observe_argv.py` | The argv: `OBSERVE_FLAGS` table, `render_observe_argv()`, the three exclusions, scalar validation. No Tekton or YAML knowledge. |
| **Modify** `pipeline/lib/tekton.py` | Call the renderer; emit one `observeArgs` param; stop emitting the nine absorbed scalars and `model`. Delete `_OBSERVE_PARAM_ORDER`. |
| **Modify** `pipeline/lib/corpus_schema.py` | `REPLAY_FIELDS` entries name a CLI **flag**, not a PipelineRun param, since `concurrentSessions`/`totalSessions` cease to exist. |
| **Modify** `pipeline/lib/manifest.py` | Add `detectors`, `apiFormat`, `recordItl`, `streaming` to the `blis_observe` allowlist with per-key type rules. |
| **Modify** `pipeline/pipeline.yaml` | Add `observeArgs`; delete 8 top-level params; rewire the observe block to 4. |
| **Modify** `.claude/skills/sim2real-bootstrap/generate_from_config.py` | `--detectors`/`--api-format`/`--record-itl`/`--no-streaming` become tuning flags; remove stale `--post-hoc-detector` from `:183` and `:210`. |
| **Create** `pipeline/tests/test_observe_argv.py` | Renderer unit tests incl. the AC-1 golden argv and all three exclusions. |
| **Modify** `pipeline/tests/{test_tekton,test_corpus_schema,test_manifest,test_pipeline_yaml}.py` | Param-set changes and the schema flag rename. |
| **Modify** `pipeline/README.md`, `CLAUDE.md` | Document `observeArgs`, the new keys, and the renderer module. |

**Param disposition, derived by scanning every `$(params.X)` reference per task rather than from the issue's list:**

| Param | Consumers | Action |
|---|---|---|
| `maxConcurrency`, `timeout`, `warmupRequests`, `prewarmDuration`, `extraArgs`, `concurrentSessions`, `totalSessions` | observe only | **delete top-level** |
| `model` | observe only | **delete top-level** and stop emitting from `tekton.py` (the renderer carries `--model`) |
| `tracePath` | prepare-trace **and** observe | **keep top-level**, drop from the observe block only |
| `workloadSpec` | observe only | **keep** — the new Task still takes it as file content |
| `runName`, `phase`, `workloadName`, `replica` | 6-7 tasks each | keep; reach observe only via `resultsDir` |
| `experimentId`, `skipTeardown` | **no task** | pre-existing dead params — OUT OF SCOPE, note in the PR |

---

### Task 1: The renderer

**Files:** Create `pipeline/lib/observe_argv.py`; Test `pipeline/tests/test_observe_argv.py`

**Interfaces:**
- Consumes: `corpus_schema.{CORPUS_FIELDS,REPLAY_FIELDS}` (read-only), `tekton.build_results_dir` (import from `tekton` would cycle — pass the rendered `results_dir` string in as an argument instead).
- Produces:
  - `render_observe_argv(*, workload, observe, model, results_dir, trace_path) -> str`
  - `ObserveArgvError(AssembleError)`
  - `OBSERVE_FLAGS: dict[str, _Flag]` — `blis_observe` key → flag name, default, renderer, validator.
  - `FORBIDDEN_CHARS: str`

- [ ] **Step 1: Write the failing tests**

Cover, at minimum:
- **AC-1 golden argv, synthetic cell.** With all-default `blis_observe`, the rendered argv equals exactly (order fixed):
  `--max-concurrency 10000 --timeout 1800 --prewarm-duration 60s --warmup-requests 50 --detectors composite --api-format completions --model <model> --workload-spec /workspace/workload.yaml --trace-header /workspace/data/<rd>/trace_header.yaml --trace-data /workspace/data/<rd>/trace_data.csv --saturation-report /workspace/data/<rd>/saturation.json`
  i.e. today's intended command with `--post-hoc-detector`→`--detectors`, plus explicit `--api-format`, minus `--server-url`, and with `--record-itl`/`--no-streaming` absent at their defaults.
- **AC-1 golden argv, corpus cell.** `--workload-spec` replaced by `--corpus-header /workspace/data/<tracePath>.yaml --corpus-data /workspace/data/<tracePath>.csv --concurrent-sessions N --total-sessions N`.
- **AC-4:** `--server-url` never appears, for either cell kind.
- **`extraArgs` renders last**, after every other flag, and is passed through as multiple words.
- **Exclusion 1:** a workload that is both a corpus document and carries spec-mode inputs raises.
- **Exclusion 2:** `recordItl: true` + `streaming: false` raises, naming both keys.
- **Exclusion 3:** `detectors: ""` while a saturation report would be emitted raises (or suppresses both — pick emit-both-or-neither and test it).
- **`detectors: none` rejected**, message naming the roster.
- **`detectors` validated against the roster**, comma-lists and `all` accepted.
- **`streaming: true` emits nothing; `streaming: false` emits `--no-streaming`** (inversion).
- **`recordItl: true` emits `--record-itl`; false emits nothing.**
- **Metacharacter rejection** for each of `" ' $ \` ; | & > < \` and newline, in a scalar and in `extraArgs`.
- **Whitespace rejected in a scalar but allowed in `extraArgs`.**
- **Glob characters are NOT rejected** — a test asserting `*` passes validation, with a comment naming the Task's `set -f` as the reason.

- [ ] **Step 2: Run to verify failure** — `No module named 'pipeline.lib.observe_argv'`.

- [ ] **Step 3: Implement.** Flag order is fixed and must match the golden test. Validation runs on every rendered scalar before assembly. The three exclusions are checked before any rendering so the error names the cause, not a symptom.

- [ ] **Step 4: Run to verify pass.**

- [ ] **Step 5: Commit.**

---

### Task 2: Allowlist keys with per-key types

**Files:** Modify `pipeline/lib/manifest.py:262-278`; Test `pipeline/tests/test_manifest.py`

- [ ] **Step 1: Write failing tests** — `detectors: composite`, `apiFormat: chat`, `recordItl: true`, `streaming: false` all accepted; `maxConcurrency: true` still rejected (the bool guard's original intent); `apiFormat: rest` rejected; `detectors: none` rejected; unknown key still rejected and the message lists all nine valid keys.

- [ ] **Step 2-4:** Replace the blanket scalar predicate with a per-key type/validity map. Keep the rule that YAML `true` must never satisfy an int field.

- [ ] **Step 5: Commit.**

---

### Task 3: `corpus_schema` replay fields name flags, not params

**Files:** Modify `pipeline/lib/corpus_schema.py`; Test `pipeline/tests/test_corpus_schema.py`

`REPLAY_FIELDS[*].param` currently names `concurrentSessions`/`totalSessions`, which this change deletes. Rename the attribute to `flag` for replay entries (`--concurrent-sessions`, `--total-sessions`) and update `test_every_schema_param_is_declared_in_pipeline_yaml` to assert only `CORPUS_FIELDS` params are declared — replay fields are now argv, verified by Task 1's golden test instead.

- [ ] **Steps 1-5:** as above. This is the tripwire from #901 firing as designed; do not weaken the test, re-point it.

---

### Task 4: `pipeline.yaml` rewiring

**Files:** Modify `pipeline/pipeline.yaml`; Test `pipeline/tests/test_pipeline_yaml.py`

- [ ] **Step 1:** Test that the observe task's params block is exactly `{endpoint, observeArgs, workloadSpec, resultsDir}`; that the eight deleted params are absent from the top-level block; that `tracePath` remains top-level (prepare-trace needs it); and that no param is forwarded to any task that the corresponding Task does not declare.
- [ ] **Steps 2-4:** Add `observeArgs`, delete the eight, rewire the block.
- [ ] **Step 5: Commit.**

---

### Task 5: Bootstrap skill — the four flags become tuning, stale name removed

**Files:** Modify `.claude/skills/sim2real-bootstrap/generate_from_config.py`; Test its `tests/`

- [ ] Add to `OBSERVE_TUNING_FLAGS`: `--detectors`→`detectors`, `--api-format`→`apiFormat`, `--record-itl`→`recordItl`, `--no-streaming`→`streaming` (**inverted**: presence means `streaming: false`).
- [ ] Remove `--post-hoc-detector` from `OBSERVE_PIPELINE_INJECTED_FLAGS:183` and `OBSERVE_VALID_FLAGS:210`; add `--detectors` to the valid set. **Both current entries fail silently** — an allowlist miss drops the flag, and an injected-set miss makes it leak into `extraArgs` and land twice.
- [ ] Tests for each, including that a `config.md` naming `--detectors` reaches `blis_observe.detectors` rather than `extraArgs`.

---

### Task 6: Docs, sweep, verification

- [ ] `pipeline/README.md`: document `observeArgs`, the nine `blis_observe` keys with defaults, the three exclusions, and the two Task-owned paths.
- [ ] `CLAUDE.md`: add `observe_argv.py` to the Pipeline Library table.
- [ ] Sweep: `grep -rn "post-hoc-detector\|maxConcurrency\|concurrentSessions\|_OBSERVE_PARAM_ORDER"` across `*.md`, `*.py`, `docs/`, `.claude/skills/`; triage every hit.
- [ ] `ruff check pipeline/ .claude/skills/ --select F`; full CI pytest with coverage.
- [ ] Confirm the parent repo shows no modifications (`git status` in worktree only).

---

### Task 7: Submodule bump — ONLY after tektonc PR #72 merges

- [ ] Confirm #72 merged; `git -C tektonc-data-collection fetch && checkout origin/main`.
- [ ] Verify the merged Task declares exactly the four params and that `pipeline.yaml` forwards nothing it lacks — reuse the check from #901's `test_pipeline_forwards_no_param_the_pinned_task_rejects`, extended to the observe task.
- [ ] Re-run full CI. Commit as `chore(deps)`.

## Self-Review

**Spec coverage.** AC-1 → Task 1's two golden tests. AC-2 → Task 4's param-set tests. AC-3 → Task 1 exclusion 1. AC-4 → Task 1's `--server-url` test. The comment's four folded flags → Tasks 2 and 5. The comment's `detectors` correction → Global Constraints, Tasks 1, 2, 5.

**Deviations from the issue, deliberate:** key named `detectors` not `postHocDetector`; `model` deleted from `tekton.py`'s emission as well as `pipeline.yaml`, since the renderer carries it; `tracePath` kept top-level (the issue's list implies deleting it, but `prepare-trace` consumes it — verified by per-task scan); three exclusions not one.

**Placeholder scan.** Task 1's test list is specified as behaviors rather than literal code because the golden argv strings depend on `build_results_dir` output; each bullet names the exact expected flags. Tasks 2-6 name exact files and line numbers.

**Risk for the reviewer:** the argv's flag ORDER is now load-bearing for the golden tests but irrelevant to blis. If a reviewer objects to asserting order, the alternative is comparing as a multiset with `extraArgs` position pinned separately — worth stating in the PR rather than defending silently.
