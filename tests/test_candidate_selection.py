import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from benchmark_candidates import build_benchmark
import clipper
from clipper import (
    VideoInfo, Word, _read_analysis_cache, _robust_normalize, deduplicate_candidates, detect_activity_regions, extract_speech_regions,
    generate_candidates, process_video, rank_candidates, refine_clip_boundaries,
    select_clips, select_diverse_candidates,
)


class CandidateSelectionTests(unittest.TestCase):
    def test_scene_proposals_produce_variable_durations(self):
        result = generate_candidates(100, [0.0] * 100, [0.0] * 100, [25, 51, 78], [], 20, 55)
        durations = {round(c["end"] - c["start"]) for c in result["candidates"] if "scene" in c["sources"]}
        self.assertTrue({25, 26, 27, 49, 51, 53}.intersection(durations))
        self.assertGreater(len(durations), 1)

    def test_activity_windows_offer_multiple_proposal_lengths(self):
        motion = [0.0] * 120
        motion[43:48] = [8.0] * 5
        result = generate_candidates(120, motion, [0.0] * 120, [], [], 20, 55)
        durations = {round(c["end"] - c["start"]) for c in result["candidates"] if "activity" in c["sources"]}
        self.assertTrue({20, 25, 30, 40, 45, 55}.issubset(durations))

    def test_minimum_and_maximum_duration_limits(self):
        motion = [0.0] * 120
        motion[42:50] = [5.0] * 8
        selected = select_clips(120, motion, [0.0] * 120, [42, 50], [], 5, 20, 55)["selected"]
        self.assertTrue(selected)
        self.assertTrue(all(20 <= c["duration"] <= 55 for c in selected))

    def test_short_source_is_skipped_with_explicit_policy(self):
        result = generate_candidates(19, [1.0] * 19, [1.0] * 19, [], [], 20, 55)
        self.assertEqual("skip_below_minimum", result["short_source_policy"])
        self.assertEqual([], result["candidates"])

    def test_candidate_generation_honors_cancellation(self):
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaises(InterruptedError):
            generate_candidates(120, [0.0] * 120, [0.0] * 120, [20, 40], [], 20, 55, cancelled)

    def test_scene_candidates_retain_boundary_provenance(self):
        result = generate_candidates(90, [0.0] * 90, [0.0] * 90, [22, 48, 72], [], 20, 55)
        scene = next(c for c in result["candidates"] if c["start"] == 0 and c["end"] == 22)
        self.assertEqual("scene", scene["sources"][0])
        self.assertEqual([False, True], scene["scene_edges"])

    def test_audio_only_event_generates_activity_candidate(self):
        audio = [0.0] * 100
        audio[41:52] = [4.0] * 11
        result = generate_candidates(100, [0.0] * 100, audio, [], [], 20, 55)
        self.assertTrue(result["activity_regions"])
        self.assertTrue(any("activity" in c["sources"] for c in result["candidates"]))

    def test_single_audio_peak_is_recorded_as_a_proposal_but_not_selected_by_itself(self):
        audio = [0.0] * 100
        audio[44] = 100.0
        result = select_clips(100, [0.0] * 100, audio, [], [], 5, 20, 55)
        self.assertTrue(any(region["start"] <= 44 < region["end"] for region in result["activity_regions"]))
        self.assertEqual([], result["selected"])

    def test_speech_units_use_pause_and_sentence_boundaries(self):
        words = [Word(1, 1.2, "hello"), Word(1.3, 1.5, "there."),
                 Word(3, 3.2, "second"), Word(3.3, 3.6, "thought?")]
        regions = extract_speech_regions(words)
        self.assertEqual(2, len(regions))
        self.assertTrue(all(region["complete_sentence"] for region in regions))

    def test_long_dialogue_generates_speech_candidate(self):
        words = [Word(float(t), float(t) + 0.2, "spoken") for t in range(10, 40)]
        result = generate_candidates(80, [0.0] * 80, [0.0] * 80, [], words, 20, 55)
        self.assertTrue(any("speech" in c["sources"] for c in result["candidates"]))

    def test_no_audio_and_no_speech_visual_action_remains_eligible(self):
        motion = [0.0] * 100
        motion[40:60] = [8.0] * 20
        selected = select_clips(100, motion, [0.0] * 100, [], [], 3, 20, 55)["selected"]
        self.assertTrue(selected)
        self.assertGreater(max(c["feature_scores"]["average_motion"] for c in selected), 0)

    def test_quiet_static_source_returns_fewer_than_requested(self):
        self.assertEqual([], select_clips(120, [0.0] * 120, [0.0] * 120, [], [], 5, 20, 55)["selected"])

    def test_word_boundaries_are_not_cut_mid_word(self):
        words = [Word(9.5, 10.2, "word"), Word(29.5, 30.3, "ending.")]
        candidate = {"start": 9.8, "end": 29.8, "sources": ["activity"]}
        refined = refine_clip_boundaries(candidate, 60, words, [], 20, 55)
        self.assertEqual((9.5, 30.3), (refined["start"], refined["end"]))

    def test_sentence_boundaries_refine_inside_phrase(self):
        words = [Word(10, 10.3, "A"), Word(10.3, 11, "sentence."),
                 Word(30, 30.3, "Next"), Word(30.3, 31, "one.")]
        refined = refine_clip_boundaries({"start": 10.5, "end": 30.5, "sources": ["speech"]},
                                         60, words, [], 20, 55)
        self.assertEqual((10.0, 31.0), (refined["start"], refined["end"]))
        self.assertTrue(refined["boundary_refinement"]["start_changed"])

    def test_boundary_context_margin_is_configurable(self):
        words = [Word(10, 10.3, "A"), Word(10.3, 11, "sentence.")]
        candidate = {"start": 10.5, "end": 35.0, "sources": ["speech"]}
        refined = refine_clip_boundaries(candidate, 60, words, [], 20, 55, context_margin=0.1)
        self.assertEqual(10.3, refined["start"])

    def test_scoring_weights_can_be_overridden(self):
        candidate = {"start": 20.0, "end": 40.0, "sources": ["activity"], "scene_edges": [False, False]}
        motion = [0.0] * 100
        motion[20:40] = [5.0] * 20
        weighted = rank_candidates([candidate], 100, motion, [0.0] * 100, [], [], 20, 55,
                                   weights={"average_motion": 1.0})
        self.assertEqual(1.0, weighted[0]["score"])

    def test_candidate_overlap_deduplication_uses_iou(self):
        candidates = [{"start": 0, "end": 25, "score": 0.8}, {"start": 2, "end": 27, "score": 0.6},
                      {"start": 40, "end": 65, "score": 0.5}]
        kept, rejected = deduplicate_candidates(candidates, 0.35)
        self.assertEqual(2, len(kept))
        self.assertEqual("overlap duplicate", rejected[0]["reason"])
        self.assertGreater(rejected[0]["iou"], 0.35)

    def test_diversity_applies_configured_minimum_distance(self):
        def item(start, end, score):
            return {"start": start, "end": end, "score": score,
                    "feature_scores": {"average_motion": 0.5, "average_audio_energy": 0.2,
                                       "speech_density": 0.0, "dialogue_completeness": 0.0,
                                       "scene_transition_quality": 0.0}}
        selected, rejected = select_diverse_candidates([item(0, 20, .9), item(24, 44, .8)], 2, 100, 5)
        self.assertEqual(1, len(selected))
        self.assertEqual("duplicate or too close", rejected[0]["reason"])

    def test_insufficient_candidate_count_does_not_fill_with_weak_clips(self):
        motion = [0.0] * 100
        motion[20:40] = [9.0] * 20
        result = select_clips(100, motion, [0.0] * 100, [], [], 5, 20, 55)
        self.assertLess(len(result["selected"]), 5)

    def test_robust_candidate_features_are_bounded_with_extreme_outlier(self):
        motion = [0.1] * 100
        motion[50] = 1e9
        candidates = generate_candidates(100, motion, [0.0] * 100, [25, 50, 75], [], 20, 55)["candidates"]
        ranked = rank_candidates(candidates, 100, motion, [0.0] * 100, [25, 50, 75], [], 20, 55)
        self.assertTrue(all(0 <= c["score"] <= 1 for c in ranked))
        self.assertTrue(all(0 <= value <= 1 for c in ranked for value in c["feature_scores"].values() if isinstance(value, float)))

    def test_invalid_signal_values_are_sanitized(self):
        self.assertEqual([0.0, 0.0, 1.0], _robust_normalize([float("nan"), None, 5.0]))

    def test_corrupt_analysis_cache_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "analysis_cache.json"
            cache.write_text(json.dumps({"schema": 1, "fingerprint": "same", "motion": ["bad"],
                                         "audio_energy": [0.0], "scene_cuts": []}), encoding="utf-8")
            self.assertIsNone(_read_analysis_cache(cache, "same"))

    def test_unresolvable_word_safe_boundary_is_marked_invalid(self):
        candidate = {"start": 0.0, "end": 55.0, "sources": ["activity"]}
        words = [Word(0.0, 60.0, "long")]
        refined = refine_clip_boundaries(candidate, 80, words, [], 20, 55)
        self.assertFalse(refined["boundary_valid"])

    def test_candidate_report_is_json_serializable(self):
        motion = [0.0] * 80
        motion[30:50] = [4.0] * 20
        report = select_clips(80, motion, [0.0] * 80, [30, 50], [], 3, 20, 55)
        payload = json.loads(json.dumps(report))
        self.assertIn("candidate_count", payload)
        self.assertIn("rejected_count", payload)
        self.assertTrue(all("feature_scores" in candidate for candidate in payload["candidates"]))

    def test_synthetic_benchmark_detects_known_events_without_invalid_boundaries(self):
        result = build_benchmark()
        self.assertEqual(1.0, result["metrics"]["candidate_recall"])
        self.assertEqual(1.0, result["metrics"]["selected_interval_precision"])
        self.assertEqual(0, result["metrics"]["invalid_boundary_count"])
        self.assertEqual(0.0, result["metrics"]["selected_duplicate_rate"])

    def test_short_source_manifest_records_skip_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "short.mp4"
            source.write_bytes(b"source")
            with patch("clipper.probe", return_value=VideoInfo(12, 1920, 1080, 30, False, 0)):
                progress = []
                count = process_video(source, root / "out", "tiny", 1, 20, 55, __import__("threading").Event(), progress.append)
            manifest = next((root / "out").rglob("analysis.json"))
            data = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(0, count)
            self.assertEqual("skip_below_minimum", data["candidate_selection"]["short_source_policy"])
            self.assertIn("shorter than", "\n".join(progress))

    def test_legacy_manifest_is_read_and_reprocessed_with_new_engine(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "episode.mp4"
            source.write_bytes(b"source")
            source_stat = source.stat()
            out = root / "out"
            suffix = __import__("hashlib").sha1(str(source.resolve()).casefold().encode()).hexdigest()[:8]
            folder = out / f"episode_{suffix}"
            folder.mkdir(parents=True)
            (folder / "clip_001.mp4").write_bytes(b"old clip")
            (folder / "analysis.json").write_text(json.dumps({
                "source_size": source_stat.st_size, "source_mtime_ns": source_stat.st_mtime_ns,
                "settings": {"model": "tiny", "clip_count": 1, "min_length": 20, "max_length": 55, "encoder": "auto"},
                "clips": [{"filename": "clip_001.mp4"}],
            }), encoding="utf-8")
            selection = {"candidate_count": 0, "rejected_count": 0, "selected_count": 0,
                         "short_source_policy": "none", "activity_regions": [], "speech_regions": [],
                         "candidates": [], "selected": [], "rejections": []}
            with patch("clipper.probe", return_value=VideoInfo(120, 1920, 1080, 30, False, 0)), \
                 patch("clipper._motion", return_value=[0.0] * 120), \
                 patch("clipper._scene_cuts", return_value=([], "none")), \
                 patch("clipper.select_clips", return_value=selection) as select:
                process_video(source, out, "tiny", 1, 20, 55, __import__("threading").Event(), lambda _message: None)
            select.assert_called_once()
            updated = json.loads((folder / "analysis.json").read_text(encoding="utf-8"))
            self.assertEqual(0, updated["candidate_selection"]["selected_count"])
            self.assertEqual("scene-aware-variable-v1", updated["settings"]["candidate_engine"])

    def test_analysis_arrays_are_reused_when_clip_settings_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "episode.mp4"
            source.write_bytes(b"source")
            selection = {"candidate_count": 0, "rejected_count": 0, "selected_count": 0,
                         "short_source_policy": "none", "activity_regions": [], "speech_regions": [],
                         "candidates": [], "selected": [], "rejections": []}
            with patch("clipper.probe", return_value=VideoInfo(120, 1920, 1080, 30, False, 0)), \
                 patch("clipper._motion", return_value=[1.0] * 120) as motion, \
                 patch("clipper._scene_cuts", return_value=([30.0, 75.0], "2 cuts")) as scenes, \
                 patch("clipper.select_clips", return_value=selection):
                process_video(source, root / "out", "tiny", 1, 20, 55, __import__("threading").Event(), lambda _message: None)
                process_video(source, root / "out", "tiny", 2, 20, 55, __import__("threading").Event(), lambda _message: None)
            motion.assert_called_once()
            scenes.assert_called_once()
            cache = next((root / "out").rglob("analysis_cache.json"))
            cached = json.loads(cache.read_text(encoding="utf-8"))
            self.assertEqual(2, cached["schema"])
            self.assertTrue(cached["motion"])


if __name__ == "__main__":
    unittest.main()
