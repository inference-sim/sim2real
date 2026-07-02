"""Tests for pipeline/sim2real.py — sim2real CLI top-level entry."""

from __future__ import annotations

import argparse
import json
from unittest.mock import patch

import pytest
import yaml

from pipeline import sim2real
from pipeline.lib import layout


@pytest.fixture(autouse=True)
def _isolated_experiment_root(tmp_path):
    layout._EXPERIMENT_ROOT = tmp_path
    yield
    layout._EXPERIMENT_ROOT = None


class TestValidateAlgorithmName:
    def test_accepts_lowercase_letters(self):
        assert sim2real._validate_algorithm_name("softreflective") == "softreflective"

    def test_accepts_hyphens_and_digits(self):
        assert sim2real._validate_algorithm_name("algo-v2-final") == "algo-v2-final"

    def test_accepts_uppercase(self):
        assert sim2real._validate_algorithm_name("SoftReflective") == "SoftReflective"

    def test_accepts_underscore(self):
        assert sim2real._validate_algorithm_name("soft_reflective") == "soft_reflective"

    def test_rejects_empty(self):
        with pytest.raises(argparse.ArgumentTypeError):
            sim2real._validate_algorithm_name("")

    def test_rejects_whitespace(self):
        with pytest.raises(argparse.ArgumentTypeError):
            sim2real._validate_algorithm_name("soft reflective")

    def test_rejects_leading_hyphen(self):
        with pytest.raises(argparse.ArgumentTypeError):
            sim2real._validate_algorithm_name("-algo")

    def test_rejects_dot(self):
        with pytest.raises(argparse.ArgumentTypeError):
            sim2real._validate_algorithm_name(".")

    def test_rejects_double_dot(self):
        with pytest.raises(argparse.ArgumentTypeError):
            sim2real._validate_algorithm_name("..")

    def test_rejects_oversized(self):
        with pytest.raises(argparse.ArgumentTypeError):
            sim2real._validate_algorithm_name("a" * 129)


class TestExtractDigest:
    def test_extracts_digest_when_present(self):
        ref = "ghcr.io/foo/bar@sha256:" + "a" * 64
        assert sim2real._extract_digest_from_ref(ref) == "sha256:" + "a" * 64

    def test_returns_none_when_tag_only(self):
        assert sim2real._extract_digest_from_ref("ghcr.io/foo/bar:v1.0") is None

    def test_returns_none_when_no_tag_no_digest(self):
        assert sim2real._extract_digest_from_ref("ghcr.io/foo/bar") is None

    def test_rejects_malformed_digest(self):
        # Not 64 hex chars
        assert sim2real._extract_digest_from_ref("ghcr.io/foo@sha256:aabb") is None

    def test_rejects_non_hex_digest(self):
        assert sim2real._extract_digest_from_ref("ghcr.io/foo@sha256:" + "z" * 64) is None


class TestComputeTranslationHash:
    def test_is_deterministic(self):
        h1 = sim2real._compute_translation_hash("sha256:aa", b"config: 1\n", "algo")
        h2 = sim2real._compute_translation_hash("sha256:aa", b"config: 1\n", "algo")
        assert h1 == h2
        assert len(h1) == 64  # sha256 hex length

    def test_changes_with_algorithm_name(self):
        h1 = sim2real._compute_translation_hash("sha256:aa", b"c", "a")
        h2 = sim2real._compute_translation_hash("sha256:aa", b"c", "b")
        assert h1 != h2

    def test_changes_with_config_content(self):
        h1 = sim2real._compute_translation_hash("sha256:aa", b"a", "algo")
        h2 = sim2real._compute_translation_hash("sha256:aa", b"b", "algo")
        assert h1 != h2

    def test_changes_with_image_ref(self):
        h1 = sim2real._compute_translation_hash("sha256:aa", b"c", "algo")
        h2 = sim2real._compute_translation_hash("sha256:bb", b"c", "algo")
        assert h1 != h2

    def test_offline_ref_produces_stable_hash(self):
        h1 = sim2real._compute_translation_hash("ghcr.io/foo:v1", b"c", "algo")
        h2 = sim2real._compute_translation_hash("ghcr.io/foo:v1", b"c", "algo")
        assert h1 == h2


class TestBuildSchemas:
    def test_translation_output_schema(self):
        # Updated to step-2 shape: image_ref/image_digest live per-algo.
        out = sim2real._build_translation_output(
            algorithm_name="softreflective",
            image_ref="ghcr.io/foo:v1",
            image_digest=None,
            config_path="generated/softreflective/softreflective_config.yaml",
            translation_hash="a" * 64,
            source="byo",
            alias="softreflective",
            created_at="2026-07-01T14:00:00Z",
        )
        assert out == {
            "version": 1,
            "translation_hash": "a" * 64,
            "source": "byo",
            "alias": "softreflective",
            "algorithms": [{
                "name": "softreflective",
                "source_path": None,
                "source_sha256": None,
                "config_path": "generated/softreflective/softreflective_config.yaml",
                "image_ref": "ghcr.io/foo:v1",
                "image_digest": None,
            }],
            "created_at": "2026-07-01T14:00:00Z",
        }

    def test_registered_schema_with_digest(self):
        reg = sim2real._build_registered(
            image_ref="ghcr.io/foo@sha256:" + "b" * 64,
            image_digest="sha256:" + "b" * 64,
            registered_at="2026-07-01T14:00:00Z",
        )
        assert reg == {
            "version": 1,
            "image_ref": "ghcr.io/foo@sha256:" + "b" * 64,
            "image_digest": "sha256:" + "b" * 64,
            "source": "byo",
            "registered_at": "2026-07-01T14:00:00Z",
        }

    def test_registered_schema_offline(self):
        reg = sim2real._build_registered(
            image_ref="ghcr.io/foo:v1",
            image_digest=None,
            registered_at="2026-07-01T14:00:00Z",
        )
        assert reg["image_digest"] is None
        assert reg["image_ref"] == "ghcr.io/foo:v1"


class TestBuildTranslationOutputV2:
    def test_new_schema_shape(self):
        out = sim2real._build_translation_output(
            algorithm_name="softreflective",
            image_ref="ghcr.io/x/sr:v1",
            image_digest="sha256:aa",
            config_path="generated/softreflective/softreflective_config.yaml",
            translation_hash="a" * 64,
            source="byo",
            alias="softreflective",
            created_at="2026-07-02T14:00:00Z",
        )
        # Top-level image_ref removed; now per-algo.
        assert "image_ref" not in out
        assert "image_digest" not in out
        assert out["alias"] == "softreflective"
        assert out["source"] == "byo"
        assert out["version"] == 1
        assert out["translation_hash"] == "a" * 64
        assert out["created_at"] == "2026-07-02T14:00:00Z"
        assert len(out["algorithms"]) == 1
        algo = out["algorithms"][0]
        assert algo["name"] == "softreflective"
        assert algo["image_ref"] == "ghcr.io/x/sr:v1"
        assert algo["image_digest"] == "sha256:aa"
        assert algo["config_path"] == \
            "generated/softreflective/softreflective_config.yaml"
        assert algo["source_path"] is None
        assert algo["source_sha256"] is None


class TestRegisterTranslation:
    def _write_overlay(self, tmp_path, content=b"scorer: mine\n"):
        p = tmp_path / "treatment.yaml"
        p.write_bytes(content)
        return p

    def test_creates_translation_dir_and_files(self, tmp_path):
        cfg = self._write_overlay(tmp_path)
        thash, status = sim2real._register_translation(
            algorithm_name="softreflective",
            image_ref="ghcr.io/foo:v1",
            config_path=cfg,
            baseline_config_path=None,
            registered_hash=None,
            now_iso="2026-07-01T14:00:00Z",
        )
        assert status == "created"
        assert len(thash) == 64
        assert layout.translation_output_path(thash).exists()
        assert layout.registered_path(thash).exists()
        assert layout.generated_config_path(thash, "softreflective").exists()

    def test_translation_output_contents(self, tmp_path):
        # Step-2 schema: image_ref lives per-algo; alias at top level.
        cfg = self._write_overlay(tmp_path)
        thash, _ = sim2real._register_translation(
            algorithm_name="softreflective",
            image_ref="ghcr.io/foo:v1",
            config_path=cfg,
            baseline_config_path=None,
            registered_hash=None,
            now_iso="2026-07-01T14:00:00Z",
        )
        out = json.loads(layout.translation_output_path(thash).read_text())
        assert out["source"] == "byo"
        assert out["alias"] == "softreflective"
        assert out["translation_hash"] == thash
        assert out["version"] == 1
        assert len(out["algorithms"]) == 1
        algo = out["algorithms"][0]
        assert algo["name"] == "softreflective"
        assert algo["image_ref"] == "ghcr.io/foo:v1"

    def test_registered_records_digest_when_present(self, tmp_path):
        cfg = self._write_overlay(tmp_path)
        ref = "ghcr.io/foo@sha256:" + "a" * 64
        thash, _ = sim2real._register_translation(
            algorithm_name="softreflective",
            image_ref=ref,
            config_path=cfg,
            baseline_config_path=None,
            registered_hash=None,
            now_iso="2026-07-01T14:00:00Z",
        )
        reg = json.loads(layout.registered_path(thash).read_text())
        assert reg["image_digest"] == "sha256:" + "a" * 64

    def test_registered_null_digest_when_tag_only(self, tmp_path):
        cfg = self._write_overlay(tmp_path)
        thash, _ = sim2real._register_translation(
            algorithm_name="softreflective",
            image_ref="ghcr.io/foo:v1",
            config_path=cfg,
            baseline_config_path=None,
            registered_hash=None,
            now_iso="2026-07-01T14:00:00Z",
        )
        reg = json.loads(layout.registered_path(thash).read_text())
        assert reg["image_digest"] is None

    def test_writes_treatment_overlay(self, tmp_path):
        cfg = self._write_overlay(tmp_path, content=b"scorer: custom\n")
        thash, _ = sim2real._register_translation(
            algorithm_name="softreflective",
            image_ref="ghcr.io/foo:v1",
            config_path=cfg,
            baseline_config_path=None,
            registered_hash=None,
            now_iso="2026-07-01T14:00:00Z",
        )
        assert (
            layout.generated_config_path(thash, "softreflective").read_bytes()
            == b"scorer: custom\n"
        )

    def test_writes_baseline_config_when_provided(self, tmp_path):
        cfg = self._write_overlay(tmp_path)
        baseline = tmp_path / "baseline.yaml"
        baseline.write_bytes(b"baseline: config\n")
        thash, _ = sim2real._register_translation(
            algorithm_name="softreflective",
            image_ref="ghcr.io/foo:v1",
            config_path=cfg,
            baseline_config_path=baseline,
            registered_hash=None,
            now_iso="2026-07-01T14:00:00Z",
        )
        gen_baseline = layout.translation_dir(thash) / "generated" / "baseline_config.yaml"
        assert gen_baseline.read_bytes() == b"baseline: config\n"

    def test_idempotent_second_call_same_inputs(self, tmp_path):
        cfg = self._write_overlay(tmp_path)
        args = dict(
            algorithm_name="softreflective",
            image_ref="ghcr.io/foo:v1",
            config_path=cfg,
            baseline_config_path=None,
            registered_hash=None,
            now_iso="2026-07-01T14:00:00Z",
        )
        h1, s1 = sim2real._register_translation(**args)
        h2, s2 = sim2real._register_translation(**args)
        assert h1 == h2
        assert s1 == "created"
        assert s2 == "idempotent"

    def test_hash_collision_different_algorithm_errors(self, tmp_path):
        cfg = self._write_overlay(tmp_path)
        thash, _ = sim2real._register_translation(
            algorithm_name="softreflective",
            image_ref="ghcr.io/foo:v1",
            config_path=cfg,
            baseline_config_path=None,
            registered_hash=None,
            now_iso="2026-07-01T14:00:00Z",
        )
        # Corrupt the existing translation_output.json to name a different algo.
        out_path = layout.translation_output_path(thash)
        out = json.loads(out_path.read_text())
        out["algorithms"] = [{"name": "otheralgo"}]
        out_path.write_text(json.dumps(out))
        with pytest.raises(ValueError, match="algorithm name mismatch"):
            sim2real._register_translation(
                algorithm_name="softreflective",
                image_ref="ghcr.io/foo:v1",
                config_path=cfg,
                baseline_config_path=None,
                registered_hash=None,
                now_iso="2026-07-01T14:00:00Z",
            )

    def test_registered_hash_mismatch_errors(self, tmp_path):
        cfg = self._write_overlay(tmp_path)
        with pytest.raises(RuntimeError, match="registered-hash mismatch"):
            sim2real._register_translation(
                algorithm_name="softreflective",
                image_ref="ghcr.io/foo:v1",
                config_path=cfg,
                baseline_config_path=None,
                registered_hash="deadbeef" * 8,
                now_iso="2026-07-01T14:00:00Z",
            )

    def test_registered_hash_match_succeeds(self, tmp_path):
        cfg = self._write_overlay(tmp_path)
        expected = sim2real._compute_translation_hash(
            "ghcr.io/foo:v1", cfg.read_bytes(), "softreflective"
        )
        thash, status = sim2real._register_translation(
            algorithm_name="softreflective",
            image_ref="ghcr.io/foo:v1",
            config_path=cfg,
            baseline_config_path=None,
            registered_hash=expected,
            now_iso="2026-07-01T14:00:00Z",
        )
        assert thash == expected
        assert status == "created"

    def test_partial_write_missing_registered_json_raises(self, tmp_path):
        # Simulate: an earlier register wrote translation_output.json but
        # died before writing registered.json (disk full, killed, etc.).
        cfg = self._write_overlay(tmp_path)
        thash, _ = sim2real._register_translation(
            algorithm_name="softreflective",
            image_ref="ghcr.io/foo:v1",
            config_path=cfg,
            baseline_config_path=None,
            registered_hash=None,
            now_iso="2026-07-01T14:00:00Z",
        )
        layout.registered_path(thash).unlink()
        with pytest.raises(RuntimeError, match="incomplete"):
            sim2real._register_translation(
                algorithm_name="softreflective",
                image_ref="ghcr.io/foo:v1",
                config_path=cfg,
                baseline_config_path=None,
                registered_hash=None,
                now_iso="2026-07-01T14:00:00Z",
            )

    def test_partial_write_missing_generated_config_raises(self, tmp_path):
        cfg = self._write_overlay(tmp_path)
        thash, _ = sim2real._register_translation(
            algorithm_name="softreflective",
            image_ref="ghcr.io/foo:v1",
            config_path=cfg,
            baseline_config_path=None,
            registered_hash=None,
            now_iso="2026-07-01T14:00:00Z",
        )
        layout.generated_config_path(thash, "softreflective").unlink()
        with pytest.raises(RuntimeError, match="incomplete"):
            sim2real._register_translation(
                algorithm_name="softreflective",
                image_ref="ghcr.io/foo:v1",
                config_path=cfg,
                baseline_config_path=None,
                registered_hash=None,
                now_iso="2026-07-01T14:00:00Z",
            )


class TestBuildParser:
    def test_parses_translation_register(self):
        parser = sim2real.build_parser()
        args = parser.parse_args([
            "translation", "register",
            "--algorithm", "softreflective",
            "--image", "ghcr.io/foo:v1",
            "--config", "/tmp/treatment.yaml",
        ])
        assert args.command == "translation"
        assert args.subcommand == "register"
        assert args.algorithm == "softreflective"
        assert args.image == "ghcr.io/foo:v1"
        assert args.config == "/tmp/treatment.yaml"
        assert args.baseline_config is None
        assert args.registered_hash is None

    def test_accepts_baseline_and_registered_hash(self):
        parser = sim2real.build_parser()
        args = parser.parse_args([
            "translation", "register",
            "--algorithm", "softreflective",
            "--image", "ghcr.io/foo:v1",
            "--config", "/tmp/treatment.yaml",
            "--baseline-config", "/tmp/baseline.yaml",
            "--registered-hash", "abcd" * 16,
        ])
        assert args.baseline_config == "/tmp/baseline.yaml"
        assert args.registered_hash == "abcd" * 16

    def test_rejects_bad_algorithm_name(self):
        # Leading hyphen fails the shared regex; use it instead of Bad_Name
        # (uppercase+underscore are now accepted per step-2 widening).
        parser = sim2real.build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args([
                "translation", "register",
                "--algorithm", "-bad-name",
                "--image", "ghcr.io/foo:v1",
                "--config", "/tmp/treatment.yaml",
            ])


class TestMainEndToEnd:
    def test_happy_path(self, tmp_path, capsys):
        cfg = tmp_path / "treatment.yaml"
        cfg.write_text("scorer: mine\n")
        rc = sim2real.main([
            "--experiment-root", str(tmp_path),
            "translation", "register",
            "--algorithm", "softreflective",
            "--image", "ghcr.io/foo:v1",
            "--config", str(cfg),
        ])
        assert rc == 0
        captured = capsys.readouterr()
        assert "registered translation" in captured.out
        # Warn about null digest since image ref is tag-only.
        assert "image_digest recorded as null" in captured.err

    def test_idempotent_second_run(self, tmp_path):
        cfg = tmp_path / "treatment.yaml"
        cfg.write_text("scorer: mine\n")
        argv = [
            "--experiment-root", str(tmp_path),
            "translation", "register",
            "--algorithm", "softreflective",
            "--image", "ghcr.io/foo:v1",
            "--config", str(cfg),
        ]
        assert sim2real.main(argv) == 0
        assert sim2real.main(argv) == 0  # idempotent, still exit 0

    def test_malformed_config_errors_no_writes(self, tmp_path):
        cfg = tmp_path / "bad.yaml"
        cfg.write_text("scorer: [unclosed\n")  # invalid YAML
        rc = sim2real.main([
            "--experiment-root", str(tmp_path),
            "translation", "register",
            "--algorithm", "softreflective",
            "--image", "ghcr.io/foo:v1",
            "--config", str(cfg),
        ])
        assert rc == 2
        # No translation dir should have been created.
        assert not layout.translations_dir().exists() or not any(
            layout.translations_dir().iterdir()
        )

    def test_missing_config_errors(self, tmp_path):
        rc = sim2real.main([
            "--experiment-root", str(tmp_path),
            "translation", "register",
            "--algorithm", "softreflective",
            "--image", "ghcr.io/foo:v1",
            "--config", str(tmp_path / "does-not-exist.yaml"),
        ])
        assert rc == 2

    def test_registered_hash_mismatch_exits_2(self, tmp_path):
        cfg = tmp_path / "treatment.yaml"
        cfg.write_text("scorer: mine\n")
        rc = sim2real.main([
            "--experiment-root", str(tmp_path),
            "translation", "register",
            "--algorithm", "softreflective",
            "--image", "ghcr.io/foo:v1",
            "--config", str(cfg),
            "--registered-hash", "deadbeef" * 8,
        ])
        assert rc == 2

    def test_digest_ref_no_null_warning(self, tmp_path, capsys):
        cfg = tmp_path / "treatment.yaml"
        cfg.write_text("scorer: mine\n")
        rc = sim2real.main([
            "--experiment-root", str(tmp_path),
            "translation", "register",
            "--algorithm", "softreflective",
            "--image", "ghcr.io/foo@sha256:" + "a" * 64,
            "--config", str(cfg),
        ])
        assert rc == 0
        captured = capsys.readouterr()
        assert "image_digest recorded as null" not in captured.err

    def test_oserror_from_register_returns_2_not_traceback(
        self, tmp_path, capsys, monkeypatch
    ):
        # translation_output.json is now written via _atomic_write_json
        # (tempfile + os.replace), not write_text. Patch the helper directly.
        cfg = tmp_path / "treatment.yaml"
        cfg.write_text("scorer: mine\n")

        # Wrap: call the real impl for non-output.json paths.
        original = sim2real._atomic_write_json

        def patched(path, data):
            if path.name == "translation_output.json":
                raise OSError("simulated: disk full")
            return original(path, data)

        monkeypatch.setattr(sim2real, "_atomic_write_json", patched)

        rc = sim2real.main([
            "--experiment-root", str(tmp_path),
            "translation", "register",
            "--algorithm", "softreflective",
            "--image", "ghcr.io/foo:v1",
            "--config", str(cfg),
        ])
        assert rc == 2
        captured = capsys.readouterr()
        assert "error:" in captured.err
        assert "disk full" in captured.err


class TestUseCommand:
    def _setup_run_dir(self, tmp_path, run_name):
        run_dir = tmp_path / "workspace" / "runs" / run_name
        run_dir.mkdir(parents=True)
        (run_dir / "run_metadata.json").write_text(
            json.dumps({
                "version": 1,
                "run_name": run_name,
                "translation_hash": "abc123",
                "cluster_id": "ocp-east",
                "params_hash": "def456",
                "image_tag": "ghcr.io/foo:v1",
                "assembled_at": "2026-07-01T14:00:00Z",
            })
        )
        return run_dir

    def test_use_updates_current_run(self, tmp_path):
        self._setup_run_dir(tmp_path, "trial-1")
        rc = sim2real.main([
            "--experiment-root", str(tmp_path),
            "use", "--run", "trial-1",
        ])
        assert rc == 0
        cfg = json.loads((tmp_path / "workspace" / "setup_config.json").read_text())
        assert cfg["current_run"] == "trial-1"

    def test_use_preserves_other_setup_config_keys(self, tmp_path):
        self._setup_run_dir(tmp_path, "trial-1")
        cfg_path = tmp_path / "workspace" / "setup_config.json"
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(json.dumps({
            "registry": "ghcr.io/me",
            "repo_name": "sim2real",
            "current_run": "trial-0",
            "orchestrator_image": "ghcr.io/me/orch:v1",
        }))
        rc = sim2real.main([
            "--experiment-root", str(tmp_path),
            "use", "--run", "trial-1",
        ])
        assert rc == 0
        cfg = json.loads(cfg_path.read_text())
        assert cfg["current_run"] == "trial-1"
        assert cfg["registry"] == "ghcr.io/me"
        assert cfg["repo_name"] == "sim2real"
        assert cfg["orchestrator_image"] == "ghcr.io/me/orch:v1"

    def test_use_nonexistent_run_errors_with_hint(self, tmp_path, capsys):
        rc = sim2real.main([
            "--experiment-root", str(tmp_path),
            "use", "--run", "ghost",
        ])
        assert rc == 2
        err = capsys.readouterr().err
        assert "run doesn't exist; try 'sim2real list runs'" in err

    def test_use_run_without_metadata_errors(self, tmp_path, capsys):
        run_dir = tmp_path / "workspace" / "runs" / "half-baked"
        run_dir.mkdir(parents=True)
        # No run_metadata.json inside.
        rc = sim2real.main([
            "--experiment-root", str(tmp_path),
            "use", "--run", "half-baked",
        ])
        assert rc == 2
        err = capsys.readouterr().err
        assert "run doesn't exist; try 'sim2real list runs'" in err


class TestListRunsCommand:
    def _write_run(self, tmp_path, name, translation, cluster, assembled, mtime_offset=0):
        run_dir = tmp_path / "workspace" / "runs" / name
        run_dir.mkdir(parents=True)
        meta = run_dir / "run_metadata.json"
        meta.write_text(json.dumps({
            "version": 1,
            "run_name": name,
            "translation_hash": translation,
            "cluster_id": cluster,
            "params_hash": "p",
            "image_tag": "ghcr.io/foo:v1",
            "assembled_at": assembled,
        }))
        if mtime_offset:
            import os
            st = meta.stat()
            os.utime(meta, (st.st_atime, st.st_mtime + mtime_offset))
        return meta

    def _write_setup_config(self, tmp_path, current_run):
        cfg = tmp_path / "workspace" / "setup_config.json"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(json.dumps({"current_run": current_run}))

    def test_missing_runs_dir_prints_no_runs_yet(self, tmp_path, capsys):
        rc = sim2real.main([
            "--experiment-root", str(tmp_path),
            "list", "runs",
        ])
        assert rc == 0
        assert capsys.readouterr().out.strip() == "no runs yet"

    def test_empty_runs_dir_prints_no_runs_yet(self, tmp_path, capsys):
        (tmp_path / "workspace" / "runs").mkdir(parents=True)
        rc = sim2real.main([
            "--experiment-root", str(tmp_path),
            "list", "runs",
        ])
        assert rc == 0
        assert capsys.readouterr().out.strip() == "no runs yet"

    def test_mtime_ordering_newest_first(self, tmp_path, capsys):
        # Write trial-1 first (older mtime), then trial-2 with a +100s bump.
        self._write_run(tmp_path, "trial-1", "abc12345", "ocp-east",
                        "2026-07-01T12:10:00Z")
        self._write_run(tmp_path, "trial-2", "abc12345", "ocp-east",
                        "2026-07-01T14:32:00Z", mtime_offset=100)
        rc = sim2real.main([
            "--experiment-root", str(tmp_path),
            "list", "runs",
        ])
        assert rc == 0
        lines = capsys.readouterr().out.splitlines()
        # First line is the header, then trial-2 (newest), then trial-1.
        assert "RUN_NAME" in lines[0] and "TRANSLATION" in lines[0]
        assert "CLUSTER" in lines[0] and "ASSEMBLED" in lines[0]
        assert lines[1].split()[0] == "trial-2"
        assert lines[2].split()[0] == "trial-1"

    def test_current_run_marker(self, tmp_path, capsys):
        self._write_run(tmp_path, "trial-1", "abc12345", "ocp-east",
                        "2026-07-01T12:10:00Z")
        self._write_run(tmp_path, "trial-2", "abc12345", "ocp-east",
                        "2026-07-01T14:32:00Z", mtime_offset=100)
        self._write_setup_config(tmp_path, "trial-1")
        rc = sim2real.main([
            "--experiment-root", str(tmp_path),
            "list", "runs",
        ])
        assert rc == 0
        lines = capsys.readouterr().out.splitlines()
        # trial-2 (newest, no marker); trial-1 (current, has "*").
        assert lines[1].lstrip().startswith("trial-2")
        assert lines[2].lstrip().startswith("* trial-1")

    def test_no_current_run_prints_no_marker(self, tmp_path, capsys):
        self._write_run(tmp_path, "trial-1", "abc12345", "ocp-east",
                        "2026-07-01T12:10:00Z")
        rc = sim2real.main([
            "--experiment-root", str(tmp_path),
            "list", "runs",
        ])
        assert rc == 0
        lines = capsys.readouterr().out.splitlines()
        # No line starts with '*'.
        for line in lines[1:]:
            assert not line.lstrip().startswith("*")

    def test_translation_hash_truncated_to_8_chars(self, tmp_path, capsys):
        self._write_run(tmp_path, "trial-1", "a" * 64, "ocp-east",
                        "2026-07-01T12:10:00Z")
        rc = sim2real.main([
            "--experiment-root", str(tmp_path),
            "list", "runs",
        ])
        assert rc == 0
        out = capsys.readouterr().out
        # First 8 chars of hash appear as a token.
        assert "aaaaaaaa" in out
        # The full 64-char hash should NOT appear on the run's data line.
        data_line = [ln for ln in out.splitlines() if "trial-1" in ln][0]
        assert "a" * 64 not in data_line

    def test_malformed_metadata_shows_question_marks(self, tmp_path, capsys):
        run_dir = tmp_path / "workspace" / "runs" / "trial-broken"
        run_dir.mkdir(parents=True)
        (run_dir / "run_metadata.json").write_text("{not valid json")
        rc = sim2real.main([
            "--experiment-root", str(tmp_path),
            "list", "runs",
        ])
        assert rc == 0
        out = capsys.readouterr().out
        assert "trial-broken" in out
        assert "?" in out  # placeholder for unreadable metadata

    def test_missing_metadata_skips_directory(self, tmp_path, capsys):
        # Directory with no run_metadata.json is not a run.
        (tmp_path / "workspace" / "runs" / "not-a-run").mkdir(parents=True)
        self._write_run(tmp_path, "trial-1", "abc12345", "ocp-east",
                        "2026-07-01T12:10:00Z")
        rc = sim2real.main([
            "--experiment-root", str(tmp_path),
            "list", "runs",
        ])
        assert rc == 0
        out = capsys.readouterr().out
        assert "trial-1" in out
        assert "not-a-run" not in out


class TestAssembleCommand:
    def _make_minimal_registration(self, tmp_path):
        cfg = tmp_path / "treatment.yaml"
        cfg.write_bytes(
            b"scenario:\n  - name: test-scenario\n"
            b"    inferenceExtension:\n      pluginsConfigFile: sr.yaml\n"
        )
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

    def _bootstrap_experiment(self, tmp_path):
        cluster_id = "ocp-east"
        cluster_dir = tmp_path / "workspace" / "clusters" / cluster_id
        cluster_dir.mkdir(parents=True)
        self._write_json(
            cluster_dir / "cluster_config.json",
            {
                "cluster_id": cluster_id,
                "namespaces": ["ns0"],
                "secret_names": {"hf_token": "hf"},
                "workspaces": {},
            },
        )
        manifest = {
            "kind": "sim2real-transfer",
            "version": 3,
            "scenario": "test-scenario",
            "component": {"repo": "acme/foo", "kind": "gaie"},
            "context": {"text": "", "files": []},
            "baselines": [
                {"name": "baseline", "scenario": "baselines/base.yaml"}
            ],
            "algorithms": [
                {"name": "sr", "source": "algo/sr.py", "defaults": "baseline"}
            ],
            "workloads": ["workloads/w1.yaml"],
            "defaults": {"disable": []},
        }
        self._write_yaml(tmp_path / "transfer.yaml", manifest)
        self._write_yaml(
            tmp_path / "baselines" / "base.yaml",
            {"scenario": [{"name": "test-scenario", "model": {"name": "M"}}]},
        )
        self._write_yaml(
            tmp_path / "workloads" / "w1.yaml",
            {"name": "w1", "num_requests": 1},
        )
        (tmp_path / "algo").mkdir()
        (tmp_path / "algo" / "sr.py").write_text("# stub\n")
        return cluster_id

    def test_success_produces_run_dir(self, tmp_path, capsys):
        thash = self._make_minimal_registration(tmp_path)
        cluster_id = self._bootstrap_experiment(tmp_path)
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
        cluster_id = self._bootstrap_experiment(tmp_path)
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
        assert "--force" in capsys.readouterr().err

    def test_force_overwrites(self, tmp_path, capsys):
        thash = self._make_minimal_registration(tmp_path)
        cluster_id = self._bootstrap_experiment(tmp_path)
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
        self._make_minimal_registration(tmp_path)
        cluster_id = self._bootstrap_experiment(tmp_path)
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

    def test_warns_on_skipped_algorithms(self, tmp_path, capsys):
        thash = self._make_minimal_registration(tmp_path)
        cluster_id = self._bootstrap_experiment(tmp_path)
        # Add an unregistered algorithm to the manifest.
        manifest_path = tmp_path / "transfer.yaml"
        manifest = yaml.safe_load(manifest_path.read_text())
        manifest["algorithms"].append(
            {"name": "cc", "source": "algo/cc.py", "defaults": "baseline"}
        )
        manifest_path.write_text(yaml.dump(manifest, sort_keys=False))
        (tmp_path / "algo" / "cc.py").write_text("# stub\n")
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
        out = capsys.readouterr()
        assert "cc" in out.err
        assert "skipped" in out.err


class TestAliasCollision:
    def _seed_register(self, tmp_path, algo, image, config_yaml):
        cfg = tmp_path / f"{algo}.yaml"
        cfg.write_text(config_yaml)
        thash, status = sim2real._register_translation(
            algorithm_name=algo,
            image_ref=image,
            config_path=cfg,
            baseline_config_path=None,
            registered_hash=None,
            now_iso="2026-07-02T14:00:00Z",
        )
        return thash

    def test_same_alias_same_content_is_idempotent(self, tmp_path):
        h1 = self._seed_register(tmp_path, "algo", "ghcr.io/x:v1", "a: 1\n")
        h2 = self._seed_register(tmp_path, "algo", "ghcr.io/x:v1", "a: 1\n")
        assert h1 == h2

    def test_alias_collision_without_force_raises(self, tmp_path):
        self._seed_register(tmp_path, "algo", "ghcr.io/x:v1", "a: 1\n")
        cfg = tmp_path / "different.yaml"
        cfg.write_text("a: 2\n")
        with pytest.raises(RuntimeError, match="already assigned"):
            sim2real._register_translation(
                algorithm_name="algo",
                image_ref="ghcr.io/x:v1",
                config_path=cfg,
                baseline_config_path=None,
                registered_hash=None,
                now_iso="2026-07-02T14:00:00Z",
            )

    def test_force_reassigns_alias_and_clears_previous(self, tmp_path):
        from pipeline.lib import translation_ref
        h_old = self._seed_register(tmp_path, "algo", "ghcr.io/x:v1", "a: 1\n")
        cfg = tmp_path / "different.yaml"
        cfg.write_text("a: 2\n")
        h_new, _status = sim2real._register_translation(
            algorithm_name="algo",
            image_ref="ghcr.io/x:v1",
            config_path=cfg,
            baseline_config_path=None,
            registered_hash=None,
            now_iso="2026-07-02T14:00:00Z",
            force=True,
        )
        assert h_new != h_old
        # New translation carries the alias.
        assert translation_ref.find_by_alias("algo") == h_new
        # Old translation's alias is null; it's still reachable by hash.
        old_data = translation_ref.read_translation_output(
            layout.translation_output_path(h_old)
        )
        assert old_data["alias"] is None
        assert layout.translation_dir(h_old).exists()


class TestAssembleResolvesAlias:
    def test_assemble_accepts_alias(self, tmp_path, monkeypatch):
        # This is a smoke test — we mock assemble_run to just capture
        # the resolved hash. Full assemble behavior is exercised in
        # test_assemble_run.py.
        cfg = tmp_path / "algo.yaml"
        cfg.write_text("scenario: []\n")
        thash, _ = sim2real._register_translation(
            algorithm_name="my-algo",
            image_ref="ghcr.io/x:v1",
            config_path=cfg,
            baseline_config_path=None,
            registered_hash=None,
            now_iso="2026-07-02T14:00:00Z",
        )

        captured = {}
        def fake_assemble(*, translation_hash, translation_ref, cluster_id,
                          run_name, experiment_root, manifest_path,
                          force, now_iso):
            captured["hash"] = translation_hash
            captured["ref"] = translation_ref

        monkeypatch.setattr(
            sim2real._assemble_run_lib, "assemble_run", fake_assemble
        )
        # Stub a minimal v3-valid transfer.yaml so the new image_ref pre-check
        # in _cmd_assemble (which now calls manifest.load_manifest) succeeds.
        # No algorithms declared here → the pre-check's declared_names ∩
        # recorded_by_name is empty, so it trivially passes.
        (tmp_path / "transfer.yaml").write_text(yaml.safe_dump({
            "kind": "sim2real-transfer",
            "version": 3,
            "scenario": "smoke",
            "baselines": [{"name": "base", "scenario": "baselines/base.yaml"}],
            "component": {"repo": "example.com/x/y", "kind": "scorer"},
            "context": {"text": "", "files": []},
        }))
        parser = sim2real.build_parser()
        args = parser.parse_args([
            "--experiment-root", str(tmp_path),
            "assemble",
            "--translation", "my-algo",
            "--cluster", "cX",
            "--run", "r1",
        ])
        sim2real.layout.set_experiment_root(str(tmp_path))
        # Mocking cluster_config lookup is out of scope here; the fake
        # replaces assemble_run entirely so cluster_config is never read.
        rc = sim2real._cmd_assemble(args)
        assert rc == 0
        assert captured["hash"] == thash
        assert captured["ref"] == "my-algo"


class TestListTranslations:
    def test_empty_prints_no_translations(self, capsys, tmp_path):
        # translations_dir absent.
        rc = sim2real._cmd_list_translations(
            sim2real.build_parser().parse_args(["list", "translations"])
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "no translations yet" in out

    def test_shows_alias_hash_source_images_created(self, capsys, tmp_path):
        from pipeline.lib import layout
        layout.set_experiment_root(tmp_path)
        base = layout.translations_dir()
        base.mkdir(parents=True)

        h1 = "a" * 64
        (base / h1).mkdir()
        (base / h1 / "translation_output.json").write_text(json.dumps({
            "version": 1,
            "translation_hash": h1,
            "source": "skill",
            "alias": "softreflective-v1",
            "algorithms": [{"name": "sr", "image_ref": "quay.io/x:v1"}],
            "created_at": "2026-07-02T14:00:00Z",
        }))

        h2 = "b" * 64
        (base / h2).mkdir()
        (base / h2 / "translation_output.json").write_text(json.dumps({
            "version": 1,
            "translation_hash": h2,
            "source": "skill",
            "alias": "compare-a-b",
            "algorithms": [
                {"name": "a", "image_ref": None},
                {"name": "b", "image_ref": None},
            ],
            "created_at": "2026-07-02T14:30:00Z",
        }))

        h3 = "c" * 64
        (base / h3).mkdir()
        (base / h3 / "translation_output.json").write_text(json.dumps({
            "version": 1,
            "translation_hash": h3,
            "source": "byo",
            "alias": None,
            "algorithms": [{"name": "legacy", "image_ref": "ghcr.io/y:v1"}],
            "created_at": "2026-07-01T10:00:00Z",
        }))

        rc = sim2real._cmd_list_translations(
            sim2real.build_parser().parse_args(["list", "translations"])
        )
        assert rc == 0
        out = capsys.readouterr().out
        lines = out.strip().splitlines()
        # Header + 3 rows, newest first.
        assert "ALIAS" in lines[0]
        assert "HASH" in lines[0]
        assert "SOURCE" in lines[0]
        assert "IMAGES" in lines[0]
        assert "CREATED" in lines[0]
        # h2 is newest by created_at; h1 middle; h3 oldest.
        assert "compare-a-b" in lines[1]
        assert "softreflective-v1" in lines[2]
        assert "-" in lines[3].split()[0:2]  # ALIAS column shows "-"

        assert "2 pending" in out
        assert "1 built" in out
        assert "1 registered" in out


class TestAssembleIncompleteTranslationCheck:
    """--translation with null image_ref on any algorithm → exit 2."""

    def _minimal_assemble_setup(self, tmp_path, image_ref=None):
        """Materialize the minimum inputs for _cmd_assemble to reach the check.

        Writes:
          - workspace/setup_config.json (registry/repo_name)
          - workspace/clusters/c/cluster_config.json
          - workspace/translations/<hash>/translation_output.json
          - <exp_root>/transfer.yaml
        Returns translation_hash.
        """
        ws = tmp_path / "workspace"
        (ws / "clusters" / "c").mkdir(parents=True)
        (ws / "clusters" / "c" / "cluster_config.json").write_text(json.dumps({
            "cluster_id": "c",
            "namespaces": ["sim2real-slot1"],
            "is_openshift": False,
            "storage_class": "",
            "secret_names": {"hf_token": "hf-token"},
            "workspaces": [],
        }))
        thash = "a" * 64
        tdir = ws / "translations" / thash
        (tdir / "generated" / "softref").mkdir(parents=True)
        (tdir / "translation_output.json").write_text(json.dumps({
            "version": 1, "translation_hash": thash, "source": "skill",
            "alias": "softref-alias",
            "algorithms": [{
                "name": "softref", "source_path": "algorithms/softref.py",
                "source_sha256": "0" * 64, "config_path": None,
                "image_ref": image_ref, "image_digest": None,
            }],
            "created_at": "2026-07-02T00:00:00Z",
        }))
        # A minimal-but-valid transfer.yaml.
        (tmp_path / "algorithms").mkdir()
        (tmp_path / "algorithms" / "softref.py").write_text("# stub\n")
        (tmp_path / "transfer.yaml").write_text(yaml.safe_dump({
            "kind": "sim2real-transfer", "version": 3,
            "scenario": "softref-alias",
            "baselines": [{"name": "base", "scenario": "baselines/base.yaml"}],
            "algorithms": [
                {"name": "softref", "source": "algorithms/softref.py",
                 "defaults": "base"}
            ],
            "component": {"repo": "example.com/x/y", "kind": "scorer"},
            "context": {"text": "", "files": []},
        }))
        return thash

    def test_null_image_ref_fails_early_with_actionable_error(
        self, tmp_path, capsys,
    ):
        self._minimal_assemble_setup(tmp_path, image_ref=None)
        rc = sim2real.main([
            "--experiment-root", str(tmp_path),
            "assemble", "--translation", "softref-alias",
            "--cluster", "c", "--run", "trial-1",
        ])
        assert rc == 2
        err_out = capsys.readouterr().err
        assert "not built for algorithms" in err_out
        assert "softref" in err_out
        assert "sim2real build --translation" in err_out
        # No writes to runs/ happened.
        assert not (tmp_path / "workspace" / "runs").exists()

    def test_non_null_image_ref_passes_check(self, tmp_path):
        """When image_ref is set, the check passes and assemble proceeds
        into the (mocked) assemble_run.

        Point of this test: verifying the check does NOT short-circuit
        when the ref is set. We stub the underlying assemble_run to keep
        the test focused on the check itself."""
        self._minimal_assemble_setup(
            tmp_path, image_ref="reg/repo:hash-softref"
        )
        with patch.object(
            sim2real._assemble_run_lib, "assemble_run"
        ) as mock_assemble:
            rc = sim2real.main([
                "--experiment-root", str(tmp_path),
                "assemble", "--translation", "softref-alias",
                "--cluster", "c", "--run", "trial-1",
            ])
        # If the check errored we would exit 2; passing check means we
        # reach assemble_run, which is mocked so it returns None → rc=0.
        assert rc == 0
        mock_assemble.assert_called_once()
