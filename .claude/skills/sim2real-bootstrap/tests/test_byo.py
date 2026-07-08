"""Tests for the BYO branch of sim2real-bootstrap (``byo.py``).

Covers the acceptance criteria from issue #499:
  - BYO happy path — 2 algorithms
  - BYO error paths (missing/duplicate args, malformed YAML, path traversal)
  - Non-interactive behavior (stdin non-TTY or --non-interactive)
  - Args-vs-NL parse equivalence (structured args and NL-derived arg-dict
    produce equivalent triples — tested on the parser helper)
  - Path safety (shlex.quote against spaces / $ / ` / ; )
  - BLIS regression (BLIS-mode dispatch unchanged; existing skill files
    untouched by this module)
  - Emitted transfer.yaml loads via pipeline/lib/manifest.load_manifest
  - Emitted register command passes the actual sim2real register argparser
"""
from __future__ import annotations

import os
import shlex
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

# Locate the skill dir (this test file's parent-parent) and put both the
# skill dir and the repo root on sys.path so we can import byo directly and
# also import from pipeline.lib.
_SKILL_DIR = Path(__file__).resolve().parents[1]
_REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(_SKILL_DIR))
sys.path.insert(0, str(_REPO_ROOT))
import byo  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _valid_scenario_yaml(name: str = "baseline") -> str:
    """Minimal scenario body accepted by yaml.safe_load; single mapping doc."""
    return textwrap.dedent(f"""\
        scenario:
        - name: {name}
          model:
            name: meta-llama/Llama-3.1-8B
            path: models/meta-llama/Llama-3.1-8B
          decode:
            replicas: 1
    """)


def _valid_overlay_yaml(algo: str) -> str:
    return textwrap.dedent(f"""\
        scenario:
        - name: {algo}
          decode:
            replicas: 2
    """)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


@pytest.fixture
def bare_exp_root(tmp_path: Path) -> Path:
    """Fake experiment repo with only workloads/ populated (BYO input shape)."""
    root = tmp_path / "myexperiment"
    (root / "workloads").mkdir(parents=True)
    _write(root / "workloads" / "w1.yaml", "workload:\n  name: w1\n")
    _write(root / "workloads" / "w2.yaml", "workload:\n  name: w2\n")
    return root


@pytest.fixture
def operator_files(tmp_path: Path) -> dict[str, Path]:
    """Operator-supplied files, placed outside the experiment repo."""
    src = tmp_path / "operator"
    src.mkdir()
    baseline_path = _write(src / "baseline.yaml", _valid_scenario_yaml("baseline"))
    foo_overlay = _write(src / "foo_overlay.yaml", _valid_overlay_yaml("foo"))
    bar_overlay = _write(src / "bar_overlay.yaml", _valid_overlay_yaml("bar"))
    return {
        "baseline": baseline_path,
        "foo": foo_overlay,
        "bar": bar_overlay,
    }


def _happy_argv(
    files: dict[str, Path],
    *,
    non_interactive: bool = True,
    extra: list[str] | None = None,
) -> list[str]:
    argv = [
        "--byo",
        "--baseline", str(files['baseline']),
        "--algorithm", "foo",
        "--algorithm", "bar",
        "--algorithm-image", "foo=ghcr.io/example/foo:latest",
        "--algorithm-image", "bar=ghcr.io/example/bar@sha256:" + "a" * 64,
        "--algorithm-config", f"foo={files['foo']}",
        "--algorithm-config", f"bar={files['bar']}",
    ]
    if non_interactive:
        argv.append("--non-interactive")
    if extra:
        argv.extend(extra)
    return argv


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_happy_path_two_algorithms(bare_exp_root, operator_files):
    argv = _happy_argv(operator_files)
    code, register_cmd = byo.run_byo(
        argv, bare_exp_root, _SKILL_DIR, stdin_isatty=False
    )
    assert code == 0, register_cmd

    # baselines/baseline.yaml exists with baseline content
    baseline_dst = bare_exp_root / "baselines" / "baseline.yaml"
    assert baseline_dst.exists()
    assert baseline_dst.read_text() == operator_files["baseline"].read_text()

    # algorithms/<algo>/<algo>_config.yaml exists per algorithm
    for algo in ("foo", "bar"):
        dst = bare_exp_root / "algorithms" / algo / f"{algo}_config.yaml"
        assert dst.exists()
        assert dst.read_text() == operator_files[algo].read_text()

    # framework defaults copied verbatim
    defaults_dir = bare_exp_root / "baselines" / "defaults"
    template_dir = _SKILL_DIR / "templates" / "defaults"
    for src in sorted(template_dir.glob("*.yaml")):
        dst = defaults_dir / src.name
        assert dst.exists()
        assert dst.read_text() == src.read_text()

    # transfer.yaml loads via manifest.load_manifest
    transfer_path = bare_exp_root / "transfer.yaml"
    assert transfer_path.exists()
    from pipeline.lib.manifest import load_manifest
    manifest = load_manifest(transfer_path)

    # component absent, every algorithm has byo: true and no source
    assert "component" not in manifest
    algo_names_manifest = [a["name"] for a in manifest["algorithms"]]
    assert sorted(algo_names_manifest) == ["bar", "foo"]
    for entry in manifest["algorithms"]:
        assert entry.get("byo") is True
        assert "source" not in entry
        assert entry["defaults"] == "baseline"

    # defaults.disable matches the sorted stems of templates/defaults/*.yaml
    expected_stems = sorted(p.stem for p in template_dir.glob("*.yaml"))
    assert manifest["defaults"]["disable"] == expected_stems

    # Register command shlex-round-trips
    tokens = shlex.split(register_cmd)
    assert "sim2real" in tokens
    assert "register" in tokens
    # Extract each --algorithm value and parse it via the actual register CLI's
    # triple parser — the code that the register subcommand runs at parse time.
    from pipeline.sim2real import _parse_algorithm_triple
    algo_values = [
        tokens[i + 1] for i, t in enumerate(tokens) if t == "--algorithm"
    ]
    assert len(algo_values) == 2
    parsed = [_parse_algorithm_triple(v) for v in algo_values]
    parsed_names = {p[0] for p in parsed}
    assert parsed_names == {"foo", "bar"}

    # And the emitted command's --algorithm args are accepted by the top-level
    # register argparser (require=True on --algorithm).
    from pipeline.sim2real import build_parser as build_top_parser
    # Strip the `cd '<path>'` line and the `sim2real translation register` head
    # to isolate the argparse-facing arg vector.
    lines = [ln.rstrip(" \\") for ln in register_cmd.split("\n")]
    all_tokens = shlex.split(" ".join(lines))
    # tokens = ['cd', '<path>', 'sim2real', 'translation', 'register',
    #           '--algorithm', '<triple>', '--algorithm', '<triple>']
    idx = all_tokens.index("register")
    argparser_args = all_tokens[idx:]  # ['register', '--algorithm', ...]
    # The top-level sim2real parser expects `translation register --algorithm...`
    # Rebuild that arg vector.
    top_argv = ["translation"] + argparser_args
    parser = build_top_parser()
    ns = parser.parse_args(top_argv)
    assert ns.command == "translation"
    assert ns.subcommand == "register"
    assert len(ns.algorithm) == 2


def test_register_command_starts_with_cd_to_abs_exp_root(bare_exp_root, operator_files):
    argv = _happy_argv(operator_files)
    code, register_cmd = byo.run_byo(
        argv, bare_exp_root, _SKILL_DIR, stdin_isatty=False
    )
    assert code == 0
    first_line = register_cmd.split("\n")[0]
    # cd <abs-path>
    assert first_line.startswith("cd ")
    quoted_path = first_line[len("cd "):]
    # shlex.split gives us the raw path back
    parts = shlex.split(quoted_path)
    assert len(parts) == 1
    resolved_root = os.path.realpath(bare_exp_root)
    assert parts[0] == resolved_root


def test_happy_path_single_algorithm(tmp_path, operator_files):
    """N=1 is also supported — same path as N=2, just one algorithm."""
    root = tmp_path / "exp"
    (root / "workloads").mkdir(parents=True)
    _write(root / "workloads" / "w1.yaml", "workload:\n  name: w1\n")

    argv = [
        "--byo",
        "--baseline", str(operator_files['baseline']),
        "--algorithm", "foo",
        "--algorithm-image", "foo=ghcr.io/example/foo:latest",
        "--algorithm-config", f"foo={operator_files['foo']}",
        "--non-interactive",
    ]
    code, register_cmd = byo.run_byo(argv, root, _SKILL_DIR, stdin_isatty=False)
    assert code == 0, register_cmd

    from pipeline.lib.manifest import load_manifest
    manifest = load_manifest(root / "transfer.yaml")
    assert [a["name"] for a in manifest["algorithms"]] == ["foo"]


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------

def test_error_missing_baseline(bare_exp_root, operator_files):
    argv = [
        "--byo",
        "--algorithm", "foo",
        "--algorithm-image", "foo=ghcr.io/example/foo:latest",
        "--algorithm-config", f"foo={operator_files['foo']}",
        "--non-interactive",
    ]
    code, message = byo.run_byo(argv, bare_exp_root, _SKILL_DIR, stdin_isatty=False)
    assert code == 2
    assert "baseline" in message.lower()


def test_error_no_algorithm(bare_exp_root, operator_files):
    argv = [
        "--byo",
        "--baseline", str(operator_files['baseline']),
        "--non-interactive",
    ]
    code, message = byo.run_byo(argv, bare_exp_root, _SKILL_DIR, stdin_isatty=False)
    assert code == 2
    assert "algorithm" in message.lower()


def test_error_algorithm_without_image(bare_exp_root, operator_files):
    argv = [
        "--byo",
        "--baseline", str(operator_files['baseline']),
        "--algorithm", "foo",
        "--algorithm-config", f"foo={operator_files['foo']}",
        "--non-interactive",
    ]
    code, message = byo.run_byo(argv, bare_exp_root, _SKILL_DIR, stdin_isatty=False)
    assert code == 2
    assert "foo" in message
    assert "algorithm-image" in message


def test_error_algorithm_without_config(bare_exp_root, operator_files):
    argv = [
        "--byo",
        "--baseline", str(operator_files['baseline']),
        "--algorithm", "foo",
        "--algorithm-image", "foo=ghcr.io/example/foo:latest",
        "--non-interactive",
    ]
    code, message = byo.run_byo(argv, bare_exp_root, _SKILL_DIR, stdin_isatty=False)
    assert code == 2
    assert "foo" in message
    assert "algorithm-config" in message


def test_error_duplicate_algorithm_names(bare_exp_root, operator_files):
    argv = [
        "--byo",
        "--baseline", str(operator_files['baseline']),
        "--algorithm", "foo",
        "--algorithm", "foo",
        "--algorithm-image", "foo=ghcr.io/example/foo:latest",
        "--algorithm-config", f"foo={operator_files['foo']}",
        "--non-interactive",
    ]
    code, message = byo.run_byo(argv, bare_exp_root, _SKILL_DIR, stdin_isatty=False)
    assert code == 2
    assert "duplicate" in message.lower()
    assert "foo" in message


def test_error_workloads_dir_missing(tmp_path, operator_files):
    root = tmp_path / "exp"
    root.mkdir()  # No workloads/ subdir.
    argv = _happy_argv(operator_files)
    code, message = byo.run_byo(argv, root, _SKILL_DIR, stdin_isatty=False)
    assert code == 2
    assert "workloads" in message.lower()


def test_error_workloads_dir_empty(tmp_path, operator_files):
    root = tmp_path / "exp"
    (root / "workloads").mkdir(parents=True)  # Empty.
    argv = _happy_argv(operator_files)
    code, message = byo.run_byo(argv, root, _SKILL_DIR, stdin_isatty=False)
    assert code == 2
    assert "workload" in message.lower()


def test_error_malformed_overlay_yaml(bare_exp_root, operator_files, tmp_path):
    bad = _write(tmp_path / "bad.yaml", "this is: not: valid: yaml: [\n")
    argv = [
        "--byo",
        "--baseline", str(operator_files['baseline']),
        "--algorithm", "foo",
        "--algorithm-image", "foo=ghcr.io/example/foo:latest",
        "--algorithm-config", f"foo={bad}",
        "--non-interactive",
    ]
    code, message = byo.run_byo(argv, bare_exp_root, _SKILL_DIR, stdin_isatty=False)
    assert code == 2
    assert str(bad) in message
    assert "yaml" in message.lower() or "parse" in message.lower()


def test_error_malformed_baseline_yaml(bare_exp_root, operator_files, tmp_path):
    bad = _write(tmp_path / "bad_baseline.yaml", "this is: not: valid: yaml: [\n")
    argv = [
        "--byo",
        "--baseline", str(bad),
        "--algorithm", "foo",
        "--algorithm-image", "foo=ghcr.io/example/foo:latest",
        "--algorithm-config", f"foo={operator_files['foo']}",
        "--non-interactive",
    ]
    code, message = byo.run_byo(argv, bare_exp_root, _SKILL_DIR, stdin_isatty=False)
    assert code == 2
    assert str(bad) in message


@pytest.mark.parametrize("bad_content", [
    "- a\n- b\n- c\n",  # list root
    "just a scalar\n",  # scalar root
    "",                 # empty
])
def test_error_non_mapping_root_yaml(bare_exp_root, operator_files, tmp_path, bad_content):
    bad = _write(tmp_path / "non_mapping.yaml", bad_content)
    argv = [
        "--byo",
        "--baseline", str(operator_files['baseline']),
        "--algorithm", "foo",
        "--algorithm-image", "foo=ghcr.io/example/foo:latest",
        "--algorithm-config", f"foo={bad}",
        "--non-interactive",
    ]
    code, message = byo.run_byo(argv, bare_exp_root, _SKILL_DIR, stdin_isatty=False)
    assert code == 2


def test_error_multi_document_yaml(bare_exp_root, operator_files, tmp_path):
    multi = _write(tmp_path / "multi.yaml", "a: 1\n---\nb: 2\n")
    argv = [
        "--byo",
        "--baseline", str(operator_files['baseline']),
        "--algorithm", "foo",
        "--algorithm-image", "foo=ghcr.io/example/foo:latest",
        "--algorithm-config", f"foo={multi}",
        "--non-interactive",
    ]
    code, message = byo.run_byo(argv, bare_exp_root, _SKILL_DIR, stdin_isatty=False)
    assert code == 2
    assert "multi" in message.lower() or "document" in message.lower()


def test_error_overlay_path_missing(bare_exp_root, operator_files, tmp_path):
    argv = [
        "--byo",
        "--baseline", str(operator_files['baseline']),
        "--algorithm", "foo",
        "--algorithm-image", "foo=ghcr.io/example/foo:latest",
        "--algorithm-config", f"foo={tmp_path / 'does_not_exist.yaml'}",
        "--non-interactive",
    ]
    code, message = byo.run_byo(argv, bare_exp_root, _SKILL_DIR, stdin_isatty=False)
    assert code == 2
    assert "does_not_exist.yaml" in message


def test_error_baseline_path_missing(bare_exp_root, operator_files, tmp_path):
    argv = [
        "--byo",
        "--baseline", str(tmp_path / 'nowhere.yaml'),
        "--algorithm", "foo",
        "--algorithm-image", "foo=ghcr.io/example/foo:latest",
        "--algorithm-config", f"foo={operator_files['foo']}",
        "--non-interactive",
    ]
    code, message = byo.run_byo(argv, bare_exp_root, _SKILL_DIR, stdin_isatty=False)
    assert code == 2
    assert "nowhere.yaml" in message


def test_error_source_symlink_outside_regular_file_required(bare_exp_root, tmp_path):
    """Source symlink that points to a non-regular file (e.g. a directory) is rejected."""
    outside = tmp_path / "outside_dir"
    outside.mkdir()
    src_symlink = tmp_path / "escape.yaml"
    src_symlink.symlink_to(outside)  # Symlink to a directory, not a file
    argv = [
        "--byo",
        "--baseline", str(src_symlink),
        "--algorithm", "foo",
        "--algorithm-image", "foo=ghcr.io/example/foo:latest",
        "--algorithm-config", f"foo={src_symlink}",
        "--non-interactive",
    ]
    code, message = byo.run_byo(argv, bare_exp_root, _SKILL_DIR, stdin_isatty=False)
    assert code == 2
    assert "regular file" in message.lower() or "not a file" in message.lower()


def test_error_dest_parent_symlink_escaping_exp_root(bare_exp_root, operator_files, tmp_path):
    """`<exp-root>/algorithms/` is a symlink that escapes -> destination check fails."""
    escape_target = tmp_path / "escape_target"
    escape_target.mkdir()
    # Replace algorithms/ with a symlink to a directory outside the exp root.
    (bare_exp_root / "algorithms").symlink_to(escape_target)
    argv = _happy_argv(operator_files)
    code, message = byo.run_byo(argv, bare_exp_root, _SKILL_DIR, stdin_isatty=False)
    assert code == 2
    assert "outside" in message.lower() or "traversal" in message.lower() or "escape" in message.lower()


def test_error_existing_dest_non_interactive_refused(bare_exp_root, operator_files):
    # First run to create the destination.
    argv = _happy_argv(operator_files)
    code, _ = byo.run_byo(argv, bare_exp_root, _SKILL_DIR, stdin_isatty=False)
    assert code == 0
    # Second run without --force: fails in non-interactive mode.
    code, message = byo.run_byo(argv, bare_exp_root, _SKILL_DIR, stdin_isatty=False)
    assert code == 2
    assert "exists" in message.lower() and "force" in message.lower()


def test_existing_dest_with_force_overwrites(bare_exp_root, operator_files, tmp_path):
    # First run to create the destination.
    code, _ = byo.run_byo(_happy_argv(operator_files), bare_exp_root, _SKILL_DIR, stdin_isatty=False)
    assert code == 0
    # Change the operator's baseline content, then re-run with --force.
    new_content = _valid_scenario_yaml("baseline") + "  # updated\n"
    operator_files["baseline"].write_text(new_content)
    argv = _happy_argv(operator_files, extra=["--force"])
    code, _ = byo.run_byo(argv, bare_exp_root, _SKILL_DIR, stdin_isatty=False)
    assert code == 0
    assert (bare_exp_root / "baselines" / "baseline.yaml").read_text() == new_content


# ---------------------------------------------------------------------------
# Non-interactive dispatch
# ---------------------------------------------------------------------------

def test_non_interactive_auto_enabled_when_stdin_not_tty(bare_exp_root, operator_files):
    """stdin.isatty() == False alone forces non-interactive; no --non-interactive needed."""
    argv = [
        "--byo",
        # Missing baseline, no --non-interactive; but stdin is non-TTY.
        "--algorithm", "foo",
        "--algorithm-image", "foo=ghcr.io/example/foo:latest",
        "--algorithm-config", f"foo={operator_files['foo']}",
    ]
    code, message = byo.run_byo(argv, bare_exp_root, _SKILL_DIR, stdin_isatty=False)
    assert code == 2
    assert "baseline" in message.lower()


def test_non_interactive_explicit_flag_forces_no_prompt(bare_exp_root, operator_files):
    """--non-interactive forces non-interactive even when stdin is a TTY."""
    argv = [
        "--byo",
        "--algorithm", "foo",
        "--algorithm-image", "foo=ghcr.io/example/foo:latest",
        "--algorithm-config", f"foo={operator_files['foo']}",
        "--non-interactive",
    ]
    # stdin_isatty=True but --non-interactive present → no prompt, fatal.
    code, message = byo.run_byo(argv, bare_exp_root, _SKILL_DIR, stdin_isatty=True)
    assert code == 2
    assert "baseline" in message.lower()


# ---------------------------------------------------------------------------
# Args-vs-NL equivalence
# ---------------------------------------------------------------------------

def test_args_vs_nl_parse_equivalence(operator_files):
    """Structured args and NL-derived argv produce equivalent parsed dicts.

    The NL frontend lives in SKILL.md prose (LLM-driven; not CI-tested per
    design.md#risks). What IS testable is that if an LLM converts NL into
    the structured argv shape, both paths produce the same dict. This test
    encodes that contract on the parser helper.
    """
    argv_structured = _happy_argv(operator_files, non_interactive=False)

    # An NL request like:
    #   "--byo with baseline at <path>, algorithm foo image <img1> config <ov1>,
    #    algorithm bar image <img2> config <ov2>"
    # would produce (from an LLM parser) the same argv tokens as structured
    # args. The parser helper is order-agnostic on the dict shape.
    argv_from_nl_shape = [
        "--byo",
        "--baseline", str(operator_files['baseline']),
        # NL parser might reorder these — validate order-independence
        "--algorithm-image", "foo=ghcr.io/example/foo:latest",
        "--algorithm-image", "bar=ghcr.io/example/bar@sha256:" + "a" * 64,
        "--algorithm", "foo",
        "--algorithm", "bar",
        "--algorithm-config", f"foo={operator_files['foo']}",
        "--algorithm-config", f"bar={operator_files['bar']}",
    ]

    d1 = byo.parse_args_dict(argv_structured)
    d2 = byo.parse_args_dict(argv_from_nl_shape)

    # `algorithms` is order-preserving; NL should produce the same relative
    # order (LLM told "foo then bar" → [foo, bar]).
    assert d1["algorithms"] == d2["algorithms"]
    # Everything else compares as-is.
    assert d1["baseline"] == d2["baseline"]
    assert d1["algorithm_images"] == d2["algorithm_images"]
    assert d1["algorithm_configs"] == d2["algorithm_configs"]
    assert d1["byo"] == d2["byo"]


# ---------------------------------------------------------------------------
# Path safety — shlex.quote against shell metacharacters
# ---------------------------------------------------------------------------

def test_shlex_quote_covers_shell_metacharacters(tmp_path):
    """Register command emission handles spaces, $, `, ; without corruption.

    Uses an experiment root with metacharacters in the path and verifies the
    emitted register command round-trips through shlex.split back to the
    original strings.
    """
    weird_root = tmp_path / "path with spaces & $vars; and `backticks`"
    (weird_root / "workloads").mkdir(parents=True)
    _write(weird_root / "workloads" / "w1.yaml", "workload:\n  name: w1\n")

    # Overlay file with problematic filename.
    weird_overlay_dir = tmp_path / "operator with; $shell `special` chars"
    weird_overlay_dir.mkdir()
    overlay = _write(weird_overlay_dir / "overlay.yaml", _valid_overlay_yaml("foo"))
    baseline = _write(tmp_path / "baseline.yaml", _valid_scenario_yaml("baseline"))

    argv = [
        "--byo",
        "--baseline", str(baseline),
        "--algorithm", "foo",
        "--algorithm-image", "foo=ghcr.io/example/foo:latest",
        "--algorithm-config", f"foo={overlay}",
        "--non-interactive",
    ]
    code, register_cmd = byo.run_byo(argv, weird_root, _SKILL_DIR, stdin_isatty=False)
    assert code == 0

    # Round-trip: shlex.split of the emitted command should recover the raw path.
    first_line = register_cmd.split("\n")[0]
    parts = shlex.split(first_line)
    assert parts[0] == "cd"
    assert parts[1] == os.path.realpath(weird_root)


def test_emit_register_command_triple_is_shlex_quoted(bare_exp_root, tmp_path):
    """An image ref containing shell-sensitive chars is quoted-and-recovered."""
    # Note: real image refs shouldn't contain shell metacharacters, but the
    # emitter must not depend on that — the register command is user-facing.
    overlay = _write(tmp_path / "foo_overlay.yaml", _valid_overlay_yaml("foo"))
    # Image ref with a colon and semicolon (control test only — not a valid ref;
    # we ensure the emitter never corrupts the string).
    image = "registry.example.com/image:tag;withsemi"
    algo = byo.ResolvedAlgorithm(name="foo", image_ref=image, config_src=overlay)
    cmd = byo.emit_register_command(Path(os.path.realpath(bare_exp_root)), [algo])
    tokens = shlex.split(cmd)
    idx = tokens.index("--algorithm")
    triple = tokens[idx + 1]
    assert triple == f"foo={image}@algorithms/foo/foo_config.yaml"


# ---------------------------------------------------------------------------
# Name validation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_name", [
    "Foo",         # uppercase
    "foo_bar",     # underscore
    "-foo",        # leading hyphen
    "foo-",        # trailing hyphen
    "",            # empty
    "a" * 21,      # too long
    "1",           # single-char is fine actually — check regex end anchor
])
def test_invalid_algorithm_name_rejected(bad_name, bare_exp_root, operator_files):
    # Special-case: "1" is a valid single-char name per the regex; skip it.
    if bad_name == "1":
        pytest.skip("single-digit name is valid")
    argv = [
        "--byo",
        "--baseline", str(operator_files['baseline']),
        "--algorithm", bad_name,
        "--algorithm-image", f"{bad_name}=ghcr.io/example/x:latest",
        "--algorithm-config", f"{bad_name}={operator_files['foo']}",
        "--non-interactive",
    ]
    code, message = byo.run_byo(argv, bare_exp_root, _SKILL_DIR, stdin_isatty=False)
    assert code == 2


def test_reserved_algorithm_name_baseline_rejected(bare_exp_root, operator_files):
    """An algorithm named `baseline` collides with the framework role."""
    argv = [
        "--byo",
        "--baseline", str(operator_files['baseline']),
        "--algorithm", "baseline",
        "--algorithm-image", "baseline=ghcr.io/example/x:latest",
        "--algorithm-config", f"baseline={operator_files['foo']}",
        "--non-interactive",
    ]
    code, message = byo.run_byo(argv, bare_exp_root, _SKILL_DIR, stdin_isatty=False)
    assert code == 2
    assert "reserved" in message.lower()


def test_baseline_identifier_is_hardcoded(bare_exp_root, operator_files):
    """Issue #544 — the baseline identifier in transfer.yaml is always
    the literal string ``baseline``, regardless of the operator's baseline
    source path. The file always lands at ``baselines/baseline.yaml``."""
    argv = _happy_argv(operator_files)
    code, _ = byo.run_byo(argv, bare_exp_root, _SKILL_DIR, stdin_isatty=False)
    assert code == 0
    from pipeline.lib.manifest import load_manifest
    manifest = load_manifest(bare_exp_root / "transfer.yaml")
    assert manifest["baselines"] == [
        {"name": "baseline", "scenario": "baselines/baseline.yaml"}
    ]
    for algo in manifest["algorithms"]:
        assert algo["defaults"] == "baseline"
    assert (bare_exp_root / "baselines" / "baseline.yaml").exists()


def test_reserved_algorithm_name_baselines_rejected(bare_exp_root, operator_files):
    """An algorithm named ``baselines`` collides with the per-baseline
    overlay umbrella dir (``translations/<hash>/generated/baselines/``)."""
    argv = [
        "--byo",
        "--baseline", str(operator_files['baseline']),
        "--algorithm", "baselines",
        "--algorithm-image", "baselines=ghcr.io/example/x:latest",
        "--algorithm-config", f"baselines={operator_files['foo']}",
        "--non-interactive",
    ]
    code, message = byo.run_byo(argv, bare_exp_root, _SKILL_DIR, stdin_isatty=False)
    assert code == 2
    assert "reserved" in message.lower()


# ---------------------------------------------------------------------------
# Scenario name derivation
# ---------------------------------------------------------------------------

def test_scenario_from_readme_first_header(tmp_path):
    root = tmp_path / "somedir"
    root.mkdir()
    (root / "README.md").write_text("# My Fancy Experiment\n\nSome description.\n")
    scenario = byo.derive_scenario_name(root, override=None)
    assert scenario == "my-fancy-experiment"


def test_scenario_from_basename_when_no_readme_header(tmp_path):
    root = tmp_path / "MyExperiment_v2"
    root.mkdir()
    scenario = byo.derive_scenario_name(root, override=None)
    assert scenario == "myexperiment-v2"


def test_scenario_override(tmp_path):
    root = tmp_path / "somedir"
    root.mkdir()
    scenario = byo.derive_scenario_name(root, override="Custom Name!")
    assert scenario == "custom-name"


def test_scenario_normalization_truncates_to_40_chars(tmp_path):
    root = tmp_path / "somedir"
    root.mkdir()
    long_name = "a" * 60
    scenario = byo.derive_scenario_name(root, override=long_name)
    assert len(scenario) == 40


def test_scenario_empty_after_normalization_is_fatal(tmp_path):
    root = tmp_path / "somedir"
    root.mkdir()
    with pytest.raises(byo.BYOError, match="scenario"):
        byo.derive_scenario_name(root, override="!!!@@@###")


# ---------------------------------------------------------------------------
# defaults.disable population
# ---------------------------------------------------------------------------

def test_defaults_disable_matches_template_stems(bare_exp_root, operator_files):
    code, _ = byo.run_byo(
        _happy_argv(operator_files), bare_exp_root, _SKILL_DIR, stdin_isatty=False
    )
    assert code == 0
    manifest = yaml.safe_load((bare_exp_root / "transfer.yaml").read_text())
    expected = sorted(
        p.stem for p in (_SKILL_DIR / "templates" / "defaults").glob("*.yaml")
    )
    assert manifest["defaults"]["disable"] == expected


def test_transfer_yaml_carries_available_fragments_comment(bare_exp_root, operator_files):
    """The emitted transfer.yaml text documents the fragment inventory as a comment
    block immediately after `defaults.disable`. Stems match sorted templates/defaults/*.yaml
    (regex-matched, not hardcoded — future additions don't require a test edit)."""
    code, _ = byo.run_byo(
        _happy_argv(operator_files), bare_exp_root, _SKILL_DIR, stdin_isatty=False
    )
    assert code == 0
    text = (bare_exp_root / "transfer.yaml").read_text()
    expected_stems = sorted(
        p.stem for p in (_SKILL_DIR / "templates" / "defaults").glob("*.yaml")
    )

    # Header comment appears
    assert "# Available fragments (filename stems in baselines/defaults/):" in text

    # One `#   - <stem>` line per available stem
    for stem in expected_stems:
        assert f"#   - {stem}" in text, f"missing stem comment for {stem}"

    # Header appears AFTER `defaults.disable` list and BEFORE the next
    # top-level key (`context:`) — protects against a regression that would
    # place it in the wrong section.
    lines = text.split("\n")
    header_idx = next(
        i for i, ln in enumerate(lines)
        if "Available fragments" in ln
    )
    disable_idx = next(i for i, ln in enumerate(lines) if ln == "  disable:")
    context_idx = next(i for i, ln in enumerate(lines) if ln == "context:")
    assert disable_idx < header_idx < context_idx


def test_inject_available_fragments_comment_no_op_on_empty(tmp_path):
    """Empty available_stems list: helper is a no-op (nothing to document)."""
    yaml_text = "defaults:\n  disable: []\nother: 1\n"
    result = byo._inject_available_fragments_comment(yaml_text, [])
    assert result == yaml_text


def test_inject_available_fragments_comment_sorts_input():
    """Even if the caller passes an unsorted list, comment stems come out sorted."""
    yaml_text = "defaults:\n  disable:\n  - b\n  - a\n  - c\ncontext: {}\n"
    result = byo._inject_available_fragments_comment(yaml_text, ["c", "a", "b"])
    # Expected order: a, b, c
    a_idx = result.index("#   - a")
    b_idx = result.index("#   - b")
    c_idx = result.index("#   - c")
    assert a_idx < b_idx < c_idx


def test_inject_available_fragments_comment_does_not_break_yaml_parse():
    """After injection, yaml.safe_load must still parse the text to the same dict
    (comments are semantically transparent, but this guards against accidental
    corruption of the surrounding list)."""
    original_doc = {
        "kind": "sim2real-transfer",
        "version": 3,
        "defaults": {"disable": ["one", "two"]},
        "context": {"files": []},
    }
    yaml_text = yaml.safe_dump(original_doc, sort_keys=False, default_flow_style=False)
    injected = byo._inject_available_fragments_comment(yaml_text, ["one", "two"])
    reparsed = yaml.safe_load(injected)
    assert reparsed == original_doc


# ---------------------------------------------------------------------------
# Workload enumeration
# ---------------------------------------------------------------------------

def test_workload_enumeration_sorts_and_skips_hidden(tmp_path):
    root = tmp_path / "exp"
    (root / "workloads").mkdir(parents=True)
    _write(root / "workloads" / "b.yaml", "b: 1\n")
    _write(root / "workloads" / "a.yaml", "a: 1\n")
    _write(root / "workloads" / ".hidden.yaml", "h: 1\n")
    _write(root / "workloads" / "not_yaml.txt", "ignored\n")
    _write(root / "workloads" / "ignored.yml", "ignored\n")  # .yml, not .yaml
    workloads = byo.enumerate_workloads(root)
    assert [p.name for p in workloads] == ["a.yaml", "b.yaml"]


def test_workload_symlink_escaping_workloads_rejected(tmp_path):
    root = tmp_path / "exp"
    (root / "workloads").mkdir(parents=True)
    outside = tmp_path / "outside.yaml"
    _write(outside, "w: 1\n")
    (root / "workloads" / "escape.yaml").symlink_to(outside)
    with pytest.raises(byo.BYOError, match="outside"):
        byo.enumerate_workloads(root)


# ---------------------------------------------------------------------------
# BLIS regression — this module must not affect existing BLIS-mode logic
# ---------------------------------------------------------------------------

def test_blis_generate_from_config_still_importable():
    """Sanity check: BLIS-mode module untouched by BYO addition."""
    import generate_from_config as gfc  # noqa: F401
    # Presence of the module and its key symbols is enough — the full BLIS
    # test suite runs adjacent to this one in the same directory.
    assert hasattr(gfc, "extract_fields")
    assert hasattr(gfc, "build_additional_flags")


# ---------------------------------------------------------------------------
# emit_transfer_yaml — direct unit tests
# ---------------------------------------------------------------------------

def test_transfer_yaml_shape_matches_design(tmp_path, operator_files):
    root = tmp_path / "exp"
    (root / "workloads").mkdir(parents=True)
    _write(root / "workloads" / "w1.yaml", "w: 1\n")
    _write(root / "workloads" / "w2.yaml", "w: 2\n")

    algorithms = [
        byo.ResolvedAlgorithm(name="foo", image_ref="i1", config_src=operator_files["foo"]),
        byo.ResolvedAlgorithm(name="bar", image_ref="i2", config_src=operator_files["bar"]),
    ]
    doc = byo.build_transfer_yaml(
        scenario="my-scenario",
        algorithms=algorithms,
        workloads=[root / "workloads" / "w1.yaml", root / "workloads" / "w2.yaml"],
        exp_root=root,
        defaults_stems=["a-default", "b-default"],
    )
    assert doc["kind"] == "sim2real-transfer"
    assert doc["version"] == 3
    assert doc["scenario"] == "my-scenario"
    assert "component" not in doc
    assert doc["baselines"] == [{"name": "baseline", "scenario": "baselines/baseline.yaml"}]
    assert doc["algorithms"] == [
        {"name": "foo", "defaults": "baseline", "byo": True},
        {"name": "bar", "defaults": "baseline", "byo": True},
    ]
    assert doc["workloads"] == ["workloads/w1.yaml", "workloads/w2.yaml"]
    assert doc["defaults"]["disable"] == ["a-default", "b-default"]
