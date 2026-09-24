---
name: calibrate
description: Find out what kinds of work this repository does and which model in the crew's pool does each kind best, by replaying small real commits with every model and asking each which work suits it; saves the result to .agent-crew/profile.json, which crew run then uses to pick models. Use after setup in a new repository, when the pool changes, or when the user asks which models suit this project.
argument-hint: "[--kinds rust,ts] [--models a,b] [--per-kind N]"
allowed-tools: Bash, Read
---

Calibrate the crew for this repository. Reply in the user's language.

What it would do:

!`sh "${CLAUDE_PLUGIN_ROOT}/scripts/crew" calibrate --dry-run $ARGUMENTS 2>&1 || true`

Above: the work mix (the share of recent changes per verify kind), the real
commit chosen to replay for each kind, and the models in the pool.

1. Tell the user in two or three lines what will run: each model redoes each
   probe commit in its own worktree, under that kind's verify, so it costs
   models x kinds worker runs (a Rust verify can take minutes each). Models
   whose allowance is exhausted are skipped and marked unavailable.
2. If no probe was found for a kind (no small commit touching only that
   kind), say so; it is simply not measured.
3. Run it in the background (Bash `run_in_background: true`), passing the
   arguments unchanged:

   ```bash
   sh "${CLAUDE_PLUGIN_ROOT}/scripts/crew" calibrate $ARGUMENTS
   ```

4. When it finishes, show `crew profile`: per model, probes passed per kind
   and what it said suits it; per kind, the model order `crew run` will now
   use. A model's own ranking is its opinion; the measured passes decide the
   order. Point out anything telling: a model that passed nothing, a kind no
   model passed (the next tasks of that kind need tighter prompts or a
   stronger model), a model much slower than the rest.
5. Calibration runs again safely: results for models or kinds a later run
   does not reach are kept. Suggest rerunning when the pool changes or an
   exhausted model comes back.
