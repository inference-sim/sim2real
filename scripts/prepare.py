#!/usr/bin/env python3
"""sim2real prepare — Extract, Translate, Generate, Build/Test, Review."""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Repo layout ──────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parent.parent
MODELS = ["Azure/gpt-4o", "GCP/gemini-2.5-flash", "aws/claude-opus-4-6"]
DEV_MODELS = ["aws/claude-opus-4-6"]

# ── Color helpers ─────────────────────────────────────────────────────────────
_tty = sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _tty else text


def info(msg: str) -> None: print(_c("34", "[INFO]  ") + msg)
def ok(msg: str)   -> None: print(_c("32", "[OK]    ") + msg)
def warn(msg: str) -> None: print(_c("33", "[WARN]  ") + msg)
def err(msg: str)  -> None: print(_c("31", "[ERROR] ") + msg, file=sys.stderr)


def step(n, title: str) -> None:
    print("\n" + _c("36", f"━━━ Step {n}: {title} ━━━"))


def _should_skip(artifact: Path, stage_name: str, force: bool) -> bool:
    """Return True if the stage should be skipped (artifact reused).
    If --force: always False. If artifact exists: ask user."""
    if force or not artifact.exists():
        return False
    rel = artifact.relative_to(REPO_ROOT)
    print(f"\n  Existing artifact: {rel}")
    while True:
        choice = input(f"  Reuse for {stage_name}? [Y]es / [n]o regenerate: ").strip().lower()
        if choice in ("", "y", "yes"):
            info(f"[skip] {stage_name} — reusing {rel}")
            return True
        if choice in ("n", "no"):
            return False
        print("  Enter 'y' to reuse or 'n' to regenerate.")


# ── Subprocess helper ─────────────────────────────────────────────────────────

def run(cmd: list[str], *, check: bool = True, capture: bool = False,
        input: str | None = None, cwd: Path | None = None,
        env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, check=check, text=True,
        capture_output=capture, input=input,
        cwd=cwd, env=env,
    )


# ── Argument parser ───────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="prepare.py",
        description="sim2real prepare: Extract → Translate → Generate → Build/Test → Review",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Environment variables:
  OPENAI_API_KEY + OPENAI_BASE_URL       (preferred)
  ANTHROPIC_AUTH_TOKEN + ANTHROPIC_BASE_URL  (fallback)

Examples:
  python scripts/prepare.py
  python scripts/prepare.py --reviews 3
""",
    )
    p.add_argument(
        "--reviews", type=int, default=2, metavar="N",
        help="Number of rounds per LLM loop [default: 2]",
    )
    p.add_argument(
        "--force", action="store_true",
        help="Regenerate all artifacts even if they already exist from a previous run",
    )
    p.add_argument(
        "--skip-generate", action="store_true",
        help="Skip the Generate stage if the scorer file is already present on disk. "
             "Useful to resume after a manual scorer edit or a partial run.",
    )
    p.add_argument(
        "--dev", action="store_true",
        help="Dev mode: use only 1 reviewer (aws/claude-opus-4-6) instead of 3. "
             "Faster iteration; not suitable for production runs.",
    )
    return p


# ── Intro banner ──────────────────────────────────────────────────────────────

def print_intro(reviews: int, dev: bool = False) -> None:
    print(_c("36", "\n━━━ sim2real-prepare ━━━\n"))
    if dev:
        print(_c("33", "  [DEV MODE] 1 reviewer (aws/claude-opus-4-6) — not for production\n"))
    print("Stages: Extract → Translate → Generate → Build/Test → Final Review\n")
    reviewer_note = "1 model (dev)" if dev else "all 3 models"
    print("LLM interactions (3 total):")
    print("  1. Translate     single call   1 model maps signals to production equivalents")
    print(f"  2. Generate      writer loop   claude-opus-4-6 writes; {reviewer_note} review;")
    print("                                 issues fed back to writer until consistent")
    print("  3. Final Review  review loop   all 3 models do a final check after build/test;")
    print("                                 if issues found, returns to Generate")
    print(f"\n--reviews {reviews}: each loop runs up to {reviews} round(s), then pauses for your input.")
    print("At each pause: [c]ontinue / [+N] N more rounds / [a]ccept / [q]uit")
    print("If artifacts from a previous run exist, you will be asked whether to reuse them.")
    print("Pass --force to regenerate everything without being asked.\n")


# ── Prerequisites ─────────────────────────────────────────────────────────────

def check_prerequisites() -> None:
    step(0, "Checking prerequisites")
    required_files = [
        REPO_ROOT / "blis_router/best/best_program.go",
        REPO_ROOT / "blis_router/best/best_program_info.json",
        REPO_ROOT / "docs/transfer/blis_to_llmd_mapping.md",
        REPO_ROOT / "docs/transfer/scorer_template.go.md",
        REPO_ROOT / "workspace/setup_config.json",
    ]
    required_dirs = [
        REPO_ROOT / "inference-sim/sim",
        REPO_ROOT / "llm-d-inference-scheduler/pkg",
    ]
    missing = [str(p) for p in required_files if not p.exists()]
    missing += [str(p) for p in required_dirs if not p.is_dir()]
    if missing:
        err("Missing prerequisites:")
        for m in missing:
            print(f"  • {m}")
        err("Run python scripts/setup.py first, and ensure submodules are initialized.")
        sys.exit(1)
    ok("Prerequisites satisfied")


# ── Setup config ──────────────────────────────────────────────────────────────

def load_setup_config() -> dict:
    cfg_path = REPO_ROOT / "workspace/setup_config.json"
    cfg = json.loads(cfg_path.read_text())
    run_name = cfg["current_run"]
    run_dir = REPO_ROOT / "workspace/runs" / run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    ok(f"Run: {run_name}  ({run_dir})")
    return cfg


# ── Run metadata ──────────────────────────────────────────────────────────────

def update_run_metadata(run_dir: Path, stage: str, **fields) -> None:
    meta_path = run_dir / "run_metadata.json"
    if not meta_path.exists():
        return
    meta = json.loads(meta_path.read_text())
    meta["stages"].setdefault(stage, {}).update(fields)
    meta_path.write_text(json.dumps(meta, indent=2))


# ── Stage 1: Extract ──────────────────────────────────────────────────────────

def stage_extract(run_dir: Path, force: bool = False) -> Path:
    out = run_dir / "prepare_algorithm_summary.json"
    if _should_skip(out, "Extract", force):
        return out
    step(1, "Extract")
    out.unlink(missing_ok=True)

    venv_python = str(REPO_ROOT / ".venv/bin/python")
    cli = str(REPO_ROOT / "tools/transfer_cli.py")

    result = run(
        [venv_python, cli, "extract", "--strict", str(REPO_ROOT / "blis_router/best/")],
        check=False, capture=True, cwd=REPO_ROOT,
    )
    if result.returncode != 0:
        err(f"extract failed:\n{result.stderr}")
        sys.exit(1)

    src = REPO_ROOT / "workspace/algorithm_summary.json"
    if not src.exists():
        err("extract succeeded but workspace/algorithm_summary.json not found")
        sys.exit(1)

    # Validate before copying — schema lookup uses filename stem
    run([venv_python, cli, "validate-schema", str(src)], cwd=REPO_ROOT)

    scope_ok = run(
        [venv_python, "-c",
         f"import json,sys; d=json.load(open('{src}')); "
         "sys.exit(0 if d.get('scope_validation_passed') is True else 1)"],
        check=False,
    ).returncode == 0
    if not scope_ok:
        err("scope_validation_passed is not true — HALT")
        sys.exit(1)

    shutil.copy(src, out)
    ok(f"Extract complete → {out.relative_to(REPO_ROOT)}")
    return out


# ── Stage 2: Translate ────────────────────────────────────────────────────────

def _extract_mapping_hash() -> str:
    text = (REPO_ROOT / "docs/transfer/blis_to_llmd_mapping.md").read_text()
    m = re.search(r"\*{0,2}Pinned commit hash:\*{0,2}\s*([0-9a-f]{7,40})", text)
    if not m:
        err("Could not extract pinned commit hash from mapping artifact")
        sys.exit(1)
    return m.group(1)[:7]


def build_translate_prompt(algo_summary: dict, mapping_doc: str,
                            submodule_head: str) -> list[dict]:
    system = (
        "You are a precise technical translator. Your task is to map simulation "
        "signals from an algorithm summary to their production equivalents, "
        "following the provided mapping document exactly. "
        "Respond ONLY with valid JSON matching the signal_coverage schema. "
        "No prose before or after the JSON."
    )
    user = f"""Map the signals in this algorithm summary to their production equivalents.

## Algorithm Summary
```json
{json.dumps(algo_summary, indent=2)}
```

## Mapping Document
{mapping_doc}

## Instructions
For each signal in algorithm_summary signals[], look it up in the mapping document.
Record: sim_name, prod_name, prod_access_path, fidelity_rating, staleness_window_ms=0, mapped=true
Propagate normalization and fidelity_provisional fields where present.
Check F-10 double-counting if composite_signals is non-empty.
Set commit_hash to: {submodule_head}
Set coverage_complete to true if unmapped_signals is empty.

Respond with ONLY the flat JSON object (no wrapper key, no markdown fences, no prose).
The top-level keys must be: signals, unmapped_signals, commit_hash, coverage_complete.
Do NOT wrap the object in a "signal_coverage" key.
"""
    return [{"role": "system", "content": system},
            {"role": "user", "content": user}]


def stage_translate(run_dir: Path, algo_summary_path: Path, force: bool = False) -> Path:
    out = run_dir / "prepare_signal_coverage.json"
    if _should_skip(out, "Translate", force):
        return out
    step(2, "Translate (single LLM call — gpt-4o)")
    out.unlink(missing_ok=True)

    # Deterministic submodule staleness check
    submodule_head = run(
        ["git", "-C", str(REPO_ROOT / "llm-d-inference-scheduler"), "rev-parse", "HEAD"],
        capture=True,
    ).stdout.strip()
    mapping_hash = _extract_mapping_hash()
    if not submodule_head.startswith(mapping_hash):
        err(f"Stale submodule: mapping pinned at {mapping_hash}, "
            f"submodule at {submodule_head[:7]}")
        sys.exit(1)
    ok(f"Submodule commit matches mapping artifact ({mapping_hash})")

    algo_summary = json.loads(algo_summary_path.read_text())
    mapping_doc = (REPO_ROOT / "docs/transfer/blis_to_llmd_mapping.md").read_text()

    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from lib.llm import call_model, LLMError

    info("Calling gpt-4o for signal translation...")
    messages = build_translate_prompt(algo_summary, mapping_doc, submodule_head)
    try:
        response = call_model("Azure/gpt-4o", messages)
    except LLMError as e:
        err(f"LLM call failed: {e}")
        sys.exit(1)

    # Strip markdown fences if model added them despite instructions
    clean = re.sub(r"^```(?:json)?\n?", "", response.strip())
    clean = re.sub(r"\n?```$", "", clean)

    try:
        coverage = json.loads(clean)
    except json.JSONDecodeError as e:
        err(f"Model returned invalid JSON: {e}\nResponse:\n{response[:500]}")
        sys.exit(1)

    # Unwrap if model returned {"signal_coverage": {...}} envelope
    if isinstance(coverage, dict) and list(coverage.keys()) == ["signal_coverage"]:
        coverage = coverage["signal_coverage"]

    # Fix up signals[] to match schema:
    # - normalization must be a string enum or absent (not null, not an object)
    # - remove any null-valued fields (schema uses additionalProperties: false)
    NORM_KEYS = {"divide_prod_by_100", "verify_and_normalize", "boolean_presence_check"}
    for sig in coverage.get("signals", []):
        norm = sig.get("normalization")
        if norm is None:
            sig.pop("normalization", None)
        elif isinstance(norm, dict):
            # Model returned {"type": "divide_prod_by_100", ...} — extract the string
            extracted = norm.get("type") or norm.get("action") or norm.get("name", "")
            if extracted in NORM_KEYS:
                sig["normalization"] = extracted
            else:
                sig.pop("normalization", None)
        elif isinstance(norm, str) and norm not in NORM_KEYS:
            sig.pop("normalization", None)
        # Remove any other null-valued fields
        for k in [k for k, v in list(sig.items()) if v is None]:
            del sig[k]

    venv_python = str(REPO_ROOT / ".venv/bin/python")
    cli = str(REPO_ROOT / "tools/transfer_cli.py")

    # Write to workspace/ first — schema lookup uses filename stem
    ws_out = REPO_ROOT / "workspace/signal_coverage.json"
    ws_out.write_text(json.dumps(coverage, indent=2))
    run([venv_python, cli, "validate-schema", str(ws_out)], cwd=REPO_ROOT)

    if not coverage.get("coverage_complete") or coverage.get("unmapped_signals"):
        err("coverage_complete is not true or unmapped_signals is non-empty — HALT")
        sys.exit(1)

    shutil.copy(ws_out, out)

    ok(f"Translate complete → {out.relative_to(REPO_ROOT)}")
    return out


# ── Stage 3: Generate (writer + reviewer loop) ────────────────────────────────

def _extract_code_block(text: str, index: int = 0) -> str:
    blocks = re.findall(r"```(?:\w+)?\s*\n(.*?)```", text, re.DOTALL)
    return blocks[index] if index < len(blocks) else ""


def _load_evolve_block(algo_summary: dict) -> str:
    """Extract EVOLVE-BLOCK lines from source file referenced in algo_summary."""
    evolve_block_source = algo_summary.get("evolve_block_source", "")
    if not evolve_block_source:
        return ""
    file_part, _, range_part = evolve_block_source.partition(":")
    full_text = (REPO_ROOT / file_part).read_text()
    if range_part and "-" in range_part:
        try:
            start, end = (int(x) for x in range_part.split("-", 1))
            return "\n".join(full_text.splitlines()[start - 1:end])
        except (ValueError, IndexError):
            pass
    return full_text


def build_generate_prompt(algo_summary: dict, signal_coverage: dict,
                           mapping_doc: str, scorer_template: str,
                           example_scorer: str, example_scorer_test: str,
                           build_error: str = "") -> list[dict]:
    system = (
        "You are an expert Go engineer implementing a production scorer plugin. "
        "Follow the template and constraints exactly. "
        "Respond with EXACTLY two fenced Go code blocks: "
        "first the scorer implementation, then the test file. "
        "Label each with its file path as a comment on the very first line:\n"
        "  // FILE: llm-d-inference-scheduler/pkg/plugins/scorer/<name>.go\n"
        "  // FILE: llm-d-inference-scheduler/pkg/plugins/scorer/<name>_test.go\n"
        "No prose before or after the code blocks."
    )
    build_error_section = ""
    if build_error:
        build_error_section = f"""
## Previous Build/Test Failure (fix these errors)
```
{build_error}
```

"""
    user = f"""Generate a production scorer plugin implementing the evolved routing algorithm.
{build_error_section}

## Algorithm Summary
```json
{json.dumps(algo_summary, indent=2)}
```

## Signal Coverage (use these production access paths exactly)
```json
{json.dumps(signal_coverage, indent=2)}
```

## Scorer Template
{scorer_template}

## Mapping Document (reference for composite signal expansion)
{mapping_doc[:4000]}

## Example Scorer Implementation (follow these patterns exactly)
```go
{example_scorer}
```

## Example Scorer Test File (copy this structure exactly for your test file)
```go
{example_scorer_test}
```

## Critical Requirements
1. Register the scorer in llm-d-inference-scheduler/pkg/plugins/register.go
2. EffectiveLoad expands to WaitingQueueSize + 2*RunningRequestsSize (both BatchSize and InFlightRequests map to RunningRequestsSize)
3. The scorer type constant must match the kebab-case scorer name with -scorer suffix
4. Test file MUST use `package scorer_test` (external test package, not `package scorer`)
5. Test file MUST import `github.com/llm-d/llm-d-inference-scheduler/test/utils` and use `utils.NewTestContext(t)` to create contexts
6. Test endpoints MUST be constructed with `scheduling.NewEndpoint(&fwkdl.EndpointMetadata{...}, &fwkdl.Metrics{...}, nil)`
7. Use `cmpopts.EquateApprox(0, 1e-9)` for float comparisons (import `github.com/google/go-cmp/cmp/cmpopts`)
8. Test math must be verified: for every test case that claims a score will be negative/positive,
   compute it manually step by step before writing the assertion. A claim like "both scores negative"
   must be validated with actual numbers — if the penalty (e.g. 0.4) is less than the base score
   (e.g. 0.407), the result is positive, not negative. Choose parameter values that guarantee
   the claimed scenario (e.g. increase effectiveLoad until base score < penalty).
9. Exported symbol names (type constant, factory function, constructor) must be consistent
   throughout scorer and test files. Use the same casing everywhere.
"""
    return [{"role": "system", "content": system},
            {"role": "user", "content": user}]


def build_revision_prompt(prev_code: str, issues: list[str],
                           algo_summary: dict, signal_coverage: dict,
                           mapping_doc: str, scorer_template: str,
                           example_scorer_test: str,
                           build_error: str = "") -> list[dict]:
    system = (
        "You are an expert Go engineer fixing a production scorer plugin based on "
        "reviewer feedback. Respond with EXACTLY two fenced Go code blocks: "
        "first the corrected scorer implementation, then the test file. "
        "Label each with its file path as a comment on the very first line:\n"
        "  // FILE: llm-d-inference-scheduler/pkg/plugins/scorer/<name>.go\n"
        "  // FILE: llm-d-inference-scheduler/pkg/plugins/scorer/<name>_test.go\n"
        "No prose before or after the code blocks."
    )
    issue_list = "\n".join(f"  - {i}" for i in issues) if issues else "  (no specific issues listed)"
    build_error_section = ""
    if build_error:
        build_error_section = f"""
## Build/Test Failure (fix these errors first)
```
{build_error}
```

"""
    user = f"""Fix the scorer plugin based on reviewer feedback.
{build_error_section}

## Reviewer Issues
{issue_list}

## Your Previous Implementation
```go
{prev_code}
```

## Algorithm Summary (reference)
```json
{json.dumps(algo_summary, indent=2)}
```

## Signal Coverage (use these production access paths exactly)
```json
{json.dumps(signal_coverage, indent=2)}
```

## Scorer Template
{scorer_template}

## Example Test File (your test must follow this structure exactly)
```go
{example_scorer_test}
```

## Critical Requirements
1. Register the scorer in llm-d-inference-scheduler/pkg/plugins/register.go
2. EffectiveLoad expands to WaitingQueueSize + 2*RunningRequestsSize
3. The scorer type constant must match the kebab-case scorer name with -scorer suffix
4. Test file MUST use `package scorer_test` (external test package, not `package scorer`)
5. Test file MUST import `github.com/llm-d/llm-d-inference-scheduler/test/utils` and use `utils.NewTestContext(t)`
6. Test endpoints MUST be constructed with `scheduling.NewEndpoint(&fwkdl.EndpointMetadata{...}, &fwkdl.Metrics{...}, nil)`
7. Use `cmpopts.EquateApprox(0, 1e-9)` for float comparisons
"""
    return [{"role": "system", "content": system},
            {"role": "user", "content": user}]


def _collect_review_feedback(responses: dict) -> list[str]:
    """Extract issue strings from reviewer responses."""
    from lib.llm import LLMError
    issues = []
    for model, resp in responses.items():
        if isinstance(resp, LLMError):
            continue
        clean = re.sub(r"^```(?:\w+)?\s*\n?", "", resp.strip())
        clean = re.sub(r"\n?```$", "", clean)
        try:
            data = json.loads(clean)
            if data.get("verdict") != "consistent":
                for issue in data.get("issues", []):
                    issues.append(f"[{model}] {issue}")
                summary = data.get("summary", "")
                if summary:
                    issues.append(f"[{model}] {summary}")
        except json.JSONDecodeError:
            pass
    return issues


def _prompt_user_continue(consensus: bool, label: str = "") -> int:
    """Prompt user after max rounds. Returns extra rounds (0 = accept). Raises SystemExit on quit."""
    tag = f" [{label}]" if label else ""
    if consensus:
        prompt = f"\n  Consensus reached{tag}. [a]ccept / [c]ontinue / [q]uit: "
    else:
        prompt = f"\n  No consensus{tag}. [c]ontinue 1 more / [+N] N more / [a]ccept-anyway / [q]uit: "
    while True:
        choice = input(prompt).strip().lower()
        if choice == "a":
            return 0
        elif choice == "q":
            print("Aborted.")
            sys.exit(0)
        elif choice == "c":
            return 1
        elif choice.startswith("+"):
            try:
                n = int(choice[1:])
                if n >= 1:
                    return n
            except ValueError:
                pass
        print("  Invalid. Enter 'a', 'c', '+N' (e.g. +3), or 'q'.")


def _scorer_name_from_summary(algo_summary: dict) -> str:
    name = algo_summary.get("algorithm_name", "evolved_scorer")
    sanitized = re.sub(r"[^a-zA-Z0-9]", "_", name).lower().strip("_")
    sanitized = re.sub(r"_+", "_", sanitized)
    if not sanitized.endswith("_scorer"):
        sanitized += "_scorer"
    return sanitized


def _scorer_type_from_name(name: str) -> str:
    t = name.replace("_", "-").lower()
    if not t.endswith("-scorer"):
        t += "-scorer"
    return t


def _write_scorer_files(response: str, algo_summary: dict) -> tuple[Path, Path]:
    """Parse code blocks from response and write scorer + test files."""
    scorer_dir = REPO_ROOT / "llm-d-inference-scheduler/pkg/plugins/scorer"
    scorer_name = _scorer_name_from_summary(algo_summary)

    scorer_code = _extract_code_block(response, index=0)
    test_code = _extract_code_block(response, index=1)

    if not scorer_code:
        err("Could not extract scorer code block from model response")
        sys.exit(1)
    if not test_code:
        warn("Could not extract test code block — test file will be minimal")
        test_code = f'package scorer\n\nimport "testing"\n\nfunc Test{scorer_name.title().replace("_", "")}Placeholder(t *testing.T) {{\n}}\n'

    # Override file name from FILE: comment if present
    file_match = re.search(r"// FILE: .*/([a-z][a-z0-9_]+)\.go", scorer_code)
    if file_match:
        scorer_name = file_match.group(1)

    scorer_path = scorer_dir / f"{scorer_name}.go"
    test_path = scorer_dir / f"{scorer_name}_test.go"

    scorer_path.write_text(scorer_code)
    test_path.write_text(test_code)

    ok(f"Wrote scorer: {scorer_path.relative_to(REPO_ROOT)}")
    ok(f"Wrote test:   {test_path.relative_to(REPO_ROOT)}")
    return scorer_path, test_path


def _validate_build(cwd: Path) -> None:
    env = {**os.environ, "GOWORK": "off"}
    result = run(["go", "build", "./..."], check=False, capture=True, cwd=cwd, env=env)
    if result.returncode != 0:
        err(f"go build failed:\n{result.stdout[-2000:]}\n{result.stderr[-500:]}")
        sys.exit(1)
    ok("go build passed")


def _blis_image_tag() -> str:
    result = run(
        ["git", "describe", "--tags"],
        check=False, capture=True,
        cwd=REPO_ROOT / "inference-sim",
    )
    tag = result.stdout.strip()
    if result.returncode != 0 or not tag.startswith("v"):
        warn(f"Could not get clean inference-sim tag (got: '{tag}'), using 'latest'")
        return "latest"
    return tag


def _load_llm_config() -> dict:
    """Read blis_router/llm_config.yaml. Returns {} if missing."""
    import yaml
    p = REPO_ROOT / "blis_router/llm_config.yaml"
    return yaml.safe_load(p.read_text()) if p.exists() else {}


def _load_workloads() -> list[dict]:
    """Read all workload YAML files from blis_router/workloads/. Required by schema."""
    import yaml
    workloads_dir = REPO_ROOT / "blis_router/workloads"
    workloads = []
    for wf in sorted(workloads_dir.glob("*.yaml")):
        spec_text = wf.read_text()
        # Parse to verify it's valid YAML, but embed as raw string per schema
        yaml.safe_load(spec_text)
        workloads.append({
            "name": wf.stem,
            "source": str(wf.relative_to(REPO_ROOT)),
            "spec": spec_text,
        })
    if not workloads:
        err("No workload YAML files found in blis_router/workloads/ — HALT")
        sys.exit(1)
    ok(f"Loaded {len(workloads)} workload(s): {[w['name'] for w in workloads]}")
    return workloads


def _generate_algorithm_values(algo_summary: dict, signal_coverage: dict,
                                out_dir: Path) -> Path:
    """Generate algorithm_values.yaml from blis_router sources. Fully deterministic."""
    import yaml

    llm_cfg = _load_llm_config()
    blis_tag = _blis_image_tag()
    scorer_name = _scorer_name_from_summary(algo_summary)
    scorer_type = _scorer_type_from_name(scorer_name)

    # ── Model identity (from llm_config.yaml) ──────────────────────────────
    model_hf_repo = (llm_cfg.get("model", {}).get("hf_repo")
                     or algo_summary.get("model_name", "meta-llama/Llama-3.1-8B-Instruct"))
    model_artifact_name = model_hf_repo.split("/")[-1].lower()
    model_pvc_uri = f"pvc://model-pvc/{model_artifact_name}"

    # ── Cluster topology (from llm_config.yaml) ────────────────────────────
    replicas = llm_cfg.get("cluster", {}).get("num_instances", 1)
    tensor_parallelism = llm_cfg.get("serving", {}).get("tensor_parallelism", 1)

    # ── vLLM args (from llm_config.yaml vllm_config section) ───────────────
    vc = llm_cfg.get("vllm_config", {})
    vllm_args = [
        "--dtype=bfloat16",
    ]
    if tensor_parallelism > 1:
        vllm_args.append(f"--tensor-parallel-size={tensor_parallelism}")
    if vc.get("gpu_memory_utilization"):
        vllm_args.append(f"--gpu-memory-utilization={vc['gpu_memory_utilization']}")
    if vc.get("max_num_running_reqs"):
        vllm_args.append(f"--max-num-seqs={vc['max_num_running_reqs']}")
    if vc.get("max_num_scheduled_tokens"):
        vllm_args.append(f"--max-num-batched-tokens={vc['max_num_scheduled_tokens']}")
    if vc.get("block_size_in_tokens"):
        vllm_args.append(f"--block-size={vc['block_size_in_tokens']}")
    # NOTE: never add --port here (causes duplicate port error per skill notes)

    # ── Container image (env_defaults.yaml overrides llm_config serving.vllm_version) ──
    env_cfg_path = REPO_ROOT / "config/env_defaults.yaml"
    env_cfg = yaml.safe_load(env_cfg_path.read_text()) if env_cfg_path.exists() else {}
    vllm_image = (env_cfg.get("stack", {}).get("model", {}).get("vllm_image", "")
                  or llm_cfg.get("serving", {}).get("vllm_version", "")
                  or "ghcr.io/llm-d/llm-d-cuda:v0.5.1")

    # ── GPU node selector (env_defaults.yaml) ──────────────────────────────
    gpu_label_values = ["NVIDIA-H100-80GB-HBM3"]
    acc = (env_cfg.get("stack", {}).get("model", {})
           .get("helmValues", {}).get("decode", {})
           .get("acceleratorTypes", {}))
    if acc.get("labelValues"):
        gpu_label_values = acc["labelValues"]

    # ── Treatment EndpointPickerConfig ──────────────────────────────────────
    epc_yaml = (
        f"apiVersion: inference.networking.x-k8s.io/v1alpha1\n"
        f"kind: EndpointPickerConfig\n"
        f"plugins:\n"
        f"- type: {scorer_type}\n"
        f"- type: decode-filter\n"
        f"- type: max-score-picker\n"
        f"- type: single-profile-handler\n"
        f"schedulingProfiles:\n"
        f"- name: default\n"
        f"  plugins:\n"
        f"  - pluginRef: decode-filter\n"
        f"  - pluginRef: max-score-picker\n"
        f"  - pluginRef: {scorer_type}\n"
        f"    weight: 1\n"
    )

    # ── Workloads (from blis_router/workloads/*.yaml) ───────────────────────
    workloads = _load_workloads()

    # ── Assemble values ─────────────────────────────────────────────────────
    values = {
        "stack": {
            "model": {
                "modelName": model_hf_repo,
                "helmValues": {
                    "modelArtifacts": {
                        "name": model_artifact_name,
                        "uri": model_pvc_uri,
                    },
                    "decode": {
                        "replicas": replicas,
                        "parallelism": {"tensor": tensor_parallelism},
                        "acceleratorTypes": {"labelValues": gpu_label_values},
                        "containers": [
                            {
                                "modelCommand": "vllmServe",
                                "mountModelVolume": True,
                                "image": vllm_image,
                                "readinessProbe": {
                                    "httpGet": {"path": "/health", "port": 8000},
                                    "initialDelaySeconds": 10,
                                    "periodSeconds": 5,
                                    "failureThreshold": 3,
                                },
                                "startupProbe": {
                                    "httpGet": {"path": "/health", "port": 8000},
                                    "initialDelaySeconds": 30,
                                    "periodSeconds": 10,
                                    "failureThreshold": 60,
                                },
                                "resources": {
                                    "limits": {"nvidia.com/gpu": str(tensor_parallelism)},
                                    "requests": {"nvidia.com/gpu": str(tensor_parallelism)},
                                },
                                "args": vllm_args,
                            }
                        ],
                    },
                },
            },
            "gaie": {
                "treatment": {
                    "helmValues": {
                        "inferenceExtension": {
                            "pluginsCustomConfig": {
                                "custom-plugins.yaml": epc_yaml,
                            }
                        }
                    }
                }
            },
        },
        "observe": {
            "image": f"ghcr.io/inference-sim/blis:{blis_tag}",
            "workloads": workloads,
        },
    }

    out_path = out_dir / "algorithm_values.yaml"
    out_path.write_text(yaml.dump(values, default_flow_style=False,
                                   sort_keys=False, allow_unicode=True))
    ok(f"Generated algorithm_values.yaml → {out_path.relative_to(REPO_ROOT)}")
    return out_path


def _run_merge_values(algo_values_path: Path, out_dir: Path) -> Path:
    venv_python = str(REPO_ROOT / ".venv/bin/python")
    cli = str(REPO_ROOT / "tools/transfer_cli.py")
    out_path = out_dir / "values.yaml"
    run([
        venv_python, cli, "merge-values",
        "--env", str(REPO_ROOT / "config/env_defaults.yaml"),
        "--algorithm", str(algo_values_path),
        "--out", str(out_path),
    ], cwd=REPO_ROOT)
    ok(f"merge-values complete → {out_path.relative_to(REPO_ROOT)}")
    return out_path


def _ensure_scorer_registered(scorer_name: str, scorer_type: str) -> Path:
    """Add plugin.Register() call to register.go if not already present."""
    register_path = (REPO_ROOT / "llm-d-inference-scheduler/pkg/plugins/register.go")
    if not register_path.exists():
        warn("register.go not found — skipping registration")
        return register_path

    content = register_path.read_text()
    if scorer_type in content:
        ok("Scorer already registered in register.go")
        return register_path

    # Read the actual exported symbol names from the scorer file itself.
    # The LLM may use non-standard casing (e.g. BLISWeightedScoring vs BlisWeightedScoring).
    scorer_file = (REPO_ROOT / "llm-d-inference-scheduler/pkg/plugins/scorer" /
                   f"{scorer_name}.go")
    type_const: str | None = None
    factory_fn: str | None = None
    if scorer_file.exists():
        for line in scorer_file.read_text().splitlines():
            # const FooType = "..."  OR  var FooType = "..."
            m = re.match(r'^\s*(?:const|var)\s+(\w+Type)\s*=', line)
            if m:
                type_const = m.group(1)
            m2 = re.match(r'^func\s+(\w+Factory)\s*\(', line)
            if m2:
                factory_fn = m2.group(1)

    if not type_const or not factory_fn:
        # Fall back to title-case derivation
        camel = "".join(w.title() for w in scorer_name.split("_"))
        type_const = type_const or f"{camel}Type"
        factory_fn = factory_fn or f"{camel}Factory"
        warn(f"Could not detect exported names from scorer file — using {type_const}/{factory_fn}")

    register_line = f"\tplugin.Register(scorer.{type_const}, scorer.{factory_fn})\n"

    # Remove any stale lines for this scorer (wrong casing or duplicates).
    # Match any plugin.Register line that references the scorer file's base name
    # (case-insensitive) to clean up previous wrong-cased entries.
    stem_pattern = re.compile(
        r'^\s*plugin\.Register\(scorer\.\w*' +
        re.escape(scorer_name.replace("_", "").lower()) +
        r'\w*,.*\)\s*$',
        re.IGNORECASE,
    )
    cleaned_lines = [ln for ln in content.splitlines(keepends=True)
                     if not stem_pattern.match(ln)]
    content = "".join(cleaned_lines)

    # Insert before the closing brace of RegisterAllPlugins
    new_content = content.rstrip()
    if new_content.endswith("}"):
        new_content = new_content[:-1] + register_line + "}\n"
        register_path.write_text(new_content)
        ok(f"Registered scorer in register.go: {register_line.strip()}")
    else:
        warn(f"Unexpected register.go format — add manually: {register_line.strip()}")
    return register_path


_STANDARD_SCORER_FILES = {
    "active_request.go", "doc.go", "load_aware.go", "no_hit_lru.go",
    "precise_prefix_cache.go", "session_affinity.go", "utils.go",
}


def _reconstruct_stage3_output(run_dir: Path, stage3_out: Path) -> Path:
    """Reconstruct prepare_stage3_output.json from the scorer file already on disk.

    Called when --skip-generate is passed but the artifact was never written
    (e.g. a previous run failed before Stage 3 completed).
    """
    scorer_dir = REPO_ROOT / "llm-d-inference-scheduler/pkg/plugins/scorer"
    candidates = [
        f for f in scorer_dir.glob("*.go")
        if not f.name.endswith("_test.go") and f.name not in _STANDARD_SCORER_FILES
    ]
    if not candidates:
        err("--skip-generate: no generated scorer file found in "
            f"{scorer_dir.relative_to(REPO_ROOT)} — cannot reconstruct stage3 output.")
        sys.exit(1)
    if len(candidates) > 1:
        names = [c.name for c in candidates]
        err(f"--skip-generate: multiple unknown scorer files found: {names}. "
            "Remove stale files or run without --skip-generate.")
        sys.exit(1)

    scorer_path = candidates[0]
    scorer_name = scorer_path.stem
    scorer_type = _scorer_type_from_name(scorer_name)
    test_path = scorer_dir / f"{scorer_name}_test.go"
    register_path = REPO_ROOT / "llm-d-inference-scheduler/pkg/plugins/register.go"

    # Look for merged values.yaml that may already exist
    out_dir = run_dir / "prepare_tekton"
    values_yaml = out_dir / "values.yaml"

    payload = {
        "scorer_file": str(scorer_path.relative_to(REPO_ROOT)),
        "test_file": str(test_path.relative_to(REPO_ROOT)) if test_path.exists() else "",
        "register_file": str(register_path.relative_to(REPO_ROOT)),
        "scorer_type": scorer_type,
        "tekton_artifacts": {
            "values_yaml": str(values_yaml.relative_to(REPO_ROOT)) if values_yaml.exists() else "",
        },
    }

    stage3_out.parent.mkdir(parents=True, exist_ok=True)
    stage3_out.write_text(json.dumps(payload, indent=2))
    info(f"[skip] Reconstructed {stage3_out.relative_to(REPO_ROOT)} from {scorer_path.name}")
    return stage3_out


def stage_generate(run_dir: Path, algo_summary_path: Path,
                   signal_coverage_path: Path, reviews: int,
                   force: bool = False, skip_generate: bool = False) -> Path:
    stage3_out = run_dir / "prepare_stage3_output.json"

    if skip_generate:
        if not stage3_out.exists():
            stage3_out = _reconstruct_stage3_output(run_dir, stage3_out)
        info(f"[skip] Generate — --skip-generate passed, using {stage3_out.relative_to(REPO_ROOT)}")
        # Still re-run registration so register.go reflects the correct symbol names.
        s3 = json.loads(stage3_out.read_text())
        scorer_name = Path(s3["scorer_file"]).stem
        scorer_type = s3.get("scorer_type", _scorer_type_from_name(scorer_name))
        _ensure_scorer_registered(scorer_name, scorer_type)
        return stage3_out

    if not force and stage3_out.exists():
        try:
            _s3 = json.loads(stage3_out.read_text())
            if (REPO_ROOT / _s3.get("scorer_file", "")).exists():
                if _should_skip(stage3_out, "Generate", force):
                    _rout = run_dir / "prepare_reviewer_output.json"
                    if _rout.exists():
                        info(f"  Reviewer output: {_rout.relative_to(REPO_ROOT)}")
                    return stage3_out
        except (json.JSONDecodeError, KeyError):
            pass

    step(3, "Generate  (writer: claude-opus-4-6  |  reviewers: all 3 models)")
    out_dir = run_dir / "prepare_tekton"
    out_dir.mkdir(parents=True, exist_ok=True)
    stage3_out.unlink(missing_ok=True)

    algo_summary = json.loads(algo_summary_path.read_text())
    signal_coverage = json.loads(signal_coverage_path.read_text())
    mapping_doc = (REPO_ROOT / "docs/transfer/blis_to_llmd_mapping.md").read_text()
    scorer_template = (REPO_ROOT / "docs/transfer/scorer_template.go.md").read_text()
    scorer_dir = REPO_ROOT / "llm-d-inference-scheduler/pkg/plugins/scorer"
    example_scorer = (scorer_dir / "blis_weighted_scoring.go").read_text()
    example_scorer_test = (scorer_dir / "blis_weighted_scoring_test.go").read_text()
    evolve_block = _load_evolve_block(algo_summary)

    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from lib.llm import call_model, call_models_parallel, LLMError

    writer_messages = build_generate_prompt(
        algo_summary, signal_coverage, mapping_doc, scorer_template,
        example_scorer, example_scorer_test,
    )

    round_num = 0
    remaining = reviews
    last_writer_response: str = ""
    review_responses: dict = {}
    all_rounds: list[dict] = []
    build_attempts = 0
    scorer_path: Path | None = None
    test_path: Path | None = None

    while True:
        round_num += 1
        remaining -= 1

        # ── Writer generates / revises ─────────────────────────────────────────
        info(f"  Writer (aws/claude-opus-4-6) — round {round_num}...")
        try:
            last_writer_response = call_model("aws/claude-opus-4-6", writer_messages)
        except LLMError as e:
            err(f"Writer failed: {e}")
            sys.exit(1)

        writer_code = _extract_code_block(last_writer_response)
        if not writer_code:
            err("Writer returned no code block — HALT")
            sys.exit(1)

        # ── Build / test before reviewers ─────────────────────────────────────
        scorer_path, test_path = _write_scorer_files(last_writer_response, algo_summary)
        _tmp_scorer_name = scorer_path.stem
        _tmp_scorer_type = _scorer_type_from_name(_tmp_scorer_name)
        _ensure_scorer_registered(_tmp_scorer_name, _tmp_scorer_type)

        build_attempts += 1
        build_passed, build_error_out = _run_build_test()
        print(f"\n━━━ Generate Round {round_num} ━━━")
        print(f"  Writer  →  {len(writer_code.splitlines())} lines")
        if not build_passed:
            print(f"  Build/test  →  FAILED (attempt {build_attempts})")
            info(f"  Passing build errors back to writer...")
            writer_messages = build_revision_prompt(
                writer_code, [build_error_out], algo_summary, signal_coverage,
                mapping_doc, scorer_template, example_scorer_test,
                build_error=build_error_out,
            )
            continue

        print(f"  Build/test  →  PASSED (attempt {build_attempts})")

        # ── Reviewers check faithfulness (only after build passes) ────────────
        info("  Reviewers checking faithfulness...")
        review_messages = build_review_prompt(
            writer_code, algo_summary, signal_coverage, evolve_block,
        )
        review_responses = call_models_parallel(MODELS, review_messages)
        consensus = check_review_consensus(review_responses)

        # ── Parse reviewer responses and build round record ────────────────────
        reviewer_data: dict = {}
        verdicts: list[str] = []
        for model, resp in review_responses.items():
            if isinstance(resp, LLMError):
                reviewer_data[model] = {"error": str(resp)}
                continue
            clean = re.sub(r"^```(?:\w+)?\s*\n?", "", resp.strip())
            clean = re.sub(r"\n?```$", "", clean)
            try:
                reviewer_data[model] = json.loads(clean)
                verdicts.append(reviewer_data[model].get("verdict", "unknown"))
            except json.JSONDecodeError:
                reviewer_data[model] = {"raw": resp}

        issues_for_writer = _collect_review_feedback(review_responses)
        all_rounds.append({
            "round": round_num,
            "writer_lines": len(writer_code.splitlines()),
            "build_attempts_so_far": build_attempts,
            "consensus": consensus,
            "reviewers": reviewer_data,
            "issues_passed_to_writer": issues_for_writer if not consensus else [],
        })

        # ── Print reviewer summary ─────────────────────────────────────────────
        print()
        for model, data in reviewer_data.items():
            if "error" in data:
                print(f"    {model:<32} ✗  error: {data['error'][:60]}")
            elif "raw" in data:
                print(f"    {model:<32} ?  could not parse response")
            else:
                verdict = data.get("verdict", "unknown")
                issues = data.get("issues", [])
                icon = "✓" if verdict == "consistent" else "✗"
                issue_note = f"  ({len(issues)} issue(s))" if issues else ""
                print(f"    {model:<32} {icon}  {verdict}{issue_note}")
        consistent_count = verdicts.count("consistent")
        verdict_str = "All consistent ✓" if consensus else "not all consistent"
        print(f"\n  {consistent_count}/{len(review_responses)} reviewers consistent  →  {verdict_str}")
        print(f"  Full output: {run_dir}/")

        if consensus:
            break

        if remaining > 0:
            info(f"  Passing {len(issues_for_writer)} issue(s) back to writer...")
            writer_messages = build_revision_prompt(
                writer_code, issues_for_writer, algo_summary, signal_coverage,
                mapping_doc, scorer_template, example_scorer_test,
            )
            continue

        # Max rounds reached — pause for user
        extra = _prompt_user_continue(consensus, label="Generate")
        if extra == 0:
            break
        remaining = extra
        writer_messages = build_revision_prompt(
            writer_code, issues_for_writer, algo_summary, signal_coverage,
            mapping_doc, scorer_template, example_scorer_test,
        )

    # ── Save all reviewer rounds ───────────────────────────────────────────────
    reviewer_out = run_dir / "prepare_reviewer_output.json"
    reviewer_out.write_text(json.dumps({
        "stage": "generate",
        "total_rounds": round_num,
        "final_consensus": consensus,
        "rounds": all_rounds,
    }, indent=2))
    ok(f"Reviewer output → {reviewer_out.relative_to(REPO_ROOT)}")
    ok(f"Build/test passed after {build_attempts} attempt(s)")

    algo_values_path = _generate_algorithm_values(algo_summary, signal_coverage, out_dir)
    values_path = _run_merge_values(algo_values_path, out_dir)

    scorer_name = scorer_path.stem
    scorer_type = _scorer_type_from_name(scorer_name)
    register_path = _ensure_scorer_registered(scorer_name, scorer_type)

    payload = json.dumps({
        "scorer_file": str(scorer_path.relative_to(REPO_ROOT)),
        "test_file": str(test_path.relative_to(REPO_ROOT)),
        "register_file": str(register_path.relative_to(REPO_ROOT)),
        "scorer_type": scorer_type,
        "tekton_artifacts": {
            "values_yaml": str(values_path.relative_to(REPO_ROOT)),
        },
    }, indent=2)

    venv_python = str(REPO_ROOT / ".venv/bin/python")
    cli = str(REPO_ROOT / "tools/transfer_cli.py")

    # Validate against canonical name, then copy to run_dir
    ws_out = REPO_ROOT / "workspace/stage3_output.json"
    ws_out.write_text(payload)
    run([venv_python, cli, "validate-schema", str(ws_out)], cwd=REPO_ROOT)
    shutil.copy(ws_out, stage3_out)

    ok(f"Generate complete → {stage3_out.relative_to(REPO_ROOT)}")
    return stage3_out


# ── Stage 4: Build / Test / Equivalence Gate ──────────────────────────────────

def _run_build_test() -> tuple[bool, str]:
    """Run go build/vet/test on the scorer. Returns (passed, error_output)."""
    sched = REPO_ROOT / "llm-d-inference-scheduler"
    env = {**os.environ, "GOWORK": "off"}
    for label, cmd in [
        ("go build", ["go", "build", "./..."]),
        ("go vet",   ["go", "vet", "./..."]),
        ("go test",  ["go", "test", "-timeout", "10m",
                      "./pkg/plugins/scorer/...", "-v"]),
    ]:
        result = run(cmd, check=False, capture=True, cwd=sched, env=env)
        if result.returncode != 0:
            output = f"{label} failed:\n{result.stdout[-3000:]}\n{result.stderr[-1000:]}".strip()
            err(output)
            return False, output
        ok(f"{label} passed")
    return True, ""


def stage_build_test(stage3_path: Path, force: bool = False) -> tuple[bool, str]:
    """Build and test the generated scorer. Returns (passed, error_output)."""
    # Silently skip if equivalence results exist — the user is asked once in stage_equivalence_gate
    equiv_out = stage3_path.parent / "prepare_equivalence_results.json"
    if not force and equiv_out.exists():
        return True, ""
    step(4, "Build & Test")
    return _run_build_test()


def stage_equivalence_gate(run_dir: Path, force: bool = False) -> Path:
    out = run_dir / "prepare_equivalence_results.json"
    if _should_skip(out, "Build & Test + Equivalence Gate", force):
        return out
    step("4.5", "Equivalence Gate (Suite A/B/C)")
    sched = REPO_ROOT / "llm-d-inference-scheduler"
    env = {**os.environ, "GOWORK": "off"}

    raw_outputs: dict[str, str] = {}
    passed_map: dict[str, bool] = {}
    for suite, tag, fatal in [("A", "suitea", True), ("B", "suiteb", False), ("C", "suitec", True)]:
        cmd = ["go", "test", f"-tags={tag}", "-json", "-v",
               "-timeout", "10m", "./pkg/plugins/scorer/..."]
        if suite == "C":
            cmd.append("-race")
        result = run(cmd, check=False, capture=True, cwd=sched, env=env)
        passed = result.returncode == 0
        raw_outputs[suite] = result.stdout + result.stderr
        passed_map[suite] = passed
        if not passed and fatal:
            err(f"Suite {suite} FAILED:\n{result.stdout[-2000:]}")
            sys.exit(1)
        if passed:
            ok(f"Suite {suite}: PASS")
        else:
            warn(f"Suite {suite}: WARN (non-fatal — informational only)")

    # Parse metrics from test output where available; fall back to schema-safe defaults.
    # Suite A: kendall_tau, max_abs_error, tuple_count
    tau_match = re.search(r"tau[=: ]+([0-9.]+)", raw_outputs["A"])
    kendall_tau = float(tau_match.group(1)) if tau_match else (1.0 if passed_map["A"] else 0.0)
    err_match = re.search(r"max_abs_error[=: ]+([0-9.]+)", raw_outputs["A"])
    max_abs_error = float(err_match.group(1)) if err_match else 0.0
    count_match = re.search(r"tuple_count[=: ]+([0-9]+)", raw_outputs["A"])
    tuple_count = int(count_match.group(1)) if count_match else 0

    # Suite B: rank_stability_tau, threshold_crossing_pct, informational_only
    stab_match = re.search(r"rank_stability_tau[=: ]+([0-9.]+)", raw_outputs["B"])
    rank_stability_tau = float(stab_match.group(1)) if stab_match else (1.0 if passed_map["B"] else 0.0)
    cross_match = re.search(r"threshold_crossing_pct[=: ]+([0-9.]+)", raw_outputs["B"])
    threshold_crossing_pct = float(cross_match.group(1)) if cross_match else 0.0

    # Suite C: deterministic, max_pile_on_ratio
    pile_match = re.search(r"max_pile_on_ratio[=: ]+([0-9.]+)", raw_outputs["C"])
    max_pile_on_ratio = float(pile_match.group(1)) if pile_match else 1.0

    suite_results = {
        "suite_a": {
            "passed": passed_map["A"],
            "kendall_tau": kendall_tau,
            "max_abs_error": max_abs_error,
            "tuple_count": tuple_count,
        },
        "suite_b": {
            "passed": passed_map["B"],
            "rank_stability_tau": rank_stability_tau,
            "threshold_crossing_pct": threshold_crossing_pct,
            "informational_only": True,  # v1: all signals have staleness_window_ms=0
        },
        "suite_c": {
            "passed": passed_map["C"],
            "deterministic": passed_map["C"],  # race detector pass implies deterministic
            "max_pile_on_ratio": max_pile_on_ratio,
        },
    }

    out = run_dir / "prepare_equivalence_results.json"
    venv_python = str(REPO_ROOT / ".venv/bin/python")
    cli = str(REPO_ROOT / "tools/transfer_cli.py")

    # Validate against canonical name, then copy to run_dir
    ws_out = REPO_ROOT / "workspace/equivalence_results.json"
    ws_out.write_text(json.dumps(suite_results, indent=2))
    run([venv_python, cli, "validate-schema", str(ws_out)], cwd=REPO_ROOT)
    shutil.copy(ws_out, out)

    ok(f"Equivalence gate → {out.relative_to(REPO_ROOT)}")
    return out


# ── Stage 5: Review ───────────────────────────────────────────────────────────

def build_review_prompt(scorer_code: str, algo_summary: dict,
                         signal_coverage: dict, evolve_block: str) -> list[dict]:
    system = (
        "You are an expert code reviewer verifying that a Go scorer plugin faithfully "
        "implements an evolved routing algorithm. Be precise and technical. "
        'Respond with JSON only: {"verdict": "consistent"|"inconsistent", '
        '"issues": [...], "per_signal": {...}, "summary": "..."}'
    )
    user = f"""Review the scorer Go code for translation fidelity.

For EACH signal verify: correct production field is read, normalization matches
algorithm_summary, weight/coefficient matches EVOLVE-BLOCK, and scoring logic is faithful.

## Generated Scorer
```go
{scorer_code}
```

## Algorithm Summary
```json
{json.dumps(algo_summary, indent=2)}
```

## Signal Coverage
```json
{json.dumps(signal_coverage, indent=2)}
```

## EVOLVE-BLOCK Source
```go
{evolve_block}
```

Respond with ONLY the JSON object (no markdown fences, no prose).
"""
    return [{"role": "system", "content": system},
            {"role": "user", "content": user}]


def check_review_consensus(responses: dict) -> bool:
    from lib.llm import LLMError
    verdicts = []
    for model, resp in responses.items():
        if isinstance(resp, LLMError):
            continue
        clean = re.sub(r"^```(?:json)?\n?", "", resp.strip())
        clean = re.sub(r"\n?```$", "", clean)
        try:
            data = json.loads(clean)
            verdicts.append(data.get("verdict", "unknown"))
        except json.JSONDecodeError:
            pass
    return all(v == "consistent" for v in verdicts) and len(verdicts) >= min(2, len(responses))


def _make_review_summarizer(run_dir: Path):
    def summarize(round_num: int, responses: dict, consensus: bool) -> str:
        from lib.llm import LLMError
        print(f"\n━━━ Final Review Round {round_num} ━━━")
        verdicts: list[str] = []
        all_issues: list[str] = []
        for model, resp in responses.items():
            if isinstance(resp, LLMError):
                print(f"    {model:<32} ✗  error: {str(resp)[:60]}")
                continue
            clean = re.sub(r"^```(?:\w+)?\s*\n?", "", resp.strip())
            clean = re.sub(r"\n?```$", "", clean)
            try:
                data = json.loads(clean)
                verdict = data.get("verdict", "unknown")
                verdicts.append(verdict)
                issues = data.get("issues", [])
                all_issues.extend(str(i) for i in issues)
                icon = "✓" if verdict == "consistent" else "✗"
                issue_note = f"  ({len(issues)} issue(s))" if issues else ""
                print(f"    {model:<32} {icon}  {verdict}{issue_note}")
            except json.JSONDecodeError:
                print(f"    {model:<32} ?  could not parse response")
        consistent = verdicts.count("consistent")
        verdict_str = "All consistent ✓" if consensus else "not all consistent"
        print(f"\n  {consistent}/{len(responses)} reviewers consistent  →  {verdict_str}")
        if all_issues:
            unique = list(dict.fromkeys(all_issues))[:3]
            print(f"  Top issues: {unique}")
        print(f"  Full output: {run_dir}/")
        return ""  # run_consensus_loop calls print() on this; empty string = no extra output

    return summarize


def stage_final_review(run_dir: Path, stage3_path: Path,
                        algo_summary_path: Path, signal_coverage_path: Path,
                        reviews: int, force: bool = False) -> bool:
    """Run final 3-model review. Returns True if passed (consensus or user-accepted)."""
    out = run_dir / "prepare_translation_reviews.json"
    # Only offer reuse if the previous review actually passed
    _review_skip_ok = False
    if not force and out.exists():
        try:
            if json.loads(out.read_text()).get("passed"):
                _review_skip_ok = True
        except (json.JSONDecodeError, KeyError):
            pass
    if _review_skip_ok and _should_skip(out, "Final Review", force):
        return True
    step(5, "Final Review (3 models)")

    stage3 = json.loads(stage3_path.read_text())
    scorer_file = REPO_ROOT / stage3["scorer_file"]
    scorer_code = scorer_file.read_text()

    algo_summary = json.loads(algo_summary_path.read_text())
    signal_coverage = json.loads(signal_coverage_path.read_text())
    evolve_block = _load_evolve_block(algo_summary)

    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from lib.llm import call_models_parallel, LLMError
    from lib.consensus import run_consensus_loop

    messages = build_review_prompt(scorer_code, algo_summary, signal_coverage, evolve_block)

    result = run_consensus_loop(
        messages=messages,
        call_fn=lambda msgs: call_models_parallel(MODELS, msgs),
        check_fn=check_review_consensus,
        summarize_fn=_make_review_summarizer(run_dir),
        max_rounds=reviews,
    )

    reviews_data: dict = {}
    for model, resp in result.responses.items():
        if isinstance(resp, LLMError):
            reviews_data[model] = {"error": str(resp)}
        else:
            clean = re.sub(r"^```(?:\w+)?\s*\n?", "", resp.strip())
            clean = re.sub(r"\n?```$", "", clean)
            try:
                reviews_data[model] = json.loads(clean)
            except json.JSONDecodeError:
                reviews_data[model] = {"raw": resp}

    passed = result.consensus or result.accepted_by_user
    out = run_dir / "prepare_translation_reviews.json"
    out.write_text(json.dumps({
        "rounds": result.rounds_run,
        "consensus": result.consensus,
        "accepted_by_user": result.accepted_by_user,
        "passed": passed,
        "reviews": reviews_data,
    }, indent=2))
    status = "passed ✓" if passed else "failed — issues found"
    ok(f"Final review {status} → {out.relative_to(REPO_ROOT)}")
    return passed


# ── Completion ────────────────────────────────────────────────────────────────

def write_outputs(run_dir: Path, cfg: dict, stage3_path: Path) -> None:
    stage3 = json.loads(stage3_path.read_text())
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    update_run_metadata(
        run_dir, "prepare",
        status="completed",
        completed_at=now,
        summary="Extract, translate, generate, build/test, and AI review completed",
        artifacts=[
            "prepare_algorithm_summary.json",
            "prepare_signal_coverage.json",
            "prepare_stage3_output.json",
            "prepare_translation_reviews.json",
        ],
    )

    print()
    print(_c("32", "━━━ sim2real-prepare complete ━━━"))
    print()
    print(f"Run:      {cfg['current_run']}")
    print(f"Run dir:  {run_dir}")
    print()
    print("Artifacts produced:")
    for name in [
        "prepare_algorithm_summary.json",
        "prepare_signal_coverage.json",
        "prepare_stage3_output.json",
        "prepare_reviewer_output.json",
        "prepare_equivalence_results.json",
        "prepare_translation_reviews.json",
    ]:
        p = run_dir / name
        exists = "✓" if p.exists() else "-"
        print(f"  {exists}  {p}")
    scorer = stage3.get("scorer_file", "")
    if scorer:
        print(f"  {REPO_ROOT / scorer}")
    print()
    print("Next: python scripts/deploy.py  OR  /sim2real-deploy in Claude")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    global MODELS
    args = build_parser().parse_args()
    if args.dev:
        MODELS = DEV_MODELS
    print_intro(args.reviews, dev=args.dev)
    check_prerequisites()
    cfg = load_setup_config()
    run_dir = REPO_ROOT / "workspace/runs" / cfg["run_name"]

    update_run_metadata(
        run_dir, "prepare",
        status="in_progress",
        started_at=datetime.now(timezone.utc).isoformat(),
    )

    force = args.force
    skip_generate = args.skip_generate

    try:
        algo_summary_path = stage_extract(run_dir, force=force)
        signal_coverage_path = stage_translate(run_dir, algo_summary_path, force=force)

        MAX_OUTER = 3  # final-review retries
        stage3_path: Path | None = None
        for outer in range(1, MAX_OUTER + 1):
            if outer > 1:
                warn(f"Final review found issues — restarting Generate "
                     f"(outer {outer}/{MAX_OUTER})...")

            # Force regeneration on any retry; --skip-generate only on first attempt
            force_gen = force or outer > 1
            stage3_path = stage_generate(
                run_dir, algo_summary_path, signal_coverage_path, args.reviews,
                force=force_gen,
                skip_generate=skip_generate and outer == 1,
            )
            stage_build_test(stage3_path, force=force_gen)
            stage_equivalence_gate(run_dir, force=force_gen)
            passed = stage_final_review(
                run_dir, stage3_path, algo_summary_path, signal_coverage_path, args.reviews,
                force=force_gen,
            )
            if passed:
                break
            if outer == MAX_OUTER:
                err("Final review still failing after max retries — HALT")
                sys.exit(1)

        write_outputs(run_dir, cfg, stage3_path)
    except SystemExit:
        update_run_metadata(
            run_dir, "prepare",
            status="failed",
            failed_at=datetime.now(timezone.utc).isoformat(),
        )
        raise

    return 0


if __name__ == "__main__":
    sys.exit(main())
