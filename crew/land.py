"""Land a finished task on the integration branch as one commit, and clean it up.

The task's whole change - committed or not - is taken relative to the commit
its worktree started from, applied to the main checkout with a three-way merge,
and committed with the message given. History stays linear: one task, one
commit, no merge commits, no leftover branches.
"""

from __future__ import annotations

import pathlib
import subprocess
import tempfile

from crew import config, worktree


class LandError(Exception):
    pass


def _git(root: pathlib.Path, *args: str, stdin: bytes | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, input=stdin)


def land(project: config.Project, task: str, message: str, keep: bool = False) -> str:
    record = worktree.info(task)
    path = pathlib.Path(record["path"])
    # Untracked files cannot conflict with an index apply; modified tracked ones can.
    status = _git(project.root, "status", "--porcelain", "--untracked-files=no")
    if status.stdout.strip():
        raise LandError(f"{project.root} has uncommitted changes to tracked files; commit or stash them first")
    excludes = [f":(exclude){shared}" for shared in project.shared]
    _git(path, "add", "-A", "--", ".", *excludes)
    patch = _git(path, "diff", "--binary", "--cached", record["start"], "--", ".", *excludes).stdout
    if not patch.strip():
        raise LandError(f"task {task} changed nothing")
    applied = _git(project.root, "apply", "--3way", "--index", "-", stdin=patch)
    if applied.returncode != 0:
        raise LandError("the change does not apply cleanly:\n" + applied.stderr.decode("utf-8", errors="replace"))
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", delete=False) as handle:
        handle.write(message if message.endswith("\n") else message + "\n")
        message_path = handle.name
    try:
        committed = _git(project.root, "commit", "-q", "-F", message_path)
    finally:
        pathlib.Path(message_path).unlink(missing_ok=True)
    if committed.returncode != 0:
        raise LandError(committed.stderr.decode("utf-8", errors="replace"))
    head = _git(project.root, "log", "--oneline", "-1").stdout.decode("utf-8", errors="replace").strip()
    if not keep:
        worktree.remove(project, task)
    return head
