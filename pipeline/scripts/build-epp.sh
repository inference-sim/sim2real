#!/usr/bin/env bash
set -euo pipefail

# ── Build EPP image on Kubernetes cluster ──────────────────────────
# Usage: build-epp.sh --run-dir <path> --run-name <name> --namespace <ns>
#          --registry-secret-name <name>
#          [--image-ref <ref>] [--source-dir <path>] [--experiment-root <path>]
#
# When --image-ref and --source-dir are provided, uses them directly.
# Otherwise reads registry/repo from run_metadata.json (created by /sim2real-setup).
# Requires a dockerconfigjson Secret in the namespace for push credentials;
# the caller passes its name via --registry-secret-name (populated from
# cluster_config.json:secret_names.registry_creds).

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
info()  { echo -e "${BLUE}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()   { echo -e "${RED}[ERROR]${NC} $*" >&2; }

cleanup() {
  kubectl delete pod source-copy -n "${NAMESPACE}" --ignore-not-found --force --grace-period=0 2>/dev/null || true
}
trap cleanup EXIT

# ── Parse args ─────────────────────────────────────────────────────
RUN_DIR=""
RUN_NAME=""
NAMESPACE=""
REGISTRY_SECRET_NAME=""
EXPERIMENT_ROOT=""
IMAGE_REF=""
SOURCE_DIR=""

while [[ $# -gt 0 ]]; do
  case $1 in
    --run-dir)              RUN_DIR="$2"; shift 2 ;;
    --run-name)             RUN_NAME="$2"; shift 2 ;;
    --namespace)            NAMESPACE="$2"; shift 2 ;;
    --registry-secret-name) REGISTRY_SECRET_NAME="$2"; shift 2 ;;
    --experiment-root)      EXPERIMENT_ROOT="$2"; shift 2 ;;
    --image-ref)            IMAGE_REF="$2"; shift 2 ;;
    --source-dir)           SOURCE_DIR="$2"; shift 2 ;;
    *) err "Unknown arg: $1"; exit 1 ;;
  esac
done

if [ -z "${RUN_DIR}" ] || [ -z "${RUN_NAME}" ] || [ -z "${NAMESPACE}" ] || [ -z "${REGISTRY_SECRET_NAME}" ]; then
  err "Usage: build-epp.sh --run-dir <path> --run-name <name> --namespace <ns> --registry-secret-name <name> [--experiment-root <path>]"
  exit 1
fi

# ── Find repo root ─────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SIM2REAL_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SCHEDULER_ROOT="${EXPERIMENT_ROOT:-${SIM2REAL_ROOT}}"

# ── Step 1: Resolve image ref and source dir ─────────────────────
info "Resolving build parameters..."
METADATA="${RUN_DIR}/run_metadata.json"

if [ -n "${IMAGE_REF}" ] && [ -n "${SOURCE_DIR}" ]; then
  FULL_IMAGE="${IMAGE_REF}"
  SCHEDULER_DIR="${SOURCE_DIR}"
else
  # Fallback: derive from run_metadata.json (backward compat)
  if [ ! -f "${METADATA}" ]; then
    err "run_metadata.json not found at ${METADATA}"
    exit 1
  fi
  REGISTRY_HUB=$(python3 -c "import json; print(json.load(open('${METADATA}'))['registry'])")
  REPO_NAME=$(python3 -c "import json; print(json.load(open('${METADATA}')).get('repo_name','llm-d-inference-scheduler'))")
  FULL_IMAGE="${REGISTRY_HUB}/${REPO_NAME}:${RUN_NAME}"
  SCHEDULER_DIR="${SCHEDULER_ROOT}/${REPO_NAME}"
fi

DIR_BASENAME=$(basename "${SCHEDULER_DIR}")
info "Target image: ${FULL_IMAGE}"
info "Source dir: ${SCHEDULER_DIR}"

# ── Step 2: Check registry secret ─────────────────────────────────
info "Checking registry secret '${REGISTRY_SECRET_NAME}'..."
if ! kubectl get secret "${REGISTRY_SECRET_NAME}" -n "${NAMESPACE}" >/dev/null 2>&1; then
  err "registry secret '${REGISTRY_SECRET_NAME}' not found in namespace ${NAMESPACE}"
  echo
  echo "This Secret is provisioned by 'cluster.py provision --registry-user <u> --registry-token <t>';"
  echo "its name is recorded in cluster_config.json:secret_names.registry_creds."
  echo
  echo "If provisioning by hand instead, create with:"
  echo "  kubectl create secret docker-registry ${REGISTRY_SECRET_NAME} \\"
  echo "    --namespace ${NAMESPACE} \\"
  echo "    --docker-server=quay.io \\"
  echo "    --docker-username=<robot-account-name> \\"
  echo "    --docker-password=<robot-account-token>"
  exit 1
fi
ok "registry secret '${REGISTRY_SECRET_NAME}' found"

# ── Step 3: Copy source to cluster ─────────────────────────────────
info "Copying ${DIR_BASENAME} source to cluster..."

# Clean up any leftover pod from a previous run
kubectl delete pod source-copy -n "${NAMESPACE}" --ignore-not-found --force --grace-period=0 2>/dev/null || true

kubectl run source-copy --image=busybox --restart=Never \
  --namespace "${NAMESPACE}" \
  --overrides='{
    "spec":{
      "volumes":[{"name":"src","persistentVolumeClaim":{"claimName":"source-pvc"}}],
      "containers":[{"name":"source-copy","image":"busybox",
        "command":["sh","-c","sleep 600"],
        "volumeMounts":[{"name":"src","mountPath":"/workspace/source"}]}]
    }
  }'

info "Waiting for source-copy pod..."
kubectl wait pod/source-copy --for=condition=Ready -n "${NAMESPACE}" --timeout=120s

info "Cleaning stale source on PVC..."
kubectl exec source-copy -n "${NAMESPACE}" -- sh -c "rm -rf /workspace/source/${RUN_NAME}"

info "Uploading source (this may take a minute)..."
kubectl exec source-copy -n "${NAMESPACE}" -- mkdir -p "/workspace/source/${RUN_NAME}"
kubectl cp "${SCHEDULER_DIR}/" "${NAMESPACE}/source-copy:/workspace/source/${RUN_NAME}/${DIR_BASENAME}"
ok "Source uploaded"

kubectl delete pod source-copy --namespace "${NAMESPACE}" --force --grace-period=0 2>/dev/null || true

# ── Step 4: Submit build pod ───────────────────────────────────────
BUILD_POD="epp-build-${RUN_NAME}"

# Clean up any leftover build pod
kubectl delete pod "${BUILD_POD}" -n "${NAMESPACE}" --ignore-not-found --force --grace-period=0 2>/dev/null || true

info "Submitting build pod: ${BUILD_POD}"
cat <<BUILDPOD | kubectl apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: ${BUILD_POD}
  namespace: ${NAMESPACE}
spec:
  restartPolicy: Never
  containers:
    - name: buildkit
      image: moby/buildkit:latest
      command:
        - buildctl-daemonless.sh
      args:
        - build
        - --frontend=dockerfile.v0
        - --local
        - context=/workspace/source/${RUN_NAME}/${DIR_BASENAME}
        - --local
        - dockerfile=/workspace/source/${RUN_NAME}/${DIR_BASENAME}
        - --opt
        - filename=Dockerfile.epp
        - --opt
        - platform=linux/amd64
        - --output
        - type=image,name=${FULL_IMAGE},push=true
      securityContext:
        privileged: true
      volumeMounts:
        - name: source
          mountPath: /workspace/source
        - name: registry-creds
          mountPath: /root/.docker
  volumes:
    - name: source
      persistentVolumeClaim:
        claimName: source-pvc
    - name: registry-creds
      secret:
        secretName: ${REGISTRY_SECRET_NAME}
        items:
          - key: .dockerconfigjson
            path: config.json
BUILDPOD

# ── Step 5: Wait for build ────────────────────────────────────────
# Poll for a terminal phase rather than `kubectl wait --for=...=Succeeded`,
# which has no OR semantics across matchers: a pod that exits with
# phase=Failed would otherwise be indistinguishable from a still-running
# build and block for the full timeout. (#275)
info "Building image (this may take several minutes)..."
BUILD_TIMEOUT=1800
deadline=$(( $(date +%s) + BUILD_TIMEOUT ))
while :; do
  phase=$(kubectl get pod "${BUILD_POD}" -n "${NAMESPACE}" \
    -o jsonpath='{.status.phase}' 2>/dev/null || echo "")
  case "${phase}" in
    Succeeded)
      ok "Image built and pushed: ${FULL_IMAGE}"
      break
      ;;
    Failed)
      err "Build failed (phase=Failed). Logs:"
      kubectl logs "${BUILD_POD}" -n "${NAMESPACE}" --tail=80 2>/dev/null || true
      exit 1
      ;;
  esac
  if (( $(date +%s) > deadline )); then
    err "Timed out after ${BUILD_TIMEOUT}s waiting for build pod (phase=${phase:-unknown}). Last logs:"
    kubectl logs "${BUILD_POD}" -n "${NAMESPACE}" --tail=80 2>/dev/null || true
    exit 1
  fi
  sleep 3
done

# ── Step 6: Set GHCR package visibility to public (best-effort) ────
# GHCR creates new packages with private visibility (inherited from the
# repository). Cluster pods then fail with ImagePullBackOff because no
# imagePullSecret is wired onto their service account. Setting the package
# to public is a one-time per-package operation; subsequent pushes are a
# no-op. Non-GHCR registries (e.g. Quay.io) manage visibility differently
# and are skipped.
#
# Best-effort: a failure here (gh not authenticated, package already public,
# unusual nested path) is non-fatal. The user will see ImagePullBackOff
# later and can fix visibility manually.
IMAGE_NO_TAG="${FULL_IMAGE%:*}"
REGISTRY_HOST="${IMAGE_NO_TAG%%/*}"
if [ "${REGISTRY_HOST}" = "ghcr.io" ]; then
  REST="${IMAGE_NO_TAG#ghcr.io/}"
  if [[ "${REST}" == */* ]]; then
    PKG_OWNER="${REST%%/*}"
    PKG_NAME="${REST#*/}"
    info "Setting GHCR package ${PKG_OWNER}/${PKG_NAME} visibility=public..."
    if gh api --method PUT "/orgs/${PKG_OWNER}/packages/container/${PKG_NAME}/visibility" \
         -f visibility=public >/dev/null 2>&1; then
      ok "GHCR package set to public (org-owned)"
    elif gh api --method PUT "/user/packages/container/${PKG_NAME}/visibility" \
         -f visibility=public >/dev/null 2>&1; then
      ok "GHCR package set to public (user-owned)"
    else
      warn "Could not set GHCR package visibility automatically."
      warn "If pods fail with ImagePullBackOff, run one of:"
      warn "  gh api --method PUT /orgs/${PKG_OWNER}/packages/container/${PKG_NAME}/visibility -f visibility=public"
      warn "  gh api --method PUT /user/packages/container/${PKG_NAME}/visibility -f visibility=public"
    fi
  fi
fi

# ── Step 7: Clean up build pod ─────────────────────────────────────
kubectl delete pod "${BUILD_POD}" -n "${NAMESPACE}" --ignore-not-found --force --grace-period=0 2>/dev/null || true

# ── Step 8: Update metadata ───────────────────────────────────────
if [ -z "${IMAGE_REF}" ] && [ -f "${METADATA}" ]; then
  python3 -c "
import json
m = json.load(open('${METADATA}'))
m['stages']['deploy']['last_completed_step'] = 'build_epp'
m['epp_image'] = '${FULL_IMAGE}'
json.dump(m, open('${METADATA}', 'w'), indent=2)
"
fi

# ── Done ───────────────────────────────────────────────────────────
echo
echo -e "${GREEN}━━━ EPP image built successfully ━━━${NC}"
echo "  Image: ${FULL_IMAGE}"
echo
