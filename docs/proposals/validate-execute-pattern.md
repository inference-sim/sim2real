# Two-phase commands: validate, then execute

A complement to [three-dimensional-sim2real.md](three-dimensional-sim2real.md). Both proposals are independent — either can land first without affecting the other.

## Motivation

Sim2real commands today mix four different "have my preconditions been met?" patterns and never name any of them:

| Pattern | Example |
|---|---|
| Validate loudly, fail with fix instruction | `prepare.py` Phase 1 Init validating `transfer.yaml`, component submodule, `component.ref` |
| Lazy auto-execute upstream | `deploy.py:_cmd_run` calling `_cmd_build` at the top |
| State-machine skip | every phase's `if state.is_done(name): return` |
| Idempotent kubectl apply | every step in `setup.py` |

The patterns coexist but don't compose. A reader can't tell from `--help` what gets checked, what gets auto-fixed, and what aborts. Some validators print a fix command; others just print a path. Some chains exist (`run → build`); others don't (`assemble → translate`). There's no dry-run.

This proposal makes the pattern a contract.

## The contract

Every command has two named phases:

```
validate(args)  → list of unmet preconditions (each labelled auto-fixable or human-required)
execute(args)   → the work itself; assumes validate has passed
```

The CLI dispatcher composes them:

```
sim2real <cmd> --plan          # validate only; print what would happen
sim2real <cmd>                 # validate, auto-execute cheap missing prereqs, execute
sim2real <cmd> --no-auto       # validate; abort on any missing prereq, never auto-execute
```

Today's default behavior is option two. Options one and three don't exist.

## Auto-execute policy is the design decision

For each precondition, the command author decides whether a violation:

- **Auto-fixes** by chaining to a prerequisite command (cheap and unsurprising), or
- **Aborts** with a fix instruction (expensive, irreversible, or requires human input).

The choice is per-precondition, not per-command. Some preconditions of one command might be auto-fixable while others require human action.

Rule of thumb:

- **Auto-fixable**: idempotent, sub-minute, no human interaction (`build` an image that's already in the registry, re-apply a Kubernetes manifest, recompute a content hash).
- **Human-required**: prompts for credentials, executes a skill, makes a destructive change, takes minutes, or has any chance of surprising the operator (`provision` a cluster, `translate` an algorithm).

When in doubt, abort. Auto-execution that surprises is worse than a clear error message.

## Per-command sketch

Assuming the three-dimensional layout from the sibling proposal:

| Command | Precondition | Auto-fixable? |
|---|---|---|
| `cluster.py provision <id>` | kubectl context reachable | no (requires credentials) |
| | user has RBAC to create resources | no (requires escalation) |
| `sim2real translate` | `transfer.yaml` valid | no (file edit required) |
| | component submodule init'd | no (`git submodule update` needs operator decision) |
| | component HEAD matches `component.ref` | no (ref mismatch needs operator review) |
| `sim2real build --translation T` | `translations/T/` exists | **yes** — chain to `translate` (heavy; gate with `--auto-translate`) |
| | registry credentials present | no |
| `sim2real assemble --translation T --cluster C --run R` | `translations/T/` exists | partial — auto-build if image missing; auto-translate gated |
| | `clusters/C/cluster_config.json` exists | no (provision required) |
| | assembly slice of `transfer.yaml` valid | no (file edit required) |
| `deploy.py run --run R` | `runs/R/cluster/` exists | **yes** — chain to `assemble` |
| | image present in target registry | **yes** — chain to `build` |
| | cluster reachable | no |
| `deploy.py collect --run R` | `runs/R/cluster/` exists | no (no run, nothing to collect) |
| | PVC has results | no (orchestrator hasn't completed) |

Heavy auto-fix steps (`translate`, `build` of a missing translation) require an explicit `--auto-<step>` opt-in. Cheap auto-fix steps run by default.

## What this gives you

1. **Predictable validation surface.** `sim2real <cmd> --help` lists preconditions and which ones auto-fix. No more "discover by running."
2. **Generic chain runner.** Today's `_cmd_run → _cmd_build` chain is hand-written. With a uniform contract, a single dispatcher walks the precondition DAG. Adding `collect`-auto-runs-`run` or similar becomes one declaration, not new glue code.
3. **Dry-run for free.** `--plan` mode runs validators only and prints the would-do list. Useful before touching a cluster.
4. **Clean error messages.** Standard shape: "missing X. Fix: <command> OR <instruction>." Today each validator invents its own format.

## What this costs

- **Per-command boilerplate.** Each command grows a `validate()` function. For trivial commands (`list`, `use`) this is overhead.
- **Discipline.** "Validate honestly about what's missing" is a habit that has to be enforced in code review. A validator that silently passes when it shouldn't is worse than no validator.
- **The auto-fix DAG can recurse.** If `run` auto-fixes via `assemble`, and `assemble` auto-fixes via `build`, you may end up running three commands when the user typed one. With `--plan` this is visible; without, it's a footgun. Mitigation: a depth limit on auto-fix chains, or always print "auto-executing: build, assemble, run" before starting.

## Implementation shape

Each command becomes:

```python
def validate(args) -> list[Precondition]:
    preconditions = []
    if not <thing>:
        preconditions.append(Precondition(
            name="thing-exists",
            description="...",
            fix_command=["sim2real", "translate"],  # None if not auto-fixable
            fix_hint="run `sim2real translate` to produce X",  # always present
        ))
    return preconditions

def execute(args) -> None:
    # actual work
    ...

# dispatcher
def cmd(args):
    unmet = validate(args)
    if args.plan:
        print_preconditions(unmet); return
    auto_fixable = [p for p in unmet if p.fix_command and (args.auto or is_cheap(p))]
    blocking = [p for p in unmet if p not in auto_fixable]
    if blocking:
        for p in blocking: print(f"missing: {p.description}. {p.fix_hint}")
        sys.exit(1)
    for p in auto_fixable:
        run_subcommand(p.fix_command)
    execute(args)
```

The boilerplate is one `Precondition` dataclass, one validator function per command, and one ~20-line dispatcher.

## Open questions

1. **Where does state-machine skip fit?** Today `_phase_*` uses `state.is_done()` for idempotency. Is that "validate determined this is already done → execute is a no-op" or a separate concept? Probably the former; folding it in keeps one model.
2. **Auto-fix depth limit.** `run → assemble → build → translate` is three hops. Allow? Cap at one? Print and prompt at two?
3. **Plan output format.** Plain text vs. structured (JSON) for tooling. Probably both — text default, `--plan --json` for tooling.
4. **Migration.** Existing commands keep working; new validate/execute split is added incrementally. No big-bang refactor needed.

## Relationship to the three-dimensional proposal

Independent. The pattern works on today's layout (validate-then-execute on `prepare.py assemble`, `deploy.py run`, etc.). It works equally well on the proposed three-dimensional layout (validate-then-execute on `cluster.py provision`, `sim2real translate`, `sim2real assemble`, etc.) — and is more *valuable* there because the new layout has more commands and a richer precondition DAG.

Either proposal can land first. Landing this one first makes the existing commands more predictable. Landing the three-dimensional one first makes the surface area to apply this pattern larger.
