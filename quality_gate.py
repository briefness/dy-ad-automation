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
    # 新增字段
    subject_centered: bool = True
    subject_size_ratio: float = 0.0
    has_watermark: bool = False
    background_complexity: float = 0.0
    is_frontal_face: bool = True
    face_score: float = 0.0
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
    # 新增字段
    conflicts: List[str] = field(default_factory=list)
    repetitions: List[str] = field(default_factory=list)
    semantic_issues: List[str] = field(default_factory=list)
    optimized_prompt: Optional[str] = None
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
    """质量门整体检查结果"""
    passed: bool
    reference_checks: List[ReferenceImageCheckResult] = field(default_factory=list)
    prompt_checks: List[PromptCheckResult] = field(default_factory=list)
    contract_checks: List[CheckResult] = field(default_factory=list)
    cost_estimate: Optional[CostEstimate] = None
    total_score: float = 0.0
    summary: List[str] = field(default_factory=list)
    # 新增字段
    optimization_suggestions: List[OptimizationSuggestion] = field(default_factory=list)
    # ── 新增：高级预检结果 ──
    coherence_result: Optional[CoherenceCheckResult] = None
    historical_result: Optional[HistoricalOptimizationResult] = None
    failure_prediction: Optional[FailurePredictionResult] = None
    optimized_prompts: List[str] = field(default_factory=list)
    recommended_parameters: Dict[str, Any] = field(default_factory=dict)


# ── 参考图预检 ──

def check_reference_image(
    image_path: Path,
    image_type: str = "product",
) -> ReferenceImageCheckResult:
    """
    检查参考图质量，在生成前验证参考图是否符合标准。

    Args:
        image_path: 参考图路径
        image_type: 图片类型（product/character/scene）

    Returns:
        参考图预检结果
    """
    config = QUALITY_GATE_CONFIG.get("reference_check", {})
    if not config.get("enabled", True):
        return ReferenceImageCheckResult(
            passed=True,
            path=image_path,
            image_type=image_type,
            messages=["参考图预检已跳过"],
        )

    result = ReferenceImageCheckResult(
        passed=True,
        path=image_path,
        image_type=image_type,
    )

    if not image_path.exists():
        result.passed = False
        result.messages.append(f"参考图不存在：{image_path}")
        result.suggestions.append("请提供有效的参考图路径")
        return result

    if image_path.stat().st_size < 1024:
        result.passed = False
        result.messages.append(f"参考图文件过小（{image_path.stat().st_size} 字节）")
        result.suggestions.append("请提供至少 1KB 的有效图片")
        return result

    try:
        with Image.open(image_path) as src:
            src.verify()
        with Image.open(image_path) as src:
            img = src.convert("RGBA")
    except Exception as e:
        result.passed = False
        result.messages.append(f"参考图格式损坏：{e}")
        result.suggestions.append("请使用有效的图片格式（JPG/PNG）")
        return result

    width, height = img.size
    result.width = width
    result.height = height

    min_w = config.get("min_width", 512)
    min_h = config.get("min_height", 512)
    max_w = config.get("max_width", 4096)
    max_h = config.get("max_height", 4096)

    if width < min_w or height < min_h:
        result.passed = False
        result.messages.append(
            f"参考图分辨率过低（{width}x{height}），要求 >= {min_w}x{min_h}"
        )
        result.suggestions.append(f"请提供分辨率至少 {min_w}x{min_h} 的图片")

    if width > max_w or height > max_h:
        result.passed = False
        result.messages.append(
            f"参考图分辨率过高（{width}x{height}），要求 <= {max_w}x{max_h}"
        )
        result.suggestions.append(f"请将图片缩放到 {max_w}x{max_h} 以内")

    alpha = img.getchannel("A")
    alpha_stat = ImageStat.Stat(alpha)
    opaque_ratio = sum(1 for p in alpha.getdata() if p > 16) / max(1, width * height)
    transparent_ratio = 1 - opaque_ratio
    result.transparent_ratio = transparent_ratio

    max_transparent = config.get("max_transparent_ratio", 0.95)
    if transparent_ratio > max_transparent:
        result.passed = False
        result.messages.append(
            f"参考图透明区域过多（{transparent_ratio:.2%}），要求 <= {max_transparent:.2%}"
        )
        result.suggestions.append("请提供主体清晰、背景不透明的参考图")

    rgb = img.convert("RGB")
    stat = ImageStat.Stat(rgb)
    channel_std = sum(stat.stddev) / max(1, len(stat.stddev))
    brightness = sum(stat.mean) / max(1, len(stat.mean))
    result.brightness = brightness
    result.contrast = channel_std

    min_brightness = config.get("min_brightness", 15)
    max_brightness = config.get("max_brightness", 240)
    if brightness < min_brightness:
        result.passed = False
        result.messages.append(
            f"参考图过暗（亮度 {brightness:.1f}），要求 >= {min_brightness}"
        )
        result.suggestions.append("请使用亮度正常的参考图")
    if brightness > max_brightness:
        result.passed = False
        result.messages.append(
            f"参考图过曝（亮度 {brightness:.1f}），要求 <= {max_brightness}"
        )
        result.suggestions.append("请使用曝光正常的参考图")

    min_contrast = config.get("min_contrast", 5)
    if channel_std < min_contrast:
        result.passed = False
        result.messages.append(
            f"参考图对比度过低（{channel_std:.1f}），要求 >= {min_contrast}"
        )
        result.suggestions.append("请提供色彩丰富、对比度正常的参考图")

    # ── 新增：主体检测（是否居中、尺寸比例）──
    if image_type in ("product", "character"):
        try:
            subject_ratio = _analyze_subject_size(img)
            result.subject_size_ratio = subject_ratio

            is_centered, offset = _analyze_subject_center(img)
            result.subject_centered = is_centered

            if not is_centered:
                result.messages.append(
                    f"主体偏移中心（偏移量：{offset:.1f}像素）"
                )
                result.suggestions.append("建议将主体居中放置")

            min_subject_ratio = 0.15 if image_type == "product" else 0.2
            if subject_ratio < min_subject_ratio:
                result.passed = False
                result.messages.append(
                    f"主体占比过小（{subject_ratio:.1%}），要求 >= {min_subject_ratio:.1%}"
                )
                result.suggestions.append("建议放大主体，使其占据画面主要区域")

            max_subject_ratio = 0.95
            if subject_ratio > max_subject_ratio:
                result.messages.append(
                    f"主体占比过大（{subject_ratio:.1%}）"
                )
                result.suggestions.append("建议适当留出背景空间")
        except Exception as e:
            result.messages.append(f"主体检测失败：{e}")

    # ── 新增：背景复杂度分析 ──
    try:
        bg_complexity = _analyze_background_complexity(rgb)
        result.background_complexity = bg_complexity

        if bg_complexity > 0.7:
            result.messages.append(
                f"背景过于复杂（复杂度：{bg_complexity:.2f}）"
            )
            result.suggestions.append("建议使用简洁背景，避免干扰AI识别主体")
        elif bg_complexity < 0.1:
            result.messages.append(
                f"背景过于单调（复杂度：{bg_complexity:.2f}）"
            )
            result.suggestions.append("建议添加适当背景元素")
    except Exception as e:
        result.messages.append(f"背景分析失败：{e}")

    # ── 新增：水印/文字检测 ──
    try:
        has_watermark = _detect_watermark_or_text(img)
        result.has_watermark = has_watermark

        if has_watermark:
            result.messages.append("检测到可能的水印或文字")
            result.suggestions.append("建议使用无水印的参考图")
    except Exception as e:
        result.messages.append(f"水印检测失败：{e}")

    # ── 新增：人脸检测（仅角色参考图）──
    if image_type == "character":
        try:
            face_result = _analyze_face_quality(rgb, width, height)
            result.is_frontal_face = face_result["is_frontal"]
            result.face_score = face_result["score"]

            if not face_result["is_frontal"]:
                result.passed = False
                result.messages.append("角色参考图非正面视角")
                result.suggestions.append("建议使用正面清晰肖像作为角色参考图")

            if face_result["score"] < 0.5:
                result.passed = False
                result.messages.append(f"人脸清晰度评分较低（{face_result['score']:.1f}）")
                result.suggestions.append("建议使用面部清晰的参考图")

            if not face_result["face_detected"]:
                result.passed = False
                result.messages.append("未检测到人脸")
                result.suggestions.append("请确保角色参考图包含清晰的人脸")
        except Exception as e:
            result.messages.append(f"人脸检测失败：{e}")

    if result.passed:
        result.messages.append(f"参考图质量合格（{width}x{height}）")

    return result


def _analyze_subject_size(img: Image.Image) -> float:
    """分析主体占比（通过边缘检测估算）"""
    grayscale = img.convert("L")
    edges = grayscale.filter(ImageFilter.FIND_EDGES)
    edge_pixels = sum(1 for p in edges.getdata() if p > 50)
    total_pixels = img.width * img.height
    return edge_pixels / max(1, total_pixels)


def _analyze_subject_center(img: Image.Image) -> Tuple[bool, float]:
    """分析主体是否居中，返回（是否居中，偏移量）"""
    grayscale = img.convert("L")
    edges = grayscale.filter(ImageFilter.FIND_EDGES)

    width, height = img.size
    center_x, center_y = width // 2, height // 2

    edge_coords = [
        (x, y)
        for y in range(height)
        for x in range(width)
        if edges.getpixel((x, y)) > 50
    ]

    if not edge_coords:
        return True, 0.0

    avg_x = sum(x for x, y in edge_coords) / len(edge_coords)
    avg_y = sum(y for x, y in edge_coords) / len(edge_coords)

    offset_x = abs(avg_x - center_x)
    offset_y = abs(avg_y - center_y)
    max_offset = min(width, height) * 0.2

    is_centered = offset_x < max_offset and offset_y < max_offset
    max_offset_pixel = max(offset_x, offset_y)

    return is_centered, max_offset_pixel


def _analyze_background_complexity(img: Image.Image) -> float:
    """分析背景复杂度（0-1，越高越复杂）"""
    gray = img.convert("L")
    stat = ImageStat.Stat(gray)

    hist = gray.histogram()
    entropy = 0.0
    total = sum(hist)
    for count in hist:
        if count > 0:
            prob = count / total
            entropy -= prob * math.log2(prob)

    normalized_entropy = entropy / 8
    return normalized_entropy


def _detect_watermark_or_text(img: Image.Image) -> bool:
    """检测水印或文字（通过高频纹理分析）"""
    gray = img.convert("L")
    width, height = img.size

    corners = [
        (0, 0),
        (width - 1, 0),
        (0, height - 1),
        (width - 1, height - 1),
    ]

    for corner in corners:
        x, y = corner
        region = gray.crop((max(0, x - 50), max(0, y - 50), min(width, x + 50), min(height, y + 50)))
        stat = ImageStat.Stat(region)
        if stat.stddev[0] > 30:
            return True

    return False


def _analyze_face_quality(img: Image.Image, width: int, height: int) -> Dict[str, Any]:
    """分析人脸质量（简化版，不依赖外部库）"""
    gray = img.convert("L")

    skin_regions = []
    for y in range(height):
        for x in range(width):
            r, g, b = img.getpixel((x, y))
            if 130 < r < 255 and 90 < g < 220 and 80 < b < 200:
                skin_regions.append((x, y))

    if not skin_regions:
        return {"face_detected": False, "is_frontal": False, "score": 0.0}

    face_detected = len(skin_regions) > width * height * 0.02
    if not face_detected:
        return {"face_detected": False, "is_frontal": False, "score": 0.0}

    avg_x = sum(x for x, y in skin_regions) / len(skin_regions)
    avg_y = sum(y for x, y in skin_regions) / len(skin_regions)

    is_frontal = abs(avg_x - width / 2) < width * 0.3

    skin_ratio = len(skin_regions) / (width * height)
    score = min(1.0, skin_ratio * 3)

    return {
        "face_detected": True,
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
    检查单个 Prompt 质量。

    Args:
        prompt: 待检查的 prompt
        prompt_type: prompt 类型（video/image）
        negative_prompt: 负面提示词

    Returns:
        Prompt 预检结果
    """
    config = QUALITY_GATE_CONFIG.get("prompt_check", {})
    if not config.get("enabled", True):
        return PromptCheckResult(
            passed=True,
            prompt=prompt[:100] + "..." if len(prompt) > 100 else prompt,
            messages=["Prompt 预检已跳过"],
        )

    result = PromptCheckResult(
        passed=True,
        prompt=prompt[:100] + "..." if len(prompt) > 100 else prompt,
        length=len(prompt),
    )

    min_len = config.get("min_length", 50)
    max_len = config.get("max_length", 2000)

    if len(prompt) < min_len:
        result.passed = False
        result.messages.append(f"Prompt 过短（{len(prompt)} 字符），要求 >= {min_len}")
        result.suggestions.append("请补充更详细的描述，包括场景、动作、质量要求等")

    if len(prompt) > max_len:
        result.passed = False
        result.messages.append(f"Prompt 过长（{len(prompt)} 字符），要求 <= {max_len}")
        result.suggestions.append("请精简 Prompt，去除冗余描述")

    required_keywords = config.get("required_keywords", {})
    prompt_lower = prompt.lower()

    for keyword_type, keywords in required_keywords.items():
        missing = [k for k in keywords if k not in prompt_lower]
        if missing:
            result.keyword_missing.extend(missing)
            result.messages.append(
                f"缺少{keyword_type}类型关键词：{', '.join(missing)}"
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
            result.messages.append(
                f"负面提示词缺少关键抑制词：{', '.join(missing_neg)}"
            )
            result.suggestions.append(
                f"建议在负面提示词中添加：{', '.join(missing_neg)}"
            )

    score, score_messages = score_prompt_quality(prompt)
    result.score = score
    result.messages.extend(score_messages)

    if score < 60:
        result.passed = False
        result.messages.append(f"Prompt 质量评分较低（{score:.1f}/100）")
        result.suggestions.append("请优化 Prompt 结构，增加细节描述")

    # ── 新增：语义冲突检测 ──
    conflicts = _detect_prompt_conflicts(prompt)
    if conflicts:
        result.conflicts = conflicts
        result.passed = False
        for conflict in conflicts:
            result.messages.append(f"检测到语义冲突：{conflict}")
            result.suggestions.append(f"请解决冲突：{conflict}")

    # ── 新增：重复描述检测 ──
    repetitions = _detect_repetitions(prompt)
    if repetitions:
        result.repetitions = repetitions
        for rep in repetitions:
            result.messages.append(f"检测到重复描述：{rep}")
            result.suggestions.append(f"建议合并或删除重复描述：{rep}")

    # ── 新增：语义一致性分析 ──
    semantic_issues = _analyze_semantic_consistency(prompt)
    if semantic_issues:
        result.semantic_issues = semantic_issues
        for issue in semantic_issues:
            result.messages.append(f"语义一致性问题：{issue}")
            result.suggestions.append(f"建议修正：{issue}")

    # ── 新增：自动优化建议 ──
    optimized = _optimize_prompt(prompt, result)
    if optimized and optimized != prompt:
        result.optimized_prompt = optimized
        result.messages.append("已生成优化版本的 Prompt")
        result.suggestions.append("建议使用优化后的 Prompt")

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
        for conflict in result.conflicts:
            for positives, negatives in CONFLICT_KEYWORDS:
                all_words = positives + negatives
                conflict_words = [w for w in all_words if w in conflict.lower()]
                if not conflict_words:
                    continue
                # 策略：保留更具体/更重要的一侧，移除另一侧
                # 对于运镜冲突（static vs dynamic），保留 camera_movement 对应的那一侧
                # 简单策略：移除第二个出现的词组（通常是后面注入的风格描述）
                words_lower = optimized.lower()
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
                    # 确定哪一侧整体更靠后
                    max_pos = max(pos_positions.values()) if pos_positions else -1
                    max_neg = max(neg_positions.values()) if neg_positions else -1
                    # 移除更靠后的那一侧的词
                    words_to_remove = neg_positions.keys() if max_neg > max_pos else pos_positions.keys()
                    for w in words_to_remove:
                        # 移除该词及其前后的逗号和空格
                        import re
                        optimized = re.sub(rf',?\s*{re.escape(w)},?\s*', ', ', optimized, flags=re.IGNORECASE)
                # 清理多余的逗号
                optimized = optimized.replace(' ,', ',').replace(',,', ',').strip(', ').strip()

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
        result.passed = False
        result.issues.append("脚本片段数量不足（需要至少5个叙事段）")
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

    success_cases = load_history_database(success_db_path)
    failure_cases = load_history_database(failure_db_path)

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

    logger.info("🚪 启动质量前置控制检查（增强版）...")

    result = QualityGateResult(passed=True)

    # ── 参考图预检 ──
    if config.get("reference_check", {}).get("enabled", True):
        logger.info("📷 检查参考图质量...")
        ref_results = check_all_reference_images(
            product_image_path,
            character_image_paths,
        )
        result.reference_checks = ref_results
        for r in ref_results:
            if not r.passed:
                result.passed = False

    # ── Prompt 预校验（返回优化后的 Prompt）──
    if config.get("prompt_check", {}).get("enabled", True) and prompts:
        logger.info("✍️ 检查 Prompt 质量（自动优化）...")
        prompt_results, optimized_prompts = check_all_prompts(
            prompts,
            auto_apply=auto_apply_optimization,
        )
        result.prompt_checks = prompt_results
        result.optimized_prompts = optimized_prompts
        for r in prompt_results:
            if not r.passed:
                result.passed = False

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
        for r in contract_results:
            if not r.passed:
                result.passed = False

    # ── 新增：片段连贯性预检 ──
    if config.get("coherence_check", {}).get("enabled", True):
        logger.info("🔗 检查片段连贯性...")
        coherence_result = check_segment_coherence(ad_script, prompts or [])
        result.coherence_result = coherence_result
        if not coherence_result.passed:
            result.passed = False

    # ── 新增：历史数据驱动优化 ──
    if config.get("history_driven_optimization", {}).get("enabled", True):
        logger.info("📊 基于历史数据优化参数...")
        historical_result = optimize_by_history(
            product_category,
            style_preference,
            num_clips,
        )
        result.historical_result = historical_result

        # 应用历史推荐的参数
        if historical_result.recommended_seed:
            result.recommended_parameters["seed"] = historical_result.recommended_seed
        if historical_result.recommended_fidelity:
            result.recommended_parameters["fidelity"] = historical_result.recommended_fidelity
        if historical_result.recommended_mode:
            result.recommended_parameters["mode"] = historical_result.recommended_mode

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
            result.passed = False

    # ── 新增：失败预测模型 ──
    if config.get("failure_prediction", {}).get("enabled", True):
        logger.info("🎯 预测生成成功率...")
        failure_prediction = predict_failure(
            result,
            result.coherence_result or CoherenceCheckResult(passed=True),
            result.historical_result or HistoricalOptimizationResult(),
        )
        result.failure_prediction = failure_prediction

        if failure_prediction.block_generation:
            result.passed = False

    # ── 计算综合评分 ──
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
        if result.failure_prediction and result.failure_prediction.predicted_success_rate >= 0.80:
            result.summary.append("✅ 所有前置检查通过，预测成功率 ≥80%，强烈推荐生成")
        else:
            result.summary.append("✅ 所有前置检查通过，可以开始生成")
    else:
        if result.failure_prediction and result.failure_prediction.block_generation:
            result.summary.append("❌ 预测成功率过低，已阻止生成")
        else:
            result.summary.append("❌ 前置检查失败，建议修复后再生成")

    return result


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