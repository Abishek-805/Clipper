# Task 05C completion report

**Status: software gaps implemented; end-user quality acceptance remains partial. Task 06 was not started.**

## Delivered

- **Smart framing:** [reframing.py](../reframing.py) segments clips at existing scene cuts, analyzes at most 48 low-resolution samples, combines built-in OpenCV Haar face boxes with edge/optical-flow saliency, smooths focus within each shot, validates crop geometry, and resets at each cut. High-confidence single-subject/saliency crops use the portrait frame; multiple distant faces, portrait sources, dispersed saliency and errors preserve the full image with blurred contain fill. Each clip records its evidence and a debug image when frames are readable. [clipper.py](../clipper.py) passes per-shot plans to the existing FFmpeg render/validation path.
- **Caption safe areas:** [captions.py](../captions.py) scores lower/middle/upper anchors against normalized face, action and source-text boxes, with stable per-shot placement and a user override. The ASS events use the chosen shot anchor. Source-text detection is an OpenCV morphology heuristic, not OCR.
- **Clip review:** [main.py](../main.py) adds a results table with a thumbnail and Play MP4, Reveal Folder, Open SRT, View Analysis, Contact Sheet and Reframe Debug actions. Content type, highlight preference, and Auto/Short/Standard/Story duration controls are persisted; values are passed into process settings and the optional editorial prompt. Single-episode mode still processes only the explicit file.
- **Quality tooling:** [storyboard.py](../storyboard.py) writes a bounded index and up to eight candidate evidence folders with no more than five frames each. [golden_set.py](../golden_set.py) validates human annotation schema and calculates one-to-one temporal matching, interval IoU, keep precision/recall, category agreement, boundary error, framing and caption acceptance. It returns `NOT ENOUGH DATA` below five annotations. The example human annotation file is [golden_set.template.json](../evaluation/golden_set.template.json).
- Cache settings include `PLAN_VERSION`, storyboard version and preferences that affect output. Task 04B's SQLite schema and queue logic were not changed. No dependencies were installed or updated, and no model weights or media were downloaded.

## Changed files

Application: `reframing.py`, `captions.py`, `clipper.py`, `editorial.py`, `main.py`, `storyboard.py`, `golden_set.py`.

Tests/data: `tests/test_task05.py`, `tests/test_clipper.py`, `evaluation/golden_set.template.json`.

Reports: this report, `task-05-final-production-engine-report.md`, `IMPLEMENTATION_STATUS.md`, and `REFERENCE_ARCHITECTURE_DECISIONS.md`. `THIRD_PARTY_NOTICES.md` did not need an update because no third-party code/assets or dependency was added.

## Verification results

Commands run from the project root:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_task05 -v
.\.venv\Scripts\python.exe -m unittest tests.test_clipper.ClipperTests.test_render_uses_shot_specific_crop_then_safe_contain -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m compileall -q .
git diff --check
```

- Focused Task 05 tests: **14 passed, 0 failed**.
- Shot-specific synthetic FFmpeg render (crop then contain): **1 passed, 0 failed**; output validation confirmed 1080×1920.
- Full active-venv suite: **105 passed, 0 failed, 0 skipped** (after the final renderer fix; no Faster-Whisper shim).
- Compilation and whitespace checks passed. Initial integration runs exposed and fixed a missing debug-render import and sample-aspect-ratio mismatch between crop and contain shots.
- Tests use synthetic videos, mocks and generated labels only. No actual movie, anime episode, cartoon or web series was processed. No human golden-set labels exist yet, so no editorial-quality measurements are available.
- `pytest` was not used; the documented suite uses `unittest`. No GUI display-based visual inspection was performed.

## Environment and model readiness

Audited with `.venv` on Windows 11:

- Python **3.14.7**; OpenCV **5.0.0**; PySceneDetect **0.7.3**; Faster-Whisper **1.2.1**; PyAV **19.0.2**.
- FFmpeg/FFprobe **9.0.2**, libass, libx264 and NVENC encoder self-tests passed.
- NVIDIA GeForce RTX 3050 A Laptop GPU, **4094 MiB**; driver **616.56**. CTranslate2 reported one CUDA device and supports int8/float16 modes. This is hardware/runtime capability only, not evidence that transcription was run.
- PyTorch, Transformers, Accelerate and Pillow are absent from `.venv`; SmolVLM2 weights are not verified, `vision_installation_status.ready` is false, and Faster-Whisper `small` weights are not cached.
- No local narrative model endpoint is configured. Thus local LLM/vision/Whisper inference and peak VRAM use were not tested. CPU/GPU performance and overnight throughput remain unmeasured.

## Data and compatibility

- Original sources are read only; rendered files and SRTs remain in each source's output folder.
- Candidate storyboard and reframe debug artifacts are additive. Existing clip reports remain readable; no database migration occurred.
- The engine versions are included in settings so prior completed outputs do not masquerade as products of the new framing/storyboard path. Existing low-level motion/audio/scene cache is source-keyed and unchanged; semantic cache behavior and pinned model revision are unchanged.
- Rendered composition segments are normalized to square sample aspect ratio before concat; the synthetic crop/contain integration test exercises this case.

## References and limits

The implementation follows the concepts summarized in [REFERENCE_ARCHITECTURE_DECISIONS.md](REFERENCE_ARCHITECTURE_DECISIONS.md), including OpenClipper scene-reset framing and contain fallback, Cutawan shot-aware face/caption handling, and FFmpeg deterministic rendering. No third-party implementation was copied. These unit and synthetic integration results do not show that face detection or crop choices are good on anime/cartoon scenes. The lack of subject identity tracking, OCR, real model inference, visual GUI inspection and human-rated quality means Task 05 remains partial until the next authorized single-episode acceptance milestone.

## Git delivery

The starting branch was `main` at `b4d44ba490b6c4e27c026feb7f1205bddba1c3a5`, clean and equal to `origin/main`. The original Task 05 implementation is `7b0636b`. Task 05C implementation commit: `2d968ab` (`Complete Task 05C framing and review tools`). Documentation is committed separately; its commit ID, push result and final working-tree state are stated in the final delivery.
