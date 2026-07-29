"""Evidence-bound semantic sticker planning and rendering."""

from __future__ import annotations

import copy
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional


VALID_STICKER_MODES = {"auto", "on", "off"}
MAX_STICKER_TEXT_LENGTH = 10
STICKER_KIND_BY_ROLE = {
    "ingredient": "ingredient",
    "origin": "origin",
    "production": "craft",
}
SEMANTIC_CUES_BY_KIND = {
    "origin": ("产地", "原产", "源头", "种植", "生长", "茶园", "茶山", "果园", "农场", "土壤", "海拔", "气候", "环境", "好山好水", "山水环境"),
    "ingredient": ("原料", "配料", "成分", "选料", "选用", "含有"),
    "craft": ("工艺", "制作", "生产", "加工", "萃取", "烘焙", "发酵", "炒制", "烘干", "研磨", "调配", "拼配", "灌装", "蒸煮", "冷泡", "低温"),
}
PRACTICAL_BENEFIT_WEIGHTS = {
    "不用": 3,
    "无需": 3,
    "省事": 3,
    "省时": 3,
    "不麻烦": 3,
    "随身": 2,
    "方便": 2,
    "安心": 2,
    "透明": 2,
    "大小": 2,
    "随时": 1,
    "出门": 1,
    "居家": 1,
}


def resolve_sticker_enabled(
    requested_mode: str,
    *,
    video_style: str = "",
    voiceover_style: str = "",
    script_style: str = "",
) -> bool:
    """Resolve the tri-state sticker switch without changing non-sales styles."""
    mode = str(requested_mode or "auto").strip().lower()
    if mode not in VALID_STICKER_MODES:
        raise ValueError(f"贴图模式必须是 {sorted(VALID_STICKER_MODES)}")
    if mode != "auto":
        return mode == "on"
    style = str(video_style or "").replace(" ", "").lower()
    if any(token in style for token in ("带货", "卖货", "转化")):
        return True
    return (
        str(voiceover_style or "").strip().lower() == "energetic"
        and str(script_style or "").strip().lower() == "demonstration"
    )


def _segment_subtitle(
    subtitles: List[Dict[str, Any]],
    segment_index: int,
) -> Optional[Dict[str, Any]]:
    matches = [
        item for item in subtitles
        if int(item.get("segment", -1)) == segment_index and str(item.get("text") or "").strip()
    ]
    if not matches:
        return None
    return {
        "text": "".join(str(item["text"]).strip() for item in matches),
        "start": min(float(item.get("start") or 0.0) for item in matches),
        "end": max(float(item.get("end") or 0.0) for item in matches),
    }


def _sticker_text(
    kind: str,
    segment: Dict[str, Any],
    product_info: Dict[str, Any],
    *,
    material_verified: bool,
) -> str:
    if kind == "origin":
        return "产地实拍" if material_verified else str(product_info.get("origin") or "")[:MAX_STICKER_TEXT_LENGTH]
    if kind == "ingredient":
        ingredients = product_info.get("ingredients") or []
        source_text = " ".join([
            str(segment.get("subtitle") or ""),
            *[str(item) for item in segment.get("visual_query") or []],
        ])
        for ingredient in ingredients:
            value = str(ingredient or "").strip()
            if value and value in source_text:
                return value[:6] if "原料" in value else f"{value[:4]}原料"
        return "原料实拍"
    if kind == "craft":
        clauses = [
            re.sub(r"[^\w\u4e00-\u9fff]", "", value)
            for value in re.split(
                r"[，。！？；,.!?;]",
                str(segment.get("subtitle") or segment.get("voiceover") or ""),
            )
        ]
        specific = next(
            (
                value
                for value in clauses
                if any(cue in value for cue in SEMANTIC_CUES_BY_KIND["craft"])
            ),
            "",
        )
        if specific:
            return specific[:MAX_STICKER_TEXT_LENGTH]
        processes = product_info.get("production_process") or []
        return str(processes[0] if processes else "")[:MAX_STICKER_TEXT_LENGTH]
    if kind in {"usage", "cta", "purchase_reason", "proof"}:
        script_text = str(segment.get("subtitle") or segment.get("voiceover") or "")
        clauses = [
            re.sub(r"[^\w\u4e00-\u9fff]", "", value)
            for value in re.split(r"[，。！？；,.!?;]", script_text)
        ]
        if kind == "cta":
            actions = ("购买", "下单", "点击", "点下方", "了解", "试试", "体验")
            action_clause = next(
                (
                    value
                    for token in actions
                    for value in clauses
                    if 2 <= len(value) <= MAX_STICKER_TEXT_LENGTH and token in value
                ),
                "",
            )
            if action_clause:
                return action_clause
        if kind in {"usage", "purchase_reason"}:
            ranked = [
                (
                    sum(
                        weight
                        for cue, weight in PRACTICAL_BENEFIT_WEIGHTS.items()
                        if cue in value
                    ),
                    -index,
                    value,
                )
                for index, value in enumerate(clauses)
                if 2 <= len(value) <= MAX_STICKER_TEXT_LENGTH
            ]
            best = max(ranked, default=(0, 0, ""))
            return best[2] if best[0] > 0 else ""
        return next(
            (value for value in clauses if 2 <= len(value) <= MAX_STICKER_TEXT_LENGTH),
            "",
        )
    return ""


def build_semantic_sticker_plan(
    *,
    ad_script: Dict[str, Any],
    subtitles: List[Dict[str, Any]],
    selected_segments: Optional[List[Dict[str, Any]]] = None,
    segment_timeline: Optional[List[Dict[str, Any]]] = None,
    product_info: Optional[Dict[str, Any]] = None,
    requested_mode: str = "auto",
    video_style: str = "",
    voiceover_style: str = "",
    script_style: str = "",
    preference_policy: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build an auditable plan from spoken copy intersected with selected material roles."""
    enabled = resolve_sticker_enabled(
        requested_mode,
        video_style=video_style,
        voiceover_style=voiceover_style,
        script_style=script_style,
    )
    plan: Dict[str, Any] = {
        "version": 1,
        "requested_mode": requested_mode,
        "enabled": enabled,
        "policy": {
            "copy_source": "subtitle_script",
            "evidence_policy": "subtitle_and_verified_material_or_product_fact",
            "max_simultaneous": 1,
            "sound_effects": False,
            "learning_source": "explicit_user_feedback_only",
        },
        "items": [],
        "skipped": [],
        "layouts": {},
        "learning": {
            "source": "explicit_user_feedback_only",
            "policy_fingerprint": str((preference_policy or {}).get("fingerprint") or ""),
            "active_rules": [
                rule for rule in (preference_policy or {}).get("rules") or []
                if rule.get("status") == "active"
            ],
        },
    }
    if not enabled:
        return plan

    verified_roles = {
        (int(item.get("semantic_segment", item.get("segment", -1))), str(item.get("product_story_role") or ""))
        for item in selected_segments or []
    }
    timeline_by_segment = {
        int(item.get("index", item.get("segment", -1))): item
        for item in segment_timeline or []
    }
    product = product_info or {}
    for position, segment in enumerate(ad_script.get("segments") or []):
        segment_index = int(segment.get("segment", position))
        role = str(segment.get("product_story_role") or "")
        narrative = str(segment.get("narrative") or "").lower()
        marketing_intent = str(segment.get("marketing_intent") or "").lower()
        marketing_device = str(segment.get("marketing_device") or "").lower()
        segment_evidence_refs = [
            str(ref).strip()
            for ref in segment.get("evidence_refs") or []
            if str(ref).strip()
        ]
        has_fact_evidence = bool(segment.get("claims") or segment_evidence_refs)
        has_verified_selling_point = bool(
            segment.get("claims")
            or any(ref not in {"product:name", "visual.product_identity"} for ref in segment_evidence_refs)
        )
        if "cta" in {narrative, marketing_intent}:
            kind = "cta"
        elif "proof" in {narrative, marketing_device} and has_fact_evidence:
            kind = "proof"
        elif role in STICKER_KIND_BY_ROLE:
            kind = STICKER_KIND_BY_ROLE[role]
        elif role == "usage":
            kind = "purchase_reason"
        elif role == "finished_product" and marketing_intent in {"value", "benefit"}:
            kind = "purchase_reason"
        else:
            kind = None
        subtitle = _segment_subtitle(subtitles, segment_index)
        if not kind or not subtitle:
            continue
        if kind == "cta":
            plan["skipped"].append({
                "segment": segment_index,
                "kind": kind,
                "reason": "not_decision_information",
            })
            continue
        if kind == "purchase_reason" and not has_verified_selling_point:
            plan["skipped"].append({
                "segment": segment_index,
                "kind": kind,
                "reason": "verified_selling_point_required",
            })
            continue
        if selected_segments is not None and (segment_index, role) not in verified_roles:
            plan["skipped"].append({
                "segment": segment_index,
                "kind": kind,
                "reason": "selected_material_role_not_verified",
            })
            continue
        if selected_segments is None and kind in {"origin", "ingredient", "craft"}:
            trusted_fact = {
                "origin": product.get("origin"),
                "ingredient": product.get("ingredients"),
                "craft": product.get("production_process"),
            }[kind]
            if not trusted_fact and not segment.get("evidence_refs"):
                plan["skipped"].append({
                    "segment": segment_index,
                    "kind": kind,
                    "reason": "trusted_product_fact_required",
                })
                continue
        if kind in SEMANTIC_CUES_BY_KIND:
            semantic_text = f"{subtitle['text']}{segment.get('subtitle') or ''}"
            if not any(cue in semantic_text for cue in SEMANTIC_CUES_BY_KIND[kind]):
                plan["skipped"].append({
                    "segment": segment_index,
                    "kind": kind,
                    "reason": "subtitle_semantic_mismatch",
                })
                continue
        text = re.sub(
            r"\s+",
            "",
            _sticker_text(
                kind,
                segment,
                product,
                material_verified=selected_segments is not None,
            ),
        )[:MAX_STICKER_TEXT_LENGTH]
        if not text:
            if kind in {"usage", "purchase_reason"}:
                plan["skipped"].append({
                    "segment": segment_index,
                    "kind": kind,
                    "reason": "no_value_bearing_copy",
                })
            continue
        timeline = timeline_by_segment.get(segment_index) or {}
        timeline_start = float(timeline.get("start") or 0.0)
        timeline_end = float(timeline.get("end") or 0.0)
        start = round(max(0.0, timeline_start + 0.05, float(subtitle["start"]) - 0.15), 3)
        spoken_end = float(subtitle["end"])
        read_end = start + 0.8 + len(text) * 0.12
        end = max(spoken_end, read_end)
        if timeline_end > timeline_start:
            end = min(end, timeline_end - 0.1)
        if end - start < 1.2:
            plan["skipped"].append({
                "segment": segment_index,
                "kind": kind,
                "reason": "insufficient_readable_time",
            })
            continue
        evidence_refs = [f"subtitle:segment:{segment_index}"]
        evidence_refs.extend(str(ref) for ref in segment.get("evidence_refs") or [])
        if selected_segments is not None:
            evidence_refs.append(f"material_role:{role}")
        elif kind in {"origin", "ingredient", "craft"}:
            evidence_refs.append(f"product_fact:{kind}")
        plan["items"].append({
            "id": f"sticker-segment-{segment_index}-{kind}",
            "segment": segment_index,
            "kind": kind,
            "text": text,
            "start": start,
            "end": round(end, 3),
            "evidence_refs": list(dict.fromkeys(evidence_refs)),
            "source_subtitle": str(subtitle["text"]),
            "render_mode": "label",
            "status": "planned",
        })
    total_duration = max((float(item.get("end") or 0.0) for item in subtitles), default=0.0)
    density_limit = max(1, min(5, round(total_duration / 5.0)))
    if len(plan["items"]) > density_limit:
        priority = {
            "cta": 7,
            "proof": 6,
            "purchase_reason": 6,
            "ingredient": 5,
            "origin": 5,
            "usage": 4,
            "craft": 3,
        }
        if preference_policy:
            from sticker_feedback import sticker_preference_score

            preference_scores = {
                str(item["id"]): sticker_preference_score(item, preference_policy)
                for item in plan["items"]
            }
        else:
            preference_scores = {}
        ranked = sorted(
            plan["items"],
            key=lambda item: (
                -(
                    priority.get(str(item["kind"]), 0)
                    + preference_scores.get(str(item["id"]), 0.0) * 10.0
                ),
                float(item["start"]),
            ),
        )
        kept_ids = {str(item["id"]) for item in ranked[:density_limit]}
        dropped = [item for item in plan["items"] if str(item["id"]) not in kept_ids]
        plan["items"] = sorted(
            (item for item in plan["items"] if str(item["id"]) in kept_ids),
            key=lambda item: float(item["start"]),
        )
        plan["skipped"].extend({
            "segment": int(item["segment"]),
            "kind": str(item["kind"]),
            "reason": "density_limit",
        } for item in dropped)
    return plan


def _probe_video(video: Path) -> tuple[int, int, float]:
    probe = subprocess.run([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height:format=duration",
        "-of", "json", str(video),
    ], check=True, capture_output=True, text=True, timeout=15)
    payload = json.loads(probe.stdout)
    stream = payload["streams"][0]
    return int(stream["width"]), int(stream["height"]), float(payload["format"]["duration"])


def _render_label_image(
    text: str,
    kind: str,
    video_width: int,
    video_height: int,
    primary_color: str,
    accent_color: str,
    output: Path,
    asset_dir: Optional[Path] = None,
) -> tuple[int, int, str]:
    from PIL import Image, ImageColor, ImageDraw, ImageFont
    from video_merger import find_system_font

    portrait = video_height >= video_width
    font_size = max(22, round(video_height * (0.029 if portrait else 0.043)))
    font_path = find_system_font()
    if not font_path:
        raise RuntimeError("贴图渲染需要可用中文字体")
    draw_probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    max_width = round(video_width * (0.48 if portrait else 0.36))
    while True:
        font = ImageFont.truetype(font_path, font_size)
        text_box = draw_probe.textbbox((0, 0), text, font=font, stroke_width=1)
        text_width = text_box[2] - text_box[0]
        text_height = text_box[3] - text_box[1]
        padding_x = max(12, round(font_size * 0.48))
        padding_y = max(8, round(font_size * 0.34))
        icon_size = max(text_height + padding_y, round(font_size * 1.35))
        width = icon_size + text_width + padding_x * 3
        if width <= max_width or font_size <= 12:
            break
        font_size -= 1
    icon_font = ImageFont.truetype(font_path, max(12, round(font_size * 0.72)))
    height = icon_size + padding_y * 2
    image = Image.new("RGBA", (width + 8, height + 8), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    primary = ImageColor.getrgb(primary_color)
    accent = ImageColor.getrgb(accent_color)
    badge_color = primary if kind in {"cta", "purchase_reason"} else accent
    draw.rounded_rectangle(
        (5, 7, width + 3, height + 5),
        radius=min(8, height // 5),
        fill=(8, 18, 28, 52),
    )
    draw.rounded_rectangle(
        (4, 3, width + 3, height + 1),
        radius=min(8, height // 5),
        fill=(248, 250, 252, 232),
        outline=(255, 255, 255, 238),
        width=max(1, round(video_width * 0.0015)),
    )
    draw.line(
        (12, height, width - 5, height),
        fill=badge_color + (220,),
        width=max(2, round(video_width * 0.002)),
    )
    icon_center = (4 + padding_x + icon_size // 2, 4 + height // 2)
    draw.ellipse(
        (
            icon_center[0] - icon_size // 2,
            icon_center[1] - icon_size // 2,
            icon_center[0] + icon_size // 2,
            icon_center[1] + icon_size // 2,
        ),
        fill=badge_color + (244,),
    )
    custom_asset = next(
        (
            Path(asset_dir) / f"{kind}{suffix}"
            for suffix in (".png", ".webp")
            if asset_dir and (Path(asset_dir) / f"{kind}{suffix}").is_file()
        ),
        None,
    )
    if custom_asset:
        with Image.open(custom_asset) as source_icon:
            icon = source_icon.convert("RGBA")
        max_icon = round(icon_size * 0.72)
        icon.thumbnail((max_icon, max_icon), Image.Resampling.LANCZOS)
        image.alpha_composite(
            icon,
            (
                round(icon_center[0] - icon.width / 2),
                round(icon_center[1] - icon.height / 2),
            ),
        )
    else:
        glyph = {
            "purchase_reason": "选",
            "ingredient": "料",
            "origin": "源",
            "craft": "艺",
            "usage": "用",
            "proof": "证",
            "cta": "购",
        }.get(kind, "荐")
        glyph_box = draw.textbbox((0, 0), glyph, font=icon_font)
        draw.text(
            (
                icon_center[0] - (glyph_box[2] - glyph_box[0]) / 2,
                icon_center[1] - (glyph_box[3] - glyph_box[1]) / 2 - glyph_box[1],
            ),
            glyph,
            font=icon_font,
            fill=(255, 255, 255, 255),
        )
    text_x = 4 + padding_x * 2 + icon_size
    text_y = 4 + (height - text_height) / 2 - text_box[1]
    draw.text(
        (text_x, text_y),
        text,
        font=font,
        fill=(28, 32, 38, 255),
    )
    image.save(output, "PNG")
    return image.width, image.height, str(custom_asset or "builtin")


def _rects_overlap(left: tuple[float, float, float, float], right: tuple[float, float, float, float]) -> bool:
    lx, ly, lw, lh = left
    rx, ry, rw, rh = right
    return lx < rx + rw and lx + lw > rx and ly < ry + rh and ly + lh > ry


def _sample_interval_frames(video: Path, start: float, end: float) -> List[Any]:
    import cv2

    capture = cv2.VideoCapture(str(video))
    if not capture.isOpened():
        return []
    frames = []
    try:
        for timestamp in (start + 0.12, (start + end) / 2.0, max(start, end - 0.12)):
            capture.set(cv2.CAP_PROP_POS_MSEC, max(0.0, timestamp) * 1000.0)
            ok, frame = capture.read()
            if ok and frame is not None:
                frames.append(frame)
    finally:
        capture.release()
    return frames


def _safe_position(
    frames: List[Any],
    sticker_width: int,
    sticker_height: int,
    video_width: int,
    video_height: int,
    subtitle_bottom_ratio: float,
    logo_position: str,
    logo_enabled: bool,
    previous_position: str = "",
) -> Optional[Dict[str, Any]]:
    import cv2
    import numpy as np

    margin_x = round(video_width * 0.04)
    margin_y = round(video_height * 0.055)
    right_x = video_width - margin_x - sticker_width
    center_x = round((video_width - sticker_width) / 2)
    candidates = [
        ("top_left", margin_x, margin_y),
        ("top_center", center_x, margin_y),
        ("top_right", right_x, margin_y),
    ]
    if video_width > video_height:
        candidates.extend([
            ("upper_left", margin_x, round(video_height * 0.24)),
            ("upper_right", right_x, round(video_height * 0.24)),
            ("middle_left", margin_x, round(video_height * 0.43)),
            ("middle_right", right_x, round(video_height * 0.43)),
        ])
    subtitle_top = video_height * (1.0 - max(0.18, subtitle_bottom_ratio) - 0.04)
    logo_rects = {
        "top_left": (0.0, 0.0, 0.30, 0.18),
        "top_right": (0.70, 0.0, 0.30, 0.18),
        "bottom_left": (0.0, 0.78, 0.30, 0.22),
        "bottom_right": (0.70, 0.78, 0.30, 0.22),
    }
    logo_rect = logo_rects.get(str(logo_position or "top_right")) if logo_enabled else None
    face_cascade = cv2.CascadeClassifier(
        str(Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml")
    )
    scored = []
    for name, x, y in candidates:
        normalized_rect = (
            x / video_width,
            y / video_height,
            sticker_width / video_width,
            sticker_height / video_height,
        )
        if y < 0 or x < 0 or y + sticker_height >= subtitle_top:
            continue
        if logo_rect and _rects_overlap(normalized_rect, logo_rect):
            continue
        frame_scores = []
        occupied_by_face = False
        previous_roi = None
        for frame in frames:
            roi = frame[y:y + sticker_height, x:x + sticker_width]
            if roi.size == 0:
                frame_scores.append(1.0)
                continue
            gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
            edge_density = float(np.mean(cv2.Canny(gray, 70, 160) > 0))
            contrast = min(1.0, float(np.std(gray)) / 96.0)
            motion = 0.0
            if previous_roi is not None and previous_roi.shape == gray.shape:
                motion = min(1.0, float(np.mean(cv2.absdiff(previous_roi, gray))) / 64.0)
            previous_roi = gray
            frame_scores.append(edge_density * 0.5 + contrast * 0.3 + motion * 0.2)
            if not face_cascade.empty():
                faces = face_cascade.detectMultiScale(
                    cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY),
                    scaleFactor=1.15,
                    minNeighbors=5,
                    minSize=(max(20, video_width // 18), max(20, video_width // 18)),
                )
                for fx, fy, fw, fh in faces:
                    if _rects_overlap((x, y, sticker_width, sticker_height), (fx, fy, fw, fh)):
                        occupied_by_face = True
                        break
        score = max(frame_scores, default=0.0) + (1.0 if occupied_by_face else 0.0)
        scored.append((score, name, x, y, normalized_rect))
    safe_scored = [item for item in scored if item[0] <= 0.45]
    if not safe_scored:
        return None
    varied_scored = [item for item in safe_scored if item[1] != previous_position]
    score, name, x, y, normalized_rect = min(
        varied_scored or safe_scored,
        key=lambda item: item[0],
    )
    return {
        "position": name,
        "x": x,
        "y": y,
        "rect": [round(value, 4) for value in normalized_rect],
        "safe_score": round(score, 4),
    }


def render_semantic_sticker_plan(
    *,
    video: Path,
    output: Path,
    plan: Dict[str, Any],
    primary_color: str = "#FF6B6B",
    accent_color: str = "#4ECDC4",
    subtitle_bottom_ratio: float = 0.22,
    logo_position: str = "top_right",
    logo_enabled: bool = False,
    asset_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Render planned stickers with per-aspect, multi-frame safe-area placement."""
    video = Path(video)
    output = Path(output)
    if not video.is_file():
        raise FileNotFoundError(video)
    rendered = copy.deepcopy(plan)
    video_width, video_height, duration = _probe_video(video)
    aspect_key = "9:16" if video_height >= video_width else "16:9"
    layouts: List[Dict[str, Any]] = []
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="semantic-stickers-") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        accepted = []
        previous_position = ""
        for index, item in enumerate(rendered.get("items") or []):
            start = max(0.0, float(item.get("start") or 0.0))
            end = min(duration, float(item.get("end") or 0.0))
            if end - start < 0.2:
                rendered.setdefault("skipped", []).append({
                    "segment": item.get("segment"),
                    "kind": item.get("kind"),
                    "reason": "invalid_render_interval",
                })
                continue
            if any(start < other_end and end > other_start for other_start, other_end in (
                (float(entry["item"]["start"]), float(entry["item"]["end"])) for entry in accepted
            )):
                rendered.setdefault("skipped", []).append({
                    "segment": item.get("segment"),
                    "kind": item.get("kind"),
                    "reason": "max_simultaneous_reached",
                })
                continue
            image_path = temp_dir / f"sticker_{index:02d}.png"
            sticker_width, sticker_height, asset_source = _render_label_image(
                str(item.get("text") or "")[:MAX_STICKER_TEXT_LENGTH],
                str(item.get("kind") or ""),
                video_width,
                video_height,
                primary_color,
                accent_color,
                image_path,
                asset_dir=asset_dir,
            )
            placement = _safe_position(
                _sample_interval_frames(video, start, end),
                sticker_width,
                sticker_height,
                video_width,
                video_height,
                subtitle_bottom_ratio,
                logo_position,
                logo_enabled,
                previous_position,
            )
            if placement is None:
                rendered.setdefault("skipped", []).append({
                    "segment": item.get("segment"),
                    "kind": item.get("kind"),
                    "reason": "no_safe_visual_region",
                })
                item["status"] = "skipped"
                continue
            accepted.append({"item": item, "image": image_path, "placement": placement})
            previous_position = str(placement["position"])
            item["status"] = "rendered"
            layouts.append({
                "sticker_id": str(item.get("id") or ""),
                "position": placement["position"],
                "rect": placement["rect"],
                "safe_score": placement["safe_score"],
                "asset_source": asset_source,
                "start": round(start, 3),
                "end": round(end, 3),
                "render_status": "rendered",
            })
        rendered.setdefault("layouts", {})[aspect_key] = layouts
        if not accepted:
            shutil.copy2(video, output)
            return rendered

        command = ["ffmpeg", "-y", "-i", str(video)]
        for entry in accepted:
            command.extend(["-loop", "1", "-framerate", "24", "-i", str(entry["image"])])
        filters = []
        previous = "[0:v]"
        for index, entry in enumerate(accepted):
            item = entry["item"]
            placement = entry["placement"]
            start = max(0.0, float(item["start"]))
            end = min(duration, float(item["end"]))
            fade = min(0.2, max(0.08, (end - start) * 0.12))
            sticker_label = f"st{index}"
            output_label = f"v{index}"
            filters.append(
                f"[{index + 1}:v]format=rgba,"
                f"fade=t=in:st={start:.3f}:d={fade:.3f}:alpha=1,"
                f"fade=t=out:st={max(start, end - fade):.3f}:d={fade:.3f}:alpha=1"
                f"[{sticker_label}]"
            )
            filters.append(
                f"{previous}[{sticker_label}]overlay="
                f"x={int(placement['x'])}:y={int(placement['y'])}:"
                f"enable='between(t,{start:.3f},{end:.3f})'[{output_label}]"
            )
            previous = f"[{output_label}]"
        command.extend([
            "-filter_complex", ";".join(filters),
            "-map", previous,
            "-map", "0:a?",
            "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-pix_fmt", "yuv420p", "-c:a", "copy",
            "-t", f"{duration:.3f}", "-movflags", "+faststart", str(output),
        ])
        result = subprocess.run(command, capture_output=True, text=True, timeout=300)
        if result.returncode != 0 or not output.is_file():
            output.unlink(missing_ok=True)
            raise RuntimeError(f"贴图渲染失败：{result.stderr[-1000:]}")
    return rendered


def apply_sticker_plan_to_videos(
    *,
    videos: Dict[str, Path],
    plan: Dict[str, Any],
    plan_path: Path,
    render_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Apply a plan transactionally; a render failure never replaces an existing video."""
    result = copy.deepcopy(plan)
    result.setdefault("layouts", {})
    result.setdefault("skipped", [])
    if result.get("enabled", True) and result.get("items"):
        for requested_aspect, video in videos.items():
            video = Path(video)
            if not video.is_file():
                continue
            temporary = video.with_name(f".{video.stem}.stickers{video.suffix}")
            baseline_skipped = len(result["skipped"])
            try:
                rendered = render_semantic_sticker_plan(
                    video=video,
                    output=temporary,
                    plan=result,
                    **(render_config or {}),
                )
                aspect_layouts = rendered.get("layouts") or {}
                resolved_aspect = next(iter(aspect_layouts), requested_aspect)
                result["layouts"][requested_aspect] = aspect_layouts.get(resolved_aspect, [])
                rendered_items = {
                    str(item.get("id") or ""): item
                    for item in rendered.get("items") or []
                }
                for item in result.get("items") or []:
                    rendered_status = str(
                        rendered_items.get(str(item.get("id") or ""), {}).get("status") or ""
                    )
                    if rendered_status == "rendered" or item.get("status") != "rendered":
                        item["status"] = rendered_status or item.get("status", "planned")
                new_skips = (rendered.get("skipped") or [])[baseline_skipped:]
                result["skipped"].extend({**item, "aspect": requested_aspect} for item in new_skips)
                temporary.replace(video)
            except Exception as error:
                temporary.unlink(missing_ok=True)
                result["skipped"].append({
                    "aspect": requested_aspect,
                    "reason": "render_failure",
                    "error": str(error),
                })
    plan_path = Path(plan_path)
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
