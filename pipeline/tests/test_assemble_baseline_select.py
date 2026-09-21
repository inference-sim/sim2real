"""Tests for one-baseline selection and overlay reuse (issue #921)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from pipeline.lib import assemble_run, layout
from pipeline.lib.errors import AssembleError


@pytest.fixture(autouse=True)
def _isolated_experiment_root(tmp_path):
    layout._EXPERIMENT_ROOT = tmp_path
    yield
    layout._EXPERIMENT_ROOT = None


_TWO = [
    {"name": "baseline", "scenario": "baselines/baseline.yaml"},
    {"name": "weka", "scenario": "baselines/baseline-weka.yaml"},
]


class TestSelectBaseline:
    def test_explicit_request_wins(self):
        assert assemble_run.select_baseline(_TWO, "weka")["name"] == "weka"

    def test_default_prefers_entry_named_baseline(self):
        reordered = [_TWO[1], _TWO[0]]
        assert assemble_run.select_baseline(reordered, None)["name"] == "baseline"

    def test_default_falls_back_to_first_entry(self):
        only = [{"name": "weka", "scenario": "b.yaml"},
                {"name": "other", "scenario": "c.yaml"}]
        assert assemble_run.select_baseline(only, None)["name"] == "weka"

    def test_unknown_request_refuses_and_lists_available(self):
        with pytest.raises(AssembleError) as exc:
            assemble_run.select_baseline(_TWO, "nope")
        msg = str(exc.value)
        assert "nope" in msg
        assert "baseline" in msg and "weka" in msg

    def test_empty_baselines_refuses(self):
        with pytest.raises(AssembleError) as exc:
            assemble_run.select_baseline([], None)
        assert "no baselines" in str(exc.value).lower()

    def test_returns_the_same_object_not_a_copy(self):
        entry = assemble_run.select_baseline(_TWO, "weka")
        assert entry is _TWO[1]


class TestDefaultBaselineName:
    def test_prefers_literal_baseline(self):
        assert assemble_run.default_baseline_name(_TWO) == "baseline"

    def test_falls_back_to_first(self):
        assert assemble_run.default_baseline_name(
            [{"name": "weka"}, {"name": "baseline2"}]
        ) == "weka"

    def test_empty_is_empty_string(self):
        assert assemble_run.default_baseline_name([]) == ""


def _overlay(root: Path, name: str, scenario_name: str = "src-scenario") -> Path:
    d = root / "baselines" / name
    d.mkdir(parents=True, exist_ok=True)
    p = d / "baseline_config.yaml"
    p.write_text(yaml.dump({"scenario": [{"name": scenario_name}]}))
    return p


class TestFindBaselineOverlay:
    def test_prefers_the_selected_baselines_own_overlay(self, tmp_path):
        own = _overlay(tmp_path, "weka")
        _overlay(tmp_path, "baseline")
        path, reused = assemble_run.find_baseline_overlay(
            tmp_path, selected="weka", default_name="baseline"
        )
        assert path == own
        assert reused is False

    def test_reuses_the_sole_overlay_when_selected_has_none(self, tmp_path):
        other = _overlay(tmp_path, "baseline")
        path, reused = assemble_run.find_baseline_overlay(
            tmp_path, selected="weka", default_name="baseline"
        )
        assert path == other
        assert reused is True

    def test_reuses_the_sole_overlay_even_when_it_is_not_the_default(self, tmp_path):
        other = _overlay(tmp_path, "thirdthing")
        path, reused = assemble_run.find_baseline_overlay(
            tmp_path, selected="weka", default_name="baseline"
        )
        assert path == other
        assert reused is True

    def test_several_overlays_tie_break_on_the_default_name(self, tmp_path):
        want = _overlay(tmp_path, "baseline")
        _overlay(tmp_path, "thirdthing")
        path, reused = assemble_run.find_baseline_overlay(
            tmp_path, selected="weka", default_name="baseline"
        )
        assert path == want
        assert reused is True

    def test_several_overlays_and_no_default_refuses_naming_candidates(self, tmp_path):
        _overlay(tmp_path, "alpha")
        _overlay(tmp_path, "bravo")
        with pytest.raises(AssembleError) as exc:
            assemble_run.find_baseline_overlay(
                tmp_path, selected="weka", default_name="baseline"
            )
        msg = str(exc.value)
        assert "alpha" in msg and "bravo" in msg
        assert "--baseline" in msg

    def test_falls_back_to_the_legacy_flat_overlay(self, tmp_path):
        legacy = tmp_path / "baseline_config.yaml"
        legacy.write_text(yaml.dump({"scenario": [{"name": "x"}]}))
        path, reused = assemble_run.find_baseline_overlay(
            tmp_path, selected="weka", default_name="baseline"
        )
        assert path == legacy
        assert reused is True

    def test_per_baseline_layout_wins_over_legacy_flat(self, tmp_path):
        own = _overlay(tmp_path, "weka")
        (tmp_path / "baseline_config.yaml").write_text(
            yaml.dump({"scenario": [{"name": "legacy"}]})
        )
        path, reused = assemble_run.find_baseline_overlay(
            tmp_path, selected="weka", default_name="baseline"
        )
        assert path == own
        assert reused is False

    def test_returns_none_when_nothing_exists(self, tmp_path):
        path, reused = assemble_run.find_baseline_overlay(
            tmp_path, selected="weka", default_name="baseline"
        )
        assert path is None
        assert reused is False

    def test_ignores_a_baselines_dir_with_no_config_file(self, tmp_path):
        (tmp_path / "baselines" / "empty").mkdir(parents=True)
        path, reused = assemble_run.find_baseline_overlay(
            tmp_path, selected="weka", default_name="baseline"
        )
        assert path is None
        assert reused is False


class TestResolveBaselineAlignsOverlay:
    def test_overlay_scenario_name_is_realigned_to_the_bundle(self, tmp_path):
        bundle = tmp_path / "b.yaml"
        bundle.write_text(yaml.dump({"scenario": [{"name": "weka-scn", "a": 1}]}))
        overlay = tmp_path / "o.yaml"
        overlay.write_text(yaml.dump({"scenario": [{"name": "other-scn", "b": 2}]}))
        resolved = assemble_run.resolve_baseline(
            bundle_path=bundle, overlay_path=overlay, framework_defaults={}
        )
        # One scenario entry, not two: the overlay merged onto the bundle
        # instead of appending a phantom entry (the issue #516 failure mode).
        assert len(resolved["scenario"]) == 1
        assert resolved["scenario"][0]["name"] == "weka-scn"
        assert resolved["scenario"][0]["a"] == 1
        assert resolved["scenario"][0]["b"] == 2

    def test_matching_names_are_unaffected(self, tmp_path):
        bundle = tmp_path / "b.yaml"
        bundle.write_text(yaml.dump({"scenario": [{"name": "s", "a": 1}]}))
        overlay = tmp_path / "o.yaml"
        overlay.write_text(yaml.dump({"scenario": [{"name": "s", "b": 2}]}))
        resolved = assemble_run.resolve_baseline(
            bundle_path=bundle, overlay_path=overlay, framework_defaults={}
        )
        assert len(resolved["scenario"]) == 1
        assert resolved["scenario"][0] == {"name": "s", "a": 1, "b": 2}


def _write_yaml(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data, sort_keys=False))


def _write_json(path: Path, data) -> None:
    import json
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def _make_two_baseline_experiment(tmp_path: Path) -> dict:
    """A bundle shaped like pd-infocomm-4: two baselines, one overlay.

    The algorithm declares ``defaults: baseline``; nothing names ``weka``, so
    only ``generated/baselines/baseline/`` exists. That is the exact shape that
    deployed an unconfigured EPP in issue #921.
    """
    exp_root = tmp_path / "exp"
    layout._EXPERIMENT_ROOT = exp_root
    workspace = exp_root / "workspace"

    cluster_id = "ocp-east"
    _write_json(
        workspace / "clusters" / cluster_id / "cluster_config.json",
        {
            "cluster_id": cluster_id,
            "namespaces": ["sim2real-slot-0"],
            "secret_names": {"hf_token": "hf-secret"},
            "workspaces": {},
        },
    )

    manifest = {
        "kind": "sim2real-transfer",
        "version": 3,
        "scenario": "test-scenario",
        "component": {"repo": "acme/llm-d-inference-scheduler", "kind": "gaie"},
        "context": {"text": "", "files": []},
        "baselines": [
            {"name": "baseline", "scenario": "baselines/baseline.yaml"},
            {"name": "weka", "scenario": "baselines/baseline-weka.yaml"},
        ],
        "algorithms": [
            {"name": "algoa", "source": "algo/algoa.py", "defaults": "baseline"},
        ],
        "workloads": ["workloads/w1.yaml"],
        "defaults": {"disable": []},
    }
    _write_yaml(exp_root / "transfer.yaml", manifest)
    _write_yaml(
        exp_root / "baselines" / "baseline.yaml",
        {"scenario": [{"name": "scn-30b", "model": {"name": "Qwen3-30B"}}]},
    )
    _write_yaml(
        exp_root / "baselines" / "baseline-weka.yaml",
        {"scenario": [{"name": "scn-weka", "model": {"name": "Qwen3-Next-80B"}}]},
    )
    _write_yaml(
        exp_root / "workloads" / "w1.yaml", {"name": "wl_a", "num_requests": 10}
    )
    (exp_root / "algo").mkdir(parents=True, exist_ok=True)
    (exp_root / "algo" / "algoa.py").write_text("# stub\n")

    thash = "b" * 64
    tdir = workspace / "translations" / thash
    generated = tdir / "generated"
    generated.mkdir(parents=True)
    _write_json(
        tdir / "translation_output.json",
        {
            "version": 1,
            "translation_hash": thash,
            "source": "byo",
            "alias": "algoa",
            "algorithms": [
                {
                    "name": "algoa",
                    "source_path": None,
                    "source_sha256": None,
                    "config_path": "generated/algoa/algoa_config.yaml",
                    "image_ref": "ghcr.io/foo/bar:v1",
                    "image_digest": "sha256:aa",
                }
            ],
            "created_at": "2026-09-21T14:00:00Z",
        },
    )
    _write_yaml(
        generated / "algoa" / "algoa_config.yaml",
        {"scenario": [{"name": "scn-30b", "router": {"epp": {"replicas": 2}}}]},
    )
    # Only the referenced baseline gets an overlay — the #921 shape.
    _write_yaml(
        generated / "baselines" / "baseline" / "baseline_config.yaml",
        {"scenario": [{"name": "scn-30b", "router": {"epp": {"replicas": 3}}}]},
    )
    return {
        "exp_root": exp_root,
        "cluster_id": cluster_id,
        "translation_hash": thash,
        "manifest_path": exp_root / "transfer.yaml",
    }


def _assemble(env, *, run="r1", baseline=None, force=False, replicas=None):
    assemble_run.assemble_run(
        translation_hash=env["translation_hash"],
        translation_ref=env["translation_hash"][:12],
        cluster_id=env["cluster_id"],
        run_name=run,
        experiment_root=env["exp_root"],
        manifest_path=env["manifest_path"],
        force=force,
        replicas=replicas,
        baseline_request=baseline,
        now_iso="2026-09-21T14:05:00Z",
    )


def _runs(env) -> Path:
    return env["exp_root"] / "workspace" / "runs"


def _scenario_stems(env, run="r1") -> list[str]:
    cluster = _runs(env) / run / "cluster"
    return sorted(
        p.stem for p in cluster.glob("*.yaml")
        if not p.name.startswith("pipelinerun-")
    )


class TestAssembleEmitsOneBaseline:
    def test_default_selection_emits_only_the_default_baseline(self, tmp_path):
        env = _make_two_baseline_experiment(tmp_path)
        _assemble(env)
        assert _scenario_stems(env) == ["algoa", "baseline"]

    def test_explicit_selection_emits_only_that_baseline(self, tmp_path):
        env = _make_two_baseline_experiment(tmp_path)
        _assemble(env, baseline="weka")
        assert _scenario_stems(env) == ["algoa", "weka"]

    def test_selected_baseline_reuses_the_only_overlay(self, tmp_path):
        env = _make_two_baseline_experiment(tmp_path)
        _assemble(env, baseline="weka")
        resolved = yaml.safe_load(
            (_runs(env) / "r1" / "cluster" / "weka.yaml").read_text()
        )
        # One scenario entry, carrying weka's model and the reused overlay's
        # EPP config realigned onto weka's scenario name.
        assert len(resolved["scenario"]) == 1
        assert resolved["scenario"][0]["name"] == "scn-weka"
        assert resolved["scenario"][0]["model"]["name"] == "Qwen3-Next-80B"
        assert resolved["scenario"][0]["router"]["epp"]["replicas"] == 3

    def test_algorithm_rebases_onto_the_selected_baseline(self, tmp_path):
        env = _make_two_baseline_experiment(tmp_path)
        _assemble(env, baseline="weka")
        resolved = yaml.safe_load(
            (_runs(env) / "r1" / "cluster" / "algoa.yaml").read_text()
        )
        assert len(resolved["scenario"]) == 1
        assert resolved["scenario"][0]["name"] == "scn-weka"
        assert resolved["scenario"][0]["model"]["name"] == "Qwen3-Next-80B"
        # Treatment overlay still wins over the baseline overlay.
        assert resolved["scenario"][0]["router"]["epp"]["replicas"] == 2

    def test_rebased_algorithms_are_recorded_for_the_cli(self, tmp_path):
        env = _make_two_baseline_experiment(tmp_path)
        _assemble(env, baseline="weka")
        assert assemble_run.assemble_run.rebased_algorithms == ["algoa"]

    def test_no_rebase_note_when_defaults_already_match(self, tmp_path):
        env = _make_two_baseline_experiment(tmp_path)
        _assemble(env, baseline="baseline")
        assert assemble_run.assemble_run.rebased_algorithms == []

    def test_unknown_baseline_refuses(self, tmp_path):
        env = _make_two_baseline_experiment(tmp_path)
        with pytest.raises(AssembleError) as exc:
            _assemble(env, baseline="nope")
        assert "nope" in str(exc.value)

    def test_pipelineruns_cover_only_the_selected_baseline(self, tmp_path):
        env = _make_two_baseline_experiment(tmp_path)
        _assemble(env, baseline="weka")
        cluster = _runs(env) / "r1" / "cluster"
        names = sorted(p.name for p in cluster.glob("pipelinerun-*.yaml"))
        assert names == [
            "pipelinerun-wl-a|algoa|i1.yaml",
            "pipelinerun-wl-a|weka|i1.yaml",
        ]


class TestResolveTreatmentAlignsItsLayers:
    """A rebased algorithm's overlay was authored against another baseline.

    Its ``scenario[0].name`` therefore names the baseline its ``defaults``
    pointed at, not the one ``--baseline`` selected. Without realignment
    deep_merge appends a phantom second scenario entry, which llm-d-benchmark
    renders as a separate deployment (issue #516) — so the arm would deploy
    twice, once with the framework-default model.
    """

    def test_overlay_named_for_another_baseline_is_realigned(self, tmp_path):
        baseline = {"scenario": [{"name": "scn-weka", "model": {"name": "big"}}]}
        overlay = tmp_path / "o.yaml"
        overlay.write_text(
            yaml.dump({"scenario": [{"name": "scn-30b", "epp": {"replicas": 2}}]})
        )
        resolved = assemble_run.resolve_treatment(
            baseline_resolved=baseline, diffs_path=None, overlay_path=overlay
        )
        assert len(resolved["scenario"]) == 1
        assert resolved["scenario"][0]["name"] == "scn-weka"
        assert resolved["scenario"][0]["model"]["name"] == "big"
        assert resolved["scenario"][0]["epp"]["replicas"] == 2

    def test_treatment_diffs_are_realigned_too(self, tmp_path):
        baseline = {"scenario": [{"name": "scn-weka", "a": 1}]}
        diffs = tmp_path / "d.yaml"
        diffs.write_text(yaml.dump({"scenario": [{"name": "scn-30b", "b": 2}]}))
        resolved = assemble_run.resolve_treatment(
            baseline_resolved=baseline, diffs_path=diffs, overlay_path=None
        )
        assert len(resolved["scenario"]) == 1
        assert resolved["scenario"][0] == {"name": "scn-weka", "a": 1, "b": 2}

    def test_matching_names_are_unaffected(self, tmp_path):
        baseline = {"scenario": [{"name": "s", "a": 1}]}
        overlay = tmp_path / "o.yaml"
        overlay.write_text(yaml.dump({"scenario": [{"name": "s", "b": 2}]}))
        resolved = assemble_run.resolve_treatment(
            baseline_resolved=baseline, diffs_path=None, overlay_path=overlay
        )
        assert len(resolved["scenario"]) == 1
        assert resolved["scenario"][0] == {"name": "s", "a": 1, "b": 2}


class TestBaselineSelectionIsHashed:
    def test_snapshot_records_the_selection(self, tmp_path):
        env = _make_two_baseline_experiment(tmp_path)
        _assemble(env, run="r1", baseline="weka")
        ma = yaml.safe_load(
            (_runs(env) / "r1" / "manifest.assembly.yaml").read_text()
        )
        assert ma["baseline"] == "weka"

    def test_two_selections_get_different_params_hashes(self, tmp_path):
        import json
        env = _make_two_baseline_experiment(tmp_path)
        _assemble(env, run="r1", baseline="baseline")
        _assemble(env, run="r2", baseline="weka")
        h1 = json.loads(
            (_runs(env) / "r1" / "run_metadata.json").read_text()
        )["params_hash"]
        h2 = json.loads(
            (_runs(env) / "r2" / "run_metadata.json").read_text()
        )["params_hash"]
        assert h1 != h2

    def test_reassembling_with_a_different_baseline_is_drift(self, tmp_path):
        env = _make_two_baseline_experiment(tmp_path)
        _assemble(env, run="r1", baseline="baseline")
        with pytest.raises(AssembleError) as exc:
            _assemble(env, run="r1", baseline="weka")
        assert "changed since last assemble" in str(exc.value)

    def test_force_overrides_the_drift_refusal(self, tmp_path):
        env = _make_two_baseline_experiment(tmp_path)
        _assemble(env, run="r1", baseline="baseline")
        _assemble(env, run="r1", baseline="weka", force=True)
        ma = yaml.safe_load(
            (_runs(env) / "r1" / "manifest.assembly.yaml").read_text()
        )
        assert ma["baseline"] == "weka"
        assert _scenario_stems(env) == ["algoa", "weka"]

    def test_reassembling_with_the_same_baseline_is_not_drift(self, tmp_path):
        env = _make_two_baseline_experiment(tmp_path)
        _assemble(env, run="r1", baseline="weka")
        _assemble(env, run="r1", baseline="weka")
        assert assemble_run.assemble_run.status == "noop"

    def test_legacy_snapshot_without_the_key_does_not_report_false_drift(
        self, tmp_path
    ):
        """A run assembled before #921 has no ``baseline`` key.

        Its ``params_hash`` was computed without one, so the drift check must
        compare without one too — otherwise every pre-existing run would refuse
        its next assemble.
        """
        env = _make_two_baseline_experiment(tmp_path)
        _assemble(env, run="r1", baseline="baseline")
        ma_path = _runs(env) / "r1" / "manifest.assembly.yaml"
        ma = yaml.safe_load(ma_path.read_text())
        del ma["baseline"]
        ma_path.write_text(yaml.dump(ma))
        # Recompute params_hash the way a pre-#921 assemble would have.
        import json
        rm_path = _runs(env) / "r1" / "run_metadata.json"
        rm = json.loads(rm_path.read_text())
        rm["params_hash"] = assemble_run.compute_params_hash(ma_path)
        rm_path.write_text(json.dumps(rm, indent=2, sort_keys=True) + "\n")
        # Must not raise.
        _assemble(env, run="r1", baseline="baseline")
        assert assemble_run.assemble_run.status == "noop"


class TestBaselineFlagParsing:
    def test_flag_defaults_to_none(self):
        from pipeline import sim2real
        parser = sim2real.build_parser()
        args = parser.parse_args(
            ["assemble", "--translation", "abcd", "--cluster", "c", "--run", "r"]
        )
        assert args.baseline is None

    def test_flag_captures_the_name(self):
        from pipeline import sim2real
        parser = sim2real.build_parser()
        args = parser.parse_args(
            ["assemble", "--translation", "abcd", "--cluster", "c", "--run", "r",
             "--baseline", "weka"]
        )
        assert args.baseline == "weka"


class TestSelectionIsStickyAcrossReassemble:
    """An explicit --baseline must survive a re-assemble that omits the flag.

    Mirrors how ``--replicas`` falls back to the run's recorded count. Without
    it, `--force` after an unrelated transfer.yaml edit silently re-resolves
    every arm against the default baseline — a different model, with no
    operator-visible signal. That is the failure class #921 exists to prevent.
    """

    def test_omitting_the_flag_inherits_the_recorded_selection(self, tmp_path):
        env = _make_two_baseline_experiment(tmp_path)
        _assemble(env, run="r1", baseline="weka")
        # No --baseline: must not be read as "revert to the default".
        _assemble(env, run="r1")
        ma = yaml.safe_load(
            (_runs(env) / "r1" / "manifest.assembly.yaml").read_text()
        )
        assert ma["baseline"] == "weka"
        assert _scenario_stems(env) == ["algoa", "weka"]

    def test_force_without_the_flag_keeps_the_recorded_selection(self, tmp_path):
        env = _make_two_baseline_experiment(tmp_path)
        _assemble(env, run="r1", baseline="weka")
        _assemble(env, run="r1", force=True)
        ma = yaml.safe_load(
            (_runs(env) / "r1" / "manifest.assembly.yaml").read_text()
        )
        assert ma["baseline"] == "weka"
        resolved = yaml.safe_load(
            (_runs(env) / "r1" / "cluster" / "weka.yaml").read_text()
        )
        assert resolved["scenario"][0]["model"]["name"] == "Qwen3-Next-80B"
        # weka.yaml must not have been pruned in favour of baseline.yaml.
        assert not (_runs(env) / "r1" / "cluster" / "baseline.yaml").exists()
        assert assemble_run.assemble_run.pruned_files == []

    def test_explicitly_naming_a_different_baseline_still_drifts(self, tmp_path):
        env = _make_two_baseline_experiment(tmp_path)
        _assemble(env, run="r1", baseline="weka")
        with pytest.raises(AssembleError) as exc:
            _assemble(env, run="r1", baseline="baseline")
        assert "changed since last assemble" in str(exc.value)

    def test_a_fresh_run_still_uses_the_default(self, tmp_path):
        env = _make_two_baseline_experiment(tmp_path)
        _assemble(env, run="r1", baseline="weka")
        # A different run name is fresh: no recorded selection to inherit.
        _assemble(env, run="r2")
        ma = yaml.safe_load(
            (_runs(env) / "r2" / "manifest.assembly.yaml").read_text()
        )
        assert ma["baseline"] == "baseline"


class TestScopedAssembleRejectsBaseline:
    """--baseline is a run-wide choice; a scoped assemble cannot record it.

    A scoped invocation writes neither manifest.assembly.yaml nor
    run_metadata.json, so honouring the flag would rewrite the scoped packages
    against a different baseline while the snapshot kept saying otherwise — on a
    pre-#921 run, an 80B treatment arm measured against a 30B baseline with
    nothing on disk recording it.
    """

    def test_package_scope_with_baseline_refuses(self, tmp_path):
        env = _make_two_baseline_experiment(tmp_path)
        _assemble(env, run="r1")
        with pytest.raises(AssembleError) as exc:
            assemble_run.assemble_run(
                translation_hash=env["translation_hash"],
                translation_ref="x",
                cluster_id=env["cluster_id"],
                run_name="r1",
                experiment_root=env["exp_root"],
                manifest_path=env["manifest_path"],
                force=True,
                package_filter=["algoa"],
                baseline_request="weka",
                now_iso="2026-09-21T14:05:00Z",
            )
        msg = str(exc.value)
        assert "--baseline" in msg
        assert "--workload/--package" in msg

    def test_workload_scope_with_baseline_refuses(self, tmp_path):
        env = _make_two_baseline_experiment(tmp_path)
        _assemble(env, run="r1")
        with pytest.raises(AssembleError) as exc:
            assemble_run.assemble_run(
                translation_hash=env["translation_hash"],
                translation_ref="x",
                cluster_id=env["cluster_id"],
                run_name="r1",
                experiment_root=env["exp_root"],
                manifest_path=env["manifest_path"],
                force=False,
                workload_filter=["wl_a"],
                baseline_request="weka",
                now_iso="2026-09-21T14:05:00Z",
            )
        assert "--baseline" in str(exc.value)

    def test_scope_without_baseline_is_unaffected(self, tmp_path):
        env = _make_two_baseline_experiment(tmp_path)
        _assemble(env, run="r1", baseline="weka")
        assemble_run.assemble_run(
            translation_hash=env["translation_hash"],
            translation_ref="x",
            cluster_id=env["cluster_id"],
            run_name="r1",
            experiment_root=env["exp_root"],
            manifest_path=env["manifest_path"],
            force=True,
            package_filter=["algoa"],
            now_iso="2026-09-21T14:05:00Z",
        )
        # The scoped rewrite inherited the recorded selection, not the default.
        resolved = yaml.safe_load(
            (_runs(env) / "r1" / "cluster" / "algoa.yaml").read_text()
        )
        assert resolved["scenario"][0]["model"]["name"] == "Qwen3-Next-80B"
