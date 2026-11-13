# Personal Preview 0.1 — Daily Workflow Report

## Git and scope

- Branch: `feature/personal-preview-0.1-daily-flow`
- Verified base: Task 06I remote tip `670f94c0360430efe43ba5896e793a0915f11be7`
- Implementation commit: `229992e656fd1047d3f0cf6216bacc4eec98bb81`
- Remote: pushed to `origin/feature/personal-preview-0.1-daily-flow`; remote tip verified at the implementation commit before this report update.
- Protected branches and the Task 06I base were not modified.
- The local-only Task 06D.3 UI was inspected, not merged or cherry-picked. This branch selectively adopts its clearer section hierarchy, status-first layout and restrained color treatment; its folder-first controls and broad UI changes were not ported.
- No packages or models were installed or downloaded. `vlc-help.txt` and the untracked workflow-spec note were preserved and are not part of this task's commit.

## Implementation

The main window now guides the user through one selected episode, explicit audio/caption selection, one **Generate Draft Shorts** action, and a provisional results area. File selection does not scan a directory or start work. Advanced processing settings open separately. Nightly queue controls remain available in their own window and keep their existing five-clip default.

The daily worker always passes a clip count of two to `process_video`; the existing nightly settings and runner are unchanged. Multiple audio tracks require a choice unless one English track is an unambiguous match for the default English transcription preference. A sole audio track is selected visibly. The chosen global FFprobe index and stream metadata are recorded; default disposition alone does not select a track. Whisper transcription, translation, embedded subtitles and no captions retain their distinct behaviors.

Each run gets a timestamp-and-UUID job folder under `Shorts_Output/jobs/`. MP4/SRT outputs and `analysis.json` are contained in that folder. A job report is written for completion, failure or cancellation; it records provisional status, selected source/audio metadata, actual transcription device/compute outcome, cache status, candidate/draft counts, artifact validation and failure details. A cancellation retains already validated outputs and does not auto-resume.

Persistent per-source caches live under `%LOCALAPPDATA%/Clipper/analysis_cache/<source-key>/`. `process_video` keeps its previous default behavior for queue and other callers; only Personal Preview supplies separate `cache_root` and `job_output_folder` arguments.

| Cache | Validation and reuse | Invalidated by |
| --- | --- | --- |
| `analysis_cache.json` | Existing schema and analysis fingerprint checks; reports `created` or `reused` | Source path/size/mtime, selected audio index, analysis schema/engine version |
| `transcription.json` | Existing schema-2 transcription fingerprint; model/device outcome remains recorded; reports actual hit or creation | Source path/size/mtime, model identity, language/task, selected audio stream, transcription settings |
| `editorial_cache.json` | Existing version and key validation; hit/miss counts are included | Analysis fingerprint, endpoint/model, candidate interval, transcript, prompt version |
| `semantic_cache.json` | Existing semantic schema/key validation; hit/miss counts are included | Source identity/stat, candidate interval, model/revision/prompt/frame schema, transcript/language |

Render outputs are never read from a previous job. Render-only changes do not enter the signal or transcription fingerprints. Interrupted-job resume is not advertised.

## UI redesign inspection note

The Task 06D.3 branch had a clearer muted palette, more deliberate grouping, and status/hardware details, but was built around folder processing and had diverged from Task 06I. The daily screen reuses the visual hierarchy and concise helper/status presentation only. Its queue is in a separate window; its old folder scan/processing layout was not carried into the primary workflow.

## Verification

- Focused Personal Preview, startup-safety, cache/selection and queue tests: **70 passed**.
- Full project suite: **203 passed, 0 failed** (248.617 seconds).
- Python compilation: **passed** for `main.py`, `clipper.py`, and the new test module.
- `git diff --check`: **passed**.
- GUI smoke check: the Personal Preview window launched successfully without starting processing. Automated GUI tests cover idle launch, file selection, explicit worker start and track selection.
- Synthetic FFmpeg rendering remains covered by the existing full suite. Cache tests verify shared validated signal caches with separate job reports; transcription fingerprint tests verify source and stream invalidation.

## Manual media verification and limits

No source episode was processed in this task. Actual stream playback, Whisper device/fallback on the selected episode, cancellation during real inference/rendering, MP4 playback, SRT content/timing and repeat-run cache hits on real footage are **not verified**. No automatic highlight quality claim is made. The GPU status shown during a run is populated from the Whisper manager callback, not inferred from NVIDIA hardware detection.

## Acceptance status

| Check | Status | Evidence |
| --- | --- | --- |
| Branch based on verified Task 06I tip | PASS | Base SHA recorded above; work began at that exact commit |
| Daily UI is one-episode and simple | PASS | GUI smoke check and source review |
| Startup/file selection remain idle | PASS | `tests.test_personal_preview` and startup-safety suite |
| Explicit Generate action starts processing | PASS | Mocked worker-start test |
| Audio choice and global stream index | PASS | Ambiguous, sole-track, and English-preference tests |
| Daily clip count capped at two; nightly unchanged | PASS | Worker-boundary and nightly regression assertions |
| Unique outputs and completed output retention | PASS | Isolated job-worker test |
| Persistent cache/output separation | PASS | Two-job cache integration test |
| Cache invalidation/reuse reporting | PASS | Existing fingerprint tests and new cache status test |
| Provisional job reporting | PASS | Worker report test |
| Cancellation on actual source media | NOT VERIFIED | No source episode processed |
| MP4/SRT manual review on actual source | NOT VERIFIED | No source episode processed |
| Focused and full test suites | PASS | 70 focused; 203 full, 0 failed (248.617 seconds) |
| Protected branch verification | PASS | Only the new feature branch is checked out/changed |
| Feature commit and remote push | PASS | Implementation commit pushed; remote branch tip verified at `229992e656fd1047d3f0cf6216bacc4eec98bb81` |

## Next task

Personal Preview 0.2 should wait until one real episode validates the 0.1 job lifecycle, artifact links, and persistent cache behavior. That real-media check does not establish editorial quality; the human benchmark remains a separate gate.
