# Step 5 design — Replicas + iteration filtering

Epic: [#502 — Epic: step-5 — Replicas + iteration filtering](https://github.com/inference-sim/sim2real/issues/502)

Base branch: `refactor/v2`

Sources:
- `docs/proposals/incremental-implementation-plan.md` §Step 5 — Replicas + iteration
- `docs/proposals/incremental-implementation-plan-addendum.md` §Step 5
- `docs/proposals/replicas-as-pair-keys.md` (proposal of record)
- `docs/epics/step-0/design.md` (schema contract — `manifest.assembly.yaml.replicas`, pair-key suffix deferred here)
- `docs/epics/step-1/design.md` (owner of `assemble` and `_cmd_run` that this step grows)

This document resolves step-5's open questions (Q3 deferred from step-0; Q21, Q22, Q24 surfaced in the addendum) and pins the shape, ordering, and PR breakdown needed for split-epic.

---

## Scope

Step 5 grows the sim2real flow from single-run to N-replica: each `(workload, phase)` pair becomes N indexed pairs (`i1..iN`), replicas assemble additively, run in parallel Tekton PipelineRuns, and get verified per-iteration by `sim2real-check`. Five work strands land under this epic:

1. **Pair-key parser rework.** `_is_pair_key` grows a structured parser; new `parse_pair_key(key) -> PairKeyParts(workload, package, iteration)`. Legacy keys (no suffix) parse as `iteration=1`. ~24 callsites in `deploy.py` audited and adjusted.
2. **`replicas` field + additive-merge `assemble`.** `manifest.assembly.yaml.replicas` (step-0 schema) becomes consumable. `assemble --replicas N` expands the pair-key set. Additive merge preserves existing entries. **Grow-only**: `assemble --replicas M` with `M < N` (current) refuses. Shrink is deferred (see Out of scope). `params_hash` drift detection unchanged from proposal.
3. **Pipeline threading + `--iteration` filter + name-length validator.** `pipeline.yaml` gains a `replica` param threaded through every `resultsDir`-touching task. `deploy.py` dispatches `replica` at PipelineRun launch. `--iteration` (list+range syntax) lands on `run`, `status`, `collect`, `reset`, `wipe`. `tekton.py` gets a name-length validator at PR-name-construction time.
4. **`sim2real-check` `iN/` traversal.** Check walks `iN/` subdirs and reports per-iteration PASS/FAIL. No aggregate verdict for now (per-iteration reporting only).
5. **Documentation.** `pipeline/README.md` (new `--replicas` / `--iteration` / pair-key sections), `.claude/skills/sim2real-check/SKILL.md` (`iN/` traversal + input shape), `CLAUDE.md` (audit for pair-key refs).

The end-of-step demo:

```bash
# Assemble with 3 replicas
python pipeline/sim2real.py assemble --run R --translation T --cluster C --replicas 3
# → workspace/runs/R/manifest.assembly.yaml has replicas: 3
# → ConfigMap has wl-*|pkg|i1..i3 keys

# Dispatch parallel N-replica PipelineRuns
python pipeline/deploy.py run --run R
# → 3× dispatch per (workload, phase); results land under runs/R/results/<phase>/<workload>/iN/

# Filter operations by iteration
python pipeline/deploy.py status --iteration 2,3       # list syntax
python pipeline/deploy.py collect --run R --iteration 1-3  # range syntax

# Grow additively
python pipeline/sim2real.py assemble --run R --replicas 5
# → preserves i1..i3, adds i4..i5

# Shrink refused (grow-only in step-5; shrink is a future item)
python pipeline/sim2real.py assemble --run R --replicas 2
# → error: cannot decrease replicas (currently 5); shrink is not supported

# Per-iteration check
/sim2real-check --run R
# → reports wl-foo|pkg|i1 PASS, wl-foo|pkg|i2 FAIL, wl-foo|pkg|i3 PASS (per-iteration)
```

Step 5 does **not**:
- Deliver cross-replica analyze aggregation — deferred with the broader `/sim2real-analyze` rework (Q23 travels with it).
- Deliver `--replicas N` shorthand on `deploy.py run` or auto-assemble from run — that is Step 6 (validate/execute + auto-fix).
- Deliver a run-level aggregate verdict from check — per-iteration reporting only for now; aggregate rule is a follow-up decision.
- Migrate legacy runs to replica shape — legacy runs remain readable, but `assemble --replicas N > 1` on a legacy run refuses; user starts a fresh run.

---

## Assumptions

- **Base branch state.** Step-5 implementation begins after step-4 merges into `refactor/v2`. All step-5 PRs assume step-0's schemas (`manifest.assembly.yaml.replicas` field), step-1's `assemble` and `_cmd_run`, step-3's `sim2real-check --run R` port, and step-4's bootstrap port are all present on `refactor/v2`.
- **Demo run.** Same experiment repo pattern as step-1 (`kalantar-msb/sr` or successor). Demo exercises replicas end-to-end with `--replicas 3` as the canonical count.
- **Step-6 boundary.** Auto-fix behavior (`--replicas N` on `deploy.py run` chaining assemble; validate/execute split) lands in step-6. Step-5's `deploy.py run` requires an already-assembled run with `replicas: N` set; errors clearly if missing.

---

## Resolved open questions

| Q | Question | Decision | Rationale |
|---|---|---|---|
| Q3 | Pair-key suffix `\|iN` vs `\|rN` (deferred from step-0) | `\|iN` | Matches the CLI flag name `--iteration`; "iteration" reads naturally next to a number. |
| Q21 | Replica-decrease semantics (`--force-shrink`) | **Deferred to [#506](https://github.com/inference-sim/sim2real/issues/506).** Step-5 ships grow-only `assemble` (refuses `M < N`). | Composing existing primitives (`wipe`, `reset`, `--iteration` filter) covers the practical cases; a dedicated shrink flag lacks a clear problem statement and introduces a race with in-flight replicas. Ship the invariant `assemble adds pair keys, never removes them` literally. |
| Q22 | Old-shape vs new-shape result coexistence | Legacy detection at parse layer: pair keys without `\|iN` suffix parse as `iteration=1`; result-path readers treat missing `iN/` as implicit `i1`. Writing: `assemble --replicas N > 1` on a legacy run refuses. | Preserves read-compat with pre-step-5 runs. Refuses write-side migration because migrating in-place is a footgun. |
| Q24 | `--iteration` filter parser shape | List + range: `--iteration 2`, `--iteration 1,3`, `--iteration 1-3`, `--iteration 1,3-5` | Marginal cost above list-only; matches conventional CLI idioms. |
| — | `sim2real-check` aggregate rule | Per-iteration reporting only for now; no aggregate verdict | Aggregate-rule design (strict-all-pass vs majority-pass) needs its own discussion; not blocking step-5. |
| — | PipelineRun name length validator location | In `pipeline/lib/tekton.py` at name-construction time (fail-fast) | Catches DNS-label overflow before dispatch, not after Tekton rejects it. |
| — | Structured parser return shape | Dataclass `PairKeyParts(workload: str, package: str, iteration: int)` in a new `pipeline/lib/pairkey.py` module. `_is_pair_key` retains its metadata-exclusion role; `parse_pair_key` is the structural accessor. | Dataclass gives type-checked field access. Separate module keeps the parser reusable and testable in isolation. |
| — | Pair-key grammar | Explicit grammar in Strand 1: `wl-<workload>|<package>[|i<N>]`, kebab-case identifiers, positive integers with no leading zeros. `wl-` prefix is literal; `PairKeyParts.workload` excludes it. | Removes ambiguity around allowed characters, leading zeros, prefix handling. Reduces parser-edge-case bugs. |
| — | Malformed-key policy | Two-layer: `parse_pair_key` raises `ValueError` (strict); `_load_pairs` catches, WARN-logs, drops (tolerant). Malformed count surfaced via `status`. | Reconciles "shout loudly at the bug site" with "don't crash `deploy.py` on a corrupt ConfigMap." Keeps operators informed. |
| — | Source-of-truth authority | Manifest (declared intent) / ConfigMap (dispatchable units + status) / results (observed outputs). Divergence → fail-fast with fresh-run guidance; `status` and `sim2real-check` are read-only exceptions that report divergence rather than refusing. | Clarifies which state carrier wins on disagreement; makes recovery semantics unambiguous. |
| — | Tekton `replica` param type | `string`, default `"1"` (Tekton has no `integer` type). Dispatcher validates numeric semantics before passing. | Tekton PipelineParam supports `string`/`array`/`object` only. |
| — | `sim2real-check` exit code | `0` all-PASS-or-SKIP; `1` any FAIL/MISSING; `2` invocation error. Automation-facing contract even without a narrative aggregate verdict. | Automation needs a stable signal; per-iteration reporting alone is insufficient. |

**Deferred (with `/sim2real-analyze` rework)**: Q23 (aggregate.json helper vs in-skill), declared-vs-successful reporting, cross-replica statistics.

**Informational only**: Q25 (replica-count ceiling — ConfigMap size grows linearly; ~100 replicas is safe per proposal).

---

## Compatibility matrix

Step-5 introduces two shape variants that must interoperate cleanly:

- **Legacy run**: pair keys have no `|iN` suffix in the ConfigMap; results live at `runs/<R>/results/<phase>/<workload>/` with no `iN/` layer. These are runs assembled prior to step-5 landing.
- **New single-replica run** (`|i1` only): pair keys have `|i1` suffix; results at `runs/<R>/results/<phase>/<workload>/i1/`. This is what `assemble --replicas 1` produces on a fresh run in the step-5 world.
- **New multi-replica run** (`|i1..|iN`): pair keys have `|i1..|iN` suffixes; results at `.../<workload>/iN/`.
- **Mixed/corrupt**: some pair keys have suffixes and some don't within the same run's ConfigMap; or manifest and ConfigMap disagree; or the ConfigMap has malformed keys beyond the tolerance limit. Not produced by any code path in step-5; only reachable via manual editing or partial-write bugs.

| Command | Legacy run | New single-replica (`i1`) | New multi-replica | Mixed / corrupt |
|---|---|---|---|---|
| `assemble` | `--replicas 1` (or default): no-op if manifest already matches ConfigMap; else refuse (fresh-run guidance). `--replicas N > 1`: **refuse** — "run is in legacy single-replica shape; create a fresh run to use replicas." | `--replicas 1`: no-op. `--replicas N > 1`: grow additively (i2..iN added). | `--replicas M >= N`: grow additively. `--replicas M < N`: refuse (grow-only, link #506). | **Refuse** at divergence detection; guide to fresh run. |
| `deploy.py run` | Works. Treats pair keys as implicit `i1`; dispatches PipelineRuns with `replica="1"`; results land at legacy path `.../<workload>/` (no `iN/` layer, preserving compat). | Works. Dispatches with `replica="1"`; results at `.../<workload>/i1/`. | Works. Dispatches N× per (workload,phase); `--iteration` filters. | **Refuse** at divergence detection. |
| `status` | Works. Renders pair keys with implicit `i1` label in output (for uniform display) but does not modify the underlying keys. `--iteration` filter effectively restricts to `1`. | Works. `--iteration` filter operates on parsed `iteration` field. | Works. `--iteration` filter (list+range) supported. | Prints divergence warning + malformed-key count at top of output; then renders what it can. **Status is read-only diagnostic — no refusal here** (operator needs to see the mess to fix it). |
| `collect` | Works. Rsyncs `.../<workload>/` from PVC to local (no `iN/` layer either side). | Works. Rsyncs `.../<workload>/i1/`. | Works. Per-iteration rsync; `--iteration` filter selects subset. | Refuse at divergence detection. |
| `reset` | Works. Sets pair status to `pending` without iN semantics. `--iteration` filter effectively restricts to `1`. | Works normally. | Works with `--iteration`. | Refuse at divergence detection. |
| `wipe` | Works. Deletes local `.../<workload>/`. Cluster-side untouched (as today). `--iteration` filter effectively restricts to `1`. | Works. Deletes local `.../<workload>/i1/`. | Works. Per-iteration deletion via `--iteration`. | Refuse at divergence detection. |
| `sim2real-check` | Works. Walks `.../<workload>/` directly; reports each pair as `wl-<workload>|pkg|i1` (implicit) for uniform display. | Walks `.../<workload>/i1/`; per-iteration report of one row. | Walks each `.../<workload>/iN/` present; per-iteration report; **missing `iN/` directories emit a MISSING row per declared iteration** (not silent). | Read-only diagnostic — prints divergence warning + malformed count at top; reports what iN/ dirs it can find, marks the rest MISSING. **Does not refuse** — the operator uses check to see the state. |

**Legacy-detection heuristic**: a run is treated as legacy if `_load_pairs` returns any pair keys without `|iN` suffix. All-suffixed = new shape (single or multi depending on N). Mixed suffixes = corrupt (fail-fast per the Source of truth section). This heuristic runs once per command invocation on the ConfigMap; commands cache the classification for the invocation's lifetime.

**Exit codes**: all commands return 0 on success. `sim2real-check` returns 0 if all iterations PASS across all pairs, non-zero if any iteration FAILs or MISSING is emitted (this is the operator-facing contract for automation; a run-level aggregate verdict is deferred but the exit code is the minimal automation surface step-5 owes).

## Source of truth

Step-5 introduces enough new state carriers that authority order needs to be explicit. **Manifest, ConfigMap, and results are not equal peers.**

| Source | Authority for | Written by | Read by |
|---|---|---|---|
| `runs/<R>/manifest.assembly.yaml` (specifically `.replicas`) | **Declared intent** — "how many replicas were asked for" | `assemble` | `assemble` (as input for expansion); reference-only elsewhere |
| ConfigMap pair-key entries (`wl-*|pkg|iN`) | **Dispatchable units and their observed status** — "what actually exists and is being tracked" | `assemble` (write), `deploy.py run` (status updates) | `_cmd_run`, `_cmd_status`, `_cmd_collect`, `_cmd_reset`, `_cmd_wipe`, `sim2real-check` |
| `runs/<R>/results/**/iN/` on disk + PVC `iN/` subtrees | **Observed outputs** — produced by executions; never authoritative for what "should" exist | Tekton tasks (into PVC), `_cmd_collect` (rsync to local) | `sim2real-check`, `sim2real-analyze` (deferred) |

**Divergence policy** (fail-fast for step-5):

- If `manifest.replicas × (workloads × packages)` count does not match ConfigMap pair-key count for the run, `_cmd_run` and `_cmd_status` refuse with a clear error: `"run <R> is inconsistent: manifest declares <N> replicas but ConfigMap has <M> pair keys per (workload,package); re-run assemble or create a fresh run."` Assemble itself detects and heals a partial-write mismatch (its own crash mid-write) by resuming from ConfigMap state.
- Malformed keys in the ConfigMap are dropped by `_load_pairs` (see Strand 1 malformed-key policy) — they do not contribute to the divergence count. The dropped count is surfaced to the operator via `status` output.
- Missing `iN/` result directories are not a divergence — they're simply "not yet produced" (or "never dispatched" for pending pairs). `sim2real-check` reports missing directories per iteration (see Strand 4).
- Cross-run comparisons (e.g. two different runs' manifests) are out of scope.

Recovery: the intended recovery path for any divergence is a fresh run. In-place repair (regenerating ConfigMap from manifest, or vice versa) is not offered in step-5.

## Approach

### Strand 1 — Pair-key parser rework

**Pair-key grammar (canonical):**

```
pair_key    := "wl-" workload "|" package [ "|" iter ]
workload    := [a-z0-9]([a-z0-9-]*[a-z0-9])?          # kebab-case identifier, no leading/trailing hyphen
package     := [a-z0-9]([a-z0-9-]*[a-z0-9])?          # same as workload
iter        := "i" [1-9][0-9]*                        # positive decimal, no leading zeros ("i1", "i10"); "i0" and "i01" are invalid
```

Notes:
- The `wl-` prefix is a literal, not part of the workload name. `PairKeyParts.workload` stores the workload without the prefix (e.g. `"chat-mid"`, not `"wl-chat-mid"`).
- Metadata keys (`_meta`, `_notes`) are not pair keys — they're rejected by `_is_pair_key` before `parse_pair_key` is called.
- Case-sensitive; all lowercase.
- No embedded `|` in workload or package names.

**New module**: `pipeline/lib/pairkey.py`.

```python
@dataclass(frozen=True)
class PairKeyParts:
    workload: str      # e.g. "chat-mid" (without "wl-" prefix)
    package: str       # e.g. "sim2real-ac"
    iteration: int     # 1 for legacy keys without |iN suffix; else parsed from suffix

    def to_key(self) -> str:
        """Reconstruct the canonical pair-key string. Always emits the |iN suffix."""
        return f"wl-{self.workload}|{self.package}|i{self.iteration}"

def parse_pair_key(key: str) -> PairKeyParts:
    """Parse a pair key per the grammar above. Legacy keys (no |iN suffix) parse as iteration=1.
    Raises ValueError on malformed input, with a message naming the offending key.
    Caller should filter with _is_pair_key first to exclude metadata keys."""
```

**Display**: consumers that print pair keys (`_cmd_status`, `sim2real-check` report rows) may either render the raw key from the ConfigMap or call `PairKeyParts.to_key()` for a canonical representation. Both should always include the `wl-` prefix and `|iN` suffix on output (even for legacy runs, where the parser normalizes to `iteration=1`).

**`_is_pair_key` in `deploy.py`** stays as-is (metadata-exclusion check: `not key.startswith("_")`). It gates whether a key is a pair-key at all; `parse_pair_key` runs after that gate for callsites that need the structural fields.

**`_load_pairs`** grows: each pair entry gains an `iteration` field derived from `parse_pair_key(key).iteration`. Existing `workload` / `package` fields can either continue to be re-derived or be sourced from `PairKeyParts` — the design leaves this to the implementer as an incremental cleanup (existing tests must keep passing).

**Malformed-key policy** (two-layer):
- **Parser layer** (`parse_pair_key`): strict. Raises `ValueError` on any grammar violation with a message naming the offending key. This is the "shout loudly at the site of the bug" layer.
- **Loader layer** (`_load_pairs`): tolerant. Wraps `parse_pair_key` in a try/except; malformed keys are logged with a WARN-level message ("skipping malformed pair key: <key>: <error>") and dropped from the returned dict. This keeps `deploy.py` operations from crashing on a corrupt ConfigMap while still leaving evidence in logs.
- **Downstream visibility**: `status` reports the count of dropped malformed keys (if any) at the top of its output so operators know something odd is happening. `_cmd_status` fetches the count via a `_load_pairs_with_errors()` variant that returns `(pairs, malformed_count)`.

**Callsite audit** (~24 `_is_pair_key` + ~5 `_load_pairs` uses in `deploy.py`): the parser change is transparent to metadata-exclusion callers. Callsites that read pair-key structural fields (`_apply_run_filters`, `_cmd_status`, `_cmd_run` dispatch) route through `parse_pair_key` or use the new `iteration` field on pair entries.

**Tests**: `tests/unit/test_pairkey.py` — legacy key parses to iteration=1, `|iN` suffix parses correctly, metadata keys are rejected, malformed keys raise ValueError. `tests/unit/test_load_pairs.py` — malformed key in the ConfigMap is dropped with a WARN log; `_load_pairs` returns the remaining valid pairs; malformed_count is surfaced correctly.

### Strand 2 — `assemble` additive-merge (grow-only)

**File**: `pipeline/lib/assemble_run.py` (existing).

**Changes**:
- Read `replicas: N` from `manifest.assembly.yaml` (step-0 already defines the field).
- Pair-key set expansion: for each (workload, package) pair, emit `wl-<workload>|<package>|i1 .. i<N>`.
- **Additive merge**: on re-assemble with `--replicas M >= N` (existing), preserve `i1..iN`, add `i(N+1)..iM`. Detect via reading the current ConfigMap state before writing.
- **Grow-only**: on re-assemble with `--replicas M < N`, refuse with a clear error naming the current count and pointing at the future shrink issue. No flag bypasses this in step-5.
- **Legacy-run guard**: if the target run has any pair keys without `|iN` suffix in its ConfigMap (legacy shape), and `--replicas N > 1` is passed, refuse: "run <R> is in legacy single-replica shape; create a fresh run to use replicas."
- **`params_hash` drift**: unchanged from proposal — refuse content changes without `--force`.

**Tests**: `tests/unit/test_assemble_replicas.py` — additive 3→5, refuse 3→1 (grow-only), legacy-run refuse.

### Strand 3 — Pipeline threading + `--iteration` filter + name validator

**`pipeline.yaml`**:
- Add `replica` to the pipeline `params:` block (Tekton param type `string`, default `"1"`). Tekton params are `string` / `array` / `object` — there is no `integer` type. Numeric validation happens in the dispatcher (`_cmd_run`) before the param is passed; downstream tasks treat the value as an opaque string substitution.
- Thread `$(params.replica)` into `resultsDir` template values for every task that references results. Rather than relying on grep alone, this PR introduces a small helper `build_results_dir(run, phase, workload, replica) -> str` in `pipeline/lib/tekton.py` and audits `pipeline.yaml` to make every result-path construction go through the helper's template contract. The rendered-pipeline test in tests/integration asserts every result-path task uses `.../<workload>/i$(params.replica)/`.
- The `resultsDir` template shifts from `.../<workload>/` to `.../<workload>/i$(params.replica)/`.

**`pipeline/lib/tekton.py`**:
- PipelineRun name construction becomes `{phase}-{workload}-{run}-i{N}`.
- Add `validate_pipelinerun_name(name: str)` — raises if `len(name) > 253` (DNS label limit) or `len(name) > 63` if the caller marks it as a label-value site. Called at name-construction time, before writing YAML.

**`deploy.py:_cmd_run` dispatch**:
- For each pair entry, extract `iteration` (from `_load_pairs`), pass `replica` PipelineRun param at dispatch.
- `_apply_run_filters` gains an `iteration` term.

**`--iteration` argparse**:
- Applies to `run`, `status`, `collect`, `reset`, `wipe`.
- **Parser**: list + range syntax. `--iteration 2` → `{2}`, `--iteration 1,3` → `{1,3}`, `--iteration 1-3` → `{1,2,3}`, `--iteration 1,3-5` → `{1,3,4,5}`.
- Implementation: new helper `parse_iteration_spec(spec: str) -> set[int]` in `pipeline/lib/pairkey.py` (co-located with parser). Reject negative/zero, reject reversed ranges (`5-1`), reject non-integer tokens.

**Tests**:
- `tests/unit/test_iteration_spec.py` — list/range/mixed parses, malformed inputs raise.
- `tests/unit/test_pipelinerun_name_validator.py` — under-limit passes, over-limit raises.
- Integration: `--iteration 2,3` on `status` filters correctly.

### Strand 4 — `sim2real-check` `iN/` traversal

**File**: `.claude/skills/sim2real-check/SKILL.md` + any Python helpers under `.claude/skills/sim2real-check/`.

**Change**: the skill's path model grows one segment. Where step-3 walked `runs/R/results/<phase>/<workload>/`, step-5 walks `runs/R/results/<phase>/<workload>/iN/` for each iteration in the run's declared range.

**Per-iteration verdicts** (four states, mutually exclusive):
- `PASS` — all signals for this (algorithm, workload, iteration) satisfied.
- `FAIL` — at least one signal for this iteration failed.
- `SKIP` — the (algorithm, workload) combination is absent from the run's translation (inherited from step-3's semantics; not iteration-specific).
- `MISSING` — the iteration is declared in the ConfigMap but the corresponding `iN/` directory does not exist on disk. Distinct from SKIP: SKIP means "the run's translation did not include this pair"; MISSING means "the run did include it, but no results are here."

**Iteration coverage**: check reports one row per declared (algorithm, workload, iteration) — including declared-but-missing iterations. It does not silently skip missing directories.

**Output format**: per-iteration rows in the skill's report, e.g.:

```
wl-chat-mid|sim2real-ac|i1  PASS
wl-chat-mid|sim2real-ac|i2  FAIL (2 signals failed: TTFT_p95, e2e_p50)
wl-chat-mid|sim2real-ac|i3  MISSING (no results/i3/ directory)
wl-chat-mid|sim2real-routing|i1  SKIP (algorithm not in translation)
```

**Exit code contract** (for automation):
- `0` — all rows are `PASS` or `SKIP`.
- `1` — at least one `FAIL` or `MISSING`.
- `2` — invocation error (missing run, unreadable ConfigMap, malformed inputs).

**No aggregate verdict** — the exit code is the automation surface; a run-level narrative verdict is deferred.

**Legacy compat**: if the run's ConfigMap has no `|iN` suffixes (legacy shape), check walks `.../<workload>/` directly and reports each pair as `wl-<workload>|pkg|i1` (implicit) for uniform display. MISSING does not apply to legacy runs (no `iN/` layer expected).

**Mixed/corrupt state**: check is read-only diagnostic and does not refuse — it prints the divergence warning + malformed-key count at the top of its output, then reports what it can find with MISSING for the rest. This is the exception to the fail-fast-on-divergence rule (per Source of truth section) because check is the tool operators use to see divergence.

**Tests**: skill's existing test suite grows fixtures for:
- Replica-shape run with all iterations present (all PASS).
- Replica-shape run with one iteration failing signals (FAIL row).
- Replica-shape run with a declared-but-missing iteration (MISSING row).
- Legacy-shape run (implicit i1 rows).
- Mixed/corrupt fixture (warning header + partial report).
- Verify exit codes for each case.

### Strand 5 — Documentation

**Audit and update**:
- `pipeline/README.md` — new sections: pair-key shape (`|iN` suffix + legacy semantics); `--replicas` flag on `assemble` (grow-only in step-5; shrink tracked at #506); `--iteration` (list+range) on filter-aware subcommands; PipelineRun name length constraint.
- `.claude/skills/sim2real-check/SKILL.md` — updated input path model (walk `iN/` subdirs); per-iteration output format; legacy-shape compat note.
- `CLAUDE.md` — audit for stale pair-key references; update where the pair-key shape or `|iN` suffix appears.

**No new docs** — no `docs/refactor/replicas.md` or similar. The proposal remains the canonical model; README + SKILL.md carry the operator-facing surface.

---

## Task breakdown (PR-sized)

Every PR merges to `refactor/v2-step-5`. Ordering below is authoritative for dependencies; parallelization opportunities called out inline.

### PR 1 — Pair-key parser module + `_is_pair_key`/`_load_pairs` integration

**Scope**:
- New `pipeline/lib/pairkey.py`: `PairKeyParts` dataclass, `parse_pair_key(key: str) -> PairKeyParts`, `parse_iteration_spec(spec: str) -> set[int]`.
- `deploy.py`: `_load_pairs` populates `iteration` field on each pair entry via `parse_pair_key`. `_is_pair_key` unchanged. Callsites that read structural fields updated to use `parse_pair_key` or the new field.
- Unit tests for both parsers.

**Acceptance criteria**:
- Legacy pair keys (no suffix) parse as `iteration=1`.
- `|iN` suffix parses correctly for N in `[1, 100]`.
- Metadata keys (`_meta`, `_notes`) rejected by `_is_pair_key`, not passed to `parse_pair_key`.
- Malformed pair keys raise `ValueError` with a message naming the key.
- All existing `deploy.py` tests pass unchanged.

**Blocks**: PRs 2, 3, 4.

### PR 2 — `assemble` additive-merge + `replicas` consumption (grow-only)

**Scope**:
- `pipeline/lib/assemble_run.py`: read `replicas: N` from `manifest.assembly.yaml`; expand pair-key set to include `|i1..|iN`.
- Additive-merge implementation: on re-assemble with `M >= N`, preserve existing keys, add new ones.
- Grow-only guard: on re-assemble with `M < N`, refuse with a clear error message that names the current count and points at the future shrink issue.
- Legacy-run guard: refuse `--replicas N > 1` on runs whose ConfigMap has no `|iN` suffixes.
- Unit + integration tests.

**Acceptance criteria**:
- `--replicas 3` on fresh run produces `i1|i2|i3` pair keys.
- `--replicas 5` on run at 3 preserves i1..i3, adds i4..i5.
- `--replicas 2` on run at 3 refuses with an error message that includes the current count and a pointer to the future shrink issue.
- `--replicas 3` on a legacy run refuses with a clear error.
- Existing `assemble` tests pass unchanged (single-replica default flow).

**Depends on**: PR 1. **Blocks**: PR 3.

### PR 3 — Pipeline threading + `_cmd_run` dispatch + name validator

**Scope**:
- `pipeline.yaml`: add `replica` param to pipeline + thread through every `resultsDir`-touching task.
- `pipeline/lib/tekton.py`: `validate_pipelinerun_name(name)` + PR name construction `{phase}-{workload}-{run}-i{N}`.
- `deploy.py:_cmd_run` dispatch: pass `replica` param per pair entry.
- `deploy.py:_apply_run_filters`: gains `iteration` term.
- Integration test: end-to-end 3-replica run against a real cluster (or a mock PipelineRun dispatcher).

**Acceptance criteria**:
- N-replica assemble → N× PipelineRuns dispatched per (workload, phase).
- Results land under `runs/R/results/<phase>/<workload>/iN/` for each replica.
- PR name exceeds 253 chars → `validate_pipelinerun_name` raises before dispatch.
- Grep audit of `resultsDir` in `pipeline.yaml`: every occurrence threads `replica`.

**Depends on**: PR 1, PR 2. **Blocks**: PR 5.

### PR 4 — `--iteration` argparse on filter-aware subcommands

**Scope**:
- Argparse entries for `--iteration` on `run`, `status`, `collect`, `reset`, `wipe`.
- Filter term wired through `_apply_run_filters` (extends PR 3's `_apply_run_filters` change, or lands independently if PR 3 kept the term optional).
- Uses `parse_iteration_spec` from PR 1.
- Integration test: `deploy.py status --iteration 2,3` filters correctly.

**Acceptance criteria**:
- `--iteration 2`, `--iteration 1,3`, `--iteration 1-3`, `--iteration 1,3-5` all parse and filter as expected.
- Malformed input (`--iteration 0`, `--iteration 5-1`, `--iteration abc`) errors clearly.
- Filter composes with `--workload`, `--package`, `--status`, `--only` (AND).

**Depends on**: PR 1. **Parallelizable with**: PR 2, PR 3.

### PR 5 — `sim2real-check` `iN/` traversal + per-iteration reporting

**Scope**:
- Update SKILL.md and any Python helpers under `.claude/skills/sim2real-check/`.
- Walk `iN/` subdirs; produce per-iteration verdicts.
- Legacy-shape compat: missing `iN/` layer → implicit `i1`.
- Skill test fixtures for replica-shape and legacy-shape runs.

**Acceptance criteria**:
- Check on a 3-replica run produces 3× per-iteration rows per (workload, package).
- Declared-but-missing iterations emit a MISSING row (not silently omitted).
- SKIP semantics inherited from step-3 (algorithm-not-in-translation) still work.
- Check on a legacy run reports as if all pairs are `i1`.
- Exit code contract: `0` on all-PASS-or-SKIP, `1` on any FAIL/MISSING, `2` on invocation error.
- Mixed/corrupt run: check does not refuse; emits a divergence-warning header, reports what it can, uses MISSING for gaps.
- No run-level narrative verdict emitted (exit code is the automation surface).

**Depends on**: PR 3 (results actually land under `iN/`). Skill development can begin against fixtures before PR 3 merges.

### PR 6 — Documentation

**Scope**:
- `pipeline/README.md`: new sections for pair-key shape, `--replicas` (grow-only), `--iteration`, name-length constraint.
- `.claude/skills/sim2real-check/SKILL.md`: covered in PR 5. Sanity-check consistency here.
- `CLAUDE.md`: pair-key reference audit + updates.

**Acceptance criteria**:
- All new CLI flags in behavior have a matching README entry.
- README example snippets execute against the merged PR 1-5 code (spot-check).
- No stale pair-key references remain in `CLAUDE.md`.

**Depends on**: PR 1-5 landed (documents the shipped behavior).

---

## Ordering summary

```
PR 1 (parser)
  ├─ PR 2 (assemble) ──┐
  ├─ PR 4 (--iteration filter, parallel with PR 2)
  └─ PR 3 (pipeline threading) ── PR 5 (check) ── PR 6 (docs)
```

- **PR 1 must land first.** Everything else keys on the parser module.
- **PR 2 and PR 4** can run in parallel after PR 1.
- **PR 3** depends on PR 2 (needs `replicas: N` written to `manifest.assembly.yaml`) and PR 1 (parser).
- **PR 5** depends on PR 3 (results land under `iN/`).
- **PR 6** last (documents shipped behavior).

---

## Testing

### Unit
- `pipeline/lib/pairkey.py` — parser and iteration-spec.
- `pipeline/lib/tekton.py` — name-length validator.
- `pipeline/lib/assemble_run.py` — additive-merge cases, grow-only refusal, legacy-run guard.

### Integration
- Full 3-replica demo run against a real cluster (canonical demo — end-to-end).
- `--iteration 2,3` filter on `status`/`collect` returns the expected subset.
- Grow-only refusal: `assemble --replicas 2` on run at 3 errors with a message that names the current count and links to #506.
- Legacy run: check produces expected `i1`-labeled output; assemble refuses `--replicas 3`.

### Skill
- `sim2real-check` tests grow replica-shape and legacy-shape fixtures.

---

## Risks

- **Silent `pipeline.yaml` threading gaps.** Missing one `resultsDir` reference means replica results land in the wrong directory. Mitigation: PR 3 grep audit of every `resultsDir` occurrence + integration test that fails loudly if a task writes to the wrong path.
- **Structural parser regression breaks legacy behavior.** Metadata-exclusion (`_meta`) must remain excluded; malformed keys must not crash `_load_pairs`. Mitigation: PR 1's test set includes both regression cases; existing `deploy.py` tests are the second guardrail.
- **PR name length varies by workload name.** A long workload name + long run name + `|iN` suffix could exceed 63 chars for k8s label values (or 253 for DNS labels). Validator catches at construction time.
- **Legacy-run migration ambiguity.** Users may expect `assemble --replicas 3` on a legacy run to migrate it in place. The refuse-and-tell-them-to-start-fresh message must be clear.
- **Grow-only invariant surprises.** Users who over-declare replicas cannot decrease within a run in step-5; the shrink follow-up (see Out of scope) will address this. The `--replicas M < N` error message must state this explicitly and link to the follow-up issue.

---

## Out of scope

- **Replica decrease (shrink).** Step-5 ships grow-only `assemble`. A shrink primitive — whatever its final shape — needs its own problem statement (which real workflow does it enable that existing primitives don't?) and a design pass on cluster-side result cleanup. Tracked as [#506](https://github.com/inference-sim/sim2real/issues/506). Practical workarounds until then: filter analysis with `--iteration 1,2` to ignore trailing replicas; or start a fresh run with the intended count.
- `/sim2real-analyze` cross-replica aggregation (declared vs successful reporting, mean/std/percentiles, `aggregate.json` helper). Deferred to a future step with the broader analyze rework.
- `--replicas N` shorthand on `deploy.py run` (chains assemble). Step-6.
- Auto-execute (validate/execute pattern) for `deploy.py run`. Step-6.
- Run-level aggregate verdict from `sim2real-check`. Follow-up decision.
- In-place legacy run migration. Users start a fresh run for replica flow.
- Distributed replica execution across clusters. Proposal §"What stays out of scope."
- Replica diversity / parameter sweep. Proposal §"What stays out of scope."
