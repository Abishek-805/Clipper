# Task 06C — Editorial quality and episode-wide coverage

## Result

**Partial.** Implementation and automated checks passed, and the one authorized S01-E01 run produced five technically valid MP4/SRT pairs. The run confirmed better episode coverage and complete speech-unit boundaries, but the sampled review still found a weak opening promotional/title montage and several ordinary dialogue moments. No semantic model was available, so the system did not establish that these are publish-worthy highlights.

## Protected baseline and scope

- Branch: `feature/task-06c-editorial-quality`
- Baseline: Task 06B checkpoint `37e6bcbcb7f9c220a265c2215fdf2709ea2323f0`
- `main` and `backup/task-06b-stable-20261112` were not changed.
- Source: `[S01-E01] Sword Art Online [1080p] [Dual] @Anime_x_Clan.mkv` (1,422.590 seconds)
- No package, model, or CUDA library was installed, copied, upgraded, or removed.
- One final isolated full-episode run was performed, with five as the maximum output count.

## Changes

- Changed the Auto duration defaults from 20–55 to 20–60 seconds and persisted the range, so existing preference files without these fields migrate to the new defaults. Complete speech candidates at 45–60 seconds receive a small score preference; complete shorter dialogue/action moments remain eligible.
- Added configurable hook context (default 4 seconds), nearby question/setup inclusion, mid-sentence start/end extension, and invalidation when a complete speech unit would exceed the configured maximum. Action-only candidates receive a neutral hook score.
- Added explainable opening-hook and duration features. These use timing and transcript structure only; they do not infer story meaning.
- Added a soft coverage bonus across up to five temporal regions. It applies only to candidates at least 85% of the best remaining score and does not force a quota.
- Added manual include/exclude ranges in seconds or `HH:MM:SS`, persisted with the preferences and run settings.
- Added a conservative low-motion/low-audio/no-dialogue intro and credits rejection heuristic. It does not identify logos with OCR or semantic vision.
- Filtered invalid refined boundaries before duplicate suppression. This fixes a case where an invalid short candidate could suppress an overlapping valid one.
- Motion analysis now retrieves image pixels at approximately 2fps. PySceneDetect evaluates at approximately 8fps. Existing source audio, motion, and scene results still use the analysis cache fingerprint; the analysis engine version was advanced to invalidate stale cache entries.
- Single-episode output folders now use the `Task06C-` prefix.

Changed source and test files: `clipper.py`, `main.py`, `tests/test_candidate_selection.py`, and `tests/test_task05.py`.

## Automated and synthetic validation

| Check | Result |
| --- | --- |
| Full active-venv suite: `.venv\\Scripts\\python.exe -m unittest discover -s tests -v` | **115 passed, 0 failed** |
| Python compile check: `.venv\\Scripts\\python.exe -m compileall -q .` | **Passed** |
| `git diff --check` | **Passed** |
| Candidate boundary, hook, coverage, manual range, and intro/outro tests | **Passed** |
| Synthetic 12-second scene sample | **Passed**: 288 frames decoded; 24 images sampled at 2fps; both cuts at 4s and 8s found in 0.738s at the scene detector stage |
| Synthetic end-to-end render | **Passed** as part of the full suite |

The tests verify temporal boundaries and score behavior. They do not prove semantic quality on anime dialogue.

## Subtitle source and duplicate-layer investigation

The source has four ASS subtitle streams: global indexes 3 (English, default), 4 (Japanese), 5 (English), and 6 (Japanese). Track 3 converts to approximately 3,233 events and is heavily styled/duplicated; track 5 has approximately 333 events and is visually leaner. Both primarily carry on-screen signs/title text rather than continuous spoken dialogue. For example, around 864 seconds both are empty while Whisper transcribes the episode narration; around 1,104 seconds the English tracks contain different sign/title wording, not a reliable replacement for the spoken English dub. Neither embedded English track was selected as the dialogue caption source. Whisper transcription remained selected.

Sampled source frames at 880 and 1,110 seconds showed no burned dialogue subtitle layer. The Japanese text visible in some reviewed frames is scene/sign artwork. The five output MP4s contain only H.264 video and AAC audio; no embedded subtitle stream is mapped into them. Each output has one burned animated Whisper caption layer and a separate same-basename SRT sidecar (6, 22, 6, 7, and 2 cues). A media player that auto-loads matching SRT files can display the sidecar over the burned captions. That player behavior is the remaining likely source of the user's two visible layers; keep external subtitles disabled during playback when reviewing the burned-caption MP4. The SRT is retained for separate subtitle use.

Whisper preserved the English spoken language and produced 2,089 word-timestamped words. The comparison did not establish a word-error rate. At least one sample includes a likely recognition defect (`mid -level`), and proper-noun/skill-name accuracy remains unverified. No terminology was manually invented or substituted.

## Full-episode validation

Run folder: `Shorts_Output/Task06C-validation-20261112-221019/`

| Clip | Source interval | Duration | Hook score | Reason | Review |
| --- | ---: | ---: | ---: | --- | --- |
| `clip_001.mp4` | 2.002–27.410s | 25.408s | 0.500 | Complete dialogue units | **Weak**: starts with a slow promotional/title montage before the spoken hook; the static/near-empty opening remains selected. |
| `clip_002.mp4` | 256.631–316.480s | 59.849s | 0.500 | Complete dialogue units | **Partial**: coherent boar fight and dialogue, but substantial action precedes the first spoken line. |
| `clip_003.mp4` | 637.600–683.308s | 45.708s | 1.000 | Complete dialogue units | **Partial**: complete opening question and exchange; dialogue is explanatory rather than clearly a standout moment. |
| `clip_004.mp4` | 955.079–976.620s | 21.541s | 0.737 | Complete dialogue units | **Partial**: complete short exchange and reaction; retains some lead-in. |
| `clip_005.mp4` | 1290.664–1310.935s | 20.271s | 1.000 | Strong audio event with scene continuity | **Partial**: short action/audio moment; semantic payoff was not assessed. |

All five selected clips occupied distinct temporal regions 0–4. The contact sheet and three sampled frames per clip show the centered landscape frame over a blurred background; no aspect distortion was observed. This is a sampled visual review, not full-duration human playback of every clip.

`ffprobe` verified all five outputs as MP4, H.264, AAC, 1080×1920, and 30fps. All five have non-empty SRT files. Each passed the application's output validator. Encoding used `h264_nvenc` for all five renders.

### Performance comparison with Task 06B

| Stage | Task 06B | Task 06C | Change |
| --- | ---: | ---: | ---: |
| Motion analysis | 498.267s | 218.161s | 56% faster |
| Scene analysis | 621.140s | 292.192s | 53% faster |
| Whisper stage | 560.807s | 793.589s | 42% slower |
| Rendering | 655.014s | 811.840s | 24% slower |
| Episode total | 2,387.861s | 2,163.949s | 9% faster |

The sampled analysis reduced motion and scene time, while total runtime improved only modestly because transcription and rendering took longer in this run. OpenCV still decodes all 34,107 source frames sequentially; it only retrieves image pixels for motion scoring at 2fps. That remains a major performance limit.

## CUDA diagnosis

- Hardware: NVIDIA GeForce RTX 3050 A Laptop GPU, 4,094 MiB VRAM; driver 616.56.
- Installed software: CTranslate2 4.9.0 and Faster-Whisper 1.2.1; CTranslate2 reports one CUDA device and supported CUDA compute types.
- `cublas64_12.dll` was not found by the Windows loader, `ctypes.util.find_library` returned no result, and no copy was found on the Python environment/package paths checked.
- A real 30-second sample returned 91 English words in **40.57s on CPU int8**. The CUDA attempt fell back because cuBLAS could not load.
- The full 1,420-second run initialized the model on CUDA, then actual inference failed with `Library cublas64_12.dll is not found or cannot be loaded`; CPU int8 completed with 2,089 words. This is evidence of CPU fallback, not CUDA inference success.
- Peak system GPU memory observed during the full run was 1,862 MiB used (2,031 MiB free). This system-wide memory sample is not GPU inference evidence.
- No full-episode GPU run was performed.

The [CTranslate2 Windows installation guidance](https://github.com/OpenNMT/CTranslate2/blob/master/docs/installation.md) requires CUDA 12 libraries for GPU execution. The [Faster-Whisper GPU requirements](https://github.com/SYSTRAN/faster-whisper#gpu) identify cuBLAS and cuDNN as runtime dependencies. A future GPU fix should use the matching official CUDA 12/cuBLAS runtime and cuDNN 9 distribution, expose the DLL directories to the process, then rerun a short actual inference and verify its device. No runtime installation or DLL copy was made in this task.

## Limitations and unresolved items

- **Partial, not editorially accepted.** The early promotional/title montage remains a selected candidate. The conservative intro heuristic did not detect it because it has motion/audio activity.
- There is no OCR or semantic model available to identify studio logos, anime title cards, or narrative setup/payoff. The activity-only ranker cannot guarantee funny, dramatic, or publish-worthy selections.
- Embedded English ASS tracks are not reliable dialogue captions for this source. Whisper remains the practical default, with proper names and anime terms still a known accuracy risk.
- Same-basename SRT files can be auto-loaded by players over the burned caption layer; this cannot be reliably controlled by Clipper once the user opens the files in an external player.
- The sampled visual review covers three frames per clip, not every frame or complete clip playback.
- GPU inference remains blocked by missing `cublas64_12.dll`; the CPU fallback is working.
- Performance is only modestly better end-to-end; Whisper and render time rose in this run.
- No branding, Task 07 work, package installation, model download, or CUDA runtime change was performed.

## Repository state

- Branch created from the exact Task 06B baseline: `feature/task-06c-editorial-quality`.
- Graphify's code graph was refreshed after implementation: 673 nodes, 1,524 edges, 26 communities; community labels were not sent for LLM refresh.
- Commit/push status: final Task06C commit is the branch HEAD; pushed to `origin`; working tree clean.
- `main` and the verified 06B backup remain untouched.
- Task 07 is not started.
