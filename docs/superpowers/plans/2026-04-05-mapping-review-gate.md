# Mapping Document Review Gate — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Insert a pre-translate interactive gate into `prepare.py` that generates, reviews, and manages the signal mapping document per-run, with a run-scoped override model.

**Architecture:** A new `stage_mapping_review()` function sits between `stage_extract()` and `stage_translate()` in `prepare.py`. It resolves the active mapping path (override → canonical → generate), presents a `[e/c/d]` loop, and returns the resolved path. Downstream functions receive the path as a parameter instead of reading it from the manifest directly.

**Tech Stack:** Python 3.10+ stdlib, existing `lib/llm.py` (`call_model`), existing `lib/gates.py` patterns (`_run_chat_loop`), PyYAML (already in venv)

**Spec:** `docs/superpowers/specs/2026-04-05-mapping-review-gate-design.md`

---

## Chunk 1: Plumbing changes (no behavior change)

These tasks make no behavioral change to the pipeline — they just thread the resolved path through to the right places so Task 3 (the new gate) can plug in cleanly.

### Task 1: Add `--path` flag to `validate-mapping` in `transfer_cli.py`

**Files:**
- Modify: `tools/transfer_cli.py` (around line 2931–2936 in `main()`, and line 539 in `cmd_validate_mapping`)

- [ ] **Step 1.1: Write the failing test**

```python
# tools/test_transfer_cli.py — add to existing TestValidateMapping class (or create it)
def test_validate_mapping_custom_path(tmp_path):
    """--path flag routes to the specified file, not MAPPING_PATH."""
    fake_mapping = tmp_path / "custom_mapping.md"
    fake_mapping.write_text("no pinned hash here")
    result = subprocess.run(
        [sys.executable, str(CLI), "validate-mapping", "--path", str(fake_mapping)],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )
    # Should fail (no pinned hash), not error with "file not found"
    assert result.returncode != 2, "Should not return infrastructure error 2"
    output = json.loads(result.stdout)
    assert output["status"] == "error"

def test_validate_mapping_default_path_unchanged():
    """No --path flag: uses the canonical MAPPING_PATH (existing behavior)."""
    result = subprocess.run(
        [sys.executable, str(CLI), "validate-mapping"],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )
    # Either 0 (valid canonical mapping) or non-zero — just must not crash
    assert result.returncode in (0, 1, 2)
    json.loads(result.stdout)  # must be valid JSON
```

- [ ] **Step 1.2: Run tests to verify they fail**

```bash
python -m pytest tools/test_transfer_cli.py::test_validate_mapping_custom_path -v
```
Expected: `FAIL` — `--path` not yet accepted.

- [ ] **Step 1.3: Add `--path` argument to the `validate-mapping` subparser**

In `tools/transfer_cli.py`, find the `p_mapping` subparser definition (~line 2931):

```python
p_mapping = subparsers.add_parser("validate-mapping", ...)
p_mapping.add_argument("--summary", help="Path to algorithm_summary.json (default: workspace/)")
p_mapping.add_argument(          # ADD THIS
    "--path", metavar="FILE",
    help="Path to mapping document to validate (default: docs/transfer/blis_to_llmd_mapping.md)",
)
p_mapping.set_defaults(func=cmd_validate_mapping)
```

- [ ] **Step 1.4: Update `cmd_validate_mapping` to use `--path` when provided**

In `tools/transfer_cli.py`, find `cmd_validate_mapping` (~line 539). Replace the hardcoded `MAPPING_PATH` at the top of the function:

```python
def cmd_validate_mapping(args: argparse.Namespace) -> int:
    """Validate mapping artifact completeness against algorithm summary."""
    mapping_path = Path(args.path) if getattr(args, "path", None) else MAPPING_PATH
    if not mapping_path.exists():
        return _output("error", 2, errors=[f"Mapping artifact not found: {mapping_path}"],
                        output_type="mapping_validation",
                        mapping_complete=False, missing_signals=[], extra_signals=[],
                        duplicate_signals=[], double_counting_risks=[], stale_commit=False)
    # ... rest of function unchanged, replace MAPPING_PATH with mapping_path throughout
```

Also replace all subsequent references to `MAPPING_PATH` inside `cmd_validate_mapping` with `mapping_path`.

- [ ] **Step 1.5: Run tests**

```bash
python -m pytest tools/test_transfer_cli.py::test_validate_mapping_custom_path \
                 tools/test_transfer_cli.py::test_validate_mapping_default_path_unchanged -v
```
Expected: both PASS.

- [ ] **Step 1.6: Commit**

```bash
git add tools/transfer_cli.py tools/test_transfer_cli.py
git commit -m "feat(cli): add --path flag to validate-mapping subcommand"
```

---

### Task 2: Thread resolved mapping path through `prepare.py`

**Files:**
- Modify: `scripts/prepare.py`

This task changes `_extract_mapping_hash()` and `stage_translate()` to accept a `Path` parameter instead of reading `manifest["context"]["mapping"]` directly. No behavioral change — the caller passes the same canonical path for now. Task 3 will pass the override path when appropriate.

Also removes `context.mapping` from `check_prerequisites()` required files — the mapping doc may not exist yet when `--no-cluster` or first run; the gate will handle creation.

- [ ] **Step 2.1: Write a unit test for the refactored `_extract_mapping_hash`**

```python
# tools/test_setup_registry.py or a new tools/test_prepare_mapping.py
# Since prepare.py is a script (not a library), test via subprocess or importlib.

import importlib.util, sys
from pathlib import Path
REPO_ROOT = Path(__file__).parent.parent
_spec = importlib.util.spec_from_file_location("prepare", REPO_ROOT / "scripts" / "prepare.py")
_prepare = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_prepare)

def test_extract_mapping_hash_reads_from_path(tmp_path):
    """_extract_mapping_hash reads hash from the given path, not manifest."""
    mapping = tmp_path / "mapping.md"
    mapping.write_text("**Pinned commit hash:** abc1234\n")
    result = _prepare._extract_mapping_hash(mapping)
    assert result == "abc123"  # 7 chars

def test_extract_mapping_hash_missing_hash(tmp_path):
    """_extract_mapping_hash exits 1 when no hash found."""
    mapping = tmp_path / "mapping.md"
    mapping.write_text("no hash here\n")
    with pytest.raises(SystemExit):
        _prepare._extract_mapping_hash(mapping)
```

- [ ] **Step 2.2: Run tests to verify they fail**

```bash
python -m pytest tools/test_prepare_mapping.py::test_extract_mapping_hash_reads_from_path -v
```
Expected: FAIL — function signature doesn't match yet.

- [ ] **Step 2.3: Refactor `_extract_mapping_hash` to accept a `Path`**

In `scripts/prepare.py`, replace (~line 417):

```python
def _extract_mapping_hash(mapping_path: Path) -> str:
    text = mapping_path.read_text()
    m = re.search(r"\*{0,2}Pinned commit hash:\*{0,2}\s*([0-9a-f]{7,40})", text)
    if not m:
        err("Could not extract pinned commit hash from mapping artifact")
        sys.exit(1)
    return m.group(1)[:7]
```

- [ ] **Step 2.4: Update `stage_translate` signature and all internal references**

Change `stage_translate` signature to accept `mapping_path: Path`:

```python
def stage_translate(run_dir: Path, algo_summary_path: Path, manifest: dict,
                    mapping_path: Path,                        # NEW parameter
                    force: bool = False, no_gate: bool = False) -> Path:
```

Inside `stage_translate`, replace every reference to `manifest["context"]["mapping"]` and `REPO_ROOT / manifest["context"]["mapping"]` with `mapping_path`:

- Line ~473: `mapping_hash = _extract_mapping_hash(mapping_path)`
- Line ~481: `mapping_doc = mapping_path.read_text()`
- Line ~560: `f"Read the mapping document at {mapping_path}.\n\n"`

- [ ] **Step 2.5: Update the call site in `main()` to pass canonical path (temporary — Task 3 will pass the resolved path)**

In `main()` (~line 2088):

```python
canonical_mapping = REPO_ROOT / manifest["context"]["mapping"]
signal_coverage_path = stage_translate(
    run_dir, algo_summary_path, manifest,
    mapping_path=canonical_mapping,    # Task 3 will replace this with resolved path
    force=force, no_gate=no_gate,
)
```

- [ ] **Step 2.6: Remove `context.mapping` from `check_prerequisites` required files**

In `check_prerequisites` (~line 149–153):

```python
required_files = [
    exp / manifest["algorithm"]["source"],
    exp / manifest["algorithm"]["info"],
    # REMOVED: REPO_ROOT / manifest["context"]["mapping"],   ← gate handles creation
    REPO_ROOT / manifest["context"]["template"],
    REPO_ROOT / "workspace/setup_config.json",
]
```

- [ ] **Step 2.7: Run tests**

```bash
python -m pytest tools/test_prepare_mapping.py -v
```
Expected: all PASS.

```bash
# Smoke test: prepare.py --help should not crash
python scripts/prepare.py --help
```

- [ ] **Step 2.8: Commit**

```bash
git add scripts/prepare.py tools/test_prepare_mapping.py
git commit -m "refactor(prepare): thread resolved mapping path through stage_translate"
```

---

### Task 3: Add `mapping_notes` default to manifest loader

**Files:**
- Modify: `scripts/lib/manifest.py`
- Modify: `config/transfer.yaml`

`context.mapping_notes` is optional. The manifest loader needs to apply a default (`""`) so callers can always do `manifest["context"].get("mapping_notes", "")` safely.

- [ ] **Step 3.1: Write the test**

```python
# scripts/test_manifest.py  (or add to existing manifest tests)
from pathlib import Path
import tempfile, yaml
import sys
sys.path.insert(0, str(Path(__file__).parent))
from lib.manifest import load_manifest

def test_mapping_notes_defaults_to_empty(tmp_path):
    """mapping_notes is optional; absent means empty string."""
    manifest_data = {
        "kind": "sim2real-transfer", "version": 1,
        "algorithm": {"experiment_dir": "x", "source": "s.go", "info": "i.json",
                       "workloads": "w/", "llm_config": "l.yaml"},
        "target": {"repo": "r", "plugin_dir": "p/", "register_file": "reg.go",
                   "package": "pkg", "naming": {"suffix": "-s"},
                   "test_commands": []},
        "context": {"mapping": "docs/m.md", "template": "docs/t.md"},
        "config": {"env_defaults": "config/e.yaml",
                   "treatment_config_template": "t", "helm_path": "h"},
        "validation": {"mode": "custom_evaluator", "evaluator": "e.py"},
    }
    p = tmp_path / "transfer.yaml"
    p.write_text(yaml.dump(manifest_data))
    m = load_manifest(p)
    assert m["context"].get("mapping_notes", "") == ""

def test_mapping_notes_preserved_when_set(tmp_path):
    manifest_data = {
        "kind": "sim2real-transfer", "version": 1,
        "algorithm": {"experiment_dir": "x", "source": "s.go", "info": "i.json",
                       "workloads": "w/", "llm_config": "l.yaml"},
        "target": {"repo": "r", "plugin_dir": "p/", "register_file": "reg.go",
                   "package": "pkg", "naming": {"suffix": "-s"},
                   "test_commands": []},
        "context": {"mapping": "docs/m.md", "template": "docs/t.md",
                    "mapping_notes": "focus on aggregate signals"},
        "config": {"env_defaults": "config/e.yaml",
                   "treatment_config_template": "t", "helm_path": "h"},
        "validation": {"mode": "custom_evaluator", "evaluator": "e.py"},
    }
    p = tmp_path / "transfer.yaml"
    p.write_text(yaml.dump(manifest_data))
    m = load_manifest(p)
    assert m["context"]["mapping_notes"] == "focus on aggregate signals"
```

- [ ] **Step 3.2: Run tests to verify they fail**

```bash
python -m pytest scripts/test_manifest.py::test_mapping_notes_defaults_to_empty -v
```
Expected: FAIL — key not in defaults yet.

- [ ] **Step 3.3: Add `mapping_notes` default in `manifest.py`**

In `scripts/lib/manifest.py`, add to the `defaults` dict (~line 72):

```python
'context.mapping_notes': '',
```

- [ ] **Step 3.4: Run tests**

```bash
python -m pytest scripts/test_manifest.py -v
```
Expected: all PASS.

- [ ] **Step 3.5: Add `mapping_notes` to `config/transfer.yaml` (as empty comment block)**

```yaml
context:
  mapping: docs/transfer/blis_to_llmd_admission_mapping.md
  template: docs/transfer/admission_template.go.md
  mapping_notes: |
    # Optional: provide domain context to steer mapping generation.
    # Example: "This is an admission control algorithm. The a.* fields
    # are internal plugin state and do not need production signal mappings."
  examples: []
  extra:
    - admission_by60/baselines/baseline_always_admit.go
```

- [ ] **Step 3.6: Commit**

```bash
git add scripts/lib/manifest.py config/transfer.yaml scripts/test_manifest.py
git commit -m "feat(manifest): add optional context.mapping_notes field with empty default"
```

---

## Chunk 2: The mapping review gate

### Task 4: Implement `stage_mapping_review()` in `prepare.py`

**Files:**
- Modify: `scripts/prepare.py`

This is the core new function. It handles the three states (canonical / override / neither), the generation session, the edit/chat loop, and writing the override file.

- [ ] **Step 4.1: Write tests for mapping path resolution**

```python
# tools/test_prepare_mapping.py — add to existing file

def test_resolve_mapping_path_override_takes_precedence(tmp_path):
    """Override file in run_dir wins over canonical."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    canonical = tmp_path / "canonical.md"
    canonical.write_text("canonical")
    override = run_dir / "mapping_override.md"
    override.write_text("override")

    resolved = _prepare._resolve_mapping_path(run_dir, canonical)
    assert resolved == override
    assert resolved.read_text() == "override"

def test_resolve_mapping_path_canonical_when_no_override(tmp_path):
    """Falls back to canonical when no override exists."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    canonical = tmp_path / "canonical.md"
    canonical.write_text("canonical")

    resolved = _prepare._resolve_mapping_path(run_dir, canonical)
    assert resolved == canonical

def test_resolve_mapping_path_none_when_neither_exists(tmp_path):
    """Returns None when neither override nor canonical exists."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    canonical = tmp_path / "nonexistent.md"

    resolved = _prepare._resolve_mapping_path(run_dir, canonical)
    assert resolved is None
```

- [ ] **Step 4.2: Run tests to verify they fail**

```bash
python -m pytest tools/test_prepare_mapping.py::test_resolve_mapping_path_override_takes_precedence -v
```
Expected: FAIL — `_resolve_mapping_path` not defined.

- [ ] **Step 4.3: Implement `_resolve_mapping_path` helper**

Add to `scripts/prepare.py` before `stage_mapping_review`:

```python
def _resolve_mapping_path(run_dir: Path, canonical: Path) -> Path | None:
    """Return the active mapping path: override > canonical > None.

    Args:
        run_dir: Current run directory (workspace/runs/<run>/).
        canonical: The canonical path from manifest["context"]["mapping"].

    Returns:
        Path to the active mapping document, or None if neither exists.
    """
    override = run_dir / "mapping_override.md"
    if override.exists():
        return override
    if canonical.exists():
        return canonical
    return None
```

- [ ] **Step 4.4: Run resolution tests**

```bash
python -m pytest tools/test_prepare_mapping.py -k "resolve_mapping_path" -v
```
Expected: all 3 PASS.

- [ ] **Step 4.5: Implement `_generate_mapping_doc` (LLM generation for State C)**

Add to `scripts/prepare.py`:

```python
def _generate_mapping_doc(
    canonical_path: Path,
    algo_summary_path: Path,
    manifest: dict,
    user_context: str,
) -> None:
    """Generate a first-draft mapping document via LLM and write to canonical_path.

    Args:
        canonical_path: Where to write the generated mapping document.
        algo_summary_path: Path to prepare_algorithm_summary.json for this run.
        manifest: Transfer manifest (used to read mapping_notes and submodule path).
        user_context: Additional context typed by the user at invocation time.
    """
    from lib.llm import call_model, LLMError

    algo_summary = json.loads(algo_summary_path.read_text())
    mapping_notes = manifest.get("context", {}).get("mapping_notes", "").strip()

    # Read submodule interface files for production-side signal knowledge
    target_repo = REPO_ROOT / manifest["target"]["repo"]
    prod_context = ""
    for candidate in [
        target_repo / "pkg/plugins/scoring.go",
        target_repo / "pkg/framework/interface.go",
    ]:
        if candidate.exists():
            prod_context += f"\n## {candidate.name}\n```go\n{candidate.read_text()[:4000]}\n```\n"

    system = (
        "You are a technical architect mapping simulation signals to production equivalents. "
        "Generate a complete signal mapping document in the established markdown format. "
        "The document must include a Signal Mapping Table, Fidelity Rating Scale, and "
        "Scorer Interface Reference. Include 'Pinned commit hash: <hash>' in the header. "
        "Be precise about production access paths and fidelity ratings."
    )
    user = f"""Generate a signal mapping document for this algorithm.

## Algorithm Summary
```json
{json.dumps(algo_summary, indent=2)}
```
{f"## Domain Context (from transfer.yaml){chr(10)}{mapping_notes}{chr(10)}" if mapping_notes else ""}
{f"## Additional User Context{chr(10)}{user_context}{chr(10)}" if user_context.strip() else ""}
{f"## Production Interface Reference{chr(10)}{prod_context}" if prod_context else ""}

## Instructions
1. For each signal in algorithm_summary.signals[], determine its production equivalent.
2. Distinguish between:
   - External observables: signals read from cluster/request state → map to production paths
   - Internal plugin state: signals the algorithm maintains itself (a.* fields) → note as
     "internal state — maintain as plugin-local variable, no production mapping needed"
3. Use the established mapping table format with columns:
   Sim Signal | Go Type | Sim Access Path | Production Equivalent | Prod Access Path | Fidelity | Staleness Window (ms) | Rationale
4. Set Pinned commit hash to: {_get_submodule_head(manifest)}
5. Include the Fidelity Rating Scale section (high/medium/low definitions).
6. Include the Scorer Interface Reference section.

Respond with ONLY the markdown document — no prose wrapper.
"""
    info("Calling LLM to generate mapping document draft...")
    try:
        response = call_model("Azure/gpt-4o", [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ])
    except LLMError as e:
        err(f"LLM call failed during mapping generation: {e}")
        sys.exit(1)

    canonical_path.parent.mkdir(parents=True, exist_ok=True)
    canonical_path.write_text(response)
    ok(f"Mapping document draft written → {canonical_path.relative_to(REPO_ROOT)}")


def _get_submodule_head(manifest: dict) -> str:
    """Return the short HEAD commit hash of the target submodule."""
    result = run(
        ["git", "-C", str(REPO_ROOT / manifest["target"]["repo"]), "rev-parse", "--short", "HEAD"],
        check=False, capture=True,
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"
```

- [ ] **Step 4.6: Implement `_mapping_chat_loop` (multi-turn document edit)**

Add to `scripts/prepare.py`:

```python
def _mapping_chat_loop(
    doc_path: Path,
    override_path: Path,
    manifest: dict,
) -> Path:
    """Multi-turn LLM chat to edit the mapping document.

    Each turn: user message + current doc → LLM returns updated full doc.
    Writes result to override_path and returns it.

    Args:
        doc_path: Current active mapping document (canonical or existing override).
        override_path: Where to write updated document (run-scoped override).
        manifest: Transfer manifest (used for mapping_notes system context).
    Returns:
        Path to the (possibly updated) override file.
    """
    from lib.llm import call_model, LLMError

    mapping_notes = manifest.get("context", {}).get("mapping_notes", "").strip()
    current_doc = doc_path.read_text()

    system_parts = [
        "You are a technical architect editing a signal mapping document. "
        "When the user asks you to make a change, return the COMPLETE updated document. "
        "Preserve all sections (Signal Mapping Table, Fidelity Rating Scale, "
        "Scorer Interface Reference). Make only the changes the user requests."
    ]
    if mapping_notes:
        system_parts.append(f"\nDomain context:\n{mapping_notes}")
    system = "\n".join(system_parts)

    info("Chatting with gpt-4o about the mapping document.")
    info("Type your request, or 'done' (or empty line) to finish.")
    print()

    modified = False
    while True:
        try:
            user_input = input("  > ").strip()
        except EOFError:
            break
        if not user_input or user_input.lower() in ("done", "exit", "quit"):
            break

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": (
                f"Here is the current mapping document:\n\n{current_doc}\n\n"
                f"User request: {user_input}\n\n"
                "Return ONLY the complete updated markdown document."
            )},
        ]
        info("Updating mapping document...")
        try:
            response = call_model("Azure/gpt-4o", messages)
        except LLMError as e:
            warn(f"LLM call failed: {e} — skipping this turn")
            continue

        current_doc = response
        override_path.write_text(current_doc)
        modified = True
        ok(f"Updated → {override_path.relative_to(REPO_ROOT)}")

    if modified:
        ok(f"Changes saved → {override_path.relative_to(REPO_ROOT)}")
    else:
        info("No changes made.")
    return override_path
```

- [ ] **Step 4.7: Implement `stage_mapping_review()`**

Add to `scripts/prepare.py` between `stage_extract` and `stage_translate`:

```python
def stage_mapping_review(
    run_dir: Path,
    manifest: dict,
    algo_summary_path: Path,
    no_gate: bool = False,
) -> Path:
    """Interactive mapping document review gate.

    Resolves the active mapping path (override → canonical → generate),
    presents an [e]dit / [c]hat / [d]one loop, and returns the resolved path.

    State A — canonical exists, no override: show path, offer e/c/d.
    State B — override exists: show both, offer e/c/d/x (drop override).
    State C — neither exists: run generation session, then fall into State A loop.

    Args:
        run_dir: Current run directory.
        manifest: Transfer manifest.
        algo_summary_path: Path to algorithm summary for generation context.
        no_gate: If True, skip interactive prompts (CI mode).

    Returns:
        Path to the resolved (and possibly overridden) mapping document.
    """
    step("1b", "Mapping document review")

    canonical = REPO_ROOT / manifest["context"]["mapping"]
    override = run_dir / "mapping_override.md"

    resolved = _resolve_mapping_path(run_dir, canonical)

    # State C: neither exists — run generation session first
    if resolved is None:
        warn(f"No mapping document found at {canonical.relative_to(REPO_ROOT)}")
        if no_gate:
            err("--no-gate: mapping document missing and cannot generate interactively — HALT")
            sys.exit(1)
        print(f"\n[INFO]  Let's create one. Provide context about this algorithm's domain")
        print(f"        (or press Enter to generate from algorithm summary alone):")
        try:
            user_context = input("  > ").strip()
        except EOFError:
            user_context = ""
        _generate_mapping_doc(canonical, algo_summary_path, manifest, user_context)
        resolved = canonical

    # Display state
    print()
    info(f"Mapping document: {canonical.relative_to(REPO_ROOT)}")
    if resolved == override:
        warn(f"Run override active: {override.relative_to(REPO_ROOT)}")
        info("The override will be used for translation.")
    else:
        info("This document guides signal translation. Review it before continuing.")

    if no_gate:
        info("--no-gate: skipping mapping review")
        _record_mapping_source(run_dir, resolved, override)
        return resolved

    # Interactive loop
    while True:
        if resolved == override:
            prompt = "  [e] Edit override  [c] Chat with model  [d] Done  [x] Drop override\n  > "
        else:
            prompt = "  [e] Edit file directly  [c] Chat with model  [d] Done\n  > "

        try:
            choice = input(prompt).strip().lower()
        except EOFError:
            choice = "d"

        if choice == "d":
            break
        elif choice == "q":
            print("Aborted.")
            sys.exit(0)
        elif choice == "x" and resolved == override:
            override.unlink()
            resolved = canonical
            ok(f"Override dropped — using canonical: {canonical.relative_to(REPO_ROOT)}")
            info(f"Mapping document: {canonical.relative_to(REPO_ROOT)}")
        elif choice == "e":
            # Copy canonical to override before editing (so canonical stays pristine)
            if resolved == canonical:
                import shutil as _shutil
                _shutil.copy(canonical, override)
                resolved = override
                info(f"Copied to override for editing: {override.relative_to(REPO_ROOT)}")
            editor = os.environ.get("EDITOR", "")
            if editor:
                subprocess.run([editor, str(resolved)], check=False)
            else:
                print(f"\n  Edit: {resolved}")
                input("  Press Enter when done editing > ")
        elif choice == "c":
            if resolved == canonical:
                # Copy to override so we don't modify the canonical
                import shutil as _shutil
                _shutil.copy(canonical, override)
                resolved = override
            resolved = _mapping_chat_loop(resolved, override, manifest)
        else:
            print(f"  Invalid choice '{choice}'.")

    _record_mapping_source(run_dir, resolved, override)
    ok(f"Using mapping: {resolved.relative_to(REPO_ROOT)}")
    return resolved


def _record_mapping_source(run_dir: Path, resolved: Path, override: Path) -> None:
    """Write mapping_source to run_metadata.json."""
    source = "override" if resolved == override else "canonical"
    update_run_metadata(run_dir, "prepare", mapping_source=source)
```

- [ ] **Step 4.8: Wire `stage_mapping_review` into `main()`**

In `scripts/prepare.py` `main()` (~line 2086–2088), replace the temporary canonical path with the resolved path from the new gate:

```python
algo_summary_path = stage_extract(run_dir, manifest, force=force, no_gate=no_gate)
mapping_path = stage_mapping_review(           # NEW
    run_dir, manifest, algo_summary_path, no_gate=no_gate
)
signal_coverage_path = stage_translate(
    run_dir, algo_summary_path, manifest,
    mapping_path=mapping_path,                 # pass resolved path
    force=force, no_gate=no_gate,
)
```

- [ ] **Step 4.9: Remove the temporary canonical_mapping line added in Task 2 Step 2.5 (if it's still there)**

Verify `main()` no longer has the temporary `canonical_mapping = ...` line — it should now get the path from `stage_mapping_review`.

- [ ] **Step 4.10: Manual smoke test (no-gate mode)**

```bash
# Smoke test: verify prepare.py --help and imports still work
python scripts/prepare.py --help

# Verify mapping resolution with a synthetic run directory
python -c "
import sys; sys.path.insert(0, 'scripts')
import importlib.util
spec = importlib.util.spec_from_file_location('prepare', 'scripts/prepare.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
from pathlib import Path
import tempfile, os
with tempfile.TemporaryDirectory() as td:
    run_dir = Path(td) / 'run'
    run_dir.mkdir()
    canonical = Path(td) / 'mapping.md'
    canonical.write_text('# mapping')
    result = m._resolve_mapping_path(run_dir, canonical)
    assert result == canonical
    override = run_dir / 'mapping_override.md'
    override.write_text('# override')
    result = m._resolve_mapping_path(run_dir, canonical)
    assert result == override
    print('Resolution logic OK')
"
```

- [ ] **Step 4.11: Run the full test suite**

```bash
python -m pytest tools/test_prepare_mapping.py -v
```
Expected: all PASS.

- [ ] **Step 4.12: Commit**

```bash
git add scripts/prepare.py tools/test_prepare_mapping.py
git commit -m "feat(prepare): add stage_mapping_review gate with generation, edit, and chat modes"
```

---

## Chunk 3: Tests and documentation cleanup

### Task 5: Tests for `stage_mapping_review` state machine

**Files:**
- Modify: `tools/test_prepare_mapping.py`

- [ ] **Step 5.1: Write state machine tests**

```python
# tools/test_prepare_mapping.py — add these tests

from unittest.mock import patch, MagicMock

def test_state_a_done_returns_canonical(tmp_path):
    """State A: canonical exists, user presses 'd' — returns canonical path."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    canonical = tmp_path / "mapping.md"
    canonical.write_text("**Pinned commit hash:** abc1234\n")

    manifest = {"context": {"mapping": str(canonical.relative_to(tmp_path)),
                             "mapping_notes": ""},
                 "target": {"repo": "llm-d-inference-scheduler"}}

    # Monkeypatch REPO_ROOT and input
    with patch.object(_prepare, "REPO_ROOT", tmp_path), \
         patch("builtins.input", return_value="d"), \
         patch.object(_prepare, "update_run_metadata"):
        result = _prepare.stage_mapping_review(run_dir, manifest, tmp_path / "summary.json",
                                                no_gate=False)
    assert result == canonical

def test_state_b_drop_override_reverts_to_canonical(tmp_path):
    """State B: override exists, user presses 'x' then 'd' — override deleted, canonical returned."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    canonical = tmp_path / "mapping.md"
    canonical.write_text("**Pinned commit hash:** abc1234\n")
    override = run_dir / "mapping_override.md"
    override.write_text("override content")

    manifest = {"context": {"mapping": str(canonical.relative_to(tmp_path)),
                             "mapping_notes": ""},
                 "target": {"repo": "llm-d-inference-scheduler"}}

    with patch.object(_prepare, "REPO_ROOT", tmp_path), \
         patch("builtins.input", side_effect=["x", "d"]), \
         patch.object(_prepare, "update_run_metadata"):
        result = _prepare.stage_mapping_review(run_dir, manifest, tmp_path / "summary.json",
                                                no_gate=False)

    assert result == canonical
    assert not override.exists()

def test_no_gate_skips_prompts(tmp_path):
    """--no-gate: skips all interactive prompts, returns canonical path."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    canonical = tmp_path / "mapping.md"
    canonical.write_text("**Pinned commit hash:** abc1234\n")

    manifest = {"context": {"mapping": str(canonical.relative_to(tmp_path)),
                             "mapping_notes": ""},
                 "target": {"repo": "llm-d-inference-scheduler"}}

    with patch.object(_prepare, "REPO_ROOT", tmp_path), \
         patch.object(_prepare, "update_run_metadata"):
        result = _prepare.stage_mapping_review(run_dir, manifest, tmp_path / "summary.json",
                                                no_gate=True)
    assert result == canonical

def test_state_c_no_gate_exits(tmp_path):
    """State C with --no-gate: mapping missing → sys.exit(1)."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    manifest = {"context": {"mapping": "nonexistent.md", "mapping_notes": ""},
                 "target": {"repo": "llm-d-inference-scheduler"}}

    with patch.object(_prepare, "REPO_ROOT", tmp_path), \
         pytest.raises(SystemExit):
        _prepare.stage_mapping_review(run_dir, manifest, tmp_path / "summary.json",
                                       no_gate=True)
```

- [ ] **Step 5.2: Run tests**

```bash
python -m pytest tools/test_prepare_mapping.py -v
```
Expected: all PASS.

- [ ] **Step 5.3: Commit**

```bash
git add tools/test_prepare_mapping.py
git commit -m "test(prepare): state machine tests for stage_mapping_review"
```

---

### Task 6: Update `print_intro` banner and `CLAUDE.md`

**Files:**
- Modify: `scripts/prepare.py` (`print_intro`)
- Modify: `CLAUDE.md`

- [ ] **Step 6.1: Add mapping review step to `print_intro`**

In `print_intro` (~line 128), add a line after the Extract gate entry:

```python
if not no_gate:
    print("                   human gate    [e]dit / [c]hat / [d]one / [q]uit")
print("  1b. Map review   mapping gate  generate or review signal mapping doc")  # NEW
if not no_gate:
    print("                   human gate    [e]dit / [c]hat / [d]one (+ [x] drop override)")
```

- [ ] **Step 6.2: Update `CLAUDE.md` CLI Commands section**

Add to the CLI Commands section of `CLAUDE.md`:

```markdown
# Copy a mapping override from one run to another
cp workspace/runs/<src-run>/mapping_override.md workspace/runs/<dst-run>/mapping_override.md

# Promote a run override to the canonical mapping document
cp workspace/runs/<run>/mapping_override.md docs/transfer/blis_to_llmd_admission_mapping.md

# Validate an override mapping file
python tools/transfer_cli.py validate-mapping --path workspace/runs/<run>/mapping_override.md
```

- [ ] **Step 6.3: Commit**

```bash
git add scripts/prepare.py CLAUDE.md
git commit -m "docs: update prepare banner and CLAUDE.md for mapping review gate"
```
