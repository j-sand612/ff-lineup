"""Markdown report rendering."""
from __future__ import annotations

from datetime import datetime

from .engine import Analysis, describe

CHANGE = {"same": "", "new": "**<- CHANGE**"}


def _fmt_player(p) -> str:
    if p is None:
        return "_(nobody available)_"
    return p.label()


def render(a: Analysis, sources: dict) -> str:
    L: list[str] = []
    add = L.append
    _n = [0]

    def sec(title: str) -> str:
        _n[0] += 1
        return f"## {_n[0]}. {title}"

    lr = {t.uid: t for t in a.newsletter if t.uid}

    def lr_tag(player) -> str:
        t = lr.get(getattr(player, "uid", None))
        return f"  ·  **Late Round {t.label.split(' ', 1)[1]}** (#{t.order})" if t else ""

    add(f"# Week {a.week} lineup report - {a.team_name}")
    add("")
    add(f"_{a.league_name} · {a.scoring.upper()} scoring · generated "
        f"{datetime.now().strftime('%a %b %d, %I:%M %p')}_")
    add("")
    src = ", ".join(f"{k} ({v})" for k, v in sources.items())
    add(f"Sources: {src}")
    add("")

    # ---------------------------------------------------------- lineup
    add(sec("Start these"))
    add("")
    add("| Slot | Player | Ranking | |")
    add("|---|---|---|---|")
    currently = {}
    for asg in a.assignments:
        p = asg.player
        flag = "" if (p and asg.was_starting) else ("**← CHANGE**" if p else "⚠️")
        add(f"| {asg.slot_name} | {_fmt_player(p)} | {describe(p) if p else '-'} | {flag} |")
    add("")

    # ---------------------------------------------------------- moves
    add(sec("Moves to make"))
    add("")
    if not a.moves:
        add("Nothing to change - your lineup is already optimal by these rankings.")
    else:
        for m in a.moves:
            if m.kind == "SWAP":
                add(f"- **{m.slot_name}: start {m.incoming.label()}, bench {m.outgoing.label()}**")
                add(f"  - {m.rationale}")
            elif m.kind == "START":
                add(f"- **{m.slot_name}: start {m.incoming.label()}**")
                add(f"  - {m.rationale}")
            else:
                add(f"- **Bench {m.outgoing.label()}** (was {m.slot_name})")
                add(f"  - {m.rationale}")
    add("")

    # ---------------------------------------------------------- bench
    add(sec("Bench"))
    add("")
    if a.bench:
        add("| Player | Ranking |")
        add("|---|---|")
        for p in a.bench:
            add(f"| {p.label()} | {describe(p)} |")
    else:
        add("_empty_")
    add("")

    # ---------------------------------------------------------- pickups
    add(sec("Free agents worth adding"))
    add("")
    if not a.fa_recommended:
        add("None clear your thresholds this week.")
    else:
        for s in a.fa_recommended:
            head = f"- **ADD {s.fa.label()}**"
            if s.gain_pts is not None:
                head += f" — {s.gain_desc}"
            elif s.gain_desc:
                head += f" — {s.gain_desc}"
            add(head + lr_tag(s.fa))
            add(f"  - {s.rationale}")
            if s.drop is not None:
                add(f"  - Drop candidate: {s.drop.label()} ({describe(s.drop)})")
            if s.fa.percent_owned is not None:
                add(f"  - Rostered in {s.fa.percent_owned:.0f}% of ESPN leagues"
                    f"{' · ' + s.fa.fa_status if s.fa.fa_status else ''}")
    add("")

    add(sec("Considered, but not recommended"))
    add("")
    if not a.fa_considered:
        add("_nothing close_")
    else:
        for s in a.fa_considered:
            add(f"- {s.fa.label()} — {s.blocked}")
            if s.gain_desc:
                add(f"  - {s.gain_desc}")
    add("")

    # ---------------------------------------------------------- newsletter
    if a.newsletter:
        add(sec("Late Round - 15 Transactions"))
        add("")
        if a.newsletter_source:
            add(f"_{a.newsletter_source}_")
            add("")
        add("_A second opinion. None of this affected the lineup or thresholds above._")
        add("")

        mine, adds, taken, trades, unresolved = [], [], [], [], []
        for t in a.newsletter:
            if t.player is None:
                unresolved.append(t)
            elif t.owner == "YOUR TEAM":
                mine.append(t)
            elif t.is_trade:
                trades.append(t)
            elif t.owner == "free agent":
                adds.append(t)
            else:
                # He says add them, but someone in your league already has them.
                taken.append(t)

        if mine:
            add(f"**Involves your roster ({len(mine)})**")
            add("")
            for t in mine:
                add(f"- **{t.action} {t.player.label()}** — his #{t.order}, "
                    f"and he's on your roster")
                if t.blurb:
                    add(f"  - {t.blurb}")
            add("")
        if adds:
            add(f"**Available right now ({len(adds)})**")
            add("")
            for t in adds:
                own = (f"  ·  {t.player.percent_owned:.0f}% rostered on ESPN"
                       if t.player.percent_owned is not None else "")
                add(f"- #{t.order} **{t.action}** {t.player.label()}{own}")
                if t.blurb:
                    add(f"  - {t.blurb}")
            add("")
        if taken:
            add(f"**Already taken in your league ({len(taken)})**")
            add("")
            for t in taken:
                add(f"- #{t.order} {t.action} {t.player.label()} — held by {t.owner}")
            add("")
        if trades:
            add(f"**Trade targets ({len(trades)})**")
            add("")
            for t in trades:
                add(f"- #{t.order} **{t.action}** {t.player.label()} — held by "
                    f"**{t.owner}**")
                if t.blurb:
                    add(f"  - {t.blurb}")
            add("")
        if unresolved:
            add(f"**Could not match to a player ({len(unresolved)})**")
            add("")
            for t in unresolved:
                add(f"- {t.action} {t.raw_name}")
            add("")

    # ---------------------------------------------------------- notes
    if a.warnings or a.unmatched:
        add(sec("Data notes"))
        add("")
        for w in a.warnings:
            add(f"- ⚠️ {w}")
        for u in a.unmatched:
            add(f"- {u}")
        add("")

    add("---")
    add("_Read-only report. Nothing was changed in your ESPN league._")
    return "\n".join(L)


_ASCII = {"←": "<-", "→": "->", "⚠️": "(!)", "⚠": "(!)", "·": "-", "—": "-"}


def to_console(md: str) -> str:
    """Light cleanup so the markdown reads fine in a plain terminal."""
    out = []
    for line in md.splitlines():
        if set(line.strip()) <= {"|", "-", " "} and "-" in line:
            continue
        line = line.replace("**", "").replace("_", "")
        for uni, asc in _ASCII.items():
            line = line.replace(uni, asc)
        out.append(line)
    return "\n".join(out)
