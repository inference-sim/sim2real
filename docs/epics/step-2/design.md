# Epic: step-2 — Skill-driven translation

## Scope

Restore the AI-driven translation half of the pipeline that step-1 stubbed out for BYO. Deliver two new commands (`sim2real translate`, `sim2real build`), re-enable the `/sim2real-translate` skill against the new workspace layout, and add a named-alias UX that lets operators reference translations by human-readable name across every touchpoint (`build`, `assemble`, and BYO `register`) instead of a 64-hex-char hash.

Both producers of a translation (BYO `translation register` from step-1 and skill-driven `translate` from step-2) write identical-shaped translations. Every downstream consumer already ported in step-1 (`assemble`, `deploy.py run`, `deploy.py collect`) works unchanged — the skill-driven path is a different producer of the same output shape.

Multi-algorithm is inherent in the schema (translation slice is `algorithms[i].source | .config`), so this epic ships the code shape that iterates over N algorithms per `transfer.yaml` in a single `translate` invocation. Multi-algorithm BYO (`register --algorithm a --image ... --algorithm b --image ...`) stays deferred per `three-dimensional-sim2real.md` §open question 4.

## Assumptions

**Codebase state:**

- Step-0 and step-1 are merged into `refactor/v2` (verified — `origin/refactor/v2` is at `b83c84d`, the step-1 close-merge commit). The new workspace layout, `pipeline/lib/slicer.py`, `pipeline/lib/layout.py`, `pipeline/lib/cluster_ops.py`, and the ported orchestrator all exist.
- `sim2real translation register` (step-1) already writes `translations/<hash>/translation_output.json` for BYO. Step-2 does not rewrite it; it extends it (writes the new `alias` field, moves `image_ref`/`image_digest` per-algorithm) and adds a peer producer (`translate`) that writes the same schema.
- `sim2real assemble --translation` (step-1) requires a full 64-char hash today; step-2 relaxes that to accept name-or-prefix-or-hash via the new resolver.

**Local operator environment:**

- `skopeo` on PATH (new — hard prerequisite for `sim2real build`).
- `kubectl` configured for the target cluster (unchanged from step-1).
- `podman` or `docker` credential file (`~/.config/containers/auth.json` or `~/.docker/config.json`) with an entry for the target registry (soft — probe falls through to "build" on missing creds).
- Framework submodules (`inference-sim`, `llm-d-benchmark`) initialized locally when `build` runs — same posture as step-1's `sim2real assemble` requires for `benchmarkGit*`/`blisGit*` params. See #458 for the analogous step-1 fix.

**Cluster state:**

- `registry-secret` Secret in the primary namespace, provisioned by `cluster.py provision` (unchanged from step-1). Required for the in-cluster BuildKit pod to push to the registry.
- BuildKit image (`moby/buildkit:latest` per today's `build-epp.sh`) pullable from the cluster.

**Manifest state:**

- `transfer.yaml:epp_image.build.{hub,name}` present. Required for image-ref construction. Missing → `sim2real build` exits 2.

## Open-question resolutions

Five inherited open questions from `incremental-implementation-plan-addendum.md §Step 2` plus four surfaced during the brainstorm.

| # | Question | Resolution |
|---|---|---|
| 12 | `translation_hash` inputs | Algorithm-source-file contents + translation-slice fields of `transfer.yaml`. Not toolchain, not base image, not build flags. Documented "toolchain-drift → stale-image-at-same-tag" gotcha; escape hatch is `--force-rebuild`. |
| 13 | Registry-auth strategy | Two surfaces. In-cluster push auth: unchanged — `cluster.py provision`'s `registry-secret` continues to work for private registries. Local registry probe (new): uses skopeo's default credential source (`~/.docker/config.json` or `~/.config/containers/auth.json`), which any `docker login` / `podman login` populates. On probe failure of any kind: treat the image as absent and build (fail-safe direction). |
| 14 | Slice extractor location | Already in `pipeline/lib/slicer.py` from step-1. Extended here with `translation_hash_with_sources(manifest, experiment_root) -> str` that reads `algorithms[i].source` file contents and folds them into the canonical-JSON envelope. BYO's `_compute_translation_hash` in `sim2real.py` stays untouched (peer producer, different input shape — image is pre-built). |
| 15 | `build` skip-vs-rebuild policy | Registry probe (skopeo inspect). If the tag exists at any digest, skip. `--skip-build` skips the whole build phase (existing); `--force-rebuild` (new) forces rebuild-and-push even when the tag exists. |
| 16 | Build placement (standalone vs lazy) | Standalone in step-2, per plan. Lazy auto-fix from `deploy.py run` is step-6. |
| — | Multi-algorithm handling in `translate` | Inherent — one `translate` invocation iterates over `manifest.algorithms[]`, writes one `translations/<hash>/generated/<algo>/` subtree per algorithm. `translation_hash` covers all of them. |
| — | Where `image_ref` lives for skill-driven translations | Mutated in place in `translation_output.json`. `translate` writes it with `image_ref: null`; `build` fills it in after push. Fewer artifacts than a separate `build_output.json` file. `registered.json` stays BYO-only. |
| — | When `translation_output.json` is written | At the start of `translate` (before the skill checkpoint), with `image_ref: null`. Skill uses it as the algorithm index. `translate --resume` uses it as the completeness-check source of truth. `build` mutates the `image_ref` field on push. |
| — | `sim2real assemble --translation` name/prefix support | Bundled into step-2 (with the alias resolver). Otherwise the UX is inconsistent — copy-paste-from-`translate`-output works for `build` but not `assemble`. |

## Schemas

### `translations/<hash>/translation_output.json` (extended)

Existing step-1 fields stay. Image metadata **moves per-algorithm** so multi-algorithm translations can carry per-algo image refs and digests without ambiguity. Full canonical shape:

```json
{
  "version": 1,
  "translation_hash": "abc123...",
  "source": "skill",
  "alias": "softreflective-v1",
  "algorithms": [
    {
      "name": "softreflective",
      "source_path": "algorithms/softreflective.py",
      "source_sha256": "…",
      "config_path": null,
      "image_ref": null,
      "image_digest": null
    }
  ],
  "created_at": "2026-07-02T14:00:00Z"
}
```

**Canonical examples.**

Skill-produced, single-algorithm, before build:

```json
{
  "version": 1,
  "translation_hash": "abc123def456…",
  "source": "skill",
  "alias": "softreflective-v1",
  "algorithms": [
    {
      "name": "softreflective",
      "source_path": "algorithms/softreflective.py",
      "source_sha256": "e3b0c44…",
      "config_path": null,
      "image_ref": null,
      "image_digest": null
    }
  ],
  "created_at": "2026-07-02T14:00:00Z"
}
```

Skill-produced, single-algorithm, after `build`:

```json
{
  "version": 1,
  "translation_hash": "abc123def456…",
  "source": "skill",
  "alias": "softreflective-v1",
  "algorithms": [
    {
      "name": "softreflective",
      "source_path": "algorithms/softreflective.py",
      "source_sha256": "e3b0c44…",
      "config_path": null,
      "image_ref": "quay.io/kalantar-msb/sr:abc123def456-softreflective",
      "image_digest": "sha256:aa…"
    }
  ],
  "created_at": "2026-07-02T14:00:00Z"
}
```

BYO-registered, single-algorithm (image is pre-built, so `image_ref` is populated at register time):

```json
{
  "version": 1,
  "translation_hash": "byo-hash-…",
  "source": "byo",
  "alias": "softreflective",
  "algorithms": [
    {
      "name": "softreflective",
      "source_path": null,
      "source_sha256": null,
      "config_path": "generated/softreflective/softreflective_config.yaml",
      "image_ref": "ghcr.io/kalantar-msb/sr-router@sha256:aa…",
      "image_digest": "sha256:aa…"
    }
  ],
  "created_at": "2026-07-01T10:00:00Z"
}
```

Skill-produced, **multi-algorithm**, after build:

```json
{
  "version": 1,
  "translation_hash": "…",
  "source": "skill",
  "alias": "compare-a-b",
  "algorithms": [
    {"name": "algo_a", "source_path": "algorithms/algo_a.py", "source_sha256": "…",
     "config_path": null,
     "image_ref": "quay.io/…:<hash[:12]>-algo_a", "image_digest": "sha256:aa…"},
    {"name": "algo_b", "source_path": "algorithms/algo_b.py", "source_sha256": "…",
     "config_path": null,
     "image_ref": "quay.io/…:<hash[:12]>-algo_b", "image_digest": "sha256:bb…"}
  ],
  "created_at": "…"
}
```

Design notes:

- `alias` uniqueness is enforced by the resolver on collision: `translate` and `register` both refuse if another translation has the same `alias` value pointing at a different hash, unless `--force` is passed (see `--force` policy under `sim2real translation register`).
- `image_ref` and `image_digest` on each `algorithms[i]` are the only mutable fields. Everything else is set at translation-time and never changes. That preserves the content-addressed intuition ("the translation is done, this is what it is") while consolidating build metadata in the same file.
- The `algorithms[i].source_path`, `source_sha256`, and `config_path` fields disambiguate the two producers: skill-driven populates `source_path`/`source_sha256` (algorithm sources live in the experiment repo); BYO populates `config_path` (points at the overlay YAML under `generated/`) and leaves source fields null.
- **Migration for step-1 BYO translations.** BYO `translation_output.json` files written by step-1 have top-level `image_ref` and no per-algo image metadata. PR 2 rewrites `translation register` to write the new per-algo shape and provides an on-read shim in `pipeline/lib/translation_ref.py` that recognizes both old and new shapes without a version bump (the field-set is a superset). No forced migration of existing files.
- BYO's `_compute_translation_hash` in `sim2real.py` (inputs: `{algorithm_name, config_sha256, image_digest_or_ref}`) stays unchanged. Only skill-driven `translate` uses `translation_hash_with_sources`. The two producers have genuinely different inputs and stay separate.

### Image-ref construction

`sim2real build` derives each algorithm's image ref from three inputs:

- **`<registry>`** and **`<repo>`** — read from `transfer.yaml:epp_image.build.hub` and `epp_image.build.name`. This is the v3 schema field that already exists in step-1 experiment repos (see `pipeline/lib/manifest.py`). No new manifest field.
- **`<translation_hash[:12]>`** — from `translations/<hash>/translation_output.json:translation_hash`, truncated.
- **`<algo>`** — from `algorithms[i].name`.

Composed as:

```
<registry>/<repo>:<translation_hash[:12]>-<algo>
```

Rationale for the tag-suffix pattern (over `<registry>/<repo>-<algo>:<hash[:12]>` or `<registry>/<repo>/<algo>:<hash[:12]>`): single-repo model matches step-1's registry provisioning (one repo per pipeline setup), keeps RBAC simple, and avoids per-algo repo creation. The `-<algo>` tag suffix disambiguates multi-algo builds within one repo.

If `transfer.yaml:epp_image.build` is absent (older experiment repos), `sim2real build` exits with `epp_image.build.{hub,name} missing from transfer.yaml — required for skill-driven build`.

### `translations/<hash>/skill_input.json`

The contract between `sim2real translate` (Python) and `/sim2real-translate` (skill prompt). Pinned schema so PR 3 (translate) and PR 4 (skill) can't drift.

```json
{
  "version": 1,
  "translation_hash": "abc123def456…",
  "experiment_root": "/absolute/path/to/experiment-repo",
  "translations_dir": "/absolute/path/to/workspace/translations/abc123def456…",
  "scenario": "softreflective-v1",
  "baselines": [
    {
      "name": "base",
      "generated_overlay_path": "generated/baseline_base/baseline_config.yaml"
    }
  ],
  "algorithms": [
    {
      "name": "softreflective",
      "source_path": "algorithms/softreflective.py",
      "source_sha256": "e3b0c44…",
      "output_dir": "generated/softreflective",
      "config_output_path": "generated/softreflective/softreflective_config.yaml",
      "baseline_overlay_path": "generated/baseline_base/baseline_config.yaml",
      "notes": "…free-form guidance from transfer.yaml.context…"
    }
  ],
  "context": {
    "text": "…transfer.yaml:context.text…",
    "file_paths": ["docs/proposals/…", "docs/design/…"]
  }
}
```

Field semantics:

- **Paths are relative to `experiment_root` or `translations_dir`** as appropriate — `algorithms[i].source_path` is relative to `experiment_root`; `algorithms[i].output_dir`, `config_output_path`, `algorithms[i].baseline_overlay_path`, and `baselines[i].generated_overlay_path` are relative to `translations_dir`. Absolute-path variants of `experiment_root` and `translations_dir` are provided at the top level so the skill doesn't have to compose them itself.
- **`baselines`** is a list — one entry per baseline that any `algorithms[*].defaults` cross-references (`manifest.py` requires `defaults` and validates it names a real `baselines[].name`). Unreferenced baselines are dropped by `sim2real translate`. When the manifest declares baselines but no algorithms, the list defensively includes `baselines[0]` so assemble can still resolve a baseline for standby workloads. `baselines` may be empty; the writer's Phase 2 loop collapses to a no-op. Each entry's `generated_overlay_path` lives under `generated/baselines/<name>/baseline_config.yaml` (updated by issue #544 — was previously `generated/baseline_<name>/baseline_config.yaml`).
- **Source content is not embedded.** The skill reads algorithm source files directly from `experiment_root/algorithms[i].source_path`. That keeps `skill_input.json` small and lets the skill inspect the full source file (imports, comments) rather than a truncated JSON blob.
- **`algorithms[i].output_dir`** is the directory the skill must populate with `{cmd,pkg,<name>_output.json,<name>_config.yaml}` — matches the layout under `translations/<hash>/generated/<name>/`.
- **`algorithms[i].baseline_overlay_path`** resolves this algorithm's `defaults` cross-reference into the concrete per-baseline overlay path (points into one of the `baselines[]` entries' `generated_overlay_path`). Null when the algorithm's `defaults` names a baseline that isn't in `baselines[]` (rare — the manifest layer prevents this for valid v3 shapes). Algorithms with the same `defaults` receive the same path; the writer's Phase 2 skip-if-exists check keeps them from clobbering each other.
- **`context`** carries the `transfer.yaml:context` block (text + file paths) so the skill has the same free-form input the pre-refactor `prepare.py` fed it.

`skill_input.json` is written by `sim2real translate` at initial-run time and is not mutated afterward. The skill reads it at its Step 0 and writes into the paths it names.

### Skill-produced artifacts (unchanged shape from the previous skill)

```
workspace/translations/<hash>/
├── skill_input.json                       # translate step-1 output; the skill's read
├── translation_output.json                 # written by translate step-1 (as above)
└── generated/
    ├── baseline_config.yaml                # BYO ``translation register`` — legacy shared baseline overlay (applies to every baseline in the manifest)
    ├── baselines/<name>/baseline_config.yaml # skill-driven — per-baseline overlay for each entry in ``skill_input.baselines`` (path updated by issue #544; was ``baseline_<name>/`` pre-#544)
    ├── <algo>/<algo>_config.yaml           # per-algorithm treatment overlay
    ├── <algo>/<algo>_output.json           # per-algorithm skill output (existence probed by --resume)
    └── <algo>/{cmd,pkg}/                    # translated Go plugin source
```

The `generated/` subtree replaces the pre-refactor `workspace/runs/<run>/generated/` shape — one of the pain points 3D identified. Under step-2 it lives under `translations/<hash>/`, so multiple runs reuse the same translation without re-translating.

## Commands

### `sim2real translate`

Two-phase, skill-checkpointed. Same pattern the pre-refactor `prepare.py` phase-3 had.

**Phase 1 (initial run):**

```bash
sim2real translate [--force] [--experiment-root PATH]
```

1. Load `transfer.yaml` from the experiment repo.
2. Read the translation slice (via `slicer.translation_slice`).
3. Compute `translation_hash` via new `slicer.translation_hash_with_sources(manifest, experiment_root)`.
4. Look up `translations/<hash>/` — if it already exists with all `generated/<algo>/<algo>_output.json` present, exit "translation already complete" (idempotent). If it exists partially, either resume (below) or `--force` to blow it away.
5. Create the translation dir.
6. Write `translation_output.json` with `image_ref: null`, `alias = manifest.scenario`, `algorithms = [{name, source_path, notes}, ...]`.
7. Write `skill_input.json` with the material the skill needs to translate each algorithm.
8. Print the checkpoint message and exit 0.

**Phase 2 (resume):**

```bash
sim2real translate --resume [--experiment-root PATH]
# or simply: sim2real translate  (auto-detects existing translation)
```

1. Recompute `translation_hash`. Locate `translations/<hash>/`.
2. Read `translation_output.json:algorithms`.
3. For each algorithm name, check that `generated/<algo>/<algo>_output.json` exists.
4. If any missing: exit with `translation incomplete — run /sim2real-translate first (missing outputs for: <names>)`.
5. If all present: print success message and exit 0. (Nothing else to do — the skill wrote everything.)

**State machine.** Pinning the plain-vs-`--resume`-vs-`--force` interaction:

| Existing state at `translations/<hash>/` | `translate` | `translate --resume` | `translate --force` |
|---|---|---|---|
| Nothing (dir absent) | Create dir, write `skill_input.json` + `translation_output.json` with `image_ref: null`. Print checkpoint. Exit 0. | Error: `no translation to resume for hash <hash> — run 'sim2real translate' first`. Exit 2. | Same as plain `translate` — creates fresh. |
| Partial (checkpoint files present, some `generated/<algo>/<algo>_output.json` missing) | Error: `translation <hash> incomplete — run '/sim2real-translate' then 'sim2real translate --resume'`. Exit 2. | Reports missing algorithms by name, exits 2. Never mutates the dir. | Delete + recreate as if no dir existed. |
| Complete (all `<algo>_output.json` present) | Print `translation <hash> already complete — run 'sim2real build' next` and exit 0 (idempotent). | Same as plain `translate` on complete state: exit 0 with "already complete". | Delete + recreate; user must re-run the skill. |

Design rationale: **plain `translate` never mutates a partial dir.** The operator picks a lane explicitly — `--resume` to validate, `--force` to blow away and re-checkpoint. This prevents accidentally re-writing `skill_input.json` after a skill run partially wrote outputs.

**Other notes:**

- The completeness check is per-`algorithms[i].name` — matches the manifest's declared set. Extra output files (e.g., leftover from a prior run of a different algorithm set) are ignored but logged as a warning.
- No auto-execute of the skill — the operator invokes `/sim2real-translate` explicitly. Auto-fix is step-6's territory.
- `translation_hash` collision (two different experiment repos happening to hash to the same value) is treated as a real collision and errors out with the offending name/hash. `translation_hash_with_sources` covers source contents, so collision requires an actual SHA-256 collision — vanishingly unlikely.

### `sim2real build`

Extracts shared build primitives from today's `_cmd_build` (`pipeline/deploy.py:257-449`, ~215 lines) into `pipeline/lib/build.py` and calls them from a thin `sim2real build` command in `sim2real.py`. Reduces long-term divergence between `deploy.py` and `sim2real build` (step-6's auto-execute work will consolidate further). `deploy.py:_cmd_build` continues to work as-is by also routing through the new library.

```bash
sim2real build --translation NAME|PREFIX|HASH [--force-rebuild] [--skip-build]
```

**Prerequisites (checked at command entry):**

- Local: `skopeo` on PATH. Missing → exit 2 with `skopeo not found on PATH — required for registry probe (brew install skopeo, apt install skopeo, or dnf install skopeo)`.
- Local: `docker`/`podman` credential file (either `~/.docker/config.json` or `~/.config/containers/auth.json`) exists and has an entry for the target registry. If absent, probe fails and we treat the tag as absent → build. Not a hard prerequisite.
- Cluster: `registry-secret` Secret in the primary namespace. `sim2real build` follows the same check `build-epp.sh` does today.
- Translation: `translations/<hash>/generated/<algo>/<algo>_output.json` must exist for every algorithm. Missing → exit 2 with `translation <hash> incomplete — missing outputs for: <names>; run '/sim2real-translate' then 'sim2real translate --resume'`. Same completeness check as `translate --resume`.
- Manifest: `transfer.yaml:epp_image.build.{hub,name}` present. Missing → exit 2 (see Image-ref construction section above).

**Flow:**

1. Resolve `--translation` via the alias resolver → full hash. Reject invalid refs before touching disk.
2. Load `translations/<hash>/translation_output.json`. Run completeness check (per prerequisites).
3. For each `algorithms[i]`, compose `image_ref = <registry>/<repo>:<translation_hash[:12]>-<algo>`.
4. If `--force-rebuild` not passed and `algorithms[i].image_ref` is already set, run `skopeo inspect docker://<image_ref>` (registry probe). If probe succeeds AND recorded `image_digest` matches, skip this algo. If probe fails for any reason, treat as absent → build (fail-safe).
5. Build via buildkit pod, pushing with in-cluster `registry-secret`.
6. **Post-build digest inspect.** Run `skopeo inspect docker://<image_ref>` a second time to record the pushed digest. If this probe fails (e.g., transient network failure), write `image_ref` with `image_digest: null` and log a warning: `built <image_ref>; digest not recorded (skopeo inspect failed: <reason>)`. Build is considered successful — downstream still has `image_ref`. Digest can be back-filled by a later `sim2real build --force-rebuild` or a small future helper.
7. Update `algorithms[i].image_ref` and `image_digest` in `translation_output.json` via atomic write (write to `translation_output.json.tmp` in the same dir, `os.replace` over the original).
8. Repeat for each algorithm. Print per-algo success and a final summary.

**Notes:**

- The `--skip-build` flag (already in step-1) bypasses everything — no probe, no build, no metadata update. Intended for "assemble/deploy dry-run: I know the image exists" scenarios. If `--skip-build` is passed and `image_ref` is null on any algorithm, `assemble` will fail downstream with an explicit "translation not built" error (see `sim2real assemble` prerequisites below).
- `--force-rebuild` says "build even if the tag exists and matches the recorded digest." Uses the same registry-secret push path.
- Local probe auth: skopeo reads `~/.docker/config.json` and `~/.config/containers/auth.json` by default. Any developer who's `docker login`ed or `podman login`ed to their registry gets the probe working. On credential absence: probe fails, treat as "absent → build". Worst outcome is one unnecessary rebuild.
- In-cluster push auth: unchanged. `cluster.py provision`'s `registry-secret` continues to work for private registries.
- The build pod picks up per-algorithm Go source from `translations/<hash>/generated/<algo>/{cmd,pkg}/`. Baseline source (for the "unchanged component" build) comes from the framework's `<repo_name>` directory as today.
- Concurrency: atomic write via `os.replace` handles same-directory concurrent writes safely on POSIX. File locking is out of scope — two concurrent `sim2real build` invocations on the same translation are an operator mistake (documented; not defended against in-code).

### `sim2real translation register` (extension of step-1 command)

Two additions:

1. **Write the `alias` field** to `translation_output.json`, populated from the existing `--algorithm <name>` argument. No new CLI surface. Also move `image_ref`/`image_digest` from the top level into `algorithms[0]` — matches the new per-algo schema.
2. **Refuse on alias collision.** If another translation dir has the same `alias` value pointing at a **different** hash, exit 2 with `alias 'X' already assigned to translation <other-hash>; pass --force to reassign`. Re-registering the exact same content (same hash) with the same alias is a no-op (idempotent).
3. **`--force` policy: atomic reassignment.** When `--force` is passed and the alias belongs to a different hash, the resolver's write path atomically (a) opens the previous translation's `translation_output.json`, clears its `alias` field, (b) writes the new translation's `translation_output.json` with the alias set. Aliases stay globally unique — never two translations pointing at the same alias. The old translation remains resolvable by hash/prefix.

### Alias and algorithm-name validation rules

Both aliases and algorithm names appear in filesystem paths under `translations/<hash>/generated/<algo>/`. To prevent path-traversal, weird shell behavior, and cross-registry-tag collisions, both are validated at write time with:

```regex
^[A-Za-z0-9][A-Za-z0-9._-]*$
```

Additional rules:

- Max length: 128 chars.
- Reject the reserved literals `.` and `..` (path-traversal guard).
- Reject leading `-` (protects against argparse ambiguity elsewhere).
- Case-sensitive. `softreflective` and `SoftReflective` are distinct.
- No normalization — the string is used verbatim as a filename component and as a tag suffix.

Validation is enforced by `translate` (on scenario name), `translation register` (on `--algorithm`), and the resolver (rejects lookups that don't match the regex before scanning).

### `sim2real assemble` (extension of step-1 command)

Two changes:

1. **Resolver-friendly `--translation`.** Pass through the alias resolver before using. Argparse block stays the same. Errors: `no translations in workspace/translations/`, `prefix 'abc' matches 2 translations: abc123..., abc456...`, `--translation 'X' not found; run 'sim2real list translations' to see available`.
2. **Incomplete-translation check.** For every algorithm listed in the resolved `transfer.yaml:algorithms`, verify `translation_output.json:algorithms[i].image_ref` is non-null. If any are null: exit 2 with `translation <alias-or-hash> not built for algorithms: <names> — run 'sim2real build --translation <alias-or-hash>' first`. Same posture as step-1's "run `sim2real assemble` first" error — no auto-fix, actionable message.

### Alias resolver

New helper in `pipeline/lib/translation_ref.py`. Explicit `translations_dir` parameter for testability (matches step-1's pattern of layout-singleton with test override).

```python
def resolve_translation_ref(
    ref: str,
    translations_dir: Path | None = None,
) -> str:
    """Resolve a name/prefix/hash to a full translation_hash.

    When ``translations_dir`` is omitted, defaults to ``layout.translations_dir()``.

    Rules:
      1. Reject refs that don't match the validation regex ([A-Za-z0-9][A-Za-z0-9._-]*).
      2. If ref is exactly 64 hex chars → return as-is if translations/<ref>/ exists,
         else error "no such translation hash".
      3. Otherwise scan translations/<hash>/translation_output.json files;
         collect (hash, alias) pairs. Malformed files are logged as warnings and skipped.
      4. If any alias equals ref exactly → return that hash.
      5. Else if any hash starts with ref (min prefix 4 chars) →
         return the unique match; error on ambiguity listing candidates.
      6. Else error "no such translation 'X'; run 'sim2real list translations' to see available".
    """
```

Consumers: `sim2real build`, `sim2real assemble`, `sim2real list translations`, and (implicitly) `sim2real translate --resume` when the user passes an explicit ref instead of recomputing from the current `transfer.yaml`.

### `sim2real list translations`

Ships in step-2 (PR 2). Enumerates all `translations/<hash>/` with alias / source / image / created-at columns. Small (~40 lines).

```bash
sim2real list translations
```

Output:

```
ALIAS                HASH         SOURCE   IMAGES                CREATED
softreflective-v1    abc123def456 skill    1 built               2026-07-02 14:00
compare-a-b          def456abc7ab skill    2 pending             2026-07-02 14:30
-                    fedcba98…    byo      1 registered          2026-07-01 10:00
```

- `IMAGES` column summarizes state across `algorithms[i].image_ref`: `N built` if all are set, `N pending` if any are null, `N registered` for BYO's pre-built refs.
- `-` in ALIAS column means no alias (either legacy step-1 translation or one whose alias was cleared by `--force` reassignment).
- Sorted newest-first by `created_at`.

Included in PR 2 (alongside the resolver) because resolver error messages already reference the command — shipping them together avoids the "error message points at a missing command" gap.

### `/sim2real-translate` skill re-enable

The skill currently at `.claude/skills/sim2real-translate/SKILL.md` is stubbed as DISABLED. Step-2 restores it:

1. Rewrite the skill prompt (`SKILL.md` + `prompts/agent-writer.md` + `prompts/agent-reviewer.md`) to read from `translations/<hash>/skill_input.json` and write to `translations/<hash>/generated/<algo>/{cmd,pkg,<algo>_output.json}`.
2. Remove references to the pre-refactor `prepare.py` from the skill's prompts (still present per plan-doc PR #464's sweep notes).
3. Update the skill's `user-invocable` field from `false` back to appropriate default. Update `description` to remove the "DISABLED for step-1" preamble.
4. Update or restore the skill's tests at `.claude/skills/sim2real-translate/tests/` to run against the new paths (the tests exist today per step-1 CI listing).

The core translation semantics (what the skill does inside `agent-writer` and `agent-reviewer`) do not change. Only path references and the `user-invocable` gate.

## Layout on disk after step-2

```
workspace/
├── clusters/<cluster_id>/cluster_config.json        # from step-0
├── translations/<translation_hash>/
│   ├── translation_output.json                       # step-1 (BYO writes) + step-2 (translate writes; both write alias)
│   ├── registered.json                               # BYO-only (from step-1); unchanged
│   ├── skill_input.json                              # NEW step-2 — written by translate; read by skill
│   └── generated/
│       ├── baseline_config.yaml                      # step-1 register writes (BYO); step-2 skill writes when the manifest declares a baseline overlay
│       ├── <algo>/<algo>_config.yaml                 # step-1 register writes (BYO); step-2 skill also writes
│       ├── <algo>/<algo>_output.json                 # NEW step-2 — skill writes; --resume probes for it
│       └── <algo>/{cmd,pkg}/                          # NEW step-2 — skill writes translated Go source
└── runs/<run_name>/                                  # unchanged from step-1
```

## Child-PR breakdown

Five PRs. Each sized ~1-2 days.

### PR 1 — Slicer extension

Add `translation_hash_with_sources(manifest: dict, experiment_root: Path) -> str` to `pipeline/lib/slicer.py`.

- Reads each `algorithms[i].source` file's bytes from `experiment_root`, computes SHA-256, folds into the canonical-JSON envelope alongside the existing `translation_slice(manifest)`.
- Tests: both algorithms present, one missing (error), source file empty, source file binary content, multi-algorithm.

### PR 2 — Alias resolver + schema plumbing + `list translations`

- Add `pipeline/lib/translation_ref.py:resolve_translation_ref(ref, translations_dir=None)`.
- Alias / algorithm-name validation regex + reserved-name checks (shared helper).
- Extend `sim2real.translation register`: write `alias` field, move `image_ref`/`image_digest` into `algorithms[i]` (schema migration for BYO). Refuse on alias collision without `--force`; atomic reassignment on `--force`.
- Extend `sim2real assemble --translation` to route through the resolver.
- Add `sim2real list translations` subcommand.
- Tests: name lookup, prefix lookup (unique + ambiguous), full-hash lookup, no-match, alias collision on register, `--force` reassignment atomicity, list output formatting, on-read shim for legacy top-level `image_ref`, validation regex rejects (path traversal, `.`, `..`, leading `-`, oversized names).

### PR 3 — `sim2real translate` command

- Add `sim2real translate` subcommand (initial-run path + `--resume` path).
- Initial run: compute hash via slicer, create translation dir, write `skill_input.json` + `translation_output.json` (with `alias`, `algorithms`, `image_ref: null`), print checkpoint message, exit.
- Resume: read `translation_output.json:algorithms`, probe each `generated/<algo>/<algo>_output.json`, error on missing.
- Tests: initial-run happy path, --force behavior, --resume happy path, --resume with missing algo outputs, idempotency (already-complete → exit 0), hash-collision.

### PR 4 — `/sim2real-translate` skill re-enable + prompt update

- Remove DISABLED preamble from `.claude/skills/sim2real-translate/SKILL.md`.
- Rewrite prompts (`agent-writer.md`, `agent-reviewer.md`) to reference new paths (`translations/<hash>/skill_input.json`, `translations/<hash>/generated/<algo>/{cmd,pkg,<algo>_output.json}`).
- Sweep for stale `prepare.py` refs in the skill (flagged as followup in PR #455).
- Update skill's tests at `.claude/skills/sim2real-translate/tests/` to run against new paths.
- Update `.github/workflows/test.yml` if any new test paths need explicit listing.

### PR 5 — `sim2real build` command + docs

- Extract shared build primitives from `pipeline/deploy.py:_cmd_build` into `pipeline/lib/build.py`: image-ref construction, buildkit pod dispatch, registry secret check, post-build inspect, atomic metadata write. Refactor `deploy.py:_cmd_build` to route through the same helpers (no behavior change; sets up step-6's consolidation).
- Add `sim2real build --translation NAME|PREFIX|HASH [--force-rebuild] [--skip-build]` in `sim2real.py`, wired to the new library.
- Prerequisites: skopeo-on-PATH check (fail-early), translation completeness check (fail-early), `epp_image.build.{hub,name}` check.
- Registry probe via `skopeo inspect` with fall-through-to-build on any failure; distinguish pre-build inspect (treat-as-miss) from post-build inspect (warn + null digest).
- Update `algorithms[i].image_ref` and `image_digest` via atomic tempfile-and-rename.
- Extend `sim2real assemble` with the incomplete-translation prerequisite check (per Commands section).
- Update `pipeline/README.md` documenting: `translate` / `/sim2real-translate` / `--resume` flow, `build`'s registry-probe behavior, alias resolution UX, common failure modes, end-to-end example.
- Update `CLAUDE.md` documenting the alias UX + the new `translations/<hash>/` schema fields.
- Tests: registry-probe-hit skips build, registry-probe-miss triggers build, `--force-rebuild` overrides skip, `--skip-build` bypasses everything, probe-auth-failure treated as miss, missing-skopeo fails cleanly, post-build inspect failure records `image_ref` with null digest + warning, `image_ref`/`image_digest` written back per-algo, incomplete-translation exits with actionable error, `assemble` fails early on null `image_ref`.

### PR 6 — CI, docs, and dead-code sweep

Final cleanup pass over the surface introduced by PRs 1–5. Land last.

- **CI.** `.github/workflows/test.yml` lists individual test files explicitly (per today's `CLAUDE.md` §CI); the existing `pipeline/` glob is not enough. Audit the workflow and add entries for every new test file created by PRs 1–5 — expected additions: `pipeline/tests/test_translation_ref.py` (PR 2), `pipeline/tests/test_translate.py` (PR 3), `pipeline/tests/test_build.py` (PR 5), plus any new golden-file / integration test files. Verify `ruff check pipeline/ .claude/skills/ --select F` runs clean against the merged tree.
- **Documentation.** Verify PR 5's `pipeline/README.md` and `CLAUDE.md` updates cover every user-facing surface added or changed (`translate`, `build`, `list translations`, alias resolver, `--force-rebuild`/`--skip-build`, new per-algo `translation_output.json` schema, the `translations/<hash>/` layout). Update module-level docstrings in touched files (`sim2real.py`, `pipeline/lib/slicer.py`, new `pipeline/lib/translation_ref.py`, new `pipeline/lib/build.py`). Fix any stale references to the pre-refactor `prepare.py` / `runs/<run>/generated/` that survived step-0/step-1 outside of the skill.
- **Dead-code sweep beyond the skill.** PR 4 sweeps `.claude/skills/sim2real-translate/`; this task sweeps everywhere else. Targets: DISABLED-era comments and dead branches in `sim2real.py` and `deploy.py`, unused imports and orphaned helpers exposed after the `_cmd_build` extraction in PR 5 (once both callers route through `pipeline/lib/build.py`, remove any leftovers in `deploy.py` that no consumer references), obsolete overlay-path constants, and any TODO/FIXME markers that PRs 1–5 resolved. Delete rather than mark. **Keep the on-read compat shim** in `pipeline/lib/translation_ref.py` for the legacy top-level-`image_ref` shape — that's live compat, not dead code.
- No new production behavior; no new tests beyond what CI needs to keep working.

## Ordering constraints

- PR 1 has no dependencies. Land first.
- PR 2 depends on PR 1 (nothing structural; both touch different files) — but the alias-field write to `translation_output.json` establishes the schema that PR 3 and later PRs read. Land PR 2 second.
- PR 3 depends on PR 1 (uses `translation_hash_with_sources`) and PR 2 (writes the alias field).
- PR 4 depends on PR 3 (the skill's `skill_input.json` reads what `translate` writes).
- PR 5 depends on PR 2 (uses the alias resolver for `--translation`) and, for end-to-end, PR 3 (needs a translation dir to build against — either skill-driven from PR 3 or BYO-registered from step-1).
- PR 6 depends on all of PRs 1–5; lands last so the audit sees the final merged surface.

## Risks

1. **`_cmd_build` extraction to shared library.** ~215 lines of build logic moved into `pipeline/lib/build.py` with two callers (`sim2real build` and `deploy.py:_cmd_build`). Refactor risk: subtle behavior drift while touching many call sites. Mitigation: (a) extraction lands as a no-op refactor first (deploy.py still passes its tests); (b) exercise PR 5 against a real cluster before merging.
2. **Multi-algorithm schema drift.** `algorithms[i].image_ref` and `image_digest` are the new normal; step-1 BYO `translation_output.json` files have the fields at the top level. The on-read shim in `pipeline/lib/translation_ref.py` recognizes both shapes so existing translations remain resolvable; but every consumer path (assemble, list translations, resolver, build) needs to be probed against both shapes in tests. Regression risk if a consumer path is missed.
3. **Registry probe on private registries.** Any developer whose `~/.docker/config.json` / `~/.config/containers/auth.json` doesn't have the target registry gets a probe failure. Our fail-safe direction (probe failure → build) handles it — worst case is one unnecessary rebuild. Document in `pipeline/README.md`.
4. **Alias collision on re-register.** BYO `register --algorithm foo` twice with different content used to succeed silently (hashes differed → separate translation dirs). Now the alias uniqueness rule refuses without `--force`, and `--force` reassigns atomically. Communicate in changelog; the previous behavior was arguably a bug.
5. **Skill prompt regressions.** The `/sim2real-translate` skill has been DISABLED since step-1 landed; re-enabling exposes any drift between its prompts and the current codebase. Skill's own tests should catch this pre-merge. The pinned `skill_input.json` schema (see Schemas section) is the primary defense against drift — anything the skill needs must be named in that schema.
6. **Concurrency around mutable `translation_output.json`.** Two `sim2real build` invocations against the same translation could race on the atomic write. Atomic replace via `os.replace` handles concurrent writes safely on POSIX, but the last writer wins — no locking. Documented; not defended against in code (single-operator workflow).
7. **Missing local `skopeo`.** New hard prerequisite for `sim2real build`. Fail-early message names the install path per OS. Not a soft fallback because post-build digest recording depends on it.
8. **Toolchain drift on shared registry.** Documented gotcha (Q1). More than a "gotcha" per reviewer — the operational consequence is stale image at same tag if two developers on different Go versions build the same translation hash. Small-team pinning via `go.mod` + `--force-rebuild` for the edge case. Add a `pipeline/README.md` note.

## Deferred to later steps

Per plan doc:

- Auto-execute (`deploy.py run` auto-chains `assemble` and `build` when needed) → step-6.
- Replicas + `--iteration` filter → step-5.
- `sim2real-check` skill port → step-3.
- `sim2real-bootstrap` skill port + `--byo` mode → step-4.
- Multi-algorithm BYO (`register --algorithm a --image ... --algorithm b --image ...`) → out-of-band.
- Toolchain-locking (`translation_hash` includes Go version / base image) → out-of-band; `--force-rebuild` is the current escape.

## Out-of-band

Attach to this epic as small line-items or file as followups against #463:

- **`sim2real list translations`** — moved into PR 2 (see Child-PR breakdown). Not out-of-band anymore.
- **`pipeline/README.md` / `CLAUDE.md`** — moved into PR 5.
- **Migration note for existing step-1 translations.** `translation_output.json` files written before PR 2 have no `alias` field and carry `image_ref`/`image_digest` at the top level. The on-read shim in `pipeline/lib/translation_ref.py` accepts both shapes so existing translations remain reachable by hash/prefix; they're missing an alias until re-registered. `version` stays `1` — the field-set is a strict superset, so no version bump is warranted (a `version: 2` bump would be reserved for a breaking-shape change).
- **Multi-algorithm BYO** (`register --algorithm a --image ... --algorithm b --image ...`) — deferred per `three-dimensional-sim2real.md` §open question 4. Step-2 lays the schema groundwork by moving `image_ref`/`image_digest` per-algo, so multi-algo BYO is a small future CLI change on top of the same schema.
- **Toolchain-locking** (`translation_hash` includes Go version / base image) — out-of-band; `--force-rebuild` is the current escape hatch.

## Testing strategy

- **Unit tests** per PR (see per-PR sections above).
- **Golden-file hash-stability tests** (PR 1): pin known-input → known-output SHA-256 values for `translation_hash_with_sources` so accidental canonicalization changes get caught. Cover: single-algorithm, multi-algorithm, algorithm-order stability, empty source file, unicode content.
- **Schema-validation tests** (PR 2): reject shapes that don't match the pinned `translation_output.json` schema; accept the legacy top-level-`image_ref` shape via the on-read shim. Malformed metadata during resolver scans logs a warning and is skipped (doesn't crash the resolver).
- **Path-safety tests** (PR 2): reject alias `../foo`, `..`, `.`, `foo/bar`, empty string, oversized names (>128 chars), leading `-`, all-unicode-punctuation. Applied to both alias and algorithm names.
- **Integration test**: `test_translate_and_build_e2e` (PR 5) — build a minimal experiment on disk, run `sim2real translate` (mock the skill to write minimal `generated/<algo>/<algo>_output.json` + `<algo>_config.yaml` + `{cmd,pkg}/`), run `sim2real translate --resume`, run `sim2real build` against a mock registry (mock `skopeo inspect` to return miss on first probe, hit on second), verify `algorithms[i].image_ref` and `image_digest` get populated per-algo.
- **Backward-compat tests** (PR 2, PR 5): resolver + `list translations` + `sim2real build` all work against a `translation_output.json` produced by step-1's `register` (top-level `image_ref`, no `alias`).
- **Missing-skopeo test** (PR 5): PATH manipulation → `sim2real build` exits 2 with the install-hint message before touching disk.
- **Real-cluster gate**: end-to-end skill-driven demo — `translate` → `/sim2real-translate` → `translate --resume` → `build` → `assemble` → `deploy.py run` → `deploy.py collect` → per-algo `per_request_lifecycle_metrics.json` on a real cluster. Same gate posture as step-1's demo (not automatable from the harness).

## Success criterion for the epic

For any experiment repo with a valid `transfer.yaml` (including `algorithms[]` populated), the operator can:

```bash
sim2real translate                                                    # produces skill_input.json + translation_output.json
/sim2real-translate                                                   # skill writes Go source + configs
sim2real translate --resume                                           # validates completeness
sim2real build --translation <alias-or-prefix>                        # builds + pushes image
sim2real assemble --translation <alias-or-prefix> --cluster <id> --run <name>
deploy.py --run <name> run
deploy.py --run <name> collect
```

And find `workspace/runs/<name>/results/<algo>/<workload>/per_request_lifecycle_metrics.json` on their local disk. That's the step-2 demo gate.
