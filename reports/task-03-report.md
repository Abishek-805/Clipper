# Task 03 — Intelligent variable-duration clip selection

## Summary

Replaced the five-second fixed-length scan with separate scene, activity, and dialogue candidate proposal paths, followed by boundary refinement, feature scoring, overlap removal, and temporal-diversity selection. Existing callers of rank_clips() still receive a list of selected clip dictionaries; process_video() now uses the detailed select_clips() result for its manifest and progress message.

No semantic model, cloud API, or new package was added.

## Algorithm changes

- Scene proposals snap configured durations to nearby scene boundaries and combine adjacent short scenes without enumerating every cut pair.
- Activity proposals use robustly normalized per-second motion, audio energy, and speech signals. Sustained regions propose multiple window sizes; isolated audio peaks are recorded as candidates but are not sufficient by themselves to win selection.
- Speech is grouped by sentence punctuation and pause gaps. Dialogue candidates accumulate adjacent units with 2.5 seconds of context, then boundaries may snap to sentence, word, or nearby scene boundaries. Context margin, minimum temporal distance, and scoring weights can be overridden by the selection functions.
- Candidate features record average/peak motion, motion consistency, average/variable audio energy, speech density, dialogue completeness, scene transition quality, boundary confidence, silence/static penalties, and scene count. Percentile-based normalization and explicit weights keep one extreme value from controlling the score.
- Selection removes candidates above 0.35 interval IoU, applies a minimum score, and rejects selected intervals that overlap or fall closer than the configured separation. It returns fewer than requested when remaining candidates do not meet the quality threshold.
- Sources shorter than the configured minimum are skipped and the reason is recorded rather than producing an invalid clip.
- A source-stat fingerprinted analysis_cache.json preserves motion/audio/scene arrays between runs when clip settings change. Existing manifests remain readable; the candidate-engine setting causes completed legacy outputs to be regenerated once using the new selector.
- Existing GUI status reporting now includes proposal count, selected count, and selected clip durations.

## Verification

- Focused candidate-selection suite: **28 passed** (corrected from 24 in the initial report).
- Full suite: **54 passed, 0 failed, 0 skipped** (project virtual-environment unittest discovery), including Task 01 rendering, Task 02 model management, startup safety, the legacy manifest path, and analysis-cache reuse.
- git diff --check: passed.
- Synthetic benchmark: 34 unique proposals, 30 rejected, 4 selected.

## Synthetic benchmark

The deterministic fixture contains motion-heavy, audio-heavy, dialogue-heavy, and silent-visual events, multiple overlapping proposals, and a long inactive interval. Results are in [task-03-benchmark.md](task-03-benchmark.md) and [task-03-benchmark.json](task-03-benchmark.json).

| Metric | Result |
| --- | ---: |
| Candidate recall | 1.0000 |
| Selected interval precision | 1.0000 |
| Mean best temporal IoU | 0.9958 |
| Selected duplicate rate | 0.0000 |
| Average clip duration | 27.750 seconds |
| Invalid boundaries | 0 |
| Signal-selection runtime | 0.004974 seconds for 34 proposals (6,834.9 proposals/second) |

These are synthetic signal-fixture metrics. They do not establish real-world scene understanding or entertainment quality.

## Performance and limits

The selector operates on the existing per-second analysis arrays and transcript word timings. Scene proposal endpoint search uses binary search and only a small duration set per scene start. Motion/audio/scene arrays are cached for re-runs when only clipping settings change. The benchmark runtime covers in-memory synthetic selection only; it excludes video decoding, Whisper, scene detection, and rendering.

No real movie, episode, or approved five-minute speech sample was processed for this task. The complete video pipeline timing and real-world clip quality remain unmeasured. Activity measurements are per-second and heuristic. Dialogue completeness means timestamped phrase coverage, not semantic sentence correctness. No emotional, comedy, or narrative labels are inferred.

## Files changed

- clipper.py
- tests/test_clipper.py
- tests/test_candidate_selection.py
- benchmark_candidates.py
- reports/task-03-benchmark.md
- reports/task-03-benchmark.json
- reports/task-03-report.md
- reports/IMPLEMENTATION_STATUS.md
- README.md

## Git

- Task 02 preservation checkpoint: 677090f is already pushed to origin/main.
- Task 03 implementation checkpoint: ec46cb9 (Implement Task 03 variable clip selection), on main. This report is added in the follow-up reporting commit; both commits are pushed to origin/main together.

Task 04 was not started.
