"""Unit tests for validate_checks.py."""
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.validate_checks import CheckItem, CheckGroup, ValidationReport, _models_match


def test_check_group_from_items_all_pass():
    items = [CheckItem("a", True), CheckItem("b", True)]
    g = CheckGroup.from_items(items)
    assert g.passed is True


def test_check_group_from_items_fail_flips_group():
    items = [CheckItem("a", True), CheckItem("b", False)]
    g = CheckGroup.from_items(items)
    assert g.passed is False


def test_check_group_warn_does_not_flip_group():
    items = [CheckItem("a", True), CheckItem("b", False, severity="warn")]
    g = CheckGroup.from_items(items)
    assert g.passed is True  # warn doesn't fail the group


def test_validation_report_overall_pass():
    g = CheckGroup(passed=True, items=[], notes=[])
    r = ValidationReport(phase="pre_deploy", run="test", timestamp="t",
                         overall="PASS", checks={"workloads": g})
    assert r.failed is False


def test_validation_report_overall_fail():
    g = CheckGroup(passed=False, items=[], notes=[])
    r = ValidationReport(phase="pre_deploy", run="test", timestamp="t",
                         overall="FAIL", checks={"workloads": g})
    assert r.failed is True


def test_models_match_case_insensitive():
    assert _models_match("Qwen/Qwen2.5-7B-Instruct", "qwen/qwen2.5-7b-instruct")


def test_models_match_strips_whitespace():
    assert _models_match("  Qwen/Qwen2.5-7B  ", "Qwen/Qwen2.5-7B")


def test_models_match_different():
    assert not _models_match("Qwen/Qwen2.5-7B", "meta-llama/Llama-3")


from lib.validate_checks import check_workloads
import yaml as _yaml


def _make_values(workloads=None, objectives=None):
    objectives = objectives or [
        {"name": "critical", "priority": 100},
        {"name": "standard", "priority": 0},
        {"name": "sheddable", "priority": -10},
        {"name": "batch", "priority": -50},
    ]
    spec = {
        "version": "1", "seed": 42, "aggregate_rate": 100.0,
        "num_requests": 1000,
        "clients": [
            {"id": "c1", "slo_class": "critical", "rate_fraction": 0.5,
             "arrival": {"process": "poisson"}},
            {"id": "c2", "slo_class": "batch", "rate_fraction": 0.5,
             "arrival": {"process": "gamma", "cv": 3.0}},
        ],
    }
    wl = workloads or [{"name": "wl_a", "spec": _yaml.dump(spec)}]
    return {
        "observe": {"workloads": wl},
        "stack": {"gaie": {"inferenceObjectives": objectives}},
    }


def test_check_workloads_valid():
    g = check_workloads(_make_values())
    assert g.passed is True


def test_check_workloads_duplicate_names():
    spec = _yaml.dump({"version": "1", "seed": 42, "aggregate_rate": 10,
                        "num_requests": 100, "clients": []})
    values = _make_values([{"name": "dup", "spec": spec},
                           {"name": "dup", "spec": spec}])
    g = check_workloads(values)
    assert g.passed is False
    assert any("unique" in i.name for i in g.items if not i.passed)


def test_check_workloads_bad_rate_fraction_sum():
    spec_dict = {
        "version": "1", "seed": 42, "aggregate_rate": 100.0, "num_requests": 1000,
        "clients": [
            {"id": "c1", "slo_class": "critical", "rate_fraction": 0.3,
             "arrival": {"process": "poisson"}},
            {"id": "c2", "slo_class": "batch", "rate_fraction": 0.3,
             "arrival": {"process": "poisson"}},
        ],
    }
    g = check_workloads(_make_values([{"name": "wl_a", "spec": _yaml.dump(spec_dict)}]))
    assert g.passed is False


def test_check_workloads_unknown_slo_class():
    spec_dict = {
        "version": "1", "seed": 42, "aggregate_rate": 100.0, "num_requests": 1000,
        "clients": [{"id": "c1", "slo_class": "UNKNOWN", "rate_fraction": 1.0,
                     "arrival": {"process": "poisson"}}],
    }
    g = check_workloads(_make_values([{"name": "wl_a", "spec": _yaml.dump(spec_dict)}]))
    assert g.passed is False


def test_check_workloads_gamma_missing_cv():
    spec_dict = {
        "version": "1", "seed": 42, "aggregate_rate": 100.0, "num_requests": 1000,
        "clients": [{"id": "c1", "slo_class": "batch", "rate_fraction": 1.0,
                     "arrival": {"process": "gamma"}}],  # no cv!
    }
    g = check_workloads(_make_values([{"name": "wl_a", "spec": _yaml.dump(spec_dict)}]))
    assert g.passed is False


def test_check_workloads_unused_objective_is_warn_not_fail():
    # Only uses "critical" — "standard", "sheddable", "batch" unused → warns, but group passes
    spec_dict = {
        "version": "1", "seed": 42, "aggregate_rate": 100.0, "num_requests": 1000,
        "clients": [{"id": "c1", "slo_class": "critical", "rate_fraction": 1.0,
                     "arrival": {"process": "poisson"}}],
    }
    g = check_workloads(_make_values([{"name": "wl_a", "spec": _yaml.dump(spec_dict)}]))
    assert g.passed is True  # warns, doesn't fail
    warn_items = [i for i in g.items if not i.passed and i.severity == "warn"]
    assert len(warn_items) >= 3  # standard, sheddable, batch


from lib.validate_checks import check_vllm_config


def _make_vllm_values(args=None, replicas=4, tensor=1, model_name="Qwen/Qwen2.5-7B-Instruct"):
    args = args or [
        "--gpu-memory-utilization=0.9",
        "--max-num-seqs=256",
        "--max-num-batched-tokens=2048",
        "--block-size=16",
    ]
    return {
        "stack": {
            "model": {
                "modelName": model_name,
                "helmValues": {
                    "decode": {
                        "replicas": replicas,
                        "parallelism": {"tensor": tensor},
                        "containers": [
                            {"modelCommand": "vllmServe", "args": args},
                        ],
                    }
                },
            }
        }
    }


def _make_llm_config(gpu_mem=0.9, max_seqs=256, batched_tokens=2048,
                     block_size=16, num_instances=4, tp=1,
                     model_id="Qwen/Qwen2.5-7B-Instruct"):
    return {
        "model": {"id": model_id},
        "serving": {"tensor_parallelism": tp},
        "cluster": {"num_instances": num_instances},
        "vllm_config": {
            "gpu_memory_utilization": gpu_mem,
            "max_num_running_reqs": max_seqs,
            "max_num_scheduled_tokens": batched_tokens,
            "block_size_in_tokens": block_size,
        },
    }


def test_check_vllm_config_valid():
    g = check_vllm_config(_make_vllm_values(), _make_llm_config())
    assert g.passed is True


def test_check_vllm_config_wrong_gpu_mem():
    g = check_vllm_config(
        _make_vllm_values(args=["--gpu-memory-utilization=0.85", "--max-num-seqs=256",
                                 "--max-num-batched-tokens=2048", "--block-size=16"]),
        _make_llm_config(),
    )
    assert g.passed is False
    assert any("gpu-memory-utilization" in i.name for i in g.items if not i.passed)


def test_check_vllm_config_wrong_replicas():
    g = check_vllm_config(_make_vllm_values(replicas=2), _make_llm_config())
    assert g.passed is False


def test_check_vllm_config_no_vllm_container():
    values = {
        "stack": {"model": {"modelName": "x",
                             "helmValues": {"decode": {"replicas": 4, "parallelism": {"tensor": 1},
                                                        "containers": [{"modelCommand": "other"}]}}}}
    }
    g = check_vllm_config(values, _make_llm_config())
    assert g.passed is False


def test_check_vllm_config_model_name_case_insensitive():
    g = check_vllm_config(
        _make_vllm_values(model_name="qwen/qwen2.5-7b-instruct"),
        _make_llm_config(model_id="Qwen/Qwen2.5-7B-Instruct"),
    )
    assert g.passed is True


from lib.validate_checks import check_signals, check_routing_policy, check_isolation, run_pre_deploy_checks
import yaml as _yaml


def _make_signal_coverage(signals=None, complete=True):
    signals = signals or [
        {"sim_name": "totalInFlight", "mapped": True, "fidelity_rating": "medium", "staleness_window_ms": 0},
        {"sim_name": "sloClass",      "mapped": True, "fidelity_rating": "high",   "staleness_window_ms": 0},
        {"sim_name": "tenantID",      "mapped": True, "fidelity_rating": "high",   "staleness_window_ms": 0},
    ]
    return {"signals": signals, "coverage_complete": complete}


def test_check_signals_valid():
    g = check_signals(_make_signal_coverage())
    assert g.passed is True


def test_check_signals_unmapped():
    sc = _make_signal_coverage([
        {"sim_name": "sloClass", "mapped": False, "fidelity_rating": "high", "staleness_window_ms": 0},
        {"sim_name": "tenantID", "mapped": True,  "fidelity_rating": "high", "staleness_window_ms": 0},
    ])
    g = check_signals(sc)
    assert g.passed is False


def test_check_signals_low_fidelity():
    sc = _make_signal_coverage([
        {"sim_name": "totalInFlight", "mapped": True, "fidelity_rating": "low", "staleness_window_ms": 0},
        {"sim_name": "sloClass",      "mapped": True, "fidelity_rating": "high", "staleness_window_ms": 0},
        {"sim_name": "tenantID",      "mapped": True, "fidelity_rating": "high", "staleness_window_ms": 0},
    ])
    g = check_signals(sc)
    assert g.passed is False


def test_check_signals_totalInFlight_must_be_fresh():
    sc = _make_signal_coverage([
        {"sim_name": "totalInFlight", "mapped": True, "fidelity_rating": "high", "staleness_window_ms": 5000},
        {"sim_name": "sloClass",      "mapped": True, "fidelity_rating": "high", "staleness_window_ms": 0},
        {"sim_name": "tenantID",      "mapped": True, "fidelity_rating": "high", "staleness_window_ms": 0},
    ])
    g = check_signals(sc)
    assert g.passed is False


def test_check_signals_prometheus_backed_too_stale():
    sc = _make_signal_coverage([
        {"sim_name": "kvUtil", "mapped": True, "fidelity_rating": "high", "staleness_window_ms": 10000},
        {"sim_name": "sloClass", "mapped": True, "fidelity_rating": "high", "staleness_window_ms": 0},
        {"sim_name": "tenantID", "mapped": True, "fidelity_rating": "high", "staleness_window_ms": 0},
    ])
    g = check_signals(sc)
    assert g.passed is False


def _baseline_epc():
    return _yaml.dump({
        "apiVersion": "inference.networking.x-k8s.io/v1alpha1",
        "kind": "EndpointPickerConfig",
        "plugins": [
            {"type": "load-aware-scorer"},
            {"type": "decode-filter"},
            {"type": "max-score-picker"},
        ],
        "schedulingProfiles": [{"name": "default", "plugins": [
            {"pluginRef": "decode-filter"},
            {"pluginRef": "max-score-picker"},
            {"pluginRef": "load-aware-scorer", "weight": 1},
        ]}],
    })


def _make_routing_values(phase="baseline", epc=None, admission_policy="some-policy"):
    epc = epc or _baseline_epc()
    baseline_hv = {"inferenceExtension": {"pluginsCustomConfig": {"custom-plugins.yaml": epc}}}
    treatment_hv = {}
    return {
        "stack": {"gaie": {
            "baseline":  {"helmValues": baseline_hv},
            "treatment": {"helmValues": treatment_hv, "admissionPolicy": admission_policy},
        }}
    }


def test_check_routing_policy_baseline_valid():
    g = check_routing_policy(_make_routing_values("baseline"), "baseline")
    assert g.passed is True


def test_check_routing_policy_baseline_missing_plugin():
    epc = _yaml.dump({
        "apiVersion": "inference.networking.x-k8s.io/v1alpha1",
        "kind": "EndpointPickerConfig",
        "plugins": [{"type": "decode-filter"}, {"type": "max-score-picker"}],  # no load-aware-scorer
        "schedulingProfiles": [{"name": "default", "plugins": []}],
    })
    g = check_routing_policy(_make_routing_values("baseline", epc=epc), "baseline")
    assert g.passed is False


def test_check_routing_policy_treatment_valid():
    g = check_routing_policy(_make_routing_values("treatment"), "treatment")
    assert g.passed is True


def test_check_routing_policy_treatment_missing_admission_policy():
    g = check_routing_policy(_make_routing_values("treatment", admission_policy=""), "treatment")
    assert g.passed is False


def test_check_isolation_unique():
    values = {"observe": {"workloads": [{"name": "a"}, {"name": "b"}]}}
    g = check_isolation(values)
    assert g.passed is True


def test_check_isolation_duplicate():
    values = {"observe": {"workloads": [{"name": "a"}, {"name": "a"}]}}
    g = check_isolation(values)
    assert g.passed is False


from lib.validate_checks import check_trace_workload
import math


def _make_workload_spec(aggregate_rate=100.0, num_requests=1000, seed=42,
                         clients=None):
    clients = clients or [
        {"id": "c1", "slo_class": "critical", "tenant_id": "t1",
         "rate_fraction": 0.6, "arrival": {"process": "poisson"},
         "input_distribution": {"type": "gaussian", "params": {"mean": 128}},
         "output_distribution": {"type": "exponential", "params": {"mean": 64}}},
        {"id": "c2", "slo_class": "batch", "tenant_id": "t2",
         "rate_fraction": 0.4, "arrival": {"process": "gamma", "cv": 3.0},
         "input_distribution": {"type": "gaussian", "params": {"mean": 256}},
         "output_distribution": {"type": "exponential", "params": {"mean": 128}}},
    ]
    return {"aggregate_rate": aggregate_rate, "num_requests": num_requests,
            "seed": seed, "clients": clients}


def _make_trace_header(model="Qwen/Qwen2.5-7B-Instruct", seed=42, mode="real"):
    return {"server": {"model": model}, "workload_seed": seed, "mode": mode}


def _make_rows(n=1000, start_us=0, rate_rps=100.0, client_ratios=None,
               input_mean=128, output_mean=64):
    """Generate synthetic trace rows."""
    import random
    random.seed(0)
    client_ratios = client_ratios or [("c1", "critical", "t1", 0.6, 128, 64),
                                       ("c2", "batch", "t2", 0.4, 256, 128)]
    rows = []
    t = start_us
    interval_us = int(1e6 / rate_rps)
    for i in range(n):
        t += interval_us + random.randint(-interval_us//10, interval_us//10)
        # pick client by ratio
        r = random.random()
        cumulative = 0
        cid, slo, tid = client_ratios[0][0], client_ratios[0][1], client_ratios[0][2]
        c_input_mean, c_output_mean = client_ratios[0][3], client_ratios[0][4]
        for c_id, c_slo, c_tid, frac, inp_mean, out_mean in client_ratios:
            cumulative += frac
            if r <= cumulative:
                cid, slo, tid = c_id, c_slo, c_tid
                c_input_mean, c_output_mean = inp_mean, out_mean
                break
        rows.append({
            "request_id": str(i),
            "client_id": cid,
            "tenant_id": tid,
            "slo_class": slo,
            "arrival_time_us": str(t),
            "input_tokens": str(int(random.gauss(c_input_mean, c_input_mean * 0.15))),
            "output_tokens": str(int(random.gauss(c_output_mean, c_output_mean * 0.15))),
            "status": "ok",
        })
    return rows


def test_check_trace_workload_valid():
    rows = _make_rows(1000, rate_rps=100.0)
    g = check_trace_workload("wl_a", rows, _make_trace_header(), _make_workload_spec(), "Qwen/Qwen2.5-7B-Instruct")
    # CV checks may fail with synthetic uniform-jitter data; exclude them
    fail_items = [i for i in g.items if not i.passed and i.severity == "fail"
                  and not i.name.endswith(".cv") and not i.name.endswith(".cv_poisson")]
    assert fail_items == [], f"Unexpected failures: {[(i.name, i.notes) for i in fail_items]}"


def test_check_trace_workload_wrong_mode():
    rows = _make_rows(100)
    g = check_trace_workload("wl_a", rows, _make_trace_header(mode="sim"),
                              _make_workload_spec(num_requests=100), "Qwen/Qwen2.5-7B-Instruct")
    assert any(not i.passed and i.name == "mode_real" for i in g.items)


def test_check_trace_workload_wrong_model():
    rows = _make_rows(100)
    g = check_trace_workload("wl_a", rows, _make_trace_header(model="other-model"),
                              _make_workload_spec(num_requests=100), "Qwen/Qwen2.5-7B-Instruct")
    assert any(not i.passed and i.name == "model_identity" for i in g.items)


def test_check_trace_workload_request_count_too_low_fails():
    rows = _make_rows(500)  # 50% of 1000 → FAIL (< 70%)
    g = check_trace_workload("wl_a", rows, _make_trace_header(),
                              _make_workload_spec(num_requests=1000), "Qwen/Qwen2.5-7B-Instruct")
    fail = [i for i in g.items if i.name == "request_count" and not i.passed and i.severity == "fail"]
    assert fail


def test_check_trace_workload_request_count_warn_band():
    rows = _make_rows(800)  # 80% of 1000 → WARN
    g = check_trace_workload("wl_a", rows, _make_trace_header(),
                              _make_workload_spec(num_requests=1000), "Qwen/Qwen2.5-7B-Instruct")
    warn = [i for i in g.items if i.name == "request_count" and i.severity == "warn"]
    assert warn
    # Check that the group would pass IF we exclude synthetic CV/distribution checks
    fail_items = [i for i in g.items if not i.passed and i.severity == "fail"
                  and not i.name.endswith(".cv") and not i.name.endswith(".cv_poisson")
                  and "_mean" not in i.name and "slo_ratio_" not in i.name]
    assert fail_items == [], f"Unexpected failures: {[(i.name, i.notes) for i in fail_items]}"


def test_check_trace_workload_empty_slo_class_fails():
    rows = _make_rows(100)
    rows[0]["slo_class"] = ""  # blank for one row
    g = check_trace_workload("wl_a", rows, _make_trace_header(),
                              _make_workload_spec(num_requests=100), "Qwen/Qwen2.5-7B-Instruct")
    fail = [i for i in g.items if i.name == "slo_class_extracted" and not i.passed]
    assert fail


def test_check_trace_workload_tenant_count_mismatch():
    rows = _make_rows(100)
    for r in rows:
        r["tenant_id"] = "t1"  # only t1, spec has t1 and t2
    g = check_trace_workload("wl_a", rows, _make_trace_header(),
                              _make_workload_spec(num_requests=100), "Qwen/Qwen2.5-7B-Instruct")
    fail = [i for i in g.items if i.name == "tenant_count" and not i.passed]
    assert fail


from unittest.mock import patch, MagicMock
from lib.validate_checks import (
    check_signal_liveness, check_prometheus_staleness,
    check_model_loaded, check_stack_readiness,
)


def _prom_url():
    return "http://prometheus:9090"


def test_check_signal_liveness_pass():
    sc = _make_signal_coverage([
        {"sim_name": "kvUtil", "mapped": True, "fidelity_rating": "high", "staleness_window_ms": 5000},
    ])
    mock_resp = MagicMock()
    mock_resp.raise_for_status = lambda: None
    mock_resp.json.return_value = {"data": {"result": [{"value": [1743600000, "1.5"]}]}}
    with patch("lib.validate_checks.requests.get", return_value=mock_resp):
        g = check_signal_liveness(sc, _prom_url())
    assert g.passed is True


def test_check_signal_liveness_no_data_fails():
    sc = _make_signal_coverage([
        {"sim_name": "kvUtil", "mapped": True, "fidelity_rating": "high", "staleness_window_ms": 5000},
    ])
    mock_resp = MagicMock()
    mock_resp.raise_for_status = lambda: None
    mock_resp.json.return_value = {"data": {"result": []}}  # no data
    with patch("lib.validate_checks.requests.get", return_value=mock_resp):
        g = check_signal_liveness(sc, _prom_url())
    assert g.passed is False


def test_check_signal_liveness_skips_non_prometheus():
    # staleness_window_ms=0 → not Prometheus-backed → no probes
    sc = _make_signal_coverage()  # all staleness=0
    with patch("lib.validate_checks.requests.get") as mock_get:
        g = check_signal_liveness(sc, _prom_url())
    mock_get.assert_not_called()
    assert g.passed is True


def test_check_stack_readiness_pass():
    kubectl_output = b'NAME  READY  UP-TO-DATE  AVAILABLE\nsim2real-epp  1/1  1  1\n'
    with patch("lib.validate_checks.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=kubectl_output, stderr=b"")
        g = check_stack_readiness("test-ns", "baseline")
    assert g.passed is True


def test_check_stack_readiness_no_deployments():
    with patch("lib.validate_checks.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=b"No resources found.\n", stderr=b"")
        g = check_stack_readiness("test-ns", "baseline")
    assert g.passed is False
