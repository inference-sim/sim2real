"""GPU capacity probe for deploy.py orchestrator."""

import json
import subprocess
from pathlib import Path
from typing import Optional

from pipeline.lib.values import deep_merge


def probe_free_gpus(
    gpu_resource_type: str = "nvidia.com/gpu",
) -> Optional[tuple[int, int, int]]:
    """Return (free_gpus, total_allocatable, total_requested) or None on failure.

    Queries kubectl for node allocatable resources and pod requests,
    computes the delta clamped to zero.

    Assumes only spec.containers request GPUs (initContainers are excluded —
    llm-d workloads do not use GPU-requesting init containers).

    Note: the two kubectl calls are not atomic — cluster state may change
    between them. Acceptable for logging; consumers that gate on capacity
    (#64) should account for this.
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

    except (json.JSONDecodeError, OSError, ValueError):
        return None

    free = max(0, total_allocatable - total_requested)
    return (free, total_allocatable, total_requested)


def probe_stderr(gpu_resource_type: str = "nvidia.com/gpu") -> Optional[str]:
    """Run the same kubectl calls as probe_free_gpus but return stderr on failure."""
    try:
        nodes_result = subprocess.run(
            ["kubectl", "get", "nodes", "-o", "json"],
            check=False, text=True, capture_output=True,
        )
        if nodes_result.returncode != 0:
            return nodes_result.stderr.strip()

        pods_result = subprocess.run(
            ["kubectl", "get", "pods", "--all-namespaces",
             "--field-selector=status.phase!=Succeeded,status.phase!=Failed",
             "-o", "json"],
            check=False, text=True, capture_output=True,
        )
        if pods_result.returncode != 0:
            return pods_result.stderr.strip()
    except OSError as e:
        return str(e)
    return None


# ── GPU cost derivation ────────────────────────────────────────────────────────


def derive_gpu_resource_type(resolved_scenario: dict, defaults: dict) -> str:
    """Derive the Kubernetes GPU resource name from scenario + defaults.

    Merges the first scenario entry over defaults, then reads
    accelerator.resource. Falls back to "nvidia.com/gpu".
    """
    scenario_entry = {}
    scenarios = resolved_scenario.get("scenario", [])
    if scenarios:
        scenario_entry = scenarios[0]

    merged = deep_merge(defaults, scenario_entry)
    return merged.get("accelerator", {}).get("resource", "nvidia.com/gpu")


def gpu_cost_per_pair(resolved_scenario: dict, defaults: dict) -> int:
    """Compute total GPU cost for one baseline/treatment pair.

    Merges the first scenario entry over defaults, then sums GPU cost
    across enabled roles using 3-level precedence:
      role.accelerator.count > accelerator.count > tensor * dataLocal

    The middle tier (accelerator.count as per-role fallback) extends the
    Jinja template's 2-level logic — kept intentionally so that top-level
    accelerator.count propagates to roles that don't override it.

    Args:
        resolved_scenario: The resolved scenario dict (has "scenario" list).
        defaults: The full defaults.yaml dict from llm-d-benchmark.

    Returns:
        Total GPU count needed for one pair (one arm of baseline or treatment).
    """
    scenario_entry = {}
    scenarios = resolved_scenario.get("scenario", [])
    if scenarios:
        scenario_entry = scenarios[0]

    merged = deep_merge(defaults, scenario_entry)

    top_accel = merged.get("accelerator", {})
    if top_accel.get("count") is not None and int(top_accel["count"]) == 0:
        return 0

    gpu_cost = 0
    for role_name, default_enabled, default_replicas in [
        ("decode", True, 1),
        ("prefill", False, 0),
    ]:
        role_cfg = merged.get(role_name, {})
        if not role_cfg.get("enabled", default_enabled):
            continue

        replicas = role_cfg.get("replicas", default_replicas)
        parallelism = role_cfg.get("parallelism", {})

        role_accel = role_cfg.get("accelerator", {})
        if "count" in role_accel:
            gpus_per_pod = int(role_accel["count"])
        elif "count" in top_accel:
            gpus_per_pod = int(top_accel["count"])
        else:
            gpus_per_pod = parallelism.get("tensor", 1) * parallelism.get("dataLocal", 1)

        gpu_cost += replicas * gpus_per_pod

    return gpu_cost


def load_defaults(repo_root: Path) -> Optional[dict]:
    """Load llm-d-benchmark defaults.yaml, or None if unavailable."""
    defaults_path = repo_root / "llm-d-benchmark" / "config" / "templates" / "values" / "defaults.yaml"
    if not defaults_path.exists():
        return None
    try:
        import yaml
        return yaml.safe_load(defaults_path.read_text()) or {}
    except Exception:
        return None
