import io

import pytest
import requests
from openpyxl import Workbook

from pipeline import fetch_heco, http
from pipeline.config import CurtailmentSource, HecoFiles
from pipeline.fetch_heco import HecoError, normalize, parse_by_reason, parse_period, parse_quarterly_totals, to_number

# A miniature of the real "totals" sheet: an annual block, then the quarterly block.
TOTALS_ROWS = [
    ("A", 2023, "2024*"),
    (None, "Annual", "Annual"),
    ("O‘ahu", None, None),
    ("2. MWh taken from curtailable renewable resources", 1, 2),
    ("1/(1+2) = % Curtailed of curtailable renewable resources", 0.1, 0.1),
    (None, None, None),
    (None, 2024, None, None, None),
    ("B", "Q1 2024", "Q2 2024", "Q3 2024", "Q4 2024"),
    ("O‘ahu", None, None, None, None),
    ("2. MWh taken from curtailable renewable resources", 100, 200, 300, 400),
    ("1. MWh curtailed from curtailable renewable resources", 1, 2, 3, None),
    ("1/(1+2) = % Curtailed of curtailable renewable resources", 0.01, 0.01, 0.01, 0.01),
    ("3. MWh taken from firm renewable and utility hydro generating facilities", 10, 20, 30, 40),
    ("4. MWh taken from uncurtailable distributed renewable generation resources", 50, 60, 70, 80),
    ("1/(1+2+3+4) = % Curtailed of all renewable energy resources", 0.0, 0.0, 0.0, 0.0),
    (None, None, None, None, None),
    ("something after the block", 9, 9, 9, 9),
]

# A miniature of the real "by reason" sheet: blocks A/B for Oʻahu, C for Maui.
REASON_ROWS = [
    (None, None, None, None, None),
    (None, "A", "2015*", 2016, None),
    (None, None, "Annual", "Annual", None),
    (None, "O‘ahu", None, None, None),
    (None, "Oversupply", 1.6, 5.4, None),
    (None, "System Constraint", 1651.1, 2548.8, None),
    (None, "Facility Requested", "0", 846.6, None),
    (None, "Total", 1652.8, 3400.8, None),
    (None, "% Curtailed of All Renewable Energy Resources", None, None, None),
    (None, None, "*Data is not available before July 2015.", None, None),
    (None, "B", "Q1 2015", "Q2 2015", "Q3 2015"),
    (None, "O‘ahu", None, None, None),
    (None, "Oversupply", "NA", "NA", 1.0),
    (None, "System Constraint", "NA", "NA", 2.0),
    (None, "Facility Requested", "NA", "NA", "0"),
    (None, "Total", "NA", "NA", 3.0),
    (None, "% Curtailed of All Renewable Energy Resources", 0.0, 0.0, 0.0),
    (None, "C", "2015*", 2016, None),
    (None, None, "Annual", "Annual", None),
    (None, "Maui County - Maui Division", None, None, None),
    (None, "Oversupply", 99999, 99999, None),
    (None, "Total", 99999, 99999, None),
]


def test_normalize_ignores_every_apostrophe_style():
    assert normalize("O‘ahu") == normalize("Oʻahu") == normalize("O'ahu") == "oahu"


def test_to_number_handles_text_markers_and_blanks():
    assert to_number("0") == 0.0 and to_number(" 1,234.5 ") == 1234.5 and to_number(7) == 7.0
    assert to_number("NA") is None and to_number(None) is None and to_number(True) is None and to_number("") is None


def test_parse_period():
    assert parse_period("Q3 2024") == ("quarterly", "2024-Q3")
    assert parse_period("2015*") == ("annual", "2015") and parse_period(2016) == ("annual", "2016")
    assert parse_period("Annual") is None and parse_period(None) is None and parse_period("Q5 2024") is None


def test_quarterly_totals_are_read_by_label_not_position():
    rows = parse_quarterly_totals(TOTALS_ROWS)
    got = {(r["period"], r["series"]): r["value"] for r in rows}
    assert got[("2024-Q1", "delivered_mwh")] == 100 and got[("2024-Q4", "delivered_mwh")] == 400
    assert got[("2024-Q2", "curtailed_mwh")] == 2
    assert ("2024-Q4", "curtailed_mwh") not in got  # blank cell skipped
    assert got[("2024-Q3", "firm_mwh")] == 30 and got[("2024-Q4", "distributed_mwh")] == 80
    assert {r["series"] for r in rows} == {"delivered_mwh", "curtailed_mwh", "firm_mwh", "distributed_mwh"}
    assert all(r["period"].startswith("2024-Q") for r in rows)  # nothing from the annual block or below


def test_quarterly_totals_layout_changes_fail_loudly():
    with pytest.raises(HecoError, match="no quarterly block"):
        parse_quarterly_totals([("A", 2023), ("x", 1)])
    without_curtailed = [r for r in TOTALS_ROWS if not str(r[0]).startswith("1. MWh curtailed")]
    with pytest.raises(HecoError, match="curtailed_mwh"):
        parse_quarterly_totals(without_curtailed)


def test_by_reason_reads_only_the_requested_island():
    rows = parse_by_reason(REASON_ROWS, "O'ahu")  # ASCII apostrophe still finds "O‘ahu"
    got = {(r["frequency"], r["period"], r["series"]): r["value"] for r in rows}
    assert got[("annual", "2015", "oversupply_mwh")] == 1.6
    assert got[("annual", "2015", "facility_requested_mwh")] == 0.0  # the text "0"
    assert got[("annual", "2016", "total_mwh")] == 3400.8
    assert got[("quarterly", "2015-Q3", "system_constraint_mwh")] == 2.0
    assert ("quarterly", "2015-Q1", "oversupply_mwh") not in got  # "NA" skipped
    assert 99999 not in {r["value"] for r in rows}  # Maui's numbers never leak in


def test_by_reason_picks_a_different_island():
    rows = parse_by_reason(REASON_ROWS, "Maui County - Maui Division")
    assert {r["value"] for r in rows} == {99999.0}


def test_by_reason_unknown_island_fails_loudly():
    with pytest.raises(HecoError, match="no by-reason rows"):
        parse_by_reason(REASON_ROWS, "Kauaʻi")


# ---- downloading and reading real .xlsx bytes ---------------------------------------------------


def workbook_bytes(sheets: dict[str, list[tuple]]) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for row in rows:
            ws.append(list(row))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


class FakeResponse:
    def __init__(self, content=b"", status_code=200):
        self.content, self.status_code = content, status_code


class FakeSession:
    def __init__(self, responses):
        self.responses, self.urls = list(responses), []

    def get(self, url, timeout=None):
        self.urls.append(url)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


SOURCE = CurtailmentSource(totals_sheet="3D Curtailed Energy Oahu", by_reason_section="Oʻahu")
FILES = HecoFiles(base_url="https://example.test/h/", totals_file="totals.xlsx", by_reason_file="reasons.xlsx")


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(http.time, "sleep", lambda s: None)
    monkeypatch.setattr(fetch_heco.time, "sleep", lambda s: None)


def test_fetch_curtailment_end_to_end_on_real_xlsx_bytes():
    totals = workbook_bytes({"3E Other Island": [("x",)], "3D Curtailed Energy Oahu": TOTALS_ROWS})
    reasons = workbook_bytes({"3J Curtailment by Category": REASON_ROWS})
    session = FakeSession([FakeResponse(totals), FakeResponse(reasons)])
    result = fetch_heco.fetch_curtailment(SOURCE, FILES, session)
    assert session.urls == ["https://example.test/h/totals.xlsx", "https://example.test/h/reasons.xlsx"]
    assert len(result["quarterly_totals"]) == 15 and len(result["by_reason"]) > 0


def test_missing_sheet_names_the_sheets_that_exist():
    totals = workbook_bytes({"Something Else": TOTALS_ROWS})
    with pytest.raises(HecoError, match="Something Else"):
        fetch_heco.fetch_curtailment(SOURCE, FILES, FakeSession([FakeResponse(totals)]))


def test_garbage_file_is_reported_cleanly():
    with pytest.raises(HecoError, match="Could not read"):
        fetch_heco.fetch_curtailment(SOURCE, FILES, FakeSession([FakeResponse(b"not an excel file")]))


def test_temporary_errors_are_retried_and_missing_files_are_not():
    totals = workbook_bytes({"3D Curtailed Energy Oahu": TOTALS_ROWS})
    reasons = workbook_bytes({"s": REASON_ROWS})
    session = FakeSession([requests.ConnectionError("x"), FakeResponse(status_code=503), FakeResponse(totals), FakeResponse(reasons)])
    assert fetch_heco.fetch_curtailment(SOURCE, FILES, session)["quarterly_totals"]
    with pytest.raises(HecoError, match="HTTP 404"):
        fetch_heco.fetch_curtailment(SOURCE, FILES, FakeSession([FakeResponse(status_code=404)]))
