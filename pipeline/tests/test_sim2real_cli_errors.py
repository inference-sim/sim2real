"""Tests for sim2real.py CLI error-path coverage.

Targets uncovered lines in _cmd_build (BYO guard, skopeo, cluster/registry
prereqs) and _cmd_translate (BYO algorithm guard, alias collision,
resume/partial state paths, slicer hash error).

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


# ─── _cmd_build early-exit error paths ───────────────────────────────────────

class TestCmdBuildBYOGuard:
    """BYO guard: transfer.yaml with no 'component' → exit 2."""

    def _valid_byo_manifest(self):
        """A valid v3 manifest with a BYO algorithm and NO 'component' key."""
        return {
            "kind": "sim2real-transfer",
            "version": 3,
            "scenario": "test-scenario",
            "baselines": [{"name": "base1", "scenario": "base1-scenario"}],
            "algorithms": [{"name": "algo1", "byo": True, "defaults": "base1"}],
            # No 'component' key → BYO guard fires.
        }

    def test_byo_manifest_no_component_errors(self, tmp_path, capsys):
        """transfer.yaml without 'component' is BYO-only → nothing to build."""
        (tmp_path / "transfer.yaml").write_text(yaml.dump(self._valid_byo_manifest()))
        rc = sim2real.main([
            "--experiment-root", str(tmp_path),
            "build", "--translation", "abc123",
        ])
        assert rc == 2
        err = capsys.readouterr().err
        assert "nothing to build" in err
        assert "BYO" in err

    def test_byo_guard_manifest_in_config_subdir(self, tmp_path, capsys):
        """BYO guard also checks config/transfer.yaml fallback."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "transfer.yaml").write_text(
            yaml.dump(self._valid_byo_manifest())
        )
        rc = sim2real.main([
            "--experiment-root", str(tmp_path),
            "build", "--translation", "abc123",
        ])
        assert rc == 2
        err = capsys.readouterr().err
        assert "nothing to build" in err

    def test_byo_guard_skipped_when_manifest_unparseable(self, tmp_path, monkeypatch, capsys):
        """Unparseable manifest defers to downstream errors (not BYO guard)."""
        # Write YAML that parses but fails load_manifest validation (wrong kind).
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


class TestCmdBuildSkopeoCheck:
    """_cmd_build: check_skopeo failure → exit 2."""

    def test_skopeo_not_found_errors(self, tmp_path, monkeypatch, capsys):
        """Missing skopeo returns exit 2 with descriptive error."""
        from pipeline.lib import build
        monkeypatch.setattr(
            "pipeline.lib.build.check_skopeo",
            lambda: (_ for _ in ()).throw(build.BuildError("skopeo not found on PATH")),
        )
        rc = sim2real.main([
            "--experiment-root", str(tmp_path),
            "build", "--translation", "abc123",
        ])
        assert rc == 2
        err = capsys.readouterr().err
        assert "skopeo" in err


class TestCmdBuildTranslationRefErrors:
    """_cmd_build: translation_ref.resolve_translation_ref failure."""

    def test_unresolvable_translation_ref(self, tmp_path, monkeypatch, capsys):
        """Invalid translation ref → exit 2."""
        monkeypatch.setattr("pipeline.lib.build.check_skopeo", lambda: None)
        rc = sim2real.main([
            "--experiment-root", str(tmp_path),
            "build", "--translation", "nonexistent-hash",
        ])
        assert rc == 2
        err = capsys.readouterr().err
        assert "error:" in err


class TestCmdBuildRegistryPrereqs:
    """_cmd_build: registry/repo_name missing from setup_config.json."""

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

    def test_missing_setup_config_errors(self, tmp_path, monkeypatch, capsys):
        """No setup_config.json → exit 2."""
        thash = self._setup_translation(tmp_path, monkeypatch)
        rc = sim2real.main([
            "--experiment-root", str(tmp_path),
            "build", "--translation", thash,
        ])
        assert rc == 2
        err = capsys.readouterr().err
        assert "setup_config.json not found" in err

    def test_empty_registry_errors(self, tmp_path, monkeypatch, capsys):
        """setup_config.json with empty registry → exit 2."""
        thash = self._setup_translation(tmp_path, monkeypatch)
        ws = tmp_path / "workspace"
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "setup_config.json").write_text(json.dumps({
            "registry": "",
            "repo_name": "myrepo",
        }))
        rc = sim2real.main([
            "--experiment-root", str(tmp_path),
            "build", "--translation", thash,
        ])
        assert rc == 2
        err = capsys.readouterr().err
        assert "registry" in err

    def test_empty_repo_name_errors(self, tmp_path, monkeypatch, capsys):
        """setup_config.json with empty repo_name → exit 2."""
        thash = self._setup_translation(tmp_path, monkeypatch)
        ws = tmp_path / "workspace"
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "setup_config.json").write_text(json.dumps({
            "registry": "ghcr.io/org",
            "repo_name": "",
        }))
        rc = sim2real.main([
            "--experiment-root", str(tmp_path),
            "build", "--translation", thash,
        ])
        assert rc == 2
        err = capsys.readouterr().err
        assert "repo_name" in err

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
    """_cmd_build: cluster resolution error paths."""

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

    def test_cluster_config_no_registry_secret_errors(self, tmp_path, monkeypatch, capsys):
        """cluster_config.json missing registry_creds secret → exit 2."""
        thash = self._setup_translation_with_config(tmp_path, monkeypatch)
        cdir = tmp_path / "workspace" / "clusters" / "test-cluster"
        cdir.mkdir(parents=True)
        (cdir / "cluster_config.json").write_text(json.dumps({
            "namespaces": ["ns1"],
            "secret_names": {},
        }))
        rc = sim2real.main([
            "--experiment-root", str(tmp_path),
            "build", "--translation", thash,
        ])
        assert rc == 2
        err = capsys.readouterr().err
        assert "registry_creds" in err

    def test_unreadable_cluster_config_errors(self, tmp_path, monkeypatch, capsys):
        """OSError reading cluster_config.json → exit 2."""
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


# ─── _cmd_translate error paths ──────────────────────────────────────────────

class TestCmdTranslateBYOGuard:
    """_cmd_translate: BYO algorithm in transfer.yaml → exit 2."""

    def _write_manifest(self, tmp_path, algos, with_component=True):
        """Write a valid v3 transfer.yaml with the given algorithm list."""
        manifest = {
            "kind": "sim2real-transfer",
            "version": 3,
            "scenario": "test-scenario",
            "baselines": [{"name": "base1", "scenario": "base1-scenario"}],
            "algorithms": algos,
        }
        if with_component:
            manifest["component"] = {
                "repo": "https://github.com/org/myrepo",
                "kind": "go",
            }
        (tmp_path / "transfer.yaml").write_text(yaml.dump(manifest))

    def test_byo_algorithm_rejected(self, tmp_path, capsys):
        """An algorithm with byo: true fails at the translate BYO guard."""
        # BYO algos don't require component, but _cmd_translate still
        # checks for byo: true and rejects them.
        self._write_manifest(tmp_path, [
            {"name": "algo1", "byo": True, "defaults": "base1"},
        ], with_component=False)
        rc = sim2real.main([
            "--experiment-root", str(tmp_path),
            "translate",
        ])
        assert rc == 2
        err = capsys.readouterr().err
        assert "cannot translate algorithm" in err
        assert "algo1" in err
        assert "BYO" in err

    def test_non_byo_algorithm_passes_guard(self, tmp_path, monkeypatch, capsys):
        """Non-BYO algorithms pass the BYO guard (may fail later at slicer)."""
        self._write_manifest(tmp_path, [
            {"name": "algo1", "source": "myrepo/algo1.go", "defaults": "base1"},
        ])
        # It will fail at slicer.translation_hash_with_sources, but NOT at BYO guard.
        # The exit code is intentionally not asserted — the point is that the BYO
        # guard does not fire, which is checked via the absence of its error below.
        sim2real.main([
            "--experiment-root", str(tmp_path),
            "translate",
        ])
        # Might fail later but not at BYO check
        err = capsys.readouterr().err
        assert "cannot translate algorithm" not in err


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

    def test_invalid_scenario_name_errors(self, tmp_path, capsys):
        """Invalid scenario name (used as alias) → exit 2."""
        (tmp_path / "transfer.yaml").write_text(yaml.dump({
            "kind": "sim2real-transfer",
            "version": 3,
            "scenario": "-invalid-name",
            "baselines": [{"name": "base1", "scenario": "base1-scenario"}],
            "algorithms": [{"name": "algo1", "source": "x.go", "defaults": "base1"}],
            "component": {"repo": "https://github.com/org/myrepo", "kind": "go"},
        }))
        rc = sim2real.main([
            "--experiment-root", str(tmp_path),
            "translate",
        ])
        assert rc == 2
        err = capsys.readouterr().err
        assert "invalid scenario name" in err

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


class TestCmdTranslateResumeErrors:
    """_cmd_translate --resume error paths."""

    def _write_manifest_with_source(self, tmp_path):
        """Write manifest + source file so slicer can compute hash."""
        src = tmp_path / "myrepo" / "algo1.go"
        src.parent.mkdir(parents=True)
        src.write_text("package algo1")
        (tmp_path / "transfer.yaml").write_text(yaml.dump({
            "kind": "sim2real-transfer",
            "version": 3,
            "scenario": "test-scenario",
            "baselines": [{"name": "base1", "scenario": "base1-scenario"}],
            "algorithms": [{"name": "algo1", "source": "myrepo/algo1.go", "defaults": "base1"}],
            "component": {"repo": "https://github.com/org/myrepo", "kind": "go"},
        }))

    def test_resume_nothing_state_errors(self, tmp_path, capsys):
        """--resume when no translation exists → exit 2."""
        self._write_manifest_with_source(tmp_path)
        rc = sim2real.main([
            "--experiment-root", str(tmp_path),
            "translate", "--resume",
        ])
        assert rc == 2
        err = capsys.readouterr().err
        assert "no translation to resume" in err

    def test_translate_partial_state_without_resume_errors(self, tmp_path, capsys):
        """Partial translation without --resume → exit 2."""
        self._write_manifest_with_source(tmp_path)
        # First, create a translation checkpoint (plain translate)
        rc = sim2real.main([
            "--experiment-root", str(tmp_path),
            "translate",
        ])
        assert rc == 0
        # Now run again without --force or --resume — should be "partial"
        # because the algo output files haven't been written
        rc = sim2real.main([
            "--experiment-root", str(tmp_path),
            "translate",
        ])
        assert rc == 2
        err = capsys.readouterr().err
        assert "incomplete" in err


class TestCmdTranslateAliasCollision:
    """_cmd_translate: alias uniqueness check."""

    def _write_scenario(self, tmp_path, scenario_name="test-scenario"):
        """Write manifest + source for a translate."""
        src = tmp_path / "myrepo" / "algo1.go"
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_text("package algo1")
        (tmp_path / "transfer.yaml").write_text(yaml.dump({
            "kind": "sim2real-transfer",
            "version": 3,
            "scenario": scenario_name,
            "baselines": [{"name": "base1", "scenario": "base1-scenario"}],
            "algorithms": [{"name": "algo1", "source": "myrepo/algo1.go", "defaults": "base1"}],
            "component": {"repo": "https://github.com/org/myrepo", "kind": "go"},
        }))

    def test_alias_collision_without_force_errors(self, tmp_path, capsys):
        """If alias is already assigned to a different hash → exit 2."""
        self._write_scenario(tmp_path, "test-scenario")
        # Create first translation successfully
        rc = sim2real.main([
            "--experiment-root", str(tmp_path),
            "translate",
        ])
        assert rc == 0

        # Now change the source content to get a different hash
        (tmp_path / "myrepo" / "algo1.go").write_text("package algo1_v2")
        rc = sim2real.main([
            "--experiment-root", str(tmp_path),
            "translate",
        ])
        assert rc == 2
        err = capsys.readouterr().err
        assert "already assigned" in err

    def test_alias_collision_with_force_succeeds(self, tmp_path, capsys):
        """--force reassigns the alias."""
        self._write_scenario(tmp_path, "test-scenario")
        # Create first translation
        rc = sim2real.main([
            "--experiment-root", str(tmp_path),
            "translate",
        ])
        assert rc == 0

        # Change source content and use --force
        (tmp_path / "myrepo" / "algo1.go").write_text("package algo1_v2")
        rc = sim2real.main([
            "--experiment-root", str(tmp_path),
            "translate", "--force",
        ])
        assert rc == 0


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


# ─── _cmd_resolve error path ─────────────────────────────────────────────────

class TestCmdResolveErrors:
    """_cmd_resolve: resolve_run failure."""

    def test_resolve_nonexistent_run_errors(self, tmp_path, capsys):
        """Resolve with a nonexistent run → exit 2."""
        rc = sim2real.main([
            "--experiment-root", str(tmp_path),
            "resolve", "--run", "nonexistent-run",
        ])
        assert rc == 2
        err = capsys.readouterr().err
        assert "error:" in err


# ─── _cmd_list_translations coverage ─────────────────────────────────────────

class TestCmdListTranslationsEmpty:
    """_cmd_list_translations: no translations directory."""

    def test_no_translations_dir_prints_none(self, tmp_path, capsys):
        """Empty workspace with no translations → prints 'no translations yet'."""
        rc = sim2real.main([
            "--experiment-root", str(tmp_path),
            "list", "translations",
        ])
        assert rc == 0
        out = capsys.readouterr().out
        assert "no translations" in out.lower() or out.strip() == ""


# ─── _cmd_build --skip-build path ────────────────────────────────────────────

class TestCmdBuildSkipBuild:
    """_cmd_build with --skip-build: skips cluster resolution, prints skip msg."""

    def _setup_for_skip_build(self, tmp_path, monkeypatch):
        """Minimal setup to reach --skip-build path."""
        thash = "c" * 64
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

    def test_skip_build_does_not_require_cluster(self, tmp_path, monkeypatch, capsys):
        """--skip-build path skips cluster resolution entirely."""
        thash = self._setup_for_skip_build(tmp_path, monkeypatch)
        # No cluster dir — should still succeed with --skip-build
        rc = sim2real.main([
            "--experiment-root", str(tmp_path),
            "build", "--translation", thash, "--skip-build",
        ])
        assert rc == 0
        out = capsys.readouterr().out
        assert "skipped" in out.lower()


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


# ─── _cmd_build completeness check ───────────────────────────────────────────

class TestCmdBuildCompletenessCheck:
    """_cmd_build: missing algo output file → exit 2."""

    def test_missing_algo_output_file_errors(self, tmp_path, monkeypatch, capsys):
        """Algorithm without generated output → exit 2."""
        monkeypatch.setattr("pipeline.lib.build.check_skopeo", lambda: None)
        thash = "e" * 64
        tdir = tmp_path / "workspace" / "translations" / thash
        tdir.mkdir(parents=True)
        # algo1 gen dir exists but NO algo1_output.json
        (tdir / "generated" / "algo1").mkdir(parents=True)
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
        rc = sim2real.main([
            "--experiment-root", str(tmp_path),
            "build", "--translation", thash,
        ])
        assert rc == 2
        err = capsys.readouterr().err
        assert "incomplete" in err
        assert "algo1" in err
