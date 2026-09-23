"""Build the NSRDB 2012 estimate end to end.

    python -m nsrdb_model.run                  # download once (then reuse the saved file), check, simulate, export
    python -m nsrdb_model.run --refresh-data   # download again even if a saved file exists

Needs NREL_API_KEY and NSRDB_EMAIL in .env for the first download only. Reads nothing from pipeline/; uses
sensor_model's array, pvlib power model and seasonal averaging unchanged. Writes data/manoa/nsrdb_pvlib/ and
web/data/manoa_solar_nsrdb.json.
"""

import argparse
import json
import sys

from sensor_model.seasonal import seasonal_averages
from sensor_model.simulate import simulate_power

from .config import RAW_CSV, OUT_DIR, WEB_JSON, YEAR, ArrayConfig, NsrdbError, credentials, season_labels, season_months, site_coordinates
from .export import build_model_payload, build_web_payload, write_hourly_csv, write_json, write_seasonal_csv
from .fetch import download_csv, request_params
from .parse import check_plausible, hourly_frame, read_metadata

REQUEST_FILE = RAW_CSV.with_suffix(".request.json")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--refresh-data", action="store_true", help="download again even if a saved file exists")
    args = parser.parse_args()
    sys.stdout.reconfigure(errors="replace")

    try:
        lat, lon = site_coordinates()
        request = request_params(lat, lon)
        saved = json.loads(REQUEST_FILE.read_text(encoding="utf-8")) if REQUEST_FILE.exists() else None
        if args.refresh_data or not RAW_CSV.exists() or saved != request:
            reason = "refresh requested" if args.refresh_data else ("not downloaded yet" if not RAW_CSV.exists() else "request settings changed")
            print(f"[fetch]   NSRDB {YEAR} at {lat}, {lon} ({reason}) ...")
            key, email = credentials()
            text = download_csv(lat, lon, key, email)
            RAW_CSV.parent.mkdir(parents=True, exist_ok=True)
            RAW_CSV.write_text(text, encoding="utf-8")
            REQUEST_FILE.write_text(json.dumps(request, indent=1) + "\n", encoding="utf-8")
        else:
            print(f"[cache]   using the saved download {RAW_CSV.name}")
        text = RAW_CSV.read_text(encoding="utf-8")

        meta = read_metadata(text)
        hourly = hourly_frame(text)
        problems = check_plausible(hourly, meta)
        if problems:
            raise NsrdbError("The NSRDB year failed its plausibility checks, so nothing was exported:\n  - " + "\n  - ".join(problems))
        print(f"[parse]   {len(hourly):,} hours; grid cell {meta['location_id']} at {meta['cell_lat']}, {meta['cell_lon']}; "
              f"{hourly['poa_wm2'].sum() / 1000:,.0f} kWh/m2 of sunshine in {YEAR}")

        array = ArrayConfig()
        hourly = simulate_power(hourly, array)
        months, labels = season_months(), season_labels()
        averages = {
            "weekdays": seasonal_averages(hourly, months, weekdays_only=True),
            "all_days": seasonal_averages(hourly, months, weekdays_only=False),
        }
        print("[process] days averaged (weekdays): " + ", ".join(f"{labels[sid]} {s['days_averaged']}" for sid, s in averages["weekdays"].items()))
        summary = {
            "hours": len(hourly),
            "annual_ghi_kwh_per_m2": float(hourly["poa_wm2"].sum() / 1000),
            "peak_ghi_wm2": float(hourly["poa_wm2"].max()),
            "annual_dc_mwh": float(hourly["dc_w"].sum() / 1e6),
            "annual_ac_mwh": float(hourly["ac_w"].sum() / 1e6),
            "ac_max_w": float(hourly["ac_w"].max()),
        }

        write_json(build_model_payload(array, averages, months, labels, meta, request, summary), OUT_DIR / "manoa_nsrdb_pvlib_model.json")
        write_hourly_csv(hourly, OUT_DIR / "manoa_nsrdb_pvlib_hourly.csv")
        write_seasonal_csv(averages, labels, OUT_DIR / "manoa_nsrdb_pvlib_seasonal_averages.csv")
        print(f"[export]  wrote {OUT_DIR}")
        write_json(build_web_payload(array, averages, months, labels, meta, summary), WEB_JSON)
        print(f"[export]  wrote {WEB_JSON}")
    except NsrdbError as exc:
        print(f"Error: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
