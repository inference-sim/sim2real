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


def test_no_streaming_is_inverted():
    """blis has no --streaming flag, so presence of --no-streaming means the
    bundle key `streaming` is FALSE. Getting this backwards would silently
    disable streaming on every run that mentioned the flag."""
    assert g.OBSERVE_PRESENCE_FLAGS["--no-streaming"] == ("streaming", False)
    assert g.OBSERVE_PRESENCE_FLAGS["--record-itl"] == ("recordItl", True)
