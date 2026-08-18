"""Persisted "my team" state: the squad you actually own, tracked across
gameweeks so the CLI can compute accumulated points and suggest transfers.

Stored as a small JSON file (default `my_team.json`) rather than pulled
from FPL's authenticated "my-team" endpoint -- that endpoint requires a
logged-in session (cookies), while everything else in this tool works
against the public, unauthenticated API. See the README for the tradeoff.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from fpl_forecast.models import PlayerScore, SquadResult


@dataclass
class OwnedPlayer:
    element_id: int
    web_name: str
    position: str
    team_id: int
    team_short: str
    now_cost: float  # price at the time the squad was last saved/updated
    is_starting: bool  # was this player in the starting XI for the saved gameweek


@dataclass
class GameweekRecord:
    gameweek: int
    points: int
    captain_id: int  # whoever's points were actually doubled (captain, or VC if captain blanked)
    transfers_made: int
    hits: int


@dataclass
class TeamState:
    squad: list[OwnedPlayer]
    captain_id: int
    vice_captain_id: int
    bank: float
    free_transfers: int
    total_points: int = 0
    history: list[GameweekRecord] = field(default_factory=list)
    last_gameweek_saved: int = 0
    last_gameweek_recorded: int = 0

    @property
    def squad_ids(self) -> set[int]:
        return {p.element_id for p in self.squad}

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> TeamState:
        squad = [OwnedPlayer(**p) for p in data["squad"]]
        history = [GameweekRecord(**h) for h in data.get("history", [])]
        return cls(
            squad=squad,
            captain_id=data["captain_id"],
            vice_captain_id=data["vice_captain_id"],
            bank=data["bank"],
            free_transfers=data["free_transfers"],
            total_points=data.get("total_points", 0),
            history=history,
            last_gameweek_saved=data.get("last_gameweek_saved", 0),
            last_gameweek_recorded=data.get("last_gameweek_recorded", 0),
        )


def load_team(path: str | Path) -> TeamState | None:
    path = Path(path)
    if not path.exists():
        return None
    return TeamState.from_dict(json.loads(path.read_text()))


def save_team(path: str | Path, state: TeamState) -> None:
    Path(path).write_text(json.dumps(state.to_dict(), indent=2))


def team_state_from_squad_result(
    result: SquadResult, gameweek: int, free_transfers: int, bank: float | None = None
) -> TeamState:
    """Build a fresh TeamState from an optimizer SquadResult (initial save,
    or after a transfers run that produced a new squad/lineup)."""
    starting_ids = {p.element_id for p in result.starting_xi}
    squad = [
        OwnedPlayer(
            element_id=p.element_id,
            web_name=p.web_name,
            position=p.position,
            team_id=p.team_id,
            team_short=p.team_short,
            now_cost=p.now_cost,
            is_starting=p.element_id in starting_ids,
        )
        for p in result.squad
    ]
    if bank is None:
        bank = round(result.budget - result.total_cost, 1)
    return TeamState(
        squad=squad,
        captain_id=result.captain.element_id,
        vice_captain_id=result.vice_captain.element_id,
        bank=bank,
        free_transfers=free_transfers,
    )


def record_gameweek(
    state: TeamState,
    gameweek: int,
    actual_points: dict[int, int],
    actual_minutes: dict[int, int],
    transfers_made: int = 0,
    hits: int = 0,
) -> TeamState:
    """Record a completed gameweek's actual points against the saved squad.

    Only the starting XI's points count (bench players don't, matching real
    FPL scoring), plus the captain's points doubled -- falling back to the
    vice-captain if the captain didn't play. This does not simulate FPL's
    automatic substitutions for a blank non-captain starter (documented
    limitation, same simplification the backtest script makes).
    """
    if any(h.gameweek == gameweek for h in state.history):
        raise ValueError(f"Gameweek {gameweek} has already been recorded for this team.")

    starting_ids = [p.element_id for p in state.squad if p.is_starting]
    raw_total = sum(actual_points.get(eid, 0) for eid in starting_ids)

    captain_minutes = actual_minutes.get(state.captain_id, 0)
    captain_for_double = state.captain_id if captain_minutes > 0 else state.vice_captain_id
    captain_bonus = actual_points.get(captain_for_double, 0)

    gw_points = raw_total + captain_bonus

    state.history.append(
        GameweekRecord(
            gameweek=gameweek,
            points=gw_points,
            captain_id=captain_for_double,
            transfers_made=transfers_made,
            hits=hits,
        )
    )
    state.total_points += gw_points
    state.last_gameweek_recorded = gameweek
    return state


def current_squad_value(state: TeamState, current_prices: dict[int, float]) -> float:
    """Total value of the owned squad at today's prices (falls back to the
    last-known price for any player current data isn't available for, e.g.
    if they've since left the league)."""
    return sum(current_prices.get(p.element_id, p.now_cost) for p in state.squad)
