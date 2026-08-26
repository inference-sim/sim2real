# Issue 858: build.commands Discovery Procedure — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `/sim2real-bootstrap` Task 5's `component.build.commands` placeholder (`<derived from algorithm imports>`) with a component-driven discovery procedure plus scoping strategies that name no specific commands, and make an unavailable component submodule a hard stop rather than a silent guess.

**Architecture:** Pure prose change to one skill file, plus one regression test. `component.build.commands` has exactly one consumer — the `/sim2real-translate` writer's build/test gate (`sim2real-translate/SKILL.md:131`, `:307`; `prompts/agent-writer.md:219`). The pipeline only type-checks it (`pipeline/lib/manifest.py:216-224`). So the field is the translation's whole verification gate; the wording must optimise for *failing when the translation is wrong*, not merely when it does not compile. Discovery reads the component submodule, which creates a precondition Task 2 does not currently enforce.

**Tech Stack:** Markdown (skill prompt), Python 3.10+ / pytest (regression guard)

**Spec:** GitHub issue #858 (`gh issue view 858`). Sibling #859 covers `context.text` — disjoint, not in this plan. Predecessor #829 closed as superseded.

## Global Constraints

- Scope is `.claude/skills/sim2real-bootstrap/SKILL.md` Task 5 (plus Task 2's failure branch, required by the discovery precondition). **No pipeline behaviour change.**
- **No specific commands may be hardcoded into the skill.** `make lint`, `go test ./pkg/...`, `golangci-lint run` etc. must not appear as normative output. The list is driven by the component submodule so it works for components other than `llm-d-router`.
- No changes to `/sim2real-specify` — `build.commands` is discovered from the component repo, which specify never writes (`sim2real-specify/SKILL.md:49`, `:258`).
- `--byo` mode is untouched: `byo.py` never writes `build.commands`, so BLIS Task 5 is the only producer.
- Operator prompts must be plain-text numbered questions, never `AskUserQuestion` (SKILL.md:56-58, Task 1:159 precedent).
- CI must pass: `ruff check pipeline/ .claude/skills/ --select F` and the pytest suite incl. `.claude/skills/sim2real-bootstrap/tests/`.

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `.claude/skills/sim2real-bootstrap/SKILL.md` | Bootstrap agent prompt | Modify Task 2 (`:175-196`) failure branch; Task 5 derivation step 4 (`:585`); Task 5 output schema (`:621-622`) |
| `.claude/skills/sim2real-bootstrap/tests/test_build_commands_guidance.py` | Regression guard | Create — assert the Task 5 `build:` schema block hardcodes no shell commands |

## Awareness note (not a code change)

`component.build.commands` is inside the translation slice (`pipeline/lib/slicer.py:58` — `component` is in `_TRANSLATION_TOP_KEYS`), so editing it in an existing bundle changes that bundle's `translation_hash` and orphans images already built under the old hash. This plan changes only the skill that *generates* a new bundle, so no live bundle is touched. Task 5's new wording states this so a future operator fixing a live bundle lands the change at a translation boundary.

---

### Task 1: Make an unavailable component submodule a hard stop (Task 2)

**Files:**
- Modify: `.claude/skills/sim2real-bootstrap/SKILL.md:175-196` (Task 2)

**Interfaces:**
- Consumes: `$COMPONENT_URL`, `$COMPONENT_NAME`, `$COMPONENT_REF` from Task 1.
- Produces: a guarantee that later tasks may read files inside `$EXPERIMENT_ROOT/$COMPONENT_NAME` at `$COMPONENT_REF` — the precondition Task 5's discovery depends on.

Rationale: Task 2's current verify is a bare `test -d "$COMPONENT_NAME/.git" && (cd ... && git log --oneline -1)`. On a private component repo or a missing credential, `git submodule add` fails, the `&&` short-circuits silently, and the agent proceeds to Task 5 with no checkout to discover from — where the only remaining option is to invent commands. That is the defect 858 exists to remove, so the failure must stop the run.

- [ ] **Step 1: Replace Task 2's bare verify with a fail-loud check plus operator prompt**

Replace the `Verify:` block with:

````markdown
**Verify (hard stop on failure):**
```bash
test -d "$COMPONENT_NAME/.git" || { echo "ERROR: submodule $COMPONENT_NAME not populated"; exit 1; }
(cd "$COMPONENT_NAME" && git rev-parse --verify HEAD >/dev/null) || { echo "ERROR: $COMPONENT_NAME has no checked-out commit"; exit 1; }
(cd "$COMPONENT_NAME" && git log --oneline -1)
```

A populated checkout is a **precondition for Task 5**, which derives
`component.build.commands` by reading this component's own build file, CI
workflow and linter config (Task 5, derivation step 4). Without it there is
nothing to discover from and the only remaining option is to invent commands —
the defect issue #858 removed.

So if the clone or checkout fails — most commonly a private component repo or an
expired credential — **halt and ask the operator**; do not continue to Task 5.
Present a plain-text numbered prompt (do NOT use `AskUserQuestion`):

```
Component submodule could not be populated:
  repo: <$COMPONENT_URL>
  ref:  <$COMPONENT_REF>
  error: <verbatim git stderr>

Task 5 cannot derive component.build.commands without this checkout.

  (1) I have fixed access — retry the submodule add
  (2) Read the component's build file, CI workflow and linter config from the
      remote host at <$COMPONENT_REF> instead (discovery only; the submodule
      still has to be added before the bundle can build)
  (3) abort
```
Wait for the operator's reply. On (2), record in `transfer.yaml` which files were
read remotely and at which ref, so a later reader can tell a remote-derived gate
from a checkout-derived one.
````

- [ ] **Step 2: Verify the edit landed in the worktree only**

Run: `git status --short` in the worktree, and `git status --short` in the parent repo root.
Expected: only the worktree shows `M .claude/skills/sim2real-bootstrap/SKILL.md`; the parent repo shows no modification to that path.

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/sim2real-bootstrap/SKILL.md
git commit -m "docs(bootstrap): hard-stop Task 2 when the component submodule is unavailable (#858)"
```

---

### Task 2: Replace Task 5's derivation step 4 with discovery + scoping strategies

**Files:**
- Modify: `.claude/skills/sim2real-bootstrap/SKILL.md:585` (Task 5 derivation step 4)

**Interfaces:**
- Consumes: Task 1's guarantee of a populated `$COMPONENT_NAME` checkout at `$COMPONENT_REF`.
- Produces: the `component.build.commands` list consumed verbatim by `/sim2real-translate`'s writer gate (`prompts/agent-writer.md:219`, 6 retries then `build-failed:`).

- [ ] **Step 1: Replace the one-line step 4**

Current text at `:585`:
```
4. `component.build.commands`: standard Go build + test for relevant package
```

Replace with the discovery procedure, the scoping strategies, the narrowing rule,
the no-hardcoding rule, and the translation-hash note. Full replacement text is
given in the PR; its required elements are:

1. A statement that this list is the translation's entire verification gate, with
   the consumer citation (`agent-writer.md:219`, 6 retries, `build-failed:`) and the
   fact that the pipeline never executes it — so it must fail when the translation
   is *wrong*, not merely when it does not compile.
2. The precondition pointing back at Task 2's hard stop.
3. Discovery order: (a) agent-facing contract (`AGENTS.md`, `CLAUDE.md`,
   `CONTRIBUTING.md`) wins when it declares an authoritative target; (b) build file
   + CI workflow, reading what targets *expand to*, not their names; (c) the
   project's own test scope as it defines it.
4. Five scoping strategies: direct invocation over container-wrapping targets (with
   a YAML comment when part of the gate cannot run here); no duplication of what a
   linter already covers (read its config first); cache-bypass flag (`-count=1` in
   Go) when a test reads a file outside the module; race/concurrency flag when the
   plugin will share state across goroutines; a YAML comment stating each entry's
   reason.

   The race trigger needs care, because at bootstrap time the plugin does not exist
   yet and the simulation source is single-threaded discrete-event code that
   typically contains no goroutines at all — so `algorithms/*.go` is the wrong place
   to look. The concurrency is a property of the *production* extension points, and
   it is already derivable from two things bootstrap has in hand: derivation step 3's
   own interface scan (which extension points the algorithm binds to), and
   `/sim2real-specify`'s Phase 2 "Shape" and Phase 3 "Observability" statements,
   restated in the experiment's `README.md` and already listed in `context.files` by
   step 8. The signal is a decision made on one path that reads a quantity only
   available on another — e.g. a request-path scoring decision reading a value
   produced on the response path. That is cross-goroutine shared state by
   construction, and it is what the race flag exists to catch.
5. The narrowing rule (resolves #858's open question): narrowing the project's test
   scope is **allowed** — the 6-retry budget makes a full scope expensive and
   inherited lint debt in unrelated packages can exhaust it on someone else's
   problem — but subject to both (i) the narrowed scope still covers the packages
   the plugin lives in and the packages its tests exercise, and (ii) the deviation
   is stated in a YAML comment naming what was excluded and why.
6. An explicit no-hardcoding rule: commands seen for any particular component are
   an illustration of what the strategies find, not a specification; run discovery
   against whatever `$COMPONENT_NAME` actually is, including non-Go components.
7. The translation-slice note: editing this field in an existing bundle changes its
   `translation_hash` and orphans images built under the old hash, so land such a
   fix at a translation boundary.

- [ ] **Step 2: Confirm no command literals leaked into the new prose**

Run a grep over the step-4 region for `make <target>`, `go test`, `go vet`,
`golangci-lint run`, `npm test`, `cargo test`.
Expected: no match. (`-count=1` and the phrase "race/concurrency flag" are flag
names, not commands, and are intended.)

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/sim2real-bootstrap/SKILL.md
git commit -m "docs(bootstrap): derive build.commands by discovery, not from algorithm imports (#858)"
```

---

### Task 3: Replace the output-schema placeholder and guard it with a test

**Files:**
- Modify: `.claude/skills/sim2real-bootstrap/SKILL.md:621-622` (Task 5 output schema `build:` block)
- Create: `.claude/skills/sim2real-bootstrap/tests/test_build_commands_guidance.py`

**Interfaces:**
- Consumes: Task 2's derivation wording (the schema block points at it).
- Produces: the `build:` shape the agent writes into `transfer.yaml`; and a CI-enforced invariant that no specific commands are hardcoded.

- [ ] **Step 1: Write the failing test**

Create `.claude/skills/sim2real-bootstrap/tests/test_build_commands_guidance.py` with
three tests over a bounded region — the Task 5 output-schema `build:` block:

- `test_build_block_hardcodes_no_commands` — the block must not match a
  command-literal regex (`make build|test|lint|format|test-unit`, `go test|vet|build`,
  `golangci-lint run`, `npm test|run`, `cargo test|build`). This is the one invariant
  #858 states outright: "They must not be hardcoded into the skill."
- `test_build_block_still_documents_commands_key` — the block must keep the
  `commands:` key that `pipeline/lib/manifest.py` type-checks.
- `test_build_block_points_at_the_discovery_procedure` — the block must route the
  agent to derivation step 4; a bare placeholder is what #858 removed.

The block is located by finding the `### Task 5: Create transfer.yaml` heading, cutting
at `### Task 6:`, then matching the two-space-indented `build:` key. The module
docstring records why only this region is asserted on (NL prose elsewhere is not
CI-tested — see `test_byo.py:518`).

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest .claude/skills/sim2real-bootstrap/tests/test_build_commands_guidance.py -v`
Expected: `test_build_block_points_at_the_discovery_procedure` FAILS (the block still
reads `commands: <derived from algorithm imports>`). The other two pass — the current
placeholder happens to name no commands, which is precisely why it produced a weak
gate rather than a wrong one.

- [ ] **Step 3: Replace the schema placeholder**

Current text at `:621-622`:
```yaml
  build:
    commands: <derived from algorithm imports>
```

Replace with a `commands:` list whose comment states: discovered from the component
submodule and not from algorithm imports, see derivation step 4; this list is the
translation's entire verification gate (writer runs each entry, 6 retries, then
`build-failed:`); each entry carries a reason comment; a narrowed test scope names
what it excluded and why; commands are whatever THIS component declares. The list
body is a single `- <command>  # <why this entry is in the gate>` placeholder — a
shape, not a command.

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest .claude/skills/sim2real-bootstrap/tests/test_build_commands_guidance.py -v`
Expected: 3 passed.

- [ ] **Step 5: Run the full gate**

Run:
```bash
ruff check pipeline/ .claude/skills/ --select F
python -m pytest pipeline/ .claude/skills/sim2real-analyze/tests/ \
  .claude/skills/sim2real-bootstrap/tests/ .claude/skills/sim2real-translate/tests/ \
  .claude/skills/sim2real-check/tests/ --cov=pipeline --cov-report=term-missing \
  --cov-fail-under=90 -q
```
Expected: ruff clean; all tests pass; coverage gate still met (no `pipeline/` lines changed).

- [ ] **Step 6: Commit**

```bash
git add .claude/skills/sim2real-bootstrap/SKILL.md
git add .claude/skills/sim2real-bootstrap/tests/test_build_commands_guidance.py
git commit -m "docs(bootstrap): replace build.commands schema placeholder, guard against hardcoding (#858)"
```

---

### Task 4: Stale-reference sweep and PR

**Files:**
- Possibly modify: `pipeline/README.md:966` (documents `commands: []` as "argv-style")

- [ ] **Step 1: Sweep for references to the changed field**

Grep for `build.commands`, `build_commands`, `BUILD_COMMANDS`, and
`derived from algorithm imports` across `*.md` and `*.py`, excluding
`.claude/worktrees/`. Decide per hit: stale (fix here), accurate (leave),
unrelated (leave).

Known hits and expected dispositions:
- `sim2real-translate/SKILL.md:131`, `:139`, `:307` — accurate, consumer unchanged. Leave.
- `sim2real-translate/prompts/agent-writer.md:219` — accurate. Leave.
- `pipeline/lib/manifest.py` — accurate (type-check only). Leave.
- `pipeline/README.md:966` — says "list of argv-style commands" while the only
  consumer treats entries as shell strings. #829's closing comment records this as a
  known discrepancy worth its own issue. **Leave it** and note in the PR body: fixing
  it is a doc-accuracy change to a different file with its own consumer question
  (`manifest.py` deliberately does not enforce a form, because `translation register`
  bundles from operators who never ran these skills are supported). Out of 858's
  stated scope.

- [ ] **Step 2: Confirm containment**

Run `git status --short` in both the worktree and the parent repo root.
Expected: changes only in the worktree, only to the two intended paths plus this plan.

- [ ] **Step 3: Push and open the PR**

```bash
git push -u origin worktree-issue-858-build-commands-discovery
gh pr create --title "docs(bootstrap): derive build.commands from the component submodule (closes #858)" --body-file /tmp/pr-858-body.md
```

PR body must state: what changed, the open question's resolution (narrowing allowed,
must be commented, must still cover the plugin's packages), the Task 2 hard stop and
why the issue's scope grew to include it, what the sweep covered, and the
`pipeline/README.md:966` deferral.
