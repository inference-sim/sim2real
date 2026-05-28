"""Tests for SKILL.md placeholder substitution — verifies {ALGO_NAME} is present."""
from pathlib import Path

import pytest


SKILL_DIR = Path(__file__).parents[1]


def test_skill_md_contains_algo_name_placeholder():
    """SKILL.md must reference {ALGO_NAME} in the substitution table."""
    content = (SKILL_DIR / "SKILL.md").read_text()
    assert "{ALGO_NAME}" in content
    assert "CURRENT_ALGORITHM" in content


def test_writer_prompt_uses_algo_name():
    """agent-writer.md must use {ALGO_NAME} for treatment config paths."""
    content = (SKILL_DIR / "prompts" / "agent-writer.md").read_text()
    assert "{ALGO_NAME}/{ALGO_NAME}_config.yaml" in content
    assert "{ALGO_NAME}/{ALGO_NAME}_output.json" in content
    # Must NOT reference flat treatment_config.yaml as an output target
    lines = content.splitlines()
    for i, line in enumerate(lines, 1):
        if "generated/treatment_config.yaml" in line and "legacy" not in line.lower():
            pytest.fail(
                f"agent-writer.md line {i} still references flat "
                f"treatment_config.yaml: {line.strip()}"
            )


def test_reviewer_prompt_uses_algo_name():
    """agent-reviewer.md must use {ALGO_NAME} for treatment config paths."""
    content = (SKILL_DIR / "prompts" / "agent-reviewer.md").read_text()
    assert "{ALGO_NAME}/{ALGO_NAME}_config.yaml" in content
    assert "{ALGO_NAME}/{ALGO_NAME}_output.json" in content
    # Must NOT reference flat treatment_config.yaml as a read target
    lines = content.splitlines()
    for i, line in enumerate(lines, 1):
        if "generated/treatment_config.yaml" in line and "legacy" not in line.lower():
            pytest.fail(
                f"agent-reviewer.md line {i} still references flat "
                f"treatment_config.yaml: {line.strip()}"
            )


def test_skill_md_copy_generated_passes_algo_name():
    """copy_generated call in SKILL.md must pass algo_name parameter."""
    content = (SKILL_DIR / "SKILL.md").read_text()
    assert "algo_name='$CURRENT_ALGORITHM'" in content


def test_skill_md_requires_current_algorithm_field():
    """skill_input.json validation must require current_algorithm."""
    content = (SKILL_DIR / "SKILL.md").read_text()
    assert "'current_algorithm'" in content
