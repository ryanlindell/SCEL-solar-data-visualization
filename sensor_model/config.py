"""Settings for the sensor + pvlib estimate: file locations, the array's electrical assumptions, and the seasons.

Reads `config/regions.yaml` directly (just the `hourly_data.seasons` block, the same seasons every other page uses)
rather than importing `pipeline.config`, so this package has no dependency on the PVWatts pipeline at all.
"""

from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
REGIONS_FILE = ROOT / "config" / "regions.yaml"

RAW_SENSOR_CSV = ROOT / "data" / "manoa" / "sensor" / "sunny_irradiance_2011_2012.csv"
OUT_DIR = ROOT / "data" / "manoa" / "sensor_pvlib"  # this package's own exports; never written to by pipeline/
WEB_JSON = ROOT / "web" / "data" / "manoa_solar_sensor.json"  # the second series manoa.js overlays on the PVWatts one

TIME_ZONE = "-10:00"  # Hawaii Standard Time, fixed offset, no daylight saving - matches the sensor's own timestamps
MIN_HOUR_FRACTION = 0.5  # an hour needs at least half its 5-minute slots present to count as a real hourly mean


@dataclass(frozen=True)
class ArrayConfig:
    """The same electrical assumptions as the PVWatts reference model (config/regions.yaml's pvwatts_sites.manoa),
    scaled to Mānoa's real nameplate capacity instead of PVWatts' 1,000 kW reference array, so the two estimates
    describe the same array and can be overlaid directly."""

    capacity_kw_dc: float = 4300.0  # Mānoa's real array nameplate (matches ARRAY_KW in web/js/manoa.js)
    tilt_deg: float = 0.0  # flat, matching pvwatts_sites.manoa; poa_wm2 below is then global horizontal irradiance
    dc_ac_ratio: float = 1.2  # PVWatts' own default, so the inverter clips at the same DC/AC ratio as the reference model
    eta_inv_nom: float = 0.96  # PVWatts' default inverter efficiency (its `inv_eff`)
    eta_inv_ref: float = 0.9637  # pvlib's constant for the PVWatts inverter formula (see pvlib.inverter.pvwatts)
    gamma_pdc: float = -0.0047  # PVWatts' default temperature coefficient for standard modules (%/degC, as a fraction)
    assumed_cell_temp_c: float = 25.0  # STC; the sensor has no ambient-temperature or wind channel to model cell heating

    @property
    def pdc0_w(self) -> float:
        """The array's DC nameplate rating, pvlib.pvsystem.pvwatts_dc's own `pdc0`."""
        return self.capacity_kw_dc * 1000.0

    @property
    def inverter_pdc0_w(self) -> float:
        """The inverter's DC input limit, pvlib.inverter.pvwatts's `pdc0` (a different quantity of the same name -
        see that function's docstring). Its AC ceiling is `inverter_pdc0_w * eta_inv_nom`."""
        return (self.pdc0_w / self.dc_ac_ratio) / self.eta_inv_nom


def season_months() -> dict[str, tuple[int, ...]]:
    """{season_id: (month, ...)}, straight from config/regions.yaml so this can never drift from the rest of the site."""
    with open(REGIONS_FILE, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return {sid: tuple(s["months"]) for sid, s in raw["hourly_data"]["seasons"].items()}


def season_labels() -> dict[str, str]:
    with open(REGIONS_FILE, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return {sid: s["label"] for sid, s in raw["hourly_data"]["seasons"].items()}
