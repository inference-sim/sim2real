import importlib.util
import sys
from pathlib import Path

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
