---
name: status
description: Show Agent Crew's running workers, exhausted keys and when they reset, measured model latency, and open task worktrees
disable-model-invocation: true
allowed-tools: Bash
---

!`sh "${CLAUDE_PLUGIN_ROOT}/scripts/crew" status 2>&1 || true`

Render the output above compactly:

- a Markdown table of running workers: task, minutes running, route; mark any
  whose process is gone and say `crew cancel <task>` clears it;
- exhausted keys, each with the allowance and the UTC time it resets;
- the fastest and the slowest models by measured latency;
- open task worktrees, pointing out any whose run already finished
  (`crew result <task>`) and should be landed or removed.

No prose beyond that.
