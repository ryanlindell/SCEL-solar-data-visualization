"""Build the sensor + pvlib estimate end to end: parse the raw sensor readings, run them through pvlib's PVWatts
power model, average into seasonal typical days, and export.

    python -m sensor_model.run

Entirely separate from `python -m pipeline.run_site_solar` (the NREL PVWatts model): no shared code, cache, or output
file, so running one can never change the other. Reads only data/manoa/sensor/sunny_irradiance_2011_2012.csv; writes
data/manoa/sensor_pvlib/ and web/data/manoa_solar_sensor.json.
"""

import sys

from .config import ArrayConfig, OUT_DIR, WEB_JSON, season_labels, season_months
from .export import build_model_payload, build_web_payload, write_hourly_csv, write_json, write_seasonal_csv
from .parse import hourly_means, read_ghi_5min
from .seasonal import seasonal_averages
from .simulate import simulate_power


def main() -> int:
    sys.stdout.reconfigure(errors="replace")
    array = ArrayConfig()
    labels = season_labels()
    months = season_months()

    ghi = read_ghi_5min()
    hourly = hourly_means(ghi)
    if hourly.empty:
        print("Error: no hour in the sensor file has enough 5-minute samples to average.")
        return 1

    hourly = simulate_power(hourly, array)

    coverage = {
        "date_min": str(hourly["date"].min()),
        "date_max": str(hourly["date"].max()),
        "days_with_data": int(hourly["date"].nunique()),
        "hours_with_any_data": sorted(int(h) for h in hourly["hour"].unique()),
        "note": "the logger only ran during expected daylight hours; hours outside hours_with_any_data are real "
        "nighttime absences, not gaps. Some calendar months in 2012 have zero days (see the main README).",
    }
    print(f"[parse]   {coverage['days_with_data']} days with usable hours, {coverage['date_min']} to {coverage['date_max']}")
    print(f"          hours logged: {coverage['hours_with_any_data']}")

    averages = {
        "weekdays": seasonal_averages(hourly, months, weekdays_only=True),
        "all_days": seasonal_averages(hourly, months, weekdays_only=False),
    }
    print("[process] days averaged: " + ", ".join(f"{labels[sid]} {s['days_averaged']}" for sid, s in averages["weekdays"].items()))

    write_json(build_model_payload(array, averages, months, labels, coverage), OUT_DIR / "manoa_sensor_pvlib_model.json")
    write_hourly_csv(hourly, OUT_DIR / "manoa_sensor_pvlib_hourly.csv")
    write_seasonal_csv(averages, labels, OUT_DIR / "manoa_sensor_pvlib_seasonal_averages.csv")
    print(f"[export]  wrote {OUT_DIR}")

    write_json(build_web_payload(array, averages, months, labels, coverage), WEB_JSON)
    print(f"[export]  wrote {WEB_JSON} (the Mānoa page overlays this on the PVWatts estimate)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
