#!/usr/bin/env python3
"""
智能决策引擎（Smart Decision Engine）

核心理念：将固定的"串行检查清单"升级为"动态决策树"，
实现基于成本-收益的智能决策，最大化一次性成功率。

设计原则：
1. 动态决策：根据前期检查结果决定后续路径
2. 修复优先级：基于修复成本与收益评估最优修复路径
3. 渐进式生成：关键片段先行验证，失败提前止损
4. 自适应检查：根据输入质量动态调整检查深度
"""

import json
import math
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable, Tuple
from dataclasses import dataclass, field
from enum import Enum

from config import (
    QUALITY_GATE_CONFIG,
    setup_logger,
)
from quality_gate import (
    QualityGateResult,
    ReferenceImageCheckResult,
    PromptCheckResult,
    CoherenceCheckResult,
    FailurePredictionResult,
    OptimizationSuggestion,
    check_reference_image,
    check_prompt,
    check_segment_coherence,
    estimate_cost,
    optimize_by_history,
    predict_failure,
)

logger = setup_logger(__name__)


class DecisionOutcome(Enum):
    """决策结果"""
    PROCEED = "proceed"           # 继续下一步
    FIX_AND_RETRY = "fix"         # 修复后重试
    SKIP = "skip"                 # 跳过当前步骤
    BLOCK = "block"               # 阻止生成
    PARTIAL_GENERATE = "partial"  # 部分生成（渐进式）
    FAST_TRACK = "fast"           # 快速通道


class CheckDepth(Enum):
    """检查深度"""
    MINIMAL = "minimal"      # 最小检查（快速通道）
    STANDARD = "standard"    # 标准检查
    DEEP = "deep"            # 深度检查（高风险输入）


@dataclass
class DecisionNode:
    """决策节点"""
    name: str
    check_function: Callable
    fix_function: Optional[Callable] = None
    fix_cost_seconds: float = 0.0          # 修复成本（秒）
    success_rate_boost: float = 0.0        # 修复后预期成功率提升
    required_for: List[str] = field(default_factory=list)  # 哪些片段类型依赖此检查
    next_on_pass: Optional[str] = None     # 通过后的下一节点
    next_on_fail: Optional[str] = None     # 失败后的下一节点
    can_skip: bool = False                 # 是否可以跳过


@dataclass
class RepairPath:
    """修复路径"""
    issue: str
    fix_description: str
    fix_cost: float                        # 修复成本（秒）
    success_rate_improvement: float        # 预期成功率提升
    roi: float = 0.0                       # 投资回报率 = improvement / cost
    auto_fixable: bool = False             # 是否可自动修复


@dataclass
class SmartDecisionResult:
    """智能决策结果"""
    can_proceed: bool
    decision_path: List[str] = field(default_factory=list)
    repair_paths: List[RepairPath] = field(default_factory=list)
    recommended_strategy: str = "standard"
    estimated_success_rate: float = 0.0
    estimated_cost: float = 0.0
    fast_track_eligible: bool = False
    partial_generation_plan: Optional[Dict[str, Any]] = None
    messages: List[str] = field(default_factory=list)


# ── 智能决策树构建 ──

def build_smart_decision_tree(
    product_category: str,
    num_clips: int,
    budget: float,
) -> Dict[str, DecisionNode]:
    """
    根据产品特性和预算构建动态决策树。

    不同产品品类的关键检查点不同：
    - 美妆：角色一致性 > 产品展示 > 色彩还原
    - 食品：产品质感 > 食欲感 > 新鲜度
    - 数码：功能展示 > 细节清晰度 > 科技感
    """

    # 品类特定的检查权重
    category_priorities = {
        "美妆": ["character_reference", "product_reference", "prompt_quality", "color_consistency"],
        "食品": ["product_reference", "lighting_quality", "texture_detail", "prompt_quality"],
        "数码": ["product_reference", "detail_clarity", "feature_demo", "prompt_quality"],
        "家居": ["scene_reference", "product_reference", "scale_accuracy", "prompt_quality"],
        "服装": ["character_reference", "fabric_texture", "fit_display", "prompt_quality"],
    }

    priorities = category_priorities.get(product_category, ["reference_image", "prompt_quality"])

    tree = {}

    # 根节点：快速筛查
    tree["root"] = DecisionNode(
        name="快速筛查",
        check_function=_quick_screening,
        fix_cost_seconds=0,
        success_rate_boost=0,
        next_on_pass="category_specific" if num_clips > 3 else "minimal_check",
        next_on_fail="deep_check",
        can_skip=False,
    )

    # 品类特定检查节点
    for i, priority in enumerate(priorities[:3]):
        node_name = f"check_{priority}"
        next_node = f"check_{priorities[i+1]}" if i < len(priorities) - 1 else "coherence_check"

        tree[node_name] = DecisionNode(
            name=f"{priority}检查",
            check_function=_get_check_function(priority),
            fix_function=_get_fix_function(priority),
            fix_cost_seconds=_get_fix_cost(priority),
            success_rate_boost=_get_success_boost(priority),
            next_on_pass=next_node,
            next_on_fail="repair_decision",
            can_skip=False,
        )

    # 连贯性检查节点
    tree["coherence_check"] = DecisionNode(
        name="片段连贯性检查",
        check_function=check_segment_coherence,
        fix_cost_seconds=300,  # 重写脚本约5分钟
        success_rate_boost=0.15,
        next_on_pass="cost_check",
        next_on_fail="repair_decision",
        can_skip=num_clips <= 2,  # 少于2个片段可跳过连贯性检查
    )

    # 成本检查节点
    tree["cost_check"] = DecisionNode(
        name="成本预算检查",
        check_function=_cost_check,
        fix_cost_seconds=0,
        success_rate_boost=0,
        next_on_pass="history_optimization",
        next_on_fail="downgrade_decision",
        can_skip=False,
    )

    # 历史数据优化节点
    tree["history_optimization"] = DecisionNode(
        name="历史数据优化",
        check_function=_history_optimize,
        fix_cost_seconds=0,
        success_rate_boost=0.10,
        next_on_pass="failure_prediction",
        next_on_fail="failure_prediction",
        can_skip=True,
    )

    # 失败预测节点
    tree["failure_prediction"] = DecisionNode(
        name="失败预测",
        check_function=_predict_failure,
        fix_cost_seconds=0,
        success_rate_boost=0,
        next_on_pass="final_decision",
        next_on_fail="repair_decision",
        can_skip=False,
    )

    # 修复决策节点
    tree["repair_decision"] = DecisionNode(
        name="修复决策",
        check_function=_repair_decision,
        fix_cost_seconds=0,
        success_rate_boost=0,
        next_on_pass="root",  # 修复后重新检查
        next_on_fail="block",
        can_skip=False,
    )

    # 降级决策节点
    tree["downgrade_decision"] = DecisionNode(
        name="降级决策",
        check_function=_downgrade_decision,
        fix_cost_seconds=0,
        success_rate_boost=-0.05,  # 降级可能降低质量
        next_on_pass="failure_prediction",
        next_on_fail="block",
        can_skip=False,
    )

    # 最终决策节点
    tree["final_decision"] = DecisionNode(
        name="最终决策",
        check_function=_final_decision,
        fix_cost_seconds=0,
        success_rate_boost=0,
        next_on_pass=None,
        next_on_fail=None,
        can_skip=False,
    )

    return tree


# ── 检查函数映射 ──

def _get_check_function(priority: str) -> Callable:
    """根据优先级获取检查函数"""
    check_map = {
        "character_reference": lambda: check_reference_image,
        "product_reference": lambda: check_reference_image,
        "prompt_quality": lambda: check_prompt,
    }
    return check_map.get(priority, lambda: lambda x: True)


def _get_fix_function(priority: str) -> Optional[Callable]:
    """根据优先级获取修复函数"""
    fix_map = {
        "prompt_quality": lambda prompt: _auto_optimize_prompt(prompt),
    }
    return fix_map.get(priority)


def _get_fix_cost(priority: str) -> float:
    """获取修复成本（秒）"""
    cost_map = {
        "character_reference": 60,    # 重新上传参考图
        "product_reference": 60,
        "prompt_quality": 1,          # 自动修复几乎无成本
        "color_consistency": 0,
        "lighting_quality": 0,
    }
    return cost_map.get(priority, 30)


def _get_success_boost(priority: str) -> float:
    """获取修复后的成功率提升"""
    boost_map = {
        "character_reference": 0.30,
        "product_reference": 0.25,
        "prompt_quality": 0.15,
        "color_consistency": 0.10,
        "lighting_quality": 0.10,
    }
    return boost_map.get(priority, 0.10)


# ── 决策节点实现 ──

def _quick_screening(
    *,
    reference_checks: List[ReferenceImageCheckResult],
    prompts: List[str],
) -> Tuple[bool, Dict[str, Any]]:
    """
    快速筛查：30秒内判断输入质量等级。

    Returns:
        (是否通过, {quality_level: str, estimated_base_success_rate: float})
    """
    # 参考图快速评分
    ref_scores = []
    for ref in reference_checks:
        score = 100.0
        if not ref.passed:
            score -= 30
        if ref.subject_size_ratio < 0.15:
            score -= 20
        if ref.background_complexity > 0.7:
            score -= 10
        ref_scores.append(score)

    avg_ref_score = sum(ref_scores) / max(1, len(ref_scores))

    # Prompt快速评分
    prompt_scores = []
    for prompt in prompts:
        score = 100.0
        if len(prompt) < 50:
            score -= 30
        if len(prompt) > 1500:
            score -= 15
        prompt_scores.append(score)

    avg_prompt_score = sum(prompt_scores) / max(1, len(prompt_scores))

    # 综合评级
    combined_score = (avg_ref_score + avg_prompt_score) / 2

    if combined_score >= 85:
        quality_level = "high"
        base_success_rate = 0.85
    elif combined_score >= 60:
        quality_level = "medium"
        base_success_rate = 0.65
    else:
        quality_level = "low"
        base_success_rate = 0.40

    return combined_score >= 60, {
        "quality_level": quality_level,
        "base_success_rate": base_success_rate,
        "ref_score": avg_ref_score,
        "prompt_score": avg_prompt_score,
    }


def _cost_check(
    *,
    cost_estimate: Any,
) -> Tuple[bool, Dict[str, Any]]:
    """成本检查"""
    if not cost_estimate:
        return True, {"within_budget": True}

    return cost_estimate.within_budget, {
        "within_budget": cost_estimate.within_budget,
        "total_cost": cost_estimate.total_cost,
        "budget_limit": cost_estimate.budget_limit,
    }


def _history_optimize(
    *,
    product_category: str,
    style_preference: str,
    num_clips: int,
) -> Tuple[bool, Dict[str, Any]]:
    """历史数据优化"""
    result = optimize_by_history(product_category, style_preference, num_clips)
    return True, {
        "recommended_seed": result.recommended_seed,
        "recommended_fidelity": result.recommended_fidelity,
        "recommended_mode": result.recommended_mode,
        "success_rate": result.success_rate_prediction,
        "confidence": result.confidence_level,
    }


def _predict_failure(
    *,
    quality_result: QualityGateResult,
    coherence_result: CoherenceCheckResult,
    historical_result: Any,
) -> Tuple[bool, Dict[str, Any]]:
    """失败预测"""
    prediction = predict_failure(
        quality_result,
        coherence_result,
        historical_result,
    )

    return prediction.risk_level != "high", {
        "predicted_success_rate": prediction.predicted_success_rate,
        "risk_level": prediction.risk_level,
        "risk_factors": prediction.risk_factors,
        "mitigation_suggestions": prediction.mitigation_suggestions,
        "block_generation": prediction.block_generation,
    }


def _repair_decision(
    *,
    failed_checks: List[Dict[str, Any]],
) -> Tuple[bool, Dict[str, Any]]:
    """
    修复决策：基于ROI选择最优修复路径。

    核心算法：ROI = 成功率提升 / 修复成本
    """
    repair_paths = []

    for check in failed_checks:
        issue = check.get("issue", "")
        fix_cost = check.get("fix_cost", 30)
        success_boost = check.get("success_boost", 0.10)
        auto_fixable = check.get("auto_fixable", False)

        roi = success_boost / max(1, fix_cost) * 100  # 每秒带来的成功率提升

        repair_paths.append(RepairPath(
            issue=issue,
            fix_description=check.get("fix_description", ""),
            fix_cost=fix_cost,
            success_rate_improvement=success_boost,
            roi=roi,
            auto_fixable=auto_fixable,
        ))

    # 按ROI排序
    repair_paths.sort(key=lambda x: x.roi, reverse=True)

    # 优先执行自动修复
    auto_fixes = [p for p in repair_paths if p.auto_fixable]
    manual_fixes = [p for p in repair_paths if not p.auto_fixable]

    return len(auto_fixes) > 0, {
        "auto_fix_paths": auto_fixes,
        "manual_fix_paths": manual_fixes,
        "total_estimated_improvement": sum(p.success_rate_improvement for p in auto_fixes),
    }


def _downgrade_decision(
    *,
    cost_estimate: Any,
) -> Tuple[bool, Dict[str, Any]]:
    """降级决策"""
    if not cost_estimate or not cost_estimate.downgrade_options:
        return False, {"available": False}

    best_option = cost_estimate.downgrade_options[0]
    return True, {
        "recommended_option": best_option,
        "savings": best_option.get("savings", 0),
        "new_cost": best_option.get("new_cost", 0),
    }


def _final_decision(
    *,
    predictions: Dict[str, Any],
) -> Tuple[bool, Dict[str, Any]]:
    """最终决策"""
    success_rate = predictions.get("predicted_success_rate", 0.5)

    if success_rate >= 0.80:
        strategy = "full_generate"
    elif success_rate >= 0.60:
        strategy = "partial_generate"  # 渐进式生成
    elif success_rate >= 0.40:
        strategy = "experimental"  # 实验性生成，高风险
    else:
        strategy = "block"

    return strategy != "block", {
        "strategy": strategy,
        "estimated_success_rate": success_rate,
    }


def _auto_optimize_prompt(prompt: str) -> str:
    """自动优化Prompt"""
    # 简化版：添加一致性关键词
    if "same" not in prompt.lower():
        prompt += ", same person from reference"
    return prompt


# ── 渐进式生成计划 ──

def build_partial_generation_plan(
    num_clips: int,
    strategy: str,
    budget: float,
) -> Dict[str, Any]:
    """
    构建渐进式生成计划。

    核心策略：
    1. 先生成最关键片段（hook + showcase + CTA）
    2. 验证质量后再生成其余片段
    3. 关键片段失败则提前止损
    """
    if strategy == "full_generate":
        return {
            "strategy": "full",
            "phases": [{"clips": list(range(num_clips)), "description": "全量生成"}],
            "estimated_cost": budget,
            "checkpoint_after": None,
        }

    if strategy == "partial_generate":
        # 分阶段生成
        phases = []

        # 第一阶段：关键片段（hook, showcase, CTA）
        key_clips = [0, 2, 4] if num_clips >= 5 else [0, num_clips - 1]
        phases.append({
            "clips": key_clips,
            "description": "关键片段验证",
            "estimated_cost": budget * 0.6,
        })

        # 第二阶段：过渡片段
        remaining = [i for i in range(num_clips) if i not in key_clips]
        if remaining:
            phases.append({
                "clips": remaining,
                "description": "过渡片段生成",
                "estimated_cost": budget * 0.4,
                "depends_on": 0,  # 依赖第一阶段
            })

        return {
            "strategy": "partial",
            "phases": phases,
            "estimated_cost": budget,
            "checkpoint_after": 0,  # 第一阶段后检查
        }

    if strategy == "experimental":
        # 实验性：只生成一个最关键片段
        return {
            "strategy": "experimental",
            "phases": [{
                "clips": [2],  # 只生成showcase段
                "description": "实验性生成（showcase段）",
                "estimated_cost": budget * 0.2,
            }],
            "estimated_cost": budget * 0.2,
            "checkpoint_after": 0,
        }

    return {
        "strategy": "block",
        "phases": [],
        "estimated_cost": 0,
    }


# ── 主智能决策流程 ──

def run_smart_decision(
    *,
    quality_gate_result: QualityGateResult,
    product_category: str = "美妆",
    style_preference: str = "cinematic",
    budget: float = 100.0,
) -> SmartDecisionResult:
    """
    运行智能决策流程。

    替代固定的线性检查，使用动态决策树根据输入质量和检查结果
    智能选择最优路径。
    """
    result = SmartDecisionResult(can_proceed=False)

    # Step 1: 快速筛查
    logger.info("🔍 执行快速筛查...")
    ref_checks = quality_gate_result.reference_checks
    prompts = [p.prompt for p in quality_gate_result.prompt_checks]

    quick_passed, quick_info = _quick_screening(
        reference_checks=ref_checks,
        prompts=prompts,
    )

    quality_level = quick_info["quality_level"]
    base_success_rate = quick_info["base_success_rate"]

    result.messages.append(f"快速筛查结果：质量等级 {quality_level.upper()}")
    result.messages.append(f"预估基础成功率：{base_success_rate:.1%}")

    # 根据质量等级选择检查深度
    if quality_level == "high":
        check_depth = CheckDepth.MINIMAL
        result.fast_track_eligible = True
        result.messages.append("✅ 输入质量高，启用快速通道")
    elif quality_level == "medium":
        check_depth = CheckDepth.STANDARD
    else:
        check_depth = CheckDepth.DEEP
        result.messages.append("⚠️ 输入质量低，启用深度检查")

    # Step 2: 收集失败项并计算修复路径
    failed_checks = []

    for ref in ref_checks:
        if not ref.passed:
            failed_checks.append({
                "issue": f"参考图质量问题：{ref.path.name}",
                "fix_description": "重新上传高质量参考图",
                "fix_cost": 60,
                "success_boost": 0.30,
                "auto_fixable": False,
            })

    for prompt_check in quality_gate_result.prompt_checks:
        if not prompt_check.passed:
            # 区分可自动修复和需手动修复
            if prompt_check.conflicts or prompt_check.repetitions:
                failed_checks.append({
                    "issue": f"Prompt质量问题：{prompt_check.name}",
                    "fix_description": "自动修复语义冲突和重复",
                    "fix_cost": 1,
                    "success_boost": 0.15,
                    "auto_fixable": True,
                })
            else:
                failed_checks.append({
                    "issue": f"Prompt质量问题：{prompt_check.name}",
                    "fix_description": "手动优化Prompt描述",
                    "fix_cost": 120,
                    "success_boost": 0.15,
                    "auto_fixable": False,
                })

    if quality_gate_result.coherence_result and not quality_gate_result.coherence_result.passed:
        failed_checks.append({
            "issue": "片段连贯性不足",
            "fix_description": "调整脚本叙事结构",
            "fix_cost": 300,
            "success_boost": 0.15,
            "auto_fixable": False,
        })

    # Step 3: 计算修复ROI并排序
    if failed_checks:
        logger.info(f"🔧 发现 {len(failed_checks)} 个问题，计算最优修复路径...")

        _, repair_info = _repair_decision(failed_checks=failed_checks)

        auto_fixes = repair_info.get("auto_fix_paths", [])
        manual_fixes = repair_info.get("manual_fix_paths", [])

        result.repair_paths = auto_fixes + manual_fixes

        # 自动修复路径直接标记为可应用
        for path in auto_fixes:
            result.messages.append(
                f"🔧 自动修复：{path.issue}（预期提升{path.success_rate_improvement:.1%}）"
            )

        for path in manual_fixes:
            result.messages.append(
                f"⚠️ 需手动修复：{path.issue}（成本{path.fix_cost}秒，预期提升{path.success_rate_improvement:.1%}）"
            )

        # 如果存在自动修复，应用后重新评估
        if auto_fixes:
            base_success_rate += sum(p.success_rate_improvement for p in auto_fixes)
            result.messages.append(f"应用自动修复后预估成功率：{min(base_success_rate, 0.95):.1%}")

    # Step 4: 失败预测
    if quality_gate_result.failure_prediction:
        prediction = quality_gate_result.failure_prediction
        predicted_rate = prediction.predicted_success_rate

        # 结合快速筛查和详细预测
        final_rate = (base_success_rate + predicted_rate) / 2
        result.estimated_success_rate = final_rate

        result.messages.append(f"🎯 综合预测成功率：{final_rate:.1%}")

        if prediction.risk_factors:
            result.messages.append(f"⚠️ 风险因素：{', '.join(prediction.risk_factors)}")
    else:
        result.estimated_success_rate = base_success_rate

    # Step 5: 决策策略选择
    # 核心原则：宁缺毋滥，不达标就不生成，避免浪费额度
    # 只有同时满足：1) 成功率够高 2) 没有未解决的严重问题，才放行
    auto_fix_count = len([p for p in result.repair_paths if p.auto_fixable])
    manual_fix_count = len([p for p in result.repair_paths if not p.auto_fixable])

    # 质量门整体未通过时，即使成功率高也不能直接放行
    # （除非所有失败项都是可自动修复的，且修复后成功率达标）
    quality_gate_passed = getattr(quality_gate_result, "passed", True)
    if not quality_gate_passed and manual_fix_count > 0:
        result.can_proceed = False
        result.recommended_strategy = "block_quality_gate"
        result.messages.append(
            f"❌ 质量门未通过且存在{manual_fix_count}个需手动修复的问题，阻止生成"
        )
        result.messages.append("💡 建议：先修复参考图质量等手动可修复的问题，再重试")
    elif result.estimated_success_rate >= 0.75 and manual_fix_count == 0:
        result.can_proceed = True
        result.recommended_strategy = "full_generate"
        result.messages.append("✅ 推荐策略：全量生成（成功率≥75%，无手动修复项）")

    elif result.estimated_success_rate >= 0.60 and manual_fix_count <= 1:
        result.can_proceed = True
        result.recommended_strategy = "partial_generate"
        result.messages.append(f"💡 推荐策略：渐进式生成（成功率60-75%，{manual_fix_count}个手动修复项）")

        result.partial_generation_plan = build_partial_generation_plan(
            num_clips=5,
            strategy="partial_generate",
            budget=budget,
        )

    else:
        result.can_proceed = False
        if result.estimated_success_rate < 0.60:
            result.recommended_strategy = "block"
            result.messages.append(
                f"❌ 推荐策略：阻止生成（成功率{result.estimated_success_rate:.0%} < 60%，大概率失败浪费额度）"
            )
        else:
            result.recommended_strategy = "block_needs_fix"
            result.messages.append(
                f"❌ 推荐策略：阻止生成（成功率达标，但有{manual_fix_count}个需手动修复的问题）"
            )
        result.messages.append("💡 建议：先修复上述问题后再生成，成功率会大幅提升")

    # Step 6: 成本估算
    if quality_gate_result.cost_estimate:
        result.estimated_cost = quality_gate_result.cost_estimate.total_cost

    result.decision_path = [
        "快速筛查",
        f"检查深度：{check_depth.value}",
        f"修复路径：{len(result.repair_paths)}条",
        f"预测成功率：{result.estimated_success_rate:.1%}",
        f"决策策略：{result.recommended_strategy}",
    ]

    return result


def print_smart_decision_report(result: SmartDecisionResult) -> None:
    """打印智能决策报告"""
    print("\n" + "=" * 70)
    print("🧠 智能决策引擎报告")
    print("=" * 70)

    if result.can_proceed:
        print("\n✅ 决策结果：可以生成")
    else:
        print("\n❌ 决策结果：阻止生成")

    print(f"\n📊 预估成功率：{result.estimated_success_rate:.1%}")
    print(f"💰 预估费用：¥{result.estimated_cost:.2f}")
    print(f"🚀 推荐策略：{result.recommended_strategy}")

    if result.fast_track_eligible:
        print("⚡ 快速通道：可用")

    if result.decision_path:
        print("\n🛤️ 决策路径：")
        for step in result.decision_path:
            print(f"   → {step}")

    if result.repair_paths:
        print("\n🔧 修复路径（按ROI排序）：")
        for i, path in enumerate(result.repair_paths[:5], 1):
            auto_tag = "[自动]" if path.auto_fixable else "[手动]"
            print(f"   {i}. {auto_tag} {path.issue}")
            print(f"      修复：{path.fix_description}")
            print(f"      成本：{path.fix_cost}秒 | 提升：{path.success_rate_improvement:.1%} | ROI：{path.roi:.2f}")

    if result.partial_generation_plan:
        plan = result.partial_generation_plan
        print(f"\n📋 渐进式生成计划（{plan['strategy']}）：")
        for i, phase in enumerate(plan["phases"]):
            clips_str = ", ".join([f"片段{c+1}" for c in phase["clips"]])
            print(f"   阶段{i+1}：{phase['description']}")
            print(f"      生成：{clips_str}")
            print(f"      预估费用：¥{phase.get('estimated_cost', 0):.2f}")
            if "depends_on" in phase:
                print(f"      依赖：阶段{phase['depends_on']+1}通过")

    if result.messages:
        print("\n💬 决策详情：")
        for msg in result.messages:
            print(f"   • {msg}")

    print("\n" + "=" * 70 + "\n")


# ── 便捷函数 ──

def run_full_smart_pipeline(
    *,
    ad_script: Dict[str, Any],
    product_image_path: Optional[Path] = None,
    character_image_paths: Optional[List[Path]] = None,
    prompts: Optional[List[str]] = None,
    character_bible: Optional[Dict[str, Any]] = None,
    product_bible: Optional[Dict[str, Any]] = None,
    product_category: str = "美妆",
    style_preference: str = "cinematic",
    budget: float = 100.0,
    mode: str = "pro",
) -> Tuple[SmartDecisionResult, QualityGateResult]:
    """
    运行完整的智能决策流水线。

    1. 先运行质量门检查
    2. 再运行智能决策
    3. 返回决策结果和质量门结果
    """
    from quality_gate import run_quality_gate

    # 运行质量门检查
    quality_result = run_quality_gate(
        ad_script=ad_script,
        product_image_path=product_image_path,
        character_image_paths=character_image_paths,
        prompts=prompts,
        character_bible=character_bible,
        product_bible=product_bible,
        product_category=product_category,
        style_preference=style_preference,
        mode=mode,
    )

    # 运行智能决策
    decision_result = run_smart_decision(
        quality_gate_result=quality_result,
        product_category=product_category,
        style_preference=style_preference,
        budget=budget,
    )

    return decision_result, quality_result
