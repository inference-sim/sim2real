"""Tests for deploy.py status subcommand dispatch and per-run scoping (issue #449).

Existing per-run status behavior lives in test_deploy_run.py:30-576 (29
tests exercising _cmd_status directly) and test_deploy_standalone.py:59-111
(3 tests exercising main() dispatch with default status). This file adds
the run-scoped dispatcher tests explicitly enumerated in issue #449's
acceptance criteria for the deploy.py status subcommand.
"""

from unittest.mock import patch

import pipeline.deploy as mod
from pipeline.lib.progress import ConfigMapProgressStore


def test_main_status_reads_from_per_run_cluster(tmp_path, monkeypatch):
    """main() dispatches 'status' with the per-run cluster_config (#449).

    Verifies the AC: deploy.py status --run trial-1 reads runs/trial-1/
    cluster/ context and the per-run ConfigMap. This test asserts that
    _load_run_cluster_config receives run_dir with name 'trial-1' and
    that _cmd_status receives the resolved run_dir + cluster_config
    verbatim.
    """
    monkeypatch.setattr("sys.argv", [
        "deploy.py", "--experiment-root", str(tmp_path),
        "--run", "trial-1", "status",
    ])
    monkeypatch.setattr(mod, "EXPERIMENT_ROOT", tmp_path)

    status_calls = []
    per_run_calls = []

    def mock_status(args, run_dir, cluster_config=None):
        status_calls.append((run_dir, cluster_config))

    def mock_per_run(run_dir):
        per_run_calls.append(run_dir)
        return {"namespaces": ["sim2real-per-run"]}

    with patch.object(mod, "_cmd_status", mock_status), \
         patch.object(mod, "_load_run_cluster_config", mock_per_run), \
         patch.object(mod, "_load_setup_config", return_value={}):
        mod.main()

    assert len(status_calls) == 1
    run_dir, cluster_config = status_calls[0]
    assert run_dir.name == "trial-1"
    assert cluster_config == {"namespaces": ["sim2real-per-run"]}
    assert len(per_run_calls) == 1
    assert per_run_calls[0].name == "trial-1"


def test_main_status_uses_current_run_when_no_flag(tmp_path, monkeypatch):
    """Omitting --run falls back to current_run from setup_config (#449)."""
    monkeypatch.setattr("sys.argv", [
        "deploy.py", "--experiment-root", str(tmp_path), "status",
    ])
    monkeypatch.setattr(mod, "EXPERIMENT_ROOT", tmp_path)

    status_calls = []

    def mock_status(args, run_dir, cluster_config=None):
        status_calls.append(run_dir)

    with patch.object(mod, "_cmd_status", mock_status), \
         patch.object(mod, "_load_run_cluster_config",
                      return_value={"namespaces": ["ns-0"]}), \
         patch.object(mod, "_load_setup_config",
                      return_value={"current_run": "default-run"}):
        mod.main()

    assert status_calls[0].name == "default-run"


def test_cmd_status_reads_run_scoped_configmap(tmp_path, monkeypatch):
    """_cmd_status constructs a ConfigMapProgressStore scoped to (scenario, run).

    The store's name controls which ConfigMap the subcommand reads
    (``sim2real-progress-<scenario>-<run>`` after issue #551). Assert the
    store is built with the run directory's basename AND the scenario from
    run_metadata.json so status snapshots stay scoped per experiment root.
    """
    import json
    from pipeline.deploy import _cmd_status

    run_dir = tmp_path / "workspace" / "runs" / "trial-1"
    run_dir.mkdir(parents=True)
    (run_dir / "run_metadata.json").write_text(
        json.dumps({"scenario": "softr", "run_name": "trial-1"})
    )

    store_kwargs = []
    original_init = ConfigMapProgressStore.__init__

    def _capturing_init(self, namespace, *, run_name="", scenario=""):
        store_kwargs.append({
            "namespace": namespace,
            "run_name": run_name,
            "scenario": scenario,
        })
        original_init(self, namespace, run_name=run_name, scenario=scenario)

    monkeypatch.setattr(ConfigMapProgressStore, "__init__", _capturing_init)
    monkeypatch.setattr(ConfigMapProgressStore, "load", lambda self: {})

    class _Args:
        only = None
        workload = None
        package = None
        status = None
        silent = False

    _cmd_status(_Args(), run_dir, cluster_config={"namespaces": ["sim2real-ns"]})

    assert len(store_kwargs) == 1
    assert store_kwargs[0]["run_name"] == "trial-1"
    assert store_kwargs[0]["namespace"] == "sim2real-ns"
    assert store_kwargs[0]["scenario"] == "softr"


def test_make_progress_store_missing_scenario_prefills_ref_and_cluster(
    tmp_path, capsys
):
    """Issue #555: when scenario is missing from run_metadata.json but
    translation_hash and cluster_id are present, the error message names
    'scenario' (not the file), drops the '(added in issue #551)' reference,
    prefills --translation and --cluster from meta, and recommends --force.
    """
    import json
    import pytest

    from pipeline.deploy import _make_progress_store

    run_dir = tmp_path / "workspace" / "runs" / "trial-1"
    run_dir.mkdir(parents=True)
    (run_dir / "run_metadata.json").write_text(
        json.dumps({
            "run_name": "trial-1",
            "translation_hash": "abc12345",
            "cluster_id": "ocp-east",
            # scenario intentionally absent
        })
    )

    with pytest.raises(SystemExit) as exc:
        _make_progress_store("sim2real-ns", run_dir)
    assert exc.value.code == 1

    err = capsys.readouterr().err
    assert "scenario not recorded for run 'trial-1'" in err
    # Names the missing thing, not the file it lives in.
    assert "run_metadata.json" not in err
    # Drops the internal issue reference.
    assert "#551" not in err
    # Names --force explicitly (defeats the no-op path).
    assert "--force" in err
    # Prefills the copy-paste-able assemble invocation.
    assert "--translation abc12345" in err
    assert "--cluster ocp-east" in err
    assert "--run trial-1" in err


def test_make_progress_store_missing_scenario_placeholders_when_meta_lacks_fields(
    tmp_path, capsys
):
    """Issue #555: if translation_hash or cluster_id are missing from
    run_metadata.json (extremely old runs), the error falls back to
    placeholders rather than emitting the empty string."""
    import json
    import pytest

    from pipeline.deploy import _make_progress_store

    run_dir = tmp_path / "workspace" / "runs" / "trial-2"
    run_dir.mkdir(parents=True)
    (run_dir / "run_metadata.json").write_text(
        json.dumps({"run_name": "trial-2"})  # nothing else
    )

    with pytest.raises(SystemExit):
        _make_progress_store("sim2real-ns", run_dir)

    err = capsys.readouterr().err
    assert "<translation-ref>" in err
    assert "<cluster-id>" in err
    assert "--force" in err
