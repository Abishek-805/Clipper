"""Human-only, two-pass local episode review window."""
from __future__ import annotations

import tkinter as tk
import time
import uuid
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from time import monotonic

from .coverage import CoverageSample, CoverageTracker
from .player import LibVLCPlayer, probe_media
from .store import CATEGORIES, RATINGS, ReviewStore

APP_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = APP_ROOT / "Shorts_Output"
POLL_MS = 250
QUICK_CATEGORIES = ("Unclassified", "Important dialogue", "Memorable phrase", "Fight/action",
                    "Plot revelation", "Emotional moment", "Comedy", "Suspense",
                    "Dramatic reaction", "Other", "Negative example")


def format_timestamp(milliseconds: int) -> str:
    if milliseconds < 0:
        return "--:--:--.---"
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02}.{millis:03}"


def parse_timestamp(value: str) -> int:
    value = value.strip()
    try:
        parts = value.split(":")
        if len(parts) == 3:
            hours, minutes, seconds = parts
        elif len(parts) == 2:
            hours, minutes, seconds = "0", *parts
        else:
            hours, minutes, seconds = "0", "0", parts[0]
        total = (int(hours) * 3600 + int(minutes) * 60 + float(seconds)) * 1000
        if total < 0 or total != int(total):
            raise ValueError
        return int(total)
    except (ValueError, TypeError) as exc:
        raise ValueError("Use seconds, MM:SS.mmm, or HH:MM:SS.mmm") from exc


def _short_lang(text: str | None) -> str:
    return (text or "").strip().casefold()


class ReviewWindow:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Clipper · Human Episode Review")
        self.root.geometry("1160x830")
        self.root.minsize(900, 660)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.player: LibVLCPlayer | None = None
        self.store: ReviewStore | None = None
        self.source: dict | None = None
        self.tracker = CoverageTracker()
        self.active_start: int | None = None
        self.selected_moment_id: str | None = None
        self._last_replay: tuple[int, int] | None = None
        self._replay_end: int | None = None
        self._last_save = monotonic()
        self._last_tracks_refresh = 0.0
        self._loading = False
        self._timeline_drag = False
        self._build()
        self._bind_shortcuts()
        self.root.after(POLL_MS, self._poll)

    def _build(self) -> None:
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("Title.TLabel", font=("Segoe UI", 19, "bold"))
        style.configure("Meta.TLabel", foreground="#4e6172")
        outer = ttk.Frame(self.root, padding=14)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Human episode review", style="Title.TLabel").pack(anchor="w")
        ttk.Label(outer, text="Local only · blind discovery · original media stays untouched", style="Meta.TLabel").pack(anchor="w", pady=(2, 10))

        source_row = ttk.Frame(outer)
        source_row.pack(fill="x", pady=(0, 8))
        ttk.Button(source_row, text="Choose Episode…", command=self.choose_source).pack(side="left")
        self.source_label = ttk.Label(source_row, text="No episode selected", style="Meta.TLabel")
        self.source_label.pack(side="left", padx=10, fill="x", expand=True)
        self.phase_var = tk.StringVar(value="Pass 1 · blind discovery")
        self.coverage_var = tk.StringVar(value="Coverage 0.0%")
        ttk.Label(source_row, textvariable=self.phase_var).pack(side="right", padx=12)
        ttk.Label(source_row, textvariable=self.coverage_var).pack(side="right")

        self.video_frame = tk.Frame(outer, bg="#111820", height=410, width=720, takefocus=False)
        self.video_frame.pack(fill="both", expand=True)
        self.video_frame.pack_propagate(False)

        controls = ttk.Frame(outer, padding=(0, 8))
        controls.pack(fill="x")
        self.play_button = ttk.Button(controls, text="Play", command=self.toggle_play, state="disabled")
        self.play_button.pack(side="left")
        ttk.Button(controls, text="−5s", command=lambda: self.seek_relative(-5000)).pack(side="left", padx=(6, 2))
        ttk.Button(controls, text="+5s", command=lambda: self.seek_relative(5000)).pack(side="left", padx=2)
        self.time_var = tk.StringVar(value="--:--:--.--- / --:--:--.---")
        ttk.Label(controls, textvariable=self.time_var, font=("Consolas", 11)).pack(side="left", padx=12)
        self.seek_entry = ttk.Entry(controls, width=15)
        self.seek_entry.insert(0, "00:00:00.000")
        self.seek_entry.pack(side="left")
        ttk.Button(controls, text="Go", command=self.seek_typed).pack(side="left", padx=4)
        self.audio_var = tk.StringVar(value="Load video to enumerate tracks")
        self.audio_combo = ttk.Combobox(controls, textvariable=self.audio_var, values=(), width=27, state="disabled")
        self.audio_combo.pack(side="right", padx=(6, 0))
        ttk.Label(controls, text="Audio").pack(side="right")
        self.audio_combo.bind("<<ComboboxSelected>>", self._audio_selected)

        self.subtitle_var = tk.StringVar(value="Off")
        self.subtitle_combo = ttk.Combobox(controls, textvariable=self.subtitle_var, values=("Off",), width=20, state="disabled")
        self.subtitle_combo.pack(side="right", padx=(6, 0))
        ttk.Label(controls, text="Source subtitles").pack(side="right")
        self.subtitle_combo.bind("<<ComboboxSelected>>", self._subtitle_selected)

        self.timeline = ttk.Scale(outer, from_=0, to=1, orient="horizontal")
        self.timeline.pack(fill="x")
        self.timeline.bind("<ButtonPress-1>", lambda _event: setattr(self, "_timeline_drag", True))
        self.timeline.bind("<ButtonRelease-1>", self._timeline_release)
        self.timeline.configure(state="disabled")

        self.coverage_detail = tk.StringVar(value="Unwatched: full episode")
        ttk.Label(outer, textvariable=self.coverage_detail, style="Meta.TLabel").pack(anchor="w", pady=(3, 8))

        self.pass1_frame = ttk.LabelFrame(outer, text=" Pass 1 · quick markers ", padding=8)
        self.pass1_frame.pack(fill="x", pady=(0, 8))
        self.mark_status = tk.StringVar(value="I start · O end · M point · R replay last · Space play/pause")
        ttk.Label(self.pass1_frame, textvariable=self.mark_status, style="Meta.TLabel").pack(side="left", padx=(0, 12))
        self.quick_category = tk.StringVar(value="Unclassified")
        ttk.Combobox(self.pass1_frame, textvariable=self.quick_category, values=QUICK_CATEGORIES,
                     width=23, state="readonly").pack(side="left")
        self.quick_note = tk.StringVar()
        quick_entry = ttk.Entry(self.pass1_frame, textvariable=self.quick_note)
        quick_entry.pack(side="left", fill="x", expand=True, padx=6)
        self.finish_pass_button = ttk.Button(self.pass1_frame, text="Finish Pass 1", command=self.finish_pass1, state="disabled")
        self.finish_pass_button.pack(side="right")
        self.play_gap_button = ttk.Button(self.pass1_frame, text="Play next gap", command=self.play_next_gap, state="disabled")
        self.play_gap_button.pack(side="right", padx=(0, 6))

        self.pass2_frame = ttk.LabelFrame(outer, text=" Pass 2 · refine your moments ", padding=8)
        self.pass2_frame.pack(fill="both", expand=True)
        self._build_pass2()
        self._set_pass2_enabled(False)
        self.pass2_frame.pack_forget()
        self.status_var = tk.StringVar(value="Choose one episode to begin.")
        ttk.Label(outer, textvariable=self.status_var, style="Meta.TLabel").pack(anchor="w", pady=(8, 0))

    def _build_pass2(self) -> None:
        body = ttk.Frame(self.pass2_frame)
        body.pack(fill="both", expand=True)
        left = ttk.Frame(body)
        left.pack(side="left", fill="y", padx=(0, 10))
        self.moment_list = tk.Listbox(left, width=32, height=10, exportselection=False)
        self.moment_list.pack(fill="y", expand=True)
        self.moment_list.bind("<<ListboxSelect>>", self._select_moment)
        ttk.Button(left, text="Export JSON…", command=self.export_json).pack(fill="x", pady=(8, 0))

        form = ttk.Frame(body)
        form.pack(side="left", fill="both", expand=True)
        self.form_vars = {}
        fields = (
            ("refined_start_ms", "Refined start"), ("refined_end_ms", "Refined end"),
            ("category", "Category"), ("decision", "Decision"), ("final_rank", "Final rank 0–2"),
            ("quote", "Exact dialogue"), ("quote_start_ms", "Quote start"), ("quote_end_ms", "Quote end"),
            ("context_before", "Context before"), ("context_after", "Context after"),
            ("event_description", "What happened"), ("setup", "Setup"), ("escalation", "Escalation"),
            ("payoff", "Payoff / reaction"), ("why_it_matters", "Why it matters"),
            ("missing_context", "Missing context"), ("rejection_reason", "Rejection reason"),
        )
        for row, (key, label) in enumerate(fields):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=2)
            var = tk.StringVar()
            self.form_vars[key] = var
            if key == "category":
                widget = ttk.Combobox(form, textvariable=var, values=QUICK_CATEGORIES, state="readonly", width=25)
            elif key == "decision":
                widget = ttk.Combobox(form, textvariable=var, values=("", "keep", "maybe", "reject"), state="readonly", width=25)
            elif key == "final_rank":
                widget = ttk.Spinbox(form, textvariable=var, from_=0, to=2, width=7)
            else:
                widget = ttk.Entry(form, textvariable=var, width=45)
            widget.grid(row=row, column=1, sticky="ew", pady=2)
            var.trace_add("write", lambda *_args, field=key: self._save_form_change(field))
        ratings_start = len(fields)
        ttk.Label(form, text="Ratings (1–5)", font=("Segoe UI", 9, "bold")).grid(row=ratings_start, column=0, sticky="w", pady=(8, 2))
        self.rating_vars = {}
        for offset, name in enumerate(sorted(RATINGS), 1):
            var = tk.StringVar()
            self.rating_vars[name] = var
            ttk.Label(form, text=name.replace("_", " ")).grid(row=ratings_start + offset, column=0, sticky="w", padx=(0, 8), pady=1)
            spin = ttk.Spinbox(form, textvariable=var, from_=1, to=5, width=7)
            spin.grid(row=ratings_start + offset, column=1, sticky="w", pady=1)
            var.trace_add("write", lambda *_args: self._save_form_change("ratings"))
        form.columnconfigure(1, weight=1)

    def _bind_shortcuts(self) -> None:
        self.root.bind_all("<KeyPress>", self._shortcut, add="+")

    def _shortcut(self, event):
        if self.store is None or self.store.get("phase") != "pass1":
            return
        if isinstance(event.widget, (tk.Entry, tk.Text, ttk.Entry, ttk.Combobox, ttk.Spinbox)):
            return
        key = event.keysym.casefold()
        actions = {"i": self.mark_start, "o": self.mark_end, "m": self.mark_point,
                   "r": self.replay_last, "space": self.toggle_play}
        if key in actions:
            actions[key]()
            return "break"
        if key == "left":
            self.seek_relative(-5000)
            return "break"
        if key == "right":
            self.seek_relative(5000)
            return "break"

    def choose_source(self) -> None:
        source = filedialog.askopenfilename(
            parent=self.root, title="Choose one episode",
            filetypes=(("Video files", "*.mkv *.mp4 *.mov *.avi *.webm *.m4v"), ("All files", "*.*")),
        )
        if not source:
            return
        try:
            info = probe_media(source)
            self._close_media()
            self.source = info
            self.source_label.configure(text=f"{info['filename']} · {format_timestamp(info['duration_ms'])} · {info['width']}×{info['height']} · {info['video_codec']}")
            fingerprint_dir = OUTPUT_ROOT / f"Task06I_{info['fingerprint'][:12]}"
            self.store = ReviewStore(fingerprint_dir / "human_review.sqlite3", info)
            self.store.set("player_runtime", self.player.version if self.player else None)
            self.tracker = CoverageTracker(self.store.coverage())
            self.active_start = self.store.get("active_start_ms")
            self._build_player(source)
            self._refresh_moments()
            self._restore_phase()
            streams = [f"{row['type']} {row.get('language') or 'language unknown'} · {row.get('codec')}" for row in info["streams"]]
            self.status_var.set(" · ".join(streams) or "Source metadata loaded")
            self.timeline.configure(to=info["duration_ms"], state="normal")
            self.play_button.configure(state="normal")
        except Exception as exc:
            self.status_var.set(f"Could not open episode: {exc}")
            messagebox.showerror("Episode could not be opened", str(exc), parent=self.root)

    def _build_player(self, source: str) -> None:
        self.root.update_idletasks()
        if self.player is None:
            self.player = LibVLCPlayer(self.video_frame.winfo_id())
        self.player.open(source)
        if self.store:
            self.store.set("player_runtime", self.player.version)
        self.audio_var.set("Choose audio track after Play")
        self.audio_combo.configure(values=(), state="disabled")
        self.subtitle_var.set("Off")
        self.subtitle_combo.configure(values=("Off",), state="disabled")
        self._audio_ids = {}
        self._subtitle_ids = {"Off": -1}
        self.tracker = CoverageTracker(self.store.coverage())

    def toggle_play(self) -> None:
        if self.player is None:
            return
        if self.player.state() == "playing":
            self.player.pause()
            self.tracker.break_continuity("paused")
            self._save_coverage()
            self.play_button.configure(text="Play")
        else:
            current = self.player.time_ms()
            if current >= 0:
                self.tracker.seed(CoverageSample(current, monotonic(), "playing", 1.0))
            self.player.play()
            self.play_button.configure(text="Pause")

    def seek_relative(self, delta: int) -> None:
        if self.player is None:
            return
        current = self.player.time_ms()
        if current < 0:
            return
        self._seek(max(0, min(self.player.length_ms(), current + delta)))

    def seek_typed(self) -> None:
        if self.player is None:
            return
        try:
            target = parse_timestamp(self.seek_entry.get())
            duration = self.player.length_ms() if self.player.length_ms() > 0 else self.source["duration_ms"]
            if target > duration:
                raise ValueError("Timestamp is beyond the source duration")
            self._seek(target)
        except (ValueError, RuntimeError) as exc:
            messagebox.showerror("Invalid timestamp", str(exc), parent=self.root)

    def _timeline_release(self, _event=None) -> None:
        self._timeline_drag = False
        if self.player and self.store and self.timeline.instate(["!disabled"]):
            try:
                self._seek(int(float(self.timeline.get())))
            except RuntimeError as exc:
                self.status_var.set(f"Seek failed: {exc}")

    def _seek(self, target: int) -> None:
        if not self.player:
            return
        was_playing = self.player.state() == "playing"
        self.tracker.break_continuity("seek")
        self._save_coverage()
        self.seek_entry.delete(0, "end")
        self.seek_entry.insert(0, format_timestamp(target))
        if not was_playing or self.player.length_ms() <= 0:
            self.player.play()
            self.root.after(250, lambda: self._seek_when_ready(target, was_playing, 0))
            return
        self._perform_seek(target, was_playing)

    def _seek_when_ready(self, target: int, was_playing: bool, attempts: int) -> None:
        if not self.player:
            return
        if self.player.length_ms() > 0 and self.player.state() == "playing":
            self._perform_seek(target, was_playing)
        elif attempts < 12:
            self.root.after(250, lambda: self._seek_when_ready(target, was_playing, attempts + 1))
        else:
            self.status_var.set("Seek failed: LibVLC did not finish initializing this source")

    def _perform_seek(self, target: int, was_playing: bool) -> None:
        if not self.player:
            return
        try:
            self.player.seek(target)
        except RuntimeError as exc:
            self.status_var.set(f"Seek failed: {exc}")
            return
        if not was_playing:
            self.root.after(250, self._pause_after_seek)
        else:
            current = self.player.time_ms()
            if current >= 0:
                self.tracker.seed(CoverageSample(current, monotonic(), "playing", 1.0))
            self.play_button.configure(text="Pause")

    def _pause_after_seek(self) -> None:
        if self.player and self.player.state() == "playing":
            self.player.pause()
        self.play_button.configure(text="Play")

    def mark_start(self) -> None:
        now = self._current_time()
        if now is None or not self._require_pass1():
            return
        self.active_start = now
        self.store.set("active_start_ms", now)
        self.mark_status.set(f"Start marked · {format_timestamp(now)} · press O at the event end")

    def mark_end(self) -> None:
        now = self._current_time()
        if now is None or not self._require_pass1():
            return
        if self.active_start is None:
            self.mark_status.set("Press I before O, or use M for a point marker")
            return
        if now <= self.active_start:
            self.mark_status.set("End must be after start; keep watching, then press O")
            return
        row = self._new_moment("interval", self.active_start, now)
        self.store.save_moment(row)
        self._last_replay = (self.active_start, now)
        self.active_start = None
        self.store.set("active_start_ms", None)
        self.quick_note.set("")
        self._refresh_moments()
        self.mark_status.set(f"Moment saved · {format_timestamp(now)} · continue watching")

    def mark_point(self) -> None:
        now = self._current_time()
        if now is None or not self._require_pass1():
            return
        row = self._new_moment("point", now, now)
        self.store.save_moment(row)
        self._refresh_moments()
        self.mark_status.set(f"Point marked · {format_timestamp(now)}")
        self.quick_note.set("")

    def _new_moment(self, kind: str, start: int, end: int) -> dict:
        return {
            "moment_id": uuid.uuid4().hex,
            "kind": kind,
            "rough_start_ms": start,
            "rough_end_ms": end,
            "refined_start_ms": start if kind == "interval" else None,
            "refined_end_ms": end if kind == "interval" else None,
            "category": self.quick_category.get() or "Unclassified",
            "quick_note": self.quick_note.get(),
            "quote": "", "quote_start_ms": None, "quote_end_ms": None,
            "context_before": "", "context_after": "", "event_description": "",
            "setup": "", "escalation": "", "payoff": "", "why_it_matters": "",
            "missing_context": "", "decision": None, "rejection_reason": "",
            "ratings": {}, "final_rank": None,
            "created_at": time.time(), "updated_at": time.time(),
        }

    def replay_last(self) -> None:
        if not self.player or not self._last_replay:
            moments = self.store.moments() if self.store else []
            if moments:
                last = moments[-1]
                self._last_replay = (last["rough_start_ms"], last["rough_end_ms"])
        if not self.player or not self._last_replay:
            return
        start, end = self._last_replay
        self._replay_end = end if end > start else None
        self._seek(start)
        current = self.player.time_ms()
        if current >= 0:
            self.tracker.seed(CoverageSample(current, monotonic(), "playing", 1.0))
        self.player.play()
        self.play_button.configure(text="Pause")
        self.tracker.reset_baseline()

    def finish_pass1(self) -> None:
        if not self.store or not self.source:
            return
        self._save_coverage()
        gaps = self.tracker.gaps(self.source["duration_ms"])
        if gaps:
            first = gaps[0]
            self.mark_status.set(f"Replay uncovered gap {format_timestamp(first[0])}–{format_timestamp(first[1])}")
            return
        self.store.set("pass1_complete", True)
        self.store.set("phase", "pass2")
        self._restore_phase()
        self.status_var.set("Pass 1 complete. Pass 2 contains only your own marked moments.")

    def play_next_gap(self) -> None:
        if not self.store or not self.source:
            return
        gaps = self.tracker.gaps(self.source["duration_ms"])
        if not gaps:
            self.finish_pass1()
            return
        start, end = gaps[0]
        self._replay_end = end
        self._seek(start)
        current = self.player.time_ms()
        if current >= 0:
            self.tracker.seed(CoverageSample(current, monotonic(), "playing", 1.0))
        self.player.play()
        self.tracker.reset_baseline()
        self.play_button.configure(text="Pause")
        self.status_var.set(f"Reviewing uncovered interval {format_timestamp(start)}–{format_timestamp(end)}")

    def _refresh_moments(self) -> None:
        if not self.store:
            return
        self._loading = True
        self.moment_list.delete(0, "end")
        self._moment_rows = self.store.moments()
        selected_index = None
        for i, row in enumerate(self._moment_rows):
            kind = "Point" if row["kind"] == "point" else "Moment"
            label = f"{kind} {format_timestamp(row['rough_start_ms'])}–{format_timestamp(row['rough_end_ms'])} · {row['category']}"
            self.moment_list.insert("end", label)
            if row["moment_id"] == self.selected_moment_id:
                selected_index = i
        if selected_index is not None:
            self.moment_list.selection_set(selected_index)
        self._loading = False

    def _select_moment(self, _event=None) -> None:
        selection = self.moment_list.curselection()
        if not selection or not self.store:
            return
        self._persist_form()
        row = self._moment_rows[selection[0]]
        self.selected_moment_id = row["moment_id"]
        self._loading = True
        for key, var in self.form_vars.items():
            value = row.get(key, "")
            if key in ("refined_start_ms", "refined_end_ms", "quote_start_ms", "quote_end_ms"):
                value = "" if value is None else format_timestamp(value)
            elif key == "final_rank":
                value = 0 if value is None else value
            var.set(value if value is not None else "")
        for name, var in self.rating_vars.items():
            var.set(row.get("ratings", {}).get(name, ""))
        self._loading = False

    def _save_form_change(self, field: str) -> None:
        if self._loading or not self.selected_moment_id or not self.store:
            return
        self._persist_form()

    def _persist_form(self) -> None:
        if self._loading or not self.selected_moment_id or not self.store:
            return
        row = next((item for item in self._moment_rows if item["moment_id"] == self.selected_moment_id), None)
        if row is None:
            return
        try:
            for key in ("refined_start_ms", "refined_end_ms", "quote_start_ms", "quote_end_ms"):
                value = self.form_vars[key].get().strip()
                row[key] = parse_timestamp(value) if value else None
            row["category"] = self.form_vars["category"].get() or "Unclassified"
            row["decision"] = self.form_vars["decision"].get() or None
            rank = int(self.form_vars["final_rank"].get() or 0)
            row["final_rank"] = rank or None
            for key in ("quote", "context_before", "context_after", "event_description", "setup", "escalation", "payoff", "why_it_matters", "missing_context", "rejection_reason"):
                row[key] = self.form_vars[key].get()
            ratings = {}
            for name, var in self.rating_vars.items():
                value = var.get().strip()
                if value:
                    ratings[name] = int(value)
            row["ratings"] = ratings
            row["updated_at"] = time.time()
            self.store.save_moment(row)
            self.status_var.set("Saved locally")
        except (ValueError, TypeError):
            self.status_var.set("Uncommitted edit: check timestamp, rating, or rank")

    def _restore_phase(self) -> None:
        if not self.store:
            return
        phase = self.store.get("phase", "pass1")
        complete = bool(self.store.get("pass1_complete", False))
        if phase == "pass2" and complete:
            self.phase_var.set("Pass 2 · refine your moments")
            self.pass1_frame.pack_forget()
            self.pass2_frame.pack(fill="both", expand=True)
            self._set_pass2_enabled(True)
        else:
            self.phase_var.set("Pass 1 · blind discovery")
            self.pass2_frame.pack_forget()
            self.pass1_frame.pack(fill="x", pady=(0, 8))
            self.finish_pass_button.configure(state="normal" if self.tracker.complete(self.source["duration_ms"]) else "disabled")
            self._set_pass2_enabled(False)
        self._update_coverage()

    def _set_pass2_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for widget in self.pass2_frame.winfo_children():
            self._set_descendant_state(widget, state)

    def _set_descendant_state(self, widget, state: str) -> None:
        try:
            widget.configure(state=state)
        except tk.TclError:
            pass
        for child in widget.winfo_children():
            self._set_descendant_state(child, state)

    def export_json(self) -> None:
        if not self.store:
            return
        target = filedialog.asksaveasfilename(parent=self.root, title="Export human reference",
                                              defaultextension=".json", filetypes=(("JSON", "*.json"),),
                                              initialfile="human_reference.json")
        if not target:
            return
        try:
            self._persist_form()
            self.store.export_json(target)
            self.status_var.set(f"Validated JSON exported: {target}")
        except Exception as exc:
            messagebox.showerror("Export failed", str(exc), parent=self.root)

    def _current_time(self) -> int | None:
        if not self.player:
            return None
        value = self.player.time_ms()
        if value < 0:
            self.status_var.set("LibVLC has no valid source time; marker not saved")
            return None
        if self.source and value > self.source["duration_ms"]:
            self.status_var.set("LibVLC returned a time beyond source duration; marker not saved")
            return None
        return value

    def _require_pass1(self) -> bool:
        if not self.store or self.store.get("phase") != "pass1":
            return False
        return True

    def _audio_selected(self, _event=None) -> None:
        if not self.player:
            return
        track_id = self._audio_ids.get(self.audio_var.get())
        if track_id is not None and not self.player.select_audio_track(track_id):
            self.status_var.set("LibVLC could not select that audio track")
        elif track_id is not None and self.store:
            self.store.set("selected_audio_track", {"id": track_id, "label": self.audio_var.get()})

    def _subtitle_selected(self, _event=None) -> None:
        if not self.player:
            return
        track_id = self._subtitle_ids.get(self.subtitle_var.get())
        if track_id is not None:
            self.player.select_subtitle_track(track_id)

    def _refresh_tracks(self) -> None:
        if not self.player:
            return
        now = monotonic()
        if now - self._last_tracks_refresh < 1:
            return
        self._last_tracks_refresh = now
        audio_tracks = self.player.audio_tracks()
        if audio_tracks:
            values = [f"{track_id}: {name}" for track_id, name in audio_tracks]
            if values != list(self.audio_combo.cget("values")):
                self._audio_ids = {label: track_id for label, (track_id, _name) in zip(values, audio_tracks)}
                self.audio_combo.configure(values=values, state="readonly")
                english = [label for label in values if any(word in label.casefold() for word in ("english", "eng"))]
                if len(english) == 1:
                    self.audio_var.set(english[0])
                    self._audio_selected()
                elif len(audio_tracks) == 1:
                    self.audio_var.set(values[0])
                    self._audio_selected()
                else:
                    self.audio_var.set("Select verified audio track")
        subtitles = self.player.subtitle_tracks()
        values = ["Off", *(f"{track_id}: {name}" for track_id, name in subtitles)]
        if values != list(self.subtitle_combo.cget("values")):
            self._subtitle_ids = {"Off": -1, **{label: track_id for label, (track_id, _name) in zip(values[1:], subtitles)}}
            self.subtitle_combo.configure(values=values, state="readonly")

    def _poll(self) -> None:
        try:
            if self.player and self.source:
                self._refresh_tracks()
                current = self.player.time_ms()
                duration = self.player.length_ms()
                if current >= 0:
                    self.time_var.set(f"{format_timestamp(current)} / {format_timestamp(duration if duration > 0 else self.source['duration_ms'])}")
                    self.timeline.configure(to=max(1, duration if duration > 0 else self.source["duration_ms"]))
                    if not self._timeline_drag:
                        self.timeline.set(current)
                    if self.root.focus_get() is not self.seek_entry:
                        self.seek_entry.delete(0, "end")
                        self.seek_entry.insert(0, format_timestamp(current))
                state = self.player.state()
                mono = monotonic()
                if state in {"playing", "ended"} and current >= 0:
                    sample = CoverageSample(current, mono, state, 1.0)
                    self.tracker.sample(sample)
                    if self._replay_end is not None and current >= self._replay_end:
                        self.player.pause()
                        self._replay_end = None
                        self.tracker.break_continuity("replay_boundary")
                        self.play_button.configure(text="Play")
                else:
                    self.tracker.break_continuity("paused_or_not_playing")
                    self.play_button.configure(text="Play")
                if mono - self._last_save >= 1:
                    self._save_coverage()
                    self._last_save = mono
                self._update_coverage()
        except Exception as exc:
            self.status_var.set(f"Playback update error: {exc}")
        finally:
            self.root.after(POLL_MS, self._poll)

    def _save_coverage(self) -> None:
        if self.store:
            self.store.save_coverage(self.tracker.intervals)

    def _update_coverage(self) -> None:
        if not self.source:
            return
        duration = self.source["duration_ms"]
        self.coverage_var.set(f"Coverage {self.tracker.percent(duration):.1f}%")
        gaps = self.tracker.gaps(duration)
        if gaps:
            sample = gaps[:3]
            self.coverage_detail.set("Unwatched: " + ", ".join(f"{format_timestamp(a)}–{format_timestamp(b)}" for a, b in sample) + (" …" if len(gaps) > 3 else ""))
        else:
            self.coverage_detail.set("Full source covered by continuous playback samples")
        if self.store and self.store.get("phase") == "pass1":
            self.finish_pass_button.configure(state="normal" if not gaps else "disabled")
            self.play_gap_button.configure(state="normal" if gaps else "disabled")

    def close(self) -> None:
        self._save_coverage()
        self._persist_form()
        self._close_media()
        self.root.destroy()

    def _close_media(self) -> None:
        if self.player:
            self.player.close()
            self.player = None
        if self.store:
            self.store.close()
            self.store = None


def launch() -> None:
    root = tk.Tk()
    ReviewWindow(root)
    root.mainloop()
