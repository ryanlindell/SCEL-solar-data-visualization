"""A third Mānoa estimate: NREL's NSRDB satellite irradiance for the real year 2012, run through the exact same pvlib
power model and seasonal averaging as the ground-sensor estimate (sensor_model/).

The point is a controlled comparison. Against "Sensor + pvlib" the only difference is where the sunlight number came
from (a satellite vs. a ground sensor, same year, same physics). Against the "PVWatts model" the differences are the
year (real 2012 vs. a composite typical year) and the power code (pvlib vs. NREL's hosted PVWatts). Entry point:
`python -m nsrdb_model.run`. Outputs: data/manoa/nsrdb_pvlib/ and web/data/manoa_solar_nsrdb.json.
"""
