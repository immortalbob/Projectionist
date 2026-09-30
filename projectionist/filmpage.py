"""Everything about one film, and one search box for the whole collection - the backbone behind the Film tab and
the main window's search (plain JSON-able answers; nothing here knows about windows).

    film_page(catalog, key)                  the film's details, its ratings beside IMDb / TMDb / Rotten Tomatoes,
                                             its credits scenes, cast and directors, similar films, and whatever
                                             the other backbones know about it (critics, your plays, its files,
                                             library-health issues)
    search(catalog, {"q": "tom hnaks"})      films, people, collections, genres, countries, studios, libraries
                                             (and critics, when projectionist.critics lists them) matching a name as
                                             it's typed - accents aside, typos forgiven, 'rocky 2' for Rocky II,
                                             'starwars' for Star Wars
    suggestions(catalog) / studio_films(catalog, studio)
                                             what the Film tab's start page and studio list show

A film is always found by its key (Film.key): two films can share a title and year ('Dracula (1931)' and
'Drácula (1931)'). An unknown key is an answer (ok: false), never an exception.

The parts that other backbones fill in are called only if they exist - critics.film_verdicts,
habits.film_history, doctor.film_files and doctor.film_issues, each as fn(catalog, film_key) - so this page works
before those are built and picks them up once they are.
"""

from __future__ import annotations

import bisect
import heapq
import importlib
import math
import re
import threading
import time
import unicodedata
from collections import Counter, defaultdict
from datetime import date

from . import costars, formats, jobs
from . import recommend as R
from .catalog import Catalog, Film, ascii_fold, fold
from .extract import local_datetime

PARTS = ("film", "ratings", "credits", "people", "similar", "critics", "history", "files", "issues")
CORE = ("film", "ratings", "credits", "people")                  # a fraction of a millisecond: fine on a UI thread
BACKGROUND = ("similar", "critics", "history", "files", "issues")
# The parts other backbones answer: part -> (module in this package, function(catalog, film_key)).
HOOKS = {"critics": ("critics", "film_verdicts"), "history": ("habits", "film_history"),
         "files": ("doctor", "film_files"), "issues": ("doctor", "film_issues")}
FRESH_FROM = 60                  # Rotten Tomatoes' own line: 60% or more of critics liking it is 'Fresh'
PLAYS_NOTE = "From Plex's play count - films marked as played count too."
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
EARLIER_EDITION = ("You rated an earlier edition of it: Plex shows this copy as unrated - rate it again in Plex to "
                   "carry the rating over.")
SIMILAR_NOTE = ("Likeness: shared people, studio, genres, collections, countries, decade and library, rarer ones "
                "counting for more (1 = the same) - it knows nothing of plot or tone. 'Not seen yet' leaves out "
                "anything you've played or rated in Plex (a classic you saw years ago elsewhere still counts as "
                "unseen).")

# Plex files some countries under their ISO names; these are the everyday ones (the same as the Overview tab's),
# and the other names a country is searched by.
SHORT_COUNTRY = {
    "United States of America": "United States",
    "United Kingdom of Great Britain and Northern Ireland": "United Kingdom",
    "Taiwan, Province of China": "Taiwan",
    "Korea, Republic of": "South Korea",
    "Republic of Korea": "South Korea",
    "Korea, Democratic People's Republic of": "North Korea",
    "Russian Federation": "Russia",
    "Iran, Islamic Republic of": "Iran",
    "Viet Nam": "Vietnam",
    "Czechia": "Czech Republic",
    "Hong Kong SAR China": "Hong Kong",
    "United Republic of Tanzania": "Tanzania",
    "Macao": "Macau",
}
COUNTRY_ALIASES = {
    "United States of America": ["United States", "USA", "US", "America"],
    "United Kingdom": ["UK", "Britain", "Great Britain", "England"],
    "United Kingdom of Great Britain and Northern Ireland": ["United Kingdom", "UK", "Britain", "England"],
    "Republic of Korea": ["South Korea", "Korea"],
    "Korea, Republic of": ["South Korea", "Korea"],
    "Czechia": ["Czech Republic"],
    "Czech Republic": ["Czechia"],
}


def country_name(name: str) -> str:
    """The everyday name of a country Plex files under its ISO name ('United States of America' -> 'United
    States')."""
    return SHORT_COUNTRY.get(name, name)


# ---------------------------------------------------------------------------------------------------------
# Finding a film
# ---------------------------------------------------------------------------------------------------------
def _label_key(text: str) -> str:
    return unicodedata.normalize("NFC", str(text or "")).casefold().strip()


def _labels(catalog: Catalog) -> dict[str, list[Film]]:
    """'title (year)' (and each other title with the year), exactly as spelled but for case -> films."""
    key = ("filmpage.labels",)
    if key not in catalog.cache:
        index = defaultdict(list)
        for f in catalog.films.values():
            for t in dict.fromkeys([f.title] + list(f.titles or [])):
                label = f"{t} ({f.year})" if f.year else t
                if f not in index[_label_key(label)]:
                    index[_label_key(label)].append(f)
        catalog.cache[key] = dict(index)
    return catalog.cache[key]


def find(catalog: Catalog, key=None, title=None) -> tuple[Film | None, str, list[Film]]:
    """The film asked for: by key exactly, else by its label exactly ('Drácula (1931)' is not 'Dracula (1931)'),
    else the collection's loose title match. -> (film or None, how: 'key' | 'exact' | 'partial' | 'guess' | '',
    other candidates)."""
    if key not in (None, ""):
        film = catalog.films.get(str(key))
        if film is not None:
            return film, "key", []
    if title:
        same = _labels(catalog).get(_label_key(title))
        if same:
            ranked = sorted(same, key=lambda f: (not f.watched, -(f.imdb_rating or 0)))
            return ranked[0], "exact", ranked[1:]
        film, others, how = catalog.find_film(str(title))
        return film, how if film is not None else "", others
    return None, "", []


# ---------------------------------------------------------------------------------------------------------
# The film page
# ---------------------------------------------------------------------------------------------------------
def film_page(catalog: Catalog, key, parts=None, similar: int = 8, also_seen_by=()) -> dict:
    """Everything about one film. parts: None or 'all' (every part), 'core' (film, ratings, credits, people - a
    fraction of a millisecond) or a list of part names ('film' always comes along). Each part is worked out on its
    own: one that fails is {ok: false, error} and the rest still come. An unknown key -> {ok: false, error}; an
    unknown part name raises ValueError. also_seen_by: other accounts whose plays count as seen (their ids) - the
    similar films' 'unseen' list leaves out what they've played."""
    wanted = _parts(parts)
    film = catalog.films.get(str(key)) if key not in (None, "") else None
    if film is None:
        return {"ok": False, "error": f"no film with the key '{key}'"}
    count = int(similar) if isinstance(similar, (int, float)) and not isinstance(similar, bool) else 8
    count = min(max(count, 1), 50)
    also = R.seen_by({"also_seen_by": also_seen_by})
    out = {"ok": True, "key": film.key, "label": film.label, "parts": list(wanted), "took_ms": {}}
    for name in wanted:
        started = time.perf_counter()
        try:
            out[name] = (_similar_part(catalog, film, count, also) if name == "similar" else
                         _PART[name](catalog, film, count))
        except jobs.Cancelled:
            raise
        except Exception as exc:                 # one part's trouble mustn't cost the others
            out[name] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        out["took_ms"][name] = round((time.perf_counter() - started) * 1000, 2)
    return out


def _parts(parts) -> tuple[str, ...]:
    if parts is None or parts == "all":
        return PARTS
    if parts == "core":
        return CORE
    if isinstance(parts, str):
        parts = [p for p in re.split(r"[,\s]+", parts) if p]
    if not isinstance(parts, (list, tuple, set)):
        raise ValueError("parts: 'all', 'core' or a list of part names")
    names = set()
    for p in parts:
        p = str(p).strip().lower()
        if p in ("all", "core"):
            names.update(PARTS if p == "all" else CORE)
        elif p in PARTS:
            names.add(p)
        else:
            raise ValueError(f"unknown part '{p}' - use {', '.join(PARTS)}, 'core' or 'all'")
    return tuple(p for p in PARTS if p == "film" or p in names)


def _local(ts, minutes: bool = False) -> str | None:
    """A moment (Unix seconds, UTC) as local time: '2026-03-03' or '2026-03-03T21:14'."""
    when = local_datetime(ts) if ts else None
    if when is None:
        return None
    return when.strftime("%Y-%m-%dT%H:%M") if minutes else when.date().isoformat()


def other_titles(film: Film) -> list[str]:
    """The film's other titles (original-language ones, other copies' titles), not counting spellings of its main
    title that differ only in case, accents or punctuation."""
    main = fold(film.title)
    seen, out = {main}, []
    for t in film.titles or []:
        k = fold(t)
        if t and k not in seen:
            seen.add(k)
            out.append(t)
    return out


def brief(film: Film) -> dict:
    return {"key": film.key, "title": film.title, "year": film.year, "label": film.label}


def _film_part(catalog: Catalog, film: Film, _count=None) -> dict:
    out = film.to_dict()
    out.update(
        key=film.key, label=film.label, titles=other_titles(film), copies=len(film.plex_ids) or 1,
        editions=list(film.editions), collections=list(film.collections), labels=list(film.labels),
        tagline=film.tagline, tmdb_rating=film.tmdb_rating, added=_local(film.added_at),
        last_played=_local(film.last_played, minutes=True), watched=film.watched,
        director_ids=[c.person for c in film.directors])
    return out


def copies_text(copies: int, editions) -> str:
    """'One copy', 'One copy (Criterion)', '2 copies: Theatrical and Director's Cut', '3 copies: Criterion, Arrow
    Video and one without an edition name'."""
    editions = [e for e in editions or [] if e]
    copies = max(int(copies or 1), 1)
    if copies == 1:
        return f"One copy ({editions[0]})" if editions else "One copy"
    unnamed = max(copies - len(editions), 0)
    parts = list(editions)
    if unnamed:
        parts.append("one without an edition name" if unnamed == 1 else f"{unnamed} without edition names")
    if not editions:
        return f"{copies} copies, none with an edition name"
    return f"{copies} copies: " + and_list(parts)


def and_list(items) -> str:
    items = [str(i) for i in items]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


# -- ratings ---------------------------------------------------------------------------------------------------
SOURCES = {"you": "Your rating", "imdb": "IMDb", "tmdb": "TMDb", "rt_critic": "Rotten Tomatoes critics",
           "rt_audience": "Rotten Tomatoes audience"}


def rating_stats(catalog: Catalog) -> tuple[float | None, int]:
    """(your average rating, how many films you've rated) - once per collection."""
    key = ("filmpage.rating_stats",)
    if key not in catalog.cache:
        rated = [f.owner_rating for f in catalog.films.values() if f.owner_rating is not None]
        catalog.cache[key] = (sum(rated) / len(rated) if rated else None, len(rated))
    return catalog.cache[key]


def fresh(percent) -> str | None:
    """Rotten Tomatoes' critics verdict by its 60% line (Plex's own ripe / rotten mark isn't kept)."""
    return None if percent is None else ("Fresh" if percent >= FRESH_FROM else "Rotten")


def _ratings_part(catalog: Catalog, film: Film, _count=None) -> dict:
    rows = []
    for source, value, scale in (("you", film.owner_rating, 10), ("imdb", film.imdb_rating, 10),
                                 ("tmdb", film.tmdb_rating, 10), ("rt_critic", film.rt_critic, 100),
                                 ("rt_audience", film.rt_audience, 100)):
        if value is None:
            continue
        row = {"source": source, "label": SOURCES[source], "value": value, "scale": scale,
               "out_of_10": round(value / 10, 2) if scale == 100 else value,
               "text": f"{value:g}%" if scale == 100 else f"{value:g} / 10"}
        if source == "rt_critic":
            row["verdict"] = fresh(value)
        if source == "you" and _earlier_edition(film):
            row["earlier_edition"] = True
        rows.append(row)
    average, rated = rating_stats(catalog)
    missing = [s for s in SOURCES if s != "you" and s not in {r["source"] for r in rows}]
    return {"rows": rows, "yours": film.owner_rating, "your_average": round(average, 2) if average else None,
            "rated_films": rated, "missing": missing, "note": ratings_note(film, missing),
            "yours_from_earlier_edition": _earlier_edition(film)}


def _earlier_edition(film: Film) -> bool:
    """Your rating was left on an earlier edition of the film (Plex shows the copy you have now as unrated)."""
    return film.owner_rating is not None and bool(getattr(film, "owner_rating_from_earlier_edition", False))


def ratings_note(film: Film, missing=()) -> str:
    """'You gave it 8 - 0.7 above IMDb's 7.3.' / 'You haven't rated it.' and which scores Plex hasn't got."""
    yours, imdb = film.owner_rating, film.imdb_rating
    if yours is None:
        text = "You haven't rated it."
    elif imdb is None:
        text = f"You gave it {yours:g}."
    else:
        diff = round(yours - imdb, 1)
        if abs(diff) < 0.05:
            text = f"You gave it {yours:g} - the same as IMDb's {imdb:g}."
        else:
            text = f"You gave it {yours:g} - {abs(diff):g} {'above' if diff > 0 else 'below'} IMDb's {imdb:g}."
    if _earlier_edition(film):
        text += " " + EARLIER_EDITION
    if missing:
        names = [SOURCES[m] for m in missing]
        if "rt_critic" in missing and "rt_audience" in missing:
            names = [n for n in names if not n.startswith("Rotten")] + ["Rotten Tomatoes"]
        text += f" Plex has no score from {and_list(names).replace(' and ', ' or ')} for it."
    return text


# -- people ------------------------------------------------------------------------------------------------------
def _people_part(catalog: Catalog, film: Film, _count=None) -> dict:
    labels = costars.namesake_labels(catalog)

    def row(c, director: bool) -> dict:
        p = catalog.people.get(c.person)
        out = {"id": c.person, "name": c.name, "order": c.order, "films_here": p.film_count if p else 1,
               "label": labels.get(c.person, c.name)}
        if director:
            out["mostly_directs"] = costars.mostly_directs(p) if p is not None else True
        else:
            out["role"] = c.role
        return out
    return {"directors": [row(c, True) for c in film.directors], "cast": [row(c, False) for c in film.cast],
            "cast_count": len(film.cast)}


# -- credits scenes ------------------------------------------------------------------------------------------------
def _credits_part(catalog: Catalog, film: Film, _count=None) -> dict:
    from .ask import _credits_dict
    return _credits_dict(film)


# -- similar films --------------------------------------------------------------------------------------------------
# What likeness can rest on: people, studio, genres, collections (a country, decade or library alone is too broad).
LIKENESS_KINDS = ("director|", "actor|", "studio|", "genre|", "collection|")
NOTHING_TO_COMPARE = ("Plex knows too little about this film (no cast, director, studio or genres it shares with "
                      "another film) to compare it with others.")


def feature_counts(catalog: Catalog) -> Counter:
    """How many films have each of the things likeness rests on (Watch Next's features: 'actor|<id>'...) - once per
    collection (a few hundredths of a second; warm() does it in the background)."""
    key = ("filmpage.feature_counts",)
    if key not in catalog.cache:
        counts = Counter()
        for n, f in enumerate(catalog.films.values()):
            if n % 500 == 0:
                jobs.check()
            counts.update(k for k in R.features(f) if k.startswith(LIKENESS_KINDS))
        catalog.cache[key] = counts
    return catalog.cache[key]


def comparable(catalog: Catalog, film: Film) -> bool:
    """Enough known about a film to find others like it: someone, a studio, a genre or a collection it shares with
    at least one other film - not just its decade and library (nor a studio no other film has)."""
    counts = feature_counts(catalog)
    return any(counts.get(k, 0) >= 2 for k in R.features(film) if k.startswith(LIKENESS_KINDS))


def _similar_part(catalog: Catalog, film: Film, count: int = 8, also_seen_by=()) -> dict:
    if not comparable(catalog, film):
        return {"ok": True, "source": "none", "unseen": [], "seen": [], "prediction": None,
                "note": NOTHING_TO_COMPARE}
    try:
        rec = R.recommender(catalog)
    except ValueError as exc:                      # too few ratings to fit Watch Next's model
        return _likeness_only(catalog, film, count, str(exc), also_seen_by)
    # (a likeness list: no limit per director - Ip Man 4 belongs under Ip Man, whoever else directed films)
    request = {"like_key": film.key, "count": count, "max_per_director": 0}
    if also_seen_by:
        request["also_seen_by"] = list(also_seen_by)
    answer = R.recommend(catalog, request)
    if not answer.get("ok"):
        return {"ok": False, "error": answer.get("error", "couldn't find similar films")}
    unseen = []
    for r in answer.get("results", []):
        other = catalog.films.get(r.get("key"))
        if other is not None:
            unseen.append(dict(brief(other), likeness=r.get("likeness"), predicted_rating=r.get("predicted_rating")))
    vector = rec.vector(R.features(film))
    close = sorted(((R._cosine(vector, v), f) for v, f in rec.rated_vectors if f.key != film.key),
                   key=lambda t: (-t[0], t[1].title))
    seen = [dict(brief(f), likeness=round(s, 2), your_rating=f.owner_rating) for s, f in close[:count] if s >= 0.1]
    prediction = None
    if film.owner_rating is None:
        predicted, expected, _kind, _lift = rec.predict(film)
        prediction = {"predicted_rating": round(predicted, 1), "expected_from_scores": round(expected, 1),
                      "your_average": round(rec.average, 2), "trained_on": len(rec.rated)}
    return {"ok": True, "source": "recommender", "unseen": unseen, "seen": seen, "prediction": prediction,
            "played_matches": answer.get("played_matches", 0), "note": SIMILAR_NOTE}


def _likeness_vectors(catalog: Catalog) -> dict[str, dict[str, float]]:
    """Every film's features (as Watch Next's model sees them, scores aside) weighted by rarity - for likeness
    when there are too few ratings for the model. Once per collection (a few hundredths of a second)."""
    key = ("filmpage.likeness_vectors",)
    if key not in catalog.cache:
        feats, df = {}, defaultdict(int)
        for n, f in enumerate(catalog.films.values()):
            if n % 500 == 0:
                jobs.check()
            x = {k: v for k, v in R.features(f).items() if not R._is_score(k)}
            feats[f.key] = x
            for k in x:
                df[k] += 1
        total = len(catalog.films) or 1
        idf = {k: math.log(total / d) for k, d in df.items()}
        catalog.cache[key] = {fk: {k: v * idf[k] for k, v in x.items()} for fk, x in feats.items()}
    return catalog.cache[key]


def _likeness_only(catalog: Catalog, film: Film, count: int, why: str, also_seen_by=()) -> dict:
    vectors = _likeness_vectors(catalog)
    mine = vectors.get(film.key) or {}
    scored = []
    for n, (k, v) in enumerate(vectors.items()):
        if n % 500 == 0:
            jobs.check()
        if k != film.key:
            s = R._cosine(mine, v)
            if s >= 0.1:
                scored.append((s, catalog.films[k]))
    scored.sort(key=lambda t: (-t[0], t[1].title))
    unseen = [dict(brief(f), likeness=round(s, 2), predicted_rating=None) for s, f in scored
              if not f.seen(also_seen_by)][:count]
    seen = [dict(brief(f), likeness=round(s, 2), your_rating=f.owner_rating)
            for s, f in scored if f.owner_rating is not None][:count]
    return {"ok": True, "source": "likeness", "unseen": unseen, "seen": seen, "prediction": None,
            "note": f"Likeness only - Watch Next can't predict your ratings yet ({why}). " + SIMILAR_NOTE}


# -- parts other backbones answer ----------------------------------------------------------------------------------
def hook(module: str, name: str):
    """The function other_backbone.name if that module and function exist, else None. A module that exists but
    fails to import raises (that's a bug to report, not a feature still being built)."""
    full = f"{__package__}.{module}"
    try:
        mod = importlib.import_module(full)
    except ModuleNotFoundError as exc:
        if exc.name == full:
            return None
        raise
    fn = getattr(mod, name, None)
    return fn if callable(fn) else None


def _hook_part(module: str, name: str, catalog: Catalog, key: str) -> dict:
    try:
        fn = hook(module, name)
    except jobs.Cancelled:
        raise
    except Exception as exc:
        return {"available": True, "ok": False, "error": f"{module} couldn't be loaded ({type(exc).__name__}: {exc})"}
    if fn is None:
        return {"available": False}
    try:
        answer = fn(catalog, key)
    except jobs.Cancelled:
        raise
    except Exception as exc:
        return {"available": True, "ok": False, "error": f"{type(exc).__name__}: {exc}"}
    if isinstance(answer, dict):
        out = {"ok": True}
        out.update(answer)
        out["available"] = True
        return out
    if isinstance(answer, (list, tuple)):
        return {"available": True, "ok": True, "issues" if name == "film_issues" else "items": list(answer)}
    if answer is None:
        return {"available": True, "ok": True}
    return {"available": True, "ok": True, "value": answer}


def _critics_part(catalog, film, _count=None):
    return _hook_part(*HOOKS["critics"], catalog, film.key)


def _files_part(catalog, film, _count=None):
    return _hook_part(*HOOKS["files"], catalog, film.key)


def _issues_part(catalog, film, _count=None):
    return _hook_part(*HOOKS["issues"], catalog, film.key)


def _history_part(catalog: Catalog, film: Film, _count=None) -> dict:
    """Your plays of it: the viewing-habits backbone's answer when there is one (as it gives it), else the
    catalog's own count - and that count is always in 'catalog' too: {plays, last_played, rating, note} (Plex's
    play count, which includes films only marked as played)."""
    base = {"plays": film.owner_plays, "last_played": _local(film.last_played, minutes=True),
            "rating": film.owner_rating, "note": PLAYS_NOTE,
            "library_items": len(film.plex_ids)}      # (Plex counts each edition's plays on its own)
    hooked = _hook_part(*HOOKS["history"], catalog, film.key)
    if hooked.get("available") and hooked.get("ok", True):
        out = hooked
    else:
        out = dict(base, **hooked)          # (not there yet, or it failed: the catalog's count, and why)
    out["catalog"] = base
    return out


_PART = {"film": _film_part, "ratings": _ratings_part, "credits": _credits_part, "people": _people_part,
         "similar": _similar_part, "critics": _critics_part, "history": _history_part, "files": _files_part,
         "issues": _issues_part}


# ---------------------------------------------------------------------------------------------------------
# The start page and the studio list
# ---------------------------------------------------------------------------------------------------------
def suggestions(catalog: Catalog, today: date | None = None, count: int = 6, exclude_libraries=(),
                exclude_genres=(), also_seen_by=()) -> dict:
    """A few films to open: the newest in Plex, your favourites (rated 9 or 10, the most recently played first)
    and well-rated films you haven't seen (IMDb 8 or more; a different few each day) - none of those in a library
    or of a genre left out (exclude_libraries / exclude_genres: Watch Next's 'Never suggest films from'), nor
    played on an account in also_seen_by (Settings > Your collection). Dates are in the style Settings > Dates
    and times chooses (formats.nice_date)."""
    today = today or date.today()
    films = list(catalog.films.values())
    added = sorted((f for f in films if f.added_at), key=lambda f: (-f.added_at, f.title))[:count]
    loved = sorted((f for f in films if (f.owner_rating or 0) >= 9),
                   key=lambda f: (-(f.last_played or 0), -(f.owner_rating or 0), f.title))[:count]
    leave_out = {"exclude_libraries": list(exclude_libraries or ()), "exclude_genres": list(exclude_genres or ())}
    also = R.seen_by({"also_seen_by": also_seen_by})
    pool = sorted((f for f in films if not f.seen(also) and (f.imdb_rating or 0) >= 8.0
                   and not R._left_out(f, leave_out)),
                  key=lambda f: (-(f.imdb_rating or 0), f.title))[:30]
    if len(pool) > count:
        start = (today.toordinal() * count) % len(pool)
        pool = (pool[start:] + pool[:start])[:count]

    def when(ts):
        d = local_datetime(ts)
        return formats.nice_date(d.date()) if d else ""   # 'Jun 17, 2024', as on the Viewing tab
    return {
        "recently_added": [dict(brief(f), note=f"added {when(f.added_at)}") for f in added],
        "favourites": [dict(brief(f), note=f"you rated it {f.owner_rating:g}") for f in loved],
        "well_rated_unseen": [dict(brief(f), note=f"IMDb {f.imdb_rating:g}") for f in pool],
    }


def studio_films(catalog: Catalog, studio: str) -> list[dict]:
    """A studio's films (Plex's one main 'studio' per film), oldest first."""
    want = fold(studio or "")
    if not want:
        return []
    films = [f for f in catalog.films.values() if f.studio and fold(f.studio) == want]
    films.sort(key=lambda f: (f.year or 0, fold(f.title, drop_article=True)))
    return [dict(brief(f), your_rating=f.owner_rating, imdb_rating=f.imdb_rating, played=f.owner_plays > 0)
            for f in films]


# ---------------------------------------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------------------------------------
GROUPS = [("film", "films", "Films"), ("person", "people", "People"), ("collection", "collections", "Collections"),
          ("genre", "genres", "Genres"), ("country", "countries", "Countries"), ("studio", "studios", "Studios"),
          ("library", "libraries", "Libraries"), ("critic", "critics", "Critics")]
GROUP_TITLES = {kind: title for kind, _plural, title in GROUPS}
GROUP_NAMES = {**{kind: kind for kind, _p, _t in GROUPS}, **{plural: kind for kind, plural, _t in GROUPS}}
ORDER = {kind: i for i, (kind, _p, _t) in enumerate(GROUPS)}
CAPS = {"film": 6, "person": 6, "collection": 4, "genre": 3, "country": 3, "studio": 4, "library": 3, "critic": 4}
YEAR = re.compile(r"^(18[89]\d|19\d\d|20[0-4]\d)$")
EXACT, WORDS, PREFIX, TYPO, PARTIAL, BY_YEAR = range(6)
SHORT = 2                      # typed words this short only filter (each must start a word of the name)
TOP = 60                       # entries kept per (1-2 letter start, group), for queries of only short words
LEAD_FILMS = 3                 # films a person needs to lead a title, collection... that matches as well
INDEX_KEY = ("filmpage.search_index",)
# Numbers as typed and as titles spell them: 'rocky 2' finds Rocky II, '7 samurai' Seven Samurai, 'toy story ii'
# Toy Story 2. A typed number 1-20 also matches its Roman numeral and its word; a typed Roman numeral (two letters
# or more - a lone 'i', 'v' or 'x' is too often a word or a letter) also matches the number.
_ROMAN = ("i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x", "xi", "xii", "xiii", "xiv", "xv", "xvi",
          "xvii", "xviii", "xix", "xx")
_NUMBER_WORDS = ("one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
                 "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty")
NUMERALS = {**{str(n): (_ROMAN[n - 1], _NUMBER_WORDS[n - 1]) for n in range(1, 21)},
            **{_ROMAN[n - 1]: (str(n),) for n in range(1, 21) if len(_ROMAN[n - 1]) > 1}}
_PUNCT = re.compile(r"[^a-z0-9]+")
_ARTICLE = re.compile(r"^(the|a|an) ")
_SKEL = str.maketrans({"c": "k", "q": "k", "z": "s", "v": "f", "w": "f"})
_INDEX_LOCK = threading.Lock()


def keys_of(text: str, drop_article: bool = False) -> list[str]:
    """The folded spellings a name is found by (a quick path for plain ASCII, most names)."""
    if text.isascii():
        k = " ".join(_PUNCT.sub(" ", text.lower()).split())
        ks = [k] if k else []
    else:
        ks = [k for k in dict.fromkeys((ascii_fold(text), fold(text))) if k]
    if drop_article:
        ks += [a for a in (_ARTICLE.sub("", k) for k in ks) if a and a not in ks]
    return ks


def skeleton(word: str) -> str:
    """Consonant skeleton, for 'sounds like': jacky / jackie -> 'jk', curosawa / kurosawa -> 'krsf'."""
    w = word.replace("ph", "f").replace("ck", "k").translate(_SKEL)
    out = w[0]
    for ch in w[1:]:
        if ch not in "aeiouyh" and ch != out[-1]:
            out += ch
    return out


def distance(a: str, b: str, most: int) -> int:
    """Damerau-Levenshtein (optimal string alignment: a swap of neighbours is one edit), or most + 1 as soon as
    it's surely more than `most`."""
    if abs(len(a) - len(b)) > most:
        return most + 1
    prev2, prev = None, list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur, ai, best = [i] + [0] * len(b), a[i - 1], i
        for j in range(1, len(b) + 1):
            v = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ai != b[j - 1]))
            if i > 1 and j > 1 and ai == b[j - 2] and a[i - 2] == b[j - 1]:
                v = min(v, prev2[j - 2] + 1)
            cur[j] = v
            best = min(best, v)
        if best > most:
            return most + 1
        prev2, prev = prev, cur
    return prev[-1]


def edits_allowed(word: str) -> int:
    """Typos forgiven in a typed word: none under 4 letters (or with digits), 1 up to 6 letters, then 2."""
    return 0 if len(word) < 4 or not word.isalpha() else 1 if len(word) < 7 else 2


def _deletes(w: str) -> set[str]:
    return {w[:i] + w[i + 1:] for i in range(len(w))}


class SearchIndex:
    """Everything searchable, as entries (group, id, label, keys, popularity, year), with the tables that make a
    keystroke's search take a few milliseconds:
      words     every word of every name -> the entries with it (and the sorted vocabulary, for prefixes)
      dels      one letter taken out of a word -> the words it could be a typo of (over the words of films,
                groups, critics and people with 2+ films: the 'typo vocabulary')
      skel      a consonant skeleton -> the words that sound like it
      short     a 1-2 letter start -> each group's most popular entries with a word starting so
      by_year   a year -> its films
    """

    def __init__(self, entries, check=lambda: None):
        self.entries = entries
        self.words: dict[str, list[int]] = defaultdict(list)
        self.entry_words: list[list[tuple]] = []
        typo_vocab = set()
        by_group = defaultdict(list)
        for n, (group, _id, _label, keys, pop, _year) in enumerate(entries):
            if n % 5000 == 0:
                check()                                  # (jobs.check in the app: a new catalog calls it off)
            kws = [tuple(k.split()) for k in keys]
            self.entry_words.append(kws)
            ws = {w for kw in kws for w in kw}
            for w in ws:
                self.words[w].append(n)
            if group != "person" or pop >= 2:
                typo_vocab.update(ws)
            by_group[group].append((pop, n))
        self.pct = [0.0] * len(entries)                  # popularity percentile inside the entry's group
        for rows in by_group.values():
            pops = sorted(p for p, _n in rows)
            for p, n in rows:                            # (equal popularity, equal percentile)
                self.pct[n] = bisect.bisect_left(pops, p) / max(len(pops) - 1, 1)
        check()
        self.vocab = sorted(self.words)
        self.skel: dict[str, list[str]] = defaultdict(list)
        self.dels: dict[str, list[str]] = defaultdict(list)
        for i, w in enumerate(self.vocab):
            if i % 5000 == 0:
                check()
            if len(w) >= 4 and w.isalpha():
                self.skel[skeleton(w)].append(w)
                if w in typo_vocab:
                    for d in _deletes(w) | {w}:
                        self.dels[d].append(w)
        check()
        heaps = defaultdict(list)
        for n, e in enumerate(entries):
            if n % 5000 == 0:
                check()
            item = (e[4], -n)
            for s in {w[:k] for kw in self.entry_words[n] for w in kw for k in (1, 2) if len(w) >= k}:
                h = heaps[(s, e[0])]
                if len(h) < TOP:
                    heapq.heappush(h, item)
                elif item > h[0]:
                    heapq.heapreplace(h, item)
        self.short: dict[str, list[int]] = defaultdict(list)
        for (s, _g), h in heaps.items():
            self.short[s].extend(-n for _p, n in h)
        self.by_year = defaultdict(list)
        for n, e in enumerate(entries):
            if e[0] == "film" and e[5]:
                self.by_year[e[5]].append(n)
        self.groups = {e[0] for e in entries}
        self.built_ms = 0.0
        self.critics = False

    # -- one typed word -------------------------------------------------------------------------------------------
    def _typed(self, w: str) -> dict:
        m = {w: (WORDS, 0)} if w in self.words else {}
        i = bisect.bisect_left(self.vocab, w)
        j = bisect.bisect_left(self.vocab, w + "￿")
        for v in self.vocab[i:min(j, i + 5000)]:
            if v != w:
                m[v] = (PREFIX, 0)
        for v in NUMERALS.get(w, ()):                    # 'iii' is 3 too
            if v in self.words:
                m[v] = (WORDS, 0)
        return m

    def _known(self, w: str, prefix: bool) -> bool:
        """A word of some name (or, prefix=True, the start of one)."""
        if w in self.words:
            return True
        if not prefix:
            return False
        i = bisect.bisect_left(self.vocab, w)
        return i < len(self.vocab) and self.vocab[i].startswith(w)

    def _near(self, w: str) -> dict:
        most = edits_allowed(w)
        if not most:
            return {}
        out, cands = {}, set(self.dels.get(w, ()))
        for d in _deletes(w):
            cands.update(self.dels.get(d, ()))
        for c in cands:
            if c != w and (d := distance(w, c, most)) <= most:
                out[c] = (TYPO, d)
        for c in self.skel.get(skeleton(w), ()):          # sounds alike: one edit more is fine
            if c != w and c not in out and (d := distance(w, c, most + 1)) <= most + 1:
                out[c] = (TYPO, d)
        return out

    # -- a query --------------------------------------------------------------------------------------------------
    def query(self, text: str, caps: dict | None = None, groups=None):
        """-> (how: 'typed' | 'spacing' (spaces moved: 'starwars') | 'near' (typos) | 'partial' | 'year' | '',
        [(group, total, [(rank, entry no)])])"""
        caps = caps or CAPS
        allowed = set(groups) if groups else None
        typed = (ascii_fold(text) or fold(text)).split()
        if not typed or (len(typed) == 1 and len(typed[0]) < 2 and typed[0].isascii()):
            return "", []
        # accents typed ('drácula') put the name spelled that way first; plain typing never does
        accents = [w for w in unicodedata.normalize("NFC", text or "").casefold().split() if not w.isascii()]
        years = [w for w in typed if YEAR.match(w)]
        words = [w for w in typed if w not in years]
        if not words:                                    # a year alone: titles with it, then that year's films
            how, out = self._words([years[0]], None, accents, allowed)
            if allowed is None or "film" in allowed:
                rows = out.setdefault("film", [])
                have = {n for _r, n in rows}
                for n in self.by_year.get(int(years[0]), []):
                    if n not in have:
                        e = self.entries[n]
                        rows.append(((BY_YEAR, 0, 0, 0, 0, -round(math.log1p(e[4]), 1), 0, 0, e[2]), n))
                if rows and not have:
                    how = "year"
                if not rows:
                    del out["film"]
            return (how or "year", self._groups(out, caps)) if out else ("", [])
        how, out = self._words(words, int(years[0]) if years else None, accents, allowed)
        if not out and years:                            # the number is part of a title: '2001 a space odyssey'
            how, out = self._words(typed, None, accents, allowed)
        return (how, self._groups(out, caps)) if out else ("", [])

    def _words(self, words, year, accents, allowed):
        long = [w for w in words if len(w) > SHORT]
        short = [w for w in words if len(w) <= SHORT]
        phrase = " ".join(words)
        if not long:
            pool = set(self.short.get(short[0], ())) | set(self.words.get(short[0], ()))
            return "typed", self._score([], [], 0, year, accents, short, pool, phrase, allowed)
        matches = [self._typed(w) for w in long]
        found = [bool(m) for m in matches]              # (words that match something as typed)
        out, how = {}, "typed"
        if all(matches):
            out = self._score(long, matches, len(long), year, accents, short, None, phrase, allowed)
        if not out:                                      # spaces left out or put in: 'starwars', 'ghost busters'
            out = self._spacing(words, year, accents, allowed)
            if out:
                return "spacing", out
        if not out:
            how, fixed = "near", False
            for m, w in zip(matches, long):
                if not m:
                    m.update(self._near(w))
                    fixed = True
            if fixed and all(matches):
                out = self._score(long, matches, len(long), year, accents, short, None, phrase, allowed)
        if not out and len(long) >= 3:
            how = "partial"
            out = self._score(long, matches, len(long) - 1, year, accents, short, None, phrase, allowed)
        if not out:
            how = "near"
            for m, w, as_typed in zip(matches, long, found):
                # (a word found as typed keeps its spelling when a number or an initial goes with it: 'rocky 2'
                # isn't 'rocks 2', which would find Arthur 2: On the Rocks)
                if as_typed and short:
                    continue
                for v, c in self._near(w).items():
                    m.setdefault(v, c)
            if all(matches):
                out = self._score(long, matches, len(long), year, accents, short, None, phrase, allowed)
        return how, out

    def _spacing(self, words, year, accents, allowed) -> dict:
        """What's typed with its spaces moved - two words run together ('starwars', 'xmen', 'deniro', 'cs lewis')
        or one split ('ghost busters', 'bat man') - matched as typed (no typos): the best match of any reading."""
        readings = []
        for i in range(len(words) - 1):                  # two neighbours as one word
            joined = words[i] + words[i + 1]
            if self._known(joined, prefix=i + 2 == len(words)):
                readings.append(words[:i] + [joined] + words[i + 2:])
        for i, w in enumerate(words):                    # one word as two
            last = i + 1 == len(words)
            if len(w) == SHORT and w.isalpha():
                readings.append(words[:i] + [w[0], w[1]] + words[i + 1:])     # 'cs' -> 'c s'
                continue
            if len(w) < 4 or self._known(w, prefix=last):
                continue
            for k in range(1, len(w)):
                a, b = w[:k], w[k:]
                a_word, b_word = len(a) > SHORT, len(b) > SHORT
                if not (a_word or b_word):
                    continue
                # a long part must be a word (the last one may be the start of one); a short part only filters,
                # so the other part must then be a whole word
                if a_word and a not in self.words:
                    continue
                if b_word and not self._known(b, prefix=last and a_word):
                    continue
                readings.append(words[:i] + [a, b] + words[i + 1:])
        best: dict[str, dict[int, tuple]] = defaultdict(dict)
        for reading in readings:
            long = [w for w in reading if len(w) > SHORT]
            short = [w for w in reading if len(w) <= SHORT]
            if not long:
                continue
            matches = [self._typed(w) for w in long]
            if not all(matches):
                continue
            got = self._score(long, matches, len(long), year, accents, short, None, " ".join(reading), allowed)
            for g, rows in got.items():
                for rank, n in rows:
                    if n not in best[g] or rank < best[g][n]:
                        best[g][n] = rank
        return {g: [(rank, n) for n, rank in rows.items()] for g, rows in best.items() if rows}

    def _candidates(self, matches, need) -> set[int]:
        if need == len(matches):
            sizes = [sum(len(self.words[v]) for v in m) for m in matches]
            cand = None
            for i in sorted(range(len(matches)), key=lambda i: sizes[i]):
                hit = {n for v in matches[i] for n in self.words[v]}
                cand = hit if cand is None else cand & hit
                if not cand:
                    return set()
            return cand
        count = Counter()
        for m in matches:
            count.update({n for v in m for n in self.words[v]})
        return {n for n, c in count.items() if c >= need}

    def _score(self, words, matches, need, year, accents, short, pool, phrase, allowed) -> dict:
        cand = pool if pool is not None else self._candidates(matches, need)
        out = defaultdict(list)
        for n in cand:
            group, _id, label, keys, pop, fyear = self.entries[n]
            if allowed is not None and group not in allowed:
                continue
            if year is not None and (group != "film" or fyear != year):
                continue                                 # a year only makes sense for films
            best = None
            for kw, key in zip(self.entry_words[n], keys):
                used, picks, missed = {}, {}, 0          # name word -> the typed word that took it (-1: a short one)
                for i, m in enumerate(matches):          # each typed word takes a different word of the name
                    pick = min(((m[v], j) for j, v in enumerate(kw) if v in m and j not in used), default=None)
                    if pick is None:
                        missed += 1
                        continue
                    (c, d), j = pick
                    used[j], picks[i] = i, (c, d)
                if len(matches) - missed < need:
                    continue
                ok, prefixes = True, 0
                for s in short:
                    also = NUMERALS.get(s, ())
                    j = next((j for j, v in enumerate(kw) if j not in used and (v.startswith(s) or v in also)),
                             None)
                    if j is None and s.isdigit():        # a number run into the word before it: 'alien 3' is
                        j = next((j for j, i in used.items()            # Alien³ ('alien3')
                                  if i >= 0 and kw[j] == words[i] + s), None)
                        if j is not None:
                            picks[used[j]] = (WORDS, 0)
                            continue
                    if j is None:
                        ok = False
                        break
                    used[j] = -1
                    prefixes += kw[j] != s and kw[j] not in also
                if not ok:
                    continue
                cls, dist = WORDS, 0
                for c, d in picks.values():
                    cls, prefixes, dist = max(cls, c), prefixes + (c == PREFIX), dist + d
                if missed:
                    cls = PARTIAL
                elif key == phrase and (group != "person" or pop >= LEAD_FILMS):
                    cls = EXACT                          # (a bit part called 'Universal' isn't a name to lead with)
                elif prefixes and not matches:
                    cls = PREFIX
                typo = round(dist - math.log1p(pop), 2) if cls >= TYPO else 0
                # a whole name but for a typo or two ('tom hnaks' is Tom Hanks) before names with more to them (or
                # only sounding alike: 'horor' isn't Hero)
                loose = int(cls == TYPO and (len(used) < len(kw) or (group == "person" and pop < LEAD_FILMS) or any(
                    c == TYPO and d > edits_allowed(words[i]) for i, (c, d) in picks.items())))
                accent = 0
                if accents:
                    spelled = unicodedata.normalize("NFC", label).casefold()
                    accent = sum(1 for w in accents if w not in spelled)
                rank = (cls, prefixes, loose, typo, accent, -round(math.log1p(pop), 1),
                        len(kw) - len(words) - len(short), 0 if 0 in used else 1)
                best = rank if best is None or rank < best else best
            if best is not None:
                out[group].append((best + (label,), n))
        return out

    def _groups(self, out: dict, caps: dict) -> list:
        groups = []
        for g, rows in out.items():
            if not rows:
                continue
            rows.sort()
            (cls, prefixes, loose, *_rest), _top = rows[0]
            film_exact = 0 if (g == "film" and cls == EXACT) else 1
            order = (cls, prefixes, loose, film_exact, -self._reach(g, rows), ORDER.get(g, 99))
            groups.append((order, g, len(rows), rows[:caps.get(g, 4)]))
        groups.sort()
        return [(g, total, rows) for _o, g, total, rows in groups]

    def _reach(self, group: str, rows) -> int:
        """How many of your films a group's best match stands for - the same measure for every group, to order
        groups that match equally well: the matching films themselves (the Rambo films for 'rambo'), a person's
        films, a collection's, genre's... ('bond': the James Bond collection before an actor called Bond with a
        few films). People with fewer than LEAD_FILMS films lead nothing, and critics lead only people like that:
        they're the least likely things to be looking for (Roger Ebert before an Ebert with 2 films, but not
        before a star with dozens)."""
        (rank, top) = rows[0]
        if group == "film":
            return sum(1 for r, _n in rows if r[:3] == rank[:3])
        if group == "critic":
            return 0
        films = int(self.entries[top][4])
        if group == "person" and films < LEAD_FILMS:
            return -1
        return films


def entries_for(catalog: Catalog, critics=()) -> list[tuple]:
    """(group, id, label, keys, popularity, year) for everything searchable. critics: [{id, name, reviews}]."""
    entries = []
    for n, f in enumerate(catalog.films.values()):
        if n % 1000 == 0:
            jobs.check()
        keys = []
        for t in f.titles or [f.title]:
            keys += [k for k in keys_of(t, drop_article=True) if k not in keys]
        entries.append(("film", f.key, f.label, keys, (3 if f.watched else 0) + (f.imdb_rating or 5) / 2, f.year))
    for n, p in enumerate(catalog.people.values()):
        if n % 10000 == 0:
            jobs.check()
        entries.append(("person", p.id, p.name, keys_of(p.name), p.film_count, None))
    counts = defaultdict(Counter)
    for f in catalog.films.values():
        for kind, values in (("collection", f.collections), ("genre", f.genres), ("country", f.countries),
                             ("studio", [f.studio] if f.studio else []), ("library", f.libraries)):
            counts[kind].update(v for v in values if v)
    for group, names in counts.items():
        for name, n in names.items():
            keys = keys_of(name)
            if group == "country":
                for alias in [country_name(name)] + COUNTRY_ALIASES.get(name, []):
                    keys += [k for k in keys_of(alias) if k not in keys]
            label = country_name(name) if group == "country" else name
            entries.append((group, name, label, keys, n, None))
    for c in critics or ():
        name = str(c.get("name") or "").strip()
        if name and c.get("id") not in (None, ""):
            entries.append(("critic", str(c["id"]), name, keys_of(name), int(c.get("reviews") or 1), None))
    return entries


def _critic_names(catalog: Catalog):
    """projectionist.critics.critic_names(catalog), when that exists: [{id, name, reviews?, publication?}] (None when
    it doesn't, or can't answer)."""
    try:
        fn = hook("critics", "critic_names")
        if fn is None:
            return None
        names = fn(catalog)
    except jobs.Cancelled:
        raise
    except Exception:
        return None
    if not isinstance(names, (list, tuple)):
        return None
    return [c for c in names if isinstance(c, dict)]


def search_index(catalog: Catalog, build: bool = True) -> SearchIndex | None:
    """The collection's search index - built the first time (about a second for a few thousand films and their
    tens of thousands of people; in a background job it stops when the job is called off), then kept with the
    catalog. build=False: None until it has been built."""
    index = catalog.cache.get(INDEX_KEY)
    if index is not None or not build:
        return index
    with _INDEX_LOCK:
        index = catalog.cache.get(INDEX_KEY)
        if index is None:
            started = time.perf_counter()
            critics = _critic_names(catalog)
            index = SearchIndex(entries_for(catalog, critics or ()), check=jobs.check)
            index.critics = bool(critics)
            index.critic_info = {str(c["id"]): c for c in critics or () if c.get("id") not in (None, "")}
            index.built_ms = round((time.perf_counter() - started) * 1000)
            catalog.cache[INDEX_KEY] = index
    return index


def warm(catalog: Catalog) -> SearchIndex:
    """Get a collection ready for the Film tab and the search box (in a background job, once it has loaded):
    the search index, and the labels that tell people with the same name apart."""
    index = search_index(catalog, build=True)
    jobs.check()
    costars.namesakes(catalog)
    jobs.check()
    costars.namesake_labels(catalog)
    jobs.check()
    feature_counts(catalog)
    _unseen_counts(catalog)
    return index


def _group_filter(value) -> set[str] | None:
    if value in (None, "", []):
        return None
    names = [value] if isinstance(value, str) else list(value)
    out = set()
    for n in names:
        k = GROUP_NAMES.get(str(n).strip().lower())
        if k is None:
            raise ValueError(f"unknown group '{n}' - use {', '.join(p for _k, p, _t in GROUPS)}")
        out.add(k)
    return out


def search(catalog: Catalog, request: dict) -> dict:
    """Everything whose name matches what's typed, in groups (Films, People, Collections, Genres, Countries,
    Studios, Libraries, Critics), the best group first. request: {q (or query, text), count (per group, 1-50),
    groups ([films, people...]), wait (default true: build the index first if it isn't yet; false answers
    ready: false instead)}."""
    request = request if isinstance(request, dict) else {}
    q = next((request[k] for k in ("q", "query", "text") if request.get(k) not in (None, "")), None)
    if q is None or not str(q).strip():
        return {"ok": False, "action": "search", "error": "q: what to look for"}
    q = str(q)
    count = R.number(request, "count", None, 1, 50, whole=True)
    allowed = _group_filter(request.get("groups"))
    caps = {g: count for g in CAPS} if count else CAPS
    out = {"ok": True, "action": "search", "q": q, "how": "", "ready": True, "groups": [], "top": None}
    typed = (ascii_fold(q) or fold(q)).split()
    if not typed or (len(typed) == 1 and len(typed[0]) < 2 and typed[0].isascii()):
        return dict(out, note="Type at least 2 letters.")
    index = search_index(catalog, build=request.get("wait", True) is not False)
    if index is None:
        return dict(out, ready=False, note="The search is getting ready.")
    started = time.perf_counter()
    how, found = index.query(q, caps, allowed)
    words = [w for w in typed if not YEAR.match(w)] or typed
    groups = []
    for kind, total, rows in found:
        results = [_result(catalog, index, index.entries[n], words) for _rank, n in rows]
        groups.append({"group": dict((k, p) for k, p, _t in GROUPS).get(kind, kind), "kind": kind,
                       "title": GROUP_TITLES.get(kind, kind.title()), "total": total, "results": results})
    return dict(out, how=how, groups=groups, top=groups[0]["results"][0] if groups else None,
                took_ms=round((time.perf_counter() - started) * 1000, 2),
                index={"entries": len(index.entries), "built_ms": index.built_ms, "critics": index.critics})


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n:,} {one if n == 1 else (many or one + 's')}"


def films_seen_text(films: int, unseen: int) -> str:
    """'27 films, 14 not seen yet' / '27 films, all seen' - a collection's (genre's...) films, and how many of them
    Watch Next can still offer (it leaves out what you've played or rated)."""
    if films == 1:
        return "1 film, not seen yet" if unseen else "1 film, seen"
    if not unseen:
        return f"{films:,} films, all seen"
    if unseen >= films:
        return f"{films:,} films, none seen yet"
    return f"{films:,} films, {unseen:,} not seen yet"


def _unseen_counts(catalog: Catalog) -> dict[str, Counter]:
    """{kind: {collection / genre / country / studio / library: films you haven't played or rated}} - once per
    collection."""
    key = ("filmpage.unseen_counts",)
    if key not in catalog.cache:
        counts = defaultdict(Counter)
        for f in catalog.films.values():
            if f.watched:
                continue
            for kind, values in (("collection", f.collections), ("genre", f.genres), ("country", f.countries),
                                 ("studio", [f.studio] if f.studio else []), ("library", f.libraries)):
                counts[kind].update(v for v in values if v)
        catalog.cache[key] = dict(counts)
    return catalog.cache[key]


def _matched_title(film: Film, words) -> str:
    """The other title a search matched, when it didn't match the main one ('黑俠' for Black Mask)."""
    def hits(title):
        return any(all(any(k.startswith(w) for k in key.split()) for w in words)
                   for key in keys_of(title, drop_article=True))
    if not words or hits(film.title):
        return ""
    return next((t for t in other_titles(film) if hits(t)), "")


def _result(catalog: Catalog, index: SearchIndex, entry, words) -> dict:
    group, id_, label, _keys, pop, _year = entry
    out = {"kind": group, "id": id_, "label": label}
    if group == "film":
        film = catalog.films.get(id_)
        if film is None:
            return dict(out, detail="")
        bits = []
        alt = _matched_title(film, words)
        if alt:
            bits.append(f"also called {alt}")
        if film.directors:
            bits.append(and_list([c.name for c in film.directors[:2]]) + (" and others" if len(film.directors) > 2
                                                                          else ""))
        if film.libraries:
            bits.append(", ".join(film.libraries))
        if film.owner_rating is not None:
            bits.append(f"you rated it {film.owner_rating:g}")
        elif film.owner_plays:
            bits.append("seen")
        return dict(out, key=film.key, year=film.year, seen=film.watched, your_rating=film.owner_rating,
                    detail="  ·  ".join(bits), **({"also_called": alt} if alt else {}))
    if group == "person":
        p = catalog.people.get(id_)
        if p is None:
            return dict(out, detail="")
        directs = costars.mostly_directs(p)
        known = costars.known_for(catalog, p)
        if directs:
            detail = f"directed {len(p.directed):,} of your films"
        else:
            detail = _plural(p.film_count, "film")
        if known is not None:
            detail += (f": {known.label}" if p.film_count == 1 else f"  ·  e.g. {known.label}")
        return dict(out, name=p.name, films=p.film_count, directs=directs,
                    known_for=brief(known) if known is not None else None, detail=detail,
                    namesake=p.name in costars.namesakes(catalog))
    if group == "critic":
        info = getattr(index, "critic_info", {}).get(id_, {})
        reviews = int(info.get("reviews") or pop or 0)
        bits = [_plural(reviews, "review")] if reviews else []
        if info.get("publication"):
            bits.append(str(info["publication"]))
        return dict(out, reviews=reviews, publication=info.get("publication") or "", detail="  ·  ".join(bits))
    films = int(pop)
    unseen = _unseen_counts(catalog).get(group, {}).get(id_, 0)
    out.update(films=films, unseen=unseen, detail=films_seen_text(films, unseen))
    if group == "country":
        out["display"] = label                   # (the id is Plex's name, which Watch Next filters on)
    return out
