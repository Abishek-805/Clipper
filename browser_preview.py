"""Loopback browser console for the Personal Preview single-episode workflow."""
from __future__ import annotations

import hashlib
import json
import mimetypes
import math
import os
import re
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from clipper import process_video, probe, rerender_single_clip
from main import ANALYSIS_CACHE, APP_STATE, OUTPUT, PREFERENCES, _read_json, _whisper_model_cached, _write_json
from model_manager import ModelManager
from diagnostics import gpu_activity
from source_import import import_source
from daily_workflow import manual_clip, restore_version, byte_range, export_delivery
from glossary import load_glossary, glossary_path
from structure import prepare_structure, apply_overrides, write_json as write_structure_json

_LOCK = threading.RLock()
_CANCEL: threading.Event | None = None
_STATE = {"state": "idle", "status": "Choose one local video file.", "logs": [], "job_folder": None,
          "report": None, "rendering": False, "rerender_result": None}
_MANIFEST_CACHE: dict[str, tuple[int, dict]] = {}
_GPU_SAMPLE = {"sampling": False, "time": 0.0, "value": {"available": False}}
_GPU_LOCK = threading.Lock()
_ALLOWED_CAPTIONS = {"Whisper transcription", "Whisper translation to English", "None"}
_STATUS_REPORT_FIELDS = ("schema", "job_id", "source", "source_path", "source_size", "source_mtime_ns",
    "source_fingerprint", "selected_audio_stream_index", "selected_audio_stream", "caption_mode",
    "visual_analysis", "transcription_executed", "transcription_device", "transcription_compute_type", "transcription_fallback_reason", "cache",
    "candidate_count", "selected_draft_count", "selection_status", "job_output_directory", "failure", "drafts")
_saved = _read_json(APP_STATE / "browser_preview_status.json")
if isinstance(_saved, dict) and _saved.get("state") != "running":
    _STATE.update(_saved)
elif isinstance(_saved, dict) and _saved.get("state") == "running":
    _STATE.update(_saved, state="interrupted", status="Previous browser job was interrupted; inspect its job folder before starting again.")

PAGE = (Path(__file__).parent / "web" / "index.html").read_text(encoding="utf-8-sig")


def _sample_gpu_activity():
    value = gpu_activity()
    with _GPU_LOCK:
        _GPU_SAMPLE.update(time=time.monotonic(), value=value, sampling=False)


def _publish(**changes):
    with _LOCK:
        _STATE.update(changes)
        snapshot = dict(_STATE)
    try:
        if isinstance(snapshot.get("report"), dict):
            snapshot["report"] = {key: snapshot["report"].get(key) for key in _STATUS_REPORT_FIELDS}
        _write_json(APP_STATE / "browser_preview_status.json", snapshot)
    except OSError:
        pass


def _probe_source(raw: str):
    source = Path(raw).expanduser().resolve(strict=True)
    if not source.is_file() or source.suffix.lower() not in {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v"}:
        raise ValueError("Choose a supported local video file.")
    info = probe(source)
    return source, info


def _job_path(job_id: str) -> Path:
    if not isinstance(job_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", job_id):
        raise ValueError("Invalid job identifier")
    path = (OUTPUT / "jobs" / job_id).resolve()
    if path.parent != (OUTPUT / "jobs").resolve():
        raise ValueError("Invalid job path")
    return path


def _open_job(job_id: str):
    folder = _job_path(job_id)
    report = json.loads((folder / "analysis.json").read_text(encoding="utf-8"))
    report["drafts"] = [{"filename": c["filename"], "srt": Path(c["filename"]).with_suffix(".srt").name,
        "start": c.get("start"), "end": c.get("end"), "edit_version": len(c.get("edit_history", [])),
        "technical_validation": c.get("validation", "PROVISIONAL"), "caption_position": c.get("caption_position"),
        "overlays": c.get("overlays", {}), "render_preset": (c.get("render") or {}).get("render_preset", "fast"),
        "glossary_replacements": (c.get("render") or {}).get("glossary_replacements", [])}
        for c in report.get("clips", []) if c.get("filename") and (folder / c["filename"]).is_file()]
    _publish(state="completed", report=report, job_id=job_id, job_folder=str(folder),
             rendering=False, rerender_result=None, status="Opened saved job")


def _run_job(source: Path, audio_index: int | None, caption_mode: str, job_id: str):
    global _CANCEL
    try:
        _execute_job(source, audio_index, caption_mode, job_id)
    except Exception as exc:
        _publish(state="failed", rendering=False, status=f"Could not run job: {exc}")
    finally:
        with _LOCK: _CANCEL = None


def _execute_job(source: Path, audio_index: int | None, caption_mode: str, job_id: str):
    global _CANCEL
    job_dir = OUTPUT / "jobs" / job_id
    cache_key = hashlib.sha256(str(source.resolve()).casefold().encode("utf-8")).hexdigest()[:20]
    cache_dir = ANALYSIS_CACHE / cache_key
    stat = source.stat()
    source_fp = hashlib.sha256(f"{source.resolve()}|{stat.st_size}|{stat.st_mtime_ns}".encode()).hexdigest()
    settings = _read_json(PREFERENCES) or {}
    os.environ["CLIPPER_RENDER_PRESET"] = "fast"
    model_size = "small"
    min_length = int(settings.get("minimum", 20))
    max_length = int(settings.get("maximum", 60))
    length_profile = settings.get("length_profile", "Auto")
    if length_profile == "Short": min_length, max_length = 12, 30
    elif length_profile == "Standard": min_length, max_length = 20, 60
    elif length_profile == "Story": min_length, max_length = 40, 90
    min_length, max_length = max(1, min_length), min(180, max_length)
    if max_length < min_length: max_length = min_length
    for key, default in (("caption_style", "bold_pop"), ("content_type", "Auto"),
                         ("highlight_preference", "Auto"), ("clip_length_profile", "Auto")):
        os.environ["CLIPPER_" + key.upper()] = str(settings.get(key, default))
    placement = settings.get("caption_placement", "auto")
    os.environ["CLIPPER_CAPTION_PLACEMENT"] = {"top": "upper", "bottom": "lower"}.get(placement, "auto")
    os.environ["CLIPPER_CONTEXT_MARGIN"] = str(settings.get("context_margin", 4.0))
    os.environ["CLIPPER_INCLUDE_RANGES"] = str(settings.get("include_ranges", ""))
    os.environ["CLIPPER_EXCLUDE_RANGES"] = str(settings.get("exclude_ranges", ""))
    audio_stream = next((s for s in (probe(source).streams or []) if s.get("type") == "audio" and s.get("index") == audio_index), None)
    cancel = threading.Event()
    with _LOCK: _CANCEL = cancel
    job_dir.mkdir(parents=True, exist_ok=False)
    started = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    _publish(state="running", status=f"Analyzing {source.name}", logs=[], job_id=job_id,
             job_folder=str(job_dir), report=None, model_status=None)
    manager = ModelManager(model_size, local_files_only=True,
                           status_callback=lambda info: _publish(model_status=info))
    outcome, failure, count = "failed", None, 0
    try:
        def progress(message):
            with _LOCK: logs = list(_STATE.get("logs", [])) + [str(message)]
            _publish(status=str(message), logs=logs[-100:])
        task = "translate" if caption_mode == "Whisper translation to English" else "transcribe"
        count = process_video(source, job_dir, model_size, 2, min_length, max_length, cancel,
            progress, str(settings.get("encoder", "auto")), manager, False, None,
            transcription_enabled=caption_mode.startswith("Whisper"), audio_index=audio_index,
            caption_source=caption_mode, transcription_task=task, cache_root=cache_dir,
            job_output_folder=job_dir, structure_filter_enabled=settings.get("structure_filter_enabled", True))
        outcome = "interrupted" if cancel.is_set() else "completed"
    except Exception as exc:
        outcome = "interrupted" if cancel.is_set() or isinstance(exc, InterruptedError) else "failed"
        failure = f"{type(exc).__name__}: {exc}"
    finally:
        try: manager.release_whisper()
        except Exception as exc: failure = (failure + "; " if failure else "") + f"Whisper cleanup: {exc}"
        manifest_path = job_dir / "analysis.json"
        try: analysis = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
        except (OSError, json.JSONDecodeError): analysis = {}
        clips = analysis.get("clips", []) if isinstance(analysis, dict) else []
        transcription_cache = _read_json(cache_dir / "transcription.json") or {}
        transcription_message = analysis.get("transcription_message")
        fallback_reason = (transcription_message if transcription_message != "transcription cache hit"
                           else transcription_cache.get("fallback_reason"))
        report = {**analysis, "schema": "clipper-personal-preview-job-v1", "job_id": job_id,
            "job_started_at": started, "job_finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "job_outcome": outcome, "source": source.name, "source_path": str(source),
            "source_size": stat.st_size, "source_mtime_ns": stat.st_mtime_ns,
            "source_fingerprint": source_fp, "source_fingerprint_method": "SHA-256(resolved path, size, modification time)",
            "selected_audio_stream_index": audio_index, "selected_audio_stream": audio_stream,
            "caption_mode": caption_mode,
            "transcription_task": "translate" if caption_mode == "Whisper translation to English" else "transcribe",
            "transcription_device": analysis.get("transcription_device", "not run"),
            "transcription_compute_type": analysis.get("transcription_precision", "not run"),
            "transcription_fallback_reason": fallback_reason if analysis.get("transcription_outcome") == "CPU_FALLBACK" else None,
            "cache": {"analysis_signals": analysis.get("analysis_cache_status", "not run"),
                      "transcription": analysis.get("transcription_cache_status", "not run"),
                      "editorial": analysis.get("editorial_analysis", {}).get("cache_hits", 0),
                      "semantic": analysis.get("semantic_analysis", {}).get("cache_hits", 0)},
            "candidate_count": analysis.get("candidate_selection", {}).get("candidate_count", 0),
            "selected_draft_count": len(clips), "selection_status": "PROVISIONAL",
            "job_output_directory": str(job_dir), "failure": failure,
            "drafts": [{"filename": c.get("filename"), "srt": Path(c.get("filename", "")).with_suffix(".srt").name,
                        "start": c.get("start"), "end": c.get("end"),
                        "technical_validation": c.get("validation", "not validated"),
                        "glossary_replacements": (c.get("render") or {}).get("glossary_replacements", []),
                        "mp4_exists": (job_dir / c.get("filename", "")).is_file(),
                        "srt_exists": (job_dir / Path(c.get("filename", "")).with_suffix(".srt").name).is_file()}
                       for c in clips]}
        _write_json(manifest_path, report)
        _publish(state=outcome, status=f"{outcome.title()} · {len(report['drafts'])} draft(s) · {failure or 'review local outputs'}",
                 report=report, job_id=job_id, job_folder=str(job_dir))
        with _LOCK: _CANCEL = None


def _run_rerender(job_id: str, clip_id: str, start: float, end: float, captions: str, position: str,
                  cancel: threading.Event, preset: str = "standard", overlays=None, allow_long=False):
    global _CANCEL
    _publish(rendering=True, rerender_result=None, status=f"Re-rendering {clip_id}")
    try:
        result = rerender_single_clip(OUTPUT / "jobs" / job_id, clip_id, start, end,
                                      captions, position, cancel=cancel, render_preset=preset, overlays=overlays, allow_long_duration=allow_long)
        with _LOCK: report = dict(_STATE.get("report") or {})
        for draft in report.get("drafts", []):
            if draft.get("filename") == clip_id:
                draft.update(start=start, end=end, edit_version=result["version"],
                             glossary_replacements=result.get("glossary_replacements", []))
        _open_job(job_id)
        _publish(report=_STATE["report"], rendering=False, rerender_result={**result, "clip_id": clip_id},
                 status=f"Re-rendered {clip_id} · {result['rerender_seconds']:.1f}s")
    except Exception as exc:
        _publish(rendering=False, rerender_result={"clip_id": clip_id, "error": f"{type(exc).__name__}: {exc}"},
                 status=f"Re-render failed · {type(exc).__name__}: {exc}")
    finally:
        with _LOCK: _CANCEL = None


class Handler(BaseHTTPRequestHandler):
    server_version = "ClipperLocal/1"

    def log_message(self, *_args): pass

    def _json(self, value, code=200):
        data = json.dumps(value).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            data = PAGE.encode(); self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data); return
        if parsed.path == "/api/status":
            with _LOCK: snapshot = dict(_STATE)
            if isinstance(snapshot.get("report"), dict):
                report = dict(snapshot["report"])
                job_id = snapshot.get("job_id")
                if isinstance(job_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,80}", job_id):
                    manifest_path = OUTPUT / "jobs" / job_id / "analysis.json"
                    try:
                        modified = manifest_path.stat().st_mtime_ns
                        cached = _MANIFEST_CACHE.get(job_id)
                        if cached and cached[0] == modified:
                            manifest = cached[1]
                        else:
                            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                            _MANIFEST_CACHE[job_id] = (modified, manifest)
                    except (OSError, json.JSONDecodeError): manifest = {}
                    by_name = {item.get("filename"): item for item in manifest.get("clips", []) if isinstance(item, dict)}
                    for draft in report.get("drafts", []):
                        clip = by_name.get(draft.get("filename"), {})
                        draft.setdefault("start", clip.get("start"))
                        draft.setdefault("end", clip.get("end"))
                        draft.setdefault("glossary_replacements", (clip.get("render") or {}).get("glossary_replacements", []))
                        draft.setdefault("edit_version", len(clip.get("edit_history", [])))
                snapshot["report"] = {key: report.get(key) for key in _STATUS_REPORT_FIELDS}
            with _GPU_LOCK:
                if time.monotonic() - _GPU_SAMPLE["time"] > 2 and not _GPU_SAMPLE["sampling"]:
                    _GPU_SAMPLE["sampling"] = True
                    try:
                        threading.Thread(target=_sample_gpu_activity, daemon=True).start()
                    except RuntimeError:
                        _GPU_SAMPLE["sampling"] = False
                snapshot["gpu_activity"] = {**_GPU_SAMPLE["value"], "sampling": _GPU_SAMPLE["sampling"]}
            return self._json(snapshot)
        if parsed.path == "/api/glossary":
            return self._json({"replacements": load_glossary()})
        if parsed.path == "/api/zones":
            try:
                query = parse_qs(parsed.query)
                job_id = query.get("job_id", [""])[0]
                if job_id:
                    value = json.loads((_job_path(job_id) / "structure.json").read_text(encoding="utf-8"))
                else:
                    source, _ = _probe_source(query.get("source", [""])[0])
                    key = hashlib.sha256(str(source.resolve()).casefold().encode()).hexdigest()[:20]
                    value = prepare_structure(source, ANALYSIS_CACHE / key)
                return self._json({**value, "filter_enabled": (_read_json(PREFERENCES) or {}).get("structure_filter_enabled", True)})
            except Exception as exc: return self._json({"error": str(exc)}, 400)
        if parsed.path == "/api/jobs":
            jobs = []
            for path in sorted((OUTPUT / "jobs").glob("*/analysis.json"), reverse=True):
                try:
                    value = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(value.get("clips"), list):
                        jobs.append({"id": path.parent.name, "source": value.get("source", path.parent.name)})
                except (OSError, ValueError):
                    continue
            return self._json(jobs)
        if parsed.path == "/api/probe":
            try:
                source, info = _probe_source(parse_qs(parsed.query).get("source", [""])[0])
                audio = [s for s in (info.streams or []) if s.get("type") == "audio"]
                video = next((s for s in (info.streams or []) if s.get("type") == "video"), {})
                return self._json({"name": source.name, "duration": info.duration, "width": info.width,
                    "height": info.height, "video_codec": video.get("codec"), "audio": audio})
            except Exception as exc: return self._json({"error": str(exc)}, 400)
        if parsed.path.startswith("/files/"):
            parts = [unquote(p) for p in parsed.path.removeprefix("/files/").split("/")]
            if len(parts) != 2: return self._json({"error": "Invalid artifact path"}, 404)
            root = (OUTPUT / "jobs" / parts[0]).resolve()
            target = (root / parts[1]).resolve()
            if root not in target.parents or not root.is_relative_to((OUTPUT / "jobs").resolve()) or not target.is_file():
                return self._json({"error": "Artifact not found"}, 404)
            size = target.stat().st_size
            try:
                start, end, status = byte_range(self.headers.get("Range"), size)
            except ValueError:
                self.send_response(416); self.send_header("Content-Range", f"bytes */{size}"); self.end_headers(); return
            self.send_response(status); self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream")
            self.send_header("Accept-Ranges", "bytes"); self.send_header("Content-Length", str(end - start + 1))
            if status == 206: self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            with target.open("rb") as f:
                f.seek(start); remaining = end - start + 1
                try:
                    while remaining:
                        chunk = f.read(min(1024 * 1024, remaining))
                        if not chunk: break
                        self.wfile.write(chunk); remaining -= len(chunk)
                except ConnectionError:
                    return
            return
        self._json({"error": "Not found"}, 404)

    def do_POST(self):
        global _CANCEL
        origin = self.headers.get("Origin")
        if origin and origin != f"http://{self.headers.get('Host', '')}":
            return self._json({"error": "Requests must originate from the local browser console."}, 403)
        if self.path == "/api/import":
            with _LOCK:
                if _STATE.get("state") == "running" or _STATE.get("rendering") or _STATE.get("importing"):
                    return self._json({"error": "An operation is already running"}, 409)
                _STATE["importing"] = True
            try:
                known = []
                for manifest in (OUTPUT / "jobs").glob("*/analysis.json"):
                    try:
                        value = json.loads(manifest.read_text(encoding="utf-8"))
                        if isinstance(value.get("source_path"), str):
                            known.append(value["source_path"])
                    except (OSError, ValueError):
                        continue
                result = import_source(self.rfile, int(self.headers.get("Content-Length", "0")),
                    unquote(self.headers.get("X-Filename", "")), OUTPUT / "Imports", known)
                return self._json(result)
            except (OSError, ValueError, RuntimeError) as exc:
                return self._json({"error": str(exc)}, 400)
            finally:
                with _LOCK: _STATE["importing"] = False
        if self.path == "/api/pick-file":
            # Drain the small retired request so Windows does not reset the response socket.
            self.rfile.read(min(1024, int(self.headers.get("Content-Length", "0"))))
            return self._json({"error": "Use the browser Choose file button or paste a local path"}, 410)
        if self.path in {"/api/manual", "/api/restore", "/api/glossary", "/api/export", "/api/zones"}:
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
                with _LOCK:
                    if _STATE.get("state") == "running" or _STATE.get("rendering") or _STATE.get("importing"):
                        return self._json({"error": "An operation is already running"}, 409)
                    if self.path == "/api/zones":
                        source, _ = _probe_source(body.get("source", ""))
                        key = hashlib.sha256(str(source.resolve()).casefold().encode()).hexdigest()[:20]
                        value = prepare_structure(source, ANALYSIS_CACHE / key)
                        overrides = body.get("overrides")
                        if not isinstance(overrides, list) or len(overrides) > 500:
                            raise ValueError("Expected at most 500 overrides")
                        value["active"] = apply_overrides(value["detected"], overrides, value["duration"])
                        enabled = body.get("filter_enabled", True)
                        if type(enabled) is not bool:
                            raise ValueError("filter_enabled must be a boolean")
                        write_structure_json(value["override_file"], overrides)
                        preferences = _read_json(PREFERENCES) or {}
                        preferences["structure_filter_enabled"] = enabled
                        _write_json(PREFERENCES, preferences)
                        if body.get("job_id"):
                            folder = _job_path(body["job_id"])
                            saved = json.loads((folder / "analysis.json").read_text(encoding="utf-8"))
                            if Path(saved["source_path"]).resolve() != source:
                                raise ValueError("Job source differs from selected episode")
                            value["overrides"] = overrides
                            write_structure_json(folder / "structure-overrides-pending.json", value)
                        return self._json({"ok": True, "active": value["active"]})
                    if self.path == "/api/glossary":
                        value = body.get("replacements")
                        if not isinstance(value, list) or len(value) > 500:
                            raise ValueError("Expected at most 500 replacements")
                        # Validate before replacing the user-owned glossary.
                        import tempfile
                        with tempfile.TemporaryDirectory() as tmp:
                            test = Path(tmp) / "glossary.json"
                            _write_json(test, {"replacements": value})
                            load_glossary(test)
                        _write_json(glossary_path(), {"replacements": value})
                        return self._json({"ok": True})
                    if self.path == "/api/export":
                        return self._json(export_delivery(_job_path(body.get("job_id")), body.get("clip_id"), OUTPUT))
                    if self.path == "/api/restore":
                        folder = _job_path(body.get("job_id"))
                        result = restore_version(folder, body.get("clip_id"), body.get("version"))
                        _open_job(folder.name)
                        return self._json(result)
                    source, info = _probe_source(body.get("source", ""))
                    audio = body.get("audio_index")
                    if info.has_audio and audio not in {x["index"] for x in info.streams if x.get("type") == "audio"}:
                        raise ValueError("Select the source audio track first")
                    from daily_workflow import interval
                    left, right = interval(body.get("start"), body.get("end"), info.duration)
                    mode = body.get("caption_mode", "Whisper transcription")
                    if mode not in _ALLOWED_CAPTIONS:
                        raise ValueError("Invalid caption mode")
                    job_id = "manual-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
                    cancel = threading.Event()
                    _STATE.update(state="running", rendering=False, status="Creating manual interval")
                    _CANCEL = cancel
                    def worker():
                        global _CANCEL
                        try:
                            manual_clip(source, _job_path(job_id), ANALYSIS_CACHE, audio, left, right, mode,
                                body.get("allow_transcription") is True, cancel, body.get("render_preset", "fast"), body.get("overlays"))
                            _open_job(job_id)
                        except Exception as exc:
                            _publish(state="interrupted" if cancel.is_set() else "failed", status=str(exc))
                        finally:
                            with _LOCK: _CANCEL = None
                    thread = threading.Thread(target=worker, daemon=True)
                    try:
                        thread.start()
                    except RuntimeError:
                        _STATE.update(state="failed"); _CANCEL = None
                        raise
                    return self._json({"job_id": job_id}, 202)
            except (OSError, ValueError, TypeError, RuntimeError) as exc:
                return self._json({"error": str(exc)}, 400)
        if self.path == "/api/open-job":
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
                with _LOCK:
                    if _STATE.get("state") == "running" or _STATE.get("rendering") or _STATE.get("importing"):
                        return self._json({"error": "An operation is already running"}, 409)
                    _open_job(body.get("job_id"))
                return self._json({"ok": True})
            except (OSError, ValueError, TypeError) as exc:
                return self._json({"error": str(exc)}, 400)
        if self.path == "/api/cancel":
            with _LOCK: cancel = _CANCEL
            if cancel: cancel.set(); return self._json({"ok": True})
            return self._json({"error": "No active job"}, 409)
        if self.path not in {"/api/start", "/api/rerender"}: return self._json({"error": "Not found"}, 404)
        try: body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        except (ValueError, json.JSONDecodeError): return self._json({"error": "Invalid JSON"}, 400)
        with _LOCK:
            if _STATE.get("state") == "running" or _STATE.get("rendering") or _STATE.get("importing"):
                return self._json({"error": "A job or clip render is already running"}, 409)
        if self.path == "/api/rerender":
            try:
                job_id, clip_id = body.get("job_id"), body.get("clip_id")
                start, end = float(body.get("start")), float(body.get("end"))
                position, captions = body.get("caption_position"), body.get("captions", "")
                overlays = body.get("overlays")
                if overlays is not None and not isinstance(overlays, dict):
                    raise ValueError("Invalid hook/context settings")
                preset = body.get("render_preset", "standard")
                if preset not in {"standard", "fast"}:
                    raise ValueError("Choose a valid render preset")
                if not isinstance(job_id, str) or job_id != _STATE.get("job_id") or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", job_id):
                    raise ValueError("Choose a clip from the current local job")
                if not isinstance(clip_id, str) or Path(clip_id).name != clip_id or not clip_id.endswith(".mp4"):
                    raise ValueError("Invalid clip filename")
                if captions is not None and (not isinstance(captions, str) or len(captions) > 1_000_000):
                    raise ValueError("Caption text is invalid or too large")
                if position not in {"bottom-safe", "center-lower"}:
                    raise ValueError("Choose a supported caption position")
                if not math.isfinite(start) or not math.isfinite(end):
                    raise ValueError("Clip times must be finite numbers")
                job_dir = (OUTPUT / "jobs" / job_id).resolve()
                if job_dir.parent != (OUTPUT / "jobs").resolve() or not (job_dir / "analysis.json").is_file():
                    raise ValueError("The local job folder is unavailable")
            except (TypeError, ValueError, OSError) as exc:
                return self._json({"error": str(exc)}, 400)
            cancel = threading.Event()
            with _LOCK:
                if _STATE.get("state") == "running" or _STATE.get("rendering") or _STATE.get("importing"):
                    return self._json({"error": "An operation is already running"}, 409)
                _write_json(job_dir / "pending-edits.json", body)
                _CANCEL = cancel
                _STATE.update(rendering=True, rerender_result=None, status=f"Re-rendering {clip_id}")
            thread = threading.Thread(target=_run_rerender,
                args=(job_id, clip_id, start, end, captions, position, cancel, preset, overlays, body.get("allow_long_duration") is True), daemon=True)
            try:
                thread.start()
            except RuntimeError as exc:
                with _LOCK: _CANCEL = None; _STATE.update(rendering=False, status=f"Could not start clip render: {exc}")
                return self._json({"error": str(exc)}, 500)
            return self._json({"job_id": job_id, "clip_id": clip_id, "state": "rendering"}, 202)
        try:
            source, info = _probe_source(body.get("source", ""))
            audio_index = body.get("audio_index")
            audio = [s for s in (info.streams or []) if s.get("type") == "audio"]
            if audio and (not isinstance(audio_index, int) or audio_index not in {s["index"] for s in audio}):
                raise ValueError("Select a valid audio track from the inspected source.")
            caption_mode = body.get("caption_mode")
            if caption_mode not in _ALLOWED_CAPTIONS: raise ValueError("Unsupported caption mode.")
            if caption_mode.startswith("Whisper") and not _whisper_model_cached("small"):
                raise ValueError("The local Whisper small model is not cached; no download was attempted.")
            job_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
        except Exception as exc: return self._json({"error": str(exc)}, 400)
        thread = threading.Thread(target=_run_job, args=(source, audio_index, caption_mode, job_id), daemon=True)
        with _LOCK:
            if _STATE.get("state") == "running" or _STATE.get("rendering") or _STATE.get("importing"):
                return self._json({"error": "An operation is already running"}, 409)
            _STATE.update(state="running", status="Starting job")
            _CANCEL = threading.Event()
        try:
            thread.start()
        except RuntimeError as exc:
            with _LOCK:
                _CANCEL = None
                _STATE.update(state="failed", status=str(exc))
            return self._json({"error": str(exc)}, 500)
        return self._json({"job_id": job_id, "state": "running"}, 202)


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True


def serve(host="127.0.0.1", port=8765):
    if host != "127.0.0.1": raise ValueError("The browser preview server must bind to loopback only.")
    server = LocalServer((host, port), Handler)
    print(f"Clipper browser console: http://{host}:{server.server_address[1]}/", flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args()
    if args.open_browser:
        import webbrowser
        threading.Timer(1, lambda: webbrowser.open(f"http://127.0.0.1:{args.port}/")).start()
    serve(args.host, args.port)
