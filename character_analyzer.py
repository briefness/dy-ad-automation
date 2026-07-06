#!/usr/bin/env python3
"""
角色分析器（Character Analyzer）

根据产品和场景动态分析需要的角色数量和类型。
解决"每次只有一个角色"的问题。

角色分级：
- 主角（Protagonist）：核心人物，必须高度一致
- 配角（Supporting）：家人/朋友/同事，需要一致性但要求稍低
- 路人（Background）：背景人物，不需要一致性，增加真实感
- 服务人员（Service）：快递员/客服/维修人员等
"""

from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from enum import Enum


class CharacterType(Enum):
    """角色类型"""
    PROTAGONIST = "protagonist"      # 主角
    SUPPORTING = "supporting"        # 配角（家人/朋友）
    SERVICE = "service"              # 服务人员
    BACKGROUND = "background"        # 背景路人


@dataclass
class CharacterRole:
    """角色角色定义"""
    role_id: str
    name: str
    character_type: CharacterType
    description: str
    gender: str
    age_range: str
    relationship_to_protagonist: str
    appearance_requirements: str
    consistency_level: float  # 0-1，一致性要求
    required_in_scenes: List[str] = field(default_factory=list)


class CharacterAnalyzer:
    """角色分析器"""

    # 品类-场景-角色映射表
    SCENE_CHARACTER_MAP = {
        "家庭温馨": {
            "min_characters": 2,
            "max_characters": 4,
            "roles": [
                {"type": CharacterType.PROTAGONIST, "name": "妈妈", "gender": "女性", "age": "28-35"},
                {"type": CharacterType.SUPPORTING, "name": "爸爸", "gender": "男性", "age": "30-40"},
                {"type": CharacterType.SUPPORTING, "name": "孩子", "gender": "不限", "age": "5-10", "optional": True},
                {"type": CharacterType.BACKGROUND, "name": "宠物", "gender": "不限", "age": "不限", "optional": True},
            ]
        },
        "家庭互动": {
            "min_characters": 2,
            "max_characters": 5,
            "roles": [
                {"type": CharacterType.PROTAGONIST, "name": "主角", "gender": "不限", "age": "25-40"},
                {"type": CharacterType.SUPPORTING, "name": "家人", "gender": "不限", "age": "不限"},
                {"type": CharacterType.SUPPORTING, "name": "朋友", "gender": "不限", "age": "25-40", "optional": True},
            ]
        },
        "危机出现": {
            "min_characters": 1,
            "max_characters": 3,
            "roles": [
                {"type": CharacterType.PROTAGONIST, "name": "主角", "gender": "不限", "age": "25-40"},
                {"type": CharacterType.SUPPORTING, "name": "家人", "gender": "不限", "age": "不限", "optional": True},
            ]
        },
        "解决方案": {
            "min_characters": 1,
            "max_characters": 3,
            "roles": [
                {"type": CharacterType.PROTAGONIST, "name": "主角", "gender": "不限", "age": "25-40"},
                {"type": CharacterType.SUPPORTING, "name": "家人", "gender": "不限", "age": "不限", "optional": True},
            ]
        },
        "服务到达": {
            "min_characters": 2,
            "max_characters": 3,
            "roles": [
                {"type": CharacterType.PROTAGONIST, "name": "客户", "gender": "不限", "age": "25-50"},
                {"type": CharacterType.SERVICE, "name": "服务人员", "gender": "不限", "age": "25-45"},
            ]
        },
        "温馨回归": {
            "min_characters": 2,
            "max_characters": 5,
            "roles": [
                {"type": CharacterType.PROTAGONIST, "name": "主角", "gender": "不限", "age": "25-40"},
                {"type": CharacterType.SUPPORTING, "name": "家人", "gender": "不限", "age": "不限"},
                {"type": CharacterType.SUPPORTING, "name": "孩子", "gender": "不限", "age": "5-12", "optional": True},
            ]
        },
    }

    # 品类-默认角色配置
    CATEGORY_CHARACTER_DEFAULTS = {
        "美妆": {
            "protagonist": {"gender": "女性", "age": "22-35", "role": "爱美女性"},
            "supporting": [
                {"gender": "女性", "age": "22-35", "role": "闺蜜", "scene": "分享推荐"},
            ],
            "background": True,
        },
        "食品": {
            "protagonist": {"gender": "不限", "age": "25-45", "role": "家庭主妇/上班族"},
            "supporting": [
                {"gender": "不限", "age": "5-15", "role": "孩子", "scene": "家庭用餐"},
                {"gender": "不限", "age": "25-45", "role": "配偶", "scene": "家庭用餐"},
            ],
            "background": True,
        },
        "家居": {
            "protagonist": {"gender": "不限", "age": "28-45", "role": " homeowner"},
            "supporting": [
                {"gender": "不限", "age": "25-45", "role": "配偶", "scene": "家庭决策"},
                {"gender": "不限", "age": "5-15", "role": "孩子", "scene": "家庭互动"},
            ],
            "background": False,
        },
        "保险": {
            "protagonist": {"gender": "不限", "age": "28-45", "role": "家庭支柱"},
            "supporting": [
                {"gender": "不限", "age": "25-45", "role": "配偶", "scene": "家庭温馨"},
                {"gender": "不限", "age": "5-15", "role": "孩子", "scene": "家庭温馨"},
            ],
            "service": [
                {"gender": "不限", "age": "25-40", "role": "保险顾问", "scene": "服务到达"},
            ],
            "background": True,
        },
        "数码": {
            "protagonist": {"gender": "不限", "age": "20-35", "role": "科技爱好者"},
            "supporting": [
                {"gender": "不限", "age": "20-35", "role": "朋友", "scene": "分享体验"},
            ],
            "background": True,
        },
    }

    def analyze_characters_needed(
        self,
        product_category: str,
        story_scenes: List[str],
        target_audience: Optional[str] = None,
    ) -> List[CharacterRole]:
        """
        分析需要的角色列表。
        
        Args:
            product_category: 产品品类
            story_scenes: 故事场景列表
            target_audience: 目标人群
        
        Returns:
            List[CharacterRole]
        """
        characters = []
        char_index = 0

        # 获取品类默认配置
        category_config = self.CATEGORY_CHARACTER_DEFAULTS.get(product_category, {})

        # 1. 创建主角
        protagonist_config = category_config.get("protagonist", {"gender": "不限", "age": "25-40", "role": "主角"})
        protagonist = CharacterRole(
            role_id=f"char_{char_index:02d}",
            name="主角",
            character_type=CharacterType.PROTAGONIST,
            description=f"{protagonist_config['role']}，{protagonist_config['gender']}，{protagonist_config['age']}岁",
            gender=protagonist_config["gender"],
            age_range=protagonist_config["age"],
            relationship_to_protagonist="self",
            appearance_requirements="核心人物，必须在所有场景中出现，要求高度一致性",
            consistency_level=0.95,
            required_in_scenes=story_scenes,
        )
        characters.append(protagonist)
        char_index += 1

        # 2. 创建配角（根据场景决定）
        supporting_configs = category_config.get("supporting", [])
        for i, config in enumerate(supporting_configs):
            # 判断该配角是否需要在故事中
            if config.get("scene") in story_scenes or self._is_family_scene(story_scenes):
                supporting = CharacterRole(
                    role_id=f"char_{char_index:02d}",
                    name=config.get("role", f"配角{i+1}"),
                    character_type=CharacterType.SUPPORTING,
                    description=f"{config.get('role', '配角')}，{config['gender']}，{config['age']}岁",
                    gender=config["gender"],
                    age_range=config["age"],
                    relationship_to_protagonist=config.get("role", "配角"),
                    appearance_requirements="重要配角，需要在多个场景中出现，要求较高一致性",
                    consistency_level=0.85,
                    required_in_scenes=self._get_relevant_scenes(config.get("scene", ""), story_scenes),
                )
                characters.append(supporting)
                char_index += 1

        # 3. 创建服务人员（如果有服务场景）
        if "服务到达" in story_scenes or "service" in [s.lower() for s in story_scenes]:
            service_configs = category_config.get("service", [])
            for i, config in enumerate(service_configs):
                service = CharacterRole(
                    role_id=f"char_{char_index:02d}",
                    name=config.get("role", "服务人员"),
                    character_type=CharacterType.SERVICE,
                    description=f"{config.get('role', '服务人员')}，{config['gender']}，{config['age']}岁，穿着统一制服",
                    gender=config["gender"],
                    age_range=config["age"],
                    relationship_to_protagonist="服务提供者",
                    appearance_requirements="服务人员，仅在服务场景出现，需要专业形象一致性",
                    consistency_level=0.75,
                    required_in_scenes=["服务到达"],
                )
                characters.append(service)
                char_index += 1

        # 4. 背景路人（增加真实感）
        if category_config.get("background", False):
            # 在特定场景添加背景人物
            background_scenes = ["家庭温馨", "家庭互动", "温馨回归"]
            for scene in story_scenes:
                if scene in background_scenes:
                    background = CharacterRole(
                        role_id=f"char_{char_index:02d}",
                        name="背景人物",
                        character_type=CharacterType.BACKGROUND,
                        description="背景路人，增加场景真实感",
                        gender="不限",
                        age_range="20-50",
                        relationship_to_protagonist="无",
                        appearance_requirements="背景人物，不需要一致性，不同场景可以不同",
                        consistency_level=0.0,
                        required_in_scenes=[scene],
                    )
                    characters.append(background)
                    char_index += 1
                    break  # 只添加一个背景角色类型

        return characters

    def _is_family_scene(self, scenes: List[str]) -> bool:
        """判断是否包含家庭场景"""
        family_keywords = ["家庭", "温馨", "互动", "回归", "用餐", "客厅"]
        for scene in scenes:
            for keyword in family_keywords:
                if keyword in scene:
                    return True
        return False

    def _get_relevant_scenes(self, role_scene: str, all_scenes: List[str]) -> List[str]:
        """获取角色相关的场景"""
        if not role_scene:
            return all_scenes
        
        # 如果角色场景在故事场景中，返回该场景
        if role_scene in all_scenes:
            return [role_scene]
        
        # 否则返回所有场景
        return all_scenes

    def generate_character_bibles(
        self,
        character_roles: List[CharacterRole],
        product_category: str,
    ) -> List[Dict[str, Any]]:
        """
        根据角色角色生成角色圣经。
        
        Args:
            character_roles: 角色角色列表
            product_category: 产品品类
        
        Returns:
            List[Dict] 角色圣经列表
        """
        bibles = []
        
        for role in character_roles:
            if role.character_type == CharacterType.BACKGROUND:
                # 背景人物不需要详细的圣经
                continue

            bible = {
                "id": role.role_id,
                "name": role.name,
                "age": role.age_range.split("-")[0] if "-" in role.age_range else role.age_range,
                "gender": role.gender if role.gender != "不限" else "女性" if role.character_type == CharacterType.PROTAGONIST and product_category in ["美妆"] else "男性",
                "hair_style": self._get_default_hair(role.gender, role.age_range),
                "hair_color": "深棕色" if role.character_type == CharacterType.PROTAGONIST else "黑色",
                "outfit": self._get_default_outfit(role.character_type, product_category),
                "facial_features": "五官端正" if role.character_type == CharacterType.PROTAGONIST else "自然",
                "expression_baseline": "自然微笑" if role.character_type == CharacterType.PROTAGONIST else "平和",
                "consistency_level": role.consistency_level,
                "character_type": role.character_type.value,
                "relationship": role.relationship_to_protagonist,
            }
            bibles.append(bible)

        return bibles

    def _get_default_hair(self, gender: str, age_range: str) -> str:
        """获取默认发型"""
        if gender == "女性":
            if "20-30" in age_range or "22-35" in age_range:
                return "中长发，自然垂落"
            else:
                return "短发或中长发"
        elif gender == "男性":
            return "短发，整齐"
        else:
            return "中长发"

    def _get_default_outfit(self, char_type: CharacterType, category: str) -> str:
        """获取默认服装"""
        if char_type == CharacterType.SERVICE:
            return "统一制服，整洁专业"
        elif char_type == CharacterType.PROTAGONIST:
            if category == "美妆":
                return "时尚休闲装"
            elif category == "食品":
                return "家居服或休闲装"
            elif category == "保险":
                return "商务休闲装"
            else:
                return "日常休闲装"
        else:
            return "休闲装"

    def get_shot_character_description(
        self,
        shot_scene: str,
        characters: List[CharacterRole],
    ) -> str:
        """
        为特定分镜生成角色描述。
        
        Args:
            shot_scene: 分镜场景
            characters: 角色列表
        
        Returns:
            角色描述字符串
        """
        present_chars = [c for c in characters if shot_scene in c.required_in_scenes or not c.required_in_scenes]
        
        if not present_chars:
            return ""

        descriptions = []
        for char in present_chars:
            if char.character_type == CharacterType.BACKGROUND:
                descriptions.append(f"背景中有模糊的行人")
            elif char.character_type == CharacterType.SERVICE:
                descriptions.append(f"{char.name}穿着制服")
            else:
                descriptions.append(f"{char.name}（{char.description}）")

        return "，".join(descriptions)
