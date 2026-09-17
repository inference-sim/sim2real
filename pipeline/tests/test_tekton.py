"""Tests for Tekton PipelineRun generation."""

import copy

import pytest
import yaml

from pipeline.lib.tekton import (
    is_trace_workload,
    make_pipelinerun_scenario,
    trace_path,
)


# ── Tests for make_pipelinerun_scenario ──────────────────────────────────────

_WORKSPACE_BINDINGS = {
    "data-storage":   {"persistentVolumeClaim": {"claimName": "data-pvc"}},
    "source":         {"persistentVolumeClaim": {"claimName": "source-pvc"}},
}

def test_make_pipelinerun_scenario_name():
    pr = make_pipelinerun_scenario(
        phase="baseline", workload={"name": "wl-smoke"}, run_name="ac",
        namespace="kalantar-0", pipeline_name="sim2real-ac",
        scenario_content="scenario: []",
        workspace_bindings=_WORKSPACE_BINDINGS,
    )
    assert pr["metadata"]["name"] == "baseline-wl-smoke-ac-i1"
    assert pr["metadata"]["namespace"] == "kalantar-0"


def test_make_pipelinerun_scenario_params():
    pr = make_pipelinerun_scenario(
        phase="treatment", workload={"name": "chatbot-mid"}, run_name="ac",
        namespace="ns", pipeline_name="sim2real-ac",
        scenario_content="scenario:\n- name: test\n",
        workspace_bindings=_WORKSPACE_BINDINGS,
    )
    params = {p["name"]: p["value"] for p in pr["spec"]["params"]}
    assert params["phase"] == "treatment"
    assert params["scenarioContent"] == "scenario:\n- name: test\n"
    assert params["workloadName"] == "chatbot-mid"
    assert "gaieConfig" not in params
    assert "inferenceObjectives" not in params


def test_make_pipelinerun_scenario_spec_content_default():
    pr = make_pipelinerun_scenario(
        phase="baseline", workload={"name": "wl"}, run_name="r",
        namespace="ns", pipeline_name="sim2real-r",
        scenario_content="{}",
        workspace_bindings=_WORKSPACE_BINDINGS,
    )
    params = {p["name"]: p["value"] for p in pr["spec"]["params"]}
    assert "specContent" in params
    spec = params["specContent"]
    assert "/workspace/source/llm-d-benchmark" in spec
    assert "/tmp/llmdbench-config/scenario.yaml" in spec
    assert "values_file:" in spec
    assert "template_dir:" in spec


def test_make_pipelinerun_scenario_spec_content_custom():
    from pipeline.lib.tekton import make_pipelinerun_scenario
    custom_spec = "base_dir: /custom\nscenario_file:\n  path: /custom/scenario.yaml\n"
    pr = make_pipelinerun_scenario(
        phase="baseline", workload={"name": "wl"}, run_name="r",
        namespace="ns", pipeline_name="sim2real-r",
        scenario_content="{}",
        workspace_bindings=_WORKSPACE_BINDINGS,
        spec_content=custom_spec,
    )
    params = {p["name"]: p["value"] for p in pr["spec"]["params"]}
    assert params["specContent"] == custom_spec


def test_make_pipelinerun_scenario_service_account():
    """PipelineRun pins taskRunTemplate.serviceAccountName so TaskRuns do not
    fall back to the namespace `default` SA."""
    pr = make_pipelinerun_scenario(
        phase="baseline", workload={"name": "wl"}, run_name="r",
        namespace="ns", pipeline_name="sim2real-r",
        scenario_content="{}",
        workspace_bindings=_WORKSPACE_BINDINGS,
    )
    assert pr["spec"]["taskRunTemplate"]["serviceAccountName"] == "helm-installer"


def test_make_pipelinerun_scenario_task_run_specs():
    """taskRunSpecs entries gate per-task timeouts on the long-running tasks."""
    from pipeline.lib.tekton import _TASK_TIMEOUTS
    pr = make_pipelinerun_scenario(
        phase="baseline", workload={"name": "wl"}, run_name="r",
        namespace="ns", pipeline_name="sim2real-r",
        scenario_content="{}",
        workspace_bindings=_WORKSPACE_BINDINGS,
    )
    specs = pr["spec"]["taskRunSpecs"]
    by_task = {s["pipelineTaskName"]: s["timeout"] for s in specs}
    assert by_task == _TASK_TIMEOUTS
    # The override must not collide with the global serviceAccountName or the
    # pipeline-level timeout — both stay where they are.
    assert pr["spec"]["taskRunTemplate"]["serviceAccountName"] == "helm-installer"
    assert pr["spec"]["timeouts"] == {"pipeline": "4h"}


def test_make_pipelinerun_scenario_workspace_bindings():
    pr = make_pipelinerun_scenario(
        phase="baseline", workload={"name": "wl"}, run_name="r",
        namespace="ns", pipeline_name="sim2real-r",
        scenario_content="{}",
        workspace_bindings=_WORKSPACE_BINDINGS,
    )
    ws_names = {ws["name"] for ws in pr["spec"]["workspaces"]}
    assert "source" in ws_names
    assert "data-storage" in ws_names
    assert "model-cache" not in ws_names
    assert "hf-credentials" not in ws_names


# ── Tests for phase name sanitization ─────────────────────────────────────────


def test_phase_name_in_pipelinerun():
    """Custom phase names appear in PipelineRun metadata and params."""
    pr = make_pipelinerun_scenario(
        phase="b1",
        workload={"name": "wl-smoke"},
        run_name="test-run",
        namespace="ns-0",
        pipeline_name="sim2real",
        scenario_content="scenario: []",
    )
    assert pr["metadata"]["name"] == "b1-wl-smoke-test-run-i1"
    params = {p["name"]: p["value"] for p in pr["spec"]["params"]}
    assert params["phase"] == "b1"


def test_phase_underscore_sanitized_in_name():
    """Underscores in phase names are converted to hyphens in PipelineRun name."""
    pr = make_pipelinerun_scenario(
        phase="my_phase",
        workload={"name": "wl-smoke"},
        run_name="test-run",
        namespace="ns-0",
        pipeline_name="sim2real",
        scenario_content="scenario: []",
    )
    assert pr["metadata"]["name"] == "my-phase-wl-smoke-test-run-i1"
    params = {p["name"]: p["value"] for p in pr["spec"]["params"]}
    assert params["phase"] == "my_phase"


def test_default_spec_content():
    """PipelineRun includes default spec content when none provided."""
    pr = make_pipelinerun_scenario(
        phase="baseline",
        workload={"name": "wl-smoke"},
        run_name="run-1",
        namespace="ns-0",
        pipeline_name="sim2real",
        scenario_content="scenario: []",
    )
    params = {p["name"]: p["value"] for p in pr["spec"]["params"]}
    assert "defaults.yaml" in params["specContent"]


def test_workspace_bindings():
    """Workspace bindings are applied when provided."""
    pr = make_pipelinerun_scenario(
        phase="baseline",
        workload={"name": "wl-smoke"},
        run_name="run-1",
        namespace="ns-0",
        pipeline_name="sim2real",
        scenario_content="scenario: []",
        workspace_bindings={"data-storage": {"persistentVolumeClaim": {"claimName": "my-pvc"}}},
    )
    assert "workspaces" in pr["spec"]
    ws = pr["spec"]["workspaces"]
    assert ws[0]["name"] == "data-storage"
    assert ws[0]["persistentVolumeClaim"]["claimName"] == "my-pvc"


# ── observe dict → PipelineRun params ─────────────────────────────────────────

_OBSERVE_KEYS = ("maxConcurrency", "timeout", "warmupRequests", "prewarmDuration", "extraArgs")


def _names(pr):
    return [p["name"] for p in pr["spec"]["params"]]


def test_observe_absent_emits_no_observe_params():
    """observe=None (or absent) leaves the PipelineRun param list at its base set."""
    pr = make_pipelinerun_scenario(
        phase="baseline", workload={"name": "wl", "clients": []}, run_name="r",
        namespace="ns", pipeline_name="sim2real-r",
        scenario_content="{}",
    )
    names = _names(pr)
    for k in _OBSERVE_KEYS:
        assert k not in names, f"absent observe leaked {k}"


def test_observe_empty_dict_emits_no_observe_params():
    """observe={} (the manifest default when section is absent) emits nothing."""
    pr = make_pipelinerun_scenario(
        phase="baseline", workload={"name": "wl", "clients": []}, run_name="r",
        namespace="ns", pipeline_name="sim2real-r",
        scenario_content="{}",
        observe={},
    )
    names = _names(pr)
    for k in _OBSERVE_KEYS:
        assert k not in names


def test_observe_values_land_in_observe_args_not_scalar_params():
    """#900: the five tuning scalars are no longer PipelineRun params — their
    values are rendered into observeArgs. Absence of the params is the contract
    (pipeline.yaml no longer declares them, so emitting one would be a Tekton
    admission error)."""
    pr = make_pipelinerun_scenario(
        phase="baseline", workload={"name": "wl", "clients": []}, run_name="r",
        namespace="ns", pipeline_name="sim2real-r", scenario_content="{}",
        observe={"timeout": 3600, "maxConcurrency": 5000},
    )
    params = {p["name"]: p["value"] for p in pr["spec"]["params"]}
    for k in _OBSERVE_KEYS:
        assert k not in params, f"{k} must no longer be a PipelineRun param"
    assert "--timeout 3600" in params["observeArgs"]
    assert "--max-concurrency 5000" in params["observeArgs"]


def test_observe_full_dict_lands_in_observe_args():
    pr = make_pipelinerun_scenario(
        phase="baseline", workload={"name": "wl", "clients": []}, run_name="r",
        namespace="ns", pipeline_name="sim2real-r", scenario_content="{}",
        observe={
            "maxConcurrency": 5000, "timeout": 3600, "warmupRequests": 25,
            "prewarmDuration": "30s", "extraArgs": "--foo bar",
        },
    )
    argv = {p["name"]: p["value"] for p in pr["spec"]["params"]}["observeArgs"]
    for frag in ("--max-concurrency 5000", "--timeout 3600",
                 "--warmup-requests 25", "--prewarm-duration 30s"):
        assert frag in argv
    assert argv.endswith("--foo bar")


def test_model_is_no_longer_a_pipelinerun_param():
    """#900 drops it: the renderer carries --model inside observeArgs, and
    pipeline.yaml no longer declares a top-level `model`."""
    pr = make_pipelinerun_scenario(
        phase="baseline", workload={"name": "wl", "clients": []}, run_name="r",
        namespace="ns", pipeline_name="sim2real-r", scenario_content="{}",
        model="qwen/qwen3-14b",
    )
    params = {p["name"]: p["value"] for p in pr["spec"]["params"]}
    assert "model" not in params
    assert "--model qwen/qwen3-14b" in params["observeArgs"]


# ── Tests for build_results_dir + RESULTS_DIR_TEMPLATE ──────────────────────

from pipeline.lib.tekton import build_results_dir, RESULTS_DIR_TEMPLATE


def test_build_results_dir_returns_slash_joined_path():
    assert build_results_dir("run1", "baseline", "chatbot-mid", 1) == "run1/baseline/chatbot-mid/i1"


def test_build_results_dir_accepts_int_replica():
    assert build_results_dir("r", "p", "w", 3) == "r/p/w/i3"


def test_build_results_dir_accepts_str_replica():
    """String replica must produce the same output — used for pipeline.yaml templating."""
    assert build_results_dir("r", "p", "w", "3") == "r/p/w/i3"


def test_results_dir_template_shape():
    """The pipeline.yaml template must be exactly the shape build_results_dir
    would produce if each segment were a Tekton param reference."""
    assert RESULTS_DIR_TEMPLATE == "$(params.runName)/$(params.phase)/$(params.workloadName)/i$(params.replica)"


def test_build_results_dir_matches_template_when_params_substituted():
    """build_results_dir with Tekton-param strings reproduces the template
    verbatim. This is the invariant test_pipeline_yaml.py will assert against
    every resultsDir value in pipeline.yaml."""
    template = build_results_dir(
        "$(params.runName)", "$(params.phase)",
        "$(params.workloadName)", "$(params.replica)",
    )
    assert template == RESULTS_DIR_TEMPLATE


# ── Tests for validate_pipelinerun_name ─────────────────────────────────────

import pytest as _pytest
from pipeline.lib.tekton import validate_pipelinerun_name


def test_validate_pipelinerun_name_accepts_short():
    validate_pipelinerun_name("baseline-wl-r-i1")  # no raise


def test_validate_pipelinerun_name_accepts_253_char_limit():
    """Exactly 253 chars is the DNS subdomain limit — must pass."""
    name = "a" * 253
    validate_pipelinerun_name(name)  # no raise


def test_validate_pipelinerun_name_rejects_254_chars():
    name = "a" * 254
    with _pytest.raises(ValueError, match="253"):
        validate_pipelinerun_name(name)


def test_make_pipelinerun_scenario_rejects_oversized_name():
    """Long run_name or workload should trip the validator at construction."""
    long_run = "r" * 240
    with _pytest.raises(ValueError, match="253"):
        make_pipelinerun_scenario(
            phase="baseline", workload={"name": "wl"}, run_name=long_run,
            namespace="ns", pipeline_name="sim2real",
            scenario_content="scenario: []",
        )


# ── Tests for the replica PipelineRun param ─────────────────────────────────


def test_make_pipelinerun_scenario_emits_replica_param_default():
    """Default iteration=1 → replica='1' in params (string, per Tekton API)."""
    pr = make_pipelinerun_scenario(
        phase="baseline", workload={"name": "wl"}, run_name="r",
        namespace="ns", pipeline_name="sim2real",
        scenario_content="scenario: []",
    )
    params = {p["name"]: p["value"] for p in pr["spec"]["params"]}
    assert params["replica"] == "1"
    assert isinstance(params["replica"], str)


def test_make_pipelinerun_scenario_emits_replica_param_explicit():
    """iteration=5 → replica='5'."""
    pr = make_pipelinerun_scenario(
        phase="baseline", workload={"name": "wl"}, run_name="r",
        namespace="ns", pipeline_name="sim2real",
        scenario_content="scenario: []",
        iteration=5,
    )
    params = {p["name"]: p["value"] for p in pr["spec"]["params"]}
    assert params["replica"] == "5"


# ── Tests for corpus (trace) workloads ──────────────────────────────────────

_TRACE_WORKLOAD = {
    "corpus": {
        "upstream": {"source": "hf:Exgentic/agent-llm-traces", "shards": 39},
        "select": {"min_rounds": 2},
        # max_think_time is REQUIRED (#905) — rendering asserts rather than
        # guesses when a required field is absent.
        "reconstruct": {"context_growth": "accumulate",
                        "max_think_time": "60s"},
    },
    "replay": {"concurrent_sessions": 128, "total_sessions": 192},
}


def _trace_params(workload: dict) -> dict:
    pr = make_pipelinerun_scenario(
        phase="baseline", workload=workload, run_name="r", namespace="ns",
        pipeline_name="sim2real", scenario_content="{}",
    )
    return {p["name"]: p["value"] for p in pr["spec"]["params"]}


def test_is_trace_workload_detects_corpus_block():
    assert is_trace_workload(_TRACE_WORKLOAD) is True


def test_is_trace_workload_false_for_generative():
    assert is_trace_workload({"name": "wl", "clients": [], "version": 1}) is False


def test_is_trace_workload_false_for_empty_corpus():
    assert is_trace_workload({"name": "wl", "corpus": {}}) is False
    assert is_trace_workload({"name": "wl"}) is False


def test_is_trace_workload_false_for_legacy_trace_block():
    """`trace:` is replaced, not dual-supported (#901). A legacy document is NOT
    a trace workload — assemble refuses it instead of guessing."""
    legacy = {"name": "wl", "trace": {"source": "hf:o/d",
                                      "pool": {"concurrent_sessions": 1,
                                               "total_sessions": 0}}}
    assert is_trace_workload(legacy) is False


# ── content-addressed corpus key ────────────────────────────────────────────


def test_trace_path_is_content_addressed_without_the_workload_name():
    p = trace_path(_TRACE_WORKLOAD["corpus"])
    assert p.startswith("traces/")
    sha = p.removeprefix("traces/")
    assert len(sha) == 12
    assert all(c in "0123456789abcdef" for c in sha)
    assert "exgentic" not in p.lower()


def test_identical_corpus_different_replay_shares_one_build():
    """AC1: two cells differing only in replay: resolve to the SAME path."""
    other = copy.deepcopy(_TRACE_WORKLOAD)
    other["replay"] = {"concurrent_sessions": 4, "total_sessions": 8}
    assert (_trace_params(_TRACE_WORKLOAD)["tracePath"]
            == _trace_params(other)["tracePath"])


def test_identical_corpus_different_workload_name_shares_one_build():
    """#901 defect 3: the name-keyed path built the identical corpus twice."""
    a = dict(_TRACE_WORKLOAD, workload_name="cell_a")
    b = dict(_TRACE_WORKLOAD, workload_name="cell_b")
    assert _trace_params(a)["tracePath"] == _trace_params(b)["tracePath"]


def test_trace_path_deterministic_and_order_independent():
    a = trace_path(_TRACE_WORKLOAD["corpus"])
    b = trace_path({
        "reconstruct": {"max_think_time": "60s", "context_growth": "accumulate"},
        "select": {"min_rounds": 2},
        "upstream": {"shards": 39, "source": "hf:Exgentic/agent-llm-traces"},
    })
    assert a == b


def test_trace_path_changes_when_corpus_content_changes():
    changed = copy.deepcopy(_TRACE_WORKLOAD["corpus"])
    changed["upstream"]["source"] = "hf:Other/dataset"
    assert trace_path(changed) != trace_path(_TRACE_WORKLOAD["corpus"])


def test_trace_path_deterministic_across_calls():
    assert (_trace_params(_TRACE_WORKLOAD)["tracePath"]
            == _trace_params(_TRACE_WORKLOAD)["tracePath"])


# ── param emission ──────────────────────────────────────────────────────────


def test_trace_workload_emits_locked_params():
    params = _trace_params(_TRACE_WORKLOAD)
    # workloadSpec is empty in trace mode — observe must not use --workload-spec.
    assert params["workloadSpec"] == ""
    # traceSpec carries the corpus mapping. Only its EMPTINESS is load-bearing:
    # prepare-trace tests `[ -z "$(params.traceSpec)" ]` to detect a generative
    # workload and never parses the content.
    assert params["traceSpec"] == yaml.dump(
        _TRACE_WORKLOAD["corpus"], default_flow_style=True
    ).strip()
    assert params["tracePath"] == trace_path(_TRACE_WORKLOAD["corpus"])
    # #900: the session counts are no longer params — they are argv flags.
    assert "concurrentSessions" not in params
    assert "totalSessions" not in params
    assert "--concurrent-sessions 128" in params["observeArgs"]
    assert "--total-sessions 192" in params["observeArgs"]
    # Scalar projections the prepare-trace steps read directly.
    assert params["traceSource"] == "hf:Exgentic/agent-llm-traces"
    assert params["traceShards"] == "39"
    assert params["traceMinRounds"] == "2"
    assert params["traceSplit"] == "test"
    assert params["traceContextGrowth"] == "accumulate"
    assert params["traceDedupByConversation"] == "1"
    assert params["traceShuffleSeed"] == "42"


def test_select_block_overrides_dedup_and_seed():
    wl = {
        "corpus": {"upstream": {"source": "hf:Org/dataset"},
                   "reconstruct": {"max_think_time": "60s"},
                   "select": {"dedup_by_conversation": False, "shuffle_seed": 7}},
        "replay": {"concurrent_sessions": 4, "total_sessions": 8},
    }
    params = _trace_params(wl)
    assert params["traceDedupByConversation"] == "0"
    assert params["traceShuffleSeed"] == "7"


def test_partition_maps_to_trace_split():
    wl = {
        "corpus": {"upstream": {"source": "hf:Org/dataset"},
                   "reconstruct": {"max_think_time": "60s"},
                   "select": {"partition": "train"}},
        "replay": {"concurrent_sessions": 1, "total_sessions": 0},
    }
    assert _trace_params(wl)["traceSplit"] == "train"


def test_minimal_corpus_uses_documented_defaults():
    """When the document omits every optional field, the scalar params fall
    back to the documented defaults (39 / test / 2 / accumulate / 1 / 42), plus
    #905's two optional additions: no revision (upstream default branch) and
    otel-parquet. max_think_time has no default and so is written here."""
    wl = {
        "corpus": {"upstream": {"source": "hf:Org/dataset"},
                   "reconstruct": {"max_think_time": "60s"}},
        "replay": {"concurrent_sessions": 4, "total_sessions": 8},
    }
    params = _trace_params(wl)
    assert params["traceSource"] == "hf:Org/dataset"
    assert params["traceShards"] == "39"
    assert params["traceMinRounds"] == "2"
    assert params["traceSplit"] == "test"
    assert params["traceContextGrowth"] == "accumulate"
    assert params["traceDedupByConversation"] == "1"
    assert params["traceShuffleSeed"] == "42"
    assert params["traceRevision"] == ""
    assert params["traceFormat"] == "otel-parquet"
    assert params["traceMaxThinkTime"] == "60s"


def test_rendering_without_validation_raises_rather_than_emitting_a_sentinel():
    """A skipped validation must fail loudly, not render a param from the
    REQUIRED sentinel."""
    wl = {"corpus": {"select": {"min_rounds": 2}},
          "replay": {"concurrent_sessions": 1, "total_sessions": 0}}
    with pytest.raises(KeyError, match="corpus.upstream.source"):
        _trace_params(wl)


def test_rendering_without_replay_raises():
    """Now raised by the argv renderer rather than the param renderer: #900
    moved the replay fields from PipelineRun params into observeArgs."""
    from pipeline.lib.observe_argv import ObserveArgvError
    # max_think_time is present so the corpus params render cleanly and the
    # MISSING REPLAY is what fails — corpus params render first, so omitting it
    # here would make this test pass for the wrong reason.
    wl = {"corpus": {"upstream": {"source": "hf:o/d"},
                     "reconstruct": {"max_think_time": "60s"}}}
    with pytest.raises(ObserveArgvError, match="replay.concurrent_sessions"):
        _trace_params(wl)


def test_generative_workload_unchanged_param_set():
    """AC5: generative workloads emit a non-empty workloadSpec and NO corpus
    params, so their PipelineRun is byte-identical to prior releases."""
    pr = make_pipelinerun_scenario(
        phase="baseline", workload={"name": "wl-a", "num_requests": 10},
        run_name="r", namespace="ns", pipeline_name="sim2real",
        scenario_content="{}",
    )
    params = {p["name"]: p["value"] for p in pr["spec"]["params"]}
    assert params["workloadSpec"] != ""
    names = _names(pr)
    for k in ("traceSpec", "tracePath", "concurrentSessions", "totalSessions",
              "traceSource", "traceShards", "traceMinRounds", "traceSplit",
              "traceContextGrowth", "traceDedupByConversation", "traceShuffleSeed"):
        assert k not in names
