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
from difflib import SequenceMatcher

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
}
# Danish club names on Danske Spil -> the model's (football-data.co.uk) names
# (word by word; also used for the Europa and Conference League names)
DANISH_CLUBS = {"koebenhavn": "copenhagen", "agf": "aarhus", "crvena": "red", "zvezda": "star", "roede": "red", "stjerne": "star",
                "basaksehir": "buyuksehyr", "saint": "st", "cp": "lisbon"}  # Sporting CP = Sp Lisbon
STOPWORDS = {"fc", "afc", "cf", "sk", "kv", "sc", "ac", "as", "fk", "club", "de", "the", "cd", "sv", "vfb", "vfl", "ss"}


def _fold(s: str) -> str:
    s = s.replace("ø", "oe").replace("Ø", "Oe").replace("æ", "ae").replace("Æ", "Ae").replace("å", "aa").replace("Å", "Aa")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _tokens(name: str) -> list[str]:
    folded = _fold(name)
    folded = DANISH_COUNTRIES.get(folded, folded)
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
