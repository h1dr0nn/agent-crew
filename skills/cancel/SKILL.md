---
name: cancel
description: Stop a running Agent Crew worker; its worktree is kept
argument-hint: "<task>"
disable-model-invocation: true
allowed-tools: Bash
---

!`sh "${CLAUDE_PLUGIN_ROOT}/scripts/crew" cancel $ARGUMENTS 2>&1 || true`

Present the output above as it is. If no task was named, or the name is not
running, show `crew status` so the user can pick one.
