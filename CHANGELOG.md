# Changelog

All notable changes to Agent Crew for Claude Code. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## 1.0.0 - 2026-09-24

The first public release.

### Workers
- `crew run`: one model, one task, one git worktree, a file boundary it cannot
  write past, and a verify it must pass before it may finish. Prompts from a
  file or stdin; `--new-task` creates the worktree.
- Routes as (model, key) pairs, ordered by priority then measured latency;
  exhaustion tracked per key and allowance until the provider's reset time;
  failover mid-conversation.
- Agent Crew's own checks: text damage, stray files, Rust modules nothing declares.
- A read budget, context compaction, a stall stop, and prompt packing with
  `@@include`, `@@grep`, `@@diff` and `@@template`.
- `crew result`, `crew status` and `crew cancel`: a cancel stops the worker at its
  next step, or stops it and its verify's whole process tree, and never
  signals a process whose pid was reused.
- `crew land`: the task's whole change as one commit on the integration branch.

### Reviews
- `crew review` and `crew adversarial-review`: one reviewer-model request on the
  uncommitted work or a branch, with failover across reviewer routes.
- The stop-time review gate (`crew gate`), off by default: a reviewer reads
  the uncommitted work each time Claude stops, and can block the stop.

### Plugin
- Skills: `setup`, `doctor`, `plan`, `dispatch`, `status`, `result`, `cancel`,
  `review`, `adversarial-review`, `land`, `review-gate`, and for Claude
  `conductor`, `worker-prompting` and `result-handling`.
- The `crew-worker` subagent, a SessionStart hook that keeps the `crew`
  launcher current, and the Stop hook for the review gate.
- `crew doctor` and `crew template`.
