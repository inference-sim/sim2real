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

This file guards translate's whole core-modification contract, which has two ends:

- the WRITER may modify component code, and must say which files, why no extension point
  sufficed, and what upstream change would invalidate it;
- the REVIEWER must judge whether the modification was DECLARED rather than treating its
  presence as a defect -- and must not stop at Criterion 3 when the registration chain
  passes, since that says nothing about the files the port also touched.

A core modification SUPPLEMENTS the plugin; it does not replace it. An earlier draft let a
port register no plugin at all, which deadlocked review -- Criteria 4, 5, 6 and the
APPROVE template all still require a registered plugin -- and was scope #862 never asked
for. It is also the only shape the treatment overlay can express: the overlay enables the
algorithm by naming its plugin type, so an unregistered port cannot be switched on for the
treatment scenario. The core edit provides the hook; the plugin provides the switch.

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

    Markdown emphasis is stripped for the same reason: `modifies *different*
    files` should satisfy a guard on `modifies different files`, since the
    asterisks are formatting, not content. Only `*` is removed -- `_` has to
    survive for identifiers like `files_modified`.
    """
    return " ".join(text.replace("*", "").split())


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


def test_writer_must_still_register_a_plugin():
    """A core modification supplements the plugin; it never replaces it.

    An earlier draft told the writer it could register none and set plugin_type /
    register_file to empty strings. That contradicted Phase 3 and Phase 4 of this same
    prompt (which unconditionally require a Type constant, a Factory, registration, and
    a pluginsCustomConfig reference) and it could not pass review, since Criteria 4, 5,
    6 and the APPROVE template all require a registered plugin.
    """
    section = _core_mod_section()
    assert "must still register a plugin" in section, (
        "The core-modification section no longer requires the port to register a "
        "plugin. #862 (option B): the core edit provides the hook, the plugin provides "
        "the switch -- the treatment overlay enables the algorithm by naming its plugin "
        "type, so an unregistered port cannot be switched on at all."
    )
    assert "empty string" not in section, (
        "The core-modification section is back to telling the writer it may set "
        "plugin_type / register_file to empty strings. That reintroduces the "
        "plugin-less port, which Phase 3, Phase 4, Criteria 4/5/6 and the APPROVE "
        "template all still forbid."
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


def test_reviewer_registration_applies_to_every_port():
    """Criterion 3 is unconditional: a core modification supplements the plugin.

    An earlier draft of #862 scoped Criterion 3 so a plugin-less port was deferred to
    3b. That created a deadlock elsewhere -- Criteria 4, 5, 6 and the APPROVE template
    all still require a registered plugin, so such a port could never earn a conforming
    APPROVE -- and it was scope this issue never asked for. The rule is now that a core
    modification supplements the plugin rather than replacing it, which is also the only
    shape the treatment overlay can express: it enables the algorithm by naming its
    plugin type, so an unregistered port cannot be switched on at all.
    """
    m = re.search(
        r"\n### Criterion 3: Registration[^\n]*\n(?P<body>.*?)(?=\nVerify the complete)",
        _reviewer(),
        re.S,
    )
    assert m, "Criterion 3 has no preamble -- did the heading or the chain intro change?"
    body = _norm(m.group("body"))
    assert "every port" in body, (
        "Criterion 3's preamble no longer says it applies to every port. #862 (option "
        "B): a core modification supplements the plugin, so the registration chain "
        "always applies."
    )
    assert "inapplicable" not in body, (
        "Criterion 3's preamble is back to declaring itself inapplicable for a "
        "plugin-less port. That reopens the deadlock: Criteria 4/5/6 and the APPROVE "
        "template still require a registered plugin, so such a port cannot pass review."
    )


def test_reviewer_3b_supplements_rather_than_replaces_criterion_3():
    """A passing registration chain must not excuse the declaration checks.

    The hazard is an agent that sees Criterion 3 pass and stops, never checking whether
    the component files the port ALSO touched were declared -- which is precisely what
    3b exists to catch. An earlier draft said every port lands in "exactly one of the
    three", which licensed exactly that mistake.
    """
    m = re.search(
        r"\n### Criterion 3b:[^\n]*\n(?P<body>.*?)(?=\n### )", _reviewer(), re.S
    )
    assert m, "Criterion 3b section body not found -- did the heading change?"
    body = _norm(m.group("body"))
    assert "never a substitute" in body, (
        "Criterion 3b no longer states it is additional to Criterion 3 rather than a "
        "substitute for it, so a port owing both may be checked for only one."
    )
    assert "never stop at Criterion 3" in body, (
        "Criterion 3b lost the explicit instruction not to stop at Criterion 3 once "
        "the registration chain passes. That is the both-at-once case, and it is the "
        "common one."
    )
    assert "exactly one of the three" not in body, (
        "Criterion 3b claims every port lands in 'exactly one of the three'. That is "
        "false when a port registers a plugin AND modifies component files, and it "
        "licenses skipping the declaration checks whenever registration passes."
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
    # "fidelity" alone is NOT sufficient here: the closing undeclared-modification
    # clause also supplies that word, so deleting item 4 -- the check that the modified
    # files match a prior {CONTEXT_TEXT} declaration -- would leave the guard green.
    # Each phrase below is unique to the item it protects.
    missing = [
        t
        for t in (
            "files_modified",                      # the applicability condition
            "upstream",                            # item 2, the rebase cost
            "no larger than the decision requires",  # item 3, minimal surface
            "different files than the declaration",  # item 4, matches the declaration
            "not silent",                          # the framing
        )
        if t not in body
    ]
    assert not missing, (
        f"Criterion 3b dropped {missing}. It must key off files_modified, require the "
        "upstream-rebase cost, hold the change to the decision's minimum, check the "
        "modified files against any prior declaration, and insist the modification be "
        "declared rather than silent."
    )
