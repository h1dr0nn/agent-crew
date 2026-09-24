"""The worker: a small tool-using loop for one delegated task in one worktree.

It offers only what a delegated task needs, and enforces the task's boundary
itself rather than trusting the model to:

* writes are refused outside the worktree and outside the globs the task owns;
* recursive search stays inside the worktree or one dependency's sources;
* `finish` is refused until `verify` has passed since the last write, and while
  files the task does not own have appeared in the worktree;
* reading is budgeted: after a run of reads with no write the model is told to
  write, and later its reads are refused;
* the context is compacted every step, because every request resends it:
  old tool results and the model's own old file contents are elided;
* a run that makes no progress stops early instead of spending its budget.

When a route runs out of allowance mid-task, the conversation moves to the next
route and carries on; nothing already done is lost.
"""

from __future__ import annotations

import fnmatch
import json
import os
import pathlib
import re
import subprocess
import time
from dataclasses import dataclass, field

from crew import checks, providers

SYSTEM = """You are a coding worker completing one delegated task in a git worktree.
Work with the tools, not with prose. The task prompt inlines the files you need;
do not re-read them. Write whole files with write_file, and small edits with
replace. Run verify after writing, fix what it reports, and repeat until it
passes. Nobody will answer questions. When verify passes, call finish with a
short summary. Every tool call costs time: aim for as few as the task allows."""

TOOLS = [
    {"name": "read_file", "description": "Read a file, optionally a 1-based inclusive line range. Returns numbered lines.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "start": {"type": "integer"}, "end": {"type": "integer"}}, "required": ["path"]}},
    {"name": "search", "description": "Regex search in a file or a directory of the worktree (recursive). Returns path:line: text, at most 60 hits.",
     "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}, "path": {"type": "string"}}, "required": ["pattern", "path"]}},
    {"name": "write_file", "description": "Create or overwrite a file with the complete content given.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}},
    {"name": "replace", "description": "Replace exactly one occurrence of old with new in a file. Fails if old is absent or ambiguous.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old": {"type": "string"}, "new": {"type": "string"}}, "required": ["path", "old", "new"]}},
    {"name": "delete_file", "description": "Delete a file this task owns.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "verify", "description": "Format, build and test the task. Prints only failures, then RESULT: PASS or FAIL.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "finish", "description": "End the task. Refused until verify has passed since your last write.",
     "parameters": {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]}},
]

READ_TOOLS = ("read_file", "search")
WRITE_TOOLS = ("write_file", "replace", "delete_file")
SKIP_DIRS = {"node_modules", "target", ".git", ".venv", "dist", "build", "__pycache__"}


@dataclass
class Settings:
    max_steps: int = 25
    nudge_after_reads: int = 8
    refuse_after_reads: int = 14
    stall_steps: int = 14
    keep_tool_results: int = 6
    keep_calls: int = 2
    compact_to: int = 400
    verify_timeout: int = 1800
    search_roots: list[pathlib.Path] = field(default_factory=lambda: [pathlib.Path.home() / ".cargo" / "registry" / "src"])


class Boundary:
    """What the worker may read, search and write."""

    def __init__(self, root: pathlib.Path, owns: list[str], search_roots: list[pathlib.Path]):
        self.root = root.resolve()
        self.owns = [o.strip().replace("\\", "/") for o in owns if o.strip()]
        self.search_roots = [r.resolve() for r in search_roots if r.exists()]

    def path(self, raw: str) -> pathlib.Path:
        path = pathlib.Path(raw)
        return (path if path.is_absolute() else self.root / path).resolve()

    def searchable(self, raw: str) -> pathlib.Path:
        path = self.path(raw)
        if path == self.root or self.root in path.parents:
            return path
        for root in self.search_roots:
            if root in path.parents and len(path.relative_to(root).parts) >= 2:
                return path
        raise PermissionError(f"search {raw} is outside the worktree; search inside it, or inside one dependency")

    def owned(self, relative: str) -> bool:
        return any(fnmatch.fnmatch(relative, pattern) for pattern in self.owns)

    def writable(self, raw: str) -> pathlib.Path:
        path = self.path(raw)
        try:
            relative = path.relative_to(self.root).as_posix()
        except ValueError:
            raise PermissionError(f"{raw} is outside the worktree") from None
        if not self.owned(relative):
            raise PermissionError(f"{relative} is not a file this task owns ({', '.join(self.owns) or 'none'})")
        return path


def run_tool(name: str, args: dict, boundary: Boundary, verify, settings: Settings) -> tuple[str, bool]:
    """Runs one tool. Returns (output, failed)."""
    try:
        if name == "read_file":
            lines = boundary.path(args["path"]).read_text(encoding="utf-8", errors="replace").splitlines()
            start = max(1, int(args.get("start") or 1))
            end = min(len(lines), int(args.get("end") or len(lines)))
            return "\n".join(f"{n:5} {lines[n - 1]}" for n in range(start, end + 1))[:60000], False
        if name == "search":
            root = boundary.searchable(args["path"])
            regex = re.compile(args["pattern"])
            files = [root] if root.is_file() else (p for p in root.rglob("*") if p.is_file() and not SKIP_DIRS.intersection(p.parts))
            hits = []
            for file in files:
                try:
                    text = file.read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    continue
                for n, line in enumerate(text.splitlines(), 1):
                    if regex.search(line):
                        shown = file.relative_to(boundary.root) if boundary.root in file.parents else file
                        hits.append(f"{shown}:{n}: {line.strip()[:200]}")
                        if len(hits) >= 60:
                            return "\n".join(hits), False
            return "\n".join(hits) or "no matches", False
        if name == "write_file":
            path = boundary.writable(args["path"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(args["content"].replace("\r\n", "\n"), encoding="utf-8", newline="\n")
            return f"wrote {path.relative_to(boundary.root).as_posix()} ({args['content'].count(chr(10)) + 1} lines)", False
        if name == "replace":
            path = boundary.writable(args["path"])
            text = path.read_text(encoding="utf-8")
            count = text.count(args["old"])
            if count != 1:
                return f"old text found {count} times; it must match exactly once (include more surrounding lines)", True
            path.write_text(text.replace(args["old"], args["new"]), encoding="utf-8", newline="\n")
            return "replaced", False
        if name == "delete_file":
            path = boundary.writable(args["path"])
            path.unlink()
            return f"deleted {path.relative_to(boundary.root).as_posix()}", False
        if name == "verify":
            return verify()
        return f"unknown tool {name}", True
    except PermissionError as error:
        return f"refused: {error}", True
    except (OSError, re.error, KeyError, ValueError, subprocess.TimeoutExpired) as error:
        return f"error: {error}", True


def make_verify(command: str | None, boundary: Boundary, base: str, check_names: list[str], suffixes: list[str], timeout: int,
                ignore_strays: list[str] | None = None):
    """The verify tool: the project's command, then Agent Crew's own checks.
    It passes when the command exits 0 without printing RESULT: FAIL, and every
    check is clean."""

    def verify() -> tuple[str, bool]:
        parts, failed = [], False
        if command:
            result = subprocess.run(command, shell=True, cwd=boundary.root, capture_output=True, text=True,
                                    encoding="utf-8", errors="replace", timeout=timeout)
            output = (result.stdout + result.stderr).strip()
            parts.append(output[-10000:])
            failed = result.returncode != 0 or "RESULT: FAIL" in output
        problems = checks.run(boundary.root, base, check_names, suffixes, owned=boundary.owned, ignore=ignore_strays)
        if problems:
            parts.append("== Agent Crew checks\n" + "\n".join(problems))
            failed = True
        parts.append("RESULT: FAIL" if failed else "RESULT: PASS")
        return "\n".join(parts), failed

    return verify


def compact(messages: list, settings: Settings) -> None:
    """Keeps the resent context from growing without bound."""
    tool_indices = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    for i in tool_indices[:-settings.keep_tool_results]:
        content = messages[i]["content"]
        if len(content) > settings.compact_to:
            messages[i]["content"] = content[:settings.compact_to] + f"\n[... {len(content) - settings.compact_to} chars elided; re-read only if you must]"
    call_indices = [i for i, m in enumerate(messages) if m.get("role") == "assistant" and m.get("tool_calls")]
    for i in call_indices[:-settings.keep_calls]:
        for call in messages[i]["tool_calls"]:
            function = call.get("function", {})
            if function.get("name") not in ("write_file", "replace"):
                continue
            try:
                arguments = json.loads(function.get("arguments") or "{}")
            except json.JSONDecodeError:
                continue
            for key in ("content", "old", "new"):
                value = arguments.get(key)
                if isinstance(value, str) and len(value) > 200:
                    arguments[key] = f"[{value.count(chr(10)) + 1} lines elided; the file on disk has them]"
            function["arguments"] = json.dumps(arguments)


class Log:
    """Events as JSON lines, each stamped with a wall-clock `_ts`."""

    def __init__(self, path: pathlib.Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.file = open(path, "a", encoding="utf-8")

    def emit(self, event: dict) -> None:
        event["_ts"] = round(time.time(), 3)
        self.file.write(json.dumps(event, ensure_ascii=False) + "\n")
        self.file.flush()

    def close(self) -> None:
        self.file.close()


@dataclass
class Outcome:
    status: str          # finished | max_steps | no_progress | stalled | no_route
    steps: int
    summary: str
    tokens_in: int
    tokens_out: int
    route: str


def run(prompt: str, boundary: Boundary, route_list: list[providers.Route], verify, log: Log,
        settings: Settings | None = None, ignore_strays: list[str] | None = None) -> Outcome:
    settings = settings or Settings()
    if not route_list:
        return Outcome("no_route", 0, "no usable model: every key is exhausted or missing", 0, 0, "")
    pool = list(route_list)
    messages: list = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]
    log.emit({"type": "thread.started", "route": pool[0].name})
    tokens_in = tokens_out = 0
    summary, status = "", "max_steps"
    nudges = reads_since_write = since_progress = 0
    verified = False
    step = 0
    for step in range(settings.max_steps):
        log.emit({"type": "turn.started", "step": step})
        response = None
        while pool:
            try:
                response = providers.complete(pool[0], messages, TOOLS)
                break
            except providers.QuotaExhausted as error:
                providers.mark_exhausted(pool[0].key_label, pool[0].model.allowance, str(error))
                log.emit({"type": "item.completed", "item": {"id": f"q{step}", "type": "error", "message": f"{pool[0].name} exhausted; failing over"}})
                pool.pop(0)
            except providers.RouteFailed as error:
                log.emit({"type": "item.completed", "item": {"id": f"r{step}", "type": "error", "message": str(error)[:300]}})
                pool.pop(0)
        if response is None:
            status = "no_route"
            break
        usage = response.get("usage") or {}
        tokens_in += usage.get("prompt_tokens", 0)
        tokens_out += usage.get("completion_tokens", 0)
        message = response["choices"][0]["message"]
        calls = message.get("tool_calls") or []
        messages.append({k: v for k, v in message.items() if k in ("role", "content", "tool_calls")})
        if message.get("content"):
            log.emit({"type": "item.completed", "item": {"id": f"m{step}", "type": "agent_message", "text": message["content"]}})
        if not calls:
            nudges += 1
            if nudges > 3:
                status = "stalled"
                break
            messages.append({"role": "user", "content": "Continue with the tools. Nobody will answer questions. Call verify when done writing, and finish when it passes."})
            continue
        finished = False
        for call in calls:
            name = call["function"]["name"]
            try:
                call_args = json.loads(call["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                call_args = {}
            item = {"id": call["id"], "type": "command_execution", "command": f"{name} {json.dumps(call_args)[:300]}"}
            log.emit({"type": "item.started", "item": dict(item, status="in_progress")})
            if name in READ_TOOLS and reads_since_write >= settings.refuse_after_reads:
                output, failed = "refused: read budget spent. You have what you need; write the files now, then verify.", True
            elif name == "finish":
                stray = checks.strays(boundary.root, boundary.owned, ignore_strays)
                if stray:
                    output, failed = ("refused: these files appeared and are not files this task owns; a test or "
                                      "command created them. Remove what writes them and delete them: " + ", ".join(stray[:20])), True
                elif not verified:
                    output, failed = "refused: verify has not passed since your last write. Run verify and fix what it reports.", True
                else:
                    summary, finished, status = call_args.get("summary", ""), True, "finished"
                    output, failed = "ok", False
            else:
                output, failed = run_tool(name, call_args, boundary, verify, settings)
            log.emit({"type": "item.completed", "item": dict(item, status="failed" if failed else "completed",
                                                              exit_code=1 if failed else 0, aggregated_output=output[:4000])})
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": output})
            if name in READ_TOOLS:
                reads_since_write += 1
            elif name in WRITE_TOOLS and not failed:
                reads_since_write = 0
                verified = False
            elif name == "verify":
                verified = not failed
                if verified:
                    since_progress = 0
        if finished:
            break
        since_progress += 1
        if since_progress >= settings.stall_steps:
            status = "no_progress"
            break
        compact(messages, settings)
        if reads_since_write == settings.nudge_after_reads:
            messages.append({"role": "user", "content": "You have read enough to start. Write the files now, run verify, and fix what it reports."})
    if status != "finished":
        output, failed = verify()
        summary = f"{summary}\nStopped ({status}). Final verify: {'FAIL' if failed else 'PASS'}\n{output[-3000:]}".strip()
    log.emit({"type": "turn.completed", "usage": {"input_tokens": tokens_in, "output_tokens": tokens_out}, "status": status})
    return Outcome(status, step + 1, summary, tokens_in, tokens_out, pool[0].name if pool else "")
