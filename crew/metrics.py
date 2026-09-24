"""Where a run's time and tokens went, from its stamped event log."""

from __future__ import annotations

import collections
import json
import pathlib


def summarise(path: pathlib.Path) -> dict:
    events = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    stamped = [e for e in events if "_ts" in e]
    started: dict[str, float] = {}
    counts: collections.Counter = collections.Counter()
    seconds: collections.Counter = collections.Counter()
    tokens_in = tokens_out = 0
    status = ""
    for event in events:
        item = event.get("item", {})
        if item.get("type") == "command_execution":
            tool = item.get("command", "").split(" ", 1)[0]
            if event.get("type") == "item.started" and "_ts" in event:
                started[item["id"]] = event["_ts"]
            elif event.get("type") == "item.completed":
                counts[tool] += 1
                if item["id"] in started and "_ts" in event:
                    seconds[tool] += event["_ts"] - started.pop(item["id"])
        if event.get("type") == "turn.completed":
            usage = event.get("usage", {})
            tokens_in += usage.get("input_tokens", 0)
            tokens_out += usage.get("output_tokens", 0)
            status = event.get("status", status)
    wall = stamped[-1]["_ts"] - stamped[0]["_ts"] if len(stamped) > 1 else 0.0
    tool_time = sum(seconds.values())
    calls = sum(counts.values())
    return {
        "status": status,
        "wall_min": round(wall / 60, 1),
        "model_min": round((wall - tool_time) / 60, 1),
        "tool_min": round(tool_time / 60, 1),
        "tool_calls": dict(counts),
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "sec_per_model_step": round((wall - tool_time) / max(1, calls), 1),
    }
