"""A second, independent estimate of Mānoa's solar power, built from the real ground sensor and pvlib - not NREL PVWatts.

Kept entirely separate from `pipeline/` (which runs the NREL PVWatts model): different inputs (a measured irradiance
sensor instead of a satellite weather API), a different library (pvlib instead of PVWatts' hosted API), and its own
output locations (`data/manoa/sensor_pvlib/`, `web/data/manoa_solar_sensor.json`) so neither estimate can overwrite
the other. See `sensor_model/run.py` for the entry point and the package docstrings for the method.
"""
