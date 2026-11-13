# Task 06G.1 — Qwen3-VL experiment completion and audit

**Status: experiment completed; real inference succeeded, editorial acceptance failed.**

## Scope and Git baseline

- Branch: `feature/task-06g-qwen-vl-real-inference`.
- Task 06F baseline and starting `HEAD`: `6be5077c8eb98b601a351ed5b064dea309caf2f6`.
- The branch began at the exact Task 06F commit. It was not based on Task 06D.3, and no other branch was checked out or changed.
- This task closed the existing experiment. It did not run a new full-episode pass, alter the production selector, captions, renderer, GUI, or queue, or start Task 07.

## Pinned local runtime and model

The previously approved files were used; this task downloaded or installed nothing.

| Asset | Version / revision | SHA-256 |
| --- | --- | --- |
| llama.cpp | b12284, source `9e9619d` | build archive `2bf31552cee22652f76a108461a1074548ba84e73294dad1061493875255673f` |
| CUDA runtime bundle | CUDA 12.4 x64 | `8c79a9b226de4b3cacfd1f83d24f962d0773be79f1e7b75c6af4ded7e32ae1d6` |
| Qwen3-VL-2B-Instruct language model | revision `52d6c8ffea26cc873ac5ad116f8631268d7eb503`, Q4_K_M, 1,107,409,952 bytes | `089d75c52f4b7ffc56ba998ffc50aae89fcafc755f9e7208aacca281dca6c2ae` |
| Qwen3-VL vision projector | same revision, Q8_0, 445,053,216 bytes | `f9a68fabba69c3b81e153367b2c7521030b0fa8bb0de400c9599c8e6725f9c82` |

The model and runtime remain in local ignored locations. They, the source episode, and generated analysis/review assets are not part of the Git change. The server used a loopback address only (`127.0.0.1:8080`).

## Hardware and inference placement

The machine has an RTX 3050 A Laptop GPU with 4,094 MiB dedicated VRAM and 16 GiB system RAM. The llama.cpp log reports **27 of 29 model layers offloaded to CUDA**, with two layers retained on CPU. This is mixed CPU/GPU inference, not full GPU execution.

The server log reports about 989 MiB of CUDA model weights, 416 MiB KV cache, 34 MiB model compute buffer, and 322 MiB multimodal compute buffer. Saved smoke-test samples measured 3,398 MiB GPU use / 495 MiB free and 3,413 MiB / 480 MiB free. The server working set peaked at 1,881 MiB in those samples, with 4.36 GiB system RAM free. Episode-run sampling observed about 3.4 GiB GPU use, a peak near 3,442 MiB, and approximately 451 MiB free. A peak host-RAM sample for the full episode pass was not retained.

GPU headroom was narrow. The test proves that this pinned model/runtime can run on this laptop, but does not establish comfortable capacity for concurrent GPU workloads or a larger context.

## Inference evidence and semantic findings

- **Single image:** actual local model inference completed on a cached source frame. The response was valid JSON. It described an animated character and scene, but made unsupported claims including flight and spell-casting. Its high confidence was not calibrated to the evidence.
- **Four-frame sequence:** actual chronological sequence inference completed. A creature encounter was described as dynamic/action, while suggesting unsupported movement such as flying/glowing. The description changed with sampling density.
- **4/8/12-frame comparison:** on the creature sequence, measured times were 5.005 / 7.215 / 6.355 seconds. On the likely-fight sequence they were 5.544 / 6.424 / 6.790 seconds. On the sword-pose negative control they were 4.855 / 6.713 / 7.149 seconds. These are latency observations, not evidence that more frames improve description quality.
- **Dialogue-context experiment:** real inference completed in 9.214 seconds (1,223 prompt and 430 completion tokens). The transcript concerned skill-activation instruction, but the model interpreted the visuals and words as a likely battle. Dialogue and visual descriptions remain distinct evidence fields; their connection was still wrongly inferred.
- **Confidence and event labels:** responses often used confidence near 0.95 and every one of the 108 window responses returned an actions list. The category histogram was weakly discriminative (50 `scene`, 12 `animation`, 11 `action`, 7 `landscape`, 5 `visual`, remaining categories sparse). Confidence and category therefore cannot be treated as editorial truth.

### Whole-episode cache and synthesis

The completed cached pass covers the 23m42.59s episode: all **862 cached frames** and **108 chronological windows** are represented, and **36 active chapter context summaries** appear in the episode memory. Each window record includes the source fingerprint, pinned model/runtime, prompt/schema identifiers, timestamps and transcript fingerprint in its cache key. The final summary reports complete frame coverage and 108 analyzed windows; static validation also confirmed 108 persisted window records, ordered timestamps, and matching window bounds.

The original two-minute grouping exceeded the 4,096-token context. The saved run uses shorter groups (roughly 30–45 seconds). A final schema-constrained synthesis still failed as an episode summary: it repeated opening/title/location material and its text fields were truncated mid-phrase. An earlier roll-up invented a transcript turn ID; the adapter schema was tightened to allow only source-provided turn IDs. This safeguard does not make the model’s interpretation of a valid turn correct.

The 108-window category scan found 11 `action` windows. Six had temporal IoU at least 0.1 with Task 06F moments and four with the five production selections. The two action windows not overlapping those intervals were adjacent to selected clips, so this pass did not establish any independent new highlight.

## Human review of saved previews

All three existing local previews were played through to completion in VLC. This review is separate from model output and generated no human labels in the evaluation dataset.

| Preview | Source interval | Human playback observation |
| --- | --- | --- |
| Creature encounter | about 239.5–265.5s (26s) | Initially the character handles/stands beside the large creature; near the end a second character makes a visible sword attack on it. The clip contains a real action payoff, but the setup occupies much of its opening. The model’s extra flying/spell-casting claims were not supported by the playback. |
| Likely fight | about 1285–1311s (26s) | Shows a character’s attack and glowing effects, then transitions into the “Sword Art Online” title sequence at the end. The transition weakens the ending as a self-contained clip; model labels alone do not identify that boundary problem. |
| Sword-pose negative control | about 394–428s (34s) | Shows a character holding/raising a sword during a conversation. There is no sustained attack, impact, and consequence progression across the preview. The model’s broad `scene` label is preferable to its likely-fight labels, but does not demonstrate full scene understanding. |

### Review board audit

The generated `candidate_review_06g/index.html` was parsed from disk. It contains 108 window cards, 862 linked frame images, separate Task 06F proposal and existing production-selection sections, unreviewed-model labeling, transcript labeling, and uncertainty text. All 862 referenced frame paths resolve. No deterministic mock output is presented as inference. The board contains no JavaScript, so a JavaScript syntax check was not applicable. Opening the local `file:` page in the in-app browser was blocked by its URL security policy; browser rendering was not visually verified.

## Server shutdown

The running PID was checked against the exact `.runtime` llama-server executable, command line, approved model and projector, and its listener. It belonged to the approved process and listened only on `127.0.0.1:8080`. A graceful Ctrl+Break to its console stopped it. Follow-up checks found that the process exited and port 8080 was released. No unrelated process was terminated.

## Validation results

- Focused Task 06G tests: **21 passed, 0 failed** (`tests.test_episode_vlm`, `tests.test_episode_brain`). One indentation error and one mismatched expectation were found during the first run and corrected; the focused suite was rerun successfully.
- Full project suite: **162 passed, 0 failed** with `.venv\Scripts\python.exe -m unittest discover -s tests -v` (38.371 seconds). FFmpeg printed expected `moov atom not found` messages from corrupt-media test fixtures; the suite passed.
- Python `compileall`: passed for the source/test directories after correction.
- `git diff --check`: passed.
- Cached episode/review board validation: passed for complete coverage, 108 cached windows and cards, chronological source timestamps, and all frame-image links. No full episode analysis was rerun.
- No separate JavaScript validation was run because the review board has no script blocks.

## Changed files

- `.gitignore` — ignore `.runtime/` to keep local binaries and CUDA files out of Git.
- `evaluation/llama_cpp_vlm.py` — request concise constrained JSON, disable model thinking mode, retain token usage, and add bounded visual/transcript context analysis with exact turn/frame validation.
- `evaluation/episode_vlm.py` — implement resumable cached chronological window analysis, bounded chapter context, episode memory, and a local review board.
- `tests/test_episode_brain.py` — adapter request, context reference, and boundary fallback tests.
- `tests/test_episode_vlm.py` — cache, chronology, resume, and review-board generation tests.
- `reports/task-06g-qwen-vl-real-inference-report.md` — this completion and evidence report.
- `reports/IMPLEMENTATION_STATUS.md` — replace the obsolete preflight status with measured results.

## Acceptance statement and limitations

**REAL MODEL INFERENCE: SUCCESSFUL.**

**WHOLE-EPISODE VISUAL COVERAGE: COMPLETED.**

**COHERENT EPISODE-LEVEL PLOT UNDERSTANDING: NOT ACCEPTED.**

**EDITORIAL QUALITY: NOT ACCEPTED.**

**PRODUCTION INTEGRATION: NOT PERFORMED.**

All model descriptions and proposed categories remain unreviewed predictions. The model hallucinates visual details, connects dialogue to visuals incorrectly, uses overconfident scores, and fails episode-level synthesis. No change was made to production selection or rendering, and no Task 07 work was started.
