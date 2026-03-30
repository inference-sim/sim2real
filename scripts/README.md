# scripts/

Three scripts drive the sim2real transfer pipeline. Each is an **interactive, step-by-step experience** — the script prompts you for inputs as needed, shows what it's doing, and pauses for decisions at key points. You do not need to read the source to use them.

## Getting Started

Run in order, once per algorithm transfer:

```bash
python scripts/setup.py      # one-time cluster + environment bootstrap
python scripts/prepare.py    # Extract → Translate → Generate → Build/Test → Review
python scripts/deploy.py     # Build EPP image → Cluster benchmarks → (optional) PR
```

Each script walks you through its steps interactively. When in doubt, accept the defaults.

---

## Advanced Usage

All scripts accept CLI flags to pre-fill prompts or change behavior. Pass `--help` to any script for the full list.

### setup.py

One-time, idempotent — safe to re-run if something changes.

```
--namespace NS          Kubernetes namespace (env: NAMESPACE)
--hf-token TOKEN        HuggingFace token (env: HF_TOKEN)
--registry REG          Container registry, e.g. quay.io/username (env: QUAY_ROBOT_USERNAME/TOKEN)
--registry-user USER    Registry robot username
--registry-token TOKEN  Registry robot token
--storage-class SC      PVC storageClassName (auto-detected on OpenShift)
--run NAME              Run name [default: sim2real-YYYY-MM-DD]
--no-cluster            Skip all kubectl/tkn steps — collect config and write JSON only
```

Secrets can also be passed via environment variables (`HF_TOKEN`, `QUAY_ROBOT_USERNAME`, `QUAY_ROBOT_TOKEN`, `NAMESPACE`) to avoid interactive prompts in CI.

### prepare.py

Runs LLM-assisted stages: Extract → Translate → Generate (writer + reviewer loop) → Build/Test → Final Review. At each loop boundary the script pauses and prompts: `[c]ontinue / [+N] more rounds / [a]ccept / [q]uit`.

```
--reviews N          Max rounds per LLM loop before pausing [default: 2]
--force              Regenerate all artifacts; skip reuse prompts
--skip-generate      Skip Generate if scorer file already exists (resume after manual edit)
--dev                1 reviewer instead of 3; faster iteration, not for production
```

Environment variables for LLM access:
```
OPENAI_API_KEY + OPENAI_BASE_URL            (preferred)
ANTHROPIC_AUTH_TOKEN + ANTHROPIC_BASE_URL   (fallback)
```

### deploy.py

Runs cluster-side stages: Build EPP image (in-cluster BuildKit) → Suite A/B/C benchmarks → optional PR creation. Benchmark phases track completion state; already-done phases are skipped unless `--force-rerun` is set.

```
--skip-build-epp   Skip EPP build (image already pushed this run)
--pr               Create PR after benchmarks pass [default: skip — review results first]
--force-rerun      Re-run already-completed benchmark phases without prompting
```
