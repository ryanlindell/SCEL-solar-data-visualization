"""web/data/milestones.json is written by hand. These tests keep it honest: its shape is what the page expects, and
every date that is supposed to come from EIA-860 still matches the plant data the pipeline produced."""

import json
from pathlib import Path

import pytest

WEB_DATA = Path(__file__).resolve().parent.parent / "web" / "data"

# Milestone id -> the unit(s) in plants.json whose date it reports: (plant name, unit id, "online" or "retired").
# A milestone naming several units is dated by the earliest of them (the four solar farms of 2019).
EIA_DATED = {
    "kahuku-wind": [("Kahuku Wind Power LLC", "1", "online")],
    "first-solar": [("Kapolei Solar Energy Park", "KSEPV", "online")],
    "kawailoa-wind": [("Kawailoa Wind", "1", "online")],
    "honolulu-retires": [("Honolulu", "H8", "retired"), ("Honolulu", "H9", "retired")],
    "four-solar-farms": [("Waipio Solar", "WPO", "online"), ("Kawailoa Solar", "KAWS", "online"),
                         ("West Loch Solar One", "WLS1", "online"), ("Lanikuhana Solar LLC", "1", "online")],
    "na-pua-makani": [("Na Pua Makani Wind Project", "WT1", "online")],
    "aes-coal": [("AES Hawaii", "GEN1", "retired")],
    "kapolei-storage": [("Kapolei Energy Storage", "KES1", "online")],
}


@pytest.fixture(scope="module")
def milestones():
    return json.loads((WEB_DATA / "milestones.json").read_text(encoding="utf-8"))["regions"]["oahu"]


@pytest.fixture(scope="module")
def plants():
    return json.loads((WEB_DATA / "plants.json").read_text(encoding="utf-8"))["plants"]


def test_every_milestone_has_what_the_card_shows(milestones):
    assert len(milestones) == 10
    for m in milestones:
        assert {"id", "month", "date_text", "short", "title", "text", "source", "url"} <= m.keys(), m["id"]
        assert all(str(m[k]).strip() for k in ("title", "text", "source", "short", "date_text")), m["id"]
        assert m["url"].startswith("https://"), m["id"]
        assert len(m["month"]) == 7 and m["month"][4] == "-", m["id"]  # "YYYY-MM": the chart places the marker by it


def test_ids_are_unique_and_the_milestones_are_in_date_order(milestones):
    ids = [m["id"] for m in milestones]
    assert len(set(ids)) == len(ids)
    assert [m["month"] for m in milestones] == sorted(m["month"] for m in milestones)  # the chart numbers them 1..10 in this order


def test_dates_taken_from_eia_match_the_plant_data(milestones, plants):
    units = {(p["name"], u["id"]): u for p in plants for u in p["units"]}
    by_id = {m["id"]: m for m in milestones}
    assert set(EIA_DATED) <= set(by_id), "a milestone this test checks has been renamed or removed"
    for milestone_id, sources in EIA_DATED.items():
        months = [units[(plant, unit)][field] for plant, unit, field in sources]
        assert min(months) == by_id[milestone_id]["month"], f"{milestone_id}: EIA says {min(months)}, milestones.json says {by_id[milestone_id]['month']}"


def test_the_policy_events_are_the_ones_not_checked_against_eia(milestones):
    """Only the two policy events and nothing else lack an EIA date; if that changes, extend EIA_DATED."""
    assert {m["id"] for m in milestones} - set(EIA_DATED) == {"act-97", "net-metering-ends"}
