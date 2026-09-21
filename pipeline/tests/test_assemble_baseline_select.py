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
