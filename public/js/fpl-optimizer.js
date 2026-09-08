/**
 * Client-side port of src/fpl_forecast/optimizer.py, using glpk.js (a
 * WebAssembly build of the real GLPK MILP solver) instead of PuLP/CBC --
 * see README "Browser tool" section for why (the pure-JS solver we
 * benchmarked first couldn't solve a real-scale FPL squad selection in
 * under two minutes; GLPK-via-WASM solves the same problem in ~100-200ms).
 *
 * Structurally the same MILP as the Python version: squad_i/xi_i/cap_i
 * binaries per player, the same captain-doubling objective term, the same
 * bench-quality secondary term, the same ownership floor and
 * force-include-most-owned constraints. Takes an already-initialized glpk
 * instance so this module works identically under Node (tests) and in the
 * browser (different init calls, same solve() call shape).
 */

import {
  POSITION_ORDER,
  SQUAD_COMPOSITION,
  XI_MIN,
  XI_MAX,
  XI_SIZE,
  DEFAULT_BUDGET,
  MAX_PER_CLUB,
  BENCH_QUALITY_WEIGHT,
  MIN_OWNERSHIP_PERCENT,
  FORCE_INCLUDE_MOST_OWNED_PLAYER,
  TRANSFER_HIT_COST,
} from "./fpl-model.js";

export class InfeasibleError extends Error {}

function buildBaseLP(players, { budget, maxPerClub, benchQualityWeight }) {
  // Accumulate objective coefficients per variable name rather than
  // pushing multiple {name, coef} entries for the same variable -- a
  // player's xi_i appears in both the primary xpts term and the
  // bench-quality term, and glpk.js keeps only the LAST entry for a
  // repeated name in a vars array rather than summing them, which
  // silently zeroed out the primary objective term (a real bug caught by
  // scripts/test-js-model.mjs, not a hypothetical one).
  const objCoefs = new Map();
  const addObj = (name, coef) => objCoefs.set(name, (objCoefs.get(name) || 0) + coef);

  const subjectTo = [];
  const binaries = [];

  const posSquadVars = { GK: [], DEF: [], MID: [], FWD: [] };
  const posXiVars = { GK: [], DEF: [], MID: [], FWD: [] };
  const clubSquadVars = {};
  const budgetVars = [];
  const squadSizeVars = [];
  const xiSizeVars = [];
  const captainVars = [];

  for (const p of players) {
    const squadVar = `squad_${p.element_id}`;
    const xiVar = `xi_${p.element_id}`;
    const capVar = `cap_${p.element_id}`;
    binaries.push(squadVar, xiVar, capVar);

    addObj(xiVar, p.xpts);
    addObj(capVar, p.xpts);
    if (p.position !== "GK" && benchQualityWeight) {
      addObj(squadVar, benchQualityWeight * p.xpts);
      addObj(xiVar, -benchQualityWeight * p.xpts);
    }

    posSquadVars[p.position].push({ name: squadVar, coef: 1 });
    posXiVars[p.position].push({ name: xiVar, coef: 1 });
    budgetVars.push({ name: squadVar, coef: p.now_cost });
    squadSizeVars.push({ name: squadVar, coef: 1 });
    xiSizeVars.push({ name: xiVar, coef: 1 });
    captainVars.push({ name: capVar, coef: 1 });
    (clubSquadVars[p.team_id] ||= []).push({ name: squadVar, coef: 1 });

    subjectTo.push({
      name: `link_${p.element_id}`,
      vars: [{ name: xiVar, coef: 1 }, { name: squadVar, coef: -1 }],
      bnds: { type: "UP", ub: 0, lb: 0 },
    });
    subjectTo.push({
      name: `caplink_${p.element_id}`,
      vars: [{ name: capVar, coef: 1 }, { name: xiVar, coef: -1 }],
      bnds: { type: "UP", ub: 0, lb: 0 },
    });
  }

  subjectTo.push({ name: "squad_size", vars: squadSizeVars, bnds: { type: "FX", ub: 15, lb: 15 } });
  subjectTo.push({ name: "xi_size", vars: xiSizeVars, bnds: { type: "FX", ub: XI_SIZE, lb: XI_SIZE } });
  subjectTo.push({ name: "budget", vars: budgetVars, bnds: { type: "UP", ub: budget, lb: 0 } });
  subjectTo.push({ name: "captain_count", vars: captainVars, bnds: { type: "FX", ub: 1, lb: 1 } });

  for (const pos of POSITION_ORDER) {
    subjectTo.push({
      name: `squad_${pos}`,
      vars: posSquadVars[pos],
      bnds: { type: "FX", ub: SQUAD_COMPOSITION[pos], lb: SQUAD_COMPOSITION[pos] },
    });
    subjectTo.push({
      name: `xi_${pos}`,
      vars: posXiVars[pos],
      bnds:
        XI_MIN[pos] === XI_MAX[pos]
          ? { type: "FX", ub: XI_MAX[pos], lb: XI_MIN[pos] }
          : { type: "DB", ub: XI_MAX[pos], lb: XI_MIN[pos] },
    });
  }
  for (const teamId of Object.keys(clubSquadVars)) {
    subjectTo.push({ name: `club_${teamId}`, vars: clubSquadVars[teamId], bnds: { type: "UP", ub: maxPerClub, lb: 0 } });
  }

  const objVars = Array.from(objCoefs, ([name, coef]) => ({ name, coef }));
  return { objVars, subjectTo, binaries };
}

function glpkBoundType(glpk, type) {
  return { UP: glpk.GLP_UP, LO: glpk.GLP_LO, FX: glpk.GLP_FX, DB: glpk.GLP_DB, FR: glpk.GLP_FR }[type];
}

async function solveLP(glpk, objVars, subjectTo, binaries) {
  const lp = {
    name: "fpl",
    objective: { direction: glpk.GLP_MAX, name: "obj", vars: objVars },
    subjectTo: subjectTo.map((row) => ({
      name: row.name,
      vars: row.vars,
      bnds: { type: glpkBoundType(glpk, row.bnds.type), ub: row.bnds.ub, lb: row.bnds.lb },
    })),
    binaries,
  };
  const solved = await glpk.solve(lp, { msglev: glpk.GLP_MSG_ERR });
  return solved.result;
}

function extractPicks(players, result) {
  const vars = result.vars;
  const squad = [];
  const startingXi = [];
  const captains = [];
  for (const p of players) {
    if (vars[`squad_${p.element_id}`] > 0.5) squad.push(p);
    if (vars[`xi_${p.element_id}`] > 0.5) startingXi.push(p);
    if (vars[`cap_${p.element_id}`] > 0.5) captains.push(p);
  }
  const squadIds = new Set(squad.map((p) => p.element_id));
  const bench = squad.filter((p) => !startingXi.some((s) => s.element_id === p.element_id));
  return { squad, startingXi, bench, squadIds };
}

function finalizeResult(squad, startingXi, bench, budget) {
  const startersSorted = [...startingXi].sort((a, b) => b.xpts - a.xpts);
  const captain = startersSorted[0];
  const viceCaptain = startersSorted[1];

  const defCount = startingXi.filter((p) => p.position === "DEF").length;
  const midCount = startingXi.filter((p) => p.position === "MID").length;
  const fwdCount = startingXi.filter((p) => p.position === "FWD").length;
  const formation = `${defCount}-${midCount}-${fwdCount}`;

  const benchGk = bench.filter((p) => p.position === "GK");
  const benchOutfield = bench.filter((p) => p.position !== "GK").sort((a, b) => b.xpts - a.xpts);
  const benchOrdered = [...benchOutfield, ...benchGk];

  const totalCost = squad.reduce((s, p) => s + p.now_cost, 0);
  const totalXiXpts = startingXi.reduce((s, p) => s + p.xpts, 0);

  return {
    squad,
    starting_xi: startersSorted,
    bench: benchOrdered,
    captain,
    vice_captain: viceCaptain,
    formation,
    total_cost: Math.round(totalCost * 10) / 10,
    total_xi_xpts: Math.round(totalXiXpts * 100) / 100,
    budget,
  };
}

/**
 * @param {object} glpk - an initialized glpk.js instance (await GLPK())
 * @param {Array} players - PlayerScore-shaped objects (scorePlayer() output)
 */
export async function optimizeSquad(glpk, players, options = {}) {
  const {
    budget = DEFAULT_BUDGET,
    maxPerClub = MAX_PER_CLUB,
    benchQualityWeight = BENCH_QUALITY_WEIGHT,
    minOwnershipPercent = MIN_OWNERSHIP_PERCENT,
    forceIncludeMostOwned = FORCE_INCLUDE_MOST_OWNED_PLAYER,
  } = options;

  if (!players || players.length === 0) throw new InfeasibleError("No players available to select from.");

  const pool = players.filter((p) => p.selected_by_percent >= minOwnershipPercent);
  if (pool.length === 0) {
    throw new InfeasibleError(`No players meet the ${minOwnershipPercent.toFixed(0)}% ownership floor.`);
  }

  const { objVars, subjectTo, binaries } = buildBaseLP(pool, { budget, maxPerClub, benchQualityWeight });

  if (forceIncludeMostOwned) {
    const mostOwned = pool.reduce((a, b) => (b.selected_by_percent > a.selected_by_percent ? b : a));
    subjectTo.push({
      name: `force_${mostOwned.element_id}`,
      vars: [{ name: `squad_${mostOwned.element_id}`, coef: 1 }],
      bnds: { type: "FX", ub: 1, lb: 1 },
    });
  }

  const result = await solveLP(glpk, objVars, subjectTo, binaries);
  if (result.status !== glpk.GLP_OPT) {
    throw new InfeasibleError(
      "Solver could not find a feasible squad. Try relaxing the budget or check that enough priced players are available."
    );
  }

  const { squad, startingXi, bench } = extractPicks(pool, result);
  return finalizeResult(squad, startingXi, bench, budget);
}

export async function optimizeTransfers(glpk, players, currentSquadIds, freeTransfers, budget, options = {}) {
  const {
    maxPerClub = MAX_PER_CLUB,
    hitCost = TRANSFER_HIT_COST,
    maxTransfers = null,
    benchQualityWeight = BENCH_QUALITY_WEIGHT,
    minOwnershipPercent = MIN_OWNERSHIP_PERCENT,
  } = options;

  if (!players || players.length === 0) throw new InfeasibleError("No players available to select from.");

  const pool = players.filter((p) => p.selected_by_percent >= minOwnershipPercent || currentSquadIds.has(p.element_id));
  if (pool.length === 0) {
    throw new InfeasibleError(`No players meet the ${minOwnershipPercent.toFixed(0)}% ownership floor.`);
  }

  const { objVars, subjectTo, binaries } = buildBaseLP(pool, { budget, maxPerClub, benchQualityWeight });

  // transfers_made = 15 - sum(squad_i for i in current_squad_ids); hits >= transfers_made - free_transfers, hits >= 0.
  // "hits" is left out of `binaries` deliberately: it's continuous, lower-bounded
  // at 0 by GLPK's default column bounds (matching PuLP's lowBound=0 in the
  // Python version), not a 0/1 decision variable.
  const hitsVar = "hits";

  const keptVars = pool
    .filter((p) => currentSquadIds.has(p.element_id))
    .map((p) => ({ name: `squad_${p.element_id}`, coef: 1 }));

  // hits - transfers_made_expr >= -free_transfers
  // transfers_made_expr = 15 - kept_expr  =>  hits + kept_expr >= 15 - free_transfers
  subjectTo.push({
    name: "hits_lower_bound",
    vars: [{ name: hitsVar, coef: 1 }, ...keptVars],
    bnds: { type: "LO", ub: 0, lb: 15 - freeTransfers },
  });

  if (maxTransfers !== null && maxTransfers !== undefined) {
    // transfers_made_expr <= maxTransfers  =>  -kept_expr <= maxTransfers - 15  =>  kept_expr >= 15 - maxTransfers
    subjectTo.push({
      name: "max_transfers",
      vars: keptVars,
      bnds: { type: "LO", ub: 0, lb: 15 - maxTransfers },
    });
  }

  objVars.push({ name: hitsVar, coef: -hitCost });

  const lp = {
    name: "fpl_transfers",
    objective: { direction: glpk.GLP_MAX, name: "obj", vars: objVars },
    subjectTo: subjectTo.map((row) => ({
      name: row.name,
      vars: row.vars,
      bnds: { type: glpkBoundType(glpk, row.bnds.type), ub: row.bnds.ub, lb: row.bnds.lb },
    })),
    binaries,
  };
  const solved = await glpk.solve(lp, { msglev: glpk.GLP_MSG_ERR });
  const result = solved.result;

  if (result.status !== glpk.GLP_OPT) {
    throw new InfeasibleError(
      "Solver could not find a feasible transfer set. Try relaxing max transfers, or check the current squad's value plus bank is enough to field a valid squad."
    );
  }

  const { squad, startingXi, bench, squadIds } = extractPicks(pool, result);

  let keptCount = 0;
  for (const id of currentSquadIds) if (squadIds.has(id)) keptCount++;
  const transfersMade = 15 - keptCount;
  const transfersOut = players.filter((p) => currentSquadIds.has(p.element_id) && !squadIds.has(p.element_id));
  const transfersIn = squad.filter((p) => !currentSquadIds.has(p.element_id));
  const hits = Math.max(0, transfersMade - freeTransfers);

  const finalized = finalizeResult(squad, startingXi, bench, budget);
  const bankRemaining = Math.round((budget - finalized.total_cost) * 10) / 10;

  return {
    result: finalized,
    transfers_out: transfersOut,
    transfers_in: transfersIn,
    transfers_made: transfersMade,
    free_transfers: freeTransfers,
    hits,
    hit_cost: hitCost,
    hit_points: hits * hitCost,
    bank_remaining: bankRemaining,
  };
}
