"""Guard the writer's licence to modify component code (issue #862).

The pipeline already carried core modifications end to end before #862: the writer's
output has a `files_modified` field, `copy_generated.py` fills it from a `git diff` of
the component checkout, `source_toggle` reverts those files, and `build-epp.sh` uploads
the whole checkout as the build context so a modified core file actually compiles.

What was missing was permission. Nothing forbade it, but the writer's job was *defined*
as producing "a production Go plugin", and an agent told to produce a plugin produces a
plugin -- it will not weigh changing core code, because that is not the deliverable it
was handed. So the constraint was the job description, and these guards protect the
widened one.

This file guards translate's whole core-modification contract, which has two ends that
only work together:

- the WRITER may act, and must say why;
- the REVIEWER must not mandatorily reject it. Criterion 3 requires a five-item plugin
  registration chain and says to raise NEEDS_CHANGES if any item is missing. A port that
  modifies core code without registering a plugin fails that unconditionally, so
  permitting the writer without scoping the reviewer would deadlock the review loop --
  the writer has a finite retry budget and the rejection would be mandatory.

The upstream half lives in sim2real-specify/tests/test_placement_guidance.py, which
guards Phase 2's DECLARED CORE MODIFICATION outcome.
"""

import re
from pathlib import Path

_PROMPTS = Path(__file__).resolve().parents[1] / "prompts"
_WRITER = _PROMPTS / "agent-writer.md"
_REVIEWER = _PROMPTS / "agent-reviewer.md"


def _norm(text: str) -> str:
    """Collapse whitespace runs to single spaces.

    These files are hard-wrapped prose, so a guarded phrase can land across a line
    break -- `declared core\\n modification` is not the substring `declared core
    modification`. A guard that fails on reflow says nothing about whether the rule
    survived, so only the words are compared, never the wrapping.
    """
    return " ".join(text.split())


def _writer() -> str:
    return _WRITER.read_text(encoding="utf-8")


def _reviewer() -> str:
    return _REVIEWER.read_text(encoding="utf-8")


def _core_mod_section() -> str:
    """Return the 'Modifying component code' section of the writer prompt."""
    m = re.search(
        r"\n## Modifying component code\n(?P<body>.*?)(?=\n## )",
        _writer(),
        re.S,
    )
    assert m, (
        "agent-writer.md has no 'Modifying component code' section -- #862 added it "
        "to give the writer explicit licence; without it the writer falls back to "
        "treating a plugin as the only permitted deliverable."
    )
    return _norm(m.group("body"))


def test_writer_job_is_not_defined_as_plugin_only():
    """#862: the deliverable is a faithful port, not a plugin specifically."""
    # The opening job statement, before any section heading.
    intro = _norm(_writer().split("\n## ", 1)[0])
    assert "into a production Go plugin," not in intro, (
        "agent-writer.md's job statement is back to 'translate ... into a production "
        "Go plugin'. #862: an agent told to produce a plugin will not weigh modifying "
        "core code even though nothing forbids it and the pipeline supports it."
    )


def test_writer_may_modify_component_code():
    """The section must grant the licence, not merely mention the possibility."""
    section = _core_mod_section()
    assert "permitted" in _norm(_writer()), (
        "agent-writer.md no longer states that modifying component code is permitted."
    )
    assert "extension point" in section, (
        "The core-modification section no longer frames an extension point as the "
        "first choice. #862 changes the fallback, not the default -- the target is "
        "plugin-based and a plugin keeps the bundle free of a carried patch."
    )


def test_writer_must_state_why_and_what_breaks():
    """files_modified is generated; the reason is not."""
    section = _core_mod_section()
    missing = [
        term
        for term in ("files_modified", "why no extension point", "upstream")
        if term not in section
    ]
    assert not missing, (
        f"The core-modification section dropped {missing}. #862 requires the writer to "
        "state which files changed, why no extension point sufficed, and what upstream "
        "change would invalidate it -- a modification appearing only as a file list is "
        "indistinguishable from an accident."
    )


def test_writer_defers_to_a_declaration_already_in_context():
    """When specify already planned it, the writer implements rather than re-derives."""
    section = _core_mod_section()
    assert "CONTEXT_TEXT" in section, (
        "The core-modification section no longer tells the writer what to do when "
        "{CONTEXT_TEXT} already declares a core modification. /sim2real-specify "
        "records these in the specification layer's header, and that declaration is "
        "the plan -- the writer should implement it, not re-litigate its necessity."
    )


def test_reviewer_registration_criterion_is_scoped():
    """Criterion 3 must not mandatorily reject a port that registers no plugin.

    Without this scoping the two prompts deadlock: the writer is permitted to modify
    core code, and the reviewer is required to raise NEEDS_CHANGES when the five-item
    registration chain is incomplete -- which it always is for a pure core modification.
    """
    reviewer = _reviewer()
    m = re.search(
        r"\n### Criterion 3: Registration[^\n]*\n(?P<body>.*?)(?=\nVerify the complete)",
        reviewer,
        re.S,
    )
    assert m, (
        "Criterion 3 has no scope preamble. #862: a port that registers no plugin "
        "fails its five-item chain unconditionally, so the criterion must say it "
        "governs the registered plugin and point at the core-modification check."
    )
    assert "inapplicable" in _norm(m.group("body")), (
        "Criterion 3's preamble no longer says the registration chain is inapplicable "
        "when the port registers no plugin, so a declared core modification would be "
        "rejected on a criterion it cannot satisfy."
    )


def test_reviewer_has_no_gap_between_criterion_3_and_3b():
    """No port may escape both registration criteria.

    Criterion 3 hands the plugin-less case to 3b, and 3b's substantive checks key off
    a non-empty files_modified. Their intersection -- no plugin AND no modification --
    would satisfy neither criterion's applicability condition, so a port delivering
    nothing would draw no NEEDS_CHANGES from either. Before #862 scoped Criterion 3,
    that case failed the registration chain unconditionally, so the gap is a
    regression this guard exists to prevent reopening.

    Note the criteria are independent, NOT alternatives: a port that registers a
    plugin AND modifies component files owes both. An earlier draft of this docstring
    and of the prompt said every port lands in "exactly one of the three", which is
    wrong and dangerous in the common both-at-once case -- an agent could classify
    such a port under Criterion 3, stop, and never apply 3b's declaration checks.
    That is the undeclared-modification hazard 3b exists to catch, so the guard below
    also pins the applicability table.
    """
    m = re.search(
        r"\n### Criterion 3b:[^\n]*\n(?P<body>.*?)(?=\n### )", _reviewer(), re.S
    )
    assert m, "Criterion 3b section body not found -- did the heading change?"
    body = _norm(m.group("body"))
    assert "no deliverable" in body, (
        "Criterion 3b lost its no-deliverable catch-all. A port with no registered "
        "plugin and an empty files_modified now falls through both registration "
        "criteria, which is the hole #862 opened by scoping Criterion 3."
    )
    # Guard the second operand too, so a rewording raises this message rather than a
    # bare ValueError from .index().
    gate_marker = "The rest of this criterion"
    assert gate_marker in body, (
        f"Criterion 3b no longer contains {gate_marker!r}, so the ordering check "
        "below cannot locate the files_modified gate. Reword the assertion along "
        "with the prompt."
    )
    # The catch-all must come before the files_modified-gated checks, or a
    # plugin-less, modification-less port never reaches it.
    assert body.index("no deliverable") < body.index(gate_marker), (
        "The no-deliverable check must be the FIRST thing in Criterion 3b. Placed "
        "after the files_modified gate it is unreachable for exactly the case it "
        "is meant to catch."
    )
    # The criteria are independent, not alternatives. "exactly one of the three"
    # invites an agent to stop at Criterion 3 for a port that also modified files.
    assert "exactly one of the three" not in body, (
        "Criterion 3b claims every port lands in 'exactly one of the three'. That is "
        "false for the common case of a plugin PLUS a core modification, where both "
        "criteria apply -- and it licenses skipping 3b's declaration checks whenever "
        "the registration chain passes."
    )
    assert "not alternatives" in body, (
        "Criterion 3b no longer states that Criteria 3 and 3b are independent rather "
        "than alternatives, so a port owing both may be checked for only one."
    )


def test_reviewer_checks_the_declaration_not_the_modification():
    """A core modification is not a defect; an undeclared one is."""
    reviewer = _reviewer()
    assert "Criterion 3b" in _norm(reviewer), (
        "agent-reviewer.md lost Criterion 3b. #862 requires the reviewer to judge "
        "whether a core modification is DECLARED, rather than treating the presence "
        "of files_modified as a defect."
    )
    m = re.search(
        r"\n### Criterion 3b:[^\n]*\n(?P<body>.*?)(?=\n### )", reviewer, re.S
    )
    assert m, "Criterion 3b section body not found -- did the heading change?"
    body = _norm(m.group("body"))
    missing = [
        t for t in ("files_modified", "upstream", "not silent", "fidelity") if t not in body
    ]
    assert not missing, (
        f"Criterion 3b dropped {missing}. It must key off files_modified, require the "
        "upstream-rebase cost, insist the modification be declared rather than silent, "
        "and raise a fidelity failure when it is not."
    )
