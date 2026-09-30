"""The collection at a glance: the counts and rankings behind the Overview tab (and ask's "overview")."""

from __future__ import annotations

import heapq
import math
from collections import Counter, defaultdict

from .catalog import Catalog
from .jobs import check

_RESOLUTIONS = ["8K", "4K", "1080p", "720p", "576p", "480p", "SD"]


def _top(counter: Counter, n: int) -> list[dict]:
    return [{"label": label, "films": count} for label, count in
            sorted(counter.items(), key=lambda kv: (-kv[1], kv[0].casefold()))[:n]]


def overview(catalog: Catalog, request: dict | None = None) -> dict:
    request = request or {}
    try:
        top_n = max(1, min(int(request.get("count", 10)), 100))
    except (TypeError, ValueError):
        raise ValueError("count must be a number") from None
    films = list(catalog.films.values())
    decades, libraries, genres, countries, resolutions = Counter(), Counter(), Counter(), Counter(), Counter()
    played_by_library = Counter()
    minutes = 0
    for f in films:
        check()
        if f.year:
            decades[f.year // 10 * 10] += 1
        for lib in f.libraries:
            libraries[lib] += 1
            if f.owner_plays:
                played_by_library[lib] += 1
        genres.update(set(f.genres))
        countries.update(set(f.countries))
        if f.resolution:
            resolutions[f.resolution] += 1
        minutes += f.runtime_min or 0

    by_decade = []
    if decades:
        for d in range(min(decades), max(decades) + 10, 10):
            by_decade.append({"decade": d, "label": f"{d}s", "films": decades.get(d, 0)})

    all_roles, leads, directing = Counter(), Counter(), Counter()
    for n, p in enumerate(catalog.people.values()):
        if not n & 1023:
            check()
        if p.acted:
            all_roles[p.id] = len(p.acted)
            leads[p.id] = sum(1 for c in p.acted.values() if c.order <= 3)
        if p.directed:
            directing[p.id] = len(p.directed)

    def people(counter: Counter) -> list[dict]:
        check()
        # Only the top few are wanted: sort just the people with at least the top_n-th biggest count (ties
        # included) - not all 76,000 names.
        least = min(heapq.nlargest(top_n, counter.values()), default=0)
        rows = sorted(((n, catalog.people[pid].name, pid) for pid, n in counter.items() if n and n >= least),
                      key=lambda t: (-t[0], t[1].casefold()))[:top_n]
        return [{"id": pid, "name": name, "films": n} for n, name, pid in rows]

    rated = [f for f in films if f.owner_rating is not None]
    pairs = [(f.owner_rating, f.imdb_rating) for f in rated if f.imdb_rating is not None]
    agreement = None
    if len(pairs) >= 3:
        mx = sum(p[1] for p in pairs) / len(pairs)
        my = sum(p[0] for p in pairs) / len(pairs)
        sx = math.sqrt(sum((p[1] - mx) ** 2 for p in pairs))
        sy = math.sqrt(sum((p[0] - my) ** 2 for p in pairs))
        if sx and sy:
            agreement = round(sum((p[1] - mx) * (p[0] - my) for p in pairs) / (sx * sy), 3)
    your_hist = Counter(round(f.owner_rating) for f in rated)

    return {
        "ok": True, "action": "overview",
        "totals": {
            # films count once however many copies (library items) they have; the Export tab counts items
            "films": len(films), "library_items": sum(len(f.plex_ids) or 1 for f in films),
            "people": len(catalog.people), "hours": round(minutes / 60),
            "libraries": len(libraries), "rated_by_you": len(rated),
            "played_by_you": sum(1 for f in films if f.owner_plays),
            "credits_scanned": sum(1 for f in films if f.credits_copies),
            "your_average": round(sum(f.owner_rating for f in rated) / len(rated), 2) if rated else None,
            "imdb_average_of_those": round(sum(p[1] for p in pairs) / len(pairs), 2) if pairs else None,
        },
        "by_decade": by_decade,
        "by_library": _top(libraries, 50),
        "genres": _top(genres, top_n),
        "countries": _top(countries, top_n),
        "resolutions": [{"label": r, "films": resolutions[r]} for r in _RESOLUTIONS if resolutions.get(r)],
        "top_actors": people(all_roles),
        "top_lead_actors": people(leads),
        "top_directors": people(directing),
        "watched_by_library": [{"label": lib, "films": n, "played": played_by_library.get(lib, 0),
                                "share": round(played_by_library.get(lib, 0) / n, 3)}
                               for lib, n in sorted(libraries.items(), key=lambda kv: -kv[1])],
        "your_ratings": [{"rating": r, "films": your_hist.get(r, 0)} for r in range(1, 11)],
        "ratings_vs_imdb": [{"title": f.title, "year": f.year, "yours": f.owner_rating, "imdb": f.imdb_rating,
                             "key": f.key} for f in rated if f.imdb_rating is not None],
        "agreement_with_imdb": agreement,
    }
