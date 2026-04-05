# Mapping Document Review Gate — Design Spec

**Date:** 2026-04-05
**Status:** Draft

## Problem

The signal mapping document (`context.mapping` in `transfer.yaml`) is currently a static,
manually-maintained file scoped to the router use case. When transferring a different algorithm
family (e.g. admission control), the mapping document either doesn't exist or contains
irrelevant signal mappings — causing translate to produce unmapped signals and halt.

There is no mechanism for the user to:
- Generate a first-draft mapping for a new algorithm family
- Review and edit the mapping before translate runs
- Provide domain context to steer LLM generation
- Override the canonical mapping for a specific run without affecting other runs

## Goals

1. Users can provide standing domain context in `transfer.yaml` that seeds LLM generation
2. At prepare time, users are shown the active mapping document and can review, edit, or chat before translate runs
3. If the mapping file doesn't exist, a generation session creates a first draft interactively
4. Edits made during a run are saved as a run-scoped override, leaving the canonical file untouched
5. Overrides are portable — copying `mapping_override.md` to another run's directory is sufficient to reuse them

## Non-Goals

- Automatic promotion of overrides back to the canonical file (user does this manually)
- Structured/machine-readable mapping format (remains a markdown document)
- Validation that the mapping covers all signals in the algorithm summary (that remains translate's job)

## Design

### 1. Transfer.yaml: `mapping_notes` field

An optional `context.mapping_notes` field provides standing LLM context for this algorithm family:

```yaml
context:
  mapping: docs/transfer/blis_to_llmd_admission_mapping.md
  mapping_notes: |                          # optional
    This is an admission control algorithm. It maintains per-tenant
    and per-class state across calls. The a.* fields are internal
    plugin state — they do not need production signal mappings.
    Focus on cluster-wide aggregate signals.
```

`mapping_notes` is fed as system context in both the generation session and chat mode.
It is not required; absent means the LLM generates from algorithm summary alone.

### 2. File resolution order

Evaluated at the start of the mapping review gate:

1. `workspace/runs/<run>/mapping_override.md` — run-specific override (created when user edits or chats)
2. `config/transfer.yaml → context.mapping` — canonical experiment-type doc
3. Neither exists → generation session

The resolved path is stored in `run_metadata.json` under `mapping_source`:
- `"override"` — run override was used
- `"canonical"` — canonical document was used

### 3. Pre-translate gate: `stage_mapping_review()`

A new function inserted between `stage_extract()` and `stage_translate()` in `prepare.py`.

#### State A — canonical file exists, no run override

```
━━━ Step 1b: Mapping document review ━━━
[INFO]  Mapping document: docs/transfer/blis_to_llmd_admission_mapping.md
[INFO]  This document guides signal translation. Review it before continuing.

  [e] Edit file directly    [c] Chat with model    [d] Done (proceed)
```

Choosing `[e]` opens the file in `$EDITOR` (fallback: prints path and waits for Enter).
Choosing `[c]` enters chat mode (see §4).
In both cases, changes are written to `workspace/runs/<run>/mapping_override.md`;
the canonical file is not modified.

#### State B — run override already exists

```
[INFO]  Mapping document: docs/transfer/blis_to_llmd_admission_mapping.md
[WARN]  Run override active: workspace/runs/admin6/mapping_override.md
[INFO]  The override will be used for translation.

  [e] Edit override    [c] Chat with model    [d] Done    [x] Drop override (revert to canonical)
```

`[x]` deletes the override file and reverts to the canonical path for this run.

#### State C — no mapping file exists

```
[WARN]  No mapping document found at docs/transfer/blis_to_llmd_admission_mapping.md
[INFO]  Let's create one. Provide context about this algorithm's domain
        (or press Enter to generate from algorithm summary alone):
> _
```

After user input (or Enter to skip), the LLM generates a full draft mapping document using:
- The `prepare_algorithm_summary.json` for this run
- Relevant source files from the submodule (scorer interface, metrics struct)
- `context.mapping_notes` from transfer.yaml (if present)
- The user's typed context (if provided)

The draft is written to the **canonical path** (`context.mapping`), then falls into State A's
`[e/c/d]` loop so the user can immediately review it.

The canonical path's parent directory is created if it doesn't exist.

### 4. Chat mode

Multi-turn conversation anchored to the current mapping document (canonical or override):

```
[INFO]  Chatting with gpt-4o about the mapping document.
[INFO]  Type your request, or 'done' to finish.
> the a.* fields are internal state — no production mapping needed
[INFO]  Updating mapping document...
> clock maps to time.Now().UnixMicro() in production
[INFO]  Updating mapping document...
> done
[OK]    Changes saved → workspace/runs/admin6/mapping_override.md
```

Each turn: user message + current document content → LLM returns the complete updated
document → written back in-place. `mapping_notes` is included as system context.

After `done` (or empty input), the gate re-presents `[e/c/d]` for a final review pass.

### 5. Impact on existing code

#### `prepare.py` — stage ordering

```python
algo_summary_path = stage_extract(run_dir, manifest, ...)
stage_mapping_review(run_dir, manifest, algo_summary_path)   # NEW
coverage_path = stage_translate(run_dir, manifest, ...)
```

`stage_mapping_review()` returns the resolved mapping path, which is passed into
`stage_translate()` instead of reading `manifest["context"]["mapping"]` directly.

#### `_extract_mapping_hash()` — path parameter

Signature changes from reading `manifest["context"]["mapping"]` to accepting
the resolved path:

```python
def _extract_mapping_hash(resolved_mapping_path: Path) -> str:
```

No logic change — still reads the pinned hash from document content.

#### `transfer_cli.py` — `validate-mapping`

Add an optional `--path` flag to allow validating an override file:

```bash
python tools/transfer_cli.py validate-mapping
python tools/transfer_cli.py validate-mapping --path workspace/runs/admin6/mapping_override.md
```

Existing behavior (no flag) is unchanged.

#### Unchanged

- Translate prompt — receives mapping document text regardless of source
- `signal_coverage.json` schema
- Hash check logic — override documents embed a pinned hash the same way canonical ones do
- `[e]dit / [c]hat / [d]one` pattern elsewhere in prepare.py

### 6. Override portability

To reuse an override in another run:

```bash
cp workspace/runs/admin6/mapping_override.md workspace/runs/admin7/mapping_override.md
```

To promote an override to canonical (shared across all runs of this algorithm type):

```bash
cp workspace/runs/admin6/mapping_override.md docs/transfer/blis_to_llmd_admission_mapping.md
```

### 7. Summary of changes

| What | Location | Change |
|------|----------|--------|
| `mapping_notes` field | `config/transfer.yaml` | New optional field |
| `stage_mapping_review()` | `scripts/prepare.py` | New function, ~150 lines |
| File resolution | `scripts/prepare.py` | Override-before-canonical lookup |
| `_extract_mapping_hash()` | `scripts/prepare.py` | Accept resolved path parameter |
| `stage_translate()` | `scripts/prepare.py` | Accept resolved path parameter |
| `validate-mapping` | `tools/transfer_cli.py` | Add optional `--path` flag |
| Override file | `workspace/runs/<run>/mapping_override.md` | New artifact (gitignored) |
| `run_metadata.json` | `workspace/runs/<run>/` | Add `mapping_source` field |
