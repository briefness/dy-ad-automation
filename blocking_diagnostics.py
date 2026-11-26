"""Structured, actionable diagnostics for strict local-asset blocking errors."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional


def _extract_gap_report_from_message(message: str) -> Optional[Dict[str, Any]]:
    match = re.search(r"报告已保存：([^\s，。]+?\.json)", str(message or ""))
    if not match:
        return None
    report_path = Path(match.group(1)).expanduser()
    if not report_path.is_file():
        return None
    try:
        data = json.loads(report_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def diagnose_blocking_error(
    exc: BaseException,
    *,
    message: Optional[str] = None,
) -> Dict[str, Any]:
    """Translate a blocking pipeline exception into structured, actionable repair guidance."""
    text = str(message if message is not None else exc).strip()
    raw_details = getattr(exc, "details", None)
    details: Dict[str, Any] = dict(raw_details) if isinstance(raw_details, dict) else {}
    if not details:
        loaded = _extract_gap_report_from_message(text)
        if loaded:
            details = loaded

    reason = str(details.get("reason") or "").strip().lower()
    actions: List[Dict[str, str]] = []
    category = "pipeline_blocked"
    summary = text

    # 1. Missing factual Evidence Anchor / unsupported product claims
    if (
        "Evidence Anchor" in text
        or "证据" in text and ("缺少" in text or "未核验" in text or "product:name" in text)
        or details.get("unsupported_claims")
    ):
        category = "missing_fact_evidence"
        summary = "脚本包含具体产品主张，但素材画面与已填产品资料中缺少可核验依据，系统按红线阻断。"
        claims = [str(c) for c in (details.get("unsupported_claims") or []) if str(c).strip()]
        if claims:
            summary += f"（未核验主张：{'、'.join(claims[:4])}）"
        actions.extend([
            {
                "label": "补充已核验卖点",
                "field": "verified_claims",
                "hint": "在项目简报「已核验卖点」中补充真实可证明的卖点。",
            },
            {
                "label": "补充原料 / 产地 / 工艺",
                "field": "ingredients",
                "hint": "如素材涉及配料、产地或工艺，请如实填写对应字段或提供清晰包装特写素材。",
            },
        ])

    # 2. Insufficient usable duration / missing narrative role footage
    elif (
        "无法完整覆盖" in text
        or "素材容量" in text
        or "低于阈值" in text
        or reason in {"insufficient_material_coverage", "plan_low_confidence", "insufficient_capacity"}
        or details.get("missing_duration") is not None
    ):
        category = "insufficient_material_capacity"
        missing_dur = details.get("missing_duration")
        seg_idx = details.get("semantic_segment")
        summary = "本地素材中与当前脚本语义匹配的可用画面不足，系统拒绝循环、拉伸或用无关镜头兜底。"
        if seg_idx is not None and missing_dur:
            summary = (
                f"第 {int(seg_idx) + 1} 段缺少约 {float(missing_dur):.1f}s 语义匹配素材，"
                "系统拒绝拉伸或用无关画面兜底。"
            )
        actions.extend([
            {
                "label": "清空或缩短目标时长",
                "field": "target_duration",
                "hint": "留空「目标时长」让素材自然容量决定成片长度，或调小目标秒数。",
            },
            {
                "label": "补充对应镜头素材",
                "field": "asset_path",
                "hint": "在素材目录中补充该叙事段落对应的清晰、无重复视频镜头后重新预检。",
            },
        ])

    # 3. Continuous voiceover / TTS contract or missing audio
    elif "口播" in text or "TTS" in text or "音频" in text:
        category = "audio_or_voiceover_blocked"
        summary = "单条连续口播或音画时间轴未能形成一致映射，系统阻断以防止声画错位。"
        actions.append({
            "label": "检查口播配置或语速",
            "field": "voice",
            "hint": "确认 TTS 服务可用，或放宽目标时长让单条连续口播自然落位。",
        })

    # 4. Vision backend or unreadable footage
    elif "视觉" in text or "有效视频窗口" in text or "抽帧" in text:
        category = "vision_or_footage_invalid"
        summary = "素材视觉理解未就绪或视频画面质量/清晰度不达标。"
        actions.append({
            "label": "检查素材目录与环境",
            "field": "asset_path",
            "hint": "确认目录内视频可正常播放、画面清晰，且视觉模型 API 配置正确。",
        })

    # 5. Generic contract / quality gate failure
    else:
        actions.append({
            "label": "查看详细日志与预检报告",
            "field": "extra_requirements",
            "hint": "检查下方原始日志中的具体阻断段与约束原因，调整事实资料或素材后重试。",
        })

    return {
        "category": category,
        "summary": summary,
        "raw_error": text,
        "actions": actions,
        "missing_duration": details.get("missing_duration"),
        "semantic_segment": details.get("semantic_segment"),
        "unsupported_claims": details.get("unsupported_claims") or [],
    }
