"""Loopback browser console for the Personal Preview single-episode workflow."""
from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from clipper import process_video, probe
from main import ANALYSIS_CACHE, APP_STATE, OUTPUT, PREFERENCES, _read_json, _whisper_model_cached, _write_json
from model_manager import ModelManager

_LOCK = threading.RLock()
_CANCEL: threading.Event | None = None
_STATE = {"state": "idle", "status": "Choose one local video file.", "logs": [], "job_folder": None, "report": None}
_ALLOWED_CAPTIONS = {"Whisper transcription", "Whisper translation to English", "None"}
_STATUS_REPORT_FIELDS = ("schema", "job_id", "source", "source_path", "source_size", "source_mtime_ns",
    "source_fingerprint", "selected_audio_stream_index", "selected_audio_stream", "caption_mode",
    "transcription_device", "transcription_compute_type", "transcription_fallback_reason", "cache",
    "candidate_count", "selected_draft_count", "selection_status", "job_output_directory", "failure", "drafts")
_saved = _read_json(APP_STATE / "browser_preview_status.json")
if isinstance(_saved, dict) and _saved.get("state") != "running":
    _STATE.update(_saved)
elif isinstance(_saved, dict) and _saved.get("state") == "running":
    _STATE.update(_saved, state="interrupted", status="Previous browser job was interrupted; inspect its job folder before starting again.")

PAGE = r"""<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Clipper · Local Preview</title><style>
:root{color-scheme:dark;--bg:#10141d;--panel:#191f2b;--line:#30394a;--text:#edf2fa;--muted:#aab6c9;--accent:#47d7b0}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 Segoe UI,system-ui,sans-serif}
main{max-width:980px;margin:32px auto;padding:0 20px}h1{font-size:28px;margin:0}p{color:var(--muted)}section{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:20px;margin:18px 0}
label{display:block;color:var(--muted);font-size:13px;margin:12px 0 5px}input,select,button{font:inherit;border-radius:8px;padding:10px 12px}input,select{width:100%;color:var(--text);background:#0d1118;border:1px solid var(--line)}button{border:0;background:var(--accent);color:#07150f;font-weight:700;cursor:pointer;margin:12px 8px 0 0}button.secondary{background:#333d50;color:var(--text)}button:disabled{opacity:.5;cursor:not-allowed}.row{display:grid;grid-template-columns:1fr 1fr;gap:18px}.status{font-size:16px}.log{font:12px/1.5 Consolas,monospace;white-space:pre-wrap;max-height:180px;overflow:auto;color:#bdc9dc}video{width:min(100%,440px);aspect-ratio:9/16;background:#080b10;border-radius:10px}a{color:var(--accent);margin-right:12px}.badge{color:var(--accent)}@media(max-width:650px){.row{grid-template-columns:1fr}main{margin:16px auto}}
</style><main><h1>Clipper · Personal Preview 0.1</h1><p>Local single-episode draft run · no upload · outputs remain on this PC</p>
<section><strong>1 · Choose one episode</strong><label for="source">Full path to a local video</label><input id="source" placeholder="C:\\Videos\\episode.mkv">
<button class="secondary" id="inspect">Inspect tracks</button><div class="row"><div><label for="audio">Audio track</label><select id="audio"><option>Inspect a video first</option></select></div><div><label for="captions">Captions</label><select id="captions"><option>Whisper transcription</option><option>Whisper translation to English</option><option>None</option></select></div></div>
<button id="start" disabled>Generate up to two provisional drafts</button><button id="cancel" class="secondary" disabled>Cancel safely</button></section>
<section><strong>2 · Processing</strong><p id="status" class="status">Choose one local video file.</p><p id="meta" class="badge"></p><p id="model" class="badge"></p><pre id="logs" class="log"></pre></section>
<section><strong>3 · Draft results</strong><p id="result">No job run yet.</p><p>Provisional draft — rights not verified. Exports remain local.</p><div id="artifacts"></div></section></main>
<script>
const $=id=>document.getElementById(id), qs=new URLSearchParams(location.search);if(qs.has('source'))$('source').value=qs.get('source');
async function inspect(){let source=$('source').value.trim();if(!source)return; $('inspect').disabled=true;try{let r=await fetch('/api/probe?source='+encodeURIComponent(source)),d=await r.json();if(!r.ok)throw Error(d.error||'Probe failed');$('audio').innerHTML='';for(const t of d.audio){let o=document.createElement('option');o.value=t.index;o.textContent=`${t.language||'und'} · stream #${t.index} · ${t.title||t.codec}${t.default?' · default':''}`;$('audio').append(o);if((t.language||'').toLowerCase().startsWith('en'))o.selected=true}if(!d.audio.length){let o=document.createElement('option');o.value='';o.textContent='No audio';$('audio').append(o)}$('meta').textContent=`${d.name} · ${d.duration.toFixed(1)} s · ${d.width}×${d.height} · ${d.video_codec||'video'}`;$('start').disabled=false}catch(e){$('meta').textContent=e.message;$('start').disabled=true}finally{$('inspect').disabled=false}}
$('inspect').onclick=inspect;$('start').onclick=async()=>{try{let r=await fetch('/api/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({source:$('source').value.trim(),audio_index:$('audio').value===''?null:Number($('audio').value),caption_mode:$('captions').value})}),d=await r.json();if(!r.ok)throw Error(d.error||'Could not start');$('result').textContent=`Job ${d.job_id} started.`}catch(e){$('status').textContent=e.message}};$('cancel').onclick=()=>fetch('/api/cancel',{method:'POST'});
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function poll(){try{let d=await(await fetch('/api/status')).json();if(!$('source').value&&d.report?.source_path){$('source').value=d.report.source_path;inspect()}$('status').textContent=d.status;$('logs').textContent=(d.logs||[]).slice(-30).join('\n');let m=d.model_status;$('model').textContent=m?`Whisper ${m.model}: ${m.device}/${m.compute_type}${m.fallback_reason?' · '+m.fallback_reason:''}`:'';$('cancel').disabled=d.state!=='running';$('start').disabled=d.state==='running'||!$('meta').textContent;$('result').textContent=d.job_folder?`${d.state.toUpperCase()} · ${d.job_folder}`:'No job run yet.';if(d.report){let e=d.report.drafts||[];$('artifacts').innerHTML=e.map((x,i)=>`<div><p>${esc(x.filename)} · ${esc(x.technical_validation)}</p><p>Provisional draft — rights not verified.</p><video id="clip-${i}" controls preload="metadata" src="/files/${encodeURIComponent(d.job_id)}/${encodeURIComponent(x.filename)}"></video><p><button class="secondary" onclick="const v=document.getElementById('clip-${i}');v.paused?v.play():v.pause()">Play / pause</button> <a href="/files/${encodeURIComponent(d.job_id)}/${encodeURIComponent(x.srt)}">Open SRT</a> <a href="/files/${encodeURIComponent(d.job_id)}/analysis.json">analysis.json</a></p><p>Applied glossary replacements: ${esc((x.glossary_replacements||[]).map(r=>`${r.original_text} → ${r.replacement} (cue ${r.cue_number})`).join('; ')||'none')}</p></div>`).join('')||'<p>No validated drafts in this job.</p>'}}catch{}setTimeout(poll,1800)}poll();
</script>"""


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


def _run_job(source: Path, audio_index: int | None, caption_mode: str, job_id: str):
    global _CANCEL
    job_dir = OUTPUT / "jobs" / job_id
    cache_key = hashlib.sha256(str(source.resolve()).casefold().encode("utf-8")).hexdigest()[:20]
    cache_dir = ANALYSIS_CACHE / cache_key
    stat = source.stat()
    source_fp = hashlib.sha256(f"{source.resolve()}|{stat.st_size}|{stat.st_mtime_ns}".encode()).hexdigest()
    settings = _read_json(PREFERENCES) or {}
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
             job_folder=str(job_dir), report=None)
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
            job_output_folder=job_dir)
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
                        "technical_validation": c.get("validation", "not validated"),
                        "glossary_replacements": (c.get("render") or {}).get("glossary_replacements", []),
                        "mp4_exists": (job_dir / c.get("filename", "")).is_file(),
                        "srt_exists": (job_dir / Path(c.get("filename", "")).with_suffix(".srt").name).is_file()}
                       for c in clips]}
        _write_json(manifest_path, report)
        _publish(state=outcome, status=f"{outcome.title()} · {len(report['drafts'])} draft(s) · {failure or 'review local outputs'}",
                 report=report, job_id=job_id, job_folder=str(job_dir))
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
                snapshot["report"] = {key: snapshot["report"].get(key) for key in _STATUS_REPORT_FIELDS}
            return self._json(snapshot)
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
            size = target.stat().st_size; start, end, status = 0, size - 1, 200
            byte_range = self.headers.get("Range")
            if byte_range and byte_range.startswith("bytes="):
                try:
                    first, last = byte_range[6:].split("-", 1); start = int(first or 0); end = min(int(last) if last else size - 1, size - 1)
                    if start > end or start >= size: raise ValueError()
                    status = 206
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
        if self.path == "/api/cancel":
            with _LOCK: cancel = _CANCEL
            if cancel: cancel.set(); return self._json({"ok": True})
            return self._json({"error": "No active job"}, 409)
        if self.path != "/api/start": return self._json({"error": "Not found"}, 404)
        try: body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        except (ValueError, json.JSONDecodeError): return self._json({"error": "Invalid JSON"}, 400)
        with _LOCK:
            if _STATE.get("state") == "running": return self._json({"error": "A job is already running"}, 409)
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
        thread.start(); return self._json({"job_id": job_id, "state": "running"}, 202)


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
    args = parser.parse_args()
    serve(args.host, args.port)
