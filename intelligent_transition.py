#!/usr/bin/env python3
"""Deterministic, content-aware transition planning with verified learning."""

from __future__ import annotations

import json
import math
import sqlite3
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


class IntelligentTransitionError(RuntimeError):
    """Raised when no transition plan reaches the release-quality threshold."""


FRAME_WIDTH = 160
FRAME_HEIGHT = 284
FPS = 30
MIN_RENDER_SCORE = 0.58
MIN_COMBINED_SCORE = 0.60
VERIFIED_SOURCES = {"human", "production_quality_gate", "transition_render_gate"}


def _run(cmd: List[str], timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=True)


def _duration(path: Path) -> float:
    result = _run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ], timeout=15)
    return float(result.stdout.strip())


def _extract_rgb_frame(path: Path, time_sec: float) -> bytes:
    result = subprocess.run([
        "ffmpeg", "-v", "error", "-ss", f"{max(0.0, time_sec):.3f}",
        "-i", str(path), "-frames:v", "1",
        "-vf", (
            f"scale={FRAME_WIDTH}:{FRAME_HEIGHT}:force_original_aspect_ratio=decrease,"
            f"pad={FRAME_WIDTH}:{FRAME_HEIGHT}:(ow-iw)/2:(oh-ih)/2:black"
        ),
        "-pix_fmt", "rgb24", "-f", "rawvideo", "-",
    ], capture_output=True, timeout=30, check=True)
    expected = FRAME_WIDTH * FRAME_HEIGHT * 3
    if len(result.stdout) != expected:
        raise IntelligentTransitionError(f"无法读取镜头边界帧：{path.name}")
    return result.stdout


def _frame_stats(rgb: bytes) -> Dict[str, Any]:
    pixels = len(rgb) // 3
    gray = [0] * pixels
    red = green = blue = brightness = 0.0
    for i in range(pixels):
        r, g, b = rgb[i * 3:i * 3 + 3]
        red += r
        green += g
        blue += b
        value = 0.299 * r + 0.587 * g + 0.114 * b
        gray[i] = int(value)
        brightness += value

    edge_weight = edge_x = edge_y = 0.0
    edge_count = 0
    for y in range(1, FRAME_HEIGHT - 1, 2):
        row = y * FRAME_WIDTH
        for x in range(1, FRAME_WIDTH - 1, 2):
            idx = row + x
            edge = abs(gray[idx + 1] - gray[idx - 1]) + abs(
                gray[idx + FRAME_WIDTH] - gray[idx - FRAME_WIDTH]
            )
            if edge < 36:
                continue
            edge_count += 1
            edge_weight += edge
            edge_x += edge * x
            edge_y += edge * y

    if edge_weight:
        subject_x = edge_x / edge_weight / FRAME_WIDTH
        subject_y = edge_y / edge_weight / FRAME_HEIGHT
    else:
        subject_x = subject_y = 0.5

    sampled = ((FRAME_WIDTH - 2) // 2) * ((FRAME_HEIGHT - 2) // 2)
    return {
        "brightness": brightness / pixels,
        "mean_rgb": (red / pixels, green / pixels, blue / pixels),
        "subject_x": subject_x,
        "subject_y": subject_y,
        "edge_occupancy": edge_count / max(sampled, 1),
        "black": brightness / pixels < 8.0,
        "gray": gray,
    }


def _frame_delta(left: Dict[str, Any], right: Dict[str, Any]) -> float:
    return sum(abs(a - b) for a, b in zip(left["gray"], right["gray"])) / (
        len(left["gray"]) * 255.0
    )


def _motion_vector(first: Dict[str, Any], second: Dict[str, Any]) -> Tuple[float, float]:
    return (
        second["subject_x"] - first["subject_x"],
        second["subject_y"] - first["subject_y"],
    )


def _vector_length(vector: Tuple[float, float]) -> float:
    return math.hypot(*vector)


def _motion_alignment(left: Tuple[float, float], right: Tuple[float, float]) -> float:
    left_len = _vector_length(left)
    right_len = _vector_length(right)
    if left_len < 0.015 and right_len < 0.015:
        return 1.0
    if left_len < 0.015 or right_len < 0.015:
        return 0.45
    cosine = (left[0] * right[0] + left[1] * right[1]) / (left_len * right_len)
    return max(0.0, min(1.0, (cosine + 1.0) / 2.0))


def _motion_direction(vector: Tuple[float, float], alignment: float) -> str:
    if _vector_length(vector) < 0.018:
        return "static"
    if alignment < 0.35:
        return "mixed"
    if abs(vector[0]) >= abs(vector[1]):
        return "right" if vector[0] > 0 else "left"
    return "down" if vector[1] > 0 else "up"


def _normalize_narrative(value: str) -> str:
    aliases = {
        "product_showcase": "showcase", "usage_demo": "showcase", "demo": "showcase",
        "detail": "showcase", "before": "hook", "intro": "hook", "after": "result",
        "effect": "result", "proof": "result", "call_to_action": "cta",
    }
    normalized = str(value or "showcase").strip().lower()
    return aliases.get(normalized, normalized)


def analyze_transition_boundary(
    left_clip: Path,
    right_clip: Path,
    from_narrative: str,
    to_narrative: str,
    style: str,
) -> Dict[str, Any]:
    """Measure the visible boundary instead of inferring a transition from labels alone."""
    left_duration = _duration(left_clip)
    right_duration = _duration(right_clip)
    left_early = _frame_stats(_extract_rgb_frame(left_clip, max(0.0, left_duration - 0.55)))
    left_late = _frame_stats(_extract_rgb_frame(left_clip, max(0.0, left_duration - 0.08)))
    right_early = _frame_stats(_extract_rgb_frame(right_clip, min(0.08, right_duration * 0.1)))
    right_late = _frame_stats(_extract_rgb_frame(right_clip, min(0.55, right_duration * 0.7)))

    left_motion = _motion_vector(left_early, left_late)
    right_motion = _motion_vector(right_early, right_late)
    alignment = _motion_alignment(left_motion, right_motion)
    combined_motion = (
        (left_motion[0] + right_motion[0]) / 2.0,
        (left_motion[1] + right_motion[1]) / 2.0,
    )
    subject_distance = math.hypot(
        left_late["subject_x"] - right_early["subject_x"],
        left_late["subject_y"] - right_early["subject_y"],
    )
    occupancy_delta = abs(left_late["edge_occupancy"] - right_early["edge_occupancy"])
    composition_similarity = max(0.0, 1.0 - min(1.0, subject_distance * 1.35 + occupancy_delta))
    brightness_delta = abs(left_late["brightness"] - right_early["brightness"]) / 255.0
    color_delta = sum(
        abs(a - b) for a, b in zip(left_late["mean_rgb"], right_early["mean_rgb"])
    ) / (3.0 * 255.0)
    boundary_delta = _frame_delta(left_late, right_early)
    scene_difference = min(
        1.0,
        boundary_delta * 0.60 + color_delta * 0.20 + (1.0 - composition_similarity) * 0.20,
    )
    motion_speed = min(1.0, (_vector_length(left_motion) + _vector_length(right_motion)) * 3.0)
    centeredness = max(0.0, 1.0 - math.hypot(
        right_early["subject_x"] - 0.5,
        right_early["subject_y"] - 0.5,
    ) * 1.8)

    return {
        "subject_distance": round(subject_distance, 4),
        "subject_centeredness": round(centeredness, 4),
        "composition_similarity": round(composition_similarity, 4),
        "motion_alignment": round(alignment, 4),
        "motion_speed": round(motion_speed, 4),
        "motion_direction": _motion_direction(combined_motion, alignment),
        "brightness_delta": round(brightness_delta, 4),
        "color_delta": round(color_delta, 4),
        "scene_difference": round(scene_difference, 4),
        "narrative_pair": f"{_normalize_narrative(from_narrative)}->{_normalize_narrative(to_narrative)}",
        "style": style if style in {"fast", "moderate", "cinematic"} else "moderate",
    }


def feature_bucket(features: Dict[str, Any]) -> str:
    scene = "low" if features["scene_difference"] < 0.3 else "medium" if features["scene_difference"] < 0.6 else "high"
    motion = "static" if features["motion_speed"] < 0.15 else features["motion_direction"]
    narrative = features["narrative_pair"].replace("->", "_")
    return f"{scene}_{motion}_{narrative}_{features['style']}"


def _candidate_names(features: Dict[str, Any]) -> List[str]:
    direction = features.get("motion_direction")
    directional = {
        "left": ["slideleft", "wipeleft"],
        "right": ["slideright", "wiperight"],
        "up": ["slideup"],
        "down": ["slidedown"],
    }.get(direction, [])
    return list(dict.fromkeys([
        "none", "cut", "dissolve", "fade", *directional,
        "rectcrop", "circlecrop", "zoomin", "fadeblack", "fadewhite",
    ]))


def score_transition_candidates(
    features: Dict[str, Any],
    learning_bonuses: Optional[Dict[str, float]] = None,
) -> List[Dict[str, Any]]:
    """Score candidates deterministically from content, narrative, rhythm and verified history."""
    learning_bonuses = learning_bonuses or {}
    composition = float(features["composition_similarity"])
    motion_alignment = float(features["motion_alignment"])
    motion_speed = float(features["motion_speed"])
    brightness_stability = 1.0 - float(features["brightness_delta"])
    color_stability = 1.0 - float(features["color_delta"])
    scene_difference = float(features["scene_difference"])
    centeredness = float(features.get("subject_centeredness", 0.5))
    pair = str(features["narrative_pair"])
    style = str(features["style"])
    direction = str(features["motion_direction"])

    scores = []
    for name in _candidate_names(features):
        category = (
            "none" if name == "none" else "cut" if name == "cut" else "fade" if name in {"fade", "dissolve", "fadeblack", "fadewhite"}
            else "directional" if name.startswith("slide") or name.startswith("wipe")
            else "mask"
        )
        if category == "none":
            visual = 0.42 * composition + 0.24 * motion_alignment + 0.20 * brightness_stability + 0.14 * color_stability
        elif category == "cut":
            visual = 0.45 * composition + 0.25 * motion_alignment + 0.20 * (1.0 - scene_difference) + 0.10 * brightness_stability
        elif category == "fade":
            dramatic = name in {"fadeblack", "fadewhite"}
            visual = 0.28 * brightness_stability + 0.24 * color_stability + 0.28 * scene_difference + 0.20 * (1.0 - motion_speed)
            if dramatic:
                visual += 0.08 if pair.endswith("->cta") or pair.startswith("result->") else -0.10
        elif category == "directional":
            matches_direction = (
                direction in name or
                (direction == "left" and name == "wipeleft") or
                (direction == "right" and name == "wiperight")
            )
            visual = 0.40 * motion_alignment + 0.30 * motion_speed + 0.20 * composition + (0.10 if matches_direction else -0.18)
        else:
            visual = 0.38 * centeredness + 0.30 * scene_difference + 0.20 * (1.0 - motion_speed) + 0.12 * color_stability
            if name == "zoomin" and pair in {"hook->showcase", "showcase->result"}:
                visual += 0.08

        narrative = 0.5
        if pair in {"hook->showcase", "showcase->result"}:
            narrative = 0.72 if name in {"zoomin", "circlecrop", "rectcrop"} else 0.58
        elif pair.endswith("->cta"):
            narrative = 0.75 if name in {"fadeblack", "dissolve", "rectcrop"} else 0.48
        elif pair == "showcase->showcase":
            narrative = 0.72 if name in {"cut", "dissolve", "fade"} else 0.52

        rhythm = 0.55
        if style == "fast":
            rhythm = 0.78 if category in {"none", "cut", "directional"} else 0.48
        elif style == "cinematic":
            rhythm = 0.78 if category in {"fade", "mask"} else 0.45
        elif style == "moderate":
            rhythm = 0.70 if name in {"none", "cut", "dissolve", "fade", "rectcrop"} else 0.55

        learned = max(-0.12, min(0.12, float(learning_bonuses.get(name, 0.0))))
        total = max(0.0, min(1.0, 0.58 * visual + 0.24 * narrative + 0.18 * rhythm + learned))
        details = {
            "visual_fit": round(visual, 4),
            "narrative_fit": round(narrative, 4),
            "rhythm_fit": round(rhythm, 4),
            "verified_learning_bonus": round(learned, 4),
        }
        scores.append({
            "type": name,
            "score": round(total, 4),
            "score_details": details,
            "reason": (
                f"visual={details['visual_fit']:.2f}, narrative={details['narrative_fit']:.2f}, "
                f"rhythm={details['rhythm_fit']:.2f}, learned={details['verified_learning_bonus']:+.2f}"
            ),
        })
    return sorted(scores, key=lambda item: (-item["score"], item["type"]))


def _candidate_duration(name: str, base_duration: float, style: str) -> float:
    if name in {"none", "cut"}:
        return 0.0
    scale = 0.75 if style == "fast" else 1.15 if style == "cinematic" else 1.0
    return round(max(0.12, min(0.50, base_duration * scale)), 3)


def _render_preview(
    left_clip: Path,
    right_clip: Path,
    transition_type: str,
    duration: float,
    output: Path,
) -> Tuple[Path, float]:
    from video_merger import XFADE_TYPE_MAP

    left_duration = _duration(left_clip)
    snippet = max(0.70, duration * 2.0 + 0.20)
    left_start = max(0.0, left_duration - snippet)
    common = (
        f"[0:v]trim=start={left_start:.4f}:duration={snippet:.4f},setpts=PTS-STARTPTS,"
        f"scale=360:640:force_original_aspect_ratio=decrease,pad=360:640:(ow-iw)/2:(oh-ih)/2:black,"
        f"fps={FPS},format=yuv420p,setsar=1[l];"
        f"[1:v]trim=start=0:duration={snippet:.4f},setpts=PTS-STARTPTS,"
        f"scale=360:640:force_original_aspect_ratio=decrease,pad=360:640:(ow-iw)/2:(oh-ih)/2:black,"
        f"fps={FPS},format=yuv420p,setsar=1[r];"
    )
    if transition_type in {"none", "cut"}:
        offset = snippet
        filter_complex = common + "[l][r]concat=n=2:v=1:a=0[v]"
    else:
        xfade_type = XFADE_TYPE_MAP[transition_type]
        offset = snippet - duration
        filter_complex = common + (
            f"[l][r]xfade=transition={xfade_type}:duration={duration:.4f}:offset={offset:.4f}[v]"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    _run([
        "ffmpeg", "-y", "-v", "error", "-i", str(left_clip), "-i", str(right_clip),
        "-filter_complex", filter_complex, "-map", "[v]", "-an", "-c:v", "libx264",
        "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p", str(output),
    ], timeout=120)
    return output, offset


def _evaluate_preview(path: Path, center: float, duration: float) -> Dict[str, Any]:
    span = max(0.25, duration * 1.2)
    times = [max(0.0, center - span + i * span * 2 / 8) for i in range(9)]
    frames = [_frame_stats(_extract_rgb_frame(path, time_sec)) for time_sec in times]
    black_ratio = sum(1 for frame in frames if frame["black"]) / len(frames)
    brightness_jumps = [
        abs(frames[i]["brightness"] - frames[i - 1]["brightness"]) / 255.0
        for i in range(1, len(frames))
    ]
    frame_jumps = [_frame_delta(frames[i - 1], frames[i]) for i in range(1, len(frames))]
    mean_jump = sum(frame_jumps) / max(len(frame_jumps), 1)
    jump_variance = sum((value - mean_jump) ** 2 for value in frame_jumps) / max(len(frame_jumps), 1)
    smoothness = max(0.0, 1.0 - math.sqrt(jump_variance) * 4.0)
    flash = max(brightness_jumps, default=0.0)
    max_jump = max(frame_jumps, default=0.0)
    quality_score = max(0.0, min(
        1.0,
        0.32 * (1.0 - black_ratio) + 0.23 * (1.0 - flash) +
        0.25 * (1.0 - max_jump) + 0.20 * smoothness,
    ))
    metrics = {
        "black_ratio": round(black_ratio, 4),
        "flash_delta": round(flash, 4),
        "max_frame_jump": round(max_jump, 4),
        "motion_smoothness": round(smoothness, 4),
    }
    passed = (
        black_ratio <= 0.05 and flash <= 0.35 and max_jump <= 0.70 and
        smoothness >= 0.25 and quality_score >= MIN_RENDER_SCORE
    )
    return {"passed": passed, "quality_score": round(quality_score, 4), "metrics": metrics}


def render_and_evaluate_transition(
    left_clip: Path,
    right_clip: Path,
    candidate: Dict[str, Any],
    work_dir: Path,
    boundary_index: int,
) -> Dict[str, Any]:
    output = work_dir / f"boundary_{boundary_index:02d}_{candidate['type']}.mp4"
    preview, center = _render_preview(
        left_clip, right_clip, candidate["type"], candidate["duration"], output,
    )
    result = _evaluate_preview(preview, center, candidate["duration"])
    result["preview"] = str(preview)
    return result


class TransitionLearningStore:
    """Stores only externally verified transition outcomes."""

    def __init__(self, db_path: Path, min_samples: int = 3):
        self.db_path = Path(db_path)
        self.min_samples = max(3, int(min_samples))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS transition_outcomes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    feature_bucket TEXT NOT NULL,
                    transition_type TEXT NOT NULL,
                    render_score REAL NOT NULL,
                    final_quality REAL NOT NULL,
                    verified_source TEXT NOT NULL,
                    verification_id TEXT NOT NULL,
                    outcome TEXT NOT NULL DEFAULT 'positive',
                    attribution REAL NOT NULL DEFAULT 1.0,
                    failure_reason TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                )
            """)
            columns = {
                row[1] for row in conn.execute("PRAGMA table_info(transition_outcomes)").fetchall()
            }
            if "verification_id" not in columns:
                conn.execute(
                    "ALTER TABLE transition_outcomes ADD COLUMN verification_id TEXT NOT NULL DEFAULT ''"
                )
            if "outcome" not in columns:
                conn.execute(
                    "ALTER TABLE transition_outcomes ADD COLUMN outcome TEXT NOT NULL DEFAULT 'positive'"
                )
            if "attribution" not in columns:
                conn.execute(
                    "ALTER TABLE transition_outcomes ADD COLUMN attribution REAL NOT NULL DEFAULT 1.0"
                )
            if "failure_reason" not in columns:
                conn.execute(
                    "ALTER TABLE transition_outcomes ADD COLUMN failure_reason TEXT NOT NULL DEFAULT ''"
                )
            conn.commit()

    def record(
        self,
        *,
        feature_bucket: str,
        transition_type: str,
        render_score: float,
        final_quality: float,
        verified_source: str,
        verification_id: Optional[str] = None,
        outcome: str = "positive",
        attribution: float = 1.0,
        failure_reason: str = "",
    ) -> bool:
        if verified_source not in VERIFIED_SOURCES:
            return False
        if outcome not in {"positive", "negative"}:
            return False
        attribution = max(0.0, min(1.0, float(attribution)))
        if attribution <= 0:
            return False
        if outcome == "positive" and (
            float(render_score) < MIN_RENDER_SCORE or float(final_quality) < 80.0
        ):
            return False
        if outcome == "negative" and not failure_reason:
            return False
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                """INSERT INTO transition_outcomes
                   (feature_bucket, transition_type, render_score, final_quality, verified_source,
                    verification_id, outcome, attribution, failure_reason, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    feature_bucket, transition_type, float(render_score), float(final_quality),
                    verified_source, verification_id or uuid.uuid4().hex, outcome, attribution,
                    failure_reason, datetime.now().isoformat(),
                ),
            )
            conn.commit()
        return True

    def get_verified_bonuses(self, bucket: str) -> Dict[str, float]:
        with sqlite3.connect(str(self.db_path)) as conn:
            rows = conn.execute(
                """SELECT transition_type, verification_id, outcome, attribution,
                          render_score, final_quality
                   FROM transition_outcomes
                   WHERE feature_bucket = ? AND verified_source IN
                         ('human', 'production_quality_gate', 'transition_render_gate')""",
                (bucket,),
            ).fetchall()
        grouped: Dict[str, Dict[str, List[float]]] = {}
        for transition_type, verification_id, outcome, attribution, render_score, final_quality in rows:
            per_type = grouped.setdefault(transition_type, {})
            sample_id = str(verification_id or "")
            value = (
                min(0.12, max(0.02, (float(render_score) - MIN_RENDER_SCORE) * 0.12 +
                    (float(final_quality) - 80.0) / 20.0 * 0.08))
                if outcome == "positive"
                else -min(0.18, max(0.04, (MIN_RENDER_SCORE - float(render_score)) * 0.18 +
                    max(0.0, 80.0 - float(final_quality)) / 80.0 * 0.08))
            ) * float(attribution)
            per_type.setdefault(sample_id, []).append(value)

        adjustments = {}
        for transition_type, samples in grouped.items():
            if len(samples) < self.min_samples:
                continue
            per_output = [sum(values) / len(values) for values in samples.values()]
            net = sum(per_output) / len(per_output)
            adjustments[transition_type] = round(max(-0.18, min(0.12, net)), 4)
        return adjustments


def plan_intelligent_transitions(
    clips: Sequence[Path],
    narratives: Sequence[str],
    *,
    style: str,
    base_duration: float,
    work_dir: Path,
    learning_store: Optional[TransitionLearningStore] = None,
    max_render_candidates: int = 5,
    verification_id: Optional[str] = None,
    max_total_overlap: Optional[float] = None,
) -> Dict[str, Any]:
    if len(clips) < 2:
        return {"transitions": [], "boundaries": [], "policy": "verified_content_aware_v1"}
    if len(narratives) < len(clips):
        raise IntelligentTransitionError("叙事段数量少于视频片段数量，无法进行智能转场决策")

    work_dir.mkdir(parents=True, exist_ok=True)
    planning_verification_id = verification_id or uuid.uuid4().hex
    selected = []
    boundaries = []
    remaining_overlap = None if max_total_overlap is None else max(0.0, float(max_total_overlap))
    for index in range(len(clips) - 1):
        features = analyze_transition_boundary(
            Path(clips[index]), Path(clips[index + 1]), narratives[index], narratives[index + 1], style,
        )
        bucket = feature_bucket(features)
        bonuses = learning_store.get_verified_bonuses(bucket) if learning_store else {}
        ranked = score_transition_candidates(features, bonuses)
        evaluated = []
        render_candidates = ranked[:max(1, max_render_candidates)]
        if not any(candidate["type"] == "none" for candidate in render_candidates):
            render_candidates.append(next(candidate for candidate in ranked if candidate["type"] == "none"))
        for candidate in render_candidates:
            current = dict(candidate)
            current["duration"] = _candidate_duration(current["type"], base_duration, style)
            try:
                render = render_and_evaluate_transition(
                    Path(clips[index]), Path(clips[index + 1]), current, work_dir, index,
                )
            except Exception as exc:
                render = {
                    "passed": False,
                    "quality_score": 0.0,
                    "metrics": {},
                    "error": str(exc),
                }
            current["render_validation"] = render
            current["combined_score"] = round(
                0.60 * current["score"] + 0.40 * float(render["quality_score"]), 4,
            )
            evaluated.append(current)
            if learning_store and not render["passed"]:
                metrics = render.get("metrics") or {}
                failure_reason = str(render.get("error") or "")
                if not failure_reason and metrics:
                    failure_reason = max(metrics, key=lambda key: float(metrics[key] or 0.0))
                learning_store.record(
                    feature_bucket=bucket,
                    transition_type=current["type"],
                    render_score=float(render["quality_score"]),
                    final_quality=0.0,
                    verified_source="transition_render_gate",
                    verification_id=planning_verification_id,
                    outcome="negative",
                    attribution=1.0,
                    failure_reason=failure_reason or "render_gate_failed",
                )

        valid = [
            candidate for candidate in evaluated
            if candidate["render_validation"]["passed"] and candidate["combined_score"] >= MIN_COMBINED_SCORE
        ]
        if remaining_overlap is not None:
            valid = [
                candidate
                for candidate in valid
                if float(candidate.get("duration") or 0.0) <= remaining_overlap + 1e-6
            ]
        if not valid:
            none_candidate = next(
                (
                    candidate
                    for candidate in evaluated
                    if candidate["type"] == "none" and candidate["render_validation"]["passed"]
                ),
                None,
            )
            if none_candidate is not None:
                valid = [none_candidate]
        if not valid:
            raise IntelligentTransitionError(
                f"镜头 {index}->{index + 1} 没有转场候选通过实渲染质量门；"
                "已阻断生成，不使用固定转场或简单拼接兜底"
            )
        chosen = sorted(valid, key=lambda item: (-item["combined_score"], item["type"]))[0]
        if remaining_overlap is not None:
            remaining_overlap = max(
                0.0,
                remaining_overlap - float(chosen.get("duration") or 0.0),
            )
        selected.append({
            "type": chosen["type"],
            "duration": chosen["duration"],
            "decision_score": chosen["combined_score"],
        })
        boundaries.append({
            "boundary": f"{index}->{index + 1}",
            "feature_bucket": bucket,
            "features": features,
            "candidates": evaluated,
            "selected": {
                "type": chosen["type"],
                "duration": chosen["duration"],
                "combined_score": chosen["combined_score"],
                "reason": chosen["reason"],
                "render_validation": chosen["render_validation"],
            },
        })
    cumulative = 0.0
    for index, boundary in enumerate(boundaries):
        left_duration = _duration(Path(clips[index]))
        transition_duration = float(boundary["selected"]["duration"])
        transition_start = cumulative + left_duration - transition_duration
        boundary["output_timing"] = {
            "start": round(transition_start, 4),
            "center": round(transition_start + transition_duration / 2.0, 4),
            "duration": transition_duration,
        }
        cumulative = transition_start

    return {
        "policy": "verified_content_aware_v1",
        "max_total_overlap": max_total_overlap,
        "selected_total_overlap": round(
            sum(float(item.get("duration") or 0.0) for item in selected),
            4,
        ),
        "thresholds": {
            "minimum_render_score": MIN_RENDER_SCORE,
            "minimum_combined_score": MIN_COMBINED_SCORE,
        },
        "transitions": selected,
        "boundaries": boundaries,
    }


def validate_merged_transition_boundaries(
    video: Path,
    report: Dict[str, Any],
    *,
    store: Optional[TransitionLearningStore] = None,
    verification_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Validate selected transitions in the actual merged timeline and record attributable failures."""
    failed = []
    recorded = 0
    for boundary in report.get("boundaries", []):
        timing = boundary.get("output_timing") or {}
        selected = boundary.get("selected") or {}
        result = _evaluate_preview(
            Path(video),
            float(timing.get("center") or 0.0),
            float(timing.get("duration") or selected.get("duration") or 0.1),
        )
        selected["merged_validation"] = result
        if result["passed"]:
            continue
        failed.append(str(boundary.get("boundary") or "unknown"))
        if store and store.record(
            feature_bucket=str(boundary.get("feature_bucket") or "unknown"),
            transition_type=str(selected.get("type") or ""),
            render_score=float(result.get("quality_score") or 0.0),
            final_quality=0.0,
            verified_source="transition_render_gate",
            verification_id=verification_id,
            outcome="negative",
            attribution=1.0,
            failure_reason="merged_boundary_quality_gate",
        ):
            recorded += 1
    return {"passed": not failed, "failed_boundaries": failed, "negative_records": recorded}


def write_transition_report(report: Dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def record_transition_outcomes(
    report: Dict[str, Any],
    *,
    final_quality: float,
    final_passed: bool,
    store: TransitionLearningStore,
    verified_source: str = "production_quality_gate",
    verification_id: Optional[str] = None,
    transition_failure_attributed: bool = False,
    failure_reason: str = "temporal_quality_gate",
) -> int:
    if verified_source not in VERIFIED_SOURCES:
        return 0
    is_positive = final_passed and final_quality >= 80.0
    is_negative = transition_failure_attributed and (not final_passed or final_quality < 80.0)
    if not is_positive and not is_negative:
        return 0
    recorded = 0
    for boundary in report.get("boundaries", []):
        selected = boundary.get("selected") or {}
        render = selected.get("render_validation") or {}
        if store.record(
            feature_bucket=str(boundary.get("feature_bucket") or "unknown"),
            transition_type=str(selected.get("type") or ""),
            render_score=float(render.get("quality_score") or 0.0),
            final_quality=float(final_quality),
            verified_source=verified_source,
            verification_id=verification_id,
            outcome="positive" if is_positive else "negative",
            attribution=1.0,
            failure_reason="" if is_positive else failure_reason,
        ):
            recorded += 1
    return recorded


def record_verified_transition_outcomes(
    report: Dict[str, Any],
    *,
    final_quality: float,
    passed: bool,
    store: TransitionLearningStore,
    verified_source: str = "production_quality_gate",
    verification_id: Optional[str] = None,
) -> int:
    """Backward-compatible positive-only wrapper."""
    return record_transition_outcomes(
        report,
        final_quality=final_quality,
        final_passed=passed,
        store=store,
        verified_source=verified_source,
        verification_id=verification_id,
    )
