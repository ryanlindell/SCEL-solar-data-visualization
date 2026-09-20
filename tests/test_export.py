from pipeline.config import Region, Settings, HourlyData
from pipeline.export import build_payload


def settings(share):
    region = Region(id="oahu", label="Oʻahu", state="HI", county="Honolulu", plants={"1": "A"}, rooftop_share_of_state=share)
    hd = HourlyData("r", "c", -5, -10, {})
    return Settings(renewable_fuels=("SUN", "WND"), storage_fuels=("MWH",), regions={"oahu": region}, hourly_data=hd, heco=None, eia860m=None)


def row(period, rooftop=None, incl=None):
    return {"region": "oahu", "period": period, "generation_by_fuel_mwh": {"SUN": 1.23456}, "total_mwh": 10.04, "renewable_mwh": 1.23456,
            "renewable_share_pct": 12.34567, "plants_reporting": 3, "provisional": False,
            "rooftop_solar_mwh_est": rooftop, "renewable_share_incl_rooftop_pct": incl}


def test_payload_carries_the_rooftop_fields_and_says_where_the_estimate_starts():
    payload = build_payload(settings(0.77), {"oahu": ([row("2013-12"), row("2014-01", 123.456, 20.123456)], None)})
    meta = payload["regions"]["oahu"]
    assert meta["rooftop_share_of_state"] == 0.77 and meta["rooftop_estimate_from"] == "2014-01"
    early, later = payload["rows"]
    assert early["rooftop_solar_mwh_est"] is None and early["renewable_share_incl_rooftop_pct"] is None
    assert later["rooftop_solar_mwh_est"] == 123.5 and later["renewable_share_incl_rooftop_pct"] == 20.123
    assert later["renewable_share_pct"] == 12.346  # existing rounding is unchanged


def test_payload_without_a_rooftop_share_has_no_estimate_start():
    payload = build_payload(settings(None), {"oahu": ([row("2024-01")], None)})
    assert payload["regions"]["oahu"]["rooftop_estimate_from"] is None
