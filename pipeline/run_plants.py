"""Run the power-plant pipeline: fetch -> cache -> process -> export the plant timeline and battery fleet.

    python -m pipeline.run_plants                 # download the newest EIA-860M file, rebuild the JSON
    python -m pipeline.run_plants --skip-fetch    # rebuild the JSON from the cache, no network

No API key is needed: EIA publishes the workbook (about 14 MB) publicly. It is refreshed monthly, and a rerun on
the same file changes nothing. This feeds the map page (plants appearing and disappearing over time) and the
battery page (its default size is Oʻahu's battery fleet).
"""

import argparse
import sys
from contextlib import closing

import requests

from . import cache
from .config import PLANTS_JSON, RAW_DB, ConfigError, load_settings
from .export import write_json
from .export_plants import build_payload
from .fetch_eia860m import Eia860Error, fetch_generator_inventory
from .process_plants import battery_fleet, build_plants

SOURCE_NAME = "eia.gov/electricity/data/eia860m"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--region", action="append", help="region id (repeatable); default: all regions")
    parser.add_argument("--skip-fetch", action="store_true", help="don't download; use the cache as it is")
    args = parser.parse_args()
    sys.stdout.reconfigure(errors="replace")

    try:
        settings = load_settings()
        region_ids = args.region or list(settings.regions)
        unknown = [r for r in region_ids if r not in settings.regions]
        if unknown:
            raise ConfigError(f"Unknown region(s): {', '.join(unknown)}. Known: {', '.join(settings.regions)}")

        # 1 + 2. Fetch EIA's newest monthly generator inventory and store this region's units.
        if not args.skip_fetch:
            session = requests.Session()
            with closing(cache.connect(RAW_DB)) as conn:
                for region_id in region_ids:
                    region = settings.regions[region_id]
                    print(f"[fetch]   {region.label}: looking for the newest EIA-860M workbook (about 14 MB) ...")
                    inventory = fetch_generator_inventory(region, settings.eia860m, session)
                    counts = cache.replace_generators(conn, region_id, inventory)
                    cache.log_fetch(conn, region_id, SOURCE_NAME, counts)
                    print(
                        f"[cache]   {inventory['source_file']}: {counts['fetched']:,} units for {region.label}: "
                        f"{counts['new']:,} new, {counts['changed']:,} changed"
                    )

        # 3. Process: units -> plants with in-service and retirement dates, plus the battery fleet.
        results = {}
        with closing(cache.connect(RAW_DB)) as conn:
            for region_id in region_ids:
                region = settings.regions[region_id]
                inventory = cache.read_generators(conn, region_id)
                if not inventory["rows"]:
                    raise ConfigError(f"No generator inventory cached for '{region_id}'. Run without --skip-fetch first.")
                plants, warnings = build_plants(inventory["rows"], region, settings.eia860m.corrections)
                fleet = battery_fleet(plants)
                results[region_id] = {
                    "plants": plants,
                    "battery_fleet": fleet,
                    "source_file": inventory["source_file"],
                    "as_of": inventory["as_of"],
                    "warnings": warnings,
                }
                retired = sum(1 for p in plants for u in p["units"] if u["retired"])
                planned = [u["online"] for p in plants for u in p["units"] if u["planned"]]
                print(
                    f"[process] {region.label}: {len(plants)} plants, {sum(len(p['units']) for p in plants)} units "
                    f"({retired} retired, {len(planned)} planned, the last expected {max(planned) if planned else 'never'}), "
                    f"as of {inventory['as_of']}"
                )
                print(
                    f"          battery fleet: {fleet['power_mw']:,.1f} MW / {fleet['energy_mwh']:,.1f} MWh "
                    f"({fleet['energy_mwh_as_filed']:,.1f} MWh as EIA filed it) in {len(fleet['units'])} units"
                )
                for w in warnings:
                    print(f"[warning] {w}")

        # 4. Export.
        write_json(build_payload(settings, results), PLANTS_JSON)
        print(f"[export]  wrote {PLANTS_JSON}")
    except (ConfigError, Eia860Error) as exc:
        print(f"Error: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
