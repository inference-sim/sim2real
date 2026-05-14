"""Remote run support — Kubernetes resource generation for --remote mode."""

from pathlib import Path

CONFIGMAP_NAME = "sim2real-run-inputs"
JOB_NAME = "sim2real-orchestrator"
SERVICE_ACCOUNT = "sim2real-runner"
MOUNT_BASE = "/data"


def build_run_inputs_configmap(
    *, run_dir: Path, workspace_dir: Path, namespace: str, run_name: str
) -> dict:
    setup_path = workspace_dir / "setup_config.json"
    if not setup_path.exists():
        raise FileNotFoundError(f"setup_config.json not found: {setup_path}")

    metadata_path = run_dir / "run_metadata.json"
    if not metadata_path.exists():
        raise FileNotFoundError(f"run_metadata.json not found: {metadata_path}")

    data = {
        "setup_config.json": setup_path.read_text(),
        "run_metadata.json": metadata_path.read_text(),
    }

    for yaml_file in sorted((run_dir / "cluster").glob("*.yaml")):
        data[f"cluster--{yaml_file.name}"] = yaml_file.read_text()

    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": CONFIGMAP_NAME, "namespace": namespace},
        "data": data,
    }


def build_orchestrator_job(
    *, namespace: str, image: str, run_name: str, run_flags: list[str]
) -> dict:
    mount_path = f"{MOUNT_BASE}/workspace/runs/{run_name}"
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": JOB_NAME, "namespace": namespace},
        "spec": {
            "backoffLimit": 0,
            "activeDeadlineSeconds": 18000,
            "template": {
                "spec": {
                    "serviceAccountName": SERVICE_ACCOUNT,
                    "restartPolicy": "Never",
                    "containers": [
                        {
                            "name": "orchestrator",
                            "image": image,
                            "args": [
                                "--experiment-root", MOUNT_BASE,
                                "--run", run_name,
                                "run", "--skip-build-epp",
                            ] + run_flags,
                            "volumeMounts": [
                                {
                                    "name": "run-inputs",
                                    "mountPath": mount_path,
                                },
                            ],
                        },
                    ],
                    "volumes": [
                        {
                            "name": "run-inputs",
                            "configMap": {"name": CONFIGMAP_NAME},
                        },
                    ],
                },
            },
        },
    }
