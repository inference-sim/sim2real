#!/usr/bin/env python3
"""sim2real cluster setup — one-time, idempotent environment bootstrap."""

import argparse
import getpass
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Repo layout ──────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parent.parent
TEKTONC_DIR = REPO_ROOT / "tektonc-data-collection"

# ── Color helpers ────────────────────────────────────────────────────
_tty = sys.stdout.isatty()
def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _tty else text

def info(msg: str)  -> None: print(_c("34", "[INFO]  ") + msg)
def ok(msg: str)    -> None: print(_c("32", "[OK]    ") + msg)
def warn(msg: str)  -> None: print(_c("33", "[WARN]  ") + msg)
def err(msg: str)   -> None: print(_c("31", "[ERROR] ") + msg, file=sys.stderr)
def step(n, title: str) -> None:
    print("\n" + _c("36", f"━━━ Step {n}: {title} ━━━"))

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="setup.py",
        description="One-time cluster and environment setup for the sim2real pipeline.\n"
                    "Idempotent — safe to re-run.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Environment variables (alternatives to --flags):
  NAMESPACE, HF_TOKEN, QUAY_ROBOT_USERNAME, QUAY_ROBOT_TOKEN, GITHUB_TOKEN

Examples:
  python scripts/setup.py                         # fully interactive
  python scripts/setup.py --namespace my-ns \\
    --hf-token hf_xxx --registry quay.io/me       # pre-fill common values
  python scripts/setup.py --no-cluster            # local config only, no kubectl
""",
    )
    p.add_argument("--namespace",      metavar="NS",    help="Kubernetes namespace")
    p.add_argument("--hf-token",       metavar="TOKEN", help="HuggingFace API token")
    p.add_argument("--registry",       metavar="REG",   help="Container registry host (e.g. quay.io/username or ghcr.io/username)")
    p.add_argument("--repo-name",      metavar="NAME",  default="llm-d-inference-scheduler",
                                                        help="Registry repository name [%(default)s]")
    p.add_argument("--registry-user",  metavar="USER",  help="Registry robot username")
    p.add_argument("--registry-token", metavar="TOKEN", help="Registry robot token")
    p.add_argument("--storage-class",  metavar="SC",    help="PVC storageClassName (auto-detected for OpenShift)")
    p.add_argument("--run",            metavar="NAME",  help="Run name [sim2real-YYYY-MM-DD]")
    p.add_argument("--no-cluster",     action="store_true",
                                       help="Skip all kubectl/tkn steps (config collection + JSON outputs only)")
    p.add_argument("--test-push-tag",  metavar="TAG",   default="_test-image-push",
                                       help="Image tag for optional registry credential test push [%(default)s]")
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

    At the prompt the user may also type an env var name (e.g. QUAY_ROBOT_TOKEN)
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
    # If input looks like an env var name, try resolving it
    if re.match(r'^[A-Z][A-Z0-9_]{1,}$', raw):
        resolved = os.environ.get(raw, "")
        if resolved:
            ok(f"Read from env var {raw} ({len(resolved)} characters)")
            return resolved
    if raw:
        ok(f"Token received ({len(raw)} characters)")
    return raw

INSTALL_HINTS = {
    "kubectl": "https://kubernetes.io/docs/tasks/tools/",
    "tkn":     "https://tekton.dev/docs/cli/",
    "python3": "https://www.python.org/downloads/",
    "gh":      "https://cli.github.com/",
    "git":     "https://git-scm.com/downloads",
    "envsubst": "brew install gettext  OR  apt install gettext",
}

def check_prerequisites(no_cluster: bool) -> str:
    """Check required tools. Returns detected container runtime. Exits on missing tools."""
    step(1, "Checking prerequisites")
    missing = []

    for cmd in ["python3", "gh", "git", "envsubst"]:
        if not which(cmd):
            hint = INSTALL_HINTS.get(cmd, "")
            missing.append(f"{cmd}  →  {hint}" if hint else cmd)

    if not no_cluster:
        for cmd in ["kubectl", "tkn"]:
            if not which(cmd):
                hint = INSTALL_HINTS.get(cmd, "")
                missing.append(f"{cmd}  →  {hint}" if hint else cmd)

    container_rt = next((rt for rt in ["podman", "docker"] if which(rt)), "")
    if not container_rt and not no_cluster:
        missing.append("podman or docker  →  https://podman.io/getting-started/installation")

    if missing:
        err("Missing prerequisites:")
        for m in missing:
            print(f"  • {m}")
        sys.exit(1)

    suffix = f" (container runtime: {container_rt})" if container_rt else ""
    ok(f"All prerequisites found{suffix}")
    return container_rt

def init_submodules() -> None:
    step(2, "Initializing git submodules")
    run(["git", "-C", str(REPO_ROOT), "submodule", "update", "--init",
         "inference-sim", "llm-d-inference-scheduler", "tektonc-data-collection"],
        check=False)
    ok("Submodules initialized")

def setup_venv() -> None:
    step(3, "Setting up Python environment")
    venv_dir = REPO_ROOT / ".venv"
    if not venv_dir.exists():
        run([sys.executable, "-m", "venv", str(venv_dir)])
        info("Created .venv")
    pip = venv_dir / "bin" / "pip"
    req = REPO_ROOT / "requirements.txt"
    if req.exists():
        run([str(pip), "install", "-q", "-r", str(req)], check=False)
    else:
        run([str(pip), "install", "-q", "PyYAML", "jinja2"], check=False)
    ok("Python venv ready")

def detect_openshift() -> bool:
    if not which("oc"):
        return False
    return run(["oc", "whoami"], check=False, capture=True).returncode == 0

def check_cluster_reachable() -> None:
    """Verify the cluster is reachable before running any kubectl commands. Exits with a
    friendly message on common connectivity failures."""
    result = run(["kubectl", "cluster-info"], check=False, capture=True)
    if result.returncode == 0:
        return

    combined = (result.stdout + result.stderr).lower()

    # Classify the failure and give an actionable suggestion
    if "no such host" in combined or "name resolution" in combined or "lookup" in combined:
        reason = "DNS resolution failed — the cluster hostname is not reachable."
        hint   = "Check your VPN, network connection, or kubeconfig server address."
    elif "connection refused" in combined:
        reason = "Connection refused — nothing is listening on the cluster API endpoint."
        hint   = "Ensure the cluster is running and the API server port is accessible."
    elif "i/o timeout" in combined or "timeout" in combined:
        reason = "Connection timed out — the cluster API server did not respond."
        hint   = "Check your VPN or firewall; the cluster may be stopped or unreachable."
    elif "unauthorized" in combined or "forbidden" in combined:
        reason = "Authentication failed — your credentials or kubeconfig may be expired."
        hint   = "Try: kubectl config view  or re-run your cluster login command."
    elif "no configuration" in combined or "no such file" in combined:
        reason = "No kubeconfig found — kubectl has no cluster to connect to."
        hint   = "Set KUBECONFIG or run your cluster login command first."
    else:
        reason = "kubectl cluster-info failed."
        hint   = result.stderr.strip() or result.stdout.strip()

    err(f"Cluster unreachable: {reason}")
    print(f"  → {hint}")
    print()
    print("  If you meant to run without a cluster, re-run with --no-cluster.")
    sys.exit(1)

def configure_namespace(args: argparse.Namespace, is_openshift: bool) -> str:
    step(4, "Configuring namespace")
    namespace = args.namespace or prompt("namespace", "Enter Kubernetes namespace", env_var="NAMESPACE")
    if not namespace:
        err("NAMESPACE is required"); sys.exit(1)

    if not args.no_cluster:
        check_cluster_reachable()
        exists = run(["kubectl", "get", "ns", namespace], check=False, capture=True).returncode == 0
        if exists:
            ok(f"Namespace {namespace} already exists")
        else:
            if is_openshift:
                proc = run(["oc", "new-project", namespace], check=False, capture=True)
                if proc.returncode != 0 and "AlreadyExists" not in (proc.stderr or ""):
                    proc.check_returncode()
            else:
                run(["kubectl", "create", "ns", namespace])
            ok(f"Namespace {namespace} ready")

        result = run(["kubectl", "config", "set-context", "--current",
                      f"--namespace={namespace}"], check=False, capture=True)
        if result.returncode == 0:
            ok(f"kubectl context set to namespace {namespace}")
        else:
            warn("Could not set kubectl context namespace")
    return namespace

def configure_run_name(args: argparse.Namespace) -> tuple[str, Path]:
    step("4b", "Configuring run name")
    default = f"sim2real-{datetime.now().strftime('%Y-%m-%d')}"
    run_name = args.run or prompt("run_name", "Enter a name for this run", default=default)

    while True:
        run_dir = REPO_ROOT / "workspace" / "runs" / run_name
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
    ok(f"Run directory: {run_dir}")
    return run_name, run_dir

def detect_storage_class(args: argparse.Namespace, is_openshift: bool) -> str:
    if args.storage_class:
        info(f"Using storageClassName: {args.storage_class}")
        return args.storage_class
    if is_openshift:
        sc = "ibm-spectrum-scale-fileset"
        info(f"OpenShift detected — using storageClass: {sc}")
        return sc
    if not args.no_cluster:
        result = run(["kubectl", "get", "storageclass", "--no-headers"],
                     check=False, capture=True)
        if result.returncode == 0 and result.stdout.strip():
            print()
            info("Available storage classes:")
            for line in result.stdout.strip().splitlines():
                parts = line.split()
                marker = "  [default]" if "(default)" in line else ""
                print(f"  • {parts[0]}{marker}")
    sc = prompt("storage_class",
                "Enter PVC storageClassName (leave blank for cluster default)", default="")
    if sc:
        info(f"Using storageClassName: {sc}")
    else:
        info("Using cluster default storageClassName")
    return sc

def secret_exists(name: str, namespace: str) -> bool:
    """Return True if the named Kubernetes secret exists in the namespace."""
    return run(["kubectl", "get", "secret", name, f"-n={namespace}"],
               check=False, capture=True).returncode == 0

def create_hf_secret(args: argparse.Namespace, namespace: str) -> str:
    step(5, "Creating HuggingFace secret")

    if not args.no_cluster and not args.hf_token and secret_exists("hf-secret", namespace):
        ok("hf-secret already exists in namespace")
        answer = prompt("override_hf", "Override existing hf-secret? [y/N]", default="N")
        if answer.strip().lower() not in ("y", "yes"):
            return "<existing>"

    hf_token = args.hf_token or prompt_secret("Enter HuggingFace token", env_var="HF_TOKEN")
    if not hf_token:
        err("HF_TOKEN is required"); sys.exit(1)
    if not args.no_cluster:
        yaml_out = run(
            ["kubectl", "create", "secret", "generic", "hf-secret",
             f"--namespace={namespace}",
             f"--from-literal=HF_TOKEN={hf_token}",
             "--dry-run=client", "-o", "yaml"],
            capture=True,
        ).stdout
        run(["kubectl", "apply", "-f", "-"], input=yaml_out)
        ok("hf-secret created/updated")
    else:
        ok("hf-secret (skipped — --no-cluster)")
    return hf_token

def test_registry_push(registry: str, repo_name: str, container_rt: str,
                       docker_server: str, reg_user: str, reg_token: str,
                       test_tag: str) -> None:
    """Push a tiny test image to verify registry credentials are correct."""
    print()
    answer = prompt("test_push", "Push a test image to verify registry credentials? [y/N]",
                    default="N")
    if answer.strip().lower() not in ("y", "yes"):
        return

    full_image = f"{registry}/{repo_name}:{test_tag}"
    base_image = "busybox:latest"

    info(f"Pulling {base_image} (used as test payload)...")
    result = run([container_rt, "pull", base_image], check=False, capture=True)
    if result.returncode != 0:
        warn(f"Could not pull {base_image} — skipping test push.")
        return

    info(f"Tagging as {full_image}...")
    run([container_rt, "tag", base_image, full_image])

    # Log in with robot credentials if available (ensure fresh auth before push)
    if reg_user and reg_token:
        login = run(
            [container_rt, "login", docker_server,
             "--username", reg_user, "--password-stdin"],
            input=reg_token, check=False, capture=False,
        )
        if login.returncode != 0:
            warn(f"Login step returned non-zero before test push")

    info(f"Pushing {full_image}...")
    push = run([container_rt, "push", full_image], check=False)

    # Clean up local test tag (not the base busybox image)
    run([container_rt, "rmi", full_image], check=False, capture=True)

    if push.returncode == 0:
        ok(f"Test push succeeded → {full_image}")
        info("Registry credentials confirmed.")
    else:
        warn("Test push failed — credentials will be verified when EPP build runs in-cluster.")
        warn("If the in-cluster build also fails, re-run setup with correct --registry-user / --registry-token.")


def create_registry_secret(args: argparse.Namespace, namespace: str,
                            container_rt: str) -> tuple[str, str]:
    step(10, "Configuring container registry for treatment EPP image")
    print()
    info("The pipeline compiles your scorer plugin into a treatment EPP image using")
    info("BuildKit running inside the cluster (faster than a local build, and avoids")
    info("cross-architecture issues on arm64 laptops targeting amd64 clusters).")
    info("The built image is pushed to your registry and pulled back by the cluster")
    info("for benchmarking. You need push access to a registry the cluster can also pull from.")
    print()
    info("If using Quay.io: create a robot account with Write access and set")
    info("  QUAY_ROBOT_USERNAME / QUAY_ROBOT_TOKEN (or pass --registry-user/--registry-token).")
    info("If using ghcr.io: set GITHUB_TOKEN (a PAT with write:packages scope).")
    print()

    if not args.no_cluster and not args.registry and secret_exists("registry-secret", namespace):
        ok("registry-secret already exists in namespace")
        answer = prompt("override_reg", "Override existing registry-secret? [y/N]", default="N")
        if answer.strip().lower() not in ("y", "yes"):
            # Still need to return registry/repo_name for downstream steps; read from args/env
            registry = args.registry or os.environ.get("REGISTRY", "")
            if not registry:
                warn("Existing registry-secret kept — update config/env_defaults.yaml manually if registry changed.")
            return registry, args.repo_name

    registry = args.registry or prompt(
        "registry", "Container registry host (e.g. quay.io/username or ghcr.io/username, or blank to skip)", default="")
    repo_name = args.repo_name

    if not registry:
        warn("No registry — skipping. Update config/env_defaults.yaml manually.")
        return "", repo_name

    repo_name = prompt("repo_name", "Repository name", default=repo_name)
    docker_server = registry.split("/")[0]

    # Credential resolution: explicit args > QUAY env vars > GITHUB_TOKEN (for ghcr.io)
    reg_user  = args.registry_user  or os.environ.get("QUAY_ROBOT_USERNAME", "")
    reg_token = args.registry_token or os.environ.get("QUAY_ROBOT_TOKEN", "")
    if not reg_user and not reg_token and docker_server == "ghcr.io":
        github_token = os.environ.get("GITHUB_TOKEN", "")
        if github_token:
            # ghcr.io accepts any non-empty string as username with a PAT
            reg_user  = registry.split("/")[1] if "/" in registry else "github"
            reg_token = github_token
            info("Using GITHUB_TOKEN for ghcr.io authentication.")
    if not reg_user and not args.registry:
        reg_user = prompt("registry_user",
            "Registry username (or press Enter to use container login)", default="")
    if reg_user and not reg_token and not args.registry:
        reg_token = prompt_secret("Registry token", env_var="QUAY_ROBOT_TOKEN")

    if not args.no_cluster:
        if reg_user and reg_token:
            yaml_out = run(
                ["kubectl", "create", "secret", "docker-registry", "registry-secret",
                 f"--namespace={namespace}",
                 f"--docker-server={docker_server}",
                 f"--docker-username={reg_user}",
                 f"--docker-password={reg_token}",
                 "--dry-run=client", "-o", "yaml"],
                capture=True,
            ).stdout
            run(["kubectl", "apply", "-f", "-"], input=yaml_out)
            ok("registry-secret created/updated")
        else:
            info(f"Falling back to container runtime login ({container_rt})...")
            result = run([container_rt, "login", docker_server], check=False)
            if result.returncode != 0:
                err("Registry login failed. Provide --registry-user and --registry-token.")
                sys.exit(1)
            config_candidates = [
                Path.home() / ".docker" / "config.json",
                Path.home() / ".config" / "containers" / "auth.json",
                Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "containers" / "auth.json",
            ]
            config_path = next((p for p in config_candidates if p.exists()), None)
            if not config_path:
                err("Could not locate docker config after login. "
                    "Provide --registry-user and --registry-token.")
                sys.exit(1)
            yaml_out = run(
                ["kubectl", "create", "secret", "docker-registry", "registry-secret",
                 f"--namespace={namespace}",
                 f"--from-file=.dockerconfigjson={config_path}",
                 "--dry-run=client", "-o", "yaml"],
                capture=True,
            ).stdout
            run(["kubectl", "apply", "-f", "-"], input=yaml_out)
            ok("registry-secret created/updated from container login")
    else:
        ok("registry-secret (skipped — --no-cluster)")

    if registry and container_rt:
        test_registry_push(registry, repo_name, container_rt, docker_server,
                           reg_user, reg_token, args.test_push_tag)

    return registry, repo_name

def apply_rbac(namespace: str, is_openshift: bool, no_cluster: bool) -> None:
    step(6, "Applying RBAC roles")
    if no_cluster:
        ok("RBAC (skipped — --no-cluster)"); return
    roles_yaml = TEKTONC_DIR / "tekton" / "roles.yaml"
    if not roles_yaml.exists():
        err(f"roles.yaml not found at {roles_yaml} — did submodule init fail?"); sys.exit(1)
    env = {**os.environ, "NAMESPACE": namespace}
    subst = subprocess.run(
        ["envsubst", "$NAMESPACE"],
        input=roles_yaml.read_text(), capture_output=True, text=True, env=env, check=True,
    )
    run(["kubectl", "apply", "-f", "-"], input=subst.stdout)
    if is_openshift:
        warn("OpenShift: adding SCC policies")
        for policy_args in [
            ["add-scc-to-user",          "anyuid",       "-z", "default",        "-n", namespace],
            ["add-scc-to-user",          "anyuid",       "-z", "helm-installer", "-n", namespace],
            ["add-scc-to-user",          "privileged",   "-z", "helm-installer", "-n", namespace],
            ["add-cluster-role-to-user", "cluster-admin", "-z", "helm-installer"],
        ]:
            run(["oc", "adm", "policy"] + policy_args, check=False)
    ok("RBAC roles applied")

def create_pvcs(namespace: str, storage_class: str, no_cluster: bool) -> None:
    step(7, "Creating PVCs")
    pvcs = [("model-pvc", "300Gi"), ("data-pvc", "20Gi"), ("source-pvc", "20Gi")]
    sc_line = f"  storageClassName: {storage_class}" if storage_class else ""

    for name, size in pvcs:
        if no_cluster:
            ok(f"PVC {name} (skipped — --no-cluster)"); continue
        if run(["kubectl", "get", "pvc", name, f"-n={namespace}"],
               check=False, capture=True).returncode == 0:
            ok(f"PVC {name} already exists"); continue
        manifest = (
            f"apiVersion: v1\nkind: PersistentVolumeClaim\n"
            f"metadata:\n  name: {name}\n  namespace: {namespace}\n"
            f"spec:\n{sc_line}\n  accessModes:\n    - ReadWriteMany\n"
            f"  resources:\n    requests:\n      storage: {size}\n"
        )
        run(["kubectl", "apply", "-f", "-"], input=manifest)
        ok(f"Created PVC {name} ({size})")

    if not no_cluster:
        info("Waiting for PVCs to bind (timeout 120s)...")
        for name, _ in pvcs:
            result = run(
                ["kubectl", "wait", "--for=jsonpath={.status.phase}=Bound",
                 f"pvc/{name}", f"-n={namespace}", "--timeout=120s"],
                check=False, capture=True,
            )
            if result.returncode == 0:
                ok(f"PVC {name} is Bound")
            else:
                warn(f"PVC {name} not yet Bound — check storageClass: {storage_class or '(default)'}")

def verify_tekton(no_cluster: bool) -> None:
    step(8, "Verifying Tekton operator")
    if no_cluster: return
    result = run(["kubectl", "get", "pods", "-n", "tekton-pipelines", "--no-headers"],
                 check=False, capture=True)
    if result.returncode == 0 and "Running" in result.stdout:
        ok("Tekton operator is running")
    else:
        warn("Tekton operator not detected — install: https://tekton.dev/docs/installation/pipelines/")

def deploy_tekton_tasks(namespace: str, no_cluster: bool) -> None:
    step(9, "Deploying Tekton steps and tasks")
    if no_cluster:
        ok("Tekton deployment (skipped — --no-cluster)"); return
    for subdir in ["steps", "tasks"]:
        for yaml_file in sorted((TEKTONC_DIR / "tekton" / subdir).glob("*.yaml")):
            run(["kubectl", "apply", "-f", str(yaml_file), f"-n={namespace}"])
    ok("Tekton steps and tasks deployed")

def update_env_defaults(registry: str, repo_name: str, run_name: str) -> None:
    step(12, "Updating config/env_defaults.yaml with registry values")
    cfg_path = REPO_ROOT / "config" / "env_defaults.yaml"
    if not cfg_path.exists():
        warn("config/env_defaults.yaml not found — skipping"); return
    if not registry:
        warn("No registry provided — config/env_defaults.yaml not updated. "
             "Update stack.gaie.epp_image.build.hub manually."); return

    try:
        import yaml
    except ImportError:
        warn("PyYAML not available — skipping config update. Run: pip install PyYAML"); return

    try:
        with cfg_path.open() as f:
            cfg = yaml.safe_load(f)

        build = (cfg.setdefault("stack", {})
                   .setdefault("gaie", {})
                   .setdefault("epp_image", {})
                   .setdefault("build", {}))
        build["hub"]  = registry
        build["name"] = repo_name
        build["tag"]  = run_name

        with cfg_path.open("w") as f:
            yaml.dump(cfg, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

        ok("config/env_defaults.yaml updated with registry info")
        warn("Note: PyYAML reformatted the file — inline comments were removed. "
             "Review the diff before committing.")
    except PermissionError:
        warn("config/env_defaults.yaml is read-only — skipping update. "
             "Manually set stack.gaie.epp_image.build.hub to: " + registry)

def write_outputs(namespace: str, registry: str, repo_name: str,
                  storage_class: str, is_openshift: bool, container_rt: str,
                  run_name: str, run_dir: Path) -> None:
    workspace = REPO_ROOT / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    commit = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"],
        capture_output=True, text=True, check=False,
    ).stdout.strip() or "unknown"

    setup_config = {
        "namespace": namespace, "registry": registry, "repo_name": repo_name,
        "storage_class": storage_class,
        "is_openshift": is_openshift, "tektonc_dir": str(TEKTONC_DIR),
        "sim2real_root": str(REPO_ROOT), "container_runtime": container_rt,
        "setup_timestamp": now_iso, "current_run": run_name,
    }
    setup_path = workspace / "setup_config.json"
    setup_path.write_text(json.dumps(setup_config, indent=2))
    ok(f"Setup config saved to {setup_path}")

    metadata = {
        **{k: v for k, v in setup_config.items()
           if k not in {"setup_timestamp", "tektonc_dir", "sim2real_root"}},
        "created_at": now_iso,
        "pipeline_commit": commit,
        "stages": {
            "setup":   {"status": "completed", "completed_at": now_iso,
                        "summary": f"Namespace {namespace} configured, PVCs created, Tekton tasks deployed"},
            "prepare": {"status": "pending"},
            "deploy":  {"status": "pending"},
            "results": {"status": "pending"},
        },
    }
    meta_path = run_dir / "run_metadata.json"
    meta_path.write_text(json.dumps(metadata, indent=2))
    ok(f"Run metadata saved to {meta_path}")

def main() -> int:
    args = build_parser().parse_args()

    container_rt  = check_prerequisites(args.no_cluster)
    init_submodules()
    setup_venv()

    is_openshift  = detect_openshift()
    namespace     = configure_namespace(args, is_openshift)
    run_name, run_dir = configure_run_name(args)
    storage_class = detect_storage_class(args, is_openshift)

    create_hf_secret(args, namespace)
    apply_rbac(namespace, is_openshift, args.no_cluster)
    create_pvcs(namespace, storage_class, args.no_cluster)
    verify_tekton(args.no_cluster)
    deploy_tekton_tasks(namespace, args.no_cluster)

    registry, repo_name = create_registry_secret(args, namespace, container_rt)

    update_env_defaults(registry, repo_name, run_name)
    write_outputs(namespace, registry, repo_name, storage_class, is_openshift, container_rt,
                  run_name, run_dir)

    print()
    print(_c("32", "━━━ Setup complete ━━━"))
    print()
    cfg_path = REPO_ROOT / "workspace" / "setup_config.json"
    print(f"Setup config:  {cfg_path}")
    print(f"Run name:      {run_name}")
    print(f"Run directory: {run_dir}")
    print()
    print("Next steps:")
    print("  1. Review config/env_defaults.yaml (gateway, vLLM image, fast_iteration)")
    print("  2. Run: python scripts/prepare.py  OR  /sim2real-prepare in Claude")
    return 0

if __name__ == "__main__":
    sys.exit(main())
