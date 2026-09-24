"""The `crew` command line.

    crew init                         write .agent-crew/project.toml for this repository
    crew config init|show|test|path   the providers file: create, inspect, probe every route
    crew config detect                find OpenAI-compatible endpoints on this machine and their models
    crew task new|rm|list NAME        one worktree per task
    crew run PROMPT --task NAME ...   run a worker on a task (PROMPT `-` reads stdin)
    crew result [NAME]                a finished run's outcome and summary (default: the latest)
    crew cancel NAME                  stop a running worker
    crew review [--base REF] ...      a reviewer model's review of the uncommitted work or a branch
    crew adversarial-review ...       the same, challenging the approach and its assumptions
    crew gate enable|disable|status   the stop-time review gate for this repository
    crew land NAME -m MSG|-F FILE     land a task as one commit and clean it up
    crew check [--task NAME]          Agent Crew's own checks on a worktree
    crew status                       running tasks, exhausted keys, measured latency
    crew pack PROMPT --task NAME      print a prompt with its directives inlined
    crew metrics LOG                  where a run's time and tokens went
    crew template [NAME]              print a built-in prompt template, or list them
    crew calibrate [--dry-run]        measure which pool model does each kind of this repository's work best
    crew profile                      what the last calibration measured, and the model order per kind
    crew doctor                       check Python, git, the providers file, keys and this repository
    crew shim [--path]                (re)write the `crew` launcher; --path also puts it on PATH
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import signal
import subprocess
import sys
import time

from crew import __version__, agent, calibrate, checks, config, land, metrics, pack, plugin_settings, procs, providers, review, setup, worktree

EXAMPLE_PROVIDERS = '''# Agent Crew: the endpoint, its key and the model pool, all in this one file.
# It lives in your home directory, outside every repository. Several keys are
# used in turn, and a key that runs out of an allowance is skipped until the
# provider says it resets. `/agent-crew:setup` in Claude Code fills this in.

[defaults]
# "auto" picks, per role, the models allowed that role, by priority then by
# measured latency (`crew config test` measures it). Or name models in order:
#   writer = "deepseek-v4.1-flash, qwen3.8-flash"
writer = "auto"
reviewer = "auto"

[[provider]]
name = "router"
base_url = "http://localhost:20128/v1"   # any OpenAI-compatible endpoint
api_keys = [""]                          # paste the key between the quotes; [] if none is needed

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
    path.parent.mkdir(parents=True, exist_ok=True)
    # Task prompts are working files, and the calibration profile measures this
    # machine's pool rather than the repository: both stay out of git.
    ignore = path.parent / ".gitignore"
    present = ignore.read_text(encoding="utf-8").split() if ignore.exists() else []
    missing = [entry for entry in ("prompts/", "profile.json") if entry not in present]
    if missing:
        with open(ignore, "a", encoding="utf-8", newline="\n") as handle:
            handle.write("".join(f"{entry}\n" for entry in missing))
    if path.exists():
        print(f"{path} already exists")
        return 0
    branch = subprocess.run(["git", "-C", str(root), "branch", "--show-current"], capture_output=True,
                            text=True).stdout.strip() or "main"
    path.write_text(setup.project_file(root, branch), encoding="utf-8", newline="\n")
    print(path.read_text(encoding="utf-8"))
    print(f"wrote {path} from what this repository has; check the [verify] commands")
    return 0


def cmd_config(args) -> int:
    path = config.providers_path()
    if args.action == "path":
        print(path)
        return 0
    if args.action == "detect":
        _print(setup.detect())
        return 0
    if args.action == "init":
        path.parent.mkdir(parents=True, exist_ok=True)
        if args.base_url:
            if not (args.force or setup.unconfigured(path)):
                raise config.ConfigError(f"{path} is already configured; pass --force to replace it")
            status, _ = setup.list_models(args.base_url)
            text = setup.providers_file(args.name, args.base_url, args.key_env, _split(args.writer), _split(args.reviewer),
                                        needs_key=status != "ok")
            path.write_text(text, encoding="utf-8", newline="\n")
            print(f"wrote {path}")
            return cmd_config(argparse.Namespace(action="show"))
        if path.exists():
            print(f"{path} already exists")
            return 0
        path.write_text(EXAMPLE_PROVIDERS, encoding="utf-8")
        print(f"wrote {path}; run /agent-crew:setup in Claude Code to fill it in, or edit it")
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


def _split(value: str | None) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


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
    if args.new_task and args.task and not worktree.exists(args.task):
        worktree.create(project, args.task, args.base)
    workdir = _task_path(args, project)
    from_stdin = args.prompt == "-"
    if from_stdin and not (args.name or args.task):
        raise config.ConfigError("a prompt on stdin needs --task or --name, so the run has a name to follow and cancel")
    name = args.name or args.task or pathlib.Path(args.prompt).stem
    log_dir = _log_dir(args)
    source = sys.stdin.buffer.read() if from_stdin else pathlib.Path(args.prompt).read_bytes()
    raw = source.decode("utf-8-sig")
    text = pack.pack(raw, workdir)
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
    order = calibrate.preferred(project, args.verify) if args.model == "auto" and args.role == "writer" else []
    if order:
        # The models calibration measured best at this kind of work go first, the rest keep their order.
        chain.sort(key=lambda route: order.index(route.model.id) if route.model.id in order else len(order))
    running = config.state_dir() / "running"
    running.mkdir(exist_ok=True)
    lock = running / f"{name}.json"
    if lock.exists() and _worker_alive(json.loads(lock.read_text(encoding="utf-8"))):
        raise config.ConfigError(f"a worker named {name} is already running (`crew cancel {name}` stops it)")
    stop = running / f"{name}.cancel"
    stop.unlink(missing_ok=True)
    lock.write_text(json.dumps({"task": name, "workdir": str(workdir), "started": time.time(), "log_dir": str(log_dir),
                                "route": chain[0].name if chain else None, "pid": os.getpid(),
                                "identity": procs.identity(os.getpid())}), encoding="utf-8")
    # `crew cancel` asks first (a file checked before each step), then signals.
    signal.signal(signal.SIGTERM, _terminated)
    events = log_dir / f"{name}.jsonl"
    events.write_text("", encoding="utf-8")
    log = agent.Log(events)
    try:
        outcome = agent.run(text, boundary, chain, verify, log, settings, project.ignore_strays, cancelled=stop.exists)
    finally:
        log.close()
        lock.unlink(missing_ok=True)
        stop.unlink(missing_ok=True)
    summary = metrics.summarise(events)
    (log_dir / f"{name}.metrics.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (log_dir / f"{name}.last.md").write_text(outcome.summary + ("\nTASK COMPLETE\n" if outcome.status == "finished" else ""), encoding="utf-8")
    result = {"task": name, "status": outcome.status, "steps": outcome.steps, "route": outcome.route,
              "tokens_in": outcome.tokens_in, "tokens_out": outcome.tokens_out, "wall_min": summary["wall_min"],
              "workdir": str(workdir), "finished_at": time.time()}
    (log_dir / f"{name}.outcome.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    _print(dict(result, summary=outcome.summary[-1500:]))
    return 0 if outcome.status == "finished" else 5


def _log_dir(args) -> pathlib.Path:
    log_dir = pathlib.Path(args.log_dir) if getattr(args, "log_dir", None) else config.home() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def _worker_alive(record: dict) -> bool:
    return procs.alive(int(record.get("pid", 0)), record.get("identity"))


def _terminated(signum, frame):
    raise SystemExit(128 + signum)


def cmd_result(args) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    log_dir = _log_dir(args)
    if args.name:
        path = log_dir / f"{args.name}.outcome.json"
    else:
        found = sorted(log_dir.glob("*.outcome.json"), key=lambda p: p.stat().st_mtime)
        if not found:
            raise config.ConfigError(f"no finished runs in {log_dir}")
        path = found[-1]
    name = path.name[: -len(".outcome.json")]
    running = config.state_dir() / "running" / f"{name}.json"
    if running.exists():
        record = json.loads(running.read_text(encoding="utf-8"))
        minutes = int((time.time() - record["started"]) / 60)
        if _worker_alive(record):
            print(f"{name} is still running ({minutes} min, {record.get('route')})")
        else:
            print(f"{name} stopped without finishing after {minutes} min (process gone); `crew cancel {name}` clears it")
        return 0
    if not path.exists():
        raise config.ConfigError(f"no run named {name} in {log_dir}")
    result = json.loads(path.read_text(encoding="utf-8"))
    for key in ("task", "status", "steps", "route", "wall_min", "tokens_in", "tokens_out", "workdir"):
        print(f"{key + ':':12} {result.get(key)}")
    print(f"{'log:':12} {log_dir / (name + '.jsonl')}")
    last = log_dir / f"{name}.last.md"
    print("\n" + (last.read_text(encoding="utf-8").strip() if last.exists() else "(no summary)"))
    if result.get("status") == "finished":
        print(f"\nnext: verify it yourself, review it, then `crew land {name} -F <message file>`")
    return 0


def cmd_cancel(args) -> int:
    lock = config.state_dir() / "running" / f"{args.name}.json"
    if not lock.exists():
        raise config.ConfigError(f"no running worker named {args.name} (see `crew status`)")
    record = json.loads(lock.read_text(encoding="utf-8"))
    pid = int(record.get("pid", 0))
    running = lock.parent
    if _worker_alive(record):
        (running / f"{args.name}.cancel").write_text("cancel", encoding="utf-8")
        for _ in range(args.wait * 2):
            if not lock.exists():
                print(f"cancelled {args.name}: it stopped at its next step; its worktree is kept")
                return 0
            time.sleep(0.5)
        # Still inside a model request or a verify: stop it and everything it started.
        procs.kill_tree(pid)
    lock.unlink(missing_ok=True)
    (running / f"{args.name}.cancel").unlink(missing_ok=True)
    log_dir = pathlib.Path(record["log_dir"]) if record.get("log_dir") else _log_dir(args)
    log_dir.mkdir(parents=True, exist_ok=True)
    result = {"task": args.name, "status": "cancelled", "steps": None, "route": record.get("route"),
              "wall_min": round((time.time() - record["started"]) / 60, 1), "workdir": record.get("workdir"),
              "finished_at": time.time()}
    (log_dir / f"{args.name}.outcome.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    (log_dir / f"{args.name}.last.md").write_text("Cancelled.\n", encoding="utf-8")
    print(f"cancelled {args.name} (pid {pid}); its worktree is kept: `crew task rm {args.name}` drops it")
    return 0


def cmd_review(args) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        root = config.find_root()
        fallback = config.load_project(root).branch
    except config.ConfigError:
        # Any git repository can be reviewed; only the default base needs a project file.
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
        if top.returncode != 0:
            raise config.ConfigError("not inside a git repository") from None
        root, fallback = pathlib.Path(top.stdout.strip()), None
    kind = "adversarial" if args.command == "adversarial-review" else "review"
    found = review.run(root, kind, args.base, args.scope, " ".join(args.focus or []), args.model, fallback)
    print(f"target: {found['target']} ({len(found['files'])} file(s))")
    if found["route"]:
        print(f"reviewer: {found['route']}")
    print("\n" + found["review"])
    return 0


def cmd_gate(args) -> int:
    root = config.find_root()
    if args.action in ("enable", "disable"):
        review.set_gate(root, args.action == "enable")
    enabled = review.gate_enabled(root)
    print(f"stop-time review gate for {root}: {'enabled' if enabled else 'disabled'}")
    if enabled:
        print("when Claude stops with uncommitted work here, a reviewer model reviews it and may block the stop")
    return 0


def setup_hint() -> str | None:
    """What the user still has to do before workers can run, in one line, or
    None when crew is ready. No network: this runs at every session start."""
    path = config.providers_path()
    if not path.exists():
        return "Agent Crew is installed but not set up yet: run /agent-crew:setup (or `crew config init`)."
    try:
        loaded = config.load_providers()
    except config.ConfigError as error:
        return f"Agent Crew: {path} has a problem: {error}. Run /agent-crew:doctor."
    if setup.unconfigured(path):
        return "Agent Crew needs its models chosen: run /agent-crew:setup (it finds your router and lists them)."
    keyless = [p.name for p in loaded.providers.values() if not p.keys()]
    if keyless:
        return f"Agent Crew: paste the API key for {', '.join(keyless)} into api_keys in {path}."
    return None


SETUP_CONTEXT = ("Agent Crew is installed but not configured. If the user asks for crew work (delegating to "
                 "worker models, dispatching tasks, crew reviews), run the agent-crew:setup skill first; "
                 "it finds the endpoint and models itself and only asks the user to choose.")


def cmd_hook(args) -> int:
    """Claude Code hooks. `session-start` refreshes the launcher and says what
    setup is still missing. `stop` reads the Stop event on stdin and prints a
    decision. Both exit 0: a failing hook must not wedge the session."""
    if args.event == "session-start":
        try:
            cmd_shim(argparse.Namespace(quiet=True, path=False))
            # Settings entered in Claude Code's plugin settings come first; without
            # them, the providers file exists from the first session, to find and edit.
            synced = plugin_settings.sync()
            if not config.providers_path().exists():
                config.providers_path().parent.mkdir(parents=True, exist_ok=True)
                config.providers_path().write_text(EXAMPLE_PROVIDERS, encoding="utf-8")
            hint = setup_hint()
        except Exception as error:  # noqa: BLE001 - a hook must never take the session down
            synced, hint = None, f"Agent Crew could not start: {error}"
        message = " ".join(part for part in (synced, hint) if part)
        if message:
            sys.stdout.reconfigure(encoding="utf-8")
            output: dict = {"systemMessage": message}
            if hint:
                output["hookSpecificOutput"] = {"hookEventName": "SessionStart",
                                                "additionalContext": SETUP_CONTEXT + " Current state: " + hint}
            print(json.dumps(output, ensure_ascii=False))
        return 0
    try:
        event = json.loads(sys.stdin.buffer.read().decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return 0
    try:
        decision = review.stop_hook(event)
    except Exception as error:  # noqa: BLE001 - a hook must never take the session down
        decision = {"systemMessage": f"Agent Crew review gate failed: {error}"}
    if decision:
        sys.stdout.reconfigure(encoding="utf-8")
        print(json.dumps(decision, ensure_ascii=False))
    return 0


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
        gone = "" if _worker_alive(record) else "  (process gone; `crew cancel` clears it)"
        print(f"  {record['task']:20} {int((time.time() - record['started']) / 60)} min  {record.get('route')}{gone}")
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


def cmd_calibrate(args) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    project = config.load_project()
    loaded = config.load_providers()
    wanted = _split(args.models)
    models = [m for m in loaded.models if not wanted or m.id in wanted]
    unknown = sorted(set(wanted) - {m.id for m in models})
    if unknown:
        raise config.ConfigError(f"not in the pool: {', '.join(unknown)}")
    kinds = _split(args.kinds)
    missing = [k for k in kinds if k not in project.verify]
    if missing:
        raise config.ConfigError(f"no [verify] {', '.join(missing)} in {config.PROJECT_FILE}")
    profile = calibrate.calibrate(project, models, kinds, args.per_kind, args.dry_run,
                                  log=lambda line: print(line, flush=True))
    if args.dry_run:
        _print({"mix": profile["mix"], "probes": profile["probes"], "models": [m.id for m in models]})
        return 0
    path = calibrate.save(project, profile)
    print(f"wrote {path}")
    return cmd_profile(args)


def cmd_profile(args) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    project = config.load_project()
    profile = calibrate.load(project)
    if not profile:
        print("no calibration yet: run `crew calibrate` (or /agent-crew:calibrate)")
        return 0
    print(f"measured {profile.get('measured_at')}")
    print("work mix: " + ", ".join(f"{k} {round(v * 100)}%" for k, v in profile.get("mix", {}).items()))
    for model, entry in profile.get("models", {}).items():
        if entry.get("unavailable"):
            print(f"  {model:40} unavailable")
            continue
        cells = [f"{k} {r['passed']}/{r['of']}" for k, r in entry.get("kinds", {}).items()]
        rank = entry.get("self", {}).get("rank")
        print(f"  {model:40} {'  '.join(cells)}" + (f"   says it suits: {', '.join(rank)}" if rank else ""))
    for kind, order in profile.get("prefer", {}).items():
        print(f"{kind}: " + (" -> ".join(order) or "no model passed; the pool order is used"))
    return 0


def cmd_template(args) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    if not args.name:
        print("\n".join(sorted(p.stem for p in pack.TEMPLATES.glob("*.md"))))
        return 0
    print(pack.template(args.name), end="")
    return 0


def cmd_doctor(args) -> int:
    """Checks everything `crew run` needs, and says how to fix what is missing."""
    problems = 0

    def report(ok: bool, what: str, fix: str = "", note: bool = False) -> None:
        nonlocal problems
        problems += 0 if ok or note else 1
        mark = "ok  " if ok else ("note" if note else "FAIL")
        print(f"{mark} {what}" + ("" if ok or not fix else f"  -> {fix}"))

    report(sys.version_info >= (3, 11), f"Python {sys.version.split()[0]}", "install Python 3.11 or later")
    git = shutil.which("git")
    report(git is not None, f"git {'at ' + git if git else 'not found'}", "install git and put it on PATH")
    launcher = config.home() / "bin" / ("crew.cmd" if os.name == "nt" else "crew")
    report(launcher.exists(), f"launcher {launcher}", "run `crew shim`, or start a new Claude Code session")
    # A PATH changed after Claude Code started is only in the registry until it restarts.
    on_path = shutil.which("crew") is not None or str(launcher.parent).lower() in (
        (config._windows_user_env("Path") or "").lower())
    report(on_path, "crew on PATH" if on_path else "crew not on PATH",
           f"call the launcher by its path, or add {launcher.parent} to PATH", note=True)
    try:
        loaded = config.load_providers()
        report(True, f"providers file {config.providers_path()}")
        report(not setup.unconfigured(config.providers_path()), "models chosen", "run /agent-crew:setup")
        for provider in loaded.providers.values():
            keys = provider.keys()
            labels = [label for label, _ in keys]
            report(bool(labels), f"provider {provider.name}: {'no key needed' if labels == ['no-key'] else f'{len(labels)} key(s) found'}",
                   f"paste the key into api_keys in {config.providers_path()}")
            status, models = setup.list_models(provider.base_url, keys[0][1] if keys else None)
            report(status == "ok", f"provider {provider.name}: {provider.base_url} {status}"
                   + (f", {len(models)} models" if models else ""),
                   "start it, or fix base_url" if status == "down" else "the key was refused")
            if status == "ok":
                missing = [m.id for m in loaded.models if m.provider == provider.name and m.id not in models]
                report(not missing, f"provider {provider.name}: every model in the pool is offered",
                       f"not offered: {', '.join(missing)}")
        for role in ("writer", "reviewer"):
            chain = providers.routes(loaded, role)
            report(bool(chain), f"{role}: {len(chain)} usable route(s)",
                   "give a model this role, or wait for an exhausted allowance to reset (`crew status`)")
    except config.ConfigError as error:
        report(False, str(error), "run `crew config init` and edit the file")
    try:
        project = config.load_project()
        report(True, f"project file in {project.root}")
        report(True, f"stop-time review gate {'enabled' if review.gate_enabled(project.root) else 'disabled'}")
        report(bool(project.verify) and "default" not in project.verify, f"verify kinds: {', '.join(project.verify) or 'none'}",
               "set [verify] commands in .agent-crew/project.toml")
        missing = [s for s in project.shared if not (project.root / s).exists()]
        report(not missing, f"shared dirs: {', '.join(project.shared) or 'none'}", f"missing: {', '.join(missing)}")
        dirty = subprocess.run(["git", "-C", str(project.root), "status", "--porcelain", "--untracked-files=no"],
                               capture_output=True, text=True).stdout.strip()
        report(not dirty, "integration checkout has no uncommitted tracked changes", "commit or stash before landing")
    except config.ConfigError as error:
        print(f"note {error}")
    print("all good" if problems == 0 else f"{problems} problem(s)")
    return 0 if problems == 0 else 1


def cmd_shim(args) -> int:
    """Writes `crew` (POSIX) and `crew.cmd` (Windows) into <crew home>/bin,
    pointing at this installation, so skills and people can call `crew`."""
    package_root = pathlib.Path(__file__).resolve().parent.parent
    bin_dir = config.home() / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    python = pathlib.Path(sys.executable).as_posix()
    entry = package_root / "scripts" / "crew.py"
    # From a plugin checkout, run its entry script (no PYTHONPATH to get wrong
    # across shells); from a pip install, the installed package.
    target = f'"{entry.as_posix()}"' if entry.exists() else "-m crew"
    posix = bin_dir / "crew"
    posix.write_text(f'#!/bin/sh\nexec "{python}" {target} "$@"\n', encoding="utf-8", newline="\n")
    posix.chmod(0o755)
    windows_target = f'"{entry}"' if entry.exists() else "-m crew"
    (bin_dir / "crew.cmd").write_text(f'@echo off\r\n"{sys.executable}" {windows_target} %*\r\n', encoding="utf-8", newline="")
    if not args.quiet:
        print(f"wrote {posix} and {posix}.cmd (package at {package_root})")
    if getattr(args, "path", False):
        print(setup.add_to_path(bin_dir))
    return 0


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(prog="crew", description="Delegate small coding tasks to OpenAI-compatible models, and check their work.")
    top.add_argument("--version", action="version", version=f"agent-crew {__version__}")
    sub = top.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="write .agent-crew/project.toml")
    p.add_argument("--root")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("config", help="the providers file")
    p.add_argument("action", choices=["init", "show", "test", "path", "detect"])
    p.add_argument("--base-url", help="init: write a file for this endpoint (see `crew config detect`)")
    p.add_argument("--name", default="router", help="init: the provider's name")
    p.add_argument("--key-env", help="init: the environment variable holding the key (omit for no key)")
    p.add_argument("--writer", help="init: comma-separated writer model ids, best first")
    p.add_argument("--reviewer", help="init: comma-separated reviewer model ids, best first")
    p.add_argument("--force", action="store_true", help="init: replace a configured file")
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
    p.add_argument("--base", help="what checks compare against, and where a --new-task worktree starts "
                                  "(default: the integration branch)")
    p.add_argument("--log-dir")
    p.add_argument("--new-task", action="store_true", help="create the --task worktree first if it does not exist")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("result", help="a finished run's outcome")
    p.add_argument("name", nargs="?")
    p.add_argument("--log-dir")
    p.set_defaults(func=cmd_result)

    p = sub.add_parser("cancel", help="stop a running worker")
    p.add_argument("name")
    p.add_argument("--wait", type=int, default=20, help="seconds to let it stop at its next step before killing it")
    p.set_defaults(func=cmd_cancel)

    for command, help_text in (("review", "review local changes with a reviewer model"),
                               ("adversarial-review", "challenge local changes: approach, assumptions, failure modes")):
        p = sub.add_parser(command, help=help_text)
        p.add_argument("--base", help="review HEAD against this ref (default: the uncommitted work, else the integration branch)")
        p.add_argument("--scope", default="auto", choices=["auto", "working-tree", "branch"])
        p.add_argument("--model", default="auto")
        p.add_argument("focus", nargs="*", help="what to look at hardest")
        p.set_defaults(func=cmd_review)

    p = sub.add_parser("gate", help="the stop-time review gate")
    p.add_argument("action", nargs="?", default="status", choices=["enable", "disable", "status"])
    p.set_defaults(func=cmd_gate)

    p = sub.add_parser("hook", help="used by the plugin's hooks")
    p.add_argument("event", choices=["session-start", "stop"])
    p.set_defaults(func=cmd_hook)

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

    p = sub.add_parser("template", help="print a built-in prompt template")
    p.add_argument("name", nargs="?")
    p.set_defaults(func=cmd_template)

    p = sub.add_parser("calibrate", help="measure which model does each kind of this repository's work best")
    p.add_argument("--kinds", help="comma-separated verify kinds (default: the biggest in the work mix, up to 3)")
    p.add_argument("--models", help="comma-separated pool models (default: the whole pool)")
    p.add_argument("--per-kind", type=int, default=1, help="probes per kind (default 1)")
    p.add_argument("--dry-run", action="store_true", help="show the work mix and the probes, run nothing")
    p.set_defaults(func=cmd_calibrate)

    p = sub.add_parser("profile", help="what calibration measured")
    p.set_defaults(func=cmd_profile)

    p = sub.add_parser("doctor", help="check the setup")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("shim", help="write the crew launcher")
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--path", action="store_true", help="also put the launcher's directory on the user's PATH")
    p.set_defaults(func=cmd_shim)
    return top


def main(argv: list[str] | None = None) -> int:
    # Summaries carry whatever a model wrote; a legacy console code page must not crash on it.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = parser().parse_args(argv)
    if args.command == "task" and args.action in ("new", "rm") and not args.name:
        print("crew task new|rm needs a name", file=sys.stderr)
        return 2
    try:
        return args.func(args)
    except (config.ConfigError, worktree.WorktreeError, land.LandError, review.ReviewError) as error:
        print(f"crew: {error}", file=sys.stderr)
        return 2
