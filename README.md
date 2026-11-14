# Clipper Personal Daily

A local Windows application for turning **one episode at a time** into reviewable vertical clips. Automatic suggestions are provisional. You can always enter your own source interval, correct captions, add optional hook/context text, and export locally.

## Launch

Double-click `run.bat`, or run:

```powershell
.\run.bat
```

The launcher opens `http://127.0.0.1:8770/` in your browser. Keep the server console open. To run without opening a desktop browser:

```powershell
.\.venv\Scripts\python.exe browser_preview.py --port 8770
```

For an in-app browser, open that URL there. No processing starts until you click Generate or Create my draft. Closing or reopening the page does not restart processing.

## Daily workflow

1. Click **Choose file** to use the browser file chooser, or paste a full local path and click **Inspect tracks**. Browser-selected files stay on this PC. Known originals are reused only after an exact SHA-256 content check; new files are stored under `Shorts_Output/Imports/` once. Importing does not start clipping.
2. Select the desired audio track. English is selected only when metadata identifies one unambiguous English track. Listen to confirm it.
3. Choose English transcription, translation to English, or an intentional captionless export.
4. Generate up to two provisional drafts, or open **Mark my own clip** and enter source start/end seconds. Manual intervals bypass selection. Matching cached captions are reused; explicitly enable transcription if they are missing.
5. Review the MP4. Edit caption rows, source trim times, placement, optional hook/context, and render quality. Click **Save & Re-render Clip**.
6. Click **Export delivery files**. The displayed folder contains MP4/SRT files and provenance JSON.
7. Upload manually after checking audio, captions, framing, and publishing permissions.

Fast rendering uses a softer, lower-resolution blurred background. Standard preserves the original background-processing resolution. Both export 1080×1920 H.264/AAC MP4 at 30 fps with fast-start metadata. Intervals can be up to 180 seconds; rerendering intervals above 58 seconds requires explicit extended-duration consent in the UI.

## Saving and recovery

- Original source files are read only.
- Each new run gets its own folder under `Shorts_Output/jobs/`.
- Saved jobs reopen from their manifests. Pending edits are saved in browser storage and submitted edits are saved separately before rendering.
- Successful edits retain `.vN.mp4`, `.vN.srt`, and metadata snapshots. Restore versions created by this release through the editor. Older versions without snapshots remain accessible in their original job folder.
- Delivery copies go to `Shorts_Output/Exports/<episode>/<job-id>/`. Repeated exports get new filenames.
- Analysis/transcription caches and user glossary live under `%LOCALAPPDATA%\Clipper\`. Editing captions does not rewrite cached recognition words.
- Failed/cancelled edits retain previous outputs. Interrupted analysis requires a new explicit action; completed artifacts remain on disk.

## Installation and runtime

Run `setup.bat` if `.venv` is missing. Python 3.11+, FFmpeg/FFprobe, and Tkinter are required. The daily browser workflow requires the Whisper small model already cached for transcription; it does not silently download a model.

Whisper uses a verified CUDA path when available and CPU int8 otherwise. This machine reuses the already approved pinned CUDA libraries in `.runtime/llama.cpp/b12284/bin` and CTranslate2's bundled cuDNN through application-local DLL loading. A successful GPU detection alone is not an inference test. Optional vision models and `python-vlc` are not required for the daily workflow.

## Limits

The current selector measures activity and dialogue; it cannot reliably establish entertainment value or story significance. Manual intervals are available when suggestions are poor. Browser playback of the original HEVC/MKV is not provided in this release; use typed source times and review the rendered clip. Audible English and perceptual synchronization require owner listening. Captions, crops, and added text do not establish copyright permission or monetization eligibility. No upload integration, cloud service, source downloader, webcam, or desktop recording feature is included.

## Development

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -o faulthandler_timeout=120
```

Historical research and the blind human-benchmark workspace remain separate under `evaluation/`, `review_workspace/`, and `reports/`. Use `run-review.bat` for the optional VLC review workspace. No cleanup operation automatically deletes or moves your files.

### Browser UI checks

`tests/ui-review.cjs` uses Playwright 1.57 and installed Microsoft Edge in headless mode. Start the server on port 8771 with a saved captioned job available. Set `CLIPPER_PLAYWRIGHT_MODULE` to your Playwright module directory, then run `node tests/ui-review.cjs`. An ordinary `playwright` installation also works without that variable. Override the server with `CLIPPER_UI_URL` and browser executable with `CLIPPER_BROWSER`.

Checks cover the saved-job layout, desktop/mobile overflow, caption editing, polling/reload recovery, file inspection, and idle startup. The test creates no processing jobs and edits only disposable browser storage. It exercises the real browser chooser against the saved source; set `CLIPPER_NEW_SOURCE` to an optional additional test video to verify a new local import. These selected files are transferred only to the local server. Screenshots/results are written under ignored `output/playwright/`.

### Local trash and generated files

Disposable test caches, inactive browser profiles, benchmark copies, and verified duplicate runtime downloads can be quarantined under `_Trash/<timestamp>/`. This folder is local and Git-ignored. Each cleanup includes `manifest.json` with original paths, reasons, byte counts, and SHA-256 integrity checks; use it to restore individual entries without overwriting regenerated files. Historical test evidence may now be at its listed trash path.

Source media, imported episodes, completed production jobs/exports, models, the active `.runtime/llama.cpp/b12284/bin` runtime, and persistent analysis caches are retained. Moving files to trash does **not** free disk space; no permanent deletion is performed. Python/test caches and screenshots may regenerate when tools run again and remain ignored.


### GPU stages and CPU work

New visual analysis prefers a single **NVDEC decode + CUDA resize** pass. PySceneDetect and motion scoring consume only 320×180 samples on the CPU instead of decoding the full-resolution episode twice. Whisper prefers CUDA int8_float16; final H.264 encoding prefers NVENC. Audio processing, scoring, captions and some render filters still use CPU, so CPU activity does not mean acceleration failed.

The status area shows actual stage/device messages and system-wide GPU memory, decode and encode utilization. Those counters include other applications; job manifests separately record device provenance. Cached analysis/transcripts are labeled as reused and do not run inference again. In Task Manager, inspect NVIDIA **Video Decode / Video Encode / CUDA** engines rather than only the default 3D graph. Unsupported GPU decoding falls back explicitly with the reason in Processing details. `CLIPPER_VISUAL_DEVICE=cpu` is available for diagnostics.

This visual-engine update refreshes old visual-analysis caches once; transcription caches remain independent. The sampled detector may shift scene boundaries slightly, so review clips as usual.

### Episode exclusions and subtitle sources (Sprint 3A)

After inspecting a video, open **Episode exclusions → Inspect selected episode**. Explicit structural chapters and independently supported ASS song styles can exclude automatic candidates; position-only warnings lower priority without proving a song. The legacy filter switch reproduces unfiltered selection. Advanced JSON overrides support adding manual zones, editing detected zones and suppressing detected IDs; remove manual add records to remove manual zones. Changes affect future runs. New jobs save raw detection and applied overrides in `structure.json`; existing exports remain unchanged.

Whisper remains the default for captions. **Use release subtitles for captions** requires a unique reliable English dialogue track; conflicting language metadata or multiple plausible tracks produces a clear error. Source cue timing is retained rather than inventing word alignment. Track assessment and scene/utterance/shot IDs prepare later work; they do not establish plot understanding or editorial quality. See [Sprint 3A results](reports/understanding-3a-report.md).

Within Episode exclusions, expand **Review subtitle #…** to inspect five source dialogue samples. **Use as English dialogue** saves your explicit confirmation for that source hash and stream; **Not dialogue** excludes the track as a plot source, and **Clear decision** restores heuristic assessment. A confirmation can resolve incorrect language tags. Multiple positive confirmations still require a unique choice. This changes future source assessment, not existing jobs or your caption-mode selection.

Sprint 3B's original text-only experiment failed its required evidence contract. The production selector remains the Sprint 3A selector; no new semantic selection is enabled. The [original Phase 1 report](reports/understanding-3b-report.md) records that baseline; the continuation below records the revised experiment and newly confirmed owner references. Previous selector intervals are not human labels.

### Owner reference review

The [continuation experiment](reports/understanding-3b-continuation-report.md) also failed its gate; no new model selector is enabled. Two source intervals are now explicitly owner-confirmed in `evaluation/known_moments/s01-e01.json`.

Choose and inspect the source, select its audio, then expand **Owner reference moments → Find proposed intervals** below the draft editor. This searches matching cached dialogue without starting a model or transcription. Play a landscape proxy, edit source seconds, and choose **Confirm reference** or **Reject**. Saving edited times keeps the interval PROPOSED until confirmed again. Existing owner-confirmed template entries are imported; subsequent local decisions take precedence.

Reference JSON and small previews live under ignored `Shorts_Output/Review/<source-sha256>/`. Open the JSON link after saving to export your dataset. Preview timestamps are approximate source offsets, not frame-accuracy guarantees. Missing cached dialogue produces an explicit error. Source mismatches cannot reuse labels; changed text matches do not delete saved owner references. This workspace is separate from the blind Task 06I benchmark.

`tests/ui-known-moments.cjs` supplements the existing browser regression script. Set `CLIPPER_REVIEW_SOURCE`, `CLIPPER_UI_URL` and `CLIPPER_PLAYWRIGHT_MODULE` against an isolated server/cache. It tests loading, hidden/visible preview states, playback and seeking; it never clicks Confirm, Reject or Save on real media.

### Local model editorial playbook

The user-supplied runtime rules, four task prompts, JSON schemas, validators and synthetic tests are preserved unchanged under `clipper/playbook/`. They are model prompt assets; no Codex skill installation, training or MCP tool calling is involved. `evaluation/playbook_adapter.py` loads them without replacing the existing `clipper.py` module and rejects malformed JSON shapes before custom validation.

The opt-in `python -m evaluation.benchmark_text_scenes ... --playbook` mode uses the existing explicit `--units`, `--runtime`, `--model` and `--output` arguments. It stays offline, disables thinking, runs one model at a time, records raw results and stops at the acceptance gate. `--playbook` and `--revised` are separate, mutually exclusive experiments. No model starts during application launch or file selection.

The measured playbook result was **24/32 accepted, gate failed**. No nomination, action or hook production feature is enabled. See [results and limitations](reports/editorial-playbook-report.md). To verify preservation: `.venv\Scripts\python.exe tools\guard_playbook.py --baseline cda29330106004a69fa5610bba61436e37b8f40a`. The local task's optional `--integrity .runtime/playbook/preserved.json` additionally checks recorded source/cache/model/job hashes.
