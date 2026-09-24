# Changelog

All notable changes to Agent Crew for Claude Code. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## 1.4.1 - 2026-09-24

### Changed
- `/agent-crew:setup` offers the plugin settings first and gives the one
  command that sets them, key included, since Claude Code shows their form
  only at install.

## 1.4.0 - 2026-09-24

### Added
- Plugin settings: when the plugin is enabled, Claude Code asks for the
  endpoint, the API key (stored in the system's secure storage) and,
  optionally, writer and reviewer models. Each session start writes the
  crew's configuration from them, and only when they changed.
- With no models named, they are chosen from the endpoint's list: models
  whose names say fast or code write, embedding and audio models are left
  out, and the reviewer comes from a different family than the writers.
- A providers file written by hand still wins, and the session start says so
  when settings are present.

## 1.3.3 - 2026-09-24

### Fixed
- A rate limit (as opposed to a spent allowance) that resets within five
  minutes is waited out on the same route, reading the provider's
  "reset after 1m 51s"; one that resets later sets the route aside until
  then. A reviewer or worker no longer gives up on a model for a minute's limit.
- `crew init` also keeps `profile.json` out of git.

## 1.3.2 - 2026-09-24

### Fixed
- A successful write counts as progress, so a task that needs many files
  before its first passing verify is no longer stopped as stalled.
- Output is written as UTF-8 whatever the console's code page, so a summary
  with a symbol a model wrote no longer crashes `crew run` at the end.

## 1.3.1 - 2026-09-24

### Fixed
- A calibration probe's worktree takes today's `.agent-crew/` settings and
  scripts, so a probe commit older than the verify script can still verify.
- A plain-text 404 from a router (`404 page not found`, as between restarts)
  is retried; a JSON 404 (an unknown model) is still refused at once.

## 1.3.0 - 2026-09-24

### Added
- `crew calibrate` and `/agent-crew:calibrate`: the repository's work mix
  from its history, then every pool model redoes a small real commit per
  kind under that kind's verify, and says which kinds suit it. Results go to
  `.agent-crew/profile.json`, and `crew run --verify <kind>` tries the
  models that passed that kind first. `crew profile` shows them.

### Fixed
- A router that wraps an upstream 429 in a 502 or 503 is read as an
  exhausted allowance with its reset time, not retried as a transient error.
- Replies with a stray `data: [DONE]` after the JSON, or sent as server-sent
  events to a non-streaming request, are read correctly.

## 1.2.0 - 2026-09-24

### Changed
- One file holds the whole setup: `providers.toml` takes the key itself
  (`api_key` / `api_keys`) next to the endpoint and the model pool. Variables
  and key files still work.
- `crew doctor` counts a PATH entry made after Claude Code started, and only
  compares the pool with the endpoint's models when the endpoint answered.

## 1.1.0 - 2026-09-24

### Added
- Setup without hand-editing: `/agent-crew:setup` finds the endpoint on this
  machine (9router, LiteLLM, Ollama, LM Studio, vLLM), lists its models, asks
  which to use and writes the providers file. Claude runs it by itself when
  crew work is asked for before crew is configured.
- `crew config detect`, and `crew config init --base-url ... --writer ... --reviewer ...`.
- `crew init` reads the repository and writes verify commands (Rust, npm,
  Python, Go), shared directories and checks from what it finds.
- `crew shim --path` puts `crew` on the user's PATH.
- `crew doctor` checks that each endpoint answers and offers the configured models.
- A provider with no key configured (a local router without auth) works
  without one.

### Changed
- The providers file is created at the first session start.
- The author is shown as h1dr0n.

## 1.0.1 - 2026-09-24

### Changed
- The plugin and marketplace author is shown as h1dr0nn.
- At session start the plugin says what setup is still missing (no providers
  file, model ids not set, no API key found) instead of staying silent.

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
