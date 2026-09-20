"""Build the modeled duck curve. Pure functions: rows in, numbers out - no network, files or database.

The idea
--------
Nobody publishes hourly Oʻahu load and solar, so we *model the shapes* and *anchor the sizes to real
data*:

  * Shape of customer demand: NREL's simulated homes + businesses (ResStock / ComStock), hour by hour.
  * Shape of rooftop solar:   ResStock's simulated rooftop panels on homes.
  * Shape of utility solar:   NREL PVWatts, a typical year of sunshine for a reference solar farm.
  * Sizes: for the reference year, each month is scaled to match real EIA numbers:
      - utility solar and wind  = what Oʻahu's plants actually generated (Tier 1 data)
      - rooftop solar           = EIA's estimate for Hawaiʻi, times Oʻahu's (measured) share
      - grid load               = Oʻahu's total actual generation (Tier 1 data)
      - customer demand         = grid load + rooftop solar

Definitions (all in megawatts, averaged over each hour)
    customer_demand = all electricity used by customers (rooftop solar output is added back in)
    grid_load       = customer_demand - rooftop_solar   (what the utility "sees")
    net_load        = grid_load - utility_solar - wind  (what fossil/other plants must supply)

The "duck" is the shape of net_load across the day.
"""

import calendar
from collections import defaultdict
from datetime import date

SOLAR_FUEL = "SUN"
WIND_FUEL = "WND"
DATA_YEAR = 2018  # calendar used for day-of-week (matches the load data)
HOURS_IN_DAY = 24


def pick_reference_year(monthly: list[dict], requested: int | None = None) -> int:
    """The year whose real monthly totals calibrate the model: `requested`, or else the latest year
    for which all 12 months are final (not provisional) in the Tier 1 data."""
    months_by_year: dict[int, set[int]] = defaultdict(set)
    for m in monthly:
        if not m["provisional"]:
            year, month = m["period"].split("-")
            months_by_year[int(year)].add(int(month))
    complete = sorted(y for y, months in months_by_year.items() if len(months) == 12)
    if requested is not None:
        if requested not in complete:
            raise ValueError(f"{requested} is not a fully final year in the EIA data (complete years: {complete}).")
        return requested
    if not complete:
        raise ValueError("The EIA data has no fully final calendar year to calibrate against.")
    return complete[-1]


def build_hourly_model(
    load_rows: list[dict], pv_rows: list[dict], share_of_state: float
) -> dict[tuple[int, int, int], dict[str, float]]:
    """Combine cached rows into one record per hour: uncalibrated demand and rooftop solar (kWh)
    and the reference solar farm's output (W per MW installed).

    Residential files cover the whole state, so their *demand* is weighted by `share_of_state`
    (the fraction of the state's homes that are in this region). Rooftop solar is left unweighted:
    only its hour-by-hour shape is used, and its size is set from EIA's estimate later.
    """
    hours: dict[tuple[int, int, int], dict[str, float]] = {}
    for r in pv_rows:
        hours[(r["month"], r["day"], r["hour"])] = {"gross_kwh": 0.0, "rooftop_kwh": 0.0, "solar_w": r["ac_w"]}

    for r in load_rows:
        key = (r["month"], r["day"], r["hour"])
        if key not in hours:
            raise ValueError(f"Load data has hour {key} that the solar data lacks; are both full years?")
        weight = share_of_state if r["sector"] == "residential" else 1.0
        hours[key]["gross_kwh"] += weight * r["total_kwh"]
        hours[key]["rooftop_kwh"] += r["pv_kwh"]

    empty = [k for k, v in hours.items() if v["gross_kwh"] <= 0]
    if empty:
        raise ValueError(f"No load data for {len(empty)} hours (first: {empty[0]}).")
    return hours


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def compute_duck_curve(
    load_rows: list[dict],
    pv_rows: list[dict],
    monthly: list[dict],
    state_rooftop_mwh: dict[str, float],
    share_of_state: float,
    rooftop_share_of_state: float,
    seasons: dict[str, tuple[int, ...]],
    reference_year: int | None = None,
) -> dict:
    """Calibrated typical-weekday curves for each season.

    `monthly` is Tier 1's monthly output for the region (process.compute_monthly).
    `state_rooftop_mwh` maps 'YYYY-MM' to EIA's estimate of the whole state's rooftop solar (MWh);
    `rooftop_share_of_state` is the region's part of it, and `share_of_state` the region's part of
    the state's homes (which sets how much residential demand goes into the demand shape).
    Returns {"reference_year", "calibration": {...}, "seasons": {season_id: {"hours": [24 dicts],
    "stats": {...}, "weekdays_averaged": n}}}.
    """
    year = pick_reference_year(monthly, reference_year)
    actual = {m["period"]: m for m in monthly}
    hours = build_hourly_model(load_rows, pv_rows, share_of_state)

    by_month: dict[int, list[tuple[int, int, int]]] = defaultdict(list)
    for key in hours:
        by_month[key[0]].append(key)

    # Step 1: one calibration per month, so the modeled month adds up to the real month.
    calibration = []
    series: dict[tuple[int, int, int], dict[str, float]] = {}
    for month in range(1, 13):
        keys = by_month[month]
        act = actual[f"{year}-{month:02d}"]
        actual_total_mwh = act["total_mwh"]
        actual_solar_mwh = act["generation_by_fuel_mwh"].get(SOLAR_FUEL, 0.0)
        actual_wind_mwh = act["generation_by_fuel_mwh"].get(WIND_FUEL, 0.0)

        period = f"{year}-{month:02d}"
        if period not in state_rooftop_mwh:
            raise ValueError(f"No EIA rooftop-solar estimate for {period}.")
        actual_rooftop_mwh = rooftop_share_of_state * state_rooftop_mwh[period]

        modeled_demand_kwh = sum(hours[k]["gross_kwh"] for k in keys)
        modeled_rooftop_kwh = sum(hours[k]["rooftop_kwh"] for k in keys)
        reference_farm_mwh = sum(hours[k]["solar_w"] for k in keys) / 1e6  # MWh from 1 MW installed
        if modeled_demand_kwh <= 0 or reference_farm_mwh <= 0 or (modeled_rooftop_kwh <= 0 < actual_rooftop_mwh):
            raise ValueError(f"Month {month} has no modeled load, rooftop output or sunshine to calibrate.")

        # Each scale turns "modeled kWh" into "actual kWh" for that month.
        demand_scale = (actual_total_mwh + actual_rooftop_mwh) * 1000 / modeled_demand_kwh
        rooftop_scale = actual_rooftop_mwh * 1000 / modeled_rooftop_kwh if modeled_rooftop_kwh > 0 else 0.0
        solar_mw_installed = actual_solar_mwh / reference_farm_mwh  # MW of the reference farm that matches
        wind_mw = actual_wind_mwh / (len(keys))  # flat within the month: hours == len(keys)

        for k in keys:
            h = hours[k]
            customer_demand = demand_scale * h["gross_kwh"] / 1000
            rooftop = rooftop_scale * h["rooftop_kwh"] / 1000
            grid = customer_demand - rooftop
            utility_solar = solar_mw_installed * h["solar_w"] / 1e6
            series[k] = {
                "customer_demand_mw": customer_demand,
                "rooftop_solar_mw": rooftop,
                "grid_load_mw": grid,
                "utility_solar_mw": utility_solar,
                "wind_mw": wind_mw,
                "net_load_mw": grid - utility_solar - wind_mw,
            }
        calibration.append(
            {
                "month": month,
                "demand_scale": demand_scale,
                "rooftop_scale": rooftop_scale,
                "reference_farm_mw_equivalent": solar_mw_installed,
                "actual_total_mwh": actual_total_mwh,
                "actual_rooftop_mwh": actual_rooftop_mwh,
                "actual_solar_mwh": actual_solar_mwh,
                "actual_wind_mwh": actual_wind_mwh,
            }
        )

    # Step 2: average weekday of each season, hour by hour.
    fields = list(next(iter(series.values())))
    season_out = {}
    for season_id, months in seasons.items():
        sums = {f: [[] for _ in range(HOURS_IN_DAY)] for f in fields}
        days = set()
        for month in months:
            for day in range(1, calendar.monthrange(DATA_YEAR, month)[1] + 1):
                if date(DATA_YEAR, month, day).weekday() >= 5:  # Saturday/Sunday
                    continue
                days.add((month, day))
                for hour in range(HOURS_IN_DAY):
                    values = series[(month, day, hour)]
                    for f in fields:
                        sums[f][hour].append(values[f])
        hours_out = [{"hour": h, **{f: _mean(sums[f][h]) for f in fields}} for h in range(HOURS_IN_DAY)]
        season_out[season_id] = {
            "hours": hours_out,
            "stats": net_load_stats([h["net_load_mw"] for h in hours_out]),
            "weekdays_averaged": len(days),
        }

    return {
        "reference_year": year,
        "calibration": {
            "months": calibration,
            "rooftop_solar_gwh_per_year": sum(m["actual_rooftop_mwh"] for m in calibration) / 1000,
            "utility_solar_gwh_per_year": sum(m["actual_solar_mwh"] for m in calibration) / 1000,
        },
        "seasons": season_out,
    }


MIDDAY_HOURS = range(9, 16)  # 9 AM - 3:59 PM: where the "belly" of the duck sits


def net_load_stats(net: list[float]) -> dict:
    """Headline numbers for a 24-hour net-load curve (index = hour beginning).

    The duck is a *daytime* dip followed by an evening climb, so the low point is searched only in
    the middle of the day (the overnight trough is a different, unrelated minimum), and the peak and
    ramp only after it.
    """
    low_hour = min(MIDDAY_HOURS, key=net.__getitem__)
    peak_hour = max(range(low_hour, len(net)), key=net.__getitem__)
    ramp_start = max(range(low_hour, len(net) - 3), key=lambda h: net[h + 3] - net[h])
    return {
        "midday_low_mw": net[low_hour],
        "midday_low_hour": low_hour,
        "evening_peak_mw": net[peak_hour],
        "evening_peak_hour": peak_hour,
        "evening_climb_mw": net[peak_hour] - net[low_hour],
        "steepest_3h_ramp_mw": net[ramp_start + 3] - net[ramp_start],
        "steepest_3h_ramp_start_hour": ramp_start,
    }
