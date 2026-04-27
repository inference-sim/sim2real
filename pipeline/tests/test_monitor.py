"""Tests for pipeline.monitor."""


def test_health_report_creates_file(tmp_path):
    from pipeline.monitor import HealthReport
    report = HealthReport(tmp_path / "health_report.md")
    report.add_finding(
        timestamp="2026-04-27 14:32:11",
        namespace="kalantar-0",
        pair_key="wl-chatbot-mid-treatment",
        pod_name="sim2real-ac-decode-0",
        signal="OOMKilled (attempt 3 — escalating)",
        action_taken="none",
        diagnosis="GPU memory exceeded.",
        suggestion="Reduce --gpu-memory-utilization to 0.85",
        tier=3,
    )
    content = (tmp_path / "health_report.md").read_text()
    assert "kalantar-0" in content
    assert "wl-chatbot-mid-treatment" in content
    assert "OOMKilled" in content
    assert "GPU memory exceeded" in content


def test_health_report_summary_counts(tmp_path):
    from pipeline.monitor import HealthReport
    path = tmp_path / "health_report.md"
    report = HealthReport(path)
    report.add_finding("2026-04-27 14:00:00", "ns-0", "wl-a", "pod-a",
                       "Evicted", "deleted pod", "", "", tier=1)
    report.add_finding("2026-04-27 14:01:00", "ns-0", "wl-b", "pod-b",
                       "OOMKilled", "none", "analysis", "fix", tier=3)
    content = path.read_text()
    assert "2 finding" in content.lower()
    assert "tier-1: 1" in content
    assert "tier-3: 1" in content


def test_health_report_preserves_on_reopen(tmp_path):
    from pipeline.monitor import HealthReport
    path = tmp_path / "health_report.md"
    r1 = HealthReport(path)
    r1.add_finding("2026-04-27 14:00:00", "ns-0", "wl-a", "pod-a",
                   "Evicted", "deleted", "", "", tier=1)
    r2 = HealthReport(path)
    r2.add_finding("2026-04-27 14:01:00", "ns-0", "wl-b", "pod-b",
                   "OOMKilled", "deleted", "", "", tier=1)
    content = path.read_text()
    assert "wl-a" in content
    assert "wl-b" in content
    assert "1 finding this session" in content.lower()  # session 2 has only 1 new finding
    assert "Prior session findings" in content           # session 1 is in the prior block


def test_health_report_no_snowball(tmp_path):
    from pipeline.monitor import HealthReport
    path = tmp_path / "health_report.md"
    r1 = HealthReport(path)
    r1.add_finding("2026-04-27 14:00:00", "ns-0", "wl-a", "pod-a",
                   "Evicted", "deleted", "", "", tier=1)
    r2 = HealthReport(path)
    r2.add_finding("2026-04-27 14:01:00", "ns-0", "wl-b", "pod-b",
                   "OOMKilled", "deleted", "", "", tier=1)
    r3 = HealthReport(path)
    r3.add_finding("2026-04-27 14:02:00", "ns-0", "wl-c", "pod-c",
                   "CrashLoopBackOff", "none", "diagnosis", "", tier=3)
    content = path.read_text()
    assert content.count("Prior session findings") <= 1


_PROGRESS_MIXED = {
    "wl-chatbot-mid-treatment": {
        "workload": "chatbot_mid", "package": "treatment",
        "status": "running", "namespace": "kalantar-0", "retries": 0,
    },
    "wl-chatbot-mid-baseline": {
        "workload": "chatbot_mid", "package": "baseline",
        "status": "done", "namespace": None, "retries": 0,
    },
    "wl-load-treatment": {
        "workload": "load", "package": "treatment",
        "status": "running", "namespace": "kalantar-1", "retries": 0,
    },
}

_PROGRESS_ALL_DONE = {
    "wl-chatbot-mid-treatment": {
        "workload": "chatbot_mid", "package": "treatment",
        "status": "done", "namespace": None, "retries": 0,
    },
}


def test_resolve_active_slots_returns_running_only():
    from pipeline.monitor import _resolve_active_slots
    slots = _resolve_active_slots(_PROGRESS_MIXED)
    assert set(slots.keys()) == {"kalantar-0", "kalantar-1"}
    assert "wl-chatbot-mid-treatment" in slots["kalantar-0"]


def test_resolve_active_slots_empty_when_all_done():
    from pipeline.monitor import _resolve_active_slots
    assert _resolve_active_slots(_PROGRESS_ALL_DONE) == {}


def test_work_remaining_true_when_running():
    from pipeline.monitor import _work_remaining
    assert _work_remaining(_PROGRESS_MIXED) is True


def test_work_remaining_false_when_all_done():
    from pipeline.monitor import _work_remaining
    assert _work_remaining(_PROGRESS_ALL_DONE) is False


def test_diagnose_with_api_returns_text():
    from unittest.mock import patch, MagicMock
    from pipeline.monitor import _diagnose_with_api
    import os

    mock_client = MagicMock()
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text="The pod OOMKilled because of X.")]
    mock_client.messages.create.return_value = mock_msg

    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
        with patch("pipeline.monitor.anthropic") as mock_anthropic:
            mock_anthropic.Anthropic.return_value = mock_client
            result = _diagnose_with_api(
                pod_name="sim2real-ac-decode-0",
                namespace="kalantar-0",
                signal="CrashLoopBackOff",
                describe_output="Name: sim2real-ac-decode-0\n...",
                logs="ERROR: CUDA out of memory\n",
                events_summary="OOMKilling: Memory cgroup out of memory",
            )
    assert "OOMKilled" in result


def test_diagnose_with_api_no_key():
    from pipeline.monitor import _diagnose_with_api
    import os
    from unittest.mock import patch

    with patch.dict(os.environ, {}, clear=True):
        result = _diagnose_with_api(
            pod_name="pod", namespace="ns", signal="CrashLoopBackOff",
            describe_output="", logs="", events_summary="",
        )
    assert "ANTHROPIC_API_KEY" in result or "unavailable" in result.lower() or result == ""
