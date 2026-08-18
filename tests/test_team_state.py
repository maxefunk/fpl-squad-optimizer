from __future__ import annotations

import pytest

from fpl_forecast.optimizer import optimize_squad
from fpl_forecast.team_state import (
    TeamState,
    current_squad_value,
    load_team,
    record_gameweek,
    save_team,
    team_state_from_squad_result,
)
from tests.conftest import make_player


def _sample_result():
    pool = [
        make_player(1, "GK", 1, 4.5, 5.0),
        make_player(2, "GK", 2, 4.0, 1.0),
        make_player(3, "DEF", 1, 4.0, 5.0),
        make_player(4, "DEF", 2, 4.0, 4.0),
        make_player(5, "DEF", 3, 4.0, 3.0),
        make_player(6, "DEF", 4, 4.0, 2.0),
        make_player(7, "DEF", 5, 4.0, 1.0),
        make_player(8, "MID", 3, 4.5, 5.0),
        make_player(9, "MID", 4, 4.5, 4.0),
        make_player(10, "MID", 5, 4.5, 3.0),
        make_player(11, "MID", 1, 4.5, 2.0),
        make_player(12, "MID", 2, 4.5, 1.0),
        make_player(13, "FWD", 3, 4.5, 5.0),
        make_player(14, "FWD", 4, 4.5, 4.0),
        make_player(15, "FWD", 5, 4.5, 3.0),
    ]
    return optimize_squad(pool, budget=100.0, max_per_club=3)


def test_team_state_from_squad_result_marks_starters_and_bench():
    result = _sample_result()
    state = team_state_from_squad_result(result, gameweek=1, free_transfers=1)

    assert len(state.squad) == 15
    starting_ids = {p.element_id for p in state.squad if p.is_starting}
    expected_starting_ids = {p.element_id for p in result.starting_xi}
    assert starting_ids == expected_starting_ids
    assert state.captain_id == result.captain.element_id
    assert state.vice_captain_id == result.vice_captain.element_id
    assert state.bank == pytest.approx(result.budget - result.total_cost, abs=1e-6)
    assert state.total_points == 0
    assert state.history == []


def test_save_and_load_round_trip(tmp_path):
    result = _sample_result()
    state = team_state_from_squad_result(result, gameweek=1, free_transfers=1)
    path = tmp_path / "my_team.json"

    save_team(path, state)
    loaded = load_team(path)

    assert loaded is not None
    assert loaded.squad_ids == state.squad_ids
    assert loaded.captain_id == state.captain_id
    assert loaded.bank == pytest.approx(state.bank)


def test_load_team_missing_file_returns_none(tmp_path):
    assert load_team(tmp_path / "does_not_exist.json") is None


def test_record_gameweek_counts_only_starting_xi():
    result = _sample_result()
    state = team_state_from_squad_result(result, gameweek=1, free_transfers=1)

    bench_id = next(p.element_id for p in state.squad if not p.is_starting)
    starting_ids = [p.element_id for p in state.squad if p.is_starting]

    actual_points = {eid: 5 for eid in starting_ids}
    actual_points[bench_id] = 999  # should NOT count -- bench player
    actual_minutes = {state.captain_id: 90}

    record_gameweek(state, gameweek=1, actual_points=actual_points, actual_minutes=actual_minutes)

    # 10 outfield starters + 1 GK = 11 starters, each worth 5, plus one more
    # 5 for the doubled captain = 11*5 + 5 = 60.
    assert state.total_points == 60
    assert state.history[0].points == 60
    assert state.history[0].captain_id == state.captain_id


def test_record_gameweek_falls_back_to_vice_captain_if_captain_blanks():
    result = _sample_result()
    state = team_state_from_squad_result(result, gameweek=1, free_transfers=1)
    starting_ids = [p.element_id for p in state.squad if p.is_starting]

    actual_points = {eid: 2 for eid in starting_ids}
    actual_minutes = {state.captain_id: 0, state.vice_captain_id: 90}  # captain didn't play

    record_gameweek(state, gameweek=1, actual_points=actual_points, actual_minutes=actual_minutes)

    assert state.history[0].captain_id == state.vice_captain_id
    # 11 starters * 2 + captain(vice)'s points doubled again = 22 + 2 = 24.
    assert state.history[0].points == 24


def test_record_gameweek_rejects_duplicate_gameweek():
    result = _sample_result()
    state = team_state_from_squad_result(result, gameweek=1, free_transfers=1)
    starting_ids = [p.element_id for p in state.squad if p.is_starting]
    actual_points = {eid: 1 for eid in starting_ids}
    actual_minutes = {state.captain_id: 90}

    record_gameweek(state, gameweek=1, actual_points=actual_points, actual_minutes=actual_minutes)

    with pytest.raises(ValueError):
        record_gameweek(state, gameweek=1, actual_points=actual_points, actual_minutes=actual_minutes)


def test_record_gameweek_accumulates_across_multiple_weeks():
    result = _sample_result()
    state = team_state_from_squad_result(result, gameweek=1, free_transfers=1)
    starting_ids = [p.element_id for p in state.squad if p.is_starting]
    actual_minutes = {state.captain_id: 90}

    record_gameweek(state, gameweek=1, actual_points={eid: 2 for eid in starting_ids}, actual_minutes=actual_minutes)
    first_total = state.total_points
    record_gameweek(state, gameweek=2, actual_points={eid: 3 for eid in starting_ids}, actual_minutes=actual_minutes)

    assert state.total_points > first_total
    assert len(state.history) == 2
    assert state.last_gameweek_recorded == 2


def test_current_squad_value_uses_live_prices_with_fallback():
    result = _sample_result()
    state = team_state_from_squad_result(result, gameweek=1, free_transfers=1)
    some_id = state.squad[0].element_id

    live_prices = {some_id: 999.0}  # only one player's price is "known"
    value = current_squad_value(state, live_prices)

    expected = 999.0 + sum(p.now_cost for p in state.squad if p.element_id != some_id)
    assert value == pytest.approx(expected)
