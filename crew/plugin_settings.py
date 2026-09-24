"""The plugin's own settings: endpoint, key and models entered in Claude Code.

The plugin declares them as `userConfig`, so Claude Code asks for them when
the plugin is enabled, stores the key in the system's secure storage, and
passes every value to the plugin's hooks as `CLAUDE_PLUGIN_OPTION_<KEY>`. At
each session start the hook hands them here, and the providers file is
written from them, so a person never has to find or edit it.

Workers are started from Claude's shell, which does not see those variables,
so the key is kept where they can read it: a file in the crew home that only
this user can open, named in the providers file rather than written into it.

When no models are given, the endpoint's own list is read and the pool is
chosen from it: writers from the models whose names say fast or code, and a
reviewer from a different family than the writers, so a review is a second
opinion. Calibration then measures that choice on the repository's own work.
"""

from __future__ import annotations

import hashlib
import os
import pathlib
import re

from crew import config, setup

MANAGED = "# managed: Agent Crew plugin settings. Change them in Claude Code's plugin settings, not here."
OPTIONS = ("endpoint", "api_key", "writer_models", "reviewer_models")

NOT_CHAT = re.compile(r"embed|whisper|tts|speech|transcri|image|dall-?e|vision-only|moderation|rerank|audio", re.I)
WRITER_HINTS = [(re.compile(r"cod(e|er)", re.I), 3), (re.compile(r"flash|fast|turbo|mini|haiku|lite|small", re.I), 2)]
REVIEWER_HINTS = [(re.compile(r"opus|sonnet|pro\b|large|max|plus|ultra|reason", re.I), 2)]
SLOW = re.compile(r"think|reason|r1\b|o1\b|o3\b", re.I)


def options(environ: dict[str, str] | None = None) -> dict[str, str]:
    """The settings Claude Code passed to this hook, by name ("" when unset)."""
    environ = os.environ if environ is None else environ
    found = {}
    for name in OPTIONS:
        value = environ.get(f"CLAUDE_PLUGIN_OPTION_{name.upper()}", environ.get(f"CLAUDE_PLUGIN_OPTION_{name}", ""))
        found[name] = value.strip()
    return found


def _split(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def family(model: str) -> str:
    """The model's family from its name: `harbor/qwen3.8-flash:free` is `qwen`."""
    name = model.rsplit("/", 1)[-1].lower()
    match = re.match(r"[a-z]+", name)
    return match[0] if match else name


def _score(model: str, hints: list) -> int:
    return sum(weight for pattern, weight in hints if pattern.search(model))


def choose(models: list[str], writers: int = 3, reviewers: int = 2) -> tuple[list[str], list[str]]:
    """Writers and reviewers from an endpoint's list, best first."""
    chat = [m for m in models if not NOT_CHAT.search(m)]
    if not chat:
        return [], []
    ranked = sorted(chat, key=lambda m: (-_score(m, WRITER_HINTS), bool(SLOW.search(m)), m))
    picked_writers = ranked[:writers]
    families = {family(m) for m in picked_writers}
    others = [m for m in chat if m not in picked_writers]
    ranked_reviewers = sorted(others, key=lambda m: (family(m) in families, -_score(m, REVIEWER_HINTS), m))
    picked_reviewers = ranked_reviewers[:reviewers] or picked_writers[-1:]
    return picked_writers, picked_reviewers


def key_file() -> pathlib.Path:
    return config.home() / "keys" / "plugin-settings.key"


def _write_key(key: str) -> pathlib.Path:
    path = key_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(key + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def managed(path: pathlib.Path) -> bool:
    return path.exists() and path.read_text(encoding="utf-8").startswith(MANAGED)


def fingerprint(chosen: dict[str, str]) -> str:
    """A hash of the settings, so an unchanged session start does nothing,
    not even ask the endpoint for its models. The key is hashed, never kept here."""
    return hashlib.sha256("\0".join(chosen[name] for name in OPTIONS).encode("utf-8")).hexdigest()[:16]


def _settings_line(chosen: dict[str, str]) -> str:
    return f"# settings: {fingerprint(chosen)}"


def providers_text(chosen: dict[str, str], writers: list[str], reviewers: list[str], auto: bool) -> str:
    endpoint, has_key = chosen["endpoint"], bool(chosen["api_key"])
    text =setup.providers_file("endpoint", endpoint, None, writers, reviewers, needs_key=False)
    body = text.split("\n", 2)[2]  # drop the two header comment lines providers_file writes
    key_line = (f'api_key_file = "{key_file().as_posix()}"   # kept by the plugin; set it in the plugin settings'
                if has_key else "api_keys = []   # no key was set in the plugin settings")
    body = body.replace("api_keys = []   # this endpoint needs no key", key_line, 1)
    how = ("The models were chosen automatically from the endpoint's list; name them in the plugin settings, "
           "or let /agent-crew:calibrate measure them." if auto else "The models are the ones named in the plugin settings.")
    return f"{MANAGED}\n{_settings_line(chosen)}\n# {how}\n{body}"


def sync(environ: dict[str, str] | None = None) -> str | None:
    """Writes the providers file from the plugin settings when there are any and
    the file is not one the person wrote by hand. Returns a one-line note when
    something changed or needs attention, else None."""
    chosen = options(environ)
    if not chosen["endpoint"]:
        return None
    project = config.project_providers_path()
    if project is not None:
        # The repository's own file is the one in use; the settings are for the shared one.
        return f"Agent Crew uses this project's {project}; the plugin settings apply to projects without one."
    path = config.global_providers_path()
    if path.exists() and not managed(path) and not setup.unconfigured(path):
        # A providers file written by hand wins over the settings; say so, since
        # otherwise the settings look ignored for no reason.
        return (f"Agent Crew uses {path}, which was written by hand, so the plugin settings are not applied. "
                "Delete that file to use the settings instead.")
    if managed(path) and _settings_line(chosen) in path.read_text(encoding="utf-8").splitlines()[:3]:
        return None  # written from these very settings already
    key = chosen["api_key"]
    writers, reviewers = _split(chosen["writer_models"]), _split(chosen["reviewer_models"])
    auto = not (writers and reviewers)
    if auto:
        status, models = setup.list_models(chosen["endpoint"], key or None)
        if status != "ok":
            reason = "refused the key" if status == "needs-key" else "did not answer"
            return (f"Agent Crew: {chosen['endpoint']} {reason}, so no models could be chosen. "
                    "Check the endpoint and key in the plugin settings, or name the models there.")
        picked_writers, picked_reviewers = choose(models)
        writers = writers or picked_writers
        reviewers = reviewers or picked_reviewers
        if not writers or not reviewers:
            return f"Agent Crew: {chosen['endpoint']} lists no chat models to choose from; name them in the plugin settings."
    if key:
        _write_key(key)
    elif key_file().exists():
        key_file().unlink()
    text = providers_text(chosen, writers, reviewers, auto)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return (f"Agent Crew is set up from the plugin settings: writers {', '.join(writers)}; "
            f"reviewers {', '.join(reviewers)}" + (" (chosen automatically)." if auto else "."))
