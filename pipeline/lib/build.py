"""Shared build primitives for sim2real.

Consumers:
  - ``pipeline/sim2real.py:_cmd_build`` (step-2, translation-scoped)
  - ``pipeline/deploy.py:_cmd_build`` (step-1, run-scoped) — routes its
    buildkit invocation through this module.

Runtime primitives (``probe_image_digest``, ``dispatch_buildkit_build``,
``atomic_write_json``) are failure-tolerant in the "fail-safe → rebuild"
direction: the skopeo probe returns ``None`` on any error, dispatch
returns the exit code without raising on non-zero, atomic_write only
raises on filesystem faults.

Precondition primitives (``compose_image_ref``, ``check_skopeo``, and
``dispatch_buildkit_build`` when ``build-epp.sh`` is missing) raise
``BuildError`` — callers surface those as ``error: ...; return 2``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


class BuildError(Exception):
    """Raised for build-time failures that should exit the CLI with code 2."""


def compose_image_ref(registry: str, repo: str, tag: str) -> str:
    """Return ``<registry>/<repo>:<tag>``. All three must be non-empty."""
    if not registry:
        raise BuildError("registry must not be empty")
    if not repo:
        raise BuildError("repo must not be empty")
    if not tag:
        raise BuildError("tag must not be empty")
    return f"{registry}/{repo}:{tag}"


def check_skopeo() -> None:
    """Raise ``BuildError`` with a platform-appropriate install hint if
    ``skopeo`` is not on PATH."""
    if shutil.which("skopeo") is None:
        raise BuildError(
            "skopeo not found on PATH — required for registry probe. Install: "
            "brew install skopeo, apt install skopeo, or dnf install skopeo"
        )


def probe_image_digest(image_ref: str, *, timeout: float = 30.0) -> str | None:
    """Return the image digest via ``skopeo inspect`` or ``None`` on any failure.

    Never raises. Every non-happy path (network, auth, timeout, invalid
    JSON, missing tag, missing binary) returns ``None`` — the caller uses
    that as the fail-safe signal to (re)build.
    """
    try:
        result = subprocess.run(
            ["skopeo", "inspect", f"docker://{image_ref}"],
            capture_output=True, text=True,
            timeout=timeout,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if result.returncode != 0:
        return None
    try:
        parsed = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    digest = parsed.get("Digest")
    if not isinstance(digest, str) or not digest:
        return None
    return digest


def dispatch_buildkit_build(
    *,
    image_ref: str,
    build_id: str,
    namespace: str,
    source_dir: Path,
    run_dir: Path,
    repo_root: Path,
    registry_secret_name: str,
) -> int:
    """Invoke ``pipeline/scripts/build-epp.sh`` and return its exit code.

    Passes every arg the script requires. The script does the actual
    buildkit-pod submit, source-copy PVC upload, and registry
    credentials Secret check. Never raises on non-zero exit — the
    caller inspects the return code (and may retry, log, or record a
    null-digest result).

    ``registry_secret_name`` is the k8s Secret name that holds the
    dockerconfigjson push credentials. Callers read it from
    ``cluster_config.json:secret_names.registry_creds`` — the same
    single-source-of-truth that ``cluster.py provision`` writes to.
    Empty string is rejected (BuildError) rather than sent to the shell
    script, because build-epp.sh would then read the wrong (or no)
    Secret and buildkit would fail to authenticate.

    Raises ``BuildError`` when ``build-epp.sh`` is missing or
    ``registry_secret_name`` is empty.
    """
    if not registry_secret_name:
        raise BuildError(
            "registry_secret_name must not be empty — populate "
            "cluster_config.json:secret_names.registry_creds via "
            "'cluster.py provision'"
        )
    build_script = repo_root / "pipeline" / "scripts" / "build-epp.sh"
    if not build_script.exists():
        raise BuildError(f"build-epp.sh not found at {build_script}")
    result = subprocess.run(
        [
            "bash", str(build_script),
            "--run-dir", str(run_dir),
            "--run-name", build_id,
            "--namespace", namespace,
            "--image-ref", image_ref,
            "--source-dir", str(source_dir),
            "--registry-secret-name", registry_secret_name,
        ],
        check=False,
        cwd=repo_root,
    )
    return result.returncode


def atomic_write_json(path: Path, data: dict) -> None:
    """Write ``data`` as pretty JSON to ``path`` via a tempfile + os.replace.

    POSIX-atomic on same-filesystem writes. Creates parent dirs. Raises
    ``OSError`` on filesystem faults; callers surface the error and exit.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=str(path.parent), prefix=".tmp-", suffix=".json"
    )
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(data, indent=2) + "\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
