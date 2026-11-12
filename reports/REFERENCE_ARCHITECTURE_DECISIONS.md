# Task 05 architecture decisions

## Repository inspection

Task 01–04B paths were inspected before editing: `clipper.py` owns ingestion, candidate generation, boundary validation, rendering and manifests; `vision.py` owns bounded frame extraction, the pinned SmolVLM2 manager and semantic cache; `queue_runner.py` calls `process_video()` in analysis, semantic and render passes; `nightly_queue.py` owns the existing SQLite schema and recovery; `main.py` owns startup safety and GUI controls. Existing `process_video()` callers and test fixtures were located. No schema migration was needed.

## Reference concepts adopted

- **AutoClip**: global word indices, transcript context, explicit local provider boundaries, strict candidate validation, caption grouping, and resumable/cache-aware stages. Clipper uses its own short-form narrative prompt, not the talking-head prompt. Reviewed source: `highlights.py`, `boundaries.py`, `captions.py`, `providers/base.py`, and `docs/ARCHITECTURE.md`.
- **Cutawan**: hook/build/payoff evaluation, complete beats, natural cut boundaries, caption style options, and per-shot reframing as a design goal. Reviewed `highlights.ts`, `faces.ts`, `captionStyles.ts`, and shared types.
- **OpenClipper**: scene cuts must reset camera/focus state; crop must be selected from saliency and tracking confidence, with contain/fallback layouts. Reviewed `build-track.util.ts` and AutoFlip types. Clipper keeps its existing full-frame blurred contain renderer where tracking confidence is absent.
- **MediaPipe AutoFlip**: low-rate feature extraction, scene-specific crop planning, salient-region fusion, and stable camera-motion principles. The project is not imported because it would bring a much larger build/runtime stack than this Python/Tkinter application needs.
- **Faster-Whisper**: existing word-level timestamps remain the source of subtitle and cut timecodes; no transcription code was copied.
- **SmolVLM2**: the existing pinned revision and multimodal path remain unchanged. Sampling now has a bounded adaptive timestamp plan; no unsupported Transformers API was introduced.
- **FFmpeg**: existing validated FFmpeg rendering, ASS burn-in, audio normalization, atomic promotion, and FFprobe verification remain in place.

Reference source links: [AutoClip](https://github.com/artbyjazi/autoclip), [Cutawan](https://github.com/JeremySNR/cutawan), [OpenClipper](https://github.com/GrepCut/OpenClipper), [MediaPipe AutoFlip](https://github.com/google-ai-edge/mediapipe/blob/master/docs/solutions/autoflip.md), [Faster-Whisper](https://github.com/SYSTRAN/faster-whisper), [SmolVLM2 model card](https://huggingface.co/HuggingFaceTB/SmolVLM2-500M-Video-Instruct), and [FFmpeg filters](https://ffmpeg.org/ffmpeg-filters.html).

## Task 05C implementation update

- OpenClipper's scene-local focus and fallback concepts are now applied by `reframing.py`. Clipper samples at most 48 frames per selected clip, uses OpenCV's installed Haar cascade plus edge/optical-flow column saliency, smooths focus only inside each detected shot, and validates even crop bounds. No persistent subject identity tracker or heavyweight object detector was added. Multiple distant faces and uncertain animation/action fall back to full-frame contain.
- Each shot is rendered independently in FFmpeg and concatenated after normalizing dimensions and sample aspect ratio. Scene cuts reset crop and caption placement state. The old blurred-background fill remains the contain path.
- Caption placement scores normalized face/action/source-text regions against three anchors. Source text uses a simple lower-frame morphology heuristic, not OCR. Fixed placement overrides continue to work. This is best-effort obstruction avoidance, not semantic face/action understanding.
- The GUI now displays individual validated clips and operating-system actions for playback, reveal, SRT, manifest, contact sheet and reframe debug. Content type/highlight preferences are saved, fingerprinted, and passed to the optional editorial prompt. A length profile maps to existing clip duration bounds.
- `storyboard.py` records a bounded candidate sample archive and `golden_set.py` supplies a human-only annotation schema and evaluator. Evaluation needs at least five labels; no labels or accuracy are fabricated.
- No dependency, weights, database schema, queue code, or pinned model revision changed. The active environment has OpenCV, Faster-Whisper and CUDA-capable CTranslate2, but optional SmolVLM2 packages/weights and the Whisper small model weights are unavailable. No model inference was run.

## Existing boundaries and defaults

- Activity scoring still proposes moments. An optional narrative assessment is separate, deterministic at the scoring layer, and only runs when the user configures both a loopback endpoint and a model name. It will not send transcript text to a non-loopback server, follow redirects off loopback, call a cloud provider, or download a model. Missing/invalid provider configuration is reported as unavailable; existing activity selection remains functional.
- Editorial score dimensions use the prompt's initial weights (hook 20%, coherence 20%, payoff 20%, impact 15%, self-containment 10%, visual clarity 10%, audio clarity 5%). These are engineering defaults, not calibrated engagement predictions. Model scores are bounded 0–5, and selected model-assessed candidates must meet the 0.58 quality floor.
- Editorial results use prompt version `editorial-v1`; frame sampling is capped at 12 timestamps and its semantic cache key changes with `candidate-v2-adaptive`. Text is cached separately. Candidate sources and the SQLite queue remain unchanged.
- Caption styles are `bold_pop`, `karaoke`, `cinematic_minimal`, and `boxed`; captions group at punctuation, pauses, width and style limits. SRT and ASS use real word timestamps. Placement can be fixed or automatic using available per-shot obstruction heuristics.
- Single-episode mode calls the existing `process_video()` with the one chosen path. It does not invoke directory scanning, create queue records, or change the nightly backlog.
- A contact sheet, `clip_review.md`, capped candidate storyboard and per-clip playback/reveal/review controls are available after successful renders.

## Licensing and code reuse

No source code, models, fonts or media assets were copied from the reference repositories. Their licenses were inspected: AutoClip and Cutawan are MIT; OpenClipper's source is MIT. The SmolVLM2 model card states Apache-2.0 and English NLP support; Clipper's existing pinned model integration is unchanged. Fonts are not bundled or fetched: ASS currently names Arial as a system fallback. The repository's existing `requirements.txt` entries were not changed. See [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).

## Limits that affect the next acceptance step

Face detections are not identity tracking; animation may not yield face detections; saliency may center motion without understanding its narrative importance. There is no OCR or calibrated semantic crop model. The local vision and editorial models were not run. Golden-set evaluation is tooling only until humans annotate and review footage. Task 05 stays partial pending authorized single-episode visual acceptance; Task 06 work was not started.
