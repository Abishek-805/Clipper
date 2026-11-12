from __future__ import annotations

import csv
import os
import queue
import shutil
import subprocess
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from clipper import process_video, scan_videos

APP_DIR = Path(__file__).resolve().parent
OUTPUT = APP_DIR / "Shorts_Output"


class ClippingApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("AI Video Clipping Factory")
        self.geometry("760x620")
        self.minsize(680, 520)
        self.configure(bg="#10141d")
        self.folder = tk.StringVar()
        self.count = tk.IntVar(value=5)
        self.minimum = tk.IntVar(value=20)
        self.maximum = tk.IntVar(value=55)
        self.model = tk.StringVar(value="small")
        self.status = tk.StringVar(value="Select a folder containing videos to begin.")
        self.detected = tk.StringVar(value="0 videos detected")
        self.queue: queue.Queue = queue.Queue()
        self.cancel_event: threading.Event | None = None
        self.running = False
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
        controls = ttk.Frame(root)
        controls.pack(fill="x", pady=(0, 12))
        self.start_button = ttk.Button(controls, text="Start Automatic Clipping", style="Accent.TButton", command=self._start)
        self.start_button.pack(side="left")
        self.cancel_button = ttk.Button(controls, text="Cancel Processing", command=self._cancel, state="disabled")
        self.cancel_button.pack(side="left", padx=8)
        ttk.Button(controls, text="Open Output Folder", command=self._open_output).pack(side="right")
        self.progress = ttk.Progressbar(root, mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(4, 8))
        ttk.Label(root, textvariable=self.status, style="Sub.TLabel").pack(anchor="w", pady=(0, 9))
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
            videos = scan_videos(Path(folder), OUTPUT)
            self.detected.set(f"{len(videos)} video{'s' if len(videos) != 1 else ''} detected")

    def _start(self):
        if self.running:
            return
        if not self.folder.get() or not Path(self.folder.get()).is_dir():
            messagebox.showinfo("Choose a folder", "Select a folder containing your videos first.")
            return
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            messagebox.showerror("FFmpeg required", "Install FFmpeg and add ffmpeg.exe and ffprobe.exe to PATH, then restart the app.")
            return
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
        threading.Thread(target=self._worker, args=(videos,), daemon=True).start()

    def _worker(self, videos: list[Path]):
        rows = []
        done = 0
        OUTPUT.mkdir(parents=True, exist_ok=True)
        def log(message: str):
            with (OUTPUT / "processing.log").open("a", encoding="utf-8") as f:
                f.write(message.rstrip() + "\n")
            self.queue.put(("log", message))
        for path in videos:
            if self.cancel_event.is_set():
                break
            try:
                n = process_video(path, OUTPUT, self.model.get(), self.count.get(), self.minimum.get(), self.maximum.get(), self.cancel_event, log)
                done += n
                rows.append((path.name, n, "completed"))
            except InterruptedError:
                rows.append((path.name, 0, "cancelled"))
                break
            except Exception as exc:
                rows.append((path.name, 0, f"failed: {exc}"))
                log(f"ERROR · {path.name}: {exc}")
            self.queue.put(("progress", videos.index(path) + 1))
        OUTPUT.mkdir(parents=True, exist_ok=True)
        with (OUTPUT / "processing_report.csv").open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(("source", "clips_generated", "status"))
            writer.writerows(rows)
        self.queue.put(("finished", (done, len(videos))))

    def _poll(self):
        try:
            while True:
                kind, value = self.queue.get_nowait()
                if kind == "log":
                    self._log(value)
                elif kind == "progress":
                    self.progress.configure(value=value)
                elif kind == "finished":
                    self.running = False
                    self.start_button.configure(state="normal")
                    self.cancel_button.configure(state="disabled")
                    cancelled = self.cancel_event.is_set()
                    self.status.set(f"{'Cancelled' if cancelled else 'Finished'} · {value[0]} clips generated")
                    self._log(self.status.get())
        except queue.Empty:
            pass
        self.after(150, self._poll)

    def _cancel(self):
        if self.cancel_event:
            self.cancel_event.set()
            self.status.set("Cancelling after the current operation…")

    def _open_output(self):
        OUTPUT.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(str(OUTPUT))
        else:
            subprocess.Popen(["xdg-open", str(OUTPUT)])


if __name__ == "__main__":
    ClippingApp().mainloop()
