# Design: Refactor prepare.py Stages 3+5 to Use `claude -p`

**Date:** 2026-03-30
**Status:** Draft
**Scope:** `scripts/prepare.py` Stage 3 (Generate) and Stage 5 (Final Review)

---

## Problem

The current `prepare.py` drives the writer/reviewer loops via direct LLM API calls
(`scripts/lib/llm.py`). This has two weaknesses:

1. **Thin reviewer context.** The generate and review prompts pass raw JSON artifacts
   plus a 4000-char slice of the mapping doc. Reviewers receive no understanding of
   how `llm-d-inference-scheduler`'s scorer plugin system works, what `BLIS` simulates,
   or why design decisions (e.g. F-10 double-counting fix) are intentional.

2. **Static writer context.** The writer (claude-opus-4-6 via API) cannot explore the
   actual codebase — it cannot read the `ScorerPlugin` interface definition, discover
   test utilities from source, or verify the registration pattern by reading `register.go`.

**Goal:** Replace the Stage 3 and Stage 5 inner loops with `claude -p` subprocess
invocations so Claude can use file-reading tools to build rich, accurate context.
Context is gathered once per Stage 3 run and persisted on disk, so subsequent
generate/review iterations read one file rather than re-crawling the codebase.

---

## Non-Goals

- Replacing Stages 1–2 (Extract, Translate) — these are deterministic CLI calls.
- Replacing `review_translation.py` or `build_review_request.py` — extended, not replaced.
- Changing the user-facing loop controls (`[a]ccept / [c]ontinue / [q]uit`).
- Changing equivalence gate (Stage 4.5) or Tekton artifact generation.

---

## Architecture

### Overview

```
prepare.py  (thin orchestrator: loop control, build/test, user prompts)
    │
    ├─ stages 1-2: deterministic CLI (unchanged)
    │
    ├─ [PREAMBLE, once per Stage 3 run]
    │     claude -p "read codebase, write prepare_codebase_context.md
    │                               and prepare_reviewer_context.md"
    │     skipped if both files exist and user confirms reuse (--force regenerates)
    │     HALT on failure — no silent fallback
    │
    ├─ outer loop (max 3 final-review retries, unchanged):
    │     ├─ inner loop (generate + review, up to --reviews rounds):
    │     │     ├─ claude -p "generate scorer" (reads prepare_codebase_context.md)
    │     │     │     writes: rounds/N/scorer_snapshot.go, generate_output.json, ...
    │     │     ├─ Python: copy scorer snapshot, then go build/vet/test
    │     │     │     writes: rounds/N/build_output.txt
    │     │     │     if failed: write rounds/N/build_issues.json → continue (no review)
    │     │     └─ claude -p "invoke review_translation.py" (build passed only)
    │     │           reads: prepare_reviewer_context.md
    │     │           calls: review_translation.py --extra-context ...
    │     │           writes: rounds/N/review_output.json, rounds/N/review_issues.json
    │     └─ equivalence gate (unchanged)
    └─ stage 5: final-review/ directory (separate from rounds/)
```

### Subprocess Invocation Pattern

```python
result = subprocess.run(
    ["claude", "-p", prompt, "--output-format", "stream-json"],
    cwd=REPO_ROOT, check=False,
)
```

All paths in prompts are **absolute** (rendered with `str(REPO_ROOT / relative_path)`).

`--output-format stream-json` lets `prepare.py` filter and relay key lines
(file-write events, errors) as `[INFO]` messages. The subprocess writes all
results as files; Python reads those to determine success/failure.

**Failure handling:** If Claude exits non-zero, `prepare.py` checks for the
expected output file (`generate_output.json` or `review_issues.json`). File
absence = subprocess failed → `err()` + `sys.exit(1)`.

---

## Preamble: Context Gathering (Once)

### When it runs

At the start of Stage 3, before the first generate iteration. If both context
files exist in `run_dir/`, Python uses `_should_skip()` to ask whether to reuse.

**When to re-run with `--force`:** Use `--force` if `docs/transfer/blis_to_llmd_mapping.md`
or `llm-d-inference-scheduler/` have changed since the context was built.
The context files contain inline timestamps so users can cross-check.

**Failure:** If the preamble `claude -p` subprocess fails, `prepare.py` HALTs with
an error message. There is no fallback to the old thin-context approach.

### Preamble prompt

```
You are building context documents for the sim2real scorer generation pipeline.
Read the following files and produce two output documents.
All paths are absolute.

## Files to read
- {REPO_ROOT}/llm-d-inference-scheduler/pkg/plugins/scoring.go
- {REPO_ROOT}/llm-d-inference-scheduler/pkg/plugins/scorer/blis_weighted_scoring.go
- {REPO_ROOT}/llm-d-inference-scheduler/pkg/plugins/scorer/blis_weighted_scoring_test.go
- {REPO_ROOT}/llm-d-inference-scheduler/test/utils/  (full directory)
- {REPO_ROOT}/llm-d-inference-scheduler/pkg/plugins/register.go
- {REPO_ROOT}/docs/transfer/scorer_template.go.md
- {REPO_ROOT}/docs/transfer/blis_to_llmd_mapping.md   ← full document, not truncated
- {REPO_ROOT}/blis_router/best/best_program.go        ← EVOLVE-BLOCK source

## Output 1: {run_dir}/prepare_codebase_context.md
For the writer. Begin with: "Generated: {ISO timestamp}"
Include:
- ScorerPlugin interface signature (exact source)
- EndpointMetadata and Metrics field names used in scoring
- Registration pattern from register.go (exact lines)
- Test package conventions (NewTestContext, NewEndpoint usage from test/utils)
- Full scorer template
- Full blis_to_llmd_mapping.md content
- Example scorer annotated with: where interface is satisfied, how signals are accessed

## Output 2: {run_dir}/prepare_reviewer_context.md
For external reviewers (GPT-4o, Gemini). Begin with: "Generated: {ISO timestamp}"
Include everything in Output 1, plus:
- What BLIS simulates (discrete-event LLM inference, what each signal measures)
- Known intentional design decisions reviewers must NOT flag as errors:
    * F-10: BatchSize zeroed; EffectiveLoad = WaitingQueueSize + RunningRequestsSize
    * (any others documented in blis_to_llmd_mapping.md)
- What "translation fidelity" means in this context
```

### Artifacts produced

- `{run_dir}/prepare_codebase_context.md`
- `{run_dir}/prepare_reviewer_context.md`

---

## Stage 3: Round Directory Layout

Each generate+review cycle corresponds to one round number. Rounds are numbered
starting at 1 and increment monotonically within a Stage 3 run. A rerun with
`--force` resets round numbering.

```
{run_dir}/
  prepare_codebase_context.md         ← preamble artifact (shared across all rounds)
  prepare_reviewer_context.md         ← preamble artifact (shared across all rounds)
  rounds/
    1/
      generate_prompt.md              ← exact absolute-path prompt rendered and sent
      generate_output.json            ← {"scorer_name":"...", "scorer_file":"...",
                                          "test_file":"...", "lines":187}
      scorer_snapshot.go              ← copy of scorer, written by Python after generate
      scorer_test_snapshot.go         ← copy of test, written by Python after generate
      build_output.txt                ← combined stdout+stderr from go build/vet/test
      build_issues.json               ← present only if build failed:
                                          {"round":1, "issues":["go build failed: ..."]}
      review_prompt.txt               ← exact argv written before calling script
      review_output.json              ← raw output from review_translation.py
      review_issues.json              ← {"round":1, "issues":["[gpt-4o] Weight mismatch"]}
                                          (empty issues list if consensus reached)
    2/
      ...
```

**Snapshots are written by Python immediately after `stage_generate_iteration`
returns** (before `go build`). This records exactly what was compiled. If build
fails, the snapshot reflects the failing scorer; `build_issues.json` documents
the failure. The next generate round will overwrite the scorer in the scheduler
repo but write a fresh snapshot to `rounds/{N+1}/`.

---

## Stage 3: Generate Iterations

### Iteration input: what the prompt reads

| Source | Path | Notes |
|---|---|---|
| Context | `{run_dir}/prepare_codebase_context.md` | Built once in preamble |
| Algorithm | `{run_dir}/prepare_algorithm_summary.json` | Stage 1 artifact |
| Signals | `{run_dir}/prepare_signal_coverage.json` | Stage 2 artifact |
| Prior issues | `{run_dir}/rounds/{N-1}/review_issues.json` | Round ≥ 2 only |
| Prior build issues | `{run_dir}/rounds/{N-1}/build_issues.json` | If last round had build failure |

Round 1 has no prior-issues input. Round 2+ reads exactly one issues file: if the
previous round had a build failure, read `build_issues.json`; if it reached review,
read `review_issues.json`.

### Generate prompt (rendered per iteration)

```markdown
Read {run_dir}/prepare_codebase_context.md for full system context.
Read {algo_summary_path} for the algorithm to implement.
Read {signal_coverage_path} for production signal access paths.
{if round > 1 and build failed previous round:
  "## Build failure from round {N-1}
   Read {run_dir}/rounds/{N-1}/build_issues.json and fix the reported errors."}
{if round > 1 and review ran previous round:
  "## Reviewer issues from round {N-1}
   Read {run_dir}/rounds/{N-1}/review_issues.json and fix every issue listed."}

Generate the scorer plugin and write:
- Scorer:   {REPO_ROOT}/llm-d-inference-scheduler/pkg/plugins/scorer/<name>.go
- Test:     {REPO_ROOT}/llm-d-inference-scheduler/pkg/plugins/scorer/<name>_test.go
- Register: patch {REPO_ROOT}/llm-d-inference-scheduler/pkg/plugins/register.go
             (existing _ensure_scorer_registered logic applies)

Write {round_dir}/generate_output.json:
  {"scorer_name": "...", "scorer_file": "...", "test_file": "...", "lines": N}
```

**Success signal:** `generate_output.json` exists. Absence = subprocess failed.

### Python actions after generate

```python
scorer_path, test_path = _parse_generate_output(round_dir)
shutil.copy(scorer_path, round_dir / "scorer_snapshot.go")
shutil.copy(test_path,   round_dir / "scorer_test_snapshot.go")
_ensure_scorer_registered(scorer_path.stem, ...)

build_passed, build_error = _run_build_test()
(round_dir / "build_output.txt").write_text(build_error or "PASS")
if not build_passed:
    (round_dir / "build_issues.json").write_text(json.dumps({
        "round": round_num,
        "issues": [build_error],
    }))
    continue  # skip review; build error feeds next generate round
```

---

## Stage 3: Review Iterations

Review only runs after a build pass. The exit code of `review_translation.py`
determines consensus: `0` = consensus (all models consistent), `1` = no consensus.

### Review prompt

```
Read {run_dir}/prepare_reviewer_context.md for system context.

Write {round_dir}/review_prompt.txt with the exact command you are about to run.

Run:
  python {REPO_ROOT}/.claude/skills/sim2real-prepare/scripts/review_translation.py \
    --scorer {scorer_file} \
    --algorithm {algo_summary_path} \
    --signals {signal_coverage_path} \
    --evolve-block {evolve_block_path} \
    --extra-context {run_dir}/prepare_reviewer_context.md \
    --rounds 1 \
    --out {round_dir}/review_output.json

After the script exits, read {round_dir}/review_output.json and write
{round_dir}/review_issues.json:
  {"round": N, "issues": ["[model] issue text", ...]}
Use an empty issues list if all models returned verdict "consistent".
```

**Consensus is determined by `review_translation.py` exit code** (0 = consensus).
Claude writes `review_issues.json` as a convenience for the next generate prompt;
Python reads exit code via `review_issues.json["issues"] == []`.

---

## Stage 5: Final Review

Stage 5 uses the same review subprocess but writes to a separate directory to
avoid collision with Stage 3 round numbering.

```
{run_dir}/
  final-review/
    review_prompt.txt
    review_output.json
    review_issues.json
```

`stage_final_review` reuses `stage_review_iteration` with `round_dir` set to
`run_dir / "final-review"`. Round numbering is not shared with Stage 3.

---

## Changes to Existing Files

### `build_review_request.py` — add `--extra-context`

Add optional `--extra-context FILE` argument. When present, append contents as
a final section in the user message:

```python
if extra_context_path and Path(extra_context_path).exists():
    extra = Path(extra_context_path).read_text()
    user_content += f"\n\n## System Context (for reviewer)\n{extra}"
```

### `review_translation.py` — pass through `--extra-context`

Add `--extra-context FILE` argument. Pass it to each `build_review_request.py`
subprocess call. No other changes.

### `prepare.py` — Stage 3 and Stage 5

Refactored functions:
- `stage_build_context(run_dir, force)` — new preamble; `_should_skip()` guarded
- `stage_generate_iteration(round_num, round_dir, ...)` — `claude -p` subprocess;
  returns `(scorer_path, test_path)` parsed from `generate_output.json`
- `stage_review_iteration(round_dir, scorer_path, ...)` — `claude -p` subprocess;
  returns `consensus: bool` from `review_issues.json`

Existing functions unchanged:
- `_run_build_test()`, `_ensure_scorer_registered()`, `_prompt_user_continue()`
- Stage 1 (`stage_extract`), Stage 2 (`stage_translate`), Stage 4.5 (`stage_equivalence_gate`)

---

## Terminal UX

```
━━━ Step 3: Generate ━━━
  [INFO] Building codebase context (reading llm-d pkg/, test/utils/, mapping doc)...
  [OK]   Context: workspace/runs/.../prepare_codebase_context.md
  [OK]   Reviewer context: workspace/runs/.../prepare_reviewer_context.md

  ── Round 1 ──────────────────────────────────────────────────────────
  [INFO] Writer (claude -p) generating scorer...
  [INFO] Wrote: llm-d-inference-scheduler/pkg/plugins/scorer/blis_evolved_scorer.go (187 lines)
  [OK]   go build passed
  [OK]   go vet passed
  [OK]   go test passed (6 tests)
  [INFO] Reviewing with 3 models...
      Azure/gpt-4o                     ✗  inconsistent  (2 issues)
      GCP/gemini-2.5-flash             ✗  inconsistent  (1 issue)
      aws/claude-opus-4-6              ✓  consistent
  1/3 consistent  →  not all consistent
  Round logs: workspace/runs/.../rounds/1/

  No consensus [round 1]. [c]ontinue / [+N] / [a]ccept-anyway / [q]uit: c

  ── Round 2 (revision: 3 issues) ────────────────────────────────────
  [INFO] Writer (claude -p) revising scorer...
  ...
```

---

## Artifact Summary

| File | Written by | Timing | Notes |
|---|---|---|---|
| `prepare_codebase_context.md` | preamble `claude -p` | once, Stage 3 start | skippable; includes timestamp |
| `prepare_reviewer_context.md` | preamble `claude -p` | once, Stage 3 start | skippable; includes timestamp |
| `rounds/N/generate_prompt.md` | Python (render) | before each generate | absolute paths |
| `rounds/N/generate_output.json` | `claude -p` generate | after generate | success signal = file exists |
| `rounds/N/scorer_snapshot.go` | Python | after generate, before build | what was compiled |
| `rounds/N/scorer_test_snapshot.go` | Python | after generate, before build | what was compiled |
| `rounds/N/build_output.txt` | Python | after build/test | "PASS" or error text |
| `rounds/N/build_issues.json` | Python | only if build failed | feeds next generate round |
| `rounds/N/review_prompt.txt` | `claude -p` review | before calling script | exact argv logged |
| `rounds/N/review_output.json` | `review_translation.py` | after review | model verdicts |
| `rounds/N/review_issues.json` | `claude -p` review | after review | feeds next generate round |
| `final-review/review_*.json` | same as review iteration | Stage 5 only | separate from rounds/ |
| `prepare_stage3_output.json` | Python | after consensus | final scorer location |

Existing artifacts (`prepare_algorithm_summary.json`, `prepare_signal_coverage.json`,
`prepare_equivalence_results.json`, `prepare_translation_reviews.json`,
`prepare_scorer.go`, `prepare_scorer_test.go`) are unchanged.

---

## Open Questions

1. **`--output-format stream-json` event types:** What specific event types does
   `claude -p` emit (file-write completions, tool-call starts) that `prepare.py`
   should relay as `[INFO]` lines? Implementation will need a small stream-json
   relay helper; exact event schema TBD from `claude -p` docs/experimentation.

2. **Context staleness signal:** Currently `--force` is the only way to regenerate
   context. A future improvement could hash the source files listed in the preamble
   and embed the hash in the context file header, enabling auto-invalidation.
