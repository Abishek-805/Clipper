from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from clipper import scan_videos
from diagnostics import collect_diagnostics
from model_manager import ModelManager, gpu_memory_usage
from vision import VisionModelManager, vision_installation_status
from nightly_queue import QueueStore, normalized_source_path
from queue_runner import NightlySessionRunner

APP_DIR = Path(__file__).resolve().parent
OUTPUT = APP_DIR / "Shorts_Output"
APP_STATE = Path(os.environ.get("LOCALAPPDATA", APP_DIR)) / "Clipper"
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
        from faster_whisper.utils import download_model
        return Path(download_model(model_size, local_files_only=True)).is_dir()
    except Exception:
        return False


class ClippingApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("AI Video Clipping Factory")
        self.geometry("760x620")
        self.minsize(680, 520)
        self.configure(bg="#10141d")
        preferences = _read_json(PREFERENCES)
        if not isinstance(preferences, dict):
            preferences = {}
        self.folder = tk.StringVar(value=preferences.get("last_folder", ""))
        self.count = tk.IntVar(value=5)
        self.minimum = tk.IntVar(value=20)
        self.maximum = tk.IntVar(value=55)
        self.model = tk.StringVar(value="small")
        self.encoder = tk.StringVar(value="auto")
        self.semantic = tk.BooleanVar(value=False)
        self.target = tk.IntVar(value=5)
        self.max_hours = tk.IntVar(value=6)
        self.batch_size = tk.IntVar(value=2)
        self.diagnostics = collect_diagnostics()
        self.gpu_memory = gpu_memory_usage()
        self.status = tk.StringVar(value="Select a folder containing videos to begin.")
        self.detected = tk.StringVar(value="0 videos detected")
        self.model_status = tk.StringVar(value="Whisper small · unloaded · CUDA available" if self.diagnostics.get("cuda_devices") else "Whisper small · unloaded · CPU int8 fallback")
        self.vision_status = tk.StringVar(value=self._vision_setup_text(vision_installation_status()))
        self.model.trace_add("write", lambda *_: self.model_status.set(f"Whisper {self.model.get()} · unloaded · load only after Start"))
        self.queue: queue.Queue = queue.Queue()
        self.runner: NightlySessionRunner | None = None
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
        style.configure("Title.TLabel", font=("Segoe UI Semibold", 23), foreground="#ffffff")
        style.configure("Sub.TLabel", foreground="#9ba8bb")
        style.configure("TButton", font=("Segoe UI Semibold", 10), padding=(13, 9))
        style.configure("Accent.TButton", background="#7157ff", foreground="white")
        style.map("Accent.TButton", background=[("active", "#8975ff"), ("disabled", "#3d3b59")])
        style.configure("Horizontal.TProgressbar", troughcolor="#252d3b", background="#7157ff", bordercolor="#252d3b")
        root = ttk.Frame(self, padding=26)
        root.pack(fill="both", expand=True)
        ttk.Label(root, text="AI Video Clipping Factory", style="Title.TLabel").pack(anchor="w")
        ttk.Label(root, text="Local analysis · vertical Shorts · originals stay untouched", style="Sub.TLabel").pack(anchor="w", pady=(4, 22))
        row = ttk.Frame(root)
        row.pack(fill="x")
        self.select_button = ttk.Button(row, text="Select Video Folder", command=self._select)
        self.select_button.pack(side="left")
        ttk.Label(row, textvariable=self.detected, style="Sub.TLabel").pack(side="right", padx=4)
        ttk.Label(root, textvariable=self.folder, style="Sub.TLabel").pack(anchor="w", fill="x", pady=(9, 12))
        advanced = ttk.LabelFrame(root, text=" Output defaults ", padding=(12, 7))
        advanced.pack(fill="x", pady=(0, 16))
        for label, var, width in [("Clips per video", self.count, 5), ("Min seconds", self.minimum, 5), ("Max seconds", self.maximum, 5)]:
            item = ttk.Frame(advanced)
            item.pack(side="left", padx=(2, 15))
            ttk.Label(item, text=label, style="Sub.TLabel").pack(anchor="w")
            ttk.Spinbox(item, from_=1, to=20 if "Clips" in label else 180, textvariable=var, width=width).pack(anchor="w", pady=(4, 0))
        model_frame = ttk.Frame(advanced)
        model_frame.pack(side="left", padx=3)
        ttk.Label(model_frame, text="Whisper model", style="Sub.TLabel").pack(anchor="w")
        ttk.Combobox(model_frame, textvariable=self.model, values=("tiny", "base", "small", "medium"), width=10, state="readonly").pack(anchor="w", pady=(4, 0))
        encoder_frame = ttk.Frame(advanced)
        encoder_frame.pack(side="left", padx=10)
        ttk.Label(encoder_frame, text="Video encoder", style="Sub.TLabel").pack(anchor="w")
        ttk.Combobox(encoder_frame, textvariable=self.encoder, values=("auto", "libx264", "h264_nvenc"), width=11, state="readonly").pack(anchor="w", pady=(4, 0))
        ttk.Checkbutton(advanced, text="Enable optional local semantic scene analysis", variable=self.semantic).pack(side="left", padx=10, pady=(14, 0))
        nightly = ttk.LabelFrame(root, text=" Nightly queue ", padding=(12, 7))
        nightly.pack(fill="x", pady=(0, 12))
        for label, variable, upper in (("Shorts this session", self.target, 10), ("Max hours", self.max_hours, 24), ("Microbatch", self.batch_size, 4)):
            item = ttk.Frame(nightly)
            item.pack(side="left", padx=(2, 15))
            ttk.Label(item, text=label, style="Sub.TLabel").pack(anchor="w")
            ttk.Spinbox(item, from_=1, to=upper, textvariable=variable, width=6).pack(anchor="w", pady=(4, 0))
        self.queue_summary = tk.StringVar(value="Queue is ready" if self.queue_store else f"Queue unavailable · {self.queue_error}")
        ttk.Label(nightly, textvariable=self.queue_summary, style="Sub.TLabel").pack(side="left", padx=8)
        controls = ttk.Frame(root)
        controls.pack(fill="x", pady=(0, 12))
        self.start_button = ttk.Button(controls, text="Start Nightly Run", style="Accent.TButton", command=self._start)
        self.start_button.pack(side="left")
        self.pause_button = ttk.Button(controls, text="Pause", command=self._toggle_pause, state="disabled")
        self.pause_button.pack(side="left", padx=8)
        self.cancel_button = ttk.Button(controls, text="Stop Safely", command=self._cancel, state="disabled")
        self.cancel_button.pack(side="left")
        self.rescan_button = ttk.Button(controls, text="Rescan Queue", command=self._rescan)
        self.rescan_button.pack(side="left", padx=8)
        ttk.Button(controls, text="Open Output Folder", command=self._open_output).pack(side="right")
        ttk.Button(controls, text="Set Up Vision Model", command=self._setup_vision).pack(side="right", padx=8)
        queue_tools = ttk.Frame(root)
        queue_tools.pack(fill="x", pady=(0, 8))
        ttk.Button(queue_tools, text="View Queue", command=self._view_queue).pack(side="left")
        self.retry_button = ttk.Button(queue_tools, text="Retry Failures", command=self._retry_failures)
        self.retry_button.pack(side="left", padx=8)
        self.reprocess_button = ttk.Button(queue_tools, text="Reprocess Changed", command=self._reprocess_changed)
        self.reprocess_button.pack(side="left")
        self.progress = ttk.Progressbar(root, mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(4, 8))
        ttk.Label(root, textvariable=self.status, style="Sub.TLabel").pack(anchor="w", pady=(0, 9))
        ttk.Label(root, textvariable=self.model_status, style="Sub.TLabel").pack(anchor="w", pady=(0, 5))
        ttk.Label(root, textvariable=self.vision_status, style="Sub.TLabel").pack(anchor="w", pady=(0, 5))
        gpu = self.diagnostics.get("gpu_name") or "NVIDIA GPU not detected"
        cuda = "CUDA transcription available" if self.diagnostics.get("cuda_devices") else "CPU transcription fallback"
        memory = f" · VRAM {self.gpu_memory['free_mib']}/{self.gpu_memory['total_mib']} MiB free" if self.gpu_memory else ""
        encoders = self.diagnostics.get("encoders", {})
        render = "NVENC verified" if encoders.get("h264_nvenc") else "libx264 verified" if encoders.get("libx264") else "H.264 encoder unavailable"
        ttk.Label(root, text=f"Runtime · {gpu}{memory} · {cuda} · {render}", style="Sub.TLabel").pack(anchor="w", pady=(0, 8))
        ttk.Label(root, text="Processing log", style="Sub.TLabel").pack(anchor="w")
        self.log = tk.Text(root, bg="#0b0e14", fg="#c9d3e2", insertbackground="white", relief="flat", font=("Cascadia Mono", 9), wrap="word", height=14, state="disabled")
        self.log.pack(fill="both", expand=True, pady=(7, 0))

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
            _write_json(PREFERENCES, {"last_folder": folder})
            videos = scan_videos(Path(folder), OUTPUT)
            self.detected.set(f"{len(videos)} video{'s' if len(videos) != 1 else ''} detected")
            self._rescan()

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
        if (self.count.get() < 1 or self.minimum.get() < 1 or self.maximum.get() < self.minimum.get() or
                not 1 <= self.target.get() <= 10 or not 1 <= self.max_hours.get() <= 24 or
                not 1 <= self.batch_size.get() <= 4):
            messagebox.showerror("Invalid settings", "Check clip duration, target (1–10), session hours (1–24), and microbatch (1–4).")
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
        self.running = True
        self.progress.configure(value=0, maximum=self.target.get())
        self.select_button.configure(state="disabled")
        self.rescan_button.configure(state="disabled")
        self.retry_button.configure(state="disabled")
        self.reprocess_button.configure(state="disabled")
        self.start_button.configure(state="disabled")
        self.pause_button.configure(text="Pause", state="normal")
        self.cancel_button.configure(state="normal")
        if not transcription_enabled:
            self.model_status.set("Whisper unavailable · heuristic-only for this run · no download")
        settings = (self.model.get(), self.count.get(), self.minimum.get(), self.maximum.get(), self.encoder.get(), self.semantic.get())
        _write_json(PREFERENCES, {"last_folder": self.folder.get()})
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
                    self.start_button.configure(state="normal")
                    self.select_button.configure(state="normal")
                    self.rescan_button.configure(state="normal")
                    self.retry_button.configure(state="normal")
                    self.reprocess_button.configure(state="normal")
                    self.pause_button.configure(state="disabled", text="Pause")
                    self.cancel_button.configure(state="disabled")
                    session = value["session"]
                    counts = value["queue_counts"]
                    self.status.set(f"Nightly run {session['status'].lower()} · {value['clips_prepared']} Shorts · {value['episodes_processed']} episodes ({value['episodes_skipped']} skipped, {value['episodes_failed']} failed) · {counts.get('PENDING', 0)} pending")
                    self._log(self.status.get())
                    if self.close_when_finished:
                        self._close_store()
                        self.destroy()
                        return
                elif kind == "failed":
                    self.running = False
                    self.start_button.configure(state="normal")
                    self.select_button.configure(state="normal")
                    self.rescan_button.configure(state="normal")
                    self.retry_button.configure(state="normal")
                    self.reprocess_button.configure(state="normal")
                    self.pause_button.configure(state="disabled", text="Pause")
                    self.cancel_button.configure(state="disabled")
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
                                           cwd=APP_DIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
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
                "Clipper will stop after the current operation, keep completed clips, and offer to resume next time.",
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


if __name__ == "__main__":
    ClippingApp().mainloop()
