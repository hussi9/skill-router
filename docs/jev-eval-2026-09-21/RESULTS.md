# Jev as the router's chooser — results, 2026-09-21

## Verdict, blunt

**Does it route better? Yes, clearly.** 39/63 against 10/66 for the v4 lexical → Gemini pipeline,
and when it is confident (≥ 0.8) it is right 32 times in 34. The old pipeline's problem was
retrieval on misspelled prompts; Jev reading the whole index removes that problem.

**Does it slow you down? It depends on the hour, and that is a real risk.** The morning run was
+0.4 s per prompt (median 416 ms, 0 of 126 calls over 2 s). The afternoon run, same code, was
bimodal: 194 calls were either 250–550 ms or 2.6–6.8 s, and **35 % were the slow kind**; ten
sequential single calls gave 254 275 256 548 3150 2708 2641 3272 297 328 ms. Nothing in this repo
changed between the two. The cut-off is now 1.2 s (was 2 s) and a prompt that waited out a timeout
no longer also pays for the Gemini stage — so in a bad hour roughly a third of prompts wait 1.2 s
and get the weak lexical route instead of Jev's. If that pattern persists, the router is slower
AND no more accurate than v4 on those prompts. Watch it for a few days before trusting it. Billed to TypeSafe, not the Claude plan: ~$0.0005 per uncached prompt, ~$2 per 3,000 prompts.

**Does it hurt your Claude token usage? A little, not a lot — and less than the router you removed.**
- The card: ~120 tokens, on ~15 % of prompts (measured on 150 real turns that needed no skill; the
  first build carded 34 % until the regex table leg was removed). Averaged over all prompts that
  is ~17 fresh tokens per prompt. It is re-read on every later turn, which at your ~33 assistant
  turns per prompt is still only a few thousand cache-read tokens per session. Noise.
- The skill bodies the card causes to load are the real cost: median ~2.8k tokens each (one is
  21.9k), carried to the end of the session. With the router on you loaded 6.6 skills per 100
  prompts; off, 0.9. Expect roughly one extra skill load per 10–15 prompts, i.e. **+1–3 % of a
  150k-token context for the rest of that session, per skill loaded**. Long sessions burn the
  plan; this does not.
- Nothing was found that it *saves*. It does not shorten sessions or avoid reads. Its token effect
  is a small pure cost.

**Is it worth turning back on? Unproven, and that is the honest answer.** What is measured is that
the router picks the skill Claude picked. What nobody has measured — here or in the baseline
session — is whether loading that skill makes the work better. Two facts cut in opposite directions:
- With the router off, skills get used 7× less (0.9 vs 6.6 per 100 prompts). If you believe the
  skills matter, the router is the only thing that has ever made them run.
- 55 of the 64 eval pairs were recorded while the OLD router was on and forcing skills with the
  IRON RULE. So much of the "truth" is what the old regex table told Claude to load, and Jev's
  process-leg score partly measures agreement with that table. On the 9 pairs recorded after the
  router was removed — the only unforced ones — Jev is right 4 of 8. That is too few to conclude
  anything, in either direction.

Of the 23 cards it would have shown on turns where you used no skill, about 14 read as sensible
(an implementation-plan request → `writing-plans`, "did you test it 100 %?" →
`verification-before-completion`, organise a folder → `file-organizer`) and about 8 are noise
("both" → `dealscout`, "keep going" → `store-screenshots`, pasted assistant text → `mac-doctor`).
So roughly one card in three is wasted, each wasted card costing ~120 tokens if ignored and a
few thousand if obeyed.

Recommendation: if you turn it on, turn it on **soft-only** and look at the follow-rate after a
week. If you are not going to check whether the loaded skills changed the work, leave it off —
you lose nothing measurable.

Everything here was measured by `router-eval-provider.py`, which calls the shipped
`scripts/jev_choose.py` (not a copy of its logic) on real `(prompt → first Skill invoked)`
pairs mined from `~/.claude/projects` transcripts of the last 75 days. Run it from a scratch
directory; it writes results into the current working directory.

## Read this first: the ground truth is noisy

"Truth" is the skill Claude invoked on that turn, not the skill that was *right*. Three
consequences:

- When two skills overlap, the label is whichever one Claude happened to pick. Both of the
  confident wrong routes below are `premium-uiux-audit` chosen where the transcript says
  `minimalist-ui`, on prompts that literally ask for a UI review. Jev's answer is arguably
  the better one. The score counts it as a miss.
- 33 mined pairs had a harness builtin as truth (`artifact-design` 18, `loop` 9, `dataviz` 2,
  `schedule` 2, `artifact-capabilities` 1, `claude-api` 1). The harness triggers those from its
  own instructions, not from the prompt. They are counted and excluded, not scored.
- The set is small (63 scored pairs). A difference of one or two hits is not a finding.
  The context decision below rests on buckets of 7 and 20 prompts. Treat it as a default, not a law.

## Headline

| Pipeline (same kind of pairs) | Right | Notes |
|---|---|---|
| Lexical rank → Gemini Flash-Lite over top 8 (v4.0) | 10 / 66 | right skill in lexical top 30 only 23 / 66 — typos |
| Jev as drop-in tie-break over the same top 8 | 13 / 66 | same retrieval ceiling |
| **Jev over the whole index, shipped provider, fresh index** | **39 / 63 (62 %)** | process leg 24 / 34 |

Shipped configuration, 63 pairs of 20–700 chars, index rebuilt today (200 entries, 177 offered):

- **≥ 0.8 (routes): fires 34 times, right 32 (94 %).** Both misses are UI-vs-UI (above).
- **0.5–0.8 (suggestions): fires 18 times, right 6 (33 %).** Weak, and it would also print on
  35 % of turns that needed no skill. Built as asked, but **off by default**
  (`SKILL_ROUTER_JEV_SUGGEST=1` turns it on).
- Below 0.5 it stays silent: 8 named picks (1 of them right) and 3 `none` answers.
- Latency: median 416 ms, p90 626 ms, max 771 ms, none over the 2 s cut-off.
- ~12.3k input tokens per uncached prompt ≈ $0.0005. Answers are cached by
  prompt + context + index fingerprint.

## Decisions the measurements made

**No hardcoded process list.** The first eval used a hand-written list of 11 process skills.
The provider splits on the index's own `kind` instead. With every `kind == process` entry offered
(27), the process leg fell from 24/34 to 18/33: slash commands (`debug`, `review`, `qa`,
`feature-dev`) duplicate real skills and took the votes. Dropping entries of type `command` /
`plugin-command` restored 24/33 with no list. Dropping `builtin` as well kept the hit count
(38/67) and raised confident-route precision from 29/32 to 31/34, because `artifact-design` and
friends stopped winning confident wrong routes. Cost: a builtin can no longer be routed to —
the harness already advertises them every session. A user skill that shadows a builtin's name
(`code-review` today) is indexed as a skill and stays routable.

**Previous assistant turn: only for short prompts.** Last 300 chars of the prior assistant
message in `state.previous_assistant_message`, measured on pairs that had one:

| Prompt length | n | hits plain → with context | confident routes right/fired |
|---|---|---|---|
| ≤ 6 words | 7 | 3 → 5 | 3/3 → 5/5 |
| 7–15 words | 20 | 10 → 10 | 6/8 → 8/9 |
| ≥ 16 words | 36 | 16 → 14 | 12/15 → 11/14 |

"yes" went from `none` (0.92) to `prove-idea` (1.00); "yes pelase concintue" from `none` (0.98)
to `subagent-driven-development` (0.91). On long prompts the context pulled the answer toward
whatever had just been discussed. So it is sent for ≤ 15 words only (`CONTEXT_MAX_WORDS`).
Kept, with the caveat that the ≤ 6-word bucket is seven prompts.

**Jev's QUESTION is never used to silence a turn.** On prompts that did invoke a skill it
answered QUESTION 5 times in 63 (3 of them at ≥ 0.8). The path answer only fills in
BROKEN / BUILD / OPERATE when the regex triage and the index are both silent.

## What is still wrong (24 misses)

- 10 are one design skill chosen over another.
- 5 are one process skill over a neighbour (`brainstorming` ↔ `writing-plans`,
  `executing-plans` ↔ `using-git-worktrees`).
- 3 chose `none`; 2 are image-only or URL-only prompts with nothing to judge.
- The rest are genuinely ambiguous prompts ("pealse reveiw above direciton").

## Proposal 1 — overlapping UI / design skills (NOT performed)

41 of 200 indexed skills are `kind: design`. Usage = `Skill` invocations found in all retained
Claude Code transcripts (222 invocations in total, so the window is short and "0" means "not in
the last few weeks", not "never in your life").

Nothing below has been moved. On a yes, each goes to `~/.claude/skills/.archive/` with `mv`.

| Keep (one per job) | Uses | Overlapping skills proposed for archive | Uses |
|---|---|---|---|
| `frontend-design` (plugin) — build new UI | 5 | `design-taste-frontend`, `high-end-visual-design`, `impeccable` | 0, 0, 0 |
| `emil-design-eng` — polish / motion feel | 3 | — | |
| `minimalist-ui` — the editorial house style | 3 | — | |
| `design-review` — audit a live UI and fix it | 2 | `premium-uiux-audit`, `ui-review`, `redesign-existing-projects`, `claude-mem:design-is` (plugin: disable, cannot archive) | 0, 0, 0, 0 |
| `uxui-principles` — principle lookup | 2 | `ui-ux-pro-max` | 0 |
| `design-consultation` — new design system | 2 | — | |
| `plan-design-review` — review a plan, not a UI | 0 | keep: distinct job, but rename risk with `design-review` (1 miss) | |
| `ui-pattern`, `ux-flow` (StyleSeed pair) | 1, 0 | keep only if StyleSeed is in use; otherwise archive both | |
| `animate` | 1 | `improve-animations`, `review-animations`, `find-animation-opportunities` overlap each other; keep `animate` + `review-animations` | 0, 0, 1 |

Caveat on `premium-uiux-audit`: zero recorded uses, but Jev prefers it to `design-review` and
`minimalist-ui` on audit-shaped prompts, which says its description is the clearest of the
group. The alternative to archiving it is to archive `design-review` instead and keep this one.
That is a taste call; the router only needs there to be one.

Expected effect: the 10 design-vs-design misses are the largest single bucket, and split votes
are why several correct answers sit at 0.5–0.7 instead of above 0.8.

## Proposal 2 — Codex (decision: usage-filtered index, not a two-level Choice)

> **Superseded the same day.** Another session pruned `~/.codex/skills` at the user's request:
> 1,243 skills moved to `~/.codex/.archive/skills-pruned-20260921/` (with `MANIFEST.json` and
> `UNDO.sh`), 190 left (verified by `ls`). That is under the 255-option cap, so the whole Codex
> directory now fits one Choice and no usage filter is needed. The numbers below are the
> pre-prune measurement that led to the same place. On the Claude side only `artifacts-builder`
> was moved; the index was rebuilt afterwards (199 entries). Still not built: a Codex-side hook.

Measured over all 2,566 Codex session files (14 GB), counting only tool calls whose arguments
read `skills/<name>/SKILL.md` — the skill list injected into every session's prompt mentions
all 1,433 paths and had to be excluded or every skill looks used 2,557 times:

| Installed in `~/.codex/skills` | 1,433 |
|---|---|
| Ever read by a tool call | 208 |
| Read in ≥ 2 sessions | 116 |
| Read in ≥ 3 sessions | 79 |
| Read in ≥ 5 sessions | 55 |

Top: `code-review` 422, `security-review` 61, `building-native-ui` 57, `copywriting` 44,
`ad-creative` 44, `visual-verdict` 41. There is an alphabetical bias in the tail
(`acceptance-orchestrator`, `accessibility-…`, `active-directory-attacks`, `advogado-especialista`):
Codex reads down a truncated list, so part of "used once" is noise.

Decision: **index the ≥ 2-session set (116) and leave the directory alone.** It fits one Choice
(cap 255) with room to grow. The full 1,433 does not fit a request at all: at ~60 tokens per
option it is ~86k tokens against a 64k limit, so "everything in one call" was never available,
and a two-level category→skill Choice would double latency and need a category taxonomy that
does not exist and would have to be maintained. `jev_choose.py` already splits an index over
255 across several questions, so nothing breaks if the set grows.

Pruning `~/.codex/skills` itself (moving ~1,200 unused skills to `.archive/`) is a separate,
bigger win for Codex's own prompt size, and is yours to call. Not done. Not wired: there is no
Codex-side hook in this repo yet, so this is a build step, not a switch.

## Re-run after the prune and the vercel plugin was disabled (same day, afternoon)

Index 199 → 137 entries (118 offered to Jev): the catalog now skips plugins that
`settings.json` switches off, which removed 46 `vercel:*` skills that could be chosen but not
loaded. Accuracy did not move: **39 / 61 right, process leg 25 / 34, ≥ 0.8 fires 34 and is right
32.** Input tokens per call fell from ~12.3k to ~8.3k. The two confident misses are now
`ui-ux-pro-max` for `animate` and `scrollbook-deploy` for `store-screenshots`. Latency is the
afternoon figure above.

## State of the machine

- `~/.claude/skill_router_catalog.json` and `~/.claude/skill_index.json` were rebuilt today
  (229 stale → 200 current entries). The stale copies are in `~/.claude/.archive/`
  (`*-stale-20260913.json`). `projects: 0` in the new index because `SKILL.personal.md` is read
  through the `~/.claude/skills/skill-router` symlink, which is gone; it returns when re-linked.
- `~/.claude/skill_router_cache/env.json` now also holds `TYPESAFE_API_KEY` (mode 600).
- Hooks ARE installed as of the afternoon — by the originating session, on the user's yes given
  there, not by this one: symlink restored, `install_hooks.py` run (+8), settings backed up to
  `~/.claude/.archive/config-backups-20260921/`. The `vercel` plugin was disabled there too.
  To turn the router off again: `python3 scripts/install_hooks.py --remove`.
- Test suite: **15 failed, 193 passed** (was 32 failed before the symlink came back). `doctor.py`
  passes every check. The 15 that remain are all the same thing: tests written against the
  author's machine as it was before 09-14. They expect `refactor`, `connect-apps` and
  `supabase:*` to be installed; none is (archived command, archived skill, disabled plugin), so
  the router correctly says nothing where the test expects a card. One is lexical ranking on
  the fallback path (`design-review` not in the top 3 for a DeenUnlock design prompt). None
  involves Jev. Fixing them means either reinstalling those skills or rewriting the tests to build
  their own fixture skills instead of reading the live install — worth doing, not done.
