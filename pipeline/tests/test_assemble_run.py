"""Unit tests for pipeline/lib/assemble_run.py."""

from __future__ import annotations

import configparser
import hashlib
import json
import subprocess
from pathlib import Path

import pytest
import yaml

from pipeline.lib import assemble_run, layout, slicer


def _init_fake_submodule(sub: Path) -> None:
    """Create a minimal git repo so `git rev-parse HEAD` succeeds inside it."""
    sub.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "trunk", str(sub)], check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "--allow-empty", "-q", "-m", "init"],
        cwd=sub, check=True,
    )


def _write_gitmodules(repo_root: Path, entries: dict[str, str]) -> None:
    """Write a .gitmodules with the given {name: url} pairs."""
    cfg = configparser.ConfigParser()
    for name, url in entries.items():
        cfg[f'submodule "{name}"'] = {"path": name, "url": url}
    with (repo_root / ".gitmodules").open("w") as f:
        cfg.write(f)


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
        kept, skipped = assemble_run.filter_algorithms(
            manifest_algos, translated_names={"a"}
        )
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


class TestLoadDefaultsOverlay:
    def test_merges_all_fragments_alphabetically(self, tmp_path):
        d = tmp_path / "defaults"
        d.mkdir()
        (d / "aaa.yaml").write_text(
            yaml.dump({"scenario": [{"name": "s", "a": 1}]})
        )
        (d / "bbb.yaml").write_text(
            yaml.dump({"scenario": [{"name": "s", "b": 2}]})
        )
        merged = assemble_run.load_defaults_overlay(d, disable=[])
        assert merged == {"scenario": [{"name": "s", "a": 1, "b": 2}]}

    def test_disable_skips_by_stem(self, tmp_path):
        d = tmp_path / "defaults"
        d.mkdir()
        (d / "keep.yaml").write_text(
            yaml.dump({"scenario": [{"name": "s", "k": 1}]})
        )
        (d / "drop.yaml").write_text(
            yaml.dump({"scenario": [{"name": "s", "d": 2}]})
        )
        merged = assemble_run.load_defaults_overlay(d, disable=["drop"])
        assert merged == {"scenario": [{"name": "s", "k": 1}]}

    def test_missing_dir_returns_empty(self, tmp_path):
        assert assemble_run.load_defaults_overlay(tmp_path / "nope", disable=[]) == {}

    def test_none_dir_returns_empty(self):
        assert assemble_run.load_defaults_overlay(None, disable=[]) == {}


class TestInjectImageTag:
    def test_injects_repository_and_tag(self):
        scenario = {"scenario": [{"name": "s"}, {"name": "s2"}]}
        assemble_run.inject_image_tag(scenario, "ghcr.io/foo/bar:v1")
        for entry in scenario["scenario"]:
            img = entry["images"]["inferenceScheduler"]
            assert img == {
                "repository": "ghcr.io/foo/bar",
                "tag": "v1",
                "pullPolicy": "Always",
            }

    def test_digest_ref_keeps_ref_and_empty_tag(self):
        scenario = {"scenario": [{"name": "s"}]}
        digest_ref = "ghcr.io/foo/bar@sha256:" + "a" * 64
        assemble_run.inject_image_tag(scenario, digest_ref)
        img = scenario["scenario"][0]["images"]["inferenceScheduler"]
        assert img["repository"] == digest_ref
        assert img["tag"] == ""

    def test_no_scenario_entries_raises(self):
        with pytest.raises(assemble_run.AssembleError):
            assemble_run.inject_image_tag({"scenario": []}, "ghcr.io/foo/bar:v1")

    def test_missing_scenario_key_raises(self):
        with pytest.raises(assemble_run.AssembleError):
            assemble_run.inject_image_tag({}, "ghcr.io/foo/bar:v1")

    def test_overwrites_existing_inference_scheduler(self):
        scenario = {
            "scenario": [
                {
                    "name": "s",
                    "images": {
                        "inferenceScheduler": {"repository": "old", "tag": "old"}
                    },
                }
            ]
        }
        assemble_run.inject_image_tag(scenario, "ghcr.io/foo/bar:v1")
        img = scenario["scenario"][0]["images"]["inferenceScheduler"]
        assert img["repository"] == "ghcr.io/foo/bar"
        assert img["tag"] == "v1"


class TestWriteManifestAssembly:
    def test_writes_yaml_snapshot_of_assembly_slice(self, tmp_path):
        manifest = {
            "kind": "sim2real-transfer",
            "version": 3,
            "scenario": "test",
            "component": {"repo": "acme/foo"},
            "context": {"text": "", "files": []},
            "baselines": [{"name": "baseline", "scenario": "baselines/base.yaml"}],
            "algorithms": [
                {"name": "sr", "source": "algo/sr.py", "defaults": "baseline"}
            ],
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
        parsed = yaml.safe_load(text)
        assert parsed["workloads"] == ["workloads/w1.yaml"]
        assert parsed["baselines"][0]["name"] == "baseline"
        assert "scenario" not in parsed
        assert "component" not in parsed
        assert parsed["algorithms"] == [{"name": "sr", "defaults": "baseline"}]

    def test_params_hash_is_sha256_of_bytes(self, tmp_path):
        p = tmp_path / "manifest.assembly.yaml"
        p.write_bytes(b"hello\n")
        assert (
            assemble_run.compute_params_hash(p)
            == hashlib.sha256(b"hello\n").hexdigest()
        )

    def test_slicer_round_trip(self, tmp_path):
        manifest = {
            "kind": "sim2real-transfer",
            "version": 3,
            "scenario": "test",
            "workloads": ["w1.yaml", "w2.yaml"],
            "baselines": [{"name": "baseline", "scenario": "b.yaml"}],
            "algorithms": [
                {"name": "sr", "source": "s.py", "defaults": "baseline"}
            ],
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
        assert resolved == {
            "scenario": [{"name": "s", "a": 1, "b": 2, "c": 2, "d": 4}]
        }

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
        assert resolved == {
            "scenario": [{"name": "s", "a": 1, "b": 2, "c": 3, "d": 3}]
        }

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
        pr = yaml.safe_load(
            (cluster_dir / "pipelinerun-wl-a-sr.yaml").read_text()
        )
        assert pr["kind"] == "PipelineRun"
        params = {p["name"]: p["value"] for p in pr["spec"]["params"]}
        assert params["phase"] == "sr"
        assert "name: M" in params["scenarioContent"]
        assert params["workloadName"] == "wl_a"


def _write_yaml(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data, sort_keys=False))


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def _make_experiment(
    tmp_path: Path,
    *,
    algo_names_registered: list[str],
    algo_names_manifest: list[str],
    image_ref: str = "ghcr.io/foo/bar:v1",
) -> dict:
    """Build a minimal experiment on disk and register a translation."""
    exp_root = tmp_path / "exp"
    workspace = exp_root / "workspace"
    layout._EXPERIMENT_ROOT = exp_root

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
    _write_yaml(
        exp_root / "workloads" / "w1.yaml",
        {"name": "wl_a", "num_requests": 10},
    )
    for n in algo_names_manifest:
        (exp_root / "algo").mkdir(exist_ok=True)
        (exp_root / "algo" / f"{n}.py").write_text("# stub\n")

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
            "alias": algo_names_registered[0] if algo_names_registered else None,
            "algorithms": [
                {
                    "name": n,
                    "source_path": None,
                    "source_sha256": None,
                    "config_path": f"generated/{n}/{n}_config.yaml",
                    "image_ref": image_ref,
                    "image_digest": "sha256:aa",
                }
                for n in algo_names_registered
            ],
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
            {
                "scenario": [
                    {
                        "name": "test-scenario",
                        "inferenceExtension": {
                            "pluginsConfigFile": f"{n}.yaml"
                        },
                    }
                ]
            },
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
            translation_ref=fx["translation_hash"],
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
        assert (run_dir / "cluster" / "pipelinerun-wl-a-baseline.yaml").exists()
        assert (run_dir / "cluster" / "pipelinerun-wl-a-sr.yaml").exists()

        meta = json.loads((run_dir / "run_metadata.json").read_text())
        assert meta["version"] == 1
        assert meta["run_name"] == "trial-1"
        assert meta["translation_hash"] == fx["translation_hash"]
        assert meta["cluster_id"] == fx["cluster_id"]
        assert meta["image_tag"] == "ghcr.io/foo/bar:v1"
        assert len(meta["params_hash"]) == 64
        assert meta["assembled_at"] == "2026-07-01T14:05:00Z"

    def test_pipelinerun_params_include_framework_submodule_state(
        self, tmp_path, monkeypatch
    ):
        """assemble() populates benchmarkGit*/blisGit* params from a fake
        framework repo layout (issue #458). Verified end-to-end: build an
        experiment + a fake framework repo with both submodules, patch
        the helper, run assemble, read one generated PipelineRun, check
        the four params."""
        fx = _make_experiment(
            tmp_path,
            algo_names_registered=["sr"],
            algo_names_manifest=["sr"],
        )

        fake_repo = tmp_path / "framework"
        fake_repo.mkdir()
        _write_gitmodules(fake_repo, {
            "inference-sim": "https://example.com/inference-sim.git",
            "llm-d-benchmark": "https://example.com/llm-d-benchmark.git",
        })
        _init_fake_submodule(fake_repo / "inference-sim")
        _init_fake_submodule(fake_repo / "llm-d-benchmark")

        monkeypatch.setattr(
            assemble_run, "_REPO_ROOT", fake_repo,
        )

        assemble_run.assemble_run(
            translation_hash=fx["translation_hash"],
            translation_ref=fx["translation_hash"],
            cluster_id=fx["cluster_id"],
            run_name="trial-1",
            experiment_root=fx["exp_root"],
            manifest_path=fx["manifest_path"],
            force=False,
            now_iso="2026-07-02T00:00:00Z",
        )

        pr = yaml.safe_load(
            (fx["exp_root"] / "workspace/runs/trial-1/cluster/"
             "pipelinerun-wl-a-baseline.yaml").read_text()
        )
        params = {p["name"]: p["value"] for p in pr["spec"]["params"]}
        assert params["benchmarkGitRepoUrl"] == "https://example.com/llm-d-benchmark.git"
        assert params["blisGitRepoUrl"] == "https://example.com/inference-sim.git"
        assert params["benchmarkGitCommit"] not in ("", "unknown")
        assert params["blisGitCommit"] not in ("", "unknown")
        assert assemble_run.assemble_run.missing_submodules == []  # type: ignore[attr-defined]

    def test_missing_submodule_falls_back_to_unknown_and_warns(
        self, tmp_path, monkeypatch
    ):
        """When a framework submodule is missing on disk, assemble()
        still writes the PipelineRuns using 'unknown' as the commit and
        records the name on the side-band attr (issue #458). The URL
        still comes through because it's parsed from .gitmodules."""
        fx = _make_experiment(
            tmp_path,
            algo_names_registered=["sr"],
            algo_names_manifest=["sr"],
        )

        fake_repo = tmp_path / "framework"
        fake_repo.mkdir()
        _write_gitmodules(fake_repo, {
            "inference-sim": "https://example.com/inference-sim.git",
            "llm-d-benchmark": "https://example.com/llm-d-benchmark.git",
        })
        # Only inference-sim is initialized; llm-d-benchmark is missing.
        _init_fake_submodule(fake_repo / "inference-sim")

        monkeypatch.setattr(
            assemble_run, "_REPO_ROOT", fake_repo,
        )

        assemble_run.assemble_run(
            translation_hash=fx["translation_hash"],
            translation_ref=fx["translation_hash"],
            cluster_id=fx["cluster_id"],
            run_name="trial-1",
            experiment_root=fx["exp_root"],
            manifest_path=fx["manifest_path"],
            force=False,
            now_iso="2026-07-02T00:00:00Z",
        )

        pr = yaml.safe_load(
            (fx["exp_root"] / "workspace/runs/trial-1/cluster/"
             "pipelinerun-wl-a-baseline.yaml").read_text()
        )
        params = {p["name"]: p["value"] for p in pr["spec"]["params"]}
        assert params["benchmarkGitCommit"] == "unknown"
        assert params["blisGitCommit"] not in ("", "unknown")
        # URL comes from .gitmodules — not affected by the missing dir.
        assert params["benchmarkGitRepoUrl"] == "https://example.com/llm-d-benchmark.git"
        assert assemble_run.assemble_run.missing_submodules == ["llm-d-benchmark"]  # type: ignore[attr-defined]

    def test_filters_unregistered_algorithms(self, tmp_path):
        fx = _make_experiment(
            tmp_path,
            algo_names_registered=["sr"],
            algo_names_manifest=["sr", "constantceiling"],
        )
        assemble_run.assemble_run(
            translation_hash=fx["translation_hash"],
            translation_ref=fx["translation_hash"],
            cluster_id=fx["cluster_id"],
            run_name="trial-1",
            experiment_root=fx["exp_root"],
            manifest_path=fx["manifest_path"],
            force=False,
            now_iso="2026-07-01T14:05:00Z",
        )
        run_dir = fx["exp_root"] / "workspace" / "runs" / "trial-1"
        assert (run_dir / "cluster" / "sr.yaml").exists()
        assert not (run_dir / "cluster" / "constantceiling.yaml").exists()
        # Skipped algorithms are surfaced for the CLI wrapper.
        assert "constantceiling" in getattr(
            assemble_run.assemble_run, "skipped_algorithms", []
        )

    def test_treatment_scenario_carries_image_tag(self, tmp_path):
        fx = _make_experiment(
            tmp_path,
            algo_names_registered=["sr"],
            algo_names_manifest=["sr"],
        )
        assemble_run.assemble_run(
            translation_hash=fx["translation_hash"],
            translation_ref=fx["translation_hash"],
            cluster_id=fx["cluster_id"],
            run_name="trial-1",
            experiment_root=fx["exp_root"],
            manifest_path=fx["manifest_path"],
            force=False,
            now_iso="2026-07-01T14:05:00Z",
        )
        sr_yaml = yaml.safe_load(
            (
                fx["exp_root"]
                / "workspace"
                / "runs"
                / "trial-1"
                / "cluster"
                / "sr.yaml"
            ).read_text()
        )
        img = sr_yaml["scenario"][0]["images"]["inferenceScheduler"]
        assert img["repository"] == "ghcr.io/foo/bar"
        assert img["tag"] == "v1"
        baseline_yaml = yaml.safe_load(
            (
                fx["exp_root"]
                / "workspace"
                / "runs"
                / "trial-1"
                / "cluster"
                / "baseline.yaml"
            ).read_text()
        )
        assert "inferenceScheduler" not in (
            baseline_yaml["scenario"][0].get("images") or {}
        )

    def test_params_hash_matches_manifest_assembly_bytes(self, tmp_path):
        fx = _make_experiment(
            tmp_path,
            algo_names_registered=["sr"],
            algo_names_manifest=["sr"],
        )
        assemble_run.assemble_run(
            translation_hash=fx["translation_hash"],
            translation_ref=fx["translation_hash"],
            cluster_id=fx["cluster_id"],
            run_name="trial-1",
            experiment_root=fx["exp_root"],
            manifest_path=fx["manifest_path"],
            force=False,
            now_iso="2026-07-01T14:05:00Z",
        )
        run_dir = fx["exp_root"] / "workspace" / "runs" / "trial-1"
        expected = hashlib.sha256(
            (run_dir / "manifest.assembly.yaml").read_bytes()
        ).hexdigest()
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
                translation_ref=fx["translation_hash"],
                cluster_id=fx["cluster_id"],
                run_name="trial-1",
                experiment_root=fx["exp_root"],
                manifest_path=fx["manifest_path"],
                force=False,
                now_iso="2026-07-01T14:05:00Z",
            )
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
            translation_ref=fx["translation_hash"],
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
                translation_ref="0" * 64,
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
                translation_ref=fx["translation_hash"],
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
        (fx["exp_root"] / "workloads" / "w1.yaml").unlink()
        with pytest.raises(assemble_run.AssembleError, match="workload"):
            assemble_run.assemble_run(
                translation_hash=fx["translation_hash"],
                translation_ref=fx["translation_hash"],
                cluster_id=fx["cluster_id"],
                run_name="trial-1",
                experiment_root=fx["exp_root"],
                manifest_path=fx["manifest_path"],
                force=False,
                now_iso="2026-07-01T14:05:00Z",
            )


class TestDiscoverFrameworkSubmodules:
    """``discover_framework_submodules(repo_root)`` reads submodule state
    from the framework repo layout: ``.gitmodules`` for URLs, ``git
    rev-parse HEAD`` for SHAs, and returns a sorted list of missing
    submodule names for the CLI wrapper to warn about."""

    def test_both_submodules_present(self, tmp_path):
        _write_gitmodules(tmp_path, {
            "inference-sim": "https://github.com/inference-sim/inference-sim.git",
            "llm-d-benchmark": "https://github.com/llm-d/llm-d-benchmark.git",
        })
        _init_fake_submodule(tmp_path / "inference-sim")
        _init_fake_submodule(tmp_path / "llm-d-benchmark")

        shas, urls, missing = assemble_run.discover_framework_submodules(tmp_path)

        assert set(shas) == {"inference-sim", "llm-d-benchmark"}
        assert shas["inference-sim"] != "unknown"
        assert shas["llm-d-benchmark"] != "unknown"
        assert urls == {
            "inference-sim": "https://github.com/inference-sim/inference-sim.git",
            "llm-d-benchmark": "https://github.com/llm-d/llm-d-benchmark.git",
        }
        assert missing == []

    def test_one_missing_reports_it(self, tmp_path):
        """Submodule dir absent → name in missing list; shas entry is
        'unknown'; url entry is still populated from .gitmodules."""
        _write_gitmodules(tmp_path, {
            "inference-sim": "https://github.com/inference-sim/inference-sim.git",
            "llm-d-benchmark": "https://github.com/llm-d/llm-d-benchmark.git",
        })
        _init_fake_submodule(tmp_path / "inference-sim")
        # llm-d-benchmark deliberately missing on disk.

        shas, urls, missing = assemble_run.discover_framework_submodules(tmp_path)

        assert shas["inference-sim"] != "unknown"
        assert shas["llm-d-benchmark"] == "unknown"
        assert missing == ["llm-d-benchmark"]
        # URL still populated from .gitmodules even though the dir is missing.
        assert urls["llm-d-benchmark"] == "https://github.com/llm-d/llm-d-benchmark.git"

    def test_both_missing(self, tmp_path):
        """Neither submodule on disk; .gitmodules present. All three
        return values populate with unknown/empty state, no crash."""
        _write_gitmodules(tmp_path, {
            "inference-sim": "https://github.com/inference-sim/inference-sim.git",
            "llm-d-benchmark": "https://github.com/llm-d/llm-d-benchmark.git",
        })

        shas, urls, missing = assemble_run.discover_framework_submodules(tmp_path)

        assert shas == {"inference-sim": "unknown", "llm-d-benchmark": "unknown"}
        assert urls == {
            "inference-sim": "https://github.com/inference-sim/inference-sim.git",
            "llm-d-benchmark": "https://github.com/llm-d/llm-d-benchmark.git",
        }
        assert missing == ["inference-sim", "llm-d-benchmark"]

    def test_no_gitmodules_file(self, tmp_path):
        """No .gitmodules at repo_root → URLs default to empty string; SHAs
        still probed. Matches legacy 'do not crash if the file is absent'."""
        _init_fake_submodule(tmp_path / "inference-sim")

        shas, urls, missing = assemble_run.discover_framework_submodules(tmp_path)

        assert shas["inference-sim"] != "unknown"
        assert shas["llm-d-benchmark"] == "unknown"
        assert urls == {"inference-sim": "", "llm-d-benchmark": ""}
        assert missing == ["llm-d-benchmark"]

    def test_extra_gitmodules_entry_ignored(self, tmp_path):
        """.gitmodules may list submodules other than the framework pair
        (e.g. tektonc-data-collection). Only the pinned pair is returned."""
        _write_gitmodules(tmp_path, {
            "inference-sim": "https://github.com/inference-sim/inference-sim.git",
            "llm-d-benchmark": "https://github.com/llm-d/llm-d-benchmark.git",
            "tektonc-data-collection": "https://github.com/x/y.git",
        })
        _init_fake_submodule(tmp_path / "inference-sim")
        _init_fake_submodule(tmp_path / "llm-d-benchmark")

        shas, urls, missing = assemble_run.discover_framework_submodules(tmp_path)

        assert set(shas) == {"inference-sim", "llm-d-benchmark"}
        assert set(urls) == {"inference-sim", "llm-d-benchmark"}
        assert "tektonc-data-collection" not in shas
        assert "tektonc-data-collection" not in urls


class TestLegacyShapeShim:
    def test_legacy_top_level_image_ref_still_resolvable(
        self, tmp_path, monkeypatch
    ):
        # Simulate a step-1 BYO translation_output.json on disk.
        from pipeline.lib import layout, translation_ref
        layout.set_experiment_root(tmp_path)
        thash = "a" * 64
        tdir = layout.translation_dir(thash)
        tdir.mkdir(parents=True)
        (tdir / "translation_output.json").write_text(json.dumps({
            "version": 1,
            "translation_hash": thash,
            "source": "byo",
            "algorithms": [{"name": "ac1"}],
            "image_ref": "quay.io/legacy:v1",
            "created_at": "2026-06-01T10:00:00Z",
        }))
        # Shim normalizes it: algorithms[0].image_ref is filled in.
        data = translation_ref.read_translation_output(
            tdir / "translation_output.json"
        )
        assert data["algorithms"][0]["image_ref"] == "quay.io/legacy:v1"
        assert data["alias"] is None


class TestIncompleteTranslation:
    def test_missing_image_ref_raises_build_first(self, tmp_path):
        from pipeline.lib import assemble_run, layout
        layout.set_experiment_root(tmp_path)
        thash = "a" * 64
        tdir = layout.translation_dir(thash)
        tdir.mkdir(parents=True)
        (tdir / "translation_output.json").write_text(json.dumps({
            "version": 1,
            "translation_hash": thash,
            "source": "skill",
            "alias": "not-built",
            "algorithms": [{
                "name": "ac1", "source_path": "a.py",
                "source_sha256": "…", "config_path": None,
                "image_ref": None, "image_digest": None,
            }],
            "created_at": "2026-07-02T14:00:00Z",
        }))
        manifest_path = tmp_path / "transfer.yaml"
        manifest_path.write_text(
            "kind: sim2real-transfer\n"
            "version: 3\n"
            "scenario: test\n"
            "component: {repo: acme/foo, kind: gaie}\n"
            "algorithms: [{name: ac1, source: algo/ac1.py, defaults: b1}]\n"
            "baselines: [{name: b1, scenario: baseline.yaml}]\n"
            "workloads: []\n"
        )
        (tmp_path / "baseline.yaml").write_text("scenario: [{a: 1}]\n")
        # Cluster config seed.
        cluster_cfg_path = layout.cluster_config_path("cX")
        cluster_cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cluster_cfg_path.write_text(json.dumps({
            "id": "cX", "namespaces": ["ns1"], "workspaces": {},
        }))
        # Regression-protect the design-verbatim message shape: the user's
        # typed ref appears BOTH in the failure line and in the suggested
        # `sim2real build --translation <ref>` command, joined by an em-dash
        # (U+2014, not "--").
        with pytest.raises(
            assemble_run.AssembleError,
            match=r"translation not-built not built for algorithms: ac1 — "
                  r"run 'sim2real build --translation not-built' first",
        ):
            assemble_run.assemble_run(
                translation_hash=thash,
                translation_ref="not-built",
                cluster_id="cX",
                run_name="r1",
                experiment_root=tmp_path,
                manifest_path=manifest_path,
                force=False,
                now_iso="2026-07-02T14:00:00Z",
            )
