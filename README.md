# AI Video Clipping Factory

A local Windows desktop app that scans a selected folder, ranks short segments from audio energy, visual motion, scene cuts and speech density, then renders reviewable 9:16 Shorts with original audio and word-timed captions.

## Start

1. Install Python 3.11 or newer and FFmpeg/FFprobe, with FFmpeg's `bin` directory on `PATH`.
2. Double-click `setup.bat`, then `run.bat`.
3. Select a video folder and click **Start Automatic Clipping**.

Faster-Whisper downloads the selected multilingual model (`small` by default) the first time it is used. Model downloads are hosted by Hugging Face and are typically hundreds of MB; exact size varies by model revision. Processing uses CUDA when CTranslate2 can load a CUDA device, with CPU int8 fallback. Video processing is sequential. After model weights are cached, analysis and rendering are local.

## Output

`Shorts_Output/<source>/` contains rendered MP4 clips, matching SRT files, and `analysis.json`; the output root contains `processing_report.csv`. Originals are only read. Failed sources are logged and do not discard finished clips. Completed videos are skipped on a later run; remove that video's output folder to reprocess it.

The portrait layout keeps the full source frame visible over a blurred fill, preventing important action from being cropped. Captions are burned in with word highlighting and are also available as SRT. If Faster-Whisper is unavailable or the source has no audio, clips still render without invented subtitles.

Moment ranking is a local heuristic, not semantic understanding: it can score activity and speech density, but it cannot reliably know whether a moment is funny, emotional, or narratively important. Review all clips before publishing.

## Requirements / limits

- Windows 10/11, Python 3.11+, FFmpeg and FFprobe on `PATH`.
- Optional packages installed by `setup.bat`: OpenCV, PySceneDetect and Faster-Whisper.
- One source video is analyzed at a time. The source's first audio track is used.
- Output uses H.264/AAC where audio exists, 1080×1920, 30 FPS, yuv420p and fast-start MP4.
- The app does not download source videos or alter originals.

## Developer checks

Run `python -m unittest discover -s tests` from the project directory. The FFmpeg integration test is skipped when FFmpeg/FFprobe are not installed.
