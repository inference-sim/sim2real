"""Tests for pipeline.lib.redact."""
from pathlib import Path

import yaml

from pipeline.lib.redact import REDACTED, redact_yaml_file


def test_redacts_standalone_secret(tmp_path: Path):
    src = (
        "apiVersion: v1\n"
        "kind: Secret\n"
        "metadata:\n"
        "  name: hf-token\n"
        "  namespace: jchen4\n"
        "type: Opaque\n"
        "data:\n"
        "  token: aGZfYWJjMTIzZGVmNDU2\n"
        "  other: dG9wc2VjcmV0\n"
    )
    p = tmp_path / "secret.yaml"
    p.write_text(src)
    assert redact_yaml_file(p) == 1
    out = p.read_text()
    assert out.startswith("# REDACTED by sim2real collect: 1 Secret stubbed\n")
    d = list(yaml.safe_load_all(out))[0]
    assert d["kind"] == "Secret"
    assert d["metadata"]["name"] == "hf-token"
    assert d["metadata"]["namespace"] == "jchen4"
    assert d["type"] == "Opaque"
    assert d["data"]["token"] == REDACTED
    assert d["data"]["other"] == REDACTED


def test_multi_doc_only_stubs_matching_kinds(tmp_path: Path):
    src = (
        "apiVersion: v1\n"
        "kind: ConfigMap\n"
        "metadata:\n"
        "  name: cm\n"
        "data:\n"
        "  foo: bar\n"
        "---\n"
        "apiVersion: v1\n"
        "kind: Secret\n"
        "metadata:\n"
        "  name: s\n"
        "type: Opaque\n"
        "data:\n"
        "  token: aGY=\n"
    )
    p = tmp_path / "mix.yaml"
    p.write_text(src)
    assert redact_yaml_file(p) == 1
    docs = list(yaml.safe_load_all(p.read_text()))
    cm = next(d for d in docs if d["kind"] == "ConfigMap")
    sec = next(d for d in docs if d["kind"] == "Secret")
    assert cm["data"]["foo"] == "bar"
    assert sec["data"]["token"] == REDACTED


def test_secret_with_string_data_only(tmp_path: Path):
    src = (
        "apiVersion: v1\n"
        "kind: Secret\n"
        "metadata:\n"
        "  name: hf\n"
        "type: Opaque\n"
        "stringData:\n"
        "  token: hf_xyz\n"
    )
    p = tmp_path / "s.yaml"
    p.write_text(src)
    assert redact_yaml_file(p) == 1
    d = list(yaml.safe_load_all(p.read_text()))[0]
    assert d["stringData"]["token"] == REDACTED


def test_secret_with_no_data_fields_unchanged(tmp_path: Path):
    src = (
        "apiVersion: v1\n"
        "kind: Secret\n"
        "metadata:\n"
        "  name: empty\n"
        "type: Opaque\n"
    )
    p = tmp_path / "empty.yaml"
    p.write_text(src)
    assert redact_yaml_file(p) == 0
    assert p.read_text() == src


def test_file_without_kind_is_passthrough(tmp_path: Path):
    src = "domain: example.com\nreplicas: 3\nflags:\n  enabled: true\n"
    p = tmp_path / "values.yaml"
    p.write_text(src)
    assert redact_yaml_file(p) == 0
    assert p.read_text() == src


def test_unparseable_file_is_untouched(tmp_path: Path):
    src = "{ this is not valid yaml"
    p = tmp_path / "bogus.yaml"
    p.write_text(src)
    assert redact_yaml_file(p) == 0
    assert p.read_text() == src


def test_metadata_and_data_key_names_preserved(tmp_path: Path):
    src = (
        "apiVersion: v1\n"
        "kind: Secret\n"
        "metadata:\n"
        "  name: hf\n"
        "  namespace: jchen4\n"
        "  labels:\n"
        "    app: scorer\n"
        "  annotations:\n"
        "    note: keep\n"
        "type: Opaque\n"
        "data:\n"
        "  token: aGY=\n"
        "  ca.crt: bXk=\n"
    )
    p = tmp_path / "s.yaml"
    p.write_text(src)
    assert redact_yaml_file(p) == 1
    d = list(yaml.safe_load_all(p.read_text()))[0]
    assert d["metadata"]["labels"] == {"app": "scorer"}
    assert d["metadata"]["annotations"] == {"note": "keep"}
    assert set(d["data"].keys()) == {"token", "ca.crt"}


def test_idempotent_on_already_redacted(tmp_path: Path):
    src = (
        "apiVersion: v1\n"
        "kind: Secret\n"
        "metadata:\n"
        "  name: hf\n"
        "type: Opaque\n"
        "data:\n"
        "  token: aGY=\n"
    )
    p = tmp_path / "s.yaml"
    p.write_text(src)
    assert redact_yaml_file(p) == 1
    after_first = p.read_text()
    assert redact_yaml_file(p) == 0
    assert p.read_text() == after_first


def test_custom_kinds_set(tmp_path: Path):
    src = (
        "apiVersion: v1\n"
        "kind: ConfigMap\n"
        "metadata:\n"
        "  name: cm\n"
        "data:\n"
        "  hf-token: hf_abc\n"
    )
    p = tmp_path / "cm.yaml"
    p.write_text(src)
    assert redact_yaml_file(p, kinds={"ConfigMap"}) == 1
    d = list(yaml.safe_load_all(p.read_text()))[0]
    assert d["data"]["hf-token"] == REDACTED


def test_multi_kind_header_combines_counts(tmp_path: Path):
    src = (
        "apiVersion: v1\n"
        "kind: Secret\n"
        "metadata: {name: a}\n"
        "type: Opaque\n"
        "data: {x: y}\n"
        "---\n"
        "apiVersion: v1\n"
        "kind: Secret\n"
        "metadata: {name: b}\n"
        "type: Opaque\n"
        "data: {x: y}\n"
        "---\n"
        "apiVersion: v1\n"
        "kind: ConfigMap\n"
        "metadata: {name: c}\n"
        "data: {x: y}\n"
    )
    p = tmp_path / "multi.yaml"
    p.write_text(src)
    assert redact_yaml_file(p, kinds={"Secret", "ConfigMap"}) == 3
    text = p.read_text()
    assert text.startswith(
        "# REDACTED by sim2real collect: 1 ConfigMap stubbed, 2 Secrets stubbed\n"
    )


def test_default_kind_set_is_secret_only(tmp_path: Path):
    """ConfigMap is NOT in the default redact set; default-call leaves it alone."""
    src = (
        "apiVersion: v1\n"
        "kind: ConfigMap\n"
        "metadata: {name: cm}\n"
        "data: {token: hf_abc}\n"
    )
    p = tmp_path / "cm.yaml"
    p.write_text(src)
    assert redact_yaml_file(p) == 0
    assert p.read_text() == src


def test_tree_walks_yaml_and_yml_recursively(tmp_path: Path):
    """redact_yaml_tree visits *.yaml and *.yml at any depth, skips others."""
    from pipeline.lib.redact import redact_yaml_tree

    secret = (
        "apiVersion: v1\n"
        "kind: Secret\n"
        "metadata: {name: s}\n"
        "type: Opaque\n"
        "data: {token: aGY=}\n"
    )
    plain = "key: value\n"

    nested = tmp_path / "deep" / "nested"
    nested.mkdir(parents=True)
    (tmp_path / "top.yaml").write_text(secret)
    (tmp_path / "deep" / "mid.yml").write_text(secret)
    (nested / "leaf.yaml").write_text(plain)
    (tmp_path / "ignore.txt").write_text(secret)
    (tmp_path / "config.json").write_text("{}")

    assert redact_yaml_tree(tmp_path) == 2
    assert "REDACTED" in (tmp_path / "top.yaml").read_text()
    assert "REDACTED" in (tmp_path / "deep" / "mid.yml").read_text()
    assert (nested / "leaf.yaml").read_text() == plain
    assert (tmp_path / "ignore.txt").read_text() == secret  # untouched
    assert (tmp_path / "config.json").read_text() == "{}"


def test_tree_on_empty_or_missing_dir(tmp_path: Path):
    """Empty / nonexistent root returns 0 with no errors."""
    from pipeline.lib.redact import redact_yaml_tree

    empty = tmp_path / "empty"
    empty.mkdir()
    assert redact_yaml_tree(empty) == 0

    missing = tmp_path / "does-not-exist"
    assert redact_yaml_tree(missing) == 0
