"""Write a single-site PVWatts dataset (e.g. UH Mānoa) to its own folder, data/<site id>/.

Three files, all named after the site so they cannot be mistaken for the Oʻahu duck-curve output (which lives in
web/data/duck_curve.json and is never touched here):

    <site>_pvwatts_model.json               what was asked, what NREL matched it to, assumptions, yearly summary and the
                                            seasonal averages
    <site>_pvwatts_hourly.csv               all 8,760 hours, every column PVWatts returned
    <site>_pvwatts_seasonal_averages.csv    the seasonal typical day (weekdays, and all days), 24 hours per season

and, for the site's own web page only, a small copy of the seasonal typical days: web/data/<site>_solar.json
(build_web_payload). No other page reads it, and the duck-curve files are never involved.
"""

import csv
from datetime import datetime, timezone
from pathlib import Path

from .config import PvSite, Season
from .process_site_solar import SERIES_FIELDS

UNITS = {
    "poa_wm2": "W/m2 plane-of-array irradiance (with tilt 0 this is global horizontal irradiance)",
    "dn_wm2": "W/m2 beam (direct normal) irradiance",
    "df_wm2": "W/m2 diffuse horizontal irradiance",
    "dc_w": "W DC power of the reference array (1,000 kW DC), before the inverter",
    "ac_w": "W AC power of the same array after the inverter model; clipped at the inverter's ceiling, so prefer dc_w",
    "tamb_c": "deg C ambient temperature",
    "tcell_c": "deg C modeled module (cell) temperature",
    "wspd_ms": "m/s wind speed",
    "albedo": "ground reflectance (0-1) used in the model",
}
DAY_SETS = {
    "weekdays": "Monday-Friday of the 2018 calendar (the Oʻahu duck curve's definition; 64/66/66/65 days per season)",
    "all_days": "every day of the season (about 90-92 days); sunlight has no weekday pattern, so this is less noisy",
}


def _r(value, digits=3):
    return None if value is None else round(value, digits)


def build_payload(
    site: PvSite,
    meta: dict,
    summary: dict,
    averages: dict[str, dict],
    seasons: dict[str, Season],
) -> dict:
    """`meta` is cache.read_pvwatts_site(); `averages` maps 'weekdays' / 'all_days' to seasonal_averages output."""
    station = meta["station_info"]
    seasonal = {}
    for day_set, by_season in averages.items():
        seasonal[day_set] = {
            sid: {
                "label": seasons[sid].label,
                "months": list(seasons[sid].months),
                "days_averaged": s["days_averaged"],
                "hours": [{"hour": h["hour"], **{f: _r(h[f]) for f in SERIES_FIELDS}} for h in s["hours"]],
            }
            for sid, s in by_season.items()
        }
    pv = site.pv_system
    return {
        "meta": {
            "dataset": f"{site.id}_pvwatts_model",
            "title": f"{site.label}: NREL PVWatts modeled irradiance and power",
            "scope": (
                "A single-site model, kept separate from the Oʻahu-wide duck curve. It is not used by, and does not "
                "change, the duck curve, the plant map or any Oʻahu page; only the site's own Mānoa page reads a compact "
                "copy of it (web/data/" + site.id + "_solar.json)."
            ),
            "kind": "modeled",
            "purpose": "the modeled side of a comparison with a real ground sensor (not yet available)",
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": f"NREL PVWatts API v{meta.get('pvwatts_version') or '?'}, NSRDB weather data",
            "fetched_at": meta["fetched_at"],
        },
        "site": {
            "id": site.id,
            "label": site.label,
            "coordinates_are_placeholder": site.placeholder,
            "coordinates_note": site.note,
            "query": {"lat": meta["query_lat"], "lon": meta["query_lon"]},
            "weather_cell": {
                "lat": station.get("lat"),
                "lon": station.get("lon"),
                "distance_from_query_m": station.get("distance"),
                "elevation_m": station.get("elev"),
                "nsrdb_cell_id": station.get("location"),
                "solar_resource_file": station.get("solar_resource_file"),
                "weather_data_source": station.get("weather_data_source"),
                "time_zone_utc_offset": station.get("tz"),
            },
            "station_info_as_returned": station,
        },
        "assumptions": {
            "request_sent": meta["request"],
            "tilt_deg": pv.get("tilt"),
            "azimuth_deg": pv.get("azimuth"),
            "array_type": pv.get("array_type"),
            "module_type": pv.get("module_type"),
            "losses_pct": pv.get("losses"),
            "system_capacity_kw_dc": 1000,
            "not_passed": "dc_ac_ratio, inv_eff, gcr, radius, soiling, bifaciality, albedo (left at the API's defaults)",
        },
        "units": UNITS,
        "conventions": {
            "hour": (
                "hour beginning, 0 = 12:00-1:00 AM, Hawaii Standard Time (UTC-10, no daylight saving). This labeling is an "
                "assumption: the generation windows fit it, but it has not been confirmed against NREL's documentation."
            ),
            "typical_year": (
                "PVWatts returns a typical meteorological year, not a real year. Month and day are positions in that "
                "composite year and do not match real dates, so compare seasonal averages and distributions, not days."
            ),
            "weekday_calendar": "the 2018 calendar, as in the duck-curve model; it has no physical meaning for sunlight",
            "day_sets": DAY_SETS,
        },
        "notes": [
            "With tilt 0 the plane-of-array irradiance (poa_wm2) is the global horizontal irradiance, and azimuth has no effect.",
            "losses does not change irradiance (poa, dn, df); it only derates the power columns. With losses 0 the "
            "power is not a real-system estimate, and the inverter model still clips ac_w at its ceiling: use dc_w or "
            "the irradiance columns for comparisons.",
            "Weather comes from a satellite-derived NSRDB grid cell several kilometres wide; it cannot resolve Mānoa "
            "Valley's local cloud and rain, or shading by the ridge, trees and buildings.",
        ],
        "annual_summary": {k: _r(v, 3) for k, v in summary.items()},
        "seasonal_averages": seasonal,
    }


def write_hourly_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["month", "day", "hour", *SERIES_FIELDS])
        for r in rows:
            w.writerow([r["month"], r["day"], r["hour"], *("" if r[c] is None else r[c] for c in SERIES_FIELDS)])


def write_seasonal_csv(averages: dict[str, dict], seasons: dict[str, Season], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["day_set", "season", "season_label", "days_averaged", "hour", *SERIES_FIELDS])
        for day_set, by_season in averages.items():
            for sid, s in by_season.items():
                for h in s["hours"]:
                    w.writerow(
                        [day_set, sid, seasons[sid].label, s["days_averaged"], h["hour"],
                         *("" if h[c] is None else round(h[c], 3) for c in SERIES_FIELDS)]
                    )


WEB_FIELDS = ("poa_wm2", "dc_w", "ac_w")  # what the page draws: sunlight, and power before and after the inverter


def build_web_payload(site: PvSite, meta: dict, summary: dict, averages: dict[str, dict], seasons: dict[str, Season]) -> dict:
    """The compact JSON the site's own page reads: the seasonal typical days (24 hours x season x day set), the assumptions
    and where the weather came from. Rounded for size (power to the watt, sunlight to 0.1 W/m2)."""
    station = meta["station_info"]
    rows = []
    for day_set, by_season in averages.items():
        for sid, s in by_season.items():
            for h in s["hours"]:
                missing = [f for f in WEB_FIELDS if h[f] is None]
                if missing:
                    raise ValueError(f"The page needs {', '.join(WEB_FIELDS)} but {', '.join(missing)} is missing for {sid}, hour {h['hour']}.")
                rows.append({"day_set": day_set, "season": sid, "hour": h["hour"], "poa_wm2": round(h["poa_wm2"], 1),
                             "dc_w": round(h["dc_w"]), "ac_w": round(h["ac_w"])})
    pv = site.pv_system
    return {
        "meta": {
            "dataset": f"{site.id}_solar",
            "kind": "modeled",
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": f"NREL PVWatts API v{meta.get('pvwatts_version') or '?'}, NSRDB weather data",
            "time_zone": "Hawaii Standard Time (no daylight saving)",
            "hour_convention": "hour beginning (12 = 12:00-1:00 PM); values are averages over the hour. Assumed, not confirmed against NREL's documentation.",
            "day_sets": DAY_SETS,
            "full_dataset": f"data/{site.id}/ (JSON and CSV, all 8,760 hours)",
        },
        "site": {
            "label": site.label,
            "coordinates_are_placeholder": site.placeholder,
            "query": {"lat": meta["query_lat"], "lon": meta["query_lon"]},
            "weather_cell": {
                "lat": station.get("lat"), "lon": station.get("lon"), "distance_from_query_m": station.get("distance"),
                "nsrdb_cell_id": station.get("location"), "weather_data_source": station.get("weather_data_source"),
            },
        },
        "assumptions": {
            "tilt_deg": pv.get("tilt"), "azimuth_deg": pv.get("azimuth"), "array_type": pv.get("array_type"),
            "module_type": pv.get("module_type"), "losses_pct": pv.get("losses"), "system_capacity_kw_dc": 1000,
        },
        "annual_summary": {k: _r(v, 3) for k, v in summary.items()},
        "seasons": {
            sid: {"label": seasons[sid].label, "months": list(seasons[sid].months),
                  "days": {day_set: averages[day_set][sid]["days_averaged"] for day_set in averages}}
            for sid in seasons
        },
        "rows": rows,
    }
