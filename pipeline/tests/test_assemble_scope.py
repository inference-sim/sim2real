"""Tests for scoped assemble (issue #876): scope resolution, the per-pair
decision table, results wiping, and orphan pruning."""

from __future__ import annotations

import json
import time

import pytest
import yaml

from pipeline.lib import assemble_run
from pipeline.lib.errors import AssembleError
from pipeline.tests.test_assemble_run import _make_experiment


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


# ── Integration: the real assemble_run() entry point ────────────────────────


def _fx(tmp_path):
    return _make_experiment(
        tmp_path, algo_names_registered=["sr"], algo_names_manifest=["sr"]
    )


def _assemble(fx, **kw):
    kw.setdefault("force", False)
    kw.setdefault("now_iso", "2026-07-01T00:00:00Z")
    return assemble_run.assemble_run(
        translation_hash=fx["translation_hash"],
        translation_ref=fx["translation_hash"],
        cluster_id=fx["cluster_id"],
        run_name="trial-1",
        experiment_root=fx["exp_root"],
        manifest_path=fx["manifest_path"],
        **kw,
    )


def _run_dir(fx):
    return fx["exp_root"] / "workspace" / "runs" / "trial-1"


def _seed_results(run_dir, package, workload="wl_a", iteration=1):
    d = run_dir / "results" / package / workload / f"i{iteration}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "trace_data.csv").write_text("measured\n")
    return d


class TestForceNoLongerDestroysResults:
    def test_force_wipes_only_regenerated_pairs_not_the_run_dir(self, tmp_path):
        """The core of issue #876: --force used to rmtree the entire run
        directory, and results/ lives inside it. Now it wipes results only for
        the pairs it regenerates and leaves everything else alone."""
        fx = _fx(tmp_path)
        _assemble(fx)
        run_dir = _run_dir(fx)
        baseline_results = _seed_results(run_dir, "baseline")
        (run_dir / "notes.txt").write_text("operator notes")
        _assemble(fx, force=True, assume_yes=True,
                  now_iso="2026-07-02T00:00:00Z")
        # Unrelated run-dir contents survive (old behavior: destroyed).
        assert (run_dir / "notes.txt").read_text() == "operator notes"
        # The regenerated pair's results were wiped (documented --force behavior).
        assert not baseline_results.exists()

    def test_force_with_no_wipe_keeps_results(self, tmp_path):
        fx = _fx(tmp_path)
        _assemble(fx)
        run_dir = _run_dir(fx)
        baseline_results = _seed_results(run_dir, "baseline")
        _assemble(fx, force=True, no_wipe=True,
                  now_iso="2026-07-02T00:00:00Z")
        assert (baseline_results / "trace_data.csv").read_text() == "measured\n"
        # And the PipelineRun was still regenerated.
        assert (run_dir / "cluster"
                / "pipelinerun-wl-a|baseline|i1.yaml").exists()

    def test_force_does_not_prompt(self, tmp_path, monkeypatch):
        """--force stays non-interactive: it is the long-documented way to
        discard a run's results, and prompting would break scripted use."""
        fx = _fx(tmp_path)
        _assemble(fx)
        _seed_results(_run_dir(fx), "baseline")

        def _boom(_displays):
            raise AssertionError("should not prompt under --force")

        monkeypatch.setattr(assemble_run, "_confirm_results_wipe", _boom)
        _assemble(fx, force=True, now_iso="2026-07-02T00:00:00Z")

    def test_results_report_lists_what_was_wiped(self, tmp_path):
        fx = _fx(tmp_path)
        _assemble(fx)
        _seed_results(_run_dir(fx), "baseline")
        _assemble(fx, force=True, assume_yes=True,
                  now_iso="2026-07-02T00:00:00Z")
        assert assemble_run.assemble_run.wiped_results == [
            "results/baseline/wl_a/i1/"
        ]


class TestRowTwoPrompt:
    """Row 2 of the table — no PipelineRun but results present — regenerates
    without the operator having typed any flag, so it must confirm first."""

    def _row_two(self, fx):
        """Delete one PipelineRun and seed results for that same pair."""
        run_dir = _run_dir(fx)
        (run_dir / "cluster" / "pipelinerun-wl-a|baseline|i1.yaml").unlink()
        return _seed_results(run_dir, "baseline")

    def test_prompt_accepted_wipes_and_regenerates(self, tmp_path, monkeypatch):
        fx = _fx(tmp_path)
        _assemble(fx)
        results = self._row_two(fx)
        seen = {}
        monkeypatch.setattr(
            assemble_run, "_confirm_results_wipe",
            lambda displays: seen.setdefault("displays", displays) or True,
        )
        _assemble(fx, now_iso="2026-07-02T00:00:00Z")
        assert seen["displays"] == ["results/baseline/wl_a/i1/"]
        assert not results.exists()
        assert (_run_dir(fx) / "cluster"
                / "pipelinerun-wl-a|baseline|i1.yaml").exists()

    def test_prompt_declined_aborts_without_writing(self, tmp_path, monkeypatch):
        fx = _fx(tmp_path)
        _assemble(fx)
        results = self._row_two(fx)
        monkeypatch.setattr(
            assemble_run, "_confirm_results_wipe", lambda displays: False
        )
        with pytest.raises(AssembleError, match="aborted"):
            _assemble(fx, now_iso="2026-07-02T00:00:00Z")
        # Results intact and the PipelineRun was NOT regenerated.
        assert (results / "trace_data.csv").read_text() == "measured\n"
        assert not (_run_dir(fx) / "cluster"
                    / "pipelinerun-wl-a|baseline|i1.yaml").exists()

    def test_eof_on_prompt_declines(self, tmp_path, monkeypatch):
        fx = _fx(tmp_path)
        _assemble(fx)
        self._row_two(fx)

        def _eof(_prompt):
            raise EOFError

        monkeypatch.setattr("builtins.input", _eof)
        with pytest.raises(AssembleError, match="aborted"):
            _assemble(fx, now_iso="2026-07-02T00:00:00Z")

    def test_no_wipe_skips_the_prompt_entirely(self, tmp_path, monkeypatch):
        fx = _fx(tmp_path)
        _assemble(fx)
        results = self._row_two(fx)

        def _boom(_displays):
            raise AssertionError("should not prompt under --no-wipe")

        monkeypatch.setattr(assemble_run, "_confirm_results_wipe", _boom)
        _assemble(fx, no_wipe=True, now_iso="2026-07-02T00:00:00Z")
        assert (results / "trace_data.csv").read_text() == "measured\n"

    def test_assume_yes_skips_the_prompt(self, tmp_path, monkeypatch):
        fx = _fx(tmp_path)
        _assemble(fx)
        results = self._row_two(fx)

        def _boom(_displays):
            raise AssertionError("should not prompt under --yes")

        monkeypatch.setattr(assemble_run, "_confirm_results_wipe", _boom)
        _assemble(fx, assume_yes=True, now_iso="2026-07-02T00:00:00Z")
        assert not results.exists()

    def test_no_prompt_when_no_results_exist(self, tmp_path, monkeypatch):
        fx = _fx(tmp_path)
        _assemble(fx)
        (_run_dir(fx) / "cluster" / "pipelinerun-wl-a|baseline|i1.yaml").unlink()

        def _boom(_displays):
            raise AssertionError("nothing to wipe, should not prompt")

        monkeypatch.setattr(assemble_run, "_confirm_results_wipe", _boom)
        _assemble(fx, now_iso="2026-07-02T00:00:00Z")


class TestScopedAssemble:
    def test_scoped_force_leaves_out_of_scope_pipelineruns_untouched(self, tmp_path):
        fx = _fx(tmp_path)
        _assemble(fx)
        run_dir = _run_dir(fx)
        other = run_dir / "cluster" / "pipelinerun-wl-a|sr|i1.yaml"
        before_bytes = other.read_bytes()
        before_mtime = other.stat().st_mtime_ns
        time.sleep(0.01)
        _assemble(fx, force=True, package_filter=["baseline"],
                  now_iso="2026-07-02T00:00:00Z")
        assert other.read_bytes() == before_bytes
        assert other.stat().st_mtime_ns == before_mtime

    def test_scoped_force_regenerates_the_in_scope_pipelinerun(self, tmp_path):
        fx = _fx(tmp_path)
        _assemble(fx)
        run_dir = _run_dir(fx)
        target = run_dir / "cluster" / "pipelinerun-wl-a|baseline|i1.yaml"
        before = target.stat().st_mtime_ns
        time.sleep(0.01)
        _assemble(fx, force=True, package_filter=["baseline"],
                  now_iso="2026-07-02T00:00:00Z")
        assert target.stat().st_mtime_ns != before

    def test_scoped_assemble_leaves_params_hash_and_manifest_assembly(self, tmp_path):
        fx = _fx(tmp_path)
        _assemble(fx)
        run_dir = _run_dir(fx)
        ma = run_dir / "manifest.assembly.yaml"
        rm = run_dir / "run_metadata.json"
        ma_mtime, rm_mtime = ma.stat().st_mtime_ns, rm.stat().st_mtime_ns
        time.sleep(0.01)
        _assemble(fx, force=True, package_filter=["baseline"],
                  now_iso="2026-07-02T00:00:00Z")
        assert ma.stat().st_mtime_ns == ma_mtime
        assert rm.stat().st_mtime_ns == rm_mtime

    def test_scoped_assemble_rewrites_cluster_package_yaml(self, tmp_path):
        fx = _fx(tmp_path)
        _assemble(fx)
        run_dir = _run_dir(fx)
        pkg_yaml = run_dir / "cluster" / "baseline.yaml"
        pkg_yaml.write_text("stale: true\n")
        _assemble(fx, force=True, package_filter=["baseline"],
                  now_iso="2026-07-02T00:00:00Z")
        assert "stale" not in pkg_yaml.read_text()

    def test_scoped_assemble_leaves_out_of_scope_cluster_yaml_alone(self, tmp_path):
        fx = _fx(tmp_path)
        _assemble(fx)
        run_dir = _run_dir(fx)
        other = run_dir / "cluster" / "sr.yaml"
        before = other.stat().st_mtime_ns
        time.sleep(0.01)
        _assemble(fx, force=True, package_filter=["baseline"],
                  now_iso="2026-07-02T00:00:00Z")
        assert other.stat().st_mtime_ns == before

    def test_scoped_assemble_on_missing_run_refuses(self, tmp_path):
        fx = _fx(tmp_path)
        with pytest.raises(AssembleError, match="existing run"):
            _assemble(fx, workload_filter=["wl_a"])

    def test_scoped_assemble_rejects_replicas(self, tmp_path):
        fx = _fx(tmp_path)
        _assemble(fx)
        with pytest.raises(AssembleError, match="--replicas"):
            _assemble(fx, workload_filter=["wl_a"], replicas=2)

    def test_scoped_assemble_uses_recorded_replica_count(self, tmp_path):
        """--replicas is rejected when scoped, so the iteration range comes
        from the run's own manifest.assembly.yaml — otherwise an operator who
        assembled with --replicas 3 would trip the shrink guard just by
        scoping."""
        fx = _fx(tmp_path)
        _assemble(fx, replicas=3)
        run_dir = _run_dir(fx)
        for i in (1, 2, 3):
            (run_dir / "cluster" / f"pipelinerun-wl-a|baseline|i{i}.yaml").unlink()
        _assemble(fx, package_filter=["baseline"],
                  now_iso="2026-07-02T00:00:00Z")
        for i in (1, 2, 3):
            assert (run_dir / "cluster"
                    / f"pipelinerun-wl-a|baseline|i{i}.yaml").exists()

    def test_scoped_assemble_refuses_on_manifest_drift(self, tmp_path):
        fx = _fx(tmp_path)
        _assemble(fx)
        rm_path = _run_dir(fx) / "run_metadata.json"
        rm = json.loads(rm_path.read_text())
        rm["params_hash"] = "0" * 64
        rm_path.write_text(json.dumps(rm))
        with pytest.raises(AssembleError, match="unscoped"):
            _assemble(fx, force=True, package_filter=["baseline"])

    def test_scoped_assemble_refuses_when_metadata_missing(self, tmp_path):
        fx = _fx(tmp_path)
        _assemble(fx)
        (_run_dir(fx) / "run_metadata.json").unlink()
        with pytest.raises(AssembleError, match="unscoped"):
            _assemble(fx, force=True, package_filter=["baseline"])

    def test_scoped_assemble_refuses_on_legacy_shape(self, tmp_path):
        fx = _fx(tmp_path)
        _assemble(fx)
        ma_path = _run_dir(fx) / "manifest.assembly.yaml"
        ma = yaml.safe_load(ma_path.read_text())
        ma.pop("replicas", None)
        ma_path.write_text(yaml.dump(ma, sort_keys=False))
        with pytest.raises(AssembleError, match="unscoped"):
            _assemble(fx, force=True, package_filter=["baseline"])

    def test_scoped_workload_filter_narrows_generation(self, tmp_path):
        fx = _fx(tmp_path)
        _assemble(fx)
        run_dir = _run_dir(fx)
        baseline = run_dir / "cluster" / "pipelinerun-wl-a|baseline|i1.yaml"
        sr = run_dir / "cluster" / "pipelinerun-wl-a|sr|i1.yaml"
        b_before, s_before = (
            baseline.stat().st_mtime_ns, sr.stat().st_mtime_ns
        )
        time.sleep(0.01)
        # Only workload wl_a exists, so a workload-only filter still covers
        # both packages — both get regenerated.
        _assemble(fx, force=True, workload_filter=["wl_a"],
                  now_iso="2026-07-02T00:00:00Z")
        assert baseline.stat().st_mtime_ns != b_before
        assert sr.stat().st_mtime_ns != s_before


class TestAlreadyAssembledReport:
    def test_reassemble_reports_pair_count_and_writes_nothing(self, tmp_path):
        fx = _fx(tmp_path)
        _assemble(fx)
        run_dir = _run_dir(fx)
        files = [p for p in run_dir.rglob("*") if p.is_file()]
        before = {p: p.stat().st_mtime_ns for p in files}
        time.sleep(0.01)
        _assemble(fx, now_iso="2026-07-02T00:00:00Z")
        assert {p: p.stat().st_mtime_ns for p in files} == before
        assert assemble_run.assemble_run.status == "noop"
        # 1 workload x 2 packages x 1 iteration
        assert assemble_run.assemble_run.already_assembled == 2

    def test_scoped_noop_counts_only_pairs_in_scope(self, tmp_path):
        fx = _fx(tmp_path)
        _assemble(fx)
        _assemble(fx, package_filter=["baseline"],
                  now_iso="2026-07-02T00:00:00Z")
        assert assemble_run.assemble_run.status == "noop"
        assert assemble_run.assemble_run.already_assembled == 1

    def test_missing_pipelinerun_is_regenerated_without_force(self, tmp_path):
        """The old no-op path keyed off params_hash, so a deleted PipelineRun
        was invisible. The predicate is now the file itself."""
        fx = _fx(tmp_path)
        _assemble(fx)
        target = _run_dir(fx) / "cluster" / "pipelinerun-wl-a|baseline|i1.yaml"
        target.unlink()
        _assemble(fx, now_iso="2026-07-02T00:00:00Z")
        assert target.exists()
        assert assemble_run.assemble_run.status == "written"


class TestOrphanPruning:
    def test_unscoped_force_prunes_pipelineruns_outside_cross_product(self, tmp_path):
        fx = _fx(tmp_path)
        _assemble(fx)
        run_dir = _run_dir(fx)
        orphan = run_dir / "cluster" / "pipelinerun-wl-gone|baseline|i1.yaml"
        orphan.write_text("stale\n")
        _assemble(fx, force=True, assume_yes=True,
                  now_iso="2026-07-02T00:00:00Z")
        assert not orphan.exists()
        assert "pipelinerun-wl-gone|baseline|i1.yaml" in (
            assemble_run.assemble_run.pruned_files
        )

    def test_unscoped_force_prunes_orphan_scenario_yaml(self, tmp_path):
        fx = _fx(tmp_path)
        _assemble(fx)
        run_dir = _run_dir(fx)
        orphan = run_dir / "cluster" / "dropped-algo.yaml"
        orphan.write_text("stale: true\n")
        _assemble(fx, force=True, assume_yes=True,
                  now_iso="2026-07-02T00:00:00Z")
        assert not orphan.exists()
        assert "dropped-algo.yaml" in assemble_run.assemble_run.pruned_files

    def test_pruning_leaves_the_orphans_results_alone(self, tmp_path):
        """Pruned pairs lose their PipelineRun, never their measured data."""
        fx = _fx(tmp_path)
        _assemble(fx)
        run_dir = _run_dir(fx)
        (run_dir / "cluster" / "pipelinerun-wl-gone|baseline|i1.yaml").write_text("x\n")
        orphan_results = _seed_results(run_dir, "baseline", workload="wl_gone")
        _assemble(fx, force=True, assume_yes=True,
                  now_iso="2026-07-02T00:00:00Z")
        assert (orphan_results / "trace_data.csv").read_text() == "measured\n"

    def test_scoped_assemble_prunes_nothing(self, tmp_path):
        fx = _fx(tmp_path)
        _assemble(fx)
        run_dir = _run_dir(fx)
        orphan = run_dir / "cluster" / "pipelinerun-wl-gone|baseline|i1.yaml"
        orphan.write_text("stale\n")
        _assemble(fx, force=True, package_filter=["baseline"],
                  now_iso="2026-07-02T00:00:00Z")
        assert orphan.exists()
        assert assemble_run.assemble_run.pruned_files == []

    def test_nothing_pruned_when_manifest_unchanged(self, tmp_path):
        fx = _fx(tmp_path)
        _assemble(fx)
        _assemble(fx, force=True, now_iso="2026-07-02T00:00:00Z")
        assert assemble_run.assemble_run.pruned_files == []
