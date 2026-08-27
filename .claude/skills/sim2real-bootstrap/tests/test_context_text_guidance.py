"""Guard issue #859: Task 5's context.text guidance must keep its accuracy constraints.

`context.text` is substituted into the /sim2real-translate writer and reviewer prompts
as {CONTEXT_TEXT} and treated as authoritative for the science. Nothing downstream
checks it: no linter, no build gate, no pipeline validation. The field was specified as
`<derived summary>`, which imposed no accuracy requirement, and two defects were
therefore within spec -- a citation range that stopped short of the construct it named,
and a placement asserted as settled that could not be built at all (two interfaces
declaring the same method name with different signatures cannot sit on one Go type).

Two bounded-region guards here, mirroring test_build_commands_guidance.py (#858):

- the schema block must not regress to a bare placeholder, and must route to the
  derivation step where the constraints live;
- the derivation step must keep both constraints -- citation bounding, and the
  settled-vs-declared-unknown split.

Prose beyond these regions is not asserted on (see test_byo.py's note on why NL
frontends are not CI-tested); what is asserted is that the two constraints the issue
states outright have not been quietly dropped.
"""

import re
from pathlib import Path

_SKILL_MD = Path(__file__).resolve().parents[1] / "SKILL.md"

# The placeholder #859 retired. Its return would mean the constraints were dropped.
_RETIRED_PLACEHOLDER = "<derived summary>"


def _task5() -> str:
    """Return the Task 5 section of SKILL.md."""
    text = _SKILL_MD.read_text(encoding="utf-8")
    start = text.index("### Task 5: Create transfer.yaml")
    return text[start : text.index("### Task 6:", start)]


def _task5_context_block() -> str:
    """Return the ``context:`` mapping from Task 5's output-schema YAML block."""
    m = re.search(r"\ncontext:\n(?P<body>(?:[ #].*\n|\n)+)", _task5())
    assert m, "Task 5 output schema has no `context:` block -- did the schema move?"
    return m.group("body")


def _task5_step9() -> str:
    """Return derivation step 9 (`context.text`) up to the start of step 10."""
    task5 = _task5()
    m = re.search(
        r"\n9\. `context\.text`:(?P<body>.*?)(?=\n10\. `blis_observe`)",
        task5,
        re.S,
    )
    assert m, "Task 5 derivation step 9 (`context.text`) not found -- did it move?"
    return m.group("body")


def test_context_block_is_not_a_bare_placeholder():
    """#859: `<derived summary>` imposed no accuracy requirement and was retired."""
    assert _RETIRED_PLACEHOLDER not in _task5_context_block(), (
        f"Task 5's context: block is back to the bare {_RETIRED_PLACEHOLDER!r} "
        "placeholder. Issue #859 replaced it because it imposed no accuracy "
        "requirement on a field /sim2real-translate treats as authoritative."
    )


def test_context_block_points_at_the_derivation_step():
    """The block must route the agent to where the constraints are stated."""
    assert "step 9" in _task5_context_block(), (
        "Task 5's context: block does not point at derivation step 9, so the "
        "accuracy constraints are reachable only by luck."
    )


def test_step9_requires_citations_to_bound_their_construct():
    """#859 constraint 1: a citation must cover the whole construct it names."""
    step9 = _task5_step9()
    assert "bound the construct" in step9, (
        "Task 5 step 9 no longer requires citations to bound the construct they "
        "name. lint_citations.py cannot catch this -- it checks that lines fall "
        "inside the file, not that a range bounds what it describes."
    )


def test_step9_separates_settled_eliminations_from_declared_placement():
    """#859 constraint 2: the two claim kinds must not share one voice."""
    step9 = _task5_step9()
    missing = [
        term
        for term in ("Elimination", "declared unknown", "registration")
        if term not in step9
    ]
    assert not missing, (
        f"Task 5 step 9 dropped {missing} from the context.text guidance. #859 "
        "requires eliminations (settled) to be written in a different voice from "
        "the proposed placement (a declared unknown until its interface set is "
        "confirmed constructible), and requires the registration count to be stated."
    )
