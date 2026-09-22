"""Turn the raw 5-minute sensor readings into hourly means, honest about which hours have too little data to trust.

The sensor (`data/manoa/sensor/sunny_irradiance_2011_2012.csv`) logged only while the sun was expected up, so most
hours of most days have up to 12 five-minute samples; night hours have none at all, same as a real sensor should show.
"""

from pathlib import Path

import pandas as pd

from .config import MIN_HOUR_FRACTION, RAW_SENSOR_CSV

SLOTS_PER_HOUR = 12  # 60 minutes / 5-minute samples


def read_ghi_5min(path: Path = RAW_SENSOR_CSV) -> pd.Series:
    """The raw column (`dev1intsolirr_avg`, W/m2) as a tz-aware Series indexed by its 5-minute timestamp.

    Timestamps carry a fixed -10:00 offset already (Hawaii Standard Time, no daylight saving), and appear to label the
    start of each 5-minute interval (values fall smoothly toward sunset, matching an "interval-beginning" reading, not
    "interval-ending"); this is assumed, not confirmed against the logger's documentation, same caveat as the PVWatts
    model's own hour convention.
    """
    df = pd.read_csv(path, parse_dates=["t_5min"])
    series = df.set_index("t_5min")["dev1intsolirr_avg"].sort_index()
    series.name = "poa_wm2"
    return series


def hourly_means(series: pd.Series, min_fraction: float = MIN_HOUR_FRACTION) -> pd.DataFrame:
    """One row per (date, hour) actually present in the data, with the mean irradiance and how many of the 12
    five-minute slots that hour had. An hour with fewer than `min_fraction` of its slots present is dropped entirely
    (not averaged in as a biased partial hour) rather than filled in some other way.

    Returns columns: date (python date), hour (0-23), poa_wm2 (float, W/m2), n_slots (int, 1-12).
    """
    if series.empty:
        return pd.DataFrame(columns=["date", "hour", "poa_wm2", "n_slots"])
    frame = pd.DataFrame({"poa_wm2": series.values}, index=series.index)
    frame["date"] = frame.index.date
    frame["hour"] = frame.index.hour
    grouped = frame.groupby(["date", "hour"])["poa_wm2"].agg(["mean", "count"]).reset_index()
    grouped = grouped.rename(columns={"mean": "poa_wm2", "count": "n_slots"})
    keep = grouped["n_slots"] >= min_fraction * SLOTS_PER_HOUR
    return grouped[keep].reset_index(drop=True)
