"""Hybrid source-dialogue and narration audio for personal Vlog edits."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


MIN_DIALOGUE_SECONDS = 0.5
MAX_DIALOGUE_RATIO = 0.72
DIALOGUE_GAP_SECONDS = 0.06
NARRATION_UNITS_PER_SECOND = 3.6
MAX_NARRATION_RATIO = 0.55

_NARRATION_STYLE_PATTERNS = {
    "humorous": re.compile(r"谁懂|没想到|居然|忍不住|好家伙|你猜|这下|就怕|可算|偏偏"),
    "storytelling": re.compile(r"一开始|后来|原来|这才|你猜|没想到|结果|总算"),
    "energetic": re.compile(r"赶紧|快来|直接|马上|立刻|冲|安排|别错过"),
    "professional": re.compile(r"采用|来自|工艺|成分|规格|适合|选择|准备"),
}


def is_personal_vlog_style(product_info: Dict[str, Any]) -> bool:
    """Return whether the resolved high-level style is personal Vlog."""
    tone = str(product_info.get("video_style_tone") or "").strip().lower()
    style = re.sub(r"\s+", "", str(product_info.get("video_style") or "")).lower()
    return tone == "personal_vlog" or "vlog" in style or style in {"生活记录", "日常记录"}


def _path_key(value: Any) -> str:
    return str(Path(str(value or "")).expanduser().resolve()) if value else ""


def _claims_are_supported(
    text: str,
    product_info: Dict[str, Any],
    analysis: Dict[str, Any],
) -> bool:
    from local_asset_pipeline import _claim_supported, _infer_copy_claims

    claims = _infer_copy_claims(text)
    has_price_claim = any(str(claim.get("type") or "") == "price" for claim in claims)
    if not has_price_claim and re.search(
        r"(?:[0-9]+(?:\.[0-9]+)?|[零〇一二三四五六七八九十百千万两]+)"
        r"\s*(?:元|块钱|块)(?:\s*[一个两三四五六七八九十0-9]+)?",
        text,
    ):
        claims.append({"text": text, "type": "price"})
    return all(
        _claim_supported(claim, product_info, analysis)
        for claim in claims
    )


def collect_source_dialogue_candidates(
    selected_segments: Iterable[Dict[str, Any]],
    asset_index: Dict[str, Any],
    product_info: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Collect complete and fact-safe ASR utterances inside selected Vlog clips."""
    profiles = {
        _path_key(source.get("path")): source.get("audio_understanding") or {}
        for source in asset_index.get("sources") or []
        if source.get("path")
    }
    candidates: List[Dict[str, Any]] = []
    seen = set()
    for selected in selected_segments:
        source_path = _path_key(selected.get("source_path"))
        profile = profiles.get(source_path) or {}
        if not profile.get("has_speech"):
            continue
        clip_start = float(selected.get("source_start") or 0.0)
        clip_end = float(selected.get("source_end") or 0.0)
        analysis = selected.get("analysis") or {}
        for utterance in profile.get("segments") or []:
            start = float(utterance.get("start") or 0.0)
            end = float(utterance.get("end") or 0.0)
            text = re.sub(r"\s+", " ", str(utterance.get("text") or "")).strip()
            confidence = float(utterance.get("confidence", 1.0) or 0.0)
            key = (source_path, round(start, 3), round(end, 3), text)
            if (
                key in seen
                or len(re.sub(r"\W+", "", text)) < 2
                or confidence < 0.35
                or end - start < MIN_DIALOGUE_SECONDS
                or start < clip_start - 0.04
                or end > clip_end + 0.04
                or not _claims_are_supported(text, product_info, analysis)
            ):
                continue
            seen.add(key)
            relation = str(analysis.get("speech_visual_relation") or "unknown")
            relation_bonus = 0.18 if relation == "aligned" else 0.10 if relation == "complementary" else 0.0
            candidates.append({
                "edit_index": int(selected.get("edit_index", len(candidates))),
                "semantic_segment": int(selected.get("semantic_segment", 0)),
                "narrative": str(selected.get("narrative") or "showcase"),
                "source_path": source_path,
                "source_start": round(start, 3),
                "source_end": round(end, 3),
                "relative_start": round(max(0.0, start - clip_start), 3),
                "relative_end": round(max(0.0, end - clip_start), 3),
                "text": text,
                "confidence": confidence,
                "score": round(confidence + relation_bonus + min(0.2, (end - start) / 15.0), 4),
            })
    return candidates


def place_source_dialogue(
    candidates: Iterable[Dict[str, Any]],
    edit_timeline: Iterable[Dict[str, Any]],
    *,
    trim_start: float,
    total_duration: float,
    max_dialogue_ratio: float = MAX_DIALOGUE_RATIO,
) -> List[Dict[str, Any]]:
    """Map source utterances onto the rendered timeline without cutting or overlap."""
    timeline = {int(item["index"]): item for item in edit_timeline}
    placed: List[Dict[str, Any]] = []
    for candidate in candidates:
        edit = timeline.get(int(candidate["edit_index"]))
        if edit is None:
            continue
        start = float(edit["start"]) + float(candidate["relative_start"]) - trim_start
        end = float(edit["start"]) + float(candidate["relative_end"]) - trim_start
        if start < -0.04 or end > total_duration + 0.04:
            continue
        start = max(0.0, start)
        end = min(total_duration, end)
        if end - start < MIN_DIALOGUE_SECONDS:
            continue
        placed.append({
            **candidate,
            "timeline_start": round(start, 3),
            "timeline_end": round(end, 3),
            "duration": round(end - start, 3),
        })

    non_overlapping: List[Dict[str, Any]] = []
    for candidate in sorted(placed, key=lambda item: (item["timeline_start"], -item["score"])):
        if non_overlapping and candidate["timeline_start"] < non_overlapping[-1]["timeline_end"] + DIALOGUE_GAP_SECONDS:
            if candidate["score"] > non_overlapping[-1]["score"]:
                non_overlapping[-1] = candidate
            continue
        non_overlapping.append(candidate)

    budget = max(3.0, total_duration * max(0.0, min(0.9, max_dialogue_ratio)))
    chosen: List[Dict[str, Any]] = []
    used = 0.0
    for candidate in sorted(non_overlapping, key=lambda item: (-item["score"], item["timeline_start"])):
        duration = float(candidate["duration"])
        if chosen and used + duration > budget:
            continue
        if not chosen and duration > budget and duration > total_duration * 0.9:
            continue
        chosen.append(candidate)
        used += duration
    return sorted(chosen, key=lambda item: item["timeline_start"])


def dialogue_subtitles(dialogue_plan: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {
            "text": str(item["text"]),
            "start": float(item["timeline_start"]),
            "end": float(item["timeline_end"]),
            "segment": int(item["semantic_segment"]),
            "source": "original_dialogue",
        }
        for item in dialogue_plan
    ]


def narration_lines_without_dialogue(
    lines: Iterable[Dict[str, Any]],
    dialogue_plan: Iterable[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Reserve source-dialogue semantic segments and time ranges from AI narration."""
    dialogue = list(dialogue_plan)
    source_segments = {int(item["semantic_segment"]) for item in dialogue}
    result = []
    for line in lines:
        if not str(line.get("text") or "").strip():
            continue
        if int(line.get("segment", -1)) in source_segments:
            continue
        start = float(line.get("start") or 0.0)
        end = float(line.get("end") or start)
        if any(
            start < float(item["timeline_end"]) + DIALOGUE_GAP_SECONDS
            and end > float(item["timeline_start"]) - DIALOGUE_GAP_SECONDS
            for item in dialogue
        ):
            continue
        result.append(dict(line))
    return result


def _narration_style_score(text: str, style_hint: str) -> float:
    hint = str(style_hint or "").strip().lower()
    aliases = {
        "funny": "humorous",
        "playful": "humorous",
        "comedy": "humorous",
        "调侃": "humorous",
        "幽默": "humorous",
        "故事": "storytelling",
        "叙事": "storytelling",
        "活力": "energetic",
        "专业": "professional",
    }
    resolved = aliases.get(hint, hint)
    pattern = _NARRATION_STYLE_PATTERNS.get(resolved)
    return 1.0 if pattern and pattern.search(text) else 0.0


def _resolve_narration_style(lines: Iterable[Dict[str, Any]], style_hint: str) -> str:
    hint = str(style_hint or "").strip().lower()
    if hint not in {"", "auto", "standard"}:
        return hint
    texts = [str(item.get("text") or "") for item in lines]
    scores = {
        style: sum(1 for text in texts if pattern.search(text))
        for style, pattern in _NARRATION_STYLE_PATTERNS.items()
    }
    style, score = max(scores.items(), key=lambda item: item[1])
    return style if score > 0 else "storytelling"


def _free_intervals(
    busy: Iterable[tuple[float, float]],
    total_duration: float,
) -> List[tuple[float, float]]:
    merged: List[List[float]] = []
    for start, end in sorted(busy):
        start = max(0.0, float(start) - DIALOGUE_GAP_SECONDS)
        end = min(float(total_duration), float(end) + DIALOGUE_GAP_SECONDS)
        if end <= start:
            continue
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    free: List[tuple[float, float]] = []
    cursor = 0.0
    for start, end in merged:
        if start > cursor:
            free.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < total_duration:
        free.append((cursor, float(total_duration)))
    return free


def place_narration_in_dialogue_gaps(
    lines: Iterable[Dict[str, Any]],
    dialogue_plan: Iterable[Dict[str, Any]],
    *,
    total_duration: float,
    style_hint: str = "auto",
    max_narration_ratio: float = MAX_NARRATION_RATIO,
) -> List[Dict[str, Any]]:
    """Select sparse narration and place it only in source-dialogue-free gaps."""
    from material_copy_optimizer import _speech_units

    total_duration = max(0.0, float(total_duration))
    source_lines = [
        dict(line)
        for line in lines
        if str(line.get("text") or "").strip()
    ]
    if not source_lines or total_duration <= 0.1:
        return []
    source_lines.sort(key=lambda item: (float(item.get("start") or 0.0), int(item.get("segment", 0))))
    narration_style = _resolve_narration_style(source_lines, style_hint)
    busy = [
        (float(item["timeline_start"]), float(item["timeline_end"]))
        for item in dialogue_plan
        if float(item.get("timeline_end") or 0.0) > float(item.get("timeline_start") or 0.0)
    ]
    budget = total_duration * max(0.15, min(0.75, float(max_narration_ratio)))
    used = 0.0
    candidates = []
    for line in source_lines:
        text = str(line.get("text") or "").strip()
        spoken_duration = max(0.6, _speech_units(text) / NARRATION_UNITS_PER_SECOND)
        candidates.append((_narration_style_score(text, narration_style), spoken_duration, line))

    planned: List[Dict[str, Any]] = []
    for _style_score, spoken_duration, line in sorted(
        candidates,
        key=lambda item: (-item[0], float(item[2].get("start") or 0.0)),
    ):
        if used + spoken_duration > budget:
            continue
        desired_start = max(0.0, float(line.get("start") or 0.0))
        desired_end = min(total_duration, float(line.get("end") or total_duration))
        options = []
        for free_start, free_end in _free_intervals(busy, total_duration):
            if free_start <= 0.001 or free_end >= total_duration - 0.001:
                continue
            desired_center = (desired_start + desired_end) / 2.0
            start = min(
                max(desired_center - spoken_duration / 2.0, free_start),
                free_end - spoken_duration,
            )
            end = start + spoken_duration
            if start < free_start or end > free_end:
                continue
            inside_desired_cue = start >= desired_start and end <= desired_end
            distance = abs((start + end) / 2.0 - (desired_start + desired_end) / 2.0)
            options.append((1 if inside_desired_cue else 0, -distance, start, end))
        if not options:
            continue
        _, _, start, end = max(options)
        planned.append({
            **line,
            "start": round(start, 3),
            "end": round(end, 3),
            "placement": "source_dialogue_gap",
            "narration_style": narration_style,
        })
        busy.append((start, end))
        used += spoken_duration
    return sorted(planned, key=lambda item: float(item["start"]))


def ensure_planned_narration_survived(
    planned_count: int,
    narration: Iterable[Dict[str, Any]],
    *,
    stage: str,
) -> None:
    """Reject silent degradation after personal Vlog narration was planned."""
    if int(planned_count) > 0 and not list(narration):
        raise RuntimeError(
            f"个人 Vlog 已计划 {int(planned_count)} 段间隙旁白，但{stage}后为 0 段；"
            "请缩短桥接文案或保留更长的内部原声间隙"
        )


def _run_ffmpeg(command: List[str], output: Path, timeout: int = 180) -> Path:
    result = subprocess.run(command, capture_output=True, timeout=timeout)
    if result.returncode != 0 or not output.is_file() or output.stat().st_size < 512:
        stderr = result.stderr.decode("utf-8", errors="ignore")[-800:] if result.stderr else ""
        raise RuntimeError(f"personal Vlog audio render failed: {stderr}")
    return output


def render_source_dialogue_track(
    dialogue_plan: Iterable[Dict[str, Any]],
    output: Path,
    total_duration: float,
) -> Optional[Path]:
    """Render selected original utterances as a timeline-aligned speech-only track."""
    dialogue = list(dialogue_plan)
    if not dialogue:
        return None
    output.parent.mkdir(parents=True, exist_ok=True)
    inputs = [
        "-f", "lavfi", "-t", f"{total_duration:.3f}",
        "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
    ]
    filters = []
    labels = ["[0:a]"]
    for index, item in enumerate(dialogue, start=1):
        duration = float(item["source_end"]) - float(item["source_start"])
        inputs.extend([
            "-ss", f"{float(item['source_start']):.3f}",
            "-t", f"{duration:.3f}",
            "-i", str(item["source_path"]),
        ])
        fade = min(0.04, duration / 4.0)
        delay = max(0, round(float(item["timeline_start"]) * 1000))
        filters.append(
            f"[{index}:a]aresample=48000,"
            "aformat=sample_fmts=fltp:channel_layouts=stereo,"
            f"atrim=duration={duration:.3f},asetpts=PTS-STARTPTS,"
            "highpass=f=80,loudnorm=I=-18:LRA=7:TP=-1.5,"
            f"afade=t=in:st=0:d={fade:.3f},"
            f"afade=t=out:st={max(0.0, duration - fade):.3f}:d={fade:.3f},"
            f"adelay={delay}|{delay}[dialogue{index}]"
        )
        labels.append(f"[dialogue{index}]")
    filters.append(
        f"{''.join(labels)}amix=inputs={len(labels)}:duration=first:"
        "dropout_transition=0:normalize=0,alimiter=limit=0.95[aout]"
    )
    command = [
        "ffmpeg", "-y", *inputs,
        "-filter_complex", ";".join(filters),
        "-map", "[aout]", "-t", f"{total_duration:.3f}",
        "-c:a", "aac", "-b:a", "192k", str(output),
    ]
    return _run_ffmpeg(command, output)


def mix_speech_tracks(
    source_dialogue: Path,
    narration: Optional[Path],
    output: Path,
    total_duration: float,
) -> Path:
    """Combine non-overlapping source dialogue and optional AI narration."""
    tracks = [Path(source_dialogue)]
    if narration is not None:
        tracks.append(Path(narration))
    inputs = [value for track in tracks for value in ("-i", str(track))]
    labels = "".join(f"[{index}:a]" for index in range(len(tracks)))
    command = [
        "ffmpeg", "-y", *inputs,
        "-filter_complex",
        f"{labels}amix=inputs={len(tracks)}:duration=longest:"
        "dropout_transition=0:normalize=0,"
        f"atrim=duration={total_duration:.3f},apad=whole_dur={total_duration:.3f},"
        "alimiter=limit=0.95[aout]",
        "-map", "[aout]", "-t", f"{total_duration:.3f}",
        "-c:a", "aac", "-b:a", "192k", str(output),
    ]
    output.parent.mkdir(parents=True, exist_ok=True)
    return _run_ffmpeg(command, output)
