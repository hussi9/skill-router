# Routing with and without Jev (2026-11-02)

Question: can a user with no Jev, Gemini or Anthropic key get useful routing? Two cheap layers were built
and measured, plus one diagnostic run of local semantic search.

- **Layer 1, typo correction** (`index_match.correct`): an unknown prompt word maps to the nearest index word
  within 1–2 edits (adjacent swaps count once; the first letter is never substituted). On by default,
  `SKILL_ROUTER_FUZZY=0` turns it off. ~5–25 ms.
- **Layer 4, shortlist** (`router.shortlist_line`): when nothing routes, name up to three installed skills
  and let Claude choose. Names only (~30 tokens), work turns only, never a loaded skill or the router, never
  enforced or asked about, not repeated within 5 turns of a session. A fallback only: silent whenever Jev
  answered the prompt. `SKILL_ROUTER_SHORTLIST`: **off by default**; `auto` (opt-in) shows it only when the
  local embedding daemon answers; `1` also allows keyword-only. Reason in "Work gate" below.

## Data

| Set | Size | Ground truth | Caveat |
|---|---|---|---|
| labeled | 27 real tasks + 6 chat controls | adjudicated acceptable skills | small; hard strategic prompts |
| mined | 74 real prompts | the domain skill Claude actually loaded next | biased toward skills earlier cards suggested |

Prompts stay local (git-ignored). `scripts/benchmark_nokey.py` reports counts only.

## Results

Where does the right skill land in the retrieval ranking (tasks only)?

| Retrieval | labeled top-1 | top-3 | top-5 | mined top-1 | top-3 | top-5 |
|---|---:|---:|---:|---:|---:|---:|
| keyword | 2 | 6 | 7 | 17 | 22 | 23 |
| keyword + typo fix | 2 | 7 | 7 | 18 | 22 | 23 |
| semantic (bge-small, local) | 7 | 10 | 13 | 7 | 14 | 20 |
| keyword + semantic, fused | 6 | 10 | 12 | 15 | 20–21 | 24–25 |

What the user would see (floor 4.0):

| System | labeled: right / lines shown / false alarms on chat | mined: right / shown | median chars per line |
|---|---|---|---:|
| v2.0 today, no key (card) | 1 / 8 / 0 | 10 / 35 | 434–501 |
| + typo fix | 0 / 9 / 0 | 10 / 35 | 414–501 |
| shortlist, keyword retrieval | 2 / 8 / 0 | 11 / 33 | 140–146 |

For reference, Jev with a key routed 2 of the same 27 labeled tasks correctly (`docs/jev-eval-2026-10-22-real/`).

## What this says

1. **Typo correction is neutral.** Typos are not the bottleneck; vocabulary is ("sort the backlog cards into piles"
   shares no words with `product-manager`). It stays on because it is cheap and never fires on known words.
2. **The shortlist is a cheaper format, not a better chooser yet.** It is a third of the card's size and never
   enforces a wrong skill, but with keyword retrieval the right name is in it about one time in four to eight.
   Shipping it on would mostly add tokens, so it ships off.
3. **Semantic retrieval is the missing piece.** Locally, with no key, it finds the right skill in the top three
   on 10 of 27 hard tasks (keyword: 6–7) and, fused with keywords, keeps the keyword wins on the mined set.
   That is the retrieval the shortlist needs. Query cost is ~5 ms once the model is warm (the repo already
   runs it in `embedder_daemon.py`).
4. **No method separates chat from tasks by similarity** (controls scored 0.58–0.71, tasks median 0.68), so
   the shortlist stays gated on "is this a work request", not on a score.

## With Jev: its full probability list (shipped, on by default)

Jev already returns a probability for every skill it was offered, not just its top pick. The router now keeps
the top six (`Choice.ranked`, same request, no extra cost) and, when the top pick is below the 0.8 routing bar,
names Jev's top three for Claude to choose from. Same burden rules as above, plus a stricter work gate: the local
triage, an explicit request, or Jev itself ≥ 0.8 sure it is work.

Retrieval potential, one Jev request per prompt (107 calls, 2.95M input tokens, median 415 ms, 0 failures):

| Variant | labeled top-1 / top-3 (of 27) | mined top-1 / top-3 (of 74) |
|---|---:|---:|
| keyword (keyless) | 2 / 7 | 18 / 22 |
| keyword + semantic (keyless) | 7 / 11 | 14 / 20 |
| Jev argmax at ≥ 0.8 (the old card) | 2 / 2 | 9 / 9 |
| **Jev probability list** | **6 / 14** | **26 / 39** |
| Jev Noul rerank of the fused top 8 | 7 / 10 | 20 / 26 |
| probability list + rerank, fused | 6 / 15 | 19 / 34 |

What the shipped router prints (card, or Jev's three names below the bar):

| | right skill shown (labeled) | (mined) | chat false alarms | median chars |
|---|---:|---:|---:|---:|
| Jev before this change | 2 / 27 | 11 / 74 | 0 / 6 | ~430 |
| **Jev + its top-three list** | **7 / 27** | **38 / 74** | **0 / 6** | ~185 |
| keyless, keyword shortlist | 2 / 27 | 11 / 74 | 0 / 6 | ~145 |
| keyless, keyword + semantic shortlist | 4 / 27 | 8 / 74 | 0 / 6 | ~150 |

The Noul rerank was not shipped: it adds eight questions per request and does not beat the list Jev already
returns. The keyless shortlist stays off by default (`SKILL_ROUTER_SHORTLIST=1`): even with semantic retrieval it
shows the right skill on 4 of 27 and 8 of 74, below the bar. Semantic retrieval needs the local embedding daemon
(`setup/launchd-embedder.plist`); the router uses it only when it answers within 150 ms.

**Jev is the difference.** With a key, the right skill reaches Claude on 7 of 27 hard tasks and 38 of 74 everyday
ones with the old gate (10 of 27 and 38 of 74 in-sample with the work gate below), and 0 of 6 chat controls got a
line. Without a key, the best local method reaches 4 and 8 with the old gate.

## Work gate (2026-11-02)

Both shortlists (keyless and Jev's three names) used to need an imperative or request marker, so real tasks phrased
as questions ("do you think X fits?") or opened with an acknowledgement ("Yes, publish ...") never got a line.
`index_match.work_signal(prompt)` (0-1) now gates them at `router.WORK_AT = 0.6`:

- 1.0: a request marker, an imperative leading the turn (after "yes / ok / go with B:"), or an imperative
  with an object ("the", "it", "our", ...) leading a later sentence. Extra verbs not in `is_request`
  (keep, use, plan, map, ...) count only at the start of the first sentence and only with an object, so
  "Keep in mind ..." or "Plan looks fine" stay chat.
- 0.8: something broken (checked before the concept words, so "... fails, which means ..." is work).
- 0.7: a question naming something people build or run (page, app, platform, integration, saas, account,
  database, ...; one or two typing slips tolerated). Explanation questions ("how does / why do ...") stay 0.3.
- 0.3: other questions and statements. 0.1: concept questions ("what is", "... mean", "difference between").
- 0.0: empty input, a short ack (ok, thanks, yes, no, hi, hello), or three words or fewer.

Jev's list also shows when Jev's own path is work at >= 0.8, as before. `detect_path` and the route card are
unchanged. The keyless list is a fallback only: when Jev answered the prompt (even if it printed nothing), the
keyless line stays silent; it speaks only when Jev is unavailable, failed or timed out.

**In-sample numbers.** The patterns were written while looking at these same 27 + 74 prompts, so treat the counts
as optimistic. Precision = right lines / lines shown.

| System | labeled right / shown (precision) | mined right / shown (precision) | chat false alarms |
|---|---:|---:|---:|
| Jev + its top-three list, old gate | 7 / 19 (37 %) | 38 / 62 (61 %) | 0 / 6 |
| **Jev + its top-three list, work_signal gate** | **10 / 25 (40 %)** | **38 / 63 (60 %)** | **0 / 6** |
| keyless keyword shortlist, old gate | 2 / 8 (25 %) | 11 / 33 (33 %) | 0 / 6 |
| keyless keyword shortlist, work_signal gate | 7 / 20 (35 %) | 16 / 42 (38 %) | 0 / 6 |
| keyless keyword + semantic, old gate | 4 / 8 (50 %) | 8 / 36 (22 %) | 0 / 6 |
| keyless keyword + semantic, work_signal gate | 10 / 21 (48 %) | 13 / 45 (29 %) | 0 / 6 |
| no key, card only (typo fix on) | 0 / 9 (0 %) | 10 / 35 (29 %) | 0 / 6 |

Jev path, per case against the old gate: none of its 7 labeled or 38 mined hits lost; 3 labeled gained. Its
numbers are the same with the daemon running. (A looser first version of the gate also reached 39 / 74 mined;
the stricter gate gives that one prompt up.)

**Keyless default: off.** Default-on needs >= 9 / 27 labeled recall with 0 / 6 false alarms *and* >= 40 %
precision on both sets. Keyword + semantic meets the first (10 / 27, 0 / 6) and labeled precision (48 %) but
not mined precision (29 %): on everyday prompts most lines would name the wrong skills. `SKILL_ROUTER_SHORTLIST=auto`
stays available as an opt-in (daemon only); `1` also allows keyword-only. The "shown" column is how many turns
of each set got a line per run.

## Next

- With Jev: measure whether Claude actually loads a skill from the three names (the learner's follow rate).
