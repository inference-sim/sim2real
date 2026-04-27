"""Tests for pipeline.lib.health."""


def test_pod_state_dataclass():
    from pipeline.lib.health import PodState
    pod = PodState(name="test-pod", phase="Running", ready=True,
                   restart_count=0, reason="", message="")
    assert pod.name == "test-pod"
    assert pod.ready is True


def test_event_record_dataclass():
    from pipeline.lib.health import EventRecord
    evt = EventRecord(reason="FailedScheduling", message="no nodes",
                      count=1, last_timestamp="2026-01-01T00:00:00Z",
                      involved_object="my-pod")
    assert evt.reason == "FailedScheduling"


def test_triage_result_dataclass():
    from pipeline.lib.health import TriageResult
    r = TriageResult(tier=1, message="deleted", suggestion="",
                     needs_logs=False, action="delete_pod")
    assert r.tier == 1
