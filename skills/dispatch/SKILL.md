---
name: dispatch
description: Hand one small, well-specified coding task to a crew worker model in its own git worktree, with the files it may write and the verify it must pass. Use when work is clearly bounded and can be delegated rather than written by Claude.
argument-hint: "[--background|--wait] [--task <name>] [--owns <globs>] [--verify <kind>] [--model <id>] [what to build]"
allowed-tools: Bash, Read, Write, Grep, Glob, AskUserQuestion
---

Delegate one task to a worker. Raw arguments: `$ARGUMENTS`

The CLI is `~/.agent-crew/bin/crew` (`crew.cmd` from cmd or PowerShell; under `$AGENT_CREW_HOME/bin` when that is set). If
`crew doctor` shows no providers file or no project file, stop and point to
`/agent-crew:setup`.

1. **Scope it.** A worker is a weaker model with a small step budget. One
   task owns one to three files, stays in one language, and has an exact
   spec. If the request is larger, split it and dispatch the parts, never two
   tasks that write the same file. The `worker-prompting` skill has the rules.
2. **Name it.** `--task` if given, else a short id (`B1`, `fix-parse`).
3. **Write the prompt** to `.agent-crew/prompts/<task>.md`. Start from
   `crew template task`, and begin it with the line `@@template rules`. Give
   the exact signatures, types and errors; inline what it builds on with
   `@@include path#Lx-Ly` and `@@grep path regex` so it never explores; say
   what the tests must cover. Check the packed result with
   `crew pack .agent-crew/prompts/<task>.md` when unsure.
4. **Run it**, with the files it owns and the verify kind from
   `.agent-crew/project.toml`:

   ```bash
   crew run .agent-crew/prompts/<task>.md --task <task> --new-task --owns "<globs>" --verify <kind> [--model <id>]
   ```

   `--background` means `run_in_background: true`; `--wait` the foreground.
   With neither, run small tasks in the foreground and anything that may take
   several minutes in the background. Several background workers can run at
   once: routes spread across keys and models, and an exhausted key is skipped
   until it resets.
5. **Report** the JSON line it prints (status, steps, route, minutes), or,
   in the background, that it started and that `/agent-crew:status` and
   `/agent-crew:result <task>` follow it. Then check the work before landing
   it (`/agent-crew:land` runs those checks).
