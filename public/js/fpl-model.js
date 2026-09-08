/**
 * Client-side port of the scoring model in src/fpl_forecast/scoring.py and
 * the constants in src/fpl_forecast/constants.py, for the self-service
 * browser tool (public/build.html). Pure functions, no DOM/fetch here --
 * testable directly under Node (see scripts/test-js-model.mjs).
 *
 * DELIBERATE SIMPLIFICATIONS vs. the full Python tool (see README "Browser
 * tool" section for the full list and rationale): this never fetches
 * element-summary (per-player gameweek history), only bootstrap-static and
 * fixtures, to avoid hundreds of relay round-trips from a visitor's
 * browser. Concretely:
 *   - "form" uses FPL's own precomputed `form` field directly, instead of
 *     recomputing a recency-weighted average from per-GW history.
 *   - There is no history_past fallback for gameweek 1 -- attacking threat
 *     and season/form numbers are only as good as the live bootstrap
 *     fields, which is fine from gameweek 2 onward but weak at gameweek 1
 *     (this tool's main use case -- checking transfers -- implies you
 *     already own a squad, i.e. gameweek 2+).
 *   - Set-piece taker bonus is omitted (needs free-text parsing of a
 *     separate endpoint).
 * Everything else (fixture-adjusted model component with FDR blend,
 * confidence shrinkage, availability, ownership floor/cap, fixture-run
 * lookahead) is a faithful, constants-for-constants port.
 */

export const POSITIONS = { 1: "GK", 2: "DEF", 3: "MID", 4: "FWD" };
export const POSITION_ORDER = ["GK", "DEF", "MID", "FWD"];
export const SQUAD_COMPOSITION = { GK: 2, DEF: 5, MID: 5, FWD: 3 };
export const XI_MIN = { GK: 1, DEF: 3, MID: 2, FWD: 1 };
export const XI_MAX = { GK: 1, DEF: 5, MID: 5, FWD: 3 };
export const XI_SIZE = 11;

export const DEFAULT_BUDGET = 100.0;
export const MAX_PER_CLUB = 3;
export const BENCH_QUALITY_WEIGHT = 0.05;

export const GOAL_POINTS = { GK: 6, DEF: 6, MID: 5, FWD: 4 };
export const ASSIST_POINTS = 3;
export const CLEAN_SHEET_POINTS = { GK: 4, DEF: 4, MID: 1, FWD: 0 };

export const LEAGUE_AVG_GOALS_PER_TEAM = 1.35;

export const WEIGHT_MODEL_COMPONENT = 0.45;
export const WEIGHT_FORM_COMPONENT = 0.35;
export const WEIGHT_SEASON_COMPONENT = 0.2;

export const MIN_SEASON_MINUTES_FOR_SIGNAL = 90;
export const MIN_MINUTES_FOR_FULL_CONFIDENCE = 900;
export const NEUTRAL_PPG_PRIOR = 2.0;

export const AVAILABILITY_NO_DATA = 0.15;

export const OWNERSHIP_CAP_THRESHOLD = 2.0;
export const OWNERSHIP_CAP_FLOOR = 0.15;

export const MIN_OWNERSHIP_PERCENT = 10.0;
export const FORCE_INCLUDE_MOST_OWNED_PLAYER = true;

export const FDR_BLEND_WEIGHT = 0.08;

export const FIXTURE_RUN_LOOKAHEAD_GWS = 3;
export const FIXTURE_RUN_WEIGHT = 0.03;
export const FIXTURE_RUN_MULTIPLIER_MIN = 0.85;
export const FIXTURE_RUN_MULTIPLIER_MAX = 1.15;

export const TRANSFER_HIT_COST = 4.0;
export const FREE_TRANSFER_CAP = 2;

function toFloat(value, fallback = 0.0) {
  if (value === null || value === undefined) return fallback;
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

function per90(total, minutes) {
  if (minutes <= 0) return 0.0;
  return (total / minutes) * 90.0;
}

function safeRatio(numerator, denominator, fallback = 1.0) {
  if (denominator <= 0) return fallback;
  return numerator / denominator;
}

function clamp(value, lo, hi) {
  return Math.max(lo, Math.min(hi, value));
}

// ---------------------------------------------------------------------
// Team strength / fixture impact
// ---------------------------------------------------------------------

export function buildTeamStrengthLookup(teams) {
  const lookup = {};
  let attackSum = 0, defenceSum = 0, n = 0;
  for (const t of teams) {
    attackSum += t.strength_attack_home + t.strength_attack_away;
    defenceSum += t.strength_defence_home + t.strength_defence_away;
    n += 2;
  }
  const avgAttack = attackSum / n;
  const avgDefence = defenceSum / n;

  for (const t of teams) {
    lookup[t.id] = {
      name: t.name,
      short_name: t.short_name,
      attack_home: t.strength_attack_home,
      attack_away: t.strength_attack_away,
      defence_home: t.strength_defence_home,
      defence_away: t.strength_defence_away,
    };
  }
  lookup._avg_attack = avgAttack;
  lookup._avg_defence = avgDefence;
  return lookup;
}

/** Returns { cleanSheetProb, attackMultiplier, lambdaAgainst } for one fixture. */
export function fixtureImpact(teamId, opponentId, isHome, strength, difficulty = null) {
  const avgAttack = strength._avg_attack;
  const avgDefence = strength._avg_defence;
  const own = strength[teamId];
  const opp = strength[opponentId];

  const ownAttack = isHome ? own.attack_home : own.attack_away;
  const ownDefence = isHome ? own.defence_home : own.defence_away;
  const oppAttack = isHome ? opp.attack_away : opp.attack_home;
  const oppDefence = isHome ? opp.defence_away : opp.defence_home;

  const lambdaAgainst =
    LEAGUE_AVG_GOALS_PER_TEAM * safeRatio(oppAttack, avgAttack) * safeRatio(avgDefence, ownDefence);
  const lambdaFor =
    LEAGUE_AVG_GOALS_PER_TEAM * safeRatio(ownAttack, avgAttack) * safeRatio(avgDefence, oppDefence);

  let cleanSheetProb = clamp(Math.exp(-lambdaAgainst), 0.02, 0.75);
  let attackMultiplier = clamp(lambdaFor / LEAGUE_AVG_GOALS_PER_TEAM, 0.4, 2.2);

  if (difficulty !== null && difficulty !== undefined) {
    const fdrFactor = 1.0 + (3.0 - difficulty) * FDR_BLEND_WEIGHT;
    cleanSheetProb = clamp(cleanSheetProb * fdrFactor, 0.02, 0.75);
    attackMultiplier = clamp(attackMultiplier * fdrFactor, 0.4, 2.2);
  }

  return { cleanSheetProb, attackMultiplier, lambdaAgainst };
}

export function teamFixturesForGw(teamId, fixtures) {
  const out = [];
  for (const f of fixtures) {
    if (f.team_h === teamId) {
      out.push({ opponentId: f.team_a, isHome: true, difficulty: f.team_h_difficulty });
    } else if (f.team_a === teamId) {
      out.push({ opponentId: f.team_h, isHome: false, difficulty: f.team_a_difficulty });
    }
  }
  return out;
}

/** For each team, its fixtures from startGw through startGw+numGws-1. */
export function buildFixtureTicker(teams, allFixtures, startGw, numGws = 5) {
  const ticker = {};
  for (const t of teams) ticker[t.id] = [];
  const endGw = startGw + numGws - 1;

  for (const f of allFixtures) {
    const event = f.event;
    if (event === null || event === undefined || event < startGw || event > endGw) continue;
    const h = f.team_h, a = f.team_a;
    if (h in ticker) ticker[h].push({ event, isHome: true, difficulty: f.team_h_difficulty });
    if (a in ticker) ticker[a].push({ event, isHome: false, difficulty: f.team_a_difficulty });
  }
  for (const teamId of Object.keys(ticker)) {
    ticker[teamId].sort((x, y) => x.event - y.event);
  }
  return ticker;
}

export function fixtureRunMultiplier(teamId, gameweek, fixtureTicker) {
  if (!fixtureTicker) return 1.0;
  const upcoming = (fixtureTicker[teamId] || [])
    .filter((f) => f.event > gameweek)
    .slice(0, FIXTURE_RUN_LOOKAHEAD_GWS);
  if (upcoming.length === 0) return 1.0;
  const avgFdr = upcoming.reduce((s, f) => s + f.difficulty, 0) / upcoming.length;
  const multiplier = 1.0 + (3.0 - avgFdr) * FIXTURE_RUN_WEIGHT;
  return clamp(multiplier, FIXTURE_RUN_MULTIPLIER_MIN, FIXTURE_RUN_MULTIPLIER_MAX);
}

// ---------------------------------------------------------------------
// Availability (simplified squad-role factor -- see module docstring)
// ---------------------------------------------------------------------

export function confidenceFromMinutes(minutes) {
  return clamp(minutes / MIN_MINUTES_FOR_FULL_CONFIDENCE, 0.0, 1.0);
}

function ownershipCredibilityCap(player) {
  const raw = player.selected_by_percent;
  if (raw === null || raw === undefined) return 1.0;
  const ownership = toFloat(raw);
  if (ownership >= OWNERSHIP_CAP_THRESHOLD) return 1.0;
  return OWNERSHIP_CAP_FLOOR + (1.0 - OWNERSHIP_CAP_FLOOR) * (ownership / OWNERSHIP_CAP_THRESHOLD);
}

function squadRoleFactor(player) {
  // Simplified: no per-GW history or history_past in the browser tool, so
  // this only ever takes the season-aggregate-minutes branch of the
  // Python version's _squad_role_factor (its fallback branch when neither
  // history nor history_past exists).
  const seasonMinutes = toFloat(player.minutes);
  if (seasonMinutes >= MIN_SEASON_MINUTES_FOR_SIGNAL) return 0.75;
  if (seasonMinutes > 0) return 0.4;
  return AVAILABILITY_NO_DATA;
}

export function computeAvailabilityProb(player) {
  const chanceNext = player.chance_of_playing_next_round;
  const fitnessFactor =
    chanceNext !== null && chanceNext !== undefined ? clamp(toFloat(chanceNext) / 100.0, 0.0, 1.0) : 1.0;

  let roleFactor = squadRoleFactor(player);
  roleFactor = Math.min(roleFactor, ownershipCredibilityCap(player));

  return clamp(fitnessFactor * roleFactor, 0.0, 1.0);
}

// ---------------------------------------------------------------------
// Main scoring entry point
// ---------------------------------------------------------------------

/**
 * @param {object} player - one bootstrap-static element
 * @param {object} teamStrength - from buildTeamStrengthLookup
 * @param {Array} fixturesForTeam - from teamFixturesForGw
 * @param {number|null} gameweek
 * @param {object|null} fixtureTicker - from buildFixtureTicker
 * @returns {object} PlayerScore-shaped result
 */
export function scorePlayer(player, teamStrength, fixturesForTeam, gameweek = null, fixtureTicker = null) {
  const position = POSITIONS[player.element_type];
  const teamId = player.team;
  const minutes = toFloat(player.minutes);
  const reasons = [];

  if (!fixturesForTeam || fixturesForTeam.length === 0) {
    return {
      element_id: player.id,
      web_name: player.web_name,
      full_name: `${player.first_name} ${player.second_name}`,
      team_id: teamId,
      team_name: teamStrength[teamId].name,
      team_short: teamStrength[teamId].short_name,
      position,
      now_cost: player.now_cost / 10.0,
      xpts: 0.0,
      availability_prob: 0.0,
      num_fixtures: 0,
      reasons: ["Blank gameweek: no fixture."],
      model_component: 0.0,
      form_component: null,
      season_component: 0.0,
      data_confidence: 0.0,
      fixture_desc: "",
      selected_by_percent: toFloat(player.selected_by_percent),
    };
  }

  // has_current_season_data approximated by minutes > 0 (see module
  // docstring -- weaker than the Python `history`-gated version at GW1).
  const hasCurrentSeasonData = minutes > 0;

  let xg90 = 0, xa90 = 0, saves90 = 0;
  let confidenceMinutes = 0;

  if (hasCurrentSeasonData) {
    xg90 = toFloat(player.expected_goals_per_90);
    xa90 = toFloat(player.expected_assists_per_90);
    saves90 = per90(toFloat(player.saves), minutes);
    if (xg90 === 0.0 && xa90 === 0.0 && minutes > 0) {
      xg90 = per90(toFloat(player.goals_scored), minutes);
      xa90 = per90(toFloat(player.assists), minutes);
    }
    confidenceMinutes = minutes;
  }

  let attackingThreatPer90 = xg90 * GOAL_POINTS[position] + xa90 * ASSIST_POINTS;

  const confidence = confidenceFromMinutes(confidenceMinutes);

  const seasonComponentRaw = toFloat(player.points_per_game);
  const seasonComponent = confidence * seasonComponentRaw + (1 - confidence) * NEUTRAL_PPG_PRIOR;

  // Form: FPL's own precomputed `form` field, only trusted when there's
  // current-season data at all (see module docstring for why this differs
  // from the Python per-GW recency curve).
  const formComponentRaw = hasCurrentSeasonData ? toFloat(player.form) : null;
  let formComponent = null;
  let effectiveFormWeight = 0.0;
  let effectiveSeasonWeight = WEIGHT_SEASON_COMPONENT + WEIGHT_FORM_COMPONENT;
  if (formComponentRaw !== null) {
    formComponent = confidence * formComponentRaw + (1 - confidence) * NEUTRAL_PPG_PRIOR;
    effectiveFormWeight = WEIGHT_FORM_COMPONENT;
    effectiveSeasonWeight = WEIGHT_SEASON_COMPONENT;
  }

  attackingThreatPer90 *= confidence;
  saves90 *= confidence;

  const availabilityProb = computeAvailabilityProb(player);

  let modelTotal = 0.0;
  const csProbs = [];
  const fixtureDescParts = [];
  for (const fx of fixturesForTeam) {
    const { cleanSheetProb, attackMultiplier, lambdaAgainst } = fixtureImpact(
      teamId, fx.opponentId, fx.isHome, teamStrength, fx.difficulty
    );
    const adjAttacking = attackingThreatPer90 * attackMultiplier;
    let model = 2.0; // appearance points, assuming a start
    if (position === "GK" || position === "DEF") {
      model += CLEAN_SHEET_POINTS[position] * cleanSheetProb;
      model -= lambdaAgainst / 2.0;
    }
    if (position === "GK") {
      model += saves90 / 3.0;
    }
    if (position === "MID" || position === "FWD") {
      model += CLEAN_SHEET_POINTS[position] * cleanSheetProb;
    }
    model += adjAttacking;
    modelTotal += model;
    csProbs.push(cleanSheetProb);

    const oppShort = teamStrength[fx.opponentId].short_name;
    const venue = fx.isHome ? "H" : "A";
    fixtureDescParts.push(`${oppShort} (${venue}, FDR ${fx.difficulty})`);
  }

  const cleanSheetProb = position !== "FWD" ? csProbs.reduce((s, v) => s + v, 0) / csProbs.length : null;
  const fixtureDescStr = fixtureDescParts.join(", ");
  reasons.push(`Fixture(s): ${fixtureDescStr}`);

  let runMult = 1.0;
  if (gameweek !== null && gameweek !== undefined) {
    runMult = fixtureRunMultiplier(teamId, gameweek, fixtureTicker);
    if (runMult !== 1.0) {
      modelTotal *= runMult;
      const direction = runMult > 1.0 ? "favorable" : "tough";
      const pct = (runMult - 1.0) * 100;
      reasons.push(`Fixture run after this GW is ${direction} (${pct >= 0 ? "+" : ""}${pct.toFixed(0)}% to model score)`);
    }
  }

  if (confidence < 0.5) {
    reasons.push(
      `Limited data: only ~${(confidenceMinutes / 90).toFixed(0)} matches worth of this season's minutes on record -- season/form/attacking numbers are shrunk toward a neutral baseline`
    );
  }

  const blended =
    WEIGHT_MODEL_COMPONENT * modelTotal +
    effectiveFormWeight * (formComponent !== null ? formComponent : 0.0) +
    effectiveSeasonWeight * seasonComponent;
  const xpts = blended * availabilityProb;

  const formDesc = formComponent !== null ? formComponent.toFixed(2) : "n/a";
  reasons.push(
    `model=${modelTotal.toFixed(2)} form=${formDesc} season_ppg=${seasonComponent.toFixed(2)} avail=${(availabilityProb * 100).toFixed(0)}%`
  );

  return {
    element_id: player.id,
    web_name: player.web_name,
    full_name: `${player.first_name} ${player.second_name}`,
    team_id: teamId,
    team_name: teamStrength[teamId].name,
    team_short: teamStrength[teamId].short_name,
    position,
    now_cost: player.now_cost / 10.0,
    xpts: Math.round(xpts * 1000) / 1000,
    availability_prob: Math.round(availabilityProb * 1000) / 1000,
    num_fixtures: fixturesForTeam.length,
    reasons,
    model_component: Math.round(modelTotal * 1000) / 1000,
    form_component: formComponent !== null ? Math.round(formComponent * 1000) / 1000 : null,
    season_component: Math.round(seasonComponent * 1000) / 1000,
    clean_sheet_prob: cleanSheetProb !== null ? Math.round(cleanSheetProb * 1000) / 1000 : null,
    data_confidence: Math.round(confidence * 1000) / 1000,
    fixture_desc: fixtureDescStr,
    selected_by_percent: toFloat(player.selected_by_percent),
  };
}

export function scoreAllPlayers(players, teams, fixtures, gameweek = null, fixtureTicker = null) {
  const teamStrength = buildTeamStrengthLookup(teams);
  const scores = [];
  for (const player of players) {
    const fixturesForTeam = teamFixturesForGw(player.team, fixtures);
    scores.push(scorePlayer(player, teamStrength, fixturesForTeam, gameweek, fixtureTicker));
  }
  return scores;
}
