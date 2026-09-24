---
name: result
description: Show a finished Agent Crew run - its status, steps, route, time, tokens, and the worker's summary with the final verify output
argument-hint: "[task]"
disable-model-invocation: true
allowed-tools: Bash
---

!`sh "${CLAUDE_PLUGIN_ROOT}/scripts/crew" result $ARGUMENTS 2>&1 || true`

Present the output above in full; do not condense it. Keep file paths, line
numbers and error text exactly as reported.

Then one line on what it means:

- `finished`: the worker's own verify passed. That is a claim, not a check:
  the next step is to verify it yourself, read the diff and have it reviewed
  (`/agent-crew:review`), then `/agent-crew:land`.
- `max_steps`, `no_progress`, `stalled`: it did not get there; the final
  verify output says where it stopped. Either send a narrower follow-up task
  or fix a trivial remainder directly.
- `no_route`: every key is exhausted or missing; see `/agent-crew:status`.
- `cancelled`: it was stopped; the worktree is kept.
