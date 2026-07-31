"""Stage lineage and invalidation reporting for local-asset runs."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence


RUN_MANIFEST_VERSION = 1
STAGE_VERSION = 1
STAGE_DEPENDENCIES = {
    "asset_understanding": (),
    "product_entities": ("asset_understanding",),
    "script": ("asset_understanding", "product_entities"),
    "edit_plan": ("asset_understanding", "product_entities", "script"),
    "subtitles": ("script", "edit_plan"),
    "stickers": ("product_entities", "script", "edit_plan", "subtitles"),
    "render": ("edit_plan", "subtitles", "stickers"),
}


def _digest(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _artifact_record(value: Any) -> Dict[str, Any]:
    path = Path(value).expanduser()
    exists = path.is_file()
    record: Dict[str, Any] = {"path": str(path), "exists": exists}
    if exists:
        stat = path.stat()
        record.update({"size": stat.st_size, "mtime_ns": stat.st_mtime_ns})
    return record


def _stage_status(artifacts: Sequence[Dict[str, Any]]) -> str:
    if not artifacts:
        return "observed"
    existing = sum(bool(item["exists"]) for item in artifacts)
    if existing == len(artifacts):
        return "completed"
    return "partial" if existing else "missing"


def load_run_manifest(path: Path) -> Dict[str, Any]:
    path = Path(path)
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def build_run_manifest(
    *,
    stage_inputs: Mapping[str, Any],
    stage_artifacts: Optional[Mapping[str, Iterable[Any]]] = None,
    previous_manifest: Optional[Mapping[str, Any]] = None,
    review_artifact: Optional[Path] = None,
) -> Dict[str, Any]:
    """Build a deterministic comparison without pretending to execute cache reuse."""
    previous_stages = (previous_manifest or {}).get("stages") or {}
    artifact_map = stage_artifacts or {}
    stages: Dict[str, Dict[str, Any]] = {}
    directly_changed = []
    directly_invalidated = []

    for name, dependencies in STAGE_DEPENDENCIES.items():
        direct_fingerprint = _digest(stage_inputs.get(name))
        dependency_fingerprints = {
            dependency: stages[dependency]["fingerprint"]
            for dependency in dependencies
        }
        fingerprint = _digest({
            "version": STAGE_VERSION,
            "direct_input_fingerprint": direct_fingerprint,
            "dependency_fingerprints": dependency_fingerprints,
        })
        artifacts = [_artifact_record(path) for path in artifact_map.get(name, ()) if path]
        previous = previous_stages.get(name) or {}
        if not previous:
            reason = "missing_previous_stage"
        elif int(previous.get("version") or 0) != STAGE_VERSION:
            reason = "stage_version_changed"
        elif previous.get("direct_input_fingerprint") != direct_fingerprint:
            reason = "direct_input_changed"
        else:
            reason = ""
        if reason:
            directly_changed.append(name)
        status = _stage_status(artifacts)
        if not reason and status in {"missing", "partial"}:
            reason = "artifact_missing"
        if reason:
            directly_invalidated.append(name)
        stages[name] = {
            "version": STAGE_VERSION,
            "direct_input_fingerprint": direct_fingerprint,
            "dependency_fingerprints": dependency_fingerprints,
            "fingerprint": fingerprint,
            "artifacts": artifacts,
            "status": status,
            "invalidation_reason": reason,
        }

    invalidated = set(directly_invalidated)
    for name, dependencies in STAGE_DEPENDENCIES.items():
        changed_dependencies = [dependency for dependency in dependencies if dependency in invalidated]
        if changed_dependencies:
            invalidated.add(name)
            if not stages[name]["invalidation_reason"]:
                stages[name]["invalidation_reason"] = (
                    "dependency_changed:" + ",".join(changed_dependencies)
                )

    invalidated_stages = [name for name in STAGE_DEPENDENCIES if name in invalidated]
    reusable_stages = [
        name for name in STAGE_DEPENDENCIES
        if name not in invalidated and stages[name]["status"] in {"completed", "observed"}
    ]
    review_path = Path(review_artifact).expanduser() if review_artifact else None
    return {
        "version": RUN_MANIFEST_VERSION,
        "mode": "local_assets",
        "stages": stages,
        "comparison": {
            "directly_changed_stages": directly_changed,
            "invalidated_stages": invalidated_stages,
            "reusable_stages": reusable_stages,
            "selective_recompute_applied": False,
        },
        "review": {
            "required": False,
            "status": "available" if review_path and review_path.is_file() else "unavailable",
            "artifact": str(review_path) if review_path else "",
        },
    }


def build_local_pipeline_manifest(
    *,
    asset_index: Mapping[str, Any],
    entity_ledger: Mapping[str, Any],
    ad_script: Mapping[str, Any],
    selected_segments: Sequence[Mapping[str, Any]],
    postproduction_contract: Mapping[str, Any],
    subtitles: Sequence[Mapping[str, Any]],
    sticker_plan: Mapping[str, Any],
    render_settings: Mapping[str, Any],
    stage_artifacts: Mapping[str, Iterable[Any]],
    previous_manifest: Optional[Mapping[str, Any]] = None,
    review_artifact: Optional[Path] = None,
) -> Dict[str, Any]:
    """Map local pipeline values onto stable stage boundaries."""
    stable_asset_index = {
        key: value for key, value in asset_index.items()
        if key != "created_at"
    }
    stable_asset_index["sources"] = [
        {key: value for key, value in source.items() if key != "mtime"}
        for source in asset_index.get("sources") or []
    ]
    stable_script = {
        key: value for key, value in ad_script.items()
        if key != "product_entity_ledger"
    }
    stable_edit_contract = {
        key: value for key, value in postproduction_contract.items()
        if key not in {"entity_ledger", "production_plan", "stickers"}
    }
    stable_sticker_plan = {
        key: value for key, value in sticker_plan.items()
        if key not in {"layouts", "skipped"}
    }
    stable_sticker_plan["items"] = [
        {key: value for key, value in item.items() if key != "status"}
        for item in sticker_plan.get("items") or []
    ]
    return build_run_manifest(
        stage_inputs={
            "asset_understanding": stable_asset_index,
            "product_entities": entity_ledger,
            "script": stable_script,
            "edit_plan": {
                "selected_segments": selected_segments,
                "postproduction_contract": stable_edit_contract,
            },
            "subtitles": subtitles,
            "stickers": stable_sticker_plan,
            "render": render_settings,
        },
        stage_artifacts=stage_artifacts,
        previous_manifest=previous_manifest,
        review_artifact=review_artifact,
    )


def write_run_manifest(manifest: Mapping[str, Any], path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    return path
