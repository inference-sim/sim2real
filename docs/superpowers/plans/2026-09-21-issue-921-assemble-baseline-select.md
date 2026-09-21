# `sim2real assemble --baseline` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `sim2real assemble` resolves exactly one baseline package — selected by `--baseline <name>`, defaulting to the entry named `baseline` — and reuses whatever generated baseline overlay exists rather than looking one up by baseline name.

**Architecture:** Three seams change. (1) `_resolve_packages` stops looping over every entry in `baselines[]` and instead selects one, reuses the single overlay that exists under `generated/baselines/`, and rebases every algorithm arm onto the selection regardless of its `defaults`. (2) The selected baseline name is written into `manifest.assembly.yaml` and counted in `params_hash`, so two runs differing only in `--baseline` are not mistaken for the same parameters; the drift check and `_additive_grow` are threaded to stay consistent with that. (3) `pipeline/lib/resolve.py` reads the recorded selection instead of hardcoding the string `"baseline"`, so `sim2real resolve --run` keeps reporting a `baseline_yaml` and correct `phases_declared` when the selection is not named `baseline`.

**Tech Stack:** Python 3.12+ (worktree venv built from `python3.13` at `.venv/`), PyYAML, pytest.

**Spec:** GitHub issue #921 plus the resolved-decisions comment at https://github.com/inference-sim/sim2real/issues/921#issuecomment-5766120305

## Global Constraints

- Python floor is `>=3.12`, enforced by `pipeline/__init__.py` and the repo-root `conftest.py`. The repo-root `.venv` is 3.11.4 and must not be used; this worktree has its own `.venv` built from `python3.13`. Run everything as `.venv/bin/python`.
- Tests: `.venv/bin/python -m pytest pipeline/ -v`
- Lint (CI gate): `.venv/bin/ruff check pipeline/ .claude/skills/ --select F`
- Coverage gate in CI: `--cov=pipeline --cov-fail-under=90`.
- All file paths passed to any tool must contain `.claude/worktrees/issue-921-assemble-baseline-select/` as a substring. The parent repo's files exist at sibling paths and writing to them leaks silently.
- Per `CLAUDE.md` → "Contributing: Read This First": after any change to `pipeline/` affecting CLI flags, subcommand behavior, or artifact schema, update `pipeline/README.md` to match.
- `AssembleError` (from `pipeline/lib/errors.py`, re-exported by `assemble_run`) is the only exception type this code raises for operator-facing failures.

## Behavioral contract being implemented

Copied from the resolved-decisions comment so executors need not fetch it:

1. **One baseline per run.** `assemble` stops emitting every entry in `baselines[]` as its own arm. `--baseline <name>` picks which entry supplies the scenario; every algorithm arm rebases onto it, overriding `algorithms[*].defaults`. Without the flag, the entry named `baseline` is used, falling back to the first entry.
2. **The overlay is reused, not looked up by name.** Resolution order: the selected baseline's own `generated/baselines/<selected>/baseline_config.yaml`; else the sole `generated/baselines/*/baseline_config.yaml` if exactly one exists; else, when several exist, the one whose directory name equals the default baseline name, refusing with an `AssembleError` naming the candidates if that one is absent; else the legacy flat `generated/baseline_config.yaml`; else `None` (empty overlay, current silent behavior — the issue declares that separable).
3. **`scenario[0].name` is rewritten from the selected scenario** via the existing `_align_overlay_name`, otherwise llm-d-benchmark deploys two scenarios instead of merging one.
4. **The selection is hashed.** Written into `manifest.assembly.yaml` as a top-level `baseline:` key and included in `params_hash`.
5. **Out of scope:** rejecting a reused overlay whose plugin config carries a literal model name rather than `${model.name}`. Also out of scope: making a missing baseline overlay warn or fail (the issue's "Separable from this").
6. **Correction to the issue body:** "a missing treatment overlay is fatal" is inaccurate — `resolve_treatment` treats it as an empty layer exactly as the baseline path does. Do not "restore" a symmetry that never existed.

## File Structure

| File | Responsibility for this change |
|---|---|
| `pipeline/lib/assemble_run.py` | New `select_baseline` and `find_baseline_overlay` helpers; `_resolve_packages` emits one baseline; `write_manifest_assembly` and the drift check carry `baseline`; `_additive_grow` preserves the recorded value; `resolve_baseline` aligns the overlay name. |
| `pipeline/sim2real.py` | `--baseline` flag on the `assemble` subparser; pass-through to `assemble_run`; surface the rebase warning. |
| `pipeline/lib/resolve.py` | Read the recorded selection instead of hardcoding `"baseline"`; narrow `phases_declared`; add `cluster_scenarios.baseline_package`. |
| `pipeline/tests/test_assemble_baseline_select.py` | **New.** Unit tests for selection, overlay reuse, name alignment, and the end-to-end one-package assertion. |
| `pipeline/tests/test_assemble_run.py` | Extend `TestWriteManifestAssembly` for the `baseline` key. |
| `pipeline/tests/test_assemble_grow.py` | Grow preserves the recorded `baseline` and keeps `params_hash` consistent. |
| `pipeline/tests/test_resolve.py` | `baseline_yaml` / `phases_declared` / `baseline_package` under a non-default selection. |
| `pipeline/README.md` | `--baseline` flag reference, overlay-reuse rule, `manifest.assembly.yaml` schema note. |
| `CLAUDE.md` | `sim2real assemble` paragraph and the `manifest.assembly.yaml` artifact row. |

---

### Task 1: `select_baseline` — pick exactly one baseline entry

**Files:**
- Modify: `pipeline/lib/assemble_run.py` (add module-level function near `_resolve_scenario_path`, around line 1122)
- Test: `pipeline/tests/test_assemble_baseline_select.py` (create)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `assemble_run.select_baseline(baselines: list[dict], requested: str | None) -> dict` returning the chosen entry dict (the same object from the list, not a copy), and `assemble_run.default_baseline_name(baselines: list[dict]) -> str` returning the name that would be chosen with `requested=None`. Tasks 2, 3 and 5 call both.

- [ ] **Step 1: Write the failing tests**

Create `pipeline/tests/test_assemble_baseline_select.py`:

```python
"""Tests for one-baseline selection and overlay reuse (issue #921)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from pipeline.lib import assemble_run, layout
from pipeline.lib.errors import AssembleError


@pytest.fixture(autouse=True)
def _isolated_experiment_root(tmp_path):
    layout._EXPERIMENT_ROOT = tmp_path
    yield
    layout._EXPERIMENT_ROOT = None


_TWO = [
    {"name": "baseline", "scenario": "baselines/baseline.yaml"},
    {"name": "weka", "scenario": "baselines/baseline-weka.yaml"},
]


class TestSelectBaseline:
    def test_explicit_request_wins(self):
        assert assemble_run.select_baseline(_TWO, "weka")["name"] == "weka"

    def test_default_prefers_entry_named_baseline(self):
        reordered = [_TWO[1], _TWO[0]]
        assert assemble_run.select_baseline(reordered, None)["name"] == "baseline"

    def test_default_falls_back_to_first_entry(self):
        only = [{"name": "weka", "scenario": "b.yaml"},
                {"name": "other", "scenario": "c.yaml"}]
        assert assemble_run.select_baseline(only, None)["name"] == "weka"

    def test_unknown_request_refuses_and_lists_available(self):
        with pytest.raises(AssembleError) as exc:
            assemble_run.select_baseline(_TWO, "nope")
        msg = str(exc.value)
        assert "nope" in msg
        assert "baseline" in msg and "weka" in msg

    def test_empty_baselines_refuses(self):
        with pytest.raises(AssembleError) as exc:
            assemble_run.select_baseline([], None)
        assert "no baselines" in str(exc.value).lower()

    def test_returns_the_same_object_not_a_copy(self):
        entry = assemble_run.select_baseline(_TWO, "weka")
        assert entry is _TWO[1]


class TestDefaultBaselineName:
    def test_prefers_literal_baseline(self):
        assert assemble_run.default_baseline_name(_TWO) == "baseline"

    def test_falls_back_to_first(self):
        assert assemble_run.default_baseline_name(
            [{"name": "weka"}, {"name": "baseline2"}]
        ) == "weka"

    def test_empty_is_empty_string(self):
        assert assemble_run.default_baseline_name([]) == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest pipeline/tests/test_assemble_baseline_select.py -v`
Expected: FAIL with `AttributeError: module 'pipeline.lib.assemble_run' has no attribute 'select_baseline'`

- [ ] **Step 3: Implement**

Insert into `pipeline/lib/assemble_run.py` immediately after `_resolve_scenario_path` (which ends at line 1135):

```python
#: The standardized baseline identifier (issue #544). Used as the default
#: selection and as the tie-break when several generated overlays exist.
_DEFAULT_BASELINE_NAME = "baseline"


def default_baseline_name(baselines: list[dict]) -> str:
    """Return the baseline name ``--baseline`` defaults to.

    The entry named ``baseline`` (the standardized identifier from issue #544)
    when present, else the first entry's name, else ``""`` for an empty list.
    """
    names = [bl.get("name", "") for bl in baselines]
    if _DEFAULT_BASELINE_NAME in names:
        return _DEFAULT_BASELINE_NAME
    return names[0] if names else ""


def select_baseline(baselines: list[dict], requested: str | None) -> dict:
    """Return the single ``baselines[]`` entry this assemble will resolve.

    Assemble emits exactly one baseline package (issue #921). Before that, it
    emitted one per entry in ``baselines[]``, which meant a bundle declaring a
    second deployment got that deployment deployed whether or not any algorithm
    named it — and, because ``sim2real translate`` only requests overlays for
    baselines some ``algorithms[*].defaults`` cross-references, deployed it with
    no EPP config at all.

    ``requested`` is the ``--baseline`` value, or ``None`` for the default
    (see :func:`default_baseline_name`). Raises AssembleError when ``requested``
    names no entry, or when there are no baselines to choose from.
    """
    if not baselines:
        raise AssembleError(
            "transfer.yaml declares no baselines; assemble needs one to "
            "resolve every package against"
        )
    if requested is None:
        name = default_baseline_name(baselines)
    else:
        name = requested
    for bl in baselines:
        if bl.get("name") == name:
            return bl
    raise AssembleError(
        f"--baseline '{requested}' names no baseline in transfer.yaml; "
        f"available: {sorted(bl.get('name', '') for bl in baselines)}"
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest pipeline/tests/test_assemble_baseline_select.py -v`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add pipeline/lib/assemble_run.py pipeline/tests/test_assemble_baseline_select.py
git commit -m "feat(assemble): add select_baseline / default_baseline_name helpers (#921)"
```

---

### Task 2: `find_baseline_overlay` — reuse whatever overlay exists

**Files:**
- Modify: `pipeline/lib/assemble_run.py` (add after `select_baseline` from Task 1)
- Test: `pipeline/tests/test_assemble_baseline_select.py` (append class)

**Interfaces:**
- Consumes: `default_baseline_name` from Task 1.
- Produces: `assemble_run.find_baseline_overlay(generated_root: Path, *, selected: str, default_name: str) -> tuple[Path | None, bool]` — the overlay path (or `None` when nothing exists) and a `reused` flag that is `True` when the returned path is not the selected baseline's own directory. Task 3 calls it.

- [ ] **Step 1: Write the failing tests**

Append to `pipeline/tests/test_assemble_baseline_select.py`:

```python
def _overlay(root: Path, name: str, scenario_name: str = "src-scenario") -> Path:
    d = root / "baselines" / name
    d.mkdir(parents=True, exist_ok=True)
    p = d / "baseline_config.yaml"
    p.write_text(yaml.dump({"scenario": [{"name": scenario_name}]}))
    return p


class TestFindBaselineOverlay:
    def test_prefers_the_selected_baselines_own_overlay(self, tmp_path):
        own = _overlay(tmp_path, "weka")
        _overlay(tmp_path, "baseline")
        path, reused = assemble_run.find_baseline_overlay(
            tmp_path, selected="weka", default_name="baseline"
        )
        assert path == own
        assert reused is False

    def test_reuses_the_sole_overlay_when_selected_has_none(self, tmp_path):
        other = _overlay(tmp_path, "baseline")
        path, reused = assemble_run.find_baseline_overlay(
            tmp_path, selected="weka", default_name="baseline"
        )
        assert path == other
        assert reused is True

    def test_reuses_the_sole_overlay_even_when_it_is_not_the_default(self, tmp_path):
        other = _overlay(tmp_path, "thirdthing")
        path, reused = assemble_run.find_baseline_overlay(
            tmp_path, selected="weka", default_name="baseline"
        )
        assert path == other
        assert reused is True

    def test_several_overlays_tie_break_on_the_default_name(self, tmp_path):
        want = _overlay(tmp_path, "baseline")
        _overlay(tmp_path, "thirdthing")
        path, reused = assemble_run.find_baseline_overlay(
            tmp_path, selected="weka", default_name="baseline"
        )
        assert path == want
        assert reused is True

    def test_several_overlays_and_no_default_refuses_naming_candidates(self, tmp_path):
        _overlay(tmp_path, "alpha")
        _overlay(tmp_path, "bravo")
        with pytest.raises(AssembleError) as exc:
            assemble_run.find_baseline_overlay(
                tmp_path, selected="weka", default_name="baseline"
            )
        msg = str(exc.value)
        assert "alpha" in msg and "bravo" in msg
        assert "--baseline" in msg

    def test_falls_back_to_the_legacy_flat_overlay(self, tmp_path):
        legacy = tmp_path / "baseline_config.yaml"
        legacy.write_text(yaml.dump({"scenario": [{"name": "x"}]}))
        path, reused = assemble_run.find_baseline_overlay(
            tmp_path, selected="weka", default_name="baseline"
        )
        assert path == legacy
        assert reused is True

    def test_per_baseline_layout_wins_over_legacy_flat(self, tmp_path):
        own = _overlay(tmp_path, "weka")
        (tmp_path / "baseline_config.yaml").write_text(
            yaml.dump({"scenario": [{"name": "legacy"}]})
        )
        path, reused = assemble_run.find_baseline_overlay(
            tmp_path, selected="weka", default_name="baseline"
        )
        assert path == own
        assert reused is False

    def test_returns_none_when_nothing_exists(self, tmp_path):
        path, reused = assemble_run.find_baseline_overlay(
            tmp_path, selected="weka", default_name="baseline"
        )
        assert path is None
        assert reused is False

    def test_ignores_a_baselines_dir_with_no_config_file(self, tmp_path):
        (tmp_path / "baselines" / "empty").mkdir(parents=True)
        path, reused = assemble_run.find_baseline_overlay(
            tmp_path, selected="weka", default_name="baseline"
        )
        assert path is None
        assert reused is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest pipeline/tests/test_assemble_baseline_select.py::TestFindBaselineOverlay -v`
Expected: FAIL with `AttributeError: ... has no attribute 'find_baseline_overlay'`

- [ ] **Step 3: Implement**

Insert into `pipeline/lib/assemble_run.py` immediately after `select_baseline`:

```python
def find_baseline_overlay(
    generated_root: Path, *, selected: str, default_name: str
) -> tuple[Path | None, bool]:
    """Return ``(overlay_path, reused)`` for the selected baseline.

    The overlay is *reused*, not looked up by name (issue #921). An
    unreferenced baseline never gets a ``generated/baselines/<name>/``
    directory, because ``sim2real translate`` only asks the skill for overlays
    that some ``algorithms[*].defaults`` cross-references. Keying the lookup on
    the selected name would therefore resolve to nothing for exactly the
    baseline ``--baseline`` exists to select, and assemble would deploy it with
    the Helm chart's stock plugin config.

    Order:

    1. ``baselines/<selected>/baseline_config.yaml`` — the selected baseline's
       own, when the skill (or an operator) produced one. ``reused`` is False.
    2. The sole ``baselines/*/baseline_config.yaml``, when exactly one exists.
    3. When several exist, the one under ``default_name``; absent that, refuse
       rather than pick, because the candidates configure different EPPs and
       guessing is what this issue is about.
    4. The legacy flat ``baseline_config.yaml`` that BYO ``translation
       register`` writes at the generated root.
    5. ``None`` — no overlay anywhere. Resolution continues with an empty
       overlay layer, which is the pre-existing behavior; making that warn or
       fail is tracked separately (issue #921 "Separable from this").

    ``reused`` is True whenever the returned path is not case 1, and is what
    the caller uses to decide it must realign ``scenario[0].name``.
    """
    own = generated_root / "baselines" / selected / "baseline_config.yaml"
    if own.exists():
        return own, False

    candidates = {
        p.parent.name: p
        for p in sorted(generated_root.glob("baselines/*/baseline_config.yaml"))
    }
    if len(candidates) == 1:
        return next(iter(candidates.values())), True
    if len(candidates) > 1:
        if default_name in candidates:
            return candidates[default_name], True
        raise AssembleError(
            f"baseline '{selected}' has no generated overlay and several "
            f"others do ({sorted(candidates)}), none of them the default "
            f"'{default_name}' — cannot choose which to reuse. Re-run "
            "`sim2real translate` so the selected baseline gets its own "
            "overlay, or pass --baseline naming one that has one."
        )

    legacy = generated_root / "baseline_config.yaml"
    if legacy.exists():
        return legacy, True
    return None, False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest pipeline/tests/test_assemble_baseline_select.py -v`
Expected: 19 passed

- [ ] **Step 5: Commit**

```bash
git add pipeline/lib/assemble_run.py pipeline/tests/test_assemble_baseline_select.py
git commit -m "feat(assemble): add find_baseline_overlay reuse resolver (#921)"
```

---

### Task 3: `_resolve_packages` emits one baseline and rebases every algorithm

**Files:**
- Modify: `pipeline/lib/assemble_run.py:385-428` (`resolve_baseline` — align the overlay name), `:1138-1158` (`_ResolvedPackages`), `:1160-1300` (`_resolve_packages`)
- Test: `pipeline/tests/test_assemble_baseline_select.py` (append class)

**Interfaces:**
- Consumes: `select_baseline`, `default_baseline_name` (Task 1), `find_baseline_overlay` (Task 2).
- Produces: `_resolve_packages(..., baseline_request: str | None)` keyword-only parameter, and two new `_ResolvedPackages` fields: `baseline_name: str` (the selected name) and `rebased_algorithms: list[str]` (algorithms whose `defaults` named a different baseline). Tasks 4 and 5 read `baseline_name`; Task 6 surfaces `rebased_algorithms`.

- [ ] **Step 1: Write the failing tests**

Append to `pipeline/tests/test_assemble_baseline_select.py`:

```python
class TestResolveBaselineAlignsOverlay:
    def test_overlay_scenario_name_is_realigned_to_the_bundle(self, tmp_path):
        bundle = tmp_path / "b.yaml"
        bundle.write_text(yaml.dump({"scenario": [{"name": "weka-scn", "a": 1}]}))
        overlay = tmp_path / "o.yaml"
        overlay.write_text(yaml.dump({"scenario": [{"name": "other-scn", "b": 2}]}))
        resolved = assemble_run.resolve_baseline(
            bundle_path=bundle, overlay_path=overlay, framework_defaults={}
        )
        # One scenario entry, not two: the overlay merged onto the bundle
        # instead of appending a phantom entry (the issue #516 failure mode).
        assert len(resolved["scenario"]) == 1
        assert resolved["scenario"][0]["name"] == "weka-scn"
        assert resolved["scenario"][0]["a"] == 1
        assert resolved["scenario"][0]["b"] == 2

    def test_matching_names_are_unaffected(self, tmp_path):
        bundle = tmp_path / "b.yaml"
        bundle.write_text(yaml.dump({"scenario": [{"name": "s", "a": 1}]}))
        overlay = tmp_path / "o.yaml"
        overlay.write_text(yaml.dump({"scenario": [{"name": "s", "b": 2}]}))
        resolved = assemble_run.resolve_baseline(
            bundle_path=bundle, overlay_path=overlay, framework_defaults={}
        )
        assert len(resolved["scenario"]) == 1
        assert resolved["scenario"][0] == {"name": "s", "a": 1, "b": 2}
```

Also append an integration class that drives `assemble_run` end to end. It builds a two-baseline experiment, so it needs its own builder — `_make_experiment` in `test_assemble_run.py` is single-baseline and must not be changed:

```python
def _write_yaml(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data, sort_keys=False))


def _write_json(path: Path, data) -> None:
    import json
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def _make_two_baseline_experiment(tmp_path: Path) -> dict:
    """A bundle shaped like pd-infocomm-4: two baselines, one overlay.

    Both algorithms declare ``defaults: baseline``; nothing names ``weka``, so
    only ``generated/baselines/baseline/`` exists. That is the exact shape that
    deployed an unconfigured EPP in issue #921.
    """
    exp_root = tmp_path / "exp"
    layout._EXPERIMENT_ROOT = exp_root
    workspace = exp_root / "workspace"

    cluster_id = "ocp-east"
    _write_json(
        workspace / "clusters" / cluster_id / "cluster_config.json",
        {
            "cluster_id": cluster_id,
            "namespaces": ["sim2real-slot-0"],
            "secret_names": {"hf_token": "hf-secret"},
            "workspaces": {},
        },
    )

    manifest = {
        "kind": "sim2real-transfer",
        "version": 3,
        "scenario": "test-scenario",
        "component": {"repo": "acme/llm-d-inference-scheduler", "kind": "gaie"},
        "context": {"text": "", "files": []},
        "baselines": [
            {"name": "baseline", "scenario": "baselines/baseline.yaml"},
            {"name": "weka", "scenario": "baselines/baseline-weka.yaml"},
        ],
        "algorithms": [
            {"name": "algoa", "source": "algo/algoa.py", "defaults": "baseline"},
        ],
        "workloads": ["workloads/w1.yaml"],
        "defaults": {"disable": []},
    }
    _write_yaml(exp_root / "transfer.yaml", manifest)
    _write_yaml(
        exp_root / "baselines" / "baseline.yaml",
        {"scenario": [{"name": "scn-30b", "model": {"name": "Qwen3-30B"}}]},
    )
    _write_yaml(
        exp_root / "baselines" / "baseline-weka.yaml",
        {"scenario": [{"name": "scn-weka", "model": {"name": "Qwen3-Next-80B"}}]},
    )
    _write_yaml(exp_root / "workloads" / "w1.yaml", {"name": "wl_a", "num_requests": 10})
    (exp_root / "algo").mkdir(parents=True, exist_ok=True)
    (exp_root / "algo" / "algoa.py").write_text("# stub\n")

    thash = "b" * 64
    tdir = workspace / "translations" / thash
    generated = tdir / "generated"
    generated.mkdir(parents=True)
    _write_json(
        tdir / "translation_output.json",
        {
            "version": 1,
            "translation_hash": thash,
            "source": "byo",
            "alias": "algoa",
            "algorithms": [
                {
                    "name": "algoa",
                    "source_path": None,
                    "source_sha256": None,
                    "config_path": "generated/algoa/algoa_config.yaml",
                    "image_ref": "ghcr.io/foo/bar:v1",
                    "image_digest": "sha256:aa",
                }
            ],
            "created_at": "2026-09-21T14:00:00Z",
        },
    )
    _write_yaml(
        generated / "algoa" / "algoa_config.yaml",
        {"scenario": [{"name": "scn-30b", "router": {"epp": {"replicas": 2}}}]},
    )
    # Only the referenced baseline gets an overlay — the #921 shape.
    _write_yaml(
        generated / "baselines" / "baseline" / "baseline_config.yaml",
        {"scenario": [{"name": "scn-30b", "router": {"epp": {"replicas": 3}}}]},
    )
    return {
        "exp_root": exp_root,
        "cluster_id": cluster_id,
        "translation_hash": thash,
        "manifest_path": exp_root / "transfer.yaml",
    }


class TestAssembleEmitsOneBaseline:
    def _assemble(self, env, *, run="r1", baseline=None, force=False):
        assemble_run.assemble_run(
            translation_hash=env["translation_hash"],
            translation_ref=env["translation_hash"][:12],
            cluster_id=env["cluster_id"],
            run_name=run,
            experiment_root=env["exp_root"],
            manifest_path=env["manifest_path"],
            force=force,
            baseline_request=baseline,
            now_iso="2026-09-21T14:05:00Z",
        )

    def test_default_selection_emits_only_the_default_baseline(self, tmp_path):
        env = _make_two_baseline_experiment(tmp_path)
        self._assemble(env)
        cluster = env["exp_root"] / "workspace" / "runs" / "r1" / "cluster"
        scenarios = sorted(
            p.stem for p in cluster.glob("*.yaml")
            if not p.name.startswith("pipelinerun-")
        )
        assert scenarios == ["algoa", "baseline"]

    def test_explicit_selection_emits_only_that_baseline(self, tmp_path):
        env = _make_two_baseline_experiment(tmp_path)
        self._assemble(env, baseline="weka")
        cluster = env["exp_root"] / "workspace" / "runs" / "r1" / "cluster"
        scenarios = sorted(
            p.stem for p in cluster.glob("*.yaml")
            if not p.name.startswith("pipelinerun-")
        )
        assert scenarios == ["algoa", "weka"]

    def test_selected_baseline_reuses_the_only_overlay(self, tmp_path):
        env = _make_two_baseline_experiment(tmp_path)
        self._assemble(env, baseline="weka")
        resolved = yaml.safe_load(
            (env["exp_root"] / "workspace" / "runs" / "r1" / "cluster" / "weka.yaml")
            .read_text()
        )
        # One scenario entry, carrying weka's model and the reused overlay's
        # EPP config realigned onto weka's scenario name.
        assert len(resolved["scenario"]) == 1
        assert resolved["scenario"][0]["name"] == "scn-weka"
        assert resolved["scenario"][0]["model"]["name"] == "Qwen3-Next-80B"
        assert resolved["scenario"][0]["router"]["epp"]["replicas"] == 3

    def test_algorithm_rebases_onto_the_selected_baseline(self, tmp_path):
        env = _make_two_baseline_experiment(tmp_path)
        self._assemble(env, baseline="weka")
        resolved = yaml.safe_load(
            (env["exp_root"] / "workspace" / "runs" / "r1" / "cluster" / "algoa.yaml")
            .read_text()
        )
        assert len(resolved["scenario"]) == 1
        assert resolved["scenario"][0]["name"] == "scn-weka"
        assert resolved["scenario"][0]["model"]["name"] == "Qwen3-Next-80B"
        # Treatment overlay still wins over the baseline overlay.
        assert resolved["scenario"][0]["router"]["epp"]["replicas"] == 2

    def test_rebased_algorithms_are_recorded_for_the_cli(self, tmp_path):
        env = _make_two_baseline_experiment(tmp_path)
        self._assemble(env, baseline="weka")
        assert assemble_run.assemble_run.rebased_algorithms == ["algoa"]

    def test_no_rebase_note_when_defaults_already_match(self, tmp_path):
        env = _make_two_baseline_experiment(tmp_path)
        self._assemble(env, baseline="baseline")
        assert assemble_run.assemble_run.rebased_algorithms == []

    def test_unknown_baseline_refuses(self, tmp_path):
        env = _make_two_baseline_experiment(tmp_path)
        with pytest.raises(AssembleError) as exc:
            self._assemble(env, baseline="nope")
        assert "nope" in str(exc.value)

    def test_pipelineruns_cover_only_the_selected_baseline(self, tmp_path):
        env = _make_two_baseline_experiment(tmp_path)
        self._assemble(env, baseline="weka")
        cluster = env["exp_root"] / "workspace" / "runs" / "r1" / "cluster"
        names = sorted(p.name for p in cluster.glob("pipelinerun-*.yaml"))
        assert names == [
            "pipelinerun-wl-a|algoa|i1.yaml",
            "pipelinerun-wl-a|weka|i1.yaml",
        ]
```

The `pipelinerun-<workload>|<package>|i<N>.yaml` shape (pipe separators, workload
underscores rendered as hyphens) is the existing convention — see the assertions at
`pipeline/tests/test_assemble_run.py:902-905`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest pipeline/tests/test_assemble_baseline_select.py -v`
Expected: `TestResolveBaselineAlignsOverlay::test_overlay_scenario_name_is_realigned_to_the_bundle` fails (two scenario entries); `TestAssembleEmitsOneBaseline` fails with `TypeError: assemble_run() got an unexpected keyword argument 'baseline_request'`.

- [ ] **Step 3: Align the overlay name in `resolve_baseline`**

In `pipeline/lib/assemble_run.py`, replace the overlay load inside `resolve_baseline` (currently lines 414-418):

```python
    overlay = (
        _load_yaml(overlay_path)
        if overlay_path is not None and overlay_path.exists()
        else {}
    )
```

with:

```python
    overlay = (
        _load_yaml(overlay_path)
        if overlay_path is not None and overlay_path.exists()
        else {}
    )
    # Realign the overlay's scenario name to the bundle's, for the same reason
    # framework defaults are realigned below: a name mismatch makes deep_merge
    # append a second scenario entry, which llm-d-benchmark renders as a
    # separate deployment (issue #516). This matters now that the overlay may
    # have been generated for a *different* baseline and reused (issue #921),
    # and is a no-op when the names already agree.
    overlay = _align_overlay_name(bundle, overlay)
```

Then extend the docstring's overlay sentence. Replace:

```
    ``framework_defaults`` may be ``{}`` (experiment has no
    ``baselines/defaults/`` directory). ``overlay_path`` may be ``None`` or
    point at a non-existent file (BYO baseline without a baseline overlay).
    Bundle is required — a missing bundle raises AssembleError.
```

with:

```
    ``framework_defaults`` may be ``{}`` (experiment has no
    ``baselines/defaults/`` directory). ``overlay_path`` may be ``None`` or
    point at a non-existent file (BYO baseline without a baseline overlay).
    Bundle is required — a missing bundle raises AssembleError.

    Both the framework defaults and the overlay have their
    ``scenario[0].name`` realigned to the bundle's before merging. The overlay
    needs it because ``find_baseline_overlay`` may return an overlay generated
    for a different baseline (issue #921).
```

- [ ] **Step 4: Add the two new `_ResolvedPackages` fields**

In `pipeline/lib/assemble_run.py`, add to the `_ResolvedPackages` NamedTuple (starts line 1138) after the existing `resolved_baselines` field:

```python
    baseline_name: str
    rebased_algorithms: list[str]
```

- [ ] **Step 5: Rewrite the baseline loop in `_resolve_packages`**

Add `baseline_request: str | None` as a keyword-only parameter to `_resolve_packages`'s signature (line 1160-1168), then replace the whole baseline loop (currently lines 1220-1251):

```python
    packages: list[tuple[str, dict]] = []
    resolved_baselines: dict[str, dict] = {}
    scalar_list_conflicts: list[str] = []
    for bl in manifest.get("baselines", []):
        bl_name = bl["name"]
        ...
        resolved_baselines[bl_name] = resolved
        packages.append((bl_name, resolved))
```

with:

```python
    packages: list[tuple[str, dict]] = []
    scalar_list_conflicts: list[str] = []

    # Exactly one baseline package per run (issue #921). Emitting one per
    # entry in ``baselines[]`` deployed every declared baseline whether or not
    # any algorithm named it, while ``sim2real translate`` only produced
    # overlays for the named ones — so an unreferenced baseline was deployed
    # with the Helm chart's stock plugin config, silently.
    manifest_baselines = manifest.get("baselines", []) or []
    selected = select_baseline(manifest_baselines, baseline_request)
    baseline_name = selected["name"]
    bundle_path = _resolve_scenario_path(
        exp_root, selected.get("scenario"), "baseline.yaml"
    )
    if bundle_path is None:
        raise AssembleError(f"baseline '{baseline_name}' has no scenario file")
    overlay_path, _reused = find_baseline_overlay(
        generated_root,
        selected=baseline_name,
        default_name=default_baseline_name(manifest_baselines),
    )
    baseline_resolved = resolve_baseline(
        bundle_path=bundle_path,
        overlay_path=overlay_path,
        framework_defaults=framework_defaults,
        sink=scalar_list_conflicts,
    )
    resolved_baselines: dict[str, dict] = {baseline_name: baseline_resolved}
    packages.append((baseline_name, baseline_resolved))
```

- [ ] **Step 6: Rebase every algorithm onto the selection**

Replace the treatment loop's baseline lookup (currently lines 1253-1266):

```python
    for algo in kept_algos:
        algo_name = algo["name"]
        base_name = algo["defaults"]
        if base_name not in resolved_baselines:
            raise AssembleError(
                f"algorithm '{algo_name}' references unknown baseline "
                f"'{base_name}'; known: {sorted(resolved_baselines)}"
            )
        diffs_path = _resolve_scenario_path(
            exp_root, algo.get("scenario"), "treatment.yaml"
        )
        overlay_path = generated_root / algo_name / f"{algo_name}_config.yaml"
        resolved = resolve_treatment(
            baseline_resolved=resolved_baselines[base_name],
            ...
```

with:

```python
    # Every algorithm rebases onto the selected baseline, overriding its own
    # ``defaults`` (issue #921). ``defaults`` still decides which baseline the
    # translate skill generates an overlay for; here it is only recorded when
    # it disagrees, so the CLI can say which arms were rebased.
    rebased_algorithms: list[str] = []
    for algo in kept_algos:
        algo_name = algo["name"]
        if algo.get("defaults") != baseline_name:
            rebased_algorithms.append(algo_name)
        diffs_path = _resolve_scenario_path(
            exp_root, algo.get("scenario"), "treatment.yaml"
        )
        algo_overlay_path = generated_root / algo_name / f"{algo_name}_config.yaml"
        resolved = resolve_treatment(
            baseline_resolved=baseline_resolved,
            diffs_path=diffs_path,
            overlay_path=algo_overlay_path,
            sink=scalar_list_conflicts,
        )
```

Leave the rest of that loop (`inject_image_tag`, `packages.append`) as it is. Note the local rename from `overlay_path` to `algo_overlay_path` — the baseline's `overlay_path` is still live in this scope and reusing the name would shadow it confusingly.

Then add both new fields to the `_ResolvedPackages(...)` construction at the end (line ~1289):

```python
        resolved_baselines=resolved_baselines,
        baseline_name=baseline_name,
        rebased_algorithms=rebased_algorithms,
```

- [ ] **Step 7: Thread `baseline_request` through `assemble_run`**

Add to `assemble_run`'s signature (line 1370), after `force: bool`:

```python
    baseline_request: "str | None" = None,
```

Add to the side-band reset block (near line 1466):

```python
    assemble_run.rebased_algorithms = []  # type: ignore[attr-defined]
```

Pass it at the `_resolve_packages` call site (line ~1676):

```python
    resolved = _resolve_packages(
        manifest,
        exp_root=exp_root,
        translation_dir=tdir,
        tout_path=tout_path,
        cluster_config=cluster_config,
        translation_ref=translation_ref,
        baseline_request=baseline_request,
    )
```

and record the side-band value immediately after, alongside the existing two:

```python
    assemble_run.rebased_algorithms = resolved.rebased_algorithms  # type: ignore[attr-defined]
```

- [ ] **Step 8: Run the new tests**

Run: `.venv/bin/python -m pytest pipeline/tests/test_assemble_baseline_select.py -v`
Expected: all pass. Fix the PipelineRun-filename assertion per the Step 1 note if it is the only failure.

- [ ] **Step 9: Run the full assemble suite to find regressions**

Run: `.venv/bin/python -m pytest pipeline/tests/test_assemble_run.py pipeline/tests/test_assemble_scope.py pipeline/tests/test_assemble_replicas.py pipeline/tests/test_assemble_grow.py -v`

Expected failures to fix, all from tests that asserted the old multi-baseline behavior or the removed error path:
- Any test asserting an `AssembleError` for "references unknown baseline" — that check is gone, because `defaults` no longer selects anything. `manifest.py:257` still rejects a `defaults` that names no baseline at load time, so the condition is still refused, just earlier. Delete such a test and note the `manifest.py` cross-reference check in the deletion commit message.
- Any test asserting a `cluster/` file for a second baseline. Update to the one-baseline expectation.

Do not weaken an assertion to make it pass — if a test's intent no longer has a corresponding behavior, delete it and say why.

- [ ] **Step 10: Commit**

```bash
git add pipeline/lib/assemble_run.py pipeline/tests/
git commit -m "feat(assemble): resolve exactly one baseline and rebase every arm onto it (#921)"
```

---

### Task 4: Record the selection in `manifest.assembly.yaml` and `params_hash`

**Files:**
- Modify: `pipeline/lib/assemble_run.py:273-292` (`write_manifest_assembly`), `:295-310` (`compute_params_hash` docstring), `:1604-1612` (drift check), `:1300-1366` (`_additive_grow`), `:1809-1812` (the write call)
- Test: `pipeline/tests/test_assemble_run.py` (`TestWriteManifestAssembly`), `pipeline/tests/test_assemble_baseline_select.py` (append), `pipeline/tests/test_assemble_grow.py` (append)

**Interfaces:**
- Consumes: `_ResolvedPackages.baseline_name` from Task 3.
- Produces: `write_manifest_assembly(run_dir, manifest, *, now_iso, replicas=1, baseline=None)` — a `baseline=None` omits the key entirely, which is the legacy snapshot shape. `compute_params_hash` continues to pop only `replicas`, so a present `baseline` key is hashed.

- [ ] **Step 1: Write the failing tests**

Append to `TestWriteManifestAssembly` in `pipeline/tests/test_assemble_run.py`:

```python
    def test_writes_baseline_field_when_provided(self, tmp_path):
        manifest = {
            "kind": "sim2real-transfer",
            "version": 3,
            "scenario": "test",
            "component": {"repo": "acme/foo"},
            "context": {"text": "", "files": []},
            "baselines": [{"name": "weka", "scenario": "b.yaml"}],
            "algorithms": [],
            "workloads": [],
            "defaults": {"disable": []},
        }
        run_dir = tmp_path / "runs" / "t"
        run_dir.mkdir(parents=True)
        p = assemble_run.write_manifest_assembly(
            run_dir, manifest, now_iso="2026-09-21T00:00:00Z", baseline="weka"
        )
        assert yaml.safe_load(p.read_text())["baseline"] == "weka"

    def test_omits_baseline_field_when_none(self, tmp_path):
        manifest = {
            "kind": "sim2real-transfer",
            "version": 3,
            "scenario": "test",
            "component": {"repo": "acme/foo"},
            "context": {"text": "", "files": []},
            "baselines": [{"name": "baseline", "scenario": "b.yaml"}],
            "algorithms": [],
            "workloads": [],
            "defaults": {"disable": []},
        }
        run_dir = tmp_path / "runs" / "t"
        run_dir.mkdir(parents=True)
        p = assemble_run.write_manifest_assembly(
            run_dir, manifest, now_iso="2026-09-21T00:00:00Z"
        )
        assert "baseline" not in yaml.safe_load(p.read_text())

    def test_params_hash_includes_baseline_but_not_replicas(self, tmp_path):
        manifest = {
            "kind": "sim2real-transfer",
            "version": 3,
            "scenario": "test",
            "component": {"repo": "acme/foo"},
            "context": {"text": "", "files": []},
            "baselines": [{"name": "baseline", "scenario": "b.yaml"}],
            "algorithms": [],
            "workloads": [],
            "defaults": {"disable": []},
        }
        hashes = {}
        for label, kwargs in {
            "a_r1": {"baseline": "baseline", "replicas": 1},
            "a_r5": {"baseline": "baseline", "replicas": 5},
            "b_r1": {"baseline": "weka", "replicas": 1},
        }.items():
            d = tmp_path / label
            d.mkdir()
            p = assemble_run.write_manifest_assembly(
                d, manifest, now_iso="2026-09-21T00:00:00Z", **kwargs
            )
            hashes[label] = assemble_run.compute_params_hash(p)
        assert hashes["a_r1"] == hashes["a_r5"], "replicas must not affect the hash"
        assert hashes["a_r1"] != hashes["b_r1"], "baseline must affect the hash"
```

Append to `pipeline/tests/test_assemble_baseline_select.py`:

```python
class TestBaselineSelectionIsHashed:
    def _assemble(self, env, *, run, baseline=None, force=False):
        assemble_run.assemble_run(
            translation_hash=env["translation_hash"],
            translation_ref=env["translation_hash"][:12],
            cluster_id=env["cluster_id"],
            run_name=run,
            experiment_root=env["exp_root"],
            manifest_path=env["manifest_path"],
            force=force,
            baseline_request=baseline,
            now_iso="2026-09-21T14:05:00Z",
        )

    def _runs(self, env):
        return env["exp_root"] / "workspace" / "runs"

    def test_snapshot_records_the_selection(self, tmp_path):
        env = _make_two_baseline_experiment(tmp_path)
        self._assemble(env, run="r1", baseline="weka")
        ma = yaml.safe_load(
            (self._runs(env) / "r1" / "manifest.assembly.yaml").read_text()
        )
        assert ma["baseline"] == "weka"

    def test_two_selections_get_different_params_hashes(self, tmp_path):
        env = _make_two_baseline_experiment(tmp_path)
        self._assemble(env, run="r1", baseline="baseline")
        self._assemble(env, run="r2", baseline="weka")
        import json
        h1 = json.loads(
            (self._runs(env) / "r1" / "run_metadata.json").read_text()
        )["params_hash"]
        h2 = json.loads(
            (self._runs(env) / "r2" / "run_metadata.json").read_text()
        )["params_hash"]
        assert h1 != h2

    def test_reassembling_with_a_different_baseline_is_drift(self, tmp_path):
        env = _make_two_baseline_experiment(tmp_path)
        self._assemble(env, run="r1", baseline="baseline")
        with pytest.raises(AssembleError) as exc:
            self._assemble(env, run="r1", baseline="weka")
        assert "changed since last assemble" in str(exc.value)

    def test_force_overrides_the_drift_refusal(self, tmp_path):
        env = _make_two_baseline_experiment(tmp_path)
        self._assemble(env, run="r1", baseline="baseline")
        self._assemble(env, run="r1", baseline="weka", force=True)
        ma = yaml.safe_load(
            (self._runs(env) / "r1" / "manifest.assembly.yaml").read_text()
        )
        assert ma["baseline"] == "weka"

    def test_reassembling_with_the_same_baseline_is_not_drift(self, tmp_path):
        env = _make_two_baseline_experiment(tmp_path)
        self._assemble(env, run="r1", baseline="weka")
        # Second call must not raise; it no-ops because the pairs exist.
        self._assemble(env, run="r1", baseline="weka")
        assert assemble_run.assemble_run.status == "noop"
```

Append to `pipeline/tests/test_assemble_grow.py`:

```python
class TestGrowPreservesRecordedBaseline:
    def test_grow_keeps_the_snapshot_baseline_and_hash_consistent(self, tmp_path):
        """Grow must not rewrite the recorded selection.

        It preserves ``params_hash`` (the drift check already passed), so
        writing a snapshot whose ``baseline`` key disagrees with that hash
        would make the *next* assemble report false drift.
        """
        import json
        from pipeline.tests.test_assemble_baseline_select import (
            _make_two_baseline_experiment,
        )
        env = _make_two_baseline_experiment(tmp_path)
        kwargs = dict(
            translation_hash=env["translation_hash"],
            translation_ref=env["translation_hash"][:12],
            cluster_id=env["cluster_id"],
            run_name="r1",
            experiment_root=env["exp_root"],
            manifest_path=env["manifest_path"],
            force=False,
            now_iso="2026-09-21T14:05:00Z",
        )
        assemble_run.assemble_run(baseline_request="weka", replicas=1, **kwargs)
        runs = env["exp_root"] / "workspace" / "runs" / "r1"
        before = json.loads((runs / "run_metadata.json").read_text())["params_hash"]

        assemble_run.assemble_run(baseline_request="weka", replicas=2, **kwargs)
        ma = yaml.safe_load((runs / "manifest.assembly.yaml").read_text())
        after = json.loads((runs / "run_metadata.json").read_text())["params_hash"]
        assert ma["baseline"] == "weka"
        assert ma["replicas"] == 2
        assert after == before

        # And the next assemble sees no drift.
        assemble_run.assemble_run(baseline_request="weka", replicas=2, **kwargs)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest pipeline/tests/test_assemble_run.py::TestWriteManifestAssembly pipeline/tests/test_assemble_baseline_select.py::TestBaselineSelectionIsHashed pipeline/tests/test_assemble_grow.py::TestGrowPreservesRecordedBaseline -v`
Expected: FAIL with `TypeError: write_manifest_assembly() got an unexpected keyword argument 'baseline'`

- [ ] **Step 3: Implement `write_manifest_assembly`**

Replace the signature and `out_dict` construction (lines 273-285):

```python
def write_manifest_assembly(
    run_dir: Path, manifest: dict, *, now_iso: str, replicas: int = 1,
) -> Path:
    """Serialize ``slicer.assembly_slice(manifest)`` + ``replicas: N`` to
    ``manifest.assembly.yaml``.

    Prepends a one-line comment header naming the tool and timestamp.
    Returns the written path.
    """
    slice_ = slicer.assembly_slice(manifest)
    # Emit replicas at the top of the file for human readability, before the
    # rest of the assembly slice.
    out_dict = {"replicas": replicas, **slice_}
```

with:

```python
def write_manifest_assembly(
    run_dir: Path,
    manifest: dict,
    *,
    now_iso: str,
    replicas: int = 1,
    baseline: str | None = None,
) -> Path:
    """Serialize ``slicer.assembly_slice(manifest)`` + ``replicas``/``baseline``
    to ``manifest.assembly.yaml``.

    ``baseline`` is the single baseline package this run resolves (issue #921).
    Unlike ``replicas`` it is *included* in ``params_hash``: two runs that
    differ only in ``--baseline`` can serve different models, so a hash that
    could not tell them apart would report them as the same parameters.

    ``baseline=None`` omits the key, producing the pre-#921 snapshot shape.
    ``_additive_grow`` uses that to preserve a legacy run's shape, since it
    preserves that run's ``params_hash`` too and the two must agree.

    Prepends a one-line comment header naming the tool and timestamp.
    Returns the written path.
    """
    slice_ = slicer.assembly_slice(manifest)
    # Emit replicas and the baseline selection at the top of the file for human
    # readability, before the rest of the assembly slice.
    out_dict: dict = {"replicas": replicas}
    if baseline is not None:
        out_dict["baseline"] = baseline
    out_dict.update(slice_)
```

- [ ] **Step 4: Document the hash asymmetry in `compute_params_hash`**

Append to `compute_params_hash`'s docstring, after the existing `replicas` paragraph:

```
    ``baseline`` (issue #921) is deliberately NOT excluded. It names the single
    baseline package the run resolves, and two runs differing only in it can
    serve different models — so it belongs in the identity of the parameters.
    Snapshots written before #921 carry no such key and hash exactly as before.
```

- [ ] **Step 5: Thread the selection into the drift check**

The drift check recomputes the canonical dict from the manifest rather than reading the file, so it must add the same key. Replace lines 1604-1612:

```python
            new_slice = slicer.assembly_slice(manifest)
            new_canonical = yaml.dump(
                new_slice, sort_keys=True, default_flow_style=False,
                allow_unicode=True,
            ).encode("utf-8")
            new_content_hash = hashlib.sha256(new_canonical).hexdigest()
            prior_params_hash = prior_rm.get("params_hash", "")
```

with:

```python
            # Mirror write_manifest_assembly + compute_params_hash exactly:
            # the snapshot's ``baseline`` key is hashed, so the comparison
            # dict must carry it too. A snapshot written before #921 has no
            # such key; comparing without it there keeps every pre-existing
            # run from reporting false drift on its next assemble. The cost is
            # that a legacy run re-assembled with an explicit --baseline is
            # rebased without a drift refusal — the operator asked for it, and
            # the rewritten snapshot records it from then on.
            new_slice = slicer.assembly_slice(manifest)
            if isinstance(prior_ma, dict) and prior_ma.get("baseline") is not None:
                new_slice = {
                    "baseline": select_baseline(
                        manifest.get("baselines", []) or [], baseline_request
                    )["name"],
                    **new_slice,
                }
            new_canonical = yaml.dump(
                new_slice, sort_keys=True, default_flow_style=False,
                allow_unicode=True,
            ).encode("utf-8")
            new_content_hash = hashlib.sha256(new_canonical).hexdigest()
            prior_params_hash = prior_rm.get("params_hash", "")
```

- [ ] **Step 6: Preserve the recorded value in `_additive_grow`**

Add a keyword-only parameter to `_additive_grow`'s signature:

```python
    recorded_baseline: str | None = None,
```

and pass it through at the `write_manifest_assembly` call (line 1354):

```python
    # Rewrite manifest.assembly.yaml with new replicas count. The baseline
    # selection is carried over verbatim rather than re-derived: grow preserves
    # params_hash (the drift check passed), and a snapshot whose baseline key
    # disagreed with that hash would make the next assemble report false drift.
    write_manifest_assembly(
        run_dir, manifest, now_iso=now_iso, replicas=new_replicas,
        baseline=recorded_baseline,
    )
```

At the `_additive_grow` call site in `assemble_run` (line ~1646), pass the prior snapshot's value:

```python
        grow_plan = _additive_grow(
            run_dir,
            manifest,
            prior_replicas=additive_grow_from,
            new_replicas=replicas_effective,
            now_iso=now_iso,
            recorded_baseline=(
                prior_ma.get("baseline") if isinstance(prior_ma, dict) else None
            ),
        )
```

- [ ] **Step 7: Pass the selection at the main write site**

Replace the `write_manifest_assembly` call at line ~1809:

```python
        manifest_assembly_path = write_manifest_assembly(
            run_dir, manifest, now_iso=now_iso, replicas=replicas_effective,
        )
```

with:

```python
        manifest_assembly_path = write_manifest_assembly(
            run_dir, manifest, now_iso=now_iso, replicas=replicas_effective,
            baseline=resolved.baseline_name,
        )
```

Read the surrounding lines first and preserve the exact existing argument list — only the `baseline=` argument is being added.

- [ ] **Step 8: Run tests**

Run: `.venv/bin/python -m pytest pipeline/tests/test_assemble_run.py pipeline/tests/test_assemble_grow.py pipeline/tests/test_assemble_replicas.py pipeline/tests/test_assemble_scope.py pipeline/tests/test_assemble_baseline_select.py -v`
Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add pipeline/lib/assemble_run.py pipeline/tests/
git commit -m "feat(assemble): record the baseline selection in manifest.assembly.yaml and params_hash (#921)"
```

---

### Task 5: `resolve --run` reads the recorded selection

**Files:**
- Modify: `pipeline/lib/resolve.py:265-306` (`_build_cluster_scenarios_section`), `:427-440` (`_phases_declared_from_manifest`), and `_build_results_section`'s call to it
- Test: `pipeline/tests/test_resolve.py` (append class)

**Interfaces:**
- Consumes: the `baseline` key written by Task 4.
- Produces: `cluster_scenarios.baseline_package` (new string field naming the selected baseline). `cluster_scenarios.baseline_yaml` now points at `cluster/<selected>.yaml`. `results.phases_declared` lists only the selected baseline plus the algorithms.

- [ ] **Step 1: Write the failing tests**

Append to `pipeline/tests/test_resolve.py`. Read the file's existing fixtures first and reuse whatever run-directory builder it already has; the class below assumes a `tmp_path`-based helper that writes `manifest.assembly.yaml` and `cluster/`. If no such helper exists, write the two files inline as shown:

```python
class TestSelectedBaselineIsHonored:
    def _run_dir(self, tmp_path, *, baseline_key, scenario_stem):
        run_dir = tmp_path / "runs" / "r1"
        (run_dir / "cluster").mkdir(parents=True)
        ma = {
            "replicas": 1,
            "baselines": [
                {"name": "baseline", "scenario": "baselines/baseline.yaml"},
                {"name": "weka", "scenario": "baselines/baseline-weka.yaml"},
            ],
            "algorithms": [{"name": "algoa", "defaults": "baseline"}],
            "workloads": ["workloads/w1.yaml"],
        }
        if baseline_key is not None:
            ma["baseline"] = baseline_key
        (run_dir / "manifest.assembly.yaml").write_text(yaml.dump(ma))
        (run_dir / "cluster" / f"{scenario_stem}.yaml").write_text("scenario: []\n")
        (run_dir / "cluster" / "algoa.yaml").write_text("scenario: []\n")
        return run_dir, ma

    def test_baseline_yaml_follows_the_recorded_selection(self, tmp_path):
        run_dir, ma = self._run_dir(
            tmp_path, baseline_key="weka", scenario_stem="weka"
        )
        section = resolve._build_cluster_scenarios_section(run_dir, ma)
        assert section["baseline_package"] == "weka"
        assert section["baseline_yaml"] == str(run_dir / "cluster" / "weka.yaml")
        assert set(section["treatment_yamls"]) == {"algoa"}

    def test_legacy_snapshot_without_the_key_still_finds_baseline_yaml(self, tmp_path):
        run_dir, ma = self._run_dir(
            tmp_path, baseline_key=None, scenario_stem="baseline"
        )
        section = resolve._build_cluster_scenarios_section(run_dir, ma)
        assert section["baseline_package"] == "baseline"
        assert section["baseline_yaml"] == str(run_dir / "cluster" / "baseline.yaml")

    def test_phases_declared_lists_only_the_selected_baseline(self, tmp_path):
        _, ma = self._run_dir(tmp_path, baseline_key="weka", scenario_stem="weka")
        assert resolve._phases_declared_from_manifest(ma) == ["weka", "algoa"]

    def test_phases_declared_falls_back_to_all_baselines_when_unrecorded(self, tmp_path):
        _, ma = self._run_dir(tmp_path, baseline_key=None, scenario_stem="baseline")
        assert resolve._phases_declared_from_manifest(ma) == [
            "baseline", "weka", "algoa",
        ]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest pipeline/tests/test_resolve.py::TestSelectedBaselineIsHonored -v`
Expected: FAIL — `KeyError: 'baseline_package'`, and `baseline_yaml` is `None` for the weka case.

- [ ] **Step 3: Add a shared selected-name helper to `resolve.py`**

Insert near the other module-level helpers in `pipeline/lib/resolve.py`:

```python
def _selected_baseline_name(manifest_assembly: dict | None) -> str:
    """Return the baseline package the run resolved, per its snapshot.

    ``sim2real assemble`` records its single baseline selection as a top-level
    ``baseline`` key (issue #921). Snapshots written before that carry no key,
    and for those the historical answer is the literal ``"baseline"`` — the
    standardized identifier from issue #544 — which is what every pre-#921 run
    named its baseline scenario file.
    """
    ma = manifest_assembly or {}
    recorded = ma.get("baseline")
    return recorded if isinstance(recorded, str) and recorded else "baseline"
```

- [ ] **Step 4: Use it in `_build_cluster_scenarios_section`**

Replace the hardcoded comparison (line ~292):

```python
            elif name == "baseline":
                baseline_yaml = str(yaml_path)
```

with:

```python
            elif name == baseline_package:
                baseline_yaml = str(yaml_path)
```

Define `baseline_package` just above the loop:

```python
    baseline_package = _selected_baseline_name(manifest_assembly)
```

and add it to the returned dict:

```python
    return {
        "cluster_dir": str(cluster_dir),
        "baseline_package": baseline_package,
        "baseline_yaml": baseline_yaml,
        "treatment_yamls": treatment_yamls,
        "pipelinerun_yamls": pipelinerun_yamls,
    }
```

Update the function's docstring sentence "``baseline_yaml`` is the (currently singular) baseline scenario file" to:

```
    ``baseline_package`` is the baseline the run resolved, read from the
    snapshot's ``baseline`` key (issue #921) and defaulting to ``"baseline"``
    for pre-#921 runs. ``baseline_yaml`` is that package's scenario file.
```

- [ ] **Step 5: Narrow `_phases_declared_from_manifest`**

Read the existing body first, then change it so the baseline portion is the selected name alone when the snapshot records one, and the full baseline list otherwise:

```python
def _phases_declared_from_manifest(manifest_assembly: dict | None) -> list[str]:
    """Return the baseline phase + algorithm names from the manifest.

    A run resolves exactly one baseline (issue #921), so when the snapshot
    records which one, that is the only baseline phase that can hold results.
    Pre-#921 snapshots carry no ``baseline`` key and did deploy every declared
    baseline, so all of them are listed for those.

    Order: baseline(s) first (in manifest order), then algorithms (in manifest
    order). Empty when the manifest is absent.
    """
    ma = manifest_assembly or {}
    all_baselines = [
        bl.get("name", "")
        for bl in (ma.get("baselines") or [])
        if isinstance(bl, dict) and bl.get("name")
    ]
    if ma.get("baseline"):
        selected = _selected_baseline_name(ma)
        baseline_phases = [selected] if selected in all_baselines else []
    else:
        baseline_phases = all_baselines
    algos = [
        a.get("name", "")
        for a in (ma.get("algorithms") or [])
        if isinstance(a, dict) and a.get("name")
    ]
    return baseline_phases + algos
```

- [ ] **Step 6: Run tests**

Run: `.venv/bin/python -m pytest pipeline/tests/test_resolve.py pipeline/tests/test_assemble_run.py::TestAssembleResolveContract -v`
Expected: all pass. If `TestAssembleResolveContract` asserts the exact key set of `cluster_scenarios`, add `baseline_package` to its expectation.

- [ ] **Step 7: Commit**

```bash
git add pipeline/lib/resolve.py pipeline/tests/test_resolve.py pipeline/tests/test_assemble_run.py
git commit -m "feat(resolve): honor the recorded baseline selection in resolve --run (#921)"
```

---

### Task 6: `--baseline` CLI flag

**Files:**
- Modify: `pipeline/sim2real.py:1176-1228` (assemble subparser), `:2566-2585` (the `assemble_run` call), `:2590-2600` (warning surface)
- Test: `pipeline/tests/test_assemble_baseline_select.py` (append class)

**Interfaces:**
- Consumes: `assemble_run(..., baseline_request=...)` (Task 3) and `assemble_run.rebased_algorithms` (Task 3).
- Produces: `--baseline NAME` on `sim2real assemble`.

- [ ] **Step 1: Write the failing test**

Find how the existing tests invoke the CLI (grep `pipeline/tests/` for `sim2real.main(` or `build_parser`) and match that style. Append to `pipeline/tests/test_assemble_baseline_select.py`:

```python
class TestBaselineFlagParsing:
    def test_flag_is_accepted_and_defaults_to_none(self):
        from pipeline import sim2real
        parser = sim2real.build_parser()
        args = parser.parse_args(
            ["assemble", "--translation", "abcd", "--cluster", "c", "--run", "r"]
        )
        assert args.baseline is None
        args = parser.parse_args(
            ["assemble", "--translation", "abcd", "--cluster", "c", "--run", "r",
             "--baseline", "weka"]
        )
        assert args.baseline == "weka"
```

`build_parser` is the real factory at `pipeline/sim2real.py:1003`.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest pipeline/tests/test_assemble_baseline_select.py::TestBaselineFlagParsing -v`
Expected: FAIL — `unrecognized arguments: --baseline weka`

- [ ] **Step 3: Add the flag**

In `pipeline/sim2real.py`, insert into the assemble subparser immediately after the `--force` argument (which ends around line 1192):

```python
    asm.add_argument(
        "--baseline",
        metavar="NAME",
        default=None,
        help="baseline package to resolve every arm against (default: the "
             "entry named 'baseline', else the first in transfer.yaml). A run "
             "carries exactly one baseline; comparing an algorithm across two "
             "server configs means two runs",
    )
```

- [ ] **Step 4: Pass it through**

At the `assemble_run(...)` call (line ~2570), add after `force=args.force,`:

```python
            baseline_request=args.baseline,
```

- [ ] **Step 5: Surface the rebase warning**

Insert after the existing `skipped_algorithms` warning loop (around line 2590), before the `scalar_list_conflicts` loop:

```python
    rebased = getattr(_assemble_run_lib.assemble_run, "rebased_algorithms", [])
    if rebased:
        print(
            f"note: {', '.join(rebased)} declare a different 'defaults' in "
            "transfer.yaml but were resolved against baseline "
            f"'{args.baseline}' as requested",
            file=sys.stderr,
        )
```

- [ ] **Step 6: Run tests and lint**

Run: `.venv/bin/python -m pytest pipeline/tests/test_assemble_baseline_select.py -v && .venv/bin/ruff check pipeline/ .claude/skills/ --select F`
Expected: all pass, no lint findings.

- [ ] **Step 7: Commit**

```bash
git add pipeline/sim2real.py pipeline/tests/test_assemble_baseline_select.py
git commit -m "feat(assemble): add --baseline flag (#921)"
```

---

### Task 7: Documentation and the stale-reference sweep

**Files:**
- Modify: `pipeline/README.md`, `CLAUDE.md`
- Modify: any file the sweep flags

- [ ] **Step 1: Find every place that documents the old behavior**

Run each and read every hit:

```bash
grep -rn "baselines\[" --include=*.md . | grep -v node_modules
grep -rn "manifest.assembly.yaml" --include=*.md .
grep -rn "baseline_config.yaml" --include=*.md .
grep -rn "params_hash" --include=*.md .
grep -rn "baseline_yaml\|phases_declared\|cluster_scenarios" --include=*.md .
grep -rn "one per baseline\|per-baseline overlay\|each baseline" --include=*.md .
```

For each hit decide: stale (update), still accurate (leave), unrelated (leave). Known files that will need edits: `pipeline/README.md` (the `assemble` flag reference, the `manifest.assembly.yaml` description, the `translate` outputs section that describes `generated/baselines/<name>/`), and `CLAUDE.md` (the `sim2real assemble` paragraph and the `manifest.assembly.yaml` artifact-table row). `docs/epics/step-3/design.md:131-133` documents the `cluster_scenarios` shape — update it to include `baseline_package` or note that the design predates #921, whichever reads more honestly.

- [ ] **Step 2: Update `pipeline/README.md`**

Add `--baseline NAME` to the assemble flag reference with the default rule, and a short subsection documenting: one baseline package per run; the overlay-reuse order from Task 2 including the refusal case; that `scenario[0].name` is realigned; and that `manifest.assembly.yaml` carries a top-level `baseline:` key which — unlike `replicas` — is part of `params_hash`.

- [ ] **Step 3: Update `CLAUDE.md`**

In the `sim2real assemble` paragraph, state that assemble resolves exactly one baseline selected by `--baseline` (default: the entry named `baseline`, else the first), that every algorithm rebases onto it overriding `defaults`, and that the overlay is reused rather than looked up by name. In the `runs/<run>/manifest.assembly.yaml` artifact row, note it carries `baseline: <name>` and that it counts toward `params_hash` while `replicas` does not.

- [ ] **Step 4: Verify the docs match the code**

Re-read both edited sections against the implemented behavior. Every flag name, default, and file path must be one you can point at in the diff.

- [ ] **Step 5: Commit**

```bash
git add pipeline/README.md CLAUDE.md docs/
git commit -m "docs: document assemble --baseline and the one-baseline-per-run rule (#921)"
```

---

### Task 8: Full verification

- [ ] **Step 1: Full test suite with the CI gate**

```bash
.venv/bin/python -m pytest pipeline/ \
  .claude/skills/sim2real-analyze/tests/ \
  .claude/skills/sim2real-bootstrap/tests/ \
  .claude/skills/sim2real-translate/tests/ \
  .claude/skills/sim2real-check/tests/ \
  --cov=pipeline --cov-report=term-missing --cov-fail-under=90 -q
```

Expected: all pass, coverage >= 90%.

- [ ] **Step 2: Lint**

```bash
.venv/bin/ruff check pipeline/ .claude/skills/ --select F
```

Expected: no output.

- [ ] **Step 3: Confirm no leak into the parent repo**

```bash
git status --short
git -C ../../.. status --short
```

Expected: the worktree shows this branch's changes; the parent repo shows only the pre-existing modifications recorded in the session-start git status (`tektonc-data-collection`, untracked `docs/blog/`, `scratch/`, etc.) and nothing from this task.

- [ ] **Step 4: Trace acceptance criteria to tests**

For each of the six contract items in "Behavioral contract being implemented", name the test that proves it. Items 5 and 6 are scope exclusions — confirm no test asserts the excluded behavior.

- [ ] **Step 5: Push and open the PR**

```bash
git push -u origin worktree-issue-921-assemble-baseline-select
gh pr create --title "feat(assemble): resolve one baseline, selected by --baseline (#921)" --body-file <(...)
```

The PR body must: say `Closes #921`; summarize the three seams; call out that the baseline-package set per run changes from N to 1 (a behavior change for multi-baseline bundles); state the `params_hash` decision and the legacy-snapshot compat shim including its one hole (a pre-#921 run re-assembled with an explicit `--baseline` rebases without a drift refusal); list what the stale-reference sweep covered; and note the two declared scope exclusions from contract items 5 and 6.
