"""Model one site's solar irradiance and power with NREL PVWatts (default site: UH Mānoa), separate from the duck curve.

    python -m pipeline.run_site_solar                     # site 'manoa': fetch if needed, then export
    python -m pipeline.run_site_solar --skip-fetch        # rebuild the exports from the cache, no network
    python -m pipeline.run_site_solar --refresh-data      # ask NREL again even if cached
    python -m pipeline.run_site_solar --site <id>         # another entry of `pvwatts_sites` in config/regions.yaml

Fetch -> cache (its own tables; the Oʻahu rows are never touched) -> check the series is physically plausible ->
seasonal typical days -> data/<site id>/, plus a compact web/data/<site id>_solar.json for that site's own page. The
Oʻahu duck-curve files and every other page's data are never read or written.

Unlike the Oʻahu solar profile (reused until --refresh-data), a site's cached copy is refetched automatically when its
settings in regions.yaml change (tilt, coordinates, ...), because the cached hours would otherwise answer an old question.
"""

import argparse
import sys
from contextlib import closing

import requests

from . import cache
from .config import RAW_DB, SITE_SOLAR_DIR, SITE_WEB_DIR, ConfigError, get_nrel_api_key, load_settings
from .export import write_json
from .export_site_solar import build_payload, build_web_payload, write_hourly_csv, write_seasonal_csv
from .fetch_nrel import NRELError, fetch_pvwatts_site, site_request
from .process_site_solar import annual_summary, check_plausible, seasonal_averages

SITE_ROUTE = "nrel/pvwatts-v8-site-hourly"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--site", default="manoa", help="id of a pvwatts_sites entry (default: manoa)")
    parser.add_argument("--skip-fetch", action="store_true", help="don't download; use the cache as it is")
    parser.add_argument("--refresh-data", action="store_true", help="download again even if already cached")
    args = parser.parse_args()
    sys.stdout.reconfigure(errors="replace")

    try:
        settings = load_settings()
        if args.site not in settings.pvwatts_sites:
            raise ConfigError(
                f"No site '{args.site}' under pvwatts_sites in config/regions.yaml. Sites: {', '.join(settings.pvwatts_sites) or 'none'}"
            )
        site = settings.pvwatts_sites[args.site]
        request = site_request(site)
        tilt = float(site.pv_system["tilt"])
        if site.placeholder:
            print(f"[note]    {site.label}: the coordinates are a PLACEHOLDER for the real sensor's; replace lat/lon in regions.yaml.")

        # 1. Fetch and cache.
        if not args.skip_fetch:
            with closing(cache.connect(RAW_DB)) as conn:
                reason = "refresh requested" if args.refresh_data else cache.pvwatts_site_stale_reason(conn, site.id, request)
                if reason is None:
                    print(f"[cache]   {site.label}: already cached with these settings")
                else:
                    print(f"[fetch]   {site.label}: PVWatts request ({reason}) ...")
                    fetched = fetch_pvwatts_site(site, get_nrel_api_key(), requests.Session())
                    counts = cache.replace_pvwatts_site(conn, site.id, site.label, fetched)
                    cache.log_fetch(conn, site.id, SITE_ROUTE, counts)
                    print(f"[cache]   {counts['fetched']:,} hours: {counts['new']:,} new, {counts['changed']:,} revised")
                    if not fetched["station_info"]:
                        print("[warning] NREL returned no station_info, so the matched weather cell is unknown.")

        # 2. Process.
        with closing(cache.connect(RAW_DB)) as conn:
            meta = cache.read_pvwatts_site(conn, site.id)
            if meta is None:
                raise ConfigError(f"No PVWatts data cached for site '{site.id}'. Run without --skip-fetch first.")
            stale = cache.pvwatts_site_stale_reason(conn, site.id, request)
            if stale:
                raise ConfigError(
                    f"The cached data for '{site.id}' does not match its settings ({stale}). Run without --skip-fetch."
                )
            rows = cache.read_pvwatts_site_hourly(conn, site.id)

        problems = check_plausible(rows, tilt)
        if problems:
            raise ConfigError(
                "The PVWatts series failed its plausibility checks, so nothing was exported:\n  - " + "\n  - ".join(problems)
            )

        seasons = settings.hourly_data.seasons
        months = {sid: s.months for sid, s in seasons.items()}
        averages = {
            "weekdays": seasonal_averages(rows, months, weekdays_only=True),
            "all_days": seasonal_averages(rows, months, weekdays_only=False),
        }
        summary = annual_summary(rows)
        st = meta["station_info"]
        print(
            f"[process] matched NSRDB cell {st.get('location')} at {st.get('lat')}, {st.get('lon')}: "
            f"{st.get('distance')} m from the query point ({meta['query_lat']}, {meta['query_lon']}); "
            f"source {st.get('weather_data_source')}"
        )
        print(
            f"          {summary['annual_poa_kwh_per_m2']:,.0f} kWh/m2 a year on the plane (tilt {site.pv_system['tilt']}), "
            f"peak {summary['peak_poa_wm2']:,.0f} W/m2"
        )
        print("          weekdays averaged: " + ", ".join(f"{seasons[sid].label} {s['days_averaged']}" for sid, s in averages["weekdays"].items()))

        # 3. Export.
        out = SITE_SOLAR_DIR / site.id
        write_json(build_payload(site, meta, summary, averages, seasons), out / f"{site.id}_pvwatts_model.json")
        write_hourly_csv(rows, out / f"{site.id}_pvwatts_hourly.csv")
        write_seasonal_csv(averages, seasons, out / f"{site.id}_pvwatts_seasonal_averages.csv")
        print(f"[export]  wrote {out}: {site.id}_pvwatts_model.json, {site.id}_pvwatts_hourly.csv, {site.id}_pvwatts_seasonal_averages.csv")
        page_json = SITE_WEB_DIR / f"{site.id}_solar.json"
        write_json(build_web_payload(site, meta, summary, averages, seasons), page_json)
        print(f"[export]  wrote {page_json} (the site's own page reads this)")
    except (ConfigError, NRELError) as exc:
        print(f"Error: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
