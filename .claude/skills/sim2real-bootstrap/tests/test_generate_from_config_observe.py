"""Tests for parse_observe_block + render_blis_observe_yaml.

Covers acceptance criteria from issues #403 and #602:
  - Full `blis observe \\ ... \\` block → all 4 tuning keys extracted
  - Partial block → only present keys extracted
  - No block in text → empty dict
  - Pipeline-injected flags dropped, not folded into extraArgs
  - Valid-but-unmodeled observe flags pass through to extraArgs (#602)
  - Non-observe flags (replay-only, sim-world, unknown) dropped + warned, never
    transcribed into extraArgs (#602)
  - Rendered YAML has correct provenance for extracted vs defaulted keys
  - Rendered YAML round-trips through PyYAML with expected typing
  - Rendered YAML matches the 5-key schema validated by manifest.py
"""
import sys
from pathlib import Path

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


def test_corpus_mode_injected_flags_are_dropped_not_extraargs():
    """Corpus-mode inputs (--corpus-header/--corpus-data) and the pool flags
    (--concurrent-sessions/--total-sessions) are injected by the Tekton task's
    trace-mode branch. A config.md block that spells them out for readability
    must NOT leak them into extraArgs (which would double-inject them and cause
    a duplicate-flag error). Regression for #605."""
    text = """\
```bash
blis observe \\
  --server-url http://gateway:80 \\
  --model foo/bar \\
  --corpus-header trace.yaml \\
  --corpus-data trace.csv \\
  --concurrent-sessions 128 \\
  --total-sessions 192 \\
  --max-concurrency 10000 \\
  --saturation-report s.json
```
"""
    parsed = gfc.parse_observe_block(text)
    # Only the tuning flag survives; every injected flag is dropped.
    assert parsed == {"maxConcurrency": "10000"}
    assert "extraArgs" not in parsed


def test_non_observe_flags_are_dropped_not_extraargs():
    """Flags that are not in blis observe's namespace are refused, not folded
    into extraArgs (which would abort observe at runtime). Issue #602."""
    text = """\
```bash
blis observe \\
  --max-concurrency 100 \\
  --new-flag foo \\
  --another-flag bar
```
"""
    parsed = gfc.parse_observe_block(text)
    assert parsed == {"maxConcurrency": "100"}
    assert "extraArgs" not in parsed


def test_bare_non_observe_flag_is_dropped_not_extraargs():
    text = """\
```bash
blis observe \\
  --verbose \\
  --timeout 60
```
"""
    parsed = gfc.parse_observe_block(text)
    assert parsed == {"timeout": "60"}
    assert "extraArgs" not in parsed


def test_session_mode_is_dropped_not_extraargs():
    """The documented failure (#602): a transcribed `blis replay` closed-loop
    invocation carries --session-mode (+ pool flags). --session-mode has no
    observe equivalent and must be dropped, not transcribed — otherwise observe
    aborts with `unknown flag: --session-mode`."""
    text = """\
```bash
blis observe \\
  --session-mode closed-loop \\
  --concurrent-sessions 128 \\
  --total-sessions 192 \\
  --max-concurrency 10000
```
"""
    parsed = gfc.parse_observe_block(text)
    # --session-mode dropped (replay-only); pool flags dropped (task-injected);
    # only the modeled tuning flag survives.
    assert parsed == {"maxConcurrency": "10000"}
    assert "extraArgs" not in parsed


def test_sim_world_flags_are_dropped_not_extraargs():
    """Simulator model-of-the-world flags (routing/hardware/instances) are
    realized by the real deployment + EPP; they must never reach observe."""
    text = """\
```bash
blis observe \\
  --num-instances 4 \\
  --routing-policy least-loaded \\
  --total-kv-blocks 8192 \\
  --hardware H100 \\
  --tp 2 \\
  --timeout 60
```
"""
    parsed = gfc.parse_observe_block(text)
    assert parsed == {"timeout": "60"}
    assert "extraArgs" not in parsed


def test_valid_but_unmodeled_observe_flags_pass_through_to_extraargs():
    """Real blis observe flags without a first-class key survive into extraArgs
    so the operator can tune them in transfer.yaml. Issue #602 choice (a)."""
    text = """\
```bash
blis observe \\
  --rate 50 \\
  --num-requests 1000 \\
  --no-streaming \\
  --timeout 60
```
"""
    parsed = gfc.parse_observe_block(text)
    assert parsed["timeout"] == "60"
    assert parsed["extraArgs"] == "--rate 50 --num-requests 1000 --no-streaming"


def test_seed_and_saturation_flags_pass_through_to_extraargs():
    """Regression for the review of #602: --seed (Int64Var, missed by the first
    allowlist regex) and the --saturation-* backlog-drift flags (registered on
    observeCmd via registerSaturationFlags, not inline) are real observe flags
    and must survive into extraArgs, not be dropped."""
    text = """\
```bash
blis observe \\
  --seed 42 \\
  --saturation-window 5s \\
  --saturation-classifier composite \\
  --timeout 60
```
"""
    parsed = gfc.parse_observe_block(text)
    assert parsed["timeout"] == "60"
    assert parsed["extraArgs"] == (
        "--seed 42 --saturation-window 5s --saturation-classifier composite"
    )


def test_equals_form_flags_are_handled():
    """--flag=value must classify on the flag name: a modeled tuning flag maps
    to its key, a valid-but-unmodeled flag passes through verbatim, and a
    non-observe flag is still dropped."""
    text = """\
```bash
blis observe \\
  --timeout=900 \\
  --rate=50 \\
  --session-mode=closed-loop
```
"""
    parsed = gfc.parse_observe_block(text)
    assert parsed["timeout"] == "900"
    assert parsed["extraArgs"] == "--rate=50"


def test_tuning_flag_with_no_value_is_dropped_and_warned(capsys):
    """A recognized tuning flag with no usable value (`--timeout` with no arg,
    or the `=`-form `--timeout=`) is malformed: drop it and warn so the
    bootstrap default applies, rather than leak an empty value into
    transfer.yaml tagged as config.md-sourced. Issue #602 review."""
    text = """\
```bash
blis observe \\
  --timeout= \\
  --max-concurrency \\
  --rate 50
```
"""
    parsed = gfc.parse_observe_block(text)
    # Neither malformed tuning flag lands a key; only the valid unmodeled flag.
    assert "timeout" not in parsed
    assert "maxConcurrency" not in parsed
    assert parsed["extraArgs"] == "--rate 50"
    err = capsys.readouterr().err
    assert "--timeout" in err
    assert "--max-concurrency" in err
    assert "recognized flag with no value" in err


def test_dropped_flag_emits_warning_to_stderr(capsys):
    """Refusing a flag is surfaced, not silent, so the operator can re-add a
    legitimate flag in transfer.yaml. Issue #602."""
    text = """\
```bash
blis observe \\
  --session-mode closed-loop \\
  --frobnicate x \\
  --timeout 60
```
"""
    gfc.parse_observe_block(text)
    err = capsys.readouterr().err
    assert "--session-mode" in err
    assert "replay/sim flag with no blis observe equivalent" in err
    assert "--frobnicate" in err
    assert "not a blis observe flag" in err


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
    parsed = {"extraArgs": "--rate 50 --no-streaming"}
    out = gfc.render_blis_observe_yaml(parsed)
    loaded = yaml.safe_load(out)
    assert loaded["blis_observe"]["extraArgs"] == "--rate 50 --no-streaming"


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
