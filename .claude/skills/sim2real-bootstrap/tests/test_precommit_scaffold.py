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
from collections.abc import Callable
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

def test_scaffolds_all_files_into_empty_repo(tmp_path: Path):
    created = scaffold_precommit.scaffold_precommit(_SKILL_DIR, tmp_path)
    assert created == [".pre-commit-config.yaml", ".secrets.baseline",
                       ".secrets.wordlist"]
    assert (tmp_path / ".pre-commit-config.yaml").is_file()
    assert (tmp_path / ".secrets.baseline").is_file()
    assert (tmp_path / ".secrets.wordlist").is_file()


def test_wordlist_is_comment_free(tmp_path: Path):
    """`build_automaton` (detect_secrets/util.py) adds every line longer than 3
    characters as a suppression word and has no comment syntax, so a `#` line
    would be loaded as a word — silently suppressing any candidate containing
    it. The explanation lives in .pre-commit-config.yaml instead (#896)."""
    scaffold_precommit.scaffold_precommit(_SKILL_DIR, tmp_path)
    lines = [ln for ln in
             (tmp_path / ".secrets.wordlist").read_text().splitlines()
             if ln.strip()]
    assert lines, "wordlist must not be empty"
    assert not [ln for ln in lines if ln.lstrip().startswith("#")]
    # Every shipped fragment must clear build_automaton's len > 3 gate, or it
    # is silently ignored rather than rejected.
    assert all(len(ln.strip()) > 3 for ln in lines)


def test_wordlist_keys_on_role_segments_not_the_model_prefix(tmp_path: Path):
    """The deployment-name prefix is `model.shortName` truncated plus a hash,
    derived per bundle — the scaffold cannot know it. Keying on the role
    segments llm-d-modelservice appends keeps the list model-agnostic, so the
    same three fragments work for every bundle (#896)."""
    scaffold_precommit.scaffold_precommit(_SKILL_DIR, tmp_path)
    words = {ln.strip() for ln in
             (tmp_path / ".secrets.wordlist").read_text().splitlines()
             if ln.strip()}
    assert words == {"-decode-", "-prefill-", "-router-epp-"}


def test_hook_declares_pyahocorasick_dependency(tmp_path: Path):
    """--word-list imports pyahocorasick lazily; without it the hook dies with
    ModuleNotFoundError rather than degrading to no suppression. Unguarded so
    CI without detect-secrets still catches a removal."""
    scaffold_precommit.scaffold_precommit(_SKILL_DIR, tmp_path)
    cfg = yaml.safe_load((tmp_path / ".pre-commit-config.yaml").read_text())
    ds = next(r for r in cfg["repos"]
              if r["repo"] == "https://github.com/ibm/detect-secrets")
    hook = ds["hooks"][0]
    assert "--word-list" in hook["args"]
    assert hook["args"][hook["args"].index("--word-list") + 1] == ".secrets.wordlist"
    assert "pyahocorasick" in hook["additional_dependencies"]


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
    # narrow `files:` include. Only the baseline and the collected-telemetry
    # trees are excluded (#896) — everything a human or template authored,
    # including resources/ manifests, stays in scope.
    assert "files" not in hook
    assert hook["exclude"] == (
        r"^(\.secrets\.baseline$"
        r"|workspace/runs/.*/(epp_logs|gpu_logs|metrics|server_logs)/)")


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
    # Existing config left intact; only the missing files are created.
    assert created == [".secrets.baseline", ".secrets.wordlist"]
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
              drop_args: tuple[str, ...] = (),
              mutate: Callable[[Path], None] | None = None,
              ) -> subprocess.CompletedProcess:
    """Scaffold into a throwaway git repo, stage `files`, run the real hook.

    `files` maps repo-relative path -> content. The hook runs with the args the
    scaffolded config ships, mirroring exactly how pre-commit invokes it.
    `base64_limit` overrides the scaffolded baseline's limit (to prove the
    shipped 4.25 tuning is load-bearing vs the 4.5 default); `drop_args`
    removes a shipped flag and its value (to prove --exclude-lines is
    load-bearing rather than redundant with the baseline's own exclude.lines);
    `mutate` is called with the scaffolded root for edits that have no
    corresponding flag, such as removing the telemetry `exclude.files` regex
    the hook honors (#896).
    """
    tmp_path.mkdir(parents=True, exist_ok=True)  # callers may pass a subdir
    scaffold_precommit.scaffold_precommit(_SKILL_DIR, tmp_path)
    if mutate is not None:
        mutate(tmp_path)
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


# ---------------------------------------------------------------------------
# Kubernetes object names (issue #896).
#
# Pod and ReplicaSet names are high-entropy by construction, and the two
# mechanisms that handle them sit at DIFFERENT LAYERS — which is why the tests
# below are shaped differently from each other:
#
#   `exclude:`    a pre-commit KEY, applied to the file list before the hook
#                 runs — so it is NOT a hook argument and cannot be tested by
#                 dropping an arg. Two things enforce it, and both are tested:
#                 pre-commit's own filtering (mirrored by `_precommit_filter`)
#                 and, verified empirically here, the baseline's `exclude.files`
#                 — which detect-secrets-hook DOES honor even when handed an
#                 explicit path. That is why the two must stay in sync.
#   --word-list   a hook ARGUMENT, so it IS testable in the drop-an-arg style
#                 used for --exclude-lines above.
#
# One trap these tests are shaped around: the word list suppresses role-named
# pods EVERYWHERE, telemetry included. So a telemetry test whose fixture is a
# role-named pod passes for the wrong reason — the word list, not the
# exclusion. The load-bearing test below therefore uses a high-entropy
# telemetry value the word list does not cover.
# ---------------------------------------------------------------------------

# A real pod name from a collected run. Shannon entropy 4.447 over the
# Base64HighEntropyString charset (which includes `-` and `_` for url-safe
# base64), ABOVE the 4.391 HF token the 4.25 limit exists to catch — so no
# entropy limit can separate the two. Not a credential.
POD_NAME = "meta-lla-888e33f1-instruct-decode-558d9f5fd4-x97w2-rank-0"

# The two shapes that carry pod names in bulk in collected telemetry.
EPP_LOG_LINE = (
    '{"level":"debug","caller":"scheduling/scheduler_profile.go:141",'
    f'"Name":"{POD_NAME}"}}\n'
)
PROM_SAMPLE_LINE = (
    "llm_d_epp_per_endpoint_queue_size{model_server_endpoint="
    f'"{POD_NAME}"}} 0\n'
)

# A high-entropy telemetry value that is NOT a role-named pod, so the word list
# does not cover it and the telemetry exclusion is the only thing suppressing
# it. Shannon entropy 5.000, well over the 4.25 limit. Synthetic, not a
# credential and not a real request id — its job is to be a value in telemetry
# that the scanner would flag.
TELEMETRY_HIGH_ENTROPY = "aQ8xR2mV5nK7pL3wY6tB9jH4cF1sD0gZ"
TELEMETRY_REQ_LINE = f'{{"level":"info","request_id":"{TELEMETRY_HIGH_ENTROPY}"}}\n'


def _baseline_without_telemetry_exclusion(exp_root: Path) -> None:
    """Rewrite the scaffolded baseline's `exclude.files` back to baseline-only.

    detect-secrets-hook honors the baseline's `exclude.files` even when handed
    an explicit path (verified empirically), so this is how the "exclusion
    removed" arm of a behavioral test is produced — there is no arg to drop.
    """
    p = exp_root / ".secrets.baseline"
    b = json.loads(p.read_text())
    b["exclude"]["files"] = r"^\.secrets\.baseline$"
    p.write_text(json.dumps(b, indent=2))


def _precommit_filter(exp_root: Path, paths: list[str]) -> list[str]:
    """The subset of `paths` pre-commit would pass to the hook.

    Mirrors pre-commit's own behavior: it drops paths matching the config's
    `exclude` regex before invoking the hook. Reading the regex from the
    scaffolded config (rather than hardcoding it) is what makes the tests below
    exercise the shipped value.
    """
    cfg = yaml.safe_load((exp_root / ".pre-commit-config.yaml").read_text())
    ds = next(r for r in cfg["repos"]
              if r["repo"] == "https://github.com/ibm/detect-secrets")
    rx = re.compile(ds["hooks"][0]["exclude"])
    return [p for p in paths if not rx.search(p)]


@pytest.mark.parametrize("relpath", [
    "workspace/runs/try8/results/baseline/wl-a/i1/epp_logs/epp.log",
    "workspace/runs/try8/results/baseline/wl-a/i1/metrics/raw/p_1_metrics.log",
])
@pytest.mark.skipif(_HOOK is None, reason="detect-secrets-hook not installed")
def test_telemetry_exclusion_is_load_bearing(tmp_path: Path, relpath):
    """A high-entropy telemetry value produces no finding under the shipped
    config, and a finding once the exclusion is removed.

    The fixture is deliberately NOT a role-named pod: the word list suppresses
    those everywhere, telemetry included, so such a test would pass because of
    the word list and keep passing with the exclusion deleted.
    """
    files = {relpath: TELEMETRY_REQ_LINE}
    assert _run_hook(tmp_path / "with", files).returncode == 0, (
        "shipped config should not flag telemetry")
    assert _run_hook(tmp_path / "without", files,
                     mutate=_baseline_without_telemetry_exclusion,
                     ).returncode != 0, (
        "expected a finding with the telemetry exclusion removed — if this "
        "passes, the fixture no longer trips the detector and the test is "
        "vacuous")


@pytest.mark.parametrize("relpath,content", [
    ("workspace/runs/try8/results/baseline/wl-a/i1/epp_logs/epp.log",
     EPP_LOG_LINE),
    ("workspace/runs/try8/results/baseline/wl-a/i1/metrics/raw/p_1_metrics.log",
     PROM_SAMPLE_LINE),
])
def test_precommit_filter_drops_the_pod_name_telemetry_shapes(
        tmp_path: Path, relpath, content):
    """The two shapes from the issue — a zap JSON value and a Prometheus label
    value — are dropped by the shipped `exclude` before the hook sees them.

    Asserted at the filter level rather than behaviorally, because these
    fixtures ARE role-named pods and the word list would suppress them anyway;
    what this pins is that the paths never reach the scanner. Unguarded, so it
    runs in CI even without detect-secrets installed."""
    scaffold_precommit.scaffold_precommit(_SKILL_DIR, tmp_path)
    assert content  # fixture is the shape under discussion, kept for the record
    assert _precommit_filter(tmp_path, [relpath]) == []


@pytest.mark.skipif(_HOOK is None, reason="detect-secrets-hook not installed")
def test_telemetry_exclusion_leaves_no_scan_layer(tmp_path: Path):
    """Pins the residual gap the exclusion accepts, so it cannot be forgotten.

    These trees are not redacted at collect time either (redact_yaml_tree walks
    resources/ only), so a credential in telemetry now has NO scanning layer.
    The same value in an in-scope path is still blocked — that contrast is the
    cost of the exclusion, stated as a test rather than only as a comment.

    If a future change narrows the exclusion or extends the redactor to logs,
    this test SHOULD fail and be updated — that is the point of it.
    """
    telemetry = "workspace/runs/try8/results/baseline/wl-a/i1/epp_logs/epp.log"
    in_scope = "workspace/runs/try8/results/baseline/wl-a/i1/resources/pod.yaml"
    secret_line = f'{{"msg":"auth","token":"{PLAINTEXT_SECRET}"}}\n'
    assert _run_hook(tmp_path / "telemetry",
                     {telemetry: secret_line}).returncode == 0
    assert _run_hook(tmp_path / "inscope",
                     {in_scope: f"token: {PLAINTEXT_SECRET}\n"}).returncode != 0


def test_telemetry_exclusion_keeps_authored_files_in_scope(tmp_path: Path):
    """The exclusion must be surgical. `resources/` manifests are where the
    credential risk actually is (env, secretKeyRef, ConfigMaps), and the
    authored bundle config must stay scanned too. Unguarded so CI without
    detect-secrets still catches an over-broad regex."""
    scaffold_precommit.scaffold_precommit(_SKILL_DIR, tmp_path)
    run = "workspace/runs/try8/results/baseline/wl-a/i1"
    excluded = [
        f"{run}/epp_logs/epp.log",
        f"{run}/gpu_logs/node-a.log",
        f"{run}/metrics/raw/p_1_metrics.log",
        f"{run}/metrics/processed/metrics_summary.json",
        f"{run}/server_logs/vllm.log",
        ".secrets.baseline",
    ]
    in_scope = [
        f"{run}/resources/pod-decode-0.yaml",
        f"{run}/trace_header.yaml",
        "workspace/runs/try8/manifest.assembly.yaml",
        "workspace/runs/try8/run_metadata.json",
        "workspace/runs/try8/cluster/baseline.yaml",
        "transfer.yaml",
        "config.md",
        "baselines/b1/baseline_config.yaml",
        # Anchored to workspace/runs/, so a same-named dir elsewhere is scanned.
        "metrics/elsewhere/foo.log",
        "docs/server_logs/notes.md",
    ]
    assert _precommit_filter(tmp_path, excluded) == []
    assert _precommit_filter(tmp_path, in_scope) == in_scope


def test_exclude_files_agrees_between_config_and_baseline(tmp_path: Path):
    """Config `exclude` and baseline `exclude.files` must be byte-identical,
    for the same reason as the `exclude.lines` pair above: a later
    `detect-secrets scan --baseline .secrets.baseline` regeneration would
    otherwise re-find in the telemetry trees exactly what the hook skips.
    Unguarded so CI without detect-secrets still catches drift."""
    scaffold_precommit.scaffold_precommit(_SKILL_DIR, tmp_path)
    cfg = yaml.safe_load((tmp_path / ".pre-commit-config.yaml").read_text())
    ds = next(r for r in cfg["repos"]
              if r["repo"] == "https://github.com/ibm/detect-secrets")
    baseline = json.loads((tmp_path / ".secrets.baseline").read_text())
    assert baseline["exclude"]["files"] == ds["hooks"][0]["exclude"]


@pytest.mark.skipif(_HOOK is None, reason="detect-secrets-hook not installed")
def test_word_list_suppresses_pod_names_in_resource_manifests(tmp_path: Path):
    """A role-named pod in `metadata.name` produces no finding with the word
    list and a finding without it (#896).

    `resources/` deliberately stays in scope, so the word list is the only
    mechanism left: detect-secrets' _analyze_yaml_file path inspects parsed
    string VALUES, so the bare `name:` scalar is a candidate even unquoted.
    """
    manifest = (
        "apiVersion: v1\n"
        "kind: Pod\n"
        "metadata:\n"
        f"  name: {POD_NAME}\n"
        "spec:\n"
        "  containers: []\n"
    )
    files = {"workspace/runs/try8/results/baseline/wl-a/i1/resources/pod.yaml":
             manifest}
    assert _run_hook(tmp_path / "with", files).returncode == 0
    assert _run_hook(tmp_path / "without", files,
                     drop_args=("--word-list",)).returncode != 0


@pytest.mark.skipif(_HOOK is None, reason="detect-secrets-hook not installed")
def test_word_list_suppression_is_per_secret_not_per_file(tmp_path: Path):
    """is_found_with_aho_corasick drops a candidate that CONTAINS a listed
    word, so the rest of the manifest keeps being scanned. A real credential
    sitting beside a suppressed pod name must still block — this is what
    separates the word list from excluding `resources/` wholesale.

    The credential is placed in a nested MAPPING, not in the k8s
    `env: [{name, value}]` list shape. detect-secrets' YAML analyzer reads
    values in mappings at any depth but misses every value inside a list unless
    it is explicitly quoted, so a list-shaped fixture would make this test pass
    vacuously — nothing would be found either way. That gap is pre-existing and
    independent of the word list; it is not what this test is about.
    """
    manifest = (
        "apiVersion: v1\n"
        "kind: Pod\n"
        "metadata:\n"
        f"  name: {POD_NAME}\n"
        "stringData:\n"
        f"  hfToken: {PLAINTEXT_SECRET}\n"
    )
    files = {"workspace/runs/try8/results/baseline/wl-a/i1/resources/pod.yaml":
             manifest}
    proc = _run_hook(tmp_path / "with", files)
    assert proc.returncode != 0, (
        "a credential beside a suppressed pod name must still block")
    # And the pod name itself is not what was reported.
    assert POD_NAME not in _hook_report(proc)
