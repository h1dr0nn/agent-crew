"""Reviewing local git state with a reviewer model, in one request.

A worker's review of another worker's task runs as a task (`crew run --role
reviewer`); this is the lighter path the conductor uses on the repository it
is standing in: the uncommitted work, or a branch against its base, inlined
into one prompt and answered once, failing over across reviewer routes.

The same path backs the stop-time review gate: when it is enabled for a
repository, the Stop hook reviews the uncommitted work and blocks the stop
when the reviewer finds something that has to be fixed first.
"""

from __future__ import annotations

import json
import pathlib
import re
import subprocess
import time
from dataclasses import dataclass

from crew import config, pack, providers

MAX_DIFF = 150_000       # characters of diff inlined; the rest is named, not shown
MAX_UNTRACKED = 40_000   # bytes of one untracked file inlined
MAX_FILES = 400          # file names listed
MAX_CONTEXT = 8_000      # characters of Claude's last message given to the gate
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"  # what a repository with no commits diffs against
GATE_BUDGET = 240        # seconds the stop gate may spend in total, well inside the hook's timeout
VERDICT = re.compile(r"^\W*(ALLOW|BLOCK)\b", re.I | re.M)


class ReviewError(Exception):
    pass


@dataclass
class Target:
    label: str
    diff: str
    files: list[str]

    @property
    def empty(self) -> bool:
        return not self.files


def _git(root: pathlib.Path, *args: str) -> str:
    # core.quotePath=false: non-ASCII file names come back as themselves, not octal escapes.
    done = subprocess.run(["git", "-c", "core.quotePath=false", "-C", str(root), *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace")
    if done.returncode != 0:
        raise ReviewError(f"git {' '.join(args)}: {done.stderr.strip()}")
    return done.stdout


def _untracked(root: pathlib.Path, budget: int) -> tuple[list[str], str]:
    """Untracked files as new-file diffs, reading at most MAX_UNTRACKED bytes
    of each and `budget` characters in all; past that, names only."""
    names = [n for n in _git(root, "ls-files", "-z", "--others", "--exclude-standard").split("\0") if n]
    parts, used = [], 0
    for name in names:
        if used >= budget:
            parts.append(f"new file {name} (not shown: the review is already at its size limit)\n")
            continue
        try:
            with open(root / name, "rb") as handle:
                data = handle.read(MAX_UNTRACKED + 1)
        except OSError:
            parts.append(f"new file {name} (unreadable, not shown)\n")
            continue
        if b"\0" in data:
            parts.append(f"new file {name} (binary, not shown)\n")
            continue
        text = data[:MAX_UNTRACKED].decode("utf-8", errors="replace")
        if len(data) > MAX_UNTRACKED:
            text += "\n... (truncated)"
        body = "".join(f"+{line}\n" for line in text.splitlines())
        part = f"diff --git a/{name} b/{name}\nnew file (untracked)\n--- /dev/null\n+++ b/{name}\n{body}"
        parts.append(part)
        used += len(part)
    return names, "".join(parts)


def _head(root: pathlib.Path) -> str:
    done = subprocess.run(["git", "-C", str(root), "rev-parse", "--verify", "-q", "HEAD"], capture_output=True, text=True)
    return "HEAD" if done.returncode == 0 else EMPTY_TREE


def _names(output: str) -> list[str]:
    return [n for n in output.split("\0") if n]


def target(root: pathlib.Path, base: str | None, scope: str = "auto", fallback: str | None = None) -> Target:
    """What to review. `working-tree` is the uncommitted work, untracked files
    included; `branch` is HEAD against `base`; `auto` is the working tree when
    it has changes, else the branch. An explicit `base` means the branch;
    `fallback` is the base used when none was given."""
    if scope not in ("auto", "working-tree", "branch"):
        raise ReviewError(f"unknown scope {scope!r}; use auto, working-tree or branch")
    if scope in ("auto", "working-tree") and not (scope == "auto" and base):
        head = _head(root)
        changed = _names(_git(root, "diff", head, "--name-only", "-z"))
        tracked = _git(root, "diff", head)
        untracked, extra = _untracked(root, max(0, MAX_DIFF - len(tracked)))
        if changed or untracked or scope == "working-tree":
            return Target("the uncommitted changes in the working tree", tracked + extra, changed + untracked)
    base = base or fallback
    if not base:
        raise ReviewError("nothing uncommitted to review; name a base (--base REF) to review a branch")
    files = _names(_git(root, "diff", f"{base}...HEAD", "--name-only", "-z"))
    return Target(f"the commits on HEAD since {base}", _git(root, "diff", f"{base}...HEAD"), files)


def prompt(kind: str, what: Target, focus: str = "", context: str = "") -> str:
    """kind: `review`, `adversarial` or `gate`."""
    name = {"review": "code-review", "adversarial": "adversarial-review", "gate": "stop-gate"}[kind]
    diff = what.diff
    if len(diff) > MAX_DIFF:
        diff = diff[:MAX_DIFF] + f"\n... (diff truncated at {MAX_DIFF} characters)\n"
    text = pack.template(name)
    files = "\n".join(f"- {f}" for f in what.files[:MAX_FILES]) or "- (none)"
    if len(what.files) > MAX_FILES:
        files += f"\n- ... and {len(what.files) - MAX_FILES} more"
    context = context.strip()
    if len(context) > MAX_CONTEXT:
        context = "... " + context[-MAX_CONTEXT:]
    for key, value in {"TARGET": what.label, "FOCUS": focus.strip() or "none given", "FILES": files,
                       "CONTEXT": context or "(none)", "DIFF": diff}.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def ask(chain: list[providers.Route], text: str, timeout: int = 600, retries: int = 2,
        deadline: float | None = None) -> tuple[str, str]:
    """One answer from the first route that gives one, trying no new route
    after `deadline` (a time.time()). Returns (answer, route name)."""
    errors = []
    for route in chain:
        if deadline is not None:
            left = deadline - time.time()
            if left < 10:
                errors.append("out of time")
                break
            timeout = int(min(timeout, left))
        try:
            reply = providers.complete(route, [{"role": "user", "content": text}], timeout=timeout, retries=retries)
        except providers.QuotaExhausted as error:
            providers.mark_exhausted(route.key_label, route.model.allowance, str(error))
            errors.append(f"{route.name}: allowance exhausted")
            continue
        except providers.RouteFailed as error:
            errors.append(str(error)[:200])
            continue
        answer = (reply["choices"][0]["message"].get("content") or "").strip()
        if answer:
            return answer, route.name
        errors.append(f"{route.name}: empty answer")
    raise ReviewError("no reviewer answered" + (": " + "; ".join(errors) if errors else ": no usable reviewer route"))


def run(root: pathlib.Path, kind: str, base: str | None, scope: str, focus: str, model: str = "auto",
        fallback: str | None = None) -> dict:
    what = target(root, base, scope, fallback)
    if what.empty:
        return {"target": what.label, "files": [], "route": None, "review": "Nothing to review."}
    chain = providers.routes(config.load_providers(), "reviewer", model)
    answer, route = ask(chain, prompt(kind, what, focus))
    log_dir = config.home() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{time.time_ns() % 1_000_000_000:09d}"
    (log_dir / f"{kind}-{stamp}.md").write_text(f"# {kind} of {what.label}\n\nroute: {route}\n\n{answer}\n", encoding="utf-8")
    return {"target": what.label, "files": what.files, "route": route, "review": answer}


# The stop-time gate --------------------------------------------------------

def _gates() -> dict:
    try:
        return json.loads((config.state_dir() / "gates.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def gate_enabled(root: pathlib.Path) -> bool:
    return bool(_gates().get(str(root.resolve())))


def set_gate(root: pathlib.Path, enabled: bool) -> None:
    gates = _gates()
    key = str(root.resolve())
    if enabled:
        gates[key] = True
    else:
        gates.pop(key, None)
    (config.state_dir() / "gates.json").write_text(json.dumps(gates, indent=2), encoding="utf-8")


def stop_hook(event: dict) -> dict | None:
    """The Stop hook: None lets Claude stop; a dict blocks it with a reason.
    Only reviews when the gate is enabled for this repository and there is
    uncommitted work, and never twice in a row (`stop_hook_active`). A gate
    that cannot run lets the stop through rather than trapping the session."""
    if event.get("stop_hook_active"):
        return None
    try:
        root = config.find_root(pathlib.Path(event.get("cwd") or "."))
    except config.ConfigError:
        return None
    if not gate_enabled(root):
        return None
    try:
        what = target(root, None, "working-tree")
        if what.empty:
            return None
        chain = providers.routes(config.load_providers(), "reviewer")
        answer, route = ask(chain, prompt("gate", what, context=event.get("last_assistant_message") or ""),
                            timeout=120, retries=1, deadline=time.time() + GATE_BUDGET)
    except (ReviewError, config.ConfigError) as error:
        return {"systemMessage": f"Agent Crew review gate skipped: {error}"}
    # The first verdict word counts, however the model decorated it (`**BLOCK:**`, a fence, "Verdict: BLOCK").
    verdict = VERDICT.search(re.sub(r"(?i)^\W*verdict\W*", "", answer.strip(), flags=re.M))
    if verdict and verdict[1].upper() == "BLOCK":
        return {"decision": "block", "reason": f"Agent Crew review gate ({route}) blocked the stop:\n{answer}"}
    return None
