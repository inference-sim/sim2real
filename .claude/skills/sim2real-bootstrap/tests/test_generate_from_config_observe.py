"""Tests for parse_observe_block + render_measurement_yaml.

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
  - Rendered YAML matches the nine-key schema validated by manifest.py
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
    --saturation-report are supplied by the pipeline and MUST NOT leak
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
    # --no-streaming is no longer extraArgs filler: #900 made it a first-class
    # key, inverted (blis has no --streaming, so presence means streaming off).
    assert parsed["streaming"] is False
    assert parsed["extraArgs"] == "--rate 50 --num-requests 1000"


def test_seed_and_saturation_flags_pass_through_to_extraargs():
    """Regression for the review of #602: --seed (Int64Var, missed by the first
    allowlist regex) and the --saturation-* backlog-drift flags (registered on
    observeCmd via registerSaturationFlags, not inline) are real observe flags
    and must survive into extraArgs, not be dropped."""
    text = """\
```bash
blis observe \\
  --seed 42 \\
  --rtt-ms 5 \\
  --slo-ttft 500 \\
  --timeout 60
```
"""
    parsed = gfc.parse_observe_block(text)
    assert parsed["timeout"] == "60"
    assert parsed["extraArgs"] == (
        "--seed 42 --rtt-ms 5 --slo-ttft 500"
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



def _protocol_values(out: str) -> dict:
    """Load an emitted measurement document and drop its kind/version envelope,
    leaving only the roster values — which is what the manifest validates and
    what the renderer consumes."""
    loaded = yaml.safe_load(out)
    return {k: v for k, v in loaded.items() if k not in ("kind", "version")}

# ---------------------------------------------------------------------------
# render_measurement_yaml
# ---------------------------------------------------------------------------

def test_render_all_defaults_when_parsed_empty():
    out = gfc.render_measurement_yaml({})
    # A standalone document now (#911), so it carries its own envelope
    # rather than being nested under a manifest key.
    assert "kind: measurement-protocol" in out
    assert "version: 1" in out
    assert "blis_observe" not in out
    # Every key present with the sim2real-bootstrap default source.
    for key in ("maxConcurrency", "timeout", "warmupRequests",
                "prewarmDuration", "detectors", "apiFormat",
                "recordItl", "streaming", "extraArgs"):
        assert key in out
    assert out.count("# source: sim2real-bootstrap default") == 9
    assert "# source: config.md" not in out


def test_render_full_block_all_from_config():
    parsed = {
        "maxConcurrency": "10000",
        "timeout": "1800",
        "warmupRequests": "50",
        "prewarmDuration": "60s",
    }
    out = gfc.render_measurement_yaml(parsed)
    # 4 sourced from config.md, extraArgs sourced from default.
    assert out.count("# source: config.md") == 4
    assert out.count("# source: sim2real-bootstrap default") == 5


def test_render_mixed_provenance():
    parsed = {"maxConcurrency": "500", "prewarmDuration": "30s"}
    out = gfc.render_measurement_yaml(parsed)
    assert out.count("# source: config.md") == 2
    assert out.count("# source: sim2real-bootstrap default") == 7


def test_render_output_parses_as_yaml_with_expected_types():
    """Numeric-string keys emit as YAML ints; string keys emit as YAML strings.

    Bools appear for exactly two keys. `recordItl` and `streaming` became
    first-class bool keys in #900; every OTHER key must not be a bool, because a
    YAML `true` sitting in an int field is the bug manifest.py's per-key type
    check exists to catch. (This docstring used to say bools must never appear,
    which stopped being true at #900 and was contradicted by the assertions
    below.)"""
    out = gfc.render_measurement_yaml({})
    loaded = _protocol_values(out)
    assert loaded == {
            "maxConcurrency": 10000,
            "timeout": 1800,
            "warmupRequests": 50,
            "prewarmDuration": "60s",
            "detectors": "composite",
            "apiFormat": "completions",
            "recordItl": False,
            "streaming": True,
        "extraArgs": "",
    }
    # recordItl and streaming are bools by design since #900; every other key
    # must still not be one (a YAML `true` in an int field is the bug the
    # manifest's per-key type check exists to catch).
    for k, v in loaded.items():
        if k in ("recordItl", "streaming"):
            assert isinstance(v, bool), f"{k} must be a real YAML boolean"
        else:
            assert not isinstance(v, bool), f"{k} must not be a bool"


def test_render_extra_args_from_config_stays_a_string():
    parsed = {"extraArgs": "--rate 50 --no-streaming"}
    out = gfc.render_measurement_yaml(parsed)
    assert _protocol_values(out)["extraArgs"] == "--rate 50 --no-streaming"


def test_render_key_order_is_canonical():
    """Order must be stable so operators skimming transfer.yaml find keys
    predictably. Mirrors OBSERVE_DEFAULTS declaration order."""
    out = gfc.render_measurement_yaml({})
    lines = [
        ln.strip() for ln in out.splitlines()
        if ln.strip() and not ln.startswith("#")
        and not ln.startswith(("kind:", "version:"))
    ]
    keys_in_order = [ln.split(":")[0] for ln in lines]
    assert keys_in_order == [
        "maxConcurrency", "timeout", "warmupRequests", "prewarmDuration",
        "detectors", "apiFormat", "recordItl", "streaming", "extraArgs",
    ]


# ---------------------------------------------------------------------------
# --emit-measurement-yaml CLI mode
# ---------------------------------------------------------------------------

import subprocess

SCRIPT = str(Path(__file__).parents[1] / "generate_from_config.py")


def _run_emit_observe(tmp_path, config_text: str | None):
    """Invoke `generate_from_config.py --emit-measurement-yaml` and return (stdout, stderr, rc).

    If config_text is None, do not create the file (test config-absent case).
    Otherwise, write it to tmp_path/config.md and pass that path.
    """
    if config_text is None:
        config_path = tmp_path / "nonexistent.md"
    else:
        config_path = tmp_path / "config.md"
        config_path.write_text(config_text)
    result = subprocess.run(
        ["python3", SCRIPT, str(config_path), "--emit-measurement-yaml"],
        capture_output=True, text=True,
    )
    return result.stdout, result.stderr, result.returncode


def test_cli_emit_observe_full_block(tmp_path):
    stdout, stderr, rc = _run_emit_observe(tmp_path, SAMPLE_FULL_BLOCK)
    assert rc == 0, stderr
    assert _protocol_values(stdout) == {
        "maxConcurrency": 10000,
        "timeout": 1800,
        "warmupRequests": 50,
        "prewarmDuration": "60s",
        "detectors": "composite",
        "apiFormat": "completions",
        "recordItl": False,
        "streaming": True,
        "extraArgs": "",
    }
    # 4 keys from config.md, extraArgs defaulted.
    assert stdout.count("# source: config.md") == 4
    assert stdout.count("# source: sim2real-bootstrap default") == 5


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
    assert _protocol_values(stdout)["maxConcurrency"] == 500
    assert _protocol_values(stdout)["timeout"] == 60
    # warmupRequests + prewarmDuration + extraArgs defaulted.
    assert stdout.count("# source: config.md") == 2
    assert stdout.count("# source: sim2real-bootstrap default") == 7


def test_cli_emit_observe_no_block_all_defaults(tmp_path):
    stdout, stderr, rc = _run_emit_observe(tmp_path, "# Nothing here\n")
    assert rc == 0, stderr
    assert _protocol_values(stdout) == {
        "maxConcurrency": 10000, "timeout": 1800, "warmupRequests": 50,
        "prewarmDuration": "60s", "detectors": "composite",
        "apiFormat": "completions", "recordItl": False,
        "streaming": True, "extraArgs": "",
    }
    assert stdout.count("# source: sim2real-bootstrap default") == 9


def test_cli_emit_observe_absent_config_all_defaults(tmp_path):
    """Per issue #403 acceptance criteria: config.md absent → all defaults, exit 0."""
    stdout, stderr, rc = _run_emit_observe(tmp_path, None)
    assert rc == 0, stderr
    assert _protocol_values(stdout) == {
        "maxConcurrency": 10000, "timeout": 1800, "warmupRequests": 50,
        "prewarmDuration": "60s", "detectors": "composite",
        "apiFormat": "completions", "recordItl": False,
        "streaming": True, "extraArgs": "",
    }


# ---------------------------------------------------------------------------
# End-to-end acceptance: emitted fragment loads through manifest.py
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parents[4]  # …/sim2real
sys.path.insert(0, str(REPO_ROOT / "pipeline" / "lib"))


def test_emitted_document_loads_through_manifest_validator(tmp_path):
    """The whole point of the bootstrap change: a bundle built from what the
    generator emits must validate cleanly through
    pipeline/lib/manifest.py:load_manifest.

    Since #911 the emitted YAML is a SEPARATE FILE, so this writes the real
    two-file shape — transfer.yaml with `measurement: measurement.yaml` plus the
    document beside it — rather than splicing a fragment inline. That is the
    coupling worth testing: the generator and the loader must agree on the file's
    envelope and key names, and previously they silently did not have to."""
    import manifest as pipeline_manifest  # noqa: E402

    # Emit the protocol document and write it as its own file.
    document = gfc.render_measurement_yaml(gfc.parse_observe_block(SAMPLE_FULL_BLOCK))
    (tmp_path / "measurement.yaml").write_text(document)

    transfer_yaml = """kind: sim2real-transfer
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
measurement: measurement.yaml
context:
  text: "test"
  files: []
defaults:
  disable: []
"""
    manifest_path = tmp_path / "transfer.yaml"
    manifest_path.write_text(transfer_yaml)

    loaded = pipeline_manifest.load_manifest(str(manifest_path))
    assert loaded["measurement"] == {
        "maxConcurrency": 10000,
        "timeout": 1800,
        "warmupRequests": 50,
        "prewarmDuration": "60s",
        "detectors": "composite",
        "apiFormat": "completions",
        "recordItl": False,
        "streaming": True,
        "extraArgs": "",
    }
