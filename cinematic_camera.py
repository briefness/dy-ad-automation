#!/usr/bin/env python3
"""
电影级运镜系统（Cinematic Camera System）

参考用户参考图中的"静态·极缓慢推拉"风格。

核心特点：
1. 支持电影级运镜类型：静态、极缓慢推拉、缓慢跟拍、缓慢摇镜
2. 基于情绪曲线自动选择运镜
3. 支持镜头语言参数化控制
4. 提供专业的运镜描述词汇
"""

from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field


@dataclass
class CameraMovement:
    """运镜定义"""
    name: str
    description: str
    english_description: str
    intensity: float  # 0-1，运镜强度
    emotion: str  # 适合的情绪
    scene_type: List[str]  # 适合的场景类型
    duration_range: tuple  # 适合的时长范围（秒）


@dataclass
class CinematicShot:
    """电影级镜头"""
    shot_type: str  # extreme_close_up, close_up, medium, medium_long, wide, extreme_wide
    camera_movement: CameraMovement
    framing: str  # rule_of_thirds, center, leading_lines, framing
    depth_of_field: str  # shallow, medium, deep
    camera_angle: str  # eye_level, low_angle, high_angle, dutch
    lighting_style: str
    duration: float
    emotion: str


class CinematicCameraSystem:
    """电影级运镜系统主类"""

    # 运镜类型库（参考用户参考图中的"极缓慢推拉"）
    CAMERA_MOVEMENTS = {
        "static": CameraMovement(
            name="静态镜头",
            description="静止不动，让观众沉浸在画面中",
            english_description="static shot, no camera movement",
            intensity=0.0,
            emotion="calm, peaceful, contemplative",
            scene_type=["family_warm", "service", "resolution"],
            duration_range=(2, 5),
        ),
        "extremely_slow_push": CameraMovement(
            name="极缓慢推进",
            description="极其缓慢地向主体推进，营造逐渐增强的情感",
            english_description="extremely slow push-in, subtle camera dollying forward at 0.5x speed",
            intensity=0.1,
            emotion="intimate, gradual, emotional build",
            scene_type=["family_warm", "solution", "result"],
            duration_range=(3, 6),
        ),
        "extremely_slow_pull": CameraMovement(
            name="极缓慢拉远",
            description="极其缓慢地拉远，揭示更大的场景或情感",
            english_description="extremely slow pull-out, subtle camera dollying backward at 0.5x speed",
            intensity=0.1,
            emotion="revealing, expansive, emotional release",
            scene_type=["crisis", "resolution", "cta"],
            duration_range=(3, 6),
        ),
        "slow_track": CameraMovement(
            name="缓慢跟拍",
            description="缓慢跟随主体移动，保持稳定的视角",
            english_description="slow tracking shot, following subject movement smoothly",
            intensity=0.2,
            emotion="dynamic, flowing, observational",
            scene_type=["family_action", "turning_point"],
            duration_range=(3, 5),
        ),
        "slow_pan": CameraMovement(
            name="缓慢摇镜",
            description="缓慢水平移动镜头，展示场景",
            english_description="slow pan left or right, revealing scene details",
            intensity=0.15,
            emotion="exploratory, revealing, gentle",
            scene_type=["family_warm", "solution"],
            duration_range=(3, 5),
        ),
        "slow_orbit": CameraMovement(
            name="缓慢环绕",
            description="缓慢环绕主体移动，展示多角度",
            english_description="slow orbit around subject, showing multiple angles",
            intensity=0.25,
            emotion="immersive, comprehensive, cinematic",
            scene_type=["showcase", "product"],
            duration_range=(4, 6),
        ),
    }

    # 镜头类型库
    SHOT_TYPES = {
        "extreme_close_up": {
            "description": "极端特写，聚焦细节",
            "english": "extreme close-up (ECU)",
            "use_case": "展示产品细节、人物表情",
        },
        "close_up": {
            "description": "特写，展现人物面部",
            "english": "close-up (CU)",
            "use_case": "人物表情、产品展示",
        },
        "medium_close_up": {
            "description": "中特写，胸部以上",
            "english": "medium close-up (MCU)",
            "use_case": "人物对话、情感表达",
        },
        "medium": {
            "description": "中景，腰部以上",
            "english": "medium shot (MS)",
            "use_case": "人物互动、产品使用",
        },
        "medium_long": {
            "description": "中远景，膝盖以上",
            "english": "medium long shot (MLS)",
            "use_case": "人物全身动作",
        },
        "wide": {
            "description": "远景，展示环境",
            "english": "wide shot (WS)",
            "use_case": "场景建立、全景展示",
        },
        "extreme_wide": {
            "description": "极端远景，宏大场面",
            "english": "extreme wide shot (EWS)",
            "use_case": "开场、结尾",
        },
    }

    # 构图类型
    FRAMING_TYPES = {
        "rule_of_thirds": "rule of thirds composition",
        "center": "center framing",
        "leading_lines": "leading lines composition",
        "symmetry": "symmetrical composition",
        "frame_within_frame": "frame within frame composition",
        "negative_space": "negative space composition",
    }

    # 景深类型
    DEPTH_OF_FIELD = {
        "shallow": "shallow depth of field, bokeh background",
        "medium": "medium depth of field",
        "deep": "deep depth of field, everything in focus",
    }

    # 拍摄角度
    CAMERA_ANGLES = {
        "eye_level": "eye-level shot",
        "low_angle": "low angle shot, looking up",
        "high_angle": "high angle shot, looking down",
        "dutch": "dutch angle, tilted horizon",
    }

    # 光线风格（参考用户参考图中的"真实温情感商业摄影"）
    LIGHTING_STYLES = {
        "warm_cinematic": {
            "name": "温暖电影光",
            "description": "warm golden hour lighting, soft shadows, cinematic glow",
        },
        "soft_natural": {
            "name": "柔和自然光",
            "description": "soft natural lighting, diffused, even illumination",
        },
        "dramatic_low_key": {
            "name": "戏剧性低光",
            "description": "dramatic low-key lighting, high contrast, moody atmosphere",
        },
        "professional_studio": {
            "name": "专业棚拍",
            "description": "professional studio lighting, clean, bright, even",
        },
        "naturalistic": {
            "name": "自然主义",
            "description": "naturalistic lighting, realistic, everyday feel",
        },
    }

    def __init__(self):
        pass

    def select_camera_movement(
        self,
        scene_type: str,
        emotion: str,
        duration: float,
    ) -> CameraMovement:
        """
        根据场景类型、情绪和时长自动选择运镜。
        
        参考用户参考图中的运镜风格：静态 + 极缓慢推拉
        """
        # 筛选适合的运镜
        candidates = []
        for movement in self.CAMERA_MOVEMENTS.values():
            if scene_type in movement.scene_type:
                if movement.duration_range[0] <= duration <= movement.duration_range[1]:
                    candidates.append(movement)

        if not candidates:
            candidates = list(self.CAMERA_MOVEMENTS.values())

        # 根据情绪匹配度排序
        emotion_keywords = emotion.lower().split()
        scored = []
        for m in candidates:
            m_emotion_keywords = m.emotion.lower().split(", ")
            match_count = sum(1 for kw in emotion_keywords if kw in m_emotion_keywords)
            scored.append((m, match_count))

        scored.sort(key=lambda x: x[1], reverse=True)

        # 默认优先选择静态或极缓慢运镜（符合用户参考图风格）
        if scored[0][1] == 0:
            if duration <= 3:
                return self.CAMERA_MOVEMENTS["static"]
            else:
                return self.CAMERA_MOVEMENTS["extremely_slow_push"]

        return scored[0][0]

    def select_shot_type(
        self,
        scene_type: str,
        narrative_position: int,
        duration: float,
        num_characters: int = 1,
    ) -> str:
        """
        根据场景类型、叙事位置和角色数量选择镜头类型。
        
        参考用户参考图中的6段式结构：
        1. 广角（建立场景）
        2. 中景（人物互动）
        3. 特写/中特写（危机/产品）
        4. 特写/中特写（解决方案）
        5. 中景/广角（服务）
        6. 广角（温馨回归）
        
        多角色调整：
        - 2个角色：优先使用 two-shot (medium)
        - 3+个角色：使用 wide shot 或 medium_long
        """
        # 基础镜头类型映射
        shot_type_map = {
            1: "wide",              # 建立场景
            2: "medium",            # 人物互动
            3: "close_up",          # 危机/产品特写
            4: "close_up",          # 解决方案特写
            5: "medium_long",       # 服务人员
            6: "wide",              # 温馨回归
        }
        
        base_shot = shot_type_map.get(narrative_position, "medium")
        
        # 根据角色数量调整
        if num_characters >= 4:
            # 4人以上必须用广角
            return "wide"
        elif num_characters == 3:
            # 3人用中远景
            if base_shot in ["close_up", "extreme_close_up"]:
                return "medium_long"
            return "medium_long"
        elif num_characters == 2:
            # 2人用双人中景
            if base_shot in ["close_up", "extreme_close_up"]:
                return "medium"
            return base_shot
        
        return base_shot

    def select_lighting(
        self,
        scene_type: str,
        emotion: str,
    ) -> str:
        """
        根据场景类型和情绪选择光线风格。
        
        参考用户参考图中的"真实温情感商业摄影"风格。
        """
        lighting_map = {
            "family_warm": "warm_cinematic",
            "family_action": "soft_natural",
            "crisis": "dramatic_low_key",
            "solution": "warm_cinematic",
            "service": "professional_studio",
            "resolution": "warm_cinematic",
        }

        return lighting_map.get(scene_type, "warm_cinematic")

    def generate_cinematic_prompt(
        self,
        scene_type: str,
        emotion: str,
        duration: float,
        narrative_position: int,
        num_characters: int = 1,
        include_camera: bool = True,
        include_lighting: bool = True,
        include_composition: bool = True,
    ) -> Dict[str, str]:
        """
        生成电影级运镜描述Prompt（支持多角色）。
        
        参考行业最佳实践：
        - 使用专业的电影术语
        - 详细描述运镜、光线、构图
        - 添加情绪导向的描述
        - 根据角色数量调整镜头类型
        """
        # 选择运镜
        movement = self.select_camera_movement(scene_type, emotion, duration)

        # 选择镜头类型（考虑角色数量）
        shot_type = self.select_shot_type(scene_type, narrative_position, duration, num_characters)
        shot_type_desc = self.SHOT_TYPES[shot_type]["english"]
        
        # 多角色时添加描述
        if num_characters == 2:
            shot_type_desc += ", two-shot framing both characters"
        elif num_characters == 3:
            shot_type_desc += ", three characters in triangular composition"
        elif num_characters >= 4:
            shot_type_desc += f", group of {num_characters} people in layered depth"

        # 选择光线
        lighting_key = self.select_lighting(scene_type, emotion)
        lighting_desc = self.LIGHTING_STYLES[lighting_key]["description"]

        # 选择构图（基于镜头类型）
        if shot_type in ["close_up", "extreme_close_up"]:
            framing = self.FRAMING_TYPES["center"]
            dof = self.DEPTH_OF_FIELD["shallow"]
        elif shot_type in ["medium", "medium_close_up"]:
            framing = self.FRAMING_TYPES["rule_of_thirds"]
            dof = self.DEPTH_OF_FIELD["medium"]
        else:
            framing = self.FRAMING_TYPES["rule_of_thirds"]
            dof = self.DEPTH_OF_FIELD["deep"]

        # 构建运镜描述
        camera_parts = []
        if include_camera:
            camera_parts.append(f"{shot_type_desc}")
            camera_parts.append(f"{movement.english_description}")

        # 构建光线描述
        lighting_parts = []
        if include_lighting:
            lighting_parts.append(f"{lighting_desc}")

        # 构建构图描述
        composition_parts = []
        if include_composition:
            composition_parts.append(f"{framing}")
            composition_parts.append(f"{dof}")

        return {
            "camera": ", ".join(camera_parts),
            "lighting": ", ".join(lighting_parts),
            "composition": ", ".join(composition_parts),
            "full": ", ".join(camera_parts + lighting_parts + composition_parts),
            "movement_name": movement.name,
            "shot_type": shot_type,
            "lighting_style": lighting_key,
        }

    def generate_emotion_curve(self, num_shots: int = 6) -> List[str]:
        """
        生成情绪曲线。
        
        参考用户参考图中的情绪起伏：
        1. 平静（温馨）→ 2. 平静（互动）→ 3. 紧张（危机）→ 
        4. 缓解（解决）→ 5. 平静（服务）→ 6. 喜悦（回归）
        """
        emotion_curve = [
            "warm peaceful content",    # 1. 温馨时光
            "lively joyful connected",  # 2. 准备晚餐
            "anxious worried tense",    # 3. 危机出现
            "relieved hopeful calm",    # 4. 解决方案
            "professional reassuring",  # 5. 服务到达
            "happy peaceful content",   # 6. 温馨回归
        ]

        return emotion_curve[:num_shots]

    def get_camera_preset(self, preset_name: str) -> Dict[str, Any]:
        """获取预设的运镜风格"""
        presets = {
            "cinematic_warm": {
                "movement": "extremely_slow_push",
                "shot_type": "medium",
                "lighting": "warm_cinematic",
                "framing": "rule_of_thirds",
                "dof": "shallow",
            },
            "cinematic_dramatic": {
                "movement": "extremely_slow_pull",
                "shot_type": "close_up",
                "lighting": "dramatic_low_key",
                "framing": "center",
                "dof": "shallow",
            },
            "naturalistic": {
                "movement": "slow_track",
                "shot_type": "medium_long",
                "lighting": "soft_natural",
                "framing": "rule_of_thirds",
                "dof": "deep",
            },
            "commercial_product": {
                "movement": "slow_orbit",
                "shot_type": "close_up",
                "lighting": "professional_studio",
                "framing": "center",
                "dof": "shallow",
            },
        }

        return presets.get(preset_name, presets["cinematic_warm"])
