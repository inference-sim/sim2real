"""Tests for pipeline/lib/source_locator.py."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from pipeline.lib import source_locator as sl


# ── parse_location ──────────────────────────────────────────────────────────


def test_parse_location_path():
    loc = sl.parse_location("./llm-d-router")
    assert isinstance(loc, sl.PathLocation)
    assert loc.path == Path("./llm-d-router")


def test_parse_location_absolute_path():
    loc = sl.parse_location("/abs/path/to/repo")
    assert isinstance(loc, sl.PathLocation)
    assert loc.path == Path("/abs/path/to/repo")


def test_parse_location_git_https():
    loc = sl.parse_location("git+https://github.com/foo/bar.git#main")
    assert isinstance(loc, sl.GitLocation)
    assert loc.url == "https://github.com/foo/bar.git"
    assert loc.ref == "main"


def test_parse_location_git_ssh_with_at_sign_in_url():
    loc = sl.parse_location(
        "git+ssh://git@github.com/foo/bar.git#abc1234"
    )
    assert isinstance(loc, sl.GitLocation)
    # The @ inside the URL host stays with the URL (rsplit on '#'
    # separates url from ref cleanly).
    assert loc.url == "ssh://git@github.com/foo/bar.git"
    assert loc.ref == "abc1234"


def test_parse_location_git_url_with_hash_in_ref_uses_last_hash():
    # Refs cannot contain '#' by git convention; rsplit-on-# gives the
    # last one as the ref boundary. Guard against hypothetical inputs.
    loc = sl.parse_location(
        "git+https://host/repo.git#branch-with-#-in-name"
    )
    # rsplit takes the LAST '#'. URL keeps everything before it; ref is
    # the tail. This is by design — refs shouldn't contain '#'.
    assert isinstance(loc, sl.GitLocation)
    assert loc.url == "https://host/repo.git#branch-with-"
    assert loc.ref == "-in-name"


def test_parse_location_git_missing_ref_raises():
    with pytest.raises(sl.SourceLocatorError, match="missing '#<ref>'"):
        sl.parse_location("git+https://github.com/foo/bar.git")


def test_parse_location_git_empty_ref_raises():
    with pytest.raises(sl.SourceLocatorError, match="empty ref"):
        sl.parse_location("git+https://github.com/foo/bar.git#")


def test_parse_location_empty_raises():
    with pytest.raises(sl.SourceLocatorError, match="empty --build"):
        sl.parse_location("")


def test_parse_location_git_prefix_case_insensitive():
    # Odd but legal to type: matches the same GitLocation regardless.
    loc = sl.parse_location("GIT+HTTPS://host/repo.git#main")
    assert isinstance(loc, sl.GitLocation)


# ── hash_path_contents ──────────────────────────────────────────────────────


def _write(root: Path, rel: str, data: bytes) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def test_hash_path_contents_deterministic(tmp_path):
    """Same content, same hash — regardless of order of file creation."""
    a = tmp_path / "a"
    b = tmp_path / "b"
    _write(a, "foo.txt", b"hello")
    _write(a, "sub/bar.txt", b"world")
    _write(b, "sub/bar.txt", b"world")   # created in different order
    _write(b, "foo.txt", b"hello")
    assert sl.hash_path_contents(a) == sl.hash_path_contents(b)


def test_hash_path_contents_sensitive_to_bytes(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    _write(a, "foo.txt", b"hello")
    _write(b, "foo.txt", b"hell!")
    assert sl.hash_path_contents(a) != sl.hash_path_contents(b)


def test_hash_path_contents_sensitive_to_paths(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    _write(a, "foo.txt", b"data")
    _write(b, "bar.txt", b"data")
    assert sl.hash_path_contents(a) != sl.hash_path_contents(b)


def test_hash_path_contents_ignores_git_dir(tmp_path):
    """Two trees with identical non-.git content should hash equal even
    when one has a .git/ directory with arbitrary content."""
    a = tmp_path / "a"
    b = tmp_path / "b"
    _write(a, "policy.go", b"package foo\n")
    _write(b, "policy.go", b"package foo\n")
    _write(b, ".git/HEAD", b"ref: refs/heads/main\n")
    _write(b, ".git/config", b"[core]\n")
    _write(b, ".git/objects/pack/pack-abc.pack", b"binary junk")
    assert sl.hash_path_contents(a) == sl.hash_path_contents(b)


def test_hash_path_contents_ignores_nested_git_dir(tmp_path):
    """Nested submodule-like .git dirs are also skipped."""
    dirty = tmp_path / "dirty"
    clean = tmp_path / "clean"
    for root in (dirty, clean):
        _write(root, "policy.go", b"package foo\n")
        _write(root, "vendor/lib/keep.go", b"")
    # Dirty tree also has a nested .git dir; clean tree does not.
    _write(dirty, "vendor/lib/.git/HEAD", b"garbage")
    _write(dirty, "vendor/lib/.git/config", b"[core]")
    assert sl.hash_path_contents(dirty) == sl.hash_path_contents(clean)


def test_hash_path_contents_symlink_recorded_by_target(tmp_path):
    """Symlinks hash as ``symlink:<target>``; same target → same hash."""
    a = tmp_path / "a"
    a.mkdir()
    (a / "link").symlink_to("outside/target")
    b = tmp_path / "b"
    b.mkdir()
    (b / "link").symlink_to("outside/target")
    assert sl.hash_path_contents(a) == sl.hash_path_contents(b)


def test_hash_path_contents_not_a_dir_raises(tmp_path):
    f = tmp_path / "file.txt"
    f.write_text("hi")
    with pytest.raises(sl.SourceLocatorError, match="not a directory"):
        sl.hash_path_contents(f)


def test_hash_path_contents_missing_raises(tmp_path):
    with pytest.raises(sl.SourceLocatorError, match="not a directory"):
        sl.hash_path_contents(tmp_path / "nope")


# ── PathLocation ────────────────────────────────────────────────────────────


def test_path_location_identity_matches_hash(tmp_path):
    d = tmp_path / "src"
    _write(d, "policy.go", b"package foo\n")
    loc = sl.PathLocation(path=d)
    assert loc.identity() == sl.hash_path_contents(d)


def test_path_location_materialize_returns_same_dir(tmp_path):
    d = tmp_path / "src"
    _write(d, "policy.go", b"package foo\n")
    loc = sl.PathLocation(path=d)
    with loc.materialize() as materialized:
        assert materialized == d


def test_path_location_provenance_empty(tmp_path):
    d = tmp_path / "src"
    d.mkdir()
    assert sl.PathLocation(path=d).provenance() == {}


# ── GitLocation ─────────────────────────────────────────────────────────────


def test_git_location_identity_passes_full_sha_through():
    loc = sl.GitLocation(
        url="https://github.com/foo/bar.git",
        ref="abc1234def567890abc1234def567890abc12345",  # 40 hex chars
    )
    # Full sha short-circuits ls-remote (no network call needed).
    with mock.patch.object(sl.subprocess, "run") as m:
        assert loc.identity() == "abc1234def567890abc1234def567890abc12345"
        assert not m.called  # no subprocess when ref is already a full sha


def test_git_location_identity_resolves_branch_via_ls_remote():
    resolved = "b" * 40
    fake = mock.Mock(returncode=0, stdout=f"{resolved}\trefs/heads/main\n", stderr="")
    loc = sl.GitLocation(url="https://x/y.git", ref="main")
    with mock.patch.object(sl.subprocess, "run", return_value=fake):
        assert loc.identity() == resolved


def test_git_location_identity_prefers_dereferenced_tag():
    """When ls-remote returns both a tag and its `^{}` deref, prefer deref."""
    tag_sha = "a" * 40
    commit_sha = "c" * 40
    stdout = (
        f"{tag_sha}\trefs/tags/v1.0\n"
        f"{commit_sha}\trefs/tags/v1.0^{{}}\n"
    )
    fake = mock.Mock(returncode=0, stdout=stdout, stderr="")
    loc = sl.GitLocation(url="https://x/y.git", ref="v1.0")
    with mock.patch.object(sl.subprocess, "run", return_value=fake):
        assert loc.identity() == commit_sha


def test_git_location_identity_ls_remote_failure_raises():
    fake = mock.Mock(returncode=2, stdout="", stderr="fatal: unknown ref\n")
    loc = sl.GitLocation(url="https://x/y.git", ref="nope")
    with mock.patch.object(sl.subprocess, "run", return_value=fake):
        with pytest.raises(sl.SourceLocatorError, match="ls-remote failed"):
            loc.identity()


def test_git_location_identity_short_sha_goes_to_ls_remote():
    """Short shas (< 40 chars) fall through to ls-remote."""
    resolved = "d" * 40
    fake = mock.Mock(returncode=0, stdout=f"{resolved}\trefs/heads/main\n", stderr="")
    loc = sl.GitLocation(url="https://x/y.git", ref="abc1234")  # 7 chars
    with mock.patch.object(sl.subprocess, "run", return_value=fake) as m:
        loc.identity()
        assert m.called


def test_git_location_materialize_shallow_clone_success(tmp_path):
    """Materialize dispatches git clone --depth 1 --branch <ref>."""
    loc = sl.GitLocation(url="https://x/y.git", ref="main")

    def fake_run(cmd, *args, **kwargs):
        # Simulate a successful shallow clone: create the dest dir.
        if cmd[:2] == ["git", "clone"] and "--depth" in cmd:
            dest = Path(cmd[-1])
            dest.mkdir(parents=True)
            (dest / "policy.go").write_text("hi")
            return mock.Mock(returncode=0, stdout="", stderr="")
        raise AssertionError(f"unexpected subprocess call: {cmd!r}")

    with mock.patch.object(sl.subprocess, "run", side_effect=fake_run):
        with loc.materialize() as p:
            assert (p / "policy.go").read_text() == "hi"
    # After context exit the scratch tempdir is removed.
    assert not p.exists()


def test_git_location_materialize_falls_back_to_full_clone(tmp_path):
    """Shallow --branch fails on raw sha → full clone + checkout."""
    loc = sl.GitLocation(url="https://x/y.git", ref="abc123")
    calls: list[list[str]] = []

    def fake_run(cmd, *args, **kwargs):
        calls.append(list(cmd))
        if cmd[:2] == ["git", "clone"] and "--depth" in cmd:
            # Shallow clone fails (ref not a branch/tag).
            return mock.Mock(
                returncode=128,
                stdout="",
                stderr="fatal: Remote branch abc123 not found\n",
            )
        if cmd[:2] == ["git", "clone"]:
            dest = Path(cmd[-1])
            dest.mkdir(parents=True)
            (dest / "policy.go").write_text("hi")
            return mock.Mock(returncode=0, stdout="", stderr="")
        if cmd[:2] == ["git", "-C"] and "checkout" in cmd:
            return mock.Mock(returncode=0, stdout="", stderr="")
        raise AssertionError(f"unexpected call: {cmd!r}")

    with mock.patch.object(sl.subprocess, "run", side_effect=fake_run):
        with loc.materialize() as p:
            assert (p / "policy.go").read_text() == "hi"

    # Three subprocess invocations: failed shallow, full clone, checkout.
    assert len(calls) == 3
    assert calls[1][:2] == ["git", "clone"]
    assert "--depth" not in calls[1]
    assert calls[2][:4] == ["git", "-C", str(p), "checkout"]


def test_git_location_materialize_full_clone_failure_raises(tmp_path):
    loc = sl.GitLocation(url="https://bogus/x.git", ref="deadbeef")

    def fake_run(cmd, *args, **kwargs):
        if "clone" in cmd:
            return mock.Mock(
                returncode=128, stdout="",
                stderr="fatal: could not read from remote\n",
            )
        raise AssertionError(f"unexpected: {cmd!r}")

    with mock.patch.object(sl.subprocess, "run", side_effect=fake_run):
        with pytest.raises(sl.SourceLocatorError, match="git clone failed"):
            with loc.materialize():
                pass


def test_git_location_materialize_checkout_failure_raises(tmp_path):
    loc = sl.GitLocation(url="https://x/y.git", ref="unreachable")

    def fake_run(cmd, *args, **kwargs):
        if "clone" in cmd and "--depth" in cmd:
            return mock.Mock(returncode=128, stdout="", stderr="fatal\n")
        if "clone" in cmd:
            dest = Path(cmd[-1])
            dest.mkdir(parents=True)
            return mock.Mock(returncode=0, stdout="", stderr="")
        if "checkout" in cmd:
            return mock.Mock(
                returncode=128, stdout="",
                stderr="fatal: reference is not a tree\n",
            )
        raise AssertionError(f"unexpected: {cmd!r}")

    with mock.patch.object(sl.subprocess, "run", side_effect=fake_run):
        with pytest.raises(sl.SourceLocatorError, match="checkout .* failed"):
            with loc.materialize():
                pass


def test_git_location_provenance_returns_url_and_resolved_ref():
    loc = sl.GitLocation(url="https://x/y.git", ref="a" * 40)
    prov = loc.provenance()
    assert prov == {
        "source_git_url": "https://x/y.git",
        "source_git_ref": "a" * 40,
    }


def test_git_location_identity_memoizes_across_calls():
    """PR #588 review fix: identity() must not re-invoke ls-remote when
    called more than once on the same instance (previously provenance()
    triggered a second network call, creating a race window if the branch
    tip shifted between calls)."""
    resolved = "9" * 40
    fake = mock.Mock(returncode=0, stdout=f"{resolved}\trefs/heads/main\n", stderr="")
    loc = sl.GitLocation(url="https://x/y.git", ref="main")
    with mock.patch.object(sl.subprocess, "run", return_value=fake) as m:
        first = loc.identity()
        second = loc.identity()
        third = loc.provenance()["source_git_ref"]
    assert first == second == third == resolved
    # Exactly one ls-remote subprocess despite three logical resolutions.
    assert m.call_count == 1


def test_git_location_memoized_identity_survives_race_scenario():
    """Even if the remote's ref-tip changed between the identity() call
    used for the translation hash and the provenance() call used for the
    on-disk record, the memoized value keeps them consistent."""
    tip_1 = "1" * 40
    tip_2 = "2" * 40
    # If the mock returned tip_1 first and tip_2 second, an unmemoized
    # implementation would produce divergent values. Memoized: both
    # observers see tip_1.
    responses = [
        mock.Mock(returncode=0, stdout=f"{tip_1}\trefs/heads/main\n", stderr=""),
        mock.Mock(returncode=0, stdout=f"{tip_2}\trefs/heads/main\n", stderr=""),
    ]
    loc = sl.GitLocation(url="https://x/y.git", ref="main")
    with mock.patch.object(sl.subprocess, "run", side_effect=responses):
        identity = loc.identity()
        prov_ref = loc.provenance()["source_git_ref"]
    assert identity == prov_ref == tip_1
