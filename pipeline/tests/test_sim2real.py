"""Tests for pipeline/sim2real.py — sim2real CLI top-level entry."""

from __future__ import annotations

import argparse

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
