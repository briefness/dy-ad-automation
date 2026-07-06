#!/usr/bin/env python3
"""
多人物一致性管理器（Multi-Character Manager）

参考行业最佳实践：
- Pixverse: Multi-Reference Character Consistency
- Google DeepMind: character consistency across scenes

核心特点：
1. 支持多人角色（全家、团队等）
2. 每人独立的参考图和圣经
3. 跨场景角色一致性检测
4. 自动生成全家福参考图
"""

import json
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from config import setup_logger, OUTPUT_DIR

logger = setup_logger(__name__)


@dataclass
class CharacterReference:
    """角色参考"""
    character_id: str
    name: str
    image_path: Path
    character_type: str = "protagonist"  # protagonist/supporting/service/background
    consistency_level: float = 0.95
    thumbnail_path: Optional[Path] = None
    embedding: Optional[List[float]] = None
    quality_score: float = 0.0
    consistency_score: float = 0.0


@dataclass
class CharacterGroup:
    """角色组（全家/团队）"""
    group_id: str
    name: str
    characters: List[CharacterReference] = field(default_factory=list)
    group_reference_image: Optional[Path] = None  # 全家福参考图
    created_at: str = ""
    total_usage: int = 0


class MultiCharacterManager:
    """多人物一致性管理器主类"""

    def __init__(self):
        self.groups: Dict[str, CharacterGroup] = {}

    # 角色类型-一致性阈值映射
    CONSISTENCY_THRESHOLDS = {
        "protagonist": 0.95,    # 主角：最高一致性
        "supporting": 0.85,     # 配角：较高一致性
        "service": 0.75,        # 服务人员：中等一致性
        "background": 0.0,      # 背景：不检查一致性
    }

    def create_character_group(
        self,
        group_name: str,
        character_bibles: List[Dict[str, Any]],
        image_paths: List[Path],
    ) -> CharacterGroup:
        """
        创建角色组（支持角色分级）。
        
        Args:
            group_name: 角色组名称（如"幸福家庭"）
            character_bibles: 角色圣经列表（包含character_type和consistency_level）
            image_paths: 角色参考图路径列表
        
        Returns:
            CharacterGroup
        """
        group_id = f"group_{hash(group_name) % 10000:04d}"

        # 创建角色参考列表
        characters = []
        for i, (bible, img_path) in enumerate(zip(character_bibles, image_paths)):
            if not img_path.exists():
                logger.warning(f"角色图片不存在：{img_path}")
                continue

            char_type = bible.get("character_type", "protagonist")
            consistency_level = bible.get("consistency_level", self.CONSISTENCY_THRESHOLDS.get(char_type, 0.85))

            char_ref = CharacterReference(
                character_id=bible.get("id", f"char_{i}"),
                name=bible.get("name", f"角色{i+1}"),
                image_path=img_path,
                character_type=char_type,
                consistency_level=consistency_level,
                quality_score=self._calculate_image_quality(img_path),
            )
            characters.append(char_ref)

        # 生成全家福参考图
        group_ref_image = self._generate_group_reference_image(characters, group_name)

        group = CharacterGroup(
            group_id=group_id,
            name=group_name,
            characters=characters,
            group_reference_image=group_ref_image,
            created_at=datetime.now().isoformat(),
        )

        self.groups[group_id] = group
        logger.info(f"👨‍👩‍👧‍👦 创建角色组：{group_name}（{len(characters)}个角色）")
        return group

    def _calculate_image_quality(self, image_path: Path) -> float:
        """计算图片质量分数"""
        try:
            with Image.open(image_path) as img:
                img = img.convert("RGB")
                np_img = np.array(img)

                # 亮度分数（避免过暗/过曝）
                brightness = np.mean(np_img) / 255
                brightness_score = max(0, 1 - abs(brightness - 0.5) * 2) * 50

                # 对比度分数
                contrast = np.std(np_img) / 255
                contrast_score = min(100, contrast * 200) * 0.5

                return brightness_score + contrast_score
        except Exception:
            return 0.0

    def _generate_group_reference_image(
        self,
        characters: List[CharacterReference],
        group_name: str,
    ) -> Optional[Path]:
        """
        生成全家福参考图。
        
        将多个角色图片拼接到一起，作为整个场景的参考图。
        """
        if not characters:
            return None

        try:
            # 创建画布
            canvas_width = 800
            canvas_height = int(canvas_width * (16/9))
            canvas = Image.new("RGB", (canvas_width, canvas_height), (255, 255, 255))
            draw = ImageDraw.Draw(canvas)

            # 计算布局
            num_chars = len(characters)
            if num_chars == 1:
                positions = [(canvas_width//2, canvas_height//2)]
            elif num_chars == 2:
                positions = [
                    (canvas_width//4, canvas_height//2),
                    (canvas_width*3//4, canvas_height//2),
                ]
            elif num_chars == 3:
                positions = [
                    (canvas_width//2, canvas_height//3),
                    (canvas_width//4, canvas_height*2//3),
                    (canvas_width*3//4, canvas_height*2//3),
                ]
            else:
                cols = 2
                rows = (num_chars + 1) // cols
                cell_w = canvas_width // cols
                cell_h = canvas_height // rows
                positions = []
                for r in range(rows):
                    for c in range(cols):
                        idx = r * cols + c
                        if idx < num_chars:
                            positions.append((
                                c * cell_w + cell_w//2,
                                r * cell_h + cell_h//2,
                            ))

            # 拼接角色图片
            for char, (cx, cy) in zip(characters, positions):
                with Image.open(char.image_path) as img:
                    # 缩放为合适大小
                    size = 150
                    img = img.resize((size, size), Image.LANCZOS)

                    # 绘制圆形裁剪
                    mask = Image.new("L", (size, size), 0)
                    mask_draw = ImageDraw.Draw(mask)
                    mask_draw.ellipse((0, 0, size, size), fill=255)

                    result = Image.new("RGBA", (size, size), (0, 0, 0, 0))
                    result.paste(img, (0, 0), mask)

                    # 粘贴到画布
                    x = cx - size // 2
                    y = cy - size // 2
                    canvas.paste(result, (x, y), result)

                    # 添加姓名标签
                    try:
                        font = ImageFont.truetype("Arial.ttf", 16)
                    except Exception:
                        font = ImageFont.load_default()
                    text_bbox = draw.textbbox((0, 0), char.name, font=font)
                    text_w = text_bbox[2] - text_bbox[0]
                    text_h = text_bbox[3] - text_bbox[1]
                    draw.text(
                        (cx - text_w//2, cy + size//2 + 10),
                        char.name,
                        fill=(50, 50, 50),
                        font=font,
                        anchor="mm",
                    )

            # 添加标题
            try:
                title_font = ImageFont.truetype("Arial.ttf", 24)
            except Exception:
                title_font = ImageFont.load_default()
            title_bbox = draw.textbbox((0, 0), group_name, font=title_font)
            draw.text(
                (canvas_width//2, 30),
                group_name,
                fill=(30, 30, 30),
                font=title_font,
                anchor="mm",
            )

            # 保存
            output_path = OUTPUT_DIR / "group_references" / f"{group_name}_family.png"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            canvas.save(output_path, "PNG")

            logger.info(f"📸 全家福参考图已生成：{output_path}")
            return output_path

        except Exception as e:
            logger.error(f"生成全家福参考图失败：{e}")
            return None

    def get_group_reference_images(self, group_id: str) -> List[Path]:
        """获取角色组的所有参考图"""
        group = self.groups.get(group_id)
        if not group:
            return []

        images = []
        if group.group_reference_image and group.group_reference_image.exists():
            images.append(group.group_reference_image)

        for char in group.characters:
            if char.image_path.exists():
                images.append(char.image_path)

        return images

    def check_inter_scene_consistency(
        self,
        group_id: str,
        scene_images: List[Path],
    ) -> Dict[str, Any]:
        """
        检查跨场景角色一致性。
        
        参考行业最佳实践：使用感知哈希 + 颜色直方图对比。
        """
        group = self.groups.get(group_id)
        if not group:
            return {"error": "角色组不存在"}

        results = []
        for scene_idx, scene_img in enumerate(scene_images):
            if not scene_img.exists():
                continue

            scene_quality = self._calculate_image_quality(scene_img)
            consistency_scores = []

            for char in group.characters:
                if char.character_type == "background":
                    # 背景人物不检查一致性
                    continue
                
                if char.image_path.exists():
                    similarity = self._calculate_image_similarity(char.image_path, scene_img)
                    threshold = char.consistency_level
                    consistency_scores.append({
                        "character_name": char.name,
                        "character_type": char.character_type,
                        "similarity": similarity,
                        "threshold": threshold,
                        "consistent": similarity >= threshold,
                    })

            results.append({
                "scene_index": scene_idx,
                "image_path": str(scene_img),
                "scene_quality": scene_quality,
                "character_consistency": consistency_scores,
                "overall_consistent": all(c["consistent"] for c in consistency_scores),
            })

        return {
            "group_id": group_id,
            "group_name": group.name,
            "total_scenes": len(results),
            "consistent_scenes": sum(1 for r in results if r["overall_consistent"]),
            "results": results,
        }

    def _calculate_image_similarity(self, img1: Path, img2: Path) -> float:
        """计算两张图片的相似度（0-1）"""
        try:
            with Image.open(img1) as im1, Image.open(img2) as im2:
                # 转为灰度图
                im1_gray = im1.convert("L").resize((64, 64))
                im2_gray = im2.convert("L").resize((64, 64))

                # 感知哈希
                hash1 = self._get_perceptual_hash(im1_gray)
                hash2 = self._get_perceptual_hash(im2_gray)

                # 汉明距离
                hamming_dist = sum(c1 != c2 for c1, c2 in zip(hash1, hash2))
                hash_sim = 1 - (hamming_dist / len(hash1))

                # 颜色直方图相似度
                hist1 = im1.convert("RGB").histogram()
                hist2 = im2.convert("RGB").histogram()
                hist_sim = 1 - sum(abs(h1 - h2) for h1, h2 in zip(hist1, hist2)) / (256 * 3 * 255)

                return (hash_sim * 0.6 + hist_sim * 0.4)
        except Exception:
            return 0.0

    def _get_perceptual_hash(self, img: Image.Image) -> str:
        """计算感知哈希"""
        np_img = np.array(img)
        avg = np.mean(np_img)
        bits = []
        for i in range(8):
            for j in range(8):
                bits.append("1" if np_img[i][j] > avg else "0")
        return "".join(bits)

    def generate_consistency_prompt(
        self,
        group_id: str,
        scene_description: str,
        present_character_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        生成包含角色分级一致性约束的Prompt和参数。
        
        返回：
        - prompt: 一致性约束文本
        - fidelity_map: 每个角色的fidelity设置
        - reference_images: 需要使用的参考图
        """
        group = self.groups.get(group_id)
        if not group:
            return {"prompt": scene_description, "fidelity_map": {}, "reference_images": []}

        # 构建角色描述（只包含需要一致性的角色）
        character_descriptions = []
        fidelity_map = {}
        reference_images = []

        for char in group.characters:
            # 背景人物不加入一致性约束
            if char.character_type == "background":
                continue
            
            # 如果指定了present_character_ids，只包含出现的角色
            if present_character_ids and char.character_id not in present_character_ids:
                continue

            character_descriptions.append(f"{char.name}({char.character_type})")
            
            # 根据角色类型设置fidelity
            fidelity_map[char.character_id] = char.consistency_level
            
            if char.image_path.exists():
                reference_images.append(str(char.image_path))

        chars_str = ", ".join(character_descriptions)

        # 构建一致性约束Prompt
        consistency_prompt = (
            f"{scene_description}, "
            f"Characters: {chars_str}, "
            f"maintain character consistency throughout the scene, "
            f"same characters as reference images, "
        )

        # 根据角色类型添加特定约束
        for char in group.characters:
            if char.character_type == "protagonist":
                consistency_prompt += (
                    f"{char.name} must look exactly the same in every frame, "
                    f"identical face features and hairstyle, "
                )
            elif char.character_type == "supporting":
                consistency_prompt += (
                    f"{char.name} should maintain consistent appearance, "
                )
            elif char.character_type == "service":
                consistency_prompt += (
                    f"{char.name} wearing uniform, professional appearance, "
                )

        return {
            "prompt": consistency_prompt,
            "fidelity_map": fidelity_map,
            "reference_images": reference_images,
        }

    def get_group_stats(self, group_id: str) -> Dict[str, Any]:
        """获取角色组统计信息"""
        group = self.groups.get(group_id)
        if not group:
            return {}

        return {
            "group_id": group.group_id,
            "group_name": group.name,
            "num_characters": len(group.characters),
            "avg_quality_score": sum(c.quality_score for c in group.characters) / max(1, len(group.characters)),
            "has_group_reference": group.group_reference_image is not None,
            "total_usage": group.total_usage,
        }
