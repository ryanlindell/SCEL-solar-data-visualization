"""Run the Tier 3 pipeline: fetch -> cache -> process -> export the reported-curtailment data.

    python -m pipeline.run_curtailment                 # download Hawaiian Electric's files, rebuild the JSON
    python -m pipeline.run_curtailment --skip-fetch    # rebuild the JSON from the cache, no network

No API key is needed: the numbers come from two public Excel files (about 200 KB in total), which
Hawaiian Electric updates every quarter and sometimes corrects, so they are downloaded on every run.

If Tier 1 (and Tier 2's rooftop estimate) are in the cache, this also prints cross-checks against
EIA's numbers. They are informational and never change the output.
"""

import argparse
import sys
from contextlib import closing

import requests

from . import cache
from .config import CURTAILMENT_JSON, RAW_DB, ConfigError, load_settings
from .export import write_json
from .export_curtailment import build_payload
from .fetch_heco import HecoError, fetch_curtailment
from .process import compute_monthly
from .process_curtailment import (
    build_annual,
    build_quarters,
    implied_share_of_state,
    reconcile_with_eia,
    summarize,
)

SOURCE_NAME = "hawaiianelectric.com/curtailment"


def _print_checks(conn, region, annual: list[dict], settings) -> None:
    """Cross-check Hawaiian Electric's numbers against EIA's (informational only)."""
    raw = cache.read_facility_fuel(conn, region.id)
    if raw:
        monthly, _ = compute_monthly(raw, settings.renewable_fuels, settings.storage_fuels)
        rows = reconcile_with_eia(annual, monthly)[-4:]
        if rows:
            print("[check]   Hawaiian Electric 'delivered' wind+solar vs EIA (Tier 1) solar+wind:")
            for r in rows:
                print(
                    f"          {r['year']}: {r['heco_delivered_mwh'] / 1000:6.0f} GWh vs {r['eia_solar_wind_mwh'] / 1000:6.0f} GWh"
                    f"  (ratio {r['ratio']:.2f})"
                )
    rooftop = cache.read_small_scale_solar(conn, region.id)
    if rooftop:
        configured = f"; config/regions.yaml uses {region.hourly.rooftop_share_of_state:.2f}" if region.hourly else ""
        print(f"[check]   {region.label}'s share of the state's rooftop solar (Hawaiian Electric's total / EIA's statewide estimate){configured}:")
        shown = 0
        for a in reversed(annual):
            share = implied_share_of_state(annual, rooftop, a["year"])
            if share is None:
                continue
            statewide = sum(v for p, v in rooftop.items() if p.startswith(f"{a['year']}-"))
            print(f"          {a['year']}: {a['distributed_mwh'] / 1000:,.0f} / {statewide / 1000:,.0f} GWh = {share:.2f}")
            shown += 1
            if shown == 3:
                break


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--region", action="append", help="region id (repeatable); default: all with curtailment settings")
    parser.add_argument("--skip-fetch", action="store_true", help="don't download; use the cache as it is")
    args = parser.parse_args()
    sys.stdout.reconfigure(errors="replace")

    try:
        settings = load_settings()
        available = [r for r, region in settings.regions.items() if region.curtailment]
        region_ids = args.region or available
        bad = [r for r in region_ids if r not in available]
        if bad:
            raise ConfigError(f"No curtailment settings for: {', '.join(bad)}. Regions with them: {', '.join(available)}")

        # 1 + 2. Fetch from Hawaiian Electric and store the raw numbers.
        if not args.skip_fetch:
            session = requests.Session()
            with closing(cache.connect(RAW_DB)) as conn:
                for region_id in region_ids:
                    region = settings.regions[region_id]
                    print(f"[fetch]   {region.label}: downloading Hawaiian Electric's curtailment workbooks ...")
                    fetched = fetch_curtailment(region.curtailment, settings.heco, session)
                    counts = cache.upsert_heco_curtailment(conn, region_id, fetched)
                    cache.log_fetch(conn, region_id, SOURCE_NAME, counts)
                    print(
                        f"[cache]   {counts['fetched']:,} numbers: {counts['new']:,} new, {counts['changed']:,} revised, "
                        f"{counts['fetched'] - counts['new'] - counts['changed']:,} unchanged"
                    )

        # 3. Process: raw numbers -> quarterly and annual series.
        results = {}
        with closing(cache.connect(RAW_DB)) as conn:
            for region_id in region_ids:
                region = settings.regions[region_id]
                raw = cache.read_heco_curtailment(conn, region_id)
                if not raw:
                    raise ConfigError(f"No curtailment data cached for '{region_id}'. Run without --skip-fetch first.")
                quarters = build_quarters(raw)
                annual = build_annual(quarters)
                summary = summarize(quarters, annual)
                results[region_id] = {"quarters": quarters, "annual": annual, "summary": summary}

                latest, peak = summary["latest_full_year"], summary["peak_year"]
                print(
                    f"[process] {region.label}: {len(quarters)} quarters, {summary['first_quarter']} to {summary['latest_quarter']}; "
                    f"{latest['year']}: {latest['curtailed_mwh'] / 1000:,.1f} GWh curtailed = {latest['curtailment_pct']:.1f}% of potential; "
                    f"worst year {peak['year']}: {peak['curtailed_mwh'] / 1000:,.1f} GWh ({peak['curtailment_pct']:.1f}%)"
                )
                _print_checks(conn, region, annual, settings)

        # 4. Export.
        write_json(build_payload(settings, results), CURTAILMENT_JSON)
        print(f"[export]  wrote {CURTAILMENT_JSON}")
    except (ConfigError, HecoError, ValueError) as exc:
        print(f"Error: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
