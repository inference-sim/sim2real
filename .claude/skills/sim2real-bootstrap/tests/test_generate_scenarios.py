"""Tests for generate_scenarios.py — the JSON-input baseline path.

This path had the identical single-role defect as generate_from_config.py and no
test file at all (issue #824). Covers:
  - `vllm_args.prefill_instances` emits a `prefill:` block with `enabled: true`
  - absent prefill input -> no `prefill:` anywhere in the output
  - `workload.prefill_hardware` overrides, otherwise prefill inherits the shared
    hardware
  - a hardware value naming several GPU types -> one type emitted plus a warning
  - `labelValues` never emitted
  - the two new fields are declared in KNOWN_FIELDS, so they do not trip the
    unknown-field warning that exists to catch unmapped input
"""
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parents[1]))
import generate_scenarios as gs


def entry(workload_extra: dict | None = None, vllm_extra: dict | None = None) -> dict:
    return {
        "workload": {
            "model": "Qwen/Qwen3-14B",
            "hardware": "H100_SXM_80GB",
            **(workload_extra or {}),
        },
        "vllm_args": {
            "num_instances": 2,
            "max_num_seqs": 256,
            **(vllm_extra or {}),
        },
    }


def emit(scenario: dict, src: dict, tmp_path: Path) -> str:
    out = tmp_path / "cand.yaml"
    gs.write_commented_yaml(scenario, src, str(out))
    return out.read_text()


# ---------------------------------------------------------------------------
# Prefill emission
# ---------------------------------------------------------------------------

def test_no_prefill_input_emits_no_prefill_block(tmp_path):
    src = entry()
    scenario = gs.build_scenario(src, "cand")
    assert "prefill" not in scenario
    assert "prefill:" not in emit(scenario, src, tmp_path)


def test_prefill_instances_emits_prefill_block(tmp_path):
    src = entry(vllm_extra={"prefill_instances": 1})
    scenario = gs.build_scenario(src, "cand")
    assert scenario["prefill"]["replicas"] == 1
    assert scenario["decode"]["replicas"] == 2
    assert "prefill:" in emit(scenario, src, tmp_path)


def test_prefill_block_carries_enabled_true(tmp_path):
    src = entry(vllm_extra={"prefill_instances": 1})
    scenario = gs.build_scenario(src, "cand")
    assert scenario["prefill"]["enabled"] is True
    parsed = yaml.safe_load(emit(scenario, src, tmp_path))
    assert parsed["scenario"][0]["prefill"]["enabled"] is True


def test_zero_prefill_instances_emits_nothing():
    """0 means aggregated, not 'a prefill pool of size zero'."""
    scenario = gs.build_scenario(entry(vllm_extra={"prefill_instances": 0}), "cand")
    assert "prefill" not in scenario


# ---------------------------------------------------------------------------
# Per-role accelerator
# ---------------------------------------------------------------------------

def test_prefill_hardware_overrides():
    src = entry(
        workload_extra={"prefill_hardware": "A100_SXM_80GB"},
        vllm_extra={"prefill_instances": 1},
    )
    scenario = gs.build_scenario(src, "cand")
    assert scenario["prefill"]["acceleratorType"]["labelValue"] == "NVIDIA-A100-SXM4-80GB"
    assert scenario["decode"]["acceleratorType"]["labelValue"] == "NVIDIA-H100-80GB-HBM3"


def test_prefill_inherits_shared_hardware():
    scenario = gs.build_scenario(entry(vllm_extra={"prefill_instances": 1}), "cand")
    assert scenario["prefill"]["acceleratorType"]["labelValue"] == "NVIDIA-H100-80GB-HBM3"


def test_prefill_inherits_parallelism_and_flags():
    src = entry(vllm_extra={"prefill_instances": 1, "tensor_parallel_size": 4})
    scenario = gs.build_scenario(src, "cand")
    assert scenario["prefill"]["parallelism"] == scenario["decode"]["parallelism"]
    assert scenario["prefill"]["vllm"] == scenario["decode"]["vllm"]


# ---------------------------------------------------------------------------
# Multi-GPU within one role
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", [
    "H100_SXM_80GB, A100_SXM_80GB",
    "H100_SXM_80GB / A100_SXM_80GB",
    "H100_SXM_80GB + A100_SXM_80GB",
    "H100_SXM_80GB and A100_SXM_80GB",
])
def test_multi_gpu_value_warns_and_uses_first(value, capsys):
    scenario = gs.build_scenario(entry(workload_extra={"hardware": value}), "cand")
    err = capsys.readouterr().err
    assert "WARNING" in err
    assert "H100_SXM_80GB" in err and "A100_SXM_80GB" in err
    assert "HOMOGENEOUS" in err
    assert scenario["decode"]["acceleratorType"]["labelValue"] == "NVIDIA-H100-80GB-HBM3"


def test_single_gpu_value_does_not_warn(capsys):
    gs.build_scenario(entry(), "cand")
    assert "WARNING" not in capsys.readouterr().err


def test_labelvalues_never_emitted(tmp_path):
    src = entry(
        workload_extra={"hardware": "H100_SXM_80GB, A100_SXM_80GB"},
        vllm_extra={"prefill_instances": 1},
    )
    scenario = gs.build_scenario(src, "cand")
    assert "labelValues" not in emit(scenario, src, tmp_path)


# ---------------------------------------------------------------------------
# Field registry
# ---------------------------------------------------------------------------

def test_new_fields_do_not_trip_unknown_field_warning():
    src = entry(
        workload_extra={"prefill_hardware": "A100_SXM_80GB"},
        vllm_extra={"prefill_instances": 1},
    )
    assert gs.check_unknown_fields(src, "cand") == []


def test_genuinely_unknown_field_still_warns():
    src = entry(vllm_extra={"not_a_real_knob": 1})
    warnings = gs.check_unknown_fields(src, "cand")
    assert any("not_a_real_knob" in w for w in warnings)


def test_emitted_yaml_round_trips_both_roles(tmp_path):
    src = entry(
        workload_extra={"prefill_hardware": "A100_SXM_80GB"},
        vllm_extra={"prefill_instances": 1, "tensor_parallel_size": 4},
    )
    scenario = gs.build_scenario(src, "cand")
    parsed = yaml.safe_load(emit(scenario, src, tmp_path))["scenario"][0]
    assert parsed["prefill"]["replicas"] == 1
    assert parsed["decode"]["replicas"] == 2
    assert parsed["prefill"]["parallelism"]["tensor"] == 4
