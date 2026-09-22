"""Typical-day seasonal averages from the sensor's own real calendar dates - not `pipeline/process_site_solar.py`'s
version, which averages a synthetic PVWatts year mapped onto the 2018 calendar purely to pick weekdays. Here the dates
are real (2012), so weekday comes straight from the date itself, and there is no need to borrow another year's calendar.

Coverage is uneven across hour and season (the sensor has gaps, and 2012's log only runs 3 Jan-25 Oct, so winter has no
December and fall has no November - see data/manoa/sensor/README in data/manoa/README.md). `n_days` on every hour
records exactly how many days contributed to it, so a thin average is visible rather than hidden.
"""

import pandas as pd

HOURS_IN_DAY = 24


def seasonal_averages(hourly: pd.DataFrame, seasons: dict[str, tuple[int, ...]], weekdays_only: bool) -> dict:
    """`hourly` has columns date, hour, poa_wm2, dc_w, ac_w (see parse.hourly_means, simulate.simulate_power).

    Returns {season_id: {"days_averaged": n, "hours": [{"hour": 0..23, "poa_wm2": .., "dc_w": .., "ac_w": .., "n_days": ..}]}},
    the same shape pipeline/process_site_solar.seasonal_averages uses (plus n_days), so both estimates export through
    similar code. A field is None for an hour no day in that season/day-set has any surviving data for.
    """
    df = hourly.assign(
        month=[d.month for d in hourly["date"]],
        weekday=[d.weekday() for d in hourly["date"]],  # 2012's real weekday, not a stand-in calendar
    )
    out = {}
    for season_id, months in seasons.items():
        sel = df[df["month"].isin(months)]
        if weekdays_only:
            sel = sel[sel["weekday"] < 5]  # Monday-Friday
        days_averaged = sel["date"].nunique()
        agg = sel.groupby("hour").agg(
            poa_wm2=("poa_wm2", "mean"), dc_w=("dc_w", "mean"), ac_w=("ac_w", "mean"), n_days=("date", "count")
        )
        hours = []
        for h in range(HOURS_IN_DAY):
            if h in agg.index:
                row = agg.loc[h]
                hours.append({"hour": h, "poa_wm2": float(row["poa_wm2"]), "dc_w": float(row["dc_w"]),
                              "ac_w": float(row["ac_w"]), "n_days": int(row["n_days"])})
            else:
                hours.append({"hour": h, "poa_wm2": None, "dc_w": None, "ac_w": None, "n_days": 0})
        out[season_id] = {"days_averaged": int(days_averaged), "hours": hours}
    return out
