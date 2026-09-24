---
name: land
description: Land a finished Agent Crew task on the integration branch as one commit, after checking it, and clean up its worktree and branch
argument-hint: "<task> [commit message]"
disable-model-invocation: true
allowed-tools: Bash, Read, Write
---

Land the task named first in `$ARGUMENTS`.

The CLI is `~/.agent-crew/bin/crew` (`crew.cmd` from cmd or PowerShell; under `$AGENT_CREW_HOME/bin` when that is set).

Before landing, check it yourself; a worker's `finished` is a claim:

1. `crew result <task>`: the status must be `finished`. If not, stop and
   say what the result shows.
2. `crew check --task <task>` must print `clean`: no text damage, no stray
   files, no Rust module that nothing declares.
3. Run the project's verify for the kind the task used, in the task's
   worktree (the path is in `crew result`); it must pass.
4. Read the diff (`git -C <worktree> diff <branch>...HEAD` plus
   `git -C <worktree> diff`, and new untracked files). Stop and report if it
   touches files outside its task, deletes behaviour, or has tests that do not
   test what their names say.

Then write the commit message to a file: the rest of `$ARGUMENTS` if given,
otherwise a Conventional Commit subject in the imperative, under 72
characters, and a short body saying what and why. End it with any commit
attribution lines this session is configured to add. Then:

```bash
crew land <task> -F <message file>
```

It applies the task's whole change to the integration branch as one commit,
and removes the worktree and its branch. It refuses when the integration
checkout has uncommitted tracked changes; say so rather than stashing them.
Report the commit it printed. Do not push.
