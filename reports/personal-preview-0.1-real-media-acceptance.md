# Personal Preview 0.1 — Browser-Based Real-Media Acceptance

## Scope

On 2026-11-13, the Personal Preview workflow was exercised through a small local browser console because the Tkinter window occupies the desktop during manual UI testing. The console runs the existing `process_video` pipeline and cache/model components, but it is a separate local UI; it does not embed or replace the Tkinter window.

Source: `[S01-E02] Sword Art Online [1080p] [Dual] @Anime_x_Clan.mkv` (23:43.1, 1920×1080 HEVC, 414,839,700 bytes). The browser console was opened in Codex's in-app browser at loopback. The selected audio was global stream #2, English AAC stereo. Processing stayed local; no model or package download occurred.

## Results

| Acceptance check | Result | Evidence |
| --- | --- | --- |
| Browser UI opens without taking over the desktop | PASS | `run-browser-preview.bat` starts a loopback-only browser UI; manually used in the in-app browser. |
| Explicit episode/audio/caption selection | PASS | Episode inspected; English stream #2 selected; Whisper transcription selected. |
| First real run completes | PASS | Job `20261113-211610-d9a8effb` completed with two validated provisional MP4/SRT pairs. |
| Video playback in browser | PASS | Opened the generated portrait MP4 in the browser; decoded video rendered and playback time advanced. |
| Output structure and streams | PASS | Both MP4s are 1080×1920, H.264, 30 fps, AAC 96 kHz stereo, with durations 49.966667 s and 59.559 s. FFprobe confirmed the container and streams. |
| SRT files exist and contain timed captions | PASS, structural only | Both SRTs are nonempty and contain valid-looking timestamped cues (3 and 2 cues). Caption completeness, translation quality and exact spoken-word accuracy were not reviewed. |
| Whisper device behavior | PASS with fallback | CUDA initialization failed because `cublas64_12.dll` could not be found. Whisper retried on CPU int8, detected English, and produced 1,615 words. This is not a GPU Whisper pass. |
| Repeat run and cache reuse | PASS | Job `20261113-215619-41b13297` completed with the same settings; analysis and transcription caches were both reported `reused`; it produced two validated MP4/SRT pairs. |
| Safe cancellation and completed-output retention | PASS | Job `20261113-220705-1a9088f4` was cancelled during clip 2. The report records `interrupted`; validated `clip_001.mp4` and its SRT remain, and the incomplete second clip is not listed as completed. |
| Editorial quality | NOT EVALUATED | The clips are provisional; this technical acceptance did not assess whether they are good Shorts. |
| Audible language and A/V sync | NOT FULLY VERIFIED | Stream metadata identifies English AAC stereo and the browser decoded video. No independent listening/synchronization check was performed. |

The video encoder selected NVENC after its self-test passed. This establishes that this render used NVENC; it does not establish Whisper GPU acceleration.

## Changes for browser testing

- Added [browser_preview.py](/C:/Users/ashek/Videos/Clipper/browser_preview.py), a minimal loopback-only browser console that uses the existing single-episode processing pipeline, local model cache, per-run output folders, cancellation, and status report.
- Added [run-browser-preview.bat](/C:/Users/ashek/Videos/Clipper/run-browser-preview.bat) to launch the browser UI without opening a Tkinter window.
- Added browser console tests and persisted the actual Whisper fallback reason in the transcription cache for future cache-hit reports.
- The browser service binds only to `127.0.0.1`; artifact serving is restricted to job folders. No screenshot, screen-recording, webcam, or desktop-capture feature was added.

The test episode's existing transcription cache was created before this metadata field was added, so its repeat-run cache hit did not contain the fallback reason. The first run's job log records the missing `cublas64_12.dll` and CPU retry; subsequent newly created transcription caches will retain that reason.

## Verification

- Focused browser/cache tests previously run: **4 passed**.
- Full project suite: **206 passed, 9 subtests passed, 0 failed** in 213.33 seconds.
- Manual browser checks: source inspection, job start/progress/results, MP4 playback, repeat run, and cancellation.
- FFprobe checks: all completed MP4s reported H.264 portrait video and AAC stereo audio at the expected dimensions/frame rate.

## Acceptance status

The browser-based real-media workflow passed its technical generation, cache reuse, playback, and cancellation checks. Personal Preview 0.1 remains **partially accepted** because the native Tkinter workflow was not manually exercised, spoken audio and A/V synchronization were not independently checked, caption accuracy/completeness was not reviewed, and editorial quality is outside this technical gate. Personal Preview 0.2 should wait until those remaining 0.1 checks are accepted.
