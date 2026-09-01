"""Tests for scoped assemble (issue #876): scope resolution, the per-pair
decision table, results wiping, and orphan pruning."""

from __future__ import annotations

import pytest

from pipeline.lib import assemble_run
from pipeline.lib.errors import AssembleError


class TestPipelinerunFilename:
    def test_matches_generator_shape(self):
        assert (
            assemble_run.pipelinerun_filename("wl_a", "baseline", 3)
            == "pipelinerun-wl-a|baseline|i3.yaml"
        )

    def test_underscore_substituted_only_in_workload_segment(self):
        assert (
            assemble_run.pipelinerun_filename("a_b", "pkg_c", 1)
            == "pipelinerun-a-b|pkg_c|i1.yaml"
        )


def _scope(**kw):
    kw.setdefault("workload_names", ["wl_a", "wl_b"])
    kw.setdefault("package_names", ["baseline", "sr"])
    kw.setdefault("workload_filter", None)
    kw.setdefault("package_filter", None)
    return assemble_run.resolve_pair_scope(**kw)


class TestResolvePairScope:
    def test_no_filters_is_full_cross_product(self):
        assert _scope() == [
            ("wl_a", "baseline"), ("wl_a", "sr"),
            ("wl_b", "baseline"), ("wl_b", "sr"),
        ]

    def test_workload_filter_narrows(self):
        assert _scope(workload_filter=["wl_b"]) == [
            ("wl_b", "baseline"), ("wl_b", "sr"),
        ]

    def test_package_filter_narrows(self):
        assert _scope(package_filter=["sr"]) == [("wl_a", "sr"), ("wl_b", "sr")]

    def test_both_filters_intersect(self):
        assert _scope(workload_filter=["wl_a"], package_filter=["sr"]) == [
            ("wl_a", "sr")
        ]

    def test_comma_separated_and_glob_accepted(self):
        assert _scope(package_filter=["base*,sr"]) == [
            ("wl_a", "baseline"), ("wl_a", "sr"),
            ("wl_b", "baseline"), ("wl_b", "sr"),
        ]

    def test_underscore_and_hyphen_spellings_both_match(self):
        # The `_` -> `-` substitution is deprecated; accept either spelling
        # rather than making the operator guess which producer they are
        # naming.
        assert _scope(workload_filter=["wl-a"]) == [
            ("wl_a", "baseline"), ("wl_a", "sr"),
        ]

    def test_scope_order_follows_declaration_not_filter_order(self):
        assert _scope(workload_filter=["wl_b", "wl_a"]) == [
            ("wl_a", "baseline"), ("wl_a", "sr"),
            ("wl_b", "baseline"), ("wl_b", "sr"),
        ]

    def test_unknown_workload_lists_valid_values(self):
        with pytest.raises(AssembleError) as exc:
            _scope(workload_filter=["nope"])
        msg = str(exc.value)
        assert "--workload" in msg
        assert "nope" in msg
        assert "wl_a" in msg and "wl_b" in msg

    def test_unknown_package_lists_valid_values(self):
        with pytest.raises(AssembleError) as exc:
            _scope(package_filter=["nope"])
        msg = str(exc.value)
        assert "--package" in msg
        assert "baseline" in msg and "sr" in msg

    def test_glob_matching_nothing_is_an_error(self):
        with pytest.raises(AssembleError, match="--package"):
            _scope(package_filter=["zzz*"])
