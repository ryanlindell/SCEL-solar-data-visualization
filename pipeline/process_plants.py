"""Turn EIA's generator inventory into a timeline of power plants, and the current battery fleet.
Pure functions: rows in, dicts out - no network, files or database.

A "unit" is one generator (EIA calls it a generator; Kahe's boilers K1-K6 are six units). Each has a size in
megawatts, a fuel/technology, and the month it entered service and (if it has) the month it was retired. A plant
is a group of units at one site, so a plant's size at any date is the sum of its units that were in service then.
"""

from .config import Eia860Correction, Region

# EIA "Technology" -> the group shown on the map. Anything not listed falls into "other".
FUEL_GROUPS = (
    ("solar", "Solar", ("solar",)),
    ("wind", "Wind", ("wind",)),
    ("battery", "Battery storage", ("batter",)),
    ("waste", "Waste & biomass", ("municipal solid waste", "landfill gas", "biomass", "wood")),
    ("oil", "Oil", ("petroleum",)),
    ("coal", "Coal", ("coal",)),
)


def fuel_group(technology: str) -> str:
    """The map group for an EIA technology name, e.g. 'Petroleum Liquids' -> 'oil'."""
    t = (technology or "").lower()
    for key, _label, needles in FUEL_GROUPS:
        if any(n in t for n in needles):
            return key
    return "other"


def _month(year: int | None, month: int | None) -> str | None:
    """'YYYY-MM'. EIA occasionally omits the month; January is used then (the year is what matters)."""
    if year is None:
        return None
    return f"{year:04d}-{(month or 1):02d}"


def _status_text(status: str) -> str:
    """EIA's status without its code: '(V) Under construction, more than 50 percent complete' -> 'Under construction, ...'."""
    text = (status or "").strip()
    return text.split(") ", 1)[1] if text.startswith("(") and ") " in text else text


def apply_corrections(units: list[dict], corrections: tuple[Eia860Correction, ...]) -> list[str]:
    """Replace known-wrong values, in place. Returns warnings for corrections that could not be applied.

    A correction applies only while EIA still publishes exactly the wrong value it was written for.
    If EIA has since changed the number, nothing is overridden and a warning asks for a review.
    """
    warnings = []
    for c in corrections:
        targets = [u for u in units if u["plant_code"] == c.plant_code and u["id"] == c.generator_id and u["retired"] is None and not u["planned"]]
        if not targets:
            warnings.append(f"Correction for plant {c.plant_code} unit {c.generator_id} matched no operating unit; it can be removed.")
            continue
        for u in targets:
            filed = u["mwh"]
            if filed is not None and abs(filed - c.filed_value) < 1e-9:
                u["corrections"].append(
                    {"field": c.field, "as_filed": filed, "used": c.value, "reason": c.reason, "source": c.source}
                )
                u["mwh"] = c.value
            else:
                warnings.append(
                    f"EIA now lists {u['plant_name']} unit {c.generator_id} at {filed} MWh, not the {c.filed_value} MWh "
                    f"the correction was written for; the correction was NOT applied. Review config/regions.yaml."
                )
    return warnings


def build_plants(rows: list[dict], region: Region, corrections: tuple[Eia860Correction, ...] = ()) -> tuple[list[dict], list[str]]:
    """Cached inventory rows for one region -> (plants, warnings).

    Units with no in-service year are left out. Each plant carries its units with 'online' and 'retired' months,
    so a page can work out what existed on any date. A planned unit (from the workbook's Planned sheet) has
    planned=True, its 'online' is the operator's expected month, and 'status' is EIA's stage of progress.
    """
    units = []
    for r in rows:
        online = _month(r["online_year"], r["online_month"])
        if online is None or r["nameplate_mw"] is None:
            continue
        units.append(
            {
                "plant_code": r["plant_code"],
                "plant_name": r["plant_name"],
                "owner": r["owner"],
                "sector": r["sector"],
                "lat": r["lat"],
                "lon": r["lon"],
                "id": r["generator_id"],
                "technology": r["technology"],
                "group": fuel_group(r["technology"]),
                "mw": r["nameplate_mw"],
                "mwh": r["energy_mwh"],
                "online": online,
                "retired": _month(r["retired_year"], r["retired_month"]) if r["sheet"] == "retired" else None,
                "planned": r["sheet"] == "planned",
                "status": _status_text(r["status"]) if r["sheet"] == "planned" else None,
                "corrections": [],
            }
        )
    warnings = apply_corrections(units, corrections)

    by_plant: dict[str, list[dict]] = {}
    for u in units:
        by_plant.setdefault(u["plant_code"], []).append(u)

    plants = []
    for code, us in by_plant.items():
        first = us[0]
        coords = next(((u["lat"], u["lon"]) for u in us if u["lat"] is not None and u["lon"] is not None), (None, None))
        plants.append(
            {
                "region": region.id,
                "code": code,
                "name": first["plant_name"],
                "owner": first["owner"],
                "sector": first["sector"],
                "lat": coords[0],
                "lon": coords[1],
                "counted_in_share_chart": code in region.plants,
                "excluded_reason": region.excluded_plants.get(code),
                "units": [
                    {k: u[k] for k in ("id", "technology", "group", "mw", "mwh", "online", "retired", "planned", "status", "corrections")}
                    for u in sorted(us, key=lambda u: (u["online"], u["id"]))
                ],
            }
        )
    plants.sort(key=lambda p: (min(u["online"] for u in p["units"]), p["name"]))
    return plants, warnings


def battery_fleet(plants: list[dict]) -> dict:
    """The batteries in service now (not retired, not merely planned): total power, total energy, and each unit."""
    fleet = []
    for p in plants:
        for u in p["units"]:
            if u["group"] == "battery" and u["retired"] is None and not u["planned"]:
                fleet.append(
                    {
                        "plant": p["name"],
                        "plant_code": p["code"],
                        "unit": u["id"],
                        "online": u["online"],
                        "power_mw": u["mw"],
                        "energy_mwh": u["mwh"],
                        "energy_mwh_as_filed": u["corrections"][0]["as_filed"] if u["corrections"] else u["mwh"],
                        "corrected": bool(u["corrections"]),
                    }
                )
    known = [b for b in fleet if b["energy_mwh"] is not None]
    return {
        "power_mw": sum(b["power_mw"] for b in fleet),
        "energy_mwh": sum(b["energy_mwh"] for b in known),
        "energy_mwh_as_filed": sum(b["energy_mwh_as_filed"] for b in known),
        "units_missing_energy": [b["plant"] for b in fleet if b["energy_mwh"] is None],
        "units": sorted(fleet, key=lambda b: -b["power_mw"]),
    }
