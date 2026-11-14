import json
from pathlib import Path
import tempfile
import unittest

from structure import apply_overrides, detect_zones, parse_subtitles, write_json, zone_check


class StructureTests(unittest.TestCase):
    def test_chapter_whole_words_and_ambiguous_intro(self):
        chapters = [dict(start_time=0, end_time=90, tags=dict(title=t)) for t in ("OP", "Opening", "Default", "Intro")]
        zones = detect_zones(chapters, [], 300)
        self.assertEqual([z["level"] for z in zones], ["HARD", "HARD"])

    def test_style_needs_independent_support_not_duration(self):
        cue = dict(start=10., end=110., style="OP", raw="Ordinary dialogue", karaoke=False)
        track = dict(index=3, cues=[cue])
        self.assertEqual(detect_zones([], [track], 300)[0]["level"], "SOFT")
        cue.update(raw=r"{\k20}Singing", karaoke=True)
        self.assertEqual(detect_zones([], [track], 300)[0]["level"], "VERIFIED")

    def test_partial_chapter_overlap_cannot_verify_long_style_run(self):
        chapter = dict(start_time=100, end_time=120, tags=dict(title="ED"))
        cue = dict(start=10., end=110., style="OP", raw="Ordinary dialogue", karaoke=False)
        zones = detect_zones([chapter], [dict(index=3, cues=[cue])], 300)
        self.assertEqual(next(z for z in zones if z["source"].startswith("subtitle"))["level"], "SOFT")
        chapter["end_time"] = 400
        self.assertFalse(any(z["level"] == "HARD" for z in detect_zones([chapter], [], 300)))

    def test_threshold_and_half_open_edges(self):
        zone = dict(id="z", start=20, end=50, level="HARD", label="OP", reason="chapter")
        self.assertFalse(zone_check(dict(start=0, end=100), [zone])[0])  # exactly 30%
        self.assertTrue(zone_check(dict(start=0, end=99), [zone])[0])
        self.assertFalse(zone_check(dict(start=50, end=70), [zone])[0])
        self.assertTrue(zone_check(dict(start=25, end=70), [zone])[0])
        zone["level"] = "SOFT"
        self.assertEqual(tuple(map(bool, zone_check(dict(start=25, end=70), [zone]))), (False, True))

    def test_cold_open_final_reveal_not_excluded(self):
        zones = detect_zones([], [], 1400)
        for start in (20, 1340):
            blocked, warnings = zone_check(dict(start=start, end=start+30), zones)
            self.assertFalse(blocked)
            self.assertTrue(warnings)

    def test_overrides_persist_and_preserve_detection(self):
        detected = detect_zones([dict(start_time=10, end_time=50, tags=dict(title="OP"))], [], 300)
        original = json.loads(json.dumps(detected))
        records = [dict(action="edit", id=detected[0]["id"], zone=dict(end=40)),
                   dict(action="add", id="manual-1", zone=dict(start=100, end=120, level="HARD", label="song"))]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"zones.json"
            write_json(path, records)
            active = apply_overrides(detected, json.loads(path.read_text()), 300)
            self.assertEqual(active[0]["end"], 40)
            self.assertEqual(len(active), 2)
            records = [dict(action="suppress", id=detected[0]["id"])]
            self.assertEqual(apply_overrides(detected, records, 300), [])
        self.assertEqual(detected, original)

    def test_invalid_overrides(self):
        for end in (float("nan"), 500, -1):
            with self.assertRaises(ValueError):
                apply_overrides([], [dict(action="add", id="m", zone=dict(start=0, end=end, level="HARD", label="OP"))], 300)

    def test_ass_and_srt(self):
        text = "[V4+ Styles]\nFormat: Name, Fontname\nStyle: OP,Arial\n[Events]\nFormat: Layer, Start, End, Style, Text\nDialogue: 0,0:00:01.23,0:00:04.56,OP,{\\k20}Hello, world\\Nagain"
        cues, styles = parse_subtitles(text)
        self.assertEqual(styles, ["OP"])
        self.assertEqual(cues[0]["start"], 1.23)
        self.assertEqual(cues[0]["text"], "Hello, world again")
        self.assertTrue(cues[0]["karaoke"])
        cues, _ = parse_subtitles("1\n00:00:01,230 --> 00:00:04,560\n<i>Hello</i>", False)
        self.assertEqual(cues[0]["text"], "Hello")
