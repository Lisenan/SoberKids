"""
Weekly league data: punishment tracking + fun awards.

Everything here comes from one endpoint -- GET /league/{id}/matchups/{week} --
so each week is fetched exactly once and both the zeros tracker and the awards
are computed from the same payload.

Each matchup entry has:
    roster_id, matchup_id, points        team total
    starters, starters_points            the lineup, in slot order
    players_points                       every rostered player's score (incl. bench)

OFFENSES (the punishment rule)
    zero      a started player who finished on 0.00
    negative  a started player who finished below 0.00 -- counts as a zero
    empty     an empty starting slot (Sleeper stores these as player id "0")

All three count toward a team's offense total; they stay labelled separately
so the week view can show what actually happened.
"""

import sleeper_keeper_report as core

BASE = core.BASE


def get(url):
    return core.get(url)


def played_weeks(league):
    """Regular-season weeks that have actually been played."""
    state = get(f"{BASE}/state/nfl") or {}
    settings = league.get("settings") or {}
    last_regular = max(1, (settings.get("playoff_week_start") or 15) - 1)

    if str(league.get("season")) != str(state.get("season")):
        return list(range(1, last_regular + 1))       # finished season

    if (state.get("season_type") or "regular") == "pre":
        return []
    current = state.get("week") or 1
    return list(range(1, min(current, last_regular + 1)))


def standings(league_id):
    """
    Records straight from the rosters endpoint.

    Sleeper stores points as an integer part plus a separate decimal field
    (fpts / fpts_decimal), so 1234 + 56 means 1234.56 -- they have to be
    recombined or every total reads as a whole number.
    """
    managers = core.get_managers(league_id)
    rows = []
    for r in core.get_rosters(league_id) or []:
        m = managers.get(r.get("owner_id"), {})
        st = r.get("settings") or {}

        def pts(whole, dec):
            return round((st.get(whole) or 0) + (st.get(dec) or 0) / 100, 2)

        w = st.get("wins") or 0
        l = st.get("losses") or 0
        t = st.get("ties") or 0
        played = w + l + t
        rows.append({
            "team": m.get("team") or m.get("manager") or f"Roster {r.get('roster_id')}",
            "manager": m.get("manager") or "",
            "wins": w, "losses": l, "ties": t,
            "record": f"{w}-{l}" + (f"-{t}" if t else ""),
            "pct": round((w + t * 0.5) / played, 3) if played else 0,
            "pf": pts("fpts", "fpts_decimal"),
            "pa": pts("fpts_against", "fpts_against_decimal"),
            "streak": st.get("streak") or "",
        })
    # Standard tiebreak: record first, then points for.
    rows.sort(key=lambda r: (-r["pct"], -r["wins"], -r["pf"]))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
        r["diff"] = round(r["pf"] - r["pa"], 2)
    return rows


def current_matchups(league_id, league, meta):
    """
    This week's games, whether or not they're finished.

    played_weeks() deliberately excludes the in-progress week so it never
    reports phantom zeros -- but the home page wants exactly that week, with
    whatever points are on the board so far.
    """
    state = get(f"{BASE}/state/nfl") or {}
    if str(league.get("season")) != str(state.get("season")):
        return None                       # looking at a finished season
    if (state.get("season_type") or "regular") == "pre":
        return None
    wk = state.get("week")
    if not wk:
        return None

    entries = get(f"{BASE}/league/{league_id}/matchups/{wk}") or []
    if not entries:
        return None

    pairs = {}
    for e in entries:
        info = meta.get(e.get("roster_id"))
        if not info:
            continue
        # "Yet to score" is the best proxy Sleeper gives for players left to
        # play: the matchup feed has no game clock or kickoff time, so a
        # starter on 0.00 is either still to play or has genuinely blanked.
        # Before kickoff that's everyone; by Monday night it's the zeros.
        starters = e.get("starters") or []
        spoints = e.get("starters_points") or []
        yet = 0
        for i, pid in enumerate(starters):
            if not pid or pid == "0":
                continue
            pts = spoints[i] if i < len(spoints) else None
            if pts is None or pts == 0:
                yet += 1
        pairs.setdefault(e.get("matchup_id"), []).append({
            "team": info["team"], "manager": info["manager"],
            "pts": round(e.get("points") or 0, 2),
            "yet": yet,
            "slots": len([p for p in starters if p and p != "0"]),
        })

    games = []
    for mid, side in sorted(pairs.items(), key=lambda kv: (kv[0] is None, kv[0])):
        side.sort(key=lambda x: -x["pts"])
        if len(side) == 2:
            games.append({"a": side[0], "b": side[1],
                          "margin": round(side[0]["pts"] - side[1]["pts"], 2)})
        elif side:
            games.append({"a": side[0], "b": None, "margin": 0})

    started = any(g["a"]["pts"] > 0 for g in games)
    return {"week": wk, "games": games, "started": started}


def _roster_meta(league_id):
    managers = core.get_managers(league_id)
    out = {}
    for r in core.get_rosters(league_id):
        m = managers.get(r.get("owner_id"), {})
        out[r["roster_id"]] = {
            "manager": m.get("manager") or "(no owner)",
            "team": m.get("team") or m.get("manager") or "(no owner)",
        }
    return out


def _slot_score(i, pid, spoints, ppoints):
    if i < len(spoints) and spoints[i] is not None:
        return spoints[i]
    return ppoints.get(pid)


def collect(league_id, league, players):
    """
    Returns dict with:
        weeks   [{week, teams:[...], awards:{...}}]
        season  {offenses:[...], scoring:[...]}
    """
    meta = _roster_meta(league_id)
    weeks_out = []
    # player_id -> {week: {pts, roster, started}} -- reused by the trade tracker
    # so trades cost no extra API calls.
    player_weeks = {}

    # season accumulators
    off = {}      # manager -> offense tallies
    sc = {}       # manager -> scoring tallies

    for wk in played_weeks(league):
        matchups = get(f"{BASE}/league/{league_id}/matchups/{wk}") or []
        if not matchups:
            continue
        # Not played yet -- don't report a week of phantom zeros.
        if not any((m.get("points") or 0) > 0 for m in matchups):
            continue

        week_teams = []
        scores = []           # (points, manager, team) for ranking
        top_player = None     # best STARTED player in the league this week
        bench_regret = None   # best player left on a bench

        for m in matchups:
            info = meta.get(m.get("roster_id"))
            if not info:
                continue

            starters = m.get("starters") or []
            spoints = m.get("starters_points") or []
            ppoints = m.get("players_points") or {}
            pts_total = round(m.get("points") or 0, 2)

            started_set = set(starters)
            for pid, pts in (ppoints or {}).items():
                if not pid or pid == "0" or pts is None:
                    continue
                player_weeks.setdefault(pid, {})[wk] = {
                    "pts": round(pts, 2),
                    "roster": m.get("roster_id"),
                    "started": pid in started_set,
                }

            zeros, negatives, empties = [], [], 0
            for i, pid in enumerate(starters):
                # Empty slot first: its score is often null, which would
                # otherwise look like "no result yet".
                if not pid or pid == "0":
                    empties += 1
                    continue

                pts = _slot_score(i, pid, spoints, ppoints)
                if pts is None:
                    continue

                entry = {
                    "name": core.player_display(pid, players),
                    "pos": (players.get(pid) or {}).get("position") or "",
                    "pts": round(pts, 2),
                }
                if pts == 0:
                    zeros.append(entry)
                elif pts < 0:
                    negatives.append(entry)

                if top_player is None or pts > top_player["pts"]:
                    top_player = dict(entry, team=info["team"], manager=info["manager"])

            # Bench regret: best scorer who was NOT in the lineup.
            started = set(starters)
            for pid, pts in (ppoints or {}).items():
                if pid in started or pts is None:
                    continue
                if bench_regret is None or pts > bench_regret["pts"]:
                    bench_regret = {
                        "name": core.player_display(pid, players),
                        "pos": (players.get(pid) or {}).get("position") or "",
                        "pts": round(pts, 2),
                        "team": info["team"], "manager": info["manager"],
                    }

            offenses = len(zeros) + len(negatives) + empties
            if offenses:
                week_teams.append({
                    "manager": info["manager"], "team": info["team"],
                    "zeros": zeros, "negatives": negatives, "empties": empties,
                    "total": offenses,
                })

            scores.append((pts_total, info["manager"], info["team"], m.get("matchup_id")))

            # ---- season accumulators ----
            o = off.setdefault(info["manager"], {
                "manager": info["manager"], "team": info["team"], "zeros": 0,
                "negatives": 0, "empties": 0, "total": 0, "weeks_hit": 0,
                "worst_week": None, "worst_count": 0})
            o["zeros"] += len(zeros)
            o["negatives"] += len(negatives)
            o["empties"] += empties
            o["total"] += offenses
            if offenses:
                o["weeks_hit"] += 1
                if offenses > o["worst_count"]:
                    o["worst_count"], o["worst_week"] = offenses, wk

            s = sc.setdefault(info["manager"], {
                "manager": info["manager"], "team": info["team"], "points": 0.0,
                "weeks": 0, "finishes": [], "highs": 0, "lows": 0,
                "best": None, "best_week": None, "worst": None, "worst_week": None})
            s["points"] += pts_total
            s["weeks"] += 1
            if s["best"] is None or pts_total > s["best"]:
                s["best"], s["best_week"] = pts_total, wk
            if s["worst"] is None or pts_total < s["worst"]:
                s["worst"], s["worst_week"] = pts_total, wk

        if not scores:
            continue

        # ---- weekly awards ----
        ranked = sorted(scores, key=lambda x: -x[0])
        for place, (p, mgr, team, _) in enumerate(ranked, 1):
            sc[mgr]["finishes"].append(place)
        sc[ranked[0][1]]["highs"] += 1
        sc[ranked[-1][1]]["lows"] += 1

        # Head-to-head results, for lucky / unlucky / margins.
        pairs = {}
        for p, mgr, team, mid in scores:
            if mid is None:
                continue
            pairs.setdefault(mid, []).append({"pts": p, "manager": mgr, "team": team})

        blowout = nail = unlucky = lucky = None
        for side in pairs.values():
            if len(side) != 2:
                continue
            hi, lo = sorted(side, key=lambda x: -x["pts"])
            margin = round(hi["pts"] - lo["pts"], 2)
            game = {"winner": hi["team"], "loser": lo["team"],
                    "win_pts": hi["pts"], "lose_pts": lo["pts"], "margin": margin}
            if blowout is None or margin > blowout["margin"]:
                blowout = game
            if nail is None or margin < nail["margin"]:
                nail = game
            # highest-scoring loser / lowest-scoring winner
            if unlucky is None or lo["pts"] > unlucky["pts"]:
                unlucky = {"team": lo["team"], "pts": lo["pts"], "beat_by": hi["team"]}
            if lucky is None or hi["pts"] < lucky["pts"]:
                lucky = {"team": hi["team"], "pts": hi["pts"], "over": lo["team"]}

        week_teams.sort(key=lambda t: (-t["total"], t["team"].lower()))
        weeks_out.append({
            "week": wk,
            "teams": week_teams,
            "awards": {
                "high": {"team": ranked[0][2], "pts": round(ranked[0][0], 2)},
                "low": {"team": ranked[-1][2], "pts": round(ranked[-1][0], 2)},
                "top_player": top_player,
                "bench": bench_regret,
                "blowout": blowout,
                "nail": nail,
                "unlucky": unlucky,
                "lucky": lucky,
            },
        })

    # ---- season rollups ----
    for s in sc.values():
        s["avg"] = round(s["points"] / s["weeks"], 2) if s["weeks"] else 0
        s["avg_finish"] = (round(sum(s["finishes"]) / len(s["finishes"]), 2)
                           if s["finishes"] else None)
        s["points"] = round(s["points"], 2)
        s.pop("finishes", None)

    offenses = sorted(off.values(), key=lambda t: (-t["total"], t["team"].lower()))
    scoring = sorted(sc.values(), key=lambda t: -t["avg"])

    return {"weeks": weeks_out, "offenses": offenses, "scoring": scoring,
            "player_weeks": player_weeks, "meta": meta,
            "last_week": weeks_out[-1]["week"] if weeks_out else None}
