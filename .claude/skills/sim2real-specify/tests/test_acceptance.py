import importlib.util
import json
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
LABELS = SKILL / "tests" / "fixtures" / "ead56ea_labels.json"


def _load():
    spec = importlib.util.spec_from_file_location(
        "acceptance", SKILL / "scripts" / "acceptance.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


acc = _load()


def test_labels_fixture_is_wellformed():
    data = json.loads(LABELS.read_text())
    assert data["fixture"]["commit"] == "08203b4"
    ids = [label["id"] for label in data["labels"]]
    assert len(ids) == len(set(ids))
    audit_findable = [label for label in data["labels"] if "audit" in label["found_by"]]
    assert len(audit_findable) == 4, "ead56ea established four audit-findable defects"


def test_match_by_line_within_tolerance():
    label = {"file": "a.go", "lines": [483], "symbol": "cLocalAfter"}
    assert acc.match({"file": "a.go", "line": 485, "symbol": "other"}, label)
    assert not acc.match({"file": "a.go", "line": 600, "symbol": "other"}, label)


def test_match_by_symbol_when_line_drifts():
    label = {"file": "a.go", "lines": [483], "symbol": "cLocalAfter"}
    assert acc.match({"file": "a.go", "line": 999, "symbol": "cLocalAfter"}, label)


def test_match_requires_same_file():
    label = {"file": "a.go", "lines": [483], "symbol": "cLocalAfter"}
    assert not acc.match({"file": "b.go", "line": 483, "symbol": "cLocalAfter"}, label)


def test_score_reports_recall_and_false_positives():
    labels = acc.load_labels(LABELS)
    audit_labels = [label for label in labels if "audit" in label["found_by"]]
    verdicts = [
        {
            "arm": "causal_slo_externality",
            "findings": [
                {
                    "kind": "WRONG",
                    "file": "algorithms/causal_slo_externality.go",
                    "line": 335,
                    "symbol": "kvTokensFor",
                },
                {
                    "kind": "WRONG",
                    "file": "algorithms/causal_slo_externality.go",
                    "line": 483,
                    "symbol": "cLocalAfter",
                },
            ],
        },
        {
            "arm": "least_ttft_joint",
            "findings": [
                {
                    "kind": "WRONG",
                    "file": "algorithms/least_ttft_joint.go",
                    "line": 12,
                    "symbol": "somethingElse",
                },
            ],
        },
    ]
    result = acc.score(verdicts, audit_labels)
    assert set(result["found"]) == {
        "kv-usage-fraction-not-percent",
        "missing-prefill-attention-charge",
    }
    assert set(result["missed"]) == {
        "admission-steps-not-ceiled",
        "chunk-not-clamped-to-uncached-suffix",
    }
    assert result["recall"] == 0.5
    assert result["false_positive_arms"] == {"least_ttft_joint": 1}


def test_confirmed_and_bridge_are_not_counted_as_false_positives():
    labels = [
        label for label in acc.load_labels(LABELS) if "audit" in label["found_by"]
    ]
    verdicts = [
        {
            "arm": "kairos_paper",
            "findings": [
                {
                    "kind": "CONFIRMED",
                    "file": "algorithms/kairos_paper.go",
                    "line": 5,
                    "symbol": "x",
                },
                {
                    "kind": "BRIDGE",
                    "file": "algorithms/kairos_paper.go",
                    "line": 9,
                    "symbol": "sPfFor",
                    "declared_at": "algorithms/kairos_paper.go:44",
                },
            ],
        }
    ]
    assert acc.score(verdicts, labels)["false_positive_arms"] == {}


def test_undeclared_bridge_findings_are_reported():
    verdicts = [
        {
            "arm": "causal_slo_externality",
            "findings": [
                {
                    "kind": "BRIDGE",
                    "file": "algorithms/causal_slo_externality.go",
                    "line": 362,
                    "symbol": "sPfFor",
                    "declared_at": None,
                },
                {
                    "kind": "BRIDGE",
                    "file": "algorithms/causal_slo_externality.go",
                    "line": 231,
                    "symbol": "residentTable",
                    "declared_at": "algorithms/causal_slo_externality.go:48",
                },
                {
                    "kind": "WRONG",
                    "file": "algorithms/causal_slo_externality.go",
                    "line": 335,
                    "symbol": "kvTokensFor",
                },
            ],
        }
    ]
    undeclared = acc.undeclared_bridges(verdicts)
    assert [f["symbol"] for f in undeclared] == ["sPfFor"]


def test_missing_declared_at_key_counts_as_undeclared():
    verdicts = [
        {
            "arm": "a",
            "findings": [
                {"kind": "BRIDGE", "file": "a.go", "line": 1, "symbol": "noKey"},
            ],
        }
    ]
    assert [f["symbol"] for f in acc.undeclared_bridges(verdicts)] == ["noKey"]
