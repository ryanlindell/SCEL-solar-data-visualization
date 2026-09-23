"""Download one year of NSRDB irradiance for one point, as NSRDB's own CSV. Only fetches; parse.py reads it.

The API key and email travel in the URL, so no exception from `requests` (which quotes the URL) is ever allowed out:
every failure is re-raised as NsrdbError with a message of our own.
"""

import time

import requests

from .config import ATTRIBUTES, DOWNLOAD_URL, INTERVAL_MIN, YEAR, NsrdbError

MAX_ATTEMPTS = 4


def request_params(lat: float, lon: float) -> dict[str, str]:
    """Everything sent except the credentials; stored with the download so a later change is visible."""
    return {
        "wkt": f"POINT({lon} {lat})",
        "names": str(YEAR),
        "interval": str(INTERVAL_MIN),
        "utc": "false",  # local standard time (UTC-10, Hawaiʻi has no daylight saving), like every other Mānoa series
        "leap_day": "true",
        "attributes": ",".join(ATTRIBUTES),
    }


def download_csv(lat: float, lon: float, api_key: str, email: str, session: requests.Session | None = None) -> str:
    session = session or requests.Session()
    params = {"api_key": api_key, "email": email, **request_params(lat, lon)}
    last_problem = "unknown error"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = session.get(DOWNLOAD_URL, params=params, timeout=180)
        except requests.RequestException as exc:
            last_problem = f"network error ({type(exc).__name__})"
        else:
            if resp.status_code == 200 and resp.text.startswith("Source,"):
                return resp.text
            if resp.status_code in (401, 403):
                raise NsrdbError(f"NSRDB rejected the request (HTTP {resp.status_code}); check NREL_API_KEY in .env.")
            if resp.status_code == 429 or resp.status_code >= 500:
                last_problem = f"HTTP {resp.status_code}"
            else:
                body = resp.text[:300].replace(api_key, "<key>").replace(email, "<email>")
                raise NsrdbError(f"NSRDB returned HTTP {resp.status_code}: {body}")
        if attempt < MAX_ATTEMPTS:
            time.sleep(2**attempt)
    raise NsrdbError(f"Giving up after {MAX_ATTEMPTS} attempts: {last_problem}")
