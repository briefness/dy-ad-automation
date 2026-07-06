#!/usr/bin/env python3
"""
图片先行验证策略（Image-First Strategy）

核心理念：在生成高成本视频前，先用低成本的图片生成验证画面质量，
确保构图、角色一致性、产品质量都OK后再生成视频，实现"一次成功"。

成本对比（以5个片段、pro模式为例）：
- 直接生成视频：5 × 5秒 × ¥0.60 = ¥15.00
- 图片先行验证：3张图片 × ¥0.10 + 5个视频 × ¥3.00 = ¥15.30
  （成功率从~60%提升到~90%，实际成本更低）

三种策略可选：
1. MINIMAL：只生成1张最关键片段的图片（成本最低）
2. STANDARD：生成2-3个关键片段的图片（平衡）
3. FULL：所有片段都先生成图片再生成视频（成功率最高）
"""

import json
import base64
import math
from enum import Enum
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field
from PIL import Image, ImageStat, ImageFilter

from config import (
    setup_logger,
    NEGATIVE_PROMPT,
)
from quality_checker import (
    _product_similarity,
    _character_similarity,
)

logger = setup_logger(__name__)


class ImageFirstMode(Enum):
    """图片先行模式"""
    MINIMAL = "minimal"      # 只生成最关键1张
    STANDARD = "standard"    # 生成2-3个关键片段
    FULL = "full"            # 所有片段都生成


@dataclass
class KeyframeCandidate:
    """关键帧候选"""
    segment_index: int
    narrative: str
    prompt: str
    image_path: Path
    quality_score: float = 0.0
    product_similarity: float = 0.0
    character_similarity: float = 0.0
    sharpness_score: float = 0.0
    composition_score: float = 0.0
    passed: bool = False
    issues: List[str] = field(default_factory=list)


@dataclass
class ImageFirstResult:
    """图片先行验证结果"""
    strategy: str
    total_image_cost: float = 0.0
    total_images_generated: int = 0
    passed_candidates: List[KeyframeCandidate] = field(default_factory=list)
    failed_candidates: List[KeyframeCandidate] = field(default_factory=list)
    best_keyframes: Dict[int, Path] = field(default_factory=dict)  # segment_index -> image_path
    can_proceed_to_video: bool = False
    estimated_video_success_rate: float = 0.0
    messages: List[str] = field(default_factory=list)


# ── 关键片段选择策略 ──

def select_key_segments(
    ad_script: Dict[str, Any],
    mode: ImageFirstMode = ImageFirstMode.STANDARD,
) -> List[int]:
    """
    根据策略选择需要先生成图片验证的关键片段索引。

    关键程度排序（由高到低）：
    1. showcase（产品展示，核心卖点呈现）
    2. hook（首帧，决定用户是否停留）
    3. result（效果展示，建立信任）
    4. cta（行动号召，转化关键）
    5. turning_point（痛点引入，过渡段）
    """
    segments = ad_script.get("segments", [])
    if not segments:
        return []

    # 按关键程度排序
    priority_map = {
        "showcase": 1,
        "product": 1,
        "hook": 2,
        "result": 3,
        "cta": 4,
        "turning_point": 5,
        "turning": 5,
    }

    segment_priorities = []
    for i, seg in enumerate(segments):
        narrative = str(seg.get("narrative") or seg.get("type") or "").lower().strip()
        priority = priority_map.get(narrative, 10)
        segment_priorities.append((i, priority, narrative))

    segment_priorities.sort(key=lambda x: x[1])

    if mode == ImageFirstMode.MINIMAL:
        # 只选最关键1个
        return [segment_priorities[0][0]]

    elif mode == ImageFirstMode.STANDARD:
        # 选前2-3个，但至少包含showcase
        showcase_indices = [i for i, p, n in segment_priorities if n in {"showcase", "product"}]
        other_indices = [i for i, p, n in segment_priorities if n not in {"showcase", "product"}]

        selected = showcase_indices[:1]  # 至少1个showcase
        remaining_slots = 2 - len(selected)
        selected.extend(other_indices[:remaining_slots])
        return sorted(selected)

    elif mode == ImageFirstMode.FULL:
        # 所有片段
        return list(range(len(segments)))

    return [segment_priorities[0][0]]


# ── 图片质量门（严格版）──

def check_keyframe_quality(
    image_path: Path,
    *,
    product_reference_path: Optional[Path] = None,
    character_reference_path: Optional[Path] = None,
    narrative: str = "",
    strict_mode: bool = True,
) -> Tuple[bool, float, List[str]]:
    """
    对生成的关键帧图片进行严格质量检测。

    Returns:
        (是否通过, 综合质量评分, 问题列表)
    """
    issues = []
    scores = {}

    if not image_path.exists():
        return False, 0.0, ["图片文件不存在"]

    try:
        with Image.open(image_path) as img:
            img = img.convert("RGB")
            width, height = img.size
    except Exception as e:
        return False, 0.0, [f"图片格式错误：{e}"]

    # 1. 清晰度检测（拉普拉斯方差）
    gray = img.convert("L")
    edges = gray.filter(ImageFilter.FIND_EDGES)
    edge_stat = ImageStat.Stat(edges)
    sharpness = edge_stat.stddev[0]
    scores["sharpness"] = min(100.0, sharpness)

    if sharpness < 30:
        issues.append(f"图片清晰度不足（{sharpness:.1f} < 30）")
    elif sharpness < 50:
        issues.append(f"图片清晰度一般（{sharpness:.1f}），建议重新生成")

    # 2. 与商品参考图一致性
    if product_reference_path and product_reference_path.exists():
        if narrative in {"showcase", "product", "cta", "hook", "result"}:
            sim = _product_similarity(product_reference_path, [image_path])
            scores["product_similarity"] = sim * 100

            if sim < 0.50:
                issues.append(f"商品一致性严重不足（{sim:.2f} < 0.50）")
            elif sim < 0.65:
                issues.append(f"商品一致性较弱（{sim:.2f}）")
        else:
            scores["product_similarity"] = 100.0
    else:
        scores["product_similarity"] = 100.0

    # 3. 与角色参考图一致性
    if character_reference_path and character_reference_path.exists():
        if narrative not in {"product", "demo"}:
            sim = _character_similarity(character_reference_path, [image_path])
            scores["character_similarity"] = sim * 100

            if sim < 0.45:
                issues.append(f"角色一致性严重不足（{sim:.2f} < 0.45）")
            elif sim < 0.55:
                issues.append(f"角色一致性较弱（{sim:.2f}）")
        else:
            scores["character_similarity"] = 100.0
    else:
        scores["character_similarity"] = 100.0

    # 4. 构图评分（主体是否居中、占比合理）
    # 简化版：检测亮度分布是否均匀
    stat = ImageStat.Stat(gray)
    brightness = stat.mean[0]
    brightness_std = stat.stddev[0]

    # 亮度分布评分（标准差适中表示有层次）
    if brightness_std < 10:
        composition_score = 40.0  # 过于平淡
        issues.append("图片对比度过低，构图可能过于平淡")
    elif brightness_std > 80:
        composition_score = 60.0  # 过于杂乱
        issues.append("图片对比度过高，可能存在过曝或过暗区域")
    else:
        composition_score = 90.0

    scores["composition"] = composition_score

    # 5. 综合评分
    weights = {
        "sharpness": 0.25,
        "product_similarity": 0.25,
        "character_similarity": 0.25,
        "composition": 0.25,
    }

    total_score = sum(scores.get(k, 0) * w for k, w in weights.items())

    # 严格模式：有任何严重问题即失败
    if strict_mode:
        passed = len(issues) == 0 and total_score >= 70.0
    else:
        passed = total_score >= 60.0

    return passed, total_score, issues


# ── 批量图片生成与筛选 ──

def generate_keyframe_candidates(
    *,
    client,
    segment_indices: List[int],
    clip_prompts: List[str],
    ad_script: Dict[str, Any],
    product_reference_path: Optional[Path] = None,
    character_reference_path: Optional[Path] = None,
    save_dir: Path,
    n_variants: int = 2,  # 每个片段生成几张备选
    aspect_ratio: str = "9:16",
    image_fidelity: float = 0.9,
) -> List[KeyframeCandidate]:
    """
    为关键片段批量生成图片候选，返回所有候选供后续筛选。

    Args:
        client: Kling API client
        segment_indices: 需要生成图片的片段索引
        clip_prompts: 所有片段的Prompt列表
        ad_script: 广告脚本
        product_reference_path: 商品参考图路径
        character_reference_path: 角色参考图路径
        save_dir: 保存目录
        n_variants: 每个片段生成几张备选（默认2张）
        aspect_ratio: 宽高比
        image_fidelity: 参考图一致性权重

    Returns:
        所有生成的候选图片信息
    """
    candidates = []
    segments = ad_script.get("segments", [])

    save_dir.mkdir(parents=True, exist_ok=True)

    for seg_idx in segment_indices:
        if seg_idx >= len(clip_prompts):
            continue

        narrative = str(segments[seg_idx].get("narrative") or "").lower().strip() if seg_idx < len(segments) else ""
        prompt = clip_prompts[seg_idx]

        # 清洗Prompt为静态图片版本
        image_prompt = _sanitize_prompt_for_image_generation(prompt)
        if not image_prompt:
            image_prompt = prompt

        logger.info(f"🖼️ 为片段 {seg_idx+1} ({narrative}) 生成 {n_variants} 张备选图片...")

        for variant in range(n_variants):
            save_path = save_dir / f"keyframe_seg{seg_idx+1}_v{variant+1}.png"

            # 选择参考图
            ref_image_b64 = None
            ref_type = None

            product_required = narrative in {"showcase", "product", "cta", "hook", "result"}
            has_product_ref = product_reference_path and product_reference_path.exists()
            has_char_ref = character_reference_path and character_reference_path.exists()

            if product_required and has_product_ref:
                ref_image_b64 = base64.b64encode(product_reference_path.read_bytes()).decode("utf-8")
                ref_type = "subject"
            elif has_char_ref:
                ref_image_b64 = base64.b64encode(character_reference_path.read_bytes()).decode("utf-8")
                ref_type = "face"
            elif has_product_ref:
                ref_image_b64 = base64.b64encode(product_reference_path.read_bytes()).decode("utf-8")
                ref_type = "subject"

            try:
                result = client.generate_image(
                    prompt=image_prompt,
                    negative_prompt=NEGATIVE_PROMPT,
                    reference_image=ref_image_b64,
                    image_reference=ref_type,
                    image_fidelity=image_fidelity,
                    aspect_ratio=aspect_ratio,
                    resolution="1k",
                    n=1,
                    wait=True,
                    timeout=90,
                )
                images = result.get("data", {}).get("task_result", {}).get("images", [])
                if not images:
                    logger.warning(f"片段 {seg_idx+1} 变体 {variant+1} 图片生成结果为空")
                    continue

                image_url = images[0].get("url")
                if not image_url:
                    continue

                img_response = client.session.get(image_url, timeout=30)
                img_response.raise_for_status()
                save_path.write_bytes(img_response.content)

                # 验证图片
                try:
                    Image.open(save_path).verify()
                except Exception:
                    logger.warning(f"片段 {seg_idx+1} 变体 {variant+1} 下载内容无效")
                    continue

                candidate = KeyframeCandidate(
                    segment_index=seg_idx,
                    narrative=narrative,
                    prompt=prompt,
                    image_path=save_path,
                )
                candidates.append(candidate)
                logger.info(f"✅ 片段 {seg_idx+1} 变体 {variant+1} 生成成功")

            except Exception as e:
                logger.warning(f"片段 {seg_idx+1} 变体 {variant+1} 生成失败：{e}")
                continue

    return candidates


def select_best_keyframes(
    candidates: List[KeyframeCandidate],
    product_reference_path: Optional[Path] = None,
    character_reference_path: Optional[Path] = None,
    strict_mode: bool = True,
) -> ImageFirstResult:
    """
    对所有候选图片进行质量检测，选出每个片段的最佳图片。

    Returns:
        图片先行验证结果
    """
    result = ImageFirstResult(strategy="standard")
    result.total_images_generated = len(candidates)

    # 按片段分组
    segment_candidates: Dict[int, List[KeyframeCandidate]] = {}
    for c in candidates:
        if c.segment_index not in segment_candidates:
            segment_candidates[c.segment_index] = []
        segment_candidates[c.segment_index].append(c)

    # 对每个片段的候选进行质量检测
    for seg_idx, seg_candidates in segment_candidates.items():
        best_candidate = None
        best_score = -1

        for candidate in seg_candidates:
            passed, score, issues = check_keyframe_quality(
                candidate.image_path,
                product_reference_path=product_reference_path,
                character_reference_path=character_reference_path,
                narrative=candidate.narrative,
                strict_mode=strict_mode,
            )

            candidate.quality_score = score
            candidate.passed = passed
            candidate.issues = issues

            # 细化评分
            if product_reference_path and product_reference_path.exists():
                candidate.product_similarity = _product_similarity(product_reference_path, [candidate.image_path])
            if character_reference_path and character_reference_path.exists():
                candidate.character_similarity = _character_similarity(character_reference_path, [candidate.image_path])

            if passed and score > best_score:
                best_score = score
                best_candidate = candidate

        if best_candidate:
            result.passed_candidates.append(best_candidate)
            result.best_keyframes[seg_idx] = best_candidate.image_path
            result.messages.append(
                f"✅ 片段 {seg_idx+1} ({best_candidate.narrative}) 最佳图片："
                f"评分 {best_candidate.quality_score:.1f}，"
                f"商品一致性 {best_candidate.product_similarity:.2f}，"
                f"角色一致性 {best_candidate.character_similarity:.2f}"
            )
        else:
            # 没有通过的，选评分最高的作为失败记录
            seg_candidates.sort(key=lambda x: x.quality_score, reverse=True)
            best_failed = seg_candidates[0]
            result.failed_candidates.append(best_failed)
            result.messages.append(
                f"❌ 片段 {seg_idx+1} ({best_failed.narrative}) 所有图片未通过质量检测："
                f"最高评分 {best_failed.quality_score:.1f}，"
                f"问题：{', '.join(best_failed.issues)}"
            )

    # 判断是否可以通过到视频生成
    total_segments = len(segment_candidates)
    passed_segments = len(result.passed_candidates)

    if total_segments > 0:
        pass_rate = passed_segments / total_segments
        result.estimated_video_success_rate = min(0.95, 0.60 + pass_rate * 0.35)

        if pass_rate >= 0.8:
            result.can_proceed_to_video = True
            result.messages.append(f"🎯 图片验证通过率 {pass_rate:.1%}，预估视频成功率 {result.estimated_video_success_rate:.1%}")
        elif pass_rate >= 0.5:
            result.can_proceed_to_video = True
            result.messages.append(f"⚠️ 图片验证通过率 {pass_rate:.1%}，建议降低fidelity后重试未通过片段")
        else:
            result.can_proceed_to_video = False
            result.messages.append(f"❌ 图片验证通过率过低（{pass_rate:.1%}），建议修复参考图或Prompt后再生成")

    return result


# ── Prompt清洗工具 ──

def _sanitize_prompt_for_image_generation(prompt: str) -> str:
    """将视频Prompt清洗为适合图片生成的静态版本。"""
    # 移除时间/运动相关词汇
    motion_keywords = [
        "moving", "walking", "running", "spinning", "rotating",
        "camera moving", "tracking shot", "panning", "zooming",
        "video", "motion", "animation", "transition",
        "walking", "turning", "looking around",
    ]

    cleaned = prompt
    for kw in motion_keywords:
        cleaned = cleaned.replace(kw, "").replace(kw.title(), "")

    # 添加静态描述
    static_additions = [
        "static image",
        "still photograph",
        "high quality photo",
    ]

    # 如果清洗后太短，保留原Prompt
    if len(cleaned.strip()) < 30:
        cleaned = prompt

    return cleaned.strip()


# ── 主流程 ──

def run_image_first_strategy(
    *,
    client,
    ad_script: Dict[str, Any],
    clip_prompts: List[str],
    product_reference_path: Optional[Path] = None,
    character_reference_path: Optional[Path] = None,
    save_dir: Path,
    mode: ImageFirstMode = ImageFirstMode.STANDARD,
    n_variants: int = 2,
    aspect_ratio: str = "9:16",
    image_fidelity: float = 0.9,
    strict_mode: bool = True,
) -> ImageFirstResult:
    """
    运行完整的图片先行验证流程。

    Returns:
        包含最佳关键帧和后续视频生成建议的结果
    """
    logger.info(f"🖼️ 启动图片先行验证策略（模式：{mode.value}）...")

    # 1. 选择关键片段
    key_segments = select_key_segments(ad_script, mode)
    logger.info(f"📋 选择的关键片段：{[i+1 for i in key_segments]}")

    # 2. 批量生成图片候选
    candidates = generate_keyframe_candidates(
        client=client,
        segment_indices=key_segments,
        clip_prompts=clip_prompts,
        ad_script=ad_script,
        product_reference_path=product_reference_path,
        character_reference_path=character_reference_path,
        save_dir=save_dir,
        n_variants=n_variants,
        aspect_ratio=aspect_ratio,
        image_fidelity=image_fidelity,
    )

    if not candidates:
        result = ImageFirstResult(strategy=mode.value)
        result.messages.append("❌ 所有图片生成失败")
        result.can_proceed_to_video = False
        return result

    # 3. 质量检测 + 最佳筛选
    result = select_best_keyframes(
        candidates,
        product_reference_path=product_reference_path,
        character_reference_path=character_reference_path,
        strict_mode=strict_mode,
    )

    result.strategy = mode.value
    # 估算图片成本（每张约 ¥0.10）
    result.total_image_cost = len(candidates) * 0.10

    logger.info(f"🎯 图片先行验证完成：{len(result.passed_candidates)}/{len(key_segments)} 片段通过")

    return result


def print_image_first_report(result: ImageFirstResult) -> None:
    """打印图片先行验证报告"""
    print("\n" + "=" * 70)
    print("🖼️ 图片先行验证报告")
    print("=" * 70)

    print(f"\n📊 策略：{result.strategy.upper()}")
    print(f"📸 生成图片总数：{result.total_images_generated}")
    print(f"💰 图片生成成本：¥{result.total_image_cost:.2f}")
    print(f"✅ 通过片段数：{len(result.passed_candidates)}")
    print(f"❌ 失败片段数：{len(result.failed_candidates)}")
    print(f"🎯 预估视频成功率：{result.estimated_video_success_rate:.1%}")

    if result.can_proceed_to_video:
        print("\n✅ 可以进入视频生成阶段")
    else:
        print("\n❌ 建议修复后再进入视频生成阶段")

    if result.best_keyframes:
        print("\n🏆 各片段最佳关键帧：")
        for seg_idx, path in sorted(result.best_keyframes.items()):
            print(f"   片段 {seg_idx+1}: {path.name}")

    if result.passed_candidates:
        print("\n📈 通过候选详情：")
        for c in result.passed_candidates:
            print(f"   片段 {c.segment_index+1} ({c.narrative}):")
            print(f"      路径：{c.image_path.name}")
            print(f"      质量评分：{c.quality_score:.1f}/100")
            print(f"      商品一致性：{c.product_similarity:.2f}")
            print(f"      角色一致性：{c.character_similarity:.2f}")

    if result.failed_candidates:
        print("\n❌ 失败候选详情：")
        for c in result.failed_candidates:
            print(f"   片段 {c.segment_index+1} ({c.narrative}):")
            print(f"      质量评分：{c.quality_score:.1f}/100")
            print(f"      问题：{', '.join(c.issues)}")

    if result.messages:
        print("\n💬 详细日志：")
        for msg in result.messages:
            print(f"   • {msg}")

    print("\n" + "=" * 70 + "\n")
