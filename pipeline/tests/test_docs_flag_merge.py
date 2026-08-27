"""Guard that docs do not describe `vllm.additionalFlags` as replace-semantics.

Since issue #851 that list merges by flag name (`_merge_lists` Tier 0). Two files
previously told authors to avoid it *because* it was replaced wholesale; both were
corrected. If that rationale creeps back — in a doc edit, or a new fragment header
copied from the old text — this test fails.

The check is deliberately paragraph-scoped rather than file-scoped: a file may
legitimately discuss replace semantics for OTHER scalar lists (`router.proxy.args`),
so only a paragraph that mentions `additionalFlags` and "replace" in the same breath
without citing #851 is treated as stale.
"""
import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]

_FILES = (
    "docs/troubleshooting.md",
    ".claude/skills/sim2real-bootstrap/templates/defaults/vllm-logging.yaml",
)


def test_files_under_guard_exist():
    """A renamed or deleted file must not silently pass the guard below."""
    missing = [rel for rel in _FILES if not (_REPO / rel).exists()]
    assert not missing, (
        f"guarded doc(s) no longer exist: {missing} — update _FILES in this test "
        "to follow the rename, or drop the entry if the doc is gone"
    )


def test_flag_merged_paths_not_described_as_replaced():
    offenders = []
    for rel in _FILES:
        text = (_REPO / rel).read_text()
        for para in re.split(r"\n\s*\n", text):
            if (
                "additionalFlags" in para
                # Case-insensitive: the original vllm-logging.yaml header said
                # "REPLACE" in caps for emphasis, which a case-sensitive pattern
                # silently missed.
                and re.search(r"\breplace(s|d)?\b", para, re.IGNORECASE)
                and "#851" not in para
            ):
                offenders.append(f"{rel}: {para.strip()[:160]}")
    assert not offenders, (
        "these passages tie additionalFlags to replace semantics without "
        f"acknowledging #851: {offenders}"
    )
