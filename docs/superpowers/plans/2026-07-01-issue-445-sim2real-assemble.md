# sim2real assemble Implementation Plan (Issue #445)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `sim2real assemble` — snapshot the assembly slice of `transfer.yaml` into `runs/<R>/`, resolve baseline+treatment scenarios via deep-merge, generate PipelineRuns; delete `pipeline/prepare.py` + `state_machine.py` + `context_builder.py` + legacy `assemble.py`; stub the `/sim2real-translate` skill; sweep docs and CI.

**Architecture:** New helper module `pipeline/lib/assemble_run.py` owns pure logic (load translation, filter algorithms, deep-merge, inject image tag / hf secret, write cluster YAMLs, generate PipelineRuns, write `run_metadata.json` + `manifest.assembly.yaml`). `pipeline/sim2real.py` gains an `assemble` subcommand that parses args, wires up `layout.set_experiment_root`, and calls the pure helper. The four legacy files disappear together; tests for them are deleted; `test_epp.py`'s two integration tests are re-plumbed to use `deep_merge` directly.

**Tech Stack:** Python 3.10+, PyYAML, `pipeline/lib/slicer.py` (translation_slice / assembly_slice), `pipeline/lib/values.py:deep_merge`, `pipeline/lib/tekton.py:make_pipelinerun_scenario`, `pipeline/lib/cluster_ops.py:read_cluster_config`, `pipeline/lib/layout.py`, `pipeline/lib/manifest.py:load_manifest`, `pipeline/lib/epp.py:inject_epp_image`, pytest.

## Global Constraints

- Base branch: `refactor/v2-step-1`. PR must target that base, not `main`.
- Command shape (design-locked): `sim2real assemble --translation HASH --cluster CLUSTER_ID --run RUN_NAME [--force]`.
- Slice via `pipeline/lib/slicer.py` — no ad-hoc key subsetting.
- Deep-merge via `pipeline/lib/values.py:deep_merge` unchanged from today.
- Schemas pinned (version-tagged with `version: 1`):
  - `runs/<R>/run_metadata.json` — `{version, run_name, translation_hash, cluster_id, params_hash, image_tag, assembled_at}` — see design §Schemas.
  - `runs/<R>/manifest.assembly.yaml` — verbatim serialization of `slicer.assembly_slice(manifest)`, prefixed with a comment header.
- `params_hash = sha256(manifest.assembly.yaml bytes)` — computed after writing.
- `image_tag` in metadata copied from `translation_output.json:image_ref` and injected into treatment scenarios.
- Framework defaults (`baselines/defaults/*.yaml` gated by `defaults.disable`) are preserved — treated as a pre-baseline layer per design §Commands step 3.
- Unregistered algorithms (present in `transfer.yaml:algorithms` but NOT in `translation_output.json:algorithms`) — WARN and SKIP (design §Failure modes wins over issue-body's "silently").
- Missing workload file → hard error. Missing translation dir → hard error. `runs/<R>/` present and `--force` absent → hard error with `--force` hint.
- No changes to `pipeline/deploy.py` — its `prepare.py` references are addressed by PR 3 of the epic.

---

## File Structure

### Create
- `pipeline/lib/assemble_run.py` — pure helper module. Exposes `assemble_run(...)` plus small helpers (`filter_algorithms`, `resolve_scenarios`, `write_manifest_assembly`, `write_run_metadata`, `generate_pipelineruns`). No argparse, no `print`/`stderr` output. Raises `AssembleError` on validation failure.
- `pipeline/tests/test_assemble_run.py` — unit tests for the pure helpers and one integration test that exercises `assemble_run(...)` end-to-end with a stub filesystem.

### Modify
- `pipeline/sim2real.py` — add `_cmd_assemble` and its argparse subparser; wire `layout.set_experiment_root` in `main()`.
- `pipeline/tests/test_epp.py` — replace two `assemble_scenarios(...)` call sites with inline `deep_merge(...)` fixture construction; drop the import.
- `.claude/skills/sim2real-translate/SKILL.md` — stub with a "skill-driven flow lands in step-2" error message (design §PR 2).
- `.github/workflows/test.yml` — no change if tests are auto-discovered under `pipeline/`; verify collection succeeds without the deleted test files.
- `CLAUDE.md` — remove references to `prepare.py`, the 6-phase state machine, and `state_machine.py`/`context_builder.py`/`assemble.py`. Add a stub note that `sim2real assemble` replaces prepare's Phase 4.
- `pipeline/README.md` — remove `## prepare.py` section; add `## Assemble a run` section; update the Pipeline library table (drop `state_machine.py`, `context_builder.py`, `assemble.py`); update workspace artifacts table (remove `.state.json`, `skill_input.json`, add `manifest.assembly.yaml`, and re-attribute `run_metadata.json`, `cluster/`, `generated/`, `translation_output.json`).

### Delete
- `pipeline/prepare.py`
- `pipeline/lib/state_machine.py`
- `pipeline/lib/context_builder.py`
- `pipeline/lib/assemble.py`
- `pipeline/tests/test_prepare.py`
- `pipeline/tests/test_state_machine.py`
- `pipeline/tests/test_context_builder.py`
- `pipeline/tests/test_assemble.py`

---

## Task Sequencing

Tasks run in order. Each task ends with pytest + ruff green on the touched paths. Task 1 lays the pure helper (TDD from red → green). Task 2 wires the CLI. Task 3 is the sweep of deletions (also fixes `test_epp.py` in the same commit so the tree stays green). Task 4 sweeps docs.

---

### Task 1: Pure `assemble_run` module + unit tests

**Files:**
- Create: `pipeline/lib/assemble_run.py`
- Test: `pipeline/tests/test_assemble_run.py`

**Interfaces:**

- Consumes:
  - `pipeline.lib.slicer.assembly_slice(manifest: dict) -> dict`
  - `pipeline.lib.values.deep_merge(a, b) -> dict`
  - `pipeline.lib.tekton.make_pipelinerun_scenario(phase, workload, run_name, namespace, pipeline_name, scenario_content, workspace_bindings, benchmark_git_commit, benchmark_git_repo_url, blis_git_commit, blis_git_repo_url, model, observe) -> dict`
  - `pipeline.lib.layout.translation_dir(hash) / translation_output_path(hash) / cluster_dir(cluster_id) / cluster_config_path(cluster_id) / runs_dir()`
  - `pipeline.lib.cluster_ops.read_cluster_config(cluster_id) -> dict`
  - `pipeline.lib.manifest.load_manifest(path) -> dict`
  - `pipeline.lib.epp.inject_epp_image(scenario, registry, repo_name, tag) -> bool` (used only when explicit image_tag injection needs the same shape — actual image injection is done by `_inject_image_tag` below, but `epp.inject_image_ref` is available if we want the same helper. For step-1 BYO we set `images.inferenceScheduler` from `image_ref` directly — see §Execute step 4.)

- Produces:
  - `class AssembleError(Exception)` — validation failures.
  - `def assemble_run(*, translation_hash: str, cluster_id: str, run_name: str, experiment_root: Path, manifest_path: Path, force: bool, now_iso: str) -> None` — top-level orchestrator.
  - Internal helpers exposed for tests: `filter_algorithms(manifest_algos, translated_names) -> tuple[list[dict], list[str]]` (returns kept + warned-skipped names), `load_defaults_overlay(defaults_dir, disable) -> dict`, `resolve_baseline(baseline_bundle_path, baseline_overlay_path, defaults_overlay) -> dict`, `resolve_treatment(baseline_resolved, treatment_bundle_diffs, algo_overlay_path) -> dict`, `inject_image_tag(scenario_dict, image_ref) -> None`, `write_manifest_assembly(run_dir, manifest, now_iso) -> Path`, `compute_params_hash(manifest_assembly_path) -> str`, `write_run_metadata(run_dir, meta) -> Path`, `generate_pipelineruns(run_dir, resolved_packages, workloads, run_name, cluster_config, pipeline_name, observe, model_name, submodule_shas, submodule_urls) -> None`.

- [ ] **Step 1.1: Write failing test — `filter_algorithms` keeps registered, warns skipped**

Add to `pipeline/tests/test_assemble_run.py`:

```python
"""Unit tests for pipeline/lib/assemble_run.py."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from pipeline.lib import assemble_run, layout


@pytest.fixture(autouse=True)
def _isolated_experiment_root(tmp_path):
    layout._EXPERIMENT_ROOT = tmp_path
    yield
    layout._EXPERIMENT_ROOT = None


class TestFilterAlgorithms:
    def test_keeps_registered_and_reports_skipped(self):
        manifest_algos = [
            {"name": "softreflective", "defaults": "baseline"},
            {"name": "constantceiling", "defaults": "baseline"},
        ]
        kept, skipped = assemble_run.filter_algorithms(
            manifest_algos, translated_names={"softreflective"}
        )
        assert [a["name"] for a in kept] == ["softreflective"]
        assert skipped == ["constantceiling"]

    def test_keeps_all_when_all_registered(self):
        manifest_algos = [{"name": "a", "defaults": "b"}]
        kept, skipped = assemble_run.filter_algorithms(manifest_algos, translated_names={"a"})
        assert kept == manifest_algos
        assert skipped == []

    def test_empty_when_none_registered(self):
        kept, skipped = assemble_run.filter_algorithms(
            [{"name": "a", "defaults": "b"}], translated_names=set()
        )
        assert kept == []
        assert skipped == ["a"]

    def test_empty_algorithms_short_circuits(self):
        assert assemble_run.filter_algorithms([], translated_names={"a"}) == ([], [])
```

- [ ] **Step 1.2: Run test — expect ImportError / attribute-missing**

Run: `python -m pytest pipeline/tests/test_assemble_run.py::TestFilterAlgorithms -v`
Expected: FAIL — module `pipeline.lib.assemble_run` does not exist.

- [ ] **Step 1.3: Create skeleton `assemble_run.py` with `filter_algorithms`**

Create `pipeline/lib/assemble_run.py`:

```python
"""Sim2real assemble command: pure logic behind `sim2real assemble`.

Reads a registered translation and an experiment repo's `transfer.yaml`,
snapshots the assembly-slice into `runs/<R>/manifest.assembly.yaml`,
deep-merges baseline + treatment scenarios (framework defaults → baseline
bundle → per-algorithm overlay), generates one PipelineRun per
(workload, package), and writes `run_metadata.json` with a stable
`params_hash` over the assembly-slice bytes.

Pure module: no argparse, no print. Callers surface errors via the
`AssembleError` exception.
"""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from pipeline.lib import cluster_ops, layout, slicer
from pipeline.lib.manifest import load_manifest, ManifestError
from pipeline.lib.tekton import make_pipelinerun_scenario
from pipeline.lib.values import deep_merge


class AssembleError(Exception):
    """Raised when assembly fails validation."""


def filter_algorithms(
    manifest_algos: list[dict],
    *,
    translated_names: set[str],
) -> tuple[list[dict], list[str]]:
    """Split ``manifest_algos`` by whether each name is in ``translated_names``.

    Returns ``(kept, skipped_names)`` where ``kept`` is a list of the
    algorithm dicts whose ``name`` appears in ``translated_names`` (order
    preserved) and ``skipped_names`` is a list of names present in the
    manifest but absent from ``translated_names``. Callers surface the
    skipped set as a warning; the design lets us prune unregistered
    algorithms without failing the run.
    """
    kept: list[dict] = []
    skipped: list[str] = []
    for algo in manifest_algos:
        name = algo.get("name")
        if name in translated_names:
            kept.append(algo)
        else:
            skipped.append(name)
    return kept, skipped
```

- [ ] **Step 1.4: Run test — expect PASS**

Run: `python -m pytest pipeline/tests/test_assemble_run.py::TestFilterAlgorithms -v`
Expected: 4 passed.

- [ ] **Step 1.5: Write failing test — framework defaults overlay**

Append to `pipeline/tests/test_assemble_run.py`:

```python
class TestLoadDefaultsOverlay:
    def test_merges_all_fragments_alphabetically(self, tmp_path):
        d = tmp_path / "defaults"
        d.mkdir()
        (d / "aaa.yaml").write_text(yaml.dump({"scenario": [{"name": "s", "a": 1}]}))
        (d / "bbb.yaml").write_text(yaml.dump({"scenario": [{"name": "s", "b": 2}]}))
        merged = assemble_run.load_defaults_overlay(d, disable=[])
        assert merged == {"scenario": [{"name": "s", "a": 1, "b": 2}]}

    def test_disable_skips_by_stem(self, tmp_path):
        d = tmp_path / "defaults"
        d.mkdir()
        (d / "keep.yaml").write_text(yaml.dump({"scenario": [{"name": "s", "k": 1}]}))
        (d / "drop.yaml").write_text(yaml.dump({"scenario": [{"name": "s", "d": 2}]}))
        merged = assemble_run.load_defaults_overlay(d, disable=["drop"])
        assert merged == {"scenario": [{"name": "s", "k": 1}]}

    def test_missing_dir_returns_empty(self, tmp_path):
        assert assemble_run.load_defaults_overlay(tmp_path / "nope", disable=[]) == {}

    def test_none_dir_returns_empty(self):
        assert assemble_run.load_defaults_overlay(None, disable=[]) == {}
```

- [ ] **Step 1.6: Implement `load_defaults_overlay`**

Append to `pipeline/lib/assemble_run.py`:

```python
def load_defaults_overlay(defaults_dir: Path | None, *, disable: list[str]) -> dict:
    """Merge framework-defaults YAML fragments into one overlay.

    Fragments live under ``defaults_dir`` (typically
    ``<experiment-root>/baselines/defaults/``). Their stems (filename
    without ``.yaml``) act as opt-out keys — any stem in ``disable`` is
    skipped. Returns ``{}`` when ``defaults_dir`` is None or missing.
    Fragments merge in filename-sorted order for determinism.
    """
    if defaults_dir is None or not defaults_dir.exists():
        return {}
    disable_set = set(disable or [])
    merged: dict = {}
    for fragment in sorted(defaults_dir.glob("*.yaml")):
        if fragment.stem in disable_set:
            continue
        try:
            data = yaml.safe_load(fragment.read_text()) or {}
        except yaml.YAMLError as exc:
            raise AssembleError(f"YAML parse error in {fragment}: {exc}") from exc
        merged = deep_merge(merged, data)
    return merged
```

- [ ] **Step 1.7: Run test — expect PASS**

Run: `python -m pytest pipeline/tests/test_assemble_run.py::TestLoadDefaultsOverlay -v`
Expected: 4 passed.

- [ ] **Step 1.8: Write failing test — image_tag injection**

Append to `pipeline/tests/test_assemble_run.py`:

```python
class TestInjectImageTag:
    def test_injects_repository_and_tag(self):
        scenario = {"scenario": [{"name": "s"}, {"name": "s2"}]}
        assemble_run.inject_image_tag(scenario, "ghcr.io/foo/bar:v1")
        for entry in scenario["scenario"]:
            img = entry["images"]["inferenceScheduler"]
            assert img == {"repository": "ghcr.io/foo/bar", "tag": "v1", "pullPolicy": "Always"}

    def test_digest_ref_splits_before_at(self):
        scenario = {"scenario": [{"name": "s"}]}
        digest_ref = "ghcr.io/foo/bar@sha256:" + "a" * 64
        assemble_run.inject_image_tag(scenario, digest_ref)
        img = scenario["scenario"][0]["images"]["inferenceScheduler"]
        # For digest refs we keep the ref verbatim in `repository` and leave `tag` empty.
        # Design step 4 says "inject image_tag from translation_output.json:image_ref";
        # tag semantics for digest refs aren't spec'd — we opt for repository=digest_ref, tag="".
        assert img["repository"] == digest_ref
        assert img["tag"] == ""

    def test_no_scenario_entries_raises(self):
        with pytest.raises(assemble_run.AssembleError):
            assemble_run.inject_image_tag({"scenario": []}, "ghcr.io/foo/bar:v1")

    def test_missing_scenario_key_raises(self):
        with pytest.raises(assemble_run.AssembleError):
            assemble_run.inject_image_tag({}, "ghcr.io/foo/bar:v1")

    def test_overwrites_existing_inference_scheduler(self):
        scenario = {"scenario": [{"name": "s", "images": {"inferenceScheduler": {"repository": "old", "tag": "old"}}}]}
        assemble_run.inject_image_tag(scenario, "ghcr.io/foo/bar:v1")
        img = scenario["scenario"][0]["images"]["inferenceScheduler"]
        assert img["repository"] == "ghcr.io/foo/bar"
        assert img["tag"] == "v1"
```

- [ ] **Step 1.9: Implement `inject_image_tag`**

Append to `pipeline/lib/assemble_run.py`:

```python
def inject_image_tag(scenario_dict: dict, image_ref: str) -> None:
    """Inject BYO image into every scenario entry's ``images.inferenceScheduler``.

    Splits a ``registry/repo:tag`` ref on the last colon into ``repository``
    and ``tag``; digest refs (``registry/repo@sha256:...``) keep the whole
    ref as ``repository`` with ``tag=""``. ``pullPolicy`` is always set to
    ``Always`` — mirrors the semantics of
    ``pipeline/lib/epp.py:inject_epp_image`` so downstream benchmark
    charts see a familiar shape.
    """
    scenario_list = scenario_dict.get("scenario")
    if not scenario_list:
        raise AssembleError(
            "cannot inject image_tag: scenario dict has no 'scenario' entries"
        )
    if "@sha256:" in image_ref:
        repository, tag = image_ref, ""
    else:
        # rsplit(":", 1) safely handles registry:port/repo:tag — the port
        # colon precedes the last slash, and rsplit takes only the tail.
        if ":" in image_ref.rsplit("/", 1)[-1]:
            repository, tag = image_ref.rsplit(":", 1)
        else:
            repository, tag = image_ref, ""
    for entry in scenario_list:
        entry["images"] = entry.get("images") or {}
        entry["images"]["inferenceScheduler"] = {
            "repository": repository,
            "tag": tag,
            "pullPolicy": "Always",
        }
```

- [ ] **Step 1.10: Run test — expect PASS**

Run: `python -m pytest pipeline/tests/test_assemble_run.py::TestInjectImageTag -v`
Expected: 5 passed.

- [ ] **Step 1.11: Write failing test — write_manifest_assembly and compute_params_hash**

Append to `pipeline/tests/test_assemble_run.py`:

```python
class TestWriteManifestAssembly:
    def test_writes_yaml_snapshot_of_assembly_slice(self, tmp_path):
        manifest = {
            "kind": "sim2real-transfer",
            "version": 3,
            "scenario": "test",
            "component": {"repo": "acme/foo"},
            "context": {"text": "", "files": []},
            "baselines": [{"name": "baseline", "scenario": "baselines/base.yaml"}],
            "algorithms": [{"name": "sr", "source": "algo/sr.py", "defaults": "baseline"}],
            "workloads": ["workloads/w1.yaml"],
            "defaults": {"disable": []},
        }
        run_dir = tmp_path / "runs" / "trial-1"
        run_dir.mkdir(parents=True)
        p = assemble_run.write_manifest_assembly(
            run_dir, manifest, now_iso="2026-07-01T14:05:00Z"
        )
        assert p == run_dir / "manifest.assembly.yaml"
        text = p.read_text()
        assert "generated by sim2real assemble at 2026-07-01T14:05:00Z" in text
        # Reparse — the assembly-slice fields must round-trip.
        parsed = yaml.safe_load(text)
        assert parsed["workloads"] == ["workloads/w1.yaml"]
        assert parsed["baselines"][0]["name"] == "baseline"
        # Slicer strips scenario/component/context/algorithms-source.
        assert "scenario" not in parsed
        assert "component" not in parsed
        assert parsed["algorithms"] == [{"name": "sr", "defaults": "baseline"}]

    def test_params_hash_is_sha256_of_bytes(self, tmp_path):
        p = tmp_path / "manifest.assembly.yaml"
        p.write_bytes(b"hello\n")
        assert assemble_run.compute_params_hash(p) == hashlib.sha256(b"hello\n").hexdigest()

    def test_slicer_round_trip(self, tmp_path):
        # An assembly-slice written to disk and re-parsed must equal the slicer output.
        manifest = {
            "kind": "sim2real-transfer",
            "version": 3,
            "scenario": "test",
            "workloads": ["w1.yaml", "w2.yaml"],
            "baselines": [{"name": "baseline", "scenario": "b.yaml"}],
            "algorithms": [{"name": "sr", "source": "s.py", "defaults": "baseline"}],
            "defaults": {"disable": []},
        }
        run_dir = tmp_path / "runs" / "trial-1"
        run_dir.mkdir(parents=True)
        p = assemble_run.write_manifest_assembly(
            run_dir, manifest, now_iso="2026-07-01T14:05:00Z"
        )
        reparsed = yaml.safe_load(p.read_text())
        expected = slicer.assembly_slice(manifest)
        assert reparsed == expected
```

- [ ] **Step 1.12: Implement `write_manifest_assembly` + `compute_params_hash` + `write_run_metadata`**

Append to `pipeline/lib/assemble_run.py`:

```python
def write_manifest_assembly(run_dir: Path, manifest: dict, *, now_iso: str) -> Path:
    """Serialize ``slicer.assembly_slice(manifest)`` to ``runs/<R>/manifest.assembly.yaml``.

    Prepends a one-line comment header naming the tool and timestamp.
    Returns the written path.
    """
    slice_ = slicer.assembly_slice(manifest)
    body = yaml.dump(slice_, default_flow_style=False, allow_unicode=True, sort_keys=False)
    text = f"# generated by sim2real assemble at {now_iso}; do not edit\n" + body
    out = run_dir / "manifest.assembly.yaml"
    out.write_text(text)
    return out


def compute_params_hash(manifest_assembly_path: Path) -> str:
    """SHA-256 over the raw bytes of ``manifest.assembly.yaml``."""
    return hashlib.sha256(manifest_assembly_path.read_bytes()).hexdigest()


def write_run_metadata(run_dir: Path, meta: dict) -> Path:
    """Write ``runs/<R>/run_metadata.json`` from ``meta`` (v1 schema).

    Caller supplies all fields — this function only serializes. Deterministic
    key order (``sort_keys=True``) so re-runs against unchanged inputs
    produce byte-identical files.
    """
    out = run_dir / "run_metadata.json"
    out.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")
    return out
```

- [ ] **Step 1.13: Run tests — expect PASS**

Run: `python -m pytest pipeline/tests/test_assemble_run.py -v`
Expected: All prior tests + the 3 new ones pass.

- [ ] **Step 1.14: Write failing test — resolve_baseline + resolve_treatment**

Append to `pipeline/tests/test_assemble_run.py`:

```python
class TestResolveScenarios:
    def _write(self, path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.dump(data, sort_keys=False))

    def test_baseline_merge_framework_then_bundle_then_overlay(self, tmp_path):
        bundle = {"scenario": [{"name": "s", "a": 1, "b": 1}]}
        overlay = {"scenario": [{"name": "s", "b": 2, "c": 2}]}
        framework = {"scenario": [{"name": "s", "a": 0, "d": 4}]}
        bundle_path = tmp_path / "baseline.yaml"
        overlay_path = tmp_path / "baseline_overlay.yaml"
        self._write(bundle_path, bundle)
        self._write(overlay_path, overlay)
        resolved = assemble_run.resolve_baseline(
            bundle_path=bundle_path,
            overlay_path=overlay_path,
            framework_defaults=framework,
        )
        # Precedence: framework < bundle < overlay
        assert resolved == {"scenario": [{"name": "s", "a": 1, "b": 2, "c": 2, "d": 4}]}

    def test_treatment_merge_baseline_then_diffs_then_overlay(self, tmp_path):
        baseline_resolved = {"scenario": [{"name": "s", "a": 1, "b": 1}]}
        diffs = {"scenario": [{"name": "s", "b": 2, "c": 2}]}
        overlay = {"scenario": [{"name": "s", "c": 3, "d": 3}]}
        diffs_path = tmp_path / "treatment.yaml"
        overlay_path = tmp_path / "sr" / "sr_config.yaml"
        self._write(diffs_path, diffs)
        self._write(overlay_path, overlay)
        resolved = assemble_run.resolve_treatment(
            baseline_resolved=baseline_resolved,
            diffs_path=diffs_path,
            overlay_path=overlay_path,
        )
        assert resolved == {"scenario": [{"name": "s", "a": 1, "b": 2, "c": 3, "d": 3}]}

    def test_missing_overlay_path_is_noop(self, tmp_path):
        baseline_resolved = {"scenario": [{"name": "s", "a": 1}]}
        resolved = assemble_run.resolve_treatment(
            baseline_resolved=baseline_resolved,
            diffs_path=None,
            overlay_path=tmp_path / "missing.yaml",
        )
        assert resolved == baseline_resolved

    def test_missing_bundle_raises(self, tmp_path):
        with pytest.raises(assemble_run.AssembleError):
            assemble_run.resolve_baseline(
                bundle_path=tmp_path / "nope.yaml",
                overlay_path=None,
                framework_defaults={},
            )
```

- [ ] **Step 1.15: Implement `resolve_baseline` and `resolve_treatment`**

Append to `pipeline/lib/assemble_run.py`:

```python
def _load_yaml(path: Path) -> dict:
    """Load a YAML file into a dict, raising AssembleError on I/O or parse error."""
    try:
        text = path.read_text()
    except OSError as exc:
        raise AssembleError(f"cannot read {path}: {exc}") from exc
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise AssembleError(f"YAML parse error in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise AssembleError(f"expected YAML mapping at {path}, got {type(data).__name__}")
    return data


def resolve_baseline(
    *,
    bundle_path: Path,
    overlay_path: Path | None,
    framework_defaults: dict,
) -> dict:
    """Return ``deep_merge(framework_defaults, bundle, overlay)`` for a baseline.

    ``framework_defaults`` may be ``{}`` (experiment has no
    ``baselines/defaults/`` directory). ``overlay_path`` may be ``None`` or
    point at a non-existent file (BYO baseline without a baseline overlay).
    Bundle is required — a missing bundle raises AssembleError.
    """
    if not bundle_path.exists():
        raise AssembleError(f"baseline scenario not found: {bundle_path}")
    bundle = _load_yaml(bundle_path)
    overlay = (
        _load_yaml(overlay_path)
        if overlay_path is not None and overlay_path.exists()
        else {}
    )
    resolved = deep_merge(copy.deepcopy(framework_defaults), bundle)
    resolved = deep_merge(resolved, overlay)
    return resolved


def resolve_treatment(
    *,
    baseline_resolved: dict,
    diffs_path: Path | None,
    overlay_path: Path | None,
) -> dict:
    """Return ``deep_merge(baseline_resolved, treatment_diffs, algo_overlay)``.

    Either or both of ``diffs_path`` / ``overlay_path`` may be ``None`` or
    point at non-existent files — the corresponding layer is treated as
    empty. Baseline is required (starts from an already-resolved dict).
    """
    diffs = (
        _load_yaml(diffs_path)
        if diffs_path is not None and diffs_path.exists()
        else {}
    )
    overlay = (
        _load_yaml(overlay_path)
        if overlay_path is not None and overlay_path.exists()
        else {}
    )
    resolved = deep_merge(copy.deepcopy(baseline_resolved), diffs)
    resolved = deep_merge(resolved, overlay)
    return resolved
```

- [ ] **Step 1.16: Run tests — expect PASS**

Run: `python -m pytest pipeline/tests/test_assemble_run.py -v`
Expected: All prior tests + the 4 new ones pass.

- [ ] **Step 1.17: Write failing test — hf_secret injection**

Append to `pipeline/tests/test_assemble_run.py`:

```python
class TestInjectHfSecret:
    def test_sets_secret_name_on_each_entry(self):
        s = {"scenario": [{"name": "a"}, {"name": "b"}]}
        assemble_run.inject_hf_secret_name(s, "hf-secret")
        for entry in s["scenario"]:
            assert entry["huggingface"]["secretName"] == "hf-secret"

    def test_preserves_explicit_secret_name(self):
        s = {"scenario": [{"name": "a", "huggingface": {"secretName": "explicit"}}]}
        assemble_run.inject_hf_secret_name(s, "hf-secret")
        assert s["scenario"][0]["huggingface"]["secretName"] == "explicit"

    def test_no_scenario_entries_raises(self):
        with pytest.raises(assemble_run.AssembleError):
            assemble_run.inject_hf_secret_name({"scenario": []}, "hf-secret")
```

- [ ] **Step 1.18: Implement `inject_hf_secret_name`**

Append to `pipeline/lib/assemble_run.py`:

```python
def inject_hf_secret_name(scenario_dict: dict, hf_secret_name: str) -> None:
    """Set ``huggingface.secretName`` on every scenario entry.

    Does not overwrite an explicitly set secretName (setdefault semantics).
    Raises AssembleError when the scenario dict has no ``scenario`` entries.
    """
    scenario_list = scenario_dict.get("scenario")
    if not scenario_list:
        raise AssembleError(
            "cannot inject hf secret: scenario dict has no 'scenario' entries"
        )
    for entry in scenario_list:
        hf = entry.setdefault("huggingface", {})
        hf.setdefault("secretName", hf_secret_name)
```

- [ ] **Step 1.19: Run tests — expect PASS**

Run: `python -m pytest pipeline/tests/test_assemble_run.py -v`
Expected: all tests pass.

- [ ] **Step 1.20: Write failing test — `generate_pipelineruns` shape**

Append to `pipeline/tests/test_assemble_run.py`:

```python
class TestGeneratePipelineruns:
    def test_one_pipelinerun_per_workload_x_package(self, tmp_path):
        run_dir = tmp_path / "runs" / "trial-1"
        cluster_dir = run_dir / "cluster"
        cluster_dir.mkdir(parents=True)
        packages = [
            ("baseline", {"scenario": [{"name": "s", "model": {"name": "M"}}]}),
            ("sr", {"scenario": [{"name": "s", "model": {"name": "M"}}]}),
        ]
        workloads = [
            {"name": "wl_a", "num_requests": 10},
            {"name": "wl_b", "num_requests": 20},
        ]
        cluster_config = {"namespaces": ["sim2real-slot-0"], "workspaces": {}}
        assemble_run.generate_pipelineruns(
            run_dir=run_dir,
            packages=packages,
            workloads=workloads,
            run_name="trial-1",
            cluster_config=cluster_config,
            pipeline_name="sim2real",
            observe={},
            model_name="M",
            submodule_shas={"llm-d-benchmark": "abc", "inference-sim": "def"},
            submodule_urls={"llm-d-benchmark": "git@e/b", "inference-sim": "git@e/i"},
        )
        yamls = sorted(p.name for p in cluster_dir.glob("pipelinerun-*.yaml"))
        assert yamls == [
            "pipelinerun-wl-a-baseline.yaml",
            "pipelinerun-wl-a-sr.yaml",
            "pipelinerun-wl-b-baseline.yaml",
            "pipelinerun-wl-b-sr.yaml",
        ]
        # Sanity-check one file — the scenarioContent must contain the
        # serialized package YAML.
        pr = yaml.safe_load((cluster_dir / "pipelinerun-wl-a-sr.yaml").read_text())
        assert pr["kind"] == "PipelineRun"
        params = {p["name"]: p["value"] for p in pr["spec"]["params"]}
        assert params["phase"] == "sr"
        assert "model: {name: M}" in params["scenarioContent"] or "name: M" in params["scenarioContent"]
        assert params["workloadName"] == "wl_a"
```

- [ ] **Step 1.21: Implement `generate_pipelineruns` and `write_resolved_scenarios`**

Append to `pipeline/lib/assemble_run.py`:

```python
def write_resolved_scenarios(run_dir: Path, packages: list[tuple[str, dict]]) -> Path:
    """Write each ``(name, resolved_dict)`` pair to ``runs/<R>/cluster/<name>.yaml``.

    Returns the cluster directory path. Creates it if absent.
    """
    cluster_dir_ = run_dir / "cluster"
    cluster_dir_.mkdir(parents=True, exist_ok=True)
    for name, resolved in packages:
        (cluster_dir_ / f"{name}.yaml").write_text(
            yaml.dump(resolved, default_flow_style=False, allow_unicode=True)
        )
    return cluster_dir_


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
) -> None:
    """Emit one PipelineRun YAML per (workload, package) pair under ``cluster/``.

    Filename shape: ``pipelinerun-<workload-safe>-<package>.yaml``, where
    ``<workload-safe>`` is the workload name with ``_`` replaced by ``-``.
    Matches the shape that ``deploy.py run``'s pair-discovery expects.
    """
    cluster_dir_ = run_dir / "cluster"
    cluster_dir_.mkdir(parents=True, exist_ok=True)

    namespaces = cluster_config.get("namespaces") or []
    namespace = namespaces[0] if namespaces else "default"
    ws_bindings = cluster_config.get("workspaces") or {}

    for pkg_name, resolved in packages:
        scenario_content = yaml.dump(resolved, default_flow_style=False, allow_unicode=True)
        for wl in workloads:
            wl_name = wl.get("name", wl.get("workload_name", "unknown"))
            safe_wl = wl_name.replace("_", "-")
            pr = make_pipelinerun_scenario(
                phase=pkg_name,
                workload=wl,
                run_name=run_name,
                namespace=namespace,
                pipeline_name=pipeline_name,
                scenario_content=scenario_content,
                workspace_bindings=ws_bindings if ws_bindings else None,
                benchmark_git_commit=submodule_shas.get("llm-d-benchmark", ""),
                benchmark_git_repo_url=submodule_urls.get("llm-d-benchmark", ""),
                blis_git_commit=submodule_shas.get("inference-sim", ""),
                blis_git_repo_url=submodule_urls.get("inference-sim", ""),
                model=model_name,
                observe=observe,
            )
            (cluster_dir_ / f"pipelinerun-{safe_wl}-{pkg_name}.yaml").write_text(
                yaml.dump(pr, default_flow_style=False, allow_unicode=True)
            )
```

- [ ] **Step 1.22: Run tests — expect PASS**

Run: `python -m pytest pipeline/tests/test_assemble_run.py -v`
Expected: all tests pass.

- [ ] **Step 1.23: Write failing tests — top-level `assemble_run` orchestrator**

Append to `pipeline/tests/test_assemble_run.py`. These tests build a full experiment repo fixture on disk and exercise `assemble_run(...)` end-to-end.

```python
def _write_yaml(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data, sort_keys=False))


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def _make_experiment(tmp_path: Path, *, algo_names_registered: list[str],
                    algo_names_manifest: list[str], image_ref: str = "ghcr.io/foo/bar:v1") -> dict:
    """Build a minimal experiment on disk and register a translation for the
    given algorithms. Returns useful paths + the translation hash.
    """
    exp_root = tmp_path / "exp"
    workspace = exp_root / "workspace"
    layout._EXPERIMENT_ROOT = exp_root

    # Cluster config
    cluster_id = "ocp-east"
    cluster_dir_ = workspace / "clusters" / cluster_id
    cluster_dir_.mkdir(parents=True)
    _write_json(
        cluster_dir_ / "cluster_config.json",
        {
            "cluster_id": cluster_id,
            "namespaces": ["sim2real-slot-0"],
            "secret_names": {"hf_token": "hf-secret"},
            "workspaces": {},
        },
    )

    # transfer.yaml
    manifest = {
        "kind": "sim2real-transfer",
        "version": 3,
        "scenario": "test-scenario",
        "component": {"repo": "acme/llm-d-inference-scheduler", "kind": "gaie"},
        "context": {"text": "", "files": []},
        "baselines": [{"name": "baseline", "scenario": "baselines/base.yaml"}],
        "algorithms": [
            {"name": n, "source": f"algo/{n}.py", "defaults": "baseline"}
            for n in algo_names_manifest
        ],
        "workloads": ["workloads/w1.yaml"],
        "defaults": {"disable": []},
    }
    _write_yaml(exp_root / "transfer.yaml", manifest)
    _write_yaml(
        exp_root / "baselines" / "base.yaml",
        {"scenario": [{"name": "test-scenario", "model": {"name": "M"}}]},
    )
    _write_yaml(exp_root / "workloads" / "w1.yaml", {"name": "wl_a", "num_requests": 10})
    # algorithm source files (existence-only)
    for n in algo_names_manifest:
        (exp_root / "algo").mkdir(exist_ok=True)
        (exp_root / "algo" / f"{n}.py").write_text("# stub\n")

    # Register a translation for algo_names_registered
    thash = "a" * 64
    tdir = workspace / "translations" / thash
    generated = tdir / "generated"
    generated.mkdir(parents=True)
    _write_json(
        tdir / "translation_output.json",
        {
            "version": 1,
            "translation_hash": thash,
            "source": "byo",
            "algorithms": [{"name": n} for n in algo_names_registered],
            "image_ref": image_ref,
            "created_at": "2026-07-01T14:00:00Z",
        },
    )
    _write_json(
        tdir / "registered.json",
        {
            "version": 1,
            "image_ref": image_ref,
            "image_digest": None,
            "source": "byo",
            "registered_at": "2026-07-01T14:00:00Z",
        },
    )
    for n in algo_names_registered:
        (generated / n).mkdir(parents=True, exist_ok=True)
        _write_yaml(
            generated / n / f"{n}_config.yaml",
            {"scenario": [{"name": "test-scenario", "inferenceExtension": {"pluginsConfigFile": f"{n}.yaml"}}]},
        )
    return {
        "exp_root": exp_root,
        "cluster_id": cluster_id,
        "translation_hash": thash,
        "manifest_path": exp_root / "transfer.yaml",
    }


class TestAssembleRun:
    def test_end_to_end_produces_expected_files(self, tmp_path):
        fx = _make_experiment(
            tmp_path,
            algo_names_registered=["sr"],
            algo_names_manifest=["sr"],
        )
        assemble_run.assemble_run(
            translation_hash=fx["translation_hash"],
            cluster_id=fx["cluster_id"],
            run_name="trial-1",
            experiment_root=fx["exp_root"],
            manifest_path=fx["manifest_path"],
            force=False,
            now_iso="2026-07-01T14:05:00Z",
        )
        run_dir = fx["exp_root"] / "workspace" / "runs" / "trial-1"
        assert (run_dir / "manifest.assembly.yaml").exists()
        assert (run_dir / "run_metadata.json").exists()
        assert (run_dir / "cluster" / "baseline.yaml").exists()
        assert (run_dir / "cluster" / "sr.yaml").exists()
        assert (run_dir / "cluster" / "pipelinerun-w1-baseline.yaml").exists()
        assert (run_dir / "cluster" / "pipelinerun-w1-sr.yaml").exists()

        meta = json.loads((run_dir / "run_metadata.json").read_text())
        assert meta["version"] == 1
        assert meta["run_name"] == "trial-1"
        assert meta["translation_hash"] == fx["translation_hash"]
        assert meta["cluster_id"] == fx["cluster_id"]
        assert meta["image_tag"] == "ghcr.io/foo/bar:v1"
        assert len(meta["params_hash"]) == 64
        assert meta["assembled_at"] == "2026-07-01T14:05:00Z"

    def test_filters_unregistered_algorithms(self, tmp_path):
        fx = _make_experiment(
            tmp_path,
            algo_names_registered=["sr"],
            algo_names_manifest=["sr", "constantceiling"],
        )
        assemble_run.assemble_run(
            translation_hash=fx["translation_hash"],
            cluster_id=fx["cluster_id"],
            run_name="trial-1",
            experiment_root=fx["exp_root"],
            manifest_path=fx["manifest_path"],
            force=False,
            now_iso="2026-07-01T14:05:00Z",
        )
        run_dir = fx["exp_root"] / "workspace" / "runs" / "trial-1"
        # sr is registered → treatment YAML present.
        assert (run_dir / "cluster" / "sr.yaml").exists()
        # constantceiling is manifest-only → no treatment YAML.
        assert not (run_dir / "cluster" / "constantceiling.yaml").exists()

    def test_treatment_scenario_carries_image_tag(self, tmp_path):
        fx = _make_experiment(
            tmp_path,
            algo_names_registered=["sr"],
            algo_names_manifest=["sr"],
        )
        assemble_run.assemble_run(
            translation_hash=fx["translation_hash"],
            cluster_id=fx["cluster_id"],
            run_name="trial-1",
            experiment_root=fx["exp_root"],
            manifest_path=fx["manifest_path"],
            force=False,
            now_iso="2026-07-01T14:05:00Z",
        )
        sr_yaml = yaml.safe_load(
            (fx["exp_root"] / "workspace" / "runs" / "trial-1" / "cluster" / "sr.yaml").read_text()
        )
        img = sr_yaml["scenario"][0]["images"]["inferenceScheduler"]
        assert img["repository"] == "ghcr.io/foo/bar"
        assert img["tag"] == "v1"
        # Baseline scenario does NOT carry the injected image_tag.
        baseline_yaml = yaml.safe_load(
            (fx["exp_root"] / "workspace" / "runs" / "trial-1" / "cluster" / "baseline.yaml").read_text()
        )
        assert "inferenceScheduler" not in (baseline_yaml["scenario"][0].get("images") or {})

    def test_params_hash_matches_manifest_assembly_bytes(self, tmp_path):
        fx = _make_experiment(
            tmp_path,
            algo_names_registered=["sr"],
            algo_names_manifest=["sr"],
        )
        assemble_run.assemble_run(
            translation_hash=fx["translation_hash"],
            cluster_id=fx["cluster_id"],
            run_name="trial-1",
            experiment_root=fx["exp_root"],
            manifest_path=fx["manifest_path"],
            force=False,
            now_iso="2026-07-01T14:05:00Z",
        )
        run_dir = fx["exp_root"] / "workspace" / "runs" / "trial-1"
        expected = hashlib.sha256((run_dir / "manifest.assembly.yaml").read_bytes()).hexdigest()
        meta = json.loads((run_dir / "run_metadata.json").read_text())
        assert meta["params_hash"] == expected

    def test_refuses_existing_run_without_force(self, tmp_path):
        fx = _make_experiment(
            tmp_path,
            algo_names_registered=["sr"],
            algo_names_manifest=["sr"],
        )
        run_dir = fx["exp_root"] / "workspace" / "runs" / "trial-1"
        run_dir.mkdir(parents=True)
        (run_dir / "sentinel").write_text("leftover")
        with pytest.raises(assemble_run.AssembleError, match="--force"):
            assemble_run.assemble_run(
                translation_hash=fx["translation_hash"],
                cluster_id=fx["cluster_id"],
                run_name="trial-1",
                experiment_root=fx["exp_root"],
                manifest_path=fx["manifest_path"],
                force=False,
                now_iso="2026-07-01T14:05:00Z",
            )
        # Sentinel is untouched (we bailed before writing).
        assert (run_dir / "sentinel").read_text() == "leftover"

    def test_force_overwrites_existing_run(self, tmp_path):
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
            cluster_id=fx["cluster_id"],
            run_name="trial-1",
            experiment_root=fx["exp_root"],
            manifest_path=fx["manifest_path"],
            force=True,
            now_iso="2026-07-01T14:05:00Z",
        )
        assert not (run_dir / "sentinel").exists()
        assert (run_dir / "manifest.assembly.yaml").exists()

    def test_missing_translation_dir_errors(self, tmp_path):
        fx = _make_experiment(
            tmp_path,
            algo_names_registered=["sr"],
            algo_names_manifest=["sr"],
        )
        with pytest.raises(assemble_run.AssembleError, match="translation"):
            assemble_run.assemble_run(
                translation_hash="0" * 64,
                cluster_id=fx["cluster_id"],
                run_name="trial-1",
                experiment_root=fx["exp_root"],
                manifest_path=fx["manifest_path"],
                force=False,
                now_iso="2026-07-01T14:05:00Z",
            )

    def test_missing_cluster_config_errors(self, tmp_path):
        fx = _make_experiment(
            tmp_path,
            algo_names_registered=["sr"],
            algo_names_manifest=["sr"],
        )
        with pytest.raises(assemble_run.AssembleError, match="cluster"):
            assemble_run.assemble_run(
                translation_hash=fx["translation_hash"],
                cluster_id="nonexistent-cluster",
                run_name="trial-1",
                experiment_root=fx["exp_root"],
                manifest_path=fx["manifest_path"],
                force=False,
                now_iso="2026-07-01T14:05:00Z",
            )

    def test_missing_workload_file_errors(self, tmp_path):
        fx = _make_experiment(
            tmp_path,
            algo_names_registered=["sr"],
            algo_names_manifest=["sr"],
        )
        # Remove the workload file.
        (fx["exp_root"] / "workloads" / "w1.yaml").unlink()
        with pytest.raises(assemble_run.AssembleError, match="workload"):
            assemble_run.assemble_run(
                translation_hash=fx["translation_hash"],
                cluster_id=fx["cluster_id"],
                run_name="trial-1",
                experiment_root=fx["exp_root"],
                manifest_path=fx["manifest_path"],
                force=False,
                now_iso="2026-07-01T14:05:00Z",
            )
```

- [ ] **Step 1.24: Implement the top-level `assemble_run` orchestrator**

Append to `pipeline/lib/assemble_run.py`:

```python
def _load_workload(exp_root: Path, wl_path_str: str) -> dict:
    """Load a workload YAML relative to the experiment root."""
    wl_path = exp_root / wl_path_str
    if not wl_path.exists():
        raise AssembleError(f"workload file not found: {wl_path}")
    try:
        data = yaml.safe_load(wl_path.read_text())
    except yaml.YAMLError as exc:
        raise AssembleError(f"invalid YAML in workload {wl_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise AssembleError(f"workload {wl_path} is not a YAML mapping")
    if "name" not in data and "workload_name" not in data:
        data["workload_name"] = Path(wl_path_str).stem
    return data


def _resolve_scenario_path(exp_root: Path, scenario_ref: str | None, fallback_name: str) -> Path | None:
    """Return the experiment-root-relative path for a scenario reference.

    ``scenario_ref`` is what the manifest recorded — may be a path, ``null``,
    or absent. ``fallback_name`` is the top-level filename to try when the
    manifest omits the reference (matches today's ``prepare.py`` behavior).
    Returns ``None`` when neither exists so callers can treat that layer as
    empty.
    """
    if scenario_ref:
        return exp_root / scenario_ref
    fallback = exp_root / fallback_name
    return fallback if fallback.exists() else None


def _clean_run_dir(run_dir: Path) -> None:
    """Recursively delete the contents of ``run_dir``, then remove it.

    Used by ``--force`` to guarantee a clean starting state before we
    re-materialize the run. Idempotent — no-op when the directory is
    absent.
    """
    import shutil

    if run_dir.exists():
        shutil.rmtree(run_dir)


def assemble_run(
    *,
    translation_hash: str,
    cluster_id: str,
    run_name: str,
    experiment_root: Path,
    manifest_path: Path,
    force: bool,
    now_iso: str,
) -> None:
    """Materialize ``workspace/runs/<run_name>/`` per the design.

    Steps (per design §Commands → sim2real assemble):
      1. Validate: translation dir + cluster_config exist; run_dir absent or --force.
      2. Load manifest; filter algorithms to those in translation_output.json.
      3. Snapshot assembly-slice → manifest.assembly.yaml; compute params_hash.
      4. Resolve baseline (framework_defaults → bundle → baseline_overlay) and
         each treatment (baseline_resolved → treatment bundle diffs → per-algo overlay).
      5. Inject image_tag into treatment scenarios; inject huggingface.secretName
         into all scenarios.
      6. Write cluster/{package}.yaml files.
      7. Generate cluster/pipelinerun-*.yaml files.
      8. Write run_metadata.json.

    Raises AssembleError on any validation failure. Never partially writes:
    validation happens before ``run_dir`` is (re)created.
    """
    layout.set_experiment_root(experiment_root)

    # 1. Validation --------------------------------------------------------
    tdir = layout.translation_dir(translation_hash)
    tout_path = layout.translation_output_path(translation_hash)
    if not tdir.exists() or not tout_path.exists():
        raise AssembleError(
            f"translation directory not found: {tdir}. "
            f"Register a translation with `sim2real translation register` first."
        )

    cluster_config = cluster_ops.read_cluster_config(cluster_id)
    if not cluster_config:
        raise AssembleError(
            f"cluster config not found for '{cluster_id}': "
            f"{layout.cluster_config_path(cluster_id)}"
        )

    run_dir = layout.runs_dir() / run_name
    if run_dir.exists():
        if not force:
            raise AssembleError(
                f"run directory already exists: {run_dir} — pass --force to overwrite"
            )
        _clean_run_dir(run_dir)

    # 2. Load manifest + translation index --------------------------------
    try:
        manifest = load_manifest(manifest_path)
    except ManifestError as exc:
        raise AssembleError(f"cannot load manifest {manifest_path}: {exc}") from exc

    try:
        tout = json.loads(tout_path.read_text())
    except json.JSONDecodeError as exc:
        raise AssembleError(
            f"translation_output.json is not valid JSON: {tout_path}: {exc}"
        ) from exc

    translated_names = {a.get("name") for a in tout.get("algorithms", [])}
    image_ref = tout.get("image_ref")
    if not image_ref:
        raise AssembleError(
            f"translation_output.json missing image_ref: {tout_path}"
        )

    kept_algos, skipped_algo_names = filter_algorithms(
        manifest.get("algorithms", []) or [],
        translated_names=translated_names,
    )

    # 3. Snapshot assembly slice + params_hash ----------------------------
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest_assembly_path = write_manifest_assembly(run_dir, manifest, now_iso=now_iso)
    params_hash = compute_params_hash(manifest_assembly_path)

    # 4. Resolve scenarios ------------------------------------------------
    exp_root = layout.experiment_root()
    defaults_dir = exp_root / "baselines" / "defaults"
    framework_defaults = load_defaults_overlay(
        defaults_dir if defaults_dir.exists() else None,
        disable=(manifest.get("defaults") or {}).get("disable") or [],
    )
    generated_root = tdir / "generated"
    baseline_overlay_path = generated_root / "baseline_config.yaml"

    packages: list[tuple[str, dict]] = []
    resolved_baselines: dict[str, dict] = {}
    for bl in manifest.get("baselines", []):
        bl_name = bl["name"]
        bundle_path = _resolve_scenario_path(exp_root, bl.get("scenario"), "baseline.yaml")
        if bundle_path is None:
            raise AssembleError(f"baseline '{bl_name}' has no scenario file")
        resolved = resolve_baseline(
            bundle_path=bundle_path,
            overlay_path=baseline_overlay_path,
            framework_defaults=framework_defaults,
        )
        resolved_baselines[bl_name] = resolved
        packages.append((bl_name, resolved))

    for algo in kept_algos:
        algo_name = algo["name"]
        base_name = algo["defaults"]
        if base_name not in resolved_baselines:
            raise AssembleError(
                f"algorithm '{algo_name}' references unknown baseline '{base_name}'; "
                f"known: {sorted(resolved_baselines)}"
            )
        diffs_path = _resolve_scenario_path(exp_root, algo.get("scenario"), "treatment.yaml")
        overlay_path = generated_root / algo_name / f"{algo_name}_config.yaml"
        resolved = resolve_treatment(
            baseline_resolved=resolved_baselines[base_name],
            diffs_path=diffs_path,
            overlay_path=overlay_path,
        )
        inject_image_tag(resolved, image_ref)
        packages.append((algo_name, resolved))

    # 5. Inject hf secret on every package --------------------------------
    hf_secret = (cluster_config.get("secret_names") or {}).get("hf_token", "hf-secret")
    for _, resolved in packages:
        inject_hf_secret_name(resolved, hf_secret)

    # 6. Write scenario YAMLs ---------------------------------------------
    write_resolved_scenarios(run_dir, packages)

    # 7. Generate PipelineRuns --------------------------------------------
    workloads = [_load_workload(exp_root, wl) for wl in manifest.get("workloads", [])]
    pipeline_name = (manifest.get("pipeline") or {}).get("name", "sim2real")
    observe = manifest.get("blis_observe") or {}
    # Derive model_name from the first baseline package (matches prior behavior).
    first_baseline = next(
        (resolved for name, resolved in packages if name in resolved_baselines),
        packages[0][1] if packages else {},
    )
    scenarios_list = first_baseline.get("scenario", [])
    model_name = scenarios_list[0].get("model", {}).get("name", "") if scenarios_list else ""

    generate_pipelineruns(
        run_dir=run_dir,
        packages=packages,
        workloads=workloads,
        run_name=run_name,
        cluster_config=cluster_config,
        pipeline_name=pipeline_name,
        observe=observe,
        model_name=model_name,
        # Step-1 does not read git submodule state — PR 3's deploy.py will.
        # Empty strings match the "no submodule initialized" fallback that
        # today's prepare.py surfaces as a warning; the PipelineRun params
        # accept "" and the downstream benchmark chart handles the absence.
        submodule_shas={},
        submodule_urls={},
    )

    # 8. Write run_metadata.json ------------------------------------------
    write_run_metadata(
        run_dir,
        {
            "version": 1,
            "run_name": run_name,
            "translation_hash": translation_hash,
            "cluster_id": cluster_id,
            "params_hash": params_hash,
            "image_tag": image_ref,
            "assembled_at": now_iso,
        },
    )
    # Skipped-algorithm warnings are surfaced by the CLI wrapper, not the
    # pure helper — the helper stays quiet.
    assemble_run.skipped_algorithms = skipped_algo_names  # type: ignore[attr-defined]


# Stash the last-run skip list on the function itself so the CLI wrapper
# can surface it without a return-value change. Reset by every call.
assemble_run.skipped_algorithms = []  # type: ignore[attr-defined]
```

Note on the `assemble_run.skipped_algorithms` pattern: exposing side-band data on a function object is uncommon. During TDD you may find it cleaner to have `assemble_run` return the skipped list (or a small named tuple). If the tests you write in Step 1.23 already assert on this via a return value, adjust the signature accordingly and drop the attribute pattern. Consistency: the tests as-written above don't observe skipped_algorithms — so either shape is fine; pick the one that reads better after you see it in context.

- [ ] **Step 1.25: Run all `assemble_run` tests — expect PASS**

Run: `python -m pytest pipeline/tests/test_assemble_run.py -v`
Expected: all tests pass. If skipped-algorithm surfacing test is missing, add one asserting on either the return value or the module-level attribute — matching whatever shape you settled on.

- [ ] **Step 1.26: Lint**

Run: `ruff check pipeline/lib/assemble_run.py pipeline/tests/test_assemble_run.py --select F`
Expected: exit 0, no output.

- [ ] **Step 1.27: Commit**

```bash
git add pipeline/lib/assemble_run.py pipeline/tests/test_assemble_run.py docs/superpowers/plans/2026-07-01-issue-445-sim2real-assemble.md
git commit -m "feat(pipeline): add pipeline/lib/assemble_run.py

Pure helper module behind the incoming \`sim2real assemble\`
command. Owns validation, translation-hash resolution,
assembly-slice snapshotting, deep-merge scenario resolution,
image_tag + hf secret injection, PipelineRun generation, and
run_metadata.json writing. No argparse, no stdout — callers
surface errors via AssembleError.

Consumers land in the next commit; tests cover the module in
isolation.

Refs #445"
```

---

### Task 2: `sim2real assemble` CLI subcommand

**Files:**
- Modify: `pipeline/sim2real.py`
- Test: `pipeline/tests/test_sim2real.py` (add cases)

**Interfaces:**
- Consumes: `pipeline.lib.assemble_run.assemble_run(...)`, `AssembleError`.
- Produces: `sim2real assemble --translation HASH --cluster CLUSTER_ID --run RUN_NAME [--force]`. Exit codes: 0 on success, 2 on validation error (matches translation register).

- [ ] **Step 2.1: Write failing test — CLI success path**

Append to `pipeline/tests/test_sim2real.py`:

```python
class TestAssembleCommand:
    def _make_minimal_registration(self, tmp_path):
        """Register a translation via sim2real's own register command so
        we exercise the same layout the assemble command will read.
        """
        cfg = tmp_path / "treatment.yaml"
        cfg.write_bytes(b"scenario:\n  - name: test-scenario\n    inferenceExtension:\n      pluginsConfigFile: sr.yaml\n")
        thash, _ = sim2real._register_translation(
            algorithm_name="sr",
            image_ref="ghcr.io/foo/bar:v1",
            config_path=cfg,
            baseline_config_path=None,
            registered_hash=None,
            now_iso="2026-07-01T14:00:00Z",
        )
        return thash

    def _write_yaml(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.dump(data, sort_keys=False))

    def _write_json(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n")

    def _bootstrap_experiment(self, tmp_path, thash):
        cluster_id = "ocp-east"
        cluster_dir = tmp_path / "workspace" / "clusters" / cluster_id
        cluster_dir.mkdir(parents=True)
        self._write_json(
            cluster_dir / "cluster_config.json",
            {"cluster_id": cluster_id, "namespaces": ["ns0"], "secret_names": {"hf_token": "hf"}, "workspaces": {}},
        )
        manifest = {
            "kind": "sim2real-transfer",
            "version": 3,
            "scenario": "test-scenario",
            "component": {"repo": "acme/foo", "kind": "gaie"},
            "context": {"text": "", "files": []},
            "baselines": [{"name": "baseline", "scenario": "baselines/base.yaml"}],
            "algorithms": [{"name": "sr", "source": "algo/sr.py", "defaults": "baseline"}],
            "workloads": ["workloads/w1.yaml"],
            "defaults": {"disable": []},
        }
        self._write_yaml(tmp_path / "transfer.yaml", manifest)
        self._write_yaml(
            tmp_path / "baselines" / "base.yaml",
            {"scenario": [{"name": "test-scenario", "model": {"name": "M"}}]},
        )
        self._write_yaml(tmp_path / "workloads" / "w1.yaml", {"name": "w1", "num_requests": 1})
        (tmp_path / "algo").mkdir()
        (tmp_path / "algo" / "sr.py").write_text("# stub\n")
        return cluster_id

    def test_success_produces_run_dir(self, tmp_path, capsys):
        thash = self._make_minimal_registration(tmp_path)
        cluster_id = self._bootstrap_experiment(tmp_path, thash)
        rc = sim2real.main(
            [
                "--experiment-root", str(tmp_path),
                "assemble",
                "--translation", thash,
                "--cluster", cluster_id,
                "--run", "trial-1",
            ]
        )
        assert rc == 0
        run_dir = tmp_path / "workspace" / "runs" / "trial-1"
        assert (run_dir / "manifest.assembly.yaml").exists()
        assert (run_dir / "run_metadata.json").exists()
        assert (run_dir / "cluster" / "baseline.yaml").exists()
        assert (run_dir / "cluster" / "sr.yaml").exists()
        assert (run_dir / "cluster" / "pipelinerun-w1-baseline.yaml").exists()
        assert (run_dir / "cluster" / "pipelinerun-w1-sr.yaml").exists()

    def test_refuses_existing_run_without_force(self, tmp_path, capsys):
        thash = self._make_minimal_registration(tmp_path)
        cluster_id = self._bootstrap_experiment(tmp_path, thash)
        (tmp_path / "workspace" / "runs" / "trial-1").mkdir(parents=True)
        rc = sim2real.main(
            [
                "--experiment-root", str(tmp_path),
                "assemble",
                "--translation", thash,
                "--cluster", cluster_id,
                "--run", "trial-1",
            ]
        )
        assert rc == 2
        out = capsys.readouterr()
        assert "--force" in out.err

    def test_force_overwrites(self, tmp_path, capsys):
        thash = self._make_minimal_registration(tmp_path)
        cluster_id = self._bootstrap_experiment(tmp_path, thash)
        existing = tmp_path / "workspace" / "runs" / "trial-1"
        existing.mkdir(parents=True)
        (existing / "sentinel").write_text("leftover")
        rc = sim2real.main(
            [
                "--experiment-root", str(tmp_path),
                "assemble",
                "--translation", thash,
                "--cluster", cluster_id,
                "--run", "trial-1",
                "--force",
            ]
        )
        assert rc == 0
        assert not (existing / "sentinel").exists()

    def test_missing_translation_hash_errors(self, tmp_path, capsys):
        thash = self._make_minimal_registration(tmp_path)
        cluster_id = self._bootstrap_experiment(tmp_path, thash)
        rc = sim2real.main(
            [
                "--experiment-root", str(tmp_path),
                "assemble",
                "--translation", "0" * 64,
                "--cluster", cluster_id,
                "--run", "trial-1",
            ]
        )
        assert rc == 2
        assert "translation" in capsys.readouterr().err.lower()
```

- [ ] **Step 2.2: Run tests — expect FAIL**

Run: `python -m pytest pipeline/tests/test_sim2real.py::TestAssembleCommand -v`
Expected: FAIL — argparse rejects `assemble` (unrecognized subcommand).

- [ ] **Step 2.3: Wire the subcommand + `_cmd_assemble` into `sim2real.py`**

Edit `pipeline/sim2real.py`:

1. Add import near the other lib imports:

```python
from pipeline.lib import assemble_run as _assemble_run_lib  # noqa: E402
```

2. In `build_parser()`, after the `translation` block, add:

```python
    asm = sub.add_parser("assemble", help="Assemble a run from a registered translation")
    asm.add_argument("--translation", required=True, metavar="HASH",
                     help="translation hash (from `sim2real translation register`)")
    asm.add_argument("--cluster", required=True, metavar="CLUSTER_ID",
                     help="cluster id (matches workspace/clusters/<id>/)")
    asm.add_argument("--run", required=True, metavar="RUN_NAME",
                     help="run name — directory created at workspace/runs/<run>/")
    asm.add_argument("--force", action="store_true",
                     help="overwrite an existing runs/<run>/ directory")
```

3. Add the command handler:

```python
def _cmd_assemble(args) -> int:
    manifest_path = Path(args.experiment_root or ".") / "transfer.yaml"
    if not manifest_path.exists():
        manifest_path = Path(args.experiment_root or ".") / "config" / "transfer.yaml"
    if not manifest_path.exists():
        print(
            f"error: transfer.yaml not found under {args.experiment_root or '.'}",
            file=sys.stderr,
        )
        return 2

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        _assemble_run_lib.assemble_run(
            translation_hash=args.translation,
            cluster_id=args.cluster,
            run_name=args.run,
            experiment_root=Path(args.experiment_root).resolve() if args.experiment_root else Path.cwd(),
            manifest_path=manifest_path,
            force=args.force,
            now_iso=now_iso,
        )
    except _assemble_run_lib.AssembleError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    skipped = getattr(_assemble_run_lib.assemble_run, "skipped_algorithms", [])
    for name in skipped:
        print(
            f"warning: algorithm '{name}' declared in transfer.yaml but not "
            f"in translation_output.json — skipped",
            file=sys.stderr,
        )
    print(f"assembled run {args.run}")
    return 0
```

4. In `main()`, add the dispatch branch after the translation-register branch:

```python
    if args.command == "assemble":
        return _cmd_assemble(args)
```

- [ ] **Step 2.4: Run tests — expect PASS**

Run: `python -m pytest pipeline/tests/test_sim2real.py -v`
Expected: existing translation-register tests + 4 new assemble tests pass.

- [ ] **Step 2.5: Lint**

Run: `ruff check pipeline/sim2real.py --select F`
Expected: exit 0.

- [ ] **Step 2.6: Commit**

```bash
git add pipeline/sim2real.py pipeline/tests/test_sim2real.py
git commit -m "feat(pipeline): add \`sim2real assemble\` subcommand

Wires pipeline/lib/assemble_run.py behind a CLI:
  sim2real assemble --translation HASH --cluster CLUSTER_ID --run NAME [--force]

Prints per-skipped-algorithm warnings, surfaces AssembleError as
'error: ...; rc=2' consistent with translation register. transfer.yaml
is resolved from --experiment-root, falling back to config/transfer.yaml.

Refs #445"
```

---

### Task 3: Delete legacy files + rewire test_epp

**Files:**
- Delete: `pipeline/prepare.py`, `pipeline/lib/state_machine.py`, `pipeline/lib/context_builder.py`, `pipeline/lib/assemble.py`, `pipeline/tests/test_prepare.py`, `pipeline/tests/test_state_machine.py`, `pipeline/tests/test_context_builder.py`, `pipeline/tests/test_assemble.py`
- Modify: `pipeline/tests/test_epp.py`
- Modify: `.claude/skills/sim2real-translate/SKILL.md`

- [ ] **Step 3.1: Rewire `test_epp.py` to drop its `assemble_scenarios` dependency**

Replace the import:

```python
# Before
from pipeline.lib.assemble import assemble_scenarios
# After
from pipeline.lib.values import deep_merge
```

Replace each `assemble_scenarios(...)` call site with an inline `deep_merge` chain. In `test_epp_image_in_pipelinerun_scenario_content`:

```python
# Load the same three files but merge with deep_merge directly.
baseline_data_loaded = yaml.safe_load((tmp_path / "baseline.yaml").read_text())
baseline_overlay_loaded = yaml.safe_load((tmp_path / "generated" / "baseline_config.yaml").read_text()) or {}
treatment_overlay_loaded = yaml.safe_load((tmp_path / "generated" / "treatment_config.yaml").read_text()) or {}
baseline_resolved = deep_merge(baseline_data_loaded, baseline_overlay_loaded)
treatment_resolved = deep_merge(baseline_resolved, treatment_overlay_loaded)
```

Apply the same replacement in `test_baseline_pipelinerun_has_no_epp_injection` (both files' fixtures already write the same three files, so the inline replacement is drop-in).

Do NOT change the assertions or the surrounding test body.

- [ ] **Step 3.2: Delete the eight legacy files**

Run:

```bash
git rm pipeline/prepare.py \
       pipeline/lib/state_machine.py \
       pipeline/lib/context_builder.py \
       pipeline/lib/assemble.py \
       pipeline/tests/test_prepare.py \
       pipeline/tests/test_state_machine.py \
       pipeline/tests/test_context_builder.py \
       pipeline/tests/test_assemble.py
```

Expected: eight files staged as deletions.

- [ ] **Step 3.3: Stub `.claude/skills/sim2real-translate/SKILL.md`**

Overwrite the file with:

````markdown
---
name: sim2real-translate
description: Placeholder — the skill-driven translation flow lands in step-2.
---

# sim2real-translate — DISABLED for step-1

This skill has been removed as part of the BYO MVP (step-1 of the
`refactor/v2-step-1` epic — issue [#443](https://github.com/inference-sim/sim2real/issues/443)).

Step-1 supports **bring-your-own translations only**. Register a
pre-built translation with:

```
python pipeline/sim2real.py translation register \
    --algorithm NAME \
    --image REF \
    --config PATH_TO_TREATMENT_OVERLAY
```

The skill-driven flow (evolved algorithm → translated EPP plugin) is
scheduled to be restored in step-2. Invoking the skill in the current
state exits with this message.

**If you got here from a slash command:** stop and use
`sim2real translation register` instead. If you don't yet have a
prebuilt image + config, wait for step-2 to land.
````

- [ ] **Step 3.4: Run the whole pipeline test suite**

Run: `python -m pytest pipeline/ -v`
Expected: all tests pass. Any collection error signals a straggling import — fix it here. In particular verify:
- No `ModuleNotFoundError` for `pipeline.prepare`, `pipeline.lib.state_machine`, `pipeline.lib.context_builder`, `pipeline.lib.assemble`.
- `test_epp.py` still passes end-to-end.
- `test_sim2real.py`, `test_assemble_run.py` still pass.

- [ ] **Step 3.5: Lint the working tree**

Run: `ruff check pipeline/ .claude/skills/ --select F`
Expected: exit 0.

- [ ] **Step 3.6: Commit**

```bash
git add pipeline/tests/test_epp.py .claude/skills/sim2real-translate/SKILL.md
git commit -m "refactor(pipeline): delete prepare.py + legacy assemble machinery

Deletes prepare.py, state_machine.py, context_builder.py,
assemble.py (247-line legacy), and their four dedicated test
files. sim2real assemble (this PR) supersedes prepare's Phase 4;
the skill-driven derivations that Phases 2-3 covered are deferred
to step-2 of the epic.

test_epp.py's two integration tests are re-plumbed to use
deep_merge directly instead of the deleted assemble_scenarios
helper — same fixtures, same assertions.

.claude/skills/sim2real-translate/SKILL.md is stubbed with a
'restored in step-2' message per the epic plan.

Refs #445"
```

---

### Task 4: Docs + CI sweep

**Files:**
- Modify: `CLAUDE.md`
- Modify: `pipeline/README.md`
- Modify: `.github/workflows/test.yml` (only if needed — see step)

- [ ] **Step 4.1: Update CLAUDE.md — remove prepare.py and 6-phase state machine references**

Concrete edits (do all of them):

1. In the "Transfer Pipeline" section, replace:

```
setup.py → prepare.py → [/sim2real-translate] → deploy.py
```

with:

```
setup.py → sim2real translation register → sim2real assemble → deploy.py
```

Update the four command examples in that section (the ones invoking `prepare.py`) to describe the new flow (translation register + assemble). Keep the "backward compat" paragraph about `--experiment-root` because it still applies to `sim2real.py`.

2. Delete the `**pipeline/prepare.py** — 6-phase state machine` paragraph and the entire phase table underneath it.

3. In the "Pipeline Library" table:
   - Remove the `manifest.py`, `state_machine.py`, `context_builder.py`, `values.py`, `assemble.py`, and `pod_pending.py` rows that reference deleted modules.
   - `manifest.py`, `values.py`, `pod_pending.py` STILL exist — keep them.
   - Delete only the `state_machine.py`, `context_builder.py`, `assemble.py` rows.
   - Add rows for `slicer.py` (already exists in the repo — surface it in CLAUDE.md too) and `assemble_run.py`:

```
| `slicer.py` | Splits transfer.yaml into translation-slice / assembly-slice + translation_hash |
| `assemble_run.py` | Assembly logic behind `sim2real assemble` (deep-merge + PipelineRun generation) |
```

4. In the "Workspace Artifacts" table, replace the four rows that reference `prepare.py`:

- `runs/<run>/.state.json` — DELETE the row.
- `runs/<run>/skill_input.json` — DELETE the row.
- `runs/<run>/translation_output.json` — retarget: written by `sim2real translation register` (under `workspace/translations/<hash>/`), NOT by prepare.py. Update Path column accordingly, or move to a new row named `translations/<hash>/translation_output.json`.
- `runs/<run>/generated/…` — retarget: content lives under `workspace/translations/<hash>/generated/` now. Same treatment.
- `runs/<run>/cluster/…` — writer becomes `sim2real assemble`.
- `runs/<run>/run_summary.md` — DELETE the row (no longer produced).

Add rows:

```
| `translations/<hash>/translation_output.json` | `sim2real translation register` | `sim2real assemble`, `deploy.py`, `run.py` |
| `translations/<hash>/registered.json` | `sim2real translation register` | audit only |
| `translations/<hash>/generated/<algo>/<algo>_config.yaml` | `sim2real translation register` | `sim2real assemble` (per-algo overlay) |
| `runs/<run>/manifest.assembly.yaml` | `sim2real assemble` | reproducibility / drift detection (step-4) |
| `runs/<run>/run_metadata.json` | `sim2real assemble` | `deploy.py`, `run.py` |
| `runs/<run>/cluster/*.yaml` | `sim2real assemble` | `deploy.py` |
```

5. Delete the entire `## Scenario-Based Assembly Architecture` paragraph — its content moves to pipeline/README.md's "Assemble a run" section (per §4.2 below).

- [ ] **Step 4.2: Update pipeline/README.md**

Concrete edits:

1. Replace the four-line phase diagram near the top:

```
cluster.py provision  (one-time per cluster — bootstrap ...)
                   ↓
setup.py → prepare.py → [/sim2real-translate] → deploy.py   (per-workspace + per-run)
```

with:

```
cluster.py provision  (one-time per cluster — bootstrap ...)
                   ↓
setup.py → sim2real translation register → sim2real assemble → deploy.py   (per-workspace + per-run)
```

Update the `Per-workspace + per-run cycle` code block on line ~27:

```bash
python pipeline/setup.py       --experiment-root ../admission-control
python pipeline/sim2real.py translation register \
    --algorithm <name> --image <ref> --config <path>
python pipeline/sim2real.py assemble \
    --translation <hash> --cluster <cluster_id> --run <run_name>
python pipeline/deploy.py      --experiment-root ../admission-control
```

2. Delete the ENTIRE `## prepare.py` section (all of the "6-phase state machine" content from `## prepare.py` through the "**`--force`**", "**`--rebuild-context`**", and "Phase state is tracked per-run" paragraphs — everything up to but not including the `---` separator before `## deploy.py`).

3. Insert a new `## Assemble a run` section in the same place. Content:

```markdown
## Assemble a run

Once a translation is registered, `sim2real assemble` produces a run
directory under `workspace/runs/<run>/` containing the resolved
scenario YAMLs, generated PipelineRun manifests, an assembly-slice
snapshot, and per-run metadata.

```bash
python pipeline/sim2real.py assemble \
    --translation HASH \
    --cluster CLUSTER_ID \
    --run RUN_NAME \
    [--force]
```

**Inputs read:**

- `workspace/translations/<hash>/translation_output.json` — algorithms + image_ref.
- `workspace/translations/<hash>/generated/baseline_config.yaml` — optional baseline overlay.
- `workspace/translations/<hash>/generated/<algo>/<algo>_config.yaml` — per-algorithm treatment overlay.
- `workspace/clusters/<cluster_id>/cluster_config.json` — namespaces, workspace bindings, hf secret name.
- `<experiment-root>/transfer.yaml` (or `config/transfer.yaml`) — v3 manifest.
- `<experiment-root>/baselines/*.yaml` — baseline bundles referenced by `transfer.yaml:baselines[].scenario`.
- `<experiment-root>/baselines/defaults/*.yaml` — framework defaults overlays (opt-out via `transfer.yaml:defaults.disable`).

**Outputs written to `workspace/runs/<run>/`:**

| File | Purpose |
|------|---------|
| `manifest.assembly.yaml` | Verbatim snapshot of the assembly slice from `transfer.yaml`. |
| `run_metadata.json` | `{version, run_name, translation_hash, cluster_id, params_hash, image_tag, assembled_at}` — pinned schema, `version: 1`. |
| `cluster/baseline.yaml` | Resolved baseline scenario (framework defaults → bundle → baseline overlay). |
| `cluster/<algo>.yaml` | Resolved treatment scenario per registered algorithm (baseline_resolved → treatment bundle diffs → algo overlay → injected image_tag). |
| `cluster/pipelinerun-<workload>-<package>.yaml` | One PipelineRun per (workload, package). Consumed by `deploy.py run`. |

**Assembly formula** (deep-merged via `pipeline/lib/values.py:deep_merge`):

```
baseline_resolved = deep_merge(framework_defaults, baseline_bundle, baseline_overlay)
treatment_resolved = deep_merge(baseline_resolved, treatment_bundle_diffs, algo_overlay)
```

**`params_hash`** is SHA-256 over the bytes of `manifest.assembly.yaml`. Recorded in `run_metadata.json` for later drift detection (step-4 of the epic).

**Algorithm filtering:** algorithms listed in `transfer.yaml:algorithms` but absent from `translation_output.json:algorithms` are skipped with a warning — the run still assembles for the algorithms that are registered.

**Failure modes:**

- Existing `runs/<run>/` without `--force` → exit 2 with `--force` hint. No writes.
- Missing translation directory or `translation_output.json` → exit 2, no writes.
- Missing `cluster_config.json` for `--cluster` → exit 2, no writes.
- Workload file referenced in `transfer.yaml:workloads` missing → exit 2, no writes.
- Malformed YAML anywhere in the input chain → exit 2, no writes.

`--force` recursively deletes `workspace/runs/<run>/` before re-materializing it.
```

4. In the "Pipeline library" table, delete these rows:

```
| `state_machine.py` | Phase tracking with atomic JSON persistence (`.state.json`) |
| `context_builder.py` | Assembles context document, caches by SHA-256 hash |
| `assemble.py` | Scenario assembly: deep-merges bundles + overlays into resolved scenarios |
```

Add:

```
| `assemble_run.py` | Assembly logic behind `sim2real assemble` (deep-merge + PipelineRun generation) |
```

`values.py`'s description mentions `assemble.py` — update to:

```
| `values.py` | Deep-merge utility used by `assemble_run.py` |
```

5. In "Workspace artifacts" table:
   - Delete rows referencing `.state.json`, `skill_input.json`, `run_summary.md`.
   - Retarget rows for `translation_output.json`, `generated/…`, `cluster/…`, `run_metadata.json` to their new writers per §4.1's list.

6. In the "Common patterns" section at the bottom, replace:

```
# Resume after translation
python pipeline/prepare.py

# Force full regeneration
python pipeline/prepare.py --force
```

with:

```
# Assemble a run
python pipeline/sim2real.py assemble --translation HASH --cluster CID --run trial-1

# Re-assemble the same run
python pipeline/sim2real.py assemble --translation HASH --cluster CID --run trial-1 --force
```

7. In the "Scenario Overlay Format" section, remove the reference to `treatment_config.yaml` legacy path. That layout is dropped in step-1; only the per-algorithm layout survives:

```
**Per-algorithm layout** (the layout `sim2real translation register` produces):
- `generated/baseline_config.yaml` — optional shared baseline overlay
- `generated/{algo_name}/{algo_name}_config.yaml` — per-algorithm treatment overlay
```

Delete the "Legacy flat layout" bullet list.

Update the phrase "`images.inferenceScheduler` (custom EPP image — injected by `prepare.py`, not the skill)" to "`images.inferenceScheduler` (custom EPP image — injected by `sim2real assemble` from `translation_output.json:image_ref`, not the skill)".

- [ ] **Step 4.3: Confirm `.github/workflows/test.yml` still resolves**

Read `.github/workflows/test.yml`. Confirm it runs `python -m pytest pipeline/ <specific test files> -v`. Since `pipeline/` is a directory target, deleted tests are simply absent — no CI update strictly required. But: verify the specific-file list at the bottom does NOT name any of the deleted files (`test_prepare.py`, `test_state_machine.py`, `test_context_builder.py`, `test_assemble.py`). Currently it names `test_layout.py`, `test_cluster_ops.py`, `test_cluster_py.py`, `test_slicer.py`, `test_sim2real.py` — none of which are deleted. So no edit needed.

Add `pipeline/tests/test_assemble_run.py` to the explicit list for parity:

```yaml
      - name: Run tests
        run: |
          python -m pytest pipeline/ \
            pipeline/tests/test_layout.py \
            pipeline/tests/test_cluster_ops.py \
            pipeline/tests/test_cluster_py.py \
            pipeline/tests/test_slicer.py \
            pipeline/tests/test_sim2real.py \
            pipeline/tests/test_assemble_run.py \
            .claude/skills/sim2real-analyze/tests/ \
            .claude/skills/sim2real-bootstrap/tests/ \
            .claude/skills/sim2real-translate/tests/ \
            -v
```

If `.claude/skills/sim2real-translate/tests/` directory does not exist after stubbing SKILL.md, keep the entry only if tests remain; otherwise drop it. Verify by listing the directory:

```bash
ls .claude/skills/sim2real-translate/tests/ 2>&1
```

If the directory exists with `.py` files, keep the CI line. If it exists but is empty or missing, drop the entry (pytest errors on a missing target).

- [ ] **Step 4.4: Run the full suite + lint**

Run: `python -m pytest pipeline/ -v && ruff check pipeline/ .claude/skills/ --select F`
Expected: all tests pass, lint exits 0.

- [ ] **Step 4.5: Grep sweep for stale references**

Run:

```bash
grep -rn "prepare\.py\|state_machine\|context_builder\|6-phase\|Phase 3 checkpoint\|/sim2real-translate" \
    docs/ CLAUDE.md pipeline/README.md .claude/skills/ .github/ pipeline/ 2>/dev/null \
    | grep -v ".git/" | grep -v "workspace/" | grep -v "graphify-out/" \
    | grep -v "docs/superpowers/plans/2026-07-01-issue-445-sim2real-assemble.md"
```

For each hit outside the plan file, decide:
- Stale — update it or delete the reference.
- Still accurate — leave it and note why in the PR body.
- Unrelated — leave it.

Common stale-ref categories to check for:
- `pipeline/deploy.py` prints "run prepare.py" — STILL ACCURATE for this PR's scope (design says PR 3 rewrites `_cmd_run`).
- `pipeline/setup.py:402` prints "3. Run: python pipeline/prepare.py" — STALE. Update to "3. Register a translation with `python pipeline/sim2real.py translation register ...`, then `python pipeline/sim2real.py assemble ...`". If the phrasing is awkward, drop the line — setup.py's job is bootstrap; sequencing hints belong in README.
- `docs/proposals/*` mentioning prepare.py — likely historical; leave alone.
- `docs/epics/step-1/design.md` mentioning prepare.py — ORIGINAL design doc; leave alone.

- [ ] **Step 4.6: Commit docs sweep**

```bash
git add CLAUDE.md pipeline/README.md .github/workflows/test.yml pipeline/setup.py
git commit -m "docs(pipeline): sweep prepare.py + 6-phase references

Removes CLAUDE.md's 'Transfer Pipeline' phase table and Scenario-Based
Assembly Architecture section (both prepare.py-shaped); replaces
pipeline/README.md's prepare.py section with an 'Assemble a run'
section describing sim2real assemble's inputs, outputs, assembly
formula, and failure modes; retargets workspace artifact tables in
both docs to name sim2real assemble / translation register instead of
prepare.py; adds test_assemble_run.py to CI's explicit test list.

setup.py's tail-of-run hint is retargeted to sim2real.py — its
'Run: python pipeline/prepare.py' pointed at a script that no
longer exists.

Refs #445"
```

---

### Task 5: Push + open PR

- [ ] **Step 5.1: Push the branch**

```bash
BRANCH=$(git rev-parse --abbrev-ref HEAD)
git push -u origin "${BRANCH}"
```

Expected: push succeeds.

- [ ] **Step 5.2: Open the PR against `refactor/v2-step-1`**

Author a PR body highlighting:
- Closes #445
- Base is `refactor/v2-step-1` (epic branch)
- Summary of the four PR-2 acceptance categories: new `sim2real assemble` command, deletion of four legacy files (+ their four tests + test_epp rewire), stubbed SKILL.md, docs/CI sweep.
- Sweep results — what was checked, what was updated, what was intentionally left (deploy.py's `prepare.py` messages, design doc).
- Verification: `python -m pytest pipeline/ -v` all green, `ruff check pipeline/ .claude/skills/ --select F` exits 0.
- Real-cluster gate: NOTE that structural verification is included via unit tests; real-cluster `sim2real assemble --run trial-1` against `sr` remains the PR's manual gate (design §PR 2).

Command:

```bash
gh pr create --base refactor/v2-step-1 \
    --title "step-1 PR 2: sim2real assemble + delete prepare/legacy-assemble (#445)" \
    --body-file <(cat <<'EOF'
Closes #445 — see the issue for full acceptance criteria.

## Summary

- New `sim2real assemble --translation HASH --cluster CLUSTER_ID --run RUN_NAME [--force]` subcommand.
- Pure helper `pipeline/lib/assemble_run.py` owns validation, slice + snapshot, deep-merge, image_tag + hf-secret injection, PipelineRun generation, and metadata writing. `pipeline/sim2real.py` gains a thin `_cmd_assemble` wrapper.
- Pinned schemas (v1): `runs/<R>/run_metadata.json` and `runs/<R>/manifest.assembly.yaml`.
- Deleted: `pipeline/prepare.py`, `pipeline/lib/{state_machine,context_builder,assemble}.py` + their four dedicated test files.
- `pipeline/tests/test_epp.py`'s two integration tests re-plumbed to use `deep_merge` directly (drops the deleted `assemble_scenarios` helper).
- Stubbed `.claude/skills/sim2real-translate/SKILL.md` with the "restored in step-2" message.
- Docs/CI sweep: CLAUDE.md, pipeline/README.md, .github/workflows/test.yml, pipeline/setup.py's tail hint.

## Acceptance mapping

| Criterion | Coverage |
|-----------|----------|
| `sim2real assemble` produces run tree | `TestAssembleCommand.test_success_produces_run_dir` + `TestAssembleRun.test_end_to_end_produces_expected_files` |
| Filter algorithms to those in `translation_output.json` (warn + skip) | `TestFilterAlgorithms` + `TestAssembleRun.test_filters_unregistered_algorithms` |
| Existing `runs/<R>/` without `--force` errors | `TestAssembleCommand.test_refuses_existing_run_without_force` + `TestAssembleRun.test_refuses_existing_run_without_force` |
| `--force` overwrites cleanly | `test_force_overwrites` + `test_force_overwrites_existing_run` |
| `manifest.assembly.yaml` is verbatim assembly slice | `TestWriteManifestAssembly.test_slicer_round_trip` |
| `params_hash = sha256(manifest.assembly.yaml bytes)` | `TestWriteManifestAssembly.test_params_hash_is_sha256_of_bytes` + `TestAssembleRun.test_params_hash_matches_manifest_assembly_bytes` |
| `image_tag` injected into treatment scenarios | `TestInjectImageTag` + `TestAssembleRun.test_treatment_scenario_carries_image_tag` |
| Slicer round-trip | `TestWriteManifestAssembly.test_slicer_round_trip` |
| 4 legacy modules deleted | See diff |
| SKILL.md stubbed | See diff |
| `.github/workflows/test.yml` updated | See diff — added `test_assemble_run.py` explicit entry |
| `CLAUDE.md` updated | See diff |
| `pipeline/README.md` "Assemble a run" section | See diff |

## Real-cluster gate

Not run in this session — a real-cluster run against `kalantar-msb/sr` remains a manual verification (design §PR 2 real-cluster gate). Every path this PR touches has structural test coverage above.

## Sweep for stale references

- Grepped `prepare.py|state_machine|context_builder|6-phase|Phase 3 checkpoint|/sim2real-translate` under `docs/`, `pipeline/`, `.claude/skills/`, `.github/`, `CLAUDE.md`, `pipeline/README.md`.
- Updated: `CLAUDE.md`, `pipeline/README.md`, `pipeline/setup.py:402`.
- Intentionally left: `pipeline/deploy.py`'s "run prepare.py" hints (rewritten by PR 3 of the epic), `docs/epics/step-1/design.md` and `docs/proposals/*` (historical design docs), the `docs/superpowers/plans/2026-07-01-...` plan file itself.

## Verification

- `python -m pytest pipeline/ -v` — passing.
- `ruff check pipeline/ .claude/skills/ --select F` — exit 0.
EOF
)
```

If `gh` fails with `Resource not accessible by personal access token`, retry with `unset GITHUB_TOKEN GH_TOKEN;` prefixed.

- [ ] **Step 5.3: Report the PR URL**

Read the URL from `gh pr create` output and pass it back to the caller for the review loop.

---

## Self-Review

- **Spec coverage:** each of issue #445's 11 acceptance checkboxes has a task/test above. Cross-referenced in the PR-body acceptance mapping table.
- **Placeholder scan:** every step contains actual code / commands / exact paths. No TBDs.
- **Type consistency:** `AssembleError` used consistently; `assemble_run(...)` argument names match between definition, tests, CLI dispatch, and PR-body copy.
- **Deletion completeness:** the four modules named in the issue body are deleted; their four test files are deleted; the one transitive test-only dependency (`test_epp.py`) is rewired in the same commit so the tree stays green.
- **Scope discipline:** no touches to `pipeline/deploy.py`. Its `prepare.py` messages get rewritten by PR 3 of the epic per the design. `pipeline/setup.py`'s obsolete print statement is the one exception — it references a script that no longer exists; the sweep step handles it.
