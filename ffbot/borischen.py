"""Boris Chen weekly tiers (RB/WR/TE/FLEX, plus QB/K/DST as a cross-check).

Published as plain CSV on S3, no auth, refreshed through the week:
    weekly-<POS>.csv            standard
    weekly-<POS>-PPR.csv        full PPR      (RB/WR/TE/FLX only)
    weekly-<POS>-HALF.csv       half PPR      (RB/WR/TE/FLX only)

FLX is the cross-position flex list, exposed via `flex_lookup` for display
only. It is generated from a different consensus snapshot than the positional
files and contradicts them, so the engine ranks flex candidates from the
positional files instead (see engine.build_flex_values).
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass

import requests

BASE = "https://s3-us-west-1.amazonaws.com/fftiers/out/"
SUFFIX = {"standard": "", "half": "-HALF", "ppr": "-PPR"}
# QB/K/DST are only published in the scoring-agnostic (standard) file
POS_HAS_SCORING_VARIANTS = {"RB", "WR", "TE", "FLX"}


@dataclass
class Rank:
    name: str
    position: str
    rank: int
    tier: int
    avg_rank: float
    best_rank: int
    worst_rank: int
    std_dev: float
    matchup: str = ""

    @property
    def tier_label(self) -> str:
        return f"Tier {self.tier}"


def _url(pos: str, scoring: str) -> str:
    sfx = SUFFIX.get(scoring, "") if pos in POS_HAS_SCORING_VARIANTS else ""
    return f"{BASE}weekly-{pos}{sfx}.csv"


def fetch_position(pos: str, scoring: str = "half", session=None, timeout=20) -> dict[str, Rank]:
    """Return {player_key: Rank} for one position. Empty dict if unavailable."""
    from .names import player_key

    sess = session or requests
    url = _url(pos, scoring)
    try:
        resp = sess.get(url, timeout=timeout)
        resp.raise_for_status()
    except Exception:
        return {}

    out: dict[str, Rank] = {}
    for row in csv.DictReader(io.StringIO(resp.text)):
        name = (row.get("Player.Name") or "").strip()
        if not name:
            continue
        # FLX rows carry "Matchup" where positional rows carry "Position"
        row_pos = (row.get("Position") or "").strip().upper()
        matchup = (row.get("Matchup") or "").strip()
        effective_pos = row_pos or pos
        try:
            r = Rank(
                name=name,
                position=effective_pos,
                rank=int(float(row["Rank"])),
                tier=int(float(row.get("Tier") or 99)),
                avg_rank=float(row.get("Avg.Rank") or 999),
                best_rank=int(float(row.get("Best.Rank") or 999)),
                worst_rank=int(float(row.get("Worst.Rank") or 999)),
                std_dev=float(row.get("Std.Dev") or 0),
                matchup=matchup,
            )
        except (ValueError, KeyError):
            continue
        key_pos = "DST" if pos == "DST" else (effective_pos if pos != "FLX" else "")
        if pos == "FLX":
            # FLX rows don't state position; key by name only and resolve later
            out[f"FLX:{_norm(name)}"] = r
        else:
            out[player_key(name, key_pos, name if pos == "DST" else None)] = r
    return out


def _norm(name: str) -> str:
    from .names import norm_name
    return norm_name(name)


def fetch_all(scoring: str = "half", session=None) -> dict[str, dict[str, Rank]]:
    """Fetch every position we care about. Returns {'RB': {...}, ..., 'FLX': {...}}."""
    return {p: fetch_position(p, scoring, session) for p in
            ("QB", "RB", "WR", "TE", "K", "DST", "FLX")}


def flex_lookup(flx: dict[str, Rank], name: str) -> Rank | None:
    return flx.get(f"FLX:{_norm(name)}")
