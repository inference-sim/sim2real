"""Unit tests for pipeline/lib/resolve.py and its CLI wrapper.

Tests the ``resolve_run`` hydrated-view helper introduced in step-3
(epic #486) plus the ``sim2real resolve`` CLI subcommand. Fixture
shape is a minimal fake workspace — just what ``resolve_run`` reads —
rather than a full ``assemble_run.py`` E2E fixture. The step-3 epic's
Child #2 adds a coupled assemble→resolve schema-completeness test that
uses the E2E fixture.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from pipeline.lib import layout, resolve


_HASH = "43b4e8d749df6b7ae334fc47609514d86dfc5a2697eb450e6a3149dbda013319"


@pytest.fixture(autouse=True)
def _isolated_experiment_root(tmp_path):
    layout._EXPERIMENT_ROOT = tmp_path
    yield
    layout._EXPERIMENT_ROOT = None


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def _write_yaml(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data))


def _make_run(
    tmp_path: Path,
    *,
    run_name: str = "trial-1",
    translation_hash: str = _HASH,
    cluster_id: str = "test-cluster",
    algorithms: list[dict] | None = None,
    baselines: list[dict] | None = None,
    workloads: list[str] | None = None,
    write_manifest: bool = True,
    write_cluster_config: bool = True,
    results_layout: dict[str, list[str]] | None = None,
    results_shape: str = "flat",
    replicas: int = 2,
    write_cluster_scenarios: bool = True,
    include_pipelineruns: bool = True,
) -> Path:
    """Materialize a minimal fake workspace and return the run_dir path.

    ``algorithms`` and ``baselines`` seed the translation_output.json +
    manifest.assembly.yaml shapes. ``results_layout`` maps phase name to
    a list of workload subdir names that should be created with a
    trace_data.csv inside. Set the various write_* flags to False to
    simulate partial/corrupt workspaces.
    """
    if algorithms is None:
        algorithms = [
            {
                "name": "softreflective",
                "source_path": "algorithms/softreflective.py",
                "source_sha256": "e3b0c44",
                "image_ref": "ghcr.io/foo/bar:v1",
                "image_digest": "sha256:aa",
            }
        ]
    if baselines is None:
        baselines = [{"name": "baseline", "scenario": "baselines/base.yaml"}]
    if workloads is None:
        workloads = ["workloads/code_generation_4.yaml"]
    if results_layout is None:
        results_layout = {"softreflective": ["code_generation_4"]}

    workspace = tmp_path / "workspace"
    run_dir = workspace / "runs" / run_name
    trans_dir = workspace / "translations" / translation_hash

    # run_metadata.json — the pinned v1 schema step-2 assemble writes.
    _write_json(
        run_dir / "run_metadata.json",
        {
            "version": 1,
            "run_name": run_name,
            "translation_hash": translation_hash,
            "cluster_id": cluster_id,
            "params_hash": "b751df5",
            "image_tag": (algorithms[0].get("image_ref") if algorithms else ""),
            "assembled_at": "2026-07-04T15:41:48Z",
            "scenario": "test-scenario",
        },
    )

    # translation_output.json (step-2 shape)
    _write_json(
        trans_dir / "translation_output.json",
        {
            "version": 1,
            "translation_hash": translation_hash,
            "source": "skill",
            "alias": "softreflective-v1",
            "algorithms": algorithms,
            "created_at": "2026-07-04T15:00:00Z",
        },
    )

    # manifest.assembly.yaml
    if write_manifest:
        _write_yaml(
            run_dir / "manifest.assembly.yaml",
            {
                "kind": "sim2real-transfer",
                "version": 3,
                "scenario": "test-scenario",
                "baselines": baselines,
                "algorithms": [
                    {"name": a["name"], "defaults": "baseline"}
                    for a in algorithms
                ],
                "workloads": workloads,
                "defaults": {"disable": []},
                "blis_observe": {"timeout": 3600},
            },
        )

    # cluster_config.json
    if write_cluster_config and cluster_id:
        _write_json(
            workspace / "clusters" / cluster_id / "cluster_config.json",
            {"cluster_id": cluster_id, "namespaces": ["ns-0"]},
        )

    # results/<phase>/<workload>/... — layout depends on ``results_shape``:
    #   "flat"    → <workload>/trace_data.csv (legacy)
    #   "replica" → <workload>/i{1..replicas}/trace_data.csv
    #   "mixed"   → first workload uses replica, remainder use flat
    if results_shape not in ("flat", "replica", "mixed"):
        raise ValueError(f"unknown results_shape: {results_shape!r}")
    for phase, phase_workloads in results_layout.items():
        for idx, wl in enumerate(phase_workloads):
            wl_dir = run_dir / "results" / phase / wl
            wl_dir.mkdir(parents=True, exist_ok=True)
            use_replica = results_shape == "replica" or (
                results_shape == "mixed" and idx == 0
            )
            if use_replica:
                for i in range(1, replicas + 1):
                    iter_dir = wl_dir / f"i{i}"
                    iter_dir.mkdir(parents=True, exist_ok=True)
                    (iter_dir / "trace_data.csv").write_text("dummy,csv\n")
            else:
                (wl_dir / "trace_data.csv").write_text("dummy,csv\n")

    # cluster scenario yamls
    if write_cluster_scenarios:
        cluster_dir = run_dir / "cluster"
        cluster_dir.mkdir(parents=True, exist_ok=True)
        _write_yaml(cluster_dir / "baseline.yaml", {"scenario": [{"name": "test-scenario"}]})
        for a in algorithms:
            _write_yaml(
                cluster_dir / f"{a['name']}.yaml",
                {"scenario": [{"name": "test-scenario", "algo": a["name"]}]},
            )
        if include_pipelineruns:
            for a in algorithms:
                _write_yaml(
                    cluster_dir / f"pipelinerun-code-generation-4-{a['name']}.yaml",
                    {"kind": "PipelineRun"},
                )
            _write_yaml(
                cluster_dir / "pipelinerun-code-generation-4-baseline.yaml",
                {"kind": "PipelineRun"},
            )

    return run_dir


# ── resolve_run happy path ──────────────────────────────────────────────────


class TestResolveRunHappyPath:
    def test_returns_expected_v1_shape(self, tmp_path):
        _make_run(tmp_path)
        result = resolve.resolve_run(tmp_path, "trial-1")
        assert result["version"] == resolve.SCHEMA_VERSION
        assert result["run_name"] == "trial-1"
        assert result["cluster_id"] == "test-cluster"
        assert result["params_hash"] == "b751df5"
        assert result["image_tag"] == "ghcr.io/foo/bar:v1"
        assert result["assembled_at"] == "2026-07-04T15:41:48Z"
        # #551: scenario surfaces from run_metadata.json.
        assert result["scenario"] == "test-scenario"
        assert Path(result["run_dir"]).is_absolute()
        assert Path(result["experiment_root"]).is_absolute()

    def test_scenario_defaults_empty_when_run_metadata_omits_it(self, tmp_path):
        """Legacy runs (assembled before #551) have no scenario key; resolve
        returns "" rather than raising, so downstream consumers can decide
        whether to error or fall back."""
        import json as _json
        _make_run(tmp_path)
        # Rewrite run_metadata without the scenario field.
        run_dir = tmp_path / "workspace" / "runs" / "trial-1"
        meta_path = run_dir / "run_metadata.json"
        meta = _json.loads(meta_path.read_text())
        del meta["scenario"]
        _write_json(meta_path, meta)
        result = resolve.resolve_run(tmp_path, "trial-1")
        assert result["scenario"] == ""

    def test_top_level_omits_translation_hash(self, tmp_path):
        """Design decision: translation_hash appears only under translation.hash."""
        _make_run(tmp_path)
        result = resolve.resolve_run(tmp_path, "trial-1")
        assert "translation_hash" not in result
        assert result["translation"]["hash"] == _HASH

    def test_populates_translation_algorithms(self, tmp_path):
        _make_run(tmp_path)
        result = resolve.resolve_run(tmp_path, "trial-1")
        algos = result["translation"]["algorithms"]
        assert len(algos) == 1
        assert algos[0]["name"] == "softreflective"
        assert algos[0]["source_path"] == "algorithms/softreflective.py"
        assert algos[0]["image_ref"] == "ghcr.io/foo/bar:v1"
        # generated_dir / config_path composed from translation_hash + algo name
        assert algos[0]["generated_dir"].endswith(
            f"translations/{_HASH}/generated/softreflective"
        )
        assert algos[0]["config_path"].endswith(
            "generated/softreflective/softreflective_config.yaml"
        )

    def test_populates_translation_baselines_from_manifest(self, tmp_path):
        _make_run(tmp_path)
        result = resolve.resolve_run(tmp_path, "trial-1")
        baselines = result["translation"]["baselines"]
        assert len(baselines) == 1
        assert baselines[0]["name"] == "baseline"
        assert baselines[0]["generated_overlay_path"].endswith(
            "generated/baselines/baseline/baseline_config.yaml"
        )

    def test_populates_results_section(self, tmp_path):
        _make_run(
            tmp_path,
            results_layout={
                "softreflective": ["code_generation_4", "code_generation_10"]
            },
        )
        result = resolve.resolve_run(tmp_path, "trial-1")
        # phases_declared is baseline + algorithms from manifest
        assert result["results"]["phases_declared"] == ["baseline", "softreflective"]
        # phases_with_data filters to those with trace_data.csv
        assert result["results"]["phases_with_data"] == ["softreflective"]
        assert result["results"]["workloads_by_phase"]["softreflective"] == [
            "code_generation_10",
            "code_generation_4",
        ]

    def test_populates_results_section_replica_shape(self, tmp_path):
        """Bug #572: replica-shape runs put trace_data.csv under iN/. The
        one-level predicate used to miss them and return empty."""
        _make_run(
            tmp_path,
            results_shape="replica",
            replicas=2,
            results_layout={
                "softreflective": ["code_generation_4", "code_generation_10"]
            },
        )
        result = resolve.resolve_run(tmp_path, "trial-1")
        assert result["results"]["phases_with_data"] == ["softreflective"]
        assert result["results"]["workloads_by_phase"]["softreflective"] == [
            "code_generation_10",
            "code_generation_4",
        ]

    def test_populates_results_section_mixed_shape(self, tmp_path):
        """Mixed-shape run: one workload replica, another flat. Both
        must be reported — the predicate accepts either shape."""
        _make_run(
            tmp_path,
            results_shape="mixed",
            replicas=2,
            results_layout={
                "softreflective": ["code_generation_4", "code_generation_10"]
            },
        )
        result = resolve.resolve_run(tmp_path, "trial-1")
        assert result["results"]["phases_with_data"] == ["softreflective"]
        assert result["results"]["workloads_by_phase"]["softreflective"] == [
            "code_generation_10",
            "code_generation_4",
        ]

    def test_results_section_ignores_malformed_iter_dirs(self, tmp_path):
        """A workload whose only children are non-iN dirs (or malformed iN
        names like i0, iabc) reports no data. Guards against the naive
        `any(child/trace_data.csv)` fix that would false-positive on
        stray dirs like ``plans/`` or ``metrics/``."""
        _make_run(
            tmp_path,
            results_layout={"softreflective": []},
        )
        # Materialize a workload dir with only bogus iteration-like children
        # that DO contain trace_data.csv. The strict predicate must still
        # report the phase as empty.
        wl_dir = (
            tmp_path / "workspace" / "runs" / "trial-1"
            / "results" / "softreflective" / "code_generation_4"
        )
        wl_dir.mkdir(parents=True, exist_ok=True)
        for bogus in ("i0", "iabc", "plans", "metrics"):
            (wl_dir / bogus).mkdir()
            (wl_dir / bogus / "trace_data.csv").write_text("bogus\n")
        result = resolve.resolve_run(tmp_path, "trial-1")
        assert result["results"]["phases_with_data"] == []
        assert result["results"]["workloads_by_phase"] == {}

    def test_populates_cluster_scenarios(self, tmp_path):
        _make_run(tmp_path)
        result = resolve.resolve_run(tmp_path, "trial-1")
        cs = result["cluster_scenarios"]
        assert cs["baseline_yaml"].endswith("cluster/baseline.yaml")
        assert cs["treatment_yamls"]["softreflective"].endswith(
            "cluster/softreflective.yaml"
        )
        # PipelineRun yamls picked up
        pr_names = [Path(p).name for p in cs["pipelinerun_yamls"]]
        assert "pipelinerun-code-generation-4-softreflective.yaml" in pr_names
        assert "pipelinerun-code-generation-4-baseline.yaml" in pr_names

    def test_populates_manifest_assembly(self, tmp_path):
        _make_run(tmp_path)
        result = resolve.resolve_run(tmp_path, "trial-1")
        ma = result["manifest_assembly"]
        assert ma["path"].endswith("manifest.assembly.yaml")
        assert ma["scenario"] == "test-scenario"
        assert ma["workloads"] == ["workloads/code_generation_4.yaml"]
        assert ma["blis_observe"] == {"timeout": 3600}

    def test_populates_cluster_config_path_when_present(self, tmp_path):
        _make_run(tmp_path)
        result = resolve.resolve_run(tmp_path, "trial-1")
        assert result["cluster_config_path"].endswith(
            "clusters/test-cluster/cluster_config.json"
        )


# ── resolve_run failure modes ────────────────────────────────────────────────


class TestResolveRunFailures:
    def test_missing_workspace_raises(self, tmp_path):
        with pytest.raises(resolve.ResolveError, match="no workspace/"):
            resolve.resolve_run(tmp_path, "trial-1")

    def test_unknown_run_raises(self, tmp_path):
        (tmp_path / "workspace" / "runs").mkdir(parents=True)
        with pytest.raises(resolve.ResolveError, match="run 'nonexistent' not found"):
            resolve.resolve_run(tmp_path, "nonexistent")

    def test_missing_run_metadata_raises(self, tmp_path):
        run_dir = tmp_path / "workspace" / "runs" / "trial-1"
        run_dir.mkdir(parents=True)
        with pytest.raises(resolve.ResolveError, match="run_metadata.json not found"):
            resolve.resolve_run(tmp_path, "trial-1")

    def test_corrupt_run_metadata_raises(self, tmp_path):
        run_dir = tmp_path / "workspace" / "runs" / "trial-1"
        run_dir.mkdir(parents=True)
        (run_dir / "run_metadata.json").write_text("not-json {{{")
        with pytest.raises(resolve.ResolveError, match="is missing/corrupt"):
            resolve.resolve_run(tmp_path, "trial-1")

    def test_run_metadata_without_translation_hash_raises(self, tmp_path):
        run_dir = tmp_path / "workspace" / "runs" / "trial-1"
        run_dir.mkdir(parents=True)
        _write_json(run_dir / "run_metadata.json", {"version": 1})
        with pytest.raises(resolve.ResolveError, match="no 'translation_hash' field"):
            resolve.resolve_run(tmp_path, "trial-1")

    def test_unresolvable_translation_hash_raises(self, tmp_path):
        run_dir = tmp_path / "workspace" / "runs" / "trial-1"
        run_dir.mkdir(parents=True)
        # workspace/translations exists but the specific hash doesn't
        (tmp_path / "workspace" / "translations").mkdir()
        _write_json(
            run_dir / "run_metadata.json",
            {
                "version": 1,
                "run_name": "trial-1",
                "translation_hash": "deadbeef" * 8,
                "cluster_id": "c",
                "params_hash": "x",
                "image_tag": "",
                "assembled_at": "",
            },
        )
        with pytest.raises(resolve.ResolveError, match="not found. Rebuild"):
            resolve.resolve_run(tmp_path, "trial-1")


# ── resolve_run partial-state cases ──────────────────────────────────────────


class TestResolveRunPartialStates:
    def test_partial_results_tree_filtered(self, tmp_path):
        """A phase's subdir with no workload trace_data.csv → not in phases_with_data."""
        _make_run(
            tmp_path,
            algorithms=[
                {"name": "sr", "source_path": "algorithms/sr.py",
                 "source_sha256": "a", "image_ref": "r:1", "image_digest": None},
                {"name": "cc", "source_path": "algorithms/cc.py",
                 "source_sha256": "b", "image_ref": "r:2", "image_digest": None},
            ],
            results_layout={"sr": ["wl_a"]},  # cc has no data
        )
        result = resolve.resolve_run(tmp_path, "trial-1")
        assert result["results"]["phases_declared"] == ["baseline", "sr", "cc"]
        assert result["results"]["phases_with_data"] == ["sr"]

    def test_no_collected_data_returns_valid_shape(self, tmp_path):
        _make_run(tmp_path, results_layout={})
        result = resolve.resolve_run(tmp_path, "trial-1")
        assert result["results"]["phases_with_data"] == []
        assert result["results"]["workloads_by_phase"] == {}

    def test_multi_baseline_multi_algorithm_all_present(self, tmp_path):
        _make_run(
            tmp_path,
            algorithms=[
                {"name": "sr", "source_path": "a/sr.py",
                 "source_sha256": "1", "image_ref": "r:1", "image_digest": None},
                {"name": "cc", "source_path": "a/cc.py",
                 "source_sha256": "2", "image_ref": "r:2", "image_digest": None},
            ],
            baselines=[
                {"name": "base_pin", "scenario": "b/pin.yaml"},
                {"name": "base_hipri", "scenario": "b/hipri.yaml"},
            ],
        )
        result = resolve.resolve_run(tmp_path, "trial-1")
        algo_names = [a["name"] for a in result["translation"]["algorithms"]]
        assert algo_names == ["sr", "cc"]
        baseline_names = [b["name"] for b in result["translation"]["baselines"]]
        assert baseline_names == ["base_pin", "base_hipri"]
        # phases_declared = baselines + algorithms in manifest order
        assert result["results"]["phases_declared"] == [
            "base_pin", "base_hipri", "sr", "cc",
        ]

    def test_missing_manifest_assembly_null_path(self, tmp_path):
        _make_run(tmp_path, write_manifest=False)
        result = resolve.resolve_run(tmp_path, "trial-1")
        assert result["manifest_assembly"]["path"] is None
        assert result["manifest_assembly"]["scenario"] == ""
        assert result["manifest_assembly"]["workloads"] == []
        # No manifest → phases_declared and phases_with_data both empty
        assert result["results"]["phases_declared"] == []
        assert result["results"]["phases_with_data"] == []
        # Translation baselines list is empty when manifest is absent
        assert result["translation"]["baselines"] == []

    def test_missing_cluster_config_null(self, tmp_path):
        _make_run(tmp_path, write_cluster_config=False)
        result = resolve.resolve_run(tmp_path, "trial-1")
        assert result["cluster_config_path"] is None


# ── CLI wrapper ──────────────────────────────────────────────────────────────


def _run_cli(experiment_root: Path, *cli_args: str) -> subprocess.CompletedProcess:
    """Invoke pipeline/sim2real.py via subprocess (real CLI path).

    Uses the current Python interpreter and the worktree's pipeline
    module. Returns the CompletedProcess so tests can inspect
    returncode, stdout, and stderr.
    """
    return subprocess.run(
        [
            sys.executable,
            "pipeline/sim2real.py",
            "--experiment-root",
            str(experiment_root),
            "resolve",
            *cli_args,
        ],
        cwd=Path(__file__).resolve().parent.parent.parent,
        capture_output=True,
        text=True,
        check=False,
    )


class TestResolveCLI:
    def test_happy_path_exits_0_with_json(self, tmp_path):
        _make_run(tmp_path)
        result = _run_cli(tmp_path, "--run", "trial-1")
        assert result.returncode == 0, result.stderr
        parsed = json.loads(result.stdout)
        assert parsed["version"] == 1
        assert parsed["run_name"] == "trial-1"

    def test_unknown_run_exits_2_with_message(self, tmp_path):
        (tmp_path / "workspace" / "runs").mkdir(parents=True)
        result = _run_cli(tmp_path, "--run", "does-not-exist")
        assert result.returncode == 2
        assert "run 'does-not-exist' not found" in result.stderr
        assert "sim2real list runs" in result.stderr

    def test_missing_workspace_exits_2_pointing_at_experiment_root(self, tmp_path):
        result = _run_cli(tmp_path, "--run", "trial-1")
        assert result.returncode == 2
        assert "no workspace/" in result.stderr
        assert "--experiment-root" in result.stderr
