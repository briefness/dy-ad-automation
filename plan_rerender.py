"""Interactive Human-in-the-Loop re-rendering from an edited local production plan."""

from __future__ import annotations

import json
import shutil
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional

from production_plan import (
    ensure_valid_local_production_plan,
    update_local_production_plan,
    write_production_plan_artifacts,
)
from render_backend import render_single_pass_composite, video_encode_args
from smart_reframe import build_smart_reframe_vf
from timeline_export import export_all_nle_timelines
from visual_search import rank_similar_windows


def apply_plan_edits(
    plan: Dict[str, Any],
    edits: Dict[str, Any],
) -> Dict[str, Any]:
    """Apply user segment/subtitle/sticker overrides to a production plan."""
    updated = deepcopy(plan)
    seg_edits = {
        int(item["edit_index"]): item
        for item in (edits.get("segments") or [])
        if "edit_index" in item
    }

    segments = updated.get("segments") or []
    subtitles = updated.get("subtitles") or []

    for seg in segments:
        eidx = int(seg.get("edit_index", 0))
        patch = seg_edits.get(eidx)
        if not patch:
            continue
        if patch.get("source_video"):
            seg["source_video"] = str(patch["source_video"])
            seg["clip_path"] = str(patch.get("clip_path") or "")
        if "source_start" in patch and patch["source_start"] is not None:
            seg["source_start"] = max(0.0, float(patch["source_start"]))
            seg["source_range_available"] = True
        if "source_end" in patch and patch["source_end"] is not None:
            seg["source_end"] = float(patch["source_end"])
            seg["source_range_available"] = True
        if "target_duration" in patch and patch["target_duration"] is not None:
            seg["target_duration"] = max(0.5, float(patch["target_duration"]))
        if "script_subtitle" in patch and patch["script_subtitle"] is not None:
            new_text = str(patch["script_subtitle"]).strip()
            seg["script_subtitle"] = new_text
            sem_idx = int(seg.get("semantic_segment", eidx))
            for sub in subtitles:
                if int(sub.get("segment", -1)) == sem_idx:
                    sub["text"] = new_text

    if "disabled_sticker_indices" in edits:
        disabled = {int(i) for i in edits.get("disabled_sticker_indices") or []}
        stickers = updated.get("stickers") or {}
        items = stickers.get("items") or []
        kept = []
        skipped = list(stickers.get("skipped") or [])
        for idx, item in enumerate(items):
            if idx in disabled:
                skipped.append({**item, "reason": "user_disabled"})
            else:
                kept.append(item)
        stickers["items"] = kept
        stickers["skipped"] = skipped
        updated["stickers"] = stickers

    updated = update_local_production_plan(
        updated,
        subtitles=subtitles,
        sticker_plan=updated.get("stickers"),
    )
    return ensure_valid_local_production_plan(updated)


def find_candidate_windows_for_plan(
    plan: Dict[str, Any],
    index_root: Path,
    *,
    limit_per_segment: int = 6,
) -> Dict[int, List[Dict[str, Any]]]:
    """Find alternative shots from cached local asset indexes for each plan segment."""
    index_root = Path(index_root)
    if not index_root.is_dir():
        return {}
    windows: List[Dict[str, Any]] = []
    for idx_file in sorted(index_root.glob("*/index.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(idx_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("windows"):
            windows.extend(data["windows"])
            break

    if not windows:
        return {}

    result: Dict[int, List[Dict[str, Any]]] = {}
    for seg in plan.get("segments") or []:
        eidx = int(seg.get("edit_index", 0))
        result[eidx] = rank_similar_windows(seg, windows, limit=limit_per_segment)
    return result


def rerender_production_plan(
    plan_path: Path,
    edits: Optional[Dict[str, Any]] = None,
    *,
    output_dir: Optional[Path] = None,
    resolution: str = "1080x1920",
    fps: int = 30,
    bitrate: str = "6M",
    reframe_mode: str = "auto",
    audio_path: Optional[Path] = None,
    export_nle: bool = True,
) -> Dict[str, Any]:
    """Re-render a local production plan directly without re-running VLM or LLM.

    Uses Smart Reframe + hardware-accelerated clip cutting + single-pass composite
    rendering, then refreshes ``production_plan.json``, review HTML, and NLE exports.
    """
    import subprocess
    from video_merger import (
        add_fancy_subtitles,
        merge_clips_ffmpeg,
        prepare_single_line_subtitles,
    )

    plan_path = Path(plan_path).resolve()
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if edits:
        plan = apply_plan_edits(plan, edits)
    else:
        plan = ensure_valid_local_production_plan(plan)

    stem = plan_path.name.removesuffix("_production_plan.json")
    out_dir = Path(output_dir).resolve() if output_dir else plan_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    rerender_stem = f"{stem}_rerender"
    target_w, target_h = (int(part) for part in resolution.split("x"))

    segments = sorted(plan.get("segments") or [], key=lambda item: int(item.get("edit_index", 0)))
    if not segments:
        raise RuntimeError("成片计划不包含任何有效镜头")

    with tempfile.TemporaryDirectory(prefix="plan-rerender-") as tmp:
        tmp_dir = Path(tmp)
        clips: List[Path] = []
        for pos, seg in enumerate(segments):
            src_video = Path(str(seg.get("source_video") or ""))
            clip_file = Path(str(seg.get("clip_path") or ""))
            media = src_video if src_video.is_file() else clip_file
            if not media.is_file():
                raise RuntimeError(f"找不到镜头 {pos + 1} 的本地素材文件：{media}")

            use_source_range = media == src_video and bool(seg.get("source_range_available"))
            start = max(0.0, float(seg.get("source_start") or 0.0)) if use_source_range else 0.0
            end = float(seg.get("source_end") or 0.0) if use_source_range else 0.0
            target_dur = float(seg.get("target_duration") or 0.0)
            dur = target_dur if target_dur > 0.2 else max(0.5, end - start)
            if end <= start:
                end = start + dur

            reframe = build_smart_reframe_vf(
                media, start, end, target_w, target_h, mode=reframe_mode
            )
            cut_out = tmp_dir / f"seg_{pos:02d}.mp4"
            cmd = [
                "ffmpeg", "-y",
                "-ss", f"{start:.3f}",
                "-i", str(media),
                "-t", f"{dur:.3f}",
                "-vf", reframe["vf"],
                *video_encode_args(profile="intermediate", bitrate=bitrate, allow_hw=True, fps=fps),
                "-an",
                "-movflags", "+faststart",
                str(cut_out),
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180, check=False)
            if proc.returncode != 0 or not cut_out.is_file():
                raise RuntimeError(f"镜头 {pos + 1} 裁剪失败：{(proc.stderr or '')[-400:]}")
            clips.append(cut_out)

        merged_path = tmp_dir / "merged.mp4"
        transitions = [{"type": "dissolve", "duration": 0.2}] * max(0, len(clips) - 1)
        merge_clips_ffmpeg(
            clips=clips,
            output=merged_path,
            transitions=transitions,
            bgm=None,
            strict_transitions=False,
        )

        # Preserve audio track from existing final video or explicit audio_path if available
        muxed_path = merged_path
        existing_audio_source = Path(audio_path) if audio_path and Path(audio_path).is_file() else None
        if existing_audio_source is None:
            candidate_final = plan_path.parent / f"{stem}_final.mp4"
            if candidate_final.is_file():
                existing_audio_source = candidate_final
        if existing_audio_source is not None:
            muxed_candidate = tmp_dir / "muxed.mp4"
            mux_proc = subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-i", str(merged_path),
                    "-i", str(existing_audio_source),
                    "-map", "0:v:0",
                    "-map", "1:a:0?",
                    "-c:v", "copy",
                    "-c:a", "aac", "-b:a", "192k",
                    "-shortest",
                    str(muxed_candidate),
                ],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            if mux_proc.returncode == 0 and muxed_candidate.is_file():
                muxed_path = muxed_candidate

        subtitled_path = muxed_path
        raw_subs = [s for s in (plan.get("subtitles") or []) if str(s.get("text") or "").strip()]
        if raw_subs:
            safe_subs = prepare_single_line_subtitles(raw_subs)
            subtitled_candidate = tmp_dir / "subtitled.mp4"
            add_fancy_subtitles(muxed_path, safe_subs, subtitled_candidate)
            if subtitled_candidate.is_file():
                subtitled_path = subtitled_candidate

        final_path = out_dir / f"{rerender_stem}_final.mp4"
        render_single_pass_composite(
            subtitled_path,
            final_path,
            resolution=resolution,
            fps=fps,
            bitrate=bitrate,
            normalize_audio=True,
            profile="final",
        )

    new_plan_path = out_dir / f"{rerender_stem}_production_plan.json"
    new_review_path = out_dir / f"{rerender_stem}_timeline_review.html"
    write_production_plan_artifacts(plan, new_plan_path, new_review_path)

    nle_exports: Dict[str, str] = {}
    if export_nle:
        exported = export_all_nle_timelines(
            plan,
            out_dir,
            rerender_stem,
            resolution=resolution,
            fps=fps,
        )
        nle_exports = {k: str(v) for k, v in exported.items()}

    return {
        "final_path": str(final_path),
        "production_plan": str(new_plan_path),
        "timeline_review": str(new_review_path),
        "nle_exports": nle_exports,
    }
