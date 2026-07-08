# Issue 402: Refresh stale transfer.yaml schema block in pipeline/README.md

**Goal:** Replace the stale `target:` / `config:` / `build:` / `epp_image:` block in `pipeline/README.md` (currently lines 756-771) with the `component:` shape the loader (`pipeline/lib/manifest.py:171-233`) has actually required since PR #109 landed on 2026-05-12.

**Architecture:** One-file docs change. No code, no tests to add — the validation is that a `transfer.yaml` copy-authored from the fixed schema block loads cleanly through `pipeline/lib/manifest.py:load_manifest` with `algorithms:` populated.

## Global Constraints

- The schema block MUST reflect what `pipeline/lib/manifest.py:171-233` actually accepts:
  - `component.repo`: required, string.
  - `component.kind`: required, string.
  - `component.path`: optional, string; defaults to the last segment of `repo` when absent.
  - `component.ref`: optional, non-empty string.
  - `component.base_image`: optional, mapping requiring `hub` and `name` — NO `tag` field (image tags now come from `translation_output.json:algorithms[i].image_ref`, per #403).
  - `component.build`: optional, mapping; `commands` list; `build.image` optional mapping with `hub` required (defaults from `base_image.hub` if present).
- `component:` itself is REQUIRED when at least one algorithm entry omits `byo: true`; optional for all-BYO manifests. This nuance is stated in prose at README:37 and README:48 and must be reflected in the schema block's inline comment.
- Preserve the `pipeline:` block (README:772-774) and the `blis_observe:` block (README:776-781) that follow — both are current and correct.
- Do NOT restructure the rest of the schema example (kind, version, scenario, baselines, algorithms, workloads, context, defaults) — those parts are still accurate.
- No changes to code, tests, or other docs unless the sweep in Step 3 uncovers a stale reference tied to the four removed top-level keys (`target`, `config`, `build`, `epp_image`).

## Acceptance criteria (from #402)

- [x] The "v3 fields" block in `pipeline/README.md` describes `component:` (and its sub-fields) instead of `target:` / `config:` / `epp_image:`.
- [x] A `transfer.yaml` authored against the README, with `algorithms:` populated, loads cleanly through `pipeline/lib/manifest.py:load_manifest` (no `Missing required field` errors, no silently-ignored fields).

---

## Task 1: Replace the stale block

**File:** `pipeline/README.md` (worktree copy only)

Replace lines 756-771 (the `# v3 fields (required unless noted)` header plus the four stale sub-blocks) with the following, in the same commenting/indentation style as the neighboring `baselines:` and `algorithms:` blocks:

```
# v3 fields (required unless noted)
component:                    # required when algorithms[] contains any non-BYO entry;
                              # optional if every algorithm carries `byo: true`.
  repo: <name>                # component repo name (matches submodule directory in the sim2real repo)
  kind: <string>              # component kind (e.g. "EndpointPickerConfig")
  path: <string>              # optional — defaults to the last segment of `repo`
  ref: <string>               # optional — tag, branch, or commit SHA identifying the expected component version
  base_image:                 # optional — base image the built EPP layers on top of
    hub: <registry>           # e.g. ghcr.io/llm-d
    name: <repo>              # e.g. llm-d-inference-scheduler
  build:                      # optional — build overrides
    commands: []              # component build commands (list of argv-style commands)
    image:                    # optional — override coords for the built EPP image
      hub: <registry>         # defaults to base_image.hub when set
      name: <repo>            # defaults to base_image.name when set
```

Do NOT touch the surrounding lines. Specifically preserve:
- The empty line separating the schema block from the `defaults:` block above.
- The `pipeline:` block (currently at 772-774).
- The `blis_observe:` block (currently at 776-781).
- The closing ``` fence and the prose after it (currently at 784+), including the sentence at line 786 ("`component.ref` (optional): tag, branch, or commit SHA ...") — that sentence is now redundant with the schema comment but leaving it alone avoids scope creep.

## Task 2: Verify the acceptance criterion

Write a minimal `transfer.yaml` matching the fixed schema block (with all required fields present) to `/tmp` and load it through `pipeline/lib/manifest.py:load_manifest`. Assert no exception is raised and that `component.repo`, `component.kind`, and every algorithm are present in the loaded dict.

## Task 3: Sweep for stale references

Grep for the four removed top-level keys across `**/*.md` and `docs/`:

```bash
grep -rn "^target:" docs/ *.md **/*.md 2>/dev/null
grep -rn "^epp_image:" docs/ *.md **/*.md 2>/dev/null
grep -rn "epp_image\|target:\s*$\|config:\s*$" pipeline/README.md CLAUDE.md docs/ .claude/skills/ 2>/dev/null
```

For each hit, decide: stale (update in this PR), still accurate (leave), unrelated (leave). Bare `config:` / `build:` hits are noise (they collide with many YAML keys); look at context before flagging. Note in the PR body what was swept and what changed.

## Task 4: Commit, push, open PR

Single commit. Message body: reference #402, note that #109 introduced the drift and this brings the README back in sync.

PR title: `docs: refresh transfer.yaml v3 fields block to component: shape (fixes #402)`.
PR body: reference #402 with `Closes #402`, summarize the swap, note the sweep result, and — if anything besides `pipeline/README.md` changed — call it out.
