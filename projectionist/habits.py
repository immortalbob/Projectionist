"""Your viewing habits, from Plex's own play history - the server owner's movie plays only.

    answer(catalog, request)          {"action": "habits", "year": 2025, "min_rating": 9, "forgotten_days": 365,
                                       "week_start": "monday" (or "sunday": the busiest week's first day)}
    film_history(catalog, film_key)   one film's plays, resume point and rating (for the Film page)
    history(catalog)                  the plays themselves, read once per catalog and kept in catalog.cache

Plex keeps two records of what you've watched:

  metadata_item_views     one row per logged play: account, GUID, title, when (viewed_at), device and view_type.
                          This is the dated history. Plex logs a play when it passes about 90% of the film (checked
                          against Plex's own hourly playback clock), so viewed_at is roughly when the film ended.
                          view_type 1 means Plex was told the film was played - 'Mark as played', or an app's own
                          report - rather than seeing it play (only view_type 0 plays match Plex's playback clock).
  metadata_item_settings  one row per account and GUID: Plex's play count, your rating, a resume point.

The dated history only starts when this server's database was made: plays from before came over as counts with
no dates. So every number here says what period it covers, and the plays with no date are counted separately.

How the logs become plays (each rule checked on a real 19-month history):
  - The same film logged again within 6 hours of its previous log is the same play logged twice.
  - A 'marked as played' log up to 4 days after a play of that film is that same play.
  - So is marking another edition (cut) of a film as played, however much later, when that edition has never
    been logged and the film's latest play was played through: that keeps the cuts' watched ticks in step.
  - Different films logged within 2 minutes of each other were marked in bulk (setting a server up, ticking off
    films seen elsewhere), not watched then: they're left out of every dated number and kept in film_history.
  - A play's start is worked back from its log: the log time less 90% of the running time (one sitting assumed).
  - Plays of films deleted since count under the title and year Plex logged.

Plays Plex counts but never logged (its count beyond the logs) are undated plays. Plex counts each edition's GUID
on its own, and ticking off all the cuts of a film you've seen adds one to each, so a film's undated plays are
those of its most-counted edition, not the editions added up.
"""

from __future__ import annotations

import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from . import jobs
from .catalog import Catalog, fold
from .extract import (METADATA_MOVIE, PlexDatabase, PlexDBError, _Extractor, _read_error, clean, local_datetime,
                      parse_timestamp, plex_movie_id, to_float, to_int, utc_date)

CACHE_KEY = "habits.history"
DOUBLE_LOG_SECONDS = 6 * 3600       # the same film logged again this soon after its last log: one play, logged twice
MARKED_AFTER_SECONDS = 4 * 86400    # 'marked as played' this soon after a play of it: that same play
BULK_SECONDS = 120                  # different films logged this close together: marked in bulk, not watched then
LOGGED_AT = 0.9                     # Plex logs a play at ~90% of the film: start = log - 0.9 x running time
RESUME_MIN_SEC = 120                # a resume point under 2 minutes in: just a peek
RESUME_MIN_LEFT_SEC = 600           # ...or under 10 minutes from the end: as good as finished
CHECK_EVERY = 500                   # rows between jobs.check() calls
DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
               "November", "December"]
PARTS = ("history", "heatmap", "months", "streaks", "most_played", "stopped", "forgotten", "year")
LEAD_BILLING = 3                    # 'stars': billed in the top 3, as the Overview's 'Lead roles'


@dataclass
class Play:
    at: int                         # Unix time of the log that dates it (about when the film ended)
    key: str | None                 # Film.key; None: the film is no longer in the library
    title: str
    year: int | None
    how: str                        # 'played' | 'marked' (Plex didn't see it play) | 'bulk' (marked in bulk)
    device: str = ""
    logs: int = 1                   # logs that are this one play
    start: int | None = None        # estimated start ('played', with a known running time)
    guid: str = ""                  # the GUID of the log that dates it (which copy or edition)
    first_at: int = 0               # its first log (bulk marks are found by these)
    upgraded: bool = False          # a 'marked' play that a real playback log turned into 'played'

    @property
    def when(self) -> datetime:
        return local_datetime(self.at)

    @property
    def day(self) -> date:
        return self.when.date()

    @property
    def ident(self) -> str:
        """One per film: its key, or for a film deleted since, its logged title and year."""
        return self.key or gone_ident(self.title, self.year)

    @property
    def dated(self) -> bool:
        return self.how != "bulk"


def gone_ident(title: str, year) -> str:
    return f"gone:{fold(title)}:{year or ''}"


@dataclass
class History:
    owner_id: int
    plays: list                     # every kept play (bulk ones too), oldest first
    doubles: int                    # logs merged into a play they repeat
    skipped: int                    # plays of movies in libraries that aren't loaded
    resume: dict                    # film key -> (offset ms, stopped at (Unix) or None, that copy's length ms or None)
    measured: dict                  # 'YYYY-MM' (UTC month) -> seconds played by Plex's own clock
    as_of: date                     # the backup's date (or the last play, if later)
    server_since: date | None       # when this server's first library was made
    logs: Counter = field(default_factory=Counter)      # film ident -> every log of it (doubles and bulk too)
    extra: dict = field(default_factory=dict)           # film key -> plays Plex counts but never logged (undated)

    @property
    def dated(self) -> list:
        return [p for p in self.plays if p.dated]

    @property
    def start(self) -> date | None:
        """The first day of the dated history: the first play on record."""
        return self.plays[0].day if self.plays else None


# ---------------------------------------------------------------------------------------------------------
# Reading the history
# ---------------------------------------------------------------------------------------------------------
def history(catalog: Catalog) -> History:
    """The owner's plays, read from catalog.source once per catalog (30 ms or so on a big database; a zipped
    backup is unpacked again, which takes seconds - call it from a background job). Raises PlexDBError when the
    database can't be read, and jobs.Cancelled in a job that's been called off."""
    cached = catalog.cache.get(CACHE_KEY)
    if cached is not None:
        return cached
    jobs.check()
    with PlexDatabase(catalog.source) as db:
        try:
            raw = _read(db)
        except sqlite3.DatabaseError as exc:
            jobs.check()
            raise _read_error(db.path, exc) from exc
    h = _build(catalog, raw)
    catalog.cache[CACHE_KEY] = h
    return h


def _read(db: PlexDatabase) -> dict:
    ex = _Extractor(db, None, [], None, None)
    _names, owner_id = ex.load_accounts()
    items = db.query(f"SELECT {db.select_list('m', 'metadata_items', ['id', 'guid', 'duration'])} "
                     f"FROM metadata_items m WHERE m.metadata_type = {METADATA_MOVIE}").fetchall()
    jobs.check()
    views, settings, counts, stats, since = [], [], [], [], None
    if db.has_table("metadata_item_views"):
        cols = ["id", "guid", "title", "originally_available_at", "viewed_at", "view_type", "device_id"]
        views = db.query(f"SELECT {db.select_list('v', 'metadata_item_views', cols)} FROM metadata_item_views v "
                         f"WHERE v.account_id = ? AND v.metadata_type = {METADATA_MOVIE} "
                         f"ORDER BY v.viewed_at, v.id", (owner_id,)).fetchall()
    if "view_offset" in db.columns("metadata_item_settings"):
        cols = ["guid", "view_offset", "last_viewed_at", "updated_at"]
        settings = db.query(f"SELECT {db.select_list('s', 'metadata_item_settings', cols)} "
                            f"FROM metadata_item_settings s WHERE s.account_id = ? AND s.view_offset > 0",
                            (owner_id,)).fetchall()
    if "view_count" in db.columns("metadata_item_settings"):         # Plex's play count, per GUID
        counts = db.query(f"SELECT {db.select_list('s', 'metadata_item_settings', ['guid', 'view_count'])} "
                          f"FROM metadata_item_settings s WHERE s.account_id = ? AND s.view_count > 0",
                          (owner_id,)).fetchall()
    if {"account_id", "metadata_type", "timespan", "at", "duration"} <= db.columns("statistics_media"):
        stats = db.query(f"SELECT at, sum(duration) AS seconds FROM statistics_media WHERE account_id = ? "
                         f"AND metadata_type = {METADATA_MOVIE} AND timespan = 1 GROUP BY at", (owner_id,)).fetchall()
    if "created_at" in db.columns("library_sections"):
        row = db.query("SELECT min(created_at) FROM library_sections WHERE created_at > 0").fetchone()
        since = row[0] if row else None
    return {"owner_id": owner_id, "items": items, "views": views, "settings": settings, "counts": counts,
            "stats": stats, "since": since, "devices": ex.load_devices(), "source": db.path}


def _build(catalog: Catalog, raw: dict) -> History:
    films = catalog.films
    id_to_key = {pid: f.key for f in films.values() for pid in f.plex_ids}
    guid_to_key, guid_ms, movie_guids = {}, {}, set()
    for r in raw["items"]:
        guid = clean(r["guid"])
        if not guid:
            continue
        movie_guids.add(guid)
        if r["id"] in id_to_key:
            guid_to_key.setdefault(guid, id_to_key[r["id"]])
            if to_int(r["duration"]):
                guid_ms.setdefault(guid, to_int(r["duration"]))

    def key_of(guid):
        key = guid_to_key.get(guid)
        if key is None and plex_movie_id(guid) in films:      # an edition renamed since: the film it's an edition of
            key = plex_movie_id(guid)
        return key

    devices = raw["devices"]
    plays, last_log, logs = [], {}, Counter()
    guid_logs, logged = Counter(), defaultdict(set)      # GUID -> its logs; film ident -> the GUIDs logged so far
    doubles = skipped = 0
    for n, v in enumerate(raw["views"]):
        if not n % CHECK_EVERY:
            jobs.check()
        at, guid = parse_timestamp(v["viewed_at"]), clean(v["guid"])
        if not at:
            continue
        key = key_of(guid)
        if key is None and guid in movie_guids:
            skipped += 1                       # a movie in a library that isn't loaded
            continue
        if key is not None:
            title, year = films[key].title, films[key].year
        else:                                  # deleted since: by the title and year Plex logged
            title = clean(v["title"]) or "Untitled"
            released = utc_date(v["originally_available_at"]) if to_int(v["originally_available_at"]) else None
            year = released.year if released else None
        how = "marked" if to_int(v["view_type"]) == 1 else "played"
        p = Play(at, key, title, year, how, devices.get(v["device_id"], ""), guid=guid, first_at=at)
        logs[p.ident] += 1
        guid_logs[guid] += 1
        prev = last_log.get(p.ident)           # (when the film was last logged, the play that log belongs to)
        # another edition of a film you played, marked as played to keep the cuts' ticks in step: that same play
        edition_mark = (prev is not None and how == "marked" and key is not None and prev[1].how == "played"
                        and guid != prev[1].guid and guid not in logged[p.ident])
        logged[p.ident].add(guid)
        if prev is not None and (at - prev[0] < DOUBLE_LOG_SECONDS or edition_mark or
                                 (how == "marked" and at - prev[1].at < MARKED_AFTER_SECONDS)):
            play = prev[1]
            play.logs += 1
            if how == "played" and play.how == "marked":     # the log of it playing gives the play its time
                play.how, play.at, play.guid, play.upgraded = "played", at, guid, True
                play.device = p.device or play.device
            last_log[p.ident] = (at, play)
            doubles += 1
            continue
        last_log[p.ident] = (at, p)
        plays.append(p)
    # Bulk marks: different films whose first logs came within 2 minutes of each other. (A play that Plex later saw
    # play for real keeps its playback.)
    by_first = sorted(plays, key=lambda p: (p.first_at, p.at))
    for a, b in zip(by_first, by_first[1:]):
        if b.first_at - a.first_at < BULK_SECONDS and a.ident != b.ident:
            for p in (a, b):
                if not p.upgraded:
                    p.how = "bulk"
    plays.sort(key=lambda p: p.at)
    for p in plays:
        if p.how == "played" and p.key is not None:
            seconds = (guid_ms.get(p.guid) or 0) / 1000 or (films[p.key].runtime_min or 0) * 60
            if seconds:
                p.start = int(p.at - LOGGED_AT * seconds)

    resume = {}
    for r in raw["settings"]:
        guid = clean(r["guid"])
        key = guid_to_key.get(guid)            # a current copy's own resume point
        offset = to_int(r["view_offset"]) or 0
        if key is None or offset <= 0:
            continue
        stopped = parse_timestamp(r["last_viewed_at"]) or parse_timestamp(r["updated_at"])
        if key not in resume or offset > resume[key][0]:
            resume[key] = (offset, stopped or None, guid_ms.get(guid))
    # Plex's undated plays of each film: its count beyond the logs, per GUID. Each edition is counted on its own
    # (ticking off every cut of a film you've seen adds one to each), so a film has its most-counted edition's.
    extra, counted = {}, set()
    for r in raw.get("counts", []):
        guid = clean(r["guid"])
        key = guid_to_key.get(guid)            # a current copy's count, as the catalog's owner_plays
        if key is None or guid in counted:
            continue
        counted.add(guid)
        beyond = max(0, (to_int(r["view_count"]) or 0) - guid_logs[guid])
        if beyond > extra.get(key, 0):
            extra[key] = beyond
    measured = {}
    for r in raw["stats"]:
        at = parse_timestamp(r["at"])
        if at is not None:
            month = (datetime(1970, 1, 1) + timedelta(seconds=at)).strftime("%Y-%m")
            measured[month] = measured.get(month, 0) + (to_float(r["seconds"]) or 0)
    from .files import dump_date
    try:
        backup = date.fromisoformat(dump_date(raw["source"]))
    except ValueError:
        backup = None
    days = [d for d in (backup, plays[-1].day if plays else None) if d]
    since = local_datetime(raw["since"]) if raw["since"] else None
    return History(raw["owner_id"], plays, doubles, skipped, resume, measured,
                   max(days) if days else date.today(), since.date() if since else None, logs, extra)


# ---------------------------------------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------------------------------------
def _film(p: Play) -> dict:
    return {"key": p.key, "title": p.title, "year": p.year, "in_library": p.key is not None}


def _iso(ts) -> str | None:
    d = local_datetime(ts) if ts else None
    return d.isoformat(timespec="minutes") if d else None


def _streak(plays) -> dict | None:
    """The longest run of consecutive days with a dated play (the earlier one when two are as long), and how
    many of its days have only films marked as played (marked_only)."""
    days = {p.day for p in plays}
    played = {p.day for p in plays if p.how != "marked"}
    best = None
    for d in sorted(days):
        if d - timedelta(days=1) in days:
            continue
        n = 1
        while d + timedelta(days=n) in days:
            n += 1
        if best is None or n > best[0]:
            best = (n, d, d + timedelta(days=n - 1))
    if best is None:
        return None
    n, lo, hi = best
    return {"days": n, "from": lo.isoformat(), "to": hi.isoformat(),
            "marked_only": sum(lo + timedelta(days=i) not in played for i in range(n))}


def _busiest(counter: Counter):
    """(key, count) with the highest count; ties go to the earliest key. None when empty."""
    if not counter:
        return None
    return min(counter.items(), key=lambda kv: (-kv[1], kv[0]))


def _next_month(d: date) -> date:
    return date(d.year + d.month // 12, d.month % 12 + 1, 1)


def _average(values) -> float | None:
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), 2) if values else None


def _hours(seconds) -> float:
    return round(seconds / 3600, 1)


def _runtime_min(catalog: Catalog, p: Play) -> float:
    f = catalog.films.get(p.key) if p.key else None
    return (f.runtime_min or 0) if f else 0


def _params(request: dict) -> dict:
    from .recommend import number
    year = request.get("year")
    if year in (None, "", "latest"):
        year = None
    else:
        year = number(request, "year", None, 1, 9999, whole=True)
    parts = request.get("parts")
    if parts in (None, "", []):
        parts = set(PARTS)
    else:
        if isinstance(parts, str):
            parts = [parts]
        if not isinstance(parts, (list, tuple, set)):
            raise ValueError("parts must be a list")
        unknown = [str(x) for x in parts if str(x) not in PARTS]
        if unknown:
            raise ValueError(f"unknown parts {unknown}: choose from {list(PARTS)}")
        parts = {str(x) for x in parts}
    return {"year": year, "parts": parts,
            "min_rating": number(request, "min_rating", 9.0, 1, 10),
            "forgotten_days": number(request, "forgotten_days", 365, 30, 3650, whole=True),
            "count": number(request, "count", 100, 1, 500, whole=True),
            "top": number(request, "top", 5, 1, 20, whole=True),
            "week_start": week_start(request.get("week_start"))}


def week_start(value) -> int:
    """A request's week_start - 'monday' (the default) or 'sunday' - as date.weekday() counts the day (0 or 6)."""
    if value in (None, ""):
        return 0
    key = str(value).strip().lower()
    if key in ("monday", "mon"):
        return 0
    if key in ("sunday", "sun"):
        return 6
    raise ValueError(f"week_start must be 'monday' or 'sunday', not {value!r}")


# ---------------------------------------------------------------------------------------------------------
# The answer
# ---------------------------------------------------------------------------------------------------------
def answer(catalog: Catalog, request: dict | None = None) -> dict:
    """Everything the Viewing tab shows (see the module's docstring for how plays are counted). A bad request
    value raises ValueError (ask.handle turns it into ok: false); an unreadable database gives ok: false."""
    request = request or {}
    params = _params(request)
    try:
        h = history(catalog)
    except (PlexDBError, sqlite3.DatabaseError, OSError) as exc:
        return {"ok": False, "action": "habits", "error": f"Couldn't read Plex's play history: {exc}"}
    parts = params["parts"]
    dated = h.dated
    out = {"ok": True, "action": "habits", "owner": catalog.owner, "history": _history_part(catalog, h, dated)}
    if not dated:
        # Nothing dated: no charts over time - but Plex's counts, resume points and ratings still say something
        bulk = out["history"]["bulk_marked"]
        out.update(empty=True, years=[],
                   note="Plex has no plays logged for your account." if not bulk else
                   f"Plex has no dated plays for your account: the {bulk} plays it logged were marked in bulk, "
                   "which says you'd seen those films but not when.")
        parts = parts & {"most_played", "stopped", "forgotten"}
    if "heatmap" in parts:
        out["heatmap"] = _heatmap(dated, with_films=True)
    if "months" in parts:
        out["months"] = _months(catalog, h, dated)
    if "streaks" in parts:
        out["streaks"] = _streaks(h, dated, params["week_start"])
    if "most_played" in parts:
        rows = _most_played(catalog, h)
        out["most_played"], out["most_played_total"] = rows[:params["count"]], len(rows)
    if "stopped" in parts:
        out["stopped"] = _stopped(catalog, h)[:params["count"]]
    if "forgotten" in parts:
        rows, rule = _forgotten(catalog, h, params["min_rating"], params["forgotten_days"])
        out["forgotten"], out["forgotten_total"], out["forgotten_rule"] = rows[:params["count"]], len(rows), rule
    if not dated:
        return out
    years = Counter(p.when.year for p in dated)
    out["years"] = [{"year": y, "plays": years[y]} for y in sorted(years)]
    if "year" in parts:
        y = params["year"] if params["year"] is not None else max(years)
        out["year"] = year_in_review(catalog, h, y, params["top"])
    return out


def _history_part(catalog: Catalog, h: History, dated: list) -> dict:
    films = catalog.films
    on_record = {p.ident for p in h.plays}
    undated = [f for f in films.values() if h.extra.get(f.key) and f.key not in on_record]
    first = h.plays[0].when if h.plays else None
    last = h.plays[-1].when if h.plays else None
    # when those films were marked as played (Plex keeps when a film's watched tick was last set): how many in
    # the history's first week (or before it) - setting the server up, ticking off films seen before
    marked = [local_datetime(f.last_played) for f in undated]
    early = sum(1 for m in marked if first and m and m.date() <= first.date() + timedelta(days=6))
    return {
        "first": first.isoformat(timespec="minutes") if first else None,
        "last": last.isoformat(timespec="minutes") if last else None,
        "as_of": h.as_of.isoformat(),
        "server_since": h.server_since.isoformat() if h.server_since else None,
        "days": (h.as_of - first.date()).days + 1 if first else 0,
        "active_days": len({p.day for p in dated}),
        "plays": len(dated),
        "played": sum(p.how == "played" for p in h.plays),
        "marked": sum(p.how == "marked" for p in h.plays),
        "bulk_marked": sum(p.how == "bulk" for p in h.plays),
        "bulk_bursts": _bursts(h.plays),
        "double_logs": h.doubles,
        "skipped": h.skipped,
        "films": len({p.ident for p in dated}),
        "gone_plays": sum(p.key is None for p in dated),
        "undated_films": len(undated),
        "undated_plays": sum(h.extra[f.key] for f in undated),
        "undated_first_week": early,
        "plex_count": sum(f.owner_plays for f in films.values()),
        "measured_hours": _hours(sum(h.measured.values())) if h.measured else None,
        "runtime_hours": round(sum(_runtime_min(catalog, p) for p in dated) / 60, 1),
        "utc_offset": datetime.now().astimezone().strftime("%z"),
    }


def _bursts(plays) -> int:
    """How many separate bursts the bulk marks came in."""
    bulk = sorted(p.first_at for p in plays if p.how == "bulk")
    return sum(1 for a, b in zip([None] + bulk, bulk) if a is None or b - a >= BULK_SECONDS)


def _heatmap(dated: list, with_films: bool) -> dict:
    """Films started per weekday (Monday first) and local hour, from each play's estimated start. Plays marked as
    played have no playback time, so they aren't in it (left_out)."""
    cells = [[0] * 24 for _ in range(7)]
    cell_films = defaultdict(list)
    for p in dated:
        if p.start is None:
            continue
        s = local_datetime(p.start)
        cells[s.weekday()][s.hour] += 1
        if with_films:
            cell_films[f"{s.weekday()},{s.hour}"].append(dict(_film(p), at=_iso(p.at), start=_iso(p.start)))
    total = sum(map(sum, cells))
    peak = max(((d, hr) for d in range(7) for hr in range(24)), key=lambda t: (cells[t[0]][t[1]], -t[0], -t[1]))
    out = {"cells": cells, "plays": total, "left_out": len(dated) - total,
           "by_day": [sum(r) for r in cells], "by_hour": [sum(cells[d][hr] for d in range(7)) for hr in range(24)],
           "peak": {"day": DAY_NAMES[peak[0]], "weekday": peak[0], "hour": peak[1],
                    "plays": cells[peak[0]][peak[1]]} if total else None}
    if with_films:
        out["films"] = dict(cell_films)
    return out


def _months(catalog: Catalog, h: History, dated: list) -> list:
    by_month = defaultdict(list)
    for p in dated:
        by_month[p.when.strftime("%Y-%m")].append(p)
    first, months = h.start, []
    m = date(first.year, first.month, 1)
    while m <= h.as_of:
        k = m.strftime("%Y-%m")
        ps = by_month.get(k, [])
        months.append({"month": k, "label": f"{MONTH_NAMES[m.month - 1][:3]} {m.year}", "plays": len(ps),
                       "marked": sum(p.how == "marked" for p in ps),
                       "films": len({p.ident for p in ps}),
                       "hours": _hours(h.measured[k]) if k in h.measured else None,
                       "runtime_hours": round(sum(_runtime_min(catalog, p) for p in ps) / 60, 1),
                       "partial": (m.year, m.month) in ((first.year, first.month), (h.as_of.year, h.as_of.month))})
        m = _next_month(m)
    return months


def _streaks(h: History, dated: list, week_start: int = 0) -> dict:
    """The longest streak and the busiest day, week and month. week_start: the weeks' first day as date.weekday()
    counts it (0: Monday, as ISO weeks; 6: Sunday)."""
    per_day = Counter(p.day for p in dated)
    per_week = Counter(p.day - timedelta(days=(p.day.weekday() - week_start) % 7) for p in dated)
    per_month = Counter(p.when.strftime("%Y-%m") for p in dated)
    bd, bw, bm = _busiest(per_day), _busiest(per_week), _busiest(per_month)
    year, month = map(int, bm[0].split("-"))
    return {"longest": _streak(dated),
            "busiest_day": {"date": bd[0].isoformat(), "plays": bd[1],
                            "films": [dict(_film(p), at=_iso(p.at), how=p.how) for p in dated if p.day == bd[0]]},
            "busiest_week": {"from": bw[0].isoformat(), "to": (bw[0] + timedelta(days=6)).isoformat(),
                             "plays": bw[1]},
            "busiest_month": {"month": bm[0], "label": f"{MONTH_NAMES[month - 1]} {year}", "plays": bm[1]}}


def _most_played(catalog: Catalog, h: History) -> list:
    """Films played twice or more: plays on record plus the plays Plex counted without a date - those marked in
    bulk, and whatever Plex's own count has beyond the film's logs (h.extra: plays from before the history, per
    edition). Plex's count includes repeat logs of one play, so it's compared with every log, not with the plays
    they make."""
    films = catalog.films
    by_film = defaultdict(list)
    for p in h.plays:
        by_film[p.ident].append(p)
    rows = []
    seen = set()
    for ident, ps in by_film.items():
        seen.add(ident)
        f = films.get(ps[0].key) if ps[0].key else None
        dated = [p for p in ps if p.dated]
        bulk = len(ps) - len(dated)
        extra = h.extra.get(ident, 0) if f else 0
        total = len(dated) + bulk + extra
        if total < 2:
            continue
        rows.append(dict(_film(ps[0]), plays=total, dated=len(dated), undated=bulk + extra,
                         plex_count=f.owner_plays if f else None, dates=[p.day.isoformat() for p in dated],
                         last=dated[-1].day.isoformat() if dated else None,
                         rating=f.owner_rating if f else None))
    for f in films.values():                                  # counted by Plex, never logged
        extra = h.extra.get(f.key, 0)
        if f.key not in seen and extra >= 2:
            rows.append({"key": f.key, "title": f.title, "year": f.year, "in_library": True, "plays": extra,
                         "dated": 0, "undated": extra, "plex_count": f.owner_plays, "dates": [],
                         "last": None, "rating": f.owner_rating})
    rows.sort(key=lambda r: (-r["plays"], -r["dated"], _desc(r["last"]), fold(r["title"]), r["year"] or 0))
    return rows


def _desc(iso: str | None) -> tuple:
    """Sort key putting later dates first and no date last."""
    if not iso:
        return (1, 0)
    return (0, -date.fromisoformat(iso[:10]).toordinal())


def _stopped(catalog: Catalog, h: History) -> list:
    films = catalog.films
    plays = Counter(p.key for p in h.plays if p.key)
    rows = []
    for key, (offset, stopped, copy_ms) in h.resume.items():
        f = films[key]
        total_ms = copy_ms or (f.runtime_min * 60000 if f.runtime_min else None)
        if not _passes_resume(offset, total_ms):
            continue
        rows.append({"key": key, "title": f.title, "year": f.year, "in_library": True, "at_sec": offset // 1000,
                     "runtime_sec": total_ms // 1000 if total_ms else None,
                     "share": round(offset / total_ms, 3) if total_ms else None, "stopped": _iso(stopped),
                     "plays_before": plays.get(key, 0), "undated_before": h.extra.get(key, 0),
                     "plex_count": f.owner_plays, "rating": f.owner_rating})
    rows.sort(key=lambda r: fold(r["title"]))
    rows.sort(key=lambda r: r["stopped"] or "", reverse=True)          # the latest first; no date last
    return rows


def _passes_resume(offset_ms: int, runtime_ms: int | None) -> bool:
    """A real 'stopped partway': more than a peek in, and not in the last few minutes."""
    if offset_ms < RESUME_MIN_SEC * 1000:
        return False
    return not (runtime_ms and runtime_ms - offset_ms < RESUME_MIN_LEFT_SEC * 1000)


def _forgotten(catalog: Catalog, h: History, min_rating: float, days: int):
    """Films you rated min_rating or more with no dated play since as_of - days (or none at all)."""
    since = h.as_of - timedelta(days=days)
    last_dated, bulk = {}, {}
    for p in h.plays:
        if p.key:
            if p.dated:
                last_dated[p.key] = p.day
            else:
                bulk[p.key] = p.day
    rows = []
    for f in catalog.films.values():
        if f.owner_rating is None or f.owner_rating < min_rating:
            continue
        last = last_dated.get(f.key)
        if last is None or last < since:
            # counted by Plex but never logged: when it was marked as played (not when it was watched)
            unlogged = last is None and f.key not in bulk and h.extra.get(f.key)
            rows.append({"key": f.key, "title": f.title, "year": f.year, "in_library": True, "rating": f.owner_rating,
                         "last_played": last.isoformat() if last else None,
                         "bulk_marked": bulk[f.key].isoformat() if f.key in bulk else None,
                         "plex_marked": _iso(f.last_played) if unlogged else None,
                         "plex_count": f.owner_plays})
    rows.sort(key=lambda r: (-r["rating"], r["last_played"] is not None, r["last_played"] or "", fold(r["title"]),
                             r["year"] or 0))
    return rows, {"min_rating": float(min_rating), "days": days, "since": since.isoformat()}


# ---------------------------------------------------------------------------------------------------------
# A year in review
# ---------------------------------------------------------------------------------------------------------
def _window(h: History, y: int) -> tuple[date, date] | None:
    """The part of year y that Plex's history covers, or None."""
    if h.start is None:
        return None
    lo, hi = max(date(y, 1, 1), h.start), min(date(y, 12, 31), h.as_of)
    return (lo, hi) if lo <= hi else None


def _md(d: date) -> tuple[int, int]:
    """(month, day), with Feb 29 as Feb 28 - so a window lines up in any two years."""
    return (d.month, min(d.day, 28) if d.month == 2 else d.day)


def year_in_review(catalog: Catalog, h: History, y: int, top_n: int = 5) -> dict:
    films = catalog.films
    dated = h.dated
    window = _window(h, y)
    yp = [p for p in dated if p.when.year == y]
    # new or rewatched: a rewatch has an earlier play on record (one marked in bulk counts: it says you'd seen it),
    # or Plex counts more plays of the film than it ever logged (plays from before the history)
    earlier, rows = set(), []
    new = rewatched = 0
    for p in h.plays:
        if p.dated and p.when.year == y:
            f = films.get(p.key) if p.key else None
            before = p.ident in earlier or bool(f and h.extra.get(p.ident, 0) > 0)
            rewatched += before
            new += not before
            rows.append(dict(_film(p), at=_iso(p.at), how=p.how, rewatch=before, device=p.device,
                             rating=f.owner_rating if f else None))
        earlier.add(p.ident)
    keys = list(dict.fromkeys(p.key for p in yp if p.key))
    genres, decades, countries, actors, directors = Counter(), Counter(), Counter(), Counter(), Counter()
    for n, k in enumerate(keys):
        if not n % CHECK_EVERY:
            jobs.check()
        f = films[k]
        genres.update(set(f.genres))
        countries.update(set(f.countries))
        if f.year:
            decades[f.year // 10 * 10] += 1
        actors.update({c.person for c in f.cast if 0 < c.order <= LEAD_BILLING})
        directors.update({c.person for c in f.directors})

    def top(counter, name=str):
        return sorted(counter.items(), key=lambda kv: (-kv[1], fold(name(kv[0])), str(kv[0])))[:top_n]

    def person(pid):
        p = catalog.people.get(pid)
        return p.name if p else str(pid)
    per_month = Counter(p.when.month for p in yp)
    measured_months = {m: v for m, v in h.measured.items() if m.startswith(f"{y}-")}
    months = []
    for mo in range(1, 13):
        first_day, last_day = date(y, mo, 1), _next_month(date(y, mo, 1)) - timedelta(days=1)
        inside = window is not None and first_day <= window[1] and last_day >= window[0]
        partial = inside and (first_day < window[0] or last_day > window[1])
        k = f"{y}-{mo:02d}"
        months.append({"month": mo, "key": k, "label": MONTH_NAMES[mo - 1][:3], "name": MONTH_NAMES[mo - 1],
                       "plays": per_month.get(mo, 0),
                       "marked": sum(p.how == "marked" and p.when.month == mo for p in yp),
                       "hours": _hours(measured_months[k]) if k in measured_months else None,
                       "in_history": inside, "partial": partial})
    bm = _busiest(Counter({mo: n for mo, n in per_month.items()}))
    rated = [films[k].owner_rating for k in keys if films[k].owner_rating is not None]
    measured = sum(measured_months.values()) if measured_months else None
    out = {"year": y, "from": window[0].isoformat() if window else None,
           "to": window[1].isoformat() if window else None,
           "in_history": window is not None,
           "partial": window is not None and (window[0] != date(y, 1, 1) or window[1] != date(y, 12, 31)),
           "plays": len(yp), "films": len({p.ident for p in yp}), "new": new, "rewatched": rewatched,
           "marked": sum(p.how == "marked" for p in yp),
           "bulk_left_out": sum(p.how == "bulk" and p.when.year == y for p in h.plays),
           "gone_plays": sum(p.key is None for p in yp),
           "hours": {"measured": round(measured / 3600) if measured is not None else None,
                     "runtime": round(sum(_runtime_min(catalog, p) for p in yp) / 60)},
           "months": months,
           "days": {d.isoformat(): n for d, n in sorted(Counter(p.day for p in yp).items())},
           "top": {"genres": [{"label": k, "films": n} for k, n in top(genres)],
                   "decades": [{"decade": k, "label": f"{k}s", "films": n}
                               for k, n in top(decades, lambda d: f"{d:05d}")],
                   "countries": [{"label": k, "films": n} for k, n in top(countries)],
                   "actors": [{"id": k, "name": person(k), "films": n} for k, n in top(actors, person)],
                   "directors": [{"id": k, "name": person(k), "films": n} for k, n in top(directors, person)]},
           "top_from": len(keys),
           "first": dict(_film(yp[0]), at=_iso(yp[0].at), how=yp[0].how) if yp else None,
           "last": dict(_film(yp[-1]), at=_iso(yp[-1].at), how=yp[-1].how) if yp else None,
           "busiest_month": ({"month": bm[0], "key": f"{y}-{bm[0]:02d}", "label": MONTH_NAMES[bm[0] - 1],
                              "plays": bm[1]} if bm else None),
           "longest_streak": _streak(yp),
           "rating": {"average": _average(rated), "rated": len(rated), "films": len(keys)},
           "heatmap": _heatmap(yp, with_films=False),
           "vs_previous": _vs_previous(catalog, h, y, window, yp),
           "plays_list": rows}
    return out


def _vs_previous(catalog: Catalog, h: History, y: int, window, yp: list) -> dict | None:
    """Year y against the same stretch of the year before: the days of the year that both have in the history."""
    prev_window = _window(h, y - 1)
    if window is None or prev_window is None or not yp:
        return None
    prev = [p for p in h.dated if p.when.year == y - 1]
    if not prev:
        return None
    lo_md, hi_md = max(_md(window[0]), _md(prev_window[0])), min(_md(window[1]), _md(prev_window[1]))
    if lo_md > hi_md:
        return None
    films = catalog.films

    def stretch(plays, year):
        lo, hi = date(year, *lo_md), date(year, *hi_md)
        if hi_md == (2, 28) and year % 4 == 0 and (year % 100 or year % 400 == 0):
            hi = date(year, 2, 29)                       # (up to the end of February in a leap year)
        return [p for p in plays if lo <= p.day <= hi]
    a, b = stretch(yp, y), stretch(prev, y - 1)

    def rated(ps):                                      # your ratings (as they are now) of the films played
        return [films[k].owner_rating for k in {p.key for p in ps if p.key} if films[k].owner_rating is not None]
    ra, rb = rated(a), rated(b)
    return {"year": y - 1, "from": f"{lo_md[0]:02d}-{lo_md[1]:02d}", "to": f"{hi_md[0]:02d}-{hi_md[1]:02d}",
            "plays": [len(a), len(b)], "films": [len({p.ident for p in a}), len({p.ident for p in b})],
            "rating": [_average(ra), _average(rb)], "rated": [len(ra), len(rb)]}


# ---------------------------------------------------------------------------------------------------------
# One film, for the Film page
# ---------------------------------------------------------------------------------------------------------
def film_history(catalog: Catalog, film_key) -> dict:
    """Your plays of one film: every play on record (bulk marks too), Plex's count, the resume point if you
    stopped partway, and your rating. Never raises for a bad key or an unreadable database (ok: false); only
    jobs.Cancelled gets through."""
    film = catalog.films.get(str(film_key)) if film_key not in (None, "") else None
    if film is None:
        return {"ok": False, "error": f"no film with the key '{film_key}'"}
    try:
        h = history(catalog)
    except (PlexDBError, sqlite3.DatabaseError, OSError) as exc:
        return {"ok": False, "key": film.key, "error": f"Couldn't read Plex's play history: {exc}"}
    except jobs.Cancelled:
        raise
    except Exception as exc:                  # a bug shouldn't take the Film page down
        return {"ok": False, "key": film.key, "error": f"couldn't read the play history ({type(exc).__name__}: {exc})"}
    ps = [p for p in h.plays if p.key == film.key]
    dated = [p for p in ps if p.dated]
    resume = None
    if film.key in h.resume:
        offset, stopped, copy_ms = h.resume[film.key]
        total = copy_ms or (film.runtime_min * 60000 if film.runtime_min else None)
        if _passes_resume(offset, total):
            s = offset // 1000
            resume = {"at_sec": s, "at": f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}",
                      "runtime_sec": total // 1000 if total else None,
                      "share": round(offset / total, 3) if total else None, "stopped": _iso(stopped)}
    return {"ok": True, "key": film.key, "title": film.title, "year": film.year,
            "plays": [{"at": _iso(p.at), "how": p.how, "device": p.device, "logs": p.logs} for p in ps],
            "plays_on_record": len(dated), "plex_count": film.owner_plays,
            # the plays Plex counts but never logged (no date), its editions counted once - Plex's count adds them up
            "undated_plays": h.extra.get(film.key, 0),
            "first_played": _iso(dated[0].at) if dated else None,
            "last_played": _iso(dated[-1].at) if dated else None,
            "resume": resume, "rating": film.owner_rating,
            "history_from": h.start.isoformat() if h.start else None, "as_of": h.as_of.isoformat()}
