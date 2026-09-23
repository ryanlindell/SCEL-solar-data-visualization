"""Settings for the NSRDB 2012 estimate. The array, seasons and power model are sensor_model's, imported rather than
copied, so the NSRDB and sensor lines cannot drift apart in anything but their irradiance source."""

import os

import yaml
from dotenv import load_dotenv

from sensor_model.config import ROOT, ArrayConfig, season_labels, season_months  # noqa: F401 (re-exported)

REGIONS_FILE = ROOT / "config" / "regions.yaml"
OUT_DIR = ROOT / "data" / "manoa" / "nsrdb_pvlib"
RAW_CSV = OUT_DIR / "raw" / "nsrdb_goes_v4_2012_60min.csv"  # the download, kept so reruns need no network
WEB_JSON = ROOT / "web" / "data" / "manoa_solar_nsrdb.json"

YEAR = 2012  # the year the ground sensor logged, so the two real-year lines describe the same weather
DATASET = "nsrdb-GOES-aggregated-v4-0-0"  # GOES-West, the same satellite family as PVWatts' Mānoa weather ("PSM V3 GOES")
DOWNLOAD_URL = f"https://developer.nlr.gov/api/nsrdb/v2/solar/{DATASET}-download.csv"
INTERVAL_MIN = 60
ATTRIBUTES = ("ghi", "dni", "dhi", "air_temperature", "wind_speed", "solar_zenith_angle")
HOURS_IN_2012 = 8784  # a leap year, requested with leap_day=true so every real date (Feb 29 included) is present


class NsrdbError(Exception):
    """NSRDB could not be reached, rejected the request, or returned something that isn't a full year."""


def site_coordinates() -> tuple[float, float]:
    """The same point the PVWatts model is queried at (pvwatts_sites.manoa), so all three lines share a location."""
    with open(REGIONS_FILE, encoding="utf-8") as f:
        site = yaml.safe_load(f)["pvwatts_sites"]["manoa"]
    return float(site["lat"]), float(site["lon"])


def credentials() -> tuple[str, str]:
    load_dotenv(ROOT / ".env")
    key = os.environ.get("NREL_API_KEY", "").strip()
    email = os.environ.get("NSRDB_EMAIL", "").strip()
    if not key or key == "your_key_here":
        raise NsrdbError("NREL_API_KEY is not set in .env (a free key: https://developer.nlr.gov/signup/).")
    if not email or email == "you@example.com":
        raise NsrdbError("NSRDB_EMAIL is not set in .env; NSRDB requires an email with every download.")
    return key, email
