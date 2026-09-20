"""Write the derived monthly data to the static JSON file the web page reads."""

import json
from datetime import datetime, timezone
from pathlib import Path

from .config import Region, Settings


def build_payload(
    settings: Settings,
    results: dict[str, tuple[list[dict], str | None]],
) -> dict:
    """results maps region id -> (monthly rows, final_through)."""
    regions = {}
    rows = []
    for region_id, (monthly, final_through) in results.items():
        region: Region = settings.regions[region_id]
        regions[region_id] = {
            "label": region.label,
            "state": region.state,
            "county": region.county,
            "plants_in_definition": len(region.plants),
            "final_through": final_through,
            # The rooftop line is an estimate: EIA's statewide figure times this share (None = no rooftop line).
            "rooftop_share_of_state": region.rooftop_share_of_state,
            "rooftop_estimate_from": next(
                (m["period"] for m in monthly if m.get("rooftop_solar_mwh_est") is not None), None
            ),
        }
        for m in monthly:
            row = dict(m)
            if row.get("rooftop_solar_mwh_est") is not None:
                row["rooftop_solar_mwh_est"] = round(row["rooftop_solar_mwh_est"], 1)
            if row.get("renewable_share_incl_rooftop_pct") is not None:
                row["renewable_share_incl_rooftop_pct"] = round(row["renewable_share_incl_rooftop_pct"], 3)
            if row.get("rooftop_share_used") is not None:
                row["rooftop_share_used"] = round(row["rooftop_share_used"], 4)
            if row["renewable_share_pct"] is not None:
                row["renewable_share_pct"] = round(row["renewable_share_pct"], 3)
            row["total_mwh"] = round(row["total_mwh"], 1)
            row["renewable_mwh"] = round(row["renewable_mwh"], 1)
            row["generation_by_fuel_mwh"] = {k: round(v, 1) for k, v in row["generation_by_fuel_mwh"].items()}
            rows.append(row)

    return {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": "U.S. Energy Information Administration, Open Data API v2, route electricity/facility-fuel (Form EIA-923); "
            "rooftop-solar line: EIA's estimated small-scale solar for the state (electricity/electric-power-operational-data)",
            "units": "MWh",
            "renewable_fuels": list(settings.renewable_fuels),
            "excluded_from_total": list(settings.storage_fuels),
        },
        "regions": regions,
        "rows": rows,
    }


def write_json(payload: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
        f.write("\n")
