from contextlib import closing

import pytest

from pipeline import cache
from pipeline.config import Eia860Correction, Region, Settings, HourlyData
from pipeline.export_plants import build_payload
from pipeline.process_plants import apply_corrections, battery_fleet, build_plants, fuel_group

REGION = Region(id="oahu", label="Oʻahu", state="HI", county="Honolulu", plants={"1": "Kahe"},
                excluded_plants={"9": "Refinery cogeneration"})


def row(plant, gen, tech, mw, year, month=1, sheet="operating", mwh=None, r_year=None, r_month=None, name=None, lat=21.3, lon=-157.9):
    return {"sheet": sheet, "plant_code": plant, "plant_name": name or f"Plant {plant}", "owner": "Owner", "sector": "IPP",
            "county": "Honolulu", "generator_id": gen, "technology": tech, "energy_source": "X", "prime_mover": "Y",
            "nameplate_mw": mw, "energy_mwh": mwh, "status": "OP", "online_year": year, "online_month": month,
            "retired_year": r_year, "retired_month": r_month, "lat": lat, "lon": lon}


def planned_row(plant, gen, tech, mw, year, month, status="(V) Under construction, more than 50 percent complete", name=None):
    return row(plant, gen, tech, mw, year, month, sheet="planned", name=name) | {"status": status}


CORRECTION = Eia860Correction(plant_code="7", generator_id="B1", field="energy_mwh", filed_value=4.6, value=144.0,
                              reason="implausible", source="https://example.test")


def test_fuel_groups_from_eia_technology_names():
    assert [fuel_group(t) for t in ("Solar Photovoltaic", "Onshore Wind Turbine", "Batteries", "Petroleum Liquids",
            "Conventional Steam Coal", "Municipal Solid Waste", "Landfill Gas", "Other Waste Biomass", "Natural Gas Steam", None)] == [
        "solar", "wind", "battery", "oil", "coal", "waste", "waste", "waste", "other", "other"]


def test_units_are_grouped_into_plants_with_dates_and_flags():
    rows = [
        row("1", "K2", "Petroleum Liquids", 90, 1965, 6, name="Kahe"),
        row("1", "K1", "Petroleum Liquids", 90, 1963, 3, name="Kahe"),
        row("2", "H1", "Petroleum Liquids", 50, 1954, 12, sheet="retired", r_year=2014, r_month=1, name="Honolulu"),
        row("9", "C1", "Petroleum Liquids", 30, 1990, name="Refinery"),
    ]
    plants, warnings = build_plants(rows, REGION)
    assert warnings == []
    assert [p["name"] for p in plants] == ["Honolulu", "Kahe", "Refinery"]  # oldest unit first
    kahe = plants[1]
    assert [u["id"] for u in kahe["units"]] == ["K1", "K2"]
    assert kahe["units"][0]["online"] == "1963-03" and kahe["units"][0]["retired"] is None
    assert kahe["counted_in_share_chart"] is True and kahe["excluded_reason"] is None
    assert plants[0]["units"][0]["retired"] == "2014-01" and plants[0]["counted_in_share_chart"] is False
    assert plants[2]["excluded_reason"] == "Refinery cogeneration"
    assert (kahe["lat"], kahe["lon"], kahe["region"]) == (21.3, -157.9, "oahu")


def test_units_that_never_entered_service_are_left_out_and_a_missing_month_means_january():
    rows = [row("1", "K1", "Solar Photovoltaic", 5, None), row("1", "K2", "Solar Photovoltaic", 5, 2020, month=None)]
    plants, _ = build_plants(rows, REGION)
    assert [u["id"] for u in plants[0]["units"]] == ["K2"] and plants[0]["units"][0]["online"] == "2020-01"


def test_plants_without_coordinates_are_kept_but_have_none():
    plants, _ = build_plants([row("1", "K1", "Solar Photovoltaic", 5, 2020, lat=None, lon=None)], REGION)
    assert plants[0]["lat"] is None and plants[0]["lon"] is None


def test_battery_fleet_counts_only_batteries_still_in_service():
    rows = [
        row("7", "B1", "Batteries", 36, 2023, mwh=144),
        row("8", "B2", "Batteries", 185, 2023, mwh=565),
        row("8", "OLD", "Batteries", 15, 2011, sheet="retired", mwh=None, r_year=2013, r_month=8),
        row("8", "PV", "Solar Photovoltaic", 40, 2023),
    ]
    fleet = battery_fleet(build_plants(rows, REGION)[0])
    assert (fleet["power_mw"], fleet["energy_mwh"]) == (221.0, 709.0)
    assert [u["unit"] for u in fleet["units"]] == ["B2", "B1"] and fleet["units_missing_energy"] == []


def test_a_correction_applies_only_to_the_exact_wrong_value_and_is_recorded():
    rows = [row("7", "B1", "Batteries", 36, 2023, mwh=4.6)]
    plants, warnings = build_plants(rows, REGION, (CORRECTION,))
    unit = plants[0]["units"][0]
    assert warnings == [] and unit["mwh"] == 144.0
    assert unit["corrections"] == [{"field": "energy_mwh", "as_filed": 4.6, "used": 144.0, "reason": "implausible", "source": "https://example.test"}]
    fleet = battery_fleet(plants)
    assert (fleet["energy_mwh"], fleet["energy_mwh_as_filed"], fleet["units"][0]["corrected"]) == (144.0, 4.6, True)


def test_if_eia_changes_the_value_the_correction_is_not_applied_and_a_warning_says_so():
    plants, warnings = build_plants([row("7", "B1", "Batteries", 36, 2023, mwh=150.0)], REGION, (CORRECTION,))
    assert plants[0]["units"][0]["mwh"] == 150.0 and plants[0]["units"][0]["corrections"] == []
    assert len(warnings) == 1 and "NOT applied" in warnings[0]


def test_a_correction_that_matches_nothing_is_reported():
    _, warnings = build_plants([row("1", "K1", "Petroleum Liquids", 90, 1963)], REGION, (CORRECTION,))
    assert len(warnings) == 1 and "matched no operating unit" in warnings[0]


def test_generator_cache_is_idempotent_and_a_unit_can_move_from_operating_to_retired(tmp_path):
    first = {"source_file": "july_generator2026.xlsx", "as_of": "2026-07",
             "rows": [row("1", "K1", "Petroleum Liquids", 90, 1963), row("2", "H1", "Petroleum Liquids", 50, 1954)]}
    with closing(cache.connect(tmp_path / "raw.sqlite")) as conn:
        assert cache.replace_generators(conn, "oahu", first) == {"fetched": 2, "new": 2, "changed": 0}
        assert cache.replace_generators(conn, "oahu", first) == {"fetched": 2, "new": 0, "changed": 0}
        # Next month: H1 has been retired, so it moves sheets and the old 'operating' row must not linger.
        second = {"source_file": "august_generator2026.xlsx", "as_of": "2026-08",
                  "rows": [row("1", "K1", "Petroleum Liquids", 90, 1963),
                           row("2", "H1", "Petroleum Liquids", 50, 1954, sheet="retired", r_year=2026, r_month=8)]}
        counts = cache.replace_generators(conn, "oahu", second)
        assert counts["fetched"] == 2 and counts["new"] == 1  # the retired row is new; the operating H1 row is gone
        stored = cache.read_generators(conn, "oahu")
        assert stored["as_of"] == "2026-08" and stored["source_file"] == "august_generator2026.xlsx"
        assert sorted((r["sheet"], r["generator_id"]) for r in stored["rows"]) == [("operating", "K1"), ("retired", "H1")]
        assert cache.read_generators(conn, "maui")["rows"] == []


def test_export_payload_shape():
    region = Region(id="oahu", label="Oʻahu", state="HI", county="Honolulu", plants={})
    settings = Settings(renewable_fuels=(), storage_fuels=(), regions={"oahu": region}, hourly_data=HourlyData("r", "c", -5, -10, {}), heco=None, eia860m=None)
    plants, _ = build_plants([row("7", "B1", "Batteries", 36, 2023, mwh=144), row("1", "K1", "Petroleum Liquids", 90, 1963)], REGION)
    payload = build_payload(settings, {"oahu": {"plants": plants, "battery_fleet": battery_fleet(plants), "source_file": "f.xlsx", "as_of": "2026-07", "warnings": ["w"]}})
    meta = payload["regions"]["oahu"]
    assert meta["first_month"] == "1963-01" and meta["as_of"] == "2026-07" and meta["battery_fleet"]["power_mw"] == 36.0
    assert payload["meta"]["warnings"] == ["w"] and {g["key"] for g in payload["meta"]["groups"]} >= {"solar", "wind", "battery", "oil", "coal", "waste", "other"}
    assert len(payload["plants"]) == 2 and all(p["region"] == "oahu" for p in payload["plants"])


# ---- planned units (EIA-860M "Planned" sheet) ---------------------------------------------------------------------


def test_planned_units_are_flagged_dated_and_carry_their_stage_without_eias_status_code():
    rows = [row("1", "K1", "Petroleum Liquids", 90, 1963), planned_row("3", "PV1", "Solar Photovoltaic", 30, 2029, 4, name="New Solar")]
    plants, _ = build_plants(rows, REGION)
    new = next(p for p in plants if p["name"] == "New Solar")["units"][0]
    assert (new["planned"], new["online"], new["retired"]) == (True, "2029-04", None)
    assert new["status"] == "Under construction, more than 50 percent complete"
    kahe = next(p for p in plants if p["name"] == "Plant 1")["units"][0]
    assert (kahe["planned"], kahe["status"]) == (False, None)


def test_a_planned_battery_is_not_in_todays_battery_fleet():
    rows = [row("8", "B2", "Batteries", 185, 2023, mwh=565), planned_row("3", "B1", "Batteries", 30, 2026, 9)]
    fleet = battery_fleet(build_plants(rows, REGION)[0])
    assert (fleet["power_mw"], fleet["energy_mwh"], len(fleet["units"])) == (185.0, 565.0, 1)


def test_a_correction_never_touches_a_planned_unit_with_the_same_ids():
    rows = [row("7", "B1", "Batteries", 36, 2023, mwh=4.6), planned_row("7", "B1", "Batteries", 36, 2027, 1)]
    plants, warnings = build_plants(rows, REGION, (CORRECTION,))
    by_planned = {u["planned"]: u for u in plants[0]["units"]}
    assert warnings == [] and by_planned[False]["mwh"] == 144.0 and by_planned[True]["corrections"] == []


def test_export_reports_the_furthest_planned_month_and_starts_the_timeline_at_the_first_real_unit():
    region = Region(id="oahu", label="Oʻahu", state="HI", county="Honolulu", plants={})
    settings = Settings(renewable_fuels=(), storage_fuels=(), regions={"oahu": region}, hourly_data=HourlyData("r", "c", -5, -10, {}), heco=None, eia860m=None)
    rows = [row("1", "K1", "Petroleum Liquids", 90, 1963), planned_row("3", "PV1", "Solar Photovoltaic", 30, 2026, 9),
            planned_row("4", "PE1", "Other Waste Biomass", 9, 2029, 4)]
    plants, _ = build_plants(rows, REGION)
    result = {"plants": plants, "battery_fleet": battery_fleet(plants), "source_file": "f.xlsx", "as_of": "2026-07", "warnings": []}
    meta = build_payload(settings, {"oahu": result})["regions"]["oahu"]
    assert (meta["first_month"], meta["as_of"], meta["last_planned_month"]) == ("1963-01", "2026-07", "2029-04")
    result["plants"] = [p for p in plants if p["name"] == "Plant 1"]
    assert build_payload(settings, {"oahu": result})["regions"]["oahu"]["last_planned_month"] is None  # nothing planned
