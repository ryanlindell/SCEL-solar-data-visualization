# UH Mānoa PVWatts model: a separate dataset

This folder holds a **single-site NREL PVWatts model** for UH Mānoa. It is **not** part of the Oʻahu-wide duck curve
(`web/data/duck_curve.json`). Only the site's own **Mānoa solar** page reads it, through a small copy
(`web/data/manoa_solar.json`) that `python -m pipeline.run_site_solar` writes next to these files.

| File | What it is |
|---|---|
| `manoa_pvwatts_model.json` | What was asked, the NSRDB weather cell it matched (with its distance), the assumptions, a yearly summary and the seasonal typical days |
| `manoa_pvwatts_hourly.csv` | All 8,760 hours, every column PVWatts returned |
| `manoa_pvwatts_seasonal_averages.csv` | The seasonal typical day, 24 hours per season, for weekdays (the duck curve's definition) and for all days |

**The coordinates are a placeholder** for the real SCEL sensor node's. Settings (tilt 0, losses 0, ...) are in
`pvwatts_sites.manoa` in `config/regions.yaml`. Regenerate everything with `python -m pipeline.run_site_solar`.

Read `notes` and `conventions` in the JSON before comparing with a sensor: the weather is a typical year, `poa` is global
horizontal irradiance only while `tilt` is 0, and `ac_w` is clipped by the inverter model (prefer `dc_w`).
See the main README, "The Mānoa PVWatts model".

## `sensor/`: real ground-sensor readings

`sunny_irradiance_2011_2012.csv` is real, measured irradiance at Mānoa: 5-minute averages (`t_5min`, with a sample count
`n` and `dev1intsolirr_avg` in W/m²), spanning 3 Jan to 25 Oct 2012 (196 days, with some gaps; no negative values). The
logger only ran roughly 6 AM to 6 PM (it did not sample at night), and July, November and December have no days at all
in this stretch, so winter and fall lean on fewer days than summer and spring.

## `sensor_pvlib/`: the second, independent estimate built from that sensor

This is the validation stage the section above once described as "later": `python -m sensor_model.run` (a separate
package, `sensor_model/`, sharing no code with `pipeline/`) turns the real sensor readings above into a second power
estimate, using [pvlib](https://pvlib-python.readthedocs.io/)'s own implementation of the PVWatts DC and inverter
equations (`pvlib.pvsystem.pvwatts_dc`, `pvlib.inverter.pvwatts`) instead of NREL's hosted PVWatts model. It uses the
same 4,300 kW, flat, lossless array as the PVWatts side, so the two can be overlaid directly; see "Two independent
Mānoa estimates" in the main README.

| File | What it is |
|---|---|
| `manoa_sensor_pvlib_model.json` | Assumptions, coverage (which days and hours the sensor actually has), and the seasonal typical days |
| `manoa_sensor_pvlib_hourly.csv` | Every (date, hour) with enough 5-minute samples to trust - a few thousand rows, not 8,760 |
| `manoa_sensor_pvlib_seasonal_averages.csv` | The seasonal typical day (weekdays, and all days), with `n_days` recording how many real days fed each hour |

Cell temperature is held at 25°C (STC) throughout: the sensor has no ambient-temperature or wind channel, so this is a
simplification (no thermal derate), the same one the PVWatts side makes with `losses 0`. A compact copy of the seasonal
averages is written to `web/data/manoa_solar_sensor.json` for `manoa.html`, which reads it alongside `manoa_solar.json`
and lets the "Estimate" toggle show either, or both overlaid.

**Timing caveat.** Compared with NSRDB's satellite data for the same 2012 hours (below), the sensor's day runs about
1.5 hours early, and on 132 of 196 days it shows light before sunrise; the best clear-sky fit is a +75-minute shift.
The timestamps are used as recorded until the logger's time base is confirmed. See the main README.

## `nsrdb_pvlib/`: NSRDB satellite irradiance for 2012, through the same pvlib code

`python -m nsrdb_model.run` downloads NREL's NSRDB `nsrdb-GOES-aggregated-v4-0-0` irradiance for every hour of 2012 at
the same point as the PVWatts model, and runs it through `sensor_model`'s array, power model and seasonal averaging
unchanged (cells at 25°C, like the sensor). Against the sensor line the only difference is satellite vs. ground sunlight.

| File | What it is |
|---|---|
| `raw/nsrdb_goes_v4_2012_60min.csv` | NSRDB's download as received (two metadata rows, then 8,784 hourly rows stamped at H:30) |
| `raw/nsrdb_goes_v4_2012_60min.request.json` | The parameters sent (never the API key or email); a change triggers a fresh download |
| `manoa_nsrdb_pvlib_model.json` | Grid cell, assumptions, a yearly summary and the seasonal typical days |
| `manoa_nsrdb_pvlib_hourly.csv` | All 8,784 hours: GHI, DNI, DHI, temperature, wind, solar zenith, DC and AC power |
| `manoa_nsrdb_pvlib_seasonal_averages.csv` | The seasonal typical day, same columns as the sensor's |

