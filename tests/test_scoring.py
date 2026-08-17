from __future__ import annotations

from fpl_forecast.scoring import (
    build_team_strength_lookup,
    compute_availability_prob,
    compute_form_component,
    fixture_impact,
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
