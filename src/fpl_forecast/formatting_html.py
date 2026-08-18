"""Self-contained HTML report for a SquadResult: a pitch-view squad card.

Produces a single standalone .html file (inline CSS, no external
requests) suitable for opening directly in a browser. This is separate
from the plain-text CLI summary in formatting.py -- both are generated
from the same SquadResult, they just render it differently.

Beyond the squad itself, the report also shows: each squad team's
upcoming fixture run (FDR-coloured), a wider "who else was in the mix"
table per position, and a glossary explaining the model's terminology --
so the report is readable without cross-referencing the README.
"""

from __future__ import annotations

from datetime import datetime, timezone
from html import escape

from fpl_forecast.constants import FIXTURE_TICKER_GWS, POSITION_ORDER
from fpl_forecast.models import PlayerScore, SquadResult

_POSITION_LABELS = {"GK": "Goalkeepers", "DEF": "Defenders", "MID": "Midfielders", "FWD": "Forwards"}

# FDR 1 (easiest) -> 5 (hardest), matching the FPL app's traffic-light convention.
_FDR_COLORS = {
    1: "#1f9d55",
    2: "#34d399",
    3: "#f5c542",
    4: "#e8834f",
    5: "#e05252",
}


def _fdr_color(difficulty: int) -> str:
    return _FDR_COLORS.get(difficulty, "#5a6b8c")


def _risk_tags(p: PlayerScore) -> str:
    tags = []
    if p.availability_prob < 0.5:
        tags.append('<span class="tag tag-risk">rotation risk</span>')
    if p.data_confidence < 0.3:
        tags.append('<span class="tag tag-warn">limited data</span>')
    return "".join(tags)


def _card(p: PlayerScore, result: SquadResult, sub_index: int | None = None) -> str:
    badge = ""
    card_class = "card"
    if p.element_id == result.captain.element_id:
        badge = '<span class="badge badge-c">C</span>'
        card_class += " captain"
    elif p.element_id == result.vice_captain.element_id:
        badge = '<span class="badge badge-vc">VC</span>'
        card_class += " vice"

    sub_label = f'<div class="sub-index">{sub_index}</div>' if sub_index is not None else ""
    tags = _risk_tags(p)
    tags_html = f'<div class="card-tags">{tags}</div>' if tags else ""

    return f"""
    <div class="{card_class}">
      {sub_label}{badge}
      <div class="name">{escape(p.web_name)}</div>
      <div class="meta">{escape(p.team_short)} &middot; £{p.now_cost:.1f}m</div>
      <div class="xpts">{p.xpts:.2f} pts</div>
      <div class="avail">{p.availability_prob:.0%} avail</div>
      {tags_html}
    </div>
    """.strip()


def _fixture_ticker_html(ticker: dict[int, list[dict]] | None, squad: list[PlayerScore]) -> str:
    if not ticker:
        return ""

    seen: dict[int, str] = {}
    for p in squad:
        seen.setdefault(p.team_id, p.team_short)

    rows = []
    for team_id, team_short in sorted(seen.items(), key=lambda kv: kv[1]):
        fixtures = ticker.get(team_id, [])
        if not fixtures:
            boxes = '<span class="fdr-box fdr-blank">BLANK</span>'
        else:
            boxes = "".join(
                f'<span class="fdr-box" style="background:{_fdr_color(f["difficulty"])}" '
                f'title="GW{f["event"]}: {escape(f["opponent"])} ({"H" if f["is_home"] else "A"}), FDR {f["difficulty"]}">'
                f'{escape(f["opponent"])} {"(H)" if f["is_home"] else "(A)"}'
                f"</span>"
                for f in fixtures
            )
        rows.append(
            f"""
            <div class="ticker-row">
              <div class="ticker-team">{escape(team_short)}</div>
              <div class="ticker-fixtures">{boxes}</div>
            </div>
            """
        )

    return f"""
    <div class="ticker-section">
      <h2>Upcoming fixtures (squad clubs, next {FIXTURE_TICKER_GWS} GWs)</h2>
      <div class="ticker-caveat">
        Colour = FDR (green easiest &rarr; red hardest), from the FPL API's own team
        strength ratings. These are informed by prior seasons and can lag real
        squad changes (transfers, managerial changes) &mdash; especially early
        in a new season.
      </div>
      {''.join(rows)}
    </div>
    """


def _gameweek_fixtures_html(
    gw_fixtures: list[dict] | None, teams_lookup: dict[int, str] | None, gameweek: int
) -> str:
    if not gw_fixtures or not teams_lookup:
        return ""

    rows = []
    for f in sorted(gw_fixtures, key=lambda x: x.get("kickoff_time") or ""):
        home = teams_lookup.get(f["team_h"], "?")
        away = teams_lookup.get(f["team_a"], "?")
        rows.append(
            f"""
            <div class="fixture-row">
              <span class="fixture-team">{escape(home)}</span>
              <span class="fdr-box" style="background:{_fdr_color(f['team_h_difficulty'])}">FDR {f['team_h_difficulty']}</span>
              <span class="fixture-vs">v</span>
              <span class="fdr-box" style="background:{_fdr_color(f['team_a_difficulty'])}">FDR {f['team_a_difficulty']}</span>
              <span class="fixture-team">{escape(away)}</span>
            </div>
            """
        )

    return f"""
    <div class="gw-fixtures-section">
      <h2>Gameweek {gameweek} fixtures</h2>
      <div class="gw-fixtures-grid">{''.join(rows)}</div>
    </div>
    """


def _full_league_ticker_html(fixture_ticker: dict[int, list[dict]] | None, teams_lookup: dict[int, str] | None) -> str:
    if not fixture_ticker or not teams_lookup:
        return ""

    rows = []
    for team_id, team_short in sorted(teams_lookup.items(), key=lambda kv: kv[1]):
        fixtures = fixture_ticker.get(team_id, [])
        if not fixtures:
            boxes = '<span class="fdr-box fdr-blank">BLANK</span>'
        else:
            boxes = "".join(
                f'<span class="fdr-box" style="background:{_fdr_color(f["difficulty"])}" '
                f'title="GW{f["event"]}: {escape(f["opponent"])} ({"H" if f["is_home"] else "A"}), FDR {f["difficulty"]}">'
                f'{escape(f["opponent"])} {"(H)" if f["is_home"] else "(A)"}'
                f"</span>"
                for f in fixtures
            )
        rows.append(
            f"""
            <div class="ticker-row">
              <div class="ticker-team">{escape(team_short)}</div>
              <div class="ticker-fixtures">{boxes}</div>
            </div>
            """
        )

    return f"""
    <div class="ticker-section">
      <h2>Full-league fixture difficulty (next {FIXTURE_TICKER_GWS} GWs)</h2>
      <div class="ticker-caveat">
        Every club, not just this squad's -- useful for spotting a good transfer target's
        run even if nobody from that club made this gameweek's squad.
      </div>
      {''.join(rows)}
    </div>
    """


def _component_breakdown_chart_html(top_picks: list[PlayerScore]) -> str:
    if not top_picks:
        return ""

    max_value = max(
        (max(p.model_component, p.form_component, p.season_component) for p in top_picks), default=1.0
    )
    max_value = max(max_value, 0.1)

    bars_spec = [("Model", "model_component", "#34d399"), ("Form", "form_component", "#f5c542"), ("Season", "season_component", "#c9d3e0")]

    rows = []
    for p in top_picks:
        bar_rows = "".join(
            f"""
            <div class="chart-bar-row">
              <span class="chart-bar-label">{label}</span>
              <div class="chart-bar-track">
                <div class="chart-bar-fill" style="width:{max(0.0, getattr(p, attr)) / max_value * 100:.1f}%;background:{color}"></div>
              </div>
              <span class="chart-bar-value">{getattr(p, attr):.2f}</span>
            </div>
            """
            for label, attr, color in bars_spec
        )
        rows.append(
            f"""
            <div class="chart-block">
              <div class="chart-title">{escape(p.web_name)} <span class="reasoning-sub">({p.xpts:.2f} proj. pts)</span></div>
              {bar_rows}
            </div>
            """
        )

    return f"""
    <div class="chart-section">
      <h2>Score breakdown (top 5 picks)</h2>
      <div class="chart-caveat">
        How each player's projected points is built: the fixture-adjusted model estimate,
        recency-weighted recent form, and season-long output -- all in points, before the
        availability scaling is applied.
      </div>
      <div class="chart-grid">{''.join(rows)}</div>
    </div>
    """


def _player_pool_tables_html(all_scores: list[PlayerScore] | None, squad_ids: set[int], top_n: int = 8) -> str:
    if not all_scores:
        return ""

    sections = []
    for pos in POSITION_ORDER:
        pool = sorted((p for p in all_scores if p.position == pos), key=lambda p: p.xpts, reverse=True)[:top_n]
        if not pool:
            continue
        rows = []
        for p in pool:
            picked = p.element_id in squad_ids
            row_class = "picked" if picked else ""
            cs = f"{p.clean_sheet_prob:.0%}" if p.clean_sheet_prob is not None else "&ndash;"
            tags = _risk_tags(p)
            rows.append(
                f"""
                <tr class="{row_class}">
                  <td>{escape(p.web_name)}{' <span class="picked-mark">&#10003; squad</span>' if picked else ''}</td>
                  <td>{escape(p.team_short)}</td>
                  <td>£{p.now_cost:.1f}m</td>
                  <td>{p.xpts:.2f}</td>
                  <td>{p.availability_prob:.0%}</td>
                  <td>{cs}</td>
                  <td>{p.selected_by_percent:.1f}%</td>
                  <td>{escape(p.fixture_desc) or '&ndash;'}</td>
                  <td>{tags or '&ndash;'}</td>
                </tr>
                """
            )
        sections.append(
            f"""
            <div class="pool-table-wrap">
              <h3>{_POSITION_LABELS[pos]}</h3>
              <div class="table-scroll">
              <table class="pool-table">
                <thead>
                  <tr>
                    <th>Player</th><th>Team</th><th>Price</th><th>Proj. Pts</th>
                    <th>Avail.</th><th>CS%</th><th>Own%</th><th>Fixture(s)</th><th>Flags</th>
                  </tr>
                </thead>
                <tbody>{''.join(rows)}</tbody>
              </table>
              </div>
            </div>
            """
        )

    return f"""
    <div class="pool-section">
      <h2>Who else was in the mix (top {top_n} per position by projected points)</h2>
      {''.join(sections)}
    </div>
    """


def _most_selected_html(all_scores: list[PlayerScore] | None, squad_ids: set[int], top_n: int = 8) -> str:
    if not all_scores:
        return ""

    sections = []
    for pos in POSITION_ORDER:
        pool = sorted(
            (p for p in all_scores if p.position == pos), key=lambda p: p.selected_by_percent, reverse=True
        )[:top_n]
        if not pool or pool[0].selected_by_percent <= 0:
            continue
        rows = []
        for p in pool:
            picked = p.element_id in squad_ids
            row_class = "picked" if picked else ""
            rows.append(
                f"""
                <tr class="{row_class}">
                  <td>{escape(p.web_name)}{' <span class="picked-mark">&#10003; squad</span>' if picked else ''}</td>
                  <td>{escape(p.team_short)}</td>
                  <td>£{p.now_cost:.1f}m</td>
                  <td>{p.selected_by_percent:.1f}%</td>
                  <td>{p.xpts:.2f}</td>
                </tr>
                """
            )
        sections.append(
            f"""
            <div class="pool-table-wrap">
              <h3>{_POSITION_LABELS[pos]}</h3>
              <div class="table-scroll">
              <table class="pool-table">
                <thead>
                  <tr><th>Player</th><th>Team</th><th>Price</th><th>Own%</th><th>Our Proj. Pts</th></tr>
                </thead>
                <tbody>{''.join(rows)}</tbody>
              </table>
              </div>
            </div>
            """
        )

    if not sections:
        return ""

    return f"""
    <div class="pool-section">
      <h2>Most selected players (highest ownership %, top {top_n} per position)</h2>
      <div class="ticker-caveat">
        What the rest of the FPL player base actually owns, independent of this tool's own
        picks -- a high-ownership player with a low "Our Proj. Pts" here is one this model
        disagrees with the crowd on, in either direction.
      </div>
      {''.join(sections)}
    </div>
    """


_GLOSSARY = [
    ("Proj. Pts (Projected Points)", "The model's best estimate of this player's FPL points for the "
     "gameweek -- built from real point-scoring components (expected goal involvement, clean-sheet "
     "probability, appearance points), then blended with recent form and season-long output, then "
     "scaled down by how likely the player is to actually play. It is a projection, not a guarantee: "
     "a 3.0 for an explosive player like Haaland means the model currently has low confidence he'll "
     "return a big haul this week (e.g. an unfavourable fixture or thin current-season data), not that "
     "3 points is his ceiling -- check the reasoning breakdown below for why. This is the same concept "
     "the FPL community calls \"xPts\", renamed here to be clearer it's a points estimate, not a "
     "probability."),
    ("FDR (Fixture Difficulty Rating)", "The FPL API's own 1 (easiest) to 5 (hardest) rating of how "
     "tough an opponent is, from the perspective of the team being rated."),
    ("Availability %", "The model's estimate of the probability a player features meaningfully this "
     "gameweek. Two signals multiplied together: an injury/fitness flag from the FPL API (which reports "
     "100% for every fully-fit player, including fit backups who rarely play), and a squad-role signal "
     "from actual minutes played (recent starts, or last season's minutes when there's no current-season "
     "data yet) -- so a fully-fit bench player still reads as unlikely to feature."),
    ("Clean Sheet % (CS%)", "Estimated probability the player's team doesn't concede in this fixture, "
     "from a simplified Poisson model built on the FPL API's team strength ratings."),
    ("Own% (Ownership)", "What percentage of all FPL managers currently own this player, straight from "
     "the FPL API (\"selected_by_percent\") -- has no bearing on this tool's own projection, shown "
     "purely for context/comparison against the crowd."),
    ("Limited data / rotation risk flags", "\"Limited data\" means the season/form/attacking numbers are "
     "backed by only a handful of minutes played, so a hot small-sample points-per-game or per-90 goal "
     "rate is discounted toward a neutral baseline rather than trusted outright -- this is what stops a "
     "single big cameo from looking like a proven nailed-on starter. \"Rotation risk\" means under 50% "
     "estimated availability."),
    ("Fixture run", "A small adjustment based on the average FDR of the next few gameweeks *after* this "
     "one -- a player who's great this week but faces a brutal run right after is worth slightly less, "
     "since swapping them back out again costs a transfer or a hit."),
    ("Formation", "The DEF-MID-FWD counts in the starting XI (goalkeeper is always exactly 1)."),
    ("Captain / Vice-Captain", "The two highest-projected-points starters. In real FPL scoring the "
     "captain's points are doubled (falling back to the vice-captain's if the captain doesn't play)."),
    ("Budget", "Total squad cost against the configurable cap (default £100.0m)."),
]


def _glossary_html() -> str:
    items = "".join(
        f"<dt>{escape(term)}</dt><dd>{escape(definition)}</dd>" for term, definition in _GLOSSARY
    )
    return f"""
    <div class="glossary-section">
      <h2>What do these numbers mean?</h2>
      <dl class="glossary">{items}</dl>
    </div>
    """


def render_html(
    result: SquadResult,
    gameweek: int,
    generated_at: str | None = None,
    all_scores: list[PlayerScore] | None = None,
    fixture_ticker: dict[int, list[dict]] | None = None,
    gw_fixtures: list[dict] | None = None,
    teams_lookup: dict[int, str] | None = None,
) -> str:
    if generated_at is None:
        generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    rows_html = []
    for pos in POSITION_ORDER:
        starters = [p for p in result.starting_xi if p.position == pos]
        if not starters:
            continue
        cards = "".join(_card(p, result) for p in starters)
        rows_html.append(f'<div class="pitch-row">{cards}</div>')

    bench_cards = "".join(_card(p, result, sub_index=i) for i, p in enumerate(result.bench, start=1))

    top_picks = sorted(result.starting_xi, key=lambda p: p.xpts, reverse=True)[:5]
    reasoning_items = []
    for p in top_picks:
        reason_lines = "".join(f"<li>{escape(r)}</li>" for r in p.reasons)
        reasoning_items.append(
            f"""
            <div class="reasoning-block">
              <div class="reasoning-title">{escape(p.web_name)}
                <span class="reasoning-sub">({escape(p.position)}, {escape(p.team_short)} &middot; {p.xpts:.2f} proj. pts)</span>
              </div>
              <ul>{reason_lines}</ul>
            </div>
            """
        )

    budget_pct = min(100.0, (result.total_cost / result.budget * 100.0) if result.budget else 0.0)
    squad_ids = {p.element_id for p in result.squad}

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>FPL Squad — Gameweek {gameweek}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root {{
    --bg: #0b1220;
    --panel: #121a2b;
    --panel-border: #23304a;
    --pitch: #0f5c2e;
    --pitch-line: rgba(255,255,255,0.35);
    --text: #eef2fb;
    --text-dim: #9fb0cc;
    --accent: #34d399;
    --gold: #f5c542;
    --silver: #c9d3e0;
    --warn: #e8834f;
    --risk: #e05252;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    padding: 2rem 1rem 3rem;
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  }}
  .wrap {{ max-width: 1080px; margin: 0 auto; }}
  h1 {{ font-size: 1.5rem; margin: 0 0 0.25rem; }}
  h2 {{ font-size: 1.05rem; color: var(--text-dim); font-weight: 600; margin: 0 0 0.75rem; }}
  h3 {{ font-size: 0.95rem; color: var(--text); font-weight: 600; margin: 1rem 0 0.5rem; }}
  .subtitle {{ color: var(--text-dim); margin-bottom: 1.5rem; }}

  .summary {{
    display: flex;
    flex-wrap: wrap;
    gap: 1rem;
    background: var(--panel);
    border: 1px solid var(--panel-border);
    border-radius: 12px;
    padding: 1rem 1.25rem;
    margin-bottom: 1.5rem;
  }}
  .summary .stat {{ min-width: 160px; }}
  .summary .stat .label {{ color: var(--text-dim); font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.04em; }}
  .summary .stat .value {{ font-size: 1.15rem; font-weight: 600; margin-top: 0.15rem; }}
  .budget-bar {{ flex: 1 1 220px; }}
  .budget-track {{
    background: #1c2740; border-radius: 999px; height: 8px; overflow: hidden; margin-top: 0.5rem;
  }}
  .budget-fill {{ background: var(--accent); height: 100%; }}

  .pitch {{
    background:
      repeating-linear-gradient(180deg, rgba(255,255,255,0.03) 0 40px, rgba(0,0,0,0.03) 40px 80px),
      var(--pitch);
    border: 2px solid var(--pitch-line);
    border-radius: 16px;
    padding: 1.5rem 1rem;
    display: flex;
    flex-direction: column;
    gap: 1.25rem;
    position: relative;
  }}
  .pitch-row {{
    display: flex;
    justify-content: center;
    flex-wrap: wrap;
    gap: 1rem;
  }}

  .card {{
    background: rgba(9, 14, 26, 0.82);
    border: 1px solid rgba(255,255,255,0.12);
    border-radius: 10px;
    padding: 0.6rem 0.9rem;
    min-width: 116px;
    text-align: center;
    position: relative;
  }}
  .card.captain {{ border-color: var(--gold); box-shadow: 0 0 0 1px var(--gold); }}
  .card.vice {{ border-color: var(--silver); box-shadow: 0 0 0 1px var(--silver); }}
  .card .name {{ font-weight: 600; font-size: 0.92rem; }}
  .card .meta {{ color: var(--text-dim); font-size: 0.75rem; margin-top: 0.15rem; }}
  .card .xpts {{ color: var(--accent); font-size: 0.85rem; margin-top: 0.3rem; font-weight: 600; }}
  .card .avail {{ color: var(--text-dim); font-size: 0.7rem; margin-top: 0.1rem; }}
  .card-tags {{ margin-top: 0.35rem; display: flex; flex-wrap: wrap; gap: 0.25rem; justify-content: center; }}
  .badge {{
    position: absolute; top: -8px; right: -8px;
    font-size: 0.65rem; font-weight: 700;
    border-radius: 999px; width: 22px; height: 22px;
    display: flex; align-items: center; justify-content: center;
    color: #0b1220;
  }}
  .badge-c {{ background: var(--gold); }}
  .badge-vc {{ background: var(--silver); }}

  .tag {{
    font-size: 0.62rem; font-weight: 600; padding: 0.1rem 0.4rem;
    border-radius: 999px; white-space: nowrap;
  }}
  .tag-risk {{ background: rgba(224, 82, 82, 0.18); color: #ff9c9c; }}
  .tag-warn {{ background: rgba(232, 131, 79, 0.18); color: #ffb98a; }}

  .bench-section {{ margin-top: 1.5rem; }}
  .bench-row {{ display: flex; flex-wrap: wrap; gap: 1rem; }}
  .sub-index {{
    position: absolute; top: -8px; left: -8px;
    background: #1c2740; color: var(--text-dim);
    font-size: 0.65rem; font-weight: 700;
    border-radius: 999px; width: 20px; height: 20px;
    display: flex; align-items: center; justify-content: center;
  }}

  .ticker-section, .pool-section, .glossary-section, .reasoning-section {{ margin-top: 2rem; }}
  .ticker-caveat {{
    color: var(--text-dim); font-size: 0.8rem; margin-bottom: 0.85rem; max-width: 720px;
  }}
  .ticker-row {{
    display: flex; align-items: center; gap: 0.75rem;
    padding: 0.4rem 0; border-bottom: 1px solid var(--panel-border);
  }}
  .ticker-team {{ width: 60px; flex-shrink: 0; font-weight: 600; font-size: 0.85rem; }}
  .ticker-fixtures {{ display: flex; flex-wrap: wrap; gap: 0.35rem; }}
  .fdr-box {{
    color: #0b1220; font-size: 0.72rem; font-weight: 700;
    padding: 0.2rem 0.5rem; border-radius: 6px; white-space: nowrap;
  }}
  .fdr-box.fdr-blank {{ background: #2a3752; color: var(--text-dim); }}

  .table-scroll {{ overflow-x: auto; }}
  .pool-table {{ width: 100%; border-collapse: collapse; font-size: 0.82rem; min-width: 560px; }}
  .pool-table th {{
    text-align: left; color: var(--text-dim); font-weight: 600; font-size: 0.72rem;
    text-transform: uppercase; letter-spacing: 0.03em;
    padding: 0.4rem 0.6rem; border-bottom: 1px solid var(--panel-border);
  }}
  .pool-table td {{ padding: 0.4rem 0.6rem; border-bottom: 1px solid rgba(35,48,74,0.5); }}
  .pool-table tr.picked {{ background: rgba(52, 211, 153, 0.08); }}
  .picked-mark {{ color: var(--accent); font-size: 0.72rem; font-weight: 600; }}

  .glossary dt {{ font-weight: 600; margin-top: 0.75rem; }}
  .glossary dd {{ color: var(--text-dim); margin: 0.2rem 0 0; font-size: 0.85rem; max-width: 720px; }}

  .gw-fixtures-section {{ margin-top: 2rem; }}
  .gw-fixtures-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 0.5rem; }}
  .fixture-row {{
    display: flex; align-items: center; gap: 0.4rem;
    background: var(--panel); border: 1px solid var(--panel-border);
    border-radius: 8px; padding: 0.5rem 0.6rem; font-size: 0.82rem;
  }}
  .fixture-team {{ flex: 1; font-weight: 600; }}
  .fixture-team:last-child {{ text-align: right; }}
  .fixture-vs {{ color: var(--text-dim); font-size: 0.72rem; }}

  .chart-section {{ margin-top: 2rem; }}
  .chart-caveat {{ color: var(--text-dim); font-size: 0.8rem; margin-bottom: 0.85rem; max-width: 720px; }}
  .chart-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 1rem; }}
  .chart-block {{
    background: var(--panel); border: 1px solid var(--panel-border);
    border-radius: 10px; padding: 0.85rem 1rem;
  }}
  .chart-title {{ font-weight: 600; margin-bottom: 0.5rem; }}
  .chart-bar-row {{ display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.3rem; }}
  .chart-bar-label {{ width: 46px; flex-shrink: 0; font-size: 0.72rem; color: var(--text-dim); }}
  .chart-bar-track {{ flex: 1; background: #1c2740; border-radius: 4px; height: 10px; overflow: hidden; }}
  .chart-bar-fill {{ height: 100%; border-radius: 4px; }}
  .chart-bar-value {{ width: 36px; flex-shrink: 0; font-size: 0.72rem; text-align: right; }}

  .reasoning-block {{
    background: var(--panel);
    border: 1px solid var(--panel-border);
    border-radius: 10px;
    padding: 0.85rem 1rem;
    margin-bottom: 0.75rem;
  }}
  .reasoning-title {{ font-weight: 600; }}
  .reasoning-sub {{ color: var(--text-dim); font-weight: 400; font-size: 0.85rem; }}
  .reasoning-block ul {{ margin: 0.4rem 0 0; padding-left: 1.1rem; color: var(--text-dim); font-size: 0.85rem; }}
  .reasoning-block li {{ margin-bottom: 0.2rem; }}

  footer {{ color: var(--text-dim); font-size: 0.75rem; margin-top: 2rem; text-align: center; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>FPL Squad Recommendation</h1>
  <div class="subtitle">Gameweek {gameweek}</div>

  <div class="summary">
    <div class="stat">
      <div class="label">Formation</div>
      <div class="value">{escape(result.formation)}</div>
    </div>
    <div class="stat">
      <div class="label">Captain</div>
      <div class="value">{escape(result.captain.web_name)}</div>
    </div>
    <div class="stat">
      <div class="label">Vice-Captain</div>
      <div class="value">{escape(result.vice_captain.web_name)}</div>
    </div>
    <div class="stat">
      <div class="label">Projected XI points</div>
      <div class="value">{result.total_xi_xpts:.2f}</div>
    </div>
    <div class="stat budget-bar">
      <div class="label">Budget used</div>
      <div class="value">£{result.total_cost:.1f}m / £{result.budget:.1f}m</div>
      <div class="budget-track"><div class="budget-fill" style="width:{budget_pct:.1f}%"></div></div>
    </div>
  </div>

  <div class="pitch">
    {''.join(rows_html)}
  </div>

  <div class="bench-section">
    <h2>Bench (sub order)</h2>
    <div class="bench-row">{bench_cards}</div>
  </div>

  {_gameweek_fixtures_html(gw_fixtures, teams_lookup, gameweek)}

  {_fixture_ticker_html(fixture_ticker, result.squad)}

  {_full_league_ticker_html(fixture_ticker, teams_lookup)}

  {_player_pool_tables_html(all_scores, squad_ids)}

  {_most_selected_html(all_scores, squad_ids)}

  <div class="reasoning-section">
    <h2>Top picks — reasoning</h2>
    {''.join(reasoning_items)}
  </div>

  {_component_breakdown_chart_html(top_picks)}

  {_glossary_html()}

  <footer>Generated by fpl-squad-optimizer at {escape(generated_at)} &middot; not affiliated with the Premier League or Fantasy Premier League</footer>
</div>
</body>
</html>
"""


def write_html(
    result: SquadResult,
    gameweek: int,
    path: str,
    generated_at: str | None = None,
    all_scores: list[PlayerScore] | None = None,
    fixture_ticker: dict[int, list[dict]] | None = None,
    gw_fixtures: list[dict] | None = None,
    teams_lookup: dict[int, str] | None = None,
) -> None:
    html = render_html(
        result,
        gameweek,
        generated_at=generated_at,
        all_scores=all_scores,
        fixture_ticker=fixture_ticker,
        gw_fixtures=gw_fixtures,
        teams_lookup=teams_lookup,
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
