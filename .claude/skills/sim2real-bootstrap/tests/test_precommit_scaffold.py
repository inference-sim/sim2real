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
"""
from __future__ import annotations

import json
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


def _hook_blocks(tmp_path: Path, content: str, base64_limit: float | None = None) -> bool:
    """Scaffold the config into a git repo, stage a file, run the real hook.

    Returns True if the hook blocks the commit (non-zero exit) — i.e. it found
    a secret — mirroring exactly how pre-commit invokes detect-secrets. If
    `base64_limit` is given, the scaffolded baseline's limit is overridden
    first (used to prove the shipped 4.25 tuning is load-bearing vs 4.5).
    """
    scaffold_precommit.scaffold_precommit(_SKILL_DIR, tmp_path)
    if base64_limit is not None:
        bl_path = tmp_path / ".secrets.baseline"
        bl = json.loads(bl_path.read_text())
        for p in bl["plugins_used"]:
            if p["name"] == "Base64HighEntropyString":
                p["base64_limit"] = base64_limit
        bl_path.write_text(json.dumps(bl))
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    target = tmp_path / "plans" / "config.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    _git(tmp_path, "add", "-A")
    proc = subprocess.run(
        [_HOOK, "--baseline", ".secrets.baseline", "--use-all-plugins",
         "plans/config.yaml"],
        cwd=str(tmp_path), capture_output=True, text=True, timeout=60,
    )
    return proc.returncode != 0


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
