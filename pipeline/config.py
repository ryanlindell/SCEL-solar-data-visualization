"""Settings: file locations, the API key, and the region definitions.

Nothing here talks to the network or the database - it only reads configuration.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
REGIONS_FILE = ROOT / "config" / "regions.yaml"
RAW_DB = ROOT / "data" / "raw" / "eia_raw.sqlite"
OUTPUT_JSON = ROOT / "web" / "data" / "renewable_share.json"
DUCK_CURVE_JSON = ROOT / "web" / "data" / "duck_curve.json"
CURTAILMENT_JSON = ROOT / "web" / "data" / "curtailment.json"
PLANTS_JSON = ROOT / "web" / "data" / "plants.json"
SITE_SOLAR_DIR = ROOT / "data"  # a site's full exports go in data/<site id>/
SITE_WEB_DIR = ROOT / "web" / "data"  # ...and a compact copy for the site's own page: web/data/<site id>_solar.json


class ConfigError(Exception):
    """Something is missing or malformed in the local configuration."""


@dataclass(frozen=True)
class HourlyModel:
    """Where and how a region's hourly (Tier 2) data is modeled."""

    lat: float
    lon: float
    pv_system: dict  # PVWatts inputs: tilt, azimuth, array_type, module_type, losses
    state: str  # state whose residential load files and rooftop-solar estimates are used
    share_of_state: float  # fraction of the state's homes that are in this region
    rooftop_share_of_state: float  # fraction of the state's rooftop solar that is in this region
    commercial_county_fips: str  # e.g. "G1500030" (the ComStock geography id)
    reference_year: int | None  # None = latest year fully covered by final EIA data


@dataclass(frozen=True)
class PvSite:
    """One point where PVWatts is queried on its own, apart from any region's duck curve (e.g. a campus sensor site)."""

    id: str  # short name used in the cache and file names, e.g. 'manoa'
    label: str
    lat: float
    lon: float
    pv_system: dict  # PVWatts inputs: tilt, azimuth, array_type, module_type, losses (all required by the API)
    placeholder: bool = False  # True while the coordinates are a stand-in for a real sensor's
    note: str = ""


@dataclass(frozen=True)
class Season:
    label: str
    months: tuple[int, ...]


@dataclass(frozen=True)
class HourlyData:
    """Settings shared by every region's hourly data."""

    residential_release: str
    commercial_release: str
    load_timestamp_utc_offset: int
    local_utc_offset: int
    seasons: dict[str, Season]


@dataclass(frozen=True)
class CurtailmentSource:
    """Where a region's rows are in Hawaiian Electric's curtailment workbooks."""

    totals_sheet: str
    by_reason_section: str


@dataclass(frozen=True)
class HecoFiles:
    base_url: str
    totals_file: str
    by_reason_file: str


@dataclass(frozen=True)
class Eia860Correction:
    """A value in EIA's generator inventory that is known to be wrong, and what to use instead."""

    plant_code: str
    generator_id: str
    field: str  # currently only "energy_mwh"
    filed_value: float  # the wrong value EIA publishes; the correction applies only while it is still exactly this
    value: float  # what to use instead
    reason: str
    source: str


@dataclass(frozen=True)
class Eia860m:
    base_url: str
    max_months_back: int
    corrections: tuple[Eia860Correction, ...]


@dataclass(frozen=True)
class Region:
    id: str
    label: str
    state: str
    county: str
    plants: dict[str, str]  # plant code -> plant name
    excluded_plants: dict[str, str] = field(default_factory=dict)  # plant code -> reason
    hourly: HourlyModel | None = None
    curtailment: CurtailmentSource | None = None
    rooftop_share_of_state: float | None = None  # this region's fraction of its state's rooftop solar

    @property
    def plant_codes(self) -> list[str]:
        return list(self.plants)


@dataclass(frozen=True)
class Settings:
    renewable_fuels: tuple[str, ...]
    storage_fuels: tuple[str, ...]
    regions: dict[str, Region]
    hourly_data: HourlyData
    heco: HecoFiles
    eia860m: Eia860m
    pvwatts_sites: dict[str, PvSite] = field(default_factory=dict)  # optional; see `pvwatts_sites` in regions.yaml


PVWATTS_REQUIRED = ("tilt", "azimuth", "array_type", "module_type", "losses")  # the API refuses a request without these


def _get_key(env_var: str, service: str) -> str:
    load_dotenv(ROOT / ".env")
    key = os.environ.get(env_var, "").strip()
    if not key or key == "your_key_here":
        raise ConfigError(
            f"{env_var} is not set. Create a file named .env in the project root "
            f"containing the line {env_var}=<your {service} key> (see .env.example)."
        )
    return key


def get_api_key() -> str:
    """Read EIA_API_KEY from the environment or from the .env file in the project root."""
    return _get_key("EIA_API_KEY", "EIA")


def get_nrel_api_key() -> str:
    """Read NREL_API_KEY (a free key from https://developer.nlr.gov/signup/)."""
    return _get_key("NREL_API_KEY", "NREL")


def load_settings(path: Path = REGIONS_FILE) -> Settings:
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    regions = {}
    for region_id, r in raw["regions"].items():
        hourly = None
        if "hourly" in r:
            h = r["hourly"]
            hourly = HourlyModel(
                lat=float(h["site"]["lat"]),
                lon=float(h["site"]["lon"]),
                pv_system=dict(h["pv_system"]),
                state=h["state"],
                share_of_state=float(h["share_of_state"]),
                rooftop_share_of_state=float(r["rooftop_share_of_state"]),  # a region-level setting, shared with Tier 1
                commercial_county_fips=h["commercial"]["county_fips"],
                reference_year=h.get("reference_year"),
            )
        # Codes are strings everywhere: the API returns them as strings, and YAML
        # would turn an unquoted 765 into an int.
        regions[region_id] = Region(
            id=region_id,
            label=r["label"],
            state=r["state"],
            county=r["county"],
            plants={str(p["code"]): p["name"] for p in r["plants"]},
            excluded_plants={str(p["code"]): p["reason"] for p in r.get("excluded_plants", [])},
            hourly=hourly,
            rooftop_share_of_state=(float(r["rooftop_share_of_state"]) if "rooftop_share_of_state" in r else None),
            curtailment=(
                CurtailmentSource(
                    totals_sheet=r["curtailment"]["totals_sheet"],
                    by_reason_section=r["curtailment"]["by_reason_section"],
                )
                if "curtailment" in r
                else None
            ),
        )

    pvwatts_sites = {}
    for site_id, s in (raw.get("pvwatts_sites") or {}).items():
        missing = [k for k in PVWATTS_REQUIRED if k not in (s.get("pv_system") or {})]
        if missing:
            raise ConfigError(f"pvwatts_sites.{site_id}.pv_system is missing {', '.join(missing)} (PVWatts requires them).")
        pvwatts_sites[site_id] = PvSite(
            id=site_id,
            label=s["label"],
            lat=float(s["lat"]),
            lon=float(s["lon"]),
            pv_system=dict(s["pv_system"]),
            placeholder=bool(s.get("placeholder", False)),
            note=str(s.get("note", "")).strip(),
        )

    hd = raw["hourly_data"]
    hourly_data = HourlyData(
        residential_release=hd["residential_release"],
        commercial_release=hd["commercial_release"],
        load_timestamp_utc_offset=int(hd["load_timestamp_utc_offset"]),
        local_utc_offset=int(hd["local_utc_offset"]),
        seasons={k: Season(label=v["label"], months=tuple(v["months"])) for k, v in hd["seasons"].items()},
    )
    return Settings(
        renewable_fuels=tuple(raw["renewable_fuels"]),
        storage_fuels=tuple(raw["storage_fuels"]),
        regions=regions,
        hourly_data=hourly_data,
        heco=HecoFiles(
            base_url=raw["heco_curtailment"]["base_url"],
            totals_file=raw["heco_curtailment"]["totals_file"],
            by_reason_file=raw["heco_curtailment"]["by_reason_file"],
        ),
        pvwatts_sites=pvwatts_sites,
        eia860m=Eia860m(
            base_url=raw["eia860m"]["base_url"],
            max_months_back=int(raw["eia860m"]["max_months_back"]),
            corrections=tuple(
                Eia860Correction(
                    plant_code=str(c["plant_code"]),
                    generator_id=str(c["generator_id"]),
                    field=c["field"],
                    filed_value=float(c["filed_value"]),
                    value=float(c["value"]),
                    reason=c["reason"],
                    source=c["source"],
                )
                for c in raw["eia860m"].get("corrections", [])
            ),
        ),
    )
