# sim2real-specify — design

A skill that turns arbitrary upstream provenance into the canonical sim2real
bundle inputs, with the algorithm specification gated for correctness.

Runs before `sim2real-bootstrap`. Date: 2026-08-10.

## Problem

Bundle creation is the only stage of the sim2real flow with no skill. It has been
done by ad-hoc, unversioned prompts — `~/Downloads/create-sim2real-bundle.md` for
Nous-campaign provenance, and a separate one-off prompt for BLIS-commit
provenance. Three consequences, all observed:

1. **The specification layer has no correctness gate.** `pd-infocomm`'s
   `algorithms/causal_slo_externality.go` was generated at `08203b4` and audited
   a day later at `ead56ea`, which found four defects in the ported algebra plus
   one undeclared fidelity gap. Per that commit's own verification note, the only
   automated check that had run on the algebra was `gofmt`.

   The most damaging defect was a spurious `/100.0` in `kvTokensFor`:
   `KVCacheUsagePercent` is a fraction in `[0,1]` despite its name, so the
   divisor under-counted KV by 100×, collapsing the `C1·KV` term that carries
   hardware heterogeneity — the mechanism the study's central claim rests on. Its
   cause was trusting a prose mapping artifact over the source.

   The other three were transcription errors invisible without line-by-line
   comparison against the cited upstream: a dropped causal prefill-attention
   charge in `cLocalAfter`, a missing `math.Ceil` on admission/arrival steps, and
   `chunk = ChunkTokens` where upstream recomputes `min(ap, ChunkTokens)`. The
   last one hid because `nChunks` is algebraically identical either way.

2. **Required analysis is discovered reactively.** `pd-infocomm/docs/` now holds
   3,713 lines of analysis written *after* the bundle: `required-signals.md`
   (1050), `signal-analysis.md` (611), `epp-flow.md` (434),
   `shared-pool-heterogeneity.md` (1618). `signal-analysis.md` states the
   protocol that should have applied during generation — "Every claim in §C was
   checked against source; every number was recomputed. Findings are stated as
   verdicts with the file and line that settles them."

3. **Unciteable prose ships as fact.** `create-sim2real-bundle.md` grounds four
   enumerated claim classes (campaign artifacts, target API, measured results,
   bundle shape) and nothing else. It is a whitelist, so any claim class it does
   not anticipate is unguarded by construction, and it has no pass that re-reads
   what it wrote. Its own closing "Key Lessons" section models unattributed
   assertion as acceptable output.

## Objective

The product is a **meaningful algorithm specification**. Citation discipline and
the correctness gate are means; they matter only because an algorithm that is
wrong, unimplementable, or indistinguishable from the baseline is not worth
transferring.

Five properties define "meaningful". The skill must establish each before the
bundle is complete.

| # | property | fails if |
|---|---|---|
| 1 | the mechanism, causally stated | code is ported with no causal story — nothing to falsify |
| 2 | the structural shape the decision requires | the target's natural decomposition silently replaces the policy |
| 3 | observability — every quantity read has a real source | the specification is fiction on a real cluster |
| 4 | isolation — a comparator that attributes the effect | improvement cannot be attributed to the mechanism |
| 5 | named threats to validity, with direction of bias | results are uninterpretable |

`pd-infocomm`'s focal-arm header already demonstrates all five, and none of that
content comes from reading code — it comes from analysis. The Go file is a
carrier for it. The skill is therefore an elicitation-and-analysis process, not
a transcription process.

Port correctness — the gate in this design — serves properties 1 and 2. It is one
row of five.

## Non-goals

Mirroring `sim2real-translate`'s own negative-scope section:

- Does not create `transfer.yaml` or `baselines/` — `bootstrap`'s job.
- Does not wire submodules — `bootstrap` Task 1 derives the ref, Task 2 adds it.
- Does not produce compiling code — `translate`'s job. The specification layer is
  non-compiling by design; its adapters remain declared unknowns.
- Does not build images or touch cluster config.
- Does not emit the deep analyses of `pd-infocomm/docs/`. Property 3 is
  established as a thin classification (below), not as a signal reference doc.

## Placement and scope

Invoked `/sim2real-specify <experiment-root>` from the sim2real repo, before
`bootstrap`.

One ordering constraint is deliberate. The skill must cite against readable sim
*and* target checkouts, but `bootstrap` is what derives the target ref and adds
the submodule, and its Final File Tree lists `algorithms/<algorithm>.go` as
pre-existing. So `specify` resolves its own pins, records them in README's
provenance table, and cites against whatever readable checkouts it was given.

## Process

**Phase 0 — Ground.** Obtain a readable sim checkout at a specific commit and a
readable target checkout at a specific ref. Verify both. Record the pins. Every
later claim is a citation against these two trees.

**Phase 1 — Mechanism (property 1).** Interview plus source reading to establish
the causal story, the decision rule, and the cited evidence that it wins: which
cells, what margin, against what baseline. Results artifacts are copied verbatim.

**Phase 2 — Shape (property 2).** Determine the structural form the decision
takes — per-endpoint score, joint argmin over a cross product, admission gate,
dispatch ceiling — and confirm the target has an extension point that can express
it. Compare explicitly against the target's *natural* decomposition; where that
decomposition is weaker, quantify what would be lost using the simulation's own
ablation.

**Phase 3 — Observability (property 3), thin.** Enumerate every quantity the
decision rule reads. Classify each as direct, derivable, degraded, or
unobtainable, each with a citation to the target or engine metric that supplies
it. This is a classification pass, not a reference document.

**Phase 4 — Isolation (property 4).** Identify the comparator arms and ablations
that isolate the mechanism, each with cited simulation evidence. Emit their
specification files.

**Phase 5 — Specify (property 5).** Write the specification layer and
`config.md`. Each degradation identified in Phase 3 is declared in the
specification layer's header with its direction of bias.

**Phase 6 — Gate.** Run the three gate components below. The bundle is not
complete until the gate passes.

## Output contract

Exactly what `08203b4` produced. The contract is observed, not invented.

| path | content | authored or copied |
|---|---|---|
| `algorithms/<arm>.go` | cited specification layer, one per arm; non-compiling by design | authored |
| `README.md` | mechanism, shape, honest status, pre-registered expectation, pins table, layout | authored |
| `config.md` | plugin parameters, engine flags, SLO targets | authored |
| `workloads/` | workload specifications | copied verbatim |
| `inputs/` | latency-law coefficients, fleet definitions | copied verbatim |
| `sim_results/` | the results the claims cite, plus `CHECKSUMS.sha256` | copied verbatim |
| `.gitignore` | standard ignores | generated |

`LIMITATIONS.md` is **not** an output. It arrived at `78df621`, after generation,
as post-hoc analysis. Property-5 content lives in the specification layer's
header, which is where `08203b4` put it: the "Honest status" and "Fidelity gap"
sections and the per-field `DEVIATION:` notes.

### Verbatim-copy principle

Anything that already exists upstream is copied, never re-expressed: workloads,
coefficients, results, and any patch carrying the validated change. Re-authoring
is reserved for the specification layer alone — the one artifact that genuinely
must change form.

This generalizes an observed asymmetry. In `create-sim2real-bundle.md`,
`scripts/treatment.patch` is copied verbatim from the winning campaign iteration
and checked with `git apply --check`; it has never been a defect source. The
re-authored plugin file has no such device, and re-authored algebra is where all
four `ead56ea` defects were.

### Specification layer

States the policy against real target interface names. Leaves adapters as
declared unknowns. Carries an upstream `file:line` citation on every non-obvious
expression. Declares its own degradations with direction of bias.

It invents no in-source marker conventions. Unresolved contract questions are
stated as questions in the README, owned by the bundle.

One rule governs how it may refer to other pipeline stages:

> A specification may **request** things of downstream stages. It may never
> **assert** what they do.

"Confirm X against the pinned checkout" is a legitimate request. "Stage Y
resolves X" is an assertion about another component's behavior, and is
unciteable unless that component's source says so.

## Gate mechanics

Three components, run in Phase 6 against the pinned checkouts.

**Citation audit — all arms.** A fresh agent receives only the bundle and the two
checkouts, with no generation transcript. It performs **correspondence
discovery**: for every non-trivial ported expression and every non-obvious prose
claim, it locates the upstream counterpart — using an existing citation as a hint
where one is present, and searching the pinned tree where none is — then returns
`CONFIRMED`, `WRONG`, or `UNSUPPORTED` with the file and line that settles it.
`WRONG` is fixed. `UNSUPPORTED` is deleted or converted to a stated open
question. The verdict format is the one `pd-infocomm/docs/signal-analysis.md`
already uses.

Correspondence discovery rather than mere citation-checking, because citation
density is itself an audit output, not an input: the pre-fix
`causal_slo_externality.go` at `08203b4` carried exactly one citation
(`pd_profile_handler.go:186`), and every citation in today's file was added by
`ead56ea`. An auditor that only verifies existing citations would have returned
clean on the file containing all four defects.

**Blind re-derivation — focal arm only.** A second agent reads only the sim source
at the pin and writes its own statement of the same policy, without seeing the
port. The two statements are diffed and every divergence adjudicated against
source. Scoped to the focal arm because that is where every observed defect was;
`ead56ea` established that both comparator arms audited clean and were unchanged.

Rationale for including this beyond the audit: an auditor reading an existing port
anchors on it. Defect 4 — which hid because `nChunks` is algebraically identical
either way — is the class a blind re-derivation surfaces and a review pass talks
itself out of.

**Citation lint — mechanical.** Extracts `path:line` patterns from the bundle and
resolves each against the checkout paths passed in at runtime, failing on paths
or line numbers that do not exist. Deterministic and cheap; catches drift on
regeneration. It cannot catch misreading — a correct citation attached to a wrong
transcription resolves fine. That is the audit's job.

**Pass condition.** Zero `UNSUPPORTED` claims remain, and every re-derivation
divergence is either resolved or recorded as a stated open question.

## Failure modes

**Hard HALT — nothing downstream is trustworthy.**

- Phase 0: sim or target checkout unreadable at the stated pin.
- Provenance disagreement: `sim_results/` do not match their checksums, or the
  sim commit is not the one that produced them. Called out explicitly because
  evidence bases are vendored from forks; if the pin and the results disagree,
  every cited margin is wrong.
- Phase 2: no extension point on the target can express the required shape. The
  specific hazard is silent fallback to the target's weaker natural
  decomposition, which transfers a different algorithm while resembling success.

**Operator decision — the skill states the case and does not decide.**

- Phase 3: a quantity carrying the core mechanism is unobtainable. Degraded may
  be acceptable with reasoning; `pd-infocomm` accepted its `S_pf` gap on the
  argument that the decode-side asymmetry dominates. That is a legitimate call
  but not the skill's to make.
- Phase 4: no comparator isolates the mechanism. The transfer may proceed; its
  results will not be attributable. Say so rather than proceeding quietly.

**Degraded output.** Partial provenance — a paper with no code, or code with no
results. A specification can still be produced, but with no results the skill
must refuse to write cited performance claims, and the pre-registered expectation
becomes a stated hypothesis with no margin attached.

**Gate non-convergence.** If `UNSUPPORTED` claims survive a bounded number of fix
iterations, stop and report them. Do not ship a bundle that claims to have been
audited.

## Structure

Mirrors `sim2real-translate`: one script, prompts, one SKILL.md. The three gate
components are independently invocable and independently testable. The lint is
the only code; everything else is prompts.

```
.claude/skills/sim2real-specify/
  SKILL.md                    # phases 0-6, interview, output contract
  prompts/audit.md            # independent citation audit
  prompts/rederive.md         # blind re-derivation, focal arm
  scripts/lint_citations.py   # mechanical path:line resolution
```

## Testing

A labeled regression fixture already exists, with ground truth established
independently and by hand.

**Sensitivity.** Run the audit on `pd-infocomm/algorithms/causal_slo_externality.go`
at `08203b4` against BLIS `871b169b`. It must find the four defects `ead56ea`
found: the dropped causal prefill-attention charge, the spurious `/100.0`, the
missing `math.Ceil`, and `ChunkTokens` versus `min(ap, ChunkTokens)`.

**Specificity.** Run the same audit on `least_ttft_joint.go` and
`kairos_paper.go` at `08203b4`. Both must come back clean, per `ead56ea`.

**Lint unit tests.** Fixture bundles covering a valid citation, a dangling path,
an out-of-range line number, and a path outside the recorded pins. The lint is
exercised on synthetic fixtures plus the *post*-fix `causal_slo_externality.go`,
which is citation-dense; the pre-fix file cannot serve as a lint fixture because
it carries only one citation.

**Acceptance run.** Regenerate the `pd-infocomm` bundle from the same provenance
(`INFOCOM_REPRODUCIBILITY.md` plus BLIS `871b169b`) and compare against
`08203b4` + `ead56ea`. Success is that the four defects never appear.

## Deferred

Both were raised during design and cut to keep v1 minimal.

- **Machine-readable `provenance.yaml`.** Pins live in README's provenance table,
  as observed practice. The lint takes checkout paths at runtime instead.
- **`bootstrap` pin-mismatch check** — verifying that bootstrap's submodule ref
  equals the pin the bundle cited against. Worth revisiting: `pd-infocomm/README.md:22`
  records exactly this drift, noting the sim2real superproject index still points
  at `583f7195` while the working checkout is `871b169b`.
