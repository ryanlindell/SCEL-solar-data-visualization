"""Opt-in checks against the REAL PVWatts API for the Mānoa site. Skipped by default, so `pytest` never touches the
network or spends API quota. Run them with:   RUN_LIVE_TESTS=1 python -m pytest tests/test_site_solar_live.py
"""

import os

import pytest
import requests

from pipeline.config import load_settings
from pipeline.fetch_nrel import fetch_pvwatts_site
from pipeline.process_site_solar import annual_summary, check_plausible

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def manoa():
    if os.environ.get("RUN_LIVE_TESTS") != "1":
        pytest.skip("live API tests are off (set RUN_LIVE_TESTS=1)")
    from pipeline.config import ConfigError, get_nrel_api_key

    try:
        key = get_nrel_api_key()
    except ConfigError:
        pytest.skip("NREL_API_KEY is not set")
    site = load_settings().pvwatts_sites["manoa"]
    return site, fetch_pvwatts_site(site, key, requests.Session())


def test_the_real_manoa_series_is_physically_plausible(manoa):
    site, got = manoa
    assert check_plausible(got["rows"], tilt=float(site.pv_system["tilt"])) == []
    s = annual_summary(got["rows"])
    assert 1500 < s["annual_poa_kwh_per_m2"] < 2500  # a real run gave about 2,010 kWh/m2 for a flat surface
    assert all(r["poa_wm2"] >= 0 and (r["dn_wm2"] or 0) >= 0 and (r["df_wm2"] or 0) >= 0 for r in got["rows"])


def test_the_matched_weather_cell_is_close_to_the_query_point_and_recorded(manoa):
    site, got = manoa
    st = got["station_info"]
    assert st["weather_data_source"] and st["solar_resource_file"] and st["location"]
    assert 0 <= st["distance"] < 5000  # metres; NSRDB cells are a few km across
    assert abs(st["lat"] - site.lat) < 0.05 and abs(st["lon"] - site.lon) < 0.05
