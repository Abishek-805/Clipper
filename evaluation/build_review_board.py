"""Build a local review board from cached analysis and transcription artifacts."""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from diagnostics import ffmpeg_path, ffprobe_path
from evaluation.anime_review import build_dataset, save_dataset, validate_dataset


def preview_command(source: Path, output: Path, start: float, duration: float,
                    audio_stream_index: int | None, audio_language: str | None) -> list[str]:
    command = [ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{start:.3f}",
               "-i", str(source), "-t", f"{duration:.3f}", "-map", "0:v:0"]
    if audio_stream_index is None:
        command += ["-an"]
    else:
        command += ["-map", f"0:{audio_stream_index}", "-c:a", "aac", "-b:a", "128k"]
        if audio_language:
            command += ["-metadata:s:a:0", f"language={audio_language}"]
    return command + ["-sn", "-dn", "-map_chapters", "-1", "-map_metadata", "-1", "-vf", "scale=640:-2,fps=12",
                      "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast", "-crf", "30",
                      "-movflags", "+faststart", str(output)]


def validate_preview(path: Path, audio_stream_index: int | None, audio_language: str | None) -> dict:
    probe = ffprobe_path()
    if not probe:
        raise FileNotFoundError("FFprobe is required to validate review previews")
    result = subprocess.run([probe, "-v", "error", "-show_entries",
        "stream=codec_type,codec_name,width,height,avg_frame_rate,start_time,duration:stream_tags=language",
        "-show_entries", "format=duration", "-of", "json", str(path)], capture_output=True,
        text=True, check=True)
    data = json.loads(result.stdout)
    streams = data.get("streams", [])
    videos = [stream for stream in streams if stream.get("codec_type") == "video"]
    audios = [stream for stream in streams if stream.get("codec_type") == "audio"]
    if len(videos) != 1 or videos[0].get("codec_name") != "h264" or videos[0].get("width") != 640 or videos[0].get("avg_frame_rate") != "12/1":
        raise ValueError(f"Invalid preview video stream: {path}")
    if len(streams) != 1 + int(audio_stream_index is not None):
        raise ValueError(f"Unexpected subtitle/data or extra media stream: {path}")
    start_delta = duration_delta = None
    if audio_stream_index is not None:
        if len(audios) != 1 or audios[0].get("codec_name") != "aac":
            raise ValueError(f"Selected audio stream is missing from preview: {path}")
        actual_language = audios[0].get("tags", {}).get("language")
        if audio_language and actual_language != audio_language:
            raise ValueError(f"Preview audio language {actual_language!r} differs from selected {audio_language!r}")
        try:
            video_start, audio_start = float(videos[0]["start_time"]), float(audios[0]["start_time"])
            video_duration, audio_duration = float(videos[0]["duration"]), float(audios[0]["duration"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Preview stream timestamps are unavailable: {path}") from exc
        if abs(video_start - audio_start) > .15 or abs(video_duration - audio_duration) > .25:
            raise ValueError(f"Preview audio/video timestamps are not synchronized: {path}")
        start_delta, duration_delta = abs(video_start - audio_start), abs(video_duration - audio_duration)
    duration = float(data.get("format", {}).get("duration", 0))
    if not 0 < duration <= 8.05:
        raise ValueError(f"Preview duration is outside the 8 second limit: {path} ({duration})")
    return {"duration": duration, "audio_available": bool(audios),
            "audio_language": audios[0].get("tags", {}).get("language") if audios else None,
            "stream_types": [stream.get("codec_type") for stream in streams],
            "av_start_delta_seconds": start_delta, "av_duration_delta_seconds": duration_delta}


def write_review_board(analysis_path: Path, output: Path, count: int = 36) -> dict:
    manifest = json.loads(analysis_path.read_text(encoding="utf-8"))
    source = Path(manifest.get("source_path") or manifest.get("source", ""))
    if not source.is_file():
        raise FileNotFoundError(f"Source video is unavailable: {source}")
    transcript_path = analysis_path.with_name("transcription.json")
    transcript = json.loads(transcript_path.read_text(encoding="utf-8")) if transcript_path.exists() else {}
    dataset = build_dataset(manifest, transcript.get("words", []), count)
    output.mkdir(parents=True, exist_ok=True)
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError("OpenCV is required to extract review frames") from exc
    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        raise RuntimeError(f"OpenCV could not open {source}")
    try:
        for item in dataset["candidates"]:
            folder = output / "candidates" / item["candidate_id"]
            folder.mkdir(parents=True, exist_ok=True)
            times = [item["start"] + .05, (item["start"] + item["end"]) / 2, max(item["start"], item["end"] - .75)]
            frames = []
            for i, timestamp in enumerate(times, 1):
                capture.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
                ok, frame = capture.read()
                if ok:
                    h, w = frame.shape[:2]
                    frame = cv2.resize(frame, (min(480, w), max(1, round(h * min(480, w) / w))))
                    rel = Path("candidates") / item["candidate_id"] / f"frame_{i}.jpg"
                    if cv2.imwrite(str(output / rel), frame, [cv2.IMWRITE_JPEG_QUALITY, 78]):
                        frames.append(rel.as_posix())
            item["frames"] = frames
            preview_start = min(max(item["start"], (item["start"] + item["end"] - 8) / 2), item["end"] - .1)
            preview = folder / "preview.mp4"
            duration = min(8, item["end"] - preview_start)
            audio_index = dataset["source"]["selected_audio_stream_index"]
            language = dataset["source"]["audio_language"]
            command = preview_command(source, preview, preview_start, duration, audio_index, language)
            try:
                subprocess.run(command, check=True, capture_output=True, text=True)
                preview_info = validate_preview(preview, audio_index, language)
            except (subprocess.CalledProcessError, ValueError):
                # Never substitute another source track if the selected stream fails to decode.
                if audio_index is None:
                    raise
                command = preview_command(source, preview, preview_start, duration, None, None)
                subprocess.run(command, check=True, capture_output=True, text=True)
                preview_info = validate_preview(preview, None, None)
                item["preview_audio_available"] = False
            item["preview_audio_available"] = preview_info["audio_available"]
            item["audio_stream_index"] = audio_index if preview_info["audio_available"] else None
            item["audio_language"] = preview_info["audio_language"]
            item["preview"] = preview.relative_to(output).as_posix()
            item["preview_duration"] = preview_info["duration"]
    finally:
        capture.release()
    dataset["source"]["preview_audio_available"] = any(item["preview_audio_available"] for item in dataset["candidates"])
    validate_dataset(dataset)
    data_path = output / "anime_highlight_dataset.json"
    save_dataset(data_path, dataset)
    board_template = Path(__file__).with_name("review_board.html").read_text(encoding="utf-8")
    embedded = json.dumps(dataset, ensure_ascii=False).replace("</", "<\\/")
    (output / "index.html").write_text(board_template.replace("__DATA__", embedded), encoding="utf-8")
    return {"board": output / "index.html", "dataset": data_path, "candidate_count": len(dataset["candidates"]),
            "review_sampling": dataset["review_sampling"],
            "audio_stream_index": dataset["source"]["selected_audio_stream_index"],
            "audio_language": dataset["source"]["audio_language"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("analysis_json", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--count", type=int, default=36)
    args = parser.parse_args()
    output = args.output or args.analysis_json.parent / "candidate_review_06d"
    result = write_review_board(args.analysis_json, output, args.count)
    print(json.dumps({**result, "board": str(result["board"]), "dataset": str(result["dataset"])}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
