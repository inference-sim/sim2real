"""Pod pending detection and reason classification."""
from __future__ import annotations

import re

_RECOVERABLE_PATTERNS = [
    re.compile(r"Insufficient\s+\S+", re.IGNORECASE),
    re.compile(r"\d+/\d+ nodes are available:.*Insufficient", re.IGNORECASE),
]

_NON_RECOVERABLE_PATTERNS = [
    re.compile(r"node\(s\) didn't match Pod's node affinity/selector", re.IGNORECASE),
    re.compile(r"persistentvolumeclaim\s+.*not found", re.IGNORECASE),
    re.compile(r"node\(s\) had untolerated taint", re.IGNORECASE),
]


def classify_pending_reason(message: str) -> str:
    """Classify a pod scheduling failure message.

    Returns "recoverable" (resource scarcity) or "non_recoverable" (config error).
    """
    if not message:
        return "non_recoverable"

    for pat in _RECOVERABLE_PATTERNS:
        if pat.search(message):
            return "recoverable"

    return "non_recoverable"


def parse_pod_conditions(pods_json: dict) -> tuple[str | None, str]:
    """Parse kubectl get pods JSON output for pending scheduling failures.

    Returns (category, detail) where category is "recoverable",
    "non_recoverable", or None if no pods are stuck pending.
    """
    for item in pods_json.get("items", []):
        status = item.get("status", {})
        if status.get("phase") != "Pending":
            continue
        for cond in status.get("conditions", []):
            if (cond.get("type") == "PodScheduled"
                    and cond.get("status") == "False"
                    and cond.get("reason") == "Unschedulable"):
                message = cond.get("message", "")
                category = classify_pending_reason(message)
                return category, message
    return None, ""
