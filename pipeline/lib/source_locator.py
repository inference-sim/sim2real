"""Source-location abstraction for ``translation register --build``.

Two location kinds are supported today:

- :class:`PathLocation` — a filesystem directory the operator already has
  on disk. Framework treats it as the buildkit input verbatim; no clone,
  no snapshot. The location's identity is a canonical SHA-256 over the
  directory contents (see :func:`hash_path_contents`).
- :class:`GitLocation` — a ``git+<url>#<ref>`` URL. Framework
  shallow-clones into a scratch directory, checks out ``<ref>`` (commit
  sha, branch, or tag), then hands the scratch directory to buildkit.
  The location's identity is the resolved full commit sha.

Both kinds satisfy a small ABC (:class:`Location`) with two methods the
register flow depends on:

- :meth:`identity` — the string fed as the ``image`` field to
  :func:`pipeline.sim2real._compute_translation_hash`. For BYO
  (``--algorithm``) that's the skopeo-probed digest; for ``--build``
  we substitute a source-content identifier so the translation hash
  is derived from *inputs*, not the not-yet-built output image.
- :meth:`materialize` — a context manager yielding the source directory
  path to hand to buildkit. Path locations pass their path through
  unchanged; git locations clone into a temp directory and clean up on
  exit.

The module is intentionally free of buildkit / registry / cluster
concerns — it only knows how to turn a location string into (identity,
source-directory). Register does the rest.
"""

from __future__ import annotations

import contextlib
import hashlib
import re
import shutil
import subprocess
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator


class SourceLocatorError(Exception):
    """Raised for invalid location specs or materialization failures."""


_GIT_PREFIX_RE = re.compile(r"^git\+(https?|ssh)://", re.IGNORECASE)


class Location(ABC):
    """Base class for a ``--build <location>`` value."""

    @abstractmethod
    def identity(self) -> str:
        """Return the source-content identifier used to hash this entry.

        Must be deterministic for the same content; used as the ``image``
        input to ``_compute_translation_hash`` for ``--build`` entries.
        """

    @abstractmethod
    @contextlib.contextmanager
    def materialize(self) -> Iterator[Path]:
        """Yield a filesystem path pointing at a ready-to-build source tree.

        For :class:`PathLocation` the yielded path is the location's own
        directory (no copy). For :class:`GitLocation` a scratch directory
        is created for the clone and removed on context exit.
        """

    @abstractmethod
    def provenance(self) -> dict:
        """Return the fields to record on the algorithm entry.

        ``{}`` for :class:`PathLocation` (nothing reproducible to
        capture). ``{"source_git_url": ..., "source_git_ref": ...}``
        for :class:`GitLocation` — resolved to a full commit sha at
        materialize time.
        """


@dataclass(frozen=True)
class PathLocation(Location):
    """A local filesystem directory used verbatim as the buildkit input."""

    path: Path

    def identity(self) -> str:
        return hash_path_contents(self.path)

    @contextlib.contextmanager
    def materialize(self) -> Iterator[Path]:
        # No copy — buildkit consumes the caller-supplied path directly.
        # Kept as a context manager for symmetry with GitLocation.
        yield self.path

    def provenance(self) -> dict:
        return {}


@dataclass(frozen=True)
class GitLocation(Location):
    """A ``git+<url>#<ref>`` URL. Cloned into a scratch dir on materialize.

    ``identity()`` resolves the ref via ``git ls-remote`` and memoizes the
    result on the instance so a second call (e.g. from ``provenance()``)
    doesn't reissue the network round-trip. The memoization also removes
    a race window where the remote's branch tip could shift between the
    identity-call (which feeds the translation hash) and the provenance-
    call (which records the ref on disk) — after this change, both reads
    return the same resolved sha for the lifetime of the instance.
    """

    url: str
    ref: str
    # Mutable memoization slot. ``frozen=True`` blocks assignment to
    # top-level dataclass fields but not mutation of nested containers,
    # which is exactly what we need here. Never reassigned; only its
    # single-key ``sha`` slot is populated on first identity() call.
    _cache: dict = field(default_factory=dict, compare=False, repr=False)

    def identity(self) -> str:
        # Resolve the user-supplied ref to a full commit sha via
        # ``git ls-remote`` on first call; memoize for subsequent calls.
        # If the user supplied a bare commit sha, ls-remote won't match
        # (refs are branches / tags), so we return the sha unchanged.
        # Short shas (< 40 hex chars) cannot be resolved without a clone;
        # require full 40-char shas so identity remains deterministic.
        if "sha" not in self._cache:
            self._cache["sha"] = _resolve_git_ref(self.url, self.ref)
        return self._cache["sha"]

    @contextlib.contextmanager
    def materialize(self) -> Iterator[Path]:
        with tempfile.TemporaryDirectory(prefix="sim2real-git-") as scratch:
            scratch_path = Path(scratch) / "clone"
            _clone_and_checkout(self.url, self.ref, scratch_path)
            yield scratch_path

    def provenance(self) -> dict:
        return {
            "source_git_url": self.url,
            "source_git_ref": self.identity(),
        }


def parse_location(spec: str) -> Location:
    """Parse a ``--build <location>`` string into a :class:`Location`.

    ``git+https://.../repo.git#<ref>`` or ``git+ssh://.../repo.git#<ref>``
    → :class:`GitLocation`. Anything else → :class:`PathLocation` (the
    caller decides whether the path resolves; we don't check existence
    here because tests may construct paths that don't yet exist on disk).
    """
    if not isinstance(spec, str) or not spec:
        raise SourceLocatorError(f"empty --build location: {spec!r}")
    if _GIT_PREFIX_RE.match(spec):
        url_ref = spec[len("git+"):]
        if "#" not in url_ref:
            raise SourceLocatorError(
                f"git URL missing '#<ref>' suffix: {spec!r} "
                "(expected e.g. 'git+https://host/repo.git#main' or "
                "'git+ssh://git@host/repo.git#<sha>')"
            )
        url, ref = url_ref.rsplit("#", 1)
        if not url:
            raise SourceLocatorError(f"git URL has empty host+path: {spec!r}")
        if not ref:
            raise SourceLocatorError(f"git URL has empty ref: {spec!r}")
        return GitLocation(url=url, ref=ref)
    return PathLocation(path=Path(spec))


def hash_path_contents(path: Path) -> str:
    """Canonical SHA-256 over the contents of a directory tree.

    Walks ``path``, sorts entries lexicographically, and folds each file's
    (relative-path, file-sha256) into a top-level SHA-256. ``.git/`` is
    skipped — cloned repos and dev checkouts should hash to the same
    identity if the checked-out tree is identical, regardless of local
    git metadata. Symlinks are followed only when they point inside
    ``path``; symlinks that escape are recorded as their target string
    (deterministic without leaking outside-tree bytes).

    Raises :class:`SourceLocatorError` if ``path`` is not an existing
    directory.
    """
    root = Path(path).resolve()
    if not root.is_dir():
        raise SourceLocatorError(
            f"--build path is not a directory: {path}"
        )
    top = hashlib.sha256()
    for rel_str, digest in _walk_for_hash(root):
        top.update(rel_str.encode("utf-8"))
        top.update(b"\0")
        top.update(digest.encode("ascii"))
        top.update(b"\n")
    return top.hexdigest()


def _walk_for_hash(root: Path) -> Iterator[tuple[str, str]]:
    """Yield (posix-relpath, hex-sha256) pairs sorted lex by relpath.

    Skips any directory named exactly ``.git`` at any depth. Regular
    files hash to their SHA-256; symlinks hash to ``symlink:<target>``
    so escape paths remain deterministic.
    """
    entries: list[tuple[str, Path]] = []
    for p in root.rglob("*"):
        # Skip anything inside a .git directory at any depth.
        rel_parts = p.relative_to(root).parts
        if any(part == ".git" for part in rel_parts):
            continue
        entries.append(("/".join(rel_parts), p))
    entries.sort(key=lambda t: t[0])
    for rel_str, p in entries:
        if p.is_symlink():
            target = str(p.readlink())
            digest = hashlib.sha256(
                f"symlink:{target}".encode("utf-8")
            ).hexdigest()
        elif p.is_file():
            h = hashlib.sha256()
            with p.open("rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    h.update(chunk)
            digest = h.hexdigest()
        elif p.is_dir():
            # Directories are represented by their entries (already
            # queued by rglob); no separate entry emitted.
            continue
        else:
            # Sockets, block devices, etc. — reject rather than pretend
            # to hash them; a router source tree should not contain
            # these.
            raise SourceLocatorError(
                f"unsupported filesystem entry in --build source: {p}"
            )
        yield rel_str, digest


_SHA1_RE = re.compile(r"^[0-9a-f]{40}$")


def _resolve_git_ref(url: str, ref: str) -> str:
    """Return a full 40-char commit sha for ``ref`` on ``url``.

    Uses ``git ls-remote <url> <ref>`` to resolve branch and tag names
    to their tip commit shas. For an already-full commit sha, returns
    the ref unchanged (git has no cheap "does this sha exist on the
    remote" probe; validation happens at clone time).

    Raises :class:`SourceLocatorError` on ls-remote failure or when the
    ref does not resolve.
    """
    if _SHA1_RE.match(ref):
        # User supplied a full commit sha; treat as authoritative. The
        # subsequent clone/checkout will fail loud if the sha is not
        # on the remote.
        return ref
    result = subprocess.run(
        ["git", "ls-remote", "--exit-code", url, ref],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SourceLocatorError(
            f"git ls-remote failed for {url}#{ref}: "
            f"{result.stderr.strip() or 'ref not found'}"
        )
    # ls-remote output: "<sha>\t<refname>\n" possibly multiple lines
    # (e.g., a tag and its dereferenced commit as `refs/tags/x^{}`).
    # Prefer the dereferenced form (`^{}`) when present; otherwise the
    # first line.
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    deref = [line for line in lines if line.endswith("^{}")]
    chosen = deref[0] if deref else lines[0]
    sha = chosen.split("\t", 1)[0].strip()
    if not _SHA1_RE.match(sha):
        raise SourceLocatorError(
            f"git ls-remote returned unexpected sha for {url}#{ref}: "
            f"{sha!r}"
        )
    return sha


def _clone_and_checkout(url: str, ref: str, dest: Path) -> None:
    """Materialize ``url`` at ``ref`` into ``dest`` (which must not exist).

    Strategy:
      1. Try a shallow clone with ``--branch <ref>``. Works when ``ref``
         is a branch or tag name.
      2. On failure, fall back to a full clone plus ``git checkout <ref>``.
         Handles arbitrary commit shas.

    ``dest`` is created by ``git clone`` itself; parents must exist.

    Raises :class:`SourceLocatorError` on unrecoverable clone failure.
    """
    if dest.exists():
        raise SourceLocatorError(
            f"internal error: clone target already exists: {dest}"
        )
    dest.parent.mkdir(parents=True, exist_ok=True)

    # Shallow-clone --branch works for branches and tags.
    shallow = subprocess.run(
        [
            "git", "clone",
            "--depth", "1",
            "--branch", ref,
            "--single-branch",
            url, str(dest),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if shallow.returncode == 0:
        return
    # Shallow-clone failed (most likely because ref is a raw sha, which
    # --branch cannot accept). Retry with a full clone + checkout.
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    full = subprocess.run(
        ["git", "clone", url, str(dest)],
        capture_output=True,
        text=True,
        check=False,
    )
    if full.returncode != 0:
        raise SourceLocatorError(
            f"git clone failed for {url}: "
            f"{full.stderr.strip() or 'unknown error'}"
        )
    checkout = subprocess.run(
        ["git", "-C", str(dest), "checkout", "--detach", ref],
        capture_output=True,
        text=True,
        check=False,
    )
    if checkout.returncode != 0:
        raise SourceLocatorError(
            f"git checkout {ref} failed in {dest}: "
            f"{checkout.stderr.strip() or 'unknown error'}"
        )
