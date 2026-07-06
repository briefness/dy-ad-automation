#!/usr/bin/env python3
"""
反馈闭环系统（Feedback Loop）

收集用户对生成视频的反馈，自动分析失败模式，持续优化生成策略。
核心理念：每收集一条反馈，系统就聪明一分。
"""

import json
import sqlite3
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from datetime import datetime

from config import setup_logger, FEEDBACK_DB_PATH

logger = setup_logger(__name__)


@dataclass
class VideoFeedback:
    """视频反馈记录"""
    video_id: str
    generation_params: Dict[str, Any] = field(default_factory=dict)
    rating: int = 0  # 1-5星
    issues: List[str] = field(default_factory=list)  # face / product / action / color / audio / other
    user_comment: str = ""
    created_at: str = ""
    # 自动化检测评分
    auto_quality_score: float = 0.0
    auto_issues: List[str] = field(default_factory=list)


class FeedbackLoop:
    """反馈闭环主类"""

    # 问题类型映射到优化策略
    ISSUE_FIX_MAP = {
        "face": {
            "adjustments": ["increase_human_fidelity", "add_front_facing_prompt"],
            "description": "角色面部崩坏",
        },
        "product": {
            "adjustments": ["increase_image_fidelity", "add_product_center_prompt", "increase_product_required_segments"],
            "description": "产品不清晰或不一致",
        },
        "action": {
            "adjustments": ["simplify_motion_description", "reduce_complex_actions"],
            "description": "动作不自然",
        },
        "color": {
            "adjustments": ["add_color_grading_postprocess", "adjust_lighting_prompt"],
            "description": "色彩偏色",
        },
        "audio": {
            "adjustments": ["adjust_bgm_volume", "improve_voiceover_clarity"],
            "description": "音频问题",
        },
        "consistency": {
            "adjustments": ["increase_seed_consistency", "lock_more_parameters"],
            "description": "片段间不一致",
        },
        "scene": {
            "adjustments": ["strengthen_scene_anchor", "add_location_keywords"],
            "description": "场景跳跃",
        },
    }

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or FEEDBACK_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        """初始化数据库"""
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    video_id TEXT NOT NULL,
                    generation_params TEXT,
                    rating INTEGER DEFAULT 0,
                    issues TEXT,
                    user_comment TEXT,
                    auto_quality_score REAL DEFAULT 0,
                    auto_issues TEXT,
                    created_at TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS failure_patterns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    issue_type TEXT NOT NULL,
                    frequency INTEGER DEFAULT 1,
                    avg_rating REAL DEFAULT 0,
                    first_seen TEXT,
                    last_seen TEXT,
                    suggested_fixes TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS prompt_optimizations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    narrative_type TEXT,
                    original_prompt TEXT,
                    optimized_prompt TEXT,
                    improvement_score REAL,
                    usage_count INTEGER DEFAULT 1,
                    avg_rating REAL DEFAULT 0,
                    created_at TEXT
                )
            """)
            conn.commit()

    def collect_feedback(
        self,
        video_id: str,
        generation_params: Dict[str, Any],
        rating: int = 0,
        issues: Optional[List[str]] = None,
        user_comment: str = "",
        auto_quality_score: float = 0.0,
        auto_issues: Optional[List[str]] = None,
    ) -> bool:
        """收集用户反馈"""
        try:
            with sqlite3.connect(str(self.db_path)) as conn:
                conn.execute(
                    """
                    INSERT INTO feedback
                    (video_id, generation_params, rating, issues, user_comment,
                     auto_quality_score, auto_issues, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        video_id,
                        json.dumps(generation_params, ensure_ascii=False),
                        rating,
                        json.dumps(issues or [], ensure_ascii=False),
                        user_comment,
                        auto_quality_score,
                        json.dumps(auto_issues or [], ensure_ascii=False),
                        datetime.now().isoformat(),
                    ),
                )
                conn.commit()

            # 更新失败模式统计
            if issues:
                self._update_failure_patterns(issues, rating)

            logger.info(f"✅ 收集到反馈：video_id={video_id}, rating={rating}, issues={issues}")
            return True
        except Exception as e:
            logger.error(f"收集反馈失败：{e}")
            return False

    def _update_failure_patterns(self, issues: List[str], rating: int):
        """更新失败模式统计"""
        now = datetime.now().isoformat()
        with sqlite3.connect(str(self.db_path)) as conn:
            for issue in issues:
                # 查询是否已存在
                cursor = conn.execute(
                    "SELECT frequency, avg_rating FROM failure_patterns WHERE issue_type = ?",
                    (issue,),
                )
                row = cursor.fetchone()

                if row:
                    freq, avg_r = row
                    new_freq = freq + 1
                    new_avg = (avg_r * freq + rating) / new_freq
                    conn.execute(
                        """
                        UPDATE failure_patterns
                        SET frequency = ?, avg_rating = ?, last_seen = ?
                        WHERE issue_type = ?
                        """,
                        (new_freq, new_avg, now, issue),
                    )
                else:
                    fixes = self.ISSUE_FIX_MAP.get(issue, {}).get("adjustments", [])
                    conn.execute(
                        """
                        INSERT INTO failure_patterns
                        (issue_type, frequency, avg_rating, first_seen, last_seen, suggested_fixes)
                        VALUES (?, 1, ?, ?, ?, ?)
                        """,
                        (issue, rating, now, now, json.dumps(fixes)),
                    )
            conn.commit()

    def analyze_failure_patterns(self) -> Dict[str, Any]:
        """分析失败模式，返回需要优先修复的问题"""
        with sqlite3.connect(str(self.db_path)) as conn:
            cursor = conn.execute(
                """
                SELECT issue_type, frequency, avg_rating, suggested_fixes
                FROM failure_patterns
                WHERE frequency >= 2
                ORDER BY frequency DESC, avg_rating ASC
                """
            )
            patterns = []
            for row in cursor.fetchall():
                issue_type, freq, avg_rating, fixes_json = row
                patterns.append({
                    "issue_type": issue_type,
                    "frequency": freq,
                    "avg_rating": avg_rating,
                    "description": self.ISSUE_FIX_MAP.get(issue_type, {}).get("description", issue_type),
                    "suggested_fixes": json.loads(fixes_json) if fixes_json else [],
                    "priority": "high" if freq >= 5 and avg_rating <= 2 else "medium",
                })

        return {
            "total_patterns": len(patterns),
            "high_priority": [p for p in patterns if p["priority"] == "high"],
            "medium_priority": [p for p in patterns if p["priority"] == "medium"],
            "all_patterns": patterns,
        }

    def get_adjustments_for_next_generation(self) -> Dict[str, Any]:
        """
        基于历史反馈，生成下一次生成的参数调整建议。
        
        Returns:
            {"prompt_adjustments": [...], "param_adjustments": {...}}
        """
        adjustments = {
            "prompt_adjustments": [],
            "param_adjustments": {},
            "blocked_narratives": [],
        }

        patterns = self.analyze_failure_patterns()

        for pattern in patterns["high_priority"]:
            issue_type = pattern["issue_type"]
            fixes = pattern.get("suggested_fixes", [])

            for fix in fixes:
                if fix == "increase_human_fidelity":
                    adjustments["param_adjustments"]["human_fidelity"] = 0.95
                elif fix == "increase_image_fidelity":
                    adjustments["param_adjustments"]["image_fidelity"] = 0.95
                elif fix == "add_front_facing_prompt":
                    adjustments["prompt_adjustments"].append("确保角色正面朝向镜头")
                elif fix == "add_product_center_prompt":
                    adjustments["prompt_adjustments"].append("产品位于画面中心，清晰可见")
                elif fix == "strengthen_scene_anchor":
                    adjustments["param_adjustments"]["use_scene_anchor"] = True
                elif fix == "increase_seed_consistency":
                    adjustments["param_adjustments"]["force_seed"] = True

        return adjustments

    def get_successful_prompt_template(
        self,
        narrative_type: str,
        min_rating: int = 4,
    ) -> Optional[str]:
        """获取某叙事段的高分Prompt模板"""
        with sqlite3.connect(str(self.db_path)) as conn:
            cursor = conn.execute(
                """
                SELECT generation_params
                FROM feedback
                WHERE rating >= ?
                ORDER BY rating DESC, auto_quality_score DESC
                LIMIT 10
                """,
                (min_rating,),
            )

            prompts = []
            for row in cursor.fetchall():
                params = json.loads(row[0])
                prompt = params.get("prompt", "")
                if prompt:
                    prompts.append(prompt)

            if not prompts:
                return None

            # 简单策略：返回最长的高分Prompt（通常描述更详细）
            return max(prompts, key=len)

    def get_stats(self) -> Dict[str, Any]:
        """获取反馈统计"""
        with sqlite3.connect(str(self.db_path)) as conn:
            cursor = conn.execute("SELECT COUNT(*), AVG(rating) FROM feedback")
            total, avg_rating = cursor.fetchone()

            cursor = conn.execute(
                "SELECT COUNT(DISTINCT issue_type) FROM failure_patterns"
            )
            pattern_count = cursor.fetchone()[0]

            cursor = conn.execute(
                """
                SELECT rating, COUNT(*) FROM feedback
                GROUP BY rating
                ORDER BY rating
                """
            )
            rating_distribution = {row[0]: row[1] for row in cursor.fetchall()}

        return {
            "total_feedback": total or 0,
            "avg_rating": round(avg_rating or 0, 2),
            "failure_patterns": pattern_count or 0,
            "rating_distribution": rating_distribution,
        }

    def export_learning_report(self, output_path: Path):
        """导出学习报告"""
        patterns = self.analyze_failure_patterns()
        stats = self.get_stats()
        adjustments = self.get_adjustments_for_next_generation()

        report = {
            "generated_at": datetime.now().isoformat(),
            "stats": stats,
            "top_issues": patterns["high_priority"][:5],
            "recommended_adjustments": adjustments,
        }

        output_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(f"📊 学习报告已导出：{output_path}")
