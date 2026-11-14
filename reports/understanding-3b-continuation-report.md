# Sprint 3B continuation — revised 2B gate failed; reference review delivered

2026-11-14 · Branch `feature/personal-v1-understanding-3b` · Verified baseline `0921111677c788a9602a30e0c20df684ef2034b4`.

| Phase | Status | Commit | Tests / result |
| --- | --- | --- | --- |
| A — Validator and saved-response re-score | Done, zero model calls | `31b2e71` | 282 tests + 9 subtests |
| B — Revised 2B experiment | Measured; **gate failed** | `38a2a9e` | 285 + 9; same 32 dialogue scenes |
| C — Timed owner references | Done | `5359794` | Final **293 + 9**, 22 browser checks |
| D — New nominations/action/selector | Skipped as required | — | B did not pass |
| E — Comparison and report | Done | This report | Two owner-confirmed intervals checked |

**Validator:** case/whitespace/dash normalization is recorded; generic roles are separated from proper names. Glossary/provided names are allowed. Both raw runs were preserved and re-scored without inference using final v2.1, including corrected prepositional-object/quotation false positives. Attribution/relationship checks are conservative literal rules, not semantic truth checks.

| Check | Original raw audit | Original re-score | Revised run |
| --- | --- | --- | --- |
| Valid JSON / IDs | 100% / 100% | 100% / 100% | 100% / 100% |
| Unsupported names | 43 unmatched labels | 0 proper names; 43 GENERIC | 0 outside allowed list |
| Quote mismatches | 1/96, formatting | 0/96 | 2/56 |
| Attribution / relationship response flags | Not checked | 4 / 4 | **14 / 1** |
| Responses passing all applicable checks | 7/32 | **25/32** | **11/32** |

Ten revised responses exceed 20 words. `Initial asks`, `Right asks`, and `Klein says` illustrate inappropriate speaker assignments. All **21 failing scene IDs and offending text** appear in [evidence JSON](understanding-3b-continuation-evidence.json). B adds word/tone/name-selection constraints; pass counts are not general accuracy, and no invented-fact rate is claimed.

**Gate failed:** required ≥28/32 and zero attribution/relationship flags. The 2B setup is **not accepted for nominations**. One offline rerun used installed b12284/Q4_K_M, temperature 0, 4096 context, one slot, no projector. Elapsed **75.069 s**, initialization 2.578 s; per-scene latencies are in evidence. Peak GPU use: **2734 MiB system-wide**, including other applications. Thinking was disabled in request/template kwargs and server reasoning budget 0; no reasoning fields/tags appeared. Server stopped; no download or prompt retry occurred.

**Owner-confirmed references:** game-master rules **727.510–776.670 s** and training activation **207.590–249.020 s**, explicitly confirmed in chat. Six text-based proposals were generated; the other four remain PROPOSED. The browser offers landscape proxy playback, time edits, Confirm/Reject, persistent source-bound JSON and reopening. Confirmation tests use synthetic fixtures; browser tests send no annotation actions. A real 49.167 s preview played and sought successfully with stream 2/AAC; audible English/perceptual sync remain owner-listening checks.

**Spot-check on copied signals:** using the matched legacy 35–60 s/context-4 settings and IoU ≥0.30 as the found threshold, unfiltered and current 3A selectors each missed both confirmed windows. The unfiltered tutorial draft partly overlaps the training reference (IoU 0.170); other overlaps are zero. Selected times, reasons and zone checks are in evidence. The new selector comparison is **NOT RUN—gate failed**, not scored as a miss. This covers only two personal references and is not editorial validation or completion of Task 06I.

Source, original responses and signal/transcription caches are hash-unchanged; other branches, jobs, models and `vlc-help.txt` are preserved. Full suite: **293 passed + 9 subtests, 0 failed/skipped**, 55.51 s; compilation/diff checks passed. Implementation `5359794110bb0d5aec963ccb739039ec9cdce808` was pushed/remote-verified; this documentation follows. No final Shorts or full-episode analysis ran. A separate 4B benchmark still requires owner approval.
