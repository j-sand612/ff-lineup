# ESPN fantasy lineup report

Generates a weekly report: who to start in each slot, with the rankings behind
each call, plus which free agents are worth adding. **It never changes
anything in ESPN** — you read the report and make the moves yourself.

```bash
python run.py
```

## Before you clone this

This tool brings **your own** accounts and subscriptions to data you already
pay for. It ships no rankings, no projections, and no newsletter content - only
the code that reads them - and it caches whatever it fetches to your machine
only. You'll need:

- an ESPN fantasy account (free)
- a [Subvertadown](https://subvertadown.com) subscription for QB/K/D-ST
  projections - the tool degrades to Boris Chen ranks without one
- optionally, the [Late-Round](https://lateround.com) newsletter for the
  `--newsletter` flag

Boris Chen's tiers are public and need no account.

`config.json`, `out/`, `newsletter/`, and `saved_html/` are gitignored: they
hold your cookies, your league and rivals' team names, and copies of paid
content. Keep it that way - commit the `.example` configs, never your real one.

Not affiliated with ESPN, Subvertadown, Boris Chen, or Late-Round.

## Why no Selenium

Browser automation is only worth its weight when a site gives you nothing else.
Here, two of the three sources hand over clean data directly:

| Source | How it's read | Why |
|---|---|---|
| **ESPN** (roster, slots, scoring, free agents) | JSON API, `requests` | The endpoints the ESPN web app itself calls. Fast, stable, and read-only — no risk of a mis-click in your league. Private leagues just need two cookies. |
| **Boris Chen** (RB/WR/TE tiers) | CSV over HTTP | He publishes the tier data as plain CSV on S3. No auth, no scraping. |
| **Subvertadown** (QB/K/D-ST) | HTML scrape | No API and no data files, so scraping is the only option. Plain `requests` gets through — Cloudflare doesn't challenge it — so still no browser needed. |

Selenium would be slower, more fragile, and would need a visible browser
session, for zero benefit. The one case that *would* need a real browser is
writing lineup changes back to ESPN — which this tool deliberately doesn't do.

## Setup

```bash
pip install -r requirements.txt
cp config.example.json config.json
```

Then fill in `config.json`:

### ESPN

`league_id` is in your league URL:
`fantasy.espn.com/football/league?leagueId=`**`123456789`**

`team_id` is in your team URL (`...&teamId=3`). Leave it out and the tool uses
the first team it finds, which is probably not yours.

If your league is private you also need two cookies. In Chrome, on any ESPN
fantasy page: **F12 → Application → Cookies → espn.com**, then copy the values
of `espn_s2` and `SWID` (keep the curly braces on SWID).

These expire every few months. When they do you'll get a clear 401 message.

### Subvertadown

The weekly pages are behind the subscriber paywall, so the tool replays your
logged-in session. In Chrome, logged in to Subvertadown:

**F12 → Network → reload the page → click the top request → Request Headers →
right-click the `Cookie:` line → Copy value**

Paste that whole string into `subvertadown.cookie`.

Three modes, set with `subvertadown.mode`:

- `cookies` *(default)* — replays the cookie above. Headless, so this is the
  one that works on a schedule.
- `playwright` — opens a real Chromium with a persistent profile you log into
  once. Needs `pip install playwright && playwright install chromium`. Use this
  if Cloudflare ever starts challenging the plain request.
- `files` — parses pages you saved by hand (Ctrl+S) into `saved_html/` as
  `qb.html`, `k.html`, `dst.html`. Always works; good as a last resort.

**Verify it before trusting a report:**

```bash
python probe.py
```

This fetches all three weekly pages and prints what came back - whether the
session is logged in, what columns each table has, which position the parser
matched it to, and the first parsed rows. Raw HTML lands in `out/debug/` so a
layout change can be diagnosed exactly rather than guessed at.

Known so far, from probing the pages logged out: the **QB** and **D/ST** pages
parse correctly (the QB page even shows 8 rows for free). The **kicker** page is
fully gated - logged out it serves no ranking table at all - so its layout is
the one piece still unconfirmed until a subscriber cookie is used. Run
`probe.py` first and it will say plainly whether the kicker table parsed.

Note that **Hold is a kicker and D/ST column only** - the QB table has no Hold
field, so the Hold rule never blocks a QB pickup.

If Subvertadown can't be reached the report still runs, falling back to Boris
Chen's QB/K/DST ranks — but those are ranks, not points, so the points
threshold is skipped and the report says so.

## Your rules

```jsonc
"rules": {
  "scoring": "auto",          // auto-detected from your league's reception scoring
  "fa_threshold_pts": 1.5,    // QB/K/DST pickup must beat your guy by this much
  "fa_threshold_tiers": 0,    // RB/WR/TE: require an N-tier jump (0 = off)
  "respect_hold": true,       // never suggest dropping someone Subvertadown flags "Hold"
  "fa_limit": 250             // how deep to scan the free agent pool
}
```

## How it decides

**QB / K / D-ST** — ordered by Subvertadown's projected points for the week.
Highest projection on your roster starts. A free agent is only recommended if
they beat your best rostered option by more than `fa_threshold_pts`, and never
if Subvertadown flags your current player as a **Hold**. Anything that fails
either test still appears under "Considered, but not recommended" with the
reason, so you can overrule it.

**RB / WR / TE / FLEX** — ordered by Boris Chen tiers. Dedicated slots are
filled first, then FLEX, walking a single ordering; with dedicated slots nested
inside FLEX, that greedy fill is provably optimal, so there's no guesswork.

To compare an RB against a WR for the same flex spot, each player's positional
rank is divided by that position's replacement level — the number of players at
that position started league-wide, computed from your league's own slot counts
and team count. In a 10-team league with RB2/WR2/TE1/FLEX2 that's about RB 28,
WR 28, TE 14, which is what makes an RB12 and a TE6 comparable.

> **On Boris Chen's combined FLEX file:** he publishes one, and the tool reports
> its numbers as context, but does not decide with them. It's generated from a
> different consensus snapshot than the positional files and contradicts them —
> in the week this was built it ranked WR6 at flex #45 and WR48 at flex #42.
> The positional files are internally consistent and are what you actually read,
> so those drive the decision.

Players on IR, ruled OUT, or on bye are excluded from the lineup automatically.

## Which week, and where reports go

The week comes from ESPN's own `scoringPeriodId`, which rolls over on Tuesday
after Monday Night Football. So a Thursday run and a Sunday run in the same NFL
week agree on the week number - no clock guessing, no config to keep current.
Override it with `--week N` if you ever need to.

**Runs never overwrite each other.** Each writes:

```
out/<league>/week01-20260903-161923.md    <- one file per run, timestamped
out/<league>/latest.md                    <- mirrors the newest run
```

That matters because rankings move during the week: Boris Chen re-tiers as
expert consensus updates, and players get ruled out Saturday and Sunday
morning. Running again Sunday is the right move, and you keep the Thursday
report to compare against.

## Several leagues

Replace the `espn` block with `espn_auth` plus a `leagues` list - see
`config.multi-league.example.json`:

```jsonc
"espn_auth": { "season": 2026, "espn_s2": "...", "swid": "{...}" },
"leagues": [
  { "name": "Main League",  "league_id": 123456789, "team_id": 1 },
  { "name": "Other League", "league_id": 987654321, "team_id": 4 }
]
```

Cookies are inherited from `espn_auth`, so they're entered once; any league can
override them (or `season`) individually. `python run.py` then does every
league, each into its own `out/<league>/` folder. `--league "Other League"`
(name or id) runs just one.

Scoring format is detected per league, so a PPR league and a half-PPR league
get the right Boris Chen files in the same run. Boris Chen is fetched once per
scoring format and Subvertadown once per run, then shared - adding leagues
costs almost nothing. If one league fails (expired cookie, wrong id) the others
still finish and the failure is listed at the end.

## Late Round newsletter (optional)

JJ Zachariason's weekly "15 Transactions" email can be folded in as a second
opinion. Open the email, right-click **"View this email in your browser"**,
copy the link, and pass it once:

```bash
python run.py --newsletter "https://mailchi.mp/lateround/15-transactions-week-1-2026"
```

That parses and caches it to `newsletter/week01.json`, so for the rest of the
week a bare `--newsletter` reuses the cached copy with no link needed:

```bash
python run.py --newsletter
```

**Omit the flag and nothing changes** - no fetch, no section, byte-identical
output. That is enforced by a test, not just intended.

It is deliberately **annotation only**. The newsletter never feeds the lineup,
the points threshold, the Hold rule, or the flex ordering; it adds its own
section and tags corroborated free agents. A stale, missing, or unparseable
newsletter therefore cannot change a single recommendation - it just drops the
section and warns.

The report groups the 15 into what's actionable for *your* league:

- **Involves your roster** - he named someone you already hold
- **Available right now** - genuine free agents, with ESPN roster %
- **Already taken in your league** - he says add them, but a rival has them
- **Trade targets** - Buy/Sell, showing *which manager* holds the player

That last one needs every roster in the league, not just yours, since Buy/Sell
targets sit on other teams. The tool pulls them from the call it already makes.

Two safety details: the `?e=...` on a Mailchimp link is a **per-recipient
tracking id** that identifies your subscription, so it is stripped before
anything is fetched or stored; and if the newsletter's own week doesn't match
the report's week, you get a warning rather than silently stale advice.

Parsing is deterministic - each transaction is an `<h2>` reading
`Action Player` - so there's no language model in the loop and nothing to pay
for. If the format ever changes, it reports zero transactions and says so
instead of guessing.

## Flags

```
python run.py --check          # verify credentials and sources, no report
python run.py --newsletter URL # fold in the Late Round 15 Transactions email
python run.py --newsletter     # reuse this week's cached newsletter
python run.py --league NAME    # just one league (name or id)
python run.py --week 5         # override the week
python run.py --no-subvert     # skip Subvertadown entirely
python run.py --open           # open the report(s) when done
python run.py --debug-html     # save fetched HTML to out/debug/ for parser issues
```

## If Subvertadown changes their HTML

The parser identifies each table by its own columns (kickers have `4THD`/`CEIL`,
QBs have `PAY`/`RUY`, D/ST has `TREND`), so it survives layout shuffling. If
they rename columns it will report zero rows rather than silently guessing.
Run with `--debug-html` and the saved page will show what changed.

## Tests

```bash
python tests/test_rules.py    # parser, points threshold, Hold veto
python tests/test_names.py    # cross-source player matching
```

The name tests exist because Brian Robinson Jr. and Bijan Robinson are
different players on the same team who score 0.93 on a raw string ratio — a
naive fuzzy match recommended adding Brian while showing Bijan's RB2 ranking.
Matching now resolves exact hits across the whole player pool first and marks
those ranking rows claimed; only then is fuzzy matching tried, and it requires
the surname to match exactly and the first names to be nickname-compatible
(Josh/Joshua, Gabe/Gabriel — but not Brian/Bijan).
