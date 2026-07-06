#!/usr/bin/env python3
"""
素材资产库（Asset Library）

管理所有参考图、角色、场景的版本和复用。
核心理念：避免重复生成，建立可复用的资产池。
"""

import json
import hashlib
import base64
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field, asdict
from datetime import datetime

import numpy as np
from PIL import Image

from config import setup_logger, ASSET_LIBRARY_PATH

logger = setup_logger(__name__)


@dataclass
class AssetMetadata:
    """资产元数据"""
    asset_id: str
    asset_type: str  # character / product / scene / keyframe
    name: str
    description: str
    tags: List[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    usage_count: int = 0
    quality_score: float = 0.0
    source: str = ""  # generated / uploaded / imported
    version: int = 1


@dataclass
class CharacterAsset:
    """角色资产"""
    metadata: AssetMetadata
    image_path: Path
    bible: Dict[str, Any] = field(default_factory=dict)
    # 面部特征向量（用于相似度匹配）
    face_embedding: Optional[List[float]] = None
    # 颜色直方图（用于快速筛选）
    color_histogram: Optional[List[float]] = None


@dataclass
class ProductAsset:
    """商品资产"""
    metadata: AssetMetadata
    image_path: Path
    bible: Dict[str, Any] = field(default_factory=dict)
    # 包装特征向量
    packaging_embedding: Optional[List[float]] = None


class AssetLibrary:
    """素材资产库主类"""

    def __init__(self, library_path: Optional[Path] = None):
        self.library_path = library_path or ASSET_LIBRARY_PATH
        self.library_path.mkdir(parents=True, exist_ok=True)
        self.index_file = self.library_path / "index.json"
        self.assets: Dict[str, Dict[str, Any]] = {}
        self._load_index()

    def _load_index(self):
        """加载资产索引"""
        if self.index_file.exists():
            try:
                with open(self.index_file, "r", encoding="utf-8") as f:
                    self.assets = json.load(f)
            except Exception as e:
                logger.warning(f"加载资产索引失败：{e}")
                self.assets = {}

    def _save_index(self):
        """保存资产索引"""
        try:
            with open(self.index_file, "w", encoding="utf-8") as f:
                json.dump(self.assets, f, ensure_ascii=False, indent=2, default=str)
        except Exception as e:
            logger.error(f"保存资产索引失败：{e}")

    def _generate_asset_id(self, image_path: Path) -> str:
        """基于图片内容生成唯一ID"""
        content = image_path.read_bytes()
        return hashlib.sha256(content).hexdigest()[:16]

    def _extract_image_features(self, image_path: Path) -> Dict[str, Any]:
        """提取图片特征向量"""
        features = {}
        try:
            with Image.open(image_path) as img:
                img = img.convert("RGB")
                # 颜色直方图
                hist = img.histogram()
                features["color_histogram"] = hist[:256]  # 简化版

                # 平均颜色
                stat = img.getextrema()
                features["avg_color"] = [
                    sum(img.getchannel(i).getextrema()) / 2
                    for i in range(3)
                ]
        except Exception as e:
            logger.warning(f"提取图片特征失败：{e}")
        return features

    def add_character(
        self,
        image_path: Path,
        name: str,
        bible: Dict[str, Any],
        description: str = "",
        tags: Optional[List[str]] = None,
    ) -> str:
        """
        添加角色资产。

        Returns:
            asset_id
        """
        if not image_path.exists():
            raise FileNotFoundError(f"图片不存在：{image_path}")

        asset_id = self._generate_asset_id(image_path)

        # 检查是否已存在
        if asset_id in self.assets:
            logger.info(f"角色资产已存在：{name} (ID: {asset_id})")
            self.assets[asset_id]["usage_count"] = self.assets[asset_id].get("usage_count", 0) + 1
            self._save_index()
            return asset_id

        # 复制图片到资产库
        asset_dir = self.library_path / "characters" / asset_id
        asset_dir.mkdir(parents=True, exist_ok=True)
        stored_path = asset_dir / "image.png"
        stored_path.write_bytes(image_path.read_bytes())

        # 提取特征
        features = self._extract_image_features(stored_path)

        # 创建元数据
        now = datetime.now().isoformat()
        metadata = {
            "asset_id": asset_id,
            "asset_type": "character",
            "name": name,
            "description": description,
            "tags": tags or [],
            "created_at": now,
            "updated_at": now,
            "usage_count": 1,
            "quality_score": 0.0,
            "source": "uploaded",
            "version": 1,
        }

        self.assets[asset_id] = {
            **metadata,
            "image_path": str(stored_path),
            "bible": bible,
            "features": features,
        }

        self._save_index()
        logger.info(f"✅ 添加角色资产：{name} (ID: {asset_id})")
        return asset_id

    def add_product(
        self,
        image_path: Path,
        name: str,
        bible: Dict[str, Any],
        description: str = "",
        tags: Optional[List[str]] = None,
    ) -> str:
        """添加商品资产"""
        if not image_path.exists():
            raise FileNotFoundError(f"图片不存在：{image_path}")

        asset_id = self._generate_asset_id(image_path)

        if asset_id in self.assets:
            logger.info(f"商品资产已存在：{name} (ID: {asset_id})")
            self.assets[asset_id]["usage_count"] += 1
            self._save_index()
            return asset_id

        asset_dir = self.library_path / "products" / asset_id
        asset_dir.mkdir(parents=True, exist_ok=True)
        stored_path = asset_dir / "image.png"
        stored_path.write_bytes(image_path.read_bytes())

        features = self._extract_image_features(stored_path)

        now = datetime.now().isoformat()
        self.assets[asset_id] = {
            "asset_id": asset_id,
            "asset_type": "product",
            "name": name,
            "description": description,
            "tags": tags or [],
            "created_at": now,
            "updated_at": now,
            "usage_count": 1,
            "quality_score": 0.0,
            "source": "uploaded",
            "version": 1,
            "image_path": str(stored_path),
            "bible": bible,
            "features": features,
        }

        self._save_index()
        logger.info(f"✅ 添加商品资产：{name} (ID: {asset_id})")
        return asset_id

    def add_video_clip(
        self,
        video_path: Path,
        name: str,
        metadata: Optional[Dict[str, Any]] = None,
        description: str = "",
        tags: Optional[List[str]] = None,
    ) -> str:
        """添加视频片段资产。"""
        if not video_path.exists():
            raise FileNotFoundError(f"视频不存在：{video_path}")

        asset_id = self._generate_asset_id(video_path)

        if asset_id in self.assets:
            logger.info(f"视频片段资产已存在：{name} (ID: {asset_id})")
            self.assets[asset_id]["usage_count"] = self.assets[asset_id].get("usage_count", 0) + 1
            self._save_index()
            return asset_id

        suffix = video_path.suffix or ".mp4"
        asset_dir = self.library_path / "video_clips" / asset_id
        asset_dir.mkdir(parents=True, exist_ok=True)
        stored_path = asset_dir / f"clip{suffix}"
        stored_path.write_bytes(video_path.read_bytes())

        now = datetime.now().isoformat()
        self.assets[asset_id] = {
            "asset_id": asset_id,
            "asset_type": "video_clip",
            "name": name,
            "description": description,
            "tags": tags or [],
            "created_at": now,
            "updated_at": now,
            "usage_count": 1,
            "quality_score": 0.0,
            "source": "generated",
            "version": 1,
            "video_path": str(stored_path),
            "metadata": metadata or {},
        }

        self._save_index()
        logger.info(f"✅ 添加视频片段资产：{name} (ID: {asset_id})")
        return asset_id

    def find_similar_character(
        self,
        description: str,
        reference_image: Optional[Path] = None,
        top_k: int = 3,
    ) -> List[Dict[str, Any]]:
        """
        根据描述或参考图查找相似角色。

        简单实现：基于标签匹配 + 使用频次排序
        """
        characters = [
            asset for asset in self.assets.values()
            if asset.get("asset_type") == "character"
        ]

        if not characters:
            return []

        # 基于标签匹配评分
        desc_words = set(description.lower().split())
        scored = []

        for char in characters:
            score = 0.0
            # 标签匹配
            tags = set(t.lower() for t in char.get("tags", []))
            score += len(desc_words & tags) * 10

            # 名称匹配
            name_words = set(char.get("name", "").lower().split())
            score += len(desc_words & name_words) * 20

            # 描述匹配
            char_desc = set(char.get("description", "").lower().split())
            score += len(desc_words & char_desc) * 5

            # 使用频次加成（用得多的更可靠）
            score += char.get("usage_count", 0) * 2

            scored.append((char, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [char for char, _ in scored[:top_k]]

    def get_best_reference_for_scene(
        self,
        scene_description: str,
        narrative: str,
    ) -> Optional[Path]:
        """
        为场景推荐最佳参考图。

        策略：
        - showcase/cta：优先商品参考图
        - hook/result：优先角色参考图
        - 默认：返回使用频次最高的资产
        """
        narrative = narrative.lower().strip()

        if narrative in {"showcase", "product", "cta"}:
            assets = [a for a in self.assets.values() if a.get("asset_type") == "product"]
        elif narrative in {"hook", "result", "turning_point", "turning"}:
            assets = [a for a in self.assets.values() if a.get("asset_type") == "character"]
        else:
            assets = list(self.assets.values())

        if not assets:
            return None

        # 返回使用频次最高的
        best = max(assets, key=lambda a: a.get("usage_count", 0))
        return Path(best["image_path"])

    def update_quality_score(self, asset_id: str, score: float):
        """更新资产质量评分"""
        if asset_id in self.assets:
            self.assets[asset_id]["quality_score"] = score
            self.assets[asset_id]["updated_at"] = datetime.now().isoformat()
            self._save_index()

    def get_asset(self, asset_id: str) -> Optional[Dict[str, Any]]:
        """获取资产信息"""
        return self.assets.get(asset_id)

    def list_assets(
        self,
        asset_type: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """列出资产"""
        results = list(self.assets.values())

        if asset_type:
            results = [a for a in results if a.get("asset_type") == asset_type]

        if tags:
            results = [
                a for a in results
                if any(t in a.get("tags", []) for t in tags)
            ]

        return sorted(results, key=lambda a: a.get("usage_count", 0), reverse=True)

    def get_stats(self) -> Dict[str, Any]:
        """获取资产库统计"""
        return {
            "total_assets": len(self.assets),
            "characters": len([a for a in self.assets.values() if a.get("asset_type") == "character"]),
            "products": len([a for a in self.assets.values() if a.get("asset_type") == "product"]),
            "video_clips": len([a for a in self.assets.values() if a.get("asset_type") == "video_clip"]),
            "total_usage": sum(a.get("usage_count", 0) for a in self.assets.values()),
            "avg_quality": sum(a.get("quality_score", 0) for a in self.assets.values()) / max(1, len(self.assets)),
        }
