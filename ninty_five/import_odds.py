"""Read odds from a Danske Spil match page (sent by the "→ ninty-five" bookmark)
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
    "sydafrika": "south africa", "saudi arabien": "saudi arabia", "iran": "iran", "irak": "iraq", "qatar": "qatar",
}
STOPWORDS = {"fc", "afc", "cf", "sk", "kv", "sc", "ac", "as", "fk", "club", "de", "the", "cd", "sv", "vfb", "vfl", "ss"}


def _fold(s: str) -> str:
    s = s.replace("ø", "oe").replace("Ø", "Oe").replace("æ", "ae").replace("Æ", "Ae").replace("å", "aa").replace("Å", "Aa")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _tokens(name: str) -> list[str]:
    folded = _fold(name)
    folded = DANISH_COUNTRIES.get(folded, folded)
    return [t for t in folded.split() if t not in STOPWORDS] or folded.split()


def same_team(danskespil: str, board: str) -> bool:
    """Danske Spil uses short names ("Blackburn", "Man United", "Atl. Madrid").
    Every one of its words must start a different word of the board name, or
    be the initials of consecutive words ("Paris SG" -> "Saint-Germain")."""
    ds, bd = _tokens(danskespil), _tokens(board)
    if not ds or not bd:
        return False
    used: set[int] = set()
    for t in ds:
        hit = next((i for i, b in enumerate(bd) if i not in used and b.startswith(t)), None)
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


def find_match(home: str, away: str, board: list[dict]) -> dict | None:
    """The board match whose home and away teams both match, if exactly one."""
    hits = [m for m in board if same_team(home, m["home"]) and same_team(away, m["away"])]
    return hits[0] if len(hits) == 1 else None


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
