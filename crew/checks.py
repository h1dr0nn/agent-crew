"""Checks Agent Crew runs itself after every verify, whatever the project's own command does.

Each one catches a failure that a delegated model produced in practice and that
a build and a test run did not catch:

* ``text``: mojibake, control characters, replacement characters or a byte
  order mark that the change introduced (a shell reading UTF-8 as a legacy code
  page and writing it back does this to every non-ASCII character);
* ``strays``: files that appeared in the worktree and are not files the task
  owns (debug output, scratch scripts, a test writing into the working copy);
* ``rust-modules``: a new ``.rs`` file no ``mod`` declares, which the compiler
  never sees, so the build passes without having built it.
"""

from __future__ import annotations

import fnmatch
import pathlib
import re
import subprocess
from typing import Callable

# Build and test artefacts a verify command legitimately leaves behind; never
# counted as strays. A project adds its own with `ignore_strays` in project.toml.
ARTEFACTS = ["__pycache__/*", "*/__pycache__/*", "*.pyc", ".pytest_cache/*", "*/.pytest_cache/*",
             ".mypy_cache/*", ".ruff_cache/*", "target/*", "*/target/*", "node_modules/*", "*/node_modules/*",
             "dist/*", "build/*", "coverage/*", ".coverage", "*.egg-info/*", ".turbo/*", ".next/*"]

MOJIBAKE = ["Â§", "Â©", "Â·", "Â°", "Â ", "â€", "Ã¡", "Ã ", "Ã©", "Ã¨", "Ã¶", "Ã¼", "Ä‘", "á»", "áº"]


def _git(root: pathlib.Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace").stdout


def changed_files(root: pathlib.Path, base: str) -> list[str]:
    tracked = _git(root, "diff", "--name-only", base).split("\n")
    untracked = _git(root, "ls-files", "-o", "--exclude-standard").split("\n")
    return sorted({p.strip() for p in tracked + untracked if p.strip()})


def _damage(text: str) -> dict[str, int]:
    return {
        "mojibake": sum(text.count(marker) for marker in MOJIBAKE),
        "control": sum(1 for ch in text if ord(ch) < 32 and ch not in "\n\t\r"),
        "replacement": text.count("\ufffd"),
        "bom": text.count("\ufeff"),
    }


def text_damage(root: pathlib.Path, base: str, suffixes: list[str]) -> list[str]:
    problems = []
    for path in changed_files(root, base):
        if not path.endswith(tuple(suffixes)):
            continue
        file = root / path
        if not file.is_file():
            continue
        now = _damage(file.read_bytes().decode("utf-8", errors="replace"))
        before_text = subprocess.run(["git", "-C", str(root), "show", f"{base}:{path}"], capture_output=True).stdout
        before = _damage(before_text.decode("utf-8", errors="replace"))
        grown = {kind: now[kind] - before[kind] for kind in now if now[kind] > before[kind]}
        if grown:
            detail = ", ".join(f"{count} {kind}" for kind, count in grown.items())
            problems.append(f"text damage in {path}: {detail} - rewrite it with write_file/replace, not a shell")
    return problems


def strays(root: pathlib.Path, owned: Callable[[str], bool], ignore: list[str] | None = None) -> list[str]:
    patterns = ARTEFACTS + list(ignore or [])
    found = []
    for line in _git(root, "status", "--porcelain", "--untracked-files=all").splitlines():
        if line.startswith("?? "):
            path = line[3:].strip().strip('"')
            if owned(path) or any(fnmatch.fnmatch(path, pattern) for pattern in patterns):
                continue
            found.append(path)
    return found


MOD_LINE = re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?mod\s+([A-Za-z_][A-Za-z0-9_]*)\s*;", re.M)


def unreferenced_rust_modules(root: pathlib.Path, base: str) -> list[str]:
    """New .rs files whose module name no sibling or parent file declares."""
    problems = []
    for path in changed_files(root, base):
        if not path.endswith(".rs"):
            continue
        file = root / path
        if not file.is_file() or file.name in ("main.rs", "lib.rs", "mod.rs", "build.rs"):
            continue
        if "tests" in file.parts or "benches" in file.parts or "examples" in file.parts:
            continue
        name = file.stem
        directory = file.parent
        declarers = [directory / "mod.rs", directory / "lib.rs", directory / "main.rs", directory.parent / f"{directory.name}.rs"]
        declared = False
        for candidate in declarers:
            if candidate.is_file() and name in MOD_LINE.findall(candidate.read_text(encoding="utf-8", errors="replace")):
                declared = True
                break
        if not declared:
            problems.append(f"{path} is never compiled: no `mod {name};` in {directory.name}/mod.rs, lib.rs or main.rs")
    return problems


def run(root: pathlib.Path, base: str, names: list[str], suffixes: list[str], owned: Callable[[str], bool],
        ignore: list[str] | None = None) -> list[str]:
    problems: list[str] = []
    if "text" in names:
        problems += text_damage(root, base, suffixes)
    if "strays" in names:
        problems += [f"stray file (not owned by this task): {p}" for p in strays(root, owned, ignore)]
    if "rust-modules" in names:
        problems += unreferenced_rust_modules(root, base)
    return problems
