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

_LOCK = threading.RLock()
_CANCEL: threading.Event | None = None
_STATE = {"state": "idle", "status": "Choose one local video file.", "logs": [], "job_folder": None,
          "report": None, "rendering": False, "rerender_result": None}
_MANIFEST_CACHE: dict[str, tuple[int, dict]] = {}
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
label{display:block;color:var(--muted);font-size:13px;margin:12px 0 5px}input,select,button,textarea{font:inherit;border-radius:8px;padding:10px 12px}input,select,textarea{width:100%;color:var(--text);background:#0d1118;border:1px solid var(--line)}textarea{min-height:170px;font:12px/1.5 Consolas,monospace;resize:vertical}button{border:0;background:var(--accent);color:#07150f;font-weight:700;cursor:pointer;margin:12px 8px 0 0}button.secondary{background:#333d50;color:var(--text)}button:disabled{opacity:.5;cursor:not-allowed}.row{display:grid;grid-template-columns:1fr 1fr;gap:18px}.status{font-size:16px}.log{font:12px/1.5 Consolas,monospace;white-space:pre-wrap;max-height:180px;overflow:auto;color:#bdc9dc}video{width:min(100%,440px);aspect-ratio:9/16;background:#080b10;border-radius:10px}a{color:var(--accent);margin-right:12px}.badge{color:var(--accent)}.draft{border-top:1px solid var(--line);margin-top:22px;padding-top:18px}.filename{overflow-wrap:anywhere}.nudge{display:flex;flex-wrap:wrap;gap:4px}.nudge button{margin:4px 2px;padding:6px 9px}.editor-times{font-variant-numeric:tabular-nums;color:var(--accent)}@media(max-width:650px){.row{grid-template-columns:1fr}main{margin:16px auto}}
</style><main><h1>Clipper · Personal Preview 0.1</h1><p>Local single-episode draft run · no upload · outputs remain on this PC</p>
<section><strong>1 · Choose one episode</strong><label for="source">Full path to a local video</label><input id="source" placeholder="C:\\Videos\\episode.mkv">
<button class="secondary" id="inspect">Inspect tracks</button><div class="row"><div><label for="audio">Audio track</label><select id="audio"><option>Inspect a video first</option></select></div><div><label for="captions">Captions</label><select id="captions"><option>Whisper transcription</option><option>Whisper translation to English</option><option>None</option></select></div></div>
<button id="start" disabled>Generate up to two provisional drafts</button><button id="cancel" class="secondary" disabled>Cancel safely</button></section>
<section><strong>2 · Processing</strong><p id="status" class="status">Choose one local video file.</p><p id="meta" class="badge"></p><p id="model" class="badge"></p><pre id="logs" class="log"></pre></section>
<section><strong>3 · Draft results</strong><p id="result">No job run yet.</p><p>Technically ready — rights not verified. Exports remain local; confirm you have publishing rights.</p><div id="artifacts"></div></section></main>
<script>
const $=id=>document.getElementById(id), qs=new URLSearchParams(location.search);if(qs.has('source'))$('source').value=qs.get('source');
async function inspect(){let source=$('source').value.trim();if(!source)return; $('inspect').disabled=true;try{let r=await fetch('/api/probe?source='+encodeURIComponent(source)),d=await r.json();if(!r.ok)throw Error(d.error||'Probe failed');$('audio').innerHTML='';for(const t of d.audio){let o=document.createElement('option');o.value=t.index;o.textContent=`${t.language||'und'} · stream #${t.index} · ${t.title||t.codec}${t.default?' · default':''}`;$('audio').append(o);if((t.language||'').toLowerCase().startsWith('en'))o.selected=true}if(!d.audio.length){let o=document.createElement('option');o.value='';o.textContent='No audio';$('audio').append(o)}$('meta').textContent=`${d.name} · ${d.duration.toFixed(1)} s · ${d.width}×${d.height} · ${d.video_codec||'video'}`;$('start').disabled=false}catch(e){$('meta').textContent=e.message;$('start').disabled=true}finally{$('inspect').disabled=false}}
$('inspect').onclick=inspect;$('start').onclick=async()=>{try{let r=await fetch('/api/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({source:$('source').value.trim(),audio_index:$('audio').value===''?null:Number($('audio').value),caption_mode:$('captions').value})}),d=await r.json();if(!r.ok)throw Error(d.error||'Could not start');$('result').textContent=`Job ${d.job_id} started.`}catch(e){$('status').textContent=e.message}};$('cancel').onclick=()=>fetch('/api/cancel',{method:'POST'});
fetch('/api/jobs').then(r=>r.json()).then(jobs=>{$('jobs').innerHTML=jobs.map(j=>'<option value="'+j.id+'">'+esc(j.source)+' · '+j.id+'</option>').join('')});$('open-job').onclick=async()=>{await fetch('/api/open-job',{method:'POST',body:JSON.stringify({job_id:$('jobs').value})});document.querySelectorAll('[data-unsaved]').forEach(x=>x.dataset.unsaved='false');renderedKey=''};
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let renderedKey=''; const editKey=(job,clip)=>'clipper-edit:'+job+':'+clip;
function clock(v){v=Math.max(0,Number(v)||0);let ms=Math.floor(v*1000),h=Math.floor(ms/3600000);ms%=3600000;let m=Math.floor(ms/60000);ms%=60000;let s=Math.floor(ms/1000);return `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}.${String(ms%1000).padStart(3,'0')}`}
function nudgeButtons(i,field){return [-1,-.5,.5,1].map(n=>'<button class="secondary" data-nudge="'+n+'" data-field="'+field+'-'+i+'">'+(n>0?'+':'')+n+'s</button>').join('')}
function appliedList(x){return esc((x.glossary_replacements||[]).map(r=>r.original_text+' → '+r.replacement+' (cue '+r.cue_number+')').join('; ')||'none')}
function drawDrafts(job,drafts){$('artifacts').innerHTML=drafts.map(function(x,i){
 const video='/files/'+encodeURIComponent(job)+'/'+encodeURIComponent(x.filename),srt='/files/'+encodeURIComponent(job)+'/'+encodeURIComponent(x.srt),status='edit-status-'+i;
 const start=Number(x.start)||0,end=Number(x.end)||0;
 return '<div class="draft"><p class="filename"><strong>'+esc(x.filename)+'</strong> · '+esc(x.technical_validation)+'</p><p>Provisional draft — rights not verified.</p><video id="clip-'+i+'" controls preload="metadata" src="'+video+'?v='+(x.edit_version||0)+'"></video><p><a href="'+srt+'">Open SRT</a> <a href="/files/'+encodeURIComponent(job)+'/analysis.json">analysis.json</a></p><div class="row"><div><label>Start · <span class="editor-times" id="start-time-'+i+'">'+clock(start)+'</span></label><input id="start-'+i+'" type="number" step="0.001" value="'+start+'"><div class="nudge">'+nudgeButtons(i,'start')+'</div></div><div><label>End · <span class="editor-times" id="end-time-'+i+'">'+clock(end)+'</span></label><input id="end-'+i+'" type="number" step="0.001" value="'+end+'"><div class="nudge">'+nudgeButtons(i,'end')+'</div></div></div><label for="captions-'+i+'">Caption cues · edit text and SRT times</label><textarea id="captions-'+i+'" spellcheck="false" placeholder="Loading SRT…"></textarea><label for="position-'+i+'">Caption position</label><select id="position-'+i+'"><option value="bottom-safe">Bottom safe</option><option value="center-lower">Center lower</option></select><label>Render quality</label><select id="preset-'+i+'"><option value="standard">Standard</option><option value="fast">Fast · softer background</option></select><button class="save-edit" data-index="'+i+'" data-clip="'+esc(x.filename)+'">Save &amp; Re-render Clip</button><span id="'+status+'" class="badge"></span><p>Applied glossary replacements: '+appliedList(x)+'</p></div>';
 }).join('')||'<p>No validated drafts in this job.</p>'
 drafts.forEach(async(x,i)=>{let area=$(`captions-${i}`);try{area.value=await(await fetch(`/files/${encodeURIComponent(job)}/${encodeURIComponent(x.srt)}`)).text()}catch{area.value='Could not load SRT.'}area.dataset.dirty='false';let saved=JSON.parse(localStorage.getItem(editKey(job,x.filename))||'null');if(saved){area.value=saved.captions??area.value;area.dataset.dirty=saved.dirty;area.dataset.unsaved='true';$(`start-${i}`).value=saved.start;$(`end-${i}`).value=saved.end;$(`position-${i}`).value=saved.position}area.oninput=()=>{area.dataset.dirty='true';area.dataset.unsaved='true'}});
 $('artifacts').querySelectorAll('[data-nudge]').forEach(b=>b.onclick=()=>{let input=$(b.dataset.field);input.value=(Number(input.value)+Number(b.dataset.nudge)).toFixed(3);input.dataset.unsaved='true';let id=input.id.startsWith('start-')?'start-time-':'end-time-';$(id+input.id.split('-').pop()).textContent=clock(input.value)});
 $('artifacts').querySelectorAll('input[type=number]').forEach(input=>input.oninput=()=>{input.dataset.unsaved='true';let id=input.id.startsWith('start-')?'start-time-':'end-time-';$(id+input.id.split('-').pop()).textContent=clock(input.value)});
 $('artifacts').oninput=()=>drafts.forEach((x,i)=>{let area=$(`captions-${i}`);localStorage.setItem(editKey(job,x.filename),JSON.stringify({start:$(`start-${i}`).value,end:$(`end-${i}`).value,captions:area.value,dirty:area.dataset.dirty,position:$(`position-${i}`).value}))});
 $('artifacts').querySelectorAll('.save-edit').forEach(button=>button.onclick=async()=>{let i=button.dataset.index,captionStatus=$(`edit-status-${i}`);button.disabled=true;captionStatus.textContent='Saving and rendering…';try{let area=$(`captions-${i}`),response=await fetch('/api/rerender',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({job_id:job,clip_id:button.dataset.clip,start:Number($(`start-${i}`).value),end:Number($(`end-${i}`).value),captions:area.dataset.dirty==='true'?area.value:null,caption_position:$(`position-${i}`).value,render_preset:$(`preset-${i}`).value})});let data=await response.json();if(!response.ok)throw Error(data.error||'Rerender failed');captionStatus.textContent='Queued…'}catch(error){captionStatus.textContent=error.message;button.disabled=false}});
}
async function poll(){try{let d=await(await fetch('/api/status')).json();if(!$('source').value&&d.report?.source_path){$('source').value=d.report.source_path;inspect()}$('status').textContent=d.status;$('logs').textContent=(d.logs||[]).slice(-30).join('\n');let m=d.model_status;$('model').textContent=m?`Whisper ${m.model}: ${m.device}/${m.compute_type}${m.fallback_reason?' · '+m.fallback_reason:''}`:'';$('cancel').disabled=d.state!=='running'&&!d.rendering;$('start').disabled=d.state==='running'||d.rendering||!$('meta').textContent;$('result').textContent=d.job_folder?`${d.rendering?'RENDERING EDIT':d.state.toUpperCase()} · ${d.job_folder}`:'No job run yet.';if(d.report){let e=d.report.drafts||[],key=d.job_id+JSON.stringify(e);if(key!==renderedKey&&!document.querySelector('[data-unsaved="true"]')){drawDrafts(d.job_id,e);renderedKey=key}document.querySelectorAll('.save-edit').forEach(button=>button.disabled=!!d.rendering);if(d.rerender_result){let i=e.findIndex(x=>x.filename===d.rerender_result.clip_id),target=document.getElementById(`edit-status-${i}`);if(target)target.textContent=d.rerender_result.error||`Rendered ${d.rerender_result.versioned_mp4||''}`}}}catch{}setTimeout(poll,1800)}poll();
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
        "technical_validation": c.get("validation", "PROVISIONAL")}
        for c in report.get("clips", []) if c.get("filename") and (folder / c["filename"]).is_file()]
    _publish(state="completed", report=report, job_id=job_id, job_folder=str(folder),
             rendering=False, rerender_result=None, status="Opened saved job")


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
                  cancel: threading.Event, preset: str = "standard"):
    global _CANCEL
    _publish(rendering=True, rerender_result=None, status=f"Re-rendering {clip_id}")
    try:
        result = rerender_single_clip(OUTPUT / "jobs" / job_id, clip_id, start, end,
                                      captions, position, cancel=cancel, render_preset=preset)
        with _LOCK: report = dict(_STATE.get("report") or {})
        for draft in report.get("drafts", []):
            if draft.get("filename") == clip_id:
                draft.update(start=start, end=end, edit_version=result["version"],
                             glossary_replacements=result.get("glossary_replacements", []))
        _publish(report=report, rendering=False, rerender_result={**result, "clip_id": clip_id},
                 status=f"Re-rendered {clip_id} · {result['rerender_seconds']:.1f}s")
    except Exception as exc:
        _publish(rendering=False, rerender_result={"error": f"{type(exc).__name__}: {exc}"},
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
            return self._json(snapshot)
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
        if self.path == "/api/open-job":
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
                with _LOCK:
                    if _STATE.get("state") == "running" or _STATE.get("rendering"):
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
            if _STATE.get("state") == "running" or _STATE.get("rendering"):
                return self._json({"error": "A job or clip render is already running"}, 409)
        if self.path == "/api/rerender":
            try:
                job_id, clip_id = body.get("job_id"), body.get("clip_id")
                start, end = float(body.get("start")), float(body.get("end"))
                position, captions = body.get("caption_position"), body.get("captions", "")
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
                if _STATE.get("state") == "running" or _STATE.get("rendering"):
                    return self._json({"error": "An operation is already running"}, 409)
                _write_json(job_dir / "pending-edits.json", body)
                _CANCEL = cancel
                _STATE.update(rendering=True, rerender_result=None, status=f"Re-rendering {clip_id}")
            thread = threading.Thread(target=_run_rerender,
                args=(job_id, clip_id, start, end, captions, position, cancel, preset), daemon=True)
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
            if _STATE.get("state") == "running" or _STATE.get("rendering"):
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
    args = parser.parse_args()
    serve(args.host, args.port)
