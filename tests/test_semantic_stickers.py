import json
import inspect
import shutil
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from semantic_stickers import (
    _render_label_image,
    _safe_position,
    apply_sticker_plan_to_videos,
    build_semantic_sticker_plan,
    render_semantic_sticker_plan,
    resolve_sticker_enabled,
)


def test_sticker_mode_follows_sales_style_without_changing_other_styles():
    assert resolve_sticker_enabled("auto", video_style="带货") is True
    assert resolve_sticker_enabled("auto", video_style="个人 Vlog") is False
    assert resolve_sticker_enabled(
        "auto",
        voiceover_style="energetic",
        script_style="demonstration",
    ) is True
    assert resolve_sticker_enabled("on", video_style="个人 Vlog") is True
    assert resolve_sticker_enabled("off", video_style="带货") is False


def test_long_sticker_text_keeps_a_transparent_margin_instead_of_being_clipped(tmp_path):
    from PIL import Image

    output = tmp_path / "cta.png"
    _render_label_image(
        "点下方链接了解",
        "cta",
        1080,
        1920,
        "#FF6B6B",
        "#4ECDC4",
        output,
    )
    image = Image.open(output).convert("RGBA")
    assert all(
        image.getpixel((x, y))[3] == 0
        for x in range(image.width - 4, image.width)
        for y in range(image.height)
    )


def test_builtin_sticker_uses_a_lightweight_surface_instead_of_a_black_box(tmp_path):
    from PIL import Image

    output = tmp_path / "proof.png"
    _render_label_image(
        "配料透明",
        "proof",
        1080,
        1920,
        "#FF6B6B",
        "#4ECDC4",
        output,
    )

    image = Image.open(output).convert("RGBA")
    red, green, blue, alpha = image.getpixel((image.width // 2, 12))
    assert alpha > 180
    assert (red + green + blue) / 3 > 180


@pytest.mark.parametrize(("video_width", "video_height"), [(360, 640), (640, 360)])
def test_sticker_visual_contract_holds_across_aspect_ratios(
    tmp_path,
    video_width,
    video_height,
):
    import numpy as np
    from PIL import Image, ImageColor

    output = tmp_path / f"ingredient-{video_width}x{video_height}.png"
    sticker_width, sticker_height, _ = _render_label_image(
        "自然茉莉花原料",
        "ingredient",
        video_width,
        video_height,
        "#FF6B6B",
        "#4ECDC4",
        output,
    )
    image = Image.open(output).convert("RGBA")
    alpha_bounds = image.getchannel("A").getbbox()
    assert alpha_bounds is not None
    assert alpha_bounds[0] > 0 and alpha_bounds[1] > 0
    assert alpha_bounds[2] < image.width and alpha_bounds[3] < image.height
    max_width_ratio = 0.48 if video_height >= video_width else 0.36
    assert sticker_width <= round(video_width * max_width_ratio) + 8
    assert sticker_height < video_height * 0.2

    accent = ImageColor.getrgb("#4ECDC4")
    accent_pixels = sum(
        pixel[:3] == accent and pixel[3] > 200
        for pixel in image.getdata()
    )
    assert accent_pixels > 20
    assert image.getpixel((0, 0))[3] == 0
    center = image.getpixel((image.width // 2, image.height // 2))
    assert center[3] > 180 and sum(center[:3]) / 3 > 120

    frame = np.full((video_height, video_width, 3), 96, dtype=np.uint8)
    placement = _safe_position(
        [frame, frame, frame],
        sticker_width,
        sticker_height,
        video_width,
        video_height,
        subtitle_bottom_ratio=0.22,
        logo_position="top_right",
        logo_enabled=False,
    )
    assert placement is not None
    x, y, width, height = placement["rect"]
    assert 0 <= x < 1 and 0 <= y < 1
    assert x + width <= 1 and y + height < 1 - 0.22 - 0.04


@pytest.mark.parametrize(("video_width", "video_height"), [(1080, 1920), (1920, 1080)])
def test_long_fancy_subtitle_keeps_only_concrete_terms_inside_safe_width(
    tmp_path,
    video_width,
    video_height,
):
    from video_merger import add_fancy_subtitles

    source = tmp_path / f"source-{video_width}x{video_height}.mp4"
    source.write_bytes(b"video")
    output = tmp_path / f"output-{video_width}x{video_height}.mp4"
    captured = {}

    def capture_ass(_cmd, timeout=300):
        ass_path = next(tmp_path.glob(f"{output.stem}_fancy_subs.ass"))
        captured["content"] = ass_path.read_text(encoding="utf-8")

    probe = SimpleNamespace(
        returncode=0,
        stdout=f"{video_width}\n{video_height}\n2.0\n",
        stderr="",
    )
    with (
        patch("video_merger.subprocess.run", return_value=probe),
        patch("video_merger._has_audio_stream", return_value=False),
        patch("video_merger.run_ffmpeg", side_effect=capture_ass),
    ):
        add_fancy_subtitles(
            source,
            [{
                "text": "精选茉莉花采用高温烘焙工艺",
                "start": 0.0,
                "end": 2.0,
                "animation": "fade",
                "fancy": True,
                "highlight": ["茉莉花", "高温烘焙"],
            }],
            output,
            accent_color="#4ECDC4",
        )

    default_style = next(
        line for line in captured["content"].splitlines()
        if line.startswith("Style: Default,")
    ).split(",")
    font_size = int(default_style[2])
    margin_left = int(default_style[-4])
    dialogue = next(
        line for line in captured["content"].splitlines()
        if line.startswith("Dialogue:")
    )
    assert len("精选茉莉花采用高温烘焙工艺") * font_size <= video_width - 2 * margin_left
    assert r"{\rHighlight}茉莉花" in dialogue
    assert r"{\rHighlight}高温烘焙" in dialogue
    assert "Style: Special" not in captured["content"]


def test_portrait_placement_skips_when_only_the_subject_center_looks_available():
    import numpy as np

    frame = np.full((640, 360, 3), 96, dtype=np.uint8)
    checker = (np.indices((50, 360)).sum(axis=0) % 2 * 255).astype(np.uint8)
    busy = np.repeat(checker[:, :, None], 3, axis=2)
    frame[30:80] = busy
    frame[150:200] = busy
    changed_frame = frame.copy()
    changed_frame[30:80] = 255 - busy
    changed_frame[150:200] = 255 - busy

    placement = _safe_position(
        [frame, changed_frame, frame],
        sticker_width=130,
        sticker_height=40,
        video_width=360,
        video_height=640,
        subtitle_bottom_ratio=0.22,
        logo_position="top_right",
        logo_enabled=False,
    )

    assert placement is None


def test_safe_alternative_is_preferred_over_repeating_the_previous_position():
    import numpy as np

    frame = np.full((640, 360, 3), 96, dtype=np.uint8)
    checker = (np.indices((40, 100)).sum(axis=0) % 2 * 255).astype(np.uint8)
    frame[35:75, 130:230] = np.repeat(checker[:, :, None], 3, axis=2)

    placement = _safe_position(
        [frame, frame, frame],
        sticker_width=100,
        sticker_height=40,
        video_width=360,
        video_height=640,
        subtitle_bottom_ratio=0.22,
        logo_position="top_right",
        logo_enabled=True,
        previous_position="top_left",
    )

    assert placement["position"] == "top_center"


def test_origin_sticker_uses_subtitle_and_selected_material_without_inventing_location():
    plan = build_semantic_sticker_plan(
        ad_script={
            "segments": [{
                "segment": 3,
                "product_story_role": "origin",
                "subtitle": "好山好水环境种植，原料喝着更安心。",
                "visual_query": ["连片绿色种植植物", "山峰群"],
            }],
        },
        subtitles=[{
            "segment": 3,
            "text": "好山好水环境种植",
            "start": 7.4,
            "end": 9.6,
        }],
        selected_segments=[{
            "semantic_segment": 3,
            "product_story_role": "origin",
        }],
        product_info={"name": "茶咖", "type": "食品"},
        requested_mode="auto",
        video_style="带货",
    )

    assert plan["items"] == [{
        "id": "sticker-segment-3-origin",
        "segment": 3,
        "kind": "origin",
        "text": "产地实拍",
        "start": 7.25,
        "end": 9.6,
        "evidence_refs": ["subtitle:segment:3", "material_role:origin"],
        "source_subtitle": "好山好水环境种植",
        "render_mode": "label",
        "status": "planned",
    }]


def test_origin_sticker_accepts_source_environment_language_without_inventing_a_location():
    plan = build_semantic_sticker_plan(
        ad_script={"segments": [{
            "segment": 2,
            "product_story_role": "origin",
            "subtitle": "好山好水出好原料，底子就不一样。",
        }]},
        subtitles=[{
            "segment": 2,
            "text": "好山好水出好原料底子就不一样",
            "start": 5.8,
            "end": 8.8,
        }],
        selected_segments=[{
            "semantic_segment": 2,
            "product_story_role": "origin",
        }],
        requested_mode="on",
    )

    assert plan["items"][0]["text"] == "产地实拍"


def test_practical_usage_copy_without_verified_product_evidence_is_not_a_sticker():
    plan = build_semantic_sticker_plan(
        ad_script={"segments": [{
            "segment": 2,
            "product_story_role": "usage",
            "subtitle": "开瓶即倒，加冰就很清爽，不用自己调配。",
        }]},
        subtitles=[{
            "segment": 2,
            "text": "开瓶即倒加冰就很清爽",
            "start": 3.2,
            "end": 5.0,
        }],
        selected_segments=[{
            "semantic_segment": 2,
            "product_story_role": "usage",
        }],
        product_info={"name": "茶咖"},
        requested_mode="on",
    )

    assert plan["items"] == []
    assert plan["skipped"][-1]["reason"] == "verified_selling_point_required"


def test_visible_action_without_a_practical_benefit_does_not_become_a_sticker():
    plan = build_semantic_sticker_plan(
        ad_script={"segments": [{
            "segment": 1,
            "product_story_role": "usage",
            "subtitle": "开盖即饮，直接倒冰水。",
        }]},
        subtitles=[{
            "segment": 1,
            "text": "开盖即饮直接倒冰水",
            "start": 2.0,
            "end": 4.0,
        }],
        selected_segments=[{
            "semantic_segment": 1,
            "product_story_role": "usage",
        }],
        requested_mode="on",
    )

    assert plan["items"] == []
    assert plan["skipped"][-1]["reason"] == "verified_selling_point_required"


def test_generic_convenience_with_only_product_identity_is_not_a_selling_point_sticker():
    plan = build_semantic_sticker_plan(
        ad_script={"segments": [{
            "segment": 4,
            "product_story_role": "finished_product",
            "marketing_intent": "value",
            "evidence_refs": ["product:name"],
            "subtitle": "瓶装即饮，出门随身带也不麻烦。",
        }]},
        subtitles=[{
            "segment": 4,
            "text": "瓶装即饮出门随身带也不麻烦",
            "start": 8.0,
            "end": 10.4,
        }],
        selected_segments=[{
            "semantic_segment": 4,
            "product_story_role": "finished_product",
        }],
        requested_mode="on",
    )

    assert plan["items"] == []
    assert plan["skipped"][-1]["reason"] == "verified_selling_point_required"


def test_cta_copy_is_not_promoted_to_a_decision_information_sticker():
    plan = build_semantic_sticker_plan(
        ad_script={"segments": [{
            "segment": 5,
            "narrative": "cta",
            "marketing_intent": "cta",
            "product_story_role": "finished_product",
            "subtitle": "想要试试的，点下方链接了解。",
        }]},
        subtitles=[{
            "segment": 5,
            "text": "想要试试的点下方链接了解",
            "start": 11.0,
            "end": 13.0,
        }],
        selected_segments=[{
            "semantic_segment": 5,
            "product_story_role": "finished_product",
        }],
        product_info={"name": "茶咖"},
        requested_mode="on",
    )

    assert plan["items"] == []
    assert plan["skipped"][-1]["reason"] == "not_decision_information"


def test_sticker_timing_is_readability_driven_and_cannot_cross_the_shot_boundary():
    plan = build_semantic_sticker_plan(
        ad_script={"segments": [{
            "segment": 1,
            "product_story_role": "production",
            "subtitle": "低温萃取。",
        }]},
        subtitles=[{
            "segment": 1,
            "text": "低温萃取",
            "start": 2.2,
            "end": 4.8,
        }],
        selected_segments=[{
            "semantic_segment": 1,
            "product_story_role": "production",
        }],
        segment_timeline=[{"index": 1, "start": 2.0, "end": 3.8}],
        requested_mode="on",
    )

    item = plan["items"][0]
    assert item["start"] == 2.05
    assert item["end"] <= 3.7
    assert item["end"] - item["start"] >= 1.2


@pytest.mark.parametrize(("segment", "product_info", "expected_kind", "expected_text"), [
    (
        {
            "segment": 0,
            "product_story_role": "ingredient",
            "subtitle": "茉莉花茶原料清晰可见。",
            "visual_query": ["茉莉花茶"],
        },
        {"ingredients": ["茉莉花茶"]},
        "ingredient",
        "茉莉花原料",
    ),
    (
        {
            "segment": 0,
            "product_story_role": "production",
            "subtitle": "低温萃取，锁住茶香。",
        },
        {},
        "craft",
        "低温萃取",
    ),
    (
        {
            "segment": 0,
            "product_story_role": "finished_product",
            "marketing_intent": "value",
            "evidence_refs": ["product:verified_claim:0"],
            "subtitle": "大小瓶都有，居家出门都能随手带。",
        },
        {"verified_claims": ["大小瓶都有"]},
        "purchase_reason",
        "大小瓶都有",
    ),
    (
        {
            "segment": 0,
            "product_story_role": "finished_product",
            "narrative": "proof",
            "marketing_device": "proof",
            "claims": ["配料透明"],
            "subtitle": "配料清清楚楚，选择更放心。",
        },
        {"verified_claims": ["配料透明"]},
        "proof",
        "配料清清楚楚",
    ),
])
def test_supported_sticker_types_are_grounded_in_script_or_verified_facts(
    segment, product_info, expected_kind, expected_text,
):
    plan = build_semantic_sticker_plan(
        ad_script={"segments": [segment]},
        subtitles=[{"segment": 0, "text": segment["subtitle"], "start": 0.4, "end": 2.4}],
        selected_segments=[{
            "semantic_segment": 0,
            "product_story_role": segment["product_story_role"],
            "analysis": {
                "matched_product_entities": ["茉莉花"]
                if segment["product_story_role"] == "ingredient" else [],
            },
        }],
        product_info=product_info,
        requested_mode="on",
    )

    assert (plan["items"][0]["kind"], plan["items"][0]["text"]) == (
        expected_kind,
        expected_text,
    )


def test_local_ingredient_sticker_without_a_visual_entity_uses_generic_copy():
    plan = build_semantic_sticker_plan(
        ad_script={"segments": [{
            "segment": 0,
            "product_story_role": "ingredient",
            "subtitle": "茉莉花茶原料清晰可见。",
            "visual_query": ["白色花类物料"],
        }]},
        subtitles=[{
            "segment": 0,
            "text": "茉莉花茶原料清晰可见",
            "start": 0.4,
            "end": 2.4,
        }],
        selected_segments=[{
            "semantic_segment": 0,
            "product_story_role": "ingredient",
            "analysis": {"matched_product_entities": []},
        }],
        product_info={"ingredients": ["茉莉花茶"]},
        requested_mode="on",
    )

    assert plan["items"][0]["text"] == "原料实拍"


def test_sticker_density_scales_with_video_duration_and_never_exceeds_five():
    segments = [
        {
            "segment": index,
            "product_story_role": "finished_product",
            "marketing_intent": "value",
            "evidence_refs": [f"product:verified_claim:{index}"],
            "subtitle": f"第{index + 1}种大小规格。",
        }
        for index in range(7)
    ]
    subtitles = [
        {
            "segment": index,
            "text": segment["subtitle"],
            "start": index * 4.0 + 0.2,
            "end": index * 4.0 + 2.2,
        }
        for index, segment in enumerate(segments)
    ]

    plan = build_semantic_sticker_plan(
        ad_script={"segments": segments},
        subtitles=subtitles,
        selected_segments=[
            {"semantic_segment": index, "product_story_role": "finished_product"}
            for index in range(7)
        ],
        requested_mode="on",
    )

    assert len(plan["items"]) == 5
    assert sum(item["reason"] == "density_limit" for item in plan["skipped"]) == 2


def test_verified_purchase_reason_outranks_generic_craft_when_density_allows_only_one():
    plan = build_semantic_sticker_plan(
        ad_script={"segments": [
            {
                "segment": 0,
                "product_story_role": "production",
                "subtitle": "低温萃取，保留自然风味。",
            },
            {
                "segment": 1,
                "product_story_role": "usage",
                "evidence_refs": ["product:verified_claim:0"],
                "subtitle": "开瓶即倒，随时都能喝。",
            },
        ]},
        subtitles=[
            {"segment": 0, "text": "低温萃取保留自然风味", "start": 0.2, "end": 1.8},
            {"segment": 1, "text": "开瓶即倒随时都能喝", "start": 2.0, "end": 3.8},
        ],
        selected_segments=[
            {"semantic_segment": 0, "product_story_role": "production"},
            {"semantic_segment": 1, "product_story_role": "usage"},
        ],
        requested_mode="on",
    )

    assert [(item["kind"], item["text"]) for item in plan["items"]] == [
        ("purchase_reason", "随时都能喝"),
    ]


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is required")
def test_renderer_preserves_audio_and_respects_subtitle_and_logo_safe_zones(tmp_path):
    from PIL import Image

    source = tmp_path / "source.mp4"
    output = tmp_path / "stickered.mp4"
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    Image.new("RGBA", (64, 64), (255, 0, 0, 220)).save(asset_dir / "origin.png")
    subprocess.run([
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "color=c=#405060:s=360x640:d=2.4:r=24",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2.4",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
        str(source),
    ], check=True, capture_output=True)
    plan = {
        "items": [{
            "id": "sticker-segment-0-origin",
            "segment": 0,
            "kind": "origin",
            "text": "产地实拍",
            "start": 0.3,
            "end": 2.0,
            "status": "planned",
        }],
        "skipped": [],
        "layouts": {},
    }

    rendered = render_semantic_sticker_plan(
        video=source,
        output=output,
        plan=plan,
        primary_color="#FF6B6B",
        accent_color="#4ECDC4",
        subtitle_bottom_ratio=0.22,
        logo_position="top_right",
        logo_enabled=True,
        asset_dir=asset_dir,
    )

    probe = subprocess.run([
        "ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
        "-of", "json", str(output),
    ], check=True, capture_output=True, text=True)
    layout = rendered["layouts"]["9:16"][0]
    assert {stream["codec_type"] for stream in json.loads(probe.stdout)["streams"]} == {"video", "audio"}
    assert layout["rect"][1] + layout["rect"][3] < 0.78
    assert layout["position"] != "top_right"
    assert layout["asset_source"].endswith("origin.png")


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is required")
def test_renderer_varies_position_when_multiple_safe_regions_are_available(tmp_path):
    source = tmp_path / "source.mp4"
    output = tmp_path / "stickered.mp4"
    subprocess.run([
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "color=c=#607080:s=360x640:d=5:r=24",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source),
    ], check=True, capture_output=True)
    plan = {
        "items": [
            {
                "id": f"sticker-{index}",
                "segment": index,
                "kind": "proof",
                "text": text,
                "start": start,
                "end": end,
                "status": "planned",
            }
            for index, (text, start, end) in enumerate([
                ("配料透明", 0.2, 1.4),
                ("规格可选", 1.6, 2.8),
                ("随身方便", 3.0, 4.4),
            ])
        ],
        "skipped": [],
        "layouts": {},
    }

    rendered = render_semantic_sticker_plan(video=source, output=output, plan=plan)

    positions = [item["position"] for item in rendered["layouts"]["9:16"]]
    assert len(set(positions)) >= 2


def test_renderer_skips_sticker_when_every_candidate_region_is_visually_busy(tmp_path):
    import cv2
    import numpy as np

    source = tmp_path / "busy.mp4"
    output = tmp_path / "unchanged.mp4"
    writer = cv2.VideoWriter(
        str(source),
        cv2.VideoWriter_fourcc(*"mp4v"),
        24.0,
        (360, 640),
    )
    rng = np.random.default_rng(42)
    for _ in range(48):
        writer.write(rng.integers(0, 256, size=(640, 360, 3), dtype=np.uint8))
    writer.release()
    plan = {
        "items": [{
            "id": "sticker-segment-0-proof",
            "segment": 0,
            "kind": "proof",
            "text": "配料透明",
            "start": 0.2,
            "end": 1.8,
            "status": "planned",
        }],
        "skipped": [],
        "layouts": {},
    }

    rendered = render_semantic_sticker_plan(video=source, output=output, plan=plan)

    assert rendered["layouts"]["9:16"] == []
    assert rendered["skipped"][-1]["reason"] == "no_safe_visual_region"
    assert output.read_bytes() == source.read_bytes()


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is required")
def test_transactional_application_persists_rendered_item_status(tmp_path):
    video = tmp_path / "final.mp4"
    plan_path = tmp_path / "sticker_plan.json"
    subprocess.run([
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "color=c=#405060:s=360x640:d=1.8:r=24",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video),
    ], check=True, capture_output=True)
    plan = {
        "enabled": True,
        "items": [{
            "id": "sticker-segment-0-usage",
            "segment": 0,
            "kind": "usage",
            "text": "开瓶即倒",
            "start": 0.2,
            "end": 1.5,
            "status": "planned",
        }],
        "skipped": [],
        "layouts": {},
    }

    result = apply_sticker_plan_to_videos(
        videos={"9:16": video},
        plan=plan,
        plan_path=plan_path,
    )

    assert result["items"][0]["status"] == "rendered"
    assert json.loads(plan_path.read_text(encoding="utf-8"))["items"][0]["status"] == "rendered"


def test_single_and_batch_entrypoints_keep_a_backward_compatible_sticker_switch(monkeypatch, tmp_path):
    from batch import create_task_args
    from one_click_create import parse_args, run_generation_pipeline, run_one_click_create

    monkeypatch.setattr(sys, "argv", ["one_click_create.py"])
    assert parse_args().stickers == "auto"
    assert inspect.signature(run_generation_pipeline).parameters["stickers"].default == "auto"
    assert create_task_args({}, {})["stickers"] == "auto"

    final_path = tmp_path / "final.mp4"
    final_path.write_bytes(b"video")
    args = SimpleNamespace(
        _explicit_args={"video_style", "stickers"},
        video_style="带货",
        stickers="off",
        style="default",
        duration=5,
        mode="std",
        aspect_ratio="9:16",
        dual_output=False,
        product_image=None,
        local_assets=None,
        voiceover=False,
    )
    with patch("one_click_create.run_generation_pipeline") as pipeline:
        pipeline.return_value = {"final_path": final_path, "wide_path": None, "preview": True}
        run_one_click_create(
            {"name": "茶咖"},
            args,
            output_name="sticker-switch",
            output_dir=tmp_path,
        )

    assert pipeline.call_args.kwargs["stickers"] == "off"


def test_non_local_origin_sticker_requires_a_verified_product_fact():
    base = {
        "ad_script": {"segments": [{
            "segment": 0,
            "product_story_role": "origin",
            "subtitle": "山清水秀的种植环境。",
        }]},
        "subtitles": [{
            "segment": 0,
            "text": "山清水秀的种植环境",
            "start": 0.2,
            "end": 2.2,
        }],
        "selected_segments": None,
        "requested_mode": "on",
    }

    assert build_semantic_sticker_plan(product_info={}, **base)["items"] == []
    verified = build_semantic_sticker_plan(product_info={"origin": "贵州茶园"}, **base)
    assert verified["items"][0]["text"] == "贵州茶园"


def test_material_role_alone_cannot_create_a_sticker_unrelated_to_the_subtitle():
    plan = build_semantic_sticker_plan(
        ad_script={"segments": [{
            "segment": 0,
            "product_story_role": "production",
            "subtitle": "上班下午茶，不知道喝点什么新鲜的？",
        }]},
        subtitles=[{
            "segment": 0,
            "text": "上班下午茶不知道喝点什么新鲜的",
            "start": 0.2,
            "end": 2.8,
        }],
        selected_segments=[{
            "semantic_segment": 0,
            "product_story_role": "production",
        }],
        requested_mode="on",
    )

    assert plan["items"] == []
    assert plan["skipped"][-1]["reason"] == "subtitle_semantic_mismatch"


def test_sticker_render_failure_preserves_the_existing_video_and_records_the_reason(tmp_path):
    video = tmp_path / "final.mp4"
    plan_path = tmp_path / "sticker_plan.json"
    original = b"existing-final-video"
    video.write_bytes(original)
    plan = {
        "items": [{
            "id": "sticker-segment-0-origin",
            "segment": 0,
            "kind": "origin",
            "text": "产地实拍",
            "start": 0.2,
            "end": 1.8,
            "status": "planned",
        }],
        "skipped": [],
        "layouts": {},
    }

    result = apply_sticker_plan_to_videos(
        videos={"9:16": video},
        plan=plan,
        plan_path=plan_path,
    )

    assert video.read_bytes() == original
    assert result["skipped"][-1]["reason"] == "render_failure"
    assert json.loads(plan_path.read_text(encoding="utf-8"))["skipped"][-1]["reason"] == "render_failure"
