"""Write the modeled duck-curve data to the static JSON file the web page reads."""

from datetime import datetime, timezone

from .config import Settings

SERIES_FIELDS = (
    "customer_demand_mw",
    "rooftop_solar_mw",
    "grid_load_mw",
    "utility_solar_mw",
    "wind_mw",
    "net_load_mw",
)


def build_payload(settings: Settings, results: dict[str, dict]) -> dict:
    """results maps region id -> process_hourly.compute_duck_curve output."""
    hd = settings.hourly_data
    regions = {}
    rows = []
    for region_id, result in results.items():
        region = settings.regions[region_id]
        regions[region_id] = {
            "label": region.label,
            "reference_year": result["reference_year"],
            "share_of_state": region.hourly.share_of_state,
            "rooftop_share_of_state": region.hourly.rooftop_share_of_state,
            "rooftop_solar_gwh_per_year": round(result["calibration"]["rooftop_solar_gwh_per_year"], 1),
            "utility_solar_gwh_per_year": round(result["calibration"]["utility_solar_gwh_per_year"], 1),
            "seasons": {},
        }
        for season_id, season in result["seasons"].items():
            cfg = hd.seasons[season_id]
            regions[region_id]["seasons"][season_id] = {
                "label": cfg.label,
                "months": list(cfg.months),
                "weekdays_averaged": season["weekdays_averaged"],
                "stats": {k: round(v, 1) for k, v in season["stats"].items()},
            }
            for h in season["hours"]:
                rows.append(
                    {
                        "region": region_id,
                        "season": season_id,
                        "hour": h["hour"],
                        **{f: round(h[f], 1) for f in SERIES_FIELDS},
                    }
                )

    return {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "kind": "modeled",
            "units": "MW (average over each hour)",
            "day_type": "average weekday (Monday-Friday)",
            "time_zone": "Hawaii Standard Time (no daylight saving)",
            "sources": [
                "NREL End-Use Load Profiles (ResStock / ComStock): modeled hourly customer demand and rooftop solar",
                "NREL PVWatts v8: modeled hourly utility-scale solar output (typical meteorological year)",
                "U.S. EIA Open Data API: actual monthly generation by plant (electricity/facility-fuel) "
                "and estimated rooftop solar (electricity/electric-power-operational-data), used to set the sizes",
            ],
        },
        "regions": regions,
        "rows": rows,
    }
