"""Write the NSRDB 2012 estimate to its own folder (data/manoa/nsrdb_pvlib/) and its own web JSON
(web/data/manoa_solar_nsrdb.json). The seasonal CSV and JSON writer are sensor_model's, so the files line up column
for column with the sensor estimate's."""

import csv
from datetime import datetime, timezone
from pathlib import Path

import pvlib

from sensor_model.export import write_json, write_seasonal_csv  # noqa: F401 (write_seasonal_csv re-exported for run.py)

from .config import DATASET, INTERVAL_MIN, YEAR, ArrayConfig

DAY_SETS = {
    "weekdays": f"Monday-Friday of the real {YEAR} calendar",
    "all_days": f"every day of the season in {YEAR}; sunlight has no weekday pattern, so this is less noisy",
}
HOURLY_COLUMNS = ("poa_wm2", "dn_wm2", "df_wm2", "tamb_c", "wspd_ms", "zenith_deg", "dc_w", "ac_w")


def _r(value, digits):
    return None if value is None else round(value, digits)


def write_hourly_csv(hourly, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["date", "hour", *HOURLY_COLUMNS])
        for row in hourly.itertuples(index=False):
            w.writerow([row.date.isoformat(), row.hour, *(round(getattr(row, c), 3) for c in HOURLY_COLUMNS)])


def _source(meta: dict) -> str:
    return (f"NREL NSRDB {DATASET} ({YEAR}, {INTERVAL_MIN}-minute, grid cell {meta['location_id']}), "
            f"power via pvlib {pvlib.__version__} (pvlib.pvsystem.pvwatts_dc, pvlib.inverter.pvwatts)")


def _assumptions(array: ArrayConfig) -> dict:
    return {
        "capacity_kw_dc": array.capacity_kw_dc, "tilt_deg": array.tilt_deg, "dc_ac_ratio": array.dc_ac_ratio,
        "eta_inv_nom": array.eta_inv_nom, "eta_inv_ref": array.eta_inv_ref, "gamma_pdc": array.gamma_pdc,
        "assumed_cell_temp_c": array.assumed_cell_temp_c,
    }


def build_model_payload(array, averages, seasons_months, seasons_labels, meta: dict, request: dict, summary: dict) -> dict:
    return {
        "meta": {
            "dataset": "manoa_nsrdb_pvlib_model",
            "title": f"UH Mānoa: NSRDB {YEAR} satellite irradiance simulated through pvlib's PVWatts power model",
            "scope": (
                "A third Mānoa estimate, alongside pipeline/'s PVWatts model and sensor_model/'s ground-sensor estimate. "
                "It uses sensor_model's array, power model and seasonal averaging unchanged, so against the sensor line the "
                "only difference is the irradiance source (satellite vs. ground, same year)."
            ),
            "kind": "satellite-derived irradiance, simulated power",
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": _source(meta),
        },
        "request": request,
        "grid_cell": meta,
        "assumptions": {
            **_assumptions(array),
            "notes": [
                "Cell temperature is held at 25 degC (STC), exactly as in the sensor estimate, even though NSRDB supplies "
                "air temperature and wind: using them would add a second difference between the two lines.",
                "The array is flat, so GHI is the plane-of-array irradiance and needs no transposition.",
            ],
        },
        "conventions": {
            "hour": "hour beginning, Hawaii Standard Time (UTC-10); NSRDB stamps each 60-minute value at H:30, the middle of that hour",
            "day_sets": DAY_SETS,
        },
        "annual_summary": {k: _r(v, 3) for k, v in summary.items()},
        "seasonal_averages": {
            day_set: {
                sid: {"label": seasons_labels[sid], "months": list(seasons_months[sid]), "days_averaged": s["days_averaged"],
                      "hours": [{"hour": h["hour"], "n_days": h["n_days"], **{f: _r(h[f], 3) for f in ("poa_wm2", "dc_w", "ac_w")}}
                                for h in s["hours"]]}
                for sid, s in by_season.items()
            }
            for day_set, by_season in averages.items()
        },
    }


def build_web_payload(array, averages, seasons_months, seasons_labels, meta: dict, summary: dict) -> dict:
    """Same row shape as web/data/manoa_solar_sensor.json (day_set, season, hour, n_days, poa_wm2, dc_w, ac_w)."""
    rows = []
    for day_set, by_season in averages.items():
        for sid, s in by_season.items():
            for h in s["hours"]:
                rows.append({"day_set": day_set, "season": sid, "hour": h["hour"], "n_days": h["n_days"],
                             "poa_wm2": _r(h["poa_wm2"], 1), "dc_w": _r(h["dc_w"], 0), "ac_w": _r(h["ac_w"], 0)})
    return {
        "meta": {
            "dataset": "manoa_solar_nsrdb",
            "kind": "satellite-derived irradiance, simulated power",
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": _source(meta),
            "time_zone": "Hawaii Standard Time (no daylight saving)",
            "hour_convention": "hour beginning (12 = 12:00-1:00 PM); NSRDB's value for that hour is stamped at 12:30.",
            "day_sets": DAY_SETS,
            "full_dataset": "data/manoa/nsrdb_pvlib/ (JSON, CSV, and the raw NSRDB download)",
        },
        "grid_cell": {k: meta[k] for k in ("location_id", "cell_lat", "cell_lon", "elevation_m", "local_time_zone")},
        "assumptions": _assumptions(array),
        "annual_summary": {k: _r(v, 3) for k, v in summary.items()},
        "seasons": {
            sid: {"label": seasons_labels[sid], "months": list(months),
                  "days": {day_set: averages[day_set][sid]["days_averaged"] for day_set in averages}}
            for sid, months in seasons_months.items()
        },
        "rows": rows,
    }
