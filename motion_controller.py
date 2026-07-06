#!/usr/bin/env python3
"""
动作控制系统（Motion Controller）

参考行业最佳实践：
- Kling/Midjourney: Motion prompts
- Runway: Motion Brush
- Stable Video Diffusion: Motion parameters

核心特点：
1. 精准控制角色动作和节奏
2. 支持动作描述模板
3. 动作曲线规划（加速/减速）
4. 动作一致性保证
"""

from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from enum import Enum


class MotionType(Enum):
    """动作类型"""
    STATIC = "static"              # 静态不动
    WALKING = "walking"            # 走路
    RUNNING = "running"            # 跑步
    HOLDING = "holding"            # 手持物品
    USING = "using"                # 使用物品
    TALKING = "talking"            # 说话
    LOOKING = "looking"            # 看
    SMILING = "smiling"            # 微笑
    CRYING = "crying"              # 哭泣
    SITTING = "sitting"            # 坐着
    STANDING = "standing"          # 站着
    WAVING = "waving"              # 挥手
    POINTING = "pointing"          # 指向
    OPENING = "opening"            # 打开
    CLOSING = "closing"            # 关闭


class MotionIntensity(Enum):
    """动作强度"""
    MINIMAL = "minimal"            # 极轻微
    SUBTLE = "subtle"              # 轻微
    NORMAL = "normal"              # 正常
    STRONG = "strong"              # 强烈
    EXTREME = "extreme"            # 极端


class MotionSpeed(Enum):
    """动作速度"""
    SLOW = "slow"                  # 慢
    MODERATE = "moderate"          # 中等
    FAST = "fast"                  # 快


@dataclass
class MotionSpec:
    """动作规格"""
    motion_type: MotionType
    intensity: MotionIntensity = MotionIntensity.NORMAL
    speed: MotionSpeed = MotionSpeed.MODERATE
    direction: str = ""            # 方向描述
    duration: float = 3.0          # 动作时长
    description: str = ""          # 详细描述


class MotionController:
    """动作控制器"""

    # 动作描述模板库
    MOTION_TEMPLATES = {
        MotionType.STATIC: {
            "description": "standing still, no movement",
            "keywords": ["static", "still", "motionless"],
        },
        MotionType.WALKING: {
            "description": "walking slowly towards camera, natural gait",
            "keywords": ["walking", "moving", "strolling"],
        },
        MotionType.RUNNING: {
            "description": "running quickly, dynamic movement",
            "keywords": ["running", "sprinting", "hurrying"],
        },
        MotionType.HOLDING: {
            "description": "holding object in hands, gentle grip",
            "keywords": ["holding", "carrying", "holding in hands"],
        },
        MotionType.USING: {
            "description": "using product, demonstrating usage",
            "keywords": ["using", "applying", "demonstrating"],
        },
        MotionType.TALKING: {
            "description": "talking naturally, mouth moving",
            "keywords": ["talking", "speaking", "chatting"],
        },
        MotionType.LOOKING: {
            "description": "looking at camera, eye contact",
            "keywords": ["looking", "gazing", "staring"],
        },
        MotionType.SMILING: {
            "description": "smiling warmly, happy expression",
            "keywords": ["smiling", "happy", "cheerful"],
        },
        MotionType.CRYING: {
            "description": "sad expression, tears",
            "keywords": ["crying", "sad", "tears"],
        },
        MotionType.SITTING: {
            "description": "sitting comfortably on chair",
            "keywords": ["sitting", "seated", "relaxing"],
        },
        MotionType.STANDING: {
            "description": "standing upright, confident posture",
            "keywords": ["standing", "upright", "confident"],
        },
        MotionType.WAVING: {
            "description": "waving hand friendly",
            "keywords": ["waving", "greeting", "hello"],
        },
        MotionType.POINTING: {
            "description": "pointing at product",
            "keywords": ["pointing", "indicating", "showing"],
        },
        MotionType.OPENING: {
            "description": "opening package or door",
            "keywords": ["opening", "unboxing", "revealing"],
        },
        MotionType.CLOSING: {
            "description": "closing package or door",
            "keywords": ["closing", "packaging", "finishing"],
        },
    }

    # 强度描述映射
    INTENSITY_MAP = {
        MotionIntensity.MINIMAL: "very subtle, barely noticeable",
        MotionIntensity.SUBTLE: "subtle, gentle movement",
        MotionIntensity.NORMAL: "natural, normal speed",
        MotionIntensity.STRONG: "strong, noticeable movement",
        MotionIntensity.EXTREME: "extreme, exaggerated movement",
    }

    # 速度描述映射
    SPEED_MAP = {
        MotionSpeed.SLOW: "slow, deliberate",
        MotionSpeed.MODERATE: "moderate, natural",
        MotionSpeed.FAST: "fast, energetic",
    }

    def __init__(self):
        pass

    def generate_motion_prompt(
        self,
        motion_spec: MotionSpec,
        character_description: str = "",
        product_description: str = "",
    ) -> str:
        """
        生成动作描述Prompt。
        
        Args:
            motion_spec: 动作规格
            character_description: 角色描述
            product_description: 产品描述
        
        Returns:
            动作描述字符串
        """
        template = self.MOTION_TEMPLATES.get(motion_spec.motion_type, {})
        base_desc = template.get("description", "")
        
        # 添加强度描述
        intensity_desc = self.INTENSITY_MAP.get(motion_spec.intensity, "")
        
        # 添加速度描述
        speed_desc = self.SPEED_MAP.get(motion_spec.speed, "")
        
        # 添加方向描述
        direction_desc = motion_spec.direction if motion_spec.direction else ""
        
        # 添加详细描述
        detail_desc = motion_spec.description if motion_spec.description else ""
        
        # 组合所有描述
        parts = [base_desc]
        if intensity_desc:
            parts.append(intensity_desc)
        if speed_desc:
            parts.append(speed_desc)
        if direction_desc:
            parts.append(direction_desc)
        if detail_desc:
            parts.append(detail_desc)
        
        # 添加角色描述
        if character_description:
            parts.append(f"{character_description}")
        
        # 添加产品描述（如果有使用动作）
        if product_description and motion_spec.motion_type in [MotionType.USING, MotionType.HOLDING, MotionType.POINTING]:
            parts.append(f"using {product_description}")
        
        return ", ".join(parts)

    def plan_motion_sequence(
        self,
        script_segments: List[Dict[str, Any]],
        emotion_curve: List[str],
    ) -> List[MotionSpec]:
        """
        根据剧本和情绪曲线规划动作序列。
        
        Args:
            script_segments: 剧本片段列表
            emotion_curve: 情绪曲线列表
        
        Returns:
            动作规格列表
        """
        motion_sequence = []
        
        for i, (segment, emotion) in enumerate(zip(script_segments, emotion_curve)):
            segment_type = segment.get("type", "")
            text = segment.get("text", "")
            
            # 根据段落类型和情绪选择动作
            motion_spec = self._select_motion_for_segment(segment_type, emotion, text)
            motion_sequence.append(motion_spec)
        
        return motion_sequence

    def _select_motion_for_segment(
        self,
        segment_type: str,
        emotion: str,
        text: str,
    ) -> MotionSpec:
        """为特定段落选择动作"""
        # 场景类型→动作映射
        motion_map = {
            "hook": MotionType.LOOKING,
            "turning_point": MotionType.STANDING,
            "showcase": MotionType.HOLDING,
            "result": MotionType.SMILING,
            "cta": MotionType.POINTING,
            "resolution": MotionType.WALKING,
        }
        
        motion_type = motion_map.get(segment_type, MotionType.STANDING)
        
        # 根据情绪调整强度
        emotion_intensity_map = {
            "warm peaceful": MotionIntensity.SUBTLE,
            "lively joyful": MotionIntensity.NORMAL,
            "anxious worried": MotionIntensity.SUBTLE,
            "relieved hopeful": MotionIntensity.NORMAL,
            "professional reassuring": MotionIntensity.SUBTLE,
            "happy peaceful": MotionIntensity.SUBTLE,
        }
        
        intensity = emotion_intensity_map.get(emotion, MotionIntensity.NORMAL)
        
        # 根据文本关键词调整动作
        if "使用" in text or "应用" in text:
            motion_type = MotionType.USING
        elif "展示" in text or "介绍" in text:
            motion_type = MotionType.POINTING
        elif "微笑" in text or "开心" in text:
            motion_type = MotionType.SMILING
        elif "哭" in text or "伤心" in text:
            motion_type = MotionType.CRYING
        
        return MotionSpec(
            motion_type=motion_type,
            intensity=intensity,
            speed=MotionSpeed.MODERATE,
            description="",
        )

    def ensure_motion_consistency(
        self,
        motion_sequence: List[MotionSpec],
        character_name: str,
    ) -> List[str]:
        """
        确保动作序列的一致性。
        
        Args:
            motion_sequence: 动作序列
            character_name: 角色名称
        
        Returns:
            一致性约束列表
        """
        constraints = []
        
        # 提取所有动作类型
        motion_types = [m.motion_type for m in motion_sequence]
        
        # 如果同一角色在多个场景有类似动作，添加一致性约束
        if MotionType.LOOKING in motion_types:
            constraints.append(f"{character_name} maintains consistent gaze direction")
        
        if MotionType.SMILING in motion_types:
            constraints.append(f"{character_name} maintains natural smile expression")
        
        if MotionType.HOLDING in motion_types:
            constraints.append(f"{character_name} holds product the same way")
        
        if MotionType.USING in motion_types:
            constraints.append(f"{character_name} uses product with consistent hand position")
        
        # 添加过渡平滑约束
        constraints.append("smooth motion transitions between actions")
        constraints.append("natural body posture throughout")
        
        return constraints

    def generate_motion_control_params(self) -> Dict[str, Any]:
        """
        生成运动控制参数（用于支持Motion Brush等高级功能）。
        
        Returns:
            运动控制参数字典
        """
        return {
            "motion_strength": 0.3,
            "motion_consistency": 0.8,
            "motion_smoothness": 0.9,
            "allow_warping": False,
            "motion_guidance": 5.0,
        }
