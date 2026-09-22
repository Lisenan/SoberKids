"""
Build the league keeper board as a single static HTML page.

Reuses the keeper rules + Sleeper pulls from sleeper_keeper_report.py, then
bakes the results into docs/index.html. No server needed -- GitHub Pages (or
Netlify/Cloudflare Pages) just serves the file.

Usage
-----
    python build_site.py                       # reads league.json
    python build_site.py --league-id 123456    # override
    LEAGUE_ID=123456 python build_site.py      # for CI

Config (league.json):
    {"league_id": "123456789012345678", "site_title": "The Big League"}
"""

import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

import sleeper_keeper_report as core
import weekly as weekly_mod
import trades as trades_mod
import scorecard as scorecard_mod

OUT_DIR = Path("docs")
CONFIG = Path("league.json")
PUNISH = Path("punishments.json")


def load_punishments(offenses):
    """
    Merge the manual punishment log with the offenses computed from Sleeper.

    Sleeper can tell us who earned a punishment; only a human can say whether
    it was actually served. So `incurred` comes from the matchup data and
    `fulfilled` comes from punishments.json, keyed on manager (preferred) or
    team name.
    """
    if not PUNISH.exists():
        return {"rule": "", "log": [], "tally": [], "problem": ""}

    raw = PUNISH.read_text()
    problem = ""
    try:
        cfg = json.loads(raw)
    except json.JSONDecodeError as first:
        # A trailing comma before a closing bracket is the mistake people
        # actually make editing this on a phone, and it used to take the whole
        # ledger down silently -- which looks exactly like the entry was
        # deleted. Try repairing that one thing before giving up.
        repaired = re.sub(r",(\s*[}\]])", r"\1", raw)
        try:
            cfg = json.loads(repaired)
            print("  punishments.json had a stray trailing comma -- repaired "
                  "for this build. Tidy the file when you get a chance.",
                  file=sys.stderr)
        except json.JSONDecodeError:
            print(f"  punishments.json could not be read: {first}", file=sys.stderr)
            # Only the served counts come from this file. Earned and owed come
            # from Sleeper, so keep building those rather than blanking the tab.
            cfg = {}
            problem = (f"punishments.json has a formatting error "
                       f"({first.msg}, line {first.lineno}), so served counts "
                       f"are missing below. Nothing was lost -- fix the file "
                       f"and they come straight back.")

    by_mgr = {o["manager"].lower(): o for o in offenses}
    by_team = {o["team"].lower(): o for o in offenses}

    entries, served = [], {}
    for row in cfg.get("log") or []:
        key = (row.get("manager") or "").lower()
        target = by_mgr.get(key) or by_team.get((row.get("team") or "").lower())
        name = target["team"] if target else (row.get("team") or row.get("manager") or "?")
        n = int(row.get("count") or 1)
        done = (row.get("status") or "").lower() == "done"
        if target and done:
            served[target["manager"]] = served.get(target["manager"], 0) + n
        entries.append({
            "team": name,
            "week": row.get("week"),
            "punishment": row.get("punishment") or "",
            "status": "done" if done else "pending",
            "date": row.get("date") or "",
            "count": n,
            "note": row.get("note") or "",
            "proof": row.get("proof") or "",
        })

    entries.sort(key=lambda e: (e["status"] != "pending", -(e["week"] or 0)))

    tally = []
    for o in offenses:
        f = served.get(o["manager"], 0)
        tally.append({
            "team": o["team"], "manager": o["manager"],
            "incurred": o["total"], "fulfilled": f,
            "outstanding": max(0, o["total"] - f),
        })
    tally.sort(key=lambda t: (-t["outstanding"], -t["incurred"]))
    return {"rule": cfg.get("rule") or "", "log": entries,
            "tally": tally, "problem": problem}


def load_config():
    cfg = {}
    if CONFIG.exists():
        cfg = json.loads(CONFIG.read_text())
    return cfg


def resolve_ids(league_id):
    """
    Work out which league to use for what, from a single id.

    Keeper prices come from LAST season's draft; punishments, awards and trades
    come from the season being played. Given either id, figure out the other:

      - id is the current season -> keepers come from its previous_league_id
      - id is a finished season  -> use it for both
    """
    league = core.get(f"{core.BASE}/league/{league_id}")
    if not league:
        sys.exit(f"League {league_id} not found. Check the id.")
    state = core.get(f"{core.BASE}/state/nfl") or {}

    if str(league.get("season")) == str(state.get("season")):
        prev = league.get("previous_league_id")
        keeper_id = prev or league_id
        if not prev:
            print("  Note: no previous season linked, so keeper prices use this "
                  "season's draft.", file=sys.stderr)
        return keeper_id, league_id
    return league_id, league_id


def league_chain(league_id, limit=6):
    """
    Walk previous_league_id back through past seasons.

    Returns oldest-first list of league objects, so the Keepers tab can offer a
    board for every season the league has existed.
    """
    chain, seen = [], set()
    lid = league_id
    while lid and lid not in seen and len(chain) < limit:
        seen.add(lid)
        lg = core.get(f"{core.BASE}/league/{lid}")
        if not lg:
            break
        chain.append(lg)
        lid = lg.get("previous_league_id")
    chain.reverse()
    return chain


def keeper_board(league):
    """Keeper prices for the season AFTER this league's season."""
    lid = league["league_id"]
    rows, total_rounds = core.build_rows(lid)
    if not rows:
        return None
    season = league.get("season")
    try:
        keeper_season = str(int(season) + 1)
    except (TypeError, ValueError):
        keeper_season = ""

    rows = sorted(rows, key=core.sort_key)
    teams = {}
    for r in rows:
        key = r["manager"] or "(no owner)"
        t = teams.setdefault(key, {"manager": key,
                                   "team": r["team_name"] or key, "players": []})
        t["players"].append({
            "name": r["player"], "pos": r["position"] or "",
            "nfl": r["nfl_team"] or "", "keep": r["keeper_round"],
            "prev": r["prior_round"], "penalty": r["penalty_rounds"],
            "kept": r["kept_last_year"] == "YES",
            "waiver": r["drafted_round"] is None,
            "from": r["originally_drafted_by"] if r["traded_in"] else "",
            "basis": r["cost_basis"],
        })
    return {
        "season": season, "keeper_season": keeper_season,
        "rounds": total_rounds,
        "waiver_round": max(1, total_rounds - 2) if total_rounds else None,
        "teams": sorted(teams.values(), key=lambda t: t["team"].lower()),
    }


def build_payload(league_id, current_league_id=None):
    league = core.get(f"{core.BASE}/league/{league_id}")
    if not league:
        sys.exit(f"League {league_id} not found.")

    # One board per season the league has existed, newest last.
    boards = []
    for lg in league_chain(league_id):
        b = keeper_board(lg)
        if b:
            boards.append(b)
    if not boards:
        sys.exit("No roster data returned -- check the league id.")
    latest = boards[-1]
    boards.reverse()          # newest first for the season picker

    season = latest["season"]
    keeper_season = latest["keeper_season"]

    # ---- Weekly data: zeros + awards (current season) ---------------------
    wk_data = {"weeks": [], "offenses": [], "scoring": [],
               "player_weeks": {}, "meta": {}, "last_week": None}
    zero_year = None
    trade_list, trade_vol = [], []
    live = None
    table = []
    cards = []
    ranks = []
    zl_id = current_league_id or league_id
    zleague = league if zl_id == league_id else core.get(f"{core.BASE}/league/{zl_id}")
    if zleague:
        try:
            allp = core.load_players()
            wk_data = weekly_mod.collect(zl_id, zleague, allp)
            zero_year = zleague.get("season")
            live = weekly_mod.current_matchups(zl_id, zleague, wk_data["meta"])
            ranks = weekly_mod.standings(zl_id)
            cards, table = scorecard_mod.build(
                zl_id, allp, wk_data["player_weeks"],
                len(wk_data["weeks"]) or 1)
            trade_list, trade_vol = trades_mod.build(
                zl_id, zleague, allp, wk_data["player_weeks"], wk_data["meta"],
                weekly_mod.played_weeks(zleague), wk_data["last_week"])
        except Exception as e:  # never let the tracker break the keeper board
            print(f"  (Weekly tracker skipped: {e})", file=sys.stderr)

    return {
        "league": league.get("name") or "Keeper Board",
        "season": season,
        "keeper_season": keeper_season,
        "rounds": latest["rounds"],
        "waiver_round": latest["waiver_round"],
        "updated": dt.datetime.now(dt.timezone.utc).strftime("%b %d, %Y at %H:%M UTC"),
        "teams": latest["teams"],
        "boards": boards,
        "zero_year": zero_year,
        "zero_weeks": wk_data["weeks"],
        "zero_season": wk_data["offenses"],
        "scoring": wk_data["scoring"],
        "trades": trade_list,
        "trade_volume": trade_vol,
        "punish": load_punishments(wk_data["offenses"]),
        "repo": load_config().get("repo", ""),
        "live": live,
        "standings": ranks,
        "scorecard": cards,
        "round_table": table,
        "last_week": wk_data["last_week"],
    }


# --------------------------------------------------------------------------- #
# Template
# --------------------------------------------------------------------------- #
PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<meta name="theme-color" content="#101720">
<title>__LEAGUE__ · Keeper Board</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@500;600;700&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  :root{
    --board:#101720; --surface:#1A2432; --line:#263346;
    --chalk:#EEF3F8; --muted:#A3B1C2; --gold:#F5BE3F;
    --qb:#EC4899; --rb:#34D399; --wr:#38BDF8; --te:#FB923C; --def:#A78BFA; --k:#94A3B8;
  }
  *{box-sizing:border-box}
  /* Some in-app browsers (iOS WKWebView) paint their own white behind the page
     instead of propagating body's background, so set it on html too and give
     the wrapper full height. Without this the page renders light-on-light. */
  html{background:var(--board); color-scheme:dark; -webkit-text-size-adjust:100%}
  body{
    margin:0; background:var(--board); color:var(--chalk);
    font-family:Inter,system-ui,sans-serif; font-size:16px; line-height:1.55;
    -webkit-font-smoothing:antialiased; min-height:100vh;
  }
  .wrap{max-width:920px; margin:0 auto; padding:0 18px 72px;
    background:var(--board); min-height:100vh}

  /* ---- masthead ---- */
  header{padding:38px 0 26px; border-bottom:1px solid var(--line)}
  h1{
    font-family:'Barlow Condensed',sans-serif; font-weight:700;
    font-size:clamp(38px,8vw,62px); line-height:.95; letter-spacing:-.01em; margin:0;
  }
  .season{color:var(--gold)}
  .sub{color:var(--muted); margin:10px 0 0; max-width:60ch}
  .rules{
    display:flex; flex-wrap:wrap; gap:8px; margin-top:18px; padding:0; list-style:none;
  }
  .rules[hidden]{display:none}
  .rules li{
    font-size:13px; color:var(--muted); background:var(--surface);
    border:1px solid var(--line); border-radius:4px; padding:5px 10px;
  }
  .rules b{color:var(--chalk); font-weight:600}

  /* ---- team rail ---- */
  nav{
    position:sticky; top:0; z-index:5; background:rgba(16,23,32,.94);
    backdrop-filter:blur(8px); border-bottom:1px solid var(--line);
    margin:0 -18px; padding:10px 18px;
  }
  .rail{display:flex; gap:6px; overflow-x:auto; scrollbar-width:none}
  .rail::-webkit-scrollbar{display:none}
  .rail button{
    flex:0 0 auto; font-family:'Barlow Condensed',sans-serif; font-size:16px;
    font-weight:600; letter-spacing:.02em; color:var(--muted); cursor:pointer;
    background:transparent; border:1px solid var(--line); border-radius:999px;
    padding:6px 14px; white-space:nowrap;
  }
  .rail button:hover{color:var(--chalk); border-color:#3A4A63}
  .rail button[aria-selected="true"]{
    background:var(--chalk); border-color:var(--chalk); color:var(--board);
  }
  .rail button:focus-visible{outline:2px solid var(--gold); outline-offset:2px}

  /* ---- search ---- */
  .tools{display:flex; gap:10px; align-items:center; margin:20px 0 6px}
  input[type=search]{
    flex:1; background:var(--surface); border:1px solid var(--line); color:var(--chalk);
    border-radius:6px; padding:9px 12px; font:inherit; font-size:14px;
  }
  input[type=search]::placeholder{color:var(--muted)}
  input[type=search]:focus{outline:2px solid var(--gold); outline-offset:1px}
  .count{color:var(--muted); font-size:13px; white-space:nowrap}

  /* ---- team block ---- */
  .team{margin-top:26px}
  .team h2{
    font-family:'Barlow Condensed',sans-serif; font-size:24px; font-weight:600;
    margin:0 0 2px; letter-spacing:.01em;
  }
  .team .mgr{color:var(--muted); font-size:13px; margin:0 0 12px}

  /* ---- player rows ---- */
  .row{
    display:grid; grid-template-columns:52px 1fr auto; gap:12px; align-items:center;
    padding:9px 12px 9px 8px; border-bottom:1px solid var(--line);
  }
  .row:first-of-type{border-top:1px solid var(--line)}
  .chip{
    font-family:'Barlow Condensed',sans-serif; font-weight:700; font-size:20px;
    text-align:center; padding:5px 0; border-radius:5px;
    background:var(--surface); border:1px solid var(--line); color:var(--chalk);
  }
  .chip.premium{background:var(--gold); border-color:var(--gold); color:#241A02}
  .chip.steep{border-color:#6B551C; color:var(--gold)}
  .chip.cheap{color:var(--muted)}
  .who{min-width:0}
  .nm{display:flex; align-items:baseline; gap:7px; flex-wrap:wrap}
  .nm strong{font-weight:600; font-size:15px}
  .pos{
    font-family:'Barlow Condensed',sans-serif; font-size:13px; font-weight:600;
    padding:1px 6px; border-radius:3px; color:var(--board);
  }
  .pos.QB{background:var(--qb)} .pos.RB{background:var(--rb)}
  .pos.WR{background:var(--wr)} .pos.TE{background:var(--te)}
  .pos.DEF{background:var(--def)} .pos.K{background:var(--k)}
  .star{color:var(--gold); font-size:13px}
  .meta{color:var(--muted); font-size:12.5px; margin-top:2px}
  .basis{
    color:var(--muted); font-size:12.5px; text-align:right; white-space:nowrap;
  }
  @media (max-width:560px){
    .row{grid-template-columns:46px 1fr; row-gap:2px}
    .basis{grid-column:2; text-align:left}
  }

  /* ---- view switch ---- */
  .views{
    display:flex; gap:4px; margin:20px -18px 0; padding:0 18px;
    border-bottom:1px solid var(--line);
    overflow-x:auto; scrollbar-width:none; -webkit-overflow-scrolling:touch;
  }
  .views::-webkit-scrollbar{display:none}
  .views button{
    flex:0 0 auto; white-space:nowrap;
    font-family:'Barlow Condensed',sans-serif; font-size:20px; font-weight:600;
    background:none; border:0; border-bottom:2px solid transparent; color:var(--muted);
    padding:9px 2px; margin-right:22px; cursor:pointer; letter-spacing:.01em;
  }
  .views button[aria-selected="true"]{color:var(--chalk); border-bottom-color:var(--gold)}
  .views button:focus-visible{outline:2px solid var(--gold); outline-offset:3px}

  /* ---- zeros ---- */
  .weekpick{display:flex; gap:6px; overflow-x:auto; margin:18px 0 4px; scrollbar-width:none}
  .weekpick::-webkit-scrollbar{display:none}
  .weekpick button{
    flex:0 0 auto; font-family:'Barlow Condensed',sans-serif; font-size:15px; font-weight:600;
    background:var(--surface); border:1px solid var(--line); color:var(--muted);
    border-radius:4px; padding:5px 11px; cursor:pointer;
  }
  .weekpick button[aria-selected="true"]{background:var(--chalk); border-color:var(--chalk); color:var(--board)}
  .weekpick button:focus-visible{outline:2px solid var(--gold); outline-offset:2px}

  .standing{
    display:grid; grid-template-columns:34px 1fr auto; gap:12px; align-items:center;
    padding:10px 12px 10px 6px; border-bottom:1px solid var(--line);
  }
  .standing:first-of-type{border-top:1px solid var(--line)}
  .rank{
    font-family:'Barlow Condensed',sans-serif; font-size:19px; font-weight:700;
    color:var(--muted); text-align:center;
  }
  .standing.lead .rank{color:var(--gold)}
  .tally{
    font-family:'Barlow Condensed',sans-serif; font-size:30px; font-weight:700;
    line-height:1; text-align:right;
  }
  .tally small{
    display:block; font-family:Inter,sans-serif; font-size:11.5px; font-weight:400;
    color:var(--muted); margin-top:3px;
  }
  .offenders{margin:2px 0 0; padding:0; list-style:none; display:flex; flex-wrap:wrap; gap:6px}
  .offenders li{
    font-size:12.5px; color:var(--muted); background:var(--surface);
    border:1px solid var(--line); border-radius:4px; padding:3px 8px;
  }
  .offenders li.zero{border-color:#5A3030; color:#F1A9A9}
  .offenders li.empty{border-color:#6B551C; color:var(--gold)}
  .offenders li.neg{border-color:#3A4A63}
  .clean{color:var(--muted); padding:30px 0; text-align:center}

  .panel-h{
    font-family:'Barlow Condensed',sans-serif; font-size:23px; font-weight:600;
    margin:20px 0 0; letter-spacing:.01em;
  }
  .section-h{
    font-family:'Barlow Condensed',sans-serif; font-size:21px; font-weight:600;
    margin:28px 0 2px; letter-spacing:.01em;
  }
  .section-h .when{color:var(--muted); font-size:14px; font-weight:500; margin-left:8px}

  /* ---- live matchups ---- */
  .games{display:grid; grid-template-columns:repeat(auto-fit,minmax(250px,1fr)); gap:10px; margin-top:12px}
  .game{background:var(--surface); border:1px solid var(--line); border-radius:6px; padding:11px 13px}
  .gside{display:flex; justify-content:space-between; align-items:baseline; gap:10px; padding:3px 0}
  .gside .tm{font-size:14px}
  .gside .pt{font-family:'Barlow Condensed',sans-serif; font-size:21px; font-weight:700}
  .gside.up .tm{font-weight:600}
  .gside.up .pt{color:var(--gold)}
  .gside.down .tm, .gside.down .pt{color:var(--muted)}
  .yet{
    font-size:11.5px; color:var(--gold); border:1px solid #6B551C;
    border-radius:3px; padding:1px 5px; margin-left:5px; white-space:nowrap;
  }
  .vs{border-top:1px solid var(--line); margin:4px 0}

  /* ---- zero chart ---- */
  .zchart{margin-top:18px}
  .zrow{display:grid; grid-template-columns:132px 1fr 32px; gap:10px; align-items:center; padding:6px 0}
  .zrow .zt{font-size:14px; text-align:right; overflow:hidden; text-overflow:ellipsis; white-space:nowrap}
  /* Phones: full team name on its own line, bar underneath. Truncating names
     to "Waiver Wire W..." made the chart unreadable. */
  @media (max-width:600px){
    .zrow{
      grid-template-columns:1fr 32px;
      grid-template-areas:"name num" "bar bar";
      padding:9px 0; border-bottom:1px solid var(--line);
    }
    .zrow .zt{grid-area:name; text-align:left; font-weight:600}
    .zrow .zbar{grid-area:bar; margin-top:6px}
    .zrow .zn{grid-area:num}
    .zscale{grid-template-columns:1fr 32px !important}
    .zscale div:first-child{display:none}
  }
  .zbar{display:flex; height:22px; border-radius:3px; overflow:hidden;
    background:rgba(255,255,255,.05); box-shadow:inset 0 0 0 1px var(--line)}
  .zbar span{display:block; height:100%}
  .zbar .z{background:#C2564F}
  .zbar .n{background:#8A5A2B}
  .zbar .e{background:var(--gold)}
  .zrow .zn{font-family:'Barlow Condensed',sans-serif; font-size:19px; font-weight:700}
  .zrow.zero-free .zn{color:var(--muted)}
  .zkey{display:flex; gap:14px; margin-top:14px; color:var(--muted); font-size:12.5px; flex-wrap:wrap}
  .zkey i{display:inline-block; width:10px; height:10px; border-radius:2px; margin-right:5px; vertical-align:0}
  .zscale{display:grid; grid-template-columns:112px 1fr 30px; gap:10px; margin-top:4px}
  .zscale div:nth-child(2){
    display:flex; justify-content:space-between; color:var(--muted); font-size:11.5px;
  }

  /* ---- punishment ledger ---- */
  .ledger{margin-top:6px}
  .pent{display:grid; grid-template-columns:1fr auto; gap:10px; align-items:start;
    padding:10px 2px; border-bottom:1px solid var(--line)}
  .pent:first-child{border-top:1px solid var(--line)}
  .pent .what{font-size:14.5px}
  .pent .who{color:var(--muted); font-size:12.5px; margin-top:2px}
  .pent .note2{color:var(--muted); font-size:12.5px; margin-top:3px; font-style:italic}
  .badge{
    font-family:'Barlow Condensed',sans-serif; font-size:13px; font-weight:600;
    border-radius:4px; padding:3px 9px; white-space:nowrap;
  }
  .badge.done{background:#1E3D2F; color:#7EE0AE; border:1px solid #2E5C46}
  .badge.pending{background:#3A2E12; color:var(--gold); border:1px solid #6B551C}
  .owed{display:grid; grid-template-columns:1fr repeat(3,52px); gap:8px;
    align-items:center; padding:8px 2px; border-bottom:1px solid var(--line)}
  .owed .n{font-family:'Barlow Condensed',sans-serif; font-size:19px; font-weight:700;
    text-align:center}
  .owed .n.out{color:var(--gold)}
  .owed .n.clear{color:var(--muted)}
  .owed.head{color:var(--muted); font-size:12px; border-bottom:1px solid var(--line)}
  .owed.head span{text-align:center; font-family:'Barlow Condensed',sans-serif; font-size:13px}
  .owed.head span:first-child{text-align:left}

  /* ---- standings ---- */
  .stand{display:grid; grid-template-columns:26px 1fr 62px 62px; gap:8px;
    align-items:center; padding:9px 2px; border-bottom:1px solid var(--line)}
  .stand.head{color:var(--muted); font-size:12px; border-bottom:1px solid var(--line)}
  .stand.head span:nth-child(n+3){text-align:right}
  .stand .rk{color:var(--muted); font-family:'Barlow Condensed',sans-serif;
    font-size:17px; font-weight:700; text-align:center}
  .stand .rec{font-family:'Barlow Condensed',sans-serif; font-size:19px;
    font-weight:700; text-align:right}
  .stand .pf{text-align:right; color:var(--muted); font-size:13.5px}
  .stand.playoff .rk{color:var(--gold)}
  .cutline{border-top:2px dashed #3A4A63; margin:2px 0; padding-top:2px;
    color:var(--muted); font-size:11.5px}

  /* ---- scorecard ---- */
  .kcard{display:grid; grid-template-columns:1fr auto; gap:10px; align-items:center;
    padding:10px 2px; border-bottom:1px solid var(--line)}
  .kcard .meta{margin-top:2px}
  .vb{font-family:'Barlow Condensed',sans-serif; font-size:14px; font-weight:600;
    border-radius:4px; padding:3px 9px; white-space:nowrap}
  .vb.Steal{background:#1E3D2F; color:#7EE0AE; border:1px solid #2E5C46}
  .vb.Fair{color:var(--muted); border:1px solid var(--line)}
  .vb.Bust{background:#4A2626; color:#F1A9A9; border:1px solid #5A3030}

  /* ---- log form ---- */
  .logbtn{
    font-family:'Barlow Condensed',sans-serif; font-size:16px; font-weight:600;
    background:var(--gold); color:#241A02; border:0; border-radius:5px;
    padding:8px 15px; cursor:pointer; margin-top:14px;
  }
  .logbtn.ghost{background:transparent; color:var(--gold); border:1px solid #6B551C}
  .form{background:var(--surface); border:1px solid var(--line); border-radius:6px;
    padding:14px; margin-top:12px}
  .form label{display:block; font-size:12.5px; color:var(--muted); margin:9px 0 3px}
  .form input, .form select{
    width:100%; background:var(--board); border:1px solid var(--line); color:var(--chalk);
    border-radius:5px; padding:9px 10px; font:inherit; font-size:15px;
  }
  .form .two{display:grid; grid-template-columns:1fr 1fr; gap:10px}
  .form pre{
    background:var(--board); border:1px solid var(--line); border-radius:5px;
    padding:10px; margin:12px 0 0; font-size:12px; overflow-x:auto; color:var(--chalk);
  }
  .form .acts{display:flex; gap:8px; flex-wrap:wrap; margin-top:10px}

  /* ---- awards ---- */
  .awards{display:grid; grid-template-columns:repeat(auto-fit,minmax(232px,1fr)); gap:10px; margin-top:16px}
  .award{background:var(--surface); border:1px solid var(--line); border-radius:6px; padding:13px 14px}
  .award h3{
    font-family:'Barlow Condensed',sans-serif; font-weight:600; font-size:15px;
    color:var(--muted); margin:0 0 7px; letter-spacing:.02em;
  }
  .award .big{
    font-family:'Barlow Condensed',sans-serif; font-size:27px; font-weight:700; line-height:1.05;
  }
  .award .sub2{color:var(--muted); font-size:12.5px; margin-top:3px}
  .award.hot{border-color:#6B551C}
  .award.hot .big{color:var(--gold)}
  .lb{width:100%; border-collapse:collapse; margin-top:14px; font-size:14px}
  .lb th{
    font-family:'Barlow Condensed',sans-serif; font-weight:600; font-size:14px;
    color:var(--muted); text-align:right; padding:6px 8px; border-bottom:1px solid var(--line);
  }
  .lb th:nth-child(-n+2), .lb td:nth-child(-n+2){text-align:left}
  .lb td{padding:8px; border-bottom:1px solid var(--line)}
  .lb td.num{
    text-align:right; font-family:'Barlow Condensed',sans-serif; font-size:17px; font-weight:600;
  }
  .lb tr:first-child td{color:var(--chalk)}
  .lb .rk{color:var(--muted); width:26px}

  /* ---- trades ---- */
  .trade{background:var(--surface); border:1px solid var(--line); border-radius:6px;
    padding:14px 15px; margin-top:12px}
  .trade .head{display:flex; justify-content:space-between; align-items:baseline;
    gap:10px; margin-bottom:11px; flex-wrap:wrap}
  .trade .wk{font-family:'Barlow Condensed',sans-serif; font-size:17px; font-weight:600}
  .trade .verdict{font-size:12.5px; color:var(--muted)}
  .trade .verdict.win{color:var(--gold); font-weight:600}
  .side{display:grid; grid-template-columns:1fr auto; gap:10px; align-items:start;
    padding:9px 0; border-top:1px solid var(--line)}
  .side .tm{font-weight:600; font-size:14.5px}
  .side .got{margin:5px 0 0; padding:0; list-style:none; color:var(--muted); font-size:13px}
  .side .got li{padding:1px 0}
  .side .got .pp{color:var(--chalk)}
  .side .num{font-family:'Barlow Condensed',sans-serif; font-size:25px;
    font-weight:700; text-align:right; line-height:1}
  .side .num small{display:block; font-family:Inter,sans-serif; font-size:11px;
    font-weight:400; color:var(--muted); margin-top:3px}
  .side.lead .num{color:var(--gold)}
  .grade{
    font-family:'Barlow Condensed',sans-serif; font-size:21px; font-weight:700;
    border:1px solid var(--line); border-radius:5px; padding:1px 9px;
    margin-left:8px; vertical-align:2px;
  }
  .grade.A{background:var(--gold); border-color:var(--gold); color:#241A02}
  .grade.B{border-color:#6B551C; color:var(--gold)}
  .grade.C{color:var(--muted)}
  .grade.D{border-color:#5A3030; color:#F1A9A9}
  .grade.F{background:#5A3030; border-color:#5A3030; color:#FFD7D7}
  .paa{color:var(--muted); font-size:12.5px; margin-top:4px}
  .note{color:var(--muted); font-size:12.5px; margin:14px 0 0}
  .note.prov{
    color:var(--gold); border-left:2px solid #6B551C; padding-left:10px;
  }

  footer{
    margin-top:44px; padding-top:18px; border-top:1px solid var(--line);
    color:var(--muted); font-size:12.5px;
  }
  .empty{color:var(--muted); padding:34px 0; text-align:center}
</style>
</head>
<body>
<div class="wrap">

  <header>
    <h1>__LEAGUE__</h1>
    <p class="sub">Punishments, weekly awards, trades and keeper prices &mdash; pulled from Sleeper.</p>
    <ul class="rules" id="rules">
      <li>Drafted <b>&minus;1 round</b></li>
      <li>Kept last year <b>&minus;2 rounds</b></li>
      <li>1st rounders <b>stay in the 1st</b></li>
      <li>Waiver pickups <b>__WAIVER__</b></li>
    </ul>
  </header>

  <div class="views" role="tablist">
    <button id="vZero" role="tab" aria-selected="true">Punishments</button>
    <button id="vHome" role="tab" aria-selected="false">This week</button>
    <button id="vStand" role="tab" aria-selected="false">Standings</button>
    <button id="vAward" role="tab" aria-selected="false">Awards</button>
    <button id="vTrade" role="tab" aria-selected="false">Trades</button>
    <button id="vKeep" role="tab" aria-selected="false">Keepers</button>
  </div>

  <section id="panelHome" hidden>
    <div id="homeBody"></div>
  </section>

  <section id="panelStand" hidden>
    <div id="standBody"></div>
  </section>

  <section id="panelKeep" hidden>
    <div class="views" role="tablist" style="border-bottom:0; margin-top:14px">
      <button id="kCost" role="tab" aria-selected="true" style="font-size:17px">Costs</button>
      <button id="kCard" role="tab" aria-selected="false" style="font-size:17px">Scorecard</button>
    </div>
    <div id="keepCost">
    <h2 class="panel-h" id="keepHead">Keeper cost for __KEEPER_SEASON__</h2>
    <div class="weekpick" id="seasonPick" style="margin:10px 0 2px"></div>
    <nav>
      <div class="rail" id="rail" role="tablist"></div>
    </nav>

    <div class="tools">
      <input type="search" id="q" placeholder="Search any player in the league" aria-label="Search players">
      <span class="count" id="count"></span>
    </div>

    <main id="board"></main>
    </div>
    <div id="keepCard" hidden></div>
  </section>

  <section id="panelZero">
    <div class="views" role="tablist" style="border-bottom:0; margin-top:14px">
      <button id="zSeason" role="tab" aria-selected="true" style="font-size:17px">Season totals</button>
      <button id="zWeek" role="tab" aria-selected="false" style="font-size:17px">By week</button>
      <button id="zLedger" role="tab" aria-selected="false" style="font-size:17px">Served</button>
    </div>
    <div id="zeroBody"></div>
    <p class="note">An offense is any starter who finished on 0.00 or below,
      plus any empty lineup slot. Negative scores count the same as zeros.</p>
  </section>

  <section id="panelAward" hidden>
    <div class="views" role="tablist" style="border-bottom:0; margin-top:14px">
      <button id="aWeek" role="tab" aria-selected="true" style="font-size:17px">This week</button>
      <button id="aSeason" role="tab" aria-selected="false" style="font-size:17px">Season</button>
    </div>
    <div id="awardBody"></div>
  </section>

  <section id="panelTrade" hidden>
    <div id="tradeBody"></div>
    <p class="note">A trade is scored on points the acquired players put up in
      weeks their new team actually started them, counted from the week after
      the deal. Those points are then measured against what an average starter
      at the same position would have given you, so 30 points from a tight end
      counts for more than 30 from a wide receiver. Grades compare the two
      sides per week, which is why a side can be up in raw points and still
      graded behind. Bench points are shown but don't decide it. Draft picks
      and FAAB are listed but can't be scored on points.</p>
  </section>

  <footer>
    <div>__LEAGUE__ &middot; rosters and draft pulled from Sleeper</div>
    <div>Updated __UPDATED__</div>
  </footer>
</div>

<script>
const DATA = __DATA__;
const rail = document.getElementById('rail');
const board = document.getElementById('board');
const q = document.getElementById('q');
const count = document.getElementById('count');
let active = 'ALL';

function chipClass(keep){
  if (keep === null || keep === undefined) return 'chip';
  if (keep <= 2) return 'chip premium';   // costs a top pick
  if (keep <= 4) return 'chip steep';
  if (keep >= 10) return 'chip cheap';
  return 'chip';
}

function playerRow(p){
  const el = document.createElement('div');
  el.className = 'row';
  const keep = (p.keep === null || p.keep === undefined) ? '?' : 'R' + p.keep;
  const bits = [];
  if (p.nfl) bits.push(p.nfl);
  if (p.from) bits.push('traded from ' + p.from);
  if (p.waiver) bits.push('waiver pickup');
  el.innerHTML = `
    <div class="${chipClass(p.keep)}">${keep}</div>
    <div class="who">
      <div class="nm">
        <strong>${p.name}</strong>
        ${p.pos ? `<span class="pos ${p.pos}">${p.pos}</span>` : ''}
        ${p.kept ? '<span class="star" title="Kept last year">&#9733; kept</span>' : ''}
      </div>
      ${bits.length ? `<div class="meta">${bits.join(' &middot; ')}</div>` : ''}
    </div>
    <div class="basis">${p.basis}</div>`;
  return el;
}

function render(){
  const term = q.value.trim().toLowerCase();
  board.innerHTML = '';
  let shown = 0;

  activeBoard.teams.forEach(t => {
    if (active !== 'ALL' && active !== t.manager) return;
    const players = t.players.filter(p =>
      !term || p.name.toLowerCase().includes(term) || p.pos.toLowerCase() === term);
    if (!players.length) return;

    const sec = document.createElement('section');
    sec.className = 'team';
    sec.innerHTML = `<h2>${t.team}</h2><p class="mgr">${t.manager}</p>`;
    players.forEach(p => { sec.appendChild(playerRow(p)); shown++; });
    board.appendChild(sec);
  });

  if (!shown){
    board.innerHTML = `<p class="empty">No players match &ldquo;${q.value}&rdquo;. Try a name or a position like RB.</p>`;
  }
  count.textContent = shown + (shown === 1 ? ' player' : ' players');
}

let activeBoard = DATA.boards[0];

function buildSeasonPick(){
  const el = document.getElementById('seasonPick');
  if (!DATA.boards || DATA.boards.length < 2){ el.style.display = 'none'; return; }
  el.innerHTML = DATA.boards.map(b =>
    `<button data-s="${b.season}" aria-selected="${b === activeBoard}">${b.keeper_season}</button>`).join('');
  el.querySelectorAll('button').forEach(b => {
    b.onclick = () => {
      activeBoard = DATA.boards.find(x => String(x.season) === b.dataset.s);
      active = 'ALL';
      document.getElementById('keepHead').textContent = `Keeper cost for ${activeBoard.keeper_season}`;
      buildSeasonPick();
      rail.innerHTML = '';
      buildRail();
      render();
    };
  });
}

function buildRail(){
  const mk = (label, key) => {
    const b = document.createElement('button');
    b.textContent = label;
    b.setAttribute('role','tab');
    b.setAttribute('aria-selected', String(key === active));
    b.onclick = () => {
      active = key;
      [...rail.children].forEach(c => c.setAttribute('aria-selected','false'));
      b.setAttribute('aria-selected','true');
      render();
      window.scrollTo({top:0, behavior:'smooth'});
    };
    rail.appendChild(b);
  };
  mk('All teams','ALL');
  activeBoard.teams.forEach(t => mk(t.team, t.manager));
}

q.addEventListener('input', render);
buildSeasonPick();
buildRail();
render();

/* ---------- zeros ---------- */
const zeroBody = document.getElementById('zeroBody');
let zeroMode = 'season';
let activeWeek = (DATA.zero_weeks.length ? DATA.zero_weeks[DATA.zero_weeks.length-1].week : null);

function chips(list, cls, label){
  return list.map(p =>
    `<li class="${cls}">${p.name}${p.pos ? ' · ' + p.pos : ''}${label ? ' ' + label(p) : ''}</li>`).join('');
}

function renderSeason(){
  const data = DATA.zero_season || [];
  if (!data.length){
    zeroBody.innerHTML = '<p class="clean">No completed weeks yet this season.</p>';
    return;
  }
  const max = Math.max(1, ...data.map(t => t.total));
  const rows = data.map(t => {
    const seg = (n, cls) => n ? `<span class="${cls}" style="width:${(n / max) * 100}%"></span>` : '';
    return `<div class="zrow${t.total ? '' : ' zero-free'}" title="${t.manager}">
      <div class="zt">${t.team}</div>
      <div class="zbar">${seg(t.zeros,'z')}${seg(t.negatives,'n')}${seg(t.empties,'e')}</div>
      <div class="zn">${t.total}</div>
    </div>`;
  }).join('');

  const ticks = [];
  for (let i = 0; i <= max; i += Math.max(1, Math.ceil(max / 6))) ticks.push(`<span>${i}</span>`);

  const prov = (DATA.zero_weeks || []).find(w => w.provisional);
  const provNote = prov
    ? `<p class="note prov">Includes week ${prov.week}, still in progress — those
       totals can drop once the late games finish.</p>` : '';

  zeroBody.innerHTML = `${provNote}<div class="zchart">${rows}
    <div class="zscale"><div></div><div>${ticks.join('')}</div><div></div></div>
    <div class="zkey">
      <span><i style="background:#C2564F"></i>zeros</span>
      <span><i style="background:#8A5A2B"></i>negatives</span>
      <span><i style="background:var(--gold)"></i>empty slots</span>
    </div></div>`;
}

function renderWeeks(){
  if (!DATA.zero_weeks.length){
    zeroBody.innerHTML = '<p class="clean">No completed weeks yet this season.</p>';
    return;
  }
  const picker = DATA.zero_weeks.map(w =>
    `<button data-wk="${w.week}" aria-selected="${w.week === activeWeek}">Wk ${w.week}${w.provisional ? ' ·' : ''}</button>`).join('');
  const wk = DATA.zero_weeks.find(w => w.week === activeWeek) || DATA.zero_weeks[0];

  const live = wk.provisional
    ? `<p class="note prov">Week ${wk.week} is still being played — these are not
       final. A starter whose game has not kicked off yet also shows 0.00, so
       wait for Tuesday before anyone pays up.</p>` : '';

  let body;
  if (!wk.teams.length){
    body = '<p class="clean">Nobody started a zero in week ' + wk.week + '. Shocking.</p>';
  } else {
    body = wk.teams.map(t => {
      const total = t.total;
      const items = chips(t.zeros, 'zero', () => '0.00')
        + chips(t.negatives, 'zero', p => p.pts)
        + (t.empties ? `<li class="empty">${t.empties} empty slot${t.empties > 1 ? 's' : ''}</li>` : '');
      return `<div class="standing">
        <div class="rank">${total || '·'}</div>
        <div class="who">
          <div class="nm"><strong>${t.team}</strong></div>
          <ul class="offenders">${items}</ul>
        </div>
        <div></div>
      </div>`;
    }).join('');
  }

  zeroBody.innerHTML = `<div class="weekpick">${picker}</div>${live}${body}`;
  zeroBody.querySelectorAll('.weekpick button').forEach(b => {
    b.onclick = () => { activeWeek = Number(b.dataset.wk); renderWeeks(); };
  });
}

function renderLedger(){
  const p = DATA.punish || {tally:[], log:[]};
  const problem = p.problem
    ? `<p class="note prov">${p.problem}</p>` : '';
  if (!p.tally.length){
    zeroBody.innerHTML = problem ||
      '<p class="clean">No completed weeks yet this season.</p>';
    return;
  }
  const rows = p.tally.map(t => `<div class="owed">
      <span><strong>${t.team}</strong></span>
      <span class="n">${t.incurred}</span>
      <span class="n">${t.fulfilled}</span>
      <span class="n ${t.outstanding ? 'out' : 'clear'}">${t.outstanding}</span>
    </div>`).join('');

  const log = p.log.length ? p.log.map(e => `<div class="pent">
      <div>
        <div class="what">${e.punishment || 'Punishment'}${e.count > 1 ? ` <span style="color:var(--muted)">×${e.count}</span>` : ''}</div>
        <div class="who">${e.team}${e.week ? ' · week ' + e.week : ''}${e.date ? ' · ' + e.date : ''}
          ${e.proof ? ` · <a href="${e.proof}" style="color:var(--gold)">proof</a>` : ''}</div>
        ${e.note ? `<div class="note2">${e.note}</div>` : ''}
      </div>
      <span class="badge ${e.status}">${e.status === 'done' ? 'served' : 'owed'}</span>
    </div>`).join('')
    : '<p class="clean">Nothing logged yet. Add entries to punishments.json.</p>';

  zeroBody.innerHTML = `
    ${problem}
    ${p.rule ? `<p class="note" style="margin-top:14px">${p.rule}</p>` : ''}
    ${(DATA.zero_weeks || []).some(w => w.provisional)
      ? `<p class="note prov">Earned counts include a week still in progress.</p>` : ''}
    <div class="owed head"><span>Team</span><span>Earned</span><span>Served</span><span>Owed</span></div>
    ${rows}
    <h2 class="section-h">The log</h2>
    <div class="ledger">${log}</div>
    <button class="logbtn" id="logOpen">Log a punishment</button>
    <div id="logForm"></div>`;

  document.getElementById('logOpen').onclick = showLogForm;
}

function showLogForm(){
  const teams = (DATA.punish.tally || []).map(t =>
    `<option value="${t.manager}">${t.team}</option>`).join('');
  const wk = DATA.zero_weeks.length ? DATA.zero_weeks[DATA.zero_weeks.length-1].week : 1;
  document.getElementById('logForm').innerHTML = `<div class="form">
    <label>Who</label><select id="fTeam">${teams}</select>
    <label>Punishment</label>
    <input id="fWhat" placeholder="e.g. Waffle House hour" />
    <div class="two">
      <div><label>Week</label><input id="fWeek" type="number" value="${wk}" min="1" /></div>
      <div><label>Clears how many</label><input id="fCount" type="number" value="1" min="1" /></div>
    </div>
    <div class="two">
      <div><label>Status</label><select id="fStatus">
        <option value="done">Served</option><option value="pending">Still owed</option>
      </select></div>
      <div><label>Date</label><input id="fDate" type="date" /></div>
    </div>
    <label>Note (optional)</label><input id="fNote" placeholder="" />
    <pre id="fOut"></pre>
    <div class="acts">
      <button class="logbtn" id="fCopy">Copy entry</button>
      ${DATA.repo ? `<a class="logbtn ghost" target="_blank" rel="noopener"
        href="https://github.com/${DATA.repo}/edit/main/punishments.json">Open the file on GitHub</a>` : ''}
    </div>
    <p class="note">Copy the entry, tap through to GitHub, and paste it
      immediately after <code>&quot;log&quot;: [</code> &mdash; the comma is
      already handled. Commit, and it shows up on the next build.</p>
  </div>`;

  const ids = ['fTeam','fWhat','fWeek','fCount','fStatus','fDate','fNote'];
  const refresh = () => {
    const v = id => document.getElementById(id).value;
    const entry = {
      manager: v('fTeam'), week: Number(v('fWeek')) || null,
      punishment: v('fWhat'), status: v('fStatus'),
      date: v('fDate'), count: Number(v('fCount')) || 1,
    };
    if (v('fNote')) entry.note = v('fNote');
    // If the log already has entries, this one goes in FIRST and needs a
    // comma after it. If the log is empty it must not have one. Getting that
    // wrong is the only way to break the file, so do it here rather than
    // leaving it to whoever is pasting on a phone.
    const tail = (DATA.punish.log || []).length ? ',' : '';
    document.getElementById('fOut').textContent =
      JSON.stringify(entry, null, 2) + tail;
  };
  ids.forEach(id => document.getElementById(id).addEventListener('input', refresh));
  refresh();

  document.getElementById('fCopy').onclick = async () => {
    const txt = document.getElementById('fOut').textContent;
    try { await navigator.clipboard.writeText(txt); }
    catch (e) {
      const r = document.createRange();
      r.selectNode(document.getElementById('fOut'));
      getSelection().removeAllRanges(); getSelection().addRange(r);
    }
    document.getElementById('fCopy').textContent = 'Copied';
    setTimeout(() => { document.getElementById('fCopy').textContent = 'Copy entry'; }, 1500);
  };
}

function renderZeros(){
  if (zeroMode === 'season') renderSeason();
  else if (zeroMode === 'week') renderWeeks();
  else renderLedger();
}

const zSeason = document.getElementById('zSeason'), zWeek = document.getElementById('zWeek');
zSeason.onclick = () => zeroTab('season', zSeason);
const zLedger = document.getElementById('zLedger');
function zeroTab(mode, btn){
  zeroMode = mode;
  [zSeason, zWeek, zLedger].forEach(b => b.setAttribute('aria-selected', String(b === btn)));
  renderZeros();
}
zWeek.onclick = () => zeroTab('week', zWeek);
zLedger.onclick = () => zeroTab('ledger', zLedger);

/* ---------- awards ---------- */
const awardBody = document.getElementById('awardBody');
let awardMode = 'week';
let awardWeek = (DATA.zero_weeks.length ? DATA.zero_weeks[DATA.zero_weeks.length-1].week : null);

function card(title, big, sub, hot){
  return `<div class="award${hot ? ' hot' : ''}"><h3>${title}</h3>
    <div class="big">${big}</div><div class="sub2">${sub}</div></div>`;
}

function renderAwardWeek(){
  if (!DATA.zero_weeks.length){
    awardBody.innerHTML = '<p class="clean">No completed weeks yet this season.</p>';
    return;
  }
  const picker = DATA.zero_weeks.map(w =>
    `<button data-wk="${w.week}" aria-selected="${w.week === awardWeek}">Wk ${w.week}</button>`).join('');
  const wk = DATA.zero_weeks.find(w => w.week === awardWeek) || DATA.zero_weeks[0];
  const a = wk.awards;
  const cards = [];

  if (a.high) cards.push(card('Top score', a.high.pts, a.high.team, true));
  if (a.low)  cards.push(card('Low score', a.low.pts, a.low.team));
  if (a.top_player) cards.push(card('Top player',
    `${a.top_player.pts}`,
    `${a.top_player.name}${a.top_player.pos ? ' · ' + a.top_player.pos : ''} — ${a.top_player.team}`, true));
  if (a.bench) cards.push(card('Best benched player', a.bench.pts,
    `${a.bench.name}${a.bench.pos ? ' · ' + a.bench.pos : ''} — ${a.bench.team} left him out`));
  if (a.unlucky) cards.push(card('Unluckiest', a.unlucky.pts,
    `${a.unlucky.team} lost to ${a.unlucky.beat_by}`));
  if (a.lucky) cards.push(card('Luckiest', a.lucky.pts,
    `${a.lucky.team} beat ${a.lucky.over}`));
  if (a.blowout) cards.push(card('Biggest blowout', a.blowout.margin,
    `${a.blowout.winner} over ${a.blowout.loser}`));
  if (a.nail) cards.push(card('Closest game', a.nail.margin,
    `${a.nail.winner} over ${a.nail.loser}`));

  awardBody.innerHTML = `<div class="weekpick">${picker}</div>
    <div class="awards">${cards.join('')}</div>`;
  awardBody.querySelectorAll('.weekpick button').forEach(b => {
    b.onclick = () => { awardWeek = Number(b.dataset.wk); renderAwardWeek(); };
  });
}

function renderAwardSeason(){
  const s = DATA.scoring || [];
  if (!s.length){
    awardBody.innerHTML = '<p class="clean">No completed weeks yet this season.</p>';
    return;
  }
  const best = s.reduce((a,b) => (b.best > a.best ? b : a));
  const worst = s.reduce((a,b) => (b.worst < a.worst ? b : a));
  const byFinish = [...s].sort((a,b) => (a.avg_finish ?? 99) - (b.avg_finish ?? 99));
  const mostHigh = [...s].sort((a,b) => b.highs - a.highs)[0];

  const cards = [
    card('Best week', best.best, `${best.team} · week ${best.best_week}`, true),
    card('Worst week', worst.worst, `${worst.team} · week ${worst.worst_week}`),
    card('Best average finish', byFinish[0].avg_finish, byFinish[0].team, true),
    card('Most weekly high scores', mostHigh.highs, mostHigh.team),
  ].join('');

  const rows = s.map((t, i) => `<tr>
      <td class="rk">${i+1}</td>
      <td><strong>${t.team}</strong><div class="meta">${t.manager}</div></td>
      <td class="num">${t.avg}</td>
      <td class="num">${t.avg_finish ?? '—'}</td>
      <td class="num">${t.highs}</td>
      <td class="num">${t.lows}</td>
      <td class="num">${t.best}</td>
      <td class="num">${t.worst}</td>
    </tr>`).join('');

  awardBody.innerHTML = `<div class="awards">${cards}</div>
    <table class="lb">
      <thead><tr><th></th><th>Team</th><th>Avg pts</th><th>Avg finish</th>
        <th>Highs</th><th>Lows</th><th>Best</th><th>Worst</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

function renderAwards(){ awardMode === 'week' ? renderAwardWeek() : renderAwardSeason(); }

const vKeep = document.getElementById('vKeep'), vZero = document.getElementById('vZero');
const pKeep = document.getElementById('panelKeep'), pZero = document.getElementById('panelZero');
const vAward = document.getElementById('vAward');
const pAward = document.getElementById('panelAward');
const aWeek = document.getElementById('aWeek'), aSeason = document.getElementById('aSeason');



/* ---------- home ---------- */
const homeBody = document.getElementById('homeBody');

function awardCards(a){
  const c = [];
  if (a.high) c.push(card('Top score', a.high.pts, a.high.team, true));
  if (a.low) c.push(card('Low score', a.low.pts, a.low.team));
  if (a.top_player) c.push(card('Top player', a.top_player.pts,
    `${a.top_player.name}${a.top_player.pos ? ' · ' + a.top_player.pos : ''} — ${a.top_player.team}`, true));
  if (a.bench) c.push(card('Best benched player', a.bench.pts,
    `${a.bench.name}${a.bench.pos ? ' · ' + a.bench.pos : ''} — ${a.bench.team} left him out`));
  return c.join('');
}

function renderHome(){
  const parts = [];
  const last = DATA.zero_weeks.length ? DATA.zero_weeks[DATA.zero_weeks.length-1] : null;

  if (last){
    parts.push(`<h2 class="section-h">Week ${last.week} awards</h2>
      <div class="awards">${awardCards(last.awards)}</div>`);
    const offenders = last.teams.reduce((n,t) => n + t.total, 0);
    if (offenders){
      const worst = last.teams[0];
      const owed = (DATA.punish && DATA.punish.tally || []).reduce((n,t) => n + t.outstanding, 0);
      parts.push(`<p class="note">${offenders} punishment${offenders>1?'s':''} earned in week ${last.week} —
        ${worst.team} led with ${worst.total}.${owed ? ` ${owed} still unserved league-wide.` : ''}
        Full detail on the Punishments tab.</p>`);
    }
  }

  const live = DATA.live;
  if (live && live.games.length){
    const games = live.games.map(g => {
      if (!g.b) return `<div class="game"><div class="gside up">
        <span class="tm">${g.a.team}</span><span class="pt">${g.a.pts}</span></div>
        <div class="vs"></div><div class="gside down"><span class="tm">bye</span></div></div>`;
      const aUp = g.margin > 0 ? ' up' : '', bDown = g.margin > 0 ? ' down' : '';
      const left = s => s.yet ? `<span class="yet">${s.yet} to go</span>` : '';
      return `<div class="game">
        <div class="gside${aUp}"><span class="tm">${g.a.team} ${left(g.a)}</span><span class="pt">${g.a.pts}</span></div>
        <div class="vs"></div>
        <div class="gside${bDown}"><span class="tm">${g.b.team} ${left(g.b)}</span><span class="pt">${g.b.pts}</span></div>
      </div>`;
    }).join('');
    const totalLeft = live.games.reduce((n,g) => n + (g.a.yet||0) + (g.b ? (g.b.yet||0) : 0), 0);
    const state = live.started
      ? `in progress · ${totalLeft} starters yet to score`
      : 'not started yet';
    parts.push(`<h2 class="section-h">Week ${live.week} matchups<span class="when">${state}</span></h2>
      <div class="games">${games}</div>`);
  } else if (!last){
    parts.push('<p class="clean">The season has not started yet. Keeper prices are ready on the Keepers tab.</p>');
  }

  homeBody.innerHTML = parts.join('');
}


/* ---------- standings ---------- */
const standBody = document.getElementById('standBody');

function renderStandings(){
  const rows = DATA.standings || [];
  if (!rows.length){
    standBody.innerHTML = '<p class="clean">No games played yet.</p>';
    return;
  }
  const half = Math.ceil(rows.length / 2);
  const body = rows.map((t, i) => {
    const cut = (i === half) ? '<div class="cutline">playoff line (top half)</div>' : '';
    return cut + `<div class="stand${i < half ? ' playoff' : ''}">
      <span class="rk">${t.rank}</span>
      <span><strong>${t.team}</strong><div class="meta">${t.manager}</div></span>
      <span class="rec">${t.record}</span>
      <span class="pf">${t.pf}<div style="font-size:11.5px">${t.diff > 0 ? '+' : ''}${t.diff}</div></span>
    </div>`;
  }).join('');
  standBody.innerHTML = `<h2 class="section-h">Standings</h2>
    <div class="stand head"><span></span><span>Team</span><span>W-L</span><span>PF / diff</span></div>
    ${body}
    <p class="note">Sorted by win percentage, then points for. The dashed line
      marks the top half, not your actual playoff format.</p>`;
}

/* ---------- keeper scorecard ---------- */
function renderScorecard(){
  const cards = DATA.scorecard || [];
  const el = document.getElementById('keepCard');
  if (!cards.length){
    el.innerHTML = `<p class="clean">No keepers found for this season yet — this
      fills in once the season's draft has keepers flagged and games are played.</p>`;
    return;
  }
  const steals = cards.filter(c => c.verdict === 'Steal').length;
  const busts = cards.filter(c => c.verdict === 'Bust').length;

  const rows = cards.map(c => `<div class="kcard">
      <div>
        <div class="nm"><strong>${c.name}</strong>
          ${c.pos ? `<span class="pos ${c.pos}">${c.pos}</span>` : ''}</div>
        <div class="meta">${c.team} · kept at R${c.round} ·
          ${c.started} pts started${c.round_avg ? ` vs ${c.round_avg} for the round` : ''}
          ${c.ratio ? ` · ${c.ratio}×` : ''}</div>
      </div>
      <span class="vb ${c.verdict}">${c.verdict}</span>
    </div>`).join('');

  el.innerHTML = `<h2 class="panel-h">Did the keepers earn it?</h2>
    <p class="note" style="margin-top:6px">${cards.length} keepers this season —
      ${steals} beating their round, ${busts} falling short.</p>
    ${rows}
    <p class="note">Each keeper is measured against what other players drafted in
      the same round have produced, counting started points only. A keeper held at
      round 3 took up a 3rd-round slot, so the fair question is whether he beat a
      typical 3rd rounder.</p>`;
}

const vStand = document.getElementById('vStand');
const pStand = document.getElementById('panelStand');
const kCost = document.getElementById('kCost'), kCard = document.getElementById('kCard');

kCost.onclick = () => {
  kCost.setAttribute('aria-selected','true'); kCard.setAttribute('aria-selected','false');
  document.getElementById('keepCost').hidden = false;
  document.getElementById('keepCard').hidden = true;
};
kCard.onclick = () => {
  kCard.setAttribute('aria-selected','true'); kCost.setAttribute('aria-selected','false');
  document.getElementById('keepCost').hidden = true;
  document.getElementById('keepCard').hidden = false;
  renderScorecard();
};

/* ---------- trades ---------- */
const tradeBody = document.getElementById('tradeBody');

function renderTrades(){
  const list = DATA.trades || [];
  if (!list.length){
    tradeBody.innerHTML = '<p class="clean">No trades yet this season. Somebody make a move.</p>';
    return;
  }
  tradeBody.innerHTML = list.map(t => {
    const sides = t.sides.map((s, i) => {
      const lead = (i === 0 && t.margin >= 10) ? ' lead' : '';
      const got = s.got.map(p => {
        const v = p.paa > 0 ? `+${p.paa}` : `${p.paa}`;
        return `<li><span class="pp">${p.name}</span>${p.pos ? ' · ' + p.pos : ''} —
          ${p.started} started${p.total !== p.started ? ` (${p.total} all)` : ''}
          <span style="opacity:.75">· ${v} vs ${p.pos || 'pos'} avg</span></li>`;
      }).join('');
      const extras = [];
      if (s.picks.length) extras.push(`<li>picks: ${s.picks.join(', ')}</li>`);
      if (s.faab) extras.push(`<li>$${s.faab} FAAB</li>`);
      const g = s.grade && s.grade !== '-' ? `<span class="grade ${s.grade}">${s.grade}</span>` : '';
      const paa = (s.paa > 0 ? '+' : '') + s.paa;
      return `<div class="side${lead}">
        <div>
          <div class="tm">${s.team}${g}</div>
          <ul class="got">${got || '<li>nothing scoreable</li>'}${extras.join('')}</ul>
          <div class="paa">${paa} vs an average starter at those positions</div>
        </div>
        <div class="num">${s.started}<small>started pts</small></div>
      </div>`;
    }).join('');
    const cls = t.margin >= 10 ? 'verdict win' : 'verdict';
    const since = t.weeks_since === 1 ? '1 week ago' : `${t.weeks_since} weeks ago`;
    return `<div class="trade">
      <div class="head">
        <span class="wk">${t.week === 0 ? 'Preseason trade' : 'Week ' + t.week + ' trade'}</span>
        <span class="${cls}">${t.verdict} · ${since}</span>
      </div>
      ${sides}
    </div>`;
  }).join('') + volumeTable();
}

function volumeTable(){
  const v = DATA.trade_volume || [];
  if (!v.length) return '';
  const rows = v.map((t, i) => `<tr>
      <td class="rk">${i+1}</td>
      <td><strong>${t.team}</strong><div class="meta">${t.manager}</div></td>
      <td class="num">${t.trades}</td>
      <td class="num">${t.got}</td>
      <td class="num">${t.wins}</td>
      <td class="num">${t.evens}</td>
      <td class="num">${(t.paa > 0 ? '+' : '') + t.paa}</td>
    </tr>`).join('');
  return `<h2 style="font-family:'Barlow Condensed',sans-serif;font-size:22px;
      font-weight:600;margin:30px 0 0">Who actually trades</h2>
    <table class="lb">
      <thead><tr><th></th><th>Team</th><th>Trades</th><th>Players in</th>
        <th>Ahead</th><th>Even</th><th>Net value</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

const vTrade = document.getElementById('vTrade');
const pTrade = document.getElementById('panelTrade');
const vHome = document.getElementById('vHome');
const pHome = document.getElementById('panelHome');

function showPanel(which){
  const map = {home:[vHome,pHome], keep:[vKeep,pKeep], zero:[vZero,pZero],
               award:[vAward,pAward], trade:[vTrade,pTrade], stand:[vStand,pStand]};
  Object.entries(map).forEach(([k,[btn,panel]]) => {
    const on = (k === which);
    btn.setAttribute('aria-selected', String(on));
    panel.hidden = !on;
  });
  document.getElementById('rules').hidden = (which !== 'keep');
}
vHome.onclick = () => { showPanel('home'); renderHome(); };
vKeep.onclick = () => showPanel('keep');
vZero.onclick = () => { showPanel('zero'); renderZeros(); };
vAward.onclick = () => { showPanel('award'); renderAwards(); };
vTrade.onclick = () => { showPanel('trade'); renderTrades(); };
vStand.onclick = () => { showPanel('stand'); renderStandings(); };

aWeek.onclick = () => {
  awardMode = 'week';
  aWeek.setAttribute('aria-selected','true'); aSeason.setAttribute('aria-selected','false');
  renderAwards();
};
aSeason.onclick = () => {
  awardMode = 'season';
  aSeason.setAttribute('aria-selected','true'); aWeek.setAttribute('aria-selected','false');
  renderAwards();
};

/* land on Punishments */
showPanel('zero');
renderZeros();

</script>
</body>
</html>
"""


def render(payload):
    waiver = (f"round {payload['waiver_round']}" if payload["waiver_round"]
              else "last round \u2212 2")
    html = (PAGE
            .replace("__DATA__", json.dumps(payload))
            .replace("__LEAGUE__", payload["league"])
            .replace("__SEASON__", str(payload["season"] or ""))
            .replace("__KEEPER_SEASON__", str(payload["keeper_season"] or ""))
            .replace("__WAIVER__", waiver)
            .replace("__UPDATED__", payload["updated"]))
    return html


def main():
    ap = argparse.ArgumentParser(description="Build the static keeper board.")
    ap.add_argument("--league-id", help="League id for the season keepers are priced FROM (last season).")
    ap.add_argument("--current-league-id", help="League id for the CURRENT season (zeros tracker).")
    ap.add_argument("--out", default=str(OUT_DIR / "index.html"))
    args = ap.parse_args()

    cfg = load_config()
    league_id = (args.league_id or os.environ.get("LEAGUE_ID")
                 or cfg.get("league_id") or cfg.get("keeper_league_id"))
    current_id = (args.current_league_id or os.environ.get("CURRENT_LEAGUE_ID")
                  or cfg.get("current_league_id"))
    if not league_id:
        sys.exit("No league id. Put one in league.json, set LEAGUE_ID, or pass --league-id.")

    # One id is enough -- work out the other automatically.
    if not current_id:
        league_id, current_id = resolve_ids(league_id)
        print(f"  Keepers from league {league_id}; season data from {current_id}.",
              file=sys.stderr)

    payload = build_payload(league_id, current_id)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(payload), encoding="utf-8")

    players = sum(len(t["players"]) for t in payload["teams"])
    weeks = len(payload["zero_weeks"])
    print(f"Built {out} - {len(payload['teams'])} teams, {players} players, "
          f"{weeks} week(s) of zeros.")


if __name__ == "__main__":
    main()
