"""Budget-constrained squad optimizer, built on a PuLP MILP.

We solve for the 15-man squad AND the starting XI simultaneously in one
integer program: the objective maximizes total starting-XI xPts (plus a
small secondary term rewarding outfield bench quality -- see
BENCH_QUALITY_WEIGHT) subject to budget, squad-composition, per-club, and
valid-XI-shape constraints. This guarantees a globally optimal combination
for the given xPts scores and constraints (not a greedy approximation).

Captain/vice-captain are not decision variables in the MILP -- per the
spec, they are simply the highest- and second-highest-xPts players in the
chosen starting XI, assigned after the solve.
"""

from __future__ import annotations

import pulp

from fpl_forecast.constants import (
    BENCH_QUALITY_WEIGHT,
    DEFAULT_BUDGET,
    MAX_PER_CLUB,
    MIN_OWNERSHIP_PERCENT,
    POSITION_ORDER,
    SQUAD_COMPOSITION,
    TRANSFER_HIT_COST,
    XI_MAX,
    XI_MIN,
    XI_SIZE,
)
from fpl_forecast.models import PlayerScore, SquadResult, TransferResult


class InfeasibleError(RuntimeError):
    """Raised when no valid squad exists under the given constraints."""


def optimize_squad(
    players: list[PlayerScore],
    budget: float = DEFAULT_BUDGET,
    max_per_club: int = MAX_PER_CLUB,
    bench_quality_weight: float = BENCH_QUALITY_WEIGHT,
    min_ownership_percent: float = MIN_OWNERSHIP_PERCENT,
) -> SquadResult:
    if not players:
        raise InfeasibleError("No players available to select from.")

    players = [p for p in players if p.selected_by_percent >= min_ownership_percent]
    if not players:
        raise InfeasibleError(
            f"No players meet the {min_ownership_percent:.0f}% ownership floor."
        )

    prob = pulp.LpProblem("fpl_squad_selection", pulp.LpMaximize)

    squad_vars = {p.element_id: pulp.LpVariable(f"squad_{p.element_id}", cat="Binary") for p in players}
    xi_vars = {p.element_id: pulp.LpVariable(f"xi_{p.element_id}", cat="Binary") for p in players}

    by_id = {p.element_id: p for p in players}

    # Objective: maximize total starting-XI xPts, plus a small secondary
    # term rewarding outfield bench quality (see BENCH_QUALITY_WEIGHT) so
    # bench slots aren't filled with zero-chance-of-playing fodder purely
    # because they're marginally cheaper than a credible backup.
    prob += pulp.lpSum(xi_vars[p.element_id] * p.xpts for p in players) + bench_quality_weight * pulp.lpSum(
        (squad_vars[p.element_id] - xi_vars[p.element_id]) * p.xpts for p in players if p.position != "GK"
    )

    # A player can only start if they're in the squad.
    for p in players:
        prob += xi_vars[p.element_id] <= squad_vars[p.element_id]

    # Exactly 15 in the squad, 11 in the starting XI.
    prob += pulp.lpSum(squad_vars.values()) == 15
    prob += pulp.lpSum(xi_vars.values()) == XI_SIZE

    # Squad composition: 2 GK, 5 DEF, 5 MID, 3 FWD.
    for pos in POSITION_ORDER:
        pos_players = [p for p in players if p.position == pos]
        prob += pulp.lpSum(squad_vars[p.element_id] for p in pos_players) == SQUAD_COMPOSITION[pos]
        prob += pulp.lpSum(xi_vars[p.element_id] for p in pos_players) >= XI_MIN[pos]
        prob += pulp.lpSum(xi_vars[p.element_id] for p in pos_players) <= XI_MAX[pos]

    # Budget cap.
    prob += pulp.lpSum(squad_vars[p.element_id] * p.now_cost for p in players) <= budget

    # Max players per real-world club.
    team_ids = {p.team_id for p in players}
    for team_id in team_ids:
        team_players = [p for p in players if p.team_id == team_id]
        prob += pulp.lpSum(squad_vars[p.element_id] for p in team_players) <= max_per_club

    solver = pulp.PULP_CBC_CMD(msg=False)
    prob.solve(solver)

    status = pulp.LpStatus[prob.status]
    if status != "Optimal":
        raise InfeasibleError(
            f"Solver could not find a feasible squad (status={status}). "
            "Try relaxing the budget or check that enough priced players are available."
        )

    squad = [by_id[eid] for eid, var in squad_vars.items() if var.value() == 1]
    starting_xi = [by_id[eid] for eid, var in xi_vars.items() if var.value() == 1]
    bench = [p for p in squad if p not in starting_xi]

    return _finalize_result(squad, starting_xi, bench, budget)


def _finalize_result(
    squad: list[PlayerScore], starting_xi: list[PlayerScore], bench: list[PlayerScore], budget: float
) -> SquadResult:
    starters_sorted = sorted(starting_xi, key=lambda p: p.xpts, reverse=True)
    captain = starters_sorted[0]
    vice_captain = starters_sorted[1]

    def_count = sum(1 for p in starting_xi if p.position == "DEF")
    mid_count = sum(1 for p in starting_xi if p.position == "MID")
    fwd_count = sum(1 for p in starting_xi if p.position == "FWD")
    formation = f"{def_count}-{mid_count}-{fwd_count}"

    bench_gk = [p for p in bench if p.position == "GK"]
    bench_outfield = sorted(
        (p for p in bench if p.position != "GK"), key=lambda p: p.xpts, reverse=True
    )
    bench_ordered = bench_outfield + bench_gk

    total_cost = sum(p.now_cost for p in squad)
    total_xi_xpts = sum(p.xpts for p in starting_xi)

    return SquadResult(
        squad=squad,
        starting_xi=starters_sorted,
        bench=bench_ordered,
        captain=captain,
        vice_captain=vice_captain,
        formation=formation,
        total_cost=round(total_cost, 1),
        total_xi_xpts=round(total_xi_xpts, 2),
        budget=budget,
    )


def optimize_transfers(
    players: list[PlayerScore],
    current_squad_ids: set[int],
    free_transfers: int,
    budget: float,
    max_per_club: int = MAX_PER_CLUB,
    hit_cost: float = TRANSFER_HIT_COST,
    max_transfers: int | None = None,
    bench_quality_weight: float = BENCH_QUALITY_WEIGHT,
    min_ownership_percent: float = MIN_OWNERSHIP_PERCENT,
) -> TransferResult:
    """Find the transfer set (0 or more swaps) that maximizes net gain.

    One MILP, structurally identical to optimize_squad's, plus:

    - `transfers_made` is a linear expression (15 minus however many of the
      currently-owned players are kept), not a separate decision variable,
      so it can be used directly in constraints.
    - a `hits` variable lower-bounded by `transfers_made - free_transfers`
      (and by 0), subtracted from the objective at `hit_cost` points each.
      Because the solver maximizes, `hits` settles exactly at
      max(0, transfers_made - free_transfers) at the optimum -- so 0, 1, 2...
      transfers are all implicitly considered and weighed against their real
      point cost in a single solve, not chosen by a separate heuristic.

    `budget` here means total resources available for the new squad: the
    current squad's value (at today's prices) plus any leftover bank. This
    is a simplification -- it doesn't model FPL's sell-price-below-current
    -price rule when a player has risen sharply in value since being
    bought (see README limitations).
    """
    if not players:
        raise InfeasibleError("No players available to select from.")

    # The ownership floor blocks new low-ownership buys, but never forces
    # out a player already owned purely because their ownership% has since
    # slipped below it -- that would mean an unrequested, potentially
    # hit-costing sell with no beneficial replacement in mind.
    players = [
        p for p in players if p.selected_by_percent >= min_ownership_percent or p.element_id in current_squad_ids
    ]
    if not players:
        raise InfeasibleError(
            f"No players meet the {min_ownership_percent:.0f}% ownership floor."
        )

    prob = pulp.LpProblem("fpl_transfer_selection", pulp.LpMaximize)

    squad_vars = {p.element_id: pulp.LpVariable(f"squad_{p.element_id}", cat="Binary") for p in players}
    xi_vars = {p.element_id: pulp.LpVariable(f"xi_{p.element_id}", cat="Binary") for p in players}
    hits_var = pulp.LpVariable("hits", lowBound=0)

    by_id = {p.element_id: p for p in players}

    kept_expr = pulp.lpSum(squad_vars[eid] for eid in current_squad_ids if eid in squad_vars)
    transfers_made_expr = 15 - kept_expr

    prob += (
        pulp.lpSum(xi_vars[p.element_id] * p.xpts for p in players)
        - hit_cost * hits_var
        + bench_quality_weight
        * pulp.lpSum(
            (squad_vars[p.element_id] - xi_vars[p.element_id]) * p.xpts for p in players if p.position != "GK"
        )
    )

    for p in players:
        prob += xi_vars[p.element_id] <= squad_vars[p.element_id]

    prob += pulp.lpSum(squad_vars.values()) == 15
    prob += pulp.lpSum(xi_vars.values()) == XI_SIZE

    for pos in POSITION_ORDER:
        pos_players = [p for p in players if p.position == pos]
        prob += pulp.lpSum(squad_vars[p.element_id] for p in pos_players) == SQUAD_COMPOSITION[pos]
        prob += pulp.lpSum(xi_vars[p.element_id] for p in pos_players) >= XI_MIN[pos]
        prob += pulp.lpSum(xi_vars[p.element_id] for p in pos_players) <= XI_MAX[pos]

    prob += pulp.lpSum(squad_vars[p.element_id] * p.now_cost for p in players) <= budget

    team_ids = {p.team_id for p in players}
    for team_id in team_ids:
        team_players = [p for p in players if p.team_id == team_id]
        prob += pulp.lpSum(squad_vars[p.element_id] for p in team_players) <= max_per_club

    prob += hits_var >= transfers_made_expr - free_transfers
    if max_transfers is not None:
        prob += transfers_made_expr <= max_transfers

    solver = pulp.PULP_CBC_CMD(msg=False)
    prob.solve(solver)

    status = pulp.LpStatus[prob.status]
    if status != "Optimal":
        raise InfeasibleError(
            f"Solver could not find a feasible transfer set (status={status}). "
            "Try relaxing max_transfers, or check the current squad's value plus "
            "bank is enough to field a valid squad."
        )

    squad = [by_id[eid] for eid, var in squad_vars.items() if var.value() == 1]
    starting_xi = [by_id[eid] for eid, var in xi_vars.items() if var.value() == 1]
    bench = [p for p in squad if p not in starting_xi]

    new_squad_ids = {p.element_id for p in squad}
    kept_ids = current_squad_ids & new_squad_ids
    transfers_made = 15 - len(kept_ids)
    transfers_out = [by_id[eid] for eid in (current_squad_ids - new_squad_ids) if eid in by_id]
    transfers_in = [by_id[eid] for eid in (new_squad_ids - current_squad_ids)]
    hits = max(0, transfers_made - free_transfers)

    result = _finalize_result(squad, starting_xi, bench, budget)
    bank_remaining = round(budget - result.total_cost, 1)

    return TransferResult(
        result=result,
        transfers_out=transfers_out,
        transfers_in=transfers_in,
        transfers_made=transfers_made,
        free_transfers=free_transfers,
        hits=hits,
        hit_cost=hit_cost,
        bank_remaining=bank_remaining,
    )
