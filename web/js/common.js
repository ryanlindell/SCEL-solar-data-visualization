// Shared helpers for every chart page. Later tiers (duck curve, curtailment, battery
// dispatch) import these too, so all charts load data, theme and configure Plotly the same way.

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** Fetch a JSON file written by the pipeline. */
export async function loadData(url) {
  if (location.protocol === "file:") {
    throw new Error(
      "Browsers block a page opened straight from disk from reading the data file. " +
        "Serve the web/ folder instead: run `python -m http.server` inside web/ and open http://localhost:8000."
    );
  }
  const response = await fetch(url, { cache: "no-cache" });
  if (!response.ok) {
    throw new Error(`Could not load ${url} (HTTP ${response.status}). Has the pipeline been run?`);
  }
  return response.json();
}

/** "2026-06" -> "Jun 2026" */
export function formatMonth(period) {
  const [year, month] = period.split("-");
  return `${MONTHS[Number(month) - 1]} ${year}`;
}

/** The current colors, read from the CSS variables in style.css (they change in dark mode). */
export function themeColors() {
  const css = getComputedStyle(document.documentElement);
  const get = (name) => css.getPropertyValue(name).trim();
  return {
    surface: get("--surface"),
    textPrimary: get("--text-primary"),
    textSecondary: get("--text-secondary"),
    muted: get("--muted"),
    grid: get("--grid"),
    axis: get("--axis"),
    series1: get("--series-1"),
    series2: get("--series-2"),
    series3: get("--series-3"),
    series4: get("--series-4"),
    series5: get("--series-5"),
  };
}

/** "#2a78d6" + 0.15 -> "rgba(42,120,214,0.15)" (Plotly wants rgba for translucent fills). */
export function withAlpha(hex, alpha) {
  const n = parseInt(hex.replace("#", ""), 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}

/** Plotly layout settings every chart shares; a chart adds its own axes/traces on top. */
export function baseLayout(t) {
  return {
    paper_bgcolor: t.surface,
    plot_bgcolor: t.surface,
    font: { family: 'system-ui, -apple-system, "Segoe UI", sans-serif', size: 13, color: t.textSecondary },
    hoverlabel: {
      bgcolor: t.surface,
      bordercolor: t.axis,
      font: { color: t.textPrimary, size: 13 },
    },
    showlegend: false,
  };
}

export const plotConfig = {
  responsive: true,
  displaylogo: false,
  modeBarButtonsToRemove: ["lasso2d", "select2d", "autoScale2d"],
};

/** Hours (0-23) to label on a 24-hour axis: every 3 hours, or every 6 on a phone where labels would crowd. */
export function hourTickValues() {
  return window.innerWidth < 600 ? [0, 6, 12, 18] : [0, 3, 6, 9, 12, 15, 18, 21];
}

/** Call `callback` when the viewer switches between light and dark mode. */
export function onThemeChange(callback) {
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", callback);
}

/** Show a readable message in place of a chart. */
export function showError(container, message) {
  container.replaceChildren();
  const p = document.createElement("p");
  p.className = "error";
  p.textContent = message;
  container.append(p);
}
