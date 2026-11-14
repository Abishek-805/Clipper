"""Tests use SYNTHETIC text only. They check the rules, not anime content."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import playbook as pb  # noqa: E402

UTT = [
    {"id": "U_0001", "text": "The bridge is closed.", "start_ms": 100000, "end_ms": 102000, "scene_id": "SC_0001"},
    {"id": "U_0002", "text": "Nobody told us about the new rule.", "start_ms": 104000, "end_ms": 107000, "scene_id": "SC_0001"},
    {"id": "U_0003", "text": "Anyone who loses is out of the mid -level game.", "start_ms": 110000, "end_ms": 114000, "scene_id": "SC_0001"},
    {"id": "U_0004", "text": "Then we have to leave tonight.", "start_ms": 128000, "end_ms": 131000, "scene_id": "SC_0001"},
]
CTX = {"utterances": UTT, "scene_ids": ["SC_0001"], "zones": [], "min_s": 20, "max_s": 58,
       "names": ["Aria"], "glossary": ["Aria"]}


def nom(**kw):
    base = {"type": "plot_reveal", "start_utterance_id": "U_0001", "end_utterance_id": "U_0004",
            "scene_ids": ["SC_0001"], "standalone": True,
            "quotes": [{"utterance_id": "U_0003", "text": "Anyone who loses is out of the mid-level game."}],
            "reason": "A line states what happens to anyone who loses."}
    base.update(kw)
    return {"nominations": [base]}


class Normalization(unittest.TestCase):
    def test_hyphen_spacing_and_quotes(self):
        self.assertEqual(pb.loose("mid -level"), pb.loose("mid-level"))
        self.assertEqual(pb.loose("I\u2019m here."), pb.loose("I'm here"))


class Prompts(unittest.TestCase):
    def test_build_fills_placeholders(self):
        p = pb.build_prompt("scene_summary", {"NAMES": "Aria", "SCENE_ID": "SC_0001",
                                              "UTTERANCES": pb.format_utterances(UTT)})
        self.assertIn("U_0001: The bridge is closed.", p)
        self.assertIn("Never output timestamps", p)
        self.assertLess(pb.estimate_tokens(p), 1800)

    def test_unfilled_placeholder_raises(self):
        with self.assertRaises(ValueError):
            pb.build_prompt("scene_summary", {"NAMES": "x"})

    def test_all_schemas_load(self):
        for m in pb.MODULES:
            self.assertEqual(pb.schema(m)["type"], "object")


class SceneSummary(unittest.TestCase):
    ctx = {"utterances": UTT, "names": ["Aria"], "glossary": ["Aria"]}

    def good(self, **kw):
        d = {"summary": "The bridge is closed and a new rule is mentioned.", "names_used": [],
             "tone": "tense", "fight_described": False}
        d.update(kw)
        return d

    def test_accepts_clean(self):
        self.assertTrue(pb.validate_scene_summary(self.good(), self.ctx)["ok"])

    def test_generic_label_is_not_an_invented_name(self):
        r = pb.validate_scene_summary(self.good(summary="Person A says the bridge is closed.".replace("says", "notes")), self.ctx)
        self.assertTrue(r["ok"], r)

    def test_rejects_invented_name(self):
        r = pb.validate_scene_summary(self.good(summary="The group follows Marcus across the bridge."), self.ctx)
        self.assertFalse(r["ok"])

    def test_rejects_speaker_attribution(self):
        r = pb.validate_scene_summary(self.good(summary="Aria said the bridge is closed."), self.ctx)
        self.assertFalse(r["ok"])

    def test_rejects_relationship_claim(self):
        r = pb.validate_scene_summary(self.good(summary="They are close friends discussing a rule."), self.ctx)
        self.assertFalse(r["ok"])

    def test_rejects_fight_without_text_support(self):
        self.assertFalse(pb.validate_scene_summary(self.good(fight_described=True), self.ctx)["ok"])

    def test_rejects_long_summary(self):
        self.assertFalse(pb.validate_scene_summary(self.good(summary="word " * 25), self.ctx)["ok"])


class Nominations(unittest.TestCase):
    def test_accepts_valid_and_resolves_times(self):
        r = pb.validate_nominations(nom(), CTX)
        self.assertTrue(r["ok"], r)
        res = r["results"][0]["resolved"]
        self.assertEqual(res["start_ms"], 99700)
        self.assertEqual(res["end_ms"], 131600)

    def test_normalized_quote_matches(self):
        self.assertTrue(pb.validate_nominations(nom(), CTX)["ok"])

    def test_rejects_fake_quote(self):
        n = nom(quotes=[{"utterance_id": "U_0003", "text": "Everyone loses eventually."}])
        self.assertFalse(pb.validate_nominations(n, CTX)["ok"])

    def test_rejects_unknown_id_and_reversed_order(self):
        self.assertFalse(pb.validate_nominations(nom(start_utterance_id="U_0099"), CTX)["ok"])
        self.assertFalse(pb.validate_nominations(nom(start_utterance_id="U_0004", end_utterance_id="U_0001"), CTX)["ok"])

    def test_rejects_quote_outside_range(self):
        n = nom(start_utterance_id="U_0001", end_utterance_id="U_0002",
                quotes=[{"utterance_id": "U_0003", "text": "Anyone who loses is out of the mid-level game."}])
        self.assertFalse(pb.validate_nominations(n, CTX)["ok"])

    def test_rejects_short_duration(self):
        n = nom(start_utterance_id="U_0001", end_utterance_id="U_0002",
                quotes=[{"utterance_id": "U_0001", "text": "The bridge is closed."}])
        self.assertFalse(pb.validate_nominations(n, CTX)["ok"])

    def test_hard_zone_rejects_soft_zone_warns(self):
        hard = dict(CTX, zones=[{"start_ms": 95000, "end_ms": 140000, "level": "HARD", "label": "ED"}])
        self.assertFalse(pb.validate_nominations(nom(), hard)["ok"])
        soft = dict(CTX, zones=[{"start_ms": 95000, "end_ms": 140000, "level": "SOFT", "label": "late prior"}])
        r = pb.validate_nominations(nom(), soft)
        self.assertTrue(r["ok"])
        self.assertTrue(r["results"][0]["warnings"])

    def test_rejects_unsupported_claim_in_reason(self):
        self.assertFalse(pb.validate_nominations(nom(reason="They are rivals who hate each other."), CTX)["ok"])

    def test_zero_nominations_is_valid(self):
        r = pb.validate_nominations({"nominations": []}, CTX)
        self.assertTrue(r["ok"])
        self.assertEqual(r["accepted_count"], 0)


class Action(unittest.TestCase):
    def test_confirm_needs_evidence(self):
        self.assertFalse(pb.validate_action({"verdict": "CONFIRM", "evidence": ["swords clash"], "confidence": "high", "ends_cleanly": "true"}, {})["ok"])
        self.assertTrue(pb.validate_action({"verdict": "CONFIRM", "evidence": ["swords clash", "impact flash"], "confidence": "medium", "ends_cleanly": "true"}, {})["ok"])

    def test_unclear_and_reject_valid(self):
        for v in ("UNCLEAR", "REJECT"):
            self.assertTrue(pb.validate_action({"verdict": v, "evidence": [], "confidence": "low", "ends_cleanly": "unclear"}, {})["ok"])

    def test_bad_verdict(self):
        self.assertFalse(pb.validate_action({"verdict": "YES", "evidence": [], "confidence": "low", "ends_cleanly": "unclear"}, {})["ok"])

    def test_invented_name_rejected(self):
        r = pb.validate_action({"verdict": "CONFIRM", "evidence": ["Marcus swings a sword", "impact flash"], "confidence": "high", "ends_cleanly": "true"}, {"names": ["Aria"]})
        self.assertFalse(r["ok"])


class Hooks(unittest.TestCase):
    def good(self, **kw):
        d = {"hooks": ["The bridge is closed", "A rule nobody mentioned", "Leave tonight or lose"],
             "context": "A group faces a new rule at a closed bridge.", "source_ids": ["U_0001", "U_0002"]}
        d.update(kw)
        return d

    def test_accepts_clean(self):
        r = pb.validate_hook(self.good(), {"utterances": UTT, "names": []})
        self.assertTrue(r["ok"], r)

    def test_rejects_clickbait_and_long_hook(self):
        r = pb.validate_hook(self.good(hooks=["You won't believe this bridge", "A rule nobody mentioned", "Leave tonight"]), {"utterances": UTT, "names": []})
        self.assertFalse(r["ok"])
        r = pb.validate_hook(self.good(hooks=["one two three four five six seven eight nine", "a rule", "the bridge"]), {"utterances": UTT, "names": []})
        self.assertFalse(r["ok"])

    def test_rejects_final_line_spoiler(self):
        r = pb.validate_hook(self.good(hooks=["Then we have to leave tonight", "A rule nobody mentioned", "The bridge is closed"]), {"utterances": UTT, "names": []})
        self.assertFalse(r["ok"])

    def test_rejects_unknown_source_id(self):
        self.assertFalse(pb.validate_hook(self.good(source_ids=["U_0099"]), {"utterances": UTT, "names": []})["ok"])


if __name__ == "__main__":
    unittest.main()
