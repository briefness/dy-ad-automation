"""Goal-aligned tests for evidence-driven local editing upgrades."""

from __future__ import annotations

import json
import subprocess
import threading
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
import pytest

from audio_alignment import align_lines_acoustic_dp
from blocking_diagnostics import diagnose_blocking_error
from local_asset_pipeline import (
    LocalAssetError,
    VisionAnalyzer,
    _extract_sharpest_detail_frame,
)
from material_copy_optimizer import semantic_overlap
from render_backend import (
    detect_hardware_encoder,
    probe_video_info,
    render_single_pass_composite,
    video_encode_args,
)
from smart_reframe import build_smart_reframe_vf
from source_audio_understanding import AudioUnderstandingConfig
from visual_search import (
    compute_visual_descriptor_from_frames,
    detect_adaptive_scene_cuts,
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
    narrow_subject: bool = False,
    wide_packaging: bool = False,
    scene_cut_at: float | None = None,
    with_audio: bool = False,
) -> Path:
    raw_path = path.with_suffix(".raw.mp4") if with_audio else path
    writer = cv2.VideoWriter(str(raw_path), cv2.VideoWriter_fourcc(*"mp4v"), float(fps), (width, height))
    total_frames = int(round(duration * fps))
    for i in range(total_frames):
        t = i / float(fps)
        bg = color_bgr if (scene_cut_at is None or t < scene_cut_at) else (220, 40, 40)
        frame = np.full((height, width, 3), bg, dtype=np.uint8)
        if narrow_subject:
            cx = int(width * (0.25 + 0.50 * (i / max(total_frames - 1, 1))))
            cy = height // 2
            cv2.rectangle(frame, (cx - 40, cy - 55), (cx + 40, cy + 55), (250, 250, 250), -1)
            cv2.rectangle(frame, (cx - 20, cy - 30), (cx + 20, cy + 30), (15, 15, 15), 3)
        elif wide_packaging:
            # High-contrast packaging/text blocks spanning 85% of the horizontal width
            for col in range(6):
                x0 = int(width * (0.06 + col * 0.145))
                cv2.rectangle(frame, (x0, 70), (x0 + 68, height - 70), (245, 245, 245), -1)
                cv2.rectangle(frame, (x0 + 10, 95), (x0 + 58, height - 95), (20, 20, 20), 3)
                cv2.line(frame, (x0 + 14, height // 2), (x0 + 54, height // 2), (0, 80, 220), 3)
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


# ------------------------------------------------------------------
# 1. High-Res Packaging / Label Detail Frame + VLM Dual-Image Payload
# ------------------------------------------------------------------

def test_sharpest_detail_frame_and_vlm_dual_image_payload(tmp_path):
    video = _make_synthetic_video(tmp_path / "pack.mp4", width=1280, height=720, duration=1.5, wide_packaging=True)
    quality = {
        "passed": True,
        "samples": [
            {"time": 0.2, "sharpness": 45.0, "readable": True},
            {"time": 0.8, "sharpness": 240.0, "readable": True},
            {"time": 1.2, "sharpness": 12.0, "readable": False},
        ],
    }
    detail = _extract_sharpest_detail_frame(video, quality, tmp_path / "detail.jpg")
    assert detail is not None and detail.is_file()
    img = cv2.imread(str(detail))
    assert img is not None and img.shape[1] == 1280

    # Verify VisionAnalyzer attaches both contact sheet and detail frame in one request
    sheet = tmp_path / "sheet.jpg"
    cv2.imwrite(str(sheet), np.full((240, 360, 3), 120, dtype=np.uint8))

    captured_payload = {}

    def fake_post(url, headers=None, json=None, timeout=None, stream=False):
        captured_payload.update(json or {})

        class _Resp:
            status_code = 200
            def raise_for_status(self): pass
            def iter_lines(self, decode_unicode=True):
                body = {
                    "shot_type": "static",
                    "narrative_roles": ["product_showcase"],
                    "product_story_role": "finished_product",
                    "usable_for_ad": True,
                    "confidence": 0.9,
                }
                yield "data: " + __import__("json").dumps({"choices": [{"delta": {"content": __import__("json").dumps(body)}}]})
                yield "data: [DONE]"
            def close(self): pass
        return _Resp()

    analyzer = VisionAnalyzer()
    analyzer.base_url = "https://example.invalid/v1"
    analyzer.api_key = "test-key"
    analyzer.model = "test-vlm"
    with patch("local_asset_pipeline.requests.post", side_effect=fake_post):
        res = analyzer.analyze_window(sheet, {"source_video": "pack.mp4", "start": 0, "end": 1.5, "duration": 1.5, "frame_count": 4}, detail_frame_path=detail)
    assert res["product_story_role"] == "finished_product"
    user_content = captured_payload["messages"][1]["content"]
    image_items = [part for part in user_content if part.get("type") == "image_url"]
    assert len(image_items) == 2


# ------------------------------------------------------------------
# 2. Shot-Aware Static-Window VLM Deduplication + Adaptive Scene Cuts
# ------------------------------------------------------------------

def test_adaptive_scene_cuts_and_static_shot_vlm_deduplication(tmp_path, monkeypatch):
    import local_asset_pipeline as lap

    cut_video = _make_synthetic_video(tmp_path / "cuts.mp4", width=320, height=180, fps=15, duration=4.2, scene_cut_at=2.0)
    cuts = detect_adaptive_scene_cuts(cut_video, 4.2, min_scene_seconds=1.2)
    assert len(cuts) == 1
    assert cuts[0] == pytest.approx(2.0, abs=0.45)

    # Static 6.5s video sliced into 2 overlapping windows inside 1 scene -> only 1 VLM call!
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    _make_synthetic_video(assets_dir / "static_product.mp4", width=320, height=180, fps=15, duration=6.5)

    vlm_calls = []

    def fake_analyze_window(self, sheet_path, metadata, detail_frame_path=None):
        vlm_calls.append(metadata["start"])
        return {
            "shot_type": "static",
            "narrative_roles": ["product_showcase"],
            "action_phase": "none",
            "motion_level": "low",
            "visible_subjects": ["product"],
            "primary_visuals": ["产品展示"],
            "setting": "桌面",
            "literal_actions": [],
            "temporal_events": [],
            "visible_objects": ["产品"],
            "visual_entity_candidates": [],
            "object_tracks": [],
            "visible_text": [],
            "product_story_role": "finished_product",
            "relation_candidates": [],
            "relation_confidence": 0.8,
            "relation_evidence": "清晰产品",
            "product_visibility": 4,
            "camera_scale": "closeup",
            "emotion": "calm",
            "spoken_summary": "",
            "spoken_intents": [],
            "spoken_claim_candidates": [],
            "speech_visual_relation": "unknown",
            "usable_for_ad": True,
            "confidence": 0.9,
            "evidence": "桌面静态产品特写",
        }

    cache_root = tmp_path / "index_cache"
    monkeypatch.setattr(lap, "LOCAL_ASSET_INDEX_PATH", cache_root)
    monkeypatch.setattr(lap, "ensure_vision_backend_available", lambda: None)
    monkeypatch.setattr(lap.VisionAnalyzer, "analyze_window", fake_analyze_window)
    monkeypatch.setattr(lap, "_detect_scene_ranges", lambda src, dur: [{"scene_id": 0, "start": 0.0, "end": 6.4}])

    index = lap.build_local_asset_index(assets_dir)
    assert len(index["windows"]) >= 2
    assert len(vlm_calls) == 1
    assert any(win.get("vlm_reused_from") for win in index["windows"][1:])


# ------------------------------------------------------------------
# 3. Concept-Equivalence Semantic Overlap (Sales Copy <-> Visual Evidence)
# ------------------------------------------------------------------

def test_semantic_overlap_bridges_sales_copy_to_visual_evidence():
    # Sales copy vs literal visual observation with minimal char overlap
    score = semantic_overlap("早八出门随手带一包放包里", "手持独立小袋装进帆布包")
    assert score >= 0.25

    # Unrelated sentences stay at 0.0 (no false positives)
    assert semantic_overlap("清晨高山云雾缭绕", "电脑键盘敲击代码") == 0.0


# ------------------------------------------------------------------
# 4. Evidence-Safe Smart Reframe (Crop vs Wide Packaging Blur-Fit)
# ------------------------------------------------------------------

def test_evidence_safe_smart_reframe_crops_narrow_and_protects_wide_packaging(tmp_path):
    narrow = _make_synthetic_video(tmp_path / "narrow.mp4", width=640, height=360, duration=1.5, narrow_subject=True)
    narrow_plan = build_smart_reframe_vf(narrow, 0.0, 1.5, 360, 640, mode="auto", sample_count=5)
    assert narrow_plan["mode"] == "smart_reframe"
    assert "crop=" in narrow_plan["vf"]

    wide = _make_synthetic_video(tmp_path / "wide_pack.mp4", width=640, height=360, duration=1.5, wide_packaging=True)
    wide_plan = build_smart_reframe_vf(wide, 0.0, 1.5, 360, 640, mode="auto", sample_count=5)
    assert wide_plan["mode"] == "blur_fit"
    assert wide_plan["evidence_safe"] is True
    assert "boxblur" in wide_plan["vf"]

    # Both filter expressions execute cleanly in FFmpeg to exact 360x640
    out_wide = tmp_path / "wide_out.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(wide),
            "-vf", wide_plan["vf"],
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            str(out_wide),
        ],
        check=True,
        capture_output=True,
    )
    info = probe_video_info(out_wide)
    assert (info["width"], info["height"]) == (360, 640)


# ------------------------------------------------------------------
# 5. Real User Feedback Loop in Web Workbench (2-Video Active Promotion)
# ------------------------------------------------------------------

def test_web_feedback_api_promotes_rules_only_across_two_distinct_videos(tmp_path, monkeypatch):
    import local_web_app.server as server_module

    out_dir = tmp_path / "output"
    final_dir = out_dir / "final"
    final_dir.mkdir(parents=True)
    v1 = final_dir / "tea_001_final.mp4"
    v2 = final_dir / "tea_002_final.mp4"
    v1.write_bytes(b"video1")
    v2.write_bytes(b"video2")
    (final_dir / "tea_001_script.json").write_text(json.dumps({"segments": [{"subtitle": "句一"}]}), encoding="utf-8")
    (final_dir / "tea_002_script.json").write_text(json.dumps({"segments": [{"subtitle": "句二"}]}), encoding="utf-8")
    (final_dir / "tea_001_sticker_plan.json").write_text(
        json.dumps({"items": [{"id": "st-1", "kind": "origin", "text": "横州产地"}]}), encoding="utf-8"
    )

    monkeypatch.setattr(server_module, "output_dir", lambda: out_dir.resolve())
    monkeypatch.setattr(server_module, "script_feedback_db", lambda: tmp_path / "script_fb.db")
    monkeypatch.setattr(server_module, "sticker_feedback_db", lambda: tmp_path / "sticker_fb.db")

    server = server_module.create_server(port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        # Cold start must be empty
        cold = json.loads(urllib.request.urlopen(base + "/api/feedback/policy").read())
        assert cold["source"] == "explicit_user_feedback_only"
        assert cold["script_rules"] == []
        assert cold["sticker_rules"] == []

        def post_fb(payload: dict) -> dict:
            req = urllib.request.Request(
                base + "/api/feedback",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            return json.loads(urllib.request.urlopen(req).read())

        # 1st video -> provisional
        r1 = post_fb({
            "target": "script",
            "verdict": "violated",
            "rule_text": "字幕应该像带货文案而不是画面描述",
            "comment": "第一版偏解说",
            "video_path": "final/tea_001_final.mp4",
        })
        assert r1["recorded"] is True
        assert r1["policy"]["script_rules"][0]["status"] == "provisional"
        assert r1["policy"]["script_rules"][0]["distinct_video_count"] == 1

        # 2nd distinct video -> active!
        r2 = post_fb({
            "target": "script",
            "verdict": "satisfied",
            "rule_text": "字幕应该像带货文案而不是画面描述",
            "comment": "第二版改好了",
            "video_path": "final/tea_002_final.mp4",
        })
        assert r2["recorded"] is True
        assert r2["policy"]["script_rules"][0]["status"] == "active"
        assert r2["policy"]["script_rules"][0]["distinct_video_count"] == 2

        # Sticker feedback also persists cleanly
        rs = post_fb({
            "target": "sticker",
            "verdict": "violated",
            "rule_text": "产地贴图不要遮挡主体",
            "video_path": "final/tea_001_final.mp4",
            "sticker_id": "st-1",
        })
        assert rs["recorded"] is True
        assert rs["policy"]["sticker_rules"][0]["text"] == "产地贴图不要遮挡主体"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


# ------------------------------------------------------------------
# 6. Structured Blocking Diagnostics
# ------------------------------------------------------------------

def test_blocking_diagnostics_classifies_evidence_and_capacity_errors(tmp_path):
    err_fact = LocalAssetError(
        "具体产品主张缺少相关的非名称 Evidence Anchor",
        details={"unsupported_claims": ["有机认证", "海拔1200米"]},
    )
    diag_fact = diagnose_blocking_error(err_fact)
    assert diag_fact["category"] == "missing_fact_evidence"
    assert "有机认证" in diag_fact["summary"]
    assert any(act["field"] == "verified_claims" for act in diag_fact["actions"])

    gap_file = tmp_path / "gap.json"
    gap_file.write_text(
        json.dumps({"reason": "insufficient_material_coverage", "semantic_segment": 2, "missing_duration": 3.4}),
        encoding="utf-8",
    )
    err_cap = RuntimeError(f"本地素材无法完整覆盖脚本段 2，报告已保存：{gap_file}")
    diag_cap = diagnose_blocking_error(err_cap)
    assert diag_cap["category"] == "insufficient_material_capacity"
    assert diag_cap["missing_duration"] == pytest.approx(3.4)
    assert any(act["field"] == "target_duration" for act in diag_cap["actions"])


# ------------------------------------------------------------------
# 7. Hardware Acceleration, Single-Pass Export & Acoustic DP Alignment
# ------------------------------------------------------------------

def test_single_pass_composite_with_logo_and_acoustic_alignment(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_HW_ACCEL", "off")
    assert detect_hardware_encoder(force_refresh=True) is None
    assert "veryfast" in video_encode_args(profile="intermediate")

    src = _make_synthetic_video(tmp_path / "src.mp4", width=360, height=640, duration=1.5, with_audio=True)
    logo = tmp_path / "logo.png"
    logo_img = np.zeros((40, 80, 4), dtype=np.uint8)
    logo_img[:, :, 2] = 240
    logo_img[:, :, 3] = 220
    cv2.imwrite(str(logo), logo_img)

    out = tmp_path / "final.mp4"
    render_single_pass_composite(
        src,
        out,
        resolution="360x640",
        fps=20,
        bitrate="2M",
        logo_config={"logo_path": logo, "position": "top_right"},
        normalize_audio=True,
        profile="final",
    )
    info = probe_video_info(out)
    assert (info["width"], info["height"]) == (360, 640)
    assert info["has_audio"] is True

    # Acoustic DP alignment snaps continuous TTS cues to real valleys
    wav = _make_segmented_speech_wav(tmp_path / "vo.wav")
    lines = [
        {"text": "第一句带货钩子", "start": 0.0, "end": 1.1, "segment": 0},
        {"text": "第二句证据理由", "start": 1.1, "end": 2.2, "segment": 1},
        {"text": "第三句行动号召", "start": 2.2, "end": 3.2, "segment": 2},
    ]
    aligned = align_lines_acoustic_dp(lines, wav, 3.2, enable_whisper=False)
    assert aligned is not None and len(aligned) == 3
    assert aligned[0]["end"] == pytest.approx(1.02, abs=0.22)
    assert aligned[1]["end"] == pytest.approx(2.10, abs=0.22)
    assert AudioUnderstandingConfig(base_url="", api_key="", model="small", provider="local").available is True
