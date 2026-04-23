import pytest  # noqa: F401
from pipeline.lib.progress import LocalProgressStore

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
