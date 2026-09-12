# League Keeper Board

A free, self-updating keeper page for a Sleeper fantasy football league.

Python pulls last season's rosters and draft from Sleeper, applies the league's
keeper rules, and writes a single static `docs/index.html`. GitHub Pages serves
it. There is no server and no database, so it costs nothing to run.

**Keeper rules baked in**

| Situation | Cost to keep |
|---|---|
| Drafted last year | 1 round earlier (R3 → R2) |
| Kept last year | 2 rounds earlier (R4 → R2) |
| Drafted in round 1 | stays round 1 |
| Waiver / free agent pickup | last round − 2 (13-round draft → R11) |

Nothing goes below round 1.

**Punishment tracker**

A second tab tracks the league's punishment rule: any player you *started* who
finished the week on 0.00. It shows a season chart, a week-by-week breakdown
naming the guilty players, and a ledger of who has actually served what.

An offense is any of these, all counted the same but labelled separately so
the week view shows what actually happened:

- **A zero** — a starter who finished on 0.00
- **A negative** — a starter below 0.00 (a QB with three picks, a DST that gets
  run over)
- **An empty slot** — nobody was set in the lineup at all

Only completed regular-season weeks are scanned, so an upcoming week never
shows up as everyone posting zeros.

**Logging served punishments.** Sleeper knows who *earned* a punishment but has
no idea whether anyone served it, so that half lives in `punishments.json`.
Edit it (directly on github.com is fine), commit, and the next build shows it.
Each entry names a `manager` (Sleeper username, most reliable) or a `team`, a
`punishment`, and a `status` of `done` or `pending`. Add `count` when one
punishment settles several offenses at once, and `proof` for a link the group
chat demanded. The Served tab then shows earned / served / owed per team, with
earned coming from Sleeper and served coming from your log.

**Awards**

A third tab tracks the fun stuff, by week and across the season.

Weekly: top score, low score, top player started, best player left on a bench,
unluckiest team (highest score that lost), luckiest (lowest score that won),
biggest blowout, closest game.

Season: best and worst single weeks, best average finish, most weekly high
scores, plus a table of average points, average weekly finish, high/low-score
counts, and each team's best and worst week.

**Trade tracker**

Every trade in the league, scored by what each side has produced since the deal
and updated weekly.

The headline number is **started points** — points the acquired players scored
in weeks their new team actually put them in the lineup, counted from the week
after the trade. Raw totals including bench points are shown alongside but
don't decide it.

That's deliberate, and it's how the 2-for-1 problem gets solved. On raw points
the side receiving two players almost always "wins", because two bench-caliber
guys outscore one starter. Started points only credit a player when he earns a
lineup spot, so a 2-for-1 wins only if both pieces genuinely play. It also
correctly penalises trading for someone who then rides your bench.

**Grades** adjust for position. Started points are measured against the league
average points per started slot at that position, so 30 from a TE (where the
bar might be 9 a week) grades better than 30 from a WR (where it might be 14).
The gap between the two sides is normalised per week, so a week-1 trade isn't
automatically the most lopsided one just because more football has happened.
A side can be up in raw points and still graded behind — the grade is relative
to what the other side got, which is the whole question a trade grade answers.

A side is only declared ahead once the per-week edge passes 3.5 points above
positional average; below that it reads as even.

**Who actually trades** ranks managers by trade count, players acquired, how
many deals they're ahead in, and net value above positional average across
every trade they've made. Draft picks and FAAB are listed on the card but can't be scored
on points. If a player is later dropped or flipped again, he stops accruing for
that team — the number reflects what you actually got out of the deal.

---

## One-time setup

1. **Create a repo** on GitHub and drop these files in it.

2. **Add your league ids.** Open `league.json`. You need two, both being the
   long number in the Sleeper URL (`sleeper.com/leagues/<id>/team`):

   - `keeper_league_id` — **last** season, where keeper prices come from
   - `current_league_id` — **this** season, for the zeros tracker

   Prefer to keep them private? Skip the file and add repo secrets named
   `LEAGUE_ID` and `CURRENT_LEAGUE_ID` instead (Settings → Secrets and
   variables → Actions).

3. **Turn on Pages.** Settings → Pages → Source: **GitHub Actions**.

4. **Run it.** Actions tab → "Build keeper board" → Run workflow.

Your board lands at `https://<your-username>.github.io/<repo-name>/`.

After that it rebuilds itself every morning, so trades and waiver moves show up
without you doing anything. You can also hit "Run workflow" any time.

## Running it locally

```bash
pip install requests
python build_site.py            # writes docs/index.html
open docs/index.html
```

`python build_site.py --league-id 123456789` overrides the config for a one-off.

## The spreadsheet version

`sleeper_keeper_report.py` is the same data as a console table plus CSV and
Excel export, with a per-player breakdown of how each keeper price was
calculated. It prompts for your username and lets you pick the league.

```bash
pip install requests pandas openpyxl
python sleeper_keeper_report.py
```

## Changing the rules

**Zeros and awards.** Both come from `collect()` in `weekly.py`, which reads
each week's matchups once. The `offenses` line is where you'd change what
counts — drop `len(negatives)` from it if the league decides a negative
shouldn't be punished like a zero. During the season it rebuilds every
morning, so Tuesday's board reflects Monday night.

## Changing the keeper rules

All the pricing lives in one function — `keeper_cost()` in
`sleeper_keeper_report.py`. The site and the spreadsheet both call it, so a
change there updates both.

## Notes

- Sleeper's read API needs no key or login, which is why this can run as a
  scheduled job with no secrets.
- "Kept last year" comes from Sleeper's own keeper flag on last season's draft
  picks. If your league entered keepers as ordinary picks instead of marking
  them as keepers, those players will price at −1 instead of −2.
- The player-name file Sleeper publishes is about 5 MB. It's cached in
  `players_nfl.json` and refreshed at most once a day. Add it to `.gitignore`
  if you don't want it committed.
