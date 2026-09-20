from contextlib import closing

from pipeline import cache


def sample_rows(mwh=100.0):
    return [
        {"period": "2024-01", "plant_code": "765", "plant_name": "Kahe", "fuel_code": "RFO",
         "prime_mover": "ALL", "state": "HI", "generation_mwh": mwh},
        {"period": "2024-02", "plant_code": "765", "plant_name": "Kahe", "fuel_code": "RFO",
         "prime_mover": "ALL", "state": "HI", "generation_mwh": 50.0},
    ]


def count(conn):
    return conn.execute("SELECT COUNT(*) FROM raw_facility_fuel").fetchone()[0]


def test_rerun_does_not_duplicate_rows(tmp_path):
    with closing(cache.connect(tmp_path / "raw.sqlite")) as conn:
        first = cache.upsert_facility_fuel(conn, "oahu", "route", sample_rows())
        second = cache.upsert_facility_fuel(conn, "oahu", "route", sample_rows())
        assert first == {"fetched": 2, "new": 2, "changed": 0}
        assert second == {"fetched": 2, "new": 0, "changed": 0}
        assert count(conn) == 2


def test_revised_value_updates_in_place(tmp_path):
    with closing(cache.connect(tmp_path / "raw.sqlite")) as conn:
        cache.upsert_facility_fuel(conn, "oahu", "route", sample_rows(100.0))
        result = cache.upsert_facility_fuel(conn, "oahu", "route", sample_rows(125.5))
        assert result == {"fetched": 2, "new": 0, "changed": 1}
        assert count(conn) == 2
        stored = {r["period"]: r["generation_mwh"] for r in cache.read_facility_fuel(conn, "oahu")}
        assert stored["2024-01"] == 125.5


def test_regions_are_kept_apart(tmp_path):
    with closing(cache.connect(tmp_path / "raw.sqlite")) as conn:
        cache.upsert_facility_fuel(conn, "oahu", "route", sample_rows())
        cache.upsert_facility_fuel(conn, "maui", "route", sample_rows())
        assert count(conn) == 4
        assert len(cache.read_facility_fuel(conn, "oahu")) == 2


def test_hourly_tables_are_idempotent_and_detect_revisions(tmp_path):
    pv = [{"month": 1, "day": 1, "hour": h, "ac_w": 10.0 * h, "poa_wm2": 1.0} for h in range(24)]
    load = [{"month": 1, "day": 1, "hour": h, "total_kwh": 5.0, "pv_kwh": 0.0} for h in range(24)]
    solar = [{"period": "2024-01", "state": "HI", "generation_mwh": 100.0}]
    with closing(cache.connect(tmp_path / "raw.sqlite")) as conn:
        assert cache.upsert_pvwatts_hourly(conn, "oahu", pv) == {"fetched": 24, "new": 24, "changed": 0}
        assert cache.upsert_pvwatts_hourly(conn, "oahu", pv) == {"fetched": 24, "new": 0, "changed": 0}
        assert cache.upsert_building_load_hourly(conn, "oahu", "commercial", "office", load)["new"] == 24
        assert cache.upsert_building_load_hourly(conn, "oahu", "commercial", "office", load)["new"] == 0
        assert cache.upsert_small_scale_solar(conn, "oahu", solar)["new"] == 1
        revised = cache.upsert_small_scale_solar(conn, "oahu", [{**solar[0], "generation_mwh": 110.0}])
        assert revised == {"fetched": 1, "new": 0, "changed": 1}

        assert len(cache.read_pvwatts_hourly(conn, "oahu")) == 24
        assert len(cache.read_building_load_hourly(conn, "oahu")) == 24
        assert cache.read_small_scale_solar(conn, "oahu") == {"2024-01": 110.0}
        assert cache.has_pvwatts(conn, "oahu") and not cache.has_pvwatts(conn, "maui")
        assert cache.has_building_load(conn, "oahu", "commercial", "office")
        assert not cache.has_building_load(conn, "oahu", "residential", "office")


def test_heco_curtailment_is_idempotent_and_keeps_datasets_apart(tmp_path):
    fetched = {
        "quarterly_totals": [{"period": "2024-Q1", "series": "curtailed_mwh", "value": 5.0}],
        "by_reason": [
            {"frequency": "quarterly", "period": "2024-Q1", "series": "oversupply_mwh", "value": 1.0},
            {"frequency": "annual", "period": "2024", "series": "oversupply_mwh", "value": 4.0},
        ],
    }
    with closing(cache.connect(tmp_path / "raw.sqlite")) as conn:
        assert cache.upsert_heco_curtailment(conn, "oahu", fetched) == {"fetched": 3, "new": 3, "changed": 0}
        assert cache.upsert_heco_curtailment(conn, "oahu", fetched) == {"fetched": 3, "new": 0, "changed": 0}
        fetched["quarterly_totals"][0]["value"] = 6.0  # Hawaiian Electric corrected a number
        assert cache.upsert_heco_curtailment(conn, "oahu", fetched) == {"fetched": 3, "new": 0, "changed": 1}
        stored = {(r["dataset"], r["period"], r["series"]): r["value"] for r in cache.read_heco_curtailment(conn, "oahu")}
        assert stored == {
            ("quarterly_totals", "2024-Q1", "curtailed_mwh"): 6.0,
            ("quarterly_by_reason", "2024-Q1", "oversupply_mwh"): 1.0,
            ("annual_by_reason", "2024", "oversupply_mwh"): 4.0,
        }
        assert cache.read_heco_curtailment(conn, "maui") == []


def test_null_generation_is_stored_as_null(tmp_path):
    rows = sample_rows()
    rows[0]["generation_mwh"] = None
    with closing(cache.connect(tmp_path / "raw.sqlite")) as conn:
        cache.upsert_facility_fuel(conn, "oahu", "route", rows)
        cache.upsert_facility_fuel(conn, "oahu", "route", rows)  # NULL vs NULL is not a "change"
        stored = cache.read_facility_fuel(conn, "oahu")
        assert stored[0]["generation_mwh"] is None
        assert conn.execute("SELECT rows_changed FROM fetch_log ORDER BY id DESC").fetchone()[0] == 0
