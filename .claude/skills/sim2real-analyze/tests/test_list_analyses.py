"""Tests for list_analyses.py — catalog discovery + front-matter parsing."""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import list_analyses


REAL_ANALYSES_DIR = Path(__file__).resolve().parent.parent / "analyses"
SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "list_analyses.py"


# ── Helpers ───────────────────────────────────────────────────────────────────

def write_entry(dir_path: Path, name: str, front_matter: str, body: str = "") -> Path:
    """Write a .md file with `front_matter` (YAML content between --- delimiters) and body."""
    dir_path.mkdir(parents=True, exist_ok=True)
    path = dir_path / f"{name}.md"
    content = f"---\n{front_matter}---\n{body}"
    path.write_text(content)
    return path


def write_script(dir_path: Path, script_name: str) -> Path:
    """Create an empty script stub so `runner: script` validation passes."""
    script_path = dir_path / script_name
    script_path.write_text("# stub\n")
    return script_path


# ── Real-catalog smoke test ──────────────────────────────────────────────────

def test_real_catalog_contains_expected_entries():
    """The shipped catalog resolves without warnings and contains every expected entry.

    Uses subset comparison so the assertion tolerates future additions without
    another test-file edit — each new entry only has to appear here and in the
    catalog itself. If an entry is REMOVED, the intersection fails loudly.
    """
    entries = list_analyses.load_catalog(REAL_ANALYSES_DIR)
    names = {e["name"] for e in entries}
    expected = {
        "latency-table",
        "per-request-scatter",
        "ttft-cdf",
        "tpot-cdf",
        "e2e-cdf",
        "throughput-over-time",
        "error-rate",
    }
    missing = expected - names
    assert not missing, f"catalog missing entries: {sorted(missing)}"


def test_real_catalog_entries_have_all_required_fields():
    entries = list_analyses.load_catalog(REAL_ANALYSES_DIR)
    for e in entries:
        for field in list_analyses.REQUIRED_FIELDS:
            assert e.get(field), f"entry {e.get('name')!r} missing {field!r}"


def test_real_catalog_script_entries_point_at_existing_files():
    """Every runner:script entry in the shipped catalog references an existing .py."""
    entries = list_analyses.load_catalog(REAL_ANALYSES_DIR)
    script_entries = [e for e in entries if e["runner"] == "script"]
    assert script_entries, "expected at least one script-runner entry"
    for e in script_entries:
        assert (REAL_ANALYSES_DIR / e["script"]).exists(), (
            f"entry {e['name']!r} points at missing script {e['script']!r}"
        )


# ── Isolated-catalog tests ────────────────────────────────────────────────────

def test_valid_prompt_entry_is_returned(tmp_path):
    write_entry(tmp_path, "foo", (
        "name: foo\n"
        "title: Foo\n"
        "when-to-use: exemplar\n"
        "inputs: run\n"
        "output: png\n"
        "runner: prompt\n"
    ))
    entries = list_analyses.load_catalog(tmp_path)
    assert len(entries) == 1
    assert entries[0]["name"] == "foo"
    assert entries[0]["runner"] == "prompt"


def test_valid_script_entry_is_returned(tmp_path):
    write_script(tmp_path, "foo.py")
    write_entry(tmp_path, "foo", (
        "name: foo\n"
        "title: Foo\n"
        "when-to-use: exemplar\n"
        "inputs: run\n"
        "output: table\n"
        "runner: script\n"
        "script: foo.py\n"
    ))
    entries = list_analyses.load_catalog(tmp_path)
    assert len(entries) == 1
    assert entries[0]["script"] == "foo.py"


def test_missing_required_field_is_skipped(tmp_path, capsys):
    write_entry(tmp_path, "bad", (
        "name: bad\n"
        "title: Bad\n"
        # missing when-to-use, inputs, output, runner
    ))
    entries = list_analyses.load_catalog(tmp_path)
    assert entries == []
    assert "missing required field" in capsys.readouterr().err


def test_malformed_yaml_is_skipped(tmp_path, capsys):
    # Unbalanced brackets → PyYAML raises
    write_entry(tmp_path, "bad", "name: bad\ntitle: [unclosed\n")
    entries = list_analyses.load_catalog(tmp_path)
    assert entries == []
    assert "malformed YAML" in capsys.readouterr().err


def test_missing_front_matter_is_skipped(tmp_path, capsys):
    """File without the leading `---` delimiter is not a catalog entry."""
    (tmp_path / "plain.md").write_text("just prose, no front-matter\n")
    entries = list_analyses.load_catalog(tmp_path)
    assert entries == []
    assert "malformed YAML front-matter" in capsys.readouterr().err


def test_unterminated_front_matter_is_skipped(tmp_path, capsys):
    """Front-matter with an opening --- but no closing --- is malformed."""
    (tmp_path / "bad.md").write_text("---\nname: bad\ntitle: Bad\n(no closing delim)\n")
    entries = list_analyses.load_catalog(tmp_path)
    assert entries == []
    assert "malformed YAML front-matter" in capsys.readouterr().err


def test_invalid_runner_is_skipped(tmp_path, capsys):
    write_entry(tmp_path, "bad", (
        "name: bad\n"
        "title: Bad\n"
        "when-to-use: exemplar\n"
        "inputs: run\n"
        "output: png\n"
        "runner: banana\n"
    ))
    entries = list_analyses.load_catalog(tmp_path)
    assert entries == []
    assert "invalid runner" in capsys.readouterr().err


def test_invalid_output_is_skipped(tmp_path, capsys):
    write_entry(tmp_path, "bad", (
        "name: bad\n"
        "title: Bad\n"
        "when-to-use: exemplar\n"
        "inputs: run\n"
        "output: gif\n"
        "runner: prompt\n"
    ))
    entries = list_analyses.load_catalog(tmp_path)
    assert entries == []
    assert "invalid output" in capsys.readouterr().err


def test_script_runner_missing_script_field_is_skipped(tmp_path, capsys):
    write_entry(tmp_path, "bad", (
        "name: bad\n"
        "title: Bad\n"
        "when-to-use: exemplar\n"
        "inputs: run\n"
        "output: table\n"
        "runner: script\n"
        # no script: field
    ))
    entries = list_analyses.load_catalog(tmp_path)
    assert entries == []
    assert "'script' field is missing" in capsys.readouterr().err


def test_script_runner_missing_script_file_is_skipped(tmp_path, capsys):
    write_entry(tmp_path, "bad", (
        "name: bad\n"
        "title: Bad\n"
        "when-to-use: exemplar\n"
        "inputs: run\n"
        "output: table\n"
        "runner: script\n"
        "script: nonexistent.py\n"
    ))
    entries = list_analyses.load_catalog(tmp_path)
    assert entries == []
    assert "does not exist" in capsys.readouterr().err


def test_empty_catalog_returns_empty_list(tmp_path):
    entries = list_analyses.load_catalog(tmp_path)
    assert entries == []


def test_entries_are_sorted_by_name(tmp_path):
    for name in ("zebra", "apple", "middle"):
        write_entry(tmp_path, name, (
            f"name: {name}\n"
            f"title: {name.title()}\n"
            "when-to-use: exemplar\n"
            "inputs: run\n"
            "output: png\n"
            "runner: prompt\n"
        ))
    entries = list_analyses.load_catalog(tmp_path)
    assert [e["name"] for e in entries] == ["apple", "middle", "zebra"]


def test_mixed_valid_and_invalid_returns_only_valid(tmp_path, capsys):
    write_entry(tmp_path, "good", (
        "name: good\n"
        "title: Good\n"
        "when-to-use: exemplar\n"
        "inputs: run\n"
        "output: png\n"
        "runner: prompt\n"
    ))
    write_entry(tmp_path, "bad", "name: bad\n")  # missing required fields
    entries = list_analyses.load_catalog(tmp_path)
    assert [e["name"] for e in entries] == ["good"]
    assert "missing required field" in capsys.readouterr().err


def test_unknown_extra_fields_are_preserved(tmp_path):
    """Forward-compat: unknown keys in front-matter are kept, not dropped."""
    write_entry(tmp_path, "foo", (
        "name: foo\n"
        "title: Foo\n"
        "when-to-use: exemplar\n"
        "inputs: run\n"
        "output: png\n"
        "runner: prompt\n"
        "future_field: some_value\n"
    ))
    entries = list_analyses.load_catalog(tmp_path)
    assert entries[0]["future_field"] == "some_value"


# ── CLI-level tests ───────────────────────────────────────────────────────────

def test_cli_emits_json_array():
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH)],
        capture_output=True, text=True, check=True,
    )
    parsed = json.loads(result.stdout)
    assert isinstance(parsed, list)
    names = {e["name"] for e in parsed}
    assert "latency-table" in names


def test_cli_missing_dir_exits_nonzero(tmp_path):
    nonexistent = tmp_path / "nope"
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--analyses-dir", str(nonexistent)],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "not found" in result.stderr


def test_cli_empty_dir_emits_empty_array(tmp_path):
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--analyses-dir", str(tmp_path)],
        capture_output=True, text=True, check=True,
    )
    assert json.loads(result.stdout) == []
