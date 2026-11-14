# Editorial playbook — integrated; scene gate failed

2026-11-14 · `feature/personal-v1-playbook`, from verified remote `cda29330106004a69fa5610bba61436e37b8f40a`.

**Delivered:** the user's 14 packaged files are copied byte-for-byte to `clipper/playbook/`, including all four prompt modules, schemas, validators and tests. `.gitattributes` preserves vendor bytes on Windows. A lazy adapter loads the script without creating a `clipper` package or shadowing `clipper.py`; normal startup/selection remains unchanged. This is runtime prompt material for Clipper's model, not a Codex skill install or model fine-tune.

| Check | Actual result |
| --- | --- |
| Baseline / full project suite | 293 → **326 passed**, 9 subtests passed; 0 failed/skipped |
| Supplied synthetic tests | **28 passed**, independently verified |
| Adapter tests | **5 passed**, including malformed JSON/types and import preservation |
| Compilation / diff / guard | Passed; 14 vendor hashes and 18 preserved-file hashes checked |

**One real text-only pass:** unchanged S01-E01 cached units/Whisper source, installed Qwen3-VL-2B Q4_K_M and llama.cpp b12284; 39 scenes, 32 dialogue requests, seven no-dialogue skips. Prompts prepend `common_rules.md` to `scene_summary.md`; the supplied schema constrains JSON and `validate_scene_summary` checks every response. The adapter additionally validates the schema's actual types/required fields/enums before calling the vendor code. No new dependency was installed.

| Gate measurement | Result |
| --- | --- |
| Valid schema-constrained JSON | 32/32 |
| Accepted by schema + supplied validator | **24/32** |
| Rejected responses | 8: six over 20 words; two allowed-name-list violations |
| Attribution / relationship response flags | **1 / 0**; attribution overlaps a name-list rejection |
| Required gate | ≥28/32 and zero attribution/relationship flags — **FAILED** |

The eight rejected scene IDs are **SC_0005, SC_0009, SC_0015, SC_0018, SC_0020, SC_0024, SC_0026, SC_0027**. Outputs, errors and per-scene timings appear in [evidence JSON](editorial-playbook-evidence.json); raw prompts/responses stay under ignored `.runtime/playbook/scene-benchmark/`.

**Important interpretation:** `Mere` (SC_0020) and `Kirito` (SC_0027) actually occur in their source lines. The supplied extractor omits first-word names, so these are list-construction failures, not invented names. `Mere asks` nevertheless violates the no-attribution rule. The vendor files were not patched and rejected content was not retried. Other accepted summaries can still be unfaithful; neither these rules nor the package's hook/spoiler checks prove meaning, editorial quality or publishing suitability. This scene schema returns no IDs or quotes, so ID/quote accuracy is **not applicable**, not 100%. Different contracts prevent a general accuracy comparison with earlier runs.

**Performance:** 44.365 s complete pass, 6.318 s initialization; 0.560–2.199 s/request. Peak sampled dedicated GPU use **2754 MiB system-wide**, including desktop/other apps; 87 samples. Prompt estimates 245–834 tokens; maximum actual prompt usage 1087, below the suggested 1800 budget. Temperature 0, 4096 context, one slot, offline, no projector. Thinking disabled in request/template and reasoning budget 0; no reasoning fields/tags observed. Owned server stopped after the pass.

**Stopped at the specified gate.** Nomination/chunking/selector activation and real action/hook inference were not implemented or run. Their supplied modules/validators exist and synthetic tests pass, but they are not production features. No MCP server, download, final-clip render or full-media analysis was added. Existing sources, jobs, caches, weights, owner-confirmed references, earlier raw experiments and `vlc-help.txt` remain unchanged. No project `AGENTS.md` or earlier guard script was present; the new read-only `tools/guard_playbook.py` checks the branch, protected tracked paths, vendor integrity and saved local-file hashes. The owner decides any subsequent playbook correction or separate 4B benchmark; no download is authorized by this result.
