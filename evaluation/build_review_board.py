"""Build a local, low-resolution candidate review board from existing analysis artifacts."""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from diagnostics import ffmpeg_path
from evaluation.anime_review import build_dataset, save_dataset, validate_dataset


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
            # Leave a decode margin at segment ends; seeking to EOF on some MKVs yields no frame.
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
            # Preview is capped at 8 seconds, muted and 360p for inexpensive local review.
            preview_start = min(max(item["start"], (item["start"] + item["end"] - 8) / 2), item["end"] - .1)
            preview = folder / "preview.mp4"
            command = [ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{preview_start:.3f}",
                       "-i", str(source), "-t", f"{min(8, item['end']-preview_start):.3f}", "-an", "-vf", "scale=640:-2,fps=12",
                       "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30", "-movflags", "+faststart", str(preview)]
            subprocess.run(command, check=True, capture_output=True, text=True)
            item["preview"] = preview.relative_to(output).as_posix()
    finally:
        capture.release()
    validate_dataset(dataset)
    data_path = output / "anime_highlight_dataset.json"
    save_dataset(data_path, dataset)
    board_template = Path(__file__).with_name("review_board.html").read_text(encoding="utf-8")
    embedded = json.dumps(dataset, ensure_ascii=False).replace("</", "<\\/")
    board = board_template.replace("__DATA__", embedded)
    (output / "index.html").write_text(board, encoding="utf-8")
    return {"board": output / "index.html", "dataset": data_path, "candidate_count": len(dataset["candidates"])}


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
