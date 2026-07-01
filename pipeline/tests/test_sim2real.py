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
