"""Six degrees of your shelf: who is linked to whom through the films you own.

Two people are one step apart when they're in the same film (both in the cast, or - if asked for -
one of them directed it). Everything is worked out from your library only, so the answers are about
your collection: the chain of your films that gets from Kevin Bacon to Tom Hanks, the best
connected person on your shelf, the people who bridge two parts of it (your French and Hollywood
films, say), and the troupes of actors who keep turning up together.

Plain Python (breadth-first searches over the person-film graph); every query takes well under a
second except 'center', which searches from each of several dozen candidates.
"""

from __future__ import annotations

from collections import Counter, defaultdict

try:
    from itertools import batched
except ImportError:                      # (Python before 3.12)
    from itertools import islice

    def batched(iterable, n):
        it = iter(iterable)
        while chunk := tuple(islice(it, n)):
            yield chunk

from .catalog import Catalog, Film, Person, fold
from .jobs import check


class Graph:
    def __init__(self, catalog: Catalog, max_billing: int | None = None, include_directors: bool = False):
        self.catalog = catalog
        self.max_billing = max_billing
        self.include_directors = include_directors
        self.film_people: dict[str, list[tuple[str, int, str]]] = {}   # film -> [(person, billing, role)]
        self.person_films: dict[str, list[str]] = defaultdict(list)
        for key, film in catalog.films.items():
            check()
            members, seen = [], set()
            for c in film.cast:
                if (max_billing is None or c.order <= max_billing) and c.person not in seen:
                    seen.add(c.person)
                    members.append((c.person, c.order, c.role))
            if include_directors:
                for c in film.directors:
                    if c.person not in seen:
                        seen.add(c.person)
                        members.append((c.person, 0, "director"))
            if len(members) > 1:
                self.film_people[key] = members
                for pid, _, _ in members:
                    self.person_films[pid].append(key)

    # -- searching ---------------------------------------------------------------------------------------------
    def layers(self, source: str, stop_at: str | None = None, avoid: set[str] = frozenset(),
               max_depth: int | None = None) -> dict[str, int]:
        """Steps from source to everyone reachable (stopping once stop_at's layer is complete)."""
        dist = {source: 0}
        frontier = [source]
        done_films = set()
        depth = 0
        while frontier and (stop_at is None or stop_at not in dist) and (max_depth is None or depth < max_depth):
            nxt = []
            for chunk in batched(frontier, 2048):      # (a few milliseconds' searching between check points)
                check()
                for p in chunk:
                    for f in self.person_films.get(p, ()):
                        if f in done_films:
                            continue
                        done_films.add(f)
                        for q, _, _ in self.film_people[f]:
                            if q not in dist and q not in avoid:
                                dist[q] = depth + 1
                                nxt.append(q)
            frontier = nxt
            depth += 1
        return dist

    def connect(self, source: str, target: str, avoid: set[str] = frozenset()) -> dict | None:
        """The shortest chain from source to target - among equally short ones, the one through the biggest
        roles - and how many shortest chains there are. None if they aren't connected."""
        if source == target:
            return {"degrees": 0, "steps": [], "shortest_chains": 1}
        dist = self.layers(source, stop_at=target, avoid=avoid)
        if target not in dist:
            return None
        goal = dist[target]
        billing = lambda order: 2 if order == 0 else min(order, 30)
        cost, ways, back = {source: 0}, {source: 1}, {}
        by_layer = defaultdict(list)
        for p, d in dist.items():
            if d < goal:
                by_layer[d].append(p)
        for d in range(goal):
            for chunk in batched(by_layer[d], 256):
                check()
                for p in chunk:
                    for f in self.person_films.get(p, ()):
                        members = self.film_people[f]
                        here = next(order for pid, order, _ in members if pid == p)
                        for q, order, _ in members:
                            if dist.get(q) != d + 1 or (d + 1 == goal and q != target):
                                continue
                            c = cost[p] + billing(here) + billing(order)
                            ways[q] = ways.get(q, 0) + ways[p]
                            if c < cost.get(q, float("inf")):
                                cost[q], back[q] = c, (p, f)
        steps = []
        q = target
        while q != source:
            p, f = back[q]
            steps.append(self._step(p, q, f))
            q = p
        steps.reverse()
        return {"degrees": goal, "steps": steps, "shortest_chains": ways[target]}

    def _step(self, p: str, q: str, f: str) -> dict:
        film = self.catalog.films[f]
        roles = {pid: (order, role) for pid, order, role in self.film_people[f]}
        return {"from": self.name(p), "to": self.name(q), "film": _film(film),
                "from_role": _role(*roles[p]), "to_role": _role(*roles[q]), "from_id": p, "to_id": q}

    def name(self, pid: str) -> str:
        person = self.catalog.people.get(pid)
        return person.name if person else pid

    # -- summaries ---------------------------------------------------------------------------------------------
    def costars(self, pid: str) -> Counter:
        shared = Counter()
        for f in self.person_films.get(pid, ()):
            for q, _, _ in self.film_people[f]:
                if q != pid:
                    shared[q] += 1
        return shared

    def reach(self, pid: str, max_depth: int = 6) -> list[int]:
        """How many people are 1, 2, 3... steps away."""
        counts = Counter(self.layers(pid, max_depth=max_depth).values())
        return [counts[d] for d in range(1, max(counts) + 1)] if len(counts) > 1 else []

    def center(self, candidates: int = 60) -> list[dict]:
        """The best-connected people: lowest average number of steps to everyone they can reach."""
        pool = sorted(self.person_films, key=lambda p: (-len(self.person_films[p]), p))[:candidates]
        rows = []
        for p in pool:
            check()
            dist = self.layers(p)
            others = len(dist) - 1
            if not others:
                continue
            total = sum(dist.values())
            rows.append({"id": p, "name": self.name(p), "average_steps": round(total / others, 3), "reaches": others,
                         "furthest": max(dist.values()), "films": len(self.person_films[p])})
        # Everyone in your biggest connected group first (someone in a small island can't be the centre).
        biggest = max((r["reaches"] for r in rows), default=0)
        rows.sort(key=lambda r: (r["reaches"] != biggest, r["average_steps"], r["name"].casefold()))
        return rows

    def troupes(self, min_shared: int = 5, max_billing: int = 8, min_size: int = 3) -> list[dict]:
        """Groups of actors who keep appearing together - every pair shares at least min_shared films,
        counting only roles billed max_billing or higher."""
        pairs = Counter()
        films_of = defaultdict(set)
        for key, film in self.catalog.films.items():
            check()
            cast = sorted({c.person for c in film.cast if c.order <= max_billing})
            for i, a in enumerate(cast):
                films_of[a].add(key)
                for b in cast[i + 1:]:
                    pairs[(a, b)] += 1
        adj = defaultdict(set)
        for (a, b), n in pairs.items():
            if n >= min_shared:
                adj[a].add(b)
                adj[b].add(a)
        cliques = []
        _bron_kerbosch(set(), set(adj), set(), adj, cliques, min_size)
        out = []
        for members in cliques:
            check()
            together = set.intersection(*(films_of[p] for p in members))
            films_all = [self.catalog.films[k] for k in together]
            weakest = min(pairs[tuple(sorted((a, b)))] for a in members for b in members if a < b)
            ordered = sorted(members, key=lambda p: self.name(p).casefold())
            out.append({"members": [self.name(p) for p in ordered], "member_ids": ordered,
                        "size": len(members), "films_with_all_of_them": len(films_all),
                        "every_pair_shares_at_least": weakest,
                        "pairs": [{"a": a, "b": b, "shared_films": pairs[tuple(sorted((a, b)))]}
                                  for i, a in enumerate(ordered) for b in ordered[i + 1:]],
                        "examples": [_film(f) for f in sorted(films_all, key=lambda f: f.year or 0)[:6]]})
        # Groups that actually share the screen, all of them at once, come first.
        out.sort(key=lambda t: (t["films_with_all_of_them"] == 0, -t["size"], -t["films_with_all_of_them"],
                                t["members"]))
        return out


def _bron_kerbosch(r, p, x, adj, out, min_size):
    check()
    if not p and not x:
        if len(r) >= min_size:
            out.append(r)
        return
    pivot = max(p | x, key=lambda v: len(adj[v] & p))
    for v in list(p - adj[pivot]):
        _bron_kerbosch(r | {v}, p & adj[v], x & adj[v], adj, out, min_size)
        p = p - {v}
        x = x | {v}


def _film(film: Film) -> dict:
    # 'key' is the catalog's own film ID: two films can share a title and year (Dracula / Drácula, 1931)
    return {"title": film.title, "year": film.year, "plex_ids": film.plex_ids, "key": film.key}


def _role(order: int, role: str) -> str:
    if order == 0:
        return "director"
    return f"{role} (billed {order})" if role else f"billed {order}"


def graph_for(catalog: Catalog, max_billing: int | None = None, include_directors: bool = False) -> Graph:
    key = ("costars.graph", max_billing, include_directors)
    if key not in catalog.cache:
        catalog.cache[key] = Graph(catalog, max_billing, include_directors)
    return catalog.cache[key]


# -- telling people apart ------------------------------------------------------------------------------------
def mostly_directs(person: Person) -> bool:
    """True for someone who directed at least as many of your films as they act in (a director with dozens of
    films who acts in one): their co-stars are the casts of the films they directed, so those should count."""
    return bool(person.directed) and len(person.directed) >= len(person.acted)


def namesakes(catalog: Catalog) -> dict[str, list[Person]]:
    """Names two or more people share (John Smith the actor, and another John Smith who directs): the
    name -> everyone called that, the most films first. Worked out once per collection."""
    key = ("costars.namesakes",)
    if key not in catalog.cache:
        by_name = defaultdict(list)
        for p in catalog.people.values():
            if p.name:
                by_name[p.name].append(p)
        catalog.cache[key] = {name: sorted(ps, key=lambda p: (-p.film_count, p.id))
                              for name, ps in by_name.items() if len(ps) > 1}
    return catalog.cache[key]


def known_for(catalog: Catalog, person: Person) -> Film | None:
    """The film on your shelf someone is best known for: well rated, a big role (or one they directed), and one
    you've seen counts for a little more - what tells two people with the same name apart."""
    best, best_score = None, None
    for key in set(person.acted) | person.directed:
        film = catalog.films.get(key)
        if film is None:
            continue
        credit = person.acted.get(key)
        order = 0 if key in person.directed or credit is None else credit.order
        score = ((film.imdb_rating or 0) + (1.0 if order <= 3 else 0.4 if order <= 8 else 0)
                 + (0.5 if film.watched else 0))
        if best is None or score > best_score or (score == best_score and film.title < best.title):
            best, best_score = film, score
    return best


def short_title(title: str, most: int = 24) -> str:
    """'The Curious Case of Benjamin Button' -> 'The Curious Case of...' (a label has to fit a name box)."""
    if len(title) <= most:
        return title
    return (title[:most].rsplit(" ", 1)[0].rstrip(" :-,;") or title[:most]) + "..."


def person_label(catalog: Catalog, person: Person) -> str:
    """A name with enough to tell two people called that apart, the same in every tab: 'John Smith (10 films,
    e.g. Paper Harbour)', 'John Smith (1 film: Harbour Lights)' - the film is known_for()'s."""
    film = known_for(catalog, person)
    n = person.film_count
    if film is None:
        return f"{person.name} ({n:,} {'film' if n == 1 else 'films'})"
    title = short_title(film.title)
    return f"{person.name} (1 film: {title})" if n == 1 else f"{person.name} ({n:,} films, e.g. {title})"


def namesake_labels(catalog: Catalog) -> dict[str, str]:
    """person ID -> person_label() for everyone who shares their name, numbered ('... #2') in the rare case two
    labels come out the same. Worked out once per collection (a few hundredths of a second)."""
    key = ("costars.namesake_labels",)
    if key not in catalog.cache:
        labels, taken = {}, set()
        for same in namesakes(catalog).values():
            for p in same:                               # the most films first
                label = base = person_label(catalog, p)
                n = 1
                while label in taken:
                    n += 1
                    label = f"{base} #{n}"
                taken.add(label)
                labels[p.id] = label
        catalog.cache[key] = labels
    return catalog.cache[key]


GROUP_KEYS = ("library", "country", "genre", "decade")


def check_group(group) -> dict:
    """A part of the collection: {'library': ..., 'country': ..., 'genre': ..., 'decade': 1970} (any mix)."""
    if not isinstance(group, dict):
        raise ValueError("a group is an object like {\"library\": \"Documentaries\"} or {\"country\": \"France\"}")
    lowered = {str(k).lower(): v for k, v in group.items()}
    unknown = sorted(set(lowered) - set(GROUP_KEYS))
    if unknown:
        raise ValueError(f"unknown group key(s) {', '.join(unknown)} - use {', '.join(GROUP_KEYS)}")
    out = {k: v for k, v in lowered.items() if v not in (None, "")}
    if not out:
        raise ValueError(f"a group needs at least one of {', '.join(GROUP_KEYS)}")
    if "decade" in out:
        out["decade"] = int(out["decade"])
    return out


def _in_group(film: Film, group: dict) -> bool:
    ok = True
    if group.get("library"):
        ok &= fold(group["library"]) in {fold(v) for v in film.libraries}
    if group.get("country"):
        ok &= fold(group["country"]) in {fold(v) for v in film.countries}
    if group.get("genre"):
        ok &= fold(group["genre"]) in {fold(v) for v in film.genres}
    if group.get("decade") is not None:
        ok &= film.year is not None and film.year // 10 * 10 == group["decade"]
    return ok


def bridges(catalog: Catalog, a: dict, b: dict, count: int = 15, max_billing: int | None = None) -> dict:
    """People (and films) that link two parts of your collection, e.g. your French and US films.

    A film that belongs to both groups (a French-US co-production) links nothing, so it's left out: a
    bridge is someone with films on each side that are only on that side."""
    a, b = check_group(a), check_group(b)
    side = {}
    for key, film in catalog.films.items():
        check()
        in_a, in_b = _in_group(film, a), _in_group(film, b)
        side[key] = (in_a and not in_b, in_b and not in_a)
    both = sum(1 for key, film in catalog.films.items() if _in_group(film, a) and _in_group(film, b))
    people = []
    for n, person in enumerate(catalog.people.values()):
        if not n & 1023:
            check()
        films_a, films_b = [], []
        for key, credit in person.acted.items():
            if max_billing is not None and credit.order > max_billing:
                continue
            in_a, in_b = side[key]
            if in_a:
                films_a.append((credit.order, key))
            if in_b:
                films_b.append((credit.order, key))
        if films_a and films_b:
            people.append((min(len(films_a), len(films_b)), len(films_a) + len(films_b), person, films_a, films_b))
    people.sort(key=lambda t: (-t[0], -t[1], t[2].name))
    link_people = {t[2].id for t in people}
    films = []
    for key, (in_a, in_b) in side.items():
        if in_a or in_b:
            film = catalog.films[key]
            n = sum(1 for c in film.cast if c.person in link_people and (max_billing is None or c.order <= max_billing))
            if n:
                films.append((n, film))
    films.sort(key=lambda t: (-t[0], t[1].title))
    pick = lambda fs: [_film(catalog.films[k]) for _, k in sorted(fs)[:3]]
    return {
        "films_only_in_a": sum(1 for x, _ in side.values() if x),
        "films_only_in_b": sum(1 for _, y in side.values() if y),
        "films_in_both_left_out": both,
        "people_in_both": len(people),
        "people": [{"id": p.id, "name": p.name, "films_in_a": len(fa), "films_in_b": len(fb), "examples_a": pick(fa),
                    "examples_b": pick(fb)} for _, _, p, fa, fb in people[:count]],
        "films_with_the_most_linking_people": [dict(_film(f), linking_people=n) for n, f in films[:count]],
    }


def network(graph: Graph, pid: str, size: int = 12) -> dict:
    """Someone's circle: them, their `size` most frequent co-stars, and how often each pair shares a film -
    nodes and weighted edges, ready to draw."""
    top = [q for q, _ in graph.costars(pid).most_common(size)]
    members = [pid] + top
    films = {p: set(graph.person_films.get(p, ())) for p in members}
    edges = []
    for i, a in enumerate(members):
        for b in members[i + 1:]:
            n = len(films[a] & films[b])
            if n:
                edges.append({"a": a, "b": b, "shared_films": n})
    return {"nodes": [{"id": p, "name": graph.name(p), "films": len(films[p]), "center": p == pid} for p in members],
            "edges": edges}


def profile(catalog: Catalog, graph: Graph, person: Person, count: int = 15, circle: int = 12) -> dict:
    shared = graph.costars(person.id)
    acted = sorted(person.acted.items(), key=lambda kv: catalog.films[kv[0]].year or 0)
    return {
        "id": person.id, "name": person.name, "photo": person.photo,
        "films": len(set(person.acted) | person.directed),
        "acted_in": [dict(_film(catalog.films[k]), role=c.role, billed=c.order) for k, c in acted],
        "directed": [_film(catalog.films[k]) for k in sorted(person.directed,
                                                             key=lambda k: catalog.films[k].year or 0)],
        "distinct_costars": len(shared),
        "top_costars": [{"id": q, "name": graph.name(q), "shared_films": n} for q, n in shared.most_common(count)],
        "people_within_steps": graph.reach(person.id),
        "circle": network(graph, person.id, circle),
    }
