"""Budget-constrained squad optimizer, built on a PuLP MILP.

We solve for the 15-man squad AND the starting XI simultaneously in one
integer program: the objective maximizes total starting-XI xPts subject to
budget, squad-composition, per-club, and valid-XI-shape constraints. This
guarantees a globally optimal combination for the given xPts scores and
constraints (not a greedy approximation).

Captain/vice-captain are not decision variables in the MILP -- per the
spec, they are simply the highest- and second-highest-xPts players in the
chosen starting XI, assigned after the solve.
"""

from __future__ import annotations

import pulp

from fpl_forecast.constants import (
    DEFAULT_BUDGET,
    MAX_PER_CLUB,
    POSITION_ORDER,
    SQUAD_COMPOSITION,
    XI_MAX,
    XI_MIN,
    XI_SIZE,
)
from fpl_forecast.models import PlayerScore, SquadResult


class InfeasibleError(RuntimeError):
    """Raised when no valid squad exists under the given constraints."""


def optimize_squad(
    players: list[PlayerScore],
    budget: float = DEFAULT_BUDGET,
    max_per_club: int = MAX_PER_CLUB,
) -> SquadResult:
    if not players:
        raise InfeasibleError("No players available to select from.")

    prob = pulp.LpProblem("fpl_squad_selection", pulp.LpMaximize)

    squad_vars = {p.element_id: pulp.LpVariable(f"squad_{p.element_id}", cat="Binary") for p in players}
    xi_vars = {p.element_id: pulp.LpVariable(f"xi_{p.element_id}", cat="Binary") for p in players}

    by_id = {p.element_id: p for p in players}

    # Objective: maximize total starting-XI xPts.
    prob += pulp.lpSum(xi_vars[p.element_id] * p.xpts for p in players)

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
