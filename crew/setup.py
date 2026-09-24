"""Setting Agent Crew up without hand-editing: find an OpenAI-compatible
endpoint on this machine, list its models, write the providers file, read a
repository to write its project file, and put the launcher on PATH.

Nothing here handles an API key's value. A key is named by the environment
variable that holds it; the user sets that variable.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import tomllib
import urllib.error
import urllib.request

from crew import config

# Local routers and servers people commonly run, with their default ports.
KNOWN_ENDPOINTS = [
    ("9router", "http://localhost:20128/v1"),
    ("litellm", "http://localhost:4000/v1"),
    ("ollama", "http://localhost:11434/v1"),
    ("lmstudio", "http://localhost:1234/v1"),
    ("vllm", "http://localhost:8000/v1"),
]

PLACEHOLDER = re.compile(r"^(set-me|your-)", re.I)


def list_models(base_url: str, key: str | None = None, timeout: float = 4) -> tuple[str, list[str]]:
    """(status, model ids) for an endpoint: status is `ok`, `needs-key`
    (it answered, but refused without a valid key) or `down`."""
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    request = urllib.request.Request(f"{base_url.rstrip('/')}/models", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.load(response)
    except urllib.error.HTTPError as error:
        return ("needs-key" if error.code in (401, 403) else "down"), []
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return "down", []
    entries = data.get("data", data.get("models", [])) if isinstance(data, dict) else data
    ids = [e.get("id") or e.get("name") if isinstance(e, dict) else str(e) for e in entries or []]
    return "ok", sorted({i for i in ids if i})


def detect() -> list[dict]:
    """Every known local endpoint, plus the configured providers, with what each answers."""
    seen, found = set(), []
    try:
        configured = [(p.name, p.base_url, p) for p in config.load_providers().providers.values()]
    except config.ConfigError:
        configured = []
    for name, url, provider in configured + [(n, u, None) for n, u in KNOWN_ENDPOINTS]:
        if url.rstrip("/") in seen:
            continue
        seen.add(url.rstrip("/"))
        keys = provider.keys() if provider else []
        status, models = list_models(url, keys[0][1] if keys else None)
        found.append({"name": name, "base_url": url, "configured": provider is not None,
                      "status": status, "models": models})
    return found


def unconfigured(path: pathlib.Path) -> bool:
    """True when the providers file is missing or still the template: safe to replace."""
    if not path.exists():
        return True
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError:
        return False
    ids = [m.get("id", "") for m in data.get("model", [])]
    return not ids or all(PLACEHOLDER.match(i) for i in ids)


def _toml_list(items: list[str]) -> str:
    return "[" + ", ".join(json.dumps(i) for i in items) + "]"


def providers_file(name: str, base_url: str, key_env: str | None, writers: list[str], reviewers: list[str],
                   needs_key: bool = True) -> str:
    """The whole setup in one file: the endpoint, its key, and the pool of
    models crew may use (nothing outside it is ever called). A model named as
    both writer and reviewer gets both roles; order is priority."""
    if not writers or not reviewers:
        raise config.ConfigError("name at least one writer and one reviewer model")
    if not needs_key:
        key_lines = ["api_keys = []   # this endpoint needs no key"]
    else:
        key_lines = ['api_keys = [""]   # paste the key between the quotes; several keys: ["k1", "k2"], used in turn']
        if key_env:
            key_lines.append(f"api_key_envs = [{json.dumps(key_env)}]   # or keep it in this environment variable")
    lines = ["# Agent Crew: the endpoint, its key and the model pool, all in this one file.",
             "# It lives in your home directory, outside every repository. Edit freely.", "",
             "[defaults]", 'writer = "auto"     # by priority, then measured latency', 'reviewer = "auto"', "",
             "[[provider]]", f"name = {json.dumps(name)}", f"base_url = {json.dumps(base_url.rstrip('/'))}", *key_lines,
             "", "# The pool: crew only ever calls these models."]
    order = list(dict.fromkeys(writers + reviewers))
    for index, model in enumerate(order):
        roles = [r for r, group in (("writer", writers), ("reviewer", reviewers)) if model in group]
        lines += ["", "[[model]]", f"id = {json.dumps(model)}", f"provider = {json.dumps(name)}",
                  f"roles = {_toml_list(roles)}", f"allowance = {json.dumps(model)}   # models sharing a quota share a label",
                  f"priority = {10 * (index + 1)}"]
    return "\n".join(lines) + "\n"


# Repositories -------------------------------------------------------------

def _scripts(package: pathlib.Path) -> dict:
    try:
        return json.loads(package.read_text(encoding="utf-8")).get("scripts", {})
    except (OSError, json.JSONDecodeError):
        return {}


HEAVY = {"node_modules", "target", ".venv", "venv"}
SKIP = {".git", "dist", "build", ".agent-crew", "__pycache__"}


def _walk(root: pathlib.Path, depth: int = 4) -> tuple[list[pathlib.Path], list[pathlib.Path]]:
    """(manifest files, heavy directories) within `depth` levels, never
    descending into a heavy or skipped directory."""
    manifests, heavy = [], []
    for directory, names, files in os.walk(root):
        here = pathlib.Path(directory)
        level = len(here.relative_to(root).parts)
        manifests += [here / f for f in files if f in ("Cargo.toml", "package.json", "pyproject.toml", "go.mod")]
        heavy += [here / n for n in names if n in HEAVY]
        names[:] = [n for n in names if n not in HEAVY | SKIP and not n.startswith(".") and level < depth]
    return manifests, heavy


def detect_project(root: pathlib.Path) -> dict:
    """Verify commands, shared directories and checks, read off the files a
    repository has. Each verify formats, builds, lints and tests what exists."""
    verify: dict[str, str] = {}
    checks = ["text", "strays"]

    manifests, heavy = _walk(root)
    shared = sorted(d.relative_to(root).as_posix() for d in heavy)
    cargo = sorted((p for p in manifests if p.name == "Cargo.toml"), key=lambda p: len(p.parts))
    if cargo:
        crate = cargo[0].parent.relative_to(root).as_posix()
        at = "" if crate == "." else f" --manifest-path {crate}/Cargo.toml"
        verify["rust"] = (f"cargo fmt{at} && cargo clippy{at} --all-targets -- -D warnings && cargo test{at}")
        checks.append("rust-modules")

    package = root / "package.json"
    if package.exists():
        scripts = _scripts(package)
        steps = [f"npm run -s {s}" for s in ("format:check", "typecheck", "lint", "test") if s in scripts]
        if "test" in scripts and "vitest" in scripts["test"] and "run" not in scripts["test"]:
            steps[-1] = "npm run -s test -- --run"
        if steps:
            verify["ts" if (root / "tsconfig.json").exists() or any("typecheck" in s for s in steps) else "js"] = " && ".join(steps)

    if (root / "pyproject.toml").exists() or (root / "setup.py").exists():
        text = (root / "pyproject.toml").read_text(encoding="utf-8") if (root / "pyproject.toml").exists() else ""
        steps = []
        if "ruff" in text:
            steps += ["python -m ruff format .", "python -m ruff check ."]
        steps.append("python -m pytest -q")
        verify["python"] = " && ".join(steps)

    if (root / "go.mod").exists():
        verify["go"] = "gofmt -l . && go vet ./... && go test ./..."

    return {"verify": verify, "shared": shared, "checks": checks}


def project_file(root: pathlib.Path, branch: str) -> str:
    found = detect_project(root)
    verify = found["verify"] or {"default": "echo set a [verify] command in .agent-crew/project.toml && exit 1"}
    lines = ["# Agent Crew project settings, written by `crew init` from what this repository has.",
             "# Check the verify commands, then commit this file.", "",
             f"branch = {json.dumps(branch)}                  # the integration branch tasks land on",
             f"worktrees = {json.dumps('../' + root.name + '-crew')}   # where task worktrees are created",
             f"shared = {_toml_list(found['shared'])}   # ignored heavy dirs linked into each worktree",
             f"checks = {_toml_list(found['checks'])}",
             "ignore_strays = []", "max_steps = 25", "", "[verify]",
             "# One command per kind. It passes when it exits 0 and does not print RESULT: FAIL."]
    lines += [f"{kind} = {json.dumps(command)}" for kind, command in found["verify"].items()] or [
        f"default = {json.dumps(verify['default'])}"]
    return "\n".join(lines) + "\n"


# PATH ---------------------------------------------------------------------

def add_to_path(directory: pathlib.Path) -> str:
    """Puts `directory` on the user's PATH for new terminals. Returns what it did."""
    directory = directory.resolve()
    if os.name == "nt":
        import ctypes
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ | winreg.KEY_WRITE) as key:
            try:
                current, kind = winreg.QueryValueEx(key, "Path")
            except OSError:
                current, kind = "", winreg.REG_EXPAND_SZ
            parts = [p for p in current.split(";") if p]
            if any(os.path.normcase(p.rstrip("\\")) == os.path.normcase(str(directory)) for p in parts):
                return f"{directory} is already on your PATH"
            winreg.SetValueEx(key, "Path", 0, kind, ";".join(parts + [str(directory)]))
        # Tell running programs the environment changed (HWND_BROADCAST, WM_SETTINGCHANGE).
        ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "Environment", 0x0002, 2000, None)
        return f"added {directory} to your user PATH; new terminals will find `crew`"
    line = f'export PATH="{directory}:$PATH"  # Agent Crew'
    written = []
    for name in (".profile", ".zshrc", ".bashrc"):
        profile = pathlib.Path.home() / name
        if name != ".profile" and not profile.exists():
            continue
        text = profile.read_text(encoding="utf-8") if profile.exists() else ""
        if str(directory) not in text:
            with open(profile, "a", encoding="utf-8") as handle:
                handle.write(("\n" if text and not text.endswith("\n") else "") + line + "\n")
            written.append(name)
    return (f"added {directory} to PATH in ~/{', ~/'.join(written)}; new terminals will find `crew`" if written
            else f"{directory} is already on your PATH")
