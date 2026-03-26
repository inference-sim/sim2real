# Design: Remove `llm-d-benchmark` Submodule

**Date:** 2026-03-26
**Issue:** https://github.com/kalantar/sim2real/issues/38

## Summary

Remove the `llm-d-benchmark` submodule from the sim2real repository. The submodule is no longer needed — benchmark configuration is managed via the Tekton pipeline and `config/env_defaults.yaml` / `workspace/tekton/values.yaml` artifacts. No files inside the submodule directory are modified during the transfer pipeline.

## Changes

### Git operations
1. `git submodule deinit -f llm-d-benchmark` — unregister the submodule
2. `git rm llm-d-benchmark` — remove from index and update `.gitmodules`
3. `rm -rf .git/modules/llm-d-benchmark` — remove cached git objects

### File edits

| File | Change |
|------|--------|
| `CLAUDE.md` | Remove `llm-d-benchmark/` from Repository Structure section |
| `prompts/pr.md` | Remove Step 6 (llm-d-benchmark PR, conditional); clean up Halt Conditions table (remove Step 6 references); remove llm-d-benchmark PR URL from Expected Outputs |
| `docs/submodule-update-guide.md` | Remove the `llm-d-benchmark` section and its table row |

### Not changed
- Archival plan docs in `docs/plans/` and `docs/contributing/` (historical references, no action needed per issue)
