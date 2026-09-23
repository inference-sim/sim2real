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

#: Flags driven by the measurement protocol, in rendered order, at their defaults. Mirrors
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
    from a PipelineRun param to an argv flag.

    Reachability is checked across every legal document shape, not within one:
    #923 made ``total_sessions``/``duration`` a one-of, so no single cell can
    render both and a same-argv assertion would be unsatisfiable. The invariant
    being protected is unchanged — a replay field that reaches no flag in ANY
    legal shape is silently ignored, which is what #901 exists to refuse.
    """
    from pipeline.lib import corpus_schema
    # One rendering per one-of member, so every member gets its turn.
    renders = [
        _render(workload=_CORPUS_WORKLOAD, trace_path="traces/x"),
        _render(workload=_CORPUS_DURATION, trace_path="traces/x"),
    ]
    for name, field in corpus_schema.REPLAY_FIELDS.items():
        assert any(field.flag in got for got in renders), (
            f"replay.{name} reaches no flag in any legal document shape"
        )
        assert field.param is None, f"replay.{name} must not name a param"
    # And the one-of really is exclusive: never both in the same argv.
    sizing_flags = [corpus_schema.REPLAY_FIELDS[n].flag
                    for n in corpus_schema.REPLAY_ONE_OF]
    for got in renders:
        present = [f for f in sizing_flags if f in got]
        assert len(present) == 1, (
            f"expected exactly one sizing flag, got {present}"
        )


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


def test_whitespace_only_argv_is_treated_as_empty():
    """A bare non-empty test accepts "   ", which word-splits to nothing and
    reaches blis exactly as "" would — the same defect wearing a disguise. The
    guard checks the joined string stripped, so a flag table that somehow
    rendered only blanks is caught too."""
    assert _render().strip() == _render()
    # No rendered argv may begin or end with padding, since the Task word-splits
    # and a padded value is indistinguishable from a missing one in a log line.
    for over in ({}, {"observe": {"extraArgs": "  --rate 5  "}},
                 {"workload": _CORPUS_WORKLOAD, "trace_path": "traces/x"}):
        got = _render(**over)
        assert got == got.strip()
        assert "  " not in got, "double space would split to an empty word"


def test_blis_rejects_corpus_plus_spec_flags_loudly():
    """Documents the DOWNSTREAM backstop, verified in blis rather than assumed:
    validateObserveCorpusFlags (cmd/observe_corpus.go:61) returns
    "--workload-spec is invalid with --concurrent-sessions" and the caller
    logrus.Fatalf's it at observe_cmd.go:300 — BEFORE the corpus branch. So a
    renderer regression cannot silently replay the wrong workload.

    That backstop does NOT make the renderer check redundant: failing at
    assemble beats a pod that starts and dies. It does mean the failure mode is
    loud rather than silent, so this test asserts the renderer never emits the
    pair and records why over-widening validation on a permissiveness
    assumption would be wrong."""
    spec = _render()
    corpus = _render(workload=_CORPUS_WORKLOAD, trace_path="traces/x")
    assert "--concurrent-sessions" not in spec
    assert "--workload-spec" not in corpus


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
    assert "if not rendered.strip():" in src
    assert "argv is empty" in src


# ── error-message provenance (#911) ──────────────────────────────────────────

def test_error_messages_name_the_measurement_file_not_the_old_manifest_block():
    """The protocol moved to measurement.yaml (#911), so runtime errors must name
    that, not the `blis_observe:` block a bundle can no longer declare.

    Asserted because nothing else did: SOURCE_LABEL could be reverted, or a call
    site re-hardcoded to "blis_observe", and every other test would still pass
    while every operator-facing error pointed at a key the loader now rejects —
    the same name-drift class this issue exists to remove.
    """
    assert observe_argv.SOURCE_LABEL == "measurement"

    # An unknown key, via the type authority the manifest consumes.
    assert "measurement" in observe_argv.check_observe_type("bogus", 1)
    assert "blis_observe" not in observe_argv.check_observe_type("bogus", 1)

    def _err(observe):
        with pytest.raises(observe_argv.ObserveArgvError) as exc:
            observe_argv.render_observe_argv(
                workload={"name": "w"}, observe=observe, model="m",
                results_dir="run/p/wl/i1",
            )
        return str(exc.value)

    # Each render-time path that names the source in its message.
    for observe, needle in [
        ({"bogus": 1}, "does not know"),
        ({"apiFormat": "nope"}, "apiFormat"),
        ({"detectors": "nope"}, "unknown detector"),
        ({"recordItl": True, "streaming": False}, "recordItl"),
    ]:
        msg = _err(observe)
        assert needle in msg, msg
        assert "measurement" in msg, msg
        assert "blis_observe" not in msg, msg


# ── prewarmDuration is a real duration, not merely a string (#905) ────────────


@pytest.mark.parametrize("good", ["0", "60s", "15m", "1h30m", "1.5s", "500ms"])
def test_prewarm_duration_accepts_go_durations(good):
    assert observe_argv.check_observe_type("prewarmDuration", good) is None


@pytest.mark.parametrize("bad", [
    "60",       # unitless: blis dies at flag parsing, in-cluster
    "",         # rendered as `--prewarm-duration ''`, which blis also refuses
    "60000ns",  # parses fine, prewarms for 60us instead of 60s — see note below
    "-5s",      # blis refuses < 0 itself (observe_cmd.go:355)
    60, 1.5, True, None, "abc", "1 s",
])
def test_prewarm_duration_rejects_non_durations(bad):
    """`--prewarm-duration` is a Cobra DurationVar (observe_cmd.go:154), so the
    old `_is_str` check let three distinct failures through: an unitless string
    that dies at blis flag parsing, an empty string that does the same, and a
    negative that blis refuses on its own. Each now fails at assemble instead.

    `60000ns` is the exception: it is a legal duration and IS accepted — listed
    here only because a reader will look for it. See
    test_duration.py::test_the_silent_thousandfold_error_is_accepted_and_that_is_the_point.
    """
    if bad == "60000ns":
        assert observe_argv.check_observe_type("prewarmDuration", bad) is None
        return
    assert observe_argv.check_observe_type("prewarmDuration", bad) is not None


def test_prewarm_duration_and_max_think_time_share_one_rule():
    """Both land on a Go DurationVar, so they must not drift into two different
    notions of a valid duration."""
    from pipeline.lib import corpus_schema
    mtt = corpus_schema.CORPUS_FIELDS["reconstruct"]["max_think_time"]
    assert mtt.check is observe_argv.OBSERVE_FLAGS["prewarmDuration"].check


def test_timeout_stays_an_int_because_its_flag_is_an_intvar():
    """`--timeout` is an IntVar of SECONDS (observe_cmd.go:192), not a
    DurationVar, so it must NOT acquire the duration check: "1800s" would be
    rejected by blis. Pinned because the two flags look interchangeable."""
    assert observe_argv.check_observe_type("timeout", 1800) is None
    assert observe_argv.check_observe_type("timeout", "1800s") is not None


# ── replay one-of rendering (#923) ──────────────────────────────────────────


_CORPUS_DURATION = {
    "corpus": {"upstream": {"source": "hf:Org/ds"}},
    "replay": {"concurrent_sessions": 128, "duration": "20m"},
}


def test_duration_renders_and_total_sessions_is_absent():
    """AC 1."""
    argv = render_observe_argv(workload=_CORPUS_DURATION, observe=None,
                               model="m", results_dir=_RD,
                               trace_path="traces/x")
    assert "--duration 20m" in argv
    assert "--total-sessions" not in argv
    assert "--concurrent-sessions 128" in argv


def test_total_sessions_rendering_is_byte_identical_to_before():
    """AC 2. The pre-#923 cell must produce exactly the same argv, which is why
    duration is APPENDED to the table rather than inserted."""
    argv = render_observe_argv(workload=_CORPUS_WORKLOAD, observe=None,
                               model="m", results_dir=_RD,
                               trace_path="traces/x")
    assert "--concurrent-sessions 128 --total-sessions 192" in argv
    assert "--duration" not in argv


def test_total_sessions_zero_still_renders_the_flag():
    """AC 3. Rendering must not drop a falsy-but-written value."""
    wl = {"corpus": {"upstream": {"source": "hf:o/d"}},
          "replay": {"concurrent_sessions": 25, "total_sessions": 0}}
    argv = render_observe_argv(workload=wl, observe=None, model="m",
                               results_dir=_RD, trace_path="traces/x")
    assert "--total-sessions 0" in argv


def test_both_one_of_members_raise_at_render_time():
    """Defence in depth: validation should have caught this, so reaching the
    renderer with both means validation was skipped. Fail loudly rather than
    emitting a pair blis fatals on."""
    wl = {"corpus": {"upstream": {"source": "hf:o/d"}},
          "replay": {"concurrent_sessions": 4, "total_sessions": 8,
                     "duration": "20m"}}
    with pytest.raises(ObserveArgvError, match="mutually exclusive"):
        render_observe_argv(workload=wl, observe=None, model="m",
                            results_dir=_RD, trace_path="traces/x")


def test_neither_one_of_member_raises_at_render_time():
    wl = {"corpus": {"upstream": {"source": "hf:o/d"}},
          "replay": {"concurrent_sessions": 4}}
    with pytest.raises(ObserveArgvError, match="exactly one of"):
        render_observe_argv(workload=wl, observe=None, model="m",
                            results_dir=_RD, trace_path="traces/x")


@pytest.mark.parametrize("extra", [
    "--total-sessions 5", "--duration 5m",
    "--record-itl --total-sessions 5",
    # The `=` spelling is an ordinary one, and pflag marks the flag Changed for
    # it identically to the space form — so matching whole words only let the
    # exact in-pod fatal this check exists to prevent through. Worse,
    # generate_from_config.py deliberately PRESERVES --flag=value when routing a
    # flag into extraArgs, so this is the spelling the surrounding code produces.
    "--total-sessions=5", "--duration=5m",
    "--record-itl --total-sessions=5",
])
def test_extra_args_naming_a_sizing_flag_is_refused(extra):
    """#923. extraArgs is a documented override for everything else, but for the
    sizing pair an override is either a silent re-size (Cobra takes the last
    occurrence) or a hard blis fatal."""
    with pytest.raises(ObserveArgvError, match="extraArgs"):
        render_observe_argv(workload=_CORPUS_DURATION,
                            observe={"extraArgs": extra}, model="m",
                            results_dir=_RD, trace_path="traces/x")


@pytest.mark.parametrize("extra", ["--duration 20m", "--duration=20m",
                                   "--total-sessions 5"])
def test_sizing_flags_in_extra_args_refused_on_a_generative_cell_too(extra):
    """The corpus-mode carve-out was wrong for --duration: blis returns
    "--duration requires --concurrent-sessions > 0" (observe_corpus.go:60), so a
    generative cell carrying it in extraArgs cannot start at all. --total-sessions
    is merely inert there, which is the accept-and-ignore shape this module
    refuses anyway. measurement.yaml is bundle-level, so one extraArgs reaches
    generative and corpus cells alike — the refusal has to as well."""
    with pytest.raises(ObserveArgvError, match="extraArgs"):
        render_observe_argv(workload=_SPEC_WORKLOAD,
                            observe={"extraArgs": extra}, model="m",
                            results_dir=_RD)


def test_extra_args_unrelated_to_sizing_still_allowed():
    argv = render_observe_argv(workload=_CORPUS_DURATION,
                               observe={"extraArgs": "--shuffle-corpus"},
                               model="m", results_dir=_RD,
                               trace_path="traces/x")
    assert argv.endswith("--shuffle-corpus")


def test_sizing_flag_check_does_not_fire_on_a_generative_cell():
    """A spec-mode cell renders no sizing flag, so extraArgs naming one is the
    operator's business and collides with nothing this module emitted."""
    argv = _render(observe={"extraArgs": "--num-requests 10"})
    assert "--num-requests 10" in argv
