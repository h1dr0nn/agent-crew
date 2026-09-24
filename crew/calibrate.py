"""Calibration: what work this repository does, and which model in the pool
does each kind of it best, measured on the repository's own history.

1. The work mix: over recent commits, how much of the change is in each
   verify kind (`rust`, `ts`, `docs`, ...), by the files it touched.
2. Probes: for each kind, a small recent commit that only touches that kind
   is replayed. A worktree starts at the commit with its code files put back
   as they were before it (its test files stay, and now fail), and each model
   is asked to make the change again, from the commit message and the
   signatures it added, under the kind's verify.
3. Each model is also asked which kinds of this work it thinks suit it.
4. The results go to `.agent-crew/profile.json`, and `crew run` then tries,
   for each verify kind, the models that did that kind best.
"""

from __future__ import annotations

import json
import pathlib
import re
import subprocess
import time
from dataclasses import dataclass, field

from crew import agent, config, metrics, pack, providers, worktree

PROFILE = pathlib.Path(".agent-crew") / "profile.json"

KIND_SUFFIXES = {
    "rust": (".rs",),
    "ts": (".ts", ".tsx", ".js", ".jsx", ".mjs", ".css"),
    "js": (".js", ".jsx", ".mjs", ".ts", ".tsx", ".css"),
    "python": (".py",),
    "go": (".go",),
    "docs": (".md", ".mdx"),
}
TEST_FILE = re.compile(r"(^|/)(tests?|__tests__|spec)/|[._-](test|spec)\.[a-z]+$|(^|/)test_[^/]+\.py$|_test\.go$")
SIGNATURE = re.compile(r"^\s*(pub(\([a-z]+\))?\s+)?(async\s+)?(fn|struct|enum|trait|impl|type|const)\s|"
                       r"^\s*(export\s+)?(async\s+)?(function|class|interface|type|const|enum)\s|"
                       r"^\s*(async\s+)?def\s|^\s*class\s|^\s*func\s|^#{1,4}\s")


def _git(root: pathlib.Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "core.quotePath=false", "-C", str(root), *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace").stdout


def kind_of(path: str, kinds: list[str]) -> str | None:
    for kind in kinds:
        if path.endswith(KIND_SUFFIXES.get(kind, ())):
            return kind
    return None


@dataclass
class Commit:
    sha: str
    subject: str
    files: list[tuple[str, int, int]] = field(default_factory=list)   # (path, added, removed)


def history(root: pathlib.Path, limit: int) -> list[Commit]:
    commits: list[Commit] = []
    # split("\n"), not splitlines(): splitlines() also breaks at the \x1e separator.
    for line in _git(root, "log", "--no-merges", f"-{limit}", "--numstat", "--format=%x1e%H%x1f%s").split("\n"):
        if line.startswith("\x1e"):
            sha, _, subject = line[1:].partition("\x1f")
            commits.append(Commit(sha, subject))
        elif line.strip() and commits:
            added, removed, path = line.split("\t", 2)
            if added != "-":
                commits[-1].files.append((path, int(added), int(removed)))
    return commits


def work_mix(root: pathlib.Path, kinds: list[str], limit: int = 300) -> dict[str, float]:
    """The share of changed lines in each kind, over the last `limit` commits."""
    lines: dict[str, int] = {}
    for commit in history(root, limit):
        for path, added, removed in commit.files:
            kind = kind_of(path, kinds)
            if kind:
                lines[kind] = lines.get(kind, 0) + added + removed
    total = sum(lines.values()) or 1
    return {k: round(v / total, 3) for k, v in sorted(lines.items(), key=lambda kv: -kv[1])}


def probes(root: pathlib.Path, kind: str, kinds: list[str], count: int = 1, limit: int = 300,
           max_files: int = 3, max_added: int = 120) -> list[Commit]:
    """Recent commits small enough to replay, whose code is all of one kind."""
    found = []
    for commit in history(root, limit):
        code = [(p, a, r) for p, a, r in commit.files if not TEST_FILE.search(p)]
        if not code or len(commit.files) > max_files or not (5 <= sum(a for _, a, _ in code) <= max_added):
            continue
        if all(kind_of(p, kinds) == kind for p, _, _ in commit.files):
            found.append(commit)
            if len(found) == count:
                break
    return found


def outline(root: pathlib.Path, commit: Commit, paths: list[str]) -> list[str]:
    """The signatures and headings the commit added: the task's contract."""
    added = []
    for line in _git(root, "show", "--format=", commit.sha, "--", *paths).splitlines():
        if line.startswith("+") and not line.startswith("+++") and SIGNATURE.search(line[1:]):
            added.append(line[1:].rstrip())
    return added[:40]


def probe_prompt(root: pathlib.Path, commit: Commit, code: list[str], tests: list[str], kind: str) -> str:
    body = _git(root, "show", "-s", "--format=%b", commit.sha).strip()
    contract = outline(root, commit, code)
    lines = ["@@template rules", "", f"# Calibration task: {commit.subject}", "",
             "Make this change to the repository. It is a change this project really made; the files you own are",
             "as they were before it.", ""]
    if body:
        lines += ["What the change is for:", "", body, ""]
    lines += ["## Files you own"] + [f"- `{p}`" for p in code] + [""]
    if contract:
        lines += ["## Signatures and headings the change adds", "```", *contract, "```", ""]
    if tests:
        lines += ["## Tests that must pass (already in place; do not edit them)"] + [f"- `{t}`" for t in tests] + [""]
    lines += [f"Verify kind: `{kind}`", "", "## Inlined sources"]
    lines += [f"@@include {p}" for p in code + tests]
    return "\n".join(lines) + "\n"


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-")[:40]


def _prepare(project: config.Project, task: str, commit: Commit, code: list[str]) -> pathlib.Path:
    path = worktree.create(project, task, commit.sha)
    existed = set(_git(path, "ls-tree", "-r", "--name-only", f"{commit.sha}^").splitlines())
    for file in code:
        if file in existed:
            subprocess.run(["git", "-C", str(path), "checkout", f"{commit.sha}^", "--", file], capture_output=True)
        else:
            (path / file).unlink(missing_ok=True)
    subprocess.run(["git", "-C", str(path), "add", "-A"], capture_output=True)
    subprocess.run(["git", "-C", str(path), "-c", "user.name=crew", "-c", "user.email=crew@localhost",
                    "commit", "-q", "--no-verify", "-m", "calibration: start"], capture_output=True)
    return path


def ask_preference(route: providers.Route, mix: dict[str, float], repo_name: str) -> dict:
    kinds = ", ".join(f"{k} ({round(v * 100)}% of recent changes)" for k, v in mix.items())
    question = (f"You may be given coding tasks in the repository {repo_name}. The kinds of work it has are: {kinds}. "
                "Which of these kinds do you do best? Answer with one JSON object only: "
                '{"rank": [kinds, best first], "why": "one sentence"}')
    try:
        reply = providers.complete(route, [{"role": "user", "content": question}], timeout=120, retries=1)
    except (providers.QuotaExhausted, providers.RouteFailed) as error:
        return {"error": str(error)[:200]}
    text = reply["choices"][0]["message"].get("content") or ""
    match = re.search(r"\{.*\}", text, re.S)
    try:
        answer = json.loads(match[0]) if match else {}
    except json.JSONDecodeError:
        answer = {}
    rank = [k for k in answer.get("rank", []) if k in mix] if isinstance(answer.get("rank"), list) else []
    return {"rank": rank, "why": str(answer.get("why", ""))[:300]} if rank else {"raw": text[:300]}


def score(runs: list[dict]) -> tuple:
    """Sort key, best first: pass rate, then fewer minutes, then fewer tokens."""
    if not runs:
        return (1, 0, 0, 0)
    passed = sum(r["status"] == "finished" for r in runs) / len(runs)
    minutes = sum(r["minutes"] for r in runs) / len(runs)
    tokens = sum(r["tokens"] for r in runs) / len(runs)
    return (0, -passed, minutes, tokens)


def calibrate(project: config.Project, models: list[config.Model], kinds: list[str], per_kind: int = 1,
              dry_run: bool = False, log=print) -> dict:
    loaded = config.load_providers()
    mix = work_mix(project.root, list(project.verify))
    kinds = kinds or [k for k in mix if k in project.verify][:3]
    plan = {kind: probes(project.root, kind, list(project.verify), per_kind) for kind in kinds}
    profile = {"measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "mix": mix,
               "probes": {k: [{"sha": c.sha[:10], "subject": c.subject} for c in v] for k, v in plan.items()},
               "models": {}, "prefer": {}}
    if dry_run:
        return profile
    for model in models:
        chain = providers.routes(loaded, "writer", model.id) if model.id else []
        entry: dict = {"kinds": {}, "self": {}}
        profile["models"][model.id] = entry
        if not chain:
            entry["unavailable"] = "every key is exhausted for this model, or it has none"
            log(f"{model.id}: unavailable")
            continue
        entry["self"] = ask_preference(chain[0], mix, project.root.name)
        for kind, commits in plan.items():
            runs = []
            for commit in commits:
                code = [p for p, _, _ in commit.files if not TEST_FILE.search(p)]
                tests = [p for p, _, _ in commit.files if TEST_FILE.search(p)]
                task = _slug(f"calib-{kind}-{model.id}-{commit.sha[:7]}")
                log(f"{model.id}: {kind} probe {commit.sha[:7]} {commit.subject[:60]}")
                path = _prepare(project, task, commit, code)
                try:
                    boundary = agent.Boundary(path, code, agent.Settings().search_roots)
                    verify = agent.make_verify(project.verify[kind], boundary, "HEAD", project.checks,
                                               project.source_suffixes, agent.Settings().verify_timeout,
                                               project.ignore_strays)
                    text = pack.pack(probe_prompt(project.root, commit, code, tests, kind), path)
                    events = config.home() / "logs" / f"{task}.jsonl"
                    events.parent.mkdir(parents=True, exist_ok=True)
                    run_log = agent.Log(events)
                    started = time.time()
                    try:
                        outcome = agent.run(text, boundary, chain, verify, run_log,
                                            agent.Settings(max_steps=project.max_steps), project.ignore_strays)
                    finally:
                        run_log.close()
                    summary = metrics.summarise(events)
                    runs.append({"sha": commit.sha[:10], "status": outcome.status, "steps": outcome.steps,
                                 "minutes": round((time.time() - started) / 60, 2),
                                 "tokens": outcome.tokens_in + outcome.tokens_out, "held_out_tests": bool(tests),
                                 "wall_min": summary.get("wall_min")})
                    log(f"  -> {outcome.status} in {outcome.steps} steps")
                finally:
                    worktree.remove(project, task)
            entry["kinds"][kind] = {"runs": runs, "passed": sum(r["status"] == "finished" for r in runs),
                                    "of": len(runs)}
    for kind in plan:
        ranked = [m for m in profile["models"] if profile["models"][m]["kinds"].get(kind, {}).get("of")]
        ranked.sort(key=lambda m: score(profile["models"][m]["kinds"][kind]["runs"]))
        profile["prefer"][kind] = [m for m in ranked if profile["models"][m]["kinds"][kind]["passed"]]
    return profile


def save(project: config.Project, profile: dict) -> pathlib.Path:
    path = project.root / PROFILE
    previous = load(project)
    # Keep what earlier calibrations measured for models or kinds this run did not reach.
    for model, entry in previous.get("models", {}).items():
        mine = profile["models"].setdefault(model, entry)
        for kind, result in entry.get("kinds", {}).items():
            mine.setdefault("kinds", {}).setdefault(kind, result)
    for kind, order in previous.get("prefer", {}).items():
        profile["prefer"].setdefault(kind, order)
    path.write_text(json.dumps(profile, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return path


def load(project: config.Project) -> dict:
    try:
        return json.loads((project.root / PROFILE).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def preferred(project: config.Project, kind: str | None) -> list[str]:
    return list(load(project).get("prefer", {}).get(kind or "", [])) if kind else []
