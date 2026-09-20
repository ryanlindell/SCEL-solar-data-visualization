"""Run the whole pipeline: fetch -> cache -> process -> export.

    python -m pipeline.run_pipeline                 # refresh everything
    python -m pipeline.run_pipeline --region oahu   # one region only
    python -m pipeline.run_pipeline --skip-fetch    # rebuild the JSON from the cache, no network

Safe to run as often as you like: the cache upserts rather than appends, and the JSON file
is rewritten in full every time.
"""

import argparse
import sys
from contextlib import closing

from . import cache
from .config import OUTPUT_JSON, RAW_DB, ConfigError, get_api_key, load_settings
from .export import build_payload, write_json
from .fetch import FACILITY_FUEL_ROUTE, STATE_OPERATIONAL_ROUTE, EIAError, fetch_facility_fuel, fetch_small_scale_solar
from .process import add_rooftop_estimate, compute_monthly
from .process_curtailment import build_quarters


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--region", action="append", help="region id (repeatable); default: all regions")
    parser.add_argument("--skip-fetch", action="store_true", help="don't call the API; use the cache as it is")
    args = parser.parse_args()
    sys.stdout.reconfigure(errors="replace")  # "Oʻahu" must not crash a legacy Windows console

    try:
        settings = load_settings()
        region_ids = args.region or list(settings.regions)
        unknown = [r for r in region_ids if r not in settings.regions]
        if unknown:
            raise ConfigError(f"Unknown region(s): {', '.join(unknown)}. Known: {', '.join(settings.regions)}")

        # 1 + 2. Fetch from EIA and store the raw rows in the cache.
        if not args.skip_fetch:
            api_key = get_api_key()
            with closing(cache.connect(RAW_DB)) as conn:
                for region_id in region_ids:
                    region = settings.regions[region_id]
                    print(f"[fetch]   {region.label}: requesting {len(region.plants)} plants from EIA ...")
                    rows = fetch_facility_fuel(region, api_key)
                    counts = cache.upsert_facility_fuel(conn, region_id, FACILITY_FUEL_ROUTE, rows)
                    print(
                        f"[cache]   {counts['fetched']:,} rows fetched: "
                        f"{counts['new']:,} new, {counts['changed']:,} revised, "
                        f"{counts['fetched'] - counts['new'] - counts['changed']:,} unchanged"
                    )
                    if region.rooftop_share_of_state is not None:
                        # EIA's statewide rooftop-solar estimate: used for the chart's dotted "including rooftop" line.
                        print(f"[fetch]   {region.label}: rooftop-solar estimate for {region.state} ...")
                        counts = cache.upsert_small_scale_solar(conn, region_id, fetch_small_scale_solar(region.state, api_key))
                        cache.log_fetch(conn, region_id, STATE_OPERATIONAL_ROUTE, counts)
                        print(f"[cache]   {counts['fetched']:,} months: {counts['new']:,} new, {counts['changed']:,} revised")

        # 3. Process: raw rows -> monthly renewable share.
        results = {}
        with closing(cache.connect(RAW_DB)) as conn:
            for region_id in region_ids:
                raw = cache.read_facility_fuel(conn, region_id)
                if not raw:
                    raise ConfigError(
                        f"The cache has no data for '{region_id}'. Run without --skip-fetch first."
                    )
                monthly, final_through = compute_monthly(raw, settings.renewable_fuels, settings.storage_fuels)
                region = settings.regions[region_id]
                rooftop = cache.read_small_scale_solar(conn, region_id)
                # If Tier 3's data is cached, use the utility's own reported rooftop totals to size the estimate.
                reported = {
                    q["period"]: q["distributed_mwh"]
                    for q in build_quarters(cache.read_heco_curtailment(conn, region_id))
                    if q["distributed_mwh"] is not None
                }
                monthly = add_rooftop_estimate(monthly, rooftop, region.rooftop_share_of_state, reported)
                results[region_id] = (monthly, final_through)
                latest = monthly[-1]
                with_rooftop = [m for m in monthly if m["rooftop_solar_mwh_est"] is not None]
                print(
                    f"[process] {region.label}: {len(monthly)} months, "
                    f"{monthly[0]['period']} to {latest['period']}; "
                    f"complete through {final_through or latest['period']}"
                )
                if with_rooftop:
                    measured = sum(1 for m in with_rooftop if m["rooftop_basis"] == "measured")
                    print(
                        f"          rooftop-solar estimate for {len(with_rooftop)} months ({with_rooftop[0]['period']} on): "
                        f"{measured} sized from Hawaiian Electric's reported totals, {len(with_rooftop) - measured} at an assumed "
                        f"{region.rooftop_share_of_state:.0%} of the state total"
                    )
                    if measured == 0:
                        print("          (run `python -m pipeline.run_curtailment` first to use Hawaiian Electric's reported totals instead)")
                elif region.rooftop_share_of_state is not None:
                    print("          no rooftop-solar estimate cached; run without --skip-fetch to add it")

        # 4. Export the static JSON the web page loads.
        write_json(build_payload(settings, results), OUTPUT_JSON)
        print(f"[export]  wrote {OUTPUT_JSON}")
    except (ConfigError, EIAError) as exc:
        print(f"Error: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
