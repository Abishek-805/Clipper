# Task 05 production-engine implementation report

**Status: partially implemented; Task 05 real-media acceptance is incomplete. Task 06 was not started.**

Task 05C preserves the existing local editorial assessment, variable-duration candidate engine, adaptive visual sampling, caption presets, single-episode workflow, persistent queue, and blurred-background render fallback. The four previously identified software gaps have working implementations; real visual/editorial quality is still unverified. See [Task 05 completion report](task-05-completion-report.md) for implementation and test evidence.

## Implemented

- Scene-wise reframing plans combine built-in OpenCV face detection with low-resolution edge and optical-flow saliency. Crop paths are bounded to even-pixel rectangles, smoothed within each shot, and reset at hard cuts. Multiple widely separated faces, portrait sources, diffuse saliency, failed detection, and analysis errors use full-frame containment. Per-shot FFmpeg segments are normalized and concatenated; the existing blur-fill path remains available.
- Automatic caption anchors score overlap against detected face/action/source-text regions and use a per-shot placement, with a user override and ASS positions. Text-like source-subtitle detection is a lightweight morphology heuristic, not OCR.
- The GUI lists clips with thumbnails and provides play, reveal, SRT, analysis, contact-sheet, and reframe-debug actions. Content type, highlight preference, caption placement/style and length profile are persisted and included in processing settings; type/highlight preferences reach the optional local editorial prompt.
- A capped candidate storyboard stores review frames, source times, transcript excerpts, score/category, selection or rejection reason, evidence and uncertainty. `golden_set.py` validates human labels and computes interval IoU, boundary error, keep precision/recall, category agreement, framing acceptance and caption acceptance. It reports `NOT ENOUGH DATA` below five labels and does not invent labels.
- Cache fingerprints include the reframing/storyboard engine versions and output-affecting preferences. No SQLite schema or Task 04B queue behavior changed. Single-episode processing remains separate from folder scanning and queue writes.
- No dependencies were installed, no model weights were downloaded, no network inference was used, and no real episode/movie was processed. No source code/assets were copied from reference projects; the third-party notices did not require changes.

## Acceptance limits

- Face handling is lightweight detection plus per-shot smoothing, not persistent subject identity tracking. Saliency tracks active columns, not semantic subject identity. The two-subject mode preserves the whole source in contain layout rather than synthesizing a split-screen.
- Source subtitle detection is heuristic and may miss stylized, low-contrast, or upper-frame text. The action/saliency region is an approximation. Caption placement is therefore best-effort and may overlap content; fixed anchors remain available.
- The clip list and operating-system actions are implemented and exercised by the application code, but a human visual review of its rendered desktop was not performed in this run.
- The local narrative model has not been configured or run. SmolVLM2 dependencies/weights and Faster-Whisper `small` weights are unavailable, so model quality, GPU inference VRAM, and end-to-end throughput are unverified. Synthetic and mocked tests do not establish production editorial quality.
- Keep Task 05 marked partial until single-episode acceptance with authorized real media has been watched and evaluated by a human. Task 06 remains not started.

## Prior Task 05 implementation

Implementation commit `7b0636b` (`Implement Task 05 editorial and review pipeline`) added the editorial assessment and initial review/caption foundation. Task 05C changes and final test evidence are documented in [task-05-completion-report.md](task-05-completion-report.md). The implementation and report commits are separate; exact Git delivery state is reported in the completion report and final delivery.
