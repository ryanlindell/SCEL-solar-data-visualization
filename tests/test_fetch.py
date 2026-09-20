import pytest
import requests

from pipeline import fetch, http
from pipeline.config import Region


class FakeResponse:
    def __init__(self, status_code=200, body=None, text=""):
        self.status_code = status_code
        self._body = body
        self.text = text

    def json(self):
        return self._body


class FakeSession:
    """Serves canned responses in order and records the parameters it was called with."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params)))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def page(rows, total):
    return FakeResponse(200, {"response": {"total": str(total), "data": rows}})


def api_row(period, gen="10", units="megawatthours"):
    return {
        "period": period, "plantCode": "765", "plantName": "Kahe", "fuel2002": "RFO",
        "primeMover": "ALL", "state": "HI", "generation": gen, "generation-units": units,
    }


REGION = Region(id="oahu", label="Oʻahu", state="HI", county="Honolulu", plants={"765": "Kahe"})


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(http.time, "sleep", lambda s: None)


def test_pagination_collects_every_page(monkeypatch):
    monkeypatch.setattr(fetch, "PAGE_SIZE", 2)
    session = FakeSession([
        page([api_row("2024-01"), api_row("2024-02")], total=3),
        page([api_row("2024-03")], total=3),
    ])
    rows = fetch.fetch_facility_fuel(REGION, "KEY", session)
    assert [r["period"] for r in rows] == ["2024-01", "2024-02", "2024-03"]
    assert [c[1]["offset"] for c in session.calls] == ["0", "2"]


def test_rows_are_normalised_and_query_is_scoped_to_the_region():
    session = FakeSession([page([api_row("2024-01", gen="12.5"), api_row("2024-02", gen=None)], total=2)])
    rows = fetch.fetch_facility_fuel(REGION, "KEY", session)
    assert rows[0] == {
        "period": "2024-01", "plant_code": "765", "plant_name": "Kahe", "fuel_code": "RFO",
        "prime_mover": "ALL", "state": "HI", "generation_mwh": 12.5,
    }
    assert rows[1]["generation_mwh"] is None
    params = session.calls[0][1]
    assert params["facets[primeMover][]"] == "ALL"
    assert params["facets[state][]"] == "HI"


def test_small_scale_solar_converts_thousand_mwh_to_mwh_and_skips_nulls():
    def row(period, gen, units="thousand megawatthours"):
        return {"period": period, "location": "HI", "generation": gen, "generation-units": units}

    session = FakeSession([page([row("2024-01", "113.95157"), row("2024-02", None)], total=2)])
    rows = fetch.fetch_small_scale_solar("HI", "KEY", session)
    assert rows == [{"period": "2024-01", "state": "HI", "generation_mwh": pytest.approx(113951.57)}]
    params = session.calls[0][1]
    assert params["facets[fueltypeid][]"] == "DPV" and params["facets[sectorid][]"] == "99"
    assert params["facets[location][]"] == "HI"

    with pytest.raises(fetch.EIAError, match="units"):
        fetch.fetch_small_scale_solar("HI", "KEY", FakeSession([page([row("2024-01", "1", units="megawatthours")], total=1)]))
    with pytest.raises(fetch.EIAError, match="no small-scale solar"):
        fetch.fetch_small_scale_solar("HI", "KEY", FakeSession([page([], total=0)]))


def test_unexpected_units_are_rejected():
    session = FakeSession([page([api_row("2024-01", units="kilowatthours")], total=1)])
    with pytest.raises(fetch.EIAError, match="units"):
        fetch.fetch_facility_fuel(REGION, "KEY", session)


def test_temporary_errors_are_retried():
    session = FakeSession([FakeResponse(503), page([api_row("2024-01")], total=1)])
    assert len(fetch.fetch_facility_fuel(REGION, "KEY", session)) == 1
    assert len(session.calls) == 2


def test_bad_key_fails_immediately_with_a_helpful_message():
    session = FakeSession([FakeResponse(403)])
    with pytest.raises(fetch.EIAError, match="EIA_API_KEY"):
        fetch.fetch_facility_fuel(REGION, "KEY", session)
    assert len(session.calls) == 1


def test_network_errors_never_leak_the_api_key():
    leaky = requests.ConnectionError("failed: https://api.eia.gov/v2/x?api_key=SECRET123")
    session = FakeSession([leaky] * fetch.MAX_ATTEMPTS)
    with pytest.raises(fetch.EIAError) as excinfo:
        fetch.fetch_facility_fuel(REGION, "SECRET123", session)
    assert "SECRET123" not in str(excinfo.value)
    assert excinfo.value.__cause__ is None
