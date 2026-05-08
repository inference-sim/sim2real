"""GPU capacity probe for deploy.py orchestrator."""

import json
import subprocess
from typing import Optional


def probe_free_gpus(
    gpu_resource_type: str = "nvidia.com/gpu",
) -> Optional[tuple[int, int, int]]:
    """Return (free_gpus, total_allocatable, total_requested) or None on failure.

    Queries kubectl for node allocatable resources and pod requests,
    computes the delta clamped to zero.
    """
    try:
        nodes_result = subprocess.run(
            ["kubectl", "get", "nodes", "-o", "json"],
            check=False, text=True, capture_output=True,
        )
        if nodes_result.returncode != 0:
            return None

        pods_result = subprocess.run(
            ["kubectl", "get", "pods", "--all-namespaces",
             "--field-selector=status.phase!=Succeeded,status.phase!=Failed",
             "-o", "json"],
            check=False, text=True, capture_output=True,
        )
        if pods_result.returncode != 0:
            return None

        nodes = json.loads(nodes_result.stdout)
        pods = json.loads(pods_result.stdout)
    except (json.JSONDecodeError, OSError):
        return None

    total_allocatable = 0
    for node in nodes.get("items", []):
        alloc = node.get("status", {}).get("allocatable", {})
        count = alloc.get(gpu_resource_type)
        if count is not None:
            total_allocatable += int(count)

    total_requested = 0
    for pod in pods.get("items", []):
        for container in pod.get("spec", {}).get("containers", []):
            requests = container.get("resources", {}).get("requests", {})
            count = requests.get(gpu_resource_type)
            if count is not None:
                total_requested += int(count)

    free = max(0, total_allocatable - total_requested)
    return (free, total_allocatable, total_requested)
