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
    """ConfigMap is NOT in the default redact set; a ConfigMap carrying only
    non-sensitive keys is not wholesale-stubbed the way a Secret would be."""
    src = (
        "apiVersion: v1\n"
        "kind: ConfigMap\n"
        "metadata: {name: cm}\n"
        "data: {greeting: hello}\n"
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


# ── Sensitive-key scrub in non-Secret documents (issue #819) ─────────


def test_redacts_sensitive_keys_in_plain_config(tmp_path: Path):
    """A non-Secret harness config with an inlined HF token is scrubbed.

    This is the issue #819 case: an llm-d-benchmark plan config.yaml whose
    top-level document has no `kind:` and carries the credential as an
    ordinary field (`huggingface.token` / `huggingface.tokenBase64`).
    """
    # Fixture values are deliberately fake and do not match any real
    # credential pattern — the test only asserts they become REDACTED.
    src = (
        "huggingface:\n"
        "  enabled: true\n"
        "  secretName: hf-secret\n"
        "  token: fake-example-token-not-real\n"
        "  tokenBase64: ZmFrZS1leGFtcGxlLXRva2Vu\n"
        "  tokenKey: HF_TOKEN\n"
        "contextSecretName: llmdbench-context\n"
    )
    p = tmp_path / "config.yaml"
    p.write_text(src)
    assert redact_yaml_file(p) == 1
    out = p.read_text()
    assert out.startswith("# REDACTED by sim2real collect: 1 document stubbed\n")
    d = list(yaml.safe_load_all(out))[0]
    hf = d["huggingface"]
    # Credentials scrubbed …
    assert hf["token"] == REDACTED
    assert hf["tokenBase64"] == REDACTED
    # … reference / name fields preserved.
    assert hf["enabled"] is True
    assert hf["secretName"] == "hf-secret"
    assert hf["tokenKey"] == "HF_TOKEN"
    assert d["contextSecretName"] == "llmdbench-context"


def test_sensitive_key_scrub_recurses_into_lists(tmp_path: Path):
    """Sensitive keys nested inside list items (e.g. extraObjects) are found."""
    src = (
        "extraObjects:\n"
        "- apiVersion: v1\n"
        "  kind: Custom\n"
        "  spec:\n"
        "    password: hunter2\n"
        "- name: keep-me\n"
    )
    p = tmp_path / "plan.yaml"
    p.write_text(src)
    assert redact_yaml_file(p) == 1
    d = list(yaml.safe_load_all(p.read_text()))[0]
    assert d["extraObjects"][0]["spec"]["password"] == REDACTED
    assert d["extraObjects"][1]["name"] == "keep-me"


def test_sensitive_key_scrub_case_insensitive_and_variants(tmp_path: Path):
    """Every case/separator spelling of a sensitive key is caught.

    Covers camelCase, snake_case, SCREAMING_CASE and no-separator forms —
    including the AWS-style access-key names and the base64/bearer keys
    whose snake_case spellings would bypass an exact-lowercase match.
    """
    src = (
        "auth:\n"
        "  Token: a\n"
        "  API_KEY: b\n"
        "  apiKey: c\n"
        "  bearerToken: d\n"
        "  bearer_token: e\n"
        "  authorization: Bearer xyz\n"
        "  accessKey: f\n"
        "  access_key: g\n"
        "  secretAccessKey: h\n"
        "  secret_access_key: i\n"
        "  tokenBase64: j\n"
        "  token_base64: k\n"
    )
    p = tmp_path / "auth.yaml"
    p.write_text(src)
    assert redact_yaml_file(p) == 1
    auth = list(yaml.safe_load_all(p.read_text()))[0]["auth"]
    assert all(v == REDACTED for v in auth.values())


def test_sensitive_key_in_non_secret_kind_is_redacted(tmp_path: Path):
    """A `token` key in a ConfigMap (not in the kind denylist) is scrubbed."""
    src = (
        "apiVersion: v1\n"
        "kind: ConfigMap\n"
        "metadata: {name: cm}\n"
        "data: {token: hf_abc, greeting: hello}\n"
    )
    p = tmp_path / "cm.yaml"
    p.write_text(src)
    assert redact_yaml_file(p) == 1
    d = list(yaml.safe_load_all(p.read_text()))[0]
    assert d["data"]["token"] == REDACTED
    assert d["data"]["greeting"] == "hello"


def test_sensitive_key_scrub_idempotent(tmp_path: Path):
    """Re-running over an already-scrubbed plain config is a no-op."""
    src = (
        "huggingface:\n"
        "  token: hf_secret\n"
        "  tokenBase64: YWJj\n"
    )
    p = tmp_path / "config.yaml"
    p.write_text(src)
    assert redact_yaml_file(p) == 1
    after_first = p.read_text()
    assert redact_yaml_file(p) == 0
    assert p.read_text() == after_first


def test_reference_keys_never_redacted(tmp_path: Path):
    """Keys that name/reference secrets are not themselves secrets."""
    src = (
        "secretName: hf-secret\n"
        "contextSecretName: llmdbench-context\n"
        "tokenKey: HF_TOKEN\n"
    )
    p = tmp_path / "refs.yaml"
    p.write_text(src)
    assert redact_yaml_file(p) == 0
    assert p.read_text() == src


# ── Additional edge-case coverage (quality agent) ────────────────────


def test_unreadable_file_returns_zero(tmp_path: Path, monkeypatch):
    """When the file cannot be read (OSError), return 0 and leave untouched."""
    from pipeline.lib.redact import redact_yaml_file

    p = tmp_path / "secret.yaml"
    p.write_text("apiVersion: v1\nkind: Secret\ndata:\n  key: dmFsdWU=\n")

    # Make read_text raise OSError
    def raise_oserror(self):
        raise OSError("Permission denied")

    monkeypatch.setattr(Path, "read_text", raise_oserror)
    result = redact_yaml_file(p)
    assert result == 0


def test_write_failure_returns_zero_and_cleans_tmp(tmp_path: Path, monkeypatch):
    """When writing the redacted output fails, return 0 and clean up tmp file."""
    from pipeline.lib.redact import redact_yaml_file

    p = tmp_path / "secret.yaml"
    p.write_text("apiVersion: v1\nkind: Secret\ndata:\n  key: dmFsdWU=\n")

    original_content = p.read_text()
    write_calls = []

    original_write_text = Path.write_text

    def failing_write(self, content, *a, **kw):
        if ".redact.tmp" in str(self):
            write_calls.append(str(self))
            raise OSError("disk full")
        return original_write_text(self, content, *a, **kw)

    monkeypatch.setattr(Path, "write_text", failing_write)
    result = redact_yaml_file(p)
    assert result == 0
    # Original file should be untouched (write failed before replace)
    assert p.read_text() == original_content


def test_yaml_with_tab_characters_is_unparseable(tmp_path: Path):
    """YAML with invalid characters triggers YAMLError path."""
    from pipeline.lib.redact import redact_yaml_file

    p = tmp_path / "bad.yaml"
    # Multi-doc with invalid YAML in first doc
    p.write_text("---\n%invalid_directive\n---\napiVersion: v1\nkind: Secret\n")
    result = redact_yaml_file(p)
    assert result == 0
    # File should be untouched
    assert "%invalid_directive" in p.read_text()


# ── annotation prune (issue #894) ────────────────────────────────────────────
# `kubectl.kubernetes.io/last-applied-configuration` is a verbatim JSON copy of
# the applied manifest. It duplicates the spec in the same document, dominates
# the high-entropy findings in collected manifests, and — the reason it is
# pruned rather than tolerated — is a redaction blind spot: the copy is a JSON
# *string*, so a credential inside it is not a YAML field `_stub_sensitive_keys`
# can reach.

LAC = "kubectl.kubernetes.io/last-applied-configuration"


def test_prunes_last_applied_configuration_leaving_other_annotations(tmp_path: Path):
    """The multi-annotation case: the pruned key goes, its siblings stay."""
    src = (
        "apiVersion: v1\n"
        "kind: Pod\n"
        "metadata:\n"
        "  name: vllm-decode-0\n"
        "  annotations:\n"
        f'    {LAC}: \'{{"apiVersion":"v1","kind":"Pod"}}\'\n'
        "    prometheus.io/scrape: 'true'\n"
        "spec:\n"
        "  containers: []\n"
    )
    p = tmp_path / "pod.yaml"
    p.write_text(src)

    assert redact_yaml_file(p) == 1

    out = p.read_text()
    assert LAC not in out
    doc = yaml.safe_load(out)
    assert doc["metadata"]["annotations"] == {"prometheus.io/scrape": "true"}
    assert doc["metadata"]["name"] == "vllm-decode-0"
    assert doc["spec"] == {"containers": []}


def test_sole_annotation_removes_the_annotations_mapping(tmp_path: Path):
    """The sole-annotation case: `annotations` must not be left as `{}`.

    On the run that motivated #894 this was 12 of 156 affected files — the
    llm-d-benchmark harness Service in each cell."""
    src = (
        "apiVersion: v1\n"
        "kind: Service\n"
        "metadata:\n"
        "  name: service-llm-d-benchmark-harness\n"
        "  annotations:\n"
        f'    {LAC}: \'{{"apiVersion":"v1","kind":"Service"}}\'\n'
        "spec:\n"
        "  ports: []\n"
    )
    p = tmp_path / "svc.yaml"
    p.write_text(src)

    assert redact_yaml_file(p) == 1

    out = p.read_text()
    assert LAC not in out
    assert "annotations" not in out
    doc = yaml.safe_load(out)
    assert "annotations" not in doc["metadata"]
    assert doc["metadata"]["name"] == "service-llm-d-benchmark-harness"


def test_prune_is_counted_in_the_summary_header(tmp_path: Path):
    """The prune deletes data rather than masking it, so it is reported."""
    src = (
        "apiVersion: v1\n"
        "kind: Pod\n"
        "metadata:\n"
        "  name: p\n"
        "  annotations:\n"
        f"    {LAC}: '{{}}'\n"
        "spec: {}\n"
    )
    p = tmp_path / "pod.yaml"
    p.write_text(src)
    redact_yaml_file(p)
    assert p.read_text().startswith(
        "# REDACTED by sim2real collect: 1 annotation pruned\n")


def test_header_reports_stubs_and_prunes_together(tmp_path: Path):
    """A file that needs both shows both, and the stub count does not absorb
    the prune."""
    src = (
        "apiVersion: v1\n"
        "kind: Secret\n"
        "metadata:\n"
        "  name: hf\n"
        "  annotations:\n"
        f"    {LAC}: '{{}}'\n"
        "    keep: me\n"
        "data:\n"
        "  token: abc\n"
    )
    p = tmp_path / "secret.yaml"
    p.write_text(src)
    redact_yaml_file(p)
    out = p.read_text()
    assert out.startswith(
        "# REDACTED by sim2real collect: 1 Secret stubbed, 1 annotation pruned\n")
    assert yaml.safe_load(out)["data"]["token"] == REDACTED


def test_prune_only_document_is_not_reported_as_stubbed(tmp_path: Path):
    """A document changed only by the prune must not be counted as stubbed —
    nothing in it was masked, and the header would be claiming otherwise."""
    src = (
        "apiVersion: v1\n"
        "kind: Pod\n"
        "metadata:\n"
        "  name: p\n"
        "  annotations:\n"
        f"    {LAC}: '{{}}'\n"
        "spec: {}\n"
    )
    p = tmp_path / "pod.yaml"
    p.write_text(src)
    redact_yaml_file(p)
    assert "stubbed" not in p.read_text().splitlines()[0]


def test_prune_plural_agreement(tmp_path: Path):
    """Two pruned keys in one file read as 'annotations', not 'annotation'."""
    src = (
        "apiVersion: v1\n"
        "kind: Pod\n"
        "metadata:\n"
        "  name: a\n"
        "  annotations:\n"
        f"    {LAC}: '{{}}'\n"
        "---\n"
        "apiVersion: v1\n"
        "kind: Pod\n"
        "metadata:\n"
        "  name: b\n"
        "  annotations:\n"
        f"    {LAC}: '{{}}'\n"
    )
    p = tmp_path / "pods.yaml"
    p.write_text(src)
    assert redact_yaml_file(p) == 2
    assert p.read_text().startswith(
        "# REDACTED by sim2real collect: 2 annotations pruned\n")


def test_prunes_nested_metadata_annotations(tmp_path: Path):
    """A Deployment can carry the annotation on its pod template too, so the
    walk is recursive rather than only checking top-level metadata."""
    src = (
        "apiVersion: apps/v1\n"
        "kind: Deployment\n"
        "metadata:\n"
        "  name: d\n"
        "  annotations:\n"
        f"    {LAC}: '{{}}'\n"
        "spec:\n"
        "  template:\n"
        "    metadata:\n"
        "      annotations:\n"
        f"        {LAC}: '{{}}'\n"
        "        sidecar.istio.io/inject: 'false'\n"
    )
    p = tmp_path / "deploy.yaml"
    p.write_text(src)
    redact_yaml_file(p)
    out = p.read_text()
    assert LAC not in out
    doc = yaml.safe_load(out)
    assert "annotations" not in doc["metadata"]
    assert doc["spec"]["template"]["metadata"]["annotations"] == {
        "sidecar.istio.io/inject": "false"}
    assert out.startswith(
        "# REDACTED by sim2real collect: 2 annotations pruned\n")


def test_file_without_the_annotation_is_not_rewritten(tmp_path: Path):
    """No prune and no stub means no rewrite — an untouched file keeps its
    bytes, including the absence of a header."""
    src = (
        "apiVersion: v1\n"
        "kind: Pod\n"
        "metadata:\n"
        "  name: p\n"
        "  annotations:\n"
        "    prometheus.io/scrape: 'true'\n"
    )
    p = tmp_path / "pod.yaml"
    p.write_text(src)
    assert redact_yaml_file(p) == 0
    assert p.read_text() == src


def test_already_empty_annotations_mapping_is_left_alone(tmp_path: Path):
    """The prune reports what it removed. An `annotations: {}` it did not empty
    is not its doing, so removing it would make the count a lie — and would
    rewrite a file that needed nothing."""
    src = (
        "apiVersion: v1\n"
        "kind: Pod\n"
        "metadata:\n"
        "  name: p\n"
        "  annotations: {}\n"
    )
    p = tmp_path / "pod.yaml"
    p.write_text(src)
    assert redact_yaml_file(p) == 0
    assert p.read_text() == src


def test_credential_inside_the_pruned_annotation_does_not_survive(tmp_path: Path):
    """The blind spot that motivated the prune.

    `_stub_sensitive_keys` descends dicts and lists; the annotation's value is
    a JSON *string*, so a token inside it is unreachable. Before the prune the
    live field was stubbed while its copy in the annotation stayed on disk."""
    src = (
        "apiVersion: v1\n"
        "kind: Pod\n"
        "metadata:\n"
        "  name: p\n"
        "  annotations:\n"
        f'    {LAC}: \'{{"spec":{{"token":"hf_LIVESECRET123"}}}}\'\n'
        "spec:\n"
        "  token: hf_LIVESECRET123\n"
    )
    p = tmp_path / "pod.yaml"
    p.write_text(src)
    redact_yaml_file(p)
    out = p.read_text()
    assert "hf_LIVESECRET123" not in out
    assert yaml.safe_load(out)["spec"]["token"] == REDACTED


def test_prune_reaches_files_via_the_tree_walker(tmp_path: Path):
    """redact_yaml_tree is what deploy.py calls on each iteration's resources/;
    the prune must ride along rather than only working on direct calls."""
    from pipeline.lib.redact import redact_yaml_tree

    root = tmp_path / "resources"
    (root / "nested").mkdir(parents=True)
    body = (
        "apiVersion: v1\n"
        "kind: Pod\n"
        "metadata:\n"
        "  name: p\n"
        "  annotations:\n"
        f"    {LAC}: '{{}}'\n"
    )
    (root / "pod-a.yaml").write_text(body)
    (root / "nested" / "pod-b.yml").write_text(body)

    assert redact_yaml_tree(root) == 2
    assert LAC not in (root / "pod-a.yaml").read_text()
    assert LAC not in (root / "nested" / "pod-b.yml").read_text()
