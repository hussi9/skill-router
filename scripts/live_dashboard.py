#!/usr/bin/env python3
"""
live_dashboard.py — a local, live "mission-control" page for skill-router and Jev.

    python3 scripts/cli.py dashboard [--port 8790] [--days 7]

Serves one self-contained HTML page (inline CSS/JS, no CDN, no fonts) and
GET /api/state, a JSON aggregate of the logs the installed skill already writes:

    ~/.claude/skill_router_log.jsonl                     routes, loads (keyword lists ignored)
    ~/.claude/skill_router_cache/jev-decisions.jsonl     Jev confidence, latency, cache hits
    ~/.claude/skill_router_outcomes.jsonl                shown / loaded / tests / held out
    ~/.claude/skill_router_cache/listing/*.json          measured /context listing
    ~/.claude/skill_router_cache/visibility-summary.json visibility plans

Read-only. Binds 127.0.0.1 and rejects requests whose Host header is not local
(DNS rebinding). No prompt text leaves this module: only skill names, counts,
confidences, latencies and timestamps. State is recomputed at most every 3 s;
the skill audit (no Jev) runs in a background thread at most every 10 minutes.
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import listing_probe  # noqa: E402
import outcomes as outcomes_mod  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
GATE = 0.80
EVENTS_MAX = 40
TOP_MAX = 10
STATE_TTL_S = 3.0
AUDIT_EVERY_S = 600.0
LOAD_DEDUPE_S = 10.0
LOCAL_HOSTS = ("127.0.0.1", "localhost")


def _claude() -> Path:
    return Path.home() / ".claude"


@dataclass(frozen=True)
class Paths:
    router_log: Path = field(default_factory=lambda: _claude() / "skill_router_log.jsonl")
    jev_log: Path = field(default_factory=lambda: _claude() / "skill_router_cache" / "jev-decisions.jsonl")
    outcomes: Path = field(default_factory=outcomes_mod.outcomes_path)
    index: Path = field(default_factory=lambda: _claude() / "skill_index.json")
    listing_dir: Path = field(default_factory=lambda: _claude() / "skill_router_cache" / "listing")
    visibility: Path = field(default_factory=lambda: _claude() / "skill_router_cache" / "visibility-summary.json")


# ---- readers -------------------------------------------------------------------

def _jsonl(path: Path) -> list[dict]:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if isinstance(e, dict):
            out.append(e)
    return out


def _json(path: Path) -> Optional[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def _iso_ts(ts: Any) -> Optional[float]:
    try:
        return time.mktime(time.strptime(str(ts).strip(), "%Y-%m-%dT%H:%M:%S"))
    except (ValueError, TypeError, OverflowError):
        return None


def _names(values: Any) -> list[str]:
    return [v for v in (values or []) if isinstance(v, str) and v][:5]


def _version() -> str:
    try:
        return (ROOT / "VERSION").read_text().strip()[:20]
    except OSError:
        return ""


@contextmanager
def _outcomes_file(path: Path) -> Iterator[None]:
    """Point scripts/outcomes.py at `path` for one call (it reads its env override)."""
    with _ENV_LOCK:
        old = os.environ.get("SKILL_ROUTER_OUTCOMES_FILE")
        os.environ["SKILL_ROUTER_OUTCOMES_FILE"] = str(path)
        try:
            yield
        finally:
            if old is None:
                os.environ.pop("SKILL_ROUTER_OUTCOMES_FILE", None)
            else:
                os.environ["SKILL_ROUTER_OUTCOMES_FILE"] = old


_ENV_LOCK = threading.Lock()


# ---- sections ------------------------------------------------------------------

def _installed(index_path: Path) -> int:
    from skill_audit import _routable
    data = _json(index_path)
    return len(_routable((data or {}).get("entries") or []))


def _percentile(sorted_vals: list[float], q: float) -> float:
    k = max(0, min(len(sorted_vals) - 1, int(round(q * len(sorted_vals) + 0.5)) - 1))
    return sorted_vals[k]


def _jev(rows: list[dict]) -> Optional[dict]:
    if not rows:
        return None
    hist = [0] * 10
    confs = []
    lat = []
    for e in rows:
        c = _num(e.get("confidence"))
        if c is not None and 0.0 <= c <= 1.0:
            confs.append(c)
            hist[min(9, int(c * 10))] += 1
        ms = _num(e.get("elapsed_ms"))
        if ms is not None and e.get("provider_attempted") and not e.get("cache_hit"):
            lat.append(ms)
    lat.sort()
    hits = sum(1 for e in rows if e.get("cache_hit") is True)
    tiers: dict[str, int] = {}
    for e in rows:
        t = e.get("work_tier")
        if isinstance(t, str) and t in ("light", "standard", "heavy"):
            tiers[t] = tiers.get(t, 0) + 1
    return {
        "decisions": len(rows),
        "cache_hit_rate": hits / len(rows),
        "median_ms": statistics.median(lat) if lat else None,
        "p90_ms": _percentile(lat, 0.9) if lat else None,
        "histogram": hist,
        "route_rate": (sum(1 for c in confs if c >= GATE) / len(confs)) if confs else None,
        "failures": sum(1 for e in rows if e.get("failure")),
        "tiers": tiers,
        "gate": GATE,
        "last_ts": max((_num(e.get("timestamp")) or 0.0) for e in rows),
    }


def _day(t: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(t))


def _routing(out_rows: list[dict], days: int, now: float) -> Optional[dict]:
    shown = {}
    turns = {}
    for e in out_rows:
        key = (e.get("session_id"), e.get("prompt_id"))
        if e.get("type") == "shown":
            shown[key] = e
        elif e.get("type") == "turn":
            turns[key] = e
    if not shown and not turns:
        return None
    per = {_day(now - i * 86400): {"cards": 0, "shortlists": 0, "silent": 0, "held_out": 0}
           for i in range(days - 1, -1, -1)}
    tot = {"cards": 0, "shortlists": 0, "silent": 0, "held_out": 0}

    def bump(t: float, k: str) -> None:
        tot[k] += 1
        d = per.get(_day(t))
        if d is not None:
            d[k] += 1

    for key, e in shown.items():
        t = _num(e.get("ts")) or now
        if e.get("held_out"):
            bump(t, "held_out")
        elif _names(e.get("card")):
            bump(t, "cards")
        elif _names(e.get("shortlist")):
            bump(t, "shortlists")
        else:
            bump(t, "silent")
    for key, e in turns.items():
        if key not in shown:
            bump(_num(e.get("ts")) or now, "silent")
    return {**tot, "per_day": [{"day": d, **v} for d, v in per.items()]}


def _events(out_rows: list[dict], router_rows: list[dict], jev_conf: dict, since: float) -> list[dict]:
    ev: list[dict] = []
    for e in out_rows:
        t = _num(e.get("ts"))
        if t is None:
            continue
        key = (e.get("session_id"), e.get("prompt_id"))
        if e.get("type") == "shown":
            card, short = _names(e.get("card")), _names(e.get("shortlist"))
            if e.get("held_out"):
                kind, skills = "held_out", []
            elif card:
                kind, skills = "route", card
            elif short:
                kind, skills = "shortlist", short
            else:
                kind, skills = "silent", []
            mode = e.get("source") if e.get("source") in ("jev", "keyless") else None
            ev.append({"ts": t, "kind": kind, "skills": skills, "jev_conf": jev_conf.get(key), "mode": mode})
        elif e.get("type") == "signal" and e.get("kind") in ("loaded", "tests_green", "tests_red"):
            skills = _names([e.get("skill")]) if e.get("kind") == "loaded" else []
            ev.append({"ts": t, "kind": e["kind"], "skills": skills, "jev_conf": None, "mode": None})
    loads = [(x["ts"], x["skills"][0]) for x in ev if x["kind"] == "loaded" and x["skills"]]
    for e in router_rows:
        if e.get("type") != "invoke" or not isinstance(e.get("skill"), str) or not e["skill"]:
            continue
        t = _iso_ts(e.get("ts"))
        if t is None or t < since:
            continue
        if any(s == e["skill"] and abs(t - lt) <= LOAD_DEDUPE_S for lt, s in loads):
            continue
        ev.append({"ts": t, "kind": "loaded", "skills": [e["skill"]], "jev_conf": None, "mode": None})
    ev.sort(key=lambda x: x["ts"], reverse=True)
    return ev[:EVENTS_MAX]


def _top_skills(router_rows: list[dict], out_rows: list[dict], since: float) -> list[dict]:
    counts: dict[str, dict[str, int]] = {}

    def add(name: Any, k: str) -> None:
        if isinstance(name, str) and name:
            counts.setdefault(name, {"routed": 0, "loaded": 0})[k] += 1

    for e in router_rows:
        t = _iso_ts(e.get("ts"))
        if t is None or t < since:
            continue
        if e.get("type") == "prompt" and isinstance(e.get("route"), str):
            add(e["route"], "routed")
        elif e.get("type") == "invoke":
            add(e.get("skill"), "loaded")
    if not counts:   # no router log: fall back to the outcomes log
        for e in out_rows:
            if e.get("type") == "shown" and not e.get("held_out"):
                for n in _names(e.get("card")):
                    add(n, "routed")
            elif e.get("type") == "signal" and e.get("kind") == "loaded":
                add(e.get("skill"), "loaded")
    ranked = sorted(counts.items(), key=lambda kv: (-(kv[1]["routed"] + kv[1]["loaded"]), kv[0]))
    return [{"name": n[:80], **c} for n, c in ranked[:TOP_MAX]]


def _listing(listing_dir: Path, now: float) -> Optional[dict]:
    try:
        files = sorted(listing_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return None
    for f in files:
        m = _json(f)
        if not m or not isinstance(m.get("entries"), list):
            continue
        described, total = listing_probe.counts(m)
        return {"entries": total, "described": described, "name_only": total - described,
                "skills_tok": int(_num(m.get("skills_tokens")) or 0),
                "context_window": int(_num(m.get("context_window")) or 0) or None,
                "age_s": max(0.0, now - (_num(m.get("measured_at")) or now)),
                "fresh": listing_probe.is_fresh(m), "line": listing_probe.line(m)}
    return None


def _visibility(path: Path, now: float) -> Optional[dict]:
    v = _json(path)
    if not v or not isinstance(v.get("projects"), dict):
        return None
    projects = [p for p in v["projects"].values() if isinstance(p, dict)]
    worth = sum(1 for p in projects if any(isinstance(m, dict) and m.get("worthwhile") for m in p.values()))
    upd = _num(v.get("updated"))
    return {"projects": len(projects), "worthwhile": worth,
            "unused_skills": int(_num(v.get("unused_skills")) or 0),
            "age_s": max(0.0, now - upd) if upd else None}


def _outcomes(path: Path, days: int) -> Optional[dict]:
    with _outcomes_file(path):
        s = outcomes_mod.summarize(days)
        if not s["shown"] and not s["held_out"]:
            return None
        return {"summary": s, "lift": outcomes_mod.lift(days), "median_chars": outcomes_mod.median_chars(days)}


# ---- aggregate -----------------------------------------------------------------

def aggregate(paths: Paths, days: int, now: float, audit_state: Optional[dict] = None) -> dict:
    """Everything the page shows, from local logs. Pure apart from reading files."""
    since = now - days * 86400
    router_rows = _jsonl(paths.router_log)
    jev_rows = [e for e in _jsonl(paths.jev_log) if (_num(e.get("timestamp")) or 0) >= since]
    out_rows = [e for e in _jsonl(paths.outcomes) if (_num(e.get("ts")) or 0) >= since]
    jev_conf = {}
    for e in jev_rows:
        c = _num(e.get("confidence"))
        if c is not None and e.get("prompt_id"):
            jev_conf[(e.get("session_id"), e.get("prompt_id"))] = round(c, 3)
    events = _events(out_rows, router_rows, jev_conf, since)
    recent: dict[str, str] = {}
    for e in events:
        for n in e["skills"]:
            if n not in recent:
                recent[n] = "loaded" if e["kind"] == "loaded" else "routed"
    last_router = max((_iso_ts(e.get("ts")) or 0.0 for e in router_rows), default=0.0)
    jev = _jev(jev_rows)
    audit = None
    if audit_state:
        audit = {**audit_state, "age_s": max(0.0, now - audit_state.get("at", now))}
        audit.pop("at", None)
    return {
        "header": {"version": _version(), "installed": _installed(paths.index), "days": days,
                   "now": now, "gate": GATE,
                   "last_activity": max(last_router, (events[0]["ts"] if events else 0.0),
                                        (jev or {}).get("last_ts") or 0.0) or None},
        "events": events,
        "recent_skills": recent,
        "jev": jev,
        "routing": _routing(out_rows, days, now),
        "outcomes": _outcomes(paths.outcomes, days),
        "listing": _listing(paths.listing_dir, now),
        "visibility": _visibility(paths.visibility, now),
        "audit": audit,
        "top_skills": _top_skills(router_rows, out_rows, since),
    }


def run_audit() -> dict:
    """Skill doctor without Jev (no network); summary counts only."""
    import skill_audit
    catalog, usage, history_days, outs, skills_sh = skill_audit.load_inputs()
    r = skill_audit.audit(catalog, usage, history_days, outs, skills_sh, jev=None)
    return {"score": r.score, "skills": r.skills, "unused": len(r.unused_90d),
            "overlaps": len(r.overlaps), "thin": r.thin_descriptions,
            "flagged": len(r.flagged), "at": time.time()}


# ---- page ----------------------------------------------------------------------

def _safe_json(obj: Any) -> str:
    """JSON safe to embed in a <script> element."""
    return (json.dumps(obj).replace("&", "\\u0026").replace("<", "\\u003c")
            .replace(">", "\\u003e").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def page(state: dict) -> str:
    return HTML.replace("/*__STATE__*/null", _safe_json(state))


# ---- server --------------------------------------------------------------------

class _State:
    def __init__(self, paths: Paths, days: int, audit: bool) -> None:
        self.paths, self.days = paths, days
        self.lock = threading.Lock()
        self.cached: Optional[dict] = None
        self.cached_at = 0.0
        self.audit_state: Optional[dict] = None
        self.stop = threading.Event()
        if audit:
            threading.Thread(target=self._audit_loop, daemon=True).start()

    def _audit_loop(self) -> None:
        while not self.stop.is_set():
            try:
                self.audit_state = run_audit()
            except Exception as e:  # noqa: BLE001 — a broken input means no audit panel
                print(f"dashboard: audit skipped ({type(e).__name__})", file=sys.stderr)
            self.stop.wait(AUDIT_EVERY_S)

    def get(self) -> dict:
        with self.lock:
            now = time.time()
            if self.cached is None or now - self.cached_at >= STATE_TTL_S:
                self.cached = aggregate(self.paths, self.days, now, self.audit_state)
                self.cached_at = now
            return self.cached


class _Handler(BaseHTTPRequestHandler):
    server_version = "skill-router-dashboard"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 — quiet
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy",
                         "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                         "connect-src 'self'; img-src data:")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
        if host not in LOCAL_HOSTS:
            self._send(403, b"forbidden", "text/plain")
            return
        st: _State = self.server.state  # type: ignore[attr-defined]
        path = self.path.split("?", 1)[0]
        if path == "/api/state":
            self._send(200, json.dumps(st.get()).encode(), "application/json")
        elif path in ("/", "/index.html"):
            self._send(200, page(st.get()).encode(), "text/html; charset=utf-8")
        else:
            self._send(404, b"not found", "text/plain")


def make_server(port: int, days: int, host: str = "127.0.0.1",
                paths: Optional[Paths] = None, audit: bool = True) -> ThreadingHTTPServer:
    if host != "127.0.0.1":
        raise ValueError("the dashboard binds 127.0.0.1 only")
    srv = ThreadingHTTPServer((host, port), _Handler)
    srv.daemon_threads = True
    srv.state = _State(paths or Paths(), max(1, days), audit)  # type: ignore[attr-defined]
    return srv


def serve(port: int = 8790, days: int = 7) -> int:
    try:
        srv = make_server(port, days)
    except OSError as e:
        print(f"error: cannot listen on 127.0.0.1:{port} ({e.strerror or e})")
        return 1
    print(f"skill-router dashboard: http://127.0.0.1:{srv.server_address[1]}  (Ctrl-C stops it)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.state.stop.set()  # type: ignore[attr-defined]
        srv.server_close()
    return 0


HTML = (Path(__file__).resolve().parent / "live_dashboard.html").read_text(encoding="utf-8") \
    if (Path(__file__).resolve().parent / "live_dashboard.html").is_file() else ""


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--days", type=int, default=7)
    a = ap.parse_args()
    sys.exit(serve(a.port, a.days))
