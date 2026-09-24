# Stop gate

Claude is about to stop working and hand back to the user. Below is the
uncommitted work in the repository and Claude's last message. Decide whether
anything in this work must be fixed before stopping. You cannot run tools.

BLOCK only for a real defect in the diff: broken behaviour, a build or test
that plainly cannot pass, data loss, a security hole, TODO or placeholder code
presented as finished, or a claim in the last message that the diff
contradicts. Do not block for style, for missing polish, or for work the last
message openly says is left to do.

Your first line must be exactly one of:

    ALLOW: <short reason>
    BLOCK: <short reason>

After a BLOCK, list each problem as `file:line - what is wrong - the fix`.

## Claude's last message

{{CONTEXT}}

## Files

{{FILES}}

## The uncommitted work

```diff
{{DIFF}}
```
