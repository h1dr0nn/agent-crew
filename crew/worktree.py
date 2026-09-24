"""One git worktree per task, sharing the repository's heavy, ignored directories.

A task runs in its own worktree on its own branch, so parallel tasks cannot
touch each other's files. Directories that are expensive to recreate and are
ignored by git (``node_modules``, a virtualenv, a built sidecar) are linked in
from the main checkout rather than rebuilt: a symlink on POSIX, a junction on
Windows, which needs no administrator rights.

The links are always removed before the worktree is, so removing a worktree
can never delete what a link points at.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess

from crew import config

BRANCH_PREFIX = "crew/"


class WorktreeError(Exception):
    pass


def _git(root: pathlib.Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and result.returncode != 0:
        raise WorktreeError(f"git {' '.join(args)}: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout.strip()


def _record(task: str) -> pathlib.Path:
    directory = config.state_dir() / "worktrees"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{task}.json"


def exists(task: str) -> bool:
    return _record(task).exists()


def info(task: str) -> dict:
    try:
        return json.loads(_record(task).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise WorktreeError(f"no worktree recorded for task {task!r}") from None


def _link(target: pathlib.Path, link: pathlib.Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True)
        if result.returncode != 0:
            raise WorktreeError(f"could not link {link} -> {target}: {result.stderr.strip() or result.stdout.strip()}")
    else:
        link.symlink_to(target, target_is_directory=True)


def _unlink(link: pathlib.Path) -> None:
    if os.name == "nt":
        # rmdir on a junction removes the junction, never what it points at.
        if link.exists() or link.is_symlink():
            subprocess.run(["cmd", "/c", "rmdir", str(link)], capture_output=True)
    elif link.is_symlink():
        link.unlink()


def create(project: config.Project, task: str, base: str | None = None) -> pathlib.Path:
    base = base or project.branch
    path = project.worktrees / task
    if path.exists():
        raise WorktreeError(f"{path} already exists")
    project.worktrees.mkdir(parents=True, exist_ok=True)
    start = _git(project.root, "rev-parse", base)
    _git(project.root, "worktree", "add", "-q", "-B", BRANCH_PREFIX + task, str(path), start)
    for shared in project.shared:
        source = project.root / shared
        if source.exists():
            _link(source, path / shared)
    _record(task).write_text(json.dumps({"task": task, "path": str(path), "start": start,
                                         "branch": BRANCH_PREFIX + task, "root": str(project.root)}), encoding="utf-8")
    return path


def remove(project: config.Project, task: str) -> None:
    record = info(task)
    path = pathlib.Path(record["path"])
    for shared in project.shared:
        _unlink(path / shared)
    if path.exists():
        _git(project.root, "worktree", "remove", "--force", str(path), check=False)
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    _git(project.root, "worktree", "prune", check=False)
    _git(project.root, "branch", "-D", record["branch"], check=False)
    _record(task).unlink(missing_ok=True)


def tasks() -> list[dict]:
    directory = config.state_dir() / "worktrees"
    found = []
    for path in sorted(directory.glob("*.json")) if directory.exists() else []:
        try:
            found.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return found
