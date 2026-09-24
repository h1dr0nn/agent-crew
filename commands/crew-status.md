---
description: Show Agent Crew's running workers, exhausted keys, model latency and open task worktrees
---

Run `crew status` (the launcher is `~/.agent-crew/bin/crew`, or `crew.cmd` on
Windows) and report it briefly: which tasks are running and for how long, which
keys are exhausted for which allowance and until when, the fastest and slowest
models by measured latency, and any task worktrees still open that have
finished and should be landed or removed.
