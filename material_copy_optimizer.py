"""Material-derived sales-copy evaluation without product-category vocabularies."""

from __future__ import annotations

import math
import re
from typing import Any, Dict, Iterable, List, Mapping, Sequence


DEVICE_PATTERNS = {
    "contrast": re.compile(r"居然|原来|没想到|竟然|不是.+而是|既.+又"),
    "question": re.compile(r"[？?]|为什么|怎么|什么|吗$"),
    "curiosity": re.compile(r"到底|关键|先看|再看|从.+看起|你见过|你发现"),
    "proof": re.compile(r"采用|来自|源自|原料|工艺|成分|参数|规格|标注"),
    "reason": re.compile(r"因为|所以|靠|离不开|关键在|原因是|这一步"),
    "convenience": re.compile(r"方便|省事|省心|随手|直接|控制|一按|一推|一拉|开盖|随时"),
    "reveal": re.compile(r"先看|再看|原来|这里|这一点|重点是"),
    "action": re.compile(r"试试|入手|了解|去看|看看|带走|开始|选择"),
}

SCENE_DESCRIPTION_PATTERNS = re.compile(
    r"(?:你(?:现在)?看到的|画面(?:里|中)|镜头(?:里|中)|眼前(?:是|有)|这里(?:是|有))"
)
FORMAL_DESCRIPTION_PATTERNS = re.compile(
    r"(?:画面|镜头|呈现|可见|位于|摆放|展示(?:了|着)|前景|远景|由此可见)"
)
OPEN_LOOP_PATTERNS = re.compile(r"为什么|怎么|到底|关键|先别|没想到|你见过|是不是|居然|原来")
CONVERSATIONAL_PATTERNS = re.compile(r"你|我|咱们|是不是|想喝|想用|平时|随手|直接|先别|说真的")


def _normalize(text: Any) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]", "", str(text or "")).lower()


def _speech_units(text: str) -> int:
    chinese = len(re.findall(r"[\u4e00-\u9fff]", str(text or "")))
    latin = re.findall(r"[A-Za-z0-9]+", str(text or ""))
    return chinese + sum(max(1, math.ceil(len(token) / 4)) for token in latin)


def _ngrams(text: str, size: int = 2) -> set[str]:
    normalized = _normalize(text)
    if not normalized:
        return set()
    if len(normalized) <= size:
        return {normalized}
    return {normalized[index:index + size] for index in range(len(normalized) - size + 1)}


def semantic_overlap(left: Any, right: Any) -> float:
    left_text = _normalize(left)
    right_text = _normalize(right)
    if not left_text or not right_text:
        return 0.0
    if left_text in right_text or right_text in left_text:
        return min(1.0, min(len(left_text), len(right_text)) / max(len(left_text), len(right_text)) + 0.35)
    left_grams = _ngrams(left_text)
    right_grams = _ngrams(right_text)
    return len(left_grams & right_grams) / max(len(left_grams | right_grams), 1)


def _anchors(contract: Mapping[str, Any]) -> Dict[str, str]:
    structured = contract.get("evidence_anchors") or []
    anchors = {
        str(item.get("id")): str(item.get("text") or "")
        for item in structured
        if isinstance(item, Mapping) and item.get("id") and str(item.get("text") or "").strip()
    }
    if anchors:
        return anchors
    for prefix, values in (
        ("visual", contract.get("visible_anchors") or []),
        ("fact", contract.get("verified_fact_phrases") or []),
    ):
        for index, value in enumerate(values):
            text = str(value or "").strip()
            if text:
                anchors[f"{prefix}:{index}"] = text
    return anchors


def _infer_device(text: str, intent: str) -> str:
    matches = [name for name, pattern in DEVICE_PATTERNS.items() if pattern.search(text)]
    if intent == "hook":
        if "contrast" in matches and "question" in matches:
            return "contrast_question"
        if "contrast" in matches:
            return "contrast"
        if "curiosity" in matches:
            return "curiosity"
        if "question" in matches:
            return "question"
    if intent == "cta" and "action" in matches:
        return "action"
    for preferred in ("convenience", "proof", "reason", "reveal", "contrast", "question", "action"):
        if preferred in matches:
            return preferred
    return "description"


def _allowed_devices(contract: Mapping[str, Any], intent: str) -> set[str]:
    configured = {
        str(value)
        for value in contract.get("allowed_marketing_devices") or []
        if str(value).strip()
    }
    if configured:
        return configured
    return {
        "hook": {"contrast_question", "contrast", "curiosity"},
        "proof": {"proof", "reason", "reveal"},
        "value": {"convenience", "reason", "proof", "reveal", "contrast"},
        "cta": {"action"},
    }.get(intent, {"reason", "proof", "convenience", "reveal"})


def evaluate_candidate(
    candidate: Mapping[str, Any],
    contract: Mapping[str, Any],
    neighbor_copy: Sequence[str] = (),
) -> Dict[str, Any]:
    """Evaluate one candidate against material-derived evidence and marketing intent."""
    text = str(candidate.get("voiceover") or candidate.get("subtitle") or "").strip()
    intent = str(contract.get("marketing_intent") or "value").lower()
    max_units = int(contract.get("max_voiceover_units") or 0)
    units = _speech_units(text)
    errors: List[str] = []
    if not text:
        errors.append("口播为空")
    if max_units > 0 and units > max_units:
        errors.append(f"口播 {units} 单位超过预算 {max_units}")

    anchors = _anchors(contract)
    supplied_refs = [str(value) for value in candidate.get("evidence_refs") or []]
    invalid_refs = [value for value in supplied_refs if value not in anchors]
    if invalid_refs:
        errors.append("引用了不存在的素材证据：" + ", ".join(invalid_refs))
    anchor_scores = {
        anchor_id: semantic_overlap(text, anchor_text)
        for anchor_id, anchor_text in anchors.items()
    }
    inferred_refs = [
        anchor_id
        for anchor_id, score in sorted(anchor_scores.items(), key=lambda item: item[1], reverse=True)
        if score >= 0.16
    ][:2]
    evidence_refs = [value for value in supplied_refs if value in anchors] or inferred_refs
    evidence_alignment = max(
        (anchor_scores.get(anchor_id, 0.0) for anchor_id in evidence_refs),
        default=0.0,
    )
    if anchors and not evidence_refs:
        errors.append("文案没有引用当前素材中的具体证据")

    supplied_device = str(candidate.get("marketing_device") or "").strip().lower()
    inferred_device = _infer_device(text, intent)
    device = supplied_device or inferred_device
    allowed_devices = _allowed_devices(contract, intent)
    if device not in allowed_devices:
        errors.append(f"营销功能 {device or 'missing'} 不适合 {intent} 段")

    buyer_value = str(candidate.get("buyer_value") or "").strip()
    requires_buyer_value = bool(
        contract.get("requires_buyer_value", intent in {"value", "proof"})
    )
    if not buyer_value and device == "convenience":
        buyer_value = text
    if requires_buyer_value and not buyer_value:
        errors.append("没有说明该素材信息对购买选择的价值")
    buyer_value_alignment = semantic_overlap(text, buyer_value) if buyer_value else 0.0
    if requires_buyer_value and buyer_value and inferred_device not in allowed_devices:
        errors.append("购买价值只写在元数据里，口播正文没有表达")
    if requires_buyer_value and SCENE_DESCRIPTION_PATTERNS.search(text):
        errors.append("口播仍是画面描述，没有把素材证据转成购买理由")

    required_continuity = contract.get("required_continuity_from")
    continuity_from = candidate.get("continuity_from")
    if required_continuity is not None and continuity_from is None:
        errors.append(f"没有声明如何承接段 {required_continuity}")
    continuity_match = (
        required_continuity is None
        or (
            continuity_from is not None
            and int(continuity_from) == int(required_continuity)
        )
    )
    if not continuity_match:
        errors.append(f"没有承接段 {required_continuity}")

    repetition = max((semantic_overlap(text, neighbor) for neighbor in neighbor_copy), default=0.0)
    if repetition >= 0.82:
        errors.append("与相邻口播重复，缺少信息增量")

    inferred_buyer_value = not bool(candidate.get("buyer_value")) and bool(buyer_value)
    score = 0.08
    score += (0.27 if supplied_device else 0.16) if device in allowed_devices else 0.0
    score += (0.14 if supplied_refs else 0.08) if evidence_refs else 0.0
    score += 0.16 * evidence_alignment
    score += (
        0.20 if candidate.get("buyer_value")
        else 0.07 if inferred_buyer_value
        else 0.16 if not requires_buyer_value
        else 0.0
    )
    score += (
        0.08 if required_continuity is None or continuity_from is not None
        else 0.03
    ) if continuity_match else 0.0
    score += 0.07 * (1.0 - repetition)
    threshold = float(contract.get("min_quality_score") or 0.58)
    score = round(max(0.0, min(1.0, score)), 3)
    return {
        "passed": not errors and score >= threshold,
        "score": score,
        "threshold": threshold,
        "errors": errors,
        "voiceover_units": units,
        "marketing_device": device,
        "buyer_value": buyer_value,
        "buyer_value_alignment": round(buyer_value_alignment, 3),
        "evidence_refs": evidence_refs,
        "evidence_alignment": round(evidence_alignment, 3),
        "continuity_from": continuity_from,
        "repetition": round(repetition, 3),
        "metadata_inferred": {
            "marketing_device": not bool(supplied_device),
            "evidence_refs": not bool(supplied_refs),
            "buyer_value": inferred_buyer_value,
        },
    }


def evaluate_script(
    segments: Sequence[Mapping[str, Any]],
    contracts: Mapping[str, Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    """Evaluate candidate quality and information gain across the full sales arc."""
    violations: List[Dict[str, Any]] = []
    texts = [str(segment.get("voiceover") or segment.get("subtitle") or "") for segment in segments]
    for index, segment in enumerate(segments):
        contract = contracts.get(str(index)) or {}
        neighbors = [
            texts[neighbor]
            for neighbor in (index - 1, index + 1)
            if 0 <= neighbor < len(texts)
        ]
        evaluation = evaluate_candidate(segment, contract, neighbors)
        if not evaluation["passed"]:
            reasons = list(evaluation["errors"])
            if evaluation["score"] < evaluation["threshold"]:
                reasons.append(
                    f"综合营销质量 {evaluation['score']:.2f} 低于 {evaluation['threshold']:.2f}"
                )
            violations.append({
                "segment": index,
                "reason": "；".join(reasons),
                "evaluation": evaluation,
            })
    return violations


def evaluate_viral_script(
    segments: Sequence[Mapping[str, Any]],
    creative_blueprint: Mapping[str, Any],
    outro_cue: str = "",
    external_cta: bool = False,
) -> Dict[str, Any]:
    """Score sales creativity independently from factual correctness."""
    texts = [
        str(segment.get("voiceover") or segment.get("cue") or segment.get("subtitle") or "").strip()
        for segment in segments
    ]
    if not texts or any(not text for text in texts):
        return {"passed": False, "score": 0.0, "threshold": 0.68, "errors": ["创意路线缺少完整口播"]}

    hook = texts[0]
    hook_device = _infer_device(hook, "hook")
    scroll_stop = 0.15
    if hook_device in {"contrast_question", "contrast", "curiosity", "question"}:
        scroll_stop += 0.35
    if OPEN_LOOP_PATTERNS.search(hook):
        scroll_stop += 0.30
    if 7 <= _speech_units(hook) <= 30:
        scroll_stop += 0.20

    joined = "".join(texts)
    average_units = sum(_speech_units(text) for text in texts) / len(texts)
    naturalness = 0.15
    if CONVERSATIONAL_PATTERNS.search(joined):
        naturalness += 0.35
    if 8 <= average_units <= 28:
        naturalness += 0.25
    if re.search(r"[，。！？；,.!?]", joined):
        naturalness += 0.15
    description_hits = len(FORMAL_DESCRIPTION_PATTERNS.findall(joined)) + len(SCENE_DESCRIPTION_PATTERNS.findall(joined))
    naturalness += max(0.0, 0.10 - 0.05 * description_hits)

    inferred_devices = [
        _infer_device(text, str(segment.get("marketing_intent") or "value"))
        for text, segment in zip(texts, segments)
    ]
    useful_devices = {value for value in inferred_devices if value != "description"}
    device_progression = min(1.0, len(useful_devices) / min(4, len(texts)))
    adjacent_overlap = [semantic_overlap(texts[index], texts[index + 1]) for index in range(len(texts) - 1)]
    information_gain = 1.0 - (sum(adjacent_overlap) / len(adjacent_overlap) if adjacent_overlap else 0.0)
    progression = max(0.0, min(1.0, 0.55 * device_progression + 0.45 * information_gain))

    value_segments = [
        (text, segment)
        for text, segment in zip(texts, segments)
        if str(segment.get("marketing_intent") or "value") in {"value", "proof"}
        and bool(segment.get("requires_buyer_value", True))
    ]
    translated = sum(
        1
        for text, segment in value_segments
        if _infer_device(text, str(segment.get("marketing_intent") or "value")) != "description"
        and not SCENE_DESCRIPTION_PATTERNS.search(text)
    )
    evidence_to_value = translated / len(value_segments) if value_segments else 1.0

    ending = str(outro_cue or texts[-1])
    cta_action = bool(DEVICE_PATTERNS["action"].search(ending))
    if external_cta:
        main_close_has_value = _infer_device(texts[-1], "value") in {
            "convenience", "proof", "reason", "reveal", "contrast",
        }
        conversion = 0.55 * float(main_close_has_value) + 0.45 * float(cta_action)
    else:
        conversion = float(cta_action)

    mechanics = creative_blueprint.get("creative_mechanics") or {}
    reference_alignment = 0.5
    if str(mechanics.get("hook_mechanism") or "") in {"question", "contrast_question", "curiosity_gap"}:
        reference_alignment += 0.25 * float(hook_device in {"question", "contrast_question", "curiosity", "contrast"})
    if str(mechanics.get("proof_pattern") or "") not in {"", "none"}:
        reference_alignment += 0.25 * float(any(device in {"proof", "reason", "reveal"} for device in inferred_devices))
    reference_alignment = min(1.0, reference_alignment)

    dimensions = {
        "scroll_stop": round(min(1.0, scroll_stop), 3),
        "spoken_naturalness": round(min(1.0, naturalness), 3),
        "information_progression": round(progression, 3),
        "evidence_to_buyer_value": round(evidence_to_value, 3),
        "conversion_close": round(conversion, 3),
        "reference_mechanism_alignment": round(reference_alignment, 3),
    }
    weights = {
        "scroll_stop": 0.22,
        "spoken_naturalness": 0.18,
        "information_progression": 0.18,
        "evidence_to_buyer_value": 0.20,
        "conversion_close": 0.12,
        "reference_mechanism_alignment": 0.10,
    }
    score = round(sum(dimensions[key] * weight for key, weight in weights.items()), 3)
    threshold = 0.68
    errors = []
    if dimensions["scroll_stop"] < 0.60:
        errors.append("开头缺少能让人停留的具体悬念或反差")
    if dimensions["spoken_naturalness"] < 0.60:
        errors.append("口播偏书面或像素材说明，不像真人带货表达")
    if dimensions["evidence_to_buyer_value"] < 0.70:
        errors.append("素材证据没有充分转译成购买理由")
    if dimensions["conversion_close"] < 0.55:
        errors.append("结尾没有回收前文价值并自然推动行动")
    if score < threshold:
        errors.append(f"爆款创意综合分 {score:.2f} 低于 {threshold:.2f}")
    return {
        "passed": not errors,
        "score": score,
        "threshold": threshold,
        "dimensions": dimensions,
        "errors": errors,
    }
