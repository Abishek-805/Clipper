import unittest
from types import SimpleNamespace
from unittest.mock import patch

from captions import group_words
from editorial import (QUALITY_FLOOR, WEIGHTS, apply_assessment, local_endpoint, transcript_windows,
                       validate_assessment)
from vision import sample_timestamps


def words(count=8):
    return [SimpleNamespace(start=i, end=i + .8, text=("Done." if i == count - 1 else f"word{i}")) for i in range(count)]


class Task05Tests(unittest.TestCase):
    def test_endpoint_is_loopback_only_and_openai_compatible(self):
        self.assertEqual("http://localhost:11434/v1/chat/completions", local_endpoint("http://localhost:11434/v1/chat/completions"))
        for value in ("https://example.com/v1/chat/completions", "http://10.0.0.1/v1/chat/completions",
                      "http://localhost:11434/api/generate", "http://user:pass@localhost/v1/chat/completions",
                      "http://localhost/v1/chat/completions?token=secret"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                local_endpoint(value)

    def test_overlapping_windows_preserve_global_word_indices(self):
        result = transcript_windows(words(400), 10, 390, max_words=180, overlap=30)
        self.assertGreater(len(result), 2)
        self.assertEqual((10, 189), (result[0]["first_word_index"], result[0]["last_word_index"]))
        self.assertLessEqual(result[0]["last_word_index"], result[1]["first_word_index"] + 30)

    def test_editorial_score_is_weighted_and_uncertainty_does_not_inflate(self):
        value = {"category": "UNCERTAIN", "scores": {key: 2 for key in WEIGHTS}, "confidence": .3,
                 "first_word_index": 1, "last_word_index": 3, "hook": "", "build": "", "payoff": ""}
        result = validate_assessment(value, words(), 0, 8)
        self.assertEqual(.4, result["editorial_score"])
        candidate = {"score": .8}
        apply_assessment(candidate, result)
        self.assertFalse(candidate["editorial_selected"])
        self.assertEqual(QUALITY_FLOOR, .58)

    def test_rejects_mid_candidate_or_invalid_word_indices_and_scores(self):
        value = {"category": "COMEDY", "scores": {key: 4 for key in WEIGHTS}, "confidence": .8,
                 "first_word_index": 0, "last_word_index": 4, "hook": "setup", "build": "", "payoff": ""}
        with self.assertRaises(ValueError):
            validate_assessment(value, words(), 2, 5)
        value["first_word_index"], value["last_word_index"] = 1, 3
        value["scores"]["payoff"] = 9
        with self.assertRaises(ValueError):
            validate_assessment(value, words(), 1, 4)

    def test_local_assessment_passes_source_language_and_parses_json(self):
        response = SimpleNamespace(read=lambda: b'{"choices":[{"message":{"content":"{\\"category\\":\\"ACTION\\",\\"scores\\":{\\"hook\\":4,\\"coherence\\":4,\\"payoff\\":4,\\"impact\\":4,\\"self_containment\\":4,\\"visual_clarity\\":2,\\"audio_clarity\\":4},\\"confidence\\":0.7,\\"first_word_index\\":1,\\"last_word_index\\":3,\\"hook\\":\\"start\\",\\"build\\":\\"rises\\",\\"payoff\\":\\"lands\\"}"}}]}')
        with patch("editorial.urllib.request.build_opener") as opener:
            opener.return_value.open.return_value.__enter__.return_value = response
            from editorial import assess_candidate
            result = assess_candidate("http://localhost/v1/chat/completions", "local", words(), 1, 4, "ja")
            payload = opener.return_value.open.call_args.args[0].data.decode()
        self.assertEqual("ACTION", result["category"])
        self.assertIn("Language: ja", payload)

    def test_adaptive_frames_are_bounded_ordered_and_keep_event_times(self):
        times = sample_timestamps(10, 50, [11.1, 17.3, 42.2], 12)
        self.assertLessEqual(len(times), 12)
        self.assertEqual(sorted(times), times)
        self.assertTrue({10, 30, 49.95, 11.1, 17.3, 42.2}.issubset(set(times)))
        with self.assertRaises(ValueError):
            sample_timestamps(5, 5, [])

    def test_caption_groups_break_on_punctuation_pause_and_word_limit(self):
        caption_words = words(14)
        caption_words[2].text = "stop."
        result = group_words(caption_words, 0, 20, "bold_pop")
        self.assertTrue(all(len(group) <= 5 for group in result))
        stop_group = next(group for group in result if any(word.text == "stop." for word in group))
        self.assertEqual("stop.", stop_group[-1].text)
        for style in ("karaoke", "cinematic_minimal", "boxed"):
            self.assertTrue(group_words(caption_words, 0, 20, style))


if __name__ == "__main__":
    unittest.main()
