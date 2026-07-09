"""Tests for pipeline/sim2real.py — sim2real CLI top-level entry."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
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


class TestParseAlgorithmTriple:
    def test_simple_triple(self):
        name, image, cfg = sim2real._parse_algorithm_triple("foo=img:v1@algo.yaml")
        assert name == "foo"
        assert image == "img:v1"
        assert cfg == "algo.yaml"

    def test_digest_ref_uses_rightmost_at(self):
        name, image, cfg = sim2real._parse_algorithm_triple(
            "foo=registry.io/img@sha256:" + "d" * 64 + "@algorithms/foo/foo_config.yaml"
        )
        assert name == "foo"
        assert image == "registry.io/img@sha256:" + "d" * 64
        assert cfg == "algorithms/foo/foo_config.yaml"

    def test_config_path_with_equals_supported(self):
        name, image, cfg = sim2real._parse_algorithm_triple("foo=img:v1@path=weird.yaml")
        assert name == "foo"
        assert image == "img:v1"
        assert cfg == "path=weird.yaml"

    def test_missing_equals_rejected(self):
        with pytest.raises(argparse.ArgumentTypeError) as ei:
            sim2real._parse_algorithm_triple("fooimg@algo.yaml")
        assert "=" in str(ei.value)

    def test_missing_at_rejected(self):
        with pytest.raises(argparse.ArgumentTypeError) as ei:
            sim2real._parse_algorithm_triple("foo=img:v1")
        assert "@" in str(ei.value)

    def test_empty_image_rejected(self):
        with pytest.raises(argparse.ArgumentTypeError):
            sim2real._parse_algorithm_triple("foo=@algo.yaml")

    def test_empty_config_rejected(self):
        with pytest.raises(argparse.ArgumentTypeError):
            sim2real._parse_algorithm_triple("foo=img:v1@")

    def test_invalid_name_rejected(self):
        with pytest.raises(argparse.ArgumentTypeError) as ei:
            sim2real._parse_algorithm_triple("bad name=img@cfg.yaml")
        # Message should mention the name-regex constraint.
        assert "name" in str(ei.value).lower()

    def test_at_in_middle_goes_to_image(self):
        """Rightmost-@ split rule: any earlier '@' stays in the image ref."""
        name, image, cfg = sim2real._parse_algorithm_triple("foo=a@b@c.yaml")
        assert name == "foo"
        assert image == "a@b"
        assert cfg == "c.yaml"

    def test_multiple_at_in_image_rejected(self):
        """Reject values whose parsed image ref has more than one '@'.

        Common cause: user tried to put a '@' in the config path.
        """
        with pytest.raises(argparse.ArgumentTypeError) as ei:
            sim2real._parse_algorithm_triple("foo=img:tag@path@with@ats/overlay.yaml")
        assert "overlay path cannot contain" in str(ei.value)


class TestComputeTranslationHash:
    """Batched hash formula (replaces step-1 single-algo formula for all N)."""

    def _e(self, name: str, image: str = "sha256:aa", config: bytes = b"c") -> dict:
        import hashlib
        return {
            "name": name,
            "image": image,
            "config_sha": hashlib.sha256(config).hexdigest(),
        }

    def test_is_deterministic_n1(self):
        h1 = sim2real._compute_translation_hash([self._e("algo")])
        h2 = sim2real._compute_translation_hash([self._e("algo")])
        assert h1 == h2
        assert len(h1) == 64

    def test_is_deterministic_n2(self):
        h1 = sim2real._compute_translation_hash([self._e("a"), self._e("b")])
        h2 = sim2real._compute_translation_hash([self._e("a"), self._e("b")])
        assert h1 == h2
        assert len(h1) == 64

    def test_order_invariant(self):
        h_ab = sim2real._compute_translation_hash([self._e("a"), self._e("b")])
        h_ba = sim2real._compute_translation_hash([self._e("b"), self._e("a")])
        assert h_ab == h_ba

    def test_changes_with_algorithm_name(self):
        h1 = sim2real._compute_translation_hash([self._e("a")])
        h2 = sim2real._compute_translation_hash([self._e("b")])
        assert h1 != h2

    def test_changes_with_config(self):
        h1 = sim2real._compute_translation_hash([self._e("a", config=b"x")])
        h2 = sim2real._compute_translation_hash([self._e("a", config=b"y")])
        assert h1 != h2

    def test_changes_with_image_ref(self):
        h1 = sim2real._compute_translation_hash([self._e("a", image="sha256:aa")])
        h2 = sim2real._compute_translation_hash([self._e("a", image="sha256:bb")])
        assert h1 != h2

    def test_offline_ref_produces_stable_hash(self):
        e = self._e("a", image="ghcr.io/foo:v1")
        h1 = sim2real._compute_translation_hash([e])
        h2 = sim2real._compute_translation_hash([e])
        assert h1 == h2

    def test_adding_algo_changes_hash(self):
        h1 = sim2real._compute_translation_hash([self._e("a")])
        h2 = sim2real._compute_translation_hash([self._e("a"), self._e("b")])
        assert h1 != h2

    def test_n1_new_differs_from_n1_old_shape(self):
        """The new formula wraps entries in a list, so N=1 hashes differ
        from the step-1 formula (which framed a single dict). Documented
        break in the design (no long-lived step-1 registrations exist)."""
        import hashlib
        import json as _json
        new_hash = sim2real._compute_translation_hash([self._e("a")])
        # Old formula: sha256(canonical-json({algorithm_name, config_sha256, image_digest_or_ref}))
        old_canonical = _json.dumps(
            {
                "algorithm_name": "a",
                "config_sha256": hashlib.sha256(b"c").hexdigest(),
                "image_digest_or_ref": "sha256:aa",
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        old_hash = hashlib.sha256(old_canonical.encode("utf-8")).hexdigest()
        assert new_hash != old_hash


class TestBuildSchemas:
    def test_translation_output_schema(self):
        out = sim2real._build_translation_output(
            algorithms=[
                {
                    "name": "algo1",
                    "image_ref": "sha256:aa",
                    "image_digest": "sha256:aa",
                    "config_path": "generated/algo1/algo1_config.yaml",
                },
            ],
            translation_hash="h" * 64,
            source="byo",
            alias="algo1",
            created_at="2026-07-05T10:00:00Z",
        )
        assert out["version"] == 1
        assert out["translation_hash"] == "h" * 64
        assert out["source"] == "byo"
        assert out["alias"] == "algo1"
        assert out["created_at"] == "2026-07-05T10:00:00Z"
        assert len(out["algorithms"]) == 1
        entry = out["algorithms"][0]
        assert entry["name"] == "algo1"
        assert entry["source_path"] is None
        assert entry["source_sha256"] is None
        assert entry["config_path"] == "generated/algo1/algo1_config.yaml"
        assert entry["image_ref"] == "sha256:aa"
        assert entry["image_digest"] == "sha256:aa"

    def test_translation_output_batched_n2(self):
        out = sim2real._build_translation_output(
            algorithms=[
                {"name": "a", "image_ref": "img1", "image_digest": None, "config_path": "generated/a/a_config.yaml"},
                {"name": "b", "image_ref": "img2", "image_digest": None, "config_path": "generated/b/b_config.yaml"},
            ],
            translation_hash="h" * 64,
            source="byo",
            alias=None,
            created_at="2026-07-05T10:00:00Z",
        )
        assert out["alias"] is None
        assert [e["name"] for e in out["algorithms"]] == ["a", "b"]

    def test_registered_schema_with_digest(self):
        reg = sim2real._build_registered(
            [
                {"name": "algo1", "image_ref": "reg/img@sha256:" + "a" * 64, "image_digest": "sha256:" + "a" * 64},
            ],
            "2026-07-05T10:00:00Z",
        )
        assert reg["version"] == 1
        assert reg["source"] == "byo"
        assert reg["registered_at"] == "2026-07-05T10:00:00Z"
        assert reg["algorithms"] == [
            {"name": "algo1", "image_ref": "reg/img@sha256:" + "a" * 64, "image_digest": "sha256:" + "a" * 64},
        ]

    def test_registered_schema_offline(self):
        reg = sim2real._build_registered(
            [
                {"name": "a", "image_ref": "ghcr.io/foo:v1", "image_digest": None},
            ],
            "2026-07-05T10:00:00Z",
        )
        assert reg["algorithms"][0]["image_digest"] is None


class TestRegisterTranslation:
    def _algo(self, tmp_path, name: str, image: str = None, config: str = None) -> dict:
        """Build an AlgorithmSpec, writing the config file on the way in."""
        cfg = tmp_path / f"{name}_config.yaml"
        cfg.write_text(config if config else f"scorers:\n  - name: {name}\n")
        return {
            "name": name,
            "image_ref": image or "ghcr.io/foo/bar:v1",
            "config_path": cfg,
        }

    def test_creates_translation_dir_and_files_n1(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        algo = self._algo(tmp_path, "algo1")
        now = "2026-07-05T10:00:00Z"
        thash, status = sim2real._register_translation(
            algorithms=[algo],
            baseline_config_path=None,
            registered_hash=None,
            now_iso=now,
        )
        assert status == "created"
        tdir = Path("workspace") / "translations" / thash
        assert (tdir / "translation_output.json").exists()
        assert (tdir / "registered.json").exists()
        assert (tdir / "generated" / "algo1" / "algo1_config.yaml").exists()

    def test_creates_translation_dir_and_files_n2(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        algos = [self._algo(tmp_path, "foo"), self._algo(tmp_path, "bar")]
        thash, status = sim2real._register_translation(
            algorithms=algos,
            baseline_config_path=None,
            registered_hash=None,
            now_iso="2026-07-05T10:00:00Z",
        )
        assert status == "created"
        tdir = Path("workspace") / "translations" / thash
        assert (tdir / "generated" / "foo" / "foo_config.yaml").exists()
        assert (tdir / "generated" / "bar" / "bar_config.yaml").exists()
        tout = json.loads((tdir / "translation_output.json").read_text())
        names = sorted(a["name"] for a in tout["algorithms"])
        assert names == ["bar", "foo"]

    def test_alias_field_null_for_batched(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        algos = [self._algo(tmp_path, "foo"), self._algo(tmp_path, "bar")]
        thash, _ = sim2real._register_translation(
            algorithms=algos,
            baseline_config_path=None,
            registered_hash=None,
            now_iso="2026-07-05T10:00:00Z",
        )
        tout = json.loads((Path("workspace") / "translations" / thash / "translation_output.json").read_text())
        assert tout["alias"] is None

    def test_alias_field_set_for_n1(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        algo = self._algo(tmp_path, "solo")
        thash, _ = sim2real._register_translation(
            algorithms=[algo],
            baseline_config_path=None,
            registered_hash=None,
            now_iso="2026-07-05T10:00:00Z",
        )
        tout = json.loads((Path("workspace") / "translations" / thash / "translation_output.json").read_text())
        assert tout["alias"] == "solo"

    def test_translation_output_algorithms_shape(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        algos = [self._algo(tmp_path, "foo", image="reg/img@sha256:" + "a" * 64)]
        thash, _ = sim2real._register_translation(
            algorithms=algos,
            baseline_config_path=None,
            registered_hash=None,
            now_iso="2026-07-05T10:00:00Z",
        )
        tout = json.loads((Path("workspace") / "translations" / thash / "translation_output.json").read_text())
        entry = tout["algorithms"][0]
        assert entry["name"] == "foo"
        assert entry["image_ref"] == "reg/img@sha256:" + "a" * 64
        assert entry["image_digest"] == "sha256:" + "a" * 64
        assert entry["config_path"] == "generated/foo/foo_config.yaml"
        assert entry["source_path"] is None
        assert entry["source_sha256"] is None

    def test_registered_records_all_algorithms(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        algos = [
            self._algo(tmp_path, "foo", image="reg/img@sha256:" + "a" * 64),
            self._algo(tmp_path, "bar", image="reg/img2:v1"),
        ]
        thash, _ = sim2real._register_translation(
            algorithms=algos,
            baseline_config_path=None,
            registered_hash=None,
            now_iso="2026-07-05T10:00:00Z",
        )
        reg = json.loads((Path("workspace") / "translations" / thash / "registered.json").read_text())
        assert reg["version"] == 1
        assert reg["source"] == "byo"
        # Batched registered.json carries N entries under 'algorithms'.
        entries = {e["name"]: e for e in reg["algorithms"]}
        assert entries["foo"]["image_digest"] == "sha256:" + "a" * 64
        assert entries["bar"]["image_digest"] is None

    def test_writes_all_treatment_overlays_verbatim(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        algos = [
            self._algo(tmp_path, "foo", config="foo_body: 1\n"),
            self._algo(tmp_path, "bar", config="bar_body: 2\n"),
        ]
        thash, _ = sim2real._register_translation(
            algorithms=algos,
            baseline_config_path=None,
            registered_hash=None,
            now_iso="2026-07-05T10:00:00Z",
        )
        tdir = Path("workspace") / "translations" / thash
        assert (tdir / "generated" / "foo" / "foo_config.yaml").read_text() == "foo_body: 1\n"
        assert (tdir / "generated" / "bar" / "bar_config.yaml").read_text() == "bar_body: 2\n"

    def test_baseline_config_written_once(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        baseline = tmp_path / "base.yaml"
        baseline.write_text("baseline: yes\n")
        algos = [self._algo(tmp_path, "foo"), self._algo(tmp_path, "bar")]
        thash, _ = sim2real._register_translation(
            algorithms=algos,
            baseline_config_path=baseline,
            registered_hash=None,
            now_iso="2026-07-05T10:00:00Z",
        )
        tdir = Path("workspace") / "translations" / thash
        assert (tdir / "generated" / "baseline_config.yaml").read_text() == "baseline: yes\n"

    def test_idempotent_second_call_same_inputs(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        algos = [self._algo(tmp_path, "foo"), self._algo(tmp_path, "bar")]
        h1, s1 = sim2real._register_translation(
            algorithms=algos,
            baseline_config_path=None,
            registered_hash=None,
            now_iso="2026-07-05T10:00:00Z",
        )
        # Second call: same algorithms in same order.
        h2, s2 = sim2real._register_translation(
            algorithms=algos,
            baseline_config_path=None,
            registered_hash=None,
            now_iso="2026-07-05T11:00:00Z",
        )
        assert h1 == h2
        assert s1 == "created"
        assert s2 == "idempotent"

    def test_idempotent_different_order_same_hash(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        algos1 = [self._algo(tmp_path, "foo"), self._algo(tmp_path, "bar")]
        algos2 = [algos1[1], algos1[0]]  # reversed
        h1, _ = sim2real._register_translation(
            algorithms=algos1,
            baseline_config_path=None,
            registered_hash=None,
            now_iso="2026-07-05T10:00:00Z",
        )
        h2, s2 = sim2real._register_translation(
            algorithms=algos2,
            baseline_config_path=None,
            registered_hash=None,
            now_iso="2026-07-05T11:00:00Z",
        )
        assert h1 == h2
        assert s2 == "idempotent"

    def test_registered_hash_mismatch_errors(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        algo = self._algo(tmp_path, "foo")
        with pytest.raises(RuntimeError, match="--registered-hash mismatch"):
            sim2real._register_translation(
                algorithms=[algo],
                baseline_config_path=None,
                registered_hash="deadbeef",
                now_iso="2026-07-05T10:00:00Z",
            )

    def test_registered_hash_match_succeeds(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        algo = self._algo(tmp_path, "foo")
        # Compute expected hash by running once and reading it back.
        expected, _ = sim2real._register_translation(
            algorithms=[algo],
            baseline_config_path=None,
            registered_hash=None,
            now_iso="2026-07-05T10:00:00Z",
        )
        # Second call with matching hash succeeds (idempotent).
        thash, status = sim2real._register_translation(
            algorithms=[algo],
            baseline_config_path=None,
            registered_hash=expected,
            now_iso="2026-07-05T11:00:00Z",
        )
        assert thash == expected
        assert status == "idempotent"

    def test_partial_write_missing_registered_json_raises(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        algo = self._algo(tmp_path, "foo")
        thash, _ = sim2real._register_translation(
            algorithms=[algo],
            baseline_config_path=None,
            registered_hash=None,
            now_iso="2026-07-05T10:00:00Z",
        )
        (Path("workspace") / "translations" / thash / "registered.json").unlink()
        with pytest.raises(RuntimeError, match="incomplete"):
            sim2real._register_translation(
                algorithms=[algo],
                baseline_config_path=None,
                registered_hash=None,
                now_iso="2026-07-05T11:00:00Z",
            )

    def test_partial_write_missing_generated_config_raises(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        algo = self._algo(tmp_path, "foo")
        thash, _ = sim2real._register_translation(
            algorithms=[algo],
            baseline_config_path=None,
            registered_hash=None,
            now_iso="2026-07-05T10:00:00Z",
        )
        (Path("workspace") / "translations" / thash / "generated" / "foo" / "foo_config.yaml").unlink()
        with pytest.raises(RuntimeError, match="incomplete"):
            sim2real._register_translation(
                algorithms=[algo],
                baseline_config_path=None,
                registered_hash=None,
                now_iso="2026-07-05T11:00:00Z",
            )

    def test_partial_write_missing_one_of_N_algos_raises(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        algos = [self._algo(tmp_path, "foo"), self._algo(tmp_path, "bar")]
        thash, _ = sim2real._register_translation(
            algorithms=algos,
            baseline_config_path=None,
            registered_hash=None,
            now_iso="2026-07-05T10:00:00Z",
        )
        # Delete one of the per-algo directories so its overlay is missing.
        (Path("workspace") / "translations" / thash / "generated" / "bar" / "bar_config.yaml").unlink()
        with pytest.raises(RuntimeError, match="incomplete"):
            sim2real._register_translation(
                algorithms=algos,
                baseline_config_path=None,
                registered_hash=None,
                now_iso="2026-07-05T11:00:00Z",
            )


class TestBuildParser:
    def test_parses_new_form_single_algo(self):
        p = sim2real.build_parser()
        args = p.parse_args([
            "translation", "register",
            "--algorithm", "foo=img:v1@overlay.yaml",
        ])
        assert args.command == "translation"
        assert args.subcommand == "register"
        assert args.algorithm == ["foo=img:v1@overlay.yaml"]
        assert args.image is None
        assert args.config is None

    def test_parses_new_form_multi_algo(self):
        p = sim2real.build_parser()
        args = p.parse_args([
            "translation", "register",
            "--algorithm", "foo=img1@o1.yaml",
            "--algorithm", "bar=img2@o2.yaml",
        ])
        assert args.algorithm == ["foo=img1@o1.yaml", "bar=img2@o2.yaml"]

    def test_parses_deprecated_form(self):
        p = sim2real.build_parser()
        args = p.parse_args([
            "translation", "register",
            "--algorithm", "foo",
            "--image", "img:v1",
            "--config", "overlay.yaml",
        ])
        assert args.algorithm == ["foo"]
        assert args.image == "img:v1"
        assert args.config == "overlay.yaml"

    def test_accepts_baseline_and_registered_hash(self):
        p = sim2real.build_parser()
        args = p.parse_args([
            "translation", "register",
            "--algorithm", "foo=img:v1@overlay.yaml",
            "--baseline-config", "b.yaml",
            "--registered-hash", "abc",
            "--force",
        ])
        assert args.baseline_config == "b.yaml"
        assert args.registered_hash == "abc"
        assert args.force is True


class TestMainEndToEnd:
    def _cfg(self, tmp_path, name: str) -> Path:
        p = tmp_path / f"{name}.yaml"
        p.write_text(f"scorers:\n  - name: {name}\n")
        return p

    def test_happy_path_n1_new_form(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = self._cfg(tmp_path, "foo")
        rc = sim2real.main([
            "translation", "register",
            "--algorithm", f"foo=ghcr.io/img:v1@{cfg}",
        ])
        assert rc == 0
        captured = capsys.readouterr()
        assert "registered translation" in captured.out
        # No deprecation warning under the new form.
        assert "deprecated" not in captured.err.lower()

    def test_happy_path_n1_deprecated_form(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = self._cfg(tmp_path, "foo")
        rc = sim2real.main([
            "translation", "register",
            "--algorithm", "foo",
            "--image", "ghcr.io/img:v1",
            "--config", str(cfg),
        ])
        assert rc == 0
        captured = capsys.readouterr()
        assert "registered translation" in captured.out
        assert "deprecated" in captured.err.lower()

    def test_happy_path_n2(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        c1 = self._cfg(tmp_path, "foo")
        c2 = self._cfg(tmp_path, "bar")
        rc = sim2real.main([
            "translation", "register",
            "--algorithm", f"foo=ghcr.io/img1:v1@{c1}",
            "--algorithm", f"bar=ghcr.io/img2:v1@{c2}",
        ])
        assert rc == 0
        # Extract the hash from stdout to inspect on-disk shape.
        out = capsys.readouterr().out.strip()
        thash = out.rsplit(" ", 1)[-1]
        tdir = Path("workspace") / "translations" / thash
        assert (tdir / "generated" / "foo" / "foo_config.yaml").exists()
        assert (tdir / "generated" / "bar" / "bar_config.yaml").exists()
        tout = json.loads((tdir / "translation_output.json").read_text())
        assert sorted(a["name"] for a in tout["algorithms"]) == ["bar", "foo"]
        assert tout["alias"] is None

    def test_order_invariant_hash(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        c1 = self._cfg(tmp_path, "foo")
        c2 = self._cfg(tmp_path, "bar")
        rc1 = sim2real.main([
            "translation", "register",
            "--algorithm", f"foo=img1:v1@{c1}",
            "--algorithm", f"bar=img2:v1@{c2}",
        ])
        assert rc1 == 0
        # Second run — reversed order. Should hit idempotent path.
        rc2 = sim2real.main([
            "translation", "register",
            "--algorithm", f"bar=img2:v1@{c2}",
            "--algorithm", f"foo=img1:v1@{c1}",
        ])
        assert rc2 == 0
        # Only one translation dir exists.
        dirs = list((Path("workspace") / "translations").iterdir())
        assert len(dirs) == 1

    def test_rightmost_at_in_image_ref(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = tmp_path / "algorithms" / "foo" / "foo_config.yaml"
        cfg.parent.mkdir(parents=True)
        cfg.write_text("scorers: []\n")
        image = "registry.io/img@sha256:" + "d" * 64
        rc = sim2real.main([
            "translation", "register",
            "--algorithm", f"foo={image}@{cfg}",
        ])
        assert rc == 0
        out = capsys.readouterr().out.strip()
        thash = out.rsplit(" ", 1)[-1]
        reg = json.loads((Path("workspace") / "translations" / thash / "registered.json").read_text())
        entry = reg["algorithms"][0]
        assert entry["image_ref"] == image
        assert entry["image_digest"] == "sha256:" + "d" * 64

    def test_at_in_config_path_rejected(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        rc = sim2real.main([
            "translation", "register",
            "--algorithm", "foo=img:tag@path@with@ats/overlay.yaml",
        ])
        assert rc == 2
        err = capsys.readouterr().err
        assert "overlay path cannot contain" in err

    def test_config_path_with_equals_supported(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        cfg = tmp_path / "weird=name.yaml"
        cfg.write_text("scorers: []\n")
        rc = sim2real.main([
            "translation", "register",
            "--algorithm", f"foo=img:v1@{cfg}",
        ])
        assert rc == 0

    def test_duplicate_algorithm_names_rejected(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        c1 = self._cfg(tmp_path, "foo")
        c2 = self._cfg(tmp_path, "foo2")
        rc = sim2real.main([
            "translation", "register",
            "--algorithm", f"foo=img1@{c1}",
            "--algorithm", f"foo=img2@{c2}",
        ])
        assert rc == 2
        assert "duplicate algorithm name" in capsys.readouterr().err

    def test_missing_config_errors(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        rc = sim2real.main([
            "translation", "register",
            "--algorithm", "foo=img:v1@does-not-exist.yaml",
        ])
        assert rc == 2
        assert "not found" in capsys.readouterr().err

    def test_malformed_config_errors_no_writes(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = tmp_path / "bad.yaml"
        cfg.write_text("::not: yaml: [\n")
        rc = sim2real.main([
            "translation", "register",
            "--algorithm", f"foo=img:v1@{cfg}",
        ])
        assert rc == 2
        assert "not valid YAML" in capsys.readouterr().err
        assert not (tmp_path / "workspace" / "translations").exists()

    def test_registered_hash_mismatch_exits_2(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = self._cfg(tmp_path, "foo")
        rc = sim2real.main([
            "translation", "register",
            "--algorithm", f"foo=img:v1@{cfg}",
            "--registered-hash", "deadbeef",
        ])
        assert rc == 2
        assert "--registered-hash mismatch" in capsys.readouterr().err

    def test_idempotent_second_run_prints_warning(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = self._cfg(tmp_path, "foo")
        sim2real.main([
            "translation", "register",
            "--algorithm", f"foo=img:v1@{cfg}",
        ])
        capsys.readouterr()  # reset
        rc = sim2real.main([
            "translation", "register",
            "--algorithm", f"foo=img:v1@{cfg}",
        ])
        assert rc == 0
        err = capsys.readouterr().err
        assert "already registered" in err

    def test_digest_ref_no_null_warning(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = self._cfg(tmp_path, "foo")
        rc = sim2real.main([
            "translation", "register",
            "--algorithm", f"foo=reg/img@sha256:{'a'*64}@{cfg}",
        ])
        assert rc == 0
        err = capsys.readouterr().err
        assert "null" not in err

    def test_deprecated_form_with_multiple_algorithms_rejected(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = self._cfg(tmp_path, "foo")
        rc = sim2real.main([
            "translation", "register",
            "--algorithm", "foo",
            "--algorithm", "bar",
            "--image", "img:v1",
            "--config", str(cfg),
        ])
        assert rc == 2
        err = capsys.readouterr().err
        assert "deprecated form" in err or "single --algorithm" in err

    def test_mixed_form_new_algo_plus_image_rejected(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = self._cfg(tmp_path, "foo")
        rc = sim2real.main([
            "translation", "register",
            "--algorithm", f"foo=img:v1@{cfg}",
            "--image", "other:v1",
        ])
        assert rc == 2

    def test_oserror_from_register_returns_2_not_traceback(
        self, tmp_path, capsys, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        cfg = self._cfg(tmp_path, "foo")
        # Simulate an OSError from _register_translation by making the workspace
        # translations dir a file (blocks mkdir).
        ws = tmp_path / "workspace" / "translations"
        ws.parent.mkdir()
        ws.write_text("")  # occupy the path with a regular file
        rc = sim2real.main([
            "translation", "register",
            "--algorithm", f"foo=img:v1@{cfg}",
        ])
        assert rc == 2
        # Not a Python traceback.
        assert "Traceback" not in capsys.readouterr().err


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
            algorithms=[{"name": "sr", "image_ref": "ghcr.io/foo/bar:v1", "config_path": cfg}],
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
        assert (run_dir / "cluster" / "pipelinerun-w1|baseline|i1.yaml").exists()
        assert (run_dir / "cluster" / "pipelinerun-w1|sr|i1.yaml").exists()
        # Issue #555: write path prints the past-tense ack.
        out = capsys.readouterr().out
        assert "assembled run trial-1" in out

    def test_noop_reassemble_prints_no_change_message(self, tmp_path, capsys):
        """Issue #555: second assemble with identical inputs prints the
        'No change needed' message and does not print 'assembled run …'."""
        thash = self._make_minimal_registration(tmp_path)
        cluster_id = self._bootstrap_experiment(tmp_path)
        # First assemble — write path.
        rc1 = sim2real.main(
            [
                "--experiment-root", str(tmp_path),
                "assemble",
                "--translation", thash,
                "--cluster", cluster_id,
                "--run", "trial-1",
            ]
        )
        assert rc1 == 0
        # Capture prior assembled_at so we can assert the message includes it.
        run_dir = tmp_path / "workspace" / "runs" / "trial-1"
        meta = json.loads((run_dir / "run_metadata.json").read_text())
        prior_assembled_at = meta["assembled_at"]
        capsys.readouterr()  # drain
        # Second assemble — same inputs — no-op path.
        rc2 = sim2real.main(
            [
                "--experiment-root", str(tmp_path),
                "assemble",
                "--translation", thash,
                "--cluster", cluster_id,
                "--run", "trial-1",
            ]
        )
        assert rc2 == 0
        out = capsys.readouterr().out
        assert "No change needed for run 'trial-1'" in out
        assert prior_assembled_at in out
        assert "--force" in out
        assert "assembled run trial-1" not in out

    def test_force_rebuild_prints_assembled_not_no_change(self, tmp_path, capsys):
        """Issue #555: --force after an initial assemble still prints the
        past-tense ack, not the no-op message."""
        thash = self._make_minimal_registration(tmp_path)
        cluster_id = self._bootstrap_experiment(tmp_path)
        rc1 = sim2real.main(
            [
                "--experiment-root", str(tmp_path),
                "assemble",
                "--translation", thash,
                "--cluster", cluster_id,
                "--run", "trial-1",
            ]
        )
        assert rc1 == 0
        capsys.readouterr()  # drain
        rc2 = sim2real.main(
            [
                "--experiment-root", str(tmp_path),
                "assemble",
                "--translation", thash,
                "--cluster", cluster_id,
                "--run", "trial-1",
                "--force",
            ]
        )
        assert rc2 == 0
        out = capsys.readouterr().out
        assert "assembled run trial-1" in out
        assert "No change needed" not in out

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
    """Alias collision is checked per-algorithm regardless of batch size."""

    def _reg(self, tmp_path, name: str, image: str = "img:v1", config: str = None):
        cfg = tmp_path / f"{name}_cfg.yaml"
        cfg.write_text(config if config else f"scorers:\n  - name: {name}\n")
        return sim2real._register_translation(
            algorithms=[{"name": name, "image_ref": image, "config_path": cfg}],
            baseline_config_path=None,
            registered_hash=None,
            now_iso="2026-07-05T10:00:00Z",
        )

    def test_same_alias_same_content_is_idempotent(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        h1, _ = self._reg(tmp_path, "foo")
        h2, s2 = self._reg(tmp_path, "foo")
        assert h1 == h2
        assert s2 == "idempotent"

    def test_alias_collision_without_force_raises(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._reg(tmp_path, "foo", image="img:v1")
        with pytest.raises(RuntimeError, match="already assigned"):
            self._reg(tmp_path, "foo", image="img:v2")

    def test_force_reassigns_alias_and_clears_previous(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        h_old, _ = self._reg(tmp_path, "foo", image="img:v1")
        # Re-register 'foo' with different content and --force.
        cfg = tmp_path / "foo_v2.yaml"
        cfg.write_text("scorers:\n  - name: foo_v2\n")
        h_new, _ = sim2real._register_translation(
            algorithms=[{"name": "foo", "image_ref": "img:v2", "config_path": cfg}],
            baseline_config_path=None,
            registered_hash=None,
            now_iso="2026-07-05T11:00:00Z",
            force=True,
        )
        assert h_new != h_old
        # Previous translation's alias cleared.
        old_tout = json.loads(
            (Path("workspace") / "translations" / h_old / "translation_output.json").read_text()
        )
        assert old_tout["alias"] is None
        # New translation owns the alias.
        new_tout = json.loads(
            (Path("workspace") / "translations" / h_new / "translation_output.json").read_text()
        )
        assert new_tout["alias"] == "foo"

    def test_batched_alias_collision_on_one_of_N_algos(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        # Pre-existing single-algo translation with alias 'foo'.
        self._reg(tmp_path, "foo", image="img:v1")
        # Now try to register a BATCHED translation whose members include 'foo'.
        c_foo = tmp_path / "foo_batched.yaml"
        c_foo.write_text("scorers:\n  - name: foo_b\n")
        c_bar = tmp_path / "bar.yaml"
        c_bar.write_text("scorers:\n  - name: bar\n")
        with pytest.raises(RuntimeError, match="already assigned"):
            sim2real._register_translation(
                algorithms=[
                    {"name": "foo", "image_ref": "img:v3", "config_path": c_foo},
                    {"name": "bar", "image_ref": "img:v4", "config_path": c_bar},
                ],
                baseline_config_path=None,
                registered_hash=None,
                now_iso="2026-07-05T11:00:00Z",
            )


class TestAssembleResolvesAlias:
    def test_assemble_accepts_alias(self, tmp_path, monkeypatch):
        # This is a smoke test — we mock assemble_run to just capture
        # the resolved hash. Full assemble behavior is exercised in
        # test_assemble_run.py.
        cfg = tmp_path / "algo.yaml"
        cfg.write_text("scenario: []\n")
        thash, _ = sim2real._register_translation(
            algorithms=[{"name": "my-algo", "image_ref": "ghcr.io/x:v1", "config_path": cfg}],
            baseline_config_path=None,
            registered_hash=None,
            now_iso="2026-07-02T14:00:00Z",
        )

        captured = {}
        def fake_assemble(*, translation_hash, translation_ref, cluster_id,
                          run_name, experiment_root, manifest_path,
                          force, replicas, now_iso):
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


class TestPositiveInt:
    def test_accepts_positive_integer(self):
        from pipeline.sim2real import _positive_int
        assert _positive_int("1") == 1
        assert _positive_int("42") == 42

    def test_rejects_zero(self):
        from pipeline.sim2real import _positive_int
        with pytest.raises(argparse.ArgumentTypeError, match=">= 1"):
            _positive_int("0")

    def test_rejects_negative(self):
        from pipeline.sim2real import _positive_int
        with pytest.raises(argparse.ArgumentTypeError, match=">= 1"):
            _positive_int("-1")

    def test_rejects_non_integer(self):
        from pipeline.sim2real import _positive_int
        with pytest.raises(argparse.ArgumentTypeError, match="positive integer"):
            _positive_int("abc")


class TestAssembleReplicasArg:
    def test_replicas_defaults_to_one(self):
        from pipeline.sim2real import _build_parser
        parser = _build_parser()
        args = parser.parse_args([
            "assemble", "--translation", "h", "--cluster", "c", "--run", "r",
        ])
        assert args.replicas == 1

    def test_replicas_accepts_positive_int(self):
        from pipeline.sim2real import _build_parser
        parser = _build_parser()
        args = parser.parse_args([
            "assemble", "--translation", "h", "--cluster", "c", "--run", "r",
            "--replicas", "3",
        ])
        assert args.replicas == 3

    def test_replicas_rejects_zero(self):
        from pipeline.sim2real import _build_parser
        parser = _build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args([
                "assemble", "--translation", "h", "--cluster", "c", "--run", "r",
                "--replicas", "0",
            ])


class TestCmdBuildOverlayLifecycle:
    """Overlay lifecycle tests for sim2real._cmd_build (issue #530).

    _cmd_build must apply each algorithm's source overlay to source_dir
    BEFORE dispatching buildkit, and restore the baseline AFTER — both on
    success and on failure. Regression to any earlier state (upload the
    same source for every algo → same binary compiled → all images
    contain the same plugin) produces silent A/A results.
    """

    def _make_fixture(self, tmp_path, monkeypatch):
        """Build a minimal working translation with two algos.

        Layout:
          exp_root/workspace/setup_config.json
          exp_root/workspace/clusters/<cid>/cluster_config.json
          exp_root/workspace/translations/<hash>/translation_output.json
          exp_root/workspace/translations/<hash>/generated/<algo>/<algo>_output.json
          exp_root/workspace/translations/<hash>/generated/<algo>/<overlay files>
          exp_root/myrepo/  (git repo, source_dir)
        """
        import subprocess
        exp_root = tmp_path / "exp"
        exp_root.mkdir()

        # Component git repo (source_dir).
        src = exp_root / "myrepo"
        src.mkdir()
        subprocess.run(["git", "init", str(src)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(src), "config", "user.email", "t@t"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(src), "config", "user.name", "T"],
                       check=True, capture_output=True)
        (src / "cmd").mkdir()
        (src / "cmd" / "runner.go").write_text("package main\n// baseline\nfunc main() {}\n")
        subprocess.run(["git", "-C", str(src), "add", "."],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(src), "commit", "-m", "init"],
                       check=True, capture_output=True)

        # workspace/setup_config.json
        ws = exp_root / "workspace"
        ws.mkdir()
        (ws / "setup_config.json").write_text(json.dumps({
            "registry": "ghcr.io/org",
            "repo_name": "myrepo",
        }))

        # cluster config
        cid = "test-cluster"
        cluster_dir = ws / "clusters" / cid
        cluster_dir.mkdir(parents=True)
        (cluster_dir / "cluster_config.json").write_text(json.dumps({
            "namespaces": ["test-ns"],
            "secret_names": {"registry_creds": "creds"},
        }))

        # translation dir with two algos
        thash = "a" * 64
        tdir = ws / "translations" / thash
        gen = tdir / "generated"
        for algo in ["algo1", "algo2"]:
            algo_gen = gen / algo
            (algo_gen / "cmd").mkdir(parents=True)
            (algo_gen / "cmd" / "runner.go").write_text(
                f"package main\n// overlay:{algo}\nfunc main() {{}}\n"
            )
            (algo_gen / "pkg" / algo).mkdir(parents=True)
            (algo_gen / "pkg" / algo / "policy.go").write_text(f"package {algo}\n")
            (algo_gen / f"{algo}_output.json").write_text(json.dumps({
                "plugin_type": f"{algo}-type",
                "package": algo,
                "files_created": [f"pkg/{algo}/policy.go"],
                "files_modified": ["cmd/runner.go"],
            }))

        (tdir / "translation_output.json").write_text(json.dumps({
            "version": 1,
            "translation_hash": thash,
            "source": "skill",
            "alias": None,
            "algorithms": [
                {"name": "algo1", "image_ref": None, "image_digest": None},
                {"name": "algo2", "image_ref": None, "image_digest": None},
            ],
            "created_at": "2026-07-08T00:00:00Z",
        }))

        monkeypatch.chdir(exp_root)
        # Stub cluster-side and buildkit-precondition primitives.
        monkeypatch.setattr("pipeline.lib.build.check_skopeo", lambda: None)
        return exp_root, src, thash

    def test_overlay_applied_per_algo_before_dispatch(self, tmp_path, monkeypatch):
        """Each algo's overlay must be on disk when dispatch_buildkit_build runs.

        Regression guard for #530: previously the same source tree was
        uploaded for every algo → identical binaries → A/A images.
        """
        exp_root, src, thash = self._make_fixture(tmp_path, monkeypatch)

        captured = []

        def fake_dispatch(*, image_ref, source_dir, **_kw):
            state = {}
            for f in Path(source_dir).rglob("*"):
                if f.is_file() and ".git" not in f.parts:
                    state[str(f.relative_to(source_dir))] = f.read_text()
            captured.append({"image_ref": image_ref, "state": state})
            return 0

        monkeypatch.setattr("pipeline.lib.build.probe_image_digest", lambda *a, **k: "sha256:x")
        monkeypatch.setattr("pipeline.lib.build.dispatch_buildkit_build", fake_dispatch)

        rc = sim2real.main(["build", "--translation", thash, "--force-rebuild"])
        assert rc == 0
        assert len(captured) == 2

        # First dispatch has algo1 overlay only.
        s1 = captured[0]["state"]
        assert "algo1" in captured[0]["image_ref"]
        assert "pkg/algo1/policy.go" in s1
        assert "overlay:algo1" in s1["cmd/runner.go"]
        assert "pkg/algo2/policy.go" not in s1

        # Second dispatch has algo2 overlay only (algo1 cleaned up in between).
        s2 = captured[1]["state"]
        assert "algo2" in captured[1]["image_ref"]
        assert "pkg/algo2/policy.go" in s2
        assert "overlay:algo2" in s2["cmd/runner.go"]
        assert "pkg/algo1/policy.go" not in s2

    def test_post_build_cleanup_after_buildkit_failure(self, tmp_path, monkeypatch, capsys):
        """buildkit non-zero rc must still trigger finally-block restore.

        Reviewer's suggested test (1): finally block runs even when
        buildkit returns non-zero.
        """
        exp_root, src, thash = self._make_fixture(tmp_path, monkeypatch)

        monkeypatch.setattr("pipeline.lib.build.probe_image_digest", lambda *a, **k: None)
        monkeypatch.setattr(
            "pipeline.lib.build.dispatch_buildkit_build", lambda **_kw: 1
        )

        rc = sim2real.main(["build", "--translation", thash, "--force-rebuild"])
        assert rc == 2  # any_failure -> 2

        # Post-build restore ran for algo1: overlay files gone, runner.go baseline.
        assert not (src / "pkg" / "algo1" / "policy.go").exists()
        assert "// baseline" in (src / "cmd" / "runner.go").read_text()
        assert "overlay:algo1" not in (src / "cmd" / "runner.go").read_text()

    def test_finally_restore_baseline_failure_fails_loud(
        self, tmp_path, monkeypatch, capsys
    ):
        """A failure in the finally-block restore_baseline must set
        any_failure and break, so the run exits 2 and does NOT proceed
        to the next algorithm on an unknown tree state.

        Reviewer's suggested test: inject a CalledProcessError from
        the finally-block restore_baseline; assert error emitted,
        subsequent iteration is NOT executed, and return code is 2.
        """
        import subprocess as _sub
        exp_root, src, thash = self._make_fixture(tmp_path, monkeypatch)

        dispatched = []
        monkeypatch.setattr("pipeline.lib.build.probe_image_digest", lambda *a, **k: "sha256:x")

        def fake_dispatch(*, image_ref, **_kw):
            dispatched.append(image_ref)
            return 0

        monkeypatch.setattr("pipeline.lib.build.dispatch_buildkit_build", fake_dispatch)

        # Patch restore_baseline to raise the second time it's called
        # (the first is pre-build for algo1, second is post-build finally
        # for algo1).
        import pipeline.lib.source_toggle as st
        real_restore = st.restore_baseline
        call_count = {"n": 0}

        def flaky_restore(component_dir, translation_output):
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise _sub.CalledProcessError(
                    1, ["git", "checkout"], output=b"", stderr=b"simulated"
                )
            real_restore(component_dir, translation_output)

        monkeypatch.setattr("pipeline.lib.source_toggle.restore_baseline", flaky_restore)
        # sim2real.py imports the symbol locally inside _cmd_build, but the
        # local `from pipeline.lib.source_toggle import restore_baseline`
        # inside the function resolves the name from the module at call
        # time (fresh import binding) — monkeypatch on the module attribute
        # covers both local and module-level use.

        rc = sim2real.main(["build", "--translation", thash, "--force-rebuild"])
        assert rc == 2

        # algo1 dispatched, but algo2 must NOT have been dispatched after
        # the finally failure — break should exit the loop.
        assert len(dispatched) == 1
        assert "algo1" in dispatched[0]

        err = capsys.readouterr().err
        assert "failed to restore baseline after build for algo1" in err

    def test_read_algo_output_failure_returns_2(self, tmp_path, monkeypatch, capsys):
        """OSError reading algo_output.json returns exit code 2.

        Reviewer's suggested test (2).
        """
        exp_root, src, thash = self._make_fixture(tmp_path, monkeypatch)

        monkeypatch.setattr("pipeline.lib.build.probe_image_digest", lambda *a, **k: None)
        # Delete algo1's _output.json so the read fails.
        tdir = exp_root / "workspace" / "translations" / thash
        (tdir / "generated" / "algo1" / "algo1_output.json").unlink()

        rc = sim2real.main(["build", "--translation", thash, "--force-rebuild"])
        # Completeness check should catch it first with code 2 and a
        # message about missing outputs.
        assert rc == 2
        err = capsys.readouterr().err
        assert "algo1" in err
