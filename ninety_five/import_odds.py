"""Read odds from a Danske Spil match page (sent by the "→ ninety-five" bookmark)
and find the match on the board.

The bookmark sends the page's visible text and title. Danske Spil's markup uses
generated class names that change between releases, so we parse the text,
which is laid out as a heading followed by label/price lines, e.g.

    Kampvinder / 1 / 2,70 / X / 3,40 / 2 / 2,60
    Begge hold scorer / Ja / 1,77 / Nej / 1,95
    Antal mål - over/under / O / 2.5 / 1,98 / U / 2.5 / 1,77 ...
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
import json

# The last imported pages, kept so readers for new page types (NFL, NBA) can
# be written and tested against real Danske Spil text. Local only (data/).
IMPORT_DIR = Path(__file__).resolve().parent.parent / "data" / "imports"
KEEP_IMPORTS = 20


def keep_page(text: str, title: str) -> None:
    """Save an imported page's text; only the newest KEEP_IMPORTS are kept."""
    IMPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    (IMPORT_DIR / f"{stamp}.json").write_text(json.dumps({"title": title, "text": text}, ensure_ascii=False))
    for old in sorted(IMPORT_DIR.glob("*.json"))[:-KEEP_IMPORTS]:
        old.unlink()

PRICE = re.compile(r"^\d+,\d+$|^\d+\.\d{2}$")
LINE = re.compile(r"^\d+(?:[.,]5)$")

# Danish names of national teams -> the INTL data set's English names
DANISH_COUNTRIES = {
    "danmark": "denmark", "sverige": "sweden", "norge": "norway", "finland": "finland", "island": "iceland",
    "faeroerne": "faroe islands", "faeroeerne": "faroe islands", "tyskland": "germany", "frankrig": "france",
    "spanien": "spain", "italien": "italy", "england": "england", "skotland": "scotland", "wales": "wales",
    "nordirland": "northern ireland", "irland": "republic of ireland", "holland": "netherlands",
    "nederlandene": "netherlands", "belgien": "belgium", "schweiz": "switzerland", "oestrig": "austria",
    "polen": "poland", "tjekkiet": "czech republic", "slovakiet": "slovakia", "ungarn": "hungary",
    "kroatien": "croatia", "serbien": "serbia", "slovenien": "slovenia", "bosnien hercegovina": "bosnia and herzegovina",
    "montenegro": "montenegro", "albanien": "albania", "nordmakedonien": "north macedonia", "graekenland": "greece",
    "tyrkiet": "turkey", "cypern": "cyprus", "rumaenien": "romania", "bulgarien": "bulgaria", "ukraine": "ukraine",
    "hviderusland": "belarus", "rusland": "russia", "litauen": "lithuania", "letland": "latvia", "estland": "estonia",
    "georgien": "georgia", "armenien": "armenia", "aserbajdsjan": "azerbaijan", "kasakhstan": "kazakhstan",
    "moldova": "moldova", "luxembourg": "luxembourg", "andorra": "andorra", "malta": "malta",
    "liechtenstein": "liechtenstein", "san marino": "san marino", "gibraltar": "gibraltar", "kosovo": "kosovo",
    "israel": "israel", "portugal": "portugal", "brasilien": "brazil", "argentina": "argentina",
    "uruguay": "uruguay", "colombia": "colombia", "chile": "chile", "peru": "peru", "ecuador": "ecuador",
    "paraguay": "paraguay", "bolivia": "bolivia", "venezuela": "venezuela", "mexico": "mexico", "usa": "united states",
    "canada": "canada", "japan": "japan", "sydkorea": "south korea", "kina": "china", "australien": "australia",
    "marokko": "morocco", "egypten": "egypt", "nigeria": "nigeria", "ghana": "ghana", "senegal": "senegal",
    "elfenbenskysten": "ivory coast", "cameroun": "cameroon", "algeriet": "algeria", "tunesien": "tunisia",
    "sydafrika": "south africa", "saudi arabien": "saudi arabia", "iran": "iran", "irak": "iraq", "qatar": "qatar", "indien": "india",
    "new zealand": "new zealand", "jamaica": "jamaica", "haiti": "haiti",
    "bosnien og hercegovina": "bosnia and herzegovina", "forenede arabiske emirater": "united arab emirates",
    "kap verde": "cape verde", "aekvatorialguinea": "equatorial guinea",
    "centralafrikanske republik": "central african republic", "den centralafrikanske republik": "central african republic",
    "etiopien": "ethiopia", "libyen": "libya", "mauretanien": "mauritania", "sydsudan": "south sudan",
    "comorerne": "comoros", "kirgisistan": "kyrgyzstan", "syrien": "syria", "kinesisk taipei": "taiwan",
    "trinidad og tobago": "trinidad and tobago", "antigua og barbuda": "antigua and barbuda",
    "saint kitts og nevis": "saint kitts and nevis", "saint vincent og grenadinerne": "saint vincent and the grenadines",
    "fransk guyana": "french guiana", "caymanoeerne": "cayman islands", "kamerun": "cameroon",
    "dominikanske republik": "dominican republic", "den dominikanske republik": "dominican republic",
    "moldavien": "moldova", "swaziland": "eswatini", "usbekistan": "uzbekistan", "indonesien": "indonesia",
    "madagaskar": "madagascar", "palaestina": "palestine", "surinam": "suriname", "libanon": "lebanon",
}
# Whole club names (folded) that word-by-word matching can't reach.
CLUB_NAMES = {
    "sporting clube de portugal": "sporting lisbon",  # word-mapping "portugal" would break the country
    "hapoel be er": "hapoel beer sheva",
    "union sg": "union st gilloise", "royale union sg": "union st gilloise",
    "hapoel be er sheva": "hapoel beer sheva", "nott m forest": "nottingham forest",
    # short names that also fit a neighbour ("Bayern" ~ "Bayer", "Paris SG" ⊃ "Paris FC")
    "bayern": "bayern munchen", "paris sg": "paris saint germain", "inter milan": "internazionale milano",
    "celta vigo b": "celta b",
}
# Danish club names on Danske Spil -> the model's (football-data.co.uk) names
# (word by word; also used for the Europa and Conference League names)
DANISH_CLUBS = {"koebenhavn": "copenhagen", "agf": "aarhus", "crvena": "red", "zvezda": "star", "roede": "red", "stjerne": "star", "ob": "odense",
                "basaksehir": "buyuksehyr", "saint": "st", "cp": "lisbon",  # Sporting CP = Sp Lisbon
                "lissabon": "lisbon", "sint": "st", "prag": "praha", "prague": "praha", "kreta": "crete",
                # nicknames and abbreviations
                "fck": "copenhagen", "fcm": "midtjylland", "aab": "aalborg", "spurs": "tottenham",
                "wolves": "wolverhampton", "bvb": "dortmund", "gladbach": "monchengladbach", "hsv": "hamburger",
                "utd": "united", "sheff": "sheffield", "nottm": "nottingham", "weds": "wednesday", "wed": "wednesday",
                "rvs": "rovers", "rennais": "rennes"}
STOPWORDS = {"fc", "afc", "cf", "sk", "kv", "sc", "ac", "as", "fk", "club", "de", "the", "cd", "sv", "vfb", "vfl", "ss",
             "rc", "rcd", "pae"}


def _fold(s: str) -> str:
    s = s.replace("ø", "oe").replace("Ø", "Oe").replace("æ", "ae").replace("Æ", "Ae").replace("å", "aa").replace("Å", "Aa")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _tokens(name: str) -> list[str]:
    folded = _fold(name)
    folded = DANISH_COUNTRIES.get(folded) or CLUB_NAMES.get(folded, folded)
    words = [DANISH_CLUBS.get(t, t) for t in folded.split()]
    return [t for t in words if t not in STOPWORDS] or words


def _word_match(ds: str, board: str) -> bool:
    """A Danske Spil word matches a board word if it starts it ("Man" ->
    "Manchester") or is a near spelling of it ("Shaktar" ~ "Shakhtar")."""
    if board.startswith(ds):
        return True
    return len(ds) >= 4 and ds[0] == board[0] and SequenceMatcher(None, ds, board).ratio() >= 0.8


def same_team(danskespil: str, board: str) -> bool:
    """Danske Spil uses short names ("Blackburn", "Man United", "Atl. Madrid").
    Every one of its words must start a different word of the board name, or
    be the initials of consecutive words ("Paris SG" -> "Saint-Germain")."""
    ds, bd = _tokens(danskespil), _tokens(board)
    if not ds or not bd:
        return False
    used: set[int] = set()
    for t in ds:
        hit = next((i for i, b in enumerate(bd) if i not in used and _word_match(t, b)), None)
        if hit is None and len(t) > 1:
            for i in range(len(bd) - len(t) + 1):
                span = range(i, i + len(t))
                if not used & set(span) and "".join(bd[j][0] for j in span) == t:
                    used |= set(span)
                    hit = -1
                    break
        if hit is None:
            return False
        if hit >= 0:
            used.add(hit)
    return True


# Words shared by many clubs: matching on these alone would mix up matches.
COMMON_WORDS = {"city", "united", "town", "county", "athletic", "rovers", "wanderers", "albion", "real",
                "sport", "sports", "saint", "st", "racing", "olympique", "dynamo", "dinamo", "republic", "islands",
                "north", "south", "new", "arab", "emirates"}


def _shares_word(danskespil: str, board: str) -> bool:
    bd = [b for b in _tokens(board) if b not in COMMON_WORDS]
    return any(_word_match(t, b) for t in _tokens(danskespil) if t not in COMMON_WORDS for b in bd)


def _same(danskespil: str, board: str) -> bool:
    """same_team either way round: the board's short football-data.co.uk names
    ("Betis", "Gent") sit inside Danske Spil's longer ones ("Real Betis")."""
    return same_team(danskespil, board) or same_team(board, danskespil)


def _exact(danskespil: str, board: str) -> bool:
    return _tokens(danskespil) == _tokens(board)


def find_match(home: str, away: str, board: list[dict]) -> dict | None:
    """The board match for a Danske Spil match: both teams matching, or failing
    that, one team matching and the other sharing a word (Danske Spil uses some
    Danish club names, e.g. "Sporting Lissabon" for Sporting Clube de Portugal).
    Only a single unambiguous match counts."""
    both = [m for m in board if _same(home, m["home"]) and _same(away, m["away"])]
    if not both:
        both = [m for m in board
                if (_same(home, m["home"]) and _shares_word(away, m["away"]))
                or (_same(away, m["away"]) and _shares_word(home, m["home"]))]
    # Several pairs fit ("Barcelona" is in "Espanyol de Barcelona", "Island"
    # is a near spelling of Ireland): keep those whose names fit exactly.
    if len({(m["home"], m["away"]) for m in both}) > 1:
        score = lambda m: (_exact(home, m["home"]) + _exact(away, m["away"]))
        best = max(map(score, both))
        both = [m for m in both if score(m) == best]
    # The same two teams can meet twice in the window (e.g. two friendlies):
    # take the next one. Different pairs of teams are ambiguous.
    if len({(m["home"], m["away"]) for m in both}) != 1:
        return None
    return min(both, key=lambda m: m.get("kickoff", ""))


def _price(s: str) -> float | None:
    s = s.strip()
    if not PRICE.match(s):
        return None
    v = float(s.replace(",", "."))
    return v if v > 1 else None


def _after(lines: list[str], heading: str) -> int | None:
    try:
        return lines.index(heading) + 1
    except ValueError:
        return None


def parse_page(text: str, title: str = "") -> dict:
    """Teams and the prices of the markets the app prices, from a match page."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    home = away = None
    head = title.split(" | ")[0]
    if " - " in head:
        home, away = (x.strip() for x in head.split(" - ", 1))
    odds: dict[str, float] = {}

    i = _after(lines, "Kampvinder")
    if i is not None and i + 5 < len(lines) and [lines[i], lines[i + 2], lines[i + 4]] == ["1", "X", "2"]:
        for k, j in (("1", i + 1), ("X", i + 3), ("2", i + 5)):
            if (p := _price(lines[j])) is not None:
                odds[k] = p

    i = _after(lines, "Begge hold scorer")
    if i is not None and i + 3 < len(lines) and [lines[i], lines[i + 2]] == ["Ja", "Nej"]:
        for k, j in (("BTTS Yes", i + 1), ("BTTS No", i + 3)):
            if (p := _price(lines[j])) is not None:
                odds[k] = p

    i = _after(lines, "Antal mål - over/under")
    while i is not None and i + 2 < len(lines) and lines[i] in ("O", "U") and LINE.match(lines[i + 1]):
        p = _price(lines[i + 2])
        line = lines[i + 1].replace(",", ".")
        if p is not None:
            odds[f"{'Over' if lines[i] == 'O' else 'Under'} {line}"] = p
        i += 3

    return {"home": home, "away": away, "odds": odds}


# ------------------------------------------------- NFL, NBA, college football
# The page's breadcrumb ("Sportsgrene / Amerikansk fodbold / USA / USA NFL")
# names the sport and league. Markets (1 = the team Danske Spil lists first):
#   Kampvinder (Inkl. OT) / 1 / 1,80 / 2 / 2,05                 -> ML 1, ML 2
#   Handicap (inkl. OT) / Team 1 / -2.5 / 1,95 / Team 2 / +2.5 / 1,80 / -2.0 / ...
#                                     (a ladder of lines, team 1 then team 2)
#   Antal point O/U (Inkl. OT) / O / 41.5 / 1,80 / U / 41.5 / 1,93 / ...
# "Kampvinder (Eks. OT)" (a tie possible) and first-half/quarter lines are
# different bets and are left out.
POINT = re.compile(r"^[+-]?\d+(?:\.\d+)?$")


def _breadcrumb(lines: list[str]) -> list[str]:
    for i in range(len(lines) - 1):
        if lines[i] == "Sportsgrene" and lines[i + 1] == "/":
            parts, j = [], i + 2
            while j < len(lines):
                parts.append(lines[j])
                if j + 1 < len(lines) and lines[j + 1] == "/":
                    j += 2
                else:
                    break
            return parts
    return []


def _us_sport(lines: list[str], title: str) -> str | None:
    crumbs = _breadcrumb(lines)
    if crumbs:
        sport, league = crumbs[0].lower(), crumbs[-1]
    else:  # no breadcrumb: the title's middle part ("USA NFL Odds")
        parts = title.split(" | ")
        sport, league = "", parts[1] if len(parts) > 1 else ""
    words = league.upper().split()
    if "NFL" in words and sport in ("", "amerikansk fodbold"):
        return "NFL"
    if "NBA" in words and sport in ("", "basketball"):
        return "NBA"
    if sport == "amerikansk fodbold" and ("NCAA" in words or "NCAAF" in words or "COLLEGE" in words):
        return "NCAAF"
    return None


def parse_us_page(text: str, title: str = "") -> dict:
    """{"sport": "NFL" | "NBA" | "NCAAF" | None, "home", "away", "odds"}: the
    teams in Danske Spil's order (1, 2) and the moneyline, spread and total
    prices named as in us_sports.py."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    head = title.split(" | ")[0]
    home = away = None
    if " - " in head:
        home, away = (x.strip() for x in head.split(" - ", 1))
    odds: dict[str, float] = {}
    for k, h in enumerate(lines):
        h = h.lower()
        i = k + 1
        if h == "kampvinder (inkl. ot)":
            if i + 3 < len(lines) and lines[i] == "1" and lines[i + 2] == "2":
                p1, p2 = _price(lines[i + 1]), _price(lines[i + 3])
                if p1 and p2:
                    odds.setdefault("ML 1", p1)
                    odds.setdefault("ML 2", p2)
        elif h == "handicap" or h.startswith("handicap ("):
            got = []
            while i < len(lines):
                if lines[i] in (home, away):
                    i += 1
                elif i + 1 < len(lines) and POINT.match(lines[i]) and (p := _price(lines[i + 1])):
                    got.append((float(lines[i]), p))
                    i += 2
                else:
                    break
            for (l1, p1), (l2, p2) in zip(got[0::2], got[1::2]):
                if l1 == -l2:
                    odds.setdefault(f"Spread 1 {l1:+g}", p1)
                    odds.setdefault(f"Spread 2 {l2:+g}", p2)
        elif h.startswith("antal point o/u") and "halvleg" not in h and "quarter" not in h:
            while i + 2 < len(lines) and lines[i] in ("O", "U") and POINT.match(lines[i + 1]):
                if (p := _price(lines[i + 2])) is not None:
                    odds.setdefault(f"{'Over' if lines[i] == 'O' else 'Under'} {float(lines[i + 1]):g}", p)
                i += 3
    return {"sport": _us_sport(lines, title), "home": home, "away": away, "odds": odds}


def _swap(market: str) -> str:
    """The same bet with teams 1 and 2 swapped."""
    if market in ("ML 1", "ML 2"):
        return "ML 2" if market == "ML 1" else "ML 1"
    if market.startswith("Spread "):
        _, side, line = market.split()
        return f"Spread {'2' if side == '1' else '1'} {line}"
    return market


def find_us_game(parsed: dict, games: list[dict]) -> tuple[dict | None, dict[str, float]]:
    """The stored game for a parsed page, and its odds named from that game's
    side (1 = home). Danske Spil may list either team first. Both teams must
    match; of several fits, the one whose names fit closest ("Michigan" is
    Michigan Wolverines, not Michigan State)."""
    a, b = parsed.get("home"), parsed.get("away")
    if not a or not b:
        return None, {}
    fits = []
    for g in games:
        for first, second, swapped in ((g["home"], g["away"], False), (g["away"], g["home"], True)):
            if _same(a, first) and _same(b, second):
                gap = sum(abs(len(_tokens(x)) - len(_tokens(y))) for x, y in ((a, first), (b, second)))
                fits.append((gap, g, swapped))
    if not fits:
        return None, {}
    best = min(f[0] for f in fits)
    fits = [f for f in fits if f[0] == best]
    if len({(f[1]["home"], f[1]["away"]) for f in fits}) != 1:
        return None, {}
    _, g, swapped = min(fits, key=lambda f: f[1].get("kickoff", ""))
    odds = parsed["odds"]
    return g, ({_swap(m): p for m, p in odds.items()} if swapped else dict(odds))
