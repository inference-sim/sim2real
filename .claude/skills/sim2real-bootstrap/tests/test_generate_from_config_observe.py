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
