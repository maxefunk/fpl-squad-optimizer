"""Self-contained HTML report for a SquadResult: a pitch-view squad card.

Produces a single standalone .html file (inline CSS, no external
requests) suitable for opening directly in a browser. This is separate
from the plain-text CLI summary in formatting.py -- both are generated
from the same SquadResult, they just render it differently.
"""

from __future__ import annotations

from datetime import datetime, timezone
from html import escape

from fpl_forecast.constants import POSITION_ORDER
from fpl_forecast.models import PlayerScore, SquadResult

_POSITION_LABELS = {"GK": "Goalkeepers", "DEF": "Defenders", "MID": "Midfielders", "FWD": "Forwards"}


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

    return f"""
    <div class="{card_class}">
      {sub_label}{badge}
      <div class="name">{escape(p.web_name)}</div>
      <div class="meta">{escape(p.team_short)} &middot; £{p.now_cost:.1f}m</div>
      <div class="xpts">{p.xpts:.2f} xPts</div>
    </div>
    """.strip()


def render_html(result: SquadResult, gameweek: int, generated_at: str | None = None) -> str:
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
                <span class="reasoning-sub">({escape(p.position)}, {escape(p.team_short)} &middot; {p.xpts:.2f} xPts)</span>
              </div>
              <ul>{reason_lines}</ul>
            </div>
            """
        )

    budget_pct = min(100.0, (result.total_cost / result.budget * 100.0) if result.budget else 0.0)

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
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    padding: 2rem 1rem 3rem;
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  }}
  .wrap {{ max-width: 960px; margin: 0 auto; }}
  h1 {{ font-size: 1.5rem; margin: 0 0 0.25rem; }}
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
    min-width: 108px;
    text-align: center;
    position: relative;
  }}
  .card.captain {{ border-color: var(--gold); box-shadow: 0 0 0 1px var(--gold); }}
  .card.vice {{ border-color: var(--silver); box-shadow: 0 0 0 1px var(--silver); }}
  .card .name {{ font-weight: 600; font-size: 0.92rem; }}
  .card .meta {{ color: var(--text-dim); font-size: 0.75rem; margin-top: 0.15rem; }}
  .card .xpts {{ color: var(--accent); font-size: 0.85rem; margin-top: 0.3rem; font-weight: 600; }}
  .badge {{
    position: absolute; top: -8px; right: -8px;
    font-size: 0.65rem; font-weight: 700;
    border-radius: 999px; width: 22px; height: 22px;
    display: flex; align-items: center; justify-content: center;
    color: #0b1220;
  }}
  .badge-c {{ background: var(--gold); }}
  .badge-vc {{ background: var(--silver); }}

  .bench-section {{ margin-top: 1.5rem; }}
  .bench-section h2 {{ font-size: 1.05rem; color: var(--text-dim); font-weight: 600; margin-bottom: 0.75rem; }}
  .bench-row {{ display: flex; flex-wrap: wrap; gap: 1rem; }}
  .sub-index {{
    position: absolute; top: -8px; left: -8px;
    background: #1c2740; color: var(--text-dim);
    font-size: 0.65rem; font-weight: 700;
    border-radius: 999px; width: 20px; height: 20px;
    display: flex; align-items: center; justify-content: center;
  }}

  .reasoning-section {{ margin-top: 2rem; }}
  .reasoning-section h2 {{ font-size: 1.05rem; color: var(--text-dim); font-weight: 600; margin-bottom: 0.75rem; }}
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
      <div class="label">Projected XI xPts</div>
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

  <div class="reasoning-section">
    <h2>Top picks — reasoning</h2>
    {''.join(reasoning_items)}
  </div>

  <footer>Generated by fpl-squad-optimizer at {escape(generated_at)} &middot; not affiliated with the Premier League or Fantasy Premier League</footer>
</div>
</body>
</html>
"""


def write_html(result: SquadResult, gameweek: int, path: str, generated_at: str | None = None) -> None:
    html = render_html(result, gameweek, generated_at=generated_at)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
