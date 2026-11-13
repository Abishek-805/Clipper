# Task 06I — Human Review Workspace

## Outcome

Implemented the local, two-pass human review workspace on `feature/task-06i-human-verified-benchmark`. The production clipping GUI, selector, renderer, model integrations, queue and Task 06H graph were left unchanged.

The reviewer can select one local episode, play it inside Tkinter through LibVLC, mark moments using the keyboard, track conservatively watched coverage, refine only their own moments, and export a validated JSON dataset. SQLite stores the session locally and resumes only when the same source is selected.

**Implementation is complete. Playback acceptance is partial, and the human benchmark is not complete.** The user still needs to watch the full episode, refine their own annotations, and export the human reference before Task 06J.

## Baseline and branch safety

- Repository: `Abishek-805/Clipper`
- Branch: `feature/task-06i-human-verified-benchmark`
- Task 06H remote tip at fetch: `2af25657909d641565745f1247c4c8b884ade6fa`
- Expected graph implementation `1b6e48edb96830b0b5b4747a052d87b4c33ae143` is an ancestor of that tip.
- Task 06I implementation commit: `23b08de8c368ecee32228db6e356366d39184c34`
- Implementation and report are committed and pushed only on `origin/feature/task-06i-human-verified-benchmark`; the remote tip was verified against the final local branch head.
- No protected branch was checked out or changed. Task 06D.3 was not merged or cherry-picked.
- The pre-existing untracked `vlc-help.txt` remains untracked and was excluded from commits.

## Implementation

- `requirements-review.txt` pins optional `python-vlc==3.0.21203`; the main application does not import it during normal startup.
- `review_workspace/player.py` discovers the installed 64-bit VLC runtime, checks architecture, loads native LibVLC lazily, probes FFprobe metadata, and enumerates LibVLC track IDs and descriptions separately from FFprobe stream indexes.
- `review_workspace/window.py` provides an embedded Tkinter player, seek controls, track selection, keyboard markers, blind chronological Pass 1, human-only Pass 2, coverage display, autosave, and JSON export.
- `review_workspace/coverage.py` counts only adjacent samples consistent with continuous playback. Pause, buffering, seeks, large polling gaps, backward movement and timing discontinuities break continuity.
- `review_workspace/store.py` stores annotations in SQLite and validates portable JSON before atomic export. Source fingerprints are SHA-256 hashes of file contents, computed in chunks.
- `run-review.bat` launches the isolated workspace from the project virtual environment.
- Human data stays in ignored `Shorts_Output/Task06I_<fingerprint>/human_review.sqlite3` directories. The Task 06H graph and earlier AI artifacts are not inputs to this module.

## Runtime and manual playback evidence

Verified on the current Windows machine:

- Python 3.14.7, 64-bit.
- `python-vlc` 3.0.21203.
- Installed native VLC/LibVLC 3.0.23, 64-bit; native runtime initialization succeeded.
- Selected source: `[S01-E01] Sword Art Online [1080p] [Dual] @Anime_x_Clan.mkv`, 23:42.590, 1920×1080 HEVC.
- The embedded video rendered visible source frames at all three required seek locations.
- LibVLC exposed an English-labeled track, and the workspace selected it by description. Audio was not independently heard or checked for synchronization, so English playback and A/V sync acceptance remain pending.

| Requested | Player-reported after settle | Offset | Visible video |
| --- | ---: | ---: | --- |
| 00:30.000 | 00:30.140 | +140 ms | Yes |
| 10:00.000 | 10:00.168 | +168 ms | Yes |
| 20:00.000 | 20:00.143 | +143 ms | Yes |

At the source's 23.976 fps, a frame is approximately 41.7 ms. The observed time offsets are larger than one frame, so the player is proven to seek to the correct visible scene neighborhood, but frame-accurate seeking is not established. These readings are millisecond reports from LibVLC, not a guarantee of frame precision.

An external Windows desktop capture of the loaded review workspace is at [task-06i-human-review-workspace.png](C:/Users/ashek/.codex/visualizations/2026/11/13/task-06i-human-review-workspace.png). It was captured outside Clipper; no screenshot or desktop-capture feature was added to the application.

## Local annotation state

The current content-hash session is `Shorts_Output/Task06I_28982060e5dc/human_review.sqlite3`: **0 moments and 0 coverage intervals**. No annotations were created during playback verification, no JSON benchmark was exported, and no human episode review was performed.

An earlier local QA database created before the content-hash correction remains preserved at `Shorts_Output/Task06I_301fa164e318/human_review.sqlite3`. It contains **0 moments and 1 coverage interval** from setup/playback checks. The workspace now uses the actual file-content SHA-256 so this QA interval does not carry into the clean review session.

## Validation

- Focused Task 06I suite: **22 passed**.
- Full project suite: **196 passed, 0 failed**.
- Python compilation: `python -m compileall -q review_workspace tests/test_task06i_workspace.py` passed.
- `git diff --cached --check`: passed for the implementation commit; final documentation checks are recorded in the publication update.
- No full-episode analysis, transcription, inference, clip rendering, or model downloads were run.

The Task 06I tests cover millisecond parsing, content fingerprints, LibVLC lifecycle and invalid time values, seek return behavior, audio/subtitle track mapping, conservative coverage, pause/seek/buffering gaps, SQLite save/resume/source mismatch, JSON validation/export, blind module isolation, and keyboard shortcut suppression while a text entry has focus.

## Fifteen implementation gates

| # | Gate | Status | Evidence / limit |
| ---: | --- | --- | --- |
| 1 | Branch from verified Task 06H tip | PASS | Exact remote tip verified; expected graph commit is its ancestor. |
| 2 | Optional Python binding and native runtime | PASS | Python 3.14.7 x64, python-vlc 3.0.21203, native VLC 3.0.23 x64 loaded together. |
| 3 | Embedded video playback | PASS | Actual HEVC MKV rendered inside the review window. |
| 4 | English audio selection verified | PARTIAL | LibVLC English-labeled track selected; listening and synchronization were not independently confirmed. |
| 5 | Playback at 00:30, 10:00, 20:00 | PARTIAL | Correct scene neighborhood shown at each point; reported offsets exceeded one frame. |
| 6 | Millisecond markers saved and reloaded | PASS (automated) | Timestamp round trip and SQLite save/reload covered by tests; no human marker was added during manual review. |
| 7 | Coverage excludes pauses, seeks and uncertainty | PASS (automated) | Coverage tests cover pause, buffering, discontinuity, large sample gap, seek and out-of-order movement. |
| 8 | Blind isolation | PASS (automated) | Lazy import and forbidden AI/recommendation imports are checked; review code has no such dependencies. |
| 9 | Pass 2 refinement available | PASS (implemented) | Human-created moments only; boundary, quote, context, decision, ratings and ranking fields are provided. Full user flow awaits human moments. |
| 10 | SQLite autosave and recovery | PASS (automated) | Save, close, reopen and source-mismatch tests pass. |
| 11 | Portable JSON export validates | PASS (automated) | Schema and atomic export tests pass; no human dataset export exists yet. |
| 12 | Focused and full suites | PASS | Focused 22/22; full 196/196. |
| 13 | Screenshots captured outside Clipper | PASS | External Windows capture saved in Codex visualizations; no capture feature is present in Clipper. |
| 14 | Report and implementation status updated | PASS | This report and `reports/IMPLEMENTATION_STATUS.md`. |
| 15 | Task 06I branch committed and pushed | PASS | Only the Task 06I feature branch was published; remote tip verification matched the final local branch head. |

## Launch

1. Run `run-review.bat` from the repository root. The project `.venv` must exist.
2. If needed, install the optional binding inside that environment with `.venv\Scripts\python.exe -m pip install -r requirements-review.txt`.
3. The 64-bit VLC desktop runtime and plugin directory must be installed on Windows. The workspace reports a clear error if native LibVLC cannot be found.
4. Choose one episode. Press Play, choose the desired LibVLC audio track, and review chronologically. `I`/`O` mark intervals; `M` marks a point; `R` replays the latest interval; Space toggles playback. Text fields suppress these shortcuts.
5. Pass 1 requires all uncovered intervals to be watched. Pass 2 becomes available after coverage is complete and contains only human-created moments.

## Remaining acceptance work

The selected MKV still needs a human listening check for English audio and A/V synchronization. Seek readings were within roughly 0.17 seconds of the requested locations but did not meet a one-frame tolerance. Manual marker capture/reload, Pass 2 interaction with real moments, and a real human JSON export remain part of the user's future review, not claimed as completed benchmark work.

No Task 06J work has started.
