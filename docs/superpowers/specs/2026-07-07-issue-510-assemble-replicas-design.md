# Issue #510 — `assemble` additive-merge + `replicas` consumption (grow-only)

Issue: [#510](https://github.com/inference-sim/sim2real/issues/510)
Parent epic: [#502 — step-5 Replicas + iteration filtering](https://github.com/inference-sim/sim2real/issues/502)
Base branch: `refactor/v2-step-5`

## Scope

Grow `pipeline/lib/assemble_run.py` from the step-1 single-replica shape to
`--replicas N` support with an additive-merge (grow-only) invariant.
Introduces the `replicas: N` field on `manifest.assembly.yaml` and expands
the emitted pair-key set from `(workload, package)` to
`(workload, package, iteration ∈ 1..N)`.

All prior refactor-branch runs are legacy shape. Since the refactored code
has not shipped, we do not preserve dual shapes: every assemble in this PR
emits the canonical pipe-shape pair keys and the legacy-shape guard exists
only to catch developers on `refactor/v2-step-5` who assembled runs before
this PR landed.

## Non-goals (deferred)

- New `replica` PipelineRun spec param and `pipeline.yaml` threading (PR 3).
- `validate_pipelinerun_name` length check (PR 3).
- `_load_pairs`, `_apply_run_filters`, `--iteration` filter (PR 3, PR 4).
- `sim2real-check` `iN/` traversal (PR 5).
- Shrink support (`--force-shrink`, tracked in #506).

## Canonical shapes

Every assembled artifact carries an iteration marker, including single-replica
default runs (which internally set `--replicas 1`):

| Artifact                    | Shape                                                   |
| --------------------------- | ------------------------------------------------------- |
| PipelineRun filename        | `pipelinerun-<workload-safe>\|<package>\|i<N>.yaml`     |
| k8s `metadata.name`         | `<safe_phase>-<safe_workload>-<run>-i<N>`               |
| `_load_pairs` derived key   | `wl-<workload>\|<package>\|i<N>` (grammar-canonical)    |
| `manifest.assembly.yaml`    | Gains `replicas: N` top-level field                     |

`<workload-safe>` continues to replace `_` with `-` (existing behavior).
The `|` characters in filenames are unusual but functional — `Path.glob`
and `open()` handle them; `workspace/runs/` is not shell-browsed. The k8s
`metadata.name` remains RFC-1123 compliant (lowercase + hyphens).

`_load_pairs` is not touched in this PR: its existing filename-stem key
derivation (`"wl-" + stem.removeprefix("pipelinerun-")`) naturally emits
canonical grammar-matching keys once filenames use pipes.

## Data flow

### Fresh assemble (`--replicas N`, `N >= 1`)

1. Validate translation + cluster (unchanged).
2. Load manifest, filter algorithms (unchanged).
3. Write `manifest.assembly.yaml` = `assembly_slice(manifest)` with
   `replicas: N` merged into the top-level dict.
4. Compute `params_hash` = SHA-256 of canonical bytes of the assembly
   slice **with `replicas` removed** (see rationale below).
5. Resolve baseline + treatment scenarios (unchanged).
6. Inject image + HF-secret (unchanged).
7. Write `cluster/<package>.yaml` scenario files (unchanged).
8. Generate `N × (workloads × packages)` PipelineRun files, one per
   iteration `i1..iN`, with the shape above.
9. Write `run_metadata.json` with `replicas: N` and `params_hash` from step 4.

### Re-assemble on existing run

Preconditions loaded before any writes:

- `prior_manifest = load(runs/<R>/manifest.assembly.yaml)`
- `prior_replicas = prior_manifest.get("replicas")` (int or `None`)
- `new_content_hash` = SHA-256 of canonical bytes of the new assembly slice
  with `replicas` removed.
- `prior_params_hash` from `runs/<R>/run_metadata.json`.

Decision table (top-down; first match wins):

| Prior state / request                                     | Action                                                                                     |
| --------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| `prior_replicas` is `None` (legacy) + `--force`           | `shutil.rmtree(run_dir)` then fresh assemble at `N`.                                       |
| `prior_replicas` is `None` (legacy) + no `--force`        | Refuse: `run '<R>' is in legacy single-replica shape; create a fresh run to use --replicas.` |
| `N < prior_replicas`                                      | Refuse (grow-only; `--force` does NOT bypass): `run '<R>' already has {prior_replicas} replicas; refusing to shrink to {N}. Replica shrink is tracked in #506.` |
| Content drift (`new_content_hash != prior_params_hash`) + no `--force` | Refuse: `manifest content changed since last assemble for run '<R>'; pass --force to overwrite.` |
| Content drift + `--force`                                 | `rmtree` + fresh assemble at `N`.                                                          |
| `N == prior_replicas`, no drift                           | **No-op** (idempotent, no writes, mtimes preserved).                                       |
| `N > prior_replicas`, no drift                            | Additive merge (below).                                                                    |

### Additive merge

1. Emit new PipelineRun files for iterations `prior_replicas+1 .. N`. Existing
   `i1..i<prior_replicas>` files are not read, not compared, not touched — their
   mtime is preserved.
2. Rewrite `manifest.assembly.yaml` with `replicas: N`. All other fields byte-
   identical to prior (since no drift).
3. Rewrite `run_metadata.json` with:
   - `params_hash` byte-identical to prior (drift excluded from hash).
   - `assembled_at` = new ISO timestamp.
   - `replicas` = new `N`.
   - other fields unchanged.
4. Do NOT rewrite the `cluster/<package>.yaml` scenario files (unchanged content).

## `params_hash` semantics

`params_hash` computation excludes the `replicas` field. Concretely:

```python
def compute_params_hash(manifest_assembly_path: Path) -> str:
    data = yaml.safe_load(manifest_assembly_path.read_text())
    data.pop("replicas", None)
    canonical = yaml.dump(data, sort_keys=True, default_flow_style=False,
                          allow_unicode=True).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
```

Rationale: bumping `--replicas 3→5` must not trip the drift check. Design
says drift detection is unchanged from proposal ("refuse content changes
without `--force`"). Excluding `replicas` from the hash makes replica-count
bumps semantically distinct from content changes, so drift semantics are
preserved by construction rather than via special-case comparison logic.

Step-1 wrote `params_hash` for future use; nothing else reads it. This PR
is the first consumer, so we own the definition.

## CLI (pipeline/sim2real.py)

```python
def _positive_int(s: str) -> int:
    try:
        v = int(s)
    except ValueError:
        raise argparse.ArgumentTypeError(f"must be a positive integer, got {s!r}")
    if v < 1:
        raise argparse.ArgumentTypeError(f"must be >= 1, got {v}")
    return v

asm.add_argument(
    "--replicas",
    type=_positive_int,
    default=1,
    metavar="N",
    help="number of replica iterations per (workload, package) pair (default: 1)",
)
```

`_cmd_assemble` passes `replicas=args.replicas` into `assemble_run(...)`.

## `assemble_run` signature change

```python
def assemble_run(
    *,
    translation_hash: str,
    translation_ref: str,
    cluster_id: str,
    run_name: str,
    experiment_root: Path,
    manifest_path: Path,
    force: bool,
    replicas: int,                # NEW
    now_iso: str,
) -> None: ...
```

## `generate_pipelineruns` signature change

Adds an `iterations` iterable so callers can request a specific subset (for
additive merge: pass `range(prior_replicas + 1, new_replicas + 1)`):

```python
def generate_pipelineruns(
    *,
    run_dir: Path,
    packages: list[tuple[str, dict]],
    workloads: list[dict],
    run_name: str,
    cluster_config: dict,
    pipeline_name: str,
    observe: dict,
    model_name: str,
    submodule_shas: dict,
    submodule_urls: dict,
    iterations: range | list[int],   # NEW; iterations to emit (1-indexed)
) -> None: ...
```

`make_pipelinerun_scenario` (in `pipeline/lib/tekton.py`) gains an
`iteration: int` param and appends `-i{iteration}` to the k8s `metadata.name`.

## Legacy-run detection

A run is legacy iff its `manifest.assembly.yaml` has no `replicas` key. This
covers any run assembled before this PR merged (all step-1 runs). We do not
also inspect filenames — the `replicas` field is the single source of truth.

## Error contract

Errors raised as `AssembleError` with the messages in the decision table
above. All errors happen before any writes to `run_dir` (existing invariant
preserved). The additive-merge write phase has no failure modes that could
partially corrupt state: new PipelineRun files are independent, and
`manifest.assembly.yaml` + `run_metadata.json` rewrites are atomic
(`write_text` truncates + writes in one syscall).

## Testing

### Existing test updates (`pipeline/tests/test_assemble_run.py`)

- `test_one_pipelinerun_per_workload_x_package` — filename assertions become pipe-shape with `|i1`.
- `test_end_to_end_produces_expected_files` — same update.
- Any other test asserting on filename or `metadata.name` shape (grep during implementation).

### New test file: `pipeline/tests/test_assemble_replicas.py`

Ten unit/integration tests covering the decision table:

1. Fresh run `--replicas 3` → 3 files per (workload, package), all with pipe-shape `|i1..|i3`, `manifest.assembly.yaml` has `replicas: 3`.
2. Existing at 3 + `--replicas 5` → preserves i1..i3 file contents byte-for-byte AND mtime; adds i4, i5; `manifest.assembly.yaml` and `run_metadata.json` rewritten with `replicas: 5`; `params_hash` byte-identical to prior.
3. Existing at 3 + `--replicas 2` → refuses, error names count `3` and mentions `#506`.
4. Existing at 3 + `--replicas 3` → true no-op: no file mtimes change (including `manifest.assembly.yaml` and `run_metadata.json`).
5. Legacy run (no `replicas` in manifest) + `--replicas 3` → refuses with legacy-shape message.
6. Legacy run + `--replicas 3 --force` → rmtree + fresh assemble at 3.
7. Content drift + `--replicas 5` (was 3) + `--force` → rmtree + fresh assemble at 5.
8. Content drift + `--replicas 5` (was 3) + no `--force` → refuses with drift error.
9. `--replicas 0` and `--replicas -1` → argparse rejects with `_positive_int` error.
10. Fresh run **without** `--replicas` flag → produces pipe-shape files with `|i1` and `manifest.assembly.yaml` has `replicas: 1` (default-arg behavior; documents the migration-visible shape change).

Argparse-level test (in `pipeline/tests/test_sim2real.py` if there's a
subparser test harness there; otherwise inline in `test_assemble_replicas.py`
via `parse_args`).

## CI

Add `pipeline/tests/test_assemble_replicas.py` to `.github/workflows/test.yml`
in the explicit pytest path list (per CLAUDE.md rule).

## Files changed

| File | Change |
| --- | --- |
| `pipeline/lib/assemble_run.py` | Additive-merge logic; `replicas` param on `assemble_run`; `iterations` param on `generate_pipelineruns`; params_hash excludes `replicas`; `manifest.assembly.yaml` gains `replicas: N`; `run_metadata.json` gains `replicas: N`. |
| `pipeline/lib/tekton.py` | `iteration: int` param on `make_pipelinerun_scenario`; `-i{N}` suffix on `metadata.name`. |
| `pipeline/sim2real.py` | `_positive_int` helper; `--replicas` arg on asm subparser; wire into `_cmd_assemble`. |
| `pipeline/tests/test_assemble_run.py` | Update shape assertions in affected tests. |
| `pipeline/tests/test_assemble_replicas.py` | New: 9 replica tests. |
| `.github/workflows/test.yml` | Add new test file to pytest path list. |

## Open follow-ups (not blocking this PR)

- Documentation update in `pipeline/README.md` and CLAUDE.md is PR 6 per
  epic ordering; don't update in this PR.
