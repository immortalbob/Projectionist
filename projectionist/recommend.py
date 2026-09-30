"""What to watch next: your own star ratings, turned into a prediction for every film you haven't seen.

The model is a ridge regression (plain Python, no extra packages) over what's known about each film:
its directors and main cast, genres, countries, studio, decade, library, your own collections, and
its IMDb / Rotten Tomatoes scores. Trained on the films you've rated, it predicts how you'd rate the
rest, on your scale: 0-10 (5 stars = 10).

Your taste follows IMDb closely, so the scores do most of the predicting. What the people, genres and
studios add is your personal tilt: how far above or below its reputation you tend to rate a kind of
film. Each result splits its prediction three ways, all from the one model, so they add up (before the
prediction is held to 0.5-10): 'expected_from_scores' (the model's starting point plus what the IMDb/RT
scores add - what its reputation suggests for you), 'kind_of_film' (what its genres, decade, library,
year and length add) and 'personal_lift' (what the specific people, studios, countries and collections
add). sort="personal" ranks by the lift, among films predicted above what their scores suggest, to find
films you'll probably like more than their reputation suggests.

evaluate() measures all this honestly: it hides a fifth of your ratings at a time, predicts them from
the rest, and compares the error with simply guessing your average or going by IMDb alone.
"""

from __future__ import annotations

import math
import random
import re
from collections import defaultdict

from .catalog import Catalog, Film, account_ids, fold
from .jobs import check

# Regularisation strength. 32 did best in cross-validation on a real collection's few hundred ratings (8 to 64 were
# all within a whisker); 'evaluate' reports the best value for your own ratings.
DEFAULT_LAMBDA = 32.0
LAMBDAS = (8.0, 16.0, 32.0, 64.0)
CAST_DEPTH = 10              # billing positions that count; the rest are extras
MIN_TRAINING = 30            # fewer ratings than this and there's nothing to learn from
STOP_WORDS = set("a an and the of in on at to for with from by is are was his her their its into about who "
                 "what when where how this that film movie story".split())
SORTS = ("predicted", "personal")


# ---------------------------------------------------------------------------------------------------------
# Features
# ---------------------------------------------------------------------------------------------------------
def features(film: Film) -> dict[str, float]:
    """What the model knows about a film: 'kind|value' -> weight."""
    x: dict[str, float] = {}
    for c in film.directors[:3]:
        x[f"director|{c.person}"] = 1.0
    for c in film.cast:
        if c.order <= CAST_DEPTH:
            x[f"actor|{c.person}"] = 1.0 if c.order <= 3 else 0.6 if c.order <= 6 else 0.35
    for group, values in (("genre", film.genres), ("country", film.countries[:3])):
        for v in values:
            x[f"{group}|{v}"] = 1 / math.sqrt(len(values))
    if film.studio:
        x[f"studio|{film.studio}"] = 1.0
    if film.year:
        x[f"decade|{film.year // 10 * 10}"] = 1.0
    if film.libraries:
        x[f"library|{film.libraries[0]}"] = 1.0
    for c in film.collections:
        x[f"collection|{c}"] = 0.7
    x.update(_scores(film))
    return x


def _scores(film: Film) -> dict[str, float]:
    x = {}
    for name, value, centre, scale in (("imdb", film.imdb_rating, 6.5, 1.0), ("rt_critic", film.rt_critic, 60, 25),
                                       ("rt_audience", film.rt_audience, 60, 20)):
        if value is None:
            x[f"missing|{name}"] = 1.0
        else:
            x[f"score|{name}"] = (value - centre) / scale
    if film.year:
        x["score|year"] = (film.year - 1990) / 20
    if film.runtime_min:
        x["score|runtime"] = max(min((film.runtime_min - 105) / 30, 3.0), -3.0)
    return x


def _is_score(key: str) -> bool:
    return key.startswith(("score|", "missing|"))


# The specific things behind a 'personal lift' - people, studios, your collections, countries - as opposed to
# broad genre / decade / library tilts, which nudge every film of a kind by the same small amount.
SPECIFIC = ("director|", "actor|", "studio|", "collection|", "country|")
# A film's reputation: its IMDb and Rotten Tomatoes scores (or that it has none). Year and length are scores to
# the model, but they describe the kind of film, so they count with genres and decades when a prediction is split.
REPUTATION = ("score|imdb", "score|rt_critic", "score|rt_audience", "missing|")
# The taste profile's order: a tilt counts as tilt x films / (films + this), so a big tilt on a handful of films
# doesn't crowd out a steady one (the tilt shown is still the plain average).
STEADY_FILMS = 10


# ---------------------------------------------------------------------------------------------------------
# Ridge regression by coordinate descent (sparse, pure Python)
# ---------------------------------------------------------------------------------------------------------
class Model:
    def __init__(self, bias: float, weights: dict[str, float], lam: float):
        self.bias, self.weights, self.lam = bias, weights, lam

    def predict(self, x: dict[str, float]) -> float:
        return self.bias + sum(self.weights.get(k, 0.0) * v for k, v in x.items())


def fit(rows: list[dict[str, float]], y: list[float], lam: float = DEFAULT_LAMBDA, max_passes: int = 200,
        tol: float = 1e-5) -> Model:
    n = len(y)
    cols = defaultdict(list)
    for i, r in enumerate(rows):
        for k, v in r.items():
            if v:
                cols[k].append((i, v))
    sq = {k: sum(v * v for _, v in c) for k, c in cols.items()}
    bias = sum(y) / n
    resid = [yi - bias for yi in y]
    w: dict[str, float] = {}
    for _ in range(max_passes):
        check()                                    # (a pass takes a few milliseconds; see jobs.check)
        biggest = 0.0
        for k, c in cols.items():
            old = w.get(k, 0.0)
            new = (sum(v * resid[i] for i, v in c) + old * sq[k]) / (sq[k] + lam)
            step = new - old
            if step:
                for i, v in c:
                    resid[i] -= step * v
                w[k] = new
                biggest = max(biggest, abs(step))
        shift = sum(resid) / n                     # the intercept isn't penalised
        if shift:
            bias += shift
            resid = [r - shift for r in resid]
        if biggest < tol:
            break
    return Model(bias, w, lam)


class Recommender:
    """Everything fitted once per catalog: the full model, the scores-only model, and explanation stats."""

    def __init__(self, catalog: Catalog, lam: float = DEFAULT_LAMBDA):
        self.catalog = catalog
        self.rated = [f for f in catalog.films.values() if f.owner_rating is not None]
        if len(self.rated) < MIN_TRAINING:
            raise ValueError(f"only {len(self.rated)} rated films - rate at least {MIN_TRAINING} in Plex to get "
                             "predictions")
        y = [f.owner_rating for f in self.rated]
        self.rows = [features(f) for f in self.rated]
        self.model = fit(self.rows, y, lam)
        # What the scores alone predict for you (a light touch: there are only a handful of them).
        self.base = fit([_scores(f) for f in self.rated], y, 1.0)
        self.average = sum(y) / len(y)
        # How far above/below its scores you rate each kind of film, and how many of them you've rated.
        tilt, count, total = defaultdict(float), defaultdict(int), defaultdict(float)
        for film, row, rating in zip(self.rated, self.rows, y):
            residual = rating - self.base.predict(_scores(film))
            for k in row:
                if not _is_score(k):
                    tilt[k] += residual
                    count[k] += 1
                    total[k] += rating
        self.stats = {k: (count[k], tilt[k] / count[k]) for k in count}
        self.means = {k: total[k] / count[k] for k in count}          # your plain average for each kind
        # Rarer features count for more when judging likeness (sharing a director says more than sharing 'Drama').
        df = defaultdict(int)
        for film in catalog.films.values():
            check()
            for k in features(film):
                if not _is_score(k):
                    df[k] += 1
        total = len(catalog.films)
        self.idf = {k: math.log(total / d) for k, d in df.items()}
        self.rated_vectors = [(self.vector(r), f) for r, f in zip(self.rows, self.rated)]
        # The films you haven't rated, for each kind of film: [not played, played] - what the recommendations
        # can show for it (matched as their filters match: any genre, country, library or collection).
        unrated = defaultdict(lambda: [0, 0])
        folded = {}
        for film in catalog.films.values():
            check()
            if film.owner_rating is not None:
                continue
            keys = set()
            for kind, values in (("genre", film.genres), ("country", film.countries), ("library", film.libraries),
                                 ("collection", film.collections), ("studio", [film.studio] if film.studio else [])):
                for v in values:
                    if v not in folded:
                        folded[v] = fold(v)
                    keys.add((kind, folded[v]))
            if film.year:
                keys.add(("decade", str(film.year // 10 * 10)))
            for k in keys:
                unrated[k][1 if film.owner_plays else 0] += 1
        self._unrated = dict(unrated)

    def vector(self, x: dict[str, float]) -> dict[str, float]:
        return {k: v * self.idf.get(k, 0.0) for k, v in x.items() if not _is_score(k)}

    def predict(self, film: Film, x=None) -> tuple[float, float, float, float]:
        """(predicted rating, what its IMDb/RT scores suggest, what the kind of film adds - genres, decade,
        library, year and length - and the personal lift from specific people, studios, countries and
        collections). All four come from the one model: the last three add up to the prediction before it's
        held to 0.5-10."""
        x = x or features(film)
        reputation, kind, lift = self.model.bias, 0.0, 0.0
        for k, v in x.items():
            c = self.model.weights.get(k, 0.0) * v
            if k.startswith(REPUTATION):
                reputation += c
            elif k.startswith(SPECIFIC):
                lift += c
            else:
                kind += c
        return min(max(reputation + kind + lift, 0.5), 10.0), min(max(reputation, 0.5), 10.0), kind, lift

    def unrated_counts(self, key: str) -> tuple[int, int]:
        """How many films of a kind ('genre|Drama', 'actor|<id>'...) you haven't rated: (not played, played).
        A person's are the films they acted in or directed - what asking for them 'with' finds."""
        kind, _, value = key.partition("|")
        if kind in ("director", "actor"):
            person = self.catalog.people.get(value)
            if person is None:
                return 0, 0
            films = [self.catalog.films[k] for k in set(person.acted) | person.directed if k in self.catalog.films]
            unrated = [f for f in films if f.owner_rating is None]
            played = sum(1 for f in unrated if f.owner_plays)
            return len(unrated) - played, played
        counts = self._unrated.get((kind, fold(value)), (0, 0))
        return counts[0], counts[1]


def recommender(catalog: Catalog, lam: float = DEFAULT_LAMBDA) -> Recommender:
    key = ("recommend.recommender", lam)
    if key not in catalog.cache:
        catalog.cache[key] = Recommender(catalog, lam)
    return catalog.cache[key]


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if len(a) > len(b):
        a, b = b, a
    dot = sum(v * b.get(k, 0.0) for k, v in a.items())
    if not dot:
        return 0.0
    return dot / math.sqrt(sum(v * v for v in a.values()) * sum(v * v for v in b.values()))


# ---------------------------------------------------------------------------------------------------------
# Honest evaluation
# ---------------------------------------------------------------------------------------------------------
def number(request: dict, key: str, default=None, lo=None, hi=None, whole: bool = False):
    """A number from a request - finite, clamped to [lo, hi] - or the default when it's absent.
    Anything that isn't a number raises ValueError with the key's name."""
    value = request.get(key, default)
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        raise ValueError(f"{key} must be a number")
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{key} must be a number, not {value!r}") from None
    if not math.isfinite(value):
        raise ValueError(f"{key} must be a finite number")
    if lo is not None:
        value = max(value, lo)
    if hi is not None:
        value = min(value, hi)
    return int(round(value)) if whole else value


def check_lambda(value) -> float:
    lam = number({"lambda": value}, "lambda", DEFAULT_LAMBDA)
    if not 0.01 <= lam <= 1e6:
        raise ValueError("lambda must be between 0.01 and 1000000")
    return lam


def evaluate(catalog: Catalog, request: dict | None = None) -> dict:
    """Cross-validated accuracy on your own ratings, against simple baselines."""
    request = request or {}
    rated = [f for f in catalog.films.values() if f.owner_rating is not None]
    n = len(rated)
    if n < MIN_TRAINING:
        return {"ok": False, "action": "evaluate", "error": f"only {n} rated films - need at least {MIN_TRAINING}"}
    folds = number(request, "folds", 5, 2, min(20, n), whole=True)
    repeats = number(request, "repeats", 2, 1, 10, whole=True)
    raw = request.get("lambdas", LAMBDAS)
    raw = [raw] if isinstance(raw, (int, float, str)) else list(raw)
    if not 1 <= len(raw) <= 8:
        raise ValueError("lambdas: give between 1 and 8 values")
    lambdas = [check_lambda(v) for v in raw]
    y = [f.owner_rating for f in rated]
    rows = [features(f) for f in rated]
    score_rows = [_scores(f) for f in rated]
    preds = {lam: [0.0] * n for lam in lambdas}
    mean_pred, imdb_pred = [0.0] * n, [0.0] * n
    for rep in range(repeats):
        order = list(range(n))
        random.Random(1000 + rep).shuffle(order)
        for k in range(folds):
            test = set(order[k::folds])
            train = [i for i in range(n) if i not in test]
            ty = [y[i] for i in train]
            avg = sum(ty) / len(ty)
            base = fit([score_rows[i] for i in train], ty, 1.0)
            for i in test:
                mean_pred[i] += avg
                imdb_pred[i] += base.predict(score_rows[i])
            for lam in lambdas:
                model = fit([rows[i] for i in train], ty, lam)
                for i in test:
                    preds[lam][i] += model.predict(rows[i])

    def score(p):
        p = [v / repeats for v in p]
        err = [pi - yi for pi, yi in zip(p, y)]
        top = sorted(range(n), key=lambda i: -p[i])[: max(1, n // 5)]
        return {"mean_error": round(sum(abs(e) for e in err) / n, 2),
                "rms_error": round(math.sqrt(sum(e * e for e in err) / n), 2),
                "rank_agreement": round(_spearman(p, y), 3),
                "top_fifth_you_rated_8_plus": round(sum(1 for i in top if y[i] >= 8) / len(top), 3)}

    results = {f"{lam:g}": score(preds[lam]) for lam in lambdas}
    best = min(lambdas, key=lambda lam: results[f"{lam:g}"]["rms_error"])
    return {
        "ok": True, "action": "evaluate", "rated_films": n, "your_average": round(sum(y) / n, 2),
        "share_you_rated_8_plus": round(sum(1 for v in y if v >= 8) / n, 3),
        "method": f"{folds}-fold cross-validation, repeated {repeats} times: every rating is predicted by a model "
                  "that never saw it. mean_error is in rating points; rank_agreement is Spearman's (1 = perfect "
                  "order); top_fifth_you_rated_8_plus is how many of each method's top 20% you rated 8 or more.",
        "baselines": {"your average for everything": score(mean_pred),
                      "IMDb / Rotten Tomatoes scores only": score(imdb_pred)},
        "model_by_lambda": results, "best_lambda": best, "default_lambda": DEFAULT_LAMBDA,
    }


def _spearman(a, b):
    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2
            i = j + 1
        return r
    ra, rb = ranks(a), ranks(b)
    ma, mb = sum(ra) / len(ra), sum(rb) / len(rb)
    cov = sum((x - ma) * (z - mb) for x, z in zip(ra, rb))
    va = math.sqrt(sum((x - ma) ** 2 for x in ra))
    vb = math.sqrt(sum((z - mb) ** 2 for z in rb))
    return cov / (va * vb) if va and vb else 0.0


# ---------------------------------------------------------------------------------------------------------
# Recommending
# ---------------------------------------------------------------------------------------------------------
def recommend(catalog: Catalog, request: dict) -> dict:
    """Films you haven't seen, best first. See REQUEST_HELP for the options."""
    request = dict(request)
    lam = check_lambda(request.get("lambda", DEFAULT_LAMBDA))
    count = number(request, "count", 20, 1, 500, whole=True)
    for key in ("decade", "min_year", "max_year", "min_runtime", "max_runtime"):
        request[key] = number(request, key, None, -1e6, 1e6, whole=True)
    for key in ("min_imdb", "max_imdb"):
        request[key] = number(request, key, None)
    request["max_per_director"] = number(request, "max_per_director", 2, 0, 1000, whole=True)
    try:
        also = seen_by(request)
    except ValueError as exc:
        return {"ok": False, "action": "recommend", "error": str(exc)}
    try:
        rec = recommender(catalog, lam)
    except ValueError as exc:
        return {"ok": False, "action": "recommend", "error": str(exc)}
    sort = str(request.get("sort", "predicted")).lower()
    if sort not in SORTS:
        return {"ok": False, "action": "recommend", "error": f"sort must be one of {', '.join(SORTS)}"}
    notes = []

    seeds = []
    for q in _as_list(request.get("like_key")):         # exact films (two can share a title and year)
        film = catalog.films.get(q)
        if film is None:
            return {"ok": False, "action": "recommend", "error": f"no film with key '{q}'"}
        seeds.append(film)
    for q in _as_list(request.get("like")):
        film, others, how = catalog.find_film(q)
        if film is None:
            return {"ok": False, "action": "recommend", "error": f"no film matching '{q}'",
                    "suggestions": [f.label for f in others[:5]]}
        seeds.append(film)
        if how != "exact":
            notes.append(f"'{q}' taken as {film.label}")
    people = []
    for group in ("with", "directed_by"):
        for q in _as_list(request.get(group + "_id")):   # exact people (two can share a name)
            person = catalog.people.get(q)
            if person is None:
                return {"ok": False, "action": "recommend", "error": f"no one with id '{q}'"}
            people.append((group, person))
        for q in _as_list(request.get(group)):
            person, others, how = catalog.find_person(q)
            if person is None:
                return {"ok": False, "action": "recommend", "error": f"no one matching '{q}'",
                        "suggestions": [p.name for p in others[:5]]}
            people.append((group, person))
            if how != "exact":
                notes.append(f"'{q}' taken as {person.name}")
            same = [p for p in others if p.name == person.name]
            if same:
                notes.append(f"{len(same) + 1} people on your shelf are called {person.name}: this is the one with "
                             f"the most films ({person.film_count}); {group}_id picks another.")
    words = [w for w in fold(str(request.get("text", ""))).split() if len(w) > 2 and w not in STOP_WORDS]
    # Up to three words must all appear; with more, two in three will do.
    need = len(words) if len(words) <= 3 else math.ceil(len(words) * 2 / 3)

    include_watched = bool(request.get("include_watched"))
    seed_vectors = [rec.vector(features(f)) for f in seeds]
    seed_keys = {f.key for f in seeds}
    candidates = []
    played_matches = 0                   # films left out only because you've played them (or also_seen_by has)
    left_out = 0                         # ...only because of exclude_libraries / exclude_genres
    for film in catalog.films.values():
        check()
        if film.owner_rating is not None or film.key in seed_keys:
            continue
        if not _passes(film, request, people, leave_out=False):
            continue
        matched = []
        if words:
            haystack = fold(" ".join([film.title, film.tagline, film.summary] + film.genres))
            matched = [w for w in words if re.search(rf"\b{re.escape(w)}", haystack)]
            if len(matched) < need:
                continue
        x = features(film)
        predicted, expected, kind, lift = rec.predict(film, x)
        # Personal picks: films you'd rate above their reputation because of their people, studios... - by at
        # least 0.05, and above it as shown too (the prediction to one decimal, the scores' part to two: 7.65
        # shows as 7.6, level with 7.60) - and at least your average: not a tilt on a film you still wouldn't like.
        if sort == "personal" and (round(lift, 2) <= 0 or predicted < rec.average or predicted - expected < 0.05
                                   or round(predicted, 1) <= round(expected, 2)):
            continue
        likeness = None
        if seeds:
            likeness = max(_cosine(rec.vector(x), s) for s in seed_vectors)
            if likeness < 0.1:
                continue                 # nothing in common with the films asked for
        played = film.seen(also)         # (not rated: that was skipped above)
        if _left_out(film, request):
            if include_watched or not played:
                left_out += 1            # (a result but for that)
            continue
        if played and not include_watched:
            played_matches += 1
            continue
        score = lift * 10 if sort == "personal" else predicted
        candidates.append([len(matched), score, predicted, expected, kind, lift, likeness, matched, film, x])
    if seeds and candidates:
        # Likeness first; the rating (or lift) breaks near-ties. Likeness is taken relative to the closest film
        # here, so it leads however alike these films are (they're often all between 0.1 and 0.2).
        closest = max(c[6] for c in candidates)
        for c in candidates:
            c[1] = 0.7 * 10 * c[6] / closest + 0.3 * c[1]
    candidates.sort(key=lambda c: (-c[0], -c[1], c[8].title))

    per_director = request["max_per_director"]
    used = defaultdict(int)
    results = []
    for _, _score, predicted, expected, kind, lift, likeness, matched, film, x in candidates:
        check()
        if per_director > 0 and any(used[d.person] >= per_director for d in film.directors):
            continue
        for d in film.directors:
            used[d.person] += 1
        results.append(_result(rec, film, x, predicted, expected, kind, lift, likeness, matched))
        if len(results) >= count:
            break
    if sort == "personal":
        notes.append("Sorted by personal lift: how much the specific people, studios, countries and collections "
                     "you rate differently add to the prediction. Only films predicted above what their IMDb/RT "
                     "scores suggest, and at or above your average.")
    if seeds:
        notes.append("Ranked mostly on likeness to " + ", ".join(f.label for f in seeds) + " (shared people, "
                     "studio, genres...), then on " + ("personal lift" if sort == "personal" else
                                                        "your predicted rating") + ".")
    if words:
        notes.append("Films matching more of the words come first.")
    if per_director > 0:
        notes.append(f"At most {per_director} films per director (max_per_director 0 = no limit).")
    notes.append(UNSEEN_NOTE + (ALSO_SEEN_NOTE if also else ""))
    answer = {
        "ok": True, "action": "recommend", "sort": sort, "results": results, "matching_films": len(candidates),
        "model": {"trained_on": len(rec.rated), "your_average": round(rec.average, 2), "lambda": rec.model.lam,
                  "scale": "your ratings, 0-10 (5 stars = 10)"},
        "notes": notes,
    }
    if not include_watched:
        answer["played_matches"] = played_matches      # how many more include_watched would add
    if leaves_out(request):
        answer["left_out"] = left_out                  # ...and how many more leaving nothing out would
    return answer


UNSEEN_NOTE = ("'Haven't seen' means you haven't played or rated it in Plex - classics you saw years ago still count "
               "as unseen.")
ALSO_SEEN_NOTE = " Films played on the other accounts asked for (also_seen_by) count as seen too."


def seen_by(request: dict) -> list[int]:
    """The other accounts whose plays count as the owner's (also_seen_by: a list of account ids, as the 'info'
    action lists them; Settings > Your collection) - ValueError for a bad one."""
    try:
        return account_ids(request.get("also_seen_by"))
    except ValueError as exc:
        raise ValueError(f"also_seen_by: {exc}") from None


def _as_list(value) -> list[str]:
    if value is None or value == "":
        return []
    return [value] if isinstance(value, str) else [str(v) for v in value]


def leaves_out(request: dict) -> bool:
    """The request leaves out some libraries or genres (exclude_libraries / exclude_genres)."""
    return bool(_as_list(request.get("exclude_libraries")) or _as_list(request.get("exclude_genres")))


def _left_out(film: Film, request: dict) -> bool:
    """In a library or of a genre the request leaves out (exclude_libraries / exclude_genres - Watch Next's
    'Never suggest films from' setting). A film in two libraries is left out if either is."""
    folded = lambda values: {fold(v) for v in values}
    if folded(_as_list(request.get("exclude_libraries"))) & folded(film.libraries):
        return True
    return bool(folded(_as_list(request.get("exclude_genres"))) & folded(film.genres))


def _passes(film: Film, request: dict, people, leave_out: bool = True) -> bool:
    """The film passes the request's filters. leave_out=False skips exclude_libraries / exclude_genres, for a
    caller that checks them later itself, to count the films they leave out (_left_out)."""
    if leave_out and _left_out(film, request):
        return False
    folded = lambda values: {fold(v) for v in values}
    libs = folded(_as_list(request.get("libraries") or request.get("library")))
    if libs and not libs & folded(film.libraries):
        return False
    genres = folded(_as_list(request.get("genres") or request.get("genre")))
    if genres and not genres & folded(film.genres):
        return False
    colls = folded(_as_list(request.get("collections") or request.get("collection")))
    if colls and not colls & folded(film.collections):
        return False
    if folded(_as_list(request.get("exclude_collections"))) & folded(film.collections):
        return False
    countries = folded(_as_list(request.get("countries") or request.get("country")))
    if countries and not countries & folded(film.countries):
        return False
    year = film.year
    if request.get("decade") is not None and (year is None or year // 10 * 10 != int(request["decade"])):
        return False
    if request.get("min_year") is not None and (year is None or year < int(request["min_year"])):
        return False
    if request.get("max_year") is not None and (year is None or year > int(request["max_year"])):
        return False
    runtime = film.runtime_min
    if request.get("max_runtime") is not None and (runtime is None or runtime > int(request["max_runtime"])):
        return False
    if request.get("min_runtime") is not None and (runtime is None or runtime < int(request["min_runtime"])):
        return False
    if request.get("min_imdb") is not None and (film.imdb_rating or 0) < float(request["min_imdb"]):
        return False
    if request.get("max_imdb") is not None and (film.imdb_rating is None or
                                                film.imdb_rating > float(request["max_imdb"])):
        return False
    if request.get("resolution") and fold(film.resolution) != fold(str(request["resolution"])):
        return False
    for group, person in people:
        if group == "with" and film.key not in person.acted and film.key not in person.directed:
            return False
        if group == "directed_by" and film.key not in person.directed:
            return False
    return True


def _describe(rec: Recommender, key: str, film: Film) -> str:
    kind, _, value = key.partition("|")
    if kind == "score":
        return {"imdb": f"IMDb {film.imdb_rating:g}" if film.imdb_rating is not None else "IMDb score",
                "rt_critic": f"Rotten Tomatoes critics {film.rt_critic}%",
                "rt_audience": f"Rotten Tomatoes audience {film.rt_audience}%",
                "year": f"Made in {film.year}", "runtime": f"Runs {film.runtime_min} min"}.get(value, value)
    if kind == "director":
        label = "Directed by " + next((c.name for c in film.directors if c.person == value), value)
    elif kind == "actor":
        label = "With " + next((c.name for c in film.cast if c.person == value), value)
    else:
        label = {"genre": f"{value}", "country": f"From {value}", "studio": f"{value}", "decade": f"The {value}s",
                 "library": f"Your {value} library", "collection": f"Your '{value}' collection"}.get(kind, value)
    n, tilt = rec.stats.get(key, (0, 0.0))
    if n >= 2 and abs(tilt) >= 0.05:
        direction = "above" if tilt > 0 else "below"
        return f"{label} - you rate these {abs(tilt):.1f} {direction} what their scores suggest ({n} films)"
    return label + (f" - you've rated {n} of these" if n else "")


def short_label(key: str, film: Film | None = None, people: dict | None = None) -> str:
    """A feature as a few words: 'IMDb 8.8', 'Quentin Tarantino (director)', 'Drama', 'The 1990s'."""
    kind, _, value = key.partition("|")
    if kind == "score" and film is not None:
        return {"imdb": f"IMDb {film.imdb_rating:g}" if film.imdb_rating is not None else "IMDb score",
                "rt_critic": f"Critics {film.rt_critic}%", "rt_audience": f"Audience {film.rt_audience}%",
                "year": f"Made in {film.year}", "runtime": f"{film.runtime_min} min long"}.get(value, value)
    if kind in ("director", "actor"):
        name = None
        if film is not None:
            name = next((c.name for c in (film.directors if kind == "director" else film.cast)
                         if c.person == value), None)
        if name is None and people is not None and value in people:
            name = people[value].name
        name = name or value
        return f"{name} (director)" if kind == "director" else name
    return {"country": f"{value}", "decade": f"The {value}s", "library": f"{value} library",
            "collection": f"'{value}' collection"}.get(kind, value)


def _breakdown(rec: Recommender, film: Film, x: dict, parts: list, predicted: float, shown: int = 7) -> dict:
    """How the prediction adds up - for a waterfall chart: start from the model's base, add each of the biggest
    pushes up or down, and the rest in one 'everything else' step."""
    biggest = sorted(parts, key=lambda p: -abs(p[0]))[:shown]
    steps = [{"label": short_label(k, film), "kind": k.partition("|")[0], "value": round(c, 3)}
             for c, k in sorted(biggest, key=lambda p: -p[0]) if abs(c) >= 0.005]
    rest = rec.model.predict(x) - rec.model.bias - sum(s["value"] for s in steps)
    return {"base": round(rec.model.bias, 3), "steps": steps, "everything_else": round(rest, 3),
            "total_before_limits": round(rec.model.predict(x), 3), "predicted": round(predicted, 1)}


TASTE_KINDS = {"genre": 5, "decade": 5, "studio": 4, "country": 4, "library": 5, "collection": 4,
               "director": 3, "actor": 3}


def taste_profile(catalog: Catalog, request: dict | None = None) -> dict:
    """Where your ratings part company with the scores: for each kind of film (genre, decade, studio, director...)
    how far above or below its IMDb/RT scores you tend to rate it."""
    request = dict(request or {})
    lam = check_lambda(request.get("lambda", DEFAULT_LAMBDA))
    count = number(request, "count", 8, 1, 100, whole=True)
    kinds = _as_list(request.get("kinds")) or list(TASTE_KINDS)
    unknown = [k for k in kinds if k not in TASTE_KINDS]
    if unknown:
        raise ValueError(f"unknown kind(s) {', '.join(unknown)} - use {', '.join(TASTE_KINDS)}")
    try:
        rec = recommender(catalog, lam)
    except ValueError as exc:
        return {"ok": False, "action": "taste", "error": str(exc)}
    groups = {}
    for kind in kinds:
        check()
        least = number(request, "min_films", TASTE_KINDS[kind], 1, 1000, whole=True)
        rows = []
        for k, (n, t) in rec.stats.items():
            if not k.startswith(kind + "|") or n < least:
                continue
            unseen, played = rec.unrated_counts(k)
            row = {"label": short_label(k, people=catalog.people), "tilt": round(t, 2), "films": n,
                   "your_average": round(rec.means[k], 2), "unseen": unseen, "played_not_rated": played}
            if kind in ("director", "actor"):
                row["id"] = k.partition("|")[2]             # the exact person (two can share a name)
            rows.append((t * n / (n + STEADY_FILMS), row))
        rows.sort(key=lambda r: (-r[0], r[1]["label"]))
        groups[kind] = {"above": [r for _s, r in rows if r["tilt"] > 0][:count],
                        "below": [r for _s, r in reversed(rows) if r["tilt"] < 0][:count],
                        "qualifying": len(rows), "min_films": least}
    return {"ok": True, "action": "taste", "your_average": round(rec.average, 2), "rated_films": len(rec.rated),
            "groups": groups,
            "note": "tilt = how far above (+) or below (-) what their IMDb/Rotten Tomatoes scores suggest you rate "
                    "these films, on your 0-10 scale. above/below put the steadiest tilts first: a tilt counts as "
                    f"tilt x films / (films + {STEADY_FILMS}), so a big tilt on a handful of films doesn't crowd out "
                    "one seen across many. unseen / played_not_rated = films of that kind you haven't rated, and "
                    "haven't / have played."}


def _telling(rec: Recommender, key: str, contribution: float) -> bool:
    """Worth giving as a reason: a score, or a kind of film whose own track record points the same way."""
    if key.startswith("score|"):
        return True
    n, tilt = rec.stats.get(key, (0, 0.0))
    return n >= 2 and abs(tilt) >= 0.05 and (tilt > 0) == (contribution > 0)


def _result(rec: Recommender, film, x, predicted, expected, kind, lift, likeness, matched) -> dict:
    parts = sorted(((rec.model.weights.get(k, 0.0) * v, k) for k, v in x.items() if not k.startswith("missing|")),
                   reverse=True)
    # Up to two score-based reasons, then the specific ones (people, studio...) that make it yours.
    up = [(c, k) for c, k in parts if c > 0.03 and _telling(rec, k, c)]
    reasons = [_describe(rec, k, film) for c, k in up if k.startswith("score|")][:2] + \
              [_describe(rec, k, film) for c, k in up if not k.startswith("score|")][:3]
    against = [_describe(rec, k, film) for c, k in reversed(parts) if c < -0.03 and _telling(rec, k, c)][:2]
    vec = rec.vector(x)
    close = sorted(((_cosine(vec, rv), f) for rv, f in rec.rated_vectors), key=lambda t: -t[0])[:3]
    known = [c for c in film.directors[:2] + [c for c in film.cast if c.order <= 6]
             if rec.stats.get(("director|" if c in film.directors else "actor|") + c.person, (0, 0))[0] >= 2]
    confidence = "high" if len(known) >= 2 else "medium" if known or (close and close[0][0] >= 0.3) else "low"
    out = film.brief()
    out.update(
        key=film.key, predicted_rating=round(predicted, 1), expected_from_scores=round(expected, 2),
        kind_of_film=round(kind, 2), personal_lift=round(lift, 2), confidence=confidence, reasons=reasons,
        against=against,
        breakdown=_breakdown(rec, film, x, parts, predicted),
        people=[{"id": c.person, "name": c.name, "role": "director"} for c in film.directors] +
               [{"id": c.person, "name": c.name, "role": c.role or "actor"} for c in film.cast[:6]],
        similar_films_you_rated=[{"title": f.title, "year": f.year, "your_rating": f.owner_rating, "key": f.key}
                                 for s, f in close if s >= 0.1],
        genres=film.genres, runtime_min=film.runtime_min, imdb_rating=film.imdb_rating, rt_critic=film.rt_critic,
        directors=[c.name for c in film.directors], cast=[c.name for c in film.cast[:4]],
        resolution=film.resolution, summary=film.summary)
    if likeness is not None:
        out["likeness"] = round(likeness, 2)
    if matched:
        out["matched_words"] = matched
    return out


REQUEST_HELP = {
    "count": "how many films (default 20)",
    "sort": "'predicted' (default: highest predicted rating) or 'personal' (films your taste rates above their "
            "reputation, by what their people, studios, countries and collections add)",
    "libraries, genres, exclude_libraries, exclude_genres, collections, exclude_collections, countries":
        "a name or a list of names. Films in a library or of a genre excluded never come up; the answer's left_out "
        "says how many more would have",
    "decade, min_year, max_year, min_runtime, max_runtime, min_imdb, max_imdb, resolution": "limits",
    "with, directed_by": "people who must be in it / must have directed it",
    "with_id, directed_by_id": "the same by person id (as 'person' answers and results' people give them), for "
                               "people who share a name",
    "like": "a title or list of titles: rank by likeness to these as well",
    "like_key": "the same by film key (each result's 'key'), for films that share a title and year",
    "text": "words to look for in the title, tagline, summary and genres",
    "include_watched": "true to include films you've played but not rated",
    "also_seen_by": "a list of the server's other accounts (their ids, as 'info' lists them): films played on them "
                    "count as seen too, so they're left out unless include_watched",
    "max_per_director": "variety: at most this many per director (default 2, 0 = no limit)",
}
