from __future__ import annotations

from fpl_forecast.constants import AVAILABILITY_NO_DATA, NEUTRAL_PPG_PRIOR
from fpl_forecast.scoring import (
    build_fixture_ticker,
    build_team_strength_lookup,
    compute_availability_prob,
    compute_form_component,
    fixture_impact,
    score_player,
    team_fixtures_for_gw,
)


def _team(id_, attack=1000, defence=1000):
    return {
        "id": id_,
        "name": f"Team{id_}",
        "short_name": f"T{id_}",
        "strength_attack_home": attack,
        "strength_attack_away": attack,
        "strength_defence_home": defence,
        "strength_defence_away": defence,
    }


def test_form_component_weights_recent_gameweeks_more():
    # Player who scored well recently and poorly a while ago...
    improving = [
        {"round": 1, "total_points": 1},
        {"round": 2, "total_points": 2},
        {"round": 3, "total_points": 10},
    ]
    # ...vs. the reverse trend, same total points.
    declining = [
        {"round": 1, "total_points": 10},
        {"round": 2, "total_points": 2},
        {"round": 3, "total_points": 1},
    ]
    assert compute_form_component(improving) > compute_form_component(declining)


def test_form_component_none_when_no_history():
    assert compute_form_component([]) is None


def test_availability_prefers_explicit_chance_of_playing():
    player = {"chance_of_playing_next_round": 50, "minutes": 900}
    assert compute_availability_prob(player, []) == 0.5


def test_availability_falls_back_to_recent_starts():
    player = {"chance_of_playing_next_round": None, "minutes": 900}
    history = [
        {"round": 1, "minutes": 90},
        {"round": 2, "minutes": 90},
        {"round": 3, "minutes": 0},
        {"round": 4, "minutes": 90},
    ]
    prob = compute_availability_prob(player, history)
    assert prob == 3 / 4


def test_availability_zero_minutes_no_history_is_low():
    player = {"chance_of_playing_next_round": None, "minutes": 0}
    assert compute_availability_prob(player, []) < 0.5


def test_availability_past_season_nailed_beats_fringe():
    # Gameweek-1-style scenario: no current-season history yet for either
    # player, so the only signal is how much they played last season.
    player = {"chance_of_playing_next_round": None, "minutes": 0}

    nailed_last_season = [{"season_name": "2025/26", "minutes": 3200}]  # ~35 games
    fringe_last_season = [{"season_name": "2025/26", "minutes": 180}]  # ~2 games

    nailed_prob = compute_availability_prob(player, [], nailed_last_season)
    fringe_prob = compute_availability_prob(player, [], fringe_last_season)

    assert nailed_prob > fringe_prob
    assert fringe_prob < 0.5


def test_availability_no_data_anywhere_is_lowest():
    # A brand-new signing with no current-season history and no past-season
    # record at all (e.g. just arrived from outside the league).
    player = {"chance_of_playing_next_round": None, "minutes": 0}
    assert compute_availability_prob(player, [], []) == AVAILABILITY_NO_DATA


def test_fixture_impact_home_advantage_for_strong_attack_vs_weak_defence():
    strength = build_team_strength_lookup([_team(1, attack=1300, defence=1000), _team(2, attack=1000, defence=800)])
    cs_prob, attack_mult, lambda_against = fixture_impact(1, 2, True, strength)
    assert 0.0 < cs_prob < 1.0
    assert attack_mult > 1.0  # team 1 attacks a weaker defence than average


def test_fixture_impact_handles_unpopulated_zero_strength_ratings():
    # Early in a new season (e.g. gameweek 1), the FPL API can report every
    # team's strength_attack_*/strength_defence_* as 0 before it has
    # computed real ratings. This must degrade to a neutral estimate
    # instead of raising ZeroDivisionError (regression test).
    strength = build_team_strength_lookup([_team(1, attack=0, defence=0), _team(2, attack=0, defence=0)])
    cs_prob, attack_mult, lambda_against = fixture_impact(1, 2, True, strength)
    assert 0.0 < cs_prob < 1.0
    assert attack_mult == 1.0
    assert lambda_against > 0.0


def test_team_fixtures_for_gw_handles_double_and_blank():
    fixtures = [
        {"team_h": 1, "team_a": 2, "team_h_difficulty": 3, "team_a_difficulty": 4},
        {"team_h": 3, "team_a": 1, "team_h_difficulty": 2, "team_a_difficulty": 5},
    ]
    team1_fixtures = team_fixtures_for_gw(1, fixtures)
    assert len(team1_fixtures) == 2  # double gameweek

    team9_fixtures = team_fixtures_for_gw(9, fixtures)
    assert team9_fixtures == []  # blank gameweek


def _make_scoring_player(minutes, points_per_game="6.0", **overrides):
    player = {
        "id": 1,
        "web_name": "AB",
        "first_name": "A",
        "second_name": "B",
        "team": 1,
        "element_type": 3,  # MID
        "now_cost": 50,
        "minutes": minutes,
        "goals_scored": 0,
        "assists": 0,
        "saves": 0,
        "expected_goals_per_90": "0",
        "expected_assists_per_90": "0",
        "points_per_game": points_per_game,
        "form": points_per_game,
        "chance_of_playing_next_round": None,
    }
    player.update(overrides)
    return player


def test_score_player_shrinks_small_sample_points_per_game():
    # Two players report the identical (high) points-per-game, but one has
    # a near-full season of minutes behind it and the other has a single
    # standout cameo. The low-minutes player's season/form numbers should
    # be pulled toward the neutral prior instead of trusted at face value --
    # this is the fix for a fringe player's hot cameo overrating them.
    strength = build_team_strength_lookup([_team(1), _team(2)])
    fixtures_for_team = [{"opponent_id": 2, "is_home": True, "difficulty": 3}]

    nailed_player = _make_scoring_player(minutes=1800)
    cameo_player = _make_scoring_player(minutes=90)

    nailed_score = score_player(nailed_player, strength, fixtures_for_team, [], None, [])
    cameo_score = score_player(cameo_player, strength, fixtures_for_team, [], None, [])

    assert nailed_score.data_confidence == 1.0
    assert cameo_score.data_confidence < 0.2
    assert nailed_score.season_component > cameo_score.season_component
    # The cameo player's shrunk season component should sit close to the
    # neutral prior rather than the raw (misleadingly high) 6.0 ppg.
    assert abs(cameo_score.season_component - NEUTRAL_PPG_PRIOR) < abs(cameo_score.season_component - 6.0)


def test_build_fixture_ticker_window_blanks_and_doubles():
    teams = [_team(1), _team(2), _team(3)]
    all_fixtures = [
        {"event": 1, "team_h": 1, "team_a": 2, "team_h_difficulty": 2, "team_a_difficulty": 3},
        {"event": 2, "team_h": 3, "team_a": 1, "team_h_difficulty": 4, "team_a_difficulty": 2},
        {"event": 2, "team_h": 1, "team_a": 2, "team_h_difficulty": 3, "team_a_difficulty": 3},  # DGW for team 1
        {"event": 6, "team_h": 2, "team_a": 3, "team_h_difficulty": 2, "team_a_difficulty": 2},  # outside window
    ]

    ticker = build_fixture_ticker(teams, all_fixtures, start_gw=1, num_gws=5)

    team1_events = [f["event"] for f in ticker[1]]
    assert team1_events == [1, 2, 2]  # single GW1 fixture + double GW2

    # Team 3 has a blank in GW1 (no fixture involving them that week).
    team3_gw1 = [f for f in ticker[3] if f["event"] == 1]
    assert team3_gw1 == []

    # The GW6 fixture is outside the 5-GW window starting at GW1.
    assert all(f["event"] <= 5 for f in ticker[2])
