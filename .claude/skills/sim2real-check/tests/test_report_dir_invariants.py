"""Guard issue #882: the report directory name is coupled to the phase-discovery
denylist, and nothing else enforces that coupling.

Step 0's legacy-mode branch falls back to ``RESULTS_DIR="$REAL"`` when a bundle has
no ``results/`` subdirectory. The phase scan then runs over the bundle root -- the
same level the report directory sits on -- so a report directory whose name is not
on the denylist is discovered as a *phase*. A bundle checked once would grow a bogus
phase on the next run, silently corrupting every downstream per-phase table.

That is why ``review`` was already on the list before #882, and it is why the
directory name and the ``case`` arm must move together. The skill says so in a
comment, but a comment does not fail CI. These tests do.

Deliberately narrow, following the precedent in
``.claude/skills/sim2real-bootstrap/tests/test_build_commands_guidance.py``: they
assert over two bounded regions of SKILL.md (the Step 0 ``case`` arm and the
Operating Constraints block) rather than over prose generally. The invariant is
mechanical -- the name in one place must appear in the other -- even though most of
this skill is natural language and not CI-tested.
"""

import re
from pathlib import Path

_SKILL_MD = Path(__file__).resolve().parents[1] / "SKILL.md"


def _skill_text() -> str:
    return _SKILL_MD.read_text(encoding="utf-8")


def _denylist_arm() -> str:
    """Return the ``case`` arm listing well-known non-phase directory names."""
    text = _skill_text()
    match = re.search(r"^\s*([a-z_|]*\|[a-z_|]*)\)\s*;;\s*$", text, re.MULTILINE)
    assert match, "Step 0 phase-discovery denylist `case` arm not found in SKILL.md"
    return match.group(1)


def _operating_constraints() -> str:
    """Return the Operating Constraints section body."""
    text = _skill_text()
    start = text.index("## Operating Constraints")
    end = text.index("## Evidence Requirements", start)
    return text[start:end]


class TestReportDirIsDenylisted:
    def test_report_dir_name_is_on_the_phase_denylist(self):
        """The directory Operating Constraints writes to must be denylisted.

        Derives the name from Operating Constraints rather than hardcoding it, so
        a rename that updates only one of the two places fails here.
        """
        constraints = _operating_constraints()
        names = set(re.findall(r"/(\w+)/<UTC timestamp>/", constraints))
        assert names, (
            "Operating Constraints does not name a report directory in the form "
            "`<parent>/<name>/<UTC timestamp>/` — if the report path changed, "
            "update this test and the Step 0 denylist together"
        )
        arm = _denylist_arm()
        denylisted = set(arm.split("|"))
        missing = sorted(names - denylisted)
        assert not missing, (
            f"report directory name(s) {missing} are not on the Step 0 "
            f"phase-discovery denylist ({arm}). In a flat legacy bundle "
            "RESULTS_DIR falls back to $REAL, so the scan covers the report "
            "directory's own level and would treat it as a phase."
        )

    def test_review_stays_denylisted(self):
        """`review` predates #882 and may still exist in bundles checked before
        the rename to `check`. Dropping it would make those bundles grow a phase."""
        assert "review" in _denylist_arm().split("|")


class TestReportDirIsNotUnderResults:
    def test_constraints_forbid_writing_under_results(self):
        assert "Never write under" in _operating_constraints()

    def test_report_path_is_not_nested_under_results(self):
        """`results/<something>/check/` would be scanned as a phase in *both*
        modes, not just flat-legacy — the denylist would not save it."""
        constraints = _operating_constraints()
        assert not re.search(r"results/\S*/\w+/<UTC timestamp>", constraints), (
            "report directory must be a sibling of results/, never nested under it"
        )


class TestSubagentWriteGuidance:
    def test_heredoc_not_write_tool(self):
        constraints = _operating_constraints()
        assert "heredoc" in constraints
        assert "`Write`" in constraints

    def test_first_chunk_truncates(self):
        """`cat >>` for the first chunk means a retry after a partial write
        appends a second copy of the section. The guidance must say `cat >`
        first, `cat >>` after."""
        constraints = _operating_constraints()
        assert 'cat > "$file"' in constraints
        assert 'cat >> "$file"' in constraints

    def test_verification_is_required(self):
        assert "verify the file on" in _operating_constraints()


class TestResolveModeUsesRunDirFromResolveOutput:
    def test_run_dir_captured_from_resolve_json(self):
        """`$RUN_DIR` must come from the resolve output, not be rebuilt from
        EXPERIMENT_ROOT/RUN — that would duplicate layout.runs_dir()."""
        text = _skill_text()
        assert "RUN_DIR=$(jq -re '.run_dir' \"$RESOLVED_JSON\")" in text

    def test_constraints_reference_the_variable(self):
        assert "$RUN_DIR/check/" in _operating_constraints()
