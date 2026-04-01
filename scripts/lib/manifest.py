"""Manifest loader for sim2real pipeline."""

from pathlib import Path
from typing import Any
import yaml


class ManifestError(Exception):
    """Exception raised for manifest validation errors."""
    pass


def load_manifest(path: Path) -> dict:
    """Load and validate a sim2real transfer manifest.

    Args:
        path: Path to the manifest YAML file

    Returns:
        Validated manifest dict with defaults applied

    Raises:
        ManifestError: If validation fails or required fields are missing
    """
    try:
        with open(path, 'r') as f:
            manifest = yaml.safe_load(f)
    except FileNotFoundError:
        raise ManifestError(f"Manifest file not found: {path}")
    except yaml.YAMLError as e:
        raise ManifestError(f"Invalid YAML in manifest: {e}")

    if not isinstance(manifest, dict):
        raise ManifestError("Manifest must be a YAML object")

    # Validate kind
    if manifest.get('kind') != 'sim2real-transfer':
        raise ManifestError(f"Invalid kind: expected 'sim2real-transfer', got '{manifest.get('kind')}'")

    # Required fields
    required_fields = [
        'algorithm.experiment_dir',
        'algorithm.source',
        'algorithm.info',
        'algorithm.workloads',
        'algorithm.llm_config',
        'target.repo',
        'target.plugin_dir',
        'target.register_file',
        'target.package',
        'target.naming.suffix',
        'context.mapping',
        'context.template',
        'config.env_defaults',
        'config.treatment_config_template',
        'config.helm_path',
        'validation.mode',
    ]

    for field in required_fields:
        if not _has_nested_path(manifest, field):
            raise ManifestError(f"Missing required field: {field}")

    # Validate custom evaluator mode
    validation_mode = _get_nested_path(manifest, 'validation.mode')
    if validation_mode == 'custom_evaluator':
        evaluator = _get_nested_path(manifest, 'validation.evaluator', default='')
        if not evaluator:
            raise ManifestError("validation.mode='custom_evaluator' requires validation.evaluator to be a non-empty string")

    # Apply defaults
    defaults = {
        'algorithm.baseline': None,
        'algorithm.extras': [],
        'target.equivalence_commands': [],
        'context.examples': [],
        'context.extra': [],
        'config.baseline_config_template': '',
        'validation.evaluator': '',
        'validation.metrics': [],
        'artifacts.plugin_snapshot': 'prepare_plugin.go',
        'artifacts.plugin_test_snapshot': 'prepare_plugin_test.go',
        'artifacts.stage3_output': 'prepare_stage3_output.json',
    }

    for dotted_path, default_value in defaults.items():
        if not _has_nested_path(manifest, dotted_path):
            set_nested_path(manifest, dotted_path, default_value)

    return manifest


def set_nested_path(obj: dict, dotted_path: str, value: Any) -> None:
    """Set a value in a nested dict using dot-separated path.

    Args:
        obj: Dictionary to modify
        dotted_path: Dot-separated path (e.g., "a.b.c")
        value: Value to set at the path
    """
    parts = dotted_path.split('.')
    current = obj

    # Navigate to the parent of the final key
    for part in parts[:-1]:
        if part not in current:
            current[part] = {}
        elif not isinstance(current[part], dict):
            current[part] = {}
        current = current[part]

    # Set the final key
    current[parts[-1]] = value


def _has_nested_path(obj: dict, dotted_path: str) -> bool:
    """Check if a nested path exists in a dict.

    Args:
        obj: Dictionary to check
        dotted_path: Dot-separated path

    Returns:
        True if path exists, False otherwise
    """
    parts = dotted_path.split('.')
    current = obj

    for part in parts:
        if not isinstance(current, dict) or part not in current:
            return False
        current = current[part]

    return True


def _get_nested_path(obj: dict, dotted_path: str, default=None) -> Any:
    """Get a value from a nested dict using dot-separated path.

    Args:
        obj: Dictionary to query
        dotted_path: Dot-separated path
        default: Default value if path doesn't exist

    Returns:
        Value at path or default
    """
    parts = dotted_path.split('.')
    current = obj

    for part in parts:
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]

    return current
