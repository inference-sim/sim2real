"""Tests for pipeline/lib/translation_ref.py — validation + shim + resolver."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from pipeline.lib import translation_ref
from pipeline.lib.translation_ref import (
    ResolveError,
    ValidationError,
    resolve_translation_ref,
    validate_name,
)


class TestValidateName:
    def test_accepts_simple_alphanumeric(self):
        assert validate_name("softreflective") == "softreflective"

    def test_accepts_uppercase(self):
        assert validate_name("SoftReflective") == "SoftReflective"

    def test_accepts_dot_underscore_hyphen(self):
        assert validate_name("algo_v2.final-1") == "algo_v2.final-1"

    def test_accepts_leading_digit(self):
        assert validate_name("1algo") == "1algo"

    def test_rejects_empty(self):
        with pytest.raises(ValidationError):
            validate_name("")

    def test_rejects_leading_hyphen(self):
        with pytest.raises(ValidationError):
            validate_name("-algo")

    def test_rejects_leading_dot(self):
        with pytest.raises(ValidationError):
            validate_name(".algo")

    def test_rejects_single_dot(self):
        with pytest.raises(ValidationError):
            validate_name(".")

    def test_rejects_double_dot(self):
        with pytest.raises(ValidationError):
            validate_name("..")

    def test_rejects_path_traversal(self):
        with pytest.raises(ValidationError):
            validate_name("../foo")

    def test_rejects_slash(self):
        with pytest.raises(ValidationError):
            validate_name("foo/bar")

    def test_rejects_whitespace(self):
        with pytest.raises(ValidationError):
            validate_name("soft reflective")

    def test_rejects_oversized(self):
        with pytest.raises(ValidationError):
            validate_name("a" * 129)

    def test_accepts_max_length(self):
        assert validate_name("a" * 128) == "a" * 128


class TestReadTranslationOutput:
    def test_new_schema_pass_through(self, tmp_path):
        payload = {
            "version": 1,
            "translation_hash": "a" * 64,
            "source": "skill",
            "alias": "softreflective-v1",
            "algorithms": [
                {"name": "sr", "source_path": "algorithms/sr.py",
                 "source_sha256": "e3b0", "config_path": None,
                 "image_ref": "quay.io/x:tag", "image_digest": "sha256:aa"},
            ],
            "created_at": "2026-07-02T14:00:00Z",
        }
        p = tmp_path / "translation_output.json"
        p.write_text(json.dumps(payload))
        data = translation_ref.read_translation_output(p)
        assert data["alias"] == "softreflective-v1"
        assert data["algorithms"][0]["image_ref"] == "quay.io/x:tag"
        assert data["algorithms"][0]["image_digest"] == "sha256:aa"

    def test_legacy_top_level_image_ref_normalized(self, tmp_path):
        payload = {
            "version": 1,
            "translation_hash": "b" * 64,
            "source": "byo",
            "algorithms": [{"name": "legacy"}],
            "image_ref": "ghcr.io/legacy:v1",
            "image_digest": "sha256:bb",
            "created_at": "2026-06-01T10:00:00Z",
        }
        p = tmp_path / "translation_output.json"
        p.write_text(json.dumps(payload))
        data = translation_ref.read_translation_output(p)
        assert data["alias"] is None
        # Top-level image_ref/digest lifted into every algorithms[i].
        assert data["algorithms"][0]["image_ref"] == "ghcr.io/legacy:v1"
        assert data["algorithms"][0]["image_digest"] == "sha256:bb"

    def test_legacy_shape_does_not_overwrite_per_algo(self, tmp_path):
        # If a file had *both* (odd, but defensive), per-algo wins.
        payload = {
            "version": 1,
            "translation_hash": "c" * 64,
            "source": "byo",
            "alias": None,
            "algorithms": [{"name": "x", "image_ref": "specific:tag"}],
            "image_ref": "top:tag",
            "created_at": "2026-06-01T10:00:00Z",
        }
        p = tmp_path / "translation_output.json"
        p.write_text(json.dumps(payload))
        data = translation_ref.read_translation_output(p)
        assert data["algorithms"][0]["image_ref"] == "specific:tag"

    def test_missing_alias_defaults_to_none(self, tmp_path):
        payload = {
            "version": 1,
            "translation_hash": "d" * 64,
            "source": "skill",
            "algorithms": [{"name": "x"}],
            "created_at": "2026-07-02T14:00:00Z",
        }
        p = tmp_path / "translation_output.json"
        p.write_text(json.dumps(payload))
        data = translation_ref.read_translation_output(p)
        assert data["alias"] is None

    def test_optional_git_provenance_fields_preserved(self, tmp_path):
        """Issue #587: algorithm entries from --build git-URL specs carry
        optional source_git_url + source_git_ref. Reader passes them through
        untouched; consumers that don't reference them are unaffected."""
        payload = {
            "version": 1,
            "translation_hash": "e" * 64,
            "source": "byo",
            "alias": None,
            "algorithms": [
                {
                    "name": "pr1956b",
                    "image_ref": "ghcr.io/x/y:169b7b2bbbbf-pr1956b",
                    "image_digest": "sha256:cc",
                    "source_git_url": "https://github.com/x/y.git",
                    "source_git_ref": "a" * 40,
                },
            ],
            "created_at": "2026-07-20T14:00:00Z",
        }
        p = tmp_path / "translation_output.json"
        p.write_text(json.dumps(payload))
        data = translation_ref.read_translation_output(p)
        e = data["algorithms"][0]
        assert e["source_git_url"] == "https://github.com/x/y.git"
        assert e["source_git_ref"] == "a" * 40
        assert e["image_ref"] == "ghcr.io/x/y:169b7b2bbbbf-pr1956b"

    def test_entries_without_git_provenance_still_parse(self, tmp_path):
        """Algorithm entries without source_git_url/ref (BYO classic +
        path-based --build) parse unchanged — the fields are optional."""
        payload = {
            "version": 1,
            "translation_hash": "f" * 64,
            "source": "byo",
            "alias": "solo",
            "algorithms": [
                {
                    "name": "solo",
                    "image_ref": "ghcr.io/x/y:v1",
                    "image_digest": "sha256:dd",
                },
            ],
            "created_at": "2026-07-20T14:00:00Z",
        }
        p = tmp_path / "translation_output.json"
        p.write_text(json.dumps(payload))
        data = translation_ref.read_translation_output(p)
        e = data["algorithms"][0]
        assert "source_git_url" not in e
        assert "source_git_ref" not in e
        assert e["image_ref"] == "ghcr.io/x/y:v1"


def _write_translation(base: Path, thash: str, payload: dict) -> None:
    tdir = base / thash
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / "translation_output.json").write_text(json.dumps(payload))


class TestIterTranslations:
    def test_yields_all_translations(self, tmp_path):
        base = tmp_path / "translations"
        _write_translation(base, "a" * 64, {
            "version": 1, "translation_hash": "a" * 64, "source": "skill",
            "alias": "algo-a", "algorithms": [{"name": "a"}],
            "created_at": "2026-07-01T00:00:00Z",
        })
        _write_translation(base, "b" * 64, {
            "version": 1, "translation_hash": "b" * 64, "source": "byo",
            "alias": "algo-b", "algorithms": [{"name": "b"}],
            "created_at": "2026-07-02T00:00:00Z",
        })
        results = dict(translation_ref.iter_translations(base))
        assert set(results.keys()) == {"a" * 64, "b" * 64}
        assert results["a" * 64]["alias"] == "algo-a"

    def test_missing_directory_yields_empty(self, tmp_path):
        base = tmp_path / "nonexistent"
        assert list(translation_ref.iter_translations(base)) == []

    def test_malformed_json_logged_and_skipped(self, tmp_path, caplog):
        base = tmp_path / "translations"
        good = "a" * 64
        bad = "b" * 64
        _write_translation(base, good, {
            "version": 1, "translation_hash": good, "source": "byo",
            "alias": None, "algorithms": [{"name": "x"}],
            "created_at": "2026-07-01T00:00:00Z",
        })
        (base / bad).mkdir(parents=True)
        (base / bad / "translation_output.json").write_text("{not json")
        caplog.set_level(logging.WARNING, logger=translation_ref.__name__)
        results = dict(translation_ref.iter_translations(base))
        assert good in results
        assert bad not in results
        assert any("malformed" in rec.message.lower() or bad in rec.message
                   for rec in caplog.records)

    def test_missing_translation_output_file_skipped(self, tmp_path):
        base = tmp_path / "translations"
        (base / ("c" * 64)).mkdir(parents=True)
        # No translation_output.json in that dir.
        assert list(translation_ref.iter_translations(base)) == []

    def test_dir_name_not_full_hash_skipped(self, tmp_path):
        # Prevents surprises from stray files/directories.
        base = tmp_path / "translations"
        _write_translation(base, "not-a-hash", {
            "version": 1, "translation_hash": "not-a-hash", "source": "byo",
            "alias": None, "algorithms": [], "created_at": "x",
        })
        assert list(translation_ref.iter_translations(base)) == []


class TestFindByAlias:
    def test_finds_matching_alias(self, tmp_path):
        base = tmp_path / "translations"
        h = "a" * 64
        _write_translation(base, h, {
            "version": 1, "translation_hash": h, "source": "skill",
            "alias": "my-alias", "algorithms": [{"name": "x"}],
            "created_at": "2026-07-01T00:00:00Z",
        })
        assert translation_ref.find_by_alias("my-alias", base) == h

    def test_returns_none_when_no_match(self, tmp_path):
        base = tmp_path / "translations"
        h = "a" * 64
        _write_translation(base, h, {
            "version": 1, "translation_hash": h, "source": "skill",
            "alias": "other", "algorithms": [{"name": "x"}],
            "created_at": "2026-07-01T00:00:00Z",
        })
        assert translation_ref.find_by_alias("my-alias", base) is None

    def test_skips_null_aliases(self, tmp_path):
        base = tmp_path / "translations"
        h = "a" * 64
        _write_translation(base, h, {
            "version": 1, "translation_hash": h, "source": "byo",
            "alias": None, "algorithms": [{"name": "x"}],
            "created_at": "2026-07-01T00:00:00Z",
        })
        assert translation_ref.find_by_alias("", base) is None
        # Also: an alias that is the string "None" doesn't match null.
        assert translation_ref.find_by_alias("None", base) is None


class TestResolveTranslationRef:
    def _seed(self, base: Path, hashes_and_aliases: list[tuple[str, str | None]]):
        for thash, alias in hashes_and_aliases:
            _write_translation(base, thash, {
                "version": 1, "translation_hash": thash, "source": "skill",
                "alias": alias, "algorithms": [{"name": "algo"}],
                "created_at": "2026-07-01T00:00:00Z",
            })

    def test_resolves_alias_exact(self, tmp_path):
        base = tmp_path / "translations"
        h = "a" * 64
        self._seed(base, [(h, "my-alias")])
        assert resolve_translation_ref("my-alias", base) == h

    def test_resolves_full_hash(self, tmp_path):
        base = tmp_path / "translations"
        h = "a" * 64
        self._seed(base, [(h, "my-alias")])
        assert resolve_translation_ref(h, base) == h

    def test_resolves_unique_prefix(self, tmp_path):
        base = tmp_path / "translations"
        h = "abcdef" + "0" * 58
        self._seed(base, [(h, None), ("f" * 64, None)])
        assert resolve_translation_ref("abcd", base) == h

    def test_prefix_too_short(self, tmp_path):
        base = tmp_path / "translations"
        h = "abcdef" + "0" * 58
        self._seed(base, [(h, None)])
        with pytest.raises(ResolveError):
            resolve_translation_ref("abc", base)

    def test_prefix_ambiguous_lists_candidates(self, tmp_path):
        base = tmp_path / "translations"
        h1 = "abcd" + "1" * 60
        h2 = "abcd" + "2" * 60
        self._seed(base, [(h1, None), (h2, None)])
        with pytest.raises(ResolveError) as exc:
            resolve_translation_ref("abcd", base)
        assert h1 in str(exc.value)
        assert h2 in str(exc.value)

    def test_full_hash_not_present_errors(self, tmp_path):
        base = tmp_path / "translations"
        # Empty dir.
        base.mkdir()
        h = "a" * 64
        with pytest.raises(ResolveError):
            resolve_translation_ref(h, base)

    def test_no_match_error_mentions_list_command(self, tmp_path):
        base = tmp_path / "translations"
        self._seed(base, [("a" * 64, "other-alias")])
        with pytest.raises(ResolveError) as exc:
            resolve_translation_ref("nomatch", base)
        assert "list translations" in str(exc.value)

    def test_alias_wins_over_prefix(self, tmp_path):
        # An alias named "abcd" wins over a prefix match of "abcd*".
        base = tmp_path / "translations"
        h1 = "1" * 64
        h2 = "abcd" + "0" * 60
        self._seed(base, [(h1, "abcd"), (h2, None)])
        assert resolve_translation_ref("abcd", base) == h1

    def test_invalid_ref_rejected_before_scan(self, tmp_path):
        base = tmp_path / "translations"
        # Ref with slash — regex fails; scan never happens.
        with pytest.raises(ResolveError):
            resolve_translation_ref("foo/bar", base)

    def test_empty_translations_dir_error(self, tmp_path):
        base = tmp_path / "translations"
        # base does not exist at all.
        with pytest.raises(ResolveError):
            resolve_translation_ref("anything", base)


class TestPathSafetyMatrix:
    @pytest.mark.parametrize("bad", [
        "../foo",
        "..",
        ".",
        "foo/bar",
        "",
        "a" * 129,
        "-leading-hyphen",
        "@#$%",           # all non-alphanumeric
        ".hidden",
        "foo\x00bar",     # embedded null
        "foo\nbar",       # embedded newline
    ])
    def test_rejects_dangerous_name(self, bad):
        with pytest.raises(ValidationError):
            validate_name(bad)

    @pytest.mark.parametrize("good", [
        "softreflective",
        "algo-v2",
        "Algo_v2",
        "1st",
        "a.b.c",
        "algo_v2.final-1",
        "a" * 128,
    ])
    def test_accepts_safe_name(self, good):
        assert validate_name(good) == good
