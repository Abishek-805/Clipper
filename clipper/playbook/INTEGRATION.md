# Wiring the playbook into Clipper

Flow for each model job (Clipper's Python does this; the model never reads files):

    build prompt  ->  llama.cpp with the module's JSON schema  ->  validate  ->  accept or log+drop

1. Copy this folder to `clipper/playbook/` (keep `modules/`, `schemas/`, `scripts/playbook.py`, `references/`).
2. Import `playbook.py` as a module. Use `build_prompt(module, vars)`, `schema(module)` and `VALIDATORS[module](output, context)`.
3. Send the schema to llama-server as constrained JSON (the Sprint 3B run already used schema-constrained output). Temperature 0, one slot, 4096 context.
4. Chunking: if `estimate_tokens(prompt)` exceeds ~1,800, split the scenes into chunks. Nominate per chunk, then merge.
5. A rejected output is logged (module, scene or clip ID, errors) and dropped. Allow at most ONE retry, and only for malformed JSON, never to "argue" with a validation failure. A rejected nomination is simply not used.
6. Zone data comes from the existing HARD / VERIFIED / SOFT zones. Utterance times come from the scene/utterance artifact.
7. Name list per scene: `names_in_text(scene_text, glossary)`.
8. Release the model after each stage; never run it beside Whisper.

What the validators prove, and what they do not:
- They prove: IDs exist, quotes are literal (after whitespace and hyphen normalisation), no speaker attribution or relationship claims, no invented proper names, duration and zone rules, no clickbait, no spoiler hooks.
- They do NOT prove the clip is good, the summary is faithful, or the plot is understood. Those still need the owner's review.

Known limits of the checks: names at the start of a sentence are not detected as new names unless they are in the glossary; the relationship check is a word-stem list, so unusual phrasing can slip through. Extend the lists in `playbook.py` when a real failure shows up, and add a test for it.

## Codex prompt (paste to integrate)

    TASK: Integrate the Clipper Editorial Playbook (folder clipper-editorial-playbook) into the understanding pipeline.
    Read AGENTS.md first. Branch from the current feature tip into feature/personal-v1-playbook. Push only that branch.
    1. Copy the playbook to clipper/playbook/ unchanged; add its tests to the project suite.
    2. Replace the Sprint 3B free-form prompt with modules/scene_summary.md; send schemas/scene_summary.schema.json to llama-server as constrained JSON; run validate_scene_summary on every response.
    3. Rerun the same 32 dialogue scenes. Report: JSON validity, validator pass rate, each rejection by rule, time per scene, VRAM samples (system-wide). Gate: at least 28 of 32 accepted and zero attribution or relationship violations; otherwise stop and report, no model download.
    4. If the gate passes, implement nominations with nominate_moments.md in chunks under 1,800 tokens, validate_nominations with real zones and utterance times, and show accepted nominations with their reason, quotes and zone status in the UI. Zero nominations is valid.
    5. Wire action_verify.md and validate_action into the existing visual verification (CONFIRM/REJECT/UNCLEAR only; never nominates) and hook_context.md into the optional hook text (owner picks or edits; default off).
    6. Run the full suite, compileall, git diff --check and the guard script. One short report (max 1 page). Do not claim plot understanding or editorial quality.
