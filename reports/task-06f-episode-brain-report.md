# Task 06F — Episode Brain prototype report

## Outcome and acceptance status

The isolated Episode Brain prototype now builds a persistent, timestamped whole-episode evidence bundle and review board from Clipper's cached transcript/signals plus one sequential low-resolution frame pass. It discovers moments independently of the five old production clips, preserves natural speech/activity intervals, and exposes cross-scene lexical links as tentative evidence.

**Editorial acceptance is not met.** No local vision or language model was available, so the episode has no semantic plot summary, visual descriptions, named entities, confirmed callbacks, fight classifications, or editorial ranking. Categories and character/speaker identities remain unknown. The two highest signal-priority moments include a likely credit/title passage, which demonstrates why signal scores cannot stand in for editorial judgment. No clips were rendered and production selection is unchanged.

## Branch, baseline, and protected refs

- Task 06F branch: `feature/task-06f-episode-brain`.
- Exact base: Task 06E remote/local tip `333174684fac0abb666dad571efc1e1b4f77d52f`.
- Expected Task 06E implementation commit `c329df1f6e5f750ca48f211204ddf51b5013d021` is an ancestor of that base; Task 06E's later report commit is also included.
- Task 06D.1 baseline: `91cf3934131d0ff42a428ce010391c9627da7de0`.
- Task 06D.3 UI branch was verified locally at `157f2c91f7191ced4c9d28277b83ed3f79fd32f7` (parent `c49b577a7f93a77ac8578df233c1cac1e4550cf0`). The remote has no `feature/task-06d3-ui-redesign` ref. Its merge base with Task 06E is `91cf393…`; the UI line was not merged or modified.
- Task 06B backup, Task 06C, Task 06D, Task 06D.1, Task 06D.2, Task 06D.3, Task 06E, and `main` were not modified. No history was rewritten or force-pushed.

Task 06F changes are research/evaluation-only: the production selector, output renderer, captions, and main GUI were not changed. User-media artifacts stay under the ignored `Shorts_Output/` tree.

## Source inspection and full coverage

Source: local S01-E01 MKV, 333,333,074 bytes, 1,422.59 seconds, 1920×1080 HEVC at 23.976 fps. It has Japanese AAC audio at global stream 1, default English AAC audio at global stream 2, and four ASS subtitle streams at global indexes 3–6. Cached analysis selected English stream 2. The original file was not modified.

The cached Faster-Whisper transcript contains 2,089 records; 2,085 have positive, in-range word intervals. Four zero-duration ASR records were excluded from timeline calculations. Cached transcription is English, CPU `int8`, outcome `CPU_FALLBACK`, with 641.436 seconds transcription time over 1,420.032 audio seconds (recorded real-time factor 0.4517). The known CTranslate2 CUDA DLL problem remains unresolved; this task did not retry GPU inference.

The new timeline uses all 387 cached scene cuts to form **388 scene intervals that partition the complete `[0, 1422.59]` second episode**. It retains all 1,423 cached one-second motion/audio bins. The visual coverage pass sampled every three seconds plus each cached cut timestamp. It wrote 862 low-resolution stills after decoding 34,095 source frames once; no Whisper, scene detection, candidate selection, or rendering pass was repeated. A later build reused those stills.

The contact sheet is [episode_contact_sheet.jpg](../Shorts_Output/Task06C-20261113-095427/_S01-E01_Sword_Art_Online_1080p_Dual_Anime_x_Clan_fbfef207/candidate_review_06f/episode_contact_sheet.jpg). It is a coarse human inspection aid, not a model description or complete playback review.

## Embedded subtitle audit

Each track was extracted locally and retained with its cue timestamps. Coverage is unioned cue-time coverage divided by source duration. Duplicate ratio counts repeated normalized cue text within that track. Whisper comparison is token-set Jaccard and is only a rough lexical cross-check, not alignment or translation accuracy.

| Global stream | Declared language / default | Cues | Time coverage | Repeated text | Token-set Jaccard vs Whisper | Audit |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| 3 | English / default | 3,233 | 8.72% | 95.08% | 0.1089 | Sparse and heavily repeated |
| 4 | Japanese / non-default | 3,559 | 65.64% | 86.68% | 0.3738 | Predominantly Latin script (94.96%); language tag conflicts |
| 5 | English / non-default | 333 | 8.36% | 80.18% | 0.1091 | Sparse and heavily repeated |
| 6 | Japanese / non-default | 655 | 66.06% | 41.83% | 0.3795 | Predominantly Latin script (99.98%); language tag conflicts |

All tracks have valid cue intervals, but duplicate content, incomplete coverage, and metadata/script conflicts make none suitable as the primary transcript without human review. Cues remain separate from Whisper word timings; exact dialogue quotes in moment records come from cached Whisper words, with overlapping subtitle cues attached as secondary evidence. The four records with zero-duration Whisper words are excluded rather than repaired or invented.

## Episode memory and moment discovery

The builder is `evaluation/build_episode_brain.py`; reusable evidence/interval logic is `evaluation/episode_brain.py`. It reuses Task 06E's timestamped utterance grouping and motion/audio event proposals, but independently groups full-transcript speech into connected moments with no 60-second cap and does not query the legacy selected-clip list for discoveries. Old selections appear only in a comparison field and five baseline cards.

The generated memory contains:

- **388** chronological scene intervals, each linked to turns, cached signal evidence, and sampled frame timestamps. Visual descriptions and visible entities are null/empty because no semantic vision ran.
- **2,085** valid Whisper words and **156** timestamped speech turns/phrase records. Four zero-length records are identified by validation and left out of the normalized word timeline.
- **23** review moments: 21 connected dialogue sequences and two `unknown_visual_activity` spans. Each has source bounds, exact ASR quote where present, surrounding transcript turns, scene IDs, subtitle evidence, cached signals, and an explicit boundary rationale.
- **35** possible question-followed-by-speech links and **115** possible cross-scene lexical-overlap links. These are sequence/word-overlap candidates only; they do not assert answers, character relationships, plot causality, or callbacks as fact.
- Empty entity memory and semantic plot summary, with explicit `UNAVAILABLE` / `NOT_SEMANTICALLY_SUMMARIZED` status.

Boundaries use first/last complete connected ASR word intervals or contiguous motion windows. No 45/60-second cap or clip quota is imposed. Dialogue groups range from 0.52 seconds to 145.8 seconds; the long group is retained because there is no semantic evidence to split it at a genuine story beat. The review card shows the full suggested interval and context, with a central 8-second excerpt only as a quick preview.

The global proposal list is ordered by **signal evidence only**. It keeps signal evidence, semantic confidence, narrative importance, editorial assessment, and boundary confidence separate. Editorial rank is null for every candidate and `quality_qualified_count` is zero. The top two signal-only windows are 1302–1313s (0.9661) and 1376–1385s (0.9471). The coarse still around 1378s shows credit/title material, so that high signal rank is not evidence of a strong highlight. The frames around 1299–1302s show an action-like sword image, but a still sequence without semantic motion analysis does not establish a complete fight progression.

## Review board and outputs

The new board is generated at:

`Shorts_Output/Task06C-20261113-095427/_S01-E01_Sword_Art_Online_1080p_Dual_Anime_x_Clan_fbfef207/candidate_review_06f/index.html`

It has 28 cards (all five production baselines plus all 23 new moments), a scene/event overview lane, source timestamps, three nearby frames where available, and short previews using the manifest-selected English audio stream 2. It reuses Task 06D.1's annotation schema, local draft save, import/export, source fingerprint, and exact-interval checks. The generated dataset has zero human annotations. All 28 previews passed the existing FFprobe stream/language/duration/synchronization validator; HTML JavaScript passed `node --check`.

Machine-readable files in the same directory include:

- `episode_brain.json`, `episode_timeline.json`, `scene_memory.json`
- `entity_memory.json`, `plot_memory.json`, `important_phrases.json`
- `visual_event_timeline.json`, `action_fight_event_index.json`
- `moment_discovery.json`, `global_ranked_moments.json`, `natural_boundaries.json`
- `old_vs_new_comparison.json`, `subtitle_quality_report.json`, `performance.json`
- `anime_highlight_dataset.json`, `index.html`, candidate stills/previews, and `episode_contact_sheet.jpg`

The action/fight index intentionally says `NOT_SEMANTICALLY_CLASSIFIED_NO_VLM`; motion intervals are not labeled as fights or action. No candidate was automatically rejected, and none was automatically accepted.

## Qwen3-VL-2B and llama.cpp feasibility

Local inspection on 2026-11-13 found Python 3.14.7, Faster-Whisper 1.2.1, CTranslate2 4.9.0, Windows 11 Home, Intel i5-13450HX, 15.71 GiB visible RAM, and RTX 3050 Laptop GPU with 4,094 MiB VRAM. Free VRAM was 2,506 MiB during the frame pass and 2,669 MiB on a later idle check. No `llama`, `llama-server`, or `llama-mtmd-cli` executable, `llama-cpp-python`, or Qwen3-VL-2B cache was found. PyTorch, Transformers, and Pillow are also absent from the project venv.

The current Qwen-hosted GGUF repo lists the language model at 1.11 GB for Q4_K_M and 1.83 GB for Q8_0; the separate vision projector is 445 MB for Q8_0 or 819 MB for F16. The selected Q4_K_M + Q8_0 pair is about 1.56 GB on disk before runtime allocations. The repo identifies Apache-2.0 licensing and llama.cpp compatibility, and its setup page documents Windows installation and says to use the latest llama.cpp build. Current llama.cpp multimodal documentation lists video input, and its multimodal API includes a Qwen3-VL video template. These sources show a plausible integration path, not proof that an uninstalled Windows binary will run this exact model on this GPU.

At the observed 2.5–2.7 GiB free VRAM, the weight pair alone leaves little headroom for vision activations, KV cache, context, and runtime. No authoritative RTX 3050 VRAM/RAM requirement or actual peak allocation was found or measured. Full GPU offload is therefore **unverified and likely tight**. A future approved experiment should pin a llama.cpp release, begin with Q4_K_M + Q8_0, short sampled image sequences, batch size 1 and a modest context, measure CUDA/CPU placement and peak memory, and preserve CPU fallback. No package, runtime, driver, or model was installed/downloaded in Task 06F.

Sources: [Qwen3-VL-2B-Instruct-GGUF model card and file sizes](https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct-GGUF), [Qwen3-VL Transformers documentation](https://huggingface.co/docs/transformers/main/model_doc/qwen3_vl), [llama.cpp multimodal input documentation](https://github.com/ggml-org/llama.cpp/blob/master/docs/multimodal.md), and [llama.cpp multimodal API video templates](https://github.com/ggml-org/llama.cpp/blob/master/tools/mtmd/mtmd.h).

The code defines `LlamaCppVisualEventAnalyzer` behind the existing `VisualEventAnalyzer` contract. It accepts only loopback HTTP, disables environment proxies and redirects, and is never instantiated by the Episode Brain builder. The existing deterministic mock explicitly says it did not inspect pixels and is used only by tests.

## Performance and limitations

- Initial whole-source frame extraction: **622.805 seconds**, 862 saved frames, 34,095 decoded source frames. The first complete build took **649.884 seconds** including subtitle extraction, indexes, and previews. Subsequent cached rebuild: **24.293 seconds**, no source frame decode.
- During the first extraction, periodic `Get-Process` samples showed working set up to **329,261,056 bytes (~314 MiB)**. This was external sampling, not a continuously instrumented peak; the first-run in-process peak field is null because the initial Win32 handle probe failed. A later cache-hit build measured 47.55 MiB peak working set for that smaller invocation only.
- No model was initialized; semantic initialization/inference and peak VRAM fields are null. GPU inference was not run. The free-VRAM readings above are device snapshots, not a model peak measurement.
- The generated artifact tree is about **30 MiB** and is gitignored.
- There is no full-episode human playback review and no human annotation set. The contact sheet is a coarse visual scan only. The prototype cannot establish plot meaning, fight progression, emotion, comedy, callback correctness, or editorial quality without model inference and human labels. Subtitle cue times are not word alignment. Lexical callback candidates can be false positives. A long dialogue sequence may contain multiple story beats because the system has no semantic split signal.

## Tests

- Full suite: `.venv\Scripts\python.exe -m unittest discover -s tests -v` — **157 passed, 0 failed** in 22.442 seconds.
- Task 06F-focused tests: **16 passed**; timeline partitioning, unknown fields, exact quotes, question structure, lexical links, natural durations beyond 60 seconds, dialogue-free activity, no quota/editorial rank, baseline-independent discovery, subtitle parsing/quality warnings, CPU fallback provenance, loopback-only model adapter, and explicit mock uncertainty.
- Compilation: `.venv\Scripts\python.exe -m compileall -q evaluation tests` — passed.
- Review-board checks: 28/28 previews validated, all referenced images exist, generated JavaScript parsed by Node, and `git diff --check` passed.
- No full episode transcription/scene-analysis rerun, no clip render, no model inference, no package/model download, and no user annotation were performed.

## Changed files

- `evaluation/episode_brain.py`
- `evaluation/build_episode_brain.py`
- `evaluation/llama_cpp_vlm.py`
- `tests/test_episode_brain.py`
- `reports/task-06f-episode-brain-report.md`
- `reports/IMPLEMENTATION_STATUS.md`

## Git publication

The Task 06F implementation commit and branch push are recorded here after publication. Task 06F remains an isolated prototype; do not treat this report as semantic/editorial quality acceptance or as authorization to start Task 07.
