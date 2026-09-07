#!/usr/bin/env python3
"""
build_index.py — the router's private, enriched view of every invokable skill.

The catalog (build_catalog.py) answers "what exists". This answers "when does
each one apply", which is the question a ranker actually needs. One entry per
invokable skill:

    name        superpowers:systematic-debugging
    kind        process | domain | project | design | meta
    owner       user | project | plugin | builtin | command
    use_when    ["tests fail", "crash on launch", "unexpected output", ...]
    keywords    ["debug", "root", "cause", "reproduce", ...]
    projects    ["deenunlock", "scrollbook"]      # from SKILL.personal.md projects:
    pairs_with  ["superpowers:systematic-debugging"]  # process leg a domain skill wants
    description the original description, untouched
    source_hash sha1 of the SKILL.md, so enrichment is cached per version

Two derivation layers:

  lexical   always. Pulls `use_when` from "Use when…" sentences and any
            "## When to use" section, keywords from name + TF-IDF over the
            description and first 3 KB of body. Zero network, ~100 ms.
  enriched  optional (`--enrich`). Asks a small model to write 3-6 "use when"
            triggers and 8-12 keywords per skill. Cached under
            ~/.claude/skill_router_cache/enrich/<name>.json keyed by
            source_hash, so the bill grows only when a skill changes.
            Provider order: Anthropic Haiku → Gemini Flash-Lite → skip.

Third-party SKILL.md files are never modified. This index is where the
router's understanding lives, not the skill files.

Usage:
    python3 scripts/build_index.py                 # write ~/.claude/skill_index.json
    python3 scripts/build_index.py --enrich        # also run/refresh enrichment
    python3 scripts/build_index.py --report        # weak-description report (user skills)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Iterable, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

HOME = Path.home()
CATALOG = HOME / ".claude" / "skill_router_catalog.json"
OUTPUT = HOME / ".claude" / "skill_index.json"
CACHE_DIR = HOME / ".claude" / "skill_router_cache" / "enrich"
PERSONAL = HOME / ".claude" / "skills" / "skill-router" / "SKILL.personal.md"
MEMORY_INDEX = HOME / ".claude" / "projects" / "-Users-airbook" / "memory" / "MEMORY.md"

VERSION = 1

PROCESS_PREFIXES = ("superpowers:",)
PROCESS_NAMES = frozenset({
    "refactor", "code-review", "security-review", "review", "test", "debug",
    "docs", "perf", "security", "qa", "artifact-design", "loop", "dataviz",
    "feature-dev:feature-dev", "code-simplifier:code-simplifier",
    "claude-md-management:revise-claude-md", "context-mode:context-mode",
})
DESIGN_RE = re.compile(
    r"\b(design|ui|ux|typography|layout|animation|motion|visual|aesthetic|theme|palette)\b",
    re.IGNORECASE)
META_NAMES = frozenset({"skill-router", "skill-creator", "superpowers:writing-skills",
                        "superpowers:using-superpowers", "context-mode:context-mode"})

USE_WHEN_RE = re.compile(
    r"(?:^|[.;\n])\s*((?:use|invoke|trigger|apply|run)\s+(?:this\s+)?(?:skill\s+)?"
    r"(?:when|whenever|for|to|if|before|after|on)\b[^.;\n]{8,160})",
    re.IGNORECASE)
WHEN_HEADING_RE = re.compile(r"^#{1,4}\s*(when to use|use when|triggers?|use cases?)\b.*$",
                             re.IGNORECASE | re.MULTILINE)
BULLET_RE = re.compile(r"^\s*[-*]\s+(.{6,160})$", re.MULTILINE)

STOPWORDS = frozenset("""
a an the and or but if then else for to of in on at by with from into onto over under
is are was were be been being do does did doing have has had having can could should
would will shall may might must this that these those it its you your we our us me my
i he she they them their there here what which who why how when where all any both
each few more most other some such no nor not only own same so than too very just now
also about above after again against below between during before further once out off
up down one two three get got make made take please help need want like use used using
useful uses way ways thing things stuff skill skills agent agents claude code file files
run running runs add adds added adding fix fixes fixed fixing update updates updated
updating remove removes removed change changes changed changing new via etc e.g i.e
""".split())
_WORD_RE = re.compile(r"[a-z0-9][a-z0-9+#.-]*")


def tokenize(text: str) -> list[str]:
    out: list[str] = []
    for raw in _WORD_RE.findall(text.lower()):
        parts = [raw] + raw.replace("_", "-").split("-") if ("-" in raw or "_" in raw) else [raw]
        for p in parts:
            p = p.strip(".")
            if len(p) < 3 or p in STOPWORDS or p.isdigit():
                continue
            out.append(p)
    return out


# ---- inputs ------------------------------------------------------------------

def load_catalog(path: Path = CATALOG) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {"entries": [], "agents": []}
    return data if isinstance(data, dict) else {"entries": [], "agents": []}


def _skill_file(entry: dict) -> Optional[Path]:
    p = entry.get("source_path")
    if not p:
        return None
    path = Path(p)
    if path.is_dir():
        path = path / "SKILL.md"
    return path if path.is_file() else None


def source_hash(entry: dict) -> str:
    f = _skill_file(entry)
    try:
        raw = f.read_bytes() if f else (entry.get("description", "") + entry.get("body", "")).encode()
    except OSError:
        raw = (entry.get("description", "") + entry.get("body", "")).encode()
    return hashlib.sha1(raw).hexdigest()[:12]


def parse_projects(text: str) -> dict[str, dict]:
    """`projects:` block in SKILL.personal.md.

        projects:
          deenunlock:
            aliases: ["deenunlock", "deen unlock", "prayer app"]
            skills: ["mac-doctor"]
            memory: ["deenunlock-160-release-state"]
            gates: ["simulator screenshot"]
    """
    out: dict[str, dict] = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        if lines[i].strip() == "projects:":
            i += 1
            cur: Optional[str] = None
            while i < len(lines):
                raw = lines[i]
                s = raw.strip()
                if s.startswith("```") or (raw and not raw[0].isspace()):
                    break
                indent = len(raw) - len(raw.lstrip())
                if indent == 2 and s.endswith(":"):
                    cur = s[:-1].strip().strip("\"'")
                    out[cur] = {"aliases": [cur], "skills": [], "memory": [], "gates": []}
                elif cur and indent >= 4 and ":" in s:
                    k, _, v = s.partition(":")
                    items = [x.strip().strip("\"'") for x in v.strip().strip("[]").split(",") if x.strip().strip("\"'")]
                    if k.strip() in ("aliases", "skills", "memory", "gates"):
                        if k.strip() == "aliases":
                            out[cur]["aliases"] = sorted(set(out[cur]["aliases"] + items))
                        else:
                            out[cur][k.strip()] = items
                i += 1
            continue
        i += 1
    return out


def load_projects() -> dict[str, dict]:
    try:
        return parse_projects(PERSONAL.read_text(encoding="utf-8"))
    except OSError:
        return {}


def load_memory_names() -> list[str]:
    try:
        text = MEMORY_INDEX.read_text(encoding="utf-8")
    except OSError:
        return []
    return re.findall(r"\]\(([a-z0-9_-]+)\.md\)", text)


# ---- lexical derivation -------------------------------------------------------

def _clean(s: str) -> str:
    return " ".join(s.replace("*", "").replace("`", "").split())


def lexical_use_when(description: str, body: str) -> list[str]:
    text = f"{description}\n{body[:3000]}"
    found: list[str] = []
    for m in USE_WHEN_RE.finditer(text):
        found.append(_clean(m.group(1)))
    head = WHEN_HEADING_RE.search(body or "")
    if head:
        section = body[head.end():head.end() + 1500]
        nxt = re.search(r"^#{1,4}\s", section, re.MULTILINE)
        if nxt:
            section = section[:nxt.start()]
        found.extend(_clean(b) for b in BULLET_RE.findall(section)[:8])
    seen: set[str] = set()
    out: list[str] = []
    for f in found:
        k = f.lower()
        if k not in seen and len(f) >= 8:
            seen.add(k)
            out.append(f[:160])
    return out[:10]


def lexical_keywords(name: str, description: str, body: str, idf: dict[str, float]) -> list[str]:
    toks = tokenize(f"{description} {body[:2500]}")
    tf = Counter(toks)
    scored = sorted(((c * idf.get(t, 1.0)), t) for t, c in tf.items())
    scored.reverse()
    kws = [t for _, t in scored[:12]]
    for t in tokenize(name.split(":")[-1]):
        if t not in kws:
            kws.insert(0, t)
    return kws[:14]


def classify_kind(name: str, description: str, owner: str) -> str:
    if name in META_NAMES:
        return "meta"
    if name.startswith(PROCESS_PREFIXES) or name in PROCESS_NAMES:
        return "process"
    if owner == "project":
        return "project"
    if DESIGN_RE.search(name) or (owner == "user" and DESIGN_RE.search(description[:200] or "")):
        return "design"
    return "domain"


def owner_of(entry: dict) -> str:
    src = entry.get("source") or ""
    typ = entry.get("type") or ""
    if src == "project":
        return "project"
    if src == "user":
        return "command" if typ == "command" else "user"
    if src == "builtin":
        return "builtin"
    if src.startswith("plugin:"):
        return "plugin"
    return "user"


# ---- enrichment -----------------------------------------------------------------

def _enrich_cache_path(name: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", name)
    return CACHE_DIR / f"{safe}.json"


def read_enrichment(name: str, h: str) -> Optional[dict]:
    p = _enrich_cache_path(name)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if data.get("hash") == h else None


def enrich_one(name: str, description: str, body: str, h: str) -> Optional[dict]:
    """Ask a small model for triggers + keywords. Cached. Fail-soft."""
    if os.environ.get("SKILL_ROUTER_NO_ENRICH_CALLS") == "1":
        return None
    try:
        import llm_classify  # type: ignore[import-not-found]
    except ImportError:
        return None
    prompt = (
        "You index Claude Code skills for a router. Given one skill, return JSON only:\n"
        '{"use_when": ["3-6 short trigger phrases a user might type, describing situations, '
        'symptoms or tasks — NOT what the skill does"], "keywords": ["8-12 single lowercase '
        'words: tools, symptoms, nouns, project names"], "kind": "process|domain|design|project|meta"}\n'
        f"\nSKILL NAME: {name}\nDESCRIPTION: {description[:600]}\nBODY (excerpt):\n{body[:1800]}"
    )
    result = llm_classify.complete_json(prompt, max_tokens=400)
    if not isinstance(result, dict):
        return None
    use_when = [str(x)[:160] for x in result.get("use_when", []) if isinstance(x, str)][:6]
    keywords = [str(x).lower()[:40] for x in result.get("keywords", []) if isinstance(x, str)][:12]
    data = {"hash": h, "use_when": use_when, "keywords": keywords,
            "kind": result.get("kind"), "provider": result.get("_provider"),
            "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _enrich_cache_path(name).write_text(json.dumps(data, indent=1))
    except OSError:
        pass
    return data


# ---- build -------------------------------------------------------------------------

def build(catalog_path: Path = CATALOG, enrich: bool = False,
          projects: Optional[dict[str, dict]] = None) -> dict:
    cat = load_catalog(catalog_path)
    entries = [e for e in cat.get("entries", []) if e.get("invokable") and e.get("name")]
    projects = projects if projects is not None else load_projects()
    memory_names = load_memory_names()

    # IDF over the invokable population for keyword extraction.
    df: Counter = Counter()
    docs_tokens: dict[str, set[str]] = {}
    for e in entries:
        toks = set(tokenize(f"{e.get('description','')} {(e.get('body') or '')[:2500]}"))
        docs_tokens[e["name"]] = toks
        df.update(toks)
    n = max(1, len(entries))
    idf = {t: math.log((n + 1) / (c + 0.5)) for t, c in df.items()}

    skill_projects: dict[str, list[str]] = {}
    skill_gates: dict[str, list[str]] = {}
    skill_memory: dict[str, list[str]] = {}
    hubs: set[str] = set()
    for pname, p in projects.items():
        for i, s in enumerate(p.get("skills", [])):
            if i == 0:
                hubs.add(s)
            skill_projects.setdefault(s, []).extend(p.get("aliases", [pname]))
            skill_gates.setdefault(s, []).extend(p.get("gates", []))
            skill_memory.setdefault(s, []).extend(p.get("memory", []))

    out: list[dict] = []
    enriched_n = 0
    for e in entries:
        name = e["name"]
        desc = (e.get("description") or "").strip()
        body = e.get("body") or ""
        owner = owner_of(e)
        h = source_hash(e)
        use_when = lexical_use_when(desc, body)
        keywords = lexical_keywords(name, desc, body, idf)
        kind = classify_kind(name, desc, owner)
        enr = read_enrichment(name, h)
        if enr is None and enrich:
            enr = enrich_one(name, desc, body, h)
        if enr:
            enriched_n += 1
            for u in enr.get("use_when", []):
                if u.lower() not in {x.lower() for x in use_when}:
                    use_when.append(u)
            for k in enr.get("keywords", []):
                if k not in keywords:
                    keywords.append(k)
            # The model may promote a domain skill to design/meta; it never
            # decides "process" — that is a name rule, and letting the model
            # call 146 domain skills "process" hid every one of them.
            if enr.get("kind") in ("design", "meta") and kind == "domain":
                kind = enr["kind"]
        projs = sorted(set(skill_projects.get(name, [])))
        # A skill whose name is itself a project alias belongs to that project.
        for pname, p in projects.items():
            if name.split(":")[-1] in p.get("aliases", []) and pname not in projs:
                projs.extend(p.get("aliases", [pname]))
        mem = sorted(set(skill_memory.get(name, [])))
        if not mem:
            base = name.split(":")[-1].replace("-", "_")
            mem = [m for m in memory_names if m.replace("-", "_").startswith(base[:10])][:3]
        out.append({
            "name": name,
            "kind": kind,
            "owner": owner,
            "type": e.get("type"),
            "description": desc[:400],
            "use_when": use_when[:12],
            "keywords": keywords[:20],
            "projects": sorted(set(projs)),
            "gates": sorted(set(skill_gates.get(name, []))),
            "memory": mem,
            "pairs_with": [],
            "hub": name in hubs,
            "source_hash": h,
            "enriched": bool(enr),
        })
    out.sort(key=lambda d: d["name"])
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "version": VERSION,
        "total": len(out),
        "enriched": enriched_n,
        "projects": projects,
        "entries": out,
        "agents": cat.get("agents", []),
    }


def build_and_write(output: Path = OUTPUT, enrich: bool = False) -> dict:
    data = build(enrich=enrich)
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    tmp.replace(output)
    return data


def weak_description_report(data: dict) -> list[str]:
    """User-owned skills whose description says what, not when."""
    lines: list[str] = []
    for e in data["entries"]:
        if e["owner"] not in ("user", "project"):
            continue
        desc = e["description"]
        starts_when = bool(re.match(r"\s*\"?(use when|use this|invoke when|when )", desc, re.IGNORECASE))
        n_when = len([u for u in e["use_when"] if not e["enriched"]]) if not e["enriched"] else len(e["use_when"])
        if not starts_when and len(desc) < 60 or (not starts_when and n_when == 0):
            lines.append(f"  {e['name']:34} {len(desc):4} chars  {desc[:70]}")
    return lines


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--enrich", action="store_true", help="run/refresh model enrichment (cached)")
    ap.add_argument("--report", action="store_true", help="print weak-description report for user skills")
    ap.add_argument("--stdout", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--output", default=str(OUTPUT))
    args = ap.parse_args()
    if args.stdout:
        print(json.dumps(build(enrich=args.enrich), indent=1))
        return 0
    data = build_and_write(Path(args.output), enrich=args.enrich)
    if args.report:
        rep = weak_description_report(data)
        print(f"weak descriptions ({len(rep)} user skills):")
        print("\n".join(rep))
    if not args.quiet:
        kinds = Counter(e["kind"] for e in data["entries"])
        print(f"skill_index: {data['total']} skills, {data['enriched']} enriched, "
              f"kinds={dict(kinds)} → {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
