# Sprint 3A — Structure exclusions and dialogue source

2026-11-14 · Branch `feature/personal-v1-understanding` · Verified parent `298f70a9e970d2744d0ef1243f363c21dd08739f` (daily-ready remote tip; approved daily baseline is its ancestor).

**Delivered:** evidence-backed zones, reversible episode overrides, existing-selector filtering, release-subtitle assessment/caption option, and deterministic structural scene/utterance/shot artifacts. No model calls/downloads or full-episode analysis/rendering were run. Manual intervals remain independent of automatic filtering.

| Phase | Result | Commit | Verification |
| --- | --- | --- | --- |
| 0 | Actual baseline; read-only FFprobe/temporary ASS extraction | Parent above | 246 passed; 9 subtests; 0 failed/skipped |
| 1 | HARD / VERIFIED / SOFT detection and atomic overrides | `a2cbf2b` | 253 passed; 9 subtests |
| 2 | Filter before ranking and after boundary refinement; visible zones, legacy switch | `74c83c7` | 257 passed; 9 subtests |
| 3 | Subtitle classification, optional release captions, source units | `b728b52` | 265 passed; 9 subtests |
| Validation | Conservative chapter support, saved warnings, source-safe edits | `5c903f1` | **267 passed; 9 subtests; 0 failed/skipped**, 60.65 s |

**S01-E01 evidence:** duration 1422.590 s; six chapters. Explicit ED **1306.013–1395.978** is HARD. Eight overlapping VERIFIED style spans are contained in that ED; their release style names misleadingly say “Opening.” These are corroborating layers, not eight separate opening sequences. Two SOFT position priors cover 0–240 and 1242.590–1422.590. Ambiguous “Intro,” “Part A/B/C,” and “Next Time” titles do not establish hard exclusions. No music/repeated-sequence classifier was added.

**Copied-cache selector comparison** (same 1814 proposals, 35–60 s, context 4 s):

| Draft | Unfiltered source seconds | Filtered source seconds | Evidence |
| --- | --- | --- | --- |
| 1 | 237.270–276.680 | 637.820–683.308 | Old interval only touches SOFT early prior (6.9272%); not proven song |
| 2 | 1092.341–1137.670 | unchanged | Outside zones |

186 structure rejections; selection took 1.517 s unfiltered / 1.702 s filtered. SOFT candidates remain eligible if no unflagged alternative exists; exclusions never refill a quota. This is technical selection evidence, **not editorial acceptance**.

**Subtitles:** ASS 3/5 are tagged `eng` but predominantly signs/songs (default only on 3; all forced flags false). Tracks 4/6 are tagged `jpn` yet contain English-looking dialogue (327/322 usable cues). Neither is silently trusted as English. Plot-source fallback is cached Whisper; captions remain Whisper by default. Optional release captions require one unambiguous English dialogue track, preserve cue timing without inventing word alignment, apply the glossary, and survive trim-only rerenders using saved cues.

**Units:** 39 structural scenes, 315 utterances, 388 shots; IDs and artifact reuse verified. Final fresh inspection 3.481 s; units/write/reuse checks 0.212 s. Keys include full source SHA-256, audio, dialogue source, boundaries/content and version. Original transcription/analysis caches are hash-unchanged. Override JSON supports add/edit/suppress; remove manual zones by removing their add record. Raw detection stays intact; applied overrides are copied into new jobs.

**Browser:** 14 Playwright checks passed, zero page/console errors and zero processing requests; separate CLI inspection/save/removal exercised zones against the real episode in an isolated sandbox. Screenshots: ignored `output/playwright/`; no capture feature added. One intermediate Windows socket failure on the retired picker route was fixed by draining its small request body. Compilation and diff checks passed. Detailed chapter/style counts, flags, timings and hashes: [evidence JSON](understanding-3a-evidence.json).

**Delivery:** implementation SHA `5c903f1eead6bb7f11e93fe42bb0218de70f9207` was pushed and matched `git ls-remote`; this report is a subsequent documentation commit on the same branch. Other branches, media, completed jobs, models and untracked `vlc-help.txt` were preserved. Launch with `run.bat` after stopping the older server, then inspect the episode and open **Episode exclusions**. The active owner server was not restarted. Human known moments remain unfilled; no listening, synchronization or editorial-quality acceptance is claimed. Sprint 3B is deferred.
