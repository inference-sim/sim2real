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


def _seed(run_dir, *, pipelinerun=False, results=False,
          workload="wl_a", package="baseline", iteration=1):
    """Create the on-disk predicates for one pair."""
    if pipelinerun:
        pr = run_dir / "cluster" / assemble_run.pipelinerun_filename(
            workload, package, iteration)
        pr.parent.mkdir(parents=True, exist_ok=True)
        pr.write_text("seeded\n")
    if results:
        res = run_dir / "results" / package / workload / f"i{iteration}"
        res.mkdir(parents=True, exist_ok=True)
        (res / "trace_data.csv").write_text("seeded\n")


def _plan_one(run_dir, *, force=False, no_wipe=False):
    plans = assemble_run.plan_pairs(
        run_dir=run_dir,
        scope=[("wl_a", "baseline")],
        iterations=[1],
        force=force,
        no_wipe=no_wipe,
    )
    assert len(plans) == 1
    return plans[0]


class TestPlanPairsDecisionTable:
    def test_row1_no_pipelinerun_no_results_generates(self, tmp_path):
        p = _plan_one(tmp_path)
        assert p.regenerate is True
        assert p.wipe_results is False

    def test_row2_no_pipelinerun_with_results_generates_and_wipes(self, tmp_path):
        _seed(tmp_path, results=True)
        p = _plan_one(tmp_path)
        assert p.regenerate is True
        assert p.wipe_results is True

    def test_row2_no_wipe_preserves_results(self, tmp_path):
        _seed(tmp_path, results=True)
        p = _plan_one(tmp_path, no_wipe=True)
        assert p.regenerate is True
        assert p.wipe_results is False

    def test_row3_pipelinerun_exists_without_force_does_nothing(self, tmp_path):
        _seed(tmp_path, pipelinerun=True)
        p = _plan_one(tmp_path)
        assert p.regenerate is False
        assert p.wipe_results is False

    def test_row3_pipelinerun_exists_with_force_regenerates(self, tmp_path):
        _seed(tmp_path, pipelinerun=True)
        p = _plan_one(tmp_path, force=True)
        assert p.regenerate is True
        assert p.wipe_results is False

    def test_row4_both_exist_without_force_keeps_results(self, tmp_path):
        _seed(tmp_path, pipelinerun=True, results=True)
        p = _plan_one(tmp_path)
        assert p.regenerate is False
        # Not regenerating means not wiping — the wipe axis only applies to
        # pairs actually being redone.
        assert p.wipe_results is False

    def test_row4_both_exist_with_force_regenerates_and_wipes(self, tmp_path):
        _seed(tmp_path, pipelinerun=True, results=True)
        p = _plan_one(tmp_path, force=True)
        assert p.regenerate is True
        assert p.wipe_results is True

    def test_row4_force_with_no_wipe_regenerates_and_keeps(self, tmp_path):
        _seed(tmp_path, pipelinerun=True, results=True)
        p = _plan_one(tmp_path, force=True, no_wipe=True)
        assert p.regenerate is True
        assert p.wipe_results is False

    def test_results_path_uses_raw_workload_name(self, tmp_path):
        p = _plan_one(tmp_path)
        assert p.results_path == tmp_path / "results" / "baseline" / "wl_a" / "i1"

    def test_pipelinerun_path_uses_substituted_workload_name(self, tmp_path):
        p = _plan_one(tmp_path)
        assert p.pipelinerun_path == (
            tmp_path / "cluster" / "pipelinerun-wl-a|baseline|i1.yaml"
        )

    def test_empty_results_dir_still_counts_as_present(self, tmp_path):
        # Path.exists() is the predicate, per the issue — an empty iteration
        # directory is still collected state and must not be silently kept
        # while its PipelineRun is regenerated.
        (tmp_path / "results" / "baseline" / "wl_a" / "i1").mkdir(parents=True)
        p = _plan_one(tmp_path)
        assert p.wipe_results is True

    def test_one_plan_per_triple(self, tmp_path):
        plans = assemble_run.plan_pairs(
            run_dir=tmp_path,
            scope=[("wl_a", "baseline"), ("wl_a", "sr")],
            iterations=[1, 2],
            force=False,
            no_wipe=False,
        )
        assert len(plans) == 4
        assert {(p.package, p.iteration) for p in plans} == {
            ("baseline", 1), ("baseline", 2), ("sr", 1), ("sr", 2),
        }
