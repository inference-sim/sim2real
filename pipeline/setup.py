#!/usr/bin/env python3
"""sim2real workspace config writer.

Writes `workspace/setup_config.json` and `workspace/runs/<run>/run_metadata.json`
with operator-side fields. Optionally runs a registry credential test push.

Cluster-side provisioning (namespaces, RBAC, secrets, PVCs, Tekton) lives in
`pipeline/cluster.py provision`; this script no longer touches the cluster.
"""

import argparse
import getpass
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

@dataclass
class SetupConfig:
    registry: str
    repo_name: str
    run_name: str
    registry_user: str
    registry_token: str
    orchestrator_image: str = ""

# ── Repo layout ──────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parent.parent
# Ensure repo root is on sys.path when run as a script (python pipeline/setup.py)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Overridden in main() when --experiment-root is specified.
EXPERIMENT_ROOT = REPO_ROOT

# ── Color helpers ────────────────────────────────────────────────────
_tty = sys.stdout.isatty()
def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _tty else text

from pipeline.lib.log import info, ok, warn, err
def step(n, total, title: str) -> None:
    print("\n" + _c("36", f"━━━ [{n}/{total}] {title} ━━━"))

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pipeline/setup.py",
        description="Workspace config writer for the sim2real pipeline.\n"
                    "Writes setup_config.json + run_metadata.json. Idempotent.\n"
                    "Cluster-side provisioning lives in `cluster.py provision`.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Environment variables (alternatives to --flags):
  REGISTRY_USER, REGISTRY_TOKEN, ORCHESTRATOR_IMAGE

Examples:
  python pipeline/setup.py --registry quay.io/me
  python pipeline/setup.py --test-push --registry quay.io/me \\
    --registry-user u --registry-token t
""",
    )
    p.add_argument("--registry",       metavar="REG",   help="Container registry host (e.g. quay.io/username)")
    p.add_argument("--repo-name",      metavar="NAME",  default=None,
                                                        help="Registry repository name [llm-d-inference-scheduler]")
    p.add_argument("--registry-user",  metavar="USER",  help="Registry username (used by --test-push)")
    p.add_argument("--registry-token", metavar="TOKEN", help="Registry token (used by --test-push)")
    p.add_argument("--run",            metavar="NAME",  help="Run name [sim2real-YYYY-MM-DD]")
    p.add_argument("--experiment-root", metavar="PATH", dest="experiment_root",
                   help="Root of the experiment repo (default: current working directory)")
    p.add_argument("--test-push",      action="store_true",
                                       help="Auto-accept test push prompt")
    p.add_argument("--test-push-tag",  metavar="TAG",   default="_test-image-push",
                                       help="Image tag for test push [%(default)s]")
    p.add_argument("--orchestrator-image", metavar="IMAGE",
                                       help="Orchestrator container image for --remote mode "
                                            "(e.g. ghcr.io/inference-sim/sim2real/orchestrator:latest)")
    return p

def run(cmd: list[str], *, check: bool = True, capture: bool = False,
        input: str | None = None) -> subprocess.CompletedProcess:
    """Run a command, raise on non-zero unless check=False."""
    return subprocess.run(cmd, check=check, text=True, capture_output=capture, input=input)

def which(cmd: str) -> bool:
    import shutil
    return shutil.which(cmd) is not None

def prompt(var_name: str, message: str, default: str = "", env_var: str = "") -> str:
    """Return env_var value, or prompt interactively with optional default."""
    value = os.environ.get(env_var or var_name.upper(), "")
    if value:
        return value
    suffix = f" [{default}]" if default else ""
    raw = input(f"{message}{suffix}: ").strip()
    return raw or default

def prompt_secret(message: str, env_var: str = "") -> str:
    """Return env var value, or prompt with hidden input.

    At the prompt the user may also type an env var name (e.g. REGISTRY_TOKEN)
    instead of the secret itself — the script will resolve it from the environment.
    Character count is printed after entry so the user can confirm input was received.
    """
    import re
    value = os.environ.get(env_var, "") if env_var else ""
    if value:
        return value
    hint = f"  (or type an env var name, e.g. {env_var})" if env_var else ""
    print(hint)
    raw = getpass.getpass(f"{message}: ")
    if re.match(r'^[A-Z][A-Z0-9_]{1,}$', raw):
        resolved = os.environ.get(raw, "")
        if resolved:
            ok(f"Read from env var {raw} ({len(resolved)} characters)")
            return resolved
    if raw:
        ok(f"Token received ({len(raw)} characters)")
    return raw

# ── Helpers ──────────────────────────────────────────────────────────

def _detect_container_runtime() -> str:
    """Auto-detect podman or docker. Returns empty string if neither found."""
    return next((rt for rt in ["podman", "docker"] if which(rt)), "")


def _resolve_run_name(args: argparse.Namespace) -> tuple[str, Path]:
    """Resolve run name, handle directory collision."""
    default = f"sim2real-{datetime.now().strftime('%Y-%m-%d')}"
    run_name = args.run or prompt("run_name", "Enter a name for this run", default=default)

    while True:
        run_dir = EXPERIMENT_ROOT / "workspace" / "runs" / run_name
        if run_dir.exists():
            warn(f"Run '{run_name}' already exists at {run_dir}")
            answer = prompt("overwrite", "Overwrite it, or enter a new name? [overwrite/NAME]",
                            default="overwrite")
            if answer.strip().lower() in ("overwrite", "o", "y", "yes", ""):
                break
            run_name = answer.strip()
        else:
            break

    run_dir.mkdir(parents=True, exist_ok=True)
    return run_name, run_dir


# ── Step 1: Configuration ────────────────────────────────────────────

def collect_config(args: argparse.Namespace) -> tuple[SetupConfig, Path, str]:
    """Step 1: Collect operator-side config. Returns (config, run_dir, container_runtime)."""
    step(1, 3, "Configuration")

    config_path = EXPERIMENT_ROOT / "workspace" / "setup_config.json"
    defaults = json.loads(config_path.read_text()) if config_path.exists() else {}
    if defaults:
        info("Loading defaults from previous setup_config.json")

    reg_default = defaults.get("registry", "")
    registry = args.registry or prompt("registry",
        "Container registry (e.g. quay.io/username)", default=reg_default)

    repo_default = defaults.get("repo_name", "llm-d-inference-scheduler")
    if args.repo_name is not None:
        repo_name = args.repo_name
    else:
        repo_name = prompt("repo_name", "Registry repo name", default=repo_default)

    run_name, run_dir = _resolve_run_name(args)

    # Registry credentials are workspace-scoped (used by step_test_push). The
    # in-cluster registry-secret is created by cluster.py provision, which
    # collects credentials independently — see #435 for the dedup plan.
    reg_user = args.registry_user or os.environ.get("REGISTRY_USER", "")
    reg_token = args.registry_token or os.environ.get("REGISTRY_TOKEN", "")
    docker_server = registry.split("/")[0] if registry else ""
    if not reg_user and not reg_token and docker_server == "ghcr.io":
        github_token = os.environ.get("GITHUB_TOKEN", "")
        if github_token:
            reg_user = registry.split("/")[1] if "/" in registry else "github"
            reg_token = github_token
            info("Using GITHUB_TOKEN for ghcr.io authentication.")
    if registry and not reg_user:
        reg_user = prompt("registry_user",
            "Registry username (or press Enter to use container login)", default="")
    if registry and reg_user and not reg_token:
        reg_token = prompt_secret("Registry token", env_var="REGISTRY_TOKEN")

    container_rt = _detect_container_runtime()

    _orch_default = (
        defaults.get("orchestrator_image", "")
        or "ghcr.io/inference-sim/sim2real/orchestrator:latest"
    )
    orchestrator_image = args.orchestrator_image or prompt(
        "orchestrator_image",
        "Orchestrator image for --remote mode (press Enter to accept default)",
        default=_orch_default,
        env_var="ORCHESTRATOR_IMAGE",
    )

    cfg = SetupConfig(
        registry=registry, repo_name=repo_name,
        run_name=run_name,
        registry_user=reg_user, registry_token=reg_token,
        orchestrator_image=orchestrator_image,
    )
    ok(f"Configuration complete (registry={registry or '(none)'})")
    return cfg, run_dir, container_rt

# ── Step 2: Registry credential test ─────────────────────────────────

def _do_test_push(container_rt: str, full_image: str, docker_server: str,
                  reg_user: str, reg_token: str) -> bool:
    """Execute pull-tag-push-pull sequence. Returns True on success."""
    base_image = "busybox:latest"

    info(f"Pulling {base_image}...")
    if run([container_rt, "pull", base_image], check=False, capture=True).returncode != 0:
        err(f"Could not pull {base_image}")
        return False

    if run([container_rt, "tag", base_image, full_image], check=False, capture=True).returncode != 0:
        err(f"Could not tag {base_image} as {full_image}")
        return False

    if reg_user and reg_token:
        run([container_rt, "login", docker_server,
             "--username", reg_user, "--password-stdin"],
            input=reg_token, check=False, capture=True)

    info(f"Pushing {full_image}...")
    push_ok = run([container_rt, "push", full_image], check=False, capture=True).returncode == 0

    # Clean up local test tag
    run([container_rt, "rmi", full_image], check=False, capture=True)

    if not push_ok:
        return False

    # Pull-back verification
    info("Verifying pull-back...")
    pull_ok = run([container_rt, "pull", full_image], check=False, capture=True).returncode == 0
    run([container_rt, "rmi", full_image], check=False, capture=True)

    if pull_ok:
        ok(f"Registry credentials verified (push + pull) → {full_image}")
    else:
        ok(f"Push succeeded → {full_image}")
        warn("Pull-back failed — may be a propagation delay. Credentials likely OK.")
    return True


def step_test_push(cfg: SetupConfig, container_rt: str, test_push_tag: str,
                   auto_push: bool) -> None:
    """Step 2: Optional registry credential test with retry on failure."""
    step(2, 3, "Registry Credential Test")
    if not cfg.registry:
        ok("Registry test (skipped — no registry configured)"); return
    if not container_rt:
        info("No podman/docker found — skipping test push.")
        info("Credentials will be verified during in-cluster EPP build.")
        return

    # Prompt to test (--test-push auto-accepts)
    if not auto_push:
        full_image = f"{cfg.registry}/{cfg.repo_name}:{test_push_tag}"
        answer = prompt("test_push",
            f"Push test image to {full_image}? [y]es / [s]kip", default="s")
        if answer.strip().lower() not in ("y", "yes"):
            info("Skipped registry credential test"); return

    full_image = f"{cfg.registry}/{cfg.repo_name}:{test_push_tag}"
    docker_server = cfg.registry.split("/")[0]
    interactive = sys.stdout.isatty()

    while True:
        success = _do_test_push(container_rt, full_image, docker_server,
                                cfg.registry_user, cfg.registry_token)
        if success:
            return

        err("Registry push failed")
        if not interactive:
            sys.exit(1)

        answer = prompt("retry", "[r]etry / [s]kip / [q]uit", default="q")
        choice = answer.strip().lower()
        if choice in ("r", "retry"):
            continue
        elif choice in ("s", "skip"):
            warn("Registry credentials unverified — will be tested during in-cluster EPP build")
            return
        else:
            sys.exit(1)

# ── Step 3: Config Output ────────────────────────────────────────────

def step_config_output(cfg: SetupConfig, run_dir: Path) -> None:
    """Step 3: Write setup_config.json and run_metadata.json.

    Both files are read-modify-write. setup_config.json now preserves keys
    this script doesn't own (e.g. future cluster_id pointer or any other
    writer's field). run_metadata.json preserves deploy-owned keys
    (source_hashes, epp_image, stages.deploy.last_completed_step). See #365.
    """
    step(3, 3, "Config")

    workspace = EXPERIMENT_ROOT / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    commit = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"],
        capture_output=True, text=True, check=False,
    ).stdout.strip() or "unknown"

    # setup_config.json — operator-side fields only. Cluster-side fields
    # (namespaces, is_openshift, storage_class, secret_names, workspaces)
    # live in workspace/clusters/<id>/cluster_config.json, written by
    # `cluster.py provision`.
    setup_config_path = workspace / "setup_config.json"
    existing_setup = {}
    if setup_config_path.exists():
        try:
            existing_setup = json.loads(setup_config_path.read_text())
        except json.JSONDecodeError:
            existing_setup = {}
    existing_setup.update({
        "registry": cfg.registry,
        "repo_name": cfg.repo_name,
        "sim2real_root": str(REPO_ROOT),
        "orchestrator_image": cfg.orchestrator_image,
        "current_run": cfg.run_name,
    })
    setup_config_path.write_text(json.dumps(existing_setup, indent=2))
    ok(f"Setup config → {setup_config_path}")

    # run_metadata.json — read-modify-write so deploy-owned keys
    # (source_hashes, epp_image, stages.deploy.last_completed_step) survive
    # setup re-runs. See issue #365.
    meta_path = run_dir / "run_metadata.json"
    if meta_path.exists():
        try:
            existing = json.loads(meta_path.read_text())
        except json.JSONDecodeError:
            existing = {}
    else:
        existing = {}

    existing.update({
        "version": 1,
        "registry": cfg.registry,
        "repo_name": cfg.repo_name,
        "created_at": now_iso,
        "pipeline_commit": commit,
    })
    if cfg.registry:
        existing["component_image"] = f"{cfg.registry}/{cfg.repo_name}:{cfg.run_name}"

    stages = existing.setdefault("stages", {})
    stages["setup"] = {
        "status": "completed",
        "completed_at": now_iso,
        "summary": "Workspace config written",
    }
    stages.setdefault("prepare", {"status": "pending"})
    stages.setdefault("deploy",  {"status": "pending"})
    stages.setdefault("results", {"status": "pending"})

    meta_path.write_text(json.dumps(existing, indent=2))
    ok(f"Run metadata → {meta_path}")


# ── main ─────────────────────────────────────────────────────────────

def main() -> int:
    args = build_parser().parse_args()

    global EXPERIMENT_ROOT
    EXPERIMENT_ROOT = Path(args.experiment_root).resolve() if getattr(args, "experiment_root", None) else Path.cwd()

    cfg, run_dir, container_rt = collect_config(args)
    step_test_push(cfg, container_rt, args.test_push_tag, args.test_push)
    step_config_output(cfg, run_dir)

    # Completion
    print()
    print(_c("32", "━━━ Setup complete ━━━"))
    print()
    cfg_path = EXPERIMENT_ROOT / "workspace" / "setup_config.json"
    print(f"Setup config:  {cfg_path}")
    print(f"Run name:      {cfg.run_name}")
    print(f"Run directory: {run_dir}")
    print()
    print("Next steps:")
    print("  1. Provision cluster: python pipeline/cluster.py provision <cluster_id> --namespaces NS1[,NS2,...]")
    print("  2. Edit <experiment-root>/transfer.yaml (algorithm source, workloads, context)")
    print("  3. Register a translation: python pipeline/sim2real.py translation register \\")
    print("                                 --algorithm NAME --image REF --config PATH")
    print("  4. Assemble a run:         python pipeline/sim2real.py assemble \\")
    print("                                 --translation HASH --cluster CLUSTER_ID --run RUN_NAME")
    return 0

if __name__ == "__main__":
    sys.exit(main())
