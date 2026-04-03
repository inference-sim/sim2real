"""Tests for per-workload isolation helpers in deploy.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from deploy import _workload_slug, _make_run_name, _make_experiment_id, _restructure_for_convert_trace


def test_workload_slug_basic():
    assert _workload_slug("overload_mixed_slo") == "overload-mixed-slo"


def test_workload_slug_already_slug():
    assert _workload_slug("bursty-adversary") == "bursty-adversary"


def test_workload_slug_truncated():
    long_name = "a" * 70
    slug = _workload_slug(long_name)
    assert len(slug) <= 40
    assert slug.isalnum() or all(c.isalnum() or c == "-" for c in slug)


def test_make_run_name():
    name = _make_run_name("baseline", ts=1743600000)
    assert name == "sim2real-baseline-1743600000"


def test_make_experiment_id():
    eid = _make_experiment_id("baseline", "overload_mixed_slo", ts=1743600000, idx=0)
    # Budget: eid <= 36 so that "gw-{eid}-inference-gateway-istio" <= 63 (k8s DNS label limit)
    assert len(eid) <= 36, f"eid too long: {len(eid)}: {eid}"
    assert eid.startswith("sim2real-baseline-")
    assert eid.endswith("-1743600000-0")
    assert "--" not in eid, "double dash in experiment ID"
    # downstream k8s service name must be <= 63 (DNS label limit)
    assert len(f"gw-{eid}-inference-gateway-istio") <= 63
    # downstream Helm release names must be <= 53
    assert len(f"sim2real-{eid}-model") <= 53
    assert len(f"sim2real-{eid}-gaie") <= 53


def test_workload_spec_no_outer_quotes():
    """Workload spec with single-quoted YAML must not gain outer double-quotes.

    The run-workload task writes $(params.workloadSpec) via heredoc then decodes
    literal \\n with sed. If the value has outer double-quotes the task writes a
    YAML string scalar, and the Go tool fails with 'cannot unmarshal !!str'.
    """
    import yaml as _yaml
    from deploy import _build_pipelinerun_yaml
    spec = "version: '1'\nseed: 42\ncategory: language\naggregate_rate: 320\n"
    manifest_text = _build_pipelinerun_yaml(
        phase="baseline",
        experiment_id="eid",
        namespace="ns",
        run_name="rn",
        workload_name="wl",
        workload_spec=spec.replace("\n", r"\n"),  # as _run_workloads_for_phase would pass it
        run_index=0,
    )
    doc = _yaml.safe_load(manifest_text)
    params = {p["name"]: p["value"] for p in doc["spec"]["params"]}
    val = params["workloadSpec"]
    assert not val.startswith('"'), f"workloadSpec must not start with double-quote: {val[:60]!r}"
    # After sed decoding, the result must be parseable YAML
    import re
    decoded = re.sub(r"\\n", "\n", val)
    parsed = _yaml.safe_load(decoded)
    assert isinstance(parsed, dict), f"decoded spec must be a dict, got {type(parsed)}"
    assert parsed.get("version") == "1"


def test_make_experiment_id_different_workloads_differ():
    eid_a = _make_experiment_id("baseline", "overload_mixed_slo", ts=1743600000, idx=0)
    eid_b = _make_experiment_id("baseline", "bursty_adversary", ts=1743600000, idx=1)
    assert eid_a != eid_b


def test_pipelinerun_yaml_contains_new_params(tmp_path):
    """_build_pipelinerun_yaml generates YAML with runName, workloadName, workloadSpec."""
    import yaml as _yaml
    from deploy import _build_pipelinerun_yaml
    manifest_text = _build_pipelinerun_yaml(
        phase="baseline",
        experiment_id="sim2real-baseline-wl-overload-123",
        namespace="sim2real-test",
        run_name="sim2real-baseline-100",
        workload_name="overload_mixed_slo",
        workload_spec='{"aggregate_rate": 320}',
        run_index=0,
    )
    doc = _yaml.safe_load(manifest_text)
    params = {p["name"]: p["value"] for p in doc["spec"]["params"]}
    assert params["experimentId"] == "sim2real-baseline-wl-overload-123"
    assert params["runName"] == "sim2real-baseline-100"
    assert params["workloadName"] == "overload_mixed_slo"
    assert params["workloadSpec"] == '{"aggregate_rate": 320}'
    assert doc["metadata"]["name"] == "sim2real-baseline-wl-overload-123"


def test_should_skip_workload_done(tmp_path):
    """_should_skip_workload returns True when workload is marked done in state."""
    import json
    state_file = tmp_path / "benchmark_state.json"
    state_file.write_text(json.dumps({
        "phases": {
            "baseline": {
                "status": "running", "run_name": "sim2real-baseline-100",
                "workloads": {
                    "overload_mixed_slo": {"status": "done"},
                },
            }
        }
    }))
    from deploy import _should_skip_workload
    assert _should_skip_workload("baseline", "overload_mixed_slo", state_file,
                                  force_rerun=False) is True


def test_should_skip_workload_force_rerun(tmp_path):
    """_should_skip_workload returns False when force_rerun=True even if done."""
    import json
    state_file = tmp_path / "benchmark_state.json"
    state_file.write_text(json.dumps({
        "phases": {
            "baseline": {
                "workloads": {"overload_mixed_slo": {"status": "done"}},
            }
        }
    }))
    from deploy import _should_skip_workload
    assert _should_skip_workload("baseline", "overload_mixed_slo", state_file,
                                  force_rerun=True) is False


def test_should_skip_workload_pending(tmp_path):
    """_should_skip_workload returns False for pending workload."""
    import json
    state_file = tmp_path / "benchmark_state.json"
    state_file.write_text(json.dumps({
        "phases": {"baseline": {"workloads": {"overload_mixed_slo": {"status": "pending"}}}}
    }))
    from deploy import _should_skip_workload
    assert _should_skip_workload("baseline", "overload_mixed_slo", state_file,
                                  force_rerun=False) is False


def test_should_skip_workload_missing_state(tmp_path):
    """_should_skip_workload returns False when state file doesn't exist."""
    from deploy import _should_skip_workload
    assert _should_skip_workload("baseline", "overload_mixed_slo",
                                  tmp_path / "nonexistent.json",
                                  force_rerun=False) is False


def test_gpu_warning_formula(capsys):
    """_gpu_warning with both knobs shows correct total GPU count."""
    import yaml, tempfile, os
    values = {
        "stack": {"model": {"helmValues": {"decode": {
            "replicas": 2,
            "resources": {"limits": {"nvidia.com/gpu": "4"}},
        }}}}
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        import yaml as _yaml
        _yaml.dump(values, f)
        f.flush()
        values_path = Path(f.name)
    from deploy import _gpu_warning
    _gpu_warning(parallel=2, parallel_workloads=3, values_path=values_path)
    values_path.unlink()
    captured = capsys.readouterr()
    # 2 phases × 3 workloads × (2 replicas × 4 gpus) = 48 GPUs
    assert "48" in captured.err or "48" in captured.out


def _make_raw_dir(tmp_path, phase, run_names, workload_names):
    """Build a fake kubectl-cp output tree: raw/{runName}/{workloadName}/trace_data.csv"""
    raw = tmp_path / f"deploy_{phase}_log"
    for rn in run_names:
        for wl in workload_names:
            d = raw / rn / wl
            d.mkdir(parents=True)
            (d / "trace_data.csv").write_text(f"data for {rn}/{wl}")
    return raw


def test_restructure_baseline(tmp_path):
    """Baseline: single runName is unwrapped; workload dirs land directly under structured/."""
    raw = _make_raw_dir(tmp_path, "baseline",
                        ["sim2real-baseline-1234"],
                        ["workload_bursty_adversary", "workload_overload"])
    out = _restructure_for_convert_trace(raw, "baseline")
    assert (out / "workload_bursty_adversary" / "trace_data.csv").exists()
    assert (out / "workload_overload" / "trace_data.csv").exists()
    # No extra nesting
    assert not (out / "sim2real-baseline-1234").exists()


def test_restructure_baseline_multiple_run_names(tmp_path):
    """Baseline: multiple runNames in raw_dir (e.g. failed + successful run).

    The last runName (sorted) wins; workload dirs must not conflict.
    """
    # First runName has empty workload dirs (failed run)
    raw = _make_raw_dir(tmp_path, "baseline",
                        ["sim2real-baseline-1000", "sim2real-baseline-2000"],
                        ["workload_bursty_adversary"])
    # Overwrite first runName's file with empty content to simulate failed run
    (raw / "sim2real-baseline-1000" / "workload_bursty_adversary" / "trace_data.csv").write_text("")
    out = _restructure_for_convert_trace(raw, "baseline")
    content = (out / "workload_bursty_adversary" / "trace_data.csv").read_text()
    # Should have data from the later run (sim2real-baseline-2000)
    assert "sim2real-baseline-2000" in content


def test_restructure_noise(tmp_path):
    """Noise: multiple runNames become run-{i} dirs under each workload."""
    raw = _make_raw_dir(tmp_path, "noise",
                        ["sim2real-noise-1000", "sim2real-noise-2000"],
                        ["workload_bursty_adversary"])
    out = _restructure_for_convert_trace(raw, "noise")
    assert (out / "workload_bursty_adversary" / "run-0" / "trace_data.csv").exists()
    assert (out / "workload_bursty_adversary" / "run-1" / "trace_data.csv"
            ).exists()
