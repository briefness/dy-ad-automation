from pathlib import Path
import base64
import copy
import json
import subprocess
from unittest.mock import patch

from PIL import Image, ImageDraw
import requests


def test_source_audio_is_automatically_transcribed_into_timestamped_speech(tmp_path):
    from source_audio_understanding import AudioUnderstandingConfig, analyze_source_audio

    video = tmp_path / "vlog.mp4"
    video.write_bytes(b"video-with-audio")

    def fake_ffmpeg(command, **_kwargs):
        Path(command[-1]).write_bytes(b"compressed-audio")
        return type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    response = type(
        "Response",
        (),
        {
            "raise_for_status": lambda self: None,
            "json": lambda self: {
                "text": "第一段介绍产品。第二段是背景音乐误识别。",
                "segments": [
                    {
                        "start": 0.4,
                        "end": 3.2,
                        "text": "第一段介绍产品。",
                        "no_speech_prob": 0.08,
                    },
                    {
                        "start": 3.2,
                        "end": 7.5,
                        "text": "第二段是背景音乐误识别。",
                        "no_speech_prob": 0.92,
                    },
                ],
            },
        },
    )()
    config = AudioUnderstandingConfig(
        base_url="https://speech.example/v1",
        api_key="test-key",
        model="test-asr",
        language="zh",
    )

    with patch("source_audio_understanding.subprocess.run", side_effect=fake_ffmpeg), patch(
        "source_audio_understanding.requests.post", return_value=response
    ) as post:
        result = analyze_source_audio(video, duration=8.0, has_audio=True, config=config)

    assert result["status"] == "transcribed"
    assert result["has_speech"] is True
    assert result["transcript"] == "第一段介绍产品。"
    assert result["segments"] == [
        {
            "start": 0.4,
            "end": 3.2,
            "text": "第一段介绍产品。",
            "confidence": 0.92,
        }
    ]
    assert post.call_args.args[0] == "https://speech.example/v1/audio/transcriptions"


def test_volcengine_turbo_transcribes_local_audio_as_base64(tmp_path):
    from source_audio_understanding import AudioUnderstandingConfig, analyze_source_audio

    video = tmp_path / "vlog.mp4"
    video.write_bytes(b"video-with-audio")

    def fake_ffmpeg(command, **_kwargs):
        Path(command[-1]).write_bytes(b"compressed-audio")
        return type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    response = type(
        "Response",
        (),
        {
            "headers": {
                "X-Api-Status-Code": "20000000",
                "X-Api-Message": "OK",
                "X-Tt-Logid": "test-log-id",
            },
            "raise_for_status": lambda self: None,
            "json": lambda self: {
                "result": {
                    "text": "今天出摊。客人很喜欢。",
                    "utterances": [
                        {"start_time": 300, "end_time": 1800, "text": "今天出摊。"},
                        {"start_time": 2100, "end_time": 3600, "text": "客人很喜欢。"},
                    ],
                }
            },
        },
    )()
    endpoint = "https://openspeech.bytedance.com/api/v3/auc/bigmodel/recognize/flash"
    config = AudioUnderstandingConfig(
        base_url=endpoint,
        api_key="speech-api-key",
        model="volc.bigasr.auc_turbo",
        provider="volcengine",
        language="zh-CN",
    )

    with patch("source_audio_understanding.subprocess.run", side_effect=fake_ffmpeg), patch(
        "source_audio_understanding.requests.post", return_value=response
    ) as post:
        result = analyze_source_audio(video, duration=4.0, has_audio=True, config=config)

    request = post.call_args
    assert request.args[0] == endpoint
    assert request.kwargs["headers"] == {
        "Content-Type": "application/json",
        "X-Api-Key": "speech-api-key",
        "X-Api-Resource-Id": "volc.bigasr.auc_turbo",
        "X-Api-Request-Id": request.kwargs["headers"]["X-Api-Request-Id"],
        "X-Api-Sequence": "-1",
    }
    assert base64.b64decode(request.kwargs["json"]["audio"]["data"]) == b"compressed-audio"
    assert request.kwargs["json"]["audio"]["format"] == "mp3"
    assert request.kwargs["json"]["request"]["show_utterances"] is True
    assert result["status"] == "transcribed"
    assert result["transcript"] == "今天出摊。客人很喜欢。"
    assert result["segments"] == [
        {"start": 0.3, "end": 1.8, "text": "今天出摊。", "confidence": 1.0},
        {"start": 2.1, "end": 3.6, "text": "客人很喜欢。", "confidence": 1.0},
    ]


def test_volcengine_turbo_silence_is_not_reported_as_failure(tmp_path):
    from source_audio_understanding import AudioUnderstandingConfig, analyze_source_audio

    video = tmp_path / "silent-vlog.mp4"
    video.write_bytes(b"video-with-audio")

    def fake_ffmpeg(command, **_kwargs):
        Path(command[-1]).write_bytes(b"compressed-audio")
        return type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    response = type(
        "Response",
        (),
        {
            "headers": {"X-Api-Status-Code": "20000003", "X-Api-Message": "silence"},
            "raise_for_status": lambda self: None,
            "json": lambda self: {},
        },
    )()
    config = AudioUnderstandingConfig(
        base_url="https://openspeech.bytedance.com/api/v3/auc/bigmodel/recognize/flash",
        api_key="speech-api-key",
        model="volc.bigasr.auc_turbo",
        provider="volcengine",
    )

    with patch("source_audio_understanding.subprocess.run", side_effect=fake_ffmpeg), patch(
        "source_audio_understanding.requests.post", return_value=response
    ):
        result = analyze_source_audio(video, duration=4.0, has_audio=True, config=config)

    assert result["status"] == "no_speech"
    assert result["has_speech"] is False
    assert result["error"] == ""


def test_volcengine_turbo_api_error_degrades_without_exposing_key(tmp_path):
    from source_audio_understanding import AudioUnderstandingConfig, analyze_source_audio

    video = tmp_path / "vlog.mp4"
    video.write_bytes(b"video-with-audio")

    def fake_ffmpeg(command, **_kwargs):
        Path(command[-1]).write_bytes(b"compressed-audio")
        return type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    response = type(
        "Response",
        (),
        {
            "headers": {
                "X-Api-Status-Code": "45000001",
                "X-Api-Message": "invalid request",
                "X-Tt-Logid": "test-log-id",
            },
            "raise_for_status": lambda self: None,
            "json": lambda self: {},
        },
    )()
    config = AudioUnderstandingConfig(
        base_url="https://openspeech.bytedance.com/api/v3/auc/bigmodel/recognize/flash",
        api_key="do-not-leak-this-key",
        model="volc.bigasr.auc_turbo",
        provider="volcengine",
    )

    with patch("source_audio_understanding.subprocess.run", side_effect=fake_ffmpeg), patch(
        "source_audio_understanding.requests.post", return_value=response
    ):
        result = analyze_source_audio(video, duration=4.0, has_audio=True, config=config)

    assert result["status"] == "failed"
    assert "45000001" in result["error"]
    assert "test-log-id" in result["error"]
    assert "do-not-leak-this-key" not in result["error"]


def test_video_without_audio_skips_audio_understanding(tmp_path):
    from source_audio_understanding import AudioUnderstandingConfig, analyze_source_audio

    video = tmp_path / "silent.mp4"
    video.write_bytes(b"silent-video")
    config = AudioUnderstandingConfig(
        base_url="https://speech.example/v1",
        api_key="test-key",
        model="test-asr",
    )

    with patch("source_audio_understanding.subprocess.run") as ffmpeg, patch(
        "source_audio_understanding.requests.post"
    ) as post:
        result = analyze_source_audio(video, duration=4.0, has_audio=False, config=config)

    assert result["status"] == "no_audio"
    assert result["has_speech"] is False
    ffmpeg.assert_not_called()
    post.assert_not_called()


def test_audio_context_keeps_distinct_speech_for_static_video_windows():
    from source_audio_understanding import audio_context_for_window

    profile = {
        "status": "transcribed",
        "has_speech": True,
        "segments": [
            {"start": 0.2, "end": 2.8, "text": "先说适合谁", "confidence": 0.93},
            {"start": 3.1, "end": 5.8, "text": "再说怎么使用", "confidence": 0.89},
        ],
    }

    first = audio_context_for_window(profile, 0.0, 3.0)
    second = audio_context_for_window(profile, 3.0, 6.0)

    assert first["has_speech"] is True
    assert first["transcript"] == "先说适合谁"
    assert second["has_speech"] is True
    assert second["transcript"] == "再说怎么使用"
    assert first["semantic_key"] != second["semantic_key"]


def test_local_asset_index_fuses_original_speech_into_static_visual_windows(tmp_path, monkeypatch):
    import config
    import local_asset_pipeline

    assets = tmp_path / "assets"
    assets.mkdir()
    image_path = tmp_path / "speaker.png"
    image = Image.new("RGB", (320, 180), "white")
    draw = ImageDraw.Draw(image)
    for x in range(0, 320, 20):
        draw.rectangle((x, 0, x + 9, 179), fill="black")
    image.save(image_path)
    video = assets / "static-vlog.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-loop",
            "1",
            "-i",
            str(image_path),
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=16000",
            "-t",
            "6",
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-shortest",
            str(video),
        ],
        check=True,
    )

    monkeypatch.setattr(local_asset_pipeline, "LOCAL_ASSET_INDEX_PATH", tmp_path / "index")
    monkeypatch.setattr(local_asset_pipeline, "VISION_ENABLED", True)
    monkeypatch.setattr(local_asset_pipeline, "VISION_BASE_URL", "https://vision.example/v1")
    monkeypatch.setattr(local_asset_pipeline, "VISION_API_KEY", "vision-key")
    monkeypatch.setattr(local_asset_pipeline, "VISION_MODEL", "vision-model")
    monkeypatch.setattr(
        config,
        "ASR_BASE_URL",
        "https://openspeech.bytedance.com/api/v3/auc/bigmodel/recognize/flash",
    )
    monkeypatch.setattr(config, "ASR_API_KEY", "speech-key")
    monkeypatch.setattr(config, "ASR_MODEL", "volc.bigasr.auc_turbo")
    monkeypatch.setattr(config, "ASR_PROVIDER", "volcengine")
    captured_metadata = []

    def fake_visual_analysis(_self, _sheet, metadata):
        captured_metadata.append(metadata)
        return {
            "shot_type": "static",
            "narrative_roles": ["product_showcase"],
            "action_phase": "none",
            "motion_level": "low",
            "visible_subjects": ["person"],
            "setting": "室内房间",
            "literal_actions": [],
            "temporal_events": [],
            "visible_objects": ["人物"],
            "object_tracks": [],
            "visible_text": [],
            "product_story_role": "context",
            "relation_candidates": [],
            "relation_confidence": 0.0,
            "relation_evidence": "",
            "product_visibility": 0,
            "camera_scale": "medium",
            "emotion": "calm",
            "usable_for_ad": True,
            "confidence": 0.9,
            "evidence": "人物面对镜头",
        }

    response = type(
        "Response",
        (),
        {
            "headers": {"X-Api-Status-Code": "20000000", "X-Api-Message": "OK"},
            "raise_for_status": lambda self: None,
            "json": lambda self: {
                "result": {
                    "text": "先说适合谁再说怎么使用",
                    "utterances": [
                        {"start_time": 200, "end_time": 1800, "text": "先说适合谁"},
                        {"start_time": 4200, "end_time": 5800, "text": "再说怎么使用"},
                    ],
                }
            },
        },
    )()

    with patch.object(
        local_asset_pipeline.VisionAnalyzer, "analyze_window", side_effect=fake_visual_analysis, autospec=True
    ), patch("source_audio_understanding.requests.post", return_value=response):
        index = local_asset_pipeline.build_local_asset_index(assets)

    assert [metadata["audio_context"]["transcript"] for metadata in captured_metadata] == [
        "先说适合谁",
        "再说怎么使用",
    ]
    assert [window["audio_context"]["semantic_key"] for window in index["windows"]] == [
        captured_metadata[0]["audio_context"]["semantic_key"],
        captured_metadata[1]["audio_context"]["semantic_key"],
    ]


def test_window_understanding_keeps_spoken_claims_separate_from_visual_facts(tmp_path):
    import local_asset_pipeline

    sheet = tmp_path / "sheet.jpg"
    Image.new("RGB", (32, 32), "white").save(sheet)
    analyzer = local_asset_pipeline.VisionAnalyzer()
    analyzer.base_url = "https://vision.example/v1"
    analyzer.api_key = "vision-key"
    analyzer.model = "vision-model"
    captured_payload = {}

    class Response:
        headers = {}

        def raise_for_status(self):
            return None

        def close(self):
            return None

    def fake_post(_url, **kwargs):
        captured_payload.update(kwargs["json"])
        return Response()

    raw = {
        "shot_type": "static",
        "narrative_roles": ["product_showcase"],
        "visible_subjects": ["person"],
        "visible_objects": ["人物"],
        "visible_text": [],
        "product_story_role": "context",
        "usable_for_ad": True,
        "confidence": 0.9,
        "evidence": "人物面对镜头",
        "spoken_summary": "人物声称饮用后可以治疗失眠",
        "spoken_intents": ["product_claim"],
        "spoken_claim_candidates": ["可以治疗失眠"],
        "speech_visual_relation": "complementary",
    }
    metadata = {
        "source_video": "vlog.mp4",
        "start": 0.0,
        "end": 4.0,
        "duration": 4.0,
        "frame_count": 4,
        "motion": {"motion_class": "static"},
        "audio_context": {
            "has_speech": True,
            "transcript": "喝了这个可以治疗失眠",
            "confidence": 0.94,
        },
    }

    with patch("local_asset_pipeline.requests.post", side_effect=fake_post), patch(
        "local_asset_pipeline._streamed_chat_json", return_value=raw
    ):
        result = analyzer.analyze_window(sheet, metadata)

    assert result["spoken_summary"] == "人物声称饮用后可以治疗失眠"
    assert result["spoken_claim_candidates"] == ["可以治疗失眠"]
    assert result["visible_objects"] == ["人物"]
    assert "可以治疗失眠" not in result["visible_objects"]
    user_text = captured_payload["messages"][1]["content"][0]["text"]
    assert "喝了这个可以治疗失眠" in user_text
    assert "口播不能证明画面事实或产品事实" in user_text
    assert "不要只按关键词字面匹配" in user_text
    assert "成片种植、栽培园区、农场、果园、花田或山地培育环境" in user_text
    assert "这只表示素材的商品叙事角色" in user_text
    assert "不得据此断言它就是当前产品的产地" in user_text
    assert "product_visibility 只统计可见的成品商品本体" in user_text


def test_story_contract_prefers_distinct_spoken_vlog_windows_over_silent_duplicate():
    from local_asset_pipeline import build_local_asset_story_contract

    windows = []
    for window_id, transcript in (
        ("z-silent", ""),
        ("a-audience", "这款更适合经常出差的人"),
        ("b-usage", "打开以后直接就可以使用"),
        ("c-experience", "我连续使用了一周"),
    ):
        windows.append({
            "window_id": window_id,
            "source_path": f"/{window_id}.mp4",
            "source_video": f"{window_id}.mp4",
            "start": 0.0,
            "end": 4.0,
            "analysis": {
                "usable_for_ad": True,
                "confidence": 0.9,
                "narrative_roles": ["product_showcase"],
                "visible_subjects": ["person"],
                "visible_objects": ["人物"],
                "product_story_role": "context",
                "product_visibility": 0,
                "evidence": "人物面对镜头",
            },
            "motion": {
                "motion_class": "static",
                "camera_speed": 0.0,
                "subject_motion_ratio": 0.0,
                "temporal_change": 0.0,
                "stability": 0.95,
            },
            "frame_quality": {"readable_ratio": 0.95},
            "audio_context": {
                "has_speech": bool(transcript),
                "transcript": transcript,
                "speech_ratio": 0.8 if transcript else 0.0,
                "confidence": 0.92 if transcript else 0.0,
                "semantic_key": window_id if transcript else "",
            },
        })

    contract = build_local_asset_story_contract(
        {"windows": windows},
        product_info={"name": "测试产品"},
    )

    assert set(contract["selected_window_ids"]) == {
        "a-audience",
        "b-usage",
        "c-experience",
    }


def test_audio_understanding_warning_names_missing_model_without_exposing_key(capsys):
    import local_asset_pipeline

    sources = [{
        "name": "vlog.mp4",
        "has_audio": True,
        "audio_understanding": {"status": "unavailable"},
    }]
    with patch.object(local_asset_pipeline, "ASR_BASE_URL", "https://asr.example.com/v1"), patch.object(
        local_asset_pipeline, "ASR_API_KEY", "secret-key"
    ), patch.object(local_asset_pipeline, "ASR_MODEL", ""):
        local_asset_pipeline._warn_source_audio_understanding_status(sources)

    output = capsys.readouterr().out
    assert "ASR_MODEL" in output
    assert "纯视觉" in output
    assert "secret-key" not in output


def test_large_single_role_vlog_pool_keeps_enough_distinct_spoken_windows():
    from local_asset_pipeline import build_local_asset_story_contract

    windows = []
    for index in range(30):
        transcript = f"第{index + 1}段不同的摆摊经历"
        windows.append({
            "window_id": f"vlog-{index}",
            "source_path": f"/tmp/vlog-{index}.mp4",
            "source_video": f"vlog-{index}.mp4",
            "start": 0.0,
            "end": 4.0,
            "analysis": {
                "usable_for_ad": True,
                "confidence": 0.9,
                "narrative_roles": ["personal_vlog"],
                "visible_subjects": ["person"],
                "visible_objects": ["人物"],
                "product_story_role": "context",
                "product_visibility": 0,
                "evidence": "人物面对镜头",
                "spoken_summary": transcript,
                "spoken_intents": ["experience"],
            },
            "motion": {"motion_class": "static", "stability": 0.95},
            "frame_quality": {"readable_ratio": 0.95},
            "audio_context": {
                "has_speech": True,
                "transcript": transcript,
                "speech_ratio": 0.8,
                "confidence": 0.92,
                "semantic_key": f"story-{index}",
            },
        })

    contract = build_local_asset_story_contract(
        {"windows": windows},
        product_info={"name": "测试产品"},
        requested_duration=15,
    )

    assert contract["recommended_segments"] == 4
    assert contract["natural_main_duration"] == 16.0
    assert len(set(contract["selected_window_ids"])) == 4


def test_transcription_retries_without_extracting_audio_twice(tmp_path):
    from source_audio_understanding import AudioUnderstandingConfig, analyze_source_audio

    video = tmp_path / "retry-vlog.mp4"
    video.write_bytes(b"video-with-audio")
    ffmpeg_calls = 0

    def fake_ffmpeg(command, **_kwargs):
        nonlocal ffmpeg_calls
        ffmpeg_calls += 1
        Path(command[-1]).write_bytes(b"compressed-audio")
        return type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    response = type(
        "Response",
        (),
        {
            "raise_for_status": lambda self: None,
            "json": lambda self: {
                "segments": [
                    {"start": 0.1, "end": 1.9, "text": "重试后识别成功", "no_speech_prob": 0.1}
                ]
            },
        },
    )()
    config = AudioUnderstandingConfig(
        base_url="https://speech.example/v1",
        api_key="test-key",
        model="test-asr",
        max_retries=1,
    )

    with patch("source_audio_understanding.subprocess.run", side_effect=fake_ffmpeg), patch(
        "source_audio_understanding.requests.post",
        side_effect=[requests.Timeout("temporary"), response],
    ) as post:
        result = analyze_source_audio(video, duration=2.0, has_audio=True, config=config)

    assert result["status"] == "transcribed"
    assert result["transcript"] == "重试后识别成功"
    assert post.call_count == 2
    assert ffmpeg_calls == 1


def test_audio_backend_signature_invalidates_only_semantic_backend_changes():
    from source_audio_understanding import AudioUnderstandingConfig, audio_understanding_signature

    first = AudioUnderstandingConfig("https://speech.example/v1", "key-a", "asr-v1")
    rotated_key = AudioUnderstandingConfig("https://speech.example/v1", "key-b", "asr-v1")
    upgraded_model = AudioUnderstandingConfig("https://speech.example/v1", "key-b", "asr-v2")
    volcengine = AudioUnderstandingConfig(
        "https://speech.example/v1",
        "key-b",
        "asr-v1",
        provider="volcengine",
    )

    assert audio_understanding_signature(first) == audio_understanding_signature(rotated_key)
    assert audio_understanding_signature(first) != audio_understanding_signature(upgraded_model)
    assert audio_understanding_signature(first) != audio_understanding_signature(volcengine)


def test_script_generation_receives_spoken_context_without_promoting_spoken_claims(tmp_path):
    from local_asset_pipeline import build_material_constrained_script

    window = {
        "window_id": "vlog-0",
        "source_video": "vlog.mp4",
        "source_path": "/tmp/vlog.mp4",
        "start": 0.0,
        "end": 4.0,
        "analysis": {
            "usable_for_ad": True,
            "confidence": 0.95,
            "product_story_role": "context",
            "product_visibility": 0,
            "visible_subjects": ["person"],
            "visible_objects": ["人物"],
            "narrative_roles": ["hook"],
            "evidence": "人物面对镜头",
            "spoken_summary": "分享一周使用体验",
            "spoken_intents": ["experience"],
            "spoken_claim_candidates": ["可以治疗失眠"],
            "speech_visual_relation": "complementary",
        },
        "audio_context": {
            "has_speech": True,
            "transcript": "我用了一周，它还可以治疗失眠",
            "speech_ratio": 0.85,
            "confidence": 0.93,
            "semantic_key": "experience",
        },
        "motion": {"motion_class": "static", "stability": 0.95},
        "frame_quality": {"passed": True, "readable_ratio": 0.95},
    }
    candidate = {
        "segments": [{
            "segment": 0,
            "marketing_intent": "cta",
            "cue": "想了解这款产品就继续看看。",
            "evidence_refs": ["product:name"],
            "claims": [],
            "desired_story_role": "context",
            "visual_query": ["人物分享"],
        }]
    }
    response = {
        "creative_candidates": [
            {"route": route, **copy.deepcopy(candidate)}
            for route in ("反差好奇", "场景共鸣", "证据递进")
        ]
    }

    with patch("config.LLM_ENABLED", True), patch(
        "llm_client.generate_json", return_value=response
    ) as generate, patch("local_asset_pipeline.LOCAL_ASSET_INDEX_PATH", tmp_path):
        build_material_constrained_script(
            product_info={"name": "测试产品", "type": "日用品"},
            coverage={},
            num_segments=1,
            script_style="testimonial",
            asset_index={"asset_folder": str(tmp_path / "assets"), "windows": [window]},
            segment_durations={0: 4.0},
        )

    prompt = json.loads(generate.call_args.args[0])
    capability = prompt["visual_capabilities"][0]
    assert capability["spoken_summaries"] == ["分享一周使用体验"]
    assert capability["spoken_intents"] == ["experience"]
    assert "可以治疗失眠" not in json.dumps(prompt["copy_evidence_anchors"], ensure_ascii=False)
