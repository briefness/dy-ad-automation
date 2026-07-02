#!/usr/bin/env python3
"""
可灵 AI 抖音广告视频 - 批量生成

使用方法：
    python batch.py --config batch.yaml

功能：
    读取 YAML 配置文件，批量生成多条广告视频
    支持并发控制，失败立即终止

前置条件：
    - 已在 config.py 中配置 KLING_API_KEY
    - 已安装 ffmpeg（brew install ffmpeg）
    - 已安装依赖：pip install requests pyyaml
"""

import sys
import argparse
import yaml
from pathlib import Path
from typing import Any, TypedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urlparse

from config import (
    OUTPUT_DIR,
    KLING_API_KEY,
    KLING_ACCESS_KEY,
    KLING_SECRET_KEY,
    DEFAULT_VIDEO_DURATION,
    DEFAULT_ASPECT_RATIO,
    DEFAULT_MODE,
    DEFAULT_IMAGE_FIDELITY,
    DEFAULT_HUMAN_FIDELITY,
    CINEMATIC_STYLES,
    DEFAULT_CINEMATIC_STYLE,
)
from video_merger import check_ffmpeg
from one_click_create import run_generation_pipeline


def _coerce_bool(value: Any, default: bool = False) -> bool:
    """把 YAML 中常见的字符串布尔值归一化，避免 'false' 被 Python 当成真值。"""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "y", "1", "on"}:
            return True
        if normalized in {"false", "no", "n", "0", "off", ""}:
            return False
    raise ValueError(f"无法解析布尔值：{value!r}")


def _coerce_int(value: Any, default: int | None = None) -> int | None:
    """把 YAML 字符串数字归一化为 int。"""
    if value is None:
        return default
    if isinstance(value, bool):
        raise ValueError(f"布尔值不能作为整数：{value!r}")
    try:
        return int(value)
    except (TypeError, ValueError) as e:
        raise ValueError(f"无法解析整数：{value!r}") from e


def _coerce_float(value: Any, default: float | None = None) -> float | None:
    """把 YAML 字符串数字归一化为 float。"""
    if value is None:
        return default
    if isinstance(value, bool):
        raise ValueError(f"布尔值不能作为浮点数：{value!r}")
    try:
        return float(value)
    except (TypeError, ValueError) as e:
        raise ValueError(f"无法解析浮点数：{value!r}") from e


def _get_config_value(task: dict, global_defaults: dict, key: str, fallback: Any) -> Any:
    """按 task > global_defaults > fallback 读取配置。"""
    return task.get(key, global_defaults.get(key, fallback))


def _build_task_output_name(task_id: int, task_name: str) -> str:
    """生成批量任务唯一输出名前缀，防止并发同名同秒覆盖。"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    safe_name = "".join(c for c in task_name if c.isalnum() or c in "-_").strip() or f"task_{task_id}"
    return f"{task_id:03d}_{safe_name}_{timestamp}"


def load_batch_config(yaml_path: Path) -> dict:
    """
    加载批量配置文件

    Args:
        yaml_path: YAML 配置文件路径

    Returns:
        配置字典
    """
    with open(yaml_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    if not config or "tasks" not in config:
        raise ValueError("YAML 配置文件必须包含 'tasks' 字段")

    # 设置默认值
    config.setdefault("concurrent", 1)
    config.setdefault("output_dir", str(OUTPUT_DIR / "batch"))
    config.setdefault("fail_fast", True)

    return config


class ProductInfo(TypedDict):
    name: str
    type: str
    selling_point: str
    audience: str
    style: str
    age: str
    gender: str
    outfit: str


def create_task_args(task: dict, global_defaults: dict) -> dict:
    """
    根据任务配置和全局默认值，构建可直接传给 run_generation_pipeline 的参数字典

    Args:
        task: 单个任务配置
        global_defaults: 全局默认值

    Returns:
        可直接 ** 展开传给 run_generation_pipeline 的参数字典
    """
    product_info: ProductInfo = {
        "name": task.get("product_name", "未命名产品"),
        "type": task.get("product_type", "default"),
        "selling_point": task.get("selling_point", "卓越品质，值得拥有"),
        "audience": task.get("audience", "18-35岁"),
        "style": task.get("ad_style", "现代简约"),
        "age": task.get("character_age", "25"),
        "gender": task.get("character_gender", "女"),
        "outfit": task.get("outfit", "casual everyday clothes"),
    }

    product_image_path = task.get("product_image")
    if product_image_path:
        parsed = urlparse(str(product_image_path))
        if parsed.scheme not in {"http", "https"}:
            product_image_path = Path(product_image_path)

    return {
        "product_info": product_info,
        "style": task.get("style", global_defaults.get("style", DEFAULT_CINEMATIC_STYLE)),
        "duration": _coerce_int(_get_config_value(task, global_defaults, "duration", DEFAULT_VIDEO_DURATION), DEFAULT_VIDEO_DURATION),
        "mode": task.get("mode", global_defaults.get("mode", DEFAULT_MODE)),
        "aspect_ratio": task.get("aspect_ratio", global_defaults.get("aspect_ratio", DEFAULT_ASPECT_RATIO)),
        "dual_output": _coerce_bool(_get_config_value(task, global_defaults, "dual_output", False), False),
        "image_fidelity": _coerce_float(_get_config_value(task, global_defaults, "image_fidelity", DEFAULT_IMAGE_FIDELITY), DEFAULT_IMAGE_FIDELITY),
        "human_fidelity": _coerce_float(_get_config_value(task, global_defaults, "human_fidelity", DEFAULT_HUMAN_FIDELITY), DEFAULT_HUMAN_FIDELITY),
        "seed": _coerce_int(_get_config_value(task, global_defaults, "seed", None), None),
        "product_image": product_image_path,
        "allow_no_product_image": _coerce_bool(_get_config_value(task, global_defaults, "allow_no_product_image", False), False),
        "characters": task.get("characters", None),
        "target_duration": _coerce_int(_get_config_value(task, global_defaults, "target_duration", None), None),
        "rhythm_style": task.get("rhythm_style", global_defaults.get("rhythm_style", "moderate")),
        # P1 修复：补充之前漏传的 11 个参数
        "hook_type": task.get("hook_type", global_defaults.get("hook_type", "question")),
        "use_voiceover": _coerce_bool(_get_config_value(task, global_defaults, "use_voiceover", False), False),
        "voiceover_style": task.get("voiceover_style", global_defaults.get("voiceover_style", "standard")),
        "voice": task.get("voice", global_defaults.get("voice", "female_young")),
        "script_style": task.get("script_style", global_defaults.get("script_style", "pain_point_solution")),
        "strict_mode": _coerce_bool(_get_config_value(task, global_defaults, "strict_mode", True), True),
        "force": _coerce_bool(_get_config_value(task, global_defaults, "force", False), False),
        "parallel": _coerce_bool(_get_config_value(task, global_defaults, "parallel", True), True),
        "min_clips": _coerce_int(_get_config_value(task, global_defaults, "min_clips", 3), 3),
        "preview": _coerce_bool(_get_config_value(task, global_defaults, "preview", False), False),
        "max_workers": _coerce_int(_get_config_value(task, global_defaults, "max_workers", 4), 4),
        "best_of": _coerce_int(_get_config_value(task, global_defaults, "best_of", 2), 2),
        "quality_frames": _coerce_int(_get_config_value(task, global_defaults, "quality_frames", 12), 12),
        "keep_candidates": _coerce_bool(_get_config_value(task, global_defaults, "keep_candidates", False), False),
        "stabilize": _coerce_bool(_get_config_value(task, global_defaults, "stabilize", True), True),
        "brand_intro_outro": _coerce_bool(_get_config_value(task, global_defaults, "brand_intro_outro", False), False),
        "kling_model": task.get("kling_model", global_defaults.get("kling_model", None)),
        "multi_shot": _coerce_bool(_get_config_value(task, global_defaults, "multi_shot", False), False),
    }


def run_single_task(task_id: int, task_config: dict, global_defaults: dict, base_output_dir: Path) -> dict:
    """
    执行单个任务

    Args:
        task_id: 任务 ID
        task_config: 任务配置
        global_defaults: 全局默认值
        base_output_dir: 基础输出目录

    Returns:
        任务结果字典
    """
    task_name = task_config.get("product_name", f"任务{task_id}")
    print(f"\n{'=' * 60}")
    print(f"📋 任务 {task_id}/{global_defaults.get('total_tasks', '?')}：{task_name}")
    print(f"{'=' * 60}")

    # 创建任务专用输出名前缀：包含 task_id + 微秒，防止并发同名任务互相覆盖
    task_output_name = _build_task_output_name(task_id, task_name)

    # 解析参数
    pipeline_kwargs = create_task_args(task_config, global_defaults)

    # 调用核心生成流水线
    try:
        result = run_generation_pipeline(
            **pipeline_kwargs,
            output_name=task_output_name,
            output_dir=base_output_dir,
        )
    except Exception as e:
        print(f"\n❌ 任务 {task_id} 失败：{e}")
        raise RuntimeError(f"任务 {task_id} 失败") from e

    final_path = result["final_path"]
    wide_path = result.get("wide_path")
    print(f"\n🎉 任务 {task_id} 完成！")
    print(f"📁 输出目录：{base_output_dir / 'final'}")
    print(f"🎬 最终成片：{final_path.name}")
    print(f"📊 文件大小：{final_path.stat().st_size / 1024 / 1024:.1f} MB")
    if wide_path and wide_path.exists():
        print(f"🖥️ 16:9 版本：{wide_path.name}")

    status = "preview" if result.get("preview") else "success"
    return {
        "task_id": task_id,
        "name": task_name,
        "status": status,
        "final_path": str(final_path),
        "wide_path": str(wide_path) if wide_path and wide_path.exists() else None,
        "output_name": task_output_name,
    }


def run_batch(config: dict):
    """
    执行批量任务

    Args:
        config: 配置字典
    """
    tasks = config.get("tasks", [])
    concurrent = _coerce_int(config.get("concurrent", 1), 1)
    fail_fast = _coerce_bool(config.get("fail_fast", True), True)
    base_output_dir = Path(config.get("output_dir", OUTPUT_DIR / "batch"))

    if not tasks:
        print("❌ 错误：没有任务需要执行")
        sys.exit(1)

    if not (KLING_API_KEY or (KLING_ACCESS_KEY and KLING_SECRET_KEY)):
        print("❌ 错误：未配置可灵鉴权信息（KLING_API_KEY 或 KLING_ACCESS_KEY + KLING_SECRET_KEY）")
        sys.exit(1)
    if not check_ffmpeg():
        print("❌ 错误：未安装 ffmpeg")
        sys.exit(1)

    print("=" * 60)
    print("🎬 可灵 AI 抖音广告视频 - 批量生成")
    print("=" * 60)
    print(f"📋 任务总数：{len(tasks)}")
    print(f"⚡ 并发数：{concurrent}")
    print(f"🚨 失败即停：{'是' if fail_fast else '否'}")
    print(f"📁 输出目录：{base_output_dir}")
    print()

    # 更新全局默认值
    # P1 修复：补充 create_task_args 中读取但此处缺失的 11 个字段
    global_defaults = {
        "style": config.get("default_style", DEFAULT_CINEMATIC_STYLE),
        "duration": config.get("default_duration", DEFAULT_VIDEO_DURATION),
        "mode": config.get("default_mode", DEFAULT_MODE),
        "aspect_ratio": config.get("default_aspect_ratio", DEFAULT_ASPECT_RATIO),
        "dual_output": config.get("default_dual_output", False),
        "image_fidelity": config.get("default_image_fidelity", DEFAULT_IMAGE_FIDELITY),
        "human_fidelity": config.get("default_human_fidelity", DEFAULT_HUMAN_FIDELITY),
        "seed": config.get("default_seed", None),
        "best_of": config.get("default_best_of", 2),
        "quality_frames": config.get("default_quality_frames", 12),
        "keep_candidates": config.get("default_keep_candidates", False),
        "stabilize": config.get("default_stabilize", True),
        "strict_mode": config.get("default_strict_mode", True),
        "brand_intro_outro": config.get("default_brand_intro_outro", False),
        "kling_model": config.get("default_kling_model", None),
        "multi_shot": config.get("default_multi_shot", False),
        "hook_type": config.get("default_hook_type", "question"),
        "use_voiceover": config.get("default_use_voiceover", False),
        "voiceover_style": config.get("default_voiceover_style", "standard"),
        "voice": config.get("default_voice", "female_young"),
        "script_style": config.get("default_script_style", "pain_point_solution"),
        "force": config.get("default_force", False),
        "parallel": config.get("default_parallel", True),
        "min_clips": config.get("default_min_clips", 3),
        "preview": config.get("default_preview", False),
        "max_workers": config.get("default_max_workers", 4),
        "target_duration": config.get("default_target_duration", None),
        "rhythm_style": config.get("default_rhythm_style", "moderate"),
        "total_tasks": len(tasks),
    }

    results = []
    failed_tasks = []

    if concurrent <= 1:
        # 串行执行
        for idx, task in enumerate(tasks, 1):
            try:
                result = run_single_task(idx, task, global_defaults, base_output_dir)
                results.append(result)
            except Exception as e:
                print(f"\n❌ 任务 {idx} 失败：{e}")
                failed_tasks.append({"task_id": idx, "task": task, "error": str(e)})
                if fail_fast:
                    print("\n🛑 遇到失败，终止批量执行（fail_fast=true）")
                    break
    else:
        # 并发执行
        with ThreadPoolExecutor(max_workers=concurrent) as executor:
            future_to_idx = {
                executor.submit(run_single_task, idx, task, global_defaults, base_output_dir): idx
                for idx, task in enumerate(tasks, 1)
            }

            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    result = future.result()
                    results.append(result)
                except Exception as e:
                    print(f"\n❌ 任务 {idx} 失败：{e}")
                    failed_tasks.append({"task_id": idx, "task": tasks[idx - 1], "error": str(e)})
                    if fail_fast:
                        print("\n🛑 遇到失败，终止批量执行（fail_fast=true）")
                        # 取消所有未完成的任务
                        for f in future_to_idx:
                            f.cancel()
                        break

    # ============================================================
    # 批量执行总结
    # ============================================================
    print()
    print("=" * 60)
    print("📊 批量执行总结")
    print("=" * 60)
    print(f"✅ 成功：{len(results)}/{len(tasks)}")
    print(f"❌ 失败：{len(failed_tasks)}/{len(tasks)}")

    if results:
        print("\n成功任务：")
        for r in sorted(results, key=lambda x: x["task_id"]):
            print(f"  - 任务 {r['task_id']}：{r['name']} → {r['final_path']}")

    if failed_tasks:
        print("\n失败任务：")
        for ft in sorted(failed_tasks, key=lambda x: x["task_id"]):
            print(f"  - 任务 {ft['task_id']}：{ft['error']}")

    print()
    print(f"📁 输出目录：{base_output_dir}")
    print()

    # 如果有失败任务，以非零状态退出
    if failed_tasks:
        sys.exit(1)


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description="可灵 AI 抖音广告视频 - 批量生成",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  python batch.py --config batch.yaml
  python batch.py --config batch.yaml --concurrent 2

YAML 配置文件示例：
  output_dir: "output/batch_20240627"
  concurrent: 1
  fail_fast: true
  default_style: "kubrick"
  default_duration: 8
  tasks:
    - product_name: "水润保湿面霜"
      product_type: "美妆"
      selling_point: "24小时深层保湿"
      style: "hitchcock"
    - product_name: "智能降噪耳机"
      product_type: "科技"
      selling_point: "40dB深度降噪"
      style: "kubrick"
        """,
    )
    parser.add_argument(
        "--config",
        required=True,
        help="YAML 配置文件路径",
    )
    parser.add_argument(
        "--concurrent",
        type=int,
        default=None,
        help="覆盖配置文件中的并发数",
    )
    args = parser.parse_args()

    # 加载配置
    config_path = Path(args.config)
    if not config_path.exists():
        print(f"❌ 错误：配置文件不存在：{config_path}")
        sys.exit(1)

    config = load_batch_config(config_path)

    # 命令行参数覆盖配置文件
    if args.concurrent is not None:
        config["concurrent"] = args.concurrent

    run_batch(config)


if __name__ == "__main__":
    main()
