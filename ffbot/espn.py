"""ESPN fantasy read-only client.

Uses the same JSON endpoints the ESPN web app calls. Read-only: this tool
reports what to do, it never writes a lineup, so no browser automation and no
risk of clicking the wrong button in your league.

Private leagues need two cookies from your logged-in browser: `espn_s2` and
`SWID`. See README for how to grab them.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

import requests

HOST = "https://lm-api-reads.fantasy.espn.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

POSITION_BY_ID = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "DST"}
SLOT_NAME = {
    0: "QB", 1: "TQB", 2: "RB", 3: "RB/WR", 4: "WR", 5: "WR/TE", 6: "TE",
    7: "OP", 8: "DT", 9: "DE", 10: "LB", 11: "DL", 12: "CB", 13: "S",
    14: "DB", 15: "DP", 16: "D/ST", 17: "K", 18: "P", 19: "HC",
    20: "BE", 21: "IR", 22: "?", 23: "FLEX", 24: "EDR",
}
# which real positions may fill a given lineup slot
SLOT_ELIGIBLE = {
    0: {"QB"}, 2: {"RB"}, 4: {"WR"}, 6: {"TE"}, 16: {"DST"}, 17: {"K"},
    23: {"RB", "WR", "TE"}, 3: {"RB", "WR"}, 5: {"WR", "TE"},
    7: {"QB", "RB", "WR", "TE"},
}
STARTING_SLOTS = [0, 2, 4, 6, 23, 3, 5, 7, 16, 17]
BENCH_SLOTS = {20, 21}
OUT_STATUSES = {"OUT", "INJURY_RESERVE", "SUSPENSION", "NOT_ACTIVE"}


@dataclass
class Player:
    uid: str
    espn_id: int
    name: str
    position: str
    pro_team: str
    lineup_slot: int = 20
    eligible_slots: list[int] = field(default_factory=list)
    injury_status: str = "ACTIVE"
    espn_proj: float | None = None
    percent_owned: float | None = None
    on_roster: bool = True
    fa_status: str = ""
    # filled in later by the engine
    bc_rank = None
    sub_rank = None
    match_note: str = ""

    @property
    def starting(self) -> bool:
        return self.lineup_slot not in BENCH_SLOTS

    @property
    def slot_name(self) -> str:
        return SLOT_NAME.get(self.lineup_slot, str(self.lineup_slot))

    @property
    def out(self) -> bool:
        return (self.injury_status or "").upper() in OUT_STATUSES

    @property
    def flex_eligible(self) -> bool:
        return self.position in ("RB", "WR", "TE")

    def label(self) -> str:
        tags = []
        st = (self.injury_status or "").upper()
        if st and st not in ("ACTIVE", "NORMAL"):
            tags.append(st[:1] if st in ("QUESTIONABLE", "DOUBTFUL", "OUT") else st)
        if self.lineup_slot == 21:
            tags.append("IR")
        suffix = f" ({', '.join(tags)})" if tags else ""
        return f"{self.name} ({self.position}-{self.pro_team}){suffix}"


class EspnLeague:
    def __init__(self, league_id, season, team_id=None, espn_s2=None, swid=None,
                 cookie=None, timeout=30, week_override=None):
        self.league_id = league_id
        self.season = season
        self.team_id = team_id
        self.timeout = timeout
        self.week_override = week_override
        self.sess = requests.Session()
        self.sess.headers.update({"User-Agent": UA, "Accept": "application/json"})
        if cookie:
            self.sess.headers["Cookie"] = cookie
        elif espn_s2 and swid:
            sw = swid if swid.startswith("{") else "{" + swid.strip("{}") + "}"
            self.sess.cookies.set("espn_s2", espn_s2)
            self.sess.cookies.set("SWID", sw)
        self._league = None
        self._roster_data = None

    # ------------------------------------------------------------- http
    @property
    def base(self) -> str:
        return f"{HOST}/apis/v3/games/ffl/seasons/{self.season}/segments/0/leagues/{self.league_id}"

    def _get(self, views, extra_headers=None, params=None):
        p = [("view", v) for v in views] + list(params or [])
        r = self.sess.get(self.base, params=p, headers=extra_headers or {}, timeout=self.timeout)
        if r.status_code == 401:
            raise RuntimeError(
                "ESPN returned 401 - league is private and the espn_s2/SWID cookies are "
                "missing or expired. Refresh them from your browser (see README).")
        if r.status_code == 404:
            raise RuntimeError(f"ESPN returned 404 - check league_id {self.league_id} and season {self.season}.")
        r.raise_for_status()
        return r.json()

    # ------------------------------------------------------------- league
    def load(self):
        if self._league is None:
            self._league = self._get(["mSettings", "mTeam", "mRoster"])
        return self._league

    @property
    def current_week(self) -> int:
        """ESPN's own scoring period, which rolls over Tuesday after MNF.

        So a Thursday run and a Sunday run in the same NFL week agree.
        """
        if self.week_override:
            return self.week_override
        d = self.load()
        status = d.get("status", {}) or {}
        return (d.get("scoringPeriodId")
                or status.get("currentMatchupPeriod")
                or status.get("latestScoringPeriod") or 1)

    @property
    def league_name(self) -> str:
        return (self.load().get("settings", {}) or {}).get("name", f"League {self.league_id}")

    def scoring(self) -> str:
        """Detect 'ppr' / 'half' / 'standard' from the receptions scoring rule."""
        d = self.load()
        items = ((d.get("settings", {}) or {}).get("scoringSettings", {}) or {}).get("scoringItems", [])
        pts = None
        for it in items:
            if it.get("statId") == 53:                      # receptions
                ov = it.get("pointsOverrides") or {}
                # position ids: 3=WR, 2=RB -- use the skill-position value
                for k in ("3", "2", "4"):
                    if k in ov:
                        pts = float(ov[k])
                        break
                if pts is None:
                    pts = float(it.get("points") or 0)
                break
        if pts is None:
            return "half"
        if pts >= 0.75:
            return "ppr"
        if pts >= 0.25:
            return "half"
        return "standard"

    def lineup_slot_counts(self) -> dict[int, int]:
        d = self.load()
        raw = (((d.get("settings", {}) or {}).get("rosterSettings", {}) or {})
               .get("lineupSlotCounts", {}) or {})
        return {int(k): int(v) for k, v in raw.items() if int(v) > 0}

    def starting_slots(self) -> list[int]:
        """Starting lineup slots, expanded to one entry per open spot."""
        counts = self.lineup_slot_counts()
        slots = []
        for slot in STARTING_SLOTS:
            slots.extend([slot] * counts.get(slot, 0))
        return slots

    def teams(self) -> list[dict]:
        return self.load().get("teams", []) or []

    def my_team(self) -> dict:
        ts = self.teams()
        if not ts:
            raise RuntimeError("No teams returned by ESPN - check credentials.")
        if self.team_id is not None:
            for t in ts:
                if t.get("id") == self.team_id:
                    return t
            raise RuntimeError(
                f"team_id {self.team_id} not in league. Available: "
                + ", ".join(f"{t.get('id')}={_team_name(t)}" for t in ts))
        return ts[0]

    def roster(self) -> list[Player]:
        # Refetch rosters pinned to the current scoring period; without the
        # param ESPN omits this week's projected stat line.
        week = self.current_week
        if self._roster_data is None:
            self._roster_data = self._get(["mRoster", "mTeam"],
                                          params=[("scoringPeriodId", week)])
        target_id = self.my_team().get("id")
        team = next((t for t in self._roster_data.get("teams", []) or []
                     if t.get("id") == target_id), None) or self.my_team()
        out = []
        for e in (team.get("roster", {}) or {}).get("entries", []) or []:
            p = _player_from_entry(e, week)
            if p:
                out.append(p)
        return out

    def league_universe(self) -> tuple[dict[str, Player], dict[str, str]]:
        """Every rostered player in the league, and which team holds them.

        Needed for the newsletter's Buy/Sell targets: those sit on other
        managers' rosters, so they appear in neither your roster nor the free
        agent pool. Returns ({uid: Player}, {uid: fantasy team name}).
        """
        week = self.current_week
        if self._roster_data is None:
            self._roster_data = self._get(["mRoster", "mTeam"],
                                          params=[("scoringPeriodId", week)])
        my_id = self.my_team().get("id")
        players: dict[str, Player] = {}
        owners: dict[str, str] = {}
        for t in self._roster_data.get("teams", []) or []:
            label = "YOUR TEAM" if t.get("id") == my_id else _team_name(t)
            for e in (t.get("roster", {}) or {}).get("entries", []) or []:
                p = _player_from_entry(e, week)
                if p:
                    players[p.uid] = p
                    owners[p.uid] = label
        return players, owners

    # ------------------------------------------------------------- free agents
    def free_agents(self, positions=("QB", "K", "DST", "RB", "WR", "TE"),
                    limit=250, include_waivers=True) -> list[Player]:
        slot_ids = sorted({sid for pos in positions
                           for sid, names in SLOT_ELIGIBLE.items()
                           if names == {pos}})
        status = ["FREEAGENT"] + (["WAIVERS"] if include_waivers else [])
        week = self.current_week
        filt = {
            "players": {
                "filterStatus": {"value": status},
                "filterSlotIds": {"value": slot_ids},
                "limit": limit,
                "offset": 0,
                "sortPercOwned": {"sortAsc": False, "sortPriority": 1},
                "sortDraftRanks": {"sortPriority": 100, "sortAsc": True,
                                   "value": "STANDARD"},
                "filterStatsForTopScoringPeriodIds": {
                    "value": 5, "additionalValue": [f"00{self.season}", f"10{self.season}"]},
            }
        }
        d = self._get(["kona_player_info"],
                      extra_headers={"x-fantasy-filter": json.dumps(filt)},
                      params=[("scoringPeriodId", week)])
        out = []
        for pe in d.get("players", []) or []:
            p = _player_from_pool(pe, week)
            if p:
                out.append(p)
        return out


# ----------------------------------------------------------------- helpers

def _team_name(t: dict) -> str:
    return (t.get("name")
            or " ".join(x for x in (t.get("location"), t.get("nickname")) if x)
            or t.get("abbrev") or f"Team {t.get('id')}")


def _projection(player: dict, week: int):
    """Weekly projection: statSourceId 1 (projected), split 1 (weekly)."""
    best = None
    for s in player.get("stats", []) or []:
        if s.get("statSourceId") != 1:
            continue
        if s.get("scoringPeriodId") != week:
            continue
        if s.get("statSplitTypeId") not in (1, None):
            continue
        v = s.get("appliedTotal")
        if v is not None:
            best = float(v)
            break
    return best


def _mk(player: dict, week: int, lineup_slot=20, injury=None, on_roster=True,
        fa_status="") -> Player | None:
    from .names import player_key, PRO_TEAM_ID
    pid = player.get("id")
    name = player.get("fullName") or " ".join(
        x for x in (player.get("firstName"), player.get("lastName")) if x)
    if not name:
        return None
    pos = POSITION_BY_ID.get(player.get("defaultPositionId"), "?")
    team = PRO_TEAM_ID.get(player.get("proTeamId"), "FA")
    return Player(
        uid=player_key(name, pos, team),
        espn_id=pid,
        name=name,
        position=pos,
        pro_team=team,
        lineup_slot=lineup_slot,
        eligible_slots=player.get("eligibleSlots") or [],
        injury_status=(injury or player.get("injuryStatus") or "ACTIVE"),
        espn_proj=_projection(player, week),
        percent_owned=((player.get("ownership") or {}).get("percentOwned")),
        on_roster=on_roster,
        fa_status=fa_status,
    )


def _player_from_entry(entry: dict, week: int) -> Player | None:
    player = ((entry.get("playerPoolEntry") or {}).get("player")) or {}
    return _mk(player, week,
               lineup_slot=entry.get("lineupSlotId", 20),
               injury=entry.get("injuryStatus"))


def _player_from_pool(pe: dict, week: int) -> Player | None:
    player = pe.get("player") or {}
    return _mk(player, week, lineup_slot=20, on_roster=False,
               fa_status=pe.get("status") or "")
