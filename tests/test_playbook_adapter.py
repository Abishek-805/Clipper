import json
from pathlib import Path
import unittest

import clipper
from evaluation.playbook_adapter import ROOT, audit_scene, playbook, scene_prompt, validate


class PlaybookAdapterTests(unittest.TestCase):
    lines = [dict(id="U_0001", text="The bridge is closed.")]

    def test_original_clipper_import_is_preserved(self):
        self.assertEqual(Path(clipper.__file__).name, "clipper.py")
        self.assertTrue((ROOT/"scripts/playbook.py").is_file())

    def test_scene_prompt_contains_only_supplied_lines_and_names(self):
        prompt, ctx = scene_prompt(dict(id="SC_0001"), self.lines, [dict(to="Aria")])
        self.assertIn("U_0001: The bridge is closed.", prompt)
        self.assertIn("Aria", ctx["names"])
        self.assertNotIn("{{", prompt)
        self.assertLess(playbook.estimate_tokens(prompt), 1800)

    def test_schema_types_and_extra_fields_are_rejected_before_vendor(self):
        context = dict(utterances=self.lines, names=[], glossary=[])
        good = dict(summary="The bridge is closed.", names_used=[], tone="calm", fight_described=False)
        self.assertTrue(validate("scene_summary", good, context)["ok"])
        for changed in (dict(good, names_used=5), dict(good, fight_described="false"), dict(good, tone="invalid"), dict(good, extra="field")):
            self.assertFalse(validate("scene_summary", changed, context)["ok"])
        self.assertFalse(validate("scene_summary", "not JSON", context)["json_valid"])

    def test_rejection_categories_and_no_quote_id_accuracy_claim(self):
        raw = json.dumps(dict(summary="Aria says they are rivals.", names_used=["Aria"], tone="tense", fight_described=False))
        result = audit_scene(raw, dict(utterances=self.lines, names=["Aria"], glossary=[]))
        self.assertFalse(result["accepted"])
        self.assertTrue(result["attribution_violations"])
        self.assertTrue(result["relationship_violations"])
        self.assertEqual(result["quote_count"], 0)

    def test_oversized_scene_is_not_silently_truncated(self):
        with self.assertRaisesRegex(ValueError, "no truncation"):
            scene_prompt(dict(id="SC_0001"), [dict(id="U_0001", text="word "*2000)], [])
