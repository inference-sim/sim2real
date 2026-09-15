"""The observe flag lists must match the blis binary the pipeline actually runs.

Why this file exists: sim2real#904 bumped the inference-sim pin past
inference-sim#1516, which renamed ``--post-hoc-detector`` to ``--detectors`` and
removed the 10-flag legacy saturation bank plus ``--saturation-threshold-ms``.
Nothing noticed. The Tekton task kept passing the renamed flag, so ``blis
observe`` died at argument parsing on every run, and this skill's
``OBSERVE_VALID_FLAGS`` kept 12 flags that no longer exist — which is worse than
it sounds, because a ``config.md`` naming one of them is transcribed VERBATIM
into ``extraArgs`` and reaches blis, failing exactly the same way.

These tests read the pinned inference-sim source, so a future pin bump that
renames or removes an observe flag fails here instead of in a pod.
"""
import pathlib
import re
import sys

import pytest

_SKILL = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SKILL))

import generate_from_config as g  # noqa: E402

#: inference-sim is a submodule of the framework repo, three levels up from the
#: skill directory (.claude/skills/sim2real-bootstrap -> repo root).
_SIM = _SKILL.parents[2] / "inference-sim"


def _registered_flags() -> set[str]:
    """Every flag name registered on any cobra command in blis's cmd/ package.

    Deliberately NOT scoped to observeCmd: flags reach observe through
    ``registerDetectorFlags(observeCmd)`` and similar helpers, so a
    per-command scan would need to model that indirection. A repo-wide scan
    can only produce FALSE NEGATIVES here (a flag that exists on `run` but not
    `observe`), never false positives — and the failure this guards against is
    a flag that exists NOWHERE.
    """
    src = "\n".join(
        p.read_text() for p in sorted((_SIM / "cmd").glob("*.go"))
        if not p.name.endswith("_test.go")
    )
    return set(re.findall(r'Flags\(\)\.\w+\(\s*&?[\w.]+\s*,\s*"([\w-]+)"', src))


requires_sim = pytest.mark.skipif(
    not (_SIM / "cmd").is_dir(),
    reason="inference-sim submodule not checked out",
)


@requires_sim
def test_observe_valid_flags_all_exist_in_blis():
    """Every flag the skill will transcribe must be one blis accepts. A flag in
    this list that blis has removed reaches the argv and exits at parse."""
    missing = sorted(
        f for f in g.OBSERVE_VALID_FLAGS if f.lstrip("-") not in _registered_flags()
    )
    assert not missing, (
        f"OBSERVE_VALID_FLAGS names flags the pinned blis does not register: "
        f"{missing}. If a pin bump renamed or removed them, update the list — "
        f"leaving them lets a config.md transcribe a dead flag into extraArgs"
    )


@requires_sim
def test_tuning_and_presence_flags_exist_in_blis():
    """The first-class keys are worse than a transcription miss if they rot:
    they are emitted on EVERY run, not only when a config.md names them."""
    registered = _registered_flags()
    for flag in (*g.OBSERVE_TUNING_FLAGS, *g.OBSERVE_PRESENCE_FLAGS):
        assert flag.lstrip("-") in registered, f"{flag} is not a blis flag"


@requires_sim
def test_post_hoc_detector_appears_nowhere():
    """The specific flag that broke the pipeline. It was renamed to
    --detectors; any surviving reference is a live defect, not a synonym."""
    for name, coll in (
        ("OBSERVE_VALID_FLAGS", g.OBSERVE_VALID_FLAGS),
        ("OBSERVE_TUNING_FLAGS", g.OBSERVE_TUNING_FLAGS),
        ("OBSERVE_PIPELINE_INJECTED_FLAGS", g.OBSERVE_PIPELINE_INJECTED_FLAGS),
        ("OBSERVE_PRESENCE_FLAGS", g.OBSERVE_PRESENCE_FLAGS),
    ):
        assert "--post-hoc-detector" not in coll, f"still present in {name}"


def test_detectors_is_settable_not_injected():
    """It moved from task-hardcoded to operator-settable in #900, so dropping it
    as pipeline-injected would silently discard an operator's choice."""
    assert "--detectors" in g.OBSERVE_TUNING_FLAGS
    assert "--detectors" not in g.OBSERVE_PIPELINE_INJECTED_FLAGS


def test_saturation_report_stays_injected():
    """The renderer supplies its path, so a config.md must not set it."""
    assert "--saturation-report" in g.OBSERVE_PIPELINE_INJECTED_FLAGS


_CONFIG_BLOCK = """\
```bash
blis observe \\
  --timeout 60 \\
  --detectors composite \\
  --api-format chat \\
  --record-itl \\
  --no-streaming
```
"""


def test_config_md_round_trips_all_four_new_flags_into_the_emitted_yaml():
    """The regression this file exists to prevent, end to end: config.md ->
    parse -> render. The parse half alone passing is what let the emitter drop
    these silently, so assert the RENDERED text, not the parsed dict."""
    rendered = g.render_blis_observe_yaml(g.parse_observe_block(_CONFIG_BLOCK))
    assert "timeout: 60  # source: config.md" in rendered
    assert 'detectors: "composite"  # source: config.md' in rendered
    assert 'apiFormat: "chat"  # source: config.md' in rendered
    assert "recordItl: true  # source: config.md" in rendered
    assert "streaming: false  # source: config.md" in rendered


def test_emitted_yaml_is_loadable_and_accepted_by_the_manifest_validator():
    """The emitted block must survive the validator that reads it. A quoted
    bool, or a key the allowlist rejects, would fail only at assemble time —
    after the bundle is written and committed."""
    import pathlib
    import sys as _sys

    import yaml as _yaml

    repo = pathlib.Path(__file__).resolve().parents[3]
    _sys.path.insert(0, str(repo))
    from pipeline.lib import observe_argv

    block = _yaml.safe_load(
        g.render_blis_observe_yaml(g.parse_observe_block(_CONFIG_BLOCK))
    )["blis_observe"]
    assert block["recordItl"] is True
    assert block["streaming"] is False
    for key, value in block.items():
        assert observe_argv.check_observe_type(key, value) is None, (
            f"emitted blis_observe.{key}={value!r} would be rejected by manifest"
        )


def test_observe_defaults_covers_every_key_the_parser_can_produce():
    """The emitter iterates OBSERVE_DEFAULTS, so a key the parser produces but
    this dict omits is SILENTLY DISCARDED — no warning, exit 0. That is how
    #900's first pass regressed --api-format / --record-itl / --no-streaming,
    which previously survived into extraArgs."""
    producible = (set(g.OBSERVE_TUNING_FLAGS.values())
                  | {key for key, _ in g.OBSERVE_PRESENCE_FLAGS.values()}
                  | {"extraArgs"})
    missing = sorted(producible - set(g.OBSERVE_DEFAULTS))
    assert not missing, (
        f"parse_observe_block can produce {missing}, but OBSERVE_DEFAULTS omits "
        f"them so render_blis_observe_yaml drops them silently"
    )


def test_observe_defaults_matches_the_runtime_flag_table():
    """The skill and the pipeline must agree on the key set, or a generated
    transfer.yaml documents one thing while the pipeline runs another."""
    import pathlib
    import sys as _sys

    repo = pathlib.Path(__file__).resolve().parents[3]
    _sys.path.insert(0, str(repo))
    from pipeline.lib import observe_argv

    assert set(g.OBSERVE_DEFAULTS) == set(observe_argv.VALID_OBSERVE_KEYS)


def test_observe_defaults_values_match_the_runtime_defaults():
    """A value mismatch is subtler than a key mismatch and worse: the emitted
    bundle would record a default the renderer does not apply."""
    import pathlib
    import sys as _sys

    repo = pathlib.Path(__file__).resolve().parents[3]
    _sys.path.insert(0, str(repo))
    from pipeline.lib import observe_argv

    for key, spec in observe_argv.OBSERVE_FLAGS.items():
        got = g.OBSERVE_DEFAULTS[key]
        # The skill stores scalars as strings (it emits YAML text); the runtime
        # table stores native types. Compare through str() except for bools,
        # which must stay bools so the emitter renders real YAML booleans.
        if isinstance(spec.default, bool):
            assert got is spec.default, f"{key}: {got!r} vs {spec.default!r}"
        else:
            assert str(got) == str(spec.default), f"{key}: {got!r} vs {spec.default!r}"


def test_bool_keys_render_as_yaml_booleans():
    """A quoted "True" is a STRING to YAML, which manifest.py's per-key type
    check then rejects — so the bundle would fail to load, not merely mislead."""
    out = g.render_blis_observe_yaml({"recordItl": True, "streaming": False})
    assert "recordItl: true" in out
    assert "streaming: false" in out
    assert '"true"' not in out and '"True"' not in out


@pytest.mark.parametrize("form,expected", [
    ("--record-itl", {"recordItl": True}),
    ("--record-itl=true", {"recordItl": True}),
    ("--record-itl=1", {"recordItl": True}),
    # BoolVar's canonical negation must NEGATE, not assert.
    ("--record-itl=false", {"recordItl": False}),
    ("--record-itl=0", {"recordItl": False}),
    ("--no-streaming", {"streaming": False}),
    ("--no-streaming=true", {"streaming": False}),
    # "do NOT disable streaming" => streaming ON. The double negative is the
    # whole reason this needs a test rather than an eyeball.
    ("--no-streaming=false", {"streaming": True}),
    # strconv.ParseBool's single-letter spellings, which pflag also accepts.
    # Rejecting these while claiming pflag fidelity would be the same silent
    # default drift in miniature: blis takes `--record-itl=t` without complaint.
    ("--record-itl=t", {"recordItl": True}),
    ("--record-itl=T", {"recordItl": True}),
    ("--record-itl=f", {"recordItl": False}),
    ("--no-streaming=f", {"streaming": True}),
    ("--no-streaming=T", {"streaming": False}),
])
def test_boolean_flags_honor_pflag_negation(form, expected):
    """Both flags are registered with pflag BoolVar (observe_cmd.go:159,195), for
    which `--flag=false` is the canonical negation. Discarding the inline value
    silently inverted the author's intent."""
    text = "```bash\nblis observe \\\n  %s \\\n  --timeout 60\n```\n" % form
    parsed = g.parse_observe_block(text)
    for key, value in expected.items():
        assert parsed[key] is value, f"{form} gave {key}={parsed.get(key)!r}"


def test_unparseable_boolean_warns_and_drops(capsys):
    """Matches every other drop path in the function rather than guessing a
    polarity — a wrong guess here changes what the run measures."""
    text = "```bash\nblis observe \\\n  --record-itl=maybe\n```\n"
    parsed = g.parse_observe_block(text)
    assert "recordItl" not in parsed
    assert "WARNING" in capsys.readouterr().err


def test_no_streaming_is_inverted():
    """blis has no --streaming flag, so presence of --no-streaming means the
    bundle key `streaming` is FALSE. Getting this backwards would silently
    disable streaming on every run that mentioned the flag."""
    assert g.OBSERVE_PRESENCE_FLAGS["--no-streaming"] == ("streaming", False)
    assert g.OBSERVE_PRESENCE_FLAGS["--record-itl"] == ("recordItl", True)
