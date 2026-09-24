"""Choosing a model and a key for each request, and moving on when one runs out.

A *route* is one (model, key) pair. For a role, the candidate routes are every
model that may play it, in order of priority and then of measured latency, each
with every key its provider has. A route is skipped while its key is exhausted
for that model's allowance: providers often run several allowances side by
side (a rolling free tier, a campaign model with its own quota), and running
out of one says nothing about the others, so exhaustion is recorded per
(key, allowance) rather than per key.

The client talks to any OpenAI-compatible ``/chat/completions`` endpoint with
the standard library only.
"""

from __future__ import annotations

import calendar
import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from crew import config

QUOTA_WORDS = re.compile(r"quota|allowance|exhaust|insufficient|limit_reached|credit|billing", re.I)
RESET = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})")
TRANSIENT = (429, 500, 502, 503, 504, 520, 522, 524)


@dataclass
class Route:
    model: config.Model
    provider: config.Provider
    key_label: str
    key: str

    @property
    def name(self) -> str:
        return f"{self.model.id} via {self.provider.name} ({self.key_label})"


class QuotaExhausted(Exception):
    """This route's key has used up this model's allowance."""


class RouteFailed(Exception):
    """This route keeps failing for a reason that is not the request's fault."""


def _marker(route_key_label: str, allowance: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", f"{route_key_label}.{allowance}")
    return f"exhausted.{safe}"


def exhausted(key_label: str, allowance: str) -> bool:
    path = config.state_dir() / _marker(key_label, allowance)
    if not path.exists():
        return False
    try:
        until = float(path.read_text(encoding="utf-8").split()[0])
    except (ValueError, IndexError, OSError):
        return True
    if time.time() >= until:
        path.unlink(missing_ok=True)
        return False
    return True


def mark_exhausted(key_label: str, allowance: str, message: str) -> float:
    """Records that a key ran out of an allowance, until the reset time the
    provider named, or for an hour when it named none."""
    match = RESET.search(message)
    until = time.time() + 3600
    if match:
        until = calendar.timegm(time.strptime(match[1], "%Y-%m-%dT%H:%M:%S"))
    (config.state_dir() / _marker(key_label, allowance)).write_text(f"{until} {message[:300]}", encoding="utf-8")
    return until


def exhausted_markers() -> list[tuple[str, float, str]]:
    found = []
    for path in sorted(config.state_dir().glob("exhausted.*")):
        try:
            until, _, message = path.read_text(encoding="utf-8").partition(" ")
            found.append((path.name[len("exhausted."):], float(until), message))
        except (OSError, ValueError):
            continue
    return found


def _latency() -> dict[str, float]:
    path = config.state_dir() / "latency.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def record_latency(model_id: str, seconds: float) -> None:
    path = config.state_dir() / "latency.json"
    data = _latency()
    previous = data.get(model_id)
    # A moving average, so one slow call does not banish a model.
    data[model_id] = seconds if previous is None else round(0.7 * previous + 0.3 * seconds, 3)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def routes(providers: config.Providers, role: str, model: str = "auto") -> list[Route]:
    """Every usable route for a role, best first. `model` pins one model id,
    or names a comma-separated preference list; `auto` uses the defaults
    table's entry for the role if it has one, else every model with the role."""
    choice = providers.defaults.get(role, "auto") if model == "auto" else model
    latency = _latency()
    if choice == "auto":
        candidates = [m for m in providers.models if role in m.roles]
        candidates.sort(key=lambda m: (m.priority, latency.get(m.id, 60.0)))
    else:
        wanted = [c.strip() for c in choice.split(",") if c.strip()]
        by_id = {m.id: m for m in providers.models}
        missing = [w for w in wanted if w not in by_id]
        if missing:
            raise config.ConfigError(f"no [[model]] with id {', '.join(missing)}")
        candidates = [by_id[w] for w in wanted]
    found = []
    for candidate in candidates:
        provider = providers.provider(candidate.provider)
        for label, key in provider.keys():
            if not exhausted(label, candidate.allowance):
                found.append(Route(candidate, provider, label, key))
    return found


def complete(route: Route, messages: list, tools: list | None = None, timeout: int = 300, retries: int = 4) -> dict:
    """One chat completion over a route. Retries transient failures with
    backoff; raises QuotaExhausted when the provider says the allowance is
    spent, and RouteFailed when the route keeps failing."""
    body: dict = {"model": route.model.id, "messages": messages, "temperature": 0.2}
    if tools:
        body["tools"] = [{"type": "function", "function": tool} for tool in tools]
        body["tool_choice"] = "auto"
    data = json.dumps(body).encode()
    headers = {"Authorization": f"Bearer {route.key}", "Content-Type": "application/json", **route.provider.headers}
    last = ""
    for attempt in range(retries):
        request = urllib.request.Request(f"{route.provider.base_url}/chat/completions", data, headers)
        started = time.time()
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                reply = json.load(response)
            record_latency(route.model.id, time.time() - started)
            if not reply.get("choices"):
                raise RouteFailed(f"{route.name}: reply had no choices: {json.dumps(reply)[:300]}")
            return reply
        except urllib.error.HTTPError as error:
            text = error.read().decode("utf-8", errors="replace")
            last = f"HTTP {error.code}: {text[:300]}"
            if error.code in (402, 403, 429) and QUOTA_WORDS.search(text):
                raise QuotaExhausted(text) from None
            if error.code in (401,):
                raise RouteFailed(f"{route.name}: the key was refused ({last})") from None
            if error.code in (400, 404, 422):
                raise RouteFailed(f"{route.name}: the request was refused ({last})") from None
            if error.code not in TRANSIENT:
                raise RouteFailed(f"{route.name}: {last}") from None
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
            last = str(error)
        time.sleep(min(60, 4 * 2 ** attempt))
    raise RouteFailed(f"{route.name}: gave up after {retries} attempts: {last}")


def probe(route: Route) -> tuple[bool, float, str]:
    """A one-line request, for `crew config test`: does this route answer, and how fast."""
    started = time.time()
    try:
        reply = complete(route, [{"role": "user", "content": "Reply with the single word: ok"}], retries=1, timeout=120)
        text = (reply["choices"][0]["message"].get("content") or "").strip()
        return True, time.time() - started, text[:40]
    except QuotaExhausted as error:
        until = mark_exhausted(route.key_label, route.model.allowance, str(error))
        return False, time.time() - started, f"allowance exhausted until {time.strftime('%Y-%m-%d %H:%M', time.gmtime(until))} UTC"
    except RouteFailed as error:
        return False, time.time() - started, str(error)[:160]
