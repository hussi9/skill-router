#!/usr/bin/env python3
"""
skills_sh_catalog.py — turn the skills.sh leaderboards into a local suggestion cache.

skills.sh is the public registry behind `npx skills`. The router uses it for ONE job:
when nothing installed fits a prompt, point at a popular, audited skill that does.
It never installs, never invokes, never adds anything to the routing index.

What it reads (all public HTML pages; robots.txt allows them. /api/ is disallowed
for crawlers, so this module never touches it — the manual `find_skills.py` search
is the one explicit-user-action exception and stays separate):

    /            all-time leaderboard   (installs, weekly series, official flag)
    /trending    24h-trending leaderboard
    /hot         hot leaderboard
    /audits      security audits: Agent Trust Hub, Socket, Snyk per skill
    /<owner>/<repo>/<skill>   one page per candidate, for its description only

Output (atomic write, mode 600):

    ~/.claude/skill_router_skills_sh.json   catalog in the online-catalog entry schema,
                                            consumed by router._online_skill_index()
    docs/skills-sh-top.md                   (with --report) top skills per category

Politeness and bounds: one request at a time, 0.4 s apart, 1 MB per response, a hard
cap on skill pages per run (default 220), descriptions cached for 7 days. Re-running
inside the refresh window costs zero requests.

    python3 scripts/skills_sh_catalog.py                 # refresh if stale
    python3 scripts/skills_sh_catalog.py --force --report
    python3 scripts/skills_sh_catalog.py --offline       # rebuild from cached pages only
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Callable, Iterable, Optional

HOME = Path.home()
HOST = "skills.sh"
OUTPUT = Path(os.environ.get("SKILL_ROUTER_SKILLS_SH_FILE") or HOME / ".claude" / "skill_router_skills_sh.json")
CACHE_DIR = Path(os.environ.get("SKILL_ROUTER_CACHE_DIR") or HOME / ".claude" / "skill_router_cache") / "skills_sh"
UA = "skill-router/2 (+https://github.com/hussi9/skill-router; leaderboard cache, 1 req/0.4s)"
MAX_BYTES = 1_000_000
DELAY_S = 0.4
REFRESH_S = 24 * 3600            # rebuild the catalog at most daily
DESC_TTL_S = 7 * 24 * 3600       # a skill's description is re-read weekly
MAX_SKILL_PAGES = 400
TOP_PER_BOARD = 150              # candidates taken from each leaderboard

BOARDS = ("/", "/trending", "/hot")
AUDITS = "/audits"

# ---------------------------------------------------------------- fetching

class FetchError(Exception):
    pass


def _allowed(url: str) -> bool:
    u = urllib.parse.urlparse(url)
    return u.scheme == "https" and u.hostname in (HOST, "www." + HOST) and not u.path.startswith(("/api", "/internal", "/search"))


def fetch(url: str, timeout: float = 20.0) -> str:
    """HTTPS GET of an allowed skills.sh page. Redirects are followed only within the allowlist."""
    if not _allowed(url):
        raise FetchError(f"refused: {url}")

    class _SameHostRedirect(urllib.request.HTTPRedirectHandler):
        """skills.sh 308s to www.skills.sh. Follow only redirects that stay on the allowlist."""
        max_redirections = 2

        def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
            if not _allowed(newurl):
                return None
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    opener = urllib.request.build_opener(_SameHostRedirect)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html"})
    try:
        with opener.open(req, timeout=timeout) as r:
            body = r.read(MAX_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise FetchError(f"{url}: {exc}") from exc
    if len(body) > MAX_BYTES:
        # Leaderboards embed a few hundred rows; larger than this is not what we parse.
        body = body[:MAX_BYTES]
    return body.decode("utf-8", "replace")


# ---------------------------------------------------------------- parsing

_PUSH = re.compile(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)')


def rsc_text(html: str) -> str:
    """Concatenate the Next.js flight payload strings of a page into plain text."""
    out = []
    for m in _PUSH.finditer(html):
        try:
            out.append(json.loads('"' + m.group(1) + '"'))
        except ValueError:
            continue
    return "".join(out)


def arrays_after(text: str, key: str) -> Iterable[list]:
    """Every JSON array that follows `"key":` in the flight text."""
    dec = json.JSONDecoder()
    needle = f'"{key}":['
    pos = 0
    while True:
        i = text.find(needle, pos)
        if i < 0:
            return
        start = i + len(needle) - 1
        try:
            arr, end = dec.raw_decode(text, start)
        except ValueError:
            pos = i + len(needle)
            continue
        if isinstance(arr, list):
            yield arr
        pos = end


def board_rows(html: str) -> list[dict]:
    """Rows with source + skillId from a leaderboard page, de-duplicated, in page order."""
    text = rsc_text(html)
    seen: dict[str, dict] = {}
    for key in ("initialSkills", "initialRows"):
        for arr in arrays_after(text, key):
            for row in arr:
                if isinstance(row, dict) and row.get("source") and row.get("skillId"):
                    seen.setdefault(f"{row['source']}/{row['skillId']}", row)
    return list(seen.values())


_BAD = {"malicious", "high", "critical", "unsafe", "fail", "failed", "dangerous", "severe"}
_GOOD = {"safe", "clean", "low", "pass", "passed", "benign", "ok", "none", "minimal"}


def audit_verdict(row: dict) -> str:
    """'flagged' | 'clean' | 'unaudited' from the partner results on an /audits row.

    One partner calling a skill dangerous is enough to flag it: a suggestion should
    never be the thing that introduces a risky skill. 'clean' needs at least one
    partner to have said so and none to have objected.
    """
    seen_good = False
    stack = [row]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            for k, v in cur.items():
                if k in ("verdict", "overall_risk_level", "status", "risk") and isinstance(v, str):
                    t = v.strip().lower()
                    if t in _BAD:
                        return "flagged"
                    if t in _GOOD:
                        seen_good = True
                elif k == "malicious" and isinstance(v, (int, float)) and v > 0:
                    return "flagged"
                elif isinstance(v, (dict, list)):
                    stack.append(v)
        elif isinstance(cur, list):
            stack.extend(cur)
    return "clean" if seen_good else "unaudited"


def trend(weekly: object) -> float:
    """Growth of the last 4 weeks over the 4 before, clamped; 1.0 when unknown."""
    if not isinstance(weekly, list) or len(weekly) < 8 or not all(isinstance(x, (int, float)) for x in weekly):
        return 1.0
    prev, last = sum(weekly[-8:-4]), sum(weekly[-4:])
    if prev <= 0:
        return 1.0
    return round(max(0.1, min(10.0, last / prev)), 2)


# ---------------------------------------------------------------- categories

# Explicit and inspectable on purpose. Order matters: first category with a hit wins,
# so narrow ones come before broad ones. The word list is matched on name + description.
CATEGORIES: list[tuple[str, tuple[str, ...]]] = [
    ("security", ("security", "vulnerab", "owasp", "pentest", "threat", "secret", "auth audit", "malware")),
    ("testing", ("test", "tdd", "playwright", "cypress", "vitest", "jest", "qa", "e2e", "coverage")),
    ("debugging", ("debug", "bug", "root cause", "diagnos", "troubleshoot", "incident", "postmortem")),
    ("database", ("database", "postgres", "sql", "supabase", "prisma", "migration", "schema", "mongodb", "redis")),
    ("devops-cloud", ("deploy", "docker", "kubernetes", "terraform", "aws", "azure", "gcp", "cloudflare", "ci/cd", "vercel", "infrastructure")),
    ("mobile", ("react native", "expo", "swiftui", "swift", "kotlin", "ios", "android", "flutter", "mobile")),
    ("design-ui", ("design", "ui ", "ux", "figma", "tailwind", "css", "accessibility", "typography", "animation", "shadcn")),
    ("frontend", ("react", "next.js", "nextjs", "vue", "svelte", "frontend", "component", "typescript", "javascript", "web app")),
    ("backend-api", ("api", "backend", "server", "fastapi", "django", "express", "graphql", "microservice", "node", "python", "rust", "golang")),
    ("ai-agents", ("agent", "llm", "prompt", "mcp", "rag", "claude", "openai", "gemini", "embedding", "skill", "subagent")),
    ("data-analytics", ("analytics", "pandas", "dataset", "data analysis", "data science", "dashboard", "visualiz", "chart", "notebook", "statistic")),
    ("marketing-seo", ("seo", "marketing", "copywriting", "ads", "growth", "conversion", "landing page", "social", "email", "brand")),
    ("docs-writing", ("document", "docs", "readme", "writing", "pdf", "docx", "pptx", "xlsx", "slides", "blog", "prose")),
    ("media-creative", ("video", "image", "audio", "remotion", "animation", "thumbnail", "music", "photo", "3d")),
    ("planning-workflow", ("plan", "brainstorm", "spec", "review", "refactor", "architecture", "workflow", "git", "commit", "pull request", "interview")),
    ("business-ops", ("finance", "sales", "legal", "support", "crm", "invoice", "contract", "pricing", "operations")),
]
OTHER = "other"


def categorize(name: str, description: str = "") -> str:
    """Keyword category. Whole-word starts only ("test" matches "testing", not "latest"),
    the skill's own name counts triple and the first sentence of its description double,
    so "data-race safety" in Swift prose cannot make a Swift skill an analytics one."""
    nm = name.replace("-", " ").replace("_", " ").lower()
    first = re.split(r"(?<=[.!?])\s", description.strip(), maxsplit=1)[0].lower()
    rest = description.lower()
    best, best_score = OTHER, 0
    for cat, words in CATEGORIES:
        score = 0
        for w in words:
            pat = re.compile(rf"(?<![a-z0-9]){re.escape(w.strip())}")
            if pat.search(nm):
                score += 3
            if pat.search(first):
                score += 2
            if pat.search(rest):
                score += 1
        if score > best_score:
            best, best_score = cat, score
    return best


# ---------------------------------------------------------------- descriptions

def _cache_path(sid: str) -> Path:
    return CACHE_DIR / (re.sub(r"[^A-Za-z0-9._-]", "_", sid) + ".json")


def description_for(sid: str, fetcher: Callable[[str], str], offline: bool, now: float) -> str:
    path = _cache_path(sid)
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
        if offline or now - cached.get("at", 0) < DESC_TTL_S:
            return str(cached.get("description") or "")
    except (OSError, ValueError):
        cached = None
    if offline:
        return ""
    try:
        html = fetcher(f"https://www.{HOST}/{sid}")
    except FetchError:
        return str(cached.get("description") or "") if cached else ""
    m = re.search(r'<meta name="description" content="([^"]*)"', html)
    desc = _unescape(m.group(1)) if m else ""
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"at": now, "description": desc}), encoding="utf-8")
    except OSError:
        pass
    time.sleep(DELAY_S)
    return desc


def _unescape(s: str) -> str:
    import html as _html
    return _html.unescape(s).strip()


# ---------------------------------------------------------------- Jev refinement

CATEGORY_CRITERIA = {
    "security": "Security review, vulnerabilities, secrets, threat modelling, hardening",
    "testing": "Writing or running tests: unit, integration, end-to-end, TDD, coverage",
    "debugging": "Finding and fixing bugs, root-cause analysis, incident response",
    "database": "Databases, SQL, schemas, migrations, ORMs",
    "devops-cloud": "Deployment, CI/CD, containers, cloud infrastructure",
    "mobile": "iOS, Android, React Native, Swift, Kotlin, Flutter apps",
    "design-ui": "Visual design, UI/UX, design systems, CSS, accessibility, animation",
    "frontend": "Web frontend frameworks and components",
    "backend-api": "Servers, APIs, backend languages and frameworks",
    "ai-agents": "LLMs, agents, prompts, MCP, skills, agent tooling",
    "data-analytics": "Data analysis, statistics, dashboards, notebooks",
    "marketing-seo": "Marketing, SEO, copywriting, growth, ads, email",
    "docs-writing": "Documents, writing, PDFs, slides, spreadsheets, technical docs",
    "media-creative": "Video, images, audio, music, 3D and creative generation",
    "planning-workflow": "Planning, specs, code review, refactoring, git and engineering workflow",
    "business-ops": "Finance, sales, legal, HR, support and other business operations",
    OTHER: "None of the above",
}
REFINE_AT = 0.8      # same gate as routing: below it the keyword category stands


def jev_category(name: str, description: str) -> Optional[str]:
    """One Jev Choice call: which category fits this skill? None on any failure.

    Sends only the skill's public name and description (never prompts or local files).
    Needs TYPESAFE_API_KEY; without it this returns None and keywords stand.
    """
    try:
        import jev_choose  # type: ignore[import-not-found]
    except ImportError:
        return None
    key = jev_choose._key()
    if not key or not jev_choose.enabled():
        return None
    body = {"model": jev_choose.MODEL,
            "state": {"request": f"{name}: {description}"[:1200]},
            "questions": {"category": {
                "type": "choice",
                "instructions": "Which category best describes this agent skill, going by what it is for.",
                "criteria": CATEGORY_CRITERIA}}}
    data = jev_choose._deadline(lambda: jev_choose._post(body, key, 4.0), 4.0)
    ans = (data or {}).get("answers", {}).get("category") if isinstance(data, dict) else None
    if not isinstance(ans, dict) or ans.get("choice") not in CATEGORY_CRITERIA:
        return None
    return ans["choice"] if jev_choose._conf(ans) >= REFINE_AT else None


def refine_with_jev(entries: list[dict], *, call: Callable[[str, str], Optional[str]] = jev_category,
                    limit: int = 150) -> int:
    """Re-file entries the keywords left in 'other'. Results cached with the description."""
    changed = 0
    done = 0
    for e in entries:
        if e["category"] != OTHER or not e["description"]:
            continue
        sid = e["source_url"].split(f"{HOST}/", 1)[-1]
        path = _cache_path(sid)
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cached = {"description": e["description"]}
        cat = cached.get("jev_category")
        if cat is None and done < limit:
            done += 1
            cat = call(e["name"], e["description"]) or ""
            cached["jev_category"] = cat
            try:
                path.write_text(json.dumps(cached), encoding="utf-8")
            except OSError:
                pass
        if cat and cat != OTHER:
            e["category"] = cat
            e["tags"][0] = cat
            changed += 1
    return changed


# ---------------------------------------------------------------- build

def build(fetcher: Callable[[str], str] = fetch, *, offline: bool = False, max_pages: int = MAX_SKILL_PAGES,
          now: Optional[float] = None) -> dict:
    now = time.time() if now is None else now
    boards: dict[str, list[dict]] = {}
    errors: list[str] = []
    for path in BOARDS + (AUDITS,):
        page_cache = CACHE_DIR / ("board_" + (path.strip("/") or "home") + ".html")
        try:
            if offline:
                html = page_cache.read_text(encoding="utf-8")
            else:
                html = fetcher(f"https://www.{HOST}{path}")
                CACHE_DIR.mkdir(parents=True, exist_ok=True)
                page_cache.write_text(html, encoding="utf-8")
                time.sleep(DELAY_S)
        except (FetchError, OSError) as exc:
            errors.append(f"{path}: {exc}")
            continue
        boards[path] = board_rows(html)

    audits = {f"{r['source']}/{r['skillId']}": audit_verdict(r) for r in boards.get(AUDITS, [])}

    cand: dict[str, dict] = {}
    for path in BOARDS:
        for rank, row in enumerate(boards.get(path, [])[:TOP_PER_BOARD], 1):
            sid = f"{row['source']}/{row['skillId']}"
            c = cand.setdefault(sid, {"source": row["source"], "skill": row["skillId"], "installs": 0,
                                      "trend": 1.0, "official": False, "ranks": {}})
            c["installs"] = max(c["installs"], int(row.get("installs") or 0))
            c["official"] = c["official"] or bool(row.get("isOfficial"))
            if row.get("weeklyInstalls"):
                c["trend"] = trend(row["weeklyInstalls"])
            c["ranks"][path.strip("/") or "all-time"] = rank

    # Spend the page budget on the most valuable candidates first.
    ordered = sorted(cand.items(), key=lambda kv: (-kv[1]["installs"], kv[0]))
    entries: list[dict] = []
    fetched = 0
    for sid, c in ordered:
        verdict = audits.get(sid, "unaudited")
        if verdict == "flagged":
            continue
        cached = _cache_path(sid).exists()
        if not cached and not offline and fetched >= max_pages:
            desc = ""
        else:
            if not cached and not offline:
                fetched += 1
            desc = description_for(sid, fetcher, offline, now)
        cat = categorize(c["skill"], desc)
        entries.append({
            "name": c["skill"],
            "description": desc,
            "category": cat,
            "tags": [cat] + sorted(c["ranks"]),
            "source": "online:skills.sh",
            "source_url": f"https://www.{HOST}/{sid}",
            "installed": False,
            "install_command": f"npx skills add {c['source']} --skill {c['skill']}",
            "author": c["source"],
            "installs": c["installs"],
            "trend": c["trend"],
            "official": c["official"],
            "audit": verdict,
            "ranks": c["ranks"],
        })
    return {"catalogs": {"skills.sh": entries},
            "metadata": {"generated_at": now, "source": f"https://{HOST}", "boards": sorted(boards),
                         "audited": sum(1 for e in entries if e["audit"] == "clean"),
                         "total": len(entries), "errors": errors,
                         "note": "suggestion cache only; nothing here is installed or invoked"}}


def write_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def fresh(path: Path = OUTPUT, now: Optional[float] = None) -> bool:
    try:
        at = json.loads(path.read_text(encoding="utf-8"))["metadata"]["generated_at"]
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return (time.time() if now is None else now) - at < REFRESH_S


# ---------------------------------------------------------------- analysis

def popularity_score(e: dict) -> float:
    """installs, nudged by momentum and trust. Order only — never shown as a number."""
    s = float(e.get("installs") or 0) * (0.75 + 0.25 * min(float(e.get("trend") or 1.0), 2.0))
    if e.get("official"):
        s *= 1.2
    if e.get("audit") == "clean":
        s *= 1.1
    return s


def top_by_category(entries: list[dict], n: int = 5) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for e in entries:
        groups[e["category"]].append(e)
    return {c: sorted(v, key=popularity_score, reverse=True)[:n] for c, v in sorted(groups.items())}


def render_report(catalog: dict, n: int = 5) -> str:
    entries = catalog["catalogs"]["skills.sh"]
    meta = catalog["metadata"]
    when = time.strftime("%Y-%m-%d", time.gmtime(meta["generated_at"]))
    out = [f"# Top skills.sh skills by category ({when})", "",
           f"{meta['total']} skills from the all-time, trending and hot boards; {meta['audited']} audited clean, "
           f"flagged skills removed. Categories are keyword-assigned (see `CATEGORIES` in "
           f"`scripts/skills_sh_catalog.py`), so treat a borderline skill's category as a hint.", "",
           "Install counts measure adoption, not quality or safety. Read the source before installing.", ""]
    for cat, items in top_by_category(entries, n).items():
        out += [f"## {cat}", "", "| Skill | Installs | Trend | Audit | What it does |", "|---|---:|---:|---|---|"]
        for e in items:
            desc = (e["description"] or "—").replace("|", "/")[:110]
            star = " ★" if e["official"] else ""
            out.append(f"| [{e['name']}]({e['source_url']}){star} | {e['installs']:,} | {e['trend']}× | {e['audit']} | {desc} |")
        out.append("")
    return "\n".join(out)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--force", action="store_true", help="refresh even if the cache is under a day old")
    ap.add_argument("--offline", action="store_true", help="rebuild from cached pages, no network")
    ap.add_argument("--report", metavar="PATH", nargs="?", const="docs/skills-sh-top.md",
                    help="also write the per-category top list (default docs/skills-sh-top.md)")
    ap.add_argument("--refine", action="store_true",
                    help="file skills the keywords left in 'other' with one Jev call each (needs TYPESAFE_API_KEY)")
    ap.add_argument("--max-pages", type=int, default=MAX_SKILL_PAGES)
    a = ap.parse_args(argv)
    if not a.force and not a.offline and fresh():
        print(f"skills.sh cache is fresh ({OUTPUT}); --force to refresh")
        return 0
    cat = build(offline=a.offline, max_pages=a.max_pages)
    if not cat["catalogs"]["skills.sh"]:
        print("no skills parsed; keeping the existing cache", file=sys.stderr)
        for e in cat["metadata"]["errors"]:
            print("  " + e, file=sys.stderr)
        return 1
    if a.refine:
        n = refine_with_jev(cat["catalogs"]["skills.sh"])
        cat["metadata"]["jev_refined"] = n
        print(f"jev refined {n} skills out of 'other'")
    write_atomic(OUTPUT, cat)
    m = cat["metadata"]
    print(f"skills.sh: {m['total']} skills ({m['audited']} audited clean) → {OUTPUT}")
    for e in m["errors"]:
        print("  partial: " + e)
    if a.report:
        Path(a.report).write_text(render_report(cat), encoding="utf-8")
        print(f"report → {a.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
