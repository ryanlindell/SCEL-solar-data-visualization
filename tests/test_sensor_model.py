"""Tests for sensor_model/ (the sensor + pvlib estimate), kept separate from tests/test_site_solar.py (the PVWatts
pipeline's own tests) the same way the two packages are kept separate in the source tree.
"""

import pandas as pd
import pytest

from sensor_model.config import ArrayConfig, season_labels, season_months
from sensor_model.export import build_web_payload
from sensor_model.parse import hourly_means, read_ghi_5min
from sensor_model.seasonal import seasonal_averages
from sensor_model.simulate import simulate_power


def _five_min_csv(tmp_path, rows):
    """rows: list of (timestamp string with -10 offset, n, ghi)."""
    path = tmp_path / "sensor.csv"
    lines = ["t_5min,n,dev1intsolirr_avg"] + [f"{t},{n},{ghi}" for t, n, ghi in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


class TestParse:
    def test_read_ghi_5min_is_sorted_and_tz_aware(self, tmp_path):
        path = _five_min_csv(tmp_path, [
            ("2012-06-01 08:05:00-10", 28, 200.0),
            ("2012-06-01 08:00:00-10", 28, 150.0),
        ])
        series = read_ghi_5min(path)
        assert list(series.values) == [150.0, 200.0]  # sorted by time, not file order
        assert str(series.index.tz) == "UTC-10:00"

    def test_hourly_means_keeps_an_hour_only_at_or_above_the_fraction(self, tmp_path):
        # Hour 8 gets 6 of 12 slots (the 50% default threshold, kept); hour 9 gets 5 (below it, dropped).
        rows = [(f"2012-06-01 08:{m:02d}:00-10", 28, 100.0) for m in range(0, 30, 5)]
        rows += [(f"2012-06-01 09:{m:02d}:00-10", 28, 200.0) for m in range(0, 25, 5)]
        hourly = hourly_means(read_ghi_5min(_five_min_csv(tmp_path, rows)))
        assert list(hourly["hour"]) == [8]
        assert hourly.iloc[0]["poa_wm2"] == pytest.approx(100.0)
        assert hourly.iloc[0]["n_slots"] == 6

    def test_an_empty_file_gives_an_empty_frame(self, tmp_path):
        hourly = hourly_means(read_ghi_5min(_five_min_csv(tmp_path, [])))
        assert hourly.empty


class TestSimulate:
    def _hourly(self, poa_values):
        return pd.DataFrame({
            "date": [pd.Timestamp("2012-06-01").date()] * len(poa_values),
            "hour": range(len(poa_values)),
            "poa_wm2": poa_values,
            "n_slots": [12] * len(poa_values),
        })

    def test_dc_power_scales_linearly_with_irradiance_at_stc(self):
        # temp_cell == temp_ref by construction, so gamma_pdc has no effect: dc_w should be exactly pdc0 * poa/1000.
        array = ArrayConfig(capacity_kw_dc=1000.0)
        out = simulate_power(self._hourly([0.0, 500.0, 1000.0]), array)
        assert list(out["dc_w"]) == pytest.approx([0.0, 500_000.0, 1_000_000.0])

    def test_ac_power_is_capped_at_the_inverters_ceiling(self):
        array = ArrayConfig(capacity_kw_dc=1000.0)  # matches the PVWatts reference model's own 1,000 kW array
        out = simulate_power(self._hourly([1400.0]), array)  # implausibly bright, to force clipping
        # This is the exact figure NREL's PVWatts model reports for the 1,000 kW reference array (web/data/manoa_solar.json's
        # annual_summary.ac_max_w): both estimates share the same dc_ac_ratio (1.2) and inverter efficiency (0.96) by design.
        assert out["ac_w"].iloc[0] == pytest.approx(833_333.33, abs=1.0)

    def test_refuses_a_tilted_array(self):
        with pytest.raises(AssertionError):
            simulate_power(self._hourly([500.0]), ArrayConfig(tilt_deg=20.0))


class TestSeasonal:
    def _hourly(self):
        # Two Fridays (weekday) and one Saturday (weekend) in June 2012 (summer), one hour each so days_averaged is exact.
        return pd.DataFrame([
            {"date": pd.Timestamp("2012-06-01").date(), "hour": 10, "poa_wm2": 500.0, "dc_w": 500_000.0, "ac_w": 480_000.0},  # Friday
            {"date": pd.Timestamp("2012-06-08").date(), "hour": 10, "poa_wm2": 700.0, "dc_w": 700_000.0, "ac_w": 650_000.0},  # Friday
            {"date": pd.Timestamp("2012-06-02").date(), "hour": 10, "poa_wm2": 900.0, "dc_w": 900_000.0, "ac_w": 800_000.0},  # Saturday
        ])

    def test_weekdays_only_excludes_the_weekend_day(self):
        out = seasonal_averages(self._hourly(), {"summer": (6, 7, 8)}, weekdays_only=True)
        summer = out["summer"]
        assert summer["days_averaged"] == 2
        hour10 = next(h for h in summer["hours"] if h["hour"] == 10)
        assert hour10["poa_wm2"] == pytest.approx(600.0)  # mean of the two Fridays, not the Saturday
        assert hour10["n_days"] == 2

    def test_all_days_includes_the_weekend_day(self):
        out = seasonal_averages(self._hourly(), {"summer": (6, 7, 8)}, weekdays_only=False)
        hour10 = next(h for h in out["summer"]["hours"] if h["hour"] == 10)
        assert hour10["n_days"] == 3
        assert hour10["poa_wm2"] == pytest.approx((500.0 + 700.0 + 900.0) / 3)

    def test_an_hour_with_no_data_is_null_not_zero(self):
        out = seasonal_averages(self._hourly(), {"summer": (6, 7, 8)}, weekdays_only=True)
        hour3 = next(h for h in out["summer"]["hours"] if h["hour"] == 3)
        assert hour3["poa_wm2"] is None and hour3["n_days"] == 0

    def test_a_season_with_no_matching_months_is_reported_empty_not_missing(self):
        out = seasonal_averages(self._hourly(), {"winter": (12, 1, 2)}, weekdays_only=True)
        assert out["winter"]["days_averaged"] == 0
        assert all(h["poa_wm2"] is None for h in out["winter"]["hours"])


class TestExport:
    def test_web_payload_rows_match_the_pvwatts_pipelines_shape(self):
        array = ArrayConfig()
        averages = {
            "weekdays": {"summer": {"days_averaged": 2, "hours": [
                {"hour": h, "poa_wm2": None, "dc_w": None, "ac_w": None, "n_days": 0} if h != 10
                else {"hour": h, "poa_wm2": 600.0, "dc_w": 600_000.0, "ac_w": 580_000.0, "n_days": 2}
                for h in range(24)
            ]}},
        }
        payload = build_web_payload(array, averages, {"summer": (6, 7, 8)}, {"summer": "Summer"}, coverage={"note": "test"})
        row = next(r for r in payload["rows"] if r["hour"] == 10)
        assert row["day_set"] == "weekdays" and row["season"] == "summer"
        assert {"poa_wm2", "dc_w", "ac_w", "n_days"} <= row.keys()
        night_row = next(r for r in payload["rows"] if r["hour"] == 3)
        assert night_row["poa_wm2"] is None and night_row["dc_w"] is None and night_row["ac_w"] is None


class TestConfig:
    def test_seasons_come_from_the_same_regions_yaml_every_other_page_uses(self):
        months = season_months()
        labels = season_labels()
        assert set(months) == {"winter", "spring", "summer", "fall"}
        assert months["summer"] == (6, 7, 8)
        assert labels["winter"] == "Winter"

    def test_inverter_pdc0_recovers_pac0_at_the_configured_efficiency(self):
        array = ArrayConfig(capacity_kw_dc=4300.0)
        pac0 = array.inverter_pdc0_w * array.eta_inv_nom
        assert pac0 == pytest.approx(array.pdc0_w / array.dc_ac_ratio)
