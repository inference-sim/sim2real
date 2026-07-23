"""Unit tests for pipeline/lib/assemble_run.py."""

from __future__ import annotations

import configparser
import json
import subprocess
from pathlib import Path

import pytest
import yaml

from pipeline.lib import assemble_run, layout, resolve, slicer


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
            img = entry["router"]["epp"]["image"]
            assert img == {
                "registry": "ghcr.io/foo",
                "repository": "bar",
                "tag": "v1",
                "pullPolicy": "Always",
            }

    def test_digest_ref_keeps_ref_and_empty_tag(self):
        scenario = {"scenario": [{"name": "s"}]}
        digest_ref = "ghcr.io/foo/bar@sha256:" + "a" * 64
        assemble_run.inject_image_tag(scenario, digest_ref)
        img = scenario["scenario"][0]["router"]["epp"]["image"]
        # Digest ref: last "/" segment becomes bare repo (includes the @sha256 suffix)
        assert img["registry"] == "ghcr.io/foo"
        assert img["repository"] == "bar@sha256:" + "a" * 64
        assert img["tag"] == ""

    def test_no_scenario_entries_raises(self):
        with pytest.raises(assemble_run.AssembleError):
            assemble_run.inject_image_tag({"scenario": []}, "ghcr.io/foo/bar:v1")

    def test_missing_scenario_key_raises(self):
        with pytest.raises(assemble_run.AssembleError):
            assemble_run.inject_image_tag({}, "ghcr.io/foo/bar:v1")

    def test_bare_image_no_registry(self):
        """A ref with no '/' becomes bare-repo + empty registry (mirrors
        epp.inject_image_ref's test_bare_repository_no_registry)."""
        scenario = {"scenario": [{"name": "s"}]}
        assemble_run.inject_image_tag(scenario, "myimage:v1")
        img = scenario["scenario"][0]["router"]["epp"]["image"]
        assert img["registry"] == ""
        assert img["repository"] == "myimage"
        assert img["tag"] == "v1"
        assert img["pullPolicy"] == "Always"

    def test_overwrites_existing_epp_image(self):
        scenario = {
            "scenario": [
                {
                    "name": "s",
                    "router": {"epp": {"image": {
                        "registry": "old", "repository": "old", "tag": "old"
                    }}},
                }
            ]
        }
        assemble_run.inject_image_tag(scenario, "ghcr.io/foo/bar:v1")
        img = scenario["scenario"][0]["router"]["epp"]["image"]
        assert img["registry"] == "ghcr.io/foo"
        assert img["repository"] == "bar"
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

    def test_writes_replicas_field_when_provided(self, tmp_path):
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
        run_dir = tmp_path / "runs" / "r"
        run_dir.mkdir(parents=True)
        p = assemble_run.write_manifest_assembly(
            run_dir, manifest, now_iso="2026-07-01T14:05:00Z", replicas=3
        )
        parsed = yaml.safe_load(p.read_text())
        assert parsed["replicas"] == 3

    def test_writes_replicas_defaults_to_one(self, tmp_path):
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
        run_dir = tmp_path / "runs" / "r"
        run_dir.mkdir(parents=True)
        p = assemble_run.write_manifest_assembly(
            run_dir, manifest, now_iso="2026-07-01T14:05:00Z"
        )
        parsed = yaml.safe_load(p.read_text())
        assert parsed["replicas"] == 1

    def test_params_hash_excludes_replicas_field(self, tmp_path):
        """params_hash is stable when only the `replicas` field changes."""
        p1 = tmp_path / "a.yaml"
        p2 = tmp_path / "b.yaml"
        p1.write_text(
            "replicas: 3\nworkloads:\n- w1\nbaselines:\n- {name: b, scenario: b.yaml}\n"
        )
        p2.write_text(
            "replicas: 5\nworkloads:\n- w1\nbaselines:\n- {name: b, scenario: b.yaml}\n"
        )
        assert (
            assemble_run.compute_params_hash(p1)
            == assemble_run.compute_params_hash(p2)
        )

    def test_params_hash_changes_when_content_changes(self, tmp_path):
        """params_hash differs when non-replicas content changes."""
        p1 = tmp_path / "a.yaml"
        p2 = tmp_path / "b.yaml"
        p1.write_text(
            "replicas: 3\nworkloads:\n- w1\nbaselines:\n- {name: b, scenario: b.yaml}\n"
        )
        p2.write_text(
            "replicas: 3\nworkloads:\n- w2\nbaselines:\n- {name: b, scenario: b.yaml}\n"
        )
        assert (
            assemble_run.compute_params_hash(p1)
            != assemble_run.compute_params_hash(p2)
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
        # replicas is prepended by write_manifest_assembly; strip before comparing
        # against the raw assembly slice.
        assert reparsed.pop("replicas") == 1
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

    def test_framework_defaults_named_defaults_merge_into_baseline_entry(self, tmp_path):
        """Regression test for issue #516.

        Framework-default fragments under ``baselines/defaults/*.yaml`` are
        authored with a placeholder ``scenario[0].name = "defaults"``. When the
        experiment's baseline entry is named anything else (i.e. every real
        experiment), the two must still merge into a SINGLE scenario entry —
        otherwise llm-d-benchmark templates the placeholder as an independent
        deployment inheriting the framework-default model (facebook/opt-125m).

        Before the fix, ``resolve_baseline`` produced two scenario entries
        (``[{name: defaults, ...}, {name: <baseline>, ...}]``) and a phantom
        facebook/opt-125m deployment materialized alongside the intended one.
        """
        bundle = {
            "scenario": [{
                "name": "sr",
                "model": {"name": "Qwen/Qwen3-14B", "path": "models/Qwen"},
                "decode": {"replicas": 2},
            }],
        }
        framework_defaults = {
            "scenario": [{
                "name": "defaults",  # placeholder; realigned at merge time
                "gateway": {"externallyManaged": True},
                "inferenceExtension": {"verbosity": "5"},
                "extraObjects": [{"apiVersion": "v1", "kind": "Role",
                                  "metadata": {"name": "epp-role"}}],
            }],
        }
        bundle_path = tmp_path / "sr.yaml"
        self._write(bundle_path, bundle)

        resolved = assemble_run.resolve_baseline(
            bundle_path=bundle_path,
            overlay_path=None,
            framework_defaults=framework_defaults,
        )

        # 1) Single scenario entry, carrying the baseline's name (not "defaults").
        assert len(resolved["scenario"]) == 1
        entry = resolved["scenario"][0]
        assert entry["name"] == "sr"

        # 2) Baseline content preserved (model + decode).
        assert entry["model"]["name"] == "Qwen/Qwen3-14B"
        assert entry["decode"]["replicas"] == 2

        # 3) Framework-defaults content merged in.
        assert entry["gateway"]["externallyManaged"] is True
        assert entry["inferenceExtension"]["verbosity"] == "5"
        assert entry["extraObjects"] == [
            {"apiVersion": "v1", "kind": "Role",
             "metadata": {"name": "epp-role"}}
        ]

    def test_framework_defaults_no_op_when_names_already_match(self, tmp_path):
        """When both sides already have matching names, no realignment needed."""
        bundle = {"scenario": [{"name": "same", "a": 1}]}
        framework_defaults = {"scenario": [{"name": "same", "b": 2}]}
        bundle_path = tmp_path / "b.yaml"
        self._write(bundle_path, bundle)
        resolved = assemble_run.resolve_baseline(
            bundle_path=bundle_path,
            overlay_path=None,
            framework_defaults=framework_defaults,
        )
        assert resolved == {"scenario": [{"name": "same", "a": 1, "b": 2}]}

    def test_framework_defaults_empty_scenario_no_op(self, tmp_path):
        """Empty framework_defaults must not corrupt the bundle."""
        bundle = {"scenario": [{"name": "sr", "a": 1}]}
        bundle_path = tmp_path / "b.yaml"
        self._write(bundle_path, bundle)
        resolved = assemble_run.resolve_baseline(
            bundle_path=bundle_path,
            overlay_path=None,
            framework_defaults={},
        )
        assert resolved == bundle

    def test_align_overlay_name_does_not_mutate_caller_state(self, tmp_path):
        """resolve_baseline must not mutate the caller's framework_defaults dict."""
        bundle = {"scenario": [{"name": "sr"}]}
        framework_defaults = {"scenario": [{"name": "defaults", "x": 1}]}
        original_snapshot = {
            "scenario": [{"name": "defaults", "x": 1}],
        }
        bundle_path = tmp_path / "b.yaml"
        self._write(bundle_path, bundle)
        assemble_run.resolve_baseline(
            bundle_path=bundle_path,
            overlay_path=None,
            framework_defaults=framework_defaults,
        )
        # Caller's dict is untouched — the helper deep-copies before renaming.
        assert framework_defaults == original_snapshot


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
            "pipelinerun-wl-a|baseline|i1.yaml",
            "pipelinerun-wl-a|sr|i1.yaml",
            "pipelinerun-wl-b|baseline|i1.yaml",
            "pipelinerun-wl-b|sr|i1.yaml",
        ]
        pr = yaml.safe_load(
            (cluster_dir / "pipelinerun-wl-a|sr|i1.yaml").read_text()
        )
        assert pr["kind"] == "PipelineRun"
        params = {p["name"]: p["value"] for p in pr["spec"]["params"]}
        assert params["phase"] == "sr"
        assert "name: M" in params["scenarioContent"]
        assert params["workloadName"] == "wl_a"

    def test_iterations_range_emits_pipe_shape_filenames(self, tmp_path):
        """iterations=range(1, 3) → 2 files per (workload, package) with |i1, |i2."""
        run_dir = tmp_path / "runs" / "trial-1"
        cluster_dir = run_dir / "cluster"
        cluster_dir.mkdir(parents=True)
        packages = [
            ("baseline", {"scenario": [{"name": "s", "model": {"name": "M"}}]}),
        ]
        workloads = [{"name": "wl-a", "num_requests": 10}]
        cluster_config = {"namespaces": ["ns-0"], "workspaces": {}}
        assemble_run.generate_pipelineruns(
            run_dir=run_dir,
            packages=packages,
            workloads=workloads,
            run_name="trial-1",
            cluster_config=cluster_config,
            pipeline_name="sim2real",
            observe={},
            model_name="M",
            submodule_shas={},
            submodule_urls={},
            iterations=range(1, 3),
        )
        yamls = sorted(p.name for p in cluster_dir.glob("pipelinerun-*.yaml"))
        assert yamls == [
            "pipelinerun-wl-a|baseline|i1.yaml",
            "pipelinerun-wl-a|baseline|i2.yaml",
        ]
        pr = yaml.safe_load(
            (cluster_dir / "pipelinerun-wl-a|baseline|i2.yaml").read_text()
        )
        assert pr["metadata"]["name"] == "baseline-wl-a-trial-1-i2"

    def test_each_iteration_carries_matching_replica_param(self, tmp_path):
        """Each generated PipelineRun YAML must carry replica=str(iteration)."""
        run_dir = tmp_path / "runs" / "trial-1"
        cluster_dir = run_dir / "cluster"
        cluster_dir.mkdir(parents=True)
        packages = [
            ("baseline", {"scenario": [{"name": "s", "model": {"name": "M"}}]}),
        ]
        workloads = [{"name": "wl-a", "num_requests": 10}]
        cluster_config = {"namespaces": ["ns-0"], "workspaces": {}}
        assemble_run.generate_pipelineruns(
            run_dir=run_dir,
            packages=packages,
            workloads=workloads,
            run_name="trial-1",
            cluster_config=cluster_config,
            pipeline_name="sim2real",
            observe={},
            model_name="M",
            submodule_shas={},
            submodule_urls={},
            iterations=range(1, 4),
        )
        for n in (1, 2, 3):
            pr = yaml.safe_load(
                (cluster_dir / f"pipelinerun-wl-a|baseline|i{n}.yaml").read_text()
            )
            params = {p["name"]: p["value"] for p in pr["spec"]["params"]}
            assert params["replica"] == str(n), (
                f"iteration {n}: expected replica='{n}', got {params['replica']!r}"
            )

    def test_oversized_pipelinerun_name_raises_assemble_error(self, tmp_path):
        """A run_name that pushes PR name over 253 chars must raise
        AssembleError from generate_pipelineruns, not a raw ValueError.
        The assemble CLI boundary catches AssembleError only, so any
        ValueError leaking through would surface as a raw Python traceback."""
        run_dir = tmp_path / "runs" / "long"
        cluster_dir = run_dir / "cluster"
        cluster_dir.mkdir(parents=True)
        packages = [
            ("baseline", {"scenario": [{"name": "s", "model": {"name": "M"}}]}),
        ]
        # Push the constructed name (`baseline-wl-a-<run_name>-i1`) over 253 chars.
        workloads = [{"name": "wl-a", "num_requests": 10}]
        cluster_config = {"namespaces": ["ns-0"], "workspaces": {}}
        long_run_name = "r" * 245
        with pytest.raises(assemble_run.AssembleError, match="253"):
            assemble_run.generate_pipelineruns(
                run_dir=run_dir,
                packages=packages,
                workloads=workloads,
                run_name=long_run_name,
                cluster_config=cluster_config,
                pipeline_name="sim2real",
                observe={},
                model_name="M",
                submodule_shas={},
                submodule_urls={},
            )


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
        assert (run_dir / "cluster" / "pipelinerun-wl-a|baseline|i1.yaml").exists()
        assert (run_dir / "cluster" / "pipelinerun-wl-a|sr|i1.yaml").exists()

        meta = json.loads((run_dir / "run_metadata.json").read_text())
        assert meta["version"] == 1
        assert meta["run_name"] == "trial-1"
        assert meta["translation_hash"] == fx["translation_hash"]
        assert meta["cluster_id"] == fx["cluster_id"]
        assert meta["image_tag"] == "ghcr.io/foo/bar:v1"
        assert len(meta["params_hash"]) == 64
        assert meta["assembled_at"] == "2026-07-01T14:05:00Z"
        # scenario is recorded so deploy.py can scope the progress
        # ConfigMap per experiment root (#551).
        assert meta["scenario"] == "test-scenario"

    def test_fresh_run_with_replicas_3_produces_three_files_per_pair(self, tmp_path):
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
            replicas=3,
            now_iso="2026-07-01T14:05:00Z",
        )
        cluster = fx["exp_root"] / "workspace" / "runs" / "trial-1" / "cluster"
        names = sorted(p.name for p in cluster.glob("pipelinerun-*.yaml"))
        assert names == [
            "pipelinerun-wl-a|baseline|i1.yaml",
            "pipelinerun-wl-a|baseline|i2.yaml",
            "pipelinerun-wl-a|baseline|i3.yaml",
            "pipelinerun-wl-a|sr|i1.yaml",
            "pipelinerun-wl-a|sr|i2.yaml",
            "pipelinerun-wl-a|sr|i3.yaml",
        ]
        # manifest.assembly.yaml records replicas
        manifest_assembly = yaml.safe_load(
            (fx["exp_root"] / "workspace/runs/trial-1/manifest.assembly.yaml")
            .read_text()
        )
        assert manifest_assembly["replicas"] == 3

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
             "pipelinerun-wl-a|baseline|i1.yaml").read_text()
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
             "pipelinerun-wl-a|baseline|i1.yaml").read_text()
        )
        params = {p["name"]: p["value"] for p in pr["spec"]["params"]}
        assert params["benchmarkGitCommit"] == "unknown"
        assert params["blisGitCommit"] not in ("", "unknown")
        # URL comes from .gitmodules — not affected by the missing dir.
        assert params["benchmarkGitRepoUrl"] == "https://example.com/llm-d-benchmark.git"
        assert assemble_run.assemble_run.missing_submodules == ["llm-d-benchmark"]  # type: ignore[attr-defined]

    def test_skill_driven_per_baseline_overlay_is_applied(self, tmp_path):
        """The PR's headline contract: assemble reads the per-baseline
        overlay at ``generated/baselines/<name>/baseline_config.yaml``
        (skill-driven layout, issue #544) and applies it to that
        baseline. If the primary path in ``assemble_run.py`` regressed
        to the wrong
        subpath — or reverted to the shared root — this test would
        fail.
        """
        fx = _make_experiment(
            tmp_path,
            algo_names_registered=["sr"],
            algo_names_manifest=["sr"],
        )
        # Simulate a skill-driven translation: write ONLY the
        # per-baseline overlay under generated/baselines/<name>/. No
        # legacy top-level file — ensures the primary branch is under
        # test, not the fallback. Layout matches issue #544.
        tdir = fx["exp_root"] / "workspace" / "translations" / fx["translation_hash"]
        per_baseline_overlay = (
            tdir / "generated" / "baselines" / "baseline" / "baseline_config.yaml"
        )
        marker_extra_object = {
            "kind": "InferenceObjective",
            "metadata": {"name": "skill-marker"},
        }
        _write_yaml(
            per_baseline_overlay,
            {"scenario": [{"name": "test-scenario", "extraObjects": [marker_extra_object]}]},
        )
        # Precondition: the legacy top-level file must NOT exist so that
        # a passing assertion proves the primary branch (not the
        # fallback) resolved the overlay.
        assert not (tdir / "generated" / "baseline_config.yaml").exists()

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
        baseline_yaml = yaml.safe_load((run_dir / "cluster" / "baseline.yaml").read_text())
        scenarios = baseline_yaml["scenario"]
        scenario = next(s for s in scenarios if s["name"] == "test-scenario")
        assert scenario["extraObjects"] == [marker_extra_object]

    def test_byo_legacy_baseline_overlay_at_generated_root_is_applied(self, tmp_path):
        """BYO ``translation register`` writes a single shared overlay at
        ``translations/<hash>/generated/baseline_config.yaml`` (no
        per-baseline subdirectory). Assemble must fall back to that path
        when the per-baseline dir is absent, or every BYO translation
        loses its baseline overlay content silently.
        """
        fx = _make_experiment(
            tmp_path,
            algo_names_registered=["sr"],
            algo_names_manifest=["sr"],
        )
        # Simulate BYO translation register: write ONLY the legacy shared
        # overlay path — no ``generated/baselines/<name>/`` subdirectory.
        tdir = fx["exp_root"] / "workspace" / "translations" / fx["translation_hash"]
        legacy_overlay = tdir / "generated" / "baseline_config.yaml"
        marker_extra_object = {"kind": "InferenceObjective", "metadata": {"name": "byo-marker"}}
        _write_yaml(
            legacy_overlay,
            {"scenario": [{"name": "test-scenario", "extraObjects": [marker_extra_object]}]},
        )
        # Guard the precondition: per-baseline dir must NOT exist so the
        # code path under test is the legacy-fallback branch.
        assert not (tdir / "generated" / "baselines" / "baseline" / "baseline_config.yaml").exists()

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
        baseline_yaml = yaml.safe_load((run_dir / "cluster" / "baseline.yaml").read_text())
        # The legacy overlay's extraObjects should have deep-merged onto
        # the baseline scenario. If the fallback regressed to overlay_path=None,
        # this field would be absent.
        scenarios = baseline_yaml["scenario"]
        scenario = next(s for s in scenarios if s["name"] == "test-scenario")
        assert scenario["extraObjects"] == [marker_extra_object]

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
        img = sr_yaml["scenario"][0]["router"]["epp"]["image"]
        assert img["registry"] == "ghcr.io/foo"
        assert img["repository"] == "bar"
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
        baseline_router = baseline_yaml["scenario"][0].get("router") or {}
        baseline_epp = baseline_router.get("epp") or {}
        assert "image" not in baseline_epp

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
        expected = assemble_run.compute_params_hash(
            run_dir / "manifest.assembly.yaml"
        )
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


class TestAssembleResolveContract:
    """Couples assemble→resolve on the same fixture to catch silent schema
    drift between what assemble writes and what resolve consumes. A pure
    unit test on either side alone can't catch drift — both would evolve
    together against the same fixture. This test asserts the on-disk
    contract instead.
    """

    def test_resolve_reads_what_assemble_writes(self, tmp_path):
        fx = _make_experiment(
            tmp_path,
            algo_names_registered=["sr"],
            algo_names_manifest=["sr"],
        )
        # The baseline overlay is a translate-time artifact: neither
        # _make_experiment nor assemble_run writes it, but resolve
        # advertises translation.baselines[].generated_overlay_path
        # unconditionally. Stub it so the path-existence walk below
        # is unqualified.
        baseline_overlay = (
            fx["exp_root"] / "workspace" / "translations"
            / fx["translation_hash"] / "generated"
            / "baselines" / "baseline" / "baseline_config.yaml"
        )
        baseline_overlay.parent.mkdir(parents=True, exist_ok=True)
        baseline_overlay.write_text("{}\n")

        run_name = "trial-1"
        assemble_run.assemble_run(
            translation_hash=fx["translation_hash"],
            translation_ref=fx["translation_hash"],
            cluster_id=fx["cluster_id"],
            run_name=run_name,
            experiment_root=fx["exp_root"],
            manifest_path=fx["manifest_path"],
            force=False,
            now_iso="2026-07-04T00:00:00Z",
        )

        view = resolve.resolve_run(fx["exp_root"], run_name)

        # (1) Every path field resolve advertises exists on disk.
        # results.results_dir is excluded — the ``collect`` command
        # populates results/, not ``assemble``; resolve exposes the
        # path as a pointer, not an existence claim.
        expected_paths = [
            view["run_dir"],
            view["cluster_config_path"],
            view["experiment_root"],
            view["translation"]["translations_dir"],
            view["translation"]["generated_dir"],
            view["manifest_assembly"]["path"],
            view["cluster_scenarios"]["cluster_dir"],
            view["cluster_scenarios"]["baseline_yaml"],
            *view["cluster_scenarios"]["treatment_yamls"].values(),
            *view["cluster_scenarios"]["pipelinerun_yamls"],
            *[a["generated_dir"] for a in view["translation"]["algorithms"]],
            *[a["config_path"] for a in view["translation"]["algorithms"]],
            *[b["generated_overlay_path"] for b in view["translation"]["baselines"]],
        ]
        for p in expected_paths:
            assert p is not None, "resolve returned None for a path we expected to exist"
            assert Path(p).exists(), f"resolve JSON references missing path: {p}"

        # (2) Value round-trip on the fields both sides share.
        run_meta_path = (
            fx["exp_root"] / "workspace" / "runs" / run_name / "run_metadata.json"
        )
        run_meta = json.loads(run_meta_path.read_text())
        assert view["run_name"] == run_meta["run_name"]
        assert view["cluster_id"] == run_meta["cluster_id"]
        assert view["translation"]["hash"] == run_meta["translation_hash"]
        assert view["image_tag"] == run_meta["image_tag"]
        assert view["params_hash"] == run_meta["params_hash"]
        assert view["assembled_at"] == run_meta["assembled_at"]

        # (3) Workloads round-trip: transfer.yaml → manifest.assembly.yaml → view.
        assert view["manifest_assembly"]["workloads"] == ["workloads/w1.yaml"]

        # (4) Schema-completeness: every key assemble writes to
        # run_metadata.json is one resolve knows about. Adding a new
        # field to run_metadata.json without extending resolve (and
        # this whitelist) will fire this assertion — the intended
        # drift-detection signal for this test.
        known_keys = {
            "version",
            "run_name",
            "translation_hash",
            "cluster_id",
            "params_hash",
            "image_tag",
            "replicas",
            "assembled_at",
            # Recorded so deploy.py can scope the progress ConfigMap
            # per (scenario, run) (#551). Surfaced in resolve's top-level
            # dict as `scenario` (with fallback to manifest.assembly.yaml
            # for legacy runs).
            "scenario",
        }
        extra = set(run_meta.keys()) - known_keys
        assert not extra, (
            f"assemble writes keys not in resolve's known set: {sorted(extra)}. "
            f"Add the field to resolve_run's return dict and to this whitelist."
        )


class TestLoadWorkloadTraceValidation:
    """`_load_workload` validates the trace-workload shape and rejects
    ambiguous workloads that declare both trace and generative keys."""

    _VALID_TRACE = {
        "name": "exgentic-agentic-trace",
        "trace": {
            "source": "hf:Exgentic/agent-llm-traces",
            "shards": 39,
            "pool": {"concurrent_sessions": 128, "total_sessions": 192},
        },
    }

    def _write(self, tmp_path: Path, data) -> Path:
        exp_root = tmp_path / "exp"
        (exp_root / "workloads").mkdir(parents=True, exist_ok=True)
        (exp_root / "workloads" / "w.yaml").write_text(yaml.dump(data))
        return exp_root

    def test_valid_trace_workload_loads(self, tmp_path):
        exp_root = self._write(tmp_path, self._VALID_TRACE)
        data = assemble_run._load_workload(exp_root, "workloads/w.yaml")
        assert data["trace"]["source"] == "hf:Exgentic/agent-llm-traces"

    def test_valid_generative_workload_loads(self, tmp_path):
        exp_root = self._write(
            tmp_path, {"name": "wl", "version": 1, "clients": []}
        )
        data = assemble_run._load_workload(exp_root, "workloads/w.yaml")
        assert "trace" not in data

    def test_trace_missing_pool_concurrent_sessions_raises(self, tmp_path):
        bad = {
            "name": "wl",
            "trace": {
                "source": "hf:x",
                "pool": {"total_sessions": 192},
            },
        }
        exp_root = self._write(tmp_path, bad)
        with pytest.raises(assemble_run.AssembleError, match="concurrent_sessions"):
            assemble_run._load_workload(exp_root, "workloads/w.yaml")

    def test_trace_missing_pool_raises(self, tmp_path):
        bad = {"name": "wl", "trace": {"source": "hf:x"}}
        exp_root = self._write(tmp_path, bad)
        with pytest.raises(assemble_run.AssembleError, match="trace.pool"):
            assemble_run._load_workload(exp_root, "workloads/w.yaml")

    def test_trace_missing_source_raises(self, tmp_path):
        bad = {
            "name": "wl",
            "trace": {"pool": {"concurrent_sessions": 4, "total_sessions": 0}},
        }
        exp_root = self._write(tmp_path, bad)
        with pytest.raises(assemble_run.AssembleError, match="trace.source"):
            assemble_run._load_workload(exp_root, "workloads/w.yaml")

    def test_concurrent_sessions_below_one_raises(self, tmp_path):
        bad = {
            "name": "wl",
            "trace": {
                "source": "hf:x",
                "pool": {"concurrent_sessions": 0, "total_sessions": 0},
            },
        }
        exp_root = self._write(tmp_path, bad)
        with pytest.raises(assemble_run.AssembleError, match="concurrent_sessions"):
            assemble_run._load_workload(exp_root, "workloads/w.yaml")

    def test_total_sessions_zero_is_allowed(self, tmp_path):
        ok = {
            "name": "wl",
            "trace": {
                "source": "hf:x",
                "pool": {"concurrent_sessions": 1, "total_sessions": 0},
            },
        }
        exp_root = self._write(tmp_path, ok)
        data = assemble_run._load_workload(exp_root, "workloads/w.yaml")
        assert data["trace"]["pool"]["total_sessions"] == 0

    def test_both_trace_and_clients_raises(self, tmp_path):
        bad = {
            "name": "wl",
            "clients": [],
            "trace": {
                "source": "hf:x",
                "pool": {"concurrent_sessions": 1, "total_sessions": 1},
            },
        }
        exp_root = self._write(tmp_path, bad)
        with pytest.raises(assemble_run.AssembleError, match="both 'trace' and 'clients'"):
            assemble_run._load_workload(exp_root, "workloads/w.yaml")
