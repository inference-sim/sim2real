"""Tests for the corpus/replay workload document schema (issue #901)."""
import copy
import pathlib
import re

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
        # max_think_time is the one REQUIRED corpus field (#905), so every valid
        # fixture must carry it. 60s is the value the exgentic gap distribution
        # argues for: above p90 (27.6s), below the outlier cluster.
        "reconstruct": {"context_growth": "accumulate",
                        "max_think_time": "60s"},
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
    """The minimal document is source + max_think_time + replay: those are the
    only three required fields (#905 made the third required)."""
    corpus_schema.validate_corpus_document(
        {"corpus": {"upstream": {"source": "hf:o/d"},
                    "reconstruct": {"max_think_time": "60s"}},
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
    ("corpus.select.partition_pct", "select", "partition_pct"),
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


def test_every_deferred_reason_names_the_component_that_must_change():
    """A refusal an operator cannot act on is a dead end. Every reason must
    point at the Task, the converter, or the sim2real emission site."""
    anchors = ("prepare-trace", "blis convert", "pipeline/pipeline.yaml")
    for path, reason in corpus_schema.DEFERRED_FIELDS.items():
        assert any(a in reason for a in anchors), (
            f"{path} names no component an operator could change"
        )


#: The three fields #905 moved out of DEFERRED_FIELDS, and the param each drives.
#: Before #905 these were asserted to be ABSENT from pipeline.yaml (the tripwire
#: that fired when the wiring landed); now they are asserted PRESENT end to end.
_905_PARAMS = {
    "corpus.upstream.revision": "traceRevision",
    "corpus.upstream.format": "traceFormat",
    "corpus.reconstruct.max_think_time": "traceMaxThinkTime",
}


@pytest.mark.parametrize("path,param", sorted(_905_PARAMS.items()))
def test_905_field_is_live_in_the_schema(path, param):
    """Each field is a real schema field naming its param, and no longer refused.
    This is the inverse of the pre-#905 tripwire: it fails if someone re-defers
    one of these without also removing it from the tables."""
    assert path not in corpus_schema.DEFERRED_FIELDS
    _, section, field = path.split(".")
    assert corpus_schema.CORPUS_FIELDS[section][field].param == param


def test_905_params_are_declared_by_the_pinned_task():
    """A param the Task does not declare is a Tekton ADMISSION error at
    PipelineRun creation. Now that assemble always sends these three, a submodule
    rollback that drops them breaks every corpus run — so pin it here rather than
    discovering it in a cluster."""
    task = (pathlib.Path(layout.repo_root()) / "tektonc-data-collection"
            / "tekton" / "tasks" / "prepare-trace.yaml")
    if not task.exists():
        pytest.skip("tektonc-data-collection submodule not checked out")
    declared = {p["name"] for p in _yaml.safe_load(task.read_text())["spec"]["params"]}
    missing = sorted(set(_905_PARAMS.values()) - declared)
    assert not missing, (
        f"the pinned prepare-trace Task does not declare: {missing}. Every "
        f"corpus PipelineRun sends these, so Tekton would reject all of them"
    )


def test_every_schema_param_is_forwarded_to_prepare_trace():
    """Declaring a param in spec.params is only half the wiring — the Pipeline
    must also FORWARD it to the prepare-trace taskRef. A declared-but-unforwarded
    param is silently dormant: the PipelineRun carries the value, the Task never
    sees it, and it runs its own default. That is exactly the accept-and-ignore
    failure #901/#905 exist to remove, so it gets its own test rather than being
    implied by the declaration test above."""
    pl = _yaml.safe_load(
        (pathlib.Path(layout.repo_root()) / "pipeline" / "pipeline.yaml").read_text()
    )
    prepare = next(t for t in pl["spec"]["tasks"] if t["name"] == "prepare-trace")
    forwarded = {p["name"] for p in prepare.get("params", [])}
    needed = {f.param
              for fields in corpus_schema.CORPUS_FIELDS.values()
              for f in fields.values()}
    needed |= {"traceSpec", "tracePath"}
    missing = sorted(needed - forwarded)
    assert not missing, (
        f"pipeline.yaml declares but does not forward to prepare-trace: "
        f"{missing}. The Task would run its own defaults instead"
    )


def test_corpus_formats_match_the_pinned_task_guard():
    """``CORPUS_FORMATS`` is the real gate and the Task's ``case`` arm is the
    in-pod backstop. If they disagree, one of them is wrong: a value we accept
    and it rejects wastes a namespace slot, and a value it accepts and we reject
    is a capability made unreachable."""
    task = (pathlib.Path(layout.repo_root()) / "tektonc-data-collection"
            / "tekton" / "tasks" / "prepare-trace.yaml")
    if not task.exists():
        pytest.skip("tektonc-data-collection submodule not checked out")
    # The guard is the only case arm listing several formats in one alternation;
    # the convert step dispatches on one format per arm.
    arms = re.findall(r"^\s*([a-z0-9-]+(?:\|[a-z0-9-]+)+)\)\s*;;",
                      task.read_text(), re.M)
    assert len(arms) == 1, f"expected exactly one multi-format guard arm, got {arms}"
    assert set(arms[0].split("|")) == set(corpus_schema.CORPUS_FORMATS)


# ── #905: the three newly-live fields ────────────────────────────────────


def test_max_think_time_is_required():
    """The one required corpus field. Every candidate default is wrong somewhere
    (the two converters default in opposite directions, and the right value comes
    from the corpus's own gap distribution), so the descriptor must state it."""
    d = _doc()
    del d["corpus"]["reconstruct"]["max_think_time"]
    with pytest.raises(AssembleError, match="max_think_time is required"):
        corpus_schema.validate_corpus_document(d, "w.yaml")


def test_max_think_time_required_even_with_no_reconstruct_section():
    """An absent section must not smuggle a required field past validation."""
    d = _doc()
    del d["corpus"]["reconstruct"]
    with pytest.raises(AssembleError, match="max_think_time is required"):
        corpus_schema.validate_corpus_document(d, "w.yaml")


@pytest.mark.parametrize("good", ["0", "60s", "15m", "1h30m", "1.5s", "500ms"])
def test_max_think_time_accepts_go_durations(good):
    d = _doc()
    d["corpus"]["reconstruct"]["max_think_time"] = good
    corpus_schema.validate_corpus_document(d, "w.yaml")


@pytest.mark.parametrize("bad", [
    15000000,       # bare int — the shape the TraceV2 think_time_us column invites
    "15000000",     # unitless string: Go refuses it, but only inside the pod
    60, 0, True, None, "", "abc", "1 s",
    "-5s",          # Go parses it; both converters reject it (convert_otel.go:47)
    "9999999999s",  # overflows int64 nanoseconds, as Go's own parser reports
])
def test_max_think_time_rejects_non_durations(bad):
    d = _doc()
    d["corpus"]["reconstruct"]["max_think_time"] = bad
    with pytest.raises(AssembleError, match="max_think_time"):
        corpus_schema.validate_corpus_document(d, "w.yaml")


def test_max_think_time_unit_is_carried_verbatim_to_the_param():
    """The unit must survive into the param — rendering "60s" as "60" would
    recreate the exact silent error the string requirement exists to prevent."""
    d = _doc()
    d["corpus"]["reconstruct"]["max_think_time"] = "90s"
    assert _params(d)["traceMaxThinkTime"] == "90s"


@pytest.mark.parametrize("fmt", corpus_schema.CORPUS_FORMATS)
def test_format_accepts_every_declared_format(fmt):
    d = _doc()
    d["corpus"]["upstream"]["format"] = fmt
    assert _params(d)["traceFormat"] == fmt


@pytest.mark.parametrize("bad", ["parquet", "weka", "otel", "", "OTEL-PARQUET", 1])
def test_format_rejects_anything_the_task_guard_would(bad):
    d = _doc()
    d["corpus"]["upstream"]["format"] = bad
    with pytest.raises(AssembleError, match="format"):
        corpus_schema.validate_corpus_document(d, "w.yaml")


def test_format_defaults_to_otel_parquet():
    """The default must match the Task's, so a descriptor written before #905
    keeps building byte-identical corpora."""
    d = _doc()
    assert "format" not in d["corpus"]["upstream"]
    assert _params(d)["traceFormat"] == "otel-parquet"


def test_revision_reaches_its_param():
    d = _doc()
    d["corpus"]["upstream"]["revision"] = "a1b2c3d4"
    assert _params(d)["traceRevision"] == "a1b2c3d4"


def test_revision_defaults_to_empty_meaning_upstream_default_branch():
    d = _doc()
    assert "revision" not in d["corpus"]["upstream"]
    assert _params(d)["traceRevision"] == ""


@pytest.mark.parametrize("bad", ["", 1, True, None, []])
def test_revision_rejects_non_strings_and_empty(bad):
    """Empty is the DEFAULT (meaning "upstream default branch") but not a legal
    written value: writing it states an intent the field cannot express."""
    d = _doc()
    d["corpus"]["upstream"]["revision"] = bad
    with pytest.raises(AssembleError, match="revision"):
        corpus_schema.validate_corpus_document(d, "w.yaml")


@pytest.mark.parametrize("section,field,value", [
    ("upstream", "revision", "feedfacecafe"),
    ("upstream", "format", "weka-jsonl"),
    ("reconstruct", "max_think_time", "1h"),
])
def test_each_new_field_changes_the_corpus_cache_key(section, field, value):
    """All three are corpus CONTENT: a different revision, format, or think-time
    cap builds a different corpus, so it must not reuse the cached one."""
    before = trace_path(_VALID["corpus"])
    c = copy.deepcopy(_VALID["corpus"])
    c[section][field] = value
    assert trace_path(c) != before


# ── #905: weka-jsonl renders three otel-only fields inert ────────────────


#: The three fields, and a non-default value for each, for the rejection tests.
_INERT = [("partition", "train"),
          ("dedup_by_conversation", False),
          ("shuffle_seed", 7)]


def _weka_doc() -> dict:
    """A legal weka-jsonl document: `_VALID` minus the three inert select fields.

    Built by REMOVING them rather than by writing a fresh literal, so a schema
    change that adds a select field is inherited here instead of silently leaving
    this fixture behind.
    """
    d = _doc()
    d["corpus"]["upstream"]["format"] = "weka-jsonl"
    for field, _ in _INERT:
        d["corpus"]["select"].pop(field, None)
    return d


def test_weka_doc_fixture_is_actually_valid():
    """The negative tests below are only meaningful if the fixture they start
    from passes — otherwise they could report the wrong field and still pass."""
    corpus_schema.validate_corpus_document(_weka_doc(), "w.yaml")


@pytest.mark.parametrize("field,value", _INERT)
def test_weka_refuses_fields_build_otel_would_have_applied(field, value):
    """build-otel applies these and is SKIPPED for weka-jsonl, so accepting one
    would hash it into the cache key while changing nothing about the corpus.

    Only the field under test is present, so the error must name THAT field —
    starting from a document carrying all three would let whichever comes first
    in iteration order satisfy every case.
    """
    d = _weka_doc()
    d["corpus"]["select"][field] = value
    with pytest.raises(AssembleError) as exc:
        corpus_schema.validate_corpus_document(d, "w.yaml")
    msg = str(exc.value)
    assert f"corpus.select.{field}" in msg
    assert "weka-jsonl" in msg
    # Must not read as a typo report — the field IS legal, just not here.
    assert "unrecognized" not in msg


@pytest.mark.parametrize("field,value", _INERT)
def test_the_same_fields_are_fine_on_the_otel_path(field, value):
    """Explicitly and by default: otel-parquet is where build-otel runs."""
    for fmt in ("otel-parquet", None):
        d = _doc()
        if fmt:
            d["corpus"]["upstream"]["format"] = fmt
        d["corpus"]["select"][field] = value
        corpus_schema.validate_corpus_document(d, "w.yaml")


def test_weka_without_the_inert_fields_is_accepted():
    """The rejection keys on what the document WROTE, not on the resolved value.
    Refusing defaults too would make weka-jsonl unreachable."""
    corpus_schema.validate_corpus_document(_weka_doc(), "w.yaml")


@pytest.mark.parametrize("section,field,value", [
    ("select", "min_rounds", 5),
    ("reconstruct", "context_growth", "independent"),
    ("reconstruct", "max_think_time", "90s"),
])
def test_weka_keeps_the_fields_the_converter_actually_receives(section, field, value):
    """`blis convert weka` is passed --min-rounds, --context-growth and
    --max-think-time, so these stay live on the weka path and must NOT be
    swept up by the inert-field rejection."""
    d = _weka_doc()
    d["corpus"][section][field] = value
    corpus_schema.validate_corpus_document(d, "w.yaml")


def test_weka_inert_fields_are_all_real_schema_fields():
    """A typo'd path here would be a rejection that never fires."""
    for path in corpus_schema.WEKA_INERT_FIELDS:
        _, section, key = path.split(".")
        assert key in corpus_schema.CORPUS_FIELDS[section], path


def test_weka_inert_set_matches_the_pinned_tasks_own_markers():
    """The Task marks exactly the params it cannot honor on the weka path
    "OTEL-PARQUET ONLY". Derive the set from those markers rather than trusting
    this module's copy: if tektonc ever gives weka a split or a shuffle, the
    marker goes away and this fails, pointing at a rejection to lift."""
    task = (pathlib.Path(layout.repo_root()) / "tektonc-data-collection"
            / "tekton" / "tasks" / "prepare-trace.yaml")
    if not task.exists():
        pytest.skip("tektonc-data-collection submodule not checked out")
    spec = _yaml.safe_load(task.read_text())["spec"]
    otel_only = {p["name"] for p in spec["params"]
                 if "OTEL-PARQUET ONLY" in (p.get("description") or "")}
    ours = {corpus_schema.CORPUS_FIELDS[s][k].param
            for s, k in (p.split(".")[1:] for p in corpus_schema.WEKA_INERT_FIELDS)}
    assert ours == otel_only, (
        f"schema refuses {sorted(ours)} for weka but the pinned Task marks "
        f"{sorted(otel_only)} as otel-only"
    )


def test_new_fields_are_picked_up_without_touching_the_renderer():
    """``tekton._render_corpus_params`` is driven entirely by CORPUS_FIELDS, so
    #905 added no emission code. Assert the table alone is what produced the
    three params, since a hand-written emit branch would be a regression."""
    emitted = set(_params(_doc()))
    assert set(_905_PARAMS.values()) <= emitted
    from pipeline.lib import tekton
    src = pathlib.Path(tekton.__file__).read_text()
    for param in _905_PARAMS.values():
        assert param not in src, (
            f"{param} is hardcoded in tekton.py; it must come from CORPUS_FIELDS"
        )


def test_replay_fields_name_a_flag_not_a_param():
    """#900: replay values are argv words, not PipelineRun params."""
    for name, field in corpus_schema.REPLAY_FIELDS.items():
        assert field.param is None, f"replay.{name} must not name a param"
        assert field.flag.startswith("--"), f"replay.{name} must name a flag"


def test_corpus_fields_name_a_param_not_a_flag():
    for sec, fields in corpus_schema.CORPUS_FIELDS.items():
        for name, field in fields.items():
            assert field.param is not None, f"corpus.{sec}.{name} needs a param"
            assert field.flag is None


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
    ("upstream", "revision"): "d34db33f",
    ("upstream", "format"): "weka-jsonl",
    ("upstream", "shards"): 7,
    ("reconstruct", "max_think_time"): "90s",
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
        "the schema changed — extend _NON_DEFAULT so the invariant tests below "
        "still cover every field"
    )


#: Fields that cannot share one document with `format: weka-jsonl`, because #905
#: refuses them there (build-otel applies them and weka skips build-otel).
_WEKA_EXCLUDED = {tuple(p.split(".")[1:]) for p in corpus_schema.WEKA_INERT_FIELDS}


def _all_fields_docs() -> list[tuple[dict, set]]:
    """Every corpus field at a non-default value, spread over the FEWEST legal
    documents rather than crammed into one.

    One document no longer suffices: `format: weka-jsonl` and
    `select.partition`/`dedup_by_conversation`/`shuffle_seed` are now mutually
    exclusive. Splitting keeps the invariant tests covering all ten fields while
    every document they build is one assemble would actually accept — a single
    document could only stay legal by dropping a field from coverage, which is
    the opposite of what these tests are for.

    Returns (document, fields-it-covers) pairs whose field sets union to
    everything in ``_NON_DEFAULT``.
    """
    def build(keys):
        corpus: dict = {}
        for sec, name in keys:
            corpus.setdefault(sec, {})[name] = _NON_DEFAULT[(sec, name)]
        return ({"corpus": corpus,
                 "replay": {"concurrent_sessions": 3, "total_sessions": 9}},
                set(keys))

    all_keys = set(_NON_DEFAULT)
    # Doc 1: the weka document — format at its non-default, minus the three it
    # renders inert.
    weka = all_keys - _WEKA_EXCLUDED
    # Doc 2: the otel document — the three excluded fields, plus everything
    # required to make a valid document, with format left at its default.
    otel = (all_keys - {("upstream", "format")})
    docs = [build(sorted(weka)), build(sorted(otel))]
    covered = set().union(*(c for _, c in docs))
    assert covered == all_keys, sorted(all_keys - covered)
    return docs


def test_every_corpus_field_is_applied_to_its_param():
    """AC4, first half. A field in the schema that reaches no param would
    change the cache key without changing the corpus."""
    for doc, keys in _all_fields_docs():
        corpus_schema.validate_corpus_document(doc, "w.yaml")
        params = _params(doc)
        for sec, name in keys:
            field = corpus_schema.CORPUS_FIELDS[sec][name]
            expected = field.render(_NON_DEFAULT[(sec, name)])
            assert params[field.param] == expected, f"{sec}.{name} not applied"


def test_every_corpus_field_is_hashed_into_the_cache_key():
    """AC4, second half. Dropping any one field must move the key."""
    for doc, keys in _all_fields_docs():
        base = corpus_schema.corpus_cache_key(doc["corpus"])
        for sec, name in keys:
            c = copy.deepcopy(doc["corpus"])
            del c[sec][name]
            assert corpus_schema.corpus_cache_key(c) != base, (
                f"{sec}.{name} not hashed"
            )


def test_replay_fields_are_applied_but_not_hashed():
    """The other half of the invariant: replay: reaches its params and does
    NOT move the corpus cache key."""
    corpus = {"upstream": {"source": "hf:o/d"},
              "reconstruct": {"max_think_time": "60s"}}
    keys = set()
    for cs, ts in ((3, 9), (11, 22)):
        doc = {"corpus": corpus,
               "replay": {"concurrent_sessions": cs, "total_sessions": ts}}
        corpus_schema.validate_corpus_document(doc, "w.yaml")
        params = _params(doc)
        assert f"--concurrent-sessions {cs}" in params["observeArgs"]
        assert f"--total-sessions {ts}" in params["observeArgs"]
        keys.add(params["tracePath"])
    assert len(keys) == 1, "replay: must not affect the corpus cache key"


def test_every_schema_param_is_declared_in_pipeline_yaml():
    """A PipelineRun param the Pipeline does not declare is a Tekton admission
    error, not a soft failure. Guard the schema against pipeline.yaml drift."""
    pl = _yaml.safe_load(
        (pathlib.Path(layout.repo_root()) / "pipeline" / "pipeline.yaml").read_text()
    )
    declared = {p["name"] for p in pl["spec"]["params"]}
    # Only CORPUS_FIELDS name PipelineRun params. #900 moved the replay fields
    # into the rendered observe argv, so concurrentSessions/totalSessions no
    # longer exist as params — asserting them here would be a dangling
    # reference. That they still reach a FLAG is asserted by
    # test_observe_argv.py::test_every_replay_field_reaches_a_flag, so #901's
    # applied-or-rejected invariant stays covered end to end.
    needed = {f.param
              for fields in corpus_schema.CORPUS_FIELDS.values()
              for f in fields.values()}
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


# ── replay one-of: total_sessions XOR duration (#923) ────────────────────────


def _replay(**over):
    """A valid corpus doc whose replay block is replaced wholesale."""
    d = copy.deepcopy(_VALID)
    d["replay"] = over
    return d


def test_one_of_group_and_table_cannot_drift():
    """Every field marked ONE_OF is in REPLAY_ONE_OF and vice versa. Without
    this, adding a third sizing flag and forgetting the group would silently
    make it required alongside the others."""
    marked = {name for name, f in corpus_schema.REPLAY_FIELDS.items()
              if f.default is corpus_schema.ONE_OF}
    assert marked == set(corpus_schema.REPLAY_ONE_OF)
    assert corpus_schema.REPLAY_ONE_OF == {"total_sessions", "duration"}


def test_one_of_members_are_never_also_REQUIRED():
    for name in corpus_schema.REPLAY_ONE_OF:
        assert (corpus_schema.REPLAY_FIELDS[name].default
                is not corpus_schema.REQUIRED)


def test_duration_accepted_in_place_of_total_sessions():
    corpus_schema.validate_corpus_document(
        _replay(concurrent_sessions=8, duration="20m"), "w.yaml")


def test_total_sessions_still_accepted_alone():
    corpus_schema.validate_corpus_document(
        _replay(concurrent_sessions=8, total_sessions=8), "w.yaml")


def test_total_sessions_zero_is_still_a_legal_value():
    """AC 3. 0 means 'replay each corpus session once' and is in real use
    (exgentic-agentic-multiturn-sess25 in pd-infocomm-4). The one-of keys on
    whether the KEY is written, never on truthiness, so 0 must survive."""
    corpus_schema.validate_corpus_document(
        _replay(concurrent_sessions=25, total_sessions=0), "w.yaml")


@pytest.mark.parametrize("value", [20, True, None, "60000", "-5m", "0", "0s"])
def test_non_duration_values_refused(value):
    """Review Focus 1 + AC 6. A bare number is the silent wrong-unit hazard
    duration.py exists to refuse; 0 is the one that also passes
    is_go_duration."""
    with pytest.raises(AssembleError, match="replay.duration"):
        corpus_schema.validate_corpus_document(
            _replay(concurrent_sessions=8, duration=value), "w.yaml")


def test_both_one_of_members_refused():
    """AC 4."""
    with pytest.raises(AssembleError, match="mutually exclusive"):
        corpus_schema.validate_corpus_document(
            _replay(concurrent_sessions=8, total_sessions=8, duration="20m"),
            "w.yaml")


def test_explicit_null_member_counts_as_written():
    """Review Focus 2. Commenting out a value leaves 'total_sessions:' -> None.
    blis keys on supplied-ness, so this must refuse rather than quietly treat
    the key as absent and emit both flags."""
    with pytest.raises(AssembleError, match="mutually exclusive"):
        corpus_schema.validate_corpus_document(
            _replay(concurrent_sessions=8, total_sessions=None,
                    duration="20m"), "w.yaml")


def test_neither_one_of_member_refused():
    """AC 5."""
    with pytest.raises(AssembleError, match="exactly one of"):
        corpus_schema.validate_corpus_document(
            _replay(concurrent_sessions=8), "w.yaml")


def test_duration_is_legal_on_weka_jsonl():
    """Review Focus 3. WEKA_INERT_FIELDS refuses three corpus: fields on this
    format because build-otel is skipped. replay: is applied by blis observe,
    not build-otel, so duration must stay legal here."""
    doc = copy.deepcopy(_VALID)
    doc["corpus"]["upstream"] = {"source": "hf:o/d", "format": "weka-jsonl"}
    doc["corpus"]["select"] = {"min_rounds": 2}
    doc["replay"] = {"concurrent_sessions": 8, "duration": "20m"}
    corpus_schema.validate_corpus_document(doc, "w.yaml")


def test_no_replay_field_is_weka_inert():
    assert not any(p.startswith("replay.")
                   for p in corpus_schema.WEKA_INERT_FIELDS)


def test_replay_only_document_still_refused():
    """Review Focus 4. DOCUMENT_MARKERS routes on the presence of corpus OR
    replay, so a generative doc carrying a stray replay block lands here and
    must be refused for the missing corpus -- not silently accepted."""
    with pytest.raises(AssembleError, match="corpus"):
        corpus_schema.validate_corpus_document(
            {"replay": {"concurrent_sessions": 8, "duration": "20m"}},
            "w.yaml")


def test_duration_does_not_move_the_corpus_cache_key():
    """AC 7. replay: is applied, never hashed -- two cells differing only in the
    time bound must share one corpus build."""
    corpus = {"upstream": {"source": "hf:o/d"},
              "reconstruct": {"max_think_time": "60s"}}
    keys = set()
    for replay in ({"concurrent_sessions": 8, "total_sessions": 8},
                   {"concurrent_sessions": 8, "duration": "20m"},
                   {"concurrent_sessions": 8, "duration": "45m"}):
        doc = {"corpus": copy.deepcopy(corpus), "replay": replay}
        corpus_schema.validate_corpus_document(doc, "w.yaml")
        keys.add(corpus_schema.corpus_cache_key(doc["corpus"]))
    assert len(keys) == 1, "replay.duration must not move the corpus cache key"
