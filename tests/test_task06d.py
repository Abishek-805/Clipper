import copy
import json
import tempfile
import unittest
from pathlib import Path

from evaluation.anime_review import (SCHEMA, build_dataset, evaluate_dataset, load_dataset,
                                    review_candidates, save_dataset, validate_dataset)
from evaluation.providers import MockVisualEventAnalyzer, validate_request, validate_result


def fixture_manifest():
    candidates = []
    for i in range(60):
        start = i * 10
        candidates.append({"candidate_id": f"c{i}", "start": start, "end": start + 6, "duration": 6,
                           "score": round((60-i) / 60, 4), "selected": i in {0, 20, 40},
                           "boundary_valid": True, "sources": ["speech"] if i % 2 else ["scene"],
                           "feature_scores": {"average_motion": .9 if i % 3 == 0 else .2,
                                              "peak_motion": .9, "average_audio_energy": .8,
                                              "speech_density": .7 if i % 2 else .1}})
    candidates.append({"candidate_id": "bad", "start": 1, "end": 4, "boundary_valid": False})
    return {"source_path": "episode.mkv", "source_size": 123, "source_mtime_ns": 456,
            "source_duration": 600, "candidate_selection": {"candidates": candidates, "rejections": []}}


class Task06DEvaluationTests(unittest.TestCase):
    def test_versioned_json_schema_requires_human_annotation_provenance(self):
        schema = json.loads((Path(__file__).parents[1] / "evaluation" / "anime_highlight_dataset.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(schema["properties"]["schema"]["const"], SCHEMA)
        required = schema["properties"]["annotations"]["items"]["required"]
        self.assertTrue({"candidate_id", "source_fingerprint", "source_interval", "decision", "category", "annotated_at"} <= set(required))

    def test_review_sampling_is_stable_spread_and_keeps_predictions_unlabelled(self):
        manifest = fixture_manifest()
        selected = review_candidates(manifest["candidate_selection"]["candidates"], 600, 36)
        self.assertEqual(selected, review_candidates(manifest["candidate_selection"]["candidates"], 600, 36))
        self.assertEqual(len(selected), 36)
        regions = {(item["start"] + item["end"]) / 2 // 100 for item in selected}
        self.assertEqual(regions, {0, 1, 2, 3, 4, 5})
        data = build_dataset(manifest, [{"word": "Hello", "start": 1, "end": 2}], 36)
        self.assertEqual(data["schema"], SCHEMA)
        self.assertEqual(data["annotations"], [])
        self.assertTrue(all(c["reviewer_label"] is None for c in data["candidates"]))

    def test_annotation_round_trip_and_trust_boundary_validation(self):
        data = build_dataset(fixture_manifest(), [], 10)
        candidate = data["candidates"][0]
        data["annotations"] = [{"candidate_id": candidate["candidate_id"], "source_fingerprint": data["source"]["fingerprint"],
                                "source_interval": [candidate["start"], candidate["end"]], "decision": "keep",
                                "category": "Action", "ratings": {"hook": 4}, "rejection_reasons": [],
                                "notes": "human label", "annotated_at": "2026-11-13T00:00:00Z"}]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dataset.json"
            save_dataset(path, data)
            self.assertEqual(load_dataset(path), data)
        invalid = copy.deepcopy(data)
        invalid["annotations"][0]["ratings"]["hook"] = 8
        with self.assertRaises(ValueError):
            validate_dataset(invalid)

    def test_metrics_wait_for_five_real_labels_and_use_only_annotation_data(self):
        data = build_dataset(fixture_manifest(), [], 10)
        self.assertEqual(evaluate_dataset(data)["status"], "INSUFFICIENT_DATA")
        for candidate in data["candidates"][:5]:
            data["annotations"].append({"candidate_id": candidate["candidate_id"],
                                        "source_fingerprint": data["source"]["fingerprint"],
                                        "source_interval": [candidate["start"], candidate["end"]], "decision": "keep",
                                        "category": "Action", "ratings": {"hook": 4, "context": 3, "payoff": 5},
                                        "rejection_reasons": [], "annotated_at": "test"})
        result = evaluate_dataset(data)
        self.assertEqual(result["status"], "EVALUATED")
        self.assertEqual(result["human_label_count"], 5)
        self.assertEqual(result["metrics"]["precision_at_k_reviewed_candidates"], 1.0)
        self.assertEqual(result["metrics"]["hook_acceptance"], 1.0)
        self.assertEqual(result["metrics"]["category_agreement"], None)

    def test_visual_mock_is_deterministic_and_explicitly_not_model_inference(self):
        request = {"frames": ["a.jpg", "b.jpg", "c.jpg"], "timestamps": [1.0, 2.0, 3.0],
                   "transcript_excerpt": "", "audio_event_description": None}
        provider = MockVisualEventAnalyzer()
        first, second = provider.analyze(request), provider.analyze(request)
        self.assertEqual(first, second)
        self.assertEqual(first["provider"], "deterministic-mock")
        self.assertIn("no pixels", first["uncertainty"])
        self.assertEqual(validate_request(request), request)
        self.assertEqual(validate_result(first), first)
        with self.assertRaises(ValueError):
            provider.analyze({"frames": [], "timestamps": []})


if __name__ == "__main__":
    unittest.main()
