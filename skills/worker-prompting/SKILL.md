---
name: worker-prompting
description: How to write task prompts that cheap, fast worker models finish in a few steps - scope, exact specs, inlined sources, and the failure modes to design around
user-invocable: false
---

# Prompting crew workers

Workers are weaker than Claude, have a step budget of about 25, and cannot
ask questions. A prompt succeeds when the worker never has to explore or
decide anything: it writes, verifies, fixes what verify reports, and finishes.
Prefer tightening the prompt over a stronger model or a bigger budget.

## Shape

Start from `crew template task`. Every prompt:

1. begins with `@@template rules` (the worker rules: the tools, the boundary,
   verify before finish, no stray files, no questions);
2. says in two or three sentences why the task exists;
3. lists the files it owns, exactly, and nothing it does not own;
4. gives the code contract as code: signatures, types, error codes, names of
   tests. Not a description of them;
5. lists each behaviour as a bullet, with its edge cases;
6. names the verify kind and what the tests must cover;
7. inlines what it builds on: `@@include path#L10-L80` for ranges,
   `@@grep path regex` for scattered lines, `@@diff base paths` for a change
   under review. Inlined sources say "do not read again"; each read the worker
   saves is a step.

## Scope

- One to three files, one language. A Rust and TypeScript change is two tasks.
- If the fix is obvious (a two-character change, the compiler's own
  suggestion), say exactly what to change and where.
- A worker that stalls twice on the same task is telling you the task is
  wrong: split it or specify it further; do not re-run it unchanged.
- Name every new module declaration, registration and export the task must
  add, because a new file nothing declares compiles to a false pass.

## Reviews

A review task owns no files. Start from `crew template task-review`, inline
the spec and the change with `@@diff`, run it with `--role reviewer`, and send
real findings back as a new precise writer task. For the repository you are
standing in, `crew review` and `crew adversarial-review` are one request
without a worktree.

More in [references/recipes.md](references/recipes.md) and
[references/antipatterns.md](references/antipatterns.md).
