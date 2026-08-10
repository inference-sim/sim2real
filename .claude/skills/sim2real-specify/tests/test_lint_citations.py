import importlib.util
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "lint_citations", SKILL / "scripts" / "lint_citations.py"
    )
    mod = importlib.util.module_from_spec(spec)
    # Register before exec: `from __future__ import annotations` makes dataclass
    # field annotations strings, and @dataclass resolves them via
    # sys.modules[cls.__module__].__dict__. An unregistered module makes that None.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


lint = _load()


def test_parses_simple_citation():
    cites = lint.parse_citations("see sim/edpp_var.go:168 for the charge")
    assert len(cites) == 1
    assert cites[0].path == "sim/edpp_var.go"
    assert cites[0].lines == (168,)
    assert cites[0].source_line == 1


def test_parses_range_and_comma_specs():
    cites = lint.parse_citations(
        "a sim/edpp_var.go:144-157 b\nc utilization/config.go:33,153 d"
    )
    assert cites[0].lines == (144, 157)
    assert cites[0].source_line == 1
    assert cites[1].lines == (33, 153)
    assert cites[1].source_line == 2


def test_parses_bare_basename():
    cites = lint.parse_citations("llm-d-router passes it through (extractor.go:127)")
    assert cites[0].path == "extractor.go"
    assert cites[0].lines == (127,)


def test_ignores_non_citation_noise():
    text = "priced 1.6x more; pinned v0.9.0 at 871b169b; ratio 0.554:0.906"
    assert lint.parse_citations(text) == []


def test_ignores_lint_skip_lines():
    text = "an example like foo/bar.go:12 is illustrative  # lint-skip"
    assert lint.parse_citations(text) == []


def test_citation_at_end_of_sentence_is_not_dropped():
    """A trailing period must not defeat the line-spec match."""
    cites = lint.parse_citations("forced at sim/edpp.go:1707.")
    assert cites[0].path == "sim/edpp.go"
    assert cites[0].lines == (1707,)


@pytest.fixture
def tree(tmp_path):
    """A fake pinned checkout with two files, one of them shadowed by name."""
    root = tmp_path / "checkout"
    (root / "sim").mkdir(parents=True)
    (root / "sim" / "edpp_var.go").write_text(
        "\n".join(f"line{i}" for i in range(1, 201))
    )
    (root / "pkg" / "a").mkdir(parents=True)
    (root / "pkg" / "a" / "dup.go").write_text("x\ny\n")
    (root / "pkg" / "b").mkdir(parents=True)
    (root / "pkg" / "b" / "dup.go").write_text("x\ny\n")
    (root / ".git").mkdir()
    (root / ".git" / "edpp_var.go").write_text("noise\n")
    return root


@pytest.fixture
def bundle(tmp_path):
    root = tmp_path / "bundle"
    (root / "algorithms").mkdir(parents=True)
    return root


def test_resolves_valid_citation(bundle, tree):
    (bundle / "algorithms" / "a.go").write_text("// ported from sim/edpp_var.go:168\n")
    assert lint.lint_bundle(bundle, [tree], (".go", ".md")) == []


def test_flags_unresolved_path(bundle, tree):
    (bundle / "README.md").write_text("see sim/nope.go:5\n")
    fails = lint.lint_bundle(bundle, [tree], (".go", ".md"))
    assert [f.kind for f in fails] == ["unresolved-path"]


def test_flags_line_out_of_range(bundle, tree):
    (bundle / "README.md").write_text("see sim/edpp_var.go:5000\n")
    fails = lint.lint_bundle(bundle, [tree], (".go", ".md"))
    assert [f.kind for f in fails] == ["line-out-of-range"]
    assert "200" in fails[0].detail


def test_flags_ambiguous_path(bundle, tree):
    (bundle / "README.md").write_text("see dup.go:1\n")
    fails = lint.lint_bundle(bundle, [tree], (".go", ".md"))
    assert [f.kind for f in fails] == ["ambiguous-path"]


def test_ignores_dot_directories(bundle, tree):
    """.git/edpp_var.go must not create ambiguity with sim/edpp_var.go."""
    (bundle / "README.md").write_text("see edpp_var.go:1\n")
    assert lint.lint_bundle(bundle, [tree], (".go", ".md")) == []


def test_bundle_is_its_own_tree(bundle, tree):
    """A self-reference like README.md:2 resolves against the bundle itself."""
    (bundle / "README.md").write_text("first\nsee README.md:2\n")
    assert lint.lint_bundle(bundle, [tree], (".go", ".md")) == []


def test_failure_records_the_bundle_file_it_came_from(bundle, tree):
    (bundle / "algorithms" / "a.go").write_text("bad sim/nope.go:5\n")
    fails = lint.lint_bundle(bundle, [tree], (".go", ".md"))
    assert fails[0].file == "algorithms/a.go"
    assert fails[0].source_line == 1


def test_main_exit_codes(bundle, tree, capsys):
    (bundle / "README.md").write_text("see sim/nope.go:5\n")
    rc = lint.main(["--bundle", str(bundle), "--tree", str(tree)])
    assert rc == 1
    assert "unresolved-path" in capsys.readouterr().out
    (bundle / "README.md").write_text("see sim/edpp_var.go:168\n")
    assert lint.main(["--bundle", str(bundle), "--tree", str(tree)]) == 0


def test_main_usage_error_on_bad_bundle(tmp_path):
    assert lint.main(["--bundle", str(tmp_path / "nonexistent")]) == 2
