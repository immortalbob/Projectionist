"""The movies in a Plex database as plain Python objects, for answering questions about the collection.

load() reads what the query tools need in a few seconds, using the same rules as the spreadsheet
(the same director credits, ratings, IDs and credits-scene verdicts). Every copy and edition of a
film becomes one Film, so a film owned three times is still one film - but each copy keeps its own
credits-scene result, since cuts differ.

Ratings and plays are the server owner's. The other accounts' plays are kept too (Film.played_by), only so that
"films you haven't seen" can leave out the films played on accounts you share - the setting "Also count as seen"
(seen_accounts, below) and the requests' also_seen_by. Nothing else reads them.
"""

from __future__ import annotations

import difflib
import re
import sqlite3
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field

from . import credits as credit_scenes
from . import jobs, prefs
from .extract import (
    TAG_COLLECTION, TAG_COUNTRY, TAG_DIRECTOR, TAG_GENRE, TAG_GUID, TAG_LABEL, TAG_MARKER, TAG_RATING, TAG_ROLE,
    PlexDatabase, _Extractor, _read_error, clean, credits_info, earlier_edition_ratings, external_ids,
    headline_ratings, main_directors, parse_timestamp, plex_movie_id, records_final_flags, resolution_label, to_float,
    to_int, unique)

TAG_TYPES = (TAG_GENRE, TAG_COLLECTION, TAG_DIRECTOR, TAG_ROLE, TAG_COUNTRY, TAG_LABEL, TAG_MARKER, TAG_GUID,
             TAG_RATING)
_RESOLUTION_RANK = {"8K": 6, "4K": 5, "1080p": 4, "720p": 3, "576p": 2, "480p": 1, "SD": 0}
_VERDICT_RANK = {"Yes": 3, "Maybe": 2, "None found": 1, "": 0}
# In a background job, SQLite asks whether the job has been called off every this many steps of a query (every
# 10-30 ms while the collection is read).
CANCEL_STEPS = 50_000


@dataclass
class Credit:
    person: str                # Plex's person ID (the same in every film); 'name:<name>' if the database has none
    name: str
    role: str = ""             # the character (cast) or the job (crew)
    order: int = 0             # billing position, from 1


@dataclass
class CopyCredits:
    """One copy's credits-scene result: editions and cuts can differ."""
    plex_id: int
    edition: str
    info: credit_scenes.CreditsInfo


@dataclass
class Film:
    key: str                   # one per film: Plex's movie ID, else 'imdb:tt...', else 'plex:<id>'
    title: str
    year: int | None
    plex_ids: list[int] = field(default_factory=list)
    titles: list[str] = field(default_factory=list)      # every copy's title and original title, for searching
    libraries: list[str] = field(default_factory=list)
    editions: list[str] = field(default_factory=list)
    genres: list[str] = field(default_factory=list)
    countries: list[str] = field(default_factory=list)
    collections: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    studio: str = ""
    directors: list[Credit] = field(default_factory=list)
    cast: list[Credit] = field(default_factory=list)
    summary: str = ""
    tagline: str = ""
    content_rating: str = ""
    runtime_min: int | None = None
    imdb_id: str = ""
    imdb_rating: float | None = None
    tmdb_rating: float | None = None
    rt_critic: int | None = None
    rt_audience: int | None = None
    owner_rating: float | None = None
    # The rating was left on an earlier edition of the film (no copy has one of its own): Plex shows it as unrated
    owner_rating_from_earlier_edition: bool = False
    owner_plays: int = 0
    last_played: int | None = None        # Unix time
    added_at: int | None = None           # Unix time
    resolution: str = ""
    credits_copies: list[CopyCredits] = field(default_factory=list)
    # The other accounts' plays: {account id: plays} - never the owner's (owner_plays), never an account with none
    played_by: dict[int, int] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return f"{self.title} ({self.year})" if self.year else self.title

    @property
    def watched(self) -> bool:
        """Played or rated by the server owner."""
        return self.owner_plays > 0 or self.owner_rating is not None

    def seen(self, also_seen_by=()) -> bool:
        """Watched by the owner - or played on one of these other accounts (ids: the setting 'Also count as seen',
        a request's also_seen_by)."""
        return self.watched or any(self.played_by.get(a) for a in also_seen_by)

    @property
    def credits(self) -> credit_scenes.CreditsInfo | None:
        """The most telling copy's credits result (a copy with a scene beats one without)."""
        if not self.credits_copies:
            return None
        return max(self.credits_copies, key=lambda c: _VERDICT_RANK.get(c.info.verdict, 0)).info

    def brief(self) -> dict:
        return {"title": self.title, "year": self.year, "plex_ids": self.plex_ids, "libraries": self.libraries}

    def to_dict(self) -> dict:
        out = self.brief()
        out.update(
            editions=self.editions, genres=self.genres, countries=self.countries, studio=self.studio,
            directors=[c.name for c in self.directors], cast=[c.name for c in self.cast[:10]],
            runtime_min=self.runtime_min, content_rating=self.content_rating, imdb_id=self.imdb_id,
            imdb_rating=self.imdb_rating, rt_critic=self.rt_critic, rt_audience=self.rt_audience,
            your_rating=self.owner_rating, your_rating_from_earlier_edition=self.owner_rating_from_earlier_edition,
            your_plays=self.owner_plays, resolution=self.resolution,
            summary=self.summary)
        return out


@dataclass
class Person:
    id: str
    name: str
    photo: str = ""
    acted: dict[str, Credit] = field(default_factory=dict)     # film key -> their credit in it
    directed: set[str] = field(default_factory=set)            # film keys

    @property
    def film_count(self) -> int:
        return len(set(self.acted) | self.directed)


class Catalog:
    def __init__(self, films: dict[str, Film], people: dict[str, Person], owner: str, source: str,
                 libraries: list[str], accounts: dict[int, str] | None = None, owner_id: int | None = None):
        self.films = films
        self.people = people
        self.owner = owner
        self.source = source
        self.libraries = libraries
        self.accounts = dict(accounts or {})  # every account on the server: {id: name} (the owner's too)
        self.owner_id = owner_id
        self.cache: dict = {}             # for the query tools: fitted models, graphs...
        self._people_index = None
        self._film_index = None

    # -- the other accounts on the server -------------------------------------------------------------------
    def account_name(self, account_id) -> str:
        return self.accounts.get(account_id) or f"User {account_id}"

    def account_plays(self) -> dict[int, int]:
        """{account id: how many films it has played} for every account but the owner's that has played one -
        most films first."""
        counts: dict[int, int] = defaultdict(int)
        for film in self.films.values():
            for account in film.played_by:
                counts[account] += 1
        return dict(sorted(counts.items(), key=lambda kv: (-kv[1], self.account_name(kv[0]).casefold())))

    # -- finding people and films by name ---------------------------------------------------------------
    def find_person(self, query: str) -> tuple[Person | None, list[Person], str]:
        """The person best matching a typed name, the other candidates, and how it matched
        ('exact', 'partial' - every typed word is in the name - or 'guess' - a near spelling)."""
        if self._people_index is None:
            index = defaultdict(list)
            for p in self.people.values():
                for key in _keys(p.name):
                    index[key].append(p)
            self._people_index = index
        return _best(query, self._people_index, lambda p: p.film_count, lambda p: p.name)

    def find_film(self, query: str) -> tuple[Film | None, list[Film], str]:
        """The film best matching a typed title - 'Alien' or 'Alien (1979)' - the other candidates, and how
        it matched (as find_person). Every copy's title counts, and so does the original-language title."""
        if self._film_index is None:
            index = defaultdict(list)
            for f in self.films.values():
                for key in {k for title in (f.titles or [f.title]) for k in _keys(title, drop_article=True)}:
                    index[key].append(f)
            self._film_index = index
        year = None
        m = re.match(r"^(.*?)\s*\((\d{4})\)\s*$", query or "")
        if m:
            query, year = m.group(1), int(m.group(2))
        # Several films share a title (The Mummy 1932/1959/1999): the one you've seen first, then the best rated.
        best, others, how = _best(query, self._film_index, lambda f: (f.watched, f.imdb_rating or 0),
                                  lambda f: f.title, drop_article=True)
        if year is not None:
            everything = ([best] if best else []) + others
            pool = [f for f in everything if f.year == year]
            if not pool:
                return None, everything, ""
            return pool[0], pool[1:], how
        return best, others, how


_ASCII = str.maketrans({"ø": "o", "æ": "ae", "œ": "oe", "ß": "ss", "đ": "d", "ð": "d", "þ": "th", "ł": "l",
                        "ı": "i", "ħ": "h"})


def fold(text: str, drop_article: bool = False) -> str:
    """Lower-case, accents and punctuation removed, other letters kept: 'Shintarō Katsu' -> 'shintaro katsu',
    'Søren' -> 'søren', 'Николь' -> 'николь'."""
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(ch for ch in t if not unicodedata.combining(ch)).casefold()
    t = " ".join(re.sub(r"[\W_]+", " ", t).split())
    if drop_article:
        t = re.sub(r"^(the|a|an) ", "", t)
    return t


def ascii_fold(text: str, drop_article: bool = False) -> str:
    """fold(), then spelled with English letters where there's an obvious way: 'Søren' -> 'soren'."""
    t = fold(text, drop_article).translate(_ASCII)
    return " ".join(re.sub(r"[^a-z0-9]+", " ", t).split())


def _keys(text: str, drop_article: bool = False) -> set[str]:
    return {k for k in (fold(text, drop_article), ascii_fold(text, drop_article)) if k}


def _best(query, index, rank, name_of, drop_article=False):
    """(best match, other candidates, how it matched: 'exact', 'partial' or 'guess')."""
    queries = [q for q in dict.fromkeys((fold(query, drop_article), ascii_fold(query, drop_article))) if q]
    if not queries:
        return None, [], ""

    def distinct(items):
        seen, out = set(), []
        for x in items:
            if id(x) not in seen:
                seen.add(id(x))
                out.append(x)
        return out

    # The same words, in any order ('Chan Shen' is also filed as 'Shen Chan').
    exact = {id(x) for q in queries for x in index.get(q, [])}
    keys = {tuple(sorted(q.split())) for q in queries}
    hits = distinct(x for name, xs in index.items() if tuple(sorted(name.split())) in keys for x in xs)
    if hits:
        hits.sort(key=rank, reverse=True)
        hits.sort(key=lambda x: id(x) not in exact)      # the exact spelling first, then by rank (stable)
        return hits[0], hits[1:], "exact"
    # Every typed word is in the name: 'de niro' -> 'Robert De Niro'. Shortest (closest) names first.
    patterns = [[re.compile(rf"(?<!\w){re.escape(w)}(?!\w)") for w in q.split()] for q in queries]
    hits = distinct(x for name, xs in index.items() if any(all(p.search(name) for p in ps) for ps in patterns)
                    for x in xs)
    if hits:
        hits.sort(key=rank, reverse=True)
        hits.sort(key=lambda x: len(fold(name_of(x))))   # stable: rank breaks ties
        return hits[0], hits[1:], "partial"
    # A near spelling: the closest spellings first and, under each one, the best known (a typo of a name two
    # people share lands on the one with more films).
    close = difflib.get_close_matches(queries[0], list(index), n=5, cutoff=0.75)
    hits = distinct(x for name in close for x in sorted(index[name], key=rank, reverse=True))
    return (hits[0], hits[1:], "guess") if hits else (None, [], "")


def load(db_path, library_ids=None) -> Catalog:
    """Read every movie in the chosen libraries (default: all movie libraries) into a Catalog.

    In a background job (see jobs.py) that is called off, it stops part-way - in the middle of a query too -
    with jobs.Cancelled."""
    with PlexDatabase(db_path) as db:
        job = jobs.current()
        if job is not None and db.con is not None:
            db.con.set_progress_handler(lambda: job.cancelled, CANCEL_STEPS)
        try:
            return _load(db, library_ids)
        except sqlite3.DatabaseError as exc:
            jobs.check()                        # (an interrupted query: the job was called off)
            raise _read_error(db.path, exc) from exc


def _load(db, library_ids) -> Catalog:
    ex = _Extractor(db, library_ids, [], None, None)
    movies = ex.load_movies()
    tags = ex.load_tags(TAG_TYPES)
    media = ex.load_media()
    ex.accounts, ex.owner_id = ex.load_accounts()
    settings = ex.load_settings()
    final_flags = records_final_flags(db)

    films: dict[str, Film] = {}
    people: dict[str, Person] = {}
    ratings = defaultdict(dict)                 # film -> {guid: rating}: Plex keeps one settings row per GUID
    counted = defaultdict(set)                  # film -> GUIDs whose plays are already counted
    others_counted = defaultdict(set)           # ...the same, for the other accounts' plays
    for m in movies:
        jobs.check()
        mid = m["id"]
        t = tags.get(mid, {})
        guid = clean(m["guid"])
        ids, _ = external_ids(guid, t)
        key = plex_movie_id(guid) or (f"imdb:{ids['imdb']}" if ids.get("imdb") else f"plex:{mid}")
        film = films.get(key)
        if film is None:
            film = films[key] = Film(key=key, title=clean(m["title"]), year=to_int(m["year"]))
            _describe(film, m, t, ids)
        film.plex_ids.append(mid)
        film.titles = unique(film.titles + [clean(m["title"]), clean(m["original_title"])])
        film.libraries = unique(film.libraries + [ex.lib_names.get(m["library_section_id"], "")])
        edition = clean(m["edition_title"])
        if edition:
            film.editions = unique(film.editions + [edition])
        film.collections = unique(film.collections + [x.tag for x in t.get(TAG_COLLECTION, [])])
        film.labels = unique(film.labels + [x.tag for x in t.get(TAG_LABEL, [])])
        added = parse_timestamp(m["added_at"])
        if added and (film.added_at is None or added < film.added_at):
            film.added_at = added
        versions = media.get(mid, [])
        for v in versions:
            res = resolution_label(v["width"], v["height"])
            if _RESOLUTION_RANK.get(res, -1) > _RESOLUTION_RANK.get(film.resolution, -1):
                film.resolution = res
        if film.runtime_min is None:
            dur = to_int(m["duration"]) or (to_int(versions[0]["duration"]) if versions else None)
            film.runtime_min = round(dur / 60000) if dur else None
        info = credits_info(t, versions, m["year"], final_flags)
        if info.credits_start is not None:
            film.credits_copies.append(CopyCredits(mid, edition, info))
        owner = next((s for s in settings.get(m["guid"], []) if s["account_id"] == ex.owner_id), None) \
            if m["guid"] else None
        if owner and m["guid"] not in counted[key]:
            counted[key].add(m["guid"])
            film.owner_plays += to_int(owner["view_count"]) or 0
            if to_float(owner["rating"]) is not None:
                ratings[key][m["guid"]] = to_float(owner["rating"])
            last = parse_timestamp(owner["last_viewed_at"])
            if last and (film.last_played is None or last > film.last_played):
                film.last_played = last
        if m["guid"] and m["guid"] not in others_counted[key]:
            others_counted[key].add(m["guid"])
            for s in settings.get(m["guid"], []):
                account, plays = s["account_id"], to_int(s["view_count"]) or 0
                if account and account != ex.owner_id and plays > 0:      # (account 0: no one)
                    film.played_by[account] = film.played_by.get(account, 0) + plays
        if not film.cast and not film.directors:
            _people(film, t, m)
        for c in film.cast:
            people.setdefault(c.person, Person(c.person, c.name)).acted.setdefault(key, c)
        for c in film.directors:
            people.setdefault(c.person, Person(c.person, c.name)).directed.add(key)
    for key, by_guid in ratings.items():
        values = list(by_guid.values())
        films[key].owner_rating = round(sum(values) / len(values), 1)
    # A rating given before Plex renamed or added an edition stays on the film's old GUID, so the copy you have now
    # reads as unrated (in Plex too). A film with no rating of its own takes it from there - as the spreadsheet does.
    for key, value in earlier_edition_ratings(db, ex.owner_id).items():
        if key in films and films[key].owner_rating is None:
            films[key].owner_rating = value
            films[key].owner_rating_from_earlier_edition = True
    photos = {}
    for t in tags.values():
        jobs.check()
        for x in t.get(TAG_ROLE, []) + t.get(TAG_DIRECTOR, []):
            if x.photo:
                photos.setdefault(x.key or f"name:{x.tag}", x.photo)
    for pid, p in people.items():
        p.photo = photos.get(pid, "")
    accounts = {i: ex.account_name(i) for i in ex.accounts if i}
    return Catalog(films, people, ex.account_name(ex.owner_id), db.path, [lib.name for lib in ex.libraries],
                   accounts=accounts, owner_id=ex.owner_id)


def _describe(film: Film, m, t, ids):
    film.genres = unique(x.tag for x in t.get(TAG_GENRE, [])) or unique((m["tags_genre"] or "").split("|"))
    film.countries = unique(x.tag for x in t.get(TAG_COUNTRY, [])) or unique((m["tags_country"] or "").split("|"))
    film.studio = clean(m["studio"])
    film.summary = clean(m["summary"])
    film.tagline = clean(m["tagline"])
    film.content_rating = clean(m["content_rating"])
    film.imdb_id = ids.get("imdb", "")
    scores = headline_ratings(t)
    film.imdb_rating, film.tmdb_rating = scores.get("imdb_rating"), scores.get("tmdb_rating")
    film.rt_critic, film.rt_audience = scores.get("rt_critic"), scores.get("rt_audience")


def _people(film: Film, t, m):
    seen = set()
    for i, x in enumerate(t.get(TAG_ROLE, []), 1):
        pid = x.key or f"name:{x.tag}"
        if x.tag and pid not in seen:
            seen.add(pid)
            film.cast.append(Credit(pid, x.tag, x.text, i))
    if not film.cast:   # legacy agents: names only
        film.cast = [Credit(f"name:{n}", n, "", i) for i, n in enumerate(unique((m["tags_star"] or "").split("|")), 1)]
    credits = t.get(TAG_DIRECTOR, [])
    seen = set()
    for x in main_directors(credits):
        pid = x.key or f"name:{x.tag}"
        if x.tag and pid not in seen:      # the same person as Director and Co-Director counts once
            seen.add(pid)
            film.directors.append(Credit(pid, x.tag, x.text, len(film.directors) + 1))
    if not credits:
        # Only when there are no directing credits at all (legacy agents): Plex's plain list of names. It can
        # include assistants, so it's never used to fill in for a film whose only credit is an assistant.
        film.directors = [Credit(f"name:{n}", n, "Director", i)
                          for i, n in enumerate(unique((m["tags_director"] or "").split("|")), 1)]


# ---------------------------------------------------------------------------------------------------------
# Whose plays count as yours: the setting, and a request's also_seen_by
# ---------------------------------------------------------------------------------------------------------
def account_ids(value) -> list[int]:
    """A list of account ids (numbers, or numbers written as text), sorted and each once - the setting
    seen_accounts and a request's also_seen_by. None is none; anything else is a ValueError."""
    if value is None:
        return []
    if isinstance(value, (str, bytes, dict)) or not hasattr(value, "__iter__"):
        raise ValueError(f"a list of account ids, not {value!r}")
    ids = set()
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, str)):
            raise ValueError(f"an account id is a whole number, not {item!r}")
        try:
            ids.add(int(item))
        except ValueError:
            raise ValueError(f"an account id is a whole number, not {item!r}") from None
    return sorted(ids)


def _accounts_words(ids) -> str:
    return f"{len(ids)} account{'s' if len(ids) != 1 else ''}" if ids else "None"


prefs.section("Your collection", order=25, hint="Whose plays count when the app works out what you've seen.")
prefs.define("seen_accounts", "Your collection", "Also count as seen: films played on these accounts",
             kind="accounts", default=[], parse=account_ids, format=_accounts_words,
             help="A film played on a ticked account counts as one you've seen - handy for a profile you watch "
                  "together on. Watch Next and the Film tab stop offering it as one you haven't seen. Your own "
                  "ratings and plays, the Viewing tab and the Overview stay yours alone.")
