"""Where Agent Crew keeps its files, and how it reads its two configuration files.

Two files, because they answer different questions:

* ``providers.toml`` (per user, in the crew home) says which model endpoints
  exist, where their keys come from, and which models may play which role.
  It never holds a key itself: keys come from environment variables or from a
  key file, so the configuration can be shared and the keys cannot leak with it.
* ``.agent-crew/project.toml`` (per repository) says how that repository is
  built and tested, where task worktrees go, and what they share.

The crew home is ``$AGENT_CREW_HOME``, or ``~/.agent-crew``.
"""

from __future__ import annotations

import os
import pathlib
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


def providers_path() -> pathlib.Path:
    return home() / "providers.toml"


class ConfigError(Exception):
    """A configuration file is missing, unreadable or inconsistent."""


@dataclass
class Provider:
    name: str
    base_url: str
    key_envs: list[str] = field(default_factory=list)
    key_file: str | None = None
    headers: dict[str, str] = field(default_factory=dict)

    def keys(self) -> list[tuple[str, str]]:
        """Every key this provider can use, as (label, key). The label names the
        key in state and logs without revealing it. A provider that names no
        key at all (a local router without auth) has one empty key."""
        if not self.key_envs and not self.key_file:
            return [("no-key", "")]
        found: list[tuple[str, str]] = []
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
