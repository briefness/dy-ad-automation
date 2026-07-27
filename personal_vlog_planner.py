"""Source-dialogue-led story planning for personal Vlog edits."""

from __future__ import annotations

import copy
import json
import re
import shutil
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from personal_vlog_audio import collect_source_dialogue_candidates


MIN_EVENT_SECONDS = 4.5
MAX_EVENT_SECONDS = 10.0
MIN_SUPPLEMENTAL_VISUAL_SECONDS = 3.0
MIN_SUPPLEMENTAL_VISUAL_CONFIDENCE = 0.55
PRE_ROLL_SECONDS = 1.25
POST_ROLL_SECONDS = 1.75
MERGE_GAP_SECONDS = 0.75
STORY_PHASES = (
    "orientation",
    "setup",
    "process",
    "interaction",
    "candid",
    "outcome",
)
STORY_PHASE_ORDER = {phase: index for index, phase in enumerate(STORY_PHASES)}
_MISHAP_DIALOGUE = re.compile(r"哎呀|糟了|滚歪|弄歪|掉了|洒了|翻了|小状况|没想到")
_INTERACTION_DIALOGUE = re.compile(r"美女|帅哥|随便挑|客人|顾客|来一个|要不要|挑一挑")


class PersonalVlogPlanningError(RuntimeError):
    pass


def _path_key(value: Any) -> str:
    return str(Path(str(value or "")).expanduser().resolve()) if value else ""


def _expand_event_span(
    utterance_start: float,
    utterance_end: float,
    source_duration: float,
) -> Tuple[float, float]:
    start = max(0.0, utterance_start - PRE_ROLL_SECONDS)
    end = min(source_duration, utterance_end + POST_ROLL_SECONDS)
    missing = max(0.0, MIN_EVENT_SECONDS - (end - start))
    start = max(0.0, start - missing / 2.0)
    end = min(source_duration, end + missing / 2.0)
    if end - start < MIN_EVENT_SECONDS:
        if start <= 0.001:
            end = min(source_duration, MIN_EVENT_SECONDS)
        elif end >= source_duration - 0.001:
            start = max(0.0, source_duration - MIN_EVENT_SECONDS)
    return round(start, 3), round(end, 3)


def _utterance_has_visible_frames(
    window: Dict[str, Any],
    utterance_start: float,
    utterance_end: float,
) -> bool:
    frame_quality = window.get("frame_quality") or {}
    samples = frame_quality.get("samples") or []
    relevant = [
        sample for sample in samples
        if utterance_start - 0.10
        <= float(sample.get("time") or 0.0)
        <= min(utterance_end, utterance_start + 0.45)
    ]
    if not relevant:
        return frame_quality.get("passed") is True
    return any(
        sample.get("readable") is True
        and float(sample.get("brightness") or 0.0) >= 10.0
        and float(sample.get("contrast") or 0.0) >= 10.0
        for sample in relevant
    )


def _safe_dialogue_anchors(
    asset_index: Dict[str, Any],
    product_info: Dict[str, Any],
) -> List[Dict[str, Any]]:
    windows_by_source: Dict[str, List[Dict[str, Any]]] = {}
    for window in asset_index.get("windows") or []:
        windows_by_source.setdefault(_path_key(window.get("source_path")), []).append(window)

    anchors = []
    for source_index, source in enumerate(asset_index.get("sources") or []):
        source_path = _path_key(source.get("path"))
        source_duration = float(source.get("duration") or 0.0)
        profile = source.get("audio_understanding") or {}
        if not source_path or source_duration <= 0.0 or not profile.get("has_speech"):
            continue
        for utterance in profile.get("segments") or []:
            utterance_start = float(utterance.get("start") or 0.0)
            utterance_end = float(utterance.get("end") or 0.0)
            overlapping_windows = []
            for window in windows_by_source.get(source_path, []):
                overlap = max(
                    0.0,
                    min(utterance_end, float(window.get("end") or 0.0))
                    - max(utterance_start, float(window.get("start") or 0.0)),
                )
                analysis = window.get("analysis") or {}
                if (
                    overlap <= 0.0
                    or float(analysis.get("confidence") or 0.0) < 0.45
                    or not _utterance_has_visible_frames(
                        window,
                        utterance_start,
                        utterance_end,
                    )
                ):
                    continue
                overlapping_windows.append(window)
            if not overlapping_windows:
                continue
            representative = max(
                overlapping_windows,
                key=lambda window: (
                    {
                        "aligned": 2,
                        "complementary": 1,
                    }.get(
                        str((window.get("analysis") or {}).get("speech_visual_relation")),
                        0,
                    ),
                    float((window.get("analysis") or {}).get("confidence") or 0.0),
                ),
            )
            analysis = representative.get("analysis") or {}
            candidates = collect_source_dialogue_candidates(
                [{
                    "source_path": source_path,
                    "source_start": utterance_start,
                    "source_end": utterance_end,
                    "analysis": analysis,
                }],
                asset_index,
                product_info,
            )
            candidate = next((
                item for item in candidates
                if abs(float(item["source_start"]) - utterance_start) <= 0.01
                and abs(float(item["source_end"]) - utterance_end) <= 0.01
            ), None)
            if candidate is None:
                continue
            anchors.append({
                **candidate,
                "source_path": source_path,
                "source_video": str(
                    representative.get("source_video") or Path(source_path).name
                ),
                "source_duration": source_duration,
                "source_order": source_index,
                "window": representative,
                "rank": (
                    float(candidate.get("score") or 0.0)
                    + 0.15 * float(analysis.get("confidence") or 0.0)
                ),
            })
    return sorted(
        anchors,
        key=lambda item: (
            int(item["source_order"]),
            float(item["source_start"]),
            float(item["source_end"]),
        ),
    )


def _dominant_group_analysis_value(
    group: List[Dict[str, Any]],
    key: str,
    ignored: set[str],
) -> str:
    scores: Dict[str, float] = {}
    first_seen: Dict[str, int] = {}
    for index, item in enumerate(group):
        analysis = item["window"].get("analysis") or {}
        value = str(analysis.get(key) or "").strip().lower()
        if not value or value in ignored:
            continue
        scores[value] = scores.get(value, 0.0) + max(
            0.1,
            float(analysis.get("confidence") or 0.0),
        )
        first_seen.setdefault(value, index)
    if not scores:
        return ""
    return max(scores, key=lambda value: (scores[value], -first_seen[value]))


def _event_windows(anchors: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    groups: List[List[Dict[str, Any]]] = []
    spans: List[Tuple[float, float]] = []
    for anchor in anchors:
        span = _expand_event_span(
            float(anchor["source_start"]),
            float(anchor["source_end"]),
            float(anchor["source_duration"]),
        )
        if groups:
            previous = groups[-1]
            previous_span = spans[-1]
            merged_end = max(previous_span[1], span[1])
            if (
                previous[0]["source_path"] == anchor["source_path"]
                and span[0] <= previous_span[1] + MERGE_GAP_SECONDS
                and merged_end - min(previous_span[0], span[0]) <= MAX_EVENT_SECONDS
            ):
                previous.append(anchor)
                spans[-1] = (min(previous_span[0], span[0]), merged_end)
                continue
        groups.append([anchor])
        spans.append(span)

    events = []
    for event_index, (group, span) in enumerate(zip(groups, spans)):
        representative = max(group, key=lambda item: float(item["rank"]))
        base_window = representative["window"]
        analysis = copy.deepcopy(base_window.get("analysis") or {})
        texts = list(dict.fromkeys(str(item["text"]) for item in group))
        spoken_intents = list(dict.fromkeys(
            str(value)
            for item in group
            for value in (item["window"].get("analysis") or {}).get("spoken_intents") or []
            if str(value).strip()
        ))
        product_story_role = _dominant_group_analysis_value(
            group,
            "product_story_role",
            {"unknown"},
        )
        action_phase = _dominant_group_analysis_value(
            group,
            "action_phase",
            {"none", "unknown"},
        )
        analysis.update({
            "spoken_summary": " ".join(texts),
            "spoken_intents": spoken_intents,
            "speech_visual_relation": (
                "aligned"
                if any(
                    str((item["window"].get("analysis") or {}).get("speech_visual_relation"))
                    == "aligned"
                    for item in group
                ) else "complementary"
            ),
            "narrative_roles": list(dict.fromkeys([
                *(analysis.get("narrative_roles") or []),
                "personal_vlog",
            ])),
            "usable_for_personal_vlog": True,
        })
        if product_story_role:
            analysis["product_story_role"] = product_story_role
        if action_phase:
            analysis["action_phase"] = action_phase
        start, end = span
        source_order = int(representative["source_order"])
        events.append({
            **copy.deepcopy(base_window),
            "window_id": f"personal_vlog_{source_order:03d}_{round(start * 1000):07d}_{event_index:02d}",
            "source_video": representative["source_video"],
            "source_path": representative["source_path"],
            "start": start,
            "end": end,
            "duration": round(end - start, 3),
            "analysis": analysis,
            "audio_context": {
                "has_speech": True,
                "transcript": " ".join(texts),
                "segments": [
                    {
                        "start": float(item["source_start"]),
                        "end": float(item["source_end"]),
                        "text": str(item["text"]),
                        "confidence": float(item["confidence"]),
                    }
                    for item in group
                ],
            },
            "personal_vlog_event": {
                "event_kind": "source_dialogue",
                "source_order": source_order,
                "dialogue_count": len(group),
                "dialogue_seconds": round(sum(
                    float(item["source_end"]) - float(item["source_start"])
                    for item in group
                ), 3),
                "transcript": " ".join(texts),
            },
            "personal_vlog_rank": round(sum(float(item["rank"]) for item in group), 4),
        })
    return events


def _classify_story_phase(event: Dict[str, Any]) -> str:
    event_meta = event.get("personal_vlog_event") or {}
    is_visual_support = event_meta.get("event_kind") == "visual_support"
    if int(event_meta.get("dialogue_count") or 0) == 0 and not is_visual_support:
        return "orientation"

    transcript = re.sub(r"\s+", "", str(event_meta.get("transcript") or ""))
    analysis = event.get("analysis") or {}
    action_phase = str(analysis.get("action_phase") or "none").strip().lower()
    role = str(analysis.get("product_story_role") or "unknown").strip().lower()

    if re.search(r"卖(?:完|光)|卖得?差不多|清空|收摊|打烊|结束营业|回家了", transcript):
        return "outcome"
    if re.search(r"开始.*(?:录|拍)|(?:录|拍).*(?:开始|开了)|在录|开拍|开机|关机|怎么关|镜头", transcript):
        return "orientation"
    if re.search(r"随便挑|挑一|选一|客人|顾客|美女|扫码|付款|给你|要几个|要哪|谢谢", transcript):
        return "interaction"
    if re.search(r"摆摊|出摊|开摊|支摊|摊子.*(?:支|摆)|准备|装车|摆好|摆出来", transcript):
        return "setup"
    if action_phase == "outcome" or role == "result":
        return "outcome"
    if action_phase == "setup":
        return "setup"
    if action_phase == "action" or role in {"ingredient", "origin", "production", "usage"}:
        return "process"
    if role == "finished_product":
        return "process"
    if role == "context" and is_visual_support:
        return "setup"
    return "candid"


def _theme_relevance(event: Dict[str, Any], phase: str) -> float:
    analysis = event.get("analysis") or {}
    relation = str(analysis.get("speech_visual_relation") or "unknown").lower()
    role = str(analysis.get("product_story_role") or "unknown").lower()
    score = {
        "orientation": 0.25,
        "setup": 0.45,
        "process": 0.5,
        "interaction": 0.6,
        "candid": 0.1,
        "outcome": 0.65,
    }.get(phase, 0.0)
    if relation == "aligned":
        score += 0.2
    elif relation == "complementary":
        score += 0.1
    if role not in {"", "unknown", "context"}:
        score += 0.15
    return round(score, 4)


def _annotate_story_event(event: Dict[str, Any]) -> Dict[str, Any]:
    event_meta = event.setdefault("personal_vlog_event", {})
    phase = _classify_story_phase(event)
    event_meta["story_phase"] = phase
    event_meta["theme_relevance"] = _theme_relevance(event, phase)
    return event


def _normalized_dialogue(event: Dict[str, Any]) -> str:
    transcript = str((event.get("personal_vlog_event") or {}).get("transcript") or "")
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", transcript).lower()


def _visual_features(event: Dict[str, Any]) -> set[str]:
    analysis = event.get("analysis") or {}
    features: set[str] = set()
    role = str(analysis.get("product_story_role") or "unknown").strip().lower()
    action_phase = str(analysis.get("action_phase") or "none").strip().lower()
    if role not in {"", "unknown"}:
        features.add(f"role:{role}")
    if action_phase not in {"", "none", "unknown"}:
        features.add(f"action:{action_phase}")
    for value in analysis.get("narrative_roles") or []:
        normalized = str(value).strip().lower()
        if normalized and normalized not in {"filler", "personal_vlog"}:
            features.add(f"narrative:{normalized}")
    for value in analysis.get("visible_subjects") or []:
        normalized = str(value).strip().lower()
        if normalized:
            features.add(f"subject:{normalized}")
    return features


def _visual_similarity(left: Dict[str, Any], right: Dict[str, Any]) -> float:
    left_path = _path_key(left.get("source_path"))
    right_path = _path_key(right.get("source_path"))
    if left_path and left_path == right_path:
        overlap = max(
            0.0,
            min(float(left.get("end") or 0.0), float(right.get("end") or 0.0))
            - max(float(left.get("start") or 0.0), float(right.get("start") or 0.0)),
        )
        shortest = min(
            float(left.get("duration") or 0.0),
            float(right.get("duration") or 0.0),
        )
        if shortest > 0.0 and overlap / shortest >= 0.35:
            return 1.0

    left_features = _visual_features(left)
    right_features = _visual_features(right)
    if not left_features or not right_features:
        return 0.0
    return len(left_features & right_features) / len(left_features | right_features)


def _events_are_audio_visual_duplicates(
    left: Dict[str, Any],
    right: Dict[str, Any],
) -> bool:
    left_dialogue = _normalized_dialogue(left)
    right_dialogue = _normalized_dialogue(right)
    if bool(left_dialogue) != bool(right_dialogue):
        return False
    if left_dialogue and SequenceMatcher(
        None,
        left_dialogue,
        right_dialogue,
    ).ratio() < 0.84:
        return False
    return _visual_similarity(left, right) >= 0.72


def _story_order(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(
        events,
        key=lambda item: (
            STORY_PHASE_ORDER.get(
                str((item.get("personal_vlog_event") or {}).get("story_phase")),
                len(STORY_PHASE_ORDER),
            ),
            int((item.get("personal_vlog_event") or {}).get("source_order") or 0),
            float(item.get("start") or 0.0),
        ),
    )


def _build_personal_vlog_theme(
    events: List[Dict[str, Any]],
    product_info: Dict[str, Any],
) -> Dict[str, str]:
    subject = str(product_info.get("name") or "日常").strip() or "日常"
    transcript = " ".join(
        str((event.get("personal_vlog_event") or {}).get("transcript") or "")
        for event in events
    )
    explicit_stall = bool(re.search(r"摆摊|出摊|开摊|收摊|摊位|摊子", transcript))
    stall_signal_count = sum((
        bool(re.search(r"在卖|开卖|卖(?:完|光|掉|得|了)|售罄|清空", transcript)),
        bool(re.search(r"随便挑|挑一|选一|要几个|要哪", transcript)),
        bool(re.search(r"客人|顾客|扫码|付款", transcript)),
    ))
    if explicit_stall or stall_signal_count >= 2:
        return {
            "type": "stall_life",
            "subject": subject,
            "title": f"我的{subject}摆摊日常",
            "narrative_goal": f"分享摆摊准备、现场过程、人物互动和收摊结果，让{subject}自然出现在日常里",
            "opening_narration": f"最近记录了一些摆摊卖{subject}的日常。",
            "tone": "轻松、真实、日常分享",
        }

    title = "我的日常" if subject == "日常" else f"我的{subject}日常"
    opening = (
        "最近记录了一些日常。"
        if subject == "日常"
        else f"最近记录了一些关于{subject}的日常。"
    )
    return {
        "type": "daily_life",
        "subject": subject,
        "title": title,
        "narrative_goal": f"围绕{subject}分享真实、有主线的日常片段",
        "opening_narration": opening,
        "tone": "轻松、真实、日常分享",
    }


def _overlaps_story_event(
    window: Dict[str, Any],
    events: List[Dict[str, Any]],
) -> bool:
    window_path = _path_key(window.get("source_path"))
    return any(
        window_path == _path_key(event.get("source_path"))
        and float(window.get("start") or 0.0) < float(event.get("end") or 0.0)
        and float(window.get("end") or 0.0) > float(event.get("start") or 0.0)
        for event in events
    )


def _supplemental_visual_event(
    window: Dict[str, Any],
    source_order: Dict[str, int],
) -> Dict[str, Any]:
    event = copy.deepcopy(window)
    analysis = event.setdefault("analysis", {})
    analysis["usable_for_personal_vlog"] = True
    source_path = _path_key(event.get("source_path"))
    start = float(event.get("start") or 0.0)
    end = float(event.get("end") or 0.0)
    event.update({
        "window_id": (
            f"personal_vlog_visual_{source_order.get(source_path, 0):03d}_"
            f"{round(start * 1000):07d}"
        ),
        "duration": round(end - start, 3),
        "personal_vlog_event": {
            "event_kind": "visual_support",
            "source_order": source_order.get(source_path, 0),
            "dialogue_count": 0,
            "dialogue_seconds": 0.0,
            "transcript": "",
        },
        "personal_vlog_rank": round(float(analysis.get("confidence") or 0.0), 4),
    })
    return _annotate_story_event(event)


def _select_supplemental_visual_events(
    asset_index: Dict[str, Any],
    story_events: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    source_order = {
        _path_key(source.get("path")): index
        for index, source in enumerate(asset_index.get("sources") or [])
        if source.get("path")
    }
    candidates: List[Dict[str, Any]] = []
    invalid_count = 0
    covered_count = 0
    for window in asset_index.get("windows") or []:
        if _overlaps_story_event(window, story_events):
            covered_count += 1
            continue
        analysis = window.get("analysis") or {}
        frame_quality = window.get("frame_quality") or {}
        audio_context = window.get("audio_context") or {}
        duration = float(window.get("end") or 0.0) - float(window.get("start") or 0.0)
        role = str(analysis.get("product_story_role") or "unknown").strip().lower()
        has_speech = bool(
            audio_context.get("has_speech")
            or str(audio_context.get("transcript") or "").strip()
        )
        if has_speech:
            continue
        if (
            frame_quality.get("passed") is not True
            or duration < MIN_SUPPLEMENTAL_VISUAL_SECONDS
            or float(analysis.get("confidence") or 0.0)
            < MIN_SUPPLEMENTAL_VISUAL_CONFIDENCE
            or role in {"", "unknown"}
        ):
            invalid_count += 1
            continue
        candidate = _supplemental_visual_event(window, source_order)
        if _visual_features(candidate):
            candidates.append(candidate)
        else:
            invalid_count += 1

    selected: List[Dict[str, Any]] = []
    duplicate_count = 0
    seen_features = set().union(*(_visual_features(event) for event in story_events))
    seen_signatures = {
        tuple(sorted(_visual_features(event)))
        for event in story_events
        if _visual_features(event)
    }
    for candidate in sorted(
        candidates,
        key=lambda item: float(item.get("personal_vlog_rank") or 0.0),
        reverse=True,
    ):
        if any(
            _events_are_audio_visual_duplicates(candidate, existing)
            for existing in [*story_events, *selected]
        ):
            duplicate_count += 1
            continue
        features = _visual_features(candidate)
        signature = tuple(sorted(features))
        if not (features - seen_features) and signature in seen_signatures:
            continue
        selected.append(candidate)
        seen_features.update(features)
        seen_signatures.add(signature)

    return _story_order(selected), {
        "supplemental_visual_candidates": len(candidates),
        "supplemental_visual_events": len(selected),
        "invalid_visual_windows_removed": invalid_count,
        "covered_visual_windows": covered_count,
        "supplemental_visual_duplicates_removed": duplicate_count,
    }


def _opening_context_event(
    asset_index: Dict[str, Any],
    dialogue_events: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    source_order = {
        _path_key(source.get("path")): index
        for index, source in enumerate(asset_index.get("sources") or [])
        if source.get("path")
    }

    def overlaps_dialogue(window: Dict[str, Any]) -> bool:
        return any(
            _path_key(event.get("source_path")) == _path_key(window.get("source_path"))
            and float(window.get("start") or 0.0) < float(event.get("end") or 0.0)
            and float(window.get("end") or 0.0) > float(event.get("start") or 0.0)
            for event in dialogue_events
        )

    candidates = []
    first_dialogue = min(
        dialogue_events,
        key=lambda event: (
            int((event.get("personal_vlog_event") or {}).get("source_order") or 0),
            float(event.get("start") or 0.0),
        ),
    )
    first_source_order = int(
        (first_dialogue.get("personal_vlog_event") or {}).get("source_order") or 0
    )
    first_source_path = _path_key(first_dialogue.get("source_path"))
    first_start = float(first_dialogue.get("start") or 0.0)
    for window in asset_index.get("windows") or []:
        analysis = window.get("analysis") or {}
        frame_quality = window.get("frame_quality") or {}
        duration = float(window.get("end") or 0.0) - float(window.get("start") or 0.0)
        window_source_path = _path_key(window.get("source_path"))
        window_source_order = source_order.get(window_source_path, len(source_order))
        occurs_before_dialogue = (
            window_source_order < first_source_order
            or (
                window_source_path == first_source_path
                and float(window.get("end") or 0.0) <= first_start
            )
        )
        if (
            frame_quality.get("passed") is not True
            or duration < 3.0
            or overlaps_dialogue(window)
            or not occurs_before_dialogue
        ):
            continue
        role = str(analysis.get("product_story_role") or "unknown")
        role_bonus = 0.2 if role in {"context", "production", "usage"} else 0.0
        candidates.append((
            window_source_order,
            float(window.get("start") or 0.0),
            -(float(analysis.get("confidence") or 0.0) + role_bonus),
            window,
        ))
    if not candidates:
        return None

    _, _, _, selected = min(candidates, key=lambda item: (item[0], item[1], item[2]))
    event = copy.deepcopy(selected)
    analysis = event.setdefault("analysis", {})
    analysis.update({
        "usable_for_personal_vlog": True,
        "narrative_roles": list(dict.fromkeys([
            *(analysis.get("narrative_roles") or []),
            "personal_vlog",
        ])),
    })
    source_path = _path_key(event.get("source_path"))
    start = float(event.get("start") or 0.0)
    end = float(event.get("end") or 0.0)
    event.update({
        "window_id": f"personal_vlog_context_{source_order.get(source_path, 0):03d}_{round(start * 1000):07d}",
        "duration": round(end - start, 3),
        "personal_vlog_event": {
            "event_kind": "opening_context",
            "source_order": source_order.get(source_path, 0),
            "dialogue_count": 0,
            "dialogue_seconds": 0.0,
            "transcript": "",
            "story_phase": "orientation",
            "theme_relevance": 0.25,
        },
        "personal_vlog_rank": round(float(analysis.get("confidence") or 0.0), 4),
    })
    return event


def _select_story_events(
    events: List[Dict[str, Any]],
    preview: bool,
) -> Tuple[List[Dict[str, Any]], int]:
    prepared = [_annotate_story_event(event) for event in events]

    def selection_key(item: Dict[str, Any]) -> Tuple[float, float, float, int, float]:
        event_meta = item.get("personal_vlog_event") or {}
        return (
            float(event_meta.get("theme_relevance") or 0.0),
            float(item.get("personal_vlog_rank") or 0.0),
            float((item.get("analysis") or {}).get("confidence") or 0.0),
            -int(event_meta.get("source_order") or 0),
            -float(item.get("start") or 0.0),
        )

    chosen: List[Dict[str, Any]] = []
    duplicates_removed = 0
    for event in sorted(prepared, key=selection_key, reverse=True):
        if any(
            _events_are_audio_visual_duplicates(event, existing)
            for existing in chosen
        ):
            duplicates_removed += 1
            continue
        chosen.append(event)

    ordered = _story_order(chosen)
    return (ordered[:1] if preview else ordered), duplicates_removed


def _narrative_for_event(index: int, event: Dict[str, Any]) -> Tuple[str, str, str]:
    event_meta = event.get("personal_vlog_event") or {}
    event_kind = str(event_meta.get("event_kind") or "source_dialogue")
    if int(event_meta.get("dialogue_count") or 0) == 0 and event_kind != "visual_support":
        return (
            "opening_context",
            "story",
            "用连续现场画面建立时间与地点，不写促销式钩子",
        )
    phase = str(event_meta.get("story_phase") or "candid")
    if event_kind == "visual_support":
        goals = {
            "setup": "补充环境或准备细节，让日常主线更完整",
            "process": "补充原声没有覆盖的动作或产品过程，不重复已有画面",
            "interaction": "补充不同的人物互动或现场反应",
            "outcome": "补充收尾结果，让日常记录自然结束",
            "candid": "补充有新信息的生活细节，不为凑时长滥用素材",
        }
        return (
            f"visual_support_{phase}",
            "story",
            goals.get(phase, goals["candid"]),
        )
    narratives = {
        "orientation": (
            "source_dialogue_orientation",
            "用真实开拍或现场原声交代记录的开始",
        ),
        "setup": (
            "source_dialogue_setup",
            "保留准备过程，让日常主线自然展开",
        ),
        "process": (
            "source_dialogue_process",
            "让原声和连续动作说明事情如何发生，只补必要上下文",
        ),
        "interaction": (
            "source_dialogue_interaction",
            "保留现场人物互动，让分享有生活感而不是促销感",
        ),
        "outcome": (
            "source_dialogue_outcome",
            "用真实结果收住主线，不扩展成营销结论",
        ),
        "candid": (
            "source_dialogue_candid",
            "保留与主线相容的随口交流，维持轻松的日常质感",
        ),
    }
    narrative, copy_goal = narratives.get(phase, narratives["candid"])
    return narrative, "story", copy_goal


def build_personal_vlog_story_plan(
    asset_index: Dict[str, Any],
    product_info: Dict[str, Any],
    requested_duration: Optional[float] = None,
    preview: bool = False,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Build a dialogue-led Vlog contract and its bound long-take planning index."""
    all_dialogue_events = _event_windows(_safe_dialogue_anchors(asset_index, product_info))
    theme = _build_personal_vlog_theme(all_dialogue_events, product_info)
    dialogue_events, dialogue_duplicates_removed = _select_story_events(
        all_dialogue_events,
        preview,
    )
    if not dialogue_events:
        raise PersonalVlogPlanningError("没有完整、相关且事实安全的原始口播事件")
    opening_context = None if preview else _opening_context_event(asset_index, dialogue_events)
    base_events = [opening_context, *dialogue_events] if opening_context else dialogue_events
    supplemental_events: List[Dict[str, Any]] = []
    visual_selection = {
        "supplemental_visual_candidates": 0,
        "supplemental_visual_events": 0,
        "invalid_visual_windows_removed": 0,
        "covered_visual_windows": 0,
        "supplemental_visual_duplicates_removed": 0,
    }
    if not preview:
        supplemental_events, visual_selection = _select_supplemental_visual_events(
            asset_index,
            base_events,
        )
    events = _story_order([*base_events, *supplemental_events])

    segment_durations: Dict[int, float] = {}
    narrative_plan = []
    for index, event in enumerate(events):
        duration = float(event["end"]) - float(event["start"])
        segment_durations[index] = round(duration, 3)
        narrative, intent, copy_goal = _narrative_for_event(index, event)
        analysis = event.get("analysis") or {}
        event_meta = event.get("personal_vlog_event") or {}
        narrative_plan.append({
            "segment": index,
            "narrative": narrative,
            "marketing_intent": intent,
            "copy_goal": copy_goal,
            "event_kind": str(event_meta.get("event_kind") or "source_dialogue"),
            "story_phase": str(event_meta.get("story_phase") or "candid"),
            "theme_relevance": float(event_meta.get("theme_relevance") or 0.0),
            "product_story_role": str(analysis.get("product_story_role") or "unknown"),
            "asset_window_ids": [str(event["window_id"])],
            "matched_product_facts": analysis.get("matched_product_facts") or [],
            "planning_basis": {
                "source": "source_dialogue_event",
                "transcript": str(event_meta.get("transcript") or ""),
                "dialogue_count": int(event_meta.get("dialogue_count") or 0),
                "dialogue_seconds": float(event_meta.get("dialogue_seconds") or 0.0),
                "context_start": float(event["start"]),
                "context_end": float(event["end"]),
                "confidence": float(analysis.get("confidence") or 0.0),
            },
        })

    natural_duration = round(sum(segment_durations.values()), 3)
    requested = float(requested_duration) if requested_duration is not None else None
    planning_index = copy.deepcopy(asset_index)
    planning_index["windows"] = copy.deepcopy(events)
    planning_index["personal_vlog_planning"] = {
        "strategy": "audio_visual_narrative_gain",
        "event_count": len(events),
        "natural_main_duration": natural_duration,
    }
    selection_summary = {
        "source_dialogue_events": len(dialogue_events),
        "audio_visual_duplicates_removed": (
            dialogue_duplicates_removed
            + int(visual_selection["supplemental_visual_duplicates_removed"])
        ),
        **visual_selection,
    }
    contract = {
        "source": "personal_vlog_source_dialogue_understanding",
        "story_mode": "personal_vlog_source_dialogue",
        "theme": theme,
        "duration_source": "complete_audio_visual_story_events_with_context",
        "recommended_segments": len(events),
        "narrative_plan": narrative_plan,
        "selected_window_ids": [str(event["window_id"]) for event in events],
        "segment_durations": segment_durations,
        "natural_main_duration": natural_duration,
        "requested_duration": requested,
        "requested_duration_applied": bool(
            requested is not None and abs(requested - natural_duration) <= 0.25
        ),
        "selection_summary": selection_summary,
        "safe_source_dialogue_count": sum(
            int((event.get("personal_vlog_event") or {}).get("dialogue_count") or 0)
            for event in events
        ),
        "safe_source_dialogue_seconds": round(sum(
            float((event.get("personal_vlog_event") or {}).get("dialogue_seconds") or 0.0)
            for event in events
        ), 3),
    }
    return planning_index, contract


def build_personal_vlog_script(
    story_contract: Dict[str, Any],
    product_info: Dict[str, Any],
) -> Dict[str, Any]:
    """Build a source-dialogue-first Vlog script without sales-copy planning."""
    theme = story_contract.get("theme") or {}
    narrative_plan = list(story_contract.get("narrative_plan") or [])
    natural_duration = float(story_contract.get("natural_main_duration") or 0.0)
    narration_limit = 1 if natural_duration < 30.0 else 2 if natural_duration < 60.0 else 3
    visual_runs = []
    cursor = 0
    while cursor < len(narrative_plan):
        item = narrative_plan[cursor]
        if str(item.get("event_kind") or "source_dialogue") != "visual_support":
            cursor += 1
            continue
        run_start = cursor
        while (
            cursor + 1 < len(narrative_plan)
            and str(narrative_plan[cursor + 1].get("event_kind") or "source_dialogue")
            == "visual_support"
        ):
            cursor += 1
        run_end = cursor
        before = next(
            (
                str((candidate.get("planning_basis") or {}).get("transcript") or "").strip()
                for candidate in reversed(narrative_plan[:run_start])
                if str((candidate.get("planning_basis") or {}).get("transcript") or "").strip()
            ),
            "",
        )
        after = next(
            (
                str((candidate.get("planning_basis") or {}).get("transcript") or "").strip()
                for candidate in narrative_plan[run_end + 1:]
                if str((candidate.get("planning_basis") or {}).get("transcript") or "").strip()
            ),
            "",
        )
        if before and after:
            context = f"{before} {after}"
            if _MISHAP_DIALOGUE.search(context):
                narration = "没想到，镜头一开就有小状况。"
                context_score = 2
            elif _INTERACTION_DIALOGUE.search(context):
                narration = "忙归忙，现场的乐子也不少。"
                context_score = 2
            else:
                narration = "趁着空档，也看看今天的现场。"
                context_score = 1
            visual_runs.append((context_score, run_end - run_start + 1, run_start, narration))
        cursor += 1
    selected_narration = {
        run_start: narration
        for _score, _length, run_start, narration in sorted(
            sorted(visual_runs, key=lambda run: (-run[0], -run[1], run[2]))[:narration_limit],
            key=lambda run: run[2],
        )
    }
    segments = []
    for plan_position, item in enumerate(narrative_plan):
        index = int(item["segment"])
        transcript = str((item.get("planning_basis") or {}).get("transcript") or "")
        narration = selected_narration.get(plan_position, "")
        role = str(item.get("product_story_role") or "unknown")
        segments.append({
            "segment": index,
            "narrative": str(item.get("narrative") or "source_dialogue"),
            "marketing_intent": "story",
            "event_kind": str(item.get("event_kind") or "source_dialogue"),
            "story_phase": str(item.get("story_phase") or "candid"),
            "product_story_role": role,
            "voiceover": narration,
            "subtitle": narration,
            "claims": [],
            "asset_window_ids": list(item.get("asset_window_ids") or []),
            "source_dialogue": transcript,
        })
    name = str(product_info.get("name") or "日常")
    theme_type = str(theme.get("type") or "daily_life")
    hashtags = (
        ["#摆摊日常", "#日常分享"]
        if theme_type == "stall_life"
        else ["#日常记录", "#轻松日常"]
    )
    return {
        "title": str(theme.get("title") or f"我的{name}日常"),
        "hashtags": hashtags,
        "segments": segments,
        "generated_by": "personal_vlog_source_dialogue_contract",
        "generation_order": "audio_visual_story_events_then_sparse_gap_narration",
        "material_story_plan_applied": True,
        "route": "personal_vlog_source_dialogue",
        "story_world": {
            "format": "personal_vlog",
            "theme": copy.deepcopy(theme),
        },
    }


def materialize_personal_vlog_clips(
    planning_asset_index: Dict[str, Any],
    story_contract: Dict[str, Any],
    vlog_script: Dict[str, Any],
    clips_dir: Path,
    final_dir: Path,
    output_name: str,
    *,
    plan_only: bool = False,
) -> Dict[str, Any]:
    """Materialize bound Vlog events without advertisement ranking or sales gates."""
    from frame_evidence import write_frame_evidence_artifacts
    from local_asset_pipeline import (
        LocalAssetError,
        _materialize_clip,
        build_local_asset_creative_profile,
    )

    clips_dir.mkdir(parents=True, exist_ok=True)
    final_dir.mkdir(parents=True, exist_ok=True)
    windows = {
        str(window.get("window_id")): window
        for window in planning_asset_index.get("windows") or []
        if window.get("window_id")
    }
    scripts = {
        int(segment.get("segment", index)): segment
        for index, segment in enumerate(vlog_script.get("segments") or [])
    }
    selected_segments = []
    selected_windows = []
    for edit_index, plan_item in enumerate(story_contract.get("narrative_plan") or []):
        semantic_segment = int(plan_item["segment"])
        bound_ids = list(plan_item.get("asset_window_ids") or [])
        window = windows.get(str(bound_ids[0])) if bound_ids else None
        if window is None:
            raise LocalAssetError(f"个人 Vlog 事件 {semantic_segment} 缺少绑定素材窗口")
        source_path = Path(str(window.get("source_path") or ""))
        start = float(window.get("start") or 0.0)
        end = float(window.get("end") or 0.0)
        if end <= start:
            raise LocalAssetError(f"个人 Vlog 事件 {semantic_segment} 没有有效素材时长")
        clip_output = clips_dir / f"clip_{edit_index + 1:02d}_{output_name}_local.mp4"
        if not plan_only:
            _materialize_clip(source_path, start, end, clip_output)
        script_segment = scripts.get(semantic_segment, {})
        event = window.get("personal_vlog_event") or {}
        event_kind = str(event.get("event_kind") or "source_dialogue")
        selected_windows.append(window)
        selected_segments.append({
            "edit_index": edit_index,
            "semantic_segment": semantic_segment,
            "script_segment": semantic_segment,
            "narrative": str(plan_item.get("narrative") or "source_dialogue"),
            "event_kind": event_kind,
            "product_story_role": str(
                (window.get("analysis") or {}).get("product_story_role") or "unknown"
            ),
            "subtitle": str(script_segment.get("subtitle") or ""),
            "voiceover": str(script_segment.get("voiceover") or ""),
            "source_video": str(window.get("source_video") or source_path.name),
            "source_path": str(source_path),
            "source_start": start,
            "source_end": end,
            "target_duration": round(end - start, 3),
            "clip_path": str(clip_output),
            "match_score": 1.0,
            "score_details": {
                "planning_contract": 1.0,
                "source_dialogue_count": float(event.get("dialogue_count") or 0),
                "source_dialogue_seconds": float(event.get("dialogue_seconds") or 0.0),
            },
            "analysis": copy.deepcopy(window.get("analysis") or {}),
            "motion": {
                key: value
                for key, value in (window.get("motion") or {}).items()
                if key != "samples"
            },
            "frame_quality": {
                key: value
                for key, value in (window.get("frame_quality") or {}).items()
                if key != "samples"
            },
            "selection_reason": (
                "个人 Vlog 原声事件及其上下文"
                if int(event.get("dialogue_count") or 0) > 0
                else (
                    "个人 Vlog 视听叙事增益画面"
                    if event_kind == "visual_support"
                    else "个人 Vlog 现场建立镜头"
                )
            ),
        })

    if plan_only:
        return {
            "selected_segments": selected_segments,
            "bound_segments": copy.deepcopy(vlog_script.get("segments") or []),
            "plan_score": 1.0,
            "semantic_indices": [int(item["semantic_segment"]) for item in selected_segments],
        }

    creative_profile = build_local_asset_creative_profile({"windows": selected_windows})
    creative_profile["source"] = "personal_vlog_selected_events"
    final_manifest = final_dir / f"{output_name}_frame_evidence.json"
    final_report = final_dir / f"{output_name}_frame_evidence_report.html"
    cached_manifest = Path(str(planning_asset_index.get("frame_evidence_manifest") or ""))
    cached_report = Path(str(planning_asset_index.get("frame_evidence_report") or ""))
    if cached_manifest.is_file() and cached_report.is_file():
        shutil.copy2(cached_manifest, final_manifest)
        shutil.copy2(cached_report, final_report)
    else:
        write_frame_evidence_artifacts(selected_windows, final_manifest, final_report)

    report_path = final_dir / f"{output_name}_edit_decision_report.json"
    report = {
        "mode": "personal_vlog_audio_visual_narrative_gain",
        "asset_folder": planning_asset_index.get("asset_folder"),
        "coverage": planning_asset_index.get("coverage"),
        "plan_score": 1.0,
        "script_strategy": vlog_script.get("generated_by"),
        "selection_summary": copy.deepcopy(
            story_contract.get("selection_summary") or {}
        ),
        "creative_profile": creative_profile,
        "frame_evidence_manifest": str(final_manifest),
        "frame_evidence_report": str(final_report),
        "selected_segments": selected_segments,
        "bound_segments": copy.deepcopy(vlog_script.get("segments") or []),
        "rejected_candidates": [],
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    indices = [int(item["edit_index"]) for item in selected_segments]
    semantic_indices = [int(item["semantic_segment"]) for item in selected_segments]
    return {
        "clip_paths": [Path(item["clip_path"]) for item in selected_segments],
        "selected_indices": indices,
        "edit_indices": indices,
        "semantic_indices": semantic_indices,
        "edit_decision_report": report_path,
        "plan_score": 1.0,
        "creative_profile": creative_profile,
        "frame_evidence_manifest": final_manifest,
        "frame_evidence_report": final_report,
        "selected_segments": selected_segments,
        "bound_segments": copy.deepcopy(vlog_script.get("segments") or []),
    }
