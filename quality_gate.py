#!/usr/bin/env python3
"""
质量前置控制模块（Quality Gate）

核心理念：在调用API生成前，通过预检查消除质量隐患，
避免"生成→检测→失败→重试"的成本浪费模式。

功能：
1. 参考图预检：验证角色/产品参考图质量
2. Prompt 预校验：检查 prompt 质量和完整性
3. 参数锁定：保证生成参数的可复现性
4. 成本估算：生成前计算预估费用
5. 契约验证：检查脚本、圣经、场景配置完整性
"""

import os
import re
import json
import math
import hashlib
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field
from PIL import Image, ImageStat, ImageFilter

from config import (
    QUALITY_GATE_CONFIG,
    REFERENCE_QUALITY_STANDARDS,
    PROMPT_QUALITY_RULES,
    COST_ESTIMATION_RULES,
    NEGATIVE_PROMPT,
    setup_logger,
)

logger = setup_logger(__name__)


@dataclass
class CheckResult:
    """预检结果"""
    passed: bool
    category: str
    name: str
    score: float = 0.0
    messages: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)


@dataclass
class ReferenceImageCheckResult:
    """参考图预检结果"""
    passed: bool
    path: Path
    image_type: str
    width: int = 0
    height: int = 0
    brightness: float = 0.0
    contrast: float = 0.0
    transparent_ratio: float = 0.0
    subject_centered: bool = True
    subject_size_ratio: float = 0.0
    has_watermark: bool = False
    background_complexity: float = 0.0
    is_frontal_face: bool = True
    face_score: float = 0.0
    quality_score: float = 100.0
    blockers: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    messages: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)


@dataclass
class PromptCheckResult:
    """Prompt 预检结果"""
    passed: bool
    prompt: str
    length: int = 0
    score: float = 0.0
    keyword_missing: List[str] = field(default_factory=list)
    negative_keyword_missing: List[str] = field(default_factory=list)
    conflicts: List[str] = field(default_factory=list)
    repetitions: List[str] = field(default_factory=list)
    semantic_issues: List[str] = field(default_factory=list)
    optimized_prompt: Optional[str] = None
    blockers: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    messages: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)


@dataclass
class CostEstimate:
    """成本估算结果"""
    total_cost: float = 0.0
    image_cost: float = 0.0
    video_cost: float = 0.0
    additional_cost: float = 0.0
    breakdown: List[Dict[str, Any]] = field(default_factory=list)
    within_budget: bool = True
    budget_limit: float = 0.0
    # 新增字段
    downgrade_options: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class OptimizationSuggestion:
    """优化建议"""
    type: str
    original_value: str
    suggested_value: str
    impact: str
    estimated_savings: float = 0.0


@dataclass
class QualityGateResult:
    """质量门整体检查结果（v2 综合评分制）"""
    passed: bool
    reference_checks: List[ReferenceImageCheckResult] = field(default_factory=list)
    prompt_checks: List[PromptCheckResult] = field(default_factory=list)
    contract_checks: List[CheckResult] = field(default_factory=list)
    cost_estimate: Optional[CostEstimate] = None
    total_score: float = 0.0
    quality_score: float = 100.0
    blocker_count: int = 0
    warning_count: int = 0
    optimization_count: int = 0
    summary: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)
    optimization_suggestions: List[OptimizationSuggestion] = field(default_factory=list)
    coherence_result: Optional[CoherenceCheckResult] = None
    historical_result: Optional[HistoricalOptimizationResult] = None
    failure_prediction: Optional[FailurePredictionResult] = None
    optimized_prompts: List[str] = field(default_factory=list)
    optimized_negative_prompt: Optional[str] = None
    preprocessed_reference_images: List[Dict[str, Any]] = field(default_factory=list)
    sorted_reference_images: List[ReferenceImageCheckResult] = field(default_factory=list)
    deduplicated_reference_images: List[ReferenceImageCheckResult] = field(default_factory=list)
    reference_sort_notes: List[str] = field(default_factory=list)
    reference_dedup_removed: List[str] = field(default_factory=list)
    recommended_parameters: Dict[str, Any] = field(default_factory=dict)


# ── 参考图预检 ──

def check_reference_image(
    image_path: Path,
    image_type: str = "product",
) -> ReferenceImageCheckResult:
    """
    检查参考图质量（v2 扣分制 + 分级机制）。

    不再一票否决，而是按严重程度分级：
    - 🔴 blocker：直接失败（如图不存在、完全无主体）
    - 🟡 warning：扣分但不直接失败（如主体略小、背景稍复杂）
    - 🔵 info：纯建议，不扣分

    最终 quality_score >= 60 视为通过。
    """
    config = QUALITY_GATE_CONFIG.get("reference_check", {})
    if not config.get("enabled", True):
        return ReferenceImageCheckResult(
            passed=True,
            path=image_path,
            image_type=image_type,
            messages=["参考图预检已跳过"],
            quality_score=100.0,
        )

    result = ReferenceImageCheckResult(
        passed=True,
        path=image_path,
        image_type=image_type,
        quality_score=100.0,
    )

    def _block(msg: str, suggestion: str = ""):
        result.blockers.append(msg)
        result.messages.append(f"🔴 {msg}")
        if suggestion:
            result.suggestions.append(suggestion)
        result.passed = False
        result.quality_score = max(0.0, result.quality_score - 50)

    def _warn(msg: str, suggestion: str = "", penalty: float = 10):
        result.warnings.append(msg)
        result.messages.append(f"🟡 {msg}")
        if suggestion:
            result.suggestions.append(suggestion)
        result.quality_score = max(0.0, result.quality_score - penalty)

    def _info(msg: str, suggestion: str = ""):
        result.messages.append(f"🔵 {msg}")
        if suggestion:
            result.suggestions.append(suggestion)

    # ── 致命错误：直接返回 ──
    if not image_path.exists():
        _block(f"参考图不存在：{image_path}", "请提供有效的参考图路径")
        return result

    if image_path.stat().st_size < 1024:
        _block(f"参考图文件过小（{image_path.stat().st_size} 字节）", "请提供至少 1KB 的有效图片")
        return result

    try:
        with Image.open(image_path) as src:
            src.verify()
        with Image.open(image_path) as src:
            img = src.convert("RGBA")
    except Exception as e:
        _block(f"参考图格式损坏：{e}", "请使用有效的图片格式（JPG/PNG）")
        return result

    width, height = img.size
    result.width = width
    result.height = height

    min_w = config.get("min_width", 512)
    min_h = config.get("min_height", 512)
    max_w = config.get("max_width", 4096)
    max_h = config.get("max_height", 4096)

    if width < min_w or height < min_h:
        _block(
            f"参考图分辨率过低（{width}x{height}），要求 >= {min_w}x{min_h}",
            f"请提供分辨率至少 {min_w}x{min_h} 的图片",
        )
        # 分辨率过低直接返回，后续检测无意义
        result.quality_score = min(result.quality_score, 30.0)
        return result

    if width > max_w or height > max_h:
        _warn(
            f"参考图分辨率过高（{width}x{height}），建议 <= {max_w}x{max_h}",
            f"可将图片缩放到 {max_w}x{max_h} 以内以加快处理",
            penalty=5,
        )

    alpha = img.getchannel("A")
    opaque_ratio = sum(1 for p in alpha.getdata() if p > 16) / max(1, width * height)
    transparent_ratio = 1 - opaque_ratio
    result.transparent_ratio = transparent_ratio

    max_transparent = config.get("max_transparent_ratio", 0.95)
    if transparent_ratio > max_transparent:
        _warn(
            f"参考图透明区域较多（{transparent_ratio:.2%}），建议 <= {max_transparent:.2%}",
            "请提供主体清晰、背景不透明的参考图",
            penalty=15,
        )

    rgb = img.convert("RGB")
    stat = ImageStat.Stat(rgb)
    channel_std = sum(stat.stddev) / max(1, len(stat.stddev))
    brightness = sum(stat.mean) / max(1, len(stat.mean))
    result.brightness = brightness
    result.contrast = channel_std

    min_brightness = config.get("min_brightness", 15)
    max_brightness = config.get("max_brightness", 240)
    if image_type == "product":
        max_brightness = max(max_brightness, 250)

    if brightness < min_brightness:
        _warn(
            f"参考图偏暗（亮度 {brightness:.1f}），建议 >= {min_brightness}",
            "可适当提高亮度",
            penalty=8,
        )
    elif brightness > max_brightness:
        _warn(
            f"参考图偏亮（亮度 {brightness:.1f}），建议 <= {max_brightness}",
            "可适当降低曝光",
            penalty=8,
        )

    min_contrast = config.get("min_contrast", 5)
    if channel_std < min_contrast:
        _warn(
            f"参考图对比度偏低（{channel_std:.1f}），建议 >= {min_contrast}",
            "请提供色彩丰富、对比度正常的参考图",
            penalty=10,
        )

    # ── 主体检测（是否居中、尺寸比例）──
    if image_type in ("product", "character"):
        try:
            subject_ratio = _analyze_subject_size(img)
            result.subject_size_ratio = subject_ratio

            is_centered, offset = _analyze_subject_center(img)
            result.subject_centered = is_centered

            if not is_centered:
                _info(
                    f"主体偏移中心（偏移量：{offset:.1f}像素）",
                    "建议将主体居中放置",
                )

            min_subject_ratio = 0.15 if image_type == "product" else 0.2
            if subject_ratio < min_subject_ratio * 0.5:
                # 主体严重过小（不足阈值的50%）→ blocker
                _block(
                    f"主体占比严重不足（{subject_ratio:.1%}），要求 >= {min_subject_ratio:.1%}",
                    "建议放大主体，使其占据画面主要区域",
                )
            elif subject_ratio < min_subject_ratio:
                # 主体略小（在阈值 50%-100% 之间）→ warning
                _warn(
                    f"主体占比偏小（{subject_ratio:.1%}），建议 >= {min_subject_ratio:.1%}",
                    "建议放大主体，使其占据画面主要区域",
                    penalty=12,
                )

            max_subject_ratio = 0.95
            if subject_ratio > max_subject_ratio:
                _info(
                    f"主体占比过大（{subject_ratio:.1%}）",
                    "建议适当留出背景空间",
                )
        except Exception as e:
            _info(f"主体检测失败：{e}")

    # ── 背景复杂度分析 ──
    try:
        bg_complexity = _analyze_background_complexity(rgb)
        result.background_complexity = bg_complexity

        if bg_complexity > 0.85:
            _warn(
                f"背景过于复杂（复杂度：{bg_complexity:.2f}）",
                "建议使用简洁背景，避免干扰AI识别主体",
                penalty=10,
            )
        elif bg_complexity > 0.7:
            _info(
                f"背景稍复杂（复杂度：{bg_complexity:.2f}）",
                "可考虑使用更简洁的背景",
            )
    except Exception as e:
        _info(f"背景分析失败：{e}")

    # ── 水印/文字检测 ──
    try:
        has_watermark = _detect_watermark_or_text(img)
        result.has_watermark = has_watermark

        if has_watermark:
            _warn(
                "检测到可能的水印或文字",
                "建议使用无水印的参考图",
                penalty=8,
            )
    except Exception as e:
        _info(f"水印检测失败：{e}")

    # ── 人脸检测（仅角色参考图）──
    if image_type == "character":
        try:
            face_result = _analyze_face_quality(rgb, width, height)
            result.is_frontal_face = face_result["is_frontal"]
            result.face_score = face_result["score"]

            if not face_result["face_detected"]:
                _block(
                    "未检测到人脸",
                    "请确保角色参考图包含清晰的人脸",
                )
            else:
                if not face_result["is_frontal"]:
                    _warn(
                        "角色参考图非正面视角",
                        "建议使用正面清晰肖像作为角色参考图",
                        penalty=15,
                    )

                if face_result["score"] < 0.3:
                    _block(
                        f"人脸清晰度过低（{face_result['score']:.2f}）",
                        "建议使用面部清晰的参考图",
                    )
                elif face_result["score"] < 0.5:
                    _warn(
                        f"人脸清晰度一般（{face_result['score']:.2f}），建议 >= 0.5",
                        "建议使用面部更清晰的参考图",
                        penalty=10,
                    )
        except Exception as e:
            _info(f"人脸检测失败：{e}")

    # ── 最终判定 ──
    if not result.blockers:
        # 没有 blocker，按分数判定
        min_pass_score = config.get("min_pass_score", 60)
        if result.quality_score >= min_pass_score:
            result.passed = True
            result.messages.append(
                f"✅ 参考图质量合格（{width}x{height}，综合评分 {result.quality_score:.0f}/100）"
            )
        else:
            result.passed = False
            result.messages.append(
                f"❌ 参考图综合评分偏低（{result.quality_score:.0f}/{min_pass_score}）"
            )

    return result


def _analyze_subject_size(img: Image.Image) -> float:
    """分析主体占比（通过肤色+非背景色区域估算真实面积占比）

    思路：
    1. 肤色区域检测（人物主体的核心特征）
    2. 中心区域的非均匀色区域（衣物/身体）
    3. 综合估算主体面积占比
    """
    import numpy as np

    width, height = img.size
    rgb = img.convert("RGB")
    arr = np.array(rgb)
    r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]

    # 1. 肤色区域检测（YCbCr 色彩空间阈值）
    max_rgb = np.maximum(np.maximum(r, g), b)
    min_rgb = np.minimum(np.minimum(r, g), b)
    skin_mask = (
        (r > 95) & (g > 40) & (b > 20) &
        (max_rgb - min_rgb > 15) &
        (np.abs(r.astype(int) - g.astype(int)) > 15) &
        (r > g) & (r > b)
    )
    skin_ratio = float(skin_mask.sum()) / (width * height)

    # 2. 非背景色区域：中心区域的非均匀像素
    # 假设背景是相对均匀的颜色（如白色/纯色背景）
    cx1, cx2 = int(width * 0.2), int(width * 0.8)
    cy1, cy2 = int(height * 0.15), int(height * 0.85)
    center_region = arr[cy1:cy2, cx1:cx2, :]

    # 计算中心区域的颜色方差
    center_gray = np.mean(center_region, axis=2)
    center_std = float(np.std(center_gray))

    # 3. 综合估算：肤色是主体的强信号
    # 如果肤色占比 > 5%，说明有明确人物，按肤色区域的 4-6 倍估算全身
    if skin_ratio > 0.05:
        # 正面头像：肤色约占主体的 1/4 ~ 1/3
        # 全身照：肤色约占主体的 1/6 ~ 1/4
        estimated_subject_ratio = min(0.95, skin_ratio * 5)
    else:
        # 肤色不明显，用中心区域复杂度估算
        # 中心区域标准差越大，主体越突出
        center_complexity = min(1.0, center_std / 80.0)
        estimated_subject_ratio = 0.15 + center_complexity * 0.4

    # 4. 用边缘密度做交叉验证（边缘密度和主体大小正相关，但不是线性）
    grayscale = img.convert("L")
    edges = grayscale.filter(ImageFilter.FIND_EDGES)
    edge_arr = np.array(edges)
    edge_ratio = float((edge_arr > 30).sum()) / (width * height)

    # 边缘密度越高，主体越复杂/越大，但边缘密度本身是轮廓比例
    # 简单校准：边缘密度 0.03 对应约 40% 主体占比
    edge_based_estimate = min(0.9, edge_ratio * 12)

    # 取两种方法的较高值（更宽容）
    final_ratio = max(estimated_subject_ratio, edge_based_estimate)

    return min(0.95, max(0.0, final_ratio))


def _analyze_subject_center(img: Image.Image) -> Tuple[bool, float]:
    """分析主体是否居中，返回（是否居中，偏移量）

    思路：
    - 水平方向（x轴）严格要求居中（人物肖像通常左右对称）
    - 垂直方向（y轴）更宽容（人脸通常在画面上半部分，符合构图美学）
    - 用肤色区域的质心来判断主体位置
    """
    import numpy as np

    width, height = img.size
    rgb = img.convert("RGB")
    arr = np.array(rgb)
    r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]

    # 肤色区域
    max_rgb = np.maximum(np.maximum(r, g), b)
    min_rgb = np.minimum(np.minimum(r, g), b)
    skin_mask = (
        (r > 95) & (g > 40) & (b > 20) &
        (max_rgb - min_rgb > 15) &
        (np.abs(r.astype(int) - g.astype(int)) > 15) &
        (r > g) & (r > b)
    )

    # 如果有足够的肤色区域，用肤色的质心作为主体位置
    skin_pixels = skin_mask.sum()
    if skin_pixels > width * height * 0.02:
        ys, xs = np.where(skin_mask)
        subject_x = float(xs.mean())
        subject_y = float(ys.mean())
    else:
        # 否则用边缘密度的质心
        grayscale = img.convert("L")
        edges = grayscale.filter(ImageFilter.FIND_EDGES)
        edge_arr = np.array(edges)
        edge_mask = edge_arr > 40
        edge_pixels = edge_mask.sum()
        if edge_pixels > 100:
            ys, xs = np.where(edge_mask)
            subject_x = float(xs.mean())
            subject_y = float(ys.mean())
        else:
            return True, 0.0

    center_x = width / 2

    # 水平方向偏移（严格）
    offset_x = abs(subject_x - center_x)
    max_offset_x = width * 0.2  # 水平方向允许 20% 偏移

    # 垂直方向偏移（宽容）：人物肖像人脸通常在上1/3到1/2处
    # 正常范围：画面高度的 20% - 60% 之间都算可以接受
    y_min_acceptable = height * 0.15
    y_max_acceptable = height * 0.65
    y_acceptable = y_min_acceptable <= subject_y <= y_max_acceptable

    # 计算综合偏移量（主要考虑水平方向）
    # 水平偏移权重 1.0，垂直偏移权重 0.3
    weighted_offset = offset_x * 1.0 + max(0, abs(subject_y - height * 0.4) - height * 0.15) * 0.3

    # 判定是否居中：水平方向在范围内 + 垂直方向合理
    is_centered = offset_x < max_offset_x and y_acceptable

    return is_centered, weighted_offset


def _analyze_background_complexity(img: Image.Image) -> float:
    """分析背景复杂度（0-1，越高越复杂）

    思路：只分析画面四周的边距区域（排除中心主体），
    用这些区域的颜色均匀度来衡量背景复杂度。
    """
    import numpy as np

    width, height = img.size
    gray = np.array(img.convert("L"))

    # 定义四个边距区域（排除中心主体区域）
    margin_ratio = 0.08  # 边距占比

    top = gray[:int(height * margin_ratio), :]
    bottom = gray[int(height * (1 - margin_ratio)):, :]
    left = gray[:, :int(width * margin_ratio)]
    right = gray[:, int(width * (1 - margin_ratio)):]

    regions = [top, bottom, left, right]

    # 计算每个边距区域的"均匀度"
    uniform_scores = []
    for region in regions:
        if region.size == 0:
            continue
        std = float(np.std(region))
        mean = float(np.mean(region))

        # 标准差越小，背景越均匀（复杂度越低）
        # 纯白背景 std < 5，简单背景 std 5-20，复杂背景 std > 30
        if std < 3:
            uniform_scores.append(1.0)  # 极均匀
        elif std < 10:
            uniform_scores.append(0.85)  # 很均匀
        elif std < 20:
            uniform_scores.append(0.65)  # 较均匀
        elif std < 35:
            uniform_scores.append(0.4)   # 一般
        else:
            uniform_scores.append(0.15)  # 复杂

    # 取最均匀的两个区域的平均（可能主体延伸到了某些边距）
    if len(uniform_scores) >= 2:
        uniform_scores.sort(reverse=True)
        avg_uniform = sum(uniform_scores[:2]) / 2
    elif uniform_scores:
        avg_uniform = uniform_scores[0]
    else:
        avg_uniform = 0.5

    # 背景复杂度 = 1 - 均匀度
    complexity = 1.0 - avg_uniform

    return max(0.0, min(1.0, complexity))


def _detect_watermark_or_text(img: Image.Image) -> bool:
    """检测水印或文字（更鲁棒的版本，减少人物边缘误报）

    思路：
    1. 检查四角区域的高频纹理
    2. 排除肤色区域（人物皮肤/头发不算水印）
    3. 检查是否有规律性的水平线条（文字特征）
    """
    import numpy as np

    width, height = img.size
    rgb = img.convert("RGB")
    arr = np.array(rgb)
    r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]

    # 肤色掩膜
    max_rgb = np.maximum(np.maximum(r, g), b)
    min_rgb = np.minimum(np.minimum(r, g), b)
    skin_mask = (
        (r > 95) & (g > 40) & (b > 20) &
        (max_rgb - min_rgb > 15) &
        (np.abs(r.astype(int) - g.astype(int)) > 15) &
        (r > g) & (r > b)
    )

    gray = np.array(img.convert("L"))
    corners = [
        (0, 0),
        (width - 1, 0),
        (0, height - 1),
        (width - 1, height - 1),
    ]

    suspicious_corners = 0
    corner_size = 60

    for cx, cy in corners:
        x1 = max(0, cx - corner_size)
        y1 = max(0, cy - corner_size)
        x2 = min(width, cx + corner_size)
        y2 = min(height, cy + corner_size)

        region_gray = gray[y1:y2, x1:x2]
        region_skin = skin_mask[y1:y2, x1:x2]

        if region_gray.size == 0:
            continue

        # 排除肤色像素后再计算标准差
        non_skin_mask = ~region_skin
        if non_skin_mask.sum() < region_gray.size * 0.3:
            # 这个角落大部分是肤色，可能是人物的一部分，跳过
            continue

        non_skin_pixels = region_gray[non_skin_mask]
        if len(non_skin_pixels) < 100:
            continue

        std = float(np.std(non_skin_pixels))

        # 检查水平方向的高频变化（文字/水印的特征）
        # 计算每行的差分平均值
        if region_gray.shape[0] > 10:
            row_diffs = np.abs(np.diff(region_gray.astype(float), axis=1)).mean(axis=1)
            high_freq_rows = (row_diffs > 15).sum()
            high_freq_ratio = high_freq_rows / len(row_diffs)
        else:
            high_freq_ratio = 0

        # 同时满足：标准差较高 + 有高频纹理 才认为是水印
        if std > 25 and high_freq_ratio > 0.3:
            suspicious_corners += 1

    # 至少 2 个角落可疑才判定为有水印（更严格，减少误报）
    return suspicious_corners >= 2


def _analyze_face_quality(img: Image.Image, width: int, height: int) -> Dict[str, Any]:
    """分析人脸质量（基于肤色区域形态学分析，不依赖外部库）

    思路：
    1. 用肤色检测找到人脸候选区域
    2. 分析区域的形态（长宽比、填充率、位置）
    3. 综合评分：检测到的区域越大、越接近脸形，分数越高
    """
    import numpy as np

    rgb = img.convert("RGB")
    arr = np.array(rgb)
    r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]

    # 肤色检测（YCbCr 空间阈值，更准确）
    max_rgb = np.maximum(np.maximum(r, g), b)
    min_rgb = np.minimum(np.minimum(r, g), b)
    skin_mask = (
        (r > 95) & (g > 40) & (b > 20) &
        (max_rgb - min_rgb > 15) &
        (np.abs(r.astype(int) - g.astype(int)) > 15) &
        (r > g) & (r > b)
    )

    skin_pixels = int(skin_mask.sum())
    total_pixels = width * height

    if skin_pixels < total_pixels * 0.01:
        return {"face_detected": False, "is_frontal": False, "score": 0.0}

    # 找肤色区域的 bounding box
    ys, xs = np.where(skin_mask)
    min_x, max_x = int(xs.min()), int(xs.max())
    min_y, max_y = int(ys.min()), int(ys.max())

    bbox_w = max_x - min_x + 1
    bbox_h = max_y - min_y + 1
    bbox_area = bbox_w * bbox_h

    # 填充率：肤色像素 / bbox 面积
    fill_ratio = skin_pixels / max(1, bbox_area)

    # 长宽比：正常人脸大约 0.7-1.2（宽:高）
    aspect_ratio = bbox_w / max(1, bbox_h)

    # 人脸中心位置
    center_x = (min_x + max_x) / 2.0
    center_y = (min_y + max_y) / 2.0

    # 判断是否正面：左右对称 + 中心位置 + 长宽比合理
    # 正面人脸的特征：
    # 1. 长宽比在 0.5-1.3 之间
    # 2. 填充率在 0.4-0.85 之间（不是太碎也不是整块）
    # 3. 水平方向基本居中
    face_like = (
        0.4 <= aspect_ratio <= 1.4 and
        0.3 <= fill_ratio <= 0.9 and
        abs(center_x - width / 2) < width * 0.35
    )

    # 正面判定：更宽松的标准
    is_frontal = (
        0.5 <= aspect_ratio <= 1.3 and
        abs(center_x - width / 2) < width * 0.3
    )

    # 综合人脸评分（0-1）
    # 维度：
    # - 肤色区域大小（占画面比例）
    # - 长宽比合理性
    # - 填充率合理性
    # - 是否居中

    size_score = min(1.0, skin_pixels / (total_pixels * 0.15))  # 15% 以上满分

    # 长宽比得分：越接近 0.8 越好
    optimal_ar = 0.8
    ar_deviation = abs(aspect_ratio - optimal_ar)
    ar_score = max(0.0, 1.0 - ar_deviation * 2)

    # 填充率得分：0.6 左右最好
    optimal_fill = 0.6
    fill_deviation = abs(fill_ratio - optimal_fill)
    fill_score = max(0.0, 1.0 - fill_deviation * 2)

    # 居中得分
    center_deviation = abs(center_x - width / 2) / (width / 2)
    center_score = max(0.0, 1.0 - center_deviation * 1.5)

    score = (
        size_score * 0.35 +
        ar_score * 0.25 +
        fill_score * 0.2 +
        center_score * 0.2
    )

    score = max(0.0, min(1.0, score))

    # 如果完全不像人脸，降低分数
    if not face_like:
        score = min(score, 0.4)

    return {
        "face_detected": skin_pixels >= total_pixels * 0.02,
        "is_frontal": is_frontal,
        "score": score,
    }


def check_all_reference_images(
    product_image_path: Optional[Path],
    character_image_paths: Optional[List[Path]] = None,
) -> List[ReferenceImageCheckResult]:
    """检查所有参考图"""
    results = []

    if product_image_path and product_image_path.exists():
        result = check_reference_image(product_image_path, "product")
        results.append(result)

    if character_image_paths:
        for i, char_path in enumerate(character_image_paths):
            if char_path and char_path.exists():
                result = check_reference_image(char_path, "character")
                result.name = f"角色参考图 {i+1}"
                results.append(result)

    return results


def _compute_image_phash(image_path: Path, hash_size: int = 16) -> Optional[str]:
    """
    计算图片的感知哈希（pHash），用于相似图片去重。

    Args:
        image_path: 图片路径
        hash_size: 哈希尺寸（越大越精确但越慢）

    Returns:
        十六进制哈希字符串，失败返回 None
    """
    try:
        with Image.open(image_path) as img:
            img = img.convert("L").resize(
                (hash_size, hash_size), Image.Resampling.LANCZOS
            )
            pixels = list(img.getdata())
            avg = sum(pixels) / len(pixels)
            bits = ["1" if p > avg else "0" for p in pixels]
            bit_str = "".join(bits)
            hex_str = f"{int(bit_str, 2):0{hash_size * hash_size // 4}x}"
            return hex_str
    except Exception:
        return None


def _hamming_distance(hash1: str, hash2: str) -> int:
    """计算两个哈希字符串的汉明距离"""
    if len(hash1) != len(hash2):
        return max(len(hash1), len(hash2)) * 4
    xor = int(hash1, 16) ^ int(hash2, 16)
    return bin(xor).count("1")


def deduplicate_reference_images(
    ref_results: List[ReferenceImageCheckResult],
    *,
    similarity_threshold: int = 12,
) -> Tuple[List[ReferenceImageCheckResult], List[str], int]:
    """
    参考图去重：移除高度相似的图片，保留质量最高的一张。

    利用感知哈希（pHash）检测相似图片，汉明距离小于阈值视为相似。

    Args:
        ref_results: 参考图检查结果列表
        similarity_threshold: 汉明距离阈值（越小越严格，推荐 8-15）

    Returns:
        (去重后的结果列表, 被移除的图片名称列表, 被移除的数量)
    """
    if len(ref_results) <= 1:
        return ref_results, [], 0

    hashes: List[Optional[str]] = []
    for r in ref_results:
        if r.path and r.path.exists():
            h = _compute_image_phash(r.path)
            hashes.append(h)
        else:
            hashes.append(None)

    kept_indices = []
    removed_names = []
    removed_indices = set()

    sorted_indices = sorted(
        range(len(ref_results)),
        key=lambda i: ref_results[i].quality_score,
        reverse=True,
    )

    for idx in sorted_indices:
        if idx in removed_indices:
            continue

        current_hash = hashes[idx]
        if current_hash is None:
            kept_indices.append(idx)
            continue

        is_duplicate = False
        for kept_idx in kept_indices:
            kept_hash = hashes[kept_idx]
            if kept_hash is None:
                continue
            if ref_results[kept_idx].image_type != ref_results[idx].image_type:
                continue
            dist = _hamming_distance(current_hash, kept_hash)
            if dist <= similarity_threshold:
                is_duplicate = True
                removed_indices.add(idx)
                name = getattr(ref_results[idx], "name", "") or ref_results[idx].path.name
                removed_names.append(
                    f"{name}（与 {getattr(ref_results[kept_idx], 'name', '') or ref_results[kept_idx].path.name} 相似，"
                    f"汉明距离 {dist}）"
                )
                break

        if not is_duplicate:
            kept_indices.append(idx)

    kept_indices.sort()
    kept_results = [ref_results[i] for i in kept_indices]

    return kept_results, removed_names, len(removed_names)


def smart_sort_reference_images(
    ref_results: List[ReferenceImageCheckResult],
    *,
    product_first: bool = True,
) -> Tuple[List[ReferenceImageCheckResult], List[str]]:
    """
    参考图智能排序：高质量图前置，利用 AI 模型对前几张参考图权重更高的特性。

    排序规则：
    1. 产品图优先（如果 product_first=True）
    2. 同类型内按质量评分从高到低排序
    3. 有 blocker 的图片排最后

    Args:
        ref_results: 参考图检查结果列表
        product_first: 产品图是否优先（广告视频通常产品最重要）

    Returns:
        (排序后的结果列表, 排序说明列表)
    """
    if len(ref_results) <= 1:
        return ref_results, []

    def sort_key(r: ReferenceImageCheckResult):
        has_blocker = len(r.blockers) > 0
        type_priority = 0 if (product_first and r.image_type == "product") else 1
        return (
            1 if has_blocker else 0,
            type_priority,
            -r.quality_score,
        )

    sorted_results = sorted(ref_results, key=sort_key)

    notes = []
    if sorted_results != ref_results:
        order_desc = " → ".join(
            [f"{getattr(r, 'name', '') or r.path.name}({r.quality_score:.0f}分)"
             for r in sorted_results]
        )
        notes.append(f"参考图已按质量智能排序：{order_desc}")
        if product_first:
            notes.append("产品图优先前置（AI 对前面的参考图权重更高）")

    return sorted_results, notes


# ── Prompt 预校验 ──

def score_prompt_quality(prompt: str) -> Tuple[float, List[str]]:
    """
    评估 Prompt 质量，返回综合评分和评估信息。

    Args:
        prompt: 待评估的 prompt

    Returns:
        (综合评分, 评估信息列表)
    """
    total_score = 0.0
    total_weight = 0.0
    messages = []

    for category, rule in PROMPT_QUALITY_RULES.items():
        weight = rule.get("weight", 0.0)
        total_weight += weight

        for sub_rule in rule.get("rules", []):
            condition = sub_rule.get("condition")
            if condition and condition(prompt):
                total_score += weight * sub_rule.get("score", 0) / 100
                messages.append(sub_rule.get("message", ""))
                break

    if total_weight > 0:
        final_score = total_score / total_weight * 100
    else:
        final_score = 0.0

    return final_score, messages


def check_prompt(
    prompt: str,
    prompt_type: str = "video",
    negative_prompt: str = NEGATIVE_PROMPT,
) -> PromptCheckResult:
    """
    检查单个 Prompt 质量（v2 分级机制）。

    - 🔴 blocker：直接失败（如 prompt 过短、严重语义冲突）
    - 🟡 warning：有改进空间但可用
    - 🔵 info：纯建议
    """
    config = QUALITY_GATE_CONFIG.get("prompt_check", {})
    if not config.get("enabled", True):
        return PromptCheckResult(
            passed=True,
            prompt=prompt[:100] + "..." if len(prompt) > 100 else prompt,
            messages=["Prompt 预检已跳过"],
            score=100.0,
        )

    result = PromptCheckResult(
        passed=True,
        prompt=prompt[:100] + "..." if len(prompt) > 100 else prompt,
        length=len(prompt),
    )

    min_len = config.get("min_length", 50)
    max_len = config.get("max_length", 2000)

    if len(prompt) < min_len:
        result.blockers.append(f"Prompt 过短（{len(prompt)} 字符）")
        result.messages.append(f"🔴 Prompt 过短（{len(prompt)} 字符），要求 >= {min_len}")
        result.suggestions.append("请补充更详细的描述，包括场景、动作、质量要求等")
        result.passed = False

    if len(prompt) > max_len:
        result.warnings.append(f"Prompt 过长（{len(prompt)} 字符）")
        result.messages.append(f"🟡 Prompt 过长（{len(prompt)} 字符），建议 <= {max_len}")
        result.suggestions.append("请精简 Prompt，去除冗余描述")

    required_keywords = config.get("required_keywords", {})
    prompt_lower = prompt.lower()

    for keyword_type, keywords in required_keywords.items():
        missing = [k for k in keywords if k not in prompt_lower]
        if missing:
            result.keyword_missing.extend(missing)
            result.warnings.append(f"缺少{keyword_type}类型关键词")
            result.messages.append(
                f"🟡 缺少{keyword_type}类型关键词：{', '.join(missing)}"
            )
            result.suggestions.append(
                f"建议添加以下关键词：{', '.join(missing)}"
            )

    if config.get("check_negative_prompt", True) and negative_prompt:
        required_negative = config.get("required_negative_keywords", [])
        neg_lower = negative_prompt.lower()
        missing_neg = [k for k in required_negative if k not in neg_lower]
        if missing_neg:
            result.negative_keyword_missing.extend(missing_neg)
            result.warnings.append("缺少负面提示词")
            result.messages.append(
                f"🟡 负面提示词缺少关键抑制词：{', '.join(missing_neg)}"
            )
            result.suggestions.append(
                f"建议在负面提示词中添加：{', '.join(missing_neg)}"
            )

    score, score_messages = score_prompt_quality(prompt)
    result.score = score
    for msg in score_messages:
        result.messages.append(f"🔵 {msg}")

    if score < 60:
        result.blockers.append(f"Prompt 质量评分过低（{score:.1f}/100）")
        result.messages.append(f"🔴 Prompt 质量评分较低（{score:.1f}/100）")
        result.suggestions.append("请优化 Prompt 结构，增加细节描述")
        result.passed = False

    # ── 语义冲突检测 ──
    conflicts = _detect_prompt_conflicts(prompt)
    if conflicts:
        result.conflicts = conflicts
        result.warnings.append("存在语义冲突")
        for conflict in conflicts:
            result.messages.append(f"🟡 检测到语义冲突：{conflict}")
            result.suggestions.append(f"建议解决冲突：{conflict}")

    # ── 重复描述检测 ──
    repetitions = _detect_repetitions(prompt)
    if repetitions:
        result.repetitions = repetitions
        for rep in repetitions:
            result.messages.append(f"🔵 检测到重复描述：{rep}")
            result.suggestions.append(f"建议合并或删除重复描述：{rep}")

    # ── 语义一致性分析 ──
    semantic_issues = _analyze_semantic_consistency(prompt)
    if semantic_issues:
        result.semantic_issues = semantic_issues
        for issue in semantic_issues:
            result.messages.append(f"🔵 语义一致性问题：{issue}")
            result.suggestions.append(f"建议修正：{issue}")

    # ── 自动优化 ──
    optimized = _optimize_prompt(prompt, result)
    if optimized and optimized != prompt:
        result.optimized_prompt = optimized
        result.messages.append("✅ 已生成优化版本的 Prompt")
        result.suggestions.append("建议使用优化后的 Prompt")

        # 如果开启自动应用，验证优化后的 Prompt 是否通过检查
        if config.get("auto_apply_optimization", True):
            opt_conflicts = _detect_prompt_conflicts(optimized)
            opt_score, _ = score_prompt_quality(optimized)
            opt_len = len(optimized)
            opt_len_ok = min_len <= opt_len <= max_len

            opt_passed = (
                opt_len_ok and
                opt_score >= 60 and
                not opt_conflicts
            )

            if opt_passed and not result.passed:
                result.passed = True
                result.score = max(result.score, opt_score)
                result.blockers = [b for b in result.blockers if "质量评分" not in b]
                result.messages.append("✅ 优化后的 Prompt 已通过检查，将使用优化版本")

    return result


CONFLICT_KEYWORDS = [
    (["close-up", "extreme close-up"], ["wide shot", "full shot", "establishing shot"]),
    (["portrait", "headshot"], ["full body", "long shot"]),
    (["black", "dark", "night"], ["white", "bright", "day"]),
    (["indoor", "inside", "room"], ["outdoor", "outside", "street"]),
    (["front view", "front-facing"], ["back view", "rear", "profile"]),
    (["still", "static", "motionless"], ["moving", "dynamic", "action"]),
    (["soft focus", "blur"], ["sharp", "crisp", "focused"]),
    (["low angle", "looking up"], ["high angle", "looking down"]),
]


def _detect_prompt_conflicts(prompt: str) -> List[str]:
    """检测 Prompt 中的语义冲突"""
    prompt_lower = prompt.lower()
    conflicts = []

    for positives, negatives in CONFLICT_KEYWORDS:
        has_positive = any(p in prompt_lower for p in positives)
        has_negative = any(n in prompt_lower for n in negatives)

        if has_positive and has_negative:
            pos_list = [p for p in positives if p in prompt_lower]
            neg_list = [n for n in negatives if n in prompt_lower]
            conflicts.append(f"{', '.join(pos_list)} 与 {', '.join(neg_list)}")

    return conflicts


def _detect_repetitions(prompt: str) -> List[str]:
    """检测重复描述（连续或近似重复）"""
    words = prompt.lower().split()
    repetitions = []

    for i in range(len(words) - 2):
        triplet = " ".join(words[i:i+3])
        for j in range(i + 3, len(words) - 2):
            other_triplet = " ".join(words[j:j+3])
            if triplet == other_triplet:
                repetitions.append(f"'{triplet}' 出现多次")
                break

    for i in range(len(words) - 1):
        if words[i] == words[i + 1]:
            repetitions.append(f"'{words[i]}' 连续重复")

    return list(set(repetitions))


def _analyze_semantic_consistency(prompt: str) -> List[str]:
    """分析语义一致性"""
    issues = []
    prompt_lower = prompt.lower()

    if "same" in prompt_lower and "reference" not in prompt_lower:
        issues.append("使用了 'same' 但未指定参考对象")

    if "holding" in prompt_lower and "product" not in prompt_lower and "item" not in prompt_lower:
        issues.append("描述了 'holding' 动作但未说明持有什么物品")

    if "cinematic" in prompt_lower and "lighting" not in prompt_lower and "composition" not in prompt_lower:
        issues.append("使用了 'cinematic' 但未描述具体的电影化元素")

    return issues


def _optimize_prompt(prompt: str, result: PromptCheckResult) -> str:
    """根据检查结果自动优化 Prompt"""
    optimized = prompt

    # 自动修复语义冲突：保留叙事功能对应的那一侧，移除冲突侧
    if result.conflicts:
        import re
        words_lower = optimized.lower()
        for positives, negatives in CONFLICT_KEYWORDS:
            # 直接检查 prompt 中是否同时存在两侧的关键词
            has_pos = any(w in words_lower for w in positives)
            has_neg = any(w in words_lower for w in negatives)
            if not (has_pos and has_neg):
                continue

            # 找到所有冲突词在 prompt 中的位置
            pos_positions = {}
            neg_positions = {}
            for w in positives:
                idx = words_lower.find(w)
                if idx >= 0:
                    pos_positions[w] = idx
            for w in negatives:
                idx = words_lower.find(w)
                if idx >= 0:
                    neg_positions[w] = idx

            # 如果两侧都有，移除出现位置靠后的那些词
            if pos_positions and neg_positions:
                max_pos = max(pos_positions.values()) if pos_positions else -1
                max_neg = max(neg_positions.values()) if neg_positions else -1
                # 移除更靠后的那一侧的词
                words_to_remove = neg_positions.keys() if max_neg > max_pos else pos_positions.keys()
                for w in words_to_remove:
                    optimized = re.sub(rf',?\s*{re.escape(w)},?\s*', ', ', optimized, flags=re.IGNORECASE)
            # 清理多余的逗号
            optimized = optimized.replace(' ,', ',').replace(',,', ',').strip(', ').strip()
            words_lower = optimized.lower()

    if result.keyword_missing:
        for keyword in result.keyword_missing[:3]:
            if keyword not in optimized.lower():
                optimized += f", {keyword}"

    if result.repetitions:
        words = optimized.split()
        seen = set()
        unique_words = []
        for word in words:
            if word.lower() not in seen or len(word) < 3:
                seen.add(word.lower())
                unique_words.append(word)
        optimized = " ".join(unique_words)

    return optimized


# ── v2 新增：按分镜类型注入针对性增强词 ──

# 不同分镜类型的视觉增强关键词（按优先级排序）
SEGMENT_TYPE_ENHANCEMENTS = {
    "hook": {
        "keywords": [
            "eye-catching composition", "dramatic lighting", "strong visual impact",
            "dynamic camera angle", "cinematic opening shot", "attention-grabbing",
            "bold composition", "striking visual", "shallow depth of field",
        ],
        "description": "钩子段：强视觉冲击力，抓注意力",
    },
    "pain": {
        "keywords": [
            "emotional lighting", "moody atmosphere", "relatable expression",
            "authentic performance", "natural acting", "subtle emotion",
            "warm lighting", "intimate moment", "genuine feeling",
        ],
        "description": "痛点段：情绪共鸣，真实感",
    },
    "showcase": {
        "keywords": [
            "product photography style", "clean product shot", "sharp focus on product",
            "studio lighting", "professional product presentation", "crystal clear detail",
            "texture detail", "premium finish", "commercial product quality",
        ],
        "description": "展示段：产品清晰，商业质感",
    },
    "result": {
        "keywords": [
            "happy family moment", "warm joyful atmosphere", "lifestyle photography",
            "bright cheerful lighting", "authentic smile", "relatable scene",
            "comfortable home environment", "peaceful mood", "satisfied expression",
        ],
        "description": "效果段：美好生活，真实感",
    },
    "cta": {
        "keywords": [
            "bold text overlay", "strong call to action", "confident presentation",
            "professional spokesperson", "direct eye contact", "trustworthy expression",
            "clean graphic design", "brand logo prominent", "memorable closing shot",
        ],
        "description": "行动号召段：信任感，品牌记忆点",
    },
}


def detect_segment_type(prompt: str) -> str:
    """从 prompt 内容推断分镜类型"""
    prompt_lower = prompt.lower()

    type_keywords = {
        "hook": ["opening", "intro", "hook", "establishing", "wide shot", "first scene"],
        "pain": ["worried", "stress", "problem", "sad", "concerned", "tired", "pain", "struggle"],
        "showcase": ["product", "showcase", "display", "present", "demonstrate", "feature", "packaging", "box"],
        "result": ["happy", "smile", "joy", "relieved", "satisfied", "family together", "peaceful", "enjoying"],
        "cta": ["call to action", "cta", "buy now", "order", "subscribe", "learn more", "visit", "logo", "brand"],
    }

    best_type = "showcase"
    best_score = 0
    for seg_type, keywords in type_keywords.items():
        score = sum(1 for k in keywords if k in prompt_lower)
        if score > best_score:
            best_score = score
            best_type = seg_type

    return best_type


def enhance_prompt_by_segment_type(prompt: str, segment_type: str = "") -> str:
    """
    按分镜类型注入针对性增强词，提高生成质量。

    Args:
        prompt: 原始 prompt
        segment_type: 分镜类型（hook/pain/showcase/result/cta），为空则自动推断

    Returns:
        增强后的 prompt
    """
    if not segment_type:
        segment_type = detect_segment_type(prompt)

    enhancements = SEGMENT_TYPE_ENHANCEMENTS.get(segment_type, {})
    if not enhancements:
        return prompt

    keywords = enhancements.get("keywords", [])
    if not keywords:
        return prompt

    prompt_lower = prompt.lower()
    new_keywords = []
    for kw in keywords:
        if kw.lower() not in prompt_lower:
            new_keywords.append(kw)
            if len(new_keywords) >= 4:  # 最多注入 4 个增强词
                break

    if not new_keywords:
        return prompt

    # 加在末尾，用质量增强区块的形式
    enhanced = prompt.rstrip()
    if not enhanced.endswith(","):
        enhanced += ","
    enhanced += " " + ", ".join(new_keywords)

    return enhanced


# ── v2 新增：场景化负面提示词增强 ──

# 不同场景类型的额外负面词（针对该场景的高风险问题）
SCENE_NEGATIVE_ENHANCEMENTS = {
    "character_closeup": {
        "detect_keywords": ["close-up", "close up", "portrait", "face shot", "facial", "headshot"],
        "extra_negative": [
            "asymmetric face", "lopsided face", "uneven eyebrows",
            "misaligned eyes", "different sized eyes", "wandering eye",
            "deformed nose", "crooked nose", "uneven nostrils",
            "distorted lips", "melting mouth", "tooth distortion",
            "plastic looking face", "uncanny valley", "doll-like appearance",
            "over-smoothed skin", "waxy skin texture", "airbrushed look",
        ],
    },
    "product_showcase": {
        "detect_keywords": ["product", "showcase", "display", "packaging", "box", "bottle"],
        "extra_negative": [
            "distorted logo", "wrong logo", "blurry text", "unreadable text",
            "misspelled text", "nonsense text", "gibberish writing",
            "warped packaging", "bent box", "crushed product",
            "unnatural reflections", "glass distortion", "plastic look",
            "inconsistent branding", "wrong colors", "faded product",
        ],
    },
    "action_motion": {
        "detect_keywords": ["walking", "running", "moving", "action", "dynamic", "jumping"],
        "extra_negative": [
            "motion blur", "ghosting effect", "trailing artifacts",
            "strobing", "frame skipping", "jerky motion",
            "rubber body", "elastic limbs", "stretching arms",
            "floating feet", "sliding on ground", "unnatural gait",
            "temporal aliasing", "frame doubling", "freeze frame effect",
        ],
    },
    "indoor_scene": {
        "detect_keywords": ["living room", "bedroom", "kitchen", "office", "indoor", "inside", "room"],
        "extra_negative": [
            "floating furniture", "deformed interior", "impossible architecture",
            "mismatched perspective", "warped walls", "bent ceiling",
            "window view distortion", "mirror reflection error",
            "unnatural shadows", "flat lighting", "depthless scene",
        ],
    },
    "outdoor_scene": {
        "detect_keywords": ["outdoor", "outside", "street", "park", "nature", "sky", "building"],
        "extra_negative": [
            "deformed buildings", "impossible architecture", "warped structures",
            "melting sky", "cloud artifacts", "unnatural horizon",
            "floating trees", "distorted perspective", "scale inconsistency",
            "overexposed sky", "blown out clouds", "washed out background",
        ],
    },
}


def detect_scene_type(prompt: str) -> List[str]:
    """从 prompt 内容推断场景类型，返回匹配的类型列表"""
    prompt_lower = prompt.lower()
    matched = []
    for scene_type, config in SCENE_NEGATIVE_ENHANCEMENTS.items():
        for kw in config.get("detect_keywords", []):
            if kw in prompt_lower:
                matched.append(scene_type)
                break
    return matched


def enhance_negative_prompt(base_negative: str, prompt: str) -> str:
    """
    根据场景类型智能增强负面提示词。

    Args:
        base_negative: 基础负面提示词
        prompt: 正向 prompt（用于推断场景）

    Returns:
        增强后的负面提示词
    """
    scene_types = detect_scene_type(prompt)
    if not scene_types:
        return base_negative

    extra_words = set()
    base_lower = base_negative.lower()

    for st in scene_types:
        config = SCENE_NEGATIVE_ENHANCEMENTS.get(st, {})
        for word in config.get("extra_negative", []):
            if word.lower() not in base_lower:
                extra_words.add(word)

    if not extra_words:
        return base_negative

    # 最多加 10 个，避免负面词过长
    extra_list = list(extra_words)[:10]
    enhanced = base_negative.rstrip()
    if not enhanced.endswith(","):
        enhanced += ","
    enhanced += " " + ", ".join(extra_list)

    return enhanced


# 失败类型 → 针对性负面词映射
FAILURE_NEGATIVE_MAPPING = {
    "quality_low_resolution": [
        "blurry", "low resolution", "pixelated", "grainy", "noisy",
        "soft focus", "out of focus", "unclear",
    ],
    "quality_black_frame": [
        "black screen", "all black", "dark frame", "underexposed",
        "too dark", "pitch black",
    ],
    "quality_duration": [
        "frozen", "static frame", "no movement", "still image",
    ],
    "character_face_distortion": [
        "deformed face", "ugly face", "extra fingers", "extra limbs",
        "mutated hands", "bad anatomy", "distorted features",
        "crooked teeth", "asymmetrical face", "cross-eyed",
    ],
    "character_inconsistency": [
        "different face", "changing appearance", "inconsistent character",
        "face swap", "different person",
    ],
    "product_inconsistency": [
        "different product", "changing product", "wrong shape",
        "inconsistent design", "color change",
    ],
    "image_quality_low": [
        "low quality", "worst quality", "poor quality",
        "jpeg artifacts", "compression artifacts",
    ],
    "image_first_failed": [
        "blurry", "distorted", "ugly", "low quality",
    ],
    "video_generation_failed": [
        "blurry", "distorted", "flickering", "jittery",
    ],
}


def enhance_negative_prompt_v2(
    base_negative: str,
    prompt: str = "",
    *,
    product_category: str = "default",
    lookback_days: int = 30,
    max_extra: int = 8,
) -> Tuple[str, List[str], Dict[str, float]]:
    """
    负面词智能增强 v2：基于历史失败数据动态调整。

    进化机制：某类失败发生越多，对应负面词权重越高。

    Returns:
        (增强后的负面词, 新增的负面词列表, 各类失败率 dict)
    """
    # 先做 v1 的场景增强
    enhanced = enhance_negative_prompt(base_negative, prompt)
    added_v1 = []

    # 查询历史失败率
    failure_rates = {}
    for fail_type in FAILURE_NEGATIVE_MAPPING.keys():
        _, total, rate = get_failure_rate_by_type(
            fail_type,
            product_category=product_category,
            lookback_days=lookback_days,
        )
        failure_rates[fail_type] = rate

    # 没有足够历史数据，直接返回 v1 结果
    total_cases = sum(1 for _ in failure_rates.values())  # 粗略估计
    high_risk_types = [ft for ft, rate in failure_rates.items() if rate > 0.05]

    if not high_risk_types:
        return enhanced, [], failure_rates

    # 按失败率从高到低排序
    high_risk_types.sort(key=lambda ft: failure_rates[ft], reverse=True)

    extra_words = set()
    enhanced_lower = enhanced.lower()

    for fail_type in high_risk_types:
        words = FAILURE_NEGATIVE_MAPPING.get(fail_type, [])
        rate = failure_rates[fail_type]

        # 失败率越高，加的词越多（最多 4 个）
        max_for_type = min(4, max(1, int(rate * 20)))
        added = 0

        for word in words:
            if word.lower() not in enhanced_lower:
                extra_words.add(word)
                added += 1
                if added >= max_for_type:
                    break

        if len(extra_words) >= max_extra:
            break

    if not extra_words:
        return enhanced, [], failure_rates

    # 限制总数
    extra_list = list(extra_words)[:max_extra]
    enhanced = enhanced.rstrip()
    if not enhanced.endswith(","):
        enhanced += ","
    enhanced += " " + ", ".join(extra_list)

    return enhanced, extra_list, failure_rates


# ── v2 新增：参考图自动预处理 ──

def auto_preprocess_reference_image(
    image_path: Path,
    image_type: str = "character",
    output_path: Optional[Path] = None,
) -> Tuple[Path, Dict[str, Any]]:
    """
    自动预处理参考图，提升质量，避免重新生成。

    处理内容：
    1. 主体偏小 → 自动裁剪放大
    2. 亮度/对比度不佳 → 自动调整
    3. 背景过于复杂 → 尝试简化（轻度边缘模糊）

    Args:
        image_path: 原图路径
        image_type: 图片类型（character/product）
        output_path: 输出路径，默认在原图同目录加 _optimized 后缀

    Returns:
        (处理后图片路径, 处理详情)
    """
    from PIL import Image, ImageEnhance, ImageFilter
    import numpy as np

    info = {
        "original_path": image_path,
        "processed": False,
        "operations": [],
    }

    if not image_path.exists():
        return image_path, info

    try:
        img = Image.open(image_path).convert("RGB")
    except Exception:
        return image_path, info

    width, height = img.size
    modified = False

    # 1. 检测主体占比，如果偏小则裁剪放大
    subject_ratio = _analyze_subject_size(img)
    target_ratio = 0.5 if image_type == "character" else 0.4

    if subject_ratio < target_ratio:
        is_centered, _ = _analyze_subject_center(img)
        if is_centered:
            # 估算裁剪比例，把主体放大到目标比例
            crop_ratio = min(0.9, target_ratio / max(0.1, subject_ratio))
            crop_ratio = min(crop_ratio, 1.6)  # 最多放大 1.6 倍，避免太糊
            if crop_ratio > 1.1:  # 至少放大 10% 才值得处理
                new_w = int(width / crop_ratio)
                new_h = int(height / crop_ratio)
                left = (width - new_w) // 2
                top = (height - new_h) // 2
                img = img.crop((left, top, left + new_w, top + new_h))
                img = img.resize((width, height), Image.LANCZOS)
                info["operations"].append(f"裁剪放大（{crop_ratio:.2f}x）")
                modified = True

    # 2. 自动调整亮度
    rgb_arr = np.array(img)
    brightness = float(np.mean(rgb_arr))

    if brightness < 100:
        # 偏暗，提亮
        factor = min(1.5, 120 / max(50, brightness))
        enhancer = ImageEnhance.Brightness(img)
        img = enhancer.enhance(factor)
        info["operations"].append(f"提亮（{factor:.2f}x）")
        modified = True
    elif brightness > 235:
        # 过亮，压暗
        factor = max(0.7, 210 / max(100, brightness))
        enhancer = ImageEnhance.Brightness(img)
        img = enhancer.enhance(factor)
        info["operations"].append(f"压暗（{factor:.2f}x）")
        modified = True

    # 3. 自动调整对比度
    from PIL import ImageStat
    stat = ImageStat.Stat(img.convert("L"))
    contrast = stat.stddev[0]

    if contrast < 30:
        # 对比度过低，增强
        factor = min(1.4, 45 / max(10, contrast))
        enhancer = ImageEnhance.Contrast(img)
        img = enhancer.enhance(factor)
        info["operations"].append(f"增强对比度（{factor:.2f}x）")
        modified = True

    if not modified:
        info["processed"] = False
        return image_path, info

    # 保存处理后的图片
    if output_path is None:
        stem = image_path.stem
        suffix = image_path.suffix
        output_path = image_path.parent / f"{stem}_optimized{suffix}"

    img.save(output_path, quality=95)
    info["processed"] = True
    info["output_path"] = output_path
    info["original_subject_ratio"] = subject_ratio
    info["original_brightness"] = brightness

    return output_path, info


# ── v2 新增：Prompt 结构化重排（黄金顺序） ──

# 扩散模型注意力"前重后轻"，按重要性从高到低排列：
# 场景环境 → 主体人物 → 动作表情 → 产品元素 → 镜头语言 → 光影色调 → 风格质量 → 技术参数
PROMPT_SECTION_PATTERNS = {
    "scene": [
        r'scene:', r'location:', r'environment:', r'background:', r'interior:', r'exterior:',
        r'living room', r'bedroom', r'kitchen', r'office', r'street', r'park', r'outdoor', r'indoor',
    ],
    "subject": [
        r'character:', r'woman', r'man', r'child', r'person', r'female', r'male',
        r'girl', r'boy', r'old', r'young', r'-year-old', r'wearing', r'outfit',
    ],
    "action": [
        r'holding', r'walking', r'sitting', r'standing', r'looking', r'smiling',
        r'talking', r'working', r'running', r'pointing', r'gesturing',
        r'expression:', r'mood:', r'emotion:',
    ],
    "product": [
        r'product:', r'packaging:', r'box:', r'bottle:', r'package:',
        r'insurance', r'policy', r'document',
    ],
    "camera": [
        r'close-up', r'close up', r'medium shot', r'wide shot', r'full shot',
        r'camera angle', r'shot type', r'lens', r'focal length',
        r'camera movement', r'panning', r'tracking', r'dolly',
    ],
    "lighting": [
        r'lighting:', r'light', r'sunlight', r'soft light', r'hard light',
        r'warm light', r'cool light', r'studio lighting', r'natural lighting',
        r'shadows', r'highlights', r'golden hour', r'blue hour',
    ],
    "style": [
        r'cinematic', r'filmic', r'photorealistic', r'realistic',
        r'professional', r'commercial', r'advertising', r'lifestyle',
        r'documentary', r'moody', r'atmospheric',
    ],
    "quality": [
        r'4k', r'8k', r'high quality', r'sharp', r'detailed', r'high resolution',
        r'best quality', r'masterpiece', r'ultra detailed',
    ],
    "technical": [
        r'9:16', r'16:9', r'1:1', r'vertical', r'horizontal',
        r'slow motion', r'time-lapse',
    ],
}


def reorder_prompt_golden(prompt: str) -> str:
    """
    按"黄金结构"重排 Prompt，利用扩散模型前重后轻特性。

    顺序：场景 → 主体 → 动作 → 产品 → 镜头 → 光影 → 风格 → 质量 → 技术

    Args:
        prompt: 原始 prompt

    Returns:
        重排后的 prompt
    """
    import re

    # 按逗号分割成词组
    raw_parts = [p.strip() for p in prompt.split(',') if p.strip()]
    if len(raw_parts) < 5:
        return prompt  # 太短不用排

    sections = {k: [] for k in PROMPT_SECTION_PATTERNS.keys()}
    unclassified = []

    for part in raw_parts:
        part_lower = part.lower()
        classified = False
        for section, patterns in PROMPT_SECTION_PATTERNS.items():
            for pat in patterns:
                if re.search(pat, part_lower):
                    sections[section].append(part)
                    classified = True
                    break
            if classified:
                break
        if not classified:
            unclassified.append(part)

    # 按黄金顺序拼接
    ordered = []
    section_order = [
        "scene", "subject", "action", "product",
        "camera", "lighting", "style", "quality", "technical"
    ]
    for sec in section_order:
        ordered.extend(sections[sec])

    # 未分类的放在中间靠后位置（假设是描述性内容）
    # 把未分类插入到 style 前面（如果是场景/主体描述，位置还可以）
    # 实际上放到 action 后面更安全
    if unclassified:
        # 重新组合：scene+subject+action + unclassified + product+camera+lighting+style+quality+technical
        front = []
        for sec in ["scene", "subject", "action"]:
            front.extend(sections[sec])
        back = []
        for sec in ["product", "camera", "lighting", "style", "quality", "technical"]:
            back.extend(sections[sec])
        ordered = front + unclassified + back

    if not ordered:
        return prompt

    result = ", ".join(ordered)
    return result if result else prompt


# ── v2 新增：镜头语言智能注入 ──

# 不同分镜类型的推荐镜头语言（景别 + 运镜 + 焦距）
SEGMENT_CINEMATOGRAPHY = {
    "hook": {
        "shot_type": "wide establishing shot",
        "camera_movement": "slow push-in",
        "lens": "35mm lens, deep focus",
        "rationale": "钩子段用全景建立场景，慢推进营造代入感",
    },
    "pain": {
        "shot_type": "medium close-up",
        "camera_movement": "slow handheld drift",
        "lens": "50mm lens, shallow depth of field",
        "rationale": "痛点段用中近景聚焦情绪，浅景深突出人物",
    },
    "showcase": {
        "shot_type": "product close-up",
        "camera_movement": "smooth orbit around product",
        "lens": "85mm macro lens, tack sharp focus",
        "rationale": "展示段用产品特写+环绕运镜，微距保证细节清晰",
    },
    "result": {
        "shot_type": "medium wide shot",
        "camera_movement": "gentle lateral tracking",
        "lens": "35mm lens, warm soft focus",
        "rationale": "效果段用中全景展现环境，侧移营造生活感",
    },
    "cta": {
        "shot_type": "medium shot",
        "camera_movement": "static with subtle push-in",
        "lens": "50mm lens, crisp focus",
        "rationale": "CTA 段用中景+稳定画面，让观众关注信息",
    },
}


# ── v2 新增：智能电影风格自动匹配 ──

# 产品品类 → 推荐电影风格映射（基于情绪和调性匹配）
PRODUCT_TO_CINEMATIC_STYLE = {
    "美妆": "wong-kar-wai",
    "护肤": "wong-kar-wai",
    "个护": "spielberg",
    "食品": "spielberg",
    "饮料": "spielberg",
    "家居": "anderson",
    "家具": "anderson",
    "房产": "nolan",
    "汽车": "nolan",
    "数码": "nolan",
    "科技": "nolan",
    "app": "nolan",
    "教育": "spielberg",
    "医疗": "kubrick",
    "金融": "hitchcock",
    "保险": "hitchcock",
    "服饰": "wong-kar-wai",
    "服装": "wong-kar-wai",
    "奢侈品": "kubrick",
    "珠宝": "kubrick",
    "旅行": "nolan",
    "游戏": "aronofsky",
    "运动": "scorsese",
    "健身": "scorsese",
    "母婴": "spielberg",
    "宠物": "anderson",
    "default": "spielberg",
}

# 基础电影质感增强词（默认注入，提升整体画面质感）
CINEMATIC_BASE_ENHANCEMENTS = {
    "lighting": [
        "soft rim light creating subtle halo",
        "side lighting with gentle shadows",
        "natural window light with soft diffusion",
    ],
    "color": [
        "cinematic color grading",
        "teal and orange color palette",
        "rich filmic tones",
    ],
    "lens": [
        "shallow depth of field",
        "beautiful bokeh background",
        "35mm prime lens look",
    ],
    "film": [
        "subtle film grain",
        "high dynamic range",
        "cinematic composition",
    ],
}


def smart_pick_cinematic_style(
    product_category: str = "default",
    story_world_desc: str = "",
    ad_script: Optional[Dict[str, Any]] = None,
) -> str:
    """
    智能选择最合适的电影风格（零配置自动匹配）。

    根据产品品类、场景氛围、脚本调性综合判断，
    不需要用户手动指定风格。

    Args:
        product_category: 产品品类
        story_world_desc: 故事世界描述
        ad_script: 广告脚本（可选，用于更精准的匹配）

    Returns:
        电影风格 key（如 "spielberg"、"nolan" 等）
    """
    from config import CINEMATIC_STYLES

    category_lower = product_category.lower() if product_category else "default"

    best_style = PRODUCT_TO_CINEMATIC_STYLE.get(product_category, None)
    if best_style is None:
        for key, style in PRODUCT_TO_CINEMATIC_STYLE.items():
            if key in category_lower or category_lower in key:
                best_style = style
                break

    if best_style is None:
        best_style = PRODUCT_TO_CINEMATIC_STYLE["default"]

    if story_world_desc:
        desc_lower = story_world_desc.lower()
        if any(w in desc_lower for w in ["suspense", "mystery", "thriller", "anxiety", "fear"]):
            if "hitchcock" in CINEMATIC_STYLES:
                best_style = "hitchcock"
        elif any(w in desc_lower for w in ["epic", "grand", "vast", "space", "future"]):
            if "nolan" in CINEMATIC_STYLES:
                best_style = "nolan"
        elif any(w in desc_lower for w in ["warm", "cozy", "family", "home", "love"]):
            if "spielberg" in CINEMATIC_STYLES:
                best_style = "spielberg"
        elif any(w in desc_lower for w in ["fashion", "romantic", "dreamy", "beautiful"]):
            if "wong-kar-wai" in CINEMATIC_STYLES:
                best_style = "wong-kar-wai"

    if best_style not in CINEMATIC_STYLES:
        available = list(CINEMATIC_STYLES.keys())
        if available:
            best_style = available[0]
        else:
            return "spielberg"

    return best_style


def inject_base_cinematic_quality(
    prompt: str,
    *,
    intensity: str = "medium",
) -> Tuple[str, List[str]]:
    """
    注入基础电影质感增强词（默认开启，零配置提升画面高级感）。

    不改变画面内容，只提升质感：
    - 光影：轮廓光、侧光、柔和阴影
    - 色彩：电影级调色、青橙色调
    - 镜头：浅景深、焦外虚化、35mm 镜头感
    - 胶片：细微颗粒、高动态范围

    Args:
        prompt: 原始 prompt
        intensity: 增强强度（light/medium/strong）

    Returns:
        (增强后的 prompt, 新增的增强词列表)
    """
    prompt_lower = prompt.lower()

    added = []

    def _add_if_absent(phrase: str, check_keywords: List[str]) -> bool:
        if any(kw in prompt_lower for kw in check_keywords):
            return False
        added.append(phrase)
        return True

    if intensity in ("medium", "strong"):
        _add_if_absent(
            "soft rim light",
            ["rim light", "backlight", "halo light"]
        )
        _add_if_absent(
            "cinematic color grading",
            ["color grading", "cinematic color", "film color"]
        )
        _add_if_absent(
            "shallow depth of field",
            ["depth of field", "bokeh", "shallow focus", "blurred background"]
        )

    if intensity == "strong":
        _add_if_absent(
            "subtle film grain",
            ["film grain", "grainy", "filmic texture"]
        )
        _add_if_absent(
            "high dynamic range",
            ["high dynamic range", "hdr"]
        )
        _add_if_absent(
            "35mm prime lens look",
            ["35mm", "lens look", "anamorphic"]
        )

    if not added:
        return prompt, []

    enhanced = prompt.rstrip(" ,")
    if enhanced and not enhanced.endswith(","):
        enhanced += ","
    enhanced += " " + ", ".join(added)

    return enhanced, added


# ── v2 新增：智能口播风格匹配 ──

PRODUCT_TO_VOICEOVER_STYLE = {
    "美妆": "emotional",
    "护肤": "emotional",
    "个护": "energetic",
    "食品": "energetic",
    "饮料": "energetic",
    "家居": "storytelling",
    "房产": "professional",
    "汽车": "professional",
    "数码": "professional",
    "科技": "professional",
    "app": "energetic",
    "教育": "storytelling",
    "医疗": "professional",
    "金融": "professional",
    "保险": "storytelling",
    "服饰": "emotional",
    "服装": "emotional",
    "奢侈品": "professional",
    "旅行": "storytelling",
    "游戏": "energetic",
    "运动": "energetic",
    "健身": "energetic",
    "母婴": "emotional",
    "宠物": "storytelling",
    "default": "standard",
}


def smart_pick_voiceover_style(product_category: str = "default") -> str:
    """
    智能选择最合适的口播风格（零配置自动匹配）。

    根据产品品类匹配最适合的音色和语气。

    Args:
        product_category: 产品品类

    Returns:
        口播风格 key
    """
    style = PRODUCT_TO_VOICEOVER_STYLE.get(product_category)
    if style:
        return style

    cat_lower = product_category.lower() if product_category else ""
    for key, s in PRODUCT_TO_VOICEOVER_STYLE.items():
        if key in cat_lower or cat_lower in key:
            return s

    return PRODUCT_TO_VOICEOVER_STYLE["default"]


def inject_cinematography(prompt: str, segment_type: str = "") -> str:
    """
    按分镜类型注入专业镜头语言，提升电影感和画面质量。

    Args:
        prompt: 原始 prompt
        segment_type: 分镜类型（hook/pain/showcase/result/cta），为空则自动推断

    Returns:
        注入镜头语言后的 prompt
    """
    if not segment_type:
        segment_type = detect_segment_type(prompt)

    cine = SEGMENT_CINEMATOGRAPHY.get(segment_type)
    if not cine:
        return prompt

    prompt_lower = prompt.lower()
    new_parts = []

    # 检查是否已有类似描述，避免重复
    shot = cine["shot_type"]
    movement = cine["camera_movement"]
    lens = cine["lens"]

    if shot.lower() not in prompt_lower:
        # 检查是否有同类词（不精确匹配但语义相近）
        has_shot = any(k in prompt_lower for k in ["close-up", "wide shot", "medium shot", "full shot"])
        if not has_shot:
            new_parts.append(shot)

    if movement.lower() not in prompt_lower:
        has_movement = any(k in prompt_lower for k in ["camera movement", "panning", "tracking", "dolly", "static"])
        if not has_movement:
            new_parts.append(movement)

    if lens.lower() not in prompt_lower:
        has_lens = any(k in prompt_lower for k in ["mm lens", "focal length", "depth of field"])
        if not has_lens:
            new_parts.append(lens)

    if not new_parts:
        return prompt

    # 注入到 prompt 中间偏后位置（镜头语言在动作之后、光影之前）
    enhanced = prompt.rstrip()
    if not enhanced.endswith(","):
        enhanced += ","
    enhanced += " " + ", ".join(new_parts)

    return enhanced


# ── v2 新增：颜色调色板锚定（提升片段间一致性） ──

# 常见场景的预设调色板（色温 + 主色调 + 氛围色）
SCENE_COLOR_PALETTES = {
    "warm_home": {
        "name": "暖调家庭",
        "color_temp": "warm color temperature",
        "primary": "soft beige and cream tones",
        "accent": "warm golden highlights",
        "mood": "cozy inviting atmosphere",
        "detect_keywords": ["living room", "home", "family", "warm", "cozy", "bedroom", "kitchen"],
    },
    "cool_modern": {
        "name": "冷调现代",
        "color_temp": "cool neutral color temperature",
        "primary": "clean white and gray tones",
        "accent": "soft blue highlights",
        "mood": "professional modern atmosphere",
        "detect_keywords": ["office", "modern", "tech", "clean", "minimal", "studio", "professional"],
    },
    "bright_outdoor": {
        "name": "明亮户外",
        "color_temp": "bright daylight",
        "primary": "vibrant natural colors",
        "accent": "sunny warm highlights",
        "mood": "cheerful energetic atmosphere",
        "detect_keywords": ["outdoor", "park", "street", "sunny", "daylight", "nature", "outside"],
    },
    "cinematic_dark": {
        "name": "电影感暗调",
        "color_temp": "warm low-key lighting",
        "primary": "deep shadows and rich tones",
        "accent": "dramatic rim lighting",
        "mood": "cinematic dramatic atmosphere",
        "detect_keywords": ["dramatic", "moody", "dark", "cinematic", "night", "evening", "low key"],
    },
    "soft_pastel": {
        "name": "柔和马卡龙",
        "color_temp": "soft warm daylight",
        "primary": "pastel pink and cream tones",
        "accent": "gentle diffused highlights",
        "mood": "soft dreamy atmosphere",
        "detect_keywords": ["beauty", "skincare", "feminine", "soft", "gentle", "dreamy"],
    },
}


def detect_color_palette(prompt: str, story_world_desc: str = "") -> dict:
    """
    从 prompt 和故事世界描述推断最匹配的调色板。

    Args:
        prompt: 当前片段 prompt
        story_world_desc: 故事世界整体描述

    Returns:
        调色板配置字典
    """
    combined = (prompt + " " + story_world_desc).lower()

    best_palette = "warm_home"
    best_score = 0

    for palette_key, palette in SCENE_COLOR_PALETTES.items():
        score = sum(1 for kw in palette.get("detect_keywords", []) if kw in combined)
        if score > best_score:
            best_score = score
            best_palette = palette_key

    return SCENE_COLOR_PALETTES[best_palette]


def inject_color_palette(prompt: str, palette: dict) -> str:
    """
    注入颜色调色板锚定词，提升片段间视觉一致性。

    Args:
        prompt: 原始 prompt
        palette: 调色板配置

    Returns:
        注入调色板后的 prompt
    """
    if not palette:
        return prompt

    prompt_lower = prompt.lower()
    new_parts = []

    color_temp = palette.get("color_temp", "")
    primary = palette.get("primary", "")
    accent = palette.get("accent", "")
    mood = palette.get("mood", "")

    if color_temp and color_temp.lower() not in prompt_lower:
        new_parts.append(color_temp)
    if primary and primary.lower() not in prompt_lower:
        has_primary = any(k in prompt_lower for k in ["color tone", "color palette", "color scheme"])
        if not has_primary:
            new_parts.append(primary)
    if accent and accent.lower() not in prompt_lower:
        new_parts.append(accent)
    if mood and mood.lower() not in prompt_lower:
        has_mood = any(k in prompt_lower for k in ["atmosphere", "mood"])
        if not has_mood:
            new_parts.append(mood)

    if not new_parts:
        return prompt

    # 注入到光影/风格区域附近（放在 prompt 后半段）
    enhanced = prompt.rstrip()
    if not enhanced.endswith(","):
        enhanced += ","
    enhanced += " " + ", ".join(new_parts[:3])  # 最多加 3 个

    return enhanced


# ── v2 新增：运动强度检测与校准 ──

MOTION_INTENSITY_KEYWORDS = {
    "high": [
        "fast", "rapid", "quick", "sudden", "abrupt", "dynamic action",
        "running", "jumping", "dancing", "fighting", "chasing",
        "fast camera", "fast zoom", "fast pan", "fast tracking",
        "explosion", "shaking", "trembling", "vibrating",
        "time-lapse", "hyperlapse", "speed ramp",
    ],
    "medium": [
        "walking", "turning", "reaching", "gesturing",
        "slow pan", "slow zoom", "tracking shot", "dolly shot",
        "gentle movement", "subtle motion",
        "walking", "turning head", "smiling",
    ],
    "low": [
        "static", "still", "motionless", "stationary", "fixed",
        "slow motion", "slow push", "slow pull", "gentle float",
        "subtle drift", "calm", "peaceful",
    ],
}

HIGH_RISK_MOTION_PATTERNS = [
    "fast zoom", "rapid zoom", "extreme close up",
    "360", "spin", "rotate", "revolve",
    "flying through", "flying over", "drone shot",
    "underwater", "heavy rain", "snow storm",
    "fire", "explosion", "thick smoke",
]


def detect_motion_intensity(prompt: str) -> Tuple[str, float, List[str]]:
    prompt_lower = prompt.lower()
    scores = {"high": 0, "medium": 0, "low": 0}
    matched = []

    for level, keywords in MOTION_INTENSITY_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in prompt_lower:
                scores[level] += 1
                matched.append(kw)

    for pattern in HIGH_RISK_MOTION_PATTERNS:
        if pattern.lower() in prompt_lower:
            scores["high"] += 2
            matched.append(f"[risk]{pattern}")

    total = sum(scores.values())
    if total == 0:
        return "medium", 0.5, ["default_medium"]

    weighted = scores["high"] * 1.0 + scores["medium"] * 0.5 + scores["low"] * 0.2
    normalized = min(1.0, weighted / max(1, total))

    if scores["high"] > 0 or normalized >= 0.7:
        level = "high"
    elif scores["low"] > scores["medium"] and normalized < 0.4:
        level = "low"
    else:
        level = "medium"

    return level, normalized, matched


def calibrate_motion_intensity(
    prompt: str,
    target_level: str = "medium",
    current_level: Optional[str] = None,
) -> Tuple[str, bool, str]:
    if current_level is None:
        current_level, _, _ = detect_motion_intensity(prompt)

    if current_level == target_level:
        return prompt, False, "motion_ok"

    prompt_lower = prompt.lower()

    if current_level == "high" and target_level in ("medium", "low"):
        constraints = []
        if "smooth motion" not in prompt_lower and "smooth steady" not in prompt_lower:
            constraints.append("smooth steady motion")
        if "stable camera" not in prompt_lower:
            constraints.append("stable camera")
        if "no camera shake" not in prompt_lower:
            constraints.append("no camera shake")

        if constraints:
            new_prompt = prompt.rstrip(" ,") + ", " + ", ".join(constraints)
            return new_prompt, True, f"motion_high→{target_level}_added_{len(constraints)}_constraints"

    if current_level == "low" and target_level == "medium":
        if "subtle movement" not in prompt_lower and "gentle motion" not in prompt_lower:
            new_prompt = prompt.rstrip(" ,") + ", subtle natural movement"
            return new_prompt, True, "motion_low→medium_added_subtle"

    return prompt, False, "motion_no_change"


def check_motion_consistency(prompts: List[str]) -> Tuple[bool, float, List[str]]:
    if len(prompts) < 2:
        return True, 1.0, ["too_few_segments"]

    levels = []
    for p in prompts:
        level, score, _ = detect_motion_intensity(p)
        levels.append((level, score))

    scores = [s for _, s in levels]
    if not scores:
        return True, 1.0, []

    import statistics
    try:
        variance = statistics.variance(scores) if len(scores) > 1 else 0.0
    except statistics.StatisticsError:
        variance = 0.0

    consistency = max(0.0, 1.0 - variance * 3.0)

    issues = []
    high_count = sum(1 for l, _ in levels if l == "high")
    low_count = sum(1 for l, _ in levels if l == "low")

    if high_count > 0 and high_count / len(levels) > 0.5:
        issues.append(f"超过半数片段运动强度过高（{high_count}/{len(levels)}）")
    if abs(high_count - low_count) > len(levels) * 0.6:
        issues.append("片段间运动强度差异过大，可能导致观感跳脱")

    if consistency >= 0.7:
        passed = True
    elif consistency >= 0.5:
        passed = True
        issues.append(f"运动一致性中等（{consistency:.2f}）")
    else:
        passed = False
        issues.append(f"运动一致性较差（{consistency:.2f}）")

    return passed, consistency, issues


# ── v2 新增：风格一致性检测与锚定 ──

# 风格关键词分类（用于检测 Prompt 中的风格倾向）
STYLE_KEYWORDS = {
    "cinematic": [
        "cinematic", "film", "movie", "cinematography", "anamorphic",
        "shallow depth of field", "bokeh", "lens flare", "film grain",
        "dramatic lighting", "golden hour", "blue hour",
        "电影感", "胶片感", "大光圈", "虚化", "电影镜头",
    ],
    "realistic": [
        "realistic", "photorealistic", "photo", "photograph", "ultra realistic",
        "8k", "4k", "high detail", "sharp focus", "professional photo",
        "dslr", "mirrorless", "raw photo",
        "写实", "真实", "照片级", "高清", "专业摄影",
    ],
    "anime": [
        "anime", "manga", "studio ghibli", "anime style", "japanese animation",
        "cel shading", "anime art",
        "动漫", "二次元", "漫画风", "吉卜力",
    ],
    "minimalist": [
        "minimalist", "minimal", "clean", "simple", "flat design",
        "negative space", "symmetrical", "geometric",
        "极简", "简约", "干净", "留白",
    ],
    "warm_cozy": [
        "warm", "cozy", "golden", "sunset", "soft light",
        "pastel", "cream tones", "beige", "earth tones",
        "温暖", "温馨", "暖色调", "奶油色",
    ],
    "dark_moody": [
        "dark", "moody", "dramatic", "low key", "noir",
        "shadow", "high contrast", "dimly lit",
        "暗色", "阴郁", "低调", "高对比",
    ],
}

# 风格锚定词（用于统一全片风格）
STYLE_ANCHORS = {
    "cinematic": "cinematic film look, consistent color grading",
    "realistic": "photorealistic, consistent professional photography style",
    "anime": "consistent anime art style, uniform character design",
    "minimalist": "minimalist clean aesthetic, consistent visual language",
    "warm_cozy": "warm cozy atmosphere, consistent golden lighting",
    "dark_moody": "dark moody cinematic style, consistent dramatic lighting",
}


def detect_style(prompt: str) -> Tuple[str, Dict[str, int], List[str]]:
    """
    检测 Prompt 的风格倾向。

    Returns:
        (主导风格, 各风格得分 dict, 匹配到的关键词)
    """
    prompt_lower = prompt.lower()
    scores = {}
    all_matched = []

    for style, keywords in STYLE_KEYWORDS.items():
        count = 0
        matched = []
        for kw in keywords:
            if kw.lower() in prompt_lower:
                count += 1
                matched.append(kw)
        scores[style] = count
        if matched:
            all_matched.extend(matched)

    if not any(scores.values()):
        return "realistic", scores, ["default_realistic"]

    dominant = max(scores, key=scores.get)
    if scores[dominant] == 0:
        return "realistic", scores, ["default_realistic"]

    return dominant, scores, all_matched


def check_style_consistency(prompts: List[str]) -> Tuple[bool, float, str, List[str]]:
    """
    检查多个片段之间的风格一致性。

    Returns:
        (是否通过, 一致性得分 0-1, 主导风格, 问题列表)
    """
    if len(prompts) < 2:
        return True, 1.0, "realistic", ["too_few_segments"]

    styles = []
    all_scores = []
    for p in prompts:
        style, scores, _ = detect_style(p)
        styles.append(style)
        all_scores.append(scores)

    # 找主导风格
    from collections import Counter
    style_counts = Counter(styles)
    dominant_style = style_counts.most_common(1)[0][0]
    dominant_ratio = style_counts.most_common(1)[0][1] / len(styles)

    # 计算一致性得分
    consistency = dominant_ratio

    issues = []
    if dominant_ratio < 0.6:
        issues.append(f"风格不统一：主导风格 '{dominant_style}' 仅占 {dominant_ratio:.0%}")
        issues.append(f"风格分布：{dict(style_counts)}")
    elif dominant_ratio < 0.8:
        issues.append(f"风格基本统一，但有 {len(styles) - style_counts[dominant_style]} 个片段风格略有差异")

    if consistency >= 0.8:
        passed = True
    elif consistency >= 0.6:
        passed = True
    else:
        passed = False

    return passed, consistency, dominant_style, issues


def anchor_style_to_prompts(
    prompts: List[str],
    target_style: Optional[str] = None,
) -> Tuple[List[str], int, str]:
    """
    给所有片段注入统一种风格锚定词。

    Returns:
        (处理后的 prompts, 修改数量, 说明)
    """
    if not prompts:
        return prompts, 0, "no_prompts"

    if target_style is None:
        _, _, target_style, _ = check_style_consistency(prompts)

    anchor = STYLE_ANCHORS.get(target_style, "consistent visual style throughout")

    modified = 0
    result = []
    for p in prompts:
        p_lower = p.lower()
        # 检查是否已经有类似的风格锚定词
        if "consistent" in p_lower and ("style" in p_lower or "look" in p_lower):
            result.append(p)
        else:
            result.append(p.rstrip(" ,") + ", " + anchor)
            modified += 1

    return result, modified, f"anchored_to_{target_style}"


# ── v2 新增：Prompt 冲突检测与自动消解 ──

# 常见冲突词对（语义矛盾的描述词）
CONFLICT_PAIRS = [
    # 亮度冲突
    ({"bright", "well-lit", "sunny", "daylight", "light"},
     {"dark", "dim", "noir", "low key", "shadowy", "pitch black", "underexposed"}),
    # 风格冲突
    ({"realistic", "photorealistic", "photo", "real life"},
     {"anime", "cartoon", "illustration", "painting", "stylized", "2d"}),
    # 运动冲突
    ({"static", "still", "motionless", "stationary"},
     {"running", "jumping", "fast", "dynamic action", "explosion"}),
    # 清晰度冲突
    ({"sharp", "crystal clear", "ultra detailed", "8k", "4k"},
     {"blurry", "soft focus", "dreamy", "hazy", "defocused"}),
    # 色彩冲突
    ({"vibrant", "colorful", "saturated"},
     {"monochrome", "black and white", "desaturated", "muted"}),
    # 景深冲突
    ({"shallow depth of field", "bokeh", "blurred background"},
     {"deep focus", "everything in focus", "sharp background"}),
    # 时间冲突
    ({"day", "daytime", "morning", "afternoon", "sunny"},
     {"night", "evening", "midnight", "dark sky", "moonlight"}),
    # 场景冲突
    ({"indoor", "inside", "room", "office", "house"},
     {"outdoor", "outside", "street", "park", "nature"}),
    # 温度冲突
    ({"warm", "golden", "orange", "red tones"},
     {"cool", "blue", "cyan", "cold tones"}),
]

# 消解策略：冲突时保留的优先级（越高越优先保留）
RESOLUTION_PRIORITY = {
    # 场景描述优先级最高（最影响整体）
    "indoor": 10, "inside": 10, "outdoor": 10, "outside": 10,
    "day": 9, "night": 9, "daytime": 9, "evening": 9,
    # 风格次之
    "realistic": 8, "photorealistic": 8, "anime": 8, "cartoon": 8,
    # 亮度次之
    "bright": 7, "dark": 7, "well-lit": 7, "dim": 7,
    # 其他默认 5
}


def detect_prompt_conflicts(prompt: str) -> List[Tuple[str, str, str]]:
    """
    检测 Prompt 中的语义冲突。

    Returns:
        [(冲突词A, 冲突词B, 冲突类型), ...]
    """
    prompt_lower = prompt.lower()
    conflicts = []

    for pair in CONFLICT_PAIRS:
        found_a = []
        found_b = []
        for word in pair[0]:
            if word in prompt_lower:
                found_a.append(word)
        for word in pair[1]:
            if word in prompt_lower:
                found_b.append(word)

        if found_a and found_b:
            for a in found_a[:2]:
                for b in found_b[:2]:
                    # 简单分类冲突类型
                    if "bright" in a or "dark" in a:
                        ctype = "brightness"
                    elif "realistic" in a or "anime" in a:
                        ctype = "style"
                    elif "indoor" in a or "outdoor" in a:
                        ctype = "scene"
                    else:
                        ctype = "general"
                    conflicts.append((a, b, ctype))

    return conflicts


def resolve_prompt_conflicts(
    prompt: str,
    *,
    prefer_style: Optional[str] = None,
) -> Tuple[str, int, List[str]]:
    """
    自动消解 Prompt 中的冲突：删除优先级更低的冲突词。

    Returns:
        (消解后的 prompt, 修复的冲突数, 冲突描述列表)
    """
    conflicts = detect_prompt_conflicts(prompt)
    if not conflicts:
        return prompt, 0, []

    prompt_lower = prompt.lower()
    to_remove = set()
    resolved_descriptions = []

    for word_a, word_b, ctype in conflicts:
        # 计算优先级
        prio_a = RESOLUTION_PRIORITY.get(word_a, 5)
        prio_b = RESOLUTION_PRIORITY.get(word_b, 5)

        # 如果指定了偏好风格，对应词优先级 +3
        if prefer_style and prefer_style in word_a:
            prio_a += 3
        if prefer_style and prefer_style in word_b:
            prio_b += 3

        # 低优先级的删除
        if prio_a >= prio_b:
            to_remove.add(word_b)
            loser = word_b
        else:
            to_remove.add(word_a)
            loser = word_a

        resolved_descriptions.append(
            f"冲突({ctype}): '{word_a}' vs '{word_b}'，保留高优先级，移除 '{loser}'"
        )

    if not to_remove:
        return prompt, 0, []

    # 从 prompt 中移除冲突词
    result = prompt
    for word in to_remove:
        # 用正则移除（考虑前后可能的逗号和空格）
        import re
        pattern = r',?\s*' + re.escape(word) + r'\s*,?'
        result = re.sub(pattern, ', ', result, flags=re.IGNORECASE)

    # 清理多余的逗号和空格
    result = re.sub(r',\s*,+', ', ', result)
    result = result.strip(' ,')

    return result, len(conflicts), resolved_descriptions


# ── v2 新增：相邻片段视觉跳变检测与过渡优化 ──

# 场景分类关键词（用于判断场景是否发生跳变）
SCENE_CATEGORIES = {
    "indoor_home": ["living room", "bedroom", "kitchen", "bathroom", "house", "home", "apartment"],
    "indoor_office": ["office", "conference room", "meeting room", "workplace", "cubicle"],
    "indoor_public": ["restaurant", "cafe", "shop", "store", "mall", "hospital", "school"],
    "outdoor_street": ["street", "road", "sidewalk", "city", "downtown", "urban"],
    "outdoor_nature": ["park", "forest", "mountain", "beach", "river", "lake", "garden", "nature"],
    "outdoor_public": ["plaza", "square", "station", "airport", "subway"],
}

# 时间段分类
TIME_CATEGORIES = {
    "day": ["day", "daytime", "morning", "afternoon", "sunny", "bright daylight", "midday"],
    "night": ["night", "evening", "midnight", "dark sky", "moonlight", "starry", "sunset", "dusk", "dawn"],
    "indoor_light": ["indoor", "inside", "room"],
}

# 色调分类
TONE_CATEGORIES = {
    "warm": ["warm", "golden", "orange", "red", "yellow", "cozy", "sunset", "amber"],
    "cool": ["cool", "blue", "cyan", "teal", "cold", "ice", "ocean"],
    "dark": ["dark", "dim", "moody", "noir", "low key", "shadow"],
    "bright": ["bright", "vibrant", "colorful", "saturated", "vivid"],
}


def extract_visual_attributes(prompt: str) -> Dict[str, Optional[str]]:
    """
    从 Prompt 中提取关键视觉属性。

    Returns:
        {scene, time, tone, has_character, has_product}
    """
    prompt_lower = prompt.lower()
    attrs: Dict[str, Optional[str]] = {
        "scene": None,
        "time": None,
        "tone": None,
        "has_character": False,
        "has_product": False,
    }

    # 场景分类
    best_scene = None
    best_count = 0
    for cat, keywords in SCENE_CATEGORIES.items():
        count = sum(1 for kw in keywords if kw in prompt_lower)
        if count > best_count:
            best_count = count
            best_scene = cat
    attrs["scene"] = best_scene

    # 时间分类
    for cat, keywords in TIME_CATEGORIES.items():
        if any(kw in prompt_lower for kw in keywords):
            attrs["time"] = cat
            break

    # 色调分类
    best_tone = None
    best_tone_count = 0
    for cat, keywords in TONE_CATEGORIES.items():
        count = sum(1 for kw in keywords if kw in prompt_lower)
        if count > best_tone_count:
            best_tone_count = count
            best_tone = cat
    attrs["tone"] = best_tone

    # 是否有人物（粗略判断）
    attrs["has_character"] = any(k in prompt_lower for k in [
        "woman", "man", "person", "girl", "boy", "lady", "gentleman",
        "she", " he ", "character", "face", "hair",
    ])

    # 是否有产品（粗略判断）
    attrs["has_product"] = any(k in prompt_lower for k in [
        "product", "bottle", "package", "box", "jar", "tube",
        "phone", "laptop", "watch", "shoe", "bag",
    ])

    return attrs


def check_visual_transitions(
    prompts: List[str],
) -> Tuple[List[int], List[str], List[Dict[str, Any]]]:
    """
    检查相邻片段之间的视觉跳变。

    Returns:
        (跳变片段索引列表, 问题描述列表, 每对相邻片段的详细信息)
    """
    if len(prompts) < 2:
        return [], [], []

    all_attrs = [extract_visual_attributes(p) for p in prompts]
    transitions = []
    issues = []
    details = []

    for i in range(len(prompts) - 1):
        a = all_attrs[i]
        b = all_attrs[i + 1]
        jump_score = 0
        jump_types = []

        # 场景跳变
        if a["scene"] and b["scene"] and a["scene"] != b["scene"]:
            # 同大类（都是 indoor 或都是 outdoor）跳变小
            a_indoor = a["scene"].startswith("indoor")
            b_indoor = b["scene"].startswith("indoor")
            if a_indoor != b_indoor:
                jump_score += 3
                jump_types.append("场景(室内↔室外)")
            else:
                jump_score += 1
                jump_types.append("场景(同类不同)")

        # 时间跳变
        if a["time"] and b["time"] and a["time"] != b["time"]:
            jump_score += 2
            jump_types.append(f"时间({a['time']}→{b['time']})")

        # 色调跳变
        if a["tone"] and b["tone"] and a["tone"] != b["tone"]:
            # 亮暗跳变最突兀
            if (a["tone"] in ("bright", "warm") and b["tone"] in ("dark", "cool")) or \
               (a["tone"] in ("dark", "cool") and b["tone"] in ("bright", "warm")):
                jump_score += 2
                jump_types.append(f"色调({a['tone']}→{b['tone']})")
            else:
                jump_score += 1
                jump_types.append(f"色调({a['tone']}→{b['tone']})")

        details.append({
            "from": i + 1,
            "to": i + 2,
            "jump_score": jump_score,
            "jump_types": jump_types,
            "from_attrs": a,
            "to_attrs": b,
        })

        if jump_score >= 3:
            transitions.append(i + 1)  # 记录跳转起始片段
            issues.append(
                f"片段 {i+1}→{i+2} 视觉跳变较大（{jump_score}分）："
                f"{', '.join(jump_types)}"
            )

    return transitions, issues, details


def add_transition_hints(
    prompts: List[str],
    transitions: List[int],
    details: List[Dict[str, Any]],
) -> Tuple[List[str], int]:
    """
    给跳变的片段添加过渡提示词，使转场更自然。

    Returns:
        (优化后的 prompts, 修改的片段数)
    """
    if not transitions:
        return prompts, 0

    result = list(prompts)
    modified = 0

    for detail in details:
        idx = detail["to"] - 1  # 目标片段索引（0-based）
        if idx < 0 or idx >= len(result):
            continue

        if detail["jump_score"] < 3:
            continue

        prompt_lower = result[idx].lower()

        # 已有过渡词就跳过
        if "transition" in prompt_lower or "smooth change" in prompt_lower:
            continue

        # 根据跳变类型添加过渡词
        hint_parts = []
        jt = detail["jump_types"]

        if any("场景" in t for t in jt):
            hint_parts.append("smooth scene transition")
        if any("时间" in t for t in jt):
            hint_parts.append("gradual lighting change")
        if any("色调" in t for t in jt):
            hint_parts.append("smooth color transition")

        if hint_parts:
            hint = ", ".join(hint_parts[:2])
            result[idx] = result[idx].rstrip(" ,") + f", {hint}"
            modified += 1

    return result, modified


# ── v2 新增：智能 Prompt 压缩器 ──

# 压缩优先级（数字越大越先被删掉）
# 顺序：场景(1) → 主体(2) → 动作(3) → 产品(4) → 镜头(5) → 光影(6) → 风格(7) → 质量(8) → 技术(9) → 未分类(5)
COMPRESSION_PRIORITY = {
    "technical": 9,    # 最先删：比例、帧率这些
    "quality": 8,      # 然后：质量形容词
    "style": 7,        # 然后：风格描述
    "lighting": 6,     # 然后：光影细节
    "camera": 5,       # 然后：镜头语言
    "unclassified": 5, # 然后：无法分类的描述
    "product": 4,      # 然后：产品细节（但保留产品主体）
    "action": 3,       # 尽量保留：动作表情
    "subject": 2,      # 必保：人物主体
    "scene": 1,        # 必保：场景环境
}


def smart_compress_prompt(prompt: str, target_chars: int = 1000) -> Tuple[str, List[str]]:
    """
    智能压缩 Prompt，按优先级从低到高删除元素，核心信息永不丢。

    Args:
        prompt: 原始 prompt
        target_chars: 目标字符数

    Returns:
        (压缩后的 prompt, 被删除的元素列表)
    """
    import re

    if len(prompt) <= target_chars:
        return prompt, []

    # 按逗号分割
    raw_parts = [p.strip() for p in prompt.split(',') if p.strip()]
    if len(raw_parts) < 5:
        return prompt, []

    # 分类
    sections = {k: [] for k in COMPRESSION_PRIORITY.keys()}

    for part in raw_parts:
        part_lower = part.lower()
        classified = False
        for section, patterns in PROMPT_SECTION_PATTERNS.items():
            for pat in patterns:
                if re.search(pat, part_lower):
                    sections[section].append(part)
                    classified = True
                    break
            if classified:
                break
        if not classified:
            sections["unclassified"].append(part)

    # 按优先级从高到低排序（优先级数字大的先删）
    priority_order = sorted(
        COMPRESSION_PRIORITY.keys(),
        key=lambda k: COMPRESSION_PRIORITY[k],
        reverse=True
    )

    deleted = []
    result_parts = list(raw_parts)
    current_len = len(prompt)

    for section in priority_order:
        if current_len <= target_chars:
            break

        section_parts = sections.get(section, [])
        # 每个类别至少保留 0-1 个（核心类别保留全部）
        min_keep = 0
        if COMPRESSION_PRIORITY[section] <= 2:
            min_keep = len(section_parts)  # 场景和主体全部保留
        elif COMPRESSION_PRIORITY[section] <= 4:
            min_keep = max(1, len(section_parts) // 2)  # 动作/产品保留一半
        else:
            min_keep = max(0, min(1, len(section_parts) // 3))  # 其他只留 0-1 个

        to_delete = section_parts[min_keep:]
        for part in to_delete:
            if current_len <= target_chars:
                break
            if part in result_parts:
                idx = result_parts.index(part)
                removed = result_parts.pop(idx)
                deleted.append(removed)
                # 减去这个元素 + 后面逗号的长度（大约）
                current_len -= (len(removed) + 2)

    compressed = ", ".join(result_parts)
    return compressed, deleted


# ── v2 新增：角色空间位置锚定 ──

# 预设的多角色位置布局（根据角色数量）
CHARACTER_POSITION_LAYOUTS = {
    2: [
        "positioned on left side of frame",
        "positioned on right side of frame",
    ],
    3: [
        "positioned on left side of frame",
        "positioned in center of frame",
        "positioned on right side of frame",
    ],
    4: [
        "positioned on left side of frame, foreground",
        "positioned on center-left of frame",
        "positioned on center-right of frame",
        "positioned on right side of frame, foreground",
    ],
    5: [
        "positioned on far left of frame",
        "positioned on left side of frame",
        "positioned in center of frame",
        "positioned on right side of frame",
        "positioned on far right of frame",
    ],
}


def assign_character_positions(character_count: int) -> List[str]:
    """
    为多角色场景分配固定的画面位置。

    Args:
        character_count: 角色数量

    Returns:
        每个角色的位置描述列表
    """
    if character_count <= 1:
        return ["positioned in center of frame"]

    layout = CHARACTER_POSITION_LAYOUTS.get(
        min(character_count, 5),
        CHARACTER_POSITION_LAYOUTS[5]
    )
    return layout[:character_count]


def inject_character_positions(
    prompt: str,
    character_names: List[str],
    positions: List[str],
) -> str:
    """
    为 Prompt 中的角色注入位置锚定词。

    Args:
        prompt: 原始 prompt
        character_names: 角色名称列表
        positions: 每个角色对应的位置描述

    Returns:
        注入位置锚定后的 prompt
    """
    if len(character_names) != len(positions) or len(character_names) <= 1:
        return prompt

    prompt_lower = prompt.lower()

    # 检查是否已有位置描述
    if "positioned on" in prompt_lower or "character composition" in prompt_lower:
        return prompt

    # 在 prompt 末尾追加角色布局说明
    position_summary = (
        "character composition: "
        + ", ".join(f"{name} {pos}" for name, pos in zip(character_names, positions))
    )

    enhanced = prompt.rstrip()
    if not enhanced.endswith(","):
        enhanced += ","
    enhanced += " " + position_summary

    return enhanced


# ── v2 新增：片段间智能过渡提示词 ──

def generate_transition_prompt(
    prev_prompt: str,
    curr_prompt: str,
    prev_narrative: str = "",
    curr_narrative: str = "",
) -> str:
    """
    根据前后两段内容差异，生成智能过渡描述，提升片段间连贯性。

    增强版：加入光照/色彩/构图多维度一致性锚定。

    Args:
        prev_prompt: 前一段 prompt
        curr_prompt: 当前段 prompt
        prev_narrative: 前一段叙事类型
        curr_narrative: 当前段叙事类型

    Returns:
        过渡描述字符串（可以拼接到当前段开头）
    """
    import re

    prev_lower = prev_prompt.lower()
    curr_lower = curr_prompt.lower()

    # 提取场景关键词
    scene_keywords = ["living room", "bedroom", "kitchen", "office", "street", "park", "outdoor", "indoor", "cafe", "restaurant", "car", "bathroom"]
    prev_scene = next((kw for kw in scene_keywords if kw in prev_lower), "")
    curr_scene = next((kw for kw in scene_keywords if kw in curr_lower), "")

    # 提取光照关键词
    lighting_keywords = ["natural light", "sunlight", "soft light", "warm light", "cool light", "dramatic lighting", "golden hour", "window light", "studio lighting", "ambient light"]
    prev_lighting = [kw for kw in lighting_keywords if kw in prev_lower]
    curr_lighting = [kw for kw in lighting_keywords if kw in curr_lower]

    # 提取人物关键词
    people_keywords = ["woman", "man", "child", "person", "mother", "father", "girl", "boy", "lady", "gentleman"]
    prev_people = [kw for kw in people_keywords if kw in prev_lower]
    curr_people = [kw for kw in people_keywords if kw in curr_lower]

    transition_parts = []

    # 场景相同 vs 不同
    if prev_scene and curr_scene and prev_scene == curr_scene:
        transition_parts.append("continuous shot within same space")
        transition_parts.append("same lighting and color temperature")
        transition_parts.append("consistent camera angle and perspective")
        transition_parts.append("matching shadows and highlights")
    else:
        transition_parts.append("seamless scene transition")
        transition_parts.append("smooth continuity of mood")
        transition_parts.append("gradual lighting transition")

    # 前一段有明确光照的话，继承光照风格
    if prev_lighting and not curr_lighting:
        transition_parts.append(f"{prev_lighting[0]} continues")

    # 人物变化分析
    same_people = set(prev_people) & set(curr_people)
    if same_people:
        transition_parts.append("same character continues action")
        transition_parts.append("consistent character appearance and outfit")
    else:
        transition_parts.append("character shift, maintaining visual style")

    # 叙事类型对应的转场节奏
    narrative_transitions = {
        ("hook", "pain"): "slow push-in, building tension",
        ("pain", "showcase"): "smooth cut to product, gentle camera pan",
        ("showcase", "result"): "lateral tracking, revealing result",
        ("result", "cta"): "gradual push-in, building trust",
    }
    if (prev_narrative, curr_narrative) in narrative_transitions:
        transition_parts.append(narrative_transitions[(prev_narrative, curr_narrative)])

    if not transition_parts:
        return "seamless transition, visual continuity"

    return ", ".join(transition_parts)


# ── v2 新增：负面提示词智能精简 + 分级 ──

# 负面词分级（按出现频率和严重程度排序，越靠前越重要）
NEGATIVE_PROMPT_TIERS = {
    "critical": [
        "low quality", "blurry", "out of focus", "poor quality",
        "deformed", "distorted", "malformed", "mangled",
        "ugly", "bad anatomy", "bad proportions",
    ],
    "high": [
        "extra limbs", "extra fingers", "fused fingers", "too many fingers",
        "missing fingers", "mutated hands", "mutated fingers",
        "long neck", "extra arms", "extra legs",
        "duplicate", "cloned", "copy", "repeating",
        "watermark", "text", "signature", "logo",
    ],
    "medium": [
        "grainy", "noisy", "pixelated", "jpeg artifacts",
        "overexposed", "underexposed", "flat lighting",
        "asymmetric face", "cropped", "cut off",
        "bad composition", "poor framing",
    ],
    "low": [
        "cartoon", "anime", "illustration", "painting", "drawing",
        "artificial", "plastic", "doll-like", "uncanny valley",
        "unrealistic", "fake", "3d render", "cgi",
    ],
}


def smart_compress_negative_prompt(
    base_negative: str,
    scene_type: str = "",
    target_count: int = 15,
) -> str:
    """
    智能精简负面提示词，精选最相关的，避免稀释效应。

    原则：
    1. critical 级别的全部保留（最常见的翻车问题）
    2. high 级别按场景相关性筛选
    3. medium 和 low 级别只保留最相关的几个
    4. 总数量控制在 target_count 左右

    Args:
        base_negative: 原始负面提示词
        scene_type: 场景类型（character/product/action/indoor/outdoor）
        target_count: 目标负面词数量（约数）

    Returns:
        精简后的负面提示词
    """
    if not base_negative:
        return base_negative

    base_lower = base_negative.lower()
    base_words = [w.strip() for w in base_lower.split(",") if w.strip()]

    # 按级别分类现有负面词
    tiers_found = {"critical": [], "high": [], "medium": [], "low": [], "unknown": []}

    for word in base_words:
        found = False
        for tier, tier_words in NEGATIVE_PROMPT_TIERS.items():
            if word in [t.lower() for t in tier_words]:
                tiers_found[tier].append(word)
                found = True
                break
        if not found:
            tiers_found["unknown"].append(word)

    # 场景相关的负面词（从场景化增强里获取）
    scene_extra = set()
    if scene_type == "character":
        scene_extra.update([
            "asymmetric face", "lopsided face", "misaligned eyes",
            "deformed nose", "distorted lips", "plastic looking face",
        ])
    elif scene_type == "product":
        scene_extra.update([
            "distorted logo", "blurry text", "warped packaging",
            "wrong colors", "faded product",
        ])
    elif scene_type == "action":
        scene_extra.update([
            "motion blur", "ghosting effect", "rubber body",
            "floating feet", "jerky motion",
        ])

    # 组合：critical 全保留 + high 精选 + 场景相关 + medium 少量
    final_words = []
    final_words.extend(tiers_found["critical"])
    final_words.extend(tiers_found["high"][:6])  # high 级别留 6 个
    final_words.extend(tiers_found["medium"][:3])  # medium 级别留 3 个
    final_words.extend(list(scene_extra)[:3])  # 场景相关留 3 个

    # 去重
    seen = set()
    unique_words = []
    for w in final_words:
        if w.lower() not in seen:
            seen.add(w.lower())
            unique_words.append(w)

    # 如果原始负面词里有不在分级表里的重要词，保留前几个
    unknown_important = [w for w in tiers_found["unknown"] if len(w) > 8][:2]
    unique_words.extend(unknown_important)

    if not unique_words:
        return base_negative

    return ", ".join(unique_words[:target_count + 3])


# ── v2 新增：参考图角度智能匹配 ──

# 镜头类型 → 推荐参考图角度映射
SHOT_TYPE_TO_REF_ANGLE = {
    "close-up": "front",
    "close up": "front",
    "medium close-up": "front",
    "medium close up": "front",
    "portrait": "front",
    "face shot": "front",
    "facial": "front",
    "headshot": "front",
    "medium shot": "front",
    "medium wide shot": "full_body",
    "wide shot": "full_body",
    "full shot": "full_body",
    "full body shot": "full_body",
    "establishing shot": "full_body",
    "walking": "full_body",
    "standing": "full_body",
    "full body": "full_body",
}


def recommend_ref_angle(prompt: str, narrative: str = "") -> str:
    """
    根据 prompt 内容和分镜类型，推荐最合适的参考图角度。

    Args:
        prompt: 片段 prompt
        narrative: 叙事类型

    Returns:
        推荐的参考图角度（front / full_body）
    """
    prompt_lower = prompt.lower()

    # 先根据镜头关键词判断
    for shot_keyword, angle in SHOT_TYPE_TO_REF_ANGLE.items():
        if shot_keyword in prompt_lower:
            return angle

    # 再根据叙事类型推断
    narrative_angle_map = {
        "hook": "full_body",
        "pain": "front",
        "showcase": "front",
        "result": "full_body",
        "cta": "front",
    }

    if narrative and narrative.lower() in narrative_angle_map:
        return narrative_angle_map[narrative.lower()]

    return "front"


# ── v2 新增：产品露出节奏智能校准 ──

# 各叙事段推荐的产品露出强度
PRODUCT_VISIBILITY_MAP = {
    "hook": "subtle",      # 钩子段：产品轻微露出即可，不要喧宾夺主
    "pain": "absent",      # 痛点段：不要出现产品，专注于痛点
    "showcase": "prominent",  # 展示段：产品必须清晰可见
    "result": "subtle",    # 效果段：产品自然融入场景
    "cta": "prominent",    # CTA 段：产品必须突出
}


def calibrate_product_visibility(prompt: str, narrative: str, product_name: str = "") -> Tuple[str, str]:
    """
    根据叙事类型智能校准产品露出强度。

    策略：
    - pain: 移除产品描述，专注痛点
    - hook: 产品轻微露出，背景/边缘位置
    - showcase/cta: 产品突出，居中清晰可见
    - result: 产品自然融入场景

    Args:
        prompt: 原始 prompt
        narrative: 叙事类型（hook/pain/showcase/result/cta）
        product_name: 产品名称（用于识别产品相关描述）

    Returns:
        (校准后的 prompt, 推荐的产品露出强度)
    """
    if not narrative:
        return prompt, "subtle"

    target_visibility = PRODUCT_VISIBILITY_MAP.get(narrative.lower(), "subtle")
    prompt_lower = prompt.lower()

    if target_visibility == "absent":
        # 痛点段：弱化产品存在，不主动提产品
        if product_name and product_name.lower() in prompt_lower:
            # 不直接删除（可能破坏语义），而是加上"in background, barely visible"
            return prompt + ", product in background, barely noticeable", target_visibility
        return prompt + ", no product visible, focus on emotional storytelling", target_visibility

    if target_visibility == "prominent":
        # 展示/CTA 段：强化产品露出
        enhancement = "product prominently displayed, center composition, clear packaging, sharp product details, well-lit product"
        if product_name:
            enhancement = f"{product_name} clearly visible, {enhancement}"
        return prompt + ", " + enhancement, target_visibility

    if target_visibility == "subtle":
        # 钩子/效果段：产品自然存在但不抢戏
        if product_name and product_name.lower() not in prompt_lower:
            return prompt + f", {product_name} subtly present in scene", target_visibility
        return prompt + ", product naturally integrated into scene", target_visibility

    return prompt, target_visibility


def check_all_prompts(
    prompts: List[str],
    negative_prompt: str = NEGATIVE_PROMPT,
    auto_apply: bool = True,
) -> Tuple[List[PromptCheckResult], List[str]]:
    """
    检查所有片段的 Prompt，并返回优化后的 Prompt 列表。

    Args:
        prompts: 原始 Prompt 列表
        negative_prompt: 负面提示词
        auto_apply: 是否自动应用优化后的 Prompt

    Returns:
        (检查结果列表, 优化后的 Prompt 列表)
    """
    results = []
    optimized_prompts = []

    config = QUALITY_GATE_CONFIG.get("prompt_check", {})
    auto_apply_enabled = config.get("auto_apply_optimization", True) and auto_apply

    for i, prompt in enumerate(prompts):
        result = check_prompt(prompt, negative_prompt=negative_prompt)
        result.name = f"片段 {i+1} Prompt"
        results.append(result)

        # 自动应用优化后的 Prompt
        if auto_apply_enabled and result.optimized_prompt:
            optimized_prompts.append(result.optimized_prompt)
            logger.info(f"片段 {i+1} Prompt 已自动优化")
        else:
            optimized_prompts.append(prompt)

    return results, optimized_prompts


def _run_v2_prompt_optimization_pipeline(
    prompts: List[str],
    ad_script: Dict[str, Any],
    character_bible: Optional[Dict[str, Any]] = None,
    product_bible: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """
    v2 深度 Prompt 优化流水线。

    按顺序应用 10 个优化：
    1. 分镜类型增强词注入
    2. 镜头语言注入
    3. 产品露出节奏校准
    4. 颜色调色板锚定
    5. 角色空间位置锚定
    6. 片段间智能过渡
    7. 黄金结构重排
    8. 智能压缩（如果超长）

    Args:
        prompts: 原始 prompt 列表
        ad_script: 广告脚本（含叙事类型、故事世界等上下文）
        character_bible: 角色圣经
        product_bible: 商品圣经

    Returns:
        优化后的 prompt 列表
    """
    if not prompts:
        return prompts

    try:
        segments = ad_script.get("segments", [])
        story_world = ad_script.get("story_world", {})
        story_world_desc = story_world.get("location", "") + " " + story_world.get("atmosphere", "")

        # 提取产品名称
        product_name = ""
        if product_bible:
            if isinstance(product_bible, dict):
                product_name = product_bible.get("name", "") or product_bible.get("product_name", "")
            elif hasattr(product_bible, "name"):
                product_name = str(product_bible.name)
        if not product_name:
            product_info = ad_script.get("product_info", {})
            if isinstance(product_info, dict):
                product_name = product_info.get("name", "")

        # 提取角色名称
        character_names = []
        if character_bible:
            character_names = list(character_bible.keys()) if isinstance(character_bible, dict) else []
        if not character_names and segments:
            for seg in segments:
                chars = seg.get("characters", [])
                for c in chars:
                    if isinstance(c, dict):
                        name = c.get("name", "")
                        if name and name not in character_names:
                            character_names.append(name)
                    elif isinstance(c, str) and c not in character_names:
                        character_names.append(c)

        # 分配角色位置
        positions = assign_character_positions(len(character_names)) if len(character_names) > 1 else []

        optimized = []

        for i, prompt in enumerate(prompts):
            current = prompt

            # 提取当前段叙事类型
            narrative = ""
            if i < len(segments):
                narrative = segments[i].get("narrative_function", "") or segments[i].get("type", "")

            # 1. 分镜类型增强词
            try:
                current = enhance_prompt_by_segment_type(current, narrative)
            except Exception:
                pass

            # 2. 镜头语言注入
            try:
                current = inject_cinematography(current, narrative)
            except Exception:
                pass

            # 3. 产品露出节奏校准
            try:
                if product_name or narrative:
                    current, _ = calibrate_product_visibility(current, narrative, product_name)
            except Exception:
                pass

            # 4. 颜色调色板锚定
            try:
                palette = detect_color_palette(current, story_world_desc)
                current = inject_color_palette(current, palette)
            except Exception:
                pass

            # 5. 角色空间位置锚定（多角色时）
            try:
                if len(character_names) > 1 and positions:
                    current = inject_character_positions(current, character_names, positions)
            except Exception:
                pass

            # 6. 片段间智能过渡（非首段）
            try:
                if i > 0 and len(prompts) > 1:
                    prev_narrative = ""
                    if i - 1 < len(segments):
                        prev_narrative = segments[i - 1].get("narrative_function", "") or segments[i - 1].get("type", "")
                    transition = generate_transition_prompt(
                        prompts[i - 1], current, prev_narrative, narrative
                    )
                    if transition:
                        current = transition + ", " + current
            except Exception:
                pass

            # 7. 基础电影质感增强（默认开启，零配置提升高级感）
            try:
                current, _ = inject_base_cinematic_quality(current, intensity="medium")
            except Exception:
                pass

            # 8. 黄金结构重排
            try:
                current = reorder_prompt_golden(current)
            except Exception:
                pass

            # 9. 智能压缩（如果超过 1200 字符）
            try:
                if len(current) > 1200:
                    current, _ = smart_compress_prompt(current, target_chars=1100)
            except Exception:
                pass

            optimized.append(current)

        return optimized if optimized else prompts

    except Exception as e:
        logger.warning(f"v2 深度优化流水线出错，跳过：{e}")
        return prompts


# ── 片段连贯性预检 ──

@dataclass
class CoherenceCheckResult:
    """片段连贯性检查结果"""
    passed: bool
    narrative_flow_score: float = 0.0
    scene_transition_score: float = 0.0
    emotion_curve_score: float = 0.0
    character_consistency_score: float = 0.0
    product_logic_score: float = 0.0
    issues: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)
    is_preview: bool = False


def check_segment_coherence(
    ad_script: Dict[str, Any],
    prompts: List[str],
) -> CoherenceCheckResult:
    """
    检查片段间的连贯性，确保叙事流畅、过渡合理。

    Args:
        ad_script: 广告脚本
        prompts: 所有片段的 Prompt 列表

    Returns:
        连贯性检查结果
    """
    config = QUALITY_GATE_CONFIG.get("coherence_check", {})
    if not config.get("enabled", True):
        return CoherenceCheckResult(passed=True, issues=["连贯性检查已跳过"])

    result = CoherenceCheckResult(passed=True)

    segments = ad_script.get("segments", [])
    if not segments or len(segments) < 5:
        result.passed = True
        result.issues.append(f"片段数较少（{len(segments)}段），跳过完整连贯性检查（预览/快速模式）")
        result.is_preview = True
        return result

    # 1. 检查叙事顺序
    if config.get("check_narrative_flow", True):
        required_order = config.get("required_segment_order", [])
        actual_order = [seg.get("narrative", "") for seg in segments]

        if actual_order != required_order:
            result.passed = False
            result.narrative_flow_score = 50.0
            result.issues.append(
                f"叙事顺序不符合要求：期望 {required_order}，实际 {actual_order}"
            )
            result.suggestions.append("请按 hook→turning_point→showcase→result→cta 顺序排列片段")
        else:
            result.narrative_flow_score = 100.0

    # 2. 检查情绪曲线
    if config.get("check_emotion_curve", True):
        expected_curve = config.get("emotion_curve_expectation", {})
        emotion_keywords = {
            "high": ["excited", "amazing", "wow", "shocking", "surprise", "震撼", "惊艳"],
            "low": ["sad", "frustrated", "problem", "pain", "困扰", "烦恼"],
            "medium": ["showing", "demonstrating", "presenting", "展示", "呈现"],
            "stable": ["buy", "get", "click", "purchase", "购买", "点击"],
        }

        emotion_score = 0.0
        for seg in segments:
            narrative = seg.get("narrative", "")
            expected_emotion = expected_curve.get(narrative, "medium")
            text = seg.get("text", "").lower()

            # 检查是否包含对应情绪关键词
            keywords = emotion_keywords.get(expected_emotion, [])
            has_emotion = any(kw in text for kw in keywords)

            if has_emotion:
                emotion_score += 20.0
            else:
                result.issues.append(
                    f"{narrative} 段缺少预期的情绪关键词（期望：{expected_emotion}）"
                )

        result.emotion_curve_score = emotion_score

        if emotion_score < 60.0:
            result.passed = False
            result.suggestions.append("请调整各段的情绪表达，使其符合广告叙事节奏")

    # 3. 检查场景过渡
    if config.get("check_scene_transition", True):
        transition_score = 100.0

        # 检查 hook→turning_point 是否有明确的转折点
        hook_text = segments[0].get("text", "").lower() if segments else ""
        turning_text = segments[1].get("text", "").lower() if len(segments) > 1 else ""

        transition_keywords = ["but", "however", "problem", "issue", "但是", "然而", "问题"]
        has_transition = any(kw in turning_text for kw in transition_keywords)

        if not has_transition:
            transition_score -= 20.0
            result.issues.append("hook→turning_point 缺少明确的转折关键词")

        # 检查 showcase→result 是否有明确的效果展示
        showcase_text = segments[2].get("text", "").lower() if len(segments) > 2 else ""
        result_text = segments[3].get("text", "").lower() if len(segments) > 3 else ""

        result_keywords = ["result", "effect", "outcome", "change", "效果", "改变", "结果"]
        has_result = any(kw in result_text for kw in result_keywords)

        if not has_result:
            transition_score -= 20.0
            result.issues.append("showcase→result 缺少明确的效果关键词")

        result.scene_transition_score = transition_score

        if transition_score < 60.0:
            result.passed = False
            result.suggestions.append("请加强片段间的过渡关键词，使叙事更流畅")

    # 4. 检查角色一致性
    if config.get("check_character_consistency", True):
        all_texts = " ".join([seg.get("text", "") for seg in segments]).lower()

        # 检查角色描述是否一致
        person_keywords = ["woman", "man", "person", "girl", "boy", "女性", "男性", "人物"]
        person_count = sum(1 for kw in person_keywords if kw in all_texts)

        if person_count > 0:
            # 检查是否有明确的角色特征描述
            feature_keywords = ["hair", "face", "age", "dress", "发型", "年龄", "穿着"]
            has_features = any(kw in all_texts for kw in feature_keywords)

            if not has_features:
                result.character_consistency_score = 60.0
                result.issues.append("角色描述缺少明确的特征（发型/年龄/穿着等）")
                result.suggestions.append("建议在脚本中明确角色的外貌特征")
            else:
                result.character_consistency_score = 100.0
        else:
            result.character_consistency_score = 100.0  # 无角色则跳过

    # 5. 检查产品展示逻辑
    if config.get("check_product_logic", True):
        # showcase 和 CTA 段必须提及产品
        showcase_text = segments[2].get("text", "").lower() if len(segments) > 2 else ""
        cta_text = segments[4].get("text", "").lower() if len(segments) > 4 else ""

        product_keywords = ["product", "item", "lipstick", "cream", "产品", "商品"]

        showcase_has_product = any(kw in showcase_text for kw in product_keywords)
        cta_has_product = any(kw in cta_text for kw in product_keywords)

        if not showcase_has_product:
            result.product_logic_score -= 30.0
            result.issues.append("showcase 段缺少产品关键词")
            result.suggestions.append("展示段应明确提及产品名称或特征")

        if not cta_has_product:
            result.product_logic_score -= 30.0
            result.issues.append("CTA 段缺少产品关键词")
            result.suggestions.append("CTA段应包含产品购买引导")

        result.product_logic_score = max(0.0, 100.0 - (0 if showcase_has_product else 30) - (0 if cta_has_product else 30))

        if result.product_logic_score < 50.0:
            result.passed = False

    return result


# ── 历史数据驱动优化 ──

@dataclass
class HistoricalOptimizationResult:
    """历史数据驱动优化结果"""
    recommended_seed: Optional[int] = None
    recommended_fidelity: Optional[float] = None
    recommended_mode: Optional[str] = None
    success_rate_prediction: float = 0.0
    confidence_level: str = "low"
    similar_cases_count: int = 0
    optimization_applied: List[str] = field(default_factory=list)


def load_history_database(db_path: str) -> List[Dict[str, Any]]:
    """加载历史案例数据库"""
    try:
        if Path(db_path).exists():
            with open(db_path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.warning(f"加载历史数据库失败：{e}")
    return []


def optimize_by_history(
    product_category: str,
    style_preference: str,
    num_clips: int,
) -> HistoricalOptimizationResult:
    """
    基于历史成功案例优化生成参数。

    Args:
        product_category: 产品品类
        style_preference: 风格偏好
        num_clips: 片段数量

    Returns:
        历史优化结果
    """
    config = QUALITY_GATE_CONFIG.get("history_driven_optimization", {})
    if not config.get("enabled", True):
        return HistoricalOptimizationResult(
            success_rate_prediction=0.75,
            confidence_level="default",
        )

    result = HistoricalOptimizationResult()

    # 加载成功案例数据库
    success_db_path = config.get("success_cases_db", "success_cases.json")
    failure_db_path = config.get("failure_cases_db", "failure_cases.json")

    # 统一路径：相对路径放到 output/history/ 下
    if not Path(success_db_path).is_absolute():
        from config import OUTPUT_DIR
        success_db_path = OUTPUT_DIR / "history" / success_db_path
        failure_db_path = OUTPUT_DIR / "history" / failure_db_path

    success_cases = load_history_database(str(success_db_path))
    failure_cases = load_history_database(str(failure_db_path))

    # 筛选相似案例
    similar_success = [
        case for case in success_cases
        if case.get("product_category") == product_category
        and case.get("style_preference") == style_preference
    ]

    similar_failure = [
        case for case in failure_cases
        if case.get("product_category") == product_category
        and case.get("style_preference") == style_preference
    ]

    result.similar_cases_count = len(similar_success) + len(similar_failure)

    if similar_success:
        # 计算成功率
        total_similar = len(similar_success) + len(similar_failure)
        result.success_rate_prediction = len(similar_success) / max(1, total_similar)

        # 提取最优参数
        best_case = max(similar_success, key=lambda c: c.get("quality_score", 0))

        if config.get("adjust_parameters_by_history", True):
            result.recommended_seed = best_case.get("seed")
            result.recommended_fidelity = best_case.get("fidelity")
            result.recommended_mode = best_case.get("mode")

            result.optimization_applied.append(
                f"基于 {len(similar_success)} 个成功案例优化参数"
            )

        # 判断置信度
        high_threshold = config.get("high_success_threshold", 0.85)
        low_threshold = config.get("low_success_threshold", 0.30)

        if result.success_rate_prediction >= high_threshold:
            result.confidence_level = "high"
        elif result.success_rate_prediction <= low_threshold:
            result.confidence_level = "low"
        else:
            result.confidence_level = "medium"

    else:
        # 无相似案例，使用默认参数
        result.success_rate_prediction = 0.75
        result.confidence_level = "default"
        result.optimization_applied.append("无相似历史案例，使用默认参数")

    return result


def save_history_database(db_path: str, cases: List[Dict[str, Any]]) -> None:
    """保存历史案例数据库"""
    try:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        with open(db_path, "w", encoding="utf-8") as f:
            json.dump(cases, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"保存历史数据库失败：{e}")


def record_failure_case(
    *,
    failure_type: str,
    failure_reason: str,
    product_category: str = "default",
    style_preference: str = "default",
    num_clips: int = 0,
    segment_index: Optional[int] = None,
    narrative_type: str = "",
    quality_score: float = 0.0,
    prompt_length: int = 0,
    has_character_ref: bool = False,
    has_product_ref: bool = False,
    estimated_cost: float = 0.0,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """
    记录失败案例到历史数据库，用于后续自我进化。

    失败类型（failure_type）建议值：
    - quality_gate_block: 质量门拦截
    - image_first_failed: 图片先行验证失败
    - video_generation_failed: 视频生成失败
    - quality_check_failed: 生成后质量检测失败
    - coherence_issue: 片段连贯性问题
    - character_inconsistency: 角色一致性问题
    - product_inconsistency: 商品一致性问题
    """
    config = QUALITY_GATE_CONFIG.get("history_driven_optimization", {})
    if not config.get("record_to_history", True):
        return

    failure_db_path = config.get("failure_cases_db", "failure_cases.json")
    if not Path(failure_db_path).is_absolute():
        from config import OUTPUT_DIR
        failure_db_path = OUTPUT_DIR / "history" / failure_db_path

    failure_cases = load_history_database(str(failure_db_path))

    case = {
        "timestamp": __import__("datetime").datetime.now().isoformat(),
        "failure_type": failure_type,
        "failure_reason": failure_reason,
        "product_category": product_category,
        "style_preference": style_preference,
        "num_clips": num_clips,
        "segment_index": segment_index,
        "narrative_type": narrative_type,
        "quality_score": quality_score,
        "prompt_length": prompt_length,
        "has_character_ref": has_character_ref,
        "has_product_ref": has_product_ref,
        "estimated_cost": estimated_cost,
    }
    if extra:
        case.update(extra)

    failure_cases.append(case)

    # 只保留最近 500 条，避免数据库无限增长
    if len(failure_cases) > 500:
        failure_cases = failure_cases[-500:]

    save_history_database(str(failure_db_path), failure_cases)
    logger.info(f"📝 已记录失败案例：{failure_type} - {failure_reason[:50]}")


def record_success_case(
    *,
    product_category: str = "default",
    style_preference: str = "default",
    num_clips: int = 0,
    quality_score: float = 0.0,
    seed: Optional[int] = None,
    fidelity: Optional[float] = None,
    mode: str = "standard",
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """记录成功案例到历史数据库"""
    config = QUALITY_GATE_CONFIG.get("history_driven_optimization", {})
    if not config.get("record_to_history", True):
        return

    success_db_path = config.get("success_cases_db", "success_cases.json")
    if not Path(success_db_path).is_absolute():
        from config import OUTPUT_DIR
        success_db_path = OUTPUT_DIR / "history" / success_db_path

    success_cases = load_history_database(str(success_db_path))

    case = {
        "timestamp": __import__("datetime").datetime.now().isoformat(),
        "product_category": product_category,
        "style_preference": style_preference,
        "num_clips": num_clips,
        "quality_score": quality_score,
        "seed": seed,
        "fidelity": fidelity,
        "mode": mode,
    }
    if extra:
        case.update(extra)

    success_cases.append(case)

    if len(success_cases) > 500:
        success_cases = success_cases[-500:]

    save_history_database(str(success_db_path), success_cases)
    logger.info(f"📝 已记录成功案例：{product_category} - 评分{quality_score:.0f}")


def get_failure_rate_by_type(
    failure_type: str,
    *,
    product_category: Optional[str] = None,
    lookback_days: int = 30,
) -> Tuple[int, int, float]:
    """
    查询某类失败的历史发生率。

    Returns:
        (失败次数, 总案例数, 失败率)
    """
    config = QUALITY_GATE_CONFIG.get("history_driven_optimization", {})
    failure_db_path = config.get("failure_cases_db", "failure_cases.json")
    success_db_path = config.get("success_cases_db", "success_cases.json")

    if not Path(failure_db_path).is_absolute():
        from config import OUTPUT_DIR
        failure_db_path = OUTPUT_DIR / "history" / failure_db_path
        success_db_path = OUTPUT_DIR / "history" / success_db_path

    failure_cases = load_history_database(str(failure_db_path))
    success_cases = load_history_database(str(success_db_path))

    from datetime import datetime, timedelta
    cutoff = datetime.now() - timedelta(days=lookback_days)

    def _is_recent(case):
        try:
            ts = datetime.fromisoformat(case.get("timestamp", ""))
            return ts >= cutoff
        except Exception:
            return True

    recent_failures = [c for c in failure_cases if _is_recent(c)]
    recent_successes = [c for c in success_cases if _is_recent(c)]

    if product_category:
        recent_failures = [c for c in recent_failures if c.get("product_category") == product_category]
        recent_successes = [c for c in recent_successes if c.get("product_category") == product_category]

    type_failures = [c for c in recent_failures if c.get("failure_type") == failure_type]
    total = len(recent_failures) + len(recent_successes)
    rate = len(type_failures) / max(1, total)

    return len(type_failures), total, rate


# ── 自我进化系统：电影风格成功率统计与动态推荐 ──

def get_cinematic_style_success_rates(
    product_category: str = "default",
    lookback_days: int = 30,
) -> Dict[str, Dict[str, float]]:
    """
    统计各电影风格的历史成功率（自我进化核心数据）。

    Args:
        product_category: 产品品类（为空则统计所有品类）
        lookback_days: 回溯天数

    Returns:
        {style_key: {"success": int, "total": int, "rate": float, "avg_quality": float}}
    """
    config = QUALITY_GATE_CONFIG.get("history_driven_optimization", {})
    if not config.get("record_to_history", True):
        return {}

    failure_db_path = config.get("failure_cases_db", "failure_cases.json")
    success_db_path = config.get("success_cases_db", "success_cases.json")
    if not Path(failure_db_path).is_absolute():
        from config import OUTPUT_DIR
        failure_db_path = OUTPUT_DIR / "history" / failure_db_path
        success_db_path = OUTPUT_DIR / "history" / success_db_path

    failures = load_history_database(str(failure_db_path))
    successes = load_history_database(str(success_db_path))

    from datetime import datetime, timedelta
    cutoff = datetime.now() - timedelta(days=lookback_days)

    def _is_recent(case: dict) -> bool:
        ts = case.get("timestamp", "")
        try:
            return datetime.fromisoformat(ts) >= cutoff
        except Exception:
            return True

    def _matches_category(case: dict) -> bool:
        if product_category == "default":
            return True
        return case.get("product_category", "default") == product_category

    style_stats: Dict[str, Dict[str, float]] = {}

    for case in successes:
        if not _is_recent(case) or not _matches_category(case):
            continue
        style = case.get("style_preference", "unknown")
        if style not in style_stats:
            style_stats[style] = {"success": 0, "total": 0, "quality_sum": 0.0}
        style_stats[style]["success"] += 1
        style_stats[style]["total"] += 1
        style_stats[style]["quality_sum"] += case.get("quality_score", 0.0)

    for case in failures:
        if not _is_recent(case) or not _matches_category(case):
            continue
        style = case.get("style_preference", "unknown")
        if style not in style_stats:
            style_stats[style] = {"success": 0, "total": 0, "quality_sum": 0.0}
        style_stats[style]["total"] += 1

    for style, stats in style_stats.items():
        stats["rate"] = stats["success"] / max(1, stats["total"])
        stats["avg_quality"] = stats["quality_sum"] / max(1, stats["success"])

    return style_stats


def evolve_cinematic_style_recommendation(
    product_category: str = "default",
    story_world_desc: str = "",
    ad_script: Optional[Dict[str, Any]] = None,
    lookback_days: int = 30,
) -> Tuple[str, Dict[str, Any]]:
    """
    自我进化版电影风格推荐：结合历史成功率 + 品类匹配 + 场景分析。

    三级 fallback 机制：
    1. 当前品类样本充足（≥5）→ 用品类历史数据为主
    2. 当前品类样本不足 → 品类基础匹配 + 全局历史数据加权混合
    3. 全局样本也不足 → 纯基础匹配

    进化逻辑：
    1. 先用基础匹配算法给出初始推荐
    2. 用历史成功率加权调整（历史表现越好，推荐优先级越高）
    3. 样本量不足时（<5次），以基础匹配为准
    4. 自动探索：每隔一段时间尝试新风格，避免局部最优

    Args:
        product_category: 产品品类
        story_world_desc: 故事世界描述
        ad_script: 广告脚本
        lookback_days: 回溯天数

    Returns:
        (推荐的风格key, 推荐详情字典)
    """
    base_style = smart_pick_cinematic_style(product_category, story_world_desc, ad_script)

    cat_stats = get_cinematic_style_success_rates(product_category, lookback_days)
    global_stats = get_cinematic_style_success_rates("default", lookback_days) if product_category != "default" else {}

    cat_total = sum(s["total"] for s in cat_stats.values()) if cat_stats else 0
    global_total = sum(s["total"] for s in global_stats.values()) if global_stats else 0

    from config import CINEMATIC_STYLES
    available_styles = list(CINEMATIC_STYLES.keys())

    if cat_total < 5 and global_total < 5:
        return base_style, {
            "method": "base_match",
            "reason": f"历史数据不足（品类{cat_total}次/全局{global_total}次），使用基础匹配",
            "confidence": 0.5,
        }

    use_global_fallback = cat_total < 5 and global_total >= 5

    scored_styles = []
    for style in available_styles:
        cat_s = cat_stats.get(style, {"success": 0, "total": 0, "rate": 0.0, "avg_quality": 0.0})
        global_s = global_stats.get(style, {"success": 0, "total": 0, "rate": 0.0, "avg_quality": 0.0})

        base_score = 0.3
        if style == base_style:
            base_score = 0.8

        if use_global_fallback:
            stats = global_s
            total_samples = global_s["total"]
            history_weight = min(total_samples / 20.0, 0.7)
            success_rate = global_s["rate"]
            final_score = base_score * (1 - history_weight * 0.5) + success_rate * history_weight
        elif cat_total >= 5:
            stats = cat_s
            total_samples = cat_s["total"]
            if total_samples >= 5:
                history_weight = min(total_samples / 20.0, 1.0)
                success_rate = cat_s["rate"]
                final_score = base_score * (1 - history_weight * 0.5) + success_rate * history_weight
            else:
                if global_s["total"] >= 3:
                    blended_rate = cat_s["rate"] * 0.3 + global_s["rate"] * 0.7
                    history_weight = min(global_s["total"] / 20.0, 0.6)
                    final_score = base_score * (1 - history_weight * 0.5) + blended_rate * history_weight
                else:
                    final_score = base_score
        else:
            final_score = base_score

        scored_styles.append({
            "style": style,
            "score": final_score,
            "success_rate": cat_s.get("rate", 0.0),
            "global_success_rate": global_s.get("rate", 0.0),
            "total_samples": cat_s.get("total", 0),
            "global_samples": global_s.get("total", 0),
            "avg_quality": cat_s.get("avg_quality", 0.0),
        })

    scored_styles.sort(key=lambda x: x["score"], reverse=True)

    import random
    if random.random() < 0.1 and len(scored_styles) > 3:
        explore_candidates = [s for s in scored_styles[1:] if s["total_samples"] < 3]
        if explore_candidates:
            chosen = random.choice(explore_candidates)
            return chosen["style"], {
                "method": "exploration",
                "reason": "探索新风格，收集数据（10%探索率）",
                "confidence": 0.3,
                "fallback_level": "global" if use_global_fallback else "category",
                "all_scores": scored_styles,
            }

    best = scored_styles[0]
    method = "evolved_global_fallback" if use_global_fallback else "evolved"
    reason_prefix = "全局 fallback：" if use_global_fallback else ""
    return best["style"], {
        "method": method,
        "reason": f"{reason_prefix}历史成功率 {best['success_rate']:.0%}（品类{int(best['total_samples'])}次/全局{int(best['global_samples'])}次样本）",
        "confidence": best["score"],
        "fallback_level": "global" if use_global_fallback else "category",
        "all_scores": scored_styles,
    }


# ── 自我进化系统：口播风格成功率统计与动态推荐 ──

def get_voiceover_style_success_rates(
    product_category: str = "default",
    lookback_days: int = 30,
) -> Dict[str, Dict[str, float]]:
    """统计各口播风格的历史成功率"""
    config = QUALITY_GATE_CONFIG.get("history_driven_optimization", {})
    if not config.get("record_to_history", True):
        return {}

    success_db_path = config.get("success_cases_db", "success_cases.json")
    failure_db_path = config.get("failure_cases_db", "failure_cases.json")
    if not Path(success_db_path).is_absolute():
        from config import OUTPUT_DIR
        success_db_path = OUTPUT_DIR / "history" / success_db_path
        failure_db_path = OUTPUT_DIR / "history" / failure_db_path

    successes = load_history_database(str(success_db_path))
    failures = load_history_database(str(failure_db_path))

    from datetime import datetime, timedelta
    cutoff = datetime.now() - timedelta(days=lookback_days)

    def _is_recent(case: dict) -> bool:
        ts = case.get("timestamp", "")
        try:
            return datetime.fromisoformat(ts) >= cutoff
        except Exception:
            return True

    def _matches_category(case: dict) -> bool:
        if product_category == "default":
            return True
        return case.get("product_category", "default") == product_category

    style_stats: Dict[str, Dict[str, float]] = {}

    for case in successes:
        if not _is_recent(case) or not _matches_category(case):
            continue
        vo_style = case.get("voiceover_style")
        if vo_style is None:
            extra = case.get("extra", {})
            vo_style = extra.get("voiceover_style", "unknown")
        if vo_style not in style_stats:
            style_stats[vo_style] = {"success": 0, "total": 0}
        style_stats[vo_style]["success"] += 1
        style_stats[vo_style]["total"] += 1

    for case in failures:
        if not _is_recent(case) or not _matches_category(case):
            continue
        vo_style = case.get("voiceover_style")
        if vo_style is None:
            extra = case.get("extra", {})
            vo_style = extra.get("voiceover_style", "unknown")
        if vo_style not in style_stats:
            style_stats[vo_style] = {"success": 0, "total": 0}
        style_stats[vo_style]["total"] += 1

    for style, stats in style_stats.items():
        stats["rate"] = stats["success"] / max(1, stats["total"])

    return style_stats


def evolve_voiceover_style_recommendation(
    product_category: str = "default",
    lookback_days: int = 30,
) -> Tuple[str, Dict[str, Any]]:
    """
    自我进化版口播风格推荐：结合历史成功率 + 品类匹配。

    三级 fallback 机制：
    1. 当前品类样本充足（≥5）→ 用品类历史数据为主
    2. 当前品类样本不足 → 品类基础匹配 + 全局历史数据加权混合
    3. 全局样本也不足 → 纯基础匹配
    """
    base_style = smart_pick_voiceover_style(product_category)

    cat_stats = get_voiceover_style_success_rates(product_category, lookback_days)
    global_stats = get_voiceover_style_success_rates("default", lookback_days) if product_category != "default" else {}

    cat_total = sum(s["total"] for s in cat_stats.values()) if cat_stats else 0
    global_total = sum(s["total"] for s in global_stats.values()) if global_stats else 0

    from tts_client import VOICEOVER_TEMPLATES
    available_styles = list(VOICEOVER_TEMPLATES.keys())

    if cat_total < 5 and global_total < 5:
        return base_style, {
            "method": "base_match",
            "reason": f"历史数据不足（品类{cat_total}次/全局{global_total}次），使用基础匹配",
            "confidence": 0.5,
        }

    use_global_fallback = cat_total < 5 and global_total >= 5

    scored_styles = []
    for style in available_styles:
        cat_s = cat_stats.get(style, {"success": 0, "total": 0, "rate": 0.0})
        global_s = global_stats.get(style, {"success": 0, "total": 0, "rate": 0.0})

        base_score = 0.3
        if style == base_style:
            base_score = 0.8

        if use_global_fallback:
            total_samples = global_s["total"]
            history_weight = min(total_samples / 20.0, 0.7)
            success_rate = global_s["rate"]
            final_score = base_score * (1 - history_weight * 0.5) + success_rate * history_weight
        elif cat_total >= 5:
            total_samples = cat_s["total"]
            if total_samples >= 5:
                history_weight = min(total_samples / 20.0, 1.0)
                success_rate = cat_s["rate"]
                final_score = base_score * (1 - history_weight * 0.5) + success_rate * history_weight
            else:
                if global_s["total"] >= 3:
                    blended_rate = cat_s["rate"] * 0.3 + global_s["rate"] * 0.7
                    history_weight = min(global_s["total"] / 20.0, 0.6)
                    final_score = base_score * (1 - history_weight * 0.5) + blended_rate * history_weight
                else:
                    final_score = base_score
        else:
            final_score = base_score

        scored_styles.append({
            "style": style,
            "score": final_score,
            "success_rate": cat_s.get("rate", 0.0),
            "global_success_rate": global_s.get("rate", 0.0),
            "total_samples": cat_s.get("total", 0),
            "global_samples": global_s.get("total", 0),
        })

    scored_styles.sort(key=lambda x: x["score"], reverse=True)

    best = scored_styles[0]
    method = "evolved_global_fallback" if use_global_fallback else "evolved"
    reason_prefix = "全局 fallback：" if use_global_fallback else ""
    return best["style"], {
        "method": method,
        "reason": f"{reason_prefix}历史成功率 {best['success_rate']:.0%}（品类{int(best['total_samples'])}次/全局{int(best['global_samples'])}次样本）",
        "confidence": best["score"],
        "fallback_level": "global" if use_global_fallback else "category",
        "all_scores": scored_styles,
    }


# ── 自我进化系统：人脸质量动态策略 ──

def get_face_quality_failure_rate(
    product_category: str = "default",
    narrative_type: str = "",
    lookback_days: int = 30,
) -> Tuple[int, int, float]:
    """
    统计人脸变形的历史失败率（自我进化数据）。

    三级 fallback 机制：
    1. 当前品类 + 当前叙事类型 → 优先使用
    2. 全品类 + 当前叙事类型 → 品类数据不足时 fallback
    3. 全品类 + 全叙事类型 → 叙事类型数据也不足时 fallback
    4. 冷启动默认 → 全局数据也不足时

    Args:
        product_category: 产品品类
        narrative_type: 叙事类型（hook/turning/result/review）
        lookback_days: 回溯天数

    Returns:
        (失败次数, 总样本数, 失败率)
    """
    config = QUALITY_GATE_CONFIG.get("history_driven_optimization", {})
    if not config.get("record_to_history", True):
        return 0, 0, 0.0

    failure_db_path = config.get("failure_cases_db", "failure_cases.json")
    success_db_path = config.get("success_cases_db", "success_cases.json")
    if not Path(failure_db_path).is_absolute():
        from config import OUTPUT_DIR
        failure_db_path = OUTPUT_DIR / "history" / failure_db_path
        success_db_path = OUTPUT_DIR / "history" / success_db_path

    failure_cases = load_history_database(str(failure_db_path))
    success_cases = load_history_database(str(success_db_path))

    from datetime import datetime, timedelta
    cutoff = datetime.now() - timedelta(days=lookback_days)

    def _is_recent(case):
        try:
            ts = datetime.fromisoformat(case.get("timestamp", ""))
            return ts >= cutoff
        except Exception:
            return True

    recent_failures = [c for c in failure_cases if _is_recent(c)]
    recent_successes = [c for c in success_cases if _is_recent(c)]

    def _filter_and_calc(fails, succs, cat_filter, narr_filter):
        f = fails
        s = succs
        if cat_filter and cat_filter != "default":
            f = [c for c in f if c.get("product_category") == cat_filter]
            s = [c for c in s if c.get("product_category") == cat_filter]
        if narr_filter:
            f = [c for c in f if c.get("narrative_type") == narr_filter]
            s = [c for c in s if c.get("narrative_type") == narr_filter]
        face_fails = [c for c in f if c.get("failure_type") == "character_face_distortion"]
        total = len(f) + len(s)
        rate = len(face_fails) / max(1, total)
        return len(face_fails), total, rate

    # 第1级：当前品类 + 当前叙事类型
    face_fails, total, rate = _filter_and_calc(
        recent_failures, recent_successes, product_category, narrative_type
    )
    if total >= 5:
        return face_fails, total, rate

    # 第2级：全品类 + 当前叙事类型（品类数据不足，用同叙事类型的全局数据）
    if narrative_type:
        g_face_fails, g_total, g_rate = _filter_and_calc(
            recent_failures, recent_successes, "default", narrative_type
        )
        if g_total >= 5:
            return g_face_fails, g_total, g_rate

    # 第3级：全品类 + 全叙事类型（叙事类型数据也不足，用全局数据）
    all_face_fails, all_total, all_rate = _filter_and_calc(
        recent_failures, recent_successes, "default", ""
    )
    return all_face_fails, all_total, all_rate


def evolve_face_quality_strategy(
    product_category: str = "default",
    narrative_type: str = "hook",
    lookback_days: int = 30,
) -> Dict[str, Any]:
    """
    自我进化版人脸质量策略：根据历史失败率动态调整 best_of 和重试强度。

    核心逻辑：失败率越高，投入越多算力保证质量
    - 失败率 < 5%：默认 best_of=1，重试 3 次
    - 失败率 5-15%：best_of=2，重试 3 次
    - 失败率 15-30%：best_of=3，重试 4 次
    - 失败率 > 30%：best_of=4，重试 5 次 + 拉远镜头默认开启

    Args:
        product_category: 产品品类
        narrative_type: 叙事类型
        lookback_days: 回溯天数

    Returns:
        {best_of, max_retries, strategy, confidence, reason}
    """
    fail_count, total, fail_rate = get_face_quality_failure_rate(
        product_category=product_category,
        narrative_type=narrative_type,
        lookback_days=lookback_days,
    )

    # 样本量不足时使用保守策略
    if total < 3:
        return {
            "best_of": 1,
            "max_retries": 3,
            "strategy": "conservative_default",
            "confidence": 0.3,
            "reason": f"历史样本不足（{total}次），使用默认保守策略",
            "fail_rate": fail_rate,
            "sample_count": total,
        }

    if fail_rate < 0.05:
        best_of = 1
        max_retries = 3
        strategy = "lightweight"
        reason = f"人脸失败率低（{fail_rate:.1%}/{total}次样本），轻量策略"
    elif fail_rate < 0.15:
        best_of = 2
        max_retries = 3
        strategy = "balanced"
        reason = f"人脸失败率中等（{fail_rate:.1%}/{total}次样本），平衡策略 best_of=2"
    elif fail_rate < 0.30:
        best_of = 3
        max_retries = 4
        strategy = "aggressive"
        reason = f"人脸失败率较高（{fail_rate:.1%}/{total}次样本），加强策略 best_of=3"
    else:
        best_of = 4
        max_retries = 5
        strategy = "very_aggressive"
        reason = f"人脸失败率高（{fail_rate:.1%}/{total}次样本），强力策略 best_of=4"

    return {
        "best_of": best_of,
        "max_retries": max_retries,
        "strategy": strategy,
        "confidence": min(total / 20.0, 1.0),
        "reason": reason,
        "fail_rate": fail_rate,
        "sample_count": total,
    }


def record_face_quality_success(
    product_category: str = "default",
    narrative_type: str = "",
    style_preference: str = "",
    quality_score: float = 0.0,
    face_issue_count: int = 0,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """
    记录人脸质量成功案例（自我进化数据闭环的成功端）。

    Args:
        product_category: 产品品类
        narrative_type: 叙事类型
        style_preference: 风格偏好
        quality_score: 质量分
        face_issue_count: 人脸问题帧数（0 表示无问题）
        extra: 额外信息
    """
    config = QUALITY_GATE_CONFIG.get("history_driven_optimization", {})
    if not config.get("record_to_history", True):
        return

    success_db_path = config.get("success_cases_db", "success_cases.json")
    if not Path(success_db_path).is_absolute():
        from config import OUTPUT_DIR
        success_db_path = OUTPUT_DIR / "history" / success_db_path

    success_cases = load_history_database(str(success_db_path))

    from datetime import datetime
    case = {
        "timestamp": datetime.now().isoformat(),
        "product_category": product_category,
        "narrative_type": narrative_type,
        "style_preference": style_preference,
        "quality_score": quality_score,
        "face_issue_count": face_issue_count,
        "failure_type": "face_quality_pass",
        "extra": extra or {},
    }

    success_cases.append(case)
    if len(success_cases) > 1000:
        success_cases = success_cases[-1000:]
    save_history_database(str(success_db_path), success_cases)


# ── 失败预测模型 ──

@dataclass
class FailurePredictionResult:
    """失败预测结果"""
    predicted_success_rate: float = 0.0
    risk_level: str = "low"
    risk_factors: List[str] = field(default_factory=list)
    mitigation_suggestions: List[str] = field(default_factory=list)
    block_generation: bool = False


def predict_failure(
    quality_gate_result: QualityGateResult,
    coherence_result: CoherenceCheckResult,
    historical_result: HistoricalOptimizationResult,
) -> FailurePredictionResult:
    """
    基于多维度特征预测生成失败风险。

    Args:
        quality_gate_result: 质量门检查结果
        coherence_result: 连贯性检查结果
        historical_result: 历史优化结果

    Returns:
        失败预测结果
    """
    config = QUALITY_GATE_CONFIG.get("failure_prediction", {})
    if not config.get("enabled", True):
        return FailurePredictionResult(predicted_success_rate=0.80, risk_level="low")

    result = FailurePredictionResult()

    weights = config.get("failure_feature_weights", {})

    # 1. 参考图质量评分（0-100）
    ref_quality_scores = [
        100.0 if r.passed else 50.0
        for r in quality_gate_result.reference_checks
    ]
    ref_quality_avg = sum(ref_quality_scores) / max(1, len(ref_quality_scores))
    ref_quality_normalized = ref_quality_avg / 100.0

    # 2. Prompt 质量评分（0-100）
    prompt_quality_normalized = quality_gate_result.total_score / 100.0

    # 3. 参数配置评分（基于历史数据）
    param_config_score = historical_result.success_rate_prediction

    # 4. 脚本结构评分
    script_structure_passed = all(
        c.passed for c in quality_gate_result.contract_checks
        if c.category == "script"
    )
    script_structure_normalized = 1.0 if script_structure_passed else 0.5

    # 5. 连贯性评分
    if getattr(coherence_result, "is_preview", False):
        coherence_avg = 1.0
    else:
        coherence_avg = (
            coherence_result.narrative_flow_score +
            coherence_result.scene_transition_score +
            coherence_result.emotion_curve_score +
            coherence_result.character_consistency_score +
            coherence_result.product_logic_score
        ) / 5.0 / 100.0

    # 计算加权预测成功率
    predicted = (
        ref_quality_normalized * weights.get("reference_image_quality", 0.25) +
        prompt_quality_normalized * weights.get("prompt_quality", 0.30) +
        param_config_score * weights.get("parameter_config", 0.15) +
        script_structure_normalized * weights.get("script_structure", 0.15) +
        coherence_avg * weights.get("coherence", 0.15)
    )

    result.predicted_success_rate = predicted

    # 判断风险等级
    min_threshold = config.get("min_prediction_threshold", 0.50)

    if predicted < min_threshold:
        result.risk_level = "high"
        result.block_generation = config.get("block_on_low_prediction", True)

        # 识别风险因素
        if ref_quality_normalized < 0.7:
            result.risk_factors.append("参考图质量不达标")
            result.mitigation_suggestions.append("请更换高质量的参考图")

        if prompt_quality_normalized < 0.6:
            result.risk_factors.append("Prompt 质量评分过低")
            result.mitigation_suggestions.append("请优化 Prompt 描述，增加细节")

        if coherence_avg < 0.6:
            result.risk_factors.append("片段连贯性不足")
            result.mitigation_suggestions.append("请调整脚本叙事结构和过渡关键词")

        if param_config_score < 0.5:
            result.risk_factors.append("历史成功率低")
            result.mitigation_suggestions.append("建议参考历史成功案例调整参数")

    elif predicted < 0.70:
        result.risk_level = "medium"
        result.risk_factors.append("部分检查项存在风险")
        result.mitigation_suggestions.append("建议检查上述预警项后再生成")
    else:
        result.risk_level = "low"

    return result


# ── 参数锁定 ──

def ensure_parameter_lock(
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """
    确保生成参数符合锁定规则，保证结果可复现。

    Args:
        params: 原始参数

    Returns:
        锁定后的参数
    """
    config = QUALITY_GATE_CONFIG.get("parameter_lock", {})
    locked = params.copy()

    if config.get("force_seed", True):
        if locked.get("seed") is None:
            locked["seed"] = config.get("default_seed", 42)
            logger.info(f"已自动设置固定 seed: {locked['seed']}")

    if config.get("lock_fidelity", True):
        if "fidelity" not in locked:
            locked["fidelity"] = 0.9
            logger.info("已锁定 image_fidelity: 0.9")

    if config.get("lock_negative_prompt", True):
        if "negative_prompt" not in locked or not locked["negative_prompt"]:
            locked["negative_prompt"] = NEGATIVE_PROMPT
            logger.info("已锁定 negative_prompt")

    if config.get("lock_aspect_ratio", True):
        if "aspect_ratio" not in locked:
            locked["aspect_ratio"] = "9:16"
            logger.info("已锁定 aspect_ratio: 9:16")

    if config.get("lock_duration", True):
        if "duration" not in locked:
            locked["duration"] = 5
            logger.info("已锁定 duration: 5秒")

    return locked


# ── 成本估算 ──

def estimate_cost(
    *,
    num_clips: int = 5,
    duration_per_clip: int = 5,
    mode: str = "pro",
    generate_character_image: bool = True,
    generate_voiceover: bool = True,
    voiceover_chars: int = 200,
) -> CostEstimate:
    """
    估算单次生成的总成本。

    Args:
        num_clips: 片段数量
        duration_per_clip: 每片段时长（秒）
        mode: 生成模式（std/standard/pro/4k）
        generate_character_image: 是否生成角色定妆照
        generate_voiceover: 是否生成口播
        voiceover_chars: 口播字符数

    Returns:
        成本估算结果
    """
    config = QUALITY_GATE_CONFIG.get("cost_control", {})
    rules = COST_ESTIMATION_RULES

    estimate = CostEstimate(
        budget_limit=config.get("max_budget", 100.0),
    )

    estimate.breakdown = []

    if generate_character_image:
        img_cost = rules["image"].get("pro", 0.10)
        estimate.image_cost = img_cost
        estimate.breakdown.append({
            "item": "角色定妆照",
            "quantity": 1,
            "unit_price": img_cost,
            "total": img_cost,
        })

    video_cost_per_sec = rules["video"].get(mode, rules["video"]["standard"])
    video_total = video_cost_per_sec * duration_per_clip * num_clips
    estimate.video_cost = video_total
    estimate.breakdown.append({
        "item": f"视频片段 ({mode})",
        "quantity": num_clips,
        "unit_price": video_cost_per_sec * duration_per_clip,
        "total": video_total,
    })

    if generate_voiceover:
        tts_cost = rules["additional"].get("tts", 0.02) * voiceover_chars
        estimate.additional_cost += tts_cost
        estimate.breakdown.append({
            "item": "口播生成",
            "quantity": voiceover_chars,
            "unit_price": rules["additional"]["tts"],
            "total": tts_cost,
        })

    estimate.total_cost = estimate.image_cost + estimate.video_cost + estimate.additional_cost

    if config.get("enabled", True):
        estimate.within_budget = estimate.total_cost <= estimate.budget_limit

    if not estimate.within_budget:
        estimate.downgrade_options = _generate_downgrade_options(
            num_clips=num_clips,
            duration_per_clip=duration_per_clip,
            mode=mode,
            generate_character_image=generate_character_image,
            generate_voiceover=generate_voiceover,
            voiceover_chars=voiceover_chars,
            budget_limit=estimate.budget_limit,
            original_cost=estimate.total_cost,
        )

    return estimate


def _generate_downgrade_options(
    *,
    num_clips: int,
    duration_per_clip: int,
    mode: str,
    generate_character_image: bool,
    generate_voiceover: bool,
    voiceover_chars: int,
    budget_limit: float,
    original_cost: float,
) -> List[Dict[str, Any]]:
    """
    生成智能降级方案。

    返回按节省金额排序的降级选项列表。
    """
    rules = COST_ESTIMATION_RULES
    downgrade_order = QUALITY_GATE_CONFIG.get("cost_control", {}).get(
        "downgrade_order", ["4k", "pro", "standard", "std"]
    )

    options = []

    current_mode_index = downgrade_order.index(mode) if mode in downgrade_order else 1

    for i in range(current_mode_index + 1, len(downgrade_order)):
        new_mode = downgrade_order[i]
        video_cost_per_sec = rules["video"].get(new_mode, rules["video"]["standard"])
        video_total = video_cost_per_sec * duration_per_clip * num_clips

        img_cost = rules["image"].get("std", 0.05) if generate_character_image else 0
        tts_cost = rules["additional"].get("tts", 0.02) * voiceover_chars if generate_voiceover else 0

        new_total = img_cost + video_total + tts_cost

        if new_total <= budget_limit:
            savings = original_cost - new_total
            options.append({
                "mode": new_mode,
                "new_cost": new_total,
                "savings": savings,
                "savings_percent": (savings / original_cost) * 100,
                "description": f"将生成模式从 {mode} 降级到 {new_mode}",
                "impact": f"视频质量略有下降，但仍保持可接受水平",
            })

    if not options:
        for duration_reduction in [4, 3, 2]:
            if duration_reduction < duration_per_clip:
                video_cost_per_sec = rules["video"].get(mode, rules["video"]["standard"])
                video_total = video_cost_per_sec * duration_reduction * num_clips
                img_cost = rules["image"].get("std", 0.05) if generate_character_image else 0
                tts_cost = rules["additional"].get("tts", 0.02) * voiceover_chars if generate_voiceover else 0

                new_total = img_cost + video_total + tts_cost

                if new_total <= budget_limit:
                    savings = original_cost - new_total
                    options.append({
                        "mode": mode,
                        "duration": duration_reduction,
                        "new_cost": new_total,
                        "savings": savings,
                        "savings_percent": (savings / original_cost) * 100,
                        "description": f"保持 {mode} 模式，将片段时长从 {duration_per_clip} 秒缩短到 {duration_reduction} 秒",
                        "impact": f"视频总时长减少，但叙事节奏更紧凑",
                    })

    options.sort(key=lambda x: x["savings"], reverse=True)
    return options


# ── 契约验证 ──

def check_script_structure(ad_script: Dict[str, Any]) -> CheckResult:
    """检查脚本结构完整性"""
    required_segments = {"hook", "turning_point", "showcase", "result", "cta"}
    # 兼容命名：turning == turning_point
    alias_map = {
        "turning": "turning_point",
        "pain": "turning_point",
        "pain_point": "turning_point",
        "solution": "showcase",
        "demo": "showcase",
        "review": "result",
    }

    segments = ad_script.get("segments", [])
    existing_raw = {seg.get("narrative", "") for seg in segments}
    # 把别名映射到标准名
    existing_segments = set()
    for s in existing_raw:
        existing_segments.add(alias_map.get(s, s))

    result = CheckResult(
        passed=True,
        category="script",
        name="脚本结构检查",
    )

    if len(segments) < 3:
        result.messages.append(f"片段数较少（{len(segments)}段），跳过完整结构检查（预览/快速模式）")
        return result

    missing = required_segments - existing_segments
    extra = existing_segments - required_segments

    if missing:
        result.passed = False
        result.messages.append(f"缺少必要的叙事段：{', '.join(missing)}")
        result.suggestions.append(f"请补充以下叙事段：{', '.join(missing)}")

    if extra:
        result.messages.append(f"存在额外的叙事段（可保留）：{', '.join(extra)}")

    if result.passed:
        result.messages.append(f"脚本结构完整，包含 {len(segments)} 个叙事段")

    return result


def check_character_bible(bible: Optional[Dict[str, Any]]) -> CheckResult:
    """检查角色圣经完整性"""
    required_fields = {"id", "name", "gender", "hair_style", "outfit"}

    result = CheckResult(
        passed=True,
        category="bible",
        name="角色圣经检查",
    )

    if not bible:
        result.passed = False
        result.messages.append("角色圣经为空")
        result.suggestions.append("请提供完整的角色描述")
        return result

    missing = required_fields - set(bible.keys())
    if missing:
        result.passed = False
        result.messages.append(f"角色圣经缺少字段：{', '.join(missing)}")
        result.suggestions.append(f"请补充以下字段：{', '.join(missing)}")
    else:
        result.messages.append(f"角色圣经完整（{bible.get('name', '未知角色')}）")

    return result


def check_product_bible(bible: Optional[Dict[str, Any]]) -> CheckResult:
    """检查商品圣经完整性"""
    required_fields = {"name", "category", "packaging", "primary_color"}

    result = CheckResult(
        passed=True,
        category="bible",
        name="商品圣经检查",
    )

    if not bible:
        result.passed = False
        result.messages.append("商品圣经为空")
        result.suggestions.append("请提供完整的商品描述")
        return result

    missing = required_fields - set(bible.keys())
    if missing:
        result.passed = False
        result.messages.append(f"商品圣经缺少字段：{', '.join(missing)}")
        result.suggestions.append(f"请补充以下字段：{', '.join(missing)}")
    else:
        result.messages.append(f"商品圣经完整（{bible.get('name', '未知商品')}）")

    return result


def check_scene_continuity(config: Optional[Dict[str, Any]]) -> CheckResult:
    """检查场景连续性配置"""
    result = CheckResult(
        passed=True,
        category="scene",
        name="场景连续性检查",
    )

    if not config:
        result.passed = False
        result.messages.append("场景连续性配置为空")
        result.suggestions.append("请配置 SCENE_CONTINUITY_CONFIG")
        return result

    if not config.get("use_scene_anchor", False):
        result.messages.append("场景锚点未启用，可能导致场景漂移")
        result.suggestions.append("建议启用 use_scene_anchor")

    result.messages.append("场景连续性配置检查通过")
    return result


def check_all_contracts(
    ad_script: Dict[str, Any],
    character_bible: Optional[Dict[str, Any]],
    product_bible: Optional[Dict[str, Any]],
    scene_continuity_config: Optional[Dict[str, Any]],
) -> List[CheckResult]:
    """执行所有契约验证"""
    config = QUALITY_GATE_CONFIG.get("contract_verification", {})
    results = []

    if config.get("check_script_structure", True):
        results.append(check_script_structure(ad_script))

    if config.get("check_character_bible", True):
        results.append(check_character_bible(character_bible))

    if config.get("check_product_bible", True):
        results.append(check_product_bible(product_bible))

    if config.get("check_scene_continuity", True):
        results.append(check_scene_continuity(scene_continuity_config))

    return results


# ── 主预检流程 ──

def run_quality_gate(
    *,
    ad_script: Dict[str, Any],
    product_image_path: Optional[Path] = None,
    character_image_paths: Optional[List[Path]] = None,
    prompts: Optional[List[str]] = None,
    character_bible: Optional[Dict[str, Any]] = None,
    product_bible: Optional[Dict[str, Any]] = None,
    scene_continuity_config: Optional[Dict[str, Any]] = None,
    num_clips: int = 5,
    duration_per_clip: int = 5,
    mode: str = "pro",
    product_category: str = "美妆",
    style_preference: str = "cinematic",
    auto_apply_optimization: bool = True,
) -> QualityGateResult:
    """
    运行完整的质量前置控制流程（增强版）。

    Args:
        ad_script: 广告脚本
        product_image_path: 商品参考图路径
        character_image_paths: 角色参考图路径列表
        prompts: 所有片段的 prompt 列表
        character_bible: 角色圣经
        product_bible: 商品圣经
        scene_continuity_config: 场景连续性配置
        num_clips: 片段数量
        duration_per_clip: 每片段时长
        mode: 生成模式
        product_category: 产品品类（用于历史数据优化）
        style_preference: 风格偏好（用于历史数据优化）
        auto_apply_optimization: 是否自动应用优化后的 Prompt

    Returns:
        质量门检查结果（包含优化后的 Prompt 和推荐参数）
    """
    config = QUALITY_GATE_CONFIG
    if not config.get("enabled", True):
        logger.info("质量前置控制已禁用")
        return QualityGateResult(passed=True, summary=["质量前置控制已禁用"])

    logger.info("🚪 启动质量前置控制检查（v2 综合评分制）...")

    result = QualityGateResult(passed=True)
    total_deduction = 0.0
    has_fatal_blocker = False

    # ── 参考图预检 ──
    if config.get("reference_check", {}).get("enabled", True):
        logger.info("📷 检查参考图质量...")
        ref_results = check_all_reference_images(
            product_image_path,
            character_image_paths,
        )
        result.reference_checks = ref_results
        ref_total_deduction = 0.0
        for r in ref_results:
            result.blocker_count += len(r.blockers)
            result.warning_count += len(r.warnings)
            if r.blockers:
                has_fatal_blocker = True
                result.passed = False
            ref_total_deduction += (100 - r.quality_score)
        # 参考图整体权重 40%，按数量平均
        if ref_results:
            avg_ref_deduction = ref_total_deduction / len(ref_results)
            total_deduction += avg_ref_deduction * 0.4

        # ── v2 新增：参考图去重（相似图只保留质量最高的）──
        if auto_apply_optimization and len(ref_results) > 1:
            logger.info("🖼️  参考图去重（移除高度相似的图片）...")
            deduped, removed_names, removed_count = deduplicate_reference_images(
                ref_results
            )
            if removed_count > 0:
                result.deduplicated_reference_images = deduped
                result.reference_dedup_removed = removed_names
                result.optimization_count += removed_count
                result.suggestions.append(
                    f"REF_DEDUP: 移除了 {removed_count} 张相似参考图，保留质量最高的"
                )
                for name in removed_names[:3]:
                    result.suggestions.append(f"REF_DEDUP:  {name}")
                logger.info(f"   移除了 {removed_count} 张相似参考图")
                ref_results = deduped
                result.reference_checks = deduped
            else:
                logger.info("   未发现高度相似的参考图")

        # ── v2 新增：参考图智能排序（高质量图前置）──
        if auto_apply_optimization and len(ref_results) > 1:
            logger.info("📊 参考图智能排序（高质量图前置）...")
            sorted_refs, sort_notes = smart_sort_reference_images(ref_results)
            if sort_notes:
                result.sorted_reference_images = sorted_refs
                result.reference_sort_notes = sort_notes
                result.suggestions.extend([f"REF_SORT: {n}" for n in sort_notes])
                result.reference_checks = sorted_refs
                logger.info(f"   {sort_notes[0]}")
            else:
                logger.info("   参考图顺序无需调整")

    # ── Prompt 预校验（返回优化后的 Prompt）──
    if config.get("prompt_check", {}).get("enabled", True) and prompts:
        logger.info("✍️ 检查 Prompt 质量（自动优化）...")
        prompt_results, optimized_prompts = check_all_prompts(
            prompts,
            auto_apply=auto_apply_optimization,
        )
        result.prompt_checks = prompt_results
        result.optimized_prompts = optimized_prompts
        prompt_total_deduction = 0.0
        for r in prompt_results:
            result.blocker_count += len(r.blockers)
            result.warning_count += len(r.warnings)
            if r.blockers:
                has_fatal_blocker = True
                result.passed = False
            prompt_total_deduction += (100 - r.score)
        # Prompt 整体权重 35%，按数量平均
        if prompt_results:
            avg_prompt_deduction = prompt_total_deduction / len(prompt_results)
            total_deduction += avg_prompt_deduction * 0.35

    # ── v2 深度优化流水线（Prompt 增强 + 结构优化）──
    if (
        config.get("prompt_check", {}).get("enabled", True)
        and prompts
        and auto_apply_optimization
    ):
        logger.info("🎨 运行 v2 深度 Prompt 优化流水线...")
        v2_optimized = _run_v2_prompt_optimization_pipeline(
            prompts=optimized_prompts if result.optimized_prompts else prompts,
            ad_script=ad_script,
            character_bible=character_bible,
            product_bible=product_bible,
        )
        if v2_optimized:
            result.optimized_prompts = v2_optimized
            logger.info(f"✅ v2 深度优化完成，共优化 {len(v2_optimized)} 个片段")

    # ── 负面词智能优化（精简 + 场景化增强）──
    if (
        config.get("prompt_check", {}).get("enabled", True)
        and auto_apply_optimization
    ):
        logger.info("🎭 运行负面词智能优化...")
        try:
            segments = ad_script.get("segments", [])
            scene_type = ""
            if segments:
                first_narrative = segments[0].get("narrative_function", "") or segments[0].get("type", "")
                if first_narrative in ("showcase", "product"):
                    scene_type = "product"
                elif first_narrative in ("pain", "hook", "result"):
                    scene_type = "character"
                else:
                    scene_type = "action"

            compressed = smart_compress_negative_prompt(
                NEGATIVE_PROMPT, scene_type=scene_type, target_count=15
            )

            effective_prompts = result.optimized_prompts if result.optimized_prompts else (prompts or [])
            if effective_prompts:
                enhanced = enhance_negative_prompt(compressed, effective_prompts[0])
            else:
                enhanced = compressed

            result.optimized_negative_prompt = enhanced
            logger.info(f"✅ 负面词优化完成：{len(NEGATIVE_PROMPT.split(','))} → {len(enhanced.split(','))} 个")
        except Exception as e:
            logger.warning(f"负面词优化失败，跳过：{e}")

    # ── 参考图自动预处理（质量不达标的自动优化）──
    if (
        config.get("reference_check", {}).get("enabled", True)
        and config.get("reference_check", {}).get("auto_preprocess", True)
        and auto_apply_optimization
        and result.reference_checks
    ):
        logger.info("🖼️  运行参考图自动预处理...")
        preprocessed = []
        for ref_result in result.reference_checks:
            if ref_result.quality_score < 80 and ref_result.path and ref_result.path.exists():
                try:
                    new_path, info = auto_preprocess_reference_image(
                        ref_result.path,
                        image_type=ref_result.image_type,
                    )
                    if info.get("processed"):
                        preprocessed.append(info)
                        logger.info(f"  ✅ {ref_result.path.name} 已优化：{', '.join(info['operations'])}")
                except Exception as e:
                    logger.warning(f"  预处理 {ref_result.path.name} 失败：{e}")
        result.preprocessed_reference_images = preprocessed
        if preprocessed:
            logger.info(f"✅ 共预处理 {len(preprocessed)} 张参考图")

    # ── 契约验证 ──
    if config.get("contract_verification", {}).get("enabled", True):
        logger.info("📝 验证契约完整性...")
        contract_results = check_all_contracts(
            ad_script,
            character_bible,
            product_bible,
            scene_continuity_config,
        )
        result.contract_checks = contract_results
        contract_failed = sum(1 for r in contract_results if not r.passed)
        contract_total = len(contract_results)
        if contract_failed > 0:
            # 判断是否有致命的合同问题（脚本结构失败）
            script_failed = any(
                not r.passed for r in contract_results if r.category == "script"
            )
            if script_failed:
                has_fatal_blocker = True
                result.passed = False
            # 合同整体权重 15%，按失败比例扣分
            contract_deduction_ratio = contract_failed / max(1, contract_total)
            total_deduction += contract_deduction_ratio * 100 * 0.15

    # ── 片段连贯性预检 ──
    if config.get("coherence_check", {}).get("enabled", True):
        logger.info("🔗 检查片段连贯性...")
        coherence_result = check_segment_coherence(ad_script, prompts or [])
        result.coherence_result = coherence_result
        if not coherence_result.passed:
            result.warning_count += 1
            total_deduction += 10  # 连贯性问题是 warning 级别

    # ── 历史数据驱动优化 ──
    if config.get("history_driven_optimization", {}).get("enabled", True):
        logger.info("📊 基于历史数据优化参数...")
        historical_result = optimize_by_history(
            product_category,
            style_preference,
            num_clips,
        )
        result.historical_result = historical_result

        if historical_result.recommended_seed:
            result.recommended_parameters["seed"] = historical_result.recommended_seed
        if historical_result.recommended_fidelity:
            result.recommended_parameters["fidelity"] = historical_result.recommended_fidelity
        if historical_result.recommended_mode:
            result.recommended_parameters["mode"] = historical_result.recommended_mode

    # ── v2 新增：运动强度检测与校准 ──
    if prompts and auto_apply_optimization:
        logger.info("🎬 检测运动强度并校准...")
        motion_issues = []
        high_motion_count = 0
        calibrated_prompts = []
        for i, p in enumerate(result.optimized_prompts or prompts):
            level, score, matched = detect_motion_intensity(p)
            if level == "high":
                high_motion_count += 1
                motion_issues.append(f"片段 {i+1}: 运动强度 {level} ({score:.2f})")
            # 自动校准：运动过高时注入约束词
            calibrated, changed, note = calibrate_motion_intensity(p, target_level="medium", current_level=level)
            calibrated_prompts.append(calibrated)
            if changed:
                result.optimization_count += 1
        if result.optimized_prompts or calibrated_prompts != prompts:
            result.optimized_prompts = calibrated_prompts

        # 运动一致性检查
        if len(prompts) >= 2:
            motion_passed, motion_score, motion_cons_issues = check_motion_consistency(
                result.optimized_prompts or prompts
            )
            motion_issues.extend(motion_cons_issues)
            if not motion_passed:
                total_deduction += 5  # 运动一致性问题扣分
                result.warning_count += 1
        result.suggestions.extend([f"MOTION: {m}" for m in motion_issues[:5]])
        logger.info(f"   高运动强度片段：{high_motion_count}/{len(prompts)}，已自动校准")

    # ── v2 新增：Prompt 冲突检测与自动消解 ──
    if prompts and auto_apply_optimization:
        logger.info("⚠️ 检测 Prompt 内部冲突并自动消解...")
        total_conflicts = 0
        resolved_prompts = []
        all_conflict_descs = []

        for i, p in enumerate(result.optimized_prompts or prompts):
            resolved, count, descs = resolve_prompt_conflicts(p, prefer_style=style_preference)
            resolved_prompts.append(resolved)
            if count > 0:
                total_conflicts += count
                all_conflict_descs.append(f"片段 {i+1}: {descs[0]}")
                result.optimization_count += count

        if total_conflicts > 0:
            result.optimized_prompts = resolved_prompts
            result.warning_count += min(total_conflicts, 3)
            total_deduction += min(total_conflicts * 2, 10)
            result.suggestions.extend([f"CONFLICT: {c}" for c in all_conflict_descs[:5]])
            result.suggestions.append(f"CONFLICT: 共发现 {total_conflicts} 处 Prompt 内部冲突，已自动消解")
            logger.info(f"   发现 {total_conflicts} 处冲突，已自动消解")
        else:
            logger.info("   未发现 Prompt 内部冲突")

    # ── v2 新增：相邻片段视觉跳变检测与过渡优化 ──
    if prompts and auto_apply_optimization and len(prompts) >= 2:
        logger.info("🎬 检测相邻片段视觉跳变并优化过渡...")
        transitions, trans_issues, trans_details = check_visual_transitions(
            result.optimized_prompts or prompts
        )

        if transitions:
            # 添加过渡提示词
            transitioned_prompts, trans_modified = add_transition_hints(
                result.optimized_prompts or prompts,
                transitions,
                trans_details,
            )
            if trans_modified > 0:
                result.optimized_prompts = transitioned_prompts
                result.optimization_count += trans_modified

            total_deduction += min(len(transitions) * 2, 8)
            result.warning_count += min(len(transitions), 3)
            result.suggestions.extend([f"TRANSITION: {t}" for t in trans_issues[:4]])
            result.suggestions.append(
                f"TRANSITION: {len(transitions)} 处跳变已添加过渡提示词"
            )
            logger.info(f"   发现 {len(transitions)} 处视觉跳变，已给 {trans_modified} 个片段添加过渡提示")
        else:
            logger.info("   相邻片段视觉过渡自然")

    # ── v2 新增：风格一致性检测与锚定 ──
    if prompts and auto_apply_optimization and len(prompts) >= 2:
        logger.info("🎨 检查风格一致性并锚定...")
        style_passed, style_score, dominant_style, style_issues = check_style_consistency(
            result.optimized_prompts or prompts
        )
        if not style_passed:
            total_deduction += 8
            result.warning_count += 1

        # 自动锚定风格
        anchored_prompts, style_modified, style_note = anchor_style_to_prompts(
            result.optimized_prompts or prompts,
            target_style=dominant_style,
        )
        if style_modified > 0:
            result.optimized_prompts = anchored_prompts
            result.optimization_count += style_modified

        result.suggestions.extend([f"STYLE: {s}" for s in style_issues[:3]])
        result.suggestions.append(f"STYLE: 主导风格 {dominant_style}，已锚定 {style_modified} 个片段")
        logger.info(f"   主导风格：{dominant_style}，一致性：{style_score:.2f}，锚定 {style_modified} 个片段")

    # ── v2 新增：负面词智能增强 v2（基于历史失败数据）──
    if auto_apply_optimization:
        logger.info("🚫 负面词智能增强 v2（历史失败数据驱动）...")
        base_neg = NEGATIVE_PROMPT
        enhanced_neg, added_neg, failure_rates = enhance_negative_prompt_v2(
            base_neg,
            product_category=product_category,
        )
        if added_neg:
            result.optimized_negative_prompt = enhanced_neg
            result.optimization_count += len(added_neg)
            result.suggestions.append(
                f"NEGATIVE_V2: 基于历史失败数据追加 {len(added_neg)} 个负面词：{', '.join(added_neg[:5])}..."
            )
            high_fail_types = [ft for ft, r in failure_rates.items() if r > 0.05]
            if high_fail_types:
                result.suggestions.append(
                    f"NEGATIVE_V2: 高频失败类型：{', '.join(high_fail_types[:3])}"
                )
            logger.info(f"   追加 {len(added_neg)} 个负面词（基于 {len(failure_rates)} 类失败模式）")
        else:
            logger.info("   历史数据不足或无高频失败类型，使用 v1 负面词增强")

    # ── 成本估算 ──
    if config.get("cost_control", {}).get("enabled", True):
        logger.info("💰 估算生成成本...")
        effective_mode = result.recommended_parameters.get("mode", mode)
        cost_estimate = estimate_cost(
            num_clips=num_clips,
            duration_per_clip=duration_per_clip,
            mode=effective_mode,
            generate_character_image=character_bible is not None,
        )
        result.cost_estimate = cost_estimate
        if not cost_estimate.within_budget:
            has_fatal_blocker = True
            result.passed = False

    # ── 计算综合质量分 ──
    result.quality_score = max(0.0, min(100.0, 100.0 - total_deduction))

    # ── 失败预测模型 ──
    if config.get("failure_prediction", {}).get("enabled", True):
        logger.info("🎯 预测生成成功率...")
        failure_prediction = predict_failure(
            result,
            result.coherence_result or CoherenceCheckResult(passed=True),
            result.historical_result or HistoricalOptimizationResult(),
        )
        result.failure_prediction = failure_prediction

        if failure_prediction.block_generation:
            has_fatal_blocker = True
            result.passed = False
            result.blocker_count += 1

    # ── 综合评分判定（无致命 blocker 时使用）──
    min_pass_score = config.get("min_pass_score", 55)
    if not has_fatal_blocker:
        # 没有致命问题，按综合评分判定
        if result.quality_score >= min_pass_score:
            result.passed = True
        else:
            result.passed = False

    # ── 计算 Prompt 平均评分（兼容旧字段）──
    scores = []
    if result.prompt_checks:
        scores.extend([p.score for p in result.prompt_checks if p.score > 0])
    if scores:
        result.total_score = sum(scores) / len(scores)

    # ── 收集优化建议 ──
    result.optimization_suggestions = []

    if result.cost_estimate and not result.cost_estimate.within_budget:
        for option in result.cost_estimate.downgrade_options[:3]:
            result.optimization_suggestions.append(OptimizationSuggestion(
                type="cost",
                original_value=f"模式: {mode}, 时长: {duration_per_clip}秒",
                suggested_value=f"模式: {option.get('mode', mode)}, 时长: {option.get('duration', duration_per_clip)}秒",
                impact=option.get("impact", ""),
                estimated_savings=option.get("savings", 0.0),
            ))

    for prompt_result in result.prompt_checks:
        if prompt_result.optimized_prompt:
            result.optimization_suggestions.append(OptimizationSuggestion(
                type="prompt",
                original_value=f"原始评分: {prompt_result.score:.1f}",
                suggested_value=f"优化后评分: 预计提升",
                impact="使用优化后的 Prompt 可提高生成质量",
                estimated_savings=0.0,
            ))

    if result.historical_result and result.historical_result.optimization_applied:
        for opt_applied in result.historical_result.optimization_applied:
            result.optimization_suggestions.append(OptimizationSuggestion(
                type="history",
                original_value="默认参数",
                suggested_value=str(result.recommended_parameters),
                impact=opt_applied,
                estimated_savings=0.0,
            ))

    # ── 生成总结 ──
    if result.passed:
        if result.warning_count > 0:
            result.summary.append(
                f"✅ 质量门通过（综合评分 {result.quality_score:.0f}/100，{result.warning_count} 个警告）"
            )
            result.summary.append("⚠️  存在改进空间，但不影响生成，建议优化后效果更佳")
        else:
            result.summary.append(
                f"✅ 质量门通过（综合评分 {result.quality_score:.0f}/100）"
            )
        if result.failure_prediction and result.failure_prediction.predicted_success_rate >= 0.80:
            result.summary.append("🎯 预测成功率 ≥80%，强烈推荐生成")
    else:
        if result.blocker_count > 0:
            result.summary.append(
                f"❌ 质量门未通过（综合评分 {result.quality_score:.0f}/100，{result.blocker_count} 个致命问题）"
            )
        else:
            result.summary.append(
                f"❌ 质量门未通过（综合评分 {result.quality_score:.0f}/{min_pass_score}）"
            )
        if result.failure_prediction and result.failure_prediction.block_generation:
            result.summary.append("🎯 预测成功率过低，已阻止生成")

    return result


# ── 自进化：质量反馈记录 ──

def _get_feedback_db_path() -> Path:
    """获取反馈数据库文件路径"""
    from config import OUTPUT_DIR
    db_dir = Path(OUTPUT_DIR) / "quality_feedback"
    db_dir.mkdir(parents=True, exist_ok=True)
    return db_dir / "feedback_records.json"


def record_quality_feedback(
    quality_gate_result: QualityGateResult,
    actual_result: str,
    actual_quality_score: float = 0.0,
    notes: str = "",
) -> None:
    """
    记录生成结果反馈，用于质量门自进化。

    数据积累后，可以：
    - 分析哪些 warning 其实不影响生成质量
    - 调整阈值，减少误判
    - 识别高风险模式

    Args:
        quality_gate_result: 质量门检查结果
        actual_result: 实际结果（success / failed / partial）
        actual_quality_score: 实际质量评分（0-100，人工或自动评估）
        notes: 备注
    """
    config = QUALITY_GATE_CONFIG.get("self_evolution", {})
    if not config.get("enabled", True):
        return

    db_path = _get_feedback_db_path()

    record = {
        "timestamp": __import__("datetime").datetime.now().isoformat(),
        "quality_score": quality_gate_result.quality_score,
        "blocker_count": quality_gate_result.blocker_count,
        "warning_count": quality_gate_result.warning_count,
        "passed": quality_gate_result.passed,
        "actual_result": actual_result,
        "actual_quality_score": actual_quality_score,
        "ref_checks": [
            {
                "type": r.image_type,
                "quality_score": r.quality_score,
                "blockers": len(r.blockers),
                "warnings": len(r.warnings),
            }
            for r in quality_gate_result.reference_checks
        ],
        "prompt_scores": [p.score for p in quality_gate_result.prompt_checks],
        "notes": notes,
    }

    try:
        if db_path.exists():
            with open(db_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = []

        data.append(record)

        # 最多保留 1000 条记录
        if len(data) > 1000:
            data = data[-1000:]

        with open(db_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        logger.info(f"📝 质量反馈已记录（{actual_result}），累计 {len(data)} 条")
    except Exception as e:
        logger.warning(f"记录质量反馈失败：{e}")


def get_self_evolution_insights() -> Dict[str, Any]:
    """
    从历史反馈数据中提取洞察，用于优化质量门。

    Returns:
        包含统计信息和优化建议的字典
    """
    db_path = _get_feedback_db_path()
    if not db_path.exists():
        return {"total_records": 0, "message": "暂无反馈数据"}

    try:
        with open(db_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {"total_records": 0, "message": "反馈数据读取失败"}

    if not data:
        return {"total_records": 0, "message": "暂无反馈数据"}

    total = len(data)
    successes = [r for r in data if r["actual_result"] == "success"]
    failures = [r for r in data if r["actual_result"] == "failed"]
    success_rate = len(successes) / max(1, total)

    # 分析：通过了但实际失败的（漏判）
    false_negatives = [r for r in data if r["passed"] and r["actual_result"] == "failed"]
    # 分析：没通过但实际可能成功的（误判）—— 这里用 partial 或人工标记
    false_positives = [r for r in data if not r["passed"] and r["actual_result"] == "success"]

    insights = {
        "total_records": total,
        "success_rate": success_rate,
        "false_negatives": len(false_negatives),
        "false_positives": len(false_positives),
        "avg_predicted_score": sum(r["quality_score"] for r in data) / total,
        "avg_actual_score": (
            sum(r["actual_quality_score"] for r in data if r["actual_quality_score"] > 0)
            / max(1, sum(1 for r in data if r["actual_quality_score"] > 0))
        ),
        "suggestions": [],
    }

    # 如果有足够数据，给出优化建议
    if total >= 10:
        if len(false_positives) > total * 0.2:
            insights["suggestions"].append(
                "误判率较高（>20%），建议适当放宽通过阈值"
            )
        if len(false_negatives) > total * 0.1:
            insights["suggestions"].append(
                "漏判率较高（>10%），建议收紧通过阈值或增加检测项"
            )

    return insights


def print_quality_gate_report(result: QualityGateResult) -> None:
    """打印质量门检查报告"""
    print("\n" + "=" * 60)
    print("🚪 质量前置控制报告")
    print("=" * 60)

    if result.passed:
        print("\n✅ 整体状态：通过")
    else:
        print("\n❌ 整体状态：失败")

    if result.total_score > 0:
        print(f"📊 Prompt 平均质量评分：{result.total_score:.1f}/100")

    if result.reference_checks:
        print("\n" + "-" * 60)
        print("📷 参考图检查结果")
        print("-" * 60)
        for ref in result.reference_checks:
            status = "✅" if ref.passed else "❌"
            print(f"\n{status} {ref.image_type}参考图：{ref.path.name}")
            print(f"   分辨率：{ref.width}x{ref.height}")
            print(f"   亮度：{ref.brightness:.1f} | 对比度：{ref.contrast:.1f}")
            if ref.subject_size_ratio > 0:
                print(f"   主体占比：{ref.subject_size_ratio:.1%} | 居中：{'是' if ref.subject_centered else '否'}")
            if ref.background_complexity > 0:
                print(f"   背景复杂度：{ref.background_complexity:.2f}")
            if ref.has_watermark:
                print(f"   水印检测：⚠️ 检测到可能的水印")
            if ref.image_type == "character":
                print(f"   正面人脸：{'是' if ref.is_frontal_face else '否'} | 人脸评分：{ref.face_score:.1f}")
            for msg in ref.messages:
                print(f"   • {msg}")
            for sug in ref.suggestions:
                print(f"   💡 {sug}")

    if result.prompt_checks:
        print("\n" + "-" * 60)
        print("✍️ Prompt 检查结果")
        print("-" * 60)
        for prompt in result.prompt_checks:
            status = "✅" if prompt.passed else "❌"
            print(f"\n{status} {prompt.name}")
            print(f"   长度：{prompt.length} 字符")
            print(f"   质量评分：{prompt.score:.1f}/100")
            if prompt.conflicts:
                print(f"   ❌ 语义冲突：{', '.join(prompt.conflicts)}")
            if prompt.repetitions:
                print(f"   ⚠️ 重复描述：{', '.join(prompt.repetitions)}")
            if prompt.semantic_issues:
                print(f"   ⚠️ 语义问题：{', '.join(prompt.semantic_issues)}")
            if prompt.optimized_prompt:
                print(f"   🔧 优化后：{prompt.optimized_prompt[:100]}...")
            for msg in prompt.messages:
                print(f"   • {msg}")
            for sug in prompt.suggestions:
                print(f"   💡 {sug}")

    if result.contract_checks:
        print("\n" + "-" * 60)
        print("📝 契约验证结果")
        print("-" * 60)
        for contract in result.contract_checks:
            status = "✅" if contract.passed else "❌"
            print(f"\n{status} {contract.name}")
            for msg in contract.messages:
                print(f"   • {msg}")
            for sug in contract.suggestions:
                print(f"   💡 {sug}")

    if result.cost_estimate:
        print("\n" + "-" * 60)
        print("💰 成本估算")
        print("-" * 60)
        print(f"\n总预估费用：¥{result.cost_estimate.total_cost:.2f}")
        print(f"预算上限：¥{result.cost_estimate.budget_limit:.2f}")
        if result.cost_estimate.within_budget:
            print("状态：✅ 在预算范围内")
        else:
            print("状态：❌ 超出预算")

        if result.cost_estimate.downgrade_options:
            print("\n💡 降级方案：")
            for i, option in enumerate(result.cost_estimate.downgrade_options[:3], 1):
                print(f"   {i}. {option['description']}")
                print(f"      新费用：¥{option['new_cost']:.2f}")
                print(f"      节省：¥{option['savings']:.2f} ({option['savings_percent']:.0f}%)")
                print(f"      影响：{option['impact']}")

        print("\n费用明细：")
        for item in result.cost_estimate.breakdown:
            print(f"   • {item['item']}: ¥{item['total']:.2f}")

    if result.optimization_suggestions:
        print("\n" + "-" * 60)
        print("💡 优化建议")
        print("-" * 60)
        for suggestion in result.optimization_suggestions:
            print(f"\n   {suggestion.type.upper()}: {suggestion.impact}")
            print(f"      原始：{suggestion.original_value}")
            print(f"      建议：{suggestion.suggested_value}")
            if suggestion.estimated_savings > 0:
                print(f"      预计节省：¥{suggestion.estimated_savings:.2f}")

    print("\n" + "=" * 60)
    for line in result.summary:
        print(line)
    print("=" * 60 + "\n")