"""Bounded, resumable nightly processing built around the existing video pipeline."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Callable

from clipper import process_video, scan_videos, validate_output
from model_manager import ModelManager
from vision import VisionModelManager
from nightly_queue import QueueStore


class NightlySessionRunner:
    def __init__(self, store: QueueStore, output_root: Path, model_size: str, clip_count: int,
                 min_length: int, max_length: int, encoder: str, semantic_enabled: bool,
                 target: int = 5, max_seconds: int = 21600, batch_size: int = 2,
                 resume_interrupted: bool = False,
                 progress: Callable[[str], None] = lambda _message: None,
                 status: Callable[[dict], None] = lambda _summary: None,
                 model_manager=None, vision_manager=None, processor=process_video, validator=None,
                 transcription_enabled: bool = True):
        self.store, self.output_root = store, Path(output_root)
        self.settings = (model_size, clip_count, min_length, max_length, encoder, semantic_enabled)
        self.target, self.max_seconds, self.batch_size = target, max_seconds, batch_size
        self.resume_interrupted = resume_interrupted
        self.transcription_enabled = transcription_enabled
        self.progress, self.status, self.processor = progress, status, processor
        self.validator = validator or (lambda path, duration, has_audio, srt: validate_output(
            path, duration, has_audio, srt, deep_verify=False))
        self.manager = model_manager or ModelManager(model_size, local_files_only=True)
        self.vision = vision_manager or (VisionModelManager() if semantic_enabled else None)
        self.pause_event, self.stop_event = threading.Event(), threading.Event()
        self.started = 0.0
        self.session_id = None
        self._source_fingerprint = {}
        self._attempted_this_run = set()
        self.batch_timings = []
        self.discovery_seconds = self.reconciliation_seconds = 0.0

    @staticmethod
    def _analysis_failure_status(exc: Exception) -> str:
        message = str(exc).casefold()
        if isinstance(exc, (ValueError, json.JSONDecodeError)) or any(
                marker in message for marker in ("no video stream", "invalid data found", "moov atom not found",
                                                 "could not decode video", "decoded no frames")):
            return "FAILED_FINAL"
        return "FAILED_RETRYABLE"

    def pause(self):
        self.pause_event.set()

    def resume(self):
        self.pause_event.clear()

    def stop(self):
        self.stop_event.set()

    def _checkpoint(self):
        if self.stop_event.is_set():
            return False
        while self.pause_event.is_set() and not self.stop_event.is_set() and time.monotonic() - self.started < self.max_seconds:
            self._publish()
            time.sleep(1)
        return not self.stop_event.is_set() and time.monotonic() - self.started < self.max_seconds

    def _time_left(self):
        return max(0, self.max_seconds - int(time.monotonic() - self.started))

    def _publish(self):
        elapsed = time.monotonic() - self.started
        prepared = self.store.session_clip_count(self.session_id)
        self.store.update_session(self.session_id, "RUNNING", elapsed_seconds=elapsed, clips_prepared=prepared)
        self.status({"session_id": self.session_id,
                     "clips_prepared": prepared,
                     "target": self.target, "elapsed_seconds": int(elapsed),
                     "time_left_seconds": self._time_left(),
                     "queue_counts": self.store.counts(Path(self.store.session(self.session_id)["folder"]))})

    def run(self, folder: Path):
        self.session_id = self.store.create_session(folder, self.target, self.max_seconds,
                                                   self.batch_size, self.settings[5])
        self.started = time.monotonic()
        final_status = "COMPLETED"
        try:
            discovery_started = time.perf_counter()
            videos = scan_videos(folder, self.output_root)
            self.store.scan(folder, videos)
            self.discovery_seconds = time.perf_counter() - discovery_started
            reconciliation_started = time.perf_counter()
            self.store.reconcile(folder, self.output_root, self.validator)
            self.reconciliation_seconds = time.perf_counter() - reconciliation_started
            for interrupted in self.store.unreported_interrupted_sessions():
                paths = self.store.export_daily_summary(interrupted["id"], self.output_root, "INTERRUPTED",
                                                        interrupted["elapsed_seconds"])
                self.store.update_session(interrupted["id"], "INTERRUPTED",
                                          elapsed_seconds=interrupted["elapsed_seconds"],
                                          clips_prepared=self.store.session_clip_count(interrupted["id"]),
                                          summary_path=paths[0])
            while self._checkpoint() and self.store.session_clip_count(self.session_id) < self.target:
                batch_started = time.perf_counter()
                whisper_release_seconds = vision_release_seconds = 0.0
                ready = self.store.ready_episodes(folder, self.batch_size)
                pending = self.store.pending_episodes(folder, self.batch_size - len(ready), self.resume_interrupted,
                                                      self._attempted_this_run) if len(ready) < self.batch_size else []
                batch = ready + pending
                if not batch:
                    break
                fresh = [episode for episode in batch if episode["status"] in {"PENDING", "INTERRUPTED", "FAILED_RETRYABLE"}]
                if fresh:
                    self.progress(f"Pass A · analyzing {len(fresh)} episode(s)")
                    for episode in fresh:
                        self._attempted_this_run.add(episode["id"])
                        if not self._checkpoint():
                            break
                        path = Path(episode["path"])
                        self.store.set_episode_status(episode["id"], "ANALYZING", increment_attempt=True)
                        try:
                            self.processor(path, self.output_root, *self.settings[:4], threading.Event(), self.progress,
                                           self.settings[4], self.manager, False, None, analysis_only=True,
                                           transcription_enabled=self.transcription_enabled)
                            manifest = self._manifest(path)
                            self._source_fingerprint[episode["id"]] = episode["fingerprint"]
                            candidates = manifest.get("candidate_selection", {}).get("candidates", [])
                            self.store.record_candidates(episode["id"], episode["fingerprint"], candidates)
                            stage = manifest.get("queue_stage")
                            if stage in {"ANALYZED", "SKIPPED"}:
                                count = manifest.get("candidate_selection", {}).get("selected_count", 0)
                                self.store.set_episode_status(episode["id"], "ANALYZED" if count else "SKIPPED",
                                                              "analysis", validated_shorts=0)
                            else:
                                self.store.set_episode_status(episode["id"], "FAILED_RETRYABLE", error="Analysis did not persist")
                        except InterruptedError as exc:
                            self.store.set_episode_status(episode["id"], "INTERRUPTED", error=str(exc))
                            raise
                        except Exception as exc:
                            self.store.set_episode_status(episode["id"], self._analysis_failure_status(exc),
                                                          error=f"{type(exc).__name__}: {exc}")
                            self.progress(f"Analysis failed · {path.name}: {exc}")
                        self._publish()
                    release_started = time.perf_counter()
                    self.manager.release_whisper()
                    whisper_release_seconds = time.perf_counter() - release_started
                vision_pending = [episode for episode in self.store.episodes({"ANALYZED"})
                                  if any(item["id"] == episode["id"] for item in batch)]
                if self.settings[5] and vision_pending:
                    self.progress(f"Pass B · semantic analysis for {len(vision_pending)} episode(s)")
                    for episode in vision_pending:
                        if not self._checkpoint():
                            break
                        path = Path(episode["path"])
                        self.store.set_episode_status(episode["id"], "ANALYZING", "analysis")
                        try:
                            self.processor(path, self.output_root, *self.settings[:4], threading.Event(), self.progress,
                                           self.settings[4], self.manager, True, self.vision,
                                           analysis_only=True, release_vision_on_exit=False, semantic_only=True,
                                           transcription_enabled=self.transcription_enabled)
                            manifest = self._manifest(path)
                            candidates = manifest.get("candidate_selection", {}).get("candidates", [])
                            self.store.record_candidates(episode["id"], episode["fingerprint"], candidates)
                            if manifest.get("queue_stage") == "READY_TO_RENDER":
                                selected = manifest.get("candidate_selection", {}).get("selected_count", 0)
                                self.store.set_episode_status(episode["id"], "READY_TO_RENDER" if selected else "SKIPPED",
                                                              "semantic_selection", validated_shorts=0 if not selected else None)
                            else:
                                self.store.set_episode_status(episode["id"], "FAILED_RETRYABLE", error="Semantic analysis did not persist")
                        except InterruptedError as exc:
                            self.store.set_episode_status(episode["id"], "INTERRUPTED", error=str(exc))
                            raise
                        except Exception as exc:
                            self.store.set_episode_status(episode["id"], "FAILED_RETRYABLE", error=f"{type(exc).__name__}: {exc}")
                            self.progress(f"Semantic analysis failed · {path.name}: {exc}")
                        self._publish()
                    if self.vision:
                        release_started = time.perf_counter()
                        self.vision.release()
                        vision_release_seconds = time.perf_counter() - release_started
                current = {row["id"]: row for row in self.store.episodes({"ANALYZED", "READY_TO_RENDER"})}
                renderable = [current[e["id"]] for e in batch if e["id"] in current]
                for episode in renderable:
                    if not self._checkpoint() or self.store.session_clip_count(self.session_id) >= self.target:
                        break
                    path = Path(episode["path"])
                    self.store.set_episode_status(episode["id"], "RENDERING", "analysis")
                    fp = self._source_fingerprint.get(episode["id"], episode["fingerprint"])
                    def validated(clip, mp4, srt, _details):
                        self.store.record_validated_clip(self.session_id, episode["id"], fp, clip, mp4, srt)
                        self._publish()
                    try:
                        remaining = self.target - self.store.session_clip_count(self.session_id)
                        render_settings = (self.settings[0], min(self.settings[1], remaining), *self.settings[2:4])
                        manifest_before_render = self._manifest(path)
                        semantic_for_render = (self.settings[5] and
                            manifest_before_render.get("semantic_analysis", {}).get("status") == "completed")
                        self.processor(path, self.output_root, *render_settings, threading.Event(), self.progress,
                                       self.settings[4], self.manager, semantic_for_render, self.vision,
                                       release_vision_on_exit=False, clip_validated=validated,
                                       should_continue=lambda: self._checkpoint() and
                                       self.store.session_clip_count(self.session_id) < self.target,
                                       transcription_enabled=self.transcription_enabled)
                        manifest = self._manifest(path)
                        candidates = manifest.get("candidate_selection", {}).get("candidates", [])
                        self.store.record_candidates(episode["id"], fp, candidates)
                        stage = manifest.get("queue_stage")
                        clips = manifest.get("clips", [])
                        if stage == "COMPLETED":
                            self.store.set_episode_status(episode["id"], "COMPLETED", "validation",
                                                          validated_shorts=sum(1 for c in clips if c.get("validation") == "VALIDATED"))
                        elif stage == "READY_TO_RENDER":
                            self.store.set_episode_status(episode["id"], "READY_TO_RENDER", "analysis")
                        elif not candidates or not any(c.get("selected") for c in candidates):
                            self.store.set_episode_status(episode["id"], "SKIPPED", "selection", validated_shorts=0)
                        else:
                            self.store.set_episode_status(episode["id"], "FAILED_RETRYABLE", error="Render did not complete")
                    except InterruptedError as exc:
                        self.store.set_episode_status(episode["id"], "INTERRUPTED", error=str(exc))
                        raise
                    except Exception as exc:
                        error = f"{type(exc).__name__}: {exc}"
                        self.store.mark_episode_render_failed(episode["id"], error)
                        self.store.set_episode_status(episode["id"], "FAILED_RETRYABLE", error=error)
                        self.progress(f"Rendering failed · {path.name}: {exc}")
                    self._publish()
                if batch:
                    self.batch_timings.append({"episode_ids": [episode["id"] for episode in batch],
                                               "episode_count": len(batch),
                                               "elapsed_seconds": round(time.perf_counter() - batch_started, 3),
                                               "whisper_release_seconds": round(whisper_release_seconds, 3),
                                               "vision_release_seconds": round(vision_release_seconds, 3)})
                if not fresh and not renderable:
                    break
            if self.stop_event.is_set():
                final_status = "STOPPED"
            elif self._time_left() == 0:
                final_status = "TIME_LIMIT"
            elif self.pause_event.is_set():
                final_status = "PAUSED"
            self.store.update_session(self.session_id, final_status,
                                      elapsed_seconds=time.monotonic() - self.started,
                                      clips_prepared=self.store.session_clip_count(self.session_id))
            paths = self.store.export_daily_summary(self.session_id, self.output_root, final_status,
                                                    time.monotonic() - self.started, self.batch_timings,
                                                    self.discovery_seconds, self.reconciliation_seconds)
            self.store.update_session(self.session_id, final_status,
                                      elapsed_seconds=time.monotonic() - self.started,
                                      clips_prepared=self.store.session_clip_count(self.session_id), summary_path=paths[0])
            return self.store.summary(self.session_id)
        except Exception:
            self.store.update_session(self.session_id, "FAILED", elapsed_seconds=time.monotonic() - self.started,
                                      clips_prepared=self.store.session_clip_count(self.session_id))
            paths = self.store.export_daily_summary(self.session_id, self.output_root, "FAILED",
                                                    time.monotonic() - self.started, self.batch_timings,
                                                    self.discovery_seconds, self.reconciliation_seconds)
            self.store.update_session(self.session_id, "FAILED", summary_path=paths[0])
            raise
        finally:
            self.manager.release_whisper()
            if self.vision:
                self.vision.release()

    def _manifest(self, source: Path):
        suffix = __import__("hashlib").sha1(str(source.resolve()).casefold().encode()).hexdigest()[:8]
        import re
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", source.stem)
        return json.loads((self.output_root / f"{name}_{suffix}" / "analysis.json").read_text(encoding="utf-8"))
