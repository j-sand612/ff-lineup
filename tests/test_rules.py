"""Checks for the two rules that decide free-agent pickups:

  * a pickup must beat your current guy by more than `fa_threshold_pts`
  * a pickup is off the table if Subvertadown flags your guy as a Hold

Uses a synthetic Subvertadown page built in the real table shape, so the
parser is exercised too.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ffbot.engine import stream_free_agents  # noqa: E402
from ffbot.espn import Player  # noqa: E402
from ffbot.subvertadown import parse_table, to_keyed  # noqa: E402

KICKER_PAGE = """
<table class="sub-table -condensed">
 <thead>
  <tr><th></th><th colspan="2">Wk 1 Proj. Risk</th></tr>
  <tr><th>PLAYER</th><th>MATCHUP</th><th>WK 1</th><th>WK 2</th><th>HOLD?</th>
      <th>ERR.</th><th>4THD</th><th>MISSED</th><th>CEIL</th><th>FG</th><th>XP</th></tr>
 </thead>
 <tbody>
  <tr><td>Cameron Dicker</td><td>LAC vs. ARI</td><td>9.9</td><td>9.8</td><td>Y</td>
      <td>3.2</td><td>0.7</td><td>0.3</td><td>13.5</td><td>1.9</td><td>2.9</td></tr>
  <tr><td>Cam Little</td><td>JAC vs. CLE</td><td>9.6</td><td>7.4</td><td>-</td>
      <td>3.7</td><td>0.8</td><td>0.4</td><td>13.8</td><td>1.8</td><td>2.4</td></tr>
  <tr><td>Brandon Aubrey</td><td>DAL @ PHI</td><td>9.0</td><td>8.1</td><td>-</td>
      <td>3.1</td><td>0.6</td><td>0.3</td><td>13.0</td><td>1.7</td><td>2.6</td></tr>
  <tr><td>Jake Bates</td><td>DET vs. GB</td><td>8.2</td><td>7.9</td><td>-</td>
      <td>3.4</td><td>0.5</td><td>0.4</td><td>12.1</td><td>1.6</td><td>2.2</td></tr>
  <tr><td>Chase McLaughlin</td><td>TB @ ATL</td><td>7.4</td><td>7.0</td><td>-</td>
      <td>3.0</td><td>0.5</td><td>0.3</td><td>11.4</td><td>1.5</td><td>2.1</td></tr>
  <tr><td>Ka'imi Fairbairn</td><td>HOU vs. IND</td><td>7.0</td><td>6.8</td><td>-</td>
      <td>3.3</td><td>0.4</td><td>0.4</td><td>11.0</td><td>1.4</td><td>2.0</td></tr>
  <tr><td>Younghoe Koo</td><td>ATL vs. TB</td><td>6.5</td><td>6.2</td><td>-</td>
      <td>3.5</td><td>0.4</td><td>0.5</td><td>10.2</td><td>1.3</td><td>1.9</td></tr>
  <tr><td>Tyler Bass</td><td>BUF @ NYJ</td><td>6.1</td><td>6.0</td><td>-</td>
      <td>3.6</td><td>0.3</td><td>0.5</td><td>9.8</td><td>1.2</td><td>1.8</td></tr>
  <tr><td>Matt Gay</td><td>WSH vs. NYG</td><td>5.9</td><td>5.5</td><td>-</td>
      <td>3.8</td><td>0.3</td><td>0.6</td><td>9.1</td><td>1.1</td><td>1.7</td></tr>
  <tr><td>Matt Prater</td><td>BUF vs. MIA</td><td>5.7</td><td>9.4</td><td>Y</td>
      <td>3.9</td><td>0.3</td><td>0.6</td><td>9.0</td><td>1.1</td><td>1.6</td></tr>
 </tbody>
</table>
"""


# Subscriber pages hang expandable detail panels inside a row; those panels
# contain their own tables and must not be read as players. Also uses "WK 3"
# headers to prove mid-season weeks parse, not just week 1.
NESTED_DETAILS_PAGE = """
<table class="sub-table">
 <thead><tr><th>PLAYER</th><th>MATCHUP</th><th>WK 3</th><th>WK 4</th><th>HOLD?</th>
   <th>ERR.</th><th>4THD</th><th>CEIL</th><th>FG</th><th>XP</th></tr></thead>
 <tbody>
  <tr><td>Cameron Dicker</td><td>LAC vs. ARI</td><td>9.9</td><td>9.8</td><td>Y</td>
      <td>3.2</td><td>0.7</td><td>13.5</td><td>1.9</td><td>2.9</td></tr>
  <tr class="details"><td colspan="10">
      <table><tbody>
        <tr><td>Drive detail</td><td>vs NYJ</td><td>4.4</td><td>1.1</td><td>-</td>
            <td>0.1</td><td>0</td><td>1</td><td>0</td><td>0</td></tr>
      </tbody></table></td></tr>
  <tr><td>Cam Little</td><td>JAC vs. CLE</td><td>9.6</td><td>7.4</td><td>-</td>
      <td>3.7</td><td>0.8</td><td>13.8</td><td>1.8</td><td>2.4</td></tr>
 </tbody>
</table>
"""


def mk(name, pos, team, on_roster=True):
    from ffbot.names import player_key
    return Player(uid=player_key(name, pos, team), espn_id=hash(name) % 99999,
                  name=name, position=pos, pro_team=team, on_roster=on_roster)


def setup(my_kicker):
    sub = to_keyed(parse_table(KICKER_PAGE, "K"))
    roster = [mk(my_kicker, "K", "XXX")]
    fas = [mk(n, "K", "XXX", on_roster=False)
           for n in ("Cam Little", "Brandon Aubrey", "Jake Bates", "Younghoe Koo")]
    for p in roster + fas:
        p.sub_rank = sub.get(p.uid)
        p.bc_rank = None
    return roster, fas


def check(label, got, want):
    ok = got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: got {got!r}, want {want!r}")
    return ok


def main():
    results = []
    rows = parse_table(KICKER_PAGE, "K")
    print("Parser")
    results.append(check("rows parsed", len(rows), 10))
    results.append(check("Dicker Hold flag", rows[0].hold, True))
    results.append(check("Little Hold flag", rows[1].hold, False))
    results.append(check("top projection", rows[0].proj, 9.9))

    nested = parse_table(NESTED_DETAILS_PAGE, "K")
    results.append(check("nested detail rows ignored", len(nested), 2))
    results.append(check("mid-season 'WK 3' header found",
                         [r.proj for r in nested], [9.9, 9.6]))

    # --- gap of 2.2 pts (Gay 5.9 -> Aubrey 9.0+), current guy NOT held
    print("\nThreshold rule (my K = Matt Gay 5.9, not held)")
    roster, fas = setup("Matt Gay")
    rec, con = stream_free_agents(roster, fas, "K", threshold=1.5, respect_hold=True)
    names = [s.fa.name for s in rec]
    results.append(check("recommends the +3.7 upgrade", "Cam Little" in names, True))
    results.append(check("recommends Aubrey (+3.1)", "Brandon Aubrey" in names, True))
    results.append(check("Koo (+0.6) not recommended",
                         "Younghoe Koo" not in names, True))
    results.append(check("Koo appears as 'considered'",
                         any(s.fa.name == "Younghoe Koo" for s in con), True))

    # --- same gaps, but a 4.0 threshold should block the 3.1 gain
    print("\nThreshold rule (bar raised to 4.0)")
    roster, fas = setup("Matt Gay")
    rec, con = stream_free_agents(roster, fas, "K", threshold=4.0, respect_hold=True)
    names = [s.fa.name for s in rec]
    results.append(check("Aubrey (+3.1) now blocked", "Brandon Aubrey" not in names, True))
    results.append(check("blocked reason mentions the bar",
                         any("under your 4-pt bar" in s.blocked for s in con), True))

    # --- Hold veto: my kicker is Dicker, flagged Hold
    print("\nHold rule (my K = Cameron Dicker, HOLD = Y)")
    roster, fas = setup("Cameron Dicker")
    rec, con = stream_free_agents(roster, fas, "K", threshold=1.5, respect_hold=True)
    results.append(check("nothing recommended over a Hold", len(rec), 0))

    # --- a held kicker who is NOT the best: the veto has to do real work here
    print("\nHold rule (my K = Matt Prater 5.7, HOLD = Y, big upgrades available)")
    roster, fas = setup("Matt Prater")
    rec, con = stream_free_agents(roster, fas, "K", threshold=1.5, respect_hold=True)
    results.append(check("Hold blocks a +3.9 upgrade", len(rec), 0))
    results.append(check("blocked reason names the Hold",
                         any("HOLD" in s.blocked for s in con), True))

    roster, fas = setup("Matt Prater")
    rec, _ = stream_free_agents(roster, fas, "K", threshold=1.5, respect_hold=False)
    results.append(check("respect_hold=False lets the upgrade through",
                         "Cam Little" in [s.fa.name for s in rec], True))

    print(f"\n{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
