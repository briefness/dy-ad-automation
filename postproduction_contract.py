"""One material-driven contract for every local-video post-production decision."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List


POSTPRODUCTION_CONTRACT_VERSION = 5


def _subtitle_emphasis(narrative: str, product_story_role: str) -> str:
    normalized = str(narrative or "").lower()
    role = str(product_story_role or "").lower()
    if role in {"origin", "ingredient", "craft", "production"}:
        return "product_fact"
    if role in {"selling_point", "benefit", "feature", "proof", "result"}:
        return "selling_point"
    if any(
        token in normalized
        for token in ("selling_point", "benefit", "feature", "proof", "result", "comparison", "value")
    ):
        return "selling_point"
    return "normal"


def _subtitle_animation(narrative: str, motion_class: str) -> str:
    if motion_class == "dynamic":
        return "fade"
    normalized = str(narrative or "").lower()
    if motion_class == "semi_dynamic":
        return "highlight" if any(token in normalized for token in ("proof", "result", "showcase")) else "fade"
    if any(token in normalized for token in ("hook", "intro", "pain")):
        return "pop"
    if any(token in normalized for token in ("cta", "outro")):
        return "slide"
    if any(token in normalized for token in ("proof", "result", "usage", "demo")):
        return "highlight"
    return "fade"


def _evidence_emphasis_terms(selected: Dict[str, Any]) -> List[str]:
    analysis = selected.get("analysis") or {}
    values = [
        *(analysis.get("matched_product_entities") or []),
        *(selected.get("emphasis_terms") or []),
        *(analysis.get("matched_product_facts") or []),
    ]
    return list(dict.fromkeys(
        str(value).strip()
        for value in values
        if 2 <= len(str(value).strip()) <= 10
    ))


def _semantic_subtitle_contracts(
    segment_contracts: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Keep material-driven animation while placement remains platform-owned."""
    grouped: Dict[int, List[Dict[str, Any]]] = {}
    for segment in segment_contracts:
        grouped.setdefault(int(segment["semantic_segment"]), []).append(segment)
    contracts = []
    for semantic_segment, edits in grouped.items():
        animations = [str((edit.get("subtitle") or {}).get("animation") or "fade") for edit in edits]
        emphasized = [
            edit.get("subtitle") or {}
            for edit in edits
            if (edit.get("subtitle") or {}).get("emphasis")
        ]
        emphasis_kinds = {
            str(subtitle.get("emphasis_kind") or "normal")
            for subtitle in emphasized
        }
        emphasis_topics = {
            str(subtitle.get("emphasis_topic") or "")
            for subtitle in emphasized
        }
        emphasis_terms = list(dict.fromkeys(
            str(term).strip()
            for subtitle in emphasized
            for term in subtitle.get("emphasis_terms") or []
            if str(term).strip()
        ))
        emphasis_kinds.discard("normal")
        emphasis_topics.discard("")
        emphasis_kind = (
            "normal"
            if not emphasis_kinds
            else next(iter(emphasis_kinds))
            if len(emphasis_kinds) == 1
            else "semantic_priority"
        )
        contracts.append({
            "semantic_segment": semantic_segment,
            "edit_indices": [int(edit["edit_index"]) for edit in edits],
            "animation": animations[0] if len(set(animations)) == 1 else "fade",
            "emphasis": bool(emphasis_kinds),
            "emphasis_kind": emphasis_kind,
            "emphasis_topic": next(iter(emphasis_topics)) if len(emphasis_topics) == 1 else "",
            "emphasis_terms": emphasis_terms,
            "placement_policy": "platform_fixed_bottom_safe_area",
        })
    return contracts


def build_local_postproduction_contract(
    selected_segments: List[Dict[str, Any]],
    creative_profile: Dict[str, Any],
    music_contract: Dict[str, Any],
    reference_profile: Dict[str, Any],
) -> Dict[str, Any]:
    """Derive all downstream decisions from the actual selected local clips."""
    segment_contracts = []
    for selected in selected_segments:
        motion = selected.get("motion") or {}
        motion_class = str(motion.get("motion_class") or "static")
        narrative = str(selected.get("narrative") or "showcase")
        frame_quality = selected.get("frame_quality") or {}
        emphasis_kind = _subtitle_emphasis(narrative, selected.get("product_story_role") or "unknown")
        emphasis_terms = _evidence_emphasis_terms(selected)
        emphasis_enabled = emphasis_kind != "normal" and bool(emphasis_terms)
        semantic_segment = int(
            selected.get("semantic_segment", selected.get("script_segment", len(segment_contracts)))
        )
        edit_index = int(selected.get("edit_index", len(segment_contracts)))
        segment_contracts.append({
            "segment": semantic_segment,
            "semantic_segment": semantic_segment,
            "edit_index": edit_index,
            "clip_path": str(selected.get("clip_path") or ""),
            "source_video": str(selected.get("source_video") or ""),
            "source_start": float(selected.get("source_start") or 0.0),
            "source_end": float(selected.get("source_end") or 0.0),
            "narrative": narrative,
            "product_story_role": str(selected.get("product_story_role") or "unknown"),
            "motion": {
                "class": motion_class,
                "camera": str(motion.get("camera_motion") or "static"),
                "speed": float(motion.get("camera_speed") or 0.0),
            },
            "subtitle": {
                "animation": _subtitle_animation(narrative, motion_class),
                "emphasis": emphasis_enabled,
                "emphasis_kind": emphasis_kind if emphasis_enabled else "normal",
                "emphasis_topic": (
                    str(selected.get("product_story_role") or "").lower()
                    if emphasis_enabled and emphasis_kind == "product_fact"
                    else ""
                ),
                "emphasis_terms": emphasis_terms if emphasis_enabled else [],
            },
            "color": {
                "policy": "preserve_source",
                "median_brightness": frame_quality.get("median_brightness"),
                "median_contrast": frame_quality.get("median_contrast"),
                "brand_tint_allowed": False,
            },
        })
    semantic_subtitles = _semantic_subtitle_contracts(segment_contracts)

    external_cta = bool(
        reference_profile.get("cta_text")
        and float(reference_profile.get("outro_duration") or 0.0) > 0
    )
    return {
        "version": POSTPRODUCTION_CONTRACT_VERSION,
        "source": "selected_local_assets",
        "segments": segment_contracts,
        "semantic_subtitles": semantic_subtitles,
        "voice": {
            "energy": str(creative_profile.get("energy") or "medium"),
            "pace": str(creative_profile.get("recommended_pace") or "moderate"),
        },
        "bgm": {
            "required": True,
            "fallback_allowed": False,
            "bpm_min": int(music_contract.get("bpm_min") or 0),
            "bpm_max": int(music_contract.get("bpm_max") or 0),
            "energy": str(creative_profile.get("energy") or music_contract.get("energy") or "medium"),
            "sfx_intensity": str(music_contract.get("sfx_intensity") or "moderate"),
        },
        "transition": {
            "allow_none": True,
            "policy": "actual_boundary_render_quality",
        },
        "subtitle_style": {
            "font_size_ratio": 0.035,
            "placement_policy": "platform_fixed_bottom_safe_area",
        },
        "cta": {
            "enabled": external_cta,
            "text": str(reference_profile.get("cta_text") or ""),
            "duration": float(reference_profile.get("outro_duration") or 0.0),
            "visual_mode": "closing_frame_tail_card" if external_cta else "in_scene",
            "continuous_voiceover": True,
        },
    }


def write_postproduction_contract(contract: Dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(contract, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
