"""The decision engine: rank attachment, optimal lineup, free-agent upgrades.

Mirrors the manual process:

  1. QB / K / D-ST  -- ordered by Subvertadown projected points. Start the
     highest projection you roster. Only pick up a free agent if they beat
     your guy by more than `fa_threshold_pts`, and never if Subvertadown
     flags your current player as a Hold.

  2. RB / WR / TE / FLEX -- ordered by Boris Chen tiers. Fill dedicated slots
     first, then FLEX, walking a single cross-position ordering. Free agents
     are worth reporting when they'd crack the starting lineup.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .espn import SLOT_ELIGIBLE, SLOT_NAME, Player
from .names import fuzzy_match

STREAM_POS = ("QB", "K", "DST")
SKILL_POS = ("RB", "WR", "TE")


@dataclass
class SlotAssignment:
    slot: int
    player: Player | None = None
    was_starting: bool = False

    @property
    def slot_name(self) -> str:
        return SLOT_NAME.get(self.slot, str(self.slot))


@dataclass
class Move:
    kind: str                 # "START" | "SIT" | "SWAP"
    slot_name: str
    incoming: Player | None = None
    outgoing: Player | None = None
    rationale: str = ""


@dataclass
class FaSuggestion:
    fa: Player
    position: str
    drop: Player | None
    gain_pts: float | None = None
    gain_desc: str = ""
    rationale: str = ""
    blocked: str = ""          # non-empty => shown as "considered, not recommended"
    priority: float = 0.0


@dataclass
class Analysis:
    week: int
    scoring: str
    league_name: str
    team_name: str
    assignments: list[SlotAssignment] = field(default_factory=list)
    bench: list[Player] = field(default_factory=list)
    moves: list[Move] = field(default_factory=list)
    fa_recommended: list[FaSuggestion] = field(default_factory=list)
    fa_considered: list[FaSuggestion] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    flex_values: dict = field(default_factory=dict)
    # Late-Round newsletter, annotation only -- never feeds a decision below.
    newsletter: list = field(default_factory=list)
    newsletter_source: str = ""


# ------------------------------------------------------------------ matching

def attach_rankings(players, bc, sub, unmatched=None):
    """Hang Boris Chen and Subvertadown rows onto each Player.

    Exact matches are resolved across the whole pool first and their ranking
    rows are marked claimed. Only then is fuzzy matching allowed, against
    what's left -- otherwise a free agent can be handed a rostered player's
    ranking (Brian Robinson Jr. scores 0.93 against Bijan Robinson, same
    surname, same team).
    """
    claimed_bc: set[str] = set()
    claimed_sub: set[str] = set()

    for p in players:                                   # pass 1: exact only
        pos_map = bc.get(p.position, {})
        p.bc_rank = pos_map.get(p.uid)
        if p.bc_rank is not None:
            claimed_bc.add(p.uid)
        p.sub_rank = None
        if p.position in STREAM_POS:
            p.sub_rank = sub.get(p.position, {}).get(p.uid)
            if p.sub_rank is not None:
                claimed_sub.add(p.uid)

    for p in players:                                   # pass 2: fuzzy leftovers
        if p.bc_rank is None:
            pos_map = bc.get(p.position, {})
            if pos_map:
                k, score = fuzzy_match(p.uid, pos_map, exclude=claimed_bc)
                if k:
                    p.bc_rank = pos_map[k]
                    claimed_bc.add(k)
                    p.match_note = f"fuzzy {score:.0%}"
        if p.position in STREAM_POS and p.sub_rank is None:
            smap = sub.get(p.position, {})
            if smap:
                k, score = fuzzy_match(p.uid, smap, exclude=claimed_sub)
                if k:
                    p.sub_rank = smap[k]
                    claimed_sub.add(k)
                    p.match_note = f"fuzzy {score:.0%}"

    for p in players:                                   # pass 3: report + flex context
        if (p.position in STREAM_POS and p.sub_rank is None
                and unmatched is not None and p.on_roster and not p.out):
            unmatched.append(f"{p.label()} - no Subvertadown row")
        elif p.bc_rank is None and unmatched is not None and p.on_roster and not p.out:
            unmatched.append(f"{p.label()} - no Boris Chen row (bye, injured, or unranked)")

        # Combined FLX list, shown for context only -- see build_flex_values.
        p.flx_rank = None
        if p.flex_eligible and bc.get("FLX"):
            from .borischen import flex_lookup
            fr = flex_lookup(bc["FLX"], p.name)
            if fr:
                p.flx_rank = fr.rank
    return players


def available(p: Player) -> bool:
    """Can this player actually be started this week?"""
    if p.lineup_slot == 21:          # IR: ESPN will not let you start them
        return False
    if p.out:
        return False
    if p.bc_rank is None and p.sub_rank is None:
        # unranked everywhere and no projection => almost certainly on bye
        if not p.espn_proj:
            return False
    return True


# ------------------------------------------------------------------ ordering

def stream_value(p: Player):
    """Projected points for QB/K/DST. Falls back to ESPN if Subvertadown is silent."""
    if p.sub_rank and p.sub_rank.proj is not None:
        return p.sub_rank.proj
    return p.espn_proj


def positional_baselines(league) -> dict[str, float]:
    """How deep each position gets started league-wide -- its replacement level.

    Dedicated slots plus a proportional share of the flex slots, times the
    number of teams. A 10-team league with RB2/WR2/TE1/FLEX2 gives roughly
    RB 28, WR 28, TE 14 -- which is what makes an RB12 and a TE6 comparable
    for the same flex spot.
    """
    counts = league.lineup_slot_counts()
    n_teams = max(1, len(league.teams()))
    dedicated = {"RB": counts.get(2, 0), "WR": counts.get(4, 0), "TE": counts.get(6, 0)}
    flex_slots = (counts.get(23, 0) + counts.get(3, 0)
                  + counts.get(5, 0) + counts.get(7, 0))
    total_ded = sum(dedicated.values()) or 1
    out = {}
    for pos, c in dedicated.items():
        share = flex_slots * (c / total_ded)
        out[pos] = max(1.0, n_teams * (c + share))
    return out


def build_flex_values(players, bc, baselines):
    """Cross-position ordering value for RB/WR/TE. Lower is better.

    Boris Chen positional rank divided by that position's replacement level:
    "how far into the startable pool is this player". This keeps the flex
    decision consistent with the positional tier charts you actually read.

    Boris Chen also publishes a combined FLX file, but it is built from a
    different consensus snapshot than the positional files -- in the week this
    was written it put WR6 at flex 45 and WR48 at flex 42 -- so it is reported
    as context and never used to decide.
    """
    vals: dict[str, float | None] = {}
    for p in players:
        if not p.flex_eligible:
            continue
        vals[p.uid] = None if p.bc_rank is None else p.bc_rank.rank / baselines.get(p.position, 30.0)
    return vals


def fv(flex_values, uid, default=999.0) -> float:
    """Flex value lookup. Missing *and* explicitly-None both mean 'unranked'."""
    v = flex_values.get(uid)
    return default if v is None else float(v)


def _sort_key(p: Player, flex_values):
    v = flex_values.get(p.uid)
    proj = p.espn_proj if p.espn_proj is not None else 0.0
    if v is not None:
        return (0, v, -proj)
    if p.bc_rank is not None:
        return (1, p.bc_rank.avg_rank, -proj)
    return (2, 0.0, -proj)


# ------------------------------------------------------------------ lineup

def optimal_lineup(players, slots, flex_values):
    """Greedy fill: walk one global ordering, take the most restrictive open slot.

    With dedicated slots nested inside FLEX this greedy is optimal, so there's
    no need for a full assignment solve.
    """
    open_slots = list(slots)
    assignments: list[SlotAssignment] = []
    used: set[str] = set()

    def take(slot):
        open_slots.remove(slot)
        return slot

    # --- QB / K / D-ST: highest projection wins its slot
    for pos in STREAM_POS:
        want = [s for s in open_slots if SLOT_ELIGIBLE.get(s) == {pos}]
        pool = sorted(
            [p for p in players if p.position == pos and available(p) and p.uid not in used],
            key=lambda p: -(stream_value(p) if stream_value(p) is not None else -999))
        for slot in list(want):
            pick = pool.pop(0) if pool else None
            if pick:
                used.add(pick.uid)
            assignments.append(SlotAssignment(take(slot), pick, bool(pick and pick.starting)))

    # --- RB / WR / TE / FLEX: single cross-position ordering
    skill = sorted([p for p in players
                    if p.flex_eligible and available(p) and p.uid not in used],
                   key=lambda p: _sort_key(p, flex_values))
    filled: dict[int, list[Player]] = {}
    for p in skill:
        dedicated = [s for s in open_slots if SLOT_ELIGIBLE.get(s) == {p.position}]
        flexish = [s for s in open_slots
                   if p.position in SLOT_ELIGIBLE.get(s, set())
                   and SLOT_ELIGIBLE.get(s) != {p.position}]
        slot = None
        if dedicated:
            slot = dedicated[0]
        elif flexish:
            # prefer the tightest flex (RB/WR before superflex)
            slot = sorted(flexish, key=lambda s: len(SLOT_ELIGIBLE.get(s, ())))[0]
        if slot is None:
            continue
        used.add(p.uid)
        filled.setdefault(slot, []).append(p)
        assignments.append(SlotAssignment(take(slot), p, p.starting))

    for slot in list(open_slots):     # nobody eligible/available
        assignments.append(SlotAssignment(take(slot), None, False))

    order = {s: i for i, s in enumerate([0, 2, 4, 6, 23, 3, 5, 7, 16, 17])}
    assignments.sort(key=lambda a: (order.get(a.slot, 99), a.player.name if a.player else ""))
    bench = [p for p in players if p.uid not in used]
    return assignments, bench


def diff_lineup(assignments, bench, roster):
    """Turn the optimal lineup into the specific moves to make in ESPN."""
    should_start = {a.player.uid for a in assignments if a.player}
    currently_start = {p.uid for p in roster if p.starting and p.lineup_slot != 21}
    moves = []

    to_start = [a for a in assignments if a.player and a.player.uid not in currently_start]
    to_sit = [p for p in roster
              if p.uid in currently_start and p.uid not in should_start]

    for a in to_start:
        p = a.player
        rationale = describe(p)
        partner = None
        for cand in to_sit:
            if cand.position == p.position or (p.flex_eligible and cand.flex_eligible):
                partner = cand
                break
        if partner:
            to_sit.remove(partner)
            moves.append(Move("SWAP", a.slot_name, p, partner,
                              f"{rationale}  vs  {describe(partner)}"))
        else:
            moves.append(Move("START", a.slot_name, p, None, rationale))
    for p in to_sit:
        moves.append(Move("SIT", p.slot_name, None, p, describe(p)))
    return moves


def describe(p: Player) -> str:
    """Short ranking blurb used throughout the report."""
    bits = []
    if p.sub_rank is not None:
        s = p.sub_rank
        if s.proj is not None:
            bits.append(f"Subvertadown {s.proj:.1f} pts")
        if s.rank:
            bits.append(f"SD #{s.rank}")
        if s.hold:
            bits.append("HOLD")
    if p.bc_rank is not None:
        b = p.bc_rank
        bits.append(f"BC {b.position}{b.rank} (Tier {b.tier})")
    if p.bc_rank is not None and getattr(p, "flx_rank", None) is not None:
        bits.append(f"BC flex #{p.flx_rank}")
    if not bits and p.espn_proj is not None:
        bits.append(f"ESPN proj {p.espn_proj:.1f}")
    if p.out:
        bits.append(f"** {p.injury_status} **")
    return ", ".join(bits) or "unranked"


# ------------------------------------------------------------------ free agents

def stream_free_agents(roster, fas, position, threshold, respect_hold):
    """QB / K / D-ST pickups, gated on the points threshold and the Hold flag."""
    mine = [p for p in roster if p.position == position]
    rated = [p for p in mine if stream_value(p) is not None]
    if not rated:
        return [], []
    best = max(rated, key=lambda p: stream_value(p))
    drop = min(rated, key=lambda p: stream_value(p))
    base = stream_value(best)

    rec, considered = [], []
    pool = sorted([f for f in fas if f.position == position and stream_value(f) is not None],
                  key=lambda f: -stream_value(f))
    for fa in pool[:6]:
        gain = stream_value(fa) - base
        if gain <= 0:
            continue
        held = respect_hold and (drop.sub_rank is not None and drop.sub_rank.hold)
        s = FaSuggestion(
            fa=fa, position=position, drop=drop, gain_pts=gain,
            gain_desc=f"+{gain:.1f} pts over {best.name} ({base:.1f})",
            rationale=f"{describe(fa)}  ->  replaces {describe(best)}",
            priority=gain,
        )
        if held:
            s.blocked = f"Subvertadown says HOLD {drop.name} - your rule says skip"
            considered.append(s)
        elif gain < threshold:
            s.blocked = f"only +{gain:.1f} pts, under your {threshold:g}-pt bar"
            considered.append(s)
        else:
            rec.append(s)
    return rec, considered


def skill_free_agents(roster, fas, assignments, flex_values_all, bc, tier_gap):
    """RB / WR / TE pickups: worth reporting when they'd crack the lineup."""
    starters = [a.player for a in assignments if a.player and a.player.flex_eligible]
    if not starters:
        return [], []
    worst = max(starters, key=lambda p: fv(flex_values_all, p.uid))
    worst_val = fv(flex_values_all, worst.uid)

    droppables = sorted(
        [p for p in roster if p.flex_eligible
         and p.uid not in {s.uid for s in starters}],
        key=lambda p: -fv(flex_values_all, p.uid))
    drop = droppables[0] if droppables else None

    rec, considered = [], []
    for fa in fas:
        if not fa.flex_eligible or fa.bc_rank is None:
            continue
        val = flex_values_all.get(fa.uid)
        if val is None:
            continue
        if val < worst_val:
            gap = (worst.bc_rank.tier - fa.bc_rank.tier) if (worst.bc_rank and fa.bc_rank) else 0
            s = FaSuggestion(
                fa=fa, position=fa.position, drop=drop,
                gain_desc=f"would start over {worst.name} ({describe(worst)})",
                rationale=describe(fa),
                priority=worst_val - val,
            )
            if fa.percent_owned is not None and fa.percent_owned > 85:
                s.blocked = f"{fa.percent_owned:.0f}% rostered - probably not actually free"
                considered.append(s)
            elif gap < tier_gap and tier_gap > 0:
                s.blocked = f"same tier as {worst.name}, not the {tier_gap}-tier jump you want"
                considered.append(s)
            else:
                rec.append(s)
    rec.sort(key=lambda s: -s.priority)
    considered.sort(key=lambda s: -s.priority)
    return rec[:6], considered[:6]


# ------------------------------------------------------------------ top level

def analyze(league, roster, fas, bc, sub, rules, newsletter=None,
            newsletter_source="") -> Analysis:
    unmatched: list[str] = []
    # One pass over roster and free agents together, roster first, so exact
    # matches are claimed before any fuzzy matching is attempted.
    everyone = roster + fas
    attach_rankings(everyone, bc, sub, unmatched)
    baselines = positional_baselines(league)
    flex_values = build_flex_values(everyone, bc, baselines)

    slots = league.starting_slots()
    assignments, bench = optimal_lineup(roster, slots, flex_values)
    moves = diff_lineup(assignments, bench, roster)

    a = Analysis(
        week=league.current_week,
        scoring=rules.get("scoring_resolved", "half"),
        league_name=league.league_name,
        team_name=_team_label(league),
        assignments=assignments,
        bench=sorted(bench, key=lambda p: _sort_key(p, flex_values)),
        moves=moves,
        unmatched=unmatched,
        flex_values=flex_values,
        newsletter=list(newsletter or []),
        newsletter_source=newsletter_source,
    )

    thr = float(rules.get("fa_threshold_pts", 1.5))
    hold = bool(rules.get("respect_hold", True))
    for pos in STREAM_POS:
        r, c = stream_free_agents(roster, fas, pos, thr, hold)
        a.fa_recommended.extend(r)
        a.fa_considered.extend(c)
    r, c = skill_free_agents(roster, fas, assignments, flex_values, bc,
                             int(rules.get("fa_threshold_tiers", 0)))
    a.fa_recommended.extend(r)
    a.fa_considered.extend(c)
    a.fa_recommended.sort(key=lambda s: -(s.gain_pts if s.gain_pts is not None else s.priority))

    for asg in a.assignments:
        if asg.player is None:
            a.warnings.append(f"No available player for {asg.slot_name} - check byes/injuries.")
    return a


def _team_label(league) -> str:
    from .espn import _team_name
    try:
        return _team_name(league.my_team())
    except Exception:
        return "your team"
