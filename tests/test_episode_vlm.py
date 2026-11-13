import json
import tempfile
import unittest
from pathlib import Path

from evaluation.episode_vlm import analyze_episode, build_windows, cache_key


class EpisodeVlmTests(unittest.TestCase):
    def test_windows_keep_chronological_coverage_and_timestamped_transcript(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            evidence = [{"timestamp": t, "path": f"frame_{t}.jpg"} for t in (0, 2, 4, 6, 8)]
            words = [{"text": f"w{t}", "start": t, "end": t + 1} for t in range(9)]
            windows = build_windows(evidence, root, words, size=2)
        self.assertEqual([(0.0, 2.0), (4.0, 6.0), (8.0, 8.0)],
                         [(x["start"], x["end"]) for x in windows])
        self.assertEqual([0.0, 2.0], windows[0]["timestamps"])
        self.assertIn("w2", windows[0]["transcript_excerpt"])
        self.assertEqual(5, sum(len(x["frames"]) for x in windows))

    def test_cache_key_invalidates_on_transcript_change(self):
        base = {"timestamps": [1.0], "transcript_excerpt": "original"}
        changed = {**base, "transcript_excerpt": "edited"}
        self.assertNotEqual(cache_key("source", base), cache_key("source", changed))

    def test_episode_run_resumes_cached_windows_and_writes_review_board(self):
        class Analyzer:
            calls = 0
            def analyze(self, request):
                self.calls += 1
                timestamp = request["timestamps"][0]
                return {"description": "Visible event", "entities": [], "actions": [],
                        "visual_changes": [], "emotional_cues": [], "category": "unknown",
                        "confidence": 0.0, "evidence_timestamps": [timestamp],
                        "uncertainty": "Not interpreted", "raw_response": "{}",
                        "provider": "test", "usage": {"prompt_tokens": 10, "completion_tokens": 2}}
            def analyze_context(self, request):
                timestamp = request["timestamps"][0]
                return {"scene_summary": "Visible context only.", "visual_evidence": [],
                        "dialogue_references": [], "relationships": [], "importance": "unknown",
                        "context_needed": "unknown", "suggested_start": request["start"],
                        "suggested_end": request["end"], "uncertainties": ["Mock only."],
                        "raw_response": "{}", "provider": "test",
                        "usage": {"prompt_tokens": 10, "completion_tokens": 2}}

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            frames.mkdir()
            (frames / "a.jpg").write_bytes(b"a")
            (frames / "b.jpg").write_bytes(b"b")
            timeline = {"duration": 2, "coarse_frame_timestamps": [0, 1],
                        "coarse_frame_evidence": [{"timestamp": 0, "path": "a.jpg"},
                                                   {"timestamp": 1, "path": "b.jpg"}]}
            brain = {"source": {"fingerprint": "stable"}, "transcript": {"words": []},
                     "important_phrase_index": [], "moments": []}
            analysis = {"clips": [{"start": 0, "end": 1}]}
            for name, data in (("timeline.json", timeline), ("brain.json", brain), ("analysis.json", analysis)):
                (root / name).write_text(json.dumps(data), encoding="utf-8")
            analyzer, output = Analyzer(), root / "review"
            first = analyze_episode(root / "timeline.json", root / "brain.json", root / "analysis.json", output, analyzer)
            second = analyze_episode(root / "timeline.json", root / "brain.json", root / "analysis.json", output, analyzer)
            self.assertEqual(1, analyzer.calls)
            self.assertEqual(1, first["analyzed_window_count"])
            self.assertTrue(second["coverage_complete"])
            self.assertEqual(1, second["production_selection_count"])
            self.assertTrue((output / "index.html").is_file())
            item = json.loads(next(output.glob("window_*.json")).read_text(encoding="utf-8"))
            self.assertEqual(10, item["result"]["usage"]["prompt_tokens"])


if __name__ == "__main__":
    unittest.main()
