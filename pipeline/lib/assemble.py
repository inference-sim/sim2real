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


def _align_overlay_name(base: dict, overlay: dict) -> dict:
    """Ensure overlay scenario name matches base to prevent list-merge duplication.

    The deep_merge list strategy merges by 'name' key. If the overlay has a
    different scenario name, it would be appended as a new entry instead of
    merged into the existing one.
    """
    base_scenarios = base.get("scenario", [])
    overlay_scenarios = overlay.get("scenario", [])
    if base_scenarios and overlay_scenarios:
        base_name = base_scenarios[0].get("name", "")
        if base_name and overlay_scenarios[0].get("name", "") != base_name:
            overlay_scenarios[0]["name"] = base_name
    return overlay


def assemble_scenarios(
    baseline_path: Path,
    treatment_path: Path | None,
    baseline_overlay_path: Path,
    treatment_overlay_path: Path,
    overlays_expected: bool = False,
) -> tuple[dict, dict]:
    """Assemble resolved baseline and treatment scenarios.

    Args:
        baseline_path: Path to baseline.yaml (bundle input).
        treatment_path: Path to treatment.yaml (bundle input, optional).
        baseline_overlay_path: Path to generated/baseline_config.yaml (skill output).
        treatment_overlay_path: Path to generated/treatment_config.yaml (skill output).
        overlays_expected: If True, raise AssemblyError when overlay files are missing.

    Returns:
        (baseline_resolved, treatment_resolved) — two scenario dicts ready to
        be serialized as scenarioContent in PipelineRun params.

    Raises:
        AssemblyError: If any input file is unreadable or contains invalid YAML,
            or if overlays_expected is True and overlay files are missing.
    """
    if overlays_expected:
        missing = []
        if not baseline_overlay_path.exists():
            missing.append(str(baseline_overlay_path))
        if not treatment_overlay_path.exists():
            missing.append(str(treatment_overlay_path))
        if missing:
            raise AssemblyError(
                f"Overlay files expected but missing: {', '.join(missing)}. "
                "Translation skill may have failed or written files to the wrong path."
            )

    baseline = _load_yaml(baseline_path)
    baseline_overlay = _load_yaml(baseline_overlay_path) if baseline_overlay_path.exists() else {}
    baseline_overlay = _align_overlay_name(baseline, baseline_overlay)
    baseline_resolved = deep_merge(baseline, baseline_overlay)

    treatment_diffs = {}
    if treatment_path is not None and treatment_path.exists():
        treatment_diffs = _load_yaml(treatment_path)

    treatment_overlay = _load_yaml(treatment_overlay_path) if treatment_overlay_path.exists() else {}
    treatment_overlay = _align_overlay_name(baseline_resolved, treatment_overlay)

    treatment_resolved = deep_merge(baseline_resolved, treatment_diffs)
    treatment_resolved = deep_merge(treatment_resolved, treatment_overlay)

    return baseline_resolved, treatment_resolved
