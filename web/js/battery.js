// Tier 4: battery dispatch simulation. Move the sliders and the page re-runs the optimizer in
// dispatch.js on the modeled typical weekday from the duck curve (web/data/duck_curve.json).
// It is a what-if simulation, not a forecast. The curtailment figures from Tier 3
// (web/data/curtailment.json) are shown only for scale.

import { baseLayout, hourTickValues, loadData, onThemeChange, plotConfig, showError, themeColors, withAlpha } from "./common.js";
import { simulateDispatch } from "./dispatch.js";

const DUCK_URL = "data/duck_curve.json";
const CURTAILMENT_URL = "data/curtailment.json";
const PLANTS_URL = "data/plants.json";
const MINUS = "−";

// The power and energy defaults below are only fallbacks: when data/plants.json is available,
// applyFleetDefaults() replaces them with Oʻahu's real battery fleet.
const SLIDERS = [
  {
    id: "power",
    label: "Battery power",
    unit: "MW",
    min: 0,
    max: 600,
    step: 1, // fine enough that the fleet's total (e.g. 326.4 MW, rounded) is a valid slider position
    value: 185,
    help: "How fast it can charge or discharge. The default is the rated power of Kapolei Energy Storage, Oʻahu's largest battery (185 MW, per EIA).",
  },
  {
    id: "energy",
    label: "Battery energy",
    unit: "MWh",
    min: 0,
    max: 2400,
    step: 5, // same reason: the default must be a position the slider can actually land on
    value: 555,
    help: "How much it can store. The default is 3 hours at full power.",
  },
  {
    id: "efficiency",
    label: "Round-trip efficiency",
    unit: "%",
    min: 60,
    max: 98,
    step: 1,
    value: 85,
    help: "The share of the energy put in that comes back out.",
  },
  {
    id: "solar",
    label: "More utility-scale solar",
    unit: "%",
    min: 0,
    max: 300,
    step: 10,
    value: 0,
    help: "New solar farms, as a percentage of today's, with the same daily shape. More solar deepens the midday dip.",
  },
  {
    id: "floor",
    label: "Fossil minimum output (illustrative)",
    unit: "MW",
    min: 0,
    max: 600,
    step: 10,
    value: 300,
    help: "Fossil plants must keep running at least this much for grid stability. Solar that would push below it is curtailed unless the battery absorbs it. An assumption to explore, not Hawaiian Electric's figure.",
  },
];

/**
 * Point the power and energy sliders at Oʻahu's real battery fleet (from EIA-860M, see the pipeline's
 * plants step). Defaults are snapped to the slider's step, so touching a slider never silently changes them.
 */
function applyFleetDefaults(fleet) {
  if (!fleet || !fleet.power_mw || !fleet.energy_mwh) return;
  const spec = (id) => SLIDERS.find((s) => s.id === id);
  const snap = (value, step) => Math.round(value / step) * step;
  spec("power").value = snap(fleet.power_mw, spec("power").step);
  spec("energy").value = snap(fleet.energy_mwh, spec("energy").step);
  spec("power").help =
    `How fast it can charge or discharge. The default is the combined rating of the ${fleet.units.length} batteries ` +
    `now on Oʻahu (${fleet.power_mw.toFixed(1)} MW, per EIA-860M), acting as one.`;
  spec("energy").help =
    `How much it can store. The default is those batteries' combined storage (${Math.round(fleet.energy_mwh).toLocaleString("en-US")} MWh; ` +
    "see the explainer above for a correction to one battery's figure).";
}

const netDiv = document.getElementById("chart-net");
const batteryDiv = document.getElementById("chart-battery");

/** 0 -> "12 AM", 13 -> "1 PM". Hours are the hour *beginning*. */
function hourLabel(h) {
  if (h === 0) return "12 AM";
  if (h < 12) return `${h} AM`;
  if (h === 12) return "12 PM";
  return `${h - 12} PM`;
}

const fmtMw = (value) => `${Math.round(value).toLocaleString("en-US")} MW`;
const fmtMwh = (value) => `${Math.round(value).toLocaleString("en-US")} MWh`;
const signed = (value, unit) => {
  const rounded = Math.round(value);
  if (rounded === 0) return `no change`;
  return `${rounded < 0 ? MINUS : "+"}${Math.abs(rounded).toLocaleString("en-US")} ${unit}`;
};

// ---------------------------------------------------------------------------
// Running the simulation
// ---------------------------------------------------------------------------

function simulate(state) {
  const rows = state.rowsBySeason[state.season];
  const p = state.params;
  // More utility solar lowers net load by scaling today's utility-solar shape.
  const net = rows.map((r) => r.net_load_mw - (p.solar / 100) * r.utility_solar_mw);
  return simulateDispatch({
    net,
    power: p.power,
    energy: p.energy,
    efficiency: p.efficiency / 100,
    floor: p.floor,
  });
}

// ---------------------------------------------------------------------------
// Charts
// ---------------------------------------------------------------------------

function drawNet(state, result) {
  const t = themeColors();
  const x = result.hours.map((h) => hourLabel(h.hour));
  const { floor } = state.params;
  const low = result.hours[result.after.middayLowHour];
  const peak = result.hours[result.after.eveningPeakHour];

  const traces = [
    {
      name: "No battery",
      x,
      y: result.hours.map((h) => h.netBefore),
      mode: "lines",
      line: { color: t.muted, width: 2, dash: "dot" },
      hovertemplate: "<b>%{y:,.0f} MW</b> without the battery<extra></extra>",
    },
    {
      name: "With battery",
      x,
      y: result.hours.map((h) => h.netAfter),
      mode: "lines",
      line: { color: t.series1, width: 2 },
      fill: "tonexty",
      fillcolor: withAlpha(t.series1, 0.14),
      hovertemplate: "<b>%{y:,.0f} MW</b> with the battery<extra></extra>",
    },
    {
      x: [hourLabel(low.hour), hourLabel(peak.hour)],
      y: [low.netAfter, peak.netAfter],
      mode: "markers",
      marker: { size: 9, color: t.series1, line: { color: t.surface, width: 2 } },
      hoverinfo: "skip",
      showlegend: false,
    },
  ];

  const annotations = [
    {
      x: hourLabel(low.hour),
      y: low.netAfter,
      // Above the point: the no-battery line often dips below the with-battery one, so below is crowded.
      text: `<b>${fmtMw(low.netAfter)}</b><br>midday low`,
      showarrow: false,
      yshift: 30,
      font: { size: 12, color: t.textPrimary },
    },
    {
      // The curve falls away after the peak, so the label sits in open space with a leader line.
      x: hourLabel(peak.hour),
      y: peak.netAfter,
      text: `<b>${fmtMw(peak.netAfter)}</b><br>evening peak`,
      showarrow: true,
      arrowhead: 0,
      arrowwidth: 1,
      arrowcolor: t.muted,
      standoff: 6,
      ax: 54,
      ay: 78,
      align: "left",
      font: { size: 12, color: t.textPrimary },
    },
  ];
  const shapes = [];
  if (floor > 0) {
    shapes.push({
      type: "line",
      xref: "paper",
      x0: 0,
      x1: 1,
      yref: "y",
      y0: floor,
      y1: floor,
      line: { color: t.textSecondary, width: 1, dash: "dash" },
    });
    annotations.push({
      xref: "paper",
      x: 0.005,
      xanchor: "left",
      y: floor,
      yanchor: "bottom",
      text: "Fossil minimum output",
      showarrow: false,
      font: { size: 11, color: t.textSecondary },
    });
  }

  const layout = {
    ...baseLayout(t),
    showlegend: true,
    legend: { orientation: "h", x: 0, y: 1.02, yanchor: "bottom", traceorder: "normal", font: { size: 12 } },
    margin: { l: 64, r: 16, t: 40, b: 40 },
    hovermode: "x unified",
    xaxis: {
      type: "category",
      tickmode: "array",
      tickvals: hourTickValues().map(hourLabel),
      showgrid: false,
      linecolor: t.axis,
      tickcolor: t.axis,
      fixedrange: true,
    },
    yaxis: {
      title: { text: "Megawatts (MW)", standoff: 8 },
      tickformat: ",.0f",
      range: [0, state.yMax],
      fixedrange: true,
      gridcolor: t.grid,
      gridwidth: 1,
      zeroline: true,
      zerolinecolor: t.axis,
    },
    shapes,
    annotations,
  };
  return Plotly.react(netDiv, traces, layout, { ...plotConfig, displayModeBar: false });
}

function drawBattery(result) {
  const t = themeColors();
  const x = result.hours.map((h) => hourLabel(h.hour));
  const gap = { line: { color: t.surface, width: 1 } };
  const layout = {
    ...baseLayout(t),
    showlegend: true,
    legend: { orientation: "h", x: 0, y: 1.02, yanchor: "bottom", traceorder: "normal", font: { size: 12 } },
    margin: { l: 64, r: 16, t: 40, b: 40 },
    barmode: "relative",
    hovermode: "x unified",
    xaxis: {
      type: "category",
      tickmode: "array",
      tickvals: hourTickValues().map(hourLabel),
      showgrid: false,
      linecolor: t.axis,
      tickcolor: t.axis,
      fixedrange: true,
    },
    yaxis: {
      title: { text: "Megawatts (MW)", standoff: 8 },
      tickformat: ",.0f",
      fixedrange: true,
      gridcolor: t.grid,
      gridwidth: 1,
      zeroline: true,
      zerolinecolor: t.axis,
    },
  };
  return Plotly.react(
    batteryDiv,
    [
      {
        type: "bar",
        name: "Charging",
        x,
        y: result.hours.map((h) => h.charge),
        marker: { color: t.series3, ...gap },
        hovertemplate: "<b>%{y:,.0f} MW</b> charging<extra></extra>",
      },
      {
        type: "bar",
        name: "Solar curtailed",
        x,
        y: result.hours.map((h) => h.curtailedAfter),
        marker: { color: t.series2, ...gap },
        hovertemplate: "<b>%{y:,.0f} MW</b> solar curtailed<extra></extra>",
      },
      {
        type: "bar",
        name: "Discharging",
        x,
        y: result.hours.map((h) => -h.discharge),
        customdata: result.hours.map((h) => h.discharge),
        marker: { color: t.series4, ...gap },
        hovertemplate: "<b>%{customdata:,.0f} MW</b> discharging<extra></extra>",
      },
    ],
    layout,
    { ...plotConfig, displayModeBar: false }
  );
}

// ---------------------------------------------------------------------------
// Page text: sliders, stat tiles, table, notes
// ---------------------------------------------------------------------------

function sliderReadout(spec, value, params) {
  if (spec.id === "energy") {
    const hours = params.power > 0 ? ` · ${(value / params.power).toFixed(1)} h at full power` : "";
    return `${value.toLocaleString("en-US")} MWh${hours}`;
  }
  const suffix = spec.id === "solar" && value > 0 ? "% more" : spec.unit === "%" ? "%" : ` ${spec.unit}`;
  return `${value.toLocaleString("en-US")}${suffix}`;
}

function buildSliders(state, onChange) {
  const box = document.getElementById("sliders");
  box.replaceChildren();
  const outputs = {};
  for (const spec of SLIDERS) {
    const wrap = document.createElement("div");
    wrap.className = "slider";

    const head = document.createElement("div");
    head.className = "slider-head";
    const label = document.createElement("label");
    label.htmlFor = `slider-${spec.id}`;
    label.textContent = spec.label;
    const output = document.createElement("output");
    output.htmlFor = `slider-${spec.id}`;
    head.append(label, output);

    const input = document.createElement("input");
    input.type = "range";
    input.id = `slider-${spec.id}`;
    input.min = spec.min;
    input.max = spec.max;
    input.step = spec.step;
    input.value = state.params[spec.id];
    input.addEventListener("input", () => {
      state.params[spec.id] = Number(input.value);
      refreshReadouts();
      onChange();
    });

    const help = document.createElement("p");
    help.className = "slider-help";
    help.textContent = spec.help;

    wrap.append(head, input, help);
    box.append(wrap);
    outputs[spec.id] = { input, output, spec };
  }

  function refreshReadouts() {
    for (const { input, output, spec } of Object.values(outputs)) {
      input.value = state.params[spec.id];
      output.textContent = sliderReadout(spec, state.params[spec.id], state.params);
    }
  }
  refreshReadouts();
  return refreshReadouts;
}

function renderControls(state, onSelect) {
  const box = document.getElementById("season-controls");
  box.replaceChildren();
  for (const [id, s] of Object.entries(state.seasons)) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.setAttribute("role", "radio");
    btn.setAttribute("aria-checked", String(id === state.season));
    btn.textContent = s.label;
    btn.addEventListener("click", () => onSelect(id));
    box.append(btn);
  }
}

function renderStats(result) {
  const { before, after } = result;
  const tiles = [
    ["Evening peak", fmtMw(after.eveningPeakMw), `was ${fmtMw(before.eveningPeakMw)} · ${signed(after.eveningPeakMw - before.eveningPeakMw, "MW")}`],
    ["Midday low", fmtMw(after.middayLowMw), `was ${fmtMw(before.middayLowMw)} · ${signed(after.middayLowMw - before.middayLowMw, "MW")}`],
    ["Steepest 3-hour ramp", `+${fmtMw(after.steepest3hRampMw)}`, `was +${fmtMw(before.steepest3hRampMw)} · ${signed(after.steepest3hRampMw - before.steepest3hRampMw, "MW")}`],
    ["Solar curtailed per day", fmtMwh(result.curtailedAfterMwh), `was ${fmtMwh(result.curtailedBeforeMwh)}`],
    [
      "Battery output per day",
      fmtMwh(result.dischargedMwh),
      `${fmtMwh(result.chargedMwh)} in · ${result.cyclesPerDay.toFixed(1)} cycles`,
    ],
  ];
  const box = document.getElementById("stats");
  box.replaceChildren();
  for (const [label, value, sub] of tiles) {
    const tile = document.createElement("div");
    tile.className = "stat";
    for (const [cls, text] of [["stat-label", label], ["stat-value", value], ["stat-sub", sub]]) {
      const el = document.createElement("div");
      el.className = cls;
      el.textContent = text;
      tile.append(el);
    }
    box.append(tile);
  }
}

function renderTable(result) {
  const table = document.getElementById("data-table");
  table.replaceChildren();
  const head = table.createTHead().insertRow();
  for (const label of [
    "Hour",
    "Net load, no battery (MW)",
    "Charging (MW)",
    "Discharging (MW)",
    "Battery stored (MWh)",
    "Solar curtailed (MW)",
    "Net load, with battery (MW)",
  ]) {
    const th = document.createElement("th");
    th.scope = "col";
    th.textContent = label;
    head.append(th);
  }
  const body = table.createTBody();
  for (const h of result.hours) {
    const tr = body.insertRow();
    [hourLabel(h.hour), h.netBefore, h.charge, h.discharge, h.soc, h.curtailedAfter, h.netAfter].forEach((value, i) => {
      tr.insertCell().textContent = i === 0 ? value : Math.round(value).toLocaleString("en-US");
    });
  }
}

function addNote(list, lead, text) {
  const li = document.createElement("li");
  const strong = document.createElement("strong");
  strong.textContent = `${lead} `;
  li.append(strong, document.createTextNode(text));
  list.append(li);
}

/**
 * The collapsed "What is this simulating?" box. Everything about the real battery comes from sources read
 * while writing it (linked at the bottom); the curtailment figures are optional and come from Tier 3's data.
 */
function renderBatteryExplainer(curtailment, region, fleet) {
  const box = document.getElementById("battery-explainer-body");
  box.replaceChildren();
  const paragraph = (text) => {
    const p = document.createElement("p");
    p.textContent = text;
    box.append(p);
    return p;
  };

  paragraph(
    "This page simulates one job a battery does: daily energy shifting. It charges when energy is plentiful, " +
      "mostly the midday hours when solar is strongest, and discharges when energy is scarce, mostly the steep " +
      "climb in the evening after the sun sets. The optimizer chooses the hours, and it also tops up at other " +
      "low-demand hours when that helps flatten the day."
  );
  if (fleet) {
    const names = fleet.units.map((u) => `${u.plant} (${u.power_mw} MW / ${u.energy_mwh} MWh)`).join(", ");
    paragraph(
      `The default battery is Oʻahu's whole battery fleet acting as one. EIA lists ${fleet.units.length} grid-connected ` +
        `batteries on the island: ${names}. Together that is ${fleet.power_mw.toFixed(1)} MW and ` +
        `${Math.round(fleet.energy_mwh).toLocaleString("en-US")} MWh.`
    );
    const corrected = fleet.units.filter((u) => u.corrected);
    if (corrected.length) {
      paragraph(
        "Two cautions about that number. First, " +
          corrected.map((u) => `EIA lists ${u.plant}'s ${u.power_mw} MW battery as holding ${u.energy_mwh_as_filed} MWh, which is not plausible; its developer reports ${u.energy_mwh} MWh, so that is used`).join("; ") +
          `. (As EIA filed it, the fleet would be ${Math.round(fleet.energy_mwh_as_filed).toLocaleString("en-US")} MWh.) ` +
          "The correction is written down in config/regions.yaml with its source. Second, batteries built alongside " +
          "solar farms may be limited in when and from where they can charge, which this simulation does not model, so " +
          "the fleet may look more flexible here than it is."
      );
    }
  }
  paragraph(
    "Oʻahu's large batteries do this kind of shifting too, but it is not all they do. The biggest, Kapolei Energy " +
      "Storage (185 MW / 565 MWh; owned by Kapolei Energy Storage I, LLC and developed by Plus Power; online since " +
      "December 2023), provides load shifting to Hawaiian Electric and also fast-frequency response, synthetic " +
      "inertia and black start. Those grid services are not simulated here. It is a grid-scale resource working for " +
      "the utility, not backup power for an individual customer."
  );

  const latest = curtailment && curtailment.regions[region] && curtailment.regions[region].latest_full_year;
  const perDay = latest
    ? ` (about ${Math.round(latest.curtailed_mwh / 365)} MWh a day in ${latest.year}, though it is concentrated on particular days)`
    : "";
  paragraph(
    "You may wonder why the Duck curve page does not already show this flattening. The reason is that page is a " +
      "model, not measured history, and it contains no battery at all, so it cannot show any. That is also why this " +
      "page can add Oʻahu's real battery fleet to it without counting the real batteries twice. It is not that " +
      "batteries are too small to matter: the default battery stores far more energy than the average daily " +
      `curtailment Hawaiian Electric reported${perDay}. Against the evening climb it is smaller: ` +
      `${fleet ? Math.round(fleet.power_mw) : 185} MW covers only part of the steepest 3-hour climb in the model ` +
      "(compare the tiles above)."
  );
  paragraph(
    "For what the utility expected: according to Hawaiian Electric's modeling, as reported by the project, " +
      "Kapolei Energy Storage should reduce curtailment by 69% over its first five years. That is a projection, " +
      "not a measurement."
  );

  const sources = document.createElement("p");
  sources.className = "explainer-sources";
  sources.append("Sources: ");
  [
    ["Utility Dive", "https://www.utilitydive.com/news/plus-power-energy-storage-online-hawaii-HECO-rolling-blackouts/704561/"],
    ["Kapolei Energy Storage project site", "https://www.kapoleienergystorage.com/overview"],
    ["EIA-860M (units, ratings, storage)", "https://www.eia.gov/electricity/data/eia860m/"],
    ["Waiawa Solar (developer)", "https://www.clearwayhawaii.com/operational-projects-2/waiawa"],
  ].forEach(([label, href], i) => {
    if (i > 0) sources.append(" · ");
    const a = document.createElement("a");
    a.href = href;
    a.textContent = label;
    a.target = "_blank";
    a.rel = "noopener";
    sources.append(a);
  });
  box.append(sources);
}

function renderNotes(duck, curtailment, region) {
  const list = document.getElementById("notes");
  list.replaceChildren();
  addNote(
    list,
    "A simulation, not a forecast.",
    "It runs on the modeled typical weekday from the Duck curve page (itself a model), so read it as showing " +
      "how a battery and more solar interact, not as predicting any real day."
  );
  addNote(
    list,
    "How the battery is scheduled.",
    "A small optimizer plans the whole day in advance. Fossil plants cost more the harder they are pushed, so each " +
      "hour is charged the square of its net load; minimizing the total flattens the day, charging when net load is " +
      "low and discharging when it is high. Solar that would push net load below the fossil minimum is curtailed, " +
      "and the optimizer avoids that first. It repeats the same day, starting empty, and shows a steady-state day."
  );
  addNote(
    list,
    "What is left out.",
    "Battery ageing, energy kept in reserve for emergencies, market rules and prices, weekends and weather, " +
      "demand response, and limits on particular lines. The battery charges from the grid, so at some hours the " +
      "energy it stores comes from fossil plants. Rooftop solar and wind are unchanged."
  );
  if (curtailment) {
    const latest = curtailment.regions[region].latest_full_year;
    const reason = curtailment.regions[region].main_reason;
    const perDay = latest.curtailed_mwh / 365;
    addNote(
      list,
      "For scale (real data).",
      `Hawaiian Electric reported ${(latest.curtailed_mwh / 1000).toFixed(1)} GWh of wind and solar curtailed on ` +
        `Oʻahu in ${latest.year}, about ${Math.round(perDay).toLocaleString("en-US")} MWh a day on average. ` +
        (reason && reason.reason === "system_constraint_mwh"
          ? `${Math.round(reason.share_pct)}% of it was recorded as “system constraint” rather than “oversupply”. ` +
            "The data does not spell out what falls under system constraints, and batteries that provide grid " +
            "services may relieve some of it, so this simulation, which models only the oversupply kind (net load " +
            "pushed below the fossil minimum), may understate what batteries can do. "
          : "") +
        "See the Curtailment page. The extra-solar slider lets you explore a future with much more solar."
    );
  }
  document.getElementById("source").textContent =
    `Built from ${duck.meta.kind} data on the Duck curve page (${duck.meta.sources.length} sources listed there). ` +
    `Times are ${duck.meta.time_zone}; ${duck.meta.day_type}.`;
}

// ---------------------------------------------------------------------------
// Start-up
// ---------------------------------------------------------------------------

async function main() {
  if (typeof Plotly === "undefined") {
    throw new Error("The Plotly library could not be loaded (it is fetched from cdn.plot.ly, so this needs internet access).");
  }
  const [duck, curtailment, plants] = await Promise.all([
    loadData(DUCK_URL),
    loadData(CURTAILMENT_URL).catch(() => null), // only used for scale; the page works without it
    loadData(PLANTS_URL).catch(() => null), // only sets the default battery size; falls back to fixed numbers
  ]);

  // ?region=<id> and ?season=<id> pick what to show.
  const query = new URLSearchParams(location.search);
  const regionId = duck.regions[query.get("region")] ? query.get("region") : Object.keys(duck.regions)[0];
  const region = duck.regions[regionId];
  const fleet = plants && plants.regions[regionId] ? plants.regions[regionId].battery_fleet : null;
  applyFleetDefaults(fleet); // before the sliders are built, so the defaults (and Reset) use the real fleet

  const rowsBySeason = {};
  for (const r of duck.rows) {
    if (r.region !== regionId) continue;
    (rowsBySeason[r.season] ??= []).push(r);
  }
  for (const rows of Object.values(rowsBySeason)) rows.sort((a, b) => a.hour - b.hour);

  // Default to the season with the deepest midday dip: the duck at its most pronounced.
  const deepest = Object.entries(region.seasons).reduce((a, b) =>
    b[1].stats.midday_low_mw < a[1].stats.midday_low_mw ? b : a
  )[0];
  const allNet = duck.rows.filter((r) => r.region === regionId).map((r) => r.net_load_mw);
  const state = {
    seasons: region.seasons,
    rowsBySeason,
    season: region.seasons[query.get("season")] ? query.get("season") : deepest,
    params: Object.fromEntries(SLIDERS.map((s) => [s.id, s.value])),
    // One fixed scale, so moving a slider changes the curve and not the axis.
    yMax: Math.ceil((Math.max(...allNet) * 1.1) / 100) * 100,
  };

  document.getElementById("title").textContent = `${region.label}: what a battery does to the duck`;

  let pending = false;
  const update = () => {
    if (pending) return; // several slider events in one frame need only one simulation
    pending = true;
    requestAnimationFrame(() => {
      pending = false;
      const result = simulate(state);
      document.getElementById("subtitle").textContent =
        `A typical ${region.seasons[state.season].label.toLowerCase()} weekday. ` +
        "Drag the sliders to change the battery and how much solar there is.";
      renderStats(result);
      renderTable(result);
      state.result = result;
      drawNet(state, result);
      drawBattery(result);
    });
  };

  const refreshReadouts = buildSliders(state, update);
  const selectSeason = (id) => {
    state.season = id;
    renderControls(state, selectSeason);
    update();
  };
  renderControls(state, selectSeason);
  document.getElementById("reset").addEventListener("click", () => {
    state.params = Object.fromEntries(SLIDERS.map((s) => [s.id, s.value]));
    refreshReadouts();
    update();
  });

  renderNotes(duck, curtailment, regionId);
  renderBatteryExplainer(curtailment, regionId, fleet);
  update();
  onThemeChange(() => {
    if (state.result) {
      drawNet(state, state.result);
      drawBattery(state.result);
    }
  });
}

main().catch((err) => showError(netDiv, err.message));
