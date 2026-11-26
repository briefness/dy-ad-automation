"""Adaptive shot-boundary detection and compact local visual descriptors."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


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

    Uses PySceneDetect's ``AdaptiveDetector`` when ``scenedetect`` is installed,
    and otherwise runs an equivalent OpenCV+NumPy adaptive rolling-window HSV
    delta detector that resists flashes, camera shake, and exposure drift far
    better than a fixed global ``gt(scene, threshold)`` threshold.
    """
    source = Path(source)
    if duration <= min_scene_seconds * 1.5:
        return []

    # Tier 1: delegate to PySceneDetect AdaptiveDetector if available
    try:
        from scenedetect import AdaptiveDetector, detect  # type: ignore

        scenes = detect(str(source), AdaptiveDetector(adaptive_threshold=adaptive_threshold))
        raw_cuts = [float(start.get_seconds()) for start, _ in scenes[1:]]
        return _filter_cuts(raw_cuts, duration, min_scene_seconds)
    except Exception:
        pass

    # Tier 2: native OpenCV + NumPy adaptive HSV + luminance rolling-window detector
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
                    # Weighted H/S/V mean absolute difference (PySceneDetect ContentDetector formula)
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
        # Must be a local peak above the rolling neighborhood
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
    """Compute a normalized 48-D visual descriptor (HSV histogram + spatial edge grid).

    Deterministic, zero-GPU, fast to store in ``index.json``, and useful for
    near-duplicate suppression, visual continuity scoring, and alternate-shot retrieval.
    """
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
        color_norm = float(np.linalg.norm(color_vec)) + 1e-6
        color_vec = color_vec / color_norm

        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 60, 150).astype(np.float32) / 255.0
        grid = []
        gh, gw = edges.shape
        for r in range(4):
            for c in range(4):
                cell = edges[
                    r * gh // 4:(r + 1) * gh // 4,
                    c * gw // 4:(c + 1) * gw // 4,
                ]
                grid.append(float(np.mean(cell)) if cell.size else 0.0)
        edge_vec = np.array(grid, dtype=np.float32)
        edge_norm = float(np.linalg.norm(edge_vec)) + 1e-6
        edge_vec = edge_vec / edge_norm

        combined = np.concatenate([color_vec * 0.75, edge_vec * 0.25])
        combined /= float(np.linalg.norm(combined)) + 1e-6
        vectors.append(combined)

    if not vectors:
        return []
    mean_vec = np.mean(vectors, axis=0)
    mean_vec /= float(np.linalg.norm(mean_vec)) + 1e-6
    return [round(float(x), 4) for x in mean_vec.tolist()]


def visual_cosine_similarity(vec_a: Sequence[float], vec_b: Sequence[float]) -> float:
    """Cosine similarity in [0, 1] between two visual descriptor vectors."""
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    dot = sum(float(a) * float(b) for a, b in zip(vec_a, vec_b))
    norm_a = sum(float(a) * float(a) for a in vec_a) ** 0.5
    norm_b = sum(float(b) * float(b) for b in vec_b) ** 0.5
    if norm_a <= 1e-8 or norm_b <= 1e-8:
        return 0.0
    return max(0.0, min(1.0, dot / (norm_a * norm_b)))


def rank_similar_windows(
    target_window: Dict[str, Any],
    windows: Sequence[Dict[str, Any]],
    *,
    limit: int = 6,
    exclude_same_source_overlap: bool = True,
) -> List[Dict[str, Any]]:
    """Rank candidate alternative windows by visual + role similarity for interactive swapping."""
    target_vec = target_window.get("visual_descriptor") or []
    target_role = str(
        target_window.get("selected_role")
        or (target_window.get("analysis") or {}).get("product_story_role")
        or ""
    ).lower()
    target_source = str(target_window.get("source_video") or target_window.get("source_path") or "")
    target_start = float(target_window.get("source_start", target_window.get("start", 0.0)) or 0.0)
    target_end = float(target_window.get("source_end", target_window.get("end", 0.0)) or 0.0)

    scored: List[Tuple[float, Dict[str, Any]]] = []
    for win in windows:
        analysis = win.get("analysis") or {}
        if analysis.get("usable_for_ad") is False:
            continue
        win_source = str(win.get("source_path") or win.get("source_video") or "")
        win_start = float(win.get("start") or 0.0)
        win_end = float(win.get("end") or 0.0)
        if exclude_same_source_overlap and Path(win_source).name == Path(target_source).name:
            overlap = max(0.0, min(target_end, win_end) - max(target_start, win_start))
            if overlap > 0.5:
                continue

        sim = visual_cosine_similarity(target_vec, win.get("visual_descriptor") or [])
        win_role = str(analysis.get("product_story_role") or "").lower()
        role_bonus = 0.35 if target_role and win_role == target_role else 0.0
        conf_bonus = 0.15 * float(analysis.get("confidence") or 0.5)
        score = round(0.50 * sim + role_bonus + conf_bonus, 4)
        scored.append((score, {
            "window_id": win.get("window_id", ""),
            "source_path": str(win.get("source_path") or ""),
            "source_video": str(win.get("source_video") or Path(win_source).name),
            "start": round(win_start, 3),
            "end": round(win_end, 3),
            "duration": round(max(0.0, win_end - win_start), 3),
            "role": win_role or "unknown",
            "summary": str(analysis.get("summary") or analysis.get("visual_summary") or ""),
            "similarity": score,
        }))

    scored.sort(key=lambda item: item[0], reverse=True)
    return [item for _, item in scored[:max(1, limit)]]
