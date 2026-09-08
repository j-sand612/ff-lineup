#!/usr/bin/env python
"""Diagnose the Subvertadown pages once your cookie is in config.json.

    python probe.py

Fetches each weekly page and reports exactly what came back: whether the
session is logged in, what tables are on the page, what columns each one has,
which position the parser assigned it, and the first parsed rows.

Run this before trusting a report. If the parser can't read the subscriber
layout, this shows precisely why, and the raw HTML is saved to out/debug/
so the parser can be adjusted against it.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from bs4 import BeautifulSoup  # noqa: E402

from ffbot import subvertadown as sd  # noqa: E402

DEBUG = ROOT / "out" / "debug"


def logged_in(html: str) -> str:
    low = html.lower()
    if "/logout" in low or "my account" in low:
        return "yes (found a logout link)"
    if "/subscribe" in low and "/login" in low:
        return "NO - page still shows Subscribe/Login"
    return "unclear"


def main():
    cfg_path = ROOT / "config.json"
    if not cfg_path.exists():
        sys.exit("No config.json - copy config.example.json and add your cookie.")
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    scfg = cfg.get("subvertadown", {})
    mode = (scfg.get("mode") or "cookies").lower()
    print(f"Subvertadown mode: {mode}")
    if mode == "cookies" and not scfg.get("cookie"):
        sys.exit("subvertadown.cookie is empty - see README for how to copy it.")
    DEBUG.mkdir(parents=True, exist_ok=True)

    total_ok = 0
    for pos in ("QB", "K", "DST"):
        print("\n" + "=" * 68)
        print(f"{pos}  ->  {sd.PAGES[pos]}")
        print("=" * 68)
        try:
            if mode == "cookies":
                rows, html = sd.fetch_cookies(pos, scfg["cookie"])
            elif mode == "playwright":
                rows, html = sd.fetch_playwright(pos, scfg.get("profile_dir", ".pw-profile"))
            else:
                rows, html = sd.fetch_file(pos, scfg.get("html_dir", "saved_html"))
        except Exception as e:
            print(f"  FETCH FAILED: {e}")
            continue

        out = DEBUG / f"subvertadown-{pos.lower()}.html"
        out.write_text(html, encoding="utf-8")
        print(f"  page size   : {len(html):,} bytes  (saved to {out})")
        print(f"  logged in?  : {logged_in(html)}")

        soup = BeautifulSoup(html, "lxml")
        tables = soup.select("table")
        print(f"  tables found: {len(tables)}")
        for i, t in enumerate(tables):
            headers = sd._header_row(t)
            if not headers:
                continue
            kind = sd._classify(headers)
            body = t.find("tbody") or t
            n = len([tr for tr in body.find_all("tr")
                     if tr.find_parent("table") is t])
            mark = "  <== used for " + pos if kind == pos else ""
            cols = " | ".join(h for h in headers if h)[:88]
            print(f"    [{i}] rows={n:<4} classified={str(kind):<5}{mark}")
            print(f"         cols: {cols}")

        print(f"\n  PARSED {len(rows)} {pos} rows")
        if rows:
            total_ok += 1
            hold_col = any(r.hold_raw for r in rows)
            print(f"  Hold column present: {'yes' if hold_col else 'NO - not on this page'}")
            for r in rows[:5]:
                print(f"    {r.rank:>2}. {r.name:<24} proj={r.proj:<6} "
                      f"next={r.proj_next} hold={r.hold_raw or '-':<3} err={r.err}")
            if len(rows) > 5:
                print(f"    ... and {len(rows) - 5} more")
        else:
            print("  !! nothing parsed - check the 'cols' lines above against what")
            print("     the parser looks for: a name column, and a 'WK <n>' column.")

    print("\n" + "=" * 68)
    if total_ok == 3:
        print("All three positions parsed. Run: python run.py")
    else:
        print(f"{total_ok}/3 positions parsed. Send the out/debug/*.html files "
              "or the column lines above to get the parser adjusted.")
    return 0 if total_ok == 3 else 1


if __name__ == "__main__":
    sys.exit(main())
