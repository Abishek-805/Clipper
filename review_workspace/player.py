"""Lazy LibVLC setup and source metadata probing for the review workspace."""
from __future__ import annotations

import importlib
import importlib.util
import json
import os
import platform
import struct
import subprocess
import sys
from pathlib import Path
from typing import Any

from .store import source_fingerprint

_DLL_DIRECTORY_HANDLES: list[Any] = []


def _vlc_directory() -> Path:
    candidates = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "VideoLAN" / "VLC",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "VideoLAN" / "VLC",
    ]
    for candidate in candidates:
        if (candidate / "libvlc.dll").is_file() and (candidate / "plugins").is_dir():
            return candidate
    raise RuntimeError("A VLC desktop installation with libvlc.dll and plugins was not found.")


def _pe_machine(path: Path) -> int:
    with path.open("rb") as stream:
        if stream.read(2) != b"MZ":
            raise RuntimeError(f"Not a Windows executable: {path}")
        stream.seek(0x3C)
        offset = struct.unpack("<I", stream.read(4))[0]
        stream.seek(offset)
        if stream.read(4) != b"PE\0\0":
            raise RuntimeError(f"Invalid Windows executable header: {path}")
        return struct.unpack("<H", stream.read(2))[0]


def load_vlc_module():
    """Load python-vlc only on demand, with app-local native DLL discovery."""
    if importlib.util.find_spec("vlc") is None:
        raise RuntimeError("The optional player binding is missing. Install it with: python -m pip install -r requirements-review.txt")
    directory = _vlc_directory()
    expected_machine = 0x8664 if struct.calcsize("P") == 8 else 0x14C
    actual_machine = _pe_machine(directory / "libvlc.dll")
    if actual_machine != expected_machine:
        raise RuntimeError(f"Python ({platform.architecture()[0]}) and VLC DLL architectures do not match.")
    if hasattr(os, "add_dll_directory"):
        _DLL_DIRECTORY_HANDLES.append(os.add_dll_directory(str(directory)))
    os.environ["VLC_PLUGIN_PATH"] = str(directory / "plugins")
    os.environ["PYTHON_VLC_LIB_PATH"] = str(directory / "libvlc.dll")
    os.environ["PYTHON_VLC_MODULE_PATH"] = str(directory / "plugins")
    try:
        vlc = importlib.import_module("vlc")
        version = vlc.libvlc_get_version()
    except ModuleNotFoundError as exc:
        if exc.name == "vlc":
            raise RuntimeError("The optional player binding is missing. Install it with: python -m pip install -r requirements-review.txt") from exc
        raise
    except (OSError, AttributeError) as exc:
        raise RuntimeError(f"Could not initialize native LibVLC from {directory}: {exc}") from exc
    if not version:
        raise RuntimeError("LibVLC did not report a version.")
    if isinstance(version, bytes):
        version = version.decode("ascii", errors="replace")
    return vlc, directory, version


def probe_media(source: str | Path, ffprobe: str | None = None) -> dict[str, Any]:
    path = Path(source).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    if ffprobe is None:
        from diagnostics import ffprobe_path
        ffprobe = ffprobe_path()
    if not ffprobe:
        raise RuntimeError("FFprobe is required to read source metadata.")
    result = subprocess.run(
        [ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
        capture_output=True, text=True, timeout=60, check=False,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "FFprobe could not read the selected video.")
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("FFprobe returned invalid metadata.") from exc
    fmt = data.get("format", {})
    streams = data.get("streams", [])
    video = next((row for row in streams if row.get("codec_type") == "video"), None)
    if not video:
        raise RuntimeError("The selected file has no video stream.")
    try:
        duration_ms = round(float(fmt.get("duration") or video.get("duration")) * 1000)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Could not determine video duration.") from exc
    if duration_ms <= 0:
        raise RuntimeError("The selected video has an invalid duration.")
    stat = path.stat()
    fingerprint = source_fingerprint(path)
    return {
        "path": str(path), "filename": path.name, "fingerprint": fingerprint,
        "size_bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns,
        "duration_ms": duration_ms, "duration_seconds": duration_ms / 1000,
        "width": int(video.get("width") or 0), "height": int(video.get("height") or 0),
        "video_codec": video.get("codec_name", "unknown"),
        "frame_rate": video.get("avg_frame_rate") or video.get("r_frame_rate") or "unknown",
        "streams": [{
            "index": row.get("index"), "type": row.get("codec_type"),
            "codec": row.get("codec_name"), "language": row.get("tags", {}).get("language"),
            "title": row.get("tags", {}).get("title"), "default": bool(int(row.get("disposition", {}).get("default", 0))),
        } for row in streams],
    }


class LibVLCPlayer:
    """Small player facade; no VLC import occurs until construction."""

    def __init__(self, hwnd: int):
        self.vlc, self.runtime_dir, self.version = load_vlc_module()
        self.instance = self.vlc.Instance("--no-video-title-show")
        if self.instance is None:
            raise RuntimeError("LibVLC could not create a playback instance.")
        self.player = self.instance.media_player_new()
        if not self.player:
            self.instance.release()
            raise RuntimeError("LibVLC could not create a media player.")
        self.player.set_hwnd(hwnd)
        self.source: Path | None = None
        self._playing = False

    def open(self, source: str | Path) -> None:
        path = Path(source).resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        media = self.instance.media_new_path(str(path))
        if media is None:
            raise RuntimeError("LibVLC could not open the selected source.")
        self.player.stop()
        self.player.set_media(media)
        media.release()
        self.source = path

    def play(self) -> int:
        if self.source is None:
            raise RuntimeError("Choose a source video first.")
        result = self.player.play()
        if result < 0:
            raise RuntimeError("LibVLC could not start this source.")
        self._playing = result == 0 or self.player.is_playing() == 1
        return result

    def pause(self) -> None:
        self.player.set_pause(1)
        self._playing = False

    def toggle(self) -> None:
        if self.player.is_playing():
            self.pause()
        else:
            self.play()

    def stop(self) -> None:
        self.player.stop()
        self._playing = False

    def seek(self, media_ms: int) -> None:
        if media_ms < 0:
            raise ValueError("Seek time must be non-negative")
        result = self.player.set_time(media_ms)
        if result is not None and result < 0:
            raise RuntimeError("LibVLC rejected this seek. The source or current playback state may not support time seeking.")

    def time_ms(self) -> int:
        return int(self.player.get_time())

    def length_ms(self) -> int:
        return int(self.player.get_length())

    def state(self) -> str:
        state = self.player.get_state()
        for name in ("playing", "paused", "stopped", "ended", "buffering", "error"):
            if state == getattr(self.vlc.State, name.title(), object()):
                return name
        return "unknown"

    def audio_tracks(self) -> list[tuple[int, str]]:
        tracks = self.player.audio_get_track_description() or []
        return [(int(track[0]), track[1].decode("utf-8", errors="replace") if isinstance(track[1], bytes) else str(track[1]))
                for track in tracks if int(track[0]) >= 0]

    def select_audio_track(self, track_id: int) -> bool:
        return self.player.audio_set_track(int(track_id)) == 0

    def subtitle_tracks(self) -> list[tuple[int, str]]:
        tracks = self.player.video_get_spu_description() or []
        return [(int(track[0]), track[1].decode("utf-8", errors="replace") if isinstance(track[1], bytes) else str(track[1]))
                for track in tracks if int(track[0]) >= 0]

    def select_subtitle_track(self, track_id: int) -> bool:
        return self.player.video_set_spu(int(track_id)) == 0

    def close(self) -> None:
        try:
            self.player.stop()
        finally:
            self.player.release()
            self.instance.release()
            self._playing = False
