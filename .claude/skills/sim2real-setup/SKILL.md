---
name: sim2real-setup
description: |
  One-time cluster and environment setup for the sim2real transfer pipeline.
  Creates namespace, secrets, RBAC roles, PVCs, and deploys Tekton tasks.
  Idempotent — safe to re-run.
argument-hint: "[--namespace NAME] [--hf-token TOKEN] [--run NAME]"
user-invocable: true
allowed-tools:
  - Bash(**/setup.sh *)
  - Bash(kubectl *)
  - Bash(oc *)
  - Bash(tkn *)
  - Bash(python3 *)
  - Bash(pip *)
  - Bash(bash *)
  - Glob
  - Read
  - Edit
  - Write
---

# sim2real-setup

One-time setup for the sim2real transfer pipeline. This skill configures
your cluster environment and deploys all required Tekton resources.

## Execution

> **Standalone option:** Users who prefer not to invoke Claude can run the
> script directly from the repo root:
> ```bash
> python scripts/setup.py --help
> python scripts/setup.py --namespace <NS> --hf-token <TOKEN> --registry <REG>
> ```
> The script is interactive — it prompts for any missing values.

**Run the setup script directly inside this Claude session.** Do not ask the
user to open a separate terminal.

1. Locate the setup script:
   ```
   Glob: **/skills/sim2real-setup/scripts/setup.sh
   ```
   Store the result as `[SETUP_SCRIPT]`.

2. Collect required values from the user via questions (namespace, HF token,
   registry) before running the script. Pass them as flags to avoid interactive
   prompts:
   ```bash
   bash [SETUP_SCRIPT] --namespace <NS> --hf-token <TOKEN> --registry <REG> --run <RUN_NAME>
   ```

   The `--run` flag names the experiment run. All artifacts will be stored under `workspace/runs/<run_name>/`. If omitted, the script prompts interactively and defaults to `sim2real-YYYY-MM-DD`.

3. If the user doesn't provide a value, ask them using the AskUserQuestion
   tool before running the script.

4. After the script completes, read `workspace/setup_config.json` to confirm
   the outputs were saved. Report the summary to the user.

## What It Does

1. Verifies prerequisites: `kubectl`, `tkn`, `python3`, `gh`, `podman`/`docker`
2. Initializes git submodules: `inference-sim`, `llm-d-inference-scheduler`, `tektonc-data-collection`
3. Creates Python venv and installs dependencies
4. Prompts for `NAMESPACE` (if not set via `$NAMESPACE` or `--namespace`)
5. Prompts for HuggingFace token (if not set via `$HF_TOKEN` or `--hf-token`)
6. Creates namespace
7. Creates `hf-secret` in namespace
8. Applies RBAC roles from `tekton/roles.yaml`
9. Deploys Tekton step, task, and pipeline definitions
10. Creates PVCs: `model-pvc` (300Gi), `data-pvc` (20Gi), `source-pvc` (20Gi)
11. Verifies Tekton operator is running
12. Prompts for container registry and creates `registry-secret`
13. Saves setup config to `workspace/setup_config.json`
14. Creates `workspace/runs/<run_name>/` directory
15. Writes `run_metadata.json` with environment info and stage tracking for pipeline resumability

> **OpenShift users:** The script detects OpenShift and runs additional
> commands: `oc adm policy add-scc-to-user anyuid` and `oc adm policy add-cluster-role-to-user cluster-admin` for helm-installer.
> PVCs use `ibm-spectrum-scale-fileset` storageClassName on OpenShift.

## After Setup

Edit `config/env_defaults.yaml` for infrastructure choices:
- `stack.gateway.helmValues.gateway.provider` — `istio` or `kgateway`
- `stack.model.vllm_image` — optional vLLM image override
- `stack.gaie.epp_image.build.hub` — your container registry
- `pipeline.fast_iteration` — `true` to skip noise gate + mechanism check
- `observe.request_multiplier` — workload scaling factor

Then run `/sim2real-prepare` to start the pipeline.
