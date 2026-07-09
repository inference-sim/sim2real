import pytest
from unittest.mock import patch, MagicMock
from pipeline.lib.progress import (
    ConfigMapProgressStore,
)


# --- ConfigMapProgressStore tests ---

def test_configmap_load_not_found_returns_empty():
    """ConfigMap NotFound on cluster returns {}."""
    with patch("subprocess.run") as mock:
        mock.return_value = MagicMock(
            returncode=1, stdout="",
            stderr='Error from server (NotFound): configmaps "sim2real-progress" not found',
        )
        store = ConfigMapProgressStore("sim2real-ns")
        assert store.load() == {}

def test_configmap_load_generic_not_found_raises():
    """Generic 'not found' without K8s reason code raises (not silently ignored)."""
    with patch("subprocess.run") as mock:
        mock.return_value = MagicMock(
            returncode=1, stdout="",
            stderr="error: the path \"sim2real-progress\" does not exist, not found anywhere",
        )
        store = ConfigMapProgressStore("sim2real-ns")
        with pytest.raises(RuntimeError, match="kubectl get configmap"):
            store.load()

def test_configmap_load_kubectl_error_raises():
    """Non-NotFound kubectl errors raise RuntimeError."""
    with patch("subprocess.run") as mock:
        mock.return_value = MagicMock(
            returncode=1, stdout="",
            stderr="error: You must be logged in to the server (Unauthorized)",
        )
        store = ConfigMapProgressStore("sim2real-ns")
        with pytest.raises(RuntimeError, match="kubectl get configmap"):
            store.load()

def test_configmap_empty_namespace_raises():
    """Empty namespace is rejected at construction time."""
    with pytest.raises(ValueError, match="non-empty namespace"):
        ConfigMapProgressStore("")

def test_configmap_load_returns_data():
    """ConfigMap with valid JSON returns parsed dict."""
    data = '{"wl-smoke-baseline": {"status": "done"}}'
    with patch("subprocess.run") as mock:
        mock.return_value = MagicMock(returncode=0, stdout=data)
        store = ConfigMapProgressStore("sim2real-ns")
        assert store.load() == {"wl-smoke-baseline": {"status": "done"}}

def test_configmap_load_corrupt_raises():
    """ConfigMap with corrupt JSON raises ValueError."""
    with patch("subprocess.run") as mock:
        mock.return_value = MagicMock(returncode=0, stdout="{truncated")
        store = ConfigMapProgressStore("sim2real-ns")
        with pytest.raises(ValueError, match="Corrupt ConfigMap"):
            store.load()

def test_configmap_load_empty_data_returns_empty():
    """ConfigMap exists but data key is empty string returns {}."""
    with patch("subprocess.run") as mock:
        mock.return_value = MagicMock(returncode=0, stdout="")
        store = ConfigMapProgressStore("sim2real-ns")
        assert store.load() == {}

def test_configmap_save_calls_kubectl_apply():
    """save() calls kubectl apply with correct ConfigMap JSON on stdin."""
    data = {"wl-smoke-baseline": {"status": "done"}}
    with patch("subprocess.run") as mock:
        mock.return_value = MagicMock(returncode=0)
        store = ConfigMapProgressStore("sim2real-ns")
        store.save(data)
    call_args = mock.call_args
    cmd = call_args[0][0]
    assert cmd == ["kubectl", "apply", "-f", "-"]
    assert call_args[1]["input"]
    import json as _json
    cm = _json.loads(call_args[1]["input"])
    assert cm["metadata"]["name"] == "sim2real-progress"
    assert cm["metadata"]["namespace"] == "sim2real-ns"
    assert _json.loads(cm["data"]["progress"]) == data

def test_configmap_save_failure_raises():
    """save() raises RuntimeError when kubectl apply fails."""
    with patch("subprocess.run") as mock:
        mock.return_value = MagicMock(returncode=1, stderr="forbidden")
        store = ConfigMapProgressStore("sim2real-ns")
        with pytest.raises(RuntimeError, match="Failed to update ConfigMap"):
            store.save({"x": 1})

def test_configmap_load_missing_kubectl_raises():
    """load() wraps OSError (missing kubectl) as RuntimeError."""
    with patch("subprocess.run", side_effect=FileNotFoundError("kubectl")):
        store = ConfigMapProgressStore("sim2real-ns")
        with pytest.raises(RuntimeError, match="kubectl not available"):
            store.load()

def test_configmap_save_missing_kubectl_raises():
    """save() wraps OSError (missing kubectl) as RuntimeError."""
    with patch("subprocess.run", side_effect=FileNotFoundError("kubectl")):
        store = ConfigMapProgressStore("sim2real-ns")
        with pytest.raises(RuntimeError, match="Failed to update ConfigMap"):
            store.save({"x": 1})


def test_configmap_name_includes_run_name():
    """ConfigMap name includes run_name suffix when provided."""
    store = ConfigMapProgressStore("sim2real-ns", run_name="experiment-1")
    assert store.configmap_name == "sim2real-progress-experiment-1"


def test_configmap_name_default_without_run_name():
    """ConfigMap name is the base name when run_name is omitted."""
    store = ConfigMapProgressStore("sim2real-ns")
    assert store.configmap_name == "sim2real-progress"


def test_configmap_run_name_sanitized_lowercase():
    """Uppercase chars in run_name are lowercased for K8s compliance."""
    store = ConfigMapProgressStore("sim2real-ns", run_name="My-Experiment")
    assert store.configmap_name == "sim2real-progress-my-experiment"


def test_configmap_run_name_sanitized_underscores():
    """Underscores in run_name are replaced with hyphens for K8s compliance."""
    store = ConfigMapProgressStore("sim2real-ns", run_name="foo_bar")
    assert store.configmap_name == "sim2real-progress-foo-bar"


def test_configmap_run_name_too_long_raises():
    """run_name that exceeds 253-char ConfigMap name limit is rejected."""
    with pytest.raises(ValueError, match="invalid ConfigMap name"):
        ConfigMapProgressStore("sim2real-ns", run_name="a" * 250)


# --- Scenario scoping (#551) ---

def test_configmap_name_includes_scenario_and_run():
    """scenario + run_name yields sim2real-progress-<scenario>-<run>."""
    store = ConfigMapProgressStore(
        "sim2real-ns", run_name="trial-1", scenario="softr"
    )
    assert store.configmap_name == "sim2real-progress-softr-trial-1"


def test_configmap_scenario_only_no_run_name():
    """scenario without run_name yields sim2real-progress-<scenario>."""
    store = ConfigMapProgressStore("sim2real-ns", scenario="softr")
    assert store.configmap_name == "sim2real-progress-softr"


def test_configmap_scenario_sanitized_lowercase_and_underscore():
    """scenario is sanitized like run_name: lowercase, underscore→hyphen."""
    store = ConfigMapProgressStore(
        "sim2real-ns", run_name="trial-1", scenario="Soft_R"
    )
    assert store.configmap_name == "sim2real-progress-soft-r-trial-1"


def test_configmap_scenario_plus_run_too_long_raises():
    """scenario + run combined that exceeds 253-char CM name limit is rejected."""
    with pytest.raises(ValueError, match="invalid ConfigMap name"):
        ConfigMapProgressStore(
            "sim2real-ns", run_name="r" * 200, scenario="s" * 100
        )


def test_configmap_legacy_name_recorded_when_scenario_and_run_supplied():
    """_legacy_configmap_name is sim2real-progress-<run> when scenario provided."""
    store = ConfigMapProgressStore(
        "sim2real-ns", run_name="trial-1", scenario="softr"
    )
    assert store._legacy_configmap_name == "sim2real-progress-trial-1"


def test_configmap_legacy_name_none_when_no_scenario():
    """_legacy_configmap_name is None when scenario is omitted."""
    store = ConfigMapProgressStore("sim2real-ns", run_name="trial-1")
    assert store._legacy_configmap_name is None


def test_load_new_name_present_does_not_check_legacy():
    """When the new-name CM exists, load() returns it without touching legacy."""
    data = '{"wl-smoke|baseline|i1": {"status": "done"}}'
    with patch("subprocess.run") as mock:
        mock.return_value = MagicMock(returncode=0, stdout=data)
        store = ConfigMapProgressStore(
            "sim2real-ns", run_name="trial-1", scenario="softr"
        )
        result = store.load()
    assert result == {"wl-smoke|baseline|i1": {"status": "done"}}
    # Only one kubectl call — get on the new name
    assert mock.call_count == 1


def test_load_new_name_notfound_legacy_notfound_returns_empty():
    """When both new-name and legacy CMs are absent, load() returns {}."""
    notfound = MagicMock(
        returncode=1, stdout="",
        stderr='Error from server (NotFound): configmaps "sim2real-progress-softr-trial-1" not found',
    )
    notfound_legacy = MagicMock(
        returncode=1, stdout="",
        stderr='Error from server (NotFound): configmaps "sim2real-progress-trial-1" not found',
    )
    with patch("subprocess.run", side_effect=[notfound, notfound_legacy]) as mock:
        store = ConfigMapProgressStore(
            "sim2real-ns", run_name="trial-1", scenario="softr"
        )
        result = store.load()
    assert result == {}
    assert mock.call_count == 2


def test_load_migrates_legacy_when_new_name_notfound():
    """New-name NotFound + legacy present → migrate, return legacy's data."""
    legacy_data = '{"wl-smoke|baseline|i1": {"status": "done"}}'
    calls = [
        # 1st: get new-name → NotFound
        MagicMock(
            returncode=1, stdout="",
            stderr='Error from server (NotFound): configmaps "sim2real-progress-softr-trial-1" not found',
        ),
        # 2nd: get legacy → returns data
        MagicMock(returncode=0, stdout=legacy_data),
        # 3rd: apply new-name (migration write) → success
        MagicMock(returncode=0),
        # 4th: delete legacy → success
        MagicMock(returncode=0),
    ]
    with patch("subprocess.run", side_effect=calls) as mock:
        store = ConfigMapProgressStore(
            "sim2real-ns", run_name="trial-1", scenario="softr"
        )
        result = store.load()
    assert result == {"wl-smoke|baseline|i1": {"status": "done"}}
    # Verify migration write targeted the new-name CM
    apply_args = mock.call_args_list[2]
    cmd = apply_args[0][0]
    assert cmd == ["kubectl", "apply", "-f", "-"]
    import json as _json
    cm = _json.loads(apply_args[1]["input"])
    assert cm["metadata"]["name"] == "sim2real-progress-softr-trial-1"
    # Verify legacy delete
    delete_args = mock.call_args_list[3]
    assert delete_args[0][0][:3] == ["kubectl", "delete", "configmap"]
    assert "sim2real-progress-trial-1" in delete_args[0][0]


def test_load_no_legacy_name_skips_migration():
    """When scenario is omitted (legacy_name is None), load() does NOT fall back."""
    notfound = MagicMock(
        returncode=1, stdout="",
        stderr='Error from server (NotFound): configmaps "sim2real-progress-trial-1" not found',
    )
    with patch("subprocess.run", side_effect=[notfound]) as mock:
        # No scenario → _legacy_configmap_name is None → single kubectl get
        store = ConfigMapProgressStore("sim2real-ns", run_name="trial-1")
        result = store.load()
    assert result == {}
    assert mock.call_count == 1


def test_save_emits_discovery_labels_when_scenario_and_run_supplied():
    """save() sets sim2real.scenario and sim2real.run labels for kubectl -l filtering."""
    data = {"wl-x|y|i1": {"status": "done"}}
    with patch("subprocess.run") as mock:
        mock.return_value = MagicMock(returncode=0)
        store = ConfigMapProgressStore(
            "sim2real-ns", run_name="trial-1", scenario="softr"
        )
        store.save(data)
    import json as _json
    cm = _json.loads(mock.call_args[1]["input"])
    labels = cm["metadata"].get("labels") or {}
    assert labels.get("sim2real.scenario") == "softr"
    assert labels.get("sim2real.run") == "trial-1"


def test_save_no_labels_when_scenario_missing():
    """save() omits labels block when scenario is not supplied."""
    data = {"wl-x|y|i1": {"status": "done"}}
    with patch("subprocess.run") as mock:
        mock.return_value = MagicMock(returncode=0)
        store = ConfigMapProgressStore("sim2real-ns", run_name="trial-1")
        store.save(data)
    import json as _json
    cm = _json.loads(mock.call_args[1]["input"])
    labels = cm["metadata"].get("labels") or {}
    assert labels == {}
