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

Portrait framing is planned separately for each shot. Clipper uses low-resolution face and motion/saliency signals when they support a safe crop, and otherwise keeps the full source frame visible over a blurred fill. Caption placement can avoid detected faces, action regions and likely lower-frame source text, or use a fixed lower/middle/upper override. These are lightweight heuristics, not semantic tracking or OCR. The GUI review table lists each output with a thumbnail and actions for playback, reveal, SRT, analysis, contact sheet and framing debug. Transcription status distinguishes no audio, no speech, unavailable model support and transcription failure in the GUI log and analysis.json; clips still render without invented subtitles.

Candidate selection proposes intervals from scene groups, sustained signal activity, and pause/sentence-grouped dialogue, then scores normalized audio, motion, speech, scene continuity, and boundary features. Overlapping and low-scoring candidates are rejected; short sources below the minimum are skipped and recorded. The analysis_cache.json file stores source-fingerprinted motion, audio, and scene signals for reuse when clip settings change. The GUI status and analysis.json show candidate counts, selected durations, feature scores, and rejection reasons.

Moment ranking is a local heuristic, not semantic understanding: it can score activity and speech density, but it cannot reliably know whether a moment is funny, emotional, or narratively important. The synthetic candidate benchmark checks interval behavior against planted signal events only; it does not establish real-world entertainment quality. Review all clips before publishing.

Optional semantic analysis can be enabled with the **Enable optional local semantic scene analysis** checkbox. It uses the pinned SmolVLM2-500M-Video-Instruct model only when Start is clicked, samples up to six frames per candidate, and blends its uncalibrated score 35% with the heuristic score (65%). Heuristic ranking remains active if setup, weights, inference, or CUDA is unavailable. Semantic analysis is English-transcript-aware only; non-English speech is not translated or treated as understood. No weights or optional dependencies are downloaded automatically.

Use the project's current virtual environment for optional dependencies:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-vision.txt
```

Install PyTorch separately from its [official Windows installation selector](https://pytorch.org/get-started/locally/), selecting Pip, Python and the CUDA build suited to the installed NVIDIA driver. The official Windows wheel supports Python 3.10–3.14. Verify with `.\.venv\Scripts\python.exe -c "import torch; print(torch.__version__, torch.cuda.is_available())"`. After setup, authorize model files with the GUI's **Set Up Vision Model** action or `.\.venv\Scripts\python.exe setup_vision_model.py --allow-download`. This downloads and verifies the pinned revision without loading PyTorch. Run `.\.venv\Scripts\python.exe benchmark_vision.py --synthetic-smoke` for a generated-image smoke test, or pass `--video <one-local-video>` for a local diagnostic. Do not pass a directory; the benchmark never searches for a sample. SmolVLM2 uses the [Transformers chat template](https://huggingface.co/docs/transformers/model_doc/smolvlm); tensors are moved to the model device and only floating point `pixel_values` are cast to its model dtype.

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

`python golden_set.py evaluation/golden_set.template.json Shorts_Output/<source>/analysis.json` compares system candidates with human annotations. Replace the template example with actual human judgements first; the evaluator refuses to report metrics before five labels.

Whisper loads only after **Start Automatic Clipping** and is reused across videos in one batch. Automatic mode prefers CUDA `int8_float16` when the installed CTranslate2 runtime reports support, with CPU int8 fallback; CUDA OOM during inference retries once on CPU. Transcription cache entries are keyed to the source file and transcription settings and are replaced only after a complete successful transcription.

Run `python benchmark_models.py` to record hardware information and initialize supported modes using models already present in the Hugging Face cache. This benchmark never downloads weights or searches for audio. Transcription timings run only when an explicit sample is supplied with `--audio-sample <path>`; the report says `NOT RUN` otherwise. The RTX 3050 float16 smoke test is skipped unless at least 2048 MiB of VRAM is free.

Run `python benchmark_vision.py` for a local-only dependency/cache/hardware diagnostic. Real inference requires an explicit `--video` path or `--synthetic-smoke`. Benchmarks never download model files; only the separately confirmed setup command downloads weights.
