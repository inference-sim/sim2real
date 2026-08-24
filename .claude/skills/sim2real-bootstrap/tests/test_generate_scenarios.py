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


def test_prefill_hardware_without_instances_warns(capsys):
    """Declared in KNOWN_FIELDS so check_unknown_fields stays quiet, but unused
    without a count — a recognized input silently discarded."""
    src = entry(workload_extra={"prefill_hardware": "A100_SXM_80GB"})
    scenario = gs.build_scenario(src, "cand")
    err = capsys.readouterr().err
    assert "prefill" not in scenario
    assert "prefill_hardware" in err
    assert "NO effect" in err
    assert gs.check_unknown_fields(src, "cand") == []  # not an unknown-field case


def test_prefill_hardware_with_instances_does_not_warn(capsys):
    src = entry(
        workload_extra={"prefill_hardware": "A100_SXM_80GB"},
        vllm_extra={"prefill_instances": 1},
    )
    gs.build_scenario(src, "cand")
    assert "NO effect" not in capsys.readouterr().err


def test_genuinely_unknown_field_still_warns():
    src = entry(vllm_extra={"not_a_real_knob": 1})
    warnings = gs.check_unknown_fields(src, "cand")
    assert any("not_a_real_knob" in w for w in warnings)


# ---------------------------------------------------------------------------
# Provenance comments must name the key the value actually came from
# ---------------------------------------------------------------------------

def test_inherited_prefill_hardware_is_not_attributed_to_prefill_hardware(tmp_path):
    """`prefill_hardware` is optional; citing it when absent misattributes."""
    src = entry(vllm_extra={"prefill_instances": 1})
    assert "prefill_hardware" not in src["workload"]
    text = emit(gs.build_scenario(src, "cand"), src, tmp_path)
    prefill_block = text[text.index("prefill:"):]
    assert "from workload.hardware (lookup table; no prefill_hardware given)" in prefill_block
    assert "from workload.prefill_hardware" not in prefill_block


def test_explicit_prefill_hardware_is_attributed_to_it(tmp_path):
    src = entry(
        workload_extra={"prefill_hardware": "A100_SXM_80GB"},
        vllm_extra={"prefill_instances": 1},
    )
    text = emit(gs.build_scenario(src, "cand"), src, tmp_path)
    prefill_block = text[text.index("prefill:"):]
    assert "from workload.prefill_hardware (lookup table)" in prefill_block


# ---------------------------------------------------------------------------
# Unmapped GPU values are surfaced, not passed through in silence
# ---------------------------------------------------------------------------

def test_unmapped_hardware_warns(capsys):
    """An unmapped value becomes the node-selector verbatim; a selector that
    matches no node leaves pods Pending, so it must not be silent."""
    result = gs.resolve_role_hardware("H200_SXM_141GB", "decode")
    err = capsys.readouterr().err
    assert result == "H200_SXM_141GB"
    assert "not in HARDWARE_LABELS" in err
    assert "decode" in err


def test_mapped_hardware_does_not_warn(capsys):
    assert gs.resolve_role_hardware("H100_SXM_80GB", "decode") == "NVIDIA-H100-80GB-HBM3"
    assert "HARDWARE_LABELS" not in capsys.readouterr().err


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


# ---------------------------------------------------------------------------
# parallelism.workers (issue #831)
# ---------------------------------------------------------------------------

def test_workers_is_one_not_tensor_parallel_size():
    """`workers` is the LWS pods-per-replica count, not a parallelism degree."""
    scenario = gs.build_scenario(entry(vllm_extra={"tensor_parallel_size": 4}), "cand")
    p = scenario["decode"]["parallelism"]
    assert p["tensor"] == 4
    assert p["workers"] == 1


def test_prefill_workers_is_one():
    src = entry(vllm_extra={"prefill_instances": 1, "tensor_parallel_size": 4})
    scenario = gs.build_scenario(src, "cand")
    assert scenario["prefill"]["parallelism"]["tensor"] == 4
    assert scenario["prefill"]["parallelism"]["workers"] == 1


def test_workers_comment_does_not_cite_tensor_parallel_size(tmp_path):
    src = entry(vllm_extra={"tensor_parallel_size": 4})
    scenario = gs.build_scenario(src, "cand")
    text = emit(scenario, src, tmp_path)
    workers_lines = [ln for ln in text.splitlines() if ln.strip().startswith("workers:")]
    assert len(workers_lines) == 1
    assert "tensor_parallel_size" not in workers_lines[0]
    assert "pods per replica" in workers_lines[0]
    assert yaml.safe_load(text)["scenario"][0]["decode"]["parallelism"]["workers"] == 1


def test_dp_only_still_emits_workers_one():
    scenario = gs.build_scenario(entry(vllm_extra={"data_parallel_size": 2}), "cand")
    p = scenario["decode"]["parallelism"]
    assert p["tensor"] == 1
    assert p["workers"] == 1
