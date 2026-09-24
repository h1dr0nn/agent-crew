---
name: conductor
description: Conduct a crew of delegated coding workers with Agent Crew. Use when the user wants work split into tasks and done by other models (cheap or fast OpenAI-compatible ones) while Claude plans, dispatches, reviews and verifies - "use the crew", "delegate this", "have the workers do it", "orchestrate", or a multi-phase plan executed by agents.
---

# Conducting a crew

You plan and judge; workers write. A worker is `crew run`: one model, one task,
one git worktree, a boundary it cannot write outside, and a verify command it
must pass before it may finish. Your job is to make each task small and
complete enough that a weak model finishes it in a handful of steps, then to
check the result yourself before it lands.

The CLI is `crew` (written to `~/.agent-crew/bin` by the plugin; on Windows
`crew.cmd`). If it is not on PATH, call it by that path. `crew --help` lists
every command.

## 0. Setup, once

- `crew config init`, then edit `~/.agent-crew/providers.toml` (providers,
  models, roles, allowances), set the key environment variables it names, and
  `crew config test`. `crew config show` prints the route chain per role.
- In the repository: `crew init`, then set `[verify]` (one command per kind,
  e.g. `rust`, `ts`, `docs`), `shared` (heavy ignored dirs to link into
  worktrees, e.g. `node_modules`), and `checks` in `.agent-crew/project.toml`.
  A verify command should format, build, lint and test, print only failures,
  and exit non-zero (or print `RESULT: FAIL`) when anything fails.

## 1. Plan

Write the plan into the repository (a phase file), not into chat. Split into
waves: a contract wave that fixes the shapes others code against, then
parallel tasks, then integration. **Parallel tasks never share a file**; give
each task an explicit list of the files it owns. When two tasks would need the
same file (a registry, a locale file), give one of them the line, or sequence
them.

## 2. Write each task prompt

A prompt is Markdown. Make it small and self-contained:

- one to three files owned, named exactly;
- the exact signatures, types and error codes to write, not a description;
- the code it builds on **inlined** with directives, so the worker never has
  to explore:
  `@@include path#L10-L80`, `@@grep path regex`, `@@diff main paths...`;
- the verify kind to use, and what its tests must cover.

Weak models stall on broad tasks and on tasks spanning languages. If a task
touches Rust and TypeScript, split it. If a fix is obvious (a two-character
change, a compiler's own suggestion), say exactly what to change.

## 3. Dispatch

```bash
crew task new B1
crew run prompts/B1.md --task B1 --owns "src/tools/orientation.rs" --verify rust --role writer
```

Run several at once in the background; routes are spread across keys and
models automatically, and a key that runs out of an allowance is skipped until
it resets. `crew status` shows what is running, what is exhausted, and how fast
each model has been.

## 4. Check every result yourself

A worker's "done" is a claim. Before landing:

1. Read the final status and summary `crew run` printed. `finished` means its
   verify passed; `no_progress`, `max_steps` or `stalled` mean it did not, and
   the summary carries the final verify output.
2. Run the verify yourself in the worktree, and `crew check --task B1`.
3. Read the diff. Look for work outside the spec, deleted behaviour, tests that
   do not test what their names say, and a "clean build" that never compiled
   the new code.
4. Have another model review it: a prompt with `@@diff main <paths>` and the
   spec, `--role reviewer`, no files owned. Send real findings back to a writer
   as a new, precise task. Repeat until clean.

When a worker is stuck on something you can see (a module named like a crate
shadowing it, a type annotation, a wrong test literal), fix the two characters
yourself rather than spend another run on it.

## 5. Land

```bash
crew land B1 -F msg.txt
```

applies the task's whole change to the integration branch as one commit and
removes its worktree and branch. History stays linear. After each wave, run the
full gate on the integration branch - everything CI runs - before starting the
next.

## 6. Run the product, not only the tests

Reviews and unit tests miss things a real run catches. After integration,
drive the built thing end to end the way a user or client would, and treat
what that finds as new tasks.
