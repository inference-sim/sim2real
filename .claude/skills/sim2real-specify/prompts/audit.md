# Independent specification audit

You are auditing ONE algorithm specification in a sim2real bundle. You have no
generation transcript and must not ask for one. Your only sources of truth are the
two checkouts named below. Treat the bundle as a claim to be tested.

## Inputs

- Bundle root: `{BUNDLE_ROOT}`
- Specification under audit: `{ARM_FILE}` (arm name: `{ARM_NAME}`)
- Simulation checkout: `{SIM_TREE}` pinned at `{SIM_PIN}`
- Target component checkout: `{TARGET_TREE}` pinned at `{TARGET_PIN}`
- Write your verdict to: `{VERDICT_PATH}`

## Method: correspondence discovery

Do NOT assume the specification's citations are complete. Citation density is an
audit OUTPUT, not an input.

For every non-trivial ported expression in `{ARM_FILE}` -- every arithmetic
formula, every unit conversion, every rounding decision, every loop bound, every
guard that skips or censors a term -- do this:

1. Locate the upstream counterpart. If the expression carries a citation, use it
   as a hint and verify it points where it claims. If it carries none, SEARCH the
   simulation checkout for the corresponding code by symbol name, by formula
   shape, and by the surrounding concept. Report `UNSUPPORTED` only after
   searching, never merely because a citation was absent.
2. Compare term by term. Enumerate the upstream terms and the ported terms and
   check the sets match. A missing additive term is the most common defect and
   the easiest to overlook, because the remaining terms look correct.
3. Check units explicitly. A name is not a unit. If the upstream reads a metric,
   find that metric's definition in the target or engine checkout and confirm the
   scale factor. Never trust a prose mapping document over source.
4. Check rounding and boundary handling. Whether a quantity is truncated,
   floored, or ceiled changes results and is invisible in aggregate.
5. Check quantities recomputed per call site upstream. If upstream derives a
   value locally at each use (for example a per-request budget clamped to a
   request-specific size), a ported constant is a defect even when an algebraically
   equivalent downstream quantity hides it.
6. Decide whether the code is PORTED or BRIDGE, and audit it accordingly.
   - PORTED: the simulation computes this quantity, so a counterpart exists.
     Compare against it. This includes quantities the simulation reads directly
     from its own state -- if the simulation knows the value exactly and the port
     obtains it some other way, the counterpart still exists and the acquisition
     is what you are checking. A unit error here is `WRONG`, not `BRIDGE`.
   - BRIDGE: the code exists ONLY because the target lacks the simulation's
     state, so no counterpart exists and none should. Do not report it
     `UNSUPPORTED` -- absence of a counterpart is the expected finding. Instead
     verify its assumptions against the target or engine checkout, and check that
     the specification header declares the degradation AND the direction of the
     resulting bias. Record that header line in `declared_at`. If no declaration
     exists, emit the finding with `declared_at: null`.

Then audit the prose. For each non-obvious claim in `{BUNDLE_ROOT}/README.md` and
`{BUNDLE_ROOT}/config.md`, resolve it against a checkout. Apply one rule:

> A specification may REQUEST things of downstream stages. It may never ASSERT
> what they do.

A sentence asserting the behavior of another pipeline stage, tool, or agent is
`UNSUPPORTED` unless that component's own source says so. Verify against the
component's source, including any section declaring what it does not do.

## Verdicts

Emit one finding per checked claim, using exactly these kinds:

- `CONFIRMED` -- the port matches upstream, or the prose claim is substantiated.
- `WRONG` -- a counterpart exists and the port disagrees with it. State the
  correct form.
- `UNSUPPORTED` -- no counterpart could be found after searching, AND the claim
  purports to describe one. The claim rests on nothing.
- `BRIDGE` -- no counterpart by construction, per method step 6. Set
  `declared_at` to the specification-header line declaring the degradation and its
  direction of bias, or `null` if no such declaration exists.

Do NOT use `UNSUPPORTED` for bridge code. Absence of a counterpart is that code's
expected property, not evidence against it.

Every finding MUST carry `settled_by`: the `path:line` in a checkout that settles
it. For `BRIDGE`, that is the target or engine line establishing what the code can
actually observe. A finding without `settled_by` is not a finding.

Be specific about consequence. "Units are wrong" is not useful; "the divisor
under-counts by 100x, collapsing the term that carries hardware heterogeneity" is.

## Output

Write JSON to `{VERDICT_PATH}`:

```json
{
  "arm": "{ARM_NAME}",
  "findings": [
    {
      "kind": "WRONG",
      "file": "algorithms/example.go",
      "line": 335,
      "symbol": "enclosingFunctionName",
      "claim": "what the specification says or computes",
      "upstream": "sim/example.go:168",
      "settled_by": "vllm/v1/metrics/loggers.py:563",
      "declared_at": null,
      "detail": "what is wrong and what the correct form is, with consequence"
    }
  ]
}
```

`file` is the bundle-relative path. `line` is the line in `{ARM_FILE}`, or in the
prose file for a prose claim. `declared_at` is required on `BRIDGE` findings and
null elsewhere.

When done, send one message to `{MAIN_SESSION_NAME}`:
`audit-complete: {ARM_NAME} <n> CONFIRMED, <n> WRONG, <n> UNSUPPORTED, <n> BRIDGE`

Do not edit any file other than `{VERDICT_PATH}`. Fixing is not your job.
