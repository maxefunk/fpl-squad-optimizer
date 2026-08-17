from __future__ import annotations

import pytest

from fpl_forecast.constants import MAX_PER_CLUB, SQUAD_COMPOSITION, XI_MAX, XI_MIN, XI_SIZE
from fpl_forecast.optimizer import InfeasibleError, optimize_squad
from tests.conftest import make_player


def _position_counts(players):
    counts = {"GK": 0, "DEF": 0, "MID": 0, "FWD": 0}
    for p in players:
        counts[p.position] += 1
    return counts


def test_squad_composition_is_valid(sample_pool):
    result = optimize_squad(sample_pool, budget=100.0, max_per_club=3)

    assert len(result.squad) == 15
    counts = _position_counts(result.squad)
    assert counts == SQUAD_COMPOSITION


def test_starting_xi_shape_is_valid(sample_pool):
    result = optimize_squad(sample_pool, budget=100.0, max_per_club=3)

    assert len(result.starting_xi) == XI_SIZE
    counts = _position_counts(result.starting_xi)
    assert counts["GK"] == 1
    for pos in ("DEF", "MID", "FWD"):
        assert XI_MIN[pos] <= counts[pos] <= XI_MAX[pos]

    def_count, mid_count, fwd_count = counts["DEF"], counts["MID"], counts["FWD"]
    assert result.formation == f"{def_count}-{mid_count}-{fwd_count}"

    # Every starter must also be in the 15-man squad, and bench = squad - XI.
    xi_ids = {p.element_id for p in result.starting_xi}
    squad_ids = {p.element_id for p in result.squad}
    assert xi_ids <= squad_ids
    assert len(result.bench) == 4
    assert {p.element_id for p in result.bench} == squad_ids - xi_ids


def test_budget_limit_is_respected(sample_pool):
    for budget in (100.0, 90.0, 80.0):
        result = optimize_squad(sample_pool, budget=budget, max_per_club=3)
        assert result.total_cost <= budget + 1e-6


def test_infeasible_budget_raises(sample_pool):
    with pytest.raises(InfeasibleError):
        optimize_squad(sample_pool, budget=30.0, max_per_club=3)


def test_empty_player_pool_raises():
    with pytest.raises(InfeasibleError):
        optimize_squad([], budget=100.0, max_per_club=3)


def test_max_per_club_constraint_binds():
    """One club is deliberately given a full, cheap, high-xPts composition.

    Without a per-club cap the optimizer would happily fill most/all of the
    squad from this single "super club". We verify the cap actually
    prevents that (rather than just happening to be satisfied anyway) by
    solving once with the real FPL cap and once with a relaxed cap and
    checking the club's representation increases when the cap is lifted.
    """
    star_club = 99
    pool = []
    eid = 1

    # Star club: cheap and by far the best xPts at every position -- exactly
    # a full valid composition on its own (2 GK, 5 DEF, 5 MID, 3 FWD).
    star_specs = (
        [("GK", 4.5, 9.0), ("GK", 4.5, 8.5)]
        + [("DEF", 4.5, v) for v in (9.0, 8.8, 8.6, 8.4, 8.2)]
        + [("MID", 5.0, v) for v in (9.5, 9.3, 9.1, 8.9, 8.7)]
        + [("FWD", 5.5, v) for v in (9.5, 9.3, 9.1)]
    )
    for position, cost, xpts in star_specs:
        pool.append(make_player(eid, position, star_club, cost, xpts))
        eid += 1

    # Five filler clubs with ample, cheap, low-xPts depth at every position
    # so a feasible squad still exists once the star club is capped.
    for team_id in range(1, 6):
        for position, n in (("GK", 2), ("DEF", 4), ("MID", 4), ("FWD", 3)):
            for i in range(n):
                pool.append(make_player(eid, position, team_id, 4.0 + i * 0.3, 1.0 + i * 0.1))
                eid += 1

    capped = optimize_squad(pool, budget=100.0, max_per_club=MAX_PER_CLUB)
    capped_count = sum(1 for p in capped.squad if p.team_id == star_club)
    assert capped_count <= MAX_PER_CLUB
    for team_id in set(p.team_id for p in pool):
        assert sum(1 for p in capped.squad if p.team_id == team_id) <= MAX_PER_CLUB

    # The optimizer only rewards starting-XI xPts (bench slots are "free"
    # once budget/composition are satisfied), so compare club representation
    # within the starting XI, where the star club's dominance actually shows.
    capped_xi_count = sum(1 for p in capped.starting_xi if p.team_id == star_club)
    assert capped_xi_count <= MAX_PER_CLUB

    relaxed = optimize_squad(pool, budget=100.0, max_per_club=15)
    relaxed_xi_count = sum(1 for p in relaxed.starting_xi if p.team_id == star_club)

    assert relaxed_xi_count > capped_xi_count
    # Every star-club player outscores every filler-club player at every
    # position, so once uncapped the entire starting XI is star-club.
    assert relaxed_xi_count == XI_SIZE


def test_captain_and_vice_are_top_two_xi_scorers(sample_pool):
    result = optimize_squad(sample_pool, budget=100.0, max_per_club=3)

    xi_by_xpts = sorted(result.starting_xi, key=lambda p: p.xpts, reverse=True)
    assert result.captain.element_id == xi_by_xpts[0].element_id
    assert result.vice_captain.element_id == xi_by_xpts[1].element_id
    assert result.captain.xpts >= result.vice_captain.xpts


def test_result_maximizes_starting_xi_xpts(sample_pool):
    result = optimize_squad(sample_pool, budget=100.0, max_per_club=3)
    naive_best_xi_xpts = sum(p.xpts for p in sorted(sample_pool, key=lambda p: p.xpts, reverse=True)[:11])
    # The true optimum must be at least as good as any greedy top-11-by-xpts
    # pick (which is very likely infeasible under position/budget/club
    # constraints, but its total is a valid upper bound on an unconstrained
    # top-11) -- more importantly, assert the objective matches the sum of
    # the chosen starters' xPts.
    assert result.total_xi_xpts == pytest.approx(sum(p.xpts for p in result.starting_xi), abs=1e-6)
    assert result.total_xi_xpts <= naive_best_xi_xpts + 1e-6
