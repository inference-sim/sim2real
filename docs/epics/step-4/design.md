# Epic step-4 — `sim2real-bootstrap` port + `--byo` mode

## Overview

Port the `/sim2real-bootstrap` skill (`.claude/skills/sim2real-bootstrap/`) to work cleanly with the three-dimensional workspace produced by steps 0-2, and add a new `--byo` mode for operators who arrive with pre-built EPP images (plus a full baseline scenario and per-algorithm overlays) instead of a BLIS-format experiment folder. Bootstrap's job — scaffold the inputs the sim2real pipeline expects (`transfer.yaml`, `baselines/`, `algorithms/` layout, workload selection, and optionally a component submodule) — is preserved verbatim; what changes is (a) which task branches fire and (b) which schema shape the emitted `transfer.yaml` takes.

Three work strands, one epic:

- **`--byo` mode in bootstrap.** New. BYO operators bring: N pre-built EPP images, N per-algorithm scenario overlays, and one full baseline scenario. They do NOT bring `algorithms/*.go`, `config.md`, `top3_selection.json`, or a component submodule. Bootstrap collects these inputs (args or natural-language description, prompt for anything missing, fatal on missing fields in non-interactive contexts), copies them into canonical locations inside the experiment repo, emits a valid `transfer.yaml` with `component:` omitted and per-algorithm `byo: true` markers, and prints one copy-pasteable `sim2real translation register` command (batched, single translation containing all N algorithms).

- **`sim2real translation register` batched-algorithm mode.** Extend the CLI so one register invocation can register N algorithms into a single translation (each with its own image ref + overlay). Necessary so multi-algorithm BYO runs into a single translation hash that `sim2real assemble --translation <hash>` can consume unchanged. The current single-algorithm shape (step-1 MVP) becomes the N=1 case of the new batched shape.

- **BLIS-mode refresh.** Preserve current bootstrap semantics (Tasks 0-6 in the existing SKILL.md); make three targeted updates: replace `AskUserQuestion` sites with plain-text numbered prompts (project-wide precedent from step-3 and a persistent user preference), audit that bootstrap's outputs still feed step-2 (`translation register`, `translate`, `assemble`) and step-3 (`sim2real-check`) cleanly, and refresh SKILL.md prose where step-0/1/2/3 CLI surface drift has occurred.

## Sources

- `docs/proposals/incremental-implementation-plan.md#step-4` — canonical scope: port `/sim2real-bootstrap` + add `--byo` mode. Explicitly excludes a shipped workload library ("Don't ship a workload library yet — customers can hand-provide workloads for now").
- `docs/proposals/incremental-implementation-plan-addendum.md#step-4` — scope reality: bootstrap has substantial code (not just SKILL.md); `--byo` interactive-vs-stub ratio and tolerant-parser scope were open questions; `transfer.yaml` schema must declare `component:` optional (a constraint that may need a step-0 amendment).
- Prior epics landing on `refactor/v2`: **step-0** (workspace + cluster provisioning; `transfer.yaml` schema v3), **step-1** (BYO MVP via single-algorithm `sim2real translation register`), **step-2** (skill-driven translation, `sim2real translate`, `sim2real translation register`), **step-3** (`sim2real-check` port).

## Design decisions (resolved during intake + external review)

| Decision | Choice | Rationale |
|---|---|---|
| BYO operator profile | Pre-built EPP image(s) + full baseline scenario + per-algorithm overlay(s). No BLIS artifacts. | Aligns with step-1's `translation register` flow; the operator brings the artifacts a completed translation would have produced. |
| BYO input collection — argument grammar | Three repeatable flags: `--algorithm <name>` (defines an algo), `--algorithm-image <name>=<ref>` (maps a name to its image), `--algorithm-config <name>=<path>` (maps a name to its overlay). Plus `--baseline <scenario-path>` (was `--baseline <name>=<scenario-path>` pre-#544; the baseline identifier is now hardcoded to the literal string `baseline`). | Digest image refs use `@` (`image@sha256:...`); overloading `@` for `image@overlay` is ambiguous. Split-flag grammar is unambiguous and shell-safe. |
| BYO input collection — natural language | Skill also accepts NL like *"--byo with baseline foo at /path/bar.yaml, algorithm quartic image ghcr.io/... config /path/z.yaml, algorithm softreflective ..."*. LLM parses into the structured triple set. | Args make it scriptable; NL makes first-time invocation ergonomic. Users overwhelmingly prefer NL for one-shot invocations. |
| Prompting behavior | Plain-text numbered prompts for missing fields (no `AskUserQuestion`). If stdin is not a TTY, missing required fields are fatal errors naming what's missing. `--force` bypasses overwrite prompts. | Matches step-3; project has a persistent user preference against `AskUserQuestion`. Non-interactive fail-fast keeps CI/scripted usage sane. |
| Multi-algorithm BYO | Supported via `sim2real translation register` batched mode (single register call registers all N algorithms into one translation). Bootstrap emits exactly one register command per BYO run. `sim2real assemble --translation <hash>` unchanged (consumes the one multi-algorithm translation). | Aligns BYO with BLIS's `translate` output shape; `assemble`'s single-translation contract stays intact. |
| Per-algorithm overlay shape | Delta overlays (partial YAML that deep-merges onto the baseline during `assemble`). Full-scenario files are tolerated — they just override everything. Merge semantics owned by `assemble`, not bootstrap. | Matches what `translation register --config <path>` already consumes. Bootstrap parse-validates the file (safe_load, mapping) but does not merge. |
| Baseline scenario shape | Full scenario. Operator supplies one baseline. Multi-baseline is not in scope for step-4 (matches step-2/3 assumptions). | Matches today's `baselines/*.yaml` convention. |
| `defaults.disable` in BYO | Populated at bootstrap time from the sorted stems of `*.yaml` files copied from `templates/defaults/`. Duplicate stems (e.g. `foo.yaml` + `foo.yml`) are fatal. | Framework fragments would silently overwrite an operator's baseline otherwise; deterministic sort makes diffs stable. |
| Framework defaults directory | Copied into `<exp-root>/baselines/defaults/` in BYO too. All listed under `defaults.disable`. | Same on-disk layout as BLIS; operator can un-disable individual fragments without re-bootstrapping. |
| `component:` in BYO `transfer.yaml` | Omitted. Schema loosening: `component:` optional when *all* algorithms carry `byo: true`. | BYO has no submodule to pin. Loosening scoped to BYO markers keeps BLIS validation strict. |
| `algorithms[].source:` in BYO `transfer.yaml` | Omitted for BYO algorithms. Schema loosening: `source:` optional per-entry when that entry carries `byo: true`. | Same reasoning. Absence of `byo: true` still requires `source:` (BLIS mistakes stay fail-fast at load time). |
| Explicit BYO discriminator | `algorithms[i].byo: true` per-entry marker (new field, defaults to `false` / absent). | Per-algorithm granularity supports future mixed-mode manifests (some BLIS, some BYO); keeps the schema loosening explicit and reviewer-visible. |
| Register CLI grammar for batched mode | New shape: `sim2real translation register --algorithm <name>=<image>@<config>` (repeatable). The step-1 single-algorithm invocation form is deprecated but kept working as the N=1 case. | Batch is unambiguous with `--algorithm` repeatable; overlay-per-algo shell-quoted. |
| Register hash for multi-algorithm | `translation_hash = sha256(sorted-canonical-serialize([{name, image_digest_or_ref, config_bytes} for each algo]))`. Deterministic in algorithm-set membership; hash is unchanged if algorithms are re-ordered on the command line. | Preserves determinism; supports idempotent re-registration. |
| Register command emission | At end of `--byo` run, print one copy-pasteable `sim2real translation register` command carrying all N algorithms. Bootstrap does not invoke register itself. All paths in the emitted command are quoted with `shlex.quote`; the block is prefixed by `cd <abs-exp-root> && ` so the emitted command runs against the intended experiment repo regardless of the operator's CWD. | Keeps bootstrap side-effect-free at the workspace level; `shlex.quote` prevents shell-injection risk from operator-supplied paths. |
| `AskUserQuestion` in SKILL.md | Replace with plain-text numbered prompts throughout (both BLIS and BYO paths). | Consistent with step-3. |
| `generate_from_config.py` / `generate_scenarios.py` | Untouched. | BLIS-only paths; the port doesn't need to change the parsers. |
| Tolerant BLIS parser (addendum #18) | Out of scope. | Not on the critical path; step-4 focus is BYO + refresh. |
| `--stub-all` fast path (addendum #17) | Not needed. | BYO operator brings baseline + overlays themselves; no fields require stubbing. |
| Rollout | Four PRs. PR-1: schema `byo:` marker + validator update + manifest tests. PR-2: `translation register` batched-algorithm mode. PR-3: BYO bootstrap (blocked by PR-1 and PR-2). PR-4: BLIS SKILL.md refresh (independent). | Schema and register CLI are independent and can land in parallel; BYO bootstrap depends on both. |
| Test level | "Solid" — regression + happy-path + core error paths + schema tests + command-specific validation. No full end-to-end integration test in this epic. | Enough to survive drift; step-5+ will re-shape assumptions. |

## Architecture

### Data flow — BLIS mode (regression, minimal change)

```
BLIS-output experiment folder
    (algorithms/*.go, config.md, top3_selection.json, workloads/*.yaml, README.md)
        ↓
/sim2real-bootstrap  (Tasks 0-6, unchanged shape; plain-text prompts replace AskUserQuestion)
    ↓
Experiment repo scaffolded:
    transfer.yaml       (kind: sim2real-transfer, version: 3, component: <submodule>,
                         algorithms[].source: <path.go>, no byo: markers)
    baselines/*.yaml   (generated by generate_from_config.py OR generate_scenarios.py)
    baselines/defaults/*.yaml   (copied from templates/defaults/)
    workloads/*.yaml    (operator's own; enumerated by bootstrap)
    <component>/        (submodule pinned at derived ref)
        ↓
Existing step-2/3 flow: `sim2real translate` → `translation register` → `assemble` → `sim2real-check`
```

### Data flow — `--byo` mode (new)

```
Operator invokes (structured args):
    /sim2real-bootstrap --byo \
        --baseline baseline=/path/to/baseline.yaml \
        --algorithm softreflective --algorithm quartic \
        --algorithm-image softreflective=ghcr.io/foo/softreflective:tag \
        --algorithm-image quartic=ghcr.io/foo/quartic@sha256:abcd... \
        --algorithm-config softreflective=/path/to/softreflective_overlay.yaml \
        --algorithm-config quartic=/path/to/quartic_overlay.yaml

Or in natural language, e.g.:
    /sim2real-bootstrap --byo with baseline at /path/baseline.yaml,
    algorithm softreflective image ghcr.io/foo/softreflective:tag config
    /path/softreflective.yaml, algorithm quartic image ...

Anything missing → plain-text prompts (interactive TTY) or fatal errors
(non-TTY / --non-interactive).
        ↓
Bootstrap collects the (name, image-ref, overlay-path) triples + (baseline-name, scenario-path)
        ↓
On-disk copies into experiment repo (atomic write-and-rename; safe against traversal):
    <exp-root>/baselines/baseline.yaml                                    <- copy of operator's baseline scenario
    <exp-root>/baselines/defaults/*.yaml                                  <- framework fragments (unchanged)
    <exp-root>/algorithms/softreflective/softreflective_config.yaml       <- copy of overlay
    <exp-root>/algorithms/quartic/quartic_config.yaml                     <- copy of overlay
    <exp-root>/workloads/*.yaml                                           <- enumerated as-is (operator brought)
        ↓
Bootstrap emits transfer.yaml (BYO shape — see next section; algorithms carry byo: true)
        ↓
Bootstrap prints one copy-pasteable register command:

    cd '/abs/path/to/exp-root'
    sim2real translation register \
        --algorithm 'softreflective=ghcr.io/foo/softreflective:tag@algorithms/softreflective/softreflective_config.yaml' \
        --algorithm 'quartic=ghcr.io/foo/quartic@sha256:abcd...@algorithms/quartic/quartic_config.yaml'

Then: sim2real assemble --translation <hash> --cluster <id> --run <name>
        ↓
No translate / build step — BYO images are already built. `assemble` consumes the one
multi-algorithm translation registered above.
```

## `--byo` mode specification

### Experiment root discovery

Bootstrap treats the current working directory as `<exp-root>` when `--byo` is invoked. It does not search parent directories. All emitted paths are relative to `<exp-root>`. The register-command block starts with `cd <abs-exp-root>` so the operator can copy-paste from any shell without worrying about their current directory.

### Scenario name derivation

`transfer.yaml`'s top-level `scenario:` is derived as follows, first match wins:

1. If a `README.md` exists at `<exp-root>/README.md` and its first non-empty line is a `# Title` header, use the title text.
2. Else use `basename(<exp-root>)`.

Whatever is derived is then normalized: lowercased, non-`[a-z0-9-]` chars replaced with `-`, runs of `-` collapsed to one, leading and trailing `-` trimmed, truncated to 40 chars. Empty result after normalization is a fatal error (`--scenario <name>` override supported).

### Invocation surface

Skill argument parsing (in SKILL.md prose):

- `--byo` — mode flag. Presence dispatches to the BYO branch; absence keeps existing BLIS behavior.
- `--baseline <scenario-path>` — one required. `<scenario-path>` is a filesystem path (absolute or CWD-relative) to a full baseline scenario YAML. Pre-#544 the flag also carried a `<name>=` prefix that named the baseline identifier; issue #544 standardized that identifier to the literal string `baseline`, so the flag now takes a path only.
- `--algorithm <name>` — repeatable, at least one required. Same `<name>` constraint as baseline. Declares the algorithm exists.
- `--algorithm-image <name>=<ref>` — repeatable, one per declared algorithm. `<ref>` is a full container image reference. Minimal validation: non-empty, no whitespace, no control characters; final validation deferred to `translation register`.
- `--algorithm-config <name>=<overlay-path>` — repeatable, one per declared algorithm. `<overlay-path>` points to a partial YAML overlay.
- `--scenario <name>` — optional override for the derived scenario name.
- `--force` — bypass overwrite confirmation on destination files that already exist.
- `--non-interactive` — assume the invocation is scripted; missing required fields are fatal errors instead of prompts. Auto-enabled when stdin is not a TTY.

Reserved algorithm/baseline names (fatal if requested): `default`, `defaults`, `baseline` collides with a per-algorithm `--baseline` name (the *baseline* named `baseline` is allowed; a *treatment algorithm* named `baseline` is not).

Natural-language equivalent: operator can describe the same triples and baseline in prose. The skill's prompt logic parses the description into the structured argument set (LLM in the loop), then prompts plain-text for any missing field. Non-interactive contexts require args — NL is prompt-time only.

### On-disk copy layout

Bootstrap COPIES the operator's files into canonical experiment-repo locations. Originals are left intact:

| Source | Destination | Rationale |
|---|---|---|
| Operator's baseline scenario | `<exp-root>/baselines/<baseline-name>.yaml` | Matches BLIS convention; downstream (`translate`, `assemble`) reads `baselines/` verbatim. |
| Operator's per-algorithm overlay | `<exp-root>/algorithms/<algo-name>/<algo-name>_config.yaml` | Matches the shape `translation register` writes under `workspace/translations/<hash>/generated/<algo>/`; makes the register command's `--algorithm ...=<config>` path readable from `<exp-root>`. |
| `<skill-dir>/templates/defaults/*.yaml` | `<exp-root>/baselines/defaults/*.yaml` | Same as BLIS mode. Copied for self-containment; all listed under `defaults.disable` so they don't apply. |
| `<exp-root>/workloads/*.yaml` | Not copied (already operator-owned in experiment repo) | Bootstrap only enumerates and lists in transfer.yaml. |

**Copy semantics:**
- Source path is resolved; must be a regular file (symlinks resolved once, target must be a regular file).
- Destination parent directory is resolved; must lie inside resolved `<exp-root>`. Traversal via `..`, symlink games, or absolute paths are rejected.
- Destination file existing → prompt in interactive mode; fatal in non-interactive mode; bypassed by `--force`.
- Write is atomic: write to a sibling temp file, then rename. Prevents half-written files on crash.

**YAML parse-validation at copy time (bootstrap, not downstream):**
- File readable + `yaml.safe_load` succeeds.
- Top-level document is a mapping (not a list, scalar, or null).
- Multi-document YAML (`---` separators) is rejected.
- Empty document is rejected.

The above applies to both the baseline scenario and each per-algorithm overlay. Deep-merge semantics between them are owned entirely by `sim2real assemble` — bootstrap parse-validates but does not merge.

### Workload enumeration

Non-recursive glob `<exp-root>/workloads/*.yaml`. Symlinks resolved and required to remain inside `<exp-root>/workloads/`. Hidden files (leading `.`) skipped. Sort by lexicographic filename. Duplicate basenames are impossible (filesystem). If the resulting list is empty (missing directory or no matches), fatal error naming the directory.

`.yml`-extension files are ignored — this matches BLIS's current behavior in Task 4. If real-world usage surfaces `.yml` files, a follow-up can add the extension; not in scope for step-4.

### `defaults.disable` population

Enumerate stems (`Path.stem`) of `*.yaml` files copied into `<exp-root>/baselines/defaults/` at bootstrap time. Hidden files skipped. Sorted alphabetically. Duplicate stems (from a hypothetical `foo.yaml` + `foo.yml` pair — cannot happen today because `.yml` is not copied but the check is cheap) are a fatal error.

Rationale: framework can add or remove fragments in a future release and BYO `transfer.yaml` stays in sync.

### Emitted `transfer.yaml` shape (BYO)

```yaml
kind: sim2real-transfer
version: 3

scenario: <derived from experiment folder name / README title, normalized>

# component: absent — BYO has no submodule (validator allows this when all algorithms
# carry byo: true; see "Schema addition" section)

baselines:
  - name: baseline
    scenario: baselines/baseline.yaml

algorithms:
  - name: softreflective
    defaults: baseline
    byo: true
    # source: absent — BYO has no .go source
  - name: quartic
    defaults: baseline
    byo: true

workloads:
  - workloads/w1.yaml
  - workloads/w2.yaml
  - workloads/w3.yaml

defaults:
  disable:
    # Populated from the actual filename stems in <exp-root>/baselines/defaults/
    # at bootstrap time. As of writing, that directory contains:
    - epp-verbosity
    - externally-managed-gateway
    - llm-d-rbac
    - preserve-request-id
    - routing-proxy-resources
    - vllm-logging

context:
  files: []
  text: |
    Bring-your-own translation. Images and per-algorithm overlays supplied
    externally; no BLIS source in this experiment repo.
```

### Register command emission

At the end of a `--byo` run, bootstrap prints ONE `sim2real translation register` command carrying all algorithms (batched mode — see the register-CLI section below). Paths are quoted with `shlex.quote`; the block is prefixed with `cd` to the absolute experiment root so paste order is CWD-agnostic:

```
Next: register all algorithms in one call.

    cd '/absolute/path/to/exp-root'
    sim2real translation register \
        --algorithm 'softreflective=ghcr.io/foo/softreflective:tag@algorithms/softreflective/softreflective_config.yaml' \
        --algorithm 'quartic=ghcr.io/foo/quartic@sha256:abcd...@algorithms/quartic/quartic_config.yaml'

The command prints one translation hash. Then:

    sim2real assemble --translation <hash> --cluster <cluster_id> --run <run-name>
```

Bootstrap does not invoke register itself. This keeps bootstrap side-effect-free at the workspace level — the operator retains control over which translations to actually register and can inspect the emitted scaffolding first.

## `sim2real translation register` batched-algorithm mode

### CLI surface

Extend the register subcommand to accept the algorithm/image/config triples in one call. Grammar:

```
sim2real translation register --algorithm <name>=<image-ref>@<config-path> [--algorithm ...]
                              [--baseline-config <path>]
                              [--force] [--registered-hash HASH]
```

- `--algorithm <name>=<image-ref>@<config-path>` — repeatable, at least one required.
  - `<name>` matches the same regex as bootstrap (Kubernetes DNS-label subset, 1-20 chars).
  - `<image-ref>` may contain `@` (digest-form refs). Parser splits the triple on the **rightmost** `=` and then the **rightmost** `@` in the remaining substring. This is the only unambiguous split. Overlay paths containing `@` are rejected (fatal, with an error naming the constraint). Overlay paths containing `=` are supported (only the rightmost `=` splits).
  - `<config-path>` is a filesystem path (absolute or CWD-relative) to a YAML overlay.
- Deprecated single-algorithm form (step-1): `--algorithm NAME --image REF --config PATH` — kept working as a legacy invocation that produces an N=1 registration. Emitted deprecation warning to stderr, no functional change. Removal is a step-5+ follow-up.
- `--baseline-config <path>` unchanged.
- `--force`, `--registered-hash` unchanged.

### Translation hash for batched registration

For an N-algorithm registration, `translation_hash` is:

```
sha256(canonical-json([
    {"name": <name>, "image": <digest-or-ref>, "config": <sha256(config-bytes)>}
    for each algorithm, sorted by name
]))
```

Deterministic in the algorithm set — reordering `--algorithm` on the command line doesn't change the hash. Idempotent — re-running the same set produces the same hash and hits the existing "already registered, no-op" path. Adding a new algorithm to a previously-registered set produces a different hash (registered as a new translation).

The step-1 single-algorithm hash formula was `sha256(digest_or_ref, config_bytes, algorithm_name)`. The new formula collapses to the same shape for N=1 with canonical-json framing — hash values will change (breaks any existing on-disk single-algorithm registrations from step-1). Mitigation: step-1 was very recently landed; no long-lived translation registrations exist in production yet. A one-line note in the PR body: "existing `workspace/translations/<hash>/` from step-1 will be re-registered under a new hash."

### On-disk shape

Unchanged from step-1: one translation dir at `workspace/translations/<hash>/`, containing:
- `translation_output.json` — algorithm index + provenance. Now carries N algorithm entries instead of 1.
- `registered.json` — image refs + digests. Now carries N entries.
- `generated/<algo>/<algo>_config.yaml` — verbatim copy of each overlay. One directory per algorithm.

## Schema addition (`pipeline/lib/manifest.py`)

### The `byo: true` per-algorithm marker

Add an optional per-entry field:

```yaml
algorithms:
  - name: softreflective
    defaults: baseline
    byo: true
```

Validator changes:

1. **`byo: true` implies `source:` optional.** Today's required-field loop at approximately `pipeline/lib/manifest.py:80-90` iterates `("name", "source", "defaults")` for each algorithm. After: if `entry.get("byo") is True`, skip the `source` check. `name` and `defaults` remain required for every entry.
2. **`component:` optional when all algorithms are `byo: true`.** Today's check at `pipeline/lib/manifest.py:160-171` requires `component` when any algorithm is declared. After: if every algorithm entry has `byo: true`, `component:` may be absent (unchanged if any algorithm is BLIS-shape).
3. **Mixed manifests are allowed at the loader level** — a manifest may declare some BLIS algorithms and some BYO algorithms. Downstream commands may or may not support mixed mode (see "Command-specific validation" below).

Non-BYO algorithm entries still require `source:` (BLIS mistakes stay fail-fast at load time). Non-BYO manifests still require `component:`.

### Command-specific validation

Loader permissiveness is paired with per-command validation so BYO-appropriate absences don't silently break BLIS-appropriate commands:

- **`sim2real translate`** — reads `algorithms[].source` to locate `.go` files. If any selected algorithm has no `source:`, error: *"cannot translate algorithm '<name>' — no `source:` in transfer.yaml (BYO algorithm; use `sim2real translation register` directly)."*
- **`sim2real build`** — reads `component:` to determine the base image. If `component:` is absent (all-BYO manifest), error: *"nothing to build — this transfer.yaml declares no component (all algorithms are BYO; images are pre-built)."*
- **`sim2real translation register`** — does not read `algorithms[].source` or `component:`. Unchanged.
- **`sim2real assemble`** — reads `algorithms[].name` and `algorithms[].defaults`. Does not need `source:` or `component:` when the referenced translation supplies images. Unchanged.
- **`sim2real-check`** (step-3) — does not read `algorithms[].source` or `component:`. Unchanged.

Each of these guardrails is covered by a targeted test in PR-1 (loader) and PR-2 (register) — see the testing plan below.

## BLIS-mode refresh

Preserve Tasks 0-6 of the current SKILL.md verbatim except for three touches:

1. **`AskUserQuestion` → plain-text numbered prompts.** Two sites in current SKILL.md (line 44 general instruction, line 108 Task 1 concrete example). Same semantics: operator confirms derived values before bootstrap proceeds. Same output shape; only the prompt mechanism changes.

2. **Path-audit prose.** Verify SKILL.md's references to downstream commands (`translate`, `translation register`, `assemble`, `sim2real-check`) match the current CLI surface after steps 0-3. If any drift is found (e.g., a flag renamed), correct in this PR. This is a prose refresh, not a semantic change.

3. **Cross-reference to `--byo`.** Add a one-line note near the top of SKILL.md pointing at the BYO branch: *"For pre-built images without BLIS-format inputs, use `--byo` mode (see below)."* Keeps the two modes discoverable from either entry point.

`generate_from_config.py` and `generate_scenarios.py` are untouched. `generate_scenarios.README.md` is touched only if the audit surfaces inaccuracies.

## Testing plan

### Level: "Solid" — regression + happy-path + core error paths + schema + command-specific validation tests

Not full end-to-end integration; that overlaps with step-1/2 test coverage and is likely to change in step-5+.

### PR-1 tests (`byo: true` marker + validator)

In `pipeline/tests/test_manifest.py` (or new file if it does not exist):

- **Schema — new field acceptance.** `algorithms[i].byo: true` with `source:` absent → loads successfully.
- **Schema — non-BYO still strict.** `algorithms[i].byo: false` (or absent) with `source:` absent → error naming `source`.
- **Schema — component optional when all algos byo.** `component:` absent, all `algorithms[].byo: true` → loads.
- **Schema — component still required when mixed.** One BYO algo + one BLIS algo, `component:` absent → error naming `component`.
- **Schema — component required when no byo.** All BLIS algorithms, `component:` absent → error (unchanged regression).
- **Schema — byo type check.** `byo:` present but not boolean → error naming the field and its type.

Command-specific validation guards:
- **`sim2real translate` BYO guard.** Attempt to translate an algorithm marked `byo: true` → error naming the algorithm and the reason (no `source:` to translate).
- **`sim2real build` all-BYO guard.** Attempt to build a manifest with all algorithms `byo: true` (no `component:`) → error naming the reason (nothing to build).
- **`sim2real translation register` BYO manifest read.** Load a BYO manifest through `manifest.load_manifest`, verify no field is dereferenced that a BYO entry omits.

### PR-2 tests (batched `translation register`)

In `pipeline/tests/test_sim2real.py` (or a new `test_translation_register_batched.py`):

- **Happy path — 2 algorithms.** Register two algorithms in one call → one translation dir with two algorithm entries in `translation_output.json`, two overlay files under `generated/`, deterministic hash.
- **Happy path — 1 algorithm (deprecated form).** Old-style `--algorithm N --image R --config P` → equivalent registration, deprecation warning on stderr.
- **Rightmost-`@`-in-image-ref.** Register with `--algorithm foo=registry.io/img@sha256:deadbeef@algorithms/foo/foo_config.yaml` → image ref parsed as `registry.io/img@sha256:deadbeef`, config path as `algorithms/foo/foo_config.yaml`.
- **Overlay path containing `@`.** `--algorithm foo=img:tag@path@with@ats` → fatal error naming the constraint.
- **Duplicate algorithm names.** Two `--algorithm foo=...` triples → fatal.
- **Idempotent re-registration.** Same triples in different order → same hash, "already registered, no-op" path.
- **Alias-collision handling.** Reuse existing single-algorithm collision-detection path; verify batched invocations that collide behave identically.
- **Malformed overlay.** Register with a config path whose YAML fails `safe_load` → fatal, error names the file and parse issue.

### PR-3 tests (BYO bootstrap)

New file `.claude/skills/sim2real-bootstrap/tests/test_byo.py` (or extension of the existing tests directory):

- **BYO happy path — 2 algorithms.** Fake experiment repo (bare — only `workloads/` populated), operator inputs supplied as args, bootstrap runs to completion. Assert:
  - `<exp-root>/baselines/<name>.yaml` exists with baseline scenario content.
  - `<exp-root>/algorithms/<algo>/<algo>_config.yaml` exists per algorithm.
  - `<exp-root>/baselines/defaults/*.yaml` copied from templates.
  - Emitted `transfer.yaml` loads successfully via `manifest.load_manifest` (integration with PR-1's `byo:` marker).
  - `defaults.disable` matches all filenames in `templates/defaults/` at test time (regex-matched, not hardcoded).
  - No `component:` key. Every `algorithms[]` entry has `byo: true` and no `source` key.
  - Emitted register command is one line (batched), well-formed, `shlex`-quoted (test asserts round-trip through `shlex.split`).

- **BYO error paths.**
  - Missing `--baseline` → error naming the missing field.
  - No `--algorithm` at all → error.
  - `--algorithm foo` with no `--algorithm-image foo=...` → error naming `foo` and the missing field.
  - `--algorithm foo` with no `--algorithm-config foo=...` → error naming `foo` and the missing field.
  - Duplicate `--algorithm` names → error.
  - Missing `<exp-root>/workloads/` or empty → error naming the directory.
  - Malformed overlay YAML → error naming the file and parse issue.
  - Malformed baseline YAML → same.
  - Non-mapping-root YAML (list, scalar, empty) for either baseline or overlay → error.
  - Multi-document YAML for either → error.
  - Baseline/overlay path doesn't exist → error naming the path.
  - Path-traversal in overlay dest (via source symlink pointing outside `<exp-root>`, or dest that resolves outside) → refused.
  - Overwrite refused when destination file exists and neither TTY-confirmation nor `--force` present.

- **Args-vs-NL parse.** If argument parsing is extracted into a small helper, unit-test structured args and NL-derived arg-dict produce equivalent triples.

- **Register command well-formedness.** Emitted register command passes `sim2real translation register --help`-compatible argparse (integration test: invoke the register argparser on the emitted args, assert it accepts them).

### PR-4 tests (BLIS refresh)

Regression only. Existing `tests/test_generate_from_config.py` continues passing. Add one new case if the `AskUserQuestion` swap warrants a prompt-parsing test (probably not — the prose change doesn't have code to exercise).

## Demo criteria

Two flows validated end-to-end at epic close (evidence attached to the epic):

1. **BLIS-source regression.** Start from a BLIS-output experiment folder, run `/sim2real-bootstrap` in BLIS mode. Produce a `transfer.yaml` + `baselines/` + workload selection that step-2's `sim2real translate` + `sim2real translation register` consume cleanly. Proceed through `sim2real assemble` and `sim2real-check --run <name>` to a full validation report. Same output as before the epic — no user-visible drift.

2. **BYO new flow — multi-algorithm.** Start from a bare experiment repo (only `workloads/` populated). Run `/sim2real-bootstrap --byo --baseline baseline=<...> --algorithm foo --algorithm bar --algorithm-image foo=<...> --algorithm-image bar=<...> --algorithm-config foo=<...> --algorithm-config bar=<...>`. Produce a valid `transfer.yaml` (with `byo: true` per algo, `component:` absent) + `baselines/` + `algorithms/` layout. Run the emitted `sim2real translation register` command (single call, both algorithms in one translation). Then `sim2real assemble --translation <hash> --run <name>` produces deployable YAMLs. Then `sim2real-check --run <name>` renders a validation report against a real-cluster smoke run. Fixture-based test coverage plus a single real-cluster smoke.

## Out of scope / deferred

- **Workload library shipping.** Plan says not yet; operator-provided.
- **Interactive-vs-stub UX ratio (addendum #17).** Moot with the resolved BYO shape — operator brings baseline + overlays, so there is nothing to stub. `--stub-all` fast path also moot.
- **Tolerant BLIS parser (addendum #18).** `generate_from_config.py` and `generate_scenarios.py` are untouched. If real BLIS-format drift is observed post-epic, file a follow-up.
- **Multi-baseline in BYO.** One baseline per BYO run. Matches step-2/3's single-baseline assumption (deferred to a future multi-baseline epic covering steps 3-5).
- **Removing the step-1 deprecated single-algorithm `register` invocation.** Kept working with a deprecation warning in step-4; removal is a step-5+ cleanup.
- **`sim2real assemble` accepting multiple `--translation` refs.** Not needed with the batched-register design — one translation per BYO run.
- **Bootstrap invoking `register` automatically.** Explicit non-goal: bootstrap stays side-effect-free at the workspace level. Operator runs the emitted register command themselves.
- **Dry-run mode for bootstrap.** Nice-to-have; deferred to a follow-up if operators want it.
- **`.yml`-extension workloads/defaults.** Only `.yaml` matches. If observed in the wild, a follow-up can broaden the glob.
- **Absolute-paths-internally / relative-paths-in-manifest split.** Emitted `transfer.yaml` uses experiment-repo-relative paths for portability; bootstrap's internal work uses resolved absolute paths for safety. Documented above; no separate spec section.
- **`generate_scenarios.README.md` refresh.** Touched only if drift is surfaced during PR-1 or PR-3.

## Rollout — four PRs

- **PR-1 — Schema `byo: true` marker + validator + command-specific guards.** `pipeline/lib/manifest.py` adds the `byo:` field; validator loosens `source:` and `component:` per the rules above; new tests. Adds command-specific validation in `translate` (require source) and `build` (require component), with tests. Small (~2 files changed, ~10 test cases added). Lands first — unblocks PR-3.
- **PR-2 — `sim2real translation register` batched-algorithm mode.** New `--algorithm <name>=<image>@<config>` repeatable flag; new multi-algorithm hash formula; new tests; deprecation warning on the step-1 single-algo form. Medium (~1 file changed + tests + doc note in `pipeline/README.md`). Independent of PR-1 — the register command doesn't need the schema marker; the schema marker is a bootstrap-side / manifest-side concern. Lands in parallel with PR-1.
- **PR-3 — BYO bootstrap.** New `--byo` branch in `sim2real-bootstrap/SKILL.md`, argument parsing (args + NL + plain-text prompts for missing, `--non-interactive` + `--force` for scripted use), on-disk copy logic + destination validation, `defaults.disable` population from `templates/defaults/` filenames, batched register-command emission with `shlex.quote`, BYO happy-path + error-path tests, top-level `README.md` + `pipeline/README.md` refs to `--byo`. Blocked by both PR-1 (schema) and PR-2 (register CLI). Larger PR.
- **PR-4 — BLIS-mode SKILL.md refresh.** `AskUserQuestion` → plain-text prompt swap (2 sites), path-audit prose, cross-reference note to `--byo`. Independent — can land any time after PR-1 (or before, if we're willing to have a brief window where the top-line prose references `--byo` before it exists in the SKILL.md branch). Small PR.

Dependency order: `{PR-1, PR-2}` parallel → PR-3 (needs both) → PR-4 (any time; simplest to land last for a clean SKILL.md diff).

## Risks

- **Schema addition needs downstream command guards.** The `byo:` marker is a loosening at the loader level; without command-specific guards, a BYO manifest reaching `translate` or `build` would silently mis-process. Mitigation: PR-1 adds those guards + tests. If a downstream command is missed, its bug will surface at the demo — treat as a hotfix follow-up rather than expanding step-4 scope.
- **Register hash formula change breaks step-1 registrations.** Existing `workspace/translations/<hash>/` from step-1 will be re-registered under a new hash after PR-2 lands. Mitigation: step-1 shipped recently; no long-lived registrations exist yet. Documented in PR-2 body. If a real user has one, they re-run register.
- **BLIS regression is silent** until an operator runs a full BLIS flow. Coverage relies on the existing `test_generate_from_config.py` + PR-3's interop check. If real drift is found post-epic, promote to a follow-up issue.
- **Copy vs reference for overlay files.** Bootstrap copies the operator's overlay into `<exp-root>/algorithms/<name>/<name>_config.yaml`; subsequent edits to the operator's original path do NOT propagate. Mitigation: SKILL.md's post-run summary names the copy destinations and points the operator at them as canonical going forward.
- **Register-command drift.** The register-command emission prose in SKILL.md carries the register CLI grammar. If PR-2's grammar changes late in review, the bootstrap prose goes stale silently. Mitigation: PR-3 includes an integration test that parses the emitted command through the actual register argparser — if register drifts, the test fails.
- **Path-traversal via operator-supplied paths.** Overlay/baseline paths from args are copied into `<exp-root>`. Bootstrap validates each resolved destination is inside resolved `<exp-root>` and rejects symlinks that escape. PR-3's tests include traversal, symlink-outside, and existing-symlink-destination cases.
- **NL parsing is nondeterministic across LLM runs.** The natural-language operator input path relies on the LLM to convert prose into structured args. Reproducibility isn't guaranteed across model versions. Mitigation: only the structured parser is tested in CI; NL is treated as an ergonomic front-end, not a stable contract. Non-interactive contexts require args (NL rejected).
- **Shell-quoting bugs in emitted register commands.** Paths with spaces, backticks, `$(...)`, or dollar-signs could produce dangerous copy-paste output. Mitigation: `shlex.quote` on every emitted path/argument; PR-3 tests include a "paths with spaces + shell metacharacters" case that round-trips through `shlex.split`.
- **`--algorithm name=image@config` grammar edge cases.** Overlay paths containing `@` are rejected; overlay paths containing `=` are supported via rightmost-split. If real BYO overlays turn out to routinely use `@` in filenames, revisit the grammar. Mitigation: PR-2 tests exercise both cases and document the constraint in the register `--help` text.
