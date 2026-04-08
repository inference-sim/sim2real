#!/usr/bin/env bash
set -euo pipefail

# ── Colors ──────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
info()  { echo -e "${BLUE}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()   { echo -e "${RED}[ERROR]${NC} $*" >&2; }
step()  { echo -e "\n${BLUE}━━━ Step $1: $2 ━━━${NC}"; }

# ── Parse args ──────────────────────────────────────────────────────
REDEPLOY_TASKS=false
while [[ $# -gt 0 ]]; do
  case $1 in
    --namespace)      NAMESPACE="$2"; shift 2 ;;
    --hf-token)       HF_TOKEN="$2"; shift 2 ;;
    --registry)       REGISTRY="$2"; shift 2 ;;
    --run)            RUN_NAME="$2"; shift 2 ;;
    --redeploy-tasks) REDEPLOY_TASKS=true; shift ;;
    *) err "Unknown arg: $1"; exit 1 ;;
  esac
done

# ── Find repo root ─────────────────────────────────────────────────
# Script lives at sim2real/.claude/skills/sim2real-setup/scripts/setup.sh
SIM2REAL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
TEKTONC_DIR="${SIM2REAL_ROOT}/tektonc-data-collection"

# ── --redeploy-tasks shortcut ──────────────────────────────────────
if [ "${REDEPLOY_TASKS}" = true ]; then
  if [ -z "${NAMESPACE:-}" ]; then
    err "--namespace is required with --redeploy-tasks"; exit 1
  fi
  step 9 "Redeploying Tekton steps and tasks (--redeploy-tasks)"
  cd "${TEKTONC_DIR}"
  for f in tekton/steps/*.yaml; do
    [ -f "$f" ] && kubectl apply -f "$f" -n "${NAMESPACE}"
  done
  for f in tekton/tasks/*.yaml; do
    [ -f "$f" ] && kubectl apply -f "$f" -n "${NAMESPACE}"
  done
  ok "Tekton steps and tasks redeployed"
  exit 0
fi

# ── Step 1: Prerequisites ──────────────────────────────────────────
step 1 "Checking prerequisites"
MISSING=()
for cmd in kubectl tkn python3 gh; do
  command -v "$cmd" &>/dev/null || MISSING+=("$cmd")
done
CONTAINER_RT=""
if command -v podman &>/dev/null; then
  CONTAINER_RT="podman"
elif command -v docker &>/dev/null; then
  CONTAINER_RT="docker"
else
  MISSING+=("podman or docker")
fi
if [ ${#MISSING[@]} -gt 0 ]; then
  err "Missing prerequisites: ${MISSING[*]}"
  exit 1
fi
ok "All prerequisites found (container runtime: ${CONTAINER_RT})"

# ── Step 2: Submodules ─────────────────────────────────────────────
step 2 "Initializing git submodules"
cd "${SIM2REAL_ROOT}"
git submodule update --init inference-sim llm-d-inference-scheduler tektonc-data-collection 2>/dev/null || true
ok "Submodules initialized"

# ── Step 3: Python venv ────────────────────────────────────────────
step 3 "Setting up Python environment"
cd "${SIM2REAL_ROOT}"
if [ ! -d .venv ]; then
  python3 -m venv .venv
  info "Created .venv"
fi
source .venv/bin/activate
pip install -q -r requirements.txt 2>/dev/null || pip install -q PyYAML jinja2
ok "Python venv ready"

# ── Step 4: Namespace ──────────────────────────────────────────────
step 4 "Configuring namespace"
if [ -z "${NAMESPACE:-}" ]; then
  read -rp "Enter Kubernetes namespace: " NAMESPACE
fi
if [ -z "${NAMESPACE}" ]; then
  err "NAMESPACE is required"; exit 1
fi
export NAMESPACE

IS_OPENSHIFT=false
if command -v oc &>/dev/null && oc whoami &>/dev/null 2>&1; then
  IS_OPENSHIFT=true
  info "OpenShift detected"
fi

# Create namespace if it doesn't exist (idempotent)
# On OpenShift, 'kubectl get ns' may fail due to RBAC even if the ns exists,
# so we try to create and treat AlreadyExists as success.
if kubectl get ns "${NAMESPACE}" &>/dev/null; then
  ok "Namespace ${NAMESPACE} already exists"
else
  if [ "$IS_OPENSHIFT" = true ]; then
    oc new-project "${NAMESPACE}" 2>&1 | grep -v 'AlreadyExists' || true
  else
    kubectl create ns "${NAMESPACE}" 2>&1 | grep -v 'AlreadyExists' || true
  fi
  ok "Namespace ${NAMESPACE} ready"
fi

# Set kubectl context to use this namespace by default
kubectl config set-context --current --namespace="${NAMESPACE}" 2>/dev/null \
  && ok "kubectl context set to namespace ${NAMESPACE}" \
  || warn "Could not set kubectl context namespace"

# ── Step 4b: Run name ──────────────────────────────────────────────
step "4b" "Configuring run name"
if [ -z "${RUN_NAME:-}" ]; then
  DEFAULT_RUN="sim2real-$(date +%Y-%m-%d)"
  read -rp "Enter a name for this run [${DEFAULT_RUN}]: " RUN_NAME
  RUN_NAME="${RUN_NAME:-${DEFAULT_RUN}}"
fi
if [ -z "${RUN_NAME}" ]; then
  RUN_NAME="sim2real-$(date +%Y-%m-%d)"
fi
RUN_DIR="${SIM2REAL_ROOT}/workspace/runs/${RUN_NAME}"
mkdir -p "${RUN_DIR}"
ok "Run directory: ${RUN_DIR}"

# ── Step 5: HuggingFace secret ─────────────────────────────────────
step 5 "Creating HuggingFace secret"
if [ -z "${HF_TOKEN:-}" ]; then
  read -rsp "Enter HuggingFace token (HF_TOKEN): " HF_TOKEN
  echo
fi
if [ -z "${HF_TOKEN}" ]; then
  err "HF_TOKEN is required"; exit 1
fi
kubectl create secret generic hf-secret \
  --namespace "${NAMESPACE}" \
  --from-literal="HF_TOKEN=${HF_TOKEN}" \
  --dry-run=client -o yaml | kubectl apply -f -
ok "hf-secret created/updated"

# ── Step 6: RBAC roles ─────────────────────────────────────────────
step 6 "Applying RBAC roles"
cd "${TEKTONC_DIR}"
envsubst '$NAMESPACE' < tekton/roles.yaml | kubectl apply -f -
if [ "$IS_OPENSHIFT" = true ]; then
  warn "OpenShift: adding SCC policies"
  oc adm policy add-scc-to-user anyuid -z default -n "${NAMESPACE}" 2>/dev/null || true
  oc adm policy add-scc-to-user anyuid -z helm-installer -n "${NAMESPACE}" 2>/dev/null || true
  oc adm policy add-scc-to-user privileged -z helm-installer -n "${NAMESPACE}" 2>/dev/null || true
  oc adm policy add-cluster-role-to-user cluster-admin -z helm-installer -n "${NAMESPACE}" 2>/dev/null || true
fi
ok "RBAC roles applied"

# ── Step 7: PVCs ───────────────────────────────────────────────────
step 7 "Creating PVCs"

STORAGE_CLASS="ibm-spectrum-scale-fileset"
info "Using storageClassName=${STORAGE_CLASS}"
SC_SNIPPET="  storageClassName: ${STORAGE_CLASS}"

create_pvc() {
  local name=$1 size=$2
  if kubectl get pvc "${name}" -n "${NAMESPACE}" &>/dev/null; then
    ok "PVC ${name} already exists"
    return
  fi
  cat <<YAML | kubectl apply -f -
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: ${name}
  namespace: ${NAMESPACE}
spec:
${SC_SNIPPET}
  accessModes:
    - ReadWriteMany
  resources:
    requests:
      storage: ${size}
YAML
  ok "Created PVC ${name} (${size})"
}

create_pvc model-pvc 300Gi
create_pvc data-pvc 20Gi
create_pvc source-pvc 20Gi

# Wait for PVCs to bind
info "Waiting for PVCs to bind..."
for pvc_name in model-pvc data-pvc source-pvc; do
  kubectl wait --for=jsonpath='{.status.phase}'=Bound \
    pvc/"${pvc_name}" -n "${NAMESPACE}" --timeout=120s 2>/dev/null \
    && ok "PVC ${pvc_name} is Bound" \
    || warn "PVC ${pvc_name} not yet Bound — check storageClass and provisioner"
done

# ── Step 8: Tekton operator ────────────────────────────────────────
step 8 "Verifying Tekton operator"
if kubectl get pods -n tekton-pipelines 2>/dev/null | grep -q Running; then
  ok "Tekton operator is running"
else
  warn "Tekton operator not detected in tekton-pipelines namespace"
  warn "Install Tekton Pipelines: https://tekton.dev/docs/installation/pipelines/"
fi

# ── Step 9: Deploy step/task definitions ───────────────────────────
step 9 "Deploying Tekton steps and tasks"
cd "${TEKTONC_DIR}"
for f in tekton/steps/*.yaml; do
  [ -f "$f" ] && kubectl apply -f "$f" -n "${NAMESPACE}"
done
for f in tekton/tasks/*.yaml; do
  [ -f "$f" ] && kubectl apply -f "$f" -n "${NAMESPACE}"
done
# NOTE: Pipeline YAMLs are compiled from tektoncsample/sim2real/pipeline.yaml.j2
# by tektonc during /sim2real-prepare (after values.yaml is generated).
# They are applied to the cluster during /sim2real-deploy.
ok "Tekton steps and tasks deployed"

# ── Step 10: Container registry (Quay.io) ──────────────────────────
step 10 "Configuring container registry for EPP image"
echo
echo -e "${BLUE}The sim2real pipeline builds an EPP (Endpoint Picker Plugin) image${NC}"
echo -e "${BLUE}containing the translated scoring algorithm. This image is pushed${NC}"
echo -e "${BLUE}to a container registry and deployed to the cluster for benchmarking.${NC}"
echo
echo -e "${BLUE}If using Quay.io, set up before continuing:${NC}"
echo "  1. Go to https://quay.io/repository/ and create a repository (e.g., llm-d-inference-scheduler)"
echo "  2. Go to Account Settings → Robot Accounts → Create Robot Account"
echo "  3. Grant the robot 'Write' permission on your repository"
echo "  4. Note the robot username (e.g., username+robot_name) and token"
echo
echo -e "${BLUE}Set these environment variables before running this script:${NC}"
echo "  export QUAY_ROBOT_USERNAME='username+robot_name'"
echo "  export QUAY_ROBOT_TOKEN='<robot-account-token>'"
echo
if [ -z "${REGISTRY:-}" ]; then
  read -rp "Enter container registry host (e.g., quay.io/username): " REGISTRY
fi
if [ -n "${REGISTRY}" ] && [ -z "${REPO_NAME:-}" ]; then
  read -rp "Enter repository name [llm-d-inference-scheduler]: " REPO_NAME
  REPO_NAME="${REPO_NAME:-llm-d-inference-scheduler}"
fi
if [ -n "${REGISTRY}" ]; then
  # Determine docker server from registry (e.g., quay.io from quay.io/username)
  DOCKER_SERVER="${REGISTRY%%/*}"

  # Create registry-secret from env vars or interactive login
  if [ -n "${QUAY_ROBOT_USERNAME:-}" ] && [ -n "${QUAY_ROBOT_TOKEN:-}" ]; then
    info "Creating registry-secret from QUAY_ROBOT_USERNAME/QUAY_ROBOT_TOKEN..."
    kubectl create secret docker-registry registry-secret \
      --namespace "${NAMESPACE}" \
      --docker-server="${DOCKER_SERVER}" \
      --docker-username="${QUAY_ROBOT_USERNAME}" \
      --docker-password="${QUAY_ROBOT_TOKEN}" \
      --dry-run=client -o yaml | kubectl apply -f -
    ok "registry-secret created/updated for in-cluster builds"
  else
    warn "QUAY_ROBOT_USERNAME or QUAY_ROBOT_TOKEN not set"
    info "Falling back to container runtime login..."
    ${CONTAINER_RT} login "${DOCKER_SERVER}" || warn "Registry login failed — you can retry later"

    # Try to find auth config from login
    CONFIG_PATH=""
    for candidate in \
      "${HOME}/.docker/config.json" \
      "${HOME}/.config/containers/auth.json" \
      "${XDG_RUNTIME_DIR:-/tmp}/containers/auth.json"; do
      if [ -f "${candidate}" ]; then
        CONFIG_PATH="${candidate}"
        break
      fi
    done

    if [ -n "${CONFIG_PATH}" ]; then
      kubectl create secret docker-registry registry-secret \
        --namespace "${NAMESPACE}" \
        --from-file=.dockerconfigjson="${CONFIG_PATH}" \
        --dry-run=client -o yaml | kubectl apply -f -
      ok "registry-secret created/updated for in-cluster builds"
    else
      err "Could not create registry-secret. Set env vars and re-run:"
      echo "  export QUAY_ROBOT_USERNAME='username+robot_name'"
      echo "  export QUAY_ROBOT_TOKEN='<robot-account-token>'"
      exit 1
    fi
  fi
else
  warn "No registry specified — skipping. Set later in config/env_defaults.yaml"
fi

# ── Step 11: Upstream EPP image registry (for baseline/noise) ──────
step 11 "Configuring upstream EPP image registry"
echo
echo -e "${BLUE}The baseline and noise pipelines use the upstream llm-d EPP image.${NC}"
echo -e "${BLUE}If the upstream image is in a private registry, provide the pull${NC}"
echo -e "${BLUE}credentials so the cluster can pull it.${NC}"
echo
UPSTREAM_REGISTRY="${UPSTREAM_REGISTRY:-ghcr.io/llm-d}"
read -rp "Upstream EPP registry [${UPSTREAM_REGISTRY}]: " input_upstream
UPSTREAM_REGISTRY="${input_upstream:-${UPSTREAM_REGISTRY}}"

UPSTREAM_REPO="${UPSTREAM_REPO:-llm-d-inference-scheduler}"
read -rp "Upstream EPP repository name [${UPSTREAM_REPO}]: " input_upstream_repo
UPSTREAM_REPO="${input_upstream_repo:-${UPSTREAM_REPO}}"

UPSTREAM_TAG="${UPSTREAM_TAG:-latest}"
read -rp "Upstream EPP image tag [${UPSTREAM_TAG}]: " input_upstream_tag
UPSTREAM_TAG="${input_upstream_tag:-${UPSTREAM_TAG}}"

UPSTREAM_SERVER="${UPSTREAM_REGISTRY%%/*}"

# Create pull secret for upstream registry if credentials are provided
if [ -n "${UPSTREAM_ROBOT_USERNAME:-}" ] && [ -n "${UPSTREAM_ROBOT_TOKEN:-}" ]; then
  info "Creating upstream-registry-secret from UPSTREAM_ROBOT_USERNAME/UPSTREAM_ROBOT_TOKEN..."
  kubectl create secret docker-registry upstream-registry-secret \
    --namespace "${NAMESPACE}" \
    --docker-server="${UPSTREAM_SERVER}" \
    --docker-username="${UPSTREAM_ROBOT_USERNAME}" \
    --docker-password="${UPSTREAM_ROBOT_TOKEN}" \
    --dry-run=client -o yaml | kubectl apply -f -
  ok "upstream-registry-secret created/updated"
elif [ "${UPSTREAM_SERVER}" != "ghcr.io" ]; then
  # Only prompt if it's not the default public ghcr.io
  warn "If the upstream registry requires authentication, set:"
  echo "  export UPSTREAM_ROBOT_USERNAME='username'"
  echo "  export UPSTREAM_ROBOT_TOKEN='token'"
  echo "  Then re-run setup, or create the secret manually:"
  echo "    kubectl create secret docker-registry upstream-registry-secret \\"
  echo "      --namespace ${NAMESPACE} \\"
  echo "      --docker-server=${UPSTREAM_SERVER} \\"
  echo "      --docker-username=<username> \\"
  echo "      --docker-password=<token>"
else
  ok "Using public registry ${UPSTREAM_REGISTRY} (no pull secret needed)"
fi

# ── Save setup outputs ─────────────────────────────────────────────
SETUP_OUTPUT="${SIM2REAL_ROOT}/workspace/setup_config.json"
mkdir -p "${SIM2REAL_ROOT}/workspace"
cat > "${SETUP_OUTPUT}" <<SETUP_JSON
{
  "namespace": "${NAMESPACE}",
  "registry": "${REGISTRY:-}",
  "repo_name": "${REPO_NAME:-llm-d-inference-scheduler}",
  "upstream_registry": "${UPSTREAM_REGISTRY}",
  "upstream_repo": "${UPSTREAM_REPO}",
  "upstream_tag": "${UPSTREAM_TAG}",
  "storage_class": "${STORAGE_CLASS}",
  "is_openshift": ${IS_OPENSHIFT},
  "tektonc_dir": "${TEKTONC_DIR}",
  "sim2real_root": "${SIM2REAL_ROOT}",
  "container_runtime": "${CONTAINER_RT}",
  "setup_timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "run_name": "${RUN_NAME}",
  "run_dir": "${RUN_DIR}"
}
SETUP_JSON
ok "Setup config saved to ${SETUP_OUTPUT}"

# ── Write run metadata ─────────────────────────────────────────────
METADATA_FILE="${RUN_DIR}/run_metadata.json"
cat > "${METADATA_FILE}" <<METADATA_JSON
{
  "run_name": "${RUN_NAME}",
  "namespace": "${NAMESPACE}",
  "registry": "${REGISTRY:-}",
  "repo_name": "${REPO_NAME:-llm-d-inference-scheduler}",
  "upstream_registry": "${UPSTREAM_REGISTRY}",
  "upstream_repo": "${UPSTREAM_REPO}",
  "upstream_tag": "${UPSTREAM_TAG}",
  "storage_class": "${STORAGE_CLASS}",
  "is_openshift": ${IS_OPENSHIFT},
  "container_runtime": "${CONTAINER_RT}",
  "created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "pipeline_commit": "$(cd ${SIM2REAL_ROOT} && git rev-parse --short HEAD 2>/dev/null || echo unknown)",
  "stages": {
    "setup": {
      "status": "completed",
      "completed_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
      "summary": "Namespace ${NAMESPACE} configured, PVCs created, Tekton tasks deployed"
    },
    "prepare": { "status": "pending" },
    "deploy": { "status": "pending" },
    "results": { "status": "pending" }
  }
}
METADATA_JSON
ok "Run metadata saved to ${METADATA_FILE}"

# ── Done ───────────────────────────────────────────────────────────
echo
echo -e "${GREEN}━━━ Setup complete ━━━${NC}"
echo
echo "Setup config saved to: ${SETUP_OUTPUT}"
echo "Run name: ${RUN_NAME}"
echo "Run directory: ${RUN_DIR}"
echo
echo "Next steps:"
echo "  1. Review config/env_defaults.yaml (gateway, vLLM image, registry, fast_iteration)"
echo "  2. Run /sim2real-prepare to start the transfer pipeline"
echo
