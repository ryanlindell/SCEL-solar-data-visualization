"""Write the power-plant timeline and battery fleet to the static JSON file the web pages read."""

from datetime import datetime, timezone

from .config import Settings
from .process_plants import FUEL_GROUPS


def build_payload(settings: Settings, results: dict[str, dict]) -> dict:
    """results maps region id -> {"plants", "battery_fleet", "source_file", "as_of", "warnings"}."""
    regions = {}
    plants = []
    for region_id, r in results.items():
        region = settings.regions[region_id]
        units = [u for p in r["plants"] for u in p["units"]]
        first_online = min(u["online"] for u in units if not u["planned"])
        planned = [u["online"] for u in units if u["planned"]]
        regions[region_id] = {
            "label": region.label,
            "first_month": first_online,  # the earliest unit in service (the map's slider starts here)
            "as_of": r["as_of"],  # the month of the EIA file: after this the map shows plans, not records
            "last_planned_month": max(planned) if planned else None,  # the furthest expected in-service month (the slider ends here)
            "battery_fleet": r["battery_fleet"],
        }
        plants.extend(r["plants"])

    return {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": "U.S. Energy Information Administration, Form EIA-860M (monthly generator inventory)",
            "source_files": {rid: r["source_file"] for rid, r in results.items()},
            "groups": [{"key": key, "label": label} for key, label, _ in FUEL_GROUPS] + [{"key": "other", "label": "Other"}],
            "warnings": [w for r in results.values() for w in r["warnings"]],
        },
        "regions": regions,
        "plants": plants,
    }
