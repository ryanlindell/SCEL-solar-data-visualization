import pytest

from pipeline.process_curtailment import (
    build_annual,
    build_quarters,
    implied_share_of_state,
    reconcile_with_eia,
    summarize,
)


def raw(dataset, period, **series):
    return [{"dataset": dataset, "period": period, "series": k, "value": v} for k, v in series.items()]


def year_of_totals(year, delivered=100.0, curtailed=10.0, distributed=500.0):
    rows = []
    for q in (1, 2, 3, 4):
        rows += raw("quarterly_totals", f"{year}-Q{q}", delivered_mwh=delivered, curtailed_mwh=curtailed,
                    firm_mwh=1.0, distributed_mwh=distributed)
    return rows


def year_of_reasons(year, oversupply=2.0, constraint=7.0, requested=1.0):
    rows = []
    for q in (1, 2, 3, 4):
        rows += raw("quarterly_by_reason", f"{year}-Q{q}", oversupply_mwh=oversupply,
                    system_constraint_mwh=constraint, facility_requested_mwh=requested, total_mwh=10.0)
    return rows


def test_quarter_potential_and_rate():
    q = build_quarters(raw("quarterly_totals", "2024-Q2", delivered_mwh=90.0, curtailed_mwh=10.0, distributed_mwh=5.0))[0]
    assert q["potential_mwh"] == 100.0 and q["curtailment_pct"] == pytest.approx(10.0)
    assert (q["year"], q["quarter"]) == (2024, 2)
    assert q["oversupply_mwh"] is None  # no by-reason numbers for this quarter


def test_quarters_are_sorted_and_incomplete_ones_skipped():
    rows = (raw("quarterly_totals", "2024-Q2", delivered_mwh=1.0, curtailed_mwh=0.0)
            + raw("quarterly_totals", "2024-Q1", delivered_mwh=1.0, curtailed_mwh=0.0)
            + raw("quarterly_totals", "2024-Q3", delivered_mwh=1.0))  # no curtailed figure
    assert [q["period"] for q in build_quarters(rows)] == ["2024-Q1", "2024-Q2"]


def test_zero_potential_has_no_rate():
    q = build_quarters(raw("quarterly_totals", "2024-Q1", delivered_mwh=0.0, curtailed_mwh=0.0))[0]
    assert q["curtailment_pct"] is None


def test_annual_sums_the_quarters_and_skips_partial_years():
    quarters = build_quarters(year_of_totals(2023) + year_of_reasons(2023) + raw(
        "quarterly_totals", "2024-Q1", delivered_mwh=1.0, curtailed_mwh=1.0))
    annual = build_annual(quarters)
    assert [a["year"] for a in annual] == [2023]  # 2024 has only one quarter
    a = annual[0]
    assert a["delivered_mwh"] == 400.0 and a["curtailed_mwh"] == 40.0 and a["potential_mwh"] == 440.0
    assert a["curtailment_pct"] == pytest.approx(100 * 40 / 440)
    assert a["distributed_mwh"] == 2000.0
    assert a["reasons_complete"] and a["oversupply_mwh"] == 8.0 and a["system_constraint_mwh"] == 28.0


def test_annual_reasons_are_left_out_when_any_quarter_lacks_them():
    rows = year_of_totals(2015) + year_of_reasons(2015)[4:]  # Q1 has no breakdown
    a = build_annual(build_quarters(rows))[0]
    assert a["reasons_complete"] is False and a["oversupply_mwh"] is None


def test_summary_finds_latest_year_peak_year_and_main_reason():
    rows = (year_of_totals(2021, curtailed=40.0) + year_of_totals(2022, curtailed=10.0)
            + year_of_reasons(2021) + year_of_reasons(2022, oversupply=0.0, constraint=9.0, requested=1.0))
    quarters = build_quarters(rows)
    s = summarize(quarters, build_annual(quarters))
    assert s["latest_full_year"]["year"] == 2022 and s["latest_full_year"]["curtailed_mwh"] == 40.0
    assert s["peak_year"]["year"] == 2021 and s["peak_year"]["curtailed_mwh"] == 160.0
    assert s["main_reason"] == {"year": 2022, "reason": "system_constraint_mwh", "share_pct": pytest.approx(90.0)}
    assert (s["first_quarter"], s["latest_quarter"]) == ("2021-Q1", "2022-Q4")


def test_summary_needs_a_full_year():
    quarters = build_quarters(raw("quarterly_totals", "2024-Q1", delivered_mwh=1.0, curtailed_mwh=0.0))
    with pytest.raises(ValueError, match="full year"):
        summarize(quarters, build_annual(quarters))


def monthly_2024(sun=10.0, wnd=5.0, provisional=False, months=12):
    return [{"period": f"2024-{m:02d}", "provisional": provisional,
             "generation_by_fuel_mwh": {"SUN": sun, "WND": wnd, "RFO": 999.0}} for m in range(1, months + 1)]


def test_reconciliation_with_eia_uses_final_full_years_only():
    annual = build_annual(build_quarters(year_of_totals(2024, delivered=45.0)))  # 180 MWh delivered
    r = reconcile_with_eia(annual, monthly_2024())[0]  # EIA: 12 x (10 + 5) = 180
    assert r["year"] == 2024 and r["eia_solar_wind_mwh"] == 180.0 and r["ratio"] == pytest.approx(1.0)
    assert reconcile_with_eia(annual, monthly_2024(provisional=True)) == []
    assert reconcile_with_eia(annual, monthly_2024(months=11)) == []


def test_implied_share_of_state():
    annual = build_annual(build_quarters(year_of_totals(2024, distributed=250.0)))  # 1000 MWh rooftop
    state = {f"2024-{m:02d}": 200.0 for m in range(1, 13)}  # 2400 MWh statewide
    assert implied_share_of_state(annual, state, 2024) == pytest.approx(1000 / 2400)
    assert implied_share_of_state(annual, {k: v for k, v in state.items() if k != "2024-05"}, 2024) is None
    assert implied_share_of_state(annual, state, 2019) is None
