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
    return m.group("body")


def test_writer_job_is_not_defined_as_plugin_only():
    """#862: the deliverable is a faithful port, not a plugin specifically."""
    # The opening job statement, before any section heading.
    intro = _writer().split("\n## ", 1)[0]
    assert "into a production Go plugin," not in intro, (
        "agent-writer.md's job statement is back to 'translate ... into a production "
        "Go plugin'. #862: an agent told to produce a plugin will not weigh modifying "
        "core code even though nothing forbids it and the pipeline supports it."
    )


def test_writer_may_modify_component_code():
    """The section must grant the licence, not merely mention the possibility."""
    section = _core_mod_section()
    assert "permitted" in _writer(), (
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
    assert "inapplicable" in m.group("body"), (
        "Criterion 3's preamble no longer says the registration chain is inapplicable "
        "when the port registers no plugin, so a declared core modification would be "
        "rejected on a criterion it cannot satisfy."
    )


def test_reviewer_checks_the_declaration_not_the_modification():
    """A core modification is not a defect; an undeclared one is."""
    reviewer = _reviewer()
    assert "Criterion 3b" in reviewer, (
        "agent-reviewer.md lost Criterion 3b. #862 requires the reviewer to judge "
        "whether a core modification is DECLARED, rather than treating the presence "
        "of files_modified as a defect."
    )
    m = re.search(
        r"\n### Criterion 3b:[^\n]*\n(?P<body>.*?)(?=\n### )", reviewer, re.S
    )
    assert m, "Criterion 3b section body not found -- did the heading change?"
    body = m.group("body")
    missing = [
        t for t in ("files_modified", "upstream", "not silent", "fidelity") if t not in body
    ]
    assert not missing, (
        f"Criterion 3b dropped {missing}. It must key off files_modified, require the "
        "upstream-rebase cost, insist the modification be declared rather than silent, "
        "and raise a fidelity failure when it is not."
    )
