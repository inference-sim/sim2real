#!/usr/bin/env python3
"""Transfer pipeline CLI — mechanical support for sim-to-production transfer.

Commands:
    extract <routing_dir>    Parse EVOLVE-BLOCK, produce algorithm_summary.json
    validate-mapping         Check mapping artifact completeness
    validate-schema <path>   Validate workspace artifact against JSON Schema

Exit codes: 0 = success, 1 = validation failure, 2 = infrastructure error
All commands output JSON to stdout.
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKSPACE = REPO_ROOT / "workspace"
SCHEMAS_DIR = Path(__file__).resolve().parent / "schemas"
MAPPING_PATH = REPO_ROOT / "docs" / "transfer" / "blis_to_llmd_mapping.md"

# RoutingSnapshot fields and their Go types (from inference-sim/sim/routing.go)
# SYNC NOTE: This dict is derived from the struct at the pinned submodule commit.
# If inference-sim evolves, re-derive via:
#   grep -A 20 'type RoutingSnapshot struct' inference-sim/sim/routing.go
# F-1 RECONCILIATION: This dict contains ALL 7 struct fields (mirrors the Go struct).
# Of these, only 5 are accessed in the EVOLVE-BLOCK as routing signals (QueueDepth,
# BatchSize, KVUtilization, CacheHitRate, InFlightRequests). ID is used as a map key
# (endpoint identifier) but is NOT a routing signal. FreeKVBlocks is not accessed.
# They appear here because test_routing_snapshot_fields_match_source verifies this
# dict matches the Go struct exactly.
ROUTING_SNAPSHOT_FIELDS = {
    "ID": "string",              # Struct field — used as map key, NOT a routing signal
    "QueueDepth": "int",
    "BatchSize": "int",
    "KVUtilization": "float64",
    "FreeKVBlocks": "int64",     # Struct field — NOT accessed in EVOLVE-BLOCK
    "CacheHitRate": "float64",
    "InFlightRequests": "int",
}

# Fields used as identifiers/keys, not routing signals. These are in
# ROUTING_SNAPSHOT_FIELDS (for struct completeness) but excluded from signal extraction.
_IDENTIFIER_FIELDS = {"ID"}

# Method calls that expand to multiple fields.
# Verified against: inference-sim EffectiveLoad() = QueueDepth + BatchSize + InFlightRequests
# AUTOMATED CHECK: See test_method_expansion_matches_source in test_transfer_cli.py
METHOD_EXPANSIONS = {
    "EffectiveLoad": ["QueueDepth", "BatchSize", "InFlightRequests"],
}

# Request-level fields accessed outside RoutingSnapshot (e.g., req.SessionID)
REQUEST_LEVEL_FIELDS = {
    "SessionID": "string",
}

# Fields that are routing-scope (non-routing would be scheduling, batching, etc.)
ROUTING_SCOPE_FIELDS = set(ROUTING_SNAPSHOT_FIELDS.keys()) | set(REQUEST_LEVEL_FIELDS.keys())

# Out-of-scope patterns (P/D disaggregation, scheduling internals)
OUT_OF_SCOPE_PATTERNS = [
    r"PrefillInstance|DecodeInstance",  # P/D disaggregation
    r"BatchFormation|SchedulingPolicy",  # Scheduling internals
]


def _output(status: str, exit_code: int, **kwargs) -> int:
    """Print JSON result and return exit code."""
    result = {"status": status, **kwargs}
    if "errors" not in result:
        result["errors"] = []
    print(json.dumps(result, indent=2))
    return exit_code


def _extract_evolve_block(source: str) -> tuple[str | None, str | None]:
    """Extract EVOLVE-BLOCK region and line range from Go source embedded in Python.

    Detects multiple EVOLVE-BLOCK pairs and emits a warning if more than
    one is found. Only the first pair is extracted.
    """
    lines = source.split("\n")
    start_idx = None
    end_idx = None
    block_count = 0
    for i, line in enumerate(lines):
        if "EVOLVE-BLOCK-START" in line:
            if start_idx is None:
                start_idx = i
            block_count += 1
        if "EVOLVE-BLOCK-END" in line:
            if end_idx is None:
                end_idx = i
    if start_idx is None and end_idx is None:
        return None, None
    if start_idx is None:
        print("WARNING: EVOLVE-BLOCK-END found without EVOLVE-BLOCK-START", file=sys.stderr)
        return None, None
    if end_idx is None:
        print(f"WARNING: EVOLVE-BLOCK-START found at line "
              f"{start_idx + 1} but no EVOLVE-BLOCK-END", file=sys.stderr)
        return None, None
    if block_count > 1:
        print(f"WARNING: Found {block_count} EVOLVE-BLOCK-START markers but only "
              f"extracting the first block (lines {start_idx + 1}-{end_idx + 1}). "
              f"Additional blocks are silently ignored. If multiple blocks are "
              f"intentional, extend _extract_evolve_block to handle them.",
              file=sys.stderr)
    block = "\n".join(lines[start_idx:end_idx + 1])
    line_range = f"{start_idx + 1}-{end_idx + 1}"
    return block, line_range


def _extract_signals(block: str) -> list[dict]:
    r"""Identify RoutingSnapshot fields accessed in the EVOLVE-BLOCK.

    NOTE: Regex-based extraction — may miss signals accessed through aliased
    variables or chained method calls not matching the patterns below.

    FALSE POSITIVE MITIGATION: The `[a-z]\.` pattern is intentionally broad
    but only matches whose field name appears in ROUTING_SNAPSHOT_FIELDS are kept.
    A negative lookbehind ensures only standalone single-char variables match.

    FALSE NEGATIVE RISK: Signals accessed through patterns not covered by the
    regex will be missed silently. The TestGoldenSignalList golden-file test
    is the primary safety net.
    """
    found: dict[str, str] = {}  # name -> access_path

    # Match direct field access on known receiver names
    # NOTE: (?<![a-zA-Z0-9_]) ensures [a-z] only matches standalone single-char
    # variables (like 's', 'l'), NOT the last char of multi-char identifiers (like 'ws').
    for match in re.finditer(
        r'(?:snap(?:shots?\[\w+\])?|target|(?<![a-zA-Z0-9_])[a-z])\.(\w+)', block
    ):
        field = match.group(1)
        if field in ROUTING_SNAPSHOT_FIELDS and field not in _IDENTIFIER_FIELDS:
            found[field] = f"snap.{field}"

    # Match method calls that expand to multiple fields
    _IGNORE_METHODS = {"String", "Error", "Format", "GoString", "Reset", "ProtoMessage"}
    for match in re.finditer(r'\.\b(\w+)\(\)', block):
        method = match.group(1)
        if method in METHOD_EXPANSIONS:
            for field in METHOD_EXPANSIONS[method]:
                if field not in found:
                    found[field] = f"snap.{method}() -> {field}"
        elif method not in _IGNORE_METHODS:
            print(f"WARNING: Unrecognized method call '{method}()' in EVOLVE-BLOCK — "
                  f"not in METHOD_EXPANSIONS. If this is a new RoutingSnapshot method, "
                  f"add it to METHOD_EXPANSIONS with its constituent fields.",
                  file=sys.stderr)

    # Match request-level field access: req.FieldName, request.FieldName
    for match in re.finditer(r'(?:req(?:uest)?)\.\b(\w+)', block):
        field = match.group(1)
        if field in REQUEST_LEVEL_FIELDS:
            found[field] = f"req.{field}"

    # Detect unrecognized field accesses and include them with type 'unknown'
    all_known = set(ROUTING_SNAPSHOT_FIELDS.keys()) | set(REQUEST_LEVEL_FIELDS.keys()) | set(METHOD_EXPANSIONS.keys())
    _IGNORE_FIELDS = {"String", "Error", "Len", "Less", "Swap", "Format"}
    for match in re.finditer(r'(?:snap(?:shots?\[\w+\])?|target|req(?:uest)?|(?<![a-zA-Z0-9_])[a-z])\.\b(\w+)', block):
        field = match.group(1)
        if field not in all_known and field not in _IGNORE_FIELDS and field not in found:
            print(f"WARNING: Unrecognized field access '{field}' in EVOLVE-BLOCK — "
                  f"not in ROUTING_SNAPSHOT_FIELDS or REQUEST_LEVEL_FIELDS. "
                  f"Included with type 'unknown'. Downstream stages MUST resolve this.",
                  file=sys.stderr)
            receiver = match.group(0).split(".")[0]
            found[field] = f"{receiver}.{field}"

    # Normalization notes for signals with known unit mismatches
    NORMALIZATION_NOTES = {
        "KVUtilization": "divide_prod_by_100: production value (0-100 percentage) must be divided by 100 to match sim's 0.0-1.0 ratio (i.e., normalized = prod_kv / 100.0)",
        "SessionID": "boolean_presence_check: compare against empty string (req.SessionID != empty)",
    }

    signals = []
    all_fields = {**ROUTING_SNAPSHOT_FIELDS, **REQUEST_LEVEL_FIELDS}
    for name, access_path in sorted(found.items()):
        sig = {
            "name": name,
            "type": all_fields.get(name, "unknown"),
            "access_path": access_path,
        }
        if name in NORMALIZATION_NOTES:
            sig["normalization_note"] = NORMALIZATION_NOTES[name]
        signals.append(sig)
    return signals


def _check_scope(block: str, signals: list[dict]) -> tuple[bool, list[str]]:
    """Check that the algorithm is routing-scope only.

    Note: re.search(pattern, block) matches anywhere including Go comments.
    This is acceptable for v1 because the EVOLVE-BLOCK is Go code where
    comments are unlikely to reference out-of-scope struct names.
    """
    errors = []

    for pattern in OUT_OF_SCOPE_PATTERNS:
        match = re.search(pattern, block)
        if match:
            errors.append(f"Out-of-scope pattern found: '{match.group()}' (matched by /{pattern}/)")

    for sig in signals:
        if sig["type"] == "unknown":
            continue  # Already flagged for downstream resolution via type='unknown'
        if sig["name"] not in ROUTING_SCOPE_FIELDS:
            errors.append(f"Non-routing signal: {sig['name']}")

    return len(errors) == 0, errors


def _check_fidelity(signals: list[dict], *, strict: bool = False) -> tuple[bool, list[str]]:
    """Check signal fidelity if mapping artifact exists. Returns (ok, errors).

    NOTE: This function has a deliberate side effect — it mutates signal dicts
    in-place by adding sig['fidelity_provisional'] = True for signals with
    provisional ratings.
    """
    if not MAPPING_PATH.exists():
        if strict:
            return False, [
                "Mapping artifact not found and --strict mode is enabled. "
                "Cannot perform fidelity check without mapping artifact. "
                "Ensure docs/transfer/blis_to_llmd_mapping.md exists."
            ]
        print("WARNING: Mapping artifact not found — fidelity check skipped. "
              "Run validate-mapping after creating the mapping artifact. "
              "Use --strict to enforce mapping artifact presence (recommended for CI).",
              file=sys.stderr)
        return True, []

    MAX_MAPPING_SIZE = 10 * 1024 * 1024  # 10 MB
    if MAPPING_PATH.stat().st_size > MAX_MAPPING_SIZE:
        return False, [
            f"Mapping artifact exceeds {MAX_MAPPING_SIZE} bytes — refusing to read."
        ]

    if not strict:
        print("NOTICE: Running without --strict. Fidelity checks are active (mapping artifact "
              "found), but CI uses --strict for deterministic enforcement. Use --strict locally "
              "to match CI behavior.",
              file=sys.stderr)

    try:
        content = MAPPING_PATH.read_text()
    except OSError as e:
        return False, [f"Failed to read mapping artifact: {e}"]

    errors = []
    # Column skip counts for fidelity extraction
    MAIN_TABLE_FIDELITY_COL_OFFSET = 4
    ADDITIONAL_TABLE_FIDELITY_COL_OFFSET = 2

    for sig in signals:
        pattern = rf'\|\s*{re.escape(sig["name"])}(?:\s*\([^)]*\))?\s*\|(?:[^|]*\|){{{MAIN_TABLE_FIDELITY_COL_OFFSET}}}\s*(low|medium|high)\s*(?:\*\(provisional\)\*)?\s*\|'
        match = re.search(pattern, content, re.IGNORECASE)
        if not match:
            pattern_alt = rf'\|\s*{re.escape(sig["name"])}(?:\s*\([^)]*\))?\s*\|(?:[^|]*\|){{{ADDITIONAL_TABLE_FIDELITY_COL_OFFSET}}}\s*(low|medium|high)\s*(?:\*\(provisional\)\*)?\s*\|'
            match = re.search(pattern_alt, content, re.IGNORECASE)
        if match:
            rating = match.group(1).lower()
            is_provisional = "*(provisional)*" in match.group(0)
            if is_provisional:
                print(f"WARNING: Signal '{sig['name']}' has provisional {rating} fidelity rating. "
                      f"No empirical data supports this rating — PR5 must validate.",
                      file=sys.stderr)
                sig["fidelity_provisional"] = True
            if rating == "low":
                errors.append(
                    f"Signal '{sig['name']}' has low fidelity rating — "
                    f"pipeline halted (low-fidelity signals not supported in v1)"
                )
        else:
            errors.append(
                f"Signal '{sig['name']}' not found in mapping artifact — "
                f"unknown fidelity treated as unsafe (add signal to mapping table)"
            )
    return len(errors) == 0, errors


def cmd_extract(args: argparse.Namespace) -> int:
    """Extract algorithm metadata from routing artifacts."""
    routing_dir = Path(args.routing_dir).resolve()

    # Infrastructure validation FIRST
    if not routing_dir.is_dir():
        return _output("error", 2, errors=[f"Routing directory not found: {routing_dir}"])

    # CI auto-detection — FAIL if --strict not used in CI environment
    import os as _os
    _ci_val = _os.environ.get("CI", "").lower()
    if _ci_val in ("true", "1", "yes") and not getattr(args, 'strict', False):
        return _output("error", 1, errors=[
            "CI environment detected (CI env var is set) but --strict flag not set. "
            "CI pipelines MUST use --strict to ensure deterministic fidelity checks. "
            "Fix: either pass --strict (recommended), set CI=false, or unset the CI "
            "environment variable if you are running locally. "
            "Usage: python tools/transfer_cli.py extract --strict routing/"
        ])

    program_py = routing_dir / "best_program.py"
    if not program_py.exists():
        return _output("error", 2, errors=[f"best_program.py not found in {routing_dir}"])

    info_json = routing_dir / "best_program_info.json"
    if not info_json.exists():
        return _output("error", 2, errors=[f"best_program_info.json not found in {routing_dir}"])

    # Size guard
    MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB
    if program_py.stat().st_size > MAX_FILE_SIZE:
        return _output("error", 2, errors=[
            f"best_program.py exceeds {MAX_FILE_SIZE} bytes — refusing to read."])
    if info_json.stat().st_size > MAX_FILE_SIZE:
        return _output("error", 2, errors=[
            f"best_program_info.json exceeds {MAX_FILE_SIZE} bytes — refusing to read."])

    # Read and parse
    source = program_py.read_text()
    block, line_range = _extract_evolve_block(source)
    if block is None:
        return _output("error", 2, errors=[
            "EVOLVE-BLOCK markers not found or malformed in best_program.py. "
            "Check stderr for specific diagnostic (e.g., START without END)."])

    # Extract signals
    signals = _extract_signals(block)
    if not signals:
        return _output("error", 1, errors=["No routing signals found in EVOLVE-BLOCK"])

    # Sanity check: warn if signal count is suspiciously low
    MINIMUM_EXPECTED_SIGNALS = 3
    if len(signals) < MINIMUM_EXPECTED_SIGNALS:
        msg = (f"Only {len(signals)} signals found (expected >= {MINIMUM_EXPECTED_SIGNALS}). "
               f"Regex may have missed field access patterns. Manually verify against EVOLVE-BLOCK.")
        if getattr(args, 'strict', False):
            return _output("error", 1, errors=[msg])
        print(f"WARNING: {msg}", file=sys.stderr)

    # Scope validation
    scope_ok, scope_errors = _check_scope(block, signals)

    # Fidelity check
    fidelity_ok, fidelity_errors = _check_fidelity(signals, strict=getattr(args, 'strict', False))
    if not fidelity_ok:
        return _output("error", 1, errors=fidelity_errors)

    # Read metrics
    try:
        info = json.loads(info_json.read_text())
    except json.JSONDecodeError as e:
        return _output("error", 2, errors=[f"Malformed JSON in {info_json}: {e}"])
    metrics = info.get("metrics", {})
    if not metrics or "combined_score" not in metrics:
        msg = ("WARNING: No 'metrics' key or missing 'combined_score' in "
               "best_program_info.json — algorithm_summary.json will have "
               "empty/incomplete metrics and WILL FAIL schema validation. "
               "Use --strict to enforce schema validity.")
        print(msg, file=sys.stderr)
        if getattr(args, 'strict', False):
            return _output("error", 1,
                           errors=["metrics.combined_score missing in "
                                   "best_program_info.json (required by schema). "
                                   "Cannot produce schema-valid artifact in --strict mode."])

    # Build summary — the FILE ARTIFACT written to workspace/
    block_hash = hashlib.sha256(block.encode()).hexdigest()

    # Read mapping artifact version
    mapping_version = "unknown"
    if MAPPING_PATH.exists():
        try:
            _mapping_content = MAPPING_PATH.read_text()
        except OSError:
            _mapping_content = ""
        ver_match = re.search(r'\*\*Version:\*\*\s*(\S+)', _mapping_content)
        if ver_match:
            raw_version = ver_match.group(1)
            if re.fullmatch(r'\d+\.\d+', raw_version):
                mapping_version = raw_version
            else:
                print(f"WARNING: Mapping artifact version '{raw_version}' does not match "
                      f"expected MAJOR.MINOR format. mapping_artifact_version will be 'unknown'.",
                      file=sys.stderr)
        else:
            print("WARNING: Could not parse mapping artifact version — "
                  "mapping_artifact_version will be 'unknown'. "
                  "Ensure mapping has a '**Version:** X.Y' line.",
                  file=sys.stderr)

    # Composite signals
    composites = []
    for method, fields in METHOD_EXPANSIONS.items():
        found_constituents = [f for f in fields if any(s["name"] == f for s in signals)]
        if found_constituents:
            composites.append({
                "name": method,
                "constituents": found_constituents,
                "formula": "sum",
            })

    fidelity_checked = MAPPING_PATH.exists()

    summary = {
        "algorithm_name": "blis_weighted_scoring",
        "evolve_block_source": f"routing/best_program.py:{line_range}",
        "evolve_block_content_hash": block_hash,
        "signals": signals,
        "composite_signals": composites,
        "metrics": metrics,
        "scope_validation_passed": scope_ok,
        "mapping_artifact_version": mapping_version,
        "fidelity_checked": fidelity_checked,
    }

    # Write to workspace
    WORKSPACE.mkdir(exist_ok=True)
    summary_path = WORKSPACE / "algorithm_summary.json"
    try:
        summary_path.write_text(json.dumps(summary, indent=2) + "\n")
    except OSError as e:
        return _output("error", 2, errors=[
            f"Failed to write {summary_path}: {e}."])

    all_errors = scope_errors + fidelity_errors
    if not scope_ok:
        return _output("error", 1,
                        output_type="operational_report",
                        artifact_path=str(summary_path),
                        algorithm_name=summary["algorithm_name"],
                        signal_count=len(signals),
                        errors=all_errors)

    return _output("ok", 0,
                    output_type="operational_report",
                    artifact_path=str(summary_path),
                    algorithm_name=summary["algorithm_name"],
                    signal_count=len(signals),
                    errors=all_errors)


def cmd_validate_mapping(args: argparse.Namespace) -> int:
    """Validate mapping artifact completeness against algorithm summary."""
    if not MAPPING_PATH.exists():
        return _output("error", 2, errors=[f"Mapping artifact not found: {MAPPING_PATH}"],
                        mapping_complete=False, missing_signals=[], extra_signals=[], stale_commit=False)

    summary_path = args.summary if hasattr(args, "summary") and args.summary else (
        WORKSPACE / "algorithm_summary.json"
    )
    summary_path = Path(summary_path).resolve()

    if not summary_path.is_relative_to(REPO_ROOT):
        return _output("error", 2, errors=[
            f"Summary path '{summary_path}' is outside repository root '{REPO_ROOT}'."],
                        mapping_complete=False, missing_signals=[], extra_signals=[], stale_commit=False)

    if not summary_path.exists():
        return _output("error", 2,
                        errors=[f"Algorithm summary not found: {summary_path}. Run 'extract' first."],
                        mapping_complete=False, missing_signals=[], extra_signals=[], stale_commit=False)

    MAX_SUMMARY_SIZE = 10 * 1024 * 1024
    try:
        if summary_path.stat().st_size > MAX_SUMMARY_SIZE:
            return _output("error", 2,
                            errors=[f"Algorithm summary exceeds {MAX_SUMMARY_SIZE} bytes — refusing to read."],
                            mapping_complete=False, missing_signals=[], extra_signals=[], stale_commit=False)
    except OSError as e:
        return _output("error", 2,
                        errors=[f"Failed to stat algorithm summary: {e}"],
                        mapping_complete=False, missing_signals=[], extra_signals=[], stale_commit=False)

    try:
        summary = json.loads(summary_path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        return _output("error", 2,
                        errors=[f"Failed to read/parse algorithm summary: {e}"],
                        mapping_complete=False, missing_signals=[], extra_signals=[], stale_commit=False)

    try:
        extracted_names = {sig['name'] for sig in summary.get('signals', [])}
    except (TypeError, KeyError) as e:
        return _output("error", 2,
                        errors=[f"Malformed algorithm summary — 'signals' is not a valid list of dicts: {e}"],
                        mapping_complete=False, missing_signals=[], extra_signals=[], stale_commit=False)

    MAX_MAPPING_SIZE = 10 * 1024 * 1024
    try:
        _mapping_size = MAPPING_PATH.stat().st_size
    except OSError as e:
        return _output("error", 2,
                        errors=[f"Failed to stat mapping artifact: {e}"],
                        mapping_complete=False, missing_signals=[], extra_signals=[], stale_commit=False)
    if _mapping_size > MAX_MAPPING_SIZE:
        return _output("error", 2,
                        errors=[f"Mapping artifact exceeds {MAX_MAPPING_SIZE} bytes — refusing to read."],
                        mapping_complete=False, missing_signals=[], extra_signals=[], stale_commit=False)

    try:
        content = MAPPING_PATH.read_text()
    except OSError as e:
        return _output("error", 2,
                        errors=[f"Failed to read mapping artifact: {e}"],
                        mapping_complete=False, missing_signals=[], extra_signals=[], stale_commit=False)

    # Detect malformed mapping artifact
    if '|' not in content or not re.search(r'^\|.*\|.*\|', content, re.MULTILINE):
        return _output("error", 1,
                        errors=["Malformed mapping artifact: no Markdown table found. "
                                "Expected pipe-delimited table rows."],
                        mapping_complete=False, missing_signals=[], extra_signals=[], stale_commit=True)

    # Check each extracted signal has a mapping entry (allow optional parenthetical annotation)
    missing = [name for name in sorted(extracted_names)
               if not re.search(rf'\|\s*{re.escape(name)}(?:\s*\([^)]*\))?\s*\|', content)]

    # Check for extra signals in mapping not present in extract
    mapping_signals = set()
    mapping_signal_counts: dict[str, int] = {}
    for row_match in re.finditer(r'^\|\s*(\w+)(?:\s*\([^)]*\))?\s*\|', content, re.MULTILINE):
        candidate = row_match.group(1)
        if candidate in ("Sim", "Signal", "Composite"):
            continue
        mapping_signal_counts[candidate] = mapping_signal_counts.get(candidate, 0) + 1
        mapping_signals.add(candidate)
    # Include composite signal names so they're not flagged as extra
    composite_names = {c['name'] for c in summary.get('composite_signals', [])
                       if isinstance(c, dict) and 'name' in c}
    known_names = extracted_names | composite_names
    extra = sorted(mapping_signals - known_names)
    duplicates = sorted(k for k, v in mapping_signal_counts.items() if v > 1)

    # Check commit hash presence
    has_commit = bool(re.search(
        r'(?:commit[_ ]hash|pinned[_ ]commit[_ ]hash)[:\s*]*([0-9a-f]{7,40})',
        content, re.IGNORECASE,
    ))

    mapping_complete = (len(missing) == 0 and len(extra) == 0
                        and len(duplicates) == 0 and has_commit)
    errors = []
    if missing:
        errors.append(f"Missing signal mappings: {', '.join(missing)}")
    if extra:
        errors.append(
            f"Extra signals in mapping not found in extract: {', '.join(extra)}. "
            f"Resolution: First check if these signals are accessed in the EVOLVE-BLOCK. "
            f"If not, remove the stale rows from the mapping artifact. "
            f"If they are accessed, the extract regex has a gap — fix _extract_signals."
        )
    if duplicates:
        dup_details = [f"{name} ({mapping_signal_counts[name]} rows)" for name in duplicates]
        errors.append(
            f"Duplicate signal rows found: {', '.join(dup_details)}. "
            f"Each signal MUST appear exactly once in the mapping table."
        )
    if not has_commit:
        errors.append("No commit hash found in mapping artifact")

    # Detect production metric overlap (double-counting risk)
    prod_metric_signals: dict[str, list[str]] = {}
    for sig_name in extracted_names:
        prod_match = re.search(
            rf'\|\s*{re.escape(sig_name)}(?:\s*\([^)]*\))?\s*\|(?:[^|]*\|){{2}}([^|]+)\|',
            content, re.IGNORECASE,
        )
        if prod_match:
            prod_metric = prod_match.group(1).strip()
            prod_metric_signals.setdefault(prod_metric, []).append(sig_name)
    for prod_metric, sigs in prod_metric_signals.items():
        if len(sigs) > 1:
            print(
                f"WARNING: Double-counting risk — signals {', '.join(sigs)} "
                f"both map to production metric '{prod_metric}'.",
                file=sys.stderr,
            )

    status = "ok" if mapping_complete else "error"
    exit_code = 0 if mapping_complete else 1
    return _output(status, exit_code,
                    output_type="mapping_validation",
                    mapping_complete=mapping_complete,
                    missing_signals=missing,
                    extra_signals=extra,
                    stale_commit=not has_commit,
                    errors=errors)


def cmd_validate_schema(args: argparse.Namespace) -> int:
    """Validate a workspace artifact against its JSON Schema."""
    from schema_validator import validate_artifact, load_schema

    artifact_path = Path(args.artifact_path).resolve()
    if not artifact_path.is_relative_to(REPO_ROOT):
        return _output("error", 2, errors=[
            f"Artifact path '{artifact_path}' is outside repository root '{REPO_ROOT}'."])
    if not artifact_path.exists():
        return _output("error", 2, errors=[f"Artifact not found: {artifact_path}"])

    stem = artifact_path.stem
    schema_path = (SCHEMAS_DIR / f"{stem}.schema.json").resolve()
    if not schema_path.is_relative_to(SCHEMAS_DIR):
        return _output("error", 2, errors=[
            f"Schema path '{schema_path}' resolves outside schemas directory '{SCHEMAS_DIR}'."])
    if not schema_path.exists():
        return _output("error", 2, errors=[f"Schema not found: {schema_path}"])

    try:
        schema = load_schema(schema_path)
    except (json.JSONDecodeError, OSError) as e:
        return _output("error", 2, errors=[f"Failed to load schema: {e}"])

    try:
        data = json.loads(artifact_path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        return _output("error", 2, errors=[f"Failed to load artifact: {e}"],
                        schema_path=str(schema_path), artifact_path=str(artifact_path),
                        violations=[])

    errors = validate_artifact(data, schema)
    violations = [{"field": e.split(":")[0].strip(), "message": e} for e in errors]

    if errors:
        return _output("error", 1,
                        output_type="schema_validation",
                        schema_path=str(schema_path),
                        artifact_path=str(artifact_path),
                        violations=violations,
                        errors=errors)

    return _output("ok", 0,
                    output_type="schema_validation",
                    schema_path=str(schema_path),
                    artifact_path=str(artifact_path),
                    violations=[])


def main():
    if sys.version_info < (3, 9):
        print("ERROR: transfer_cli.py requires Python >= 3.9 "
              f"(running {sys.version_info.major}.{sys.version_info.minor})",
              file=sys.stderr)
        sys.exit(2)
    parser = argparse.ArgumentParser(
        description="Transfer pipeline CLI — mechanical support for sim-to-production transfer",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # extract
    p_extract = subparsers.add_parser("extract",
        help="Parse EVOLVE-BLOCK, produce algorithm_summary.json")
    p_extract.add_argument("routing_dir", help="Path to routing/ directory")
    p_extract.add_argument("--strict", action="store_true",
        help="CI mode: require mapping artifact, enforce fidelity checks, "
             "fail if signal count < minimum threshold")
    p_extract.set_defaults(func=cmd_extract)

    # validate-mapping
    p_mapping = subparsers.add_parser("validate-mapping", help="Check mapping artifact completeness")
    p_mapping.add_argument("--summary", help="Path to algorithm_summary.json (default: workspace/)")
    p_mapping.set_defaults(func=cmd_validate_mapping)

    # validate-schema
    p_schema = subparsers.add_parser("validate-schema", help="Validate workspace artifact against schema")
    p_schema.add_argument("artifact_path", help="Path to workspace JSON artifact")
    p_schema.set_defaults(func=cmd_validate_schema)

    args = parser.parse_args()
    exit_code = args.func(args)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
