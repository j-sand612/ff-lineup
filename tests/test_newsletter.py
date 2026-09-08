"""Late-Round newsletter parsing, resolution, and the no-flag guarantee.

The fixture mirrors the real campaign markup: each transaction is an <h2>
of the form "<Action> <Player>", with the rationale in following paragraphs.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ffbot import newsletter as nl  # noqa: E402
from ffbot.engine import Analysis  # noqa: E402
from ffbot.espn import Player  # noqa: E402
from ffbot.names import player_key  # noqa: E402
from ffbot.report import render  # noqa: E402

PAGE = """
<html><body>
<h1>15 Transactions: Week 1</h1>
<p>Intro paragraph that is not a transaction.</p>
<h2>Add Malik Willis</h2>
<p>Willis is available in most leagues.</p>
<h2>Buy Luther Burden</h2>
<p>There is risk here, sure.</p>
<h2>Sell Brian Thomas</h2>
<p>Brutal opening schedule.</p>
<h2>Drop Ezekiel Elliott</h2>
<p>Roster clog.</p>
<h2>Add the Dallas Cowboys Defense</h2>
<p>Streaming option.</p>
</body></html>
"""


def mk(name, pos, team, on_roster=True):
    return Player(uid=player_key(name, pos, team), espn_id=1, name=name,
                  position=pos, pro_team=team, on_roster=on_roster)


def _short(v, n=70):
    t = repr(v)
    return t if len(t) <= n else t[:n] + f"...<{len(t)} chars>"


def check(label, got, want):
    ok = got == want
    if ok and len(repr(got)) > 70:
        print(f"  [PASS] {label}")
    else:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}: "
              f"got {_short(got)}, want {_short(want)}")
    return ok


def main():
    r = []
    print("Parsing")
    txns = nl.parse(PAGE)
    r.append(check("transaction count", len(txns), 5))
    r.append(check("actions", [t.action for t in txns],
                   ["ADD", "BUY", "SELL", "DROP", "ADD"]))
    r.append(check("h1 title is not treated as a transaction",
                   all("15 Transactions" not in t.raw_name for t in txns), True))
    r.append(check("'the' stripped from D/ST name",
                   txns[4].raw_name, "Dallas Cowboys Defense"))
    r.append(check("rationale captured", txns[0].blurb,
                   "Willis is available in most leagues."))
    r.append(check("ordering preserved", [t.order for t in txns], [1, 2, 3, 4, 5]))

    print("\nTracking id is stripped (it identifies the subscriber)")
    r.append(check("e= removed",
                   nl.strip_tracking("https://mailchi.mp/lateround/x?e=0000example"),
                   "https://mailchi.mp/lateround/x"))
    r.append(check("other params kept",
                   nl.strip_tracking("https://us20.campaign-archive.com/?u=1&id=2&e=zz"),
                   "https://us20.campaign-archive.com/?u=1&id=2"))
    print("\nWeek detection (guards against pasting a stale link)")
    r.append(check("from url", nl.week_of("", "https://x/15-transactions-week-7-2026"), 7))
    r.append(check("nonsense week ignored", nl.week_of("", "https://x/week-99"), None))

    print("\nResolution against the league-wide universe")
    universe = {}
    owners = {}
    for name, pos, team, owner in [
        ("Malik Willis", "QB", "MIA", "free agent"),
        ("Luther Burden III", "WR", "CHI", "Some Other Team"),
        ("Brian Thomas Jr.", "WR", "JAX", "YOUR TEAM"),
        ("Cowboys D/ST", "DST", "DAL", "free agent"),
    ]:
        p = mk(name, pos, team)
        universe[p.uid] = p
        owners[p.uid] = owner
    nl.resolve(txns, universe, owners)
    got = {t.raw_name: (t.player.name if t.player else None) for t in txns}
    r.append(check("suffix tolerated (Burden -> Burden III)",
                   got["Luther Burden"], "Luther Burden III"))
    r.append(check("suffix tolerated (Thomas -> Thomas Jr.)",
                   got["Brian Thomas"], "Brian Thomas Jr."))
    r.append(check("D/ST phrasing resolved",
                   got["Dallas Cowboys Defense"], "Cowboys D/ST"))
    r.append(check("unrostered name stays unresolved",
                   got["Ezekiel Elliott"], None))
    r.append(check("owner recorded for trade target",
                   [t.owner for t in txns if t.raw_name == "Luther Burden"][0],
                   "Some Other Team"))

    print("\nNo-flag guarantee: an empty newsletter changes nothing")
    base = dict(week=1, scoring="half", league_name="L", team_name="T")
    a_empty = Analysis(**base)
    a_none = Analysis(**base, newsletter=[])
    src = {"Boris Chen": "half tiers"}
    r.append(check("identical render with no newsletter",
                   render(a_empty, src), render(a_none, src)))
    r.append(check("no 'Late Round' section appears",
                   "Late Round" in render(a_empty, src), False))
    r.append(check("section numbering unchanged (Data notes stays absent)",
                   "## 5. Considered, but not recommended" in render(a_empty, src), True))

    a_with = Analysis(**base, newsletter=txns, newsletter_source="test")
    with_md = render(a_with, src)
    r.append(check("newsletter section appears when supplied",
                   "## 6. Late Round - 15 Transactions" in with_md, True))
    r.append(check("sections 1-5 byte-identical with and without newsletter",
                   render(a_empty, src).split("## 6.")[0].split("## 5.")[0],
                   with_md.split("## 6.")[0].split("## 5.")[0]))

    print(f"\n{sum(r)}/{len(r)} passed")
    return 0 if all(r) else 1


if __name__ == "__main__":
    sys.exit(main())
