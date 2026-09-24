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

Then run `/agent-crew:setup`. Claude finds the endpoint on your machine
(9router, LiteLLM, Ollama, LM Studio, vLLM, or a hosted API you name), lists
its models, asks you which to use, writes both files and puts `crew` on your
PATH. You only choose, and set a key variable if the endpoint needs one. Until
crew is configured, each session start says so, and Claude runs the setup
itself when you ask for crew work. The files it writes are described below if
you prefer to edit them. `/agent-crew:doctor`
says what is still missing.

## Configure providers

`~/.agent-crew/providers.toml` (created at the first session start;
`crew config init --base-url URL --writer A,B --reviewer C` fills it in, and
`crew config detect` shows what answers on this machine):

```toml
[defaults]
writer = "auto"          # or a preference list: "model-a, model-b"
reviewer = "auto"

[[provider]]
name = "router"
base_url = "http://localhost:20128/v1"   # any OpenAI-compatible endpoint
api_keys = ["sk-..."]                    # or api_key_envs = ["VAR"], api_key_file = "~/keys.txt"

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

This one file holds the whole setup. It lives in your home directory, outside
every repository, so the key in it is never committed; keep it out of
anything you share. The `[[model]]` entries are the pool: crew calls those
models and no others. `api_keys = []` means the endpoint needs no key. Several keys on one provider are used in
turn. `crew config test` probes every route and records its latency; `auto`
orders models by priority, then by measured latency. `crew config show` prints
the route chain each role will use.

Tip: use a different model for review than for writing, so review is a second
opinion rather than the author reading its own work.

## Configure a repository

`crew init` writes `.agent-crew/project.toml` from what the repository has
(Cargo, npm scripts, pyproject, go.mod, heavy ignored directories); check the
verify commands and commit it.

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

Ask Claude to use the crew ("plan this and have the crew build it"); the
`conductor` skill carries the method, and Claude picks up `plan`, `dispatch`,
`worker-prompting` and the `crew-worker` subagent as it goes. Or drive it
yourself with the skills below, or the CLI:

```bash
crew run .agent-crew/prompts/parse-args.md --task parse-args --new-task --owns "src/args.rs" --verify rust
crew result parse-args
crew check --task parse-args
crew review --base main
crew land parse-args -m "feat: parse the command-line arguments"
```

A prompt is Markdown with directives that inline what the worker needs, so it
spends no turns reading:

```
@@template rules
@@include src/config.rs#L1-L80
@@grep src/lib.rs ^pub (fn|struct)
@@diff main src/args.rs
```

`@@template rules` inlines the worker rules; `crew template` lists the
built-in templates (a task skeleton, a review task, and the review and gate
prompts). Keep prompts in `.agent-crew/prompts/`, which `crew init` keeps out
of git.

## Skills

Invoke as `/agent-crew:<name>`.

| Skill | Does |
| --- | --- |
| `setup` | the providers file, key variables, this repository's project file; `--enable-review-gate` |
| `doctor` | what is missing, and the fix for each |
| `plan` | waves of small tasks, no two in a wave writing the same file, written into the repository |
| `dispatch` | one bounded task to one worker, in the foreground or the background |
| `status` | running workers, exhausted keys and when they reset, latency, open worktrees |
| `result` | a finished run: status, steps, route, time, tokens, summary, final verify |
| `cancel` | stop a running worker |
| `review` | a reviewer model on the uncommitted work or a branch: real defects only |
| `adversarial-review` | the same, challenging the approach, assumptions and failure modes |
| `land` | check a finished task, then land it as one commit |
| `review-gate` | turn the stop-time review gate on or off |
| `calibrate` | which pool model does each kind of this repository's work best, measured on its own commits |

Claude also uses `conductor` (the whole method), `worker-prompting` (how to
write tasks weak models finish) and `result-handling` on its own, and the
`crew-worker` subagent hands a bounded task to a worker from a subagent.

## Calibration

`crew calibrate` (or `/agent-crew:calibrate`) reads the repository's history
for its work mix, the share of recent change in each verify kind. For each
kind it picks a small real commit that touches only that kind and replays it:
a worktree starts from the commit with its code files put back as they were
before it (the commit's tests stay, and fail until the change is made again),
and every model in the pool is asked to redo it under that kind's verify.
Each model is also asked which of the kinds it thinks suit it. The results go
to `.agent-crew/profile.json`: passes, steps, minutes and tokens per model
and kind. From then on `crew run --verify <kind>` tries the models that
passed that kind first, best first. `crew profile` shows the table; `--dry-run`
shows the mix and the probes without running anything.

## Stop-time review gate

Off by default. `crew gate enable` (or `/agent-crew:review-gate enable`) turns
it on for one repository: each time Claude is about to stop with uncommitted
work there, a reviewer model reads the work and Claude's last message and
answers ALLOW or BLOCK. A BLOCK keeps Claude going, with the reasons as its
next instruction. It never fires twice in a row, does nothing when there is
nothing uncommitted, and lets the stop through when no reviewer answers. Each
stop costs one reviewer request and its wait.

`crew status` shows running tasks, exhausted keys and model latency;
`crew metrics <log>` shows where a run's time and tokens went. Logs are in
`~/.agent-crew/logs`.

## Commands

| Command | Does |
| --- | --- |
| `crew init` | write `.agent-crew/project.toml` |
| `crew config init\|show\|test\|path\|detect` | the providers file; `detect` finds endpoints and models |
| `crew task new\|rm\|list NAME` | one worktree per task, shared dirs linked |
| `crew run PROMPT --task NAME --owns GLOBS --verify KIND` | run a worker (`-` reads the prompt from stdin, `--new-task` creates the worktree) |
| `crew result [NAME]` | a finished run's outcome and summary |
| `crew cancel NAME` | stop a running worker |
| `crew review [--base REF] [--scope ...] [focus]` | one reviewer request on local changes |
| `crew adversarial-review [...]` | the same, challenging the approach |
| `crew gate enable\|disable\|status` | the stop-time review gate |
| `crew land NAME -m MSG` | land a task as one commit and remove its worktree |
| `crew check --task NAME` | Agent Crew's checks on a worktree |
| `crew status` | running tasks, exhausted keys, latency, open worktrees |
| `crew pack PROMPT --task NAME` | print a prompt with its directives inlined |
| `crew metrics LOG` | a run's time and token summary |
| `crew template [NAME]` | list or print the built-in templates |
| `crew calibrate [--kinds ...] [--models ...] [--dry-run]` | measure the pool on this repository's own commits |
| `crew profile` | what calibration measured, and the model order per kind |
| `crew doctor` | check the whole setup |
| `crew shim [--path]` | rewrite the launcher; `--path` puts it on PATH |

## Develop

```bash
python -m pytest
```

The tests run the worker loop against a scripted local OpenAI-compatible
server, and the worktree and land flow against a scratch git repository.

## Licence

MIT.
