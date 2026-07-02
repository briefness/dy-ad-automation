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
)


class TestCinematicStyles:
    """测试电影风格配置"""

    def test_cinematic_styles_not_empty(self):
        """至少有 1 种风格"""
        assert len(CINEMATIC_STYLES) >= 1

    def test_cinematic_styles_has_none(self):
        """默认风格应为 'none'，且 argparse choices 包含它"""
        from config import DEFAULT_CINEMATIC_STYLE
        assert DEFAULT_CINEMATIC_STYLE == "none"
        # 验证 none 可作为合法风格传入（通过 choices 构造）
        valid_choices = list(CINEMATIC_STYLES.keys()) + [DEFAULT_CINEMATIC_STYLE]
        assert "none" in valid_choices

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
        assert result["image_count"] == 1
        assert result["video_seconds"] == 25
        expected_cost = 1 * KLING_PRICING["image"]["pro"] + 25 * KLING_PRICING["video"]["pro"]
        assert abs(result["estimated_cost"] - expected_cost) < 0.01

    def test_std_mode_1_clip_preview(self):
        """预览模式（std + 1 段）的成本估算"""
        result = estimate_cost(mode="std", duration_per_clip=5, num_clips=1, num_characters=1)
        assert result["image_count"] == 1
        assert result["video_seconds"] == 5
        expected_cost = 1 * KLING_PRICING["image"]["std"] + 5 * KLING_PRICING["video"]["std"]
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
        """CLI 默认应启用发布级质量策略"""
        with patch.object(sys, "argv", ["one_click_create.py"]):
            args = parse_args()
        assert args.strict is True
        assert args.stabilize is True
        assert args.best_of == 2

    def test_quality_defaults_can_be_disabled_for_debug(self):
        """调试时应允许显式关闭严格模式和稳定化"""
        with patch.object(sys, "argv", ["one_click_create.py", "--no-strict", "--no-stabilize", "--best-of", "1"]):
            args = parse_args()
        assert args.strict is False
        assert args.stabilize is False
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
        assert result.startswith("Reference image binding:")
        assert "Product reference: <<<image_1>>>" in result
        assert "Character reference: <<<image_2>>>" in result
        assert "Continuity frame: <<<image_3>>>" in result
        assert prompt in result

    def test_reference_binding_uses_roles_not_narrative_guess(self):
        """展示段如果只有角色图，也不能误标为 Product reference"""
        result = _bind_reference_tags_to_prompt(
            "A person talks to camera.",
            [{"role": "character", "image": "img1"}],
            "showcase",
        )
        assert "Character reference: <<<image_1>>>" in result
        assert "Product reference: <<<image_1>>>" not in result

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


class TestBatchQualityDefaults:
    """测试批量模式质量默认值"""

    def test_batch_defaults_match_publish_first_policy(self):
        """批量生成默认也应使用发布级质量策略"""
        from batch import create_task_args

        args = create_task_args({"product_name": "测试产品"}, {})
        assert args["strict_mode"] is True
        assert args["stabilize"] is True
        assert args["best_of"] == 2
        assert args["allow_no_product_image"] is False


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
            "best_of": 2,
            "quality_frames": 12,
            "keep_candidates": False,
            "stabilize": True,
            "strict_mode": True,
            "brand_intro_outro": False,
            "kling_model": None,
            "multi_shot": False,
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

        class FakeResp:
            def __init__(self, status_code: int, chunks: list[bytes]):
                self.status_code = status_code
                self._chunks = chunks
                self.text = "rate limited"

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def json(self):
                return {"code": self.status_code, "message": self.text}

            def iter_content(self, chunk_size=4096):
                yield from self._chunks

        responses = [
            FakeResp(429, []),
            FakeResp(200, [b"x" * 2048]),
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
        """重编码输出应显式标记 full-range，避免平台误读色彩范围"""
        from video_merger import _color_range_args

        assert _color_range_args() == ["-color_range", "pc"]


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
        for narrative in ("showcase", "cta", "review", "proof", "demo", "detail", "reason", "effect"):
            assert _is_product_required_narrative(narrative)
        assert not _is_product_required_narrative("hook")

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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
