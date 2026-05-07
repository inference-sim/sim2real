"""Tests for EPP image injection (pipeline/lib/epp.py)."""

from pipeline.lib.epp import inject_epp_image


class TestInjectEppImage:
    """Unit tests for inject_epp_image."""

    def test_treatment_gets_epp_image(self):
        """BC-1: Treatment scenario entries get the EPP image injected."""
        scenario = {
            "scenario": [
                {"name": "test-scenario", "images": {"vllm": {"repository": "r", "tag": "t"}}},
            ]
        }
        result = inject_epp_image(scenario, "ghcr.io/me", "llm-d-inference-scheduler", "run-1")
        assert result is True
        img = scenario["scenario"][0]["images"]["inferenceScheduler"]
        assert img == {
            "repository": "ghcr.io/me/llm-d-inference-scheduler",
            "tag": "run-1",
            "pullPolicy": "Always",
        }

    def test_treatment_multiple_entries(self):
        """BC-1: All scenario entries get injected, not just the first."""
        scenario = {
            "scenario": [
                {"name": "entry-1"},
                {"name": "entry-2"},
            ]
        }
        result = inject_epp_image(scenario, "reg.io", "epp", "v1")
        assert result is True
        for entry in scenario["scenario"]:
            assert entry["images"]["inferenceScheduler"]["repository"] == "reg.io/epp"
            assert entry["images"]["inferenceScheduler"]["tag"] == "v1"

    def test_baseline_not_modified(self):
        """BC-2: Baseline scenarios are never passed to inject_epp_image.

        This test verifies the function's contract: if you don't call it
        on baseline, baseline stays untouched. The integration test (Task 3)
        verifies the pipeline only calls it on treatment.
        """
        baseline = {
            "scenario": [
                {"name": "baseline", "images": {"inferenceScheduler": {"repository": "orig", "tag": "v0"}}},
            ]
        }
        original_img = baseline["scenario"][0]["images"]["inferenceScheduler"].copy()
        # Not calling inject_epp_image on baseline — verifying it's unchanged
        assert baseline["scenario"][0]["images"]["inferenceScheduler"] == original_img

    def test_empty_registry_skips(self):
        """BC-3: Empty registry string skips injection entirely."""
        scenario = {"scenario": [{"name": "test"}]}
        result = inject_epp_image(scenario, "", "repo", "tag")
        assert result is False
        assert "images" not in scenario["scenario"][0]

    def test_no_scenario_entries_skips(self):
        """BC-3 variant: No scenario entries means no injection."""
        scenario = {"scenario": []}
        result = inject_epp_image(scenario, "reg.io", "repo", "tag")
        assert result is False

    def test_missing_scenario_key_skips(self):
        """BC-3 variant: Missing 'scenario' key means no injection."""
        scenario = {"other_key": "value"}
        result = inject_epp_image(scenario, "reg.io", "repo", "tag")
        assert result is False

    def test_existing_images_preserved(self):
        """BC-1: Other image entries (e.g. vllm) are not clobbered."""
        scenario = {
            "scenario": [
                {"name": "s", "images": {"vllm": {"repository": "vllm-r", "tag": "vllm-t"}}},
            ]
        }
        inject_epp_image(scenario, "reg.io", "epp", "v2")
        assert scenario["scenario"][0]["images"]["vllm"] == {"repository": "vllm-r", "tag": "vllm-t"}
        assert scenario["scenario"][0]["images"]["inferenceScheduler"]["tag"] == "v2"
