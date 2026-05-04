"""Tests for scenario assembly (baseline/treatment merge logic)."""
import yaml

from pipeline.lib.assemble import assemble_scenarios


BASELINE = {
    "scenario": [{
        "name": "admission-control",
        "model": {"name": "Qwen/Qwen3-14B", "maxModelLen": 40960},
        "images": {
            "vllm": {"repository": "ghcr.io/llm-d/llm-d-cuda", "tag": "v0.6.0"},
            "inferenceScheduler": {"repository": "ghcr.io/llm-d/llm-d-inference-scheduler", "tag": "v0.7.1"},
        },
        "inferenceExtension": {
            "verbosity": "3",
            "inferencePoolProviderConfig": {"destinationRule": {"trafficPolicy": {}}},
        },
        "decode": {"replicas": 4},
    }],
}

BASELINE_OVERLAY = {
    "scenario": [{
        "name": "admission-control",
        "inferenceExtension": {
            "pluginsConfigFile": "custom-plugins.yaml",
            "pluginsCustomConfig": {
                "custom-plugins.yaml": "kind: EndpointPickerConfig\nplugins:\n- type: random-picker\n",
            },
        },
        "extraObjects": [
            {"apiVersion": "v1alpha2", "kind": "InferenceObjective", "metadata": {"name": "critical"}, "spec": {"priority": 100}},
        ],
    }],
}

TREATMENT_DIFFS = {
    "scenario": [{
        "name": "admission-control",
        "images": {
            "inferenceScheduler": {"repository": "ghcr.io/kalantar/llm-d-inference-scheduler", "tag": "ac"},
        },
    }],
}

TREATMENT_OVERLAY = {
    "scenario": [{
        "name": "admission-control",
        "inferenceExtension": {
            "pluginsConfigFile": "custom-plugins.yaml",
            "pluginsCustomConfig": {
                "custom-plugins.yaml": "kind: EndpointPickerConfig\nplugins:\n- type: quintic-shed\n",
            },
        },
    }],
}


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data, default_flow_style=False))


def test_baseline_merge(tmp_path):
    """Baseline + baseline_overlay produces merged scenario with plugin config and extraObjects."""
    _write(tmp_path / "baseline.yaml", BASELINE)
    _write(tmp_path / "generated" / "baseline_config.yaml", BASELINE_OVERLAY)
    _write(tmp_path / "generated" / "treatment_config.yaml", {})

    bl, _ = assemble_scenarios(
        baseline_path=tmp_path / "baseline.yaml",
        treatment_path=None,
        baseline_overlay_path=tmp_path / "generated" / "baseline_config.yaml",
        treatment_overlay_path=tmp_path / "generated" / "treatment_config.yaml",
    )

    sc = bl["scenario"][0]
    assert sc["inferenceExtension"]["pluginsConfigFile"] == "custom-plugins.yaml"
    assert "custom-plugins.yaml" in sc["inferenceExtension"]["pluginsCustomConfig"]
    assert sc["extraObjects"][0]["kind"] == "InferenceObjective"
    assert sc["model"]["name"] == "Qwen/Qwen3-14B"


def test_treatment_merge(tmp_path):
    """Treatment = baseline + treatment diffs + treatment overlay."""
    _write(tmp_path / "baseline.yaml", BASELINE)
    _write(tmp_path / "treatment.yaml", TREATMENT_DIFFS)
    _write(tmp_path / "generated" / "baseline_config.yaml", BASELINE_OVERLAY)
    _write(tmp_path / "generated" / "treatment_config.yaml", TREATMENT_OVERLAY)

    bl, tr = assemble_scenarios(
        baseline_path=tmp_path / "baseline.yaml",
        treatment_path=tmp_path / "treatment.yaml",
        baseline_overlay_path=tmp_path / "generated" / "baseline_config.yaml",
        treatment_overlay_path=tmp_path / "generated" / "treatment_config.yaml",
    )

    bl_sc = bl["scenario"][0]
    tr_sc = tr["scenario"][0]

    assert bl_sc["images"]["inferenceScheduler"]["tag"] == "v0.7.1"
    assert tr_sc["images"]["inferenceScheduler"]["tag"] == "ac"
    assert tr_sc["images"]["inferenceScheduler"]["repository"] == "ghcr.io/kalantar/llm-d-inference-scheduler"

    assert "random-picker" in bl_sc["inferenceExtension"]["pluginsCustomConfig"]["custom-plugins.yaml"]
    assert "quintic-shed" in tr_sc["inferenceExtension"]["pluginsCustomConfig"]["custom-plugins.yaml"]

    assert tr_sc["model"]["name"] == "Qwen/Qwen3-14B"
    assert tr_sc["decode"]["replicas"] == 4


def test_absent_treatment_uses_baseline(tmp_path):
    """When treatment.yaml is absent, treatment == baseline + treatment overlay."""
    _write(tmp_path / "baseline.yaml", BASELINE)
    _write(tmp_path / "generated" / "baseline_config.yaml", BASELINE_OVERLAY)
    _write(tmp_path / "generated" / "treatment_config.yaml", TREATMENT_OVERLAY)

    bl, tr = assemble_scenarios(
        baseline_path=tmp_path / "baseline.yaml",
        treatment_path=None,
        baseline_overlay_path=tmp_path / "generated" / "baseline_config.yaml",
        treatment_overlay_path=tmp_path / "generated" / "treatment_config.yaml",
    )

    assert bl["scenario"][0]["images"]["inferenceScheduler"]["tag"] == "v0.7.1"
    assert tr["scenario"][0]["images"]["inferenceScheduler"]["tag"] == "v0.7.1"
    assert "quintic-shed" in tr["scenario"][0]["inferenceExtension"]["pluginsCustomConfig"]["custom-plugins.yaml"]


def test_missing_overlays_passthrough(tmp_path):
    """When overlay files don't exist, baseline passes through unchanged."""
    _write(tmp_path / "baseline.yaml", BASELINE)

    bl, tr = assemble_scenarios(
        baseline_path=tmp_path / "baseline.yaml",
        treatment_path=None,
        baseline_overlay_path=tmp_path / "generated" / "baseline_config.yaml",
        treatment_overlay_path=tmp_path / "generated" / "treatment_config.yaml",
    )

    assert bl["scenario"][0]["model"]["name"] == "Qwen/Qwen3-14B"
    assert bl == tr


def test_overlay_preserves_existing_fields(tmp_path):
    """Overlay adds fields without clobbering unrelated existing fields."""
    _write(tmp_path / "baseline.yaml", BASELINE)
    _write(tmp_path / "generated" / "baseline_config.yaml", BASELINE_OVERLAY)
    _write(tmp_path / "generated" / "treatment_config.yaml", {})

    bl, _ = assemble_scenarios(
        baseline_path=tmp_path / "baseline.yaml",
        treatment_path=None,
        baseline_overlay_path=tmp_path / "generated" / "baseline_config.yaml",
        treatment_overlay_path=tmp_path / "generated" / "treatment_config.yaml",
    )

    sc = bl["scenario"][0]
    assert sc["inferenceExtension"]["verbosity"] == "3"
    assert sc["inferenceExtension"]["inferencePoolProviderConfig"] == {"destinationRule": {"trafficPolicy": {}}}
    assert sc["images"]["vllm"]["tag"] == "v0.6.0"
