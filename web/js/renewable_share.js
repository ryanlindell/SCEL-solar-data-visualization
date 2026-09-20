// Tier 1: renewable share of generation over time.
// Reads the static JSON written by the pipeline (web/data/renewable_share.json) - no live API calls.

import { baseLayout, formatMonth, loadData, onThemeChange, plotConfig, showError, themeColors, withAlpha } from "./common.js";

const DATA_URL = "data/renewable_share.json";
const MILESTONES_URL = "data/milestones.json";
const FUEL_NAMES = { SUN: "solar", WND: "wind" };

const chartDiv = document.getElementById("chart");

// The milestones for the region on screen (hand-written in data/milestones.json), the one whose card is open, and
// which of them the chart can place (one whose month has no data is not drawn). `view` holds what draw() needs
// again when a milestone is picked.
let milestones = [];
let selectedMilestone = null;
let placedMilestones = [];
let view = null;

/** "2026-06" -> "2026-06-01", the date string Plotly's time axis expects. */
const toDate = (period) => `${period}-01`;

function nextMonthDate(period) {
  const [year, month] = period.split("-").map(Number);
  const d = new Date(Date.UTC(year, month, 1)); // month is 1-based here, so this is the next month
  return d.toISOString().slice(0, 10);
}

/** The latest the date axis may end: the newest month plus ~75 days, enough room for the end-dot. */
function axisEnd(period) {
  const [year, month] = period.split("-").map(Number);
  return Date.UTC(year, month - 1, 1) + 75 * 24 * 60 * 60 * 1000;
}

function fmtPct(value) {
  return `${value.toFixed(1)}%`;
}

function fmtMwh(value) {
  return Math.round(value).toLocaleString("en-US");
}

// ---------------------------------------------------------------------------
// The chart
// ---------------------------------------------------------------------------

function draw(rows, finalThrough) {
  const t = themeColors();
  const final = rows.filter((r) => !r.provisional);
  const prelim = rows.filter((r) => r.provisional);
  const last = rows[rows.length - 1];

  const customdata = (list) =>
    list.map((r) => [r.generation_by_fuel_mwh.SUN ?? 0, r.generation_by_fuel_mwh.WND ?? 0, r.total_mwh]);
  // Hovering shows only the line under the pointer (hovermode "closest" below), so each tooltip names its own month.
  const hover = (note) =>
    "<b>%{x|%B %Y}</b><br>" +
    "<b>%{y:.1f}%</b> renewable share<br>" +
    "Solar %{customdata[0]:,.0f} MWh · Wind %{customdata[1]:,.0f} MWh<br>" +
    "Total generation %{customdata[2]:,.0f} MWh" +
    note +
    "<extra></extra>";
  const line = { color: t.series1, width: 2 };
  const faded = withAlpha(t.series1, 0.55); // preliminary months: same line, fainter

  const traces = [
    {
      name: "Utility-scale solar + wind",
      x: final.map((r) => toDate(r.period)),
      y: final.map((r) => r.renewable_share_pct),
      customdata: customdata(final),
      mode: "lines",
      line,
      hovertemplate: hover(""),
      hoverlabel: { bordercolor: t.series1 },
    },
  ];

  const annotations = [
    {
      // Label the newest month directly instead of putting a number on every point. It is pinned to the
      // right edge of the plot (paper coordinates), not to a date, because Plotly widens an axis to fit
      // any annotation placed in data coordinates.
      x: 1,
      y: last.renewable_share_pct,
      xref: "paper",
      yref: "y",
      text: `<b>${fmtPct(last.renewable_share_pct)}</b><br>${formatMonth(last.period)}`,
      showarrow: false,
      xanchor: "left",
      yanchor: "middle",
      xshift: 12,
      align: "left",
      font: { color: t.textPrimary, size: 13 },
    },
  ];
  const shapes = [];

  if (prelim.length) {
    // Fainter continuation (inside the shaded band) for months where some plants haven't
    // reported yet. Dashes look broken on a jagged monthly line, so fade it instead.
    const lastFinal = final[final.length - 1];
    traces.push(
      {
        // Joins the last complete month to the first preliminary one; carries no hover of its own.
        x: [toDate(lastFinal.period), toDate(prelim[0].period)],
        y: [lastFinal.renewable_share_pct, prelim[0].renewable_share_pct],
        mode: "lines",
        line: { ...line, color: faded },
        hoverinfo: "skip",
        showlegend: false,
      },
      {
        name: "Utility-scale solar + wind (preliminary)",
        showlegend: false, // shares the first trace's legend entry; the shaded band marks it

        x: prelim.map((r) => toDate(r.period)),
        y: prelim.map((r) => r.renewable_share_pct),
        customdata: customdata(prelim),
        mode: "lines",
        line: { ...line, color: faded },
        hovertemplate: hover("<br><i>Preliminary: some plants not yet reported</i>"),
        hoverlabel: { bordercolor: t.series1 },
      }
    );
    shapes.push({
      type: "rect",
      xref: "x",
      yref: "paper",
      x0: toDate(prelim[0].period),
      x1: nextMonthDate(last.period),
      y0: 0,
      y1: 1,
      fillcolor: t.muted,
      opacity: 0.12,
      line: { width: 0 },
      layer: "below",
    });
    annotations.push({
      x: toDate(prelim[0].period),
      y: 0,
      xref: "x",
      yref: "paper",
      text: "Preliminary →",
      showarrow: false,
      xanchor: "right",
      yanchor: "bottom",
      xshift: -6,
      yshift: 4,
      font: { color: t.textSecondary, size: 12 },
    });
  }

  // End-dot with a surface-colored ring so it stays legible against the line. It is a marker on the
  // last point of the final line trace (size 0 everywhere else) rather than a one-point trace of its own.
  // (Plotly pads an axis to make room for markers; see `autorangeoptions` in the layout below.)
  const endTrace = traces[traces.length - 1];
  endTrace.mode = "lines+markers";
  endTrace.marker = {
    size: endTrace.x.map((_, i) => (i === endTrace.x.length - 1 ? 9 : 0)),
    color: t.series1,
    opacity: 1, // Plotly otherwise dims the dot (0.7) when the line itself is translucent
    line: { color: t.surface, width: 2 },
  };

  // The dotted line: EIA's estimate of rooftop solar, scaled to Oʻahu, added on top of the plant-survey
  // numbers. It exists only from 2014 (when EIA's estimate begins) and is drawn in the rooftop color used on
  // the duck curve page. It is a different measure (share of ALL electricity used), so it has its own label.
  const withRooftop = rows.filter((r) => r.renewable_share_incl_rooftop_pct != null);
  if (withRooftop.length) {
    const lastRooftop = withRooftop[withRooftop.length - 1];
    traces.push({
      name: "Including rooftop solar (estimate)",
      x: withRooftop.map((r) => toDate(r.period)),
      y: withRooftop.map((r) => r.renewable_share_incl_rooftop_pct),
      customdata: withRooftop.map((r) => [
        r.rooftop_solar_mwh_est,
        r.rooftop_basis === "measured"
          ? "sized from Hawaiian Electric's reported total for the quarter"
          : `scaled at an assumed ${Math.round((r.rooftop_share_used ?? 0) * 100)}% of the state total`,
      ]),
      mode: "lines",
      line: { color: t.series3, width: 2, dash: "dot" },
      hoverlabel: { bordercolor: t.series3 },
      hovertemplate:
        "<b>%{x|%B %Y}</b><br>" +
        "<b>%{y:.1f}%</b> including rooftop solar (estimate)<br>" +
        `<span style='color:${t.textSecondary}'>Rooftop solar about %{customdata[0]:,.0f} MWh, %{customdata[1]}</span><extra></extra>`,
    });
    annotations.push({
      x: 1,
      y: lastRooftop.renewable_share_incl_rooftop_pct,
      xref: "paper",
      yref: "y",
      text: `<b>${fmtPct(lastRooftop.renewable_share_incl_rooftop_pct)}</b><br>with rooftop`,
      showarrow: false,
      xanchor: "left",
      yanchor: "middle",
      xshift: 12,
      align: "left",
      font: { color: t.textPrimary, size: 13 },
    });
  }

  // Milestones: numbered diamonds in a clear band across the top of the chart (nothing is drawn there), each with a
  // faint dotted guide down to its month. Neighbours alternate between two rows so their numbers do not collide.
  // Clicking a diamond (or its button under the chart) opens its card. The March 2011 one also says why "first
  // wind" means first in EIA's data.
  const peak = Math.max(...rows.map((r) => Math.max(r.renewable_share_pct, r.renewable_share_incl_rooftop_pct ?? 0)));
  const yMax = Math.ceil((peak * 1.15) / 2) * 2;
  // Heights as a fraction of the plot, so the band clears the data whatever the scale (the data never passes 87% of it).
  const railFraction = (m) => (milestones.indexOf(m) % 2 ? 0.905 : 0.955); // odd-numbered ones sit on the lower row
  const railY = (m) => yMax * railFraction(m);
  const isSelected = (m) => m.id === selectedMilestone;
  const available = new Set(rows.map((r) => r.period));
  placedMilestones = milestones.filter((m) => available.has(m.month));
  if (placedMilestones.length) {
    for (const m of placedMilestones) {
      shapes.push({
        type: "line",
        xref: "x",
        yref: "y",
        x0: toDate(m.month),
        x1: toDate(m.month),
        y0: 0,
        y1: railY(m),
        line: { color: isSelected(m) ? t.series2 : t.muted, width: isSelected(m) ? 1.5 : 1, dash: "dot" },
        opacity: isSelected(m) ? 0.9 : 0.5,
        layer: "below",
      });
    }
    traces.push({
      name: "Milestones",
      showlegend: false,
      x: placedMilestones.map((m) => toDate(m.month)),
      // On a hidden 0-to-1 axis of their own (yaxis2, below), which keeps the range slider from redrawing them in miniature.
      yaxis: "y2",
      y: placedMilestones.map(railFraction),
      text: placedMilestones.map((m) => String(milestones.indexOf(m) + 1)),
      customdata: placedMilestones.map((m) => `${m.date_text}: ${m.title}`),
      mode: "markers+text",
      textposition: "middle right",
      textfont: { color: t.textPrimary, size: 12 },
      marker: {
        symbol: "diamond",
        size: placedMilestones.map((m) => (isSelected(m) ? 15 : 11)),
        color: placedMilestones.map((m) => (isSelected(m) ? t.series2 : t.textPrimary)),
        opacity: 1,
        line: { color: t.surface, width: 1.5 },
      },
      hovertemplate: "<b>Milestone %{text}</b> · %{customdata}<br><i>Click for details</i><extra></extra>",
    });
  }

  const layout = {
    ...baseLayout(t),
    showlegend: true,
    legend: { orientation: "h", x: 1, xanchor: "right", y: 1.02, yanchor: "bottom", traceorder: "normal", font: { size: 12 } },
    margin: { l: 56, r: 100, t: 44, b: 24 },  // right margin holds the end-of-line labels ("with rooftop" is the widest)
    // A tooltip appears only when the pointer is on (or within a few pixels of) a line or diamond, not for the whole
    // month under it: the old "x unified" box listed every line at once and covered the data.
    hovermode: "closest",
    hoverdistance: 14,
    xaxis: {
      type: "date",
      // Plotly pads an autoranged axis for the end-dot marker, which stretched this one about 18 months
      // past the data and made the "5y"/"10y" buttons count back from the wrong end. Cap the axis just
      // after the newest month (room for the dot). Date axes take this limit in milliseconds.
      autorangeoptions: { maxallowed: axisEnd(last.period) },
      tickformat: "%Y",
      hoverformat: "%B %Y",
      showgrid: false,
      linecolor: t.axis,
      tickcolor: t.axis,
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
      rangeslider: {
        visible: true,
        thickness: 0.1,
        bgcolor: t.surface,
        bordercolor: t.axis,
        borderwidth: 1,
      },
    },
    yaxis: {
      title: { text: "Renewable share", standoff: 8 },
      ticksuffix: "%",
      tickformat: ".0f",
      range: [0, yMax],
      fixedrange: true,
      gridcolor: t.grid,
      gridwidth: 1,
      zeroline: true,
      zerolinecolor: t.axis,
    },
    yaxis2: { overlaying: "y", range: [0, 1], visible: false, fixedrange: true },
    shapes,
    annotations,
  };

  // Redrawing for a theme change must not throw away a range the viewer already chose.
  const xa = chartDiv.layout && chartDiv.layout.xaxis;
  if (xa && xa.autorange === false && xa.range) {
    layout.xaxis.range = xa.range.slice();
  }

  // The range slider redraws every trace in miniature; the style sheet hides that copy of the milestones (the last
  // trace) so the diamonds do not show up clipped along the slider's top edge.
  chartDiv.classList.toggle("has-milestones", placedMilestones.length > 0);

  return Plotly.react(chartDiv, traces, layout, plotConfig);
}

// ---------------------------------------------------------------------------
// Milestones: the button strip and the card
// ---------------------------------------------------------------------------

function renderMilestoneCard() {
  const box = document.getElementById("milestone-card");
  const m = milestones.find((x) => x.id === selectedMilestone);
  if (!m) {
    box.textContent = "No milestone selected yet.";
    box.className = "plant-card milestone-card plant-card-empty";
    return;
  }
  box.className = "plant-card milestone-card";
  const h3 = document.createElement("h3");
  h3.textContent = m.title;
  const date = document.createElement("p");
  date.className = "plant-card-meta";
  date.textContent = m.date_text;
  const text = document.createElement("p");
  text.textContent = m.text;
  const source = document.createElement("p");
  source.className = "plant-card-note";
  const link = document.createElement("a");
  link.href = m.url;
  link.textContent = m.source;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  source.append("Source: ", link);
  box.replaceChildren(h3, date, text, source);
}

function selectMilestone(id) {
  selectedMilestone = id;
  for (const button of document.querySelectorAll("#milestone-buttons button")) {
    button.setAttribute("aria-pressed", String(button.dataset.id === id));
  }
  renderMilestoneCard();
  draw(view.rows, view.finalThrough); // redraw so the chosen diamond is highlighted
}

function setUpMilestones() {
  const section = document.getElementById("milestones");
  section.hidden = milestones.length === 0;
  const box = document.getElementById("milestone-buttons");
  box.replaceChildren();
  milestones.forEach((m, i) => {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.id = m.id;
    button.textContent = `${i + 1} · ${m.short}`;
    button.title = `${m.date_text}: ${m.title}`;
    button.setAttribute("aria-pressed", "false");
    button.addEventListener("click", () => selectMilestone(m.id));
    box.append(button);
  });
  renderMilestoneCard();
  if (!milestones.length) return;
  chartDiv.on("plotly_click", (event) => {
    const hit = event.points.find((p) => p.data.name === "Milestones");
    if (hit) selectMilestone(placedMilestones[hit.pointIndex].id);
  });
}

// ---------------------------------------------------------------------------
// Page text and the table view
// ---------------------------------------------------------------------------

function addNote(list, lead, text) {
  const li = document.createElement("li");
  const strong = document.createElement("strong");
  strong.textContent = `${lead} `;
  li.append(strong, document.createTextNode(text));
  list.append(li);
}

function renderNotes(meta, region, rows) {
  const list = document.getElementById("notes");
  list.replaceChildren();

  const renewable = meta.renewable_fuels.map((f) => FUEL_NAMES[f] ?? f).join(" + ");
  addNote(
    list,
    "What the line shows.",
    `${renewable} output as a share of all electricity generated by the ${region.plants_in_definition} ` +
      `grid-connected plants EIA lists for ${region.label}, month by month. Battery charging is not counted as generation.`
  );
  addNote(
    list,
    "What it leaves out.",
    "Rooftop solar is not in the solid line, because EIA's plant survey does not cover privately owned rooftop " +
      "panels. The dotted line adds an estimate of it, from 2014 when EIA's estimate begins, scaled to Oʻahu with " +
      "Hawaiian Electric's reported rooftop totals (see “About this data”). Because rooftop energy is added to both " +
      "the top and the bottom of the fraction, the dotted line is the share of all electricity used, not just what " +
      "utility-scale plants generated. Biomass and waste-to-energy are not counted as renewable in either line."
  );
  addNote(
    list,
    "Wind before 2011.",
    "Oʻahu had wind power before the chart's first wind. Earlier turbines at Kahuku (installed from 1985, " +
      "including a 3.2 MW Boeing MOD-5B running by 1987; the MOD-5B was shut down in 1996) are not in " +
      "EIA's plant data, which begins in 2001 and records no Oʻahu wind from then until early 2011. " +
      "March 2011 is when the current Kahuku Wind farm (30 MW) started, which makes it the first Oʻahu wind in " +
      "EIA's data, not the first on the island."
  );
  if (region.final_through) {
    const firstPrelim = rows.find((r) => r.provisional);
    addNote(
      list,
      "Preliminary months (shaded).",
      `Small plants only report to EIA once a year, so their output after ${formatMonth(region.final_through)} ` +
        `(from ${formatMonth(firstPrelim.period)}) is not published yet. Until it is, those months are missing ` +
        "these plants and read low."
    );
  }

  const generated = meta.generated_at.slice(0, 10);
  document.getElementById("source").textContent = `Source: ${meta.source}. Data refreshed ${generated}.`;
}

function renderTable(rows) {
  const table = document.getElementById("data-table");
  table.replaceChildren();

  const head = table.createTHead().insertRow();
  for (const label of [
    "Month",
    "Renewable share",
    "Solar (MWh)",
    "Wind (MWh)",
    "Total (MWh)",
    "Rooftop solar, estimated (MWh)",
    "Share incl. rooftop (estimate)",
    "Data",
  ]) {
    const th = document.createElement("th");
    th.scope = "col";
    th.textContent = label;
    head.append(th);
  }

  const body = table.createTBody();
  for (const r of [...rows].reverse()) {
    const tr = body.insertRow();
    if (r.provisional) tr.className = "provisional";
    const cells = [
      formatMonth(r.period),
      fmtPct(r.renewable_share_pct),
      fmtMwh(r.generation_by_fuel_mwh.SUN ?? 0),
      fmtMwh(r.generation_by_fuel_mwh.WND ?? 0),
      fmtMwh(r.total_mwh),
      r.rooftop_solar_mwh_est == null ? "–" : fmtMwh(r.rooftop_solar_mwh_est),
      r.renewable_share_incl_rooftop_pct == null ? "–" : fmtPct(r.renewable_share_incl_rooftop_pct),
      r.provisional ? "preliminary" : "complete",
    ];
    for (const text of cells) {
      tr.insertCell().textContent = text;
    }
  }
}

// ---------------------------------------------------------------------------
// Start-up
// ---------------------------------------------------------------------------

async function main() {
  if (typeof Plotly === "undefined") {
    throw new Error("The Plotly library could not be loaded (it is fetched from cdn.plot.ly, so this needs internet access).");
  }
  const [data, milestoneData] = await Promise.all([
    loadData(DATA_URL),
    loadData(MILESTONES_URL).catch(() => null), // the chart works without its milestones
  ]);

  // ?region=<id> picks a region; the default is the first one in the file.
  const requested = new URLSearchParams(location.search).get("region");
  const regionId = requested && data.regions[requested] ? requested : Object.keys(data.regions)[0];
  const region = data.regions[regionId];
  const rows = data.rows.filter((r) => r.region === regionId && r.renewable_share_pct !== null);
  if (!rows.length) {
    throw new Error(`No data for region "${regionId}".`);
  }

  document.getElementById("title").textContent = `${region.label}: renewable share of electricity generation`;
  document.getElementById("subtitle").textContent =
    `Monthly, ${formatMonth(rows[0].period)} to ${formatMonth(rows[rows.length - 1].period)}. ` +
    "Drag the slider under the chart, or use the buttons, to zoom in on a period.";
  chartDiv.setAttribute(
    "aria-label",
    `Line chart of ${region.label} renewable share of generation by month. The same numbers are in the table below the chart.`
  );

  milestones = (milestoneData && milestoneData.regions[regionId]) || [];
  view = { rows, finalThrough: region.final_through };
  renderNotes(data.meta, region, rows);
  renderTable(rows);
  await draw(rows, region.final_through);
  setUpMilestones(); // after the first draw: it attaches a click handler to the chart
  onThemeChange(() => draw(rows, region.final_through));
}

main().catch((err) => showError(chartDiv, err.message));
