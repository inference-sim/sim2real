---
name: sim2real-specify
description: |
  Turn arbitrary upstream provenance (a BLIS commit, a Nous campaign, a paper
  plus code) into the canonical sim2real bundle inputs: algorithms/<arm>.go as a
  cited specification layer, README.md, config.md, workloads/, inputs/, and
  sim_results/ with checksums. Establishes the five properties that make an
  algorithm worth transferring -- causal mechanism, required structural shape,
  observability, isolation, and named threats to validity -- then gates the
  specification with an independent citation audit, a blind re-derivation of the
  focal arm, and a mechanical citation lint. Run BEFORE /sim2real-bootstrap.
argument-hint: "<experiment-root> [--focal ARM]"
user-invocable: true
allowed-tools:
  - Agent
  - SendMessage
  - TaskCreate
  - TaskUpdate
  - TaskList
  - AskUserQuestion
  - Bash(python3 *)
  - Bash(.venv/bin/python *)
  - Bash(git *)
  - Bash(test *)
  - Bash(shasum *)
  - Bash(cp *)
  - Bash(mkdir *)
  - Glob
  - Grep
  - Read
  - Write
  - Edit
---

# sim2real-specify

Produce a bundle whose algorithm specification is worth transferring. This runs
before `/sim2real-bootstrap`, which consumes `algorithms/` and `workloads/` as
pre-existing inputs.

The product is a MEANINGFUL algorithm specification. Correctness of the algebra is
necessary but not sufficient; an algorithm that is unimplementable on the target,
or indistinguishable from the baseline, is not worth transferring even when its
arithmetic is perfect.

## What this skill does NOT do

- Does not create `transfer.yaml` or `baselines/` — `/sim2real-bootstrap`.
- Does not wire submodules — bootstrap Task 1 derives the ref, Task 2 adds it.
- Does not produce compiling code — `/sim2real-translate`. The specification layer
  is non-compiling by design and its adapters stay declared unknowns.
- Does not build images or touch cluster config.
- Does not emit a limitations register or signal reference document. Degradations
  are declared in the specification layer's own header.

## Output contract

Produce exactly these paths under `<experiment-root>`:

| path | authored or copied |
|---|---|
| `algorithms/<arm>.go` | authored |
| `README.md` | authored |
| `config.md` | authored |
| `workloads/` | copied verbatim |
| `inputs/` | copied verbatim |
| `sim_results/` + `CHECKSUMS.sha256` | copied verbatim |
| `.gitignore` | generated |

Do NOT create `LIMITATIONS.md`, `docs/`, or `scripts/`.

**Verbatim-copy principle.** Anything that already exists upstream is copied, never
re-expressed: workloads, coefficients, results, patches. Re-authoring is reserved
for the specification layer, the one artifact that must change form. Re-authored
algebra is where defects come from; copied artifacts have never been a defect
source.

## Phase 0 — Ground

Obtain and verify two readable checkouts. Ask the operator for whichever is not
already present:

- the simulation source at a specific commit;
- the target component at a specific ref.

Record both pins. Every later claim is a citation against these two trees.

HALT if either is unreadable at its stated pin. Say which one and stop — nothing
downstream can be substantiated.

## Phase 1 — Mechanism

Establish, by interview plus source reading:

- the decision rule, and where it lives in the simulation source;
- the CAUSAL story: not that it wins, but why. Name the quantity that differs
  between conditions and the mechanism that exploits it.
- the cited evidence that it wins: which cells, what margin, against what
  baseline.

Copy the results artifacts verbatim into `sim_results/` and write
`CHECKSUMS.sha256` over them with `shasum -a 256`.

HALT if the results do not match their own checksums, or if the commit that
produced them is not the pin from Phase 0. A disagreement here invalidates every
cited margin.

## Phase 2 — Shape

Determine the structural form the decision takes: per-endpoint score, joint argmin
over a cross product, admission gate, dispatch ceiling, or something else. Then
read the target checkout and find an extension point that can express it.

Compare against the target's NATURAL decomposition. Where the natural
decomposition is weaker, quantify what would be lost, using the simulation's own
ablation rather than an estimate.

HALT if no extension point can express the required shape. The hazard is silent
fallback to the weaker natural decomposition, which transfers a different
algorithm while resembling success. Say so loudly instead.

## Phase 3 — Observability

Enumerate every quantity the decision rule reads. Classify each:

| status | meaning |
|---|---|
| direct | an existing target or engine value with the same definition |
| derivable | computable from existing values; record the derivation |
| degraded | an approximation is available; record the direction of bias |
| unobtainable | no real signal exists |

Every row carries a citation to the value that supplies it. This is a
classification pass, not a reference document — keep it to a table.

If a quantity carrying the CORE mechanism is `unobtainable`, you MUST put the case
to the operator with `AskUserQuestion` before proceeding to Phase 5. This is
mandatory and blocking, not advisory. Present the quantity, what the target can
supply instead, and the direction of the resulting bias — then let the operator
decide. Writing a well-reasoned degradation note is NOT a substitute for asking:
accepting a degradation that weakens the mechanism under test is the operator's
call, however good your reasoning is.

This classification governs runtime observability ONLY. It does not license
omitting anything from the specification — see the firewall in Phase 5.

## Phase 4 — Isolation

Identify the comparator arms and ablations that isolate the mechanism, each with
cited simulation evidence. A comparator that shares the machinery and differs only
in the objective is worth more than a weaker baseline, because it attributes the
effect to the mechanism rather than to the machinery.

If nothing isolates the mechanism, say so: the transfer may proceed but its
results will not be attributable.

## Phase 5 — Specify

Write `algorithms/<arm>.go` per arm, plus `README.md` and `config.md`. All three
have stated contracts below; `config.md`'s is the one most easily lost, because
its first part is consumed by a machine rather than a reader.

The specification layer:

- states the COMPLETE computation — see the completeness rule below;
- states the policy against REAL target interface names;
- leaves TARGET-API ADAPTERS as declared unknowns rather than guesses, so a wrong
  guess fails loudly rather than mis-scoring silently;
- carries an upstream `path:line` citation on every non-obvious expression;
- declares each Phase 3 degradation in its header WITH the direction of bias;
- invents no in-source marker conventions.

### Completeness, and the Phase 3 firewall

READ THIS TWICE. It is the failure mode this skill is most prone to.

The specification must state the whole computation: every term the simulation
sums, every coefficient, every rounding, every guard that skips a term. Nothing
downstream re-derives the algebra — `/sim2real-translate` treats this file as
authoritative for the science, so an omission here is carried faithfully into the
plugin and never questioned again.

A function with NO BODY is legitimate in exactly one case: a target-API adapter
whose exact accessor must be confirmed against the pinned checkout. It is never
legitimate for a quantity the simulation computes.

> Phase 3 tells you what the port can OBSERVE AT RUNTIME. It never tells you what
> the specification may leave UNSTATED.

An `unobtainable` or `degraded` INPUT does not license omitting the algebra that
consumes it. Those are independent facts and both must be recorded:

- the computation, stated in full, cited to the simulation source;
- the input's observability status, declared as a degradation with its direction
  of bias.

Worked example of getting this WRONG. The rollout admission estimate depends on
per-resident remaining steps, which the target cannot observe. The wrong response
is a bodiless `estimateAdmissionDelay` marked UNKNOWN. The right response is to
port the roll-forward estimator in full from the simulation, and separately
declare that its per-resident input is degraded — approximated from EPP-side
bookkeeping — and that the resulting bias understates contention.

If you find yourself writing a bodiless function for anything other than an
accessor, you are omitting science. Write the algebra.

One rule governs references to other pipeline stages:

> A specification may REQUEST things of downstream stages. It may never ASSERT
> what they do.

"Confirm X against the pinned checkout" is a legitimate request. "Stage Y resolves
X" asserts another component's behavior and is forbidden unless that component's
source says so.

`README.md` states the mechanism, the required shape, the honest status including
where the policy did NOT win, the pre-registered expectation, the pins table, and
the layout. If Phase 1 found no results, write the expectation as a hypothesis with
no margin attached and say that no measurement backs it.

### What `config.md` must contain

`config.md` states the deployment the transfer targets and every knob the arms
read. Five parts, in this order:

1. **vLLM pod configuration** — a table headed `## vLLM Pod Configuration`. This
   table is MACHINE-READ: `/sim2real-bootstrap` Task 3 derives
   `baselines/baseline.yaml` from it and HALTS without it. `Model` and `GPU` are
   mandatory. Include `max_model_len` whenever the model is absent from
   bootstrap's `MODEL_METADATA`, because that pair is what makes the omission
   fatal rather than merely warned.
2. **Simulation → deployment mapping** — each simulator flag beside the vLLM
   parameter it calibrates, so a reader can audit that the two agree.
3. **Simulator-only knobs** — flags with no deployment equivalent.
4. **`blis observe` invocation** — a fenced bash block. Emit it even when every
   value equals the pipeline default, so the values are this bundle's decision
   and not a downstream fallback.
5. **Per-arm settings** — the flags distinguishing each arm.

Add a **`## Fleet topology`** section whenever the fleet is not one homogeneous
pool: state the layout, and state plainly if it is not expressible in the
scenario schema the target deploys with. Give the operator the options and what
each costs. Do not let a topology the schema cannot represent reach bootstrap
undocumented.

Parts 1 and 2 are a PROJECTION of what Phase 1 already read from the campaign
runner into the dialect the target deploys in. You are not discovering new facts;
you are restating known ones in the consumer's vocabulary. The simulator dialect
alone is not enough: a bundle that records `--max-num-running-reqs 256` and stops
has stated the fact and still fails bootstrap, because the consumer reads
`max_num_seqs`.

Do not restate bootstrap's field list here — it lives in that skill's
`PARAMETER_ALIASES` and `VLLM_SECTION_KEYWORDS`, and the Phase 6 consumer check
verifies agreement mechanically. Duplicating it invites drift.

### Deployment values the simulation cannot supply

Some vLLM parameters are properties of the target cluster, not of the simulation.
`gpu_memory_utilization` is the common case; `max_model_len` and
`enable_prefix_caching` are often fixed by no campaign flag.

Do NOT infer them, and do NOT omit the row. Ask the operator. If the operator
does not know, write the row's value as `**CONFIRM**` and say in the notes column
what depends on it.

An omitted row becomes a silent downstream default. `CONFIRM` fails loudly.
Prefer the loud failure. `enable_prefix_caching` earns particular care: bootstrap
interprets silence as ON rather than flagging it, so a bundle that needs it OFF
and stays silent gets the opposite of what it meant.

## Phase 6 — Gate

### Placeholder substitution

Read `prompts/audit.md` and `prompts/rederive.md` and substitute each braced name
below. This list is the contract: `tests/test_skill_substitution.py` fails if a
prompt uses a name absent here, or if a name here is used by no prompt.

- `{BUNDLE_ROOT}` → the absolute experiment root
- `{ARM_FILE}` → absolute path to the arm under audit
- `{ARM_NAME}` → the arm's name, matching its filename stem
- `{SIM_TREE}` → absolute path to the simulation checkout
- `{SIM_PIN}` → the simulation commit recorded in Phase 0
- `{TARGET_TREE}` → absolute path to the target checkout
- `{TARGET_PIN}` → the target ref recorded in Phase 0
- `{VERDICT_PATH}` → `/tmp/specify-verdict-<arm>.json`
- `{POLICY_ENTRY_POINTS}` → newline-separated `path:symbol` entry points from Phase 1
- `{DERIVATION_PATH}` → `/tmp/specify-derivation-<arm>.json`
- `{MAIN_SESSION_NAME}` → `"main"`

### Run the three components

1. **Citation audit, every arm.** Spawn one agent per arm in a single tool-call
   message: `Agent(name="audit-<arm>", run_in_background=true,
   subagent_type=general-purpose, model="opus", prompt=<substituted audit.md>)`.
2. **Blind re-derivation, EVERY arm.** Spawn one per arm:
   `Agent(name="rederive-<arm>", run_in_background=true,
   subagent_type=general-purpose, model="opus",
   prompt=<substituted rederive.md>)`.

   Every arm, not just the focal one, because this is the gate's ONLY omission
   detector. The audit does correspondence discovery over expressions that are
   present; a term that was never written has no expression to audit. Since
   omission is this skill's most likely failure, the detector cannot be scoped to
   one file.
3. **Citation lint.** Run:

   ```bash
   .venv/bin/python .claude/skills/sim2real-specify/scripts/lint_citations.py \
     --bundle <experiment-root> --tree <sim-tree> --tree <target-tree>
   ```

   Pass a `--tree` for every checkout the bundle cites, including the inference
   engine if its metrics are cited. A citation into an unpassed tree reports
   `unresolved-path`, which is the tool refusing to guess rather than a defect.
4. **Consumer check.** The one contract `config.md` has is that
   `/sim2real-bootstrap` can parse it. Verify with the real consumer rather than
   by inspection, so the two skills cannot drift and the field list stays
   single-sourced in bootstrap:

   ```bash
   python3 .claude/skills/sim2real-bootstrap/generate_from_config.py \
     <experiment-root>/config.md -o "$(mktemp -d)"
   python3 .claude/skills/sim2real-bootstrap/generate_from_config.py \
     <experiment-root>/config.md --emit-observe-yaml
   ```

   The first must exit 0 — a temp `-o` makes it a dry run, so no bundle file is
   written. The second must report `# source: config.md` on every key the bundle
   intends to set; `# source: sim2real-bootstrap default` means part 4 of
   `config.md` is missing or unparsed.

   Read the warnings, not just the exit code. `model '<x>' not in MODEL_METADATA`
   and `hardware '<x>' not in HARDWARE_LABELS` are both non-fatal, and both mean
   bootstrap will need a lookup-table entry before Task 3 produces a usable
   baseline. Say so in `config.md` where the operator will see it.

### Reconcile

- Every `WRONG` finding: fix the specification. Cite the `settled_by` path in the
  fix.
- Every `UNSUPPORTED` finding: delete the claim, or convert it to a stated open
  question in `README.md` owned by the bundle.
- Every `BRIDGE` finding with `declared_at: null`: write the declaration into the
  specification header, naming the degradation AND the direction of the resulting
  bias. Do NOT delete the code — absence of a simulation counterpart is that
  code's expected property. This is prose in the header; do not introduce a marker
  convention for it.
- Every `BRIDGE` finding whose assumptions the auditor found wrong against the
  target or engine checkout: fix as for `WRONG`.
- Every re-derivation term absent from the specification: ADD IT. The default is
  to write the algebra, not to declare it missing. Declaring an omission is
  permitted only when the term cannot be stated at all — not merely when its
  inputs are unobservable, which is a Phase 3 degradation and does not license
  omission. A silent omission is a defect even when defensible.
- Any bodiless function that is NOT a target-API accessor: write its algebra from
  the simulation source. This is the check that catches a specification which
  states its top-level score and leaves every operand unimplemented.
- Every lint failure: correct the citation, or add `lint-skip` on that line if the
  reference is illustrative rather than a citation.
- Consumer-check failure: fix `config.md`, never the consumer. If the check cannot
  be made to pass because the fleet is not expressible in the scenario schema,
  that is a `## Fleet topology` disclosure plus an operator decision — not a
  reason to ship a `config.md` bootstrap cannot read.

### Pass condition

Zero `UNSUPPORTED` claims remain, every `BRIDGE` finding has a non-null
`declared_at`, the lint exits 0, the consumer check exits 0 with no unintended
defaults, and every re-derivation divergence is resolved or declared.

If `UNSUPPORTED` findings survive three fix rounds, STOP. Report what remains
unresolved. Do not describe the bundle as audited.

## After specify

Tell the operator:

```
Specification complete at <experiment-root>. Gate: <n> arms audited,
<n> re-derivation divergences declared, lint clean.

Next: /sim2real-bootstrap <experiment-root>
```
