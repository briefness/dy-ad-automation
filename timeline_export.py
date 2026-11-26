"""Non-destructive NLE timeline exporters (JianYing/CapCut Draft, OpenTimelineIO, FCPXML)."""

from __future__ import annotations

import json
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

MICROSECOND = 1_000_000


def _to_us(seconds: float) -> int:
    return max(0, int(round(float(seconds or 0.0) * MICROSECOND)))


def _file_uri(path_str: str) -> str:
    raw = str(path_str or "").strip()
    if not raw:
        return ""
    try:
        return Path(raw).expanduser().resolve().as_uri()
    except Exception:
        return "file://" + quote(raw)


def _segment_timeline_entries(plan: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Resolve ordered timeline entries with exact source and target ranges."""
    segments = sorted(
        plan.get("segments") or [],
        key=lambda item: int(item.get("edit_index", 0)),
    )
    entries: List[Dict[str, Any]] = []
    cursor = 0.0
    for index, seg in enumerate(segments):
        source_start = max(0.0, float(seg.get("source_start") or 0.0))
        source_end = float(seg.get("source_end") or 0.0)
        target_dur = float(seg.get("target_duration") or 0.0)
        source_dur = max(0.0, source_end - source_start)
        duration = target_dur if target_dur > 0.05 else (source_dur if source_dur > 0.05 else 3.0)
        if source_dur <= 0.05:
            source_dur = duration
        source_path = str(seg.get("source_video") or seg.get("clip_path") or "")
        clip_path = str(seg.get("clip_path") or source_path)
        entries.append({
            "index": index,
            "edit_index": int(seg.get("edit_index", index)),
            "semantic_segment": int(seg.get("semantic_segment", index)),
            "name": Path(source_path).name if source_path else f"shot_{index + 1}",
            "source_path": source_path,
            "clip_path": clip_path,
            "source_start": source_start,
            "source_duration": min(source_dur, duration) if source_dur > 0 else duration,
            "target_start": round(cursor, 4),
            "target_duration": round(duration, 4),
            "subtitle": str(seg.get("script_subtitle") or ""),
            "narrative": str(seg.get("narrative") or ""),
        })
        cursor += duration
    return entries


def _normalize_audio_tracks(
    audio_tracks: Optional[List[Dict[str, Any]]],
    total_duration: float,
) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for idx, track in enumerate(audio_tracks or []):
        path = str(track.get("path") or "").strip()
        if not path:
            continue
        start = max(0.0, float(track.get("start") or 0.0))
        duration = float(track.get("duration") or max(0.1, total_duration - start))
        source_start = max(0.0, float(track.get("source_start") or 0.0))
        normalized.append({
            "name": str(track.get("name") or Path(path).stem or f"audio_{idx + 1}"),
            "role": str(track.get("role") or "audio"),
            "path": path,
            "start": start,
            "duration": max(0.1, duration),
            "source_start": source_start,
            "volume": float(track.get("volume", 1.0)),
        })
    return normalized


def export_jianying_draft(
    plan: Dict[str, Any],
    draft_dir: Path,
    *,
    draft_name: Optional[str] = None,
    audio_tracks: Optional[List[Dict[str, Any]]] = None,
    resolution: str = "1080x1920",
    fps: int = 30,
) -> Path:
    """Export ``production_plan`` to a native JianYing / CapCut draft directory.

    Writes ``draft_content.json`` and ``draft_meta_info.json`` following JianYing's
    microsecond-precision track & material schema so the project opens cleanly in
    JianYing / CapCut for fine-grained human editing.
    """
    draft_dir = Path(draft_dir)
    draft_dir.mkdir(parents=True, exist_ok=True)
    width, height = (int(part) for part in resolution.split("x"))
    entries = _segment_timeline_entries(plan)
    total_duration = sum(item["target_duration"] for item in entries)
    total_us = _to_us(total_duration)
    audios = _normalize_audio_tracks(audio_tracks, total_duration)

    video_materials: List[Dict[str, Any]] = []
    video_segments: List[Dict[str, Any]] = []
    for entry in entries:
        mat_id = str(uuid.uuid4()).upper()
        seg_id = str(uuid.uuid4()).upper()
        media_path = entry["source_path"] if Path(entry["source_path"]).is_file() else entry["clip_path"]
        src_start_us = _to_us(entry["source_start"]) if media_path == entry["source_path"] else 0
        src_dur_us = _to_us(entry["source_duration"])
        tgt_start_us = _to_us(entry["target_start"])
        tgt_dur_us = _to_us(entry["target_duration"])
        video_materials.append({
            "id": mat_id,
            "type": "video",
            "material_name": entry["name"],
            "path": media_path,
            "width": width,
            "height": height,
            "duration": max(src_start_us + src_dur_us, tgt_dur_us),
            "category_name": "local",
        })
        video_segments.append({
            "id": seg_id,
            "material_id": mat_id,
            "source_timerange": {"start": src_start_us, "duration": src_dur_us},
            "target_timerange": {"start": tgt_start_us, "duration": tgt_dur_us},
            "speed": round(src_dur_us / max(tgt_dur_us, 1), 4),
            "volume": 0.0,
            "visible": True,
            "clip": {
                "alpha": 1.0,
                "flip": {"horizontal": False, "vertical": False},
                "rotation": 0.0,
                "scale": {"x": 1.0, "y": 1.0},
                "transform": {"x": 0.0, "y": 0.0},
            },
        })

    audio_materials: List[Dict[str, Any]] = []
    audio_track_objs: List[Dict[str, Any]] = []
    for track in audios:
        mat_id = str(uuid.uuid4()).upper()
        seg_id = str(uuid.uuid4()).upper()
        dur_us = _to_us(track["duration"])
        src_start_us = _to_us(track["source_start"])
        audio_materials.append({
            "id": mat_id,
            "type": "extract_music" if track["role"] == "bgm" else "music",
            "name": track["name"],
            "path": track["path"],
            "duration": src_start_us + dur_us,
        })
        audio_track_objs.append({
            "id": str(uuid.uuid4()).upper(),
            "type": "audio",
            "attribute": 0,
            "flag": 0,
            "segments": [{
                "id": seg_id,
                "material_id": mat_id,
                "source_timerange": {"start": src_start_us, "duration": dur_us},
                "target_timerange": {"start": _to_us(track["start"]), "duration": dur_us},
                "volume": track["volume"],
                "visible": True,
            }],
        })

    text_materials: List[Dict[str, Any]] = []
    subtitle_segments: List[Dict[str, Any]] = []
    for sub in plan.get("subtitles") or []:
        text = str(sub.get("text") or "").strip()
        if not text:
            continue
        start = max(0.0, float(sub.get("start") or 0.0))
        end = max(start + 0.1, float(sub.get("end") or start + 1.5))
        mat_id = str(uuid.uuid4()).upper()
        seg_id = str(uuid.uuid4()).upper()
        rich_content = json.dumps({
            "text": text,
            "styles": [{
                "fill": {"content": {"solid": {"color": [1.0, 1.0, 1.0]}}},
                "size": 8.0,
                "range": [0, len(text)],
            }],
        }, ensure_ascii=False)
        text_materials.append({
            "id": mat_id,
            "type": "subtitle",
            "content": rich_content,
            "font_size": 8.0,
            "alignment": 1,
        })
        subtitle_segments.append({
            "id": seg_id,
            "material_id": mat_id,
            "target_timerange": {"start": _to_us(start), "duration": _to_us(end - start)},
            "clip": {
                "alpha": 1.0,
                "scale": {"x": 1.0, "y": 1.0},
                "transform": {"x": 0.0, "y": -0.56},
            },
            "visible": True,
        })

    sticker_segments: List[Dict[str, Any]] = []
    for sticker in (plan.get("stickers") or {}).get("items") or []:
        text = str(sticker.get("text") or "").strip()
        if not text:
            continue
        start = max(0.0, float(sticker.get("start") or 0.0))
        end = max(start + 0.2, float(sticker.get("end") or start + 2.0))
        mat_id = str(uuid.uuid4()).upper()
        seg_id = str(uuid.uuid4()).upper()
        text_materials.append({
            "id": mat_id,
            "type": "text",
            "content": json.dumps({"text": text, "styles": []}, ensure_ascii=False),
            "font_size": 7.0,
            "alignment": 1,
        })
        sticker_segments.append({
            "id": seg_id,
            "material_id": mat_id,
            "target_timerange": {"start": _to_us(start), "duration": _to_us(end - start)},
            "clip": {
                "alpha": 1.0,
                "scale": {"x": 1.0, "y": 1.0},
                "transform": {"x": 0.0, "y": 0.35},
            },
            "visible": True,
        })

    tracks: List[Dict[str, Any]] = [{
        "id": str(uuid.uuid4()).upper(),
        "type": "video",
        "attribute": 0,
        "flag": 0,
        "segments": video_segments,
    }]
    tracks.extend(audio_track_objs)
    if subtitle_segments:
        tracks.append({
            "id": str(uuid.uuid4()).upper(),
            "type": "text",
            "attribute": 0,
            "flag": 0,
            "segments": subtitle_segments,
        })
    if sticker_segments:
        tracks.append({
            "id": str(uuid.uuid4()).upper(),
            "type": "text",
            "attribute": 0,
            "flag": 0,
            "segments": sticker_segments,
        })

    draft_id = str(uuid.uuid4()).upper()
    now_us = int(time.time() * MICROSECOND)
    draft_content = {
        "id": draft_id,
        "version": 360000,
        "fps": float(fps),
        "duration": total_us,
        "canvas_config": {"width": width, "height": height, "ratio": "original"},
        "materials": {
            "videos": video_materials,
            "audios": audio_materials,
            "texts": text_materials,
            "stickers": [],
            "transitions": [],
        },
        "tracks": tracks,
        "extra_info": {
            "generator": "kling-ad-automation/timeline_export",
            "production_plan_version": plan.get("version", 1),
        },
    }
    draft_meta = {
        "draft_id": draft_id,
        "draft_name": draft_name or draft_dir.name,
        "draft_fold_path": str(draft_dir.resolve()),
        "draft_root_path": str(draft_dir.parent.resolve()),
        "tm_draft_create": now_us,
        "tm_draft_modified": now_us,
        "tm_duration": total_us,
    }

    content_path = draft_dir / "draft_content.json"
    meta_path = draft_dir / "draft_meta_info.json"
    content_path.write_text(json.dumps(draft_content, ensure_ascii=False, indent=2), encoding="utf-8")
    meta_path.write_text(json.dumps(draft_meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return content_path


def _rational_time(seconds: float, rate: float) -> Dict[str, Any]:
    return {
        "OTIO_SCHEMA": "RationalTime.1",
        "rate": float(rate),
        "value": round(float(seconds) * float(rate), 3),
    }


def _time_range(start_sec: float, dur_sec: float, rate: float) -> Dict[str, Any]:
    return {
        "OTIO_SCHEMA": "TimeRange.1",
        "start_time": _rational_time(start_sec, rate),
        "duration": _rational_time(dur_sec, rate),
    }


def export_otio_timeline(
    plan: Dict[str, Any],
    output_path: Path,
    *,
    timeline_name: Optional[str] = None,
    audio_tracks: Optional[List[Dict[str, Any]]] = None,
    fps: int = 30,
) -> Path:
    """Export ``production_plan`` as a standards-compliant OpenTimelineIO (.otio) file."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    entries = _segment_timeline_entries(plan)
    total_duration = sum(item["target_duration"] for item in entries)
    audios = _normalize_audio_tracks(audio_tracks, total_duration)

    video_children: List[Dict[str, Any]] = []
    for entry in entries:
        media_path = entry["source_path"] if Path(entry["source_path"]).is_file() else entry["clip_path"]
        src_start = entry["source_start"] if media_path == entry["source_path"] else 0.0
        video_children.append({
            "OTIO_SCHEMA": "Clip.1",
            "name": entry["name"],
            "source_range": _time_range(src_start, entry["target_duration"], fps),
            "media_reference": {
                "OTIO_SCHEMA": "ExternalReference.1",
                "target_url": _file_uri(media_path),
                "available_range": _time_range(0.0, src_start + entry["source_duration"], fps),
            },
            "metadata": {
                "edit_index": entry["edit_index"],
                "semantic_segment": entry["semantic_segment"],
                "narrative": entry["narrative"],
                "subtitle": entry["subtitle"],
            },
            "effects": [],
            "markers": [],
        })

    markers: List[Dict[str, Any]] = []
    for sub in plan.get("subtitles") or []:
        text = str(sub.get("text") or "").strip()
        if not text:
            continue
        start = max(0.0, float(sub.get("start") or 0.0))
        end = max(start + 0.1, float(sub.get("end") or start + 1.5))
        markers.append({
            "OTIO_SCHEMA": "Marker.1",
            "name": text,
            "color": "CYAN",
            "marked_range": _time_range(start, end - start, fps),
            "metadata": {"kind": "subtitle", "fancy": bool(sub.get("fancy"))},
        })
    for sticker in (plan.get("stickers") or {}).get("items") or []:
        text = str(sticker.get("text") or "").strip()
        if not text:
            continue
        start = max(0.0, float(sticker.get("start") or 0.0))
        end = max(start + 0.1, float(sticker.get("end") or start + 2.0))
        markers.append({
            "OTIO_SCHEMA": "Marker.1",
            "name": f"[贴图] {text}",
            "color": "ORANGE",
            "marked_range": _time_range(start, end - start, fps),
            "metadata": {"kind": "sticker", "sticker_kind": sticker.get("kind", "")},
        })

    tracks: List[Dict[str, Any]] = [{
        "OTIO_SCHEMA": "Track.1",
        "name": "V1",
        "kind": "Video",
        "children": video_children,
        "effects": [],
        "markers": markers,
        "metadata": {},
    }]

    for idx, audio in enumerate(audios, start=1):
        clip = {
            "OTIO_SCHEMA": "Clip.1",
            "name": audio["name"],
            "source_range": _time_range(audio["source_start"], audio["duration"], fps),
            "media_reference": {
                "OTIO_SCHEMA": "ExternalReference.1",
                "target_url": _file_uri(audio["path"]),
                "available_range": _time_range(0.0, audio["source_start"] + audio["duration"], fps),
            },
            "metadata": {"role": audio["role"], "volume": audio["volume"]},
            "effects": [],
            "markers": [],
        }
        children: List[Dict[str, Any]] = []
        if audio["start"] > 0.01:
            children.append({
                "OTIO_SCHEMA": "Gap.1",
                "name": "offset",
                "source_range": _time_range(0.0, audio["start"], fps),
                "effects": [],
                "markers": [],
                "metadata": {},
            })
        children.append(clip)
        tracks.append({
            "OTIO_SCHEMA": "Track.1",
            "name": f"A{idx}_{audio['role']}",
            "kind": "Audio",
            "children": children,
            "effects": [],
            "markers": [],
            "metadata": {},
        })

    otio_doc = {
        "OTIO_SCHEMA": "Timeline.1",
        "name": timeline_name or output_path.stem,
        "global_start_time": _rational_time(0.0, fps),
        "metadata": {
            "generator": "kling-ad-automation/timeline_export",
            "production_plan_version": plan.get("version", 1),
        },
        "tracks": {
            "OTIO_SCHEMA": "Stack.1",
            "name": "tracks",
            "children": tracks,
            "effects": [],
            "markers": [],
            "metadata": {},
        },
    }
    output_path.write_text(json.dumps(otio_doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def _fcpx_time(seconds: float, fps: int) -> str:
    frames = max(0, int(round(float(seconds or 0.0) * fps)))
    return f"{frames}/{fps}s"


def export_fcpxml(
    plan: Dict[str, Any],
    output_path: Path,
    *,
    project_name: Optional[str] = None,
    audio_tracks: Optional[List[Dict[str, Any]]] = None,
    resolution: str = "1080x1920",
    fps: int = 30,
) -> Path:
    """Export ``production_plan`` as FCPXML v1.10 (DaVinci Resolve / Final Cut Pro)."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    width, height = (int(part) for part in resolution.split("x"))
    entries = _segment_timeline_entries(plan)
    total_duration = sum(item["target_duration"] for item in entries)
    audios = _normalize_audio_tracks(audio_tracks, total_duration)

    root = ET.Element("fcpxml", version="1.10")
    resources = ET.SubElement(root, "resources")
    ET.SubElement(
        resources,
        "format",
        id="r1",
        name=f"FFVideoFormat{width}x{height}p{fps}",
        frameDuration=f"1/{fps}s",
        width=str(width),
        height=str(height),
    )

    asset_ids: Dict[str, str] = {}
    next_rid = 2

    def register_asset(path_str: str, dur_sec: float, has_video: bool, has_audio: bool) -> str:
        nonlocal next_rid
        key = f"{path_str}|{has_video}|{has_audio}"
        if key in asset_ids:
            return asset_ids[key]
        rid = f"r{next_rid}"
        next_rid += 1
        asset = ET.SubElement(
            resources,
            "asset",
            id=rid,
            name=Path(path_str).name if path_str else rid,
            start="0s",
            duration=_fcpx_time(max(dur_sec, 0.1), fps),
            hasVideo="1" if has_video else "0",
            hasAudio="1" if has_audio else "0",
            format="r1",
        )
        ET.SubElement(asset, "media-rep", kind="original-media", src=_file_uri(path_str))
        asset_ids[key] = rid
        return rid

    library = ET.SubElement(root, "library")
    event = ET.SubElement(library, "event", name=project_name or output_path.stem)
    project = ET.SubElement(event, "project", name=project_name or output_path.stem)
    sequence = ET.SubElement(
        project,
        "sequence",
        format="r1",
        duration=_fcpx_time(total_duration, fps),
        tcStart="0s",
        tcFormat="NDF",
    )
    spine = ET.SubElement(sequence, "spine")

    for entry in entries:
        media_path = entry["source_path"] if Path(entry["source_path"]).is_file() else entry["clip_path"]
        src_start = entry["source_start"] if media_path == entry["source_path"] else 0.0
        rid = register_asset(media_path, src_start + entry["source_duration"], True, False)
        clip_el = ET.SubElement(
            spine,
            "asset-clip",
            ref=rid,
            offset=_fcpx_time(entry["target_start"], fps),
            name=entry["name"],
            start=_fcpx_time(src_start, fps),
            duration=_fcpx_time(entry["target_duration"], fps),
        )
        if entry["subtitle"]:
            ET.SubElement(
                clip_el,
                "marker",
                start=_fcpx_time(src_start, fps),
                duration=f"1/{fps}s",
                value=entry["subtitle"],
            )

    for lane_idx, audio in enumerate(audios, start=1):
        rid = register_asset(audio["path"], audio["source_start"] + audio["duration"], False, True)
        ET.SubElement(
            spine,
            "asset-clip",
            ref=rid,
            lane=str(-lane_idx),
            offset=_fcpx_time(audio["start"], fps),
            name=audio["name"],
            start=_fcpx_time(audio["source_start"], fps),
            duration=_fcpx_time(audio["duration"], fps),
            role=audio["role"],
        )

    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(output_path, encoding="utf-8", xml_declaration=True)
    return output_path


def export_all_nle_timelines(
    plan: Dict[str, Any],
    output_dir: Path,
    stem: str,
    *,
    audio_tracks: Optional[List[Dict[str, Any]]] = None,
    resolution: str = "1080x1920",
    fps: int = 30,
) -> Dict[str, Path]:
    """Export JianYing draft, OpenTimelineIO (.otio), and FCPXML (.fcpxml) together."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    otio_path = export_otio_timeline(
        plan,
        output_dir / f"{stem}.otio",
        timeline_name=stem,
        audio_tracks=audio_tracks,
        fps=fps,
    )
    fcpxml_path = export_fcpxml(
        plan,
        output_dir / f"{stem}.fcpxml",
        project_name=stem,
        audio_tracks=audio_tracks,
        resolution=resolution,
        fps=fps,
    )
    jianying_content = export_jianying_draft(
        plan,
        output_dir / f"{stem}_jianying_draft",
        draft_name=stem,
        audio_tracks=audio_tracks,
        resolution=resolution,
        fps=fps,
    )
    return {
        "otio": otio_path,
        "fcpxml": fcpxml_path,
        "jianying_draft": jianying_content,
    }
