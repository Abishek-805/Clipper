"""One NVDEC/CUDA visual pass with bounded, low-resolution CPU measurements."""
from __future__ import annotations

import math
import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from diagnostics import ffmpeg_path


def visual_signals(path: Path, duration: float, source_fps: float, cancel: threading.Event, progress):
    import cv2
    import numpy as np
    from scenedetect import ContentDetector, FrameTimecode

    width, height, sample_fps = 320, 180, 8
    command = [ffmpeg_path(), "-v", "error", "-hwaccel", "cuda", "-hwaccel_output_format", "cuda",
        "-i", str(path), "-t", str(duration), "-map", "0:v:0", "-an", "-sn", "-dn",
        "-vf", "fps=8:start_time=0,scale_cuda=320:180:format=nv12,hwdownload,format=nv12,format=bgr24",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    if cancel.is_set():
        raise InterruptedError("Processing cancelled")
    progress("Visual analysis · GPU NVDEC decode + CUDA resize · CPU scene/motion on 320×180 samples")
    motion = [0.0] * max(1, math.ceil(duration))
    cuts, index, sampled, previous = [], 0, 0, None
    # Preserve the old 15-source-frame minimum interval in seconds at the new sample rate.
    detector = ContentDetector(threshold=27.0, min_scene_len=15.0 / max(1, source_fps))
    stopped = threading.Event()
    started = time.perf_counter()
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=errors,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        def watch_cancel():
            while not stopped.wait(.1):
                if cancel.is_set():
                    if process.poll() is None:
                        process.terminate()
                    return
        watcher = threading.Thread(target=watch_cancel, daemon=True)
        watcher.start()
        try:
            while True:
                data = process.stdout.read(width * height * 3)
                if cancel.is_set():
                    raise InterruptedError("Processing cancelled")
                if not data:
                    break
                if len(data) != width * height * 3:
                    raise RuntimeError("NVDEC returned an incomplete analysis frame")
                frame = np.frombuffer(data, dtype=np.uint8).reshape(height, width, 3)
                cuts.extend(float(point.seconds) for point in detector.process_frame(FrameTimecode(index, fps=float(sample_fps)), frame))
                if index % 4 == 0:
                    gray = cv2.cvtColor(cv2.resize(frame, (160, 90)), cv2.COLOR_BGR2GRAY)
                    if previous is not None:
                        second = min(len(motion)-1, max(0, int(index/sample_fps-.25)))
                        motion[second] = max(motion[second], float(cv2.absdiff(previous, gray).mean()))
                    previous = gray
                    sampled += 1
                index += 1
                if index % (sample_fps * 10) == 0:
                    progress(f"Visual analysis · GPU NVDEC · {min(duration, index/sample_fps):.0f}/{duration:.0f}s")
            code = process.wait(timeout=10)
            if code or not index:
                errors.seek(0, 2)
                errors.seek(max(0, errors.tell()-2000))
                raise RuntimeError(errors.read().decode(errors="replace").strip() or "NVDEC decoded no frames")
            if index < max(1, int(duration*sample_fps)-sample_fps*2):
                raise RuntimeError("NVDEC ended before the complete source interval was analyzed")
        finally:
            stopped.set()
            if process.poll() is None:
                process.kill()
                process.wait()
            process.stdout.close()
            watcher.join(timeout=1)
    stats = {"device": "cuda-nvdec", "resize_device": "cuda", "measurement_device": "cpu",
        "frames_decoded": index, "frame_count_basis": "sampled frames downloaded; source decoding remains on NVDEC",
        "frames_sampled": sampled, "scene_sample_fps": sample_fps, "sample_rate_fps": 2,
        "sample_resolution": [width, height], "seconds": round(time.perf_counter()-started, 3), "command": command}
    return motion, sorted(set(cuts)), f"{len(cuts)} cut(s) found · NVDEC/CUDA samples at 8 fps", stats
