# Sprint 3B — Phase 1 gate failed; production selector unchanged

2026-11-14 · `feature/personal-v1-understanding-3b` · Verified parent `bf5077f1f826759f14ef6deb9a48cfb089ac8d84`.

| Phase | Status | Commit | Actual verification |
| --- | --- | --- | --- |
| 0 — Subtitle confirmation | Implemented | `1d23468` | Baseline 267 + 9 subtests; phase 0: 270 + 9 |
| 1 — Existing-model measurement | Run; contract gate failed | `a07f252` | Final 277 + 9; 32 real text requests |
| 2–4 — Nominations/action/new selector | **Not started: Phase 1 stop rule** | — | No acceptance claimed |
| 5 — Reference template/report | Partial; timed references missing | Report commit | Two owner descriptions; zero timed entries |

**Owner decisions honored.** Track 4 remains unconfirmed; plot input is copied cached Whisper words, not release subtitles. Caption default remains Whisper. The browser now shows five distributed source lines per usable track and explicit Use as English dialogue / Not dialogue / Clear controls. Decisions persist by full source SHA-256 and stream index; wrong hashes/indexes are ignored, and stale browser submissions are rejected. Explicit confirmations take priority over language heuristics; multiple positive choices remain ambiguous. Normal caption rendering retains the shared glossary path. Confirmation tests use synthetic data, not fabricated owner labels.

**Measured model pass.** Used the already-approved Qwen3-VL-2B Q4_K_M (SHA-256 `089d75c5…`), installed llama.cpp b12284, offline text-only mode, no projector, 4096 context, one slot, temperature 0, schema-constrained JSON. 39 structural scenes: **32 analyzed, 7 skipped without dialogue**. Initialization **3.157 s**; request times **1.488–6.236 s**, median **3.241 s**, sum **105.093 s**; complete run **114.207 s**. Timings are observations on the live desktop, not a controlled speed comparison; the test suite began near the end of the pass.

| Check | Result |
| --- | --- |
| Schema-constrained valid JSON | 32/32 (100%) |
| Existing scene/utterance IDs | 128/128 (100%) |
| Names absent literally from supplied scene | 43/72 entries (59.72%) |
| Quotes not matching cited text | 1/96 (1.04%) |
| Responses passing all literal checks | **7/32** |
| General invented-fact rate / editorial accuracy | **Not measured**; no human ground truth |

The unsupported names are mostly generic role labels (“Person A”, “NPC”), **not 43 invented character identities**. The quote mismatch changes `mid -level` to `mid-level`; it does not establish fabricated dialogue. Nevertheless both violate the required contract. Source inspection also found unsupported named-speaker assignments in SC_0004 and a speculative “close relationship” in SC_0027. These examples are developer observations, not human annotations or a population-wide fact-error rate. Valid JSON/IDs do not establish faithful interpretation. **This configuration is not accepted for nominations.** No prompt retry or larger-model experiment was performed.

**GPU/lifecycle.** CUDA offload was requested with `--n-gpu-layers 99`; exact offloaded-layer count was not retained in this log. Sampled dedicated GPU use peaked at **3142 MiB system-wide**, baseline 1394 MiB, after shutdown 1302 MiB; these include desktop/other applications and are not per-process peaks. Both known Clipper servers reported no loaded Whisper before startup; monitoring stops this benchmark if either becomes busy. The owned model server was deliberately terminated after the pass; no llama-server remains. No models ran in the production application during this experiment.

**Next-model options — not downloaded.** Metadata, revisions, file hashes and sizes were read from official Qwen repositories; all are Apache-2.0. Exact pins are in [evidence JSON](understanding-3b-evidence.json).

| Candidate file | Download | Hardware plan (unmeasured estimates) |
| --- | --- | --- |
| [Qwen3-4B-Q4_K_M.gguf](https://huggingface.co/Qwen/Qwen3-4B-GGUF/blob/bc640142c66e1fdd12af0bd68f40445458f3869b/Qwen3-4B-Q4_K_M.gguf) | 2,497,280,256 B (2.326 GiB) | First optional trial; roughly 2.7–3.5 GiB model/runtime VRAM plus desktop use. Full GPU fit is **not guaranteed**; short context or CPU offload may be needed. Plan 4–6 GiB extra RAM. |
| [Qwen3-8B-Q4_K_M.gguf](https://huggingface.co/Qwen/Qwen3-8B-GGUF/blob/7c41481f57cb95916b40956ab2f0b139b296d974/Qwen3-8B-Q4_K_M.gguf) | 5,027,783,488 B (4.682 GiB) | Requires CPU offload on 4 GB; plan 7–10 GiB extra RAM and slower execution. Not the first trial. |

Any approval covers one pinned GGUF under `%LOCALAPPDATA%/Clipper/models/<repo>/<revision>/`, SHA-256 verification, and another text-only benchmark using the existing runtime; no Python/CUDA packages, projector, or guaranteed quality improvement. A revised 2B prompt/schema experiment is also an option, requiring no download. Per the supplied Phase 1 instruction, further intelligence implementation waits for the owner's decision.

**Verification/delivery.** 19 focused tests; full suite **277 passed, 9 subtests passed, 0 failed/skipped** (45.95 s). Compilation/diff checks passed. **14 Playwright checks** passed with zero page/console errors or processing requests; separate CLI inspection verified five track-4 samples, unconfirmed state and unchanged Whisper mode. Screenshot is local under ignored `output/playwright/`. Original transcription/analysis hashes match their copies; sources, jobs, models, other branches and `vlc-help.txt` are preserved. Implementation `a07f252adcfe5ab0a1f726a9de55a7f6abb20683` matched the pushed remote; this report follows on the same branch. Raw responses/timings remain under ignored `.runtime/sprint3b/text-benchmark/`. No full-media analysis/rendering, action verification, new selection, or publishing occurred. The owner supplied two scene descriptions in [the template](../evaluation/known_moments/s01-e01.json), without timestamps; found/missed metrics are therefore **NOT RUN**. The previously selected 637.820–683.308 interval was not rendered or owner-reviewed here.
