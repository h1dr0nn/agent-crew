---
name: result-handling
description: How to read and present Agent Crew worker results and reviews, and what to do next with each outcome
user-invocable: false
---

# Handling crew results

**Runs** (`crew run`, `crew result`):

- Keep the status, steps, route and minutes, and the worker's summary with
  its final verify output. Quote file paths, line numbers and errors exactly.
- `finished` means the worker's own verify passed. It is a claim, not a
  check. Before landing: `crew check --task <t>`, the verify run yourself in
  the worktree, the diff read, and a review by another model.
- `max_steps`, `no_progress`, `stalled`: the task was too broad or its spec
  too loose, or the worker hit something it cannot see. Read the final
  verify output. If the remainder is trivial and you can see it (a type
  annotation, a wrong literal, a module named like a crate shadowing it),
  fix it directly. Otherwise write a narrower follow-up task with the exact
  change.
- `no_route`: every key is exhausted or missing. Show `crew status`; do not
  retry in a loop.
- Never turn a failed worker run into silently doing the whole task
  yourself; say what failed and what you are doing about it.

**Reviews** (`crew review`, `crew adversarial-review`, the review gate):

- Present the verdict first, then the findings in severity order, exactly as
  the reviewer gave them. Keep what it marked as an inference as such.
- A review is another model's opinion. Before acting on a finding in the
  conductor's own work, confirm it against the code; drop the ones that do not
  hold and say why.
- When the user asked for the review, stop after presenting it and ask which
  findings to fix. When the review is part of a conducted workflow the user
  already set in motion, send the confirmed findings back to a worker as a
  precise task, then review again.
