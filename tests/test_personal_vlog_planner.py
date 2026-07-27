from pathlib import Path

import pytest

from personal_vlog_planner import (
    PersonalVlogPlanningError,
    build_personal_vlog_script,
    build_personal_vlog_story_plan,
    materialize_personal_vlog_clips,
)


def _source(tmp_path: Path, name: str, text: str, start: float, end: float):
    path = tmp_path / f"{name}.mp4"
    return {
        "source": {
            "path": str(path),
            "duration": 8.0,
            "audio_understanding": {
                "has_speech": True,
                "segments": [{
                    "start": start,
                    "end": end,
                    "text": text,
                    "confidence": 0.95,
                }],
            },
        },
        "window": {
            "window_id": name,
            "source_video": path.name,
            "source_path": str(path),
            "start": 0.0,
            "end": 8.0,
            "duration": 8.0,
            "analysis": {
                "usable_for_ad": False,
                "confidence": 0.92,
                "product_story_role": "usage",
                "product_visibility": 5,
                "product_relevance_prior": "high",
                "speech_visual_relation": "aligned",
                "spoken_intents": ["conversation"],
                "narrative_roles": ["personal_vlog", "usage_demo"],
            },
            "motion": {"motion_class": "semi_dynamic", "stability": 0.9},
            "frame_quality": {"passed": True, "readable_ratio": 0.9},
        },
    }


def _index(items):
    return {
        "sources": [item["source"] for item in items],
        "windows": [item["window"] for item in items],
        "coverage": {},
    }


def _without_speech(item):
    item["source"]["audio_understanding"] = {"has_speech": False, "segments": []}
    return item


def test_story_duration_comes_from_complete_dialogue_events_not_short_ad_target(tmp_path):
    items = [
        _source(tmp_path, "opening", "今天先把摊子支起来", 1.0, 2.2),
        _source(tmp_path, "middle", "哎呀，滚歪了", 2.5, 3.4),
        _source(tmp_path, "ending", "卖得差不多了，都清空了", 4.0, 6.0),
    ]

    planning_index, contract = build_personal_vlog_story_plan(
        _index(items),
        {"name": "面包", "video_style": "个人 Vlog"},
        requested_duration=8.0,
    )

    assert contract["story_mode"] == "personal_vlog_source_dialogue"
    assert contract["duration_source"] == "complete_audio_visual_story_events_with_context"
    assert contract["natural_main_duration"] > 8.0
    assert contract["requested_duration_applied"] is False
    assert contract["safe_source_dialogue_count"] == 3
    assert len(planning_index["windows"]) == 3
    assert all(duration >= 3.5 for duration in contract["segment_durations"].values())
    for item, event in zip(items, planning_index["windows"]):
        utterance = item["source"]["audio_understanding"]["segments"][0]
        assert event["start"] <= utterance["start"]
        assert event["end"] >= utterance["end"]


def test_unverified_price_dialogue_never_becomes_vlog_story_anchor(tmp_path):
    safe = _source(tmp_path, "safe", "哎呀，滚歪了", 1.0, 2.0)
    price = _source(tmp_path, "price", "新鲜面包十块钱三个", 1.0, 2.0)

    _, contract = build_personal_vlog_story_plan(
        _index([safe, price]),
        {"name": "面包", "video_style": "个人 Vlog"},
        preview=True,
    )

    assert contract["safe_source_dialogue_count"] == 1
    assert contract["narrative_plan"][0]["planning_basis"]["transcript"] == "哎呀，滚歪了"


def test_near_black_dialogue_video_is_not_used_as_a_main_vlog_event(tmp_path):
    safe = _source(tmp_path, "safe", "随便挑", 1.0, 2.0)
    dark = _source(tmp_path, "dark", "这空筐子全堆满了", 1.0, 2.0)
    safe["window"]["frame_quality"]["samples"] = [{
        "time": 1.2,
        "brightness": 42.0,
        "contrast": 25.0,
        "readable": True,
    }]
    dark["window"]["frame_quality"]["samples"] = [{
        "time": 1.2,
        "brightness": 6.5,
        "contrast": 8.0,
        "readable": False,
    }]

    _, contract = build_personal_vlog_story_plan(
        _index([safe, dark]),
        {"name": "面包", "video_style": "个人 Vlog"},
        preview=True,
    )

    assert contract["safe_source_dialogue_count"] == 1
    assert contract["narrative_plan"][0]["planning_basis"]["transcript"] == "随便挑"


def test_vlog_planner_requires_at_least_one_safe_source_dialogue(tmp_path):
    price = _source(tmp_path, "price-only", "新鲜面包十块钱三个", 1.0, 2.0)

    with pytest.raises(PersonalVlogPlanningError, match="事实安全"):
        build_personal_vlog_story_plan(
            _index([price]),
            {"name": "面包", "video_style": "个人 Vlog"},
        )


def test_vlog_keeps_authentic_banter_drops_fragments_and_opens_chronologically(tmp_path):
    opening = _without_speech(_source(tmp_path, "opening", "", 0.0, 0.0))
    opening["window"]["analysis"].update({
        "confidence": 0.55,
        "product_story_role": "context",
    })
    fragment = _source(tmp_path, "fragment", "最少啊", 0.2, 0.6)
    banter = _source(tmp_path, "banter", "要潇洒一点的，可以", 0.7, 2.2)
    banter["window"]["analysis"]["speech_visual_relation"] = "unrelated"
    late_context = _without_speech(_source(tmp_path, "late", "", 0.0, 0.0))
    late_context["window"]["analysis"].update({
        "confidence": 1.0,
        "product_story_role": "usage",
    })

    planning_index, contract = build_personal_vlog_story_plan(
        _index([opening, fragment, banter, late_context]),
        {"name": "面包", "video_style": "个人 Vlog"},
    )

    assert contract["safe_source_dialogue_count"] == 1
    assert [window["source_video"] for window in planning_index["windows"]] == [
        "opening.mp4",
        "banter.mp4",
    ]
    assert contract["narrative_plan"][1]["planning_basis"]["transcript"] == "要潇洒一点的，可以"


def test_vlog_script_uses_source_events_without_ad_beats_or_cta(tmp_path):
    _, contract = build_personal_vlog_story_plan(
        _index([
            _source(tmp_path, "opening", "今天先把摊子支起来", 1.0, 2.2),
            _source(tmp_path, "ending", "卖得差不多了，都清空了", 4.0, 6.0),
        ]),
        {"name": "面包", "video_style": "个人 Vlog"},
    )

    script = build_personal_vlog_script(contract, {"name": "面包"})

    assert script["route"] == "personal_vlog_source_dialogue"
    assert script["generation_order"] == "audio_visual_story_events_then_sparse_gap_narration"
    assert [segment["source_dialogue"] for segment in script["segments"]] == [
        "今天先把摊子支起来",
        "卖得差不多了，都清空了",
    ]
    assert all(segment["marketing_intent"] == "story" for segment in script["segments"])
    assert all(segment["narrative"] != "cta" for segment in script["segments"])
    assert "voiceover_full" not in script


def test_vlog_reorders_cross_session_dialogue_into_a_daily_story(tmp_path):
    outcome = _source(tmp_path, "outcome", "卖得差不多了，都清空了", 1.0, 2.2)
    outcome["window"]["analysis"].update({
        "product_story_role": "result",
        "action_phase": "outcome",
    })
    process = _source(tmp_path, "process", "哎呀，滚歪了", 1.0, 2.0)
    process["window"]["analysis"]["action_phase"] = "action"
    orientation = _source(tmp_path, "orientation", "已经开始在录了，然后怎么关呢", 1.0, 2.4)
    orientation["window"]["analysis"].update({
        "product_story_role": "unknown",
        "action_phase": "none",
    })
    interaction = _source(tmp_path, "interaction", "来，随便挑", 1.0, 2.0)
    interaction["window"]["analysis"].update({
        "product_story_role": "finished_product",
        "action_phase": "action",
    })
    setup = _source(tmp_path, "setup", "先把摊子支起来", 1.0, 2.2)
    setup["window"]["analysis"].update({
        "product_story_role": "context",
        "action_phase": "setup",
    })

    planning_index, contract = build_personal_vlog_story_plan(
        _index([outcome, process, orientation, interaction, setup]),
        {"name": "面包", "video_style": "个人 Vlog"},
    )

    assert [
        (window["personal_vlog_event"]["story_phase"],
         window["personal_vlog_event"]["transcript"])
        for window in planning_index["windows"]
    ] == [
        ("orientation", "已经开始在录了，然后怎么关呢"),
        ("setup", "先把摊子支起来"),
        ("process", "哎呀，滚歪了"),
        ("interaction", "来，随便挑"),
        ("outcome", "卖得差不多了，都清空了"),
    ]
    assert [item["story_phase"] for item in contract["narrative_plan"]] == [
        "orientation", "setup", "process", "interaction", "outcome",
    ]


def test_stall_vlog_theme_frames_product_as_light_daily_sharing(tmp_path):
    opening = _without_speech(_source(tmp_path, "opening", "", 0.0, 0.0))
    opening["window"]["analysis"].update({
        "confidence": 0.7,
        "product_story_role": "context",
    })
    _, contract = build_personal_vlog_story_plan(
        _index([
            opening,
            _source(tmp_path, "interaction", "来，随便挑", 1.0, 2.0),
            _source(tmp_path, "outcome", "今天卖完了，可以收摊了", 1.0, 2.4),
        ]),
        {"name": "面包", "video_style": "个人 Vlog"},
    )
    script = build_personal_vlog_script(contract, {"name": "面包"})

    assert contract["theme"] == {
        "type": "stall_life",
        "subject": "面包",
        "title": "我的面包摆摊日常",
        "narrative_goal": "分享摆摊准备、现场过程、人物互动和收摊结果，让面包自然出现在日常里",
        "opening_narration": "最近记录了一些摆摊卖面包的日常。",
        "tone": "轻松、真实、日常分享",
    }
    assert script["title"] == "我的面包摆摊日常"
    assert script["segments"][0]["voiceover"] == ""
    assert "#摆摊日常" in script["hashtags"]
    assert all(segment["marketing_intent"] == "story" for segment in script["segments"])


def test_vlog_script_adds_sparse_contextual_narration_only_to_bounded_visual_runs():
    def item(index, event_kind, transcript="", role="unknown"):
        return {
            "segment": index,
            "narrative": "visual_context" if event_kind == "visual_support" else "source_dialogue",
            "event_kind": event_kind,
            "story_phase": "process",
            "product_story_role": role,
            "asset_window_ids": [f"event-{index}"],
            "planning_basis": {"transcript": transcript},
        }

    contract = {
        "theme": {
            "type": "stall_life",
            "title": "我的面包摆摊日常",
            "opening_narration": "最近记录了一些摆摊卖面包的日常。",
        },
        "natural_main_duration": 48.0,
        "narrative_plan": [
            item(0, "opening_context", role="context"),
            item(1, "source_dialogue", "哎呀，刚才滚歪了", "production"),
            item(2, "visual_support", role="production"),
            item(3, "visual_support", role="production"),
            item(4, "visual_support", role="finished_product"),
            item(5, "source_dialogue", "这炉刚搬过来", "finished_product"),
            item(6, "source_dialogue", "来，美女随便挑", "finished_product"),
            item(7, "visual_support", role="usage"),
            item(8, "source_dialogue", "客人都挑得差不多了", "result"),
        ],
    }

    script = build_personal_vlog_script(contract, {"name": "面包"})
    narrated = [segment for segment in script["segments"] if segment["voiceover"]]

    assert [segment["segment"] for segment in narrated] == [2, 7]
    assert all(segment["event_kind"] == "visual_support" for segment in narrated)
    assert "状况" in narrated[0]["voiceover"]
    assert "乐子" in narrated[1]["voiceover"]
    assert script["segments"][0]["voiceover"] == ""
    assert all(
        not segment["voiceover"]
        for segment in script["segments"]
        if segment["source_dialogue"]
    )


def test_generic_vlog_does_not_invent_a_stall_theme(tmp_path):
    _, contract = build_personal_vlog_story_plan(
        _index([
            _source(tmp_path, "walk", "今天出来走一走", 1.0, 2.0),
            _source(tmp_path, "weather", "今天风有点大", 1.0, 2.0),
            _source(tmp_path, "phrase", "别卖关子了，先选择一条路", 1.0, 2.2),
        ]),
        {"name": "周末", "video_style": "个人 Vlog"},
    )
    script = build_personal_vlog_script(contract, {"name": "周末"})

    assert contract["theme"]["type"] == "daily_life"
    assert "摆摊" not in script["title"]
    assert "摆摊" not in script["segments"][0]["voiceover"]
    assert "卖" not in script["segments"][0]["voiceover"]


def test_vlog_does_not_truncate_distinct_story_events_at_eight(tmp_path):
    texts = [
        "已经开始录了",
        "先把摊子摆好",
        "哎呀，滚歪了",
        "这炉刚搬过来",
        "来，美女随便挑",
        "客人问还有没有",
        "袋子给你装好了",
        "今天风有点大",
        "卖得差不多，都清空了",
    ]

    planning_index, contract = build_personal_vlog_story_plan(
        _index([
            _source(tmp_path, f"event-{index}", text, 1.0, 2.2)
            for index, text in enumerate(texts)
        ]),
        {"name": "面包", "video_style": "个人 Vlog"},
    )

    selected_texts = {
        window["personal_vlog_event"]["transcript"]
        for window in planning_index["windows"]
    }
    assert selected_texts == set(texts)
    assert contract["safe_source_dialogue_count"] == len(texts)


def test_vlog_deduplicates_only_when_dialogue_and_visual_meaning_both_repeat(tmp_path):
    repeated = _source(tmp_path, "repeated", "来，随便挑", 1.0, 2.0)
    duplicate = _source(tmp_path, "duplicate", "来，随便挑", 1.0, 2.0)
    different_dialogue = _source(tmp_path, "different", "袋子给你装好了", 1.0, 2.0)

    planning_index, contract = build_personal_vlog_story_plan(
        _index([repeated, duplicate, different_dialogue]),
        {"name": "面包", "video_style": "个人 Vlog"},
    )

    selected_texts = [
        window["personal_vlog_event"]["transcript"]
        for window in planning_index["windows"]
    ]
    assert selected_texts.count("来，随便挑") == 1
    assert "袋子给你装好了" in selected_texts
    assert contract["safe_source_dialogue_count"] == 2
    assert contract["selection_summary"]["audio_visual_duplicates_removed"] == 1


def test_vlog_adds_novel_silent_visual_context_without_repeating_it(tmp_path):
    dialogue = _source(tmp_path, "dialogue", "来，随便挑", 1.0, 2.0)
    production = _without_speech(_source(tmp_path, "production", "", 0.0, 0.0))
    production["window"]["analysis"].update({
        "product_story_role": "production",
        "action_phase": "action",
        "narrative_roles": ["production_process"],
        "visible_subjects": ["person", "product"],
    })
    repeated_production = _without_speech(
        _source(tmp_path, "production-repeat", "", 0.0, 0.0)
    )
    repeated_production["window"]["analysis"].update({
        "product_story_role": "production",
        "action_phase": "action",
        "narrative_roles": ["production_process"],
        "visible_subjects": ["person", "product"],
    })
    unknown = _without_speech(_source(tmp_path, "unknown", "", 0.0, 0.0))
    unknown["window"]["analysis"].update({
        "product_story_role": "unknown",
        "action_phase": "none",
        "narrative_roles": ["filler"],
    })

    planning_index, contract = build_personal_vlog_story_plan(
        _index([dialogue, production, repeated_production, unknown]),
        {"name": "面包", "video_style": "个人 Vlog"},
    )

    visual_support = [
        window for window in planning_index["windows"]
        if (window.get("personal_vlog_event") or {}).get("event_kind") == "visual_support"
    ]
    assert [window["source_video"] for window in visual_support] == ["production.mp4"]
    assert visual_support[0]["personal_vlog_event"]["story_phase"] == "process"
    assert contract["selection_summary"]["supplemental_visual_events"] == 1
    assert contract["selection_summary"]["invalid_visual_windows_removed"] == 1


def test_vlog_materializer_preserves_bound_event_order_and_exact_durations(tmp_path):
    planning_index, contract = build_personal_vlog_story_plan(
        _index([
            _source(tmp_path, "first", "今天先把摊子支起来", 1.0, 2.2),
            _source(tmp_path, "second", "哎呀，滚歪了", 2.5, 3.4),
        ]),
        {"name": "面包", "video_style": "个人 Vlog"},
    )
    script = build_personal_vlog_script(contract, {"name": "面包"})

    result = materialize_personal_vlog_clips(
        planning_asset_index=planning_index,
        story_contract=contract,
        vlog_script=script,
        clips_dir=tmp_path / "clips",
        final_dir=tmp_path / "final",
        output_name="vlog",
        plan_only=True,
    )

    assert [item["source_video"] for item in result["selected_segments"]] == [
        "first.mp4",
        "second.mp4",
    ]
    assert [item["target_duration"] for item in result["selected_segments"]] == [
        pytest.approx(contract["segment_durations"][0]),
        pytest.approx(contract["segment_durations"][1]),
    ]


def test_main_pipeline_routes_only_personal_vlog_around_ad_planning():
    source = Path("one_click_create.py").read_text(encoding="utf-8")

    assert "ad_script = build_personal_vlog_script(local_story_contract, product_info)" in source
    assert "local_asset_result = materialize_personal_vlog_clips(" in source
    assert "if _personal_vlog_audio_mode:\n        print(\"🎬 个人 Vlog 连续性" in source
    assert "else:\n            local_asset_result = plan_and_materialize_local_clips(" in source
    assert "个人 Vlog 节奏：服从原素材事件时长，不应用广告情绪节拍曲线" in source
    assert "个人 Vlog 视听叙事时间线已生成" in source
    assert "个人 Vlog 裁片：按视听叙事顺序保留连续上下文" in source
    assert "叙事=原素材口播事件" in source
    assert "个原声事件 + " in source
    assert "个增益画面" in source
    assert "视听联合筛选" in source
