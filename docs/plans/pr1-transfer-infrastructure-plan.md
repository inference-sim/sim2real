# PR1: Transfer Infrastructure + Mapping Artifact — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Establish the foundational directory structure, JSON schemas, validation utilities, and the signal mapping artifact that all later transfer pipeline PRs depend on.

**The problem today:** OpenEvolve can discover adaptive routing algorithms via BLIS, but there is no infrastructure to transfer those algorithms to a production system. There is no `docs/transfer/` directory, no `openevolve/transfer/` package, no schema definitions for inter-stage artifacts, and no documented mapping between BLIS signals and llm-d equivalents. Without this foundation, no pipeline stage can be implemented.

**What this PR adds:**
1. **Transfer directory tree** — `docs/transfer/` with a README and `docs/transfer/schemas/` containing JSON Schema files for the two key inter-stage artifacts (`algorithm_summary` and `signal_coverage`)
2. **Schema validator** — `openevolve/transfer/schema_validator.py` that validates JSON artifacts against their schemas, used by every pipeline stage that consumes an artifact
3. **Mapping artifact** — `docs/transfer/blis_to_llmd_mapping.md` documenting every BLIS signal, its llm-d equivalent, fidelity rating, translation strategy, staleness window, and the BLIS workload generator config schema
4. **CLAUDE.md update** — Transfer pipeline section so contributors know about the new infrastructure

**Why this matters:** This is Milestone 1's first PR. Every subsequent PR (PR2–PR7) imports the schema validator and references the mapping artifact. Without this foundation, no pipeline stage can be built.

**Architecture:** New `openevolve/transfer/` Python package with `__init__.py` and `schema_validator.py`. New `docs/transfer/` directory tree with JSON Schema files and the mapping artifact markdown. Follows existing package patterns (absolute imports, `__all__` exports). No new external dependencies (uses stdlib `json`, `jsonschema` if available, else manual validation).

**Source:** PR1 in `docs/plans/2026-03-02-blis-to-llmd-transfer-macro-plan-v2.md`

**Closes:** N/A — source is macro plan, no linked issues

**Behavioral Contracts:** See Part 1, Section B below

---

## Part 1: Design Validation

### A) Executive Summary

This PR creates the transfer pipeline's foundational infrastructure:

- **`docs/transfer/`** directory with README, JSON Schema files for `algorithm_summary` and `signal_coverage` artifacts, and the `blis_to_llmd_mapping.md` mapping artifact
- **`openevolve/transfer/`** Python package with a schema validator module
- **CLAUDE.md** updated with transfer pipeline section

**Where it fits:** This is the first PR in a 7-PR series. PR2 (scorer template) and PR3 (extraction + translation) both depend on artifacts created here. No existing code is modified — this is purely additive.

**Adjacent blocks:** The schema validator will be consumed by PR3's extraction module and PR4's generation module. The mapping artifact will be consumed by PR3's translation module.

**DEVIATION flags:** None from Phase 0 inspection.

### B) Behavioral Contracts

**Positive Contracts:**

BC-1: Schema file validity
- GIVEN the JSON Schema files in `docs/transfer/schemas/`
- WHEN loaded with Python's `json.loads()`
- THEN each file parses as valid JSON and contains `"type"`, `"properties"`, and `"required"` keys
- MECHANISM: Standard JSON Schema draft-07 format

BC-2: Algorithm summary schema completeness
- GIVEN the `algorithm_summary.schema.json` schema
- WHEN inspected for required fields
- THEN it requires: `evolve_block_code`, `evolve_block_file`, `hypothesis_results`, `workload_results`, `signals_used`, `scope_verdict`, `source_experiment`
- MECHANISM: The `"required"` array in the schema lists all fields from the design doc's Stage 1 output specification

BC-3: Signal coverage schema completeness
- GIVEN the `signal_coverage.schema.json` schema
- WHEN inspected for required fields
- THEN it requires: `signals`, `matched_workload`, `benchmark_workloads`, `scorer_overlap`, `has_low_fidelity_critical`, `mapping_artifact_version`
- MECHANISM: The `"required"` array matches the design doc's Stage 2 output specification

BC-4: Validator accepts valid JSON
- GIVEN a JSON object conforming to `algorithm_summary.schema.json`
- WHEN passed to `validate_artifact(data, "algorithm_summary")`
- THEN the function returns without raising an exception
- MECHANISM: Field-by-field validation against schema constraints

BC-5: Validator rejects missing required fields
- GIVEN a JSON object missing `evolve_block_code` (a required field)
- WHEN passed to `validate_artifact(data, "algorithm_summary")`
- THEN the function raises `SchemaValidationError` with a message naming the missing field
- MECHANISM: Check each required field exists and is not None

BC-6: Validator rejects invalid enum values
- GIVEN a JSON object with `scope_verdict` set to `"invalid_value"`
- WHEN passed to `validate_artifact(data, "algorithm_summary")`
- THEN the function raises `SchemaValidationError` naming the field and allowed values
- MECHANISM: Enum fields validated against allowed values list

BC-7: Validator rejects empty required lists
- GIVEN a JSON object with `signals_used` set to `[]`
- WHEN passed to `validate_artifact(data, "algorithm_summary")`
- THEN the function raises `SchemaValidationError` stating the list must be non-empty
- MECHANISM: `minItems: 1` constraint on list fields per design doc

BC-8: Mapping artifact structure
- GIVEN `docs/transfer/blis_to_llmd_mapping.md`
- WHEN parsed for signal entries
- THEN it contains at least one complete signal mapping entry with all fields: BLIS signal name, llm-d equivalent, fidelity rating, fidelity justification, translation strategy, staleness window, and source group
- MECHANISM: Structured markdown table with all columns populated

**Negative Contracts:**

BC-9: No external dependencies
- GIVEN the `openevolve/transfer/` package
- WHEN inspecting its imports
- THEN it imports only Python stdlib modules (`json`, `pathlib`, `os`, `re`) — no `jsonschema` or other third-party packages
- MECHANISM: Manual validation (no `pip install` needed)

BC-10: No runtime impact
- GIVEN the existing OpenEvolve test suite
- WHEN running `python -m unittest discover tests`
- THEN all existing tests continue to pass unchanged
- MECHANISM: PR is purely additive — no existing files modified except CLAUDE.md

**Error Handling Contracts:**

BC-11: Schema file not found
- GIVEN a schema name that doesn't exist (e.g., `"nonexistent"`)
- WHEN passed to `validate_artifact(data, "nonexistent")`
- THEN the function raises `SchemaValidationError` with message "Unknown schema: nonexistent"
- MECHANISM: Schema name lookup against known schema registry

BC-12: Null field in required position
- GIVEN a JSON object with `evolve_block_code` set to `None`
- WHEN passed to `validate_artifact(data, "algorithm_summary")`
- THEN the function raises `SchemaValidationError` stating the field must not be null
- MECHANISM: Explicit null check on required fields

### C) Component Interaction

```
┌──────────────────────────────────┐
│ docs/transfer/                   │
│  ├── README.md                   │
│  ├── schemas/                    │
│  │   ├── algorithm_summary.schema.json  │
│  │   └── signal_coverage.schema.json    │
│  └── blis_to_llmd_mapping.md     │
└──────────────────────────────────┘
         │ consumed by
         ▼
┌──────────────────────────────────┐
│ openevolve/transfer/             │
│  ├── __init__.py                 │
│  └── schema_validator.py         │
│     validate_artifact(data, name)│
│     SchemaValidationError        │
└──────────────────────────────────┘
         │ consumed by (future PRs)
         ▼
┌──────────────────────────────────┐
│ PR3: extract.py, translate.py    │
│ PR4: generate.py, test_runner.py │
│ PR6: equivalence.py              │
└──────────────────────────────────┘
```

**API Contracts:**
- `validate_artifact(data: dict, schema_name: str) -> None` — raises `SchemaValidationError` on failure
- `SchemaValidationError(message: str)` — extends `ValueError`
- `load_schema(schema_name: str) -> dict` — loads and returns parsed JSON Schema

**State Changes:** None. All functions are stateless. Schema files are read-only.

**Extension Friction:** Adding a new schema = 1 JSON file in `docs/transfer/schemas/` + 1 entry in the schema registry dict in `schema_validator.py` + 1 test. Low friction (2 files).

### D) Deviation Log

| Source Says | Micro Plan Does | Reason |
|-------------|-----------------|--------|
| Macro plan says `tests/test_transfer_schemas.py` | Plan uses `tests/test_transfer_schemas.py` (same) | No deviation |
| Design doc has BLOCKING TODO for `metrics.workload` schema | Schema uses `object` type for `workload_results[].results` with a note that concrete fields TBD | DEFERRAL: Concrete schema depends on having example experiment output; `object` type allows any structure and will be tightened in PR3 when fixtures are available |
| Macro plan mentions per-workload YAML config schema in mapping artifact | Mapping artifact documents YAML config structure based on actual workload files | No deviation |

### E) Review Guide

**The tricky part:** The `algorithm_summary.schema.json` must match the design doc's Stage 1 output spec exactly. The `workload_results[].results` field is typed as `object` (not fully specified) because the concrete `metrics.workload` schema is a BLOCKING TODO in the design doc. This is intentional — PR3 will tighten it once example experiment output is available.

**What to scrutinize:** BC-5 through BC-7 and BC-12 (rejection behavior) — these are the validator's core safety guarantees.

**What's safe to skim:** The mapping artifact content (it will be refined as we gain llm-d access). The README is informational.

**Known debt:** The mapping artifact's llm-d entries are UNVERIFIED (no repo access). Concrete Go types will be filled in when llm-d repos are inspected for PR2.

---

## Part 2: Executable Implementation

### F) Implementation Overview

**Files to create:**
- `docs/transfer/README.md` — overview of transfer pipeline artifacts
- `docs/transfer/schemas/algorithm_summary.schema.json` — JSON Schema for Stage 1 output
- `docs/transfer/schemas/signal_coverage.schema.json` — JSON Schema for Stage 2 output
- `docs/transfer/blis_to_llmd_mapping.md` — signal mapping artifact v1.0
- `openevolve/transfer/__init__.py` — package init with exports
- `openevolve/transfer/schema_validator.py` — validation logic
- `tests/test_transfer_schemas.py` — tests for schemas and validator

**Files to modify:**
- `CLAUDE.md` — add transfer pipeline section

**Key decisions:**
- No `jsonschema` dependency — implement validation manually using stdlib `json` to keep zero external deps
- Schema files use JSON Schema draft-07 format but are validated manually (not via `jsonschema` library)
- `workload_results[].results` typed as `object` (not fully specified) per design doc BLOCKING TODO

**Confirmation:** No dead code. Schema validator is exercised by tests. Schemas are consumed by validator. Mapping artifact consumed by PR3. All paths exercisable.

### G) Task Breakdown

---

### Task 1: Create transfer directory structure and README

**Contracts Implemented:** (infrastructure — no behavioral contract, enables all others)

**Files:**
- Create: `docs/transfer/README.md`
- Create: `docs/transfer/schemas/` (directory)

**Step 1: Create directory structure and README**

Context: We need the `docs/transfer/` directory tree before any artifacts can be placed in it.

```bash
mkdir -p docs/transfer/schemas
```

In `docs/transfer/README.md`:
```markdown
# Transfer Pipeline Artifacts

This directory contains artifacts for the BLIS-to-llm-d algorithm transfer pipeline.

## Contents

- `schemas/` — JSON Schema files for inter-stage artifact validation
  - `algorithm_summary.schema.json` — Stage 1 output (extraction)
  - `signal_coverage.schema.json` — Stage 2 output (translation)
- `blis_to_llmd_mapping.md` — Signal mapping between BLIS and llm-d (v1.0)

## Overview

The transfer pipeline takes an evolved routing algorithm from OpenEvolve/BLIS
and translates it into a production scorer plugin for llm-d-inference-scheduler.

See `docs/plans/2026-03-02-blis-to-llmd-transfer-macro-plan-v2.md` for the full
pipeline design and PR plan.

## Artifact Versioning

Artifacts use `major.minor` versioning:
- **Major bump**: Breaking change requiring pipeline update
- **Minor bump**: Additive/compatible change

The pipeline validates major version compatibility at Stage 1.
```

**Step 2: Verify directory exists**

Run: `ls -la docs/transfer/ && ls -la docs/transfer/schemas/`
Expected: Both directories exist, README.md present

**Step 3: Commit**

```bash
git add docs/transfer/README.md
git commit -m "$(cat <<'EOF'
docs: create transfer pipeline directory structure

Create docs/transfer/ with README and schemas/ subdirectory.
This is the foundation for all transfer pipeline artifacts.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Create algorithm_summary JSON Schema

**Contracts Implemented:** BC-1, BC-2

**Files:**
- Create: `docs/transfer/schemas/algorithm_summary.schema.json`

**Step 1: Write the schema file**

Context: This schema defines the Stage 1 output artifact. Fields come from the design doc's "Stage Artifact Schemas" section. The `workload_results[].results` field is typed as `object` because the concrete schema is a BLOCKING TODO.

In `docs/transfer/schemas/algorithm_summary.schema.json`:
```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "Algorithm Summary",
  "description": "Stage 1 output: extracted algorithm with metadata from a BLIS experiment",
  "type": "object",
  "required": [
    "evolve_block_code",
    "evolve_block_file",
    "hypothesis_results",
    "workload_results",
    "signals_used",
    "scope_verdict",
    "source_experiment"
  ],
  "properties": {
    "evolve_block_code": {
      "type": "string",
      "minLength": 1,
      "description": "Raw EVOLVE-BLOCK source code extracted from the best program"
    },
    "evolve_block_file": {
      "type": "string",
      "minLength": 1,
      "description": "Path to the source file containing the EVOLVE-BLOCK"
    },
    "hypothesis_results": {
      "type": "object",
      "description": "Hypothesis name, test results, and verdict from the experiment"
    },
    "workload_results": {
      "type": "array",
      "minItems": 1,
      "description": "Per-workload configuration and results from the BLIS experiment",
      "items": {
        "type": "object",
        "required": ["workload_name", "workload_params", "results", "config_file"],
        "properties": {
          "workload_name": {
            "type": "string",
            "minLength": 1,
            "description": "BLIS workload identifier (e.g., 'cache_warmup')"
          },
          "workload_params": {
            "type": "object",
            "description": "BLIS workload generator config parameters from the YAML file"
          },
          "results": {
            "type": "object",
            "description": "Experiment results for this workload from metrics.workload. Concrete schema TBD — see design doc BLOCKING TODO."
          },
          "config_file": {
            "type": "string",
            "minLength": 1,
            "description": "Path to the source workload YAML config file"
          }
        }
      }
    },
    "signals_used": {
      "type": "array",
      "minItems": 1,
      "description": "Signal names referenced in the EVOLVE-BLOCK",
      "items": {
        "type": "string",
        "minLength": 1
      }
    },
    "scope_verdict": {
      "type": "string",
      "enum": ["pass", "marginal", "reject"],
      "description": "Scope validation result: pass (linear), marginal (piecewise-linear, user confirmed), reject (non-linear, pipeline halts)"
    },
    "source_experiment": {
      "type": "string",
      "minLength": 1,
      "description": "Path to the experiment output directory"
    }
  }
}
```

**Step 2: Verify JSON is valid**

Run: `python -c "import json; json.load(open('docs/transfer/schemas/algorithm_summary.schema.json')); print('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
git add docs/transfer/schemas/algorithm_summary.schema.json
git commit -m "$(cat <<'EOF'
docs: add algorithm_summary JSON Schema (BC-1, BC-2)

Define Stage 1 output schema with all required fields from design doc.
workload_results[].results typed as object (concrete schema TBD).

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Create signal_coverage JSON Schema

**Contracts Implemented:** BC-1, BC-3

**Files:**
- Create: `docs/transfer/schemas/signal_coverage.schema.json`

**Step 1: Write the schema file**

Context: This schema defines the Stage 2 output artifact. Fields come from the design doc's "Stage Artifact Schemas" section.

In `docs/transfer/schemas/signal_coverage.schema.json`:
```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "Signal Coverage Report",
  "description": "Stage 2 output: signal mapping analysis with fidelity ratings and branch classification",
  "type": "object",
  "required": [
    "signals",
    "matched_workload",
    "benchmark_workloads",
    "scorer_overlap",
    "has_low_fidelity_critical",
    "mapping_artifact_version"
  ],
  "properties": {
    "signals": {
      "type": "array",
      "minItems": 1,
      "description": "Per-signal mapping analysis",
      "items": {
        "type": "object",
        "required": [
          "blis_signal",
          "fidelity",
          "branches",
          "translation_strategy"
        ],
        "properties": {
          "blis_signal": {
            "type": "string",
            "minLength": 1,
            "description": "Signal category name from the mapping artifact"
          },
          "fidelity": {
            "type": "string",
            "enum": ["High", "Medium", "Low", "Upgrade"],
            "description": "Fidelity rating assigned from mapping artifact"
          },
          "fidelity_justification": {
            "type": "string",
            "description": "Explanation of fidelity rating. Required for Medium, Low, Upgrade."
          },
          "branches": {
            "type": "array",
            "description": "Code branches referencing this signal",
            "items": {
              "type": "object",
              "required": ["line", "code_snippet", "classification", "classification_reason"],
              "properties": {
                "line": {
                  "type": "integer",
                  "description": "Line number in the EVOLVE-BLOCK"
                },
                "code_snippet": {
                  "type": "string",
                  "description": "Code fragment at this branch"
                },
                "classification": {
                  "type": "string",
                  "enum": ["critical", "non-critical"],
                  "description": "Branch criticality classification"
                },
                "classification_reason": {
                  "type": "string",
                  "enum": [
                    "default_path",
                    "score_dominant",
                    "all_request",
                    "nested_critical",
                    "narrow_with_fallback",
                    "conservative_tiebreak"
                  ],
                  "description": "Reason for classification"
                }
              }
            }
          },
          "translation_strategy": {
            "type": "string",
            "enum": ["direct_map", "proxy", "stub", "drop"],
            "description": "How this signal is translated to llm-d"
          }
        }
      }
    },
    "matched_workload": {
      "type": "object",
      "required": ["blis_workload_name", "workload_params"],
      "description": "Primary mechanism workload",
      "properties": {
        "blis_workload_name": {
          "type": "string",
          "minLength": 1
        },
        "workload_params": {
          "type": "object"
        },
        "benchmark_config_path": {
          "type": "string",
          "description": "Populated by Stage 3"
        }
      }
    },
    "benchmark_workloads": {
      "type": "array",
      "minItems": 1,
      "description": "All BLIS workloads with benchmark config info",
      "items": {
        "type": "object",
        "required": ["blis_workload_name", "workload_params", "is_matched"],
        "properties": {
          "blis_workload_name": {
            "type": "string",
            "minLength": 1
          },
          "workload_params": {
            "type": "object"
          },
          "benchmark_config_path": {
            "type": "string",
            "description": "Populated by Stage 3"
          },
          "is_matched": {
            "type": "boolean"
          }
        }
      }
    },
    "scorer_overlap": {
      "type": "array",
      "description": "Existing scorers sharing signals with the new scorer",
      "items": {
        "type": "object",
        "required": ["scorer_name", "shared_signals", "action"],
        "properties": {
          "scorer_name": {
            "type": "string",
            "minLength": 1
          },
          "shared_signals": {
            "type": "array",
            "items": {"type": "string"}
          },
          "action": {
            "type": "string",
            "enum": ["disable", "keep"]
          }
        }
      }
    },
    "has_low_fidelity_critical": {
      "type": "boolean",
      "description": "True if any critical branch uses a Low-fidelity signal"
    },
    "mapping_artifact_version": {
      "type": "string",
      "minLength": 1,
      "description": "Version of blis_to_llmd_mapping.md used for fidelity ratings"
    }
  }
}
```

**Step 2: Verify JSON is valid**

Run: `python -c "import json; json.load(open('docs/transfer/schemas/signal_coverage.schema.json')); print('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
git add docs/transfer/schemas/signal_coverage.schema.json
git commit -m "$(cat <<'EOF'
docs: add signal_coverage JSON Schema (BC-1, BC-3)

Define Stage 2 output schema with signal entries, branch classification,
workload matching, scorer overlap, and fidelity tracking.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Create schema validator module

**Contracts Implemented:** BC-4, BC-5, BC-6, BC-7, BC-9, BC-11, BC-12

**Files:**
- Create: `openevolve/transfer/__init__.py`
- Create: `openevolve/transfer/schema_validator.py`
- Create: `tests/test_transfer_schemas.py`

**Step 1: Write failing tests**

Context: We test all validation behaviors before implementing. Tests cover: valid input accepted, missing required fields rejected, invalid enum rejected, empty list rejected, null field rejected, unknown schema rejected.

In `tests/test_transfer_schemas.py`:
```python
"""Tests for transfer pipeline schema validation (BC-1 through BC-12)."""

import json
import os
import unittest


class TestSchemaFilesValid(unittest.TestCase):
    """BC-1: Schema files parse as valid JSON with required keys."""

    def setUp(self):
        self.schema_dir = os.path.join(
            os.path.dirname(__file__),
            "..",
            "docs",
            "transfer",
            "schemas",
        )

    def test_algorithm_summary_schema_parses(self):
        path = os.path.join(self.schema_dir, "algorithm_summary.schema.json")
        with open(path) as f:
            schema = json.load(f)
        self.assertIn("type", schema)
        self.assertIn("properties", schema)
        self.assertIn("required", schema)

    def test_signal_coverage_schema_parses(self):
        path = os.path.join(self.schema_dir, "signal_coverage.schema.json")
        with open(path) as f:
            schema = json.load(f)
        self.assertIn("type", schema)
        self.assertIn("properties", schema)
        self.assertIn("required", schema)


class TestAlgorithmSummarySchemaCompleteness(unittest.TestCase):
    """BC-2: Algorithm summary schema has all required fields."""

    def setUp(self):
        schema_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "docs",
            "transfer",
            "schemas",
            "algorithm_summary.schema.json",
        )
        with open(schema_path) as f:
            self.schema = json.load(f)

    def test_required_fields_present(self):
        expected = [
            "evolve_block_code",
            "evolve_block_file",
            "hypothesis_results",
            "workload_results",
            "signals_used",
            "scope_verdict",
            "source_experiment",
        ]
        for field in expected:
            self.assertIn(field, self.schema["required"])


class TestSignalCoverageSchemaCompleteness(unittest.TestCase):
    """BC-3: Signal coverage schema has all required fields."""

    def setUp(self):
        schema_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "docs",
            "transfer",
            "schemas",
            "signal_coverage.schema.json",
        )
        with open(schema_path) as f:
            self.schema = json.load(f)

    def test_required_fields_present(self):
        expected = [
            "signals",
            "matched_workload",
            "benchmark_workloads",
            "scorer_overlap",
            "has_low_fidelity_critical",
            "mapping_artifact_version",
        ]
        for field in expected:
            self.assertIn(field, self.schema["required"])


def _make_valid_algorithm_summary():
    """Factory for a minimal valid algorithm_summary artifact."""
    return {
        "evolve_block_code": "scores[snap.ID] += s * ws.weights[i]",
        "evolve_block_file": "examples/blis_router/initial_program.py",
        "hypothesis_results": {"name": "test", "verdict": "confirmed"},
        "workload_results": [
            {
                "workload_name": "cache_warmup",
                "workload_params": {"rate": 1000, "num_requests": 5000},
                "results": {"e2e_mean_ms": 45.2},
                "config_file": "workload_v2_cache_warmup.yaml",
            }
        ],
        "signals_used": ["queue_depth", "kv_utilization"],
        "scope_verdict": "pass",
        "source_experiment": "examples/blis_router/openevolve_output",
    }


def _make_valid_signal_coverage():
    """Factory for a minimal valid signal_coverage artifact."""
    return {
        "signals": [
            {
                "blis_signal": "queue_depth",
                "fidelity": "High",
                "branches": [
                    {
                        "line": 5,
                        "code_snippet": "load := snap.QueueDepth",
                        "classification": "critical",
                        "classification_reason": "all_request",
                    }
                ],
                "translation_strategy": "direct_map",
            }
        ],
        "matched_workload": {
            "blis_workload_name": "cache_warmup",
            "workload_params": {"rate": 1000},
        },
        "benchmark_workloads": [
            {
                "blis_workload_name": "cache_warmup",
                "workload_params": {"rate": 1000},
                "is_matched": True,
            }
        ],
        "scorer_overlap": [],
        "has_low_fidelity_critical": False,
        "mapping_artifact_version": "1.0",
    }


class TestValidatorAcceptsValid(unittest.TestCase):
    """BC-4: Validator accepts valid JSON artifacts."""

    def test_valid_algorithm_summary_accepted(self):
        from openevolve.transfer.schema_validator import validate_artifact

        data = _make_valid_algorithm_summary()
        validate_artifact(data, "algorithm_summary")  # Should not raise

    def test_valid_signal_coverage_accepted(self):
        from openevolve.transfer.schema_validator import validate_artifact

        data = _make_valid_signal_coverage()
        validate_artifact(data, "signal_coverage")  # Should not raise


class TestValidatorRejectsMissingFields(unittest.TestCase):
    """BC-5: Validator rejects missing required fields."""

    def test_missing_evolve_block_code(self):
        from openevolve.transfer.schema_validator import (
            SchemaValidationError,
            validate_artifact,
        )

        data = _make_valid_algorithm_summary()
        del data["evolve_block_code"]
        with self.assertRaises(SchemaValidationError) as ctx:
            validate_artifact(data, "algorithm_summary")
        self.assertIn("evolve_block_code", str(ctx.exception))

    def test_missing_signals(self):
        from openevolve.transfer.schema_validator import (
            SchemaValidationError,
            validate_artifact,
        )

        data = _make_valid_signal_coverage()
        del data["signals"]
        with self.assertRaises(SchemaValidationError) as ctx:
            validate_artifact(data, "signal_coverage")
        self.assertIn("signals", str(ctx.exception))


class TestValidatorRejectsInvalidEnum(unittest.TestCase):
    """BC-6: Validator rejects invalid enum values."""

    def test_invalid_scope_verdict(self):
        from openevolve.transfer.schema_validator import (
            SchemaValidationError,
            validate_artifact,
        )

        data = _make_valid_algorithm_summary()
        data["scope_verdict"] = "invalid_value"
        with self.assertRaises(SchemaValidationError) as ctx:
            validate_artifact(data, "algorithm_summary")
        self.assertIn("scope_verdict", str(ctx.exception))
        self.assertIn("invalid_value", str(ctx.exception))


class TestValidatorRejectsEmptyLists(unittest.TestCase):
    """BC-7: Validator rejects empty required lists."""

    def test_empty_signals_used(self):
        from openevolve.transfer.schema_validator import (
            SchemaValidationError,
            validate_artifact,
        )

        data = _make_valid_algorithm_summary()
        data["signals_used"] = []
        with self.assertRaises(SchemaValidationError) as ctx:
            validate_artifact(data, "algorithm_summary")
        self.assertIn("signals_used", str(ctx.exception))

    def test_empty_workload_results(self):
        from openevolve.transfer.schema_validator import (
            SchemaValidationError,
            validate_artifact,
        )

        data = _make_valid_algorithm_summary()
        data["workload_results"] = []
        with self.assertRaises(SchemaValidationError) as ctx:
            validate_artifact(data, "algorithm_summary")
        self.assertIn("workload_results", str(ctx.exception))


class TestValidatorRejectsUnknownSchema(unittest.TestCase):
    """BC-11: Validator rejects unknown schema names."""

    def test_unknown_schema(self):
        from openevolve.transfer.schema_validator import (
            SchemaValidationError,
            validate_artifact,
        )

        with self.assertRaises(SchemaValidationError) as ctx:
            validate_artifact({}, "nonexistent")
        self.assertIn("Unknown schema", str(ctx.exception))


class TestValidatorRejectsNullFields(unittest.TestCase):
    """BC-12: Validator rejects null values in required fields."""

    def test_null_evolve_block_code(self):
        from openevolve.transfer.schema_validator import (
            SchemaValidationError,
            validate_artifact,
        )

        data = _make_valid_algorithm_summary()
        data["evolve_block_code"] = None
        with self.assertRaises(SchemaValidationError) as ctx:
            validate_artifact(data, "algorithm_summary")
        self.assertIn("evolve_block_code", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
```

**Step 2: Run tests to verify they fail**

Run: `python -m unittest tests.test_transfer_schemas -v 2>&1 | head -30`
Expected: FAIL — `ModuleNotFoundError: No module named 'openevolve.transfer'`

**Step 3: Write minimal implementation**

Context: Implement the schema validator without any third-party dependency. Load schemas from the JSON files, validate required fields, enum constraints, and list minimums.

In `openevolve/transfer/__init__.py`:
```python
"""Transfer pipeline utilities for BLIS-to-llm-d algorithm transfer."""

from openevolve.transfer.schema_validator import (
    SchemaValidationError,
    load_schema,
    validate_artifact,
)

__all__ = ["SchemaValidationError", "load_schema", "validate_artifact"]
```

In `openevolve/transfer/schema_validator.py`:
```python
"""
Schema validation for transfer pipeline inter-stage artifacts.

Validates JSON artifacts against JSON Schema files in docs/transfer/schemas/
without requiring the jsonschema third-party package. Uses manual validation
against the schema's required fields, enum constraints, and minItems/minLength.
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional


class SchemaValidationError(ValueError):
    """Raised when an artifact fails schema validation."""

    pass


# Schema directory relative to repo root
_SCHEMA_DIR = os.path.join(
    os.path.dirname(__file__),
    "..",
    "..",
    "docs",
    "transfer",
    "schemas",
)

# Known schema names → file names
_SCHEMA_REGISTRY = {
    "algorithm_summary": "algorithm_summary.schema.json",
    "signal_coverage": "signal_coverage.schema.json",
}


def load_schema(schema_name: str) -> Dict[str, Any]:
    """Load a JSON Schema by name.

    Args:
        schema_name: One of the registered schema names
            (e.g., "algorithm_summary", "signal_coverage").

    Returns:
        Parsed JSON Schema as a dict.

    Raises:
        SchemaValidationError: If schema_name is unknown or file is missing.
    """
    if schema_name not in _SCHEMA_REGISTRY:
        raise SchemaValidationError(
            f"Unknown schema: {schema_name}. "
            f"Available: {', '.join(sorted(_SCHEMA_REGISTRY.keys()))}"
        )

    schema_path = os.path.join(_SCHEMA_DIR, _SCHEMA_REGISTRY[schema_name])
    try:
        with open(schema_path) as f:
            return json.load(f)
    except FileNotFoundError:
        raise SchemaValidationError(
            f"Schema file not found: {schema_path}"
        )


def validate_artifact(data: Dict[str, Any], schema_name: str) -> None:
    """Validate a JSON artifact against its schema.

    Checks: required fields present, no null values in required fields,
    enum constraints, minItems for arrays, minLength for strings.

    Args:
        data: The artifact data as a dict.
        schema_name: Name of the schema to validate against.

    Raises:
        SchemaValidationError: On any validation failure, with a message
            naming the specific field and constraint that failed.
    """
    schema = load_schema(schema_name)
    _validate_object(data, schema, path="")


def _validate_object(
    data: Any, schema: Dict[str, Any], path: str
) -> None:
    """Recursively validate an object against a schema node."""
    if not isinstance(data, dict):
        raise SchemaValidationError(
            f"Expected object at {path or 'root'}, got {type(data).__name__}"
        )

    # Check required fields
    for field in schema.get("required", []):
        field_path = f"{path}.{field}" if path else field
        if field not in data:
            raise SchemaValidationError(
                f"Missing required field: {field_path}"
            )
        if data[field] is None:
            raise SchemaValidationError(
                f"Field {field_path} must not be null"
            )

    # Validate each property that has a schema definition
    properties = schema.get("properties", {})
    for field, field_schema in properties.items():
        if field not in data or data[field] is None:
            continue
        field_path = f"{path}.{field}" if path else field
        _validate_field(data[field], field_schema, field_path)


def _validate_field(
    value: Any, schema: Dict[str, Any], path: str
) -> None:
    """Validate a single field value against its schema definition."""
    field_type = schema.get("type")

    # Type check
    if field_type == "string" and not isinstance(value, str):
        raise SchemaValidationError(
            f"Field {path}: expected string, got {type(value).__name__}"
        )
    if field_type == "integer" and not isinstance(value, int):
        raise SchemaValidationError(
            f"Field {path}: expected integer, got {type(value).__name__}"
        )
    if field_type == "boolean" and not isinstance(value, bool):
        raise SchemaValidationError(
            f"Field {path}: expected boolean, got {type(value).__name__}"
        )
    if field_type == "array" and not isinstance(value, list):
        raise SchemaValidationError(
            f"Field {path}: expected array, got {type(value).__name__}"
        )
    if field_type == "object" and not isinstance(value, dict):
        raise SchemaValidationError(
            f"Field {path}: expected object, got {type(value).__name__}"
        )

    # String constraints
    if field_type == "string" and isinstance(value, str):
        # Enum check
        if "enum" in schema and value not in schema["enum"]:
            raise SchemaValidationError(
                f"Field {path}: value '{value}' not in allowed values "
                f"{schema['enum']}"
            )
        # MinLength check
        min_length = schema.get("minLength", 0)
        if len(value) < min_length:
            raise SchemaValidationError(
                f"Field {path}: string length {len(value)} < "
                f"minimum {min_length}"
            )

    # Array constraints
    if field_type == "array" and isinstance(value, list):
        min_items = schema.get("minItems", 0)
        if len(value) < min_items:
            raise SchemaValidationError(
                f"Field {path}: array has {len(value)} items, "
                f"minimum is {min_items}"
            )
        # Validate each item
        items_schema = schema.get("items", {})
        if items_schema:
            for i, item in enumerate(value):
                item_path = f"{path}[{i}]"
                if items_schema.get("type") == "object":
                    _validate_object(item, items_schema, item_path)
                else:
                    _validate_field(item, items_schema, item_path)

    # Nested object
    if field_type == "object" and isinstance(value, dict):
        if "properties" in schema or "required" in schema:
            _validate_object(value, schema, path)
```

**Step 4: Run tests to verify they pass**

Run: `python -m unittest tests.test_transfer_schemas -v`
Expected: All tests PASS

**Step 5: Run lint**

Run: `python -m black openevolve/transfer/ tests/test_transfer_schemas.py --check`
Expected: No reformatting needed (or reformat if needed)

**Step 6: Run existing tests to verify no regressions**

Run: `python -m unittest discover tests -v 2>&1 | tail -5`
Expected: All existing tests still pass (BC-10)

**Step 7: Commit**

```bash
git add openevolve/transfer/__init__.py openevolve/transfer/schema_validator.py tests/test_transfer_schemas.py
git commit -m "$(cat <<'EOF'
feat(transfer): add schema validator with tests (BC-4 through BC-12)

- Create openevolve/transfer/ package with schema_validator module
- Validate required fields, null values, enum constraints, minItems
- No external dependencies (stdlib json only)
- Tests cover: valid acceptance, missing fields, invalid enums,
  empty lists, null fields, unknown schema names

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Create mapping artifact

**Contracts Implemented:** BC-8

**Files:**
- Create: `docs/transfer/blis_to_llmd_mapping.md`

**Step 1: Write the mapping artifact**

Context: This is the single source of truth for BLIS↔llm-d signal correspondences. BLIS signals come from `RoutingSnapshot` fields in `initial_program.py`. llm-d equivalents are UNVERIFIED (described behaviorally) since we don't have repo access yet. Version 1.0.

In `docs/transfer/blis_to_llmd_mapping.md`:
```markdown
# BLIS-to-llm-d Signal Mapping

**Version:** 1.0
**Last verified against llm-d-inference-scheduler:** `<COMMIT_HASH_TBD>` (not yet verified — no repo access)
**Last verified against llm-d-benchmark:** `<COMMIT_HASH_TBD>` (not yet verified — no repo access)
**Date:** 2026-03-05

---

## Signal Mapping Table

Each entry maps a BLIS routing signal to its llm-d equivalent.

| # | BLIS Signal | BLIS Source | llm-d Equivalent | llm-d Access Pattern | Fidelity | Fidelity Justification | Translation Strategy | Source Group | Staleness Window |
|---|-------------|------------|-------------------|---------------------|----------|----------------------|---------------------|-------------|-----------------|
| S1 | QueueDepth | `RoutingSnapshot.QueueDepth` | Endpoint pending request count | TBD (endpoint metrics interface) | High | Direct numeric equivalent — both count pending requests | direct_map | endpoint-metrics | <1s (synchronous in BLIS; async polling in llm-d) |
| S2 | BatchSize | `RoutingSnapshot.BatchSize` | Endpoint active batch size | TBD (endpoint metrics interface) | High | Direct numeric equivalent — both count in-flight requests | direct_map | endpoint-metrics | <1s |
| S3 | KVUtilization | `RoutingSnapshot.KVUtilization` | Endpoint KV cache utilization fraction | TBD (endpoint metrics interface) | High | Both express as float in [0,1] range | direct_map | endpoint-metrics | <2s (KV metrics may lag behind actual state) |
| S4 | FreeKVBlocks | `RoutingSnapshot.FreeKVBlocks` | Endpoint free KV cache blocks | TBD (endpoint metrics interface) | Medium | Same concept but llm-d may use different block size granularity | direct_map | endpoint-metrics | <2s |
| S5 | CacheHitRate | `RoutingSnapshot.CacheHitRate` | Per-request prefix match ratio | TBD (prefix cache scorer interface) | Upgrade | BLIS uses aggregate hit rate; llm-d provides per-request prefix match — strictly richer granularity | direct_map | prefix-cache | N/A (computed per-request) |
| S6 | PendingRequests | `RoutingSnapshot.PendingRequests` | Endpoint pending routed-not-queued count | TBD (endpoint metrics interface) | High | Direct numeric equivalent — tracks requests routed but not yet in queue | direct_map | endpoint-metrics | <1s |
| S7 | EffectiveLoad() | `QueueDepth + BatchSize + PendingRequests` | Derived: sum of S1 + S2 + S6 | Computed from component signals | High | Same formula, same semantics | direct_map | derived | N/A (computed) |
| S8 | Request.InputTokens | `req.InputTokens` | Request input token count | TBD (request metadata parsing) | Medium | BLIS has typed field; llm-d requires parsing from request payload (transport-dependent) | direct_map | request-metadata | N/A (per-request) |

## Fidelity Definitions

These definitions are immutable for pipeline major version 1.x (owned by the design doc):

| Rating | Criteria |
|--------|----------|
| **High** | Behavioral equivalence with <5% divergence in test comparisons |
| **Medium** | Same concept but different granularity or access pattern |
| **Low** | Requires approximation or proxy |
| **Upgrade** | Target signal is strictly richer than source |

## Interface List

Public types, interfaces, and method signatures the generated scorer may use. **This list must be kept in sync with the scorer template (PR2).** Stage 4 uses this to classify errors (unrecognized symbols → stale mapping).

> **UNVERIFIED** — populate after inspecting llm-d-inference-scheduler repo.

| Type/Interface | Methods | Purpose |
|---------------|---------|---------|
| TBD: Scorer plugin interface | `Identity()`, `Category()`, `Score()` | Plugin lifecycle |
| TBD: Endpoint metrics interface | TBD | Access endpoint signals |
| TBD: Request interface | TBD | Access request metadata |
| TBD: Scoring context | TBD | Scheduling context (P/D detection) |

## P/D Disaggregation Indicators

Indicators that the target cluster uses prefill/decode disaggregation:

> **UNVERIFIED** — populate after inspecting llm-d-inference-scheduler config format.

- TBD: Config field indicating P/D mode
- TBD: Scheduling context method for detecting disaggregated path

## Scorer Overlap

Existing llm-d scorers that share signals with BLIS-discovered algorithms:

> **UNVERIFIED** — populate after inspecting llm-d-inference-scheduler scorers.

| Scorer Name | Shared Signals | Recommended Action | Notes |
|-------------|---------------|-------------------|-------|
| TBD | TBD | TBD | TBD |

## Weight Convention

- **BLIS convention:** Normalized weights summing to 1.0
- **llm-d convention:** TBD (UNVERIFIED — may use raw weights per scorer)
- **Translation rule:** TBD (normalize or scale based on convention difference)

## BLIS Workload Generator Configuration Schema

The BLIS workload generator (in llm-d-benchmark) accepts configuration matching
the BLIS workload YAML format. Required parameters:

| Parameter | Type | Description | Example |
|-----------|------|-------------|---------|
| `version` | string | Workload format version | `"2"` |
| `seed` | integer | Random seed for reproducibility | `42` |
| `category` | string | Workload category | `"language"`, `"reasoning"` |
| `aggregate_rate` | float | Requests per second | `1000.0` |
| `num_requests` | integer | Total request count | `5000` |
| `clients` | array | Client definitions (see below) | |

**Client parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `id` | string | yes | Client identifier |
| `slo_class` | string | yes | One of: `"critical"`, `"standard"`, `"background"`, `"sheddable"` |
| `rate_fraction` | float | yes | Fraction of aggregate rate (must sum to 1.0 across clients) |
| `streaming` | boolean | yes | Whether client uses streaming |
| `arrival.process` | string | yes | `"poisson"` or `"gamma"` |
| `arrival.cv` | float | no | Coefficient of variation (gamma only) |
| `input_distribution.type` | string | yes | `"gaussian"` or `"exponential"` |
| `input_distribution.params` | object | yes | Distribution parameters (mean, std_dev, min, max) |
| `output_distribution.type` | string | yes | `"exponential"` |
| `output_distribution.params` | object | yes | Distribution parameters (mean) |
| `prefix_group` | string | no | Prefix group identifier for cache affinity |
| `prefix_length` | integer | no | Prefix token length |
| `multi_turn.max_rounds` | integer | no | Max conversation rounds |
| `multi_turn.context_growth` | string | no | `"accumulate"` or `"fixed"` |

## Staleness Prevention Checks

Stage 1 performs these checks before each transfer:

1. **Compilation check:** Build scorer template against llm-d HEAD
2. **Smoke test:** Run scorer template unit tests
3. **Commit distance:** Verify "last verified against" commit is within 50 commits of llm-d HEAD
4. **BLIS workload generator version:** Verify installed generator major version matches this artifact's expected version

**Expected BLIS workload generator version:** `1.x` (TBD — set when generator is added to llm-d-benchmark)

---

*This artifact is the single source of truth for BLIS↔llm-d implementation-level mappings. Update when llm-d APIs change. Do not duplicate fidelity assignments elsewhere.*
```

**Step 2: Verify mapping artifact has at least one complete signal entry**

Run: `python -c "
with open('docs/transfer/blis_to_llmd_mapping.md') as f:
    content = f.read()
# Check S1 entry has all columns populated
assert '| S1 |' in content
assert 'QueueDepth' in content
assert 'High' in content
assert 'direct_map' in content
assert 'endpoint-metrics' in content
print('OK: mapping artifact has complete signal entries')
"`
Expected: `OK: mapping artifact has complete signal entries`

**Step 3: Commit**

```bash
git add docs/transfer/blis_to_llmd_mapping.md
git commit -m "$(cat <<'EOF'
docs: add BLIS-to-llm-d mapping artifact v1.0 (BC-8)

Signal mapping table with 8 entries covering all RoutingSnapshot fields.
Fidelity ratings, translation strategies, staleness windows documented.
BLIS workload generator config schema documented from actual YAML files.
llm-d entries marked UNVERIFIED (no repo access yet).

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Update CLAUDE.md with transfer pipeline section

**Contracts Implemented:** (documentation — supports BC-10 regression safety)

**Files:**
- Modify: `CLAUDE.md`

**Step 1: Add transfer pipeline section to CLAUDE.md**

Context: The macro plan requires CLAUDE.md to be updated with a transfer pipeline section so contributors know about the new infrastructure.

Add the following section at the end of `CLAUDE.md`, before the `### Development Notes` section:

```markdown
### Transfer Pipeline

Infrastructure for transferring BLIS-discovered algorithms to production systems.

- **Mapping artifact** (`docs/transfer/blis_to_llmd_mapping.md`): Signal-level mapping between BLIS and llm-d — fidelity ratings, translation strategies, staleness windows. Single source of truth for implementation-level correspondences.
- **JSON Schemas** (`docs/transfer/schemas/`): Validation schemas for inter-stage artifacts (`algorithm_summary`, `signal_coverage`).
- **Schema validator** (`openevolve/transfer/schema_validator.py`): Validates artifacts against schemas. Used by all pipeline stages that consume inter-stage data.
- **Transfer pipeline design** (`docs/plans/2026-02-26-blis-to-llmd-transfer-design-v2.md`): Full pipeline design document.
- **Macro plan** (`docs/plans/2026-03-02-blis-to-llmd-transfer-macro-plan-v2.md`): 7-PR implementation plan.
```

**Step 2: Run existing tests to verify no regressions**

Run: `python -m unittest discover tests 2>&1 | tail -3`
Expected: All tests pass

**Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "$(cat <<'EOF'
docs: add transfer pipeline section to CLAUDE.md

Document new docs/transfer/ directory, schema validator, and mapping
artifact so contributors know about the transfer infrastructure.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### H) Test Strategy

| Contract | Task | Test Type | Test Name / Description |
|----------|------|-----------|--------------------------|
| BC-1 | Task 4 | Unit | `TestSchemaFilesValid.test_algorithm_summary_schema_parses`, `test_signal_coverage_schema_parses` |
| BC-2 | Task 4 | Unit | `TestAlgorithmSummarySchemaCompleteness.test_required_fields_present` |
| BC-3 | Task 4 | Unit | `TestSignalCoverageSchemaCompleteness.test_required_fields_present` |
| BC-4 | Task 4 | Unit | `TestValidatorAcceptsValid.test_valid_algorithm_summary_accepted`, `test_valid_signal_coverage_accepted` |
| BC-5 | Task 4 | Unit | `TestValidatorRejectsMissingFields.test_missing_evolve_block_code`, `test_missing_signals` |
| BC-6 | Task 4 | Unit | `TestValidatorRejectsInvalidEnum.test_invalid_scope_verdict` |
| BC-7 | Task 4 | Unit | `TestValidatorRejectsEmptyLists.test_empty_signals_used`, `test_empty_workload_results` |
| BC-8 | Task 5 | Manual | Verify mapping artifact has complete S1 entry with all columns |
| BC-9 | Task 4 | Manual | Inspect imports in `schema_validator.py` — stdlib only |
| BC-10 | Task 6 | Integration | `python -m unittest discover tests` — all existing tests pass |
| BC-11 | Task 4 | Unit | `TestValidatorRejectsUnknownSchema.test_unknown_schema` |
| BC-12 | Task 4 | Unit | `TestValidatorRejectsNullFields.test_null_evolve_block_code` |

No golden datasets. No invariant tests needed (no simulation or runtime behavior).

### I) Risk Analysis

| Risk | Likelihood | Impact | Mitigation | Task |
|------|-----------|--------|------------|------|
| Schema doesn't match design doc | Medium | High | Schema fields traced directly from design doc Section "Stage Artifact Schemas" | Task 2, 3 |
| Mapping artifact llm-d entries wrong | High | Low | All llm-d entries marked UNVERIFIED; will be corrected in PR2 when repo is inspected | Task 5 |
| Schema validator misses edge cases | Low | Medium | Comprehensive rejection tests (BC-5 through BC-7, BC-11, BC-12) | Task 4 |
| `workload_results[].results` too loose | Medium | Low | Intentionally deferred (design doc BLOCKING TODO); PR3 will tighten | Task 2 |

---

## Part 3: Quality Assurance

### J) Sanity Checklist

- [x] No unnecessary abstractions — schema validator is minimal, no ORM or framework
- [x] No feature creep — only infrastructure + mapping, no pipeline stages
- [x] No unexercised flags or interfaces — all functions called by tests
- [x] No partial implementations — validator is complete and tested
- [x] No breaking changes — purely additive PR
- [x] No hidden global state — all functions are stateless
- [x] All new code will pass black formatting
- [x] CLAUDE.md updated with transfer pipeline section
- [x] No stale references in CLAUDE.md
- [x] Deviation log reviewed — one DEFERRAL (metrics.workload concrete schema)
- [x] Each task produces working, testable code
- [x] Task dependencies correctly ordered (1→2→3→4→5→6)
- [x] All contracts mapped to tasks
- [x] R1: No silent continue/return — validator raises on all failures
- [x] R9: N/A — no YAML config fields
- [x] R11: N/A — no division operations
- [x] Construction site audit: No existing structs modified; new classes have single construction point

---

## Appendix: File-Level Implementation Details

All complete implementations are provided inline in the Task Breakdown (Section G) above. Key notes:

**`openevolve/transfer/schema_validator.py`:**
- Recursive validation: `validate_artifact` → `_validate_object` → `_validate_field`
- Handles nested objects (e.g., `workload_results[].items`) via recursion
- Enum validation reads `enum` array from schema definition
- Error messages always name the specific field path (e.g., `workload_results[0].workload_name`)
- No `jsonschema` dependency — manual validation against schema structure

**`docs/transfer/blis_to_llmd_mapping.md`:**
- Signal table sourced from `RoutingSnapshot` struct in `examples/blis_router/initial_program.py:24-32`
- `EffectiveLoad()` documented as derived signal (S7) since it's a method, not a field
- `Request.InputTokens` included as S8 — the only request-level signal currently used
- Workload generator config schema sourced from actual YAML files: `workload_v2_cache_warmup.yaml`, `workload_v2_load_spikes.yaml`, `workload_v2_multiturn.yaml`
- All `TBD` entries correspond to UNVERIFIED llm-d items — will be populated in PR2

---

Plan complete and saved to `docs/plans/pr1-transfer-infrastructure-plan.md`. Two execution options:

**1. Subagent-Driven (this session)** — I dispatch fresh subagent per task, review between tasks, fast iteration

**2. Parallel Session (separate)** — Open new session with executing-plans, batch execution with checkpoints

Which approach?