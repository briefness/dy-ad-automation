import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from personal_vlog_audio import (
    collect_source_dialogue_candidates,
    dialogue_subtitles,
    ensure_planned_narration_survived,
    is_personal_vlog_style,
    mix_speech_tracks,
    narration_lines_without_dialogue,
    place_narration_in_dialogue_gaps,
    place_source_dialogue,
    render_source_dialogue_track,
)


def _selected(**overrides):
    selected = {
        "edit_index": 0,
        "semantic_segment": 0,
        "narrative": "hook",
        "source_path": "/tmp/vlog.mp4",
        "source_start": 0.0,
        "source_end": 4.0,
        "analysis": {
            "speech_visual_relation": "aligned",
            "spoken_intents": ["conversation"],
        },
    }
    selected.update(overrides)
    return selected


def _index(segments, path="/tmp/vlog.mp4"):
    return {
        "sources": [{
            "path": path,
            "audio_understanding": {
                "has_speech": True,
                "segments": segments,
            },
        }],
    }


def test_only_personal_vlog_enables_hybrid_style():
    assert is_personal_vlog_style({"video_style": "个人 Vlog"}) is True
    assert is_personal_vlog_style({"video_style_tone": "personal_vlog"}) is True
    assert is_personal_vlog_style({"video_style": "产品展示"}) is False
    assert is_personal_vlog_style({"video_style": "测评"}) is False


def test_local_personal_vlog_enables_sparse_narration_without_changing_other_styles(tmp_path):
    from one_click_create import run_one_click_create

    final_path = tmp_path / "preview.mp4"
    final_path.write_bytes(b"preview")

    def args(video_style):
        return SimpleNamespace(
            _explicit_args={"video_style"},
            video_style=video_style,
            style="default",
            duration=5,
            mode="std",
            aspect_ratio="9:16",
            dual_output=False,
            product_image=None,
            local_assets=str(tmp_path),
            voiceover=False,
        )

    with patch("one_click_create.run_generation_pipeline") as pipeline:
        pipeline.return_value = {
            "final_path": final_path,
            "wide_path": None,
            "preview": True,
        }
        run_one_click_create(
            {"name": "面包"},
            args("个人 Vlog"),
            output_name="vlog-preview",
            output_dir=tmp_path,
        )
        assert pipeline.call_args.kwargs["use_voiceover"] is True

        run_one_click_create(
            {"name": "面包"},
            args("产品展示"),
            output_name="product-preview",
            output_dir=tmp_path,
        )
        assert pipeline.call_args.kwargs["use_voiceover"] is False


def test_personal_vlog_fallback_keeps_material_timeline_and_segmented_narration():
    source = Path("one_click_create.py").read_text(encoding="utf-8")
    planning = source[
        source.index("if local_asset_mode:\n        from local_asset_pipeline"):
        source.index("print(\"\\n🎞️ 本地素材选片")
    ]
    narration = source[
        source.index("continuous_voiceover_text = None"):
        source.index("if _vlog_dialogue_track:", source.index("continuous_voiceover_text = None"))
    ]

    assert "if use_voiceover and not _personal_vlog_audio_mode:" in planning
    assert "连续旁白回退" not in planning
    assert "continuous_narration=local_asset_mode and not _personal_vlog_audio_mode" in narration
    assert "continuous_voiceover_text = str(ad_script.get(\"voiceover_full\") or \"\").strip()" in narration
    assert "max_rate_multiplier=1.15 if _personal_vlog_audio_mode else 1.6" in source
    assert "ensure_planned_narration_survived(" in narration
    assert '"mode": "personal_vlog_segmented_narration"' in source


def test_planned_vlog_narration_cannot_silently_degrade_to_zero_lines():
    ensure_planned_narration_survived(0, [], stage="间隙放置")
    ensure_planned_narration_survived(2, [{"text": "保留下来的旁白"}], stage="语音生成")

    with pytest.raises(RuntimeError, match="已计划 2 段.*间隙放置后为 0 段"):
        ensure_planned_narration_survived(2, [], stage="间隙放置")


def test_collects_complete_source_utterances_including_authentic_banter():
    index = _index([
        {"start": 0.2, "end": 1.5, "text": "今天带你看看面包", "confidence": 0.92},
        {"start": 3.7, "end": 4.4, "text": "这句话会被切断", "confidence": 0.95},
    ])

    candidates = collect_source_dialogue_candidates(
        [_selected()],
        index,
        {"name": "面包"},
    )

    assert [item["text"] for item in candidates] == ["今天带你看看面包"]
    assert candidates[0]["relative_start"] == pytest.approx(0.2)
    assert candidates[0]["relative_end"] == pytest.approx(1.5)

    unrelated = _selected(analysis={"speech_visual_relation": "unrelated"})
    assert [
        item["text"]
        for item in collect_source_dialogue_candidates([unrelated], index, {"name": "面包"})
    ] == ["今天带你看看面包"]


def test_rejects_unverified_claims_and_accepts_matching_product_facts():
    index = _index([
        {"start": 0.2, "end": 1.8, "text": "新鲜面包十块钱三个", "confidence": 0.95},
    ])

    assert collect_source_dialogue_candidates([_selected()], index, {"name": "面包"}) == []
    accepted = collect_source_dialogue_candidates(
        [_selected()],
        index,
        {"name": "面包", "price": "十块钱三个"},
    )
    assert [item["text"] for item in accepted] == ["新鲜面包十块钱三个"]


def test_places_dialogue_on_edit_timeline_and_reserves_narration_segments():
    candidates = [
        {
            "edit_index": 0,
            "semantic_segment": 0,
            "narrative": "hook",
            "source_path": "/tmp/vlog.mp4",
            "source_start": 0.5,
            "source_end": 1.5,
            "relative_start": 0.5,
            "relative_end": 1.5,
            "text": "今天带你看看",
            "confidence": 0.9,
            "score": 1.0,
        },
    ]
    plan = place_source_dialogue(
        candidates,
        [{"index": 0, "start": 1.0, "end": 5.0, "duration": 4.0}],
        trim_start=0.2,
        total_duration=6.0,
    )

    assert plan[0]["timeline_start"] == pytest.approx(1.3)
    assert plan[0]["timeline_end"] == pytest.approx(2.3)
    assert dialogue_subtitles(plan)[0]["source"] == "original_dialogue"

    narration = narration_lines_without_dialogue([
        {"text": "不应重叠", "start": 1.1, "end": 2.5, "segment": 1},
        {"text": "同一语义段也跳过", "start": 0.0, "end": 0.8, "segment": 0},
        {"text": "保留的旁白", "start": 3.0, "end": 4.0, "segment": 2},
        {"text": "", "start": 4.0, "end": 4.5, "segment": 3},
    ], plan)
    assert narration == [{"text": "保留的旁白", "start": 3.0, "end": 4.0, "segment": 2}]


def test_places_sparse_narration_only_inside_source_dialogue_gaps():
    lines = [{
        "text": "忙归忙，现场的乐子也不少。",
        "start": 2.0,
        "end": 7.0,
        "segment": 1,
        "narrative": "visual_context",
    }]
    dialogue = [
        {"timeline_start": 0.2, "timeline_end": 2.0, "semantic_segment": 0},
        {"timeline_start": 7.0, "timeline_end": 9.0, "semantic_segment": 2},
    ]

    planned = place_narration_in_dialogue_gaps(lines, dialogue, total_duration=10.0)

    assert planned
    assert all(
        item["start"] >= dialogue[0]["timeline_end"] + 0.06
        and item["end"] <= dialogue[1]["timeline_start"] - 0.06
        for item in planned
    )
    assert planned[0]["end"] - planned[0]["start"] >= 3.0


def test_personal_vlog_narration_does_not_use_unbounded_space_without_dialogue():
    texts = [
        "你猜这成堆备货的都是什么好东西？",
        "全都是每天新鲜准备的现烤面包。",
        "出摊支好摊子就能迎接客人了。",
        "你看这才一会功夫就卖空大半了。",
        "每天都提前备足新鲜货，就怕大家买不到。",
        "口味款式多，想吃哪款都能挑。",
        "想吃新鲜现烤面包的，快来尝尝吧。",
    ]
    durations = [4.0, 4.0, 4.0, 4.0, 4.0, 3.774, 2.537]
    lines = []
    cursor = 0.0
    for segment, (text, duration) in enumerate(zip(texts, durations)):
        lines.append({
            "text": text,
            "start": cursor,
            "end": cursor + duration,
            "segment": segment,
            "narrative": "opening_context" if segment == 0 else "source_dialogue",
        })
        cursor += duration

    planned = place_narration_in_dialogue_gaps(lines, [], total_duration=cursor)

    assert planned == []


def test_narration_prefers_internal_dialogue_gap_and_matching_style_without_filling_every_gap():
    dialogue = [
        {"timeline_start": 0.2, "timeline_end": 2.0, "semantic_segment": 0},
        {"timeline_start": 7.0, "timeline_end": 9.0, "semantic_segment": 2},
    ]
    lines = [
        {"text": "这里展示的是今天准备的面包。", "start": 2.0, "end": 7.0, "segment": 1, "narrative": "source_dialogue_context"},
        {"text": "谁懂啊，这一摊看着就忍不住。", "start": 2.0, "end": 7.0, "segment": 1, "narrative": "source_dialogue_context"},
    ]

    planned = place_narration_in_dialogue_gaps(
        lines,
        dialogue,
        total_duration=12.0,
        style_hint="humorous",
    )

    assert [item["text"] for item in planned] == ["谁懂啊，这一摊看着就忍不住。"]
    assert planned[0]["start"] >= dialogue[0]["timeline_end"] + 0.06
    assert planned[0]["end"] <= dialogue[1]["timeline_start"] - 0.06


def test_narration_moves_to_another_internal_gap_when_the_original_cue_is_too_short():
    lines = [{
        "text": "没想到，镜头一开就有小状况。",
        "start": 12.5,
        "end": 13.5,
        "segment": 5,
        "narrative": "visual_context",
    }]
    dialogue = [
        {"timeline_start": 0.2, "timeline_end": 2.0, "semantic_segment": 0},
        {"timeline_start": 10.0, "timeline_end": 12.0, "semantic_segment": 4},
        {"timeline_start": 14.0, "timeline_end": 16.0, "semantic_segment": 6},
    ]

    planned = place_narration_in_dialogue_gaps(lines, dialogue, total_duration=18.0)

    assert [item["text"] for item in planned] == ["没想到，镜头一开就有小状况。"]
    assert planned[0]["start"] >= dialogue[0]["timeline_end"] + 0.06
    assert planned[0]["end"] <= dialogue[1]["timeline_start"] - 0.06


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")
def test_renders_and_combines_playable_personal_vlog_speech_tracks(tmp_path: Path):
    source = tmp_path / "source.wav"
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-c:a", "pcm_s16le", str(source),
    ], capture_output=True, check=True)
    plan = [{
        "edit_index": 0,
        "semantic_segment": 0,
        "source_path": str(source),
        "source_start": 0.2,
        "source_end": 1.2,
        "timeline_start": 0.5,
        "timeline_end": 1.5,
        "duration": 1.0,
        "text": "原声",
        "confidence": 1.0,
    }]

    dialogue = render_source_dialogue_track(plan, tmp_path / "dialogue.m4a", 3.0)
    assert dialogue is not None and dialogue.stat().st_size > 512
    combined = mix_speech_tracks(dialogue, None, tmp_path / "combined.m4a", 3.0)
    assert combined.stat().st_size > 512
