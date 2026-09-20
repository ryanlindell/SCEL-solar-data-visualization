// Battery dispatch for one 24-hour day. Pure functions - no page, no network - so they can be tested
// with `node --test` (see tests/js/dispatch.test.mjs).
//
// The question a battery answers: given the day's net load (what fossil plants must supply), when
// should it charge and discharge? Here the answer comes from a small optimizer:
//
//   * Fossil plants get more expensive per MWh the harder they are pushed, so the cost of an hour is
//     modeled as (net load)^2. Minimizing the total flattens the day: the battery charges when net
//     load is low and discharges when it is high.
//   * Fossil plants also cannot turn down below a minimum ("floor"). Solar that would push net load
//     below it has to be curtailed - unless the battery can soak it up. Curtailed energy is penalized
//     so heavily that absorbing it always comes first.
//   * The battery has a power limit (MW), an energy capacity (MWh) and a round-trip efficiency.
//
// The optimizer is dynamic programming over the battery's state of charge, on a grid of `steps` levels.
// The same day is repeated five times, starting with an EMPTY battery, and the middle day is returned.
// That day is a steady-state one: the battery is neither warming up from empty (the first days) nor
// running down for the end of the simulation (the last days). Starting empty matters: a battery that
// began with free energy would just spend it slowly, and the "typical day" would be a draining day.

export const HOURS_IN_DAY = 24;
const DAYS_SIMULATED = 5;
const DAYS_BEFORE_RESULT = 2; // days simulated before the one that is returned
const MIDDAY_HOURS = [9, 10, 11, 12, 13, 14, 15]; // where the duck's "belly" sits (matches process_hourly.py)
const TOL = 1e-9;
const THROUGHPUT_PENALTY = 0.01; // MW^2 per MW moved: only breaks ties, so the battery does not cycle for nothing

/** Headline numbers of a 24-hour net-load curve. Same definitions as net_load_stats in process_hourly.py. */
export function netLoadStats(net) {
  const argmin = (indexes) => indexes.reduce((a, b) => (net[b] < net[a] ? b : a));
  const argmax = (indexes) => indexes.reduce((a, b) => (net[b] > net[a] ? b : a));
  const range = (from, to) => Array.from({ length: Math.max(0, to - from) }, (_, i) => from + i);

  const lowHour = argmin(MIDDAY_HOURS);
  const peakHour = argmax(range(lowHour, net.length));
  const rampStart = range(lowHour, net.length - 3).reduce(
    (a, b) => (net[b + 3] - net[b] > net[a + 3] - net[a] ? b : a)
  );
  return {
    middayLowMw: net[lowHour],
    middayLowHour: lowHour,
    eveningPeakMw: net[peakHour],
    eveningPeakHour: peakHour,
    eveningClimbMw: net[peakHour] - net[lowHour],
    steepest3hRampMw: net[rampStart + 3] - net[rampStart],
    steepest3hRampStartHour: rampStart,
  };
}

/**
 * Schedule a battery over a typical day.
 *
 * @param {number[]} net       net load for each hour, in MW, before the battery (24 values)
 * @param {number} power       battery charge/discharge limit in MW
 * @param {number} energy      battery energy capacity in MWh
 * @param {number} efficiency  round-trip efficiency, 0-1 (split evenly between charging and discharging)
 * @param {number} floor       minimum fossil output in MW; net load cannot go below it (excess is curtailed)
 * @param {number} steps       resolution of the state-of-charge grid
 */
export function simulateDispatch({ net, power, energy, efficiency, floor = 0, steps = 200 }) {
  const n = net.length;
  const netBefore = net.map((x) => Math.max(x, floor));
  const curtailedBefore = net.map((x) => Math.max(0, floor - x));

  const hasBattery = power > 0 && energy > 0;
  const plan = hasBattery
    ? optimizeSchedule(net, power, energy, efficiency, floor, steps)
    : { socBefore: new Array(n).fill(0), socAfter: new Array(n).fill(0), socStart: 0 };

  const eta = Math.sqrt(efficiency);
  const hours = [];
  for (let h = 0; h < n; h++) {
    const stored = plan.socAfter[h] - plan.socBefore[h]; // MWh added to (+) or taken from (-) the battery
    const charge = stored > 0 ? stored / eta : 0; // MW drawn from the grid
    const discharge = stored < 0 ? -stored * eta : 0; // MW delivered to the grid
    const raw = net[h] + charge - discharge;
    const curtailed = Math.max(0, floor - raw);
    hours.push({
      hour: h,
      netBefore: netBefore[h],
      netAfter: raw + curtailed,
      charge,
      discharge,
      soc: plan.socAfter[h],
      curtailedBefore: curtailedBefore[h],
      curtailedAfter: curtailed,
    });
  }

  const sum = (key) => hours.reduce((total, r) => total + r[key], 0);
  return {
    hours,
    socStartMwh: plan.socStart,
    chargedMwh: sum("charge"),
    dischargedMwh: sum("discharge"),
    cyclesPerDay: hasBattery ? sum("discharge") / energy : 0,
    curtailedBeforeMwh: sum("curtailedBefore"),
    curtailedAfterMwh: sum("curtailedAfter"),
    before: netLoadStats(hours.map((r) => r.netBefore)),
    after: netLoadStats(hours.map((r) => r.netAfter)),
  };
}

/** Dynamic programming over state of charge. Returns the state of charge (MWh) before and after each hour. */
function optimizeSchedule(net, power, energy, efficiency, floor, steps) {
  const n = net.length;
  const total = n * DAYS_SIMULATED;
  const eta = Math.sqrt(efficiency);
  const dE = energy / steps; // MWh of stored energy per grid step
  const maxUp = Math.min(steps, Math.floor(((power + TOL) * eta) / dE)); // most steps it can charge in an hour
  const maxDown = Math.min(steps, Math.floor((power + TOL) / (eta * dE))); // most steps it can discharge in an hour
  const lambda = 4 * Math.max(floor, ...net) + 1; // one MW curtailed costs more than any flattening it could buy

  let value = new Float64Array(steps + 1).fill(Infinity);
  value[0] = 0; // the battery starts empty
  const choice = [];
  for (let t = 0; t < total; t++) {
    const x = net[t % n];
    const next = new Float64Array(steps + 1).fill(Infinity);
    const from = new Int16Array(steps + 1);
    for (let i = 0; i <= steps; i++) {
      const base = value[i];
      if (base === Infinity) continue; // not reachable
      for (let j = Math.max(0, i - maxDown); j <= Math.min(steps, i + maxUp); j++) {
        const charge = j > i ? ((j - i) * dE) / eta : 0;
        const discharge = j < i ? (i - j) * dE * eta : 0;
        const raw = x + charge - discharge;
        const curtailed = raw < floor ? floor - raw : 0;
        const after = raw + curtailed;
        const cost = base + after * after + lambda * curtailed + THROUGHPUT_PENALTY * (charge + discharge);
        if (cost < next[j]) {
          next[j] = cost;
          from[j] = i;
        }
      }
    }
    value = next;
    choice.push(from);
  }

  // Walk back from the cheapest final state to recover the whole path of states of charge.
  let best = 0;
  for (let j = 1; j <= steps; j++) if (value[j] < value[best]) best = j;
  const path = new Array(total + 1); // path[t] = level before hour t; path[t + 1] = level after it
  path[total] = best;
  for (let t = total - 1; t >= 0; t--) path[t] = choice[t][path[t + 1]];

  const middle = n * DAYS_BEFORE_RESULT;
  const socBefore = [];
  const socAfter = [];
  for (let h = 0; h < n; h++) {
    socBefore.push(path[middle + h] * dE);
    socAfter.push(path[middle + h + 1] * dE);
  }
  return { socBefore, socAfter, socStart: socBefore[0] };
}
