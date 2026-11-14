# Clipper Personal Daily — completion report

Branch: `feature/personal-v1-daily-ready`. Base: `6e9e9054fbda6360cdc02ca7d8bd1dd86c352d0a`.

| Stage | Result | Commit | Verification |
| --- | --- | --- | --- |
| A | Done: nullable captions, operation reservation, pending edits, job reopening | `4ddfb8f` | 225 tests; later headless trim/caption edits and concurrent-start regression passed |
| B | Done: merged contain graph, Standard/Fast presets | `a497c69` | 226 tests; matched 46.329s render benchmark below |
| D-minimal | Implemented: file picker/path fallback, manual intervals, caption rows, hooks/context, glossary, restore, range requests | `051fba4` | 230 tests; real headless workflow completed; in-app access unavailable |
| C | Done: existing native DLLs preloaded by absolute path; no fallback in real CUDA test | `86f7b1b` | 230-test suite plus focused DLL regression; final suite includes corrected fixture isolation |
| D-extended | Skipped: six-candidate picker, original-source proxy and three-view redesign | `ce28fb2` | Kept typed manual intervals, saved-job list and the tested focused editor; avoided additional playback/selection work |
| E | Done: validated delivery copies, README, launcher; cache mismatch guard | `4d6192b`, `7828cdb` | Final **233 tests + 9 subtests passed**, 50.63s; compilation, JavaScript syntax and diff checks passed |

## Measurements

Same copied S01-E01 interval, captions and NVENC: baseline **218.676s** (encode/filter 216.783s; validation 1.322s), merged Standard **182.846s**, Fast **19.863s** (18.028s + 1.253s). Fast is **11.0× faster** and intentionally changes background softness. Observed process peak RAM: baseline **at least 3.28 GB**; Standard 829 MB; final peaks/Fast RAM were not captured. Exact commands and controlled-run evidence are in `.runtime/daily-ready/{baseline,optimized,fast}.json`. Standard/baseline audio correlation **1.0**, identical SRT, sampled frame mean differences 0–0.471/255. At the first frame, source alignment favored the requested timestamp over ±1 source frame in all presets. NVDEC passed a 2s decode smoke test; it was not enabled in production.

Real 30s Whisper sample: CUDA int8_float16 **3.280s init / 5.487s inference / 9.348s total**, CPU int8 **4.390s / 21.138s / 25.788s**; both 49 English words. CUDA had no fallback; observed total dedicated VRAM peak **1,779 MiB**, including desktop use. Registration alone failed; absolute preloading reused pinned cuBLAS from `.runtime/llama.cpp/b12284/bin` and CTranslate2's bundled cuDNN. The unnecessary 553.2 MB cuBLAS wheel download attempt was stopped; no NVIDIA package or Python 3.12 environment was installed. CPU fallback remains available.

## Real acceptance and limitations

Headless Edge exercised the actual browser UI and selected metadata-labeled stream 2. Jobs `20261114-133621-d99bea52` and `20261114-133854-8c3e7a99` each produced two drafts; the repeat recorded **transcription reused / analysis signals reused**. MP4 playback advanced at 1080×1920. Cancellation stopped `20261114-134021-98b6831f` during rendering. Manual job `manual-20261114-134124-4fc3141e` bypassed selection; trim/hook and caption edits took **3.234s / 3.461s**. Delivery export, byte-identical version restoration, page reload and server restart passed. All **155 protected job/cache file hashes remained unchanged**. A caption-row newline/spacing issue found in visual review was fixed and checked after restart. Local browser evidence and screenshot: `.runtime/daily-ready/browser-acceptance.json`, `restart-check.json`, `review-window.png`.

**Audible English and perceptual synchronization: PENDING OWNER LISTENING.** The in-app tool returned “Local browser access is unavailable for this chat”; headless verification does not certify that surface. File-picker interaction awaits owner use; path entry was tested. Original HEVC/MKV playback is not implemented here: enter source times and review the rendered clip. Editorial quality and publishing rights are not accepted. Trash/moves/deletion were skipped; no unknown files were moved. `vlc-help.txt` remains untouched/untracked.

Launch: `run.bat`, or `.venv\Scripts\python.exe browser_preview.py --port 8770` and open `http://127.0.0.1:8770/`. Port 8770 served the page with idle startup in the smoke test. Delivery files: `Shorts_Output/Exports/<episode>/<job-id>/`.

Remote implementation tip verified: `7828cdbd5acdd1ec71e1125ea0e8f60da0c9b8c5` on this feature branch only. This completion report is committed afterward; the final response records the verified documentation tip. No protected branch was pushed.


## File chooser follow-up

Installed the Playwright CLI and interactive skills into the user skill directory. Used the CLI's real file chooser with S01-E01, then tested manual creation, captions/trim, placement, hook/context, rerender (3.498s), delivery export and byte-identical version restore on a new test job. The browser now selects files directly; new videos stream to local Imports, and known originals are reused only after an exact SHA-256 content check. The old request-thread Tk dialog is retired. Status stays responsive during imports, interrupted transfers clean up, and selection never starts clipping. Also fixed saved-job refresh, accessible trim labels and favicon errors.

Verification: **241 Python tests + 9 subtests**, **14 Playwright UI checks**, no page/console errors in the final browser pass. All 155 protected hashes stayed unchanged. Graphify's existing graph was queried without rebuilding (zero extraction/model cost); it predates the browser workspace, so current source was checked directly. Taste and Ponytail guided the focused UI changes. Skills are available automatically next turn. Owner listening remains pending.


## GPU visual-analysis follow-up

The user's S01-E03 job actually used CUDA int8_float16 transcription (1,920 words; 122.470s inference; no fallback) and validated two NVENC outputs. Its earlier CPU visual passes cost 327.035s motion + 458.813s scenes, explaining the screenshot. Those two decodes are now replaced by one NVDEC/CUDA sampled pass with explicit CPU fallback and device/cache provenance; lightweight scene/motion calculations remain CPU. On a real 30.238s HEVC sample, old CPU passes took 22.132s versus 2.200s for the GPU-backed pass. Both found 12 cuts; maximum boundary difference was 0.255s, so editorial equivalence is not claimed. A fresh isolated end-to-end sample job recorded `cuda-nvdec` and validated an NVENC output; observed total system GPU memory peaked at 1,726/4,094 MiB. No new runtime/model downloads.

Added responsive system-wide GPU telemetry and cache-aware device labels. Verification: **246 tests + 9 subtests**, **14 Playwright UI checks**, compilation and diff checks; original protected assets retained. GPU evidence is local under `.runtime/gpu-check/`. Current jobs are not changed in place; the new analysis path is used after relaunch. Some CPU work remains expected.
