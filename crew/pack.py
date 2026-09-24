"""Prompt packing: inline what a task needs, so the worker spends no turns reading it.

A task prompt may contain directives, each on a line of its own:

    @@include <path>             the whole file, with line numbers
    @@include <path>#L10-L80     lines 10 to 80
    @@grep <path> <regex>        the matching lines, with their numbers
    @@diff <base> [paths...]     `git diff <base>...HEAD` plus uncommitted changes
    @@template <name>            a built-in template (`crew template` lists them; `rules` is the worker rules)

Relative paths resolve against the worktree. A missing file is inlined as a
note rather than failing, because a task may be the one creating it.
"""

from __future__ import annotations

import pathlib
import re
import subprocess

from crew import config

FENCE = {".rs": "rust", ".ts": "ts", ".tsx": "tsx", ".js": "js", ".json": "json", ".md": "md",
         ".toml": "toml", ".py": "python", ".css": "css", ".go": "go", ".java": "java", ".cs": "csharp"}


def _resolve(root: pathlib.Path, raw: str) -> pathlib.Path:
    path = pathlib.Path(raw)
    return path if path.is_absolute() else root / path


def include(root: pathlib.Path, spec: str) -> str:
    raw, _, span = spec.partition("#")
    path = _resolve(root, raw)
    if not path.exists():
        return f"(`{raw}` does not exist yet.)\n"
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    start, end = 1, len(lines)
    match = re.fullmatch(r"L(\d+)-L(\d+)", span) if span else None
    if match:
        start, end = int(match[1]), min(int(match[2]), len(lines))
    body = "\n".join(f"{n:5} {lines[n - 1]}" for n in range(start, end + 1))
    label = raw + (f" lines {start}-{end}" if match else f" ({len(lines)} lines)")
    return f"`{label}` — already inlined, do not read it again:\n```{FENCE.get(path.suffix, '')}\n{body}\n```\n"


def grep(root: pathlib.Path, spec: str) -> str:
    raw, _, pattern = spec.partition(" ")
    path = _resolve(root, raw)
    if not path.exists():
        return f"(`{raw}` does not exist yet.)\n"
    regex = re.compile(pattern.strip())
    hits = [f"{n:5} {line}" for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1)
            if regex.search(line)]
    return f"`{raw}` lines matching `{pattern.strip()}`:\n```\n" + "\n".join(hits) + "\n```\n"


def diff(root: pathlib.Path, spec: str) -> str:
    base, *paths = spec.split()

    def git(*args: str) -> str:
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                              encoding="utf-8", errors="replace").stdout

    body = git("diff", f"{base}...HEAD", "--", *paths) + git("diff", "--", *paths)
    return f"The change under review (`git diff {base}...HEAD`), already inlined:\n```diff\n{body}\n```\n"


TEMPLATES = pathlib.Path(__file__).resolve().parent / "templates"


def template(name: str) -> str:
    path = TEMPLATES / f"{name.strip()}.md"
    if not path.is_file():
        known = ", ".join(sorted(p.stem for p in TEMPLATES.glob("*.md")))
        raise config.ConfigError(f"no template {name!r}; known: {known}")
    return path.read_text(encoding="utf-8")


def pack(prompt: str, root: pathlib.Path) -> str:
    out = []
    for line in prompt.splitlines():
        if line.startswith("@@include "):
            out.append(include(root, line[len("@@include "):].strip()))
        elif line.startswith("@@grep "):
            out.append(grep(root, line[len("@@grep "):].strip()))
        elif line.startswith("@@template "):
            out.append(template(line[len("@@template "):]))
        elif line.startswith("@@diff "):
            out.append(diff(root, line[len("@@diff "):].strip()))
        else:
            out.append(line)
    return "\n".join(out)
