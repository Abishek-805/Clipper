import json
import tempfile
import unittest
from pathlib import Path

from evaluation.anime_review import SCHEMA as REVIEW_SCHEMA, evaluate_dataset
from evaluation.story_graph import (SCHEMA, action_progression, build_story_graph,
                                    cross_modal_flags, make_review_dataset,
                                    validate_story_graph)


class StoryGraphTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.frame = self.root / "frame.jpg"
        self.frame.write_bytes(b"cached evidence fixture")
        self.fingerprint = "a" * 64
        self.brain = {"source": {"fingerprint": self.fingerprint}, "source_duration": 100.0,
            "transcript": {"language": "en", "words": [
                {"text": "What", "start": 10.0, "end": 10.2},
                {"text": "is", "start": 10.2, "end": 10.3},
                {"text": "that?", "start": 10.3, "end": 10.6},
                {"text": "Training", "start": 11.0, "end": 11.5}],
                "turns": [{"beat_id": "beat_1", "start": 10.0, "end": 10.6, "text": "What is that?"},
                          {"beat_id": "beat_2", "start": 11.0, "end": 11.5, "text": "Training"}],
                "subtitle_tracks": []},
            "important_phrase_index": [{"phrase_id": "beat_1", "exact_text": "What is that?",
                "start": 10.0, "end": 10.6}],
            "scene_memory": [{"scene_id": "scene_1", "start": 0.0, "end": 50.0}],
            "timeline": {"signal_bins": [{"start": 10.0, "end": 11.0, "motion": .4}]},
            "moments": [{"event_id": "m1", "event_type": "unknown_dialogue_sequence", "start": 10.0,
                "end": 11.5, "exact_quote": "What is that? Training", "boundary_recommendation":
                    {"start": 10.0, "end": 11.5, "reason": "complete turns"}}],
            "plot_memory": {"relationships": [{"from_turn_id": "beat_1", "to_turn_id": "beat_2",
                "type": "possible_question_followed_by_speech"}], "possible_cross_scene_callbacks": []}}
        self.window = {"window_id": "window_001", "source_fingerprint": self.fingerprint,
            "start": 10.0, "end": 12.0, "timestamps": [10.0, 11.0],
            "frames": [str(self.frame), str(self.frame)], "transcript_excerpt": "Training explains skill activation.",
            "model_revision": "Qwen3-VL-2B", "runtime_version": "llama.cpp-b12284",
            "prompt_version": "test-v1", "schema_version": "test-v1",
            "result": {"description": "A fighter starts a battle and casts a spell while flying.",
                "actions": ["flying", "spell casting", "attack"], "entities": ["unknown person", "creature"],
                "category": "battle", "confidence": .95, "raw_response": "cached model output",
                "evidence_timestamps": [10.0, 11.0], "uncertainty": "unverified"}}
        self.memory = {"model_revision": "Qwen3-VL-2B", "runtime_version": "llama.cpp-b12284"}
        self.dataset = {"schema": REVIEW_SCHEMA, "schema_version": 1, "source": {
            "fingerprint": self.fingerprint, "filename": "episode.mkv", "duration": 100},
            "candidate_count": 1, "candidates": [{"candidate_id": "baseline_001", "start": 10.0,
                "end": 11.5, "duration": 1.5, "selected": True, "baseline": True, "frames": [],
                "sources": ["speech"], "score": .8}], "annotations": [], "review_sampling": {}}

    def tearDown(self):
        self.temp.cleanup()

    def graph(self):
        return build_story_graph(self.brain, self.memory, [self.window], self.dataset["candidates"])

    def test_graph_persists_stable_ids_and_all_edges_have_evidence(self):
        first, second = self.graph(), self.graph()
        self.assertEqual(first["schema"], SCHEMA)
        self.assertEqual([row["node_id"] for row in first["nodes"]], [row["node_id"] for row in second["nodes"]])
        self.assertTrue(all(edge["evidence_ids"] for edge in first["edges"]))
        self.assertEqual(first["summary"]["comparison_candidate_count"], 1)
        pairs = [(e["source"], e["target"], e["relationship"]) for e in first["edges"]]
        self.assertEqual(len(pairs), len(set(pairs)))

    def test_quotes_are_exact_and_model_actions_stay_unverified(self):
        graph = self.graph()
        turn = next(row for row in graph["nodes"] if row["node_type"] == "dialogue_turn")
        self.assertEqual(turn["exact_text"], "What is that?")
        claims = [row for row in graph["claims"] if row["predicate"] == "possible_action"]
        self.assertTrue(claims)
        self.assertEqual({row["status"] for row in claims}, {"unverified"})
        self.assertTrue(any("flying" in row["object"] for row in claims))
        self.assertTrue(any("spell" in row["object"] for row in claims))
        self.assertTrue(all(row["model_reported_confidence"] == .95 for row in claims))

    def test_quote_that_does_not_match_cached_words_is_rejected(self):
        self.brain["transcript"]["turns"][0]["text"] = "A fabricated quote."
        with self.assertRaisesRegex(ValueError, "does not match its source word timestamps"):
            self.graph()

    def test_instructional_dialogue_disagreement_is_flagged_not_resolved_as_fact(self):
        graph = self.graph()
        claim = next(row for row in graph["claims"] if row["predicate"] == "visual_description")
        self.assertEqual(claim["status"], "unverified")
        self.assertIn("instructional_dialogue_vs_combat_visual_claim_requires_human_review",
                      claim["cross_modal_disagreement_flags"])
        self.assertEqual(claim["cross_modal_agreement"], "NOT_ASSESSED")

    def test_aggregate_action_list_never_becomes_temporal_fight(self):
        graph = self.graph()
        visual_event = next(n for n in graph["nodes"] if n.get("visual_observation_id"))
        self.assertEqual(visual_event["event_type"], "unknown_event")
        self.assertEqual(visual_event["action_progression"]["type"], "unknown_event")
        self.assertEqual(action_progression([(1.0, ["attack"], ["A"])] )["type"], "unknown_event")
        progression = action_progression([(1.0, ["attack"], ["A"]), (2.0, ["block"], ["B"])])
        self.assertEqual(progression["type"], "possible_fight")
        self.assertEqual(progression["status"], "unverified")

    def test_title_or_credit_window_stays_unverified_and_does_not_change_selection(self):
        self.window.update(start=99.0, end=100.0, timestamps=[99.0])
        self.window["result"].update(category="credits", description="A closing title card and end credits.")
        graph = self.graph()
        event = next(node for node in graph["nodes"] if node.get("visual_observation_id"))
        claim = next(row for row in graph["claims"] if row["predicate"] == "visual_description")
        self.assertEqual(event["event_type"], "unknown_event")
        self.assertEqual(claim["status"], "unverified")
        self.assertFalse(graph["summary"]["production_selection_changed"])

    def test_question_followed_by_speech_remains_tentative(self):
        edge = next(e for e in self.graph()["edges"] if e["relationship"] == "possible_response_to")
        self.assertEqual(edge["status"], "unverified")
        self.assertTrue(edge["evidence_ids"])

    def test_natural_source_interval_is_not_padded_to_a_fixed_duration(self):
        node = next(n for n in self.graph()["nodes"] if n.get("identity") == "task06f:m1")
        self.assertEqual((node["start"], node["end"]), (10.0, 11.5))
        self.assertIn("complete turns", node["boundary"]["reason"])

    def test_lexical_cross_scene_relation_stays_possible(self):
        self.brain["plot_memory"]["possible_cross_scene_callbacks"] = [{
            "from_turn_id": "beat_1", "to_turn_id": "beat_2", "shared_terms": ["training"],
            "relationship": "possible_cross_scene_lexical_callback"}]
        edge = next(e for e in self.graph()["edges"] if e["relationship"] == "possible_callback_to")
        self.assertEqual(edge["status"], "unverified")
        self.assertTrue(edge["evidence_ids"])

    def test_graph_rejects_missing_claim_evidence_edge_evidence_and_bad_nodes(self):
        graph = self.graph()
        invalid = json.loads(json.dumps(graph))
        invalid["claims"][0]["evidence_ids"] = ["missing"]
        with self.assertRaises(ValueError):
            validate_story_graph(invalid)
        invalid = json.loads(json.dumps(graph))
        invalid["edges"][0]["evidence_ids"] = []
        with self.assertRaises(ValueError):
            validate_story_graph(invalid)
        invalid = json.loads(json.dumps(graph))
        invalid["claims"][0]["subject"] = "missing"
        with self.assertRaises(ValueError):
            validate_story_graph(invalid)
        invalid = json.loads(json.dumps(graph))
        invalid["nodes"][0]["end"] = 101.0
        with self.assertRaises(ValueError):
            validate_story_graph(invalid)
        invalid = json.loads(json.dumps(graph))
        action_claim = next(row for row in invalid["claims"] if row["predicate"] == "possible_action")
        signal_id = next(row["evidence_id"] for row in invalid["evidence"] if row["evidence_type"] == "cached_signal")
        action_claim["evidence_ids"] = [signal_id]
        with self.assertRaisesRegex(ValueError, "requires visual frame evidence"):
            validate_story_graph(invalid)

    def test_review_dataset_keeps_existing_annotations_without_inventing_labels(self):
        annotated = json.loads(json.dumps(self.dataset))
        annotated["annotations"] = [{"candidate_id": "baseline_001", "source_fingerprint": self.fingerprint,
            "source_interval": [10.0, 11.5], "decision": "maybe", "category": "Unclassified",
            "ratings": {}, "rejection_reasons": [], "notes": "check", "annotated_at": "2026-11-13T00:00:00Z"}]
        dataset = make_review_dataset(annotated, self.graph(), [self.window])
        self.assertEqual(dataset["annotations"], annotated["annotations"])
        self.assertIsNone(next(c for c in dataset["candidates"] if c["candidate_id"].startswith("vlm_"))["reviewer_label"])
        self.assertEqual(evaluate_dataset(dataset)["status"], "INSUFFICIENT_DATA")

    def test_source_fingerprint_mismatch_is_rejected(self):
        other = json.loads(json.dumps(self.window))
        other["source_fingerprint"] = "b" * 64
        with self.assertRaises(ValueError):
            build_story_graph(self.brain, self.memory, [other], self.dataset["candidates"])


if __name__ == "__main__":
    unittest.main()
