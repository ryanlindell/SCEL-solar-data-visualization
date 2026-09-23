"""NSRDB's CSV -> the hourly frame sensor_model's power model and seasonal averaging expect, plus plausibility checks.

NSRDB's file is two metadata rows (column names, then values: the grid cell's id, its centre, elevation, time zone),
then a header row and one row per interval. With interval=60 each row is stamped at the half hour (hour H, minute 30),
the middle of the hour H:00-H+1:00, which is the same hour-beginning slot the other Mānoa series use.
"""

import io

import pandas as pd

from .config import HOURS_IN_2012, YEAR, NsrdbError

COLUMNS = {"GHI": "poa_wm2", "DNI": "dn_wm2", "DHI": "df_wm2", "Temperature": "tamb_c", "Wind Speed": "wspd_ms",
           "Solar Zenith Angle": "zenith_deg"}
MAX_GHI_WM2 = 1500.0
DARK_ZENITH_DEG = 95.0  # comfortably past sunset, allowing for the half-hour stamp and refraction
ANNUAL_GHI_RANGE_KWH_M2 = (1400.0, 2600.0)  # same bounds pipeline/process_site_solar uses for flat Hawaiʻi sunshine


def read_metadata(text: str) -> dict:
    lines = text.splitlines()
    names, values = lines[0].split(","), lines[1].split(",")
    meta = dict(zip(names, values))
    return {
        "source": meta.get("Source"),
        "location_id": meta.get("Location ID"),
        "cell_lat": float(meta["Latitude"]),
        "cell_lon": float(meta["Longitude"]),
        "elevation_m": float(meta["Elevation"]),
        "time_zone": int(float(meta["Time Zone"])),
        "local_time_zone": int(float(meta["Local Time Zone"])),
    }


def hourly_frame(text: str) -> pd.DataFrame:
    """One row per hour of 2012: date, hour, minute, poa_wm2 (GHI; the array is flat), dn/df, temperature, wind, zenith."""
    raw = pd.read_csv(io.StringIO(text), skiprows=2)
    missing = [c for c in ("Year", "Month", "Day", "Hour", "Minute", *COLUMNS) if c not in raw.columns]
    if missing:
        raise NsrdbError(f"NSRDB's file is missing columns: {', '.join(missing)}")
    frame = raw.rename(columns=COLUMNS)
    frame["date"] = pd.to_datetime(frame[["Year", "Month", "Day"]]).dt.date
    frame = frame.rename(columns={"Hour": "hour", "Minute": "minute"})
    return frame[["date", "hour", "minute", *COLUMNS.values()]]


def check_plausible(frame: pd.DataFrame, meta: dict) -> list[str]:
    """Reasons this year cannot be right (empty list = passed)."""
    problems = []
    if meta["local_time_zone"] != -10:
        problems.append(f"expected Hawaiʻi standard time (UTC-10), file says {meta['local_time_zone']}; was utc=false sent?")
    if len(frame) != HOURS_IN_2012:
        problems.append(f"expected {HOURS_IN_2012} hours for {YEAR}, got {len(frame)}")
    if frame.duplicated(["date", "hour"]).any():
        problems.append("some hours appear twice")
    if frame["date"].map(lambda d: d.year).ne(YEAR).any():
        problems.append(f"rows outside {YEAR}")
    if set(frame["minute"]) != {30}:
        problems.append(f"expected every row stamped at minute 30, got {sorted(set(frame['minute']))}")
    ghi = frame["poa_wm2"]
    if (ghi < 0).any():
        problems.append("negative GHI")
    if ghi.max() > MAX_GHI_WM2:
        problems.append(f"GHI reaches {ghi.max():.0f} W/m2, above {MAX_GHI_WM2:.0f}")
    dark = frame[frame["zenith_deg"] > DARK_ZENITH_DEG]["poa_wm2"]
    if (dark > 1.0).any():
        problems.append(f"{int((dark > 1.0).sum())} hours with the sun well below the horizon still show sunlight: time shifted?")
    annual = ghi.sum() / 1000
    if not ANNUAL_GHI_RANGE_KWH_M2[0] <= annual <= ANNUAL_GHI_RANGE_KWH_M2[1]:
        problems.append(f"annual GHI {annual:.0f} kWh/m2 is outside {ANNUAL_GHI_RANGE_KWH_M2} for a flat surface in Hawaiʻi")
    return problems
