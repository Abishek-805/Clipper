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
