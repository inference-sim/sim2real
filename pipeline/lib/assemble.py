"""Scenario assembly for llmdbenchmark-style baseline/treatment outputs.

Deep-merges bundle inputs (baseline.yaml, treatment.yaml) with skill-generated
overlay files (generated/baseline_config.yaml, generated/treatment_config.yaml)
to produce fully resolved scenario files.
"""
import yaml
from pathlib import Path

from pipeline.lib.values import deep_merge


class AssemblyError(Exception):
    """Raised when scenario assembly fails due to I/O or parse errors."""


def _load_yaml(path: Path) -> dict:
    """Load a YAML file with clear error attribution."""
    try:
        content = path.read_text()
    except OSError as exc:
        raise AssemblyError(f"Cannot read {path}: {exc}") from exc
    try:
        return yaml.safe_load(content) or {}
    except yaml.YAMLError as exc:
        raise AssemblyError(f"YAML parse error in {path}: {exc}") from exc


def assemble_scenarios(
    baseline_path: Path,
    treatment_path: Path | None,
    baseline_overlay_path: Path,
    treatment_overlay_path: Path,
) -> tuple[dict, dict]:
    """Assemble resolved baseline and treatment scenarios.

    Args:
        baseline_path: Path to baseline.yaml (bundle input).
        treatment_path: Path to treatment.yaml (bundle input, optional).
        baseline_overlay_path: Path to generated/baseline_config.yaml (skill output).
        treatment_overlay_path: Path to generated/treatment_config.yaml (skill output).

    Returns:
        (baseline_resolved, treatment_resolved) — two scenario dicts ready to
        be serialized as scenarioContent in PipelineRun params.

    Raises:
        AssemblyError: If any input file is unreadable or contains invalid YAML.
    """
    baseline = _load_yaml(baseline_path)
    baseline_overlay = _load_yaml(baseline_overlay_path) if baseline_overlay_path.exists() else {}
    baseline_resolved = deep_merge(baseline, baseline_overlay)

    treatment_diffs = {}
    if treatment_path is not None and treatment_path.exists():
        treatment_diffs = _load_yaml(treatment_path)

    treatment_overlay = _load_yaml(treatment_overlay_path) if treatment_overlay_path.exists() else {}

    treatment_resolved = deep_merge(baseline_resolved, treatment_diffs)
    treatment_resolved = deep_merge(treatment_resolved, treatment_overlay)

    return baseline_resolved, treatment_resolved
