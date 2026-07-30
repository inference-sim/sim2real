"""Shared process-execution utilities.

Single seam for spawning subprocesses across the pipeline. Historically
``deploy.py``, ``setup.py`` and ``lib/cluster_ops.py`` each defined their own
near-identical thin wrapper over :func:`subprocess.run` (plus duplicated
``which`` helpers). That duplication meant cross-cutting concerns — output
capture, timeouts, redaction, and the test monkeypatch seam — had to be applied
N times and drifted per module.

This module consolidates them into one place. Callers should delegate here so a
single change (e.g. adding a default timeout or output redaction) applies
uniformly. The signature is a strict superset of the previous wrappers'
signatures, so existing call sites are unaffected.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import Optional


def run(
    cmd: "list[str]",
    *,
    check: bool = True,
    capture: bool = False,
    cwd: "str | None" = None,
    input: "str | None" = None,
    timeout: "Optional[int]" = None,
) -> "subprocess.CompletedProcess":
    """Run ``cmd`` as a subprocess.

    Text mode is always enabled. ``check`` raises on non-zero exit unless
    ``False``. ``capture`` captures stdout/stderr. ``cwd`` sets the working
    directory. ``input`` feeds stdin. ``timeout`` is the subprocess timeout in
    seconds — ``None`` means no timeout (callers such as ``cluster_ops._run``
    and ``deploy.run`` pass their own defaults). This is the single
    process-exec seam for the pipeline; tests may monkeypatch this function
    to intercept invocations.
    """
    return subprocess.run(
        cmd,
        check=check,
        text=True,
        capture_output=capture,
        cwd=cwd,
        input=input,
        timeout=timeout,
    )


def which(cmd: str) -> bool:
    """Return True if ``cmd`` is resolvable on PATH."""
    return shutil.which(cmd) is not None
