"""Scaffold a pre-commit secret scan into an experiment repo (issue #822).

Second layer behind the collect-time redactor (``pipeline/lib/redact.py``,
issue #819): the redactor guards what the pipeline *writes*; this hook guards
what is about to be *committed*. Experiment repos track their ``workspace/``
tree, so a credential the redactor misses (a new key name, a hand-copied
results tree, a stray ``.env`` / kubeconfig) would otherwise land in public
git history the moment it is committed.

Writes two files into the experiment root, **create-if-missing** (like the
skill's Task 0 ``.gitignore``): an existing file is never overwritten, so a
re-run over an already-bootstrapped repo only fills gaps and never clobbers
operator edits or an audited baseline.

  templates/precommit/pre-commit-config.yaml -> <exp-root>/.pre-commit-config.yaml
  templates/precommit/secrets.baseline       -> <exp-root>/.secrets.baseline

Used by both bootstrap modes: BLIS mode invokes ``main()`` via SKILL.md Task
0b (``action: shell``); ``--byo`` mode calls ``scaffold_precommit()`` from
``byo.run_byo``. The shipped baseline has empty ``results`` — nothing is
whitelisted, so it never suppresses a real hit.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Reuse the atomic writer from byo.py rather than duplicating it. No import
# cycle: byo imports this module only inside run_byo (function scope), so
# importing byo here at module load is safe in both directions.
from byo import _atomic_copy_file

# (template filename under templates/precommit/, destination name in exp root)
_SCAFFOLD_FILES: tuple[tuple[str, str], ...] = (
    ("pre-commit-config.yaml", ".pre-commit-config.yaml"),
    ("secrets.baseline", ".secrets.baseline"),
)


def scaffold_precommit(skill_dir: Path, exp_root: Path) -> list[str]:
    """Write the pre-commit scan files into ``exp_root``, create-if-missing.

    Returns the sorted list of destination names actually created (files that
    already existed are skipped, not overwritten). Raises ``FileNotFoundError``
    if a bundled template is missing (a packaging error, not operator input).
    """
    src_dir = skill_dir / "templates" / "precommit"
    created: list[str] = []
    for template_name, dest_name in _SCAFFOLD_FILES:
        src = src_dir / template_name
        if not src.is_file():
            raise FileNotFoundError(
                f"bundled pre-commit template missing: {src} "
                "(is this skill's templates/precommit/ populated?)"
            )
        dst = exp_root / dest_name
        if dst.exists():
            continue  # create-if-missing: never clobber an existing file
        _atomic_copy_file(src, dst)
        created.append(dest_name)
    created.sort()
    return created


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(
        prog="scaffold_precommit",
        description="Scaffold a pre-commit secret scan into an experiment repo.",
    )
    parser.add_argument(
        "--experiment-root",
        default=".",
        help="Experiment repo root to scaffold into (default: cwd).",
    )
    args = parser.parse_args(argv)

    skill_dir = Path(__file__).resolve().parent
    exp_root = Path(args.experiment_root).resolve()
    if not exp_root.is_dir():
        print(f"error: experiment root is not a directory: {exp_root}",
              file=sys.stderr)
        return 2

    created = scaffold_precommit(skill_dir, exp_root)
    if created:
        print(f"scaffolded pre-commit secret scan: {', '.join(created)}")
        print("activate it with: pip install pre-commit detect-secrets && "
              "pre-commit install")
    else:
        print("pre-commit secret scan already present — nothing to do")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
