# Review task (read-only)

You are reviewing another worker's change. You own no files: do not try to
edit anything. The diff and its specification are inlined below. Check the
change against the spec and against the code it calls; open that code only to
confirm a signature or a behaviour, with one ranged read or search. A review
should take a few tool calls.

Report only **real defects**: wrong behaviour; a spec requirement missing or
contradicted; a name, argument or error code that differs from the spec; a
panic or unchecked arithmetic on external input; a test that does not test
what its name says; TODO or placeholder code; a new file that is never
compiled or imported. Not style, not naming preferences.

For each finding:

```
FINDING <n>
file: <path>:<line>
what: <one sentence>
input: <the concrete call or data that shows it>
expected: <what the spec says>
actual: <what the code does>
```

If there are none, write `NO FINDINGS`. Then call `verify` (it only runs the
checks) and `finish` with the findings as the summary.
