import assert from "node:assert/strict";
import { test } from "node:test";

import { formatClock, halfPowerSpan, hourRangeLabel, monotoneCubic, sample } from "../../web/js/bell.js";

// 24 hourly averages of a bell-shaped day centred at 12.5 (the middle of the 12-1 PM hour) with sigma 2.5 h, peak 800.
// (Used only as a realistic-looking test fixture here - the curve code itself does not assume or fit any particular shape.)
const HOURS = Array.from({ length: 24 }, (_, i) => i + 0.5);
const bell = (mu, sigma, peak) => HOURS.map((x) => peak * Math.exp(-((x - mu) ** 2) / (2 * sigma * sigma)));

test("the smooth curve passes exactly through every data point", () => {
  const ys = bell(12.5, 2.5, 800);
  const f = monotoneCubic(HOURS, ys);
  HOURS.forEach((x, i) => assert.ok(Math.abs(f(x) - ys[i]) < 1e-9, `hour ${i}`));
});

test("it never dips below zero or rises above the data, even where the curve meets the axis at dawn and dusk", () => {
  const ys = [0, 0, 0, 0, 0, 0, 40, 300, 620, 810, 830, 700, 400, 120, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0];
  const f = monotoneCubic(HOURS, ys);
  const { y } = sample(f, 0.5, 23.5, 2000);
  assert.ok(Math.min(...y) >= 0, `lowest ${Math.min(...y)}`); // an ordinary cubic spline goes negative here
  assert.ok(Math.max(...y) <= Math.max(...ys) + 1e-9);
  assert.equal(f(0.5 + 1e-3) >= 0, true);
});

test("between two neighbouring points the curve stays between them (no wiggles)", () => {
  const ys = bell(12.5, 2.5, 800);
  const f = monotoneCubic(HOURS, ys);
  for (let i = 0; i < 23; i++) {
    const lo = Math.min(ys[i], ys[i + 1]) - 1e-9;
    const hi = Math.max(ys[i], ys[i + 1]) + 1e-9;
    for (let k = 0; k <= 10; k++) {
      const v = f(HOURS[i] + k / 10);
      assert.ok(v >= lo && v <= hi, `between hours ${i} and ${i + 1}: ${v}`);
    }
  }
});

test("a real, lopsided day (bright morning, dim afternoon) is followed exactly too, no smoothing toward symmetry", () => {
  const lopsided = bell(11.5, 2.0, 800).map((y, i) => (HOURS[i] > 11.5 ? y * 0.7 : y));
  const f = monotoneCubic(HOURS, lopsided);
  HOURS.forEach((x, i) => assert.ok(Math.abs(f(x) - lopsided[i]) < 1e-9, `hour ${i}`));
});

test("the width at half power of a bell-shaped day is 2.355 sigma, read straight off the curve", () => {
  const ys = bell(12.5, 2.5, 800);
  const f = monotoneCubic(HOURS, ys);
  const span = halfPowerSpan(f, 0.5, 23.5, Math.max(...ys));
  assert.ok(Math.abs(span.width - 2.3548 * 2.5) / (2.3548 * 2.5) < 0.03, `width ${span.width}`);
  assert.ok(Math.abs((span.start + span.end) / 2 - 12.5) < 0.05);
});

test("the half-power width of a flat-topped day still comes out right (no bell shape assumed)", () => {
  const flat = HOURS.map((x) => (x > 8 && x < 17 ? 800 : 0)); // on from 8 to 17, off otherwise
  const f = monotoneCubic(HOURS, flat);
  const span = halfPowerSpan(f, 0.5, 23.5, 800);
  assert.ok(Math.abs(span.width - 9) < 0.5, `width ${span.width}`); // the whole on-period is at or above half power
});

test("no half-power span for a day with no sun", () => {
  assert.equal(halfPowerSpan(() => 0, 0, 24, 0.0001), null);
});

test("clock times read naturally, including noon, midnight and rounding up to the next hour", () => {
  assert.equal(formatClock(8.7), "8:42 AM");
  assert.equal(formatClock(12), "12:00 PM");
  assert.equal(formatClock(12.5), "12:30 PM");
  assert.equal(formatClock(0), "12:00 AM");
  assert.equal(formatClock(13.5), "1:30 PM");
  assert.equal(formatClock(11.9999), "12:00 PM");
  assert.equal(formatClock(23.999), "12:00 AM");
});

test("an hour that begins at h is labelled as the interval it covers", () => {
  assert.equal(hourRangeLabel(12), "12–1 PM");
  assert.equal(hourRangeLabel(11), "11 AM–12 PM");
  assert.equal(hourRangeLabel(6), "6–7 AM");
  assert.equal(hourRangeLabel(23), "11 PM–12 AM");
  assert.equal(hourRangeLabel(0), "12–1 AM");
});
