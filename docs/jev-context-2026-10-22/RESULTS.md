# Fresh conversation-context routing evaluation — 2026-10-22

Threshold remains **0.8**, model `jev-1.13.0`, timeout 1.2 seconds. No production context widening or confidence-folding changes were made.

## Frozen real examples

23 unseen actual user messages with their preceding visible assistant responses, across nine sessions: 17 specialist tasks and six controls. Labels were independently reviewed before predictions; ambiguous and previously used examples were excluded. See cases.json, labels.md, adjudication.md and excluded-cases.json. Original wording and provenance are retained locally; these contain private conversation material and must not be published.

| Arm | Correct overall | Specialist routes correct | False routes on controls | Provider failures |
|---|---:|---:|---:|---:|
| Request only | 8/23 | 2/17 | 0/6 | 0 |
| Production context: requests <=15 words | 7/23 | 2/17 | 1/6 | 0 |
| Context for every request | 7/23 | 2/17 | 1/6 | 1 |

69 attempts, no retries or cache hits. Order was counterbalanced. These single-pass results do **not** demonstrate an improvement or isolate causal effects: identical provider states also produced varying answers. The all-context timeout remains a failure, even though a late answer was captured. Actual context was sent on 0/12/23 cases respectively; no input was truncated. See run-1/manifest.json, summary.json, rows.jsonl and answers.jsonl. evaluated_chooser.py preserves the exact evaluated chooser; logging added afterward has a different hash.

## Why specialists are missed

- All expected skills were present. The index contains 426 entries, including 408 eligible domain/process skills.
- An accepted specialist appeared among successful raw chunk winners for only 8/17 tasks in each arm. Choosing a better final winner cannot recover candidates absent from that shortlist.
- Uncapped low-confidence specialist domains account for 11/11/9 cases respectively. Both successes in every arm were explicit commit requests.
- The conservative cross-chunk disagreement guard caps confidence at .79. It suppressed high raw winners on 0/1/4 cases; only two of those winners were accepted specialist answers, both in the all-context arm. It also suppressed wrong answers. Independently normalized chunk confidence is not global certainty.
- Context introduced a subject-matter distraction on one control asking for an existing link, with no additional specialist successes. Confident process fallbacks did not satisfy the frozen specialist labels.

Detailed evidence and repeated-input variation are in context-results-analysis.md. New privacy-safe telemetry records each domain chunk's selected supplied skill and confidence, plus whether the existing conflict guard fired. It does not change routing decisions or log prompt text.

## find-skills and the old Antigravity path

The old path matches a local external catalog using keyword overlap and runs only when installed routing produces no announcement. A generic installed route can prevent discovery entirely. Its historical catalog was dominated by Antigravity; it was not a semantic JEV discovery system. See discovery-analysis.md.

The official find-skills workflow searches the skills.sh ecosystem. We tested an **opt-in advisory JEV reranker** over supplied, source-verified candidate descriptions. It keeps source-qualified external IDs, a none option and .8 threshold, excludes exact installed names, rejects oversized candidate pools, and returns a non-invokable candidate requiring review. It never searches, installs, invokes, or changes hook routing itself.

Six frozen smoke probes over three actual source-verified skills produced 6/6 expected selections/abstentions: three matches and three no-fit controls. Six attempts, no retries/cache hits/failures. This is a small functional check, not an end-to-end search benchmark or proof of broad discovery quality. Candidate retrieval and trust review remain separate; namespaced installed identities and short description representation need further work. See discovery-manifest.json, discovery-probes.json and discovery-run-1/.

75 total inference attempts across both experiments; one additional read-only model-listing request verified the existing credential. No external skills were installed.

## Decision and next experiment

Keep the production <=15-word context policy and .8 threshold. Keep the discovery command manual. Do not remove the .79 guard merely to increase coverage. A future bounded experiment should compare current folding against a second global adjudication over chunk winners plus none, keeping the same context policy and threshold; include repeated identical-state calls and a new holdout. That experiment has not been run or shipped.

## Verification

Full suite: 280 passed, three skipped, 142 subtests passed. Diff whitespace checks passed. Existing broader calibration/doctor failures were previously reproduced on baseline and remain outside this change; this is not a claim that the overall release gate is green. See ../jev-eval-2026-10-21/preexisting-checks.json. Install evidence is recorded separately in install-receipt.json.
