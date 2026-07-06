#!/usr/bin/env python3
"""
场景编辑器（Scene Editor）

参考行业最佳实践：
- Runway: Scene Editing
- Adobe Firefly: Scene Composition
- Stable Diffusion: Inpainting
- Pika: Scene Consistency
- Deforum: Temporal Coherence

核心特点：
1. 场景精细化编辑
2. 场景描述生成与优化
3. 场景过渡控制
4. 环境细节增强
5. 全局场景锚点系统
6. 镜头衔接与连续性检查
7. 场景一致性验证
"""

from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from enum import Enum


class SceneType(Enum):
    """场景类型"""
    INDOOR = "indoor"              # 室内
    OUTDOOR = "outdoor"            # 室外
    LIVING_ROOM = "living_room"    # 客厅
    KITCHEN = "kitchen"            # 厨房
    BEDROOM = "bedroom"            # 卧室
    OFFICE = "office"              # 办公室
    STREET = "street"              # 街道
    PARK = "park"                  # 公园
    STORE = "store"                # 商店
    STUDIO = "studio"              # 摄影棚


class TimeOfDay(Enum):
    """时间"""
    MORNING = "morning"            # 早晨
    AFTERNOON = "afternoon"        # 下午
    EVENING = "evening"            # 傍晚
    NIGHT = "night"                # 夜晚


class Weather(Enum):
    """天气"""
    SUNNY = "sunny"                # 晴朗
    CLOUDY = "cloudy"              # 多云
    RAINY = "rainy"                # 下雨
    SNOWY = "snowy"                # 下雪
    FOGGY = "foggy"                # 雾


class ContinuityLevel(Enum):
    """场景连续性级别"""
    STRICT = "strict"              # 严格连续（相同场景）
    MODERATE = "moderate"          # 中等连续（同一地点不同视角）
    LOOSE = "loose"                # 松散连续（不同场景但有视觉关联）
    DISCONNECTED = "disconnected"  # 无连续性（完全不同场景）


@dataclass
class SceneAnchor:
    """场景锚点：用于维持全局场景一致性"""
    anchor_id: str
    anchor_type: str
    reference_frames: List[str]
    scene_description: str
    lighting_description: str
    color_palette: str
    key_elements: List[str]
    camera_angle: str = ""


@dataclass
class ShotTransition:
    """镜头衔接描述"""
    from_shot_index: int
    to_shot_index: int
    transition_type: str
    duration: float
    continuity_level: ContinuityLevel
    reference_frame: Optional[str] = None
    color_correction: Dict[str, Any] = field(default_factory=dict)
    pacing_note: str = ""


@dataclass
class SceneDescription:
    """场景描述"""
    scene_type: SceneType
    time_of_day: TimeOfDay
    weather: Weather = Weather.SUNNY
    location_details: str = ""     # 位置细节
    background_elements: List[str] = field(default_factory=list)
    foreground_elements: List[str] = field(default_factory=list)
    atmosphere: str = ""           # 氛围描述
    lighting_keywords: str = ""    # 光线关键词
    color_palette: str = ""        # 色调
    camera_perspective: str = ""   # 视角
    anchor: Optional[SceneAnchor] = None  # 关联的场景锚点


class SceneEditor:
    """场景编辑器"""

    # 场景描述模板库
    SCENE_TEMPLATES = {
        SceneType.LIVING_ROOM: {
            "description": "cozy living room with comfortable sofa, warm lighting",
            "background": ["sofa", "coffee table", "TV", "plants", "bookshelf"],
            "atmosphere": "warm, cozy, inviting",
            "lighting": "warm ambient lighting, soft shadows",
        },
        SceneType.KITCHEN: {
            "description": "modern kitchen with stainless steel appliances",
            "background": ["refrigerator", "countertop", "oven", "sink", "cabinet"],
            "atmosphere": "clean, organized, functional",
            "lighting": "bright even lighting, natural light from window",
        },
        SceneType.BEDROOM: {
            "description": "comfortable bedroom with soft bedding",
            "background": ["bed", "nightstand", "wardrobe", "window", "lamp"],
            "atmosphere": "peaceful, relaxing, intimate",
            "lighting": "soft warm lighting, gentle shadows",
        },
        SceneType.OFFICE: {
            "description": "modern office workspace",
            "background": ["desk", "computer", "chair", "bookshelf", "window"],
            "atmosphere": "professional, productive, clean",
            "lighting": "bright functional lighting, natural light",
        },
        SceneType.STREET: {
            "description": "busy city street with shops and pedestrians",
            "background": ["buildings", "shops", "cars", "street lamp", "signs"],
            "atmosphere": "lively, dynamic, urban",
            "lighting": "natural daylight or streetlights",
        },
        SceneType.PARK: {
            "description": "beautiful park with trees and flowers",
            "background": ["trees", "grass", "flowers", "bench", "pathway"],
            "atmosphere": "peaceful, natural, refreshing",
            "lighting": "natural sunlight, dappled light",
        },
        SceneType.STORE: {
            "description": "modern retail store with products on display",
            "background": ["shelves", "products", "display cases", "cash register"],
            "atmosphere": "bright, inviting, commercial",
            "lighting": "bright even lighting, spotlight on products",
        },
        SceneType.STUDIO: {
            "description": "professional photo studio with backdrop",
            "background": ["backdrop", "lighting equipment", "reflector", "camera"],
            "atmosphere": "professional, controlled, clean",
            "lighting": "professional studio lighting, soft and even",
        },
    }

    # 时间描述映射
    TIME_OF_DAY_MAP = {
        TimeOfDay.MORNING: "bright morning light, golden hour",
        TimeOfDay.AFTERNOON: "bright afternoon sunlight",
        TimeOfDay.EVENING: "warm evening light, sunset glow",
        TimeOfDay.NIGHT: "soft night lighting, warm interior glow",
    }

    # 天气描述映射
    WEATHER_MAP = {
        Weather.SUNNY: "clear sky, bright sunlight",
        Weather.CLOUDY: "overcast sky, soft diffused light",
        Weather.RAINY: "rain falling, wet surfaces, moody atmosphere",
        Weather.SNOWY: "snow falling, white landscape, cold atmosphere",
        Weather.FOGGY: "foggy atmosphere, soft mist, mysterious mood",
    }

    def __init__(self):
        pass

    def generate_scene_prompt(
        self,
        scene_desc: SceneDescription,
        character_descriptions: List[str] = None,
        product_description: str = "",
    ) -> str:
        """
        生成场景描述Prompt。
        
        Args:
            scene_desc: 场景描述
            character_descriptions: 角色描述列表
            product_description: 产品描述
        
        Returns:
            场景描述字符串
        """
        template = self.SCENE_TEMPLATES.get(scene_desc.scene_type, {})
        
        # 基础场景描述
        parts = [template.get("description", "")]
        
        # 添加时间描述
        time_desc = self.TIME_OF_DAY_MAP.get(scene_desc.time_of_day, "")
        if time_desc:
            parts.append(time_desc)
        
        # 添加天气描述
        weather_desc = self.WEATHER_MAP.get(scene_desc.weather, "")
        if weather_desc:
            parts.append(weather_desc)
        
        # 添加位置细节
        if scene_desc.location_details:
            parts.append(scene_desc.location_details)
        
        # 添加背景元素
        background_elements = scene_desc.background_elements or template.get("background", [])
        if background_elements:
            parts.append(f"background with {', '.join(background_elements)}")
        
        # 添加前景元素
        if scene_desc.foreground_elements:
            parts.append(f"foreground with {', '.join(scene_desc.foreground_elements)}")
        
        # 添加氛围描述
        atmosphere = scene_desc.atmosphere or template.get("atmosphere", "")
        if atmosphere:
            parts.append(f"atmosphere: {atmosphere}")
        
        # 添加光线描述
        lighting = scene_desc.lighting_keywords or template.get("lighting", "")
        if lighting:
            parts.append(lighting)
        
        # 添加色调
        if scene_desc.color_palette:
            parts.append(f"color palette: {scene_desc.color_palette}")
        
        # 添加角色描述
        if character_descriptions:
            for char_desc in character_descriptions:
                parts.append(char_desc)
        
        # 添加产品描述
        if product_description:
            parts.append(f"product: {product_description}")
        
        return ", ".join(parts)

    def analyze_and_optimize_scene(
        self,
        raw_description: str,
    ) -> SceneDescription:
        """
        分析并优化场景描述。
        
        Args:
            raw_description: 原始场景描述
        
        Returns:
            优化后的场景描述
        """
        # 简单的关键词匹配分析
        scene_type = SceneType.INDOOR
        time_of_day = TimeOfDay.AFTERNOON
        weather = Weather.SUNNY
        
        # 判断场景类型
        if "客厅" in raw_description or "living room" in raw_description.lower():
            scene_type = SceneType.LIVING_ROOM
        elif "厨房" in raw_description or "kitchen" in raw_description.lower():
            scene_type = SceneType.KITCHEN
        elif "卧室" in raw_description or "bedroom" in raw_description.lower():
            scene_type = SceneType.BEDROOM
        elif "办公室" in raw_description or "office" in raw_description.lower():
            scene_type = SceneType.OFFICE
        elif "街道" in raw_description or "street" in raw_description.lower():
            scene_type = SceneType.STREET
        elif "公园" in raw_description or "park" in raw_description.lower():
            scene_type = SceneType.PARK
        elif "商店" in raw_description or "store" in raw_description.lower():
            scene_type = SceneType.STORE
        
        # 判断时间
        if "早晨" in raw_description or "morning" in raw_description.lower():
            time_of_day = TimeOfDay.MORNING
        elif "傍晚" in raw_description or "evening" in raw_description.lower():
            time_of_day = TimeOfDay.EVENING
        elif "夜晚" in raw_description or "night" in raw_description.lower():
            time_of_day = TimeOfDay.NIGHT
        
        # 判断天气
        if "下雨" in raw_description or "rain" in raw_description.lower():
            weather = Weather.RAINY
        elif "下雪" in raw_description or "snow" in raw_description.lower():
            weather = Weather.SNOWY
        elif "雾" in raw_description or "fog" in raw_description.lower():
            weather = Weather.FOGGY
        elif "多云" in raw_description or "cloudy" in raw_description.lower():
            weather = Weather.CLOUDY
        
        return SceneDescription(
            scene_type=scene_type,
            time_of_day=time_of_day,
            weather=weather,
            location_details=raw_description,
        )

    def generate_scene_transition(
        self,
        from_scene: SceneDescription,
        to_scene: SceneDescription,
        transition_type: str = "cut",
    ) -> Dict[str, Any]:
        """
        生成场景过渡描述。
        
        Args:
            from_scene: 原场景
            to_scene: 目标场景
            transition_type: 过渡类型
        
        Returns:
            过渡描述
        """
        transition_desc = {
            "type": transition_type,
            "from_scene": from_scene.scene_type.value,
            "to_scene": to_scene.scene_type.value,
            "transition_prompt": "",
            "color_correction": {},
        }
        
        # 根据场景差异生成过渡提示
        if from_scene.scene_type != to_scene.scene_type:
            transition_desc["transition_prompt"] = f"transition from {from_scene.scene_type.value} to {to_scene.scene_type.value}"
        
        # 颜色校正建议
        if from_scene.time_of_day != to_scene.time_of_day:
            if to_scene.time_of_day == TimeOfDay.EVENING:
                transition_desc["color_correction"] = {
                    "warmth": "+10",
                    "brightness": "-5",
                }
            elif to_scene.time_of_day == TimeOfDay.NIGHT:
                transition_desc["color_correction"] = {
                    "brightness": "-20",
                    "contrast": "+10",
                }
        
        return transition_desc

    def enhance_scene_details(
        self,
        scene_desc: SceneDescription,
        additional_elements: List[str],
    ) -> SceneDescription:
        """
        增强场景细节。
        
        Args:
            scene_desc: 场景描述
            additional_elements: 额外元素列表
        
        Returns:
            增强后的场景描述
        """
        scene_desc.background_elements.extend(additional_elements)
        return scene_desc

    def generate_environment_prompt(self, scene_desc: SceneDescription) -> str:
        """
        生成环境描述Prompt（用于Inpainting等高级功能）。
        
        Args:
            scene_desc: 场景描述
        
        Returns:
            环境描述字符串
        """
        template = self.SCENE_TEMPLATES.get(scene_desc.scene_type, {})
        
        env_desc = (
            f"environment: {template.get('description', '')}, "
            f"atmosphere: {template.get('atmosphere', '')}, "
            f"lighting: {template.get('lighting', '')}, "
        )
        
        if scene_desc.time_of_day:
            env_desc += f"{self.TIME_OF_DAY_MAP.get(scene_desc.time_of_day, '')}, "
        
        if scene_desc.weather:
            env_desc += f"{self.WEATHER_MAP.get(scene_desc.weather, '')}, "
        
        return env_desc

    # ============================================================
    # 全局场景锚点系统（Scene Anchor System）
    # ============================================================

    def create_scene_anchor(
        self,
        anchor_id: str,
        scene_desc: SceneDescription,
        reference_frames: List[str],
        anchor_type: str = "primary",
    ) -> SceneAnchor:
        """
        创建场景锚点，用于维持全局场景一致性。
        
        Args:
            anchor_id: 锚点ID
            scene_desc: 场景描述
            reference_frames: 参考帧列表（base64或文件路径）
            anchor_type: 锚点类型（primary/secondary/detail）
        
        Returns:
            SceneAnchor 对象
        """
        template = self.SCENE_TEMPLATES.get(scene_desc.scene_type, {})
        
        return SceneAnchor(
            anchor_id=anchor_id,
            anchor_type=anchor_type,
            reference_frames=reference_frames,
            scene_description=template.get("description", "") + ", " + scene_desc.location_details,
            lighting_description=template.get("lighting", "") + ", " + scene_desc.lighting_keywords,
            color_palette=scene_desc.color_palette or "",
            key_elements=scene_desc.background_elements + scene_desc.foreground_elements,
            camera_angle=scene_desc.camera_perspective,
        )

    def apply_scene_anchor_to_prompt(
        self,
        prompt: str,
        anchor: SceneAnchor,
        is_first_shot: bool = False,
    ) -> str:
        """
        将场景锚点应用到Prompt中，确保场景一致性。
        
        Args:
            prompt: 原始Prompt
            anchor: 场景锚点
            is_first_shot: 是否是第一个镜头
        
        Returns:
            增强后的Prompt
        """
        continuity_parts = []
        
        if anchor.scene_description:
            continuity_parts.append(f"same location as reference: {anchor.scene_description}")
        
        if anchor.lighting_description:
            continuity_parts.append(f"same lighting as reference: {anchor.lighting_description}")
        
        if anchor.color_palette:
            continuity_parts.append(f"same color palette: {anchor.color_palette}")
        
        if anchor.key_elements:
            continuity_parts.append(f"same key elements: {', '.join(anchor.key_elements)}")
        
        if not is_first_shot:
            continuity_parts.append("continuous shot, seamless transition from previous scene")
        
        continuity_str = ", ".join(continuity_parts)
        
        if continuity_str:
            return f"{continuity_str}, {prompt}"
        return prompt

    # ============================================================
    # 镜头衔接系统（Shot Transition System）
    # ============================================================

    def analyze_continuity_between_shots(
        self,
        from_scene: SceneDescription,
        to_scene: SceneDescription,
    ) -> ContinuityLevel:
        """
        分析两个镜头之间的连续性级别。
        
        Args:
            from_scene: 源场景
            to_scene: 目标场景
        
        Returns:
            ContinuityLevel
        """
        same_scene = from_scene.scene_type == to_scene.scene_type
        same_time = from_scene.time_of_day == to_scene.time_of_day
        same_weather = from_scene.weather == to_scene.weather
        same_elements = set(from_scene.background_elements) & set(to_scene.background_elements)
        
        if same_scene and same_time and same_weather and len(same_elements) >= 2:
            return ContinuityLevel.STRICT
        elif same_scene and same_time:
            return ContinuityLevel.MODERATE
        elif same_scene or len(same_elements) >= 1:
            return ContinuityLevel.LOOSE
        else:
            return ContinuityLevel.DISCONNECTED

    def generate_shot_transition(
        self,
        from_shot_index: int,
        to_shot_index: int,
        from_scene: SceneDescription,
        to_scene: SceneDescription,
        rhythm_intensity: float = 5.0,
    ) -> ShotTransition:
        """
        生成镜头衔接描述。
        
        Args:
            from_shot_index: 源镜头索引
            to_shot_index: 目标镜头索引
            from_scene: 源场景
            to_scene: 目标场景
            rhythm_intensity: 节奏强度（0-10）
        
        Returns:
            ShotTransition 对象
        """
        continuity_level = self.analyze_continuity_between_shots(from_scene, to_scene)
        
        transition_map = {
            ContinuityLevel.STRICT: {
                "type": "dissolve",
                "duration": 0.2,
            },
            ContinuityLevel.MODERATE: {
                "type": "slide",
                "duration": 0.3,
            },
            ContinuityLevel.LOOSE: {
                "type": "zoom",
                "duration": 0.4,
            },
            ContinuityLevel.DISCONNECTED: {
                "type": "cut",
                "duration": 0.1,
            },
        }
        
        transition_info = transition_map.get(continuity_level, transition_map[ContinuityLevel.MODERATE])
        
        duration_factor = 1.0
        if rhythm_intensity >= 8:
            duration_factor = 0.7
        elif rhythm_intensity <= 3:
            duration_factor = 1.3
        
        return ShotTransition(
            from_shot_index=from_shot_index,
            to_shot_index=to_shot_index,
            transition_type=transition_info["type"],
            duration=transition_info["duration"] * duration_factor,
            continuity_level=continuity_level,
            color_correction=self._calculate_color_correction(from_scene, to_scene),
            pacing_note=self._generate_pacing_note(continuity_level, rhythm_intensity),
        )

    def _calculate_color_correction(
        self,
        from_scene: SceneDescription,
        to_scene: SceneDescription,
    ) -> Dict[str, Any]:
        """
        计算场景转换时的颜色校正参数。
        
        Args:
            from_scene: 源场景
            to_scene: 目标场景
        
        Returns:
            颜色校正参数字典
        """
        correction = {}
        
        time_change = from_scene.time_of_day != to_scene.time_of_day
        weather_change = from_scene.weather != to_scene.weather
        
        if time_change:
            if to_scene.time_of_day == TimeOfDay.EVENING:
                correction.update({"warmth": 10, "brightness": -5})
            elif to_scene.time_of_day == TimeOfDay.NIGHT:
                correction.update({"brightness": -20, "contrast": 10})
            elif to_scene.time_of_day == TimeOfDay.MORNING:
                correction.update({"warmth": 5, "brightness": 5})
        
        if weather_change:
            if to_scene.weather == Weather.RAINY:
                correction.update({"saturation": -10, "contrast": 5})
            elif to_scene.weather == Weather.SUNNY:
                correction.update({"saturation": 5, "brightness": 5})
        
        return correction

    def _generate_pacing_note(
        self,
        continuity_level: ContinuityLevel,
        rhythm_intensity: float,
    ) -> str:
        """
        生成节奏提示。
        
        Args:
            continuity_level: 连续性级别
            rhythm_intensity: 节奏强度
        
        Returns:
            节奏提示字符串
        """
        if continuity_level == ContinuityLevel.STRICT:
            return "smooth continuous movement"
        elif continuity_level == ContinuityLevel.DISCONNECTED:
            return "sharp cut, new scene"
        elif rhythm_intensity >= 8:
            return "fast pacing, energetic transition"
        elif rhythm_intensity <= 3:
            return "slow deliberate transition"
        else:
            return "moderate pacing"

    # ============================================================
    # 场景一致性验证（Scene Consistency Validation）
    # ============================================================

    def validate_scene_consistency(
        self,
        scenes: List[SceneDescription],
        anchor: Optional[SceneAnchor] = None,
    ) -> Dict[str, Any]:
        """
        验证整个视频的场景一致性。
        
        Args:
            scenes: 场景描述列表
            anchor: 场景锚点（可选）
        
        Returns:
            验证结果字典
        """
        if not scenes:
            return {"valid": True, "issues": [], "warnings": []}
        
        issues = []
        warnings = []
        
        for i in range(1, len(scenes)):
            prev_scene = scenes[i-1]
            curr_scene = scenes[i]
            
            continuity_level = self.analyze_continuity_between_shots(prev_scene, curr_scene)
            
            if continuity_level == ContinuityLevel.DISCONNECTED:
                issues.append(
                    f"镜头 {i-1} → {i}: 场景完全不连续，建议添加转场效果"
                )
            elif continuity_level == ContinuityLevel.LOOSE:
                warnings.append(
                    f"镜头 {i-1} → {i}: 场景连续性较弱，建议检查场景元素一致性"
                )
            
            if prev_scene.time_of_day != curr_scene.time_of_day:
                warnings.append(
                    f"镜头 {i-1} → {i}: 时间发生变化（{prev_scene.time_of_day.value} → {curr_scene.time_of_day.value}）"
                )
            
            if prev_scene.weather != curr_scene.weather:
                warnings.append(
                    f"镜头 {i-1} → {i}: 天气发生变化（{prev_scene.weather.value} → {curr_scene.weather.value}）"
                )
        
        if anchor:
            for i, scene in enumerate(scenes):
                if not scene.background_elements:
                    warnings.append(
                        f"镜头 {i}: 缺少背景元素，可能导致与锚点场景不一致"
                    )
        
        return {
            "valid": len(issues) == 0,
            "issues": issues,
            "warnings": warnings,
            "total_scenes": len(scenes),
            "continuity_score": self._calculate_continuity_score(scenes),
        }

    def _calculate_continuity_score(self, scenes: List[SceneDescription]) -> float:
        """
        计算场景连续性分数（0-100）。
        
        Args:
            scenes: 场景描述列表
        
        Returns:
            连续性分数
        """
        if len(scenes) <= 1:
            return 100.0
        
        total_score = 0.0
        total_pairs = len(scenes) - 1
        
        for i in range(1, len(scenes)):
            continuity = self.analyze_continuity_between_shots(scenes[i-1], scenes[i])
            
            score_map = {
                ContinuityLevel.STRICT: 100,
                ContinuityLevel.MODERATE: 75,
                ContinuityLevel.LOOSE: 50,
                ContinuityLevel.DISCONNECTED: 25,
            }
            
            total_score += score_map.get(continuity, 50)
        
        return round(total_score / total_pairs, 2)

    def generate_continuity_prompt(
        self,
        scene_desc: SceneDescription,
        anchor: SceneAnchor,
        prev_scene_desc: Optional[SceneDescription] = None,
    ) -> str:
        """
        生成连续性增强的场景Prompt。
        
        Args:
            scene_desc: 当前场景描述
            anchor: 场景锚点
            prev_scene_desc: 前一个场景描述（可选）
        
        Returns:
            连续性增强的Prompt
        """
        prompt_parts = []
        
        prompt_parts.append(f"scene: {self.generate_scene_prompt(scene_desc)}")
        
        if anchor:
            prompt_parts.append(f"scene anchor: {anchor.scene_description}")
            prompt_parts.append(f"lighting anchor: {anchor.lighting_description}")
            if anchor.key_elements:
                prompt_parts.append(f"key elements must match: {', '.join(anchor.key_elements)}")
        
        if prev_scene_desc:
            continuity_level = self.analyze_continuity_between_shots(prev_scene_desc, scene_desc)
            if continuity_level != ContinuityLevel.DISCONNECTED:
                prompt_parts.append("continuous from previous shot, seamless scene transition")
        
        return ", ".join(prompt_parts)
