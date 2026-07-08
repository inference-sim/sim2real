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
        run_after = task["runAfter"]
        assert "llmdbenchmark-standup" in run_after, (
            "Streamer must start after standup (when EPP pods exist) and "
            "in parallel with the workload, not after it."
        )
        assert "run-workload-blis-observe-binary" not in run_after, (
            "Streamer must run in parallel with the workload, not after it."
        )

    def test_runs_after_prepare_results_dir(self):
        """Streamer must wait for prepare-results-dir so RESULTS_DIR exists before it writes."""
        task = self._get_stream_epp_logs_task()
        assert "prepare-results-dir" in task["runAfter"]

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


class TestStreamGpuStatsTask:
    """stream-gpu-stats task invocation in pipeline.yaml."""

    def _get_stream_gpu_stats_task(self):
        pipeline = yaml.safe_load(PIPELINE_YAML.read_text())
        tasks = pipeline["spec"]["tasks"]
        s = [t for t in tasks if t["name"] == "stream-gpu-stats"]
        assert len(s) == 1, "Expected exactly one stream-gpu-stats task"
        return s[0]

    def test_task_present(self):
        """stream-gpu-stats must be wired into the pipeline."""
        self._get_stream_gpu_stats_task()

    def test_task_ref(self):
        """taskRef points at the stream-gpu-stats Task resource."""
        task = self._get_stream_gpu_stats_task()
        assert task["taskRef"]["name"] == "stream-gpu-stats"

    def test_runs_after_standup(self):
        """Streamer starts as soon as the model stack is up — in parallel with the workload."""
        task = self._get_stream_gpu_stats_task()
        run_after = task["runAfter"]
        assert "llmdbenchmark-standup" in run_after, (
            "Streamer must start after standup (when vLLM pods exist) and "
            "in parallel with the workload, not after it."
        )
        assert "run-workload-blis-observe-binary" not in run_after, (
            "Streamer must run in parallel with the workload, not after it."
        )

    def test_runs_after_prepare_results_dir(self):
        """Streamer must wait for prepare-results-dir so RESULTS_DIR exists before it writes."""
        task = self._get_stream_gpu_stats_task()
        assert "prepare-results-dir" in task["runAfter"]

    def test_data_workspace_bound(self):
        """data workspace must be bound to data-storage so logs land on the shared PVC."""
        task = self._get_stream_gpu_stats_task()
        ws = {w["name"]: w["workspace"] for w in task["workspaces"]}
        assert ws.get("data") == "data-storage"

    def test_has_namespace_param(self):
        """stream-gpu-stats must receive the namespace param."""
        task = self._get_stream_gpu_stats_task()
        param_names = [p["name"] for p in task["params"]]
        assert "namespace" in param_names

    def test_has_results_dir_param(self):
        """stream-gpu-stats must receive the resultsDir param so its output sits next to the workload's."""
        task = self._get_stream_gpu_stats_task()
        param_names = [p["name"] for p in task["params"]]
        assert "resultsDir" in param_names

    def test_results_dir_matches_workload(self):
        """The streamer's resultsDir must be identical to the workload's, so they write into the same per-workload subdir."""
        pipeline = yaml.safe_load(PIPELINE_YAML.read_text())
        tasks = pipeline["spec"]["tasks"]

        s = next(t for t in tasks if t["name"] == "stream-gpu-stats")
        w = next(t for t in tasks if t["name"] == "run-workload-blis-observe-binary")

        s_rd = next(p["value"] for p in s["params"] if p["name"] == "resultsDir")
        w_rd = next(p["value"] for p in w["params"] if p["name"] == "resultsDir")
        assert s_rd == w_rd, f"resultsDir mismatch: streamer={s_rd!r} workload={w_rd!r}"


class TestPrepareResultsDirTask:
    """prepare-results-dir is the single owner of RESULTS_DIR creation; every writer must runAfter it."""

    WRITERS = [
        "stream-epp-logs",
        "stream-gpu-stats",
        "run-workload-blis-observe-binary",
        "collect-results",
    ]

    def _pipeline(self):
        return yaml.safe_load(PIPELINE_YAML.read_text())

    def _get(self, name):
        tasks = self._pipeline()["spec"]["tasks"]
        matches = [t for t in tasks if t["name"] == name]
        assert len(matches) == 1, f"Expected exactly one {name} task"
        return matches[0]

    def test_task_present(self):
        """prepare-results-dir must be wired into the pipeline."""
        self._get("prepare-results-dir")

    def test_task_ref(self):
        """taskRef points at the prepare-results-dir Task resource."""
        assert self._get("prepare-results-dir")["taskRef"]["name"] == "prepare-results-dir"

    def test_data_workspace_bound(self):
        """data workspace must be bound to data-storage so the wipe targets the shared PVC."""
        ws = {w["name"]: w["workspace"] for w in self._get("prepare-results-dir")["workspaces"]}
        assert ws.get("data") == "data-storage"

    def test_has_results_dir_param(self):
        """prepare-results-dir must receive resultsDir to know what to wipe."""
        params = [p["name"] for p in self._get("prepare-results-dir")["params"]]
        assert "resultsDir" in params

    def test_results_dir_matches_workload(self):
        """prepare-results-dir's resultsDir must match every writer's resultsDir verbatim."""
        prep_rd = next(p["value"] for p in self._get("prepare-results-dir")["params"]
                       if p["name"] == "resultsDir")
        for w in self.WRITERS:
            w_rd = next(p["value"] for p in self._get(w)["params"]
                        if p["name"] == "resultsDir")
            assert prep_rd == w_rd, f"resultsDir mismatch: prepare={prep_rd!r} {w}={w_rd!r}"

    def test_every_writer_runs_after_prepare(self):
        """Every task that writes inside RESULTS_DIR must list prepare-results-dir in runAfter, directly or transitively. Direct edges keep the contract self-documenting and survive future re-ordering of upstream dependencies."""
        tasks_by_name = {t["name"]: t for t in self._pipeline()["spec"]["tasks"]}

        def transitively_after(task_name, target):
            """Walk runAfter edges; return True if target reachable."""
            seen = set()
            frontier = list(tasks_by_name[task_name].get("runAfter", []))
            while frontier:
                n = frontier.pop()
                if n in seen:
                    continue
                seen.add(n)
                if n == target:
                    return True
                frontier.extend(tasks_by_name.get(n, {}).get("runAfter", []))
            return False

        for w in self.WRITERS:
            assert transitively_after(w, "prepare-results-dir"), (
                f"{w} must runAfter prepare-results-dir (directly or transitively)"
            )

    def test_prepare_runs_first(self):
        """prepare-results-dir itself must not have any runAfter — it owns the RESULTS_DIR creation invariant and must run before every writer."""
        prep = self._get("prepare-results-dir")
        assert "runAfter" not in prep or not prep["runAfter"], (
            "prepare-results-dir must have no runAfter so it runs as early as possible "
            "in the DAG and the wipe-and-create invariant holds before any writer starts."
        )


class TestReplicaParamThreading:
    """pipeline.yaml declares and threads the replica param through every
    resultsDir writer, per step-5 (issue #511).
    """

    def _pipeline(self):
        return yaml.safe_load(PIPELINE_YAML.read_text())

    def test_declares_replica_param(self):
        """Pipeline must declare a top-level 'replica' param, string, default '1'."""
        pipeline = self._pipeline()
        params = {p["name"]: p for p in pipeline["spec"]["params"]}
        assert "replica" in params, "pipeline.yaml missing 'replica' param declaration"
        assert params["replica"].get("type", "string") == "string"
        assert params["replica"].get("default") == "1"

    def test_every_results_dir_matches_canonical_template(self):
        """Every resultsDir occurrence in pipeline.yaml must equal
        tekton.RESULTS_DIR_TEMPLATE. This is the grep-audit-in-code that
        catches a task quietly writing to the wrong (unversioned) path."""
        from pipeline.lib.tekton import RESULTS_DIR_TEMPLATE
        pipeline = self._pipeline()
        for task in pipeline["spec"]["tasks"]:
            for param in task.get("params", []):
                if param["name"] == "resultsDir":
                    assert param["value"] == RESULTS_DIR_TEMPLATE, (
                        f"task {task['name']}.resultsDir = {param['value']!r} "
                        f"but canonical is {RESULTS_DIR_TEMPLATE!r}"
                    )

    def test_results_dir_includes_replica_segment(self):
        """Every resultsDir must reference $(params.replica) — a bare
        assertion complementary to the template match, so a future edit
        that changes the template shape without dropping the replica
        segment still passes; a change that drops the segment fails
        with a clearer message."""
        pipeline = self._pipeline()
        for task in pipeline["spec"]["tasks"]:
            for param in task.get("params", []):
                if param["name"] == "resultsDir":
                    assert "$(params.replica)" in param["value"], (
                        f"task {task['name']}.resultsDir does not thread replica"
                    )
