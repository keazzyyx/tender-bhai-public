"""Keyword matching for M.J. International's product range."""

from __future__ import annotations

import re
from collections.abc import Iterable

CRICKET = [
    "cricket",
    "cricket bat",
    "cricket ball",
    "batting pad",
    "batting glove",
    "wicket keeping",
    "stumps",
    "bails",
    "cricket kit",
    "cricket net",
    "leather ball",
    "tennis ball cricket",
    "abdominal guard",
    "thigh guard",
    "helmet",
]
CLOTHING = [
    "sports clothing",
    "sports wear",
    "sportswear",
    "sports uniform",
    "uniform",
    "tracksuit",
    "track suit",
    "jersey",
    "t-shirt",
    "t shirt",
    "tshirt",
    "shorts",
    "sports kit",
    "kit bag",
    "cap",
    "hat",
    "socks",
    "jacket",
    "lower",
    "trouser",
]
TRAINING = [
    "training equipment",
    "agility ladder",
    "cone",
    "hurdle",
    "bowling machine",
    "bowling machine ball",
    "practice net",
    "net",
    "gym equipment",
    "fitness equipment",
    "multi gym",
    "treadmill",
    "dumbbell",
    "boxing",
    "punching bag",
    "hockey",
    "athletics",
    "javelin",
    "discus",
    "shot put",
    "football",
    "volleyball",
    "basketball",
    "badminton",
    "kabaddi",
    "kho kho",
    "mat",
    "yoga mat",
    "skipping rope",
    "medicine ball",
]
GENERIC = [
    "sports goods",
    "sports good",
    "sports material",
    "sports equipment",
    "sports item",
    "sports articles",
    "sporting goods",
    "school sports kit",
    "physical education",
    "games material",
    "games equipment",
    "play material",
    "playing material",
    "sports accessories",
    "khel samagri",
    "khel",
    "sport",
]

ALL_KEYWORDS: list[str] = CRICKET + CLOTHING + TRAINING + GENERIC

# Short, portal-search-friendly queries (used where a portal offers keyword search).
SEARCH_QUERIES: list[str] = [
    "cricket",
    "sports goods",
    "sports equipment",
    "sports material",
    "sports kit",
    "tracksuit",
    "jersey",
    "uniform",
    "gym equipment",
    "fitness equipment",
    "physical education",
    "bowling machine",
    "hockey",
    "boxing",
    "athletics",
]

# Words that are so generic we only accept them when they appear with a sport context.
_WEAK = {"net", "cap", "hat", "mat", "cone", "lower", "trouser", "uniform", "jacket",
         "socks", "shorts", "helmet"}  # fmt: skip

_SPORT_CONTEXT = re.compile(
    r"\b(sport|sports|cricket|game|games|athlet|gym|fitness|physical education|"
    r"stadium|coach|player|team|tournament|pe\b|khel|football|hockey|kabaddi)",
    re.IGNORECASE,
)

# Things that match a weak keyword but are clearly not for us.
_NEGATIVE = re.compile(
    r"\b(mosquito net|fishing net|safety net|wire net|netting|cap(?:acitor|acity|ital|sule)|"
    r"hat(?:ch|e)|mat(?:erial|rix|tress|ic)|cone crusher|traffic cone|road cone|"
    r"police uniform|nurse uniform|security uniform|lower (?:division|court)|"
    r"doormat|floor mat|rubber mat for|welding helmet|safety helmet|industrial helmet)",
    re.IGNORECASE,
)


def _compile(keyword: str) -> re.Pattern[str]:
    escaped = re.escape(keyword).replace(r"\ ", r"[\s\-]*")
    return re.compile(rf"\b{escaped}s?\b", re.IGNORECASE)


_COMPILED: dict[str, re.Pattern[str]] = {kw: _compile(kw) for kw in ALL_KEYWORDS}


def matched_keywords(text: str, extra_keywords: Iterable[str] = ()) -> list[str]:
    """Return the keywords found in text, honouring weak-word context rules."""
    if not text:
        return []
    cleaned = _NEGATIVE.sub(" ", text)
    found: list[str] = []
    has_context = bool(_SPORT_CONTEXT.search(cleaned))
    for keyword, pattern in _COMPILED.items():
        if pattern.search(cleaned):
            if keyword in _WEAK and not has_context:
                continue
            found.append(keyword)
    for keyword in extra_keywords:
        keyword = keyword.strip()
        if keyword and _compile(keyword).search(cleaned) and keyword not in found:
            found.append(keyword)
    return found


def matches(text: str, extra_keywords: Iterable[str] = (), ignore_keywords: Iterable[str] = ()) -> bool:
    if any(_compile(k).search(text or "") for k in ignore_keywords if k.strip()):
        return False
    return bool(matched_keywords(text, extra_keywords))
