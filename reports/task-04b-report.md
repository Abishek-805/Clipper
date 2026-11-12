# Task 04B — Nightly queue and bounded processing

## Implementation summary

Clipper now stores episode and session progress in `%LOCALAPPDATA%\Clipper\queue.sqlite3`, outside the video source folder. SQLite runs with WAL, full synchronous writes, foreign keys, a versioned schema, transactions, and a unique active-session index. A live-process check prevents a second app instance from recovering a session owned by another running process.

The queue keys episodes by normalized absolute path and fingerprints file size, modification time, plus the first and last 64 KiB. This is fast for large files, but a same-size edit in the unsampled middle with a restored modification time can evade detection. A detected change moves the source to `SOURCE_CHANGED`; the GUI requires a separate **Reprocess Changed** confirmation. Old interrupted-job JSON entries are imported into the database without starting work.

Episode states cover pending, analysis, analyzed, ready to render, rendering, validation, completed, interrupted, retryable/final failure, skipped, and source changed. Unreadable-media errors with recognized invalid-stream signatures become `FAILED_FINAL`; transient errors remain retryable. Retry and source reprocess actions require explicit user action. Candidate rows store source and interval identity, scores, category, selection, render/validation state, output paths, and errors. The session target increments only when a unique clip is rendered and validated.

Each run scans and reconciles manifests and outputs while holding the single-session lock. It adopts valid existing outputs, leaves partial work ready to render, repairs the queue stage in an atomic manifest write, and removes only known `clip_*.partial.*` render artifacts. Original source files and valid clips are not removed. Interrupted sessions are reported on the next explicit run.

The worker uses three passes per configurable microbatch (default 2, range 1–4):

1. **Analysis:** run scene/audio/motion analysis and transcription for pending episodes, using valid caches, then release Whisper once for the batch.
2. **Semantic analysis:** if enabled and available, load and reuse one vision model for all analyzed episodes in the batch, persist per-candidate semantic cache entries, then release vision.
3. **Rendering:** render only the remaining session target, validate each output, and persist each clip immediately. Completed, analyzed, and ready-to-render work is prioritized over new sources.

Whisper is configured local-files-only by the GUI and queue runner. The GUI checks the selected model cache before start and asks whether to continue without transcription when it is unavailable. Vision dependencies and verified weights are checked before semantic use; missing vision prompts an explicit heuristic-only choice. Start never installs packages or downloads model files. The existing **Set Up Vision Model** action remains the explicit vision download path.

The GUI retains folder restoration without auto-start and adds target (default 5), time budget (default 6 hours), batch size, start, pause/resume, safe stop, rescan, queue view, retry failures, and confirmed source reprocessing. Runtime VRAM and package/model availability are shown. Session JSON and Markdown summaries are written under `Shorts_Output/Daily_Sessions/<local-date>/`, and include validated clips, episode/queue counts, elapsed time, discovery/reconciliation time, and microbatch timings.

Timing data is now recorded per processing pass in each `analysis.json`, including metadata/cache checks, motion decode count and time, scene analysis, transcription, semantic initialization/inference, encoding, validation, and pass total. The session report records batch duration and model release time. The motion analyzer still decodes every frame; no sampling or quality-threshold change was made without profiling evidence.

## Queue transitions and recovery

| Event | Result |
| --- | --- |
| Newly discovered source | `PENDING` |
| Analysis begins / succeeds | `ANALYZING` → `ANALYZED` |
| Semantic pass or pending render | `ANALYZING` → `READY_TO_RENDER` |
| Render begins / validated outputs finish | `RENDERING` → `COMPLETED` |
| No acceptable candidate or too-short source | `SKIPPED` |
| Crash during active work | `ANALYZING`/`RENDERING`/`VALIDATING` → `INTERRUPTED` |
| Confirmed run retry | `INTERRUPTED` → `PENDING` for that run |
| Retryable/final failure | Explicit retry action → `PENDING` |
| Fingerprint change | `SOURCE_CHANGED`; confirmed reprocess required |

The session time budget is enforced at safe checkpoints. A currently running source analysis or clip render can finish after the selected limit; stop and pause also wait for the current operation or clip boundary. No output is counted before successful validation.

## Verification

- Full command: `.venv\Scripts\python.exe -m unittest discover -s tests`
- Result: **89 passed, 0 failed, 0 skipped** in 27.027 seconds.
- Compilation: `.venv\Scripts\python.exe -m py_compile main.py clipper.py nightly_queue.py queue_runner.py tests/test_nightly_queue.py tests/test_startup_safety.py tests/test_clipper.py tests/test_vision.py` — passed.
- `git diff --check` — passed.
- New queue tests cover natural ordering, deduplication, persistence, source changes, retries, crash recovery, schema guard, one active session, output reconciliation, clip uniqueness, target accounting, backlog continuation, no-candidate continuation, time checkpoint, safe stop, and model reuse across the semantic microbatch.
- GUI tests cover folder restoration without work at launch, explicit confirmation for interrupted work, old-ledger import, safe stop on close, and absence of desktop capture APIs.
- Existing synthetic FFmpeg rendering and output validation tests passed. The mocked microbatch test verifies ordering and model-instance reuse; it does not measure real model initialization or inference.
- The vision downloader test uses mocked Hub calls. No network model download or package installation was performed.

## Performance and hardware

- No real source-video folder was scanned or processed for this task.
- Per-stage timings and frame counts are now captured during future user runs; no representative real-video throughput measurement was made here.
- `_motion()` still decodes every source frame. Scene detection also performs a full source pass. This known cost remains unoptimized pending representative footage and profiling.
- Hardware snapshot: NVIDIA GeForce RTX 3050 A Laptop GPU, 4094 MiB total; 2497 MiB free at the read-only check. CTranslate2 reports one CUDA device.
- Python: 3.14.7. `faster-whisper` and `huggingface_hub` are present. The selected Whisper `small` model is not cached.
- Missing for vision: PyTorch, Transformers, Accelerate, Pillow; pinned SmolVLM2 weights are missing or unverified. Vision is not ready.
- CPU Whisper inference, CUDA Whisper inference, CPU SmolVLM inference, CUDA SmolVLM inference, real-video selection quality, peak inference VRAM, and actual batch model-load counts: **NOT RUN**. No claims of real inference speed or quality are made.

## Files changed

- `clipper.py` — staged analysis/semantic/render modes, per-pass timings, atomic resumable manifests, validated-output reuse, explicit no-transcription mode.
- `main.py` — nightly controls, local-only model preflight, interrupted-ledger integration, queue/status dashboard, retries and source-change confirmation.
- `nightly_queue.py` — new SQLite queue, source and clip records, recovery, locking, reconciliation, daily summaries.
- `queue_runner.py` — new bounded three-pass microbatch controller and session target/time handling.
- `tests/test_nightly_queue.py` — new queue, recovery, target, and lifecycle tests.
- `tests/test_startup_safety.py` — updated startup and interruption tests.
- `tests/test_clipper.py` — heuristic-only and timing assertions.
- `tests/test_vision.py` — test fixture for the GUI’s read-only VRAM check.
- `reports/task-04b-report.md` and `reports/IMPLEMENTATION_STATUS.md` — Task 04B record.

## Git and next step

- Intended branch: `main`; remote: `origin` (`Abishek-805/Clipper`).
- Implementation commit: `6e8b5cf` (`Implement persistent nightly queue and batch workflow`). The reporting changes are being committed separately.
- Push status will be updated after the push attempt.
- Task 05 remains **not started**. After reviewing this queue milestone, the next planned task remains Task 05: professional captions and improved framing.

## Known limits

This task validates queue orchestration and synthetic rendering only. Real Whisper and SmolVLM inference, real-footage accuracy, and hardware throughput remain unverified. Fast fingerprints can miss edits confined to the unsampled middle when size and modification time are restored. A single long analysis operation may exceed the session time budget before the next safe checkpoint.
