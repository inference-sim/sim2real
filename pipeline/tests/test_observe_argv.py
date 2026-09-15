"""Tests for the rendered `blis observe` argv (issue #900).

AC-1 is asserted as a GOLDEN STRING for both cell kinds. The baseline is today's
*intended* command, not the literal one: the Task hardcodes
`--post-hoc-detector composite`, and that flag was renamed to `--detectors`
upstream (inference-sim #1516, commit 18c2c926) so it no longer exists at the
pinned blis — the current invocation dies at argument parsing. Reproducing the
literal argv would encode a parse failure in a passing test.
"""
import pytest

from pipeline.lib import observe_argv
from pipeline.lib.observe_argv import ObserveArgvError, render_observe_argv

_RD = "myrun/baseline/wl_chat/i1"
_BASE = f"/workspace/data/{_RD}"

_SPEC_WORKLOAD = {"workload_name": "wl_chat", "version": "1", "clients": []}
_CORPUS_WORKLOAD = {
    "corpus": {"upstream": {"source": "hf:Org/ds"}},
    "replay": {"concurrent_sessions": 128, "total_sessions": 192},
}

#: Flags driven by blis_observe, in rendered order, at their defaults. Mirrors
#: today's intended command: --max-concurrency/--timeout/--prewarm-duration/
#: --warmup-requests then the detector, with --api-format now explicit and
#: --record-itl / --no-streaming absent because their defaults are absence.
_TUNING_DEFAULTS = (
    "--max-concurrency 10000 --timeout 1800 --prewarm-duration 60s "
    "--warmup-requests 50 --detectors composite --api-format completions"
)
_OUTPUTS = (
    f"--trace-header {_BASE}/trace_header.yaml "
    f"--trace-data {_BASE}/trace_data.csv "
    f"--saturation-report {_BASE}/saturation.json"
)


def _render(**over):
    kwargs = dict(workload=_SPEC_WORKLOAD, observe=None, model="qwen/qwen3-14b",
                  results_dir=_RD, trace_path="")
    kwargs.update(over)
    return render_observe_argv(**kwargs)


# ── AC-1: golden argv ───────────────────────────────────────────────────────


def test_ac1_golden_argv_synthetic_cell():
    assert _render() == (
        f"{_TUNING_DEFAULTS} --model qwen/qwen3-14b "
        f"--workload-spec /workspace/workload.yaml {_OUTPUTS}"
    )


def test_ac1_golden_argv_corpus_cell():
    got = _render(workload=_CORPUS_WORKLOAD, trace_path="traces/abc123def456")
    assert got == (
        f"{_TUNING_DEFAULTS} --model qwen/qwen3-14b "
        f"--corpus-header /workspace/data/traces/abc123def456.yaml "
        f"--corpus-data /workspace/data/traces/abc123def456.csv "
        f"--concurrent-sessions 128 --total-sessions 192 {_OUTPUTS}"
    )


@pytest.mark.parametrize("kind,over", [
    ("synthetic", {}),
    ("corpus", {"workload": _CORPUS_WORKLOAD, "trace_path": "traces/x"}),
])
def test_ac4_server_url_never_rendered(kind, over):
    assert "--server-url" not in _render(**over)


def test_workload_spec_and_corpus_flags_are_never_both_present():
    assert "--corpus-header" not in _render()
    corpus = _render(workload=_CORPUS_WORKLOAD, trace_path="traces/x")
    assert "--workload-spec" not in corpus


# ── extraArgs ───────────────────────────────────────────────────────────────


def test_extra_args_render_last_and_word_split():
    got = _render(observe={"extraArgs": "--rate 5 --num-requests 100"})
    assert got.endswith("--rate 5 --num-requests 100")
    # It follows the output flags, matching its position in the Task's command
    # before #900 (it was the final argument).
    assert got.index("--saturation-report") < got.index("--rate")


def test_extra_args_absent_when_empty():
    assert _render(observe={"extraArgs": ""}) == _render(observe={})


# ── the three exclusions ────────────────────────────────────────────────────


def test_exclusion_corpus_and_spec_inputs_both_present():
    """AC-3. Reachable only if corpus_schema validation was skipped, which is
    exactly when a renderer-level guard earns its keep."""
    both = dict(_CORPUS_WORKLOAD, clients=[])
    with pytest.raises(ObserveArgvError, match="exactly one kind"):
        render_observe_argv(workload=both, observe=None, model="m",
                            results_dir=_RD, trace_path="traces/x")


def test_corpus_document_without_trace_path_raises():
    with pytest.raises(ObserveArgvError, match="no tracePath"):
        render_observe_argv(workload=_CORPUS_WORKLOAD, observe=None, model="m",
                            results_dir=_RD, trace_path="")


def test_generative_workload_given_a_trace_path_raises():
    with pytest.raises(ObserveArgvError, match="generative"):
        render_observe_argv(workload=_SPEC_WORKLOAD, observe=None, model="m",
                            results_dir=_RD, trace_path="traces/x")


def test_exclusion_record_itl_with_no_streaming():
    """blis rejects the pair itself (validateITLStreamingFlags); caught here so
    it fails at assemble rather than after a pod is scheduled."""
    with pytest.raises(ObserveArgvError, match="recordItl"):
        _render(observe={"recordItl": True, "streaming": False})


def test_record_itl_with_streaming_is_fine():
    got = _render(observe={"recordItl": True, "streaming": True})
    assert "--record-itl" in got
    assert "--no-streaming" not in got


def test_exclusion_saturation_report_requires_detectors():
    """blis errors on '--saturation-report requires --detectors', so detectors
    off must suppress the report too — emit both or neither."""
    got = _render(observe={"detectors": ""})
    assert "--detectors" not in got
    assert "--saturation-report" not in got
    # The other outputs still render; only the gated one drops.
    assert "--trace-header" in got
    assert "--trace-data" in got


# ── detectors ───────────────────────────────────────────────────────────────


def test_detectors_none_is_rejected_with_the_roster():
    with pytest.raises(ObserveArgvError, match="composite"):
        _render(observe={"detectors": "none"})


def test_detectors_none_message_explains_empty_is_off():
    with pytest.raises(ObserveArgvError) as exc:
        _render(observe={"detectors": "none"})
    assert "EMPTY" in str(exc.value)


@pytest.mark.parametrize("value", ["composite", "threshold", "backlog-drift",
                                   "peak-rate", "all", "composite,threshold"])
def test_detectors_accepts_roster_all_and_comma_lists(value):
    assert f"--detectors {value}" in _render(observe={"detectors": value})


def test_detectors_unknown_name_rejected():
    with pytest.raises(ObserveArgvError, match="bogus"):
        _render(observe={"detectors": "bogus"})


def test_post_hoc_detector_is_never_rendered():
    """The renamed flag must not survive anywhere — rendering it would fail at
    blis argument parsing, which is how the current Task is broken."""
    for over in ({}, {"observe": {"detectors": "all"}},
                 {"workload": _CORPUS_WORKLOAD, "trace_path": "traces/x"}):
        assert "--post-hoc-detector" not in _render(**over)


# ── inverted / presence flags ───────────────────────────────────────────────


def test_streaming_true_emits_nothing():
    assert "--no-streaming" not in _render(observe={"streaming": True})


def test_streaming_false_emits_no_streaming():
    assert "--no-streaming" in _render(observe={"streaming": False})


def test_record_itl_false_emits_nothing():
    assert "--record-itl" not in _render(observe={"recordItl": False})


def test_api_format_is_emitted_explicitly_even_at_its_default():
    """Emit-always: suppressing at the default would expose us to blis DEFAULT
    drift, which changes measurements silently. Explicit emission risks
    flag-NAME drift instead, which fails loudly at parse."""
    assert "--api-format completions" in _render(observe={})


def test_api_format_chat_accepted_and_bad_value_rejected():
    assert "--api-format chat" in _render(observe={"apiFormat": "chat"})
    with pytest.raises(ObserveArgvError, match="apiFormat"):
        _render(observe={"apiFormat": "rest"})


# ── value validation ────────────────────────────────────────────────────────


@pytest.mark.parametrize("ch", ['"', "'", "$", "`", ";", "|", "&", ">", "<",
                                "\\", "\n"])
def test_metacharacter_rejected_in_a_scalar(ch):
    with pytest.raises(ObserveArgvError, match="break out of"):
        _render(model=f"qwen{ch}14b")


@pytest.mark.parametrize("ch", ['"', "$", ";", "|", "&", "`"])
def test_metacharacter_rejected_in_extra_args(ch):
    """extraArgs is exempt from the SPACE rule but not this one — a ';' here is
    command injection into the Task's step."""
    with pytest.raises(ObserveArgvError, match="break out of"):
        _render(observe={"extraArgs": f"--rate 5{ch} rm -rf /"})


def test_whitespace_rejected_in_a_scalar():
    with pytest.raises(ObserveArgvError, match="whitespace"):
        _render(model="qwen 14b")


def test_whitespace_allowed_in_extra_args():
    assert "--rate 5" in _render(observe={"extraArgs": "--rate 5"})


@pytest.mark.parametrize("ch", ["*", "?", "["])
def test_glob_characters_are_deliberately_allowed(ch):
    """NOT rejected: the observe Task sets `set -f`, which disables pathname
    expansion while keeping word-splitting, so a glob cannot expand there. That
    defense sits at the consumer and therefore covers every producer. If `set
    -f` is ever removed from the Task, add these to FORBIDDEN_CHARS."""
    assert ch in _render(observe={"extraArgs": f"--rate 5 --tag a{ch}b"})


def test_unknown_observe_key_rejected():
    with pytest.raises(ObserveArgvError, match="postHocDetector"):
        _render(observe={"postHocDetector": "composite"})


def test_missing_replay_field_raises_rather_than_rendering_a_sentinel():
    wl = {"corpus": {"upstream": {"source": "hf:o/d"}},
          "replay": {"concurrent_sessions": 4}}
    with pytest.raises(ObserveArgvError, match="total_sessions"):
        render_observe_argv(workload=wl, observe=None, model="m",
                            results_dir=_RD, trace_path="traces/x")


# ── structural guards ───────────────────────────────────────────────────────


def test_forbidden_chars_covers_the_assignment_breakers():
    for ch in "\"'$`;|&><\\":
        assert ch in observe_argv.FORBIDDEN_CHARS


def test_glob_chars_absent_from_forbidden_set():
    for ch in "*?[":
        assert ch not in observe_argv.FORBIDDEN_CHARS


def test_every_replay_field_reaches_a_flag():
    """#901's applied-or-rejected invariant, after #900 moved the destination
    from a PipelineRun param to an argv flag."""
    from pipeline.lib import corpus_schema
    got = _render(workload=_CORPUS_WORKLOAD, trace_path="traces/x")
    for name, field in corpus_schema.REPLAY_FIELDS.items():
        assert field.flag in got, f"replay.{name} reaches no flag"
        assert field.param is None, f"replay.{name} must not name a param"


def test_rendered_argv_is_never_empty():
    """Tekton's "required param" means SUPPLIED, not non-empty, so an empty
    observeArgs would satisfy the Task's `no default` and reach blis as
    `observe --server-url <ep>` — no workload source, and a failure naming
    blis's complaint rather than the cause. Assert non-emptiness for every cell
    kind so a future refactor cannot open the hole quietly."""
    for over in ({}, {"observe": {"detectors": ""}},
                 {"observe": {"extraArgs": ""}},
                 {"workload": _CORPUS_WORKLOAD, "trace_path": "traces/x"}):
        got = _render(**over)
        assert got.strip(), f"empty argv for {over!r}"
        # A workload source is the one thing blis cannot proceed without.
        assert ("--workload-spec" in got) or ("--corpus-header" in got)


def test_empty_argv_guard_exists_and_is_unreachable_by_construction():
    """The `if not argv` guard is deliberately NOT driven by a test.

    Reaching it requires emptying OBSERVE_FLAGS, which then makes `_resolved`
    return {} and every later lookup a KeyError — so the only way to "cover" the
    line is to make the module tolerate a state it should never be in. That
    trades a real invariant (`resolved` is always fully populated, so `[]` is
    the correct accessor and a KeyError would be a genuine bug signal) for one
    line of coverage. Not worth it.

    What IS asserted is the property the guard protects — see
    test_rendered_argv_is_never_empty. This test pins the guard's existence so
    it cannot be deleted as dead code without a deliberate decision."""
    import inspect

    src = inspect.getsource(observe_argv.render_observe_argv)
    assert "if not argv:" in src
    assert "argv is empty" in src
