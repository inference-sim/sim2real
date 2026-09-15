"""Tests for the corpus/replay workload document schema (issue #901)."""
import copy
import pathlib

import pytest
import yaml as _yaml

from pipeline.lib import corpus_schema, layout
from pipeline.lib.errors import AssembleError
from pipeline.lib.tekton import make_pipelinerun_scenario, trace_path

_VALID = {
    "corpus": {
        "upstream": {"source": "hf:Exgentic/agent-llm-traces", "shards": 39},
        "select": {"partition": "test", "min_rounds": 2,
                   "dedup_by_conversation": True, "shuffle_seed": 42},
        "reconstruct": {"context_growth": "accumulate"},
    },
    "replay": {"concurrent_sessions": 128, "total_sessions": 192},
}


def _doc(**over):
    d = copy.deepcopy(_VALID)
    d.update(over)
    return d


def _params(doc: dict) -> dict[str, str]:
    pr = make_pipelinerun_scenario(
        phase="baseline", workload=doc, run_name="r", namespace="ns",
        pipeline_name="sim2real", scenario_content="{}",
    )
    return {p["name"]: p["value"] for p in pr["spec"]["params"]}


# ── discriminator ────────────────────────────────────────────────────────


def test_is_corpus_document_on_corpus_key():
    assert corpus_schema.is_corpus_document(_VALID) is True
    assert corpus_schema.is_corpus_document({"corpus": {}}) is False
    assert corpus_schema.is_corpus_document({"clients": [], "version": 1}) is False
    assert corpus_schema.is_corpus_document({}) is False


def test_is_corpus_document_false_for_non_mapping_corpus():
    assert corpus_schema.is_corpus_document({"corpus": "hf:o/d"}) is False


# ── acceptance ───────────────────────────────────────────────────────────


def test_minimal_document_validates():
    corpus_schema.validate_corpus_document(
        {"corpus": {"upstream": {"source": "hf:o/d"}},
         "replay": {"concurrent_sessions": 1, "total_sessions": 0}},
        "w.yaml",
    )


def test_full_document_validates():
    corpus_schema.validate_corpus_document(_VALID, "w.yaml")


def test_injected_workload_name_is_tolerated():
    """_load_workload injects workload_name from the stem BEFORE validating."""
    corpus_schema.validate_corpus_document(_doc(workload_name="wl_chat"), "w.yaml")


# ── strict rejection: top level ──────────────────────────────────────────


@pytest.mark.parametrize("bad_top", ["name", "version", "trace", "blis_observe"])
def test_unknown_top_level_key_rejected(bad_top):
    with pytest.raises(AssembleError, match=bad_top):
        corpus_schema.validate_corpus_document(_doc(**{bad_top: "x"}), "w.yaml")


@pytest.mark.parametrize("marker", ["clients", "cohorts"])
def test_corpus_beside_generative_marker_names_both_kinds(marker):
    with pytest.raises(AssembleError, match="exactly one kind"):
        corpus_schema.validate_corpus_document(_doc(**{marker: []}), "w.yaml")


def test_non_mapping_corpus_rejected():
    with pytest.raises(AssembleError, match="non-empty mapping"):
        corpus_schema.validate_corpus_document(
            {"corpus": {}, "replay": {"concurrent_sessions": 1,
                                      "total_sessions": 0}},
            "w.yaml",
        )


# ── strict rejection: inside corpus / replay ─────────────────────────────


@pytest.mark.parametrize("section", ["upstream", "select", "reconstruct"])
def test_unknown_key_in_each_corpus_section_rejected(section):
    d = _doc()
    d["corpus"][section]["nope"] = 1
    with pytest.raises(AssembleError, match="nope"):
        corpus_schema.validate_corpus_document(d, "w.yaml")


def test_unknown_corpus_section_rejected():
    d = _doc()
    d["corpus"]["bogus"] = {}
    with pytest.raises(AssembleError, match="bogus"):
        corpus_schema.validate_corpus_document(d, "w.yaml")


def test_unknown_replay_key_rejected():
    """#901 defect 1: replay.warmup_requests used to ride along silently."""
    d = _doc()
    d["replay"]["warmup_requests"] = 0
    with pytest.raises(AssembleError, match="warmup_requests"):
        corpus_schema.validate_corpus_document(d, "w.yaml")


@pytest.mark.parametrize("section", ["upstream", "select", "reconstruct"])
def test_non_mapping_section_rejected(section):
    d = _doc()
    d["corpus"][section] = ["not", "a", "map"]
    with pytest.raises(AssembleError, match=section):
        corpus_schema.validate_corpus_document(d, "w.yaml")


# ── deferred fields ──────────────────────────────────────────────────────


@pytest.mark.parametrize("path,section,field", [
    ("corpus.upstream.revision", "upstream", "revision"),
    ("corpus.upstream.format", "upstream", "format"),
    ("corpus.select.partition_pct", "select", "partition_pct"),
    ("corpus.reconstruct.max_think_time", "reconstruct", "max_think_time"),
    ("corpus.reconstruct.max_context", "reconstruct", "max_context"),
])
def test_deferred_field_rejected_with_its_own_reason(path, section, field):
    """A known-but-unhonored field explains the gap; it is not a typo report."""
    d = _doc()
    d["corpus"][section][field] = "x"
    with pytest.raises(AssembleError) as exc:
        corpus_schema.validate_corpus_document(d, "w.yaml")
    msg = str(exc.value)
    assert path in msg
    assert corpus_schema.DEFERRED_FIELDS[path] in msg
    # The strict unknown-key branch is a DIFFERENT error; keep them distinct so
    # an operator can tell "not supported yet" from "you typo'd".
    assert "unrecognized" not in msg


#: Fields whose task-side support is being built and therefore have a tracking
#: issue. The other two deferred fields (partition_pct, max_context) have no
#: issue yet — the capability they need has not been proposed anywhere — so
#: requiring an issue number for every refusal would be wrong.
_TRACKED_BY_905 = (
    "corpus.upstream.revision",
    "corpus.upstream.format",
    "corpus.reconstruct.max_think_time",
)


def test_every_deferred_reason_names_the_component_that_must_change():
    """A refusal an operator cannot act on is a dead end. Every reason must
    point at the Task, the converter, or the sim2real emission site."""
    anchors = ("prepare-trace", "blis convert", "pipeline/pipeline.yaml")
    for path, reason in corpus_schema.DEFERRED_FIELDS.items():
        assert any(a in reason for a in anchors), (
            f"{path} names no component an operator could change"
        )


#: The three #905 fields and the PipelineRun param each would drive. The Task
#: accepts all three at the pinned submodule; sim2real does not send them.
_905_PARAMS = {
    "corpus.upstream.revision": "traceRevision",
    "corpus.upstream.format": "traceFormat",
    "corpus.reconstruct.max_think_time": "traceMaxThinkTime",
}


def test_905_fields_are_declared_by_the_pinned_task():
    """The reasons claim task-side support EXISTS at the pin. If that ever stops
    being true (a submodule rollback), the reasons become wrong and this fails
    rather than leaving a refusal citing the wrong blocker."""
    task = (pathlib.Path(layout.repo_root()) / "tektonc-data-collection"
            / "tekton" / "tasks" / "prepare-trace.yaml")
    if not task.exists():
        pytest.skip("tektonc-data-collection submodule not checked out")
    declared = {p["name"] for p in _yaml.safe_load(task.read_text())["spec"]["params"]}
    missing = sorted(set(_905_PARAMS.values()) - declared)
    assert not missing, (
        f"DEFERRED_FIELDS claims the pinned prepare-trace Task accepts these, "
        f"but it does not declare: {missing}"
    )


@pytest.mark.parametrize("path,param", sorted(_905_PARAMS.items()))
def test_905_fields_are_not_yet_emitted_by_sim2real(path, param):
    """The reasons claim the remaining blocker is sim2real's own emission side.
    This is the condition that must flip for #905 — when someone declares the
    param in pipeline.yaml, this fails and points at the field to un-defer."""
    pl = (pathlib.Path(layout.repo_root()) / "pipeline" / "pipeline.yaml")
    declared = {p["name"] for p in _yaml.safe_load(pl.read_text())["spec"]["params"]}
    assert param not in declared, (
        f"pipeline.yaml now declares {param}, so {path} is no longer blocked on "
        f"sim2real's emission side — move it out of DEFERRED_FIELDS into the "
        f"schema tables (#905) and update its reason"
    )


@pytest.mark.parametrize("path", _TRACKED_BY_905)
def test_deferred_field_with_task_side_work_in_flight_names_its_issue(path):
    assert "sim2real#905" in corpus_schema.DEFERRED_FIELDS[path]


def test_max_think_time_reason_states_the_clamp_stays_in_force():
    """Refusing the field does not lift the converter's 15s default — the most
    load-bearing sentence in the whole module, so pin it."""
    reason = corpus_schema.DEFERRED_FIELDS["corpus.reconstruct.max_think_time"]
    assert "15s" in reason


def test_no_field_is_both_deferred_and_applied():
    applied = {f"corpus.{sec}.{name}"
               for sec, fields in corpus_schema.CORPUS_FIELDS.items()
               for name in fields}
    applied |= {f"replay.{name}" for name in corpus_schema.REPLAY_FIELDS}
    assert applied.isdisjoint(corpus_schema.DEFERRED_FIELDS)


# ── required fields and value checks ─────────────────────────────────────


def test_missing_required_source_rejected():
    d = _doc()
    del d["corpus"]["upstream"]["source"]
    with pytest.raises(AssembleError, match="corpus.upstream.source"):
        corpus_schema.validate_corpus_document(d, "w.yaml")


def test_missing_replay_block_rejected():
    d = _doc()
    del d["replay"]
    with pytest.raises(AssembleError, match="replay"):
        corpus_schema.validate_corpus_document(d, "w.yaml")


@pytest.mark.parametrize("field", ["concurrent_sessions", "total_sessions"])
def test_missing_required_replay_field_rejected(field):
    d = _doc()
    del d["replay"][field]
    with pytest.raises(AssembleError, match=field):
        corpus_schema.validate_corpus_document(d, "w.yaml")


def test_concurrent_sessions_must_be_at_least_one():
    d = _doc()
    d["replay"]["concurrent_sessions"] = 0
    with pytest.raises(AssembleError, match="concurrent_sessions"):
        corpus_schema.validate_corpus_document(d, "w.yaml")


def test_total_sessions_zero_allowed():
    d = _doc()
    d["replay"]["total_sessions"] = 0
    corpus_schema.validate_corpus_document(d, "w.yaml")


@pytest.mark.parametrize("field", ["concurrent_sessions", "total_sessions"])
def test_bool_rejected_where_int_required(field):
    """bool subclasses int — YAML `true` must not satisfy an int check."""
    d = _doc()
    d["replay"][field] = True
    with pytest.raises(AssembleError, match=field):
        corpus_schema.validate_corpus_document(d, "w.yaml")


def test_bool_rejected_for_shards_and_min_rounds():
    for section, field in (("upstream", "shards"), ("select", "min_rounds")):
        d = _doc()
        d["corpus"][section][field] = True
        with pytest.raises(AssembleError, match=field):
            corpus_schema.validate_corpus_document(d, "w.yaml")


def test_source_must_be_non_empty():
    d = _doc()
    d["corpus"]["upstream"]["source"] = ""
    with pytest.raises(AssembleError, match="source"):
        corpus_schema.validate_corpus_document(d, "w.yaml")


def test_partition_value_is_constrained():
    d = _doc()
    d["corpus"]["select"]["partition"] = "validation"
    with pytest.raises(AssembleError, match="partition"):
        corpus_schema.validate_corpus_document(d, "w.yaml")


def test_context_growth_value_is_constrained():
    d = _doc()
    d["corpus"]["reconstruct"]["context_growth"] = "sliding"
    with pytest.raises(AssembleError, match="context_growth"):
        corpus_schema.validate_corpus_document(d, "w.yaml")


def test_dedup_must_be_bool_not_string():
    d = _doc()
    d["corpus"]["select"]["dedup_by_conversation"] = "yes"
    with pytest.raises(AssembleError, match="dedup_by_conversation"):
        corpus_schema.validate_corpus_document(d, "w.yaml")


# ── cache key ────────────────────────────────────────────────────────────


def test_cache_key_is_twelve_hex():
    k = corpus_schema.corpus_cache_key(_VALID["corpus"])
    assert len(k) == 12
    assert all(c in "0123456789abcdef" for c in k)


def test_cache_key_ignores_key_order():
    a = {"upstream": {"source": "hf:o/d", "shards": 3},
         "select": {"min_rounds": 2, "shuffle_seed": 7}}
    b = {"select": {"shuffle_seed": 7, "min_rounds": 2},
         "upstream": {"shards": 3, "source": "hf:o/d"}}
    assert corpus_schema.corpus_cache_key(a) == corpus_schema.corpus_cache_key(b)


def test_cache_key_changes_with_any_corpus_field():
    base = corpus_schema.corpus_cache_key(_VALID["corpus"])
    for section, field, new in [
        ("upstream", "source", "hf:other/ds"),
        ("upstream", "shards", 7),
        ("select", "partition", "train"),
        ("select", "min_rounds", 5),
        ("select", "dedup_by_conversation", False),
        ("select", "shuffle_seed", 1),
        ("reconstruct", "context_growth", "independent"),
    ]:
        c = copy.deepcopy(_VALID["corpus"])
        c[section][field] = new
        assert corpus_schema.corpus_cache_key(c) != base, f"{section}.{field}"


# ── AC4: no field is hashed without being applied ────────────────────────


#: Every legal corpus field mapped to a NON-DEFAULT value. Kept beside the
#: invariant test that consumes it; the test fails if the schema grows a field
#: this mapping does not cover, which is what forces the two to stay in step.
_NON_DEFAULT: dict[tuple[str, str], object] = {
    ("upstream", "source"): "hf:Other/ds",
    ("upstream", "shards"): 7,
    ("select", "partition"): "train",
    ("select", "min_rounds"): 5,
    ("select", "dedup_by_conversation"): False,
    ("select", "shuffle_seed"): 1234,
    ("reconstruct", "context_growth"): "independent",
}


def test_non_default_mapping_covers_every_declared_corpus_field():
    declared = {(sec, name)
                for sec, fields in corpus_schema.CORPUS_FIELDS.items()
                for name in fields}
    assert declared == set(_NON_DEFAULT), (
        "the schema changed — extend _NON_DEFAULT so the invariant test below "
        "still covers every field"
    )


def _all_fields_doc() -> dict:
    corpus: dict = {}
    for (sec, name), val in _NON_DEFAULT.items():
        corpus.setdefault(sec, {})[name] = val
    return {"corpus": corpus,
            "replay": {"concurrent_sessions": 3, "total_sessions": 9}}


def test_every_corpus_field_is_applied_to_its_param():
    """AC4, first half. A field in the schema that reaches no param would
    change the cache key without changing the corpus."""
    doc = _all_fields_doc()
    corpus_schema.validate_corpus_document(doc, "w.yaml")
    params = _params(doc)
    for (sec, name), val in _NON_DEFAULT.items():
        field = corpus_schema.CORPUS_FIELDS[sec][name]
        assert params[field.param] == field.render(val), f"{sec}.{name} not applied"


def test_every_corpus_field_is_hashed_into_the_cache_key():
    """AC4, second half. Dropping any one field must move the key."""
    doc = _all_fields_doc()
    base = corpus_schema.corpus_cache_key(doc["corpus"])
    for sec, name in _NON_DEFAULT:
        c = copy.deepcopy(doc["corpus"])
        del c[sec][name]
        assert corpus_schema.corpus_cache_key(c) != base, f"{sec}.{name} not hashed"


def test_replay_fields_are_applied_but_not_hashed():
    """The other half of the invariant: replay: reaches its params and does
    NOT move the corpus cache key."""
    corpus = {"upstream": {"source": "hf:o/d"}}
    keys = set()
    for cs, ts in ((3, 9), (11, 22)):
        doc = {"corpus": corpus,
               "replay": {"concurrent_sessions": cs, "total_sessions": ts}}
        corpus_schema.validate_corpus_document(doc, "w.yaml")
        params = _params(doc)
        assert params["concurrentSessions"] == str(cs)
        assert params["totalSessions"] == str(ts)
        keys.add(params["tracePath"])
    assert len(keys) == 1, "replay: must not affect the corpus cache key"


def test_every_schema_param_is_declared_in_pipeline_yaml():
    """A PipelineRun param the Pipeline does not declare is a Tekton admission
    error, not a soft failure. Guard the schema against pipeline.yaml drift."""
    pl = _yaml.safe_load(
        (pathlib.Path(layout.repo_root()) / "pipeline" / "pipeline.yaml").read_text()
    )
    declared = {p["name"] for p in pl["spec"]["params"]}
    needed = {f.param
              for fields in corpus_schema.CORPUS_FIELDS.values()
              for f in fields.values()}
    needed |= {f.param for f in corpus_schema.REPLAY_FIELDS.values()}
    needed |= {"traceSpec", "tracePath"}
    missing = sorted(needed - declared)
    assert not missing, f"not declared in pipeline/pipeline.yaml: {missing}"


def test_pipeline_forwards_no_param_the_pinned_task_rejects():
    """A Pipeline forwarding a param the Task does not declare is a Tekton
    ADMISSION error at PipelineRun creation, not a soft failure — so a
    submodule bump has to be checked against pipeline.yaml, not assumed."""
    root = pathlib.Path(layout.repo_root())
    task_path = (root / "tektonc-data-collection" / "tekton" / "tasks"
                 / "prepare-trace.yaml")
    if not task_path.exists():
        pytest.skip("tektonc-data-collection submodule not checked out")
    task_params = {
        p["name"] for p in _yaml.safe_load(task_path.read_text())["spec"]["params"]
    }
    pl = _yaml.safe_load((root / "pipeline" / "pipeline.yaml").read_text())
    prepare = next(t for t in pl["spec"]["tasks"] if t["name"] == "prepare-trace")
    forwarded = {p["name"] for p in prepare.get("params", [])}
    undeclared = sorted(forwarded - task_params)
    assert not undeclared, (
        f"pipeline.yaml forwards params the pinned prepare-trace Task does not "
        f"declare: {undeclared}. Tekton rejects the PipelineRun outright"
    )


def test_trace_path_is_the_cache_key_under_the_traces_prefix():
    """tekton.trace_path is a thin wrapper; pin the relationship so the two
    cannot drift into different addressing schemes."""
    corpus = _VALID["corpus"]
    assert trace_path(corpus) == f"traces/{corpus_schema.corpus_cache_key(corpus)}"
