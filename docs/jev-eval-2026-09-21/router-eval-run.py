#!/usr/bin/env python3
"""Real prompts -> which skill? Lexical rank vs Gemini Flash-Lite tie-break vs Jev Choice. Read-only."""
import json, os, sys, time, urllib.request, urllib.error
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, str(Path.home() / "devpro/skill-router/scripts"))
import index_match  # noqa: E402  (pure ranking; no hook state is written)

HERE = Path(__file__).parent
SKIP = {"skill-router", "artifact-design", "artifact-capabilities", "loop", "schedule", "update-config"}
pairs = [p for p in json.load(open(HERE / "router-eval-set.json")) if p["in_index"] and p["skill"] not in SKIP]
idx = {e["name"]: e for e in json.load(open(Path.home() / ".claude/skill_index.json"))["entries"]}
short = lambda s: s.split(":")[-1]


def summary(name):
    e = idx.get(name) or idx.get(short(name)) or {}
    uw = "; ".join((e.get("use_when") or [])[:2])
    return (f"{(e.get('description') or '')[:160]} | {uw}")[:240]


def post(url, headers, body, timeout=10):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"content-type": "application/json", **headers}, method="POST")
    t = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode()), (time.time() - t) * 1000
    except urllib.error.HTTPError as e:
        return {"_err": f"{e.code} {e.read().decode()[:160]}"}, (time.time() - t) * 1000
    except Exception as e:  # noqa: BLE001
        return {"_err": str(e)[:160]}, (time.time() - t) * 1000


def gemini_pick(prompt, cands):
    lines = "\n".join(f"- {n}: {summary(n)[:140]}" for n in cands)
    p = ("You route a developer's request to the right installed Claude Code skill.\n"
         "Return JSON only: {\"path\": \"BROKEN|BUILD|OPERATE|QUESTION\", \"skills\": [\"up to 2 candidate names that truly fit, best first, or empty\"], \"reason\": \"<12 words\"}\n"
         "BROKEN = something is failing/wrong. BUILD = create something new. OPERATE = improve, ship, research, configure, review. QUESTION = the user wants an answer, not work.\n"
         f"Candidates:\n{lines}\n\nRequest: {prompt[:600]}")
    d, ms = post(f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-lite:generateContent?key={os.environ['GEMINI_API_KEY']}", {},
                 {"contents": [{"parts": [{"text": p}]}], "generationConfig": {"responseMimeType": "application/json", "temperature": 0, "maxOutputTokens": 120}})
    try:
        out = json.loads(d["candidates"][0]["content"]["parts"][0]["text"])
        s = [x for x in out.get("skills", []) if x in cands]
        return (s[0] if s else None), ms, None
    except Exception:  # noqa: BLE001
        return None, ms, d.get("_err", "parse")


def jev_pick(prompt, cands):
    keys = {f"s{i}": n for i, n in enumerate(cands)}
    criteria = {k: f"{n}: {summary(n)}" for k, n in keys.items()}
    criteria["none"] = "None of the listed skills genuinely fits this request"
    d, ms = post("https://api.typesafe.ai/v1/systemone", {"Authorization": f"Bearer {os.environ['TYPESAFE_API_KEY']}"},
                 {"model": "jev-1.13.0", "state": {"request": prompt[:600]},
                  "questions": {"skill": {"type": "choice", "instructions": "Which installed coding-agent skill should handle the developer's `request`. Pick the skill whose purpose matches what the developer is asking to get done.", "criteria": criteria}}})
    try:
        a = d["answers"]["skill"]
        return keys.get(a["choice"]), ms, None, a.get("confidence"), d["usage"]["input_tokens"]
    except Exception:  # noqa: BLE001
        return None, ms, d.get("_err", "parse"), None, 0


def run(p):
    truth = short(p["skill"])
    ranked = [m.name for m in index_match.rank(p["prompt"], limit=30)]
    r = {"prompt": p["prompt"][:90], "truth": truth, "top1": short(ranked[0]) if ranked else None,
         "in8": truth in map(short, ranked[:8]), "in30": truth in map(short, ranked[:30])}
    c8, c30 = ranked[:8], ranked[:30]
    if c8:
        g, r["g_ms"], r["g_err"] = gemini_pick(p["prompt"], c8)
        j8, r["j8_ms"], r["j8_err"], r["j8_conf"], r["j8_tok"] = jev_pick(p["prompt"], c8)
        j30, r["j30_ms"], r["j30_err"], r["j30_conf"], r["j30_tok"] = jev_pick(p["prompt"], c30)
        r.update(g=short(g) if g else None, j8=short(j8) if j8 else None, j30=short(j30) if j30 else None)
    return r


with ThreadPoolExecutor(4) as ex:
    rows = list(ex.map(run, pairs))
json.dump(rows, open(HERE / "router-eval-results.json", "w"), indent=1)

n = len(rows)
med = lambda xs: sorted(xs)[len(xs) // 2] if xs else 0
hit = lambda k: sum(1 for r in rows if r.get(k) == r["truth"])
print(f"real prompt->skill pairs: {n}")
print(f"lexical: truth in top-8 {sum(r['in8'] for r in rows)}/{n} | in top-30 {sum(r['in30'] for r in rows)}/{n} | top-1 correct {hit('top1')}/{n}")
print(f"Gemini Flash-Lite over top-8 : {hit('g')}/{n}  median {med([r['g_ms'] for r in rows if 'g_ms' in r]):.0f}ms  errors {sum(1 for r in rows if r.get('g_err'))}")
print(f"Jev over top-8               : {hit('j8')}/{n}  median {med([r['j8_ms'] for r in rows if 'j8_ms' in r]):.0f}ms  errors {sum(1 for r in rows if r.get('j8_err'))}")
print(f"Jev over top-30              : {hit('j30')}/{n}  median {med([r['j30_ms'] for r in rows if 'j30_ms' in r]):.0f}ms  errors {sum(1 for r in rows if r.get('j30_err'))}")
tok = sum(r.get("j30_tok", 0) for r in rows)
print(f"Jev top-30 cost: {tok} tokens = ${tok * 0.042 / 1e6:.5f} for {n} prompts (${tok * 0.042 / 1e6 / max(n, 1) * 1000:.4f} per 1000 prompts /1000)")
# does Jev's confidence separate right from wrong?
right = [r["j30_conf"] for r in rows if r.get("j30") == r["truth"] and r.get("j30_conf") is not None]
wrong = [r["j30_conf"] for r in rows if r.get("j30") != r["truth"] and r.get("j30_conf") is not None]
print(f"Jev top-30 confidence: right median {med(right):.2f} (n={len(right)}) | wrong median {med(wrong):.2f} (n={len(wrong)})")
errs = [r.get("j30_err") or r.get("g_err") for r in rows if r.get("j30_err") or r.get("g_err")]
if errs: print("sample error:", errs[0])
print("\nmisses (Jev top-30):")
for r in rows:
    if r.get("j30") != r["truth"]:
        print(f"  truth={r['truth']:<28} jev={str(r.get('j30')):<26} gem={str(r.get('g')):<24} in30={r['in30']} | {r['prompt'][:70]!r}")
