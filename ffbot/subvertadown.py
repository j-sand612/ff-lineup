"""Subvertadown weekly QB / K / D-ST projections.

No API. Laravel + Livewire, server-rendered `<table class="sub-table">`,
behind a subscriber paywall and Cloudflare. Three ways in, in order of
preference:

  cookies    -- replay your logged-in session cookie with requests (headless,
                schedulable; Cloudflare may eventually demand a real browser)
  playwright -- drive a persistent logged-in Chromium profile (survives
                Cloudflare; needs `pip install playwright && playwright install`)
  files      -- parse pages you saved manually from the browser (always works)

Columns we care about, per the live tables:
  QB   PLAYER | MATCHUP | WK n | WK n+1 | ERR. | PAY RUY PTD INT.
  K    PLAYER | MATCHUP | WK n | WK n+1 | HOLD? | ERR. | ...risk cols
  DST  TEAM   | WK n | WK n TREND | WK n+1 | HOLD? | ERR.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from bs4 import BeautifulSoup

PAGES = {
    "QB": "https://subvertadown.com/weekly/quarterback",
    "K": "https://subvertadown.com/weekly/kicker",
    "DST": "https://subvertadown.com/weekly/defense",
}
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


@dataclass
class SubRank:
    name: str
    position: str
    proj: float | None = None          # this week's projected points
    proj_next: float | None = None     # next week, for streaming context
    hold: bool = False
    hold_raw: str = ""
    err: float | None = None
    matchup: str = ""
    rank: int | None = None            # our own ordering by proj
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------- parsing

def _cells(tr):
    """Row cells expanded across colspans, as plain text."""
    out = []
    for td in tr.find_all(["td", "th"], recursive=False):
        txt = re.sub(r"\s+", " ", td.get_text(" ", strip=True)).strip()
        span = int(td.get("colspan") or 1)
        out.append(txt)
        out.extend([""] * (span - 1))
    return out


def _header_row(table):
    """Pick the thead row that actually names the columns.

    These tables stack a spanning banner row ("Wk 1 Proj. Stats") above the
    real header, so take the row containing PLAYER/TEAM.
    """
    head = table.find("thead")
    rows = head.find_all("tr") if head else []
    if not rows:
        rows = table.find_all("tr")[:2]
    best, best_score = None, -1
    for tr in rows:
        cells = _cells(tr)
        joined = " ".join(cells).upper()
        score = sum(x in joined for x in ("PLAYER", "TEAM", "WK", "HOLD", "ERR"))
        score = score * 10 + len([c for c in cells if c])
        if score > best_score:
            best, best_score = cells, score
    return best or []


def _col_index(headers):
    """Map semantic field -> column index, tolerant of naming drift."""
    idx = {}
    wk_cols = []
    for i, h in enumerate(headers):
        H = h.upper().strip()
        if not H:
            continue
        if "PLAYER" in H or H == "TEAM":
            idx.setdefault("name", i)
        elif "MATCHUP" in H:
            idx.setdefault("matchup", i)
        elif "HOLD" in H:
            idx.setdefault("hold", i)
        elif H.startswith("ERR"):
            idx.setdefault("err", i)
        elif "TREND" in H:
            # Must precede the week check: the column is literally "Wk 1 Trend"
            # and would otherwise be read as the week-1 projection.
            idx.setdefault("trend", i)
        elif (m := re.match(r"WK\s*(\d+)", H)):
            # Prefix, not exact: the defense page labels a column "Wk 2 Top 4".
            wk_cols.append((int(m.group(1)), i))
    wk_cols.sort()
    if wk_cols:
        idx["proj"] = wk_cols[0][1]
        idx["week"] = wk_cols[0][0]
    if len(wk_cols) > 1:
        idx["proj_next"] = wk_cols[1][1]
    return idx


def _classify(headers) -> str | None:
    """Identify which position's table this is from its own columns.

    Each table has a distinctive fingerprint, so we never rely on the page
    only containing one table (the homepage stacks all three).
    """
    H = " ".join(headers).upper()
    toks = set(re.findall(r"[A-Z0-9]+", H))
    # Order matters. D/ST also carries an "Int." column, so it has to be
    # settled before the QB check or every defense table reads as a QB table.
    if {"4THD", "MISSED", "CEIL", "IMPL"} & toks or ("FG" in toks and "XP" in toks):
        return "K"
    if {"SACKS", "FR", "TREND"} & toks or ("TEAM" in toks and "PLAYER" not in toks):
        return "DST"
    if {"PAY", "RUY", "PTD"} & toks:
        return "QB"
    return None


def _num(s):
    if s is None:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", str(s).replace(",", ""))
    return float(m.group()) if m else None


def _truthy_hold(s: str) -> bool:
    return str(s).strip().upper() in {"Y", "YES", "TRUE", "HOLD"}


def parse_table(html: str, position: str) -> list[SubRank]:
    """Extract SubRanks from a Subvertadown weekly page (or saved HTML)."""
    soup = BeautifulSoup(html, "lxml")
    results: list[SubRank] = []
    seen: set[str] = set()

    for table in soup.select("table.sub-table, table"):
        headers = _header_row(table)
        if not headers:
            continue
        idx = _col_index(headers)
        if "name" not in idx or "proj" not in idx:
            continue
        # Skip the strength-of-schedule / matchup-bonus grids, which are also
        # week-numbered but describe teams rather than startable players.
        header_join = " ".join(headers).upper()
        if re.search(r"BASELINE|NEXT 4|PLAYER/TEAM", header_join):
            continue
        if _classify(headers) != position:
            continue

        body = table.find("tbody") or table
        for tr in body.find_all("tr", recursive=True):
            # Subscriber pages hang expandable "details" panels inside a row,
            # and those contain their own tables. Only take rows belonging to
            # THIS table, or a nested panel's rows get read as players.
            if tr.find_parent("table") is not table:
                continue
            cells = _cells(tr)
            if len(cells) <= idx["name"]:
                continue
            raw_name = cells[idx["name"]].strip()
            if not raw_name or raw_name.upper() in {"PLAYER", "TEAM"}:
                continue
            proj = _num(cells[idx["proj"]]) if len(cells) > idx["proj"] else None
            if proj is None:
                continue

            matchup = ""
            if "matchup" in idx and len(cells) > idx["matchup"]:
                matchup = cells[idx["matchup"]]

            if position == "DST":
                # "Jaguars vs. Browns" -> own team is the first half
                parts = re.split(r"\s+(?:vs\.?|@|at)\s+", raw_name, maxsplit=1, flags=re.I)
                name = parts[0].strip()
                if len(parts) > 1 and not matchup:
                    matchup = raw_name
            else:
                name = raw_name.split("|")[0].strip()

            hold_raw = ""
            if "hold" in idx and len(cells) > idx["hold"]:
                hold_raw = cells[idx["hold"]]

            sr = SubRank(
                name=name,
                position=position,
                proj=proj,
                proj_next=(_num(cells[idx["proj_next"]])
                           if "proj_next" in idx and len(cells) > idx["proj_next"] else None),
                hold=_truthy_hold(hold_raw),
                hold_raw=hold_raw.strip(),
                err=(_num(cells[idx["err"]])
                     if "err" in idx and len(cells) > idx["err"] else None),
                matchup=matchup,
            )
            dedupe = f"{sr.name.lower()}|{sr.proj}"
            if dedupe in seen:
                continue
            seen.add(dedupe)
            results.append(sr)

    results.sort(key=lambda r: -(r.proj if r.proj is not None else -999))
    for i, r in enumerate(results, 1):
        r.rank = i
    return results


def to_keyed(rows: list[SubRank]) -> dict[str, SubRank]:
    from .names import player_key
    out = {}
    for r in rows:
        team = r.name if r.position == "DST" else None
        out[player_key(r.name, r.position, team)] = r
    return out


# ---------------------------------------------------------------- fetching

def _looks_paywalled(html: str, rows: list[SubRank]) -> bool:
    return len(rows) < 8 and ("Subscribe" in html or "login" in html.lower())


def fetch_cookies(position: str, cookie: str, timeout=30):
    import requests
    headers = {
        "User-Agent": UA,
        "Cookie": cookie,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    }
    r = requests.get(PAGES[position], headers=headers, timeout=timeout)
    r.raise_for_status()
    return parse_table(r.text, position), r.text


def fetch_playwright(position: str, profile_dir: str, timeout=45000):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(profile_dir, headless=False, user_agent=UA)
        page = ctx.new_page()
        page.goto(PAGES[position], wait_until="domcontentloaded", timeout=timeout)
        try:
            page.wait_for_selector("table.sub-table", timeout=15000)
        except Exception:
            pass
        html = page.content()
        ctx.close()
    return parse_table(html, position), html


def fetch_file(position: str, html_dir: str):
    d = Path(html_dir)
    for cand in (f"{position.lower()}.html", f"{position.lower()}.htm",
                 f"subvertadown-{position.lower()}.html"):
        f = d / cand
        if f.exists():
            html = f.read_text(encoding="utf-8", errors="ignore")
            return parse_table(html, position), html
    raise FileNotFoundError(
        f"No saved HTML for {position} in {d} (expected {position.lower()}.html)")


def fetch(position: str, cfg: dict, debug_dir: str | None = None):
    """Fetch one position using the configured mode, falling back to saved files."""
    mode = (cfg.get("mode") or "cookies").lower()
    order = {
        "cookies": ["cookies", "files"],
        "playwright": ["playwright", "files"],
        "files": ["files"],
    }.get(mode, ["cookies", "files"])

    errors = []
    for m in order:
        try:
            if m == "cookies":
                if not cfg.get("cookie"):
                    raise RuntimeError("no subvertadown.cookie configured")
                rows, html = fetch_cookies(position, cfg["cookie"])
            elif m == "playwright":
                rows, html = fetch_playwright(position, cfg.get("profile_dir", ".pw-profile"))
            else:
                rows, html = fetch_file(position, cfg.get("html_dir", "saved_html"))

            if debug_dir:
                Path(debug_dir).mkdir(parents=True, exist_ok=True)
                (Path(debug_dir) / f"subvertadown-{position.lower()}.html").write_text(
                    html, encoding="utf-8")
            if _looks_paywalled(html, rows):
                raise RuntimeError(
                    f"only {len(rows)} {position} rows -- session looks logged out or paywalled")
            return rows, m
        except Exception as e:
            errors.append(f"{m}: {e}")
    raise RuntimeError(f"Subvertadown {position} failed -> " + " | ".join(errors))
