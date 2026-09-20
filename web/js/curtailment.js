// Tier 3: curtailment - wind and utility-scale solar energy the grid took vs. energy it asked plants
// to hold back. Reads the static JSON written by the pipeline (web/data/curtailment.json). Unlike the
// duck curve, these numbers are REPORTED by Hawaiian Electric, not modeled.

import { baseLayout, loadData, onThemeChange, plotConfig, showError, themeColors } from "./common.js";

const DATA_URL = "data/curtailment.json";
const REASONS = [
  ["oversupply_mwh", "Oversupply", "series3"],
  ["system_constraint_mwh", "System constraint", "series4"],
  ["facility_requested_mwh", "Facility requested", "series5"],
];
const REASON_LABELS = Object.fromEntries(REASONS.map(([key, label]) => [key, label]));

const energyDiv = document.getElementById("chart-energy");
const rateDiv = document.getElementById("chart-rate");
const reasonsDiv = document.getElementById("chart-reasons");
const followers = [rateDiv, reasonsDiv]; // these two follow the zoom of the first chart

const DAY_MS = 24 * 60 * 60 * 1000;
const BAR_WIDTH = 78 * DAY_MS; // a little under a quarter, leaving a gap between bars

/** A bar is drawn centered on its x, so put each quarter's bar in the middle of that quarter. */
const midQuarter = (r) => `${r.year}-${String((r.quarter - 1) * 3 + 2).padStart(2, "0")}-15`;
const qLabel = (period) => {
  const [year, q] = period.split("-Q");
  return `Q${q} ${year}`;
};
const gwh = (mwh) => mwh / 1000;
const fmtGwh = (mwh) => {
  const g = gwh(mwh);
  return `${g >= 100 ? Math.round(g).toLocaleString("en-US") : g.toFixed(1)} GWh`;
};
const fmtPct = (value) => (value === null || value === undefined ? "–" : `${value.toFixed(1)}%`);

// ---------------------------------------------------------------------------
// Charts
// ---------------------------------------------------------------------------

function xAxis(t, extra = {}) {
  return {
    type: "date",
    tickformat: "%Y",
    hoverformat: "Q%q %Y",
    showgrid: false,
    linecolor: t.axis,
    tickcolor: t.axis,
    ...extra,
  };
}

function yAxis(t, title, extra = {}) {
  return {
    title: { text: title, standoff: 8 },
    gridcolor: t.grid,
    gridwidth: 1,
    zeroline: true,
    zerolinecolor: t.axis,
    fixedrange: true,
    ...extra,
  };
}

/** A hairline in the surface color between stacked segments, in place of a border. */
const gap = (t) => ({ line: { color: t.surface, width: 1 } });

function drawEnergy(state) {
  const t = themeColors();
  const q = state.quarters;
  const x = q.map(midQuarter);
  const layout = {
    ...baseLayout(t),
    showlegend: true,
    legend: { orientation: "h", x: 1, xanchor: "right", y: 1.02, yanchor: "bottom", traceorder: "normal", font: { size: 12 } },
    margin: { l: 64, r: 16, t: 44, b: 24 },
    barmode: "stack",
    hovermode: "x unified",
    xaxis: {
      ...xAxis(t),
      rangeselector: {
        buttons: [
          { count: 5, label: "5y", step: "year", stepmode: "backward" },
          { count: 10, label: "10y", step: "year", stepmode: "backward" },
          { step: "all", label: "All" },
        ],
        x: 0,
        y: 1.02,
        yanchor: "bottom",
        bgcolor: t.surface,
        activecolor: t.grid,
        bordercolor: t.axis,
        borderwidth: 1,
        font: { color: t.textSecondary },
      },
      rangeslider: { visible: true, thickness: 0.1, bgcolor: t.surface, bordercolor: t.axis, borderwidth: 1 },
    },
    yaxis: yAxis(t, "Gigawatt-hours (GWh)", { tickformat: ",.0f" }),
  };
  // Redrawing (for a theme change) must not throw away a range the viewer already chose.
  const xa = energyDiv.layout && energyDiv.layout.xaxis;
  if (xa && xa.autorange === false && xa.range) layout.xaxis.range = xa.range.slice();

  return Plotly.react(
    energyDiv,
    [
      {
        type: "bar",
        name: "Delivered to the grid",
        x,
        y: q.map((r) => gwh(r.delivered_mwh)),
        width: BAR_WIDTH,
        marker: { color: t.series1, ...gap(t) },
        hovertemplate: "<b>%{y:,.1f} GWh</b> delivered<extra></extra>",
      },
      {
        type: "bar",
        name: "Curtailed",
        x,
        y: q.map((r) => gwh(r.curtailed_mwh)),
        customdata: q.map((r) => [r.curtailment_pct === null ? "–" : r.curtailment_pct.toFixed(1), gwh(r.potential_mwh)]),
        width: BAR_WIDTH,
        marker: { color: t.series2, ...gap(t) },
        hovertemplate:
          "<b>%{y:,.1f} GWh</b> curtailed<br>" +
          `<span style='color:${t.textSecondary}'>%{customdata[0]}% of the %{customdata[1]:,.0f} GWh potential</span><extra></extra>`,
      },
    ],
    layout,
    plotConfig
  );
}

function drawRate(state) {
  const t = themeColors();
  const q = state.quarters.filter((r) => r.curtailment_pct !== null);
  const peak = q.reduce((a, b) => (b.curtailment_pct > a.curtailment_pct ? b : a));
  const layout = {
    ...baseLayout(t),
    margin: { l: 64, r: 16, t: 12, b: 32 },
    hovermode: "x unified",
    xaxis: xAxis(t, { fixedrange: true }),
    yaxis: yAxis(t, "Curtailed", { ticksuffix: "%", tickformat: ".0f", range: [0, Math.ceil((peak.curtailment_pct * 1.3) / 2) * 2] }),
    annotations: [
      {
        // Label the extreme rather than every bar.
        x: midQuarter(peak),
        y: peak.curtailment_pct,
        text: `<b>${fmtPct(peak.curtailment_pct)}</b><br>${qLabel(peak.period)}`,
        showarrow: false,
        yshift: 22,
        font: { size: 12, color: t.textPrimary },
      },
    ],
  };
  applyFollowerRange(layout, state);
  return Plotly.react(
    rateDiv,
    [
      {
        type: "bar",
        x: q.map(midQuarter),
        y: q.map((r) => r.curtailment_pct),
        customdata: q.map((r) => [gwh(r.curtailed_mwh), gwh(r.potential_mwh)]),
        width: BAR_WIDTH,
        marker: { color: t.series2 },
        hovertemplate: "<b>%{y:.1f}%</b> curtailed<br>%{customdata[0]:,.1f} of %{customdata[1]:,.0f} GWh<extra></extra>",
      },
    ],
    layout,
    { ...plotConfig, displayModeBar: false }
  );
}

function drawReasons(state) {
  const t = themeColors();
  const q = state.quarters.filter((r) => REASONS.every(([key]) => r[key] !== null));
  const x = q.map(midQuarter);
  const layout = {
    ...baseLayout(t),
    showlegend: true,
    legend: { orientation: "h", x: 0, y: 1.02, yanchor: "bottom", traceorder: "normal", font: { size: 12 } },
    margin: { l: 64, r: 16, t: 40, b: 32 },
    barmode: "stack",
    hovermode: "x unified",
    xaxis: xAxis(t, { fixedrange: true }),
    yaxis: yAxis(t, "Gigawatt-hours (GWh)", { tickformat: ",.0f" }),
  };
  applyFollowerRange(layout, state);
  return Plotly.react(
    reasonsDiv,
    REASONS.map(([key, name, color]) => ({
      type: "bar",
      name,
      x,
      y: q.map((r) => gwh(r[key])),
      width: BAR_WIDTH,
      marker: { color: t[color], ...gap(t) },
      hovertemplate: `<b>%{y:,.1f} GWh</b> ${name.toLowerCase()}<extra></extra>`,
    })),
    layout,
    { ...plotConfig, displayModeBar: false }
  );
}

/** The whole span of the first chart's bars, so every chart starts on the same dates. */
function fullRange(quarters) {
  const edge = (r, days) => new Date(new Date(`${midQuarter(r)}T00:00:00Z`).getTime() + days * DAY_MS).toISOString().slice(0, 10);
  return [edge(quarters[0], -39), edge(quarters[quarters.length - 1], 39)];
}

/** The first chart holds the range the viewer chose; the other two copy it (or show everything). */
function applyFollowerRange(layout, state) {
  const xa = energyDiv.layout && energyDiv.layout.xaxis;
  layout.xaxis.range = xa && xa.autorange === false && xa.range ? xa.range.slice() : state.fullRange;
}

function syncRanges(event, state) {
  let range;
  if (event["xaxis.range[0]"] !== undefined) {
    range = [event["xaxis.range[0]"], event["xaxis.range[1]"]];
  } else if (event["xaxis.range"]) {
    range = event["xaxis.range"];
  } else if (event["xaxis.autorange"]) {
    range = state.fullRange; // the "All" button: show everything, on all three charts
  } else {
    return;
  }
  for (const div of followers) Plotly.relayout(div, { "xaxis.range": range });
}

const drawAll = (state) => Promise.all([drawEnergy(state), drawRate(state), drawReasons(state)]);

// ---------------------------------------------------------------------------
// Page text, stat tiles and the table view
// ---------------------------------------------------------------------------

function renderHeader(region) {
  document.getElementById("title").textContent = `${region.label}: solar and wind that could have been used`;
  document.getElementById("subtitle").textContent =
    `Quarterly, ${qLabel(region.first_quarter)} to ${qLabel(region.latest_quarter)}. ` +
    "Hawaiian Electric's own figures for the wind and utility-scale solar it took, and the amount it asked plants to hold back.";
}

function renderStats(region, latestQuarter) {
  const latest = region.latest_full_year;
  const peak = region.peak_year;
  const tiles = [
    [`${latest.year} curtailed`, fmtGwh(latest.curtailed_mwh), `${fmtPct(latest.curtailment_pct)} of what the plants could have supplied`],
    ["Worst year", String(peak.year), `${fmtGwh(peak.curtailed_mwh)} · ${fmtPct(peak.curtailment_pct)} of potential`],
  ];
  if (region.main_reason) {
    tiles.push([
      `Main cause in ${region.main_reason.year}`,
      REASON_LABELS[region.main_reason.reason],
      `${Math.round(region.main_reason.share_pct)}% of curtailed energy`,
    ]);
  }
  tiles.push([
    `Latest quarter (${qLabel(latestQuarter.period)})`,
    fmtPct(latestQuarter.curtailment_pct),
    `${fmtGwh(latestQuarter.curtailed_mwh)} curtailed`,
  ]);

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

function renderTable(quarters) {
  const table = document.getElementById("data-table");
  table.replaceChildren();
  const head = table.createTHead().insertRow();
  for (const label of [
    "Quarter",
    "Delivered (GWh)",
    "Curtailed (GWh)",
    "Potential (GWh)",
    "Curtailed",
    "Oversupply (GWh)",
    "System constraint (GWh)",
    "Facility requested (GWh)",
  ]) {
    const th = document.createElement("th");
    th.scope = "col";
    th.textContent = label;
    head.append(th);
  }
  const num = (mwh) => (mwh === null ? "–" : gwh(mwh).toLocaleString("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 }));
  const body = table.createTBody();
  for (const r of [...quarters].reverse()) {
    const tr = body.insertRow();
    for (const text of [
      qLabel(r.period),
      num(r.delivered_mwh),
      num(r.curtailed_mwh),
      num(r.potential_mwh),
      fmtPct(r.curtailment_pct),
      num(r.oversupply_mwh),
      num(r.system_constraint_mwh),
      num(r.facility_requested_mwh),
    ]) {
      tr.insertCell().textContent = text;
    }
  }
}

function addNote(list, lead, text) {
  const li = document.createElement("li");
  const strong = document.createElement("strong");
  strong.textContent = `${lead} `;
  li.append(strong, document.createTextNode(text));
  list.append(li);
}

function renderNotes(meta, region) {
  const list = document.getElementById("notes");
  list.replaceChildren();
  addNote(
    list,
    "Reported, not modeled.",
    "These are Hawaiian Electric's own published numbers. “Curtailment” means the utility told a plant " +
      "to produce less than it could have, usually because the grid could not use all of it. " +
      "Potential is delivered plus curtailed."
  );
  addNote(
    list,
    "Wind and solar together.",
    "Hawaiian Electric reports wind and utility-scale solar as one group (the resources it can turn down), so " +
      "this page cannot say how much of the curtailed energy was solar. Rooftop solar is not curtailable, so it " +
      "is not part of these numbers."
  );
  addNote(
    list,
    "The reasons.",
    "Oversupply: more supply than demand across the whole system. System constraint: limits on particular parts " +
      "of the grid or equipment rather than overall supply and demand. Facility requested: the plant's owner asked. " +
      `Hawaiian Electric has recorded reasons only since July 2015. In ${region.main_reason ? region.main_reason.year : "the latest complete year"}, ` +
      (region.main_reason
        ? `${Math.round(region.main_reason.share_pct)}% of curtailed energy was for ${REASON_LABELS[region.main_reason.reason].toLowerCase()}s.`
        : "see the chart.")
  );
  addNote(
    list,
    "Corrections happen.",
    "Hawaiian Electric occasionally corrects past figures, and in a few quarters the by-reason total differs " +
      "slightly from the curtailed total. Each pipeline run picks up the latest corrected files."
  );
  document.getElementById("source").textContent =
    `Source: ${meta.source}. Data refreshed ${meta.generated_at.slice(0, 10)}.`;
}

// ---------------------------------------------------------------------------
// Start-up
// ---------------------------------------------------------------------------

async function main() {
  if (typeof Plotly === "undefined") {
    throw new Error("The Plotly library could not be loaded (it is fetched from cdn.plot.ly, so this needs internet access).");
  }
  const data = await loadData(DATA_URL);

  // ?region=<id> picks a region; the default is the first one in the file.
  const requested = new URLSearchParams(location.search).get("region");
  const regionId = requested && data.regions[requested] ? requested : Object.keys(data.regions)[0];
  const region = data.regions[regionId];
  const quarters = data.quarterly_rows.filter((r) => r.region === regionId);
  if (!quarters.length) throw new Error(`No data for region "${regionId}".`);

  const state = { quarters, fullRange: fullRange(quarters) };
  renderHeader(region);
  renderStats(region, quarters[quarters.length - 1]);
  renderNotes(data.meta, region);
  renderTable(quarters);

  await drawAll(state);
  energyDiv.on("plotly_relayout", (event) => syncRanges(event, state));
  onThemeChange(() => drawAll(state));
}

main().catch((err) => showError(energyDiv, err.message));
