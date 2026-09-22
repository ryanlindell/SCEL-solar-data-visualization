# Hawaiʻi grid data visualization

An interactive look at Hawaiʻi's electricity grid, starting with Oʻahu.

**Tier 1:** a data pipeline plus one chart — *what share of Oʻahu's electricity comes from
solar and wind, month by month, since 2001?* (`web/index.html`)

**Tier 2:** a **modeled** duck curve — *what does a typical weekday look like hour by hour, and
how much does solar reshape it?* (`web/duck_curve.html`). See [Tier 2: the duck curve](#tier-2-the-duck-curve).

**Tier 3:** **reported** curtailment — *how much wind and solar energy did Hawaiian Electric ask
plants to hold back, and why?* (`web/curtailment.html`). See [Tier 3: curtailment](#tier-3-curtailment).

**Tier 4:** a **simulation** — *what would a battery do to the duck, and how does that change with more
solar?* Move the sliders and the page re-runs the battery scheduler live (`web/battery.html`). See
[Tier 4: the battery simulation](#tier-4-the-battery-simulation).

Each tier was added on top of the earlier ones without restructuring them; see
[How the pieces fit together](#how-the-pieces-fit-together-and-how-to-extend-them).

---

## The big picture

Two separate programs, connected by one file:

```
  EIA website ──► Python pipeline ──► web/data/renewable_share.json ──► web page (Plotly chart)
  (the data)      (you run it         (a plain file on disk)            (just HTML + JavaScript,
                   when you want                                         no server-side code)
                   fresh data)
```

* The **pipeline** downloads data, stores it, does the math, and writes a JSON file.
* The **web page** only reads that JSON file. It never contacts EIA itself, so it is fast,
  free to host anywhere, and works even if EIA is down.

## Quick start

You need **Python 3.10 or newer**, a free **EIA API key** (register at
<https://www.eia.gov/opendata/>), and, for Tier 2 only, a free **NREL API key** (register at
<https://developer.nlr.gov/signup/>; NREL has been renamed NLR, but the service is the same).

1. **Create a virtual environment** (a private folder of Python packages for this project) and
   install the packages. From the project folder:

   ```powershell
   # Windows PowerShell
   python -m venv .venv
   .venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```
   ```bash
   # macOS / Linux
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

   > This project sits in a OneDrive folder, and `.venv` contains thousands of small files.
   > If OneDrive sync gets slow, exclude `.venv` from syncing or create the environment
   > somewhere outside OneDrive.

2. **Add your API key.** Copy `.env.example` to a new file named `.env` and replace
   `your_key_here` with your key:

   ```
   EIA_API_KEY=abc123...
   NREL_API_KEY=def456...
   ```

   `.env` is listed in `.gitignore`, so the keys are never committed to Git. Do not paste
   a key into any other file.

3. **Run the pipeline:**

   ```
   python -m pipeline.run_pipeline
   ```

   You will see four steps (`fetch`, `cache`, `process`, `export`). It takes a few seconds.

4. **View the chart.** Browsers won't let a page opened straight from disk read the data
   file, so serve the `web` folder:

   ```
   cd web
   python -m http.server
   ```

   Then open <http://localhost:8000>. Press Ctrl+C in the terminal to stop the server.
   The duck curve is at <http://localhost:8000/duck_curve.html> (run step 5 below first).

5. **(Tier 2) Build the duck curve.** Needs step 3 done first, and the NREL key:

   ```
   python -m pipeline.run_duck_curve
   ```

   The first run downloads about 300 MB of building-load files from NREL (streamed, and only a few
   columns are kept), so it takes a few minutes. After that they are cached and never downloaded
   again, and a rerun takes seconds.

6. **(Tier 3) Build the curtailment page.** No key needed:

   ```
   python -m pipeline.run_curtailment
   ```

   It downloads two small Excel files from Hawaiian Electric (about 200 KB), and takes a few seconds.
   View it at <http://localhost:8000/curtailment.html>.

7. **(Plant map and battery size) Build the power-plant data.** No key needed:

   ```
   python -m pipeline.run_plants
   ```

   It downloads EIA's monthly generator inventory (about 14 MB), keeps Oʻahu's units, and writes
   `web/data/plants.json`. View the map at <http://localhost:8000/map.html>. The same file sets the battery
   page's default size (Oʻahu's real battery fleet), so run it before step 8 if you want that.

8. **(Tier 4) Open the battery page.** There is nothing to run: it works in the browser from the files
   steps 3, 5 and 7 produced (and step 6 for scale). Open <http://localhost:8000/battery.html> and drag the sliders.

## Refreshing the data

EIA adds new months and occasionally revises old ones. To update, just run step 3 again:

```
python -m pipeline.run_pipeline
```

It is safe to run repeatedly ("idempotent"): the cache updates rows it already has instead of
adding copies. The summary line tells you what happened, for example
`12,335 rows fetched: 0 new, 0 revised, 12,335 unchanged`. Then reload the page.

Other options:

| Command | What it does |
|---|---|
| `python -m pipeline.run_pipeline --skip-fetch` | Rebuild the JSON from the cache without contacting EIA (no API key needed). Useful after changing `process.py`. |
| `python -m pipeline.run_pipeline --region oahu` | Refresh one region only (matters once there is more than one). |
| `python -m pipeline.discover_plants --region oahu` | Check EIA's plant registry for plants that are not in `config/regions.yaml` yet (e.g. a newly built solar farm). |
| `python -m pipeline.run_duck_curve` | Tier 2: fetch what is missing, then rebuild `web/data/duck_curve.json`. Refreshes EIA's rooftop-solar estimate every time; the NREL data only once. |
| `python -m pipeline.run_duck_curve --skip-fetch` | Rebuild the duck-curve JSON from the cache with no network. Use after Tier 1 data changes or after editing `process_hourly.py`. |
| `python -m pipeline.run_duck_curve --refresh-data` | Download the NREL data again. |
| `python -m pipeline.run_curtailment` | Tier 3: download Hawaiian Electric's curtailment files (every time, since it revises them), then rebuild `web/data/curtailment.json`. Also prints cross-checks against EIA's numbers. |
| `python -m pipeline.run_curtailment --skip-fetch` | Rebuild the curtailment JSON from the cache with no network. |
| `python -m pipeline.run_plants` | Download EIA's newest monthly generator inventory (EIA-860M) and rebuild `web/data/plants.json` (the plant map's data and the battery fleet). Tries this month's file, then earlier months, until one exists. |
| `python -m pipeline.run_plants --skip-fetch` | Rebuild `plants.json` from the cache with no network. |
| `python -m pipeline.run_site_solar` | The **Mānoa PVWatts model** (a separate dataset, see below): fetch if needed, check, and write `data/manoa/`. `--skip-fetch` rebuilds from the cache; `--refresh-data` asks NREL again; `--site <id>` picks another `pvwatts_sites` entry. |
| `python -m sensor_model.run` | The **second Mānoa estimate** (see below): the real ground sensor run through pvlib, no network needed. Writes `data/manoa/sensor_pvlib/` and `web/data/manoa_solar_sensor.json`. Shares no code with `pipeline/`. |
| `python -m pytest` | Run the Python tests (the pipeline: fetching, caching, calculations). Two live-API tests are skipped unless `RUN_LIVE_TESTS=1`. |
| `node --test "tests/js/*.test.mjs"` | Run the JavaScript tests (the battery scheduler and the Mānoa page's curve-smoothing maths). Needs [Node.js](https://nodejs.org) 22 or newer (tested on 24); no packages to install. |
| `python tests/browser/run_browser_tests.py` | Click through the real pages in a headless browser (buttons, sliders, zoom, phone width). Needs Edge, Chrome or Chromium and `pip install websocket-client`; add `--shots some_folder` to save screenshots. Slower, so run it when you change something under `web/`. |

## What each file does

```
config/regions.yaml       Which plants make up each region + which fuels count as renewable.
                          The only file you edit to change or add a region.

pipeline/                 Tier 1 (monthly renewable share)
  config.py               Reads .env and regions.yaml; knows where files live.
  http.py                 Shared "GET with retries, never leak the key" helper.
  fetch.py                FETCH: talks to the EIA API (paging, retries). Nothing else.
  cache.py                CACHE: stores the raw rows in SQLite. Never computes anything.
  process.py              PROCESS: raw rows -> monthly totals and renewable share.
  export.py               OUTPUT: writes the JSON file the web page reads.
  run_pipeline.py         Runs the four steps above in order.
  discover_plants.py      Helper that finds plants missing from regions.yaml.

                          Tier 2 (modeled hourly duck curve) - same four steps, own modules
  fetch_nrel.py           FETCH: solar output for a typical year from NREL PVWatts.
  fetch_oedi.py           FETCH: hourly home/business electricity use from NREL's public dataset.
  process_hourly.py       PROCESS: calibrates those shapes to real monthly EIA numbers,
                          then averages a weekday per season.
  export_hourly.py        OUTPUT: writes web/data/duck_curve.json.
  run_duck_curve.py       Runs the Tier 2 steps in order.

                          Tier 3 (reported curtailment)
  fetch_heco.py           FETCH: downloads Hawaiian Electric's two Excel files and reads the
                          numbers out by row/column *labels* (fails loudly if the layout changes).
  process_curtailment.py  PROCESS: quarterly and annual series, headline stats, cross-checks.
  export_curtailment.py   OUTPUT: writes web/data/curtailment.json.
  run_curtailment.py      Runs the Tier 3 steps in order.

                          Power-plant map + battery fleet (EIA-860M)
  fetch_eia860m.py        FETCH: finds and downloads EIA's newest monthly generator inventory; checks the
                          file's contents, since EIA answers a missing month with a normal-looking web page.
  process_plants.py       PROCESS: units -> plants with in-service/retirement dates; the battery fleet;
                          applies documented corrections to values EIA published wrongly.
  export_plants.py        OUTPUT: writes web/data/plants.json.
  run_plants.py           Runs those steps in order.

                          A single-site PVWatts model (UH Mānoa), separate from the Oʻahu duck curve
  fetch_nrel.py           (also) fetch_pvwatts_site: one point, every hourly field, the full station_info.
  process_site_solar.py   PROCESS: plausibility checks and the seasonal typical days (the duck curve's definition).
  export_site_solar.py    OUTPUT: writes data/<site id>/ (JSON + two CSVs), and web/data/<site id>_solar.json
                          for that site's own page (never the duck-curve files).
  run_site_solar.py       Runs those steps in order.

  (cache.py holds the tables for all tiers.)

sensor_model/             A second, independent Mānoa estimate (see "Two independent Mānoa estimates" below).
                          Shares no code with pipeline/: different input (a real sensor, not NREL), different
                          library (pvlib, not a network call), and its own output files.
  config.py               Where the raw sensor file, exports and array assumptions live; seasons read straight
                          from config/regions.yaml.
  parse.py                The raw 5-minute sensor readings -> hourly means (dropping hours with too few samples).
  simulate.py              Hourly irradiance -> DC/AC power, via pvlib.pvsystem.pvwatts_dc and pvlib.inverter.pvwatts.
  seasonal.py             The seasonal typical days, from the sensor's own real calendar dates (not a stand-in year).
  export.py               OUTPUT: writes data/manoa/sensor_pvlib/ and web/data/manoa_solar_sensor.json.
  run.py                  Runs those steps in order (python -m sensor_model.run).

data/raw/eia_raw.sqlite   The cache (created on first run, not committed to Git).
                          Raw data only; computed values never go in here.
data/manoa/               The Mānoa PVWatts model's own exports (see "The Mānoa PVWatts model" below).
data/manoa/sensor/        Real ground-sensor irradiance readings for Mānoa. See data/manoa/README.md.
data/manoa/sensor_pvlib/  The second estimate's own exports, built from that sensor by sensor_model/ (see
                          "Two independent Mānoa estimates" below).

web/
  index.html              Tier 1 page: renewable share over time.
  duck_curve.html         Tier 2 page: the duck curve.
  css/style.css           Colors and layout (light + dark mode).
  js/common.js            Helpers shared by every chart: load data, theme, Plotly defaults.
  js/renewable_share.js   Tier 1 chart.
  js/duck_curve.js        Tier 2 chart.
  curtailment.html        Tier 3 page: curtailment.
  js/curtailment.js       Tier 3 charts.
  battery.html            Tier 4 page: the battery simulation.
  js/dispatch.js          Tier 4 battery scheduler: pure calculation, no page code (so it can be tested).
  js/battery.js           Tier 4 page: sliders, stat tiles, charts.
  map.html                Plant map page: Oʻahu's power plants appearing and retiring over time.
  js/map.js               The map (Leaflet, not Plotly: it draws with plain SVG and needs no WebGL).
  manoa.html              Mānoa solar page: two independent estimates of solar power over a day at UH Mānoa,
                          overlaid or shown alone.
  js/manoa.js             The Mānoa page. js/bell.js is its maths (pure, so it can be tested).
  data/renewable_share.json   Tier 1 output. Committed so the page works right after cloning.
  data/duck_curve.json        Tier 2 output. Committed too.
  data/curtailment.json       Tier 3 output. Committed too.
  data/plants.json            Plant-map / battery-fleet output. Committed too.
  data/manoa_solar.json       The PVWatts estimate (see "The Mānoa PVWatts model").
  data/manoa_solar_sensor.json  The sensor + pvlib estimate (see "Two independent Mānoa estimates").
  data/milestones.json        The ten events marked on the Tier 1 chart. Written by hand, not by the pipeline.

tests/                    test_*.py: Python tests for fetching, caching and the calculations, including
                          test_sensor_model.py for sensor_model/.
  js/dispatch.test.mjs    JavaScript tests for the battery scheduler (run with node).
  browser/                Click-through tests of the real pages in a headless browser.

report/                   report.tex: a data-focused technical report on the project (sources, methods, limits,
                          what extra data would help, novelty and users). figures/ holds its screenshots;
                          report.pdf is a pre-built copy. Build with `pdflatex report.tex`, twice.
```

Keeping fetch, cache and process in separate files means each can change without touching
the others — for instance, a later tier can reuse the cache but compute something new.

## Where the data comes from

The pipeline calls the **EIA Open Data API v2** route `electricity/facility-fuel`: monthly
electricity generation, in megawatt-hours (MWh), for each power plant and each fuel, going
back to January 2001. (It comes from EIA survey form EIA-923.)

**Why plants instead of "Hawaiian Electric"?** EIA does not publish a fuel mix per utility
or per island — only per plant (and per state). So a *region* is defined as a list of plants.
`config/regions.yaml` lists the 38 plants on Oʻahu's grid, found by matching EIA's plant
registry (route `electricity/operating-generator-capacity`, which records each plant's county;
Honolulu County is Oʻahu). This includes plants owned by independent power producers who sell
to Hawaiian Electric, and retired plants such as the AES coal plant so the history is complete.
Two refinery cogeneration plants are excluded because they power the refinery, not the grid
(reasons are recorded in the YAML).

## How the number is calculated

For each month:

```
total generation      = sum of MWh over every plant and fuel
                        (battery charging is excluded — storage isn't generation)
renewable generation  = the solar + wind part of that total
renewable share (%)   = 100 × renewable generation ÷ total generation
```

EIA publishes each plant's numbers several overlapping ways (a plant total, a per-fuel
breakdown, a per-fuel-and-engine-type breakdown). `fetch.py` asks only for the per-fuel level,
and `process.py` drops the plant-total rows, so nothing is counted twice. There are tests for
this in `tests/test_process.py`.

Every row carries a `region` field (`"oahu"`), and the JSON also keeps generation for **every**
fuel each month, not just solar and wind, so other tiers can reuse it.

## Things to know when reading the chart

1. **Rooftop solar is not in the solid line, but there is an estimate of it.** EIA's plant-level data
   covers utility-scale plants (1 MW or larger) only. Privately owned rooftop panels supply a substantial
   share of Hawaiʻi's electricity, so the solid line understates the real renewable share. The dotted
   line, "Including rooftop solar (estimate)", adds EIA's own estimate of small-scale solar. That estimate
   is for the whole state and starts in 2014, so it has to be brought down to Oʻahu. It is sized from
   **Hawaiian Electric's own reported Oʻahu rooftop total for each quarter**: EIA's monthly figures are
   scaled so each quarter adds up exactly to Hawaiian Electric's number (checked for all 50 quarters), and
   EIA's statewide monthly pattern only decides how a quarter splits across its three months. (Why not one
   fixed share? Measured against Hawaiian Electric's totals, Oʻahu's share of the state has ranged from
   about 0.65 to 0.83, so a constant 0.77 would be off by up to roughly 18% in some years, 2017 worst.)
   This needs Tier 3's data in the cache (`python -m pipeline.run_curtailment`); without it, or for a
   quarter Hawaiian Electric has not reported, the pipeline falls back to `rooftop_share_of_state` (0.77)
   and says so; each month's hover text and the JSON's `rooftop_basis` field record which was used. The
   figure still counts all customer-owned renewable generation, which is almost all rooftop solar. The
   dotted line also uses a different denominator: rooftop output is added to both the top and the bottom of
   the fraction, so it is the share of *all* electricity used, not just what utility-scale plants generated.
   Adding this line did not change any existing number (checked by comparing the output before and after).
2. **Only solar and wind count as renewable** (your project definition). Waste-to-energy
   (H-POWER) and biomass are not counted. To change this, edit `renewable_fuels` in
   `config/regions.yaml` and run with `--skip-fetch`.
3. **The newest months are preliminary.** About a dozen small plants file to EIA only once a
   year, so their most recent data arrives up to two years late. Until it does, the newest
   months are missing those plants and read low (roughly 1 to 1.5 percentage points in the last
   complete years). The pipeline detects this automatically and marks those months
   `"provisional": true`; the chart shades them and the table labels them "preliminary".
   Once EIA publishes the missing year, the next pipeline run corrects them by itself.
4. **EIA lags by two to three months**, so the latest month is never this month.
5. **March 2011 is the first Oʻahu wind in EIA's data, not the first on the island.** Oʻahu's earlier
   turbines at Kahuku (installed from 1985, including a 3.2 MW Boeing MOD-5B running by 1987 and shut down
   in 1996) predate EIA's plant data, which starts in 2001 and records no Oʻahu wind until Kahuku Wind Power
   (30 MW) in March 2011. Milestone 1 on the chart marks that point (its card says so) and the page has a
   footnote; the data is untouched.
   (I could not confirm one detail I was given, that Hurricane Iniki ended the early wind farm: none of the
   pages I read mention it, and they give other reasons, so it is not on the page.)
6. **Ten milestones are marked on the chart.** Numbered diamonds sit in a band along the top, each with a
   faint dotted guide down to its month; click one (or its button under the chart, which also works from the
   keyboard) to open a card with a one-sentence description, the date, and a source link. They are:
   1 Kahuku Wind starts (Mar 2011), 2 first utility-scale solar (Feb 2012), 3 Kawailoa Wind (Nov 2012),
   4 the Honolulu Power Plant retires (Jan 2014), 5 Act 97's 100% renewable goal (Jun 2015), 6 net metering
   closes to new customers (Oct 2015), 7 four solar farms (Sep to Nov 2019), 8 Nā Pua Makani wind (Dec 2020),
   9 the AES coal plant closes (Sep 1, 2022), 10 Kapolei Energy Storage (Dec 2023). They live in
   `web/data/milestones.json`, which is written by hand, one entry per event (`month`, `date_text`, `title`,
   `text`, `source`, `url`); to add one, add an entry in date order. Eight of the ten dates (everything
   except the two laws) are also in EIA-860M, and `tests/test_milestones.py` checks them against
   `plants.json`, so a refresh that moves one fails the test instead of leaving the chart wrong. The diamonds
   do not change any number on the chart.
7. **An "About this data" panel** beside the chart summarizes where the numbers come from, and a collapsed
   "Learn more about the data collection" section explains who reports to EIA and how often (about 3,000
   plants monthly, 9,500 more annually), the two-to-three-month lag and why recent months are provisional,
   the 1 MW threshold, and how EIA estimates rooftop solar. The figures come from EIA's own documentation.

## Tier 2: the duck curve

**What it is.** On a sunny day, solar panels flood the grid with power at midday, so the demand
that *conventional* power plants must meet dips — then solar fades just as everyone comes home, and
that demand climbs steeply. Plotted over 24 hours, the dip and climb look like a duck. The chart
shows this for a typical weekday in each season.

**Why it is modeled.** Hourly grid data for Hawaiʻi is not public: EIA's hourly grid monitor doesn't
cover Hawaiʻi, and Hawaiian Electric publishes only monthly and annual summaries. So the chart is
labelled **Modeled** everywhere. Please read it as an illustration of the pattern, not a record of
any real day. If you obtain real hourly data later, it can replace the modeled inputs without
changing the chart (see below).

**How the model is built.** Shapes come from NREL; sizes come from real EIA data (Tier 1):

| Piece | Daily *shape* from | *Size* (per month) set by |
|---|---|---|
| Customer demand | NREL End-Use Load Profiles: simulated homes (ResStock) + businesses (ComStock) | Oʻahu's actual generation (Tier 1) + rooftop solar |
| Rooftop solar | ResStock's simulated hour-by-hour output of rooftop panels on Hawaiʻi homes (actual 2018 weather; the same pattern stands in for businesses' panels, which NREL doesn't simulate). Not the PVWatts model, which is used only for utility-scale solar | EIA's estimate of Hawaiʻi rooftop solar × 77% for Oʻahu (the 77% is measured; see Tier 3's cross-check). Each month's total is spread over all hours of that month in proportion to the pattern |
| Utility-scale solar | NREL PVWatts, a typical year of sunshine for a reference solar farm | What Oʻahu's solar plants actually generated (Tier 1) |
| Wind | none (spread evenly through the month) | What Oʻahu's wind farms actually generated (Tier 1) |

Then, in megawatts, hour by hour:

```
grid load   = customer demand - rooftop solar          (what the utility "sees")
net load    = grid load - utility solar - wind         (what fossil-fuel plants must supply)
```

The duck is the shape of **net load**. The reference year is the latest year of *final* Tier 1 data
(2024 at the time of writing). One typical weekday per season is the hourly average over that
season's Monday-to-Friday days.

**How well does it match reality?** What I could check, and what I couldn't:

* ✅ *Rooftop solar total* matches Hawaiian Electric's own Oʻahu figure for 2024 (1,211 GWh). This
  used to be a weaker point: the first version assumed Oʻahu had 70% of the state's rooftop solar
  (from population), which gave 1,101 GWh, 9% low. Hawaiian Electric's published totals (found while
  building Tier 3) show the real share is 0.65–0.83 depending on the year (0.77–0.83 since 2019), so
  `rooftop_share_of_state` is now set to the measured 2024 value, 0.77.
* ✅ *Evening peak.* Hawaiian Electric told the Public Utilities Commission that Oʻahu's 2020 system
  peak was 1,087 MW at 5:26 PM (2010–2019 peaks were 1,141–1,206 MW). The model's *average* weekday
  peaks at roughly 990–1,070 MW around 6 PM, depending on season — the right size and nearly the
  right hour, and it is naturally a bit below the single busiest day of the year.
  ([Adequacy of Supply report, Jan 2021](https://puc.hawaii.gov/wp-content/uploads/2021/02/GEN-RPT.HECO_.ADEQUACY-OF-SUPPLY-2021.pdf))
* ❓ *Overnight and midday minimums are unchecked* — I found no public figures to compare with. The
  modeled overnight grid load (roughly 40–50% of the evening peak, depending on season) looks lower than I would expect for Oʻahu,
  which exaggerates how deep the "belly" looks relative to the night.
* ❓ *Curtailment* (solar switched off because the grid can't absorb it) is not modeled, so the real
  midday dip is probably shallower than drawn.

**Assumptions worth knowing** (all in `config/regions.yaml`; `rooftop_share_of_state` is set once for the
region because Tier 1 uses it too, the rest are under `hourly:`):

* `share_of_state: 0.70` — Oʻahu's share of Hawaiʻi's *homes*, which sets how much residential
  demand goes into the demand shape. It is approximated by Oʻahu's share of the population
  (2020 Census: 1.02 million of 1.46 million).
* `rooftop_share_of_state: 0.77` — Oʻahu's share of Hawaiʻi's *rooftop solar*, which sets the size of
  the modeled rooftop output for the reference year (2024, where it is exact); Tier 1's dotted line only
  falls back to it. Measured: Hawaiian Electric's Oʻahu total ÷ EIA's statewide estimate (2017: 0.65,
  2019: 0.79, 2022: 0.83, 2023: 0.82, 2024: 0.77, 2025: 0.81). `python -m pipeline.run_curtailment`
  reprints the recent years each run.
* `pv_system` — the reference solar farm used for the daily *shape* (fixed panels tilted 20°, facing
  south). Its size doesn't matter, only its shape; the size is set by real generation.
* `reference_year` — `null` means "the latest fully final year"; set a year to pin it.
* `seasons` (top of the file) — which months make up each season.

**A gotcha worth knowing.** NREL's load files are time-stamped in *Eastern* Standard Time, not
Hawaiʻi time, and each stamp marks the *end* of a 15-minute period. `fetch_oedi.py` converts them
(5 hours earlier), and `tests/test_fetch_hourly.py` checks it — without this, every curve would sit
five hours off, and the belly would land at the wrong time of day.

**Swapping in real data later.** The chart reads `web/data/duck_curve.json`, which is built from
three inputs in the cache: an hourly demand table, an hourly rooftop solar table and an hourly utility
solar table (`raw_building_load_hourly`, `raw_pvwatts_hourly`, ...). If you obtain real Hawaiian Electric
hourly data, add a `fetch_*` module for it, store it in the cache, and change `process_hourly.py` to
use it instead of the modeled shapes; the chart, the JSON layout and `run_duck_curve.py` stay as they are.

## Tier 3: curtailment

**What it is.** *Curtailment* means the utility tells a wind farm or solar plant to produce less than
it could have — usually because the grid can't use all of it. It is energy that was available and
wasted. This page shows, quarter by quarter, the energy the grid took ("delivered"), the energy it
asked plants to hold back ("curtailed"), and the two together, which is what the plants could have
supplied ("potential").

**Reported, not estimated.** The original plan was to *estimate* curtailment by comparing a modeled
"potential" solar output with actual generation. That turned out to be unnecessary and, I think, a
poor idea: Hawaiian Electric publishes the measured numbers for every island, quarterly since 2012
([Key Performance Metrics: Renewable Energy](https://www.hawaiianelectric.com/about-us/performance-scorecards-and-metrics/renewable-energy)).
Since 2015, curtailment has been only 1–7% of potential a year on Oʻahu, while the inputs a model needs (how much DC capacity
sits behind each plant's AC rating, real weather vs a typical year, panel aging) are each uncertain by
more than that, so a modeled estimate could not have resolved the thing being measured. (That is my
reasoning; I did not build the modeled version to test it.) The page is tagged **Reported**, as
Tier 2 is tagged **Modeled**.

**Where the numbers come from.** Two public Excel files, downloaded on every run because Hawaiian
Electric updates them quarterly and sometimes corrects past figures:

* `historical_03_curtailment.xlsx` — delivered and curtailed energy per island and quarter
  (Oʻahu: Q1 2012 to the latest quarter).
* `historical_03_curtailment_cat.xlsx` — curtailed energy by reason (oversupply, system constraint,
  facility requested), recorded since July 2015.

The files are made for people, so `fetch_heco.py` finds rows by their *labels* and headings rather than
fixed cell positions, and stops with a clear error if the layout changes instead of quietly reading the
wrong cells. `tests/test_fetch_heco.py` covers the quirks of the real files (numbers stored as text,
`NA` markers, `2015*` headings, three different apostrophe styles in "Oʻahu", several islands stacked in one sheet).

**What to know when reading it.**

* **Wind and solar are reported together** (Hawaiian Electric's "curtailable renewable resources"),
  so the page cannot say how much of the curtailed energy was solar.
* **Rooftop solar isn't curtailable**, so it is not part of potential.
* **Reasons matter.** *System constraint* means limits on particular parts of the grid or equipment;
  *oversupply* means more supply than demand across the whole system. At the time of writing,
  system constraints were 95% of 2025's curtailed energy — so much curtailment is not the classic
  midday-oversupply story. The data does not say what falls under "system constraint", so it is unclear how
  much of it storage could relieve. (An earlier version of this README said storage would help only
  partly. That was an overreach: Hawaiian Electric's own modeling reportedly expected the Kapolei battery
  to cut curtailment by 69%, and batteries that provide grid services may relieve system-constraint
  curtailment too.)
* **Quarterly, not hourly.** Nothing here says *when* in the day energy was curtailed.
* **Annual totals are sums of the quarters.** Hawaiian Electric's own annual figures differ slightly
  for a few years (2018, 2023) after corrections it describes in its notes; using the quarters keeps
  every view consistent.
* **The earliest quarters look dramatic** (over 25% in 2012) because the amount of wind and solar
  was tiny then.

**Cross-checks** (printed by `python -m pipeline.run_curtailment` every run, computed against the
other tiers' data):

* Hawaiian Electric's "delivered" wind + solar is 0.85–0.94 of EIA's solar + wind (Tier 1) for
  2021–2024. The two count slightly different sets of plants, so an exact match isn't expected; a
  stable ratio near 1 says both describe the same thing.
* Hawaiian Electric's Oʻahu rooftop total ÷ EIA's statewide estimate = 0.77–0.82. This is what fixed
  Tier 2's rooftop assumption (see above).

## Tier 4: the battery simulation

**What it is.** A what-if tool. Pick a season, then drag sliders for the battery's size and for how much
extra solar there is; the page instantly re-plans when the battery should charge and discharge, and shows
what that does to the evening peak, the midday low, the steepest climb, and any curtailed solar.

**No pipeline of its own.** It runs entirely in the browser on `web/data/duck_curve.json` (the modeled typical
weekday from Tier 2), `web/data/plants.json` (which sets the default battery size) and, for scale only,
`web/data/curtailment.json` (Tier 3). Rerun those pipelines and reload the page to refresh it. If
`plants.json` is missing the sliders fall back to fixed defaults (185 MW / 555 MWh).

**The sliders.**

| Slider | Default | Where the default comes from |
|---|---|---|
| Battery power (MW) | 326 | **Oʻahu's whole battery fleet**, from EIA-860M: six batteries totalling 326.4 MW (Kapolei Energy Storage 185, Hoʻohana 52, Mililani South 39, Waiawa 36, West Oʻahu 12.5, BYU-Hawaiʻi 1.9). Set by the pipeline (`plants.json`), so it follows the data. |
| Battery energy (MWh) | 1,125 | The fleet's combined storage: 985.5 MWh as EIA filed it, **1,124.9 MWh after one correction**: EIA lists Waiawa's 36 MW battery as 4.6 MWh, which is implausible; its developer reports 144 MWh. The correction is in `config/regions.yaml` with its source, and applies only while EIA still publishes exactly 4.6. |
| Round-trip efficiency (%) | 85 | A typical lithium-ion figure. |
| More utility-scale solar (%) | 0 | New solar farms as a percentage of today's, with the same daily shape. |
| Fossil minimum output (MW) | 300 | **An invented illustration.** See below. |

**How the battery is scheduled** (`web/js/dispatch.js`). A small optimizer plans the whole day at once
(dynamic programming over the battery's state of charge; no libraries):

* Fossil plants get more expensive per MWh the harder they are pushed, so each hour is charged the
  *square* of its net load. Minimizing the total flattens the day: charge when net load is low,
  discharge when it is high.
* Fossil plants also can't turn down below a minimum. Solar that would push net load under the
  "fossil minimum" has to be curtailed, unless the battery can soak it up; curtailed energy is penalized
  so heavily that absorbing it always comes first.
* It knows the whole day in advance ("perfect foresight"), and repeats the same day five times starting
  with an empty battery, showing the middle one, so you see a steady-state day. (Starting the battery
  *with* free energy was tried first and was wrong: the optimizer just spent the gift slowly, and the
  "typical day" was a draining day. A test now guards against it.)

**What to know when reading it.**

* **It is a simulation of a model.** It runs on Tier 2's modeled weekday, which is itself a model. It
  shows how a battery and more solar *interact*, not what will happen on any real day.
* **The "fossil minimum" is not a real figure.** I have no public number for how low Oʻahu's fossil
  plants can be turned down. 300 MW was chosen so that today's typical weekday isn't curtailed and
  extra solar visibly is. The slider exists so you can explore it, and it is labelled "illustrative".
* **The battery also charges overnight.** Minimizing squared net load flattens the *whole* day, not just
  the midday dip, so it also tops up in the small hours and discharges in the morning shoulder as well
  as the evening peak. Any energy it stores outside the solar hours comes from fossil plants; it is not
  free.
* **The simulation only models one kind of curtailment.** Tier 3 found that most recent curtailment was
  recorded as "system constraint" rather than "oversupply". The simulation's curtailment is the oversupply
  kind (net load pushed below a floor), so it may *understate* what real batteries can do: grid-forming
  batteries such as Kapolei Energy Storage also provide inertia and frequency response, and Hawaiian
  Electric's modeling reportedly expected that battery to cut curtailment by 69%. Hawaiian Electric's real
  curtailed energy (about 39 MWh a day on average in 2025) is shown on the page for scale.
* **What a real Oʻahu battery does.** Kapolei Energy Storage (185 MW / 565 MWh; owner Kapolei Energy
  Storage I, LLC per EIA, developed by Plus Power; online December 2023) provides load shifting, which is what
  this page simulates, and also fast-frequency response, synthetic inertia and black start, which it does not.
  The page's collapsed explainer says so, and explains why the Duck curve page (a battery-free model, not
  measured history) doesn't already show any flattening.
* **Left out:** battery ageing, energy kept in reserve for emergencies, prices and market rules,
  weekends and weather, demand response, limits on particular lines, and any change to rooftop solar
  or wind.

**A few things it shows** (spring weekday, at the time of writing; the page is the source of truth):

* The default (the whole fleet acting as one) takes the evening peak from about 993 MW to 687 MW, lifts
  the midday low from 387 MW to 557 MW, and shrinks the steepest 3-hour climb from +397 MW to +129 MW,
  cycling about once a day. Kapolei alone (185 MW / 565 MWh) would take the peak only to about 808 MW.
  That large effect rests on the fleet being free to charge from the grid whenever it likes; batteries built
  with solar farms may not be, so treat it as an upper bound.
* With 200% more solar and no battery, about 783 MWh a day of solar would be curtailed against the fossil
  minimum; the fleet absorbs all of it. It saturates further out: at +300% solar about 376 of 1,604
  MWh/day are still curtailed, at +400% about 1,251 of 2,528. That is the interplay the sliders are for.

**How it is tested.** The scheduler is plain JavaScript with no page code, so `tests/js/dispatch.test.mjs`
tests it directly (`node --test "tests/js/*.test.mjs"`): power and energy limits are never exceeded, energy is
conserved through the efficiency losses, a flat day is left alone, a bigger or more efficient battery is never
worse, absorbing surplus takes priority over flattening, and it runs fast enough (tens of milliseconds) for live
sliders. `tests/browser/` drags the real sliders and clicks the real buttons.

## The power-plant map

**What it shows.** Every plant EIA lists for Oʻahu (Honolulu County) as a circle on a map, with a time
slider from December 1947 to the furthest-away *planned* plant (April 2029 in the current data). It opens on
today's date, and the **Today** button jumps back to it from anywhere. Plants appear when their first unit
enters service and vanish when their last unit is retired. Circle size is the capacity in service on that date
and color is the fuel of the largest units; hovering shows a plant's mix, and clicking opens a card listing
each unit with its size and in-service and retirement dates (bold for those in service on the slider date).
Press Play to watch the whole timeline in about nine seconds; it moves a month at a time, at a fixed speed
of a year every 0.11 s (it is timed against the clock, so it stays the same speed on any screen). Beside the
map, a live table shows capacity by fuel.

**Planned plants.** The monthly file has a second sheet, *Planned*: units whose operators have told EIA they
intend to build them, each with an expected in-service month and a stage (from "under construction, more than
50 percent complete" to "planned, regulatory approvals not initiated"). Past the newest month EIA has records
for (July 2026 today), the map shows those units as faint circles with a dashed outline, from their expected
month; the panel and card label them "planned", and they are **never counted as in service**. The slider ends
at the furthest expected month. Three things to keep in mind: the dates are operators' expectations and
often slip; a project that is cancelled or postponed drops off EIA's Planned sheet, so the future view can
change from one month's file to the next; and **EIA lists no planned retirements for Oʻahu**, so the future
view can only add plants, it never shows an existing plant closing. Today is a few weeks past EIA's newest
file, so a plant that came online since then still shows as planned until the next file. (In the current data
the biggest planned addition is Puuloa Energy, eleven 9.4 MW waste-biomass units, 103 MW in all, expected in
April 2029, at the earliest stage of approval; the other four are solar projects, three of them with batteries.)

**Where the data comes from: EIA-860.** Every plant on the map is on Form EIA-860, EIA's inventory of the
country's power plants. What it takes to be on it:

* The plant's generators must add up to **1 MW or more** of nameplate capacity, **and** the plant must be
  **connected to the local or regional electric grid** (able to deliver power to it or draw power from it).
* The plant's **operator** files the form (for jointly owned plants, only the operator does). There is an
  annual form, and EIA publishes a monthly update (EIA-860M); the map uses the monthly file.
* For each generator it records size, fuel and technology, location, in-service month, retirement month
  (once retired), and for batteries their energy capacity.

So it does **not** contain rooftop solar or other small systems (under 1 MW in total), generators that are
not connected to the grid, or anything that closed before EIA's records begin.

**What that means for the early years, honestly.** The monthly file lists retirements only from 2002 on, so
a plant that closed before then does not appear at all, even if it once supplied Oʻahu. That is why 1947
shows a single plant (Waiau unit 3, retired in 2024, is the only unit from that era EIA still lists) and the map looks
sparse until later: it shows the plants that *survived to 2002 or later*, not everything that ever existed.
Oʻahu's first wind turbines at Kahuku (from 1985; the last, a Boeing MOD-5B, shut down in 1996) are missing
for the same reason. You asked for EIA-860 plants only, so I did not add them by hand. The page says all
this in a collapsed explainer.

**Which plants.** Everything EIA places in Honolulu County. Two refinery cogeneration plants (Tesoro/Par
Hawaii and Hawaii Cogen) are on the map but are excluded from the renewable-share chart because they power
the refinery, not the grid; the plant card says which plants are counted there.

**Running it.** `python -m pipeline.run_plants` finds EIA's newest monthly file (it tries this month, then
earlier months) and stores Oʻahu's units (operating, retired and planned: 117 in the current file) in the cache. One trap worth knowing: for a month that hasn't
been published yet, EIA answers with a normal-looking web page and a "success" status instead of an error,
so the fetcher checks that what it got is really an Excel file. The cache holds one snapshot that each run
replaces (a unit moves from "operating" to "retired" between releases, so rows are replaced, not merged).

**A documented correction.** EIA lists Waiawa Solar's 36 MW battery as storing 4.6 MWh, which is implausible
(its developer reports 144 MWh). `config/regions.yaml` records that correction with its source, and it applies
only while EIA still publishes exactly 4.6; if EIA changes the number, the pipeline warns instead of silently
overriding a possibly correct value. `plants.json` keeps both values.

**Design notes.** The map uses Leaflet with OpenStreetMap tiles (no key needed; please keep use light per
[OSM's tile policy](https://operations.osmfoundation.org/policies/tiles/)). I first tried CARTO's basemaps, but
they now require an API key and stamp "API KEY REQUIRED" across the map, which a tile-loading check missed
and a screenshot caught. Dark mode darkens the tiles with a CSS filter. Leaflet was chosen over Plotly's map
layer because Plotly's needs WebGL, which some browsers and locked-down machines lack.

## The Mānoa PVWatts model (a separate dataset)

**What it is.** A modeled series of sunlight and solar power at UH Mānoa's coordinates, one of two independent estimates
the site's page overlays (see "Two independent Mānoa estimates" below for the other, sensor-based one). It is **not**
part of the Oʻahu duck curve or any Oʻahu page: it is cached in tables of its own, and its full data is written to
`data/manoa/`. Only the site's own **Mānoa solar** page (`manoa.html`) reads it, through a small file of its own,
`web/data/manoa_solar.json`; the duck-curve files are never involved. Run it with `python -m pipeline.run_site_solar`.

**The page: modeled power over a day.** `manoa.html` shows the modeled power of Mānoa's real 4,300 kW of flat, lossless
panels over an average day (PVWatts itself was run for a 1,000 kW reference array; the page scales every number up ×4.3,
since PVWatts output scales linearly with capacity), with the model's own hourly averages as dots and a smooth line
traced through those exact points (for readability only - it is not a fitted or idealized curve of any assumed shape).
Choose an **estimate** (PVWatts, the sensor + pvlib one, or both overlaid), a **season** (or compare all four), **DC**
power (the panels) or **AC** (after the inverter), and **weekdays** or **all days**; `?season=summer|all`, `?power=ac`,
`?days=all_days` and `?sources=pvwatts,sensor` in the address pick a view. The tiles give the peak, the day's energy,
how many hours output stays at half its peak or more, the capacity factor (average power over the day, as a share
of the array's rating), and peak sun hours (PSH) - that day's sunlight in kWh/m², independent of array size, the same
figure installers use for a back-of-envelope estimate (system size × PSH × a real-world derate ≈ that day's kWh) - all
read directly off the data, once per active estimate. The smoothing curve is a monotone cubic (an ordinary spline can
dip below zero at dawn and dusk); the math is in `web/js/bell.js`, tested by `tests/js/bell.test.mjs`.

**The request.** PVWatts v8, `timeframe=hourly`, `dataset=nsrdb`, a 1,000 kW (DC) reference array, and these settings from
`pvwatts_sites.manoa` in `config/regions.yaml`: `tilt 0` (flat), `azimuth 180`, `array_type 0`, `module_type 0`, `losses 0`.
The coordinates are a **placeholder**, 21.2984, −157.8174: the median of the centres of 214 named campus buildings from
OpenStreetMap, 130 m from Campus Center. Replace `lat`/`lon` when the real SCEL sensor node's position is known. Everything
PVWatts returns for the hour is kept (`poa`, `dn`, `df`, `dc`, `ac`, `tamb`, `tcell`, `wspd`, `alb`), and so is the whole
`station_info` block: the weather cell it matched (id 17574, centre 21.2900, −157.8200, 76 m elevation), the distance to it
(948 m), the weather source (`NSRDB PSM V3 GOES tmy-2020 3.2.0`) and the solar resource file.

**Why flat and lossless.** A ground sensor measures local sunlight, not a solar farm's output. On a flat surface the
plane-of-array irradiance (`poa`) *is* global horizontal irradiance, so `poa` is the quantity to compare with a flat
sensor. `losses` only derates the power columns (irradiance is identical at 14 and 0), so `losses 0` gives the closest thing to
raw modeled irradiance. Change `tilt` and `azimuth` in the config once the sensor's mounting is confirmed (azimuth does
nothing while `tilt` is 0). Changing any setting is noticed: unlike the Oʻahu profile, a site's cached copy is refetched
automatically when its settings differ from the ones stored with it.

**What to know before comparing.**

* **Prefer `poa`, `dn`, `df` and `dc_w`.** Even with `losses 0` the power model still applies module temperature (cells reach
  about 64 °C) and the inverter model, which clips `ac_w` at its ceiling of 833.3 kW for the raw 1,000 kW reference array
  (its 1.2 DC/AC ratio, the API's default; 3,583 kW once the page scales it to Mānoa's real 4,300 kW): 326 hours a year sit at the cap.
* **The weather is a typical year**, not a real one, from a satellite-derived NSRDB cell about 4 km wide. Month and day are
  positions in a composite year, so compare seasonal averages and distributions, not particular days. The cell cannot see
  Mānoa Valley's local cloud and rain or shade from the ridge, trees and buildings, so differences from a real sensor are not all model error.
* **The whole campus is one cell.** Nine points tested across the campus outline all matched the same cell, so the exact
  coordinate changes the recorded *distance* (0.5 to 1.9 km) but not the data. Its annual sunshine is close to the existing
  Honolulu cell's (2,010 versus 2,036 kWh/m² on a flat surface).
* **Hours** are labeled hour-beginning in Hawaiʻi standard time. The generation windows fit that, but it has not been
  confirmed against NREL's documentation, so check it against the sensor's timestamps.
* **Seasonal averages** (`*_seasonal_averages.csv`, and in the JSON) use the duck curve's exact definition: the hourly mean over
  the Monday-to-Friday days of the 2018 calendar in each season's months (64, 66, 66 and 65 days). Sunlight has no weekday pattern, so
  an all-days average (90 to 92 days) is exported next to it; it is less noisy.
* **Checks.** Before anything is exported the series must be a full year with no negative or impossible irradiance, darkness at
  night (which catches a series shifted by hours), power that never exceeds DC, and sensible yearly sunshine. A series
  that fails is not exported.

**Tests.** `tests/test_site_solar.py` covers the request, everything kept from the response, the additive cache (the
Oʻahu rows come out byte-identical, on the same month/day/hour keys), the plausibility checks, and the seasonal averaging (including a
test that feeds one series through both this code and `compute_duck_curve` and requires the same result). The
Oʻahu-wide PVWatts call is pinned by a regression test in `tests/test_fetch_hourly.py`. Two opt-in tests call the real API:
`RUN_LIVE_TESTS=1 python -m pytest tests/test_site_solar_live.py`.

## Two independent Mānoa estimates

**What it is.** A second, independent estimate of Mānoa's solar power, alongside the PVWatts model above. It shares no
code, cache or output file with `pipeline/`: it starts from the campus's own real ground sensor
(`data/manoa/sensor/sunny_irradiance_2011_2012.csv`, 5-minute irradiance readings, 3 Jan-25 Oct 2012) instead of a
satellite weather API, and turns it into power with [pvlib](https://pvlib-python.readthedocs.io/)'s own implementation
of the PVWatts equations (`pvlib.pvsystem.pvwatts_dc`, `pvlib.inverter.pvwatts`) instead of NREL's hosted model. Build
it with `python -m sensor_model.run` (needs no network or API key; see `sensor_model/` above and `data/manoa/README.md`).

**Why a second estimate.** The PVWatts model answers "what would a typical year of satellite-derived weather produce."
The sensor + pvlib estimate answers "what did the light that actually reached Mānoa in 2012 produce." They share an
array size (4,300 kW, flat, lossless) and electrical assumptions (1.2 DC/AC ratio, 96% inverter efficiency) so they can
be overlaid directly, but they differ in every other way: different years, different clouds, and a real sensor gap
where PVWatts has none (the logger only ran about 6 AM-6 PM, and some months in 2012 - July, November, December -
have no days at all). Where the two curves agree is more convincing than either alone; where they disagree (the real
sensor's winter peak, from a thin 51-day sample, comes out *higher* than the modeled typical year's) is informative
too, not necessarily an error in either.

**The page.** `manoa.html`'s **Estimate** toggle shows either curve alone or both overlaid (color still means season;
a dashed line means the sensor + pvlib estimate, solid means PVWatts). At least one estimate is always shown - the
toggle refuses to switch off the last one. Hours with no sensor data (night, or a thin gap) are left out of that curve
entirely rather than drawn as zero, so the sensor curve's own width is part of what it is telling you.

**Tests.** `tests/test_sensor_model.py` covers the 5-minute-to-hourly averaging (and its completeness threshold), the
pvlib power model (including that its inverter cap matches the PVWatts model's own, at the same reference capacity - a
cross-check that the two estimates truly share their electrical assumptions), the seasonal averaging against real 2012
weekdays, and the web JSON's shape. `tests/browser/run_browser_tests.py`'s Mānoa section covers the toggle itself.

## Adding another island later

1. Copy the `oahu:` block in `config/regions.yaml`, and change the id, `label`, `county`
   (Maui, Hawaii, Kauai) and clear the plant list.
2. Run `python -m pipeline.discover_plants --region maui`. It prints every plant EIA places in
   that county, formatted ready to paste into the `plants:` list.
3. Run `python -m pipeline.run_pipeline`. The new region flows through the cache, the
   calculation and the JSON. Open the page with `?region=maui` (`http://localhost:8000/?region=maui`).
4. For the duck curve, add an `hourly:` block to the new region (site coordinates, its county's
   ComStock id such as `G1500090`, and its `share_of_state`), then run
   `python -m pipeline.run_duck_curve --region maui` and open `duck_curve.html?region=maui`.
   Note that ComStock has county-level files but ResStock (homes) only has state-level ones, which
   is why `share_of_state` exists. `rooftop_share_of_state` can be filled in from the cross-check that
   `run_curtailment` prints (step 5).
5. For curtailment, add a `curtailment:` block to the new region with its sheet name in the totals
   file (Maui: `3E Curtailed Energy Maui`, Lāna‘i: `3F ...`, Hawaiʻi Island: `3G ...`) and its
   heading in the by-reason file (e.g. `Maui County - Maui Division`, `Hawaiʻi Island`); then run
   `python -m pipeline.run_curtailment --region maui`. Kauaʻi is not in Hawaiian Electric's files
   (its utility is separate), so it is not available this way.

## Troubleshooting

| Problem | Fix |
|---|---|
| `EIA_API_KEY is not set` | Create `.env` in the project root with `EIA_API_KEY=...` and **save** the file. |
| `EIA rejected the request (HTTP 403)` | The key is wrong or not yet activated. Check the email from EIA. |
| Page says the data file can't be read | You opened `index.html` directly. Serve it with `python -m http.server` from inside `web/`. |
| Page says Plotly could not be loaded | The page fetches Plotly from a CDN and needs internet access. |
| A new plant is missing from the chart | Run `discover_plants` and add it to `regions.yaml`. |
| `NREL_API_KEY is not set` | Add `NREL_API_KEY=...` to `.env` (free key: <https://developer.nlr.gov/signup/>) and save. |
| `No Tier 1 data cached ... Run run_pipeline first` | The duck curve is calibrated to Tier 1's data. Run `python -m pipeline.run_pipeline` first. |
| `Unexpected layout: ...` from `run_curtailment` | Hawaiian Electric changed its Excel files' layout. The message says which rows or sheet it could not find; adjust `fetch_heco.py` (or the sheet/section names in `config/regions.yaml`). |
| The duck-curve download is slow or interrupted | Just rerun `python -m pipeline.run_duck_curve`; files already downloaded are kept, and each file is retried automatically. |
| `ModuleNotFoundError` | Activate the virtual environment (step 1) and run `pip install -r requirements.txt`. |

## How the pieces fit together (and how to extend them)

```
  Tier 1  EIA plant data ─────────────► renewable_share.json ──► renewable share chart
     │                                      
     ├──► Tier 2  + NREL shapes ────────► duck_curve.json ──────► duck curve chart ──┐
     │                                                                               ├─► Tier 4 battery
  Tier 3  Hawaiian Electric's reported ─► curtailment.json ─────► curtailment charts ─┘   (in the browser)
          curtailment
```

Tier 1's data calibrates Tier 2; Tier 3's reported numbers checked and improved Tier 2's rooftop assumption and
now size Tier 1's dotted rooftop line; the plant pipeline (EIA-860M) feeds the map and sets Tier 4's default
battery. Tier 4 reads Tier 2's hourly curves (plus the fleet and, for scale, Tier 3's totals) and needs no
pipeline of its own. Each
pipeline tier was added without restructuring the earlier ones: new `fetch_*`, `process_*`, `export_*` and
`run_*` modules, new tables in the same cache, a new page, and shared helpers in `web/js/common.js`.

Each new region, whatever the tier, is just another block in `config/regions.yaml`.
