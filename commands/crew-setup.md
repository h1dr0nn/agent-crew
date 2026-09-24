---
description: Set up Agent Crew - the providers file, the key variables, and this repository's project file
argument-hint: "[provider base URL]"
---

Set up Agent Crew for the user, step by step, checking each step works before the next.

1. Make sure the launcher exists: run `python3 -m crew shim` or `python -m crew shim`
   with `PYTHONPATH` set to this plugin's directory if `~/.agent-crew/bin/crew`
   (or `crew.cmd` on Windows) is missing, and use that path for the rest.
2. Run `crew config init` if `~/.agent-crew/providers.toml` does not exist. Open
   it and fill it in with the user: their provider's base URL ($ARGUMENTS if
   given), the environment variable names their keys are in, and the models
   they want as writers and reviewers, with a priority each. Never write a key
   into the file, into chat, or into any command line: ask the user to set the
   variables themselves (`setx NAME value` on Windows, their shell profile
   elsewhere) and to restart the terminal if needed.
3. Run `crew config test` and show the result. A failing route says why:
   refused key, unknown model, exhausted allowance.
4. In the current repository, run `crew init` if `.agent-crew/project.toml` is
   missing, and fill in `[verify]` commands for the languages the repository
   uses (they should format, build, lint and test, and fail loudly), `shared`
   directories to link into worktrees, and `checks`.
5. Finish with `crew status`.
