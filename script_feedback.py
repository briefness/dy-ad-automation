#!/usr/bin/env python3
"""User-authored script preferences with traceable evidence and cold-start emptiness."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


DEFAULT_DB_PATH = Path(__file__).resolve().parent / "data" / "script_feedback.db"
VALID_VERDICTS = {"satisfied", "violated"}


def _rule_key(rule_text: str) -> str:
    normalized = re.sub(r"\s+", "", rule_text).lower()
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


class ScriptFeedbackStore:
    """Store only explicit user judgments; automatic observations are not learning data."""

    def __init__(self, db_path: Optional[Path] = None, min_distinct_videos: int = 2):
        self.db_path = Path(db_path or DEFAULT_DB_PATH)
        self.min_distinct_videos = max(2, int(min_distinct_videos))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_script_feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    video_id TEXT NOT NULL,
                    product_category TEXT NOT NULL DEFAULT '',
                    script_style TEXT NOT NULL DEFAULT '',
                    rule_key TEXT NOT NULL,
                    rule_text TEXT NOT NULL,
                    verdict TEXT NOT NULL,
                    source TEXT NOT NULL,
                    user_comment TEXT NOT NULL DEFAULT '',
                    script_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    UNIQUE(video_id, rule_key, verdict, user_comment)
                )
                """
            )
            conn.commit()

    def record_feedback(
        self,
        *,
        video_id: str,
        rule_text: str,
        verdict: str,
        source: str,
        user_comment: str = "",
        script: Optional[Dict[str, Any]] = None,
        product_category: str = "",
        script_style: str = "",
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
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO user_script_feedback
                (video_id, product_category, script_style, rule_key, rule_text,
                 verdict, source, user_comment, script_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 'user', ?, ?, ?)
                """,
                (
                    video_id,
                    str(product_category or "").strip(),
                    str(script_style or "").strip(),
                    _rule_key(rule_text),
                    rule_text,
                    verdict,
                    str(user_comment or "").strip(),
                    json.dumps(script or {}, ensure_ascii=False),
                    datetime.now().isoformat(),
                ),
            )
            conn.commit()
            return cursor.rowcount > 0

    def build_policy(
        self,
        product_category: str = "",
        script_style: str = "",
    ) -> Dict[str, Any]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT * FROM user_script_feedback
                WHERE source = 'user'
                  AND (product_category = '' OR ? = '' OR product_category = ?)
                  AND (script_style = '' OR ? = '' OR script_style = ?)
                ORDER BY id
                """,
                (product_category, product_category, script_style, script_style),
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
            rule = {
                "id": key,
                "text": str(evidence[0]["rule_text"]),
                "status": status,
                "distinct_video_count": distinct_videos,
                "feedback_count": len(evidence),
                "source": "explicit_user_feedback",
            }
            rules.append(rule)
            for row in evidence:
                try:
                    script = json.loads(str(row["script_json"] or "{}"))
                except json.JSONDecodeError:
                    script = {}
                example = {
                    "rule_id": key,
                    "rule_status": status,
                    "video_id": str(row["video_id"]),
                    "user_comment": str(row["user_comment"] or ""),
                    "script": script,
                }
                (positive_examples if row["verdict"] == "satisfied" else negative_examples).append(example)

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


def candidate_preference_score(candidate: Dict[str, Any], policy: Dict[str, Any]) -> float:
    """Rank by active user examples only; never turn this score into a hard gate."""
    text = "".join(
        str(segment.get("cue") or "")
        for segment in candidate.get("segments") or []
        if isinstance(segment, dict)
    ) + str(candidate.get("outro_cue") or "")
    positives = [
        example for example in policy.get("positive_examples") or []
        if example.get("rule_status") == "active"
    ]
    negatives = [
        example for example in policy.get("negative_examples") or []
        if example.get("rule_status") == "active"
    ]

    def example_text(example: Dict[str, Any]) -> str:
        script = example.get("script") or {}
        return str(script.get("voiceover_full") or "".join(script.get("voiceover_cues") or []))

    positive = max((_similarity(text, example_text(item)) for item in positives), default=0.0)
    negative = max((_similarity(text, example_text(item)) for item in negatives), default=0.0)
    return round(positive - negative, 4)


def _load_script(path: Optional[str]) -> Dict[str, Any]:
    if not path:
        return {}
    return json.loads(Path(path).expanduser().read_text(encoding="utf-8"))


def _script_for_video(video: str, explicit_script: Optional[str]) -> Dict[str, Any]:
    if explicit_script:
        return _load_script(explicit_script)
    video_path = Path(video).expanduser()
    sidecar = video_path.with_suffix(".script.json")
    return _load_script(str(sidecar)) if sidecar.is_file() else {}


def main() -> int:
    parser = argparse.ArgumentParser(description="记录使用者真实脚本质量反馈")
    parser.add_argument("--video", required=True, help="成片路径或稳定 video_id")
    parser.add_argument("--rule", required=True, help="使用者原文规则，不由程序改写")
    parser.add_argument("--verdict", required=True, choices=sorted(VALID_VERDICTS))
    parser.add_argument("--comment", default="")
    parser.add_argument("--script-json")
    parser.add_argument("--product-category", default="")
    parser.add_argument("--script-style", default="")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args()

    video_id = str(Path(args.video).expanduser().resolve()) if Path(args.video).expanduser().exists() else args.video
    stored = ScriptFeedbackStore(args.db).record_feedback(
        video_id=video_id,
        rule_text=args.rule,
        verdict=args.verdict,
        source="user",
        user_comment=args.comment,
        script=_script_for_video(args.video, args.script_json),
        product_category=args.product_category,
        script_style=args.script_style,
    )
    print("已记录使用者脚本反馈" if stored else "该反馈已存在")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
