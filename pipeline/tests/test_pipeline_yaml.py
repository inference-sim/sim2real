"""Tests for pipeline.yaml structural correctness."""
import yaml
from pathlib import Path

PIPELINE_YAML = Path(__file__).resolve().parents[2] / "pipeline" / "pipeline.yaml"


class TestCollectResultsParams:
    """collect-results task invocation uses correct params."""

    def _get_collect_results_task(self):
        pipeline = yaml.safe_load(PIPELINE_YAML.read_text())
        tasks = pipeline["spec"]["tasks"]
        cr = [t for t in tasks if t["name"] == "collect-results"]
        assert len(cr) == 1, "Expected exactly one collect-results task"
        return cr[0]

    def test_no_model_label_param(self):
        """modelLabel should not be passed to collect-results."""
        task = self._get_collect_results_task()
        param_names = [p["name"] for p in task["params"]]
        assert "modelLabel" not in param_names

    def test_has_namespace_param(self):
        """collect-results must receive a namespace param."""
        task = self._get_collect_results_task()
        param_names = [p["name"] for p in task["params"]]
        assert "namespace" in param_names

    def test_has_results_dir_param(self):
        """collect-results must receive a resultsDir param."""
        task = self._get_collect_results_task()
        param_names = [p["name"] for p in task["params"]]
        assert "resultsDir" in param_names


class TestStreamEppLogsTask:
    """stream-epp-logs task invocation in pipeline.yaml."""

    def _get_stream_epp_logs_task(self):
        pipeline = yaml.safe_load(PIPELINE_YAML.read_text())
        tasks = pipeline["spec"]["tasks"]
        s = [t for t in tasks if t["name"] == "stream-epp-logs"]
        assert len(s) == 1, "Expected exactly one stream-epp-logs task"
        return s[0]

    def test_task_present(self):
        """stream-epp-logs must be wired into the pipeline."""
        self._get_stream_epp_logs_task()

    def test_task_ref(self):
        """taskRef points at the stream-epp-logs Task resource."""
        task = self._get_stream_epp_logs_task()
        assert task["taskRef"]["name"] == "stream-epp-logs"

    def test_runs_after_standup(self):
        """Streamer starts as soon as the model stack is up — in parallel with the workload."""
        task = self._get_stream_epp_logs_task()
        assert task["runAfter"] == ["llmdbenchmark-standup"], (
            "Streamer must start after standup (when EPP pods exist) and "
            "in parallel with the workload, not after it."
        )

    def test_data_workspace_bound(self):
        """data workspace must be bound to data-storage so logs land on the shared PVC."""
        task = self._get_stream_epp_logs_task()
        ws = {w["name"]: w["workspace"] for w in task["workspaces"]}
        assert ws.get("data") == "data-storage"

    def test_has_namespace_param(self):
        """stream-epp-logs must receive the namespace param."""
        task = self._get_stream_epp_logs_task()
        param_names = [p["name"] for p in task["params"]]
        assert "namespace" in param_names

    def test_has_results_dir_param(self):
        """stream-epp-logs must receive the resultsDir param so its output sits next to the workload's."""
        task = self._get_stream_epp_logs_task()
        param_names = [p["name"] for p in task["params"]]
        assert "resultsDir" in param_names

    def test_results_dir_matches_workload(self):
        """The streamer's resultsDir must be identical to the workload's, so they write into the same per-workload subdir."""
        pipeline = yaml.safe_load(PIPELINE_YAML.read_text())
        tasks = pipeline["spec"]["tasks"]

        s = next(t for t in tasks if t["name"] == "stream-epp-logs")
        w = next(t for t in tasks if t["name"] == "run-workload-blis-observe-binary")

        s_rd = next(p["value"] for p in s["params"] if p["name"] == "resultsDir")
        w_rd = next(p["value"] for p in w["params"] if p["name"] == "resultsDir")
        assert s_rd == w_rd, f"resultsDir mismatch: streamer={s_rd!r} workload={w_rd!r}"
