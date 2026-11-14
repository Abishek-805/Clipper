"""CPU-only shot framing using built-in faces plus bounded visual saliency evidence."""
from __future__ import annotations

import math
from pathlib import Path

TARGET_RATIO = 9 / 16
MIN_SALIENCY_COVERAGE = .76
MIN_FACE_PERSISTENCE = .50
MAX_ANALYSIS_SAMPLES = 48
PLAN_VERSION = "shot-frame-v1"


def shot_intervals(start: float, end: float, cuts: list[float]) -> list[tuple[float, float]]:
    if end <= start:
        raise ValueError("Clip end must follow clip start")
    points = [start, *sorted({float(cut) for cut in cuts if start < cut < end}), end]
    return list(zip(points, points[1:]))


def crop_bounds(width: int, height: int, center_x: float) -> dict | None:
    if width <= 0 or height <= 0 or width / height <= TARGET_RATIO:
        return None
    crop_w = max(2, int(height * TARGET_RATIO) // 2 * 2)
    left = min(width - crop_w, max(0, int(center_x - crop_w / 2)))
    return {"x": left, "y": 0, "width": crop_w, "height": height}


def validate_crop(crop: dict, width: int, height: int) -> bool:
    return (all(isinstance(crop.get(key), int) for key in ("x", "y", "width", "height")) and
            crop["x"] >= 0 and crop["y"] >= 0 and crop["width"] > 0 and crop["height"] > 0 and
            crop["x"] + crop["width"] <= width and crop["y"] + crop["height"] <= height)


def smooth_centers(centers: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Three-sample moving average; segments are planned independently at every cut."""
    output = []
    for index in range(len(centers)):
        neighbors = centers[max(0, index - 1):min(len(centers), index + 2)]
        output.append((sum(item[0] for item in neighbors) / len(neighbors),
                       sum(item[1] for item in neighbors) / len(neighbors)))
    return output


def plan_shot(start: float, end: float, width: int, height: int,
              face_samples: list[list[tuple[float, float, float, float]]],
              saliency_samples: list[list[float]], min_coverage: float = MIN_SALIENCY_COVERAGE,
              min_face_persistence: float = MIN_FACE_PERSISTENCE, shot_id: int = 0,
              source_text_boxes: list[tuple[float, float, float, float]] | None = None) -> dict:
    sample_count = max(len(face_samples), len(saliency_samples))
    if width <= 0 or height <= 0 or end <= start:
        raise ValueError("Invalid source geometry or shot interval")
    base = {"shot_id": shot_id, "start": round(start, 3), "end": round(end, 3),
            "composition": "full_frame_contain", "crop": None, "focus_path": [],
            "evidence": "low-confidence fallback", "signal_coverage": 0.0, "obstructions": []}
    for faces in face_samples:
        for x, y, box_w, box_h in faces:
            base["obstructions"].append({"kind": "face", "box": [x / width, y / height,
                                                                      (x + box_w) / width, (y + box_h) / height]})
    for x, y, box_w, box_h in source_text_boxes or []:
        base["obstructions"].append({"kind": "source_text", "box": [x / width, y / height,
                                                                       (x + box_w) / width, (y + box_h) / height]})
    if width / height <= TARGET_RATIO:
        base["evidence"] = "source is portrait or narrower; cropping would remove vertical content"
        return base
    crop_w = max(2, int(height * TARGET_RATIO) // 2 * 2)
    detected = []
    for index, faces in enumerate(face_samples):
        valid = [box for box in faces if len(box) == 4 and box[2] > 0 and box[3] > 0 and
                 box[0] >= 0 and box[1] >= 0 and box[0] + box[2] <= width and box[1] + box[3] <= height]
        if valid:
            # Multiple distant faces are preserved together in contain mode.
            centers = sorted(box[0] + box[2] / 2 for box in valid)
            if centers[-1] - centers[0] > crop_w * .72:
                base["composition"] = "two_subject_contain"
                base["evidence"] = "multiple detected faces do not fit one portrait crop"
                return base
            chosen = max(valid, key=lambda box: box[2] * box[3])
            detected.append((index, chosen[0] + chosen[2] / 2, chosen[1] + chosen[3] / 2))
    persistence = len(detected) / max(1, sample_count)
    centers = []
    mode = "subject_crop"
    coverage = persistence
    evidence = "built-in face detector persistence across sampled frames"
    if detected and persistence >= min_face_persistence:
        values = smooth_centers([(x, y) for _, x, y in detected])
        centers = [(index, *point) for (index, _, _), point in zip(detected, values)]
    else:
        mode = "saliency_crop"
        evidence = "edge and optical-flow saliency fits within the portrait crop"
        per_sample = []
        for index, saliency in enumerate(saliency_samples):
            if len(saliency) != width:
                continue
            total = sum(max(0.0, value) for value in saliency)
            if total <= 1e-9:
                continue
            best_left, best_mass = 0, -1.0
            running = sum(max(0.0, value) for value in saliency[:crop_w])
            best_mass = running
            for left in range(1, width - crop_w + 1):
                running += max(0.0, saliency[left + crop_w - 1]) - max(0.0, saliency[left - 1])
                if running > best_mass:
                    best_left, best_mass = left, running
            per_sample.append((index, best_left + crop_w / 2, best_mass / total))
        coverage = sum(value[2] for value in per_sample) / max(1, len(per_sample))
        if not per_sample or coverage < min_coverage:
            base["signal_coverage"] = round(coverage, 4)
            base["evidence"] = "saliency is too dispersed for a safe portrait crop"
            return base
        centers = [(index, center_x, height / 2) for index, center_x, _ in per_sample]
        smoothed = smooth_centers([(x, y) for _, x, y in centers])
        centers = [(index, *point) for (index, _, _), point in zip(centers, smoothed)]
    path = []
    for index, center_x, center_y in centers:
        crop = crop_bounds(width, height, center_x)
        if crop is None or not validate_crop(crop, width, height):
            continue
        sample_time = start + (end - start) * ((index + .5) / max(1, sample_count))
        path.append({"sample_index": index, "sample_time": round(sample_time - start, 3),
                     "center_x": round(center_x, 2), "center_y": round(center_y, 2),
                     "crop": crop, "scene_reset": index == 0})
    if not path:
        base["evidence"] = "no valid crop rectangle; full-frame fallback"
        return base
    if mode == "saliency_crop":
        for point in path:
            cx, cy = point["center_x"] / width, point["center_y"] / height
            base["obstructions"].append({"kind": "action", "box": [max(0, cx - .16), max(0, cy - .18),
                                                                         min(1, cx + .16), min(1, cy + .18)]})
    base.update(composition=mode, crop=path[len(path) // 2]["crop"], focus_path=path,
                evidence=evidence, signal_coverage=round(coverage, 4))
    return base


def _saliency_columns(frame, previous_gray, cv2, np, width: int, height: int) -> list[float]:
    small_w = min(320, width)
    small_h = max(1, round(height * small_w / width))
    gray = cv2.cvtColor(cv2.resize(frame, (small_w, small_h)), cv2.COLOR_BGR2GRAY)
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    energy = cv2.magnitude(gx, gy)
    if previous_gray is not None:
        flow = cv2.calcOpticalFlowFarneback(previous_gray, gray, None, .5, 2, 15, 2, 5, 1.1, 0)
        energy += cv2.magnitude(flow[..., 0], flow[..., 1]) * 3
    columns = energy.sum(axis=0)
    return [float(value) for value in np.interp(np.arange(width), np.linspace(0, width - 1, small_w), columns)]


def _text_like_boxes(gray, cv2, scale_x: float, scale_y: float) -> list[tuple[float, float, float, float]]:
    """Find lower-frame text-shaped clusters; this is not OCR and may yield false positives."""
    height, width = gray.shape[:2]
    binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(5, width // 20), 1))
    joined = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)
    contours = cv2.findContours(joined, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]
    result = []
    for contour in contours:
        x, y, box_w, box_h = cv2.boundingRect(contour)
        if y > height * .66 and box_w > width * .12 and 2 <= box_h <= height * .18:
            result.append((x * scale_x, y * scale_y, box_w * scale_x, box_h * scale_y))
    return result


def build_reframe_plan(source: Path, start: float, end: float, cuts: list[float],
                       cancel=None, min_coverage: float = MIN_SALIENCY_COVERAGE,
                       preserve_source: bool = False) -> dict:
    if preserve_source:
        if cancel and cancel.is_set():
            raise InterruptedError("Processing cancelled")
        return {"version": PLAN_VERSION, "status": "full_frame_requested", "analysis_samples": 0,
                "reason": "Preserve source graphics, scoreboards and shared screens",
                "shots": [{"shot_id": 0, "start": start, "end": end,
                           "composition": "full_frame_contain", "crop": None,
                           "focus_path": [], "obstructions": [], "evidence": "Owner-selected source profile"}]}
    import cv2
    import numpy as np
    cap = cv2.VideoCapture(str(source))
    if not cap.isOpened():
        return {"version": PLAN_VERSION, "status": "fallback", "shots": [], "reason": "OpenCV could not open source"}
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    intervals = shot_intervals(start, end, cuts)
    if not hasattr(cv2, "CascadeClassifier"):
        cap.release()
        return {"version": PLAN_VERSION, "status": "blurred_background", "source_width": width,
                "source_height": height, "analysis_samples": 0,
                "reason": "face-aware smart crop unavailable in installed OpenCV; full-frame blurred background selected",
                "shots": [{"shot_id": index, "start": left, "end": right,
                           "composition": "full_frame_contain", "crop": None, "focus_path": [],
                           "obstructions": [], "evidence": "intentional blurred-background layout"}
                          for index, (left, right) in enumerate(intervals)]}
    sample_budget = max(1, MAX_ANALYSIS_SAMPLES // max(1, len(intervals)))
    shots = []
    cascade = cv2.CascadeClassifier(str(Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"))
    try:
        for shot_id, (shot_start, shot_end) in enumerate(intervals):
            if cancel and cancel.is_set():
                raise InterruptedError("Processing cancelled")
            count = max(1, min(6, sample_budget))
            timestamps = [shot_start + (shot_end - shot_start) * ((i + .5) / count) for i in range(count)]
            face_samples, saliency_samples, text_samples, previous_gray = [], [], [], None
            for timestamp in timestamps:
                if cancel and cancel.is_set():
                    raise InterruptedError("Processing cancelled")
                cap.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
                ok, frame = cap.read()
                if not ok:
                    face_samples.append([])
                    continue
                small_w = min(320, width)
                small_h = max(1, round(height * small_w / width))
                small = cv2.resize(frame, (small_w, small_h))
                gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
                saliency = _saliency_columns(frame, previous_gray, cv2, np, width, height)
                saliency_samples.append(saliency)
                previous_gray = gray
                boxes = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(18, 18)) if not cascade.empty() else ()
                face_samples.append([(x * width / small_w, y * height / small_h,
                                      box_w * width / small_w, box_h * height / small_h)
                                     for x, y, box_w, box_h in boxes])
                text_samples.extend(_text_like_boxes(gray, cv2, width / small_w, height / small_h))
            shots.append(plan_shot(shot_start, shot_end, width, height, face_samples,
                                   saliency_samples, min_coverage, shot_id=shot_id,
                                   source_text_boxes=text_samples))
    finally:
        cap.release()
    return {"version": PLAN_VERSION, "status": "completed", "source_width": width,
            "source_height": height, "analysis_samples": sum(len(shot["focus_path"]) for shot in shots),
            "shots": shots}


def write_reframe_debug(source: Path, plan: dict, output: Path) -> Path | None:
    import cv2
    import numpy as np
    shots = plan.get("shots", [])
    if not shots:
        return None
    if len(shots) > 12:
        shots = [shots[round(i * (len(shots) - 1) / 11)] for i in range(12)]
    cap = cv2.VideoCapture(str(source))
    if not cap.isOpened():
        return None
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cards = []
    try:
        for shot in shots:
            timestamp = (float(shot["start"]) + float(shot["end"])) / 2
            cap.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
            ok, frame = cap.read()
            if not ok:
                continue
            for item in shot.get("obstructions", []):
                x1, y1, x2, y2 = item["box"]
                color = (0, 100, 255) if item["kind"] in {"face", "source_text"} else (0, 255, 255)
                cv2.rectangle(frame, (int(x1 * width), int(y1 * height)),
                              (int(x2 * width), int(y2 * height)), color, max(1, width // 500))
            crop = shot.get("crop")
            if crop:
                cv2.rectangle(frame, (crop["x"], crop["y"]),
                              (crop["x"] + crop["width"], crop["y"] + crop["height"]), (0, 255, 0), max(2, width // 300))
            frame = cv2.resize(frame, (320, 180))
            cv2.putText(frame, f"shot {shot['shot_id']} · {shot['composition']}", (5, 172),
                        cv2.FONT_HERSHEY_SIMPLEX, .38, (255, 255, 255), 1, cv2.LINE_AA)
            cards.append(frame)
    finally:
        cap.release()
    if not cards:
        return None
    columns, rows = 3, math.ceil(len(cards) / 3)
    sheet = np.zeros((rows * 180, columns * 320, 3), dtype=np.uint8)
    for index, frame in enumerate(cards):
        y, x = (index // columns) * 180, (index % columns) * 320
        sheet[y:y + 180, x:x + 320] = frame
    output.parent.mkdir(parents=True, exist_ok=True)
    return output if cv2.imwrite(str(output), sheet) else None


def crop_x_expression(path: list[dict], crop_width: int, source_width: int, duration: float) -> str:
    if not path:
        return str(max(0, (source_width - crop_width) // 2))
    values = []
    for point in path:
        x = min(source_width - crop_width, max(0, int(point["center_x"] - crop_width / 2)))
        values.append((float(point.get("sample_time", point.get("sample_index", 0))), x))
    if len(values) == 1:
        return str(values[0][1])
    values[0] = (0.0, values[0][1])
    values[-1] = (duration, values[-1][1])
    expression = str(values[-1][1])
    for (left_t, left_x), (right_t, right_x) in reversed(list(zip(values, values[1:]))):
        if right_t <= left_t:
            continue
        movement = f"({left_x}+({right_x-left_x})*(t-{left_t:.3f})/{right_t-left_t:.3f})"
        expression = f"if(lt(t,{right_t:.3f}),{movement},{expression})"
    return expression
