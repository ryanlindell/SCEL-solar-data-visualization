from datetime import datetime, timedelta

import pytest
import requests

from pipeline import fetch_nrel, fetch_oedi, http
from pipeline.config import HourlyData, HourlyModel, Season

HD = HourlyData(
    residential_release="r",
    commercial_release="c",
    load_timestamp_utc_offset=-5,
    local_utc_offset=-10,
    seasons={"winter": Season("Winter", (12, 1, 2))},
)
MODEL = HourlyModel(
    lat=21.3, lon=-157.86, pv_system={"tilt": 20, "azimuth": 180, "array_type": 0, "module_type": 0, "losses": 14},
    state="HI", share_of_state=0.7, rooftop_share_of_state=0.77, commercial_county_fips="G1500030", reference_year=None,
)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(http.time, "sleep", lambda s: None)
    monkeypatch.setattr(fetch_oedi.time, "sleep", lambda s: None)


# ---- timestamps: 15-minute-ending EST -> hour-beginning Hawaiʻi time ----------------------------


def test_first_timestamp_falls_on_the_previous_evening_in_hawaii():
    # 00:15 EST is the quarter hour 00:00-00:15 EST = 19:00-19:15 HST on Dec 31 (filed under 2018).
    assert fetch_oedi.to_local_hour_key("2018-01-01 00:15:00", -5, -10) == (12, 31, 19)


def test_all_four_quarter_hours_share_one_hour():
    keys = {fetch_oedi.to_local_hour_key(f"2018-06-15 12:{m}:00", -5, -10) for m in ("15", "30", "45")}
    keys.add(fetch_oedi.to_local_hour_key("2018-06-15 13:00:00", -5, -10))
    assert keys == {(6, 15, 7)}  # 12:00-13:00 EST = 07:00-08:00 HST


def test_last_timestamp_is_the_final_quarter_of_the_last_local_hour():
    assert fetch_oedi.to_local_hour_key("2019-01-01 00:00:00", -5, -10) == (12, 31, 18)


def test_building_type_from_url():
    assert fetch_oedi.building_type_from_url("https://x/y/up00-hi-single-family_detached.csv") == "single-family_detached"
    assert fetch_oedi.building_type_from_url("https://x/y/up00-g1500030-largeoffice.csv") == "largeoffice"


# ---- summing a CSV -------------------------------------------------------------------------------


def test_sum_csv_by_hour_adds_quarter_hours_and_picks_columns():
    lines = [
        "upgrade,timestamp,other,total",
        "0,2018-01-01 05:15:00,9,1.0",  # -> (1, 1, 0)
        "0,2018-01-01 05:30:00,9,2.0",
        "0,2018-01-01 05:45:00,9,3.0",
        "0,2018-01-01 06:00:00,9,4.0",
        "0,2018-01-01 06:15:00,9,10.0",  # -> (1, 1, 1)
    ]
    rows = fetch_oedi.sum_csv_by_hour(lines, {"total_kwh": "total"}, -5, -10)
    assert rows == [
        {"month": 1, "day": 1, "hour": 0, "total_kwh": 10.0},
        {"month": 1, "day": 1, "hour": 1, "total_kwh": 10.0},
    ]


def test_missing_column_is_reported():
    with pytest.raises(fetch_oedi.OEDIError, match="layout"):
        fetch_oedi.sum_csv_by_hour(["a,timestamp", "1,2018-01-01 00:15:00"], {"t": "nope"}, -5, -10)


# ---- streaming a whole file ----------------------------------------------------------------------


class StreamResponse:
    def __init__(self, lines, status_code=200):
        self._lines = lines
        self.status_code = status_code

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_lines(self):
        return (line.encode("utf-8") for line in self._lines)


class StreamSession:
    def __init__(self, responses):
        self.responses = list(responses)

    def get(self, url, stream=None, timeout=None):
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def year_of_quarter_hours(pv_value):
    t = datetime(2018, 1, 1, 0, 15)
    lines = ["upgrade,timestamp,out.electricity.total.energy_consumption..kwh,out.electricity.pv.energy_consumption..kwh"]
    while t <= datetime(2019, 1, 1, 0, 0):
        lines.append(f"0,{t:%Y-%m-%d %H:%M:%S},250,{pv_value}")
        t += timedelta(minutes=15)
    return lines


def test_residential_download_flips_pv_sign_and_returns_a_full_year():
    session = StreamSession([StreamResponse(year_of_quarter_hours(-30))])
    rows = fetch_oedi.download_building_load("residential", "https://x/up00-hi-mobile_home.csv", HD, session)
    assert len(rows) == 8760
    assert rows[0]["total_kwh"] == 1000.0  # four quarter hours of 250
    assert rows[0]["pv_kwh"] == 120.0  # 4 x 30, reported positive


def test_commercial_download_has_no_rooftop_solar():
    lines = [l.rsplit(",", 1)[0].replace("..kwh", ".kwh") for l in year_of_quarter_hours(0)]
    session = StreamSession([StreamResponse(lines)])
    rows = fetch_oedi.download_building_load("commercial", "https://x/up00-g1500030-largeoffice.csv", HD, session)
    assert rows[0]["pv_kwh"] == 0.0 and rows[0]["total_kwh"] == 1000.0


def test_dropped_download_is_retried():
    session = StreamSession([requests.ConnectionError("dropped"), StreamResponse(year_of_quarter_hours(0))])
    rows = fetch_oedi.download_building_load("residential", "https://x/up00-hi-mobile_home.csv", HD, session)
    assert len(rows) == 8760


def test_short_file_is_rejected():
    lines = year_of_quarter_hours(0)[:500]
    session = StreamSession([StreamResponse(lines)])
    with pytest.raises(fetch_oedi.OEDIError, match="8760"):
        fetch_oedi.download_building_load("residential", "https://x/up00-hi-mobile_home.csv", HD, session)


# ---- PVWatts -------------------------------------------------------------------------------------


class JsonResponse:
    def __init__(self, body, status_code=200):
        self._body, self.status_code, self.text = body, status_code, ""

    def json(self):
        return self._body


class JsonSession:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params)))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def pv_body(n=8760, errors=None):
    return {"errors": errors or [], "outputs": {"ac": [float(i) for i in range(n)], "poa": [1.0] * n}}


def test_pvwatts_rows_are_keyed_by_hour_and_use_a_1mw_system():
    session = JsonSession([JsonResponse(pv_body())])
    rows = fetch_nrel.fetch_pvwatts_hourly(MODEL, "KEY", session)
    assert len(rows) == 8760
    assert rows[0] == {"month": 1, "day": 1, "hour": 0, "ac_w": 0.0, "poa_wm2": 1.0}
    assert (rows[-1]["month"], rows[-1]["day"], rows[-1]["hour"]) == (12, 31, 23)
    params = session.calls[0][1]
    assert params["system_capacity"] == "1000" and params["timeframe"] == "hourly" and params["tilt"] == "20"


def test_pvwatts_wrong_length_is_rejected():
    with pytest.raises(fetch_nrel.NRELError, match="8760"):
        fetch_nrel.fetch_pvwatts_hourly(MODEL, "KEY", JsonSession([JsonResponse(pv_body(100))]))


def test_pvwatts_api_errors_are_reported():
    with pytest.raises(fetch_nrel.NRELError, match="bad lat"):
        fetch_nrel.fetch_pvwatts_hourly(MODEL, "KEY", JsonSession([JsonResponse(pv_body(errors=["bad lat"]))]))


def test_pvwatts_bad_key_message_and_no_leak():
    with pytest.raises(fetch_nrel.NRELError, match="NREL_API_KEY"):
        fetch_nrel.fetch_pvwatts_hourly(MODEL, "KEY", JsonSession([JsonResponse({}, status_code=403)]))
    leaky = requests.ConnectionError("failed: https://developer.nlr.gov/x?api_key=SECRET123")
    with pytest.raises(fetch_nrel.NRELError) as excinfo:
        fetch_nrel.fetch_pvwatts_hourly(MODEL, "SECRET123", JsonSession([leaky] * http.MAX_ATTEMPTS))
    assert "SECRET123" not in str(excinfo.value)
