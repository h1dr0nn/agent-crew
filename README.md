# Agent Crew for Claude Code

Claude plans and judges. A crew of cheap, fast models writes the code. Nothing
lands until a real build and real tests say it works.

Agent Crew is a Claude Code plugin and a small command-line tool. Claude splits
work into small tasks; each task runs in its own git worktree, on a model you
choose from any OpenAI-compatible endpoint, inside a file boundary the worker
cannot write past. The worker may only finish once your verify command passes.
Claude checks the result, has a different model review it, and lands each task
on your branch as one commit.

It was built by running a real multi-phase project this way and fixing every
failure the workers produced. The checks below each exist because a delegated
model did that thing and a build did not catch it.

## What the runtime enforces

- **A file boundary.** A task lists the globs it owns. Writes anywhere else, and
  any path outside the worktree, are refused.
- **Verify before finish.** `finish` is refused until the verify command has
  passed since the last write. A worker cannot report success it did not earn.
- **Agent Crew's own checks,** added to every verify: text damage (mojibake,
  control characters, a byte order mark - a shell reading UTF-8 as a legacy code
  page does this to every non-ASCII character), stray files the task does not
  own (debug output, a test writing into the working copy), and for Rust a new
  source file no `mod` declares, which the compiler never sees.
- **A read budget.** Weak models explore instead of writing. After a run of
  reads they are told to write; later their reads are refused.
- **Context compaction.** Every request resends the conversation, so old tool
  results and the model's own old file contents are elided each step. This is
  most of the token saving.
- **Confined search** inside the worktree or one dependency's sources, never a
  whole drive.
- **Early stop** when a run makes no progress, instead of spending its budget.
- **Failover by allowance.** Providers often run several quotas side by side (a
  rolling free tier, a campaign model with its own). A key that runs out of one
  is skipped for that allowance only, until the reset time the provider gave,
  and the conversation carries on with the next route without losing work.

## Install

In Claude Code:

```
/plugin marketplace add h1dr0nn/agent-crew
/plugin install agent-crew@agent-crew
```

Requires Python 3.11 or later and git. On each session start the plugin writes
a `crew` launcher to `~/.agent-crew/bin` (`crew.cmd` on Windows); add that
directory to your PATH, or call the launcher by its path.

Then run `/agent-crew:crew-setup`, or do it by hand as below.

## Configure providers

`crew config init` writes `~/.agent-crew/providers.toml`:

```toml
[defaults]
writer = "auto"          # or a preference list: "model-a, model-b"
reviewer = "auto"

[[provider]]
name = "router"
base_url = "http://localhost:20128/v1"   # any OpenAI-compatible endpoint
api_key_envs = ["CREW_ROUTER_KEY"]       # or api_key_file = "~/.crew-keys"

[[model]]
id = "fast-coder"
provider = "router"
roles = ["writer"]
allowance = "free"       # models sharing a quota share a label
priority = 10

[[model]]
id = "careful-reviewer"
provider = "router"
roles = ["reviewer", "writer"]
priority = 20
```

Keys never go in this file: name the environment variables that hold them, or
a key file with one key per line. Several keys on one provider are used in
turn. `crew config test` probes every route and records its latency; `auto`
orders models by priority, then by measured latency. `crew config show` prints
the route chain each role will use.

Tip: use a different model for review than for writing, so review is a second
opinion rather than the author reading its own work.

## Configure a repository

`crew init` writes `.agent-crew/project.toml`; commit it.

```toml
branch = "main"
worktrees = "../myrepo-crew"
shared = ["node_modules"]            # heavy ignored dirs linked into each worktree
checks = ["text", "strays", "rust-modules"]
max_steps = 25

[verify]
rust = "cargo fmt && cargo clippy --all-targets -- -D warnings && cargo test"
ts = "npm run typecheck && npm run lint && npm test"
```

A verify command passes when it exits 0 and does not print `RESULT: FAIL`.
Make it format, build, lint and test, and print only what failed: every line
it prints is a token the worker reads.

## Use

Ask Claude to use the crew; the `conductor` skill carries the method. Or by hand:

```bash
crew task new parse-args
crew run prompts/parse-args.md --task parse-args --owns "src/args.rs" --verify rust
crew check --task parse-args
crew land parse-args -m "feat: parse the command-line arguments"
```

A prompt is Markdown with directives that inline what the worker needs, so it
spends no turns reading:

```
@@include src/config.rs#L1-L80
@@grep src/lib.rs ^pub (fn|struct)
@@diff main src/args.rs
```

`crew status` shows running tasks, exhausted keys and model latency;
`crew metrics <log>` shows where a run's time and tokens went. Logs are in
`~/.agent-crew/logs`.

## Commands

| Command | Does |
| --- | --- |
| `crew init` | write `.agent-crew/project.toml` |
| `crew config init\|show\|test\|path` | the providers file |
| `crew task new\|rm\|list NAME` | one worktree per task, shared dirs linked |
| `crew run PROMPT --task NAME --owns GLOBS --verify KIND` | run a worker |
| `crew land NAME -m MSG` | land a task as one commit and remove its worktree |
| `crew check --task NAME` | Agent Crew's checks on a worktree |
| `crew status` | running tasks, exhausted keys, latency, open worktrees |
| `crew pack PROMPT --task NAME` | print a prompt with its directives inlined |
| `crew metrics LOG` | a run's time and token summary |

## Develop

```bash
python -m pytest
```

The tests run the worker loop against a scripted local OpenAI-compatible
server, and the worktree and land flow against a scratch git repository.

## Licence

MIT.
