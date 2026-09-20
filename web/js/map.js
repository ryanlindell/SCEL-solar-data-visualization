// The plant map: Oʻahu's power plants appearing and disappearing over time, from EIA's generator inventory
// (EIA-860M, via web/data/plants.json). Drag the slider or press Play; click a plant for its units.
// The slider runs from the first unit EIA lists to the furthest-away planned unit, and opens on today.
//
// The map is drawn with Leaflet (plain SVG, works in every browser); the charts elsewhere use Plotly.

import { formatMonth, loadData, onThemeChange, showError, themeColors } from "./common.js";

const DATA_URL = "data/plants.json";
// OpenStreetMap's own tiles need no key (CARTO's basemaps now do). They are light only; the stylesheet darkens
// them in dark mode with a CSS filter. Please keep use light: https://operations.osmfoundation.org/policies/tiles/
const TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';
// Each fuel group's color: a fixed slot of the shared palette (coal and "other" use neutral inks).
const GROUP_COLORS = { solar: "series1", oil: "series2", wind: "series3", waste: "series4", battery: "series5", coal: "textSecondary", other: "muted" };
const GROUP_CSS_VARS = { solar: "--series-1", oil: "--series-2", wind: "--series-3", waste: "--series-4", battery: "--series-5", coal: "--text-secondary", other: "--muted" };
const OAHU_BOUNDS = [[21.2, -158.35], [21.75, -157.6]];
// Play sweeps the timeline at a fixed speed: a year every 0.11 s (about 109 months a second). It is measured
// against the clock, so the slider moves a month at a time on every screen refresh instead of jumping a year.
const PLAY_MONTHS_PER_SECOND = 12 / 0.11;

const mapDiv = document.getElementById("map");
const slider = document.getElementById("time");
const playButton = document.getElementById("play");
const todayButton = document.getElementById("today");

// ---------------------------------------------------------------------------
// What was in service when
// ---------------------------------------------------------------------------

/** "YYYY-MM" strings from `first` to `last`, inclusive. */
function monthRange(first, last) {
  const [y0, m0] = first.split("-").map(Number);
  const [y1, m1] = last.split("-").map(Number);
  const out = [];
  for (let y = y0, m = m0; y < y1 || (y === y1 && m <= m1); m === 12 ? (y++, (m = 1)) : m++) {
    out.push(`${y}-${String(m).padStart(2, "0")}`);
  }
  return out;
}

/** The current month as "YYYY-MM", in the viewer's own time zone. */
function currentMonth(now = new Date()) {
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
}

/** A unit is in service from its in-service month until the month it was retired (exclusive). "YYYY-MM" sorts as text.
 *  A planned unit is never "in service": it has not been built as of EIA's latest file. */
const inService = (unit, month) => !unit.planned && unit.online <= month && (unit.retired === null || unit.retired > month);

/** A planned unit shows from its expected month, but only after the last month EIA has records for (`asOf`):
 *  before that we know it was not in service. One whose date has already slipped past shows from the month after `asOf`. */
const isPlannedOn = (unit, month, asOf) => unit.planned && month > asOf && month >= unit.online;

/** Capacity by fuel group for some units, the group that colors them, and their totals. */
function summarize(units) {
  const byGroup = {};
  for (const u of units) {
    const g = (byGroup[u.group] ??= { mw: 0, mwh: 0, mwhKnown: false });
    g.mw += u.mw;
    if (u.mwh != null) {
      g.mwh += u.mwh;
      g.mwhKnown = true;
    }
  }
  const generating = Object.entries(byGroup).filter(([group]) => group !== "battery");
  const dominant = generating.length ? generating.reduce((a, b) => (b[1].mw > a[1].mw ? b : a))[0] : units.length ? "battery" : null;
  return {
    units,
    byGroup,
    dominant,
    mwAll: units.reduce((sum, u) => sum + u.mw, 0), // includes batteries: sets the circle's size
    mwGenerating: generating.reduce((sum, [, g]) => sum + g.mw, 0), // excludes batteries
  };
}

/** A plant's state on a date: what is in service (and its capacity by fuel group) and what is planned. */
function plantAt(plant, month, asOf) {
  return {
    ...summarize(plant.units.filter((u) => inService(u, month))),
    planned: summarize(plant.units.filter((u) => isPlannedOn(u, month, asOf))),
  };
}

const fmtMw = (mw) => `${mw >= 100 ? Math.round(mw).toLocaleString("en-US") : mw.toFixed(1).replace(/\.0$/, "")} MW`;

// ---------------------------------------------------------------------------
// Building the page
// ---------------------------------------------------------------------------

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "className") node.className = v;
    else if (k === "text") node.textContent = v;
    else node.setAttribute(k, v);
  }
  node.append(...children);
  return node;
}

function renderStats(state, at) {
  const plantsOnline = at.filter((a) => a.state.units.length > 0);
  const generating = plantsOnline.reduce((sum, a) => sum + a.state.mwGenerating, 0);
  const renewable = plantsOnline.reduce((sum, a) => sum + (a.state.byGroup.solar?.mw ?? 0) + (a.state.byGroup.wind?.mw ?? 0), 0);
  const tiles = [
    ["Plants", String(plantsOnline.length)],
    ["Generating capacity", fmtMw(generating)],
    ["Solar + wind capacity", fmtMw(renewable)],
  ];
  const planned = at.filter((a) => a.state.planned.units.length > 0);
  if (planned.length) {
    const mw = planned.reduce((sum, a) => sum + a.state.planned.mwGenerating, 0);
    tiles.push([`Planned, not yet built`, `${planned.length} ${planned.length === 1 ? "plant" : "plants"}, ${fmtMw(mw)}`]);
  }
  const box = document.getElementById("stats");
  box.replaceChildren(
    ...tiles.map(([label, value]) => el("div", { className: "stat" }, el("div", { className: "stat-label", text: label }), el("div", { className: "stat-value", text: value })))
  );
}

function renderGroupTable(state, at) {
  const table = document.getElementById("group-table");
  table.replaceChildren();
  const anyPlanned = at.some((a) => a.state.planned.units.length > 0);
  const head = table.createTHead().insertRow();
  for (const label of ["Fuel", "Plants", "MW", ...(anyPlanned ? ["Planned"] : [])]) head.append(el("th", { scope: "col", text: label }));
  if (anyPlanned) head.cells[3].title = "Planned capacity in MW: units EIA lists as planned, not built yet";
  const body = table.createTBody();
  for (const g of state.groups) {
    const rows = at.filter((a) => a.state.byGroup[g.key]);
    const plannedRows = at.filter((a) => a.state.planned.byGroup[g.key]);
    if (!rows.length && !plannedRows.length) continue;
    const mw = rows.reduce((sum, a) => sum + a.state.byGroup[g.key].mw, 0);
    const tr = body.insertRow();
    const nameCell = tr.insertCell();
    nameCell.append(el("span", { className: "swatch", style: `background: var(${GROUP_CSS_VARS[g.key]})` }), g.label);
    tr.insertCell().textContent = String(rows.length);
    tr.insertCell().textContent = rows.length ? fmtMw(mw) : "–";
    if (anyPlanned) {
      const plannedMw = plannedRows.reduce((sum, a) => sum + a.state.planned.byGroup[g.key].mw, 0);
      tr.insertCell().textContent = plannedRows.length ? `+${fmtMw(plannedMw).replace(" MW", "")}` : "–"; // the MW column beside it gives the unit
    }
  }
  if (!body.rows.length) {
    const tr = body.insertRow();
    const cell = tr.insertCell();
    cell.colSpan = 3;
    cell.textContent = "No plants in service yet.";
  }
}

function renderList(state, at) {
  const table = document.getElementById("data-table");
  table.replaceChildren();
  const head = table.createTHead().insertRow();
  for (const label of ["Plant", "Fuel", "Capacity", "Owner"]) head.append(el("th", { scope: "col", text: label }));
  const body = table.createTBody();
  const size = (a) => a.state.mwAll + a.state.planned.mwAll;
  for (const a of at.filter((x) => x.state.units.length || x.state.planned.units.length).sort((p, q) => size(q) - size(p))) {
    const tr = body.insertRow();
    const button = el("button", { type: "button", className: "link-button", text: a.plant.name });
    button.addEventListener("click", () => selectPlant(state, a.plant));
    tr.insertCell().append(button);
    tr.insertCell().textContent = state.groupLabels[a.state.dominant ?? a.state.planned.dominant] ?? "";
    const capacity = [];
    if (a.state.units.length) capacity.push(`${fmtMw(a.state.mwAll)} in service`);
    if (a.state.planned.units.length) capacity.push(`${fmtMw(a.state.planned.mwAll)} planned`);
    tr.insertCell().textContent = capacity.join(", ");
    tr.insertCell().textContent = a.plant.owner;
  }
}

/** The card shown when a plant is clicked: its units, with those in service on the selected date in bold. */
function renderCard(state) {
  const box = document.getElementById("plant-card");
  const plant = state.selected;
  if (!plant) return;
  const month = state.months[Number(slider.value)];
  const anyPlanned = plant.units.some((u) => u.planned);
  const counted = plant.counted_in_share_chart
    ? "Yes, it is one of the plants in the renewable-share chart."
    : plant.excluded_reason
      ? `No. ${plant.excluded_reason}.`
      : anyPlanned && plant.units.every((u) => u.planned)
        ? "Not yet: it has not been built."
        : "No, it is not in the renewable-share chart's plant list.";
  const table = el("table", { className: "unit-table" });
  const head = table.createTHead().insertRow();
  for (const label of ["Unit", "Fuel", "MW", "MWh", "In service", "Retired"]) head.append(el("th", { scope: "col", text: label }));
  const body = table.createTBody();
  for (const u of plant.units) {
    const tr = body.insertRow();
    tr.className = inService(u, month) ? "unit-now" : isPlannedOn(u, month, state.asOf) ? "unit-planned" : "unit-not-now";
    const mwh = u.mwh == null ? "–" : u.mwh.toLocaleString("en-US", { maximumFractionDigits: 1 });
    const inServiceText = u.planned ? `planned ${formatMonth(u.online)}` : formatMonth(u.online);
    const retiredText = u.planned ? "–" : u.retired ? formatMonth(u.retired) : "still operating";
    [u.id, u.technology, u.mw.toLocaleString("en-US", { maximumFractionDigits: 1 }), u.group === "battery" ? mwh : "–", inServiceText, retiredText].forEach((text) => {
      tr.insertCell().textContent = text;
    });
    if (u.planned) tr.cells[4].title = `EIA's stage for this unit: ${u.status}`;
    if (u.corrections.length) {
      tr.cells[3].title = `EIA lists ${u.corrections[0].as_filed} MWh; corrected to ${u.corrections[0].used} MWh. ${u.corrections[0].reason}`;
      tr.cells[3].textContent += " *";
    }
  }
  const stages = [...new Set(plant.units.filter((u) => u.planned).map((u) => u.status))];
  box.replaceChildren(
    el("h2", { text: plant.name }),
    el("p", { className: "plant-card-meta", text: `${plant.owner} · ${plant.sector}` }),
    table,
    el("p", { className: "plant-card-note", text: `Bold rows are in service in ${formatMonth(month)}. Counted in the renewable-share chart? ${counted}` }),
    ...(anyPlanned
      ? [
          el("p", {
            className: "plant-card-note",
            text: `Planned units are not built yet: the month is the operator's expectation and can slip. EIA's stage: ${stages.join("; ")}.`,
          }),
        ]
      : []),
    ...(plant.units.some((u) => u.corrections.length)
      ? [el("p", { className: "plant-card-note", text: "* EIA's published energy figure for this battery is implausible; the corrected value is shown (hover for the reason)." })]
      : [])
  );
}

function selectPlant(state, plant) {
  state.selected = plant;
  renderCard(state);
  const m = state.markers.get(plant.code);
  if (m) state.map.panTo(m.marker.getLatLng(), { animate: true });
  document.getElementById("plant-card").scrollIntoView({ block: "nearest", behavior: "smooth" });
}

// ---------------------------------------------------------------------------
// Drawing the map for one date
// ---------------------------------------------------------------------------

function tooltipContent(state, plant, s) {
  const describe = (summary) =>
    Object.entries(summary.byGroup)
      .map(([g, v]) => `${state.groupLabels[g] ?? g} ${fmtMw(v.mw)}${g === "battery" && v.mwhKnown ? ` / ${Math.round(v.mwh).toLocaleString("en-US")} MWh` : ""}`)
      .join(" · ");
  const lines = [el("strong", { text: plant.name })];
  if (s.units.length) lines.push(el("br"), document.createTextNode(describe(s)));
  if (s.planned.units.length) {
    const soonest = s.planned.units.reduce((a, b) => (b.online < a.online ? b : a)).online;
    lines.push(el("br"), document.createTextNode(`${s.units.length ? "Also planned" : "Planned"}: ${describe(s.planned)} (expected ${formatMonth(soonest)})`));
  }
  return el("div", {}, ...lines);
}

/** Circles for one date. `full` false is the lighter update used while the timeline is playing. */
function renderMap(state, { full = true } = {}) {
  const month = state.months[Number(slider.value)];
  const colors = themeColors();
  const at = state.plants.map((plant) => ({ plant, state: plantAt(plant, month, state.asOf) }));

  for (const { plant, state: s } of at) {
    const entry = state.markers.get(plant.code);
    if (!entry) continue; // a plant with no coordinates
    const built = s.units.length > 0;
    if (!built && !s.planned.units.length) {
      if (state.map.hasLayer(entry.marker)) entry.marker.remove();
      continue;
    }
    if (built) {
      const color = colors[GROUP_COLORS[s.dominant]] ?? colors.muted;
      entry.marker.setStyle({ radius: Math.min(32, 4 + 1.15 * Math.sqrt(s.mwAll)), fillColor: color, color: colors.surface, weight: 1.5, fillOpacity: 0.85, dashArray: null });
    } else {
      // Planned only: a faint circle with a dashed outline, sized by the planned capacity.
      const color = colors[GROUP_COLORS[s.planned.dominant]] ?? colors.muted;
      entry.marker.setStyle({ radius: Math.min(32, 4 + 1.15 * Math.sqrt(s.planned.mwAll)), fillColor: color, color, weight: 2, fillOpacity: 0.2, dashArray: "4 3" });
    }
    entry.marker.setTooltipContent(tooltipContent(state, plant, s));
    if (!state.map.hasLayer(entry.marker)) entry.marker.addTo(state.map);
  }
  // Big circles underneath, small ones on top, so nothing hides a neighbor.
  const size = (a) => a.state.mwAll + a.state.planned.mwAll;
  [...at]
    .filter((a) => (a.state.units.length || a.state.planned.units.length) && state.markers.get(a.plant.code))
    .sort((p, q) => size(q) - size(p))
    .forEach((a) => state.markers.get(a.plant.code).marker.bringToFront());

  document.getElementById("time-label").textContent = formatMonth(month);
  slider.setAttribute("aria-valuetext", formatMonth(month));
  const note = document.getElementById("time-note");
  note.textContent =
    month > state.asOf
      ? `After ${formatMonth(state.asOf)} (the latest EIA file) the map shows only plants EIA lists as planned. Their dates are expectations and can slip.`
      : `EIA's records run through ${formatMonth(state.asOf)}. Past that, the slider shows planned plants.`;
  note.classList.toggle("time-note-plan", month > state.asOf);
  renderStats(state, at);
  renderGroupTable(state, at);
  if (full) {
    renderList(state, at);
    renderCard(state);
  }
}

// ---------------------------------------------------------------------------
// Text
// ---------------------------------------------------------------------------

function renderExplainer(meta, region) {
  const box = document.getElementById("explainer-body");
  const paragraph = (text) => box.append(el("p", { text }));
  paragraph(
    "Every plant here comes from Form EIA-860, the U.S. Energy Information Administration's inventory of power plants. " +
      "A plant must report if two things are true: its generators add up to 1 megawatt (MW) or more of capacity, and it is " +
      "connected to the electric grid, meaning it can deliver power to the grid or draw power from it. The plant's operator " +
      "files the form each year, and EIA publishes a monthly update (EIA-860M), which is what this map uses. For every " +
      "generator it records the size, fuel and technology, location, the month it entered service, the month it was " +
      "retired, and for batteries how much energy they can store."
  );
  paragraph(
    "What that leaves out: rooftop solar and other small systems (under 1 MW in total), generators that are not connected to the " +
      "grid, and anything that closed before EIA's records begin. In the monthly file, retirements are listed only from " +
      "2002 on, so a plant that closed earlier does not appear at all, even if it once supplied Oʻahu. That is why the early " +
      "years look so sparse: in 1947 the map shows a single plant because the others from that era have since closed and are " +
      "not in EIA's list. Oʻahu's first wind turbines at Kahuku, built in the 1980s, are missing for the same reason (the " +
      "last of them, the Boeing MOD-5B, was shut down in 1996)."
  );
  paragraph(
    "Which plants: every unit EIA places in Honolulu County, which is Oʻahu. Two refinery cogeneration plants (Tesoro/Par Hawaii " +
      "and Hawaii Cogen) are on the map, but they make power for the refinery rather than the grid, so they are not counted in " +
      "the renewable-share chart; the plant card says which plants are. Circle size is a plant's capacity in service on the " +
      "selected date, and color is the fuel of its largest units."
  );
  paragraph(
    `Planned plants: the same monthly file has a Planned sheet, the units whose operators have told EIA they intend to build them, ` +
      `each with an expected month and a stage, from under construction to planned with regulatory approvals not yet started. ` +
      `Past ${formatMonth(region.as_of)}, the newest month EIA has records for, the slider shows those units as dashed circles; ` +
      `it ends at the furthest expected month, ${formatMonth(region.last_planned_month)}. Expected months are the operators' ` +
      `and often slip, and a plant that is cancelled or postponed drops off EIA's Planned sheet, so the future view can change ` +
      `from one month's file to the next. EIA lists no planned retirements for Oʻahu, so the future view can only add plants: ` +
      `it does not show any existing plant closing. Planned plants are not counted in the in-service numbers.`
  );
}

function renderNotes(meta, region) {
  const list = document.getElementById("notes");
  const note = (lead, text) => list.append(el("li", {}, el("strong", { text: `${lead} ` }), document.createTextNode(text)));
  note("Capacity, not generation.", "The map shows how big each plant is, not how much it produced or whether it was running. A large plant can sit idle and a small one run all year.");
  note("Dates.", "In-service and retirement dates are EIA's, to the month. Where EIA gives no month, January is used. Units EIA lists as standby or out of service still count as in service.");
  note("Locations.", "Coordinates are EIA's for each plant and are approximate; plants at one site share a circle.");
  document.getElementById("source").textContent =
    `Source: ${meta.source}, file ${Object.values(meta.source_files)[0]} (data as of ${formatMonth(region.as_of)}). Refreshed ${meta.generated_at.slice(0, 10)}.`;
}

// ---------------------------------------------------------------------------
// Start-up
// ---------------------------------------------------------------------------

async function main() {
  if (typeof L === "undefined") {
    throw new Error("The map library (Leaflet) could not be loaded; it is fetched from cdn.jsdelivr.net, so this needs internet access.");
  }
  const data = await loadData(DATA_URL);
  const query = new URLSearchParams(location.search);
  const regionId = data.regions[query.get("region")] ? query.get("region") : Object.keys(data.regions)[0];
  const region = data.regions[regionId];
  const plants = data.plants.filter((p) => p.region === regionId);
  const lastMonth = region.last_planned_month && region.last_planned_month > region.as_of ? region.last_planned_month : region.as_of;
  const months = monthRange(region.first_month, lastMonth);

  const state = {
    plants,
    months,
    asOf: region.as_of,
    groups: data.meta.groups,
    groupLabels: Object.fromEntries(data.meta.groups.map((g) => [g.key, g.label])),
    markers: new Map(),
    selected: null,
    map: null,
  };

  document.getElementById("title").textContent = `${region.label}'s power plants over time`;
  document.getElementById("subtitle").textContent =
    `Every grid-connected plant of 1 MW or more that EIA lists for ${region.label}, from ${formatMonth(region.first_month)} to ${formatMonth(region.as_of)}, ` +
    (lastMonth > region.as_of ? `then the plants planned through ${formatMonth(lastMonth)}. ` : ". ") +
    "It opens on today; drag the slider or press Play to watch plants come online and retire.";

  // Whole-number zoom levels only: fractional zooms scale the tiles and leave faint seams between them.
  state.map = L.map(mapDiv, { zoomSnap: 1, minZoom: 9, maxBounds: [[20.9, -158.7], [22.0, -157.3]] });
  state.map.fitBounds(OAHU_BOUNDS);
  L.tileLayer(TILE_URL, { attribution: ATTRIBUTION, maxZoom: 18 }).addTo(state.map);
  for (const plant of plants) {
    if (plant.lat == null || plant.lon == null) continue;
    const marker = L.circleMarker([plant.lat, plant.lon], { radius: 6 });
    marker.bindTooltip(document.createElement("div"), { direction: "top", offset: [0, -4] });
    marker.on("click", () => selectPlant(state, plant));
    state.markers.set(plant.code, { marker });
  }

  // Today, kept inside the slider's range (a viewer opening this in 2031 lands on the last month).
  const todayIndex = () => {
    const now = currentMonth();
    return now <= months[0] ? 0 : now >= months[months.length - 1] ? months.length - 1 : months.indexOf(now);
  };
  const requested = query.get("date");
  slider.max = String(months.length - 1);
  slider.value = String(requested && months.includes(requested) ? months.indexOf(requested) : todayIndex());
  slider.addEventListener("input", () => {
    stop();
    renderMap(state);
  });

  // Play: advance by the clock, one screen refresh at a time. The lighter draw skips the plant list and card
  // while the timeline moves; both are redrawn in full when it stops.
  let frame = null;
  const stop = () => {
    if (frame === null) return;
    cancelAnimationFrame(frame);
    frame = null;
    playButton.textContent = "▶ Play";
    playButton.setAttribute("aria-label", "Play the timeline");
    renderMap(state);
  };
  playButton.addEventListener("click", () => {
    if (frame !== null) return stop();
    if (Number(slider.value) >= months.length - 1) slider.value = "0"; // pressing Play at the end starts over
    playButton.textContent = "❚❚ Pause";
    playButton.setAttribute("aria-label", "Pause the timeline");
    const startIndex = Number(slider.value);
    let startTime = null;
    const tick = (now) => {
      startTime ??= now;
      const index = Math.min(months.length - 1, startIndex + Math.floor(((now - startTime) / 1000) * PLAY_MONTHS_PER_SECOND));
      if (index !== Number(slider.value)) {
        slider.value = String(index);
        renderMap(state, { full: false });
      }
      if (index >= months.length - 1) return stop();
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
  });
  todayButton.addEventListener("click", () => {
    if (frame !== null) stop();
    slider.value = String(todayIndex());
    renderMap(state);
  });

  renderExplainer(data.meta, region);
  renderNotes(data.meta, region);
  renderMap(state);
  onThemeChange(() => renderMap(state)); // recolor the circles; the tiles darken themselves through CSS
  window.__mapState = state; // exposed for the browser tests
}

main().catch((err) => showError(mapDiv, err.message));
