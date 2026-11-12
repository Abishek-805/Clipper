# Task 04 — Optional local semantic scene analysis

## Implemented

- Added an opt-in GUI checkbox. The VLM is neither loaded nor downloaded at application startup. The **Set Up Vision Model** action asks for confirmation before downloading the pinned SmolVLM2-500M-Video-Instruct revision (`7b375e1b73b11138ff12fe22c8f2822d8fe03467`). Python packages and PyTorch remain a separate manual installation.
- Added `VisionModelManager` with lazy imports/load, local-cache-only default, explicit download permission, GPU free-memory preflight (2,200 MiB threshold), CUDA FP16/SDPA, CPU FP32 fallback, release, and one CPU retry for CUDA OOM.
- Samples up to six ordered frames across each eligible candidate, resizes to at most 512 pixels wide, rejects unreadable/near-black/duplicate frames, and deletes temporary frame files. It examines at most 12 top-scored valid candidates per source.
- Validates constrained JSON categories, a 0–1 uncalibrated model score, short reason text, and evidence timestamps that match sampled frames. Invalid output gets one retry; failures preserve the Task 03 heuristic ranking.
- Blends 65% heuristic score with 35% semantic score and adds a small category novelty preference during selection. Records the original heuristic score, model score/category/reason/evidence and overall analysis status in `analysis.json`.
- Releases Whisper before model initialization to avoid co-resident large GPU models. English transcript text is optional context only; other detected languages are not passed as understood dialogue. Existing transcript word timing remains the subtitle authority.
- Adds atomic semantic cache entries keyed by source path/stat, candidate interval, pinned model revision, prompt/schema/frame settings, transcript hash and language. Bumps Task 03 signal cache schema and analysis-engine version to invalidate stale signals.
- Added `benchmark_vision.py`, which accepts one explicit local `--video`, optional category ground truth and a deliberate `--allow-model-download` flag. It does not search for samples. Added optional dependency notes and setup instructions.

## Verification

- Python compilation: passed for the modified application, benchmark and tests.
- Full suite: **58 passed, 0 failed, 0 skipped** (`.venv\\Scripts\\python.exe -m unittest discover -s tests`).
- Synthetic Task 03 selector benchmark: recall **1.0000**, interval precision **1.0000**, mean best IoU **0.9958**, duplicate rate **0**, invalid boundaries **0**.
- `git diff --check`: passed.
- Vision benchmark diagnostic ran without download permission: **real inference NOT RUN**, **quality metrics NOT RUN**. Detected RTX 3050 A Laptop GPU, 4094 MiB total and 2451 MiB free at the diagnostic snapshot. `torch`, `transformers`, `accelerate`, and Pillow were missing. No local sample video was supplied.

## Limits

The VLM path has not run on this machine, so model loading, CUDA memory use, inference latency and semantic category quality remain unverified. The 2,200 MiB free-memory threshold is a conservative preflight, not a guarantee against later OOM; OOM gets one CPU retry. Category and semantic scores are uncalibrated. The optional model card describes English support; non-English semantic interpretation is intentionally not claimed. No model weights or packages were downloaded for this task.

## Changed files

- `vision.py`, `clipper.py`, `main.py`, `benchmark_vision.py`
- `requirements-vision.txt`, `README.md`
- `tests/test_vision.py`, `tests/test_candidate_selection.py`
- `reports/task-04-benchmark.json`, `reports/task-04-benchmark.md`
- `reports/task-04-report.md`, `reports/IMPLEMENTATION_STATUS.md`

Task 05 was not started.
