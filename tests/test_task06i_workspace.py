import json
import os
import subprocess
import sys
import tempfile
import unittest
import tkinter as tk
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from review_workspace import SCHEMA, SCHEMA_VERSION
from review_workspace.coverage import CoverageSample, CoverageTracker
from review_workspace.store import ReviewStore, source_fingerprint, validate_dataset
from review_workspace.window import format_timestamp, parse_timestamp
from review_workspace.player import LibVLCPlayer, probe_media


class TimestampTests(unittest.TestCase):
    def test_round_trip_keeps_milliseconds(self):
        for value in (0, 41, 59_999, 3_661_041, 1_422_590):
            self.assertEqual(parse_timestamp(format_timestamp(value)), value)

    def test_parse_seconds_and_reject_precision_beyond_milliseconds(self):
        self.assertEqual(parse_timestamp("12.345"), 12_345)
        self.assertEqual(parse_timestamp("01:02.003"), 62_003)
        with self.assertRaises(ValueError):
            parse_timestamp("00:00:01.0001")


class CoverageTests(unittest.TestCase):
    def test_counts_only_contiguous_expected_playback(self):
        tracker = CoverageTracker()
        tracker.seed(CoverageSample(0, 10.0, "playing"))
        self.assertEqual(tracker.sample(CoverageSample(250, 10.25, "playing")), (0, 250))
        self.assertEqual(tracker.sample(CoverageSample(500, 10.50, "playing")), (250, 500))
        self.assertEqual(tracker.intervals, [(0, 500)])

    def test_pause_and_buffering_create_no_coverage(self):
        tracker = CoverageTracker()
        tracker.seed(CoverageSample(0, 0.0, "playing"))
        tracker.sample(CoverageSample(250, 0.25, "playing"))
        self.assertIsNone(tracker.sample(CoverageSample(250, 1.0, "paused")))
        self.assertIsNone(tracker.sample(CoverageSample(500, 1.25, "playing")))
        self.assertIsNone(tracker.sample(CoverageSample(500, 1.5, "buffering")))
        self.assertIsNone(tracker.sample(CoverageSample(750, 1.75, "playing")))
        self.assertEqual(tracker.intervals, [(0, 250)])

    def test_seek_discontinuity_and_out_of_order_play_are_not_counted(self):
        tracker = CoverageTracker()
        tracker.seed(CoverageSample(0, 0.0, "playing"))
        tracker.sample(CoverageSample(250, 0.25, "playing"))
        tracker.break_continuity("seek")
        tracker.seed(CoverageSample(10_000, 1.0, "playing"))
        self.assertIsNone(tracker.sample(CoverageSample(10_250, 1.25, "playing")))
        self.assertEqual(tracker.intervals, [(0, 250)])
        self.assertEqual(tracker.break_reason, "out_of_order_interval")

    def test_large_sample_gap_is_left_unwatched(self):
        tracker = CoverageTracker()
        tracker.seed(CoverageSample(0, 0.0, "playing"))
        self.assertIsNone(tracker.sample(CoverageSample(1000, 1.0, "playing")))
        self.assertEqual(tracker.intervals, [])

    def test_gaps_and_completion_are_exact(self):
        tracker = CoverageTracker([(0, 100), (150, 250)])
        self.assertEqual(tracker.gaps(300), [(100, 150), (250, 300)])
        self.assertFalse(tracker.complete(300))
        tracker.add_interval(100, 150)
        tracker.add_interval(250, 300)
        self.assertTrue(tracker.complete(300))
        self.assertEqual(tracker.percent(300), 100.0)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source_file = self.root / "episode.mkv"
        self.source_file.write_bytes(b"local media fixture")
        self.source = {
            "path": str(self.source_file.resolve()), "filename": self.source_file.name,
            "fingerprint": source_fingerprint(self.source_file), "duration_ms": 100_000,
            "width": 1920, "height": 1080, "video_codec": "hevc", "frame_rate": "24000/1001",
            "streams": [{"index": 0, "type": "video", "language": None, "codec": "hevc", "title": None, "default": True}],
        }
        self.db = self.root / "human_review.sqlite3"

    def tearDown(self):
        self.temp.cleanup()

    def _moment(self):
        return {
            "moment_id": "moment_1", "kind": "interval", "rough_start_ms": 10_123,
            "rough_end_ms": 20_456, "refined_start_ms": 10_124, "refined_end_ms": 20_455,
            "category": "Fight/action", "quick_note": "", "quote": "A line.",
            "quote_start_ms": 12_345, "quote_end_ms": 13_001, "context_before": "Before",
            "context_after": "After", "event_description": "A human marked event",
            "setup": "Setup", "escalation": "Escalation", "payoff": "Payoff",
            "why_it_matters": "Human note", "missing_context": "", "decision": "keep",
            "rejection_reason": "", "ratings": {"plot_importance": 3, "entertainment_value": 5},
            "final_rank": 1, "created_at": 1.0, "updated_at": 2.0,
        }

    def test_source_fingerprint_tracks_contents_when_size_and_mtime_are_unchanged(self):
        before = source_fingerprint(self.source_file)
        original = self.source_file.stat()
        self.source_file.write_bytes(b"other media fixture")
        os.utime(self.source_file, ns=(original.st_atime_ns, original.st_mtime_ns))
        self.assertEqual(self.source_file.stat().st_size, original.st_size)
        self.assertEqual(self.source_file.stat().st_mtime_ns, original.st_mtime_ns)
        self.assertNotEqual(source_fingerprint(self.source_file), before)

    def test_autosave_resume_and_export_preserve_human_fields(self):
        store = ReviewStore(self.db, self.source, reviewer="reviewer")
        store.set("phase", "pass2")
        store.set("pass1_complete", True)
        store.set("player_runtime", "3.0.23")
        store.set("selected_audio_track", {"id": 2, "label": "English"})
        store.save_coverage([(0, 100_000)])
        store.save_moment(self._moment())
        store.close()

        store = ReviewStore(self.db, self.source)
        self.assertEqual(store.moments()[0]["rough_start_ms"], 10_123)
        self.assertEqual(store.moments()[0]["refined_start_ms"], 10_124)
        exported = self.root / "human_reference.json"
        store.export_json(exported)
        data = json.loads(exported.read_text(encoding="utf-8"))
        self.assertEqual(data["schema"], SCHEMA)
        self.assertEqual(data["schema_version"], SCHEMA_VERSION)
        self.assertEqual(data["player_runtime"], "3.0.23")
        self.assertEqual(data["selected_audio_track"]["label"], "English")
        self.assertEqual(data["moments"][0]["quote_start_ms"], 12_345)
        self.assertEqual(data["moments"][0]["ratings"]["entertainment_value"], 5)
        store.close()

    def test_source_mismatch_is_refused(self):
        store = ReviewStore(self.db, self.source)
        store.close()
        changed = dict(self.source, fingerprint="b" * 64)
        with self.assertRaisesRegex(ValueError, "does not match"):
            ReviewStore(self.db, changed)

    def test_point_marker_can_export_without_becoming_an_interval(self):
        store = ReviewStore(self.db, self.source)
        row = self._moment()
        row.update(kind="point", rough_start_ms=10_123, rough_end_ms=10_123,
                   refined_start_ms=None, refined_end_ms=None, final_rank=None)
        store.save_moment(row)
        data = store.dataset()
        self.assertEqual(data["point_markers"][0]["rough_start_ms"], 10_123)
        store.close()

    def test_duplicate_final_ranks_and_invalid_ratings_are_rejected(self):
        store = ReviewStore(self.db, self.source)
        first = self._moment()
        second = dict(first, moment_id="moment_2", rough_start_ms=30_000, rough_end_ms=40_000,
                      refined_start_ms=30_000, refined_end_ms=40_000)
        store.save_moment(first)
        store.save_moment(second)
        with self.assertRaisesRegex(ValueError, "ranking"):
            store.dataset()
        second["final_rank"] = None
        second["ratings"] = {"entertainment_value": 6}
        store.save_moment(second)
        with self.assertRaisesRegex(ValueError, "Ratings"):
            store.dataset()
        store.close()


class IsolationTests(unittest.TestCase):
    def test_review_import_is_lazy_and_has_no_legacy_ai_imports(self):
        script = (
            "import sys; import review_workspace.window; "
            "assert 'vlc' not in sys.modules; "
            "assert not any(name.startswith('evaluation.') for name in sys.modules); "
            "import main; assert 'vlc' not in sys.modules"
        )
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                                timeout=30, check=False, cwd=Path(__file__).resolve().parents[1])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_review_workspace_has_no_ai_recommendation_imports(self):
        source = "\n".join(path.read_text(encoding="utf-8") for path in Path("review_workspace").glob("*.py"))
        for forbidden in ("from evaluation", "import evaluation", "episode_brain", "story_graph", "candidate_selection"):
            self.assertNotIn(forbidden, source)

    def test_invalid_export_schema_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_dataset({"schema": "other"})

    def test_shortcuts_do_not_capture_text_entry_input(self):
        from review_workspace.window import ReviewWindow

        root = tk.Tk()
        self.addCleanup(root.destroy)
        entry = tk.Entry(root)
        window = ReviewWindow.__new__(ReviewWindow)
        window.store = SimpleNamespace(get=lambda _key: "pass1")
        window.mark_start = lambda: self.fail("Typing 'i' in an entry must not mark a moment")
        self.assertIsNone(window._shortcut(SimpleNamespace(widget=entry, keysym="i")))


class FakePlayer:
    def __init__(self):
        self.current_time = 12_345
        self.duration = 90_000
        self.current_state = "Paused"
        self.track = 2
        self.released = False
        self.selected_audio = None
        self.selected_subtitle = None

    def set_hwnd(self, _hwnd): pass
    def set_media(self, media): self.media = media
    def play(self): self.current_state = "Playing"; return 0
    def pause(self): self.current_state = "Paused"
    def set_pause(self, paused): self.current_state = "Paused" if paused else "Playing"
    def stop(self): self.current_state = "Stopped"
    def is_playing(self): return int(self.current_state == "Playing")
    def set_time(self, value): self.current_time = value; return 0
    def get_time(self): return self.current_time
    def get_length(self): return self.duration
    def get_state(self): return self.current_state
    def audio_get_track_description(self): return [(1, b"Japanese"), (2, b"Anime Clan - [English]")]
    def audio_set_track(self, value): self.selected_audio = value; return 0
    def video_get_spu_description(self): return [(3, b"English subtitles")]
    def video_set_spu(self, value): self.selected_subtitle = value; return 0
    def release(self): self.released = True


class FakeMedia:
    def release(self): self.released = True


class FakeInstance:
    def __init__(self):
        self.media_player = FakePlayer()
        self.released = False

    def media_player_new(self): return self.media_player
    def media_new_path(self, _path): return FakeMedia()
    def release(self): self.released = True


class PlayerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.source = Path(self.temp.name) / "episode.mkv"
        self.source.write_bytes(b"video placeholder")
        self.instance = FakeInstance()
        self.fake_vlc = SimpleNamespace(Instance=lambda *_args: self.instance)
        self.patch = patch("review_workspace.player.load_vlc_module", return_value=(self.fake_vlc, Path(self.temp.name), "fake-libvlc"))
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def tearDown(self):
        self.temp.cleanup()

    def test_player_lifecycle_and_millisecond_reads(self):
        self.fake_vlc.State = SimpleNamespace(Playing="Playing",
                                               Paused="Paused", Stopped="Stopped", Ended="Ended",
                                               Buffering="Buffering", Error="Error")
        player = LibVLCPlayer(123)
        player.open(self.source)
        self.assertEqual(player.time_ms(), 12_345)
        self.assertEqual(player.length_ms(), 90_000)
        self.assertEqual(player.state(), "stopped")
        self.assertEqual(player.play(), 0)
        self.assertEqual(player.state(), "playing")
        player.pause()
        self.assertEqual(player.state(), "paused")
        player.close()
        self.assertTrue(self.instance.media_player.released)
        self.assertTrue(self.instance.released)

    def test_audio_and_original_subtitle_track_ids_come_from_libvlc(self):
        self.fake_vlc.State = SimpleNamespace(Playing="Playing", Paused="Paused", Stopped="Stopped",
                                               Ended="Ended", Buffering="Buffering", Error="Error")
        player = LibVLCPlayer(123)
        player.open(self.source)
        self.assertEqual(player.audio_tracks(), [(1, "Japanese"), (2, "Anime Clan - [English]")])
        self.assertTrue(player.select_audio_track(2))
        self.assertEqual(self.instance.media_player.selected_audio, 2)
        self.assertEqual(player.subtitle_tracks(), [(3, "English subtitles")])
        self.assertTrue(player.select_subtitle_track(3))
        self.assertEqual(self.instance.media_player.selected_subtitle, 3)
        player.close()

    def test_invalid_seek_is_rejected(self):
        self.fake_vlc.State = SimpleNamespace(Playing="Playing", Paused="Paused", Stopped="Stopped",
                                               Ended="Ended", Buffering="Buffering", Error="Error")
        player = LibVLCPlayer(123)
        with self.assertRaises(ValueError):
            player.seek(-1)
        player.close()

    def test_invalid_libvlc_time_value_is_preserved(self):
        self.fake_vlc.State = SimpleNamespace(Playing="Playing", Paused="Paused", Stopped="Stopped",
                                               Ended="Ended", Buffering="Buffering", Error="Error")
        self.instance.media_player.current_time = -1
        player = LibVLCPlayer(123)
        self.assertEqual(player.time_ms(), -1)
        player.close()

    def test_libvlc_none_seek_result_is_valid(self):
        self.fake_vlc.State = SimpleNamespace(Playing="Playing", Paused="Paused", Stopped="Stopped",
                                               Ended="Ended", Buffering="Buffering", Error="Error")
        self.instance.media_player.set_time = lambda value: setattr(self.instance.media_player, "current_time", value)
        player = LibVLCPlayer(123)
        player.seek(30_000)
        self.assertEqual(player.time_ms(), 30_000)
        player.close()

    def test_ffprobe_metadata_helper_does_not_map_global_indexes_to_libvlc_ids(self):
        payload = {"format": {"duration": "23.5"}, "streams": [
            {"index": 0, "codec_type": "video", "codec_name": "hevc", "width": 1920, "height": 1080,
             "avg_frame_rate": "24000/1001", "disposition": {"default": 1}},
            {"index": 2, "codec_type": "audio", "codec_name": "aac", "tags": {"language": "eng"},
             "disposition": {"default": 1}},
        ]}
        completed = subprocess.CompletedProcess(["ffprobe"], 0, json.dumps(payload), "")
        with patch("review_workspace.player.subprocess.run", return_value=completed):
            data = probe_media(self.source, "ffprobe")
        self.assertEqual(data["duration_ms"], 23_500)
        self.assertEqual(data["streams"][1]["index"], 2)
        self.assertNotIn("libvlc_track_id", data["streams"][1])


if __name__ == "__main__":
    unittest.main()
