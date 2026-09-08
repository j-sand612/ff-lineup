"""Late-Round "15 Transactions" newsletter.

The newsletter is a Mailchimp campaign, and every transaction is published as
its own heading in the form `<Action> <Player>`:

    <h2>Add Malik Willis</h2>
    <h2>Buy Luther Burden</h2>
    <h2>Sell Trey McBride</h2>

so parsing is deterministic - no language model needed. You supply the
"View this email in your browser" URL; the parsed result is cached per week so
one paste covers every run that week.

This is a *second opinion only*. Nothing here feeds the lineup decision, the
points threshold, or the Hold rule - it annotates the report and adds its own
section, so a newsletter that is stale, missing, or unparseable can never
change what the tool recommends.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

import requests
from bs4 import BeautifulSoup

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

ACTIONS = ("ADD", "DROP", "BUY", "SELL", "HOLD", "STASH")
_ACTION_RE = re.compile(
    r"^(%s)\s+(?:the\s+)?(.+?)[\s.!]*$" % "|".join(ACTIONS), re.I)
BLURB_MAX = 240


@dataclass
class Transaction:
    action: str                 # ADD / DROP / BUY / SELL / HOLD / STASH
    raw_name: str               # as written in the newsletter
    order: int                  # 1..15, his ordering
    blurb: str = ""             # short rationale, for context in the report
    uid: str = ""               # resolved player key, filled by resolve()
    player: object = field(default=None, repr=False)
    owner: str = ""             # fantasy team holding them, or "free agent"

    @property
    def is_trade(self) -> bool:
        return self.action in ("BUY", "SELL")

    @property
    def label(self) -> str:
        return f"LR #{self.order} {self.action.title()}"


# ------------------------------------------------------------------ fetching

def strip_tracking(url: str) -> str:
    """Drop Mailchimp's per-recipient id. It identifies the subscriber and is
    not needed to read the page, so it never gets stored or logged."""
    parts = urlsplit(url.strip())
    q = [(k, v) for k, v in parse_qsl(parts.query) if k.lower() not in ("e", "c")]
    return urlunsplit((parts.scheme or "https", parts.netloc, parts.path,
                       urlencode(q), ""))


def fetch_html(url: str, timeout=30) -> str:
    r = requests.get(strip_tracking(url), headers={"User-Agent": UA}, timeout=timeout)
    r.raise_for_status()
    return r.text


# ------------------------------------------------------------------ parsing

def _text_lines(soup) -> list[str]:
    for bad in soup(["script", "style"]):
        bad.decompose()
    lines = [re.sub(r"\s+", " ", ln).strip()
             for ln in soup.get_text("\n", strip=True).splitlines()]
    return [ln for ln in lines if ln]


def parse(html: str) -> list[Transaction]:
    """Pull the transaction list out of a newsletter page."""
    soup = BeautifulSoup(html, "lxml")
    headings = []
    for h in soup.find_all(["h1", "h2", "h3", "h4"]):
        t = re.sub(r"\s+", " ", h.get_text(" ", strip=True)).strip()
        if t:
            headings.append(t)

    lines = _text_lines(soup)
    # Heading text -> its position in the flattened text, so the paragraphs
    # under each heading can be picked up as the rationale.
    idx = {}
    for i, ln in enumerate(lines):
        for h in headings:
            if h not in idx and ln == h:
                idx[h] = i
    ordered = sorted(((i, h) for h, i in idx.items()))

    out: list[Transaction] = []
    for n, (pos, head) in enumerate(ordered):
        m = _ACTION_RE.match(head)
        if not m:
            continue
        stop = next((p for p, _ in ordered if p > pos), len(lines))
        blurb = " ".join(lines[pos + 1:stop]).strip()
        if len(blurb) > BLURB_MAX:
            blurb = blurb[:BLURB_MAX].rsplit(" ", 1)[0] + "..."
        out.append(Transaction(
            action=m.group(1).upper(),
            raw_name=m.group(2).strip(),
            order=len(out) + 1,
            blurb=blurb,
        ))
    return out


def week_of(html: str, url: str = "") -> int | None:
    """The week the newsletter is for, so a stale link can be spotted."""
    for hay in (url, html[:4000]):
        m = re.search(r"week[\s\-_]*(\d{1,2})", hay or "", re.I)
        if m:
            wk = int(m.group(1))
            if 1 <= wk <= 18:
                return wk
    return None


# ------------------------------------------------------------------ resolving

def resolve(txns: list[Transaction], universe: dict, owners: dict) -> list[Transaction]:
    """Match each name to a real player, and note who holds them.

    `universe` is every player in the league - all rosters plus free agents -
    because Buy/Sell targets sit on other managers' teams and would otherwise
    never resolve.
    """
    from .names import fuzzy_match, norm_name, team_abbrev

    by_norm: dict[str, list] = {}
    for p in universe.values():
        by_norm.setdefault(norm_name(p.name), []).append(p)

    for t in txns:
        p = None
        if re.search(r"defense|d/?st\b", t.raw_name, re.I):
            ab = team_abbrev(t.raw_name)
            p = universe.get(f"dst:{ab}") if ab else None
        if p is None:
            cands = by_norm.get(norm_name(t.raw_name)) or []
            p = cands[0] if cands else None
        if p is None:
            for pos in ("RB", "WR", "TE", "QB", "K"):
                k, _score = fuzzy_match(f"{pos}:{norm_name(t.raw_name)}", universe)
                if k:
                    p = universe[k]
                    break
        if p is not None:
            t.player = p
            t.uid = p.uid
            t.owner = owners.get(p.uid, "free agent")
    return txns


def by_uid(txns: list[Transaction]) -> dict[str, Transaction]:
    return {t.uid: t for t in txns if t.uid}


# ------------------------------------------------------------------ caching

def cache_path(base: Path, week: int) -> Path:
    return Path(base) / f"week{week:02d}.json"


def save_cache(path: Path, url: str, week: int | None, txns: list[Transaction]):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "url": strip_tracking(url),
        "week": week,
        "fetched": datetime.now().isoformat(timespec="seconds"),
        "items": [{k: v for k, v in asdict(t).items()
                   if k in ("action", "raw_name", "order", "blurb")} for t in txns],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_cache(path: Path) -> tuple[list[Transaction], dict]:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    txns = [Transaction(**it) for it in d.get("items", [])]
    return txns, d


def get(url_or_cache: str, week: int, cache_dir: Path) -> tuple[list[Transaction], str, list[str]]:
    """Return (transactions, description, warnings).

    A URL fetches and caches. The sentinel "cache" reuses this week's saved
    copy. Any failure returns an empty list -- the report then renders exactly
    as it would with no newsletter at all.
    """
    warnings: list[str] = []
    path = cache_path(cache_dir, week)

    if url_or_cache == "cache":
        if not path.exists():
            return [], "", [f"No cached newsletter for week {week} "
                            f"({path}). Pass --newsletter <url> once to fetch it."]
        txns, meta = load_cache(path)
        return txns, f"cached {path.name} (fetched {meta.get('fetched', '?')})", warnings

    try:
        html = fetch_html(url_or_cache)
    except Exception as e:
        return [], "", [f"Could not fetch the newsletter: {e}"]

    txns = parse(html)
    if not txns:
        return [], "", ["Fetched the newsletter but found no transactions in it - "
                        "the layout may have changed, or that URL may not be a "
                        "'15 Transactions' issue."]

    nl_week = week_of(html, url_or_cache)
    if nl_week is not None and nl_week != week:
        warnings.append(f"That newsletter is for week {nl_week}, but this report "
                        f"is week {week}. Using it anyway - check the link.")
    save_cache(path, url_or_cache, nl_week, txns)
    return txns, f"{len(txns)} transactions from {strip_tracking(url_or_cache)}", warnings
