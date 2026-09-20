// Run with:  node --test tests/js
import assert from "node:assert/strict";
import test from "node:test";

import { netLoadStats, simulateDispatch } from "../../web/js/dispatch.js";

const close = (actual, expected, tolerance = 1e-6, message = "") =>
  assert.ok(Math.abs(actual - expected) <= tolerance, `${message} expected ${expected}, got ${actual}`);
const max = (hours, key) => Math.max(...hours.map((h) => h[key]));
const min = (hours, key) => Math.min(...hours.map((h) => h[key]));

/** A duck-shaped day: 600 MW, dipping to 300 around midday, peaking at 900 in the evening. */
function duck() {
  const net = new Array(24).fill(600);
  [10, 11, 12, 13, 14].forEach((h) => (net[h] = 300));
  [18, 19, 20].forEach((h) => (net[h] = 900));
  return net;
}

// ---- netLoadStats mirrors the Python version (tests/test_process_hourly.py) ---------------------------

test("netLoadStats finds the belly, the evening peak and the climb - not the overnight trough", () => {
  const net = new Array(24).fill(400); // a low overnight trough must NOT be taken for the belly
  for (let h = 9; h < 24; h++) net[h] = 1000;
  net[12] = 500;
  net[15] = 900;
  net[16] = 900;
  net[19] = 1250;
  const s = netLoadStats(net);
  assert.equal(s.middayLowHour, 12);
  assert.equal(s.middayLowMw, 500);
  assert.equal(s.eveningPeakHour, 19);
  assert.equal(s.eveningPeakMw, 1250);
  assert.equal(s.eveningClimbMw, 750);
  assert.equal(s.steepest3hRampStartHour, 12);
  assert.equal(s.steepest3hRampMw, 400);
});

// ---- no battery ----------------------------------------------------------------------------------

test("with no battery, net load is unchanged and nothing moves", () => {
  for (const args of [{ power: 0, energy: 500 }, { power: 100, energy: 0 }]) {
    const r = simulateDispatch({ net: duck(), efficiency: 0.85, ...args });
    assert.deepEqual(r.hours.map((h) => h.netAfter), duck());
    assert.equal(r.chargedMwh, 0);
    assert.equal(r.dischargedMwh, 0);
    assert.equal(r.cyclesPerDay, 0);
  }
});

test("with no battery, anything below the fossil floor is curtailed", () => {
  const r = simulateDispatch({ net: duck(), power: 0, energy: 0, efficiency: 0.85, floor: 400 });
  close(r.curtailedBeforeMwh, 5 * 100); // hours 10-14, 100 MW under the floor
  close(r.curtailedAfterMwh, 5 * 100);
  assert.ok(min(r.hours, "netAfter") >= 400 - 1e-9);
});

// ---- what a battery does to the duck --------------------------------------------------------------

test("a battery shaves the evening peak and raises the midday low", () => {
  const r = simulateDispatch({ net: duck(), power: 200, energy: 600, efficiency: 0.9 });
  assert.ok(r.after.eveningPeakMw < r.before.eveningPeakMw - 50, `peak ${r.before.eveningPeakMw} -> ${r.after.eveningPeakMw}`);
  assert.ok(r.after.middayLowMw > r.before.middayLowMw + 50, `low ${r.before.middayLowMw} -> ${r.after.middayLowMw}`);
  assert.ok(r.after.steepest3hRampMw < r.before.steepest3hRampMw);
  // It charges in the valley and discharges at the peak, not the other way round.
  assert.ok(r.hours[12].charge > 0 && r.hours[12].discharge === 0);
  assert.ok(r.hours[19].discharge > 0 && r.hours[19].charge === 0);
});

test("a flat day gives the battery nothing to do", () => {
  const r = simulateDispatch({ net: new Array(24).fill(500), power: 200, energy: 600, efficiency: 0.85 });
  close(r.chargedMwh, 0);
  close(r.dischargedMwh, 0);
});

test("a lossless battery on a peak/valley day flattens it exactly as far as its energy allows", () => {
  // 100 MW below and above a 500 MW baseline for one hour each; a 100 MW / 100 MWh lossless battery can fix both.
  const net = new Array(24).fill(500);
  net[12] = 400;
  net[19] = 600;
  const r = simulateDispatch({ net, power: 100, energy: 100, efficiency: 1 });
  close(r.hours[12].netAfter, 500, 0.5);
  close(r.hours[19].netAfter, 500, 0.5);
});

// ---- physical consistency -------------------------------------------------------------------------

test("the battery obeys its limits and conserves energy", () => {
  for (const efficiency of [0.7, 0.85, 1]) {
    const power = 150;
    const energy = 400;
    const r = simulateDispatch({ net: duck(), power, energy, efficiency, floor: 0 });
    const eta = Math.sqrt(efficiency);
    for (const h of r.hours) {
      assert.ok(h.charge >= 0 && h.charge <= power + 1e-6, `charge ${h.charge}`);
      assert.ok(h.discharge >= 0 && h.discharge <= power + 1e-6, `discharge ${h.discharge}`);
      assert.ok(!(h.charge > 0 && h.discharge > 0), "charging and discharging in the same hour");
      assert.ok(h.soc >= -1e-9 && h.soc <= energy + 1e-9, `state of charge ${h.soc}`);
    }
    // Energy in (after charging losses) - energy out (before discharging losses) = change in the battery.
    const stored = r.chargedMwh * eta - r.dischargedMwh / eta;
    close(stored, r.hours[23].soc - r.socStartMwh, 1e-6, `efficiency ${efficiency}:`);
    // A steady-state day: the battery ends about where it started.
    assert.ok(Math.abs(r.hours[23].soc - r.socStartMwh) <= energy * 0.02, "day should be roughly cyclic");
    // Round-trip losses: you get back at most `efficiency` of what you put in.
    assert.ok(r.dischargedMwh <= r.chargedMwh * efficiency + energy * 0.02 + 1e-6);
  }
});

test("a bigger or more efficient battery is never worse for the evening peak", () => {
  const peak = (opts) => simulateDispatch({ net: duck(), ...opts }).after.eveningPeakMw;
  assert.ok(peak({ power: 200, energy: 800, efficiency: 0.85 }) <= peak({ power: 200, energy: 300, efficiency: 0.85 }) + 1);
  assert.ok(peak({ power: 300, energy: 600, efficiency: 0.85 }) <= peak({ power: 100, energy: 600, efficiency: 0.85 }) + 1);
  assert.ok(peak({ power: 200, energy: 600, efficiency: 0.95 }) <= peak({ power: 200, energy: 600, efficiency: 0.7 }) + 1);
});

// ---- curtailment ----------------------------------------------------------------------------------

test("a battery soaks up solar that would otherwise be curtailed", () => {
  const args = { net: duck(), efficiency: 0.9, floor: 400 };
  const without = simulateDispatch({ ...args, power: 0, energy: 0 });
  const withBattery = simulateDispatch({ ...args, power: 100, energy: 700 });
  assert.ok(without.curtailedAfterMwh > 400);
  assert.ok(withBattery.curtailedAfterMwh < without.curtailedAfterMwh * 0.05, `left ${withBattery.curtailedAfterMwh}`);
  assert.ok(min(withBattery.hours, "netAfter") >= 400 - 1e-6, "net load must never go below the floor");
  assert.ok(withBattery.chargedMwh > 400, "it must actually charge from the surplus");
});

test("a battery that is too small absorbs only part of the surplus", () => {
  const args = { net: duck(), efficiency: 1, floor: 400 };
  const small = simulateDispatch({ ...args, power: 40, energy: 100 });
  assert.ok(small.curtailedAfterMwh > 0 && small.curtailedAfterMwh < 500);
  close(small.curtailedBeforeMwh - small.curtailedAfterMwh, 100, 5); // it can store 100 MWh of the 500 MWh surplus
});

test("absorbing surplus takes priority over flattening", () => {
  // Plenty of flattening to do, but a tiny battery: it must spend its energy on the surplus, not the peak.
  const net = duck();
  const r = simulateDispatch({ net, power: 50, energy: 50, efficiency: 1, floor: 400 });
  assert.ok(r.curtailedBeforeMwh - r.curtailedAfterMwh >= 45, "should absorb ~50 MWh of surplus");
});

// ---- practicalities -------------------------------------------------------------------------------

test("it is fast enough to run on every slider movement", () => {
  const start = performance.now();
  for (let i = 0; i < 5; i++) simulateDispatch({ net: duck(), power: 600, energy: 2400, efficiency: 0.85, floor: 300 });
  const perRun = (performance.now() - start) / 5;
  assert.ok(perRun < 250, `${perRun.toFixed(0)} ms per run`);
});

test("results are deterministic", () => {
  const args = { net: duck(), power: 185, energy: 555, efficiency: 0.85, floor: 350 };
  assert.deepEqual(simulateDispatch(args), simulateDispatch(args));
});
