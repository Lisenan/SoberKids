"""
Keeper scorecard.

The keeper board is a forecast: here is what this player will cost you. This is
the retrospective half -- now that the season is under way, did the keeper earn
the round he cost?

How value is judged
-------------------
Each keeper is compared against what OTHER players drafted in the same round
have produced this season. That's the honest comparison: a keeper held at round
3 occupied a 3rd-round slot, so the question is whether he beat a typical 3rd
rounder, not whether he scored a lot in the abstract.

    ratio >= 1.25   Steal   -- beat his round comfortably
    0.75 - 1.25     Fair    -- about what the round returns
    ratio <= 0.75   Bust    -- the round was worth more than he was

Production counts STARTED points only, same as the trade tracker: a keeper
riding your bench did not earn his price.

Keepers are identified by Sleeper's is_keeper flag on this season's draft
picks, which is the same flag the keeper board uses to apply the -2 penalty.
"""

import sleeper_keeper_report as core

BASE = core.BASE


def _production(pid, player_weeks):
    started = total = 0.0
    weeks = 0
    for rec in (player_weeks.get(pid) or {}).values():
        total += rec["pts"]
        if rec["started"]:
            started += rec["pts"]
            weeks += 1
    return round(started, 2), round(total, 2), weeks


def build(league_id, players, player_weeks, weeks_played):
    """Returns (cards, round_table) -- one card per keeper, plus round averages."""
    pick_map, _draft = core.get_draft_pick_map(league_id)
    if not pick_map:
        return [], []
    managers = core.get_managers(league_id)

    # What each round has actually returned this season.
    by_round = {}
    for pid, info in pick_map.items():
        rnd = info.get("round")
        if not rnd:
            continue
        started, _tot, _w = _production(pid, player_weeks)
        by_round.setdefault(rnd, []).append(started)
    round_avg = {r: round(sum(v) / len(v), 2) for r, v in by_round.items() if v}

    cards = []
    for pid, info in pick_map.items():
        if not info.get("is_keeper"):
            continue
        rnd = info.get("round")
        started, total, wks = _production(pid, player_weeks)
        base = round_avg.get(rnd, 0)
        ratio = round(started / base, 2) if base else None

        if ratio is None:
            verdict = "—"
        elif ratio >= 1.25:
            verdict = "Steal"
        elif ratio <= 0.75:
            verdict = "Bust"
        else:
            verdict = "Fair"

        mgr = managers.get(info.get("drafted_by_user"), {})
        cards.append({
            "name": core.player_display(pid, players),
            "pos": (players.get(pid) or {}).get("position") or "",
            "team": mgr.get("team") or mgr.get("manager") or "",
            "manager": mgr.get("manager") or "",
            "round": rnd,
            "started": started,
            "total": total,
            "weeks_started": wks,
            "round_avg": base,
            "ratio": ratio,
            "verdict": verdict,
            "per_week": round(started / weeks_played, 2) if weeks_played else 0,
        })

    order = {"Steal": 0, "Fair": 1, "Bust": 2, "—": 3}
    cards.sort(key=lambda c: (order[c["verdict"]], -(c["ratio"] or 0)))

    table = [{"round": r, "avg": round_avg[r], "n": len(by_round[r])}
             for r in sorted(round_avg)]
    return cards, table
