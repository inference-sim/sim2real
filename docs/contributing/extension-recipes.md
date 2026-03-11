# sim2real Extension Recipes

Step-by-step guides for extending the sim2real transfer pipeline. Each recipe lists the exact files to touch, the order, and what to verify.

## 1. Adding a New Transfer Type

To add a new transfer type (e.g., admission policy transfer alongside routing):

1. **Create mapping artifact** in `docs/transfer/<type>/mapping-artifact.md`
   - Signal mapping table: sim signal -> prod signal, fidelity rating, staleness window, access path
   - API reference section: target system types, function signatures at pinned commit
   - Overlap analysis: which existing target system components does this interact with?

2. **Create scorer template** in `docs/transfer/<type>/scorer-template.md`
   - Target system plugin conventions for this policy type
   - Plugin interface description (what methods to implement)
   - Config file format (enabled/disabled, weights, parameters)
   - Required test patterns (unit test, no-op test, overlap test)

3. **Create prompt templates** in `prompts/`:
   - `prompts/<type>-stage-2-translate.md` -- signal mapping guidance for this type
   - `prompts/<type>-stage-3-generate.md` -- code generation guidance
   - `prompts/<type>-stage-5-validate.md` -- validation specifics (Suite A tuple design for this type)
   - Each prompt must have: prerequisites, validation steps, halt conditions, expected outputs

4. **Add CLI support** in `tools/transfer_cli.py`:
   - `extract` command: handle this type's input artifact format
   - `validate-mapping`: validate this type's mapping artifact structure
   - Update JSON schemas in `tools/schemas/` for any new artifact fields

5. **Add harness support** in `tools/harness/`:
   - Equivalence test adapter for this type's algorithm structure
   - Suite A tuple generator for this type's signal patterns
   - Suite B staleness injection for this type's relevant signals

6. **Update documentation**:
   - Add to `docs/contributing/index.md` workflow tables
   - Add to mapping artifact validation in `tools/transfer_cli.py`
   - Create `docs/transfer/<type>/README.md` with quick start

**Touch points:** ~10+ files (heaviest extension type). Consider splitting across multiple PRs per the macro plan template.

## 2. Adding a New Signal Mapping

To add a new signal to an existing mapping artifact (e.g., adding cache_hit_rate to the routing mapping):

1. **Update mapping artifact** in `docs/transfer/<type>/mapping-artifact.md`:
   - Add row to signal mapping table
   - Specify: sim_field, prod_field, prod_type, access_path, fidelity rating, staleness_window_ms, mapping_notes
   - Verify access_path against actual target submodule code at pinned commit

2. **Update JSON schema** in `tools/schemas/signal_coverage.schema.json`:
   - Add new signal to the allowed signal names if using an enum
   - Or ensure the schema allows extensible signal lists

3. **Update prompt templates** if the signal requires special handling:
   - Stage 2 translate prompt: add guidance for mapping this signal
   - Stage 3 generate prompt: add guidance for accessing this signal in generated code

4. **Add Suite A test tuples** that exercise the new signal:
   - Include boundary values if the signal appears in algorithm conditionals
   - Include extreme values (zero, max) to verify access path correctness

5. **Verify**: Run `python tools/transfer_cli.py validate-mapping` to confirm structural completeness

**Touch points:** ~2-4 files (lightest extension type). This is the recommended "first contribution" for new contributors.

## 3. Adding a New Validation Suite

To add a new validation suite (e.g., Suite D for latency sensitivity testing):

1. **Define the suite** in `docs/contributing/standards/experiments.md`:
   - What does this suite test? (What analysis question?)
   - What are the pass criteria?
   - What are the input requirements?
   - Where does it fit in the validation hierarchy (after C? parallel to B?)

2. **Implement the harness test** in `tools/harness/`:
   - New test function: `TestEquivalence_SuiteD` (or similar naming)
   - Input: tuples JSON, code under test
   - Output: per-tuple results + aggregate verdict
   - Must output JSON to stdout for pipeline consumption

3. **Update JSON schema** in `tools/schemas/validation_results.schema.json`:
   - Add `suite_d` field to validation_results.json schema
   - Define required sub-fields (passed, per-tuple metrics, aggregate metrics)

4. **Update CLI** in `tools/transfer_cli.py`:
   - If the suite requires CLI support (e.g., new benchmark mode), add a command
   - Update `validate-schema` to accept the new schema

5. **Update prompt templates**:
   - Stage 5 validate prompt: add instructions for running the new suite
   - Include halt conditions if the new suite fails

6. **Update transfer validation workflow** in `docs/contributing/transfer-validation.md`:
   - Add the new suite to the appropriate step
   - Update quality gates

**Touch points:** ~5-6 files.

## 4. Adding a New Target System

To add a new target system (e.g., SGLang alongside llm-d):

1. **Add submodule**: `git submodule add <repo-url> submodules/<system-name>`

2. **Create mapping artifact** in `docs/transfer/routing/mapping-artifact-<system>.md`:
   - Map all signals to the new system's API types
   - Pin the submodule commit hash
   - Document the new system's plugin/extension mechanism

3. **Create scorer template** in `docs/transfer/routing/scorer-template-<system>.md`:
   - New system's plugin conventions
   - Config file format
   - Required test patterns

4. **Update prompt templates** or create system-specific variants:
   - Stage 3 generate prompt may need different guidance for the new system's conventions
   - Stage 4 test prompt may need different build/test commands

5. **Update CLI** in `tools/transfer_cli.py`:
   - Add `--target-system` flag or equivalent system selection
   - Update path resolution for the new submodule

6. **Update Go harness** if the new system requires different equivalence testing:
   - May need a new test adapter
   - Suite A/B/C test infrastructure should be reusable; only the harness entry point changes

7. **Update documentation**:
   - README.md with new system information
   - docs/contributing/index.md with updated quick start commands

**Touch points:** ~8-10 files. Consider a dedicated macro plan for a new target system.

## 5. Adding a New Workspace Artifact

To add a new inter-stage artifact (e.g., a dependency graph between generated files):

1. **Define the schema** in `tools/schemas/<artifact_name>.schema.json`:
   - All required fields with types
   - Validation constraints (non-empty strings, allowed enum values)
   - Document the producing stage and consuming stage(s)

2. **Update the producing stage** (prompt template + CLI if applicable):
   - Prompt: add instructions for generating the artifact
   - CLI: add validation command if the artifact has complex validation rules
   - Ensure the artifact is written to `workspace/<artifact_name>.json`

3. **Update the consuming stage**:
   - Add input validation: artifact exists, parses correctly, required fields present
   - Add halt condition if artifact is missing or malformed

4. **Update inter-stage contract documentation** in the macro plan or design doc:
   - Add to the workspace artifact table (Writer stage, Reader stage, Required Fields)

5. **Verify**: Trace the Writer->Reader chain completely. Every field the reader needs must be written by the producer.

**Touch points:** ~3-4 files.

## 6. Updating the Scorer Template After Target System Changes

When the target system changes plugin conventions (e.g., new scorer interface version, different config format):

1. **Update submodule**: `cd submodules/<system>; git fetch; git checkout <new-commit>`

2. **Update mapping artifact** commit pin to the new commit

3. **Review API changes**: `git diff <old-commit>..<new-commit> -- pkg/plugins/` (or equivalent path)
   - Identify changed type signatures, renamed fields, new required methods

4. **Update scorer template** to reflect new conventions:
   - Update plugin interface description
   - Update config format if changed
   - Update required test patterns

5. **Update signal access paths** in the mapping artifact if metric APIs changed

6. **Run validation**: `python tools/transfer_cli.py validate-mapping` with updated artifacts

7. **Update any affected prompt templates** that reference specific API patterns

**Touch points:** ~3-5 files depending on scope of target system changes.

---

## Cross-Cutting Verification

After any extension, verify:
- `python tools/transfer_cli.py validate-mapping` passes (if mapping changed)
- `python tools/transfer_cli.py validate-schema` passes for all modified schemas
- `python -m pytest tests/` passes
- `go test ./tools/harness/...` passes (if harness changed)
- `go build ./tools/harness/...` passes (if harness changed)
- All cross-references between artifacts are consistent (self-audit dimension 5)
