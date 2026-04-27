"""Tests for pipeline.lib.health."""
import json


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


_PODS_JSON = json.dumps({
    "items": [
        {
            "metadata": {"name": "sim2real-ac-decode-0"},
            "status": {
                "phase": "Running",
                "conditions": [{"type": "Ready", "status": "False"}],
                "containerStatuses": [{
                    "name": "vllm", "ready": False, "restartCount": 2,
                    "lastState": {"terminated": {"reason": "OOMKilled", "exitCode": 137}},
                    "state": {"waiting": {"reason": "CrashLoopBackOff", "message": "back-off"}},
                }],
            },
        },
        {
            "metadata": {"name": "sim2real-ac-epp-0"},
            "status": {
                "phase": "Pending",
                "conditions": [],
                "containerStatuses": [],
            },
        },
        {
            "metadata": {"name": "sim2real-ac-decode-1"},
            "status": {
                "phase": "Running",
                "conditions": [{"type": "Ready", "status": "True"}],
                "containerStatuses": [{
                    "name": "vllm", "ready": True, "restartCount": 0,
                    "lastState": {}, "state": {"running": {}},
                }],
            },
        },
    ]
})

_EVENTS_JSON = json.dumps({
    "items": [
        {
            "reason": "FailedScheduling",
            "message": "0/5 nodes available: 5 Insufficient nvidia.com/gpu",
            "count": 3,
            "lastTimestamp": "2026-04-27T14:30:00Z",
            "involvedObject": {"name": "sim2real-ac-epp-0", "kind": "Pod"},
        },
        {
            "reason": "OOMKilling",
            "message": "Memory cgroup out of memory",
            "count": 1,
            "lastTimestamp": "2026-04-27T14:28:00Z",
            "involvedObject": {"name": "sim2real-ac-decode-0", "kind": "Pod"},
        },
    ]
})


def test_parse_pods_count():
    from pipeline.lib.health import parse_pods
    assert len(parse_pods(_PODS_JSON)) == 3


def test_parse_pods_oom_killed():
    from pipeline.lib.health import parse_pods
    pods = parse_pods(_PODS_JSON)
    p = next(x for x in pods if x.name == "sim2real-ac-decode-0")
    assert p.reason == "OOMKilled"
    assert p.restart_count == 2
    assert p.ready is False


def test_parse_pods_pending():
    from pipeline.lib.health import parse_pods
    pods = parse_pods(_PODS_JSON)
    p = next(x for x in pods if x.name == "sim2real-ac-epp-0")
    assert p.phase == "Pending"
    assert p.ready is False


def test_parse_pods_healthy():
    from pipeline.lib.health import parse_pods
    pods = parse_pods(_PODS_JSON)
    p = next(x for x in pods if x.name == "sim2real-ac-decode-1")
    assert p.ready is True
    assert p.reason == ""


def test_parse_events_count():
    from pipeline.lib.health import parse_events
    assert len(parse_events(_EVENTS_JSON)) == 2


def test_parse_events_fields():
    from pipeline.lib.health import parse_events
    events = parse_events(_EVENTS_JSON)
    sched = next(e for e in events if e.reason == "FailedScheduling")
    assert sched.involved_object == "sim2real-ac-epp-0"
    assert sched.count == 3
