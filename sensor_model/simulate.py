"""Sensor irradiance -> array power, using pvlib's own implementation of the PVWatts DC and inverter models
(`pvlib.pvsystem.pvwatts_dc`, `pvlib.inverter.pvwatts`) - the same equations NREL's hosted PVWatts API runs, but
driven by the real ground sensor instead of a satellite weather file, and pvlib's open implementation instead of a
network call.
"""

import pandas as pd
import pvlib

from .config import ArrayConfig


def simulate_power(hourly: pd.DataFrame, array: ArrayConfig = ArrayConfig()) -> pd.DataFrame:
    """Adds `dc_w` and `ac_w` to a frame that already has `poa_wm2` (see `parse.hourly_means`).

    With the array flat (tilt 0) `poa_wm2` is global horizontal irradiance, exactly as PVWatts treats it, so it is fed
    straight in as pvlib's `effective_irradiance` - no plane-of-array transposition is needed. Cell temperature is held
    at STC (25 degC, `array.assumed_cell_temp_c`) because the sensor has no ambient-temperature or wind channel to
    model heating; this makes the temperature term a no-op (matching the reference PVWatts model's own `losses 0`
    simplification) rather than a real thermal derate. `ac_w` is left uncapped by daylight-savings or negative-power
    edge cases by pvlib itself, which floors AC output at zero and caps it at the inverter's rated ceiling.
    """
    assert array.tilt_deg == 0.0, "poa_wm2 is only global horizontal irradiance while the array is flat"
    out = hourly.copy()
    dc_w = pvlib.pvsystem.pvwatts_dc(
        effective_irradiance=out["poa_wm2"],
        temp_cell=array.assumed_cell_temp_c,
        pdc0=array.pdc0_w,
        gamma_pdc=array.gamma_pdc,
        temp_ref=array.assumed_cell_temp_c,
    )
    out["dc_w"] = dc_w
    out["ac_w"] = pvlib.inverter.pvwatts(
        pdc=dc_w,
        pdc0=array.inverter_pdc0_w,
        eta_inv_nom=array.eta_inv_nom,
        eta_inv_ref=array.eta_inv_ref,
    )
    return out
