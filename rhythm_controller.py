#!/usr/bin/env python3
"""
节奏控制器（Rhythm Controller）

参考行业最佳实践：
- Runway ML: Rhythm curve system
- Adobe Premiere: Beat Detection
- TikTok: Rhythm analysis

核心特点：
1. 情绪到 BPM 的映射
2. 脚本节奏分析
3. 节拍时间点生成
4. 转场节奏匹配
5. 字幕同步到节拍
"""

from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum


class EmotionLevel(Enum):
    """情绪强度等级"""
    LOW = "low"              # 低强度（平静、舒缓）
    MODERATE = "moderate"    # 中强度（正常、稳定）
    HIGH = "high"            # 高强度（紧张、兴奋）
    EXTREME = "extreme"      # 极高强度（激动、震撼）


class RhythmPattern(Enum):
    """节奏模式"""
    SLOW = "slow"            # 慢节奏
    MODERATE = "moderate"    # 中等节奏
    FAST = "fast"            # 快节奏
    DYNAMIC = "dynamic"      # 动态变化


@dataclass
class RhythmSegment:
    """节奏段落"""
    segment_index: int
    narrative_type: str
    emotion: str
    emotion_level: EmotionLevel
    bpm: int
    beats_per_second: float
    duration: float
    start_time: float = 0.0
    end_time: float = 0.0


@dataclass
class BeatTiming:
    """节拍时间点"""
    time: float              # 节拍时间（秒）
    beat_number: int         # 节拍编号
    segment_index: int       # 所属段落索引
    is_emphasis: bool = False  # 是否为重拍


@dataclass
class RhythmCurve:
    """节奏曲线"""
    segments: List[RhythmSegment]
    overall_bpm: int
    total_duration: float
    beats: List[BeatTiming]


class RhythmController:
    """节奏控制器主类"""

    # 叙事类型→情绪强度映射
    NARRATIVE_EMOTION_MAP = {
        "hook": EmotionLevel.HIGH,
        "turning_point": EmotionLevel.MODERATE,
        "showcase": EmotionLevel.MODERATE,
        "result": EmotionLevel.HIGH,
        "cta": EmotionLevel.EXTREME,
        "opening": EmotionLevel.MODERATE,
        "build": EmotionLevel.HIGH,
        "climax": EmotionLevel.EXTREME,
        "resolution": EmotionLevel.LOW,
    }

    # 情绪强度→BPM映射
    EMOTION_BPM_MAP = {
        EmotionLevel.LOW: (60, 80),
        EmotionLevel.MODERATE: (80, 100),
        EmotionLevel.HIGH: (100, 120),
        EmotionLevel.EXTREME: (120, 140),
    }

    # 产品品类→基础BPM映射
    CATEGORY_BPM_MAP = {
        "美妆": 90,
        "食品": 95,
        "家居": 85,
        "数码": 100,
        "个护": 88,
        "服饰": 105,
        "app": 100,
        "汽车": 95,
        "房产": 80,
        "教育": 85,
        "医疗": 80,
        "default": 90,
    }

    # 节奏模式→BPM范围
    PATTERN_BPM_RANGES = {
        RhythmPattern.SLOW: (60, 85),
        RhythmPattern.MODERATE: (85, 105),
        RhythmPattern.FAST: (105, 130),
        RhythmPattern.DYNAMIC: (70, 130),
    }

    # 情绪关键词→强度映射
    EMOTION_KEYWORDS = {
        "calm": EmotionLevel.LOW,
        "peaceful": EmotionLevel.LOW,
        "relaxed": EmotionLevel.LOW,
        "content": EmotionLevel.LOW,
        "gentle": EmotionLevel.LOW,
        "neutral": EmotionLevel.MODERATE,
        "normal": EmotionLevel.MODERATE,
        "steady": EmotionLevel.MODERATE,
        "confident": EmotionLevel.MODERATE,
        "lively": EmotionLevel.MODERATE,
        "tense": EmotionLevel.HIGH,
        "excited": EmotionLevel.HIGH,
        "energetic": EmotionLevel.HIGH,
        "anxious": EmotionLevel.HIGH,
        "hopeful": EmotionLevel.HIGH,
        "joyful": EmotionLevel.HIGH,
        "dramatic": EmotionLevel.EXTREME,
        "shocking": EmotionLevel.EXTREME,
        "intense": EmotionLevel.EXTREME,
        "powerful": EmotionLevel.EXTREME,
        "urgent": EmotionLevel.EXTREME,
    }

    def analyze_script_rhythm(
        self,
        segments: List[Dict[str, Any]],
        product_category: str = "default",
    ) -> RhythmCurve:
        """
        分析脚本节奏，生成节奏曲线。

        Args:
            segments: 脚本段落列表
            product_category: 产品品类

        Returns:
            RhythmCurve
        """
        rhythm_segments = []
        current_time = 0.0
        category_base_bpm = self.CATEGORY_BPM_MAP.get(product_category, 90)

        for i, segment in enumerate(segments):
            narrative_type = segment.get("narrative", "") or segment.get("type", "")
            emotion = segment.get("emotion", "")
            duration = segment.get("duration", 5.0)

            emotion_level = self._determine_emotion_level(narrative_type, emotion)
            bpm = self._calculate_bpm(emotion_level, category_base_bpm)
            beats_per_second = bpm / 60

            rhythm_segment = RhythmSegment(
                segment_index=i,
                narrative_type=narrative_type,
                emotion=emotion,
                emotion_level=emotion_level,
                bpm=bpm,
                beats_per_second=beats_per_second,
                duration=duration,
                start_time=current_time,
                end_time=current_time + duration,
            )

            rhythm_segments.append(rhythm_segment)
            current_time += duration

        total_duration = current_time
        overall_bpm = self._calculate_overall_bpm(rhythm_segments)
        beats = self._generate_beats(rhythm_segments)

        return RhythmCurve(
            segments=rhythm_segments,
            overall_bpm=overall_bpm,
            total_duration=total_duration,
            beats=beats,
        )

    def _determine_emotion_level(
        self,
        narrative_type: str,
        emotion_text: str,
    ) -> EmotionLevel:
        """
        根据叙事类型和情绪文本确定情绪强度。

        Args:
            narrative_type: 叙事类型
            emotion_text: 情绪描述文本

        Returns:
            EmotionLevel
        """
        narrative_type = narrative_type.lower().strip()

        if narrative_type in self.NARRATIVE_EMOTION_MAP:
            return self.NARRATIVE_EMOTION_MAP[narrative_type]

        if emotion_text:
            emotion_text = emotion_text.lower()
            for keyword, level in self.EMOTION_KEYWORDS.items():
                if keyword in emotion_text:
                    return level

        return EmotionLevel.MODERATE

    def _calculate_bpm(
        self,
        emotion_level: EmotionLevel,
        base_bpm: int,
    ) -> int:
        """
        根据情绪强度和基础BPM计算目标BPM。

        Args:
            emotion_level: 情绪强度
            base_bpm: 基础BPM

        Returns:
            目标BPM
        """
        bpm_range = self.EMOTION_BPM_MAP.get(emotion_level, (80, 100))
        min_bpm, max_bpm = bpm_range

        emotion_factor = {
            EmotionLevel.LOW: 0.85,
            EmotionLevel.MODERATE: 1.0,
            EmotionLevel.HIGH: 1.15,
            EmotionLevel.EXTREME: 1.30,
        }[emotion_level]

        target_bpm = int(base_bpm * emotion_factor)

        return max(min_bpm, min(max_bpm, target_bpm))

    def _calculate_overall_bpm(self, segments: List[RhythmSegment]) -> int:
        """
        计算整体BPM（加权平均）。

        Args:
            segments: 节奏段落列表

        Returns:
            整体BPM
        """
        if not segments:
            return 90

        total_weighted_bpm = 0
        total_duration = 0

        for seg in segments:
            total_weighted_bpm += seg.bpm * seg.duration
            total_duration += seg.duration

        if total_duration == 0:
            return 90

        return int(total_weighted_bpm / total_duration)

    def _generate_beats(self, segments: List[RhythmSegment]) -> List[BeatTiming]:
        """
        生成所有节拍时间点。

        Args:
            segments: 节奏段落列表

        Returns:
            节拍时间点列表
        """
        beats = []
        beat_number = 0

        for seg in segments:
            beats_per_second = seg.beats_per_second
            duration = seg.duration
            start_time = seg.start_time

            num_beats = int(duration * beats_per_second)
            beat_interval = duration / num_beats if num_beats > 0 else 0.5

            for i in range(num_beats):
                beat_time = start_time + i * beat_interval
                is_emphasis = (i % 4 == 0)

                beats.append(BeatTiming(
                    time=beat_time,
                    beat_number=beat_number,
                    segment_index=seg.segment_index,
                    is_emphasis=is_emphasis,
                ))
                beat_number += 1

        return beats

    def generate_beat_timings(
        self,
        segments: List[Dict[str, Any]],
        rhythm_curve: Optional[RhythmCurve] = None,
    ) -> List[Dict[str, Any]]:
        """
        生成节拍时间点列表（用于转场和字幕同步）。

        Args:
            segments: 脚本段落列表
            rhythm_curve: 节奏曲线（可选，如不提供则自动计算）

        Returns:
            节拍时间点字典列表
        """
        if not rhythm_curve:
            rhythm_curve = self.analyze_script_rhythm(segments)

        timings = []

        for beat in rhythm_curve.beats:
            timings.append({
                "time": beat.time,
                "beat_number": beat.beat_number,
                "segment_index": beat.segment_index,
                "is_emphasis": beat.is_emphasis,
            })

        return timings

    def match_transition_to_rhythm(
        self,
        from_segment: Dict[str, Any],
        to_segment: Dict[str, Any],
        rhythm_curve: RhythmCurve,
    ) -> Dict[str, Any]:
        """
        根据节奏匹配转场效果。

        Args:
            from_segment: 源段落
            to_segment: 目标段落
            rhythm_curve: 节奏曲线

        Returns:
            转场配置
        """
        from_narrative = from_segment.get("narrative", "") or from_segment.get("type", "")
        to_narrative = to_segment.get("narrative", "") or to_segment.get("type", "")

        from_level = self._determine_emotion_level(from_narrative, from_segment.get("emotion", ""))
        to_level = self._determine_emotion_level(to_narrative, to_segment.get("emotion", ""))

        intensity_change = self._get_intensity_change(from_level, to_level)

        return self._select_transition(intensity_change, rhythm_curve.overall_bpm)

    def _get_intensity_change(
        self,
        from_level: EmotionLevel,
        to_level: EmotionLevel,
    ) -> str:
        """
        获取强度变化类型。

        Args:
            from_level: 源强度
            to_level: 目标强度

        Returns:
            变化类型：stable/increase/decrease/dramatic
        """
        level_order = [EmotionLevel.LOW, EmotionLevel.MODERATE, EmotionLevel.HIGH, EmotionLevel.EXTREME]
        from_idx = level_order.index(from_level)
        to_idx = level_order.index(to_level)

        diff = to_idx - from_idx

        if diff == 0:
            return "stable"
        elif diff == 1:
            return "increase"
        elif diff >= 2:
            return "dramatic"
        elif diff == -1:
            return "decrease"
        else:
            return "dramatic"

    def _select_transition(
        self,
        intensity_change: str,
        bpm: int,
    ) -> Dict[str, Any]:
        """
        根据强度变化和BPM选择转场。

        Args:
            intensity_change: 强度变化类型
            bpm: BPM

        Returns:
            转场配置
        """
        transition_map = {
            "stable": {
                "type": "dissolve",
                "duration": max(0.2, 0.4 - bpm / 300),
            },
            "increase": {
                "type": "zoom_in" if bpm > 100 else "push",
                "duration": max(0.15, 0.3 - bpm / 400),
            },
            "decrease": {
                "type": "zoom_out" if bpm > 100 else "pull",
                "duration": max(0.2, 0.4 - bpm / 400),
            },
            "dramatic": {
                "type": "flash" if bpm > 110 else "cut",
                "duration": max(0.1, 0.25 - bpm / 500),
            },
        }

        return transition_map.get(intensity_change, transition_map["stable"])

    def sync_subtitles_to_beats(
        self,
        subtitles: List[Dict[str, Any]],
        rhythm_curve: RhythmCurve,
    ) -> List[Dict[str, Any]]:
        """
        将字幕同步到节拍。

        Args:
            subtitles: 字幕列表
            rhythm_curve: 节奏曲线

        Returns:
            同步后的字幕列表
        """
        if not rhythm_curve.beats:
            return subtitles

        synced_subtitles = []

        for subtitle in subtitles:
            start_time = subtitle.get("start_time", 0.0)

            nearest_beat = min(
                rhythm_curve.beats,
                key=lambda b: abs(b.time - start_time)
            )

            adjusted_start = nearest_beat.time

            synced_subtitles.append({
                **subtitle,
                "start_time": adjusted_start,
                "synced_to_beat": nearest_beat.beat_number,
                "is_emphasis_subtitle": nearest_beat.is_emphasis,
            })

        return synced_subtitles

    def get_rhythm_pattern(self, rhythm_curve: RhythmCurve) -> RhythmPattern:
        """
        获取整体节奏模式。

        Args:
            rhythm_curve: 节奏曲线

        Returns:
            RhythmPattern
        """
        bpm = rhythm_curve.overall_bpm

        if bpm < 85:
            return RhythmPattern.SLOW
        elif bpm < 105:
            return RhythmPattern.MODERATE
        elif bpm < 130:
            return RhythmPattern.FAST
        else:
            return RhythmPattern.DYNAMIC

    def generate_bgm_keywords(
        self,
        rhythm_curve: RhythmCurve,
        product_category: str = "default",
    ) -> List[str]:
        """
        根据节奏曲线生成BGM关键词。

        Args:
            rhythm_curve: 节奏曲线
            product_category: 产品品类

        Returns:
            BGM关键词列表
        """
        keywords = []

        pattern = self.get_rhythm_pattern(rhythm_curve)
        pattern_keywords = {
            RhythmPattern.SLOW: ["chill", "ambient", "relaxing", "gentle"],
            RhythmPattern.MODERATE: ["upbeat", "positive", "energetic", "happy"],
            RhythmPattern.FAST: ["dynamic", "powerful", "exciting", "dance"],
            RhythmPattern.DYNAMIC: ["epic", "cinematic", "dramatic", "build"],
        }
        keywords.extend(pattern_keywords.get(pattern, []))

        category_keywords = {
            "美妆": ["beauty", "elegant", "feminine"],
            "食品": ["food", "warm", "cozy"],
            "家居": ["home", "comfortable", "warm"],
            "数码": ["tech", "modern", "futuristic"],
            "个护": ["clean", "fresh", "relaxing"],
            "服饰": ["fashion", "trendy", "elegant"],
            "app": ["tech", "modern", "minimal"],
            "汽车": ["powerful", "luxury", "dynamic"],
            "房产": ["home", "elegant", "comfortable"],
            "教育": ["inspiring", "positive", "motivational"],
            "医疗": ["professional", "trustworthy", "calm"],
        }
        keywords.extend(category_keywords.get(product_category, []))

        return keywords

    def adjust_segment_duration(
        self,
        segments: List[Dict[str, Any]],
        rhythm_curve: RhythmCurve,
        target_duration: float = None,
    ) -> List[Dict[str, Any]]:
        """
        根据节奏曲线调整段落时长。

        Args:
            segments: 脚本段落列表
            rhythm_curve: 节奏曲线
            target_duration: 目标总时长（可选）

        Returns:
            调整后的段落列表
        """
        if not target_duration:
            return segments

        current_total = sum(s.get("duration", 5.0) for s in segments)
        if current_total == 0:
            return segments

        scale_factor = target_duration / current_total

        adjusted_segments = []
        for i, segment in enumerate(segments):
            original_duration = segment.get("duration", 5.0)
            adjusted_duration = original_duration * scale_factor

            adjusted_segments.append({
                **segment,
                "duration": round(adjusted_duration, 2),
                "original_duration": original_duration,
            })

        return adjusted_segments