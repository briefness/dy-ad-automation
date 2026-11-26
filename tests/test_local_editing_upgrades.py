"""Unit and real-FFmpeg integration tests for all local-editing upgrades."""

from __future__ import annotations

import json
import subprocess
import threading
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

import cv2
import numpy as np
import pytest

from audio_alignment import (
    align_lines_acoustic_dp,
    build_word_timings,
    detect_dead_air_intervals,
)
from personal_vlog_audio import apply_j_l_cuts, place_source_dialogue
from plan_rerender import apply_plan_edits, rerender_production_plan
from production_plan import build_local_production_plan, build_product_entity_ledger
from render_backend import (
    build_color_grading_filters,
    detect_hardware_encoder,
    probe_video_info,
    render_single_pass_composite,
    video_encode_args,
)
from smart_reframe import build_smart_reframe_vf, smooth_crop_trajectory
from source_audio_understanding import AudioUnderstandingConfig
from timeline_export import (
    export_all_nle_timelines,
    export_fcpxml,
    export_jianying_draft,
    export_otio_timeline,
)
from visual_search import (
    compute_visual_descriptor_from_frames,
    detect_adaptive_scene_cuts,
    rank_similar_windows,
    visual_cosine_similarity,
)


def _make_synthetic_video(
    path: Path,
    *,
    width: int = 640,
    height: int = 360,
    fps: int = 20,
    duration: float = 2.0,
    color_bgr: tuple[int, int, int] = (40, 80, 140),
    moving_box: bool = False,
    scene_cut_at: float | None = None,
    with_audio: bool = False,
) -> Path:
    """Create a deterministic synthetic MP4 using OpenCV (+ optional FFmpeg audio mux)."""
    raw_path = path.with_suffix(".raw.mp4") if with_audio else path
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(raw_path), fourcc, float(fps), (width, height))
    total_frames = int(round(duration * fps))
    for i in range(total_frames):
        t = i / float(fps)
        bg = color_bgr if (scene_cut_at is None or t < scene_cut_at) else (220, 40, 40)
        frame = np.full((height, width, 3), bg, dtype=np.uint8)
        if moving_box:
            # High-contrast textured object panning across the horizontal frame
            cx = int(width * (0.20 + 0.60 * (i / max(total_frames - 1, 1))))
            cy = height // 2
            cv2.rectangle(frame, (cx - 45, cy - 60), (cx + 45, cy + 60), (250, 250, 250), -1)
            cv2.rectangle(frame, (cx - 25, cy - 35), (cx + 25, cy + 35), (15, 15, 15), 3)
            cv2.circle(frame, (cx, cy), 14, (0, 200, 255), -1)
        elif scene_cut_at is not None and t >= scene_cut_at:
            cv2.circle(frame, (width // 2, height // 2), min(width, height) // 4, (255, 255, 0), -1)
        else:
            cv2.rectangle(frame, (width // 4, height // 4), (width * 3 // 4, height * 3 // 4), (240, 240, 240), 4)
        writer.write(frame)
    writer.release()

    if with_audio:
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", str(raw_path),
                "-f", "lavfi", "-t", f"{duration:.3f}",
                "-i", "sine=frequency=440:sample_rate=44100",
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "128k", "-shortest",
                str(path),
            ],
            check=True,
            capture_output=True,
        )
        raw_path.unlink(missing_ok=True)
    return path


def _make_segmented_speech_wav(path: Path) -> Path:
    """Create a 3.2s WAV with 3 loud bursts separated by quiet valleys around 1.0s and 2.1s."""
    import wave

    sr = 8000
    dur = 3.2
    t = np.arange(int(sr * dur), dtype=np.float32) / sr
    signal = np.zeros_like(t)
    for s, e, freq in ((0.12, 0.90, 320.0), (1.15, 1.95, 420.0), (2.25, 3.05, 360.0)):
        mask = (t >= s) & (t <= e)
        signal[mask] = 0.65 * np.sin(2.0 * np.pi * freq * t[mask])
    pcm = np.clip(signal * 32767.0, -32768, 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())
    return path


def test_hardware_encoder_detection_and_env_override(monkeypatch):
    monkeypatch.setenv("VIDEO_HW_ACCEL", "off")
    assert detect_hardware_encoder(force_refresh=True) is None
    sw_args = video_encode_args(profile="intermediate", allow_hw=True)
    assert "libx264" in sw_args
    assert "veryfast" in sw_args

    monkeypatch.setenv("VIDEO_HW_ACCEL", "auto")
    enc = detect_hardware_encoder(force_refresh=True)
    # On macOS VideoToolbox is detected; on headless CI it may be None — both valid
    assert enc is None or enc.startswith("h264_")


def test_single_pass_composite_renders_grading_subtitle_and_sticker(tmp_path):
    src = _make_synthetic_video(tmp_path / "src.mp4", width=360, height=640, duration=1.5, with_audio=True)
    sticker_png = tmp_path / "sticker.png"
    img = np.zeros((60, 140, 4), dtype=np.uint8)
    img[:, :, 1] = 220
    img[:, :, 3] = 230
    cv2.imwrite(str(sticker_png), img)

    out = tmp_path / "composite.mp4"
    grading = build_color_grading_filters("warm_cinematic")
    assert len(grading) >= 1

    render_single_pass_composite(
        src,
        out,
        resolution="360x640",
        fps=20,
        bitrate="2M",
        grading_filters=grading,
        sticker_overlays=[{"path": str(sticker_png), "x": 20, "y": 40, "start": 0.1, "end": 1.2}],
        normalize_audio=True,
        profile="final",
    )
    info = probe_video_info(out)
    assert out.is_file() and out.stat().st_size > 2000
    assert info["width"] == 360 and info["height"] == 640
    assert info["has_audio"] is True
    assert info["duration"] == pytest.approx(1.5, abs=0.25)


def test_smart_reframe_crops_horizontal_to_vertical_without_black_bars(tmp_path):
    horiz = _make_synthetic_video(
        tmp_path / "horiz.mp4",
        width=640,
        height=360,
        duration=2.0,
        moving_box=True,
    )
    info = build_smart_reframe_vf(horiz, 0.0, 2.0, 360, 640, mode="auto", sample_count=6)
    assert info["mode"] == "smart_reframe"
    assert "crop=" in info["vf"]
    assert "pad=" not in info["vf"]
    assert info["crop_w"] < 640 and info["crop_h"] == 360

    out = tmp_path / "vertical.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(horiz),
            "-vf", info["vf"],
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            str(out),
        ],
        check=True,
        capture_output=True,
    )
    probed = probe_video_info(out)
    assert probed["width"] == 360 and probed["height"] == 640

    # Pad mode still preserves legacy black-bar behavior when requested
    pad_info = build_smart_reframe_vf(horiz, 0.0, 2.0, 360, 640, mode="pad")
    assert "pad=" in pad_info["vf"]


def test_smooth_crop_trajectory_tracks_pan_smoothly():
    centers = [(0.0, 0.2, 0.5), (0.5, 0.5, 0.5), (1.0, 0.8, 0.5)]
    traj = smooth_crop_trajectory(centers, 1920, 1080, 1080, 1920)
    assert traj["crop_w"] == 608
    assert traj["crop_h"] == 1080
    assert traj["is_dynamic"] is True
    xs = [x for _, x, _ in traj["keyframes"]]
    assert xs[0] < xs[-1]


def test_nle_timeline_exports_jianying_otio_and_fcpxml(tmp_path):
    clip_a = tmp_path / "a.mp4"
    clip_b = tmp_path / "b.mp4"
    clip_a.write_bytes(b"video_a")
    clip_b.write_bytes(b"video_b")
    vo = tmp_path / "vo.m4a"
    vo.write_bytes(b"audio_vo")

    ledger = build_product_entity_ledger({"ingredients": ["茉莉花"], "origin": "横州"})
    plan = build_local_production_plan(
        ad_script={"segments": [
            {"segment": 0, "narrative": "hook", "subtitle": "先看横州茉莉", "voiceover": "先看横州茉莉"},
            {"segment": 1, "narrative": "proof", "subtitle": "真实花茶原料", "voiceover": "真实花茶原料"},
        ]},
        selected_segments=[
            {"semantic_segment": 0, "edit_index": 0, "source_video": str(clip_a), "source_start": 0.5, "source_end": 3.0, "target_duration": 2.5},
            {"semantic_segment": 1, "edit_index": 1, "source_video": str(clip_b), "source_start": 1.0, "source_end": 4.0, "target_duration": 3.0},
        ],
        postproduction_contract={"segments": []},
        entity_ledger=ledger,
        subtitles=[
            {"segment": 0, "text": "先看横州茉莉", "start": 0.0, "end": 2.5},
            {"segment": 1, "text": "真实花茶原料", "start": 2.5, "end": 5.5},
        ],
        sticker_plan={"items": [{"segment": 0, "kind": "origin", "text": "横州产地", "start": 0.4, "end": 2.0, "evidence_refs": ["product:origin:0"]}]},
    )

    exports = export_all_nle_timelines(
        plan,
        tmp_path,
        "demo",
        audio_tracks=[{"name": "vo", "role": "voiceover", "path": str(vo), "start": 0.0, "duration": 5.5}],
    )

    # 1. JianYing Draft validation
    jy = json.loads(exports["jianying_draft"].read_text(encoding="utf-8"))
    assert jy["duration"] == 5_500_000
    assert len(jy["materials"]["videos"]) == 2
    assert len(jy["materials"]["audios"]) == 1
    assert len(jy["materials"]["texts"]) == 3  # 2 subtitles + 1 sticker
    assert (exports["jianying_draft"].parent / "draft_meta_info.json").is_file()

    # 2. OpenTimelineIO validation
    otio = json.loads(exports["otio"].read_text(encoding="utf-8"))
    assert otio["OTIO_SCHEMA"] == "Timeline.1"
    tracks = otio["tracks"]["children"]
    assert tracks[0]["kind"] == "Video" and len(tracks[0]["children"]) == 2
    assert len(tracks[0]["markers"]) == 3
    assert tracks[1]["kind"] == "Audio"

    # 3. FCPXML validation
    tree = ET.parse(exports["fcpxml"])
    root = tree.getroot()
    assert root.tag == "fcpxml" and root.attrib["version"] == "1.10"
    assert len(root.findall(".//spine/asset-clip")) == 3  # 2 video + 1 audio lane


def test_adaptive_scene_cuts_and_visual_descriptors(tmp_path):
    cut_video = _make_synthetic_video(
        tmp_path / "two_scenes.mp4",
        width=320,
        height=180,
        fps=15,
        duration=4.2,
        scene_cut_at=2.0,
    )
    cuts = detect_adaptive_scene_cuts(cut_video, 4.2, min_scene_seconds=1.2)
    assert len(cuts) == 1
    assert cuts[0] == pytest.approx(2.0, abs=0.45)

    frame_a = np.full((72, 128, 3), (30, 180, 40), dtype=np.uint8)
    frame_b = np.full((72, 128, 3), (200, 40, 20), dtype=np.uint8)
    vec_a = compute_visual_descriptor_from_frames([frame_a])
    vec_a2 = compute_visual_descriptor_from_frames([frame_a])
    vec_b = compute_visual_descriptor_from_frames([frame_b])
    assert len(vec_a) == 48
    assert visual_cosine_similarity(vec_a, vec_a2) > 0.99
    assert visual_cosine_similarity(vec_a, vec_b) < 0.7

    ranked = rank_similar_windows(
        {"selected_role": "proof", "source_video": "a.mp4", "source_start": 0, "source_end": 2, "visual_descriptor": vec_a},
        [
            {"window_id": "w1", "source_video": "b.mp4", "start": 0, "end": 3, "analysis": {"usable_for_ad": True, "product_story_role": "proof", "confidence": 0.9}, "visual_descriptor": vec_a2},
            {"window_id": "w2", "source_video": "c.mp4", "start": 0, "end": 3, "analysis": {"usable_for_ad": True, "product_story_role": "hook", "confidence": 0.8}, "visual_descriptor": vec_b},
        ],
    )
    assert ranked[0]["window_id"] == "w1"


def test_acoustic_dp_alignment_dead_air_and_karaoke_ass(tmp_path):
    wav = _make_segmented_speech_wav(tmp_path / "speech.wav")
    lines = [
        {"text": "第一句口播测试", "start": 0.0, "end": 1.1, "segment": 0},
        {"text": "第二句口播测试", "start": 1.1, "end": 2.2, "segment": 1},
        {"text": "第三句口播测试", "start": 2.2, "end": 3.2, "segment": 2},
    ]
    aligned = align_lines_acoustic_dp(lines, wav, 3.2, enable_whisper=False)
    assert aligned is not None and len(aligned) == 3
    assert aligned[0]["end"] == pytest.approx(1.02, abs=0.22)
    assert aligned[1]["end"] == pytest.approx(2.10, abs=0.22)
    assert all(item["word_timings"] for item in aligned)

    # Dead-air detector finds silent gap when longer than min_silence_seconds
    cuts = detect_dead_air_intervals(wav, min_silence_seconds=0.20, keep_padding_seconds=0.02)
    assert len(cuts) >= 1

    # Local ASR config is marked available without remote API keys
    local_cfg = AudioUnderstandingConfig(base_url="", api_key="", model="small", provider="local")
    assert local_cfg.available is True

    # Karaoke ASS subtitle rendering works end-to-end
    from video_merger import add_fancy_subtitles

    src = _make_synthetic_video(tmp_path / "ksrc.mp4", width=360, height=640, duration=1.5)
    kout = tmp_path / "kout.mp4"
    add_fancy_subtitles(
        src,
        [{
            "text": "横州茉莉花茶",
            "start": 0.1,
            "end": 1.3,
            "animation": "karaoke",
            "word_timings": build_word_timings("横州茉莉花茶", 0.1, 1.3),
        }],
        kout,
        font_size=40,
    )
    assert kout.is_file() and kout.stat().st_size > 2000


def test_j_l_cut_audio_overlap_and_speed_ramp(tmp_path):
    dialogue = [
        {"edit_index": 0, "semantic_segment": 0, "source_start": 1.0, "source_end": 2.2, "timeline_start": 0.5, "timeline_end": 1.7, "duration": 1.2, "score": 0.9, "text": "第一句"},
        {"edit_index": 1, "semantic_segment": 1, "source_start": 2.0, "source_end": 3.2, "timeline_start": 3.0, "timeline_end": 4.2, "duration": 1.2, "score": 0.9, "text": "第二句"},
    ]
    shifted = apply_j_l_cuts(dialogue, total_duration=6.0, j_cut_lead=0.25, l_cut_trail=0.20)
    # Shot 0 gets L-cut trail; Shot 1 gets J-cut lead and L-cut trail without collision
    assert shifted[0]["timeline_end"] == pytest.approx(1.9, abs=0.01)
    assert shifted[1]["timeline_start"] == pytest.approx(2.75, abs=0.01)
    assert shifted[1]["source_start"] == pytest.approx(1.75, abs=0.01)
    assert shifted[1]["timeline_start"] > shifted[0]["timeline_end"]

    # Speed ramp (ease-in/out speedup) + motion-interpolated slow-mo
    from video_merger import adjust_clip_speed_ramp

    clip = _make_synthetic_video(tmp_path / "ramp_src.mp4", width=360, height=640, duration=3.0)
    fast_out = adjust_clip_speed_ramp(clip, 2.0, tmp_path / "fast.mp4", mode="ease_in_out")
    slow_out = adjust_clip_speed_ramp(clip, 4.0, tmp_path / "slow.mp4", interpolate_slowmo=True)
    assert probe_video_info(fast_out)["duration"] == pytest.approx(2.0, abs=0.25)
    assert probe_video_info(slow_out)["duration"] == pytest.approx(4.0, abs=0.25)


def test_plan_rerender_and_web_api_endpoints(tmp_path, monkeypatch):
    src_a = _make_synthetic_video(tmp_path / "clip_a.mp4", width=640, height=360, duration=2.0, moving_box=True)
    src_b = _make_synthetic_video(tmp_path / "clip_b.mp4", width=640, height=360, duration=2.0, color_bgr=(60, 150, 80))

    final_dir = tmp_path / "final"
    final_dir.mkdir()
    ledger = build_product_entity_ledger({"origin": "横州"})
    plan = build_local_production_plan(
        ad_script={"segments": [
            {"segment": 0, "narrative": "hook", "subtitle": "原始字幕一", "voiceover": "原始字幕一"},
            {"segment": 1, "narrative": "proof", "subtitle": "原始字幕二", "voiceover": "原始字幕二"},
        ]},
        selected_segments=[
            {"semantic_segment": 0, "edit_index": 0, "source_video": str(src_a), "source_start": 0.0, "source_end": 1.5, "target_duration": 1.5},
            {"semantic_segment": 1, "edit_index": 1, "source_video": str(src_b), "source_start": 0.0, "source_end": 1.5, "target_duration": 1.5},
        ],
        postproduction_contract={"segments": []},
        entity_ledger=ledger,
        subtitles=[
            {"segment": 0, "text": "原始字幕一", "start": 0.0, "end": 1.4},
            {"segment": 1, "text": "原始字幕二", "start": 1.4, "end": 2.8},
        ],
        sticker_plan={"items": []},
    )
    plan_path = final_dir / "sample_production_plan.json"
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")

    # Apply edits and rerender directly without LLM/VLM
    result = rerender_production_plan(
        plan_path,
        edits={"segments": [{"edit_index": 0, "script_subtitle": "微调后的字幕一", "source_start": 0.2, "source_end": 1.4}]},
        resolution="360x640",
        fps=20,
        bitrate="2M",
        reframe_mode="auto",
        export_nle=True,
    )
    assert Path(result["final_path"]).is_file()
    assert Path(result["nle_exports"]["otio"]).is_file()
    assert Path(result["nle_exports"]["fcpxml"]).is_file()
    assert Path(result["nle_exports"]["jianying_draft"]).is_file()
    edited_plan = json.loads(Path(result["production_plan"]).read_text(encoding="utf-8"))
    assert edited_plan["segments"][0]["script_subtitle"] == "微调后的字幕一"

    # Verify new Web API endpoints (/api/plan and /api/plan/export)
    import local_web_app.server as server_module

    monkeypatch.setattr(server_module, "output_dir", lambda: tmp_path)
    monkeypatch.setattr(server_module, "cache_roots", lambda: [tmp_path / "no_index"])
    server = server_module.create_server(port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        plan_resp = json.loads(urllib.request.urlopen(base + "/api/plan?path=final/sample_production_plan.json").read())
        assert len(plan_resp["plan"]["segments"]) == 2

        req = urllib.request.Request(
            base + "/api/plan/export",
            data=json.dumps({"plan_path": "final/sample_production_plan.json"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        exp_resp = json.loads(urllib.request.urlopen(req).read())
        assert exp_resp["ok"] is True
        assert {"otio", "fcpxml", "jianying_draft"}.issubset(
            {item["kind"] for item in exp_resp["exports"]}
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
