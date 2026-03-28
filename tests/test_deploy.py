import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import importlib

deploy = importlib.import_module("deploy")


# ── _inject_image_reference ─────────────────────────────────────────

def test_inject_image_sets_hub_name_and_tag():
    alg = {"stack": {"gaie": {"treatment": {"helmValues": {}}}}}
    result = deploy._inject_image_reference(alg, "quay.io/me", "my-repo", "run-2026-03-28")
    hv = result["stack"]["gaie"]["treatment"]["helmValues"]
    ie = hv["inferenceExtension"]["image"]
    assert ie["hub"] == "quay.io/me"
    assert ie["name"] == "my-repo"
    assert ie["tag"] == "run-2026-03-28"


def test_inject_image_replaces_existing_image():
    # Must overwrite a pre-existing (possibly wrong) image dict — not update it
    alg = {"stack": {"gaie": {"treatment": {"helmValues": {
        "inferenceExtension": {"image": {"hub": "old.io/org/repo", "tag": "old-tag"}}
    }}}}}
    result = deploy._inject_image_reference(alg, "quay.io/me", "new-repo", "new-tag")
    ie = result["stack"]["gaie"]["treatment"]["helmValues"]["inferenceExtension"]["image"]
    assert ie == {"hub": "quay.io/me", "name": "new-repo", "tag": "new-tag"}


def test_inject_image_preserves_other_keys():
    alg = {"stack": {"gaie": {"treatment": {"helmValues": {"foo": "bar"}}}}}
    result = deploy._inject_image_reference(alg, "hub", "repo", "tag")
    assert result["stack"]["gaie"]["treatment"]["helmValues"]["foo"] == "bar"


def test_inject_image_creates_missing_nesting():
    alg = {}
    result = deploy._inject_image_reference(alg, "hub", "repo", "tag")
    ie = result["stack"]["gaie"]["treatment"]["helmValues"]["inferenceExtension"]["image"]
    assert ie["hub"] == "hub"
    assert ie["name"] == "repo"
    assert ie["tag"] == "tag"


# ── _construct_validation_results ───────────────────────────────────

def _make_equiv(suite_a_passed=True, suite_c_passed=True):
    return {
        "suite_a": {"passed": suite_a_passed, "kendall_tau": 0.9},
        "suite_b": {"passed": True},
        "suite_c": {"passed": suite_c_passed},
    }


def test_construct_fast_mode_pass():
    val = deploy._construct_validation_results(_make_equiv(), fast_iter=True)
    assert val["overall_verdict"] == "PASS"
    assert val["suite_a"]["kendall_tau"] == 0.9


def test_construct_fast_mode_fail_suite_a():
    val = deploy._construct_validation_results(_make_equiv(suite_a_passed=False), fast_iter=True)
    assert val["overall_verdict"] == "FAIL"


def test_construct_fast_mode_fail_suite_c():
    val = deploy._construct_validation_results(_make_equiv(suite_c_passed=False), fast_iter=True)
    assert val["overall_verdict"] == "FAIL"


def test_construct_full_mode_no_overall_verdict():
    val = deploy._construct_validation_results(_make_equiv(), fast_iter=False)
    assert "overall_verdict" not in val


def test_construct_copies_all_suites():
    val = deploy._construct_validation_results(_make_equiv(), fast_iter=False)
    assert "suite_a" in val
    assert "suite_b" in val
    assert "suite_c" in val


# ── _merge_benchmark_into_validation ────────────────────────────────

def _make_bench(verdict="PASS", noise_cv=0.05):
    return {
        "mechanism_check_verdict": verdict,
        "noise_cv": noise_cv,
        "workload_classification": [],
    }


def _make_val(suite_a_passed=True, suite_c_passed=True):
    return {
        "suite_a": {"passed": suite_a_passed},
        "suite_b": {"passed": True},
        "suite_c": {"passed": suite_c_passed},
    }


def test_merge_benchmark_pass():
    val = deploy._merge_benchmark_into_validation(_make_val(), _make_bench("PASS"))
    assert val["overall_verdict"] == "PASS"
    assert val["noise_cv"] == 0.05
    assert "noise_cv" not in val["benchmark"]
    assert val["benchmark"]["mechanism_check_verdict"] == "PASS"


def test_merge_benchmark_inconclusive():
    val = deploy._merge_benchmark_into_validation(_make_val(), _make_bench("INCONCLUSIVE"))
    assert val["overall_verdict"] == "INCONCLUSIVE"


def test_merge_benchmark_fail_verdict():
    val = deploy._merge_benchmark_into_validation(_make_val(), _make_bench("FAIL"))
    assert val["overall_verdict"] == "FAIL"


def test_merge_benchmark_fail_suite_a():
    val = deploy._merge_benchmark_into_validation(
        _make_val(suite_a_passed=False), _make_bench("PASS")
    )
    assert val["overall_verdict"] == "FAIL"


def test_merge_benchmark_fail_suite_c():
    val = deploy._merge_benchmark_into_validation(
        _make_val(suite_c_passed=False), _make_bench("PASS")
    )
    assert val["overall_verdict"] == "FAIL"


def test_merge_noise_cv_at_top_level():
    val = deploy._merge_benchmark_into_validation(_make_val(), _make_bench(noise_cv=0.12))
    assert val["noise_cv"] == 0.12
    assert "noise_cv" not in val["benchmark"]
