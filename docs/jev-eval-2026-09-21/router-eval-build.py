#!/usr/bin/env python3
"""Mine (user prompt -> first Skill invoked in that turn) pairs from local Claude Code transcripts."""
import json, os, re, sys, time
from pathlib import Path

ROOT = Path.home() / ".claude" / "projects"
OUT = Path(__file__).with_name("router-eval-set.json")
CUTOFF = time.time() - 75 * 86400
NOISE = ("[skill-router]", "Stop hook", "PreToolUse", "IRON RULE", "hookSpecificOutput", "<system-reminder>",
         "<command-name>", "<local-command", "Caveat:", "<task-notification", "[Request interrupted", "This session is being continued")
idx = json.load(open(Path.home() / ".claude" / "skill_index.json"))
known = {e["name"] for e in idx["entries"]}


def user_text(msg):
    c = msg.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        if any(isinstance(p, dict) and p.get("type") == "tool_result" for p in c):
            return ""
        return "\n".join(p.get("text", "") for p in c if isinstance(p, dict) and p.get("type") == "text")
    return ""


pairs, seen = [], set()
files = [p for p in ROOT.glob("*/*.jsonl") if p.stat().st_mtime > CUTOFF]
files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
for f in files:
    if "skill-router" in f.parent.name:      # the router's own test sessions
        continue
    pending = None
    try:
        with open(f, encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                if '"Skill"' not in line and '"type":"user"' not in line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("isSidechain"):
                    continue
                msg = ev.get("message") or {}
                if ev.get("type") == "user":
                    t = user_text(msg).strip()
                    if t:
                        pending = None if (any(n in t for n in NOISE) or t.startswith("/") or not (20 <= len(t) <= 700)) else t
                elif ev.get("type") == "assistant" and pending:
                    for part in msg.get("content") or []:
                        if isinstance(part, dict) and part.get("type") == "tool_use" and part.get("name") == "Skill":
                            s = str((part.get("input") or {}).get("skill", "")).strip().lstrip("/")
                            key = pending[:120].lower()
                            if s and key not in seen:
                                seen.add(key)
                                pairs.append({"prompt": pending, "skill": s, "in_index": s in known or s.split(":")[-1] in known,
                                              "project": f.parent.name[-40:]})
                            pending = None
                            break
    except OSError:
        continue

json.dump(pairs, open(OUT, "w"), indent=1)
from collections import Counter
print("files scanned", len(files), "| pairs", len(pairs), "| skill in index", sum(p["in_index"] for p in pairs))
print(Counter(p["skill"] for p in pairs).most_common(25))
