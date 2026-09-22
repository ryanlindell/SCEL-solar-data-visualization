// The Mānoa page: two independent estimates of solar power over a typical day at UH Mānoa, overlaid.
//
// 1. "PVWatts model" - web/data/manoa_solar.json (written by `python -m pipeline.run_site_solar`). PVWatts was run for
//    a hypothetical 1,000 kW reference array lying flat, on a satellite-derived typical year; every number here is
//    scaled up (linearly, per PVWatts' own convention) to Mānoa's real 4,300 kW (4.3 MW) array.
// 2. "Sensor + pvlib" - web/data/manoa_solar_sensor.json (written by `python -m sensor_model.run`). The real ground
//    sensor's measured irradiance (data/manoa/sensor/), run through pvlib's own PVWatts equations for the same
//    4,300 kW array. Shares no code with the PVWatts model; see sensor_model/ for the method.
//
// Nothing here is measured *AC power* at Mānoa (only the sensor's irradiance is a real measurement); this page is
// separate from the Oʻahu pages. Both curves are the data's own hourly averages, smoothed for readability only.

import { baseLayout, loadData, onThemeChange, plotConfig, showError, themeColors, withAlpha } from "./common.js";
import { formatClock, halfPowerSpan, hourRangeLabel, monotoneCubic, sample } from "./bell.js";

const MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const SEASON_COLORS = { winter: "series1", spring: "series3", summer: "series2", fall: "series4" }; // one fixed color per season
const POWER = {
  dc: { field: "dc_w", label: "DC (the panels)", short: "DC", note: "before the inverter" },
  ac: { field: "ac_w", label: "AC (after the inverter)", short: "AC", note: "after the inverter" },
};
const DAY_SETS = { weekdays: "Weekdays", all_days: "All days" };
const ALL = "all"; // the "compare every season" view
const REFERENCE_KW = 1000; // the PVWatts model was run for a reference array this size
const ARRAY_KW = 4300; // Mānoa's real array nameplate (DC), what both estimates are scaled or sized to
const PVWATTS_SCALE = ARRAY_KW / REFERENCE_KW; // PVWatts output scales linearly with capacity, so this scales the reference model up
const RATED_KW = ARRAY_KW; // the array's nameplate size, for the capacity-factor tile

// Two independent estimates of the same array, overlaid. Each converts its own file's watts to kW at the real 4,300
// kW scale (the PVWatts file is a 1,000 kW reference and needs scaling up; the sensor file is already at 4,300 kW),
// and gets its own line style so the two are distinguishable without a second color (color stays fixed to season).
const SOURCES = {
  pvwatts: {
    url: "data/manoa_solar.json",
    label: "PVWatts model",
    short: "PVWatts",
    dash: "solid",
    spanDash: "dot",
    symbol: "circle",
    toKw: (w) => (w / 1000) * PVWATTS_SCALE,
  },
  sensor: {
    url: "data/manoa_solar_sensor.json",
    label: "Sensor + pvlib",
    short: "Sensor",
    dash: "dash",
    spanDash: "dashdot",
    symbol: "diamond",
    toKw: (w) => w / 1000,
  },
};
const DEFAULT_SOURCES = ["pvwatts"]; // the sensor estimate is opt-in: turn it on to compare

const chartDiv = document.getElementById("chart");

const fmtKw = (value) => `${Math.round(value).toLocaleString("en-US")} kW`;
const monthRange = (months) => `${MONTH_NAMES[months[0] - 1]}–${MONTH_NAMES[months[months.length - 1] - 1]}`;
const activeSourceIds = (state) => Object.keys(SOURCES).filter((id) => state.sources[id]);

// ---------------------------------------------------------------------------
// One season's numbers: everything the chart, the tiles and the table need
// ---------------------------------------------------------------------------

/** One season's power curve for one source: the hourly points with data, a smooth line through them (for readability
 * only), and the headline numbers read directly off that data. Hours with no value (a real night, or a sensor gap -
 * see sensor_model's `n_days`) are left out rather than treated as zero, so the sensor estimate's curve only spans
 * the hours it actually has data for. Returns null if the source has fewer than two such hours to draw. */
function describe(rows, power, toKw) {
  const field = POWER[power].field;
  const present = rows.filter((r) => r[field] != null);
  if (present.length < 2) return null;
  const sorted = [...present].sort((a, b) => a.hour - b.hour);
  const centers = sorted.map((r) => r.hour + 0.5); // an hourly average describes the whole hour, so plot it at the middle
  const values = sorted.map((r) => toKw(r[field]));
  const smooth = monotoneCubic(centers, values);
  const peakIndex = values.indexOf(Math.max(...values));
  const peak = values[peakIndex];
  const span = halfPowerSpan(smooth, centers[0], centers[centers.length - 1], peak);
  const energy = values.reduce((a, b) => a + b, 0) / 1000; // kW x 1 h summed over the day, in MWh
  const capacityFactorPct = (100 * (energy * 1000)) / (RATED_KW * 24); // average power over the day, as a share of nameplate
  // Peak sun hours: that day's sunlight (kWh/m2) expressed as hours at the standard-test 1,000 W/m2 - independent of
  // array size, so it is the same for both estimates' underlying sunlight regardless of the DC/AC toggle. This is the
  // number installers use for a back-of-envelope estimate: system size (kW) x PSH x a real-world derate ~ that day's energy (kWh).
  const pshHours = sorted.reduce((a, r) => a + (r.poa_wm2 ?? 0), 0) / 1000;
  return { sorted, centers, values, smooth, peak, peakIndex, peakHour: sorted[peakIndex].hour, span, energy, capacityFactorPct, pshHours };
}

/** The curve to draw, tapered to zero at either edge if the real data doesn't already reach it (the sensor's logger
 * only ran roughly 6 AM-6 PM, so its last real reading most hours is still several hundred kW, not the true dawn/dusk
 * zero - without this the line just stops mid-air instead of settling to the axis). Adds a synthetic zero point one
 * hour beyond the real data on that side purely for this drawn line; `describe`'s own numbers (peak, energy, span,
 * capacity factor) are computed from the real hours only and never see this point. */
function displayCurve(d) {
  const centers = [...d.centers];
  const values = [...d.values];
  if (values[0] > 0) {
    centers.unshift(centers[0] - 1);
    values.unshift(0);
  }
  if (values[values.length - 1] > 0) {
    centers.push(centers[centers.length - 1] + 1);
    values.push(0);
  }
  return { smooth: monotoneCubic(centers, values), from: centers[0], to: centers[centers.length - 1] };
}

// ---------------------------------------------------------------------------
// The chart
// ---------------------------------------------------------------------------

function xAxis(t) {
  const phone = window.innerWidth < 600;
  const ticks = phone ? [6, 12, 18] : [6, 9, 12, 15, 18];
  const names = { 6: "6 AM", 9: "9 AM", 12: "12 PM", 15: "3 PM", 18: "6 PM" };
  return {
    type: "linear",
    range: [5, 20],
    tickmode: "array",
    tickvals: ticks,
    ticktext: ticks.map((h) => names[h]),
    showgrid: false,
    linecolor: t.axis,
    tickcolor: t.axis,
    fixedrange: true,
  };
}

function draw(state) {
  const t = themeColors();
  const { season, power, daySet } = state;
  const compare = season === ALL;
  const active = activeSourceIds(state);
  const seasonIds = compare ? Object.keys(SOURCES.pvwatts.data.seasons) : [season];
  const yMax = state.yMax[power];
  const traces = [];
  const shapes = [];
  const annotations = [];

  for (const sid of seasonIds) {
    const color = t[SEASON_COLORS[sid]];
    const label = SOURCES.pvwatts.data.seasons[sid].label;

    // Computed once per source so the half-power labels below can be placed by which line actually sits higher,
    // not by a fixed source order (PVWatts' half-power height isn't always above the sensor's, or vice versa).
    const descs = Object.fromEntries(active.map((id) => [id, describe(SOURCES[id].rows[daySet][sid], power, SOURCES[id].toKw)]));
    const higherHalfPowerSource = active
      .filter((id) => descs[id]?.span)
      .reduce((a, b) => (descs[b].peak > descs[a].peak ? b : a), active.find((id) => descs[id]?.span));

    active.forEach((sourceId, sourceIndex) => {
      const src = SOURCES[sourceId];
      const d = descs[sourceId];
      if (!d) return; // this source has no usable data for this season/day-set combination

      const traceName = compare ? (active.length > 1 ? `${label} · ${src.short}` : label) : src.label;
      const display = displayCurve(d);
      const dense = sample(display.smooth, display.from, display.to, Math.round((display.to - display.from) * 12));

      // The smooth line through the source's own hourly averages (no hover of its own: the markers below are the real numbers).
      traces.push({
        name: traceName,
        x: dense.x,
        y: dense.y,
        mode: "lines",
        line: { color, width: 2.5, dash: src.dash },
        ...(!compare && sourceIndex === 0 ? { fill: "tozeroy", fillcolor: withAlpha(color, 0.2) } : {}),
        hoverinfo: "skip",
      });
      // The hourly averages themselves, only for hours with sun (a zero reading at dawn/dusk is real, but adds clutter).
      const sunny = d.sorted.map((r, i) => ({ r, i })).filter(({ i }) => d.values[i] > 0);
      traces.push({
        name: `${traceName} hourly averages`,
        showlegend: false,
        x: sunny.map(({ i }) => d.centers[i]),
        y: sunny.map(({ i }) => d.values[i]),
        customdata: sunny.map(({ r }) => [
          hourRangeLabel(r.hour), src.toKw(r.dc_w), src.toKw(r.ac_w), r.poa_wm2, label, src.label, r.n_days ?? null,
        ]),
        mode: "markers",
        marker: {
          size: sunny.map(({ i }) => (!compare && i === d.peakIndex ? 11 : 7)),
          symbol: src.symbol,
          color,
          line: { color: t.surface, width: 1.5 },
          opacity: 1,
        },
        hovertemplate: compare
          ? `<b>${label}${active.length > 1 ? " · " + src.short : ""}</b> · %{customdata[0]}<br><b>%{y:,.0f} kW</b> ${POWER[power].short} power<extra></extra>`
          : "<b>%{customdata[0]}</b> · <b>%{customdata[5]}</b><br>DC power <b>%{customdata[1]:,.0f} kW</b> · AC %{customdata[2]:,.0f} kW<br>" +
            "Sunlight on the flat panels %{customdata[3]:,.0f} W/m²" +
            (sourceId === "sensor" ? "<br>Averaged over %{customdata[6]} days" : "") +
            "<extra></extra>",
      });

      if (!compare) {
        // How many hours the day stays at half its peak power or more, marked directly on the curve. With two
        // estimates active their half-power heights often sit close together, so each source gets its own dash
        // pattern and the labels are pushed apart (one above its line, one below) instead of both drifting upward
        // into the same spot.
        if (d.span) {
          shapes.push({
            type: "line", xref: "x", yref: "y", x0: d.span.start, x1: d.span.end, y0: d.peak / 2, y1: d.peak / 2,
            line: { color, width: 1.5, dash: src.spanDash },
          });
          annotations.push({
            x: (d.span.start + d.span.end) / 2, y: d.peak / 2, yshift: sourceId === higherHalfPowerSource ? 14 : -14,
            xref: "x", yref: "y", showarrow: false,
            text: `${active.length > 1 ? src.short + ": " : ""}${d.span.width.toFixed(1)} hours at half power or more`,
            font: { size: 12, color: t.textPrimary },
            bgcolor: withAlpha(t.surface, 0.85), borderpad: 2,
          });
        }
        annotations.push({
          x: d.centers[d.peakIndex], y: d.peak, xref: "x", yref: "y", yshift: 30, showarrow: false, align: "center",
          text: `<b>${fmtKw(d.peak)}</b>${active.length > 1 ? " " + src.short : ""}<br>peak, ${hourRangeLabel(d.peakHour)}`,
          font: { size: 12, color: t.textPrimary },
          bgcolor: withAlpha(t.surface, 0.85), borderpad: 2,
        });
      }
    });
  }

  const layout = {
    ...baseLayout(t),
    showlegend: true,
    legend: { orientation: "h", x: 0, y: 1.02, yanchor: "bottom", font: { size: 12 } },
    margin: { l: 64, r: 16, t: 56, b: 44 },
    hovermode: "closest",
    hoverdistance: 14,
    xaxis: xAxis(t),
    yaxis: {
      title: { text: `Power from Mānoa's ${ARRAY_KW.toLocaleString("en-US")} kW array (${POWER[power].short}, kW)`, standoff: 8 },
      tickformat: ",.0f",
      range: [0, yMax],
      fixedrange: true,
      gridcolor: t.grid,
      gridwidth: 1,
      zeroline: true,
      zerolinecolor: t.axis,
    },
    shapes,
    annotations,
  };
  return Plotly.react(chartDiv, traces, layout, { ...plotConfig, displayModeBar: false });
}

// ---------------------------------------------------------------------------
// Header, controls, stat tiles, table
// ---------------------------------------------------------------------------

function renderHeader(state) {
  const { season, daySet } = state;
  const data = SOURCES.pvwatts.data;
  const who = daySet === "weekdays" ? "weekday" : "day";
  const active = activeSourceIds(state);
  const showing = active.length === Object.keys(SOURCES).length ? "Both estimates shown, overlaid." : `Showing: ${SOURCES[active[0]]?.label ?? "none"}.`;
  document.getElementById("subtitle").textContent =
    (season === ALL
      ? `All four seasons compared: the average ${who} of each, for the ${ARRAY_KW.toLocaleString("en-US")} kW of panels lying flat at UH Mānoa.`
      : `The average ${who} in ${data.seasons[season].label.toLowerCase()} (${monthRange(data.seasons[season].months)}), ` +
        `for the ${ARRAY_KW.toLocaleString("en-US")} kW of panels lying flat at UH Mānoa.`) +
    ` ${showing}`;
  chartDiv.setAttribute(
    "aria-label",
    season === ALL
      ? "Chart comparing the daily solar power curves of all four seasons at UH Mānoa."
      : `Chart of solar power over the average ${who} in ${data.seasons[season].label.toLowerCase()} at UH Mānoa.`
  );
}

function radio(box, options, current, onSelect) {
  box.replaceChildren();
  for (const [id, label] of options) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.setAttribute("role", "radio");
    btn.setAttribute("aria-checked", String(id === current));
    btn.textContent = label;
    btn.addEventListener("click", () => onSelect(id));
    box.append(btn);
  }
}

/** A group of independent on/off toggles (not mutually exclusive, unlike `radio`). Refuses to turn off the last one
 * still on, so the chart is never left with nothing to show. */
function checkboxGroup(box, options, current, onToggle) {
  box.replaceChildren();
  for (const [id, label] of options) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.setAttribute("role", "checkbox");
    btn.setAttribute("aria-checked", String(current[id]));
    btn.textContent = label;
    btn.addEventListener("click", () => {
      const onCount = options.filter(([oid]) => current[oid]).length;
      if (current[id] && onCount <= 1) return; // keep at least one estimate visible
      onToggle(id, !current[id]);
    });
    box.append(btn);
  }
}

function renderControls(state, onChange) {
  const seasons = Object.entries(SOURCES.pvwatts.data.seasons).map(([id, s]) => [id, `${s.label} (${monthRange(s.months)})`]);
  radio(document.getElementById("season-controls"), [...seasons, [ALL, "Compare all four"]], state.season, (id) => onChange({ season: id }));
  radio(document.getElementById("power-controls"), Object.entries(POWER).map(([id, p]) => [id, p.label]), state.power, (id) => onChange({ power: id }));
  radio(document.getElementById("days-controls"), Object.entries(DAY_SETS), state.daySet, (id) => onChange({ daySet: id }));
  checkboxGroup(
    document.getElementById("estimate-controls"),
    Object.entries(SOURCES).map(([id, s]) => [id, s.label]),
    state.sources,
    (id, on) => onChange({ sources: { ...state.sources, [id]: on } })
  );
}

function tile(label, value, sub) {
  const el = document.createElement("div");
  el.className = "stat";
  for (const [cls, text] of [["stat-label", label], ["stat-value", value], ["stat-sub", sub]]) {
    const part = document.createElement("div");
    part.className = cls;
    part.textContent = text;
    el.append(part);
  }
  return el;
}

function renderStats(state) {
  const { season, power, daySet } = state;
  const data = SOURCES.pvwatts.data;
  const active = activeSourceIds(state);
  const box = document.getElementById("stats");
  box.replaceChildren();
  // With both estimates shown for one season, each contributes the same five tiles (peak, energy, width, capacity
  // factor, peak sun hours) in the same order: force 5 columns so they land in two aligned rows instead of wrapping
  // wherever they fit.
  box.classList.toggle("paired", season !== ALL && active.length > 1);
  if (season === ALL) {
    for (const sid of Object.keys(data.seasons)) {
      for (const sourceId of active) {
        const src = SOURCES[sourceId];
        const d = describe(src.rows[daySet][sid], power, src.toKw);
        const label = active.length > 1 ? `${data.seasons[sid].label} (${src.short})` : data.seasons[sid].label;
        box.append(
          d
            ? tile(label, `${fmtKw(d.peak)} peak`, `${d.energy.toFixed(1)} MWh a day · ${d.pshHours.toFixed(1)} PSH · ${d.span ? d.span.width.toFixed(1) : "–"} h wide`)
            : tile(label, "No data", "this source has no usable hours for this season")
        );
      }
    }
    return;
  }
  for (const sourceId of active) {
    const src = SOURCES[sourceId];
    const d = describe(src.rows[daySet][season], power, src.toKw);
    const lbl = (text) => (active.length > 1 ? `${text} (${src.short})` : text);
    if (!d) {
      box.append(tile(lbl("Peak power"), "No data", `${src.label} has no usable hours for this view`));
      continue;
    }
    box.append(
      tile(lbl("Peak power"), fmtKw(d.peak), `in the ${hourRangeLabel(d.peakHour)} hour, ${POWER[power].note}`),
      tile(lbl("Energy in a day"), `${d.energy.toFixed(1)} MWh`, `like running at the full ${(ARRAY_KW / 1000).toFixed(1)} MW for ${d.energy.toFixed(1)} hours`),
      tile(lbl("Width at half power"), d.span ? `${d.span.width.toFixed(1)} hours` : "–", d.span ? `${formatClock(d.span.start)} to ${formatClock(d.span.end)}` : ""),
      tile(lbl("Capacity factor"), `${d.capacityFactorPct.toFixed(1)}%`, `average power over the day ÷ the array's ${ARRAY_KW.toLocaleString("en-US")} kW rating`),
      tile(lbl("Peak sun hours"), `${d.pshHours.toFixed(1)} PSH`, `system size (kW) × PSH × a real-world derate ≈ that day's energy (kWh)`)
    );
  }
}

function renderTable(state) {
  const { season, power, daySet } = state;
  const data = SOURCES.pvwatts.data;
  const active = activeSourceIds(state);
  const table = document.getElementById("data-table");
  table.replaceChildren();
  const head = table.createTHead().insertRow();
  const seasonIds = Object.keys(data.seasons);
  const fmt = (v) => (v == null ? "–" : Math.round(v).toLocaleString("en-US"));
  const columns =
    season === ALL
      ? ["Hour", ...seasonIds.flatMap((sid) => active.map((id) => `${data.seasons[sid].label}${active.length > 1 ? " · " + SOURCES[id].short : ""} (${POWER[power].short} kW)`))]
      : ["Hour", ...active.flatMap((id) => [`Sunlight${active.length > 1 ? " · " + SOURCES[id].short : ""} (W/m²)`, `DC power${active.length > 1 ? " · " + SOURCES[id].short : ""} (kW)`, `AC power${active.length > 1 ? " · " + SOURCES[id].short : ""} (kW)`])];
  for (const label of columns) {
    const th = document.createElement("th");
    th.scope = "col";
    th.textContent = label;
    head.append(th);
  }
  const body = table.createTBody();
  for (let hour = 0; hour < 24; hour++) {
    const tr = body.insertRow();
    tr.insertCell().textContent = hourRangeLabel(hour);
    if (season === ALL) {
      for (const sid of seasonIds) {
        for (const id of active) {
          const src = SOURCES[id];
          const r = src.rows[daySet][sid][hour];
          const v = r[POWER[power].field];
          tr.insertCell().textContent = fmt(v == null ? null : src.toKw(v));
        }
      }
    } else {
      for (const id of active) {
        const src = SOURCES[id];
        const r = src.rows[daySet][season][hour];
        [r.poa_wm2, r.dc_w == null ? null : src.toKw(r.dc_w), r.ac_w == null ? null : src.toKw(r.ac_w)].forEach(
          (v) => (tr.insertCell().textContent = fmt(v))
        );
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Words
// ---------------------------------------------------------------------------

function addNote(list, lead, text) {
  const li = document.createElement("li");
  const strong = document.createElement("strong");
  strong.textContent = `${lead} `;
  li.append(strong, document.createTextNode(text));
  list.append(li);
}

function renderText(data, sensorData) {
  const a = data.assumptions;
  const cell = data.site.weather_cell;
  const q = data.site.query;
  const acShare = data.annual_summary.ac_over_dc_energy;
  const box = document.getElementById("explainer-body");
  const p = (text) => {
    const el = document.createElement("p");
    el.textContent = text;
    box.append(el);
  };
  p(
    "Sunshine is strongest around noon and fades toward morning and evening, so the power a solar panel makes over a day " +
      `rises through the morning, peaks near midday and falls through the afternoon. This page shows that curve for the ${ARRAY_KW.toLocaleString("en-US")} ` +
      `kilowatts (${(ARRAY_KW / 1000).toFixed(1)} megawatts) of panels at the University of Hawaiʻi at Mānoa, averaged over the days of a season.`
  );
  p(
    "There are two independent estimates, and the “Estimate” toggle above the chart shows either or both at once: " +
      "the PVWatts model (a satellite-derived typical year, run through NREL's hosted power model) and Sensor + pvlib " +
      "(the campus's real ground sensor, run through pvlib's own implementation of the same PVWatts equations). They " +
      "share an array size and electrical assumptions, but not a data source or a code path, so where they agree is " +
      "more convincing than either alone, and where they disagree is informative too."
  );
  p(
    "The dots are each estimate's own hourly averages; the line is a smooth curve drawn through those exact points, for " +
      "readability, not a fitted or idealized shape. Every number in the tiles below (peak power, the day's energy, how " +
      "long output stays above half its peak, the capacity factor, and peak sun hours) is read directly off that data."
  );
  p(
    "“Peak sun hours” (PSH) is that day's total sunlight, in kWh per square metre, written as the number of hours the " +
      "sun would need to shine at its standard full-strength intensity (1,000 W/m²) to deliver that much energy. It is " +
      "the number solar installers actually use for a back-of-envelope estimate - a system's size in kW times its PSH " +
      "times a real-world derate (commonly 75-85%, for wiring, heat and dust) is roughly that day's output in kWh - so " +
      "it is a useful sanity check on a claimed output figure, independent of any particular array's size."
  );
  p(
    "“Width at half power” is how many hours the panels make at least half of their peak that day: a measure of how broad " +
      "the productive part of the day is. Summer days are taller and broader than winter ones because the days are longer " +
      "and the sun is higher in the sky."
  );

  const list = document.getElementById("notes");
  addNote(list, "A model, not a measurement.", "The PVWatts curve comes from NREL's PVWatts model, which turns a satellite-derived typical year of weather into the power a solar array would make. No panel at Mānoa was measured; only the sensor's irradiance (used by the other estimate) is a real measurement.");
  addNote(
    list,
    "What the array is.",
    `Both estimates use ${ARRAY_KW.toLocaleString("en-US")} kW (DC), Mānoa's real nameplate capacity, lying flat (tilt ${a.tilt_deg}°), with no system losses (${a.losses_pct}%). ` +
      "A real installation would be tilted and would lose part of its output to wiring, dust and heat, so treat the size of the curve as an ideal upper reference; " +
      "the timing and shape are the point."
  );
  addNote(
    list,
    "DC and AC.",
    `DC is what the panels produce; AC is what is left after the inverter. Both models cap AC at ${fmtKw(SOURCES.pvwatts.toKw(data.annual_summary.ac_max_w))}, so on the sunniest hours AC is flattened. ` +
      (acShare ? `Over a year of the PVWatts model AC is ${(acShare * 100).toFixed(1)}% of DC. ` : "") +
      "The AC curve is a little lower and rounder than DC."
  );
  addNote(
    list,
    "Scaled from a smaller reference model.",
    `PVWatts was run for a ${REFERENCE_KW.toLocaleString("en-US")} kW (DC) reference array; every PVWatts number on this page is scaled up ×${PVWATTS_SCALE.toFixed(1)} to match ` +
      `Mānoa's real ${ARRAY_KW.toLocaleString("en-US")} kW capacity. Peak power and daily energy both scale in direct proportion to installed capacity, as long as ` +
      "the real array's inverters are sized in the same DC-to-AC proportion as the model's. Capacity factor and the width at half power do not change with size " +
      "at all: they describe the shape of the day, not how big the array is."
  );
  addNote(
    list,
    "Where.",
    `${data.site.label}. ${data.site.coordinates_are_placeholder ? "The coordinates (" + q.lat.toFixed(4) + ", " + q.lon.toFixed(4) + ") are a placeholder for the real sensor's. " : ""}` +
      `The PVWatts weather comes from one satellite grid cell (id ${cell.nsrdb_cell_id}), ${(cell.distance_from_query_m / 1000).toFixed(2)} km from that point and several kilometres wide, ` +
      "so it cannot see Mānoa Valley's local clouds and rain or shade from the ridge, trees and buildings; the sensor estimate has none of that problem, since it measures the light that actually arrived."
  );
  addNote(
    list,
    "A typical year, versus two real seasons.",
    "The PVWatts weather is a composite “typical” year, not any real one, so read its curve as what a normal day in the season looks like, not a particular day. " +
      "Weekdays (Monday to Friday) is the definition the Oʻahu duck-curve page uses; sunlight has no weekday pattern, so “All days” " +
      "averages more days and is a little smoother."
  );
  addNote(
    list,
    "Time.",
    `${data.meta.time_zone}. Each dot is the average for the hour that begins at that time (the 12–1 PM dot sits at 12:30). This labeling is assumed for both estimates, not confirmed against NREL's or the sensor logger's documentation.`
  );

  if (sensorData) {
    const c = sensorData.coverage;
    const loggedFrom = formatClock(c.hours_with_any_data[0]);
    const loggedTo = formatClock(c.hours_with_any_data[c.hours_with_any_data.length - 1] + 1);
    addNote(
      list,
      "The sensor's real coverage.",
      `${c.date_min} to ${c.date_max} (${c.days_with_data} days with any usable hour), and only from ${loggedFrom} to ${loggedTo}: ` +
        "the logger did not run at night, so those hours are a real absence, not a gap. Some months have no days at all in this " +
        "stretch (see the main README), so winter and fall lean on fewer days than summer and spring; each hour's average comes " +
        "from a specific number of days, recorded as “n_days” in the underlying CSV/JSON if you want to check how thin an hour is."
    );
    addNote(
      list,
      "No temperature model.",
      "The sensor logs irradiance only, not module temperature or wind, so pvlib's cell temperature is held at the 25 degC standard-test-condition value: a simplification, " +
        "not a real thermal derate, the same simplification the PVWatts side makes with losses 0."
    );
  }

  document.getElementById("source").textContent =
    `Source: ${data.meta.source}; a ${a.system_capacity_kw_dc.toLocaleString("en-US")} kW (DC) reference array scaled ×${PVWATTS_SCALE.toFixed(1)} to Mānoa's ${ARRAY_KW.toLocaleString("en-US")} kW capacity, tilt ${a.tilt_deg}°, azimuth ${a.azimuth_deg}°, losses ${a.losses_pct}%. ` +
    (sensorData ? `Sensor + pvlib source: ${sensorData.meta.source}. ` : "") +
    `Full data in data/manoa/ (PVWatts) and data/manoa/sensor_pvlib/ (sensor + pvlib), alongside the raw sensor readings in data/manoa/sensor/. ` +
    `Data refreshed ${data.meta.generated_at.slice(0, 10)}. Separate from the Oʻahu grid pages.`;
}

// ---------------------------------------------------------------------------
// Start-up
// ---------------------------------------------------------------------------

function groupRows(rows) {
  // rows[daySet][season] = 24 hourly rows
  const grouped = {};
  for (const r of rows) ((grouped[r.day_set] ??= {})[r.season] ??= []).push(r);
  for (const bySeason of Object.values(grouped)) for (const list of Object.values(bySeason)) list.sort((a, b) => a.hour - b.hour);
  return grouped;
}

async function main() {
  if (typeof Plotly === "undefined") {
    throw new Error("The Plotly library could not be loaded (it is fetched from cdn.plot.ly, so this needs internet access).");
  }

  // Both estimates are always loaded (so toggling is instant and offline-safe); which ones are drawn is a view choice.
  const [pvwattsData, sensorData] = await Promise.all(Object.values(SOURCES).map((s) => loadData(s.url)));
  SOURCES.pvwatts.data = pvwattsData;
  SOURCES.pvwatts.rows = groupRows(pvwattsData.rows);
  SOURCES.sensor.data = sensorData;
  SOURCES.sensor.rows = groupRows(sensorData.rows);

  // One fixed height for every view of a power type, so switching seasons or toggling a source compares like with like.
  const yMax = {};
  for (const [id, p] of Object.entries(POWER)) {
    const values = Object.values(SOURCES).flatMap((src) => src.data.rows.map((r) => (r[p.field] == null ? 0 : src.toKw(r[p.field]))));
    yMax[id] = Math.ceil((Math.max(...values) * 1.2) / 100) * 100;
  }

  // ?season=<id|all>, ?power=dc|ac, ?days=weekdays|all_days and ?sources=pvwatts,sensor choose what to show.
  const params = new URLSearchParams(location.search);
  const richest = Object.keys(pvwattsData.seasons).reduce((a, b) =>
    describe(SOURCES.pvwatts.rows.weekdays[b], "dc", SOURCES.pvwatts.toKw).energy >
    describe(SOURCES.pvwatts.rows.weekdays[a], "dc", SOURCES.pvwatts.toKw).energy
      ? b
      : a
  );
  const requestedSources = (params.get("sources") ?? "").split(",").filter((id) => id in SOURCES);
  const sourceList = requestedSources.length ? requestedSources : DEFAULT_SOURCES;
  const state = {
    yMax,
    season: params.get("season") === ALL || pvwattsData.seasons[params.get("season")] ? params.get("season") : richest,
    power: POWER[params.get("power")] ? params.get("power") : "dc",
    daySet: DAY_SETS[params.get("days")] ? params.get("days") : "weekdays",
    sources: Object.fromEntries(Object.keys(SOURCES).map((id) => [id, sourceList.includes(id)])),
  };

  const refresh = () => {
    renderHeader(state);
    renderControls(state, (change) => {
      Object.assign(state, change);
      refresh();
    });
    renderStats(state);
    renderTable(state);
    return draw(state);
  };

  renderText(pvwattsData, sensorData);
  await refresh();
  onThemeChange(() => draw(state));
  window.__manoa = state; // exposed for the browser tests
}

main().catch((err) => showError(chartDiv, err.message));
