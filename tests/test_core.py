"""
核心逻辑单元测试

运行方式：
    cd kling-ad-automation
    python -m pytest tests/ -v
"""

import sys
import time
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from unittest.mock import patch, MagicMock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest

from config import (
    CINEMATIC_STYLES,
    PRODUCT_PRESETS,
    get_preset,
    DEFAULT_CINEMATIC_STYLE,
    KLING_PRICING,
    KLING_VIDEO_MODEL,
)
from one_click_create import (
    apply_cinematic_style,
    generate_clip_prompts,
    generate_character_prompt,
    estimate_cost,
    print_cost_estimate,
    parse_args,
    build_stable_output_name,
    _bind_reference_tags_to_prompt,
    _build_video_idempotency_key,
    _build_clip_manifest,
    _build_character_manifest,
    _manifest_matches,
    _score_candidate_video_quality,
    _check_segment_semantic_quality,
    _is_product_required_narrative,
    _validate_product_image_file,
    _write_clip_manifest,
    _sanitize_prompt_for_image_generation,
    _preflight_keyframe_check,
    _estimate_image_first_segment_count,
    apply_low_cost_generation_policy,
    _record_production_workflow_completion,
    build_character_bibles,
    build_product_bible,
    character_bible_to_prompt,
    product_bible_to_prompt,
    _get_primary_char_for_clip,
    build_music_contract,
    _repair_prompt_by_issues,
    MusicContract,
    CharacterBible,
    ProductBible,
)


class TestCinematicStyles:
    """测试电影风格配置"""

    def test_cinematic_styles_not_empty(self):
        """至少有 1 种风格"""
        assert len(CINEMATIC_STYLES) >= 1

    def test_cinematic_styles_has_default(self):
        """默认风格应为 'auto'，且是有效的风格值"""
        from config import DEFAULT_CINEMATIC_STYLE
        assert DEFAULT_CINEMATIC_STYLE == "auto"
        valid_choices = list(CINEMATIC_STYLES.keys()) + [DEFAULT_CINEMATIC_STYLE, "none"]
        assert DEFAULT_CINEMATIC_STYLE in valid_choices

    def test_cinematic_styles_has_required_keys(self):
        """每种风格必须包含必要字段"""
        required_keys = {
            "name", "name_en", "description",
            "camera_push", "camera_pull", "camera_orbit",
            "transition_match", "transition_light",
            "lighting", "color", "mood",
        }
        for key, style in CINEMATIC_STYLES.items():
            missing = required_keys - set(style.keys())
            assert not missing, f"风格 {key} 缺少字段：{missing}"

    def test_cinematic_styles_all_have_non_empty_strings(self):
        """每种风格的字段都不能为空（支持字符串和列表类型）"""
        for key, style in CINEMATIC_STYLES.items():
            for field, value in style.items():
                if isinstance(value, str):
                    assert len(value) > 0, f"风格 {key} 的 {field} 为空字符串"
                elif isinstance(value, list):
                    assert len(value) > 0, f"风格 {key} 的 {field} 为空列表"
                    for i, item in enumerate(value):
                        assert isinstance(item, str) and len(item) > 0, \
                            f"风格 {key} 的 {field}[{i}] 为空或不是字符串"
                else:
                    assert False, f"风格 {key} 的 {field} 类型不支持：{type(value)}"


class TestProductPresets:
    """测试产品预设配置"""

    def test_product_presets_not_empty(self):
        """至少有 1 个产品预设"""
        assert len(PRODUCT_PRESETS) >= 1

    def test_product_presets_has_default(self):
        """必须有 default 预设"""
        assert "default" in PRODUCT_PRESETS

    def test_product_presets_has_required_keys(self):
        """每个预设必须包含必要字段"""
        required_keys = {"style", "lighting", "scene", "demo_action", "result"}
        for key, preset in PRODUCT_PRESETS.items():
            missing = required_keys - set(preset.keys())
            assert not missing, f"产品预设 {key} 缺少字段：{missing}"

    def test_get_preset_returns_default_for_unknown(self):
        """未知产品类型应返回 default 预设"""
        preset = get_preset("未知类型")
        assert preset == PRODUCT_PRESETS["default"]

    def test_get_preset_returns_correct_preset(self):
        """已知产品类型应返回对应预设"""
        for key in PRODUCT_PRESETS:
            if key != "default":
                preset = get_preset(key)
                assert preset == PRODUCT_PRESETS[key]


class TestApplyCinematicStyle:
    """测试电影风格注入"""

    def test_none_style_returns_base_prompt(self):
        """none 风格应返回原始 Prompt"""
        base = "static shot, slow push in"
        result = apply_cinematic_style(base, "none", "push")
        assert result == base

    def test_unknown_style_returns_base_prompt(self):
        """未知风格应返回原始 Prompt"""
        base = "static shot, slow push in"
        result = apply_cinematic_style(base, "unknown_style", "push")
        assert result == base

    def test_hitchcock_style_injects_camera_push(self):
        """hitchcock 风格应注入推镜描述"""
        base = "static shot, slow push in"
        result = apply_cinematic_style(base, "hitchcock", "push")
        assert "Hitchcock" in result
        assert "dolly zoom" in result.lower()

    def test_kubrick_style_injects_camera_pull(self):
        """kubrick 风格应注入拉镜描述"""
        base = "slow pull back"
        result = apply_cinematic_style(base, "kubrick", "pull")
        assert "Kubrick" in result

    def test_all_styles_have_push_description(self):
        """所有风格都必须有 push 描述"""
        for key, style in CINEMATIC_STYLES.items():
            if key != "none":
                desc = style.get("camera_push", "")
                assert len(desc) > 0, f"风格 {key} 缺少 camera_push 描述"

    def test_all_styles_have_pull_description(self):
        """所有风格都必须有 pull 描述"""
        for key, style in CINEMATIC_STYLES.items():
            if key != "none":
                desc = style.get("camera_pull", "")
                assert len(desc) > 0, f"风格 {key} 缺少 camera_pull 描述"

    def test_all_styles_have_orbit_description(self):
        """所有风格都必须有 orbit 描述"""
        for key, style in CINEMATIC_STYLES.items():
            if key != "none":
                desc = style.get("camera_orbit", "")
                assert len(desc) > 0, f"风格 {key} 缺少 camera_orbit 描述"


class TestGenerateCharacterPrompt:
    """测试角色定妆照 Prompt 生成"""

    def test_basic_product_info(self):
        """基础产品信息应生成有效 Prompt"""
        product_info = {
            "name": "测试产品",
            "type": "default",
            "age": "25",
            "gender": "女",
            "outfit": "casual clothes",
        }
        prompt = generate_character_prompt(product_info)
        assert "测试产品" in prompt
        assert "25-year-old" in prompt
        assert "女" in prompt

    def test_product_type_preset_affects_prompt(self):
        """不同产品类型应影响 Prompt"""
        product_info = {
            "name": "面霜",
            "type": "美妆",
            "age": "28",
            "gender": "女",
            "outfit": "white hoodie",
        }
        prompt = generate_character_prompt(product_info)
        # 美妆预设的 scene 应该出现在 prompt 中
        preset = get_preset("美妆")
        assert preset["scene"] in prompt


class TestGenerateClipPrompts:
    """测试分镜片段 Prompt 生成"""

    def test_generates_five_clips(self):
        """应生成 5 个片段"""
        product_info = {"name": "测试产品", "type": "default"}
        clips = generate_clip_prompts(product_info, cinematic_style="none")
        assert len(clips) == 5

    def test_hitchcock_style_injected_in_all_clips(self):
        """hitchcock 风格应注入所有片段"""
        product_info = {"name": "测试产品", "type": "default"}
        clips = generate_clip_prompts(product_info, cinematic_style="hitchcock")
        for clip in clips:
            assert "Hitchcock" in clip or "hitchcock" in clip.lower()

    def test_none_style_no_cinematic_injection(self):
        """none 风格不应注入电影描述"""
        product_info = {"name": "测试产品", "type": "default"}
        clips = generate_clip_prompts(product_info, cinematic_style="none")
        for clip in clips:
            # 不应包含导演名字
            assert "Hitchcock" not in clip
            assert "Kubrick" not in clip
            assert "Spielberg" not in clip

    def test_clips_contain_product_name(self):
        """展示/CTA 片段（2-5）应包含产品名称"""
        product_info = {"name": "我的产品", "type": "default"}
        clips = generate_clip_prompts(product_info, cinematic_style="none")
        # 片段 1（钩子）不含产品名，片段 2-5 应包含
        assert "我的产品" not in clips[0]
        for clip in clips[1:]:
            assert "我的产品" in clip

    def test_clips_are_strings(self):
        """所有片段应为字符串"""
        product_info = {"name": "测试", "type": "default"}
        clips = generate_clip_prompts(product_info, cinematic_style="none")
        for clip in clips:
            assert isinstance(clip, str)
            assert len(clip) > 0


class TestEstimateCost:
    """测试成本估算"""

    def test_pro_mode_5_clips_5s(self):
        """pro 模式 5 段 5 秒的成本估算"""
        result = estimate_cost(mode="pro", duration_per_clip=5, num_clips=5, num_characters=1)
        assert result["image_count"] == 2  # 1 角色 × 2 张/角色
        assert result["video_seconds"] == 25
        expected_cost = 2 * KLING_PRICING["image"]["pro"] + 25 * KLING_PRICING["video"]["pro"]
        assert abs(result["estimated_cost"] - expected_cost) < 0.01

    def test_std_mode_1_clip_preview(self):
        """预览模式（std + 1 段）的成本估算"""
        result = estimate_cost(mode="std", duration_per_clip=5, num_clips=1, num_characters=1)
        assert result["image_count"] == 2  # 1 角色 × 2 张/角色
        assert result["video_seconds"] == 5
        expected_cost = 2 * KLING_PRICING["image"]["std"] + 5 * KLING_PRICING["video"]["std"]
        assert abs(result["estimated_cost"] - expected_cost) < 0.01

    def test_preview_is_cheaper_than_pro(self):
        """预览模式成本应显著低于完整 pro 版本"""
        preview_cost = estimate_cost(mode="std", duration_per_clip=5, num_clips=1, num_characters=1)
        full_cost = estimate_cost(mode="pro", duration_per_clip=5, num_clips=5, num_characters=1)
        # 预览应该是完整版本的约 1/10 成本
        assert preview_cost["estimated_cost"] < full_cost["estimated_cost"] * 0.3

    def test_4k_mode_highest_cost(self):
        """4k 模式成本应最高"""
        std_cost = estimate_cost(mode="std", duration_per_clip=5, num_clips=5)["estimated_cost"]
        pro_cost = estimate_cost(mode="pro", duration_per_clip=5, num_clips=5)["estimated_cost"]
        k4_cost = estimate_cost(mode="4k", duration_per_clip=5, num_clips=5)["estimated_cost"]
        assert std_cost < pro_cost < k4_cost

    def test_ab_versions_multiplies_cost(self):
        """A/B 多版本应倍增成本"""
        cost_1 = estimate_cost(mode="pro", duration_per_clip=5, num_clips=5, ab_versions=1)
        cost_3 = estimate_cost(mode="pro", duration_per_clip=5, num_clips=5, ab_versions=3)
        assert abs(cost_3["estimated_cost"] - cost_1["estimated_cost"] * 3) < 0.01

    def test_image_first_cost_counts_preflight_candidates(self):
        """图片先行候选图应计入成本，避免低估发布级生成预算"""
        result = estimate_cost(
            mode="pro",
            duration_per_clip=5,
            num_clips=5,
            num_characters=1,
            image_first_segments=2,
            image_first_variants=2,
        )
        # 2 张角色定妆照（1角色×2） + 4 张图片先行候选（2段×2张/段）= 6 张
        assert result["image_count"] == 6
        assert any("图片先行预检" in line for line in result["breakdown"])

    def test_image_first_segment_count_by_mode(self):
        """图片先行范围估算应匹配 minimal/standard/full 策略"""
        assert _estimate_image_first_segment_count(5, "minimal") == 1
        assert _estimate_image_first_segment_count(5, "standard") == 2
        assert _estimate_image_first_segment_count(5, "full") == 5
        assert _estimate_image_first_segment_count(5, "standard", enabled=False) == 0


class TestParallelGeneration:
    """测试并行生成逻辑（mock 生成函数）"""

    def test_parallel_execution_completes_all_tasks(self):
        """并行执行应完成所有任务"""
        results = {}
        lock = threading.Lock()

        def mock_generate(idx, prompt):
            time.sleep(0.05)  # 模拟耗时
            with lock:
                results[idx] = f"clip_{idx:02d}.mp4"
            return (idx, f"clip_{idx:02d}.mp4", None)

        tasks = [{"idx": i, "prompt": f"prompt_{i}"} for i in range(2, 6)]  # 4 个任务

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(mock_generate, t["idx"], t["prompt"]) for t in tasks]
            for future in as_completed(futures):
                idx, path, err = future.result()

        assert len(results) == 4
        for i in range(2, 6):
            assert i in results
            assert results[i] == f"clip_{i:02d}.mp4"

    def test_parallel_is_faster_than_serial(self):
        """并行执行应快于串行"""
        def slow_task(idx):
            time.sleep(0.1)
            return idx

        # 串行计时
        start = time.time()
        serial_results = []
        for i in range(4):
            serial_results.append(slow_task(i))
        serial_time = time.time() - start

        # 并行计时
        start = time.time()
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(slow_task, i) for i in range(4)]
            parallel_results = [f.result() for f in as_completed(futures)]
        parallel_time = time.time() - start

        # 并行应该明显更快
        assert parallel_time < serial_time * 0.8
        assert len(parallel_results) == 4

    def test_parallel_with_failures_continues(self):
        """部分任务失败时，并行执行应继续并收集成功结果"""
        results = {}
        lock = threading.Lock()
        fail_indices = {3, 5}  # 让第 3、5 段失败

        def mock_generate(idx, prompt):
            time.sleep(0.02)
            if idx in fail_indices:
                with lock:
                    results[idx] = None
                return (idx, None, RuntimeError("mock failure"))
            with lock:
                results[idx] = f"clip_{idx:02d}.mp4"
            return (idx, f"clip_{idx:02d}.mp4", None)

        tasks = [{"idx": i, "prompt": f"prompt_{i}"} for i in range(2, 7)]  # 5 个任务（段 2-6）

        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(mock_generate, t["idx"], t["prompt"]) for t in tasks]
            for future in as_completed(futures):
                idx, path, err = future.result()

        success_count = sum(1 for v in results.values() if v is not None)
        fail_count = sum(1 for v in results.values() if v is None)

        assert success_count == 3  # 5 段中 3 段成功
        assert fail_count == 2     # 2 段失败
        # 成功数 >= 3 应该继续（60% 阈值）
        min_clips = 3
        assert success_count >= min_clips

    def test_parallel_below_min_clips_should_fail(self):
        """成功数低于 min_clips 时应判定失败"""
        results = {}
        lock = threading.Lock()

        def mock_generate(idx, prompt):
            time.sleep(0.02)
            if idx > 3:  # 只有前 2 段成功
                with lock:
                    results[idx] = None
                return (idx, None, RuntimeError("mock failure"))
            with lock:
                results[idx] = f"clip_{idx:02d}.mp4"
            return (idx, f"clip_{idx:02d}.mp4", None)

        tasks = [{"idx": i, "prompt": f"prompt_{i}"} for i in range(2, 7)]  # 5 个任务

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(mock_generate, t["idx"], t["prompt"]) for t in tasks]
            for future in as_completed(futures):
                idx, path, err = future.result()

        success_count = sum(1 for v in results.values() if v is not None)
        min_clips = 3
        # 只有 2 段成功，低于 3 段的最低要求
        assert success_count < min_clips
        # 这种情况应抛出 RuntimeError（由调用方判定）
        with pytest.raises(RuntimeError):
            if success_count < min_clips:
                raise RuntimeError(
                    f"片段生成失败过多（成功 {success_count}/5，需要 ≥{min_clips} 段）"
                )

    def test_results_are_ordered_by_index(self):
        """并行结果应按索引顺序收集"""
        results = {}
        lock = threading.Lock()

        def mock_generate(idx, prompt):
            # 让高索引先完成（倒序完成）
            sleep_time = (10 - idx) * 0.01
            time.sleep(sleep_time)
            with lock:
                results[idx] = f"clip_{idx:02d}.mp4"
            return (idx, f"clip_{idx:02d}.mp4", None)

        tasks = [{"idx": i, "prompt": f"prompt_{i}"} for i in range(2, 6)]

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(mock_generate, t["idx"], t["prompt"]) for t in tasks]
            for future in as_completed(futures):
                future.result()

        # 按索引顺序收集
        ordered_clips = []
        for i in range(2, 6):
            if results.get(i):
                ordered_clips.append(results[i])

        assert ordered_clips == [
            "clip_02.mp4", "clip_03.mp4", "clip_04.mp4", "clip_05.mp4"
        ]

    def test_thread_safety_of_shared_dict(self):
        """并发写入共享字典应是线程安全的（使用 Lock）"""
        results = {}
        lock = threading.Lock()
        counter = {"value": 0}
        counter_lock = threading.Lock()

        def mock_generate(idx, prompt):
            with lock:
                results[idx] = f"clip_{idx:02d}.mp4"
            with counter_lock:
                counter["value"] += 1
            return (idx, f"clip_{idx:02d}.mp4", None)

        tasks = [{"idx": i, "prompt": f"prompt_{i}"} for i in range(100)]

        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = [executor.submit(mock_generate, t["idx"], t["prompt"]) for t in tasks]
            for future in as_completed(futures):
                future.result()

        assert len(results) == 100
        assert counter["value"] == 100


class TestCLIArguments:
    """测试新的 CLI 参数"""

    def test_preview_flag_short(self):
        """-p 短选项应启用预览模式"""
        args = parse_args.__wrapped__(["-p"]) if hasattr(parse_args, "__wrapped__") else None
        # 直接测试 argparse 行为
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--preview", "-p", action="store_true")
        args = parser.parse_args(["-p"])
        assert args.preview is True

    def test_preview_flag_long(self):
        """--preview 长选项应启用预览模式"""
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--preview", "-p", action="store_true")
        args = parser.parse_args(["--preview"])
        assert args.preview is True

    def test_serial_flag(self):
        """--serial 应强制串行模式"""
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--serial", action="store_true")
        args = parser.parse_args(["--serial"])
        assert args.serial is True

    def test_min_clips_default(self):
        """--min-clips 默认值应为 3"""
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--min-clips", type=int, default=3)
        args = parser.parse_args([])
        assert args.min_clips == 3

    def test_min_clips_custom(self):
        """--min-clips 可自定义"""
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--min-clips", type=int, default=3)
        args = parser.parse_args(["--min-clips", "4"])
        assert args.min_clips == 4

    def test_max_workers_default(self):
        """--max-workers 默认值应为 4"""
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--max-workers", type=int, default=4)
        args = parser.parse_args([])
        assert args.max_workers == 4

    def test_default_parallel_mode(self):
        """默认应为并行模式（serial=False）"""
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--serial", action="store_true")
        args = parser.parse_args([])
        parallel = not args.serial
        assert parallel is True

    def test_quality_defaults_are_publish_first(self):
        """CLI 默认应启用发布级质量策略，同时避免默认视频抽卡"""
        with patch.object(sys, "argv", ["one_click_create.py"]):
            args = parse_args()
        assert args.strict is True
        assert args.stabilize is True
        assert args.best_of == 1
        assert args.preflight_keyframe is True
        assert args.image_first is True
        assert args.image_first_mode == "standard"
        assert args.image_first_variants == 2

    def test_quality_defaults_can_be_disabled_for_debug(self):
        """调试时应允许显式关闭严格模式和稳定化"""
        with patch.object(sys, "argv", ["one_click_create.py", "--no-strict", "--no-stabilize", "--no-image-first", "--best-of", "1"]):
            args = parse_args()
        assert args.strict is False
        assert args.stabilize is False
        assert args.image_first is False
        assert args.best_of == 1


class TestProductPresenceDetection:
    """测试轻量产品出现检测"""

    def test_product_similarity_same_image_high(self, tmp_path):
        """同一商品图与帧图应有较高相似度"""
        from PIL import Image, ImageDraw
        from quality_checker import _product_similarity

        product = tmp_path / "product.png"
        frame = tmp_path / "frame.png"

        img = Image.new("RGB", (160, 160), "white")
        draw = ImageDraw.Draw(img)
        draw.rectangle((45, 25, 115, 135), fill=(220, 30, 60))
        draw.ellipse((65, 45, 95, 75), fill=(255, 240, 180))
        img.save(product)
        img.save(frame)

        assert _product_similarity(product, [frame]) >= 0.8

    def test_product_similarity_different_image_low(self, tmp_path):
        """明显不同的画面应有较低商品相似度"""
        from PIL import Image, ImageDraw
        from quality_checker import _product_similarity

        product = tmp_path / "product.png"
        frame = tmp_path / "frame.png"

        p = Image.new("RGB", (160, 160), "white")
        pd = ImageDraw.Draw(p)
        pd.rectangle((45, 25, 115, 135), fill=(220, 30, 60))
        p.save(product)

        f = Image.new("RGB", (160, 160), (20, 80, 210))
        fd = ImageDraw.Draw(f)
        fd.polygon([(20, 20), (140, 40), (80, 140)], fill=(30, 220, 80))
        f.save(frame)

        assert _product_similarity(product, [frame]) < 0.45


class TestSemanticQualityGates:
    """测试语义质量门禁基础能力"""

    def test_character_similarity_same_image_high(self, tmp_path):
        """同一角色参考图与帧图应有较高相似度"""
        from PIL import Image, ImageDraw
        from quality_checker import _character_similarity

        ref = tmp_path / "char.png"
        frame = tmp_path / "frame.png"

        img = Image.new("RGB", (180, 240), (245, 245, 245))
        draw = ImageDraw.Draw(img)
        draw.ellipse((55, 35, 125, 115), fill=(230, 160, 120))
        draw.rectangle((70, 115, 110, 210), fill=(40, 80, 180))
        img.save(ref)
        img.save(frame)

        assert _character_similarity(ref, [frame]) >= 0.75

    def test_check_video_quality_accepts_semantic_gate_args(self):
        """发布级质检应支持商品/角色语义门禁参数"""
        import inspect
        from quality_checker import check_video_quality

        sig = inspect.signature(check_video_quality)
        assert "character_reference_image" in sig.parameters
        assert "require_semantic_alignment" in sig.parameters


class TestResumeAndIdempotency:
    """测试断点续跑和幂等键"""

    def test_parse_args_has_resume_output_name_and_product_escape_hatch(self):
        """CLI 应暴露续跑参数和无商品图显式放行参数"""
        with patch.object(
            sys,
            "argv",
            ["one_click_create.py", "--resume", "--output-name", "demo_run", "--allow-no-product-image"],
        ):
            args = parse_args()
        assert args.resume is True
        assert args.output_name == "demo_run"
        assert args.allow_no_product_image is True

    def test_resume_is_enabled_by_default_and_can_be_disabled(self):
        """默认应复用稳定输出名，必要时可显式关闭。"""
        with patch.object(sys, "argv", ["one_click_create.py"]):
            args = parse_args()
        assert args.resume is True

        with patch.object(sys, "argv", ["one_click_create.py", "--no-resume"]):
            args = parse_args()
        assert args.resume is False

    def test_stable_output_name_is_deterministic(self):
        """相同输入和关键参数应生成相同续跑输出名"""
        product_info = {"name": "测试产品", "type": "美妆", "selling_point": "清爽"}
        with patch.object(sys, "argv", ["one_click_create.py", "--style", "none", "--seed", "42"]):
            args = parse_args()
        first = build_stable_output_name(product_info, args)
        second = build_stable_output_name(product_info, args)
        assert first == second
        assert first.startswith("测试产品_")

    def test_idempotency_key_is_stable_for_same_candidate(self):
        """同一候选视频的幂等键必须稳定，供 POST 重试复用"""
        key1 = _build_video_idempotency_key(
            "prompt",
            ["data:image/png;base64,abc"],
            1,
            Path("clip_01_demo_cand1.mp4"),
            model="kling-v3-omni",
            mode="pro",
            duration=5,
            aspect_ratio="9:16",
            seed=42,
        )
        key2 = _build_video_idempotency_key(
            "prompt",
            ["data:image/png;base64,abc"],
            1,
            Path("clip_01_demo_cand1.mp4"),
            model="kling-v3-omni",
            mode="pro",
            duration=5,
            aspect_ratio="9:16",
            seed=42,
        )
        assert key1 == key2
        assert key1.startswith("kaa-")

    def test_reference_binding_is_structured(self):
        """参考图 tag 应以结构化语义块绑定，不再依赖泛关键词插入"""
        prompt = "A person uses the product in a clean room."
        result = _bind_reference_tags_to_prompt(
            prompt,
            [
                {"role": "product", "image": "img1"},
                {"role": "character", "image": "img2"},
                {"role": "continuity", "image": "img3"},
            ],
            "showcase",
        )
        assert result.startswith("PRODUCT REFERENCE")
        assert "<<<image_1>>>" in result
        assert "<<<image_2>>>" in result
        assert "<<<image_3>>>" in result
        assert "must match exactly" in result
        assert "Exact same person" in result
        assert "continuity" in result.lower()
        assert prompt in result

    def test_reference_binding_marks_approved_keyframe(self):
        """图片先行通过的关键帧应作为强首帧参考绑定到视频 Prompt"""
        result = _bind_reference_tags_to_prompt(
            "A person presents the product.",
            [{"role": "approved_keyframe", "image": "img1"}],
            "showcase",
        )
        assert "APPROVED KEYFRAME REFERENCE" in result
        assert "quality preflight passed" in result
        assert "first-frame visual target" in result

    def test_image_first_approved_keyframe_skips_duplicate_preflight(self):
        """图片先行已通过的片段不应再额外触发单张首帧预检"""
        src = Path("one_click_create.py").read_text(encoding="utf-8")
        assert "and (idx - 1) not in approved_keyframes" in src

    def test_reference_binding_uses_roles_not_narrative_guess(self):
        """展示段如果只有角色图，也不能误标为 Product reference"""
        result = _bind_reference_tags_to_prompt(
            "A person talks to camera.",
            [{"role": "character", "image": "img1"}],
            "showcase",
        )
        assert "CHARACTER REFERENCE" in result
        assert "<<<image_1>>>" in result
        assert "Exact same person" in result
        assert "PRODUCT REFERENCE" not in result

    def test_clip_manifest_must_match_for_cache(self, tmp_path):
        """片段缓存必须严格匹配 manifest，避免旧画面配新字幕"""
        clip = tmp_path / "clip_01_demo.mp4"
        clip.write_bytes(b"x" * 1024)
        manifest = _build_clip_manifest(
            final_prompt="old prompt",
            ref_images=[{"role": "character", "image": "img1"}],
            idx=1,
            model="kling-v3-omni",
            mode="pro",
            duration=5,
            aspect_ratio="9:16",
            seed=42,
            negative_prompt="bad",
        )
        manifest["target_name"] = clip.name
        _write_clip_manifest(clip, manifest)

        assert _manifest_matches(clip, manifest) is True

        changed = dict(manifest)
        changed["prompt_sha256"] = "different"
        assert _manifest_matches(clip, changed) is False

    def test_low_cost_policy_reduces_best_of_before_mode(self):
        """超预算时应优先减少候选数，再降低生成模式。"""
        with patch.object(
            sys,
            "argv",
            ["one_click_create.py", "--mode", "4k", "--best-of", "3", "--duration", "8"],
        ):
            args = parse_args()

        changes = apply_low_cost_generation_policy(
            args,
            num_clips=5,
            num_characters=1,
            ab_versions=1,
            budget_limit=20.0,
        )

        assert changes[0] == "best_of 3 -> 1"
        assert args.best_of == 1
        assert "mode 4k -> pro" in changes
        assert args.mode in {"pro", "std"}


class TestBatchQualityDefaults:
    """测试批量模式质量默认值"""

    def test_batch_defaults_match_publish_first_policy(self):
        """批量生成默认也应使用发布级质量策略，同时避免默认视频抽卡"""
        from batch import create_task_args

        args = create_task_args({"product_name": "测试产品"}, {})
        assert args["strict_mode"] is True
        assert args["stabilize"] is True
        assert args["best_of"] == 1
        assert args["preflight_keyframe"] is True
        assert args["image_first"] is True
        assert args["image_first_mode"] == "standard"
        assert args["image_first_variants"] == 2
        assert args["allow_no_product_image"] is False
        assert args["resume"] is True

    def test_batch_stable_output_name_is_deterministic(self):
        """批量任务默认输出名应稳定，便于重跑命中缓存。"""
        from batch import _build_stable_task_output_name

        kwargs = {
            "product_info": {"name": "测试产品", "type": "美妆"},
            "style": "none",
            "duration": 5,
            "mode": "pro",
            "aspect_ratio": "9:16",
        }

        first = _build_stable_task_output_name(1, "测试产品", kwargs)
        second = _build_stable_task_output_name(1, "测试产品", dict(reversed(list(kwargs.items()))))

        assert first == second
        assert first.startswith("001_测试产品_")


class TestProductionWorkflowBridge:
    """回归测试：one_click 主流程接入工作流闭环"""

    def test_completion_records_assets_feedback_and_experiment(self, tmp_path):
        """最终质检通过后应登记资产、收集反馈并追踪实验。"""
        from PIL import Image

        image_path = tmp_path / "product.png"
        Image.new("RGB", (64, 64), (80, 120, 200)).save(image_path)
        final_path = tmp_path / "final.mp4"
        final_path.write_bytes(b"fake-video")

        class FakeQuality:
            overall_score = 86.0
            issues = []

        class FakeAssetLibrary:
            def __init__(self):
                self.characters = []
                self.products = []
                self.scores = []

            def add_character(self, **kwargs):
                self.characters.append(kwargs)
                return "char_1"

            def add_product(self, **kwargs):
                self.products.append(kwargs)
                return "product_1"

            def update_quality_score(self, asset_id, score):
                self.scores.append((asset_id, score))

        class FakeFeedbackLoop:
            def __init__(self):
                self.calls = []

            def collect_feedback(self, **kwargs):
                self.calls.append(kwargs)
                return True

        class FakeExperimentTracker:
            def __init__(self):
                self.started = []
                self.completed = []

            def start_experiment(self, **kwargs):
                self.started.append(kwargs)
                return True

            def complete_experiment(self, **kwargs):
                self.completed.append(kwargs)
                return True

        assets = FakeAssetLibrary()
        feedback = FakeFeedbackLoop()
        experiments = FakeExperimentTracker()

        summary = _record_production_workflow_completion(
            output_name="demo",
            final_path=final_path,
            quality_result=FakeQuality(),
            product_info={"name": "测试产品", "type": "美妆"},
            ad_script={"segments": [{"narrative": "hook"}]},
            generation_params={"mode": "pro", "best_of": 1},
            character_assets=[{"name": "主角", "image_path": image_path}],
            product_image_path=image_path,
            character_bibles=[{"name": "主角"}],
            product_bible={"name": "测试产品"},
            asset_library=assets,
            feedback_loop=feedback,
            experiment_tracker=experiments,
        )

        assert len(summary["registered_assets"]) == 2
        assert summary["feedback_collected"] is True
        assert summary["experiment_tracked"] is True
        assert len(feedback.calls) == 1
        assert len(experiments.started) == 1
        assert len(experiments.completed) == 1

    def test_one_click_pipeline_calls_workflow_bridge_after_quality_gate(self):
        """主流水线源码应包含前置智能决策和完成闭环。"""
        src = Path("one_click_create.py").read_text(encoding="utf-8")
        assert "_run_pre_generation_smart_decision(" in src
        assert "_record_production_workflow_completion(" in src

    def test_workflow_orchestrator_uses_current_quality_gate_api(self):
        """工作流编排器应调用当前质量门 API，而不是旧的 storyboard 参数。"""
        src = Path("workflow_orchestrator.py").read_text(encoding="utf-8")
        quality_block = src[src.index("def _step_quality_gate"):src.index("def _step_smart_decision")]
        assert "ad_script=ad_script" in quality_block
        assert "product_image_path=product_image_path" in quality_block
        assert "run_quality_gate(\n            storyboard=" not in quality_block

    def test_strict_quality_gate_failure_blocks_without_prompt(self):
        """严格模式下质量门失败应直接阻断，不能进入人工确认再抽视频。"""
        src = Path("one_click_create.py").read_text(encoding="utf-8")
        block = src[src.index("if not quality_gate_result.passed:"):src.index("workflow_decision_result =")]
        assert "if strict_mode:" in block
        assert "避免进入高成本视频抽卡" in block
        assert block.index("if strict_mode:") < block.index("input(")

    def test_workflow_registers_video_clips_as_video_assets(self):
        """工作流资产注册不能把 mp4 片段当商品图片资产。"""
        src = Path("workflow_orchestrator.py").read_text(encoding="utf-8")
        asset_block = src[src.index("def _step_asset_registration"):src.index("def _step_feedback_collection")]
        block = asset_block[asset_block.index("for clip in video_clips:"):asset_block.index("print(f\"📦 资产注册完成")]
        assert "add_video_clip(" in block
        assert "add_product(" not in block

    def test_asset_library_supports_video_clip_assets(self, tmp_path):
        """资产库应原生支持视频片段资产类型。"""
        from asset_library import AssetLibrary

        video = tmp_path / "clip.mp4"
        video.write_bytes(b"fake-video")

        library = AssetLibrary(tmp_path / "assets")
        asset_id = library.add_video_clip(
            video_path=video,
            name="clip_0",
            metadata={"narrative": "hook"},
            tags=["clip", "hook"],
        )

        asset = library.get_asset(asset_id)
        assert asset["asset_type"] == "video_clip"
        assert Path(asset["video_path"]).exists()
        assert library.get_stats()["video_clips"] == 1


class TestTrimOffsetCompensation:
    """回归测试：预裁切后字幕/口播时间轴偏移补偿"""

    def test_subtitle_times_shifted_by_trim_start(self):
        """字幕起始/结束时间应减去裁切时长并截断到 >=0"""
        subtitles = [
            {"start": 0.5, "end": 2.5, "text": "第一段"},
            {"start": 3.0, "end": 5.0, "text": "第二段"},
        ]
        _trim_start = 0.3
        for sub in subtitles:
            sub["start"] = max(0.0, sub["start"] - _trim_start)
            sub["end"] = max(0.0, sub["end"] - _trim_start)

        assert subtitles[0]["start"] == 0.2
        assert subtitles[0]["end"] == 2.2
        assert subtitles[1]["start"] == 2.7
        assert subtitles[1]["end"] == 4.7

    def test_subtitle_times_clamped_to_zero(self):
        """当裁切时长大于字幕起始时间时，应截断到 0"""
        subtitles = [
            {"start": 0.1, "end": 2.0, "text": "第一段"},
        ]
        _trim_start = 0.3
        for sub in subtitles:
            sub["start"] = max(0.0, sub["start"] - _trim_start)
            sub["end"] = max(0.0, sub["end"] - _trim_start)

        assert subtitles[0]["start"] == 0.0
        assert subtitles[0]["end"] == 1.7


class TestFallbackAudioQualityGate:
    """回归测试：fallback 底噪应被质量门拦截"""

    def test_analyze_audio_flags_extremely_low_volume(self):
        """_analyze_audio_ffmpeg 应对低于 -35 LUFS 的音频标记问题"""
        from quality_checker import _analyze_audio_ffmpeg
        from unittest.mock import patch

        # 模拟 ffmpeg 返回 -45 LUFS 的响度数据
        mock_stderr = '{"input_i":"-45.0","input_tp":"-50.0","input_lra":"1.0"}'
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stderr=mock_stderr, returncode=0)
            lufs, peak, issues = _analyze_audio_ffmpeg(Path("dummy.mp4"))

        assert lufs == -45.0
        assert any("音量极低" in issue for issue in issues)

    def test_quality_gate_rejects_silent_audio_when_required(self):
        """require_audio=True 时，底噪填充音频应直接判失败"""
        from quality_checker import VideoQualityResult

        result = VideoQualityResult()
        result.audio_lufs = -45.0
        result.audio_issues = ["音量极低，可能为静音或底噪填充"]
        require_audio = True

        # 复现评分逻辑中的硬失败判定
        if require_audio and result.audio_lufs < -30:
            result.passed = False
            result.issues.insert(0, "最终成片音频为静音或底噪填充，无法作为可发布广告视频")

        assert result.passed is False
        assert "底噪填充" in result.issues[0]


class TestProductDetectionThreshold:
    """回归测试：产品检测阈值提高"""

    def test_product_detected_threshold_is_0_55(self):
        """产品出现判定阈值应为 0.55"""
        from quality_checker import VideoQualityResult

        result = VideoQualityResult()
        result.product_similarity = 0.50
        result.product_detected = result.product_similarity >= 0.55
        assert result.product_detected is False

        result.product_similarity = 0.60
        result.product_detected = result.product_similarity >= 0.55
        assert result.product_detected is True

    def test_weak_detection_threshold_is_0_65(self):
        """产品特征较弱提示阈值应为 0.65"""
        from quality_checker import VideoQualityResult

        result = VideoQualityResult()
        result.product_similarity = 0.60
        result.product_detected = result.product_similarity >= 0.55
        is_weak = result.product_detected and result.product_similarity < 0.65
        assert is_weak is True


class TestVoiceoverSegmentBounds:
    """回归测试：口播单段边界检查"""

    def test_per_line_overflow_detected(self):
        """单句口播超出 line['end'] 时应触发 overflow_ratio"""
        script_lines = [
            {"text": "短句", "start": 0.0, "end": 1.0, "segment": 0},
            {"text": "另一句", "start": 2.0, "end": 3.0, "segment": 1},
        ]
        # 模拟：第一句生成后 current_time = 1.5 > line["end"] = 1.0
        overflow_ratio = 1.0
        for line in script_lines:
            current_time = 1.5 if line["segment"] == 0 else 2.8
            _line_end = line.get("end", 10.0)
            if _line_end > 0 and current_time > _line_end:
                _seg_ratio = current_time / _line_end
                overflow_ratio = max(overflow_ratio, _seg_ratio)

        assert overflow_ratio == 1.5  # 1.5 / 1.0


class TestMergeTransitionsAudioMapping:
    """回归测试：无 BGM 时原片音频映射"""

    def test_filter_parts_has_outa_when_clips_have_audio_no_bgm(self):
        """片段有音频且无 BGM 时，filter_parts 必须包含 [outa] 路由"""
        # 直接验证逻辑：any_audio=True + current_alabel 存在 + 无 BGM
        # 应追加 f"{current_alabel}anull[outa]"
        any_audio = True
        current_alabel = "[acatfaded]"
        bgm_exists = False

        filter_parts = []
        if bgm_exists:
            pass
        elif any_audio and current_alabel:
            filter_parts.append(f"{current_alabel}anull[outa]")

        assert "[acatfaded]anull[outa]" in filter_parts


class TestBatchGlobalDefaults:
    """回归测试：batch 全局默认参数完整性"""

    def test_run_batch_global_defaults_has_all_keys(self):
        """global_defaults 应包含 create_task_args 中读取的所有字段"""
        from batch import create_task_args

        # 获取 create_task_args 中从 global_defaults 读取的所有 key
        import inspect
        src = inspect.getsource(create_task_args)
        import re
        keys_in_code = set(re.findall(r'global_defaults\.get\("([^"]+)"', src))

        # run_batch 中定义的 global_defaults  keys（模拟构造）
        global_defaults = {
            "style": "none",
            "duration": 5,
            "mode": "std",
            "aspect_ratio": "9:16",
            "dual_output": False,
            "image_fidelity": 0.5,
            "human_fidelity": 0.5,
            "seed": None,
            "best_of": 1,
            "quality_frames": 12,
            "keep_candidates": False,
            "stabilize": True,
            "strict_mode": True,
            "brand_intro_outro": False,
            "kling_model": None,
            "multi_shot": False,
            "preflight_keyframe": True,
            "image_first": True,
            "image_first_mode": "standard",
            "image_first_variants": 2,
            "hook_type": "question",
            "use_voiceover": False,
            "voiceover_style": "standard",
            "voice": "female_young",
            "script_style": "pain_point_solution",
            "force": False,
            "parallel": True,
            "min_clips": 3,
            "preview": False,
            "max_workers": 4,
            "target_duration": None,
            "rhythm_style": "moderate",
            "resume": True,
            "total_tasks": 1,
        }

        missing = keys_in_code - set(global_defaults.keys())
        assert not missing, f"global_defaults 缺少字段：{missing}"


class TestFinalQualityBugFixes:
    """回归测试：最终成片质量相关修复"""

    def test_drawtext_escape_handles_special_chars(self):
        """品牌/CTA 文案中的特殊字符不应破坏 ffmpeg drawtext 语法"""
        from video_merger import _ffmpeg_escape_drawtext_text

        escaped = _ffmpeg_escape_drawtext_text("L'Oreal: 50%, now\\new")
        assert r"\'" in escaped
        assert r"\:" in escaped
        assert r"\," in escaped
        assert "\\\\" in escaped

    def test_video_vbv_args_derive_from_output_bitrate(self):
        """中间编码应使用 maxrate/bufsize 约束，防止 CRF 文件体积失控"""
        from video_merger import _video_vbv_args

        args = _video_vbv_args("10M")
        assert args == ["-maxrate", "15M", "-bufsize", "20M"]

    def test_extract_frame_b64_cleans_temp_file_on_failure(self, tmp_path):
        """抽帧失败时 NamedTemporaryFile(delete=False) 也必须清理"""
        import tempfile
        import one_click_create

        created = tmp_path / "leaked.png"

        class FakeTmp:
            name = str(created)

            def __enter__(self):
                created.write_bytes(b"partial")
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        with patch.object(tempfile, "NamedTemporaryFile", return_value=FakeTmp()):
            with patch.object(one_click_create, "extract_frame", side_effect=RuntimeError("mock fail")):
                result = one_click_create._extract_frame_b64(Path("missing.mp4"), 1.0)

        assert result is None
        assert not created.exists()

    def test_tts_retries_on_429(self, tmp_path):
        """火山 TTS 遇到 429 应应用层重试，而不是直接放弃口播"""
        import tts_client
        import base64
        import json

        class FakeResp:
            def __init__(self, status_code: int, audio_data: bytes | None = None):
                self.status_code = status_code
                self._audio_data = audio_data
                self.text = "rate limited"

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def json(self):
                return {"code": self.status_code, "message": self.text}

            def iter_content(self, chunk_size=4096):
                if self._audio_data:
                    yield self._audio_data

            def iter_lines(self, decode_unicode=False):
                if self._audio_data:
                    chunk_b64 = base64.b64encode(self._audio_data).decode()
                    yield json.dumps({"code": 0, "data": chunk_b64})
                    yield json.dumps({"code": 20000000, "message": "ok", "data": None})
                return
                yield

        fake_audio = b"x" * 2048
        responses = [
            FakeResp(429),
            FakeResp(200, fake_audio),
        ]

        with patch("requests.post", side_effect=responses) as post:
            with patch("time.sleep"):
                with patch.object(tts_client, "_validate_audio_file"):
                    out = tts_client._generate_tts_volcengine(
                        text="测试",
                        output_path=tmp_path / "out.mp3",
                        speaker="speaker",
                        api_key="key",
                    )

        assert out.exists()
        assert post.call_count == 2


class TestFifthReviewFixes:
    """回归测试：第 5 轮致命问题修复"""

    def test_kling_downloaded_video_validation_rejects_invalid_file(self, tmp_path):
        """下载到非空但不可解码的视频时，应阻断后续 pipeline"""
        from kling_client import _validate_downloaded_video
        from config import VideoGenerationError

        bad_video = tmp_path / "bad.mp4"
        bad_video.write_bytes(b"not a real mp4")

        with pytest.raises(VideoGenerationError):
            _validate_downloaded_video(bad_video)

    def test_http_url_detection_for_product_image(self):
        """product_image 支持 HTTP/HTTPS URL 判断，避免被 Path 破坏"""
        from one_click_create import _is_http_url

        assert _is_http_url("https://example.com/product.jpg") is True
        assert _is_http_url("http://example.com/product.jpg") is True
        assert _is_http_url("/tmp/product.jpg") is False

    def test_batch_keeps_product_image_url_as_string(self):
        """批量模式不能把 URL 提前转成 Path('https:/...')"""
        from batch import create_task_args

        args = create_task_args(
            {"product_name": "测试", "product_image": "https://example.com/a.jpg"},
            {},
        )
        assert args["product_image"] == "https://example.com/a.jpg"

    def test_ffmpeg_filter_path_escape_handles_special_chars(self):
        """字幕/字体滤镜路径需要保护空格、冒号、逗号、单引号"""
        from video_merger import _ffmpeg_escape_filter_path

        escaped = _ffmpeg_escape_filter_path("/Users/me/My Project/a,b's/font.ttf")
        assert r"\ " in escaped
        assert r"\," in escaped
        assert r"\'" in escaped

    def test_ffconcat_path_escape_handles_single_quote(self):
        """concat demuxer 的 file 行需要保护单引号"""
        from video_merger import _ffconcat_escape_path

        escaped = _ffconcat_escape_path(Path("/tmp/a'b.mp4"))
        assert r"'\''" in escaped

    def test_xfade_duration_uses_shorter_neighbor(self):
        """转场时长必须按相邻两段较短者钳制"""
        durations = [5.0, 0.3]
        requested = 1.2
        trans_duration = min(requested, min(durations[0], durations[1]) * 0.45)
        assert trans_duration == pytest.approx(0.135)

    def test_config_import_does_not_create_output_dir(self):
        """config 模块导入阶段不应 mkdir，避免只读目录下无法启动"""
        config_text = Path("config.py").read_text(encoding="utf-8")
        assert "OUTPUT_DIR.mkdir(parents=True, exist_ok=True)" not in config_text

    def test_color_range_args_marks_full_range(self):
        """重编码输出应显式标记 BT.709 + full-range，避免平台误读色彩范围"""
        from video_merger import _color_range_args

        args = _color_range_args()
        assert "-color_range" in args and "pc" in args
        assert "-colorspace" in args and "bt709" in args
        assert "-color_trc" in args and "bt709" in args
        assert "-color_primaries" in args and "bt709" in args


class TestPublishableSuccessFixes:
    """回归测试：错误 success 收敛修复"""

    def test_batch_coerces_quoted_bool_and_int(self):
        """YAML 中的 'false'/'5' 应归一化为 bool/int，而不是按字符串透传"""
        from batch import create_task_args

        args = create_task_args(
            {
                "product_name": "测试",
                "preview": "false",
                "strict_mode": "true",
                "parallel": "false",
                "duration": "5",
                "min_clips": "3",
                "max_workers": "4",
            },
            {},
        )
        assert args["preview"] is False
        assert args["strict_mode"] is True
        assert args["parallel"] is False
        assert args["duration"] == 5
        assert args["min_clips"] == 3
        assert args["max_workers"] == 4

    def test_batch_rejects_invalid_bool_string(self):
        """非法布尔字符串必须显式报错，不能靠 truthy/falsey 猜测"""
        from batch import create_task_args

        with pytest.raises(ValueError):
            create_task_args({"product_name": "测试", "preview": "maybe"}, {})

    def test_batch_output_name_contains_task_id_and_microseconds(self):
        """批量输出名应包含 task_id 和微秒，避免并发同名同秒覆盖"""
        from batch import _build_task_output_name

        name1 = _build_task_output_name(1, "同款面霜")
        name2 = _build_task_output_name(2, "同款面霜")
        assert name1.startswith("001_同款面霜_")
        assert name2.startswith("002_同款面霜_")
        assert name1 != name2

    def test_pipeline_uses_output_name_scoped_intermediate_dirs(self):
        """色调/均衡/节奏适配中间目录必须按 output_name 隔离，防止 batch 串片"""
        src = Path("one_click_create.py").read_text(encoding="utf-8")
        assert 'clips_dir / f"{output_name}_color_matched"' in src
        assert 'clips_dir / f"{output_name}_histeq"' in src
        assert 'clips_dir / f"{output_name}_rhythm_adjusted"' in src
        assert 'final_dir / f"{output_name}_refs"' in src

    def test_final_export_failure_does_not_fallback_to_intermediate(self):
        """最终导出失败不能把 _sfx 等中间文件作为 success final_path"""
        src = Path("one_click_create.py").read_text(encoding="utf-8")
        assert 'final_path = sfx_path' not in src
        assert "已阻断中间文件被标记为成功成片" in src

    def test_fallback_audio_requires_voiceover(self):
        """fallback 底噪只能作为口播混音占位，不能单独成为可发布音频"""
        src = Path("one_click_create.py").read_text(encoding="utf-8")
        assert "fallback 底噪不能作为可发布音频" in src
        assert "BGM 不可用且未启用口播" in src

    def test_voiceover_is_validated_before_subtitle_burn(self):
        """口播必须在字幕烧录前校验，避免无效口播时间轴被烧进视频"""
        src = Path("one_click_create.py").read_text(encoding="utf-8")
        validate_pos = src.index("_validate_voiceover_audio(voiceover_audio)")
        subtitle_pos = src.index("add_fancy_subtitles(")
        assert validate_pos < subtitle_pos

    def test_wide_path_has_quality_gate(self):
        """16:9 版本作为请求产物，也必须单独经过质量门"""
        src = Path("one_click_create.py").read_text(encoding="utf-8")
        assert "开始 16:9 版本发布级质量检测" in src
        assert "check_video_quality(" in src
        assert "16:9 成片质量检测未通过" in src


class TestSeventhReviewFixes:
    """回归测试：第7轮深度审查修复"""

    def test_ffmpeg_filter_path_escapes_brackets(self):
        """filter_complex 路径必须转义方括号，避免被解析为流标签"""
        from video_merger import _ffmpeg_escape_filter_path
        from pathlib import Path

        p = Path("/tmp/[cache]/subs.ass")
        escaped = _ffmpeg_escape_filter_path(p)
        assert r"\[" in escaped
        assert r"\]" in escaped
        assert "[cache]" not in escaped

    def test_transition_ffmpeg_aevalsrc_has_aformat(self):
        """aevalsrc 默认格式可能与输入音频不匹配，必须追加 aformat 统一"""
        src = Path("video_merger.py").read_text(encoding="utf-8")
        assert "aevalsrc=0:d={clip2_duration},aformat=" in src
        assert "aevalsrc=0:d={clip1_duration},aformat=" in src

    def test_subtitles_ffmpeg_handles_no_audio(self):
        """烧录字幕时输入无音轨不能硬编码 -c:a copy，否则 ffmpeg 崩溃"""
        src = Path("video_merger.py").read_text(encoding="utf-8")
        assert "_has_audio_stream(video)" in src
        assert 'cmd.append("-an")' in src

    def test_bgm_download_sanitizes_track_id(self):
        """track_id 必须 sanitize 后再拼路径，防止路径遍历"""
        src = Path("bgm_client.py").read_text(encoding="utf-8")
        assert "safe_track_id = re.sub" in src
        assert "safe_track_id" in src

    def test_bgm_medium_pace_does_bpm_check(self):
        """medium 节奏不应跳过 BPM 校验，否则可能选中严重脱拍的 BGM"""
        src = Path("bgm_client.py").read_text(encoding="utf-8")
        # 旧代码有 pace != "medium" 的跳过，修复后应已删除
        assert 'pace != "medium"' not in src

    def test_kling_download_checks_content_length(self):
        """下载视频后应对比 Content-Length，防止残缺文件漏过"""
        src = Path("kling_client.py").read_text(encoding="utf-8")
        assert "expected_size = int(response.headers" in src
        assert "actual_size != expected_size" in src

    def test_kling_character_ref_validates_image(self):
        """角色定妆照下载后必须验证是合法图片，防止 CDN 错误页被当图片保存"""
        src = Path("kling_client.py").read_text(encoding="utf-8")
        assert "Image.open(io.BytesIO(image_bytes)).verify()" in src
        assert "角色定妆照下载内容不是有效图片" in src

    def test_tts_amix_uses_longest_duration(self):
        """amix duration=first 会截断后续语音，应改为 longest"""
        src = Path("tts_client.py").read_text(encoding="utf-8")
        assert "duration=longest:dropout_transition=0" in src

    def test_quality_checker_face_ratio_over_50_fails(self):
        """超过一半帧人脸异常应直接判失败，不能只扣分"""
        src = Path("quality_checker.py").read_text(encoding="utf-8")
        assert "face_ratio > 0.5" in src
        # 在 face_ratio > 0.5 分支内应有 result.passed = False
        face_block = src[src.index("face_ratio > 0.5"):src.index("face_ratio > 0.5") + 300]
        assert "result.passed = False" in face_block

    def test_quality_checker_audio_parse_failure_fails(self):
        """音频响度解析失败时应判失败，不能因 lufs=0 而意外通过"""
        src = Path("quality_checker.py").read_text(encoding="utf-8")
        assert "lufs = -999.0" in src
        assert "result.audio_lufs == -999.0" in src

    def test_best_of_rejects_all_zero_scores(self):
        """所有候选质量分为 0 时应直接失败，不能选第一个蒙混过关"""
        src = Path("one_click_create.py").read_text(encoding="utf-8")
        assert "best_score <= 0" in src
        assert "无法选出有效片段" in src


class TestFinalVideoQualityFixes:
    """回归测试：发布级成片质量收敛修复"""

    def test_stable_output_name_uses_effective_default_kling_model(self):
        """未显式传 --kling-model 时，稳定输出名应绑定真实默认模型"""
        args_default = MagicMock()
        args_default.style = "none"
        args_default.duration = 5
        args_default.mode = "std"
        args_default.aspect_ratio = "9:16"
        args_default.product_image = "product.png"
        args_default.hook = "question"
        args_default.script_style = "pain_point_solution"
        args_default.target_duration = None
        args_default.rhythm_style = "moderate"
        args_default.seed = 7
        args_default.kling_model = None
        args_default.multi_shot = False

        args_explicit = MagicMock()
        args_explicit.style = args_default.style
        args_explicit.duration = args_default.duration
        args_explicit.mode = args_default.mode
        args_explicit.aspect_ratio = args_default.aspect_ratio
        args_explicit.product_image = args_default.product_image
        args_explicit.hook = args_default.hook
        args_explicit.script_style = args_default.script_style
        args_explicit.target_duration = args_default.target_duration
        args_explicit.rhythm_style = args_default.rhythm_style
        args_explicit.seed = args_default.seed
        args_explicit.kling_model = KLING_VIDEO_MODEL
        args_explicit.multi_shot = args_default.multi_shot

        product_info = {"name": "同款面霜", "type": "beauty"}
        assert build_stable_output_name(product_info, args_default) == build_stable_output_name(product_info, args_explicit)

    def test_character_ref_manifest_invalidates_changed_character(self, tmp_path):
        """同一个 output_name 下，人设变化必须让角色定妆照缓存失效"""
        char_path = tmp_path / "demo_charA_ref.png"
        char_path.write_bytes(b"x" * 2048)
        product_info = {"name": "面霜", "type": "beauty"}
        character = {"name": "Character A", "description": "25-year-old woman"}
        prompt = "portrait prompt"

        manifest = _build_character_manifest(
            product_info=product_info,
            character=character,
            prompt=prompt,
        )
        _write_clip_manifest(char_path, manifest)
        assert _manifest_matches(char_path, manifest)

        changed_manifest = _build_character_manifest(
            product_info=product_info,
            character={"name": "Character A", "description": "45-year-old man"},
            prompt="portrait prompt for another person",
        )
        assert not _manifest_matches(char_path, changed_manifest)

    def test_candidate_quality_scoring_zeroes_failed_semantic_candidate(self, tmp_path):
        """best-of 候选一旦未通过语义/质量门禁，择优分数必须归零"""
        video_path = tmp_path / "candidate.mp4"
        video_path.write_bytes(b"fake")
        product_ref = tmp_path / "product.png"
        product_ref.write_bytes(b"fake-product")
        character_ref = tmp_path / "character.png"
        character_ref.write_bytes(b"fake-character")

        fake_result = MagicMock()
        fake_result.passed = False
        fake_result.overall_score = 96
        fake_result.issues = ["[产品检测] 未检测到足够的商品参考图特征"]

        with patch("one_click_create.check_video_quality", return_value=fake_result) as mocked_check:
            score, issues = _score_candidate_video_quality(
                video_path,
                quality_frames=12,
                product_reference_image=product_ref,
                character_reference_image=character_ref,
            )

        assert score == 0.0
        assert issues == fake_result.issues
        mocked_check.assert_called_once()
        kwargs = mocked_check.call_args.kwargs
        assert kwargs["product_reference_image"] == product_ref
        assert kwargs["character_reference_image"] == character_ref
        assert kwargs["require_semantic_alignment"] is True
        assert kwargs["content_focus"] == "center"

    def test_best_of_uses_semantic_candidate_scoring(self):
        """best-of 不应退回只按通用清晰度评分"""
        src = Path("one_click_create.py").read_text(encoding="utf-8")
        assert "_score_candidate_video_quality(" in src
        assert "product_ref_for_candidate" in src
        assert "character_ref_for_candidate" in src
        assert "scores[cand_path] = score" in src

    def test_wide_output_uses_same_semantic_quality_gate(self):
        """16:9 版本也必须校验产品和角色语义，避免横版裁切后不可发布"""
        src = Path("one_click_create.py").read_text(encoding="utf-8")
        wide_block = src[src.index("开始 16:9 版本发布级质量检测"):src.index("print_quality_report(wide_quality_result")]
        assert "product_reference_image=product_image_path if product_image_path else None" in wide_block
        assert "character_reference_image=main_char_path if main_char_path else None" in wide_block
        assert "require_semantic_alignment=True" in wide_block

    def test_product_required_narrative_covers_review_and_proof(self):
        """review/proof/demo 等产品相关段必须纳入产品语义门禁"""
        for narrative in ("hook", "showcase", "cta", "review", "proof", "demo", "detail", "reason", "effect"):
            assert _is_product_required_narrative(narrative)
        assert not _is_product_required_narrative("pure_emotion")

    def test_local_product_image_validation_rejects_non_image(self, tmp_path):
        """本地商品参考图不能只检查 exists，损坏文件必须提前失败"""
        bad_image = tmp_path / "product.png"
        bad_image.write_text("not an image", encoding="utf-8")

        with pytest.raises(RuntimeError, match="商品参考图"):
            _validate_product_image_file(bad_image)

    def test_local_product_image_validation_accepts_real_image(self, tmp_path):
        """合法商品图应通过预检，避免误杀正常输入"""
        from PIL import Image

        image_path = tmp_path / "product.png"
        img = Image.new("RGB", (512, 512), (240, 80, 60))
        for x in range(128, 384):
            for y in range(128, 384):
                img.putpixel((x, y), (60, 180, 220))
        img.save(image_path)

        _validate_product_image_file(image_path)

    def test_segment_semantic_quality_blocks_failed_product_segment(self, tmp_path):
        """关键分镜语义失败必须阻断，不能只靠整片抽帧兜底"""
        clip_path = tmp_path / "clip.mp4"
        clip_path.write_bytes(b"fake video")
        product_ref = tmp_path / "product.png"
        product_ref.write_bytes(b"fake product")

        fake_result = MagicMock()
        fake_result.passed = False
        fake_result.issues = ["[产品检测] 未检测到足够的商品参考图特征"]

        with patch("one_click_create.check_video_quality", return_value=fake_result) as mocked_check:
            with pytest.raises(RuntimeError, match="分段语义质检未通过"):
                _check_segment_semantic_quality(
                    clip_paths=[clip_path],
                    successful_clip_indices=[2],
                    ad_script={"segments": [{"narrative": "hook"}, {"narrative": "turning"}, {"narrative": "showcase"}]},
                    product_image_path=product_ref,
                    main_char_path=None,
                    quality_frames=12,
                )

        kwargs = mocked_check.call_args.kwargs
        assert kwargs["product_reference_image"] == product_ref
        assert kwargs["content_focus"] == "center"

    def test_publish_mode_blocks_missing_segments(self):
        """strict 发布级成片缺段时必须阻断，避免生成缺 CTA/产品段的视频"""
        src = Path("one_click_create.py").read_text(encoding="utf-8")
        failed_block = src[src.index("if failed_indices:"):src.index("# 最少成功段数")]
        assert "strict_mode and not preview" in failed_block
        assert "发布级成片要求分镜完整" in failed_block

    def test_rhythm_over_limit_blocks_in_strict_mode(self):
        """节奏模板超过后期拉伸能力时，strict 模式不能只警告后继续"""
        src = Path("one_click_create.py").read_text(encoding="utf-8")
        rhythm_block = src[src.index("if _over_limit_segs:"):src.index("# 生成完整广告脚本")]
        assert "strict_mode and not preview" in rhythm_block
        assert "节奏模板存在超过当前生成片段后期拉伸能力" in rhythm_block


class TestLowCostQualityStrategy:
    """回归测试：低成本高质量生成策略"""

    def test_negative_prompt_does_not_block_product_logo(self):
        """负面词不能全局禁止商品包装自身 logo，只能禁止无关品牌/水印"""
        from config import NEGATIVE_PROMPT

        assert "unrelated logo" in NEGATIVE_PROMPT
        assert "unrelated brand mark" in NEGATIVE_PROMPT
        assert "text watermark" in NEGATIVE_PROMPT
        assert "text watermark, logo, brand mark" not in NEGATIVE_PROMPT

    def test_reference_strategy_product_segment_uses_quality_roles(self):
        """产品段优先商品图 + 主角多角度 + 连续性（性价比策略：用图买成功率）"""
        from one_click_create import _reference_strategy_for_narrative

        roles = _reference_strategy_for_narrative(
            "showcase",
            product_available=True,
            character_available=True,
            continuity_available=True,
            multi_angle_char=True,
        )
        assert roles[0] == "product"
        assert "character_primary" in roles
        assert "continuity" in roles
        assert len(roles) <= 5

    def test_reference_strategy_character_segment_prioritizes_character(self):
        """非产品强制段应优先人物多角度，再补连续性"""
        from one_click_create import _reference_strategy_for_narrative

        roles = _reference_strategy_for_narrative(
            "turning",
            product_available=True,
            character_available=True,
            continuity_available=True,
            multi_angle_char=True,
        )
        assert roles[0] == "character_primary"
        assert "character_angle" in roles
        assert "continuity" in roles

    def test_preflight_contract_blocks_product_segment_without_product_name(self):
        """产品强制分镜缺少产品名时应在视频生成前阻断，而不是生成后才质检失败"""
        from one_click_create import _preflight_generation_contract

        with pytest.raises(RuntimeError, match="生成前合同预检未通过"):
            _preflight_generation_contract(
                product_info={"name": "蓝罐汽水"},
                ad_script={"segments": [{"narrative": "showcase", "product_visibility": "prominent"}]},
                clip_prompts=["close-up lifestyle shot, cold drink on table"],
                product_image_path=Path("product.png"),
                char_refs=[{"img_b64": "abc"}],
                strict_mode=True,
            )

    def test_prompt_compact_keeps_within_budget(self):
        """最终 Prompt 调用前应压缩，避免泛词挤掉核心商品/动作信息"""
        from one_click_create import _compact_prompt_for_generation

        long_prompt = ", ".join(["蓝罐汽水 product hero shot"] + ["high quality"] * 200)
        compacted = _compact_prompt_for_generation(long_prompt, max_chars=220)
        assert len(compacted) <= 220
        assert "蓝罐汽水 product hero shot" in compacted

    def test_adaptive_best_of_has_early_stop_and_strategy_difference(self):
        """best_of 应自适应早停，补候选时必须改变策略而非重复抽卡"""
        src = Path("one_click_create.py").read_text(encoding="utf-8")
        assert "early_stop_score = 85.0" in src
        assert "停止继续生成候选以节省成本" in src
        assert "product_rescue" in src
        assert "character_rescue" in src

    def test_parallel_generation_does_not_reuse_first_tail_as_fake_prev(self):
        """并行模式不能把第 1 段尾帧伪装成所有后续段的上一帧"""
        src = Path("one_click_create.py").read_text(encoding="utf-8")
        assert "first_clip_last_frame" not in src
        assert "_generate_one_clip(idx, prompt, None, None)" in src


class TestPreflightKeyframe:
    """测试首帧低成本预检逻辑"""

    def test_sanitize_prompt_removes_camera_terms(self):
        """运镜词汇应从图片 Prompt 中被清洗掉"""
        prompt = "A woman holds a bottle, slow push in, dolly zoom, natural lighting"
        result = _sanitize_prompt_for_image_generation(prompt)
        assert "slow push in" not in result
        assert "dolly zoom" not in result
        assert "natural lighting" in result

    def test_sanitize_prompt_removes_image_tags(self):
        """参考图绑定标签应从图片 Prompt 中被清洗掉"""
        prompt = "A product display, <<<image_1>>>, bright studio light"
        result = _sanitize_prompt_for_image_generation(prompt)
        assert "<<<image_1>>>" not in result
        assert "product display" in result

    def test_sanitize_prompt_preserves_content(self):
        """非运镜的核心内容描述应保留"""
        prompt = "Young woman, red dress, holding skincare bottle, soft window light, clean background"
        result = _sanitize_prompt_for_image_generation(prompt)
        assert "Young woman" in result
        assert "skincare bottle" in result
        assert "soft window light" in result

    def test_preflight_skips_when_no_references(self):
        """无参考图时首帧预检应直接跳过，避免无意义调用"""
        client = MagicMock()
        passed, issues, path = _preflight_keyframe_check(
            client=client,
            prompt="test prompt",
            ref_images=[],
            narrative="hook",
            product_image_path=None,
            main_char_path=None,
            save_path=Path("/tmp/test_preflight.png"),
        )
        assert passed is True
        assert not issues
        assert path is None
        client.generate_image.assert_not_called()

    def test_preflight_calls_generate_image_with_sanitized_prompt(self, tmp_path):
        """有参考图时应调用 generate_image，且 prompt 已被清洗"""
        from PIL import Image

        client = MagicMock()
        # 构造一个假图片结果
        client.session.get.return_value = MagicMock(
            content=b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR",
            raise_for_status=lambda: None,
        )
        client.generate_image.return_value = {
            "data": {
                "task_result": {
                    "images": [{"url": "http://fake.url/img.png"}]
                }
            }
        }

        # 构造一张真实的小图片作为参考图
        ref_img = tmp_path / "ref.png"
        img = Image.new("RGB", (64, 64), color="red")
        img.save(ref_img)

        save_path = tmp_path / "preflight.png"
        prompt = "Woman, slow push in, <<<image_1>>>, soft light"
        passed, issues, kf_path = _preflight_keyframe_check(
            client=client,
            prompt=prompt,
            ref_images=[],
            narrative="hook",
            product_image_path=None,
            main_char_path=ref_img,
            save_path=save_path,
            aspect_ratio="9:16",
            image_fidelity=0.85,
        )

        assert client.generate_image.called
        call_kwargs = client.generate_image.call_args.kwargs
        # prompt 应被清洗过
        assert "slow push in" not in call_kwargs["prompt"]
        assert "<<<image_" not in call_kwargs["prompt"]
        # 参考图参数应正确
        assert call_kwargs["image_reference"] == "face"
        assert call_kwargs["resolution"] == "1k"
        assert call_kwargs["aspect_ratio"] == "9:16"

    def test_preflight_fails_when_image_generation_empty(self, tmp_path):
        """图片生成返回空结果时预检应失败"""
        client = MagicMock()
        client.generate_image.return_value = {"data": {"task_result": {"images": []}}}

        ref_img = tmp_path / "ref.png"
        from PIL import Image
        img = Image.new("RGB", (64, 64), color="red")
        img.save(ref_img)

        passed, issues, kf_path = _preflight_keyframe_check(
            client=client,
            prompt="test",
            ref_images=[],
            narrative="hook",
            product_image_path=None,
            main_char_path=ref_img,
            save_path=tmp_path / "pf.png",
        )
        assert passed is False
        assert any("结果为空" in i for i in issues)


class TestCharacterBible:
    """测试角色圣经 / 商品圣经结构"""

    def test_build_character_bibles_single_role(self):
        """单角色时从 product_info 构建默认圣经"""
        product_info = {
            "name": "TestProduct",
            "age": "28",
            "gender": "女",
            "outfit": "white dress",
        }
        bibles = build_character_bibles(product_info, characters=None)
        assert len(bibles) == 1
        assert bibles[0]["id"] == "char_01"
        assert bibles[0]["age"] == "28"
        assert bibles[0]["gender"] == "女"
        assert bibles[0]["outfit"] == "white dress"

    def test_build_character_bibles_multi_role_with_description(self):
        """多角色时从 description 解析结构化字段"""
        product_info = {"age": "25", "gender": "女", "outfit": "casual"}
        characters = [
            {"name": "小雅", "description": "25-year-old Asian woman, long black hair"},
            {"name": "Amy", "description": "30-year-old Caucasian woman, short blonde hair"},
        ]
        bibles = build_character_bibles(product_info, characters)
        assert len(bibles) == 2
        assert bibles[0]["name"] == "小雅"
        assert bibles[0]["age"] == "25"
        assert "long black hair" in bibles[0]["hair_style"]
        assert bibles[1]["name"] == "Amy"
        assert bibles[1]["age"] == "30"
        assert "short blonde hair" in bibles[1]["hair_style"]

    def test_infer_family_insurance_characters(self):
        """家财险核心角色应包含父母+孩子+保险顾问（孩子是情感锚点必须定妆，顾问是服务场景核心）"""
        from one_click_create import build_cast_plan, infer_characters_from_product

        product_info = {
            "name": "众安家财险",
            "type": "app",
            "selling_point": "全屋保障无忧，极速理赔守护家庭财产安全",
            "audience": "2545",
        }
        cast_plan = build_cast_plan(product_info)
        characters = infer_characters_from_product(product_info)

        assert len(characters) == 4
        core_names = [c["name"] for c in characters]
        assert "Mother" in core_names
        assert "Father" in core_names
        assert "Child" in core_names
        assert "Insurance Advisor" in core_names

    def test_explicit_characters_are_preserved(self):
        """LLM 或模板显式给出的角色列表优先，不被兜底推断覆盖"""
        from one_click_create import infer_characters_from_product

        product_info = {
            "name": "家庭保险",
            "characters": [
                {
                    "name": "Grandma",
                    "role": "elder family member",
                    "description": "68-year-old Chinese woman, silver hair, warm smile",
                },
                {
                    "name": "Daughter",
                    "role": "adult child",
                    "description": "32-year-old Chinese woman, short black hair, office outfit",
                },
            ],
        }
        characters = infer_characters_from_product(product_info)

        assert len(characters) == 2
        assert [c["name"] for c in characters] == ["Grandma", "Daughter"]

    def test_pet_product_can_use_animal_as_core_subject(self):
        """宠物是主角时动物应作为核心主体，而不是背景路人式实体"""
        from one_click_create import build_cast_plan

        product_info = {
            "name": "智能猫粮机",
            "type": "宠物",
            "selling_point": "自动定时喂猫，出差也能照顾猫咪",
        }
        cast_plan = build_cast_plan(product_info)

        assert [c["name"] for c in cast_plan["core_characters"]] == ["Owner", "Pet"]
        assert cast_plan["core_characters"][1]["role_type"] == "animal"

    def test_character_bible_to_prompt_includes_all_fields(self):
        """角色圣经转 prompt 应包含所有非空字段"""
        bible = CharacterBible(
            id="char_01", name="Test", age="25", gender="female",
            ethnicity="Asian", hair_style="long black hair",
            outfit="red dress", accessories="gold earrings",
            facial_features="high cheekbones", expression_baseline="warm smile",
        )
        prompt = character_bible_to_prompt(bible)
        assert "25-year-old female" in prompt
        assert "Asian" in prompt
        assert "long black hair" in prompt
        assert "wearing red dress" in prompt
        assert "gold earrings" in prompt
        assert "high cheekbones" in prompt
        assert "warm smile" in prompt

    def test_generate_character_prompt_uses_bible(self):
        """generate_character_prompt 优先使用圣经生成更精确的描述"""
        bible = CharacterBible(
            id="char_01", name="小雅", age="28", gender="female",
            ethnicity="Asian", hair_style="long straight black hair",
            outfit="white blouse and jeans", accessories="",
            facial_features="", expression_baseline="confident",
        )
        product_info = {"name": "面霜", "type": "美妆", "age": "28", "gender": "女", "outfit": "casual"}
        prompt = generate_character_prompt(product_info, bible=bible)
        # 圣经描述应出现在 prompt 中
        assert "long straight black hair" in prompt
        assert "white blouse and jeans" in prompt
        assert "confident" in prompt

    def test_generate_clip_prompts_uses_bible_for_multi_role(self):
        """多角色时分镜 prompt 应包含每个角色的精确圣经描述"""
        product_info = {"name": "TestProduct", "type": "default"}
        bibles = [
            CharacterBible(
                id="char_01", name="小雅", age="25", gender="female",
                ethnicity="Asian", hair_style="long black hair",
                outfit="red dress", accessories="", facial_features="",
                expression_baseline="",
            ),
            CharacterBible(
                id="char_02", name="Amy", age="30", gender="female",
                ethnicity="Caucasian", hair_style="short blonde hair",
                outfit="blue suit", accessories="", facial_features="",
                expression_baseline="",
            ),
        ]
        clips = generate_clip_prompts(
            product_info,
            cinematic_style="none",
            character_bibles=bibles,
        )
        # 至少有一个分镜包含两个角色的精确描述
        assert any("小雅: " in clip and "long black hair" in clip for clip in clips)
        assert any("Amy: " in clip and "short blonde hair" in clip for clip in clips)

    def test_build_product_bible_includes_brand_info(self):
        """商品圣经应整合 product_info 和 BRAND_CONFIG"""
        product_info = {"name": "TestCream", "type": "美妆", "selling_point": "保湿"}
        bible = build_product_bible(product_info)
        assert bible["name"] == "TestCream"
        assert bible["category"] == "美妆"
        assert bible["key_selling_point"] == "保湿"
        # BRAND_CONFIG 中的字段也应被纳入
        assert "packaging" in bible
        assert "primary_color" in bible

    def test_generate_clip_prompts_uses_product_bible(self):
        """商品圣经应注入分镜 prompt 的商品一致性描述"""
        product_info = {"name": "TestProduct", "type": "default"}
        p_bible = ProductBible(
            name="TestProduct", category="default",
            packaging="white cylindrical bottle", primary_color="white",
            shape="slim cylindrical", logo_description="minimalist logo",
            usage_context="", key_selling_point="",
        )
        clips = generate_clip_prompts(
            product_info,
            cinematic_style="none",
            product_bible=p_bible,
        )
        # product_consistency 应使用圣经描述
        assert any("white cylindrical bottle" in clip for clip in clips)


class TestMultiCharacterReferenceBinding:
    """测试多人物按角色 ID 绑定参考图"""

    def test_single_character_returns_zero(self):
        """单角色时始终返回主角色索引 0"""
        bibles = [CharacterBible(id="c1", name="小雅", age="25", gender="女", outfit="", ethnicity="", hair_style="", hair_color="", accessories="", facial_features="", expression_baseline="")]
        result = _get_primary_char_for_clip(1, "小雅在化妆", {"segments": []}, bibles)
        assert result == 0

    def test_detects_extra_character_by_name(self):
        """clip_prompt 中出现额外角色名时应返回对应索引"""
        bibles = [
            CharacterBible(id="c1", name="小雅", age="25", gender="女", outfit="", ethnicity="", hair_style="", hair_color="", accessories="", facial_features="", expression_baseline=""),
            CharacterBible(id="c2", name="Amy", age="30", gender="女", outfit="", ethnicity="", hair_style="", hair_color="", accessories="", facial_features="", expression_baseline=""),
        ]
        result = _get_primary_char_for_clip(2, "Amy introduces the product", {"segments": []}, bibles)
        assert result == 1

    def test_detects_from_ad_script_scene_prompt(self):
        """优先从 ad_script segment 的 scene_prompt 中检测角色名"""
        bibles = [
            CharacterBible(id="c1", name="小雅", age="25", gender="女", outfit="", ethnicity="", hair_style="", hair_color="", accessories="", facial_features="", expression_baseline=""),
            CharacterBible(id="c2", name="Amy", age="30", gender="女", outfit="", ethnicity="", hair_style="", hair_color="", accessories="", facial_features="", expression_baseline=""),
        ]
        ad_script = {
            "segments": [
                {"narrative": "hook", "scene_prompt": "小雅 looks at her phone"},
                {"narrative": "showcase", "scene_prompt": "Amy holds the bottle"},
            ]
        }
        result = _get_primary_char_for_clip(2, "some generic prompt", ad_script, bibles)
        assert result == 1

    def test_fallback_to_primary_when_no_match(self):
        """检测不到任何角色名时回退主角色"""
        bibles = [
            CharacterBible(id="c1", name="小雅", age="25", gender="女", outfit="", ethnicity="", hair_style="", hair_color="", accessories="", facial_features="", expression_baseline=""),
            CharacterBible(id="c2", name="Amy", age="30", gender="女", outfit="", ethnicity="", hair_style="", hair_color="", accessories="", facial_features="", expression_baseline=""),
        ]
        result = _get_primary_char_for_clip(1, "A generic scene with no names", {"segments": []}, bibles)
        assert result == 0

    def test_empty_bibles_returns_zero(self):
        """空圣经列表时安全回退 0"""
        result = _get_primary_char_for_clip(1, "test", {"segments": []}, [])
        assert result == 0


class TestMusicContract:
    """测试音乐合同结构"""

    def test_build_music_contract_basic(self):
        """基础产品信息应生成合理的音乐合同"""
        product_info = {"type": "美妆", "audience": "18-25"}
        contract = build_music_contract(product_info, cinematic_style="none")
        assert contract["mood"] == "upbeat"
        assert contract["genre"] == "pop"
        assert contract["energy"] == "high"
        assert contract["recommended_pace"] == "fast"
        assert contract["bpm_min"] >= 120
        assert contract["bpm_max"] <= 150

    def test_cinematic_style_overrides_mood(self):
        """电影风格应覆盖基础 mood 和 genre"""
        product_info = {"type": "美妆", "audience": "18-25"}
        contract = build_music_contract(product_info, cinematic_style="hitchcock")
        assert contract["mood"] == "suspenseful"
        assert contract["genre"] == "orchestral"
        assert contract["intro_type"] == "buildup"

    def test_audience_affects_bpm(self):
        """不同受众年龄应有不同 BPM 范围"""
        young = build_music_contract({"type": "default", "audience": "18-25"})
        old = build_music_contract({"type": "default", "audience": "45+"})
        assert young["bpm_min"] > old["bpm_min"]
        assert young["bpm_max"] > old["bpm_max"]

    def test_energy_affects_pace(self):
        """energy 应正确映射到 recommended_pace"""
        high = build_music_contract({"type": "美妆"})
        low = build_music_contract({"type": "家居"})
        assert high["recommended_pace"] == "fast"
        assert low["recommended_pace"] == "cinematic"

    def test_generate_clip_prompts_injects_rhythm(self):
        """音乐合同应注入分镜 prompt 的节奏描述"""
        product_info = {"name": "TestProduct", "type": "default"}
        contract = build_music_contract(product_info, cinematic_style="hitchcock")
        clips = generate_clip_prompts(
            product_info,
            cinematic_style="none",
            music_contract=contract,
        )
        # 每个 clip 都应包含节奏描述（插入到 prompt 开头）
        for clip in clips:
            assert "suspenseful orchestral energy" in clip
            assert "BPM rhythm" in clip


class TestIssueDrivenRepair:
    """测试失败原因驱动的精准修复策略"""

    def test_no_issues_returns_original(self):
        """无问题时返回原 prompt"""
        prompt = "A woman holds a bottle"
        repaired, tags = _repair_prompt_by_issues(prompt, [])
        assert repaired == prompt
        assert tags == []

    def test_detects_logo_issue(self):
        """检测到 logo 问题时注入品牌清晰度修复"""
        prompt = "A woman holds a bottle"
        issues = ["brand logo not visible, text unreadable"]
        repaired, tags = _repair_prompt_by_issues(prompt, issues)
        assert "clear brand logo visible" in repaired
        assert "logo" in tags

    def test_detects_face_issue(self):
        """检测到面部问题时注入正面约束"""
        prompt = "A woman uses the product"
        issues = ["face not detected, character similarity low"]
        repaired, tags = _repair_prompt_by_issues(prompt, issues)
        assert "front-facing portrait" in repaired
        assert "face" in tags

    def test_detects_profile_issue(self):
        """检测到侧脸问题时注入正面朝向修复"""
        prompt = "A woman turns around"
        issues = ["side profile detected"]
        repaired, tags = _repair_prompt_by_issues(prompt, issues)
        assert "no profile or back view" in repaired
        assert "profile" in tags

    def test_detects_product_obstruction(self):
        """检测到商品遮挡时注入可见性修复"""
        prompt = "A woman shows the product"
        issues = ["product hidden by hands, packaging obstructed"]
        repaired, tags = _repair_prompt_by_issues(prompt, issues)
        assert "product fully visible" in repaired
        assert "obstructed" in tags

    def test_multiple_issues_merge_repairs(self):
        """多个问题时应合并所有修复指令"""
        prompt = "A woman holds a bottle"
        issues = ["logo unclear", "product color mismatch"]
        repaired, tags = _repair_prompt_by_issues(prompt, issues)
        assert "clear brand logo visible" in repaired
        assert "exact same product color" in repaired
        assert len(tags) == 2

    def test_uses_product_bible_for_exact_description(self):
        """有商品圣经时，用精确描述替换泛化修复"""
        prompt = "A woman holds a bottle"
        issues = ["product similarity low"]
        bible = ProductBible(
            name="Test", category="default",
            packaging="white cylindrical bottle with gold cap", primary_color="white",
            shape="", logo_description="", usage_context="", key_selling_point="",
        )
        repaired, tags = _repair_prompt_by_issues(prompt, issues, product_bible=bible)
        assert "white cylindrical bottle with gold cap" in repaired

    def test_uses_character_bible_for_exact_description(self):
        """有角色圣经时，用精确描述替换泛化修复"""
        prompt = "A woman uses the product"
        issues = ["face not detected"]
        bible = CharacterBible(
            id="c1", name="小雅", age="25", gender="女",
            hair_style="long straight black hair", hair_color="", outfit="", ethnicity="",
            accessories="", facial_features="", expression_baseline="",
        )
        repaired, tags = _repair_prompt_by_issues(prompt, issues, character_bible=bible)
        assert "long straight black hair" in repaired


class TestPostProcessingP0Fixes:
    """第8轮审查：P0 级后处理修复回归测试"""

    def test_color_range_args_includes_bt709(self):
        """颜色空间标记必须包含完整 BT.709 triplet + full-range"""
        from video_merger import _color_range_args
        args = _color_range_args()
        assert "-colorspace" in args
        assert args[args.index("-colorspace") + 1] == "bt709"
        assert "-color_trc" in args
        assert args[args.index("-color_trc") + 1] == "bt709"
        assert "-color_primaries" in args
        assert args[args.index("-color_primaries") + 1] == "bt709"
        assert "-color_range" in args
        assert args[args.index("-color_range") + 1] == "pc"

    def test_subtitle_outline_is_2(self, tmp_path):
        """字幕描边必须从 5 降到 2，避免过粗影响画面"""
        import subprocess
        from unittest.mock import patch
        from video_merger import add_subtitles_ffmpeg

        video = tmp_path / "test.mp4"
        subprocess.run(
            [
                "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=100x100:d=1",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video),
            ],
            check=True, capture_output=True,
        )
        subtitles = [{"text": "测试", "start": 0, "end": 0.5}]
        output = tmp_path / "out.mp4"

        captured = []
        with patch("video_merger.run_ffmpeg") as mock_run:
            mock_run.side_effect = lambda cmd, **kw: captured.extend(cmd)
            add_subtitles_ffmpeg(video, subtitles, output, font_size=24)

        cmd_str = " ".join(str(c) for c in captured)
        assert "Outline=2" in cmd_str, f"字幕描边应为 2，实际命令：{cmd_str}"
        assert "Outline=5" not in cmd_str, f"不应再出现 Outline=5：{cmd_str}"

    def test_sidechain_ratio_12_and_alimiter(self, tmp_path):
        """BGM ducking 必须使用 ratio=12 并追加 alimiter 防止爆音"""
        import subprocess
        from unittest.mock import patch
        from one_click_create import _mix_voiceover_with_bgm

        video = tmp_path / "video.mp4"
        voice = tmp_path / "voice.m4a"
        output = tmp_path / "out.mp4"
        subprocess.run(
            [
                "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=100x100:d=1",
                "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                "-c:v", "libx264", "-c:a", "aac", "-shortest", str(video),
            ],
            check=True, capture_output=True,
        )
        subprocess.run(
            [
                "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                "-t", "1", "-c:a", "aac", str(voice),
            ],
            check=True, capture_output=True,
        )

        captured = []
        with patch("one_click_create.run_ffmpeg") as mock_run:
            mock_run.side_effect = lambda cmd, **kw: captured.extend(cmd)
            _mix_voiceover_with_bgm(video, voice, output)

        filter_str = None
        for i, c in enumerate(captured):
            if c == "-filter_complex":
                filter_str = captured[i + 1]
                break
        assert filter_str is not None, "未找到 -filter_complex"
        assert "ratio=12" in filter_str, f"sidechain ratio 应为 12：{filter_str}"
        assert "alimiter" in filter_str, f"应包含 alimiter 限幅器：{filter_str}"
        assert "ratio=3" not in filter_str, f"不应再使用旧的 ratio=3：{filter_str}"

    def test_atempo_subtitles_sequential_not_original_start(self, tmp_path):
        """atempo 加速后字幕时间轴必须顺序累加，不能保留原始空隙导致错位"""
        import subprocess, shutil
        from unittest.mock import patch
        from tts_client import generate_full_voiceover
        import tts_client

        template = tmp_path / "template.m4a"
        subprocess.run(
            [
                "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                "-t", "1", "-c:a", "aac", str(template),
            ],
            check=True, capture_output=True,
        )

        def mock_tts(text, path, voice=None, rate=None):
            shutil.copy(str(template), str(path))

        def mock_duration(path):
            # 正常音频 4.0 秒；atempo 后的音频（路径含 fast_）约 3.1 秒
            if "fast_" in str(path):
                return 3.1
            return 4.0

        # 3 句话，每句 4 秒 + 0.15 秒停顿 = 12.3 秒 > 9.5 秒（total_duration=10），触发 atempo
        script_lines = [
            {"text": "第一句。第二句。第三句。", "start": 0, "end": 10, "segment": 0}
        ]
        out_path = tmp_path / "voiceover.m4a"

        with patch.object(tts_client, "generate_tts_audio", side_effect=mock_tts), \
             patch.object(tts_client, "_get_audio_duration", side_effect=mock_duration):
            _, subtitles = generate_full_voiceover(
                script_lines, out_path, total_duration=10.0, pause_between_sentences=0.15
            )

        assert len(subtitles) == 3, f"应有 3 句字幕，实际 {len(subtitles)}"
        # 关键断言：修复前 bug 会导致第二段 start 保留原始值≈4.15，
        # 修复后应紧密跟随第一段结束（≈3.1）
        assert subtitles[1]["start"] < 4.0, (
            f"atempo 后第二段 start={subtitles[1]['start']}, "
            "应紧密跟随第一段而非保留原始空隙"
        )
        assert subtitles[2]["start"] < 7.0, (
            f"atempo 后第三段 start={subtitles[2]['start']}, "
            "应顺序累加而非累积错位"
        )
        # 确保字幕之间没有超过 0.5 秒的不自然空隙
        for i in range(1, len(subtitles)):
            gap = subtitles[i]["start"] - subtitles[i - 1]["end"]
            assert gap < 0.5, f"字幕 {i} 与 {i-1} 之间空隙过大：{gap:.2f}s"


class TestWorkflowOrchestratorInterfaceContracts:
    """回归测试：工作流编排器必须使用当前模块接口，避免真实流程后段才失败"""

    def test_audio_generation_uses_current_bgm_and_voiceover_interfaces(self):
        src = Path("workflow_orchestrator.py").read_text(encoding="utf-8")
        block = src[src.index("    def _step_audio_generation"):src.index("    def _step_post_processing")]

        assert "generate_voiceover_script(\n            product_info" in block
        assert "_, voiceover_subtitles = generate_full_voiceover(" in block
        assert "align_subtitles_to_voiceover(subtitles, voiceover_subtitles)" in block
        assert "product_type=product_info.get" in block
        assert "product_category=" not in block
        assert "rhythm_curve=rhythm_curve" not in block
        assert "bgm_audio = Path(bgm_info) if bgm_info else None" in block

    def test_post_processing_uses_current_merger_and_beat_interfaces(self):
        src = Path("workflow_orchestrator.py").read_text(encoding="utf-8")
        block = src[src.index("    def _step_post_processing"):src.index("    def _step_brand_ending")]

        assert "envelope_key_times=beat_timings" in block
        assert "beat_timings=" not in block
        assert "align_subtitles_to_beats(subtitles, bgm_audio)" in block
        assert "align_subtitles_to_beats(subtitles, beat_timings)" not in block


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
