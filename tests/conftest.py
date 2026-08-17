from __future__ import annotations

import pytest

from fpl_forecast.models import PlayerScore


def make_player(
    element_id: int,
    position: str,
    team_id: int,
    cost: float,
    xpts: float,
    web_name: str | None = None,
) -> PlayerScore:
    return PlayerScore(
        element_id=element_id,
        web_name=web_name or f"Player{element_id}",
        full_name=web_name or f"Player {element_id}",
        team_id=team_id,
        team_name=f"Team{team_id}",
        team_short=f"T{team_id}",
        position=position,
        now_cost=cost,
        xpts=xpts,
        availability_prob=0.9,
        num_fixtures=1,
        reasons=[],
    )


@pytest.fixture
def sample_pool() -> list[PlayerScore]:
    """A diverse, feasible pool: 10 clubs x (2 GK, 3 DEF, 3 MID, 2 FWD).

    Costs and xPts vary by club/index so the optimizer has real choices to
    make, rather than every player being interchangeable.
    """
    pool: list[PlayerScore] = []
    eid = 1
    base_cost = {"GK": 4.0, "DEF": 4.0, "MID": 4.5, "FWD": 4.5}
    counts = {"GK": 2, "DEF": 3, "MID": 3, "FWD": 2}

    for team_id in range(1, 11):
        for position, n in counts.items():
            for i in range(n):
                cost = base_cost[position] + i * 0.7 + (team_id % 3) * 0.4
                xpts = 2.0 + (i * 0.9) + ((team_id * 7) % 5) * 0.5
                pool.append(make_player(eid, position, team_id, round(cost, 1), round(xpts, 2)))
                eid += 1
    return pool
