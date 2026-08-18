"""Expected-points (xPts) scoring model.

Approach (documented in detail in the README): a weighted composite of

  1. a fixture-adjusted statistical "model" component for the target
     gameweek, built from each player's season-long per-90 underlying
     output (expected goals/assists where available) and a simplified
     Poisson clean-sheet estimate derived from FPL's own team strength
     ratings;
  2. a recency-weighted "form" component from actual points in the last
     few gameweeks;
  3. a season-long points-per-game prior, to stabilise small samples.

The blend is then scaled by an availability probability (nailed-on-starter
/ rotation-risk estimate) and nudged by a small set-piece-duty bonus.

This is a transparent, tunable heuristic rather than a fitted statistical
model -- see the README "Scoring model" section for the rationale and
known limitations.
"""

from __future__ import annotations

import re

from fpl_forecast.constants import (
    ASSIST_POINTS,
    AVAILABILITY_NO_DATA,
    AVAILABILITY_PAST_SEASON_FRINGE,
    AVAILABILITY_PAST_SEASON_NAILED,
    AVAILABILITY_PAST_SEASON_ROTATION,
    CLEAN_SHEET_POINTS,
    FIXTURE_RUN_LOOKAHEAD_GWS,
    FIXTURE_RUN_MULTIPLIER_MAX,
    FIXTURE_RUN_MULTIPLIER_MIN,
    FIXTURE_RUN_WEIGHT,
    FORM_LOOKBACK_GWS,
    FORM_RECENCY_WEIGHTS,
    GOAL_POINTS,
    LEAGUE_AVG_GOALS_PER_TEAM,
    MIN_MINUTES_FOR_FULL_CONFIDENCE,
    MIN_SEASON_MINUTES_FOR_SIGNAL,
    NEUTRAL_PPG_PRIOR,
    PAST_SEASON_CONFIDENCE_DISCOUNT,
    PAST_SEASON_GAMES_FRINGE,
    PAST_SEASON_GAMES_NAILED,
    PAST_SEASON_GAMES_ROTATION,
    PENALTY_TAKER_BONUS,
    POSITIONS,
    SET_PIECE_TAKER_BONUS,
    START_MINUTES_THRESHOLD,
    WEIGHT_FORM_COMPONENT,
    WEIGHT_MODEL_COMPONENT,
    WEIGHT_SEASON_COMPONENT,
)
from fpl_forecast.models import PlayerScore


def _to_float(value, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _per90(total: float, minutes: float) -> float:
    if minutes <= 0:
        return 0.0
    return total / minutes * 90.0


def _safe_ratio(numerator: float, denominator: float, default: float = 1.0) -> float:
    """numerator / denominator, or `default` if denominator isn't usable.

    FPL's team strength ratings (strength_attack_*/strength_defence_*) can
    legitimately be 0 for every team early in a season, before the API has
    populated them -- most notably around gameweek 1. Treating a missing
    rating as "average" (ratio 1.0) degrades gracefully to a fixture-neutral
    estimate instead of crashing.
    """
    if denominator <= 0:
        return default
    return numerator / denominator


# ---------------------------------------------------------------------------
# Team strength / fixture impact
# ---------------------------------------------------------------------------


def build_team_strength_lookup(teams: list[dict]) -> dict[int, dict]:
    lookup = {}
    attack_vals, defence_vals = [], []
    for t in teams:
        attack_vals += [t["strength_attack_home"], t["strength_attack_away"]]
        defence_vals += [t["strength_defence_home"], t["strength_defence_away"]]
    avg_attack = sum(attack_vals) / len(attack_vals)
    avg_defence = sum(defence_vals) / len(defence_vals)

    for t in teams:
        lookup[t["id"]] = {
            "name": t["name"],
            "short_name": t["short_name"],
            "attack_home": t["strength_attack_home"],
            "attack_away": t["strength_attack_away"],
            "defence_home": t["strength_defence_home"],
            "defence_away": t["strength_defence_away"],
        }
    lookup["_avg_attack"] = avg_attack
    lookup["_avg_defence"] = avg_defence
    return lookup


def fixture_impact(team_id: int, opponent_id: int, is_home: bool, strength: dict) -> tuple[float, float]:
    """Return (clean_sheet_prob, attack_multiplier) for one fixture.

    Uses a simplified independent-Poisson model: each side's expected goals
    is the league-average scaled by (their attack strength / league-average
    attack) and by (opponent's defence strength inverted relative to
    league-average). Clean-sheet probability is P(0 goals conceded) under
    a Poisson(lambda_against) assumption.
    """
    avg_attack = strength["_avg_attack"]
    avg_defence = strength["_avg_defence"]
    own = strength[team_id]
    opp = strength[opponent_id]

    own_attack = own["attack_home"] if is_home else own["attack_away"]
    own_defence = own["defence_home"] if is_home else own["defence_away"]
    # Opponent is playing at the opposite venue.
    opp_attack = opp["attack_away"] if is_home else opp["attack_home"]
    opp_defence = opp["defence_away"] if is_home else opp["defence_home"]

    lambda_against = (
        LEAGUE_AVG_GOALS_PER_TEAM
        * _safe_ratio(opp_attack, avg_attack)
        * _safe_ratio(avg_defence, own_defence)
    )
    lambda_for = (
        LEAGUE_AVG_GOALS_PER_TEAM
        * _safe_ratio(own_attack, avg_attack)
        * _safe_ratio(avg_defence, opp_defence)
    )

    clean_sheet_prob = pow(2.71828182845904523536, -lambda_against)
    clean_sheet_prob = max(0.02, min(0.75, clean_sheet_prob))

    attack_multiplier = lambda_for / LEAGUE_AVG_GOALS_PER_TEAM
    attack_multiplier = max(0.4, min(2.2, attack_multiplier))

    return clean_sheet_prob, attack_multiplier, lambda_against


def team_fixtures_for_gw(team_id: int, fixtures: list[dict]) -> list[dict]:
    """All fixtures (0, 1, or 2 for a double gameweek) for a team in a GW."""
    out = []
    for f in fixtures:
        if f["team_h"] == team_id:
            out.append({"opponent_id": f["team_a"], "is_home": True, "difficulty": f["team_h_difficulty"]})
        elif f["team_a"] == team_id:
            out.append({"opponent_id": f["team_h"], "is_home": False, "difficulty": f["team_a_difficulty"]})
    return out


def fixture_run_multiplier(team_id: int, gameweek: int, fixture_ticker: dict[int, list[dict]] | None) -> float:
    """Nudge for the run of fixtures *after* the target gameweek.

    A player who looks great this week but has a brutal run right after is
    a worse pick than the single-GW xPts implies, since dropping them again
    soon costs a transfer or a hit. Returns 1.0 (no effect) when there's no
    ticker data or no fixtures in the lookahead window (nothing to judge).
    """
    if not fixture_ticker:
        return 1.0
    upcoming = [f for f in fixture_ticker.get(team_id, []) if f["event"] > gameweek]
    upcoming = upcoming[:FIXTURE_RUN_LOOKAHEAD_GWS]
    if not upcoming:
        return 1.0
    avg_fdr = sum(f["difficulty"] for f in upcoming) / len(upcoming)
    multiplier = 1.0 + (3.0 - avg_fdr) * FIXTURE_RUN_WEIGHT
    return max(FIXTURE_RUN_MULTIPLIER_MIN, min(FIXTURE_RUN_MULTIPLIER_MAX, multiplier))


# ---------------------------------------------------------------------------
# Form / availability
# ---------------------------------------------------------------------------


def compute_form_component(history: list[dict]) -> float | None:
    """Recency-weighted average points/GW over the last FORM_LOOKBACK_GWS."""
    if not history:
        return None
    recent = sorted(history, key=lambda h: h["round"], reverse=True)[:FORM_LOOKBACK_GWS]
    weights = FORM_RECENCY_WEIGHTS[: len(recent)]
    total_weight = sum(weights)
    if total_weight == 0:
        return None
    weighted = sum(w * h["total_points"] for w, h in zip(weights, recent))
    return weighted / total_weight


def _squad_role_factor(player: dict, history: list[dict], history_past: list[dict] | None) -> float:
    """How likely this player is to be in the matchday XI on current form,
    ignoring injury/fitness doubts entirely (see compute_availability_prob).

    Inferred from actual minutes: current-season per-GW history when it
    exists, falling back to the most recently completed season's minutes
    (history_past) when it doesn't -- most notably at gameweek 1, before
    any current-season data exists at all. This is what keeps a fringe
    player who happened to have one big cameo (inflating their
    points-per-game) from being read as a nailed-on starter.
    """
    if history:
        recent = sorted(history, key=lambda h: h["round"], reverse=True)[:FORM_LOOKBACK_GWS]
        starts = sum(1 for h in recent if h["minutes"] >= START_MINUTES_THRESHOLD)
        return max(0.05, min(1.0, starts / len(recent)))

    if history_past:
        last_season = history_past[-1]
        games_equivalent = _to_float(last_season.get("minutes")) / 90.0
        if games_equivalent >= PAST_SEASON_GAMES_NAILED:
            return AVAILABILITY_PAST_SEASON_NAILED
        if games_equivalent >= PAST_SEASON_GAMES_ROTATION:
            return AVAILABILITY_PAST_SEASON_ROTATION
        if games_equivalent >= PAST_SEASON_GAMES_FRINGE:
            return AVAILABILITY_PAST_SEASON_FRINGE
        return AVAILABILITY_NO_DATA

    season_minutes = _to_float(player.get("minutes"))
    if season_minutes >= MIN_SEASON_MINUTES_FOR_SIGNAL:
        return 0.75
    if season_minutes > 0:
        return 0.4
    return AVAILABILITY_NO_DATA  # no minutes on record anywhere: unproven


def compute_availability_prob(
    player: dict, history: list[dict], history_past: list[dict] | None = None
) -> float:
    """Estimate probability the player plays a meaningful role this GW.

    Combines two independent signals multiplicatively:

    - fitness factor: `chance_of_playing_next_round` when the API has set
      it. This only captures injury/fitness doubts -- FPL reports 100 here
      for every fully-fit player regardless of first-team status, so a
      fully-fit backup goalkeeper also reports 100. It must NOT be treated
      as confirmation that a player starts; it's a discount applied on top
      of the squad-role signal, not a replacement for it. Missing (None)
      is treated as "no doubt flagged" (1.0), not "no minutes signal".
    - squad-role factor: is this player actually in the matchday XI most
      weeks, from actual minutes played (see _squad_role_factor).

    Multiplying the two means a fringe player who happens to be fully fit
    still reads as unlikely to feature, and an injury-doubtful starter
    still reads as much more likely to play than a fit bench option.
    """
    chance_next = player.get("chance_of_playing_next_round")
    fitness_factor = max(0.0, min(1.0, _to_float(chance_next) / 100.0)) if chance_next is not None else 1.0

    squad_role_factor = _squad_role_factor(player, history, history_past)

    return max(0.0, min(1.0, fitness_factor * squad_role_factor))


# ---------------------------------------------------------------------------
# Set-piece duty parsing (best-effort text heuristic; see README limitations)
# ---------------------------------------------------------------------------

_SECTION_KEYWORDS = {
    "penalties": ["penal"],
    "set_piece": ["corner", "free kick", "free-kick", "freekick"],
}


def _extract_text_blobs(node) -> list[str]:
    """Recursively pull every string value out of an arbitrary JSON blob."""
    blobs = []
    if isinstance(node, str):
        blobs.append(node)
    elif isinstance(node, dict):
        for v in node.values():
            blobs.extend(_extract_text_blobs(v))
    elif isinstance(node, list):
        for v in node:
            blobs.extend(_extract_text_blobs(v))
    return blobs


def build_set_piece_lookup(set_piece_notes: dict, players_by_team: dict[int, list[dict]]) -> dict[int, dict]:
    """Best-effort map of element_id -> {"penalty_taker": bool, "set_piece_taker": bool}.

    The set-piece-notes endpoint returns free-text notes per team rather
    than a structured list of taker IDs, so this does a case-insensitive
    substring match of each player's surname against the section of text
    following each keyword. It is intentionally conservative (an
    unparseable format degrades to "no bonus" rather than raising).
    """
    lookup: dict[int, dict] = {}
    teams = set_piece_notes.get("teams", []) if isinstance(set_piece_notes, dict) else []

    for team_entry in teams:
        team_id = team_entry.get("id")
        if team_id is None or team_id not in players_by_team:
            continue
        blobs = _extract_text_blobs(team_entry)
        full_text = "\n".join(blobs).lower()
        if not full_text:
            continue

        # Split into rough sections by keyword occurrence so a player named
        # in the "Penalties" section isn't also credited for "Corners".
        sections: dict[str, str] = {"penalties": "", "set_piece": ""}
        lines = re.split(r"[\n\r]+", full_text)
        current = None
        for line in lines:
            lower = line.lower()
            matched = None
            for section, keywords in _SECTION_KEYWORDS.items():
                if any(kw in lower for kw in keywords):
                    matched = section
                    break
            if matched:
                current = matched
            if current:
                sections[current] += line + " "

        for player in players_by_team[team_id]:
            surname = (player.get("second_name") or "").strip().lower()
            web_name = (player.get("web_name") or "").strip().lower()
            names = {n for n in (surname, web_name) if n}
            if not names:
                continue
            is_pen = any(name in sections["penalties"] for name in names)
            is_sp = any(name in sections["set_piece"] for name in names)
            if is_pen or is_sp:
                lookup[player["id"]] = {"penalty_taker": is_pen, "set_piece_taker": is_sp}

    return lookup


# ---------------------------------------------------------------------------
# Main scoring entry point
# ---------------------------------------------------------------------------


def _confidence_from_minutes(minutes: float) -> float:
    """0-1: how much a season/no-history-form number should be trusted.

    Linear ramp from 0 minutes (no trust) to MIN_MINUTES_FOR_FULL_CONFIDENCE
    (full trust). Used to shrink small-sample points-per-game/form figures
    toward a neutral prior instead of taking them at face value.
    """
    return max(0.0, min(1.0, minutes / MIN_MINUTES_FOR_FULL_CONFIDENCE))


def score_player(
    player: dict,
    team_strength: dict,
    fixtures_for_team: list[dict],
    history: list[dict],
    set_piece_info: dict | None,
    history_past: list[dict] | None = None,
    gameweek: int | None = None,
    fixture_ticker: dict[int, list[dict]] | None = None,
) -> PlayerScore:
    position = POSITIONS[player["element_type"]]
    team_id = player["team"]
    minutes = _to_float(player.get("minutes"))

    reasons: list[str] = []

    if not fixtures_for_team:
        # Blank gameweek for this player's team.
        return PlayerScore(
            element_id=player["id"],
            web_name=player["web_name"],
            full_name=f"{player['first_name']} {player['second_name']}",
            team_id=team_id,
            team_name=team_strength[team_id]["name"],
            team_short=team_strength[team_id]["short_name"],
            position=position,
            now_cost=player["now_cost"] / 10.0,
            xpts=0.0,
            availability_prob=0.0,
            num_fixtures=0,
            reasons=["Blank gameweek: no fixture."],
            data_confidence=0.0,
            selected_by_percent=_to_float(player.get("selected_by_percent")),
        )

    # -- underlying per-90 attacking output (prefer expected stats) --------
    xg90 = _to_float(player.get("expected_goals_per_90"))
    xa90 = _to_float(player.get("expected_assists_per_90"))
    saves90 = _per90(_to_float(player.get("saves")), minutes)
    confidence_minutes = minutes
    used_past_season_rate = False
    past_season_ppg = None

    if xg90 == 0.0 and xa90 == 0.0 and minutes > 0:
        # Fall back to actual goals/assists if underlying xG data is absent.
        xg90 = _per90(_to_float(player.get("goals_scored")), minutes)
        xa90 = _per90(_to_float(player.get("assists")), minutes)

    if minutes == 0 and history_past:
        # No current-season minutes yet (typically gameweek 1): fall back to
        # last season's actual per-90 rate rather than assuming 0. Without
        # this, every player looks equally unproven at the start of a
        # season regardless of whether they're Haaland or a fringe squad
        # player -- last season's real output is a far better prior than
        # silence, discounted for being a season old (see
        # PAST_SEASON_CONFIDENCE_DISCOUNT).
        last_season = history_past[-1]
        last_season_minutes = _to_float(last_season.get("minutes"))
        if last_season_minutes > 0:
            xg90 = _per90(_to_float(last_season.get("goals_scored")), last_season_minutes)
            xa90 = _per90(_to_float(last_season.get("assists")), last_season_minutes)
            saves90 = _per90(_to_float(last_season.get("saves")), last_season_minutes)
            confidence_minutes = last_season_minutes
            used_past_season_rate = True
            games_equivalent = max(1.0, last_season_minutes / 90.0)
            past_season_ppg = _to_float(last_season.get("total_points")) / games_equivalent

    attacking_threat_per90 = xg90 * GOAL_POINTS[position] + xa90 * ASSIST_POINTS

    # -- confidence: how much to trust points_per_game/form/attacking-rate,
    # shrinking small samples toward a neutral prior instead of taking them
    # at face value (a single hot cameo shouldn't look like a full season) -
    confidence = _confidence_from_minutes(confidence_minutes)
    if used_past_season_rate:
        confidence *= PAST_SEASON_CONFIDENCE_DISCOUNT

    # When there's no current-season data, points_per_game/form must also
    # come from history_past rather than the (stale or reset) live bootstrap
    # fields -- otherwise `confidence` (derived from last season's minutes)
    # would be applied to a number from a different, inconsistent source.
    if used_past_season_rate:
        season_component_raw = past_season_ppg
        form_component_raw_default = past_season_ppg
    else:
        season_component_raw = _to_float(player.get("points_per_game"))
        form_component_raw_default = season_component_raw

    season_component = confidence * season_component_raw + (1 - confidence) * NEUTRAL_PPG_PRIOR

    form_component = compute_form_component(history)
    if form_component is None:
        # No per-GW history to compute recency-weighted form from -- the
        # API's own 'form' field is itself just as small-sample-prone here,
        # so it gets the same shrinkage treatment.
        if used_past_season_rate:
            form_component_raw = form_component_raw_default
        else:
            form_component_raw = _to_float(player.get("form"), default=form_component_raw_default)
        form_component = confidence * form_component_raw + (1 - confidence) * NEUTRAL_PPG_PRIOR

    # Attacking threat has no sensible "neutral" prior to shrink toward other
    # than 0 -- an unproven rate shouldn't be assumed to keep producing at
    # the same per-90 clip, so it's shrunk multiplicatively instead of blended.
    attacking_threat_per90 *= confidence
    saves90 *= confidence

    availability_prob = compute_availability_prob(player, history, history_past)

    # -- fixture-adjusted model component (assumes a full 90 mins) ---------
    model_total = 0.0
    cs_probs = []
    fixture_desc = []
    for fx in fixtures_for_team:
        cs_prob, attack_mult, lambda_against = fixture_impact(
            team_id, fx["opponent_id"], fx["is_home"], team_strength
        )
        adj_attacking = attacking_threat_per90 * attack_mult
        model = 2.0  # appearance points, assuming a start
        if position in ("GK", "DEF"):
            model += CLEAN_SHEET_POINTS[position] * cs_prob
            model -= lambda_against / 2.0  # conceded-goals deduction
        if position == "GK":
            model += saves90 / 3.0
        if position in ("MID", "FWD"):
            model += CLEAN_SHEET_POINTS[position] * cs_prob
        model += adj_attacking
        model_total += model
        cs_probs.append(cs_prob)

        opp_short = team_strength[fx["opponent_id"]]["short_name"]
        venue = "H" if fx["is_home"] else "A"
        fixture_desc.append(f"{opp_short} ({venue}, FDR {fx['difficulty']})")

    clean_sheet_prob = (sum(cs_probs) / len(cs_probs)) if position != "FWD" else None
    fixture_desc_str = ", ".join(fixture_desc)
    reasons.append(f"Fixture(s): {fixture_desc_str}")

    run_mult = 1.0
    if gameweek is not None:
        run_mult = fixture_run_multiplier(team_id, gameweek, fixture_ticker)
        if run_mult != 1.0:
            model_total *= run_mult
            direction = "favorable" if run_mult > 1.0 else "tough"
            reasons.append(
                f"Fixture run after this GW is {direction} "
                f"({(run_mult - 1.0) * 100:+.0f}% to model score)"
            )

    if confidence < 0.5:
        source = "last season's" if used_past_season_rate else "this season's"
        reasons.append(
            f"Limited data: only ~{confidence_minutes / 90:.0f} matches worth of {source} minutes on "
            "record -- season/form/attacking numbers are shrunk toward a neutral baseline"
        )

    # -- set-piece duty bonus ------------------------------------------------
    set_piece_bonus = 0.0
    if set_piece_info and position in ("MID", "FWD"):
        if set_piece_info.get("penalty_taker"):
            set_piece_bonus += PENALTY_TAKER_BONUS
            reasons.append("Primary penalty taker")
        if set_piece_info.get("set_piece_taker"):
            set_piece_bonus += SET_PIECE_TAKER_BONUS
            reasons.append("On corners/free-kicks")

    blended = (
        WEIGHT_MODEL_COMPONENT * model_total
        + WEIGHT_FORM_COMPONENT * form_component
        + WEIGHT_SEASON_COMPONENT * season_component
    )
    xpts = (blended + set_piece_bonus) * availability_prob

    reasons.append(
        f"model={model_total:.2f} form={form_component:.2f} "
        f"season_ppg={season_component:.2f} avail={availability_prob:.0%}"
    )

    return PlayerScore(
        element_id=player["id"],
        web_name=player["web_name"],
        full_name=f"{player['first_name']} {player['second_name']}",
        team_id=team_id,
        team_name=team_strength[team_id]["name"],
        team_short=team_strength[team_id]["short_name"],
        position=position,
        now_cost=player["now_cost"] / 10.0,
        xpts=round(xpts, 3),
        availability_prob=round(availability_prob, 3),
        num_fixtures=len(fixtures_for_team),
        reasons=reasons,
        model_component=round(model_total, 3),
        form_component=round(form_component, 3),
        season_component=round(season_component, 3),
        clean_sheet_prob=round(clean_sheet_prob, 3) if clean_sheet_prob is not None else None,
        data_confidence=round(confidence, 3),
        fixture_desc=fixture_desc_str,
        selected_by_percent=_to_float(player.get("selected_by_percent")),
    )


def score_all_players(
    players: list[dict],
    teams: list[dict],
    fixtures: list[dict],
    element_summaries: dict[int, dict],
    set_piece_notes: dict | None = None,
    gameweek: int | None = None,
    fixture_ticker: dict[int, list[dict]] | None = None,
) -> list[PlayerScore]:
    team_strength = build_team_strength_lookup(teams)

    players_by_team: dict[int, list[dict]] = {}
    for p in players:
        players_by_team.setdefault(p["team"], []).append(p)

    set_piece_lookup = {}
    if set_piece_notes:
        try:
            set_piece_lookup = build_set_piece_lookup(set_piece_notes, players_by_team)
        except Exception:
            set_piece_lookup = {}

    scores = []
    for player in players:
        team_id = player["team"]
        fixtures_for_team = team_fixtures_for_gw(team_id, fixtures)
        summary = element_summaries.get(player["id"], {})
        history = summary.get("history", [])
        history_past = summary.get("history_past", [])
        set_piece_info = set_piece_lookup.get(player["id"])
        scores.append(
            score_player(
                player,
                team_strength,
                fixtures_for_team,
                history,
                set_piece_info,
                history_past,
                gameweek=gameweek,
                fixture_ticker=fixture_ticker,
            )
        )

    return scores


# ---------------------------------------------------------------------------
# Fixture ticker (upcoming-fixtures view for the HTML report)
# ---------------------------------------------------------------------------


def build_fixture_ticker(
    teams: list[dict], all_fixtures: list[dict], start_gw: int, num_gws: int = 5
) -> dict[int, list[dict]]:
    """For each team, its fixtures from start_gw through start_gw+num_gws-1.

    Handles blank gameweeks (a team may have 0 entries for some GW) and
    double gameweeks (it may have 2) naturally, since it's just a filter +
    group-by over the full fixture list rather than one fixture per GW.
    """
    team_short = {t["id"]: t["short_name"] for t in teams}
    ticker: dict[int, list[dict]] = {t["id"]: [] for t in teams}
    end_gw = start_gw + num_gws - 1

    for f in all_fixtures:
        event = f.get("event")
        if event is None or event < start_gw or event > end_gw:
            continue
        h, a = f["team_h"], f["team_a"]
        if h in ticker:
            ticker[h].append(
                {
                    "event": event,
                    "opponent": team_short.get(a, "?"),
                    "is_home": True,
                    "difficulty": f["team_h_difficulty"],
                }
            )
        if a in ticker:
            ticker[a].append(
                {
                    "event": event,
                    "opponent": team_short.get(h, "?"),
                    "is_home": False,
                    "difficulty": f["team_a_difficulty"],
                }
            )

    for team_id in ticker:
        ticker[team_id].sort(key=lambda x: x["event"])

    return ticker
