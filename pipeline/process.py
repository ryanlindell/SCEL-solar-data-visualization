"""Turn raw cached rows into monthly derived numbers. Pure functions: no network, no
database, no files - rows in, rows out - which makes them easy to test.

How the numbers are built
-------------------------
EIA reports each plant's monthly generation several ways at once (plant total, per fuel,
per fuel *and* prime mover). To avoid counting the same MWh twice we use exactly one level:
the per-fuel rows (fuel_code != 'ALL'), and only those that are real generation (battery
charging, fuel code MWH, is storage, so it is excluded).

    total_mwh          = sum of generation over all remaining fuel rows in the month
    renewable_mwh      = the part of that from renewable fuels (solar + wind by default)
    renewable_share_pct = 100 * renewable_mwh / total_mwh
"""

from collections import defaultdict

# A plant that stops reporting in a December, while other plants keep reporting later
# months, is an annual-only reporter whose newest year isn't published yet.
ANNUAL_LAG_MAX_MONTHS = 30
ANNUAL_MIN_PLANTS = 2


def _month_index(period: str) -> int:
    year, month = period.split("-")
    return int(year) * 12 + int(month) - 1


def find_final_through(plant_last_periods: dict[str, str], latest_period: str) -> str | None:
    """Last month for which every plant's data can be considered complete.

    Small plants only file annual EIA-923 reports, so their newest months arrive up to
    ~2 years after the big plants'. In the months they're missing, totals are understated.
    We detect those plants by their last reported month being a December shortly before the
    region's newest month. Returns that December ('YYYY-12'), or None if no such plants exist
    (meaning all months are complete).
    """
    by_last_period: dict[str, int] = defaultdict(int)
    for last in plant_last_periods.values():
        gap = _month_index(latest_period) - _month_index(last)
        if last.endswith("-12") and 0 < gap <= ANNUAL_LAG_MAX_MONTHS:
            by_last_period[last] += 1
    candidates = [p for p, n in by_last_period.items() if n >= ANNUAL_MIN_PLANTS]
    return max(candidates) if candidates else None


def _quarter_of(period: str) -> str:
    """'2024-05' -> '2024-Q2'."""
    year, month = period.split("-")
    return f"{year}-Q{(int(month) - 1) // 3 + 1}"


def add_rooftop_estimate(
    monthly: list[dict],
    state_rooftop_mwh: dict[str, float],
    share_of_state: float | None,
    measured_quarterly_mwh: dict[str, float] | None = None,
) -> list[dict]:
    """Add an ESTIMATE of rooftop solar, and a renewable share that includes it, to each month.

    Rooftop panels sit behind customers' meters, so the plant survey behind `compute_monthly` never sees
    them. EIA does publish its own estimate of small-scale solar, but only for a whole state, so it has to be
    brought down to the region. Two ways, best first:

    1. MEASURED. If the utility's reported total for the region is known for the month's quarter
       (`measured_quarterly_mwh`, e.g. 'Q1 2024' -> MWh) and EIA's statewide figure exists for all three months
       of it, each month gets EIA's monthly figure scaled so the three months add up to the reported total.
       (The quarter's total is then the utility's number; EIA's statewide pattern only decides how it splits
       across the three months.)
    2. ASSUMED. Otherwise the month gets `share_of_state` x EIA's statewide figure.

    Months EIA has no statewide figure for (it starts in 2014) get None, as do months that neither method
    covers. Four new fields are added and every existing field is left exactly as it was:

        rooftop_solar_mwh_est             the estimate, in MWh
        rooftop_basis                     'measured' or 'assumed' (None when there is no estimate)
        rooftop_share_used                the estimate as a fraction of EIA's statewide figure
        renewable_share_incl_rooftop_pct  100 x (renewable + rooftop) / (total + rooftop)

    The last uses a different denominator from `renewable_share_pct`, on purpose: rooftop output is not part
    of what utility-scale plants generated, so it is added to both the top and the bottom - the share of
    everything *consumed* that came from solar and wind.
    """
    measured_quarterly_mwh = measured_quarterly_mwh or {}
    out = []
    for m in monthly:
        row = dict(m)
        state_mwh = state_rooftop_mwh.get(m["period"])
        quarter = _quarter_of(m["period"])
        year, month = m["period"].split("-")
        quarter_months = [f"{year}-{mm:02d}" for mm in range(3 * ((int(month) - 1) // 3) + 1, 3 * ((int(month) - 1) // 3) + 4)]
        state_quarter = [state_rooftop_mwh.get(p) for p in quarter_months]

        rooftop = basis = None
        if state_mwh is not None:
            if quarter in measured_quarterly_mwh and all(v is not None for v in state_quarter) and sum(state_quarter) > 0:
                rooftop, basis = state_mwh * measured_quarterly_mwh[quarter] / sum(state_quarter), "measured"
            elif share_of_state is not None:
                rooftop, basis = share_of_state * state_mwh, "assumed"

        if rooftop is None:
            row["rooftop_solar_mwh_est"] = row["rooftop_basis"] = row["rooftop_share_used"] = None
            row["renewable_share_incl_rooftop_pct"] = None
        else:
            denominator = m["total_mwh"] + rooftop
            row["rooftop_solar_mwh_est"] = rooftop
            row["rooftop_basis"] = basis
            row["rooftop_share_used"] = rooftop / state_mwh if state_mwh else None
            row["renewable_share_incl_rooftop_pct"] = (
                100 * (m["renewable_mwh"] + rooftop) / denominator if denominator > 0 else None
            )
        out.append(row)
    return out


def compute_monthly(
    rows: list[dict],
    renewable_fuels: tuple[str, ...],
    storage_fuels: tuple[str, ...],
) -> tuple[list[dict], str | None]:
    """Raw cached rows for ONE region -> (monthly result rows, final_through period).

    Each result row: region, period, generation_by_fuel_mwh, total_mwh, renewable_mwh,
    renewable_share_pct, plants_reporting, provisional.
    """
    if not rows:
        return [], None

    region = rows[0]["region"]

    # Last month each plant has any data at all (used to spot annual-only reporters).
    plant_last: dict[str, str] = {}
    for r in rows:
        if r["generation_mwh"] is None:
            continue
        if r["plant_code"] not in plant_last or r["period"] > plant_last[r["plant_code"]]:
            plant_last[r["plant_code"]] = r["period"]
    latest_period = max(plant_last.values())
    final_through = find_final_through(plant_last, latest_period)

    by_fuel: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    plants: dict[str, set[str]] = defaultdict(set)
    for r in rows:
        if r["prime_mover"] != "ALL" or r["fuel_code"] == "ALL" or r["fuel_code"] in storage_fuels:
            continue
        if r["generation_mwh"] is None:
            continue
        by_fuel[r["period"]][r["fuel_code"]] += r["generation_mwh"]
        plants[r["period"]].add(r["plant_code"])

    monthly = []
    for period in sorted(by_fuel):
        fuels = dict(sorted(by_fuel[period].items()))
        total = sum(fuels.values())
        renewable = sum(v for k, v in fuels.items() if k in renewable_fuels)
        monthly.append(
            {
                "region": region,
                "period": period,
                "generation_by_fuel_mwh": fuels,
                "total_mwh": total,
                "renewable_mwh": renewable,
                "renewable_share_pct": 100 * renewable / total if total > 0 else None,
                "plants_reporting": len(plants[period]),
                "provisional": final_through is not None and period > final_through,
            }
        )
    return monthly, final_through
