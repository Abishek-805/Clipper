# Supplied audit fixes — Windows integration, 2026-11-15

Branch: `codex/audit-workflow-fixes`. Baseline: verified local/remote main `4bdb693f760b8b49cd7bc6c27ecf8209a2236159`.

Imported `Clipper-fixed.zip` (SHA-256 `c2c28f8b28489ab2943aec3efa7f98c2dd3056535268a91a3ddefebe1a616780`): 18 modified tracked files and the supplied `reports/clipping-workflow-audit.md`. Every archive path was checked before extraction into ignored `.runtime/audit-import/`; only differing files were applied. Nothing absent was deleted. The ZIP has no Git history, so supplied commit `9580066` cannot be authenticated. The supplied report's Linux results remain historical, separate from this Windows verification.

## Changes

- Optional explicit Whisper setup/retry command, cached-weight check, and clear captionless path. No silent model download, 2B model or paid API required. Daily workflow explicitly disables legacy editorial calls and reports transcription failures.
- Fixed FFmpeg discovery and ASS rendering from apostrophe-containing folders. Manual General clips use existing smart framing; full-frame source profiles remain intact. Installed the compatible **OpenCV 4.14.0.94 Windows wheel (41.2 MB)** in `.venv`, replacing 5.0.0.93; confirmed `CascadeClassifier` is available. NumPy and other package versions were unchanged; pip dependency check passes.
- Searchable timestamped transcripts and lightweight source-interval playback using the selected audio. Source-relative caption corrections survive subsequent trim/placement edits. Render and browser share the configured cache root. Media revisions invalidate stale previews.
- Cancellation events survive worker startup, completion cleanup is paired with publication, stale operation flags clear on restart, loopback Host validation and artifact containment tightened. Fixed source/window-relative static-speech indexing.
- Added Windows regression coverage and a reproducible browser test. During testing, found and fixed a pending-submission UI race: disable existing draft edits until the new job request is acknowledged, then wait for the current job. Also keep New recording disabled until initial restoration finishes, avoiding a late poll folding the newly opened source panel.

## Actual results on this machine

| Check | Result |
| --- | --- |
| Starting Windows suite | **338 passed, 9 subtests passed**, zero failed/skipped; 100.82 s |
| Final full suite | **347 passed, 9 subtests passed**, zero failed/skipped; 76.72 s (`python -m pytest -o faulthandler_timeout=120 -q`) |
| New Python regressions | **9 passed**, including selected-track preview/range responses, invalid tracks, no-model captionless processing, explicit setup mocked without downloading, early cancellation, model-cache weights, transcript grouping and FFmpeg discovery |
| Existing browser regression | **15 checks passed**, zero page/console errors or processing requests |
| New end-to-end browser test | **6 checks passed**: seeded transcript search, selected-track preview, manual creation/first caption correction, second trim/placement edit, reload/playback and delivery export; zero page/console errors |
| Export inspection | **1080×1920, H.264, AAC, yuv420p, 30 fps**, 2.866667 s after trimming the requested interval to 2.9 s; actual encoder **h264_nvenc**. Both corrected cues persist in source milliseconds/seconds. This confirms hardware encoding, not CUDA transcription. |
| Integrity | All **18** recorded source/cache/model/job/reference/raw-result hashes and **14** supplied playbook hashes unchanged. Untracked `vlc-help.txt` preserved. |
| Other checks | Compilation, `pip check` and `git diff --check` pass. Windows tests remained active; supplied platform skips do not skip Windows CUDA/Tk checks here. |

Browser testing ran headlessly against disposable port 8775 and copied/synthetic artifacts, without taking over the desktop or touching the owner's server. Ignored evidence: `output/playwright/audit-workflow-result.json`, `audit-export-result.json`, `audit-workflow.png`, regression screenshots/result, and `.runtime/audit-acceptance/fixture.json`. The synthetic six-second source has tone audio and deliberately seeded text: **no fresh Whisper inference or caption-accuracy claim**. No model download, full-episode processing, AI gate activation or capture feature. The 2B editorial gate remains failed.

Restart the existing server when convenient, then run `run.bat`. Choose a local recording, inspect tracks, and open **Find a moment and mark my own clip**. Use cached transcript search or captions None; explicit transcription requires the prepared local Whisper model. Real-footage caption accuracy, audible language, perceptual sync and fresh Windows/CUDA transcription remain unverified. Only this integration branch is intended for push; main stays unchanged. Delivery response records the verified remote SHA.
