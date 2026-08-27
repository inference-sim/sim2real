"""Tests for the pre-commit secret-scan scaffolding (issue #822).

Covers the acceptance criteria:
  - /sim2real-bootstrap scaffolds a pre-commit secret scan (both modes)
  - scaffolding survives `git clone` (committed .pre-commit-config.yaml +
    .secrets.baseline, not bare .git/hooks/)
  - a staged file with an hf_-style token / token:/password:/apiKey: value is
    rejected (behavioral, guarded on detect-secrets being installed)
  - legit *_tokens tuning fields (max_num_batched_tokens, --prompt-tokens) do
    NOT trip the scan
  - the shipped baseline doesn't pre-whitelist anything (empty results)
  - create-if-missing: re-running never clobbers operator edits / baseline

And for issue #865 (the bundle's own content hashes must not block commits):
  - the scaffolded hook PASSES on a file of translation_hash / *_sha256 /
    package_manifest_short / "hash" fields and a transfer.yaml-style `ref:` sha
  - it still BLOCKS hf_token / aws_secret_access_key / password /
    refresh_token / hashicorp_vault_token sitting in that same file — the
    exclusion must not become a blanket bypass
  - --exclude-lines is load-bearing (the hook ignores the baseline's own
    exclude.lines) and the two copies of the regex agree
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

_SKILL_DIR = Path(__file__).resolve().parents[1]
_REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(_SKILL_DIR))
sys.path.insert(0, str(_REPO_ROOT))
import scaffold_precommit  # noqa: E402
import byo  # noqa: E402


# ---------------------------------------------------------------------------
# scaffold_precommit() unit behavior
# ---------------------------------------------------------------------------

def test_scaffolds_both_files_into_empty_repo(tmp_path: Path):
    created = scaffold_precommit.scaffold_precommit(_SKILL_DIR, tmp_path)
    assert created == [".pre-commit-config.yaml", ".secrets.baseline"]
    assert (tmp_path / ".pre-commit-config.yaml").is_file()
    assert (tmp_path / ".secrets.baseline").is_file()


def test_config_is_whole_repo_detect_secrets_hook(tmp_path: Path):
    scaffold_precommit.scaffold_precommit(_SKILL_DIR, tmp_path)
    doc = yaml.safe_load((tmp_path / ".pre-commit-config.yaml").read_text())
    repos = {r["repo"] for r in doc["repos"]}
    assert "https://github.com/ibm/detect-secrets" in repos
    ds = next(r for r in doc["repos"]
              if r["repo"] == "https://github.com/ibm/detect-secrets")
    hook = ds["hooks"][0]
    assert hook["id"] == "detect-secrets"
    assert "--use-all-plugins" in hook["args"]
    assert hook["args"][hook["args"].index("--baseline") + 1] == ".secrets.baseline"
    # Whole-repo scan: the hook must NOT restrict to a subdirectory via a
    # narrow `files:` include. Only the baseline itself is excluded.
    assert "files" not in hook
    assert hook["exclude"] == r"^\.secrets\.baseline$"


def test_baseline_is_valid_json_with_empty_results(tmp_path: Path):
    scaffold_precommit.scaffold_precommit(_SKILL_DIR, tmp_path)
    baseline = json.loads((tmp_path / ".secrets.baseline").read_text())
    # Empty results => nothing pre-whitelisted (the trap this avoids).
    assert baseline["results"] == {}
    assert baseline["plugins_used"]  # detectors are configured
    # Baseline version must match the rev the config pins detect-secrets to.
    cfg = yaml.safe_load((tmp_path / ".pre-commit-config.yaml").read_text())
    ds = next(r for r in cfg["repos"]
              if r["repo"] == "https://github.com/ibm/detect-secrets")
    assert baseline["version"] == ds["rev"]


def test_baseline_tunes_base64_limit_below_default(tmp_path: Path):
    """base64_limit is lowered to 4.25 so an HF-token-shaped value (Shannon
    entropy ~4.39, under the 4.5 default) is caught. Locked by a test so a
    future baseline regeneration doesn't silently reset it to 4.5."""
    scaffold_precommit.scaffold_precommit(_SKILL_DIR, tmp_path)
    baseline = json.loads((tmp_path / ".secrets.baseline").read_text())
    b64 = next(p for p in baseline["plugins_used"]
               if p["name"] == "Base64HighEntropyString")
    assert b64["base64_limit"] == 4.25


def test_create_if_missing_never_clobbers(tmp_path: Path):
    (tmp_path / ".pre-commit-config.yaml").write_text("# operator edited\n")
    created = scaffold_precommit.scaffold_precommit(_SKILL_DIR, tmp_path)
    # Existing config left intact; only the missing baseline is created.
    assert created == [".secrets.baseline"]
    assert (tmp_path / ".pre-commit-config.yaml").read_text() == "# operator edited\n"


def test_idempotent_second_run_creates_nothing(tmp_path: Path):
    scaffold_precommit.scaffold_precommit(_SKILL_DIR, tmp_path)
    assert scaffold_precommit.scaffold_precommit(_SKILL_DIR, tmp_path) == []


def test_missing_template_dir_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        scaffold_precommit.scaffold_precommit(tmp_path, tmp_path)  # no templates/


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def test_cli_scaffolds_and_reports(tmp_path: Path, capsys):
    rc = scaffold_precommit.main(["--experiment-root", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "scaffolded pre-commit secret scan" in out
    assert (tmp_path / ".secrets.baseline").is_file()


def test_cli_rejects_nonexistent_root(tmp_path: Path, capsys):
    rc = scaffold_precommit.main(["--experiment-root", str(tmp_path / "nope")])
    assert rc == 2


# ---------------------------------------------------------------------------
# --byo mode wires the scaffolder in
# ---------------------------------------------------------------------------

def _write(p: Path, text: str) -> Path:
    p.write_text(textwrap.dedent(text))
    return p


def test_byo_run_scaffolds_precommit(tmp_path: Path):
    exp = tmp_path / "exp"
    exp.mkdir()
    (exp / "workloads").mkdir()
    _write(exp / "workloads" / "w.yaml", "name: w\n")
    _write(exp / "baseline.yaml", """\
        scenario:
        - name: baseline
          model: m
    """)
    _write(exp / "cfg.yaml", "plugins: []\n")
    argv = [
        "--byo",
        "--baseline", str(exp / "baseline.yaml"),
        "--algorithm", "foo",
        "--algorithm-image", "foo=ghcr.io/x/foo:v1",
        "--algorithm-config", f"foo={exp / 'cfg.yaml'}",
        "--non-interactive",
    ]
    rc, _ = byo.run_byo(argv, exp, _SKILL_DIR, stdin_isatty=False)
    assert rc == 0
    assert (exp / ".pre-commit-config.yaml").is_file()
    assert (exp / ".secrets.baseline").is_file()


# ---------------------------------------------------------------------------
# Behavioral: the SHIPPED config actually blocks/allows the right commits.
# Runs the real detect-secrets-hook (what pre-commit invokes) with the
# scaffolded .secrets.baseline inside a throwaway git repo, so it exercises
# the tuned base64_limit through the true hook path — not a plain `scan`,
# which at the default 4.5 limit would miss the plaintext HF token.
# Guarded on detect-secrets-hook being installed so CI without it still passes.
# ---------------------------------------------------------------------------

_HOOK = shutil.which("detect-secrets-hook")

# Synthetic, deterministically-generated high-entropy values — NOT real
# credentials and with no provider-recognized prefix (so they don't trip
# GitHub push protection). Entropies chosen to bracket the tuned limit:
#   PLAINTEXT_SECRET  H=4.463  -> caught at 4.25, MISSED at the 4.5 default
#   BASE64_SECRET     H=4.536  -> caught at both (mirrors the real base64 twin)
# These stand in for the issue #819 token (H=4.391) and its tokenBase64 twin
# (H=4.546); a real HF token can't live in this repo — it would be blocked by
# the very scan this feature scaffolds.
PLAINTEXT_SECRET = "HcpcpTxQOChfWjCam6fYn9gPmTGdTGPBHwsTT"
BASE64_SECRET = "Sq5ydDKTYH5FYbhwh7JS6JI8HJbKHTT5vM2KHGrw15DxDHMw=="


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(repo), check=True,
                   capture_output=True, text=True)


def _shipped_hook_args(exp_root: Path) -> list[str]:
    """The args the scaffolded .pre-commit-config.yaml passes to the hook.

    Read from the config instead of hardcoded so every behavioral test below
    exercises the shipped invocation. A hardcoded list would silently omit
    --exclude-lines (#865) and test an invocation no operator ever runs.
    """
    cfg = yaml.safe_load((exp_root / ".pre-commit-config.yaml").read_text())
    ds = next(r for r in cfg["repos"]
              if r["repo"] == "https://github.com/ibm/detect-secrets")
    return list(ds["hooks"][0]["args"])


def _run_hook(tmp_path: Path, files: dict[str, str], *,
              base64_limit: float | None = None,
              drop_args: tuple[str, ...] = ()) -> subprocess.CompletedProcess:
    """Scaffold into a throwaway git repo, stage `files`, run the real hook.

    `files` maps repo-relative path -> content. The hook runs with the args the
    scaffolded config ships, mirroring exactly how pre-commit invokes it.
    `base64_limit` overrides the scaffolded baseline's limit (to prove the
    shipped 4.25 tuning is load-bearing vs the 4.5 default); `drop_args`
    removes a shipped flag and its value (to prove --exclude-lines is
    load-bearing rather than redundant with the baseline's own exclude.lines).
    """
    tmp_path.mkdir(parents=True, exist_ok=True)  # callers may pass a subdir
    scaffold_precommit.scaffold_precommit(_SKILL_DIR, tmp_path)
    if base64_limit is not None:
        bl_path = tmp_path / ".secrets.baseline"
        bl = json.loads(bl_path.read_text())
        for p in bl["plugins_used"]:
            if p["name"] == "Base64HighEntropyString":
                p["base64_limit"] = base64_limit
        bl_path.write_text(json.dumps(bl))
    args = _shipped_hook_args(tmp_path)
    for flag in drop_args:
        i = args.index(flag)
        del args[i:i + 2]  # the flag and its value
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    for rel, content in files.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    _git(tmp_path, "add", "-A")
    return subprocess.run(
        [_HOOK, *args, *files],
        cwd=str(tmp_path), capture_output=True, text=True, timeout=60,
    )


def _hook_blocks(tmp_path: Path, content: str, base64_limit: float | None = None) -> bool:
    """Single-file convenience wrapper over _run_hook().

    Returns True if the hook blocks the commit (non-zero exit) — i.e. it found
    a secret.
    """
    return _run_hook(tmp_path, {"plans/config.yaml": content},
                     base64_limit=base64_limit).returncode != 0


def _hook_report(proc: subprocess.CompletedProcess) -> str:
    """The hook's findings report. detect-secrets-hook writes it to stderr;
    both streams are joined so a future version moving it doesn't blind us."""
    return f"{proc.stderr}\n{proc.stdout}"


def _flagged_lines(proc: subprocess.CompletedProcess, relpath: str) -> set[int]:
    """Line numbers the hook reported for `relpath`, parsed from its report."""
    return {
        int(m.group(1)) for m in re.finditer(
            rf"^Location:\s+{re.escape(relpath)}:(\d+)$",
            _hook_report(proc), re.MULTILINE)
    }


@pytest.mark.skipif(_HOOK is None, reason="detect-secrets-hook not installed")
def test_hook_blocks_high_entropy_token_value(tmp_path: Path):
    """A token value below the 4.5 default but above 4.25 is blocked by the
    shipped baseline — the #819 miss (plaintext token) closed."""
    assert _hook_blocks(tmp_path, f"huggingface:\n  token: {PLAINTEXT_SECRET}\n")


@pytest.mark.skipif(_HOOK is None, reason="detect-secrets-hook not installed")
def test_hook_blocks_tokenbase64_field(tmp_path: Path):
    """The base64-encoded twin is blocked too."""
    assert _hook_blocks(
        tmp_path, f"huggingface:\n  tokenBase64: {BASE64_SECRET}\n")


@pytest.mark.skipif(_HOOK is None, reason="detect-secrets-hook not installed")
def test_tuned_limit_is_load_bearing(tmp_path: Path):
    """The same value the shipped 4.25 catches would slip at the 4.5 default —
    guards against a future baseline regeneration resetting the limit."""
    content = f"huggingface:\n  token: {PLAINTEXT_SECRET}\n"
    assert _hook_blocks(tmp_path, content, base64_limit=4.25)
    assert not _hook_blocks(tmp_path, content, base64_limit=4.5)


@pytest.mark.skipif(_HOOK is None, reason="detect-secrets-hook not installed")
def test_hook_allows_token_tuning_fields(tmp_path: Path):
    """Legitimate benchmark tuning fields — numeric values, must NOT block."""
    assert not _hook_blocks(
        tmp_path,
        "vllm:\n"
        "  max_num_batched_tokens: 2048\n"
        "  max_model_len: 4096\n"
        "args:\n"
        "  - --prompt-tokens=512\n"
        "  - --max-num-batched-tokens=2048\n",
    )


# ---------------------------------------------------------------------------
# Issue #865: the bundle's own content hashes must not block every commit.
# SHA-256 hex has Shannon entropy ~3.9, above HexHighEntropyString's hex_limit
# of 3, so before the field-name line exclusion every hash the pipeline wrote
# tripped the detector — and neither printed mitigation applies (JSON has no
# comments for `pragma: allowlist secret`, and the baseline keys on values that
# change every translation). The exclusion must drop those lines WITHOUT
# becoming a blanket bypass, which is what the pairing below pins down.
# ---------------------------------------------------------------------------

# Synthetic 64-hex values standing in for real content hashes — high entropy,
# so they DO trip HexHighEntropyString when the exclusion is absent, which is
# what makes test_exclude_lines_is_load_bearing meaningful.
_HASH_FIELDS = {
    "translation_hash": "9f2b7c1de4a58306bf1029384756abcdef0123456789abcdef0123456789abcd",
    "source_sha256": "a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90",
    "package_manifest_sha256": "0f1e2d3c4b5a69788796a5b4c3d2e1f00f1e2d3c4b5a69788796a5b4c3d2e1f0",
    "runner_go_sha256": "beefcafe0123456789abcdef0123456789abcdef0123456789abcdef01234567",
    # 7-char short form; below the detector's length floor even unexcluded, so
    # it is here to pin the field name, not the entropy.
    "package_manifest_short": "beefcaf",
    # sim_results/ sample manifests use the bare key.
    "hash": "cafebabe0123456789abcdef0123456789abcdef0123456789abcdef0123beef",
}

# Real secret shapes that must STILL be caught in the same file. The last two
# are the "ending in" anchor's job: `refresh_token` and `hashicorp_vault_token`
# merely CONTAIN ref/hash, so loosening that anchor would silently let a
# credential through and these two would fail.
_SECRET_FIELDS = {
    "hf_token": PLAINTEXT_SECRET,
    "aws_secret_access_key": BASE64_SECRET,
    "password": "correct-horse-battery-staple-nine",
    "refresh_token": "d34db33f0123456789abcdef0123456789abcdef0123456789abcdef01234567",
    "hashicorp_vault_token": "f00dbabe0123456789abcdef0123456789abcdef0123456789abcdef01234567",
}

# transfer.yaml pins the component submodule to a bare git sha under `ref:`.
# `ref_branch` is a non-sha value under a ref-ish key: it stays in scope (the
# exclusion only fires on a bare 7-64 hex value), and is here so a future
# loosening to any `ref:` value shows up as a diff on this fixture.
_TRANSFER_YAML = (
    "component:\n"
    "  repo: https://github.com/llm-d/llm-d-router\n"
    "  ref: 0123456789abcdef0123456789abcdef01234567\n"
    "  ref_branch: refs/heads/main\n"
)


def _canary(include_secrets: bool) -> tuple[str, set[int], set[int]]:
    """A translation_output.json-shaped canary.

    Returns (text, hash_field_lines, secret_field_lines) so assertions key on
    derived line numbers instead of hand-counted ones.
    """
    lines: list[str] = ["{"]
    hash_lines: set[int] = set()
    secret_lines: set[int] = set()
    buckets = [(_HASH_FIELDS, hash_lines)]
    if include_secrets:
        buckets.append((_SECRET_FIELDS, secret_lines))
    for fields, sink in buckets:
        for key, value in fields.items():
            lines.append(f'  "{key}": "{value}",')
            sink.add(len(lines))  # 1-indexed: len() after append is the lineno
    lines[-1] = lines[-1].rstrip(",")
    lines.append("}")
    return "\n".join(lines) + "\n", hash_lines, secret_lines


@pytest.mark.skipif(_HOOK is None, reason="detect-secrets-hook not installed")
def test_hook_allows_bundle_content_hashes(tmp_path: Path):
    """The scaffolded hook passes on a bundle's own content hashes — the #865
    symptom (every first real commit blocked) closed."""
    content, _, _ = _canary(include_secrets=False)
    proc = _run_hook(tmp_path, {
        "workspace/translations/abc123/translation_output.json": content,
        "transfer.yaml": _TRANSFER_YAML,
    })
    assert proc.returncode == 0, _hook_report(proc)


@pytest.mark.skipif(_HOOK is None, reason="detect-secrets-hook not installed")
def test_hook_blocks_real_secrets_beside_content_hashes(tmp_path: Path):
    """The exclusion is not a blanket bypass: in a file whose hash lines are
    skipped, every real secret shape is still caught — and only those."""
    rel = "workspace/translations/abc123/translation_output.json"
    content, hash_lines, secret_lines = _canary(include_secrets=True)
    proc = _run_hook(tmp_path, {rel: content})
    assert proc.returncode != 0
    flagged = _flagged_lines(proc, rel)
    assert flagged == secret_lines, (
        f"flagged={sorted(flagged)} expected={sorted(secret_lines)} "
        f"(hash lines {sorted(hash_lines)} must stay skipped)\n{_hook_report(proc)}"
    )


@pytest.mark.skipif(_HOOK is None, reason="detect-secrets-hook not installed")
def test_exclude_lines_is_load_bearing(tmp_path: Path):
    """Dropping --exclude-lines restores every false positive.

    The hook does NOT honor the baseline's own `exclude.lines`, so the CLI arg
    is what actually takes effect — this guards against a future "simplify"
    that deletes the arg on the assumption the baseline covers it.
    """
    content, _, _ = _canary(include_secrets=False)
    files = {"workspace/translations/abc123/translation_output.json": content}
    assert _run_hook(tmp_path / "with", files).returncode == 0
    assert _run_hook(tmp_path / "without", files,
                     drop_args=("--exclude-lines",)).returncode != 0


def test_exclude_lines_agrees_between_config_and_baseline(tmp_path: Path):
    """Config arg and baseline `exclude.lines` must be byte-identical.

    They drift silently otherwise: a later `detect-secrets scan --baseline
    .secrets.baseline` regeneration would re-find exactly what the hook skips.
    Unguarded so CI without detect-secrets still catches a removal.
    """
    scaffold_precommit.scaffold_precommit(_SKILL_DIR, tmp_path)
    args = _shipped_hook_args(tmp_path)
    assert "--exclude-lines" in args
    baseline = json.loads((tmp_path / ".secrets.baseline").read_text())
    assert baseline["exclude"]["lines"] == args[args.index("--exclude-lines") + 1]
