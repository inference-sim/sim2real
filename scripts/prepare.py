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
        help="Skip the Generate stage if the plugin file is already present on disk. "
             "Useful to resume after a manual edit or a partial run.",
    )
    p.add_argument(
        "--dev", action="store_true",
        help="Dev mode: use only 1 reviewer (aws/claude-opus-4-6) instead of 3. "
             "Faster iteration; not suitable for production runs.",
    )
    p.add_argument(
        "--no-gate", action="store_true",
        help="Skip human review gates (extract & translate). "
             "AI review still runs; useful for CI or unattended runs.",
    )
    p.add_argument(
        "--manifest", type=Path, default=REPO_ROOT / "config/transfer.yaml",
        help="Path to transfer.yaml manifest (default: config/transfer.yaml)",
    )
    return p


# ── Intro banner ──────────────────────────────────────────────────────────────

def print_intro(reviews: int, dev: bool = False, no_gate: bool = False) -> None:
    print(_c("36", "\n━━━ sim2real-prepare ━━━\n"))
    if dev:
        print(_c("33", "  [DEV MODE] 1 reviewer (aws/claude-opus-4-6) — not for production\n"))
    if no_gate:
        print(_c("33", "  [NO-GATE] Human review gates disabled — AI review only\n"))
    print("Stages: Extract → Translate → Generate → Build/Test → Final Review\n")
    reviewer_note = "1 model (dev)" if dev else "all 3 models"
    print("LLM interactions:")
    print("  1. Extract       claude -p     full-file + cross-file context extraction")
    print(f"                   AI review     {reviewer_note} check completeness")
    if not no_gate:
        print("                   human gate    [e]dit / [c]hat / [d]one / [q]uit")
    print("  2. Translate     single call   1 model maps signals to production equivalents")
    print("                   claude -p     enhanced translation (types, helpers)")
    print(f"                   AI review     {reviewer_note} check coverage")
    if not no_gate:
        print("                   human gate    [e]dit / [c]hat / [d]one / [q]uit")
    print(f"  3. Generate      writer loop   claude -p writes (with codebase access);")
    print(f"                                 {reviewer_note} review via claude -p + review script;")
    print("                                 issues fed back to writer until consistent")
    print("  4. Final Review  review loop   claude -p invokes all 3 models for final check;")
    print("                                 if issues found, returns to Generate")
    print(f"\n--reviews {reviews}: each loop runs up to {reviews} round(s), then pauses for your input.")
    print("At each pause: [c]ontinue / [+N] N more rounds / [a]ccept / [q]uit")
    print("If artifacts from a previous run exist, you will be asked whether to reuse them.")
    print("Pass --force to regenerate everything without being asked.\n")


# ── Prerequisites ─────────────────────────────────────────────────────────────

def check_prerequisites(manifest: dict) -> None:
    step(0, "Checking prerequisites")
    exp = REPO_ROOT / manifest["algorithm"]["experiment_dir"]
    required_files = [
        exp / manifest["algorithm"]["source"],
        exp / manifest["algorithm"]["info"],
        # mapping doc intentionally excluded — gate handles creation
        REPO_ROOT / manifest["context"]["template"],
        REPO_ROOT / "workspace/setup_config.json",
    ]
    required_dirs = [
        REPO_ROOT / "inference-sim/sim",
        REPO_ROOT / manifest["target"]["repo"] / "pkg",
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
    current_run = cfg["current_run"]
    run_dir = REPO_ROOT / "workspace/runs" / current_run
    run_dir.mkdir(parents=True, exist_ok=True)
    ok(f"Run: {current_run}  ({run_dir})")
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

MAX_GATE_LOOPS = 5


def stage_extract(run_dir: Path, manifest: dict, force: bool = False, no_gate: bool = False) -> Path:
    out = run_dir / "prepare_algorithm_summary.json"
    if _should_skip(out, "Extract", force):
        return out
    step(1, "Extract")
    out.unlink(missing_ok=True)

    venv_python = str(REPO_ROOT / ".venv/bin/python")
    cli = str(REPO_ROOT / "tools/transfer_cli.py")
    exp = REPO_ROOT / manifest["algorithm"]["experiment_dir"]
    extract_dir = str((exp / manifest["algorithm"]["source"]).parent) + "/"

    # Phase 1: Base extraction via transfer_cli
    result = run(
        [venv_python, cli, "extract", "--strict", extract_dir],
        check=False, capture=True, cwd=REPO_ROOT,
    )
    if result.returncode != 0:
        err(f"extract failed:\n{result.stderr}")
        sys.exit(1)

    ws_src = REPO_ROOT / "workspace/algorithm_summary.json"
    if not ws_src.exists():
        err("extract succeeded but workspace/algorithm_summary.json not found")
        sys.exit(1)

    run([venv_python, cli, "validate-schema", str(ws_src)], cwd=REPO_ROOT)

    scope_ok = run(
        [venv_python, "-c",
         f"import json,sys; d=json.load(open('{ws_src}')); "
         "sys.exit(0 if d.get('scope_validation_passed') is True else 1)"],
        check=False,
    ).returncode == 0
    if not scope_ok:
        err("scope_validation_passed is not true — HALT")
        sys.exit(1)

    # Move artifact into the run directory — all subsequent work happens here
    shutil.copy(ws_src, out)

    # Phase 2: Enhanced extraction via claude -p (full-file + cross-file context)
    source_file = exp / manifest["algorithm"]["source"]
    extract_prompt = (
        f"Read the prompt template at {REPO_ROOT / 'prompts/extract-full.md'}.\n"
        f"The algorithm source file is {source_file}.\n"
        f"The existing algorithm_summary.json is at {out}.\n"
        f"Follow the instructions in the prompt template to enhance the extraction.\n"
        f"Write the updated file back to {out}.\n"
    )
    info("Running enhanced extraction (full-file context)...")
    proc = _run_claude(extract_prompt, label="Enhanced extract", max_turns=15, timeout=600)
    if proc.returncode != 0:
        warn("Enhanced extraction failed — continuing with base extraction only")
    else:
        # Re-validate after enhancement
        val = run(
            [venv_python, cli, "validate-schema", str(out)],
            check=False, cwd=REPO_ROOT,
        )
        if val.returncode != 0:
            warn("Enhanced extraction produced invalid schema — reverting to base extraction")
            shutil.copy(ws_src, out)

    # Phase 3: AI review + human gate loop
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from lib.gates import review_artifacts, human_gate, ReviewResult

    review_prompt = (
        "Review this algorithm_summary.json for completeness. Check that:\n"
        "1. All signals referenced in the EVOLVE-BLOCK are captured\n"
        "2. Signal types and access paths are correct\n"
        "3. Cross-file dependencies (type_refs, helper_refs) are captured if present\n"
        "4. The evolve_block_source line range matches the actual markers\n"
        "5. unused_source_attributes captures relevant attributes from the source file"
        " that the EVOLVE-BLOCK does not reference\n"
    )

    for loop_i in range(1, MAX_GATE_LOOPS + 1):
        ai_review = review_artifacts(
            artifact_paths=[out],
            review_prompt=review_prompt,
            models=MODELS,
        )
        if ai_review.passed:
            info("AI review: PASSED")
        else:
            warn(f"AI review: issues found (loop {loop_i}/{MAX_GATE_LOOPS})")
            for line in ai_review.summary_lines():
                print(line)

        if no_gate:
            if not ai_review.passed and loop_i < MAX_GATE_LOOPS:
                warn("--no-gate: AI review failed but skipping human gate")
            break

        # Read gaps from artifact for display
        _artifact = json.loads(out.read_text())
        _extract_gaps = _artifact.get("unused_source_attributes")

        gate_result = human_gate(
            stage_name="Extract",
            artifact_paths=[out],
            ai_review=ai_review,
            context_for_chat=[source_file],
            repo_root=REPO_ROOT,
            gaps=_extract_gaps,
        )
        if not gate_result.modified:
            break
        # Human made edits — re-validate and loop
        val = run(
            [venv_python, cli, "validate-schema", str(out)],
            check=False, cwd=REPO_ROOT,
        )
        if val.returncode != 0:
            err("Edited artifact fails schema validation — fix before continuing")
            continue
        info(f"Re-running AI review after edits (loop {loop_i}/{MAX_GATE_LOOPS})...")

    ok(f"Extract complete → {out.relative_to(REPO_ROOT)}")
    return out


# ── Stage 2: Translate ────────────────────────────────────────────────────────


def _write_full_context(
    full_context_path: Path,
    algo_summary_path: Path,
    signal_coverage_path: Path,
    manifest: dict,
) -> None:
    """Write prepare_full_context.md combining extraction + translation for downstream stages."""
    algo = json.loads(algo_summary_path.read_text())
    coverage = json.loads(signal_coverage_path.read_text())

    source_file = REPO_ROOT / manifest["algorithm"]["experiment_dir"] / manifest["algorithm"]["source"]
    evolve_src = algo.get("evolve_block_source", "")

    lines = [
        "# Prepare Full Context",
        "",
        "Combined extraction and translation context for Stage 3 (Generate) and beyond.",
        "",
        "## Algorithm Summary",
        "",
        f"- **Name**: {algo.get('algorithm_name', 'unknown')}",
        f"- **Source**: {evolve_src}",
        f"- **Content Hash**: {algo.get('evolve_block_content_hash', 'N/A')}",
        "",
        "### Signals",
        "",
    ]
    for sig in algo.get("signals", []):
        prov = " [PROVISIONAL]" if sig.get("fidelity_provisional") else ""
        norm = f" (normalize: {sig.get('normalization_note', '')})" if sig.get("normalization_note") else ""
        lines.append(f"- **{sig['name']}** ({sig.get('type', '?')}){prov}{norm}")

    if algo.get("composite_signals"):
        lines.append("")
        lines.append("### Composite Signals")
        lines.append("")
        for c in algo["composite_signals"]:
            lines.append(f"- **{c['name']}**: {c.get('formula', '')}({c.get('constituents', '')})")

    if algo.get("type_refs"):
        lines.append("")
        lines.append("### Type References")
        lines.append("")
        for t in algo["type_refs"]:
            fields = ", ".join(f"{f['name']}: {f['type']}" for f in t.get("fields", []))
            lines.append(f"- **{t['name']}** (`{t.get('file_path', '')}`): {fields}")

    if algo.get("helper_refs"):
        lines.append("")
        lines.append("### Helper Functions")
        lines.append("")
        for h in algo["helper_refs"]:
            lines.append(f"- **{h['name']}** (`{h.get('file_path', '')}`): `{h.get('signature', '')}`")

    if algo.get("cross_file_deps"):
        lines.append("")
        lines.append("### Cross-File Dependencies")
        lines.append("")
        for d in algo["cross_file_deps"]:
            lines.append(f"- **{d['symbol']}** (`{d.get('file_path', '')}`): {d.get('usage_note', '')}")

    lines.append("")
    lines.append("## Signal Coverage")
    lines.append("")
    for sig in coverage.get("signals", []):
        mapped = "mapped" if sig.get("mapped") else "UNMAPPED"
        ctx = f" — {sig['context_notes']}" if sig.get("context_notes") else ""
        lines.append(
            f"- **{sig['sim_name']}** → `{sig.get('prod_access_path', 'N/A')}` "
            f"(fidelity: {sig.get('fidelity_rating', '?')}, {mapped}){ctx}"
        )

    if coverage.get("type_mappings"):
        lines.append("")
        lines.append("### Type Mappings")
        lines.append("")
        for tm in coverage["type_mappings"]:
            lines.append(f"- {tm['sim_type']} → {tm['prod_type']}: {tm.get('notes', '')}")

    if coverage.get("helper_translations"):
        lines.append("")
        lines.append("### Helper Translations")
        lines.append("")
        for ht in coverage["helper_translations"]:
            lines.append(f"- {ht['sim_function']} → {ht['prod_pattern']}: {ht.get('notes', '')}")

    lines.append("")
    full_context_path.write_text("\n".join(lines))
    ok(f"Full context written → {full_context_path.relative_to(REPO_ROOT)}")


def _extract_mapping_hash(mapping_path: Path) -> str:
    text = mapping_path.read_text()
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


def stage_translate(run_dir: Path, algo_summary_path: Path, manifest: dict,
                    mapping_path: Path,
                    force: bool = False, no_gate: bool = False) -> Path:
    out = run_dir / "prepare_signal_coverage.json"
    if _should_skip(out, "Translate", force):
        return out
    step(2, "Translate (single LLM call — gpt-4o)")
    out.unlink(missing_ok=True)

    # Deterministic submodule staleness check
    submodule_head = run(
        ["git", "-C", str(REPO_ROOT / manifest["target"]["repo"]), "rev-parse", "HEAD"],
        capture=True,
    ).stdout.strip()
    mapping_hash = _extract_mapping_hash(mapping_path)
    if not submodule_head.startswith(mapping_hash):
        err(f"Stale submodule: mapping pinned at {mapping_hash}, "
            f"submodule at {submodule_head[:7]}")
        sys.exit(1)
    ok(f"Submodule commit matches mapping artifact ({mapping_hash})")

    algo_summary = json.loads(algo_summary_path.read_text())
    mapping_doc = mapping_path.read_text()

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
    # - fidelity_rating must be high/medium/low (LLM sometimes returns "N/A")
    # - staleness_window_ms must be int (LLM sometimes returns "0" or "N/A")
    # - remove any null-valued fields (schema uses additionalProperties: false)
    NORM_KEYS = {"divide_prod_by_100", "verify_and_normalize", "boolean_presence_check"}
    FIDELITY_KEYS = {"high", "medium", "low"}
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
        # Coerce fidelity_rating to valid enum (unmapped signals default to "low")
        fid = sig.get("fidelity_rating", "")
        if fid not in FIDELITY_KEYS:
            sig["fidelity_rating"] = "low"
        # Coerce staleness_window_ms to int (LLM sometimes returns "0" or "N/A")
        sw = sig.get("staleness_window_ms")
        if sw is not None:
            try:
                sig["staleness_window_ms"] = int(sw)
            except (ValueError, TypeError):
                sig["staleness_window_ms"] = 0
        # Remove any other null-valued fields
        for k in [k for k, v in list(sig.items()) if v is None]:
            del sig[k]

    venv_python = str(REPO_ROOT / ".venv/bin/python")
    cli = str(REPO_ROOT / "tools/transfer_cli.py")

    # Write directly into the run directory — all subsequent work happens here
    out.write_text(json.dumps(coverage, indent=2))
    run([venv_python, cli, "validate-schema", str(out)], cwd=REPO_ROOT)

    if not coverage.get("coverage_complete") or coverage.get("unmapped_signals"):
        err("coverage_complete is not true or unmapped_signals is non-empty — HALT")
        sys.exit(1)

    # Phase 2: Enhanced translation via claude -p (type mappings, helper translations)
    source_file = REPO_ROOT / manifest["algorithm"]["experiment_dir"] / manifest["algorithm"]["source"]
    enhance_prompt = (
        f"Read the signal_coverage.json at {out}.\n"
        f"Read the algorithm_summary.json at {algo_summary_path}.\n"
        f"Read the algorithm source file at {source_file}.\n"
        f"Read the mapping document at {mapping_path}.\n\n"
        "Enhance signal_coverage.json with:\n"
        "1. context_notes for each signal (how it's used in the algorithm — weight, threshold, formula)\n"
        "2. type_mappings: how simulation types map to production types\n"
        "3. helper_translations: how simulation helper functions map to production patterns\n\n"
        f"Write the updated signal_coverage.json back to {out}.\n"
        f"Then run: .venv/bin/python tools/transfer_cli.py validate-schema {out}\n"
    )
    info("Running enhanced translation (type mappings, helper translations)...")
    proc = _run_claude(enhance_prompt, label="Enhanced translate", max_turns=15, timeout=600)
    if proc.returncode != 0:
        warn("Enhanced translation failed — continuing with base translation only")
    else:
        val = run([venv_python, cli, "validate-schema", str(out)], check=False, cwd=REPO_ROOT)
        if val.returncode != 0:
            warn("Enhanced translation produced invalid schema — reverting to base translation")
            out.write_text(json.dumps(coverage, indent=2))

    # Phase 3: AI review + human gate loop
    from lib.gates import review_artifacts, human_gate

    review_prompt = (
        "Review this signal_coverage.json for completeness. Check that:\n"
        "1. All signals from algorithm_summary are mapped to production equivalents\n"
        "2. prod_access_path expressions are valid Go code paths\n"
        "3. fidelity_rating reflects actual mapping quality\n"
        "4. context_notes explain how each signal is used in the algorithm\n"
        "5. type_mappings and helper_translations are present if applicable\n"
        "6. production_gaps captures relevant production capabilities that are NOT"
        " mapped by this algorithm\n"
    )

    for loop_i in range(1, MAX_GATE_LOOPS + 1):
        ai_review = review_artifacts(
            artifact_paths=[out],
            review_prompt=review_prompt,
            models=MODELS,
        )
        if ai_review.passed:
            info("AI review: PASSED")
        else:
            warn(f"AI review: issues found (loop {loop_i}/{MAX_GATE_LOOPS})")
            for line in ai_review.summary_lines():
                print(line)

        if no_gate:
            if not ai_review.passed and loop_i < MAX_GATE_LOOPS:
                warn("--no-gate: AI review failed but skipping human gate")
            break

        # Read gaps from artifact for display
        _artifact = json.loads(out.read_text())
        _translate_gaps = _artifact.get("production_gaps")

        gate_result = human_gate(
            stage_name="Translate",
            artifact_paths=[out],
            ai_review=ai_review,
            context_for_chat=[algo_summary_path, source_file],
            repo_root=REPO_ROOT,
            gaps=_translate_gaps,
        )
        if not gate_result.modified:
            break
        # Human made edits — re-validate and loop
        val = run([venv_python, cli, "validate-schema", str(out)], check=False, cwd=REPO_ROOT)
        if val.returncode != 0:
            err("Edited artifact fails schema validation — fix before continuing")
            continue
        info(f"Re-running AI review after edits (loop {loop_i}/{MAX_GATE_LOOPS})...")

    # Phase 4: Generate prepare_full_context.md for downstream stages
    full_context_path = run_dir / "prepare_full_context.md"
    _write_full_context(full_context_path, algo_summary_path, out, manifest)

    ok(f"Translate complete → {out.relative_to(REPO_ROOT)}")
    return out


# ── Helpers: claude -p subprocess ────────────────────────────────────────────

REVIEW_SCRIPT = (REPO_ROOT / ".claude/skills/sim2real-prepare/scripts/review_translation.py")


def _run_claude(prompt: str, *, label: str = "",
                max_turns: int = 15, timeout: int = 600,
                model: str = "") -> subprocess.CompletedProcess:
    """Run claude -p with stream-json output, relaying tool events to terminal.

    Args:
        max_turns: Maximum conversation turns before claude stops (default 15).
        timeout: Wall-clock timeout in seconds (default 600 = 10 min).
        model: Model override (e.g. "haiku"). Empty string uses default.
    """
    if label:
        info(f"{label}...")
    cmd = [
        "claude", "-p", prompt,
        "--output-format", "stream-json",
        "--verbose",
        "--max-turns", str(max_turns),
        "--dangerously-skip-permissions",
        "--add-dir", str(REPO_ROOT),
    ]
    if model:
        cmd.extend(["--model", model])
    proc = subprocess.Popen(
        cmd,
        cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True,
    )
    import time as _time
    start = _time.monotonic()
    turn_count = 0
    while True:
        line = proc.stdout.readline()
        if not line:
            break
        # Timeout check
        elapsed = _time.monotonic() - start
        if elapsed > timeout:
            err(f"  claude -p timed out after {timeout}s — killing subprocess")
            proc.kill()
            proc.wait()
            return subprocess.CompletedProcess(proc.args, 1)

        line = line.rstrip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            print(f"  [claude] {line}")
            continue
        ev_type = ev.get("type", "")
        if ev_type == "assistant":
            turn_count += 1
            for block in ev.get("message", {}).get("content", []):
                btype = block.get("type", "")
                if btype == "tool_use":
                    name = block.get("name", "")
                    inp = block.get("input", {})
                    detail = inp.get("path") or inp.get("command") or inp.get("file_path") or ""
                    if detail:
                        info(f"  [{turn_count}] {name}: {detail}")
                    else:
                        info(f"  [{turn_count}] {name}")
                elif btype == "text":
                    text = block.get("text", "").strip()
                    if text and len(text) > 20:
                        # Only show substantive text, skip filler like "I'll do X now"
                        preview = text[:120] + ("..." if len(text) > 120 else "")
                        print(f"  [claude] {preview}")
        elif ev_type == "tool_result":
            for block in ev.get("content", []):
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text", "")
                    low = text.lower()
                    if "permission" in low or "denied" in low:
                        err(f"  PERMISSION ISSUE: {text[:300]}")
                    elif "error" in low:
                        err(f"  tool error: {text[:200]}")
        elif ev_type == "result":
            if ev.get("is_error"):
                err(f"  claude -p error: {ev.get('error', ev)}")
            else:
                # Show final result snippet
                result_text = ev.get("result", "")
                if result_text:
                    preview = result_text[:200] + ("..." if len(result_text) > 200 else "")
                    info(f"  [done] {preview}")
    proc.wait()
    if proc.returncode != 0:
        err(f"  claude -p exited with code {proc.returncode} after {turn_count} turns")
    return subprocess.CompletedProcess(proc.args, proc.returncode)


# ── Preamble: context gathering (once per Stage 3 run) ────────────────────────

def _preamble_prompt(run_dir: Path, manifest: dict) -> str:
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Build example file list from manifest
    example_lines = "\n".join(
        f"{i+2}. {{REPO_ROOT}}/{manifest['target']['repo']}/{ex}"
        for i, ex in enumerate(manifest["context"]["examples"])
    )

    return f"""\
You are building context documents for the sim2real plugin generation pipeline.
Read the following files and produce two output documents.
All paths below are absolute.

STRICT CONSTRAINTS:
- Read ONLY the files listed below — nothing else. No ls, no glob, no grep, no exploration.
- Do NOT follow imports or search for additional context.
- If a file does not exist, skip it.
- Use ONE Read tool call per file. Read all files, then write both outputs. No other actions.

## Files to read (use Read tool only)
1. {{REPO_ROOT}}/{manifest["target"]["repo"]}/pkg/plugins/scoring.go
{example_lines}
{len(manifest["context"]["examples"]) + 2}. {{REPO_ROOT}}/{manifest["target"]["repo"]}/test/utils/context.go
{len(manifest["context"]["examples"]) + 3}. {{REPO_ROOT}}/{manifest["target"]["repo"]}/test/utils/network.go
{len(manifest["context"]["examples"]) + 4}. {{REPO_ROOT}}/{manifest["target"]["repo"]}/{manifest["target"]["register_file"]}
{len(manifest["context"]["examples"]) + 5}. {{REPO_ROOT}}/{manifest["context"]["template"]}
{len(manifest["context"]["examples"]) + 6}. {{REPO_ROOT}}/{manifest["context"]["mapping"]}
{len(manifest["context"]["examples"]) + 7}. {{REPO_ROOT}}/{manifest["algorithm"]["experiment_dir"]}/{manifest["algorithm"]["source"]}

## Output 1: {run_dir}/prepare_codebase_context.md
For the plugin writer. Begin with exactly: "Generated: {ts}"
Include:
- Plugin interface signature (exact source from scoring.go)
- EndpointMetadata and Metrics field names used in scoring (exact field names)
- Registration pattern from register.go (exact lines)
- Test package conventions: NewTestContext, NewEndpoint usage copied from test/utils
- Full plugin template (from template file)
- Full mapping.md content (complete, untruncated)
- Example plugins annotated with where interface is satisfied and how each signal is accessed

## Output 2: {run_dir}/prepare_reviewer_context.md
For external model reviewers (GPT-4o, Gemini). Begin with exactly: "Generated: {ts}"
Include everything from Output 1, plus:
- What the simulation simulates: discrete-event simulator; signals represent system state
- Known intentional design decisions reviewers MUST NOT flag as errors (documented in mapping)
- Definition of translation fidelity: the plugin must implement the same mathematical formula as the evolved code, using the mapped production signals

Write both files now. Do not output anything else.
"""


def stage_build_context(run_dir: Path, manifest: dict, force: bool = False) -> tuple[Path, Path]:
    """Build codebase and reviewer context documents (once per Stage 3 run)."""
    ctx_path = run_dir / "prepare_codebase_context.md"
    rev_path = run_dir / "prepare_reviewer_context.md"

    # Skip if both exist and user confirms
    if not force and ctx_path.exists() and rev_path.exists():
        rel = ctx_path.relative_to(REPO_ROOT)
        print(f"\n  Existing context: {rel}")
        while True:
            choice = input("  Reuse context for Generate? [Y]es / [n]o rebuild: ").strip().lower()
            if choice in ("", "y", "yes"):
                info("[skip] Context — reusing existing files")
                return ctx_path, rev_path
            if choice in ("n", "no"):
                break
            print("  Enter 'y' to reuse or 'n' to rebuild.")

    step("3a", "Build codebase context (reads pkg/, test/utils/, mapping doc)")
    ctx_path.unlink(missing_ok=True)
    rev_path.unlink(missing_ok=True)

    result = _run_claude(_preamble_prompt(run_dir, manifest), label="Building context via claude -p",
                         max_turns=6, model="haiku")
    if result.returncode != 0 or not ctx_path.exists() or not rev_path.exists():
        missing = [str(p) for p in (ctx_path, rev_path) if not p.exists()]
        err(f"Context preamble failed (exit {result.returncode}). Missing: {missing}")
        sys.exit(1)

    ok(f"Codebase context  → {ctx_path.relative_to(REPO_ROOT)}")
    ok(f"Reviewer context  → {rev_path.relative_to(REPO_ROOT)}")
    return ctx_path, rev_path


# ── Helpers: evolve block file ────────────────────────────────────────────────

def _write_evolve_block(algo_summary: dict, run_dir: Path) -> Path:
    """Extract EVOLVE-BLOCK and write to run_dir/prepare_evolve_block.go."""
    out = run_dir / "prepare_evolve_block.go"
    content = _load_evolve_block(algo_summary)
    out.write_text(content)
    return out


# ── Helpers: generate iteration ───────────────────────────────────────────────

def _generate_prompt(round_num: int, round_dir: Path, run_dir: Path,
                     algo_summary_path: Path, signal_coverage_path: Path,
                     manifest: dict) -> str:
    ctx_path = run_dir / "prepare_codebase_context.md"

    # Determine prior issues file and plugin snapshot (build failure takes priority over review issues)
    prior_context = ""
    if round_num > 1:
        prev_dir = run_dir / "rounds" / str(round_num - 1)
        build_issues = prev_dir / "build_issues.json"
        review_issues = prev_dir / "review_issues.json"
        plugin_snapshot = prev_dir / "plugin_snapshot.go"
        prior_plugin_line = (
            f"Read {plugin_snapshot} — this is the exact plugin you wrote in round {round_num - 1}.\n"
            if plugin_snapshot.exists() else ""
        )
        if build_issues.exists():
            prior_context = f"""\
## Round {round_num - 1} plugin + build failure
{prior_plugin_line}Read {build_issues} and fix all reported errors.
"""
        elif review_issues.exists():
            prior_context = f"""\
## Round {round_num - 1} plugin + reviewer issues
{prior_plugin_line}Read {review_issues} and fix every issue listed.
"""
        elif prior_plugin_line:
            prior_context = f"""\
## Round {round_num - 1} plugin (no output recorded)
{prior_plugin_line}"""

    plugin_dir = REPO_ROOT / manifest["target"]["repo"] / manifest["target"]["plugin_dir"]
    return f"""\
You are generating a plugin for the production system.

Read {ctx_path} for full system context (interfaces, examples, conventions).
Read {run_dir / "prepare_full_context.md"} for combined extraction + translation context (if it exists).
Read {algo_summary_path} for the algorithm to implement.
Read {signal_coverage_path} for production signal access paths.
{prior_context}
Generate the plugin. Write files in THIS ORDER (use Write tool, not Edit):

1. Plugin: {plugin_dir}/<name>.go
2. IMMEDIATELY write {round_dir}/generate_output.json:
   {{"plugin_name": "<stem>", "plugin_file": "<relative-path-from-repo-root>", \
"test_file": "<relative-path-from-repo-root>", "lines": <int>}}
3. Test: {plugin_dir}/<name>_test.go (if time permits — not required)
4. Register the plugin in {REPO_ROOT / manifest["target"]["repo"] / manifest["target"]["register_file"]}

Registration instructions for register.go:
  - Add the import for your new package
  - Add the plugin.Register() call inside RegisterAllPlugins()
  - Follow the exact pattern of existing registrations in that file
Do NOT read other plugin files in {plugin_dir}/ — only read your own snapshot above if provided.
Use relative paths from the repo root ({REPO_ROOT}) in generate_output.json.
Do not output anything else after writing the files.
"""


def _clean_stale_plugins(manifest: dict) -> None:
    """Remove plugin files not tracked by git so claude writes fresh instead of editing."""
    sched = REPO_ROOT / manifest["target"]["repo"]
    plugin_dir = sched / manifest["target"]["plugin_dir"]
    result = run(["git", "ls-tree", "-r", "--name-only", "HEAD", manifest["target"]["plugin_dir"]],
                 capture=True, check=False, cwd=sched)
    tracked = {Path(p).name for p in result.stdout.strip().splitlines() if p}
    for f in plugin_dir.glob("*.go"):
        if f.name not in tracked:
            f.unlink()
            info(f"  Removed stale: {f.name}")


def stage_generate_iteration(round_num: int, round_dir: Path, run_dir: Path,
                              algo_summary_path: Path,
                              signal_coverage_path: Path, manifest: dict) -> tuple[Path, Path]:
    """Run one generate iteration via claude -p. Returns (plugin_path, test_path)."""
    if round_num == 1:
        _clean_stale_plugins(manifest)

    prompt = _generate_prompt(round_num, round_dir, run_dir,
                               algo_summary_path, signal_coverage_path, manifest)
    prompt_file = round_dir / "generate_prompt.md"
    prompt_file.write_text(prompt)

    label = f"Writer (claude -p) — round {round_num}"
    if round_num > 1:
        label += " (revision)"
    result = _run_claude(prompt, label=label, max_turns=15, model="claude-opus-4-6",
                         timeout=900)

    out_path = round_dir / "generate_output.json"
    if not out_path.exists():
        err(f"Generate round {round_num} failed (exit {result.returncode}) — "
            f"generate_output.json not written; will retry next round")
        return None, None

    try:
        out = json.loads(out_path.read_text())
        plugin_path = REPO_ROOT / (out["plugin_file"])
        test_file_str = out.get("test_file", "")
        test_path = (REPO_ROOT / test_file_str) if test_file_str else None
    except (json.JSONDecodeError, KeyError) as e:
        err(f"Could not parse generate_output.json: {e}; will retry next round")
        return None, None

    if not plugin_path.exists():
        err(f"Plugin file not found after generate: {plugin_path}; will retry next round")
        return None, None

    lines = out.get("lines", len(plugin_path.read_text().splitlines()))
    ok(f"Wrote plugin: {plugin_path.relative_to(REPO_ROOT)} ({lines} lines)")
    return plugin_path, test_path


# ── Helpers: review iteration ─────────────────────────────────────────────────

def _review_prompt(round_dir: Path, run_dir: Path, plugin_path: Path,
                   algo_summary_path: Path, signal_coverage_path: Path,
                   evolve_block_path: Path) -> str:
    rev_ctx = run_dir / "prepare_reviewer_context.md"
    cmd = (
        f"python3 {REVIEW_SCRIPT} "
        f"--plugin {plugin_path} "
        f"--algorithm {algo_summary_path} "
        f"--signals {signal_coverage_path} "
        f"--evolve-block {evolve_block_path} "
        f"--extra-context {rev_ctx} "
        f"--rounds 1 "
        f"--out {round_dir}/review_output.json"
    )
    full_ctx = run_dir / "prepare_full_context.md"
    full_ctx_line = f"Read {full_ctx} for combined extraction + translation context.\n" if full_ctx.exists() else ""
    return f"""\
Read {rev_ctx} for reviewer system context.
{full_ctx_line}
First, write the exact command you are about to run to {round_dir}/review_prompt.txt.

Then run:
  {cmd}

After the script exits (exit 0 = consensus, exit 1 = no consensus), read \
{round_dir}/review_output.json and write {round_dir}/review_issues.json:
  {{"round": <N>, "issues": ["[model] issue text", ...]}}
Use an empty list for "issues" if all models returned verdict "consistent".

Do not output anything else.
"""


def stage_review_iteration(round_dir: Path, run_dir: Path, plugin_path: Path,
                            algo_summary_path: Path, signal_coverage_path: Path,
                            evolve_block_path: Path) -> bool:
    """Run one review iteration via claude -p. Returns True if consensus reached."""
    prompt = _review_prompt(round_dir, run_dir, plugin_path,
                             algo_summary_path, signal_coverage_path,
                             evolve_block_path)
    info("Reviewers (Azure/gpt-4o, GCP/gemini-2.5-flash, aws/claude-opus-4-6)...")
    result = _run_claude(prompt, max_turns=10, model="haiku")

    issues_path = round_dir / "review_issues.json"
    if not issues_path.exists():
        err(f"Review failed (exit {result.returncode}) — review_issues.json not written")
        sys.exit(1)

    try:
        data = json.loads(issues_path.read_text())
        issues = data.get("issues", [])
    except (json.JSONDecodeError, KeyError) as e:
        err(f"Could not parse review_issues.json: {e}")
        sys.exit(1)

    consensus = len(issues) == 0
    return consensus


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
                           mapping_doc: str, plugin_template: str,
                           example_plugin: str, example_plugin_test: str,
                           build_error: str = "") -> list[dict]:
    system = (
        "You are an expert Go engineer implementing a production plugin. "
        "Follow the template and constraints exactly. "
        "Respond with EXACTLY two fenced Go code blocks: "
        "first the plugin implementation, then the test file. "
        "Label each with its file path as a comment on the very first line:\n"
        "  // FILE: <repo>/pkg/plugins/<dir>/<name>.go\n"
        "  // FILE: <repo>/pkg/plugins/<dir>/<name>_test.go\n"
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
    user = f"""Generate a production plugin implementing the evolved algorithm.
{build_error_section}

## Algorithm Summary
```json
{json.dumps(algo_summary, indent=2)}
```

## Signal Coverage (use these production access paths exactly)
```json
{json.dumps(signal_coverage, indent=2)}
```

## Plugin Template
{plugin_template}

## Mapping Document (reference for composite signal expansion)
{mapping_doc[:4000]}

## Example Plugin Implementation (follow these patterns exactly)
```go
{example_plugin}
```

## Example Plugin Test File (copy this structure exactly for your test file)
```go
{example_plugin_test}
```

## Critical Requirements
1. Register the plugin in register.go
2. EffectiveLoad expands to WaitingQueueSize + 2*RunningRequestsSize (both BatchSize and InFlightRequests map to RunningRequestsSize)
3. The plugin type constant must match the kebab-case plugin name with manifest naming suffix
4. Test file MUST use external test package (e.g. `package scorer_test`)
5. Test file MUST import test/utils and use `utils.NewTestContext(t)` to create contexts
6. Test endpoints MUST be constructed with `scheduling.NewEndpoint(&fwkdl.EndpointMetadata{...}, &fwkdl.Metrics{...}, nil)`
7. Use `cmpopts.EquateApprox(0, 1e-9)` for float comparisons (import `github.com/google/go-cmp/cmp/cmpopts`)
8. Test math must be verified: for every test case that claims a score will be negative/positive,
   compute it manually step by step before writing the assertion. A claim like "both scores negative"
   must be validated with actual numbers — if the penalty (e.g. 0.4) is less than the base score
   (e.g. 0.407), the result is positive, not negative. Choose parameter values that guarantee
   the claimed scenario (e.g. increase effectiveLoad until base score < penalty).
9. Exported symbol names (type constant, factory function, constructor) must be consistent
   throughout plugin and test files. Use the same casing everywhere.
"""
    return [{"role": "system", "content": system},
            {"role": "user", "content": user}]


def build_revision_prompt(prev_code: str, issues: list[str],
                           algo_summary: dict, signal_coverage: dict,
                           mapping_doc: str, plugin_template: str,
                           example_plugin_test: str,
                           build_error: str = "") -> list[dict]:
    system = (
        "You are an expert Go engineer fixing a production plugin based on "
        "reviewer feedback. Respond with EXACTLY two fenced Go code blocks: "
        "first the corrected plugin implementation, then the test file. "
        "Label each with its file path as a comment on the very first line:\n"
        "  // FILE: <repo>/pkg/plugins/<dir>/<name>.go\n"
        "  // FILE: <repo>/pkg/plugins/<dir>/<name>_test.go\n"
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
    user = f"""Fix the plugin based on reviewer feedback.
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

## Plugin Template
{plugin_template}

## Example Test File (your test must follow this structure exactly)
```go
{example_plugin_test}
```

## Critical Requirements
1. Register the plugin in register.go
2. EffectiveLoad expands to WaitingQueueSize + 2*RunningRequestsSize
3. The plugin type constant must match the kebab-case plugin name with manifest naming suffix
4. Test file MUST use external test package
5. Test file MUST import test/utils and use `utils.NewTestContext(t)`
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


def _plugin_name_from_summary(algo_summary: dict) -> str:
    name = algo_summary.get("algorithm_name", "evolved_plugin")
    sanitized = re.sub(r"[^a-zA-Z0-9]", "_", name).lower().strip("_")
    sanitized = re.sub(r"_+", "_", sanitized)
    return sanitized


def _plugin_type_from_name(name: str, manifest: dict) -> str:
    suffix = manifest["target"]["naming"]["suffix"]
    t = name.replace("_", "-").lower()
    if not t.endswith(suffix):
        t += suffix
    return t


def _plugin_type_from_source(plugin_path: Path) -> str | None:
    """Read the PluginType constant from a Go source file.

    More reliable than _plugin_type_from_name because it reflects what the
    plugin actually registers, regardless of file naming conventions.
    """
    import re
    try:
        text = plugin_path.read_text()
    except OSError:
        return None
    m = re.search(r'PluginType\s*=\s*"([^"]+)"', text)
    return m.group(1) if m else None


def _write_plugin_files(response: str, algo_summary: dict, manifest: dict) -> tuple[Path, Path]:
    """Parse code blocks from response and write plugin + test files."""
    plugin_dir = REPO_ROOT / manifest["target"]["repo"] / manifest["target"]["plugin_dir"]
    plugin_name = _plugin_name_from_summary(algo_summary)

    plugin_code = _extract_code_block(response, index=0)
    test_code = _extract_code_block(response, index=1)

    if not plugin_code:
        err("Could not extract plugin code block from model response")
        sys.exit(1)
    if not test_code:
        warn("Could not extract test code block — test file will be minimal")
        test_code = f'package {manifest["target"]["package"]}\n\nimport "testing"\n\nfunc Test{plugin_name.title().replace("_", "")}Placeholder(t *testing.T) {{\n}}\n'

    # Override file name from FILE: comment if present
    file_match = re.search(r"// FILE: .*/([a-z][a-z0-9_]+)\.go", plugin_code)
    if file_match:
        plugin_name = file_match.group(1)

    plugin_path = plugin_dir / f"{plugin_name}.go"
    test_path = plugin_dir / f"{plugin_name}_test.go"

    plugin_path.write_text(plugin_code)
    test_path.write_text(test_code)

    ok(f"Wrote plugin: {plugin_path.relative_to(REPO_ROOT)}")
    ok(f"Wrote test:   {test_path.relative_to(REPO_ROOT)}")
    return plugin_path, test_path


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


def _load_llm_config(manifest: dict) -> dict:
    """Read llm_config.yaml. Returns {} if missing."""
    import yaml
    exp = REPO_ROOT / manifest["algorithm"]["experiment_dir"]
    p = exp / manifest["algorithm"]["llm_config"]
    return yaml.safe_load(p.read_text()) if p.exists() else {}


def _load_workloads(manifest: dict) -> list[dict]:
    """Read all workload YAML files from workloads directory. Required by schema."""
    import yaml
    exp = REPO_ROOT / manifest["algorithm"]["experiment_dir"]
    workloads_dir = exp / manifest["algorithm"]["workloads"]
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
        err(f"No workload YAML files found in {workloads_dir.relative_to(REPO_ROOT)} — HALT")
        sys.exit(1)
    ok(f"Loaded {len(workloads)} workload(s): {[w['name'] for w in workloads]}")
    return workloads


def _generate_algorithm_values(algo_summary: dict, signal_coverage: dict,
                                out_dir: Path, manifest: dict) -> Path:
    """Generate algorithm_values.yaml from experiment sources. Fully deterministic."""
    import yaml
    from lib.manifest import set_nested_path

    llm_cfg = _load_llm_config(manifest)
    blis_tag = _blis_image_tag()
    plugin_name = _plugin_name_from_summary(algo_summary)
    plugin_type = _plugin_type_from_name(plugin_name, manifest)

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
    env_cfg_path = REPO_ROOT / manifest["config"]["env_defaults"]
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

    # ── Treatment config (from manifest template) ───────────────────────────
    config_yaml = manifest["config"]["treatment_config_template"].format(plugin_type=plugin_type)

    # ── Workloads (from experiment workloads dir) ────────────────────────────
    workloads = _load_workloads(manifest)

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
                                    "httpGet": {"path": "/health", "port": 8200},
                                    "initialDelaySeconds": 10,
                                    "periodSeconds": 5,
                                    "failureThreshold": 3,
                                },
                                "startupProbe": {
                                    "httpGet": {"path": "/health", "port": 8200},
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
        },
        "observe": {
            "image": f"ghcr.io/inference-sim/blis:{blis_tag}",
            "workloads": workloads,
        },
    }

    # Apply treatment config to nested path via manifest
    helm_path = manifest["config"]["helm_path"]
    set_nested_path(values["stack"], helm_path, config_yaml)

    # ── Treatment EndpointPickerConfig (pluginsCustomConfig) ────────────────
    # Read the actual PluginType from the generated Go source — more reliable
    # than the naming-formula because the LLM may choose a different constant.
    plugin_dir = REPO_ROOT / manifest["target"]["repo"] / manifest["target"]["plugin_dir"]
    plugin_file = plugin_dir / f"{_plugin_name_from_summary(algo_summary)}.go"
    actual_plugin_type = _plugin_type_from_source(plugin_file) or plugin_type
    epc_content = (
        "apiVersion: inference.networking.x-k8s.io/v1alpha1\n"
        "kind: EndpointPickerConfig\n"
        "plugins:\n"
        f"- type: {actual_plugin_type}\n"
        "- type: load-aware-scorer\n"
        "- type: decode-filter\n"
        "- type: max-score-picker\n"
        "- type: single-profile-handler\n"
        "schedulingProfiles:\n"
        "- name: default\n"
        "  plugins:\n"
        f"  - pluginRef: {actual_plugin_type}\n"
        "  - pluginRef: decode-filter\n"
        "  - pluginRef: max-score-picker\n"
        "  - pluginRef: load-aware-scorer\n"
        "    weight: 1\n"
    )
    (values["stack"]
     .setdefault("gaie", {})
     .setdefault("treatment", {})
     .setdefault("helmValues", {})
     .setdefault("inferenceExtension", {})
     ["pluginsCustomConfig"]) = {"custom-plugins.yaml": epc_content}

    out_path = out_dir / "algorithm_values.yaml"
    out_path.write_text(yaml.dump(values, default_flow_style=False,
                                   sort_keys=False, allow_unicode=True))
    ok(f"Generated algorithm_values.yaml → {out_path.relative_to(REPO_ROOT)}")
    return out_path


def _run_merge_values(algo_values_path: Path, out_dir: Path, manifest: dict) -> Path:
    venv_python = str(REPO_ROOT / ".venv/bin/python")
    cli = str(REPO_ROOT / "tools/transfer_cli.py")
    out_path = out_dir / "values.yaml"
    helm_path = manifest["config"]["helm_path"]
    run([
        venv_python, cli, "merge-values",
        "--env", str(REPO_ROOT / manifest["config"]["env_defaults"]),
        "--algorithm", str(algo_values_path),
        "--out", str(out_path),
        "--helm-path", f"stack.{helm_path}",
    ], cwd=REPO_ROOT)
    ok(f"merge-values complete → {out_path.relative_to(REPO_ROOT)}")
    return out_path


def _ensure_plugin_registered(plugin_name: str, plugin_type: str, manifest: dict) -> Path:
    """Verify the generator registered the plugin in register.go.

    The LLM generator is responsible for modifying register.go (adding both the
    import and the plugin.Register() call). This function only verifies the result
    and warns if registration is missing — it does not attempt mechanical edits.
    """
    register_path = REPO_ROOT / manifest["target"]["repo"] / manifest["target"]["register_file"]
    if not register_path.exists():
        warn("register.go not found — skipping registration check")
        return register_path

    content = register_path.read_text()
    pkg = manifest["target"]["package"]

    # Check for both import and Register() call
    has_register = plugin_type in content
    has_import = f'plugins/{pkg}"' in content or f'plugins/{pkg}\n' in content
    if has_register and has_import:
        ok("Plugin registered in register.go (import + Register call)")
    else:
        missing = []
        if not has_import:
            missing.append(f"import for {pkg} package")
        if not has_register:
            missing.append(f"plugin.Register() call for {plugin_type}")
        warn(f"register.go missing: {', '.join(missing)} — generator should have added these")

    return register_path


def _reconstruct_stage3_output(run_dir: Path, stage3_out: Path, manifest: dict) -> Path:
    """Reconstruct prepare_stage3_output.json from the plugin file already on disk.

    Called when --skip-generate is passed but the artifact was never written
    (e.g. a previous run failed before Stage 3 completed).
    """
    sched = REPO_ROOT / manifest["target"]["repo"]
    plugin_dir = sched / manifest["target"]["plugin_dir"]

    # Detect generated files by checking git tracking
    result = run(["git", "ls-tree", "-r", "--name-only", "HEAD", manifest["target"]["plugin_dir"]],
                 capture=True, check=False, cwd=sched)
    tracked = {Path(p).name for p in result.stdout.strip().splitlines() if p}

    candidates = [
        f for f in plugin_dir.glob("*.go")
        if not f.name.endswith("_test.go") and f.name not in tracked
    ]
    if not candidates:
        err("--skip-generate: no generated plugin file found in "
            f"{plugin_dir.relative_to(REPO_ROOT)} — cannot reconstruct stage3 output.")
        sys.exit(1)
    if len(candidates) > 1:
        names = [c.name for c in candidates]
        err(f"--skip-generate: multiple unknown plugin files found: {names}. "
            "Remove stale files or run without --skip-generate.")
        sys.exit(1)

    plugin_path = candidates[0]
    plugin_name = plugin_path.stem
    plugin_type = _plugin_type_from_name(plugin_name, manifest)
    test_path = plugin_dir / f"{plugin_name}_test.go"
    register_path = REPO_ROOT / manifest["target"]["repo"] / manifest["target"]["register_file"]

    # Look for merged values.yaml that may already exist
    out_dir = run_dir / "prepare_tekton"
    values_yaml = out_dir / "values.yaml"

    payload = {
        "plugin_file": str(plugin_path.relative_to(REPO_ROOT)),
        "plugin_type": plugin_type,
        "plugin_package": manifest["target"]["package"],
        "test_file": str(test_path.relative_to(REPO_ROOT)) if test_path.exists() else "",
        "register_file": str(register_path.relative_to(REPO_ROOT)),
        "tekton_artifacts": {
            "values_yaml": str(values_yaml.relative_to(REPO_ROOT)) if values_yaml.exists() else "",
        },
    }

    stage3_out.parent.mkdir(parents=True, exist_ok=True)
    stage3_out.write_text(json.dumps(payload, indent=2))
    info(f"[skip] Reconstructed {stage3_out.relative_to(REPO_ROOT)} from {plugin_path.name}")
    return stage3_out


def stage_generate(run_dir: Path, algo_summary_path: Path,
                   signal_coverage_path: Path, reviews: int, manifest: dict,
                   force: bool = False, skip_generate: bool = False) -> Path:
    stage3_out = run_dir / "prepare_stage3_output.json"

    if skip_generate:
        if not stage3_out.exists():
            stage3_out = _reconstruct_stage3_output(run_dir, stage3_out, manifest)
        info(f"[skip] Generate — --skip-generate passed, using {stage3_out.relative_to(REPO_ROOT)}")
        s3 = json.loads(stage3_out.read_text())
        plugin_name = Path(s3["plugin_file"]).stem
        plugin_type = s3.get("plugin_type", _plugin_type_from_name(plugin_name, manifest))
        _ensure_plugin_registered(plugin_name, plugin_type, manifest)
        return stage3_out

    if not force and stage3_out.exists():
        try:
            _s3 = json.loads(stage3_out.read_text())
            if (REPO_ROOT / (_s3.get("plugin_file", ""))).exists():
                if _should_skip(stage3_out, "Generate", force):
                    return stage3_out
        except (json.JSONDecodeError, KeyError):
            pass

    step(3, "Generate  (writer: claude -p  |  reviewers: all 3 models)")
    out_dir = run_dir / "prepare_tekton"
    out_dir.mkdir(parents=True, exist_ok=True)
    stage3_out.unlink(missing_ok=True)

    algo_summary = json.loads(algo_summary_path.read_text())
    signal_coverage = json.loads(signal_coverage_path.read_text())

    # Preamble: build context documents once (skippable on rerun)
    stage_build_context(run_dir, manifest, force=force)

    # Write evolve block to stable file for review script
    evolve_block_path = _write_evolve_block(algo_summary, run_dir)

    # Round loop — resume from last incomplete round if rerunning
    round_num = 0
    remaining = reviews
    plugin_path: Path | None = None
    test_path: Path | None = None
    consensus = False

    rounds_root = run_dir / "rounds"
    if not force and rounds_root.exists():
        existing = sorted(
            (int(d.name) for d in rounds_root.iterdir()
             if d.is_dir() and d.name.isdigit()),
        )
        for n in existing:
            rd = rounds_root / str(n)
            complete = (rd / "build_issues.json").exists() or (rd / "review_issues.json").exists()
            if complete:
                round_num = n
                remaining -= 1
                # Check if this round reached consensus
                ri = rd / "review_issues.json"
                if ri.exists():
                    try:
                        issues = json.loads(ri.read_text()).get("issues", [])
                        if len(issues) == 0:
                            consensus = True
                    except (json.JSONDecodeError, KeyError):
                        pass
            else:
                break  # incomplete round — will redo it
        if round_num > 0 and not consensus:
            info(f"  Resuming from round {round_num + 1} ({round_num} completed rounds found)")

    if consensus:
        # Previous run already reached consensus — recover plugin path from generate_output.json
        last_dir = rounds_root / str(round_num)
        gen_out = last_dir / "generate_output.json"
        if gen_out.exists():
            gdata = json.loads(gen_out.read_text())
            plugin_path = REPO_ROOT / (gdata["plugin_file"])
            test_file_str = gdata.get("test_file", "")
            test_path = (REPO_ROOT / test_file_str) if test_file_str else None
            ok(f"  Consensus already reached in round {round_num} (previous run)")
        else:
            # Can't recover — redo this round
            consensus = False
            round_num -= 1
            info(f"  Round {round_num + 1} had consensus but missing generate_output.json — redoing")

    while not consensus:
        round_num += 1
        round_dir = run_dir / "rounds" / str(round_num)
        round_dir.mkdir(parents=True, exist_ok=True)

        revision = " (revision)" if round_num > 1 else ""
        print(f"\n  ── Round {round_num}{revision} " + "─" * max(0, 48 - len(revision)))

        plugin_path, test_path = stage_generate_iteration(
            round_num, round_dir, run_dir, algo_summary_path, signal_coverage_path, manifest,
        )

        if plugin_path is None:
            info("  Generate did not produce output — retrying next round...")
            remaining -= 1
            if remaining > 0:
                continue
            extra = _prompt_user_continue(False, label=f"Generate round {round_num}")
            if extra == 0:
                sys.exit(1)
            remaining = extra
            continue

        # Snapshots written before build — records exactly what was compiled
        shutil.copy(plugin_path, round_dir / "plugin_snapshot.go")
        if test_path and test_path.exists():
            shutil.copy(test_path, round_dir / "plugin_test_snapshot.go")

        plugin_name = plugin_path.stem
        plugin_type = _plugin_type_from_name(plugin_name, manifest)
        _ensure_plugin_registered(plugin_name, plugin_type, manifest)

        build_passed, build_error = _run_build_test(manifest)
        (round_dir / "build_output.txt").write_text(build_error or "PASS")

        if not build_passed:
            (round_dir / "build_issues.json").write_text(json.dumps({
                "round": round_num,
                "issues": [build_error],
            }, indent=2))
            info("  Build failed — passing errors to next generate round...")
            continue  # skip review; build error feeds next round

        consensus = stage_review_iteration(
            round_dir, run_dir, plugin_path,
            algo_summary_path, signal_coverage_path, evolve_block_path,
        )

        issues = json.loads(
            (round_dir / "review_issues.json").read_text()
        ).get("issues", [])
        print(f"  Round logs: {round_dir.relative_to(REPO_ROOT)}/")

        if consensus:
            ok(f"  Consensus reached in round {round_num}")
            break

        remaining -= 1
        if remaining > 0:
            info(f"  Passing {len(issues)} issue(s) to next round...")
            continue

        # Max rounds reached — pause for user
        extra = _prompt_user_continue(consensus, label=f"Generate round {round_num}")
        if extra == 0:
            break
        remaining = extra

    # Generate Tekton artifacts (logic unchanged from original)
    algo_values_path = _generate_algorithm_values(algo_summary, signal_coverage, out_dir, manifest)
    values_path = _run_merge_values(algo_values_path, out_dir, manifest)

    plugin_name = plugin_path.stem
    plugin_type = _plugin_type_from_name(plugin_name, manifest)
    register_path = _ensure_plugin_registered(plugin_name, plugin_type, manifest)

    payload = json.dumps({
        "plugin_file": str(plugin_path.relative_to(REPO_ROOT)),
        "plugin_type": plugin_type,
        "plugin_package": manifest["target"]["package"],
        "test_file": str(test_path.relative_to(REPO_ROOT)) if test_path and test_path.exists() else "",
        "register_file": str(register_path.relative_to(REPO_ROOT)),
        "tekton_artifacts": {
            "values_yaml": str(values_path.relative_to(REPO_ROOT)),
        },
    }, indent=2)

    venv_python = str(REPO_ROOT / ".venv/bin/python")
    cli = str(REPO_ROOT / "tools/transfer_cli.py")

    ws_out = REPO_ROOT / "workspace/stage3_output.json"
    ws_out.write_text(payload)
    run([venv_python, cli, "validate-schema", str(ws_out)], cwd=REPO_ROOT)
    shutil.copy(ws_out, stage3_out)

    ok(f"Generate complete → {stage3_out.relative_to(REPO_ROOT)}")
    ok(f"Round logs: {(run_dir / 'rounds').relative_to(REPO_ROOT)}/")
    return stage3_out


# ── Stage 4: Build / Test / Equivalence Gate ──────────────────────────────────

def _run_build_test(manifest: dict) -> tuple[bool, str]:
    """Run go build/vet/test on the plugin. Returns (passed, error_output)."""
    sched = REPO_ROOT / manifest["target"]["repo"]
    env = {**os.environ, "GOWORK": "off"}
    for cmd_args in manifest["target"]["test_commands"]:
        label = " ".join(cmd_args[:2])
        result = run(cmd_args, check=False, capture=True, cwd=sched, env=env)
        if result.returncode != 0:
            output = f"{label} failed:\n{result.stdout[-3000:]}\n{result.stderr[-1000:]}".strip()
            err(output)
            return False, output
        ok(f"{label} passed")
    return True, ""


def stage_build_test(stage3_path: Path, manifest: dict, force: bool = False) -> tuple[bool, str]:
    """Build and test the generated plugin. Returns (passed, error_output)."""
    # Silently skip if equivalence results exist — the user is asked once in stage_equivalence_gate
    equiv_out = stage3_path.parent / "prepare_equivalence_results.json"
    if not force and equiv_out.exists():
        return True, ""
    step(4, "Build & Test")
    return _run_build_test(manifest)


def stage_equivalence_gate(run_dir: Path, manifest: dict, force: bool = False) -> Path:
    out = run_dir / "prepare_equivalence_results.json"
    if _should_skip(out, "Equivalence Gate", force):
        return out

    equiv_cmds = manifest["target"].get("equivalence_commands", [])
    if not equiv_cmds:
        info("No equivalence commands in manifest — skipping gate")
        out.write_text(json.dumps({"skipped": True}, indent=2))
        return out

    step("4.5", "Equivalence Gate")
    sched = REPO_ROOT / manifest["target"]["repo"]
    env = {**os.environ, "GOWORK": "off"}

    results = {}
    for entry in equiv_cmds:
        cmd = entry["command"]
        fatal = entry.get("fatal", True)
        name = entry.get("name", " ".join(cmd[:3]))
        result = run(cmd, check=False, capture=True, cwd=sched, env=env)
        passed = result.returncode == 0
        results[name] = {
            "passed": passed,
            "fatal": fatal,
            "output": (result.stdout + result.stderr)[-2000:],
        }
        if not passed and fatal:
            err(f"{name} FAILED:\n{result.stdout[-2000:]}")
            sys.exit(1)
        ok(f"{name}: {'PASS' if passed else 'WARN (non-fatal)'}")

    out.write_text(json.dumps(results, indent=2))
    ok(f"Equivalence gate → {out.relative_to(REPO_ROOT)}")
    return out


# ── Stage 5: Review ───────────────────────────────────────────────────────────

def build_review_prompt(plugin_code: str, algo_summary: dict,
                         signal_coverage: dict, evolve_block: str) -> list[dict]:
    system = (
        "You are an expert code reviewer verifying that a Go plugin faithfully "
        "implements an evolved algorithm. Be precise and technical. "
        'Respond with JSON only: {"verdict": "consistent"|"inconsistent", '
        '"issues": [...], "per_signal": {...}, "summary": "..."}'
    )
    user = f"""Review the plugin Go code for translation fidelity.

For EACH signal verify: correct production field is read, normalization matches
algorithm_summary, weight/coefficient matches EVOLVE-BLOCK, and scoring logic is faithful.

## Generated Plugin
```go
{plugin_code}
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
                        reviews: int, manifest: dict, force: bool = False) -> bool:
    """Run final 3-model review via claude -p. Returns True if consensus reached."""
    out = run_dir / "prepare_translation_reviews.json"
    if not force and out.exists():
        try:
            if json.loads(out.read_text()).get("passed"):
                if _should_skip(out, "Final Review", force):
                    return True
        except (json.JSONDecodeError, KeyError):
            pass
    step(5, "Final Review (3 models)")

    stage3 = json.loads(stage3_path.read_text())
    plugin_path = REPO_ROOT / (stage3["plugin_file"])

    algo_summary = json.loads(algo_summary_path.read_text())
    evolve_block_path = _write_evolve_block(algo_summary, run_dir)

    final_dir = run_dir / "final-review"
    final_dir.mkdir(parents=True, exist_ok=True)

    consensus = stage_review_iteration(
        final_dir, run_dir, plugin_path,
        algo_summary_path, signal_coverage_path, evolve_block_path,
    )

    issues = json.loads(
        (final_dir / "review_issues.json").read_text()
    ).get("issues", [])
    passed = consensus

    out.write_text(json.dumps({
        "rounds": 1,
        "consensus": consensus,
        "accepted_by_user": False,
        "passed": passed,
        "issues": issues,
        "logs": str(final_dir.relative_to(REPO_ROOT)),
    }, indent=2))
    status = "passed ✓" if passed else "failed — issues found"
    ok(f"Final review {status} → {out.relative_to(REPO_ROOT)}")
    ok(f"Final review logs: {final_dir.relative_to(REPO_ROOT)}/")
    return passed


# ── Scorer snapshot ───────────────────────────────────────────────────────────

def persist_plugin_snapshot(run_dir: Path, stage3_path: Path, manifest: dict) -> tuple[Path, Path | None]:
    """Copy generated plugin + test files into run_dir as durable run artifacts.

    Called only after all gates pass. Returns (plugin_dest, test_dest).
    test_dest is None when no test file was generated.
    """
    stage3 = json.loads(stage3_path.read_text())

    plugin_src = REPO_ROOT / (stage3["plugin_file"])
    test_src_str = stage3.get("test_file", "")
    test_src = (REPO_ROOT / test_src_str) if test_src_str else None

    plugin_dest = run_dir / manifest["artifacts"]["plugin_snapshot"]
    shutil.copy(plugin_src, plugin_dest)
    ok(f"Plugin snapshot → {plugin_dest.relative_to(REPO_ROOT)}")

    test_dest: Path | None = None
    if test_src and test_src.exists():
        test_dest = run_dir / manifest["artifacts"]["plugin_test_snapshot"]
        shutil.copy(test_src, test_dest)
        ok(f"Test snapshot   → {test_dest.relative_to(REPO_ROOT)}")

    return plugin_dest, test_dest


# ── Completion ────────────────────────────────────────────────────────────────

def write_outputs(run_dir: Path, cfg: dict, stage3_path: Path, manifest: dict) -> None:
    stage3 = json.loads(stage3_path.read_text())
    persist_plugin_snapshot(run_dir, stage3_path, manifest)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    artifact_names = [
        "prepare_algorithm_summary.json",
        "prepare_signal_coverage.json",
        "prepare_stage3_output.json",
        "prepare_translation_reviews.json",
        manifest["artifacts"]["plugin_snapshot"],
        manifest["artifacts"]["plugin_test_snapshot"],
    ]

    update_run_metadata(
        run_dir, "prepare",
        status="completed",
        completed_at=now,
        summary="Extract, translate, generate, build/test, and AI review completed",
        artifacts=artifact_names,
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
        manifest["artifacts"]["plugin_snapshot"],
        manifest["artifacts"]["plugin_test_snapshot"],
    ]:
        p = run_dir / name
        exists = "✓" if p.exists() else "-"
        print(f"  {exists}  {p}")
    plugin = stage3.get("plugin_file", "")
    if plugin:
        print(f"  {REPO_ROOT / plugin}")
    print()
    print("Next: python scripts/deploy.py  OR  /sim2real-deploy in Claude")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    global MODELS
    args = build_parser().parse_args()
    if args.dev:
        MODELS = DEV_MODELS
    print_intro(args.reviews, dev=args.dev, no_gate=args.no_gate)

    # Load manifest
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from lib.manifest import load_manifest, ManifestError
    try:
        manifest = load_manifest(args.manifest)
    except ManifestError as e:
        err(f"Manifest error: {e}")
        sys.exit(1)

    check_prerequisites(manifest)
    cfg = load_setup_config()
    run_dir = REPO_ROOT / "workspace/runs" / cfg["current_run"]

    update_run_metadata(
        run_dir, "prepare",
        status="in_progress",
        started_at=datetime.now(timezone.utc).isoformat(),
    )

    force = args.force
    skip_generate = args.skip_generate
    no_gate = args.no_gate

    try:
        algo_summary_path = stage_extract(run_dir, manifest, force=force, no_gate=no_gate)
        canonical_mapping = REPO_ROOT / manifest["context"]["mapping"]
        signal_coverage_path = stage_translate(
            run_dir, algo_summary_path, manifest,
            mapping_path=canonical_mapping,    # Task 3 will replace this with resolved path
            force=force, no_gate=no_gate,
        )

        MAX_OUTER = 3  # final-review retries
        stage3_path: Path | None = None
        for outer in range(1, MAX_OUTER + 1):
            if outer > 1:
                warn(f"Final review found issues — restarting Generate "
                     f"(outer {outer}/{MAX_OUTER})...")

            # Force regeneration on any retry; --skip-generate only on first attempt
            force_gen = force or outer > 1
            stage3_path = stage_generate(
                run_dir, algo_summary_path, signal_coverage_path, args.reviews, manifest,
                force=force_gen,
                skip_generate=skip_generate and outer == 1,
            )
            stage_build_test(stage3_path, manifest, force=force_gen)
            stage_equivalence_gate(run_dir, manifest, force=force_gen)
            passed = stage_final_review(
                run_dir, stage3_path, algo_summary_path, signal_coverage_path, args.reviews, manifest,
                force=force_gen,
            )
            if passed:
                break
            if outer == MAX_OUTER:
                err("Final review still failing after max retries — HALT")
                sys.exit(1)

        write_outputs(run_dir, cfg, stage3_path, manifest)
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
