"""Tests for pipeline/sim2real.py — sim2real CLI top-level entry."""

from __future__ import annotations

import argparse
import json

import pytest

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

    def test_rejects_uppercase(self):
        with pytest.raises(argparse.ArgumentTypeError):
            sim2real._validate_algorithm_name("SoftReflective")

    def test_rejects_underscore(self):
        with pytest.raises(argparse.ArgumentTypeError):
            sim2real._validate_algorithm_name("soft_reflective")

    def test_rejects_empty(self):
        with pytest.raises(argparse.ArgumentTypeError):
            sim2real._validate_algorithm_name("")

    def test_rejects_whitespace(self):
        with pytest.raises(argparse.ArgumentTypeError):
            sim2real._validate_algorithm_name("soft reflective")


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
        out = sim2real._build_translation_output(
            algorithm_name="softreflective",
            image_ref="ghcr.io/foo:v1",
            translation_hash="a" * 64,
            created_at="2026-07-01T14:00:00Z",
        )
        assert out == {
            "version": 1,
            "translation_hash": "a" * 64,
            "source": "byo",
            "algorithms": [{"name": "softreflective"}],
            "image_ref": "ghcr.io/foo:v1",
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
        assert out["algorithms"] == [{"name": "softreflective"}]
        assert out["translation_hash"] == thash
        assert out["image_ref"] == "ghcr.io/foo:v1"
        assert out["version"] == 1

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
        parser = sim2real.build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args([
                "translation", "register",
                "--algorithm", "Bad_Name",
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
