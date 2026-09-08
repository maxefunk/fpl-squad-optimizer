#!/usr/bin/env node
/**
 * Manual sanity-test harness for public/js/fpl-model.js and
 * public/js/fpl-optimizer.js -- the client-side port used by
 * public/build.html. Not part of the Python pytest suite (different
 * language, different toolchain); run via `npm run test:js`.
 *
 * Mirrors the same scenarios the Python test suite hand-verifies for the
 * equivalent logic in scoring.py/optimizer.py, so the JS port is checked
 * against the same standard of evidence, not just "looks right".
 */
import assert from "node:assert/strict";
import GLPK from "glpk.js/node";
import {
  buildTeamStrengthLookup,
  fixtureImpact,
  scorePlayer,
  confidenceFromMinutes,
} from "../public/js/fpl-model.js";
import { optimizeSquad, optimizeTransfers, InfeasibleError } from "../public/js/fpl-optimizer.js";

let passed = 0;
let failed = 0;

function test(name, fn) {
  return (async () => {
    try {
      await fn();
      passed++;
      console.log(`  ok  - ${name}`);
    } catch (e) {
      failed++;
      console.log(`FAIL  - ${name}`);
      console.log(`        ${e.message}`);
    }
  })();
}

function team(id, attack = 1000, defence = 1000, name = `Team${id}`) {
  return {
    id,
    name,
    short_name: `T${id}`,
    strength_attack_home: attack,
    strength_attack_away: attack,
    strength_defence_home: defence,
    strength_defence_away: defence,
  };
}

function player(overrides = {}) {
  return {
    id: 1,
    web_name: "Player",
    first_name: "First",
    second_name: "Last",
    team: 1,
    element_type: 3, // MID
    now_cost: 80,
    minutes: 900,
    goals_scored: 0,
    assists: 0,
    saves: 0,
    expected_goals_per_90: 0,
    expected_assists_per_90: 0,
    points_per_game: "5.0",
    form: "5.0",
    chance_of_playing_next_round: null,
    selected_by_percent: "50.0",
    ...overrides,
  };
}

async function main() {
  console.log("fpl-model.js / fpl-optimizer.js sanity tests\n");

  // -- fixture_impact / FDR blend --------------------------------------
  await test("FDR blend: easy fixture beats hard fixture with identical flat team strength", () => {
    const strength = buildTeamStrengthLookup([team(1, 1000, 1000), team(2, 1000, 1000)]);
    const easy = fixtureImpact(1, 2, true, strength, 1);
    const hard = fixtureImpact(1, 2, true, strength, 5);
    const neutral = fixtureImpact(1, 2, true, strength, 3);
    assert.ok(easy.cleanSheetProb > neutral.cleanSheetProb, "easy CS% should exceed neutral");
    assert.ok(hard.cleanSheetProb < neutral.cleanSheetProb, "hard CS% should be below neutral");
    assert.ok(easy.attackMultiplier > hard.attackMultiplier, "easy attack mult should exceed hard");
  });

  await test("fixture_impact degrades gracefully when team strength is all zero", () => {
    const strength = buildTeamStrengthLookup([team(1, 0, 0), team(2, 0, 0)]);
    const { cleanSheetProb, attackMultiplier } = fixtureImpact(1, 2, true, strength, 3);
    assert.ok(Number.isFinite(cleanSheetProb) && Number.isFinite(attackMultiplier), "must not be NaN/Infinity");
  });

  // -- confidence shrinkage ---------------------------------------------
  await test("confidenceFromMinutes ramps 0 -> 1 and caps at 1", () => {
    assert.equal(confidenceFromMinutes(0), 0);
    assert.equal(confidenceFromMinutes(450), 0.5);
    assert.equal(confidenceFromMinutes(900), 1);
    assert.equal(confidenceFromMinutes(5000), 1);
  });

  await test("scorePlayer shrinks season_component toward the neutral prior for a small sample", () => {
    const strength = buildTeamStrengthLookup([team(1), team(2)]);
    const fixturesForTeam = [{ opponentId: 2, isHome: true, difficulty: 3 }];
    const p = player({ minutes: 90, points_per_game: "10.0", form: "10.0" });
    const result = scorePlayer(p, strength, fixturesForTeam);
    // confidence = 90/900 = 0.1, season = 0.1*10 + 0.9*2.0 = 2.8
    assert.ok(Math.abs(result.season_component - 2.8) < 0.01, `expected ~2.8, got ${result.season_component}`);
  });

  await test("scorePlayer leaves form_component null with zero minutes (no fabricated form)", () => {
    const strength = buildTeamStrengthLookup([team(1), team(2)]);
    const fixturesForTeam = [{ opponentId: 2, isHome: true, difficulty: 3 }];
    const p = player({ minutes: 0, points_per_game: "0.0", form: "0.0" });
    const result = scorePlayer(p, strength, fixturesForTeam);
    assert.equal(result.form_component, null);
  });

  await test("scorePlayer returns a blank-gameweek zero score with no fixtures", () => {
    const strength = buildTeamStrengthLookup([team(1), team(2)]);
    const result = scorePlayer(player(), strength, []);
    assert.equal(result.xpts, 0.0);
    assert.equal(result.availability_prob, 0.0);
  });

  // -- availability / ownership cap --------------------------------------
  await test("ownership credibility cap pulls a low-owned player's availability down", () => {
    const strength = buildTeamStrengthLookup([team(1), team(2)]);
    const fixturesForTeam = [{ opponentId: 2, isHome: true, difficulty: 3 }];
    const nailed = player({ selected_by_percent: "40.0" });
    const capped = player({ selected_by_percent: "0.5" });
    const nailedResult = scorePlayer(nailed, strength, fixturesForTeam);
    const cappedResult = scorePlayer(capped, strength, fixturesForTeam);
    assert.ok(cappedResult.availability_prob < nailedResult.availability_prob);
  });

  // -- optimizer: ownership floor & force-include ------------------------
  const glpk = await GLPK();

  function makePool(n, overrides = (i) => ({})) {
    const positions = ["GK", "GK", ...Array(5).fill("DEF"), ...Array(5).fill("MID"), ...Array(3).fill("FWD")];
    const pool = [];
    let id = 1;
    for (let team_id = 1; team_id <= n; team_id++) {
      for (const pos of ["GK", "GK", "DEF", "DEF", "DEF", "MID", "MID", "MID", "FWD", "FWD"]) {
        pool.push({
          element_id: id,
          web_name: `P${id}`,
          position: pos,
          team_id,
          now_cost: 4.0 + (id % 5) * 0.5,
          xpts: 2.0 + (id % 7) * 0.5,
          selected_by_percent: 50.0,
          ...overrides(id),
        });
        id++;
      }
    }
    return pool;
  }

  await test("optimizeSquad excludes a player below the 10% ownership floor even with huge xpts", async () => {
    const pool = makePool(10);
    pool.push({
      element_id: 999, web_name: "Differential", position: "MID", team_id: 1,
      now_cost: 4.0, xpts: 20.0, selected_by_percent: 3.0,
    });
    const result = await optimizeSquad(glpk, pool, { budget: 100.0, maxPerClub: 3 });
    assert.ok(!result.squad.some((p) => p.web_name === "Differential"));
  });

  await test("optimizeSquad force-includes the single most-owned player", async () => {
    const pool = makePool(10);
    pool.push({
      element_id: 999, web_name: "CrowdFavorite", position: "FWD", team_id: 1,
      now_cost: 14.0, xpts: 0.5, selected_by_percent: 90.0,
    });
    const result = await optimizeSquad(glpk, pool, { budget: 100.0, maxPerClub: 3 });
    assert.ok(result.squad.some((p) => p.web_name === "CrowdFavorite"));
  });

  await test("optimizeSquad respects budget, composition, and per-club cap", async () => {
    const pool = makePool(10);
    const result = await optimizeSquad(glpk, pool, { budget: 100.0, maxPerClub: 3 });
    assert.equal(result.squad.length, 15);
    assert.equal(result.starting_xi.length, 11);
    assert.ok(result.total_cost <= 100.0 + 1e-6);
    const byPos = { GK: 0, DEF: 0, MID: 0, FWD: 0 };
    for (const p of result.squad) byPos[p.position]++;
    assert.deepEqual(byPos, { GK: 2, DEF: 5, MID: 5, FWD: 3 });
    const clubCounts = {};
    for (const p of result.squad) clubCounts[p.team_id] = (clubCounts[p.team_id] || 0) + 1;
    assert.ok(Object.values(clubCounts).every((c) => c <= 3));
  });

  await test("optimizeSquad raises InfeasibleError with an empty pool", async () => {
    await assert.rejects(() => optimizeSquad(glpk, [], {}), InfeasibleError);
  });

  // -- optimizer: captain-bonus tie-break (mirrors the Python regression test) --
  await test("captain-bonus term breaks a flat-xpts tie in favor of a standout scorer", async () => {
    const pool = [];
    let id = 1;
    const add = (position, teamId, cost, xpts, name) => {
      pool.push({ element_id: id++, web_name: name || `P${id}`, position, team_id: teamId, now_cost: cost, xpts, selected_by_percent: 50.0 });
    };
    add("GK", 1, 4.0, 3.0); add("GK", 2, 4.0, 1.0);
    for (let i = 0; i < 4; i++) add("DEF", 3 + i, 4.0, 3.0);
    add("DEF", 50, 5.0, 5.0, "DefGood"); add("DEF", 51, 3.0, 1.0, "DefCheap");
    for (let i = 0; i < 3; i++) add("MID", 8 + i, 4.0, 3.0);
    add("MID", 11, 4.0, 6.0, "AltCaptain");
    add("MID", 20, 5.0, 5.0, "MidGood"); add("MID", 21, 3.0, 1.0, "MidCheap");
    for (let i = 0; i < 2; i++) add("FWD", 13 + i, 4.0, 3.0);
    add("FWD", 30, 8.0, 7.0, "Standout"); add("FWD", 31, 4.0, 3.0, "FillerFWD");

    const result = await optimizeSquad(glpk, pool, { budget: 62.0, maxPerClub: 15, minOwnershipPercent: 0, forceIncludeMostOwned: false });
    assert.ok(result.squad.some((p) => p.web_name === "Standout"), "Standout should be picked");
    assert.equal(result.captain.web_name, "Standout");
    assert.ok(Math.abs(result.total_xi_xpts - 40.0) < 1e-6, `flat sum should be unchanged (tied), got ${result.total_xi_xpts}`);
  });

  // -- optimizer: transfers / hits --------------------------------------
  function pickValidSquad(pool, maxPerClub = 3) {
    // Greedily assemble a legal 2 GK / 5 DEF / 5 MID / 3 FWD current squad
    // that also respects the per-club cap, spreading picks across clubs --
    // a naive "first N of this position" slice can easily stack too many
    // players in one or two clubs, which itself would force transfers
    // regardless of value and isn't representative of a real owned squad.
    const need = { GK: 2, DEF: 5, MID: 5, FWD: 3 };
    const clubCounts = {};
    const picked = [];
    for (const pos of Object.keys(need)) {
      const candidates = pool.filter((p) => p.position === pos);
      let count = 0;
      for (const p of candidates) {
        if (count >= need[pos]) break;
        if ((clubCounts[p.team_id] || 0) >= maxPerClub) continue;
        picked.push(p);
        clubCounts[p.team_id] = (clubCounts[p.team_id] || 0) + 1;
        count++;
      }
    }
    return picked;
  }

  await test("optimizeTransfers suggests zero transfers and zero hits when nothing beats the current squad", async () => {
    const pool = makePool(10);
    const current = pickValidSquad(pool);
    assert.equal(current.length, 15, "test fixture sanity check");
    const currentIds = new Set(current.map((p) => p.element_id));
    // Make the current 15 clearly the best available (bump their xpts).
    for (const p of pool) if (currentIds.has(p.element_id)) p.xpts += 10;
    const budget = current.reduce((s, p) => s + p.now_cost, 0);
    const tr = await optimizeTransfers(glpk, pool, currentIds, 1, budget);
    assert.equal(tr.transfers_made, 0);
    assert.equal(tr.hits, 0);
  });

  await test("optimizeTransfers charges a hit for a transfer beyond free transfers", async () => {
    const pool = makePool(6); // fewer clubs so the pool stays small and quick to solve
    // Current squad: first 15 valid-composition players.
    const currentIds = new Set(pool.slice(0, 15).map((p) => p.element_id));
    const budget = pool.filter((p) => currentIds.has(p.element_id)).reduce((s, p) => s + p.now_cost, 0) + 5;
    // Make one non-owned player a huge upgrade over the weakest owned one, same position.
    const weakest = pool.find((p) => currentIds.has(p.element_id) && p.position === "MID");
    const upgrade = pool.find((p) => !currentIds.has(p.element_id) && p.position === "MID");
    upgrade.xpts = weakest.xpts + 20;
    upgrade.now_cost = weakest.now_cost;

    const tr = await optimizeTransfers(glpk, pool, currentIds, 0, budget); // 0 free transfers -> any move is a hit
    assert.ok(tr.transfers_made >= 1, "should suggest at least one transfer given a huge upgrade");
    assert.equal(tr.hits, tr.transfers_made);
    assert.ok(Math.abs(tr.hit_points - tr.hits * 4.0) < 1e-6);
  });

  console.log(`\n${passed} passed, ${failed} failed`);
  if (failed > 0) process.exit(1);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
