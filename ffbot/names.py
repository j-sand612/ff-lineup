"""Name normalization and cross-source player matching.

The three sources spell players differently:
    ESPN          "Marvin Harrison Jr."   "Texans D/ST"
    Boris Chen    "Marvin Harrison Jr."   "Houston Texans"
    Subvertadown  "Marvin Harrison | ARI" "Texans vs. Browns"

Everything here funnels into a normalized key so those line up.
"""
from __future__ import annotations

import difflib
import re
import unicodedata

# ESPN proTeamId -> abbreviation
PRO_TEAM_ID = {
    0: "FA", 1: "ATL", 2: "BUF", 3: "CHI", 4: "CIN", 5: "CLE", 6: "DAL",
    7: "DEN", 8: "DET", 9: "GB", 10: "TEN", 11: "IND", 12: "KC", 13: "LV",
    14: "LAR", 15: "MIA", 16: "MIN", 17: "NE", 18: "NO", 19: "NYG", 20: "NYJ",
    21: "PHI", 22: "ARI", 23: "PIT", 24: "LAC", 25: "SF", 26: "SEA", 27: "TB",
    28: "WSH", 29: "CAR", 30: "JAX", 33: "BAL", 34: "HOU",
}

# canonical abbrev -> (city, nickname)
TEAMS = {
    "ARI": ("Arizona", "Cardinals"), "ATL": ("Atlanta", "Falcons"),
    "BAL": ("Baltimore", "Ravens"), "BUF": ("Buffalo", "Bills"),
    "CAR": ("Carolina", "Panthers"), "CHI": ("Chicago", "Bears"),
    "CIN": ("Cincinnati", "Bengals"), "CLE": ("Cleveland", "Browns"),
    "DAL": ("Dallas", "Cowboys"), "DEN": ("Denver", "Broncos"),
    "DET": ("Detroit", "Lions"), "GB": ("Green Bay", "Packers"),
    "HOU": ("Houston", "Texans"), "IND": ("Indianapolis", "Colts"),
    "JAX": ("Jacksonville", "Jaguars"), "KC": ("Kansas City", "Chiefs"),
    "LAC": ("Los Angeles", "Chargers"), "LAR": ("Los Angeles", "Rams"),
    "LV": ("Las Vegas", "Raiders"), "MIA": ("Miami", "Dolphins"),
    "MIN": ("Minnesota", "Vikings"), "NE": ("New England", "Patriots"),
    "NO": ("New Orleans", "Saints"), "NYG": ("New York", "Giants"),
    "NYJ": ("New York", "Jets"), "PHI": ("Philadelphia", "Eagles"),
    "PIT": ("Pittsburgh", "Steelers"), "SEA": ("Seattle", "Seahawks"),
    "SF": ("San Francisco", "49ers"), "TB": ("Tampa Bay", "Buccaneers"),
    "TEN": ("Tennessee", "Titans"), "WSH": ("Washington", "Commanders"),
}

# every spelling anyone uses -> canonical abbrev
_ALIAS = {
    "JAC": "JAX", "WAS": "WSH", "LA": "LAR", "STL": "LAR", "SD": "LAC",
    "OAK": "LV", "LVR": "LV", "GNB": "GB", "KAN": "KC", "NWE": "NE",
    "NOR": "NO", "SFO": "SF", "TAM": "TB", "ARZ": "ARI", "BLT": "BAL",
    "CLV": "CLE", "HST": "HOU", "WFT": "WSH",
}
for _ab, (_city, _nick) in TEAMS.items():
    _ALIAS[_ab] = _ab
    _ALIAS[_nick.upper()] = _ab
    _ALIAS[f"{_city} {_nick}".upper()] = _ab
_ALIAS["FOOTBALL TEAM"] = "WSH"
_ALIAS["WASHINGTON FOOTBALL TEAM"] = "WSH"

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def team_abbrev(raw: str | int | None) -> str | None:
    """Map any team spelling (id, abbrev, nickname, full name) to canonical abbrev."""
    if raw is None:
        return None
    if isinstance(raw, int):
        return PRO_TEAM_ID.get(raw)
    s = re.sub(r"[^A-Za-z0-9 ]", " ", str(raw)).strip()
    s = re.sub(r"\s+", " ", s).upper()
    if not s:
        return None
    if s in _ALIAS:
        return _ALIAS[s]
    # strip trailing "D/ST", "DST", "DEFENSE"
    s2 = re.sub(r"\b(D ST|DST|DEFENSE|D)\b", "", s).strip()
    if s2 in _ALIAS:
        return _ALIAS[s2]
    # last word is usually the nickname ("Houston Texans" -> "TEXANS")
    parts = s2.split()
    if parts and parts[-1] in _ALIAS:
        return _ALIAS[parts[-1]]
    if len(parts) >= 2 and " ".join(parts[-2:]) in _ALIAS:
        return _ALIAS[" ".join(parts[-2:])]
    return None


def norm_name(name: str) -> str:
    """Normalize a person's name to a comparison key."""
    if not name:
        return ""
    s = unicodedata.normalize("NFKD", str(name))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.split("|")[0]                      # "Lamar Jackson | BAL"
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"[.'`’-]", "", s)       # D.K. -> DK, Ja'Marr -> JaMarr
    s = re.sub(r"[^A-Za-z ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip().lower()
    toks = [t for t in s.split() if t not in _SUFFIXES]
    return " ".join(toks)


def is_dst_name(name: str) -> bool:
    s = (name or "").upper()
    return bool(re.search(r"\b(D/ST|DST|DEFENSE)\b", s)) or team_abbrev(name) is not None


def player_key(name: str, position: str, team: str | int | None = None) -> str:
    """The key both sides of a match are reduced to.

    D/ST collapses to the team ("dst:HOU"); everyone else is name+position,
    which is enough to separate the two Josh Allens.
    """
    pos = (position or "").upper().replace("/", "").replace("D ST", "DST")
    if pos in ("DST", "DEF", "D"):
        ab = team_abbrev(team) or team_abbrev(name)
        return f"dst:{ab}" if ab else f"dst:{norm_name(name)}"
    return f"{pos}:{norm_name(name)}"


def _first_names_compatible(a: str, b: str) -> bool:
    """Is one first name plausibly a nickname for the other?

    Accepts Josh/Joshua, Cam/Cameron, Gabe/Gabriel, Mike/Michael. Rejects
    Brian/Bijan -- two different people who happen to score 0.93 on a raw
    string ratio, and who really do share a surname and a team.
    """
    if a == b:
        return True
    if a.startswith(b) or b.startswith(a):
        return True
    # a short nickname that isn't a clean prefix (Gabe/Gabriel, Mike/Michael)
    if min(len(a), len(b)) <= 4 and a[:2] == b[:2]:
        return True
    return False


def fuzzy_match(key: str, candidates: dict, cutoff: float = 0.70, exclude=()):
    """Close-enough name matching within one position.

    Surnames must match exactly and first names must be nickname-compatible.
    Those two structural checks do the discriminating; the string ratio is only
    a loose backstop, since "Cam Little" vs "Cameron Little" scores just 0.83.
    `exclude` holds keys already claimed by an exact match, so a near-miss can
    never steal another player's row.

    Returns (matched_key, score) or (None, 0.0).
    """
    if ":" not in key:
        return None, 0.0
    pos, name = key.split(":", 1)
    if pos == "dst" or not name:
        return None, 0.0

    parts = name.split()
    surname, first = parts[-1], parts[0]
    best, best_score = None, 0.0
    for k in candidates:
        if k in exclude or not k.startswith(pos + ":"):
            continue
        cand = k.split(":", 1)[1]
        cparts = cand.split()
        if not cparts or cparts[-1] != surname:
            continue
        if not _first_names_compatible(first, cparts[0]):
            continue
        score = difflib.SequenceMatcher(None, name, cand).ratio()
        if score >= cutoff and score > best_score:
            best, best_score = k, score
    return best, best_score
