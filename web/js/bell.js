// Small maths helpers for the Mānoa page's power-over-a-day chart. Pure functions: no page, no network - so they can be
// tested with `node --test` (see tests/js/bell.test.mjs).
//
// The data are 24 hourly averages. Times here are the MIDDLE of each hour (hour 12 = 12:00-1:00 PM is at 12.5), because
// an hourly average describes the whole hour, so the curve's peak should land at about the true solar-noon hour's middle.

/**
 * A smooth curve through the points that never overshoots (Fritsch-Carlson monotone cubic). An ordinary spline can dip
 * below zero at dawn and dusk, where a solar curve meets the axis; this one cannot: between two points it stays between them.
 * This traces the real (or modeled) hourly averages themselves - it is not a fitted curve of any particular shape.
 * @returns {(x: number) => number} the curve; it returns the end values outside the data.
 */
export function monotoneCubic(xs, ys) {
  const n = xs.length;
  const h = [];
  const d = [];
  for (let i = 0; i < n - 1; i++) {
    h.push(xs[i + 1] - xs[i]);
    d.push((ys[i + 1] - ys[i]) / h[i]);
  }
  const m = new Array(n);
  m[0] = d[0];
  m[n - 1] = d[n - 2];
  for (let i = 1; i < n - 1; i++) {
    if (d[i - 1] * d[i] <= 0) m[i] = 0; // a peak, a trough or a flat stretch: the curve must be level here
    else {
      const w1 = 2 * h[i] + h[i - 1];
      const w2 = h[i] + 2 * h[i - 1];
      m[i] = (w1 + w2) / (w1 / d[i - 1] + w2 / d[i]); // weighted harmonic mean of the neighbouring slopes
    }
  }
  return (x) => {
    if (x <= xs[0]) return ys[0];
    if (x >= xs[n - 1]) return ys[n - 1];
    let i = 0;
    while (x > xs[i + 1]) i++;
    const t = (x - xs[i]) / h[i];
    const t2 = t * t;
    const t3 = t2 * t;
    return (
      (2 * t3 - 3 * t2 + 1) * ys[i] + (t3 - 2 * t2 + t) * h[i] * m[i] + (-2 * t3 + 3 * t2) * ys[i + 1] + (t3 - t2) * h[i] * m[i + 1]
    );
  };
}

/** `count + 1` evenly spaced x values from `from` to `to`, with f(x) alongside. */
export function sample(f, from, to, count) {
  const x = [];
  const y = [];
  for (let i = 0; i <= count; i++) {
    const xi = from + ((to - from) * i) / count;
    x.push(xi);
    y.push(f(xi));
  }
  return { x, y };
}

/** Where a curve is at least half its peak: how many hours generation stays at half power or more. Searches [from, to] in
 * 1-minute steps, directly on the real (smoothed) curve - no fitted shape is assumed. */
export function halfPowerSpan(f, from, to, peak) {
  const half = peak / 2;
  let start = null;
  let end = null;
  const steps = Math.round((to - from) * 60);
  for (let i = 0; i <= steps; i++) {
    const x = from + i / 60;
    if (f(x) >= half) {
      if (start === null) start = x;
      end = x;
    }
  }
  return start === null ? null : { start, end, width: end - start };
}

/** 8.7 -> "8:42 AM"; 12 -> "12:00 PM"; 0 -> "12:00 AM". Rounds to the nearest minute. */
export function formatClock(hours) {
  const total = Math.round(hours * 60) % (24 * 60);
  const h24 = Math.floor(total / 60);
  const minute = total % 60;
  const h12 = h24 % 12 === 0 ? 12 : h24 % 12;
  return `${h12}:${String(minute).padStart(2, "0")} ${h24 < 12 ? "AM" : "PM"}`;
}

/** 12 -> "12–1 PM", 11 -> "11 AM–12 PM": the hour that BEGINS at `hour` (0-23). */
export function hourRangeLabel(hour) {
  const label = (h) => {
    const h24 = h % 24;
    const h12 = h24 % 12 === 0 ? 12 : h24 % 12;
    return { n: h12, ampm: h24 < 12 ? "AM" : "PM" };
  };
  const a = label(hour);
  const b = label(hour + 1);
  return a.ampm === b.ampm ? `${a.n}–${b.n} ${b.ampm}` : `${a.n} ${a.ampm}–${b.n} ${b.ampm}`;
}
