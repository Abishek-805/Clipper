# Task 06D.1 — Review board deduplication and audio

## Result

**Complete.** The corrected board contains 36 temporally distinct candidate moments selected from the existing cached Task 06C analysis. Previews now include only the manifest-selected English audio stream when available. Production candidate scoring and selection remain unchanged. No full-episode pipeline was rerun, and no package or model was installed or downloaded.

## Protected baseline and branch

- Baseline: Task 06D commit `3cf88adad32e2fba9df303379d14851506cff0f9` on `feature/task-06d-multimodal-evaluation`.
- Work branch: `feature/task-06d1-review-board-quality`, created at that baseline.
- Task 06D, Task 06C, `main`, and Task 06B backup branches were not modified.
- The original `candidate_review_06d` dataset/board was left intact. Corrected artifacts were generated separately under `candidate_review_06d1`.

## Candidate clustering and review-set coverage

The sampler uses deterministic temporal non-maximum suppression. Two proposals cluster when temporal IoU is at least **0.60**, or when intersection covers at least **80% of the shorter proposal** and the closer start/end boundary is within `max(3 seconds, 10% of the shorter duration)`. The second condition groups highly contained alternate boundaries; nearby but separate intervals without substantial overlap remain distinct. Invalid or non-positive intervals are excluded before clustering.

Representative preference, in order: preserve an existing selected candidate; prefer valid and complete boundaries; prefer stronger dialogue completeness and boundary confidence; then higher Task 06C score; then earlier time and candidate ID for deterministic ties. Each representative retains every cluster member's candidate ID, start/end, sources, score and selected/valid status. The board displays cluster size and alternate timings.

For the cached S01-E01 manifest:

| Count | Result |
| --- | ---: |
| Raw Task 06C proposals | 2,461 |
| Valid proposals | 2,137 |
| Invalid proposals excluded | 324 |
| Unique temporal clusters after NMS | 128 |
| Duplicate proposals removed into clusters | 2,009 |
| Duplicate-containing clusters / singleton clusters | 122 / 6 |
| Final review cards | 36 |
| Final cards representing existing selected candidates | 5 of 5 |
| Old 36-card Task 06D review set, unique under the same rule | 16 |

The final cards cover all six equal episode regions: **7, 5, 6, 5, 6, 7** from opening through ending. Each final pair falls below the duplicate threshold. These are heuristic review strata, not semantic labels or a claim that every sampled moment is interesting.

## Audio previews

The analysis manifest selected **global stream index 2, language `eng`**. FFmpeg now explicitly maps `0:v:0` and that selected global index, encodes audio as AAC, disables subtitle/data streams and strips chapters/metadata that otherwise caused Matroska chapter data to appear as an extra MP4 stream. If the selected track is absent or cannot produce a valid preview, the card is marked audio-unavailable and the preview is rendered video-only; the code does not substitute another language.

All 36 S01-E01 previews passed FFprobe validation:

- Exactly one H.264 640×360 video stream at 12fps and one AAC audio stream.
- Audio language tag is English (`eng`); no subtitle, data or other streams.
- Preview duration is at most 8.001 seconds (the command cap is 8 seconds; measured maximum 8.000333 seconds).
- Maximum video/audio start-time difference: **0.000 seconds**.
- Maximum video/audio duration difference: **0.001167 seconds**.
- Browser video controls remain enabled, previews do not autoplay, and the reviewer can mute/unmute in the native controls.

The board shows per-card audio availability and selected source stream/language. Annotation drafts remain local-storage autosaved; import/export is preserved, imports verify source fingerprint and exact candidate interval, and the page reminds reviewers to export the dataset periodically. The local-storage namespace was advanced so old Task 06D cards cannot populate these changed candidate IDs/timings.

## Tests and checks

- Full suite: `.venv\Scripts\python.exe -m unittest discover -s tests` — **125 passed, 0 failed**.
- Task 06D evaluation tests cover exact/partial duplicates, close non-overlapping events, cross-source-type clusters, representative priority and deterministic output, six-region sampling, invalid-boundary exclusion, preview mapping, selected language, stream validation and A/V synchronization.
- Cached-board verification: 36 unique moments, all five selected candidates preserved, all six regions covered, 36/36 previews with selected English audio and no extra streams.
- Browser JavaScript parsed with Node 24.19.0; import/export/draft/audio UI fields checked in generated HTML.
- `.venv\Scripts\python.exe -m compileall -q evaluation tests` — passed.
- `git diff --check` — passed.
- No full episode processing, VLM inference, package installation, or model download.

## Files changed

- `evaluation/anime_review.py`: deterministic overlap clustering and cluster-member provenance before regional sampling.
- `evaluation/build_review_board.py`: selected-stream audio mapping, metadata/chapter stripping, silent fallback, FFprobe stream/language/sync validation.
- `evaluation/review_board.html`: audible-review metadata, cluster alternatives, export reminder, import timing verification, draft namespace separation.
- `tests/test_task06d.py`: clustering, representative, regional sampler, stream mapping and preview validation coverage.
- `reports/IMPLEMENTATION_STATUS.md`: Task 06D.1 status.

## Output

The existing output was not overwritten. The new review board and dataset are in:

`Shorts_Output/Task06C-validation-20261112-221019/_S01-E01_Sword_Art_Online_1080p_Dual_Anime_x_Clan_fbfef207/candidate_review_06d1/`

Open `index.html` to review and listen, then export the JSON dataset for a portable annotation backup. The 36-card dataset currently has zero human annotations; this correction improves reviewability but does not itself establish editorial benchmark quality.

## Git publication

- Branch: `feature/task-06d1-review-board-quality`
- Baseline: `3cf88adad32e2fba9df303379d14851506cff0f9`
- Implementation commit: `8fe1fad` (`Deduplicate anime review board and include audio`), pushed to `origin/feature/task-06d1-review-board-quality`.
- The report update records the final commit after its push; protected refs were checked unchanged: Task 06D `3cf88adad32e2fba9df303379d14851506cff0f9`, Task 06C `b9b07d410f0cdcaceeb4456a3feaabc543b08a45`, `main` `1fe43b76de7c20930c34f36c6ea86696fd72c695`, and Task 06B backup `37e6bcbcb7f9c220a265c2215fdf2709ea2323f0`.
- Task 06E and Task 07 have not started.
