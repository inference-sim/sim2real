---
name: sim2real-translate
description: |
  DISABLED for step-1. The skill-driven translation flow is scheduled to
  be restored in step-2 of the refactor/v2-step-1 epic (issue #443).
  Step-1 supports BYO translations only — use
  `pipeline/sim2real.py translation register` instead.
user-invocable: false
---

# sim2real-translate — DISABLED for step-1

This skill has been removed as part of the **BYO MVP** (step-1 of the
`refactor/v2-step-1` epic — issue
[#443](https://github.com/inference-sim/sim2real/issues/443)).

Step-1 supports **bring-your-own translations only**. Register a
pre-built translation with:

```
python pipeline/sim2real.py translation register \
    --algorithm NAME \
    --image REF \
    --config PATH_TO_TREATMENT_OVERLAY
```

Then assemble a run:

```
python pipeline/sim2real.py assemble \
    --translation HASH \
    --cluster CLUSTER_ID \
    --run RUN_NAME
```

The skill-driven flow (evolved algorithm → translated EPP plugin) is
scheduled to be restored in step-2 of the epic. Invoking the skill in
the current state exits with this message.

**If you got here from a slash command:** stop and use
`sim2real translation register` instead. If you don't yet have a
prebuilt image + config, wait for step-2 to land.
