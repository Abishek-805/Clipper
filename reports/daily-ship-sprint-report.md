# Daily Ship Sprint — Personal Preview 0.1

**Branch:** `feature/personal-v1-daily-ship`
**Baseline:** `1267953a385783d18b6be3c8f15e39c1f415cbc8`
**Scope:** CUDA diagnostics/fallback, caption corrections, local glossary, single-clip rerender, and a simple local editor. No Personal Preview 0.2 work.

## Phase results

| Phase | Result | Commit / evidence |
| --- | --- | --- |
| 0 — Baseline | Complete; branch created from the verified Preview 0.1 baseline. Existing outputs, caches, protected branches, and `vlc-help.txt` preserved. | `1267953a` |
| 1 — Whisper CUDA | CUDA DLL discovery added. CUDA inference still fails because `cublas64_12.dll` cannot load. CPU int8 fallback succeeded on a 30-second stream-2 sample in 6.425 s and returned 49 English words; CUDA attempt failed in 2.949 s. No system CUDA installation or PATH changes. | `9ddd142a`; 213 tests passed at phase gate |
| 2 — Caption formatting | Short trailing fragments are rebalanced; punctuation spacing and line breaks are retained. On copied outputs, regenerated SRTs removed both under-500 ms single-word cues in clip 001. | `dd838265`; 215 tests passed at phase gate |
| 3 — Name glossary | Added local whole-word corrections: `Kaya -la → Kayaba`, `Kain → Klein`, and `Nerve Gear → NerveGear`. Captions change; cached ASR words do not. | `3b4f56f5`; 219 tests passed at phase gate |
| 4 — Single-clip editor backend | Added cached-word rerendering, trim validation, artifact validation, version backups, cancellation safety, and edit history. | `896a9511`; 223 tests passed at phase gate |
| 5 — Browser editor | Added preview, trim nudges/exact time entry, caption editing, caption placement, rerender, and busy/error status. The local in-app browser showed the real clip card and editor controls. | `328d476e`; 224 tests passed at phase gate |
| 6 — Boundary refinement | **Skipped.** Sentence/scene boundary changes alter selection semantics and risk changing the accepted 0.1 output. It needs separate tests and editorial review. | No code changes |
| 7 — Real copied-job check | Complete. Reused copied cached transcription and glossary in an isolated app-data directory; rerendered only an ignored validation copy. | This report and final commit |

## Copied-job evidence

Source: S01-E01 MKV, stream 2; source path and fingerprint were verified from the copied job manifest. The two protected acceptance job folders and persistent cache were not used as render targets. The isolated copy of `clip_002` was extended from 45.329 to **46.329 seconds** and rerendered in **206.093 seconds** using **h264_nvenc**. FFprobe validated MP4, H.264, AAC, 1080×1920, 30 fps, and 46.329-second duration. Renderer validation checked audio/video sync; no independent listening or perceptual sync review was performed here.

The new SRT has **19 cues** and the renderer recorded all three glossary applications: `Kaya-la → Kayaba`, `Nerve Gear → NerveGear`, `Kain → Klein`. Its preserved `.v1` SRT is the before version. Across regenerated copied SRTs, short single-word cues under 500 ms went from 2 to 0 in clip 001 and remained 0 in clip 002. This formatting heuristic can still merge genuinely brief utterances; transcription accuracy is not guaranteed.

The rerender reused the copied persistent transcription cache; it did **not** rerun the whole episode or independently prove a second end-to-end analysis-cache hit. The earlier Preview 0.1 acceptance remains owner-accepted technically. Audible English-track identity and perceptual synchronization remain pending owner listening. Editorial quality was not evaluated.

## Verification and state

- Targeted caption/glossary tests: **4 passed**.
- Full project suite: **225 passed, 0 failed** in 44.339 seconds.
- `compileall`: passed.
- `git diff --check`: passed.
- Production selection boundaries and ranking were unchanged. **No Personal Preview 0.2 implementation or branch was started.**
- Current status: **Technically ready — rights not verified.** Local exports do not establish permission to publish; no YouTube integration is included.
- Limits: Whisper CUDA is still blocked by the missing/unloadable CUBLAS DLL; CPU fallback works. Editorial selection, audible language, perceptual sync, and publication rights are not certified by these checks.

## Phase commit history

`1267953a` → `9ddd142a` → `dd838265` → `3b4f56f5` → `896a9511` → `328d476e` → final report commit.
