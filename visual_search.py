"""Adaptive shot-boundary detection and compact visual descriptors for window deduplication."""

from __future__ import annotations

from pathlib import Path
from typing import Any, List, Sequence


def detect_adaptive_scene_cuts(
    source: Path,
    duration: float,
    *,
    min_scene_seconds: float = 1.5,
    adaptive_threshold: float = 2.8,
    min_content_delta: float = 18.0,
    window_radius: int = 4,
    sample_fps: float = 4.0,
) -> List[float]:
    """Detect shot boundaries using adaptive rolling-baseline HSV content deltas.

    Delegates to PySceneDetect's ``AdaptiveDetector`` when installed, and otherwise
    uses an equivalent OpenCV+NumPy rolling-window HSV detector that resists
    flashes, camera shake, and exposure drift better than a static global threshold.
    """
    source = Path(source)
    if duration <= min_scene_seconds * 1.5:
        return []

    try:
        from scenedetect import AdaptiveDetector, detect  # type: ignore

        scenes = detect(str(source), AdaptiveDetector(adaptive_threshold=adaptive_threshold))
        raw_cuts = [float(start.get_seconds()) for start, _ in scenes[1:]]
        return _filter_cuts(raw_cuts, duration, min_scene_seconds)
    except Exception:
        pass

    try:
        import cv2
        import numpy as np
    except ImportError:
        return []

    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        return []

    native_fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
    if native_fps <= 0.0 or native_fps > 240.0:
        native_fps = 30.0
    step = max(1, int(round(native_fps / max(1.0, sample_fps))))

    timestamps: List[float] = []
    deltas: List[float] = []
    prev_hsv = None
    frame_idx = 0
    try:
        while True:
            ok = capture.grab()
            if not ok:
                break
            if frame_idx % step == 0:
                ret, frame = capture.retrieve()
                if not ret or frame is None:
                    frame_idx += 1
                    continue
                small = cv2.resize(frame, (128, 72), interpolation=cv2.INTER_AREA)
                hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV).astype(np.float32)
                ts = frame_idx / native_fps
                if prev_hsv is not None:
                    dh = np.abs(hsv[:, :, 0] - prev_hsv[:, :, 0])
                    dh = np.minimum(dh, 180.0 - dh) * (255.0 / 90.0)
                    ds = np.abs(hsv[:, :, 1] - prev_hsv[:, :, 1])
                    dv = np.abs(hsv[:, :, 2] - prev_hsv[:, :, 2])
                    score = float(np.mean(0.45 * dh + 0.25 * ds + 0.30 * dv))
                    timestamps.append(ts)
                    deltas.append(score)
                prev_hsv = hsv
            frame_idx += 1
    finally:
        capture.release()

    if len(deltas) < 3:
        return []

    cuts: List[float] = []
    n = len(deltas)
    for i in range(n):
        val = deltas[i]
        if val < min_content_delta:
            continue
        left = deltas[max(0, i - window_radius):i]
        right = deltas[i + 1:min(n, i + 1 + window_radius)]
        neighbors = left + right
        if not neighbors:
            continue
        baseline = max(1.0, float(sum(neighbors) / len(neighbors)))
        if val >= adaptive_threshold * baseline and val >= max(neighbors):
            cuts.append(timestamps[i])

    return _filter_cuts(cuts, duration, min_scene_seconds)


def _filter_cuts(raw_cuts: Sequence[float], duration: float, min_scene_seconds: float) -> List[float]:
    usable_end = max(duration - 0.05, 0.5)
    clean: List[float] = []
    for cut in sorted(set(float(v) for v in raw_cuts)):
        if cut < min_scene_seconds or cut > usable_end - min_scene_seconds:
            continue
        if clean and cut - clean[-1] < min_scene_seconds:
            continue
        clean.append(round(cut, 3))
    return clean


def compute_visual_descriptor_from_frames(frames_bgr: Sequence[Any]) -> List[float]:
    """Compute a compact 48-D HSV+edge descriptor for intra-shot static window deduplication."""
    if not frames_bgr:
        return []
    import cv2
    import numpy as np

    vectors = []
    for frame in frames_bgr:
        if frame is None:
            continue
        small = cv2.resize(frame, (128, 72), interpolation=cv2.INTER_AREA)
        hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
        h_hist = cv2.calcHist([hsv], [0], None, [16], [0, 180]).flatten()
        s_hist = cv2.calcHist([hsv], [1], None, [8], [0, 256]).flatten()
        v_hist = cv2.calcHist([hsv], [2], None, [8], [0, 256]).flatten()
        color_vec = np.concatenate([h_hist, s_hist, v_hist]).astype(np.float32)
        color_vec = color_vec / (float(np.linalg.norm(color_vec)) + 1e-6)

        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 60, 150).astype(np.float32) / 255.0
        gh, gw = edges.shape
        grid = [
            float(np.mean(edges[r * gh // 4:(r + 1) * gh // 4, c * gw // 4:(c + 1) * gw // 4]))
            for r in range(4)
            for c in range(4)
        ]
        edge_vec = np.array(grid, dtype=np.float32)
        edge_vec = edge_vec / (float(np.linalg.norm(edge_vec)) + 1e-6)

        combined = np.concatenate([color_vec * 0.75, edge_vec * 0.25])
        combined /= float(np.linalg.norm(combined)) + 1e-6
        vectors.append(combined)

    if not vectors:
        return []
    mean_vec = np.mean(vectors, axis=0)
    mean_vec /= float(np.linalg.norm(mean_vec)) + 1e-6
    return [round(float(x), 4) for x in mean_vec.tolist()]


def visual_cosine_similarity(vec_a: Sequence[float], vec_b: Sequence[float]) -> float:
    """Cosine similarity in [0, 1] between two visual descriptors."""
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    dot = sum(float(a) * float(b) for a, b in zip(vec_a, vec_b))
    norm_a = sum(float(a) * float(a) for a in vec_a) ** 0.5
    norm_b = sum(float(b) * float(b) for b in vec_b) ** 0.5
    if norm_a <= 1e-8 or norm_b <= 1e-8:
        return 0.0
    return max(0.0, min(1.0, dot / (norm_a * norm_b)))
