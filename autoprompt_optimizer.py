#!/usr/bin/env python3
"""
AutoPrompt优化器（AutoPrompt Optimizer）

基于遗传算法思想自动优化Prompt，通过生成多个变体、评估效果、筛选优胜者，
持续进化出更高质量的Prompt。

核心理念：Prompt不是写死的，而是进化出来的。
"""

import re
import random
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field

from config import setup_logger

logger = setup_logger(__name__)


@dataclass
class PromptVariant:
    """Prompt变体"""
    original: str
    variant: str
    mutations: List[str] = field(default_factory=list)
    score: float = 0.0
    generation: int = 0


class AutoPromptOptimizer:
    """AutoPrompt优化器主类"""

    # Prompt增强词库
    QUALITY_BOOSTERS = [
        "sharp focus", "highly detailed", "professional lighting",
        "cinematic composition", "realistic texture", "8k resolution",
        "studio quality", "vivid colors", "perfect exposure",
    ]

    STYLE_MODIFIERS = {
        "cinematic": ["cinematic color grading", "film grain", "anamorphic lens"],
        "product": ["commercial photography", "product showcase", "clean background"],
        "fashion": ["editorial style", "high fashion", "soft lighting"],
        "lifestyle": ["natural lighting", "authentic moment", "warm tones"],
    }

    # 同义词替换映射
    SYNONYMS = {
        "beautiful": ["gorgeous", "stunning", "elegant", "radiant"],
        "woman": ["female", "lady", "girl"],
        "holding": ["presenting", "showcasing", "displaying"],
        "product": ["item", "goods", "merchandise"],
        "close-up": ["macro shot", "extreme close-up", "detailed view"],
    }

    def __init__(self, max_generations: int = 3, population_size: int = 5):
        self.max_generations = max_generations
        self.population_size = population_size

    def generate_variants(
        self,
        base_prompt: str,
        narrative_type: str = "showcase",
        style: str = "cinematic",
        n_variants: int = 5,
    ) -> List[PromptVariant]:
        """
        生成Prompt的多个变体。

        变异策略：
        1. 同义词替换
        2. 添加质量增强词
        3. 调整描述顺序
        4. 添加风格修饰词
        5. 精简冗余描述
        """
        variants = []

        # 变体1：同义词替换
        v1 = self._synonym_replace(base_prompt)
        variants.append(PromptVariant(base_prompt, v1, ["synonym_replace"]))

        # 变体2：添加质量增强词
        v2 = self._add_quality_boosters(base_prompt, narrative_type)
        variants.append(PromptVariant(base_prompt, v2, ["add_quality_boosters"]))

        # 变体3：添加风格修饰词
        v3 = self._add_style_modifiers(base_prompt, style)
        variants.append(PromptVariant(base_prompt, v3, ["add_style_modifiers"]))

        # 变体4：精简版（去除冗余）
        v4 = self._compress_prompt(base_prompt)
        variants.append(PromptVariant(base_prompt, v4, ["compress"]))

        # 变体5：综合优化
        v5 = self._comprehensive_optimize(base_prompt, narrative_type, style)
        variants.append(PromptVariant(base_prompt, v5, ["comprehensive"]))

        return variants[:n_variants]

    def _synonym_replace(self, prompt: str) -> str:
        """同义词替换"""
        words = prompt.split()
        new_words = []
        replaced = 0

        for word in words:
            clean_word = re.sub(r'[^\w]', '', word.lower())
            if clean_word in self.SYNONYMS and replaced < 3:
                synonym = random.choice(self.SYNONYMS[clean_word])
                # 保持原大小写格式
                if word[0].isupper():
                    synonym = synonym.capitalize()
                new_words.append(synonym)
                replaced += 1
            else:
                new_words.append(word)

        return " ".join(new_words)

    def _add_quality_boosters(self, prompt: str, narrative_type: str) -> str:
        """添加质量增强词"""
        boosters = random.sample(self.QUALITY_BOOSTERS, min(2, len(self.QUALITY_BOOSTERS)))
        booster_str = ", ".join(boosters)

        # 在句尾添加
        if prompt.endswith("."):
            return f"{prompt[:-1]}, {booster_str}."
        else:
            return f"{prompt}, {booster_str}"

    def _add_style_modifiers(self, prompt: str, style: str) -> str:
        """添加风格修饰词"""
        modifiers = self.STYLE_MODIFIERS.get(style, self.STYLE_MODIFIERS["cinematic"])
        selected = random.sample(modifiers, min(2, len(modifiers)))
        modifier_str = ", ".join(selected)

        return f"{prompt}, {modifier_str}"

    def _compress_prompt(self, prompt: str) -> str:
        """精简Prompt，去除冗余"""
        # 移除重复词
        words = prompt.split()
        seen = set()
        unique = []
        for word in words:
            key = word.lower().strip(",.")
            if key not in seen or len(key) < 3:
                seen.add(key)
                unique.append(word)

        # 限制长度
        compressed = " ".join(unique)
        if len(compressed) > 500:
            compressed = compressed[:500] + "..."

        return compressed

    def _comprehensive_optimize(self, prompt: str, narrative_type: str, style: str) -> str:
        """综合优化：结合多种策略"""
        # 先精简
        optimized = self._compress_prompt(prompt)
        # 再添加质量词
        optimized = self._add_quality_boosters(optimized, narrative_type)
        # 再添加风格词
        optimized = self._add_style_modifiers(optimized, style)
        return optimized

    def score_variant(
        self,
        variant: PromptVariant,
        quality_metrics: Optional[Dict[str, float]] = None,
    ) -> float:
        """
        对Prompt变体进行评分。

        评分维度：
        1. 长度适中（50-500字符）
        2. 包含质量词
        3. 包含动作描述
        4. 无重复词
        5. 外部质量指标（如生成的图片评分）
        """
        score = 0.0
        text = variant.variant.lower()

        # 1. 长度评分
        length = len(variant.variant)
        if 50 <= length <= 300:
            score += 30
        elif 300 < length <= 500:
            score += 20
        else:
            score += 10

        # 2. 质量词覆盖率
        quality_keywords = ["sharp", "detailed", "professional", "cinematic", "high quality"]
        matched = sum(1 for kw in quality_keywords if kw in text)
        score += matched * 5

        # 3. 动作描述
        action_keywords = ["holding", "using", "showing", "presenting", "applying"]
        has_action = any(kw in text for kw in action_keywords)
        if has_action:
            score += 15

        # 4. 一致性关键词
        if "same" in text or "reference" in text:
            score += 10

        # 5. 外部质量指标（如果有）
        if quality_metrics:
            score += quality_metrics.get("image_quality", 0) * 0.3
            score += quality_metrics.get("consistency", 0) * 0.2

        variant.score = min(100, score)
        return variant.score

    def select_best_variant(
        self,
        variants: List[PromptVariant],
    ) -> Tuple[str, float]:
        """选择最佳变体"""
        if not variants:
            return "", 0.0

        best = max(variants, key=lambda v: v.score)
        return best.variant, best.score

    def optimize(
        self,
        base_prompt: str,
        narrative_type: str = "showcase",
        style: str = "cinematic",
        generate_and_evaluate_func: Optional[callable] = None,
    ) -> Tuple[str, List[PromptVariant]]:
        """
        完整的优化流程。

        Args:
            base_prompt: 原始Prompt
            narrative_type: 叙事类型
            style: 风格
            generate_and_evaluate_func: 可选的生成+评估函数，用于真实质量反馈

        Returns:
            (最佳Prompt, 所有变体)
        """
        logger.info(f"🧬 开始优化Prompt（代数：{self.max_generations}，种群：{self.population_size}）")

        # 生成初始种群
        variants = self.generate_variants(base_prompt, narrative_type, style, self.population_size)

        # 评估初始种群
        for variant in variants:
            metrics = None
            if generate_and_evaluate_func:
                try:
                    metrics = generate_and_evaluate_func(variant.variant)
                except Exception as e:
                    logger.warning(f"评估变体失败：{e}")
            self.score_variant(variant, metrics)

        best_prompt, best_score = self.select_best_variant(variants)
        logger.info(f"✅ 最佳Prompt（评分{best_score:.1f}）：{best_prompt[:80]}...")

        return best_prompt, variants

    def evolve_prompt_template(
        self,
        narrative_type: str,
        successful_prompts: List[str],
    ) -> str:
        """
        基于多个成功案例进化出通用Prompt模板。

        策略：提取成功案例中的共同关键词，构建模板。
        """
        if not successful_prompts:
            return ""

        # 提取所有词
        all_words = []
        for prompt in successful_prompts:
            all_words.extend(re.findall(r'\b\w+\b', prompt.lower()))

        # 统计词频
        from collections import Counter
        word_freq = Counter(all_words)

        # 保留高频词（去掉常见停用词）
        stop_words = {"a", "an", "the", "in", "on", "at", "to", "of", "and", "or", "is", "are"}
        common_words = [w for w, c in word_freq.most_common(20) if w not in stop_words and len(w) > 3]

        # 构建模板
        template = f"A {{subject}}, {', '.join(common_words[:8])}, high quality, cinematic"

        return template
