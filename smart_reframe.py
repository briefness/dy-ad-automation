"""Evidence-safe Smart Reframe for cross-aspect local product footage."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_FACE_CASCADE: Any = None
_FACE_CASCADE_LOADED = False

# Tolerance multiplier relative to the physical crop width fraction (crop_w / src_w).
# For 16:9 -> 9:16, crop_w / src_w is ~0.316; if foreground visual content spans wider
# than ~1.15x the crop window, cropping would physically sever product/packaging evidence,
# so auto mode preserves full evidence with blur_fit instead.
SAFE_CROP_SPREAD_MULTIPLIER = 1.15


def _get_face_cascade():
    global _FACE_CASCADE, _FACE_CASCADE_LOADED
    if _FACE_CASCADE_LOADED:
        return _FACE_CASCADE
    _FACE_CASCADE_LOADED = True
    try:
        import cv2

        cascade_path = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
        if cascade_path.is_file():
            cascade = cv2.CascadeClassifier(str(cascade_path))
            if not cascade.empty():
                _FACE_CASCADE = cascade
    except Exception:
        _FACE_CASCADE = None
    return _FACE_CASCADE


def _analyze_frame_subject(frame: Any) -> Tuple[float, float, float, str]:
    """Return normalized ``(cx, cy, horizontal_spread, method)`` for one BGR frame."""
    import cv2
    import numpy as np

    h, w = frame.shape[:2]
    if h <= 0 or w <= 0:
        return 0.5, 0.5, 1.0, "fallback"

    scale = min(1.0, 320.0 / max(w, h))
    small = (
        cv2.resize(frame, (max(16, int(w * scale)), max(16, int(h * scale))), interpolation=cv2.INTER_AREA)
        if scale < 1.0
        else frame
    )
    sh, sw = small.shape[:2]
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

    # 1. Optional MediaPipe face detection
    try:
        import mediapipe as mp  # type: ignore

        if hasattr(mp, "solutions") and hasattr(mp.solutions, "face_detection"):
            with mp.solutions.face_detection.FaceDetection(
                model_selection=0, min_detection_confidence=0.5
            ) as detector:
                rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
                res = detector.process(rgb)
                if res.detections:
                    best = max(
                        res.detections,
                        key=lambda d: d.location_data.relative_bounding_box.width
                        * d.location_data.relative_bounding_box.height,
                    )
                    bbox = best.location_data.relative_bounding_box
                    cx = max(0.0, min(1.0, float(bbox.xmin + bbox.width * 0.5)))
                    cy = max(0.0, min(1.0, float(bbox.ymin + bbox.height * 0.45)))
                    spread = max(0.0, min(1.0, float(bbox.width)))
                    return cx, cy, spread, "mediapipe_face"
    except Exception:
        pass

    # 2. Bundled OpenCV Haar face detector
    cascade = _get_face_cascade()
    if cascade is not None:
        faces = cascade.detectMultiScale(
            gray,
            scaleFactor=1.15,
            minNeighbors=4,
            minSize=(max(20, sw // 12), max(20, sh // 12)),
        )
        if len(faces) > 0:
            fx, fy, fw, fh = max(faces, key=lambda rect: int(rect[2]) * int(rect[3]))
            cx = (fx + fw * 0.5) / float(sw)
            cy = (fy + fh * 0.45) / float(sh)
            spread = float(fw) / float(sw)
            return max(0.0, min(1.0, cx)), max(0.0, min(1.0, cy)), spread, "opencv_face"

    # 3. Foreground saliency centroid + horizontal spread (guards wide packaging/text)
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    mag = cv2.magnitude(gx, gy)
    thresh = float(np.percentile(mag, 75))
    energy = np.where(mag >= thresh, mag - thresh, 0.0)
    total = float(np.sum(energy))
    if total <= 1e-6:
        return 0.5, 0.5, 1.0, "center_fallback"

    col_energy = np.sum(energy, axis=0)
    col_cumsum = np.cumsum(col_energy) / max(float(np.sum(col_energy)), 1e-6)
    x10 = float(np.searchsorted(col_cumsum, 0.10)) / float(sw)
    x90 = float(np.searchsorted(col_cumsum, 0.90)) / float(sw)
    horizontal_spread = max(0.0, min(1.0, x90 - x10))

    ys = np.linspace(-1.0, 1.0, sh, dtype=np.float32)[:, None]
    xs = np.linspace(-1.0, 1.0, sw, dtype=np.float32)[None, :]
    center_prior = np.exp(-0.5 * (xs * xs + ys * ys) / 0.85)
    weights = energy * center_prior
    w_total = float(np.sum(weights))
    if w_total <= 1e-6:
        return 0.5, 0.5, horizontal_spread, "center_fallback"

    x_coords = np.linspace(0.0, 1.0, sw, dtype=np.float32)[None, :]
    y_coords = np.linspace(0.0, 1.0, sh, dtype=np.float32)[:, None]
    cx = float(np.sum(weights * x_coords) / w_total)
    cy = float(np.sum(weights * y_coords) / w_total)
    return max(0.0, min(1.0, cx)), max(0.0, min(1.0, cy)), horizontal_spread, "saliency"


def detect_subject_centers(
    source: Path,
    start: float,
    end: float,
    sample_count: int = 8,
) -> Tuple[Tuple[int, int], List[Tuple[float, float, float]], float, str]:
    """Sample frames across [start, end] -> ((src_w, src_h), centers, median_spread, method)."""
    import cv2

    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        return (0, 0), [(0.0, 0.5, 0.5)], 1.0, "fallback"

    src_w = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    src_h = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    duration = max(0.1, float(end) - float(start))
    count = max(2, min(16, int(sample_count)))
    centers: List[Tuple[float, float, float]] = []
    spreads: List[float] = []
    methods: List[str] = []
    try:
        for idx in range(count):
            rel_t = duration * idx / max(count - 1, 1)
            abs_t = max(0.0, float(start) + rel_t)
            capture.set(cv2.CAP_PROP_POS_MSEC, abs_t * 1000.0)
            ok, frame = capture.read()
            if not ok or frame is None:
                continue
            if src_w == 0 or src_h == 0:
                src_h, src_w = frame.shape[:2]
            cx, cy, spread, method = _analyze_frame_subject(frame)
            centers.append((round(rel_t, 3), round(cx, 4), round(cy, 4)))
            spreads.append(spread)
            methods.append(method)
    finally:
        capture.release()

    if not centers:
        return (src_w, src_h), [(0.0, 0.5, 0.5)], 1.0, "fallback"
    sorted_spreads = sorted(spreads)
    median_spread = float(sorted_spreads[len(sorted_spreads) // 2])
    primary_method = (
        "mediapipe_face" if "mediapipe_face" in methods
        else "opencv_face" if "opencv_face" in methods
        else "saliency" if "saliency" in methods
        else "center_fallback"
    )
    return (src_w, src_h), centers, round(median_spread, 4), primary_method


def smooth_crop_trajectory(
    centers: List[Tuple[float, float, float]],
    src_w: int,
    src_h: int,
    target_w: int,
    target_h: int,
    *,
    alpha: float = 0.35,
    deadzone_ratio: float = 0.04,
) -> Dict[str, Any]:
    """Compute a jitter-free crop window and smoothed pan/tilt keyframes."""
    if src_w <= 0 or src_h <= 0 or target_w <= 0 or target_h <= 0:
        return {
            "crop_w": target_w,
            "crop_h": target_h,
            "keyframes": [(0.0, 0, 0)],
            "is_dynamic": False,
        }

    target_ratio = float(target_w) / float(target_h)
    src_ratio = float(src_w) / float(src_h)
    if src_ratio > target_ratio:
        crop_h = src_h
        crop_w = max(2, int(round(src_h * target_ratio)))
    else:
        crop_w = src_w
        crop_h = max(2, int(round(src_w / target_ratio)))
    crop_w = min(src_w, crop_w - (crop_w % 2))
    crop_h = min(src_h, crop_h - (crop_h % 2))

    max_x = max(0, src_w - crop_w)
    max_y = max(0, src_h - crop_h)
    deadzone_x = max(4.0, src_w * deadzone_ratio)
    deadzone_y = max(4.0, src_h * deadzone_ratio)

    raw_points: List[Tuple[float, float, float]] = []
    for rel_t, cx, cy in centers:
        px = max(0.0, min(float(max_x), cx * src_w - crop_w * 0.5))
        py = max(0.0, min(float(max_y), cy * src_h - crop_h * 0.5))
        raw_points.append((float(rel_t), px, py))

    smoothed: List[Tuple[float, int, int]] = []
    cur_x, cur_y = raw_points[0][1], raw_points[0][2]
    for idx, (rel_t, px, py) in enumerate(raw_points):
        if idx > 0:
            if abs(px - cur_x) >= deadzone_x:
                cur_x = alpha * px + (1.0 - alpha) * cur_x
            if abs(py - cur_y) >= deadzone_y:
                cur_y = alpha * py + (1.0 - alpha) * cur_y
        smoothed.append((round(rel_t, 3), int(round(cur_x)), int(round(cur_y))))

    xs = [pt[1] for pt in smoothed]
    ys = [pt[2] for pt in smoothed]
    span_x = (max(xs) - min(xs)) if xs else 0
    span_y = (max(ys) - min(ys)) if ys else 0
    is_dynamic = len(smoothed) >= 2 and (span_x >= deadzone_x or span_y >= deadzone_y)

    if not is_dynamic:
        med_x = int(round(sorted(xs)[len(xs) // 2])) if xs else 0
        med_y = int(round(sorted(ys)[len(ys) // 2])) if ys else 0
        smoothed = [(0.0, med_x, med_y)]

    return {
        "crop_w": crop_w,
        "crop_h": crop_h,
        "keyframes": smoothed,
        "is_dynamic": is_dynamic,
    }


def _piecewise_linear_expr(keyframes: List[Tuple[float, int]]) -> str:
    if not keyframes:
        return "0"
    if len(keyframes) == 1:
        return str(keyframes[0][1])

    expr = str(keyframes[-1][1])
    for index in range(len(keyframes) - 2, -1, -1):
        t0, v0 = keyframes[index]
        t1, v1 = keyframes[index + 1]
        dt = max(0.001, t1 - t0)
        seg = str(v0) if v0 == v1 else f"({v0}+({v1 - v0})*(t-{t0:.3f})/{dt:.3f})"
        expr = f"if(lt(t,{t1:.3f}),{seg},{expr})"
    return expr


def _blur_fit_vf(target_w: int, target_h: int) -> str:
    """Evidence-preserving full-frame fit over a soft blurred backdrop (no black bars, no cropped labels)."""
    return (
        f"split[bg][fg];"
        f"[bg]scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
        f"crop={target_w}:{target_h},boxblur=20:4,setsar=1[bgb];"
        f"[fg]scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,setsar=1[fgb];"
        f"[bgb][fgb]overlay=(W-w)/2:(H-h)/2,setsar=1"
    )


def build_smart_reframe_vf(
    source: Path,
    start: float,
    end: float,
    target_w: int,
    target_h: int,
    *,
    mode: Optional[str] = None,
    sample_count: int = 8,
) -> Dict[str, Any]:
    """Build an FFmpeg ``-vf`` chain that reframes cross-aspect clips without losing visual evidence.

    Modes:
    - ``pad``: legacy black-bar letterbox
    - ``blur_fit``: preserve 100% of frame over blurred backdrop (zero black bars, zero cropped evidence)
    - ``auto`` (default): Smart Reframe when subject fits safely inside the crop box
      (``horizontal_spread <= 0.68``), otherwise ``blur_fit`` so wide packaging/labels are never cut off
    - ``crop``: always Smart Reframe to fill target frame
    """
    resolved_mode = (
        (mode or os.getenv("LOCAL_ASSET_SMART_REFRAME", "auto")).strip().lower()
    )
    pad_vf = (
        f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,"
        f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:black,setsar=1"
    )
    if resolved_mode == "pad":
        return {
            "vf": pad_vf,
            "mode": "pad",
            "method": "letterbox_pad",
            "is_dynamic": False,
            "keyframes": [],
            "evidence_safe": True,
        }
    if resolved_mode == "blur_fit":
        return {
            "vf": _blur_fit_vf(target_w, target_h),
            "mode": "blur_fit",
            "method": "evidence_preserving_blur_fit",
            "is_dynamic": False,
            "keyframes": [],
            "evidence_safe": True,
        }

    (src_w, src_h), centers, median_spread, method = detect_subject_centers(
        source, start, end, sample_count=sample_count
    )
    if src_w <= 0 or src_h <= 0:
        return {
            "vf": pad_vf,
            "mode": "pad",
            "method": "probe_fallback",
            "is_dynamic": False,
            "keyframes": [],
            "evidence_safe": True,
        }

    src_ratio = float(src_w) / float(src_h)
    target_ratio = float(target_w) / float(target_h)
    ratio_delta = abs(src_ratio - target_ratio) / max(target_ratio, 0.001)
    if resolved_mode == "auto" and ratio_delta <= 0.05:
        return {
            "vf": f"scale={target_w}:{target_h},setsar=1",
            "mode": "direct",
            "method": "aspect_matched",
            "is_dynamic": False,
            "keyframes": [],
            "evidence_safe": True,
        }

    # Evidence-preservation guard: compare foreground horizontal spread directly against
    # the physical crop window width fraction (crop_w / src_w). If the product/packaging
    # cannot physically fit inside the 9:16 crop window without severing evidence, use blur_fit.
    crop_width_fraction = min(1.0, (target_ratio / max(src_ratio, 0.001)))
    safe_spread_limit = min(0.85, crop_width_fraction * SAFE_CROP_SPREAD_MULTIPLIER)
    if (
        resolved_mode == "auto"
        and src_ratio > target_ratio
        and method not in {"mediapipe_face", "opencv_face"}
        and median_spread > safe_spread_limit
    ):
        return {
            "vf": _blur_fit_vf(target_w, target_h),
            "mode": "blur_fit",
            "method": "wide_evidence_blur_fit",
            "horizontal_spread": median_spread,
            "safe_spread_limit": round(safe_spread_limit, 4),
            "is_dynamic": False,
            "keyframes": [],
            "evidence_safe": True,
        }

    traj = smooth_crop_trajectory(centers, src_w, src_h, target_w, target_h)
    cw, ch = traj["crop_w"], traj["crop_h"]
    kfs = traj["keyframes"]
    if traj["is_dynamic"]:
        x_expr = _piecewise_linear_expr([(t, x) for t, x, _ in kfs])
        y_expr = _piecewise_linear_expr([(t, y) for t, _, y in kfs])
        vf = (
            f"crop=w={cw}:h={ch}:x='{x_expr}':y='{y_expr}',"
            f"scale={target_w}:{target_h},setsar=1"
        )
    else:
        _, x0, y0 = kfs[0]
        vf = f"crop={cw}:{ch}:{x0}:{y0},scale={target_w}:{target_h},setsar=1"

    return {
        "vf": vf,
        "mode": "smart_reframe",
        "method": method,
        "horizontal_spread": median_spread,
        "is_dynamic": bool(traj["is_dynamic"]),
        "crop_w": cw,
        "crop_h": ch,
        "keyframes": kfs,
        "evidence_safe": True,
    }
