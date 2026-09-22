"""Tests for the single-site PVWatts model (UH Mānoa): fetch, cache, checks, seasonal averages and export.

The Oʻahu-wide PVWatts call's regression guard lives in test_fetch_hourly.py. Here: the new site call must not
disturb it, and the new pieces must do what they say.
"""

import calendar
import csv
import json
from contextlib import closing
from datetime import date
from pathlib import Path

import pytest
import requests

from pipeline import cache, fetch_nrel, http
from pipeline.config import ConfigError, PvSite, Season, load_settings
from pipeline.export_site_solar import build_payload, write_hourly_csv, write_seasonal_csv
from pipeline.process_hourly import compute_duck_curve
from pipeline.process_site_solar import HOURS_PER_YEAR, SERIES_FIELDS, annual_summary, check_plausible, seasonal_averages

ROOT = Path(__file__).resolve().parent.parent
SEASONS = {"winter": (12, 1, 2), "spring": (3, 4, 5), "summer": (6, 7, 8), "fall": (9, 10, 11)}

MANOA = PvSite(
    id="manoa", label="UH Mānoa campus (placeholder)", lat=21.2984, lon=-157.8174,
    pv_system={"tilt": 0, "azimuth": 180, "array_type": 0, "module_type": 0, "losses": 0}, placeholder=True,
)
STATION = {
    "lat": 21.29000091552734, "lon": -157.8200073242188, "elev": 76.08333587646484, "tz": -10.0, "location": "17574",
    "city": "", "state": "Hawaii", "country": "United States", "solar_resource_file": "17574.csv", "distance": 948,
    "weather_data_source": "NSRDB PSM V3 GOES tmy-2020 3.2.0",
}


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(http.time, "sleep", lambda s: None)


# ---- a stand-in for PVWatts' answer ------------------------------------------------------------------


def hour_keys():
    for month in range(1, 13):
        for day in range(1, calendar.monthrange(2018, month)[1] + 1):
            for hour in range(24):
                yield month, day, hour


def sunny_year(scale=1.0):
    """A clean synthetic year of PVWatts-like rows: a daytime half-sine of sunlight (peak 800 W/m2 x scale), 15%
    of it diffuse, beam >= what is needed, DC proportional to it and AC 4% lower (never clipped)."""
    import math

    rows = []
    for month, day, hour in hour_keys():
        sun = scale * 800 * math.sin(math.pi * (hour + 0.5 - 6) / 12) if 6 <= hour < 18 else 0.0
        rows.append({
            "month": month, "day": day, "hour": hour, "poa_wm2": sun, "dn_wm2": sun, "df_wm2": 0.15 * sun,
            "dc_w": 900.0 * sun, "ac_w": 0.96 * 900.0 * sun, "tamb_c": 24.0, "tcell_c": 30.0, "wspd_ms": 5.0, "albedo": 0.2,
        })
    return rows


def body_from(rows, station=STATION):
    names = {"poa_wm2": "poa", "dn_wm2": "dn", "df_wm2": "df", "dc_w": "dc", "ac_w": "ac", "tamb_c": "tamb",
             "tcell_c": "tcell", "wspd_ms": "wspd", "albedo": "alb"}
    return {
        "errors": [], "warnings": [], "version": "8.5.0", "station_info": station,
        "outputs": {names[c]: [r[c] for r in rows] for c in names},
    }


class Resp:
    def __init__(self, body, status_code=200):
        self._body, self.status_code, self.text = body, status_code, ""

    def json(self):
        return self._body


class Session:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, list(params)))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


# ---- fetch: the site call ----------------------------------------------------------------------------


def test_the_site_request_uses_the_sites_own_coordinates_and_panel_settings():
    session = Session(Resp(body_from(sunny_year())))
    fetch_nrel.fetch_pvwatts_site(MANOA, "KEY", session)
    url, params = session.calls[0]
    assert url == "https://developer.nlr.gov/api/pvwatts/v8.json"
    assert params == [
        ("api_key", "KEY"), ("lat", "21.2984"), ("lon", "-157.8174"), ("system_capacity", "1000"), ("timeframe", "hourly"),
        ("dataset", "nsrdb"), ("tilt", "0"), ("azimuth", "180"), ("array_type", "0"), ("module_type", "0"), ("losses", "0"),
    ]


def test_tilt_azimuth_and_location_are_changed_by_changing_the_site_not_the_code():
    tilted = PvSite(id="x", label="x", lat=21.5, lon=-158.0, pv_system={"tilt": 25, "azimuth": 200, "array_type": 0, "module_type": 0, "losses": 0})
    session = Session(Resp(body_from(sunny_year())))
    fetch_nrel.fetch_pvwatts_site(tilted, "KEY", session)
    sent = dict(session.calls[0][1])
    assert (sent["lat"], sent["lon"], sent["tilt"], sent["azimuth"]) == ("21.5", "-158.0", "25", "200")


def test_everything_pvwatts_returns_is_kept_along_with_the_whole_station_block():
    rows = sunny_year()
    got = fetch_nrel.fetch_pvwatts_site(MANOA, "KEY", Session(Resp(body_from(rows))))
    assert len(got["rows"]) == HOURS_PER_YEAR
    assert set(got["rows"][0]) == {"month", "day", "hour", *SERIES_FIELDS}
    noon = next(r for r in got["rows"] if (r["month"], r["day"], r["hour"]) == (6, 21, 12))
    assert noon["poa_wm2"] > 700 and noon["dn_wm2"] == noon["poa_wm2"] and noon["dc_w"] > noon["ac_w"] > 0
    assert got["station_info"] == STATION  # every field, unchanged (grid cell, distance, source, resource file)
    assert got["pvwatts_version"] == "8.5.0"
    assert "api_key" not in got["request"] and got["request"]["tilt"] == "0" and got["request"]["losses"] == "0"


def test_optional_fields_that_do_not_come_back_are_none_but_poa_and_ac_are_required():
    body = body_from(sunny_year())
    del body["outputs"]["dn"], body["outputs"]["df"]
    got = fetch_nrel.fetch_pvwatts_site(MANOA, "KEY", Session(Resp(body)))
    assert got["rows"][0]["dn_wm2"] is None and got["rows"][0]["df_wm2"] is None and got["rows"][0]["poa_wm2"] is not None

    body = body_from(sunny_year())
    del body["outputs"]["poa"]
    with pytest.raises(fetch_nrel.NRELError, match="poa"):
        fetch_nrel.fetch_pvwatts_site(MANOA, "KEY", Session(Resp(body)))


def test_a_short_series_errors_and_a_missing_station_block_is_tolerated():
    body = body_from(sunny_year())
    body["outputs"]["tamb"] = body["outputs"]["tamb"][:100]
    with pytest.raises(fetch_nrel.NRELError, match="8760.*tamb"):
        fetch_nrel.fetch_pvwatts_site(MANOA, "KEY", Session(Resp(body)))
    body = body_from(sunny_year())
    del body["station_info"]
    assert fetch_nrel.fetch_pvwatts_site(MANOA, "KEY", Session(Resp(body)))["station_info"] == {}
    with pytest.raises(fetch_nrel.NRELError, match="bad lat"):
        fetch_nrel.fetch_pvwatts_site(MANOA, "KEY", Session(Resp({"errors": ["bad lat"]})))


def test_the_site_call_never_leaks_the_key():
    leaky = requests.ConnectionError("failed: https://developer.nlr.gov/x?api_key=SECRET123")
    with pytest.raises(fetch_nrel.NRELError) as excinfo:
        fetch_nrel.fetch_pvwatts_site(MANOA, "SECRET123", Session(*[leaky] * http.MAX_ATTEMPTS))
    assert "SECRET123" not in str(excinfo.value)


# ---- cache: the new tables sit beside the old ones ------------------------------------------------------


def oahu_rows(n=24):
    return [{"month": 1, "day": 1, "hour": h, "ac_w": 100.0 * h, "poa_wm2": 10.0 * h} for h in range(n)]


def dump(conn, table):
    return [tuple(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY 1, 2, 3, 4")]


def site_fetch(rows=None, request=None):
    return {"rows": rows or fetch_nrel.fetch_pvwatts_site(MANOA, "K", Session(Resp(body_from(sunny_year()))))["rows"],
            "station_info": STATION, "request": request or fetch_nrel.site_request(MANOA), "pvwatts_version": "8.5.0"}


def test_storing_a_site_leaves_the_oahu_rows_byte_identical(tmp_path):
    with closing(cache.connect(tmp_path / "raw.sqlite")) as conn:
        cache.upsert_pvwatts_hourly(conn, "oahu", oahu_rows())
        before = dump(conn, "raw_pvwatts_hourly")
        # The site's 8,760 hours use the very same (month, day, hour) keys as the region's rows: they must not collide.
        assert cache.replace_pvwatts_site(conn, "manoa", MANOA.label, site_fetch())["fetched"] == HOURS_PER_YEAR
        assert dump(conn, "raw_pvwatts_hourly") == before  # every column, including fetched_at
        assert cache.has_pvwatts(conn, "oahu") and len(cache.read_pvwatts_hourly(conn, "oahu")) == 24
        assert conn.execute("SELECT COUNT(*) FROM raw_pvwatts_site_hourly").fetchone()[0] == HOURS_PER_YEAR


def test_opening_an_existing_cache_only_adds_tables_it_never_alters_or_drops_the_old_ones(tmp_path):
    path = tmp_path / "raw.sqlite"
    with closing(cache.connect(path)) as conn:
        cache.upsert_pvwatts_hourly(conn, "oahu", oahu_rows())
        with conn:  # turn it back into a cache from before sites existed
            conn.execute("DROP TABLE raw_pvwatts_site")
            conn.execute("DROP TABLE raw_pvwatts_site_hourly")
        before = dump(conn, "raw_pvwatts_hourly")
        schema_before = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'raw_pvwatts_hourly'").fetchone()[0]
    with closing(cache.connect(path)) as conn:  # the next run of any pipeline
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert {"raw_pvwatts_site", "raw_pvwatts_site_hourly"} <= tables
        assert dump(conn, "raw_pvwatts_hourly") == before
        assert conn.execute("SELECT sql FROM sqlite_master WHERE name = 'raw_pvwatts_hourly'").fetchone()[0] == schema_before


def test_storing_a_site_twice_changes_nothing_and_keeps_the_full_station_block(tmp_path):
    with closing(cache.connect(tmp_path / "raw.sqlite")) as conn:
        fetched = site_fetch()
        assert cache.replace_pvwatts_site(conn, "manoa", MANOA.label, fetched) == {"fetched": 8760, "new": 8760, "changed": 0}
        assert cache.replace_pvwatts_site(conn, "manoa", MANOA.label, fetched) == {"fetched": 8760, "new": 0, "changed": 0}
        meta = cache.read_pvwatts_site(conn, "manoa")
        assert meta["station_info"] == STATION and meta["request"] == fetched["request"]
        assert (meta["station_distance_m"], meta["station_location"], meta["solar_resource_file"]) == (948, "17574", "17574.csv")
        assert meta["weather_data_source"] == "NSRDB PSM V3 GOES tmy-2020 3.2.0"
        assert (meta["query_lat"], meta["query_lon"], meta["station_lat"]) == (21.2984, -157.8174, STATION["lat"])
        rows = cache.read_pvwatts_site_hourly(conn, "manoa")
        assert len(rows) == 8760 and rows[0]["month"] == 1 and rows[-1]["hour"] == 23 and rows[6 * 24]["poa_wm2"] == fetched["rows"][6 * 24]["poa_wm2"]


def test_one_site_can_be_replaced_without_touching_another(tmp_path):
    other = PvSite(id="b", label="b", lat=21.6, lon=-158.1, pv_system=MANOA.pv_system)
    with closing(cache.connect(tmp_path / "raw.sqlite")) as conn:
        cache.replace_pvwatts_site(conn, "manoa", "m", site_fetch())
        cache.replace_pvwatts_site(conn, "b", "b", site_fetch(request=fetch_nrel.site_request(other)))
        b_before = cache.read_pvwatts_site_hourly(conn, "b")
        cache.replace_pvwatts_site(conn, "manoa", "m", site_fetch(rows=[{**r, "poa_wm2": 1.0} for r in site_fetch()["rows"]]))
        assert cache.read_pvwatts_site_hourly(conn, "b") == b_before
        assert cache.read_pvwatts_site(conn, "b")["query_lat"] == 21.6


def test_a_cached_site_is_refetched_when_its_settings_change_but_not_otherwise(tmp_path):
    with closing(cache.connect(tmp_path / "raw.sqlite")) as conn:
        request = fetch_nrel.site_request(MANOA)
        assert cache.pvwatts_site_stale_reason(conn, "manoa", request) == "not cached yet"
        cache.replace_pvwatts_site(conn, "manoa", MANOA.label, site_fetch())
        assert cache.pvwatts_site_stale_reason(conn, "manoa", request) is None
        tilted = PvSite(id="manoa", label="m", lat=21.2984, lon=-157.8174, pv_system={**MANOA.pv_system, "tilt": 20})
        reason = cache.pvwatts_site_stale_reason(conn, "manoa", fetch_nrel.site_request(tilted))
        assert reason == "settings changed (tilt: 0 -> 20)"
        with conn:
            conn.execute("DELETE FROM raw_pvwatts_site_hourly WHERE hour = 5")
        assert "incomplete" in cache.pvwatts_site_stale_reason(conn, "manoa", request)


# ---- checks: is the series physically plausible? -------------------------------------------------------------


def test_a_clean_year_passes_and_a_gently_off_diffuse_value_is_tolerated():
    assert check_plausible(sunny_year(), tilt=0) == []
    rows = sunny_year()
    noon = next(r for r in rows if (r["month"], r["day"], r["hour"]) == (6, 21, 12))
    noon["df_wm2"] = noon["poa_wm2"] + 0.17  # the size of the real quirk seen in PVWatts' own output
    assert check_plausible(rows, tilt=0) == []


@pytest.mark.parametrize(
    "break_it, expected",
    [
        (lambda rows: rows[12].update(poa_wm2=-5.0), "negative"),
        (lambda rows: rows[12].update(poa_wm2=1600.0, dn_wm2=1600.0), "above the plausible limit"),
        (lambda rows: rows[3].update(poa_wm2=50.0, dn_wm2=50.0), "small hours"),
        (lambda rows: rows[12].update(df_wm2=rows[12]["poa_wm2"] + 50), "below the diffuse"),
        (lambda rows: rows[12].update(poa_wm2=rows[12]["dn_wm2"] + rows[12]["df_wm2"] + 50), "exceeds beam plus diffuse"),
        (lambda rows: rows[12].update(ac_w=rows[12]["dc_w"] * 2), "AC power exceeds DC"),
        (lambda rows: rows[12].update(tamb_c=80.0), "implausible"),
    ],
)
def test_impossible_values_are_flagged(break_it, expected):
    rows = sunny_year()
    break_it(rows)
    assert any(expected in p for p in check_plausible(rows, tilt=0)), check_plausible(rows, tilt=0)


def test_a_missing_hour_a_duplicate_hour_and_a_dim_or_too_bright_year_are_flagged():
    rows = sunny_year()
    assert any("expected 8760" in p for p in check_plausible(rows[:-1]))
    dup = sunny_year()
    dup[-1] = dict(dup[0])
    assert any("complete non-leap year" in p for p in check_plausible(dup))
    assert any("annual plane-of-array sunshine" in p for p in check_plausible(sunny_year(scale=0.3)))
    assert any("annual plane-of-array sunshine" in p for p in check_plausible(sunny_year(scale=1.5)))


def test_flat_surface_consistency_is_only_demanded_of_a_flat_surface():
    rows = sunny_year()
    rows[12]["df_wm2"] = rows[12]["poa_wm2"] + 50  # a tilted plane can legitimately receive less than diffuse-horizontal
    assert check_plausible(rows, tilt=25) == []


def test_a_series_shifted_by_the_5_hour_east_coast_offset_is_caught():
    rows = sunny_year()
    shifted = [{**r, "hour": (r["hour"] + 5) % 24} for r in rows]
    assert any("small hours" in p or "late evening" in p for p in check_plausible(shifted, tilt=0))


def test_the_yearly_summary_reports_sunshine_and_where_the_inverter_clips():
    rows = sunny_year()
    for r in rows:
        r["ac_w"] = min(r["ac_w"], 600_000.0)
    s = annual_summary(rows)
    assert s["hours"] == 8760 and 1800 < s["annual_poa_kwh_per_m2"] < 2600 and 790 < s["peak_poa_wm2"] <= 800
    assert s["ac_max_w"] == 600_000.0 and s["ac_hours_at_max"] > 100 and s["ac_over_dc_energy"] < 0.96
    assert s["hours_with_sun"] == 365 * 12


# ---- seasonal averages: the duck curve's own definition --------------------------------------------------------


def test_weekday_counts_are_the_duck_curves_64_66_66_65_and_all_days_90_92_92_91():
    seasons = {sid: s.months for sid, s in load_settings().hourly_data.seasons.items()}
    assert seasons == SEASONS  # the seasons come from the same config the duck curve uses
    weekdays = seasonal_averages(sunny_year(), seasons, weekdays_only=True)
    every = seasonal_averages(sunny_year(), seasons, weekdays_only=False)
    assert [weekdays[s]["days_averaged"] for s in seasons] == [64, 66, 66, 65]
    assert [every[s]["days_averaged"] for s in seasons] == [90, 92, 92, 91]


def test_the_weekday_counts_match_what_the_published_duck_curve_averaged():
    published = json.loads((ROOT / "web" / "data" / "duck_curve.json").read_text(encoding="utf-8"))["regions"]["oahu"]["seasons"]
    mine = seasonal_averages(sunny_year(), SEASONS)
    assert {sid: s["days_averaged"] for sid, s in mine.items()} == {sid: s["weekdays_averaged"] for sid, s in published.items()}


def test_which_days_are_averaged_is_checked_by_hand_for_june_2018():
    # June 1, 2018 was a Friday. Weekdays: 1, 4-8, 11-15, 18-22, 25-29.
    assert date(2018, 6, 1).weekday() == 4
    weekdays = [1, 4, 5, 6, 7, 8, 11, 12, 13, 14, 15, 18, 19, 20, 21, 22, 25, 26, 27, 28, 29]
    rows = [{"month": m, "day": d, "hour": h, "poa_wm2": float(d) if h == 12 else 0.0} for m, d, h in hour_keys()]
    wk = seasonal_averages(rows, {"june": (6,)}, fields=("poa_wm2",), weekdays_only=True)["june"]
    assert wk["days_averaged"] == 21 and wk["hours"][12]["poa_wm2"] == pytest.approx(sum(weekdays) / 21) and wk["hours"][11]["poa_wm2"] == 0
    every = seasonal_averages(rows, {"june": (6,)}, fields=("poa_wm2",), weekdays_only=False)["june"]
    assert every["days_averaged"] == 30 and every["hours"][12]["poa_wm2"] == pytest.approx(15.5)


def test_a_column_that_is_missing_for_every_day_averages_to_none_not_zero():
    rows = [{**r, "dn_wm2": None} for r in sunny_year()]
    avg = seasonal_averages(rows, {"summer": (6, 7, 8)})["summer"]["hours"]
    assert avg[12]["dn_wm2"] is None and avg[12]["poa_wm2"] > 500 and len(avg) == 24


def test_seasonal_averaging_matches_the_duck_curve_code_it_claims_to_follow():
    """Feed one series through BOTH implementations. With each month's utility-solar size set so its scale is exactly 1,
    compute_duck_curve's utility_solar_mw is the plain seasonal weekday average of ac_w / 1e6, and so is ours."""
    pv = [{"month": m, "day": d, "hour": h, "ac_w": 1000.0 * d + 10.0 * h + m, "poa_wm2": 0.0} for m, d, h in hour_keys()]
    load = [{"sector": "commercial", "building_type": "office", "month": m, "day": d, "hour": h, "total_kwh": 1000.0, "pv_kwh": 0.0}
            for m, d, h in hour_keys()]
    monthly = []
    for month in range(1, 13):
        farm_mwh = sum(r["ac_w"] for r in pv if r["month"] == month) / 1e6  # what the model divides by
        monthly.append({"region": "oahu", "period": f"2024-{month:02d}", "provisional": False, "total_mwh": 1e5,
                        "generation_by_fuel_mwh": {"SUN": farm_mwh, "WND": 0.0}})
    rooftop = {f"2024-{m:02d}": 0.0 for m in range(1, 13)}
    duck = compute_duck_curve(load, pv, monthly, rooftop, 0.5, 0.5, SEASONS)

    mine = seasonal_averages(pv, SEASONS, fields=("ac_w",), weekdays_only=True)
    for sid in SEASONS:
        assert duck["seasons"][sid]["weekdays_averaged"] == mine[sid]["days_averaged"]
        for h in range(24):
            assert duck["seasons"][sid]["hours"][h]["utility_solar_mw"] == pytest.approx(mine[sid]["hours"][h]["ac_w"] / 1e6, rel=1e-9)


# ---- config and export ---------------------------------------------------------------------------------------------


def test_the_manoa_site_is_configured_as_a_flat_lossless_placeholder_and_oahu_is_untouched():
    settings = load_settings()
    site = settings.pvwatts_sites["manoa"]
    assert site.placeholder is True and (site.lat, site.lon) == (21.2984, -157.8174)
    assert site.pv_system == {"tilt": 0, "azimuth": 180, "array_type": 0, "module_type": 0, "losses": 0}
    oahu = settings.regions["oahu"].hourly  # the duck curve's inputs are exactly what they were
    assert (oahu.lat, oahu.lon) == (21.3, -157.86)
    assert oahu.pv_system == {"tilt": 20, "azimuth": 180, "array_type": 0, "module_type": 0, "losses": 14}


def test_a_site_missing_a_required_pvwatts_setting_is_rejected_at_load_time(tmp_path):
    text = (ROOT / "config" / "regions.yaml").read_text(encoding="utf-8")
    broken = text.replace("pv_system: {tilt: 0, azimuth: 180, array_type: 0, module_type: 0, losses: 0}",
                          "pv_system: {tilt: 0, azimuth: 180, array_type: 0, module_type: 0}")
    assert broken != text
    (tmp_path / "regions.yaml").write_text(broken, encoding="utf-8")
    with pytest.raises(ConfigError, match="losses"):
        load_settings(tmp_path / "regions.yaml")


def exported(tmp_path):
    rows = sunny_year()
    meta = {"query_lat": 21.2984, "query_lon": -157.8174, "station_info": STATION, "request": fetch_nrel.site_request(MANOA),
            "pvwatts_version": "8.5.0", "fetched_at": "2026-09-21T00:00:00+00:00"}
    averages = {"weekdays": seasonal_averages(rows, SEASONS, weekdays_only=True), "all_days": seasonal_averages(rows, SEASONS, weekdays_only=False)}
    seasons = {sid: Season(sid.title(), m) for sid, m in SEASONS.items()}
    payload = build_payload(MANOA, meta, annual_summary(rows), averages, seasons)
    write_hourly_csv(rows, tmp_path / "hourly.csv")
    write_seasonal_csv(averages, seasons, tmp_path / "seasonal.csv")
    return payload


def test_the_export_says_plainly_that_it_is_a_separate_placeholder_manoa_dataset_with_the_station_block(tmp_path):
    payload = exported(tmp_path)
    assert payload["meta"]["dataset"] == "manoa_pvwatts_model" and "separate from the Oʻahu-wide duck curve" in payload["meta"]["scope"]
    assert payload["site"]["coordinates_are_placeholder"] is True
    cell = payload["site"]["weather_cell"]
    assert (cell["distance_from_query_m"], cell["nsrdb_cell_id"], cell["solar_resource_file"]) == (948, "17574", "17574.csv")
    assert cell["weather_data_source"] == "NSRDB PSM V3 GOES tmy-2020 3.2.0" and payload["site"]["station_info_as_returned"] == STATION
    a = payload["assumptions"]
    assert (a["tilt_deg"], a["azimuth_deg"], a["losses_pct"], a["system_capacity_kw_dc"]) == (0, 180, 0, 1000)
    assert "api_key" not in json.dumps(payload)
    assert set(payload["seasonal_averages"]) == {"weekdays", "all_days"}
    assert [payload["seasonal_averages"]["weekdays"][s]["days_averaged"] for s in SEASONS] == [64, 66, 66, 65]


def test_the_csv_files_have_every_hour_and_every_seasonal_hour(tmp_path):
    exported(tmp_path)
    with open(tmp_path / "hourly.csv", encoding="utf-8", newline="") as f:
        hourly = list(csv.reader(f))
    assert hourly[0] == ["month", "day", "hour", *SERIES_FIELDS] and len(hourly) == 1 + 8760
    with open(tmp_path / "seasonal.csv", encoding="utf-8", newline="") as f:
        seasonal = list(csv.reader(f))
    assert len(seasonal) == 1 + 2 * 4 * 24 and {r[0] for r in seasonal[1:]} == {"weekdays", "all_days"}


def test_site_exports_go_to_their_own_folder_never_to_the_websites_data_folder():
    from pipeline.config import SITE_SOLAR_DIR

    assert SITE_SOLAR_DIR / "manoa" != ROOT / "web" / "data" and "web" not in (SITE_SOLAR_DIR / "manoa").relative_to(ROOT).parts


# ---- the compact JSON for the site's own page -----------------------------------------------------------------------


def web_payload():
    from pipeline.export_site_solar import build_web_payload

    rows = sunny_year()
    meta = {"query_lat": 21.2984, "query_lon": -157.8174, "station_info": STATION, "request": fetch_nrel.site_request(MANOA),
            "pvwatts_version": "8.5.0", "fetched_at": "2026-09-21T00:00:00+00:00"}
    averages = {"weekdays": seasonal_averages(rows, SEASONS, weekdays_only=True), "all_days": seasonal_averages(rows, SEASONS, weekdays_only=False)}
    seasons = {sid: Season(sid.title(), m) for sid, m in SEASONS.items()}
    return build_web_payload(MANOA, meta, annual_summary(rows), averages, seasons), averages


def test_the_pages_json_has_every_hour_of_every_season_and_day_set_and_matches_the_averages():
    payload, averages = web_payload()
    assert len(payload["rows"]) == 2 * 4 * 24
    assert {(r["day_set"], r["season"]) for r in payload["rows"]} == {(d, s) for d in ("weekdays", "all_days") for s in SEASONS}
    assert set(payload["rows"][0]) == {"day_set", "season", "hour", "poa_wm2", "dc_w", "ac_w"}
    noon = next(r for r in payload["rows"] if (r["day_set"], r["season"], r["hour"]) == ("weekdays", "summer", 12))
    avg = averages["weekdays"]["summer"]["hours"][12]
    assert noon["dc_w"] == round(avg["dc_w"]) and noon["ac_w"] == round(avg["ac_w"]) and noon["poa_wm2"] == round(avg["poa_wm2"], 1)
    assert payload["seasons"]["winter"]["days"] == {"weekdays": 64, "all_days": 90}
    assert payload["seasons"]["fall"]["days"] == {"weekdays": 65, "all_days": 91}


def test_the_pages_json_labels_itself_as_modeled_placeholder_and_keeps_the_weather_cell_and_no_key():
    payload, _ = web_payload()
    assert payload["meta"]["kind"] == "modeled" and payload["meta"]["dataset"] == "manoa_solar"
    assert payload["site"]["coordinates_are_placeholder"] is True and payload["site"]["query"] == {"lat": 21.2984, "lon": -157.8174}
    cell = payload["site"]["weather_cell"]
    assert (cell["nsrdb_cell_id"], cell["distance_from_query_m"]) == ("17574", 948)
    a = payload["assumptions"]
    assert (a["tilt_deg"], a["losses_pct"], a["system_capacity_kw_dc"]) == (0, 0, 1000)
    assert payload["annual_summary"]["ac_over_dc_energy"] is not None
    assert "api_key" not in json.dumps(payload)


def test_the_page_json_refuses_a_series_it_cannot_draw():
    from pipeline.export_site_solar import build_web_payload

    rows = [{**r, "dc_w": None} for r in sunny_year()]
    averages = {"weekdays": seasonal_averages(rows, SEASONS)}
    seasons = {sid: Season(sid.title(), m) for sid, m in SEASONS.items()}
    meta = {"query_lat": 21.2984, "query_lon": -157.8174, "station_info": STATION, "request": {}, "pvwatts_version": "8.5.0", "fetched_at": ""}
    with pytest.raises(ValueError, match="dc_w"):
        build_web_payload(MANOA, meta, {}, averages, seasons)


def test_the_pages_json_has_its_own_file_and_never_the_duck_curves():
    from pipeline.config import DUCK_CURVE_JSON, SITE_WEB_DIR

    assert (SITE_WEB_DIR / "manoa_solar.json").parent == DUCK_CURVE_JSON.parent
    assert (SITE_WEB_DIR / "manoa_solar.json") != DUCK_CURVE_JSON
