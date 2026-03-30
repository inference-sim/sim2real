# Design: deploy.py Phase Re-run Control

**Date:** 2026-03-29
**Status:** Approved

## Problem

When `benchmark_state.json` marks a phase as `done`, `deploy.py` silently skips it with an `[INFO]` message. The user has no opportunity to decide whether to re-run the phase. This is the correct default for CI, but in interactive use the operator often wants to re-run a single phase (e.g., treatment) without re-running all others.

## Solution

Add an interactive prompt and a `--force-rerun` flag.

### Behavior Matrix

| Condition | Behavior |
|---|---|
| Phase done, `--force-rerun` set | Log "re-running" and proceed |
| Phase done, interactive TTY, user enters `y` | Clear phase state and re-run |
| Phase done, interactive TTY, user enters `n` or Enter | Log "skipping" and skip |
| Phase done, non-interactive (no TTY), no flag | Silent skip (current behavior) |

### CLI Change

One new flag in `build_parser()`:

```
--force-rerun    Re-run all already-done benchmark phases without prompting
```

Updated examples in epilog:

```
python scripts/deploy.py --skip-build-epp --force-rerun   # re-run all done phases
```

### Phase Loop Change

Replace the silent-skip block in `stage_benchmarks` (currently lines 599–603):

```python
# Before
if state.get("phases", {}).get(phase, {}).get("status") == "done":
    info(f"Phase {phase} already done — skipping")
    continue

# After
if state.get("phases", {}).get(phase, {}).get("status") == "done":
    if force_rerun:
        info(f"Phase {phase} already done — re-running (--force-rerun)")
    elif sys.stdin.isatty():
        answer = input(f"  Phase {phase} already done — re-run? [y/N]: ").strip().lower()
        if answer != "y":
            info(f"Skipping {phase}")
            continue
    else:
        info(f"Phase {phase} already done — skipping (non-interactive)")
        continue
    _clear_phase_state(phase, bench_state_file)
```

### `_clear_phase_state` Helper

Reads `benchmark_state.json`, removes `status` and `results_path` from the named phase entry (preserving other keys), writes it back. Handles missing file gracefully (no-op).

```python
def _clear_phase_state(phase: str, bench_state_file: Path) -> None:
    if not bench_state_file.exists():
        return
    state = json.loads(bench_state_file.read_text())
    phase_dict = state.get("phases", {}).get(phase, {})
    phase_dict.pop("status", None)
    phase_dict.pop("results_path", None)
    bench_state_file.write_text(json.dumps(state, indent=2))
```

Only `status` and `results_path` are cleared — any other keys in the phase entry (e.g. timestamps) are preserved. The phase entry itself is not removed, so downstream readers won't see a missing key where they expect a dict.

State is cleared before the phase runs. If the phase fails mid-execution, the cleared state is intentional: the phase will simply re-run on the next `deploy.py` invocation, which is the correct resume behavior.

The noise phase is treated as a single unit — the prompt or `--force-rerun` flag applies to the entire noise phase (all noise runs), not to individual noise iterations inside `_run_noise_phase`.

### Signature Change

```python
def stage_benchmarks(run_dir: Path, namespace: str, fast_iter: bool, force_rerun: bool) -> str:
```

`main()` passes `args.force_rerun`. `--force-rerun` only applies to phases whose `status == "done"`; phases that never ran or previously failed are unaffected and run unconditionally.

## Scope

- `scripts/deploy.py` only — no changes to `transfer_cli.py`, schemas, or `prepare.py`.
- No new files.

## Non-Goals

- Per-phase targeting via CLI flags (e.g., `--force-rerun treatment`) — the interactive prompt handles this at runtime.
- Prompting for EPP build or PR stages — those already have explicit flags (`--skip-build-epp`, `--pr`).
