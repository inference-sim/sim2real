"""Guard issue #858: Task 5's build.commands guidance must stay component-driven.

The defect #858 fixed was a *fixed* command list -- the skill telling the agent what
to run instead of the agent discovering it from the component submodule. A hardcoded
list silently re-specialises the skill to one component (llm-d-router) and
reintroduces a gate with no linter, no race detector and no cache bypass.

`component.build.commands` has exactly one consumer: /sim2real-translate's writer
build/test gate. The pipeline only type-checks the key (pipeline/lib/manifest.py),
and `sim2real build` builds Dockerfile.epp, which runs no tests. So the list is the
translation's entire verification gate, and a weak list is a weak gate.

These tests are deliberately narrow: they assert over one bounded region of
SKILL.md -- Task 5's output-schema `build:` block. NL prose elsewhere in the skill
is not CI-tested (see test_byo.py's note on the NL frontend), but "no command
literals in the schema block" is mechanical, and it is the one invariant the issue
states outright: "They must not be hardcoded into the skill."
"""

import re
from pathlib import Path

_SKILL_MD = Path(__file__).resolve().parents[1] / "SKILL.md"

# Shell command literals whose presence would mean the skill, not the component, is
# choosing the gate. Deliberately covers several ecosystems: the point of #858 is
# that this field works for components other than llm-d-router.
_HARDCODED_COMMANDS = re.compile(
    r"\b(?:make\s+(?:build|test|lint|format|test-unit)"
    r"|go\s+(?:test|vet|build)"
    r"|golangci-lint\s+run"
    r"|npm\s+(?:test|run)"
    r"|cargo\s+(?:test|build))\b"
)


def _task5_build_block() -> str:
    """Return the ``build:`` mapping from Task 5's output-schema YAML block."""
    text = _SKILL_MD.read_text(encoding="utf-8")
    start = text.index("### Task 5: Create transfer.yaml")
    end = text.index("### Task 6:", start)
    task5 = text[start:end]

    # In the output schema, `build:` is indented two spaces under `component:`.
    # Its body is the more-indented lines that follow (blank lines allowed).
    m = re.search(r"\n  build:\n(?P<body>(?:    .*\n|\n)+)", task5)
    assert m, "Task 5 output schema has no `build:` block -- did the schema move?"
    return m.group("body")


def test_build_block_hardcodes_no_commands():
    """#858: the schema must not name commands; discovery supplies them."""
    found = _HARDCODED_COMMANDS.findall(_task5_build_block())
    assert not found, (
        f"Task 5's build: block hardcodes command literals {sorted(set(found))}. "
        "Issue #858 requires the list be discovered from the component submodule so "
        "it works for components other than llm-d-router. Describe how to discover "
        "them; do not name them."
    )


def test_build_block_still_documents_commands_key():
    """The block must keep the `commands:` key the manifest validates."""
    assert "commands:" in _task5_build_block(), (
        "Task 5's build: block no longer documents `commands:`, which "
        "pipeline/lib/manifest.py type-checks and /sim2real-translate consumes."
    )


def test_build_block_points_at_the_discovery_procedure():
    """A bare placeholder is what #858 removed -- the block must route to step 4."""
    assert "step 4" in _task5_build_block(), (
        "Task 5's build: block does not point the agent at derivation step 4, so "
        "the discovery procedure is reachable only by luck. A one-line placeholder "
        "here is the #858 defect."
    )
