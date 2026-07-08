---
name: sim2real-translate
description: |
  Translates simulation-discovered algorithms into production Go plugins for the
  target component (e.g. llm-d-inference-scheduler). Reads
  workspace/translations/<hash>/skill_input.json (written by `sim2real translate`),
  spawns a three-agent team (expert + writer + reviewer) per algorithm, and
  writes plugin source + treatment overlay to
  workspace/translations/<hash>/generated/<algo>/{cmd,pkg,<algo>_config.yaml,<algo>_output.json}.
  Use after `sim2real translate` reaches the translation checkpoint; follow up
  with `sim2real translate --resume` to validate outputs.
argument-hint: "[--experiment-root PATH] [--rounds N]"
user-invocable: true
allowed-tools:
  - Agent
  - SendMessage
  - TaskCreate
  - TaskUpdate
  - TaskList
  - TaskStop
  - Bash(python3 *)
  - Bash(.venv/bin/python *)
  - Bash(cd * && *)
  - Bash(test *)
  - Bash(git *)
  - Glob
  - Read
  - Edit
  - Write
  - Grep
---

# sim2real-translate

Translate simulation algorithms into production plugins using a three-agent
team per algorithm: an expert that explores the target repo up front and
answers technical queries, a writer that owns the build/test gate and round
loop, and a reviewer that is exceptionally critical and includes
assembly-simulation checks. Runs one algorithm at a time; iterates over every
algorithm that `skill_input.json:algorithms[]` declares but has no
`<algo>_output.json` yet.

## CRITICAL: Working Directory

Run from the **experiment repo root** (where `transfer.yaml`, `algorithms/`,
and the target component checkout live) OR pass `--experiment-root PATH`. The
`sim2real/` pipeline root is discovered automatically from
`workspace/setup_config.json` (falls back to the `SIM2REAL_ROOT` env var).

Target repo commands run via subshell: `(cd $TARGET_REPO && ...)`

Before proceeding, verify the experiment root has the expected layout:
```bash
test -f "$EXPERIMENT_ROOT/transfer.yaml" || { echo "ERROR: no transfer.yaml in $EXPERIMENT_ROOT"; exit 1; }
```

## Arguments

- `--experiment-root PATH` — Path to experiment repo (default: current working directory).
- `--rounds N` — Max review rounds per algorithm (default: 3).

Initialize shell variables from skill invocation arguments:

```bash
EXPERIMENT_ROOT=$(pwd)    # overridden by --experiment-root PATH
REVIEW_ROUNDS=3           # overridden by --rounds N
```

If `--experiment-root PATH` was passed, set `EXPERIMENT_ROOT=$(realpath PATH)`.
If `--rounds N` was passed, set `REVIEW_ROUNDS=N`.

## Prerequisites

Create an overall progress task:
```
TaskCreate subject="sim2real-translate" description="Running translation skill against workspace/translations/<hash>/"
```

Locate `REPO_ROOT` (the sim2real repo root — this file's ancestor). Prefer
`workspace/setup_config.json:sim2real_root` if present; fall back to
`SIM2REAL_ROOT`:

```bash
REPO_ROOT=$(python3 - <<'PY'
import json, os
from pathlib import Path
exp = Path(os.environ.get("EXPERIMENT_ROOT", os.getcwd()))
cfg = exp / "workspace" / "setup_config.json"
root = ""
if cfg.exists():
    try:
        root = json.loads(cfg.read_text()).get("sim2real_root", "")
    except Exception:
        root = ""
if not root:
    root = os.environ.get("SIM2REAL_ROOT", "")
if not root or not Path(root).exists():
    raise SystemExit(
        "HALT: cannot determine sim2real repo root. "
        "Run `pipeline/setup.py` first, or export SIM2REAL_ROOT=/path/to/sim2real."
    )
print(root)
PY
) || exit 1
test -n "$REPO_ROOT" || exit 1
```

Load and validate `transfer.yaml`, then compute the translation hash exactly
the way `sim2real translate` does (same call site — the skill is not allowed
to drift from the pinned hash):

```bash
python3 - "$EXPERIMENT_ROOT" "$REPO_ROOT" <<'PY' > /tmp/sim2real_translate_prereq.txt
import sys
from pathlib import Path
exp = Path(sys.argv[1])
repo = Path(sys.argv[2])
sys.path.insert(0, str(repo))
from pipeline.lib import manifest as mf
from pipeline.lib import slicer as sl
manifest_path = exp / "transfer.yaml"
data = mf.load_manifest(manifest_path)
thash = sl.translation_hash_with_sources(data, exp)
print(f"TRANSLATION_HASH={thash}")
print(f"SCENARIO={data['scenario']}")
component = data.get("component", {}) or {}
print(f"TARGET_REPO_REL={component.get('path', '')}")
print(f"CONFIG_KIND={component.get('kind', '')}")
build = component.get("build") or {}
import json as _json
print("BUILD_COMMANDS_JSON=" + _json.dumps(build.get("commands", [])))
PY
[ $? -eq 0 ] || exit 1

TRANSLATION_HASH=$(grep '^TRANSLATION_HASH=' /tmp/sim2real_translate_prereq.txt | cut -d= -f2-)
SCENARIO=$(grep '^SCENARIO=' /tmp/sim2real_translate_prereq.txt | cut -d= -f2-)
TARGET_REPO_REL=$(grep '^TARGET_REPO_REL=' /tmp/sim2real_translate_prereq.txt | cut -d= -f2-)
CONFIG_KIND=$(grep '^CONFIG_KIND=' /tmp/sim2real_translate_prereq.txt | cut -d= -f2-)
BUILD_COMMANDS=$(grep '^BUILD_COMMANDS_JSON=' /tmp/sim2real_translate_prereq.txt | cut -d= -f2-)
TARGET_REPO="$EXPERIMENT_ROOT/$TARGET_REPO_REL"
TRANSLATIONS_DIR="$EXPERIMENT_ROOT/workspace/translations/$TRANSLATION_HASH"

test -n "$TRANSLATION_HASH" || { echo "HALT: empty TRANSLATION_HASH"; exit 1; }
test -n "$TARGET_REPO_REL" || { echo "HALT: transfer.yaml component.repo/path missing"; exit 1; }
test -d "$TARGET_REPO" || { echo "HALT: target repo not found at $TARGET_REPO"; exit 1; }
test -f "$TRANSLATIONS_DIR/skill_input.json" || {
    echo "HALT: $TRANSLATIONS_DIR/skill_input.json not found — run 'sim2real translate' first"
    exit 1
}
```

Load per-algorithm inputs from `skill_input.json`. Every path in the file is
interpreted relative to `experiment_root` (for `source_path`) or
`translations_dir` (for `output_dir`, `config_output_path`,
`algorithms[i].baseline_overlay_path`):

Path-safe emission: each `ALGO` row is TAB-delimited; each context file path is
written to a separate line in a dedicated file (`/tmp/sim2real_translate_ctx_paths.txt`)
so paths containing spaces are preserved unchanged. `CONTEXT_TEXT` is base64-encoded
to survive newlines and non-printable characters in operator-supplied notes.

```bash
python3 - "$TRANSLATIONS_DIR" "$EXPERIMENT_ROOT" <<'PY' > /tmp/sim2real_translate_algos.txt
import json, sys, base64
from pathlib import Path
tdir = Path(sys.argv[1])
exp = Path(sys.argv[2])
si = json.loads((tdir / "skill_input.json").read_text())
required = ("version", "translation_hash", "experiment_root", "translations_dir",
            "scenario", "algorithms", "context")
missing = [k for k in required if k not in si]
if missing:
    raise SystemExit(f"HALT: skill_input.json missing keys: {missing}")
ctx = si.get("context") or {}
print("CONTEXT_TEXT_B64=" + base64.b64encode(
    (ctx.get("text") or "").encode()).decode())
# Write context paths (one per line) to a dedicated file — paths may contain spaces.
ctx_paths_out = Path("/tmp/sim2real_translate_ctx_paths.txt")
paths = [str(exp / p) for p in (ctx.get("file_paths") or [])]
ctx_paths_out.write_text("".join(p + "\n" for p in paths))
for algo in si["algorithms"]:
    # baseline_overlay_path is None when this algorithm's ``defaults``
    # baseline isn't in ``skill_input.baselines`` (or the manifest has
    # no baselines at all) — emit an empty string so the writer's
    # Phase 2 skip-check triggers.
    print("ALGO\t" + "\t".join([
        algo["name"],
        algo["source_path"],
        algo["output_dir"],
        algo["config_output_path"],
        algo.get("baseline_overlay_path") or "",
        algo.get("notes") or "",
    ]))
PY
[ $? -eq 0 ] || exit 1

CONTEXT_TEXT_B64_VAL=$(grep '^CONTEXT_TEXT_B64=' /tmp/sim2real_translate_algos.txt | cut -d= -f2-)
if [ -n "$CONTEXT_TEXT_B64_VAL" ]; then
    CONTEXT_TEXT=$(printf '%s' "$CONTEXT_TEXT_B64_VAL" | base64 -d) || {
        echo "HALT: failed to decode CONTEXT_TEXT_B64"; exit 1;
    }
else
    CONTEXT_TEXT=""
fi
# When the writer/reviewer/expert prompts substitute {CONTEXT_FILE_PATHS},
# they receive the newline-delimited file contents (spaces preserved).
CONTEXT_FILE_PATHS=$(cat /tmp/sim2real_translate_ctx_paths.txt)
```

Verify that every algorithm source and every context file exists (absolute
paths; iterate via `< <(...)` so `exit 1` inside the loop halts the outer
script, and read context paths line-by-line so paths containing spaces are
preserved):

```bash
while IFS=$'\t' read -r _ name source_rel _ _ _ _; do
    test -f "$EXPERIMENT_ROOT/$source_rel" || {
        echo "HALT: algorithm source not found: $EXPERIMENT_ROOT/$source_rel"
        exit 1
    }
done < <(grep -E '^ALGO\b' /tmp/sim2real_translate_algos.txt) || exit 1

while IFS= read -r p; do
    [ -n "$p" ] || continue
    test -f "$p" || { echo "HALT: context file not found: $p"; exit 1; }
done < /tmp/sim2real_translate_ctx_paths.txt || exit 1
```

## Idempotency Check

Skip algorithms whose `<algo>_output.json` already exists — those are done. If
every algorithm is complete, exit with an "already complete" message:

```bash
INCOMPLETE_ALGOS=""
while IFS=$'\t' read -r _ name _ output_dir _ _ _; do
    algo_out="$TRANSLATIONS_DIR/$output_dir/${name}_output.json"
    if [ ! -f "$algo_out" ]; then
        INCOMPLETE_ALGOS="$INCOMPLETE_ALGOS $name"
    fi
done < <(grep -E '^ALGO\b' /tmp/sim2real_translate_algos.txt)
INCOMPLETE_ALGOS=$(echo "$INCOMPLETE_ALGOS" | xargs)

if [ -z "$INCOMPLETE_ALGOS" ]; then
    echo "translation $TRANSLATION_HASH already complete — run 'sim2real translate --resume' next"
    exit 0
fi
```

## Per-Algorithm Loop

For each name in `$INCOMPLETE_ALGOS`, run one full team session. Every session
is independent — a failure on one algorithm leaves the others available to
retry on the next skill invocation (the on-disk `<algo>_output.json`
completeness check ensures idempotency).

For each `ALGO_NAME` in `$INCOMPLETE_ALGOS`:

### Step 1: Load per-algorithm variables

```bash
# Extract this algorithm's row from /tmp/sim2real_translate_algos.txt.
IFS=$'\t' read -r _ _ ALGO_SOURCE_REL OUTPUT_DIR_REL CONFIG_OUTPUT_REL BASELINE_OVERLAY_REL ALGO_NOTES < <(
    awk -F$'\t' -v n="$ALGO_NAME" '$1=="ALGO" && $2==n {print}' /tmp/sim2real_translate_algos.txt
)
ALGO_SOURCE="$EXPERIMENT_ROOT/$ALGO_SOURCE_REL"
OUTPUT_DIR="$TRANSLATIONS_DIR/$OUTPUT_DIR_REL"
CONFIG_OUTPUT_PATH="$TRANSLATIONS_DIR/$CONFIG_OUTPUT_REL"
if [ -n "$BASELINE_OVERLAY_REL" ]; then
    BASELINE_OVERLAY_PATH="$TRANSLATIONS_DIR/$BASELINE_OVERLAY_REL"
else
    BASELINE_OVERLAY_PATH=""
fi
# The new skill_input schema has no algorithm_config field — parameters (if
# any) live inside the source file. Pass "" so the prompts take their
# "parameter-free" branches when reasoning about configurable weights.
ALGO_CONFIG=""

mkdir -p "$OUTPUT_DIR"
```

### Step 2: Spawn the team

Read the three prompt templates:
- `$REPO_ROOT/.claude/skills/sim2real-translate/prompts/agent-expert.md`
- `$REPO_ROOT/.claude/skills/sim2real-translate/prompts/agent-writer.md`
- `$REPO_ROOT/.claude/skills/sim2real-translate/prompts/agent-reviewer.md`

Substitute every `{PLACEHOLDER}` occurrence with the corresponding shell
variable:

- `{REPO_ROOT}` → `$REPO_ROOT`
- `{EXPERIMENT_ROOT}` → `$EXPERIMENT_ROOT`
- `{TRANSLATIONS_DIR}` → `$TRANSLATIONS_DIR`
- `{OUTPUT_DIR}` → `$OUTPUT_DIR`
- `{BASELINE_OVERLAY_PATH}` → `$BASELINE_OVERLAY_PATH` (per-algorithm — points
  at the overlay for THIS algorithm's baseline via `algorithms[i].baseline_overlay_path`;
  empty when the algorithm's `defaults` isn't in `skill_input.baselines` or the
  manifest has no baselines — writer's Phase 2 handles this)
- `{ALGO_SOURCE}` → `$ALGO_SOURCE`
- `{ALGO_CONFIG}` → `$ALGO_CONFIG` (always empty string; see Step 1 comment)
- `{ALGO_NAME}` → `$ALGO_NAME`
- `{ALGO_NOTES}` → `$ALGO_NOTES`
- `{TARGET_REPO}` → `$TARGET_REPO`
- `{CONFIG_KIND}` → `$CONFIG_KIND`
- `{SCENARIO}` → `$SCENARIO`
- `{BUILD_COMMANDS}` → `$BUILD_COMMANDS` (JSON list of shell commands)
- `{REVIEW_ROUNDS}` → `$REVIEW_ROUNDS`
- `{CONTEXT_TEXT}` → `$CONTEXT_TEXT`
- `{CONTEXT_FILE_PATHS}` → `$CONTEXT_FILE_PATHS` (newline-separated absolute paths — one path per line)
- `{EXPERT_AGENT_NAME}` → `"expert"`
- `{MAIN_SESSION_NAME}` → `"main"`

**Spawn all three agents in a single tool-call message** so they start in
parallel. Issue three Agent tool calls at once — do not wait between them:

1. Agent(name="expert",
         run_in_background=true, subagent_type=general-purpose, model="opus",
         prompt=<substituted agent-expert.md>)
2. Agent(name="reviewer",
         run_in_background=true, subagent_type=general-purpose, model="opus",
         prompt=<substituted agent-reviewer.md>)
3. Agent(name="writer",
         run_in_background=true, subagent_type=general-purpose, model="opus",
         prompt=<substituted agent-writer.md>)

All three start simultaneously. Expert and Reviewer initialize in the
background while Writer begins Phase 2. By the time Writer reaches Phase 4
(translation), Expert will have completed its repo exploration and be ready
to answer queries.

### Step 3: Handle team messages

Wait for messages from the team (Expert and Writer both send to
`{MAIN_SESSION_NAME}`). Handle each case:

**On `expert-ready: ...`:**

The Expert has finished initialization and is ready for queries. Log
`"Expert initialized"` and continue waiting for the next message (no reply
to the Expert needed — its readiness state is implicit from here on).

**On `baseline-ready: ...`:**

If `$BASELINE_OVERLAY_PATH` is empty (manifest declares no baseline overlay),
this branch should not fire; if it does, send back
`SendMessage("writer", "no-baseline")` and skip. Otherwise read the file the
writer produced (at `$BASELINE_OVERLAY_PATH`) and print to the user:
```
━━━ Baseline Config (derived from sim → real EPP) ━━━
<file contents>
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Provide feedback to revise, or type 'done' to proceed.
```
Read user input. If it starts with `feedback:`, forward verbatim to the
writer. If it is exactly `done`, send `SendMessage("writer", "continue")`.

**On `treatment-ready: ...`:**

Read `$CONFIG_OUTPUT_PATH` and print to the user:
```
━━━ Treatment Config (derived from baseline + algorithm) ━━━
<file contents>
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Provide feedback to revise, or type 'done' to proceed.
```
Handle feedback / continue as above.

**On `review-passed: round=N plugin_type=<type>`:**

Read `$OUTPUT_DIR/${ALGO_NAME}_output.json` for the plugin file list and
print `$CONFIG_OUTPUT_PATH` for the operator. Then prompt:
```
━━━ Review Passed — ACTION REQUIRED ━━━
Treatment Config: <contents above>
Plugin files: <files_created from ${ALGO_NAME}_output.json>

⚠ This algorithm's translation is INCOMPLETE until you reply. Source files
  have NOT yet been copied to $OUTPUT_DIR/. Choose one:

  • Reply `done`             → finalize: copy plugin source files from the target repo into $OUTPUT_DIR/,
                               shut down the team, and move to the next algorithm
  • Reply `feedback: <text>` → iterate one more review round with the feedback

Without a reply, the writer agent will wait indefinitely.
```
If reply is exactly `done`: `SendMessage("writer", "done")`.
If reply starts with `feedback:`: `SendMessage("writer", "feedback: <text>")`.
Otherwise: re-print the prompt and wait — do NOT silently treat ambiguous
input as either branch.

**On `done: ...`:**

The writer completed successfully. Proceed to Step 4.

**On `escalate: ...`:**

Prompt the operator:
```
Translation exhausted $REVIEW_ROUNDS rounds without reviewer approval for
$ALGO_NAME. Remaining issues:
<paste issues from the escalate message>

Options:
  [c] Continue for N more rounds — reply with: continue N
  [a] Accept translation as-is — reply with: accept
  [q] Quit and investigate — reply with: quit
```

- If `continue N`: bump `REVIEW_ROUNDS=$((REVIEW_ROUNDS + N))`, then stop the
  current writer with `TaskStop(task_id="writer")` before spawning a NEW
  writer agent with the updated value substituted (the harness silently
  renames a second live `writer` to `writer-2`, and the reviewer would then
  message the wrong one). The reviewer is still idle — it does not need to
  be restarted.
- If `accept`: proceed to Step 4 with `consensus='accepted_without_consensus'`.
- If `quit`:
  ```
  TaskStop(task_id="writer")
  TaskStop(task_id="reviewer")
  TaskStop(task_id="expert")
  ```
  Skip to the next algorithm in the loop (or exit if none remain).

**On `build-failed: ...`:**

Surface the build error to the operator:
```
Translation build failed after 6 retries for $ALGO_NAME.
Error: <paste error from message>

Fix the algorithm source or target repo state, then re-run /sim2real-translate.
```
Shut down the team as under `quit` and skip to the next algorithm.

### Step 4: Copy source and shut down

Derive the file list from git state in `$TARGET_REPO`, update
`${ALGO_NAME}_output.json`, and copy plugin sources into `$OUTPUT_DIR`:

```bash
python3 -c "
import sys
sys.path.insert(0, '$REPO_ROOT/.claude/skills/sim2real-translate/scripts')
from copy_generated import copy_generated
copy_generated('$TARGET_REPO', '$TRANSLATIONS_DIR', algo_name='$ALGO_NAME')
" || exit 1
```

Verify the per-algorithm output:

```bash
python3 - "$OUTPUT_DIR" "$ALGO_NAME" <<'PY' || exit 1
import json, sys
from pathlib import Path
out_dir = Path(sys.argv[1])
algo = sys.argv[2]
algo_out = out_dir / f"{algo}_output.json"
if not algo_out.exists():
    raise SystemExit(f"ERROR: {algo_out} not found")
o = json.loads(algo_out.read_text())
required = ("plugin_type", "files_created", "files_modified", "package",
            "register_file", "test_commands", "config_kind",
            "treatment_config_generated", "description")
missing = [f for f in required if f not in o]
if missing:
    raise SystemExit(f"ERROR: {algo_out} missing keys: {missing}")
for rel in o["files_created"] + o.get("files_modified", []):
    if not (out_dir / rel).exists():
        raise SystemExit(f"ERROR: expected copy at {out_dir}/{rel} not found")
print(f"Verified {algo}: plugin_type={o['plugin_type']}, "
      f"{len(o['files_created'])} created, "
      f"{len(o.get('files_modified', []))} modified")
PY
```

Shut down the team so the agent names are freed before the next algorithm's
spawn:
```
TaskStop(task_id="writer")
TaskStop(task_id="reviewer")
TaskStop(task_id="expert")
```

Loop to the next algorithm in `$INCOMPLETE_ALGOS`.

## Completion

After all algorithms are done, print:

```
━━━ /sim2real-translate complete ━━━

Translation: $TRANSLATION_HASH
Algorithms translated: <count>

Per-algorithm artifacts:
  $TRANSLATIONS_DIR/generated/<algo>/{cmd,pkg}/
  $TRANSLATIONS_DIR/generated/<algo>/<algo>_config.yaml
  $TRANSLATIONS_DIR/generated/<algo>/<algo>_output.json

Next: run `sim2real translate --resume` to validate outputs, then
`sim2real build --translation <alias-or-hash>` to build the EPP images.
```

TaskUpdate all open tasks → completed.

## What This Skill Does NOT Do

- Does not drift from the pinned `translation_hash`: it calls `slicer.translation_hash_with_sources` at the same call site `sim2real translate` uses (so hashes stay identical) and then looks up `translations/<hash>/skill_input.json`. It does not cross-check the hash inside `skill_input.json` against the recomputed value — divergence there is treated as a caller bug in `sim2real translate`, not a skill invariant.
- Does not build container images (`sim2real build`'s job).
- Does not touch cluster config, bundle inputs, or run summaries.
- Job: produce working, reviewed Go plugin source + treatment overlay + per-algo metadata under `translations/<hash>/generated/<algo>/`.
