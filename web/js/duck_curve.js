// Tier 2: the duck curve - a typical weekday's demand and net load, by hour, for each season.
// Reads the static JSON written by the pipeline (web/data/duck_curve.json). The numbers are MODELED
// (see the notes on the page), not measurements.

import {
  baseLayout,
  hourTickValues,
  loadData,
  onThemeChange,
  plotConfig,
  showError,
  themeColors,
  withAlpha,
} from "./common.js";

const DATA_URL = "data/duck_curve.json";
const CURTAILMENT_URL = "data/curtailment.json";
const MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

const chartDiv = document.getElementById("chart");

/** 0 -> "12 AM", 13 -> "1 PM". Hours are the hour *beginning* (13 = 1:00-2:00 PM). */
function hourLabel(h) {
  if (h === 0) return "12 AM";
  if (h < 12) return `${h} AM`;
  if (h === 12) return "12 PM";
  return `${h - 12} PM`;
}

const fmtMw = (value) => `${Math.round(value).toLocaleString("en-US")} MW`;

// ---------------------------------------------------------------------------
// The chart
// ---------------------------------------------------------------------------

function draw(state) {
  const { region, rowsBySeason, season, yMax } = state;
  const t = themeColors();
  const rows = rowsBySeason[season];
  const stats = region.seasons[season].stats;
  const x = rows.map((r) => hourLabel(r.hour));
  const col = (field) => rows.map((r) => r[field]);
  const line = (color, extra = {}) => ({ color, width: 2, ...extra });

  // Three lines, drawn bottom to top so each `fill: tonexty` shades the gap to the line below it:
  //   net load  ->  grid load (gap = utility solar + wind)  ->  customer demand (gap = rooftop solar)
  const traces = [
    {
      name: "Net load (after utility solar + wind)",
      x,
      y: col("net_load_mw"),
      customdata: rows.map((r) => [r.rooftop_solar_mw, r.utility_solar_mw, r.wind_mw]),
      mode: "lines",
      line: line(t.series1),
      hovertemplate:
        "<b>%{y:,.0f} MW</b> net load<br>" +
        "<span style='color:" + t.textSecondary + "'>Rooftop solar %{customdata[0]:,.0f} · " +
        "utility solar %{customdata[1]:,.0f} · wind %{customdata[2]:,.0f} MW</span><extra></extra>",
    },
    {
      name: "Grid load (after rooftop solar)",
      x,
      y: col("grid_load_mw"),
      mode: "lines",
      line: line(t.textSecondary),
      fill: "tonexty",
      fillcolor: withAlpha(t.series2, 0.22),
      hovertemplate: "<b>%{y:,.0f} MW</b> grid load<extra></extra>",
    },
    {
      name: "Customer demand",
      x,
      y: col("customer_demand_mw"),
      mode: "lines",
      line: line(t.muted, { dash: "dot" }),
      fill: "tonexty",
      fillcolor: withAlpha(t.series3, 0.22),
      hovertemplate: "<b>%{y:,.0f} MW</b> customer demand<extra></extra>",
    },
  ];

  // Direct labels: name each shaded band (when it is thick enough to hold a label) ...
  const annotations = [];
  const noon = rows[12];
  const bands = [
    ["Rooftop solar", noon.grid_load_mw, noon.customer_demand_mw],
    ["Utility solar + wind", noon.net_load_mw, noon.grid_load_mw],
  ];
  for (const [text, low, high] of bands) {
    if ((high - low) / yMax < 0.05) continue;
    annotations.push({
      x: hourLabel(12),
      y: (low + high) / 2,
      text,
      showarrow: false,
      font: { size: 12, color: t.textPrimary },
      bgcolor: withAlpha(t.surface, 0.8),
      borderpad: 2,
    });
  }

  // ... and mark the two moments the duck is about: the belly and the evening peak.
  const lowRow = rows[stats.midday_low_hour];
  const peakRow = rows[stats.evening_peak_hour];
  traces.push({
    x: [hourLabel(lowRow.hour), hourLabel(peakRow.hour)],
    y: [lowRow.net_load_mw, peakRow.net_load_mw],
    mode: "markers",
    marker: { size: 9, color: t.series1, line: { color: t.surface, width: 2 } },
    hoverinfo: "skip",
    showlegend: false,
  });
  annotations.push(
    {
      x: hourLabel(lowRow.hour),
      y: lowRow.net_load_mw,
      text: `<b>${fmtMw(lowRow.net_load_mw)}</b><br>midday low`,
      showarrow: false,
      yshift: -26,
      font: { size: 12, color: t.textPrimary },
    },
    {
      // The lines descend right after the peak, so the label sits in the open space below-right,
      // joined to the marker by a thin leader line.
      x: hourLabel(peakRow.hour),
      y: peakRow.net_load_mw,
      text: `<b>${fmtMw(peakRow.net_load_mw)}</b><br>evening peak`,
      showarrow: true,
      arrowhead: 0,
      arrowwidth: 1,
      arrowcolor: t.muted,
      standoff: 6,
      ax: 54,
      ay: 78,
      align: "left",
      font: { size: 12, color: t.textPrimary },
    }
  );

  const layout = {
    ...baseLayout(t),
    showlegend: true,
    legend: { orientation: "h", x: 0, y: 1.02, yanchor: "bottom", traceorder: "reversed", font: { size: 12 } },
    margin: { l: 64, r: 16, t: 56, b: 44 },
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
      range: [0, yMax],
      fixedrange: true,
      gridcolor: t.grid,
      gridwidth: 1,
      zeroline: true,
      zerolinecolor: t.axis,
    },
    annotations,
  };
  return Plotly.react(chartDiv, traces, layout, { ...plotConfig, displayModeBar: false });
}

// ---------------------------------------------------------------------------
// Page text, controls, stat tiles and the table view
// ---------------------------------------------------------------------------

function monthRange(months) {
  // [12, 1, 2] -> "Dec-Feb"
  return `${MONTH_NAMES[months[0] - 1]}–${MONTH_NAMES[months[months.length - 1] - 1]}`;
}

function renderHeader(state) {
  const { region, season } = state;
  const s = region.seasons[season];
  document.getElementById("title").textContent = `${region.label}: a typical weekday on the grid`;
  document.getElementById("subtitle").textContent =
    `Average weekday in ${s.label.toLowerCase()} (${monthRange(s.months)}), hour by hour. ` +
    `Sizes are calibrated to ${region.reference_year} generation.`;
}

function renderControls(state, onSelect) {
  const box = document.getElementById("season-controls");
  box.replaceChildren();
  for (const [id, s] of Object.entries(state.region.seasons)) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.setAttribute("role", "radio");
    btn.setAttribute("aria-checked", String(id === state.season));
    btn.textContent = `${s.label} (${monthRange(s.months)})`;
    btn.addEventListener("click", () => onSelect(id));
    box.append(btn);
  }
}

function renderStats(state) {
  const st = state.region.seasons[state.season].stats;
  const tiles = [
    ["Midday low (net load)", fmtMw(st.midday_low_mw), `at ${hourLabel(st.midday_low_hour)}`],
    ["Evening peak (net load)", fmtMw(st.evening_peak_mw), `at ${hourLabel(st.evening_peak_hour)}`],
    ["Evening climb", `+${fmtMw(st.evening_climb_mw)}`, "from the midday low to the peak"],
    [
      "Steepest 3-hour ramp",
      `+${fmtMw(st.steepest_3h_ramp_mw)}`,
      `starting at ${hourLabel(st.steepest_3h_ramp_start_hour)}`,
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

function renderTable(state) {
  const table = document.getElementById("data-table");
  table.replaceChildren();
  const head = table.createTHead().insertRow();
  for (const label of [
    "Hour",
    "Customer demand (MW)",
    "Rooftop solar (MW)",
    "Grid load (MW)",
    "Utility solar (MW)",
    "Wind (MW)",
    "Net load (MW)",
  ]) {
    const th = document.createElement("th");
    th.scope = "col";
    th.textContent = label;
    head.append(th);
  }
  const body = table.createTBody();
  for (const r of state.rowsBySeason[state.season]) {
    const tr = body.insertRow();
    const cells = [
      hourLabel(r.hour),
      r.customer_demand_mw,
      r.rooftop_solar_mw,
      r.grid_load_mw,
      r.utility_solar_mw,
      r.wind_mw,
      r.net_load_mw,
    ];
    cells.forEach((value, i) => {
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

function renderNotes(meta, region) {
  const list = document.getElementById("notes");
  list.replaceChildren();
  const homes = Math.round(region.share_of_state * 100);
  const rooftop = Math.round(region.rooftop_share_of_state * 100);
  addNote(
    list,
    "A model, not a measurement.",
    "No public hourly grid data exists for Hawaiʻi, so this page is a model. Treat it as an illustration of " +
      "the pattern, not a record of any particular day."
  );
  addNote(
    list,
    "Shape from NREL, size from EIA.",
    "Every curve here combines two things. NREL's simulations supply the shape: how the quantity rises and " +
      "falls hour by hour (homes and businesses for demand, a reference solar farm for utility-scale solar, and " +
      "rooftop panels on homes for rooftop solar). EIA's real numbers supply the size: how much energy there " +
      `was in each month of ${region.reference_year}. Each shape is scaled, month by month, until its total ` +
      "matches EIA's figure."
  );
  addNote(
    list,
    "Where the rooftop solar comes from.",
    "EIA's estimate of rooftop solar covers the whole state of Hawaiʻi, not Oʻahu alone. To get Oʻahu's part, " +
      `${rooftop}% of it is used: that is Hawaiian Electric's reported Oʻahu rooftop total for 2024 divided by ` +
      "EIA's statewide estimate for 2024. This gives Oʻahu's rooftop energy for each month (the size). For the " +
      "shape, the page uses NREL's simulated hour-by-hour output of rooftop panels on Hawaiʻi homes (from its " +
      "ResStock housing model, using actual 2018 weather). That pattern is stretched or shrunk so that each " +
      "month's total equals Oʻahu's figure. The same pattern also stands in for rooftop panels on businesses, " +
      "because EIA's estimate covers both and NREL does not simulate businesses' rooftop panels."
  );
  addNote(
    list,
    "How to read it.",
    "Customer demand is everything customers use. Rooftop solar cuts what the utility must deliver (grid load); " +
      "utility-scale solar and wind cut it further. What remains, net load, is what fossil-fuel plants must " +
      "supply. The midday dip followed by the steep evening climb is the “duck”."
  );
  addNote(
    list,
    "What is simplified.",
    `Home energy use is modeled for all of Hawaiʻi and scaled to ${homes}% for Oʻahu. Wind is spread evenly ` +
      "through each month. Batteries and electric vehicles are not modeled, so the effect of Oʻahu's large " +
      "battery (Kapolei Energy Storage, online since December 2023) is not in these curves. Curtailment (see " +
      "“What is curtailment?” above) is not shown either, so the real midday dip is likely shallower than " +
      "drawn. The modeled overnight demand may also be lower than the real thing."
  );
  document.getElementById("source").textContent =
    `Sources: ${meta.sources.join("; ")}. Times are ${meta.time_zone}; values are hourly averages by hour ` +
    `beginning, for an ${meta.day_type}. Data refreshed ${meta.generated_at.slice(0, 10)}.`;
}

/** The plain-language "What is curtailment?" box. `curtailment` (Tier 3's data) is optional: it only adds the real figures. */
function renderCurtailmentExplainer(curtailment, regionId) {
  const box = document.getElementById("curtailment-explainer-body");
  box.replaceChildren();
  const paragraph = (text) => {
    const p = document.createElement("p");
    p.textContent = text;
    box.append(p);
  };
  paragraph(
    "Curtailment means the grid operator tells a solar farm or wind farm to produce less than it could. It " +
      "happens when the grid cannot use all the power that is available at that moment, typically around midday " +
      "when solar output is highest and demand is comparatively low, or when part of the grid cannot carry it. " +
      "The energy is simply lost: the panels or turbines are throttled back."
  );
  paragraph(
    "It matters for the duck curve because the midday dip in the chart is exactly where it happens. When the dip " +
      "gets deep enough, there is nowhere left for extra solar to go. This page draws the dip as if all solar were " +
      "used, so the real dip is probably shallower than shown."
  );
  const region = curtailment && curtailment.regions[regionId];
  if (region) {
    const latest = region.latest_full_year;
    const peak = region.peak_year;
    paragraph(
      `How much? Hawaiian Electric reported that ${latest.year} curtailment on Oʻahu was ` +
        `${(latest.curtailed_mwh / 1000).toFixed(1)} GWh of wind and utility-scale solar, ` +
        `${latest.curtailment_pct.toFixed(1)}% of what those plants could have supplied. The worst year was ` +
        `${peak.year}, at ${peak.curtailment_pct.toFixed(1)}%. The Curtailment page has the details and the reasons.`
    );
  }
}

// ---------------------------------------------------------------------------
// Start-up
// ---------------------------------------------------------------------------

async function main() {
  if (typeof Plotly === "undefined") {
    throw new Error("The Plotly library could not be loaded (it is fetched from cdn.plot.ly, so this needs internet access).");
  }
  const [data, curtailment] = await Promise.all([
    loadData(DATA_URL),
    loadData(CURTAILMENT_URL).catch(() => null), // only adds real figures to the explainer; the page works without it
  ]);

  // ?region=<id> and ?season=<id> pick what to show; defaults are the first region and the season
  // with the deepest midday dip (the duck at its most pronounced).
  const params = new URLSearchParams(location.search);
  const regionId = data.regions[params.get("region")] ? params.get("region") : Object.keys(data.regions)[0];
  const region = data.regions[regionId];

  const rowsBySeason = {};
  for (const r of data.rows) {
    if (r.region !== regionId) continue;
    (rowsBySeason[r.season] ??= []).push(r);
  }
  for (const rows of Object.values(rowsBySeason)) rows.sort((a, b) => a.hour - b.hour);

  const deepest = Object.entries(region.seasons).reduce((a, b) =>
    b[1].stats.midday_low_mw < a[1].stats.midday_low_mw ? b : a
  )[0];
  const state = {
    region,
    rowsBySeason,
    season: region.seasons[params.get("season")] ? params.get("season") : deepest,
    // One fixed scale for every season, so switching seasons compares like with like.
    yMax: Math.ceil((Math.max(...data.rows.filter((r) => r.region === regionId).map((r) => r.customer_demand_mw)) * 1.08) / 100) * 100,
  };

  const refresh = () => {
    renderHeader(state);
    renderControls(state, (id) => {
      state.season = id;
      refresh();
    });
    renderStats(state);
    renderTable(state);
    return draw(state);
  };

  renderNotes(data.meta, region);
  renderCurtailmentExplainer(curtailment, regionId);
  await refresh();
  onThemeChange(() => draw(state));
}

main().catch((err) => showError(chartDiv, err.message));
