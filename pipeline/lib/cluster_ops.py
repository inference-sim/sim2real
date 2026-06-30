"""File-system primitives for ``clusters/<cluster_id>/cluster_config.json``.

Step 0 carves cluster-side responsibilities out of ``pipeline/setup.py`` into
a new ``pipeline/cluster.py`` entry point backed by this library. This module
is the first slice: read/write/update of the per-cluster config file. The
kubectl/oc primitives (``provision_namespace``, ``apply_cluster_resources``)
land in a separate child.

Writes are atomic (tmpfile + ``Path.replace``) so a concurrent reader either
sees the previous full content or the new full content, never a torn write.
Path resolution is delegated to :mod:`pipeline.lib.layout` — no string
mashing of workspace paths in this module.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from pipeline.lib import layout
from pipeline.lib.values import deep_merge


# Top-level keys in cluster_config.json whose contents are merged into the
# existing value rather than wholesale-replaced. Per design (see
# docs/epics/step-0/design.md, "cluster_config.json schema"): ``secret_names``
# is a flat string→string dict; ``workspaces`` is a dict of Tekton workspace
# bindings. Partial updates to either should preserve untouched keys.
_DEEP_MERGE_KEYS = ("secret_names", "workspaces")


def read_cluster_config(cluster_id: str) -> dict:
    """Read ``clusters/<cluster_id>/cluster_config.json``.

    Returns the parsed JSON object as a dict. Returns ``{}`` if the file does
    not exist — callers that need a not-yet-provisioned cluster to fail loudly
    should check the result themselves.
    """
    path = layout.cluster_config_path(cluster_id)
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_cluster_config(cluster_id: str, config: dict) -> None:
    """Atomically write ``clusters/<cluster_id>/cluster_config.json``.

    Creates the cluster directory if it does not exist. Uses tmpfile +
    ``Path.replace`` so a concurrent reader never observes a partially
    written file: the rename is atomic on POSIX (and Windows) for paths on
    the same filesystem, and the tmpfile is created in the same directory.
    """
    path = layout.cluster_config_path(cluster_id)
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".cluster_config-", suffix=".json.tmp")
    try:
        with open(fd, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, sort_keys=True)
            f.write("\n")
        Path(tmp).replace(path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def update_cluster_config(cluster_id: str, /, **updates) -> dict:
    """Read-modify-write of ``cluster_config.json``. Returns the new config.

    Update semantics, per design:

    - Keys ``secret_names`` and ``workspaces`` are **deep-merged** into the
      existing values when the caller passes a dict. Keys not mentioned in
      the update are preserved.
    - All other top-level keys are **fully replaced** by the supplied value.

    Examples::

        # Replace the namespaces list wholesale.
        update_cluster_config("ocp-east", namespaces=["a", "b"])

        # Merge a new secret name in; existing entries are preserved.
        update_cluster_config("ocp-east", secret_names={"hf_token": "hf-2"})

        # Add a workspace binding; existing bindings are preserved.
        update_cluster_config(
            "ocp-east",
            workspaces={"new-wks": {"persistentVolumeClaim": {"claimName": "new-pvc"}}},
        )

    Operates on the read result of ``read_cluster_config`` — an absent file
    is treated as ``{}``, so this function can be used to initialise a
    cluster_config from scratch as well as to mutate an existing one.

    The write step is atomic; the read-modify-write sequence as a whole is
    not (no inter-process locking). Concurrent updaters are not expected.
    """
    cfg = read_cluster_config(cluster_id)
    for key, value in updates.items():
        if key in _DEEP_MERGE_KEYS and isinstance(value, dict):
            existing = cfg.get(key)
            if isinstance(existing, dict):
                cfg[key] = deep_merge(existing, value)
                continue
        cfg[key] = value
    write_cluster_config(cluster_id, cfg)
    return cfg
