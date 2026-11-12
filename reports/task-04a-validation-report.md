# Task 04A — Validation and corrections

## Corrections

- Replaced the GUI's benchmark invocation with `setup_vision_model.py --allow-download`. The GUI asks before starting the downloader and streams its progress to the log. The downloader uses the pinned model ID and immutable revision from `vision.py`, requests only inference-related files (not ONNX exports), checks a Hugging Face dry-run plan and available disk space, then verifies the exact snapshot directory and required config, tokenizer, processor, chat-template and weight files. It does not import PyTorch or instantiate the model. Missing Hub dependency and network/access errors are surfaced. Benchmark runs cannot download model files.
- Added separate GUI status for missing vision packages, missing PyTorch, missing/unverified weights, download progress, verified weights, ready/CUDA or CPU fallback, and model initialization failure. Installation instructions use this repository's `.venv`; PyTorch remains a separate selection from its official Windows/Pip/CUDA installer. PyTorch currently documents Windows Python 3.10–3.14 support; this project's Python 3.14.7 is in that range. The GUI and README point to the official selector instead of pinning a possibly incompatible CUDA wheel. [PyTorch Windows installation guidance](https://pytorch.org/get-started/locally/)
- Updated SmolVLM2 messages to retain ordered local image paths. Processor output is validated; floating `pixel_values` move to the model device and dtype, while integer token IDs and attention masks only move devices and keep their integer dtype. The documented SmolVLM processor exposes float pixel values and an integer image attention mask. [SmolVLM Transformers API](https://huggingface.co/docs/transformers/model_doc/smolvlm), [multimodal local-path and chat-template format](https://huggingface.co/docs/transformers/chat_templating_multimodal)
- Added a generated-image benchmark mode and split initialization, inference, device, precision, before/after VRAM and sampled peak VRAM fields for when inference can run. `--video` still accepts one explicit file and does not search for videos.
- Implemented category-aware selection with a configurable default weight of `0.05`. Category preference activates only when multiple valid non-`UNCERTAIN` labels exist among candidates above the existing quality floor. It does not change heuristic or semantic scores, lower the floor, or remove temporal separation. Selected candidates record their temporal factor and category bonus in `analysis.json`.
- Recorded source-by-source model lifecycle: Whisper is released before vision loads, vision is released at the end of each source, and Whisper initializes again for the next source. A mocked two-source test verifies those releases. No batch model reuse or switching-time performance is claimed.

## Verification results

- Full suite: **68 passed, 0 failed, 0 skipped** (`.venv\\Scripts\\python.exe -m unittest discover -s tests`).
- Focused vision and candidate tests: **42 passed**.
- Python compilation and `git diff --check`: passed.
- Category-diversity fixtures selected an alternate valid category at a test weight of `0.15`; the stored bonus matched the configured value. Single-category and heuristic-only selections recorded zero category bonus.
- Generated-image smoke attempt created **2 synthetic images**. It stopped before model initialization because `torch`, `transformers`, `accelerate`, and Pillow are missing. `huggingface_hub` is available. Weights are missing/unverified. This was **not a real inference run**.
- Hardware snapshot during the smoke attempt: NVIDIA RTX 3050 A Laptop GPU, **4094 MiB total / 2545 MiB free**. CPU and GPU model inference time, precision, post-load VRAM, and peak inference VRAM: **NOT RUN**. Real-video classification accuracy: **NOT RUN**; no sample video was supplied.
- The downloader flow, pinned revision verification, setup button command, tensor casting, multi-image order, category selection, and per-source model releases were tested with mocks. No network download was performed. Model inference against the installed Transformers API remains **NOT RUN**.

## Changed files

- `setup_vision_model.py`, `vision.py`, `benchmark_vision.py`
- `main.py`, `clipper.py`, `requirements-vision.txt`, `README.md`
- `tests/test_vision.py`, `tests/test_candidate_selection.py`
- `reports/task-04-benchmark.json`, `reports/task-04-benchmark.md`, `reports/task-04a-validation-report.md`, `reports/IMPLEMENTATION_STATUS.md`

## Git and limits

- Branch: `main`.
- Implementation commit: `dd6c31b` (`Correct Task 04 setup and validate semantic selection`).
- Model weights were **not downloaded**. Real inference, CUDA/CPU performance, and actual multi-source model-switch latency remain unverified until the optional packages and pinned model files are installed and smoke tests run.
- This validates input plumbing and selection behavior; it does not establish reliable category predictions across anime, films, cartoons, or other footage.

Task 05 was not started.
