import pytest

from pipeline.process import compute_monthly, find_final_through

RENEWABLE = ("SUN", "WND")
STORAGE = ("MWH",)


def row(period, plant, fuel, mwh, prime_mover="ALL"):
    return {
        "region": "oahu",
        "period": period,
        "plant_code": plant,
        "plant_name": plant,
        "fuel_code": fuel,
        "prime_mover": prime_mover,
        "generation_mwh": mwh,
    }


def test_share_is_solar_plus_wind_over_total():
    rows = [
        row("2024-01", "A", "RFO", 700.0),
        row("2024-01", "B", "SUN", 200.0),
        row("2024-01", "C", "WND", 100.0),
    ]
    monthly, _ = compute_monthly(rows, RENEWABLE, STORAGE)
    assert len(monthly) == 1
    m = monthly[0]
    assert m["total_mwh"] == 1000.0
    assert m["renewable_mwh"] == 300.0
    assert m["renewable_share_pct"] == 30.0
    assert m["generation_by_fuel_mwh"] == {"RFO": 700.0, "SUN": 200.0, "WND": 100.0}
    assert m["plants_reporting"] == 3
    assert m["region"] == "oahu"


def test_nested_rows_are_not_double_counted():
    rows = [
        row("2024-01", "A", "ALL", 100.0),  # plant total: must be ignored
        row("2024-01", "A", "RFO", 100.0),  # the level we use
        row("2024-01", "A", "RFO", 60.0, prime_mover="ST"),  # prime-mover detail: ignored
        row("2024-01", "A", "RFO", 40.0, prime_mover="GT"),
    ]
    monthly, _ = compute_monthly(rows, RENEWABLE, STORAGE)
    assert monthly[0]["total_mwh"] == 100.0


def test_battery_storage_is_excluded_from_generation():
    rows = [
        row("2024-01", "A", "SUN", 100.0),
        row("2024-01", "A", "MWH", -30.0),  # battery charging
        row("2024-01", "B", "MWH", -50.0),  # storage-only plant
    ]
    monthly, _ = compute_monthly(rows, RENEWABLE, STORAGE)
    assert monthly[0]["total_mwh"] == 100.0
    assert monthly[0]["renewable_share_pct"] == 100.0
    assert monthly[0]["plants_reporting"] == 1


def test_months_are_sorted_and_missing_generation_is_skipped():
    rows = [
        row("2024-02", "A", "RFO", 10.0),
        row("2024-01", "A", "RFO", None),
        row("2024-01", "B", "RFO", 5.0),
    ]
    monthly, _ = compute_monthly(rows, RENEWABLE, STORAGE)
    assert [m["period"] for m in monthly] == ["2024-01", "2024-02"]
    assert monthly[0]["total_mwh"] == 5.0


def test_zero_total_gives_no_share():
    monthly, _ = compute_monthly([row("2024-01", "A", "RFO", 0.0)], RENEWABLE, STORAGE)
    assert monthly[0]["renewable_share_pct"] is None


def test_empty_input():
    assert compute_monthly([], RENEWABLE, STORAGE) == ([], None)


def test_final_through_detects_annual_only_plants():
    last = {"big1": "2026-06", "big2": "2026-06", "small1": "2024-12", "small2": "2024-12"}
    assert find_final_through(last, "2026-06") == "2024-12"


def test_final_through_ignores_old_retirements_and_lone_plants():
    # Retired long ago, or a single plant that happens to end in a December: not annual reporters.
    assert find_final_through({"a": "2026-06", "old1": "2014-12", "old2": "2014-12"}, "2026-06") is None
    assert find_final_through({"a": "2026-06", "one": "2025-12"}, "2026-06") is None
    assert find_final_through({"a": "2026-06", "b": "2026-06"}, "2026-06") is None


def test_later_months_are_flagged_provisional():
    rows = []
    for period in ("2024-11", "2024-12", "2025-01"):
        rows.append(row(period, "big", "RFO", 100.0))
    rows += [row("2024-12", "s1", "SUN", 1.0), row("2024-12", "s2", "SUN", 1.0)]
    monthly, final_through = compute_monthly(rows, RENEWABLE, STORAGE)
    assert final_through == "2024-12"
    assert [(m["period"], m["provisional"]) for m in monthly] == [
        ("2024-11", False),
        ("2024-12", False),
        ("2025-01", True),
    ]


# ---- rooftop-solar estimate (added for the renewable-share chart's dotted line) ---------------------------------


from pipeline.process import add_rooftop_estimate


def month(period, total, renewable, provisional=False):
    return {"region": "oahu", "period": period, "total_mwh": total, "renewable_mwh": renewable,
            "renewable_share_pct": 100 * renewable / total, "generation_by_fuel_mwh": {"SUN": renewable},
            "plants_reporting": 5, "provisional": provisional}


def test_rooftop_estimate_scales_the_state_figure_and_widens_the_denominator():
    monthly = [month("2024-01", total=1000.0, renewable=100.0)]
    out = add_rooftop_estimate(monthly, {"2024-01": 500.0}, share_of_state=0.8)
    assert out[0]["rooftop_solar_mwh_est"] == 400.0  # 0.8 x 500
    # (100 + 400) / (1000 + 400): rooftop is added to both the numerator and the denominator
    assert out[0]["renewable_share_incl_rooftop_pct"] == pytest.approx(100 * 500 / 1400)


def test_rooftop_estimate_leaves_every_existing_field_untouched():
    monthly = [month("2024-01", 1000.0, 100.0), month("2024-02", 900.0, 90.0, provisional=True)]
    before = [dict(m) for m in monthly]
    out = add_rooftop_estimate(monthly, {"2024-01": 500.0, "2024-02": 500.0}, 0.77)
    assert monthly == before  # the input is not modified
    for old, new in zip(before, out):
        assert {k: new[k] for k in old} == old  # all the original fields are identical


def test_months_without_an_estimate_get_none_not_zero():
    monthly = [month("2013-12", 1000.0, 50.0), month("2014-01", 1000.0, 50.0)]
    out = add_rooftop_estimate(monthly, {"2014-01": 100.0}, 0.5)  # EIA's series starts in 2014
    assert out[0]["rooftop_solar_mwh_est"] is None and out[0]["renewable_share_incl_rooftop_pct"] is None
    assert out[1]["rooftop_solar_mwh_est"] == 50.0


def test_no_share_configured_means_no_rooftop_line_at_all():
    out = add_rooftop_estimate([month("2024-01", 1000.0, 100.0)], {"2024-01": 500.0}, None)
    assert out[0]["rooftop_solar_mwh_est"] is None and out[0]["renewable_share_incl_rooftop_pct"] is None


# ---- rooftop estimate anchored to the utility's reported quarterly totals ------------------------------------------


def test_reported_quarterly_total_sizes_the_estimate_and_statewide_pattern_splits_it_across_months():
    monthly = [month(p, 1000.0, 100.0) for p in ("2024-01", "2024-02", "2024-03")]
    state = {"2024-01": 100.0, "2024-02": 200.0, "2024-03": 300.0}  # 600 statewide in the quarter
    out = add_rooftop_estimate(monthly, state, share_of_state=0.5, measured_quarterly_mwh={"2024-Q1": 480.0})  # 80% of it
    assert [m["rooftop_solar_mwh_est"] for m in out] == pytest.approx([80.0, 160.0, 240.0])
    assert sum(m["rooftop_solar_mwh_est"] for m in out) == pytest.approx(480.0)  # the quarter equals the reported number
    assert {m["rooftop_basis"] for m in out} == {"measured"}
    assert [m["rooftop_share_used"] for m in out] == pytest.approx([0.8, 0.8, 0.8])


def test_quarters_without_a_reported_total_fall_back_to_the_assumed_share():
    monthly = [month("2024-01", 1000.0, 100.0), month("2024-04", 1000.0, 100.0)]
    state = {"2024-01": 100.0, "2024-02": 100.0, "2024-03": 100.0, "2024-04": 200.0, "2024-05": 200.0, "2024-06": 200.0}
    out = add_rooftop_estimate(monthly, state, share_of_state=0.5, measured_quarterly_mwh={"2024-Q1": 150.0})
    assert (out[0]["rooftop_basis"], out[0]["rooftop_solar_mwh_est"]) == ("measured", 50.0)
    assert (out[1]["rooftop_basis"], out[1]["rooftop_solar_mwh_est"]) == ("assumed", 100.0)  # Q2 not reported: 0.5 x 200


def test_an_incomplete_statewide_quarter_cannot_be_anchored():
    monthly = [month("2024-01", 1000.0, 100.0)]
    out = add_rooftop_estimate(monthly, {"2024-01": 100.0, "2024-02": 100.0}, 0.5, {"2024-Q1": 300.0})  # March missing
    assert (out[0]["rooftop_basis"], out[0]["rooftop_solar_mwh_est"]) == ("assumed", 50.0)


def test_no_reported_totals_and_no_share_means_no_estimate():
    out = add_rooftop_estimate([month("2024-01", 1000.0, 100.0)], {"2024-01": 100.0}, None, {})
    assert out[0]["rooftop_solar_mwh_est"] is None and out[0]["rooftop_basis"] is None
    # ...but a reported total alone is enough, even with no fallback share
    full = {"2024-01": 100.0, "2024-02": 100.0, "2024-03": 100.0}
    out = add_rooftop_estimate([month("2024-01", 1000.0, 100.0)], full, None, {"2024-Q1": 90.0})
    assert out[0]["rooftop_basis"] == "measured" and out[0]["rooftop_solar_mwh_est"] == pytest.approx(30.0)


def test_anchoring_to_reported_totals_still_leaves_every_existing_field_alone():
    monthly = [month("2024-01", 1000.0, 100.0)]
    before = [dict(m) for m in monthly]
    out = add_rooftop_estimate(monthly, {"2024-01": 100.0, "2024-02": 100.0, "2024-03": 100.0}, 0.77, {"2024-Q1": 231.0})
    assert monthly == before and {k: out[0][k] for k in before[0]} == before[0]
