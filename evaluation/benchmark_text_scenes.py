"""Explicit offline benchmark; starts and stops only its own text-only llama.cpp server."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import socket
import threading
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

from diagnostics import gpu_activity
from evaluation.text_scenes import VERSION, audit_response, benchmark_metrics, request_scene, scene_lines
from structure import write_json


def clipper_busy(status_urls):
    for url in status_urls:
        # Status locations are supplied by this local-only diagnostic, never model output.
        from urllib.parse import urlparse
        if urlparse(url).hostname != "127.0.0.1":
            raise ValueError("Only loopback Clipper status URLs are allowed")
        try:
            with build_opener(ProxyHandler({})).open(url, timeout=2) as response:
                state = json.load(response)
            if state.get("state") == "running" or state.get("rendering") or (state.get("model_status") or {}).get("loaded"):
                return True
        except (URLError, TimeoutError, ConnectionError):
            continue
    return False


def run(args):
    units = json.loads(args.units.read_text(encoding="utf-8"))
    if units.get("version") != "episode-units-v1" or not units.get("source_hash"):
        raise ValueError("Expected source-bound Sprint 3A episode units")
    runtime, model = args.runtime.resolve(strict=True), args.model.resolve(strict=True)
    if clipper_busy(args.clipper_status):
        raise RuntimeError("A Clipper operation/model is active; no benchmark model was started")
    with socket.socket() as check:
        check.bind(("127.0.0.1", args.port))
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    command = [str(runtime), "--model", str(model), "--offline", "--no-mmproj", "--no-webui",
               "--host", "127.0.0.1", "--port", str(args.port), "--ctx-size", "4096", "--parallel", "1",
               "--n-gpu-layers", "99", "--threads", "6"]
    samples, abort, stop = [], threading.Event(), threading.Event()
    baseline_gpu = gpu_activity()
    rows = []
    with model.open("rb") as handle:
        model_hash = hashlib.file_digest(handle, "sha256").hexdigest()
    result = dict(version=VERSION, source_hash=units["source_hash"], subtitle_source=units["subtitle_source"],
                  runtime=str(runtime), model=str(model), model_sha256=model_hash,
                  command=command, rows=rows, baseline_gpu=baseline_gpu,
                  gate_rule="Complete pass; >=95% valid JSON; zero unsupported names/quotes; 100% valid IDs; source-entailment audit required",
                  status="running")
    def monitor():
        while not stop.is_set():
            samples.append(gpu_activity())
            if clipper_busy(args.clipper_status):
                abort.set()
                if server.poll() is None:
                    server.terminate()
                return
            stop.wait(.25)
    started = time.perf_counter()
    try:
        with (output / "server.log").open("w", encoding="utf-8") as log:
            server = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                      creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            result["server_pid"] = server.pid
            observer = threading.Thread(target=monitor, daemon=True)
            observer.start()
            try:
                endpoint = f"http://127.0.0.1:{args.port}"
                deadline = time.monotonic() + 60
                while True:
                    if server.poll() is not None:
                        raise RuntimeError("llama.cpp exited during startup; see server.log")
                    if abort.is_set():
                        raise RuntimeError("Owner Clipper became busy; benchmark stopped")
                    try:
                        with build_opener(ProxyHandler({})).open(endpoint + "/health", timeout=1) as response:
                            if json.load(response).get("status") == "ok":
                                break
                    except (URLError, TimeoutError, ConnectionError):
                        pass
                    if time.monotonic() >= deadline:
                        raise TimeoutError("llama.cpp readiness timeout; no repeated startup attempted")
                    time.sleep(.25)
                result["initialization_seconds"] = round(time.perf_counter() - started, 3)
                for scene in units["scenes"]:
                    if abort.is_set() or clipper_busy(args.clipper_status):
                        raise RuntimeError("Owner Clipper became busy; no further model requests sent")
                    lines = scene_lines(scene, units)
                    row = dict(scene_id=scene["id"], start=scene["start"], end=scene["end"], line_count=len(lines))
                    if not lines:
                        row["status"] = "NOT_RUN_NO_DIALOGUE"
                    else:
                        request_started = time.perf_counter()
                        raw, usage, timings = request_scene(endpoint + "/v1/chat/completions", model.name, scene, lines)
                        row.update(status="executed", seconds=round(time.perf_counter()-request_started, 3),
                                   raw_response=raw, usage=usage, server_timings=timings,
                                   audit=audit_response(raw, scene, lines))
                        print(f"{scene['id']}: {row['seconds']}s; {row['audit']['errors'] or 'literal checks passed'}", flush=True)
                    rows.append(row)
                    write_json(output / "results.json", result)
                result["status"] = "completed"
            finally:
                stop.set()
                if server.poll() is None:
                    server.terminate()
                    try:
                        server.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        server.kill()
                        server.wait(timeout=5)
                observer.join(5)
                result["server_exit_code"] = server.poll()
    except Exception as exc:
        result.update(status="blocked", blocker=f"{type(exc).__name__}: {exc}")
    finally:
        stop.set()
        result.update(metrics=benchmark_metrics(rows), total_seconds=round(time.perf_counter()-started, 3),
                      peak_system_gpu_used_mib=max((s.get("used_mib", 0) for s in samples if s.get("available")), default=None),
                      gpu_measurement_scope="System-wide dedicated VRAM, includes desktop/other applications; not per-process peak",
                      gpu_samples=len(samples), after_gpu=gpu_activity())
        write_json(output / "results.json", result)
    print(json.dumps({k: v for k, v in result.items() if k not in ("rows", "command")}, indent=2), flush=True)
    return 0 if result["status"] == "completed" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--units", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8778)
    parser.add_argument("--clipper-status", action="append", default=["http://127.0.0.1:8765/api/status", "http://127.0.0.1:8770/api/status"])
    raise SystemExit(run(parser.parse_args()))


if __name__ == "__main__":
    main()
