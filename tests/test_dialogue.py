import json
from pathlib import Path
import tempfile
import unittest
import threading
from unittest.mock import patch

from clipper import Word
from dialogue import assess_tracks, build_units, choose_dialogue, clipped_cues, release_cues, save_units


def fixture(language="eng"):
    cues = [dict(start=i*4., end=i*4.+3., style="Default", text=f"This is spoken line {i}.", karaoke=False)
            for i in range(25)]
    return dict(duration=100, active=[], source_hash="a"*64,
                tracks=[dict(index=3, language=language, title="Full", cues=cues)])


class DialogueTests(unittest.TestCase):
    def test_unique_dialogue_source_and_flags(self):
        data = fixture()
        self.assertEqual(choose_dialogue(data, [])["stream_index"], 3)
        data["tracks"][0]["forced"] = True
        self.assertEqual(assess_tracks(data)[0]["classification"], "forced")
        data["tracks"][0].update(forced=False, title="Director commentary")
        self.assertEqual(assess_tracks(data)[0]["classification"], "commentary")

    def test_conflicting_language_and_ambiguous_tracks_fall_back(self):
        data = fixture("jpn")
        words = [Word(1.123, 2.345, "Hi."), Word(4, 5, "Bye.")]
        result = choose_dialogue(data, words)
        self.assertEqual(result["source"], "cached Whisper words")
        self.assertEqual(result["cues"][0]["start"], 1.123)
        with self.assertRaisesRegex(ValueError, "Whisper"):
            release_cues(data)
        data = fixture()
        data["tracks"].append({**data["tracks"][0], "index": 5})
        self.assertIsNone(choose_dialogue(data, words)["stream_index"])

    def test_filter_styles_and_karaoke_but_preserve_yelled_dialogue(self):
        from dialogue import dialogue_cues
        base = dict(start=1., end=3., text="LOOK OUT!", karaoke=False)
        track = dict(cues=[dict(base, style=s) for s in ("Signs", "Opening-Romaji", "Title", "Prev2", "Default")])
        self.assertEqual(dialogue_cues(track, []), [{k: base[k] for k in ("start", "end", "text")}])
        track["cues"] = [dict(base, style="Default", karaoke=True)]
        self.assertEqual(dialogue_cues(track, []), [])

    def test_all_caps_sparse_track_not_assumed_dialogue(self):
        data = fixture()
        for cue in data["tracks"][0]["cues"]:
            cue["text"] = "MENU SCREEN"
        self.assertEqual(assess_tracks(data)[0]["classification"], "unknown")

    def test_clipping_preserves_cue_timing(self):
        self.assertEqual(clipped_cues([dict(start=1., end=4., text="Phrase")], 2., 3.),
                         [dict(start=2., end=3., text="Phrase")])

    def test_units_deterministic_and_zone_respecting(self):
        dialogue = dict(source="cached Whisper words", stream_index=None, reason="fallback",
                        cues=[dict(start=1., end=3., text="First."), dict(start=10., end=12., text="Second.")])
        zones = [dict(start=14., end=18.)]
        args = (20, [2, 6, 11, 16], dialogue, zones, "a"*64, 2)
        one = build_units(*args)
        self.assertEqual(one, build_units(*args))
        self.assertEqual([u["id"] for u in one["utterances"]], ["U_0001", "U_0002"])
        edges = [s["start"] for s in one["scenes"]]
        self.assertTrue(all(t in edges for t in (6, 14, 18)))
        with tempfile.TemporaryDirectory() as tmp:
            path = save_units(tmp, one)
            self.assertEqual(path, save_units(tmp, one))
            other = build_units(*args[:-1], 1)
            self.assertNotEqual(path, save_units(tmp, other))
            self.assertEqual(json.loads(path.read_text()), one)

    def test_release_caption_render_and_trim_only_use_saved_cues(self):
        import clipper
        from daily_workflow import manual_clip
        from tests.test_rerender import SingleClipRerenderTests
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, _, _ = SingleClipRerenderTests().make_job(root)
            data = fixture()
            with patch.object(clipper, "prepare_structure", return_value=data), \
                 patch.object(clipper, "transcribe", side_effect=AssertionError("Whisper must not run")):
                folder = root / "release-job"
                manual_clip(source, folder, root / "cache", 1, 0, 1.5, "Release subtitles", False, threading.Event())
                self.assertIn("This is spoken line 0.", (folder / "clip_001.srt").read_text())
                clipper.rerender_single_clip(folder, "clip_001.mp4", .1, 1.6, None, "bottom-safe", render_preset="fast")
                self.assertIn("This is spoken line 0.", (folder / "clip_001.srt").read_text())
                self.assertFalse((root / "cache" / "transcription.json").exists())
