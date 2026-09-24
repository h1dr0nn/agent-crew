---
name: review
description: Have a reviewer model from the crew review local git state - the uncommitted work, or a branch against its base - for real defects
argument-hint: "[--wait|--background] [--base <ref>] [--scope auto|working-tree|branch] [--model <id>] [focus ...]"
disable-model-invocation: true
allowed-tools: Bash, AskUserQuestion
---

Run a review with one of the crew's reviewer models.

Raw arguments: `$ARGUMENTS`

This is review-only. Do not fix anything, apply patches, or say you are about
to. Your job is to run the review and return its output.

**Size and mode.** If the arguments contain `--wait`, run in the foreground;
`--background`, in a background Bash task. Otherwise look at the size first:
`git status --short --untracked-files=all` and `git diff --shortstat HEAD` for
the working tree, or `git diff --shortstat <base>...HEAD` for a branch.
Untracked files count as work to review. Only say there is nothing to review
when the scope is actually empty. Recommend waiting when it is one or two
files; otherwise recommend the background. Ask once with `AskUserQuestion`:
`Wait for results` and `Run in background`, the recommended one first with
`(Recommended)`.

**Run** (drop `--wait` and `--background`; pass everything else unchanged):

```bash
~/.agent-crew/bin/crew review <the other arguments>
```

In the background, run it with `run_in_background: true`, say "Crew review
started in the background", and do not wait for it in this turn.

**Output.** Return the review verbatim: the target, the reviewer route, the
verdict and each finding with its file and line. Do not paraphrase or add a
commentary. Then stop and ask which findings, if any, the user wants fixed.
Do not fix them unasked, even an obvious one.
