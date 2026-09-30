"""Library Doctor: a to-do list from your films' files - feed a request in, get the answer (no windows involved).

It reads every copy's files and tracks (media_items, media_parts, media_streams - the tables behind the
spreadsheet's Files and Streams sheets) from the database the collection was loaded from, read-only, once per
loaded collection, and checks them:

    duplicates     copies of one film that look like the same cut twice
    weaker         copies with a worse picture than another copy of the same cut, and nothing it lacks
    large, small   files far bigger or smaller than usual for their running time and resolution
    unavailable    files Plex has marked unavailable
    editions       films kept in several editions on purpose (for reference)
    subtitles      foreign soundtrack and no subtitles in your languages
    audio          no soundtrack in your languages (fine when there are subtitles you read)
    unknown_audio  a soundtrack with no language tag
    upgrade        films you rated highly whose best copy is below 1080p (or 4K: the request's upgrade_below)
    metadata       films Plex hasn't matched, or with no year, genres or IMDb rating, or a file far shorter than
                   the film, or your rating left on an earlier edition (Plex shows them as unrated)
    disk           where the space goes: by library, resolution, codec and drive, and every file by size

    answer(catalog, request)        what {"action": "doctor", ...} answers (see ask.py)
    film_files(catalog, film_key)   one film's copies and their files, for the Film page
    film_issues(catalog, film_key)  one film's issues, for the Film page
    save_xlsx(path, sheets, about)  a list (or several) as a spreadsheet

Every check says in one plain sentence what it looks for (EXPLANATIONS), and every row says why it's there.
Nothing is ever changed: this only reads a backup of the database.
"""

from __future__ import annotations

import os
import re
import sqlite3
import statistics
import tempfile
import threading
from collections import Counter, defaultdict
from datetime import datetime

from . import APP_NAME, jobs
from .catalog import CANCEL_STEPS
from .extract import (LANGUAGES, VIDEO_CODECS, _ISO3, PlexDatabase, PlexDBError, _Extractor, _read_error,
                      audio_format, clean, hdr_label, language_name, local_datetime, resolution_label, sort_key,
                      split_path, to_int)

# ---------------------------------------------------------------------------------------------------------
# The checks and their settings
# ---------------------------------------------------------------------------------------------------------
ISSUE_IDS = ["duplicates", "weaker", "large", "small", "unavailable", "editions", "subtitles", "audio",
             "unknown_audio", "upgrade", "metadata", "disk"]
ALIASES = {"size": "large", "summary": "disk", "missing": "metadata", "duplicate": "duplicates",
           "space": "disk", "untagged": "unknown_audio", "unknown": "unknown_audio", "edition": "editions",
           "upgrades": "upgrade", "languages": "subtitles", "big": "large"}

SAME_CUT_MS = 120_000           # copies whose running times are this close can be the same cut
WEAKER_BITRATE_SHARE = 0.8      # at the same resolution and codec, under 80% of the data rate is a worse picture
CLOSE_RATE = 0.1                # two copies' data rates within a tenth of each other are too close to call
SIZE_RATIO = 3.0                # 'very large' at 3x the usual data rate, 'very small' at a third
SIZE_MIN_FILES = 20             # a resolution needs this many files to say what's usual
SIZE_MIN_MINUTES = 20           # shorter files aren't size-checked
SHORT_FILE_SHARE = 0.6          # a file under 60% of the film's running time is missing part of it (or isn't it)
SHORT_MIN_MINUTES = 20          # ...when it runs at least this long (shorter ones are extras and shorts)
CROPPED_1080_HEIGHT = 1000      # a picture this tall is a 1080p frame cropped at the sides (1374x1036, say)
HIGH_RATING = 8.0               # 'rated highly': 8 or more out of 10
UPGRADE_BELOW = "1080p"         # upgrade candidates: the best copy is below this (a request's upgrade_below: 4K...)
DEFAULT_LANGUAGES = ["en"]

TITLES = {
    "duplicates": "Likely duplicates", "weaker": "Weaker copies", "large": "Very large files",
    "small": "Very small files", "unavailable": "Unavailable files", "editions": "Several editions",
    "subtitles": "No subtitles you read", "audio": "No audio in your languages", "unknown_audio": "Untagged audio",
    "upgrade": "Upgrade candidates", "metadata": "Missing details", "disk": "Disk use",
}
SEVERITY = {"duplicates": "fix", "weaker": "fix", "subtitles": "fix", "unavailable": "fix",
            "large": "check", "small": "check", "unknown_audio": "check", "upgrade": "check", "metadata": "check",
            "audio": "info", "editions": "info", "disk": "info"}
SEVERITY_ORDER = {"fix": 0, "check": 1, "info": 2}

# One plain sentence each: what the check looks for.
EXPLANATIONS = {
    "duplicates": "Copies of one film that look like the same cut twice: the same file, or edition names that "
                  "differ only by label or release wording like 'Special Edition' (or not at all) and running "
                  "times within 2 minutes.",
    "weaker": "Copies with a worse picture than another copy of the same cut (lower resolution, or at least a "
              "fifth less picture data at the same resolution) and no soundtrack or subtitle the better copy "
              "lacks.",
    "large": "Files taking at least three times the usual space for their running time at their resolution in "
             "your library.",
    "small": "Files taking under a third of the usual space for their running time at their resolution in your "
             "library, so probably heavily compressed.",
    "unavailable": "Files Plex has marked unavailable: deleted, moved, or on a drive that was offline when Plex "
                   "last looked.",
    "editions": "Films you keep in more than one edition on purpose: the copies are different cuts or formats "
                "by name, or run more than 2 minutes apart.",
    "subtitles": "Copies whose main soundtrack is in a language you don't speak and that have no subtitles in "
                 "your languages (forced subtitles, which only translate the odd sign, don't count).",
    "audio": "Copies with no soundtrack in your languages, which is fine when they have subtitles you read.",
    "unknown_audio": "Copies with a soundtrack that has no language tag, so Plex can't choose it by language and "
                     "these checks can't judge it.",
    "upgrade": "Films you rated {rated} out of 10 whose best copy is below {upgrade_below}.",
    "metadata": "Films Plex hasn't matched or that have no year, genres or IMDb rating (so charts, searches and "
                "recommendations miss them), whose file runs far shorter than the film, or that you rated only on "
                "an earlier edition (so Plex shows them as unrated).",
    "disk": "How much space your films take, where it goes, and the biggest files.",
}

# What each check can't see - said plainly next to it.
LIMITS = {
    "duplicates": "Judged from edition names and running times: a release label this app doesn't know makes two "
                  "releases of one cut look like different editions - a missed duplicate, never a false alarm - "
                  "and a cut that runs under 2 minutes longer with no cut name looks like the same cut. Extra GB "
                  "counts every copy but the one that looks best (resolution, HDR, picture data, sound channels); "
                  "the Why says which copies those are.",
    "weaker": "Compares the picture (resolution, and the picture's own data rate at the same resolution and codec "
              "- the whole file's when Plex didn't record it), HDR, sound channels, and the soundtracks and "
              "subtitles in each language (a mono soundtrack, often the original mix, counts as one the other "
              "copy lacks) - not encode quality, the source (Blu-ray or DVD), extras or chapters. Whether to "
              "delete is up to you.",
    "large": "Uses the whole file's data rate - picture plus every soundtrack - so several lossless soundtracks "
             "make a file look large. Usual is the middle rate of your own files at that resolution; resolutions "
             "with under {size_min_files} files, and files under {size_min_minutes} minutes, aren't checked.",
    "small": "Uses the whole file's data rate. Animation compresses well, so some animated films land here "
             "legitimately. Usual is the middle rate of your own files at that resolution; resolutions with "
             "under {size_min_files} files, and files under {size_min_minutes} minutes, aren't checked.",
    "unavailable": "As of this backup: Plex may have found the files again since.",
    "editions": "Copies whose names say different cuts are trusted even when they run the same length.",
    "subtitles": "Trusts the language tags in the files. The main soundtrack is the one Plex plays first, not "
                 "necessarily the original language, and subtitle files beside the film count only once Plex has "
                 "scanned them. Copies Plex never analysed aren't checked (they're under Missing details).",
    "audio": "Trusts the language tags in the files ('Chinese' covers Mandarin and Cantonese). Copies Plex never "
             "analysed aren't checked (they're under Missing details).",
    "unknown_audio": "Untagged soundtracks are left out of the other language checks.",
    "upgrade": "Uses your ratings only (the server owner's account{owner}), and looks at resolution, not data "
               "rate. A 1080p picture cropped at the sides ({cropped_height} lines or more, like 1374×1036) counts "
               "as 1080p, though Plex calls it 720p.",
    "metadata": "Concerts and stand-up specials often have no IMDb rating at all, so not everything here can be "
                "fixed. A file is 'far shorter' under {short_share} of the running time Plex has for the film (and "
                "over {short_min_minutes} minutes), unless its edition name says what it is - a short version, "
                "one part.",
    "disk": "Sizes are decimal (1 GB = 1,000,000,000 bytes, as on the spreadsheet): Windows Explorer counts in "
            "1,024s, so it shows about 7% less for GB and 9% less for TB. A drive is the top folder of the path as "
            "the Plex server sees it, not a Windows drive letter.",
}

# The columns of each list: (key, heading, kind). Kinds: text, int, gb, mbps, ratio, rating (yours: 8, 7.5),
# score (IMDb's: 8.0, 7.1), minutes, date.
_COPY = [("film", "Film", "text"), ("edition", "Edition", "text"), ("library", "Library", "text")]
COLUMNS = {
    "duplicates": [("film", "Film", "text"), ("copies", "Copies", "int"), ("editions", "Editions", "text"),
                   ("libraries", "Libraries", "text"), ("extra_gb", "Extra GB", "gb"), ("why", "Why", "text")],
    "weaker": _COPY + [("resolution", "Resolution", "text"), ("picture_mbps", "Picture Mbit/s", "mbps"),
                       ("size_gb", "Size GB", "gb"), ("better", "Better copy", "text"), ("why", "Why", "text")],
    "large": [("film", "Film", "text"), ("edition", "Edition", "text"), ("resolution", "Resolution", "text"),
              ("minutes", "Minutes", "minutes"), ("size_gb", "Size GB", "gb"), ("mbps", "Mbit/s", "mbps"),
              ("usual_mbps", "Usual Mbit/s", "mbps"), ("times", "Times usual", "ratio"), ("why", "Why", "text")],
    "subtitles": _COPY + [("soundtracks", "Soundtracks", "text"), ("subtitles", "Subtitles", "text"),
                          ("why", "Why", "text")],
    "audio": _COPY + [("soundtracks", "Soundtracks", "text"), ("your_subtitles", "Your subtitles", "text"),
                      ("why", "Why", "text")],
    "unknown_audio": _COPY + [("soundtracks", "Soundtracks", "text"), ("why", "Why", "text")],
    "upgrade": [("film", "Film", "text"), ("your_rating", "Your rating", "rating"), ("best", "Best copy", "text"),
                ("plays", "Plays", "int"), ("imdb", "IMDb", "score"), ("size_gb", "Size GB", "gb"),
                ("why", "Why", "text")],
    "metadata": [("film", "Film", "text"), ("library", "Library", "text"), ("missing", "Missing", "text"),
                 ("why", "Why", "text")],
    "unavailable": _COPY + [("path", "File", "text"), ("since", "Since", "date"), ("why", "Why", "text")],
    "editions": [("film", "Film", "text"), ("copies", "Copies", "int"), ("editions", "Editions", "text"),
                 ("minutes", "Minutes", "text"), ("size_gb", "Size GB", "gb"), ("why", "Why", "text")],
    "disk": _COPY + [("resolution", "Resolution", "text"), ("codec", "Codec", "text"),
                     ("minutes", "Minutes", "minutes"), ("size_gb", "Size GB", "gb"), ("mbps", "Mbit/s", "mbps"),
                     ("drive", "Drive", "text"), ("path", "File", "text")],
}
COLUMNS["small"] = COLUMNS["large"]
# What one row of each list is, for "Saved 57 copies"
ROW_NOUNS = {"duplicates": ("film", "films"), "editions": ("film", "films"), "upgrade": ("film", "films"),
             "metadata": ("film", "films"), "disk": ("file", "files")}

RANK = {"8K": 6, "4K": 5, "1080p": 4, "720p": 3, "576p": 2, "480p": 1, "SD": 0}
RESOLUTION_ORDER = ["8K", "4K", "1080p", "720p", "576p", "480p", "SD"]
# Resolutions pooled for 'usual size' (DVD-sized and smaller share one)
SIZE_GROUP = {"8K": "8K", "4K": "4K", "1080p": "1080p", "720p": "720p", "576p": "SD", "480p": "SD", "SD": "SD"}
SIZE_GROUP_WORDS = {"8K": "8K", "4K": "4K", "1080p": "1080p", "720p": "720p",
                    "SD": "DVD resolution and below (576p, 480p and SD)"}
UNKNOWN_LANGUAGES = {"", "und", "unk", "unknown", "xx", "zxx", "mis", "mul", "qaa"}

# Words in an edition name that say who released it, or how it was packaged, rather than which cut it is.
# Two names that differ only in these are the same cut. Any other word (theatrical, extended, director's, cut,
# unrated, 3d, sbs, colorized, black & white, full frame, hk, us, japan, a/b/c, 1982...) means a different cut or
# format - so an unknown word errs towards "on purpose": a missed duplicate, never a false alarm.
RELEASE_WORDS = frozenset("""
    criterion collection arrow video films film academy shout factory select scream kino lorber studio classics
    eureka masters cinema 88 radiance severin vinegar syndrome synapse blue underground indicator powerhouse
    umbrella imprint via vision twilight time warner archive olive signature anchor bay magnet magnolia well go usa
    dragon dynasty tartan asia extreme tokyo shock discotek mvd rewind vestron mill creek bci image full moon rlje
    a24 neon ifc cohen movement music box oscilloscope canal second sight cinedigm gaiam midnite movies movie
    paramount presents fox disney club hbo lionsgate sony universal mgm pictures entertainment home media nova mpi
    vve vci pearl river soul martini kam ronson legend legends western w2 simitar rhino vantage kani fusian
    fangoria frightfest arthaus alliance vivendi superbit diamond gold platinum steelbook blu ray bluray uhd 4k hd
    edition editions special deluxe collector collector's collectors limited anniversary remastered restored
    remaster the and & of
""".split())
_ORDINAL = re.compile(r"^\d+(st|nd|rd|th)$")

# Guids of films Plex hasn't matched to anything
_UNMATCHED = re.compile(r"^(local://|com\.plexapp\.agents\.none)", re.I)


class CantRead(Exception):
    """The files can't be read: no database behind the collection, or it can't be opened."""


# ---------------------------------------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------------------------------------
def issue_id(value) -> str:
    """'weaker', 'size' (-> 'large')... Raises ValueError for a check there isn't."""
    key = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
    key = ALIASES.get(key, key)
    if key not in ISSUE_IDS:
        raise ValueError(f"there's no check called '{value}' (checks: {', '.join(ISSUE_IDS)})")
    return key


def lang_code(code) -> str:
    """A track's language as a plain code: 'eng', 'en-US' and 'en' are all 'en'."""
    c = clean(code).lower().replace("_", "-").split("-")[0]
    return _ISO3.get(c, c)


def lang_name(code: str) -> str:
    return LANGUAGES.get(code) or code or "Unknown"


_BY_NAME: dict[str, str] = {}
for _code, _name in LANGUAGES.items():
    _BY_NAME.setdefault(_name.casefold(), _code)


def parse_languages(value) -> list[str]:
    """['en', 'ja'] from ['en', 'ja'], 'en, Japanese', ['English', 'jpn'], 'en-US'... Default ['en'].
    An unknown language raises ValueError."""
    if value is None or value == "" or value == []:
        return list(DEFAULT_LANGUAGES)
    if isinstance(value, str):
        value = [v for v in re.split(r"[,;/|]", value)]
    if not isinstance(value, (list, tuple)):
        raise ValueError("languages must be a list, e.g. [\"en\", \"ja\"]")
    out = []
    for v in value:
        if not isinstance(v, str):
            raise ValueError(f"unknown language {v!r}")
        text = clean(v)
        if not text:
            continue
        code = _BY_NAME.get(text.casefold()) or lang_code(text)
        if code not in LANGUAGES:
            raise ValueError(f"unknown language '{text}'")
        if code not in out:
            out.append(code)
    return out or list(DEFAULT_LANGUAGES)


def languages_words(codes, joiner: str = "or", many: str = "your languages") -> str:
    """'English', 'English or Spanish', 'English, Spanish or French' - and `many` for none or more than three."""
    names = [lang_name(c) for c in codes]
    if not names or len(names) > 3:
        return many
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + f" {joiner} " + names[-1]


def subtitle_words(codes, joiner: str = "or", many: str | None = None) -> str:
    """Subtitles in some languages, as words that fit a sentence: 'English subtitles', 'English or Japanese
    subtitles' - and for more than three languages 'subtitles in one of your languages' (joiner 'or'),
    'subtitles in 4 of your languages' (joiner 'and'), or `many` (e.g. 'subtitles in any of your languages' after
    'no')."""
    codes = list(codes)
    if 0 < len(codes) <= 3:
        return f"{languages_words(codes, joiner)} subtitles"
    if many is not None:
        return many
    if joiner == "and":
        return f"subtitles in {len(codes)} of your languages"
    return "subtitles in one of your languages"


def _and_list(items) -> str:
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def _mbps(bits) -> str:
    """A data rate in Mbit/s for a sentence: '13.5', '0.67' (two places under 1 Mbit/s)."""
    x = (bits or 0) / 1e6
    return f"{x:.2f}" if x < 1 else f"{x:.1f}"


def gb(n) -> float:
    return round((n or 0) / 1e9, 2)


def size_text(n) -> str:
    """Bytes in words: '350 MB', '8.2 GB', '66.9 GB', '123 GB', '14.8 TB' (decimal units)."""
    n = n or 0
    if n >= 1e12:
        return f"{n / 1e12:.1f} TB"
    if n >= 1e11:
        return f"{n / 1e9:.0f} GB"
    if n >= 1e9:
        return f"{n / 1e9:.1f} GB"
    if n >= 1e6:
        return f"{n / 1e6:.0f} MB"
    return f"{n / 1e3:.0f} KB" if n else "0 GB"


def _minutes(ms) -> int | None:
    return round(ms / 60000) if ms else None


def _minutes_list(values) -> str:
    """'117 and 116 min', '117, 105 and 98 min'."""
    mins = [f"{m}" for m in values]
    if len(mins) == 1:
        return f"{mins[0]} min"
    return ", ".join(mins[:-1]) + " and " + mins[-1] + " min"


def _rating(v):
    """8.0 -> 8, 7.5 -> 7.5 (ratings come in halves)."""
    if v is None:
        return None
    return int(v) if float(v).is_integer() else round(float(v), 1)


def rated_words(min_rating) -> str:
    """'8 or more', '7.5 or more' - and '10' for 10 (there's nothing more)."""
    return "10" if float(min_rating) >= 10 else f"{_rating(min_rating)} or more"


def upgrade_target(value) -> str:
    """A request's upgrade_below as the resolution it names: '1080p' (the default), '4K', '4k', '720p'...
    Raises ValueError for one there isn't."""
    if value in (None, ""):
        return UPGRADE_BELOW
    text = str(value).strip()
    for name in RANK:
        if name.lower() == text.lower():
            return name
    raise ValueError(f"upgrade_below must be one of {', '.join(RESOLUTION_ORDER)}, not {value!r}")


def _quoted(edition: str) -> str:
    return f"'{edition}'" if edition else "the copy with no edition name"


def drive_of(path: str) -> str:
    """The top folder of a path as the server sees it: '/disk1', 'D:', '\\\\nas\\share'."""
    path = clean(path)
    if not path:
        return ""
    if path.startswith("\\\\") or path.startswith("//"):
        sep = path[0]
        parts = [p for p in re.split(r"[\\/]+", path) if p]
        return sep * 2 + sep.join(parts[:2]) if parts else ""
    m = re.match(r"^([A-Za-z]):", path)
    if m:
        return m.group(1).upper() + ":"
    if path.startswith("/"):
        parts = path.split("/")
        return "/" + parts[1] if len(parts) > 2 and parts[1] else "/"
    return re.split(r"[\\/]", path)[0]


def edition_words(edition: str) -> list[str]:
    """An edition name as words: "Arrow Video Director's Cut" -> ['arrow', 'video', "director's", 'cut']."""
    t = clean(edition).casefold().replace("’", "'")
    return [w.strip("'") for w in re.split(r"[^\w'&]+", t) if w.strip("'")]


def _release_word(word: str) -> bool:
    return word in RELEASE_WORDS or bool(_ORDINAL.match(word))


def film_sort_key(film) -> tuple:
    """Title order as a person expects it: articles, accents, quotes and case ignored ('Weird Al' under W)."""
    from .catalog import fold
    return (fold(film.title or "", drop_article=True) or sort_key(film.title), film.year or 0, film.key)


# ---------------------------------------------------------------------------------------------------------
# Reading every copy (once per loaded collection)
# ---------------------------------------------------------------------------------------------------------
class _Only(_Extractor):
    """The spreadsheet's loaders, limited to some library items (one film's copies, for the Film page)."""

    def __init__(self, db, ids):
        super().__init__(db, None, [], None, None)
        self._only = ",".join(str(int(i)) for i in ids) or "NULL"

    def _movie_filter(self, alias="m") -> str:
        return super()._movie_filter(alias) + f" AND {alias}.id IN ({self._only})"


_LOCK = threading.Lock()              # one read of the files at a time
_STORE_LOCK = threading.Lock()        # the worked-out lists kept with a catalog


def _read(catalog, plex_ids=None) -> dict:
    """{'copies': [copy dict...], 'items': {plex_id: item dict}} for the catalog's films (or just plex_ids).
    Raises CantRead when there's no database to read; jobs.Cancelled in a job that's called off."""
    source = getattr(catalog, "source", "") or ""
    if not source or source == "memory":
        raise CantRead("Couldn't read your files: this collection wasn't read from a Plex database file.")
    if not os.path.isfile(source):
        raise CantRead(f"Couldn't read your files: {os.path.basename(source)} isn't there any more.")
    key_of = {pid: f.key for f in catalog.films.values() for pid in f.plex_ids}
    try:
        with PlexDatabase(source) as db:
            job = jobs.current()
            if job is not None and db.con is not None:
                db.con.set_progress_handler(lambda: job.cancelled, CANCEL_STEPS)
            try:
                ex = _Only(db, plex_ids) if plex_ids is not None else _Extractor(db, None, [], None, None)
                movies = ex.load_movies()
                jobs.check()
                media = ex.load_media()
            except sqlite3.DatabaseError as exc:
                jobs.check()                   # (an interrupted query: the job was called off)
                raise _read_error(db.path, exc) from exc
            libraries = {lib.id: lib.name for lib in ex.libraries}
    except (PlexDBError, OSError) as exc:
        text = " ".join(str(exc).split())
        raise CantRead(f"Couldn't read your files from {os.path.basename(source)}: {text}") from exc
    lib_order = {name: i for i, name in enumerate(getattr(catalog, "libraries", []) or [])}
    copies, items = [], {}
    for n, m in enumerate(movies):
        if n % 256 == 0:
            jobs.check()
        key = key_of.get(m["id"])
        if key is None:
            continue
        library = libraries.get(m["library_section_id"], "")
        items[m["id"]] = {"film_key": key, "guid": clean(m["guid"]), "edition": clean(m["edition_title"]),
                          "library": library, "title": clean(m["title"])}
        for vi, v in enumerate(media.get(m["id"], []), 1):
            c = _copy(m, v, vi, key, library)
            c["_order"] = (c["unavailable"], lib_order.get(library, len(lib_order)), m["id"], vi)
            copies.append(c)
    return {"copies": copies, "items": items}


def _copy(m, v, version, film_key, library) -> dict:
    """One copy (a media item: a version of a library item) as plain data."""
    parts = v["parts"]
    first = parts[0]["id"] if parts else None
    # each file of a split copy (CD1, CD2) repeats the tracks: take the first file's, as the spreadsheet does
    streams = [s for s in v["streams"] if s["media_part_id"] in (first, None)] if parts else v["streams"]
    video = next((s for s in streams if s["stream_type_id"] == 1 and s["is_default"]),
                 next((s for s in streams if s["stream_type_id"] == 1), None))
    vx = video["extra"] if video else {}
    audio = [s for s in streams if s["stream_type_id"] == 2]
    subs = [s for s in streams if s["stream_type_id"] == 3]
    size = sum(to_int(p["size"]) or 0 for p in parts) or to_int(v["size"]) or 0
    duration = (to_int(v["duration"]) or sum(to_int(p["duration"]) or 0 for p in parts)
                or to_int(m["duration"]) or 0)
    width = to_int(v["width"]) or to_int(vx.get("ma:width"))
    height = to_int(v["height"]) or to_int(vx.get("ma:height"))
    codec = clean(v["video_codec"] or (video["codec"] if video else "")).lower()
    bitrate = to_int(v["bitrate"]) or (round(size * 8 / (duration / 1000)) if size and duration else 0)
    deleted = [x for x in [m["deleted_at"], v["deleted_at"]] + [p["deleted_at"] for p in parts] if to_int(x)]
    main = next((s for s in audio if s["is_default"]), audio[0] if audio else None)
    return {
        "film_key": film_key, "plex_id": m["id"], "version": version, "media_id": v["id"],
        "title": clean(m["title"]), "year": to_int(m["year"]), "edition": clean(m["edition_title"]),
        "library": library, "guid": clean(m["guid"]),
        "resolution": resolution_label(width, height), "width": width, "height": height,
        "video_codec": VIDEO_CODECS.get(codec, codec.upper()), "codec_raw": codec,
        "hdr": hdr_label(vx, v["color_trc"]) if (video or v["color_trc"]) else "",
        "container": clean(v["container"]).upper(), "bitrate": bitrate or 0, "duration_ms": duration, "size": size,
        # the picture's own data rate (the whole file's above also counts every soundtrack): 0 when Plex didn't
        # record it
        "picture_bitrate": (to_int(video.get("bitrate")) or 0) if video else 0,
        # how long Plex says the film runs (its library item's running time, not this file's)
        "film_duration_ms": to_int(m["duration"]) or 0,
        "files": [{"path": clean(p["file"]), "size": to_int(p["size"]) or 0, "duration_ms": to_int(p["duration"]),
                   "hash": clean(p["hash"])} for p in parts],
        "analysed": bool(streams),
        "audio": [{"code": lang_code(s["language"]), "language": language_name(s["language"]) or "Unknown",
                   "format": audio_format(s["codec"], s["extra"].get("ma:profile"), s["channels"],
                                          s["extra"].get("ma:audioChannelLayout")),
                   "channels": to_int(s["channels"]) or 0, "default": bool(s["is_default"]),
                   "title": clean(s["extra"].get("ma:title"))} for s in audio],
        "main_audio": lang_code(main["language"]) if main else None,
        "subtitles": [{"code": lang_code(s["language"]), "language": language_name(s["language"]) or "Unknown",
                       "forced": bool(s["forced"]), "external": bool(s["url"]),
                       "format": _subtitle_format(s["codec"])} for s in subs],
        "unavailable": bool(deleted), "unavailable_since": min(to_int(x) for x in deleted) if deleted else None,
        "added_at": to_int(v["created_at"]) or to_int(m["added_at"]),
    }


def _subtitle_format(codec) -> str:
    from .extract import SUBTITLE_CODECS
    codec = clean(codec).lower()
    return SUBTITLE_CODECS.get(codec, codec.upper())


def copies_data(catalog) -> dict:
    """Every copy of the catalog's films, read once and kept with the catalog (half a second for a few thousand).
    Safe from any thread: a second caller waits for the first read rather than doing it again."""
    got = catalog.cache.get("doctor.copies")
    if got is not None:
        return got
    with _LOCK:
        got = catalog.cache.get("doctor.copies")
        if got is None:
            got = _read(catalog)
            got["copies"].sort(key=lambda c: c["_order"])
            catalog.cache["doctor.copies"] = got
        return got


def _film_copies(catalog, film) -> list[dict]:
    """One film's copies: from the whole-library read when it's been done, else just this film's (35 ms)."""
    data = catalog.cache.get("doctor.copies")
    if data is not None:
        return [c for c in data["copies"] if c["film_key"] == film.key]
    cache = catalog.cache.setdefault("doctor.film_copies", {})
    got = cache.get(film.key)
    if got is None:
        got = _read(catalog, film.plex_ids)["copies"]
        got.sort(key=lambda c: c["_order"])
        cache[film.key] = got
    return got


# ---------------------------------------------------------------------------------------------------------
# Comparing copies
# ---------------------------------------------------------------------------------------------------------
def _hdr_rank(c) -> int:
    h = (c.get("hdr") or "").lower()
    if not h or h == "sdr":
        return 0
    return 2 if h.startswith("dolby vision") else 1


def _cropped_1080(c) -> bool:
    """A 1080p picture cropped at the sides - 1374x1036, say - which Plex calls 720p."""
    return RANK.get(c["resolution"], -1) == RANK["720p"] and (c.get("height") or 0) >= CROPPED_1080_HEIGHT


def _rank(c) -> int:
    """How sharp a copy's picture is (RANK), a cropped 1080p picture counting as 1080p. -1: unknown."""
    return RANK["1080p"] if _cropped_1080(c) else RANK.get(c["resolution"], -1)


def _resolution_words(c) -> str:
    """'1080p' - or the real size when that says more than Plex's label: '1374×1036'."""
    if _cropped_1080(c):
        return f"{c['width']}×{c['height']}"
    return c["resolution"]


def _rates(a: dict, b: dict) -> tuple[int, int, str]:
    """Two copies' data rates to compare, and which they are: the pictures' own when Plex recorded both
    ('picture'), else the whole files' ('file') - never one of each."""
    pa, pb = a.get("picture_bitrate") or 0, b.get("picture_bitrate") or 0
    if pa and pb:
        return pa, pb, "picture"
    return a["bitrate"] or 0, b["bitrate"] or 0, "file"


def _max_channels(c) -> int:
    return max((t["channels"] for t in c["audio"]), default=0)


def _quality(c, picture: bool = False) -> tuple:
    """Best copy first when sorted descending. picture: compare the pictures' own data rates (only when every
    copy compared has one - see _ranker)."""
    rate = (c.get("picture_bitrate") if picture else c["bitrate"]) or 0
    return (_rank(c), _hdr_rank(c), rate, _max_channels(c), c["size"] or 0)


def _ranker(copies):
    """_quality for comparing these copies: by the pictures' data rates when Plex recorded every one's."""
    picture = bool(copies) and all(c.get("picture_bitrate") for c in copies)
    return lambda c: _quality(c, picture)


def _language_word(code: str) -> str:
    return "untagged" if code in UNKNOWN_LANGUAGES else lang_name(code)


def _lacks(x: dict, y: dict) -> list[str]:
    """What copy x has that copy y lacks, in words: more soundtracks or (non-forced) subtitle tracks in a language,
    or a mono soundtrack - often the original mix - in a language where y has none. [] when y has it all."""
    out = []
    xa, ya = Counter(t["code"] for t in x["audio"]), Counter(t["code"] for t in y["audio"])
    y_mono = {t["code"] for t in y["audio"] if t["channels"] == 1}
    x_mono = {t["code"] for t in x["audio"] if t["channels"] == 1}
    mono = []
    for code, n in xa.items():
        more = n - ya.get(code, 0)
        word = _language_word(code)
        if more > 0:
            out.append(f"an extra {word} soundtrack" if more == 1 else f"{more} extra {word} soundtracks")
        elif code in x_mono and code not in y_mono:
            mono.append(word)
    if mono:
        out.append(f"a mono {mono[0]} soundtrack" if len(mono) == 1 else f"mono {_and_list(mono)} soundtracks")
    xs = Counter(t["code"] for t in x["subtitles"] if not t["forced"])
    ys = Counter(t["code"] for t in y["subtitles"] if not t["forced"])
    for code, n in xs.items():
        more = n - ys.get(code, 0)
        if more > 0:
            word = _language_word(code)
            out.append(f"an extra {word} subtitle track" if more == 1 else f"{more} extra {word} subtitle tracks")
    return out


def _edge(best: dict, other: dict) -> str:
    """What clearly makes `best` the better of two copies, in words ('a higher resolution (1080p against 720p)',
    'more picture data (13.5 against 11.0 Mbit/s)'...) - '' when nothing clearly does."""
    if _rank(best) > _rank(other):
        return f"a higher resolution ({_resolution_words(best)} against {_resolution_words(other)})"
    if _hdr_rank(best) > _hdr_rank(other):
        return f"{best['hdr']} where the other has " + (other["hdr"] if _hdr_rank(other) else "none")
    xa, xb, kind = _rates(best, other)
    if xa and xb and xa >= xb * (1 + CLOSE_RATE):
        return f"more {'picture ' if kind == 'picture' else ''}data ({_mbps(xa)} against {_mbps(xb)} Mbit/s)"
    ca, cb = _max_channels(best), _max_channels(other)
    if ca > cb and best["analysed"] and other["analysed"]:
        return f"more sound channels ({ca} against {cb})"
    return ""


def _first_path(c) -> str:
    return c["files"][0]["path"] if c["files"] else ""


def _first_hash(c) -> str:
    return c["files"][0]["hash"] if c["files"] else ""


def same_cut(a: dict, b: dict) -> tuple[bool, str]:
    """Whether two copies of one film look like the same cut, and how it was decided:
    'same_path' (one file in the library twice), 'same_content' (identical files), 'same_name' / 'release'
    (edition names alike, running times close), 'same_name_nodur' / 'release_nodur' (running time unknown),
    'names' (different cuts or formats by name) or 'runtime' (more than 2 minutes apart)."""
    pa, pb = _first_path(a), _first_path(b)
    if pa and pa == pb:
        return True, "same_path"
    ha, hb = _first_hash(a), _first_hash(b)
    if ha and ha == hb and a["size"] and a["size"] == b["size"]:
        return True, "same_content"
    diff = set(edition_words(a["edition"])) ^ set(edition_words(b["edition"]))
    if diff and not all(_release_word(w) for w in diff):
        return False, "names"
    how = "release" if diff else "same_name"
    if a["duration_ms"] and b["duration_ms"]:
        if abs(a["duration_ms"] - b["duration_ms"]) > SAME_CUT_MS:
            return False, "runtime"
        return True, how
    return True, how + "_nodur"


def weaker_than(b: dict, a: dict):
    """(picture words, sound compared?) when copy b is strictly weaker than copy a of the same cut, else None.

    b is weaker when its resolution is no higher, it has no HDR a lacks, (when Plex has analysed both) no more
    sound channels and nothing in its soundtracks and subtitles a lacks (see _lacks) - and its picture is worse:
    a lower resolution, or the same codec with under 80% of a's data rate. The data rates are the pictures' own
    when Plex recorded both (a file's rate also counts its soundtracks: ten lossless ones make a file look
    sharper than it is), else the whole files'."""
    rb, ra = _rank(b), _rank(a)
    if rb < 0 or ra < 0 or rb > ra or _hdr_rank(b) > _hdr_rank(a):
        return None
    compared = a["analysed"] and b["analysed"]
    if compared:
        if _max_channels(b) > _max_channels(a):
            return None
        if _lacks(b, a):
            return None
    if rb < ra:
        picture = f"{_resolution_words(a)} against {_resolution_words(b)}"
    else:
        xa, xb, kind = _rates(a, b)
        if not (b["codec_raw"] == a["codec_raw"] and xa and xb and xb < WEAKER_BITRATE_SHARE * xa):
            return None
        if kind == "picture":
            picture = f"{_mbps(xa)} against {_mbps(xb)} Mbit/s of picture at {_resolution_words(a)}"
        else:
            picture = f"{_mbps(xa)} against {_mbps(xb)} Mbit/s at {_resolution_words(a)}, whole-file rates"
    if _hdr_rank(a) > _hdr_rank(b):
        picture += f", with {a['hdr']}"
    return picture, compared


def _groups(copies: list[dict]) -> tuple[list[list[dict]], dict]:
    """The copies of one film grouped into cuts (copies that look like the same cut share a group), and how each
    pair was judged {(i, j): (same, how)}."""
    parent = list(range(len(copies)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    judged = {}
    for i in range(len(copies)):
        for j in range(i + 1, len(copies)):
            same, how = same_cut(copies[i], copies[j])
            judged[(i, j)] = (same, how)
            if same:
                parent[root(i)] = root(j)
    groups = defaultdict(list)
    for i, c in enumerate(copies):
        groups[root(i)].append(i)
    return [[copies[i] for i in members] for members in groups.values()], {
        (id(copies[i]), id(copies[j])): v for (i, j), v in judged.items()}


# ---------------------------------------------------------------------------------------------------------
# The checks
# ---------------------------------------------------------------------------------------------------------
def _base(film, c=None) -> dict:
    row = {"film_key": film.key, "film": film.label, "title": film.title, "year": film.year,
           "plex_id": c["plex_id"] if c else (film.plex_ids[0] if film.plex_ids else None),
           "edition": c["edition"] if c else "",
           "library": c["library"] if c else ", ".join(film.libraries)}
    if c is not None:
        row["media_id"] = c["media_id"]
        row["path"] = _first_path(c)
    return row


def _duplicate_why(group, how_pairs) -> str:
    hows = set(how_pairs)
    mins = sorted({_minutes(c["duration_ms"]) for c in group if c["duration_ms"]}, reverse=True)
    if "same_path" in hows and len(group) == 2:
        libs = sorted({c["library"] for c in group})
        where = f"in {libs[0]} and {libs[1]}" if len(libs) == 2 else f"in {libs[0]} twice"
        return (f"The same file is in your library twice ({where}), so it takes no extra space - remove it "
                f"from one library in Plex rather than deleting the file.")
    if "same_content" in hows and len(group) == 2:
        return ("Two identical files - the same size and content - are in different folders, so one of them "
                "is taking space for nothing.")
    names = []
    for c in group:
        n = _quoted(c["edition"])
        if n not in names:
            names.append(n)
    two = len(group) == 2
    unknown = not mins or any(h.endswith("_nodur") for h in hows)
    if unknown:
        run = None
    elif len(mins) == 1:
        run = f"{'both' if two else 'all'} run {mins[0]} min"
    else:
        run = f"they run {_minutes_list(mins)}"
    if len(names) == 1:
        subject = (f"{'Both' if two else f'All {len(group)}'} copies are called {names[0]}" if group[0]["edition"]
                   else f"{'Neither copy has' if two else 'None of the copies has'} an edition name")
        if run is None:
            return f"{subject}, so they look like the same cut (the running time of one copy is unknown)."
        return f"{subject} and {run}, so they look like the same cut."
    listed = f"{', '.join(names[:-1])} and {names[-1]}"
    listed = listed[0].upper() + listed[1:]
    if run is None:
        return (f"{listed} look like the same cut: the names differ only by label or release wording (the "
                f"running time of one copy is unknown).")
    return f"{listed} look like the same cut: the names differ only by label or release wording, and {run}."


def _copy_name(c: dict, group: list[dict]) -> str:
    """How to tell a copy from the others in its group: its edition name, else its library, else its file."""
    if sum(1 for x in group if x["edition"] == c["edition"]) == 1:
        return _quoted(c["edition"])
    if c["library"] and sum(1 for x in group if x["library"] == c["library"]) == 1:
        return f"the copy in {c['library']}"
    path = _first_path(c)
    return f"the file {split_path(path)[1]}" if path else f"version {c['version']}"


def _extra_why(group: list[dict], extra: int) -> str:
    """Which copies the Extra GB counts - every copy but the best (group[0]) - and what makes that one the best,
    or that the two are too close to call. Says too what the extra copy has that the best lacks."""
    best, other = group[0], group[1]
    name_best, name_other = _copy_name(best, group), _copy_name(other, group)
    two = len(group) == 2
    counted = name_other if two else f"every copy but {name_best}"
    compared = best["analysed"] and other["analysed"]
    theirs = _lacks(other, best) if compared else []
    if compared and _max_channels(other) > _max_channels(best):
        theirs.insert(0, f"more sound channels ({_max_channels(other)} against {_max_channels(best)})")
    if _hdr_rank(other) > _hdr_rank(best):              # (a sharper picture came first)
        theirs.insert(0, other["hdr"])
    edge = _edge(best, other) or (_and_list(_lacks(best, other)) if compared else "")
    if not edge and not theirs:
        if not compared:
            return f"The extra {size_text(extra)} is {counted}."
        return (f"The two {'' if two else 'best '}copies are too close to call: the extra {size_text(extra)} is "
                f"{counted}.")
    text = f"The extra {size_text(extra)} is {counted}"
    if edge:
        text += f"; {name_best} has {edge}"
    if theirs:
        who = ("the other" if two else name_other) if edge else "it"
        text += f", but {who} has {_and_list(theirs)}"
    return text + "."


def _editions_why(film_copies, groups, judged) -> str:
    mins = [_minutes(c["duration_ms"]) for c in film_copies if c["duration_ms"]]
    by_name = any(judged.get((id(a), id(b)), judged.get((id(b), id(a)), (False, "")))[1] == "names"
                  for gi, g in enumerate(groups) for h in groups[gi + 1:] for a in g for b in h)
    names = []
    for g in groups:
        n = g[0]["edition"] or "no edition name"
        if n not in names:
            names.append(n)
    run = f"they run {_minutes_list(mins)}" if mins else "their running times are unknown"
    if by_name:
        return f"Different cuts or formats by name ({', '.join(names)}); {run}."
    gap = max(mins) - min(mins) if mins else 0
    return f"The edition names don't say they differ, but they run {gap} minutes apart ({_minutes_list(mins)})."


def _short_file(c: dict) -> bool:
    """A file that runs far shorter than the film Plex matched it to (under 60% of its running time, and over 20
    minutes): half the film missing, or the wrong film - unless its edition name says what it is ('Short',
    'Part 2', '1982'): any word but release wording."""
    film_ms, ms = c.get("film_duration_ms") or 0, c["duration_ms"] or 0
    if not film_ms or ms < SHORT_MIN_MINUTES * 60000 or ms >= SHORT_FILE_SHARE * film_ms:
        return False
    return all(_release_word(w) for w in edition_words(c["edition"]))


def _short_why(short: list[dict], copies: int) -> str:
    """'Its file runs 55 minutes, but Plex says the film runs 120: it may be incomplete, or matched to the wrong
    film.'"""
    film_min = _minutes(short[0]["film_duration_ms"])
    mins = [_minutes(c["duration_ms"]) for c in short]
    if copies == 1:
        subject = f"Its file runs {mins[0]} minutes"
    elif len(short) == 1:
        subject = f"One of its {copies} files runs {mins[0]} minutes"
    else:
        subject = f"{len(short)} of its {copies} files run {_minutes_list(mins)[:-4]} minutes"
    it = "it" if len(short) == 1 else "they"
    return (f"{subject}, but Plex says the film runs {film_min}: {it} may be incomplete, or matched to the wrong "
            f"film.")


def _track_language(t) -> str:
    """A track's language by name ('ja-JP' is plain Japanese here; the Film page shows the region)."""
    return "Unknown" if t["code"] in UNKNOWN_LANGUAGES else lang_name(t["code"])


def _soundtracks(c) -> str:
    """'Japanese (DTS-HD MA 2.0, DTS 2.0), English (Dolby Digital 5.1)' - each language once, its tracks' formats
    in the file's order."""
    by_language: dict[str, list[str]] = {}
    for t in c["audio"]:
        by_language.setdefault(_track_language(t), []).append(t["format"])
    parts = []
    for language, formats in by_language.items():
        formats = [f for f in formats if f]
        parts.append(f"{language} ({', '.join(formats)})" if formats else language)
    return ", ".join(parts) or "none"


def _subtitles_text(c) -> str:
    seen = []
    for t in c["subtitles"]:
        label = _track_language(t) + (" (forced)" if t["forced"] else "")
        if label not in seen:
            seen.append(label)
    return "; ".join(seen) or "none"


def _fraction_words(ratio: float) -> str:
    if ratio <= 0:
        return "a tiny fraction of"
    n = 1 / ratio
    for limit, words in ((3.5, "about a third of"), (4.5, "about a quarter of"), (5.5, "about a fifth of"),
                         (6.5, "about a sixth of"), (7.5, "about a seventh of"), (8.5, "about an eighth of"),
                         (9.5, "about a ninth of"), (10.5, "about a tenth of")):
        if n < limit:
            return words
    return "under a tenth of"


def _unavailable_date(ts) -> str:
    dt = local_datetime(ts)
    return dt.strftime("%Y-%m-%d") if dt else ""


def analyse(catalog, languages=None, min_rating: float = HIGH_RATING, upgrade_below: str = UPGRADE_BELOW) -> dict:
    """Every check's rows (all of them), plus the usual data rates and the disk totals. The checks that don't
    depend on your languages or ratings are worked out once per collection; the language lists and the upgrade
    list once per choice - so asking again, for another language or for one film, is quick."""
    languages = parse_languages(languages)
    min_rating = float(min_rating)
    upgrade_below = upgrade_target(upgrade_below)
    key = (tuple(languages), min_rating, upgrade_below)
    with _STORE_LOCK:
        store = catalog.cache.setdefault("doctor.analyses", {})
        got = store.get(key)
    if got is not None:
        return got
    base = _base_checks(catalog)
    rows = dict(base["rows"])
    rows.update(_language_checks(catalog, base, languages))
    rows["upgrade"] = _upgrade_checks(catalog, base, min_rating, upgrade_below)
    rows = {k: rows[k] for k in ISSUE_IDS}
    got = {"rows": rows, "typical": base["typical"], "disk": base["disk"], "languages": languages,
           "min_rating": min_rating, "upgrade_below": upgrade_below, "copies": base["copies"],
           "available": base["available"],
           "drives": base["drives"], "counts": _tile_counts(rows, languages)}
    with _STORE_LOCK:
        while len(store) >= 8:               # (a few language choices at most)
            store.pop(next(iter(store)))
        store[key] = got
    return got


def _base_checks(catalog) -> dict:
    """The checks that don't depend on your languages or ratings, kept with the catalog."""
    got = catalog.cache.get("doctor.base")
    if got is None:
        got = _checks(catalog, copies_data(catalog))
        catalog.cache["doctor.base"] = got
    return got


def _title_order(catalog, base):
    """A sort key for rows: the film's title as a person expects it (worked out once per film)."""
    order, films = base["title_order"], catalog.films

    def by_title(r):
        key = r["film_key"]
        got = order.get(key)
        if got is None:
            got = order[key] = film_sort_key(films[key])
        return got, r.get("plex_id") or 0, r.get("media_id") or 0
    return by_title


def _checks(catalog, data) -> dict:
    copies, items = data["copies"], data["items"]
    films = catalog.films
    rows = {i: [] for i in ISSUE_IDS if i not in ("subtitles", "audio", "upgrade")}
    available = [c for c in copies if not c["unavailable"]]
    by_film = defaultdict(list)
    for c in available:
        by_film[c["film_key"]].append(c)

    # -- several copies of one film: the same cut twice, several editions, weaker copies ---------------------
    for key, cs in by_film.items():
        if len(cs) < 2 or key not in films:
            continue
        jobs.check()
        film = films[key]
        groups, judged = _groups(cs)
        for g in groups:
            if len(g) < 2:
                continue
            quality = _ranker(g)
            g = sorted(g, key=quality, reverse=True)
            best = g[0]
            hows = [judged.get((id(a), id(b))) or judged.get((id(b), id(a))) for n, a in enumerate(g)
                    for b in g[n + 1:]]
            hows = [h[1] for h in hows if h and h[0]]
            same_file = all(h == "same_path" for h in hows)
            extra = 0 if same_file else sum(c["size"] for c in g[1:])
            why = _duplicate_why(g, hows)
            if extra and not ("same_content" in hows and len(g) == 2):
                why += " " + _extra_why(g, extra)          # (which copies the Extra GB is)
            row = _base(film, best)
            row.update(copies=len(g), editions="; ".join(c["edition"] or "(no edition name)" for c in g),
                       libraries=", ".join(sorted({c["library"] for c in g})), extra_gb=gb(extra),
                       extra_bytes=extra, keep=best["edition"], plex_ids=[c["plex_id"] for c in g],
                       paths=[_first_path(c) for c in g], why=why)
            rows["duplicates"].append(row)
            # a weaker copy: weaker than some other copy of the same cut (compared with the best such copy)
            for c in g:
                better = [(weaker_than(c, other), other) for other in g if other is not c]
                better = [(w, o) for w, o in better if w]
                if not better:
                    continue
                (picture, compared), other = max(better, key=lambda wo: quality(wo[1]))
                if _first_path(c) and _first_path(c) == _first_path(other):
                    continue
                same_name = c["edition"] == other["edition"]
                who = "Your other copy" if same_name else f"Your {_quoted(other['edition'])} copy"
                if compared:
                    why = (f"{who} of the same cut has a better picture ({picture}) and every soundtrack and "
                           f"subtitle this one has; removing this frees {size_text(c['size'])}.")
                else:
                    unseen = ("neither copy" if not c["analysed"] and not other["analysed"] else
                              "this copy" if not c["analysed"] else "the better copy")
                    why = (f"{who} of the same cut has a better picture ({picture}), but Plex hasn't analysed "
                           f"{unseen}, so their soundtracks and subtitles weren't compared - check before removing "
                           f"this one, which would free {size_text(c['size'])}.")
                # the rates the check compared: the pictures' own, else (Plex didn't record one) the whole files'
                rate_c, rate_other, kind = _rates(c, other)
                row = _base(film, c)
                row.update(resolution=c["resolution"], mbps=round(c["bitrate"] / 1e6, 1),
                           picture_mbps=round(rate_c / 1e6, 1) if kind == "picture" and rate_c else None,
                           size_gb=gb(c["size"]), size_bytes=c["size"],
                           better=" · ".join(x for x in (
                               other["edition"] or "no edition name", _resolution_words(other),
                               (f"{_mbps(rate_other)} Mbit/s" + ("" if kind == "picture" else " whole file"))
                               if rate_other else "") if x),
                           better_plex_id=other["plex_id"], better_media_id=other["media_id"], compared=compared,
                           why=why)
                rows["weaker"].append(row)
        if len(groups) >= 2:
            row = _base(film)
            ordered = sorted(cs, key=lambda c: c["_order"])
            row.update(copies=len(cs), editions="; ".join(c["edition"] or "(no edition name)" for c in ordered),
                       minutes=" / ".join(str(_minutes(c["duration_ms"]) or "?") for c in ordered),
                       size_gb=gb(sum(c["size"] for c in cs)), paths=[_first_path(c) for c in ordered],
                       why=_editions_why(ordered, groups, judged))
            rows["editions"].append(row)

    # -- each copy: unavailable, untagged soundtracks, size ------------------------------------------------------
    typical = {}
    rates = defaultdict(list)
    for c in available:
        if c["size"] and c["duration_ms"]:
            rates[SIZE_GROUP.get(c["resolution"], "")].append(c["size"] * 8 / (c["duration_ms"] / 1000))
    for g, xs in rates.items():
        if g and len(xs) >= SIZE_MIN_FILES:
            typical[g] = statistics.median(xs)
    for n, c in enumerate(copies):
        if n % 256 == 0:
            jobs.check()
        film = films.get(c["film_key"])
        if film is None:
            continue
        if c["unavailable"]:
            row = _base(film, c)
            since = _unavailable_date(c["unavailable_since"])
            row.update(path=_first_path(c), since=since,
                       why=(f"Plex marked this file unavailable{' on ' + since if since else ''} - deleted, moved, "
                            f"or on a drive that was offline when it last looked."))
            rows["unavailable"].append(row)
            continue
        if c["analysed"] and c["audio"]:
            codes = [t["code"] for t in c["audio"]]
            unknown = [i for i, x in enumerate(codes) if x in UNKNOWN_LANGUAGES]
            if unknown:
                which = [c["audio"][i] for i in unknown]
                formats = ", ".join(t["format"] for t in which if t["format"])
                numbers = [str(i + 1) for i in unknown]
                nums = numbers[0] if len(numbers) == 1 else ", ".join(numbers[:-1]) + " and " + numbers[-1]
                row = _base(film, c)
                row.update(soundtracks=_soundtracks(c),
                           why=(f"Soundtrack{'s' if len(unknown) > 1 else ''} {nums} of {len(codes)}"
                                f"{' (' + formats + ')' if formats else ''} "
                                f"{'have' if len(unknown) > 1 else 'has'} no language tag."))
                rows["unknown_audio"].append(row)
        g = SIZE_GROUP.get(c["resolution"], "")
        if g in typical and c["size"] and c["duration_ms"] >= SIZE_MIN_MINUTES * 60000:
            rate = c["size"] * 8 / (c["duration_ms"] / 1000)
            ratio = rate / typical[g]
            if ratio >= SIZE_RATIO or ratio <= 1 / SIZE_RATIO:
                big = ratio >= SIZE_RATIO
                usual = typical[g] / 1e6
                spent = f"{size_text(c['size'])} for {_minutes(c['duration_ms'])} minutes"
                if big:
                    why = (f"At {rate / 1e6:.1f} Mbit/s it's {ratio:.1f} times the usual rate for "
                           f"{SIZE_GROUP_WORDS[g]} in your library ({usual:.1f} Mbit/s): {spent}.")
                else:
                    why = (f"At {rate / 1e6:.1f} Mbit/s it's {_fraction_words(ratio)} the usual rate for "
                           f"{SIZE_GROUP_WORDS[g]} in your library ({usual:.1f} Mbit/s): {spent}, so it's probably "
                           f"heavily compressed.")
                row = _base(film, c)
                row.update(resolution=c["resolution"], minutes=_minutes(c["duration_ms"]), size_gb=gb(c["size"]),
                           mbps=round(rate / 1e6, 1), usual_mbps=round(usual, 1), times=round(ratio, 2), why=why)
                rows["large" if big else "small"].append(row)

    # -- each film: missing details ------------------------------------------------------------
    guids = defaultdict(list)
    for pid, item in items.items():
        guids[item["film_key"]].append(item["guid"])
    all_by_film = defaultdict(list)
    for c in copies:
        all_by_film[c["film_key"]].append(c)
    for n, film in enumerate(films.values()):
        if n % 256 == 0:
            jobs.check()
        missing = []
        film_guids = guids.get(film.key, [])
        unmatched = bool(film_guids) and any((not g) or _UNMATCHED.match(g) for g in film_guids)
        if unmatched:
            missing.append("not matched in Plex")
        details = [label for label, gone in (("year", not film.year), ("genres", not film.genres),
                                              ("IMDb rating", film.imdb_rating is None)) if gone]
        missing += details
        mine_copies = by_film.get(film.key, [])
        unanalysed = [c for c in mine_copies if not c["analysed"]]
        if unanalysed:
            missing.append("file not analysed")
        no_file = bool(film_guids) and not all_by_film.get(film.key)
        if no_file:
            missing.append("no file")
        short = [c for c in mine_copies if _short_file(c)]
        if short:
            missing.append("; ".join(f"file runs {_minutes(c['duration_ms'])} of {_minutes(c['film_duration_ms'])} "
                                     f"min" for c in short))
        # your only rating of it was given to an earlier edition: Plex shows the copy you have as unrated
        earlier = bool(getattr(film, "owner_rating_from_earlier_edition", False)) and film.owner_rating is not None
        if earlier:
            missing.append("your rating (on an earlier edition)")
        if not missing:
            continue
        # one sentence: "Plex hasn't matched it, so it has no genres or IMDb rating; Plex also hasn't analysed..."
        clauses = []
        list_words = (", ".join(details[:-1]) + " or " + details[-1]) if len(details) > 1 else \
            (details[0] if details else "")
        if unmatched:
            clauses.append("hasn't matched it" + (f", so it has no {list_words}" if details else ""))
        elif details:
            clauses.append(f"has no {list_words} for it")
        if unanalysed:
            clauses.append("hasn't analysed " + ("its file" if len(mine_copies) == 1 else
                                                 f"{len(unanalysed)} of its {len(mine_copies)} files")
                           + " (so its picture and tracks are unknown)")
        if no_file:
            clauses.append("has no file for it")
        whys = ["Plex " + clauses[0] + "".join(f"; Plex also {c}" for c in clauses[1:]) + "."] if clauses else []
        if short:
            whys.append(_short_why(short, len(mine_copies)))
        if earlier:
            whys.append(f"You rated it {_rating(film.owner_rating)} out of 10 on an earlier edition, so Plex shows "
                        "the copy you have as unrated (and smart collections by rating leave it out): rate it "
                        "again in Plex to carry the rating over.")
        row = _base(film)
        row.update(missing="; ".join(missing), unmatched=unmatched, short_file=bool(short), earlier_rating=earlier,
                   why=" ".join(whys))
        if short:
            row["paths"] = [_first_path(c) for c in short if _first_path(c)]
        rows["metadata"].append(row)

    # -- disk use ----------------------------------------------------------------------------------------------------
    seen_paths = set()
    totals = {"library": Counter(), "resolution": Counter(), "codec": Counter(), "drive": Counter()}
    counts = {k: Counter() for k in totals}
    total_bytes = files = 0
    for c in available:
        film = films.get(c["film_key"])
        if film is None:
            continue
        path = _first_path(c)
        drive = drive_of(path)
        row = _base(film, c)
        row.update(resolution=c["resolution"] or "Unknown", codec=c["video_codec"] or "Unknown",
                   minutes=_minutes(c["duration_ms"]), size_gb=gb(c["size"]), size_bytes=c["size"],
                   mbps=round(c["bitrate"] / 1e6, 1) if c["bitrate"] else None, drive=drive, path=path)
        rows["disk"].append(row)
        if path and path in seen_paths:                  # one file in two libraries takes its space once
            continue
        seen_paths.add(path)
        total_bytes += c["size"]
        files += max(len(c["files"]), 1)
        for kind, label in (("library", c["library"] or "Unknown"), ("resolution", c["resolution"] or "Unknown"),
                            ("codec", c["video_codec"] or "Unknown"), ("drive", drive or "Unknown")):
            totals[kind][label] += c["size"]
            counts[kind][label] += max(len(c["files"]), 1)
    rows["disk"].sort(key=lambda r: -r["size_bytes"])
    for n, r in enumerate(rows["disk"], 1):
        r["why"] = f"Number {n:,} by size: {size_text(r['size_bytes'])}" + \
            (f" for {r['minutes']} minutes of {r['resolution']}." if r["minutes"] else ".")

    def breakdown(kind, order=None):
        labels = list(totals[kind])
        if order:
            labels.sort(key=lambda x: order.index(x) if x in order else len(order))
        else:
            labels.sort(key=lambda x: (-totals[kind][x], x))
        return [{"label": x, "bytes": totals[kind][x], "files": counts[kind][x]} for x in labels]
    disk = {"total_bytes": total_bytes, "files": files,
            "by_library": breakdown("library"), "by_resolution": breakdown("resolution", RESOLUTION_ORDER),
            "by_codec": breakdown("codec"), "by_drive": breakdown("drive")}

    # -- default orders ------------------------------------------------------------------------------------------------
    base = {"title_order": {}}
    by_title = _title_order(catalog, base)
    rows["duplicates"].sort(key=lambda r: (-r["extra_bytes"], by_title(r)))
    rows["weaker"].sort(key=lambda r: (-r["size_bytes"], by_title(r)))
    rows["large"].sort(key=lambda r: (-r["times"], by_title(r)))
    rows["small"].sort(key=lambda r: (r["times"], by_title(r)))
    for k in ("unavailable", "editions", "unknown_audio", "metadata"):
        rows[k].sort(key=by_title)
    drives = len([d for d in totals["drive"] if d != "Unknown"])
    base.update(rows=rows, typical=typical, disk=disk, copies=len(copies), available=len(available),
                drives=drives, by_film=by_film, available_copies=available)
    return base


def _language_checks(catalog, base, languages) -> dict:
    """The two language lists for your languages: {'subtitles': rows, 'audio': rows}."""
    films = catalog.films
    mine = set(languages)
    # 'no English subtitles', 'no English or Japanese subtitles'... 'no subtitles in any of your languages'
    no_subtitles = "no " + subtitle_words(languages, many="subtitles in any of your languages")
    rows = {"subtitles": [], "audio": []}
    for n, c in enumerate(base["available_copies"]):
        if n % 256 == 0:
            jobs.check()
        film = films.get(c["film_key"])
        if film is None or not (c["analysed"] and c["audio"]):
            continue
        codes = [t["code"] for t in c["audio"]]
        known = [x for x in codes if x not in UNKNOWN_LANGUAGES]
        subs = {t["code"] for t in c["subtitles"] if not t["forced"]}
        forced = {t["code"] for t in c["subtitles"] if t["forced"]}
        readable = bool(subs & mine)
        main = c["main_audio"]
        if main and main not in UNKNOWN_LANGUAGES and main not in mine and not readable:
            dubs = [x for x in languages if x in set(known)]
            dubbed = bool(dubs)
            why = f"Its {'main ' if len(codes) > 1 else ''}soundtrack is in {lang_name(main)} and it has " \
                  f"{no_subtitles}"
            if forced & mine:
                why += " (only forced ones, which translate the odd sign)"
            if dubbed:
                dub_words = languages_words(dubs, "and", many="")
                dub_words = f"{dub_words} soundtrack{'s' if len(dubs) > 1 else ''}" if dub_words else \
                    f"soundtracks in {len(dubs)} of your languages"
                why += f", so you can only watch it dubbed ({dub_words})."
            else:
                why += "."
            row = _base(film, c)
            row.update(soundtracks=_soundtracks(c), subtitles=_subtitles_text(c), dubbed=dubbed, why=why)
            rows["subtitles"].append(row)
        if known and len(known) == len(codes) and not (set(known) & mine):
            names = []
            for x in known:
                if lang_name(x) not in names:
                    names.append(lang_name(x))
            spoken = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
            only = "Its only soundtrack is in" if len(codes) == 1 else \
                "Its soundtracks are all in" if len(names) == 1 else "Its soundtracks are in"
            read = subtitle_words([x for x in languages if x in subs], "and")
            row = _base(film, c)
            row.update(soundtracks=_soundtracks(c), your_subtitles="Yes" if readable else "No",
                       why=(f"{only} {spoken}; it has {read}." if readable else
                            f"{only} {spoken}, and it has {no_subtitles}."))
            rows["audio"].append(row)
    by_title = _title_order(catalog, base)
    for k in rows:
        rows[k].sort(key=by_title)
    return rows


def _upgrade_checks(catalog, base, min_rating, upgrade_below: str = UPGRADE_BELOW) -> list:
    """Films you rated min_rating or more whose best copy is below upgrade_below (1080p, or 4K...). A 1080p
    picture cropped at the sides, which Plex calls 720p, counts as 1080p: there's no sharper 1080p release to get,
    though it's still below 4K."""
    by_film = base["by_film"]
    below = RANK[upgrade_below]
    rows = {"upgrade": []}
    for n, film in enumerate(catalog.films.values()):
        if n % 256 == 0:
            jobs.check()
        cs = [c for c in by_film.get(film.key, []) if c["resolution"] in RANK]
        if film.owner_rating is not None and film.owner_rating >= min_rating and cs:
            best = max(cs, key=_ranker(cs))
            if _rank(best) < below:
                rating = _rating(film.owner_rating)
                size = f" ({best['width']}×{best['height']})" if best.get("width") and best.get("height") else ""
                if _cropped_1080(best):               # (only below a 4K target: 1080p cropped at the sides)
                    shown, size = "1080p", f" ({best['width']}×{best['height']}, cropped at the sides)"
                else:
                    shown = best["resolution"]
                row = _base(film, best)
                row.update(edition=best["edition"], your_rating=rating, best=shown,
                           plays=film.owner_plays, imdb=film.imdb_rating, size_gb=gb(best["size"]),
                           why=f"You rated it {rating} out of 10, but your best copy is only {shown}{size}.")
                rows["upgrade"].append(row)
    by_title = _title_order(catalog, base)
    rows["upgrade"].sort(key=lambda r: (-(r["your_rating"] or 0), by_title(r)))
    return rows["upgrade"]


def _tile_counts(rows, languages) -> dict:
    return {"audio_with_subs": sum(1 for r in rows["audio"] if r["your_subtitles"] == "Yes"),
            "unmatched": sum(1 for r in rows["metadata"] if r.get("unmatched")),
            "short_files": sum(1 for r in rows["metadata"] if r.get("short_file")),
            "earlier_ratings": sum(1 for r in rows["metadata"] if r.get("earlier_rating")),
            "duplicates_extra": sum(r["extra_bytes"] for r in rows["duplicates"]),
            "weaker_bytes": sum(r["size_bytes"] for r in rows["weaker"])}


# ---------------------------------------------------------------------------------------------------------
# The answer
# ---------------------------------------------------------------------------------------------------------
def explanation(issue: str, min_rating: float = HIGH_RATING, upgrade_below: str = UPGRADE_BELOW) -> str:
    return EXPLANATIONS[issue].format(rated=rated_words(min_rating), upgrade_below=upgrade_below)


def limits(issue: str, owner: str = "") -> str:
    return LIMITS[issue].format(size_min_files=SIZE_MIN_FILES, size_min_minutes=SIZE_MIN_MINUTES,
                                owner=f", {owner}" if owner else "", cropped_height=f"{CROPPED_1080_HEIGHT:,}",
                                short_share=f"{SHORT_FILE_SHARE:.0%}", short_min_minutes=SHORT_MIN_MINUTES)


def _note(issue: str, count: int, a: dict) -> str:
    counts, languages = a["counts"], a["languages"]
    if issue == "disk":
        drives = a["drives"]
        files = a["disk"]["files"]
        return f"{files:,} file{'s' if files != 1 else ''} on {drives} drive{'s' if drives != 1 else ''}"
    if not count:
        return {"unavailable": "all files found", "editions": "none kept", "audio": "all have one of yours",
                "unknown_audio": "every soundtrack tagged", "metadata": "nothing missing"}.get(issue, "nothing to do")
    if issue == "duplicates":
        return f"same cut twice · {size_text(counts['duplicates_extra'])} extra"
    if issue == "weaker":
        return f"{size_text(counts['weaker_bytes'])} to free"
    if issue == "large":
        return f"{SIZE_RATIO:g}× the usual size or more"
    if issue == "small":
        return "under a third of the usual size"
    if issue == "unavailable":
        return "marked missing by Plex"
    if issue == "editions":
        return "kept on purpose"
    if issue == "subtitles":            # ('no subtitles you read' would only repeat the title)
        if len(languages) > 3:
            return f"none in your {len(languages)} languages"
        return f"no {subtitle_words(languages)}"
    if issue == "audio":
        n = counts["audio_with_subs"]
        return f"{n:,} {'has' if n == 1 else 'have'} {subtitle_words(languages, many='subtitles you read')}"
    if issue == "unknown_audio":
        return "a soundtrack with no language"
    if issue == "upgrade":
        rated = "10" if a["min_rating"] >= 10 else f"{_rating(a['min_rating'])}+"
        return f"rated {rated} but below {a['upgrade_below']}"
    if issue == "metadata":
        n = counts["unmatched"]
        if n:
            return f"{n:,} not matched in Plex"
        if counts["short_files"] == count:
            return "files far shorter than the film"
        if counts.get("earlier_ratings") == count:
            return "rated on an earlier edition"
        return "no year, genres or IMDb rating"
    return ""


def columns(issue: str) -> list[dict]:
    return [{"key": k, "heading": h, "kind": kind} for k, h, kind in COLUMNS[issue]]


def backup_note(catalog) -> str:
    """Plainly: what was read, and that nothing was changed."""
    source = getattr(catalog, "source", "") or ""
    if not source or not os.path.isfile(source):
        return "Nothing is changed in Plex or on your drives: this only reads."
    from .files import dump_date
    when = dump_date(source)
    what = f"your Plex backup of {when}" if when in os.path.basename(source) else f"your Plex database ({when})"
    return (f"Read from {what}: files added or removed since then don't show, and nothing is changed in Plex or "
            f"on your drives.")


def _summary_item(issue: str, a: dict) -> dict:
    rows = a["rows"][issue]
    count = len(rows)
    value = size_text(a["disk"]["total_bytes"]) if issue == "disk" else f"{count:,}"
    return {"id": issue, "title": TITLES[issue], "severity": SEVERITY[issue],
            "count": count, "value": value, "note": _note(issue, count, a),
            "explanation": explanation(issue, a["min_rating"], a["upgrade_below"])}


def available_languages(data: dict) -> list[dict]:
    """The languages of the tracks in your files (soundtracks and subtitles), most tracks first."""
    got = data.get("languages")
    if got is None:
        counts = Counter()
        for c in data["copies"]:
            if c["unavailable"]:
                continue
            for t in c["audio"] + c["subtitles"]:
                if t["code"] in LANGUAGES:
                    counts[t["code"]] += 1
        got = data["languages"] = [{"code": k, "name": lang_name(k), "tracks": n}
                                   for k, n in sorted(counts.items(), key=lambda kv: (-kv[1], lang_name(kv[0])))]
    return got


def answer(catalog, request: dict | None = None) -> dict:
    """{"action": "doctor", "languages": ["en"], "issue": "weaker", "count": 50, "min_rating": 8,
    "upgrade_below": "1080p" (or "4K"), "film_key": "..."} -> the lists (see the module's docstring). A bad request
    raises ValueError (ask.handle turns it into ok: false); a database that can't be read gives ok: false."""
    from .recommend import number
    request = request or {}
    languages = parse_languages(request.get("languages"))
    only = issue_id(request["issue"]) if request.get("issue") not in (None, "") else None
    count = number(request, "count", None, 1, 100000, whole=True)
    min_rating = number(request, "min_rating", HIGH_RATING, 1, 10)
    upgrade_below = upgrade_target(request.get("upgrade_below"))
    film_key = request.get("film_key")
    if film_key not in (None, ""):
        film = catalog.films.get(str(film_key))
        if film is None:
            return {"ok": False, "error": f"no film with key '{film_key}'"}
        files = film_files(catalog, film.key)
        if not files.get("ok"):
            return {"ok": False, "error": files.get("error", "")}
        return {"ok": True, "film_key": film.key, "film": film.brief(), "files": files,
                "issues": film_issues(catalog, film.key, languages, min_rating, upgrade_below)}
    try:
        data = copies_data(catalog)
        a = analyse(catalog, languages, min_rating, upgrade_below)
    except CantRead as exc:
        return {"ok": False, "error": str(exc)}
    # (the Film page's issues - film_issues with no choices - use the ones last asked for)
    catalog.cache["doctor.languages"] = list(languages)
    catalog.cache["doctor.min_rating"] = float(min_rating)
    catalog.cache["doctor.upgrade_below"] = upgrade_below
    owner = getattr(catalog, "owner", "") or ""
    issues = {}
    for issue in ISSUE_IDS:
        if only is not None and issue != only:
            continue
        rows = a["rows"][issue]
        item = _summary_item(issue, a)
        item.update(limits=limits(issue, owner), columns=columns(issue),
                    rows=list(rows if count is None else rows[:count]))
        item["truncated"] = len(item["rows"]) < len(rows)
        issues[issue] = item
    disk = a["disk"]
    available_codes = {x["code"] for x in available_languages(data)}
    return {
        "ok": True, "action": "doctor", "database": getattr(catalog, "source", ""), "owner": owner,
        "note": backup_note(catalog),
        "languages": [{"code": c, "name": lang_name(c), "in_your_files": c in available_codes} for c in languages],
        "available_languages": available_languages(data),
        "totals": {"films": len(catalog.films), "copies": a["copies"], "available": a["available"],
                   "files": disk["files"], "size_bytes": disk["total_bytes"],
                   "size_tb": round(disk["total_bytes"] / 1e12, 2), "drives": a["drives"]},
        "summary": [_summary_item(issue, a) for issue in ISSUE_IDS],
        "issues": issues,
        "disk": disk,
        "typical_mbps": {g: round(a["typical"][g] / 1e6, 1) for g in ("8K", "4K", "1080p", "720p", "SD")
                         if g in a["typical"]},
        "settings": {"same_cut_minutes": SAME_CUT_MS / 60000, "weaker_bitrate_share": WEAKER_BITRATE_SHARE,
                     "size_ratio": SIZE_RATIO, "size_min_files": SIZE_MIN_FILES,
                     "size_min_minutes": SIZE_MIN_MINUTES, "min_rating": _rating(min_rating),
                     "upgrade_below": upgrade_below, "cropped_1080_height": CROPPED_1080_HEIGHT,
                     "short_file_share": SHORT_FILE_SHARE, "short_min_minutes": SHORT_MIN_MINUTES},
    }


def sheet_for(result: dict, issue: str) -> dict:
    """One list of an answer as a sheet for save_xlsx."""
    item = result["issues"][issue]
    return {"title": item["title"], "explanation": item["explanation"], "note": item.get("limits", ""),
            "columns": item["columns"], "rows": item["rows"]}


# ---------------------------------------------------------------------------------------------------------
# For the Film page
# ---------------------------------------------------------------------------------------------------------
def film_files(catalog, film_key) -> dict:
    """One film's copies and their files: path, size, resolution, codecs, soundtrack and subtitle languages.
    Quick from any thread (35 ms for a film's own read, instant once the whole library has been read). Never
    raises for an unknown key or a database that can't be read: ok is false then."""
    film = catalog.films.get(str(film_key)) if film_key is not None else None
    if film is None:
        return {"ok": False, "error": f"no film with key '{film_key}'", "film_key": film_key, "copies": []}
    try:
        copies = _film_copies(catalog, film)
    except CantRead as exc:
        return {"ok": False, "error": str(exc), "film_key": film.key, "copies": []}
    out = []
    for c in copies:
        files = []
        for f in c["files"]:
            folder, name = split_path(f["path"]) if f["path"] else ("", "")
            files.append({"path": f["path"], "folder": folder, "name": name, "size_gb": gb(f["size"]),
                          "duration_min": round(f["duration_ms"] / 60000, 1) if f["duration_ms"] else None})
        audio_langs, sub_langs = [], []
        for t in c["audio"]:
            if t["language"] not in audio_langs:
                audio_langs.append(t["language"])
        for t in c["subtitles"]:
            if t["language"] not in sub_langs:
                sub_langs.append(t["language"])
        added = local_datetime(c["added_at"])
        since = local_datetime(c["unavailable_since"])
        out.append({
            "plex_id": c["plex_id"], "media_id": c["media_id"], "version": c["version"], "edition": c["edition"],
            "library": c["library"], "resolution": c["resolution"], "width": c["width"], "height": c["height"],
            "video_codec": c["video_codec"], "hdr": c["hdr"], "container": c["container"],
            "bitrate_mbps": round(c["bitrate"] / 1e6, 1) if c["bitrate"] else None,
            "duration_min": round(c["duration_ms"] / 60000, 1) if c["duration_ms"] else None,
            "size_gb": gb(c["size"]), "size_bytes": c["size"], "files": files,
            "audio": [dict(t) for t in c["audio"]], "subtitles": [dict(t) for t in c["subtitles"]],
            "audio_languages": audio_langs, "subtitle_languages": sub_langs,
            "analysed": c["analysed"], "unavailable": c["unavailable"],
            "unavailable_since": since.strftime("%Y-%m-%d") if since else None,
            "added": added.strftime("%Y-%m-%d") if added else None,
        })
    return {"ok": True, "film_key": film.key, "film": film.label, "title": film.title, "year": film.year,
            "total_size_gb": gb(sum(c["size"] for c in copies if not c["unavailable"])), "copies": out}


def film_issues(catalog, film_key, languages=None, min_rating: float | None = None,
                upgrade_below: str | None = None) -> list[dict]:
    """One film's issues [{id, title, severity, why, plex_id, edition}], things to fix first. Needs the whole
    library's copies (the size checks compare with your other files): half a second the first time, so call it
    in the background. languages, min_rating, upgrade_below: None means the ones last asked for or shared by the
    Library Doctor tab (catalog.cache 'doctor.languages', 'doctor.min_rating', 'doctor.upgrade_below'), else
    English, 8 and 1080p. Never raises for an unknown key or a database that can't be read: the list is empty
    then."""
    film = catalog.films.get(str(film_key)) if film_key is not None else None
    if film is None:
        return []
    if languages is None:
        languages = catalog.cache.get("doctor.languages") or DEFAULT_LANGUAGES
    if min_rating is None:
        min_rating = catalog.cache.get("doctor.min_rating") or HIGH_RATING
    if upgrade_below is None:
        upgrade_below = catalog.cache.get("doctor.upgrade_below") or UPGRADE_BELOW
    try:
        a = analyse(catalog, languages, min_rating, upgrade_below)
    except (CantRead, ValueError):
        return []
    out = []
    for issue in ISSUE_IDS:
        if issue == "disk":
            continue
        for r in a["rows"][issue]:
            if r["film_key"] == film.key:
                out.append({"id": issue, "title": TITLES[issue], "severity": SEVERITY[issue], "why": r["why"],
                            "plex_id": r.get("plex_id"), "media_id": r.get("media_id"),
                            "edition": r.get("edition", "")})
    out.sort(key=lambda x: SEVERITY_ORDER[x["severity"]])        # stable: the lists' own order within
    return out


# ---------------------------------------------------------------------------------------------------------
# Saving a list
# ---------------------------------------------------------------------------------------------------------
_SHEET_BAD = re.compile(r"[\[\]:*?/\\]")
_WIDTHS = {"film": 42, "why": 90, "path": 70, "editions": 34, "soundtracks": 44, "subtitles": 30,
           "missing": 34, "better": 32, "libraries": 24, "library": 18, "edition": 24}
_NUMBER_KINDS = {"int": "#,##0", "gb": "0.00", "mbps": "0.0", "ratio": "0.00", "rating": "General", "score": "0.0",
                 "minutes": "0"}


def sheet_name(title: str, taken=()) -> str:
    name = _SHEET_BAD.sub("", str(title or "List")).strip().strip("'")[:31] or "List"
    base, n = name, 2
    while name.casefold() in {t.casefold() for t in taken}:
        suffix = f" ({n})"
        name = base[:31 - len(suffix)] + suffix
        n += 1
    return name


def save_xlsx(path: str, sheets: list[dict], about=()) -> str:
    """Write lists to an .xlsx: one worksheet per list - its title (bold), what it checks, the about line and
    the limits, then a header row (frozen, with filters) and the rows. Numbers stay numbers and text stays text
    (a title like '=Wonder' is never a formula). Written to a temporary file beside it, then moved into place, so
    a failure never leaves half a file. Raises export.OutputError with a plain message (e.g. the file is open in
    LibreOffice). Never opens the file. Returns the path."""
    import xlsxwriter
    from .export import OutputError, check_writable, usual_permissions
    path = os.path.abspath(path)
    check_writable(path)
    folder = os.path.dirname(path)
    name = os.path.basename(path)
    try:
        fd, tmp = tempfile.mkstemp(prefix="~projectionist-", suffix=".xlsx", dir=folder)
    except OSError as exc:
        raise OutputError(f"Can't save in {folder}: {exc.strerror or exc}") from None
    os.close(fd)
    about_line = "   ·   ".join(f"{a[0]}: {a[1]}" if isinstance(a, (tuple, list)) else str(a) for a in about)
    try:
        wb = xlsxwriter.Workbook(tmp, {"in_memory": True, "strings_to_numbers": False,
                                       "strings_to_formulas": False, "strings_to_urls": False,
                                       "nan_inf_to_errors": True})
        wb.set_properties({"title": "Library Doctor", "comments": f"Created by {APP_NAME}"})
        title_fmt = wb.add_format({"bold": True, "font_size": 14, "font_color": "#1F3A5F"})
        muted = wb.add_format({"font_color": "#52514E"})
        small = wb.add_format({"font_color": "#898781", "italic": True})
        header = wb.add_format({"bold": True, "bottom": 1, "bg_color": "#F4F6F9", "text_wrap": True,
                                "valign": "top"})
        numbers = {k: wb.add_format({"num_format": f}) for k, f in _NUMBER_KINDS.items()}
        date_fmt = wb.add_format({"num_format": "yyyy-mm-dd", "align": "left"})
        taken = []
        for sheet in sheets:
            ws = wb.add_worksheet(sheet_name(sheet.get("title"), taken))
            taken.append(ws.get_name())
            cols = [c if isinstance(c, dict) else {"key": c[0], "heading": c[1], "kind": c[2] if len(c) > 2 else
                                                   "text"} for c in sheet.get("columns", [])]
            rows = list(sheet.get("rows", []))
            ws.write_string(0, 0, str(sheet.get("title") or ""), title_fmt)
            if sheet.get("explanation"):
                ws.write_string(1, 0, str(sheet["explanation"]), muted)
            if about_line:
                ws.write_string(2, 0, about_line, small)
            if sheet.get("note"):
                ws.write_string(3, 0, str(sheet["note"]), small)
            for ci, col in enumerate(cols):
                kind = col.get("kind", "text")
                width = _WIDTHS.get(col["key"], 18 if kind == "text" else 12)
                ws.set_column(ci, ci, width)
                ws.write_string(4, ci, str(col.get("heading", col["key"])), header)
            for ri, row in enumerate(rows, 5):
                for ci, col in enumerate(cols):
                    value = row.get(col["key"])
                    kind = col.get("kind", "text")
                    if value is None or value == "":
                        continue
                    if isinstance(value, bool):
                        ws.write_string(ri, ci, "Yes" if value else "No")
                    elif kind in _NUMBER_KINDS and isinstance(value, (int, float)):
                        ws.write_number(ri, ci, value, numbers[kind])
                    elif kind == "date" and isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                        when = datetime.strptime(value, "%Y-%m-%d")
                        if when.year >= 1900:
                            ws.write_datetime(ri, ci, when, date_fmt)
                        else:
                            ws.write_string(ri, ci, value)
                    elif isinstance(value, (int, float)):
                        ws.write_number(ri, ci, value)
                    else:
                        text = value if isinstance(value, str) else ", ".join(map(str, value)) \
                            if isinstance(value, (list, tuple)) else str(value)
                        ws.write_string(ri, ci, text[:32767])
            if cols:
                ws.freeze_panes(5, 1)
                ws.autofilter(4, 0, 4 + max(len(rows), 1), len(cols) - 1)
        if not sheets:
            wb.add_worksheet("List")
        try:
            wb.close()
        except Exception as exc:          # xlsxwriter's FileCreateError etc. - e.g. the disk filled up
            raise OutputError(f"Couldn't save {name}: {exc}") from exc
        usual_permissions(tmp, path)
        try:
            os.replace(tmp, path)
        except PermissionError:
            raise OutputError(f"Couldn't replace {name} - it's open in another program (probably LibreOffice "
                              "Calc or Excel).\n\nClose it and save again.") from None
        except OSError as exc:
            raise OutputError(f"Couldn't save {name}: {exc.strerror or exc}") from None
        return path
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
