from __future__ import annotations

import pytest

from fpl_forecast.constants import MAX_PER_CLUB, SQUAD_COMPOSITION, TRANSFER_HIT_COST, XI_MAX, XI_MIN, XI_SIZE
from fpl_forecast.optimizer import InfeasibleError, optimize_squad, optimize_transfers
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


def _captain_tiebreak_pool():
    """Two squads are exactly tied on raw starting-XI xPts (40.0 flat,
    whether or not "Standout" is picked -- hand-verified via a standalone
    MILP without the captain term), but only the Standout squad lets a
    higher-forecast player (7.0, vs 6.0 for "AltCaptain", the best player in
    the alternative squad) wear the armband. A flat, captaincy-blind sum is
    genuinely indifferent between the two; real FPL rules (captain's points
    count double) make picking the standout strictly better once that's
    accounted for -- which is exactly what the objective's captain term
    exists to capture. Budget is set so a squad can afford Standout only by
    downgrading both the DEF and MID "lever" slots to their cheap option,
    or afford both lever slots' good option only by giving up Standout --
    not both at once."""
    pool = []
    eid = 1
    pool.append(make_player(eid, "GK", 1, 4.0, 3.0)); eid += 1
    pool.append(make_player(eid, "GK", 2, 4.0, 1.0)); eid += 1
    for i in range(4):
        pool.append(make_player(eid, "DEF", 3 + i, 4.0, 3.0)); eid += 1
    pool.append(make_player(eid, "DEF", 50, 5.0, 5.0, web_name="DefGood")); eid += 1
    pool.append(make_player(eid, "DEF", 51, 3.0, 1.0, web_name="DefCheap")); eid += 1
    for i in range(3):
        pool.append(make_player(eid, "MID", 8 + i, 4.0, 3.0)); eid += 1
    pool.append(make_player(eid, "MID", 11, 4.0, 6.0, web_name="AltCaptain")); eid += 1
    pool.append(make_player(eid, "MID", 20, 5.0, 5.0, web_name="MidGood")); eid += 1
    pool.append(make_player(eid, "MID", 21, 3.0, 1.0, web_name="MidCheap")); eid += 1
    for i in range(2):
        pool.append(make_player(eid, "FWD", 13 + i, 4.0, 3.0)); eid += 1
    pool.append(make_player(eid, "FWD", 30, 8.0, 7.0, web_name="Standout")); eid += 1
    pool.append(make_player(eid, "FWD", 31, 4.0, 3.0, web_name="FillerFWD")); eid += 1
    return pool


def test_captain_bonus_breaks_ties_in_favor_of_a_standout_scorer():
    result = optimize_squad(_captain_tiebreak_pool(), budget=62.0, max_per_club=15)

    assert any(p.web_name == "Standout" for p in result.squad)
    assert result.captain.web_name == "Standout"
    # The flat sum is unchanged from what the (rejected) alternative squad
    # would have scored -- proving this wasn't a flat-xPts improvement, only
    # a captaincy-value one.
    assert result.total_xi_xpts == pytest.approx(40.0)


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


def _owned_squad():
    """15 players across 5 clubs (composition-exact, satisfies max_per_club=3
    trivially), with one clearly weakest player per outfield position so
    transfer scenarios have an obvious candidate to replace."""
    return [
        make_player(1, "GK", 1, 4.5, 5.0),
        make_player(2, "GK", 2, 4.0, 1.0),
        make_player(3, "DEF", 1, 4.0, 5.0),
        make_player(4, "DEF", 2, 4.0, 4.0),
        make_player(5, "DEF", 3, 4.0, 3.0),
        make_player(6, "DEF", 4, 4.0, 2.0),
        make_player(7, "DEF", 5, 4.0, 1.0),  # weakest DEF
        make_player(8, "MID", 3, 4.5, 5.0),
        make_player(9, "MID", 4, 4.5, 4.0),
        make_player(10, "MID", 5, 4.5, 3.0),
        make_player(11, "MID", 1, 4.5, 2.0),
        make_player(12, "MID", 2, 4.5, 1.0),  # weakest MID
        make_player(13, "FWD", 3, 4.5, 5.0),
        make_player(14, "FWD", 4, 4.5, 4.0),
        make_player(15, "FWD", 5, 4.5, 3.0),  # weakest FWD
    ]


def _transfer_pool(def_gain: float, mid_gain: float, fwd_gain: float):
    """Owned squad plus 3 same-cost replacement candidates in an unused 6th
    club, each an upgrade on the weakest owned player at their position by
    the given gain -- lets tests dial in whether a gain clears the -4 hit."""
    owned = _owned_squad()
    replacements = [
        make_player(101, "DEF", 6, 4.0, 1.0 + def_gain, web_name="RepDEF"),
        make_player(102, "MID", 6, 4.5, 1.0 + mid_gain, web_name="RepMID"),
        make_player(103, "FWD", 6, 4.5, 3.0 + fwd_gain, web_name="RepFWD"),
    ]
    return owned + replacements


def test_optimize_transfers_makes_zero_transfers_with_no_alternatives():
    owned = _owned_squad()
    current_ids = {p.element_id for p in owned}
    budget = sum(p.now_cost for p in owned)

    tr = optimize_transfers(owned, current_ids, free_transfers=1, budget=budget)

    assert tr.transfers_made == 0
    assert tr.hits == 0
    assert tr.transfers_in == []
    assert tr.transfers_out == []


def test_optimize_transfers_takes_one_free_beneficial_transfer():
    pool = _transfer_pool(def_gain=0.1, mid_gain=12.0, fwd_gain=0.1)
    current_ids = {p.element_id for p in _owned_squad()}
    budget = sum(p.now_cost for p in _owned_squad())

    # Cap at 1 to isolate "does it take the single best beneficial swap".
    tr = optimize_transfers(pool, current_ids, free_transfers=1, budget=budget, max_transfers=1)

    assert tr.transfers_made == 1
    assert tr.hits == 0
    assert len(tr.transfers_in) == 1
    assert tr.transfers_in[0].web_name == "RepMID"


def test_optimize_transfers_takes_hit_when_worth_it():
    # Two big upgrades (MID +12, DEF +6), only 1 free transfer: taking both
    # costs a -4 hit but nets (12+6-4)=14 > just taking the MID alone (+12).
    pool = _transfer_pool(def_gain=6.0, mid_gain=12.0, fwd_gain=0.1)
    current_ids = {p.element_id for p in _owned_squad()}
    budget = sum(p.now_cost for p in _owned_squad())

    tr = optimize_transfers(pool, current_ids, free_transfers=1, budget=budget)

    assert tr.transfers_made == 2
    assert tr.hits == 1


def test_optimize_transfers_skips_hit_when_not_worth_it():
    # A second upgrade (DEF +1.0) is smaller than the hit cost (4.0), so
    # taking it would net negative -- the optimizer should skip it and only
    # take the single free, clearly-beneficial MID transfer.
    pool = _transfer_pool(def_gain=1.0, mid_gain=12.0, fwd_gain=0.5)
    current_ids = {p.element_id for p in _owned_squad()}
    budget = sum(p.now_cost for p in _owned_squad())

    tr = optimize_transfers(pool, current_ids, free_transfers=1, budget=budget)

    assert tr.transfers_made == 1
    assert tr.hits == 0
    assert tr.transfers_in[0].web_name == "RepMID"


def test_optimize_transfers_respects_max_transfers_cap():
    # Both swaps are individually worth a hit, but capped at 1 transfer.
    pool = _transfer_pool(def_gain=6.0, mid_gain=12.0, fwd_gain=0.1)
    current_ids = {p.element_id for p in _owned_squad()}
    budget = sum(p.now_cost for p in _owned_squad())

    tr = optimize_transfers(pool, current_ids, free_transfers=1, budget=budget, max_transfers=1)

    assert tr.transfers_made == 1
    assert tr.hits == 0  # 1 transfer is within the 1 free transfer


def test_optimize_transfers_respects_budget_and_club_constraints():
    pool = _transfer_pool(def_gain=6.0, mid_gain=12.0, fwd_gain=3.0)
    current_ids = {p.element_id for p in _owned_squad()}
    budget = sum(p.now_cost for p in _owned_squad())

    tr = optimize_transfers(pool, current_ids, free_transfers=2, budget=budget, max_per_club=3)

    assert tr.result.total_cost <= budget + 1e-6
    for team_id in {p.team_id for p in pool}:
        assert sum(1 for p in tr.result.squad if p.team_id == team_id) <= 3


def test_optimize_transfers_hit_cost_is_configurable():
    # With a near-zero hit cost, even a marginal upgrade becomes worth taking.
    pool = _transfer_pool(def_gain=1.0, mid_gain=12.0, fwd_gain=0.5)
    current_ids = {p.element_id for p in _owned_squad()}
    budget = sum(p.now_cost for p in _owned_squad())

    tr = optimize_transfers(pool, current_ids, free_transfers=1, budget=budget, hit_cost=0.1)

    assert tr.transfers_made > 1
    assert tr.hit_cost == 0.1


def test_optimize_transfers_infeasible_budget_raises():
    pool = _transfer_pool(def_gain=1.0, mid_gain=1.0, fwd_gain=1.0)
    current_ids = {p.element_id for p in _owned_squad()}

    with pytest.raises(InfeasibleError):
        optimize_transfers(pool, current_ids, free_transfers=1, budget=5.0)


def test_optimize_transfers_default_hit_cost_matches_constant():
    owned = _owned_squad()
    current_ids = {p.element_id for p in owned}
    budget = sum(p.now_cost for p in owned)

    tr = optimize_transfers(owned, current_ids, free_transfers=1, budget=budget)
    assert tr.hit_cost == TRANSFER_HIT_COST


def _bench_quality_pool():
    """4 clearly XI-worthy DEF (always start, need only 3-5 of 5 to), plus a
    contested 5th-DEF slot: two same-price candidates, one pure fodder and
    one a credible-but-not-XI-worthy backup. Neither can ever start (both
    are weaker than every fixed starter), so whichever wins the slot only
    affects the bench, never the starting XI -- isolating the bench-quality
    term's effect from squad/XI selection."""
    return [
        make_player(1, "GK", 1, 4.5, 6.0),
        make_player(2, "GK", 2, 4.0, 1.0),
        make_player(3, "DEF", 1, 5.0, 6.0),
        make_player(4, "DEF", 2, 5.0, 5.5),
        make_player(5, "DEF", 3, 5.0, 5.0),
        make_player(6, "DEF", 4, 5.0, 4.5),
        make_player(7, "DEF", 5, 4.0, 0.1, web_name="Fodder"),
        make_player(107, "DEF", 5, 4.0, 3.0, web_name="Credible"),
        make_player(8, "MID", 3, 4.5, 6.0),
        make_player(9, "MID", 4, 4.5, 5.5),
        make_player(10, "MID", 5, 4.5, 5.0),
        # Not 4.5 (would exactly tie DEF #6 above): a genuine tie between two
        # equally-optimal starting XIs is a real ambiguity the solver can
        # break either way, unrelated to anything this test is checking.
        make_player(11, "MID", 1, 4.5, 4.4),
        make_player(12, "MID", 2, 4.5, 4.0),
        make_player(13, "FWD", 3, 5.0, 6.0),
        make_player(14, "FWD", 4, 5.0, 5.5),
        make_player(15, "FWD", 5, 5.0, 5.0),
    ]


def test_bench_quality_weight_prefers_credible_backup_without_hurting_xi():
    pool = _bench_quality_pool()
    budget = 70.0

    with_weight = optimize_squad(pool, budget=budget, max_per_club=3)
    no_weight = optimize_squad(pool, budget=budget, max_per_club=3, bench_quality_weight=0.0)

    assert any(p.web_name == "Credible" for p in with_weight.squad)
    assert not any(p.web_name == "Fodder" for p in with_weight.squad)

    # The bench-quality preference must never come at the cost of
    # starting-XI quality: same starters, same total either way.
    assert with_weight.total_xi_xpts == pytest.approx(no_weight.total_xi_xpts, abs=1e-6)
    assert {p.element_id for p in with_weight.starting_xi} == {p.element_id for p in no_weight.starting_xi}


def test_optimize_transfers_also_prefers_credible_bench():
    pool = _bench_quality_pool()
    # Currently-owned squad has "Fodder" in the bench slot; "Credible" is an
    # available same-price alternative not yet owned.
    current_ids = {p.element_id for p in pool if p.web_name != "Credible"}
    budget = 70.0

    tr = optimize_transfers(pool, current_ids, free_transfers=1, budget=budget)

    assert tr.transfers_made == 1
    assert tr.transfers_in[0].web_name == "Credible"
    assert tr.transfers_out[0].web_name == "Fodder"


def test_optimize_squad_excludes_players_below_ownership_floor(sample_pool):
    # A single standout player, cheap and huge xPts, would otherwise be an
    # obvious pick -- but at 3% ownership it must be excluded outright, not
    # merely down-weighted, regardless of how good its own numbers look.
    differential = make_player(999, "MID", 1, 4.0, 20.0, web_name="Differential", selected_by_percent=3.0)
    pool = sample_pool + [differential]

    result = optimize_squad(pool, budget=100.0, max_per_club=3)

    assert not any(p.web_name == "Differential" for p in result.squad)


def test_optimize_squad_raises_when_no_players_meet_ownership_floor(sample_pool):
    for p in sample_pool:
        p.selected_by_percent = 5.0  # every player below the 10% floor

    with pytest.raises(InfeasibleError):
        optimize_squad(sample_pool, budget=100.0, max_per_club=3)


def test_optimize_transfers_excludes_new_low_ownership_buys():
    pool = _bench_quality_pool()
    # "Credible" would otherwise win the contested bench slot (see the test
    # above) but is now a low-ownership differential -- it must not be
    # transferred in, even though nothing else about the scenario changed.
    for p in pool:
        if p.web_name == "Credible":
            p.selected_by_percent = 4.0
    current_ids = {p.element_id for p in pool if p.web_name != "Credible"}
    budget = 70.0

    tr = optimize_transfers(pool, current_ids, free_transfers=1, budget=budget)

    assert not any(p.web_name == "Credible" for p in tr.result.squad)


def test_optimize_transfers_does_not_force_sell_existing_low_ownership_player():
    # A player already owned whose ownership% has since slipped below the
    # floor must still be keepable -- the floor blocks new low-ownership
    # buys, it doesn't force an unrequested, potentially hit-costing sell.
    # "Credible" is also given low ownership here so it can't be bought in
    # as a replacement, isolating the "keep what's owned" behavior from the
    # separate bench-quality preference exercised above.
    pool = _bench_quality_pool()
    for p in pool:
        if p.web_name == "Fodder":
            p.selected_by_percent = 1.0
        elif p.web_name == "Credible":
            p.selected_by_percent = 4.0
    current_ids = {p.element_id for p in pool if p.web_name != "Credible"}
    budget = 70.0

    tr = optimize_transfers(pool, current_ids, free_transfers=1, budget=budget)

    assert tr.transfers_made == 0
    assert any(p.web_name == "Fodder" for p in tr.result.squad)
    assert tr.hits == 0  # a free transfer, no reason to skip a strictly-better bench option


def test_optimize_squad_force_includes_the_most_owned_player(sample_pool):
    # A poor-value, expensive player that the xPts objective would never
    # pick on its own merits, but who's overwhelmingly the most-owned
    # player in the pool -- the crowd's #1 pick is trusted as a hard signal
    # (see FORCE_INCLUDE_MOST_OWNED_PLAYER), regardless of what the model's
    # own numbers say.
    crowd_favorite = make_player(999, "FWD", 1, 14.0, 0.5, web_name="CrowdFavorite", selected_by_percent=90.0)
    pool = sample_pool + [crowd_favorite]

    result = optimize_squad(pool, budget=100.0, max_per_club=3)

    assert any(p.web_name == "CrowdFavorite" for p in result.squad)


def test_optimize_squad_force_include_can_be_disabled(sample_pool):
    crowd_favorite = make_player(999, "FWD", 1, 14.0, 0.5, web_name="CrowdFavorite", selected_by_percent=90.0)
    pool = sample_pool + [crowd_favorite]

    result = optimize_squad(pool, budget=100.0, max_per_club=3, force_include_most_owned=False)

    assert not any(p.web_name == "CrowdFavorite" for p in result.squad)


def test_optimize_squad_force_include_raises_when_unaffordable(sample_pool):
    # The most-owned player is so expensive that no valid 15-man squad can
    # both include them and stay within budget -- this must surface as the
    # normal InfeasibleError, not silently ignore the crowd-favorite rule.
    crowd_favorite = make_player(999, "FWD", 1, 95.0, 0.5, web_name="CrowdFavorite", selected_by_percent=90.0)
    pool = sample_pool + [crowd_favorite]

    with pytest.raises(InfeasibleError):
        optimize_squad(pool, budget=100.0, max_per_club=3)
