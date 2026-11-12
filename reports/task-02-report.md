# Task 02 — GPU optimization and model management

## Implemented

- Added a lazy, single-instance `ModelManager`. The GUI creates it only inside the explicitly started worker, reuses it across videos in the batch, serializes inference, and releases it when the batch ends.
- Automatic mode selects CUDA `int8_float16` when CTranslate2 reports support. It uses CUDA float16 only when that precision is supported and at least 2048 MiB is free; otherwise it selects CPU int8. CUDA model initialization failures fall back to CPU int8. CUDA errors during transcription retry once on CPU int8.
- Faster-Whisper's lazy segment iterator is consumed fully inside the manager so inference timing, cancellation, and memory sampling cover the actual transcription work.
- Added status reporting for the selected device/precision, initialization time, CUDA fallback reason, VRAM before/after model load and release, and peak VRAM sampled during transcription. `nvidia-smi` reports whole-device memory here, so these are system-wide GPU memory readings rather than process-only allocations.
- Transcription cache schema 2 fingerprints the source path, size, modification time, model identity, language, and transcription settings. It only replaces a cache after successful full transcription; failed/cancelled runs keep any earlier cache intact.
- Added `benchmark_models.py`. It reads only the local model cache, never downloads weights or searches for sample audio, and runs speech timing only for a path passed explicitly with `--audio-sample`.

## Verification

Environment checked using the project virtual environment on 2026-11-12:

- Python 3.14.7; Faster-Whisper 1.2.1; CTranslate2 4.9.0.
- NVIDIA GeForce RTX 3050 A Laptop GPU, 4094 MiB; driver 616.56; one CTranslate2 CUDA device.
- CUDA compute types reported: bfloat16, float16, float32, int8, int8_bfloat16, int8_float16, and int8_float32.
- `nvidia-smi` snapshot before benchmark: 2475 MiB free, 1418 MiB used.
- Focused model-manager tests: **9 passed**.
- Full test suite: **26 passed, 0 failed, 0 skipped** (`.venv\Scripts\python.exe -m unittest discover -s tests -v`). This includes the startup-safety and synthetic rendering tests.
- `python diagnostics.py`: passed FFmpeg/FFprobe, libx264, NVENC, libass, NVIDIA detection, and CUDA runtime checks.

## Benchmark outcome and limits

`python benchmark_models.py` completed and wrote [task-02-benchmark.md](task-02-benchmark.md) and [task-02-benchmark.json](task-02-benchmark.json). The local Hugging Face cache did not contain a usable `Systran/faster-whisper-small` snapshot, so CUDA int8-float16, CUDA float16, and CPU int8 model initialization were **skipped**. No model was downloaded. No approved audio sample was provided, so transcription runtime, real-time factor, language detection, segment count, and inference peak VRAM are **NOT RUN**. Model initialization latency and actual inference performance therefore remain unverified on this machine.

No personal folders or user video directories were scanned. The benchmark only checked the explicit Hugging Face cache through Faster-Whisper's `local_files_only=True` API.

## Files changed

- `model_manager.py`
- `clipper.py`
- `main.py`
- `benchmark_models.py`
- `tests/test_model_manager.py`
- `tests/test_clipper.py`
- `README.md`
- `reports/task-02-benchmark.md`
- `reports/task-02-benchmark.json`

Task 03 was not started.
