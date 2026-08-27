"""Guard Phase 2's placement-confidence rules against drift (issue #859).

Phase 2 decides where the algorithm plugs into the component, and
`/sim2real-bootstrap` copies that conclusion into `transfer.yaml:context.text`,
from which `/sim2real-translate`'s writer reads it as authoritative. So this is
the origin of the claim, and a wrong claim here propagates through two more
skills before a compiler contradicts it.

The defect #859 documents happened exactly that way: Phase 2's gate asked only
whether an extension point could *express* the shape, a placement naming two
interfaces that share a method name was asserted flatly, and the writer designed
a port around it before the collision surfaced as a build error.

Nothing downstream can catch that, so these are the guards: the four rules the
fix added to Phase 2 must not be silently dropped by a later edit. Bounded to the
Phase 2 section, mirroring `sim2real-bootstrap/tests/test_context_text_guidance.py`.
"""

import re
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
_SKILL_MD = SKILL_DIR / "SKILL.md"


def _phase2() -> str:
    """Return the Phase 2 section of specify's SKILL.md."""
    text = _SKILL_MD.read_text(encoding="utf-8")
    m = re.search(
        r"\n## Phase 2 [^\n]*\n(?P<body>.*?)(?=\n## Phase 3 )",
        text,
        re.S,
    )
    assert m, "specify SKILL.md has no Phase 2 section -- did the heading change?"
    return m.group("body")


def test_phase2_distinguishes_expressible_from_constructible():
    """The gate must not collapse back to expressibility alone."""
    phase2 = _phase2()
    assert "constructible" in phase2, (
        "Phase 2 no longer distinguishes expressible from constructible. #859: "
        "citations showing a value is reachable do not show that one Go type can "
        "carry the interface set being named."
    )


def test_phase2_requires_placement_stated_as_candidate():
    """Placement is a declared unknown until its method sets are checked."""
    phase2 = _phase2()
    missing = [t for t in ("declared unknown", "candidate") if t not in phase2]
    assert not missing, (
        f"Phase 2 dropped {missing}. #859 requires the placement be written as a "
        "candidate / declared unknown rather than asserted, because bootstrap "
        "copies it into context.text and the writer treats it as authoritative."
    )


def test_phase2_requires_registration_count():
    """\"A custom X plus a custom Y\" is ambiguous about how many types."""
    assert "registration" in _phase2(), (
        "Phase 2 no longer requires the number of plugin registrations to be "
        "stated. #859: the ambiguity between one type implementing both and two "
        "separate registrations is what got resolved the cheap way."
    )


def test_phase2_keeps_the_core_modification_escape_hatch():
    """Without somewhere to go, \"look elsewhere\" is not actionable."""
    assert "#862" in _phase2(), (
        "Phase 2 lost its pointer to #862. If no extension point fits at all, a "
        "core modification is the legitimate outcome; without that pointer the "
        "only documented move is to halt."
    )
