#!/usr/bin/env python3
"""
故事板生成器（Storyboard Generator）

将剧本转化为可视化分镜，支持6段式叙事结构。
参考行业最佳实践：StoryboardHero、Runway分镜工作流。

核心特点：
1. 剧本→文字分镜→可视化分镜（图片）→视频片段
2. 每段分镜包含：场景描述、镜号、画面构图、运镜、光线、时长
3. 支持故事板预览，生成前确认视觉效果
"""

import json
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from datetime import datetime

from config import setup_logger, OUTPUT_DIR
from character_analyzer import CharacterRole, CharacterType

logger = setup_logger(__name__)


@dataclass
class StoryboardShot:
    """单个分镜"""
    shot_number: int
    scene: str              # 场景名称
    description: str        # 画面描述
    camera_movement: str    # 运镜描述
    lighting: str           # 光线描述
    composition: str        # 构图描述
    duration: float         # 时长（秒）
    emotion: str            # 情绪
    key_elements: List[str] = field(default_factory=list)  # 关键元素
    reference_images: List[str] = field(default_factory=list)
    image_path: Optional[str] = None
    video_path: Optional[str] = None
    quality_score: float = 0.0
    present_characters: List[str] = field(default_factory=list)  # 本镜出现的角色ID


@dataclass
class Storyboard:
    """完整故事板"""
    title: str
    description: str
    style: str              # cinematic / realistic / commercial
    shots: List[StoryboardShot] = field(default_factory=list)
    created_at: str = ""
    total_duration: float = 0.0


class StoryboardGenerator:
    """故事板生成器主类"""

    # 场景模板库（参考用户参考图中的6段式结构）
    SCENE_TEMPLATES = {
        "family_warm": {
            "name": "家庭温馨",
            "lighting": "warm golden hour lighting, soft shadows, cozy atmosphere",
            "composition": "medium shot, rule of thirds, natural framing",
            "camera": "static or extremely slow push-in",
            "emotion": "warm, happy, peaceful",
        },
        "family_action": {
            "name": "家庭互动",
            "lighting": "bright natural lighting, even illumination",
            "composition": "wide shot, dynamic composition",
            "camera": "slow tracking shot",
            "emotion": "lively, joyful, connected",
        },
        "crisis": {
            "name": "危机出现",
            "lighting": "dramatic low-key lighting, contrast between warm interior and cold exterior",
            "composition": "medium close-up, tight framing",
            "camera": "static, slow pull-back",
            "emotion": "anxious, worried, tense",
        },
        "solution": {
            "name": "解决方案",
            "lighting": "bright hopeful lighting, warm tones with soft blue accents",
            "composition": "medium shot, center framing",
            "camera": "slow push-in on subject",
            "emotion": "relieved, hopeful, calm",
        },
        "service": {
            "name": "服务到达",
            "lighting": "professional lighting, clean and bright",
            "composition": "wide shot, symmetrical composition",
            "camera": "static or slow pan",
            "emotion": "professional, reassuring, trustworthy",
        },
        "resolution": {
            "name": "温馨回归",
            "lighting": "warm golden lighting, sunset-like glow",
            "composition": "wide shot, open framing",
            "camera": "extremely slow pull-out",
            "emotion": "happy, peaceful, content",
        },
    }

    # 运镜类型（参考用户参考图中的"极缓慢推拉"）
    CAMERA_STYLES = {
        "static": "static shot, no camera movement",
        "slow_push": "extremely slow push-in, subtle camera dollying forward",
        "slow_pull": "extremely slow pull-out, subtle camera dollying backward",
        "slow_track": "slow tracking shot, following subject movement",
        "slow_pan": "slow pan left or right",
        "slow_orbit": "slow orbit around subject",
    }

    def __init__(self):
        self.shot_id_counter = 0

    def generate_from_script(
        self,
        ad_script: Dict[str, Any],
        character_roles: Optional[List[CharacterRole]] = None,
        product_bible: Optional[Dict[str, Any]] = None,
        style: str = "cinematic",
        character_bibles: Optional[List[Dict[str, Any]]] = None,
        emotion_curve: Optional[List[dict]] = None,
    ) -> Storyboard:
        """
        从广告脚本生成完整故事板（支持多角色）。
        
        参考用户参考图的6段式结构：
        1. 傍晚客厅·家庭温馨时光
        2. 傍晚厨房·准备晚餐
        3. 夜晚客厅·暴雨漏水
        4. 夜晚客厅·电话联系
        5. 次日清晨玄关·服务到达
        6. 当日下午客厅·温馨回归
        """
        shots = []
        total_duration = 0.0

        # 定义6段式场景映射
        scene_mapping = [
            ("hook", "家庭温馨时光", "family_warm", 3.0),
            ("turning_point", "家庭互动", "family_action", 3.0),
            ("showcase", None, None, 4.0),  # 动态确定
            ("result", "解决方案", "solution", 4.0),
            ("cta", "服务验证", "service", 3.0),
            ("resolution", "温馨回归", "resolution", 3.0),
        ]

        character_roles = character_roles or []
        product_bible = product_bible or {}

        for shot_num, (script_key, scene_name, scene_type, duration) in enumerate(scene_mapping, 1):
            script_text = ad_script.get(script_key, {}).get('text', '')
            
            if script_key == "showcase":
                if "危机" in script_text or "问题" in script_text:
                    scene_type = "crisis"
                    scene_name = "危机出现"
                else:
                    scene_type = "solution"
                    scene_name = "产品展示"

            present_chars = self._get_characters_for_scene(scene_name, character_roles)
            present_char_ids = [c.role_id for c in present_chars]
            
            char_desc = self._generate_shot_character_description(present_chars)
            
            if script_key == "showcase":
                description = f"{char_desc}，{script_text}"
            elif script_key == "result":
                product_name = product_bible.get('name', '')
                description = f"{char_desc}使用{product_name}，{script_text}"
            elif script_key == "cta":
                description = f"{char_desc}，{script_text}"
            elif script_key == "resolution":
                selling_point = product_bible.get('key_selling_point', '')
                description = f"{char_desc}恢复正常生活，{selling_point}"
            else:
                description = f"{char_desc}，{script_text}"

            composition = self._adjust_composition_for_characters(
                self.SCENE_TEMPLATES[scene_type]["composition"],
                len(present_chars)
            )

            shot = StoryboardShot(
                shot_number=shot_num,
                scene=scene_name,
                description=description,
                camera_movement=self.CAMERA_STYLES["static"] if scene_type in ["family_warm", "service"] else self.CAMERA_STYLES["slow_track"],
                lighting=self.SCENE_TEMPLATES[scene_type]["lighting"],
                composition=composition,
                duration=duration,
                emotion=self.SCENE_TEMPLATES[scene_type]["emotion"],
                key_elements=self._get_key_elements(scene_type, product_bible),
                present_characters=present_char_ids,
            )
            shots.append(shot)
            total_duration += duration

        storyboard = Storyboard(
            title=ad_script.get("title", "Untitled"),
            description=ad_script.get("description", ""),
            style=style,
            shots=shots,
            created_at=datetime.now().isoformat(),
            total_duration=total_duration,
        )

        logger.info(f"📋 故事板生成完成：{len(shots)}个分镜，{len(character_roles)}个角色，总时长{total_duration:.1f}秒")
        return storyboard

    def _get_characters_for_scene(self, scene_name: str, all_characters: Optional[List[CharacterRole]]) -> List[CharacterRole]:
        """获取特定场景应该出现的角色"""
        present = []
        if not all_characters:
            return present
        
        for char in all_characters:
            # 主角始终出现
            if char.character_type == CharacterType.PROTAGONIST:
                present.append(char)
                continue
            
            # 背景人物仅在特定场景
            if char.character_type == CharacterType.BACKGROUND:
                if scene_name in ["家庭温馨时光", "家庭互动", "温馨回归"]:
                    present.append(char)
                continue
            
            # 服务人员仅在服务场景
            if char.character_type == CharacterType.SERVICE:
                if scene_name in ["服务验证", "服务到达"]:
                    present.append(char)
                continue
            
            # 配角根据required_in_scenes判断
            if char.required_in_scenes:
                if scene_name in char.required_in_scenes or any(s in scene_name for s in char.required_in_scenes):
                    present.append(char)
            else:
                # 默认配角在家庭场景出现
                if scene_name in ["家庭温馨时光", "家庭互动", "温馨回归", "危机出现"]:
                    present.append(char)
        
        return present

    def _generate_shot_character_description(self, characters: List[CharacterRole]) -> str:
        """生成分镜角色描述"""
        if not characters:
            return ""
        
        descriptions = []
        for char in characters:
            if char.character_type == CharacterType.BACKGROUND:
                descriptions.append("背景中有模糊的行人")
            elif char.character_type == CharacterType.SERVICE:
                descriptions.append(f"{char.name}（{char.description}）穿着制服")
            else:
                descriptions.append(f"{char.name}（{char.description}）")
        
        return "，".join(descriptions)

    def _adjust_composition_for_characters(self, base_composition: str, num_characters: int) -> str:
        """根据角色数量调整构图"""
        if num_characters <= 1:
            return base_composition
        elif num_characters == 2:
            return f"two-shot, {base_composition}, both characters in frame"
        elif num_characters == 3:
            return f"medium wide shot, {base_composition}, three characters arranged in triangular composition"
        else:
            return f"wide shot, {base_composition}, group of {num_characters} people, layered depth"

    def _get_key_elements(self, scene_type: str, product_bible: Dict[str, Any]) -> List[str]:
        """获取关键元素"""
        elements_map = {
            "family_warm": ["family", "living room", "warm atmosphere"],
            "family_action": ["family", "kitchen", "cooking"],
            "crisis": ["problem", "concern", product_bible.get("name", "")],
            "solution": [product_bible.get("name", ""), "solution", "relief"],
            "service": ["service", "professional", "trust"],
            "resolution": ["happy ending", "peaceful", product_bible.get("name", "")],
        }
        return elements_map.get(scene_type, ["scene"])

    def generate_prompt_for_shot(
        self,
        shot: StoryboardShot,
        character_bibles: List[Dict[str, Any]],
        product_bible: Dict[str, Any],
        reference_images: List[str] = None,
    ) -> str:
        """
        为单个分镜生成详细的视频生成Prompt。
        
        参考行业最佳实践：
        - 包含所有视觉元素的详细描述
        - 指定运镜、光线、构图
        - 添加一致性约束（same characters from reference）
        """
        # 构建角色描述
        character_prompts = []
        for bible in character_bibles:
            char_prompt = f"{bible.get('name', '')}: {bible.get('age', '')}岁{bible.get('gender', '')}，{bible.get('hair_style', '')}，{bible.get('hair_color', '')}发色，穿着{bible.get('outfit', '')}"
            character_prompts.append(char_prompt)

        characters_str = "; ".join(character_prompts)

        # 构建产品描述
        product_name = product_bible.get("name", "")
        product_desc = f"{product_bible.get('packaging', '')}，{product_bible.get('primary_color', '')}，{product_bible.get('shape', '')}"

        # 构建基础Prompt
        prompt = (
            f"Cinematic film scene, {shot.scene}, "
            f"{shot.description}, "
            f"Characters: {characters_str}, "
        )

        # 添加产品（如果是showcase段）
        if shot.shot_number in [3, 4]:
            prompt += f"Product: {product_name} ({product_desc}), "

        # 添加视觉风格
        prompt += (
            f"{shot.lighting}, "
            f"{shot.composition}, "
            f"{shot.camera_movement}, "
            f"emotion: {shot.emotion}, "
        )

        # 添加质量词
        prompt += (
            "sharp focus, highly detailed, professional cinematography, "
            "film grain, cinematic color grading, realistic texture, "
        )

        # 添加一致性约束
        if reference_images:
            prompt += "same characters and style as reference images, maintain character consistency across scenes, "

        # 添加负面提示词
        negative = (
            "blurry, low quality, distorted faces, disfigured, "
            "bad anatomy, extra limbs, text, watermark, logo, "
            "cartoon, anime, illustration, 2d art"
        )

        return {
            "prompt": prompt.strip(),
            "negative_prompt": negative,
            "duration": shot.duration,
            "shot_number": shot.shot_number,
            "scene": shot.scene,
        }

    def export_storyboard(self, storyboard: Storyboard, output_path: Path) -> Path:
        """导出故事板为JSON格式"""
        data = {
            "title": storyboard.title,
            "description": storyboard.description,
            "style": storyboard.style,
            "total_duration": storyboard.total_duration,
            "created_at": storyboard.created_at,
            "shots": [
                {
                    "shot_number": s.shot_number,
                    "scene": s.scene,
                    "description": s.description,
                    "camera_movement": s.camera_movement,
                    "lighting": s.lighting,
                    "composition": s.composition,
                    "duration": s.duration,
                    "emotion": s.emotion,
                    "key_elements": s.key_elements,
                    "image_path": s.image_path,
                    "video_path": s.video_path,
                    "quality_score": s.quality_score,
                }
                for s in storyboard.shots
            ],
        }

        output_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(f"📝 故事板已导出：{output_path}")
        return output_path

    def print_storyboard(self, storyboard: Storyboard):
        """打印故事板摘要"""
        print(f"\n📋 {storyboard.title}")
        print(f"风格: {storyboard.style}")
        print(f"总时长: {storyboard.total_duration:.1f}秒")
        print("-" * 60)

        for shot in storyboard.shots:
            print(f"\n🎬 分镜 {shot.shot_number}: {shot.scene}")
            print(f"   描述: {shot.description[:50]}...")
            print(f"   运镜: {shot.camera_movement[:30]}...")
            print(f"   光线: {shot.lighting[:30]}...")
            print(f"   情绪: {shot.emotion}")
            print(f"   时长: {shot.duration:.1f}秒")
            print(f"   关键元素: {', '.join(shot.key_elements)}")

        print("\n" + "-" * 60)
