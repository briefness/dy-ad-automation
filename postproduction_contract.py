"""One material-driven contract for every local-video post-production decision."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List


POSTPRODUCTION_CONTRACT_VERSION = 4


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
        contracts.append({
            "semantic_segment": semantic_segment,
            "edit_indices": [int(edit["edit_index"]) for edit in edits],
            "animation": animations[0] if len(set(animations)) == 1 else "fade",
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
