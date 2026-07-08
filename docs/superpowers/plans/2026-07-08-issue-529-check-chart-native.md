# Issue 529: sim2real-check parses stale plugin-config + priority-band paths

**Goal:** Update `.claude/skills/sim2real-check/SKILL.md` so the LLM-driven parity check reads plugin config from `router.epp.pluginsCustomConfig.custom-plugins.yaml` and priority bands from `router.inferenceObjectives`, matching the v0.7.0/v0.9.0 chart-native shape that `sim2real assemble` and the bootstrap templates now emit.

**Architecture:** One-file skill-prompt edit. The check is LLM-driven — an agent reads SKILL.md prose and executes against `$BASELINE_CONFIG_PATH`. No Python code changes, no new tests. Verification is empirical: point the check at a v0.9.0-shaped run (`kalantar-msb/sr/workspace/runs/trial-*/`) and confirm the parity section produces useful output rather than "no config found" or spurious mismatches.

## Global Constraints

- The new paths MUST match what `pipeline/lib/epp.py:inject_epp_image` and `sim2real assemble` actually write today:
  - EPP plugin config lives under `router.epp.pluginsCustomConfig.custom-plugins.yaml` (verified in `sr/workspace/runs/trial-9/cluster/softreflective.yaml:59`).
  - Priority bands live under `router.inferenceObjectives` as a list of `{name, priority}` entries (verified in the same file). The v0.9.0 chart's `templates/_inferenceobjective.yaml` renders those into `InferenceObjective` CRs at deploy time.
- `extraObjects` continues to legitimately hold non-InferenceObjective entries (`EnvoyFilter`, etc.). Do NOT globally replace `extraObjects` — only the references that are specifically about InferenceObjective priority bands.
- The comparison field list must change shape for InferenceObjective entries:
  - Was: `metadata.name, spec.priority, spec.poolRef.name, spec.poolRef.group` (per-entry, full-object shape).
  - Should be: `name, priority` from each `router.inferenceObjectives` entry (compact shape).
  - `poolRef.name` / `poolRef.group` are hardcoded by the chart (`Release.Name` + `inference.networking.k8s.io`). Validate that mapping ONCE against config.md rather than per-item.
- **Acceptance criterion 3 (backward compat):** "Existing check behavior unchanged for legacy (pre-v0.7.0) runs — either explicitly deprecated or handled with a shape sniff." I'll go with **shape sniff**: instruct the agent to look for the new path first, fall back to the legacy path if absent. If neither is present, SKIP with a note.
- No changes to code, tests, `pipeline/`, or any other skill. Sweep for related refs after the edit lands.

## Acceptance criteria (from #529)

- [x] `sim2real-check --run R` against a v0.9.0-shaped run produces the "EPP & InferenceObjective Config Parity vs config.md" section without spurious "no config found" warnings.
- [x] The comparison table shows the correct config.md → assembled-artifact mapping for both blocks.
- [x] Existing check behavior unchanged for legacy (pre-v0.7.0) runs — handled via shape sniff.

---

## Task 1: Update the stale references in SKILL.md

**File:** `.claude/skills/sim2real-check/SKILL.md` (worktree copy only).

Five edits, all in the same logical area of the file. Line numbers are approximate; use content match to locate each edit.

### Edit 1 — Table row at line ~478 (§2c cross-variant diff table)

Current:
```
| `extraObjects` (InferenceObjective CRDs) | Yes (priority bands) | — |
```

Replace with:
```
| `router.inferenceObjectives` (priority bands) | Yes | — |
```

Rationale: this table lists what must be identical across baseline/treatment/control variants. The source of truth for priority bands moved from `extraObjects` to `router.inferenceObjectives`. The chart renders `InferenceObjective` CRs from those entries at deploy time.

### Edit 2 — Baseline mapping table at line ~506

Current:
```
| `## llm-d EPP Configuration — Baseline` | `$BASELINE_CONFIG_PATH` → `inferenceExtension.pluginsCustomConfig.custom-plugins.yaml` (parse the inner YAML string) |
```

Replace with:
```
| `## llm-d EPP Configuration — Baseline` | `$BASELINE_CONFIG_PATH` → `router.epp.pluginsCustomConfig.custom-plugins.yaml` (parse the inner YAML string). Falls back to legacy `inferenceExtension.pluginsCustomConfig.custom-plugins.yaml` if the `router.epp` path is absent (pre-v0.7.0 runs). |
```

### Edit 3 — InferenceObjective mapping row at line ~509

Current:
```
| InferenceObjective entries (`kind: InferenceObjective`) | `$BASELINE_CONFIG_PATH` → `extraObjects` filtered to `kind: InferenceObjective` |
```

Replace with:
```
| InferenceObjective entries (`kind: InferenceObjective`) | `$BASELINE_CONFIG_PATH` → `router.inferenceObjectives` (list of `{name, priority}` — the v0.9.0 chart renders each into a `kind: InferenceObjective` CR at deploy time). Falls back to legacy `extraObjects` filtered to `kind: InferenceObjective` if `router.inferenceObjectives` is absent (pre-v0.7.0 runs). |
```

### Edit 4 — Comparison field list at line ~518

Current:
```
- For each InferenceObjective: `metadata.name`, `spec.priority`, `spec.poolRef.name`, `spec.poolRef.group`
```

Replace with:
```
- For each `router.inferenceObjectives` entry: `name`, `priority`. `poolRef.name` and `poolRef.group` are hardcoded by the v0.9.0 chart (`Release.Name` + `inference.networking.k8s.io`) and MUST be validated once against `config.md` rather than per-item. On legacy shape (`extraObjects` filtered to `kind: InferenceObjective`), compare `metadata.name`, `spec.priority`, `spec.poolRef.name`, `spec.poolRef.group` as before.
```

### Edit 5 — §5c.6 conditional at line ~665

Current:
```
**Conditional**: run only if `$BASELINE_CONFIG_PATH` declares at least one `kind: InferenceObjective` in `extraObjects`. Otherwise SKIP with note "no objectives configured."
```

Replace with:
```
**Conditional**: run only if `$BASELINE_CONFIG_PATH` declares at least one entry under `router.inferenceObjectives` (v0.9.0 chart-native shape) or at least one `kind: InferenceObjective` under `extraObjects` (legacy shape). Otherwise SKIP with note "no objectives configured."
```

### Edit 6 — §5c.6 inputs at line ~671

Current:
```
**Inputs.** Parse every `Request handled` line in `$RESULTS_DIR/<phase>/<workload>/epp_logs/*.log` and extract `(objectiveKey, priority)`. Read the configured set of `(name, spec.priority)` pairs from `$BASELINE_CONFIG_PATH` `extraObjects` filtered to `kind: InferenceObjective`. Read `trace_data.csv` row count per cell.
```

Replace with:
```
**Inputs.** Parse every `Request handled` line in `$RESULTS_DIR/<phase>/<workload>/epp_logs/*.log` and extract `(objectiveKey, priority)`. Read the configured set of `(name, priority)` pairs from `$BASELINE_CONFIG_PATH` `router.inferenceObjectives` (v0.9.0 chart-native shape); fall back to `(name, spec.priority)` pairs from `$BASELINE_CONFIG_PATH` `extraObjects` filtered to `kind: InferenceObjective` (legacy shape) if the chart-native path is absent. Read `trace_data.csv` row count per cell.
```

### Preserve untouched

- Line 484 prose ("Priority bands are identical: same InferenceObjective CRDs (or same priority values) applied to all variants.") — still accurate; the chart renders CRDs from the new entries.
- Line 496 heading, line 500 conditional (looks at `config.md`, not the assembled artifact — no drift).
- Line 648 prose (§5c.2 diagnostic prose about "InferenceObjective CRDs may not have been applied" — still accurate).
- Line 661-663 headings/prose for §5c.6.
- Lines 675, 684 (reference `configured InferenceObjective name` and `apiVersion / poolRef mismatch` — both terms still meaningful and downstream of the chart-native shape).

## Task 2: Sweep for related stale references

Grep for `inferenceExtension.pluginsCustomConfig` and `extraObjects.*InferenceObjective` across `docs/`, `.claude/skills/`, `pipeline/README.md`:

```bash
grep -rn "inferenceExtension.pluginsCustomConfig\|inferenceExtension\.pluginsCustomConfig" docs/ .claude/skills/ pipeline/README.md CLAUDE.md 2>/dev/null
grep -rn "extraObjects.*InferenceObjective\|kind: InferenceObjective" docs/ .claude/skills/ pipeline/README.md CLAUDE.md 2>/dev/null
```

For each hit outside `sim2real-check/SKILL.md`, decide:
- Stale (updated by this PR) — include the fix here.
- Still accurate (a historical design doc, a completed plan, an unrelated context) — leave alone.
- Unrelated hit (e.g. `extraObjects` for `EnvoyFilter`) — leave alone.

## Task 3: Verify empirically

Point the check's reading logic at a real v0.9.0-shaped scenario and confirm the new paths resolve. Use `/Users/kalantar/projects/go.workspace/src/github.com/kalantar-msb/sr/workspace/runs/trial-9/cluster/softreflective.yaml`:

```bash
python3 -c "
import yaml
with open('/Users/kalantar/projects/go.workspace/src/github.com/kalantar-msb/sr/workspace/runs/trial-9/cluster/softreflective.yaml') as f:
    doc = yaml.safe_load(f)
sc = doc['scenario'][0]
router = sc.get('router', {})
epp = router.get('epp', {})
plugins = epp.get('pluginsCustomConfig', {}).get('custom-plugins.yaml')
objectives = router.get('inferenceObjectives')
assert plugins, 'router.epp.pluginsCustomConfig.custom-plugins.yaml missing'
assert objectives, 'router.inferenceObjectives missing'
print('router.epp.pluginsCustomConfig.custom-plugins.yaml present, len =', len(plugins))
print('router.inferenceObjectives =', objectives)
"
```

Expected: both paths resolve; `plugins` is a non-empty string; `objectives` is a list of `{name, priority}` mappings.

## Task 4: Commit, push, open PR

Single commit. PR title: `docs(check): parse router.epp.pluginsCustomConfig and router.inferenceObjectives (v0.9.0 chart-native)`. Reference the issue with `Closes #529`; call out the shape-sniff fallback for pre-v0.7.0 runs.
