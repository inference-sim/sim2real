# Issue 403: Emit `blis_observe:` in bootstrap-generated `transfer.yaml`

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the `sim2real-bootstrap` skill so the `transfer.yaml` it emits carries a `blis_observe:` section populated from the `blis observe \ ... \` command block that experiment authors already document in `config.md`.

**Architecture:** Add two pure functions (`parse_observe_block`, `render_blis_observe_yaml`) plus a new CLI mode (`--emit-observe-yaml`) to the existing `generate_from_config.py`. The skill's Task 5 (transfer.yaml assembly) invokes this new mode to obtain a YAML fragment with `# source: config.md` / `# source: sim2real-bootstrap default` provenance comments, which the agent pastes verbatim into `transfer.yaml`. The runtime side (manifest validator, assemble pipeline) already accepts `blis_observe:` — this plan is bootstrap-only.

**Tech Stack:** Python 3.10+ (skill scripts), PyYAML (validation only), pytest.

## Global Constraints

- The manifest schema (already landed via #337) accepts exactly these 5 keys under `blis_observe:`: `maxConcurrency`, `timeout`, `warmupRequests`, `prewarmDuration`, `extraArgs`. Emitting any other key is rejected by `pipeline/lib/manifest.py:263-278`. Values must be `str`, `int`, or `float` — not `bool`.
- Default values (used when the flag is absent from config.md) come from `pipeline/pipeline.yaml:36-50` and MUST match those Pipeline-level defaults exactly: `maxConcurrency=10000`, `timeout=1800`, `warmupRequests=50`, `prewarmDuration=60s`, `extraArgs=""`.
- Provenance comment format follows the pattern already used by `generate_from_config.py` for the prefix-caching flag (see `.claude/skills/sim2real-bootstrap/generate_from_config.py:373`): `# source: config.md` for values parsed from the block, `# source: sim2real-bootstrap default` otherwise.
- Only the 4 CLI tuning flags map to explicit `blis_observe` keys (see field mapping table below). Pipeline-injected flags (`--server-url`, `--model`, `--workload-spec`, `--trace-header`, `--trace-data`, `--saturation-report`, `--post-hoc-detector`) MUST be dropped — they are hardcoded by the Tekton task (see `tektonc-data-collection/tekton/tasks/run-workload-blis-observe-binary.yaml`). Any remaining unknown flags collect into `extraArgs`.
- The bootstrap SKILL.md structure (numbered tasks, "action:" tags, "Present to user" gates) is stable — do NOT restructure it. Only add the new derivation step and update the Task 5 output schema.
- Every `gh` write in this session goes through the `unset GITHUB_TOKEN GH_TOKEN` fallback on `Resource not accessible` errors, silently.

**Field mapping (canonical):**

| CLI flag                | `blis_observe:` key |
|-------------------------|---------------------|
| `--max-concurrency`     | `maxConcurrency`    |
| `--timeout`             | `timeout`           |
| `--warmup-requests`     | `warmupRequests`    |
| `--prewarm-duration`    | `prewarmDuration`   |
| any other non-injected flag | folded into `extraArgs` (space-joined) |

---

## File Structure

**Modified:**
- `.claude/skills/sim2real-bootstrap/generate_from_config.py` — add module-level constants (`OBSERVE_TUNING_FLAGS`, `OBSERVE_PIPELINE_INJECTED_FLAGS`, `OBSERVE_DEFAULTS`), two pure functions (`parse_observe_block`, `render_blis_observe_yaml`), and a `--emit-observe-yaml` CLI mode.
- `.claude/skills/sim2real-bootstrap/SKILL.md` — update Task 5 output schema to include `blis_observe:` block; add a derivation step that invokes the new CLI mode.
- `pipeline/README.md` — mention that `sim2real-bootstrap` now populates `blis_observe:` from `config.md`, if the section covers bootstrap-emitted transfer.yaml.

**Created:**
- `.claude/skills/sim2real-bootstrap/tests/test_generate_from_config_observe.py` — new test file, sibling of `test_generate_from_config.py`. Kept separate because the observe parser is an independent surface (different input format, different output shape) and mingling would push both files past comfortable size.

**No changes required:**
- `pipeline/lib/manifest.py` (already validates `blis_observe:` — #337)
- `pipeline/lib/assemble_run.py` (already reads `manifest["blis_observe"]` — #337)
- `pipeline/pipeline.yaml` (Pipeline-level defaults unchanged)
- `.github/workflows/test.yml` (the new test file lives under `.claude/skills/sim2real-bootstrap/tests/`, which is already in the CI test glob at line 46)

---

## Task 1: Parser + renderer (pure functions)

**Files:**
- Modify: `.claude/skills/sim2real-bootstrap/generate_from_config.py` — add constants and two functions, no CLI wiring yet.
- Create: `.claude/skills/sim2real-bootstrap/tests/test_generate_from_config_observe.py`

**Interfaces:**
- Produces:
  ```python
  OBSERVE_TUNING_FLAGS: dict[str, str]  # CLI flag → transfer.yaml key
  OBSERVE_PIPELINE_INJECTED_FLAGS: set[str]  # flags to silently drop
  OBSERVE_DEFAULTS: dict[str, str]  # key → default (must match pipeline.yaml)

  def parse_observe_block(config_md_text: str) -> dict[str, str]:
      """Extract flags from the `blis observe \\ ... \\` block in config.md.

      Returns a dict keyed by transfer.yaml key (maxConcurrency, timeout,
      warmupRequests, prewarmDuration, extraArgs). Keys are present ONLY when
      the block contained the corresponding flag(s). Absent block → {}.
      extraArgs is present only when the block had at least one flag that is
      neither in OBSERVE_TUNING_FLAGS nor OBSERVE_PIPELINE_INJECTED_FLAGS;
      its value is those flags joined with a single space in source order.
      """

  def render_blis_observe_yaml(parsed: dict[str, str]) -> str:
      """Render a `blis_observe:` YAML block with provenance comments.

      Emits all 5 keys in canonical order (maxConcurrency, timeout,
      warmupRequests, prewarmDuration, extraArgs). Keys present in `parsed`
      use those values with `# source: config.md`; keys absent use
      OBSERVE_DEFAULTS with `# source: sim2real-bootstrap default`.

      Numeric string values (all-digit) emit as bare YAML integers;
      non-numeric values emit as YAML strings with double quotes (which
      cleanly round-trips `60s`, `""`, and any future free-form values).
      """
  ```

**Key values (from `pipeline/pipeline.yaml:36-50`):**
```python
OBSERVE_TUNING_FLAGS = {
    "--max-concurrency": "maxConcurrency",
    "--timeout": "timeout",
    "--warmup-requests": "warmupRequests",
    "--prewarm-duration": "prewarmDuration",
}
OBSERVE_PIPELINE_INJECTED_FLAGS = {
    "--server-url", "--model", "--workload-spec",
    "--trace-header", "--trace-data", "--saturation-report",
    "--post-hoc-detector",
}
OBSERVE_DEFAULTS = {
    "maxConcurrency": "10000",
    "timeout": "1800",
    "warmupRequests": "50",
    "prewarmDuration": "60s",
    "extraArgs": "",
}
```

- [ ] **Step 1: Write the failing tests**

Create `.claude/skills/sim2real-bootstrap/tests/test_generate_from_config_observe.py` with the following content:

```python
"""Tests for parse_observe_block + render_blis_observe_yaml.

Covers acceptance criteria from issue #403:
  - Full `blis observe \\ ... \\` block → all 4 tuning keys extracted
  - Partial block → only present keys extracted
  - No block in text → empty dict
  - Pipeline-injected flags dropped, not folded into extraArgs
  - Unknown flags collected into extraArgs (space-joined)
  - Rendered YAML has correct provenance for extracted vs defaulted keys
  - Rendered YAML round-trips through PyYAML with expected typing
  - Rendered YAML matches the 5-key schema validated by manifest.py
"""
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parents[1]))
import generate_from_config as gfc


SAMPLE_FULL_BLOCK = """\
Some prose.

## Real-Cluster Load Generator (blis observe)

```bash
blis observe \\
  --server-url http://<gateway>:80 \\
  --model Qwen/Qwen3-14B \\
  --workload-spec <workload>.yaml \\
  --max-concurrency 10000 \\
  --prewarm-duration 60s \\
  --warmup-requests 50 \\
  --timeout 1800 \\
  --post-hoc-detector composite \\
  --trace-header trace.yaml \\
  --trace-data trace.csv \\
  --saturation-report saturation.json
```

More prose.
"""


# ---------------------------------------------------------------------------
# parse_observe_block
# ---------------------------------------------------------------------------

def test_full_block_extracts_all_four_tuning_flags():
    parsed = gfc.parse_observe_block(SAMPLE_FULL_BLOCK)
    assert parsed == {
        "maxConcurrency": "10000",
        "prewarmDuration": "60s",
        "warmupRequests": "50",
        "timeout": "1800",
    }
    assert "extraArgs" not in parsed


def test_partial_block_extracts_only_present_flags():
    text = """\
```bash
blis observe \\
  --max-concurrency 500 \\
  --timeout 900
```
"""
    parsed = gfc.parse_observe_block(text)
    assert parsed == {"maxConcurrency": "500", "timeout": "900"}


def test_absent_block_returns_empty_dict():
    text = "# Config\n\nJust some prose, no blis observe command anywhere.\n"
    assert gfc.parse_observe_block(text) == {}


def test_pipeline_injected_flags_are_dropped_not_extraargs():
    """--server-url, --model, --workload-spec, --trace-*, --saturation-report,
    --post-hoc-detector are hardcoded by the Tekton task and MUST NOT leak
    into extraArgs."""
    text = """\
```bash
blis observe \\
  --server-url http://gateway:80 \\
  --model foo/bar \\
  --workload-spec wl.yaml \\
  --trace-header t.yaml \\
  --trace-data t.csv \\
  --saturation-report s.json \\
  --post-hoc-detector composite
```
"""
    assert gfc.parse_observe_block(text) == {}


def test_unknown_flags_collected_into_extra_args():
    text = """\
```bash
blis observe \\
  --max-concurrency 100 \\
  --new-flag foo \\
  --another-flag bar
```
"""
    parsed = gfc.parse_observe_block(text)
    assert parsed["maxConcurrency"] == "100"
    assert parsed["extraArgs"] == "--new-flag foo --another-flag bar"


def test_bare_unknown_flag_collected_into_extra_args():
    text = """\
```bash
blis observe \\
  --verbose \\
  --timeout 60
```
"""
    parsed = gfc.parse_observe_block(text)
    assert parsed["timeout"] == "60"
    assert parsed["extraArgs"] == "--verbose"


def test_block_without_backslash_continuation_still_parses():
    """Handle the last-line case (no trailing \\) and single-line invocations."""
    text = """\
```bash
blis observe --max-concurrency 42 --timeout 7
```
"""
    parsed = gfc.parse_observe_block(text)
    assert parsed == {"maxConcurrency": "42", "timeout": "7"}


# ---------------------------------------------------------------------------
# render_blis_observe_yaml
# ---------------------------------------------------------------------------

def test_render_all_defaults_when_parsed_empty():
    out = gfc.render_blis_observe_yaml({})
    assert out.startswith("blis_observe:\n")
    # Every key present with the sim2real-bootstrap default source.
    for key in ("maxConcurrency", "timeout", "warmupRequests",
                "prewarmDuration", "extraArgs"):
        assert key in out
    assert out.count("# source: sim2real-bootstrap default") == 5
    assert "# source: config.md" not in out


def test_render_full_block_all_from_config():
    parsed = {
        "maxConcurrency": "10000",
        "timeout": "1800",
        "warmupRequests": "50",
        "prewarmDuration": "60s",
    }
    out = gfc.render_blis_observe_yaml(parsed)
    # 4 sourced from config.md, extraArgs sourced from default.
    assert out.count("# source: config.md") == 4
    assert out.count("# source: sim2real-bootstrap default") == 1


def test_render_mixed_provenance():
    parsed = {"maxConcurrency": "500", "prewarmDuration": "30s"}
    out = gfc.render_blis_observe_yaml(parsed)
    assert out.count("# source: config.md") == 2
    assert out.count("# source: sim2real-bootstrap default") == 3


def test_render_output_parses_as_yaml_with_expected_types():
    """Numeric-string keys emit as YAML ints; string keys emit as YAML
    strings. bool must never appear — manifest.py rejects bool values."""
    out = gfc.render_blis_observe_yaml({})
    loaded = yaml.safe_load(out)
    assert loaded == {
        "blis_observe": {
            "maxConcurrency": 10000,
            "timeout": 1800,
            "warmupRequests": 50,
            "prewarmDuration": "60s",
            "extraArgs": "",
        }
    }
    for v in loaded["blis_observe"].values():
        assert not isinstance(v, bool)


def test_render_extra_args_from_config_stays_a_string():
    parsed = {"extraArgs": "--verbose --dry-run"}
    out = gfc.render_blis_observe_yaml(parsed)
    loaded = yaml.safe_load(out)
    assert loaded["blis_observe"]["extraArgs"] == "--verbose --dry-run"


def test_render_key_order_is_canonical():
    """Order must be stable so operators skimming transfer.yaml find keys
    predictably. Mirrors OBSERVE_DEFAULTS declaration order."""
    out = gfc.render_blis_observe_yaml({})
    lines = [ln.strip() for ln in out.splitlines() if ln.strip() and not ln.startswith("blis_observe")]
    keys_in_order = [ln.split(":")[0] for ln in lines]
    assert keys_in_order == [
        "maxConcurrency", "timeout", "warmupRequests",
        "prewarmDuration", "extraArgs",
    ]
```

- [ ] **Step 2: Run tests to verify they all fail**

```bash
cd /Users/kalantar/projects/go.workspace/src/github.com/inference-sim/sim2real/.claude/worktrees/issue-403-blis-observe-bootstrap
python -m pytest .claude/skills/sim2real-bootstrap/tests/test_generate_from_config_observe.py -v
```

Expected: every test fails with `AttributeError: module 'generate_from_config' has no attribute 'parse_observe_block'` (or `render_blis_observe_yaml`).

- [ ] **Step 3: Add constants near the top of `generate_from_config.py`**

Locate the "Lookup tables" section (around line 20) in `.claude/skills/sim2real-bootstrap/generate_from_config.py`. After the existing `VLLM_INDICATOR_FIELDS` definition (around line 78), append:

```python
# ---------------------------------------------------------------------------
# blis observe → blis_observe:  (issue #403)
# ---------------------------------------------------------------------------

OBSERVE_TUNING_FLAGS = {
    "--max-concurrency": "maxConcurrency",
    "--timeout": "timeout",
    "--warmup-requests": "warmupRequests",
    "--prewarm-duration": "prewarmDuration",
}

# Hardcoded by tekton/tasks/run-workload-blis-observe-binary.yaml — the block
# in config.md typically lists them for readability but the Tekton task
# supplies them at runtime, so they must NOT leak into extraArgs.
OBSERVE_PIPELINE_INJECTED_FLAGS = {
    "--server-url",
    "--model",
    "--workload-spec",
    "--trace-header",
    "--trace-data",
    "--saturation-report",
    "--post-hoc-detector",
}

# Match pipeline/pipeline.yaml:36-50. Update in lockstep if those defaults
# ever change.
OBSERVE_DEFAULTS = {
    "maxConcurrency": "10000",
    "timeout": "1800",
    "warmupRequests": "50",
    "prewarmDuration": "60s",
    "extraArgs": "",
}
```

- [ ] **Step 4: Add `parse_observe_block` to `generate_from_config.py`**

Append after the constants block from Step 3:

```python
def parse_observe_block(config_md_text: str) -> dict[str, str]:
    """Extract flags from the `blis observe \\ ... \\` command in config.md.

    Returns a dict keyed by transfer.yaml key. Keys are present only when the
    block contained the corresponding flag. `extraArgs` collects any unknown
    non-injected flags (whitespace-joined, source order). Absent block → {}.
    """
    # Locate the first line that starts a `blis observe` invocation. We accept
    # optional leading whitespace so the block can live inside a code fence.
    lines = config_md_text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if re.match(r"^\s*blis\s+observe\b", line):
            start = i
            break
    if start is None:
        return {}

    # Collect the invocation line plus continuation lines. A continuation is
    # any line whose predecessor's stripped form ends with '\'. Stop at the
    # first line without a trailing continuation.
    collected = [lines[start]]
    idx = start
    while collected[-1].rstrip().endswith("\\"):
        idx += 1
        if idx >= len(lines):
            break
        collected.append(lines[idx])

    # Flatten to a single string, strip trailing backslashes and code-fence
    # boundaries, then tokenize on whitespace.
    joined = " ".join(ln.rstrip("\\").strip() for ln in collected)
    # Drop leading "blis observe" tokens.
    tokens = joined.split()
    # Skip until we see the first token starting with '--'.
    flag_tokens = []
    seen_flag = False
    for tok in tokens:
        if tok.startswith("--"):
            seen_flag = True
        if seen_flag:
            flag_tokens.append(tok)

    parsed: dict[str, str] = {}
    extra_pieces: list[str] = []
    i = 0
    while i < len(flag_tokens):
        tok = flag_tokens[i]
        if not tok.startswith("--"):
            # Stray value with no preceding flag — skip.
            i += 1
            continue
        # Peek at next token; it's a value if it exists and is not a flag.
        has_value = i + 1 < len(flag_tokens) and not flag_tokens[i + 1].startswith("--")
        value = flag_tokens[i + 1] if has_value else None

        if tok in OBSERVE_TUNING_FLAGS:
            if value is not None:
                parsed[OBSERVE_TUNING_FLAGS[tok]] = value
                i += 2
            else:
                # Tuning flag with no value is malformed; skip.
                i += 1
        elif tok in OBSERVE_PIPELINE_INJECTED_FLAGS:
            # Drop entirely — the Tekton task supplies these.
            i += 2 if has_value else 1
        else:
            # Unknown → extraArgs.
            if has_value:
                extra_pieces.extend([tok, value])
                i += 2
            else:
                extra_pieces.append(tok)
                i += 1

    if extra_pieces:
        parsed["extraArgs"] = " ".join(extra_pieces)
    return parsed
```

- [ ] **Step 5: Add `render_blis_observe_yaml` to `generate_from_config.py`**

Append immediately after `parse_observe_block`:

```python
def render_blis_observe_yaml(parsed: dict[str, str]) -> str:
    """Render a `blis_observe:` YAML block with provenance comments.

    Emits all 5 keys in canonical order. Keys present in `parsed` are marked
    `# source: config.md`; keys absent are defaulted from OBSERVE_DEFAULTS
    and marked `# source: sim2real-bootstrap default`. Numeric-string values
    (all-digit) emit as bare YAML integers; other values emit as double-
    quoted YAML strings so a bare `60s` round-trips cleanly.
    """
    lines = ["blis_observe:"]
    for key, default in OBSERVE_DEFAULTS.items():
        if key in parsed:
            value = parsed[key]
            source = "config.md"
        else:
            value = default
            source = "sim2real-bootstrap default"
        # Emit as bare int when the value is purely digits (no leading zero
        # edge case: '0' is fine as int, '007' would still parse fine).
        if value.isdigit():
            rendered = value
        else:
            # Escape embedded double quotes.
            rendered = '"' + value.replace('"', '\\"') + '"'
        lines.append(f"  {key}: {rendered}  # source: {source}")
    return "\n".join(lines) + "\n"
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
python -m pytest .claude/skills/sim2real-bootstrap/tests/test_generate_from_config_observe.py -v
```

Expected: all tests PASS.

- [ ] **Step 7: Run the full bootstrap test suite (no regressions)**

```bash
python -m pytest .claude/skills/sim2real-bootstrap/tests/ -v
```

Expected: all tests PASS, including the pre-existing `test_generate_from_config.py` and `test_byo.py`.

- [ ] **Step 8: Commit**

```bash
git add .claude/skills/sim2real-bootstrap/generate_from_config.py \
        .claude/skills/sim2real-bootstrap/tests/test_generate_from_config_observe.py
git commit -m "feat(bootstrap): parse blis observe block from config.md

Add parse_observe_block() and render_blis_observe_yaml() to
generate_from_config.py. Extracts --max-concurrency, --timeout,
--warmup-requests, --prewarm-duration from the blis observe command
block in config.md and renders a blis_observe: YAML fragment with
per-key provenance comments (source: config.md vs sim2real-bootstrap
default). Pipeline-injected flags are dropped; unknown flags fold
into extraArgs. Pure functions only, no CLI wiring yet.

Refs #403"
```

---

## Task 2: `--emit-observe-yaml` CLI mode

**Files:**
- Modify: `.claude/skills/sim2real-bootstrap/generate_from_config.py` — extend `main()` with the new flag.
- Modify: `.claude/skills/sim2real-bootstrap/tests/test_generate_from_config_observe.py` — add CLI-invocation tests.

**Interfaces:**
- Consumes: `parse_observe_block`, `render_blis_observe_yaml` from Task 1.
- Produces: CLI mode. When `--emit-observe-yaml` is set, `generate_from_config.py`:
  1. Reads the positional `config` argument (defaults to `./config.md`).
  2. If the file is missing, emits an all-defaults `blis_observe:` block and exits 0 (no error). This matches the issue's acceptance criterion "config.md absent → all five keys defaulted".
  3. If the file exists, parses only the observe block (skips vLLM-table parsing entirely) and emits the rendered fragment on stdout.
  4. Does NOT write scenario YAML. `-o` / `-n` / `--dry-run` are silently ignored in this mode (they are meaningful only for the default scenario-emission path).

- [ ] **Step 1: Add CLI-integration tests to `test_generate_from_config_observe.py`**

Append to `.claude/skills/sim2real-bootstrap/tests/test_generate_from_config_observe.py`:

```python
# ---------------------------------------------------------------------------
# --emit-observe-yaml CLI mode
# ---------------------------------------------------------------------------

import subprocess

SCRIPT = str(Path(__file__).parents[1] / "generate_from_config.py")


def _run_emit_observe(tmp_path, config_text: str | None):
    """Invoke `generate_from_config.py --emit-observe-yaml` and return (stdout, stderr, rc).

    If config_text is None, do not create the file (test config-absent case).
    Otherwise, write it to tmp_path/config.md and pass that path.
    """
    if config_text is None:
        config_path = tmp_path / "nonexistent.md"
    else:
        config_path = tmp_path / "config.md"
        config_path.write_text(config_text)
    result = subprocess.run(
        ["python3", SCRIPT, str(config_path), "--emit-observe-yaml"],
        capture_output=True, text=True,
    )
    return result.stdout, result.stderr, result.returncode


def test_cli_emit_observe_full_block(tmp_path):
    stdout, stderr, rc = _run_emit_observe(tmp_path, SAMPLE_FULL_BLOCK)
    assert rc == 0, stderr
    loaded = yaml.safe_load(stdout)
    assert loaded == {
        "blis_observe": {
            "maxConcurrency": 10000,
            "timeout": 1800,
            "warmupRequests": 50,
            "prewarmDuration": "60s",
            "extraArgs": "",
        }
    }
    # 4 keys from config.md, extraArgs defaulted.
    assert stdout.count("# source: config.md") == 4
    assert stdout.count("# source: sim2real-bootstrap default") == 1


def test_cli_emit_observe_partial_block(tmp_path):
    text = """\
```bash
blis observe \\
  --max-concurrency 500 \\
  --timeout 60
```
"""
    stdout, stderr, rc = _run_emit_observe(tmp_path, text)
    assert rc == 0, stderr
    loaded = yaml.safe_load(stdout)
    assert loaded["blis_observe"]["maxConcurrency"] == 500
    assert loaded["blis_observe"]["timeout"] == 60
    # warmupRequests + prewarmDuration + extraArgs defaulted.
    assert stdout.count("# source: config.md") == 2
    assert stdout.count("# source: sim2real-bootstrap default") == 3


def test_cli_emit_observe_no_block_all_defaults(tmp_path):
    stdout, stderr, rc = _run_emit_observe(tmp_path, "# Nothing here\n")
    assert rc == 0, stderr
    loaded = yaml.safe_load(stdout)
    assert loaded == {"blis_observe": {
        "maxConcurrency": 10000, "timeout": 1800, "warmupRequests": 50,
        "prewarmDuration": "60s", "extraArgs": "",
    }}
    assert stdout.count("# source: sim2real-bootstrap default") == 5


def test_cli_emit_observe_absent_config_all_defaults(tmp_path):
    """Per issue #403 acceptance criteria: config.md absent → all defaults, exit 0."""
    stdout, stderr, rc = _run_emit_observe(tmp_path, None)
    assert rc == 0, stderr
    loaded = yaml.safe_load(stdout)
    assert loaded == {"blis_observe": {
        "maxConcurrency": 10000, "timeout": 1800, "warmupRequests": 50,
        "prewarmDuration": "60s", "extraArgs": "",
    }}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest .claude/skills/sim2real-bootstrap/tests/test_generate_from_config_observe.py::test_cli_emit_observe_full_block -v
```

Expected: fails with a stderr message about an unrecognized argument or a `sys.exit(1)` from the config-file-missing path.

- [ ] **Step 3: Wire the CLI flag in `main()` of `generate_from_config.py`**

Locate `main()` (around line 592). Modify the `argparse` block and the top of `main()` as follows.

Add the flag definition after the existing `--dry-run` argument (around line 606):

```python
    parser.add_argument(
        "--emit-observe-yaml",
        action="store_true",
        help=(
            "Emit only a `blis_observe:` YAML fragment (parsed from the "
            "`blis observe \\ ... \\` block in config.md) to stdout, then "
            "exit 0. Skips scenario YAML generation. If config.md is "
            "missing, emits an all-defaults fragment."
        ),
    )
```

Then, immediately after `args = parser.parse_args()` (currently around line 608), branch on the new flag BEFORE any of the "file not found" / table-parsing logic:

```python
    if args.emit_observe_yaml:
        config_path = args.config
        if os.path.isfile(config_path):
            with open(config_path) as f:
                text = f.read()
            parsed = parse_observe_block(text)
        else:
            parsed = {}
        sys.stdout.write(render_blis_observe_yaml(parsed))
        return
```

Insert this block between the `args = parser.parse_args()` line and the existing `config_path = args.config` line.

- [ ] **Step 4: Run CLI tests to verify they pass**

```bash
python -m pytest .claude/skills/sim2real-bootstrap/tests/test_generate_from_config_observe.py -v
```

Expected: all tests PASS, including the new CLI-integration tests.

- [ ] **Step 5: Verify the default scenario-emission path still works**

```bash
python -m pytest .claude/skills/sim2real-bootstrap/tests/test_generate_from_config.py -v
```

Expected: all pre-existing tests PASS. The `--emit-observe-yaml` branch is only entered when the flag is set, so the scenario-emission code path must be unchanged.

- [ ] **Step 6: Sanity-check the CLI against the real soft-reflective config**

Run the new CLI mode against the real `soft-reflective/config.md` in the parent workspace and eyeball the output:

```bash
python3 .claude/skills/sim2real-bootstrap/generate_from_config.py \
    /Users/kalantar/projects/go.workspace/src/github.com/kalantar-msb/soft-reflective/config.md \
    --emit-observe-yaml
```

Expected output (approximately):
```yaml
blis_observe:
  maxConcurrency: 10000  # source: config.md
  timeout: 1800  # source: config.md
  warmupRequests: 50  # source: config.md
  prewarmDuration: "60s"  # source: config.md
  extraArgs: ""  # source: sim2real-bootstrap default
```

If any key falls back to `sim2real-bootstrap default` that should have come from config.md, the parser has a bug — stop and diagnose.

- [ ] **Step 7: Commit**

```bash
git add .claude/skills/sim2real-bootstrap/generate_from_config.py \
        .claude/skills/sim2real-bootstrap/tests/test_generate_from_config_observe.py
git commit -m "feat(bootstrap): add --emit-observe-yaml CLI mode

Wires parse_observe_block + render_blis_observe_yaml into the
existing generate_from_config.py CLI. When --emit-observe-yaml is
set, prints only the blis_observe: fragment to stdout and returns
without touching scenario output. Missing config.md → all-defaults
fragment, exit 0 (per issue #403 acceptance).

Refs #403"
```

---

## Task 3: SKILL.md — instruct Task 5 to invoke the new CLI

**Files:**
- Modify: `.claude/skills/sim2real-bootstrap/SKILL.md`

**Interfaces:**
- Consumes: `--emit-observe-yaml` CLI from Task 2.
- Produces: updated Task 5 instructions that (a) include `blis_observe:` in the output schema example, and (b) tell the agent to invoke the new CLI and paste its output into `transfer.yaml`.

- [ ] **Step 1: Update the Task 5 output schema (`SKILL.md:317-358`)**

Read the current output-schema block first to confirm exact indentation. Then in `.claude/skills/sim2real-bootstrap/SKILL.md`, locate the fenced code block starting with `kind: sim2real-transfer` under "**Output schema:**" (currently around line 318). Insert a `blis_observe:` block between `workloads:` and `context:` so the schema now reads:

```yaml
kind: sim2real-transfer
version: 3

scenario: <derived>

component:
  repo: <$COMPONENT_NAME>
  kind: <derived from algorithm interface>
  ref: <$COMPONENT_REF>
  base_image:
    hub: <derived from component repo org, e.g., ghcr.io/llm-d>
    name: <$COMPONENT_NAME>
  build:
    commands: <derived from algorithm imports>

algorithms:
  - name: <lowercase-alphanumeric>
    source: <relative path to algorithm .go file>
    defaults: <name of baseline from task-3 — must match baselines[].name>

baselines:
  - name: <from task-3>
    scenario: <path to baseline YAML>

workloads: <list from task-4>

blis_observe:
  # Populated by `generate_from_config.py --emit-observe-yaml` (see Derivation
  # step 10 below). Each key carries a `# source:` comment indicating whether
  # it came from the `blis observe \ ... \` block in config.md or from the
  # sim2real-bootstrap default (which matches pipeline/pipeline.yaml).
  maxConcurrency: <value>  # source: config.md | sim2real-bootstrap default
  timeout: <value>         # source: config.md | sim2real-bootstrap default
  warmupRequests: <value>  # source: config.md | sim2real-bootstrap default
  prewarmDuration: <value> # source: config.md | sim2real-bootstrap default
  extraArgs: <value>       # source: config.md | sim2real-bootstrap default

context:
  text: <derived summary>
  files: <list of context files>

defaults:
  disable: []
  # Available fragments (filename stems in baselines/defaults/):
  #   - epp-verbosity
  #   - externally-managed-gateway
  #   - llm-d-rbac
  #   - preserve-request-id
  #   - routing-proxy-resources
  #   - vllm-logging
```

- [ ] **Step 2: Add a derivation step for `blis_observe:` in Task 5**

Locate the numbered "Derivation:" list in Task 5 (currently 9 items ending with "context.text: summary of target interface..." at `SKILL.md:315`). Append a new item 10 between item 9 and the "**Output schema:**" heading:

```
10. `blis_observe`: obtained by invoking
    `python3 "$SKILL_DIR/generate_from_config.py" "$EXPERIMENT_ROOT/config.md" --emit-observe-yaml`
    and pasting its stdout verbatim between `workloads:` and `context:`. The
    script parses the `blis observe \ ... \` block in config.md and emits all
    5 keys with per-key `# source:` provenance comments; if `config.md` is
    absent or the block is missing, an all-defaults fragment is emitted. Do
    NOT hand-edit the fragment — regenerate by re-running the script.
```

- [ ] **Step 3: Add a bulleted note under "**Constraints:**" in Task 5**

Immediately after the existing "`component` required when `algorithms` is non-empty" line (currently `SKILL.md:377`), add:

```
- `blis_observe` keys must match the schema in `pipeline/lib/manifest.py`:
  exactly the 5 keys `maxConcurrency`, `timeout`, `warmupRequests`,
  `prewarmDuration`, `extraArgs`. Values must be scalars (string or number,
  not bool). Do not add other keys — the manifest validator rejects them.
```

- [ ] **Step 4: Verify the SKILL.md still validates as valid markdown**

```bash
python3 -c "
import pathlib
p = pathlib.Path('.claude/skills/sim2real-bootstrap/SKILL.md')
text = p.read_text()
# Sanity check: yaml block boundaries balanced, ATX-heading count unchanged.
assert text.count('\`\`\`yaml') == text.count('\`\`\`', 0, text.rfind('\`\`\`')+3) // 2 - text.count('\`\`\`bash') - text.count('\`\`\`markdown'), 'yaml fence count wrong'
print('SKILL.md fences OK')
print(f'lines: {len(text.splitlines())}')
"
```

Expected: prints `SKILL.md fences OK` and a line count. (The check is loose — the real validation is that a human agent reading SKILL.md can follow the new step. If in doubt, re-read Task 5 end-to-end and confirm the new derivation slots in coherently.)

- [ ] **Step 5: Commit**

```bash
git add .claude/skills/sim2real-bootstrap/SKILL.md
git commit -m "docs(bootstrap): SKILL.md Task 5 emits blis_observe from config.md

Update Task 5 output schema to include blis_observe: block between
workloads: and context:. Add derivation step 10 that invokes
generate_from_config.py --emit-observe-yaml. Add a constraint note
about the exact 5-key schema.

Refs #403"
```

---

## Task 4: Stale-reference sweep, end-to-end validation, PR

**Files:**
- Potentially modify: `pipeline/README.md`, any other doc referencing bootstrap-emitted transfer.yaml.
- New end-to-end assertion (no new file — folded into existing test file).

- [ ] **Step 1: Sweep for stale references**

Run these greps and inspect each hit:

```bash
grep -rn "blis_observe" \
    docs/ pipeline/README.md README.md \
    .claude/skills/ 2>/dev/null
grep -rn "blis observe" \
    docs/ pipeline/README.md README.md \
    .claude/skills/sim2real-bootstrap/ 2>/dev/null
grep -rn "sim2real-bootstrap default" \
    docs/ pipeline/README.md README.md \
    .claude/skills/ 2>/dev/null
```

For each hit, decide:
- **Stale (documents old behavior — no `blis_observe:` in emitted transfer.yaml):** update to reflect the new behavior. Include in this PR.
- **Still accurate:** leave alone.
- **Unrelated:** leave alone.

Known likely stale ref: `pipeline/README.md` may describe the bootstrap-emitted `transfer.yaml` shape in its "Contributing" / "Workspace Artifacts" section. If it does and does not mention `blis_observe:`, add a one-line note (do not restructure).

- [ ] **Step 2: Add an end-to-end acceptance test**

Append to `.claude/skills/sim2real-bootstrap/tests/test_generate_from_config_observe.py`:

```python
# ---------------------------------------------------------------------------
# End-to-end acceptance: emitted fragment loads through manifest.py
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parents[4]  # …/sim2real
sys.path.insert(0, str(REPO_ROOT / "pipeline" / "lib"))


def test_emitted_fragment_loads_through_manifest_validator(tmp_path):
    """The whole point of the bootstrap change: a transfer.yaml with the
    emitted blis_observe: block must validate cleanly through
    pipeline/lib/manifest.py:load_manifest."""
    import manifest as pipeline_manifest  # noqa: E402

    # Emit the fragment.
    fragment = gfc.render_blis_observe_yaml(gfc.parse_observe_block(SAMPLE_FULL_BLOCK))

    # Assemble a minimal transfer.yaml that includes it.
    transfer_yaml = f"""kind: sim2real-transfer
version: 3
scenario: test
component:
  repo: dummy
  kind: EndpointPickerConfig
  ref: 0000000000000000000000000000000000000000
  base_image:
    hub: ghcr.io/example
    name: dummy
  build:
    commands: []
algorithms:
  - name: a1
    source: algo.go
    defaults: baseline
baselines:
  - name: baseline
    scenario: baselines/baseline.yaml
workloads:
  - workloads/w1.yaml
{fragment}context:
  text: "test"
  files: []
defaults:
  disable: []
"""
    manifest_path = tmp_path / "transfer.yaml"
    manifest_path.write_text(transfer_yaml)

    loaded = pipeline_manifest.load_manifest(str(manifest_path))
    assert loaded["blis_observe"] == {
        "maxConcurrency": 10000,
        "timeout": 1800,
        "warmupRequests": 50,
        "prewarmDuration": "60s",
        "extraArgs": "",
    }
```

- [ ] **Step 3: Run the acceptance test**

```bash
python -m pytest .claude/skills/sim2real-bootstrap/tests/test_generate_from_config_observe.py::test_emitted_fragment_loads_through_manifest_validator -v
```

Expected: PASS. If it fails on `manifest.load_manifest`, inspect the actual failure — the fragment's YAML output may have a formatting issue the parser doesn't like (e.g. indentation mismatch, missing newline).

- [ ] **Step 4: Run the full CI-equivalent test suite**

```bash
python -m pytest pipeline/ \
  pipeline/tests/test_layout.py \
  pipeline/tests/test_cluster_ops.py \
  pipeline/tests/test_cluster_py.py \
  pipeline/tests/test_slicer.py \
  pipeline/tests/test_sim2real.py \
  pipeline/tests/test_assemble_run.py \
  pipeline/tests/test_translation_ref.py \
  pipeline/tests/test_translate.py \
  pipeline/tests/test_build.py \
  pipeline/tests/test_pairkey.py \
  pipeline/tests/test_load_pairs.py \
  .claude/skills/sim2real-analyze/tests/ \
  .claude/skills/sim2real-bootstrap/tests/ \
  .claude/skills/sim2real-translate/tests/ \
  .claude/skills/sim2real-check/tests/ \
  -v 2>&1 | tail -40
```

Expected: all tests PASS. If any pre-existing test fails, stop and diagnose — the new code should not affect any code path outside `--emit-observe-yaml`.

- [ ] **Step 5: Run the lint check**

```bash
ruff check pipeline/ .claude/skills/ --select F
```

Expected: no errors (only `F` codes are enforced by CI — style-only warnings are OK to leave).

- [ ] **Step 6: Commit sweep updates (if any) and the end-to-end test**

```bash
git add -A
git status  # verify only intended paths
git commit -m "test(bootstrap): end-to-end validation of emitted blis_observe fragment

Add integration test that pipes render_blis_observe_yaml output
through pipeline/lib/manifest.py:load_manifest, confirming the
emitted fragment satisfies the schema validator. Also sweep docs
for any stale references to bootstrap-emitted transfer.yaml.

Closes #403"
```

If no docs were updated in Step 1, drop the "Also sweep..." sentence from the message.

- [ ] **Step 7: Push and open PR**

```bash
git push -u origin worktree-issue-403-blis-observe-bootstrap
gh pr create --title "bootstrap: emit blis_observe: from config.md in transfer.yaml" \
    --body-file - <<'EOF'
Closes #403.

## Summary

Adds `parse_observe_block()`, `render_blis_observe_yaml()`, and a
`--emit-observe-yaml` CLI mode to `generate_from_config.py`. Updates
SKILL.md Task 5 to invoke the new CLI and paste its output into the
emitted `transfer.yaml`.

## Details

- Parser extracts `--max-concurrency`, `--timeout`, `--warmup-requests`,
  `--prewarm-duration` from the `blis observe \ ... \` command block in
  `config.md`. Pipeline-injected flags (`--server-url`, `--model`,
  `--workload-spec`, `--trace-*`, `--saturation-report`,
  `--post-hoc-detector`) are dropped, not folded into `extraArgs` —
  they are hardcoded by the Tekton task at runtime. Unknown flags
  collect into `extraArgs` as an escape hatch.
- Renderer emits all 5 keys in canonical order with per-key
  `# source:` provenance (`config.md` vs `sim2real-bootstrap
  default`). Defaults match `pipeline/pipeline.yaml:36-50` verbatim.
- CLI mode is invoked by SKILL.md Task 5 derivation step 10; output is
  pasted between `workloads:` and `context:` in the emitted
  `transfer.yaml`.

## Acceptance criteria (from #403)

- [x] config.md present with full block → all 5 keys emitted, 4
      marked `source: config.md`, `extraArgs` defaulted.
- [x] config.md present with partial block → present keys marked
      `source: config.md`, missing keys defaulted.
- [x] config.md present without `blis observe` block → all 5
      defaulted.
- [x] config.md absent → all 5 defaulted, exit 0.
- [x] Generated `transfer.yaml` loads cleanly through
      `pipeline/lib/manifest.py` (covered by
      `test_emitted_fragment_loads_through_manifest_validator`).

## Design deviation from issue text

The issue mapping table says "any other flags found in the block →
concatenated into `extraArgs`". Following that literally would fold
Pipeline-injected flags (`--server-url`, `--model`, `--trace-*`, etc.)
into `extraArgs`, which is wrong — the Tekton task
(`tektonc-data-collection/tekton/tasks/run-workload-blis-observe-binary.yaml`)
supplies those at runtime, so listing them in `extraArgs` would
duplicate or override them. This PR whitelists 7 known Pipeline-
injected flags and drops them; everything else goes into `extraArgs`.

## Stale-doc sweep

<fill in from Task 4 Step 1 — say what was searched and what changed
(if nothing changed, say "swept docs/, pipeline/README.md, and
.claude/skills/ for blis_observe / blis observe / sim2real-bootstrap
default references; no stale references found").>
EOF
```

If `gh pr create` fails with `Resource not accessible by personal access token`, silently retry with `unset GITHUB_TOKEN GH_TOKEN; gh pr create ...`.

Capture the PR URL from the output.

- [ ] **Step 8: Report PR URL and hand off**

Print the PR URL to the user. This is the end of `/implement-issue` — the surrounding `/fix-issue` chain will pick up with the review-changes workflow next.

---

## Self-Review

- **Spec coverage:** Every acceptance criterion in #403 is tested. Full block → `test_full_block_extracts_all_four_tuning_flags` + `test_cli_emit_observe_full_block`. Partial block → `test_partial_block_extracts_only_present_flags` + `test_cli_emit_observe_partial_block`. No block → `test_absent_block_returns_empty_dict` + `test_cli_emit_observe_no_block_all_defaults`. Absent config → `test_cli_emit_observe_absent_config_all_defaults`. Manifest load → `test_emitted_fragment_loads_through_manifest_validator`.
- **Placeholder scan:** All steps show the exact code / commands to run. No "TBD" or "handle edge cases." The one place with an interpolated section is the PR body's "Stale-doc sweep" which explicitly directs the author to substitute the Step 1 output — that is the intended shape.
- **Type consistency:** `parse_observe_block` returns `dict[str, str]` in Task 1 and is consumed as `dict[str, str]` by `render_blis_observe_yaml` in Task 1 and by the CLI wire-up in Task 2. `OBSERVE_DEFAULTS` values are `str` (matching pipeline.yaml's stringly-typed Tekton params); the renderer casts to bare int when the value is all-digits, which lets PyYAML round-trip them as ints per the manifest schema's `str|int|float` acceptance.
- **Deviation from issue:** the "greedy extraArgs" rule is deliberately narrowed to a whitelist. Called out in the PR body and justified inline (the Tekton task supplies the injected flags). Reviewer should scrutinize this choice.
