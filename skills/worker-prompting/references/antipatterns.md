# What makes workers fail

Each of these cost real runs.

- **Exploration left to the worker.** "Look at how the other tools do it"
  spends the step budget on reads. Inline the one example that matters.
- **A description instead of a contract.** "Add a function that finds the
  nearest colour" gets a different name, signature and error type than the
  task next to it expects. Write the signature.
- **Two languages in one task.** The worker fixes one side, breaks the other,
  and runs out of steps between them.
- **A file shared by two parallel tasks.** Whichever lands second conflicts.
  Give the shared line to one task, or a follow-up task.
- **New files nothing declares.** A Rust module without `mod`, a component
  nothing imports: the build passes without compiling it. Name the
  declaration in the spec; the `rust-modules` check catches the Rust case.
- **Modules named like crates.** A module `png` shadows the `png` crate
  inside its parent; say to use `::png::` for the crate.
- **Shell-written text.** Windows PowerShell 5.1 writes UTF-8 as mojibake.
  Workers write files with their tools, never with shell redirection; the
  `text` check flags damage.
- **Verify that hides failures.** A verify that swallows a panic message or
  skips lint gives a worker nothing to fix. It must print the failure and fail.
- **Tests that create files.** A test writing `out.png` in the repository
  leaves a stray; tests use a temporary directory.
- **Open questions in the prompt.** "Decide whether to cache it" gets a
  question back, or a guess. Decide, and write the decision down.
- **Re-running a stalled task unchanged.** The same prompt stalls the same
  way. Narrow it, or fix the remainder yourself.
