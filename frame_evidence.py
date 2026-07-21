"""Adaptive, auditable frame evidence for local-video understanding."""

from __future__ import annotations

import base64
import html
import json
import math
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


GLOBAL_DUPLICATE_THRESHOLD = 0.06
SCENE_CHANGE_THRESHOLD = 0.10
LOCAL_STATE_THRESHOLD = 0.05
RECENT_FRAME_WINDOW = 4
MAX_CANDIDATES = 72
FRAME_EVIDENCE_VERSION = 1


def _candidate_times(
    start: float,
    end: float,
    frame_count: int,
    preferred_times: Optional[Iterable[float]],
    required_times: Optional[Iterable[float]],
    fps: float,
) -> List[Dict[str, Any]]:
    duration = max(end - start, 0.2)
    count = min(
        MAX_CANDIDATES,
        max(frame_count * 4, math.ceil(duration / 0.25) + 1),
    )
    end_inset = min(max(1.0 / max(fps, 1.0), 0.02), duration * 0.08)
    tagged: List[tuple[float, str]] = [
        (start + min(end_inset, duration / 3), "edge_anchor"),
        (end - min(end_inset, duration / 3), "edge_anchor"),
    ]
    tagged.extend(
        (start + duration * index / max(count - 1, 1), "adaptive_density")
        for index in range(count)
    )
    tagged.extend((float(value), "motion_anchor") for value in (preferred_times or []))
    tagged.extend((float(value), "required_anchor") for value in (required_times or []))
    minimum_gap = max(1.0 / max(fps, 1.0), duration / max(count * 2.0, 1.0))
    merged: List[Dict[str, Any]] = []
    for timestamp, source in sorted(tagged, key=lambda item: item[0]):
        timestamp = max(start, min(end - min(end_inset, duration / 3), timestamp))
        existing = next(
            (
                item
                for item in merged
                if abs(float(item["timestamp"]) - timestamp) < minimum_gap
            ),
            None,
        )
        if existing is not None:
            existing["candidate_sources"].add(source)
            continue
        merged.append({"timestamp": timestamp, "candidate_sources": {source}})
    return merged


def _decode_candidates(
    source: Path,
    candidates: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError("素材帧证据分析需要现有 OpenCV 运行时") from exc

    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        raise RuntimeError(f"无法打开视频素材：{source}")
    decoded: List[Dict[str, Any]] = []
    try:
        for candidate in candidates:
            capture.set(cv2.CAP_PROP_POS_MSEC, float(candidate["timestamp"]) * 1000.0)
            ok, frame = capture.read()
            if not ok or frame is None:
                continue
            height, width = frame.shape[:2]
            scale = min(1.0, 480.0 / max(width, 1))
            if scale < 1.0:
                frame = cv2.resize(
                    frame,
                    (max(1, round(width * scale)), max(1, round(height * scale))),
                    interpolation=cv2.INTER_AREA,
                )
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            coarse = cv2.resize(rgb, (32, 32), interpolation=cv2.INTER_AREA)
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            fine_height = max(64, round(192 * gray.shape[0] / max(gray.shape[1], 1)))
            fine = cv2.resize(gray, (192, fine_height), interpolation=cv2.INTER_AREA)
            edges = cv2.Canny(fine, 55, 145) > 0
            decoded.append({
                **candidate,
                "frame": frame,
                "coarse": coarse,
                "edges": edges,
            })
    finally:
        capture.release()
    if not decoded:
        raise RuntimeError(f"无法从视频素材解码候选帧：{source}")
    return decoded


def _global_difference(left: Any, right: Any) -> float:
    import numpy as np

    difference = np.abs(left.astype(np.float32) - right.astype(np.float32))
    return float(np.mean(np.max(difference, axis=2)) / 255.0)


def _local_state_difference(left: Any, right: Any) -> float:
    import cv2
    import numpy as np

    kernel = np.ones((3, 3), dtype=np.uint8)
    left_u8 = left.astype(np.uint8) * 255
    right_u8 = right.astype(np.uint8) * 255
    left_near = cv2.dilate(left_u8, kernel, iterations=1) > 0
    right_near = cv2.dilate(right_u8, kernel, iterations=1) > 0
    changed = (left & ~right_near) | (right & ~left_near)
    grid_rows, grid_cols = 6, 10
    height, width = changed.shape
    scores = []
    for row in range(grid_rows):
        y0 = round(row * height / grid_rows)
        y1 = round((row + 1) * height / grid_rows)
        for col in range(grid_cols):
            x0 = round(col * width / grid_cols)
            x1 = round((col + 1) * width / grid_cols)
            cell = changed[y0:y1, x0:x1]
            if cell.size:
                scores.append(float(np.count_nonzero(cell)) / float(cell.size))
    return max(scores, default=0.0)


def _evaluate_candidates(candidates: List[Dict[str, Any]], frame_count: int) -> None:
    recent: List[int] = []
    provisional: List[int] = []
    duration = float(candidates[-1]["timestamp"]) - float(candidates[0]["timestamp"])
    density_gap = max(0.45, duration / max(frame_count - 1, 1))
    last_kept_time: Optional[float] = None
    previous = None
    has_required_anchors = any(
        "required_anchor" in candidate["candidate_sources"]
        for candidate in candidates
    )

    for index, candidate in enumerate(candidates):
        global_recent = min(
            (
                _global_difference(candidate["coarse"], candidates[recent_index]["coarse"])
                for recent_index in recent
            ),
            default=1.0,
        )
        local_recent = min(
            (
                _local_state_difference(candidate["edges"], candidates[recent_index]["edges"])
                for recent_index in recent
            ),
            default=1.0,
        )
        raw_change = (
            _global_difference(candidate["coarse"], previous["coarse"])
            if previous is not None else 1.0
        )
        next_candidate = candidates[index + 1] if index + 1 < len(candidates) else None
        next_global = (
            _global_difference(candidate["coarse"], next_candidate["coarse"])
            if next_candidate is not None else 0.0
        )
        next_local = (
            _local_state_difference(candidate["edges"], next_candidate["edges"])
            if next_candidate is not None else 0.0
        )
        settled = next_global <= 0.035 and next_local <= LOCAL_STATE_THRESHOLD * 0.65
        timestamp = float(candidate["timestamp"])
        candidate.update({
            "global_difference": round(global_recent, 4),
            "raw_change": round(raw_change, 4),
            "local_change": round(local_recent, 4),
            "settled": settled,
            "kept": False,
            "selection_reason": "near_duplicate",
            "priority": 0,
        })

        reason = None
        priority = 0
        if "required_anchor" in candidate["candidate_sources"]:
            reason, priority = "required_anchor", 110
        elif index == 0 and not has_required_anchors:
            reason, priority = "first_frame", 100
        elif index == len(candidates) - 1 and not has_required_anchors:
            reason, priority = "final_frame", 100
        elif "motion_anchor" in candidate["candidate_sources"] and (
            global_recent >= GLOBAL_DUPLICATE_THRESHOLD * 0.45
            or local_recent >= LOCAL_STATE_THRESHOLD * 0.7
        ):
            reason, priority = "motion_anchor", 90
        elif raw_change >= SCENE_CHANGE_THRESHOLD and global_recent >= GLOBAL_DUPLICATE_THRESHOLD:
            reason, priority = "scene_change", 85
        elif (
            global_recent < GLOBAL_DUPLICATE_THRESHOLD
            and local_recent >= LOCAL_STATE_THRESHOLD
            and settled
        ):
            reason, priority = "local_state_change", 80
        elif last_kept_time is None or timestamp - last_kept_time >= density_gap:
            reason, priority = "density_floor", 45
        elif not settled and local_recent >= LOCAL_STATE_THRESHOLD:
            candidate["selection_reason"] = "transient_motion"

        if reason is not None:
            candidate["kept"] = True
            candidate["selection_reason"] = reason
            candidate["priority"] = priority
            provisional.append(index)
            recent.append(index)
            recent = recent[-RECENT_FRAME_WINDOW:]
            last_kept_time = timestamp
        previous = candidate

    if len(provisional) <= frame_count:
        return
    required = [
        index
        for index in provisional
        if "required_anchor" in candidates[index]["candidate_sources"]
    ]
    selected = set(required) if required else {provisional[0], provisional[-1]}
    while len(selected) < frame_count:
        remaining = [index for index in provisional if index not in selected]
        if not remaining:
            break
        chosen = max(
            remaining,
            key=lambda index: (
                int(candidates[index]["priority"]),
                min(
                    abs(float(candidates[index]["timestamp"]) - float(candidates[kept]["timestamp"]))
                    for kept in selected
                ),
                float(candidates[index]["global_difference"])
                + float(candidates[index]["local_change"]),
            ),
        )
        selected.add(chosen)
    for index in provisional:
        if index not in selected:
            candidates[index]["kept"] = False
            candidates[index]["selection_reason"] = "capacity_pruned"


def _render_contact_sheet(
    candidates: List[Dict[str, Any]],
    output: Path,
    tile_size: tuple[int, int],
    columns: int,
    jpeg_quality: int,
) -> None:
    from PIL import Image, ImageDraw
    import cv2

    kept = [candidate for candidate in candidates if candidate["kept"]]
    if not kept:
        raise RuntimeError("素材帧证据没有保留任何可渲染画面")
    tile_width, tile_height = tile_size
    columns = max(1, columns)
    rows = math.ceil(len(kept) / columns)
    sheet = Image.new("RGB", (columns * tile_width, rows * tile_height), (20, 20, 20))
    draw = ImageDraw.Draw(sheet)
    for index, candidate in enumerate(kept):
        rgb = cv2.cvtColor(candidate["frame"], cv2.COLOR_BGR2RGB)
        image = Image.fromarray(rgb)
        image.thumbnail((tile_width, tile_height), getattr(Image, "Resampling", Image).LANCZOS)
        x = (index % columns) * tile_width
        y = (index // columns) * tile_height
        sheet.paste(
            image,
            (x + (tile_width - image.width) // 2, y + (tile_height - image.height) // 2),
        )
        label = f"t={candidate['timestamp']:.2f}s {candidate['selection_reason']}"
        label_width = min(tile_width, max(150, 7 * len(label)))
        draw.rectangle((x, y, x + label_width, y + 24), fill=(0, 0, 0))
        draw.text((x + 5, y + 4), label, fill=(255, 255, 255))
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output, "JPEG", quality=jpeg_quality)


def build_contact_sheet_evidence(
    source: Path,
    start: float,
    end: float,
    output: Path,
    frame_count: int,
    preferred_times: Optional[List[float]] = None,
    required_times: Optional[List[float]] = None,
    tile_size: tuple[int, int] = (360, 240),
    columns: int = 4,
    jpeg_quality: int = 88,
) -> Dict[str, Any]:
    """Select, deduplicate, render and explain the frames used for video understanding."""
    source = Path(source)
    output = Path(output)
    if not source.exists() or not source.is_file():
        raise FileNotFoundError(source)
    if start < 0 or end <= start:
        raise ValueError(f"无效素材时间范围：{start}..{end}")
    if frame_count < 2:
        raise ValueError("素材帧证据至少需要 2 帧，才能判断时间变化")
    if len(required_times or []) > frame_count:
        raise ValueError("不可裁剪的素材帧锚点数量不能超过联系表帧预算")
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError("素材帧证据分析需要现有 OpenCV 运行时") from exc

    capture = cv2.VideoCapture(str(source))
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 25.0) if capture.isOpened() else 25.0
    capture.release()
    candidates = _decode_candidates(
        source,
        _candidate_times(start, end, frame_count, preferred_times, required_times, fps),
    )
    _evaluate_candidates(candidates, max(1, frame_count))
    _render_contact_sheet(candidates, output, tile_size, columns, jpeg_quality)
    records = []
    for candidate in candidates:
        thumbnail = candidate["frame"]
        thumb_scale = min(1.0, 96.0 / max(thumbnail.shape[1], 1))
        if thumb_scale < 1.0:
            thumbnail = cv2.resize(
                thumbnail,
                (round(thumbnail.shape[1] * thumb_scale), round(thumbnail.shape[0] * thumb_scale)),
                interpolation=cv2.INTER_AREA,
            )
        encoded, buffer = cv2.imencode(".jpg", thumbnail, [cv2.IMWRITE_JPEG_QUALITY, 35])
        audit_thumbnail = (
            "data:image/jpeg;base64," + base64.b64encode(buffer.tobytes()).decode("ascii")
            if encoded else ""
        )
        records.append({
            "timestamp": round(float(candidate["timestamp"]), 3),
            "candidate_sources": sorted(candidate["candidate_sources"]),
            "kept": bool(candidate["kept"]),
            "selection_reason": str(candidate["selection_reason"]),
            "global_difference": float(candidate["global_difference"]),
            "raw_change": float(candidate["raw_change"]),
            "local_change": float(candidate["local_change"]),
            "settled": bool(candidate["settled"]),
            "audit_thumbnail": audit_thumbnail,
        })
    return {
        "schema_version": FRAME_EVIDENCE_VERSION,
        "method": "adaptive_scene_density_dual_channel_evidence_v1",
        "source_path": str(source),
        "start": round(float(start), 3),
        "end": round(float(end), 3),
        "candidate_count": len(records),
        "kept_count": sum(1 for record in records if record["kept"]),
        "thresholds": {
            "global_duplicate": GLOBAL_DUPLICATE_THRESHOLD,
            "scene_change": SCENE_CHANGE_THRESHOLD,
            "local_state": LOCAL_STATE_THRESHOLD,
            "recent_window": RECENT_FRAME_WINDOW,
        },
        "candidates": records,
    }


def write_frame_evidence_artifacts(
    windows: List[Dict[str, Any]],
    manifest_path: Path,
    report_path: Path,
) -> None:
    """Write one machine-readable ledger and one human audit page for an asset index."""
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": FRAME_EVIDENCE_VERSION,
        "method": "adaptive_scene_density_dual_channel_evidence_v1",
        "windows": [
            {
                "window_id": window.get("window_id"),
                "contact_sheet": window.get("contact_sheet"),
                **{
                    key: value
                    for key, value in (window.get("frame_evidence") or {}).items()
                    if key != "candidates"
                },
                "candidates": [
                    {
                        key: value
                        for key, value in candidate.items()
                        if key != "audit_thumbnail"
                    }
                    for candidate in (window.get("frame_evidence") or {}).get("candidates") or []
                ],
            }
            for window in windows
        ],
    }
    manifest_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    cards = []
    for window in windows:
        evidence = window.get("frame_evidence") or {}
        sheet_path = Path(str(window.get("contact_sheet") or ""))
        sheet_data = ""
        if sheet_path.is_file():
            sheet_data = "data:image/jpeg;base64," + base64.b64encode(sheet_path.read_bytes()).decode("ascii")
        rows = []
        for candidate in evidence.get("candidates") or []:
            state = "保留" if candidate.get("kept") else "丢弃"
            css_class = "kept" if candidate.get("kept") else "dropped"
            rows.append(
                f'<tr class="{css_class}"><td><img class="thumb" src="{candidate.get("audit_thumbnail", "")}" alt="candidate"></td>'
                f'<td>{candidate.get("timestamp", 0):.3f}s</td>'
                f'<td>{state}</td><td>{html.escape(str(candidate.get("selection_reason") or ""))}</td>'
                f'<td>{float(candidate.get("global_difference") or 0):.3f}</td>'
                f'<td>{float(candidate.get("local_change") or 0):.3f}</td></tr>'
            )
        cards.append(
            '<section class="card">'
            f'<h2>{html.escape(str(window.get("window_id") or "unknown"))}</h2>'
            f'<p>候选 {evidence.get("candidate_count", 0)} · 保留 {evidence.get("kept_count", 0)}</p>'
            f'<img src="{sheet_data}" alt="contact sheet">'
            '<table><thead><tr><th>候选帧</th><th>时间</th><th>决策</th><th>原因</th><th>全局差异</th><th>局部变化</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></section>'
        )
    report_path.write_text(
        """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<title>本地素材帧证据审计</title><style>
body{font:14px system-ui;margin:24px;background:#111;color:#eee}h1{margin-bottom:8px}
.card{background:#1c1c1c;border:1px solid #333;border-radius:12px;padding:16px;margin:18px 0}
img{max-width:100%;border-radius:8px;background:#000}.thumb{width:88px;max-height:60px;object-fit:contain}table{width:100%;border-collapse:collapse;margin-top:12px}
th,td{text-align:left;padding:7px;border-bottom:1px solid #333}.kept{color:#9be28f}.dropped{color:#999}
</style><body><h1>本地素材帧证据审计</h1><p>展示每个素材窗口为什么保留或丢弃候选帧。</p>"""
        + "".join(cards)
        + "</body></html>",
        encoding="utf-8",
    )
