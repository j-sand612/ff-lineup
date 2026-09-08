"""Cross-source player matching.

The case that motivated all of this: Brian Robinson Jr. and Bijan Robinson are
different players on the same team with the same surname, and score 0.93 on a
raw string ratio. A naive fuzzy match handed Brian the free-agent slot with
Bijan's RB2 ranking attached.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ffbot.names import fuzzy_match, norm_name, player_key, team_abbrev  # noqa: E402

RANKED = ["Bijan Robinson", "Joshua Palmer", "Cameron Little", "Gabriel Davis",
          "Michael Evans", "Christopher Olave", "Marvin Harrison Jr."]


def check(label, got, want):
    ok = got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: got {got!r}, want {want!r}")
    return ok


def main():
    r = []

    print("Normalization")
    r.append(check("suffix stripped", norm_name("Marvin Harrison Jr."), "marvin harrison"))
    r.append(check("Subvertadown team suffix", norm_name("Lamar Jackson | BAL"), "lamar jackson"))
    r.append(check("punctuation", norm_name("D.K. Metcalf"), "dk metcalf"))
    r.append(check("apostrophe", norm_name("Ja'Marr Chase"), "jamarr chase"))

    print("\nD/ST identity across sources")
    r.append(check("ESPN spelling", team_abbrev("Texans D/ST"), "HOU"))
    r.append(check("Boris Chen spelling", team_abbrev("Houston Texans"), "HOU"))
    r.append(check("Subvertadown spelling", team_abbrev("Texans"), "HOU"))
    r.append(check("all three collapse to one key",
                   len({player_key("Texans D/ST", "D/ST"),
                        player_key("Houston Texans", "DST"),
                        player_key("Texans", "DST")}), 1))
    r.append(check("abbreviation aliases", (team_abbrev("JAC"), team_abbrev("WAS")), ("JAX", "WSH")))

    print("\nFuzzy matching")
    cands = {player_key(n, "RB"): n for n in RANKED}

    def match(name):
        k, _ = fuzzy_match(player_key(name, "RB"), cands)
        return cands.get(k) if k else None

    r.append(check("Brian Robinson Jr. does NOT take Bijan's row",
                   match("Brian Robinson Jr."), None))
    r.append(check("Josh -> Joshua", match("Josh Palmer"), "Joshua Palmer"))
    r.append(check("Cam -> Cameron", match("Cam Little"), "Cameron Little"))
    r.append(check("Gabe -> Gabriel", match("Gabe Davis"), "Gabriel Davis"))
    r.append(check("Mike -> Michael", match("Mike Evans"), "Michael Evans"))
    r.append(check("Chris -> Christopher", match("Chris Olave"), "Christopher Olave"))
    r.append(check("different surname never matches", match("Josh Jacobs"), None))

    print("\nClaimed rows cannot be stolen")
    claimed = {player_key("Bijan Robinson", "RB")}
    k, _ = fuzzy_match(player_key("Bijan Robinsonn", "RB"), cands, exclude=claimed)
    r.append(check("excluded key is skipped even on a near-perfect ratio", k, None))

    print(f"\n{sum(r)}/{len(r)} passed")
    return 0 if all(r) else 1


if __name__ == "__main__":
    sys.exit(main())
