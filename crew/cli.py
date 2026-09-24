"""The `crew` command line.

    crew init                         write .agent-crew/project.toml for this repository
    crew config init|show|test|path   the providers file: create, inspect, probe every route
    crew task new|rm|list NAME        one worktree per task
    crew run PROMPT --task NAME ...   run a worker on a task
    crew land NAME -m MSG|-F FILE     land a task as one commit and clean it up
    crew check [--task NAME]          Agent Crew's own checks on a worktree
    crew status                       running tasks, exhausted keys, measured latency
    crew pack PROMPT --task NAME      print a prompt with its directives inlined
    crew metrics LOG                  where a run's time and tokens went
    crew shim                         (re)write the `crew` launcher in the crew home
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time

from crew import __version__, agent, checks, config, land, metrics, pack, providers, worktree

EXAMPLE_PROVIDERS = '''# Agent Crew providers. Keys never go in this file: name the environment
# variables that hold them (api_key_envs), or a file with one key per line
# (api_key_file). Several keys on one provider are used in turn, and a key
# that runs out of an allowance is skipped until the provider says it resets.

[defaults]
# "auto" picks, per role, the models allowed that role, by priority then by
# measured latency (`crew config test` measures it). Or name models in order:
#   writer = "deepseek-v4.1-flash, qwen3.8-flash"
writer = "auto"
reviewer = "auto"

[[provider]]
name = "router"
base_url = "http://localhost:20128/v1"   # any OpenAI-compatible endpoint
api_key_envs = ["CREW_ROUTER_KEY"]

[[model]]
id = "your-fast-coding-model"
provider = "router"
roles = ["writer"]
allowance = "default"    # models sharing a quota share an allowance label
priority = 10

[[model]]
id = "your-careful-model"
provider = "router"
roles = ["reviewer", "writer"]
priority = 20
'''

EXAMPLE_PROJECT = '''# Agent Crew project settings. Commit this file.

branch = "main"                  # the integration branch tasks land on
worktrees = "../{name}-crew"     # where task worktrees are created
shared = []                      # ignored heavy dirs to link into each worktree, e.g. ["node_modules"]
checks = ["text", "strays"]      # add "rust-modules" for a Rust project
ignore_strays = []               # extra globs a verify may leave behind (build artefacts are already ignored)
max_steps = 25

[verify]
# One command per kind; a worker's verify runs `crew run --verify <kind>`.
# It passes when the command exits 0 and does not print "RESULT: FAIL".
default = "echo configure [verify] in .agent-crew/project.toml && exit 1"
'''


def _print(value) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False) if not isinstance(value, str) else value)


def cmd_init(args) -> int:
    root = pathlib.Path(args.root or ".").resolve()
    path = root / config.PROJECT_FILE
    if path.exists():
        print(f"{path} already exists")
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(EXAMPLE_PROJECT.replace("{name}", root.name), encoding="utf-8")
    print(f"wrote {path}; set [verify] and shared for this repository")
    return 0


def cmd_config(args) -> int:
    path = config.providers_path()
    if args.action == "path":
        print(path)
        return 0
    if args.action == "init":
        if path.exists():
            print(f"{path} already exists")
            return 0
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(EXAMPLE_PROVIDERS, encoding="utf-8")
        print(f"wrote {path}; edit it, set the key variables, then run `crew config test`")
        return 0
    loaded = config.load_providers()
    if args.action == "show":
        for provider in loaded.providers.values():
            labels = [label for label, _ in provider.keys()]
            print(f"provider {provider.name}: {provider.base_url} keys: {', '.join(labels) or 'NONE FOUND'}")
        for model in loaded.models:
            print(f"model {model.id} ({model.provider}) roles={','.join(model.roles)} allowance={model.allowance} priority={model.priority}")
        for role in ("writer", "reviewer"):
            chain = providers.routes(loaded, role)
            print(f"{role}: " + (" -> ".join(r.name for r in chain) or "no usable route"))
        return 0
    if args.action == "test":
        ok_any = False
        seen = set()
        for role in ("writer", "reviewer"):
            for route in providers.routes(loaded, role):
                if route.name in seen:
                    continue
                seen.add(route.name)
                ok, seconds, detail = providers.probe(route)
                ok_any = ok_any or ok
                print(f"{'ok  ' if ok else 'FAIL'} {seconds:6.1f}s  {route.name}  {detail}")
        return 0 if ok_any else 1
    return 2


def _task_path(args, project: config.Project) -> pathlib.Path:
    if getattr(args, "workdir", None):
        return pathlib.Path(args.workdir).resolve()
    if getattr(args, "task", None):
        return pathlib.Path(worktree.info(args.task)["path"])
    return project.root


def cmd_task(args) -> int:
    project = config.load_project()
    if args.action == "new":
        print(worktree.create(project, args.name, args.base))
    elif args.action == "rm":
        worktree.remove(project, args.name)
        print(f"removed {args.name}")
    else:
        for record in worktree.tasks():
            print(f"{record['task']:20} {record['path']}  from {record['start'][:10]}")
    return 0


def cmd_run(args) -> int:
    project = config.load_project()
    workdir = _task_path(args, project)
    name = args.name or args.task or pathlib.Path(args.prompt).stem
    log_dir = pathlib.Path(args.log_dir) if args.log_dir else config.home() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    text = pack.pack(pathlib.Path(args.prompt).read_text(encoding="utf-8"), workdir)
    (log_dir / f"{name}.packed.md").write_text(text, encoding="utf-8")
    command = args.verify_cmd
    if command is None and args.verify:
        if args.verify not in project.verify:
            raise config.ConfigError(f"no [verify] {args.verify} in {config.PROJECT_FILE}")
        command = project.verify[args.verify]
    owns = [o for o in (args.owns or "").split(",") if o.strip()]
    settings = agent.Settings(max_steps=args.max_steps or project.max_steps)
    boundary = agent.Boundary(workdir, owns, settings.search_roots)
    verify = agent.make_verify(command, boundary, args.base or project.branch, project.checks, project.source_suffixes,
                               settings.verify_timeout, project.ignore_strays)
    chain = providers.routes(config.load_providers(), args.role, args.model)
    running = config.state_dir() / "running"
    running.mkdir(exist_ok=True)
    lock = running / f"{name}.json"
    lock.write_text(json.dumps({"task": name, "workdir": str(workdir), "started": time.time(),
                                "route": chain[0].name if chain else None, "pid": os.getpid()}), encoding="utf-8")
    events = log_dir / f"{name}.jsonl"
    events.write_text("", encoding="utf-8")
    log = agent.Log(events)
    try:
        outcome = agent.run(text, boundary, chain, verify, log, settings, project.ignore_strays)
    finally:
        log.close()
        lock.unlink(missing_ok=True)
    summary = metrics.summarise(events)
    (log_dir / f"{name}.metrics.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (log_dir / f"{name}.last.md").write_text(outcome.summary + ("\nTASK COMPLETE\n" if outcome.status == "finished" else ""), encoding="utf-8")
    _print({"task": name, "status": outcome.status, "steps": outcome.steps, "route": outcome.route,
            "tokens_in": outcome.tokens_in, "tokens_out": outcome.tokens_out, "wall_min": summary["wall_min"],
            "summary": outcome.summary[-1500:]})
    return 0 if outcome.status == "finished" else 5


def cmd_land(args) -> int:
    project = config.load_project()
    message = pathlib.Path(args.file).read_text(encoding="utf-8") if args.file else args.message
    if not message:
        raise config.ConfigError("give the commit message with -m or -F")
    print(land.land(project, args.name, message, keep=args.keep))
    return 0


def cmd_check(args) -> int:
    project = config.load_project()
    root = _task_path(args, project)
    owns = [o for o in (args.owns or "**").split(",") if o.strip()]
    boundary = agent.Boundary(root, owns, [])
    problems = checks.run(root, args.base or project.branch, project.checks, project.source_suffixes, boundary.owned,
                          project.ignore_strays)
    print("\n".join(problems) or "clean")
    return 1 if problems else 0


def cmd_status(args) -> int:
    running = sorted((config.state_dir() / "running").glob("*.json")) if (config.state_dir() / "running").exists() else []
    print("running:" if running else "running: none")
    for path in running:
        record = json.loads(path.read_text(encoding="utf-8"))
        print(f"  {record['task']:20} {int((time.time() - record['started']) / 60)} min  {record.get('route')}")
    markers = providers.exhausted_markers()
    print("exhausted:" if markers else "exhausted: none")
    for name, until, message in markers:
        print(f"  {name:40} until {time.strftime('%Y-%m-%d %H:%M', time.gmtime(until))} UTC")
    latency = providers._latency()
    if latency:
        print("latency (moving average):")
        for model, seconds in sorted(latency.items(), key=lambda item: item[1]):
            print(f"  {model:40} {seconds:6.1f}s")
    tasks = worktree.tasks()
    print("worktrees:" if tasks else "worktrees: none")
    for record in tasks:
        print(f"  {record['task']:20} {record['path']}")
    return 0


def cmd_pack(args) -> int:
    project = config.load_project()
    sys.stdout.reconfigure(encoding="utf-8")
    print(pack.pack(pathlib.Path(args.prompt).read_text(encoding="utf-8"), _task_path(args, project)))
    return 0


def cmd_metrics(args) -> int:
    _print(metrics.summarise(pathlib.Path(args.log)))
    return 0


def cmd_shim(args) -> int:
    """Writes `crew` (POSIX) and `crew.cmd` (Windows) into <crew home>/bin,
    pointing at this installation, so skills and people can call `crew`."""
    package_root = pathlib.Path(__file__).resolve().parent.parent
    bin_dir = config.home() / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    python = sys.executable
    posix = bin_dir / "crew"
    posix.write_text(f'#!/bin/sh\nPYTHONPATH="{package_root}${{PYTHONPATH:+:$PYTHONPATH}}" exec "{python}" -m crew "$@"\n', encoding="utf-8")
    posix.chmod(0o755)
    (bin_dir / "crew.cmd").write_text(f'@echo off\r\nset "PYTHONPATH={package_root};%PYTHONPATH%"\r\n"{python}" -m crew %*\r\n', encoding="utf-8")
    if not args.quiet:
        print(f"wrote {posix} and {posix}.cmd (package at {package_root})")
    return 0


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(prog="crew", description="Delegate small coding tasks to OpenAI-compatible models, and check their work.")
    top.add_argument("--version", action="version", version=f"agent-crew {__version__}")
    sub = top.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="write .agent-crew/project.toml")
    p.add_argument("--root")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("config", help="the providers file")
    p.add_argument("action", choices=["init", "show", "test", "path"])
    p.set_defaults(func=cmd_config)

    p = sub.add_parser("task", help="task worktrees")
    p.add_argument("action", choices=["new", "rm", "list"])
    p.add_argument("name", nargs="?")
    p.add_argument("--base")
    p.set_defaults(func=cmd_task)

    p = sub.add_parser("run", help="run a worker")
    p.add_argument("prompt")
    p.add_argument("--task", help="run in this task's worktree")
    p.add_argument("--workdir", help="or in this directory")
    p.add_argument("--name", help="log name (default: the task, else the prompt file's stem)")
    p.add_argument("--owns", help="comma-separated globs the worker may write, relative to the worktree")
    p.add_argument("--verify", help="a [verify] kind from project.toml")
    p.add_argument("--verify-cmd", help="or a verify command")
    p.add_argument("--role", default="writer", choices=["writer", "reviewer"])
    p.add_argument("--model", default="auto", help="auto, one model id, or a comma-separated preference list")
    p.add_argument("--max-steps", type=int)
    p.add_argument("--base", help="what checks compare against (default: the integration branch)")
    p.add_argument("--log-dir")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("land", help="land a task as one commit")
    p.add_argument("name")
    p.add_argument("-m", "--message")
    p.add_argument("-F", "--file")
    p.add_argument("--keep", action="store_true", help="keep the worktree")
    p.set_defaults(func=cmd_land)

    p = sub.add_parser("check", help="Agent Crew's own checks")
    p.add_argument("--task")
    p.add_argument("--workdir")
    p.add_argument("--owns")
    p.add_argument("--base")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("status", help="running tasks, exhausted keys, latency")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("pack", help="print a packed prompt")
    p.add_argument("prompt")
    p.add_argument("--task")
    p.add_argument("--workdir")
    p.set_defaults(func=cmd_pack)

    p = sub.add_parser("metrics", help="summarise a run log")
    p.add_argument("log")
    p.set_defaults(func=cmd_metrics)

    p = sub.add_parser("shim", help="write the crew launcher")
    p.add_argument("--quiet", action="store_true")
    p.set_defaults(func=cmd_shim)
    return top


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "task" and args.action in ("new", "rm") and not args.name:
        print("crew task new|rm needs a name", file=sys.stderr)
        return 2
    try:
        return args.func(args)
    except (config.ConfigError, worktree.WorktreeError, land.LandError) as error:
        print(f"crew: {error}", file=sys.stderr)
        return 2
