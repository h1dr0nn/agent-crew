---
name: setup
description: Set up Agent Crew - the providers file, the key variables, this repository's project file - and optionally turn the stop-time review gate on or off
argument-hint: "[provider base URL] [--enable-review-gate|--disable-review-gate]"
disable-model-invocation: true
allowed-tools: Bash, Read, Edit, Write, AskUserQuestion
---

Set up Agent Crew for the user, checking each step works before the next.

Where things stand now:

!`sh "${CLAUDE_PLUGIN_ROOT}/scripts/crew" doctor 2>&1 || true`

Raw arguments: `$ARGUMENTS`

The CLI is `crew`: the launcher `~/.agent-crew/bin/crew` (`crew.cmd` from
cmd or PowerShell). If it is missing, run
`sh "${CLAUDE_PLUGIN_ROOT}/scripts/crew" shim` once.

1. **Providers.** If `~/.agent-crew/providers.toml` does not exist, run
   `crew config init`. Fill it in with the user: the provider's base URL (the
   first argument, if one was given), the names of the environment variables
   that hold their keys, and the models they want as writers and reviewers,
   each with a priority and, when models share a quota, the same `allowance`.
   Any OpenAI-compatible endpoint works: a local router, OpenRouter, a vendor
   API.
2. **Keys.** Never write a key into the file, into chat, or into a command
   line. Ask the user to set the variables themselves (`setx NAME value` on
   Windows, their shell profile elsewhere), then to restart Claude Code so the
   new environment is seen. `api_key_file` (one key per line) is the
   alternative for many keys.
3. **Probe.** Run `crew config test` and show the table. A failing route says
   why: a refused key, an unknown model id, an exhausted allowance.
4. **Project.** In the current repository, run `crew init` if
   `.agent-crew/project.toml` is missing. Fill in `[verify]`: one command per
   kind (for example `rust`, `ts`, `docs`), each formatting, building, linting
   and testing, printing failures, and exiting non-zero or printing
   `RESULT: FAIL` when anything fails. Add `shared` for heavy ignored
   directories to link into worktrees (`node_modules`, `.venv`), and
   `"rust-modules"` to `checks` for a Rust project. Suggest committing the file.
5. **Review gate.** If the arguments contain `--enable-review-gate`, run
   `crew gate enable`; for `--disable-review-gate`, `crew gate disable`.
   Otherwise leave it as it is and mention it exists: when enabled, each time
   Claude stops with uncommitted work in this repository, a reviewer model
   reviews that work and can block the stop.
6. Finish with `crew doctor` and show what it reports.
