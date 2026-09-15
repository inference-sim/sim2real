"""Tekton PipelineRun generation for sim2real."""
import yaml

from pipeline.lib import corpus_schema, observe_argv

_SPEC_BASE_DIR = "/workspace/source/llm-d-benchmark"
_SCENARIO_FILE_PATH = "/tmp/llmdbench-config/scenario.yaml"

# Per-pipelineTask timeout overrides. These ride on top of the pipeline-level
# 4h ceiling and catch a stuck task earlier so the slot frees up. Values must
# stay below spec.timeouts.pipeline below; Tekton rejects taskRunSpecs entries
# whose timeout exceeds the enclosing pipeline timeout.
_TASK_TIMEOUTS: dict[str, str] = {
    "stream-epp-logs": "3h",
    "stream-gpu-stats": "3h",
    "stream-metrics": "3h",
    "run-workload-blis-observe-binary": "3h",
    # Runs parallel to standup and gates the 3h observe task, so it has well
    # under 1h of the 4h budget. A corpus build that needs longer wants a
    # pre-seeded PVC, not a bigger bound (#902).
    "prepare-trace": "30m",
}


# Canonical shape of resultsDir in pipeline.yaml. Every task that writes into
# resultsDir threads this exact template. build_results_dir() renders it with
# concrete values for callers that construct the path locally (e.g. tests).
# Kept in one place so pipeline.yaml drift is caught by test_pipeline_yaml.py.
RESULTS_DIR_TEMPLATE = (
    "$(params.runName)/$(params.phase)/$(params.workloadName)/i$(params.replica)"
)


def build_results_dir(run: str, phase: str, workload: str, replica) -> str:
    """Return the canonical resultsDir path for a (run, phase, workload, replica)
    tuple. Callers supply either concrete strings/ints or Tekton param
    references — both round-trip through the same template.
    """
    return f"{run}/{phase}/{workload}/i{replica}"


_DNS_SUBDOMAIN_MAX = 253


def validate_pipelinerun_name(name: str) -> None:
    """Raise ValueError if ``name`` exceeds the RFC 1123 DNS subdomain limit
    (253 chars). PipelineRun.metadata.name is a DNS subdomain, so Tekton
    rejects longer names at admission. Called at construction time so
    assemble surfaces the failure before any dispatch attempt.
    """
    if len(name) > _DNS_SUBDOMAIN_MAX:
        raise ValueError(
            f"PipelineRun name {name!r} is {len(name)} chars, exceeds the "
            f"{_DNS_SUBDOMAIN_MAX}-char DNS subdomain limit"
        )


def _default_spec_content(base_dir: str = _SPEC_BASE_DIR,
                          scenario_file: str = _SCENARIO_FILE_PATH) -> str:
    """Return the llmdbenchmark spec content string with PVC paths."""
    return (
        f"base_dir: {base_dir}\n"
        f"\n"
        f"values_file:\n"
        f"  path: {base_dir}/config/templates/values/defaults.yaml\n"
        f"\n"
        f"template_dir:\n"
        f"  path: {base_dir}/config/templates/jinja\n"
        f"\n"
        f"scenario_file:\n"
        f"  path: {scenario_file}\n"
    )


def _apply_workspace_bindings(ws_names: list, bindings: dict) -> list:
    """Map workspace names to their PVC/secret bindings.

    Falls back to a PVC claim named after the workspace for any unmapped name.
    """
    return [
        {"name": name, **bindings.get(name, {"persistentVolumeClaim": {"claimName": name}})}
        for name in ws_names
    ]


def _render_corpus_params(corpus: dict) -> list[dict]:
    """Render the ``corpus:`` sections as ``prepare-trace`` PipelineRun params.

    Driven entirely by ``corpus_schema.CORPUS_FIELDS``, so a field cannot be
    legal in the schema yet reach no param (issue #901's invariant). Order
    follows the table's insertion order, which keeps generated PipelineRun YAML
    diffable.

    ``replay:`` is deliberately NOT rendered here. Issue #900 collapsed
    ``concurrentSessions``/``totalSessions`` into the rendered observe argv, so
    those params no longer exist; the replay fields are applied by
    ``observe_argv.render_observe_argv`` as ``--concurrent-sessions`` /
    ``--total-sessions`` instead. #901's applied-or-rejected invariant still
    holds — the destination moved, not the guarantee.

    Callers must have validated the document first: this asserts rather than
    guesses when a required field is missing, so a skipped validation surfaces
    as a loud error instead of a param rendered from a sentinel.
    """
    out: list[dict] = []
    for section, fields in corpus_schema.CORPUS_FIELDS.items():
        written = corpus.get(section) or {}
        for key, field in fields.items():
            value = written.get(key, field.default)
            if value is corpus_schema.REQUIRED:
                raise KeyError(
                    f"corpus.{section}.{key} is required but absent — validate "
                    f"the document with corpus_schema.validate_corpus_document "
                    f"before rendering"
                )
            out.append({"name": field.param, "value": field.render(value)})
    return out


def is_trace_workload(workload: dict) -> bool:
    """Return True iff ``workload`` is a corpus (trace) workload.

    The discriminator is the presence of a non-empty top-level ``corpus:``
    mapping — see :mod:`pipeline.lib.corpus_schema`. Such a workload sources its
    request stream from a recorded corpus (prepare-trace + a replay pool) rather
    than from a generative WorkloadSpec. Anything else is generative and flows
    through the ``workloadSpec`` path unchanged.

    The legacy ``trace:`` mapping is NOT recognized (issue #901): it was
    replaced outright rather than dual-supported. A document still carrying it
    is refused by ``assemble_run._validate_workload`` rather than silently
    treated as generative.
    """
    return corpus_schema.is_corpus_document(workload)


def trace_path(corpus: dict) -> str:
    """Return the content-addressed corpus path ``traces/<sha12>``.

    The key covers the ``corpus:`` mapping ALONE. Two consequences, both
    deliberate (issue #901):

    * **No workload name.** The previous ``traces/<safe_wl_name>-<sha12>`` form
      made two cells with byte-identical corpus content resolve to different
      paths and build the identical corpus twice.
    * **No ``replay:``.** Session counts are consumed by ``blis observe`` at
      replay time and cannot change the corpus, so cells differing only in
      ``replay:`` share one cache entry by construction.

    See :func:`pipeline.lib.corpus_schema.corpus_cache_key` for the hash itself.
    """
    return f"traces/{corpus_schema.corpus_cache_key(corpus)}"


def make_pipelinerun_scenario(
    phase: str,
    workload: dict,
    run_name: str,
    namespace: str,
    pipeline_name: str,
    scenario_content: str,
    workspace_bindings: dict | None = None,
    spec_content: str | None = None,
    benchmark_git_commit: str = "",
    benchmark_git_repo_url: str = "",
    blis_git_commit: str = "",
    blis_git_repo_url: str = "",
    model: str = "",
    observe: dict | None = None,
    iteration: int = 1,
) -> dict:
    """Generate a PipelineRun with resolved scenario content."""
    if spec_content is None:
        spec_content = _default_spec_content()
    wl_name = workload.get("name", workload.get("workload_name", "unknown"))
    safe_name = wl_name.replace("_", "-")
    safe_phase = phase.replace("_", "-")
    pr_name = f"{safe_phase}-{safe_name}-{run_name}-i{iteration}"
    validate_pipelinerun_name(pr_name)

    # A trace workload emits a locked set of trace params (traceSpec/tracePath/
    # session counts) and an EMPTY workloadSpec — observe must NOT source
    # requests from a generative WorkloadSpec in trace mode. Generative
    # workloads keep the historical workloadSpec path byte-for-byte unchanged.
    trace_mode = is_trace_workload(workload)
    if trace_mode:
        corpus = workload["corpus"]
        wl_spec_str = ""
        # Only traceSpec's EMPTINESS is load-bearing: prepare-trace tests
        # `[ -z "$(params.traceSpec)" ]` to recognize a generative workload and
        # skip the corpus build. Nothing parses the content, so this carries the
        # corpus mapping purely to keep the PipelineRun self-describing for an
        # operator reading it. (sim2real#900 collapses these params.)
        trace_spec_str = yaml.dump(corpus, default_flow_style=True).strip()
        t_path = trace_path(corpus)
        # Scalar projections of the corpus/replay document, generated from the
        # schema tables so the set of emitted params cannot drift from the set
        # of legal fields. The prepare-trace steps consume these directly, so no
        # in-container YAML parsing of the compact traceSpec is needed
        # (line-based sed on single-line flow YAML was fragile and could extract
        # an empty source → 404 on download).
        trace_scalars = _render_corpus_params(corpus)
    else:
        wl_spec = {k: v for k, v in workload.items() if k != "workload_name"}
        wl_spec_str = yaml.dump(wl_spec, default_flow_style=True).strip()
        t_path = ""

    # The whole `blis observe` command line, rendered here rather than
    # reassembled by the Task's shell from a dozen scalar params (issue #900).
    # `--server-url` is excluded: it is a runtime Task result (the standup task's
    # endpoint), so the Task appends it. resultsDir is rendered from the SAME
    # helper pipeline.yaml uses for the Task's `resultsDir` param, so the argv's
    # output paths cannot drift from the directory the Task chmods and lists.
    observe_args = observe_argv.render_observe_argv(
        workload=workload,
        observe=observe,
        model=model,
        results_dir=build_results_dir(run_name, phase, wl_name, iteration),
        trace_path=t_path,
    )

    params: list[dict] = [
        {"name": "experimentId",      "value": run_name},
        {"name": "runName",           "value": run_name},
        {"name": "namespace",         "value": namespace},
        {"name": "phase",             "value": phase},
        {"name": "scenarioContent",   "value": scenario_content},
        {"name": "specContent",       "value": spec_content},
        {"name": "workloadName",      "value": wl_name},
        {"name": "workloadSpec",      "value": wl_spec_str},
        {"name": "benchmarkGitRepoUrl", "value": benchmark_git_repo_url},
        {"name": "benchmarkGitCommit", "value": benchmark_git_commit},
        {"name": "blisGitRepoUrl",   "value": blis_git_repo_url},
        {"name": "blisGitCommit",     "value": blis_git_commit},
        {"name": "replica",          "value": str(iteration)},
        # The rendered observe command line. Replaces the nine scalar params the
        # Task used to reassemble (maxConcurrency, timeout, warmupRequests,
        # prewarmDuration, extraArgs, tracePath-as-observe-input,
        # concurrentSessions, totalSessions) plus `model`, which is now a flag
        # inside the argv rather than a param of its own (#900).
        {"name": "observeArgs",      "value": observe_args},
    ]
    if trace_mode:
        # Corpus-only params, adjacent to workloadSpec. Generative workloads
        # deliberately do NOT emit these so their param list stays identical
        # to prior releases. These feed `prepare-trace`, NOT observe — observe's
        # copy of the corpus path now lives inside observeArgs.
        params += [
            {"name": "traceSpec", "value": trace_spec_str},
            {"name": "tracePath", "value": t_path},
        ]
        params += trace_scalars

    spec: dict = {
        "pipelineRef": {"name": pipeline_name},
        "taskRunTemplate": {"serviceAccountName": "helm-installer"},
        "params": params,
        "timeouts": {"pipeline": "4h"},
        "taskRunSpecs": [
            {"pipelineTaskName": name, "timeout": dur}
            for name, dur in _TASK_TIMEOUTS.items()
        ],
    }

    if workspace_bindings is not None:
        ws_names = list(workspace_bindings.keys())
        spec["workspaces"] = _apply_workspace_bindings(ws_names, workspace_bindings)

    return {
        "apiVersion": "tekton.dev/v1",
        "kind": "PipelineRun",
        "metadata": {"name": pr_name, "namespace": namespace},
        "spec": spec,
    }


