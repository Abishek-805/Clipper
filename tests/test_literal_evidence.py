import json
import unittest
from evaluation.literal_evidence import audit_literal_response, claim_violations, normalize


class LiteralEvidenceTests(unittest.TestCase):
    scene = {"id": "SC_0001", "utterance_ids": ["U_0001"]}
    lines = [{"id": "U_0001", "text": "A mid -level boss. My name is Klein."}]

    def audit(self, names=None, summary="A mid-level boss.", quote="a MID–level boss."):
        raw = dict(scene_id="SC_0001", summary=summary, names=names or [], tones=["neutral"],
                   references=[dict(utterance_id="U_0001", quote=quote)])
        return audit_literal_response(json.dumps(raw), self.scene, self.lines)

    def test_case_whitespace_and_dash_normalization_recorded(self):
        self.assertEqual(normalize(" MID – level  boss "), "mid-level boss")
        result = self.audit()
        self.assertEqual(result["errors"], [])
        self.assertTrue(result["normalizations"])

    def test_generic_labels_are_not_unsupported_names(self):
        result = self.audit(["Person A", "NPC", "narrator", "someone"])
        self.assertEqual(result["name_count"], 0)
        self.assertEqual(len(result["generic_labels"]), 4)
        self.assertEqual(result["unsupported_proper_names"], [])

    def test_proper_names_and_glossary(self):
        self.assertEqual(self.audit(["Asuna"])["unsupported_proper_names"], ["Asuna"])
        self.assertEqual(self.audit(["Klein"])["unsupported_proper_names"], [])

    def test_named_attribution_and_relationship_claims(self):
        a, r = claim_violations("Klein, who is new, asks Kirito for tips. They have a close relationship.", "My name is Klein. I'm new.", ["Klein", "Kirito"])
        self.assertTrue(a)
        self.assertTrue(r)
        self.assertEqual(claim_violations("Klein says hello.", "Klein says hello.", ["Klein"]), ([], []))

    def test_unsupported_words_are_not_normalized_away(self):
        self.assertIn("quote not present after normalization", self.audit(quote="A very strong boss.")["errors"])
