import calendar
from datetime import date

import pytest

from pipeline.process_hourly import compute_duck_curve, net_load_stats, pick_reference_year

SEASONS = {"winter": (12, 1, 2), "summer": (6, 7, 8)}
SOLAR_HOURS = range(10, 15)  # 10:00-14:59
SHARE = 0.5

# The synthetic year is built so every calibration factor comes out round:
#   modeled demand  = 1000 (business) + 0.5 x 400 (homes, weighted by SHARE) = 1200 kWh every hour
#   modeled rooftop = 100 kWh in solar hours (unweighted)
#   reference farm  = 0.5 MW in solar hours
# and the "actual" data below asks for demand_scale 2000, rooftop_scale 2000, a 200 MW farm, 50 MW wind.
#   -> demand 2400 MW always; rooftop 200 MW in solar hours; utility solar 100 MW; wind 50 MW.


def year_keys():
    for month in range(1, 13):
        for day in range(1, calendar.monthrange(2018, month)[1] + 1):
            for hour in range(24):
                yield month, day, hour


def pv_rows():
    return [
        {"month": m, "day": d, "hour": h, "ac_w": 500_000.0 if h in SOLAR_HOURS else 0.0, "poa_wm2": 0.0}
        for m, d, h in year_keys()
    ]


def load_rows():
    rows = []
    for m, d, h in year_keys():
        rows.append({"sector": "commercial", "building_type": "office", "month": m, "day": d, "hour": h,
                     "total_kwh": 1000.0, "pv_kwh": 0.0})
        rows.append({"sector": "residential", "building_type": "home", "month": m, "day": d, "hour": h,
                     "total_kwh": 400.0, "pv_kwh": 100.0 if h in SOLAR_HOURS else 0.0})
    return rows


def _month_sizes(month):
    days = calendar.monthrange(2018, month)[1]
    return days, days * 24


def rooftop_state_mwh(year=2024):
    """Statewide rooftop estimate; Oʻahu's part (x SHARE) must equal rooftop_scale 2000 x modeled kWh."""
    out = {}
    for month in range(1, 13):
        days, _ = _month_sizes(month)
        oahu_mwh = 2000 * days * len(SOLAR_HOURS) * 100 / 1000
        out[f"{year}-{month:02d}"] = oahu_mwh / SHARE
    return out


def monthly_rows(year=2024, provisional_from=None):
    out = []
    for month in range(1, 13):
        days, hours = _month_sizes(month)
        rooftop_mwh = 2000 * days * len(SOLAR_HOURS) * 100 / 1000
        demand_mwh = 2000 * hours * 1200 / 1000
        farm_mwh = days * len(SOLAR_HOURS) * 0.5
        period = f"{year}-{month:02d}"
        out.append({
            "region": "oahu", "period": period,
            "total_mwh": demand_mwh - rooftop_mwh,  # grid = demand - rooftop
            "generation_by_fuel_mwh": {"SUN": 200 * farm_mwh, "WND": 50 * hours, "RFO": 1.0},
            "provisional": provisional_from is not None and period >= provisional_from,
        })
    return out


def compute(**kwargs):
    args = dict(load_rows=load_rows(), pv_rows=pv_rows(), monthly=monthly_rows(),
                state_rooftop_mwh=rooftop_state_mwh(), share_of_state=SHARE, rooftop_share_of_state=SHARE,
                seasons=SEASONS)
    args.update(kwargs)
    return compute_duck_curve(**args)


def hour(result, season, h):
    return result["seasons"][season]["hours"][h]


def test_night_hour_after_calibration():
    r = hour(compute(), "winter", 0)
    assert r["customer_demand_mw"] == pytest.approx(2400)
    assert r["rooftop_solar_mw"] == 0
    assert r["grid_load_mw"] == pytest.approx(2400)
    assert r["utility_solar_mw"] == 0
    assert r["wind_mw"] == pytest.approx(50)
    assert r["net_load_mw"] == pytest.approx(2350)


def test_midday_hour_shows_the_duck_belly():
    r = hour(compute(), "winter", 12)
    assert r["customer_demand_mw"] == pytest.approx(2400)
    assert r["rooftop_solar_mw"] == pytest.approx(200)
    assert r["grid_load_mw"] == pytest.approx(2200)
    assert r["utility_solar_mw"] == pytest.approx(100)  # 200 MW-equivalent farm x 0.5
    assert r["net_load_mw"] == pytest.approx(2200 - 100 - 50)


def test_net_load_is_grid_load_minus_utility_solar_and_wind():
    result = compute()
    for season in SEASONS:
        for h in result["seasons"][season]["hours"]:
            assert h["net_load_mw"] == pytest.approx(h["grid_load_mw"] - h["utility_solar_mw"] - h["wind_mw"])
            assert h["grid_load_mw"] == pytest.approx(h["customer_demand_mw"] - h["rooftop_solar_mw"])


def test_calibration_reproduces_the_real_monthly_numbers():
    result = compute()
    assert result["reference_year"] == 2024
    months = result["calibration"]["months"]
    assert all(m["demand_scale"] == pytest.approx(2000) for m in months)
    assert all(m["rooftop_scale"] == pytest.approx(2000) for m in months)
    assert all(m["reference_farm_mw_equivalent"] == pytest.approx(200) for m in months)
    # rooftop: 200 MW for 5 hours a day, all year = 365 GWh; it is EIA's estimate x SHARE, not ResStock's
    assert result["calibration"]["rooftop_solar_gwh_per_year"] == pytest.approx(365.0)
    assert result["calibration"]["utility_solar_gwh_per_year"] == pytest.approx(
        sum(m["actual_solar_mwh"] for m in months) / 1000
    )


def test_rooftop_size_comes_from_eias_estimate_not_the_models_adoption():
    doubled = {k: v * 2 for k, v in rooftop_state_mwh().items()}
    # Grid load (real generation) must stay the same; only the split between rooftop and demand moves.
    base, more = compute(), compute(state_rooftop_mwh=doubled)
    assert hour(more, "winter", 12)["rooftop_solar_mw"] == pytest.approx(2 * hour(base, "winter", 12)["rooftop_solar_mw"])
    assert hour(more, "winter", 12)["customer_demand_mw"] > hour(base, "winter", 12)["customer_demand_mw"]
    total_grid = lambda r: sum(h["grid_load_mw"] for h in r["seasons"]["winter"]["hours"])
    assert total_grid(more) == pytest.approx(total_grid(base), rel=0.02)


def test_rooftop_share_and_home_share_are_independent():
    base = compute()
    # A bigger region share of the state's ROOFTOP solar doubles rooftop output ...
    more_rooftop = compute(rooftop_share_of_state=2 * SHARE)
    assert hour(more_rooftop, "winter", 12)["rooftop_solar_mw"] == pytest.approx(2 * hour(base, "winter", 12)["rooftop_solar_mw"])
    # ... while a different share of the state's HOMES changes the demand mix, not the rooftop size.
    more_homes = compute(share_of_state=0.9)
    assert hour(more_homes, "winter", 12)["rooftop_solar_mw"] == pytest.approx(hour(base, "winter", 12)["rooftop_solar_mw"])
    assert hour(more_homes, "winter", 12)["customer_demand_mw"] == pytest.approx(hour(base, "winter", 12)["customer_demand_mw"])  # flat synthetic load: mix cannot matter


def test_missing_rooftop_estimate_is_reported():
    partial = {k: v for k, v in rooftop_state_mwh().items() if not k.endswith("-07")}
    with pytest.raises(ValueError, match="rooftop-solar estimate for 2024-07"):
        compute(state_rooftop_mwh=partial)


def test_only_weekdays_are_averaged():
    result = compute()
    expected = sum(
        1
        for month in (12, 1, 2)
        for day in range(1, calendar.monthrange(2018, month)[1] + 1)
        if date(2018, month, day).weekday() < 5
    )
    assert result["seasons"]["winter"]["weekdays_averaged"] == expected == 64


def test_weekday_average_ignores_weekend_values():
    # Move load on Saturdays/Sundays from hour 22 to hour 5. Monthly totals (and so the calibration)
    # are unchanged, so if weekends leaked into the average, hours 5 and 22 would differ.
    loads = load_rows()
    for r in loads:
        if r["sector"] == "commercial" and date(2018, r["month"], r["day"]).weekday() >= 5:
            if r["hour"] == 5:
                r["total_kwh"] += 500.0
            elif r["hour"] == 22:
                r["total_kwh"] -= 500.0
    base, shifted = compute(), compute(load_rows=loads)
    for h in (5, 22):
        assert hour(shifted, "winter", h)["customer_demand_mw"] == pytest.approx(hour(base, "winter", h)["customer_demand_mw"])


def test_reference_year_is_latest_fully_final_year():
    monthly = monthly_rows(2023) + monthly_rows(2024, provisional_from="2024-11")
    assert pick_reference_year(monthly) == 2023
    assert pick_reference_year(monthly, requested=2023) == 2023
    with pytest.raises(ValueError, match="not a fully final year"):
        pick_reference_year(monthly, requested=2024)


def test_no_complete_year_is_an_error():
    with pytest.raises(ValueError, match="no fully final"):
        pick_reference_year(monthly_rows(2024)[:6])


def test_missing_load_hours_are_reported():
    with pytest.raises(ValueError, match="No load data"):
        compute(load_rows=[r for r in load_rows() if not (r["month"] == 3 and r["day"] == 3)])


def test_load_hours_missing_from_solar_data_are_reported():
    with pytest.raises(ValueError, match="lacks"):
        compute(pv_rows=pv_rows()[:-24])


def test_net_load_stats_finds_the_belly_the_evening_peak_and_the_climb():
    net = [400.0] * 24  # a low overnight trough must NOT be mistaken for the duck's belly
    for h in range(9, 24):
        net[h] = 1000.0
    net[12] = 500.0
    net[15] = 900.0
    net[16] = 900.0
    net[19] = 1250.0
    stats = net_load_stats(net)
    assert stats["midday_low_hour"] == 12 and stats["midday_low_mw"] == 500.0
    assert stats["evening_peak_hour"] == 19 and stats["evening_peak_mw"] == 1250.0
    assert stats["evening_climb_mw"] == 750.0
    assert stats["steepest_3h_ramp_start_hour"] == 12  # 500 -> 900 over hours 12..15
    assert stats["steepest_3h_ramp_mw"] == 400.0
