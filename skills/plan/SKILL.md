---
name: plan
description: Split a feature or phase into waves of small parallel tasks for crew workers, each owning files no other task in its wave touches, and write the plan into the repository. Use before dispatching multi-task work to the crew.
argument-hint: "[what to build, or a spec file]"
allowed-tools: Read, Grep, Glob, Write, Edit, Bash
---

Plan the work in `$ARGUMENTS` for a crew of worker models, and write the plan
to a file in the repository (`docs/plan/<name>.md`, or wherever this
repository keeps plans), not into chat.

1. **Read first.** Read the spec and the code it touches until you can name
   every file that changes and every signature that crosses a file boundary.
2. **Waves.**
   - *Contract wave*: the shared shapes others code against: types, function
     signatures, error codes, registries, locale keys, config. Small,
     sequential if needed, landed and gated before anything builds on them.
   - *Parallel waves*: tasks that only depend on landed contracts. **Tasks in
     one wave never write the same file.** Where two would (a registry, an
     index, a locale file), give the line to one of them, or add a small
     follow-up task that adds all the lines.
   - *Integration wave*: wiring, docs, the end-to-end run.
3. **Each task** gets an id, one line of intent, the files it owns (one to
   three, in one language), the verify kind, what its tests cover, and the
   sources to inline. A task a weak model cannot finish in about ten steps is
   too big: split it.
4. **Gates.** After each wave, the full gate on the integration branch:
   everything CI runs. The next wave starts only when it passes.
5. **Check the plan.** List the owned files per wave and confirm no file
   appears twice in a wave. Name the risks: tasks likely to need a stronger
   model, spec gaps you had to decide, anything that needs the user.

Finish by saying where the plan is and which wave to dispatch first.
