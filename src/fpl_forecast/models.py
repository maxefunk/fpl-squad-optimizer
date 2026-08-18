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

    # Structured breakdown (same numbers as in `reasons`, but typed for
    # display -- e.g. the HTML report's player-pool table and glossary).
    model_component: float = 0.0
    form_component: float = 0.0
    season_component: float = 0.0
    clean_sheet_prob: float | None = None  # None for FWD (no CS points)
    data_confidence: float = 1.0  # 0-1: how much minutes back the season/form numbers
    fixture_desc: str = ""  # e.g. "TOT (H, FDR 3)" or "ARS (H, FDR 2), CHE (A, FDR 4)"

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


@dataclass
class TransferResult:
    """Output of the transfer optimizer: a new squad plus what changed to get there."""

    result: SquadResult
    transfers_out: list[PlayerScore]
    transfers_in: list[PlayerScore]
    transfers_made: int
    free_transfers: int
    hits: int
    hit_cost: float
    bank_remaining: float

    @property
    def hit_points(self) -> float:
        return self.hits * self.hit_cost
