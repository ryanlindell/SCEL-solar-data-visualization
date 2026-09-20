"""Fetch a typical year of hourly solar output from NREL's PVWatts API.

PVWatts models a solar system at a given latitude/longitude using a "typical meteorological
year" (TMY) of satellite-derived weather. We ask for a 1 MW reference system, so the result is
"watts produced per megawatt installed" for each of the 8,760 hours of a year. This module only
fetches - it never touches the database.

(NREL was renamed NLR; the API now lives at developer.nlr.gov.)
"""

from datetime import datetime, timedelta

import requests

from .config import HourlyModel
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


def fetch_pvwatts_hourly(
    model: HourlyModel, api_key: str, session: requests.Session | None = None
) -> list[dict]:
    """Return 8,760 rows: month, day, hour (Hawaiʻi standard time), ac_w, poa_wm2."""
    session = session or requests.Session()
    params = [
        ("api_key", api_key),
        ("lat", str(model.lat)),
        ("lon", str(model.lon)),
        ("system_capacity", str(SYSTEM_KW)),
        ("timeframe", "hourly"),
        ("dataset", "nsrdb"),
        *[(k, str(v)) for k, v in model.pv_system.items()],
    ]
    body = get_json(session, PVWATTS_URL, params, NRELError, "NREL_API_KEY")
    if body.get("errors"):
        raise NRELError(f"PVWatts reported errors: {body['errors']}")

    ac = body["outputs"]["ac"]
    poa = body["outputs"]["poa"]
    if len(ac) != HOURS_PER_YEAR or len(poa) != HOURS_PER_YEAR:
        raise NRELError(f"Expected {HOURS_PER_YEAR} hourly values, got {len(ac)} (ac) and {len(poa)} (poa).")

    rows = []
    for i in range(HOURS_PER_YEAR):
        month, day, hour = hour_of_year_to_key(i)
        rows.append({"month": month, "day": day, "hour": hour, "ac_w": float(ac[i]), "poa_wm2": float(poa[i])})
    return rows
