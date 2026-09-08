#!/usr/bin/env python
"""Weekly ESPN fantasy lineup report.

    python run.py                    # every configured league, current week
    python run.py --league Wankers   # just one league (name or id)
    python run.py --check            # verify credentials and sources only
    python run.py --week 5           # override the week
    python run.py --no-subvert       # skip Subvertadown
    python run.py --open             # open the report(s) when done

Read-only. It tells you what to change; you make the changes in ESPN.

Reports never overwrite each other: each run writes
`out/<league>/week<NN>-<timestamp>.md`, so a Thursday report and a Sunday
report of the same week both survive. `out/<league>/latest.md` always mirrors
the newest run for that league.
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import shutil
import sys
import traceback
import webbrowser
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Windows consoles default to cp1252 and choke on the report's arrows/emoji.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from ffbot import borischen, newsletter as nl, subvertadown  # noqa: E402
from ffbot.engine import analyze  # noqa: E402
from ffbot.espn import EspnLeague  # noqa: E402
from ffbot.report import render, to_console  # noqa: E402

CONFIG = ROOT / "config.json"


def load_config() -> dict:
    if not CONFIG.exists():
        sys.exit(f"Missing {CONFIG}\nCopy config.example.json to config.json and fill it in.")
    with CONFIG.open(encoding="utf-8") as f:
        return json.load(f)


def resolve_leagues(cfg: dict) -> list[dict]:
    """Normalize either config shape into a list of league specs.

    Single league:   {"espn": {...}}
    Several leagues: {"leagues": [{...}, {...}], "espn_auth": {...}}

    Cookies are usually the same account across leagues, so a league inherits
    `espn_auth` (or the `espn` block's) credentials unless it sets its own.
    """
    shared = dict(cfg.get("espn_auth") or {})
    raw = cfg.get("leagues")
    if not raw:
        one = cfg.get("espn")
        if not one:
            sys.exit('config.json needs either an "espn" block or a "leagues" list.')
        raw = [one]
        shared = {k: one.get(k) for k in ("espn_s2", "swid", "cookie", "season")}

    specs = []
    for i, lg in enumerate(raw):
        spec = dict(shared)
        spec.update({k: v for k, v in lg.items() if v is not None})
        if not spec.get("league_id"):
            sys.exit(f"config.json: leagues[{i}] is missing league_id.")
        if not spec.get("season"):
            sys.exit(f"config.json: leagues[{i}] is missing season.")
        specs.append(spec)
    return specs


def slugify(text: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", str(text)).strip("-").lower()
    return s or "league"


def league_dir(base: Path, spec: dict, league: EspnLeague, taken: dict) -> Path:
    """One folder per league, named after it. Disambiguated if names collide."""
    name = spec.get("name") or league.league_name
    slug = slugify(name)
    owner = taken.get(slug)
    if owner is not None and owner != spec["league_id"]:
        slug = f"{slug}-{spec['league_id']}"
    taken.setdefault(slug, spec["league_id"])
    d = base / slug
    d.mkdir(parents=True, exist_ok=True)
    return d


def fetch_subvertadown(cfg, debug_dir):
    """Returns ({pos: {key: SubRank}}, mode_used, errors)."""
    scfg = cfg.get("subvertadown", {})
    out, errors, mode_used = {}, [], None
    for pos in ("QB", "K", "DST"):
        try:
            rows, mode = subvertadown.fetch(pos, scfg, debug_dir=debug_dir)
            out[pos] = subvertadown.to_keyed(rows)
            mode_used = mode
        except Exception as e:
            errors.append(f"{pos}: {e}")
            out[pos] = {}
    return out, mode_used, errors


def subvertadown_from_borischen(bc):
    """Fallback: synthesize QB/K/DST rows from Boris Chen ranks.

    Ranks are not points, so the points threshold can't apply -- the report
    says as much when this path is used.
    """
    from ffbot.subvertadown import SubRank
    out = {}
    for pos in ("QB", "K", "DST"):
        d = {}
        for key, r in bc.get(pos, {}).items():
            sr = SubRank(name=r.name, position=pos, proj=None, hold=False)
            sr.rank = r.rank
            d[key] = sr
        out[pos] = d
    return out


def run_one(spec, cfg, args, caches, outbase, taken) -> Path | None:
    """Produce one league's report. Returns the path written, or None."""
    rules = dict(cfg.get("rules", {}))
    label = spec.get("name") or f"league {spec['league_id']}"
    print(f"\n{'=' * 70}\n{label}\n{'=' * 70}")

    league = EspnLeague(
        league_id=spec["league_id"], season=spec["season"], team_id=spec.get("team_id"),
        espn_s2=spec.get("espn_s2"), swid=spec.get("swid"), cookie=spec.get("cookie"),
        week_override=args.week,
    )
    print("ESPN        ... ", end="", flush=True)
    roster = league.roster()
    week = league.current_week
    scoring = rules.get("scoring") or "auto"
    if scoring == "auto":
        scoring = league.scoring()
    print(f"ok - {league.league_name}, week {week}, {scoring} scoring, "
          f"{len(roster)} rostered")

    print("Free agents ... ", end="", flush=True)
    try:
        fas = league.free_agents(limit=int(rules.get("fa_limit", 250)))
        print(f"ok - {len(fas)} available")
    except Exception as e:
        fas = []
        print(f"FAILED ({e})")

    # Boris Chen depends only on scoring format, so it is fetched once per
    # format and shared across leagues.
    print("Boris Chen  ... ", end="", flush=True)
    if scoring not in caches["bc"]:
        caches["bc"][scoring] = borischen.fetch_all(scoring)
    bc = caches["bc"][scoring]
    counts = {k: len(v) for k, v in bc.items()}
    print("ok - " + ", ".join(f"{k}:{v}" for k, v in counts.items())
          + ("" if len(caches["bc"]) == 1 else f"  [{scoring}]"))
    if not counts.get("RB") or not counts.get("WR"):
        print("  !! Boris Chen returned nothing for RB/WR - rankings may not be posted yet.")

    # Subvertadown is league-independent: fetch once for the whole run.
    sources = {"Boris Chen": f"{scoring} tiers"}
    if args.no_subvert:
        sub = subvertadown_from_borischen(bc)
        sources["Subvertadown"] = "SKIPPED - using Boris Chen ranks for QB/K/DST"
        print("Subvertadown... skipped (--no-subvert)")
    else:
        print("Subvertadown... ", end="", flush=True)
        if caches["sub"] is None:
            caches["sub"] = fetch_subvertadown(
                cfg, str(outbase / "debug") if args.debug_html else None)
        sub, mode, errors = caches["sub"]
        got = {k: len(v) for k, v in sub.items()}
        if any(got.values()):
            print("ok - " + ", ".join(f"{k}:{v}" for k, v in got.items()) + f"  [{mode}]")
            sources["Subvertadown"] = f"weekly projections via {mode}"
            for e in errors:
                print(f"    note: {e}")
        else:
            print("FAILED")
            for e in errors:
                print(f"    {e}")
            print("  -> falling back to Boris Chen ranks for QB/K/DST "
                  "(the points threshold will not apply)")
            sub = subvertadown_from_borischen(bc)
            sources["Subvertadown"] = "UNAVAILABLE - fell back to Boris Chen"

    if args.check:
        return None

    # Late Round newsletter: optional, annotation only. Fetched once per run
    # and shared across leagues; resolution is per-league because it depends
    # on who owns whom in *this* league.
    txns, nl_source = [], ""
    if args.newsletter:
        if caches["nl"] is None:
            caches["nl"] = nl.get(args.newsletter, week, ROOT / "newsletter")
        raw, nl_source, nl_warnings = caches["nl"]
        for w in nl_warnings:
            print(f"  !! {w}")
        if raw:
            universe, owners = league.league_universe()
            for p in fas:
                universe.setdefault(p.uid, p)
                owners.setdefault(p.uid, "free agent")
            txns = nl.resolve([copy.copy(t) for t in raw], universe, owners)
            named = sum(1 for t in txns if t.uid)
            print(f"Newsletter  ... ok - {named}/{len(txns)} matched to players")

    rules["scoring_resolved"] = scoring
    a = analyze(league, roster, fas, bc, sub, rules,
                newsletter=txns, newsletter_source=nl_source)
    md = render(a, sources)

    d = league_dir(outbase, spec, league, taken)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    md_path = d / f"week{week:02d}-{stamp}.md"
    md_path.write_text(md, encoding="utf-8")
    shutil.copyfile(md_path, d / "latest.md")

    print("\n" + "-" * 70)
    print(to_console(md))
    print("-" * 70)
    print(f"Saved: {md_path}")
    return md_path


def main():
    ap = argparse.ArgumentParser(description="ESPN fantasy lineup report")
    ap.add_argument("--check", action="store_true", help="verify sources, no report")
    ap.add_argument("--week", type=int, help="override current week")
    ap.add_argument("--league", help="only this league (name or id)")
    ap.add_argument("--no-subvert", action="store_true", help="skip Subvertadown")
    ap.add_argument("--newsletter", nargs="?", const="cache", metavar="URL",
                    help="Late Round '15 Transactions' link ('view this email in "
                         "your browser'). Bare --newsletter reuses this week's "
                         "cached copy. Omit entirely and nothing changes.")
    ap.add_argument("--open", action="store_true", help="open the report(s) when done")
    ap.add_argument("--out", default=str(ROOT / "out"), help="output directory")
    ap.add_argument("--debug-html", action="store_true", help="save fetched HTML")
    args = ap.parse_args()

    cfg = load_config()
    specs = resolve_leagues(cfg)

    if args.league:
        want = str(args.league).lower()
        specs = [s for s in specs
                 if want in (str(s.get("name", "")).lower(), str(s["league_id"]))]
        if not specs:
            sys.exit(f"No configured league matches {args.league!r}.")

    outbase = Path(args.out)
    outbase.mkdir(parents=True, exist_ok=True)
    caches = {"bc": {}, "sub": None, "nl": None}
    taken: dict[str, int] = {}

    written, failed = [], []
    for spec in specs:
        try:
            p = run_one(spec, cfg, args, caches, outbase, taken)
            if p:
                written.append(p)
        except Exception as e:
            # One bad league should not sink the rest of the run.
            failed.append((spec.get("name") or spec["league_id"], e))
            print(f"  FAILED: {e}")

    if args.check:
        print("\nAll source checks done.")
    elif len(specs) > 1:
        print(f"\n{'=' * 70}")
        for p in written:
            print(f"  {p}")
    for name, e in failed:
        print(f"  !! {name}: {e}")

    if args.open:
        for p in written:
            webbrowser.open(p.as_uri())
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        sys.exit(1)
