import unittest
import os
import json
import tempfile
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from captions import group_words
from captions import choose_placement, shot_placements
from editorial import (QUALITY_FLOOR, WEIGHTS, apply_assessment, local_endpoint, transcript_windows,
                       validate_assessment)
from golden_set import SCHEMA, evaluate, interval_iou, validate_labels
from reframing import crop_bounds, plan_shot, shot_intervals, validate_crop
from storyboard import write_candidate_storyboard
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
        with patch.dict(os.environ, {"CLIPPER_CONTENT_TYPE": "Anime/Animation", "CLIPPER_HIGHLIGHT_PREFERENCE": "Comedy"}), \
                patch("editorial.urllib.request.build_opener") as opener:
            opener.return_value.open.return_value.__enter__.return_value = response
            from editorial import assess_candidate
            result = assess_candidate("http://localhost/v1/chat/completions", "local", words(), 1, 4, "ja")
            payload = opener.return_value.open.call_args.args[0].data.decode()
        self.assertEqual("ACTION", result["category"])
        self.assertIn("Language: ja", payload)
        self.assertIn("Anime/Animation", payload)
        self.assertIn("Comedy", payload)

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

    def test_shot_crop_geometry_and_portrait_obstructions(self):
        self.assertEqual([(0, 3), (3, 7), (7, 10)], shot_intervals(0, 10, [3, 7]))
        crop = crop_bounds(1920, 1080, 960)
        self.assertTrue(validate_crop(crop, 1920, 1080))
        self.assertEqual(0, crop["width"] % 2)
        portrait = plan_shot(0, 2, 720, 1280, [[(200, 100, 80, 100)]], [],
                             source_text_boxes=[(100, 1050, 300, 45)])
        self.assertEqual("full_frame_contain", portrait["composition"])
        self.assertEqual({"face", "source_text"}, {item["kind"] for item in portrait["obstructions"]})

    def test_caption_safe_area_prefers_unobstructed_zone_and_holds_per_shot(self):
        shots = [{"start": 0, "end": 4, "obstructions": [{"kind": "source_text", "box": [.1, .7, .9, .9]}]},
                 {"start": 4, "end": 8, "obstructions": [{"kind": "face", "box": [.1, .4, .9, .6]}]}]
        planned = shot_placements(shots)
        self.assertEqual("middle", planned[0]["anchor"])
        self.assertEqual("lower", planned[1]["anchor"])
        self.assertEqual("upper", choose_placement([], "upper")["anchor"])

    def test_golden_set_validation_and_metrics_are_bounded_by_human_labels(self):
        labels = [{"source_id": "episode-a", "start": i * 10, "end": i * 10 + 5, "keep": i < 3,
                   "category": "ACTION", "framing_acceptable": True, "captions_acceptable": True}
                  for i in range(5)]
        self.assertEqual(labels, validate_labels({"schema": SCHEMA, "labels": labels}))
        predictions = [{"source_id": "episode-a", "start": i * 10, "end": i * 10 + 5,
                       "selected": i in (0, 3), "editorial_category": "ACTION"} for i in range(5)]
        result = evaluate(labels, predictions)
        self.assertEqual("EVALUATED", result["status"])
        self.assertEqual(0.5, result["metrics"]["keep_precision"])
        self.assertAlmostEqual(1 / 3, result["metrics"]["keep_recall"])
        self.assertEqual(1.0, interval_iou(labels[0], predictions[0]))
        self.assertEqual("NOT ENOUGH DATA", evaluate(labels[:4], predictions)["status"])
        with self.assertRaises(ValueError):
            validate_labels({"schema": SCHEMA, "labels": [{**labels[0], "end": 0}]})

    def test_candidate_storyboard_is_capped_and_records_selection_reason(self):
        import tempfile
        import cv2
        import numpy as np
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.avi"
            writer = cv2.VideoWriter(str(source), cv2.VideoWriter_fourcc(*"MJPG"), 5, (64, 48))
            for index in range(20):
                writer.write(np.full((48, 64, 3), index * 10, dtype=np.uint8))
            writer.release()
            selection = {"candidates": [{"start": 0.2, "end": 3.5, "selected": True,
                                           "score": .9, "reason": "high action", "editorial_category": "ACTION"}]}
            result = write_candidate_storyboard(source, root / "storyboard", selection, [], [1, 2, 3])
            self.assertTrue(result and result.is_file())
            import json
            record = json.loads(result.read_text(encoding="utf-8"))["candidates"][0]
            self.assertLessEqual(len(record["frames"]), 5)
            self.assertEqual("high action", record["selection_reason"])

    def test_review_actions_target_selected_clip_artifacts(self):
        import main
        app = main.ClippingApp.__new__(main.ClippingApp)
        app.review_tree = SimpleNamespace(selection=lambda: ("clip-1",))
        app.review_items = {"clip-1": {"folder": Path("results/source"), "filename": "clip_001.mp4",
                                        "debug_image": "reframe_debug/clip_001.png"}}
        with patch.object(main.ClippingApp, "_open_local_file") as opener, \
                patch("main.subprocess.Popen") as popen:
            main.ClippingApp._play_review_clip(app)
            main.ClippingApp._open_review_srt(app)
            main.ClippingApp._open_review_analysis(app)
            main.ClippingApp._open_review_contact_sheet(app)
            main.ClippingApp._open_review_debug(app)
            main.ClippingApp._reveal_review_clip(app)
        opened = [call.args[0].as_posix() for call in opener.call_args_list]
        self.assertEqual(["results/source/clip_001.mp4", "results/source/clip_001.srt",
                          "results/source/analysis.json", "results/source/clip_contact_sheet.png",
                          "results/source/reframe_debug/clip_001.png"], opened)
        self.assertEqual(1, popen.call_count)

    def test_review_list_loads_only_safe_clip_filenames_and_thumbnail(self):
        import main
        class Tree:
            def __init__(self):
                self.rows = {}
                self.selection = None
            def get_children(self):
                return list(self.rows)
            def delete(self, item):
                self.rows.pop(item, None)
            def insert(self, _parent, _where, iid, values):
                self.rows[iid] = values
            def selection_set(self, items):
                self.selection = items[0]
        app = main.ClippingApp.__new__(main.ClippingApp)
        app.review_tree = Tree()
        app.review_items = {}
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "analysis.json").write_text(json.dumps({"clips": [
                {"filename": "clip_001.mp4", "start": 1, "end": 5, "duration": 4},
                {"filename": "..\\outside.mp4", "start": 5, "end": 8, "duration": 3}]}), encoding="utf-8")
            with patch.object(main.ClippingApp, "_review_selected"):
                main.ClippingApp._load_review_folder(app, folder)
        self.assertEqual(1, len(app.review_items))
        self.assertEqual("clip_001.mp4", app.review_tree.rows["clip-1"][0])

    def test_content_preferences_are_exported_to_pipeline_environment(self):
        import main
        app = main.ClippingApp.__new__(main.ClippingApp)
        for name, value in {"caption_style": "boxed", "caption_placement": "auto",
                            "content_type": "Anime/Animation", "highlight_preference": "Comedy",
                            "length_profile": "Story", "editorial_endpoint": "", "editorial_model": ""}.items():
            setattr(app, name, SimpleNamespace(get=lambda value=value: value))
        app._save_preferences = lambda: None
        with patch.dict(os.environ, {}, clear=False):
            main.ClippingApp._configure_local_options(app)
            self.assertEqual("Anime/Animation", os.environ["CLIPPER_CONTENT_TYPE"])
            self.assertEqual("Comedy", os.environ["CLIPPER_HIGHLIGHT_PREFERENCE"])
            self.assertEqual("Story", os.environ["CLIPPER_CLIP_LENGTH_PROFILE"])


if __name__ == "__main__":
    unittest.main()
