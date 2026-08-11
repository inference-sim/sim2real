"""Guard the config.md contract against drift in its downstream consumer.

Phase 5 tells the author which heading to put the vLLM pod configuration table
under. `/sim2real-bootstrap` decides which headings it will actually look for.
Those two live in different skills, so nothing but a test keeps them agreeing —
and when they disagree the bundle parses as "no vLLM configuration table" and
bootstrap Task 3 halts, which is the regression this test exists to prevent
(issue #821 amendment).

The field list itself is deliberately NOT duplicated into SKILL.md, so there is
nothing to check for it here. The heading is the one string SKILL.md must state
literally, which makes it the one string that can drift.
"""

import importlib.util
import re
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parents[1]
BOOTSTRAP_GENERATOR = (
    SKILL_DIR.parent / "sim2real-bootstrap" / "generate_from_config.py"
)


def _bootstrap_module():
    """Load bootstrap's generator as a module, or skip if it is not checked out."""
    if not BOOTSTRAP_GENERATOR.exists():
        pytest.skip(f"bootstrap generator not present at {BOOTSTRAP_GENERATOR}")
    spec = importlib.util.spec_from_file_location(
        "_bootstrap_generate_from_config", BOOTSTRAP_GENERATOR
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _config_contract_section() -> str:
    text = (SKILL_DIR / "SKILL.md").read_text()
    start = text.index("### What `config.md` must contain")
    return text[start : text.index("### Deployment values", start)]


def test_prescribed_heading_is_one_bootstrap_recognizes():
    keywords = [k.lower() for k in _bootstrap_module().VLLM_SECTION_KEYWORDS]
    section = _config_contract_section()

    headings = re.findall(r"`(##+ [^`]+)`", section)
    assert headings, "SKILL.md's config.md contract names no heading in backticks"

    for heading in headings:
        title = heading.lstrip("#").strip().lower()
        if any(keyword in title for keyword in keywords):
            return
    pytest.fail(
        f"none of the headings SKILL.md prescribes {headings} contains a "
        f"keyword bootstrap searches for {keywords}"
    )


def test_contract_names_the_mandatory_fields():
    """`Model` and `GPU` are bootstrap's only hard requirements — say so."""
    section = _config_contract_section()
    assert "`Model`" in section and "`GPU`" in section, (
        "the contract must name the two fields bootstrap treats as mandatory"
    )


def test_mandatory_fields_are_still_the_ones_bootstrap_requires():
    """If bootstrap's required set changes, the prose above goes stale."""
    source = BOOTSTRAP_GENERATOR.read_text() if BOOTSTRAP_GENERATOR.exists() else ""
    if not source:
        pytest.skip("bootstrap generator not present")
    required = set(re.findall(r"required field '(\w+)'", source))
    assert required == {"model", "hardware"}, (
        f"bootstrap's required fields changed to {sorted(required)}; update the "
        "config.md contract in SKILL.md to match"
    )


def test_confirm_sentinel_is_the_documented_convention():
    """`08203b4` used `**CONFIRM**`; a second marker convention would fork it."""
    text = (SKILL_DIR / "SKILL.md").read_text()
    start = text.index("### Deployment values the simulation cannot supply")
    section = text[start : text.index("## Phase 6", start)]
    assert "**CONFIRM**" in section
    for invented in ("TBD-OPERATOR", "TODO-OPERATOR", "FIXME"):
        assert invented not in section, (
            f"{invented} competes with the CONFIRM convention already in use"
        )
