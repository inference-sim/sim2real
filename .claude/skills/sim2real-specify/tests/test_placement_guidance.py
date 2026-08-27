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
    """Without somewhere to go, "look elsewhere" is not actionable.

    This asserted a bare `#862` pointer when #859 added it, because the policy did
    not exist yet. #862 implemented it inline, so the guard now checks the policy
    itself -- a strictly stronger assertion than "an issue number is mentioned."
    """
    phase2 = _phase2()
    assert "DECLARED CORE MODIFICATION" in phase2, (
        "Phase 2 lost the DECLARED CORE MODIFICATION outcome. If no extension point "
        "fits at all, modifying the component's own code is the legitimate outcome "
        "(#862); without it the only documented move is to halt, which limits the "
        "pipeline to algorithms the component already anticipated."
    )


def test_phase2_halts_only_on_silent_fallback():
    """#862: the halt must be scoped to silent fallback, not to 'no extension point'.

    The original wording halted whenever no extension point could express the shape,
    which made a core modification unrepresentable. The halt's real purpose -- never
    quietly accept a decomposition that scores a different decision -- survives; what
    changed is that it no longer swallows the core-modification case.
    """
    phase2 = _phase2()
    assert "silent fallback" in phase2, (
        "Phase 2 no longer scopes its halt to silent fallback. #862 requires the "
        "halt cover the case where the weaker natural decomposition would be used "
        "instead, NOT every case where no extension point expresses the shape."
    )


def test_phase2_requires_the_rebase_cost_be_stated():
    """A carried patch invalidates the pin as a free variable; say what breaks."""
    phase2 = _phase2()
    assert "rebase" in phase2, (
        "Phase 2 no longer requires a declared core modification to state what "
        "breaks on upstream rebase. That is the cost most easily left implicit: the "
        "bundle now carries a patch, so the pinned ref is no longer free."
    )
