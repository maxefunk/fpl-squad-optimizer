"""Plain data containers passed between the scoring, optimizer, and CLI layers."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PlayerScore:
    """A single player's projected gameweek data, ready for optimization."""

    element_id: int
    web_name: str
    full_name: str
    team_id: int
    team_name: str
    team_short: str
    position: str  # GK / DEF / MID / FWD
    now_cost: float  # in millions, e.g. 8.5
    xpts: float
    availability_prob: float
    num_fixtures: int
    reasons: list[str] = field(default_factory=list)

    @property
    def is_playing(self) -> bool:
        return self.num_fixtures > 0 and self.availability_prob > 0.0


@dataclass
class SquadResult:
    """Output of the optimizer: a full 15-man squad plus the chosen starting XI."""

    squad: list[PlayerScore]
    starting_xi: list[PlayerScore]
    bench: list[PlayerScore]
    captain: PlayerScore
    vice_captain: PlayerScore
    formation: str
    total_cost: float
    total_xi_xpts: float
    budget: float
