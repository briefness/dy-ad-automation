#!/usr/bin/env python3
"""
实验追踪器（Experiment Tracker）

追踪不同参数组合的效果，支持A/B测试和持续优化。
核心理念：用数据驱动决策，而非假设。
"""

import json
import sqlite3
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from datetime import datetime

from config import setup_logger, EXPERIMENT_DB_PATH

logger = setup_logger(__name__)


@dataclass
class Experiment:
    """实验记录"""
    experiment_id: str
    hypothesis: str
    params: Dict[str, Any]
    video_id: str
    rating: int = 0
    quality_score: float = 0.0
    created_at: str = ""
    status: str = "pending"  # pending / completed / failed


class ExperimentTracker:
    """实验追踪主类"""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or EXPERIMENT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        """初始化数据库"""
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS experiments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    experiment_id TEXT UNIQUE,
                    hypothesis TEXT,
                    params TEXT,
                    video_id TEXT,
                    rating INTEGER DEFAULT 0,
                    quality_score REAL DEFAULT 0,
                    created_at TEXT,
                    status TEXT DEFAULT 'pending'
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS param_effectiveness (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    param_name TEXT,
                    param_value TEXT,
                    avg_rating REAL DEFAULT 0,
                    usage_count INTEGER DEFAULT 0,
                    success_rate REAL DEFAULT 0,
                    last_used TEXT
                )
            """)
            conn.commit()

    def start_experiment(
        self,
        experiment_id: str,
        hypothesis: str,
        params: Dict[str, Any],
        video_id: str,
    ) -> bool:
        """开始一个实验"""
        try:
            with sqlite3.connect(str(self.db_path)) as conn:
                conn.execute(
                    """
                    INSERT INTO experiments
                    (experiment_id, hypothesis, params, video_id, created_at, status)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        experiment_id,
                        hypothesis,
                        json.dumps(params, ensure_ascii=False),
                        video_id,
                        datetime.now().isoformat(),
                        "pending",
                    ),
                )
                conn.commit()
            logger.info(f"🔬 开始实验：{experiment_id} - {hypothesis}")
            return True
        except Exception as e:
            logger.error(f"创建实验失败：{e}")
            return False

    def complete_experiment(
        self,
        experiment_id: str,
        rating: int,
        quality_score: float,
    ) -> bool:
        """完成实验并记录结果"""
        try:
            with sqlite3.connect(str(self.db_path)) as conn:
                conn.execute(
                    """
                    UPDATE experiments
                    SET rating = ?, quality_score = ?, status = ?
                    WHERE experiment_id = ?
                    """,
                    (rating, quality_score, "completed", experiment_id),
                )
                conn.commit()

                # 更新参数有效性统计
                cursor = conn.execute(
                    "SELECT params FROM experiments WHERE experiment_id = ?",
                    (experiment_id,),
                )
                row = cursor.fetchone()
                if row:
                    params = json.loads(row[0])
                    self._update_param_effectiveness(params, rating)

            logger.info(f"✅ 实验完成：{experiment_id}, 评分={rating}, 质量分={quality_score}")
            return True
        except Exception as e:
            logger.error(f"完成实验失败：{e}")
            return False

    def _update_param_effectiveness(self, params: Dict[str, Any], rating: int):
        """更新参数有效性统计"""
        now = datetime.now().isoformat()
        with sqlite3.connect(str(self.db_path)) as conn:
            for param_name, param_value in params.items():
                value_str = str(param_value)
                cursor = conn.execute(
                    """
                    SELECT usage_count, avg_rating FROM param_effectiveness
                    WHERE param_name = ? AND param_value = ?
                    """,
                    (param_name, value_str),
                )
                row = cursor.fetchone()

                if row:
                    count, avg_r = row
                    new_count = count + 1
                    new_avg = (avg_r * count + rating) / new_count
                    conn.execute(
                        """
                        UPDATE param_effectiveness
                        SET usage_count = ?, avg_rating = ?, last_used = ?
                        WHERE param_name = ? AND param_value = ?
                        """,
                        (new_count, new_avg, now, param_name, value_str),
                    )
                else:
                    conn.execute(
                        """
                        INSERT INTO param_effectiveness
                        (param_name, param_value, avg_rating, usage_count, last_used)
                        VALUES (?, ?, ?, 1, ?)
                        """,
                        (param_name, value_str, rating, now),
                    )
            conn.commit()

    def get_best_params(
        self,
        param_name: str,
        min_usage: int = 3,
    ) -> Optional[Any]:
        """获取某参数的历史最优值"""
        with sqlite3.connect(str(self.db_path)) as conn:
            cursor = conn.execute(
                """
                SELECT param_value, avg_rating, usage_count
                FROM param_effectiveness
                WHERE param_name = ? AND usage_count >= ?
                ORDER BY avg_rating DESC, usage_count DESC
                LIMIT 1
                """,
                (param_name, min_usage),
            )
            row = cursor.fetchone()
            if row:
                return row[0]
        return None

    def get_param_comparison(
        self,
        param_name: str,
    ) -> List[Dict[str, Any]]:
        """获取某参数所有取值的对比"""
        with sqlite3.connect(str(self.db_path)) as conn:
            cursor = conn.execute(
                """
                SELECT param_value, avg_rating, usage_count
                FROM param_effectiveness
                WHERE param_name = ?
                ORDER BY avg_rating DESC
                """,
                (param_name,),
            )
            return [
                {
                    "value": row[0],
                    "avg_rating": round(row[1], 2),
                    "usage_count": row[2],
                }
                for row in cursor.fetchall()
            ]

    def recommend_params(self, base_params: Dict[str, Any]) -> Dict[str, Any]:
        """基于历史数据推荐最优参数"""
        recommended = base_params.copy()

        for param_name in ["seed", "image_fidelity", "human_fidelity", "mode"]:
            best_value = self.get_best_params(param_name)
            if best_value is not None:
                # 类型转换
                original_value = base_params.get(param_name)
                if isinstance(original_value, int):
                    try:
                        recommended[param_name] = int(best_value)
                    except ValueError:
                        recommended[param_name] = original_value
                elif isinstance(original_value, float):
                    try:
                        recommended[param_name] = float(best_value)
                    except ValueError:
                        recommended[param_name] = original_value
                else:
                    recommended[param_name] = best_value

        return recommended

    def get_experiment_report(self) -> Dict[str, Any]:
        """获取实验报告"""
        with sqlite3.connect(str(self.db_path)) as conn:
            cursor = conn.execute(
                "SELECT COUNT(*), AVG(rating), AVG(quality_score) FROM experiments WHERE status = 'completed'"
            )
            total, avg_rating, avg_quality = cursor.fetchone()

            cursor = conn.execute(
                """
                SELECT param_name, param_value, avg_rating, usage_count
                FROM param_effectiveness
                WHERE usage_count >= 3
                ORDER BY avg_rating DESC
                LIMIT 20
                """
            )
            top_params = [
                {
                    "param": row[0],
                    "value": row[1],
                    "avg_rating": round(row[2], 2),
                    "usage": row[3],
                }
                for row in cursor.fetchall()
            ]

        return {
            "total_experiments": total or 0,
            "avg_rating": round(avg_rating or 0, 2),
            "avg_quality_score": round(avg_quality or 0, 2),
            "top_performing_params": top_params,
        }
