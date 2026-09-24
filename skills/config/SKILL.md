---
name: config
description: Show which Agent Crew configuration file is in use (this project's .agent-crew/providers.toml or the shared one), open it in the user's editor, or move it between the project and the shared location
argument-hint: "[open|move-to-project|move-to-global]"
allowed-tools: Bash, AskUserQuestion
---

Help the user with Agent Crew's configuration file. Reply in the user's language.

In use now:

!`sh "${CLAUDE_PLUGIN_ROOT}/scripts/crew" config path 2>&1 || true`

Raw arguments: `$ARGUMENTS`

Run crew as `sh "${CLAUDE_PLUGIN_ROOT}/scripts/crew" <command>`.

- Explain in one or two lines which file is in use: `(project)` is this
  repository's `.agent-crew/providers.toml`, kept out of git because it holds
  the key, like a `.env`; `(global)` is the one in the crew home, shared by
  every repository that has no file of its own. A project's file always wins.
- `open` (or no argument, when the user wants to edit it): open the file in
  their editor. On Windows run `Start-Process notepad "<path>"` in PowerShell,
  or `code "<path>"` when VS Code is installed; on macOS `open -t "<path>"`;
  on Linux `xdg-open "<path>"`. Do not print the file: it may hold a key.
- `move-to-project` / `move-to-global`: move the file (never copy a key to two
  places). To the project: the repository must have `.agent-crew/project.toml`
  (run `crew init` first if not); after moving, run `crew config init --scope
  project` only to make sure `.gitignore` covers it, which leaves an existing
  file alone. To global: move it into the crew home (`crew config path` after
  the move confirms). Refuse to move a file onto an existing one; ask first.
- With no argument and no clear intent, ask once with `AskUserQuestion`:
  open it, move it to this project, or move it to the shared location.

Never type, echo or store a key yourself; if the user needs to enter one,
tell them to paste it into the file you opened, between the quotes of
`api_keys = [""]`.
