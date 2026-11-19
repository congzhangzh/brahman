"""What a step cost (specs/views.md, phase 3): tokens, wall time and active time, from the marking session's
own Claude Code transcript, between its "doing" and its end. By script, $0, read locally at mark time.

- Tokens: each API response once (the transcript repeats a response per content block with the same
  usage: counted by requestId), in, out, cache read, cache write. The session's subagents run in
  transcripts of their own: not counted here, and the result says so (`main_only`).
- $: only when [views.prices] in buddy.toml names the model's prices per million tokens
  ({"claude-opus-5-5": {"in": …, "out": …, "cache_read": …, "cache_write": …}}); no guessed price.
- Active time: the gaps between the session's own entries, each capped at IDLE, so a wait for the owner
  or a long build does not count as work. Wall time: end minus start.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

IDLE = 300  # seconds: a longer gap between two entries is waiting, not working


def transcript(session_id: str, home: Path | None = None) -> Path | None:
    base = (home or Path.home()) / ".claude" / "projects"
    hits = sorted(base.glob(f"*/{session_id}.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True) if base.exists() else []
    return hits[0] if hits else None


def _ts(s: str) -> float | None:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def between(path: Path | None, start: float, end: float, prices: dict | None = None) -> dict | None:
    """The cost of the session's work from start to end (epoch seconds), or None without a transcript."""
    if path is None or not path.exists() or end <= start:
        return None
    seen, times, anon = set(), [], 0
    tok = {"in": 0, "out": 0, "cache_read": 0, "cache_write": 0}
    models: dict[str, dict] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            t = _ts(d.get("timestamp", ""))
            if t is None or not start <= t <= end or d.get("isSidechain"):
                continue
            times.append(t)
            msg = d.get("message") or {}
            u = msg.get("usage") if d.get("type") == "assistant" else None
            rid = d.get("requestId") or msg.get("id")
            if not u or (rid and rid in seen):
                continue
            if rid:
                seen.add(rid)
            else:  # no id to count it once by: each such entry is its own call
                anon += 1
            got = {"in": u.get("input_tokens", 0), "out": u.get("output_tokens", 0),
                   "cache_read": u.get("cache_read_input_tokens", 0), "cache_write": u.get("cache_creation_input_tokens", 0)}
            for k, v in got.items():
                tok[k] += int(v or 0)
            m = models.setdefault(msg.get("model") or "?", {k: 0 for k in tok})
            for k, v in got.items():
                m[k] += int(v or 0)
    times.sort()
    active = sum(min(b - a, IDLE) for a, b in zip(times, times[1:]))
    usd = None
    if prices and models and all(name in prices for name in models):
        usd = round(sum(m[k] * float(prices[name].get(k, 0)) / 1e6 for name, m in models.items() for k in m), 4)
    return {"tokens": tok, "calls": len(seen) + anon, "models": sorted(models), "usd": usd,
            "wall_s": round(end - start), "active_s": round(active), "main_only": True}
