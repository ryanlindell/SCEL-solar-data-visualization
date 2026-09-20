"""Write the curtailment data to the static JSON file the web page reads."""

from datetime import datetime, timezone

from .config import Settings

QUARTER_FIELDS = (
    "delivered_mwh",
    "curtailed_mwh",
    "potential_mwh",
    "curtailment_pct",
    "oversupply_mwh",
    "system_constraint_mwh",
    "facility_requested_mwh",
)
ANNUAL_FIELDS = QUARTER_FIELDS + ("reasons_complete",)


def _round(value):
    return round(value, 3) if isinstance(value, float) else value


def build_payload(settings: Settings, results: dict[str, dict]) -> dict:
    """results maps region id -> {"quarters": [...], "annual": [...], "summary": {...}}."""
    regions = {}
    quarterly_rows = []
    annual_rows = []
    for region_id, result in results.items():
        regions[region_id] = {"label": settings.regions[region_id].label, **result["summary"]}
        for q in result["quarters"]:
            quarterly_rows.append(
                {"region": region_id, "period": q["period"], "year": q["year"], "quarter": q["quarter"],
                 **{f: _round(q[f]) for f in QUARTER_FIELDS}}
            )
        for a in result["annual"]:
            annual_rows.append({"region": region_id, "year": a["year"], **{f: _round(a[f]) for f in ANNUAL_FIELDS}})

    return {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "kind": "reported",
            "units": "MWh",
            "source": "Hawaiian Electric, Key Performance Metrics: historical curtailment data (Excel files on hawaiianelectric.com)",
            "covers": "wind and utility-scale solar together (Hawaiian Electric's 'curtailable renewable resources')",
        },
        "regions": regions,
        "quarterly_rows": quarterly_rows,
        "annual_rows": annual_rows,
    }
