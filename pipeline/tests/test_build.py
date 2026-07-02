"""Tests for pipeline/lib/build.py — shared build primitives, and the
sim2real build command."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import patch

import pytest

from pipeline import sim2real
from pipeline.lib import build, layout


@pytest.fixture(autouse=True)
def _isolated_experiment_root(tmp_path):
    layout._EXPERIMENT_ROOT = tmp_path
    yield
    layout._EXPERIMENT_ROOT = None


class TestComposeImageRef:
    def test_composes_registry_repo_tag(self):
        assert build.compose_image_ref(
            "quay.io/user", "sched", "abc123-softref"
        ) == "quay.io/user/sched:abc123-softref"

    def test_rejects_empty_registry(self):
        with pytest.raises(build.BuildError, match="registry"):
            build.compose_image_ref("", "repo", "tag")

    def test_rejects_empty_repo(self):
        with pytest.raises(build.BuildError, match="repo"):
            build.compose_image_ref("reg", "", "tag")

    def test_rejects_empty_tag(self):
        with pytest.raises(build.BuildError, match="tag"):
            build.compose_image_ref("reg", "repo", "")


class TestCheckSkopeo:
    def test_success_when_skopeo_on_path(self):
        with patch("pipeline.lib.build.shutil.which",
                   return_value="/usr/local/bin/skopeo"):
            build.check_skopeo()  # returns None; must not raise

    def test_raises_with_install_hint_when_missing(self):
        with patch("pipeline.lib.build.shutil.which", return_value=None):
            with pytest.raises(build.BuildError, match="skopeo not found"):
                build.check_skopeo()

    def test_error_includes_install_commands(self):
        with patch("pipeline.lib.build.shutil.which", return_value=None):
            with pytest.raises(build.BuildError) as excinfo:
                build.check_skopeo()
            msg = str(excinfo.value)
            assert "brew install skopeo" in msg
            assert "apt install skopeo" in msg
            assert "dnf install skopeo" in msg


class TestProbeImageDigest:
    def _mock_run(self, stdout: str = "", stderr: str = "",
                  returncode: int = 0, raise_exc: Exception | None = None):
        """Return a patch context that stubs subprocess.run."""
        def fake_run(*args, **kwargs):
            if raise_exc is not None:
                raise raise_exc
            return subprocess.CompletedProcess(
                args=args[0], returncode=returncode,
                stdout=stdout, stderr=stderr,
            )
        return patch("pipeline.lib.build.subprocess.run", side_effect=fake_run)

    def test_returns_digest_on_success(self):
        payload = json.dumps({
            "Digest": "sha256:abcdef0123456789" + "0" * 48
        })
        with self._mock_run(stdout=payload, returncode=0):
            digest = build.probe_image_digest("quay.io/u/r:t")
        assert digest == "sha256:abcdef0123456789" + "0" * 48

    def test_returns_none_on_nonzero_exit(self):
        with self._mock_run(stdout="", stderr="manifest unknown", returncode=1):
            assert build.probe_image_digest("quay.io/u/r:missing") is None

    def test_returns_none_on_invalid_json(self):
        with self._mock_run(stdout="not json{{{", returncode=0):
            assert build.probe_image_digest("quay.io/u/r:t") is None

    def test_returns_none_on_json_without_digest(self):
        with self._mock_run(stdout=json.dumps({"other": "value"}), returncode=0):
            assert build.probe_image_digest("quay.io/u/r:t") is None

    def test_returns_none_on_timeout(self):
        with self._mock_run(
            raise_exc=subprocess.TimeoutExpired(cmd=["skopeo"], timeout=30)
        ):
            assert build.probe_image_digest("quay.io/u/r:t") is None

    def test_returns_none_on_file_not_found(self):
        with self._mock_run(raise_exc=FileNotFoundError()):
            assert build.probe_image_digest("quay.io/u/r:t") is None

    def test_calls_skopeo_inspect_with_docker_scheme(self):
        with patch(
            "pipeline.lib.build.subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=[], returncode=0,
                stdout=json.dumps({"Digest": "sha256:" + "0" * 64}),
                stderr="",
            ),
        ) as mock_run:
            build.probe_image_digest("quay.io/u/r:t")
            assert mock_run.called
            call_args = mock_run.call_args[0][0]
            assert call_args[0] == "skopeo"
            assert call_args[1] == "inspect"
            assert "docker://quay.io/u/r:t" in call_args


class TestAtomicWriteJson:
    def test_writes_pretty_json(self, tmp_path):
        target = tmp_path / "out.json"
        build.atomic_write_json(target, {"a": 1, "b": [2, 3]})
        loaded = json.loads(target.read_text())
        assert loaded == {"a": 1, "b": [2, 3]}
        text = target.read_text()
        assert "  " in text  # indented, not compact

    def test_creates_parent_dirs(self, tmp_path):
        target = tmp_path / "a" / "b" / "c.json"
        build.atomic_write_json(target, {"x": 1})
        assert target.exists()
        assert json.loads(target.read_text()) == {"x": 1}

    def test_overwrites_existing_atomically(self, tmp_path):
        target = tmp_path / "out.json"
        target.write_text('{"old": true}')
        build.atomic_write_json(target, {"new": True})
        assert json.loads(target.read_text()) == {"new": True}

    def test_cleans_up_tempfile_on_write_failure(self, tmp_path):
        target = tmp_path / "out.json"
        target.write_text('{"placeholder": true}')

        with patch("pipeline.lib.build.os.replace",
                   side_effect=OSError("boom")):
            with pytest.raises(OSError, match="boom"):
                build.atomic_write_json(target, {"new": True})
        # No stray .tmp-*.json siblings should remain.
        siblings = [
            p.name for p in tmp_path.iterdir() if p.name != "out.json"
        ]
        assert siblings == [], f"leaked tempfile(s): {siblings}"


class TestDispatchBuildkitBuild:
    def _patch_run(self, returncode: int = 0):
        return patch(
            "pipeline.lib.build.subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=[], returncode=returncode, stdout="", stderr="",
            ),
        )

    def test_invokes_build_script_with_all_flags(self, tmp_path):
        source_dir = tmp_path / "src"
        source_dir.mkdir()
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        repo_root = tmp_path / "repo"
        (repo_root / "pipeline" / "scripts").mkdir(parents=True)
        (repo_root / "pipeline" / "scripts" / "build-epp.sh").write_text(
            "#!/bin/bash\n"
        )

        with self._patch_run(returncode=0) as mock_run:
            rc = build.dispatch_buildkit_build(
                image_ref="reg/repo:tag-algo",
                build_id="build-xyz",
                namespace="sim2real-slot1",
                source_dir=source_dir,
                run_dir=run_dir,
                repo_root=repo_root,
            )
        assert rc == 0
        assert mock_run.called
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "bash"
        assert str(repo_root / "pipeline" / "scripts" / "build-epp.sh") in cmd
        assert "--image-ref" in cmd
        assert "reg/repo:tag-algo" in cmd
        assert "--namespace" in cmd
        assert "sim2real-slot1" in cmd
        assert "--source-dir" in cmd
        assert str(source_dir) in cmd
        assert "--run-dir" in cmd
        assert str(run_dir) in cmd
        assert "--run-name" in cmd
        assert "build-xyz" in cmd

    def test_returns_nonzero_on_script_failure(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "run").mkdir()
        (tmp_path / "repo" / "pipeline" / "scripts").mkdir(parents=True)
        (tmp_path / "repo" / "pipeline" / "scripts" / "build-epp.sh").touch()
        with self._patch_run(returncode=42):
            rc = build.dispatch_buildkit_build(
                image_ref="r/r:t", build_id="b", namespace="ns",
                source_dir=tmp_path / "src", run_dir=tmp_path / "run",
                repo_root=tmp_path / "repo",
            )
        assert rc == 42

    def test_raises_when_build_script_missing(self, tmp_path):
        with pytest.raises(build.BuildError, match="build-epp.sh"):
            build.dispatch_buildkit_build(
                image_ref="r/r:t", build_id="b", namespace="ns",
                source_dir=tmp_path, run_dir=tmp_path,
                repo_root=tmp_path,
            )

    def test_cwd_is_repo_root(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "run").mkdir()
        (tmp_path / "pipeline" / "scripts").mkdir(parents=True)
        (tmp_path / "pipeline" / "scripts" / "build-epp.sh").touch()
        with self._patch_run(returncode=0) as mock_run:
            build.dispatch_buildkit_build(
                image_ref="r/r:t", build_id="b", namespace="ns",
                source_dir=tmp_path / "src", run_dir=tmp_path / "run",
                repo_root=tmp_path,
            )
        assert mock_run.call_args.kwargs.get("cwd") == tmp_path


# ── sim2real build command ──────────────────────────────────────────


def _make_workspace(tmp_path, *, registry="quay.io/user", repo_name="sched"):
    """Create a workspace/setup_config.json with registry/repo_name."""
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "setup_config.json").write_text(json.dumps({
        "registry": registry, "repo_name": repo_name,
    }))
    return ws


def _make_translation(tmp_path, *, thash="a" * 64, alias="softref",
                      algorithms=None, source="skill"):
    """Materialize workspace/translations/<hash>/translation_output.json.

    Each algorithm dict is stored verbatim under algorithms[i]. Defaults
    to a single algo named 'softref' with image_ref=None.
    """
    ws = tmp_path / "workspace"
    tdir = ws / "translations" / thash
    if algorithms is None:
        algorithms = [{
            "name": "softref", "source_path": "algorithms/softref.py",
            "source_sha256": "0" * 64, "config_path": None,
            "image_ref": None, "image_digest": None,
        }]
    for algo in algorithms:
        gd = tdir / "generated" / algo["name"]
        gd.mkdir(parents=True, exist_ok=True)
        (gd / f"{algo['name']}_output.json").write_text(
            json.dumps({"stub": True})
        )
    tout = tdir / "translation_output.json"
    tout.write_text(json.dumps({
        "version": 1, "translation_hash": thash, "source": source,
        "alias": alias, "algorithms": algorithms,
        "created_at": "2026-07-02T00:00:00Z",
    }))
    return thash


class TestSim2realBuildParser:
    def _parse(self, argv):
        return sim2real.build_parser().parse_args(argv)

    def test_translation_required(self):
        with pytest.raises(SystemExit):
            self._parse(["build"])

    def test_accepts_translation_alias(self):
        args = self._parse(["build", "--translation", "softreflective"])
        assert args.command == "build"
        assert args.translation == "softreflective"
        assert args.force_rebuild is False
        assert args.skip_build is False

    def test_force_rebuild_flag(self):
        args = self._parse(["build", "--translation", "abc", "--force-rebuild"])
        assert args.force_rebuild is True

    def test_skip_build_flag(self):
        args = self._parse(["build", "--translation", "abc", "--skip-build"])
        assert args.skip_build is True


class TestSim2realBuildPrereqs:
    def test_missing_skopeo_exits_2(self, tmp_path, capsys):
        _make_workspace(tmp_path)
        _make_translation(tmp_path, alias="softref")
        with patch("pipeline.lib.build.shutil.which", return_value=None):
            rc = sim2real.main([
                "--experiment-root", str(tmp_path),
                "build", "--translation", "softref",
            ])
        assert rc == 2
        assert "skopeo not found" in capsys.readouterr().err

    def test_unknown_translation_exits_2(self, tmp_path, capsys):
        _make_workspace(tmp_path)
        (tmp_path / "workspace" / "translations").mkdir(parents=True)
        with patch("pipeline.lib.build.shutil.which",
                   return_value="/usr/bin/skopeo"):
            rc = sim2real.main([
                "--experiment-root", str(tmp_path),
                "build", "--translation", "nope",
            ])
        assert rc == 2
        assert "no translations" in capsys.readouterr().err

    def test_missing_algo_output_exits_2(self, tmp_path, capsys):
        """Prereq: translation completeness — every algo needs
        generated/<algo>/<algo>_output.json on disk."""
        _make_workspace(tmp_path)
        thash = _make_translation(tmp_path, alias="softref")
        algo_out = (
            tmp_path / "workspace" / "translations" / thash
            / "generated" / "softref" / "softref_output.json"
        )
        algo_out.unlink()
        with patch("pipeline.lib.build.shutil.which",
                   return_value="/usr/bin/skopeo"):
            rc = sim2real.main([
                "--experiment-root", str(tmp_path),
                "build", "--translation", "softref",
            ])
        assert rc == 2
        err_out = capsys.readouterr().err
        assert "incomplete" in err_out
        assert "softref" in err_out

    def test_missing_registry_exits_2(self, tmp_path, capsys):
        _make_workspace(tmp_path, registry="", repo_name="")
        _make_translation(tmp_path, alias="softref")
        with patch("pipeline.lib.build.shutil.which",
                   return_value="/usr/bin/skopeo"):
            rc = sim2real.main([
                "--experiment-root", str(tmp_path),
                "build", "--translation", "softref",
            ])
        assert rc == 2
        assert "registry" in capsys.readouterr().err.lower()

    def test_missing_setup_config_exits_2(self, tmp_path, capsys):
        """No workspace/setup_config.json at all → prereq error."""
        (tmp_path / "workspace").mkdir()
        _make_translation(tmp_path, alias="softref")
        with patch("pipeline.lib.build.shutil.which",
                   return_value="/usr/bin/skopeo"):
            rc = sim2real.main([
                "--experiment-root", str(tmp_path),
                "build", "--translation", "softref",
            ])
        assert rc == 2
        combined = capsys.readouterr()
        stderr = combined.err.lower()
        assert "setup_config" in stderr or "registry" in stderr


class TestSim2realBuildLoop:
    """Tests the per-algorithm probe → build → write loop.

    Each test:
      - stubs pipeline.lib.build.shutil.which so check_skopeo passes
      - stubs pipeline.lib.build.probe_image_digest and .dispatch_buildkit_build
      - materializes a workspace with translations/<hash>/ and cluster_config
      - asserts on returncode, on the mutated translation_output.json, and on
        which subprocess calls were made
    """

    def _make_cluster_config(self, tmp_path, cluster_id="test-cluster",
                             namespaces=("sim2real-slot1",)):
        cdir = tmp_path / "workspace" / "clusters" / cluster_id
        cdir.mkdir(parents=True)
        (cdir / "cluster_config.json").write_text(json.dumps({
            "cluster_id": cluster_id,
            "namespaces": list(namespaces),
            "is_openshift": False,
            "storage_class": "",
            "secret_names": {"hf_token": "hf-token"},
            "workspaces": [],
        }))

    def _read_translation_output(self, tmp_path, thash):
        return json.loads(
            (tmp_path / "workspace" / "translations" / thash
             / "translation_output.json").read_text()
        )

    def test_probe_hit_skips_build(self, tmp_path, capsys):
        _make_workspace(tmp_path)
        self._make_cluster_config(tmp_path)
        thash = _make_translation(tmp_path, alias="softref")

        with patch("pipeline.lib.build.shutil.which",
                   return_value="/usr/bin/skopeo"), \
             patch("pipeline.lib.build.probe_image_digest",
                   return_value="sha256:" + "d" * 64) as mock_probe, \
             patch("pipeline.lib.build.dispatch_buildkit_build") as mock_build:
            rc = sim2real.main([
                "--experiment-root", str(tmp_path),
                "build", "--translation", "softref",
            ])
        assert rc == 0
        mock_probe.assert_called()
        mock_build.assert_not_called()  # probe hit → no build

        data = self._read_translation_output(tmp_path, thash)
        algo = data["algorithms"][0]
        assert algo["image_ref"] == f"quay.io/user/sched:{thash[:12]}-softref"
        assert algo["image_digest"] == "sha256:" + "d" * 64

    def test_probe_miss_triggers_build(self, tmp_path):
        _make_workspace(tmp_path)
        self._make_cluster_config(tmp_path)
        thash = _make_translation(tmp_path, alias="softref")

        probe_returns = [None, "sha256:" + "b" * 64]  # miss, then post-build hit
        with patch("pipeline.lib.build.shutil.which",
                   return_value="/usr/bin/skopeo"), \
             patch("pipeline.lib.build.probe_image_digest",
                   side_effect=probe_returns) as mock_probe, \
             patch("pipeline.lib.build.dispatch_buildkit_build",
                   return_value=0) as mock_build:
            rc = sim2real.main([
                "--experiment-root", str(tmp_path),
                "build", "--translation", "softref",
            ])
        assert rc == 0
        assert mock_probe.call_count == 2  # pre + post
        mock_build.assert_called_once()

        data = self._read_translation_output(tmp_path, thash)
        algo = data["algorithms"][0]
        assert algo["image_ref"] == f"quay.io/user/sched:{thash[:12]}-softref"
        assert algo["image_digest"] == "sha256:" + "b" * 64

    def test_force_rebuild_ignores_probe_hit(self, tmp_path):
        _make_workspace(tmp_path)
        self._make_cluster_config(tmp_path)
        _make_translation(tmp_path, alias="softref")

        with patch("pipeline.lib.build.shutil.which",
                   return_value="/usr/bin/skopeo"), \
             patch("pipeline.lib.build.probe_image_digest",
                   return_value="sha256:" + "c" * 64), \
             patch("pipeline.lib.build.dispatch_buildkit_build",
                   return_value=0) as mock_build:
            rc = sim2real.main([
                "--experiment-root", str(tmp_path),
                "build", "--translation", "softref", "--force-rebuild",
            ])
        assert rc == 0
        mock_build.assert_called_once()  # forced despite probe hit

    def test_skip_build_bypasses_everything(self, tmp_path):
        _make_workspace(tmp_path)
        self._make_cluster_config(tmp_path)
        thash = _make_translation(tmp_path, alias="softref")

        with patch("pipeline.lib.build.probe_image_digest") as mock_probe, \
             patch("pipeline.lib.build.dispatch_buildkit_build") as mock_build, \
             patch("pipeline.lib.build.check_skopeo") as mock_check:
            rc = sim2real.main([
                "--experiment-root", str(tmp_path),
                "build", "--translation", "softref", "--skip-build",
            ])
        assert rc == 0
        mock_check.assert_not_called()
        mock_probe.assert_not_called()
        mock_build.assert_not_called()

        data = self._read_translation_output(tmp_path, thash)
        assert data["algorithms"][0]["image_ref"] is None

    def test_probe_auth_failure_treated_as_miss(self, tmp_path):
        """probe_image_digest returns None on any failure — including auth."""
        _make_workspace(tmp_path)
        self._make_cluster_config(tmp_path)
        _make_translation(tmp_path, alias="softref")

        probe_returns = [None, "sha256:" + "1" * 64]  # auth-fail miss, then post-build
        with patch("pipeline.lib.build.shutil.which",
                   return_value="/usr/bin/skopeo"), \
             patch("pipeline.lib.build.probe_image_digest",
                   side_effect=probe_returns), \
             patch("pipeline.lib.build.dispatch_buildkit_build",
                   return_value=0) as mock_build:
            rc = sim2real.main([
                "--experiment-root", str(tmp_path),
                "build", "--translation", "softref",
            ])
        assert rc == 0
        mock_build.assert_called_once()  # miss → build

    def test_post_build_probe_failure_records_null_digest(
        self, tmp_path, capsys,
    ):
        _make_workspace(tmp_path)
        self._make_cluster_config(tmp_path)
        thash = _make_translation(tmp_path, alias="softref")

        probe_returns = [None, None]  # miss then post-build failure
        with patch("pipeline.lib.build.shutil.which",
                   return_value="/usr/bin/skopeo"), \
             patch("pipeline.lib.build.probe_image_digest",
                   side_effect=probe_returns), \
             patch("pipeline.lib.build.dispatch_buildkit_build",
                   return_value=0):
            rc = sim2real.main([
                "--experiment-root", str(tmp_path),
                "build", "--translation", "softref",
            ])
        assert rc == 0
        data = self._read_translation_output(tmp_path, thash)
        algo = data["algorithms"][0]
        assert algo["image_ref"] == f"quay.io/user/sched:{thash[:12]}-softref"
        assert algo["image_digest"] is None
        combined = capsys.readouterr()
        assert "digest not recorded" in combined.out or \
               "digest not recorded" in combined.err

    def test_build_failure_returns_2(self, tmp_path, capsys):
        _make_workspace(tmp_path)
        self._make_cluster_config(tmp_path)
        _make_translation(tmp_path, alias="softref")

        with patch("pipeline.lib.build.shutil.which",
                   return_value="/usr/bin/skopeo"), \
             patch("pipeline.lib.build.probe_image_digest",
                   return_value=None), \
             patch("pipeline.lib.build.dispatch_buildkit_build",
                   return_value=1):
            rc = sim2real.main([
                "--experiment-root", str(tmp_path),
                "build", "--translation", "softref",
            ])
        assert rc == 2
        assert "build failed" in capsys.readouterr().err.lower()

    def test_per_algo_writes_are_atomic_and_incremental(self, tmp_path):
        """Two-algo translation: first algo succeeds, second fails.

        After the run, the first algo's image_ref/image_digest are recorded
        and persisted; the second's are still None."""
        _make_workspace(tmp_path)
        self._make_cluster_config(tmp_path)
        thash = _make_translation(tmp_path, alias="softref", algorithms=[
            {"name": "algo1", "source_path": "a1.py",
             "source_sha256": "0" * 64, "config_path": None,
             "image_ref": None, "image_digest": None},
            {"name": "algo2", "source_path": "a2.py",
             "source_sha256": "1" * 64, "config_path": None,
             "image_ref": None, "image_digest": None},
        ])

        # probe: miss algo1, post-build hit for algo1, miss algo2
        probe_returns = [None, "sha256:" + "1" * 64, None]
        dispatch_returns = [0, 1]

        with patch("pipeline.lib.build.shutil.which",
                   return_value="/usr/bin/skopeo"), \
             patch("pipeline.lib.build.probe_image_digest",
                   side_effect=probe_returns), \
             patch("pipeline.lib.build.dispatch_buildkit_build",
                   side_effect=dispatch_returns):
            rc = sim2real.main([
                "--experiment-root", str(tmp_path),
                "build", "--translation", "softref",
            ])
        assert rc == 2
        data = self._read_translation_output(tmp_path, thash)
        algo1 = next(a for a in data["algorithms"] if a["name"] == "algo1")
        algo2 = next(a for a in data["algorithms"] if a["name"] == "algo2")
        assert algo1["image_ref"] == f"quay.io/user/sched:{thash[:12]}-algo1"
        assert algo1["image_digest"] == "sha256:" + "1" * 64
        assert algo2["image_ref"] is None
        assert algo2["image_digest"] is None

    def test_idempotent_when_image_ref_and_digest_already_recorded(
        self, tmp_path,
    ):
        """A translation with a known image_ref+digest doesn't probe or build."""
        _make_workspace(tmp_path)
        self._make_cluster_config(tmp_path)
        thash = "a" * 64
        _make_translation(tmp_path, thash=thash, alias="softref", algorithms=[
            {"name": "softref", "source_path": "s.py",
             "source_sha256": "0" * 64, "config_path": None,
             "image_ref": f"quay.io/user/sched:{thash[:12]}-softref",
             "image_digest": "sha256:" + "e" * 64},
        ])

        with patch("pipeline.lib.build.shutil.which",
                   return_value="/usr/bin/skopeo"), \
             patch("pipeline.lib.build.probe_image_digest") as mock_probe, \
             patch("pipeline.lib.build.dispatch_buildkit_build") as mock_build:
            rc = sim2real.main([
                "--experiment-root", str(tmp_path),
                "build", "--translation", "softref",
            ])
        assert rc == 0
        mock_probe.assert_not_called()
        mock_build.assert_not_called()
