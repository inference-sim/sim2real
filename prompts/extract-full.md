---
stage: 1
version: "2.0"
pipeline_commit: "set-at-runtime"
description: "Stage 1 — Enhanced extraction with full-file and cross-file context"
---

# Stage 1: Enhanced Extract

Extract algorithm metadata from routing artifacts by reading the **entire source file**
(not just the EVOLVE-BLOCK) and following cross-file references to types, helpers,
and dependencies.

## Inputs

You will be given:
1. The full contents of the algorithm source file (e.g., `best_program.go`)
2. The EVOLVE-BLOCK boundaries (between `// EVOLVE-BLOCK-START` and `// EVOLVE-BLOCK-END` markers)
3. The existing `algorithm_summary.json` from the base extraction (if available)
4. Access to the repository for reading referenced files

## Task

Enhance the algorithm summary with full-context information:

### 1. Identify Cross-File Dependencies

Read the full source file. For any types, functions, or constants referenced within
the EVOLVE-BLOCK that are defined **outside** the block:

- **Types**: Struct types used as parameters or in scoring logic
- **Helpers**: Functions called from within the EVOLVE-BLOCK
- **Constants**: Named constants that affect scoring behavior

Follow `import` statements and type references to their definitions in other files
within the same package or nearby packages.

### 2. Populate Enhanced Fields

Update `workspace/algorithm_summary.json` with:

- **`type_refs`**: Array of type definitions referenced by the EVOLVE-BLOCK.
  Each entry: `{name, file_path, fields: [{name, type}]}`

- **`helper_refs`**: Array of helper functions called from the EVOLVE-BLOCK.
  Each entry: `{name, file_path, signature, returns}`

- **`cross_file_deps`**: Array of cross-file symbol dependencies.
  Each entry: `{symbol, file_path, usage_note}`

### 3. Validate Signal Completeness

Cross-check the signals in `algorithm_summary.json` against **all** signal accesses
in the full EVOLVE-BLOCK. Report any signals that appear in the code but are missing
from the `signals` array.

### 4. Output

Write the updated `workspace/algorithm_summary.json` with the enhanced fields.
Preserve all existing fields from the base extraction. The output must pass
schema validation:

```bash
.venv/bin/python tools/transfer_cli.py validate-schema workspace/algorithm_summary.json
```

### 5. Report Unused Source Attributes

Scan the full source file (the file containing the EVOLVE-BLOCK) for class attributes,
state variables, configuration parameters, and fields on key types that exist in the
source file but are NOT referenced within the EVOLVE-BLOCK.

**Scope:** Only scan the file that contains the EVOLVE-BLOCK. Do not scan other files
in the repository.

For each, report in the `unused_source_attributes` array:
- `name`: attribute or field name
- `type`: Go type if determinable, otherwise `"unknown"`
- `location`: `"file:line"` location in the source
- `description`: brief description of what it represents

If no unused attributes are found, omit the field or set it to an empty array.

## Important

- Do NOT modify the core extracted fields (signals, composite_signals, metrics, etc.)
  unless you find a clear error.
- The new fields (`type_refs`, `helper_refs`, `cross_file_deps`) are **optional** in
  the schema — only populate them if relevant references exist.
- Always run schema validation after writing the file.
