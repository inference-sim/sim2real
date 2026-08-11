"""Guard the prompt/SKILL.md placeholder contract.

A prompt placeholder that SKILL.md forgets to substitute reaches the agent as a
literal `{BRACE}` string, which the agent then treats as a path. The failure is
silent, so it is worth a mechanical check.
"""

import re
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
PLACEHOLDER = re.compile(r"\{([A-Z][A-Z0-9_]*)\}")


def _documented() -> set[str]:
    text = (SKILL_DIR / "SKILL.md").read_text()
    start = text.index("### Placeholder substitution")
    section = text[start : text.index("### Run the three components", start)]
    return set(PLACEHOLDER.findall(section))


def _used() -> dict[str, set[str]]:
    return {
        p.name: set(PLACEHOLDER.findall(p.read_text()))
        for p in sorted((SKILL_DIR / "prompts").glob("*.md"))
    }


def test_every_prompt_placeholder_is_documented():
    documented = _documented()
    for name, used in _used().items():
        missing = used - documented
        assert not missing, f"{name} uses undocumented placeholders: {sorted(missing)}"


def test_no_documented_placeholder_is_unused():
    all_used = set().union(*_used().values())
    unused = _documented() - all_used
    assert not unused, f"SKILL.md documents unused placeholders: {sorted(unused)}"


def test_expected_placeholder_sets():
    used = _used()
    assert used["audit.md"] == {
        "ARM_FILE",
        "ARM_NAME",
        "BUNDLE_ROOT",
        "MAIN_SESSION_NAME",
        "SIM_PIN",
        "SIM_TREE",
        "TARGET_PIN",
        "TARGET_TREE",
        "VERDICT_PATH",
    }
    assert used["rederive.md"] == {
        "ARM_NAME",
        "DERIVATION_PATH",
        "MAIN_SESSION_NAME",
        "POLICY_ENTRY_POINTS",
        "SIM_PIN",
        "SIM_TREE",
    }


def test_both_prompts_are_present():
    """A renamed or deleted prompt must fail loudly, not silently shrink coverage."""
    assert set(_used()) == {"audit.md", "rederive.md"}
