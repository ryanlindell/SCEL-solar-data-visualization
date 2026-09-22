"""Write the sensor + pvlib estimate to its own folder (data/manoa/sensor_pvlib/) and its own web JSON
(web/data/manoa_solar_sensor.json) - never the PVWatts pipeline's files, so the two estimates cannot collide.
"""

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

import pvlib

from .config import ArrayConfig, MIN_HOUR_FRACTION

DAY_SETS = {
    "weekdays": "Monday-Friday of the real calendar date (2012)",
    "all_days": "every day with data in the season; sunlight has no weekday pattern, so this is less noisy",
}
FIELDS = ("poa_wm2", "dc_w", "ac_w")


def _r(value, digits):
    return None if value is None else round(value, digits)


def write_json(payload: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
        f.write("\n")


def write_hourly_csv(hourly, path: Path) -> None:
    """Every (date, hour) the sensor actually produced a trustworthy mean for - a few thousand rows, not 8,760;
    see MIN_HOUR_FRACTION for what "trustworthy" means here."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["date", "hour", "n_slots_of_12", "poa_wm2", "dc_w", "ac_w"])
        for row in hourly.itertuples(index=False):
            w.writerow([row.date.isoformat(), row.hour, row.n_slots, round(row.poa_wm2, 3), round(row.dc_w, 3), round(row.ac_w, 3)])


def write_seasonal_csv(averages: dict, seasons_labels: dict[str, str], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["day_set", "season", "season_label", "days_averaged", "hour", "n_days", *FIELDS])
        for day_set, by_season in averages.items():
            for sid, s in by_season.items():
                for h in s["hours"]:
                    w.writerow(
                        [day_set, sid, seasons_labels[sid], s["days_averaged"], h["hour"], h["n_days"],
                         *("" if h[f] is None else round(h[f], 3) for f in FIELDS)]
                    )


def build_model_payload(
    array: ArrayConfig,
    averages: dict[str, dict],
    seasons_months: dict[str, tuple[int, ...]],
    seasons_labels: dict[str, str],
    coverage: dict,
) -> dict:
    """The full record: what was measured, what pvlib was asked to do with it, and the seasonal typical days."""
    return {
        "meta": {
            "dataset": "manoa_sensor_pvlib_model",
            "title": "UH Mānoa: real sensor irradiance simulated through pvlib's PVWatts power model",
            "scope": (
                "A second, independent estimate of Mānoa's solar power, separate from pipeline/'s NREL PVWatts model "
                "(data/manoa/manoa_pvwatts_*). It shares no code and no output file with that pipeline; only the "
                "Mānoa page (web/js/manoa.js) reads a compact copy of both, to overlay them."
            ),
            "kind": "measured irradiance, simulated power",
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": f"Ground sensor data/manoa/sensor/sunny_irradiance_2011_2012.csv, power via pvlib {pvlib.__version__} "
            "(pvlib.pvsystem.pvwatts_dc, pvlib.inverter.pvwatts)",
        },
        "coverage": coverage,
        "assumptions": {
            "capacity_kw_dc": array.capacity_kw_dc,
            "tilt_deg": array.tilt_deg,
            "dc_ac_ratio": array.dc_ac_ratio,
            "eta_inv_nom": array.eta_inv_nom,
            "eta_inv_ref": array.eta_inv_ref,
            "gamma_pdc": array.gamma_pdc,
            "assumed_cell_temp_c": array.assumed_cell_temp_c,
            "min_hour_fraction": MIN_HOUR_FRACTION,
            "notes": [
                "The array size, tilt and DC/AC ratio match the PVWatts reference model exactly, so the two curves "
                "describe the same hypothetical array and can be overlaid without a separate scaling step.",
                "Cell temperature is held at STC (25 degC) because the sensor has no ambient-temperature or wind "
                "channel; this makes gamma_pdc a no-op rather than a real thermal derate, the same simplification "
                "the reference model makes with losses 0.",
                "An hour needs at least half its twelve 5-minute samples present to be averaged at all; otherwise "
                "it is left out (null), not filled in.",
            ],
        },
        "units": {
            "poa_wm2": "W/m2, the sensor's own reading (global horizontal irradiance; poa because the array is flat)",
            "dc_w": "W DC power of the array, from pvlib.pvsystem.pvwatts_dc",
            "ac_w": "W AC power after pvlib's inverter model; clipped at the inverter's ceiling, same as the reference model",
        },
        "conventions": {
            "hour": "hour beginning, Hawaii Standard Time (UTC-10, no daylight saving) - assumed from the sensor's own timestamps, not confirmed against the logger's documentation",
            "day_sets": DAY_SETS,
        },
        "seasons": {sid: {"label": seasons_labels[sid], "months": list(months)} for sid, months in seasons_months.items()},
        "seasonal_averages": {
            day_set: {
                sid: {"label": seasons_labels[sid], "months": list(seasons_months[sid]), "days_averaged": s["days_averaged"],
                      "hours": [{"hour": h["hour"], "n_days": h["n_days"], **{f: _r(h[f], 3) for f in FIELDS}} for h in s["hours"]]}
                for sid, s in by_season.items()
            }
            for day_set, by_season in averages.items()
        },
    }


def build_web_payload(
    array: ArrayConfig,
    averages: dict[str, dict],
    seasons_months: dict[str, tuple[int, ...]],
    seasons_labels: dict[str, str],
    coverage: dict,
) -> dict:
    """The compact JSON web/js/manoa.js reads to overlay this estimate on the PVWatts one. Same row shape as
    pipeline/export_site_solar.py's web payload (day_set, season, hour, poa_wm2, dc_w, ac_w) plus n_days, and nulls
    left as nulls (a real night or a data gap, not zero)."""
    rows = []
    for day_set, by_season in averages.items():
        for sid, s in by_season.items():
            for h in s["hours"]:
                rows.append({
                    "day_set": day_set, "season": sid, "hour": h["hour"], "n_days": h["n_days"],
                    "poa_wm2": _r(h["poa_wm2"], 1), "dc_w": _r(h["dc_w"], 0), "ac_w": _r(h["ac_w"], 0),
                })
    return {
        "meta": {
            "dataset": "manoa_solar_sensor",
            "kind": "measured irradiance, simulated power",
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": f"Real ground sensor + pvlib {pvlib.__version__} (PVWatts DC and inverter equations)",
            "time_zone": "Hawaii Standard Time (no daylight saving)",
            "hour_convention": "hour beginning (12 = 12:00-1:00 PM); assumed from the sensor's own timestamps, not confirmed against the logger's documentation.",
            "day_sets": DAY_SETS,
            "full_dataset": "data/manoa/sensor_pvlib/ (JSON and CSV) and data/manoa/sensor/ (the raw 5-minute readings)",
        },
        "coverage": coverage,
        "assumptions": {
            "capacity_kw_dc": array.capacity_kw_dc, "tilt_deg": array.tilt_deg, "dc_ac_ratio": array.dc_ac_ratio,
            "eta_inv_nom": array.eta_inv_nom, "assumed_cell_temp_c": array.assumed_cell_temp_c,
        },
        "seasons": {
            sid: {"label": seasons_labels[sid], "months": list(months),
                  "days": {day_set: averages[day_set][sid]["days_averaged"] for day_set in averages}}
            for sid, months in seasons_months.items()
        },
        "rows": rows,
    }
