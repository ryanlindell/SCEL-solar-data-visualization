"""Helper for keeping config/regions.yaml complete.

Lists plants that EIA's plant registry (EIA-860) places in a region's county but that are not
yet in the region's `plants` or `excluded_plants` list - e.g. a new solar farm. Also the tool
to use when defining a brand-new region.

    python -m pipeline.discover_plants --region oahu
"""

import argparse
import sys

from .config import ConfigError, get_api_key, load_settings
from .fetch import EIAError, GENERATOR_CAPACITY_ROUTE, get_all_rows, get_latest_period


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--region", required=True, help="region id from config/regions.yaml")
    args = parser.parse_args()
    sys.stdout.reconfigure(errors="replace")  # plant names may contain characters a legacy console can't show

    try:
        settings = load_settings()
        if args.region not in settings.regions:
            raise ConfigError(f"Unknown region {args.region!r}. Known: {', '.join(settings.regions)}")
        region = settings.regions[args.region]
        api_key = get_api_key()

        period = get_latest_period(GENERATOR_CAPACITY_ROUTE, api_key)
        rows = get_all_rows(
            GENERATOR_CAPACITY_ROUTE,
            [
                ("frequency", "monthly"),
                ("data[0]", "county"),
                ("data[1]", "nameplate-capacity-mw"),
                ("facets[stateid][]", region.state),
                ("start", period),
                ("end", period),
            ],
            api_key,
        )
    except (ConfigError, EIAError) as exc:
        print(f"Error: {exc}")
        return 1

    known = set(region.plants) | set(region.excluded_plants)
    in_county: dict[str, dict] = {}
    for r in rows:
        if (r.get("county") or "").strip().lower() != region.county.lower():
            continue
        plant = in_county.setdefault(
            r["plantid"], {"name": r["plantName"], "sector": r["sectorName"], "owner": r["entityName"], "mw": 0.0}
        )
        plant["mw"] += float(r["nameplate-capacity-mw"] or 0)

    new = {code: p for code, p in in_county.items() if code not in known}
    print(f"EIA-860 registry as of {period}: {len(in_county)} plants in {region.county} County, {region.state}.")
    if not new:
        print(f"All are already listed in config/regions.yaml for '{region.id}'. Nothing to add.")
        return 0

    print(f"\n{len(new)} not yet in config/regions.yaml. Add each to `plants:` (or `excluded_plants:`):\n")
    for code, p in sorted(new.items(), key=lambda kv: kv[1]["name"]):
        print(f'  - {{code: "{code}", name: "{p["name"]}"}}   # {p["sector"]}, {p["owner"]}, {p["mw"]:.1f} MW')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
