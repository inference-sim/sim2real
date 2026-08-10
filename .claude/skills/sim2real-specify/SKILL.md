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

### Resolve first, ask last

Do NOT ask the operator how to source a signal. Work it out, then ask only about
what genuinely remains. In order:

1. **Apply the established pattern.** Per-request state that the target exposes
   only in aggregate is reconstructed by an EPP-side SHADOW TABLE populated from
   the request lifecycle the EPP already observes — the post-schedule observer,
   the first-token/streaming hook, and the completion hook. Routing instant, first
   token, and input length are EXACT this way. Remaining steps are a censored
   estimate (N̂_out minus tokens streamed); check whether the simulation censors
   too, in which case the degradation is like-for-like rather than new. This is a
   solved problem — derive it, cite the hooks, declare the bias.
2. **Prefer an aggregate correction where one exists.** A shadow table misses
   requests this replica did not place, so cross-check any count the target
   reports directly and prefer it. State which quantities remain shadow-only.
3. **Flag the replication hazard.** A replicated EPP splits a shadow table and
   systematically under-prices contention. Record it.
4. **Then batch ONE decision** for whatever is still open.

Ask the operator in a SINGLE `AskUserQuestion` at the end of this phase,
presenting the whole classification table at once. Never one question per signal.
Include only signals where you cannot resolve the mapping yourself AND the
degradation could change the comparison the experiment exists to make — for those,
give the options you found in the target checkout and your recommendation, not an
open-ended question. Everything else is a declared degradation and needs no ask.

If nothing meets that bar, do not ask at all. Proceed and declare.

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

Write `algorithms/<arm>.go` per arm, plus `config.md`.

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

### Pass condition

Zero `UNSUPPORTED` claims remain, every `BRIDGE` finding has a non-null
`declared_at`, the lint exits 0, and every re-derivation divergence is resolved or
declared.

If `UNSUPPORTED` findings survive three fix rounds, STOP. Report what remains
unresolved. Do not describe the bundle as audited.

## After specify

Tell the operator:

```
Specification complete at <experiment-root>. Gate: <n> arms audited,
<n> re-derivation divergences declared, lint clean.

Next: /sim2real-bootstrap <experiment-root>
```
