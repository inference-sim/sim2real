"""Integration tests for scripts/validate.py CLI."""
import json
import sys
from pathlib import Path
import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import importlib
validate = importlib.import_module("validate")


def _make_run_dir(tmp_path: Path) -> Path:
    """Build a minimal valid run directory for pre-deploy tests."""
    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    tekton_dir = run_dir / "prepare_tekton"
    tekton_dir.mkdir(parents=True)

    spec = yaml.dump({
        "version": "1", "seed": 42, "aggregate_rate": 100.0, "num_requests": 1000,
        "clients": [
            {"id": "c1", "slo_class": "critical", "tenant_id": "t1", "rate_fraction": 0.6,
             "arrival": {"process": "poisson"},
             "input_distribution": {"type": "gaussian", "params": {"mean": 128}},
             "output_distribution": {"type": "exponential", "params": {"mean": 64}}},
            {"id": "c2", "slo_class": "batch", "tenant_id": "t2", "rate_fraction": 0.4,
             "arrival": {"process": "gamma", "cv": 3.0},
             "input_distribution": {"type": "gaussian", "params": {"mean": 256}},
             "output_distribution": {"type": "exponential", "params": {"mean": 128}}},
        ],
    })
    epc = yaml.dump({
        "apiVersion": "inference.networking.x-k8s.io/v1alpha1",
        "kind": "EndpointPickerConfig",
        "plugins": [{"type": "load-aware-scorer"}, {"type": "decode-filter"}, {"type": "max-score-picker"}],
        "schedulingProfiles": [{"name": "default", "plugins": [
            {"pluginRef": "decode-filter"}, {"pluginRef": "max-score-picker"},
            {"pluginRef": "load-aware-scorer", "weight": 1},
        ]}],
    })
    values = {
        "observe": {"workloads": [{"name": "wl_a", "spec": spec}]},
        "stack": {
            "gaie": {
                "inferenceObjectives": [{"name": "critical"}, {"name": "batch"}],
                "baseline":  {"helmValues": {"inferenceExtension": {"pluginsCustomConfig": {"custom-plugins.yaml": epc}}}},
                "treatment": {"helmValues": {}, "admissionPolicy": "some-policy"},
            },
            "model": {
                "modelName": "Qwen/Qwen2.5-7B-Instruct",
                "helmValues": {"decode": {
                    "replicas": 4,
                    "parallelism": {"tensor": 1},
                    "containers": [{"modelCommand": "vllmServe", "args": [
                        "--gpu-memory-utilization=0.9",
                        "--max-num-seqs=256",
                        "--max-num-batched-tokens=2048",
                        "--block-size=16",
                    ]}],
                }},
            },
        },
    }
    (tekton_dir / "values.yaml").write_text(yaml.dump(values))

    coverage = {
        "coverage_complete": True,
        "signals": [
            {"sim_name": "totalInFlight", "mapped": True, "fidelity_rating": "medium", "staleness_window_ms": 0},
            {"sim_name": "sloClass",      "mapped": True, "fidelity_rating": "high",   "staleness_window_ms": 0},
            {"sim_name": "tenantID",      "mapped": True, "fidelity_rating": "high",   "staleness_window_ms": 0},
        ],
    }
    (run_dir / "prepare_signal_coverage.json").write_text(json.dumps(coverage))

    return run_dir


def _make_llm_config(repo_root: Path):
    blis = repo_root / "blis_router"
    blis.mkdir(parents=True)
    llm_cfg = {
        "model": {"id": "Qwen/Qwen2.5-7B-Instruct"},
        "serving": {"tensor_parallelism": 1},
        "cluster": {"num_instances": 4},
        "vllm_config": {
            "gpu_memory_utilization": 0.9,
            "max_num_running_reqs": 256,
            "max_num_scheduled_tokens": 2048,
            "block_size_in_tokens": 16,
        },
    }
    (blis / "llm_config.yaml").write_text(yaml.dump(llm_cfg))


def test_pre_deploy_passes_exit_zero(tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    run_dir = _make_run_dir(tmp_path)
    _make_llm_config(repo_root)
    monkeypatch.chdir(tmp_path)

    rc = validate.main_with_args([
        "pre-deploy",
        "--run-dir", str(run_dir),
        "--repo-root", str(repo_root),
    ])
    assert rc == 0
    report = json.loads((run_dir / "validate_pre_deploy.json").read_text())
    assert report["overall"] == "PASS"


def test_pre_deploy_writes_report(tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    run_dir = _make_run_dir(tmp_path)
    _make_llm_config(repo_root)
    monkeypatch.chdir(tmp_path)

    validate.main_with_args([
        "pre-deploy",
        "--run-dir", str(run_dir),
        "--repo-root", str(repo_root),
    ])
    report_path = run_dir / "validate_pre_deploy.json"
    assert report_path.exists()
    report = json.loads(report_path.read_text())
    assert "checks" in report
    assert "workloads" in report["checks"]
    assert "vllm_config" in report["checks"]


def test_pre_deploy_fails_exit_one_on_bad_config(tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    run_dir = _make_run_dir(tmp_path)
    # Write llm_config with wrong GPU mem
    blis = repo_root / "blis_router"
    blis.mkdir(parents=True)
    bad_cfg = {
        "model": {"id": "Qwen/Qwen2.5-7B-Instruct"},
        "serving": {"tensor_parallelism": 1},
        "cluster": {"num_instances": 4},
        "vllm_config": {
            "gpu_memory_utilization": 0.85,  # mismatch!
            "max_num_running_reqs": 256,
            "max_num_scheduled_tokens": 2048,
            "block_size_in_tokens": 16,
        },
    }
    (blis / "llm_config.yaml").write_text(yaml.dump(bad_cfg))
    monkeypatch.chdir(tmp_path)

    rc = validate.main_with_args([
        "pre-deploy",
        "--run-dir", str(run_dir),
        "--repo-root", str(repo_root),
    ])
    assert rc == 1


def test_pre_deploy_exit_two_on_missing_artifact(tmp_path, monkeypatch):
    run_dir = tmp_path / "workspace" / "runs" / "empty-run"
    run_dir.mkdir(parents=True)
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    monkeypatch.chdir(tmp_path)

    rc = validate.main_with_args([
        "pre-deploy",
        "--run-dir", str(run_dir),
        "--repo-root", str(repo_root),
    ])
    assert rc == 2


def test_stage_benchmarks_calls_pre_deploy_and_post_collection(tmp_path):
    """Verify deploy.py source contains both validate integration call sites."""
    source = (Path(__file__).resolve().parent.parent / "scripts" / "deploy.py").read_text()
    assert "run_pre_deploy_checks" in source, "deploy.py must call run_pre_deploy_checks"
    assert "run_post_collection_checks" in source, "deploy.py must call run_post_collection_checks"
