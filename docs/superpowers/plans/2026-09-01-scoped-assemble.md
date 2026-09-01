# Scoped `sim2real assemble` + non-destructive `--force` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `sim2real assemble` `--workload` / `--package` scoping, stop `--force` from `rmtree`-ing the whole run directory (which destroys collected `results/`), and replace the false "inputs are unchanged" no-op message.

**Architecture:** Replace the single run-dir-wide `shutil.rmtree` escape hatch with a per-pair decision evaluated over the manifest cross product. `--force` keeps its meaning ("this PipelineRun already exists — redo it") but now applies per pair rather than per run dir; a new orthogonal `--no-wipe` controls whether collected results for a regenerated pair are discarded. Scope is derived from the manifest (workload YAML names × package names), never from the ConfigMap-backed progress store, because assemble has no cluster.

**Tech Stack:** Python 3.10+, argparse, PyYAML, pytest.

**Spec:** GitHub issue #876 (`gh issue view 876`), plus three operator decisions recorded in the issue thread and restated under Global Constraints.

## Global Constraints

- Python >= 3.10. Tests: `python -m pytest pipeline/ -v`. Lint: `ruff check pipeline/ .claude/skills/ --select F`. Coverage gate: `--cov=pipeline --cov-fail-under=90`.
- **A scoped assemble rewrites `cluster/<pkg>.yaml`, and leaves `manifest.assembly.yaml` and `run_metadata.json:params_hash` untouched.** Operator decision. Rationale: `params_hash` then keeps meaning "the last state in which the *whole* run was consistent", so a later unscoped assemble still detects manifest drift instead of silently reporting nothing to do.
- **No `--iteration` and no `--only` on assemble.** Operator decision: the iteration count is fixed for all pairs, and `--only` is slated for deprecation. `--workload` + `--package` are the only scope axes.
- **Do not add special handling for workload names containing `_`.** Operator decision: the `_` → `-` substitution is slated for deprecation. Existing producers already use the raw name for `results/<pkg>/<wl>/` and the substituted name for `cluster/pipelinerun-<wl>|...`; mirror them faithfully and compare filter values with a normalizing comparison so either spelling matches.
- Never write to a path outside the worktree. Every absolute path must contain `.claude/worktrees/issue-876-scoped-assemble/`.
- `AssembleError` is the only exception type raised out of `pipeline/lib/assemble_run.py`; `sim2real.py` turns it into `error: <msg>` on stderr and exit code 2.

---

## Behavior specification

### Scope

`scope` is the cross product of workload names (the `name` field of each loaded workload YAML) and package names (`"baseline"` plus each kept algorithm name), narrowed by `--workload` / `--package`. Filter values support shell globs and comma-separated lists, exactly as `deploy` does. An unmatched filter value raises `AssembleError` naming the valid values.

### Per-pair decision table

Evaluated for every `(workload, package, iteration)` triple in `scope × 1..replicas`:

| PipelineRun exists | results exist | behavior |
|---|---|---|
| no | no | generate |
| no | yes | generate; wipe results unless `--no-wipe` (**prompt first**) |
| yes | no | nothing unless `--force` |
| yes | yes | nothing unless `--force`; when `--force`, wipe results unless `--no-wipe` |

- PipelineRun predicate: `run_dir/cluster/pipelinerun-<wl with _ replaced by ->|<pkg>|i<N>.yaml`.
- Results predicate: `run_dir/results/<pkg>/<wl>/i<N>/` (raw workload name).
- The prompt fires only for the `--force`-absent case (row 2), where the operator typed no flag authorizing data loss. With `--force`, the wipe is the already-documented behavior and stays non-interactive so CI keeps working; `--no-wipe` is the opt-out. `--yes` skips the prompt; EOF on the prompt aborts with `AssembleError`.

### Mode rules

- **Unscoped** (neither `--workload` nor `--package`): `--replicas` defaults to 1 as today; run-wide files (`manifest.assembly.yaml`, `run_metadata.json`) are written; orphan `cluster/` files outside the current cross product are pruned with a warning.
- **Scoped**: requires an existing run; rejects `--replicas` (iterations come from the run's recorded `replicas`); refuses on manifest drift regardless of `--force`; writes neither `manifest.assembly.yaml` nor `run_metadata.json`; prunes nothing.
- Structural-repair branches (missing / corrupt `manifest.assembly.yaml` or `run_metadata.json`, legacy pre-`replicas` shape) still require `--force`, and refuse outright in scoped mode.
- `--replicas N` greater than the recorded count without `--force` still takes the existing additive-grow path, unchanged.
- When the regeneration set is empty, nothing is written at all (so a plain re-assemble stays byte- and mtime-identical) and the CLI prints the new message.

---

## File Structure

| File | Responsibility |
|---|---|
| `pipeline/lib/scope.py` | **Create.** CLI filter-value primitives (`parse_name_list`, `is_glob`, `expand_glob_values`) lifted out of `deploy.py` so `deploy` and `assemble` share one implementation. |
| `pipeline/deploy.py` | **Modify.** Delete the three lifted functions; import them from `pipeline.lib.scope` under their existing private names so no call site changes. |
| `pipeline/lib/assemble_run.py` | **Modify.** Add `pipelinerun_filename`, `resolve_pair_scope`, `PairPlan`, `plan_pairs`, `_confirm_results_wipe`, `prune_orphan_cluster_files`; teach `generate_pipelineruns` a `triples` argument; rewire `assemble_run` to remove all six `shutil.rmtree(run_dir)` calls and drive writes from the pair plan. |
| `pipeline/sim2real.py` | **Modify.** Add `--workload`, `--package`, `--no-wipe`, `--yes` to the `assemble` subparser; change `--replicas` default to `None`; replace the no-op message; surface prune and wipe warnings. |
| `pipeline/tests/test_scope.py` | **Create.** Unit tests for the lifted primitives. |
| `pipeline/tests/test_assemble_scope.py` | **Create.** Unit + integration tests for scope resolution, the pair table, wipe/prompt, and pruning. |
| `pipeline/tests/test_assemble_run.py` | **Modify.** Rewrite `test_force_overwrites_existing_run` (it currently asserts the bug). |
| `pipeline/tests/test_assemble_replicas.py` | **Modify.** Rewrite the three sentinel-destruction assertions; rename `test_drift_with_force_rmtree_rebuilds`. |
| `pipeline/tests/test_sim2real.py` | **Modify.** Rewrite `test_noop_reassemble_prints_no_change_message`; add CLI-level scope tests. |
| `pipeline/README.md`, `CLAUDE.md` | **Modify.** Document the new flags and the `--force` semantics change. |

---

### Task 1: Shared CLI filter primitives

**Files:**
- Create: `pipeline/lib/scope.py`
- Modify: `pipeline/deploy.py` (delete `_parse_list` at `:2237-2246`, `_GLOB_METACHARS`/`_is_glob` at `:2249-2253`, `_expand_glob_values` at `:2256-2294`; add import)
- Test: `pipeline/tests/test_scope.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `pipeline.lib.scope.parse_name_list(value) -> list[str] | None`, `pipeline.lib.scope.is_glob(value: str) -> bool`, `pipeline.lib.scope.expand_glob_values(values: Iterable[str], valid: Iterable[str], *, exclude_from_pattern=frozenset()) -> tuple[list[str], list[str]]`.

- [ ] **Step 1: Write the failing test**

Create `pipeline/tests/test_scope.py`:

```python
"""Unit tests for pipeline/lib/scope.py — CLI filter-value primitives
shared by deploy.py and `sim2real assemble` (issue #876)."""

from __future__ import annotations

from pipeline.lib import scope


class TestParseNameList:
    def test_none_returns_none(self):
        assert scope.parse_name_list(None) is None

    def test_splits_comma_separated_string(self):
        assert scope.parse_name_list("a,b , c") == ["a", "b", "c"]

    def test_flattens_nargs_list_with_embedded_commas(self):
        assert scope.parse_name_list(["a,b", "c"]) == ["a", "b", "c"]

    def test_empty_after_strip_returns_none(self):
        assert scope.parse_name_list([" ", ","]) is None


class TestExpandGlobValues:
    def test_literal_hit_and_miss(self):
        expanded, unknown = scope.expand_glob_values(["a", "z"], {"a", "b"})
        assert expanded == ["a"]
        assert unknown == ["z"]

    def test_pattern_expands_sorted_and_dedupes(self):
        expanded, unknown = scope.expand_glob_values(["b*", "bar"], {"bar", "baz"})
        assert expanded == ["bar", "baz"]
        assert unknown == []

    def test_pattern_matching_nothing_is_unknown(self):
        expanded, unknown = scope.expand_glob_values(["q*"], {"a"})
        assert expanded == []
        assert unknown == ["q*"]

    def test_exclude_from_pattern_keeps_token_literal_only(self):
        expanded, unknown = scope.expand_glob_values(
            ["exp*"], {"experiment", "explode"}, exclude_from_pattern={"experiment"}
        )
        assert expanded == ["explode"]

    def test_is_glob(self):
        assert scope.is_glob("a*")
        assert scope.is_glob("a?")
        assert scope.is_glob("a[bc]")
        assert not scope.is_glob("abc")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest pipeline/tests/test_scope.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pipeline.lib.scope'`

- [ ] **Step 3: Create `pipeline/lib/scope.py`**

Move the bodies verbatim from `deploy.py` (do not re-derive them — behavior parity with `deploy` is the point):

```python
"""CLI filter-value primitives shared by ``deploy.py`` and ``sim2real assemble``.

Both commands expose ``--workload`` / ``--package`` flags with the same
grammar: ``nargs="+"`` values that may additionally be comma-separated, and
that may be shell-glob patterns rather than literal names. The parsing and
glob expansion live here so the two commands cannot drift apart (issue #876).
"""

from __future__ import annotations

import fnmatch
from collections.abc import Iterable

# Any of these in a value makes it a shell-glob pattern; otherwise the value
# is a literal.
_GLOB_METACHARS = ("*", "?", "[")


def is_glob(value: str) -> bool:
    return any(c in value for c in _GLOB_METACHARS)


def parse_name_list(value) -> "list[str] | None":
    """Flatten a CLI flag value (possibly a list from nargs='+') by splitting on commas."""
    if value is None:
        return None
    if isinstance(value, list):
        result = [v.strip() for item in value for v in item.split(",") if v.strip()]
    else:
        result = [v.strip() for v in value.split(",") if v.strip()]
    return result if result else None


def expand_glob_values(
    values: "Iterable[str]",
    valid: "Iterable[str]",
    *,
    exclude_from_pattern: "Iterable[str]" = frozenset(),
) -> "tuple[list[str], list[str]]":
    """Expand a mixed list of literals and shell-glob patterns against *valid*.

    A value containing ``*``, ``?``, or ``[`` is treated as an ``fnmatch`` pattern;
    otherwise it must be a literal member of *valid*. Patterns match against
    ``valid - exclude_from_pattern`` so magic tokens (e.g. ``experiment``) remain
    literal-only and are never surfaced by a pattern like ``exp*``.

    Returns ``(expanded, unknown)`` where *expanded* preserves the order of the
    user's input (first occurrence wins) and *unknown* lists literals not in
    *valid* plus patterns that matched zero names.
    """
    pattern_pool = sorted(set(valid) - set(exclude_from_pattern))
    valid_set = set(valid)
    expanded: list[str] = []
    seen: set[str] = set()
    unknown: list[str] = []
    for v in values:
        if is_glob(v):
            matches = [n for n in pattern_pool if fnmatch.fnmatchcase(n, v)]
            if not matches:
                unknown.append(v)
                continue
            for m in matches:
                if m not in seen:
                    seen.add(m)
                    expanded.append(m)
        elif v in valid_set:
            if v not in seen:
                seen.add(v)
                expanded.append(v)
        else:
            unknown.append(v)
    return expanded, unknown
```

- [ ] **Step 4: Point `deploy.py` at the shared module**

Delete `_parse_list`, `_GLOB_METACHARS`, `_is_glob`, and `_expand_glob_values` from `deploy.py`. Add to the `pipeline.lib` import block near `:58-62`:

```python
from pipeline.lib.scope import expand_glob_values as _expand_glob_values
from pipeline.lib.scope import parse_name_list as _parse_list
```

Aliasing to the existing private names keeps all ~10 call sites unchanged. Then check whether `import fnmatch` at `:16` is still used in `deploy.py` (`grep -n 'fnmatch' pipeline/deploy.py`); if it is not, remove the import so `ruff --select F` stays clean.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest pipeline/tests/test_scope.py pipeline/tests/test_deploy_status.py pipeline/tests/test_deploy_run.py pipeline/tests/test_deploy_collect.py -v`
Expected: PASS (the deploy suites exercise the filter paths and must be unaffected)

Run: `ruff check pipeline/ --select F`
Expected: no output

- [ ] **Step 6: Commit**

```bash
git add pipeline/lib/scope.py pipeline/deploy.py pipeline/tests/test_scope.py
git commit -m "refactor(pipeline): lift CLI filter primitives into pipeline/lib/scope.py"
```

---

### Task 2: Scope resolution and the PipelineRun filename helper

**Files:**
- Modify: `pipeline/lib/assemble_run.py` (add helpers; use the filename helper inside `generate_pipelineruns` at `:513`)
- Test: `pipeline/tests/test_assemble_scope.py`

**Interfaces:**
- Consumes: `pipeline.lib.scope.parse_name_list`, `pipeline.lib.scope.expand_glob_values` (Task 1).
- Produces:
  - `pipelinerun_filename(workload: str, package: str, iteration: int) -> str`
  - `resolve_pair_scope(*, workload_names: list[str], package_names: list[str], workload_filter, package_filter) -> list[tuple[str, str]]` — returns `(workload, package)` pairs; raises `AssembleError` on an unmatched filter value.

- [ ] **Step 1: Write the failing test**

Create `pipeline/tests/test_assemble_scope.py`:

```python
"""Tests for scoped assemble (issue #876): scope resolution, the per-pair
decision table, results wiping, and orphan pruning."""

from __future__ import annotations

import pytest

from pipeline.lib import assemble_run
from pipeline.lib.errors import AssembleError


class TestPipelinerunFilename:
    def test_matches_generator_shape(self):
        assert (
            assemble_run.pipelinerun_filename("wl_a", "baseline", 3)
            == "pipelinerun-wl-a|baseline|i3.yaml"
        )

    def test_underscore_substituted_only_in_workload_segment(self):
        assert (
            assemble_run.pipelinerun_filename("a_b", "pkg_c", 1)
            == "pipelinerun-a-b|pkg_c|i1.yaml"
        )


def _scope(**kw):
    kw.setdefault("workload_names", ["wl_a", "wl_b"])
    kw.setdefault("package_names", ["baseline", "sr"])
    kw.setdefault("workload_filter", None)
    kw.setdefault("package_filter", None)
    return assemble_run.resolve_pair_scope(**kw)


class TestResolvePairScope:
    def test_no_filters_is_full_cross_product(self):
        assert _scope() == [
            ("wl_a", "baseline"), ("wl_a", "sr"),
            ("wl_b", "baseline"), ("wl_b", "sr"),
        ]

    def test_workload_filter_narrows(self):
        assert _scope(workload_filter=["wl_b"]) == [
            ("wl_b", "baseline"), ("wl_b", "sr"),
        ]

    def test_package_filter_narrows(self):
        assert _scope(package_filter=["sr"]) == [("wl_a", "sr"), ("wl_b", "sr")]

    def test_both_filters_intersect(self):
        assert _scope(workload_filter=["wl_a"], package_filter=["sr"]) == [
            ("wl_a", "sr")
        ]

    def test_comma_separated_and_glob_accepted(self):
        assert _scope(package_filter=["base*,sr"]) == [
            ("wl_a", "baseline"), ("wl_a", "sr"),
            ("wl_b", "baseline"), ("wl_b", "sr"),
        ]

    def test_underscore_and_hyphen_spellings_both_match(self):
        # The `_` -> `-` substitution is deprecated; accept either spelling
        # rather than making the operator guess which producer they are
        # naming.
        assert _scope(workload_filter=["wl-a"]) == [
            ("wl_a", "baseline"), ("wl_a", "sr"),
        ]

    def test_unknown_workload_lists_valid_values(self):
        with pytest.raises(AssembleError) as exc:
            _scope(workload_filter=["nope"])
        msg = str(exc.value)
        assert "--workload" in msg
        assert "nope" in msg
        assert "wl_a" in msg and "wl_b" in msg

    def test_unknown_package_lists_valid_values(self):
        with pytest.raises(AssembleError) as exc:
            _scope(package_filter=["nope"])
        msg = str(exc.value)
        assert "--package" in msg
        assert "baseline" in msg and "sr" in msg
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest pipeline/tests/test_assemble_scope.py -v`
Expected: FAIL — `AttributeError: module 'pipeline.lib.assemble_run' has no attribute 'pipelinerun_filename'`

- [ ] **Step 3: Add the helpers to `pipeline/lib/assemble_run.py`**

Add near the other module-level helpers, above `generate_pipelineruns`:

```python
def pipelinerun_filename(workload: str, package: str, iteration: int) -> str:
    """Filename of the PipelineRun YAML for one ``(workload, package, iteration)``.

    Single source of truth for the ``_`` -> ``-`` substitution applied to the
    workload segment: the ``|`` separators make the derived pair key match the
    canonical grammar in ``pipeline/lib/pairkey.py``, which does not admit
    underscores. Note the substitution is NOT applied to ``results/`` paths —
    those use the raw workload name, because that is what the cluster-side
    collector writes.
    """
    return f"pipelinerun-{workload.replace('_', '-')}|{package}|i{iteration}.yaml"


def _normalize_scope_name(name: str) -> str:
    """Comparison form for a ``--workload`` / ``--package`` filter value.

    Workload names reach the operator in two spellings — raw (``results/``,
    the workload YAML) and ``_``-substituted (``cluster/pipelinerun-*``). The
    substitution is slated for deprecation; until then, accept either
    spelling rather than making the operator guess which producer they are
    naming.
    """
    return name.replace("_", "-")


def resolve_pair_scope(
    *,
    workload_names: list[str],
    package_names: list[str],
    workload_filter: "list[str] | None",
    package_filter: "list[str] | None",
) -> list[tuple[str, str]]:
    """Return the ``(workload, package)`` pairs in scope for this assemble.

    Scope is the cross product of *workload_names* x *package_names*, narrowed
    by the filters. Both filters accept comma-separated values and shell globs,
    matching ``deploy``'s ``--workload`` / ``--package`` grammar (the parsing is
    literally shared — see ``pipeline/lib/scope.py``).

    Derived from the manifest rather than from ``deploy``'s ``_resolve_scope``,
    which reads the ConfigMap-backed progress store and therefore needs a
    cluster namespace — unavailable and irrelevant at assemble time.

    Raises :class:`AssembleError` naming the valid values when a filter value
    matches nothing.
    """
    selected_wl = _select_scope_names(workload_names, workload_filter, "--workload")
    selected_pkg = _select_scope_names(package_names, package_filter, "--package")
    return [(wl, pkg) for wl in selected_wl for pkg in selected_pkg]


def _select_scope_names(
    valid: list[str], raw_filter: "list[str] | None", flag: str
) -> list[str]:
    """Apply one scope filter to *valid*, preserving *valid*'s order."""
    values = _scope_mod.parse_name_list(raw_filter)
    if values is None:
        return list(valid)
    # Match on the normalized spelling but return the canonical names, so
    # downstream path construction always uses the producer's own spelling.
    by_norm: dict[str, str] = {}
    for name in valid:
        by_norm.setdefault(_normalize_scope_name(name), name)
    expanded, unknown = _scope_mod.expand_glob_values(
        [_normalize_scope_name(v) for v in values], list(by_norm)
    )
    if unknown:
        raise AssembleError(
            f"{flag}: no match for {sorted(set(unknown))}. "
            f"Valid {flag} values: {', '.join(valid)}"
        )
    selected = {by_norm[n] for n in expanded}
    return [n for n in valid if n in selected]
```

Add the import at the top of `assemble_run.py`, alongside the other `pipeline.lib` imports:

```python
from pipeline.lib import scope as _scope_mod
```

- [ ] **Step 4: Use the helper inside `generate_pipelineruns`**

In `generate_pipelineruns`, replace the inline filename construction at `:513`:

```python
                fname = f"pipelinerun-{safe_wl}|{pkg_name}|i{iteration}.yaml"
```

with:

```python
                fname = pipelinerun_filename(wl_name, pkg_name, iteration)
```

Confirm with `grep -n safe_wl pipeline/lib/assemble_run.py` whether the `safe_wl` assignment has any other reader; delete it if not.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest pipeline/tests/test_assemble_scope.py pipeline/tests/test_assemble_run.py pipeline/tests/test_assemble_replicas.py -v`
Expected: `test_assemble_scope.py` all PASS; the two existing suites unchanged in outcome (the filename helper is a pure refactor)

- [ ] **Step 6: Commit**

```bash
git add pipeline/lib/assemble_run.py pipeline/tests/test_assemble_scope.py
git commit -m "feat(assemble): add pair-scope resolution and a PipelineRun filename helper"
```

---

### Task 3: The per-pair decision table

**Files:**
- Modify: `pipeline/lib/assemble_run.py`
- Test: `pipeline/tests/test_assemble_scope.py`

**Interfaces:**
- Consumes: `pipelinerun_filename` (Task 2).
- Produces:
  - `PairPlan` frozen dataclass with fields `workload: str`, `package: str`, `iteration: int`, `pipelinerun_path: Path`, `results_path: Path`, `regenerate: bool`, `wipe_results: bool`.
  - `plan_pairs(*, run_dir: Path, scope: list[tuple[str, str]], iterations: Iterable[int], force: bool, no_wipe: bool) -> list[PairPlan]`

- [ ] **Step 1: Write the failing test**

Append to `pipeline/tests/test_assemble_scope.py`:

```python
def _seed(run_dir, *, pipelinerun=False, results=False,
          workload="wl_a", package="baseline", iteration=1):
    """Create the on-disk predicates for one pair."""
    if pipelinerun:
        pr = run_dir / "cluster" / assemble_run.pipelinerun_filename(
            workload, package, iteration)
        pr.parent.mkdir(parents=True, exist_ok=True)
        pr.write_text("seeded\n")
    if results:
        res = run_dir / "results" / package / workload / f"i{iteration}"
        res.mkdir(parents=True, exist_ok=True)
        (res / "trace_data.csv").write_text("seeded\n")


def _plan_one(run_dir, *, force=False, no_wipe=False):
    plans = assemble_run.plan_pairs(
        run_dir=run_dir,
        scope=[("wl_a", "baseline")],
        iterations=[1],
        force=force,
        no_wipe=no_wipe,
    )
    assert len(plans) == 1
    return plans[0]


class TestPlanPairsDecisionTable:
    def test_row1_no_pipelinerun_no_results_generates(self, tmp_path):
        p = _plan_one(tmp_path)
        assert p.regenerate is True
        assert p.wipe_results is False

    def test_row2_no_pipelinerun_with_results_generates_and_wipes(self, tmp_path):
        _seed(tmp_path, results=True)
        p = _plan_one(tmp_path)
        assert p.regenerate is True
        assert p.wipe_results is True

    def test_row2_no_wipe_preserves_results(self, tmp_path):
        _seed(tmp_path, results=True)
        p = _plan_one(tmp_path, no_wipe=True)
        assert p.regenerate is True
        assert p.wipe_results is False

    def test_row3_pipelinerun_exists_without_force_does_nothing(self, tmp_path):
        _seed(tmp_path, pipelinerun=True)
        p = _plan_one(tmp_path)
        assert p.regenerate is False
        assert p.wipe_results is False

    def test_row3_pipelinerun_exists_with_force_regenerates(self, tmp_path):
        _seed(tmp_path, pipelinerun=True)
        p = _plan_one(tmp_path, force=True)
        assert p.regenerate is True
        assert p.wipe_results is False

    def test_row4_both_exist_without_force_keeps_results(self, tmp_path):
        _seed(tmp_path, pipelinerun=True, results=True)
        p = _plan_one(tmp_path)
        assert p.regenerate is False
        # Not regenerating means not wiping — the wipe axis only applies to
        # pairs actually being redone.
        assert p.wipe_results is False

    def test_row4_both_exist_with_force_regenerates_and_wipes(self, tmp_path):
        _seed(tmp_path, pipelinerun=True, results=True)
        p = _plan_one(tmp_path, force=True)
        assert p.regenerate is True
        assert p.wipe_results is True

    def test_row4_force_with_no_wipe_regenerates_and_keeps(self, tmp_path):
        _seed(tmp_path, pipelinerun=True, results=True)
        p = _plan_one(tmp_path, force=True, no_wipe=True)
        assert p.regenerate is True
        assert p.wipe_results is False

    def test_results_path_uses_raw_workload_name(self, tmp_path):
        p = _plan_one(tmp_path)
        assert p.results_path == tmp_path / "results" / "baseline" / "wl_a" / "i1"

    def test_one_plan_per_triple(self, tmp_path):
        plans = assemble_run.plan_pairs(
            run_dir=tmp_path,
            scope=[("wl_a", "baseline"), ("wl_a", "sr")],
            iterations=[1, 2],
            force=False,
            no_wipe=False,
        )
        assert len(plans) == 4
        assert {(p.package, p.iteration) for p in plans} == {
            ("baseline", 1), ("baseline", 2), ("sr", 1), ("sr", 2),
        }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest pipeline/tests/test_assemble_scope.py -k PlanPairs -v`
Expected: FAIL — `AttributeError: module 'pipeline.lib.assemble_run' has no attribute 'plan_pairs'`

- [ ] **Step 3: Implement `PairPlan` and `plan_pairs`**

Add to `pipeline/lib/assemble_run.py`, after `resolve_pair_scope`:

```python
@dataclass(frozen=True)
class PairPlan:
    """What assemble will do to one ``(workload, package, iteration)`` triple.

    ``regenerate`` and ``wipe_results`` implement the two orthogonal axes from
    issue #876: ``--force`` answers "this PipelineRun already exists — redo
    it?", ``--no-wipe`` answers "results exist for a pair being regenerated —
    keep them?". Because the second question is only asked about pairs that
    are actually being redone, ``wipe_results`` is never True when
    ``regenerate`` is False.
    """

    workload: str
    package: str
    iteration: int
    pipelinerun_path: Path
    results_path: Path
    regenerate: bool
    wipe_results: bool


def plan_pairs(
    *,
    run_dir: Path,
    scope: list[tuple[str, str]],
    iterations: "Iterable[int]",
    force: bool,
    no_wipe: bool,
) -> list[PairPlan]:
    """Decide, per triple in ``scope`` x ``iterations``, what assemble will do.

    Both predicates are plain ``Path.exists()`` checks — no new state, no
    content hashing, no ``run_metadata.json`` schema change:

    * PipelineRun: ``cluster/pipelinerun-<wl>|<pkg>|i<N>.yaml``
    * results:     ``results/<pkg>/<wl>/i<N>/``

    Note the two use different spellings of the workload name (``_``-substituted
    and raw respectively) because that is what their producers write — see
    ``pipelinerun_filename``.
    """
    cluster_dir_ = run_dir / "cluster"
    results_root = run_dir / "results"
    plans: list[PairPlan] = []
    for workload, package in scope:
        for iteration in iterations:
            pr_path = cluster_dir_ / pipelinerun_filename(workload, package, iteration)
            res_path = results_root / package / workload / f"i{iteration}"
            regenerate = force or not pr_path.exists()
            plans.append(
                PairPlan(
                    workload=workload,
                    package=package,
                    iteration=iteration,
                    pipelinerun_path=pr_path,
                    results_path=res_path,
                    regenerate=regenerate,
                    wipe_results=(
                        regenerate and not no_wipe and res_path.exists()
                    ),
                )
            )
    return plans
```

Confirm `dataclass` and `Iterable` are imported at the top of the module (`grep -n 'dataclass\|Iterable' pipeline/lib/assemble_run.py`); add `from dataclasses import dataclass` and/or `from collections.abc import Iterable` if absent.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest pipeline/tests/test_assemble_scope.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pipeline/lib/assemble_run.py pipeline/tests/test_assemble_scope.py
git commit -m "feat(assemble): add per-pair regenerate/wipe decision (PairPlan, plan_pairs)"
```

---

### Task 4: Generate only the planned pairs, and stop `rmtree`-ing the run dir

This is the task that actually fixes the data loss. It is deliberately the largest: the `rmtree` removal and the pair-driven write path cannot land separately without leaving `main` in a state where `--force` neither cleans nor regenerates.

**Files:**
- Modify: `pipeline/lib/assemble_run.py` (`generate_pipelineruns` at `:456-516`; `assemble_run` decision tree at `:927-1010`; write phase at `:1057-1112`)
- Test: `pipeline/tests/test_assemble_scope.py`, `pipeline/tests/test_assemble_run.py:1350`, `pipeline/tests/test_assemble_replicas.py:202,228,283`

**Interfaces:**
- Consumes: `plan_pairs`, `PairPlan`, `resolve_pair_scope` (Tasks 2-3).
- Produces:
  - `generate_pipelineruns(..., triples: "set[tuple[str, str, int]] | None" = None)` — when `triples` is given, only those are emitted.
  - `prune_orphan_cluster_files(run_dir: Path, *, expected_pipelineruns: set[str], package_names: list[str]) -> list[str]` — returns the deleted filenames.
  - `assemble_run(..., workload_filter=None, package_filter=None, no_wipe=False, assume_yes=False, replicas: int | None = None)`.
  - Side-band attrs: `assemble_run.already_assembled: int`, `assemble_run.pruned_files: list[str]`, `assemble_run.wiped_results: list[str]`.

- [ ] **Step 1: Write the failing integration tests**

Append to `pipeline/tests/test_assemble_scope.py`:

```python
import time

import yaml

from pipeline.tests.test_assemble_run import _make_experiment


def _assemble(fx, **kw):
    kw.setdefault("force", False)
    kw.setdefault("now_iso", "2026-07-01T00:00:00Z")
    return assemble_run.assemble_run(
        translation_hash=fx["translation_hash"],
        translation_ref=fx["translation_hash"],
        cluster_id=fx["cluster_id"],
        run_name="trial-1",
        experiment_root=fx["exp_root"],
        manifest_path=fx["manifest_path"],
        **kw,
    )


def _fx(tmp_path):
    return _make_experiment(
        tmp_path, algo_names_registered=["sr"], algo_names_manifest=["sr"]
    )


def _run_dir(fx):
    return fx["exp_root"] / "workspace" / "runs" / "trial-1"


def _seed_results(run_dir, package, workload="wl_a", iteration=1):
    d = run_dir / "results" / package / workload / f"i{iteration}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "trace_data.csv").write_text("measured\n")
    return d


class TestForceNoLongerDestroysResults:
    def test_force_wipes_only_regenerated_pairs_not_the_run_dir(self, tmp_path):
        """--force wipes results only for pairs it regenerates — and nothing else
        in the run dir. This is the core of issue #876: the old code rmtree'd
        the entire run directory."""
        fx = _fx(tmp_path)
        _assemble(fx)
        run_dir = _run_dir(fx)
        baseline_results = _seed_results(run_dir, "baseline")
        (run_dir / "notes.txt").write_text("operator notes")
        _assemble(fx, force=True, assume_yes=True,
                  now_iso="2026-07-02T00:00:00Z")
        # Unrelated run-dir contents survive (old behavior: destroyed).
        assert (run_dir / "notes.txt").read_text() == "operator notes"
        # The regenerated pair's results were wiped (documented --force behavior).
        assert not baseline_results.exists()

    def test_force_with_no_wipe_keeps_results(self, tmp_path):
        fx = _fx(tmp_path)
        _assemble(fx)
        run_dir = _run_dir(fx)
        baseline_results = _seed_results(run_dir, "baseline")
        _assemble(fx, force=True, no_wipe=True,
                  now_iso="2026-07-02T00:00:00Z")
        assert (baseline_results / "trace_data.csv").read_text() == "measured\n"
        # And the PipelineRun was still regenerated.
        assert (run_dir / "cluster"
                / "pipelinerun-wl-a|baseline|i1.yaml").exists()


class TestScopedAssemble:
    def test_scoped_force_leaves_out_of_scope_pipelineruns_untouched(self, tmp_path):
        fx = _fx(tmp_path)
        _assemble(fx)
        run_dir = _run_dir(fx)
        other = run_dir / "cluster" / "pipelinerun-wl-a|sr|i1.yaml"
        before_bytes = other.read_bytes()
        before_mtime = other.stat().st_mtime_ns
        time.sleep(0.01)
        _assemble(fx, force=True, package_filter=["baseline"],
                  now_iso="2026-07-02T00:00:00Z")
        assert other.read_bytes() == before_bytes
        assert other.stat().st_mtime_ns == before_mtime

    def test_scoped_assemble_leaves_params_hash_and_manifest_assembly(self, tmp_path):
        fx = _fx(tmp_path)
        _assemble(fx)
        run_dir = _run_dir(fx)
        ma = run_dir / "manifest.assembly.yaml"
        rm = run_dir / "run_metadata.json"
        ma_mtime, rm_mtime = ma.stat().st_mtime_ns, rm.stat().st_mtime_ns
        time.sleep(0.01)
        _assemble(fx, force=True, package_filter=["baseline"],
                  now_iso="2026-07-02T00:00:00Z")
        assert ma.stat().st_mtime_ns == ma_mtime
        assert rm.stat().st_mtime_ns == rm_mtime

    def test_scoped_assemble_rewrites_cluster_package_yaml(self, tmp_path):
        fx = _fx(tmp_path)
        _assemble(fx)
        run_dir = _run_dir(fx)
        pkg_yaml = run_dir / "cluster" / "baseline.yaml"
        pkg_yaml.write_text("stale: true\n")
        _assemble(fx, force=True, package_filter=["baseline"],
                  now_iso="2026-07-02T00:00:00Z")
        assert "stale" not in pkg_yaml.read_text()

    def test_scoped_assemble_on_missing_run_refuses(self, tmp_path):
        fx = _fx(tmp_path)
        with pytest.raises(AssembleError, match="existing run"):
            _assemble(fx, workload_filter=["wl_a"])

    def test_scoped_assemble_rejects_replicas(self, tmp_path):
        fx = _fx(tmp_path)
        _assemble(fx)
        with pytest.raises(AssembleError, match="--replicas"):
            _assemble(fx, workload_filter=["wl_a"], replicas=2)

    def test_scoped_assemble_uses_recorded_replica_count(self, tmp_path):
        fx = _fx(tmp_path)
        _assemble(fx, replicas=3)
        run_dir = _run_dir(fx)
        for i in (1, 2, 3):
            (run_dir / "cluster" / f"pipelinerun-wl-a|baseline|i{i}.yaml").unlink()
        _assemble(fx, package_filter=["baseline"],
                  now_iso="2026-07-02T00:00:00Z")
        for i in (1, 2, 3):
            assert (run_dir / "cluster"
                    / f"pipelinerun-wl-a|baseline|i{i}.yaml").exists()

    def test_scoped_assemble_refuses_on_manifest_drift(self, tmp_path):
        fx = _fx(tmp_path)
        _assemble(fx)
        import json
        rm_path = _run_dir(fx) / "run_metadata.json"
        rm = json.loads(rm_path.read_text())
        rm["params_hash"] = "0" * 64
        rm_path.write_text(json.dumps(rm))
        with pytest.raises(AssembleError, match="unscoped"):
            _assemble(fx, force=True, package_filter=["baseline"])


class TestAlreadyAssembledMessage:
    def test_reassemble_reports_pair_count_and_writes_nothing(self, tmp_path):
        fx = _fx(tmp_path)
        _assemble(fx)
        run_dir = _run_dir(fx)
        files = [p for p in run_dir.rglob("*") if p.is_file()]
        before = {p: p.stat().st_mtime_ns for p in files}
        time.sleep(0.01)
        _assemble(fx, now_iso="2026-07-02T00:00:00Z")
        assert {p: p.stat().st_mtime_ns for p in files} == before
        assert assemble_run.assemble_run.status == "noop"
        # 1 workload x 2 packages x 1 iteration
        assert assemble_run.assemble_run.already_assembled == 2


class TestOrphanPruning:
    def test_unscoped_force_prunes_pipelineruns_outside_cross_product(self, tmp_path):
        fx = _fx(tmp_path)
        _assemble(fx)
        run_dir = _run_dir(fx)
        orphan = run_dir / "cluster" / "pipelinerun-wl-gone|baseline|i1.yaml"
        orphan.write_text("stale\n")
        _assemble(fx, force=True, assume_yes=True,
                  now_iso="2026-07-02T00:00:00Z")
        assert not orphan.exists()
        assert "pipelinerun-wl-gone|baseline|i1.yaml" in (
            assemble_run.assemble_run.pruned_files
        )

    def test_scoped_assemble_prunes_nothing(self, tmp_path):
        fx = _fx(tmp_path)
        _assemble(fx)
        run_dir = _run_dir(fx)
        orphan = run_dir / "cluster" / "pipelinerun-wl-gone|baseline|i1.yaml"
        orphan.write_text("stale\n")
        _assemble(fx, force=True, package_filter=["baseline"],
                  now_iso="2026-07-02T00:00:00Z")
        assert orphan.exists()
        assert assemble_run.assemble_run.pruned_files == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest pipeline/tests/test_assemble_scope.py -k "Force or Scoped or Already or Orphan" -v`
Expected: FAIL — `assemble_run() got an unexpected keyword argument 'workload_filter'`

- [ ] **Step 3: Teach `generate_pipelineruns` to emit a subset**

Add the parameter, keeping the existing loop order so unscoped output is byte-identical:

```python
    iterations: "range | list[int]" = range(1, 2),
    triples: "set[tuple[str, str, int]] | None" = None,
) -> None:
```

Extend the docstring with:

```
    When *triples* is given, only ``(workload_name, package, iteration)``
    members of that set are emitted; every other combination is skipped and
    its existing file on disk is left untouched (byte- and mtime-identical).
    When it is ``None`` the full cross product of *packages* x *workloads* x
    *iterations* is emitted, which is the unscoped behavior.
```

Inside the innermost loop, immediately after `for iteration in iterations:`:

```python
                if triples is not None and (
                    wl_name, pkg_name, iteration
                ) not in triples:
                    continue
```

- [ ] **Step 4: Add `prune_orphan_cluster_files`**

```python
def prune_orphan_cluster_files(
    run_dir: Path,
    *,
    expected_pipelineruns: set[str],
    package_names: list[str],
) -> list[str]:
    """Delete ``cluster/`` files that the current manifest no longer describes.

    Necessary because ``--force`` no longer ``rmtree``s the run directory
    (issue #876): without pruning, a manifest edit that drops a workload or an
    algorithm would leave its PipelineRun behind, and ``deploy`` discovers
    pairs by globbing ``cluster/pipelinerun-*.yaml`` — so the dropped pair
    would still be executed.

    Only ever called for an unscoped assemble: a scoped invocation has no
    business judging pairs outside its scope. Collected ``results/`` for a
    pruned pair are deliberately left alone — they are measured data, and
    the point of this issue is to stop assemble from destroying it.

    Returns the deleted filenames, sorted.
    """
    cluster_dir_ = run_dir / "cluster"
    if not cluster_dir_.is_dir():
        return []
    keep_scenarios = set(package_names)
    removed: list[str] = []
    for path in sorted(cluster_dir_.glob("*.yaml")):
        if path.name.startswith("pipelinerun-"):
            if path.name not in expected_pipelineruns:
                path.unlink()
                removed.append(path.name)
        elif path.stem not in keep_scenarios:
            path.unlink()
            removed.append(path.name)
    return removed
```

- [ ] **Step 5: Rewire the `assemble_run` decision tree — remove every `rmtree`**

Change the signature:

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
    replicas: "int | None" = None,
    workload_filter: "list[str] | None" = None,
    package_filter: "list[str] | None" = None,
    no_wipe: bool = False,
    assume_yes: bool = False,
    now_iso: str,
) -> None:
```

Add to the docstring, replacing the "Steps" list's step 1 and appending a paragraph:

```
      1. Validate: translation dir + cluster_config exist; existing run dirs
         are inspected, never deleted.
...
    ``--force`` no longer removes the run directory (issue #876). It is now a
    per-pair predicate — "this PipelineRun already exists, redo it" — and the
    separate ``no_wipe`` axis decides whether a redone pair's collected
    ``results/`` survive. Nothing outside the pairs in scope is ever deleted,
    except ``cluster/`` files that the current manifest no longer describes
    (unscoped only — see ``prune_orphan_cluster_files``).

    Passing ``workload_filter`` or ``package_filter`` makes the invocation
    *scoped*. A scoped assemble requires an existing run, rejects an explicit
    ``replicas`` (it uses the run's recorded count), refuses on manifest drift
    regardless of ``force``, and writes neither ``manifest.assembly.yaml`` nor
    ``run_metadata.json`` — so ``params_hash`` keeps meaning "the last state in
    which the whole run was consistent" and a later unscoped assemble still
    detects the drift.
```

Reset the new side-band attrs alongside the existing three:

```python
    assemble_run.already_assembled = 0  # type: ignore[attr-defined]
    assemble_run.pruned_files = []  # type: ignore[attr-defined]
    assemble_run.wiped_results = []  # type: ignore[attr-defined]
```

...and add matching module-level initializers next to the existing block at the end of the file.

Replace the whole existing-run decision tree (`:927-1010`, from `if run_dir.exists():` through the `additive_grow_from = prior_replicas` else-branch) with:

```python
    scoped = workload_filter is not None or package_filter is not None
    run_dir = layout.runs_dir() / run_name
    additive_grow_from: int | None = None
    prior_replicas: int | None = None

    if not run_dir.exists():
        if scoped:
            raise AssembleError(
                "--workload/--package require an existing run: there is no "
                f"runs/{run_name}/ to re-assemble a subset of. Assemble the "
                "full run once first."
            )
        replicas_effective = 1 if replicas is None else replicas
    else:
        prior_ma_path = run_dir / "manifest.assembly.yaml"
        prior_rm_path = run_dir / "run_metadata.json"
        prior_ma: dict = {}
        prior_rm: dict = {}
        repairing = False
        if not (prior_ma_path.exists() and prior_rm_path.exists()):
            if scoped:
                raise AssembleError(
                    f"run '{run_name}' is missing manifest.assembly.yaml or "
                    "run_metadata.json — repair it with an unscoped "
                    "`sim2real assemble --force` before scoping."
                )
            if not force:
                raise AssembleError(
                    f"run directory '{run_name}' is missing manifest.assembly.yaml or "
                    f"run_metadata.json — pass --force to rebuild"
                )
            repairing = True
        else:
            try:
                prior_ma = yaml.safe_load(prior_ma_path.read_text()) or {}
                prior_rm = json.loads(prior_rm_path.read_text())
            except (yaml.YAMLError, json.JSONDecodeError, OSError) as exc:
                if scoped:
                    raise AssembleError(
                        f"run '{run_name}' has a corrupt manifest.assembly.yaml "
                        f"or run_metadata.json: {exc} — repair it with an "
                        "unscoped `sim2real assemble --force` before scoping."
                    ) from exc
                if not force:
                    raise AssembleError(
                        f"run '{run_name}' has a corrupt manifest.assembly.yaml "
                        f"or run_metadata.json: {exc} — pass --force to rebuild"
                    ) from exc
                repairing = True

        if not repairing:
            prior_replicas = (
                prior_ma.get("replicas") if isinstance(prior_ma, dict) else None
            )
        if repairing or prior_replicas is None:
            if not repairing:
                # Legacy single-replica shape (pre-step-5).
                if scoped:
                    raise AssembleError(
                        f"run '{run_name}' is in legacy single-replica shape "
                        "(pre-step-5) — repair it with an unscoped "
                        "`sim2real assemble --force` before scoping."
                    )
                if not force:
                    raise AssembleError(
                        f"run '{run_name}' is in legacy single-replica shape "
                        "(pre-step-5); create a fresh run to use --replicas, "
                        "or pass --force to rebuild."
                    )
            # Repair path: --force is set, run-wide files get rewritten below
            # and every pair in scope is regenerated.
            replicas_effective = 1 if replicas is None else replicas
        else:
            if scoped and replicas is not None:
                raise AssembleError(
                    "--replicas cannot be combined with --workload/--package: "
                    "a scoped assemble reuses the run's recorded replica count "
                    f"({prior_replicas}). Grow the run with an unscoped "
                    "`sim2real assemble --replicas` first."
                )
            replicas_effective = prior_replicas if scoped else (
                1 if replicas is None else replicas
            )
            # Grow-only guard runs BEFORE the drift check because --force
            # bypasses drift but does NOT bypass grow-only.
            if replicas_effective < prior_replicas:
                raise AssembleError(
                    f"run '{run_name}' already has {prior_replicas} "
                    f"replicas; refusing to shrink to {replicas_effective}. "
                    "Replica shrink is tracked in #506."
                )
            new_slice = slicer.assembly_slice(manifest)
            new_canonical = yaml.dump(
                new_slice, sort_keys=True, default_flow_style=False,
                allow_unicode=True,
            ).encode("utf-8")
            new_content_hash = hashlib.sha256(new_canonical).hexdigest()
            prior_params_hash = prior_rm.get("params_hash", "")
            if new_content_hash != prior_params_hash:
                if scoped:
                    # A scoped assemble does not rewrite manifest.assembly.yaml
                    # or params_hash, so it must not generate PipelineRuns from
                    # a manifest the snapshot no longer describes.
                    raise AssembleError(
                        f"transfer.yaml changed since run '{run_name}' was "
                        "assembled; a scoped assemble cannot record that. "
                        "Re-assemble unscoped (`sim2real assemble --force`) "
                        "first, then scope."
                    )
                if not force:
                    raise AssembleError(
                        f"manifest content changed since last assemble for "
                        f"run '{run_name}'; pass --force to overwrite."
                    )
            elif replicas_effective > prior_replicas and not force:
                # replicas > prior: additive grow (scoped is impossible here —
                # scoped forces replicas_effective == prior_replicas).
                additive_grow_from = prior_replicas
        assemble_run.prior_assembled_at = (  # type: ignore[attr-defined]
            str(prior_rm.get("assembled_at") or "")
        )
```

Note what is gone: all six `shutil.rmtree(run_dir)` calls, and the `status = "noop"` early return (the empty-regeneration-set case now produces it, in step 6). Check whether `shutil` still has a reader in the module (`grep -n 'shutil' pipeline/lib/assemble_run.py`) and drop the import if not.

- [ ] **Step 6: Drive the write phase from the pair plan**

Replace the write phase (`:1057-1112`, from `run_dir.mkdir(...)` onward) with:

```python
    # 5. Scope + per-pair plan ------------------------------------------
    package_names = [name for name, _ in packages]
    workload_names = [
        wl.get("name", wl.get("workload_name", "unknown"))
        for wl in resolved.workloads
    ]
    pair_scope = resolve_pair_scope(
        workload_names=workload_names,
        package_names=package_names,
        workload_filter=workload_filter,
        package_filter=package_filter,
    )
    iterations = list(range(1, replicas_effective + 1))
    plans = plan_pairs(
        run_dir=run_dir,
        scope=pair_scope,
        iterations=iterations,
        force=force,
        no_wipe=no_wipe,
    )
    regen = [p for p in plans if p.regenerate]
    if not regen:
        assemble_run.status = "noop"  # type: ignore[attr-defined]
        assemble_run.already_assembled = len(plans)  # type: ignore[attr-defined]
        return

    # 6. Confirm before destroying measured data --------------------------
    # Only when --force was NOT what authorized the regeneration: with
    # --force the wipe is the long-documented behavior and stays
    # non-interactive so scripted use keeps working (--no-wipe is the
    # opt-out). Without it, results would die on a pair the operator never
    # typed a flag for. The prompt runs before any write, so declining
    # leaves the run untouched.
    to_wipe = [p for p in regen if p.wipe_results]
    if to_wipe and not force and not assume_yes:
        if not _confirm_results_wipe(
            [
                f"results/{p.package}/{p.workload}/i{p.iteration}/"
                for p in to_wipe
            ]
        ):
            raise AssembleError(
                "aborted — pass --no-wipe to regenerate while keeping "
                "collected results, or --yes to confirm the wipe"
            )
    for p in to_wipe:
        shutil.rmtree(p.results_path)
    assemble_run.wiped_results = [  # type: ignore[attr-defined]
        f"results/{p.package}/{p.workload}/i{p.iteration}/" for p in to_wipe
    ]

    # 7. Snapshot assembly slice + params_hash ---------------------------
    # Skipped entirely when scoped: params_hash must keep meaning "the last
    # state in which the WHOLE run was consistent", so a later unscoped
    # assemble still sees manifest drift instead of reporting nothing to do.
    run_dir.mkdir(parents=True, exist_ok=True)
    params_hash = ""
    if not scoped:
        manifest_assembly_path = write_manifest_assembly(
            run_dir, manifest, now_iso=now_iso, replicas=replicas_effective
        )
        params_hash = compute_params_hash(manifest_assembly_path)

    # 8. Write scenario YAMLs for the packages in scope -------------------
    scoped_packages = [
        (name, res) for name, res in packages
        if name in {pkg for _, pkg in pair_scope}
    ]
    write_resolved_scenarios(run_dir, scoped_packages)

    # 9. Generate PipelineRuns for the planned pairs only -----------------
    generate_pipelineruns(
        run_dir=run_dir,
        packages=packages,
        workloads=resolved.workloads,
        run_name=run_name,
        cluster_config=cluster_config,
        pipeline_name=(manifest.get("pipeline") or {}).get("name", "sim2real"),
        observe=manifest.get("blis_observe") or {},
        model_name=resolved.model_name,
        submodule_shas=resolved.submodule_shas,
        submodule_urls=resolved.submodule_urls,
        iterations=iterations,
        triples={(p.workload, p.package, p.iteration) for p in regen},
    )

    # 10. Prune cluster/ files the manifest no longer describes -----------
    if not scoped:
        expected = {
            pipelinerun_filename(wl, pkg, i)
            for wl in workload_names
            for pkg in package_names
            for i in iterations
        }
        assemble_run.pruned_files = prune_orphan_cluster_files(  # type: ignore[attr-defined]
            run_dir,
            expected_pipelineruns=expected,
            package_names=package_names,
        )
```

Then guard the `run_metadata.json` write with `if not scoped:` and use `replicas_effective`:

```python
    if not scoped:
        run_meta_image_tag = (
            translated_algos[kept_algos[0]["name"]]["image_ref"]
            if kept_algos else ""
        )
        write_run_metadata(
            run_dir,
            {
                "version": 1,
                "run_name": run_name,
                "translation_hash": translation_hash,
                "cluster_id": cluster_id,
                "params_hash": params_hash,
                "image_tag": run_meta_image_tag,
                "replicas": replicas_effective,
                "assembled_at": now_iso,
                "scenario": manifest.get("scenario", "") or "",
            },
        )
```

Add the confirmation helper near the top of the module's helper section:

```python
def _confirm_results_wipe(displays: list[str]) -> bool:
    """Prompt before deleting collected results. Mirrors ``deploy wipe``'s
    pattern (enumerate targets, ``[y/N]``, EOF aborts) so the two commands
    feel the same. Tests monkeypatch this function.
    """
    print(
        "The following collected results will be deleted so their pairs can "
        "be re-assembled:"
    )
    for d in displays:
        print(f"    {d}")
    try:
        answer = input(f"Wipe {len(displays)} result director(ies)? [y/N] ")
    except EOFError:
        return False
    return answer.strip().lower() == "y"
```

Finally, in the additive-grow early-return block (`:1012-1035`), pass `prior_replicas=additive_grow_from, new_replicas=replicas_effective` and leave the rest as-is.

- [ ] **Step 7: Run the new tests**

Run: `python -m pytest pipeline/tests/test_assemble_scope.py -v`
Expected: PASS

- [ ] **Step 8: Rewrite the tests that encode the bug**

`pipeline/tests/test_assemble_run.py:1350` — replace `test_force_overwrites_existing_run` with:

```python
    def test_force_rebuilds_without_destroying_unrelated_run_dir_contents(
        self, tmp_path
    ):
        """Issue #876: --force regenerates PipelineRuns but no longer rmtrees
        the run directory. The previous version of this test asserted the
        sentinel was destroyed, which encoded the data-loss bug — collected
        results/ live in the same directory."""
        fx = _make_experiment(
            tmp_path,
            algo_names_registered=["sr"],
            algo_names_manifest=["sr"],
        )
        run_dir = fx["exp_root"] / "workspace" / "runs" / "trial-1"
        run_dir.mkdir(parents=True)
        (run_dir / "sentinel").write_text("leftover")
        assemble_run.assemble_run(
            translation_hash=fx["translation_hash"],
            translation_ref=fx["translation_hash"],
            cluster_id=fx["cluster_id"],
            run_name="trial-1",
            experiment_root=fx["exp_root"],
            manifest_path=fx["manifest_path"],
            force=True,
            now_iso="2026-07-01T14:05:00Z",
        )
        assert (run_dir / "sentinel").read_text() == "leftover"
        assert (run_dir / "manifest.assembly.yaml").exists()
```

`pipeline/tests/test_assemble_replicas.py` — three edits:

1. `test_reassemble_at_same_replica_count_with_force_rebuilds` (`:202`): replace the `assert not (run_dir / "sentinel").exists()` with an mtime-based rebuild proof, since bytes are identical when inputs match:

```python
    def test_reassemble_at_same_replica_count_with_force_rebuilds(self, tmp_path):
        """Issue #532: --force must rebuild even when the manifest hash and
        --replicas match the prior assemble. Issue #876: it must do so WITHOUT
        removing unrelated run-dir contents (results/ lives there)."""
        fx = _make_experiment(tmp_path, algo_names_registered=["sr"],
                              algo_names_manifest=["sr"])
        _assemble(fx, replicas=3, now_iso="2026-07-01T00:00:00Z")
        run_dir = _run_dir_of(fx)
        (run_dir / "sentinel").write_text("leftover")
        pr = run_dir / "cluster" / "pipelinerun-wl-a|baseline|i3.yaml"
        before = pr.stat().st_mtime_ns
        time.sleep(0.01)
        _assemble(fx, replicas=3, force=True,
                  now_iso="2026-07-02T00:00:00Z")
        # PipelineRuns were rewritten...
        assert pr.stat().st_mtime_ns != before
        # ...and unrelated run-dir contents survived (issue #876).
        assert (run_dir / "sentinel").read_text() == "leftover"
        names = _pipelinerun_files(_cluster_dir_of(fx))
        assert "pipelinerun-wl-a|baseline|i3.yaml" in names
        assert "pipelinerun-wl-a|sr|i3.yaml" in names
        ma = yaml.safe_load((run_dir / "manifest.assembly.yaml").read_text())
        assert ma["replicas"] == 3
```

2. `test_grow_with_force_rebuilds_instead_of_additive_grow` (`:228`): flip the sentinel assertion and prove the full rebuild via an i1 mtime change instead:

```python
        run_dir = _run_dir_of(fx)
        (run_dir / "sentinel").write_text("leftover")
        # A full rebuild rewrites the pre-existing iterations; additive-grow
        # would leave them byte- and mtime-identical.
        i1 = run_dir / "cluster" / "pipelinerun-wl-a|baseline|i1.yaml"
        before_i1 = i1.stat().st_mtime_ns
        time.sleep(0.01)
        _assemble(fx, replicas=5, force=True,
                  now_iso="2026-07-02T00:00:00Z")
        assert i1.stat().st_mtime_ns != before_i1
        assert (run_dir / "sentinel").read_text() == "leftover"
```

3. Rename `test_drift_with_force_rmtree_rebuilds` (`:283`) to `test_drift_with_force_rebuilds` — its body (params_hash refreshed, replicas updated) stays valid; only the name referenced the removed `rmtree`.

- [ ] **Step 9: Run the full suite**

Run: `python -m pytest pipeline/ -v`
Expected: PASS. Any other failure is a real consumer this task broke — trace it rather than editing the assertion.

Run: `ruff check pipeline/ --select F`
Expected: no output

- [ ] **Step 10: Commit**

```bash
git add pipeline/lib/assemble_run.py pipeline/tests/
git commit -m "fix(assemble): stop --force from destroying collected results"
```

---

### Task 5: CLI flags, the replaced message, and operator warnings

**Files:**
- Modify: `pipeline/sim2real.py` (`assemble` subparser at `:1167-1198`; `_cmd_assemble` at `:2539-2590`)
- Test: `pipeline/tests/test_sim2real.py` (rewrite `test_noop_reassemble_prints_no_change_message` at `:1971`; add scope tests)

**Interfaces:**
- Consumes: `assemble_run(..., workload_filter, package_filter, no_wipe, assume_yes, replicas)` and the side-band attrs `already_assembled`, `pruned_files`, `wiped_results` (Task 4).
- Produces: `sim2real assemble --workload NAME... --package NAME... [--no-wipe] [--yes]`.

- [ ] **Step 1: Write the failing tests**

In `pipeline/tests/test_sim2real.py`, replace `test_noop_reassemble_prints_no_change_message` with:

```python
    def test_reassemble_reports_already_assembled_pairs(self, tmp_path, capsys):
        """Issue #876: the old message claimed 'inputs and translation are
        unchanged', which params_hash cannot establish — it covers only
        transfer.yaml's own fields, not the overlays or workload YAMLs that
        resolution reads. The replacement states only what is checked."""
        thash = self._make_minimal_registration(tmp_path)
        cluster_id = self._bootstrap_experiment(tmp_path)
        base = [
            "--experiment-root", str(tmp_path),
            "assemble", "--translation", thash,
            "--cluster", cluster_id, "--run", "trial-1",
        ]
        assert sim2real.main(base) == 0
        capsys.readouterr()  # drain
        assert sim2real.main(base) == 0
        out = capsys.readouterr().out
        assert "already assembled" in out
        assert "--force" in out
        assert "unchanged" not in out
        assert "assembled run trial-1" not in out
```

And add, in the same class:

```python
    def test_unknown_workload_filter_exits_non_zero_with_valid_values(
        self, tmp_path, capsys
    ):
        thash = self._make_minimal_registration(tmp_path)
        cluster_id = self._bootstrap_experiment(tmp_path)
        base = [
            "--experiment-root", str(tmp_path),
            "assemble", "--translation", thash,
            "--cluster", cluster_id, "--run", "trial-1",
        ]
        assert sim2real.main(base) == 0
        capsys.readouterr()
        rc = sim2real.main(base + ["--workload", "nope"])
        assert rc == 2
        errout = capsys.readouterr().err
        assert "--workload" in errout
        assert "nope" in errout

    def test_no_wipe_flag_reaches_the_library(self, tmp_path, monkeypatch):
        thash = self._make_minimal_registration(tmp_path)
        cluster_id = self._bootstrap_experiment(tmp_path)
        seen = {}

        def _fake(**kwargs):
            seen.update(kwargs)

        monkeypatch.setattr(sim2real._assemble_run_lib, "assemble_run", _fake)
        rc = sim2real.main([
            "--experiment-root", str(tmp_path),
            "assemble", "--translation", thash,
            "--cluster", cluster_id, "--run", "trial-1",
            "--workload", "w1", "--package", "baseline,sr",
            "--no-wipe", "--yes",
        ])
        assert rc == 0
        assert seen["workload_filter"] == ["w1"]
        assert seen["package_filter"] == ["baseline,sr"]
        assert seen["no_wipe"] is True
        assert seen["assume_yes"] is True
        assert seen["replicas"] is None
```

Note on the last test: `monkeypatch.setattr` replaces the function object, so the `getattr(..., "status", ...)` reads that follow in `_cmd_assemble` fall back to their defaults — which is why it asserts on the captured kwargs rather than on stdout.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest pipeline/tests/test_sim2real.py -k "already_assembled or unknown_workload or no_wipe_flag" -v`
Expected: FAIL — `unrecognized arguments: --workload`

- [ ] **Step 3: Add the flags**

In the `assemble` subparser, change `--force`'s help text and `--replicas`' default, and add the four new flags:

```python
    asm.add_argument(
        "--force",
        action="store_true",
        help="re-generate PipelineRuns for pairs in scope that already have "
             "one (wipes their collected results unless --no-wipe)",
    )
    asm.add_argument(
        "--replicas",
        type=_positive_int,
        default=None,
        metavar="N",
        help="number of replica iterations per (workload, package) pair "
             "(default: 1; cannot be combined with --workload/--package)",
    )
    asm.add_argument(
        "--workload",
        nargs="+",
        metavar="NAME",
        help="scope to these workloads (comma or space-separated, globs OK); "
             "requires an existing run",
    )
    asm.add_argument(
        "--package",
        nargs="+",
        metavar="NAME",
        help="scope to these packages (comma or space-separated, globs OK); "
             "requires an existing run",
    )
    asm.add_argument(
        "--no-wipe",
        action="store_true",
        dest="no_wipe",
        help="keep collected results for pairs being re-generated",
    )
    asm.add_argument(
        "-y", "--yes",
        action="store_true",
        dest="assume_yes",
        help="skip the confirmation prompt before deleting collected results",
    )
```

- [ ] **Step 4: Pass them through and replace the message**

In `_cmd_assemble`, extend the `assemble_run(...)` call:

```python
            force=args.force,
            replicas=args.replicas,
            workload_filter=args.workload,
            package_filter=args.package,
            no_wipe=args.no_wipe,
            assume_yes=args.assume_yes,
            now_iso=now_iso,
```

Replace the `status == "noop"` block:

```python
    for display in getattr(_assemble_run_lib.assemble_run, "wiped_results", []):
        print(f"wiped collected results: {display}", file=sys.stderr)
    for name in getattr(_assemble_run_lib.assemble_run, "pruned_files", []):
        print(
            f"warning: removed cluster/{name} — the current transfer.yaml no "
            "longer describes that pair; its collected results (if any) were "
            "left in place",
            file=sys.stderr,
        )
    status = getattr(_assemble_run_lib.assemble_run, "status", "written")
    if status == "noop":
        n = getattr(_assemble_run_lib.assemble_run, "already_assembled", 0)
        print(
            f"run '{args.run}': {n} pair(s) already assembled, nothing to do. "
            "Pass --force to re-assemble them (add --no-wipe to keep their "
            "collected results), or scope with --workload/--package."
        )
    else:
        print(f"assembled run {args.run}")
    return 0
```

Then check whether `prior_assembled_at` still has any reader (`grep -rn 'prior_assembled_at' pipeline/`). It is still set by the library and asserted by `test_assemble_replicas.py:129`, so leave both in place — it is a true statement about the run, just no longer part of a message that over-claims.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest pipeline/tests/test_sim2real.py -v`
Expected: PASS

Run: `python -m pytest pipeline/ -v && ruff check pipeline/ .claude/skills/ --select F`
Expected: PASS, no lint output

- [ ] **Step 6: Commit**

```bash
git add pipeline/sim2real.py pipeline/tests/test_sim2real.py
git commit -m "feat(assemble): add --workload/--package/--no-wipe/--yes, replace no-op message"
```

---

### Task 6: Documentation and stale-reference sweep

**Files:**
- Modify: `pipeline/README.md`, `CLAUDE.md`
- Test: none (docs), but the sweep is verified by grep

- [ ] **Step 1: Sweep for references to the changed behavior**

```bash
grep -rn "rmtree" pipeline/README.md CLAUDE.md docs/ .claude/skills/ 2>/dev/null
grep -rn "assemble" pipeline/README.md | grep -in "force\|replicas\|noop\|no change\|up to date"
grep -rn "_parse_list\|_expand_glob_values" pipeline/ .claude/skills/
grep -rn "params_hash" pipeline/README.md CLAUDE.md .claude/skills/
```

Classify each hit as stale / accurate / unrelated, and fix the stale ones.

- [ ] **Step 2: Update `pipeline/README.md`**

In the `sim2real assemble` section: document `--workload`, `--package`, `--no-wipe`, `-y/--yes`; restate `--force` as per-pair regeneration rather than run-dir replacement; add the four-case table verbatim from the Behavior specification above; state the scoped-mode rules (existing run required, `--replicas` rejected, drift refused, `manifest.assembly.yaml` / `params_hash` untouched, `cluster/<pkg>.yaml` rewritten); note that orphan `cluster/` files are pruned on an unscoped assemble. Add `pipeline/lib/scope.py` to the library module table.

- [ ] **Step 3: Update `CLAUDE.md`**

In the `sim2real assemble` paragraph, replace "Re-assembling an existing run is grow-only" with the new semantics, and add `scope.py` to the Pipeline Library table:

```markdown
| `scope.py` | CLI filter-value primitives (`parse_name_list`, `expand_glob_values`) shared by `deploy.py` and `sim2real assemble` so their `--workload`/`--package` grammar cannot drift |
```

- [ ] **Step 4: Verify and commit**

Run: `python -m pytest pipeline/ .claude/skills/sim2real-analyze/tests/ .claude/skills/sim2real-bootstrap/tests/ .claude/skills/sim2real-translate/tests/ .claude/skills/sim2real-check/tests/ --cov=pipeline --cov-report=term-missing --cov-fail-under=90 -v`
Expected: PASS with coverage >= 90%

```bash
git add pipeline/README.md CLAUDE.md docs/superpowers/plans/2026-09-01-scoped-assemble.md
git commit -m "docs: document scoped assemble and the new --force semantics"
```

---

## Self-Review

**Spec coverage** — every item in the issue maps to a task:

| Issue requirement | Task |
|---|---|
| `--workload` / `--package` on assemble, deploy-matching names | 1, 2, 5 |
| valid values printed on filter miss, non-zero exit | 2 (`AssembleError`), 5 (CLI test) |
| scope derived from manifest, not `_resolve_scope` | 2 (`resolve_pair_scope` takes names, not a cluster) |
| `--force` keeps meaning; `--no-wipe` added | 3, 5 |
| four-case table evaluated per pair | 3 |
| both predicates `Path.exists()`, no new state / no schema change | 3 |
| row 2 prompt reusing `deploy wipe`'s pattern, `--yes`, EOF aborts | 4 (`_confirm_results_wipe`), 5 (`--yes`) |
| replace the no-op message | 4 (empty-regen path), 5 (wording) |
| test: results survive a scoped re-assemble with `--force` | 4 (`TestForceNoLongerDestroysResults`) |
| test: `--no-wipe` preserves results while regenerating | 3, 4 |
| test: out-of-scope PipelineRuns byte- and mtime-identical | 4 (`test_scoped_force_leaves_out_of_scope_pipelineruns_untouched`) |
| test: filter mismatch prints valid values, exits non-zero | 2, 5 |
| test: `test_force_overwrites_existing_run:1350` rewritten | 4 Step 8 |

**Beyond the issue** (call these out in the PR body so the reviewer knows they were deliberate): `pipeline/lib/scope.py` exists so assemble's filter grammar is literally deploy's rather than a lookalike; `prune_orphan_cluster_files` exists because removing `rmtree` would otherwise let a dropped workload's PipelineRun survive a manifest edit and still be executed by `deploy`, which globs `cluster/pipelinerun-*.yaml`.

**Out of scope** (per the issue): per-workload config as a transfer.yaml axis; deploy-time staleness detection for un-assembled overlay edits; `--iteration` / `--only` on assemble; deprecating the workload `_` -> `-` substitution and `deploy --only`; #877's replica-grow overlay divergence.

**Type consistency** — `pipelinerun_filename(workload, package, iteration)` is used identically in `generate_pipelineruns`, `plan_pairs`, and the prune expectation set. `PairPlan` field names are used verbatim in Tasks 3, 4, and 5. `resolve_pair_scope` returns `list[tuple[str, str]]` in workload-major order in both its definition and every consumer. `replicas_effective` is the single resolved integer used by `plan_pairs`, `write_manifest_assembly`, `write_run_metadata`, and `_additive_grow`.

**Placeholder scan** — no TBDs; every code step carries the actual code, and every test step names the exact command and expected outcome.
