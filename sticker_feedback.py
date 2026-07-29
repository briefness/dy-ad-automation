#!/usr/bin/env python3
"""Explicit user feedback store for semantic sticker preferences."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


DEFAULT_DB_PATH = Path(__file__).resolve().parent / "data" / "sticker_feedback.db"
VALID_VERDICTS = {"satisfied", "violated"}


def _rule_key(rule_text: str) -> str:
    normalized = re.sub(r"\s+", "", str(rule_text or "")).lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _ngrams(text: str, size: int = 2) -> set[str]:
    normalized = re.sub(r"[^\w\u4e00-\u9fff]", "", str(text or "")).lower()
    if not normalized:
        return set()
    if len(normalized) <= size:
        return {normalized}
    return {normalized[index:index + size] for index in range(len(normalized) - size + 1)}


def _similarity(left: str, right: str) -> float:
    left_grams = _ngrams(left)
    right_grams = _ngrams(right)
    if not left_grams or not right_grams:
        return 0.0
    return len(left_grams & right_grams) / len(left_grams | right_grams)


class StickerFeedbackStore:
    """Persist user-authored sticker judgments without learning from automatic scores."""

    def __init__(self, db_path: Optional[Path] = None, min_distinct_videos: int = 2):
        self.db_path = Path(db_path or DEFAULT_DB_PATH)
        self.min_distinct_videos = max(2, int(min_distinct_videos))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS user_sticker_feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    video_id TEXT NOT NULL,
                    product_category TEXT NOT NULL DEFAULT '',
                    video_style TEXT NOT NULL DEFAULT '',
                    rule_key TEXT NOT NULL,
                    rule_text TEXT NOT NULL,
                    verdict TEXT NOT NULL,
                    source TEXT NOT NULL,
                    user_comment TEXT NOT NULL DEFAULT '',
                    sticker_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    UNIQUE(video_id, rule_key, verdict, user_comment, sticker_json)
                )
                """
            )
            connection.commit()

    def record_feedback(
        self,
        *,
        video_id: str,
        rule_text: str,
        verdict: str,
        source: str,
        user_comment: str = "",
        sticker: Optional[Dict[str, Any]] = None,
        product_category: str = "",
        video_style: str = "",
    ) -> bool:
        if source != "user":
            return False
        video_id = str(video_id or "").strip()
        rule_text = str(rule_text or "").strip()
        verdict = str(verdict or "").strip().lower()
        if not video_id or not rule_text:
            raise ValueError("video_id 和 rule_text 不能为空")
        if verdict not in VALID_VERDICTS:
            raise ValueError(f"verdict 必须是 {sorted(VALID_VERDICTS)}")
        sticker_json = json.dumps(sticker or {}, ensure_ascii=False, sort_keys=True)
        with sqlite3.connect(self.db_path) as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO user_sticker_feedback
                (video_id, product_category, video_style, rule_key, rule_text,
                 verdict, source, user_comment, sticker_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 'user', ?, ?, ?)
                """,
                (
                    video_id,
                    str(product_category or "").strip(),
                    str(video_style or "").strip(),
                    _rule_key(rule_text),
                    rule_text,
                    verdict,
                    str(user_comment or "").strip(),
                    sticker_json,
                    datetime.now().isoformat(),
                ),
            )
            connection.commit()
            return cursor.rowcount > 0

    def build_policy(self, product_category: str = "", video_style: str = "") -> Dict[str, Any]:
        with sqlite3.connect(self.db_path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT * FROM user_sticker_feedback
                WHERE source = 'user'
                  AND (product_category = '' OR ? = '' OR product_category = ?)
                  AND (video_style = '' OR ? = '' OR video_style = ?)
                ORDER BY id
                """,
                (product_category, product_category, video_style, video_style),
            ).fetchall()

        grouped: Dict[str, list[sqlite3.Row]] = {}
        for row in rows:
            grouped.setdefault(str(row["rule_key"]), []).append(row)
        rules = []
        positive_examples = []
        negative_examples = []
        for key, evidence in grouped.items():
            distinct_videos = len({str(row["video_id"]) for row in evidence})
            status = "active" if distinct_videos >= self.min_distinct_videos else "provisional"
            rules.append({
                "id": key,
                "text": str(evidence[0]["rule_text"]),
                "status": status,
                "distinct_video_count": distinct_videos,
                "feedback_count": len(evidence),
                "source": "explicit_user_feedback",
            })
            for row in evidence:
                try:
                    sticker = json.loads(str(row["sticker_json"] or "{}"))
                except json.JSONDecodeError:
                    sticker = {}
                example = {
                    "rule_id": key,
                    "rule_status": status,
                    "video_id": str(row["video_id"]),
                    "user_comment": str(row["user_comment"] or ""),
                    "sticker": sticker,
                }
                target = positive_examples if row["verdict"] == "satisfied" else negative_examples
                target.append(example)
        fingerprint_payload = {
            "rules": rules,
            "positive_examples": positive_examples,
            "negative_examples": negative_examples,
        }
        fingerprint = hashlib.sha256(
            json.dumps(fingerprint_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        return {
            "source": "explicit_user_feedback_only",
            "rules": rules,
            "positive_examples": positive_examples,
            "negative_examples": negative_examples,
            "fingerprint": fingerprint,
        }


def sticker_preference_score(sticker: Dict[str, Any], policy: Dict[str, Any]) -> float:
    """Rank candidates from active user examples without changing hard eligibility rules."""
    candidate_text = f"{sticker.get('kind', '')} {sticker.get('text', '')}"

    def score_examples(examples: list[Dict[str, Any]]) -> float:
        scores = []
        for example in examples:
            if example.get("rule_status") != "active":
                continue
            saved = example.get("sticker") or {}
            saved_text = f"{saved.get('kind', '')} {saved.get('text', '')}"
            kind_bonus = 0.5 if saved.get("kind") and saved.get("kind") == sticker.get("kind") else 0.0
            scores.append(min(1.0, kind_bonus + _similarity(candidate_text, saved_text) * 0.5))
        return max(scores, default=0.0)

    return round(
        score_examples(policy.get("positive_examples") or [])
        - score_examples(policy.get("negative_examples") or []),
        4,
    )


def _load_plan(video: str, explicit_plan: Optional[str]) -> Dict[str, Any]:
    if explicit_plan:
        return json.loads(Path(explicit_plan).expanduser().read_text(encoding="utf-8"))
    video_path = Path(video).expanduser()
    candidate = video_path.with_name(f"{video_path.stem.removesuffix('_final')}_sticker_plan.json")
    return json.loads(candidate.read_text(encoding="utf-8")) if candidate.is_file() else {}


def main() -> int:
    parser = argparse.ArgumentParser(description="记录使用者真实贴图反馈")
    parser.add_argument("--video", required=True, help="成片路径或稳定 video_id")
    parser.add_argument("--rule", required=True, help="使用者原文规则")
    parser.add_argument("--verdict", required=True, choices=sorted(VALID_VERDICTS))
    parser.add_argument("--sticker-id", default="")
    parser.add_argument("--comment", default="")
    parser.add_argument("--plan-json")
    parser.add_argument("--product-category", default="")
    parser.add_argument("--video-style", default="")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args()
    plan = _load_plan(args.video, args.plan_json)
    sticker = next(
        (item for item in plan.get("items") or [] if str(item.get("id") or "") == args.sticker_id),
        {},
    )
    video_path = Path(args.video).expanduser()
    video_id = str(video_path.resolve()) if video_path.exists() else args.video
    stored = StickerFeedbackStore(args.db).record_feedback(
        video_id=video_id,
        rule_text=args.rule,
        verdict=args.verdict,
        source="user",
        user_comment=args.comment,
        sticker=sticker,
        product_category=args.product_category,
        video_style=args.video_style,
    )
    print("已记录使用者贴图反馈" if stored else "该反馈已存在")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
