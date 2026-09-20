"""Talk to the EIA Open Data API (v2). This module only fetches - it never touches the
database and never computes anything.
"""

import requests

from .config import Region
from .http import MAX_ATTEMPTS, get_json  # noqa: F401  (MAX_ATTEMPTS re-exported for callers/tests)

BASE_URL = "https://api.eia.gov/v2"
PAGE_SIZE = 5000  # the API's maximum rows per request

FACILITY_FUEL_ROUTE = "electricity/facility-fuel"
GENERATOR_CAPACITY_ROUTE = "electricity/operating-generator-capacity"
STATE_OPERATIONAL_ROUTE = "electricity/electric-power-operational-data"


class EIAError(Exception):
    """The API could not be reached, or it returned an error."""


def _get_json(session: requests.Session, url: str, params: list[tuple[str, str]]) -> dict:
    return get_json(session, url, params, EIAError, "EIA_API_KEY")


def get_all_rows(
    route: str,
    params: list[tuple[str, str]],
    api_key: str,
    session: requests.Session | None = None,
) -> list[dict]:
    """Fetch every row for a query, following the API's pagination (5000 rows per page)."""
    session = session or requests.Session()
    url = f"{BASE_URL}/{route}/data"
    rows: list[dict] = []
    offset = 0
    while True:
        page_params = [("api_key", api_key), *params, ("offset", str(offset)), ("length", str(PAGE_SIZE))]
        body = _get_json(session, url, page_params)
        response = body["response"]
        rows.extend(response["data"])
        total = int(response["total"])
        offset += PAGE_SIZE
        if offset >= total:
            break
    if len(rows) != total:
        raise EIAError(f"Expected {total} rows from {route} but received {len(rows)}.")
    return rows


def get_latest_period(route: str, api_key: str, session: requests.Session | None = None) -> str:
    """The most recent month (YYYY-MM) that a route has data for."""
    session = session or requests.Session()
    body = _get_json(session, f"{BASE_URL}/{route}/", [("api_key", api_key)])
    return body["response"]["endPeriod"]


def fetch_small_scale_solar(state: str, api_key: str, session: requests.Session | None = None) -> list[dict]:
    """EIA's monthly *estimate* of rooftop / small-scale solar generation for a whole state.

    Rooftop panels sit behind customers' meters, so no plant reports them; EIA estimates them from
    net-metering data. The estimate is per state (not per island) and covers all sectors.
    Rows: period, state, generation_mwh.
    """
    params = [
        ("frequency", "monthly"),
        ("data[0]", "generation"),
        ("facets[location][]", state),
        ("facets[fueltypeid][]", "DPV"),  # "estimated small scale solar photovoltaic"
        ("facets[sectorid][]", "99"),  # all sectors
        ("sort[0][column]", "period"),
        ("sort[0][direction]", "asc"),
    ]
    rows = []
    for r in get_all_rows(STATE_OPERATIONAL_ROUTE, params, api_key, session):
        if r.get("generation-units") != "thousand megawatthours":
            raise EIAError(f"Unexpected units {r.get('generation-units')!r}; expected thousand megawatthours.")
        if r["generation"] is None:
            continue
        rows.append({"period": r["period"], "state": state, "generation_mwh": float(r["generation"]) * 1000})
    if not rows:
        raise EIAError(f"EIA returned no small-scale solar estimate for {state}.")
    return rows


def fetch_facility_fuel(region: Region, api_key: str, session: requests.Session | None = None) -> list[dict]:
    """Monthly generation by plant and fuel for every plant in the region, all history.

    Only rows with primeMover == "ALL" are requested. EIA also publishes the same
    megawatt-hours split by prime mover (steam turbine, combustion turbine, ...); asking
    for that as well would make every MWh appear several times.

    Returned rows use snake_case keys but otherwise carry EIA's values unchanged.
    """
    params = [
        ("frequency", "monthly"),
        ("data[0]", "generation"),
        ("facets[state][]", region.state),
        ("facets[primeMover][]", "ALL"),
        ("sort[0][column]", "period"),
        ("sort[0][direction]", "asc"),
    ]
    params += [("facets[plantCode][]", code) for code in region.plant_codes]

    raw_rows = get_all_rows(FACILITY_FUEL_ROUTE, params, api_key, session)

    rows = []
    for r in raw_rows:
        if r.get("generation-units") != "megawatthours":
            raise EIAError(f"Unexpected units {r.get('generation-units')!r}; the pipeline assumes MWh.")
        gen = r["generation"]
        rows.append(
            {
                "period": r["period"],
                "plant_code": str(r["plantCode"]),
                "plant_name": r["plantName"],
                "fuel_code": r["fuel2002"],
                "prime_mover": r["primeMover"],
                "state": r["state"],
                "generation_mwh": None if gen is None else float(gen),
            }
        )
    return rows
