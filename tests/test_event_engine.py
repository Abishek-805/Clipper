import unittest

from evaluation.event_engine import assess_candidate_interval, build_event_proposals
from evaluation.providers import MockVisualEventAnalyzer


def _words(line, start, step=.32):
    result = []
    for token in line.split():
        result.append({"start": start, "end": start + .22, "text": token})
        start += step
    return result


def _build(lines=(), duration=80, motion=None, audio=None):
    words = []
    for text, start in lines:
        words.extend(_words(text, start))
    analysis = {"source": "fixture.mp4", "source_duration": duration, "scene_cuts": []}
    cache = {"motion": motion or [0.0] * duration, "audio_energy": audio or [0.0] * duration}
    return build_event_proposals(analysis, words, cache), words, cache


class EventEngineTests(unittest.TestCase):
    def test_complete_exchange_has_timestamped_question_and_following_speech(self):
        result, _, _ = _build([("Who opened the gate?", 10), ("I opened it yesterday.", 12)])
        event = result["events"][0]
        self.assertEqual("question_then_following_speech_turn", event["structure"])
        self.assertEqual(["unknown"], [event["category"]])
        self.assertEqual(2, len(event["beats"]))
        self.assertEqual([[10.0, 11.18], [12.0, 13.18]], event["evidence_timestamps"])

    def test_question_without_answer_does_not_invent_payoff(self):
        result, _, _ = _build([("Why did the tower fall?", 10)])
        event = result["events"][0]
        self.assertEqual("single_speech_turn", event["structure"])
        self.assertIsNone(event["payoff_evidence"])
        self.assertEqual("NOT_ASSESSED", event["editorial_assessment"]["status"])

    def test_reveal_phrase_then_reaction_is_structural_evidence_only(self):
        result, _, _ = _build([("There is only one way.", 10), ("You must clear the game.", 13), ("What? Why?", 16)])
        event = result["events"][0]
        self.assertEqual("reveal_phrase_then_possible_reaction", event["structure"])
        self.assertIn("only one way", event["escalation_evidence"])
        self.assertIn("Why?", event["payoff_evidence"])
        self.assertEqual("unknown", event["category"])

    def test_high_motion_without_speech_stays_unclassified(self):
        motion, audio = [0.0] * 40, [0.0] * 40
        for i in range(8, 28):
            motion[i], audio[i] = .9, .6
        result, _, _ = _build(duration=40, motion=motion, audio=audio)
        event = next(e for e in result["events"] if e["structure"] == "unclassified_visual_activity_span")
        self.assertEqual("unknown", event["category"])
        self.assertIn("activity_only_meaning_unverified", event["negative_flags"])
        self.assertIn("Motion is not proof", event["editorial_assessment"]["reason"])
        self.assertTrue(event["beats"][0]["beat_id"].startswith(event["event_id"].replace("event", "beat")))

    def test_visual_only_motion_survives_without_speech_or_audio(self):
        motion = [0.0] * 40
        motion[8:24] = [.8] * 16
        result, _, _ = _build(duration=40, motion=motion)
        event = next(e for e in result["events"] if e["structure"] == "unclassified_visual_activity_span")
        self.assertEqual("unknown", event["category"])
        self.assertEqual(0, event["signal_evidence"]["word_count"])

    def test_quiet_silent_static_episode_produces_no_activity_event(self):
        result, _, _ = _build(duration=40)
        self.assertEqual([], result["events"])

    def test_scene_change_does_not_split_dialogue_or_infer_comedy(self):
        words = _words("Why did the robot cross the road?", 10) + _words("I do not know.", 12)
        result = build_event_proposals({"source_duration": 40, "scene_cuts": [11]}, words,
                                       {"motion": [0] * 40, "audio_energy": [0] * 40})
        self.assertEqual(1, result["event_count"])
        self.assertTrue(result["timeline"][11]["scene_boundary"])
        self.assertEqual("question_then_following_speech_turn", result["events"][0]["structure"])
        self.assertEqual("unknown", result["events"][0]["category"])

    def test_possible_lyric_repetition_is_warned_not_rejected_as_dialogue(self):
        result, _, _ = _build([("la la la la la la la la", 10)])
        self.assertIn("possible_lyrics_or_repetitive_asr_requires_review", result["events"][0]["negative_flags"])

    def test_opening_and_ending_warnings_use_general_timing_and_text_evidence(self):
        result, _, _ = _build([("Can you guess what happened?", 2), ("Thanks for watching.", 74)], duration=80)
        flags = [flag for event in result["events"] for flag in event["negative_flags"]]
        self.assertIn("near_episode_opening_review_title_or_recap_possible", flags)
        self.assertIn("near_episode_end_review_credits_possible", flags)
        self.assertIn("closing_language_detected_asr_only", flags)

    def test_long_gap_splits_events_and_warns_on_old_wide_interval(self):
        result, words, cache = _build([("The first group finished.", 5), ("A different conversation starts.", 35)])
        self.assertEqual(2, len(result["events"]))
        interval = assess_candidate_interval(5, 40, 80, words, cache)
        self.assertIn("long_unexplained_speech_gap", interval["warnings"])
        self.assertIn("multiple_speech_islands_separated_by_long_gap", interval["warnings"])

    def test_incomplete_sentence_boundaries_are_flagged_using_neighbor_words(self):
        words = _words("The machine will", 9) + _words("destroy your brain.", 11)
        interval = assess_candidate_interval(10, 11.5, 40, words, {"motion": [0] * 40, "audio_energy": [0] * 40})
        self.assertIn("candidate_starts_inside_transcript_sentence", interval["warnings"])
        self.assertIn("candidate_ends_inside_transcript_sentence", interval["warnings"])

    def test_natural_event_boundaries_are_variable_and_not_padded_to_a_quota(self):
        result, _, _ = _build([("This is a short complete thought.", 10), ("Another separate thought.", 40)])
        self.assertEqual(2, result["event_count"])
        self.assertTrue(all(event["duration"] < 12 for event in result["events"]))
        self.assertTrue(all("short_event_keep_natural_duration_do_not_pad" in event["negative_flags"]
                            for event in result["events"]))

    def test_no_semantic_provider_or_editorial_claim_is_recorded(self):
        result, _, _ = _build([("What happened?", 10)])
        event = result["events"][0]
        self.assertEqual("unavailable_no_vlm", event["semantic_interpretation"])
        self.assertEqual("NOT_ASSESSED", event["editorial_assessment"]["status"])
        self.assertEqual(0, result["editorial_assessment_count"])

    def test_mock_visual_provider_stays_explicitly_nonsemantic(self):
        response = MockVisualEventAnalyzer().analyze({"frames": ["test-frame"], "timestamps": [12.0]})
        self.assertEqual("deterministic-mock", response["provider"])
        self.assertIn("no pixels", response["uncertainty"])


if __name__ == "__main__":
    unittest.main()
