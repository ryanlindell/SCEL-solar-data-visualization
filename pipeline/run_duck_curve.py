"""Run the Tier 2 pipeline: fetch -> cache -> process -> export the modeled duck curve.

    python -m pipeline.run_duck_curve                 # downloads what's missing, rebuilds the JSON
    python -m pipeline.run_duck_curve --skip-fetch    # rebuild the JSON from the cache, no network
    python -m pipeline.run_duck_curve --refresh-data  # download everything again

Run `python -m pipeline.run_pipeline` (Tier 1) first: the duck curve is calibrated to its data.

The NREL inputs are model output for a fixed year, not live measurements, so once cached they never
change and are not downloaded again unless you ask (--refresh-data). The first run downloads about
300 MB of building-load files (they are streamed, and only a few columns are kept).
"""

import argparse
import sys
from contextlib import closing

import requests

from . import cache
from .config import DUCK_CURVE_JSON, RAW_DB, ConfigError, get_api_key, get_nrel_api_key, load_settings
from .export import write_json
from .export_hourly import build_payload
from .fetch import STATE_OPERATIONAL_ROUTE, EIAError, fetch_small_scale_solar
from .fetch_nrel import NRELError, fetch_pvwatts_hourly
from .fetch_oedi import OEDIError, download_building_load, list_building_files
from .process import compute_monthly
from .process_hourly import compute_duck_curve

PVWATTS_ROUTE = "nrel/pvwatts-v8-hourly"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--region", action="append", help="region id (repeatable); default: all with hourly settings")
    parser.add_argument("--skip-fetch", action="store_true", help="don't download; use the cache as it is")
    parser.add_argument("--refresh-data", action="store_true", help="download again even if already cached")
    args = parser.parse_args()
    sys.stdout.reconfigure(errors="replace")

    try:
        settings = load_settings()
        modeled = [r for r, region in settings.regions.items() if region.hourly]
        region_ids = args.region or modeled
        bad = [r for r in region_ids if r not in modeled]
        if bad:
            raise ConfigError(f"No hourly settings for: {', '.join(bad)}. Regions with them: {', '.join(modeled)}")

        hd = settings.hourly_data
        session = requests.Session()

        # 1 + 2. Fetch and cache (only what is missing).
        if not args.skip_fetch:
            api_key = get_nrel_api_key()
            eia_key = get_api_key()
            with closing(cache.connect(RAW_DB)) as conn:
                for region_id in region_ids:
                    region = settings.regions[region_id]
                    model = region.hourly

                    # EIA revises its rooftop-solar estimate, so this small series is always refreshed.
                    print(f"[fetch]   {region.label}: rooftop-solar estimate for {model.state} from EIA ...")
                    counts = cache.upsert_small_scale_solar(
                        conn, region_id, fetch_small_scale_solar(model.state, eia_key, session)
                    )
                    cache.log_fetch(conn, region_id, STATE_OPERATIONAL_ROUTE, counts)
                    print(
                        f"[cache]   {counts['fetched']:,} months: {counts['new']:,} new, {counts['changed']:,} revised"
                    )

                    if args.refresh_data or not cache.has_pvwatts(conn, region_id):
                        print(f"[fetch]   {region.label}: solar profile from NREL PVWatts ...")
                        counts = cache.upsert_pvwatts_hourly(conn, region_id, fetch_pvwatts_hourly(model, api_key, session))
                        cache.log_fetch(conn, region_id, PVWATTS_ROUTE, counts)
                        print(f"[cache]   {counts['fetched']:,} hours: {counts['new']:,} new, {counts['changed']:,} revised")
                    else:
                        print(f"[cache]   {region.label}: solar profile already cached")

                    for sector in ("residential", "commercial"):
                        files = list_building_files(sector, model, hd, session)
                        for i, (building_type, url) in enumerate(files, 1):
                            label = f"{sector} / {building_type} ({i}/{len(files)})"
                            if not args.refresh_data and cache.has_building_load(conn, region_id, sector, building_type):
                                print(f"[cache]   {label}: already cached")
                                continue
                            print(f"[fetch]   {label}: downloading ...", flush=True)
                            rows = download_building_load(sector, url, hd, session)
                            counts = cache.upsert_building_load_hourly(conn, region_id, sector, building_type, rows)
                            cache.log_fetch(conn, region_id, f"oedi/{sector}", counts)

        # 3. Process: cached hourly shapes + Tier 1's actual monthly totals -> seasonal duck curves.
        results = {}
        with closing(cache.connect(RAW_DB)) as conn:
            for region_id in region_ids:
                region = settings.regions[region_id]
                raw = cache.read_facility_fuel(conn, region_id)
                if not raw:
                    raise ConfigError(
                        f"No Tier 1 data cached for '{region_id}'. Run `python -m pipeline.run_pipeline` first."
                    )
                pv_rows = cache.read_pvwatts_hourly(conn, region_id)
                load_rows = cache.read_building_load_hourly(conn, region_id)
                rooftop = cache.read_small_scale_solar(conn, region_id)
                if not pv_rows or not load_rows or not rooftop:
                    raise ConfigError(f"No hourly data cached for '{region_id}'. Run without --skip-fetch first.")

                monthly, _ = compute_monthly(raw, settings.renewable_fuels, settings.storage_fuels)
                model = region.hourly
                result = compute_duck_curve(
                    load_rows,
                    pv_rows,
                    monthly,
                    rooftop,
                    model.share_of_state,
                    model.rooftop_share_of_state,
                    {sid: s.months for sid, s in hd.seasons.items()},
                    model.reference_year,
                )
                results[region_id] = result
                cal = result["calibration"]
                print(
                    f"[process] {region.label}: calibrated to {result['reference_year']} EIA generation; "
                    f"rooftop solar {cal['rooftop_solar_gwh_per_year']:,.0f} GWh/yr, "
                    f"utility solar {cal['utility_solar_gwh_per_year']:,.0f} GWh/yr"
                )
                for sid, season in result["seasons"].items():
                    st = season["stats"]
                    print(
                        f"          {hd.seasons[sid].label:<6} midday low {st['midday_low_mw']:,.0f} MW "
                        f"at hour {st['midday_low_hour']}, evening peak {st['evening_peak_mw']:,.0f} MW "
                        f"at hour {st['evening_peak_hour']}"
                    )

        # 4. Export.
        write_json(build_payload(settings, results), DUCK_CURVE_JSON)
        print(f"[export]  wrote {DUCK_CURVE_JSON}")
    except (ConfigError, EIAError, NRELError, OEDIError, ValueError) as exc:
        print(f"Error: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
