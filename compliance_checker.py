#!/usr/bin/env python3
"""
广告合规检测模块

功能：
- 广告极限词检测（《广告法》禁用词）
- 敏感内容检测
- 自动替换建议
- 风险等级评估
- 检测字幕、口播、标题、话题标签
"""

import re
from typing import Dict, List, Tuple, Optional, Any
from dataclasses import dataclass, field


# ============================================================
# 极限词库（基于《广告法》及抖音平台规则）
# ============================================================

# 最高级禁用词（绝对不能用）
# #18 修复：移除"最"/"完全"/"彻底"/"全部"等单字/常用词，避免误判正常词组
BANNED_WORDS_SUPREME = [
    # 最高级词组（必须是完整短语，不匹配单字）
    "最佳", "最好", "最大", "最小", "最多", "最少", "最先进",
    "最高级", "最便宜", "最划算", "最美味", "最有效", "最安全",
    "第一名", "唯一", "首个", "首选", "独家专属", "绝无仅有", "史无前例",
    "顶级", "顶尖", "尖端技术", "极品", "极致体验", "终极", "完美无缺",
    "100%有效", "百分之百", "永不复发", "永久有效",
    # 国家级
    "国家级", "世界级", "全球级", "宇宙级",
    "国家免检", "国家领导人", "国宴",
    # 第一/唯一
    "全网第一", "销量第一", "口碑第一", "效果第一",
    "独一无二", "仅此一家",
    # 虚假承诺
    "包治百病", "药到病除", "立竿见影", "瞬间见效",
    "无效退款", "假一赔十", "假一赔万",
    # 权威暗示
    "特供", "专供", "领导品牌", "驰名商标",
    "中国驰名商标", "著名商标", "名牌产品",
]

# 高风险词（谨慎使用，最好替换）
# #18 修复：移除"超级"/"超"/"巨"等单字，保留具体短语
BANNED_WORDS_HIGH_RISK = [
    # 效果保证
    "根治", "治愈", "痊愈", "康复",
    "美白效果", "祛斑", "脱发再生", "增高",
    "减肥", "瘦身", "燃脂", "塑形",
    # 投资回报
    "赚钱", "暴富", "躺赚", "月入过万",
    "零风险", "稳赚不赔", "高回报",
    # 夸张表述
    "神奇", "神效", "神器", "黑科技",
    "逆天", "炸裂", "秒杀", "碾压",
    "绝了", "无敌", "爆表",
    # 紧迫营销
    "最后一天", "最后机会", "限时秒杀",
    "仅剩", "手慢无", "抢完即止",
]

# 中风险词（建议核实）
# #18 修复：移除"超"/"巨"/"非常"/"推荐"/"更好"/"更快"/"更强"等单字/常用词，改为词组
BANNED_WORDS_MEDIUM_RISK = [
    # 数据相关
    "99%", "98%", "95%", "90%",
    "万人好评", "百万用户", "千万销量", "亿万消费者",
    "销量领先", "市场领先", "行业领先",
    # 主观感受（词组，不匹配单字）
    "超级好用", "超级划算", "超级推荐",
    "爆款", "热销", "畅销",
    "必入", "必买", "种草推荐",
    # 对比词（词组）
    "优于竞品", "胜过同类", "远超",
    "碾压同款",
]

# 敏感词（政治/色情/暴力/医疗等）
SENSITIVE_WORDS = [
    # 政治敏感
    "政府", "国家领导人", "中南海", "军队",
    # 医疗术语（普通商品不能用医疗词汇）
    "治疗", "诊断", "处方", "医药", "疗效",
    "医生", "医院", "诊所", "中药", "西药",
    # 金融投资
    "股票", "基金", "理财", "投资回报",
    # 低俗色情
    "色情", "低俗", "性感", "诱惑",
    # 暴力血腥
    "暴力", "血腥", "恐怖",
]


# 合规替换建议词库
REPLACEMENT_SUGGESTIONS = {
    "最佳": "优秀",
    "最好": "很好",
    "最大": "很大",
    "第一": "领先",
    "唯一": "独特",
    "顶级": "高品质",
    "完美": "出色",
    "100%": "高比例",
    "绝对": "相当",
    "彻底": "深度",
    "全部": "大部分",
    "神奇": "出色",
    "神器": "好产品",
    "黑科技": "创新技术",
    "秒杀": "快速见效",
    "绝了": "很棒",
    "无敌": "出众",
    "爆款": "受欢迎",
    "热销": "销售不错",
    "必入": "值得考虑",
    "必买": "推荐入手",
    "99%": "高比例",
    "立竿见影": "快速感受",
    "瞬间见效": "很快有感觉",
    "根治": "改善",
    "治愈": "缓解",
    "赚钱": "增加收入",
    "暴富": "改善生活",
    "躺赚": "轻松增收",
    "零风险": "低风险",
    "稳赚不赔": "收益稳定",
    "最后一天": "活动进行中",
    "手慢无": "库存有限",
    "全网第一": "口碑很好",
    "销量第一": "销量领先",
    "独家": "特色",
    "史无前例": "前所未有",
    "终极": "进阶",
    "极品": "优质",
}


@dataclass
class ComplianceIssue:
    """合规问题"""
    word: str
    level: str  # "supreme" / "high" / "medium" / "sensitive"
    category: str  # "extreme" / "sensitive" / "medical" / "financial"
    suggestion: str = ""
    context: str = ""  # 出现的上下文


@dataclass
class ComplianceResult:
    """合规检测结果"""
    passed: bool = True
    risk_level: str = "low"  # low / medium / high / critical
    issues: List[ComplianceIssue] = field(default_factory=list)
    total_issues: int = 0
    supreme_count: int = 0
    high_count: int = 0
    medium_count: int = 0
    sensitive_count: int = 0


def _find_words_in_text(
    text: str,
    word_list: List[str],
    level: str,
    category: str,
) -> List[ComplianceIssue]:
    """
    在文本中查找违禁词（去重：如果一个词是另一个的子串，只保留长的）

    Args:
        text: 文本
        word_list: 词列表
        level: 风险等级
        category: 分类

    Returns:
        问题列表
    """
    # 按长度降序排列，长词优先匹配
    sorted_words = sorted(word_list, key=len, reverse=True)
    found_words = []
    issues = []

    for word in sorted_words:
        if word not in text:
            continue
        # 检查是否已经被更长的词覆盖
        is_covered = False
        for fw in found_words:
            if word in fw:
                is_covered = True
                break
        if is_covered:
            continue

        found_words.append(word)
        if level == "supreme":
            suggestion = REPLACEMENT_SUGGESTIONS.get(word, "建议删除或替换")
        elif level == "high":
            suggestion = REPLACEMENT_SUGGESTIONS.get(word, "建议谨慎使用")
        elif level == "medium":
            suggestion = REPLACEMENT_SUGGESTIONS.get(word, "建议核实后使用")
        else:
            suggestion = "建议删除"

        issues.append(ComplianceIssue(
            word=word,
            level=level,
            category=category,
            suggestion=suggestion,
            context=_get_context(text, word),
        ))

    return issues


def check_compliance(
    text: str,
    context: str = "general",
) -> ComplianceResult:
    """
    检测文本的广告合规性

    Args:
        text: 要检测的文本
        context: 文本来源（subtitle/voiceover/title/hashtag）

    Returns:
        合规检测结果
    """
    result = ComplianceResult()
    result.total_issues = 0

    if not text:
        return result

    # P2 修复：category 按词库类型正确分配，而非全部标为 "extreme"
    # 检测最高级禁用词
    supreme_issues = _find_words_in_text(text, BANNED_WORDS_SUPREME, "supreme", "banned_supreme")
    result.issues.extend(supreme_issues)
    result.supreme_count = len(supreme_issues)
    result.total_issues += len(supreme_issues)

    # 检测高风险词
    high_issues = _find_words_in_text(text, BANNED_WORDS_HIGH_RISK, "high", "marketing_claim")
    result.issues.extend(high_issues)
    result.high_count = len(high_issues)
    result.total_issues += len(high_issues)

    # 检测中风险词
    medium_issues = _find_words_in_text(text, BANNED_WORDS_MEDIUM_RISK, "medium", "regulatory")
    result.issues.extend(medium_issues)
    result.medium_count = len(medium_issues)
    result.total_issues += len(medium_issues)

    # 检测敏感词
    sensitive_issues = _find_words_in_text(text, SENSITIVE_WORDS, "sensitive", "sensitive")
    result.issues.extend(sensitive_issues)
    result.sensitive_count = len(sensitive_issues)
    result.total_issues += len(sensitive_issues)

    # 评估风险等级
    if result.supreme_count > 0 or result.sensitive_count > 0:
        result.risk_level = "critical"
        result.passed = False
    elif result.high_count >= 3:
        result.risk_level = "high"
        result.passed = False
    elif result.high_count > 0 or result.medium_count >= 3:
        result.risk_level = "medium"
        result.passed = False
    elif result.medium_count > 0:
        result.risk_level = "low"
        result.passed = False  # P2.6: low 风险也返回未通过，由调用方决定是否放行
    else:
        result.risk_level = "none"
        result.passed = True

    return result


def _get_context(text: str, word: str, window: int = 10) -> str:
    """获取词语出现的上下文"""
    idx = text.find(word)
    if idx == -1:
        return text[:20]
    start = max(0, idx - window)
    end = min(len(text), idx + len(word) + window)
    return text[start:end]


def check_script_compliance(
    script: Dict[str, Any],
) -> Dict[str, Any]:
    """
    检测整个广告脚本的合规性

    Args:
        script: 广告脚本（来自 generate_ad_script）

    Returns:
        {
            "passed": bool,
            "risk_level": str,
            "all_issues": [...],
            "by_segment": { segment_index: issues },
            "summary": str,
        }
    """
    all_issues = []
    by_segment = {}

    for seg in script.get("segments", []):
        seg_idx = seg.get("segment", 0)
        seg_issues = []

        # 检测字幕
        subtitle = seg.get("subtitle", "")
        sub_result = check_compliance(subtitle, context=f"segment_{seg_idx}_subtitle")
        seg_issues.extend(sub_result.issues)

        # 检测口播
        voiceover = seg.get("voiceover", "")
        vo_result = check_compliance(voiceover, context=f"segment_{seg_idx}_voiceover")
        seg_issues.extend(vo_result.issues)

        by_segment[seg_idx] = seg_issues
        all_issues.extend(seg_issues)

    # 检测标题
    title = script.get("title", "")
    title_result = check_compliance(title, context="title")
    all_issues.extend(title_result.issues)

    # P1 修复：检测 hashtags（之前完全跳过）
    hashtag_issues = []
    for tag in script.get("hashtags", []):
        # 去掉 # 前缀再检测
        tag_text = tag.lstrip("#")
        tag_result = check_compliance(tag_text, context=f"hashtag_{tag_text[:20]}")
        hashtag_issues.extend(tag_result.issues)
    all_issues.extend(hashtag_issues)

    # 评估整体风险
    supreme_count = sum(1 for i in all_issues if i.level == "supreme")
    high_count = sum(1 for i in all_issues if i.level == "high")
    medium_count = sum(1 for i in all_issues if i.level == "medium")
    sensitive_count = sum(1 for i in all_issues if i.level == "sensitive")

    if supreme_count > 0 or sensitive_count > 0:
        overall_risk = "critical"
        passed = False
    elif high_count >= 3:
        overall_risk = "high"
        passed = False
    elif high_count > 0 or medium_count >= 5:
        overall_risk = "medium"
        passed = False
    elif medium_count > 0:
        overall_risk = "low"
        passed = False  # P2.6: low 风险也返回未通过
    else:
        overall_risk = "none"
        passed = True

    # 生成摘要
    summary_parts = []
    if supreme_count > 0:
        summary_parts.append(f"⚠️ {supreme_count} 个最高级禁用词（必须修改）")
    if sensitive_count > 0:
        summary_parts.append(f"🚨 {sensitive_count} 个敏感词（必须删除）")
    if high_count > 0:
        summary_parts.append(f"⚡ {high_count} 个高风险词（建议修改）")
    if medium_count > 0:
        summary_parts.append(f"💡 {medium_count} 个中风险词（建议核实）")
    if not summary_parts:
        summary_parts.append("✅ 未检测到合规问题")

    return {
        "passed": passed,
        "risk_level": overall_risk,
        "all_issues": all_issues,
        "by_segment": by_segment,
        "title_issues": title_result.issues,
        "hashtag_issues": hashtag_issues,  # P1 修复：新增 hashtag 问题字段
        "summary": " | ".join(summary_parts),
        "counts": {
            "supreme": supreme_count,
            "high": high_count,
            "medium": medium_count,
            "sensitive": sensitive_count,
            "total": len(all_issues),
        },
    }


def print_compliance_report(result: Dict[str, Any]):
    """打印合规检测报告"""
    print()
    print("=" * 60)
    print("📋 广告合规检测报告")
    print("=" * 60)
    print(f"  风险等级：{result['risk_level'].upper()}")
    print(f"  检测结果：{'✅ 通过' if result['passed'] else '❌ 未通过'}")
    print(f"  问题总数：{result['counts']['total']}")
    print()

    if result["counts"]["total"] == 0:
        print("🎉 太棒了！未检测到任何合规问题。")
        print("=" * 60)
        return

    # 按严重程度分类显示
    level_order = ["supreme", "sensitive", "high", "medium"]
    level_names = {
        "supreme": "🚨 最高级禁用词（必须修改）",
        "sensitive": "🚨 敏感词（必须删除）",
        "high": "⚡ 高风险词（建议修改）",
        "medium": "💡 中风险词（建议核实）",
    }

    for level in level_order:
        level_issues = [i for i in result["all_issues"] if i.level == level]
        if not level_issues:
            continue

        print(f"\n{level_names[level]}（{len(level_issues)} 个）")
        print("-" * 40)

        seen = set()
        for issue in level_issues:
            if issue.word in seen:
                continue
            seen.add(issue.word)
            suggestion = f" → 建议：{issue.suggestion}" if issue.suggestion else ""
            print(f"  • \"{issue.word}\"{suggestion}")
            if issue.context:
                print(f"    上下文：...{issue.context}...")

    print()
    print("=" * 60)
    print("💡 提示：")
    print("   1. 最高级禁用词违反《广告法》，必须修改")
    print("   2. 高风险词建议替换为更温和的表述")
    print("   3. 敏感词请直接删除")
    print("   4. 最终发布前建议人工复核")
    print("=" * 60)


if __name__ == "__main__":
    # 测试
    test_texts = [
        "这是最好的产品，全网第一，秒杀所有竞品！",
        "这款产品效果很好，很多人都推荐",
        "100%有效，立竿见影，包治百病",
        "超级好用，爆款推荐，必入！",
        "这是一款优秀的产品，值得考虑",
    ]

    for text in test_texts:
        print(f"\n检测：{text}")
        result = check_compliance(text)
        print(f"  风险等级：{result.risk_level}")
        print(f"  是否通过：{result.passed}")
        print(f"  问题数：{result.total_issues}")
        for issue in result.issues:
            print(f"    - [{issue.level}] {issue.word} → {issue.suggestion}")
