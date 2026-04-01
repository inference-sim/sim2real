# Prepare Pipeline Gap Discovery Design

## Goal

Enhance the prepare pipeline's Extract and Translate stages to automatically discover and surface gaps — attributes and production capabilities that exist but are not used by the algorithm being transferred. Surface these gaps as human decision points in the review gates.

## Motivation

The current pipeline accurately extracts what the algorithm uses and maps those signals to production. But it has a blind spot: it doesn't report what the algorithm *doesn't* use. This means the human reviewer has no visibility into production capabilities (e.g., SLOClass-based admission, prefix cache hit rate metrics) that could be relevant but are absent from the evolved algorithm. Without this, the human must independently know the full production surface to judge whether the transfer is complete enough.

## Architecture

Two parallel gap discovery paths, one per stage:

1. **Extract (Stage 1)** — Sim-side gaps: source-file attributes outside the EVOLVE-BLOCK
2. **Translate (Stage 2)** — Prod-side gaps: production capabilities not mapped by the algorithm

Both are LLM-driven (no mechanical scanner). The LLMs already read the relevant codebases during their primary tasks; the prompts are expanded to also report what they find that the algorithm doesn't use.

Gaps are informational — they don't halt the pipeline. But they are surfaced as decision points in the human gates: the human must acknowledge them (proceed), provide context (chat), or halt.

### Backwards Compatibility Note

Both `algorithm_summary.schema.json` and `signal_coverage.schema.json` use `additionalProperties: false`. This means the new fields **must** be added to each schema for validation to pass. However, the fields are defined as optional (not in `required`), so existing artifacts without these fields remain valid. New artifacts may include them as empty arrays or omit them entirely — both pass validation.

## Extract Stage Changes

### Prompt change (`prompts/extract-full.md`)

Add a new section to the extraction prompt:

> **Section 5: Report Unused Source Attributes**
>
> Scan the full source file (the file containing the EVOLVE-BLOCK) for class attributes, state variables, configuration parameters, and fields on key types (e.g., Request, RouterState, Instance) that exist in the source file but are NOT referenced within the EVOLVE-BLOCK. Report these as potential gaps — they represent algorithm inputs that could affect routing/scoring but are ignored by this algorithm.
>
> **Scope:** Only scan the file that contains the EVOLVE-BLOCK. Do not scan other files in the repository.
>
> For each, report: name, type (Go type if determinable, otherwise "unknown"), location (file:line), and a brief description of what it represents.

### Schema change (`tools/schemas/algorithm_summary.schema.json`)

Add optional field to the top-level `properties` (not to `required`). Because the schema uses `additionalProperties: false`, this field **must** be added for new artifacts containing it to pass validation.

```json
"unused_source_attributes": {
  "type": "array",
  "items": {
    "type": "object",
    "required": ["name", "type", "location", "description"],
    "additionalProperties": false,
    "properties": {
      "name": { "type": "string", "description": "Attribute or field name" },
      "type": { "type": "string", "description": "Go type if determinable, otherwise 'unknown'" },
      "location": { "type": "string", "description": "file:line location in source" },
      "description": { "type": "string", "description": "Brief description of what it represents" }
    }
  }
}
```

The field is **not** added to the top-level `required` array. Existing artifacts without this field remain valid. New artifacts may include it as an empty array or omit it entirely.

The `required` array inside each item includes all four fields (`name`, `type`, `location`, `description`) to match what the prompt asks the LLM to report. This ensures structured, complete gap entries.

### Human gate behavior

Display gap information when `unused_source_attributes` is **present in the artifact AND non-empty** (length > 0). If the field is absent or is an empty array, no extra messaging — gate behaves as today.

When displayed, show before the gate menu:

```
  Sim-side gaps (attributes available but unused by algorithm):
    - SessionID (string) — Session affinity identifier
    - SLOClass (string) — SLO tier classification

  These source-file attributes exist but are NOT captured by the EVOLVE-BLOCK.
  If any are important to the algorithm's behavior, the extraction may be incomplete.

  [e] Edit    [c] Chat    [d] Done (accept gaps)    [q] Quit (halt)
```

Chat lets the human provide context (e.g., "SessionID is used for affinity, include it"). Done = accept gaps and proceed. Quit = halt.

### AI review prompt update

The extract review prompt is an inline string in `scripts/prepare.py` (around line 267). Add a new numbered check:

> 5. Check whether the `unused_source_attributes` field captures all relevant attributes from the source file that the EVOLVE-BLOCK does not reference.

## Translate Stage Changes

### Prompt change (`prompts/translate.md`)

Add a new step to the translation prompt:

> **Report Production Gaps**
>
> As you navigate the production codebase (`llm-d-inference-scheduler/`) to map signals, also note any metrics fields, request headers, CRD spec fields, or plugin types you encounter that are NOT needed by this algorithm. These represent production capabilities available in llm-d that the algorithm doesn't leverage.
>
> **Scope:** Only report capabilities you encounter while navigating the production codebase to complete the signal mapping. This is not an exhaustive audit of the entire production surface — report what you naturally discover during your mapping work. Focus on the scorer plugin interface, endpoint metrics, request metadata, and CRD spec fields.
>
> For each, report: name, category (header/metric/crd_field/plugin_type/config_option), production_location (file path within llm-d-inference-scheduler), and a brief description.

### Schema change (`tools/schemas/signal_coverage.schema.json`)

Add optional field to the top-level `properties` (not to `required`). Because the schema uses `additionalProperties: false`, this field **must** be added for new artifacts containing it to pass validation.

```json
"production_gaps": {
  "type": "array",
  "items": {
    "type": "object",
    "required": ["name", "category", "production_location", "description"],
    "additionalProperties": false,
    "properties": {
      "name": { "type": "string", "description": "Capability name" },
      "category": {
        "type": "string",
        "enum": ["header", "metric", "crd_field", "plugin_type", "config_option"],
        "description": "Category of production capability"
      },
      "production_location": { "type": "string", "description": "File path within llm-d-inference-scheduler" },
      "description": { "type": "string", "description": "Brief description of the capability" }
    }
  }
}
```

The field is **not** added to the top-level `required` array. Existing artifacts without this field remain valid. New artifacts may include it as an empty array or omit it entirely.

The `required` array inside each item includes all four fields (`name`, `category`, `production_location`, `description`) to match what the prompt asks the LLM to report.

### Human gate behavior

Display gap information when `production_gaps` is **present in the artifact AND non-empty** (length > 0). If the field is absent or is an empty array, no extra messaging — gate behaves as today.

When displayed, show before the gate menu:

```
  Production-side gaps (available in llm-d but unused by algorithm):
    - x-gateway-inference-objective (header) — priority-based admission
    - PrefixCacheHitRate (metric) — prefix cache hit rate signal

  These production capabilities are NOT covered by this algorithm's translation.
  If any are critical to your use case, the generated plugin may be incomplete.

  You can provide additional context to help the AI refine the mapping,
  or halt if the translation won't be useful without these capabilities.

  [e] Edit    [c] Chat    [d] Done (accept gaps)    [q] Quit (halt)
```

Same pattern as Extract: chat for context, done to accept, quit to halt.

### AI review prompt update

The translate review prompt is an inline string in `scripts/prepare.py` (around line 574). Add a new numbered check:

> 6. Check whether `production_gaps` captures relevant production capabilities that are NOT mapped by this algorithm. Consider headers, metrics, CRD fields, and plugin extension points.

## Data Flow

Gap information flows: LLM prompt → artifact JSON → prepare.py → human_gate.

1. The LLM writes gap entries into the artifact (algorithm_summary.json or signal_coverage.json).
2. `prepare.py` reads the artifact and extracts the gap list field. If the field is absent or `null`, it passes `None`. If present (including empty `[]`), it passes the list as-is.
3. `prepare.py` passes the gap list to `human_gate` via a new optional `gaps: list[dict] | None = None` parameter.
4. `human_gate` owns the display decision: it displays gaps only when `gaps` is not `None` and has length > 0. This keeps the condition check in one place.

## Implementation Scope

| File | Change |
|------|--------|
| `prompts/extract-full.md` | Add Section 5: report unused source attributes |
| `prompts/translate.md` | Add step: report production gaps while mapping |
| `scripts/prepare.py` (extract review prompt, ~line 267) | Add check #5 for `unused_source_attributes` completeness |
| `scripts/prepare.py` (translate review prompt, ~line 574) | Add check #6 for `production_gaps` completeness |
| `scripts/prepare.py` (extract gate call, ~line 293) | Read `unused_source_attributes` from artifact, pass to `human_gate` |
| `scripts/prepare.py` (translate gate call, ~line 601) | Read `production_gaps` from artifact, pass to `human_gate` |
| `scripts/lib/gates.py` | Add optional `gaps: list[dict] | None = None` parameter to `human_gate`; display before gate menu when non-empty |
| `tools/schemas/algorithm_summary.schema.json` | Add optional `unused_source_attributes` array to `properties` |
| `tools/schemas/signal_coverage.schema.json` | Add optional `production_gaps` array to `properties` |

## What This Does NOT Cover

- **Deeper validation** (state/control flow audit, data flow trace, error handling audit) — deferred to a separate design focused on validation depth
- **Cross-run persistence** — gaps are per-run only, no persistent known_gaps file
- **Mechanical scanning** — purely LLM-driven discovery, no grep-based scanner
- **Automatic remediation** — gaps are surfaced for human decision, not auto-fixed

## Testing

- Existing prepare tests (`test_prepare_snapshot.py`) should pass unchanged (new fields are optional)
- Add test cases for `human_gate` with non-empty gaps parameter
- Add schema validation tests for artifacts with and without the new fields
