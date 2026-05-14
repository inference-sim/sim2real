import json
import pytest
from unittest.mock import patch, MagicMock
from pipeline.lib.progress import (
    LocalProgressStore,
    ConfigMapProgressStore,
    CompositeProgressStore,
    ProgressStore,
)

def test_load_missing_returns_empty(tmp_path):
    store = LocalProgressStore(tmp_path / "progress.json")
    assert store.load() == {}

def test_save_and_load_roundtrip(tmp_path):
    store = LocalProgressStore(tmp_path / "progress.json")
    data = {
        "wl-smoke-baseline": {
            "workload": "wl-smoke", "package": "baseline",
            "status": "done", "namespace": "sim2real-0", "retries": 0,
        }
    }
    store.save(data)
    assert store.load() == data

def test_save_is_atomic(tmp_path):
    path = tmp_path / "progress.json"
    store = LocalProgressStore(path)
    store.save({"a": {"workload": "a", "package": "baseline", "status": "done", "namespace": "ns", "retries": 0}})
    assert not (tmp_path / "progress.json.tmp").exists()
    assert path.exists()

def test_save_overwrites_existing(tmp_path):
    store = LocalProgressStore(tmp_path / "progress.json")
    store.save({"x": {"workload": "x", "package": "baseline", "status": "pending", "namespace": None, "retries": 0}})
    store.save({"y": {"workload": "y", "package": "treatment", "status": "done", "namespace": "ns", "retries": 0}})
    assert list(store.load().keys()) == ["y"]

def test_load_corrupt_file_raises(tmp_path):
    path = tmp_path / "progress.json"
    path.write_text("{truncated")
    store = LocalProgressStore(path)
    with pytest.raises((json.JSONDecodeError, ValueError)):
        store.load()


# --- ConfigMapProgressStore tests ---

def test_configmap_load_missing_returns_empty():
    """ConfigMap not found on cluster returns {}."""
    with patch("subprocess.run") as mock:
        mock.return_value = MagicMock(returncode=1, stdout="", stderr="not found")
        store = ConfigMapProgressStore("sim2real-ns")
        assert store.load() == {}

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


# --- CompositeProgressStore tests ---

def test_composite_save_writes_to_all_stores(tmp_path):
    """save() writes to both primary and secondary stores."""
    primary = LocalProgressStore(tmp_path / "a.json")
    secondary = LocalProgressStore(tmp_path / "b.json")
    store = CompositeProgressStore(primary, secondary)
    data = {"wl-x": {"status": "done"}}
    store.save(data)
    assert primary.load() == data
    assert secondary.load() == data

def test_composite_load_returns_primary(tmp_path):
    """load() returns data from primary when available."""
    primary = LocalProgressStore(tmp_path / "a.json")
    secondary = LocalProgressStore(tmp_path / "b.json")
    primary.save({"from": "primary"})
    secondary.save({"from": "secondary"})
    store = CompositeProgressStore(primary, secondary)
    assert store.load() == {"from": "primary"}

def test_composite_load_falls_back_to_secondary(tmp_path):
    """load() falls back to secondary when primary is empty."""
    primary = LocalProgressStore(tmp_path / "a.json")
    secondary = LocalProgressStore(tmp_path / "b.json")
    secondary.save({"from": "secondary"})
    store = CompositeProgressStore(primary, secondary)
    assert store.load() == {"from": "secondary"}

def test_composite_load_all_empty(tmp_path):
    """load() returns {} when all stores are empty."""
    primary = LocalProgressStore(tmp_path / "a.json")
    secondary = LocalProgressStore(tmp_path / "b.json")
    store = CompositeProgressStore(primary, secondary)
    assert store.load() == {}

def test_composite_secondary_save_failure_warns(tmp_path, capsys):
    """Secondary store save failure warns but doesn't raise."""
    primary = LocalProgressStore(tmp_path / "a.json")

    class FailStore(ProgressStore):
        def load(self): return {}
        def save(self, data): raise RuntimeError("kubectl fail")

    store = CompositeProgressStore(primary, FailStore())
    store.save({"wl-x": {"status": "done"}})
    assert primary.load() == {"wl-x": {"status": "done"}}
    assert "kubectl fail" in capsys.readouterr().err

def test_composite_secondary_load_failure_skipped(tmp_path):
    """Secondary store load failure is skipped silently."""
    primary = LocalProgressStore(tmp_path / "a.json")

    class FailStore(ProgressStore):
        def load(self): raise RuntimeError("boom")
        def save(self, data): pass

    store = CompositeProgressStore(primary, FailStore())
    assert store.load() == {}
