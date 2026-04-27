"""Pod health detection and remediation for the deploy monitor."""
from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass
class PodState:
    name: str
    phase: str        # Pending, Running, Failed, Succeeded, Unknown
    ready: bool
    restart_count: int
    reason: str       # OOMKilled, CrashLoopBackOff, ImagePullBackOff, Evicted, ""
    message: str


@dataclass
class EventRecord:
    reason: str
    message: str
    count: int
    last_timestamp: str
    involved_object: str  # pod name


@dataclass
class TriageResult:
    tier: int         # 1, 2, or 3
    message: str      # one-line summary for stdout
    suggestion: str   # actionable suggestion for report (empty for tier 1)
    needs_logs: bool  # whether to fetch pod logs for tier 3
    action: str       # "delete_pod", "suggest", "api", "none"


class RemediationTracker:
    """Tracks consecutive remediation attempts per pod name."""

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}

    def record(self, pod_name: str) -> int:
        """Increment counter and return new count."""
        self._counts[pod_name] = self._counts.get(pod_name, 0) + 1
        return self._counts[pod_name]

    def reset(self, pod_name: str) -> None:
        """Reset counter when pod recovers to healthy state."""
        self._counts.pop(pod_name, None)

    def count(self, pod_name: str) -> int:
        return self._counts.get(pod_name, 0)


def parse_pods(json_str: str) -> list[PodState]:
    """Parse `kubectl get pods -o json` output into PodState list."""
    data = json.loads(json_str)
    pods = []
    for item in data.get("items", []):
        name = item["metadata"]["name"]
        status = item.get("status", {})
        phase = status.get("phase", "Unknown")
        ready = any(
            c.get("type") == "Ready" and c.get("status") == "True"
            for c in status.get("conditions", [])
        )
        reason = ""
        message = ""
        restart_count = 0
        for cs in status.get("containerStatuses", []):
            restart_count = max(restart_count, cs.get("restartCount", 0))
            last_term = cs.get("lastState", {}).get("terminated", {})
            if last_term.get("reason"):
                reason = last_term["reason"]
                message = last_term.get("message", "")
            waiting = cs.get("state", {}).get("waiting", {})
            if waiting.get("reason") and not reason:
                reason = waiting["reason"]
                message = waiting.get("message", "")
        if status.get("reason") == "Evicted":
            reason = "Evicted"
            message = status.get("message", "")
        pods.append(PodState(name=name, phase=phase, ready=ready,
                             restart_count=restart_count, reason=reason,
                             message=message))
    return pods


def parse_events(json_str: str) -> list[EventRecord]:
    """Parse `kubectl get events -o json` output into EventRecord list."""
    data = json.loads(json_str)
    return [
        EventRecord(
            reason=item.get("reason", ""),
            message=item.get("message", ""),
            count=item.get("count", 1),
            last_timestamp=item.get("lastTimestamp", ""),
            involved_object=item.get("involvedObject", {}).get("name", ""),
        )
        for item in data.get("items", [])
    ]
