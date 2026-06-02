"""GPU capacity probe for deploy.py orchestrator."""

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Union

import yaml

from pipeline.lib.values import deep_merge


# ── Node eligibility ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class NodeFilter:
    """Per-role node eligibility constraints derived from a resolved scenario.

    required_gpu_products: set of acceptable nvidia.com/gpu.product label values.
        Empty set means no product constraint.
    tolerations: list of K8s toleration dicts (key/operator/value/effect).
        Empty list means no taints can be tolerated.
    """
    required_gpu_products: frozenset[str] = field(default_factory=frozenset)
    tolerations: tuple[dict, ...] = field(default_factory=tuple)

    def __post_init__(self):
        if not isinstance(self.required_gpu_products, frozenset):
            object.__setattr__(self, "required_gpu_products",
                               frozenset(self.required_gpu_products or ()))
        if not isinstance(self.tolerations, tuple):
            object.__setattr__(self, "tolerations",
                               tuple(self.tolerations or ()))


def _toleration_matches_taint(toleration: dict, taint: dict) -> bool:
    t_effect = toleration.get("effect", "")
    if t_effect and t_effect != taint.get("effect"):
        return False
    op = toleration.get("operator", "Equal")
    t_key = toleration.get("key", "")
    if op == "Exists":
        return not t_key or t_key == taint.get("key")
    return t_key == taint.get("key") and toleration.get("value", "") == taint.get("value", "")


def _node_is_cordoned(node: dict) -> bool:
    return bool(node.get("spec", {}).get("unschedulable", False))


def _node_blocking_taints(node: dict) -> list[dict]:
    return [t for t in node.get("spec", {}).get("taints", []) or []
            if t.get("effect") in ("NoSchedule", "NoExecute")]


def _filter_admits_node(filt: NodeFilter, node: dict) -> bool:
    if _node_is_cordoned(node):
        return False
    for taint in _node_blocking_taints(node):
        if not any(_toleration_matches_taint(tol, taint) for tol in filt.tolerations):
            return False
    if filt.required_gpu_products:
        product = node.get("metadata", {}).get("labels", {}).get("nvidia.com/gpu.product")
        if product not in filt.required_gpu_products:
            return False
    return True


def node_is_eligible(node: dict, filters: list[NodeFilter]) -> bool:
    """A node is eligible if no filter is given, or some filter accepts it.

    Empty filter list → unfiltered (legacy behavior).
    """
    if not filters:
        return True
    return any(_filter_admits_node(f, node) for f in filters)


# ── Cluster probe ──────────────────────────────────────────────────────────────


def probe_free_gpus(
    gpu_resource_type: str = "nvidia.com/gpu",
    *,
    node_filters: "list[NodeFilter] | None" = None,
) -> Union[tuple[int, int, int], str]:
    """Return (free_gpus, total_allocatable, total_requested) or error string.

    Queries kubectl for node allocatable resources and pod requests,
    computes the delta clamped to zero.

    Skips pods without spec.nodeName — these are Pending (unscheduled) and
    have not been allocated any node resources.

    Assumes only spec.containers request GPUs (initContainers are excluded —
    llm-d workloads do not use GPU-requesting init containers).

    When node_filters is provided, restrict the sum to nodes accepted by
    at least one filter (union eligibility across roles). A node is excluded
    if cordoned, if it has a NoSchedule/NoExecute taint that no filter's
    tolerations match, or if every filter requires a gpu.product label that
    this node does not carry. Pods on excluded nodes are also excluded from
    the requested sum, so the (free, alloc, requested) tuple stays internally
    consistent.

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
            return nodes_result.stderr.strip() or "kubectl get nodes failed"

        pods_result = subprocess.run(
            ["kubectl", "get", "pods", "--all-namespaces",
             "--field-selector=status.phase!=Succeeded,status.phase!=Failed",
             "-o", "json"],
            check=False, text=True, capture_output=True,
        )
        if pods_result.returncode != 0:
            return pods_result.stderr.strip() or "kubectl get pods failed"

    except OSError as e:
        return str(e)

    try:
        nodes = json.loads(nodes_result.stdout)
        pods = json.loads(pods_result.stdout)
    except json.JSONDecodeError as e:
        return f"JSON parse error: {e}"

    filters = node_filters or []
    eligible_node_names: set[str] = set()
    total_allocatable = 0
    for node in nodes.get("items", []):
        if filters and not node_is_eligible(node, filters):
            continue
        name = node.get("metadata", {}).get("name", "")
        if name:
            eligible_node_names.add(name)
        alloc = node.get("status", {}).get("allocatable", {})
        count = alloc.get(gpu_resource_type)
        if count is not None:
            try:
                total_allocatable += int(count)
            except ValueError:
                return f"non-integer allocatable value {count!r} on node {name or '?'}"

    total_requested = 0
    for pod in pods.get("items", []):
        node_name = pod.get("spec", {}).get("nodeName")
        if node_name is None:
            continue
        if filters and node_name not in eligible_node_names:
            continue
        for container in pod.get("spec", {}).get("containers", []):
            requests = container.get("resources", {}).get("requests", {})
            count = requests.get(gpu_resource_type)
            if count is not None:
                try:
                    total_requested += int(count)
                except ValueError:
                    return f"non-integer request value {count!r} in pod {pod.get('metadata', {}).get('name', '?')}"

    free = max(0, total_allocatable - total_requested)
    return (free, total_allocatable, total_requested)


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


def gpu_cost_per_pair(resolved_scenario: dict, defaults: dict) -> Union[int, str]:
    """Compute total GPU cost for one baseline/treatment pair.

    Merges the first scenario entry over defaults, then sums GPU cost
    across enabled roles using 3-level precedence:
      role.accelerator.count > accelerator.count > tensor * dataLocal

    The middle tier (accelerator.count as per-role fallback) extends the
    Jinja template's 2-level logic — kept intentionally so that top-level
    accelerator.count propagates to roles that don't override it.

    Returns int on success, or error string describing the problematic field.
    """
    scenario_entry = {}
    scenarios = resolved_scenario.get("scenario", [])
    if scenarios:
        scenario_entry = scenarios[0]

    merged = deep_merge(defaults, scenario_entry)

    top_accel = merged.get("accelerator", {})
    top_count_raw = top_accel.get("count")
    if top_count_raw is not None:
        try:
            if int(top_count_raw) == 0:
                return 0
        except (ValueError, TypeError):
            return f"accelerator.count={top_count_raw!r} is not a valid integer"

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
        try:
            if "count" in role_accel:
                gpus_per_pod = int(role_accel["count"])
            elif top_count_raw is not None:
                gpus_per_pod = int(top_count_raw)
            else:
                gpus_per_pod = parallelism.get("tensor", 1) * parallelism.get("dataLocal", 1)
        except (ValueError, TypeError):
            field = f"{role_name}.accelerator.count" if "count" in role_accel else "accelerator.count"
            val = role_accel.get("count") if "count" in role_accel else top_count_raw
            return f"{field}={val!r} is not a valid integer"

        gpu_cost += replicas * gpus_per_pod

    return gpu_cost


_KNOWN_ROLES = ("decode", "prefill")
_GPU_PRODUCT_LABEL = "nvidia.com/gpu.product"


def _extract_required_gpu_products(affinity: dict) -> frozenset[str]:
    """Read requiredDuringSchedulingIgnoredDuringExecution matchExpressions
    for the nvidia.com/gpu.product key with operator In; return value set.

    NotIn / Exists are not treated as positive product constraints (would
    require negation logic outside this issue's scope).
    """
    node_aff = affinity.get("nodeAffinity", {}) or {}
    required = node_aff.get("requiredDuringSchedulingIgnoredDuringExecution", {}) or {}
    terms = required.get("nodeSelectorTerms", []) or []
    products: set[str] = set()
    for term in terms:
        for expr in term.get("matchExpressions", []) or []:
            if expr.get("key") == _GPU_PRODUCT_LABEL and expr.get("operator") == "In":
                products.update(expr.get("values", []) or [])
    return frozenset(products)


def extract_node_filters(resolved_scenario: dict) -> dict[str, NodeFilter]:
    """Build per-role NodeFilter dict from a resolved scenario.

    Reads model.helmValues.{role}.extraConfig.affinity.nodeAffinity for each
    role present. Tolerations are always returned as empty per the
    conservative assumption in issue #261 (see follow-up #263).

    Returns empty dict if the scenario has no helmValues for any known role.
    """
    scenarios = resolved_scenario.get("scenario", []) or []
    if not scenarios:
        return {}
    entry = scenarios[0]
    helm_values = entry.get("model", {}).get("helmValues", {}) or {}
    out: dict[str, NodeFilter] = {}
    for role in _KNOWN_ROLES:
        if role not in helm_values:
            continue
        role_cfg = helm_values[role] or {}
        affinity = role_cfg.get("extraConfig", {}).get("affinity", {}) or {}
        out[role] = NodeFilter(
            required_gpu_products=_extract_required_gpu_products(affinity),
            tolerations=(),
        )
    return out


def load_defaults(repo_root: Path, *, defaults_path: "Path | None" = None) -> Union[dict, str, None]:
    """Load llm-d-benchmark defaults.yaml.

    Args:
        repo_root: experiment repo root (used to locate defaults.yaml by convention).
        defaults_path: if provided, read from this path directly instead of
            constructing from repo_root. Used by the remote orchestrator where
            the file is mounted at a known location.

    Returns:
        dict: parsed defaults on success.
        None: file not found (expected when submodule not initialized).
        str: error message when file exists but can't be parsed.
    """
    if defaults_path is None:
        defaults_path = repo_root / "llm-d-benchmark" / "config" / "templates" / "values" / "defaults.yaml"
    if not defaults_path.exists():
        return None
    try:
        return yaml.safe_load(defaults_path.read_text()) or {}
    except yaml.YAMLError as e:
        return f"defaults.yaml parse error: {e}"
    except OSError as e:
        return f"defaults.yaml read error: {e}"
