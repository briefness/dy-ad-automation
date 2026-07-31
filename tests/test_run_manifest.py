import json
from copy import deepcopy

from run_manifest import (
    STAGE_DEPENDENCIES,
    build_local_pipeline_manifest,
    build_run_manifest,
    load_run_manifest,
    write_run_manifest,
)


def _stage_inputs(subtitle: str = "自然茉莉香气") -> dict:
    return {
        "asset_understanding": {"sources": [{"sha256": "asset-a"}]},
        "product_entities": {"ingredients": ["茉莉花"]},
        "script": {"segments": [{"subtitle": subtitle}]},
        "edit_plan": {"segments": [{"source": "clip-a"}]},
        "subtitles": [{"text": subtitle, "start": 0.0, "end": 2.0}],
        "stickers": {"items": [{"kind": "ingredient", "text": "茉莉花"}]},
        "render": {"aspect_ratio": "9:16"},
    }


def _stage_artifacts(tmp_path) -> dict:
    artifacts = {}
    for stage in STAGE_DEPENDENCIES:
        path = tmp_path / f"{stage}.json"
        path.write_text(stage, encoding="utf-8")
        artifacts[stage] = [path]
    return artifacts


def test_unchanged_stage_lineage_reports_reuse_candidates_without_applying_recompute(tmp_path):
    inputs = _stage_inputs()
    artifacts = _stage_artifacts(tmp_path)
    first = build_run_manifest(stage_inputs=inputs, stage_artifacts=artifacts)
    second = build_run_manifest(
        stage_inputs=inputs,
        stage_artifacts=artifacts,
        previous_manifest=first,
    )

    assert first["comparison"]["invalidated_stages"] == list(STAGE_DEPENDENCIES)
    assert second["comparison"] == {
        "directly_changed_stages": [],
        "invalidated_stages": [],
        "reusable_stages": list(STAGE_DEPENDENCIES),
        "selective_recompute_applied": False,
    }


def test_subtitle_change_invalidates_only_subtitle_consumers(tmp_path):
    artifacts = _stage_artifacts(tmp_path)
    previous = build_run_manifest(
        stage_inputs=_stage_inputs(),
        stage_artifacts=artifacts,
    )
    changed_inputs = deepcopy(_stage_inputs())
    changed_inputs["subtitles"][0]["text"] = "自然玫瑰香气"
    current = build_run_manifest(
        stage_inputs=changed_inputs,
        stage_artifacts=artifacts,
        previous_manifest=previous,
    )

    assert current["comparison"]["directly_changed_stages"] == ["subtitles"]
    assert current["comparison"]["invalidated_stages"] == [
        "subtitles", "stickers", "render",
    ]
    assert current["comparison"]["reusable_stages"] == [
        "asset_understanding", "product_entities", "script", "edit_plan",
    ]
    assert current["stages"]["stickers"]["invalidation_reason"] == (
        "dependency_changed:subtitles"
    )


def test_local_stage_mapping_ignores_runtime_metadata_and_downstream_contract_attachments(tmp_path):
    artifacts = _stage_artifacts(tmp_path)
    values = {
        "asset_index": {
            "created_at": 1.0,
            "sources": [{"sha256": "asset-a", "mtime": 1.0}],
        },
        "entity_ledger": {"entities": [{"canonical": "茉莉花"}]},
        "ad_script": {
            "segments": [{"subtitle": "自然茉莉香气"}],
            "product_entity_ledger": {"runtime_attachment": 1},
        },
        "selected_segments": [{"window_id": "window-a"}],
        "postproduction_contract": {
            "segments": [{"edit_index": 0}],
            "stickers": {"runtime_attachment": 1},
            "production_plan": {"path": "old.json"},
        },
        "subtitles": [{"text": "自然茉莉香气"}],
        "sticker_plan": {
            "items": [{"text": "茉莉花", "status": "planned"}],
            "layouts": {},
        },
        "render_settings": {"aspect_ratio": "9:16"},
        "stage_artifacts": artifacts,
    }
    previous = build_local_pipeline_manifest(**values)
    changed = deepcopy(values)
    changed["asset_index"]["created_at"] = 2.0
    changed["asset_index"]["sources"][0]["mtime"] = 2.0
    changed["ad_script"]["product_entity_ledger"] = {"runtime_attachment": 2}
    changed["postproduction_contract"]["stickers"] = {"runtime_attachment": 2}
    changed["postproduction_contract"]["production_plan"] = {"path": "new.json"}
    changed["sticker_plan"]["items"][0]["status"] = "rendered"
    changed["sticker_plan"]["layouts"] = {"9:16": [{"position": "top_left"}]}
    current = build_local_pipeline_manifest(previous_manifest=previous, **changed)

    assert current["comparison"]["invalidated_stages"] == []


def test_missing_stage_artifact_invalidates_its_consumers(tmp_path):
    artifacts = _stage_artifacts(tmp_path)
    previous = build_run_manifest(
        stage_inputs=_stage_inputs(),
        stage_artifacts=artifacts,
    )
    artifacts["subtitles"][0].unlink()
    current = build_run_manifest(
        stage_inputs=_stage_inputs(),
        stage_artifacts=artifacts,
        previous_manifest=previous,
    )

    assert current["comparison"]["directly_changed_stages"] == []
    assert current["comparison"]["invalidated_stages"] == [
        "subtitles", "stickers", "render",
    ]
    assert current["stages"]["subtitles"]["invalidation_reason"] == "artifact_missing"


def test_run_manifest_write_is_atomic_and_review_is_nonblocking(tmp_path):
    review = tmp_path / "timeline_review.html"
    review.write_text("<html></html>", encoding="utf-8")
    manifest = build_run_manifest(
        stage_inputs=_stage_inputs(),
        stage_artifacts=_stage_artifacts(tmp_path),
        review_artifact=review,
    )
    path = write_run_manifest(manifest, tmp_path / "run_manifest.json")

    assert load_run_manifest(path) == json.loads(path.read_text(encoding="utf-8"))
    assert manifest["review"] == {
        "required": False,
        "status": "available",
        "artifact": str(review),
    }
    assert not list(tmp_path.glob("*.tmp"))
