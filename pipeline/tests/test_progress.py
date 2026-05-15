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
