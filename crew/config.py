"""Where Agent Crew keeps its files, and how it reads its two configuration files.

Two files, because they answer different questions:

* ``providers.toml`` says which model endpoint to use, its key, and which
  models may play which role. It lives either in the repository, as
  ``.agent-crew/providers.toml`` (kept out of git, like a ``.env``), or in the
  crew home, shared by every repository; the repository's own wins.
* ``.agent-crew/project.toml`` (per repository, committed) says how that
  repository is built and tested, where task worktrees go, and what they share.

The crew home is ``$AGENT_CREW_HOME``, or ``~/.agent-crew``.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import tomllib
from dataclasses import dataclass, field

PROJECT_FILE = pathlib.Path(".agent-crew") / "project.toml"


def home() -> pathlib.Path:
    """The crew home: configuration, run state and logs."""
    return pathlib.Path(os.environ.get("AGENT_CREW_HOME") or pathlib.Path.home() / ".agent-crew")


def state_dir() -> pathlib.Path:
    path = home() / "state"
    path.mkdir(parents=True, exist_ok=True)
    return path


PROJECT_PROVIDERS = pathlib.Path(".agent-crew") / "providers.toml"


def global_providers_path() -> pathlib.Path:
    """The providers file shared by every repository: in the crew home."""
    return home() / "providers.toml"


def _main_worktree(start: pathlib.Path) -> pathlib.Path | None:
    """The main checkout of the repository `start` is in, so a task worktree
    (where the git-ignored providers file does not exist) finds the project's."""
    try:
        done = subprocess.run(["git", "-C", str(start), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                              capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    common = done.stdout.strip()
    return pathlib.Path(common).parent if done.returncode == 0 and common else None


def project_providers_path(start: pathlib.Path | None = None) -> pathlib.Path | None:
    """The repository's own providers file, if it has one: `.agent-crew/providers.toml`
    in the nearest directory with a `.agent-crew`, or in the repository's main checkout."""
    here = (start or pathlib.Path.cwd()).resolve()
    # A project is a directory with `.agent-crew/project.toml`, not merely one
    # with `.agent-crew`: the crew home is `~/.agent-crew`, so every path under
    # the home directory would otherwise take the shared file for a project's.
    candidates = [directory for directory in (here, *here.parents) if (directory / PROJECT_FILE).is_file()]
    main = _main_worktree(here)
    if main is not None and (main / PROJECT_FILE).is_file():
        candidates.append(main)
    for directory in candidates:
        path = directory / PROJECT_PROVIDERS
        if path.is_file():
            return path
    return None


def providers_path(start: pathlib.Path | None = None) -> pathlib.Path:
    """The providers file in use: the repository's own, else the shared one."""
    return project_providers_path(start) or global_providers_path()


class ConfigError(Exception):
    """A configuration file is missing, unreadable or inconsistent."""


@dataclass
class Provider:
    name: str
    base_url: str
    key_envs: list[str] = field(default_factory=list)
    key_file: str | None = None
    inline_keys: list[str] = field(default_factory=list)
    headers: dict[str, str] = field(default_factory=dict)

    def keys(self) -> list[tuple[str, str]]:
        """Every key this provider can use, as (label, key). The label names the
        key in state and logs without revealing it. A provider that names no
        key at all (a local router without auth) has one empty key."""
        if not self.key_envs and not self.key_file and not self.inline_keys:
            return [("no-key", "")]
        found: list[tuple[str, str]] = []
        for index, value in enumerate(self.inline_keys, 1):
            if value.strip():
                found.append((f"{self.name}.key{index}", value.strip()))
        for var in self.key_envs:
            value = os.environ.get(var) or _windows_user_env(var)
            if value:
                found.append((var, value.strip()))
        if self.key_file:
            path = pathlib.Path(os.path.expanduser(self.key_file))
            if path.exists():
                for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                    line = line.strip()
                    if line and not line.startswith("#"):
                        found.append((f"{path.name}#{index}", line))
        return found


@dataclass
class Model:
    id: str
    provider: str
    roles: list[str]
    allowance: str = "default"
    priority: int = 100
    context: int = 128_000


@dataclass
class Providers:
    providers: dict[str, Provider]
    models: list[Model]
    defaults: dict[str, str]

    def provider(self, name: str) -> Provider:
        try:
            return self.providers[name]
        except KeyError:
            raise ConfigError(f"model refers to unknown provider {name!r}") from None


def load_providers(path: pathlib.Path | None = None) -> Providers:
    path = path or providers_path()
    if not path.exists():
        raise ConfigError(f"{path} does not exist; run `crew config init`")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"{path}: {error}") from None
    providers = {}
    for entry in data.get("provider", []):
        try:
            provider = Provider(
                name=entry["name"],
                base_url=entry["base_url"].rstrip("/"),
                key_envs=list(entry.get("api_key_envs", [])) + ([entry["api_key_env"]] if "api_key_env" in entry else []),
                key_file=entry.get("api_key_file"),
                inline_keys=[str(k) for k in ([entry["api_key"]] if "api_key" in entry else []) + list(entry.get("api_keys", []))],
                headers=dict(entry.get("headers", {})),
            )
        except KeyError as missing:
            raise ConfigError(f"{path}: a [[provider]] is missing {missing}") from None
        providers[provider.name] = provider
    models = []
    for entry in data.get("model", []):
        try:
            model = Model(
                id=entry["id"],
                provider=entry["provider"],
                roles=list(entry.get("roles", ["writer", "reviewer"])),
                allowance=str(entry.get("allowance", "default")),
                priority=int(entry.get("priority", 100)),
                context=int(entry.get("context", 128_000)),
            )
        except KeyError as missing:
            raise ConfigError(f"{path}: a [[model]] is missing {missing}") from None
        if model.provider not in providers:
            raise ConfigError(f"{path}: model {model.id!r} names unknown provider {model.provider!r}")
        models.append(model)
    if not models:
        raise ConfigError(f"{path}: no [[model]] entries")
    return Providers(providers=providers, models=models, defaults=dict(data.get("defaults", {})))


@dataclass
class Project:
    root: pathlib.Path
    branch: str
    worktrees: pathlib.Path
    shared: list[str]
    verify: dict[str, str]
    checks: list[str]
    max_steps: int
    source_suffixes: list[str]
    ignore_strays: list[str] = field(default_factory=list)


def find_root(start: pathlib.Path | None = None) -> pathlib.Path:
    """The nearest directory, from start upwards, holding .agent-crew/project.toml."""
    here = (start or pathlib.Path.cwd()).resolve()
    for directory in (here, *here.parents):
        if (directory / PROJECT_FILE).exists():
            return directory
    raise ConfigError(f"no {PROJECT_FILE.as_posix()} in {here} or above; run `crew init` in the repository")


def load_project(root: pathlib.Path | None = None) -> Project:
    root = root or find_root()
    path = root / PROJECT_FILE
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ConfigError(f"{path}: {error}") from None
    worktrees = pathlib.Path(os.path.expanduser(data.get("worktrees", f"../{root.name}-crew")))
    if not worktrees.is_absolute():
        worktrees = (root / worktrees).resolve()
    return Project(
        root=root,
        branch=data.get("branch", "main"),
        worktrees=worktrees,
        shared=list(data.get("shared", [])),
        verify={str(k): str(v) for k, v in data.get("verify", {}).items()},
        checks=list(data.get("checks", ["text", "strays"])),
        max_steps=int(data.get("max_steps", 25)),
        source_suffixes=list(data.get("source_suffixes", [".rs", ".ts", ".tsx", ".py", ".json", ".md", ".toml", ".css"])),
        ignore_strays=list(data.get("ignore_strays", [])),
    )


def _windows_user_env(var: str) -> str | None:
    """A variable set with `setx` is invisible to processes started before it;
    read it from the registry so a key set in another terminal still works."""
    if os.name != "nt":
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _ = winreg.QueryValueEx(key, var)
            return str(value)
    except OSError:
        return None
