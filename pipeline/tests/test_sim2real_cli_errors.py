"""Tests for sim2real.py CLI error-path coverage.

Covers branches not already exercised by the existing suite (test_build.py,
test_translate.py, test_resolve.py, test_sim2real.py): the ``_cmd_build`` copy
of cluster resolution (no cluster / multiple clusters / no namespaces /
unreadable cluster_config), malformed ``setup_config.json``, the BYO-guard
manifest-defer branch, ``_cmd_translate`` manifest errors (missing
transfer.yaml / empty algorithms / invalid algorithm name), the slicer hash
error, and the ``sim2real build`` no-algorithms-recorded branch.

Filed by quality agent (hold-gated mode).
"""

from __future__ import annotations

import json

import pytest
import yaml

from pipeline import sim2real
from pipeline.lib import layout


@pytest.fixture(autouse=True)
def _isolated_experiment_root(tmp_path):
    layout._EXPERIMENT_ROOT = tmp_path
    yield
    layout._EXPERIMENT_ROOT = None


# ─── _cmd_build BYO guard: manifest-defer branch ─────────────────────────────

class TestCmdBuildBYOGuard:
    """BYO guard defers to downstream errors when the manifest fails to load."""

    def test_byo_guard_skipped_when_manifest_invalid(self, tmp_path, monkeypatch, capsys):
        """A transfer.yaml that parses as YAML but fails manifest validation
        (wrong ``kind``) makes the BYO guard defer to downstream errors rather
        than fire — it must NOT emit the BYO 'nothing to build' message."""
        # Valid YAML, but load_manifest rejects it (wrong kind) → ManifestError
        # is caught and the BYO guard is skipped.
        (tmp_path / "transfer.yaml").write_text(yaml.dump({
            "kind": "wrong-kind",
            "version": 3,
            "scenario": "x",
        }))
        # Stub check_skopeo so we get past it
        monkeypatch.setattr("pipeline.lib.build.check_skopeo", lambda: None)
        # Will fail later at translation_ref resolution — but not at BYO guard
        rc = sim2real.main([
            "--experiment-root", str(tmp_path),
            "build", "--translation", "nonexistent",
        ])
        assert rc == 2
        err = capsys.readouterr().err
        # Should NOT be BYO error — should be a downstream error
        assert "nothing to build" not in err


# ─── _cmd_build: malformed setup_config.json ─────────────────────────────────

class TestCmdBuildRegistryPrereqs:
    """_cmd_build: malformed setup_config.json → exit 2."""

    def _setup_translation(self, tmp_path, monkeypatch):
        """Create minimal translation that passes hash resolution."""
        monkeypatch.setattr("pipeline.lib.build.check_skopeo", lambda: None)
        thash = "a" * 64
        tdir = tmp_path / "workspace" / "translations" / thash
        tdir.mkdir(parents=True)
        gen = tdir / "generated" / "algo1"
        gen.mkdir(parents=True)
        (gen / "algo1_output.json").write_text(json.dumps({
            "files_created": [], "files_modified": [],
        }))
        (tdir / "translation_output.json").write_text(json.dumps({
            "version": 1,
            "translation_hash": thash,
            "source": "skill",
            "alias": None,
            "algorithms": [
                {"name": "algo1", "image_ref": None, "image_digest": None},
            ],
            "created_at": "2026-07-01T00:00:00Z",
        }))
        return thash

    def test_malformed_setup_config_errors(self, tmp_path, monkeypatch, capsys):
        """Malformed JSON in setup_config.json → exit 2."""
        thash = self._setup_translation(tmp_path, monkeypatch)
        ws = tmp_path / "workspace"
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "setup_config.json").write_text("{bad json")
        rc = sim2real.main([
            "--experiment-root", str(tmp_path),
            "build", "--translation", thash,
        ])
        assert rc == 2
        err = capsys.readouterr().err
        assert "error:" in err


class TestCmdBuildClusterPrereqs:
    """_cmd_build: cluster resolution error paths (distinct from the
    translation-register copy of this logic)."""

    def _setup_translation_with_config(self, tmp_path, monkeypatch):
        """Create translation + setup_config.json for cluster-resolution tests."""
        monkeypatch.setattr("pipeline.lib.build.check_skopeo", lambda: None)
        thash = "b" * 64
        tdir = tmp_path / "workspace" / "translations" / thash
        tdir.mkdir(parents=True)
        gen = tdir / "generated" / "algo1"
        gen.mkdir(parents=True)
        (gen / "algo1_output.json").write_text(json.dumps({
            "files_created": [], "files_modified": [],
        }))
        (tdir / "translation_output.json").write_text(json.dumps({
            "version": 1,
            "translation_hash": thash,
            "source": "skill",
            "alias": None,
            "algorithms": [
                {"name": "algo1", "image_ref": None, "image_digest": None},
            ],
            "created_at": "2026-07-01T00:00:00Z",
        }))
        ws = tmp_path / "workspace"
        (ws / "setup_config.json").write_text(json.dumps({
            "registry": "ghcr.io/org",
            "repo_name": "myrepo",
        }))
        return thash

    def test_no_cluster_provisioned_errors(self, tmp_path, monkeypatch, capsys):
        """No clusters → exit 2."""
        thash = self._setup_translation_with_config(tmp_path, monkeypatch)
        # clusters dir empty or doesn't exist
        rc = sim2real.main([
            "--experiment-root", str(tmp_path),
            "build", "--translation", thash,
        ])
        assert rc == 2
        err = capsys.readouterr().err
        assert "no cluster provisioned" in err

    def test_multiple_clusters_errors(self, tmp_path, monkeypatch, capsys):
        """Multiple clusters → exit 2 (single-cluster-per-workspace assumption)."""
        thash = self._setup_translation_with_config(tmp_path, monkeypatch)
        clusters_dir = tmp_path / "workspace" / "clusters"
        for cid in ["cluster-a", "cluster-b"]:
            (clusters_dir / cid).mkdir(parents=True)
            (clusters_dir / cid / "cluster_config.json").write_text(json.dumps({
                "namespaces": ["ns1"],
            }))
        rc = sim2real.main([
            "--experiment-root", str(tmp_path),
            "build", "--translation", thash,
        ])
        assert rc == 2
        err = capsys.readouterr().err
        assert "multiple clusters found" in err

    def test_cluster_config_no_namespaces_errors(self, tmp_path, monkeypatch, capsys):
        """cluster_config.json with empty namespaces → exit 2."""
        thash = self._setup_translation_with_config(tmp_path, monkeypatch)
        cdir = tmp_path / "workspace" / "clusters" / "test-cluster"
        cdir.mkdir(parents=True)
        (cdir / "cluster_config.json").write_text(json.dumps({
            "namespaces": [],
        }))
        rc = sim2real.main([
            "--experiment-root", str(tmp_path),
            "build", "--translation", thash,
        ])
        assert rc == 2
        err = capsys.readouterr().err
        assert "no namespaces" in err

    def test_unreadable_cluster_config_errors(self, tmp_path, monkeypatch, capsys):
        """OSError/invalid JSON reading cluster_config.json → exit 2."""
        thash = self._setup_translation_with_config(tmp_path, monkeypatch)
        cdir = tmp_path / "workspace" / "clusters" / "test-cluster"
        cdir.mkdir(parents=True)
        (cdir / "cluster_config.json").write_text("{invalid json!!")
        rc = sim2real.main([
            "--experiment-root", str(tmp_path),
            "build", "--translation", thash,
        ])
        assert rc == 2
        err = capsys.readouterr().err
        assert "error:" in err


# ─── _cmd_translate manifest errors ──────────────────────────────────────────

class TestCmdTranslateManifestErrors:
    """_cmd_translate: manifest loading/validation errors."""

    def test_no_transfer_yaml_errors(self, tmp_path, capsys):
        """No transfer.yaml → exit 2."""
        rc = sim2real.main([
            "--experiment-root", str(tmp_path),
            "translate",
        ])
        assert rc == 2
        err = capsys.readouterr().err
        assert "transfer.yaml not found" in err

    def test_empty_algorithms_errors(self, tmp_path, capsys):
        """transfer.yaml with no algorithms → exit 2."""
        (tmp_path / "transfer.yaml").write_text(yaml.dump({
            "kind": "sim2real-transfer",
            "version": 3,
            "scenario": "test-scenario",
            "baselines": [{"name": "base1", "scenario": "base1-scenario"}],
            "algorithms": [],
            "component": {"repo": "https://github.com/org/myrepo", "kind": "go"},
        }))
        rc = sim2real.main([
            "--experiment-root", str(tmp_path),
            "translate",
        ])
        assert rc == 2
        err = capsys.readouterr().err
        assert "no algorithms declared" in err

    def test_invalid_algorithm_name_errors(self, tmp_path, capsys):
        """Invalid algorithm name → exit 2.

        Note: manifest.py's _validate_package_name catches names with leading
        dots before _cmd_translate's own validate_name check runs. Either way
        the CLI surfaces a descriptive error and exits 2.
        """
        (tmp_path / "transfer.yaml").write_text(yaml.dump({
            "kind": "sim2real-transfer",
            "version": 3,
            "scenario": "test-scenario",
            "baselines": [{"name": "base1", "scenario": "base1-scenario"}],
            "algorithms": [{"name": ".bad-name", "source": "x.go", "defaults": "base1"}],
            "component": {"repo": "https://github.com/org/myrepo", "kind": "go"},
        }))
        rc = sim2real.main([
            "--experiment-root", str(tmp_path),
            "translate",
        ])
        assert rc == 2
        err = capsys.readouterr().err
        assert ".bad-name" in err and "invalid" in err


class TestCmdTranslateSlicerError:
    """_cmd_translate: slicer.translation_hash_with_sources error."""

    def test_missing_source_file_errors(self, tmp_path, capsys):
        """Source file referenced in manifest does not exist → exit 2."""
        (tmp_path / "transfer.yaml").write_text(yaml.dump({
            "kind": "sim2real-transfer",
            "version": 3,
            "scenario": "test-scenario",
            "baselines": [{"name": "base1", "scenario": "base1-scenario"}],
            "algorithms": [{"name": "algo1", "source": "missing/file.go", "defaults": "base1"}],
            "component": {"repo": "https://github.com/org/myrepo", "kind": "go"},
        }))
        rc = sim2real.main([
            "--experiment-root", str(tmp_path),
            "translate",
        ])
        assert rc == 2
        err = capsys.readouterr().err
        assert "error:" in err


# ─── _cmd_build no-algorithms error ──────────────────────────────────────────

class TestCmdBuildNoAlgorithms:
    """_cmd_build: translation with empty algorithms list → exit 2."""

    def test_empty_algorithms_list_errors(self, tmp_path, monkeypatch, capsys):
        """Translation with algorithms=[] → exit 2."""
        monkeypatch.setattr("pipeline.lib.build.check_skopeo", lambda: None)
        thash = "d" * 64
        tdir = tmp_path / "workspace" / "translations" / thash
        tdir.mkdir(parents=True)
        (tdir / "translation_output.json").write_text(json.dumps({
            "version": 1,
            "translation_hash": thash,
            "source": "skill",
            "alias": None,
            "algorithms": [],
            "created_at": "2026-07-01T00:00:00Z",
        }))
        rc = sim2real.main([
            "--experiment-root", str(tmp_path),
            "build", "--translation", thash,
        ])
        assert rc == 2
        err = capsys.readouterr().err
        assert "no algorithms recorded" in err
