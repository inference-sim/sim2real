"""Unit tests for manifest loader."""

import pytest
from pathlib import Path
import yaml

from lib.manifest import load_manifest, set_nested_path, ManifestError


def _write_manifest(tmp_path: Path, overrides: dict = None) -> Path:
    """Write a minimal valid manifest YAML for testing.

    Args:
        tmp_path: pytest tmp_path fixture
        overrides: Dict of dot-separated paths to override or remove (None value = delete)

    Returns:
        Path to the written manifest file
    """
    manifest = {
        'kind': 'sim2real-transfer',
        'version': 1,
        'algorithm': {
            'experiment_dir': 'test_algo',
            'source': 'best/best_program.go',
            'info': 'best/best_program_info.json',
            'workloads': 'workloads/',
            'llm_config': 'llm_config.yaml',
        },
        'target': {
            'repo': 'target-repo',
            'plugin_dir': 'pkg/plugins/scorer/',
            'register_file': 'pkg/plugins/register.go',
            'package': 'scorer',
            'naming': {
                'suffix': '-scorer',
            },
            'test_commands': [
                ['go', 'build', './...'],
            ],
        },
        'context': {
            'mapping': 'docs/mapping.md',
            'template': 'docs/template.go.md',
        },
        'config': {
            'env_defaults': 'config/env_defaults.yaml',
            'treatment_config_template': 'apiVersion: test',
            'helm_path': 'gaie.treatment.config',
        },
        'validation': {
            'mode': 'latency_comparison',
        },
    }

    # Apply overrides
    if overrides:
        for path, value in overrides.items():
            if value is None:
                # Delete the key
                parts = path.split('.')
                current = manifest
                for part in parts[:-1]:
                    if part not in current:
                        break
                    current = current[part]
                else:
                    current.pop(parts[-1], None)
            else:
                # Set the value
                parts = path.split('.')
                current = manifest
                for part in parts[:-1]:
                    if part not in current:
                        current[part] = {}
                    current = current[part]
                current[parts[-1]] = value

    manifest_path = tmp_path / 'transfer.yaml'
    with open(manifest_path, 'w') as f:
        yaml.dump(manifest, f)

    return manifest_path


def test_load_valid_manifest(tmp_path):
    """Test loading a valid manifest returns expected structure."""
    manifest_path = _write_manifest(tmp_path)
    manifest = load_manifest(manifest_path)

    assert manifest['kind'] == 'sim2real-transfer'
    assert manifest['algorithm']['experiment_dir'] == 'test_algo'
    assert manifest['target']['repo'] == 'target-repo'
    assert manifest['context']['mapping'] == 'docs/mapping.md'
    assert manifest['config']['env_defaults'] == 'config/env_defaults.yaml'
    assert manifest['validation']['mode'] == 'latency_comparison'


def test_missing_target_repo_raises(tmp_path):
    """Test that missing target.repo raises ManifestError."""
    manifest_path = _write_manifest(tmp_path, overrides={'target.repo': None})

    with pytest.raises(ManifestError, match='target.repo'):
        load_manifest(manifest_path)


def test_defaults_applied(tmp_path):
    """Test that default values are applied for missing fields."""
    manifest_path = _write_manifest(tmp_path)
    manifest = load_manifest(manifest_path)

    assert manifest['artifacts']['plugin_snapshot'] == 'prepare_plugin.go'
    assert manifest['algorithm']['baseline'] is None
    assert manifest['target']['equivalence_commands'] == []
    assert manifest['validation']['evaluator'] == ''
    assert manifest['validation']['metrics'] == []
    assert manifest['context']['examples'] == []
    assert manifest['context']['extra'] == []
    assert manifest['config']['baseline_config_template'] == ''
    assert manifest['algorithm']['extras'] == []
    assert manifest['artifacts']['plugin_test_snapshot'] == 'prepare_plugin_test.go'
    assert manifest['artifacts']['stage3_output'] == 'prepare_stage3_output.json'


def test_custom_evaluator_requires_evaluator(tmp_path):
    """Test that custom_evaluator mode without evaluator field raises error."""
    manifest_path = _write_manifest(
        tmp_path,
        overrides={'validation.mode': 'custom_evaluator'}
    )

    with pytest.raises(ManifestError, match='evaluator'):
        load_manifest(manifest_path)


def test_set_nested_path(tmp_path):
    """Test set_nested_path builds full path in empty dict."""
    obj = {}
    set_nested_path(obj, 'a.b.c', 'value')

    assert obj == {'a': {'b': {'c': 'value'}}}


def test_set_nested_path_existing(tmp_path):
    """Test set_nested_path preserves existing keys at intermediate levels."""
    obj = {'a': {'x': 'preserved', 'b': {'y': 'also_preserved'}}}
    set_nested_path(obj, 'a.b.c', 'new_value')

    assert obj == {
        'a': {
            'x': 'preserved',
            'b': {
                'y': 'also_preserved',
                'c': 'new_value',
            },
        },
    }


def test_invalid_kind_raises(tmp_path):
    """Test that invalid kind field raises ManifestError."""
    manifest_path = _write_manifest(tmp_path, overrides={'kind': 'wrong-kind'})

    with pytest.raises(ManifestError, match='kind'):
        load_manifest(manifest_path)
