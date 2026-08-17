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
    CLEAN_SHEET_POINTS,
    FORM_LOOKBACK_GWS,
    FORM_RECENCY_WEIGHTS,
    GOAL_POINTS,
    LEAGUE_AVG_GOALS_PER_TEAM,
    MIN_SEASON_MINUTES_FOR_SIGNAL,
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

    lambda_against = LEAGUE_AVG_GOALS_PER_TEAM * (opp_attack / avg_attack) * (avg_defence / own_defence)
    lambda_for = LEAGUE_AVG_GOALS_PER_TEAM * (own_attack / avg_attack) * (avg_defence / opp_defence)

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


def compute_availability_prob(player: dict, history: list[dict]) -> float:
    """Estimate probability the player plays a meaningful role this GW."""
    chance_next = player.get("chance_of_playing_next_round")
    if chance_next is not None:
        return max(0.0, min(1.0, _to_float(chance_next) / 100.0))

    if history:
        recent = sorted(history, key=lambda h: h["round"], reverse=True)[:FORM_LOOKBACK_GWS]
        starts = sum(1 for h in recent if h["minutes"] >= START_MINUTES_THRESHOLD)
        return max(0.05, min(1.0, starts / len(recent)))

    season_minutes = _to_float(player.get("minutes"))
    if season_minutes >= MIN_SEASON_MINUTES_FOR_SIGNAL:
        return 0.75
    if season_minutes > 0:
        return 0.4
    return 0.15  # no minutes on record: unproven, treat as unlikely nailed-on


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


def score_player(
    player: dict,
    team_strength: dict,
    fixtures_for_team: list[dict],
    history: list[dict],
    set_piece_info: dict | None,
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
        )

    # -- underlying per-90 attacking output (prefer expected stats) --------
    xg90 = _to_float(player.get("expected_goals_per_90"))
    xa90 = _to_float(player.get("expected_assists_per_90"))
    if xg90 == 0.0 and xa90 == 0.0 and minutes > 0:
        # Fall back to actual goals/assists if underlying xG data is absent.
        xg90 = _per90(_to_float(player.get("goals_scored")), minutes)
        xa90 = _per90(_to_float(player.get("assists")), minutes)

    saves90 = _per90(_to_float(player.get("saves")), minutes)

    attacking_threat_per90 = xg90 * GOAL_POINTS[position] + xa90 * ASSIST_POINTS

    # -- season & form components -------------------------------------------
    season_component = _to_float(player.get("points_per_game"))
    form_component = compute_form_component(history)
    if form_component is None:
        form_component = _to_float(player.get("form"), default=season_component)

    availability_prob = compute_availability_prob(player, history)

    # -- fixture-adjusted model component (assumes a full 90 mins) ---------
    model_total = 0.0
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

        opp_short = team_strength[fx["opponent_id"]]["short_name"]
        venue = "H" if fx["is_home"] else "A"
        fixture_desc.append(f"{opp_short} ({venue}, FDR {fx['difficulty']})")

    reasons.append(f"Fixture(s): {', '.join(fixture_desc)}")

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
    )


def score_all_players(
    players: list[dict],
    teams: list[dict],
    fixtures: list[dict],
    element_summaries: dict[int, dict],
    set_piece_notes: dict | None = None,
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
        history = element_summaries.get(player["id"], {}).get("history", [])
        set_piece_info = set_piece_lookup.get(player["id"])
        scores.append(score_player(player, team_strength, fixtures_for_team, history, set_piece_info))

    return scores
