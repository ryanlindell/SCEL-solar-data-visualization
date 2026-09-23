"""Tests for nsrdb_model/ (NSRDB 2012 + pvlib). No network: the download is replaced by small synthetic NSRDB files."""

from datetime import date, datetime, timedelta

import pytest
import requests

from nsrdb_model import fetch
from nsrdb_model.config import HOURS_IN_2012, NsrdbError
from nsrdb_model.export import build_web_payload
from nsrdb_model.parse import check_plausible, hourly_frame, read_metadata
from sensor_model.config import ArrayConfig
from sensor_model.seasonal import seasonal_averages
from sensor_model.simulate import simulate_power

HEADER = (
    "Source,Location ID,City,State,Country,Latitude,Longitude,Time Zone,Elevation,Local Time Zone\n"
    "NSRDB,25243,-,-,-,21.29,-157.82,-10,40,-10\n"
    "Year,Month,Day,Hour,Minute,GHI,DNI,DHI,Temperature,Wind Speed,Solar Zenith Angle\n"
)


def midday(t):
    return max(0.0, 900.0 * (1 - abs(t.hour + 0.5 - 12.5) / 6.5))


def nsrdb_text(hours=HOURS_IN_2012, ghi=midday, zenith=None, minute=30, tz=-10):
    """A synthetic year in NSRDB's layout: a clean midday bump (6 AM to 7 PM); the sun is down (zenith 120) exactly
    when there is no light, unless `zenith` says otherwise."""
    lines = [HEADER.replace(",-10,40,-10", f",-10,40,{tz}")]
    start = datetime(2012, 1, 1)
    for i in range(hours):
        t = start + timedelta(hours=i)
        g = ghi(t)
        zenith_deg = zenith(t) if zenith else (30.0 if g > 0 else 120.0)
        lines.append(f"{t.year},{t.month},{t.day},{t.hour},{minute},{g},{g * 0.8},{g * 0.2},24,3.5,{zenith_deg}\n")
    return "".join(lines)


class TestParse:
    def test_metadata_reads_the_grid_cell_and_time_zone(self):
        meta = read_metadata(nsrdb_text(hours=24))
        assert meta["location_id"] == "25243"
        assert (meta["cell_lat"], meta["cell_lon"]) == (21.29, -157.82)
        assert meta["local_time_zone"] == -10

    def test_hourly_frame_maps_ghi_to_poa_and_keeps_real_dates(self):
        frame = hourly_frame(nsrdb_text(hours=48))
        assert list(frame.columns[:3]) == ["date", "hour", "minute"]
        assert frame.iloc[0]["date"] == date(2012, 1, 1) and frame.iloc[24]["date"] == date(2012, 1, 2)
        assert frame.iloc[12]["poa_wm2"] == pytest.approx(900.0)

    def test_a_full_clean_leap_year_passes(self):
        text = nsrdb_text()
        frame = hourly_frame(text)
        assert len(frame) == 8784 and date(2012, 2, 29) in set(frame["date"])
        assert check_plausible(frame, read_metadata(text)) == []

    def test_a_short_year_fails(self):
        text = nsrdb_text(hours=8760)
        assert any("8784" in p for p in check_plausible(hourly_frame(text), read_metadata(text)))

    def test_utc_timestamps_are_refused(self):
        text = nsrdb_text(tz=0)
        assert any("UTC-10" in p for p in check_plausible(hourly_frame(text), read_metadata(text)))

    def test_sunlight_with_the_sun_below_the_horizon_is_caught(self):
        # Light runs 9 AM-10 PM while the sun is only up 6 AM-7 PM: a series shifted by hours.
        text = nsrdb_text(ghi=lambda t: 500.0 if 9 <= t.hour <= 21 else 0.0, zenith=lambda t: 30.0 if 6 <= t.hour <= 18 else 120.0)
        assert any("below the horizon" in p for p in check_plausible(hourly_frame(text), read_metadata(text)))


class TestSharedPhysics:
    def test_the_nsrdb_line_uses_the_sensor_lines_array_and_averaging_unchanged(self):
        hourly = simulate_power(hourly_frame(nsrdb_text()), ArrayConfig())
        # Same array: DC is 4,300 kW x GHI/1000 at STC, and AC is capped at the same 3,583 kW ceiling.
        noon = hourly[(hourly["date"] == date(2012, 6, 1)) & (hourly["hour"] == 12)].iloc[0]
        assert noon["dc_w"] == pytest.approx(4_300_000 * 0.9)
        assert hourly["ac_w"].max() <= 4_300_000 / 1.2 + 1
        avg = seasonal_averages(hourly, {"summer": (6, 7, 8)}, weekdays_only=False)["summer"]
        assert avg["days_averaged"] == 92  # every day of June-August 2012, no gaps

    def test_web_payload_rows_match_the_other_estimates_shape(self):
        hourly = simulate_power(hourly_frame(nsrdb_text()), ArrayConfig())
        months, labels = {"summer": (6, 7, 8)}, {"summer": "Summer"}
        averages = {d: seasonal_averages(hourly, months, weekdays_only=(d == "weekdays")) for d in ("weekdays", "all_days")}
        meta = read_metadata(nsrdb_text(hours=24))
        payload = build_web_payload(ArrayConfig(), averages, months, labels, meta, {"hours": 8784})
        row = payload["rows"][12]
        assert set(row) == {"day_set", "season", "hour", "n_days", "poa_wm2", "dc_w", "ac_w"}
        assert payload["grid_cell"]["location_id"] == "25243"


class TestFetch:
    class _Resp:
        def __init__(self, status, text):
            self.status_code, self.text = status, text

    def test_credentials_never_appear_in_an_error(self, monkeypatch):
        def boom(*a, **k):
            raise requests.ConnectionError("https://x/?api_key=SECRETKEY&email=me@example.com")

        monkeypatch.setattr(fetch.time, "sleep", lambda s: None)
        session = requests.Session()
        monkeypatch.setattr(session, "get", boom)
        with pytest.raises(NsrdbError) as err:
            fetch.download_csv(21.3, -157.8, "SECRETKEY", "me@example.com", session)
        assert "SECRETKEY" not in str(err.value) and "me@example.com" not in str(err.value)

    def test_an_error_body_echoing_the_credentials_is_scrubbed(self, monkeypatch):
        session = requests.Session()
        monkeypatch.setattr(session, "get", lambda *a, **k: self._Resp(400, "bad request for SECRETKEY / me@example.com"))
        with pytest.raises(NsrdbError) as err:
            fetch.download_csv(21.3, -157.8, "SECRETKEY", "me@example.com", session)
        assert "SECRETKEY" not in str(err.value) and "me@example.com" not in str(err.value)

    def test_the_saved_request_never_contains_credentials(self):
        params = fetch.request_params(21.2984, -157.8174)
        assert "api_key" not in params and "email" not in params
        assert params["names"] == "2012" and params["utc"] == "false" and params["leap_day"] == "true"
