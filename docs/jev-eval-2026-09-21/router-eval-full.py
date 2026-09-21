#!/usr/bin/env python3
"""No lexical pre-filter: Jev chooses among ALL indexed skills, plus a separate process-skill question, in one call."""
import json, os, time, urllib.request, urllib.error
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
HERE = Path(__file__).parent
rows = json.load(open(HERE / "router-eval-results.json"))
entries = [e for e in json.load(open(Path.home() / ".claude/skill_index.json"))["entries"] if e.get("type") != "agent"]
PROCESS = ["brainstorming", "writing-plans", "executing-plans", "subagent-driven-development", "systematic-debugging", "test-driven-development",
           "code-review", "requesting-code-review", "verification-before-completion", "using-git-worktrees", "finishing-a-development-branch"]
short = lambda s: s.split(":")[-1]
byname = {short(e["name"]): e for e in entries}
def summ(e, n=150):
    uw = "; ".join((e.get("use_when") or [])[:2]); return f"{short(e['name'])}: {(e.get('description') or '')[:n]} | {uw}"[: n + 90]
domain = [e for e in entries if short(e["name"]) not in PROCESS][:250]
dkeys = {f"d{i}": short(e["name"]) for i, e in enumerate(domain)}
dcrit = {k: summ(byname[n]) for k, n in dkeys.items()}; dcrit["none"] = "No specialised skill fits; this is general work or conversation"
pkeys = {f"p{i}": n for i, n in enumerate(PROCESS) if n in byname}
pcrit = {k: summ(byname[n], 200) for k, n in pkeys.items()}; pcrit["none"] = "No process discipline applies; a direct answer or a small direct action"
def call(r):
    body = {"model": "jev-1.13.0", "state": {"request": r["prompt_full"][:600]}, "questions": {
        "domain": {"type": "choice", "instructions": "Which installed skill's subject matter matches what the developer's `request` is about. The request may contain spelling mistakes; judge the intended meaning.", "criteria": dcrit},
        "process": {"type": "choice", "instructions": "Which working method the developer's `request` calls for. The request may contain spelling mistakes; judge the intended meaning.", "criteria": pcrit}}}
    req = urllib.request.Request("https://api.typesafe.ai/v1/systemone", data=json.dumps(body).encode(), method="POST",
                                 headers={"content-type": "application/json", "Authorization": f"Bearer {os.environ['TYPESAFE_API_KEY']}"})
    t = time.time()
    try:
        d = json.loads(urllib.request.urlopen(req, timeout=20).read().decode())
    except urllib.error.HTTPError as e:
        return {**r, "err": f"{e.code} {e.read().decode()[:200]}"}
    a = d["answers"]
    return {**r, "ms": (time.time() - t) * 1000, "tok": d["usage"]["input_tokens"], "fd": dkeys.get(a["domain"]["choice"]), "fdc": a["domain"]["confidence"],
            "fp": pkeys.get(a["process"]["choice"]), "fpc": a["process"]["confidence"]}
full = {p["prompt"][:90]: p["prompt"] for p in json.load(open(HERE / "router-eval-set.json"))}
for r in rows: r["prompt_full"] = full.get(r["prompt"], r["prompt"])
with ThreadPoolExecutor(3) as ex: out = list(ex.map(call, rows))
json.dump(out, open(HERE / "router-eval-full-results.json", "w"), indent=1)
ok = [r for r in out if "err" not in r]; n = len(out)
if len(ok) < n: print("errors", n - len(ok), [r["err"] for r in out if "err" in r][:1])
med = lambda xs: sorted(xs)[len(xs) // 2]
isproc = lambda r: r["truth"] in PROCESS
P = [r for r in ok if isproc(r)]; D = [r for r in ok if not isproc(r)]
print(f"options: {len(dcrit)} domain + {len(pcrit)} process | median {med([r['ms'] for r in ok]):.0f}ms | {med([r['tok'] for r in ok])} tokens/prompt = ${med([r['tok'] for r in ok]) * 0.042 / 1e6:.5f}")
print(f"process-skill truths: Jev full {sum(r['fp'] == r['truth'] for r in P)}/{len(P)}   (lexical top-1 {sum(r['top1'] == r['truth'] for r in P)}, Gemini/top-8 {sum(r.get('g') == r['truth'] for r in P)}, Jev/top-30 {sum(r.get('j30') == r['truth'] for r in P)})")
print(f"domain-skill  truths: Jev full {sum(r['fd'] == r['truth'] for r in D)}/{len(D)}   (lexical top-1 {sum(r['top1'] == r['truth'] for r in D)}, Gemini/top-8 {sum(r.get('g') == r['truth'] for r in D)}, Jev/top-30 {sum(r.get('j30') == r['truth'] for r in D)})")
hit = lambda r: (r["fp"] if isproc(r) else r["fd"]) == r["truth"]
print(f"combined: {sum(map(hit, ok))}/{len(ok)}")
print("\nremaining misses:")
for r in ok:
    if not hit(r): print(f"  truth={r['truth']:<30} domain={str(r['fd']):<24}({r['fdc']:.2f}) process={str(r['fp']):<28}({r['fpc']:.2f}) | {r['prompt'][:62]!r}")
