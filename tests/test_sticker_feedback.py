from sticker_feedback import StickerFeedbackStore
from semantic_stickers import build_semantic_sticker_plan


def test_only_explicit_feedback_from_distinct_videos_can_activate_a_sticker_rule(tmp_path):
    store = StickerFeedbackStore(tmp_path / "stickers.db")
    common = {
        "rule_text": "产地贴图不要连续出现",
        "verdict": "violated",
        "product_category": "食品",
        "video_style": "带货",
        "sticker": {"kind": "origin", "text": "产地实拍"},
    }

    assert store.record_feedback(video_id="video-a", source="automatic_quality", **common) is False
    assert store.record_feedback(video_id="video-a", source="user", **common) is True
    assert store.build_policy("食品", "带货")["rules"][0]["status"] == "provisional"
    assert store.record_feedback(video_id="video-b", source="user", **common) is True

    policy = store.build_policy("食品", "带货")
    assert policy["source"] == "explicit_user_feedback_only"
    assert policy["rules"][0]["status"] == "active"
    assert len(policy["negative_examples"]) == 2


def test_active_feedback_changes_candidate_ranking_without_changing_evidence_rules(tmp_path):
    store = StickerFeedbackStore(tmp_path / "stickers.db")
    for video_id in ("video-a", "video-b"):
        store.record_feedback(
            video_id=video_id,
            rule_text="产地贴图不要连续出现",
            verdict="violated",
            source="user",
            sticker={"kind": "origin", "text": "产地实拍"},
            product_category="食品",
            video_style="带货",
        )
    segments = [
        {"segment": 0, "product_story_role": "origin", "subtitle": "茶园环境种植。"},
        {
            "segment": 1,
            "product_story_role": "usage",
            "evidence_refs": ["product:verified_claim:0"],
            "subtitle": "随时都能喝。",
        },
    ]
    plan = build_semantic_sticker_plan(
        ad_script={"segments": segments},
        subtitles=[
            {"segment": 0, "text": "茶园环境种植", "start": 0.2, "end": 2.2},
            {"segment": 1, "text": "随时都能喝", "start": 2.5, "end": 4.8},
        ],
        selected_segments=[
            {"semantic_segment": 0, "product_story_role": "origin"},
            {"semantic_segment": 1, "product_story_role": "usage"},
        ],
        product_info={"type": "食品"},
        requested_mode="on",
        preference_policy=store.build_policy("食品", "带货"),
    )

    assert [item["kind"] for item in plan["items"]] == ["purchase_reason"]
