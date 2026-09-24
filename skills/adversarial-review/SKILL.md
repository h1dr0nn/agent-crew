---
name: adversarial-review
description: Have a crew reviewer model challenge local changes - the approach, its assumptions, and where it fails under real conditions - not only the lines
argument-hint: "[--wait|--background] [--base <ref>] [--scope auto|working-tree|branch] [--model <id>] [focus ...]"
disable-model-invocation: true
allowed-tools: Bash, AskUserQuestion
---

Run an adversarial review with one of the crew's reviewer models. It questions
whether the chosen approach is the right one, what it assumes, and where it
breaks: trust boundaries, data loss, retries and partial failure, races,
empty and malformed input, compatibility. It is not only a stricter pass over
the lines.

Raw arguments: `$ARGUMENTS`

This is review-only. Do not fix anything, apply patches, or say you are about
to. Keep the user's focus text exactly as given; do not soften it.

**Size and mode.** As for `/agent-crew:review`: `--wait` runs in the
foreground and `--background` in a background Bash task. Otherwise check the
size with `git status --short --untracked-files=all` and
`git diff --shortstat HEAD` (or `<base>...HEAD`), and ask once with
`AskUserQuestion`: `Wait for results` and `Run in background`, the recommended
one first with `(Recommended)`. Recommend waiting only for one or two files.

**Run** (drop `--wait` and `--background`; pass everything else unchanged,
focus text last):

```bash
~/.agent-crew/bin/crew adversarial-review <the other arguments>
```

**Output.** Return it verbatim: the verdict (`ship` or `do-not-ship`), then
each finding with its confidence, file and line, risk, impact and fix. Then
stop and ask which, if any, to act on. Do not fix anything unasked.
