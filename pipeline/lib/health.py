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


_OOM_MAX_ATTEMPTS = 2  # tier-1 retries before escalating


def triage_pod(
    pod: PodState,
    events: list[EventRecord],
    tracker: RemediationTracker,
) -> "TriageResult | None":
    """Return a TriageResult if the pod needs attention, None if healthy.

    Does NOT modify tracker — caller records remediation after acting.
    """
    pod_events = [e for e in events if e.involved_object == pod.name]

    if pod.phase == "Running" and pod.ready:
        return None

    # Tier 1: Evicted
    if pod.reason == "Evicted":
        return TriageResult(
            tier=1, action="delete_pod", needs_logs=False,
            message=f"{pod.name}: Evicted → deleting pod",
            suggestion="",
        )

    # Tier 1/2: OOMKilled
    if pod.reason == "OOMKilled":
        attempt = tracker.count(pod.name) + 1
        if attempt <= _OOM_MAX_ATTEMPTS:
            return TriageResult(
                tier=1, action="delete_pod", needs_logs=False,
                message=f"{pod.name}: OOMKilled (attempt {attempt}/{_OOM_MAX_ATTEMPTS}) → deleting pod",
                suggestion="",
            )
        return TriageResult(
            tier=2, action="suggest", needs_logs=False,
            message=f"{pod.name}: OOMKilled (attempt {attempt}) — persistent",
            suggestion=(
                "Persistent OOM: reduce --gpu-memory-utilization (e.g. 0.85), "
                "--max-model-len, or replica count in "
                "env_defaults.yaml → stack.model.helmValues.decode.containers"
            ),
        )

    # Tier 2: Image pull failure
    if pod.reason in ("ImagePullBackOff", "ErrImagePull"):
        img_detail = next(
            (e.message for e in pod_events if "pull" in e.message.lower()),
            pod.message,
        )
        return TriageResult(
            tier=2, action="suggest", needs_logs=False,
            message=f"{pod.name}: {pod.reason}",
            suggestion=(
                f"Image pull failed: {img_detail}\n"
                "Check env_defaults.yaml → stack.model.vllm_image "
                "or stack.gaie.epp_image.build.tag"
            ),
        )

    # Tier 2: Scheduling failure
    if pod.phase == "Pending":
        sched = next((e for e in pod_events if e.reason == "FailedScheduling"), None)
        if sched:
            msg_lower = sched.message.lower()
            if "quota" in msg_lower or "exceeded" in msg_lower:
                return TriageResult(
                    tier=2, action="suggest", needs_logs=False,
                    message=f"{pod.name}: Pending (resource quota exceeded)",
                    suggestion=f"Resource quota exhausted: {sched.message}",
                )
            if "insufficient" in msg_lower or "nodes available" in msg_lower:
                return TriageResult(
                    tier=2, action="suggest", needs_logs=False,
                    message=f"{pod.name}: Pending (no nodes match GPU affinity)",
                    suggestion=(
                        f"No schedulable nodes: {sched.message}\n"
                        "Check nodeAffinity in env_defaults.yaml → "
                        "stack.model.helmValues.decode.extraConfig.affinity"
                    ),
                )
            # unrecognized scheduling message — falls through to None

    # Tier 2: Startup probe timeout
    if pod.phase == "Running" and not pod.ready:
        startup_fail = next(
            (e for e in pod_events
             if e.reason == "Unhealthy" and "startup probe" in e.message.lower()),
            None,
        )
        if startup_fail:
            return TriageResult(
                tier=2, action="suggest", needs_logs=False,
                message=f"{pod.name}: startup probe failing",
                suggestion=(
                    "Startup probe timing out before model finishes loading.\n"
                    "Increase failureThreshold in "
                    "env_defaults.yaml → stack.model.helmValues.decode.containers"
                    "[].extraConfig.startupProbe.failureThreshold"
                ),
            )

    # Tier 3: CrashLoopBackOff or other failure requiring log analysis
    if pod.reason == "CrashLoopBackOff" or pod.phase in ("Failed", "Unknown"):
        return TriageResult(
            tier=3, action="api", needs_logs=True,
            message=f"{pod.name}: {pod.reason or pod.phase} — API diagnosis",
            suggestion="",
        )

    return None
