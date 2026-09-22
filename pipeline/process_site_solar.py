"""Checks and seasonal averages for a single-site PVWatts series (e.g. UH Mānoa). Pure functions: rows in, numbers out -
no network, files or database - and separate from the Oʻahu-wide duck curve in process_hourly.py.

The seasonal average follows the duck curve's definition exactly: for each season, the hourly mean over every weekday
(Monday to Friday) of the 2018 calendar in that season's months, which gives 64, 66, 66 and 65 days for the standard
seasons. That calendar and the hour count are imported from process_hourly rather than copied, so the two cannot drift
apart. (For irradiance the weekday filter has no physical meaning, since the sun does not know it is Tuesday; it is kept
because it is the comparison unit chosen for this project, and an all-days average is computed alongside it.)
"""

import calendar
from datetime import date

from .process_hourly import DATA_YEAR, HOURS_IN_DAY

HOURS_PER_YEAR = 8760
IRRADIANCE_FIELDS = ("poa_wm2", "dn_wm2", "df_wm2")
SERIES_FIELDS = ("poa_wm2", "dn_wm2", "df_wm2", "dc_w", "ac_w", "tamb_c", "tcell_c", "wspd_ms", "albedo")

# Sanity limits. Sunlight at the ground never reaches the ~1,361 W/m2 above the atmosphere; diffuse light on a horizontal
# surface stays well under 900 W/m2. Hawaiʻi's sun is never up in the hours listed (sunrise is never before ~5:50 AM and
# sunset never after ~7:20 PM in local standard time), so a series shifted by a few hours shows light there.
MAX_IRRADIANCE_WM2 = {"poa_wm2": 1500.0, "dn_wm2": 1400.0, "df_wm2": 900.0}
DARK_HOURS = (0, 1, 2, 3, 4, 20, 21, 22, 23)
DARK_TOLERANCE_WM2 = 1.0
# PVWatts' plane-of-array value at tilt 0 can sit a fraction of a W/m2 below its diffuse value (the largest gap seen in
# a real Mānoa response was 0.17), so consistency checks between them allow a little slack.
CONSISTENCY_TOLERANCE_WM2 = 1.0
ANNUAL_POA_RANGE_KWH_M2 = (1400.0, 2600.0)  # for a flat or gently tilted surface in Hawaiʻi (a real run gave about 2,010)


def _year_keys() -> set[tuple[int, int, int]]:
    return {
        (m, d, h)
        for m in range(1, 13)
        for d in range(1, calendar.monthrange(DATA_YEAR, m)[1] + 1)
        for h in range(HOURS_IN_DAY)
    }


def check_plausible(rows: list[dict], tilt: float = 0.0) -> list[str]:
    """Reasons this series cannot be right (an empty list means it passed).

    Checks a full 8,760-hour year, no negative or impossibly large irradiance, darkness at night, power that never
    exceeds what DC allows, sensible temperatures and yearly sunshine, and (only for a flat surface, where plane-of-array
    irradiance is global horizontal) that plane-of-array lies between the diffuse and diffuse-plus-beam values.
    """
    problems: list[str] = []
    keys = [(r["month"], r["day"], r["hour"]) for r in rows]
    if len(rows) != HOURS_PER_YEAR:
        problems.append(f"expected {HOURS_PER_YEAR} hours, got {len(rows)}")
    if set(keys) != _year_keys() or len(set(keys)) != len(keys):
        problems.append("the hours are not one complete non-leap year, each month/day/hour exactly once")

    def values(field):
        return [r[field] for r in rows if r.get(field) is not None]

    for field in IRRADIANCE_FIELDS:
        v = values(field)
        if not v:
            continue
        if min(v) < 0:
            problems.append(f"{field} has negative values (lowest {min(v):.2f})")
        if max(v) > MAX_IRRADIANCE_WM2[field]:
            problems.append(f"{field} reaches {max(v):.0f} W/m2, above the plausible limit {MAX_IRRADIANCE_WM2[field]:.0f}")
        dark = [r[field] for r in rows if r["hour"] in DARK_HOURS and r.get(field) is not None]
        if dark and max(dark) > DARK_TOLERANCE_WM2:
            problems.append(f"{field} shows sunlight in the small hours or late evening (up to {max(dark):.0f} W/m2): time shifted?")

    poa = values("poa_wm2")
    if poa and tilt <= 30:
        annual = sum(poa) / 1000
        if not ANNUAL_POA_RANGE_KWH_M2[0] <= annual <= ANNUAL_POA_RANGE_KWH_M2[1]:
            problems.append(f"annual plane-of-array sunshine {annual:.0f} kWh/m2 is outside {ANNUAL_POA_RANGE_KWH_M2} for Hawaiʻi")
        if max(poa) < 700:
            problems.append(f"the brightest hour is only {max(poa):.0f} W/m2; a year in Hawaiʻi should have clear middays above 700")

    if tilt == 0:
        both = [r for r in rows if r.get("poa_wm2") is not None and r.get("df_wm2") is not None]
        below = sum(1 for r in both if r["poa_wm2"] < r["df_wm2"] - CONSISTENCY_TOLERANCE_WM2)
        if below:
            problems.append(f"{below} hours where flat-surface irradiance is below the diffuse irradiance")
        beam = [r for r in both if r.get("dn_wm2") is not None]
        above = sum(1 for r in beam if r["poa_wm2"] > r["dn_wm2"] + r["df_wm2"] + CONSISTENCY_TOLERANCE_WM2)
        if above:
            problems.append(f"{above} hours where flat-surface irradiance exceeds beam plus diffuse")

    for field in ("dc_w", "ac_w", "wspd_ms"):
        v = values(field)
        if v and min(v) < 0:
            problems.append(f"{field} has negative values")
    both = [r for r in rows if r.get("dc_w") is not None and r.get("ac_w") is not None]
    over = sum(1 for r in both if r["ac_w"] > r["dc_w"] * 1.0001 + 1e-6)
    if over:
        problems.append(f"{over} hours where AC power exceeds DC power")
    temps = values("tamb_c")
    if temps and (min(temps) < -30 or max(temps) > 55):
        problems.append(f"ambient temperature range {min(temps)} to {max(temps)} deg C is implausible")
    return problems


def seasonal_averages(
    rows: list[dict],
    seasons: dict[str, tuple[int, ...]],
    fields: tuple[str, ...] = SERIES_FIELDS,
    weekdays_only: bool = True,
) -> dict:
    """Typical-day average per season, hour by hour.

    `seasons` maps a season id to its calendar months, as in process_hourly.compute_duck_curve. With `weekdays_only`
    (the duck curve's definition) Saturdays and Sundays of the 2018 calendar are skipped. Returns
    {season_id: {"days_averaged": n, "hours": [{"hour": 0..23, <field>: mean or None}, ...]}}; a field that is missing
    (None) for every day gives None.
    """
    by_key = {(r["month"], r["day"], r["hour"]): r for r in rows}
    out = {}
    for season_id, months in seasons.items():
        samples = {f: [[] for _ in range(HOURS_IN_DAY)] for f in fields}
        days = 0
        for month in months:
            for day in range(1, calendar.monthrange(DATA_YEAR, month)[1] + 1):
                if weekdays_only and date(DATA_YEAR, month, day).weekday() >= 5:  # Saturday / Sunday
                    continue
                days += 1
                for hour in range(HOURS_IN_DAY):
                    row = by_key[(month, day, hour)]
                    for f in fields:
                        if row.get(f) is not None:
                            samples[f][hour].append(row[f])
        out[season_id] = {
            "days_averaged": days,
            "hours": [
                {"hour": h, **{f: (sum(samples[f][h]) / len(samples[f][h]) if samples[f][h] else None) for f in fields}}
                for h in range(HOURS_IN_DAY)
            ],
        }
    return out


def annual_summary(rows: list[dict]) -> dict:
    """Headline numbers for the year: how much sun, how bright at best, and what the power columns add up to."""

    def col(field):
        return [r[field] for r in rows if r.get(field) is not None]

    poa, dc, ac = col("poa_wm2"), col("dc_w"), col("ac_w")
    out = {
        "hours": len(rows),
        "hours_with_sun": sum(1 for v in poa if v > 0),
        "annual_poa_kwh_per_m2": sum(poa) / 1000,
        "peak_poa_wm2": max(poa),
    }
    for name, field in (("peak_dn_wm2", "dn_wm2"), ("peak_df_wm2", "df_wm2")):
        v = col(field)
        out[name] = max(v) if v else None
    if dc:
        out["annual_dc_kwh_per_kw_dc"] = sum(dc) / 1000 / 1000  # the array is 1,000 kW (DC)
        out["dc_capacity_factor_pct"] = 100 * sum(dc) / (1_000_000 * len(dc))
    if ac:
        out["annual_ac_kwh_per_kw_dc"] = sum(ac) / 1000 / 1000
        top = max(ac)
        out["ac_max_w"] = top
        out["ac_hours_at_max"] = sum(1 for v in ac if v >= top * 0.9999)  # the inverter's ceiling, if it clips
    if dc and ac and sum(dc) > 0:
        out["ac_over_dc_energy"] = sum(ac) / sum(dc)
    temps = col("tamb_c")
    if temps:
        out["mean_ambient_c"] = sum(temps) / len(temps)
    return out
