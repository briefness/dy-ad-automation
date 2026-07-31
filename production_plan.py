"""Canonical, auditable production plan for local-asset videos."""

from __future__ import annotations

import hashlib
import html
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


PRODUCTION_PLAN_VERSION = 1
ENTITY_LEDGER_VERSION = 1

FACT_FIELDS = {
    "ingredient": ("ingredients", "raw_materials"),
    "origin": ("origin",),
    "craft": ("production_process",),
    "selling_point": ("selling_point", "verified_claims"),
    "feature": ("specifications",),
}
ROLE_FACT_TYPES = {
    "ingredient": "ingredient",
    "origin": "origin",
    "production": "craft",
    "craft": "craft",
    "selling_point": "selling_point",
    "benefit": "selling_point",
    "feature": "feature",
    "proof": "selling_point",
    "result": "selling_point",
}
GENERIC_EMPHASIS_TERMS = frozenset({
    "产地", "成分", "配料", "原料", "工艺", "卖点", "特色", "特点", "亮点",
    "产品", "品质", "优势", "功效", "效果", "口感", "风味", "香气",
})
ENTITY_QUALIFIERS = (
    "种植株", "植株", "植物", "花朵", "鲜花", "干花", "叶片", "果实", "原料", "成分",
)
SENSORY_SUFFIXES = ("花香", "香气", "香味", "风味", "口感", "味道")
FACT_STICKER_KINDS = {
    "ingredient": "ingredient",
    "origin": "origin",
    "craft": "craft",
    "selling_point": "selling_point",
    "proof": "selling_point",
}


class ProductionPlanValidationError(ValueError):
    """Raised when a local production plan violates a blocking invariant."""


def _compact(value: Any) -> str:
    return "".join(re.findall(r"[0-9A-Za-z\u4e00-\u9fff]+", str(value or ""))).lower()


def _alias_key(value: Any) -> str:
    normalized = _compact(value)
    changed = True
    while changed and normalized:
        changed = False
        for qualifier in ENTITY_QUALIFIERS:
            compact_qualifier = _compact(qualifier)
            if normalized.endswith(compact_qualifier) and len(normalized) > len(compact_qualifier):
                normalized = normalized[:-len(compact_qualifier)]
                changed = True
                break
    return normalized


def _related_entities(left: Any, right: Any) -> bool:
    left_key = _alias_key(left)
    right_key = _alias_key(right)
    if not left_key or not right_key:
        return False
    if left_key == right_key:
        return True
    return min(len(left_key), len(right_key)) >= 2 and (
        left_key in right_key or right_key in left_key
    )


def _iter_fact_values(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        for nested in value.values():
            yield from _iter_fact_values(nested)
        return
    if isinstance(value, (list, tuple, set)):
        for nested in value:
            yield from _iter_fact_values(nested)
        return
    if value is None:
        return
    for part in re.split(r"[、,，;；|｜\n]+", str(value)):
        normalized = part.strip()
        if normalized:
            yield normalized


def _json_digest(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _unique_strings(values: Iterable[Any]) -> List[str]:
    return list(dict.fromkeys(
        str(value).strip() for value in values if str(value or "").strip()
    ))


def build_product_entity_ledger(
    product_info: Dict[str, Any],
    selected_segments: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Merge trusted facts and verified visual aliases without product-specific rules."""
    entities: List[Dict[str, Any]] = []

    def add(
        fact_type: str,
        value: str,
        *,
        source: str,
        confidence: float,
        evidence_ref: str,
        trusted: bool,
    ) -> None:
        normalized = str(value or "").strip()
        if len(_compact(normalized)) < 2:
            return
        entry = next(
            (
                item for item in entities
                if item["fact_type"] == fact_type
                and any(_related_entities(normalized, alias) for alias in item["aliases"])
            ),
            None,
        )
        if entry is None:
            entry = {
                "id": "",
                "canonical": normalized,
                "aliases": [],
                "fact_type": fact_type,
                "confidence": 0.0,
                "provenance": [],
                "conflicts": [],
            }
            entities.append(entry)
        if normalized not in entry["aliases"]:
            entry["aliases"].append(normalized)
        if trusted:
            trusted_aliases = {
                item["value"] for item in entry["provenance"] if item.get("trusted")
            }
            if not trusted_aliases:
                entry["canonical"] = normalized
        entry["confidence"] = round(max(float(entry["confidence"]), confidence), 3)
        provenance = {
            "source": source,
            "evidence_ref": evidence_ref,
            "value": normalized,
            "confidence": round(max(0.0, min(1.0, confidence)), 3),
            "trusted": trusted,
        }
        if provenance not in entry["provenance"]:
            entry["provenance"].append(provenance)

    for fact_type, fields in FACT_FIELDS.items():
        for field in fields:
            for index, fact in enumerate(_iter_fact_values(product_info.get(field))):
                add(
                    fact_type,
                    fact,
                    source=f"product_info.{field}",
                    confidence=1.0,
                    evidence_ref=f"product:{field}:{index}",
                    trusted=True,
                )

    for segment in selected_segments or []:
        role = str(segment.get("product_story_role") or "").lower()
        fact_type = ROLE_FACT_TYPES.get(role)
        if not fact_type or not segment.get("product_relationship_verified"):
            continue
        analysis = segment.get("analysis") or {}
        aliases = _unique_strings([
            *(segment.get("matched_product_entities") or []),
            *(analysis.get("matched_product_entities") or []),
        ])
        confidence = float(
            segment.get("product_relationship_confidence")
            or analysis.get("product_relationship_confidence")
            or 0.75
        )
        window_id = str(segment.get("window_id") or segment.get("source_video") or "unknown")
        relationship_source = str(
            segment.get("product_relationship_source")
            or analysis.get("product_relationship_source")
            or "verified_visual_relationship"
        )
        for alias in aliases:
            add(
                fact_type,
                alias,
                source="verified_visual_relationship",
                confidence=confidence,
                evidence_ref=f"material:{window_id}:{relationship_source}",
                trusted=False,
            )

    order = {name: index for index, name in enumerate(FACT_FIELDS)}
    entities.sort(key=lambda item: (order.get(item["fact_type"], 99), item["canonical"]))
    for index, entry in enumerate(entities):
        entry["id"] = f"entity-{entry['fact_type']}-{index}"
        canonical = entry["canonical"]
        entry["aliases"] = [canonical, *sorted(
            (alias for alias in entry["aliases"] if alias != canonical),
            key=lambda alias: (len(_compact(alias)), alias),
        )]
        entry["allowed_claims"] = [canonical]
    return {
        "version": ENTITY_LEDGER_VERSION,
        "source": "trusted_product_facts_and_verified_visual_relationships",
        "entities": entities,
    }


def build_local_production_plan(
    *,
    ad_script: Dict[str, Any],
    selected_segments: List[Dict[str, Any]],
    postproduction_contract: Dict[str, Any],
    entity_ledger: Dict[str, Any],
    subtitles: Optional[List[Dict[str, Any]]] = None,
    sticker_plan: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Create the single downstream-readable decision artifact for local footage."""
    scripts = {
        int(item.get("segment", index)): item
        for index, item in enumerate(ad_script.get("segments") or [])
    }
    post_by_edit = {
        int(item.get("edit_index", index)): item
        for index, item in enumerate(postproduction_contract.get("segments") or [])
    }
    segments = []
    for position, selected in enumerate(selected_segments):
        semantic_segment = int(selected.get("semantic_segment", selected.get("segment", position)))
        edit_index = int(selected.get("edit_index", position))
        script = scripts.get(semantic_segment) or {}
        post = post_by_edit.get(edit_index) or {}
        analysis = selected.get("analysis") or {}
        segments.append({
            "semantic_segment": semantic_segment,
            "edit_index": edit_index,
            "narrative": str(script.get("narrative") or selected.get("narrative") or ""),
            "marketing_intent": str(script.get("marketing_intent") or ""),
            "script_role": str(
                script.get("desired_product_story_role")
                or script.get("product_story_role")
                or ""
            ),
            "selected_role": str(selected.get("product_story_role") or "unknown"),
            "script_subtitle": str(script.get("subtitle") or ""),
            "script_voiceover": str(script.get("voiceover") or ""),
            "source_video": str(selected.get("source_video") or ""),
            "source_range_available": any(
                key in selected for key in ("source_start", "source_end", "start", "end")
            ),
            "source_start": float(selected.get("source_start", selected.get("start", 0.0)) or 0.0),
            "source_end": float(selected.get("source_end", selected.get("end", 0.0)) or 0.0),
            "target_duration": float(selected.get("target_duration") or 0.0),
            "clip_path": str(selected.get("clip_path") or ""),
            "contact_sheet": str(selected.get("contact_sheet") or ""),
            "relationship_verified": bool(
                selected.get("product_relationship_verified")
                or analysis.get("product_relationship_verified")
            ),
            "relationship_source": str(
                selected.get("product_relationship_source")
                or analysis.get("product_relationship_source")
                or ""
            ),
            "matched_product_facts": _unique_strings([
                *(selected.get("matched_product_facts") or []),
                *(analysis.get("matched_product_facts") or []),
            ]),
            "matched_product_entities": _unique_strings([
                *(selected.get("matched_product_entities") or []),
                *(analysis.get("matched_product_entities") or []),
            ]),
            "subtitle": deepcopy(post.get("subtitle") or {}),
        })
    segments.sort(key=lambda item: item["edit_index"])
    return {
        "version": PRODUCTION_PLAN_VERSION,
        "mode": "local_assets",
        "status": "planned",
        "lineage": {
            "product_entity_ledger_sha256": _json_digest(entity_ledger),
            "ad_script_sha256": _json_digest(ad_script),
            "selected_segments_sha256": _json_digest(selected_segments),
            "postproduction_contract_sha256": _json_digest(postproduction_contract),
        },
        "entity_ledger": deepcopy(entity_ledger),
        "segments": segments,
        "subtitles": deepcopy(subtitles or []),
        "stickers": deepcopy(sticker_plan or {}),
        "validation": [],
    }


def _violation(
    code: str,
    message: str,
    *,
    segment: Optional[int] = None,
    severity: str = "blocking",
) -> Dict[str, Any]:
    result = {"code": code, "severity": severity, "message": message}
    if segment is not None:
        result["segment"] = int(segment)
    return result


def _concrete_emphasis_terms(value: Dict[str, Any]) -> List[str]:
    return [
        term for term in _unique_strings(value.get("emphasis_terms") or [])
        if _compact(term) and _compact(term) not in {_compact(item) for item in GENERIC_EMPHASIS_TERMS}
    ]


def _subtitle_emphasis_violations(subtitle: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not subtitle.get("emphasis") and not subtitle.get("fancy"):
        return []
    segment = int(subtitle.get("segment", 0))
    terms = _unique_strings(subtitle.get("emphasis_terms") or [])
    concrete = _concrete_emphasis_terms(subtitle)
    if terms and not concrete:
        return [_violation(
            "generic_emphasis_term",
            "花字重点只能是具体实体、地点、工艺或卖点，不能只突出分类词",
            segment=segment,
        )]
    if not concrete:
        return [_violation(
            "missing_emphasis_term",
            "花字必须明确记录需要突出的具体词",
            segment=segment,
        )]
    text = _compact(subtitle.get("text"))
    if text and any(_compact(term) == text for term in concrete):
        return [_violation(
            "full_subtitle_emphasis",
            "整句字幕不能全部作为花字重点",
            segment=segment,
        )]
    return []


def _ledger_aliases(ledger: Dict[str, Any], fact_type: str) -> List[str]:
    return _unique_strings(
        alias
        for entity in ledger.get("entities") or []
        if entity.get("fact_type") == fact_type
        for alias in entity.get("aliases") or []
    )


def _sticker_item_violations(
    item: Dict[str, Any],
    ledger: Dict[str, Any],
) -> List[Dict[str, Any]]:
    segment = int(item.get("segment", 0))
    text = _compact(item.get("text"))
    source_subtitle = _compact(item.get("source_subtitle"))
    if not text:
        return [_violation("empty_sticker", "贴图文字不能为空", segment=segment)]
    if text in {_compact(term) for term in GENERIC_EMPHASIS_TERMS}:
        return [_violation(
            "generic_sticker_text",
            "贴图不能只显示产地、成分、卖点等分类词",
            segment=segment,
        )]
    if source_subtitle and text == source_subtitle:
        return [_violation(
            "sticker_repeats_subtitle",
            "贴图不能原样复述整句字幕",
            segment=segment,
        )]
    if not item.get("evidence_refs"):
        return [_violation(
            "sticker_missing_evidence",
            "贴图必须保留脚本或素材证据引用",
            segment=segment,
        )]
    kind = str(item.get("kind") or "")
    fact_type = FACT_STICKER_KINDS.get(kind)
    aliases = _ledger_aliases(ledger, fact_type) if fact_type else []
    if fact_type == "ingredient" and any(text.endswith(_compact(suffix)) for suffix in SENSORY_SUFFIXES):
        return [_violation(
            "sticker_fact_type_mismatch",
            "感官属性不能被标记为成分或原料",
            segment=segment,
        )]
    if aliases and not any(_compact(alias) in text for alias in aliases):
        return [_violation(
            "sticker_fact_not_in_ledger",
            "事实型贴图必须对应实体证据账本中的同类事实",
            segment=segment,
        )]
    return []


def validate_local_production_plan(plan: Dict[str, Any]) -> List[Dict[str, Any]]:
    violations: List[Dict[str, Any]] = []
    segments = sorted(plan.get("segments") or [], key=lambda item: int(item.get("edit_index", 0)))
    edit_indices = [int(item.get("edit_index", 0)) for item in segments]
    if len(edit_indices) != len(set(edit_indices)):
        violations.append(_violation("duplicate_edit_index", "成片计划存在重复的实际镜头索引"))
    if segments:
        opening = segments[0]
        selected_role = str(opening.get("selected_role") or "")
        script_role = str(opening.get("script_role") or "")
        if (
            selected_role in {"ingredient", "origin", "production", "craft"}
            and script_role
            and script_role != selected_role
        ):
            violations.append(_violation(
                "opening_role_mismatch",
                "开场选中了原料、产地或工艺镜头，但开场脚本没有要求该角色",
                segment=int(opening.get("semantic_segment", 0)),
            ))
    for segment in segments:
        index = int(segment.get("semantic_segment", 0))
        if (
            segment.get("source_range_available")
            and float(segment.get("source_end") or 0.0) <= float(segment.get("source_start") or 0.0)
        ):
            violations.append(_violation(
                "invalid_source_range",
                "素材截取结束时间必须晚于开始时间",
                segment=index,
            ))
        subtitle_contract = segment.get("subtitle") or {}
        violations.extend(_subtitle_emphasis_violations({
            "segment": index,
            "text": segment.get("script_subtitle"),
            **subtitle_contract,
        }))
    for subtitle in plan.get("subtitles") or []:
        violations.extend(_subtitle_emphasis_violations(subtitle))
    ledger = plan.get("entity_ledger") or {}
    for item in (plan.get("stickers") or {}).get("items") or []:
        violations.extend(_sticker_item_violations(item, ledger))
    unique = []
    seen = set()
    for violation in violations:
        key = (violation["code"], violation.get("segment"), violation["message"])
        if key not in seen:
            seen.add(key)
            unique.append(violation)
    return unique


def ensure_valid_local_production_plan(plan: Dict[str, Any]) -> Dict[str, Any]:
    plan = deepcopy(plan)
    plan["validation"] = validate_local_production_plan(plan)
    blocking = [item for item in plan["validation"] if item.get("severity") == "blocking"]
    if blocking:
        codes = ", ".join(item["code"] for item in blocking)
        raise ProductionPlanValidationError(f"本地素材成片计划校验失败：{codes}")
    plan["status"] = "validated"
    return plan


def sanitize_subtitle_emphasis(
    subtitles: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    sanitized = deepcopy(subtitles)
    violations: List[Dict[str, Any]] = []
    for subtitle in sanitized:
        item_violations = _subtitle_emphasis_violations(subtitle)
        if not item_violations:
            continue
        violations.extend(item_violations)
        subtitle["fancy"] = False
        subtitle["emphasis"] = False
        subtitle["emphasis_kind"] = "normal"
        subtitle["emphasis_topic"] = ""
        subtitle["emphasis_terms"] = []
    return sanitized, violations


def sanitize_postproduction_contract(
    contract: Dict[str, Any],
    ad_script: Dict[str, Any],
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Downgrade repairable emphasis errors before they reach the renderer."""
    sanitized = deepcopy(contract)
    scripts = {
        int(item.get("segment", index)): item
        for index, item in enumerate(ad_script.get("segments") or [])
    }
    violations: List[Dict[str, Any]] = []

    def sanitize_value(value: Dict[str, Any], segment: int) -> Dict[str, Any]:
        cue = {
            "segment": segment,
            "text": str((scripts.get(segment) or {}).get("subtitle") or ""),
            "fancy": bool(value.get("emphasis")),
            **value,
        }
        item_violations = _subtitle_emphasis_violations(cue)
        if not item_violations:
            return value
        violations.extend(item_violations)
        return {
            **value,
            "emphasis": False,
            "emphasis_kind": "normal",
            "emphasis_topic": "",
            "emphasis_terms": [],
        }

    for position, segment in enumerate(sanitized.get("segments") or []):
        semantic_segment = int(segment.get("semantic_segment", segment.get("segment", position)))
        segment["subtitle"] = sanitize_value(segment.get("subtitle") or {}, semantic_segment)
    for position, subtitle in enumerate(sanitized.get("semantic_subtitles") or []):
        semantic_segment = int(subtitle.get("semantic_segment", position))
        subtitle.update(sanitize_value(subtitle, semantic_segment))
    return sanitized, violations


def sanitize_sticker_plan(
    sticker_plan: Dict[str, Any],
    entity_ledger: Dict[str, Any],
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    sanitized = deepcopy(sticker_plan)
    kept = []
    violations: List[Dict[str, Any]] = []
    for item in sanitized.get("items") or []:
        item_violations = _sticker_item_violations(item, entity_ledger)
        if not item_violations:
            kept.append(item)
            continue
        violations.extend(item_violations)
        sanitized.setdefault("skipped", []).append({
            "segment": int(item.get("segment", 0)),
            "kind": str(item.get("kind") or ""),
            "text": str(item.get("text") or ""),
            "reason": "production_plan_invariant",
            "violation_codes": [violation["code"] for violation in item_violations],
        })
    sanitized["items"] = kept
    return sanitized, violations


def update_local_production_plan(
    plan: Dict[str, Any],
    *,
    subtitles: Optional[List[Dict[str, Any]]] = None,
    sticker_plan: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    updated = deepcopy(plan)
    if subtitles is not None:
        updated["subtitles"] = deepcopy(subtitles)
        updated["lineage"]["subtitles_sha256"] = _json_digest(subtitles)
    if sticker_plan is not None:
        updated["stickers"] = deepcopy(sticker_plan)
        updated["lineage"]["sticker_plan_sha256"] = _json_digest(sticker_plan)
    updated["validation"] = validate_local_production_plan(updated)
    blocking = [item for item in updated["validation"] if item.get("severity") == "blocking"]
    updated["status"] = "invalid" if blocking else "validated"
    return updated


def _timeline_review_html(plan: Dict[str, Any]) -> str:
    entity_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('fact_type') or ''))}</td>"
        f"<td>{html.escape(str(item.get('canonical') or ''))}</td>"
        f"<td>{html.escape(' / '.join(item.get('aliases') or []))}</td>"
        f"<td>{float(item.get('confidence') or 0.0):.2f}</td>"
        "</tr>"
        for item in (plan.get("entity_ledger") or {}).get("entities") or []
    )
    sticker_by_segment: Dict[int, List[Dict[str, Any]]] = {}
    for sticker in (plan.get("stickers") or {}).get("items") or []:
        sticker_by_segment.setdefault(int(sticker.get("segment", 0)), []).append(sticker)
    cards = []
    for segment in plan.get("segments") or []:
        index = int(segment.get("semantic_segment", 0))
        image = str(segment.get("contact_sheet") or "")
        image_markup = ""
        if image:
            try:
                image_markup = f'<img src="{html.escape(Path(image).expanduser().resolve().as_uri())}" alt="镜头 {index} 素材预览">'
            except ValueError:
                image_markup = ""
        emphasis = (segment.get("subtitle") or {}).get("emphasis_terms") or []
        stickers = "、".join(str(item.get("text") or "") for item in sticker_by_segment.get(index, []))
        cards.append(
            '<article class="shot">'
            f'<div class="shot-media">{image_markup}</div>'
            '<div class="shot-copy">'
            f'<div class="shot-index">镜头 {int(segment.get("edit_index", 0)) + 1} · 语义段 {index}</div>'
            f'<h2>{html.escape(str(segment.get("script_subtitle") or "未设置字幕"))}</h2>'
            f'<p>{html.escape(str(segment.get("script_voiceover") or ""))}</p>'
            '<dl>'
            f'<dt>脚本角色</dt><dd>{html.escape(str(segment.get("script_role") or "未指定"))}</dd>'
            f'<dt>素材角色</dt><dd>{html.escape(str(segment.get("selected_role") or "unknown"))}</dd>'
            f'<dt>来源</dt><dd>{html.escape(str(segment.get("source_video") or ""))} '
            f'{float(segment.get("source_start") or 0.0):.2f}-{float(segment.get("source_end") or 0.0):.2f}s</dd>'
            f'<dt>花字重点</dt><dd>{html.escape("、".join(emphasis) or "无")}</dd>'
            f'<dt>贴图</dt><dd>{html.escape(stickers or "无")}</dd>'
            f'<dt>实体证据</dt><dd>{html.escape("、".join(segment.get("matched_product_entities") or []) or "无")}</dd>'
            '</dl></div></article>'
        )
    violations = "".join(
        f'<li class="{html.escape(str(item.get("severity") or "warning"))}">'
        f'{html.escape(str(item.get("code") or ""))}：{html.escape(str(item.get("message") or ""))}</li>'
        for item in plan.get("validation") or []
    ) or "<li>无</li>"
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>成片时间线审阅</title>
<style>
* {{ box-sizing: border-box; }}
body {{ margin: 0; color: #171717; background: #f4f5f6; font: 14px/1.55 -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif; }}
header {{ padding: 24px max(24px, calc((100vw - 1120px) / 2)); color: #fff; background: #15191d; }}
header h1 {{ margin: 0 0 4px; font-size: 24px; letter-spacing: 0; }}
main {{ width: min(1120px, calc(100% - 32px)); margin: 20px auto 48px; }}
section {{ margin: 0 0 24px; }}
h2 {{ margin: 0 0 8px; font-size: 17px; letter-spacing: 0; }}
table {{ width: 100%; border-collapse: collapse; background: #fff; }}
th, td {{ padding: 10px 12px; border: 1px solid #d9dde1; text-align: left; }}
.shot {{ display: grid; grid-template-columns: minmax(220px, 38%) 1fr; margin-bottom: 12px; overflow: hidden; border: 1px solid #d9dde1; border-radius: 6px; background: #fff; }}
.shot-media {{ min-height: 180px; background: #e6e9ec; }}
.shot-media img {{ display: block; width: 100%; height: 100%; object-fit: cover; }}
.shot-copy {{ padding: 16px; }}
.shot-index {{ margin-bottom: 8px; color: #66707a; font-size: 12px; }}
dl {{ display: grid; grid-template-columns: 84px 1fr; gap: 5px 10px; margin: 14px 0 0; }}
dt {{ color: #66707a; }} dd {{ margin: 0; overflow-wrap: anywhere; }}
.blocking {{ color: #a51d1d; }}
@media (max-width: 680px) {{ .shot {{ grid-template-columns: 1fr; }} .shot-media {{ min-height: 150px; }} }}
</style>
</head>
<body>
<header><h1>成片时间线审阅</h1><div>计划版本 {PRODUCTION_PLAN_VERSION} · 状态 {html.escape(str(plan.get('status') or 'planned'))}</div></header>
<main>
<section><h2>语义校验</h2><ul>{violations}</ul></section>
<section><h2>产品实体证据</h2><table><thead><tr><th>类型</th><th>规范实体</th><th>别名</th><th>置信度</th></tr></thead><tbody>{entity_rows}</tbody></table></section>
<section><h2>完整时间线</h2>{''.join(cards)}</section>
</main>
</body>
</html>
"""


def write_production_plan_artifacts(
    plan: Dict[str, Any],
    json_path: Path,
    review_path: Path,
) -> Tuple[Path, Path]:
    json_path = Path(json_path)
    review_path = Path(review_path)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    review_path.parent.mkdir(parents=True, exist_ok=True)
    json_tmp = json_path.with_suffix(json_path.suffix + ".tmp")
    review_tmp = review_path.with_suffix(review_path.suffix + ".tmp")
    json_tmp.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    review_tmp.write_text(_timeline_review_html(plan), encoding="utf-8")
    json_tmp.replace(json_path)
    review_tmp.replace(review_path)
    return json_path, review_path


def replay_production_plan_fixture(path: Path) -> Dict[str, Any]:
    fixture = json.loads(Path(path).read_text(encoding="utf-8"))
    ledger = build_product_entity_ledger(
        fixture.get("product_info") or {},
        fixture.get("selected_segments") or [],
    )
    plan = build_local_production_plan(
        ad_script=fixture.get("ad_script") or {},
        selected_segments=fixture.get("selected_segments") or [],
        postproduction_contract=fixture.get("postproduction_contract") or {},
        entity_ledger=ledger,
        subtitles=fixture.get("subtitles") or [],
        sticker_plan=fixture.get("sticker_plan") or {},
    )
    violations = validate_local_production_plan(plan)
    return {
        "plan": plan,
        "violations": violations,
        "blocking_codes": list(dict.fromkeys(
            item["code"] for item in violations if item.get("severity") == "blocking"
        )),
        "expected_blocking_codes": fixture.get("expected_blocking_codes") or [],
    }
