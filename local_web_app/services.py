from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

from .validation import validate_asset_directory

_PREFLIGHT_CACHE: dict[str, dict[str, Any]] = {}


def parse_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in str(value or "").replace("，", ",").split(",") if item.strip()]


PRODUCT_FIELDS = ("name", "type", "selling_point", "verified_claims", "ingredients", "origin", "production_process", "audience", "extra_requirements")


def normalize_product(payload: dict[str, Any]) -> dict[str, Any]:
    product = {field: payload.get(field, "") for field in PRODUCT_FIELDS}
    for field in ("verified_claims", "ingredients", "production_process"):
        product[field] = parse_list(product[field])
    return product


def environment_check(output_dir: str | Path | None = None, asset_root: str | Path | None = None) -> dict[str, Any]:
    try:
        from config import LLM_ENABLED, LLM_BASE_URL, LLM_MODEL, LLM_API_KEY, VISION_ENABLED, VISION_BASE_URL, VISION_MODEL, VISION_API_KEY
    except Exception:
        LLM_ENABLED = VISION_ENABLED = False
        LLM_BASE_URL = VISION_BASE_URL = LLM_MODEL = VISION_MODEL = LLM_API_KEY = VISION_API_KEY = ""
    output = Path(output_dir or _output_dir()).expanduser()
    writable = False
    try:
        output.mkdir(parents=True, exist_ok=True)
        probe = output / ".local_web_write_test"
        probe.write_text("ok", encoding="utf-8"); probe.unlink()
        writable = True
    except OSError:
        writable = False
    return {
        "ffmpeg": {"ok": bool(shutil.which("ffmpeg")), "label": "FFmpeg"},
        "ffprobe": {"ok": bool(shutil.which("ffprobe")), "label": "FFprobe"},
        "vision": {"ok": bool(VISION_ENABLED and VISION_BASE_URL and VISION_MODEL and VISION_API_KEY), "label": "视觉理解"},
        "vision_api_key": {"ok": bool(VISION_API_KEY), "label": "视觉 API Key"},
        "llm": {"ok": bool(LLM_ENABLED and LLM_BASE_URL and LLM_MODEL and LLM_API_KEY), "label": "LLM 文案"},
        "llm_api_key": {"ok": bool(LLM_API_KEY), "label": "LLM API Key"},
        "tts": {"ok": bool(os.getenv("VOLC_API_KEY")), "label": "TTS（火山）"},
        "output_writable": {"ok": writable, "label": "输出目录可写"},
        "output_dir": str(output.resolve()),
    }


def _output_dir() -> Path:
    from config import OUTPUT_DIR
    return Path(OUTPUT_DIR)


def preflight_fingerprint(asset_path: str | Path, product_payload: dict[str, Any], asset_root: str | Path | None = None, output_dir: str | Path | None = None) -> str:
    path = validate_asset_directory(asset_path, asset_root, output_dir)
    payload = {"path": str(path), "product": normalize_product(product_payload)}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def build_preflight(asset_path: str | Path, product_payload: dict[str, Any], requested_duration: Any = None, preview: bool = False, asset_root: str | Path | None = None, output_dir: str | Path | None = None) -> dict[str, Any]:
    path = validate_asset_directory(asset_path, asset_root, output_dir)
    product = normalize_product(product_payload)
    key = preflight_fingerprint(path, product, asset_root, output_dir)
    if key in _PREFLIGHT_CACHE:
        return {**_PREFLIGHT_CACHE[key], "cached": True}
    from local_asset_pipeline import build_local_asset_index, build_local_asset_story_contract
    index = build_local_asset_index(path)
    contract = build_local_asset_story_contract(index, product, requested_duration=requested_duration, preview=preview)
    coverage = index.get("coverage") or {}
    sources = index.get("sources") or []
    roles = contract.get("roles") or sorted({str(item.get("product_story_role")) for item in contract.get("narrative_plan", []) if item.get("product_story_role")})
    recommendations = {"target_duration": contract.get("natural_main_duration"), "video_style": "auto", "rhythm_style": "moderate", "voiceover": True, "voice": "auto", "stickers": "auto"}
    result = {"key": key, "cached": False, "coverage": coverage, "source_count": len(sources), "window_count": len(index.get("windows") or []), "natural_main_duration": contract.get("natural_main_duration"), "recommended_segments": contract.get("recommended_segments"), "roles": roles, "warnings": index.get("warnings", []) or [], "recommendations": recommendations, "recommended_values": recommendations, "index": index, "contract": contract}
    _PREFLIGHT_CACHE[key] = result
    return result
