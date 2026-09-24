---
name: setup
description: Set up Agent Crew end to end - find the OpenAI-compatible endpoint on this machine, list its models, write the providers file, write this repository's project file from what it contains, and put `crew` on PATH. Use when the user asks to set up Agent Crew, or before any crew work when Agent Crew reports it is not configured.
argument-hint: "[endpoint URL] [--enable-review-gate|--disable-review-gate]"
allowed-tools: Bash, Read, Edit, Write, AskUserQuestion
---

Set Agent Crew up for the user. Do the work yourself; the user should only
have to choose, never type commands. Reply in the user's language.

Where things stand:

!`sh "${CLAUDE_PLUGIN_ROOT}/scripts/crew" doctor 2>&1 || true`

Endpoints found on this machine:

!`sh "${CLAUDE_PLUGIN_ROOT}/scripts/crew" config detect 2>&1 || true`

Raw arguments: `$ARGUMENTS`

Run crew as `sh "${CLAUDE_PLUGIN_ROOT}/scripts/crew" <command>` (the Bash
tool); it needs nothing on PATH.

1. **Endpoint.** Use the URL in the arguments if one was given. Otherwise use
   an endpoint above whose status is `ok` or `needs-key`, preferring one
   already configured. If none answers, ask the user once which endpoint they
   use (their router's `/v1` URL, or a hosted API such as
   `https://openrouter.ai/api/v1`), and if it is a local router, remind them
   to start it. Re-check a URL with `crew config detect` after they answer.
2. **Models.** `ok` means it answered without a key. For `needs-key` the
   model list needs the key, so ask the user to name the models they want in
   the pool (they can copy them from their router's dashboard). Otherwise
   take the list from `crew config detect`. Everything crew calls comes from
   this pool and nothing else. Ask the user once with `AskUserQuestion`: the
   writer models (fast coding models) and the reviewer model (a careful model
   from a different family than the writers). Recommend a choice from the
   names, labelled `(Recommended)`, and allow several writers, best first.
3. **Write the one config file** (`~/.agent-crew/providers.toml`: endpoint,
   key slot and the model pool):

   ```bash
   sh "${CLAUDE_PLUGIN_ROOT}/scripts/crew" config init --name <provider> --base-url <url> --writer "<a,b>" --reviewer "<c>"
   ```

   It replaces the file only while it is still the template; if the user has
   a configured file, ask before adding `--force`.
4. **Key.** If the endpoint needs one, the file has `api_keys = [""]`. Never
   type, store or repeat a key yourself, even one the user pastes into chat
   (tell them to replace it, since chat keeps it). Give them the file's path
   and ask them to paste the key between the quotes, then wait.
5. **Probe:** `crew config test`. Every route should answer `ok`. Fix what a
   failing one says (an unknown model id, a refused key).
6. **This repository.** If `.agent-crew/project.toml` is missing, run
   `crew init`: it reads the repository and writes verify commands,
   shared directories and checks from what exists. Show the verify commands
   and adjust them if the repository has its own conventions (a verify script,
   a `make check`). A verify must fail loudly when anything fails.
7. **PATH.** If `crew doctor` says `crew not on PATH`, run `crew shim --path`
   so the user can type `crew` in new terminals.
8. **Review gate.** `--enable-review-gate` runs `crew gate enable`;
   `--disable-review-gate` runs `crew gate disable`. Otherwise mention in one
   line that it exists (`/agent-crew:review-gate`).
9. Finish with `crew doctor`, and tell the user in two lines what is set up
   and what crew can do now.
