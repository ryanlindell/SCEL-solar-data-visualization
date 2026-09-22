"""Fetch a typical year of hourly solar output from NREL's PVWatts API.

PVWatts models a solar system at a given latitude/longitude using a "typical meteorological
year" (TMY) of satellite-derived weather. We ask for a 1 MW reference system, so the result is
"watts produced per megawatt installed" for each of the 8,760 hours of a year. This module only
fetches - it never touches the database.

(NREL was renamed NLR; the API now lives at developer.nlr.gov.)
"""

from datetime import datetime, timedelta

import requests

from .config import HourlyModel, PvSite
from .http import get_json

PVWATTS_URL = "https://developer.nlr.gov/api/pvwatts/v8.json"
SYSTEM_KW = 1000  # a 1 MW reference system
HOURS_PER_YEAR = 8760


class NRELError(Exception):
    """NREL's API could not be reached, or it returned an error."""


def hour_of_year_to_key(index: int) -> tuple[int, int, int]:
    """0 -> (1, 1, 0): the hour beginning at midnight on Jan 1. A non-leap year, like PVWatts."""
    t = datetime(2018, 1, 1) + timedelta(hours=index)
    return t.month, t.day, t.hour


def _pvwatts_params(lat: float, lon: float, pv_system: dict, api_key: str) -> list[tuple[str, str]]:
    """The exact query string, in order. Shared by the Oʻahu-wide call and the site-specific call."""
    return [
        ("api_key", api_key),
        ("lat", str(lat)),
        ("lon", str(lon)),
        ("system_capacity", str(SYSTEM_KW)),
        ("timeframe", "hourly"),
        ("dataset", "nsrdb"),
        *[(k, str(v)) for k, v in pv_system.items()],
    ]


def _request_pvwatts(lat: float, lon: float, pv_system: dict, api_key: str, session: requests.Session) -> dict:
    body = get_json(session, PVWATTS_URL, _pvwatts_params(lat, lon, pv_system, api_key), NRELError, "NREL_API_KEY")
    if body.get("errors"):
        raise NRELError(f"PVWatts reported errors: {body['errors']}")
    return body


def fetch_pvwatts_hourly(
    model: HourlyModel, api_key: str, session: requests.Session | None = None
) -> list[dict]:
    """Return 8,760 rows: month, day, hour (Hawaiʻi standard time), ac_w, poa_wm2.

    This is the Oʻahu-wide duck curve's solar shape. Its request and rows are pinned by a regression test
    (tests/test_fetch_hourly.py), so changes for other uses must go through fetch_pvwatts_site instead.
    """
    session = session or requests.Session()
    body = _request_pvwatts(model.lat, model.lon, model.pv_system, api_key, session)

    ac = body["outputs"]["ac"]
    poa = body["outputs"]["poa"]
    if len(ac) != HOURS_PER_YEAR or len(poa) != HOURS_PER_YEAR:
        raise NRELError(f"Expected {HOURS_PER_YEAR} hourly values, got {len(ac)} (ac) and {len(poa)} (poa).")

    rows = []
    for i in range(HOURS_PER_YEAR):
        month, day, hour = hour_of_year_to_key(i)
        rows.append({"month": month, "day": day, "hour": hour, "ac_w": float(ac[i]), "poa_wm2": float(poa[i])})
    return rows


# ---- a single site, on its own (e.g. UH Mānoa), with everything PVWatts tells us kept --------------------------

# Our column name -> PVWatts' hourly output name. `ac` and `poa` must be present; the rest are kept when they come back.
SITE_FIELDS = {
    "poa_wm2": "poa",  # plane-of-array irradiance, W/m2 (tilt 0: this is global horizontal irradiance)
    "dn_wm2": "dn",  # beam (direct normal) irradiance, W/m2
    "df_wm2": "df",  # diffuse horizontal irradiance, W/m2
    "dc_w": "dc",  # DC power of the 1,000 kW (DC) reference array, W
    "ac_w": "ac",  # AC power after the inverter model (clipped at the inverter's rating), W
    "tamb_c": "tamb",  # ambient temperature, deg C
    "tcell_c": "tcell",  # modeled cell temperature, deg C
    "wspd_ms": "wspd",  # wind speed, m/s
    "albedo": "alb",  # ground reflectance used
}
SITE_REQUIRED = ("poa_wm2", "ac_w")


def site_request(site: PvSite) -> dict[str, str]:
    """Every parameter sent for this site (never the API key), as the strings sent. Stored with the data so a later
    change to the site's settings is noticed instead of silently reusing rows fetched with the old ones."""
    return {k: v for k, v in _pvwatts_params(site.lat, site.lon, site.pv_system, "")[1:]}


def fetch_pvwatts_site(site: PvSite, api_key: str, session: requests.Session | None = None) -> dict:
    """Query PVWatts for one site and keep everything that matters for comparing with a sensor.

    Returns {"rows": 8,760 dicts (month, day, hour + SITE_FIELDS columns; None where PVWatts omitted a field),
    "station_info": the API's whole station_info block, "request": the parameters sent, "pvwatts_version": str}.
    """
    session = session or requests.Session()
    body = _request_pvwatts(site.lat, site.lon, site.pv_system, api_key, session)
    outputs = body.get("outputs") or {}

    series: dict[str, list] = {}
    for column, name in SITE_FIELDS.items():
        values = outputs.get(name)
        if values is None:
            if column in SITE_REQUIRED:
                raise NRELError(f"PVWatts did not return '{name}' hourly values.")
            continue
        if len(values) != HOURS_PER_YEAR:
            raise NRELError(f"Expected {HOURS_PER_YEAR} hourly values for '{name}', got {len(values)}.")
        series[column] = values

    rows = []
    for i in range(HOURS_PER_YEAR):
        month, day, hour = hour_of_year_to_key(i)
        row = {"month": month, "day": day, "hour": hour}
        for column in SITE_FIELDS:
            row[column] = float(series[column][i]) if column in series else None
        rows.append(row)
    return {
        "rows": rows,
        "station_info": body.get("station_info") or {},
        "request": site_request(site),
        "pvwatts_version": str(body.get("version", "")),
    }
