# AI Video Clipping Factory

A local Windows desktop app that scans a selected folder, proposes variable-duration segments from scene boundaries, visual motion, audio energy and grouped dialogue, then renders reviewable 9:16 Shorts with original audio and word-timed captions.

## Start

1. Install Python 3.11 or newer and FFmpeg/FFprobe, with FFmpeg's `bin` directory on `PATH`.
2. Double-click `setup.bat`, then `run.bat`.
3. Select a video folder and click **Start Automatic Clipping**. Startup only restores the last folder; it never starts or resumes processing automatically.

Faster-Whisper downloads the selected multilingual model (`small` by default, about 486 MB) the first time it is used. Processing uses CUDA when CTranslate2 can load a CUDA device, with CPU int8 fallback. Faster-Whisper's current Windows GPU path needs CUDA 12 and cuDNN 9 runtime libraries on `PATH`; otherwise it uses the CPU. Video processing is sequential. After model weights are cached, analysis and rendering are local.

## Output

`Shorts_Output/<source>/` contains rendered MP4 clips, matching SRT files, and `analysis.json`; the output root contains `processing_report.csv` and `processing.log`. Originals are only read. Failed sources are logged and do not discard finished clips. Completed videos are skipped on a later run; remove that video's output folder to reprocess it.

If Clipper closes while processing, it stops after the current operation, keeps completed clips, and records the unfinished job under the user's local application data. The next launch asks before resuming. Choosing **No** leaves the interrupted job available for a later launch.

The portrait layout keeps the full source frame visible over a blurred fill, preventing important action from being cropped. Captions are burned in with word highlighting and are also available as SRT. Transcription status distinguishes no audio, no speech, unavailable model support and transcription failure in the GUI log and analysis.json; clips still render without invented subtitles.

Candidate selection proposes intervals from scene groups, sustained signal activity, and pause/sentence-grouped dialogue, then scores normalized audio, motion, speech, scene continuity, and boundary features. Overlapping and low-scoring candidates are rejected; short sources below the minimum are skipped and recorded. The analysis_cache.json file stores source-fingerprinted motion, audio, and scene signals for reuse when clip settings change. The GUI status and analysis.json show candidate counts, selected durations, feature scores, and rejection reasons.

Moment ranking is a local heuristic, not semantic understanding: it can score activity and speech density, but it cannot reliably know whether a moment is funny, emotional, or narratively important. The synthetic candidate benchmark checks interval behavior against planted signal events only; it does not establish real-world entertainment quality. Review all clips before publishing.

## Requirements / limits

- Windows 10/11, Python 3.11+, FFmpeg and FFprobe on `PATH`.
- GPU transcription requires compatible NVIDIA CUDA/cuDNN runtime libraries; CPU int8 is the fallback.
- Optional packages installed by `setup.bat`: OpenCV, PySceneDetect and Faster-Whisper.
- One source video is analyzed at a time. The source's first audio track is used.
- Output uses H.264/AAC where audio exists, 1080×1920, 30 FPS, yuv420p and fast-start MP4.
- The app does not download source videos or alter originals.

## Developer checks

Run python diagnostics.py to check FFmpeg/FFprobe, NVIDIA hardware, CUDA support, and usable encoders. Run python -m unittest discover -s tests -v for the synthetic rendering integration suite. NVENC tests skip if the runtime encoder probe fails.
Run python benchmark_candidates.py to regenerate the deterministic signal benchmark and its JSON/Markdown reports under reports/.

Whisper loads only after **Start Automatic Clipping** and is reused across videos in one batch. Automatic mode prefers CUDA `int8_float16` when the installed CTranslate2 runtime reports support, with CPU int8 fallback; CUDA OOM during inference retries once on CPU. Transcription cache entries are keyed to the source file and transcription settings and are replaced only after a complete successful transcription.

Run `python benchmark_models.py` to record hardware information and initialize supported modes using models already present in the Hugging Face cache. This benchmark never downloads weights or searches for audio. Transcription timings run only when an explicit sample is supplied with `--audio-sample <path>`; the report says `NOT RUN` otherwise. The RTX 3050 float16 smoke test is skipped unless at least 2048 MiB of VRAM is free.
