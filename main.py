from __future__ import annotations

import json
import hashlib
import os
import re
import queue
import subprocess
import sys
import threading
import time
import uuid
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from clipper import parse_time_ranges, process_video, probe, scan_videos
from diagnostics import collect_diagnostics
from model_manager import ModelManager, gpu_memory_usage
from vision import VisionModelManager, vision_installation_status
from nightly_queue import QueueStore, normalized_source_path
from queue_runner import NightlySessionRunner
from clipper_runtime.cuda_dlls import register_cuda_dll_directories

APP_DIR = Path(__file__).resolve().parent
OUTPUT = APP_DIR / "Shorts_Output"
APP_STATE = Path(os.environ.get("LOCALAPPDATA", APP_DIR)) / "Clipper"
ANALYSIS_CACHE = APP_STATE / "analysis_cache"
PREFERENCES = APP_STATE / "preferences.json"
INTERRUPTED_JOB = APP_STATE / "interrupted_job.json"


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
    temporary.replace(path)


def _whisper_model_cached(model_size: str) -> bool:
    try:
        register_cuda_dll_directories()
        from faster_whisper.utils import download_model
        return Path(download_model(model_size, local_files_only=True)).is_dir()
    except Exception:
        return False


def _source_output_folder(source: Path, base: Path = OUTPUT) -> Path:
    suffix = hashlib.sha1(str(source.resolve()).casefold().encode()).hexdigest()[:8]
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", source.stem)
    return base / f"{stem}_{suffix}"


class ClippingApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Clipper Personal Preview 0.1")
        self.geometry("1020x900")
        self.minsize(820, 700)
        self.configure(bg="#10141d")
        preferences = _read_json(PREFERENCES)
        if not isinstance(preferences, dict):
            preferences = {}
        self.folder = tk.StringVar(value=preferences.get("last_folder", ""))
        self.count = tk.IntVar(value=5)
        self.minimum = tk.IntVar(value=preferences.get("minimum", 20))
        self.maximum = tk.IntVar(value=preferences.get("maximum", 60))
        self.context_margin = tk.DoubleVar(value=preferences.get("context_margin", 4.0))
        self.include_ranges = tk.StringVar(value=preferences.get("include_ranges", ""))
        self.exclude_ranges = tk.StringVar(value=preferences.get("exclude_ranges", ""))
        self.model = tk.StringVar(value="small")
        self.encoder = tk.StringVar(value="auto")
        self.semantic = tk.BooleanVar(value=False)
        self.target = tk.IntVar(value=5)
        self.max_hours = tk.IntVar(value=6)
        self.batch_size = tk.IntVar(value=2)
        self.single_file = tk.StringVar(value="")
        self.audio_track = tk.StringVar(value="Choose audio track…")
        self.audio_track_indices = {}
        self.source_streams = []
        self.source_info = None
        self.caption_source = tk.StringVar(value="Whisper transcription")
        self.subtitle_track_indices = {}
        self.latest_output: Path | None = None
        self.caption_style = tk.StringVar(value=preferences.get("caption_style", "bold_pop"))
        self.caption_placement = tk.StringVar(value=preferences.get("caption_placement", "auto"))
        self.content_type = tk.StringVar(value=preferences.get("content_type", "Auto"))
        self.highlight_preference = tk.StringVar(value=preferences.get("highlight_preference", "Auto"))
        self.length_profile = tk.StringVar(value=preferences.get("length_profile", "Auto"))
        self.editorial_endpoint = tk.StringVar(value=preferences.get("editorial_endpoint", os.getenv("CLIPPER_LOCAL_LLM_ENDPOINT", "")))
        self.editorial_model = tk.StringVar(value=preferences.get("editorial_model", os.getenv("CLIPPER_LOCAL_LLM_MODEL", "")))
        self.editorial_status = tk.StringVar(value=self._editorial_status_text())
        self.editorial_endpoint.trace_add("write", lambda *_: self.editorial_status.set(self._editorial_status_text()))
        self.editorial_model.trace_add("write", lambda *_: self.editorial_status.set(self._editorial_status_text()))
        self.diagnostics = collect_diagnostics()
        self.gpu_memory = gpu_memory_usage()
        self.status = tk.StringVar(value="Choose one episode to create up to two provisional drafts.")
        self.detected = tk.StringVar(value="0 videos detected")
        self.model_status = tk.StringVar(value="Whisper small · unloaded · CUDA available" if self.diagnostics.get("cuda_devices") else "Whisper small · unloaded · CPU int8 fallback")
        self.vision_status = tk.StringVar(value=self._vision_setup_text(vision_installation_status()))
        self.model.trace_add("write", lambda *_: self.model_status.set(f"Whisper {self.model.get()} · unloaded · load only after Start"))
        self.queue: queue.Queue = queue.Queue()
        self.runner: NightlySessionRunner | None = None
        self.queue_window = None
        try:
            self.queue_store = QueueStore(APP_STATE / "queue.sqlite3")
        except Exception as exc:
            self.queue_store = None
            self.queue_error = str(exc)
        else:
            self.queue_error = ""
        self.running = False
        self.close_when_finished = False
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._build()
        self.after(150, self._poll)

    def _build(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background="#10141d")
        style.configure("TLabel", background="#10141d", foreground="#edf2fa", font=("Segoe UI", 10))
        style.configure("Title.TLabel", font=("Segoe UI Semibold", 24), foreground="#ffffff")
        style.configure("Sub.TLabel", foreground="#9ba8bb")
        style.configure("TButton", font=("Segoe UI Semibold", 10), padding=(13, 9))
        style.configure("Accent.TButton", background="#357e72", foreground="white")
        style.map("Accent.TButton", background=[("active", "#438f82"), ("disabled", "#354b49")])
        style.configure("Horizontal.TProgressbar", troughcolor="#252d3b", background="#438f82", bordercolor="#252d3b")
        root = ttk.Frame(self, padding=30)
        root.pack(fill="both", expand=True)
        ttk.Label(root, text="Clipper", style="Title.TLabel").pack(anchor="w")
        ttk.Label(root, text="Personal Preview 0.1 · local episode drafts", style="Sub.TLabel").pack(anchor="w", pady=(4, 24))
        choose = ttk.LabelFrame(root, text=" 1 · Choose episode ", padding=16)
        choose.pack(fill="x", pady=(0, 12))
        self.select_button = ttk.Button(choose, text="Choose video…", command=self._choose_single)
        self.select_button.pack(anchor="w")
        self.episode_details = tk.StringVar(value="No episode selected")
        ttk.Label(choose, textvariable=self.episode_details, style="Sub.TLabel", wraplength=800).pack(anchor="w", fill="x", pady=(10, 0))
        streams = ttk.LabelFrame(root, text=" 2 · Audio and captions ", padding=16)
        streams.pack(fill="x", pady=(0, 12))
        ttk.Label(streams, text="Audio track", style="Sub.TLabel").grid(row=0, column=0, sticky="w")
        self.audio_combo = ttk.Combobox(streams, textvariable=self.audio_track, values=("Choose audio track…",), width=50, state="readonly")
        self.audio_combo.grid(row=1, column=0, sticky="ew", padx=(0, 14), pady=(4, 10))
        ttk.Label(streams, text="Caption output", style="Sub.TLabel").grid(row=0, column=1, sticky="w")
        self.caption_combo = ttk.Combobox(streams, textvariable=self.caption_source,
                                          values=("Whisper transcription", "Whisper translation to English", "None"),
                                          width=38, state="readonly")
        self.caption_combo.grid(row=1, column=1, sticky="ew", pady=(4, 10))
        self.stream_details = tk.StringVar(value="Choose one video. No folder scan or processing starts during selection.")
        ttk.Label(streams, textvariable=self.stream_details, style="Sub.TLabel", wraplength=850).grid(row=2, column=0, columnspan=2, sticky="w")
        streams.columnconfigure(0, weight=1)
        streams.columnconfigure(1, weight=1)
        action = ttk.Frame(root)
        action.pack(fill="x", pady=(10, 16))
        self.single_start_button = ttk.Button(action, text="Generate Draft Shorts", style="Accent.TButton", command=self._start_single)
        self.single_start_button.pack(side="left")
        self.cancel_button = ttk.Button(action, text="Cancel safely", command=self._cancel, state="disabled")
        self.cancel_button.pack(side="left", padx=10)
        ttk.Button(action, text="Advanced settings", command=self._open_advanced).pack(side="right")
        ttk.Button(action, text="Nightly queue…", command=self._open_queue_window).pack(side="right", padx=8)
        self.progress = ttk.Progressbar(root, mode="indeterminate", maximum=100)
        self.progress.pack(fill="x", pady=(0, 8))
        ttk.Label(root, textvariable=self.status).pack(anchor="w")
        ttk.Label(root, textvariable=self.model_status, style="Sub.TLabel").pack(anchor="w", pady=(5, 0))
        ttk.Label(root, textvariable=self.vision_status, style="Sub.TLabel").pack(anchor="w", pady=(2, 8))
        ttk.Label(root, text="3 · Draft results — provisional selection", style="Title.TLabel").pack(anchor="w", pady=(8, 4))
        ttk.Label(root, text="Selection quality has not been editorially accepted.", style="Sub.TLabel").pack(anchor="w", pady=(0, 6))
        result = ttk.LabelFrame(root, text=" Latest job ", padding=10)
        result.pack(fill="x")
        self.latest_summary = tk.StringVar(value="No job run yet. Your source files remain untouched.")
        ttk.Label(result, textvariable=self.latest_summary, style="Sub.TLabel", wraplength=850).pack(anchor="w")
        self.results_links = ttk.Frame(result)
        self.results_links.pack(fill="x", anchor="w", pady=(8, 0))
        self.latest_button = ttk.Button(result, text="Open job folder", command=self._open_latest_results, state="disabled")
        self.latest_button.pack(anchor="e")
        review = ttk.LabelFrame(root, text=" Draft files ", padding=(8, 6))
        review.pack(fill="both", expand=True, pady=(10, 0))
        columns = ("clip", "source_time", "duration", "status")
        self.review_tree = ttk.Treeview(review, columns=columns, show="headings", height=3, selectmode="browse")
        for column, title, width in zip(columns, ("MP4", "Source time", "Duration", "Validation"), (160, 150, 90, 110)):
            self.review_tree.heading(column, text=title)
            self.review_tree.column(column, width=width, stretch=column == "clip")
        self.review_tree.pack(fill="both", expand=True)
        self.review_tree.bind("<<TreeviewSelect>>", self._review_selected)
        self.review_items = {}
        self.review_image = None
        self.review_image_label = None
        log_frame = ttk.LabelFrame(root, text=" Processing log ", padding=(8, 6))
        log_frame.pack(fill="both", expand=True, pady=(10, 0))
        self.log = tk.Text(log_frame, bg="#0b0e14", fg="#c9d3e2", insertbackground="white", relief="flat", font=("Cascadia Mono", 9), wrap="word", height=5, state="disabled")
        self.log.pack(fill="both", expand=True)
        self._build_settings_windows()

    def _build_settings_windows(self):
        self.advanced_window = None

    def _populate_advanced_settings(self, advanced):
        for label, var, width in (("Minimum seconds", self.minimum, 6), ("Maximum seconds", self.maximum, 6)):
            row = ttk.Frame(advanced); row.pack(fill="x", pady=4)
            ttk.Label(row, text=label, width=24).pack(side="left")
            ttk.Spinbox(row, from_=1, to=180, textvariable=var, width=width).pack(side="left")
        for label, variable, values in (("Whisper model", self.model, ("tiny", "base", "small", "medium")),
                ("Video encoder", self.encoder, ("auto", "libx264", "h264_nvenc")),
                ("Caption style", self.caption_style, ("bold_pop", "karaoke", "cinematic_minimal", "boxed")),
                ("Caption placement", self.caption_placement, ("auto", "bottom", "top")),
                ("Content type", self.content_type, ("Auto", "Anime/Animation", "Live Action", "Mixed")),
                ("Highlight", self.highlight_preference, ("Auto", "Comedy", "Action", "Drama", "Suspense", "Emotional", "Other")),
                ("Length", self.length_profile, ("Auto", "Short", "Standard", "Story"))):
            row = ttk.Frame(advanced); row.pack(fill="x", pady=4)
            ttk.Label(row, text=label, width=24).pack(side="left")
            ttk.Combobox(row, textvariable=variable, values=values, state="readonly", width=28).pack(side="left")
        ttk.Label(advanced, text="Hook context seconds").pack(anchor="w", pady=(8, 2))
        ttk.Spinbox(advanced, from_=0, to=15, increment=.5, textvariable=self.context_margin, width=8).pack(anchor="w")
        ranges = ttk.Frame(advanced); ranges.pack(fill="x", pady=8)
        ttk.Label(ranges, text="Include ranges").grid(row=0, column=0, sticky="w")
        ttk.Entry(ranges, textvariable=self.include_ranges, width=36).grid(row=1, column=0, padx=(0, 8))
        ttk.Label(ranges, text="Exclude ranges").grid(row=0, column=1, sticky="w")
        ttk.Entry(ranges, textvariable=self.exclude_ranges, width=36).grid(row=1, column=1)
        ttk.Checkbutton(advanced, text="Optional local semantic analysis", variable=self.semantic).pack(anchor="w", pady=5)
        for label, variable in (("Local model endpoint", self.editorial_endpoint), ("Local model name", self.editorial_model)):
            ttk.Label(advanced, text=label).pack(anchor="w", pady=(6, 2))
            ttk.Entry(advanced, textvariable=variable, width=70).pack(anchor="w")
        return advanced

    def _open_advanced(self):
        if self.advanced_window and self.advanced_window.winfo_exists():
            self.advanced_window.deiconify(); self.advanced_window.lift(); return
        window = tk.Toplevel(self); window.title("Advanced settings"); window.geometry("620x660"); window.minsize(520, 520)
        self.advanced_window = window
        self._populate_advanced_settings(ttk.Frame(window, padding=18)).pack(fill="both", expand=True)

    def _open_queue_window(self):
        if self.queue_window and self.queue_window.winfo_exists():
            self.queue_window.deiconify(); self.queue_window.lift(); return
        window = tk.Toplevel(self); window.title("Nightly queue"); window.geometry("650x360"); window.minsize(520, 300)
        self.queue_window = window
        frame = ttk.Frame(window, padding=18); frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Nightly queue", style="Title.TLabel").pack(anchor="w")
        ttk.Label(frame, text="Batch workflow · separate from daily single-episode drafts", style="Sub.TLabel").pack(anchor="w", pady=(3, 16))
        self.queue_summary = tk.StringVar(value="Queue is ready" if self.queue_store else f"Queue unavailable · {self.queue_error}")
        ttk.Label(frame, textvariable=self.queue_summary, style="Sub.TLabel").pack(anchor="w", pady=(0, 10))
        settings = ttk.Frame(frame); settings.pack(fill="x")
        for label, variable, upper in (("Shorts/session", self.target, 10), ("Max hours", self.max_hours, 24), ("Microbatch", self.batch_size, 4)):
            item = ttk.Frame(settings); item.pack(side="left", padx=(0, 15))
            ttk.Label(item, text=label).pack(anchor="w")
            ttk.Spinbox(item, from_=1, to=upper, textvariable=variable, width=7).pack(anchor="w", pady=4)
        tools = ttk.Frame(frame); tools.pack(fill="x", pady=12)
        self.start_button = ttk.Button(tools, text="Start nightly run", style="Accent.TButton", command=self._start); self.start_button.pack(side="left")
        self.pause_button = ttk.Button(tools, text="Pause", command=self._toggle_pause, state="disabled"); self.pause_button.pack(side="left", padx=6)
        self.rescan_button = ttk.Button(tools, text="Rescan", command=self._rescan); self.rescan_button.pack(side="left", padx=6)
        ttk.Button(tools, text="View queue", command=self._view_queue).pack(side="left", padx=6)
        self.retry_button = ttk.Button(tools, text="Retry failures", command=self._retry_failures); self.retry_button.pack(side="left", padx=6)
        self.reprocess_button = ttk.Button(tools, text="Reprocess changed", command=self._reprocess_changed); self.reprocess_button.pack(side="left", padx=6)
        ttk.Button(frame, text="Select video folder…", command=self._select).pack(anchor="w")
        ttk.Label(frame, textvariable=self.folder, style="Sub.TLabel", wraplength=580).pack(anchor="w", pady=8)

    def _log(self, text: str):
        self.log.configure(state="normal")
        self.log.insert("end", text.rstrip() + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _select(self):
        if self.running:
            return
        folder = filedialog.askdirectory(title="Select your video folder")
        if folder:
            self.folder.set(folder)
            self._save_preferences()
            videos = scan_videos(Path(folder), OUTPUT)
            self.detected.set(f"{len(videos)} video{'s' if len(videos) != 1 else ''} detected")
            self._rescan()

    def _save_preferences(self):
        _write_json(PREFERENCES, {"last_folder": self.folder.get(), "caption_style": self.caption_style.get(),
                                  "caption_placement": self.caption_placement.get(),
                                  "content_type": self.content_type.get(),
                                  "highlight_preference": self.highlight_preference.get(),
                                  "length_profile": self.length_profile.get(),
                                  "minimum": self.minimum.get(), "maximum": self.maximum.get(),
                                  "context_margin": self.context_margin.get(),
                                  "include_ranges": self.include_ranges.get(), "exclude_ranges": self.exclude_ranges.get(),
                                  "editorial_endpoint": self.editorial_endpoint.get(),
                                  "editorial_model": self.editorial_model.get()})

    def _editorial_status_text(self):
        if self.editorial_endpoint.get().strip() and self.editorial_model.get().strip():
            return "Local narrative model configured · loopback and response schema checked when processing starts"
        return "Narrative AI unavailable · using explainable activity heuristics only"

    def _configure_local_options(self):
        os.environ["CLIPPER_CAPTION_STYLE"] = self.caption_style.get()
        os.environ["CLIPPER_CAPTION_PLACEMENT"] = {"top": "upper", "bottom": "lower"}.get(self.caption_placement.get(), "auto")
        os.environ["CLIPPER_CONTENT_TYPE"] = self.content_type.get()
        os.environ["CLIPPER_HIGHLIGHT_PREFERENCE"] = self.highlight_preference.get()
        os.environ["CLIPPER_CLIP_LENGTH_PROFILE"] = self.length_profile.get()
        os.environ["CLIPPER_CONTEXT_MARGIN"] = str(self.context_margin.get())
        os.environ["CLIPPER_INCLUDE_RANGES"] = self.include_ranges.get()
        os.environ["CLIPPER_EXCLUDE_RANGES"] = self.exclude_ranges.get()
        if self.editorial_endpoint.get().strip():
            os.environ["CLIPPER_LOCAL_LLM_ENDPOINT"] = self.editorial_endpoint.get().strip()
        else:
            os.environ.pop("CLIPPER_LOCAL_LLM_ENDPOINT", None)
        if self.editorial_model.get().strip():
            os.environ["CLIPPER_LOCAL_LLM_MODEL"] = self.editorial_model.get().strip()
        else:
            os.environ.pop("CLIPPER_LOCAL_LLM_MODEL", None)
        self._save_preferences()

    def _clip_lengths(self):
        return {"Auto": (self.minimum.get(), self.maximum.get()), "Short": (12, 30),
                "Standard": (20, 60), "Story": (40, 90)}[self.length_profile.get()]

    def _validate_editorial_controls(self):
        try:
            parse_time_ranges(self.include_ranges.get())
            parse_time_ranges(self.exclude_ranges.get())
            if not 0 <= self.context_margin.get() <= 15:
                raise ValueError("Hook context must be between 0 and 15 seconds.")
        except (ValueError, TypeError) as exc:
            messagebox.showerror("Invalid source-time settings", str(exc), parent=self)
            return False
        return True

    def _choose_single(self):
        if self.running:
            return
        path = filedialog.askopenfilename(title="Choose one episode", filetypes=[("Video files", "*.mp4 *.mkv *.mov *.avi *.webm *.m4v"), ("All files", "*.*")])
        if path:
            self.single_file.set(path)
            self.source_info = None
            self.source_streams = []
            self.audio_track_indices = {"Choose audio track…": None}
            self.subtitle_track_indices = {}
            try:
                info = probe(Path(path))
                streams = info.streams or []
                self.source_info = info
                self.source_streams = streams
                audio = [stream for stream in streams if stream["type"] == "audio"]
                self.audio_track_indices = {"Choose audio track…": None}
                choices = ["Choose audio track…"]
                details = []
                captions = ["Whisper transcription", "Whisper translation to English", "None"]
                self.subtitle_track_indices = {}
                for stream in streams:
                    details.append(f"#{stream['index']} {stream['type']} · {stream.get('language') or 'und'} · {stream['codec']} · {stream.get('title') or 'untitled'}" + (f" · {stream.get('channels')}ch" if stream.get("channels") else "") + (" · default" if stream["default"] else ""))
                    if stream["type"] == "audio":
                        label = f"{stream.get('language') or 'und'} · #{stream['index']} · {stream.get('title') or stream['codec']}" + (" · default" if stream["default"] else "")
                        choices.append(label)
                        self.audio_track_indices[label] = stream["index"]
                    if stream["type"] == "subtitle":
                        label = f"Embedded {stream.get('language') or 'und'} subtitles · #{stream['index']}"
                        captions.insert(-1, label)
                        self.subtitle_track_indices[label] = stream["index"]
                self.audio_combo.configure(values=choices)
                self.caption_combo.configure(values=captions)
                english_matches = [stream for stream in audio if str(stream.get("language") or "").casefold() in {"en", "eng"}
                                   or str(stream.get("language") or "").casefold().startswith("en-")]
                selected_stream = audio[0] if len(audio) == 1 else english_matches[0] if len(english_matches) == 1 else None
                if selected_stream:
                    self.audio_track.set(next(label for label in choices if self.audio_track_indices.get(label) == selected_stream["index"]))
                    self.audio_track_indices.pop("Choose audio track…", None)
                else:
                    self.audio_track.set(choices[0])
                self.episode_details.set(f"{Path(path).name} · {info.duration / 60:.1f} min · {info.width}×{info.height} · {sum(s['type'] == 'video' for s in streams)} video / {len(audio)} audio stream(s)")
                self.stream_details.set("Source streams: " + (" | ".join(details) if details else "no audio or subtitle streams"))
            except Exception as exc:
                self.source_info = None
                self.stream_details.set(f"Stream inspection failed: {type(exc).__name__}: {exc}")

    def _start_single(self):
        if self.running:
            return
        source = Path(self.single_file.get())
        if not source.is_file() or source.suffix.lower() not in {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v"}:
            messagebox.showinfo("Choose one episode", "Select one supported video file first.", parent=self)
            return
        if not self.diagnostics.get("ffmpeg") or not self.diagnostics.get("ffprobe") or not any(self.diagnostics.get("encoders", {}).values()):
            messagebox.showerror("Media tools unavailable", "FFmpeg, FFprobe and a verified H.264 encoder are required.", parent=self)
            return
        subtitle_filter = next((c for c in self.diagnostics["checks"] if c["name"] == "Subtitle filter"), {})
        if self.caption_source.get() != "None" and subtitle_filter.get("status") != "available":
            messagebox.showerror("FFmpeg subtitles unavailable", "This FFmpeg build lacks the libass subtitles filter required to burn captions.", parent=self)
            return
        needs_whisper = self.caption_source.get().startswith("Whisper")
        faster = next((item for item in self.diagnostics["checks"] if item["name"] == "Faster-Whisper"), {})
        transcription = needs_whisper and faster.get("status") == "available" and _whisper_model_cached(self.model.get())
        if needs_whisper and not transcription and not messagebox.askyesno("Continue without speech recognition?", "Whisper or its selected model is unavailable. Continue in heuristic-only mode? No model will be downloaded.", parent=self):
            return
        min_length, max_length = self._clip_lengths()
        if min_length < 1 or max_length < min_length or max_length > 180:
            messagebox.showerror("Invalid settings", "Draft duration must be between 1 and 180 seconds.", parent=self)
            return
        audio_tracks = [item for item in self.source_streams if item["type"] == "audio"]
        audio_label = self.audio_track.get()
        if audio_tracks and (audio_label not in self.audio_track_indices or self.audio_track_indices[audio_label] is None):
            messagebox.showerror("Choose an audio track", "Select the source audio track explicitly before generating drafts.", parent=self)
            return
        if self.caption_source.get().startswith("Embedded") and self.caption_source.get() not in self.subtitle_track_indices:
            messagebox.showerror("Choose source subtitles", "Select a valid embedded subtitle stream or choose a Whisper caption mode.", parent=self)
            return
        if self.semantic.get() and not vision_installation_status().get("ready"):
            messagebox.showerror("Vision model unavailable", "Disable optional semantic analysis or use a verified local model installation.", parent=self)
            return
        if not self._validate_editorial_controls():
            return
        self._configure_local_options()
        self.running = True
        self.runner = None
        self.single_start_button.configure(state="disabled")
        self.select_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.single_cancel = threading.Event()
        self.status.set(f"Starting provisional draft job · {source.name}")
        self.progress.start(12)
        manager = ModelManager(self.model.get(), local_files_only=True,
                               status_callback=lambda value: self.queue.put(("model_status", value)))
        vision = VisionModelManager(status_callback=lambda value: self.queue.put(("vision_status", value))) if self.semantic.get() else None
        audio_index = self.audio_track_indices.get(self.audio_track.get())
        subtitle_index = self.subtitle_track_indices.get(self.caption_source.get())
        task = "translate" if self.caption_source.get() == "Whisper translation to English" else "transcribe"
        settings = (self.model.get(), 2, min_length, max_length, self.encoder.get(), self.semantic.get())
        threading.Thread(target=self._single_worker, args=(source, settings, manager, vision, transcription,
                                                           audio_index, self.caption_source.get(), task, subtitle_index), daemon=True).start()

    def _single_worker(self, source, settings, manager, vision, transcription, audio_index=None,
                       caption_source="Whisper transcription", transcription_task="transcribe", subtitle_index=None):
        job_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
        job_dir = OUTPUT / "jobs" / job_id
        cache_key = hashlib.sha256(str(source.resolve()).casefold().encode("utf-8")).hexdigest()[:20]
        cache_dir = ANALYSIS_CACHE / cache_key
        started = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        stat = None
        source_fingerprint = None
        outcome = "failed"
        count = 0
        report = {}
        try:
            stat = source.stat()
            source_fingerprint = hashlib.sha256(f"{source.resolve()}|{stat.st_size}|{stat.st_mtime_ns}".encode()).hexdigest()
            def progress(message):
                self.queue.put(("log", message))
                self.queue.put(("status", message))
            job_dir.mkdir(parents=True, exist_ok=False)
            count = process_video(source, job_dir, settings[0], 2, settings[2], settings[3], self.single_cancel, progress, settings[4], manager,
                                  settings[5], vision, transcription_enabled=transcription,
                                  audio_index=audio_index, caption_source=caption_source,
                                  transcription_task=transcription_task, subtitle_index=subtitle_index,
                                  cache_root=cache_dir, job_output_folder=job_dir)
            outcome = "interrupted" if self.single_cancel.is_set() else "completed"
        except Exception as exc:
            outcome = "interrupted" if self.single_cancel.is_set() or isinstance(exc, InterruptedError) else "failed"
            report["failure"] = f"{type(exc).__name__}: {exc}"
        finally:
            try:
                manager.release_whisper()
            except Exception as exc:
                report["whisper_cleanup_error"] = f"{type(exc).__name__}: {exc}"
            if vision:
                try:
                    vision.release()
                except Exception as exc:
                    report["vision_cleanup_error"] = f"{type(exc).__name__}: {exc}"
            manifest_path = job_dir / "analysis.json"
            try:
                existing = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
            except (OSError, json.JSONDecodeError):
                existing = {}
            try:
                info = self.source_info if self.single_file.get() == str(source) and self.source_info else probe(source)
                source_metadata = {"duration_seconds": info.duration, "width": info.width, "height": info.height,
                                   "video_codec": next((s.get("codec") for s in self.source_streams if s["type"] == "video"), None)}
            except Exception as exc:
                source_metadata = {"probe_error": str(exc)}
            if stat is None:
                try:
                    stat = source.stat()
                    source_fingerprint = hashlib.sha256(f"{source.resolve()}|{stat.st_size}|{stat.st_mtime_ns}".encode()).hexdigest()
                except OSError:
                    pass
            source_size = stat.st_size if stat else None
            source_mtime_ns = stat.st_mtime_ns if stat else None
            clips = existing.get("clips", []) if isinstance(existing, dict) else []
            if outcome == "interrupted":
                count = len(clips)
            candidate = existing.get("candidate_selection", {}) if isinstance(existing, dict) else {}
            report.update(existing if isinstance(existing, dict) else {})
            report.update({"schema": "clipper-personal-preview-job-v1", "job_id": job_id,
                "job_started_at": started, "job_finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "job_outcome": outcome,
                "source": source.name, "source_path": str(source.resolve()), "source_size": source_size,
                "source_mtime_ns": source_mtime_ns, "source_fingerprint": source_fingerprint,
                "source_fingerprint_method": "SHA-256(resolved path, size, modification time)", **source_metadata,
                "selected_audio_stream_index": audio_index,
                "selected_audio_stream": next((s for s in self.source_streams if s.get("index") == audio_index), None),
                "caption_mode": caption_source, "transcription_task": transcription_task,
                "transcription_device": existing.get("transcription_device", "not run"),
                "transcription_compute_type": existing.get("transcription_precision", "not run"),
                "transcription_fallback_reason": existing.get("transcription_message") if existing.get("transcription_outcome") == "CPU_FALLBACK" else None,
                "cache": {"analysis_signals": existing.get("analysis_cache_status", "not run"),
                          "transcription": existing.get("transcription_cache_status", "not run"),
                          "editorial": existing.get("editorial_analysis", {}).get("cache_hits", 0),
                          "semantic": existing.get("semantic_analysis", {}).get("cache_hits", 0)},
                "stage_outcomes": {"analysis_signals": existing.get("analysis_cache_status", "not run"),
                    "transcription": existing.get("transcription_outcome", "not run"),
                    "candidate_selection": "completed" if candidate else "not completed",
                    "rendered_artifacts": [clip.get("validation", "not validated") for clip in clips]},
                "candidate_count": candidate.get("candidate_count", 0), "selected_draft_count": len(clips),
                "drafts": [{"start": clip.get("start"), "end": clip.get("end"), "filename": clip.get("filename"),
                            "srt": Path(clip.get("filename", "")).with_suffix(".srt").name,
                            "technical_validation": clip.get("validation", "not validated"),
                            "mp4_exists": (job_dir / clip.get("filename", "")).is_file(),
                            "srt_exists": (job_dir / Path(clip.get("filename", "")).with_suffix(".srt").name).is_file()}
                           for clip in clips],
                "selection_status": "PROVISIONAL",
                "editorial_acceptance": "Not evaluated or accepted; outputs are provisional drafts.",
                "job_output_directory": str(job_dir)})
            _write_json(manifest_path, report)
            self.queue.put(("single_finished", (source.name, count, job_dir, outcome, report)))

    def _rescan(self):
        if self.running:
            return
        if not self.folder.get() or not Path(self.folder.get()).is_dir():
            return
        videos = scan_videos(Path(self.folder.get()), OUTPUT)
        self.detected.set(f"{len(videos)} video{'s' if len(videos) != 1 else ''} detected")
        if self.queue_store:
            self.queue_store.scan(Path(self.folder.get()), videos)
            counts = self.queue_store.counts(Path(self.folder.get()))
            self.queue_summary.set("Queue · " + " · ".join(f"{state.lower()} {count}" for state, count in counts.items() if count))

    def _retry_failures(self):
        if self.running or not self.queue_store:
            return
        root = Path(self.folder.get()).resolve(strict=False)
        failed = [row for row in self.queue_store.episodes({"FAILED_RETRYABLE", "FAILED_FINAL"})
                  if Path(row["path"]).resolve(strict=False).is_relative_to(root)]
        if not failed:
            messagebox.showinfo("No failed episodes", "There are no failed episodes to retry.", parent=self)
            return
        if messagebox.askyesno("Retry failed episodes?", f"Return {len(failed)} failed episode(s) to the pending queue?", parent=self):
            for episode in failed:
                self.queue_store.retry_episode(episode["id"])
            self._rescan()

    def _view_queue(self):
        if not self.queue_store:
            return
        root = Path(self.folder.get()).resolve(strict=False) if self.folder.get() else None
        rows = [row for row in self.queue_store.episodes()
                if root is None or Path(row["path"]).resolve(strict=False).is_relative_to(root)]
        text = "\n".join(f"{row['status']:<18} {row['filename']}" +
                         (f" · {row['last_error']}" if row["last_error"] else "") for row in rows)
        messagebox.showinfo("Nightly queue", text or "The queue is empty.", parent=self)

    def _reprocess_changed(self):
        if self.running or not self.queue_store:
            return
        root = Path(self.folder.get()).resolve(strict=False)
        changed = [row for row in self.queue_store.episodes({"SOURCE_CHANGED"})
                   if Path(row["path"]).resolve(strict=False).is_relative_to(root)]
        if not changed:
            messagebox.showinfo("No changed sources", "No source changes need reprocessing.", parent=self)
            return
        if messagebox.askyesno("Reprocess changed sources?", f"Reprocess {len(changed)} changed source(s)? Previous output files will be retained.", parent=self):
            for episode in changed:
                self.queue_store.requeue_source_changed(episode["id"])
            self._rescan()

    def _start(self):
        if self.running:
            return
        if not self.folder.get() or not Path(self.folder.get()).is_dir():
            messagebox.showinfo("Choose a folder", "Select a folder containing your videos first.")
            return
        if not self.diagnostics.get("ffmpeg") or not self.diagnostics.get("ffprobe"):
            messagebox.showerror("FFmpeg required", "FFmpeg/FFprobe were not found. Install them or set CLIPPER_FFMPEG_DIR to their folder, then restart the app.")
            return
        subtitle_filter = next((c for c in self.diagnostics["checks"] if c["name"] == "Subtitle filter"), {})
        if subtitle_filter.get("status") != "available":
            messagebox.showerror("FFmpeg subtitles unavailable", "This FFmpeg build lacks the libass subtitles filter required to burn captions.")
            return
        if not any(self.diagnostics.get("encoders", {}).values()):
            messagebox.showerror("H.264 encoder unavailable", "This FFmpeg build has no verified H.264 encoder. Install a build with libx264 or h264_nvenc.")
            return
        faster_whisper = next((item for item in self.diagnostics["checks"] if item["name"] == "Faster-Whisper"), {})
        transcription_enabled = faster_whisper.get("status") == "available"
        if faster_whisper.get("status") != "available" and not messagebox.askyesno(
                "Speech recognition unavailable",
                "Faster-Whisper is not installed. Continue with audio and visual heuristics only? No package will be installed by Clipper.", parent=self):
            return
        if transcription_enabled and not _whisper_model_cached(self.model.get()):
            if not messagebox.askyesno("Whisper model unavailable",
                                       f"The {self.model.get()} model is not cached. Continue in heuristic-only mode without speech recognition? Clipper will not download model weights during this run.",
                                       parent=self):
                return
            transcription_enabled = False
        if self.semantic.get() and not vision_installation_status().get("ready"):
            if not messagebox.askyesno("Vision model unavailable",
                                       "The optional local vision model is not ready. Continue in heuristic-only mode? Clipper will disable semantic analysis for this run.",
                                       parent=self):
                return
            self.semantic.set(False)
        if not self.queue_store:
            messagebox.showerror("Queue unavailable", self.queue_error, parent=self)
            return
        videos = scan_videos(Path(self.folder.get()), OUTPUT)
        if not videos:
            messagebox.showinfo("No videos found", "This folder contains no supported video files.")
            return
        min_length, max_length = self._clip_lengths()
        if (self.count.get() < 1 or min_length < 1 or max_length < min_length or
                not 1 <= self.target.get() <= 10 or not 1 <= self.max_hours.get() <= 24 or
                not 1 <= self.batch_size.get() <= 4):
            messagebox.showerror("Invalid settings", "Check clip duration, target (1–10), session hours (1–24), and microbatch (1–4).")
            return
        if not self._validate_editorial_controls():
            return
        self.queue_store.scan(Path(self.folder.get()), videos)
        self._import_legacy_interrupted_job()
        interrupted = self.queue_store.has_interrupted(Path(self.folder.get()))
        resume_interrupted = False
        if interrupted:
            resume_interrupted = messagebox.askyesno(
                "Resume interrupted queue?",
                "This folder has interrupted episodes. Resume them in this nightly run? Completed, validated clips will be retained.",
                parent=self)
        self._configure_local_options()
        self.running = True
        self.progress.stop()
        self.progress.configure(mode="determinate", value=0, maximum=self.target.get())
        self.single_start_button.configure(state="disabled")
        self.select_button.configure(state="disabled")
        self.rescan_button.configure(state="disabled")
        self.retry_button.configure(state="disabled")
        self.reprocess_button.configure(state="disabled")
        self.start_button.configure(state="disabled")
        self.pause_button.configure(text="Pause", state="normal")
        self.cancel_button.configure(state="normal")
        if not transcription_enabled:
            self.model_status.set("Whisper unavailable · heuristic-only for this run · no download")
        settings = (self.model.get(), self.count.get(), min_length, max_length, self.encoder.get(), self.semantic.get())
        self._save_preferences()
        def progress(message):
            OUTPUT.mkdir(parents=True, exist_ok=True)
            with (OUTPUT / "processing.log").open("a", encoding="utf-8") as log_file:
                log_file.write(message.rstrip() + "\n")
            self.queue.put(("log", message))
            self.queue.put(("status", message))
        manager = ModelManager(settings[0], local_files_only=True,
                               status_callback=lambda value: self.queue.put(("model_status", value)))
        vision = VisionModelManager(status_callback=lambda value: self.queue.put(("vision_status", value))) if settings[5] else None
        self.runner = NightlySessionRunner(
            self.queue_store, OUTPUT, *settings[:5], semantic_enabled=settings[5],
            target=self.target.get(), max_seconds=self.max_hours.get() * 3600,
            batch_size=self.batch_size.get(), resume_interrupted=resume_interrupted,
            progress=progress, status=lambda value: self.queue.put(("queue_status", value)),
            model_manager=manager, vision_manager=vision,
            transcription_enabled=transcription_enabled)
        threading.Thread(target=self._worker, args=(Path(self.folder.get()),), daemon=True).start()

    def _worker(self, folder: Path):
        try:
            result = self.runner.run(folder)
            self.queue.put(("finished", result))
        except Exception as exc:
            self.queue.put(("failed", f"{type(exc).__name__}: {exc}"))

    def _import_legacy_interrupted_job(self):
        job = _read_json(INTERRUPTED_JOB)
        if not self.queue_store or not isinstance(job, dict) or job.get("status") not in {"running", "interrupted"}:
            return
        folder_value, videos = job.get("folder"), job.get("videos")
        if (not isinstance(folder_value, str) or normalized_source_path(Path(folder_value)) !=
                normalized_source_path(Path(self.folder.get())) or not isinstance(videos, list)):
            return
        paths = [Path(value) for value in videos if isinstance(value, str) and Path(value).is_file()]
        completed_values = job.get("completed", [])
        completed = {normalized_source_path(Path(value)) for value in completed_values if isinstance(value, str)} if isinstance(completed_values, list) else set()
        by_path = {row["path"]: row for row in self.queue_store.episodes()}
        for path in paths:
            row = by_path.get(normalized_source_path(path))
            if row and normalized_source_path(path) not in completed and row["status"] == "PENDING":
                self.queue_store.set_episode_status(row["id"], "INTERRUPTED", error="Imported from the previous interrupted-job ledger")

    def _poll(self):
        try:
            while True:
                kind, value = self.queue.get_nowait()
                if kind == "log":
                    self._log(value)
                elif kind == "progress":
                    self.progress.configure(value=value)
                elif kind == "status":
                    self.status.set(value)
                elif kind == "queue_status":
                    prepared = value["clips_prepared"]
                    self.progress.configure(value=min(prepared, value["target"]))
                    remaining = value["time_left_seconds"]
                    elapsed = value["elapsed_seconds"]
                    self.status.set(f"Nightly queue · {prepared}/{value['target']} Shorts · elapsed {elapsed // 3600}h {(elapsed % 3600) // 60}m · {remaining // 3600}h {(remaining % 3600) // 60}m left")
                    self.queue_summary.set("Queue · " + " · ".join(f"{state.lower()} {count}" for state, count in value["queue_counts"].items() if count))
                elif kind == "model_status":
                    device = f"{value.get('device')} / {value.get('compute_type')}"
                    gpu = value.get("vram_after_load") or value.get("vram_before") or {}
                    memory = f" · GPU memory {gpu.get('free_mib')} / {gpu.get('total_mib')} MiB free" if gpu else ""
                    fallback = f" · {value['fallback_reason']}" if value.get("fallback_reason") else ""
                    self.model_status.set(f"Whisper {value.get('model')} · {value.get('state')} · {device}{memory}{fallback}")
                elif kind == "vision_status":
                    gpu = value.get("vram") or value.get("vram_after_load") or {}
                    memory = f" · GPU memory {gpu.get('free_mib')} / {gpu.get('total_mib')} MiB free" if gpu else ""
                    precision = f" / {value.get('precision')}" if value.get("precision") else ""
                    fallback = f" · CPU fallback: {value.get('fallback_reason')}" if value.get("fallback_reason") else ""
                    reason = f" · {value.get('reason')}" if value.get("reason") else ""
                    state = "model initialization failed" if value.get("reason") and value.get("state") == "unloaded" else value.get("state")
                    self.vision_status.set(f"Vision {state} · {value.get('device', 'unloaded')}{precision}{memory}{fallback}{reason}")
                elif kind == "vision_setup_finished":
                    code, verified, text = value
                    prefix = "Model downloaded and verified · " if code == 0 and verified else "Vision setup failed · "
                    self.vision_status.set(prefix + text)
                elif kind == "finished":
                    self.running = False
                    self.single_start_button.configure(state="normal")
                    self.start_button.configure(state="normal")
                    self.select_button.configure(state="normal")
                    self.rescan_button.configure(state="normal")
                    self.retry_button.configure(state="normal")
                    self.reprocess_button.configure(state="normal")
                    self.pause_button.configure(state="disabled", text="Pause")
                    self.cancel_button.configure(state="disabled")
                    self.progress.stop()
                    self.progress.configure(mode="indeterminate")
                    session = value["session"]
                    counts = value["queue_counts"]
                    self.status.set(f"Nightly run {session['status'].lower()} · {value['clips_prepared']} Shorts · {value['episodes_processed']} episodes ({value['episodes_skipped']} skipped, {value['episodes_failed']} failed) · {counts.get('PENDING', 0)} pending")
                    self._log(self.status.get())
                    if self.close_when_finished:
                        self._close_store()
                        self.destroy()
                        return
                elif kind == "single_finished":
                    self.running = False
                    self.single_start_button.configure(state="normal")
                    if hasattr(self, "start_button"):
                        self.start_button.configure(state="normal")
                    self.select_button.configure(state="normal")
                    self.cancel_button.configure(state="disabled")
                    self.progress.stop()
                    self.progress.configure(mode="indeterminate")
                    name, count, self.latest_output, outcome, report = value
                    self.latest_button.configure(state="normal")
                    self._load_review_folder(self.latest_output)
                    actual = "validated draft" if count == 1 else "validated drafts"
                    self.status.set(f"{outcome.title()} · {name} · {count} {actual}")
                    self.latest_summary.set(f"{count} provisional draft(s) · job {report['job_id']} · never treated as quality-approved")
                    if count == 0:
                        reason = report.get("failure") or ("No candidates passed the current selection rules." if outcome == "completed" else "See the job report for the recorded reason.")
                        self.latest_summary.set(f"No draft exported · {outcome.title()} · {reason}")
                    for child in self.results_links.winfo_children():
                        child.destroy()
                    for draft in report.get("drafts", []):
                        filename = draft.get("filename")
                        if not isinstance(filename, str) or Path(filename).name != filename:
                            continue
                        row = ttk.Frame(self.results_links); row.pack(fill="x", pady=2)
                        ttk.Label(row, text=filename, width=22).pack(side="left")
                        if draft.get("mp4_exists"):
                            ttk.Button(row, text="Open MP4", command=lambda f=filename: self._open_local_file(self.latest_output / f)).pack(side="left", padx=4)
                        srt = Path(filename).with_suffix(".srt").name
                        if draft.get("srt_exists"):
                            ttk.Button(row, text="Open SRT", command=lambda f=srt: self._open_local_file(self.latest_output / f)).pack(side="left", padx=4)
                    if self.close_when_finished:
                        self._close_store()
                        self.destroy()
                        return
                elif kind == "failed":
                    self.running = False
                    self.single_start_button.configure(state="normal")
                    self.start_button.configure(state="normal")
                    self.select_button.configure(state="normal")
                    self.rescan_button.configure(state="normal")
                    self.retry_button.configure(state="normal")
                    self.reprocess_button.configure(state="normal")
                    self.pause_button.configure(state="disabled", text="Pause")
                    self.cancel_button.configure(state="disabled")
                    self.progress.stop()
                    self.progress.configure(mode="indeterminate")
                    self.status.set("Nightly run failed · see log")
                    self._log(value)
                    if self.close_when_finished:
                        self._close_store()
                        self.destroy()
                        return
        except queue.Empty:
            pass
        self.after(150, self._poll)

    def _cancel(self):
        if self.runner:
            self.runner.stop()
            self.status.set("Stopping safely after the current operation…")
        elif getattr(self, "single_cancel", None):
            self.single_cancel.set()
            self.status.set("Stopping single-episode processing safely…")

    def _toggle_pause(self):
        if not self.runner:
            return
        if self.runner.pause_event.is_set():
            self.runner.resume()
            self.pause_button.configure(text="Pause")
            self.status.set("Resuming nightly queue…")
        else:
            self.runner.pause()
            self.pause_button.configure(text="Resume")
            self.status.set("Pause requested · current operation will finish")

    def _close_store(self):
        if self.queue_store:
            self.queue_store.close()
            self.queue_store = None

    def _setup_vision(self):
        if not messagebox.askyesno("Download local vision model?",
                                   "This downloads and verifies the pinned SmolVLM2 model files from Hugging Face.\n\nInstall optional packages first with:\n.venv\\Scripts\\python.exe -m pip install -r requirements-vision.txt\n\nInstall PyTorch separately using the official Windows/Pip/CUDA selector at pytorch.org/get-started/locally. Python 3.14 is supported. Continue with model download?",
                                   parent=self):
            return
        self.vision_status.set("Model download in progress · pinned revision")
        def download():
            try:
                process = subprocess.Popen([sys.executable, str(APP_DIR / "setup_vision_model.py"), "--allow-download"],
                                           cwd=APP_DIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
                                           creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                output = []
                line = ""
                while char := process.stdout.read(1):
                    if char in "\r\n":
                        if line:
                            output.append(line)
                            self.queue.put(("log", line))
                            line = ""
                    else:
                        line += char
                if line:
                    output.append(line)
                    self.queue.put(("log", line))
                code = process.wait()
                self.queue.put(("vision_setup_finished", (code,
                    any(item.startswith("MODEL_SNAPSHOT_VERIFIED ") for item in output),
                    self._vision_setup_text(vision_installation_status()))))
            except Exception as exc:
                self.queue.put(("vision_setup_finished", (1, False, f"Model initialization failed: {exc}")))
        threading.Thread(target=download, daemon=True).start()

    @staticmethod
    def _vision_setup_text(status: dict) -> str:
        parts = []
        if status["missing_dependencies"]:
            parts.append("Python dependencies missing: " + ", ".join(status["missing_dependencies"]))
        if status["torch_missing"]:
            parts.append("PyTorch missing · use the official Windows CUDA selector")
        if not status["weights_verified"]:
            parts.append("Model weights missing or unverified")
        else:
            parts.append("Model downloaded and verified")
            if status["ready"]:
                parts.append("ready for inference · " + status["device"])
        return "Vision · " + " · ".join(parts)

    def _on_close(self):
        if self.running:
            if not messagebox.askyesno(
                "Stop processing and close?",
                "Clipper will stop safely, keep completed files, and record the interrupted job. It will not resume automatically.",
                parent=self,
            ):
                return
            self.close_when_finished = True
            self._cancel()
            return
        self._close_store()
        self.destroy()

    def _open_output(self):
        OUTPUT.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(str(OUTPUT))
        else:
            subprocess.Popen(["xdg-open", str(OUTPUT)])

    def _open_latest_results(self):
        if not self.latest_output or not self.latest_output.is_dir():
            return
        if os.name == "nt":
            os.startfile(str(self.latest_output))
        else:
            subprocess.Popen(["xdg-open", str(self.latest_output)])

    def _load_review_folder(self, folder: Path):
        self.review_folder = folder
        self.review_items.clear()
        for item in self.review_tree.get_children():
            self.review_tree.delete(item)
        try:
            data = json.loads((folder / "analysis.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        for index, clip in enumerate(data.get("clips", [])):
            filename = clip.get("filename")
            if not isinstance(filename, str) or Path(filename).name != filename:
                continue
            iid = f"clip-{index + 1}"
            self.review_items[iid] = {**clip, "folder": folder}
            self.review_tree.insert("", "end", iid=iid, values=(filename,
                f"{clip.get('start', 0):.1f}–{clip.get('end', 0):.1f}s", f"{clip.get('duration', 0):.1f}s",
                clip.get("validation", clip.get("status", "not validated"))))

    def _selected_review_item(self):
        selected = self.review_tree.selection()
        return self.review_items.get(selected[0]) if selected else None

    def _review_selected(self, _event=None):
        item = self._selected_review_item()
        if not item or self.review_image_label is None:
            return
        thumbnail = item["folder"] / f"{Path(item['filename']).stem}_thumb.png"
        try:
            self.review_image = tk.PhotoImage(file=str(thumbnail))
            factor = max(1, self.review_image.width() // 160)
            if factor > 1:
                self.review_image = self.review_image.subsample(factor)
            self.review_image_label.configure(image=self.review_image, text="")
        except tk.TclError:
            self.review_image = None
            self.review_image_label.configure(image="", text="Thumbnail unavailable")

    @staticmethod
    def _open_local_file(path: Path):
        if not path.is_file() and not path.is_dir():
            messagebox.showinfo("File unavailable", f"Could not find {path}.")
            return
        if os.name == "nt":
            os.startfile(str(path))
        else:
            subprocess.Popen(["xdg-open", str(path)])

    def _play_review_clip(self):
        item = self._selected_review_item()
        if item:
            self._open_local_file(item["folder"] / item["filename"])

    def _reveal_review_clip(self):
        item = self._selected_review_item()
        if item:
            path = item["folder"] / item["filename"]
            if os.name == "nt":
                subprocess.Popen(["explorer", "/select,", str(path)])
            else:
                self._open_local_file(item["folder"])

    def _open_review_srt(self):
        item = self._selected_review_item()
        if item:
            self._open_local_file((item["folder"] / item["filename"]).with_suffix(".srt"))

    def _open_review_analysis(self):
        item = self._selected_review_item()
        if item:
            self._open_local_file(item["folder"] / "analysis.json")

    def _open_review_contact_sheet(self):
        item = self._selected_review_item()
        if item:
            self._open_local_file(item["folder"] / "clip_contact_sheet.png")

    def _open_review_debug(self):
        item = self._selected_review_item()
        if item and item.get("debug_image"):
            self._open_local_file(item["folder"] / item["debug_image"])


if __name__ == "__main__":
    ClippingApp().mainloop()
