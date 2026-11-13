import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from evaluation.build_episode_brain import _parse_srt, extract_subtitle_tracks
from evaluation.episode_brain import build_episode_brain
from evaluation.llama_cpp_vlm import LlamaCppVisualEventAnalyzer
from evaluation.providers import MockVisualEventAnalyzer


def _make_words(lines, word_seconds=.32):
    words, cursor = [], 0.0
    for text, start in lines:
        cursor = start
        for token in text.split():
            words.append({"start": cursor, "end": cursor+.2, "text": token})
            cursor += word_seconds
    return words


def _build(words, duration=120, cuts=None, motion=None, audio=None, clips=None):
    analysis = {"source": "episode.mkv", "source_duration": duration,
                "scene_cuts": cuts or [], "clips": clips or []}
    cache = {"motion": motion or [0.0]*duration, "audio_energy": audio or [0.0]*duration}
    transcript = {"language": "en", "device": "cpu", "precision": "int8",
                  "outcome": "CPU_FALLBACK", "words": words}
    return build_episode_brain(analysis, transcript, cache)


class EpisodeBrainTests(unittest.TestCase):
    def test_scene_intervals_and_cached_bins_cover_the_whole_episode(self):
        brain = _build(_make_words([("Hello there.", 2)]), duration=31, cuts=[10, 20])
        scenes = brain["timeline"]["scene_intervals"]
        self.assertEqual([(0.0, 10.0), (10.0, 20.0), (20.0, 31.0)], [(s["start"], s["end"]) for s in scenes])
        self.assertTrue(brain["timeline"]["coverage_complete"])
        self.assertEqual(31, len(brain["timeline"]["signal_bins"]))

    def test_empty_episode_regions_remain_present_and_visual_claims_stay_unknown(self):
        brain = _build([], duration=20, cuts=[5, 10])
        self.assertEqual(3, len(brain["scene_memory"]))
        self.assertTrue(all(scene["visual_description"] is None for scene in brain["scene_memory"]))
        self.assertEqual([], brain["entity_memory"]["entities"])

    def test_exact_word_quote_and_question_followed_by_speech_are_timestamped(self):
        words = _make_words([("Why did the gate open?", 10), ("I opened it myself.", 13)])
        brain = _build(words, duration=40, cuts=[12])
        self.assertEqual("Why did the gate open? I opened it myself.", brain["moments"][0]["exact_quote"])
        self.assertEqual("unknown", brain["moments"][0]["category"])
        self.assertEqual("NOT_ASSESSED", brain["moments"][0]["editorial_assessment"]["status"])
        self.assertEqual(2, len(brain["moments"][0]["turns"]))
        self.assertEqual("possible_question_followed_by_speech", brain["plot_memory"]["relationships"][0]["type"])

    def test_important_phrase_index_preserves_asr_text_and_scene_provenance(self):
        words = _make_words([("The only way is forward.", 12)])
        brain = _build(words, duration=60, cuts=[10])
        phrase = brain["important_phrase_index"][0]
        self.assertEqual("The only way is forward.", phrase["exact_text"])
        self.assertEqual("scene_0002", phrase["scene_id"])
        self.assertIn("explicit_reveal_phrase", phrase["structural_cues"])
        self.assertIsNone(phrase["importance"])

    def test_lexical_callback_is_only_a_candidate_across_scenes(self):
        words = _make_words([("The silver key opens doors.", 3), ("The silver key matters.", 25)])
        brain = _build(words, duration=50, cuts=[15])
        link = brain["plot_memory"]["possible_cross_scene_callbacks"][0]
        self.assertEqual("possible_cross_scene_lexical_callback", link["relationship"])
        self.assertEqual("lexical_overlap_only", link["confidence"])
        self.assertIn("silver", link["shared_terms"])

    def test_long_connected_dialogue_is_not_cut_by_fixed_duration(self):
        lines = [("One more complete sentence.", i*3.0) for i in range(30)]
        brain = _build(_make_words(lines), duration=120)
        self.assertTrue(any(moment["duration"] > 60 for moment in brain["moments"]))
        self.assertFalse(brain["ranking"]["fixed_duration_cap"])

    def test_speech_free_motion_event_remains_reviewable_and_unknown(self):
        motion = [0.0]*40
        motion[8:30] = [.9]*22
        brain = _build([], duration=40, motion=motion)
        moment = next(m for m in brain["moments"] if m["event_type"] == "unknown_visual_activity")
        self.assertEqual("unknown", moment["category"])
        self.assertEqual("signal_interval_only", moment["boundary_recommendation"]["confidence"])
        self.assertIsNone(moment["narrative_importance"])

    def test_no_forced_quota_or_editorial_global_rank_is_reported(self):
        brain = _build(_make_words([("A quiet line.", 5)]), duration=60)
        self.assertFalse(brain["ranking"]["forced_clip_quota"])
        self.assertEqual("NOT_RUN_NO_SEMANTIC_PROVIDER", brain["ranking"]["editorial_assessment"])
        self.assertTrue(all(moment["global_editorial_rank"] is None for moment in brain["moments"]))

    def test_old_selected_intervals_are_comparison_only_not_discovery_input(self):
        words = _make_words([("A line outside the old clip.", 75)])
        first = _build(words, duration=120)
        second = _build(words, duration=120, clips=[{"start": 0, "end": 30, "score": .8}])
        self.assertEqual([(m["start"], m["exact_quote"]) for m in first["moments"]],
                         [(m["start"], m["exact_quote"]) for m in second["moments"]])
        self.assertEqual(1, second["baseline_comparison"]["production_selection_count"])

    def test_cpu_fallback_provenance_is_retained(self):
        brain = _build(_make_words([("A cached transcript.", 5)]), duration=40)
        self.assertEqual("CPU_FALLBACK", brain["episode_overview"]["transcription"]["outcome"])
        self.assertEqual("cpu", brain["episode_overview"]["transcription"]["device"])

    def test_subtitle_cue_parser_retains_timing_and_removes_ass_markup_only(self):
        cues = _parse_srt("1\n00:00:01,200 --> 00:00:02,400\n{\\an8}A line\\Ncontinued\n")
        self.assertEqual([{"start": 1.2, "end": 2.4, "text": "A line continued"}], cues)

    def test_subtitle_track_metrics_retain_duplicate_and_coverage_warnings(self):
        raw = "1\n00:00:01,000 --> 00:00:02,000\nA repeated line.\n\n2\n00:00:03,000 --> 00:00:04,000\nA repeated line.\n"
        with patch("evaluation.build_episode_brain.subprocess.run", return_value=SimpleNamespace(stdout=raw)):
            tracks = extract_subtitle_tracks(Path("episode.mkv"),
                [{"type": "subtitle", "index": 3, "language": "eng", "default": True}], 20, [])
        self.assertEqual(2, tracks[0]["cue_count"])
        self.assertEqual(1, tracks[0]["duplicate_text_cue_count"])
        self.assertIn("many repeated cue texts", tracks[0]["reasons_for_review"])
        self.assertFalse(tracks[0]["use_as_primary_transcript"])

    def test_subtitle_evidence_is_kept_separate_from_asr_quote(self):
        words = _make_words([("Open the gate.", 4)])
        tracks = [{"stream_index": 3, "language": "eng", "cues": [{"start": 3.9, "end": 5.2, "text": "Open the door."}]}]
        brain = _build(words, duration=20)
        # Supply subtitle evidence on the same contract used by the builder.
        from evaluation.episode_brain import build_episode_brain as build
        analysis = {"source_duration": 20, "scene_cuts": []}
        cache = {"motion": [0.0]*20, "audio_energy": [0.0]*20}
        transcript = {"words": words}
        result = build(analysis, transcript, cache, tracks)
        self.assertEqual("Open the gate.", result["moments"][0]["exact_quote"])
        self.assertEqual("Open the door.", result["moments"][0]["subtitle_evidence"][0]["text"])
        self.assertEqual(1, len(brain["moments"]))

    def test_llama_adapter_is_loopback_only_and_does_not_download_or_start_runtime(self):
        analyzer = LlamaCppVisualEventAnalyzer()
        self.assertEqual("Qwen3-VL-2B-Instruct-GGUF", analyzer.model)
        with self.assertRaisesRegex(ValueError, "External inference"):
            LlamaCppVisualEventAnalyzer("http://example.com/v1/chat/completions")

    def test_mock_provider_explicitly_says_it_did_not_inspect_video_pixels(self):
        mock = MockVisualEventAnalyzer().analyze({"frames": ["fixture.jpg"], "timestamps": [2.0]})
        self.assertEqual("deterministic-mock", mock["provider"])
        self.assertIn("no pixels", mock["uncertainty"])

    def test_separate_speech_events_have_natural_nonoverlapping_intervals(self):
        words = _make_words([("The first event ends.", 5), ("A new event begins.", 30)])
        brain = _build(words, duration=60)
        self.assertEqual(2, len(brain["moments"]))
        self.assertLess(brain["moments"][0]["end"], brain["moments"][1]["start"])


if __name__ == "__main__":
    unittest.main()
