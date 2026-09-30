"""Work out which movies belong to Plex *smart* collections.

A smart collection has no stored member list - Plex keeps a saved filter (the collection's
`pv:uri`, e.g. `...?type=1&director=29969`) and re-runs it whenever the collection is shown.
This module re-runs those filters over the exported movies.

Plex also records how many members the collection had (`at:childCount`), so every result is
checked against that number: a collection is only expanded when the counts agree exactly.
Anything Projectionist can't evaluate faithfully (an unknown field or operator, relative dates,
a top-N limit, no recorded count...) is listed, but left unexpanded rather than guessed.

Filter syntax, as Plex writes it - `field` + operator + value:
    =    contains (text fields) / is (everything else)     !=   doesn't contain / is not
    ==   is exactly (text)                                  !==  is not exactly (text)
    <=   begins with (text)                                 >=   ends with (text)
    <<=  less than (numbers, strict)                        >>=  greater than (numbers, strict)
Comma-separated values mean "any of"; `and=1` / `or=1` join conditions (and is the default);
`push=1` ... `pop=1` wrap a group in brackets. Operators may be percent-encoded (`studio%3D=x`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import unquote_plus

TAG_FIELDS = {   # filter field -> readable name; the value is a tags.id
    "actor": "Actor", "director": "Director", "writer": "Writer", "producer": "Producer", "genre": "Genre",
    "country": "Country", "collection": "Collection", "label": "Label", "mood": "Mood", "style": "Style",
}
TEXT_FIELDS = {"studio": "Studio", "editionTitle": "Edition", "title": "Title"}   # case-insensitive text
LIST_FIELDS = {"contentRating": "Content rating", "resolution": "Resolution",
               "audioLanguage": "Audio language", "subtitleLanguage": "Subtitle language"}
NUMBER_FIELDS = {"year": "Year", "userRating": "Owner rating"}
OWNER_FIELDS = {"unwatched", "userRating"}   # depend on whose watch state - Plex's count is the owner's

RESOLUTION_NAMES = {"4k": "4K", "1080": "1080p", "720": "720p", "576": "576p", "480": "480p", "sd": "SD"}

_OPS = {"=", "!=", "==", "!==", "<=", ">=", "<<=", ">>="}
_ID = re.compile(r"[0-9]{1,18}")   # plain ASCII digits that fit an SQLite integer


class Unsupported(Exception):
    """The filter uses something Projectionist can't evaluate faithfully."""


@dataclass
class MovieFacts:
    """What the filters look at, for one movie."""
    library_id: int
    tag_ids: set[int]
    studio: str
    edition: str
    title: str
    content_rating: str
    year: int | None
    resolutions: set[str]          # Plex resolution classes of every version: '4k', '1080', ... 'sd'
    audio_languages: set[str]      # two-letter codes where known
    subtitle_languages: set[str]
    owner_watched: bool
    owner_rating: float | None


@dataclass
class SmartResult:
    members: list[int] = field(default_factory=list)   # movie ids, when expanded
    expanded: bool = False
    status: str = ""
    filter_text: str = ""


@dataclass
class _Cond:
    field: str
    op: str        # one of _OPS
    value: str


def _split_condition(raw_key: str, raw_value: str) -> _Cond:
    """'studio!' + '=Shaw' -> _Cond('studio', '!==', 'Shaw') - Plex's operator is spread over both sides."""
    key = unquote_plus(raw_key)
    extra = 0
    while key.endswith("="):               # an encoded '=' belongs to the operator ('studio%3D=x' is '==')
        key, extra = key[:-1], extra + 1
    while raw_value.startswith("="):       # so does a raw one ('studio==x' splits as 'studio' + '=x')
        raw_value, extra = raw_value[1:], extra + 1
    for suffix, base in (("<<", "<<="), (">>", ">>="), ("!", "!="), ("<", "<="), (">", ">=")):
        if key.endswith(suffix):
            key = key[:-len(suffix)]
            break
    else:
        base = "="
    op = base + "=" * extra
    if op not in _OPS:
        raise Unsupported(f"the operator '{op}'")
    if re.search(r"%2c", raw_value, re.I):
        # An encoded comma inside one value - it's unclear whether Plex treats it as a list separator.
        raise Unsupported("a comma inside a value")
    return _Cond(key, op, unquote_plus(raw_value))


def parse(uri: str) -> tuple[int | None, list]:
    """(library id, tokens) from a smart collection's pv:uri. Raises Unsupported for odd operators."""
    lib = re.search(r"/sections/(\d+)/", uri or "")
    query = uri.split("?", 1)[1] if "?" in (uri or "") else ""
    tokens = []
    for piece in query.split("&"):
        if not piece:
            continue
        raw_key, sep, raw_value = piece.partition("=")
        key = unquote_plus(raw_key)
        if key == "type" and unquote_plus(raw_value) != "1":
            raise Unsupported(f"item type {unquote_plus(raw_value)!r} (not movies)")
        if key in ("type", "sort"):
            continue
        if key in ("and", "or", "push", "pop") and sep and raw_value == "1":
            tokens.append(key)
            continue
        tokens.append(_split_condition(raw_key, raw_value))
    return (int(lib.group(1)) if lib else None), tokens


def describe(tokens, tag_names: dict[int, str]) -> str:
    """Readable version of a filter, e.g. 'Director is Alfred Hitchcock and Year before 1977'."""
    out = []
    for t in tokens:
        if t == "push":
            if out and out[-1] not in ("(", "and", "or") and not out[-1].endswith("here)"):
                out.append("and")
            out.append("(")
        elif t == "pop":
            out.append(")")
        elif t in ("and", "or"):
            if out and out[-1] in ("and", "or"):
                # Keep the description faithful to what Plex stored, but point out the oddity.
                out.append(f"(Plex's saved filter repeats '{t}' here)")
            else:
                out.append(t)
        else:
            if out and out[-1] not in ("(", "and", "or") and not out[-1].endswith("here)"):
                out.append("and")
            out.append(_describe_cond(t, tag_names))
    return " ".join(out).replace("( ", "(").replace(" )", ")")


DATE_FIELDS = {"addedAt": "Date added", "originallyAvailableAt": "Release date", "lastViewedAt": "Last played",
               "updatedAt": "Date updated"}
_RELATIVE = re.compile(r"^-(\d+)([smhdwy])$")
_UNITS = {"s": "second", "m": "minute", "h": "hour", "d": "day", "w": "week", "y": "year"}


def _describe_cond(c: _Cond, tag_names) -> str:
    values = c.value.split(",")
    if c.field == "unwatched" and c.value in ("0", "1") and c.op in ("=", "!="):
        watched = (c.value == "1") == (c.op == "!=")
        return "Watched by the owner" if watched else "Unwatched by the owner"
    relative = _RELATIVE.match(c.value)
    if c.field in DATE_FIELDS and relative and c.op in ("<<=", ">>="):   # e.g. addedAt>>=-30d
        n, unit = int(relative.group(1)), _UNITS[relative.group(2)]
        span = f"the last {n} {unit}{'s' if n != 1 else ''}"
        return f"{DATE_FIELDS[c.field]} {'in' if c.op == '>>=' else 'before'} {span}"
    text = c.field in TEXT_FIELDS
    if c.field in TAG_FIELDS:
        label = TAG_FIELDS[c.field]
        shown = [tag_names.get(int(v), f"#{v}") if _ID.fullmatch(v) else v for v in values]
    elif c.field == "resolution":
        label, shown = LIST_FIELDS[c.field], [RESOLUTION_NAMES.get(v.lower(), v) for v in values]
    elif c.field in ("audioLanguage", "subtitleLanguage"):
        from .extract import language_name
        label, shown = LIST_FIELDS[c.field], [language_name(v) or v for v in values]
    else:
        label = (TEXT_FIELDS.get(c.field) or LIST_FIELDS.get(c.field) or NUMBER_FIELDS.get(c.field)
                 or DATE_FIELDS.get(c.field) or c.field[:1].upper() + c.field[1:])
        shown = [f"'{v}'" for v in values] if text else values
    if c.op in ("<=", ">=") and not text:
        # Plex only documents these as 'begins/ends with' for text; don't invent a meaning elsewhere.
        return f"{label} {c.op} {' or '.join(shown)}"
    year = c.field in ("year",) or c.field in DATE_FIELDS
    verb = {"=": "contains" if text else "is", "!=": "doesn't contain" if text else "is not",
            "==": "is", "!==": "is not", "<=": "begins with", ">=": "ends with",
            "<<=": "before" if year else "below", ">>=": "after" if year else "above"}[c.op]
    return f"{label} {verb} {' or '.join(shown)}"


def _norm_lang(code: str) -> str:
    from .extract import _ISO3   # shared language table
    base = code.lower().replace("_", "-").split("-")[0]
    return _ISO3.get(base, base)


def _unsupported_op(c: _Cond):
    return Unsupported(f"the '{c.op}' operator on '{c.field}'")


_SIMPLE_FIELDS = set(TAG_FIELDS) | {"contentRating", "resolution", "audioLanguage", "subtitleLanguage", "unwatched"}


_NUMBER = re.compile(r"-?[0-9]+(?:\.[0-9]+)?")   # plain decimals only - no '1_979', 'nan', 'inf'


def _match(c: _Cond, m: MovieFacts) -> bool:
    values = c.value.split(",")
    negate = c.op in ("!=", "!==")
    if c.field not in NUMBER_FIELDS and c.field not in TEXT_FIELDS and c.field not in _SIMPLE_FIELDS:
        raise Unsupported(f"the '{c.field}' filter")   # name the field, whatever its operator
    if any(v.strip() == "" for v in values):
        raise Unsupported(f"an empty '{c.field}' value")   # does Plex match nothing, or everything?

    if c.field in NUMBER_FIELDS:
        x = m.year if c.field == "year" else m.owner_rating
        if c.op not in ("=", "!=", "<<=", ">>="):
            raise _unsupported_op(c)
        if not all(_NUMBER.fullmatch(v) for v in values):
            raise Unsupported(f"'{c.field}' value {c.value!r}")
        numbers = [float(v) for v in values]
        if c.op in ("<<=", ">>="):   # Plex's <<= and >>= are strict
            if len(numbers) != 1:
                raise Unsupported(f"several '{c.field}' values with '{c.op}'")
            return x is not None and (x < numbers[0] if c.op == "<<=" else x > numbers[0])
        hit = x is not None and any(abs(x - v) < 1e-9 for v in numbers)
        return not hit if negate else hit

    if c.field in TEXT_FIELDS:
        text = {"studio": m.studio, "editionTitle": m.edition, "title": m.title}[c.field].casefold()
        wanted = [v.casefold() for v in values]
        if c.op in ("=", "!="):
            hit = any(v in text for v in wanted)
        elif c.op in ("==", "!=="):
            hit = text in wanted
        elif c.op == "<=":
            hit = any(text.startswith(v) for v in wanted)
        elif c.op == ">=":
            hit = any(text.endswith(v) for v in wanted)
        else:   # '<<=' / '>>=' on text: no documented meaning - never guess
            raise _unsupported_op(c)
        return not hit if negate else hit

    if c.op not in ("=", "!="):
        raise _unsupported_op(c)
    if c.field in TAG_FIELDS:
        if not values or not all(_ID.fullmatch(v) for v in values):
            raise Unsupported(f"'{c.field}' value {c.value!r}")
        hit = any(int(v) in m.tag_ids for v in values)
    elif c.field == "contentRating":
        hit = m.content_rating.casefold() in {v.casefold() for v in values}
    elif c.field == "resolution":
        hit = any(v.lower() in m.resolutions for v in values)
    elif c.field in ("audioLanguage", "subtitleLanguage"):
        have = m.audio_languages if c.field == "audioLanguage" else m.subtitle_languages
        hit = any(_norm_lang(v) in have for v in values)
    else:   # unwatched
        if c.value not in ("0", "1"):
            raise Unsupported(f"'unwatched' value {c.value!r}")
        hit = (not m.owner_watched) if c.value == "1" else m.owner_watched
    return not hit if negate else hit


def _evaluate(tokens, m: MovieFacts) -> bool:
    pos = 0

    def group(depth: int) -> bool:
        nonlocal pos
        result, joiner = None, None
        while pos < len(tokens):
            t = tokens[pos]
            pos += 1
            if t == "pop":
                if depth == 0:
                    raise Unsupported("unbalanced brackets")   # a ')' with no '('
                break
            if t in ("and", "or"):
                if joiner is not None or result is None:
                    raise Unsupported("an unusual filter layout")   # e.g. 'and' twice in a row
                joiner = t
                continue
            value = group(depth + 1) if t == "push" else _match(t, m)
            if result is None:
                result = value
            elif joiner == "or":
                result = result or value
            else:
                result = result and value
            joiner = None
        else:
            if depth > 0:
                raise Unsupported("unbalanced brackets")   # a '(' never closed
        if joiner is not None:
            raise Unsupported("an unusual filter layout")
        if result is None:
            raise Unsupported("an empty bracket group")
        return result

    return group(0)


def referenced_tag_ids(uri: str) -> set[int]:
    """Tag ids a filter mentions (for looking up their names). Never raises."""
    try:
        tokens = parse(uri)[1]
    except Exception:
        return set()
    return {int(v) for t in tokens if isinstance(t, _Cond) and t.field in TAG_FIELDS
            for v in t.value.split(",") if _ID.fullmatch(v)}


def expand(uri: str, plex_count: int | None, movies: dict[int, MovieFacts], tag_names) -> SmartResult:
    """Evaluate one smart collection over `movies`. Never raises - problems become a status."""
    try:
        return _expand(uri, plex_count, movies, tag_names)
    except Exception as exc:   # a hand-edited or corrupt filter mustn't stop the whole export
        return SmartResult(status=f"Not expanded - Plex's saved filter couldn't be read ({type(exc).__name__}).")


def _expand(uri, plex_count, movies, tag_names) -> SmartResult:
    try:
        lib, tokens = parse(uri)
    except Unsupported as exc:
        return SmartResult(status=f"Not expanded - uses {exc}, which Projectionist can't evaluate.")
    result = SmartResult(filter_text=describe(tokens, tag_names) if tokens else "All movies in the library")
    if lib is None:
        result.status = "Not expanded - Plex's saved filter couldn't be read."
        return result
    for t in tokens:
        if isinstance(t, _Cond) and "." in t.field:
            result.status = f"Not expanded - uses '{t.field}', which isn't a movie filter."
            return result
    try:
        # No conditions at all (just a sort order) means every movie in the library.
        members = [mid for mid, m in movies.items()
                   if m.library_id == lib and (not tokens or _evaluate(tokens, m))]
    except Unsupported as exc:
        result.status = f"Not expanded - uses {exc}, which Projectionist can't evaluate."
        return result
    if plex_count is None:
        result.status = ("Not expanded - Plex didn't record how many movies it holds, so the result "
                         "couldn't be checked.")
        return result
    if len(members) != plex_count:
        result.status = (f"Not expanded - the filter gives {len(members)} movies but Plex counted {plex_count}, "
                         "so Projectionist can't reproduce it exactly.")
        return result
    owner_note = " (watch state and ratings as the server owner's)" if any(
        isinstance(t, _Cond) and t.field in OWNER_FIELDS for t in tokens) else ""
    result.members, result.expanded = members, True
    result.status = f"Expanded{owner_note} - matches Plex's own count."
    return result
