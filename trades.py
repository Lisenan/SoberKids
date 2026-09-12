"""
Trade tracker.

Finds every trade in the league and scores it by what each side has actually
produced SINCE the trade, updating each week and locking in at the end of the
regular season.

Where the data comes from
-------------------------
GET /league/{id}/transactions/{week}  -- every add/drop/trade for that week.
A trade looks like:

    {"type": "trade", "status": "complete", "leg": 3,
     "adds": {"4034": 2, "6797": 1},     player_id -> roster that RECEIVED him
     "drops": {"4034": 1, "6797": 2},    player_id -> roster that GAVE him up
     "draft_picks": [...], "waiver_budget": [...]}

Per-player weekly points come from the matchup data the weekly module already
pulled, so scoring trades costs no extra API calls.

Scoring a trade
---------------
Two numbers per side, both counted from the week AFTER the trade:

  started   points the acquired players scored in weeks their new team
            actually STARTED them. This is the headline number.
  total     every point they scored, bench included.

`started` is the fairer verdict, especially for 2-for-1s: you can only start so
many players, so the extra piece only counts when it earns a lineup spot. A guy
who rides the bench contributes nothing, which is the honest answer to "did
this trade help me?".

Draft picks and FAAB in a trade can't be scored on points, so they're recorded
and shown on the card but left out of the totals.
"""

import sleeper_keeper_report as core

BASE = core.BASE


def get(url):
    return core.get(url)


def fetch_trades(league_id, weeks):
    """All completed trades, oldest first. `weeks` is the list of scanned weeks."""
    out = []
    # Week 0 catches offseason / pre-week-1 trades.
    for wk in [0] + list(weeks):
        txns = get(f"{BASE}/league/{league_id}/transactions/{wk}") or []
        for t in txns:
            if t.get("type") != "trade" or t.get("status") != "complete":
                continue
            out.append({
                "id": t.get("transaction_id"),
                "week": wk,
                "created": t.get("status_updated") or t.get("created"),
                "adds": t.get("adds") or {},
                "drops": t.get("drops") or {},
                "roster_ids": t.get("roster_ids") or [],
                "picks": t.get("draft_picks") or [],
                "faab": t.get("waiver_budget") or [],
            })
    out.sort(key=lambda t: (t["week"], t["created"] or 0))
    return out


def positional_baselines(player_weeks, players):
    """
    League average points per STARTED slot, by position.

    This is what makes a trade grade meaningful: 30 points from a TE in a league
    where tight ends average 9 a week is a very different asset from 30 points
    at WR where the average is 14. Bench weeks are excluded -- we want the bar
    for a startable player at that position, not for everyone rostered.
    """
    tot, cnt = {}, {}
    for pid, wks in player_weeks.items():
        pos = (players.get(pid) or {}).get("position")
        if not pos:
            continue
        for rec in wks.values():
            if not rec["started"]:
                continue
            tot[pos] = tot.get(pos, 0.0) + rec["pts"]
            cnt[pos] = cnt.get(pos, 0) + 1
    return {pos: round(tot[pos] / cnt[pos], 2) for pos in tot if cnt[pos]}


def grade_for(per_week):
    """Letter grade from a side's per-week edge in points above positional average."""
    if per_week >= 8:   return "A"
    if per_week >= 3.5: return "B"
    if per_week > -3.5: return "C"
    if per_week > -8:   return "D"
    return "F"


def _score(pid, roster_id, after_week, player_weeks):
    """Points this player produced for `roster_id` in weeks after the trade."""
    started = total = 0.0
    weeks_started = 0
    for wk, rec in (player_weeks.get(pid) or {}).items():
        if wk <= after_week:
            continue
        if rec["roster"] != roster_id:
            continue          # dropped or traded on -- stops counting
        total += rec["pts"]
        if rec["started"]:
            started += rec["pts"]
            weeks_started += 1
    return round(started, 2), round(total, 2), weeks_started


def build(league_id, league, players, player_weeks, meta, weeks, last_week):
    trades = fetch_trades(league_id, weeks)
    if not trades:
        return [], []

    baselines = positional_baselines(player_weeks, players)
    out = []
    volume = {}
    for t in trades:
        # Group what each roster received.
        sides = {}
        for pid, rid in t["adds"].items():
            sides.setdefault(rid, {"roster_id": rid, "got": [], "picks": [], "faab": 0})
            s_pts, tot, wks = _score(pid, rid, t["week"], player_weeks)
            pos = (players.get(pid) or {}).get("position") or ""
            base = baselines.get(pos, 0)
            # Value above what an average starter at that position would have
            # given you over the same number of starts.
            paa = round(s_pts - wks * base, 2)
            sides[rid]["got"].append({
                "name": core.player_display(pid, players),
                "pos": pos, "started": s_pts, "total": tot,
                "weeks_started": wks, "paa": paa, "base": base,
            })

        for rid in t["roster_ids"]:
            sides.setdefault(rid, {"roster_id": rid, "got": [], "picks": [], "faab": 0})

        for p in t["picks"]:
            rid = p.get("owner_id")
            if rid in sides:
                sides[rid]["picks"].append(
                    f"{p.get('season','')} round {p.get('round','?')}")
        for f in t["faab"]:
            rid = f.get("receiver")
            if rid in sides:
                sides[rid]["faab"] += f.get("amount") or 0

        rows = []
        for rid, side in sides.items():
            info = meta.get(rid, {"team": f"Roster {rid}", "manager": ""})
            rows.append({
                "team": info["team"], "manager": info["manager"],
                "got": sorted(side["got"], key=lambda p: -p["started"]),
                "picks": side["picks"], "faab": side["faab"],
                "started": round(sum(p["started"] for p in side["got"]), 2),
                "total": round(sum(p["total"] for p in side["got"]), 2),
                "paa": round(sum(p["paa"] for p in side["got"]), 2),
                "count": len(side["got"]),
            })
        rows.sort(key=lambda r: -r["paa"])

        weeks_since = max(0, (last_week or 0) - t["week"])

        # Verdict + grades from points above positional average, normalised per
        # week so an early trade isn't automatically the most lopsided one.
        verdict, margin = "Too early to call", 0
        if len(rows) >= 2 and weeks_since > 0:
            margin = round(rows[0]["paa"] - rows[1]["paa"], 2)
            per_week = margin / weeks_since
            for i, r in enumerate(rows):
                edge = (r["paa"] - max(x["paa"] for j, x in enumerate(rows) if j != i))
                r["grade"] = grade_for(edge / weeks_since)
            verdict = "Even so far" if per_week < 3.5 else f"{rows[0]['team']} ahead"
        else:
            for r in rows:
                r["grade"] = "-"
            if len(rows) < 2:
                verdict = "Incomplete data"

        for r in rows:
            v = volume.setdefault(r["team"], {
                "team": r["team"], "manager": r["manager"], "trades": 0,
                "got": 0, "wins": 0, "evens": 0, "losses": 0, "paa": 0.0})
            v["trades"] += 1
            v["got"] += r["count"]
            v["paa"] += r["paa"]
            if weeks_since > 0 and len(rows) >= 2:
                if r is rows[0] and margin / weeks_since >= 3.5:
                    v["wins"] += 1
                elif margin / weeks_since < 3.5:
                    v["evens"] += 1
                else:
                    v["losses"] += 1

        out.append({
            "week": t["week"],
            "sides": rows,
            "verdict": verdict,
            "margin": margin,
            "weeks_since": weeks_since,
        })

    out.sort(key=lambda t: -t["week"])
    for v in volume.values():
        v["paa"] = round(v["paa"], 2)
    vol = sorted(volume.values(), key=lambda v: (-v["trades"], -v["paa"]))
    return out, vol
