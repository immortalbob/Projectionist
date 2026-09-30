"""Your plays and ratings as a file Letterboxd can import: the server owner's diary, for letterboxd.com.

    rows(catalog, options)        the file's lines (Line), in order - a list that also carries .columns, .counts,
                                  .since and .covered_to
    write(path, rows)             save them as Letterboxd's CSV, split into several files over its size limit
                                  -> the paths written
    summary(rows)                 '212 diary entries, 64 rated films without a date, 3 films with no IMDb ID ...'
    answer(catalog, request)      {"action": "letterboxd", ...} for projectionist.ask (it never writes a file)

Letterboxd's import format, as its help page describes it (https://letterboxd.com/about/importing-data/, read on
2026-09-29):
  - a CSV file "(with UTF-8 character encoding)", the column titles on its first line - at least one of
    LetterboxdURI, tmdbID, imdbID or Title - "in any order";
  - "There is a file size limit of 1MB, so you'll need to divide large files into multiple smaller files (the header
    row containing column names must be in every file)";
  - imdbID matches a film exactly; Title, with Year and Directors ("use commas to delimit multiple director names"),
    is a best guess, for films with no ID;
  - Rating10: whole numbers 1-10 that it "will be converted to 0.5-5 scale" (Rating takes half stars);
  - WatchedDate (YYYY-MM-DD) "creates a Diary Entry for the film on this calendar date" - a local calendar date,
    not a timestamp; Rewatch: "if true, sets the rewatch flag on the Diary Entry"; Tags are "added to Diary Entry
    when WatchedDate is provided";
  - "All films imported to your Profile will be automatically marked as watched": a line with no WatchedDate marks
    the film watched (and rates it) with no diary entry;
  - "Multiple lines containing the same film with the same WatchedDate will be combined into a single entry", and
    the importer updates a diary entry already there on that date - so saving the same plays again doubles nothing;
  - no space after the commas between columns; a value with a comma in it goes in quotes, and a quote inside quoted
    text is escaped "by prefixing them with a backslash".

How the lines are made:
  - The server owner's account only, as on the Viewing tab. The setting "Also count as seen" doesn't count here:
    it's for what the app suggests you (films played on a profile you share), while a Letterboxd diary is yours -
    and a shared profile's plays can't be told from someone else watching alone.
  - Each dated play (habits.History, the Viewing tab's plays: a film logged twice in a row is one play, a 'marked as
    played' just after a play is that play) is a diary entry on the day it started - the start is estimated from
    the log, so a film that ran past midnight goes on the evening it began; one Plex was only told of goes on the
    day it was marked. Two plays of a film on one day are one entry (Letterboxd would combine them anyway).
  - Rewatch: the film has an earlier play on record (one marked in bulk too: that says you'd seen it), or Plex
    counts plays of it from before the history began - as the Viewing tab's year in review counts rewatches.
  - Films marked as played in bulk (setting a server up, ticking off films seen elsewhere) have no real watch date,
    so they're dateless, like the films Plex counts as played with no date at all and the films you rated without
    a play: one line each, marking the film watched (options 'undated' and, for rated films, 'ratings').
  - Your rating goes on a film's latest diary entry (Plex keeps only your rating now, not what you thought each
    time), or on its dateless line. Plex's 1-10 is Rating10 exactly - 8 is four stars. A film whose copies you
    rated differently has their average (7.5, say), rounded half up here (counts['ratings_rounded']).
  - Every line has the IMDb ID when Plex knows it - or else its TMDB ID (tmdbID, which Letterboxd also matches
    exactly), when Plex has that - and the title, year and directors for Letterboxd to match on when it has
    neither. A film deleted since goes by the title and year Plex logged (and the ID in an old agent's GUID).
  - Every library counts: the file is about what you watched, whichever libraries the spreadsheet leaves out -
    unless leave_out_libraries names some (a film that's also in another library stays).
  - since (only what's new): plays logged after it; a dateless film when it was marked, played or rated after it;
    and a film already in the diary whose rating changed after it gets a dateless line with the new rating.
    covered_to says when the newest thing read happened - the next export's since. Settings > Letterboxd also
    remembers what that export put in (its scope): when the next one puts in more - ratings or library tags
    turned on, a library it left out now in - 'only what's new' would miss the older plays and ratings the last
    file didn't have, so that export has everything (Letterboxd merges what it already has).
"""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from . import APP_NAME, formats, habits, jobs, prefs
from .catalog import Catalog, fold
from .extract import (METADATA_MOVIE, TAG_GUID, PlexDatabase, PlexDBError, _read_error, clean, external_ids,
                      local_datetime, parse_timestamp, plex_movie_id)

FORMAT_URL = "https://letterboxd.com/about/importing-data/"
MAX_BYTES = 1_000_000               # Letterboxd's "1MB" a file (under a binary megabyte too)
NEWLINE = "\r\n"
COLUMNS = ["imdbID", "tmdbID", "Title", "Year", "Directors", "WatchedDate", "Rewatch", "Rating10", "Tags"]
DEFAULTS = {"diary": True, "undated": True, "ratings": True, "library_tags": False}
EXTRAS_KEY = "letterboxd.extras"
CHECK_EVERY = 500


@dataclass
class Line:
    """One line of the file: a diary entry (a dated play) or a film marked watched with no date ('undated')."""
    kind: str                       # 'diary' | 'undated'
    key: str | None                 # Film.key; None: a film deleted since
    title: str
    year: int | None
    imdb: str = ""
    directors: list = field(default_factory=list)
    watched: date | None = None     # the diary entry's day
    rewatch: bool = False
    rating: int | None = None       # Rating10, 1-10
    tags: list = field(default_factory=list)
    at: int = 0                     # when it happened (Unix): the play's log, or when a dateless film last changed
    tmdb: str = ""                  # the TMDB ID - only for a film with no IMDb ID

    def cells(self) -> dict:
        """{column: text} - every column Letterboxd reads (write() keeps the ones asked for)."""
        diary = self.kind == "diary"
        return {"imdbID": self.imdb, "tmdbID": "" if self.imdb else self.tmdb, "Title": self.title,
                "Year": str(self.year) if self.year else "",
                "Directors": ", ".join(self.directors),
                "WatchedDate": self.watched.isoformat() if diary and self.watched else "",
                "Rewatch": ("true" if self.rewatch else "false") if diary else "",
                "Rating10": str(self.rating) if self.rating is not None else "",
                "Tags": ", ".join(self.tags) if diary else ""}

    def to_dict(self) -> dict:
        return dict({"kind": self.kind, "key": self.key}, **self.cells())


class Rows(list):
    """rows()'s answer: the Lines in the file's order - diary entries oldest first, then the dateless films by
    title - and what the file is made of."""

    def __init__(self, lines=(), columns=None, counts=None, since=None, covered_to=None, scope=None, wider=False):
        super().__init__(lines)
        self.columns = list(columns or COLUMNS)
        self.counts = dict(counts or {})
        self.since = since                  # the 'since' asked for (ISO), or None
        self.covered_to = covered_to        # when the newest thing read happened (ISO, with its UTC offset)
        self.scope = scope                  # what it put in, apart from since (see scope()), or None
        self.wider = wider                  # everything, as it puts in more than the last export (see options_for)


# ---------------------------------------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------------------------------------
def flag(name: str, value) -> bool:
    """A yes/no option: true/false (or 1/0, 'yes'/'no'). Anything else is a ValueError."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.strip().lower() in ("true", "false", "yes", "no", "1", "0"):
        return value.strip().lower() in ("true", "yes", "1")
    raise ValueError(f"{name} must be true or false, not {value!r}")


def since_time(value) -> float | None:
    """'since' as Unix time: an ISO date ('2026-09-23': from the start of that day, local time) or date and time
    ('2026-09-23T21:30', local; or with its UTC offset, as covered_to gives it). None or '' is none."""
    if value is None or value == "":
        return None
    if isinstance(value, str):
        value = value.strip()
        if value[-1:] in ("Z", "z"):               # (UTC written as Z: Python before 3.11 doesn't read it)
            value = value[:-1] + "+00:00"
        try:
            when = datetime.fromisoformat(value)
        except ValueError:
            pass
        else:
            return when.timestamp()          # (a time with no offset is local time)
    raise ValueError(f"since must be a date or a time like 2026-09-23 or 2026-09-23T21:30, not {value!r}")


def iso_time(ts) -> str | None:
    """Unix time -> local ISO time with its UTC offset ('2026-09-20T21:05:12+01:00'), exact both ways."""
    if not ts:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone().isoformat(timespec="seconds")


def latest(*values) -> str | None:
    """The latest of some ISO times (None and '' skipped)."""
    times = [(since_time(v), v) for v in values if v]
    return max(times)[1] if times else None


def _names(value) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set)) and all(isinstance(v, str) for v in value):
        return [v for v in value if v]
    raise ValueError(f"leave_out_libraries must be a list of library names, not {value!r}")


def options(request: dict | None = None) -> dict:
    """A request's options, checked: diary, undated, ratings, library_tags (true/false), since (see since_time),
    leave_out_libraries (names). ValueError for a bad one."""
    request = request or {}
    out = {name: flag(name, default if request.get(name) is None else request.get(name))
           for name, default in DEFAULTS.items()}
    out["since"] = since_time(request.get("since"))
    out["since_text"] = request.get("since") or None
    out["leave_out_libraries"] = _names(request.get("leave_out_libraries"))
    # (options_for's note that 'only what's new' was set aside, as this puts in more than the last export: words)
    out["wider"] = flag("wider", request.get("wider") or False)
    return out


def scope(opts: dict) -> dict:
    """What an export puts in, apart from since - its four choices and the libraries it leaves out (as options()
    gives them) - to be remembered with the time it went up to (see wider())."""
    return {name: bool(opts[name]) for name in DEFAULTS} | \
        {"leave_out": sorted({name.casefold() for name in opts["leave_out_libraries"]})}


def wider(now: dict, before) -> bool:
    """Whether an export with the scope `now` puts in something the one with `before` didn't: a choice turned on
    since, or a library it left out that's in now. (False when the last one's isn't known.)"""
    if not isinstance(before, dict):
        return False
    if any(now.get(name) and before.get(name) is False for name in DEFAULTS):
        return True
    left_out = before.get("leave_out")
    return isinstance(left_out, list) and bool(set(left_out) - set(now.get("leave_out") or []))


def columns_for(opts: dict) -> list[str]:
    """The columns a file needs: the film (IMDb or TMDB ID, title, year, directors), then what the options put
    in."""
    cols = ["imdbID", "tmdbID", "Title", "Year", "Directors"]
    if opts["diary"]:
        cols += ["WatchedDate", "Rewatch"]
    if opts["ratings"]:
        cols.append("Rating10")
    if opts["diary"] and opts["library_tags"]:
        cols.append("Tags")
    return cols


def rating10(value) -> int | None:
    """Plex's rating (1-10) as Letterboxd's Rating10: a whole number from 1 to 10, rounded half up (an average of
    two copies' ratings can be 7.5); None when there's none."""
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or value < 0.5:
        return None
    return int(min(math.floor(value + 0.5), 10))


# ---------------------------------------------------------------------------------------------------------
# The lines
# ---------------------------------------------------------------------------------------------------------
def _extras(catalog: Catalog, owner_id) -> dict:
    """What the file needs from Plex beyond the catalog and the play history - read once per catalog:
        rated_at   {film key: when the owner last rated it (Unix)}, from Plex's last_rated_at ({} for a database
                   without it: then a changed rating can't be told from an old one)
        tmdb       {film key: its TMDB ID}, for the films with no IMDb ID (a GUID tag, or an old agent's GUID)"""
    cached = catalog.cache.get(EXTRAS_KEY)
    if cached is not None:
        return cached
    films = catalog.films
    no_imdb = {pid: f.key for f in films.values() if not f.imdb_id for pid in f.plex_ids}
    items, rated, tags = [], [], []
    jobs.check()
    with PlexDatabase(catalog.source) as db:
        try:
            items = db.query(f"SELECT id, guid FROM metadata_items WHERE metadata_type = {METADATA_MOVIE}").fetchall()
            if owner_id is not None and db.has_table("metadata_item_settings") and \
                    {"account_id", "guid", "rating", "last_rated_at"} <= db.columns("metadata_item_settings"):
                rated = db.query("SELECT guid, last_rated_at FROM metadata_item_settings "
                                 "WHERE account_id = ? AND rating IS NOT NULL", (owner_id,)).fetchall()
            if no_imdb:
                tags = db.query(f"SELECT tg.metadata_item_id AS id, t.tag FROM taggings tg JOIN tags t ON "
                                f"t.id = tg.tag_id WHERE t.tag_type = {TAG_GUID} AND t.tag LIKE 'tmdb://%' "
                                f"ORDER BY tg.metadata_item_id, tg.id").fetchall()
        except sqlite3.DatabaseError as exc:
            jobs.check()
            raise _read_error(db.path, exc) from exc
    id_to_key = {pid: f.key for f in films.values() for pid in f.plex_ids}
    guid_to_key = {}
    for r in items:
        if r["id"] in id_to_key and clean(r["guid"]):
            guid_to_key.setdefault(clean(r["guid"]), id_to_key[r["id"]])
    when_rated: dict = {}
    for r in rated:
        guid = clean(r["guid"])
        key = guid_to_key.get(guid) or (plex_movie_id(guid) if plex_movie_id(guid) in films else None)
        when = parse_timestamp(r["last_rated_at"])
        if key and when and when > when_rated.get(key, 0):
            when_rated[key] = when
    tmdb: dict = {}
    for r in tags:                                  # the film's first copy with one, as the IMDb ID is taken
        value = _tmdb_id(clean(r["tag"])[len("tmdb://"):])
        if r["id"] in no_imdb and value:
            tmdb.setdefault(no_imdb[r["id"]], value)
    for r in items:                                 # an old agent's GUID has it in itself
        if r["id"] in no_imdb and no_imdb[r["id"]] not in tmdb:
            value = _tmdb_id(external_ids(clean(r["guid"]), {})[0].get("tmdb"))
            if value:
                tmdb[no_imdb[r["id"]]] = value
    out = {"rated_at": when_rated, "tmdb": tmdb}
    catalog.cache[EXTRAS_KEY] = out
    return out


def _tmdb_id(value) -> str:
    """A TMDB ID as Letterboxd's tmdbID takes it (a number), or ''."""
    value = clean(value)
    return value if re.fullmatch(r"\d{1,12}", value) else ""


def rated_at(catalog: Catalog, owner_id) -> dict:
    """{film key: when the owner last rated it (Unix)}, from Plex's last_rated_at - read once per catalog. A
    database without it gives {} (then a changed rating can't be told from an old one)."""
    return _extras(catalog, owner_id)["rated_at"]


def _day(p: habits.Play) -> date:
    """The diary's day for a play: the day it started (estimated), else the day it was logged."""
    start = local_datetime(p.start) if p.start else None
    return start.date() if start else p.day


def _tag(name: str) -> str:
    """A library's name as a tag (a comma would split it in two)."""
    return " ".join(name.replace(",", " ").split())


def _logged_ids(plays) -> tuple[str, str]:
    """A deleted film's (IMDb ID, TMDB ID), from the GUIDs Plex logged its plays under (an old agent's has one),
    each '' when none has it. (The TMDB ID only when there's no IMDb ID, as for the films still there.)"""
    ids = [external_ids(p.guid, {})[0] for p, _rewatch in plays]
    imdb = next((x["imdb"] for x in ids if x.get("imdb")), "")
    tmdb = next((_tmdb_id(x["tmdb"]) for x in ids if _tmdb_id(x.get("tmdb"))), "")
    return imdb, "" if imdb else tmdb


def rows(catalog: Catalog, request: dict | None = None) -> Rows:
    """The lines of the file for these options (see options() and the module's docstring). Raises ValueError for a
    bad option, PlexDBError (or sqlite3.DatabaseError) when the play history can't be read, and jobs.Cancelled in a
    job that's been called off."""
    opts = options(request)
    h = habits.history(catalog)
    films = catalog.films
    since = opts["since"]
    extras = _extras(catalog, h.owner_id)
    rated, tmdb = extras["rated_at"], extras["tmdb"]
    leave_out = {name.casefold() for name in opts["leave_out_libraries"]}
    counts = Counter()

    # every film's plays on record, oldest first, each with whether it's a rewatch (as the year in review says)
    by_film, earlier = defaultdict(list), set()
    for p in h.plays:
        rewatch = p.ident in earlier or bool(p.key and h.extra.get(p.key, 0) > 0)
        earlier.add(p.ident)
        by_film[p.ident].append((p, rewatch))
    idents = list(by_film) + [key for key, f in films.items() if key not in by_film and f.watched]

    diary, undated, no_imdb, by_tmdb = [], [], set(), set()
    for n, ident in enumerate(idents):
        if not n % CHECK_EVERY:
            jobs.check()
        plays = by_film.get(ident, [])
        film = films.get(ident)
        if film is not None and leave_out and film.libraries and \
                all(lib.casefold() in leave_out for lib in film.libraries):
            counts["left_out"] += 1
            continue
        if film is not None:
            base = dict(key=film.key, title=film.title, year=film.year, imdb=film.imdb_id,
                        tmdb="" if film.imdb_id else tmdb.get(film.key, ""),
                        directors=[c.name for c in film.directors])
            tags = [_tag(lib) for lib in film.libraries if _tag(lib)] if opts["library_tags"] else []
            rating = rating10(film.owner_rating) if opts["ratings"] else None
        else:                                   # deleted since: as Plex logged it
            first = plays[0][0]
            imdb, tmdb_id = _logged_ids(plays)
            base = dict(key=None, title=first.title, year=first.year, imdb=imdb, tmdb=tmdb_id, directors=[])
            tags, rating = [], None
        made = []
        dated = [(p, rw) for p, rw in plays if p.dated] if opts["diary"] else []
        if dated:
            entries = {}                        # day -> (play, rewatch): one diary entry a day
            for p, rw in dated:
                day = _day(p)
                if day in entries:
                    counts["same_day"] += 1
                    continue
                entries[day] = (p, rw)
            last_day = max(entries)
            for day, (p, rw) in sorted(entries.items()):
                if since is not None and p.at <= since:
                    counts["older"] += 1
                    continue
                made.append(Line("diary", watched=day, rewatch=rw, rating=rating if day == last_day else None,
                                 tags=tags, at=p.at, **base))
            diary += made
            # a rating changed since, on a film with nothing new in the diary: a dateless line carries it
            if since is not None and not made and rating is not None and rated.get(ident, 0) > since:
                line = Line("undated", rating=rating, at=rated[ident], **base)
                made.append(line)
                undated.append(line)
                counts["ratings_changed"] += 1
        else:
            # no dated play (or diary entries off): one line with no date - if you've seen it, and it's wanted
            seen = bool(plays) or (film is not None and film.watched)
            if seen and (rating is not None or opts["undated"]):
                changed = max([p.at for p, _ in plays] + [(film.last_played or 0) if film else 0, rated.get(ident, 0)])
                if since is not None and changed <= since:
                    counts["older"] += 1
                else:
                    if plays and all(p.how == "bulk" for p, _ in plays):
                        counts["bulk_marked"] += 1
                    line = Line("undated", rating=rating, at=changed, **base)
                    made.append(line)
                    undated.append(line)
        if made:
            if not base["imdb"] and not base["tmdb"]:
                no_imdb.add(ident)                  # (matched by its title, year and directors)
            elif not base["imdb"]:
                by_tmdb.add(ident)
            if film is None:
                counts["deleted_since"] += 1
            if rating is not None and film is not None and float(film.owner_rating) != rating:
                counts["ratings_rounded"] += 1

    diary.sort(key=lambda x: (x.watched, x.at, fold(x.title)))
    undated.sort(key=lambda x: (fold(x.title), x.year or 0, x.key or ""))
    lines = diary + undated
    counts.update(diary=len(diary), rewatches=sum(x.rewatch for x in diary),
                  rated_undated=sum(x.rating is not None for x in undated),
                  played_undated=sum(x.rating is None for x in undated),
                  films=len({x.key or f"gone:{fold(x.title)}:{x.year}" for x in lines}), no_imdb=len(no_imdb),
                  by_tmdb=len(by_tmdb), rated=sum(x.rating is not None for x in lines))
    times = [p.at for p in h.plays] + [f.last_played or 0 for f in films.values()] + list(rated.values())
    return Rows(lines, columns_for(opts), dict(counts), since=opts["since_text"],
                covered_to=iso_time(max(times)) if any(times) else None, scope=scope(opts),
                wider=opts["wider"])


# ---------------------------------------------------------------------------------------------------------
# The file
# ---------------------------------------------------------------------------------------------------------
def cell(value) -> str:
    """A value as Letterboxd's importer reads it: one line, no space around it, and in quotes - with any quote or
    backslash in it escaped by a backslash - when it has a comma, a quote or a backslash."""
    text = " ".join(str("" if value is None else value).split())
    if any(ch in text for ch in ',"\\'):
        return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return text


def csv_texts(lines, max_bytes: int = MAX_BYTES) -> list[str]:
    """The file's text - split into several, each with the header, so none is over max_bytes in UTF-8. At least
    one (only the header, when there are no lines)."""
    columns = getattr(lines, "columns", None) or COLUMNS
    header = ",".join(columns) + NEWLINE
    texts, current, size = [], [header], len(header.encode("utf-8"))
    for line in lines:
        cells = line.cells() if isinstance(line, Line) else line
        text = ",".join(cell(cells.get(c, "")) for c in columns) + NEWLINE
        n = len(text.encode("utf-8"))
        if len(current) > 1 and size + n > max_bytes:
            texts.append("".join(current))
            current, size = [header], len(header.encode("utf-8"))
        current.append(text)
        size += n
    texts.append("".join(current))
    return texts


def part_path(path: str, n: int) -> str:
    """The n-th file's name (from 1): 'Letterboxd.csv', then 'Letterboxd (2).csv', ..."""
    if n <= 1:
        return path
    stem, ext = os.path.splitext(path)
    return f"{stem} ({n}){ext or '.csv'}"


def write(path: str, lines, max_bytes: int = MAX_BYTES) -> list[str]:
    """Save the lines as Letterboxd's CSV (UTF-8, no byte-order mark - it would hide the first column's name).
    Over max_bytes, the rest go on in 'name (2).csv' and so on, each with the header. Each file is written whole
    or not at all. -> the paths written. OSError when one can't be."""
    written = []
    for n, text in enumerate(csv_texts(lines, max_bytes), 1):
        target = part_path(path, n)
        tmp = target + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8", newline="") as f:
                f.write(text)
            os.replace(tmp, target)
        except OSError:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
        written.append(target)
    return written


def file_name(db_path: str | None) -> str:
    """'Projectionist Letterboxd 2026-09-25.csv' - the backup's date, as the spreadsheet's name has it."""
    from .files import dump_date
    return f"{APP_NAME} Letterboxd {dump_date(db_path)}.csv" if db_path else f"{APP_NAME} Letterboxd.csv"


# ---------------------------------------------------------------------------------------------------------
# In words
# ---------------------------------------------------------------------------------------------------------
def _n(count: int, one: str, many: str) -> str:
    return f"{count:,} {one if count == 1 else many}"


def summary(lines) -> str:
    """'212 diary entries, 64 rated films without a date, 9 played films without a date, 3 films with no IMDb ID
    matched by title' - what's in the file; 'nothing' when it's empty."""
    c = getattr(lines, "counts", None) or {}
    parts = []
    if c.get("diary"):
        parts.append(_n(c["diary"], "diary entry", "diary entries"))
    if c.get("rated_undated"):
        parts.append(_n(c["rated_undated"], "rated film", "rated films") + " without a date")
    if c.get("played_undated"):
        parts.append(_n(c["played_undated"], "played film", "played films") + " without a date")
    if c.get("no_imdb"):
        parts.append(_n(c["no_imdb"], "film", "films") + " with no IMDb ID matched by title")
    return ", ".join(parts) if parts else "nothing"


def saved_words(lines, paths) -> str:
    """The Export tab's one line once the file is saved (or wasn't, with nothing to put in it)."""
    since = getattr(lines, "since", None)
    if not paths:
        if since:
            return f"Nothing new for Letterboxd since {formats.nice_date(since)} - no file saved."
        return "Nothing to save for Letterboxd with Settings > Letterboxd as it is - no file saved."
    text = f"Saved for Letterboxd: {summary(lines)}"
    if since:
        text += f" (new since {formats.nice_date(since)})"
    elif getattr(lines, "wider", False):
        text += " (everything, as Settings > Letterboxd now puts in more than the last file did)"
    if len(paths) > 1:
        text += f", in {len(paths)} files - Letterboxd takes 1 MB at a time"
    return text + "."


# ---------------------------------------------------------------------------------------------------------
# For projectionist.ask
# ---------------------------------------------------------------------------------------------------------
def answer(catalog: Catalog, request: dict | None = None) -> dict:
    """{"action": "letterboxd", diary, undated, ratings, library_tags, since, leave_out_libraries, csv: true for
    the file's text (one string a file), count: the lines to list (default all)}. The answer lists the lines as
    {kind, key, and the columns}; a bad option raises ValueError (ask.handle says so); an unreadable database
    gives ok: false. Nothing is written - saving the text is the front end's business."""
    from .recommend import number
    request = request or {}
    want_csv = flag("csv", False if request.get("csv") is None else request.get("csv"))
    count = number(request, "count", None, 0, 1_000_000, whole=True)
    try:
        lines = rows(catalog, request)
    except (PlexDBError, sqlite3.DatabaseError, OSError) as exc:
        return {"ok": False, "action": "letterboxd", "error": f"Couldn't read Plex's play history: {exc}"}
    texts = csv_texts(lines)
    out = {"ok": True, "action": "letterboxd", "owner": catalog.owner, "summary": summary(lines),
           "counts": lines.counts, "columns": lines.columns, "since": lines.since, "covered_to": lines.covered_to,
           "scope": lines.scope, "files": len(texts), "lines_total": len(lines),
           "lines": [x.to_dict() for x in (lines if count is None else lines[:count])],
           "format": FORMAT_URL,
           "note": "The owner's plays and ratings only. Dated plays are diary entries (the day each started); films "
                   "played with no date, or rated, are marked watched with no date. Films are matched by IMDb ID, "
                   "else TMDB ID, else title, year and directors. Letterboxd takes up to 1 MB a file - 'files' says "
                   "how many this needs."}
    if want_csv:
        out["csv"] = texts
    return out


# ---------------------------------------------------------------------------------------------------------
# Settings > Letterboxd, and the Export tab's button
# ---------------------------------------------------------------------------------------------------------
def _since_setting(value) -> str:
    if value in (None, ""):
        return ""
    since_time(value)                           # (ValueError for one that isn't a time)
    return str(value)


def _scope_setting(value) -> str:
    if value in (None, ""):
        return ""
    if not isinstance(value, str) or not isinstance(json.loads(value), dict):   # (ValueError for bad JSON)
        raise ValueError(f"not a Letterboxd export's scope: {value!r}")
    return value


prefs.section("Letterboxd", order=22,
              hint="What the Export tab's 'Export for Letterboxd...' puts in the file for Letterboxd's importer. "
                   "Only your own plays and ratings go in.")
prefs.define("letterboxd_diary", "Letterboxd", "Plays with a date go in your diary", kind="bool", default=True,
             help="Each play Plex logged becomes a diary entry on the day you watched it, and a film you'd seen "
                  "before is marked as a rewatch.")
prefs.define("letterboxd_undated", "Letterboxd", "Films played with no date", kind="bool", default=True,
             help="Films Plex counts as played but has no date for (from before its play history, or ticked off "
                  "in bulk) are marked as watched, with no diary entry.")
prefs.define("letterboxd_ratings", "Letterboxd", "Your ratings", kind="bool", default=True,
             help="Plex's ratings become Letterboxd's stars exactly (8 out of 10 is four stars). A film you rated "
                  "with no dated play goes in as watched, with its rating.")
prefs.define("letterboxd_tags", "Letterboxd", "Tag diary entries with their Plex library", kind="bool",
             default=False, help="Letterboxd keeps tags only on diary entries, so films without a date get none.")
prefs.define("letterboxd_only_new", "Letterboxd", "Only what's new since the last Letterboxd export", kind="bool",
             default=True,
             help="Plays, ratings and films marked as played since the last file you saved for Letterboxd (the "
                  "first time: everything - and everything again when the choices here put in more than that file "
                  "did). Turn it off to save everything again - Letterboxd merges a film's entries on the same day, "
                  "so nothing is doubled.")
prefs.define("letterboxd_leave_out", "Letterboxd", "Leave out the libraries unticked on the Export tab",
             kind="bool", default=False,
             help="Off: films from every library, whichever ones the spreadsheet leaves out. A film that's also in "
                  "a ticked library stays in.")
prefs.define("letterboxd_since", "Letterboxd", "The last Letterboxd export went up to", kind="text", default="",
             parse=_since_setting, shown=False)
prefs.define("letterboxd_scope", "Letterboxd", "What the last Letterboxd export put in", kind="text", default="",
             parse=_scope_setting, shown=False)
prefs.define("letterboxd_dir", "Letterboxd", "Save Letterboxd files in", kind="folder", default="", shown=False)


def last_scope(app) -> dict | None:
    """What the last export put in (see scope()), or None when that isn't known."""
    text = prefs.get(app, "letterboxd_scope")
    return json.loads(text) if text else None


def options_for(app, left_out=()) -> dict:
    """The request Settings > Letterboxd makes: its options, the last export's time when only what's new is wanted,
    and the libraries left out (left_out: the ones unticked on the Export tab) when those are to be. When it puts in
    more than the last export did, only what's new would miss what that one left out: then it has everything
    (since None, and wider: true to say why)."""
    request = {"diary": prefs.get(app, "letterboxd_diary"), "undated": prefs.get(app, "letterboxd_undated"),
               "ratings": prefs.get(app, "letterboxd_ratings"), "library_tags": prefs.get(app, "letterboxd_tags"),
               "since": (prefs.get(app, "letterboxd_since") or None) if prefs.get(app, "letterboxd_only_new")
               else None,
               "leave_out_libraries": list(left_out) if prefs.get(app, "letterboxd_leave_out") else []}
    if request["since"] and wider(scope(options(dict(request, since=None))), last_scope(app)):
        request.update(since=None, wider=True)
    return request


def hint(app) -> str:
    """The Export tab's line beside the button, before anything is saved. (The libraries left out are taken as
    they were last saved: the ticks on the Export tab can differ until the next export.)"""
    left_out = prefs.get(app, "excluded_libraries") if "excluded_libraries" in prefs.PREFS else []
    request = options_for(app, left_out)
    if request["since"]:
        return (f"Your plays and ratings since the last file ({formats.nice_date(request['since'])}), for "
                f"Letterboxd's importer - Settings > Letterboxd says what goes in.")
    if request.get("wider"):
        return ("All your plays and ratings this time, as Settings > Letterboxd now puts in more than the last file "
                "did - for Letterboxd's importer.")
    return "Your plays and ratings as a file for Letterboxd's importer - Settings > Letterboxd says what goes in."


def remember(app, lines) -> None:
    """After saving: the newest thing the file covered is where the next 'only what's new' starts (never going
    back, if an older backup was exported), and what the file put in is what that next one is measured against
    (kept only when it went up to the newest yet: an older backup's file doesn't say what the newer ones had)."""
    covered = getattr(lines, "covered_to", None)
    if not covered:
        return
    before = prefs.get(app, "letterboxd_since")
    if getattr(lines, "scope", None) is not None and (not before or since_time(covered) >= since_time(before)):
        prefs.set(app, "letterboxd_scope", json.dumps(lines.scope, sort_keys=True))
    prefs.set(app, "letterboxd_since", latest(before, covered))
