"""Turn Hawaiian Electric's reported curtailment numbers into quarterly and annual series.
Pure functions: rows in, rows out - no network, files or database.

Terms (all energy in MWh; they cover wind and utility-scale solar together, the resources the
utility can turn down - Hawaiian Electric calls them "curtailable renewable resources"):

    delivered   energy the grid actually took                     ("MWh taken from curtailable ...")
    curtailed   energy the utility told the plants NOT to produce ("MWh curtailed from curtailable ...")
    potential   delivered + curtailed: what the plants could have supplied
    curtailment rate = curtailed / potential

Rooftop solar ("distributed" generation) is reported too, but it is not curtailable, so it is not part
of potential; it is kept only as context and for cross-checks.
"""

from collections import defaultdict

REASONS = ("oversupply_mwh", "system_constraint_mwh", "facility_requested_mwh")


def _by_period(rows: list[dict], dataset: str) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = defaultdict(dict)
    for r in rows:
        if r["dataset"] == dataset:
            out[r["period"]][r["series"]] = r["value"]
    return out


def _rate(curtailed: float, potential: float) -> float | None:
    return 100 * curtailed / potential if potential > 0 else None


def build_quarters(rows: list[dict]) -> list[dict]:
    """Cached rows for ONE region -> one dict per quarter, oldest first.

    Quarters lacking delivered or curtailed energy are skipped. The by-reason numbers only exist
    from July 2015, so earlier quarters have None for them.
    """
    totals = _by_period(rows, "quarterly_totals")
    reasons = _by_period(rows, "quarterly_by_reason")
    quarters = []
    for period in sorted(totals):
        t = totals[period]
        if "delivered_mwh" not in t or "curtailed_mwh" not in t:
            continue
        potential = t["delivered_mwh"] + t["curtailed_mwh"]
        year, quarter = period.split("-Q")
        quarters.append(
            {
                "period": period,
                "year": int(year),
                "quarter": int(quarter),
                "delivered_mwh": t["delivered_mwh"],
                "curtailed_mwh": t["curtailed_mwh"],
                "potential_mwh": potential,
                "curtailment_pct": _rate(t["curtailed_mwh"], potential),
                "firm_mwh": t.get("firm_mwh"),
                "distributed_mwh": t.get("distributed_mwh"),
                **{k: reasons.get(period, {}).get(k) for k in REASONS},
            }
        )
    return quarters


def build_annual(quarters: list[dict]) -> list[dict]:
    """Calendar years for which all four quarters exist, summed from the quarters.

    (Hawaiian Electric also prints annual figures, but it has corrected some by hand after the fact;
    summing the quarters keeps the two views consistent. `reasons_complete` says whether every
    quarter of the year had a by-reason breakdown.)
    """
    by_year: dict[int, list[dict]] = defaultdict(list)
    for q in quarters:
        by_year[q["year"]].append(q)

    annual = []
    for year in sorted(by_year):
        qs = by_year[year]
        if {q["quarter"] for q in qs} != {1, 2, 3, 4}:
            continue
        delivered = sum(q["delivered_mwh"] for q in qs)
        curtailed = sum(q["curtailed_mwh"] for q in qs)
        reasons_complete = all(q[k] is not None for q in qs for k in REASONS)
        distributed = [q["distributed_mwh"] for q in qs]
        annual.append(
            {
                "year": year,
                "delivered_mwh": delivered,
                "curtailed_mwh": curtailed,
                "potential_mwh": delivered + curtailed,
                "curtailment_pct": _rate(curtailed, delivered + curtailed),
                "distributed_mwh": sum(distributed) if all(d is not None for d in distributed) else None,
                "reasons_complete": reasons_complete,
                **{k: (sum(q[k] for q in qs) if reasons_complete else None) for k in REASONS},
            }
        )
    return annual


def summarize(quarters: list[dict], annual: list[dict]) -> dict:
    """Headline numbers for the page."""
    if not quarters or not annual:
        raise ValueError("Not enough curtailment data to summarize (need at least one full year).")
    latest = annual[-1]
    peak = max(annual, key=lambda a: a["curtailed_mwh"])
    main_reason = None
    with_reasons = [a for a in annual if a["reasons_complete"]]
    if with_reasons:
        a = with_reasons[-1]
        key = max(REASONS, key=lambda k: a[k])
        total = sum(a[k] for k in REASONS)
        main_reason = {"year": a["year"], "reason": key, "share_pct": 100 * a[key] / total if total else None}
    return {
        "first_quarter": quarters[0]["period"],
        "latest_quarter": quarters[-1]["period"],
        "latest_full_year": {
            k: latest[k] for k in ("year", "delivered_mwh", "curtailed_mwh", "potential_mwh", "curtailment_pct")
        },
        "peak_year": {k: peak[k] for k in ("year", "curtailed_mwh", "curtailment_pct")},
        "main_reason": main_reason,
    }


def reconcile_with_eia(annual: list[dict], monthly: list[dict]) -> list[dict]:
    """Compare Hawaiian Electric's delivered wind+solar with EIA's solar+wind (Tier 1) for each year.

    The two count slightly different sets of plants, so they will not match exactly; a stable ratio
    close to 1 says both are describing the same thing. Only years with 12 final EIA months are used.
    """
    months: dict[int, list[dict]] = defaultdict(list)
    for m in monthly:
        if not m["provisional"]:
            months[int(m["period"][:4])].append(m)
    out = []
    for a in annual:
        ms = months.get(a["year"], [])
        if len(ms) != 12:
            continue
        eia = sum(m["generation_by_fuel_mwh"].get("SUN", 0.0) + m["generation_by_fuel_mwh"].get("WND", 0.0) for m in ms)
        out.append(
            {
                "year": a["year"],
                "heco_delivered_mwh": a["delivered_mwh"],
                "eia_solar_wind_mwh": eia,
                "ratio": a["delivered_mwh"] / eia if eia else None,
            }
        )
    return out


def implied_share_of_state(annual: list[dict], state_rooftop_mwh: dict[str, float], year: int) -> float | None:
    """Hawaiian Electric's rooftop total for this region divided by EIA's estimate for the whole state.

    This is the number the Tier 2 duck curve assumes (`share_of_state`); here it is measured.
    Returns None if either side lacks the year.
    """
    heco = next((a["distributed_mwh"] for a in annual if a["year"] == year), None)
    months = [v for p, v in state_rooftop_mwh.items() if p.startswith(f"{year}-")]
    if heco is None or len(months) != 12:
        return None
    return heco / sum(months)
