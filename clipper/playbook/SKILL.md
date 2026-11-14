---
name: clipper-editorial-playbook
description: Editorial rules, task prompts, JSON schemas and validators that Clipper's local model uses to find, check and package anime Shorts from one episode. Load the matching module for each job (scene summary, moment nomination, action verification, hook and context text) and validate every model output before use.
---

# Clipper Editorial Playbook

Purpose: make Clipper's local model behave like a careful anime editor. The model never decides alone: it reads a small, structured input, answers in a fixed JSON shape, and Python checks the answer.

## How it is used (the pipeline calls this; the model does not browse files)
1. `playbook.py build <module>` fills a module template with the scene or clip data and prepends `modules/common_rules.md`.
2. The prompt goes to llama.cpp with the module's schema as constrained JSON.
3. `playbook.py validate <module>` checks the output against the source text. Rejected outputs are logged with the reason and never used.

## Modules (one job per prompt; keep each prompt under ~1,800 tokens for a 2B model, 4,096 context)
| Job | Module | Input | Output schema |
|---|---|---|---|
| Summarise a scene | `modules/scene_summary.md` | NAMES + one scene's utterances | `schemas/scene_summary.schema.json` |
| Nominate moments | `modules/nominate_moments.md` | NAMES + a chunk of scenes (summaries + utterances) | `schemas/nominations.schema.json` |
| Verify an action candidate | `modules/action_verify.md` | 6-10 frames in time order | `schemas/action_verdict.schema.json` |
| Hook and context text | `modules/hook_context.md` | NAMES + the clip's transcript | `schemas/hook_context.schema.json` |

## Hard principles (also enforced by the validators)
- Text-only grounding: use only supplied lines. No outside anime knowledge.
- IDs, never timestamps. Python resolves boundaries.
- No speaker attribution, relationships, motives or feelings unless a line states them.
- Names only from the NAMES list (glossary + names found in the scene).
- "unclear" and empty lists are valid answers. Zero nominations is fine.
- The vision model may CONFIRM, REJECT or say UNCLEAR. It never nominates.
- Every nomination carries quoted lines that exist in the cited utterances.

Full dos and don'ts: `references/dos_and_donts.md`.
Placeholders in modules use `{{NAME}}`. Add new examples only from real, owner-approved moments.
