"""Remote run support — Kubernetes resource generation for --remote mode."""

import re
from pathlib import Path

CONFIGMAP_NAME = "sim2real-run-inputs"
JOB_NAME = "sim2real-orchestrator"
SERVICE_ACCOUNT = "sim2real-runner"
MOUNT_BASE = "/data"

# ConfigMap key prefix for the per-cluster cluster_config.json. The suffix is
# the cluster id, so the pod can reconstruct the layout-correct path
# ``clusters/<cluster_id>/cluster_config.json``. Distinct from ``cluster--``,
# which is reserved for per-run cluster/*.yaml files.
_CLUSTER_CONFIG_KEY_PREFIX = "cluster_config--"

# Kubernetes ConfigMap data keys must match this regex. See
# https://kubernetes.io/docs/concepts/overview/working-with-objects/names/
# and the validator's error message ("regex used for validation is
# '[-._a-zA-Z0-9]+'"). Anything outside this set must be encoded before use
# as a data key and decoded when reconstructing the source filename.
_CM_KEY_ALLOWED_RE = re.compile(r"^[-._a-zA-Z0-9]+$")

# In-scope characters to encode. Today only ``|`` appears in cluster/*.yaml
# filenames (produced by the pipe-shape pair-key grammar in lib/pairkey.py).
# Encoding follows URL-percent-encoding shape but uses ``.`` as the escape
# introducer since ``%`` is not in the CM-key allowed set. Round-trip is
# ``|`` <-> ``.7C``; extend ``_CM_KEY_ESCAPES`` if new disallowed chars
# appear in filenames.
_CM_KEY_ESCAPES = {"|": ".7C"}


def _encode_filename_for_cm(name: str) -> str:
    """Return ``name`` transformed into a ConfigMap-key-legal string.

    Only characters listed in ``_CM_KEY_ESCAPES`` are transformed. All other
    characters pass through — in particular ``.`` (extension separator) and
    ``-`` (identifier separator) are already legal. Callers should verify the
    result is legal before use; see ``_CM_KEY_ALLOWED_RE``.
    """
    for src, esc in _CM_KEY_ESCAPES.items():
        name = name.replace(src, esc)
    return name


def _decode_filename_from_cm(key_suffix: str) -> str:
    """Inverse of ``_encode_filename_for_cm``.

    ``key_suffix`` is the part of the CM key after the ``cluster--`` prefix has
    been stripped. Returns the original filename with disallowed characters
    restored so it can be used as an on-disk path.
    """
    for src, esc in _CM_KEY_ESCAPES.items():
        key_suffix = key_suffix.replace(esc, src)
    return key_suffix


def _discover_cluster_id(workspace_dir: Path) -> str:
    """Return the single cluster id under ``workspace_dir/clusters/``.

    Step 0 assumes one cluster per workspace. Raises FileNotFoundError if no
    cluster is registered (the in-pod orchestrator needs ``namespaces`` from
    cluster_config) or RuntimeError if more than one is present.
    """
    clusters_root = workspace_dir / "clusters"
    cluster_ids = (
        sorted(p.name for p in clusters_root.iterdir() if p.is_dir())
        if clusters_root.is_dir() else []
    )
    if not cluster_ids:
        raise FileNotFoundError(
            f"No cluster registered under {clusters_root}. "
            f"Run pipeline/cluster.py provision first."
        )
    if len(cluster_ids) > 1:
        raise RuntimeError(
            f"Multiple clusters found in workspace ({len(cluster_ids)}); "
            f"Step 0 assumes a single cluster."
        )
    return cluster_ids[0]


def build_run_inputs_configmap(
    *, run_dir: Path, workspace_dir: Path, namespace: str, run_name: str,
    defaults_content: "str | None" = None,
) -> dict:
    setup_path = workspace_dir / "setup_config.json"
    if not setup_path.exists():
        raise FileNotFoundError(f"setup_config.json not found: {setup_path}")

    metadata_path = run_dir / "run_metadata.json"
    if not metadata_path.exists():
        raise FileNotFoundError(f"run_metadata.json not found: {metadata_path}")

    cluster_id = _discover_cluster_id(workspace_dir)
    cluster_config_path = (
        workspace_dir / "clusters" / cluster_id / "cluster_config.json"
    )
    if not cluster_config_path.exists():
        raise FileNotFoundError(
            f"cluster_config.json not found: {cluster_config_path}"
        )

    data = {
        "setup_config.json": setup_path.read_text(),
        f"{_CLUSTER_CONFIG_KEY_PREFIX}{cluster_id}": cluster_config_path.read_text(),
        "run_metadata.json": metadata_path.read_text(),
    }

    if defaults_content is not None:
        data["defaults.yaml"] = defaults_content

    cluster_dir = run_dir / "cluster"
    yaml_files = sorted(cluster_dir.glob("*.yaml")) if cluster_dir.is_dir() else []
    if not yaml_files:
        raise FileNotFoundError(
            f"No cluster YAML files in {cluster_dir} — re-assemble the run"
        )
    for yaml_file in yaml_files:
        data[f"cluster--{_encode_filename_for_cm(yaml_file.name)}"] = yaml_file.read_text()

    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": CONFIGMAP_NAME, "namespace": namespace},
        "data": data,
    }


def _configmap_items(data: dict, run_name: str) -> list[dict]:
    """Build the items list that maps ConfigMap keys to filesystem paths."""
    items = []
    for key in sorted(data):
        if key == "setup_config.json":
            items.append({"key": key, "path": key})
        elif key == "defaults.yaml":
            items.append({"key": key, "path": "defaults.yaml"})
        elif key == "run_metadata.json":
            items.append({"key": key, "path": f"runs/{run_name}/{key}"})
        elif key.startswith(_CLUSTER_CONFIG_KEY_PREFIX):
            cluster_id = key[len(_CLUSTER_CONFIG_KEY_PREFIX):]
            items.append({
                "key": key,
                "path": f"clusters/{cluster_id}/cluster_config.json",
            })
        elif key.startswith("cluster--"):
            filename = _decode_filename_from_cm(key[len("cluster--"):])
            items.append({"key": key, "path": f"runs/{run_name}/cluster/{filename}"})
        else:
            raise ValueError(
                f"Unrecognized ConfigMap key '{key}' — update _configmap_items "
                f"to handle this key or remove it from build_run_inputs_configmap"
            )
    return items


def build_orchestrator_job(
    *, namespace: str, image: str, run_name: str, run_flags: list[str],
    configmap_data: dict,
) -> dict:
    workspace_path = f"{MOUNT_BASE}/workspace"
    config_path = f"{MOUNT_BASE}/config"
    items = _configmap_items(configmap_data, run_name)

    args = [
        "--experiment-root", MOUNT_BASE,
        "--run", run_name,
        "run", "--skip-build",
    ] + run_flags

    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": JOB_NAME, "namespace": namespace},
        "spec": {
            "backoffLimit": 0,
            "template": {
                "spec": {
                    "serviceAccountName": SERVICE_ACCOUNT,
                    "restartPolicy": "Never",
                    "initContainers": [{
                        "name": "copy-inputs",
                        "image": image,
                        "command": ["cp", "-r", f"{config_path}/.", workspace_path],
                        "volumeMounts": [
                            {"name": "config", "mountPath": config_path, "readOnly": True},
                            {"name": "workspace", "mountPath": workspace_path},
                        ],
                    }],
                    "containers": [{
                        "name": "orchestrator",
                        "image": image,
                        "args": args,
                        "env": [{"name": "PYTHONUNBUFFERED", "value": "1"}],
                        "volumeMounts": [
                            {"name": "workspace", "mountPath": workspace_path},
                        ],
                    }],
                    "volumes": [
                        {
                            "name": "config",
                            "configMap": {
                                "name": CONFIGMAP_NAME,
                                "items": items,
                            },
                        },
                        {
                            "name": "workspace",
                            "emptyDir": {},
                        },
                    ],
                },
            },
        },
    }
