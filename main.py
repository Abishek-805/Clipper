from __future__ import annotations

import csv
import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from clipper import process_video, scan_videos
from diagnostics import collect_diagnostics
from model_manager import ModelManager
from vision import VisionModelManager

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
        self.diagnostics = collect_diagnostics()
        self.status = tk.StringVar(value="Select a folder containing videos to begin.")
        self.detected = tk.StringVar(value="0 videos detected")
        self.model_status = tk.StringVar(value="Whisper small · unloaded · CUDA available" if self.diagnostics.get("cuda_devices") else "Whisper small · unloaded · CPU int8 fallback")
        self.model.trace_add("write", lambda *_: self.model_status.set(f"Whisper {self.model.get()} · unloaded · load only after Start"))
        self.queue: queue.Queue = queue.Queue()
        self.cancel_event: threading.Event | None = None
        self.running = False
        self.close_when_finished = False
        self.resume_job: dict | None = None
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._build()
        self.after(150, self._poll)
        job = _read_json(INTERRUPTED_JOB)
        if isinstance(job, dict) and job.get("status") in ("running", "interrupted"):
            job["status"] = "interrupted"
            _write_json(INTERRUPTED_JOB, job)
            self.after_idle(lambda: self._offer_resume(job))

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
        ttk.Button(row, text="Select Video Folder", command=self._select).pack(side="left")
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
        controls = ttk.Frame(root)
        controls.pack(fill="x", pady=(0, 12))
        self.start_button = ttk.Button(controls, text="Start Automatic Clipping", style="Accent.TButton", command=self._start)
        self.start_button.pack(side="left")
        self.cancel_button = ttk.Button(controls, text="Cancel Processing", command=self._cancel, state="disabled")
        self.cancel_button.pack(side="left", padx=8)
        ttk.Button(controls, text="Open Output Folder", command=self._open_output).pack(side="right")
        ttk.Button(controls, text="Set Up Vision Model", command=self._setup_vision).pack(side="right", padx=8)
        self.progress = ttk.Progressbar(root, mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(4, 8))
        ttk.Label(root, textvariable=self.status, style="Sub.TLabel").pack(anchor="w", pady=(0, 9))
        ttk.Label(root, textvariable=self.model_status, style="Sub.TLabel").pack(anchor="w", pady=(0, 5))
        gpu = self.diagnostics.get("gpu_name") or "NVIDIA GPU not detected"
        cuda = "CUDA transcription available" if self.diagnostics.get("cuda_devices") else "CPU transcription fallback"
        encoders = self.diagnostics.get("encoders", {})
        render = "NVENC verified" if encoders.get("h264_nvenc") else "libx264 verified" if encoders.get("libx264") else "H.264 encoder unavailable"
        ttk.Label(root, text=f"Runtime · {gpu} · {cuda} · {render}", style="Sub.TLabel").pack(anchor="w", pady=(0, 8))
        ttk.Label(root, text="Processing log", style="Sub.TLabel").pack(anchor="w")
        self.log = tk.Text(root, bg="#0b0e14", fg="#c9d3e2", insertbackground="white", relief="flat", font=("Cascadia Mono", 9), wrap="word", height=14, state="disabled")
        self.log.pack(fill="both", expand=True, pady=(7, 0))

    def _log(self, text: str):
        self.log.configure(state="normal")
        self.log.insert("end", text.rstrip() + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _select(self):
        folder = filedialog.askdirectory(title="Select your video folder")
        if folder:
            self.folder.set(folder)
            _write_json(PREFERENCES, {"last_folder": folder})
            videos = scan_videos(Path(folder), OUTPUT)
            self.detected.set(f"{len(videos)} video{'s' if len(videos) != 1 else ''} detected")

    def _offer_resume(self, job: dict):
        folder_value = job.get("folder")
        videos = job.get("videos")
        if not isinstance(folder_value, str) or not isinstance(videos, list):
            return
        folder = Path(folder_value)
        if not folder.is_dir():
            return
        completed = {path for path in job.get("completed", []) if isinstance(path, str)} if isinstance(job.get("completed", []), list) else set()
        if not any(isinstance(path, str) and path not in completed and Path(path).is_file() for path in videos):
            INTERRUPTED_JOB.unlink(missing_ok=True)
            return
        job["videos"] = [path for path in videos if isinstance(path, str)]
        job["completed"] = sorted(completed)
        if messagebox.askyesno(
            "Interrupted job found",
            "An earlier clipping job did not finish. Prepare it to resume? Completed clips will be kept; click Start Automatic Clipping to continue.",
            parent=self,
        ):
            self.folder.set(str(folder))
            settings = job.get("settings", [])
            if len(settings) in (5, 6):
                self.model.set(settings[0])
                self.count.set(settings[1])
                self.minimum.set(settings[2])
                self.maximum.set(settings[3])
                self.encoder.set(settings[4])
                self.semantic.set(bool(settings[5]) if len(settings) > 5 else False)
            self.resume_job = job
            self.status.set("Interrupted job ready · click Start Automatic Clipping to resume")

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
        resume_job = self.resume_job
        if resume_job:
            completed_values = resume_job.get("completed", [])
            completed = {path for path in completed_values if isinstance(path, str)} if isinstance(completed_values, list) else set()
            videos = [Path(path) for path in resume_job.get("videos", []) if isinstance(path, str) and path not in completed and Path(path).is_file()]
        else:
            videos = scan_videos(Path(self.folder.get()), OUTPUT)
        if not videos:
            messagebox.showinfo("No videos found", "This folder contains no supported video files.")
            return
        if self.count.get() < 1 or self.minimum.get() < 1 or self.maximum.get() < self.minimum.get():
            messagebox.showerror("Invalid clip length", "Maximum seconds must be at least the minimum.")
            return
        self.running = True
        self.cancel_event = threading.Event()
        self.progress.configure(value=0, maximum=len(videos))
        self.start_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        settings = (self.model.get(), self.count.get(), self.minimum.get(), self.maximum.get(), self.encoder.get(), self.semantic.get())
        _write_json(PREFERENCES, {"last_folder": self.folder.get()})
        job = resume_job or {"folder": self.folder.get(), "videos": [str(path) for path in videos], "completed": [], "settings": settings}
        job["status"] = "running"
        _write_json(INTERRUPTED_JOB, job)
        self.resume_job = None
        threading.Thread(target=self._worker, args=(videos, settings, job), daemon=True).start()

    def _worker(self, videos: list[Path], settings: tuple, job: dict):
        rows = []
        done = 0
        completed = set(job.get("completed", []))
        OUTPUT.mkdir(parents=True, exist_ok=True)
        def log(message: str):
            with (OUTPUT / "processing.log").open("a", encoding="utf-8") as f:
                f.write(message.rstrip() + "\n")
            self.queue.put(("log", message))
        def progress(message: str):
            log(message)
            self.queue.put(("status", message))
        manager = ModelManager(settings[0], status_callback=lambda value: self.queue.put(("model_status", value)))
        vision = VisionModelManager(status_callback=lambda value: self.queue.put(("vision_status", value))) if len(settings) > 5 and settings[5] else None
        try:
            for path in videos:
                if self.cancel_event.is_set():
                    break
                try:
                    n = process_video(path, OUTPUT, *settings[:4], self.cancel_event, progress, settings[4], manager,
                                      bool(settings[5]) if len(settings) > 5 else False, vision)
                    done += n
                    rows.append((path.name, n, "completed"))
                    completed.add(str(path))
                    job["completed"] = sorted(completed)
                    _write_json(INTERRUPTED_JOB, job)
                except InterruptedError:
                    rows.append((path.name, 0, "cancelled"))
                    log(f"Cancelled · {path.name}")
                    break
                except Exception as exc:
                    rows.append((path.name, 0, f"failed: {exc}"))
                    log(f"ERROR · {path.name}: {exc}")
                self.queue.put(("progress", videos.index(path) + 1))
        finally:
            manager.release_whisper()
            if vision:
                vision.release()
        OUTPUT.mkdir(parents=True, exist_ok=True)
        with (OUTPUT / "processing_report.csv").open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(("source", "clips_generated", "status"))
            writer.writerows(rows)
        interrupted = self.cancel_event.is_set() or any(status != "completed" for _, _, status in rows)
        if interrupted:
            job["status"] = "interrupted"
            job["completed"] = sorted(completed)
            _write_json(INTERRUPTED_JOB, job)
        else:
            INTERRUPTED_JOB.unlink(missing_ok=True)
        self.queue.put(("finished", (done, len(videos), interrupted)))

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
                elif kind == "model_status":
                    device = f"{value.get('device')} / {value.get('compute_type')}"
                    gpu = value.get("vram_after_load") or value.get("vram_before") or {}
                    memory = f" · GPU memory {gpu.get('free_mib')} / {gpu.get('total_mib')} MiB free" if gpu else ""
                    fallback = f" · {value['fallback_reason']}" if value.get("fallback_reason") else ""
                    self.model_status.set(f"Whisper {value.get('model')} · {value.get('state')} · {device}{memory}{fallback}")
                elif kind == "vision_status":
                    gpu = value.get("vram") or value.get("vram_after_load") or {}
                    memory = f" · GPU memory {gpu.get('free_mib')} / {gpu.get('total_mib')} MiB free" if gpu else ""
                    self.model_status.set(f"Vision {value.get('model')} · {value.get('state')} · {value.get('device', 'unloaded')}{memory}")
                elif kind == "finished":
                    self.running = False
                    self.start_button.configure(state="normal")
                    self.cancel_button.configure(state="disabled")
                    self.status.set(f"{'Interrupted; resume requires confirmation' if value[2] else 'Finished'} · {value[0]} clips generated")
                    self._log(self.status.get())
                    if self.close_when_finished:
                        self.destroy()
                        return
                    elif value[2]:
                        job = _read_json(INTERRUPTED_JOB)
                        if isinstance(job, dict):
                            self._offer_resume(job)
        except queue.Empty:
            pass
        self.after(150, self._poll)

    def _cancel(self):
        if self.cancel_event:
            self.cancel_event.set()
            self.status.set("Cancelling after the current operation…")

    def _setup_vision(self):
        if not messagebox.askyesno("Download local vision model?",
                                   "This downloads the pinned SmolVLM2 model files from Hugging Face. Optional Python packages and PyTorch must be installed separately. Continue?",
                                   parent=self):
            return
        self.status.set("Downloading the explicitly selected local vision model…")
        def download():
            try:
                result = subprocess.run([sys.executable, str(APP_DIR / "benchmark_vision.py"), "--allow-model-download"],
                                        cwd=APP_DIR, capture_output=True, text=True)
                self.queue.put(("log", result.stdout or result.stderr or "Vision model setup finished."))
                self.queue.put(("status", "Vision model setup finished." if result.returncode == 0 else "Vision model setup failed; see processing log."))
            except Exception as exc:
                self.queue.put(("status", f"Vision model setup failed: {exc}"))
        threading.Thread(target=download, daemon=True).start()

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
        self.destroy()

    def _open_output(self):
        OUTPUT.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(str(OUTPUT))
        else:
            subprocess.Popen(["xdg-open", str(OUTPUT)])


if __name__ == "__main__":
    ClippingApp().mainloop()
