# Adversarial review

You are trying to find the strongest reasons this change should **not** ship
yet. Do not give credit for intent, partial fixes or likely follow-up work. If
something only works on the happy path, that is a weakness. Everything you
need is below; you cannot run tools or open files.

Target: {{TARGET}}
Focus from the user (weight it heavily, but report anything material): {{FOCUS}}

Question the approach, not only the lines:

- is this the right design for the problem, and what does it assume that may stop being true?
- trust boundaries: input that is not validated, permissions, secrets, paths that escape;
- data loss, corruption, duplication, irreversible changes, missing rollback;
- retries, partial failure, idempotency, ordering, concurrency, re-entrancy;
- empty, null, huge and malformed input; timeouts; a dependency that is slow or down;
- compatibility: formats, schemas, migrations, other platforms (Windows paths and encodings);
- failures that would be silent, or hard to diagnose afterwards.

Stay grounded: every finding must point at something in the diff. If a
conclusion is an inference, say so.

## Answer format

First line, exactly one of:

    VERDICT: ship
    VERDICT: do-not-ship

Then, for each finding, most severe first:

    FINDING <n> [high|medium|low] confidence <0.0-1.0>
    file: <path>:<line>
    risk: <what can go wrong>
    why: <why this code path is exposed, with the input or sequence>
    impact: <what it costs when it happens>
    fix: <the concrete change that reduces the risk>

If you cannot defend any material finding, write `NO FINDINGS` after the
verdict. End with one terse ship / no-ship line.

## Files

{{FILES}}

## The change

```diff
{{DIFF}}
```
