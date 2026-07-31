import json
from pathlib import Path

from production_plan import (
    build_local_production_plan,
    build_product_entity_ledger,
    replay_production_plan_fixture,
    sanitize_postproduction_contract,
    sanitize_sticker_plan,
    sanitize_subtitle_emphasis,
    validate_local_production_plan,
    write_production_plan_artifacts,
)


FIXTURES = Path(__file__).parent / "fixtures" / "production_plan"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_entity_ledger_merges_verified_visual_alias_into_trusted_ingredient():
    fixture = _load("jasmine_valid.json")
    ledger = build_product_entity_ledger(
        fixture["product_info"],
        fixture["selected_segments"],
    )

    ingredient = next(
        item for item in ledger["entities"]
        if item["fact_type"] == "ingredient" and item["canonical"] == "茉莉花"
    )
    assert ingredient["canonical"] == "茉莉花"
    assert ingredient["aliases"] == ["茉莉花", "茉莉花植株"]
    assert ingredient["provenance"][0]["source"] == "product_info.ingredients"
    assert any(item["source"] == "verified_visual_relationship" for item in ingredient["provenance"])


def test_historical_production_plan_fixtures_replay_expected_failures():
    for fixture_path in sorted(FIXTURES.glob("*.json")):
        replay = replay_production_plan_fixture(fixture_path)
        assert replay["blocking_codes"] == replay["expected_blocking_codes"], fixture_path.name


def test_invalid_fancy_subtitle_and_repeated_sticker_are_removed_without_touching_valid_items():
    invalid = _load("historical_regressions.json")
    valid = _load("jasmine_valid.json")
    ledger = build_product_entity_ledger(valid["product_info"], valid["selected_segments"])

    subtitles, subtitle_violations = sanitize_subtitle_emphasis(invalid["subtitles"])
    assert subtitles[0]["fancy"] is False
    assert subtitles[0]["emphasis"] is False
    assert subtitle_violations[0]["code"] == "generic_emphasis_term"

    sticker_plan, sticker_violations = sanitize_sticker_plan(invalid["sticker_plan"], ledger)
    assert sticker_plan["items"] == []
    assert sticker_plan["skipped"][-1]["reason"] == "production_plan_invariant"
    assert sticker_violations[0]["code"] == "sticker_repeats_subtitle"

    valid_subtitles, valid_subtitle_violations = sanitize_subtitle_emphasis(valid["subtitles"])
    valid_stickers, valid_sticker_violations = sanitize_sticker_plan(valid["sticker_plan"], ledger)
    assert valid_subtitles == valid["subtitles"]
    assert valid_stickers == valid["sticker_plan"]
    assert valid_subtitle_violations == []
    assert valid_sticker_violations == []


def test_repairable_contract_emphasis_is_downgraded_before_plan_validation():
    fixture = _load("historical_regressions.json")
    contract, violations = sanitize_postproduction_contract(
        fixture["postproduction_contract"],
        fixture["ad_script"],
    )

    assert contract["segments"][0]["subtitle"]["emphasis"] is False
    assert contract["segments"][0]["subtitle"]["emphasis_terms"] == []
    assert [item["code"] for item in violations] == ["generic_emphasis_term"]


def test_production_plan_artifacts_are_atomic_and_review_contains_full_timeline(tmp_path):
    fixture = _load("jasmine_valid.json")
    ledger = build_product_entity_ledger(fixture["product_info"], fixture["selected_segments"])
    plan = build_local_production_plan(
        ad_script=fixture["ad_script"],
        selected_segments=fixture["selected_segments"],
        postproduction_contract=fixture["postproduction_contract"],
        entity_ledger=ledger,
        subtitles=fixture["subtitles"],
        sticker_plan=fixture["sticker_plan"],
    )
    plan["validation"] = validate_local_production_plan(plan)

    json_path, review_path = write_production_plan_artifacts(
        plan,
        tmp_path / "production_plan.json",
        tmp_path / "timeline_review.html",
    )

    saved = json.loads(json_path.read_text(encoding="utf-8"))
    review = review_path.read_text(encoding="utf-8")
    assert saved["lineage"]["selected_segments_sha256"]
    assert [item["semantic_segment"] for item in saved["segments"]] == [0, 1]
    assert "这瓶茶咖为什么值得看" in review
    assert "茉莉花" in review
    assert "jasmine.mp4" in review
    assert not list(tmp_path.glob("*.tmp"))
