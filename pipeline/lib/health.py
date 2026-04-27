"""Pod health detection and remediation for the deploy monitor."""
from __future__ import annotations

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
