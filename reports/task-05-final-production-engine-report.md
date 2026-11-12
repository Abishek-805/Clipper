# Task 05 production-engine implementation report

**Status: partially implemented; Task 05 acceptance is not complete. Task 06 was not started.**

## Changes delivered

- Added an optional editorial assessment stage in `editorial.py`. It accepts only an explicitly configured loopback OpenAI-compatible endpoint and a model name. It rejects external URLs and non-loopback redirects, validates the structured response and global transcript word indices, calculates the requested weighted score deterministically, and applies a 0.58 quality floor to model-assessed candidates. Editorial scores are called editorial scores; they do not claim to predict views or virality.
- Kept the Task 03 motion/audio/scene/dialogue engine as proposal generation. If no local narrative model is configured or a request fails, the manifest records the unavailable/fallback status and existing activity scoring remains available.
- Added prompt-versioned editorial caching. Caption and model settings participate in the source processing settings fingerprint; semantic frame cache keys changed to `candidate-v2-adaptive`.
- Increased visual frame sampling from six fixed points to a chronological, deduplicated budget of up to twelve timestamps. Sampling preserves start, middle, end and prioritized scene/motion/audio events. Existing SmolVLM2 model code and pinned weights revision were not changed. The manifest records sampled timestamps when extraction succeeds.
- Added `bold_pop`, `karaoke`, `cinematic_minimal` and `boxed` ASS styles; groups captions at punctuation, pauses, word count and line width. Caption placement can be set to lower/middle/upper. SRT and ASS continue to use Whisper word timings and are rendered by the existing validated FFmpeg path.
- Added a separate one-file GUI workflow that invokes `process_video()` with the chosen file. It does not call folder scanning or create/update nightly queue records. The mode limits the requested clip count to five and offers a source output-folder shortcut after completion.
- Added `clip_review.md` and a generated `clip_contact_sheet.png` for successful outputs. They supplement, and do not replace, `analysis.json` and per-clip SRT files.
- Kept the Task 04B SQLite schema and nightly queue logic intact. Added no package dependencies, model downloads, network model downloads, or real-media processing.

## Reference architecture study

The implementation was designed after inspecting the reference source files listed in [REFERENCE_ARCHITECTURE_DECISIONS.md](REFERENCE_ARCHITECTURE_DECISIONS.md). Adopted concepts include word-index timecodes, narrative hook/build/payoff scoring, local provider boundaries, scene-aware event frame sampling, caption grouping, and full-frame contain fallback. No third-party code, model weights, fonts or assets were copied. License audit notes are in [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).

## Test and environment results

- `python -m compileall -q .` — passed.
- Full `unittest` suite — **97 passed, 0 failed, 0 skipped** with this one-line temporary in-memory `faster_whisper` import shim (used because the package is not installed; existing model-manager cases inject mocked Whisper classes):
  `python -c "import sys,types,unittest; fw=types.ModuleType('faster_whisper'); fw.__path__=[]; utils=types.ModuleType('faster_whisper.utils'); utils.download_model=lambda *a,**k: None; fw.utils=utils; sys.modules['faster_whisper']=fw; sys.modules['faster_whisper.utils']=utils; suite=unittest.defaultTestLoader.discover('tests'); result=unittest.TextTestRunner(verbosity=1).run(suite); sys.exit(not result.wasSuccessful())"`
- After that full run, `python -m unittest discover -s tests -p test_task05.py -v` — **7 passed** after the final loopback credential/query validation change.
- `git diff --check` — passed.
- Targeted synthetic FFmpeg pipeline test — passed, including output metadata, unavailable-model reporting, `clip_review.md` and contact-sheet generation.
- Targeted startup/single-episode safety tests — **6 passed**.
- `python -m unittest discover -s tests -v` without the shim — cannot import `tests/test_model_manager.py` because `faster_whisper` is not installed in the active system Python.
- `python -m pytest -q` — unavailable because `pytest` is not installed in `.venv`.
- No dependencies or weights were installed. No actual episode/movie/anime/cartoon was opened or processed.

## Acceptance gaps and risks

This delivery is **not ready to claim the full Task 05 production acceptance criteria**:

- The app does not currently include reliable subject/person tracking or shot-by-shot crop paths. It retains the tested blurred-background, full-frame composition to avoid cropping important animation or action incorrectly.
- Caption placement is a user-selected fixed anchor; there is no automatic face/action/source-subtitle avoidance or per-shot stable placement analysis.
- The GUI has single-file selection and a source output-folder shortcut, but no per-clip thumbnail/result table with play, reveal, and analysis actions. Content-type/highlight-preference/profile controls are absent.
- No candidate storyboard archive or golden-set quality comparison tooling was added.
- The local narrative endpoint and model were not available for inference verification. SmolVLM2 dependencies and weights remain unavailable, so no real text/vision model result was generated. The current GPU inference, peak VRAM, timing and overnight throughput remain unverified.
- Caption language shaping depends on the user's FFmpeg/libass build and available system fonts. No font is bundled; Arial is named as the existing host font fallback.
- Real editorial quality is not established by schema/unit tests or synthetic clips. Model score thresholds and prompt weights require later golden-set calibration.

Task 05 must remain marked partial until the missing composition, safe-area, review UI and quality-evaluation work is completed and checked. The next work is still Task 05 completion; the real-footage Task 06 acceptance workflow was not run.

## Git delivery

Implementation commit: `7b0636b` (`Implement Task 05 editorial and review pipeline`). Documentation is committed separately. Push status, final documentation commit, branch and working-tree state are provided in the final delivery message.
