"""Ask questions about your collection: a JSON request in, a JSON answer out.

    python -m projectionist.ask '{"action": "connect", "from": "Kevin Bacon", "to": "Tom Hanks"}'
    python -m projectionist.ask requests.json            # a file holding one request, or a list of them
    echo {"action": "help"} | python -m projectionist.ask -
    python -m projectionist.ask --serve 8765             # answer POSTed requests at http://127.0.0.1:8765/

From Python:  handle({"action": "recommend", "sort": "personal"}, db_path)  ->  dict

The database defaults to the newest Plex dump in the folder above this package (where Projectionist
lives); --db picks another file or folder. Everything is read-only. Names and titles are matched
loosely ("de niro", "star wars 4"); when a match is only a guess, the answer says so in
"matched" so a front end can check with the user. A bad request never crashes anything: the answer
just has "ok": false and an "error".
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time

from . import APP_NAME, costars, insights, recommend
from .catalog import Catalog, Film, fold, load
from .credits import clock, describe, span
from .extract import PlexDBError
from .recommend import number

ACTIONS = {
    "help": "this list",
    "info": "what's loaded: films, people, ratings, libraries, and the server's other accounts that have played "
            "films (their ids are what also_seen_by takes)",
    "overview": "the collection at a glance: films by decade, library, genre, country and resolution; the most "
                "featured actors and directors; your ratings against IMDb. {count}",
    "film": "everything about one film: details, credits scenes and its page - ratings, people, similar films, "
            "critics, your plays, files and library health. {title or film_key, parts: all (default) | core | none "
            "| [film, ratings, credits, people, similar, critics, history, files, issues], similar (1-50), "
            "also_seen_by: other accounts' ids - films they played aren't listed as similar ones not seen yet}",
    "credits": "credits scenes: {title} for one film, or a list filtered by {verdict: Yes|Maybe|None found, "
               "library, count}",
    "recommend": "films to watch next. " + "; ".join(f"{k}: {v}" for k, v in recommend.REQUEST_HELP.items()),
    "evaluate": "how well the recommender predicts your own ratings (cross-validated). {folds (2-20), "
                "repeats (1-10), lambdas (up to 8)}",
    "taste": "your taste profile: the genres, decades, studios, countries, directors and actors you rate above or "
             "below their IMDb/RT scores. {kinds, count, min_films}",
    "connect": "the chain of your films linking two people. {from or from_id, to or to_id, avoid: [names], "
               "avoid_ids: [ids], max_billing, include_directors: true, false or 'auto' (when either of them "
               "mostly directs)}",
    "person": "someone's films, frequent co-stars, circle and reach on your shelf. {name or id, max_billing, "
              "include_directors: true, false or 'auto' (when they mostly direct), circle}",
    "center": "the best-connected people on your shelf. {count, candidates (5-500), max_billing, include_directors}",
    "bridges": "people linking two parts of your collection. {a, b: a library name or {library, country, genre, "
               "decade}, count, max_billing}",
    "troupes": "groups of actors who keep appearing together. {min_shared (2-50), max_billing (2-12), min_size, "
               "count}",
    "search": "find films, people, collections, genres, countries, studios, libraries and critics by name (accents "
              "aside, typos forgiven). {q, count (per group, 1-50), groups: [films, people, collections, genres, "
              "countries, studios, libraries, critics]}",
    "critics": "the critics whose Fresh/Rotten verdicts agree with your ratings, and what they say about films. "
               "{view: overview (default; count, min_shared, points) | critic (critic: an id or a name, count, "
               "also_seen_by) | film (film_key or title) | picks (recommend's filters, include_watched, also_seen_by, "
               "max_per_director, count) | evaluate (model_check) | names}",
    "habits": "your viewing habits: when you watch, streaks, most played, films you stopped partway, forgotten "
              "favourites, a year in review. {year: a year or 'latest', min_rating (1-10, default 9), forgotten_days "
              "(30-3650, default 365), count (1-500), top (1-20), parts: [history, heatmap, months, streaks, "
              "most_played, stopped, forgotten, year]}",
    "doctor": "library health: duplicate copies, weaker copies, unusual file sizes, missing subtitles, audio "
              "languages, upgrade candidates, missing details, disk use. {languages, issue, count, min_rating, "
              "film_key}",
    "letterboxd": "your plays and ratings as Letterboxd's import file (the owner's only): each dated play a diary "
                  "entry, films played or rated with no date marked watched, IMDb (else TMDB) IDs to match on. "
                  "{diary, undated, ratings, library_tags: true or false, since: a date or time (only what's new; "
                  "covered_to gives the next one), leave_out_libraries: [names], csv: true for the files' text "
                  "(split at Letterboxd's 1 MB), count: lines to list}",
}

_CATALOGS: dict[tuple, Catalog] = {}


def default_database() -> str | None:
    from .files import find_newest_database
    return find_newest_database(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def resolve_database(path: str | None) -> str:
    from .files import find_newest_database
    if not path:
        found = default_database()
        if not found:
            raise FileNotFoundError(f"no Plex database found next to {APP_NAME} - pass --db")
        return found
    if os.path.isdir(path):
        found = find_newest_database(path)
        if not found:
            raise FileNotFoundError(f"no Plex database found in {path}")
        return found
    return path


def catalog_for(db_path: str) -> Catalog:
    """Load a database once per process (reloaded if the file changes)."""
    path = os.path.abspath(db_path)
    stat = os.stat(path)
    key = (path, stat.st_size, stat.st_mtime)
    if key not in _CATALOGS:
        _CATALOGS.clear()
        _CATALOGS[key] = load(path)
    return _CATALOGS[key]


def handle(request, db_path: str | None = None, catalog: Catalog | None = None) -> dict:
    """Answer one request. Never raises: a bad request gets ok: false and an error."""
    if not isinstance(request, dict):
        return {"ok": False, "error": "a request is a JSON object, e.g. {\"action\": \"help\"}"}
    action = str(request.get("action", "help")).lower()
    if action == "help":
        return {"ok": True, "action": "help", "actions": ACTIONS}
    if action not in ACTIONS:
        return {"ok": False, "action": action, "error": f"unknown action '{action}'", "actions": list(ACTIONS)}
    try:
        catalog = catalog or catalog_for(resolve_database(db_path))
    except (PlexDBError, OSError) as exc:
        return {"ok": False, "action": action, "error": str(exc)}
    try:
        answer = _ANSWERS[action](catalog, request)
    except (ValueError, TypeError, OverflowError) as exc:
        answer = {"ok": False, "error": f"bad request: {exc}"}
    except Exception as exc:          # a bug shouldn't take a server down - report it instead
        answer = {"ok": False, "error": f"couldn't answer ({type(exc).__name__}: {exc})"}
    answer.setdefault("action", action)
    return answer


def to_json(answer, indent=None) -> str:
    """JSON any client can read: no NaN or Infinity (they become null)."""
    def clean(x):
        if isinstance(x, float) and not math.isfinite(x):
            return None
        if isinstance(x, dict):
            return {str(k): clean(v) for k, v in x.items()}
        if isinstance(x, (list, tuple, set)):
            return [clean(v) for v in x]
        return x
    return json.dumps(clean(answer), ensure_ascii=False, indent=indent, default=str, allow_nan=False)


# ---------------------------------------------------------------------------------------------------------
def _info(catalog: Catalog, request: dict) -> dict:
    films = catalog.films.values()
    return {"ok": True, "database": catalog.source, "owner": catalog.owner, "libraries": catalog.libraries,
            "films": len(catalog.films), "people": len(catalog.people),
            "films_you_rated": sum(1 for f in films if f.owner_rating is not None),
            "films_you_played": sum(1 for f in films if f.owner_plays),
            "films_with_credits_markers": sum(1 for f in films if f.credits_copies),
            # (the ids a request's also_seen_by takes: films played on those accounts count as seen)
            "other_accounts": [{"id": a, "name": catalog.account_name(a), "films_played": n}
                               for a, n in catalog.account_plays().items()]}


def _find_film(catalog: Catalog, title: str):
    film, others, how = catalog.find_film(title)
    if film is None:
        return None, {"ok": False, "error": f"no film matching '{title}'", "suggestions": [f.label for f in others[:8]]}
    extra = {"matched": {"asked": title, "found": film.label, "how": how,
                         "other_candidates": [f.label for f in others[:5]]}}
    return film, extra


def _find_person(catalog: Catalog, name: str):
    person, others, how = catalog.find_person(name)
    if person is None:
        return None, {"ok": False, "error": f"no one matching '{name}'", "suggestions": [p.name for p in others[:8]],
                      "suggestion_ids": [p.id for p in others[:8]]}
    if how == "guess":
        # A near spelling of a name two people share: the one with more films first, as for an exact name
        # (the guesses otherwise keep their closest-spelling order).
        everyone, group = [person] + others, {}
        for p in everyone:
            group.setdefault(fold(p.name), len(group))
        everyone.sort(key=lambda p: (group[fold(p.name)], -p.film_count))
        person, others = everyone[0], everyone[1:]
    return person, _matched(name, person, how, others)


def _matched(asked: str, person, how: str, others) -> dict:
    """How a name was matched, with IDs: two people can share a name, and a front end needs to tell them apart."""
    return {"asked": asked, "found": person.name, "how": how, "id": person.id,
            "other_candidates": [f"{p.name} ({p.film_count} films)" for p in others[:5]],
            "other_ids": [p.id for p in others[:5]]}


def _person_by_id(catalog: Catalog, pid):
    """An exact person by ID, or (None, None) when there's no such ID. Anyone else with the same name is listed
    among the other candidates, so a front end can say there are two of them."""
    person = catalog.people.get(str(pid)) if pid not in (None, "") else None
    if person is None:
        return None, None
    others = [p for p in costars.namesakes(catalog).get(person.name, []) if p is not person]
    return person, _matched(str(pid), person, "exact", others)


def _directors(request: dict, people) -> tuple[bool, bool]:
    """include_directors is true, false or 'auto': count directors when one of these people mostly directs (their
    co-stars are the casts of the films they directed). -> (count them?, decided automatically?)"""
    value = request.get("include_directors")
    if isinstance(value, str) and value.strip().lower() == "auto":
        return any(costars.mostly_directs(p) for p in people), True
    return bool(value), False


def _one_copy(info) -> dict:
    ignored = set(info.ignored)

    def sec(ms):
        # To a tenth of a second, but never rounded up into the next whole second (2:05:11.96 is 7511.9, not
        # 7512.0), so a time cut to the second says what the h:mm:ss strings beside it say.
        return min(round(ms / 1000, 1), ms // 1000 + 0.9)
    return {
        "stay_after_credits": info.verdict, "credits_start": clock(info.credits_start),
        "runtime_before_credits_min": round(info.credits_start / 60000),
        "scenes": [{"kind": s.kind, "verdict": s.verdict, "starts_at": clock(s.start), "ends_at": clock(s.end),
                    "length": span(s.length), "why_maybe": s.reason, "summary": describe(s),
                    "start_sec": sec(s.start), "end_sec": sec(s.end)}
                   for s in info.scenes],
        # For drawing the end of the film: the file's length and every stretch Plex marked as credits
        # ('counted' is false for ones taken to be on-screen text inside the film).
        "duration_sec": sec(info.duration) if info.duration else None,
        "credits_start_sec": sec(info.credits_start),
        "stretches": [{"start_sec": sec(a), "end_sec": sec(b),
                       "counted": (a, b) not in ignored} for a, b in info.stretches],
    }


def _credits_dict(film: Film) -> dict:
    if not film.credits_copies:
        return {"stay_after_credits": "",
                "note": "nothing to go on: Plex hasn't marked credits on this film (or only something it took for "
                        "credits, followed by minutes more film)"}
    best = max(film.credits_copies, key=lambda c: {"Yes": 3, "Maybe": 2, "None found": 1}.get(c.info.verdict, 0))
    out = dict(_one_copy(best.info), plex_id=best.plex_id, edition=best.edition)
    if len(film.credits_copies) > 1:
        if len({c.info.verdict for c in film.credits_copies}) > 1:
            out["note"] = "Your copies differ; the answer above is for the copy with the most to stay for."
        out["copies"] = [dict(_one_copy(c.info), plex_id=c.plex_id, edition=c.edition)
                         for c in film.credits_copies]
    return out


def _film(catalog: Catalog, request: dict) -> dict:
    """One film: {title | film_key (or key), parts: 'all' (default) | 'core' | 'none' | [part names], similar}.
    By key exactly; else by 'Title (Year)' exactly (so 'Drácula (1931)' can't come back as 'Dracula (1931)');
    else the loose title match. 'page' holds filmpage.film_page's answer (see there) unless parts is 'none'."""
    from . import filmpage
    key = request.get("film_key") or request.get("key")
    title = str(request.get("title") or "")
    parts = request.get("parts", "all")
    if isinstance(parts, str):
        parts = parts.strip().lower() or "all"
    elif parts is None:
        parts = "all"
    film, how, others = filmpage.find(catalog, key, None)
    if film is not None:
        extra = {"matched": {"asked": str(key), "found": film.label, "how": "key", "other_candidates": []}}
    elif key and not title:
        return {"ok": False, "error": f"no film with the key '{key}'"}
    else:
        film, how, others = filmpage.find(catalog, None, title) if title else (None, "", [])
        if film is not None and how == "exact":
            extra = {"matched": {"asked": title, "found": film.label, "how": "exact",
                                 "other_candidates": [f.label for f in others[:5]]}}
        else:
            film, extra = _find_film(catalog, title)
            if film is None:
                return extra
    answer = dict({"ok": True, "film": dict(film.to_dict(), key=film.key), "credits": _credits_dict(film)}, **extra)
    if parts != "none":
        answer["page"] = filmpage.film_page(catalog, film.key, parts=parts,
                                            similar=number(request, "similar", 8, 1, 50, whole=True),
                                            also_seen_by=recommend.seen_by(request))
    return answer


def _credits(catalog: Catalog, request: dict) -> dict:
    if request.get("title"):
        film, extra = _find_film(catalog, str(request["title"]))
        if film is None:
            return extra
        return dict({"ok": True, "film": film.brief(), "credits": _credits_dict(film)}, **extra)
    wanted = {v.casefold() for v in recommend._as_list(request.get("verdict") or ["Yes", "Maybe"])}
    libs = {v.casefold() for v in recommend._as_list(request.get("library") or request.get("libraries"))}
    count = number(request, "count", 50, 1, 5000, whole=True)
    rows = []
    for film in sorted(catalog.films.values(), key=lambda f: (f.title.casefold(), f.year or 0)):
        if not any(c.info.verdict.casefold() in wanted for c in film.credits_copies):
            continue                     # any copy with the verdict asked for counts
        if libs and not libs & {lib.casefold() for lib in film.libraries}:
            continue
        rows.append(dict(film.brief(), **_credits_dict(film)))
    return {"ok": True, "matching_films": len(rows), "films": rows[:count],
            "note": "From Plex's credits markers. Yes = a likely mid/post-credits scene or outtakes; Maybe = "
                    "footage that could be a title card or text; None found = nothing Plex could see (a short "
                    "gag at the very end or outtakes beside the credits can still be missed)."}


def _graph(catalog: Catalog, request: dict) -> costars.Graph:
    billing = number(request, "max_billing", None, 1, 1000, whole=True)
    return costars.graph_for(catalog, billing, bool(request.get("include_directors")))


def _connect(catalog: Catalog, request: dict) -> dict:
    ends = []
    for side in ("from", "to"):          # by ID when there is one (two people can share a name), else by name
        p, how = _person_by_id(catalog, request.get(f"{side}_id"))
        if p is None:
            p, how = _find_person(catalog, str(request.get(side, "")))
        if p is None:
            return dict(how, side=side)
        ends.append((p, how))
    (a, how_a), (b, how_b) = ends
    avoid, avoided = set(), []
    for pid in recommend._as_list(request.get("avoid_ids")):
        p, how = _person_by_id(catalog, pid)
        if p is None:
            return {"ok": False, "error": f"no one with the ID '{pid}'", "side": "avoid"}
        avoid.add(p.id)
        avoided.append({k: how[k] for k in ("asked", "found", "how", "id")})
    for name in recommend._as_list(request.get("avoid")):
        p, how = _find_person(catalog, name)
        if p is None:
            return dict(how, side="avoid")
        avoid.add(p.id)
        avoided.append({k: how[k] for k in ("asked", "found", "how", "id")})
    directors, auto = _directors(request, [a, b])
    graph = costars.graph_for(catalog, number(request, "max_billing", None, 1, 1000, whole=True), directors)
    started = time.perf_counter()
    path = graph.connect(a.id, b.id, avoid - {a.id, b.id})
    out = {"ok": True, "from": a.name, "to": b.name, "from_id": a.id, "to_id": b.id, "matched": [how_a, how_b],
           "avoided": avoided, "include_directors": directors, "directors_auto": auto,
           "searched_ms": round((time.perf_counter() - started) * 1000)}
    if path is None:
        out.update(connected=False, note="no chain of films in your library links them" +
                   (" with those people avoided" if avoid else ""))
    else:
        out.update(connected=True, **path)
    return out


def _person(catalog: Catalog, request: dict) -> dict:
    p, how = _person_by_id(catalog, request.get("id"))    # an exact person (two people can share a name)
    if p is None:
        p, how = _find_person(catalog, str(request.get("name", "")))
    if p is None:
        return how
    directors, auto = _directors(request, [p])
    graph = costars.graph_for(catalog, number(request, "max_billing", None, 1, 1000, whole=True), directors)
    count = number(request, "count", 15, 1, 500, whole=True)
    circle = number(request, "circle", 12, 0, 40, whole=True)
    return dict({"ok": True, "matched": how, "include_directors": directors, "directors_auto": auto},
                **costars.profile(catalog, graph, p, count, circle))


def _center(catalog: Catalog, request: dict) -> dict:
    graph = _graph(catalog, request)
    candidates = number(request, "candidates", 100, 5, 500, whole=True)
    count = number(request, "count", 10, 1, 500, whole=True)
    key = ("center", graph.max_billing, graph.include_directors, candidates)
    if key not in catalog.cache:                      # a few seconds' work: keep it
        catalog.cache[key] = graph.center(candidates)
    return {"ok": True, "people": catalog.cache[key][:count],
            "note": f"Average number of steps to everyone they can reach, among the {candidates} people with the "
                    "most films (a step = sharing a film)."}


def _group(value) -> dict:
    if isinstance(value, str) and value:
        value = {"library": value}
    return costars.check_group(value)


def _bridges(catalog: Catalog, request: dict) -> dict:
    a, b = _group(request.get("a")), _group(request.get("b"))
    out = costars.bridges(catalog, a, b, number(request, "count", 15, 1, 500, whole=True),
                          number(request, "max_billing", None, 1, 1000, whole=True))
    return dict({"ok": True, "a": a, "b": b}, **out)


def _troupes(catalog: Catalog, request: dict) -> dict:
    graph = _graph(catalog, {})
    rows = graph.troupes(number(request, "min_shared", 5, 2, 50, whole=True),
                         number(request, "max_billing", 8, 2, 12, whole=True),
                         number(request, "min_size", 3, 2, 20, whole=True))
    return {"ok": True, "troupes": rows[: number(request, "count", 15, 1, 500, whole=True)], "found": len(rows)}


def _lazy(module: str, name: str):
    """An answer that lives in its own backbone module, imported the first time it's asked for."""
    def answer(catalog: Catalog, request: dict) -> dict:
        import importlib
        return getattr(importlib.import_module(f"{__package__}.{module}"), name)(catalog, request)
    answer.__name__ = f"{module}.{name}"
    return answer


_ANSWERS = {
    "info": _info, "overview": insights.overview, "film": _film, "credits": _credits,
    "recommend": recommend.recommend, "evaluate": recommend.evaluate, "taste": recommend.taste_profile,
    "connect": _connect, "person": _person, "center": _center, "bridges": _bridges, "troupes": _troupes,
    "search": _lazy("filmpage", "search"), "critics": _lazy("critics", "answer"),
    "habits": _lazy("habits", "answer"), "doctor": _lazy("doctor", "answer"),
    "letterboxd": _lazy("letterboxd", "answer"),
}


def _answer_all(request, db_path) -> dict:
    if isinstance(request, list):
        return {"ok": True, "answers": [handle(r, db_path) for r in request]}
    return handle(request, db_path)


# ---------------------------------------------------------------------------------------------------------
def serve(db_path: str | None, port: int, host: str = "127.0.0.1"):
    """Answer JSON requests POSTed to http://host:port/ (GET / answers {"action": "help"})."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def _send(self, answer: dict, status: int = 200):
            body = to_json(answer).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self._send(handle({"action": "help"}, db_path))

        def do_POST(self):
            try:
                size = int(self.headers.get("Content-Length") or 0)
                request = json.loads(_decode(self.rfile.read(min(max(size, 0), 1 << 20))) or "{}")
            except (ValueError, UnicodeDecodeError) as exc:
                self._send({"ok": False, "error": f"not JSON: {exc}"}, 400)
                return
            self._send(_answer_all(request, db_path))

        def log_message(self, fmt, *args):
            sys.stderr.write(f"  {self.address_string()} {fmt % args}\n")

    catalog_for(resolve_database(db_path))          # load up front, so the first request is quick
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Answering requests at http://{host}:{port}/  (Ctrl+C to stop)", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def _decode(data: bytes) -> str:
    """Request text in UTF-8 (with or without a BOM), UTF-16 (PowerShell's '>') or the ANSI code page."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = argparse.ArgumentParser(prog="python -m projectionist.ask",
                                     description=f"{APP_NAME}: answer JSON questions about a Plex movie "
                                                 "collection.")
    parser.add_argument("request", nargs="?", default=None,
                        help="a JSON request (or list of requests), a file holding one, or - to read stdin")
    parser.add_argument("--db", help="Plex database file, or a folder (its newest dump is used)")
    parser.add_argument("--serve", type=int, metavar="PORT", help="answer POSTed JSON requests on this port")
    parser.add_argument("--pretty", action="store_true", help="indent the JSON answer")
    args = parser.parse_args(argv)
    if args.serve:
        serve(args.db, args.serve)
        return 0
    try:
        if args.request is None or args.request == "-":
            raw = _decode(sys.stdin.buffer.read()) if hasattr(sys.stdin, "buffer") else sys.stdin.read()
        elif os.path.isfile(args.request):
            with open(args.request, "rb") as fh:
                raw = _decode(fh.read())
        else:
            raw = args.request
        request = json.loads(raw.strip() or '{"action": "help"}')
    except (ValueError, OSError) as exc:
        print(to_json({"ok": False, "error": f"couldn't read the request: {exc}"}))
        return 2
    answer = _answer_all(request, args.db)
    print(to_json(answer, 2 if args.pretty else None))
    return 0 if answer.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
