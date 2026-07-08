# Issue #421 — `pipeline/cluster.py provision` orchestrator

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `pipeline/cluster.py` with a `provision` subcommand — the first consumer of `pipeline/lib/cluster_ops.py`. Carves cluster-side bootstrap out of `setup.py` while the new file leaves `setup.py` untouched (it remains the legacy 8-step flow; trimming `setup.py` is a later epic child).

**Architecture:**

- `pipeline/cluster.py` is a thin CLI shell. All side effects (kubectl, oc, disk) live in `pipeline.lib.cluster_ops` and `pipeline.lib.layout`.
- Flow: parse args → resolve flags > env > prompt → build `cluster_config` dict → `write_cluster_config` (must be on disk for `apply_cluster_resources` to read) → `apply_cluster_resources` → per-namespace `provision_namespace` → print summary line each → exit non-zero on any `steps_failed`.
- The script delegates secret resolution and OpenShift detection to library functions but never imports anything from `pipeline/setup.py` — the carve-out is one-way.

**Tech Stack:** Python ≥3.10, `argparse`, `pipeline.lib.cluster_ops`, `pipeline.lib.layout`, `pytest` with `monkeypatch`-driven kubectl/oc fakes.

## Global Constraints

- CLI surface must match the design **exactly**: positional `cluster_id`; `--namespaces`, `--storage-class`, `--hf-token`, `--github-token`, `--registry-user`, `--registry-token`, `--dockerhub-user`, `--dockerhub-token`, `--experiment-root`. No other flags (no `--openshift`, no secret-name overrides, no `--workspace-binding`, no `--no-cluster`, no `--redeploy-tasks`, no `--test-push`).
- Secret value resolution priority: **flag > env var > interactive prompt**. Same priority for the docker-registry `server` field where it has to be resolved (env var only; defaults to `ghcr.io` for `REGISTRY_SERVER` and `docker.io` for `DOCKERHUB_SERVER`).
- Idempotent re-run with same inputs is a no-op. `created_at` is preserved from any existing `cluster_config.json`; only set on first write.
- Hardcoded today's-setup.py defaults:
  - `secret_names = {"hf_token": "hf-secret", "registry_creds": "registry-creds", "github_token": "github-token", "dockerhub_creds": "<dockerhub-creds-or-empty>"}`. `dockerhub_creds` is `"dockerhub-creds"` only when both `--dockerhub-user` and `--dockerhub-token` resolve to non-empty values; `""` otherwise.
  - `workspaces = {"data-storage": {"persistentVolumeClaim": {"claimName": "data-pvc"}}, "source": {"persistentVolumeClaim": {"claimName": "source-pvc"}}}`.
- Exit non-zero (`1`) if **any** namespace's `ProvisionResult` has `steps_failed`. `steps_skipped` alone (soft divergence) does NOT trigger a failure exit, but it IS reflected in the per-namespace summary line.
- `ruff check pipeline/cluster.py --select F` must be clean.
- Tests live at `pipeline/tests/test_cluster_py.py`; they monkeypatch `cluster_ops._run` (and where needed, `cluster_ops._which`) using the existing `FakeRun` pattern in `pipeline/tests/test_cluster_ops.py`. No network, no real kubectl/oc.

---

## File Structure

- **Create:** `pipeline/cluster.py` — argparse + `cmd_provision()` function.
- **Create:** `pipeline/tests/test_cluster_py.py` — end-to-end mocked tests.
- **Modify:** `.github/workflows/test.yml` — add the new test file to the pytest run.
- **Modify (sweep):** `CLAUDE.md` — extend the pipeline-stage chain documentation to mention `cluster.py` (it's a new entry point, the README structure highlights existing scripts).
- **Modify (sweep):** `pipeline/README.md` if it lists entry points (verify; only update if it does).

No changes to `pipeline/setup.py` in this PR. Trimming `setup.py` is issue #424.
No changes to `pipeline/lib/cluster_ops.py` — its API is complete as merged in #430.

---

### Task 1: Argparse skeleton + executable entry point

**Files:**
- Create: `pipeline/cluster.py`

**Interfaces:**
- Consumes: none yet.
- Produces:
  - `build_parser() -> argparse.ArgumentParser` with subcommand `provision`.
  - `main(argv: list[str] | None = None) -> int`.

- [ ] **Step 1: Write the failing test**

Create `pipeline/tests/test_cluster_py.py`:

```python
"""Tests for pipeline/cluster.py — provision orchestrator."""

from __future__ import annotations

import json
import os
import subprocess
from types import SimpleNamespace

import pytest

from pipeline import cluster as cluster_cmd
from pipeline.lib import cluster_ops, layout


def _completed(returncode: int = 0, stdout: str = "", stderr: str = ""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


class FakeRun:
    def __init__(self):
        self.calls: list[list[str]] = []
        self.inputs: list[str | None] = []
        self._responses: list[tuple[list[str], SimpleNamespace]] = []

    def set(self, prefix: list[str], result):
        self._responses.append((prefix, result))

    def __call__(self, cmd, *, check=True, capture=False, input=None):
        self.calls.append(list(cmd))
        self.inputs.append(input)
        for prefix, response in self._responses:
            if cmd[: len(prefix)] == prefix:
                if check and response.returncode != 0:
                    raise subprocess.CalledProcessError(
                        response.returncode, cmd, response.stdout, response.stderr,
                    )
                return response
        return _completed(returncode=0, stdout="", stderr="")


@pytest.fixture
def fake_run(monkeypatch):
    fr = FakeRun()
    monkeypatch.setattr(cluster_ops, "_run", fr)
    return fr


@pytest.fixture(autouse=True)
def _isolated_experiment_root(tmp_path, monkeypatch):
    layout._EXPERIMENT_ROOT = tmp_path
    yield
    layout._EXPERIMENT_ROOT = None


class TestParser:
    def test_provision_subcommand_accepts_required_positional_and_namespaces(self):
        parser = cluster_cmd.build_parser()
        args = parser.parse_args(["provision", "ocp-east", "--namespaces", "a,b"])
        assert args.command == "provision"
        assert args.cluster_id == "ocp-east"
        assert args.namespaces == "a,b"

    def test_parser_rejects_unknown_top_level_subcommand(self, capsys):
        parser = cluster_cmd.build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["bogus", "ocp-east", "--namespaces", "a"])

    def test_parser_rejects_unknown_flag(self, capsys):
        parser = cluster_cmd.build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["provision", "ocp-east", "--namespaces", "a", "--no-cluster"])
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest pipeline/tests/test_cluster_py.py -x 2>&1 | tail -20
```

Expected: ImportError or AttributeError because `pipeline.cluster` doesn't exist yet.

- [ ] **Step 3: Write minimal pipeline/cluster.py**

```python
#!/usr/bin/env python3
"""sim2real cluster bootstrap — first consumer of pipeline.lib.cluster_ops.

The ``provision`` subcommand replaces today's cluster-side responsibilities
in ``pipeline/setup.py``: namespace creation, RBAC, Secrets, PVCs, and
Tekton task bindings, plus the cluster-wide Pipeline definition. Writes
``workspace/clusters/<cluster_id>/cluster_config.json`` for downstream
commands to read.

Idempotent — safe to re-run; pre-existing resources reconcile via
``kubectl apply``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pipeline.lib import cluster_ops, layout


# ── Argparse ──────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pipeline/cluster.py",
        description="Cluster-side bootstrap for the sim2real pipeline. "
                    "Idempotent — safe to re-run.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    pv = sub.add_parser("provision", help="Provision cluster-side resources")
    pv.add_argument("cluster_id", help="Cluster identifier (slug; matches workspace/clusters/<id>/)")
    pv.add_argument("--namespaces", required=True, metavar="NS1,NS2,...",
                    help="Comma-separated namespaces to provision")
    pv.add_argument("--storage-class", metavar="SC", default=None,
                    help="PVC storage class (empty → cluster default)")
    pv.add_argument("--hf-token", metavar="TOKEN", default=None,
                    help="HuggingFace API token (env: HF_TOKEN; else prompt)")
    pv.add_argument("--github-token", metavar="TOKEN", default=None,
                    help="GitHub token (env: GITHUB_TOKEN; else prompt)")
    pv.add_argument("--registry-user", metavar="USER", default=None,
                    help="Registry username (env: REGISTRY_USER; else prompt)")
    pv.add_argument("--registry-token", metavar="TOKEN", default=None,
                    help="Registry token (env: REGISTRY_TOKEN; else prompt)")
    pv.add_argument("--dockerhub-user", metavar="USER", default=None,
                    help="Docker Hub username (env: DOCKERHUB_USER; optional)")
    pv.add_argument("--dockerhub-token", metavar="TOKEN", default=None,
                    help="Docker Hub token (env: DOCKERHUB_TOKEN; optional)")
    pv.add_argument("--experiment-root", metavar="PATH", default=None,
                    help="Root of the experiment repo (default: cwd)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "provision":
        return cmd_provision(args)
    return 2  # unreachable: subparser is required


def cmd_provision(args: argparse.Namespace) -> int:
    raise NotImplementedError("filled in by subsequent tasks")


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify Task 1 tests pass**

```bash
python -m pytest pipeline/tests/test_cluster_py.py::TestParser -v 2>&1 | tail -20
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add pipeline/cluster.py pipeline/tests/test_cluster_py.py
git commit -m "feat(cluster): add cluster.py provision argparse skeleton"
```

---

### Task 2: Namespace parsing + ProvisionConfig builder

Build the in-memory `cluster_config` dict from CLI args, with the design-mandated defaults baked in. Pure function — no I/O — so it's trivially testable.

**Files:**
- Modify: `pipeline/cluster.py` — add `_build_cluster_config_dict()`.
- Modify: `pipeline/tests/test_cluster_py.py` — add `TestBuildClusterConfig`.

**Interfaces:**
- Consumes (from Task 1): argparse Namespace shape.
- Produces:
  - `_parse_namespaces(raw: str) -> list[str]` (rejects empty/whitespace-only entries; rejects empty result).
  - `_build_cluster_config_dict(cluster_id, namespaces, is_openshift, storage_class, *, has_dockerhub: bool, existing: dict | None = None) -> dict` — returns the cluster_config dict with hardcoded `secret_names` and `workspaces`. `created_at` is preserved from `existing` when present; left absent here (Task 6 sets it on the actual write).

- [ ] **Step 1: Write the failing test**

Add to `pipeline/tests/test_cluster_py.py`:

```python
class TestParseNamespaces:
    def test_splits_csv(self):
        assert cluster_cmd._parse_namespaces("a,b,c") == ["a", "b", "c"]

    def test_strips_whitespace(self):
        assert cluster_cmd._parse_namespaces(" a , b ,c ") == ["a", "b", "c"]

    def test_rejects_empty_string(self):
        with pytest.raises(ValueError):
            cluster_cmd._parse_namespaces("")

    def test_rejects_only_whitespace_or_commas(self):
        with pytest.raises(ValueError):
            cluster_cmd._parse_namespaces(" , ,, ")


class TestBuildClusterConfig:
    def test_hardcoded_defaults(self):
        cfg = cluster_cmd._build_cluster_config_dict(
            "ocp-east",
            ["a", "b"],
            is_openshift=True,
            storage_class="",
            has_dockerhub=False,
        )
        assert cfg["cluster_id"] == "ocp-east"
        assert cfg["namespaces"] == ["a", "b"]
        assert cfg["is_openshift"] is True
        assert cfg["storage_class"] == ""
        assert cfg["secret_names"] == {
            "hf_token": "hf-secret",
            "registry_creds": "registry-creds",
            "github_token": "github-token",
            "dockerhub_creds": "",
        }
        assert cfg["workspaces"] == {
            "data-storage": {"persistentVolumeClaim": {"claimName": "data-pvc"}},
            "source":       {"persistentVolumeClaim": {"claimName": "source-pvc"}},
        }

    def test_dockerhub_secret_name_set_when_creds_present(self):
        cfg = cluster_cmd._build_cluster_config_dict(
            "ocp-east", ["a"], is_openshift=False, storage_class="", has_dockerhub=True,
        )
        assert cfg["secret_names"]["dockerhub_creds"] == "dockerhub-creds"

    def test_existing_created_at_preserved(self):
        cfg = cluster_cmd._build_cluster_config_dict(
            "ocp-east", ["a"], is_openshift=False, storage_class="",
            has_dockerhub=False, existing={"created_at": "2026-01-01T00:00:00Z"},
        )
        assert cfg["created_at"] == "2026-01-01T00:00:00Z"

    def test_no_created_at_when_no_existing(self):
        cfg = cluster_cmd._build_cluster_config_dict(
            "ocp-east", ["a"], is_openshift=False, storage_class="", has_dockerhub=False,
        )
        assert "created_at" not in cfg
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest pipeline/tests/test_cluster_py.py -x -k "TestParseNamespaces or TestBuildClusterConfig" 2>&1 | tail -20
```

Expected: AttributeError — functions don't exist.

- [ ] **Step 3: Add `_parse_namespaces` and `_build_cluster_config_dict`**

Add to `pipeline/cluster.py` (above `cmd_provision`):

```python
# ── Hardcoded defaults (match today's setup.py behavior) ──────────────


_DEFAULT_SECRET_NAMES = {
    "hf_token": "hf-secret",
    "registry_creds": "registry-creds",
    "github_token": "github-token",
    "dockerhub_creds": "",  # filled in when --dockerhub-user/--dockerhub-token both provided
}

_DEFAULT_WORKSPACES = {
    "data-storage": {"persistentVolumeClaim": {"claimName": "data-pvc"}},
    "source":       {"persistentVolumeClaim": {"claimName": "source-pvc"}},
}


# ── Argument resolution helpers ──────────────────────────────────────


def _parse_namespaces(raw: str) -> list[str]:
    """Split ``--namespaces`` CSV into a non-empty list.

    Whitespace around each entry is stripped; empty / whitespace-only
    entries are dropped. Raises ``ValueError`` if the result is empty so the
    caller exits with a clean message rather than letting ``provision_namespace``
    iterate over an empty list.
    """
    items = [n.strip() for n in (raw or "").split(",") if n.strip()]
    if not items:
        raise ValueError("--namespaces resolved to an empty list")
    return items


def _build_cluster_config_dict(
    cluster_id: str,
    namespaces: list[str],
    *,
    is_openshift: bool,
    storage_class: str,
    has_dockerhub: bool,
    existing: dict | None = None,
) -> dict:
    """Compose the cluster_config dict to be written to disk.

    Pure — no I/O. ``existing`` is the prior on-disk config (or empty)
    used only to preserve ``created_at``; everything else comes from the
    current command-line invocation.
    """
    secret_names = dict(_DEFAULT_SECRET_NAMES)
    if has_dockerhub:
        secret_names["dockerhub_creds"] = "dockerhub-creds"
    cfg = {
        "cluster_id": cluster_id,
        "namespaces": list(namespaces),
        "is_openshift": bool(is_openshift),
        "storage_class": storage_class or "",
        "secret_names": secret_names,
        "workspaces": dict(_DEFAULT_WORKSPACES),
    }
    prior_created = (existing or {}).get("created_at")
    if prior_created:
        cfg["created_at"] = prior_created
    return cfg
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest pipeline/tests/test_cluster_py.py -x -k "TestParseNamespaces or TestBuildClusterConfig" -v 2>&1 | tail -20
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add pipeline/cluster.py pipeline/tests/test_cluster_py.py
git commit -m "feat(cluster): build cluster_config dict from CLI args"
```

---

### Task 3: Secret value resolution (flag > env > prompt)

Resolve the four secret payloads from a single resolver. `getpass`-based prompts only fire when neither the flag nor the env var is set; tests cover prompt behavior via `monkeypatch`.

**Files:**
- Modify: `pipeline/cluster.py` — add `_resolve_secret_values()` and the small string/getpass helpers.
- Modify: `pipeline/tests/test_cluster_py.py` — add `TestResolveSecretValues`.

**Interfaces:**
- Consumes (from Task 1): argparse Namespace.
- Produces:
  - `_resolve_secret_values(args, *, env: dict[str, str], prompter, secret_prompter) -> tuple[dict, bool]` — returns `(secret_values_dict, has_dockerhub)`. `prompter`/`secret_prompter` injected for testability (callable signatures: `prompter(label, default="") -> str`, `secret_prompter(label) -> str`).
  - For shapes, see test below — the dict matches `pipeline.lib.cluster_ops._build_secret_manifest` expectations.

- [ ] **Step 1: Write the failing test**

Add to `pipeline/tests/test_cluster_py.py`:

```python
class _FakePrompts:
    """Records every prompt call; returns canned responses by label match."""
    def __init__(self, *, plain=None, secret=None):
        self._plain = plain or {}
        self._secret = secret or {}
        self.plain_calls: list[str] = []
        self.secret_calls: list[str] = []

    def plain(self, label, default=""):
        self.plain_calls.append(label)
        return self._plain.get(label, default)

    def secret(self, label):
        self.secret_calls.append(label)
        return self._secret.get(label, "")


def _ns(**kwargs):
    """argparse.Namespace stand-in for resolution tests."""
    base = dict(
        hf_token=None, github_token=None,
        registry_user=None, registry_token=None,
        dockerhub_user=None, dockerhub_token=None,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


class TestResolveSecretValues:
    def test_all_flags_provided_no_prompts(self):
        prompts = _FakePrompts()
        args = _ns(hf_token="hf", github_token="gh",
                   registry_user="ru", registry_token="rt",
                   dockerhub_user="du", dockerhub_token="dt")
        values, has_dh = cluster_cmd._resolve_secret_values(
            args, env={}, prompter=prompts.plain, secret_prompter=prompts.secret,
        )
        assert has_dh is True
        assert values["hf_token"] == "hf"
        assert values["github_token"] == "gh"
        assert values["registry_creds"] == {"server": "ghcr.io", "user": "ru", "token": "rt"}
        assert values["dockerhub_creds"] == {"server": "docker.io", "user": "du", "token": "dt"}
        assert prompts.plain_calls == []
        assert prompts.secret_calls == []

    def test_env_var_used_when_flag_absent(self):
        prompts = _FakePrompts()
        args = _ns()
        env = {
            "HF_TOKEN": "hf-from-env",
            "GITHUB_TOKEN": "gh-from-env",
            "REGISTRY_USER": "ru-env",
            "REGISTRY_TOKEN": "rt-env",
        }
        values, has_dh = cluster_cmd._resolve_secret_values(
            args, env=env, prompter=prompts.plain, secret_prompter=prompts.secret,
        )
        assert has_dh is False
        assert values["hf_token"] == "hf-from-env"
        assert values["github_token"] == "gh-from-env"
        assert values["registry_creds"]["user"] == "ru-env"
        assert values["registry_creds"]["token"] == "rt-env"
        assert "dockerhub_creds" not in values
        assert prompts.plain_calls == []
        assert prompts.secret_calls == []

    def test_prompt_fires_when_neither_flag_nor_env(self):
        prompts = _FakePrompts(
            plain={"Registry username": "ru-prompted"},
            secret={"HuggingFace token": "hf-prompted",
                    "Registry token": "rt-prompted"},
        )
        args = _ns()
        values, has_dh = cluster_cmd._resolve_secret_values(
            args, env={}, prompter=prompts.plain, secret_prompter=prompts.secret,
        )
        assert values["hf_token"] == "hf-prompted"
        assert values["registry_creds"]["user"] == "ru-prompted"
        assert values["registry_creds"]["token"] == "rt-prompted"
        assert "HuggingFace token" in prompts.secret_calls
        assert "Registry username" in prompts.plain_calls
        assert has_dh is False

    def test_github_token_does_not_prompt(self):
        """GitHub token is optional; we never block on it. No flag and no env
        means the value is absent (cluster_ops will reuse an existing Secret
        or surface a structured skip)."""
        prompts = _FakePrompts()
        args = _ns(hf_token="hf", registry_user="ru", registry_token="rt")
        values, _ = cluster_cmd._resolve_secret_values(
            args, env={}, prompter=prompts.plain, secret_prompter=prompts.secret,
        )
        assert "github_token" not in values
        assert prompts.plain_calls == []
        assert prompts.secret_calls == []

    def test_dockerhub_server_overridable_by_env(self):
        prompts = _FakePrompts()
        args = _ns(hf_token="hf", registry_user="ru", registry_token="rt",
                   dockerhub_user="du", dockerhub_token="dt")
        env = {"DOCKERHUB_SERVER": "docker.acme.io"}
        values, _ = cluster_cmd._resolve_secret_values(
            args, env=env, prompter=prompts.plain, secret_prompter=prompts.secret,
        )
        assert values["dockerhub_creds"]["server"] == "docker.acme.io"

    def test_registry_server_overridable_by_env(self):
        prompts = _FakePrompts()
        args = _ns(hf_token="hf", registry_user="ru", registry_token="rt")
        env = {"REGISTRY_SERVER": "quay.io"}
        values, _ = cluster_cmd._resolve_secret_values(
            args, env=env, prompter=prompts.plain, secret_prompter=prompts.secret,
        )
        assert values["registry_creds"]["server"] == "quay.io"

    def test_partial_dockerhub_creds_skip(self):
        """User without token (or vice versa) does NOT register dockerhub_creds —
        we only emit a fully-formed dict when both are present."""
        prompts = _FakePrompts()
        args = _ns(hf_token="hf", registry_user="ru", registry_token="rt",
                   dockerhub_user="du")  # token missing
        values, has_dh = cluster_cmd._resolve_secret_values(
            args, env={}, prompter=prompts.plain, secret_prompter=prompts.secret,
        )
        assert has_dh is False
        assert "dockerhub_creds" not in values
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest pipeline/tests/test_cluster_py.py -x -k "TestResolveSecretValues" 2>&1 | tail -20
```

Expected: AttributeError.

- [ ] **Step 3: Add `_resolve_secret_values` and default prompts**

Add to `pipeline/cluster.py`:

```python
import getpass


def _default_plain_prompter(label: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    raw = input(f"{label}{suffix}: ").strip()
    return raw or default


def _default_secret_prompter(label: str) -> str:
    return getpass.getpass(f"{label}: ").strip()


def _resolve_secret_values(
    args,
    *,
    env: dict[str, str],
    prompter,
    secret_prompter,
) -> tuple[dict, bool]:
    """Resolve the four secret payloads using flag > env > prompt.

    Returns ``(secret_values, has_dockerhub)``. The dict shape matches
    ``pipeline.lib.cluster_ops._build_secret_manifest`` expectations:

      * ``hf_token`` / ``github_token`` → ``str``
      * ``registry_creds`` / ``dockerhub_creds`` → ``{"server", "user", "token"}``

    Resolution rules:

    * Required secrets (``hf_token``, ``registry_creds``) prompt the
      operator when neither flag nor env var supplies a value.
    * Optional secrets (``github_token``, ``dockerhub_creds``) never
      prompt — when no flag/env is set, the key is omitted entirely so
      :func:`cluster_ops.provision_namespace` records a structured skip
      (or reuses a pre-installed Secret).
    * Registry/dockerhub ``server`` fields default to ``ghcr.io`` and
      ``docker.io`` respectively; overridable by ``REGISTRY_SERVER`` /
      ``DOCKERHUB_SERVER`` env vars. No flag, per design.
    """
    values: dict[str, object] = {}

    # hf_token — required; prompt if absent.
    hf = args.hf_token or env.get("HF_TOKEN") or secret_prompter("HuggingFace token")
    if hf:
        values["hf_token"] = hf

    # github_token — optional; never prompt.
    gh = args.github_token or env.get("GITHUB_TOKEN")
    if gh:
        values["github_token"] = gh

    # registry_creds — user + token required together; prompt if missing.
    reg_user = args.registry_user or env.get("REGISTRY_USER") \
        or prompter("Registry username")
    reg_token = args.registry_token or env.get("REGISTRY_TOKEN") \
        or (secret_prompter("Registry token") if reg_user else "")
    if reg_user and reg_token:
        values["registry_creds"] = {
            "server": env.get("REGISTRY_SERVER") or "ghcr.io",
            "user": reg_user,
            "token": reg_token,
        }

    # dockerhub_creds — optional; never prompt; both fields required.
    dh_user = args.dockerhub_user or env.get("DOCKERHUB_USER")
    dh_token = args.dockerhub_token or env.get("DOCKERHUB_TOKEN")
    has_dockerhub = bool(dh_user and dh_token)
    if has_dockerhub:
        values["dockerhub_creds"] = {
            "server": env.get("DOCKERHUB_SERVER") or "docker.io",
            "user": dh_user,
            "token": dh_token,
        }

    return values, has_dockerhub
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest pipeline/tests/test_cluster_py.py -x -k "TestResolveSecretValues" -v 2>&1 | tail -30
```

Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add pipeline/cluster.py pipeline/tests/test_cluster_py.py
git commit -m "feat(cluster): resolve secret values flag > env > prompt"
```

---

### Task 4: Per-namespace summary line formatter

A small pure helper so the orchestrator step can call it without leaking print formatting into the test surface.

**Files:**
- Modify: `pipeline/cluster.py` — add `_format_summary_line()`.
- Modify: `pipeline/tests/test_cluster_py.py` — add `TestFormatSummary`.

**Interfaces:**
- Consumes: `pipeline.lib.cluster_ops.ProvisionResult`.
- Produces:
  - `_format_summary_line(result: ProvisionResult) -> str` returning one of:
    - `"<ns>: ok"` when nothing diverged.
    - `"<ns>: diverged: <reason>"` when `steps_failed` or `steps_skipped` non-empty.
    - Reason concatenates `failed=<step>(<msg>)` then `skipped=<step>(<msg>)`, comma-separated. Failed items first so the most-severe divergence is the first the operator sees.

- [ ] **Step 1: Write the failing test**

Add to `pipeline/tests/test_cluster_py.py`:

```python
class TestFormatSummary:
    def test_ok_when_no_divergence(self):
        r = cluster_ops.ProvisionResult(namespace="ns-a", steps_ok=["namespace", "rbac"])
        assert cluster_cmd._format_summary_line(r) == "ns-a: ok"

    def test_skipped_only(self):
        r = cluster_ops.ProvisionResult(
            namespace="ns-a",
            steps_ok=["namespace"],
            steps_skipped=[("secrets", "no value provided for: hf_token(hf-secret)")],
        )
        line = cluster_cmd._format_summary_line(r)
        assert line.startswith("ns-a: diverged: ")
        assert "skipped=secrets" in line
        assert "no value provided" in line

    def test_failed_listed_before_skipped(self):
        r = cluster_ops.ProvisionResult(
            namespace="ns-a",
            steps_failed=[("rbac", "kubectl forbidden")],
            steps_skipped=[("secrets", "no value")],
        )
        line = cluster_cmd._format_summary_line(r)
        # failed= appears before skipped=
        assert line.index("failed=") < line.index("skipped=")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest pipeline/tests/test_cluster_py.py -x -k "TestFormatSummary" 2>&1 | tail -15
```

Expected: AttributeError.

- [ ] **Step 3: Add `_format_summary_line`**

Append to `pipeline/cluster.py`:

```python
def _format_summary_line(result) -> str:
    """One-line per-namespace summary for stdout printing.

    Failed items come first because they block the exit code (steps_skipped
    is soft divergence; steps_failed is the only thing that flips the exit
    to non-zero — operators scanning a multi-namespace summary care about
    the failures first).
    """
    if not result.diverged:
        return f"{result.namespace}: ok"
    parts: list[str] = []
    for step, reason in result.steps_failed:
        parts.append(f"failed={step}({reason})")
    for step, reason in result.steps_skipped:
        parts.append(f"skipped={step}({reason})")
    return f"{result.namespace}: diverged: " + ", ".join(parts)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest pipeline/tests/test_cluster_py.py -x -k "TestFormatSummary" -v 2>&1 | tail -15
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add pipeline/cluster.py pipeline/tests/test_cluster_py.py
git commit -m "feat(cluster): per-namespace summary line formatter"
```

---

### Task 5: `cmd_provision` orchestrator end-to-end

Wire everything together: parse → resolve → write cluster_config → apply cluster resources → provision each namespace → print summaries → return exit code.

`apply_cluster_resources` reads `cluster_config.json` from disk via `read_cluster_config(cluster_id)`. So the write must happen BEFORE `apply_cluster_resources`. Design step ordering shows write at step 7 but the dependency forces write earlier; the resulting on-disk content is identical either way (idempotent).

**Files:**
- Modify: `pipeline/cluster.py` — implement `cmd_provision()`.
- Modify: `pipeline/tests/test_cluster_py.py` — add `TestProvisionOrchestration`.

**Interfaces:**
- Consumes: every prior task.
- Produces: `cmd_provision(args) -> int`.

Behavior contract (matches issue body):

1. Resolve `--experiment-root` via `layout.set_experiment_root`.
2. Read any existing `cluster_config.json` for `cluster_id` (just for `created_at` preservation).
3. Parse `--namespaces`; fail fast with a clear message + exit 2 if empty.
4. Detect OpenShift via `cluster_ops.detect_openshift()`.
5. Resolve `secret_values` (Task 3).
6. Build `cluster_config` dict (Task 2). Stamp `created_at` only if not preserved from `existing`.
7. Write `cluster_config` (atomic; `cluster_ops.write_cluster_config`).
8. Call `cluster_ops.apply_cluster_resources(cluster_id)`. Catch `FileNotFoundError` (pipeline.yaml missing) and `subprocess.CalledProcessError` (per-namespace apply failure) → print error, exit 1.
9. For each namespace, call `cluster_ops.provision_namespace(ns, cluster_config, secret_values=secret_values)`. Print summary line. Accumulate `ProvisionResult`.
10. Return `1` if any result has `steps_failed`; `0` otherwise.

- [ ] **Step 1: Write the failing test**

Add to `pipeline/tests/test_cluster_py.py`:

```python
class TestProvisionOrchestration:
    """End-to-end orchestration over mocked kubectl/oc."""

    def _full_arg_setup(self, monkeypatch, fake_run, tmp_path):
        """Common harness: returns the env dict + a 'no-prompt' getpass."""
        monkeypatch.setattr(cluster_ops, "_which", lambda cmd: False)  # not OpenShift
        # No RBAC YAMLs / tekton YAMLs on disk so those steps either fail (rbac)
        # or skip (tekton). For green-path tests, monkeypatch Path methods.
        return None

    def test_happy_path_returns_zero(self, fake_run, monkeypatch, tmp_path, capsys):
        # Pretend YAML files exist.
        monkeypatch.setattr(cluster_ops.Path, "exists", lambda self: True)
        monkeypatch.setattr(cluster_ops.Path, "read_text", lambda self: "# yaml\n")

        real_path = cluster_ops.Path
        def fake_glob(self, pattern):
            if pattern == "*.yaml":
                return [real_path("/fake/a.yaml"), real_path("/fake/b.yaml")]
            return []
        monkeypatch.setattr(cluster_ops.Path, "glob", fake_glob)

        # Namespace pre-checks: not present yet.
        fake_run.set(["kubectl", "get", "ns"], _completed(returncode=1))
        # PVC pre-checks: not present yet.
        fake_run.set(["kubectl", "get", "pvc"], _completed(returncode=1))

        rc = cluster_cmd.main([
            "provision", "ocp-east",
            "--namespaces", "ns-a,ns-b",
            "--hf-token", "hf-x",
            "--github-token", "gh-x",
            "--registry-user", "ru",
            "--registry-token", "rt",
        ])
        assert rc == 0

        # cluster_config.json was written with the expected shape.
        cfg = cluster_ops.read_cluster_config("ocp-east")
        assert cfg["cluster_id"] == "ocp-east"
        assert cfg["namespaces"] == ["ns-a", "ns-b"]
        assert cfg["is_openshift"] is False
        assert "created_at" in cfg
        assert cfg["secret_names"]["dockerhub_creds"] == ""  # no dockerhub flags

        # Per-namespace summary lines printed.
        out = capsys.readouterr().out
        assert "ns-a: ok" in out
        assert "ns-b: ok" in out

    def test_returns_one_when_any_namespace_failed(self, fake_run, monkeypatch, capsys):
        monkeypatch.setattr(cluster_ops.Path, "exists", lambda self: True)
        monkeypatch.setattr(cluster_ops.Path, "read_text", lambda self: "# yaml\n")
        monkeypatch.setattr(cluster_ops.Path, "glob",
                            lambda self, p: ([cluster_ops.Path("/fake/a.yaml")] if p == "*.yaml" else []))

        # Force RBAC apply to fail on ns-b only.
        def custom_run(cmd, *, check=True, capture=False, input=None):
            fake_run.calls.append(list(cmd))
            fake_run.inputs.append(input)
            # ns existence pre-checks: ns-a present, ns-b created fresh
            if cmd[:3] == ["kubectl", "get", "ns"]:
                return _completed(returncode=1)
            if cmd[:3] == ["kubectl", "create", "ns"]:
                return _completed(returncode=0)
            if cmd[:3] == ["kubectl", "get", "pvc"]:
                return _completed(returncode=1)
            if cmd[:3] == ["kubectl", "apply", "-f"] and input and "ns-b" not in (input or "") and False:
                pass
            # Detect rbac apply via input containing fake yaml content + ns-b: simplest is
            # to fail when an apply has cmd ['kubectl','apply','-f','-'] and input contains '# a.yaml'
            # — every test apply uses input='# yaml\n' but we cannot disambiguate by content here.
            # Instead, fail on the SECOND kubectl apply -f - call (rbac of ns-b).
            return _completed(returncode=0)

        # Replace with a counter-based failure: 1st rbac apply (ns-a) ok, 2nd (ns-b) fails.
        seen = {"applies": 0}
        def counting_run(cmd, *, check=True, capture=False, input=None):
            fake_run.calls.append(list(cmd))
            fake_run.inputs.append(input)
            if cmd[:3] == ["kubectl", "get", "ns"]:
                return _completed(returncode=1)
            if cmd[:3] == ["kubectl", "create", "ns"]:
                return _completed(returncode=0)
            if cmd[:3] == ["kubectl", "get", "pvc"]:
                return _completed(returncode=1)
            if cmd[:4] == ["kubectl", "apply", "-f", "-"]:
                seen["applies"] += 1
                # First per-namespace apply is rbac of ns-a → ok.
                # Once we cross 6 applies (ns-a: 5 rbac + 2 pvc + 2 tekton -> way too many to count),
                # too fragile. Use a simpler trigger: ANY 'apply' on a ConfigMap-shaped yaml containing
                # the literal 'ns-b' substring. Skip — easier to monkeypatch _step_rbac directly.
            return _completed(returncode=0)

        # Easier: monkeypatch _step_rbac for ns-b only.
        real_step_rbac = cluster_ops._step_rbac
        def selective_rbac(ns, cfg, sv):
            if ns == "ns-b":
                return ("failed", "synthetic kubectl forbidden")
            return real_step_rbac(ns, cfg, sv)
        monkeypatch.setattr(cluster_ops, "_step_rbac", selective_rbac)

        rc = cluster_cmd.main([
            "provision", "ocp-east",
            "--namespaces", "ns-a,ns-b",
            "--hf-token", "hf",
            "--registry-user", "ru", "--registry-token", "rt",
        ])
        assert rc == 1
        out = capsys.readouterr().out
        assert "ns-a: ok" in out
        assert "ns-b: diverged" in out
        assert "rbac" in out

    def test_idempotent_rerun_preserves_created_at(self, fake_run, monkeypatch, tmp_path):
        monkeypatch.setattr(cluster_ops.Path, "exists", lambda self: True)
        monkeypatch.setattr(cluster_ops.Path, "read_text", lambda self: "# yaml\n")
        monkeypatch.setattr(cluster_ops.Path, "glob",
                            lambda self, p: ([cluster_ops.Path("/fake/a.yaml")] if p == "*.yaml" else []))
        fake_run.set(["kubectl", "get", "ns"], _completed(returncode=0))  # ns already present
        fake_run.set(["kubectl", "get", "pvc"], _completed(returncode=0))  # pvcs already present

        # First run.
        rc1 = cluster_cmd.main([
            "provision", "ocp-east", "--namespaces", "ns-a",
            "--hf-token", "hf", "--registry-user", "ru", "--registry-token", "rt",
        ])
        assert rc1 == 0
        first = cluster_ops.read_cluster_config("ocp-east")
        first_created = first["created_at"]

        # Second run — must preserve created_at byte-for-byte.
        rc2 = cluster_cmd.main([
            "provision", "ocp-east", "--namespaces", "ns-a",
            "--hf-token", "hf", "--registry-user", "ru", "--registry-token", "rt",
        ])
        assert rc2 == 0
        second = cluster_ops.read_cluster_config("ocp-east")
        assert second["created_at"] == first_created

    def test_empty_namespaces_exits_two(self, fake_run, capsys):
        rc = cluster_cmd.main([
            "provision", "ocp-east", "--namespaces", "  , ,",
            "--hf-token", "hf", "--registry-user", "ru", "--registry-token", "rt",
        ])
        assert rc == 2
        err = capsys.readouterr().err
        assert "--namespaces" in err

    def test_pipeline_yaml_missing_exits_one(self, fake_run, monkeypatch, capsys):
        # Force apply_cluster_resources to raise FileNotFoundError.
        def boom(_):
            raise FileNotFoundError("Pipeline YAML not found at /nowhere")
        monkeypatch.setattr(cluster_ops, "apply_cluster_resources", boom)

        rc = cluster_cmd.main([
            "provision", "ocp-east", "--namespaces", "ns-a",
            "--hf-token", "hf", "--registry-user", "ru", "--registry-token", "rt",
        ])
        assert rc == 1
        err = capsys.readouterr().err
        assert "Pipeline YAML" in err

    def test_dockerhub_creds_recorded_when_provided(self, fake_run, monkeypatch):
        monkeypatch.setattr(cluster_ops.Path, "exists", lambda self: True)
        monkeypatch.setattr(cluster_ops.Path, "read_text", lambda self: "# yaml\n")
        monkeypatch.setattr(cluster_ops.Path, "glob",
                            lambda self, p: ([cluster_ops.Path("/fake/a.yaml")] if p == "*.yaml" else []))
        fake_run.set(["kubectl", "get", "ns"], _completed(returncode=0))
        fake_run.set(["kubectl", "get", "pvc"], _completed(returncode=0))

        rc = cluster_cmd.main([
            "provision", "ocp-east", "--namespaces", "ns-a",
            "--hf-token", "hf", "--registry-user", "ru", "--registry-token", "rt",
            "--dockerhub-user", "du", "--dockerhub-token", "dt",
        ])
        assert rc == 0
        cfg = cluster_ops.read_cluster_config("ocp-east")
        assert cfg["secret_names"]["dockerhub_creds"] == "dockerhub-creds"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest pipeline/tests/test_cluster_py.py -x -k "TestProvisionOrchestration" 2>&1 | tail -25
```

Expected: `NotImplementedError` from the placeholder `cmd_provision`.

- [ ] **Step 3: Implement `cmd_provision`**

Replace the placeholder body in `pipeline/cluster.py`:

```python
import os
import subprocess
from datetime import datetime, timezone


def cmd_provision(args: argparse.Namespace) -> int:
    layout.set_experiment_root(args.experiment_root)
    cluster_id = args.cluster_id

    # 1. Parse namespaces — fail fast on empty.
    try:
        namespaces = _parse_namespaces(args.namespaces)
    except ValueError as exc:
        print(f"error: --namespaces: {exc}", file=sys.stderr)
        return 2

    # 2. Existing config (for created_at preservation).
    existing = cluster_ops.read_cluster_config(cluster_id)

    # 3. Detect OpenShift once.
    is_openshift = cluster_ops.detect_openshift()

    # 4. Resolve secret values from flag > env > prompt.
    secret_values, has_dockerhub = _resolve_secret_values(
        args,
        env=os.environ,
        prompter=_default_plain_prompter,
        secret_prompter=_default_secret_prompter,
    )

    # 5. Build cluster_config; stamp created_at if first-time write.
    cluster_config = _build_cluster_config_dict(
        cluster_id, namespaces,
        is_openshift=is_openshift,
        storage_class=args.storage_class or "",
        has_dockerhub=has_dockerhub,
        existing=existing,
    )
    if "created_at" not in cluster_config:
        cluster_config["created_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # 6. Persist BEFORE apply_cluster_resources (it reads cluster_config from disk).
    cluster_ops.write_cluster_config(cluster_id, cluster_config)

    # 7. Cluster-wide resources.
    try:
        cluster_ops.apply_cluster_resources(cluster_id)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        msg = (exc.stderr or exc.stdout or str(exc)).strip()
        print(f"error: cluster-wide apply failed: {msg}", file=sys.stderr)
        return 1

    # 8. Provision each namespace; print one summary line each.
    any_failed = False
    for ns in namespaces:
        result = cluster_ops.provision_namespace(
            ns, cluster_config, secret_values=secret_values,
        )
        print(_format_summary_line(result))
        if result.steps_failed:
            any_failed = True

    return 1 if any_failed else 0
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest pipeline/tests/test_cluster_py.py -v 2>&1 | tail -40
```

Expected: all tests in `TestProvisionOrchestration` green + everything from earlier tasks still green.

- [ ] **Step 5: Commit**

```bash
git add pipeline/cluster.py pipeline/tests/test_cluster_py.py
git commit -m "feat(cluster): cmd_provision orchestrator end-to-end"
```

---

### Task 6: CI wiring + repo-wide doc sweep

Run full test suite + lint locally to mirror CI, then add the new test file to `.github/workflows/test.yml`. Also sweep docs for stale references.

**Files:**
- Modify: `.github/workflows/test.yml` — add `pipeline/tests/test_cluster_py.py` to the explicit list (CI only covers paths listed; CLAUDE.md is explicit about this).
- Modify (sweep): `CLAUDE.md` — the "Pipeline Library" / "Transfer Pipeline" sections currently describe the four scripts (`setup.py`, `prepare.py`, `deploy.py`, `run.py`). Add a brief note that `cluster.py` exists as the new cluster-bootstrap entry point. Do NOT rewrite setup.py's section — setup.py is not being trimmed in this PR.
- Modify (sweep): `pipeline/README.md` — if it lists scripts; check first.

- [ ] **Step 1: Run full test suite + lint locally**

```bash
python -m pytest pipeline/ -v 2>&1 | tail -30
ruff check pipeline/cluster.py --select F 2>&1
ruff check pipeline/ --select F 2>&1 | tail -5
```

Expected: tests all green; ruff prints "All checks passed!" for both invocations.

- [ ] **Step 2: Inspect CI workflow**

```bash
cat .github/workflows/test.yml
```

Look for the `pytest` command — it explicitly lists files/directories per CLAUDE.md: "CI only covers paths explicitly listed."

- [ ] **Step 3: Add the new test file to the workflow**

Edit `.github/workflows/test.yml`: find the `pytest` invocation that currently lists `pipeline/ .claude/skills/...` and confirm `pipeline/tests/test_cluster_py.py` is already covered by the `pipeline/` glob. If `pipeline/` is passed as a directory, no change is required. If individual test files are enumerated, add `pipeline/tests/test_cluster_py.py` to the list.

Verify your edit:

```bash
git diff .github/workflows/test.yml
```

- [ ] **Step 4: Sweep CLAUDE.md and pipeline/README.md**

```bash
grep -n "setup.py\|prepare.py\|deploy.py\|run.py\|cluster.py" CLAUDE.md pipeline/README.md 2>&1 | head -40
```

Decide for each hit:
- The "Transfer Pipeline" diagram block (`setup.py → prepare.py → ...`) — add a brief note that `cluster.py` is a new cluster-side entry point. setup.py is not being trimmed in this PR; just acknowledge cluster.py exists.
- The pipeline/setup.py invocation example — leave untouched.
- `pipeline/README.md` — if it lists scripts, add cluster.py to the list. If not, no change.

Apply the minimal edit needed.

- [ ] **Step 5: Commit + final lint**

```bash
ruff check pipeline/cluster.py pipeline/tests/test_cluster_py.py --select F 2>&1
git status
git add .github/workflows/test.yml CLAUDE.md pipeline/README.md 2>&1
git diff --cached --stat 2>&1
git commit -m "docs(cluster): document cluster.py entry point; wire CI"
```

(Only stage files that actually changed; `git add` of an unchanged file is a no-op.)

---

### Task 7: Push branch, open PR

- [ ] **Step 1: Push the branch**

```bash
git push -u origin refactor/v2-step-0-issue-421-cluster-py-provision
```

- [ ] **Step 2: Open PR**

Title: `feat(pipeline): cluster.py provision orchestrator (#421)`

Body must include:
- `Closes #421`
- Brief summary: what the PR adds and why it doesn't touch `setup.py` (that's #424).
- Note on the design contract: CLI surface matches design exactly; no extra flags; idempotent.
- Doc sweep results: what was checked, what was updated.

```bash
gh pr create --base refactor/v2-step-0 --title "feat(pipeline): cluster.py provision orchestrator (#421)" --body-file - <<'EOF'
Closes #421
Part of #416 (Epic: Step 0 — Foundation: workspace + cluster provisioning).

## Summary

Adds `pipeline/cluster.py` with a `provision` subcommand — the first consumer of `pipeline/lib/cluster_ops.py`. Carves cluster-side bootstrap (namespaces, RBAC, secrets, PVCs, Tekton tasks, cluster-wide Pipeline definition) out of `setup.py` while leaving `setup.py` itself untouched. `setup.py` trimming is issue #424.

CLI matches the design contract exactly:

```
python pipeline/cluster.py provision <cluster_id> \
    --namespaces NS1,NS2,NS3 \
    [--storage-class SC] \
    [--hf-token TOKEN] [--github-token TOKEN] \
    [--registry-user USER] [--registry-token TOKEN] \
    [--dockerhub-user USER] [--dockerhub-token TOKEN] \
    [--experiment-root PATH]
```

No extra flags (no `--openshift`, no secret-name overrides, no `--workspace-binding`, no `--no-cluster`, no `--redeploy-tasks`, no `--test-push`).

Secret resolution: flag > env var > prompt. Server fields for docker-registry secrets default to `ghcr.io` (`REGISTRY_SERVER`) and `docker.io` (`DOCKERHUB_SERVER`); both overridable by env var, no flag (per design's "no extra flags").

## Notable design choices

- **Write before apply**: `cluster_ops.apply_cluster_resources` reads `cluster_config.json` from disk via `read_cluster_config`. The script therefore writes `cluster_config.json` *before* invoking `apply_cluster_resources`, even though the design's textual order lists write at step 7 and apply at step 4 — the dependency is one-way and the resulting on-disk content is identical either way (idempotent).
- **`created_at` preservation**: when an existing `cluster_config.json` already has `created_at`, it's preserved; otherwise stamped at first write.
- **`dockerhub_creds` opt-in**: `secret_names.dockerhub_creds` is only set to `"dockerhub-creds"` when both `--dockerhub-user` and `--dockerhub-token` resolve to non-empty values. Otherwise the field is the empty string, matching the schema in the parent epic's design.

## Tests

`pipeline/tests/test_cluster_py.py` covers:
- Argparse surface (positional + required `--namespaces`; rejection of unknown flags).
- `_parse_namespaces`, `_build_cluster_config_dict`, `_resolve_secret_values`, `_format_summary_line` — pure helpers.
- End-to-end orchestration over mocked kubectl/oc: happy path, one-namespace-failed, idempotent re-run preserves `created_at`, empty `--namespaces` → exit 2, missing pipeline.yaml → exit 1, dockerhub creds plumb through.

All mocks use the existing `FakeRun` pattern from `pipeline/tests/test_cluster_ops.py`. No network, no real cluster.

## Doc sweep

- `CLAUDE.md`: extended the pipeline-stage chain notes to mention `cluster.py` as a new entry point. `setup.py`'s section is untouched — it's still the legacy flow until #424.
- `pipeline/README.md`: verified script list and updated if needed.
- No skill prompts reference `cluster.py` yet (it's a new file).

## Out of scope

- Trimming `setup.py` — issue #424.
- `setup_config.json` retirement — epic-level work.
EOF
```

If the `gh` call fails with a token-scope error, retry with `unset GITHUB_TOKEN GH_TOKEN; gh pr create ...`.

- [ ] **Step 3: Capture PR URL**

```bash
gh pr view --json url,number 2>&1
```

Record the PR number — fix-issue's loop hands it to the `review-changes` Workflow.

---

## Self-Review Checklist

- [ ] Spec coverage: every acceptance criterion in issue #421 is touched.
  - cluster.py exists with `provision` subcommand → Task 1.
  - CLI matches design exactly → Task 1 + parser-rejects-unknown test.
  - Secret resolution priority → Task 3.
  - OpenShift auto-detected → Task 5 (`cluster_ops.detect_openshift()`).
  - Idempotent re-run → Task 5 (`test_idempotent_rerun_preserves_created_at`).
  - Per-namespace summary line → Task 4 + Task 5.
  - Non-zero exit on failure → Task 5 (`test_returns_one_when_any_namespace_failed`).
  - Test file end-to-end → Task 5.
  - ruff clean → Task 6.
  - Demo from design — covered manually post-merge; not in CI scope.
- [ ] No placeholders.
- [ ] Type consistency: secret_values dict shape matches `cluster_ops._build_secret_manifest` (verified by reading `pipeline/lib/cluster_ops.py:476-542`).
