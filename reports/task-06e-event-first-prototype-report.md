# Task 06E — Event-first prototype and narrative moment review

## Result

The Task 06E offline prototype is implemented on a separate branch from the exact Task 06D.1 baseline. It turns cached transcript and activity evidence into timestamped reviewable event proposals, compares them with the five existing production selections, and creates a local review board. The production selector and its decisions were not changed.

This is **not editorial-quality acceptance**. No human labels exist for this set, and no visual-language model ran. The system reports observable structure and evidence, keeps categories `unknown`, and leaves editorial assessment `NOT_ASSESSED`.

## Baseline and scope

- Baseline commit: `91cf3934131d0ff42a428ce010391c9627da7de0` (Task 06D.1).
- Work branch: `feature/task-06e-event-first-prototype`.
- Protected branches (`main`, Task 06B backup, Task 06C, Task 06D and Task 06D.1) remain unchanged.
- The existing cached S01-E01 Task 06C run was used. No full episode pipeline or production render was run.
- No package, model, CUDA runtime, or other dependency was installed or downloaded. No cloud endpoint or semantic model was used.
- Original video and existing Task 06C output were left untouched. New review artifacts are local and gitignored.

## Baseline findings

The cached episode is 1,422.59 seconds long and has 2,089 word timestamps, 387 scene cuts, and cached one-second motion/audio signals. The old pipeline produced 1,660 candidate proposals, 1,709 rejections, and five selected clips. Semantic analysis was disabled; the narrative endpoint was unavailable and zero editorial assessments were recorded. All five clip categories were `activity-only`.

| Existing clip | Interval | Old signal score | Transcript words | Speech coverage | Largest speech gap | Review concern |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| 001 | 237.230–281.060 s | 0.7819 | 38 | 24.69% | 27.14 s | Sparse dialogue islands; terminal punctuation warning |
| 002 | 637.600–683.308 s | 0.7727 | 28 | 17.41% | 37.75 s | Sparse transcript and long gap |
| 003 | 878.260–923.422 s | 0.7693 | 80 | 59.78% | 15.59 s | Contains a question/announcement exchange; still no editorial assessment |
| 004 | 1092.341–1140.514 s | 0.8152 | 51 | 38.69% | 23.93 s | Multiple speech islands; terminal punctuation warning |
| 005 | 1292.360–1342.841 s | 0.7863 | 7 | 6.62% | 43.08 s | Very sparse transcript, high activity, near episode end |

These coverage and gap values are review evidence, not a verdict that a clip is bad. Existing subtitle excerpts include several phrases that continue in following subtitle cues; no full playback audit established whether those clip boundaries cut dialogue, so the board flags evidence for review rather than asserting a confirmed cut.

## Event-first prototype

`evaluation/event_engine.py` builds a one-second signal timeline, groups timestamped ASR words into speech beats and connected turns, and preserves scene-change timestamps without treating cuts as story boundaries. It recognizes limited transcript structures such as a question followed by another speech turn or an explicit reveal phrase followed by a possible reaction. Those are structural text cues only. Motion-only windows remain unclassified visual activity, not “action.” Candidate scores are named signal-evidence scores and are not editorial-quality scores. No number of clips is forced.

Each proposal includes natural transcript/activity boundaries, beat timestamps, transcript excerpt, hook/context/escalation/payoff evidence where available, motion/audio/scene evidence, speech coverage and gap, and review warnings. Generic opening/end timing warnings and transcript cues can flag titles/credits for human review; they do not use hardcoded episode timestamps. The provider mock is test-only and explicitly reports that it did not inspect pixels.

The generated set contains **30 proposals**. The review board preserves all five old selections and adds **27 non-overlapping event proposals**; three event proposals overlapping an existing clip at the configured threshold were skipped. It spans the episode and provides three timestamp-labeled representative stills and a short preview per card. No annotations are prefilled. The existing dataset schema and save/import safeguards are reused; a rebuild preserves matching human annotations and refuses to overwrite annotations if the source or candidate intervals changed.

Generated files are under:

`Shorts_Output/Task06C-20261113-095427/_S01-E01_Sword_Art_Online_1080p_Dual_Anime_x_Clan_fbfef207/candidate_review_06e/`

Open `index.html` for the board. It includes the five baseline cards plus the 27 new proposals; `event_timeline.json`, `event_proposals.json`, and `old_vs_event_comparison.json` preserve the machine-readable evidence. The dataset currently contains **zero human annotations**. These user-media previews and datasets must remain untracked.

## Environment and model feasibility status

The Task 06D inspection recorded Windows 11, Python 3.14.7 64-bit, 15.71 GiB visible system RAM, and an RTX 3050 Laptop GPU with 4,094 MiB dedicated VRAM (2,653 MiB free at that inspection). CTranslate2 4.9.0 detected CUDA. PyTorch, Transformers, Accelerate, Pillow and the pinned SmolVLM2 weights were absent from the active environment/cache; `CLIPPER_LOCAL_LLM_ENDPOINT` was empty.

Accordingly, Task 06E did not install dependencies, download weights, run SmolVLM2/Moondream, measure model inference, or measure peak VRAM/RAM. The previously recorded metadata-only SmolVLM2 snapshot estimate is 2,034,845,165 bytes (about 1.89 GiB of files and about 2.39 GiB free-disk preflight including cache headroom). The model card's 1.8 GB video-inference VRAM figure is a published claim, not a measurement on this machine; with only 2,653 MiB free at the earlier inspection, GPU feasibility remains uncertain. Moondream 3.1 was not installed or tested and has no verified fit measurement for this 4 GiB GPU. These are prior Task 06D observations, not a new hardware/model benchmark.

## Validation

- Full suite: `.venv\Scripts\python.exe -m unittest discover -s tests -v` — **141 passed, 0 failed** (32.398 seconds).
- Task 06E focused suite: event engine and review-board preservation tests — **16 passed** in the prior focused run; final full suite also includes the beat/event ID consistency assertion.
- Python compilation: `.venv\Scripts\python.exe -m compileall -q evaluation tests` — passed.
- `git diff --check` — passed.
- Cached review board: 32 cards (5 baseline + 27 event proposals), 30 raw proposals, 3 overlaps skipped, 96 timestamped frames. The generated preview files were individually revalidated with the existing preview validator.
- Event generation from cached JSON took 0.718 seconds; `tracemalloc` measured 0.817 MiB peak Python heap for that operation. This excludes input JSON/object memory, native libraries, OpenCV, and process RSS.
- No browser interaction test, full-length human playback review, VLM inference, GPU/RAM inference benchmark, or full episode reprocessing was performed.

Expected FFmpeg `moov atom not found` messages occurred in existing corrupt-media test fixtures; the full suite still passed.

## Changed files

- `evaluation/event_engine.py` — offline event/timeline generation and baseline interval evidence audit.
- `evaluation/build_event_review.py` — cached-input comparison and local review-board builder with preview generation and annotation preservation.
- `evaluation/review_board.html` — event evidence summary, explicit score meaning, baseline distinction, and frame timestamps.
- `tests/test_event_engine.py` — structural evidence, uncertainty, boundaries, and activity-only cases.
- `tests/test_event_review_board.py` — annotation preservation and mismatch refusal.
- `reports/task-06e-event-first-prototype-report.md` — this report.
- `reports/IMPLEMENTATION_STATUS.md` — roadmap status.

## Acceptance and limitations

This milestone provides a reviewable alternative representation of complete transcript/signal moments and exposes weaknesses in the five old selections. It does not prove that the new proposals are more interesting or coherent. Anime action, comedy, emotion, and visual events cannot be semantically identified from the current signals alone. ASR errors, translated dialogue, music, and non-speech events can also mislead heuristics. Human review and labels are required before quality metrics or editorial acceptance can be reported. The production pipeline remains activity-based and unchanged.

## Git publication

- Implementation commit: `c329df1f6e5f750ca48f211204ddf51b5013d021`.
- Branch: `feature/task-06e-event-first-prototype`.
- Push: verified on `origin` at the implementation commit before this publication note was added.
- Protected remote refs were checked before work and remained at their recorded baseline commits. No protected branch was pushed or modified.
