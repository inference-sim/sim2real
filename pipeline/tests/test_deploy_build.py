"""Tests for deploy build subcommand."""
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from pipeline import deploy
from pipeline.deploy import build_parser


def test_build_parser_exists():
    """build subcommand is registered."""
    parser = build_parser()
    args = parser.parse_args(["build"])
    assert args.command == "build"


def test_build_parser_skip_flag():
    """build subcommand has --skip-build flag."""
    parser = build_parser()
    args = parser.parse_args(["build", "--skip-build"])
    assert args.skip_build is True


def test_run_parser_skip_build_flag():
    """run subcommand has --skip-build (not --skip-build-epp)."""
    parser = build_parser()
    args = parser.parse_args(["run", "--skip-build"])
    assert args.skip_build is True


def test_run_parser_no_skip_build_epp():
    """--skip-build-epp is no longer accepted."""
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["run", "--skip-build-epp"])


class TestCmdBuildScenarioIteration:
    """Integration tests for multi-scenario image build logic."""

    def _make_cluster(self, tmp_path, scenarios: dict):
        """Create cluster/ dir with named scenario YAML files."""
        cluster_dir = tmp_path / "cluster"
        cluster_dir.mkdir()
        for name, content in scenarios.items():
            (cluster_dir / f"{name}.yaml").write_text(yaml.dump(content))
        return cluster_dir

    def test_collects_both_baseline_and_treatment_refs(self, tmp_path):
        from pipeline.lib.ensure_image import collect_scenario_images

        cluster_dir = self._make_cluster(tmp_path, {
            "base1": {"scenario": [{"name": "s", "images": {
                "inferenceScheduler": {"repository": "ghcr.io/org/sched", "tag": "abc12345"}
            }}]},
            "algo1": {"scenario": [{"name": "s", "images": {
                "inferenceScheduler": {"repository": "ghcr.io/org/sched", "tag": "r1"}
            }}]},
        })
        images = collect_scenario_images(cluster_dir)
        refs = {i["image_ref"] for i in images}
        assert "ghcr.io/org/sched:abc12345" in refs
        assert "ghcr.io/org/sched:r1" in refs
        assert len(refs) == 2

    def test_deduplicates_shared_baseline_image(self, tmp_path):
        """Multiple baselines sharing the same image are deduplicated."""
        from pipeline.lib.ensure_image import collect_scenario_images

        cluster_dir = self._make_cluster(tmp_path, {
            "base1": {"scenario": [{"name": "s", "images": {
                "inferenceScheduler": {"repository": "ghcr.io/org/sched", "tag": "abc12345"}
            }}]},
            "base2": {"scenario": [{"name": "s", "images": {
                "inferenceScheduler": {"repository": "ghcr.io/org/sched", "tag": "abc12345"}
            }}]},
        })
        images = collect_scenario_images(cluster_dir)
        assert len(images) == 1

    def test_skipped_when_hash_matches(self, tmp_path):
        """Images with matching source hashes are reported as current."""
        from pipeline.lib.ensure_image import compute_source_hash, image_needs_build

        src = tmp_path / "src"
        src.mkdir()
        subprocess.run(["git", "init", str(src)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(src), "config", "user.email", "t@t"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(src), "config", "user.name", "T"], check=True, capture_output=True)
        (src / "f.txt").write_text("x")
        subprocess.run(["git", "-C", str(src), "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(src), "commit", "-m", "i"], check=True, capture_output=True)

        run_dir = tmp_path / "run"
        run_dir.mkdir()
        current = compute_source_hash(src)
        meta = {"version": 1, "stages": {}, "source_hashes": {"ghcr.io/org/sched:abc12345": current}}
        (run_dir / "run_metadata.json").write_text(json.dumps(meta))

        assert image_needs_build(run_dir, "ghcr.io/org/sched:abc12345", src) is False

    def test_needs_build_when_no_stored_hash(self, tmp_path):
        """Images with no stored hash need building."""
        from pipeline.lib.ensure_image import image_needs_build

        src = tmp_path / "src"
        src.mkdir()
        subprocess.run(["git", "init", str(src)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(src), "config", "user.email", "t@t"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(src), "config", "user.name", "T"], check=True, capture_output=True)
        (src / "f.txt").write_text("x")
        subprocess.run(["git", "-C", str(src), "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(src), "commit", "-m", "i"], check=True, capture_output=True)

        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "run_metadata.json").write_text(json.dumps({"version": 1, "stages": {}}))

        assert image_needs_build(run_dir, "ghcr.io/org/sched:abc12345", src) is True

    def test_treatment_ref_identified_correctly(self, tmp_path):
        """Treatment ref matches {registry}/{repo_name}:{run_name} pattern."""
        from pipeline.lib.ensure_image import collect_scenario_images

        registry = "ghcr.io/org"
        repo_name = "sched"
        run_name = "r1"
        treatment_ref = f"{registry}/{repo_name}:{run_name}"
        assert treatment_ref == "ghcr.io/org/sched:r1"

        cluster_dir = self._make_cluster(tmp_path, {
            "algo1": {"scenario": [{"name": "s", "images": {
                "inferenceScheduler": {"repository": "ghcr.io/org/sched", "tag": "r1"}
            }}]},
        })
        images = collect_scenario_images(cluster_dir)
        assert images[0]["image_ref"] == treatment_ref


class TestWriteBuildMetadata:
    """Unit tests for _write_build_metadata helper (issue #191)."""

    def test_writes_epp_image_and_last_completed_step(self, tmp_path):
        from pipeline.deploy import _write_build_metadata

        run_dir = tmp_path / "run"
        run_dir.mkdir()
        meta = {"version": 1, "stages": {}}
        (run_dir / "run_metadata.json").write_text(json.dumps(meta))

        _write_build_metadata(run_dir, "ghcr.io/org/sched:r1")

        result = json.loads((run_dir / "run_metadata.json").read_text())
        assert result["epp_image"] == "ghcr.io/org/sched:r1"
        assert result["stages"]["deploy"]["last_completed_step"] == "build"

    def test_creates_stages_and_deploy_keys_when_missing(self, tmp_path):
        from pipeline.deploy import _write_build_metadata

        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "run_metadata.json").write_text(json.dumps({"version": 1}))

        _write_build_metadata(run_dir, "img:tag")

        result = json.loads((run_dir / "run_metadata.json").read_text())
        assert result["stages"]["deploy"]["last_completed_step"] == "build"
        assert result["epp_image"] == "img:tag"

    def test_preserves_other_stage_keys(self, tmp_path):
        from pipeline.deploy import _write_build_metadata

        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "run_metadata.json").write_text(json.dumps({
            "version": 1,
            "stages": {"setup": {"status": "ok"}, "deploy": {"status": "in_progress"}},
        }))

        _write_build_metadata(run_dir, "img:tag")

        result = json.loads((run_dir / "run_metadata.json").read_text())
        assert result["stages"]["setup"] == {"status": "ok"}
        assert result["stages"]["deploy"]["status"] == "in_progress"
        assert result["stages"]["deploy"]["last_completed_step"] == "build"

    def test_no_op_when_metadata_missing(self, tmp_path):
        from pipeline.deploy import _write_build_metadata

        run_dir = tmp_path / "run"
        run_dir.mkdir()
        _write_build_metadata(run_dir, "img:tag")
        assert not (run_dir / "run_metadata.json").exists()

    def test_no_op_when_metadata_unparseable(self, tmp_path):
        from pipeline.deploy import _write_build_metadata

        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "run_metadata.json").write_text("not json {{{")

        _write_build_metadata(run_dir, "img:tag")

        assert (run_dir / "run_metadata.json").read_text() == "not json {{{"


class TestCmdBuildForwardsRegistrySecretName:
    """Regression tests for issue #480 — deploy.py:_cmd_build must
    thread cluster_config.secret_names.registry_creds through to
    dispatch_buildkit_build. Without this, buildkit mounts the wrong
    (or a nonexistent) k8s Secret and pushes fail after rotation.
    """

    def _make_run_dir(self, tmp_path: Path, *, component_image: str = "img:tag") -> Path:
        run_dir = tmp_path / "run"
        (run_dir / "cluster").mkdir(parents=True)
        (run_dir / "run_metadata.json").write_text(json.dumps({
            "version": 1,
            "component_image": component_image,
            "registry": "ghcr.io/org",
            "repo_name": "sched",
            "stages": {},
        }))
        # Source dir on the experiment root — _cmd_build reads it via
        # EXPERIMENT_ROOT / repo_name.
        (tmp_path / "sched").mkdir()
        deploy.EXPERIMENT_ROOT = tmp_path
        return run_dir

    def test_forwards_registry_secret_name_to_dispatch(self, tmp_path):
        run_dir = self._make_run_dir(tmp_path)
        with patch("pipeline.lib.ensure_image.collect_scenario_images",
                   return_value=[{"image_ref": "ghcr.io/org/sched:r", "package": "treatment"}]), \
             patch("pipeline.lib.ensure_image.image_needs_build",
                   return_value=True), \
             patch("pipeline.lib.ensure_image.compute_source_hash",
                   return_value="hash-x"), \
             patch("pipeline.lib.ensure_image.save_source_hash"), \
             patch("pipeline.lib.build.dispatch_buildkit_build",
                   return_value=0) as mock_dispatch:
            deploy._cmd_build(
                run_dir,
                namespace="sim2real-slot-0",
                skip_build=False,
                registry_secret_name="my-org-creds",
            )
        assert mock_dispatch.called
        assert (
            mock_dispatch.call_args.kwargs["registry_secret_name"]
            == "my-org-creds"
        )

    def test_missing_registry_secret_name_exits_1(self, tmp_path, capsys):
        run_dir = self._make_run_dir(tmp_path)
        # Should abort before any build primitive is called.
        with patch("pipeline.lib.ensure_image.collect_scenario_images") as m_col, \
             patch("pipeline.lib.build.dispatch_buildkit_build") as m_dispatch, \
             pytest.raises(SystemExit) as excinfo:
            deploy._cmd_build(
                run_dir,
                namespace="sim2real-slot-0",
                skip_build=False,
                registry_secret_name="",
            )
        assert excinfo.value.code == 1
        err = capsys.readouterr().err
        assert "registry_creds" in err
        assert "cluster.py provision" in err
        m_col.assert_not_called()
        m_dispatch.assert_not_called()

    def test_skip_build_does_not_require_registry_secret_name(self, tmp_path):
        """--skip-build returns 'skip' before touching credentials, so
        an empty secret name must NOT cause an error in that path.
        """
        run_dir = self._make_run_dir(tmp_path)
        result = deploy._cmd_build(
            run_dir,
            namespace="sim2real-slot-0",
            skip_build=True,
            registry_secret_name="",
        )
        assert result == "skip"
