---
name: doctor
description: Check that Agent Crew is ready - Python, git, the launcher, the providers file and its keys, usable routes per role, this repository's project file
disable-model-invocation: true
allowed-tools: Bash
---

!`sh "${CLAUDE_PLUGIN_ROOT}/scripts/crew" doctor 2>&1 || true`

Present the report above as it is. Then, for each `FAIL` line, give the one
thing to do about it, in order: the arrow after each line names it. If it ends
with `all good`, say so in one line and stop. If the providers file or the
project file is missing, suggest `/agent-crew:setup`.
