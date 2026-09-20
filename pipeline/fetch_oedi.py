"""Fetch modeled hourly building electricity use from NREL's End-Use Load Profiles dataset,
hosted in a public S3 bucket (Open Energy Data Initiative).

The dataset holds simulated homes (ResStock) and businesses (ComStock). Each CSV covers one
building type and has one row per 15 minutes. Files are 10-25 MB, so they are *streamed* line by
line and only the columns we need are kept, summed into hours. This module only fetches - it never
touches the database.

Timestamps in these files are 15-minute-*ending* and in Eastern Standard Time; we convert them to
the hour-beginning in Hawaiʻi time so they line up with the solar data.
"""

import csv
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Iterable

import requests

from .config import HourlyData, HourlyModel
from .http import MAX_ATTEMPTS

BUCKET_URL = "https://oedi-data-lake.s3.amazonaws.com"
ROOT_PREFIX = "nrel-pds-building-stock/end-use-load-profiles-for-us-building-stock/"
S3_NS = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
DATA_YEAR = 2018  # the (actual-weather) year these datasets model
HOURS_PER_YEAR = 8760

# Column names differ between the two datasets (ResStock has a double dot before "kwh").
RESIDENTIAL_TOTAL_COL = "out.electricity.total.energy_consumption..kwh"
RESIDENTIAL_PV_COL = "out.electricity.pv.energy_consumption..kwh"  # rooftop PV output, as a negative number
COMMERCIAL_TOTAL_COL = "out.electricity.total.energy_consumption.kwh"


class OEDIError(Exception):
    """The building-load data could not be downloaded or understood."""


def to_local_hour_key(timestamp: str, timestamp_utc_offset: int, local_utc_offset: int) -> tuple[int, int, int]:
    """'2018-01-01 00:15:00' (15-min ending, EST) -> (month, day, hour) of the hour it belongs to
    in local time, e.g. (12, 31, 19) for Hawaiʻi: 00:15 EST is 19:15 HST the evening before."""
    end = datetime(
        int(timestamp[0:4]), int(timestamp[5:7]), int(timestamp[8:10]), int(timestamp[11:13]), int(timestamp[14:16])
    )
    start_local = end - timedelta(minutes=15) + timedelta(hours=local_utc_offset - timestamp_utc_offset)
    # The first hours of the file land on Dec 31 of the previous year. A year of data is cyclic, so
    # file them under Dec 31 of the data year (the same calendar date in a non-leap year).
    if start_local.year != DATA_YEAR:
        start_local = start_local.replace(year=DATA_YEAR)
    return start_local.month, start_local.day, start_local.hour


def sum_csv_by_hour(
    lines: Iterable[str],
    columns: dict[str, str],
    timestamp_utc_offset: int,
    local_utc_offset: int,
) -> list[dict]:
    """Sum the named columns of a 15-minute CSV into hours of local time.

    `columns` maps an output name to a CSV column name, e.g. {"total_kwh": "out.electricity...."}.
    Returns rows: month, day, hour and one number per output name (kWh in the hour = average kW).
    """
    reader = csv.reader(lines)
    header = next(reader)
    try:
        ts_idx = header.index("timestamp")
        col_idx = {out: header.index(col) for out, col in columns.items()}
    except ValueError as exc:
        raise OEDIError(f"Unexpected file layout: {exc}") from None

    sums: dict[tuple[int, int, int], dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for row in reader:
        if not row:
            continue
        key = to_local_hour_key(row[ts_idx], timestamp_utc_offset, local_utc_offset)
        for out, idx in col_idx.items():
            sums[key][out] += float(row[idx] or 0)

    return [{"month": m, "day": d, "hour": h, **dict(vals)} for (m, d, h), vals in sorted(sums.items())]


def list_csv_urls(prefix: str, session: requests.Session) -> list[str]:
    """URLs of every .csv file directly under an S3 prefix of the public bucket."""
    urls: list[str] = []
    token = None
    while True:
        params = {"list-type": "2", "prefix": prefix}
        if token:
            params["continuation-token"] = token
        try:
            resp = session.get(BUCKET_URL + "/", params=params, timeout=60)
        except requests.RequestException as exc:
            raise OEDIError(f"Could not list the data bucket ({type(exc).__name__}).") from None
        if resp.status_code != 200:
            raise OEDIError(f"Listing the data bucket failed (HTTP {resp.status_code}).")
        root = ET.fromstring(resp.content)
        for key in root.findall("s3:Contents/s3:Key", S3_NS):
            if key.text and key.text.endswith(".csv"):
                urls.append(f"{BUCKET_URL}/{key.text}")
        if root.findtext("s3:IsTruncated", namespaces=S3_NS) != "true":
            return sorted(urls)
        token = root.findtext("s3:NextContinuationToken", namespaces=S3_NS)


def building_type_from_url(url: str) -> str:
    """'.../up00-hi-single-family_detached.csv' -> 'single-family_detached'."""
    stem = url.rsplit("/", 1)[-1].removesuffix(".csv")
    return stem.split("-", 2)[2]


def list_building_files(
    sector: str, model: HourlyModel, hd: HourlyData, session: requests.Session
) -> list[tuple[str, str]]:
    """(building_type, url) for every file of a sector ('residential' or 'commercial')."""
    if sector == "residential":
        prefix = (
            f"{ROOT_PREFIX}{hd.residential_release}/timeseries_aggregates/by_state/"
            f"upgrade=0/state={model.state}/"
        )
    elif sector == "commercial":
        prefix = (
            f"{ROOT_PREFIX}{hd.commercial_release}/timeseries_aggregates/by_county/"
            f"upgrade=0/county={model.commercial_county_fips}/"
        )
    else:
        raise ValueError(sector)
    urls = list_csv_urls(prefix, session)
    if not urls:
        raise OEDIError(f"No {sector} load files found under {prefix}")
    return [(building_type_from_url(u), u) for u in urls]


def download_building_load(sector: str, url: str, hd: HourlyData, session: requests.Session) -> list[dict]:
    """Hourly rows (month, day, hour, total_kwh, pv_kwh) for one building type.

    `pv_kwh` is rooftop solar output as a *positive* number (always 0 for commercial buildings,
    which the dataset does not model with rooftop PV). A dropped download is retried.
    """
    if sector == "residential":
        columns = {"total_kwh": RESIDENTIAL_TOTAL_COL, "pv_raw": RESIDENTIAL_PV_COL}
    else:
        columns = {"total_kwh": COMMERCIAL_TOTAL_COL}
    name = url.rsplit("/", 1)[-1]

    last_problem = "unknown error"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with session.get(url, stream=True, timeout=(30, 120)) as resp:
                if resp.status_code == 200:
                    lines = (raw.decode("utf-8") for raw in resp.iter_lines())
                    rows = sum_csv_by_hour(lines, columns, hd.load_timestamp_utc_offset, hd.local_utc_offset)
                    if len(rows) != HOURS_PER_YEAR:
                        raise OEDIError(f"{name}: expected {HOURS_PER_YEAR} hours, found {len(rows)}.")
                    for r in rows:
                        r["pv_kwh"] = max(0.0, -r.pop("pv_raw")) if "pv_raw" in r else 0.0
                    return rows
                if resp.status_code == 429 or resp.status_code >= 500:
                    last_problem = f"HTTP {resp.status_code}"
                else:
                    raise OEDIError(f"HTTP {resp.status_code} downloading {name}")
        except requests.RequestException as exc:
            last_problem = f"network error ({type(exc).__name__})"
        if attempt < MAX_ATTEMPTS:
            time.sleep(2**attempt)
    raise OEDIError(f"Giving up on {name} after {MAX_ATTEMPTS} attempts: {last_problem}")
