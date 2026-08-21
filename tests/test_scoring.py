from __future__ import annotations

import pytest

from fpl_forecast.constants import (
    AVAILABILITY_NO_DATA,
    AVAILABILITY_PAST_SEASON_FRINGE,
    AVAILABILITY_PAST_SEASON_NAILED,
    NEUTRAL_PPG_PRIOR,
)
from fpl_forecast.scoring import (
    build_fixture_ticker,
    build_team_strength_lookup,
    compute_availability_prob,
    compute_form_component,
    fixture_impact,
    fixture_run_multiplier,
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


def test_availability_combines_fitness_and_squad_role_multiplicatively():
    # 900 current-season minutes with no per-GW history given -> squad-role
    # factor falls to the season_minutes>=90 tier (0.75). A 50% fitness
    # doubt should discount that, not replace it outright.
    player = {"chance_of_playing_next_round": 50, "minutes": 900}
    assert compute_availability_prob(player, []) == pytest.approx(0.5 * 0.75)


def test_availability_fully_fit_backup_is_not_read_as_nailed():
    # The Jacquet case: FPL reports chance_of_playing_next_round=100 for
    # every fully-fit player, including fringe squad players who rarely
    # play. That fitness flag must not override a weak squad-role signal.
    player = {"chance_of_playing_next_round": 100, "minutes": 0}
    fringe_last_season = [{"season_name": "2025/26", "minutes": 300}]  # ~3.3 games

    prob = compute_availability_prob(player, [], fringe_last_season)

    assert prob < 0.5
    assert prob == AVAILABILITY_PAST_SEASON_FRINGE  # fitness factor of 1.0 doesn't change it


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


def test_availability_ownership_caps_stale_nailed_signal():
    # The backup-GK case: history_past says "nailed" (a full season's worth
    # of minutes, maybe covering for an injured #1 or at a different club),
    # but almost nobody owns them -- the crowd has already priced in that
    # they're not starting now, which our minutes heuristic alone missed.
    player = {"chance_of_playing_next_round": None, "minutes": 0, "selected_by_percent": "0.1"}
    nailed_last_season = [{"season_name": "2025/26", "minutes": 3200}]

    prob = compute_availability_prob(player, [], nailed_last_season)

    assert prob < 0.2


def test_availability_ownership_cap_does_not_affect_well_owned_players():
    player = {"chance_of_playing_next_round": None, "minutes": 0, "selected_by_percent": "15.0"}
    nailed_last_season = [{"season_name": "2025/26", "minutes": 3200}]

    prob = compute_availability_prob(player, [], nailed_last_season)

    assert prob == AVAILABILITY_PAST_SEASON_NAILED  # comfortably above the ownership threshold, uncapped


def test_availability_missing_ownership_data_does_not_apply_cap():
    # No selected_by_percent key at all -- absence of a signal must not be
    # treated the same as a known-near-zero signal.
    player = {"chance_of_playing_next_round": None, "minutes": 0}
    nailed_last_season = [{"season_name": "2025/26", "minutes": 3200}]

    prob = compute_availability_prob(player, [], nailed_last_season)

    assert prob == AVAILABILITY_PAST_SEASON_NAILED


def test_availability_ownership_cap_never_boosts_a_weak_signal():
    # High ownership on a player whose minutes signal is already weak must
    # not push availability up -- the cap only ever pulls down.
    player = {"chance_of_playing_next_round": None, "minutes": 0, "selected_by_percent": "50.0"}
    fringe_last_season = [{"season_name": "2025/26", "minutes": 300}]

    prob = compute_availability_prob(player, [], fringe_last_season)

    assert prob == AVAILABILITY_PAST_SEASON_FRINGE


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


def test_score_player_parses_selected_by_percent():
    strength = build_team_strength_lookup([_team(1), _team(2)])
    fixtures_for_team = [{"opponent_id": 2, "is_home": True, "difficulty": 3}]
    player = _make_scoring_player(minutes=1800, selected_by_percent="42.7")

    result = score_player(player, strength, fixtures_for_team, [], None, [])

    assert result.selected_by_percent == pytest.approx(42.7)


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
    # A real player accumulates minutes by having per-GW history rows; the
    # gate for "trust current-season data" is history, not the minutes
    # total alone (see score_player), so both need at least one row.
    nailed_history = [{"round": 1, "total_points": 6, "minutes": 90}]
    cameo_history = [{"round": 1, "total_points": 6, "minutes": 90}]

    nailed_score = score_player(nailed_player, strength, fixtures_for_team, nailed_history, None, [])
    cameo_score = score_player(cameo_player, strength, fixtures_for_team, cameo_history, None, [])

    assert nailed_score.data_confidence == 1.0
    assert cameo_score.data_confidence < 0.2
    assert nailed_score.season_component > cameo_score.season_component
    # The cameo player's shrunk season component should sit close to the
    # neutral prior rather than the raw (misleadingly high) 6.0 ppg.
    assert abs(cameo_score.season_component - NEUTRAL_PPG_PRIOR) < abs(cameo_score.season_component - 6.0)


def test_hot_cameo_does_not_outscore_established_player_on_attacking_threat():
    # The Carvalho case: a player with one big cameo (huge per-90 xG/xA rate
    # from a tiny sample) must not out-project a proven, consistent player
    # at a similar price -- otherwise the "hot cameo" ends up as captain.
    strength = build_team_strength_lookup([_team(1), _team(2)])
    fixtures_for_team = [{"opponent_id": 2, "is_home": True, "difficulty": 3}]

    hot_cameo = _make_scoring_player(
        minutes=90,
        points_per_game="2.0",
        form="2.0",
        expected_goals_per_90="1.2",  # huge rate, but from a single 90
        expected_assists_per_90="0.5",
    )
    established = _make_scoring_player(
        minutes=1800,
        points_per_game="5.5",
        form="5.5",
        expected_goals_per_90="0.35",  # solid, realistic sustained rate
        expected_assists_per_90="0.2",
    )

    cameo_history = [{"round": 1, "total_points": 15, "minutes": 90}]  # one big haul, no track record beyond it
    established_history = [{"round": 1, "total_points": 6, "minutes": 90}]

    cameo_score = score_player(hot_cameo, strength, fixtures_for_team, cameo_history, None, [])
    established_score = score_player(established, strength, fixtures_for_team, established_history, None, [])

    assert cameo_score.data_confidence < 0.2
    assert established_score.xpts > cameo_score.xpts


def test_attacking_threat_falls_back_to_last_season_when_no_current_minutes():
    # The Haaland case: at gameweek 1, current-season minutes/xG are 0 for
    # literally everyone, so a proven proper season's output (history_past)
    # should give a real, non-zero attacking-threat baseline rather than
    # everyone looking equally blank.
    strength = build_team_strength_lookup([_team(1), _team(2)])
    fixtures_for_team = [{"opponent_id": 2, "is_home": True, "difficulty": 3}]

    proven_last_season = [
        {"season_name": "2025/26", "minutes": 3000, "goals_scored": 30, "assists": 8, "saves": 0}
    ]
    unproven_no_history = []

    proven_score = score_player(
        _make_scoring_player(minutes=0, points_per_game="0.0", form="0.0"),
        strength,
        fixtures_for_team,
        [],
        None,
        proven_last_season,
    )
    unproven_score = score_player(
        _make_scoring_player(minutes=0, points_per_game="0.0", form="0.0"),
        strength,
        fixtures_for_team,
        [],
        None,
        unproven_no_history,
    )

    assert proven_score.model_component > unproven_score.model_component
    assert proven_score.xpts > unproven_score.xpts


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


def test_fixture_run_multiplier_rewards_easy_run_and_punishes_hard_run():
    easy_ticker = {1: [{"event": e, "difficulty": 1} for e in (2, 3, 4)]}
    hard_ticker = {1: [{"event": e, "difficulty": 5} for e in (2, 3, 4)]}

    easy = fixture_run_multiplier(1, gameweek=1, fixture_ticker=easy_ticker)
    hard = fixture_run_multiplier(1, gameweek=1, fixture_ticker=hard_ticker)

    assert easy > 1.0
    assert hard < 1.0


def test_fixture_run_multiplier_ignores_target_gw_and_excess_lookahead():
    # GW1 (the target) has an easy fixture but must not count; only events
    # after it (GW2 onward) should factor in.
    ticker = {1: [{"event": 1, "difficulty": 1}, {"event": 2, "difficulty": 5}, {"event": 3, "difficulty": 5}]}
    mult = fixture_run_multiplier(1, gameweek=1, fixture_ticker=ticker)
    assert mult < 1.0  # only the hard GW2/GW3 fixtures should count


def test_fixture_run_multiplier_neutral_without_ticker_data():
    assert fixture_run_multiplier(1, gameweek=1, fixture_ticker=None) == 1.0
    assert fixture_run_multiplier(1, gameweek=1, fixture_ticker={}) == 1.0
    assert fixture_run_multiplier(1, gameweek=1, fixture_ticker={1: []}) == 1.0


def test_score_player_applies_fixture_run_multiplier():
    strength = build_team_strength_lookup([_team(1), _team(2)])
    fixtures_for_team = [{"opponent_id": 2, "is_home": True, "difficulty": 3}]
    player = _make_scoring_player(minutes=1800)

    easy_run_ticker = {1: [{"event": e, "difficulty": 1} for e in (6, 7, 8)]}
    hard_run_ticker = {1: [{"event": e, "difficulty": 5} for e in (6, 7, 8)]}

    easy_score = score_player(
        player, strength, fixtures_for_team, [], None, [], gameweek=5, fixture_ticker=easy_run_ticker
    )
    hard_score = score_player(
        player, strength, fixtures_for_team, [], None, [], gameweek=5, fixture_ticker=hard_run_ticker
    )
    baseline_score = score_player(player, strength, fixtures_for_team, [], None, [])

    assert easy_score.model_component > baseline_score.model_component > hard_score.model_component


def test_stale_current_season_minutes_do_not_mask_history_past_fallback():
    # Reproduces a real gameweek-1 report bug: the FPL API can carry over
    # last season's aggregate `minutes`/`points_per_game` right up until the
    # new season's first gameweek is played, even though `history` (this
    # season's per-GW rows) correctly starts empty and `form` correctly
    # resets to "0.0". Gating on `minutes` (as an earlier version did) meant
    # a stale nonzero `minutes` blocked the history_past fallback entirely,
    # so the live `form` field of "0.0" got trusted at full confidence,
    # flattening form_component to exactly 0 for every proven player
    # regardless of who they were.
    strength = build_team_strength_lookup([_team(1), _team(2)])
    fixtures_for_team = [{"opponent_id": 2, "is_home": True, "difficulty": 3}]
    player = _make_scoring_player(
        minutes=3200,  # stale carryover from last season, NOT reset to 0
        points_per_game="6.8",  # also stale carryover
        form="0.0",  # correctly reset -- but must not be trusted at face value here
    )
    history_past = [{"season_name": "2025/26", "minutes": 3200, "goals_scored": 25, "assists": 8, "total_points": 260}]

    result = score_player(player, strength, fixtures_for_team, [], None, history_past)

    assert result.form_component > 1.0  # not flattened to 0
    # Both season and form come from the same history_past-derived figure
    # and the same confidence, so they must agree with each other.
    assert result.form_component == pytest.approx(result.season_component)


def test_fdr_blend_differentiates_flat_team_strength():
    # Reproduces the other real report bug: every clean-sheet % showed
    # exactly 26% regardless of opponent, because team strength ratings can
    # be flat/undifferentiated very early in a season even though FPL's own
    # FDR is already meaningful (e.g. a title contender at home to a newly
    # promoted side). Blending FDR in must make an easy and a hard fixture
    # produce different clean-sheet/attack numbers even with identical
    # underlying strength ratings.
    strength = build_team_strength_lookup([_team(1, attack=1000, defence=1000), _team(2, attack=1000, defence=1000)])

    easy_cs, easy_attack, _ = fixture_impact(1, 2, True, strength, difficulty=1)
    hard_cs, hard_attack, _ = fixture_impact(1, 2, True, strength, difficulty=5)
    neutral_cs, neutral_attack, _ = fixture_impact(1, 2, True, strength, difficulty=3)

    assert easy_cs > neutral_cs > hard_cs
    assert easy_attack > neutral_attack > hard_attack


def test_fdr_blend_is_a_no_op_without_a_difficulty_argument():
    strength = build_team_strength_lookup([_team(1), _team(2)])
    with_default = fixture_impact(1, 2, True, strength)
    explicit_none = fixture_impact(1, 2, True, strength, difficulty=None)
    assert with_default == explicit_none
