import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nightly_queue import ActiveSessionError, QueueSchemaError, QueueStore
from queue_runner import NightlySessionRunner


class DummyManager:
    def __init__(self):
        self.releases = 0

    def release_whisper(self):
        self.releases += 1


class DummyVision:
    def __init__(self):
        self.releases = 0

    def release(self):
        self.releases += 1


class NightlyQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / "videos"
        self.source.mkdir()
        self.output = self.root / "output"
        self.db = self.root / "queue.sqlite3"
        self.store = QueueStore(self.db)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def videos(self, *names):
        result = []
        for name in names:
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(("content " + name).encode())
            result.append(path)
        return result

    def test_recursive_scan_natural_order_and_deduplicates_paths(self):
        files = self.videos("Episode 10.mkv", "Season 1/Episode 2.mkv", "Episode 1.mkv", "Episode 2.mkv")
        rows = self.store.scan(self.source, files + [files[0]])
        self.assertEqual(["Episode 1.mkv", "Episode 2.mkv", "Episode 10.mkv", "Episode 2.mkv"], [r["filename"] for r in rows])
        self.assertEqual(4, len(rows))

    def test_scan_persists_and_detects_source_change_without_auto_reprocess(self):
        path, = self.videos("Episode 1.mkv")
        row, = self.store.scan(self.source, [path])
        self.store.set_episode_status(row["id"], "COMPLETED", validated_shorts=1)
        self.store.close()
        self.store = QueueStore(self.db)
        self.assertEqual("COMPLETED", self.store.episodes()[0]["status"])
        path.write_bytes(b"changed source data")
        changed, = self.store.scan(self.source, [path])
        self.assertEqual("SOURCE_CHANGED", changed["status"])
        self.assertEqual(1, self.store.episodes()[0]["validated_shorts"])
        self.store.requeue_source_changed(row["id"])
        self.assertEqual("PENDING", self.store.episodes()[0]["status"])

    def test_rescan_adds_new_episode_without_resetting_completed_rows(self):
        first, = self.videos("Episode 1.mkv")
        row, = self.store.scan(self.source, [first])
        self.store.set_episode_status(row["id"], "COMPLETED", validated_shorts=1)
        second, = self.videos("Episode 2.mkv")
        rows = self.store.scan(self.source, [first, second])
        self.assertEqual(["COMPLETED", "PENDING"], [item["status"] for item in rows])

    def test_schema_version_guard_preserves_database(self):
        self.store.connection.execute("PRAGMA user_version=99")
        self.store.close()
        with self.assertRaises(QueueSchemaError):
            QueueStore(self.db)
        self.assertTrue(self.db.exists())
        self.store = QueueStore(self.root / "fresh.sqlite3")

    def test_active_session_is_exclusive_and_limits_are_checked(self):
        self.videos("Episode 1.mkv")
        self.store.create_session(self.source, target=5)
        with self.assertRaises(ActiveSessionError):
            self.store.create_session(self.source)
        with self.assertRaises(ValueError):
            self.store.create_session(self.source, target=11)

    def test_retry_state_transition(self):
        path, = self.videos("Episode 1.mkv")
        row, = self.store.scan(self.source, [path])
        self.store.set_episode_status(row["id"], "FAILED_RETRYABLE", error="temporary")
        self.store.retry_episode(row["id"])
        self.assertEqual("PENDING", self.store.episodes()[0]["status"])
        with self.assertRaises(ValueError):
            self.store.retry_episode(row["id"])

    def test_failed_clip_is_recorded_and_reset_to_ready_when_retried(self):
        path, = self.videos("Episode 1.mkv")
        episode, = self.store.scan(self.source, [path])
        candidate = {"start": 2, "end": 22, "score": .8, "selected": True}
        self.store.record_candidates(episode["id"], episode["fingerprint"], [candidate])
        self.store.mark_episode_render_failed(episode["id"], "encoder failed")
        clip, = self.store.connection.execute("SELECT * FROM clips").fetchall()
        self.assertEqual("FAILED", clip["render_state"])
        self.store.record_candidates(episode["id"], episode["fingerprint"], [candidate])
        clip, = self.store.connection.execute("SELECT * FROM clips").fetchall()
        self.assertEqual("READY", clip["render_state"])

    def test_session_counts_only_unique_validated_clips(self):
        path, = self.videos("Episode 1.mkv")
        episode, = self.store.scan(self.source, [path])
        session = self.store.create_session(self.source)
        candidate = {"start": 2, "end": 22, "score": .8, "selected": True}
        mp4, srt = self.output / "clip.mp4", self.output / "clip.srt"
        mp4.parent.mkdir(parents=True)
        mp4.touch(); srt.touch()
        self.assertTrue(self.store.record_validated_clip(session, episode["id"], episode["fingerprint"], candidate, mp4, srt))
        self.assertFalse(self.store.record_validated_clip(session, episode["id"], episode["fingerprint"], candidate, mp4, srt))
        self.assertEqual(1, self.store.session_clip_count(session))

    def test_interrupted_recovery_requires_explicit_include(self):
        path, = self.videos("Episode 1.mkv")
        episode, = self.store.scan(self.source, [path])
        self.store.set_episode_status(episode["id"], "INTERRUPTED")
        self.assertTrue(self.store.has_interrupted(self.source))
        self.assertEqual([], self.store.pending_episodes(self.source, 5, include_interrupted=False))
        self.assertEqual(1, len(self.store.pending_episodes(self.source, 5, include_interrupted=True)))

    def test_crash_recovery_marks_inflight_episode_and_session_interrupted(self):
        path, = self.videos("Episode 1.mkv")
        episode, = self.store.scan(self.source, [path])
        session = self.store.create_session(self.source)
        self.store.set_episode_status(episode["id"], "ANALYZING")
        self.store.close()
        with patch("nightly_queue.os.kill", side_effect=ProcessLookupError):
            self.store = QueueStore(self.db)
        self.assertEqual("INTERRUPTED", self.store.episodes()[0]["status"])
        self.assertEqual("INTERRUPTED", self.store.session(session)["status"])

    def test_reconcile_adopts_only_validator_approved_outputs(self):
        path, = self.videos("Episode 1.mkv")
        episode, = self.store.scan(self.source, [path])
        folder = self.output / "Episode_1_00000000"
        # Use the same output folder naming convention as the pipeline.
        import hashlib, re
        suffix = hashlib.sha1(str(path.resolve()).casefold().encode()).hexdigest()[:8]
        folder = self.output / f"Episode_1_{suffix}"
        folder.mkdir(parents=True)
        (folder / "clip_001.mp4").touch(); (folder / "clip_001.srt").touch()
        manifest = {"source_size": episode["source_size"], "source_mtime_ns": episode["source_mtime_ns"],
                    "metadata": {"has_audio": True}, "candidate_selection": {"selected_count": 1},
                    "clips": [{"filename": "clip_001.mp4", "duration": 20, "start": 2, "end": 22}]}
        (folder / "analysis.json").write_text(json.dumps(manifest), encoding="utf-8")
        result = self.store.reconcile(self.source, self.output, lambda *_args, **_kwargs: {"valid": True})
        self.assertEqual({"checked": 1, "adopted": 1, "invalid": 0}, result)
        self.assertEqual("COMPLETED", self.store.episodes()[0]["status"])

    def test_reconcile_keeps_partially_rendered_episode_ready(self):
        path, = self.videos("Episode 1.mkv")
        episode, = self.store.scan(self.source, [path])
        import hashlib
        suffix = hashlib.sha1(str(path.resolve()).casefold().encode()).hexdigest()[:8]
        folder = self.output / f"Episode_1_{suffix}"
        folder.mkdir(parents=True)
        (folder / "clip_001.mp4").touch(); (folder / "clip_001.srt").touch()
        manifest = {"queue_stage": "READY_TO_RENDER", "source_size": episode["source_size"],
                    "source_mtime_ns": episode["source_mtime_ns"], "metadata": {"has_audio": False},
                    "candidate_selection": {"selected_count": 3},
                    "clips": [{"filename": "clip_001.mp4", "duration": 20, "start": 2, "end": 22}]}
        (folder / "analysis.json").write_text(json.dumps(manifest), encoding="utf-8")
        self.store.reconcile(self.source, self.output, lambda *_args, **_kwargs: {})
        self.assertEqual("READY_TO_RENDER", self.store.episodes()[0]["status"])

    def test_reconcile_rejects_invalid_output_and_removes_only_known_temp_files(self):
        path, = self.videos("Episode 1.mkv")
        episode, = self.store.scan(self.source, [path])
        import hashlib
        suffix = hashlib.sha1(str(path.resolve()).casefold().encode()).hexdigest()[:8]
        folder = self.output / f"Episode_1_{suffix}"
        folder.mkdir(parents=True)
        temp = folder / "clip_001.partial.mp4"
        temp.touch()
        (folder / "clip_001.mp4").touch(); (folder / "clip_001.srt").touch()
        manifest = {"source_size": episode["source_size"], "source_mtime_ns": episode["source_mtime_ns"],
                    "metadata": {"has_audio": False}, "candidate_selection": {"selected_count": 1},
                    "clips": [{"filename": "clip_001.mp4", "duration": 20, "start": 2, "end": 22}]}
        (folder / "analysis.json").write_text(json.dumps(manifest), encoding="utf-8")
        result = self.store.reconcile(self.source, self.output, lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("invalid")))
        self.assertEqual(1, result["invalid"])
        self.assertFalse(temp.exists())
        self.assertEqual(0, self.store.connection.execute("SELECT COUNT(*) FROM clips").fetchone()[0])

    def _fake_processor(self, calls):
        def process(path, output, model, count, minimum, maximum, cancel, progress, encoder,
                    manager, semantic, vision, **kwargs):
            calls.append((path.name, kwargs.get("analysis_only", False), count, manager, vision,
                          kwargs.get("semantic_only", False)))
            import hashlib, re
            suffix = hashlib.sha1(str(path.resolve()).casefold().encode()).hexdigest()[:8]
            folder = output / f"{re.sub(r'[^A-Za-z0-9._-]+', '_', path.stem)}_{suffix}"
            folder.mkdir(parents=True, exist_ok=True)
            candidate = {"start": 2, "end": 22, "duration": 20, "score": .8, "selected": True,
                         "boundary_valid": True, "heuristic_score": .8}
            if kwargs.get("analysis_only"):
                manifest = {"queue_stage": "READY_TO_RENDER" if kwargs.get("semantic_only") else "ANALYZED",
                            "source_size": path.stat().st_size,
                            "source_mtime_ns": path.stat().st_mtime_ns,
                            "candidate_selection": {"selected_count": 1, "candidates": [candidate]}, "clips": [],
                            "semantic_analysis": {"status": "completed" if kwargs.get("semantic_only") else "disabled"}}
            else:
                mp4, srt = folder / "clip_001.mp4", folder / "clip_001.srt"
                mp4.touch(); srt.touch()
                details = {"encoder": "fake"}
                kwargs["clip_validated"](candidate, mp4, srt, details)
                manifest = {"queue_stage": "COMPLETED", "source_size": path.stat().st_size,
                            "source_mtime_ns": path.stat().st_mtime_ns,
                            "candidate_selection": {"selected_count": 1, "candidates": [candidate]},
                            "clips": [{**candidate, "filename": mp4.name, "validation": "VALIDATED"}]}
            (folder / "analysis.json").write_text(json.dumps(manifest), encoding="utf-8")
            return 0 if kwargs.get("analysis_only") else 1
        return process

    def test_runner_respects_session_target_and_retains_analyzed_backlog(self):
        files = self.videos("Episode 1.mkv", "Episode 2.mkv", "Episode 3.mkv")
        calls, manager = [], DummyManager()
        runner = NightlySessionRunner(self.store, self.output, "small", 5, 20, 55, "auto", False,
                                      target=1, batch_size=2, model_manager=manager,
                                      processor=self._fake_processor(calls), validator=lambda *_args, **_kwargs: {})
        result = runner.run(self.source)
        self.assertEqual(1, result["clips_prepared"])
        self.assertEqual(2, sum(1 for call in calls if call[1]))
        states = {row["filename"]: row["status"] for row in self.store.episodes()}
        self.assertEqual("COMPLETED", states["Episode 1.mkv"])
        self.assertEqual("ANALYZED", states["Episode 2.mkv"])
        self.assertEqual("PENDING", states["Episode 3.mkv"])
        self.assertGreaterEqual(manager.releases, 1)

    def test_runner_continues_backlog_on_next_explicit_session(self):
        self.videos("Episode 1.mkv", "Episode 2.mkv")
        calls = []
        run = lambda: NightlySessionRunner(self.store, self.output, "small", 5, 20, 55, "auto", False,
                                           target=1, batch_size=2, processor=self._fake_processor(calls),
                                           validator=lambda *_args, **_kwargs: {}).run(self.source)
        run()
        calls.clear()
        result = run()
        self.assertEqual(1, result["clips_prepared"])
        self.assertFalse(any(call[1] for call in calls), "analyzed episode should resume at render stage")
        self.assertEqual("COMPLETED", self.store.episodes()[1]["status"])

    def test_semantic_model_instance_is_reused_across_microbatch(self):
        self.videos("Episode 1.mkv", "Episode 2.mkv")
        calls, vision = [], DummyVision()
        runner = NightlySessionRunner(self.store, self.output, "small", 5, 20, 55, "auto", True,
                                      target=2, batch_size=2, vision_manager=vision,
                                      processor=self._fake_processor(calls), validator=lambda *_args, **_kwargs: {})
        runner.run(self.source)
        render_calls = [call for call in calls if not call[1]]
        semantic_calls = [call for call in calls if call[5]]
        self.assertEqual(2, len(semantic_calls))
        self.assertIs(semantic_calls[0][4], semantic_calls[1][4])
        self.assertTrue(all(calls.index(call) < calls.index(render_calls[0]) for call in semantic_calls))
        self.assertEqual(2, vision.releases)  # Once after the microbatch and once during final cleanup.

    def test_time_budget_checkpoint_stops_new_work(self):
        runner = NightlySessionRunner(self.store, self.output, "small", 5, 20, 55, "auto", False,
                                      max_seconds=60, processor=self._fake_processor([]), validator=lambda *_args, **_kwargs: {})
        import time
        runner.started = time.monotonic() - 61
        self.assertFalse(runner._checkpoint())

    def test_permanent_media_errors_are_not_automatically_retried(self):
        self.assertEqual("FAILED_FINAL", NightlySessionRunner._analysis_failure_status(ValueError("No video stream found")))
        self.assertEqual("FAILED_RETRYABLE", NightlySessionRunner._analysis_failure_status(RuntimeError("CUDA out of memory")))

    def test_zero_candidate_episode_does_not_block_later_episode(self):
        self.videos("Episode 1.mkv", "Episode 2.mkv")
        calls = []
        base = self._fake_processor(calls)
        def process(*args, **kwargs):
            result = base(*args, **kwargs)
            if kwargs.get("analysis_only") and args[0].name.casefold() == "episode 1.mkv":
                manifest = json.loads(next(self.output.glob("Episode_1_*/analysis.json")).read_text(encoding="utf-8"))
                manifest["candidate_selection"] = {"selected_count": 0, "candidates": []}
                next(self.output.glob("Episode_1_*/analysis.json")).write_text(json.dumps(manifest), encoding="utf-8")
            return result
        result = NightlySessionRunner(self.store, self.output, "small", 5, 20, 55, "auto", False,
                                      target=1, batch_size=1, processor=process,
                                      validator=lambda *_args, **_kwargs: {}).run(self.source)
        self.assertEqual(1, result["clips_prepared"])
        statuses = {row["filename"]: row["status"] for row in self.store.episodes()}
        self.assertEqual("SKIPPED", statuses["Episode 1.mkv"], (statuses, [(c[0], c[1]) for c in calls]))
        self.assertEqual("COMPLETED", statuses["Episode 2.mkv"])

    def test_runner_stops_between_episodes_without_losing_completed_outputs(self):
        files = self.videos("Episode 1.mkv", "Episode 2.mkv")
        calls = []
        runner = NightlySessionRunner(self.store, self.output, "small", 5, 20, 55, "auto", False,
                                      target=5, batch_size=1, processor=self._fake_processor(calls))
        runner.stop()
        result = runner.run(self.source)
        self.assertEqual(0, result["clips_prepared"])
        self.assertEqual("STOPPED", result["session"]["status"])
        self.assertEqual([], calls)


if __name__ == "__main__":
    unittest.main()
