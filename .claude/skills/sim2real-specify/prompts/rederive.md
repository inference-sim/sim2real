# Blind re-derivation

Derive an algorithm's decision rule from simulation source alone. You will NOT be
shown any existing port, and you must not look for one. If you encounter a file
that appears to be a port of this policy, stop reading it and continue from the
simulation source.

## Inputs

- Simulation checkout: `{SIM_TREE}` pinned at `{SIM_PIN}`
- Entry points for the policy: `{POLICY_ENTRY_POINTS}`
- Arm name: `{ARM_NAME}`
- Write your derivation to: `{DERIVATION_PATH}`

## Method

Read the entry points and follow every call they make that contributes to the
decision. Then write down, as a flat list of named terms, the complete
computation: what is summed, multiplied, compared, rounded, skipped, and returned.

For each term record:

- `name` -- a short identifier you choose
- `expression` -- the algebra, in terms of other names you have defined
- `upstream` -- the `path:line` in `{SIM_TREE}` it comes from
- `units` -- microseconds, tokens, requests, dimensionless
- `notes` -- anything a reimplementation would get wrong

Rules that matter for this comparison:

1. Be exhaustive about ADDITIVE terms. If the upstream sums four contributions,
   list four. Omission is the defect class this exercise exists to catch.
2. Record every rounding operation as its own note. Floor, ceil, and truncate are
   not interchangeable.
3. Record any quantity the upstream recomputes at each call site, and say what it
   is clamped to. Do not summarize it as a constant.
4. Record guards that skip a term -- censoring, sentinel values, early
   `continue` -- and what the skipped contribution would have been.
5. Where the upstream selects between branches by a flag, follow the branch this
   policy actually forces and say which flag forces it. Ignore unreachable
   branches, and note that you ignored them.
6. Note every quantity that comes from simulator-internal state with no
   real-cluster analogue. Do not invent a substitute.

## Output

Write JSON to `{DERIVATION_PATH}`:

```json
{
  "arm": "{ARM_NAME}",
  "terms": [
    {
      "name": "decodeIterationTime",
      "expression": "alpha + c0*batchSize + c1*kvTokens + cPf*prefillTokens",
      "upstream": "sim/example_coeffs.go:41",
      "units": "microseconds",
      "notes": "recomputed per candidate under that candidate's own coefficients"
    }
  ]
}
```

When done, send one message to `{MAIN_SESSION_NAME}`:
`rederive-complete: {ARM_NAME} <n> terms`

Do not edit any file other than `{DERIVATION_PATH}`.
