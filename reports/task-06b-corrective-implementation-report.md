# Task 06B — Corrective implementation and one-episode validation

**Result: technical pipeline improved and real speech transcription verified; editorial quality remains partial and Task 07 is not started.**

## Environment and root causes

- Windows virtual environment: `.venv\Scripts\python.exe` (Python 3.14).
- Installed versions: Faster-Whisper 1.2.1, PyAV 19.0.2, OpenCV 5.0.0, FFmpeg/FFprobe 9.0.2. No package was installed, upgraded, downgraded, or removed.
- Installed Faster-Whisper `WhisperModel.transcribe` accepts an `audio` waveform. Its implementation decodes only non-NumPy inputs through `decode_audio`; passing an ndarray avoids the incompatible `av.open(metadata_errors=...)` path.
- OpenCV 5.0.0 has no `CascadeClassifier`; attempting that attribute caused the earlier frame-plan failure.
- Task 06A hard-coded `0:a:0` for energy and final audio while passing the MKV path into Whisper. It could select neither independently nor consistently.

## Changes

- FFprobe now returns every global stream index, type, codec, language, title, channels, and default flag. The one-episode GUI lists audio/subtitle tracks and supports Auto/default or a chosen audio stream, Whisper transcription, Whisper translation to English, embedded subtitle streams, and None.
- The chosen audio global index is used by audio-energy extraction, FFmpeg mono 16 kHz float32 decoding, Whisper, and final AAC mapping. Stream selection and caption/transcription settings are in the run settings/manifest and cache fingerprints. Render output strips copied chapters/metadata and maps no unrelated/data streams. AAC language is tagged from the selected source stream.
- Whisper now receives an in-memory NumPy waveform, uses language auto-detection, supports `task="translate"`, retains source-relative word timestamps, and records decoded duration and inference status. Embedded SRT conversion preserves cue times and distributes cue words across each cue; this is approximate word timing, unlike Whisper’s measured word timings.
- Requested captions with zero valid SRT cues fail output validation. Top/bottom placement maps to the existing upper/lower safe regions. Caption source and detected language are recorded.
- Missing OpenCV face detection yields an explicit `blurred_background` plan with full-frame contain shots. Smart face crop is not claimed.
- Windows FFmpeg, FFprobe, encoder checks, GPU diagnostics, frame extraction, and setup subprocesses use `CREATE_NO_WINDOW` while retaining captured error output.
- Candidates with no dialogue, low motion, and likely static title/credit signals are rejected; heuristic-only labels are `activity-only`, not semantic categories. This is a limited filter, not reliable title-card/credit recognition.
- Tests cover embedded SRT time preservation and rejection of empty caption output. Existing synthetic rendering and startup-safety tests remain passing.

## Real Whisper smoke test

Source stream enumeration confirmed:

| Global index | Type/codec | Language | Channels | Default |
| --- | --- | --- | ---: | --- |
| 0 | HEVC video | — | — | yes |
| 1 | AAC audio | Japanese (`jpn`) | 2 | no |
| 2 | AAC audio | English (`eng`) | 2 | yes |
| 3 | ASS subtitle | English (`eng`) | — | yes |
| 4 | ASS subtitle | Japanese (`jpn`) | — | no |
| 5 | ASS subtitle | English (`eng`) | — | no |
| 6 | ASS subtitle | Japanese (`jpn`) | — | no |

The first short window (110–140s) was intro/title material and yielded only four words, so it was not treated as a representative speech smoke test. A later 30-second sample at source time 600s decoded from global audio stream 2 and returned 91 word-timestamped words. Whisper detected English and ran on CUDA `int8_float16`; inference took 31.93 seconds. The RTX 3050 reported 4,094 MiB total VRAM and approximately 1,503 MiB used at peak. Readable sample: “I got a mom and a sister, and I'm pretty sure they'll notice by dinnertime and come look at her. You got a sister? How old is she?”

## One-episode rerun

- Source: the same S01E01 file only. No directory scan, queue, or other episode was run.
- New output: `Shorts_Output/Task06B-validation-20261112-203422/_S01-E01_Sword_Art_Online_1080p_Dual_Anime_x_Clan_fbfef207/`.
- Selected source audio: global stream 2, English AAC. Caption source: Whisper transcription. Output contains only video and the selected AAC audio; `ffprobe` verified AAC language `eng` and no data stream.
- Whole-source transcription: 1,420.03 audio seconds, 2,162 words, detected language `en`, 560.807 seconds, RTF 0.395. CUDA inference encountered `Library cublas64_12.dll is not found or cannot be loaded`; the existing manager retried and completed on CPU `int8`. The result is recorded as `CPU_FALLBACK`, not GPU success.
- Selection: 2,545 candidates, five selected, durations 24.544, 41.625, 27.124, 21.939, and 21.188 seconds. All were honestly categorized `activity-only`; no story-level category was assigned.
- Source intervals: 0.626–25.170s; 251.001–292.626s; 864.100–891.224s; 1104.395–1126.334s; 1284.825–1306.013s.
- All five MP4s passed FFprobe and deep decode validation: MP4, H.264, AAC `eng`, 1080×1920, 30 FPS, yuv420p. Their separate UTF-8 SRT files passed timestamp validation with 4, 10, 10, 5, and 3 cues. The burned captions are visible in the contact sheet, placed in the blurred lower region.
- Reframing mode was `blurred_background`, with the full landscape source centered. Face-aware smart crop is unavailable in the installed OpenCV build.
- Contact sheet: `Shorts_Output/Task06B-validation-20261112-203422/_S01-E01_Sword_Art_Online_1080p_Dual_Anime_x_Clan_fbfef207/clip_contact_sheet.png`. Visual review shows: (1) a studio/logo opening with exposition captions; (2) feet in grass; (3) a cloaked figure with dialogue; (4) manga pages with dialogue; (5) a sunset/path. No ending-credit sequence was selected, but clip 1 still includes opening-logo imagery. The automated candidate filter therefore does not fully reject irrelevant title/logo material.
- Local editorial endpoint was not configured; SmolVLM semantic analysis remained disabled. The selected clips are not accepted as story-quality highlights. Their varied durations and valid captions improve reviewability, but the contact sheet still shows weak/uncertain selections and the app must not claim semantic understanding.

## Stage timings and progress

| Stage | Seconds |
| --- | ---: |
| Metadata probe | 0.154 |
| Audio RMS analysis | 1.329 |
| Motion analysis (34,107 frames) | 498.267 |
| Scene detection (381 cuts) | 621.140 |
| Full transcription (CPU fallback) | 560.807 |
| Candidate selection | 9.159 |
| Rendering and validation, five clips | 655.014 |
| Total | 2,387.861 (39m 47.9s) |

The GUI reports current-stage messages but does not provide measurable stage percentages or a trustworthy ETA. The first run therefore cannot validate ETA accuracy. Normal GUI terminal flicker was not visually checked in this run; subprocess flags were implemented and the test suite passed.

## Verification

- `.venv\Scripts\python.exe -m compileall -q .` — passed.
- `.venv\Scripts\python.exe -m unittest discover -s tests -v` — **107 passed, 0 failed, 0 skipped** (final suite run after implementation; generated-media tests included).
- Real 30-second English speech sample — 91 words, language `en`, CUDA `int8_float16`; passed as an inference smoke test.
- Real full episode — transcription completed with 2,162 words on CPU int8 after a CUDA library failure; captions, MP4s, language tag, stream mapping, and output validation passed.
- Manual visual review — contact sheet inspected; editorial quality remains partial/failing as described above.
- Not run: Japanese-to-English full episode translation, GUI interaction of the new stream controls, terminal-flicker visual check, manual exclusion ranges, branding controls, reliable title-card classifier, local semantic/editorial models, and Task 07.

## Repository state

- Branch: `main`.
- HEAD at start of task: `1fe43b76de7c20930c34f36c6ea86696fd72c695`.
- `main...origin/main`: 0 ahead / 0 behind at report time. Task 06B changes are uncommitted and not pushed.
- Changed implementation/test files: `clipper.py`, `main.py`, `reframing.py`, `diagnostics.py`, `model_manager.py`, `vision.py`, `tests/test_clipper.py`, `tests/test_model_manager.py`.
- Reports updated: this file, [Task 06A baseline report](task-06a-single-episode-acceptance-report.md), and [implementation status](IMPLEMENTATION_STATUS.md).
