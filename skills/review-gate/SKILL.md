---
name: review-gate
description: Turn Agent Crew's stop-time review gate on or off for this repository, or show whether it is on
argument-hint: "enable|disable|status"
disable-model-invocation: true
allowed-tools: Bash
---

!`sh "${CLAUDE_PLUGIN_ROOT}/scripts/crew" gate $ARGUMENTS 2>&1 || true`

Present the output above. Then explain in two or three sentences what the gate
does, so the choice is informed:

- When it is on, each time Claude is about to stop in this repository with
  uncommitted work, a reviewer model from the crew reads that work and Claude's
  last message, and answers ALLOW or BLOCK. A BLOCK keeps Claude working, with
  the reviewer's reasons as the next instruction.
- It never fires twice in a row, never runs when there is nothing
  uncommitted, and lets the stop through when no reviewer answers.
- Each stop costs one reviewer request, and waits for it.
