#!/usr/bin/env python3
"""
quota.py — what the session is running on and how much subscription is left.

Hooks are not told the session model or the rate-limit windows. The status
line is: Claude Code feeds it `model.id` and, on Pro/Max, `rate_limits`
(`five_hour` / `seven_day`, each `used_percentage` + `resets_at`) on every
redraw. statusline.sh writes those to

    ~/.claude/skill_router_cache/quota.json

and this module reads them back for the router (card hints) and the Task
hook. Everything is best-effort: a missing, stale or malformed file reads as
"unknown", and unknown never triggers anything.

    session_model()   -> "haiku" | "sonnet" | "opus" | "fable" | ""  (class, not id)
    band()            -> "" | "high" | "critical"
    offload_wanted()  -> bool   whether the card should point at Kimi

Thresholds (percent used, either window):
    SKILL_ROUTER_QUOTA_HIGH      default 80  → "high"
    SKILL_ROUTER_QUOTA_CRITICAL  default 95  → "critical"
Mode:
    SKILL_ROUTER_KIMI = quota (default) | always | off
      quota   point at Kimi when band() is high/critical and the work is not heavy
      always  point at Kimi for every light/standard task
      off     never mention Kimi
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

HOME = Path.home()
FILE = HOME / ".claude" / "skill_router_cache" / "quota.json"
STALE_S = 15 * 60          # the status line redraws far more often than this
HEAVY_CLASSES = ("opus", "fable")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def read(path: Optional[Path] = None) -> dict:
    """The raw record, or {} when missing/stale/malformed."""
    p = Path(os.environ.get("SKILL_ROUTER_QUOTA_FILE") or path or FILE)
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(d, dict):
        return {}
    try:
        if time.time() - float(d.get("ts") or 0) > STALE_S:
            return {}
    except (TypeError, ValueError):
        return {}
    return d


def model_class(model_id: str) -> str:
    m = (model_id or "").lower()
    for cls in ("haiku", "sonnet", "opus", "fable", "mythos"):
        if cls in m:
            return "fable" if cls == "mythos" else cls
    return ""


def session_model(rec: Optional[dict] = None) -> str:
    rec = read() if rec is None else rec
    return model_class(str(rec.get("model_id") or rec.get("model") or ""))


def used(rec: Optional[dict] = None) -> tuple[float, float]:
    """(five_hour %, seven_day %); -1 for a window that is absent."""
    rec = read() if rec is None else rec
    out = []
    for k in ("five_hour", "seven_day"):
        try:
            v = rec.get(k)
            out.append(float(v) if v is not None else -1.0)
        except (TypeError, ValueError):
            out.append(-1.0)
    return out[0], out[1]


def band(rec: Optional[dict] = None) -> str:
    rec = read() if rec is None else rec
    high = _env_int("SKILL_ROUTER_QUOTA_HIGH", 80)
    crit = _env_int("SKILL_ROUTER_QUOTA_CRITICAL", 95)
    worst = max(used(rec))
    if worst < 0:
        return ""
    if worst >= crit:
        return "critical"
    if worst >= high:
        return "high"
    return ""


def kimi_mode() -> str:
    m = os.environ.get("SKILL_ROUTER_KIMI", "quota").strip().lower()
    return m if m in ("quota", "always", "off") else "quota"


def offload_wanted(work_tier: str, rec: Optional[dict] = None) -> bool:
    """Should the card point light/standard work at Kimi?"""
    if kimi_mode() == "off" or work_tier not in ("light", "standard"):
        return False
    if kimi_mode() == "always":
        return True
    return band(rec) != ""


def summary(rec: Optional[dict] = None) -> str:
    """One short phrase for a card line: '5h 87% · 7d 62%'. Empty when unknown."""
    rec = read() if rec is None else rec
    fh, sd = used(rec)
    parts = []
    if fh >= 0:
        parts.append(f"5h {fh:.0f}%")
    if sd >= 0:
        parts.append(f"7d {sd:.0f}%")
    return " · ".join(parts)


if __name__ == "__main__":
    rec = read()
    print(json.dumps({"record": rec, "session_model": session_model(rec), "band": band(rec),
                      "kimi_mode": kimi_mode(), "summary": summary(rec)}, indent=1))
