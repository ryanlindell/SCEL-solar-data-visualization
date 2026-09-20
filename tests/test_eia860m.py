import io
from datetime import date

import pytest
import requests
from openpyxl import Workbook

from pipeline import fetch_eia860m, http
from pipeline.config import Eia860m, Region
from pipeline.fetch_eia860m import Eia860Error, candidate_files, fetch_generator_inventory, parse_workbook

OPERATING_HEADER = ["Entity ID", "Entity Name", "Plant ID", "Plant Name", "Google Map", "Bing Map", "Plant State", "County",
                    "Balancing Authority Code", "Sector", "Generator ID", "Unit Code", "Nameplate Capacity (MW)", "Technology",
                    "Energy Source Code", "Prime Mover Code", "Operating Month", "Operating Year", "Status",
                    "Nameplate Energy Capacity (MWh)", "Latitude", "Longitude"]
RETIRED_HEADER = ["Entity ID", "Entity Name", "Plant ID", "Plant Name", "Google Map", "Bing Map", "Plant State", "County",
                  "Balancing Authority Code", "Sector", "Generator ID", "Unit Code", "Nameplate Capacity (MW)", "Technology",
                  "Energy Source Code", "Prime Mover Code", "Operating Month", "Operating Year", "Retirement Month",
                  "Retirement Year", "Nameplate Energy Capacity (MWh)", "Latitude", "Longitude"]
PLANNED_HEADER = ["Entity ID", "Entity Name", "Plant ID", "Plant Name", "Google Map", "Bing Map", "Plant State", "County",
                  "Balancing Authority Code", "Sector", "Generator ID", "Unit Code", "Nameplate Capacity (MW)", "Technology",
                  "Energy Source Code", "Prime Mover Code", "Planned Operation Month", "Planned Operation Year", "Status",
                  "Latitude", "Longitude"]


def op(plant_id, name, state, county, gen, mw, tech, month, year, mwh=None, lat=21.3, lon=-157.9, owner="Owner LLC"):
    return [1, owner, plant_id, name, "", "", state, county, "HECO", "IPP Non-CHP", gen, None, mw, tech, "SUN", "PV",
            month, year, "(OP) Operating", mwh, lat, lon]


def ret(plant_id, name, state, county, gen, mw, tech, month, year, r_month, r_year, lat=21.3, lon=-157.9):
    return [1, "Owner LLC", plant_id, name, "", "", state, county, "HECO", "Electric Utility", gen, None, mw, tech, "RFO", "ST",
            month, year, r_month, r_year, None, lat, lon]


def plan(plant_id, name, state, county, gen, mw, tech, month, year, status="(V) Under construction, more than 50 percent complete"):
    return [1, "Developer LLC", plant_id, name, "", "", state, county, "HECO", "IPP Non-CHP", gen, None, mw, tech, "SUN", "PV",
            month, year, status, 21.43, -157.98]


def workbook(operating, retired, sheet_names=("Operating", "Retired", "Planned"), planned=()) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    for sheet, header, rows in ((sheet_names[0], OPERATING_HEADER, operating), (sheet_names[1], RETIRED_HEADER, retired),
                                (sheet_names[2], PLANNED_HEADER, planned)):
        ws = wb.create_sheet(sheet)
        ws.append([f"{sheet} Generators as of July 2026"])
        ws.append([])
        ws.append(header)
        for r in rows:
            ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


OPERATING = [
    op("1", "Kahe", "HI", "Honolulu", "K1", 90, "Petroleum Liquids", 3, 1963),
    op("2", "Kapolei Energy Storage", "HI", "Honolulu", "KES1", 185, "Batteries", 12, 2023, mwh=565, lat=21.33, lon=-158.1),
    op("3", "Maui Plant", "HI", "Maui", "M1", 20, "Petroleum Liquids", 1, 2000),  # another county: excluded
    op("4", "Texas Plant", "TX", "Honolulu", "T1", 20, "Petroleum Liquids", 1, 2000),  # another state: excluded
]
PLANNED = [
    plan("6", "New Solar", "HI", "Honolulu", "PV1", 30, "Solar Photovoltaic", 9, 2026),
    plan("7", "Maui Solar", "HI", "Maui", "PV1", 30, "Solar Photovoltaic", 9, 2026),  # another county: excluded
]
RETIRED = [ret("5", "AES Hawaii", "HI", "Honolulu", "GEN1", 203, "Conventional Steam Coal", 5, 1992, 9, 2022)]


class Resp:
    def __init__(self, content=b"", status_code=200):
        self.content, self.status_code = content, status_code


class Session:
    def __init__(self, by_name):
        self.by_name, self.urls = by_name, []

    def get(self, url, timeout=None):
        self.urls.append(url)
        item = self.by_name.get(url.rsplit("/", 1)[-1], Resp(b"<!doctype html><html>Not found</html>"))  # EIA's fake-200 page
        if isinstance(item, Exception):
            raise item
        return item


REGION = Region(id="oahu", label="Oʻahu", state="HI", county="Honolulu", plants={})
FILES = Eia860m(base_url="https://example.test/x/", max_months_back=3, corrections=())


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(http.time, "sleep", lambda s: None)
    monkeypatch.setattr(fetch_eia860m.time, "sleep", lambda s: None)


def test_candidate_files_go_back_month_by_month_across_a_year_boundary():
    assert candidate_files(date(2026, 2, 10), 3) == [
        ("2026-02", "february_generator2026.xlsx"),
        ("2026-01", "january_generator2026.xlsx"),
        ("2025-12", "december_generator2025.xlsx"),
        ("2025-11", "november_generator2025.xlsx"),
    ]


def test_parse_reads_only_the_requested_county_and_all_three_sheets():
    rows = parse_workbook(workbook(OPERATING, RETIRED, planned=PLANNED), "HI", "Honolulu")
    assert sorted((r["sheet"], r["plant_name"]) for r in rows) == [
        ("operating", "Kahe"), ("operating", "Kapolei Energy Storage"), ("planned", "New Solar"), ("retired", "AES Hawaii")]
    kes = next(r for r in rows if r["plant_name"] == "Kapolei Energy Storage")
    assert (kes["nameplate_mw"], kes["energy_mwh"], kes["online_year"], kes["online_month"]) == (185.0, 565.0, 2023, 12)
    assert (kes["lat"], kes["lon"], kes["technology"], kes["plant_code"]) == (21.33, -158.1, "Batteries", "2")
    aes = next(r for r in rows if r["plant_name"] == "AES Hawaii")
    assert (aes["retired_year"], aes["retired_month"], aes["online_year"]) == (2022, 9, 1992)


def test_a_planned_unit_gets_its_expected_month_and_stage_as_its_in_service_date():
    new = next(r for r in parse_workbook(workbook(OPERATING, RETIRED, planned=PLANNED), "HI", "Honolulu") if r["sheet"] == "planned")
    assert (new["online_year"], new["online_month"], new["retired_year"]) == (2026, 9, None)
    assert new["status"] == "(V) Under construction, more than 50 percent complete" and (new["lat"], new["lon"]) == (21.43, -157.98)
    assert new["energy_mwh"] is None  # the Planned sheet has no storage energy column


def test_a_planned_date_left_blank_by_eia_stays_empty_instead_of_becoming_zero():
    blank = plan("6", "New Solar", "HI", "Honolulu", "PV1", 30, "Solar Photovoltaic", " ", " ")  # EIA pads empty cells with a space
    row = next(r for r in parse_workbook(workbook(OPERATING, RETIRED, planned=[blank]), "HI", "Honolulu") if r["sheet"] == "planned")
    assert row["online_year"] is None and row["online_month"] is None


def test_county_match_ignores_case():
    assert len(parse_workbook(workbook(OPERATING, RETIRED, planned=PLANNED), "HI", "HONOLULU")) == 4


def test_wrong_layouts_fail_loudly():
    with pytest.raises(Eia860Error, match="Sheet 'Retired' not found"):
        parse_workbook(workbook(OPERATING, RETIRED, sheet_names=("Operating", "Renamed", "Planned")), "HI", "Honolulu")
    with pytest.raises(Eia860Error, match="Sheet 'Planned' not found"):
        parse_workbook(workbook(OPERATING, RETIRED, sheet_names=("Operating", "Retired", "Renamed")), "HI", "Honolulu")
    with pytest.raises(Eia860Error, match="Could not read"):
        parse_workbook(b"not an excel file", "HI", "Honolulu")


def test_the_newest_month_that_really_exists_is_used():
    content = workbook(OPERATING, RETIRED)
    # August is missing (EIA answers 200 with an HTML page), July is real.
    session = Session({"july_generator2026.xlsx": Resp(content)})
    inv = fetch_generator_inventory(REGION, FILES, session, today=date(2026, 8, 20))
    assert (inv["source_file"], inv["as_of"], len(inv["rows"])) == ("july_generator2026.xlsx", "2026-07", 3)
    assert [u.rsplit("/", 1)[-1] for u in session.urls] == ["august_generator2026.xlsx", "july_generator2026.xlsx"]


def test_an_html_page_with_a_200_status_is_not_mistaken_for_a_workbook():
    session = Session({})  # every month returns EIA's HTML page
    with pytest.raises(Eia860Error, match="No EIA-860M file found"):
        fetch_generator_inventory(REGION, FILES, session, today=date(2026, 8, 20))
    assert len(session.urls) == FILES.max_months_back + 1


def test_a_real_404_also_moves_on_and_a_dropped_connection_is_retried():
    content = workbook(OPERATING, RETIRED)
    session = Session({"august_generator2026.xlsx": Resp(status_code=404), "july_generator2026.xlsx": Resp(content)})
    assert fetch_generator_inventory(REGION, FILES, session, today=date(2026, 8, 20))["as_of"] == "2026-07"

    class Flaky(Session):
        calls = 0

        def get(self, url, timeout=None):
            Flaky.calls += 1
            if Flaky.calls == 1:
                raise requests.ConnectionError("dropped")
            return super().get(url, timeout)

    assert fetch_generator_inventory(REGION, FILES, Flaky({"july_generator2026.xlsx": Resp(content)}), today=date(2026, 7, 5))["as_of"] == "2026-07"
