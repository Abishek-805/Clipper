TASK: Summarise one scene.
NAMES: {{NAMES}}
SCENE {{SCENE_ID}}
{{UTTERANCES}}

Write:
- summary: at most 20 words, a plain restatement of what the lines say happens. No guesses.
- names_used: names from NAMES that appear in the lines (empty list if none).
- tone: choose from calm, tense, urgent, sad, happy, funny, angry, explanatory, unclear.
- fight_described: true only if the lines themselves describe fighting or an attack, else false.
