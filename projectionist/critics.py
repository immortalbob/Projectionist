"""Your critics: the Rotten Tomatoes critics whose Fresh/Rotten verdicts agree with your own ratings.

Plex keeps up to 20 critic reviews per film (the critic, Fresh or Rotten, a quote and the publication) - a
collection of a few thousand films has a few thousand critics. This module sets them against the server owner's
own ratings:

  liked         you liked a film if you rated it at or above the middle of your own ratings (your median:
                7 out of 10, say)
  agreement     a critic agrees with you on a film when they called it Fresh and you liked it, or Rotten and you
                didn't
  chance        what someone handing out Fresh at random - as often as that critic really does - would score
                against you on the same films. A critic who calls everything Fresh agrees with anyone who likes
                most films, which says nothing, so every comparison allows for it
  match         how much more (+) or less (-) often than a typical critic they agree with you, allowing for their
                chance level, and pulled toward 'typical' when you share few films: their own record counts as
                much as a typical critic's once you share PRIOR_FILMS films. The ranking key, in points
  stand_out     is the spread between critics more than luck would make? (A chi-squared test - a cautious one,
                erring toward 'luck': see _stand_out.) On a few hundred ratings it usually isn't, so 'closest'
                means 'closest so far' - and the answers say so
  anti_twin     a listed critic who agrees with you less often than a coin would, by more than luck explains
                with every listed critic tested at once (1 in 20 over all of them): 'reliably disagrees'
  picks         films you haven't seen that more of your closest critics called Fresh than Rotten
  evaluate      an honest test: every rated film judged by the closest critics picked without it, against the
                Tomatometer, IMDb and Watch Next's own model; and whether adding the critics to Watch Next's model
                makes it measurably better. In testing it didn't, so Watch Next doesn't use them: the picks are
                a different way in, not a better guess

The catalog doesn't load the reviews (catalog.TAG_TYPES leaves them out, to keep the collection's load quick).
They are read here from catalog.source - read-only, like everything else - once per catalog, and the model is kept
in catalog.cache ('critics.raw', 'critics.model'). The first call takes about half a second on tens of thousands
of reviews; ready(catalog) says whether that's done, so a window can call the rest on its own thread (each a
millisecond or so). Catalogs that don't come from a database (tests) are given their reviews with use_reviews().

Critic ids are Plex's tag ids, as text: stable within one database, not across servers.
"""

from __future__ import annotations

import copy
import difflib
import heapq
import math
import os
import random
import re
import sqlite3
import statistics
import threading
import time
from collections import Counter, defaultdict

from . import jobs
from . import recommend as R
from .catalog import Catalog, Film, _best, _keys, fold
from .extract import TAG_REVIEW, PlexDatabase, PlexDBError, clean, parse_extra, rating_source

MIN_SHARED = 5            # films you've both judged before a critic is listed
PRIOR_FILMS = 20          # a critic's own record weighs as much as a typical critic's at this many shared films
GENEROSITY_PRIOR = 5      # a critic's Fresh share is pulled toward everyone's by this many reviews' worth
CLOSEST = 25              # 'your closest critics': their picks, and the Film page's verdicts
TEST_MIN_SHARED = 10      # 'does anyone stand out?' tests the critics sharing this many films
MIN_RATED = 30            # rated films with reviews needed to compare at all
QUOTE_CHARS = 300
CANCEL_STEPS = 50_000     # SQLite asks whether the job was called off every this many steps (as catalog.load)
TOP_AT = 8.0              # 'top picks you rated 8+', as recommend.evaluate counts them
FOLDS, REPEATS = 5, 2     # Watch Next's accuracy test (recommend.evaluate's defaults: the same folds)
HELPS_RMS, HELPS_RANK = 0.02, 0.01   # what 'measurably better' takes: both at once
ANTI_TWIN_P = 0.05        # 'reliably disagrees': this likely by luck across all the listed critics together
RANDOM_PANELS = 100       # the critics test's like-for-like yardstick: this many panels of critics picked at random
VIEWS = ("overview", "critic", "film", "picks", "evaluate", "names")

RAW, MODEL, ERROR = "critics.raw", "critics.model", "critics.error"
_LOCK = threading.Lock()            # one thread builds a catalog's model; the others wait for it

NO_REVIEWS = ("no critic reviews stored in this database - Plex keeps them for films its movie agent matched")
NOT_IN_WATCH_NEXT = "Watch Next's predictions don't use these critics."     # (a picks note; the tab's hint says it)


# ---------------------------------------------------------------------------------------------------------
# The data
# ---------------------------------------------------------------------------------------------------------
class Review:
    __slots__ = ("critic", "film", "fresh", "quote", "publication", "link", "order")

    def __init__(self, critic, film, fresh, quote="", publication="", link="", order=0):
        self.critic, self.film, self.fresh = critic, film, fresh
        self.quote, self.publication, self.link, self.order = quote, publication, link, order

    @property
    def verdict(self) -> str:
        return "Fresh" if self.fresh else "Rotten"


class Critic:
    __slots__ = ("id", "name", "publications", "reviews", "fresh")

    def __init__(self, cid: str, name: str):
        self.id, self.name = cid, name
        self.publications: Counter = Counter()
        self.reviews = 0
        self.fresh = 0

    @property
    def publication(self) -> str:
        """The publication they wrote for most often here (hundreds of critics write for more than one)."""
        return self.publications.most_common(1)[0][0] if self.publications else ""


class Stat:
    """One critic against your ratings: the films you've both judged, and what that comes to."""
    __slots__ = ("films", "shared", "agreed", "liked", "chance", "prior", "score", "match")

    def __init__(self):
        self.films = []             # [(film key, fresh, your rating, agreed)]
        self.shared = self.agreed = self.liked = 0
        self.chance = self.prior = self.score = self.match = 0.0


def _quote(text) -> str:
    text = clean(text)
    if len(text) > QUOTE_CHARS:
        text = text[:QUOTE_CHARS].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return text


def use_reviews(catalog: Catalog, rows) -> None:
    """Give a catalog its reviews directly - for tests, and for catalogs not read from a database. rows are dicts:
    {critic (name), film_key, verdict 'Fresh'|'Rotten', critic_id?, quote?, publication?, link?, order?}. A critic
    without an id gets one from their name. Rows for films the catalog doesn't have, or without a Fresh/Rotten
    verdict, are skipped; a critic's second review of the same film too. Resets the model."""
    ids, out, seen = {}, [], set()
    for n, r in enumerate(rows):
        name = clean(r.get("critic"))
        key = str(r.get("film_key") or "")
        verdict = clean(r.get("verdict")).casefold()
        if not name or key not in catalog.films or verdict not in ("fresh", "rotten"):
            continue
        cid = clean(r.get("critic_id")) or ids.setdefault(name, f"c{len(ids) + 1}")
        if (cid, key) in seen:
            continue
        seen.add((cid, key))
        order = r.get("order")
        out.append((cid, name, key, verdict == "fresh", _quote(r.get("quote")), clean(r.get("publication")),
                    clean(r.get("link")), int(order) if isinstance(order, (int, float)) else n))
    with _LOCK:
        catalog.cache[RAW] = out
        catalog.cache.pop(MODEL, None)
        catalog.cache.pop(ERROR, None)


def _read(catalog: Catalog) -> list[tuple]:
    """[(critic id, name, film key, fresh, quote, publication, link, order)] for the catalog's films - one per
    critic and film (a film owned twice carries the same reviews on each copy; the first copy's is kept). A
    catalog read with a library choice only gets its own libraries' reviews. (Called holding _LOCK.)"""
    raw = catalog.cache.get(RAW)
    if raw is not None:
        return raw
    source = str(catalog.source or "")
    if not os.path.isfile(source):                  # a catalog built in memory, or a file that's gone
        if source and source != "memory":
            catalog.cache[ERROR] = f"the database file isn't there any more ({source})"
        catalog.cache[RAW] = []
        return []
    by_pid = {pid: f.key for f in catalog.films.values() for pid in f.plex_ids}
    try:
        with PlexDatabase(source) as db:
            job = jobs.current()
            if job is not None and db.con is not None:
                db.con.set_progress_handler(lambda: job.cancelled, CANCEL_STEPS)
            have = db.columns("taggings")
            text = "tg.text" if "text" in have else "NULL"
            extra = "tg.extra_data" if "extra_data" in have else "NULL"
            index = 'tg."index"' if "index" in have else "0"
            try:
                rows = db.query(f"""SELECT tg.metadata_item_id AS mid, t.id AS tid, t.tag AS name, {index} AS idx,
                                           {text} AS text, {extra} AS extra
                                      FROM taggings tg JOIN tags t ON t.id = tg.tag_id
                                     WHERE t.tag_type = {TAG_REVIEW}""").fetchall()
            except sqlite3.DatabaseError:
                jobs.check()                        # (a query interrupted because the job was called off)
                raise
    except (PlexDBError, OSError, sqlite3.DatabaseError) as exc:
        catalog.cache[ERROR] = str(exc).replace("\n\n", " ").replace("\n", " ")
        catalog.cache[RAW] = []
        return []
    out, seen = [], {}
    for n, r in enumerate(rows):
        if n % 5000 == 0:
            jobs.check()
        key = by_pid.get(r["mid"])
        if key is None:
            continue
        name = clean(r["name"])
        if not name:
            continue
        info = parse_extra(r["extra"])
        verdict = rating_source(str(info.get("at:image") or ""))[1]
        if verdict not in ("Fresh", "Rotten"):
            continue
        row = [str(r["tid"]), name, key, verdict == "Fresh", _quote(r["text"]), clean(info.get("at:source")),
               clean(info.get("at:link")), r["idx"] if isinstance(r["idx"], int) else 0]
        kept = seen.get((r["tid"], key))
        if kept is not None:             # another copy of the same film: one review - its blanks filled from this
            for i in (4, 5, 6):
                if not kept[i] and row[i]:
                    kept[i] = row[i]
            continue
        seen[(r["tid"], key)] = row
        out.append(row)
    out = [tuple(row) for row in out]
    catalog.cache[RAW] = out
    return out


# ---------------------------------------------------------------------------------------------------------
# The model: every critic against your ratings
# ---------------------------------------------------------------------------------------------------------
def _liked_at(ratings) -> float | None:
    """Where 'liked' starts: your median rating - or, if that's your lowest (most films rated the same low
    score), the next rating up, so there's something on each side. None when every rating is the same."""
    values = sorted(ratings)
    if len(set(values)) < 2:
        return None
    at = statistics.median(values)
    if at <= values[0]:
        at = min(v for v in values if v > values[0])
    return float(at)


def liked_words(at: float | None) -> str:
    """'7 or more out of 10 (3½ stars)'."""
    if at is None:
        return ""
    stars = at / 2
    if stars * 2 == int(stars * 2):
        whole = int(stars)
        star_text = (str(whole) if whole else "") + ("½" if stars - whole else "") or "0"
    else:
        star_text = f"{stars:.1f}"
    return f"{at:g} or more out of 10 ({star_text} stars)"


class Critics:
    """Every critic's reviews and, when there are enough ratings to compare, their standing with you."""

    def __init__(self, catalog: Catalog, raw, error: str = ""):
        self.catalog = catalog
        self.error = error
        self.critics: dict[str, Critic] = {}
        self.by_film: dict[str, list[Review]] = defaultdict(list)
        self.by_critic: dict[str, list[Review]] = defaultdict(list)
        for n, (cid, name, key, fresh, quote, pub, link, order) in enumerate(raw):
            if n % 5000 == 0:
                jobs.check()
            c = self.critics.get(cid)
            if c is None:
                c = self.critics[cid] = Critic(cid, name)
            c.reviews += 1
            c.fresh += fresh
            if pub:
                c.publications[pub] += 1
            rv = Review(cid, key, fresh, quote, pub, link, order)
            self.by_film[key].append(rv)
            self.by_critic[cid].append(rv)
        for rvs in self.by_film.values():
            rvs.sort(key=lambda rv: rv.order)            # Plex's own order
        self.reviews = sum(c.reviews for c in self.critics.values())
        fresh = sum(c.fresh for c in self.critics.values())
        self.overall_fresh = fresh / self.reviews if self.reviews else 0.6
        # How often each critic says Fresh, pulled toward everyone's share (a critic with 2 reviews says little)
        self.generosity = {cid: (c.fresh + GENEROSITY_PRIOR * self.overall_fresh) / (c.reviews + GENEROSITY_PRIOR)
                           for cid, c in self.critics.items()}
        self.predicted: dict[str, float | None] = {}     # Watch Next's prediction per film, as asked for
        self._names = None
        # The owner's ratings, in the catalog's order (recommend.evaluate's folds follow it)
        rated = {f.key: f.owner_rating for f in catalog.films.values() if f.owner_rating is not None}
        self._fit(rated, _liked_at(rated.values()))

    # -- your standing with each critic ----------------------------------------------------------------------
    def _fit(self, rated: dict, liked_at: float | None):
        self.rated = rated
        self.liked_at = liked_at
        self.rated_with_reviews = sum(1 for k in rated if self.by_film.get(k))
        self.stats: dict[str, Stat] = {}
        self.typical = self.chance = self.lift = None
        self.when_fresh = self.when_rotten = None
        self.fresh_pairs = self.rotten_pairs = 0
        self.pairs = 0
        self.agreed_total = self.chance_total = 0.0
        self.problem = None
        if not self.critics:
            self.problem = "no_reviews"
        elif self.rated_with_reviews < MIN_RATED:
            self.problem = "few_ratings"
        elif liked_at is None:
            self.problem = "flat_ratings"
        else:
            self._agreement()
        self.ranked = self._rank(MIN_SHARED)
        # 'reliably disagrees': one-sided, 1 in 20 across every listed critic at once (Bonferroni) - z below
        # -1.64 for one critic, about -3.5 for 241
        self.anti_cut = statistics.NormalDist().inv_cdf(ANTI_TWIN_P / max(len(self.ranked), 1))
        self.rank_of = {cid: n + 1 for n, cid in enumerate(self.ranked)}
        self.closest = [cid for cid in self.ranked[:CLOSEST] if self.stats[cid].match > 0]
        self.closest_rank = {cid: n + 1 for n, cid in enumerate(self.closest)}
        self.listed = [(cid, self.stats[cid]) for cid in self.ranked]     # for the leave-one-out test
        self._stand_out = None

    def liked(self, rating: float) -> bool:
        return rating >= self.liked_at

    def _agreement(self):
        stats = self.stats
        fresh_sum = rotten_sum = 0.0
        for key, rating in self.rated.items():
            liked = self.liked(rating)
            for rv in self.by_film.get(key, ()):
                s = stats.get(rv.critic)
                if s is None:
                    s = stats[rv.critic] = Stat()
                ok = rv.fresh == liked
                s.films.append((key, rv.fresh, rating, ok))
                s.shared += 1
                s.agreed += ok
                s.liked += liked
                if rv.fresh:
                    fresh_sum += rating
                    self.fresh_pairs += 1
                else:
                    rotten_sum += rating
                    self.rotten_pairs += 1
        pairs = self.fresh_pairs + self.rotten_pairs
        if not pairs:
            self.problem = "few_ratings"
            return
        for cid, s in stats.items():
            p, q = self.generosity[cid], s.liked / s.shared
            s.chance = p * q + (1 - p) * (1 - q)          # a coin landing Fresh as often as they say it
            self.agreed_total += s.agreed
            self.chance_total += s.chance * s.shared
        self.pairs = pairs
        self.typical = self.agreed_total / pairs          # every critic-and-film pair you've both judged
        self.chance = self.chance_total / pairs
        self.lift = self.typical - self.chance            # how much better than a coin a typical critic does
        self.when_fresh = fresh_sum / self.fresh_pairs if self.fresh_pairs else None
        self.when_rotten = rotten_sum / self.rotten_pairs if self.rotten_pairs else None
        for s in stats.values():
            s.prior = min(max(s.chance + self.lift, 0.0), 1.0)      # a typical critic, allowing for their chance
            s.score = (s.agreed + PRIOR_FILMS * s.prior) / (s.shared + PRIOR_FILMS)
            s.match = s.score - s.prior

    def _rank(self, min_shared: int) -> list[str]:
        names = self.critics
        ids = [cid for cid, s in self.stats.items() if s.shared >= min_shared]
        ids.sort(key=lambda c: (-self.stats[c].match, -self.stats[c].shared, fold(names[c].name), c))
        return ids

    def ranking(self, min_shared: int = MIN_SHARED) -> list[str]:
        return self.ranked if min_shared == MIN_SHARED else self._rank(min_shared)

    def anti_twin(self, cid: str) -> bool:
        """Listed, and agreeing with you less often than a coin would - even pulled toward a typical critic, and
        by more than luck alone would make, allowing for every listed critic being tested: their Rotten may well
        be your Fresh. (Just below chance isn't enough, and neither is a shortfall luck makes 1 time in 20: one
        critic in twenty falls that short by luck, a dozen of 240. So the cut is 1 in 20 for all the listed
        critics together - self.anti_cut, a one-sided Bonferroni cut.)"""
        s = self.stats.get(cid)
        if s is None or s.shared < MIN_SHARED or s.score >= s.chance:
            return False
        spread = math.sqrt(s.shared * s.chance * (1 - s.chance)) or 1.0
        return (s.agreed - s.shared * s.chance) / spread < self.anti_cut

    def subset(self, keys) -> "Critics":
        """This model as if you'd rated only these films (the reviews are shared; 'liked' keeps its line)."""
        sub = copy.copy(self)
        sub.predicted = self.predicted
        sub._fit({k: self.rated[k] for k in keys}, self.liked_at)
        return sub

    def stand_out(self) -> dict:
        """Do some critics really agree with you more than others - or is the spread no more than luck would
        make? Each critic sharing TEST_MIN_SHARED+ films against a typical critic (their own chance level plus
        the typical lift): a chi-squared test."""
        if self._stand_out is None:
            self._stand_out = _stand_out(self.stats, TEST_MIN_SHARED)
        return self._stand_out

    # -- names --------------------------------------------------------------------------------------------------
    def find(self, query) -> tuple[Critic | None, list[Critic], str]:
        """A critic by id, else by name ('exact', 'partial' - every typed word - or 'guess')."""
        q = str(query).strip()
        if q in self.critics:
            return self.critics[q], [], "id"
        if self._names is None:
            index = defaultdict(list)
            for c in self.critics.values():
                for k in _keys(c.name):
                    index[k].append(c)
            self._names = index
        # The best-known first - 'travers' is Peter Travers (hundreds of reviews), not a Travers with one - so no
        # shortest-name preference (the name function _best orders partial matches by is a constant here).
        return _best(q, self._names, lambda c: (c.reviews, c.id in self.stats), lambda c: "")

    # -- Watch Next's prediction, for listing picks ---------------------------------------------------------
    def predict(self, film: Film) -> float | None:
        if film.key not in self.predicted:
            rec = _recommender(self.catalog)
            self.predicted[film.key] = round(rec.predict(film)[0], 2) if rec is not None else None
        return self.predicted[film.key]


def _recommender(catalog):
    try:
        return R.recommender(catalog)
    except ValueError:                      # too few ratings to predict anything
        return None


def model(catalog: Catalog) -> Critics:
    """The catalog's critics model, built (and the reviews read) the first time it's asked for."""
    m = catalog.cache.get(MODEL)
    if m is not None:
        return m
    with _LOCK:
        m = catalog.cache.get(MODEL)
        if m is None:
            raw = _read(catalog)
            m = Critics(catalog, raw, catalog.cache.get(ERROR, ""))
            catalog.cache[MODEL] = m
    return m


def ready(catalog: Catalog) -> bool:
    """The model is built: every call here is quick now (a few milliseconds)."""
    return catalog is not None and MODEL in catalog.cache


# ---------------------------------------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------------------------------------
def _gammq(a: float, x: float) -> float:
    """The regularised upper incomplete gamma function Q(a, x) (series or continued fraction)."""
    if x <= 0:
        return 1.0
    gln = math.lgamma(a)
    if x < a + 1:
        term = total = 1.0 / a
        ap = a
        for _ in range(1000):
            ap += 1
            term *= x / ap
            total += term
            if abs(term) < abs(total) * 1e-15:
                break
        return max(0.0, 1.0 - total * math.exp(-x + a * math.log(x) - gln))
    b = x + 1 - a
    c = 1 / 1e-300
    d = 1 / b
    h = d
    for i in range(1, 1000):
        an = -i * (i - a)
        b += 2
        d = an * d + b
        d = 1e-300 if abs(d) < 1e-300 else d
        c = b + an / c
        c = 1e-300 if abs(c) < 1e-300 else c
        d = 1 / d
        delta = d * c
        h *= delta
        if abs(delta - 1) < 1e-15:
            break
    return min(1.0, math.exp(-x + a * math.log(x) - gln) * h)


def chi2_sf(q: float, df: int) -> float:
    """P(a chi-squared variable with df degrees of freedom > q)."""
    if df <= 0:
        return 1.0
    return _gammq(df / 2, q / 2)


def _stand_out(stats: dict, min_shared: int) -> dict:
    """The chi-squared test behind stand_out(). It's cautious: it treats each critic's agreements as coin tosses
    at one rate, but films differ in how contested they are (nearly every critic agrees with you on some, others
    split them), so the real spread from luck is a little smaller than it allows for - about nine tenths of it on
    a real collection's ratings, going by reshuffling each film's verdicts among its critics. So it errs toward
    'luck': it says 'real' less often than 1 time in 20 when nothing is there, and a small real spread can pass as
    luck.
    (Each pair's own agreement rate - from the film's other critics - in var would make it sharper.)"""
    ids = [cid for cid, s in stats.items() if s.shared >= min_shared]
    if len(ids) < 5:
        return {"verdict": "too few", "tested": len(ids), "min_shared": min_shared}
    q = 0.0
    outside = 0
    for cid in ids:
        s = stats[cid]
        pi = min(max(s.prior, 0.02), 0.98)
        var = s.shared * pi * (1 - pi)
        dev = s.agreed - s.shared * pi
        q += dev * dev / var
        if abs(dev) > 1.96 * math.sqrt(var):
            outside += 1
    df = len(ids) - 1
    p = chi2_sf(q, df)
    return {"verdict": "real" if p < 0.05 else "luck", "tested": len(ids), "min_shared": min_shared,
            "q": round(q, 1), "df": df, "p": round(p, 3), "outside_luck": outside,
            "expected_by_luck": round(0.05 * len(ids), 1)}


def _closest_without(m: Critics, key: str, rating: float) -> list[str]:
    """Your closest critics as they'd be had you never rated this film: the film comes out of each of its critics'
    records and out of the typical critic's (exact, not approximate)."""
    reviewed = {rv.critic: rv.fresh for rv in m.by_film.get(key, ()) if rv.critic in m.stats}
    liked = m.liked(rating)
    agreed_total, chance_total, pairs = m.agreed_total, m.chance_total, m.pairs
    changed = {}
    for cid, fresh in reviewed.items():
        s = m.stats[cid]
        ok = fresh == liked
        n = s.shared - 1
        agreed_total -= ok
        chance_total -= s.chance * s.shared
        pairs -= 1
        if n <= 0:
            changed[cid] = None
            continue
        p, q = m.generosity[cid], (s.liked - liked) / n
        ch = p * q + (1 - p) * (1 - q)
        chance_total += ch * n
        changed[cid] = (s.agreed - ok, n, ch)
    if pairs <= 0:
        return []
    lift = (agreed_total - chance_total) / pairs
    best = []
    for cid, s in m.listed:
        if cid in changed:
            if changed[cid] is None:
                continue
            a, n, ch = changed[cid]
            if n < MIN_SHARED:
                continue
        else:
            a, n, ch = s.agreed, s.shared, s.chance
        prior = min(max(ch + lift, 0.0), 1.0)
        match = (a - n * prior) / (n + PRIOR_FILMS)
        if match > 0:
            best.append((match, n, cid))
    return [cid for _m, _n, cid in heapq.nlargest(CLOSEST, best)]


def _pick(m: Critics, key: str, closest) -> tuple[float, int, int] | None:
    """(score, Fresh, Rotten) from your closest critics' verdicts on a film, or None if none of them reviewed it.
    score = the Fresh share, smoothed: 1 Fresh of 1 = 0.67, 3 of 3 = 0.8, 5 of 6 = 0.75."""
    fresh = rotten = 0
    for rv in m.by_film.get(key, ()):
        if rv.critic in closest:
            if rv.fresh:
                fresh += 1
            else:
                rotten += 1
    if not fresh + rotten:
        return None
    return (fresh + 1) / (fresh + rotten + 2), fresh, rotten


def _adjusted_share(m: Critics, key: str) -> float | None:
    """Every stored review of a film, each allowing for how often that critic says Fresh."""
    rvs = m.by_film.get(key)
    if not rvs:
        return None
    return sum((1.0 if rv.fresh else 0.0) - m.generosity[rv.critic] for rv in rvs) / len(rvs)


def _top_fifth(scores, ys, at: float = TOP_AT) -> float:
    """Of each method's top 20% (films tied at the cut share its last places evenly), the share you rated 8+."""
    n = len(ys)
    room = max(1, n // 5)
    groups = defaultdict(list)
    for s, y in zip(scores, ys):
        groups[s].append(y)
    got, left = 0.0, room
    for s in sorted(groups, reverse=True):
        members = groups[s]
        hits = sum(1 for y in members if y >= at)
        if len(members) <= left:
            got += hits
            left -= len(members)
        else:
            got += hits * left / len(members)
            left = 0
        if not left:
            break
    return got / room


def _scores(p, y) -> dict:
    """recommend.evaluate's measures, for the Watch Next model with and without the critics."""
    n = len(y)
    err = [a - b for a, b in zip(p, y)]
    top = sorted(range(n), key=lambda i: -p[i])[: max(1, n // 5)]
    return {"mean_error": sum(abs(e) for e in err) / n, "rms_error": math.sqrt(sum(e * e for e in err) / n),
            "rank_agreement": R._spearman(p, y), "top_fifth_you_rated_8_plus": sum(1 for i in top if y[i] >= TOP_AT)
            / len(top)}


def _rounded(scores: dict) -> dict:
    return {k: round(v, 3) for k, v in scores.items()}


# ---------------------------------------------------------------------------------------------------------
# Answers
# ---------------------------------------------------------------------------------------------------------
def _problem(m: Critics, view: str) -> dict:
    out = {"ok": False, "action": "critics", "view": view, "reason": m.problem}
    if m.problem == "no_reviews":
        out["error"] = NO_REVIEWS if not m.error else f"couldn't read the critic reviews: {m.error}"
        if m.error:
            out["read_error"] = m.error
    elif m.problem == "few_ratings":
        out["error"] = (f"only {m.rated_with_reviews} of the films you rated have critic reviews - rate at least "
                        f"{MIN_RATED} films critics reviewed (the stars in Plex) to compare")
        out.update(rated_films=len(m.rated), rated_with_reviews=m.rated_with_reviews)
    else:
        out["error"] = "all your ratings are the same, so there's nothing to tell the films you liked from the rest"
    return out


def _flag(value, default: bool) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, str):
        return value.strip().casefold() not in ("false", "0", "no", "off")
    return bool(value)


def _row(m: Critics, cid: str) -> dict:
    c, s = m.critics[cid], m.stats[cid]
    return {"id": cid, "name": c.name, "publication": c.publication, "shared": s.shared, "agreed": s.agreed,
            "agreement": round(s.agreed / s.shared, 3), "score": round(s.score, 3), "match": round(s.match, 3),
            "chance": round(s.chance, 3), "reviews": c.reviews,
            "fresh_share": round(c.fresh / c.reviews, 3) if c.reviews else None, "anti_twin": m.anti_twin(cid)}


def _blind_spots(m: Critics) -> list[dict]:
    """Libraries where many films you rated have no reviews at all (10+ rated, 30%+ without)."""
    rated, without = Counter(), Counter()
    for key in m.rated:
        film = m.catalog.films.get(key)
        for lib in (film.libraries if film is not None else []):
            rated[lib] += 1
            if not m.by_film.get(key):
                without[lib] += 1
    spots = [{"library": lib, "rated": n, "without_reviews": without[lib]} for lib, n in rated.items()
             if n >= 10 and without[lib] >= 0.3 * n]
    return sorted(spots, key=lambda r: -r["without_reviews"])


def overview(catalog: Catalog, request: dict | None = None) -> dict:
    """Your closest critics and the least in step, how a typical critic does, and whether any really stand out."""
    request = request or {}
    count = R.number(request, "count", 15, 1, 500, whole=True)
    least = R.number(request, "min_shared", MIN_SHARED, 1, 1000, whole=True)
    points = _flag(request.get("points"), False)
    m = model(catalog)
    if m.problem:
        return _problem(m, "overview")
    ranked = m.ranking(least)
    closest = m.closest if least == MIN_SHARED else [c for c in ranked[:CLOSEST] if m.stats[c].match > 0]
    near = set(closest[:count])
    furthest = [c for c in reversed(ranked) if c not in near][:count]
    anti = [c for c in ranked if m.anti_twin(c)]
    test = m.stand_out()
    out = {
        "ok": True, "action": "critics", "view": "overview", "owner": catalog.owner, "liked_at": m.liked_at,
        "liked_words": liked_words(m.liked_at),
        "liked_share": round(sum(1 for v in m.rated.values() if m.liked(v)) / len(m.rated), 3),
        "rated_films": len(m.rated), "rated_with_reviews": m.rated_with_reviews, "blind_spots": _blind_spots(m),
        "critics_in_library": len(m.critics), "reviews_in_library": m.reviews, "critics_compared": len(m.stats),
        "critics_listed": len(ranked), "min_shared": least,
        "typical": {"agreement": round(m.typical, 3), "chance": round(m.chance, 3), "lift": round(m.lift, 3),
                    "when_fresh": round(m.when_fresh, 2) if m.when_fresh is not None else None,
                    "when_rotten": round(m.when_rotten, 2) if m.when_rotten is not None else None,
                    "fresh_pairs": m.fresh_pairs, "rotten_pairs": m.rotten_pairs},
        "stand_out": test,
        "closest": [_row(m, c) for c in closest[:count]], "furthest": [_row(m, c) for c in furthest],
        "anti_twins": len(anti), "closest_count": len(closest),
        "notes": [
            f"Agreement: a critic agrees with you on a film when they called it Fresh and you rated it "
            f"{liked_words(m.liked_at)} - the middle of your own ratings - or Rotten and you rated it lower.",
            "Match: how many points more (+) or less (-) often than a typical critic they agree with you, allowing "
            "for how often they say Fresh. With few films in common it stays near 0: their own record counts as "
            f"much as a typical critic's once you share {PRIOR_FILMS} films.",
            "Plex keeps at most 20 reviews per film, so most critics share only a handful of your films; only films "
            "you chose to watch and rate count, and only your own ratings.",
        ],
    }
    if test["verdict"] == "luck":
        out["notes"].append(f"The differences between critics are no bigger than luck would make (tested on the "
                            f"{test['tested']} sharing {test['min_shared']}+ films), so 'closest' means closest "
                            "so far.")
    if points:
        out["points"] = [{"id": c, "name": m.critics[c].name, "publication": m.critics[c].publication,
                          "shared": m.stats[c].shared, "agreed": m.stats[c].agreed,
                          "match": round(m.stats[c].match, 4)} for c in ranked]
    return out


def _film_brief(f: Film) -> dict:
    return {"film_key": f.key, "title": f.title, "year": f.year, "label": f.label}


def critic_detail(catalog: Catalog, critic: Critic, count: int = 12, also_seen_by=()) -> dict:
    """One critic: how they've gone with you, film by film, and what they liked (or panned) that you haven't
    played (nor any account in also_seen_by). Their yardstick is 'expected': how often a typical critic who says
    Fresh as often as they do would agree with you on these films (their chance level plus a typical critic's
    lift). match = (agreed - shared * expected) / (shared + PRIOR_FILMS): their edge over it, pulled toward 0 when
    you share few films."""
    m = model(catalog)
    c = critic
    out = {"ok": True, "action": "critics", "view": "critic", "id": c.id, "name": c.name,
           "publication": c.publication, "publications": [p for p, _n in c.publications.most_common()],
           "reviews": c.reviews, "fresh_share": round(c.fresh / c.reviews, 3) if c.reviews else None,
           "liked_at": m.liked_at, "typical": round(m.typical, 3) if m.typical is not None else None,
           "ranked": len(m.ranked), "closest_count": len(m.closest),
           "stand_out": m.stand_out()["verdict"] if not m.problem else None,
           "comparable": not m.problem}
    s = m.stats.get(c.id)
    films = []
    if s is not None:
        mine = {rv.film: rv for rv in m.by_critic.get(c.id, ())}
        line = m.liked_at - 0.5                    # between a film you liked and one you didn't
        for key, fresh, rating, ok in s.films:
            f = catalog.films[key]
            rv = mine[key]
            films.append(dict(_film_brief(f), verdict=rv.verdict, your_rating=rating, agreed=ok, quote=rv.quote,
                              link=rv.link, publication=rv.publication))
        strength = lambda r: (-abs(r["your_rating"] - line), fold(r["title"]))      # strongest feelings first
        films.sort(key=strength)
        agreed = [r for r in films if r["agreed"]]
        disagreed = [r for r in films if not r["agreed"]]
        fresh_r = [r["your_rating"] for r in films if r["verdict"] == "Fresh"]
        rotten_r = [r["your_rating"] for r in films if r["verdict"] == "Rotten"]
        out.update(shared=s.shared, agreed=s.agreed, agreement=round(s.agreed / s.shared, 3),
                   score=round(s.score, 3), match=round(s.match, 3), chance=round(s.chance, 3),
                   expected=round(s.prior, 3),
                   rank=m.rank_of.get(c.id), listed=s.shared >= MIN_SHARED, closest=c.id in m.closest_rank,
                   anti_twin=m.anti_twin(c.id),
                   when_fresh={"films": len(fresh_r),
                               "your_average": round(statistics.mean(fresh_r), 2) if fresh_r else None},
                   when_rotten={"films": len(rotten_r),
                                "your_average": round(statistics.mean(rotten_r), 2) if rotten_r else None},
                   films=films, agreed_on=agreed[:count], disagreed_on=disagreed[:count],
                   agreed_total=len(agreed), disagreed_total=len(disagreed))
    else:
        out.update(shared=0, agreed=0, agreement=None, score=None, match=None, chance=None, expected=None, rank=None,
                   listed=False,
                   closest=False, anti_twin=False, when_fresh={"films": 0, "your_average": None},
                   when_rotten={"films": 0, "your_average": None}, films=[], agreed_on=[], disagreed_on=[],
                   agreed_total=0, disagreed_total=0)
    # Their Fresh verdicts (Rotten, for a critic who reliably disagrees) on films you've neither played nor rated
    kind = out["anti_twin"]
    picks = []
    for n, rv in enumerate(m.by_critic.get(c.id, ())):
        if n % 200 == 0:
            jobs.check()
        f = catalog.films.get(rv.film)
        if f is None or f.seen(also_seen_by) or rv.fresh == kind:
            continue
        picks.append(dict(_film_brief(f), verdict=rv.verdict, quote=rv.quote, link=rv.link,
                          predicted_rating=m.predict(f), imdb_rating=f.imdb_rating))
    picks.sort(key=lambda r: (-(r["predicted_rating"] if r["predicted_rating"] is not None else -1),
                              fold(r["title"]), r["year"] or 0))
    for r in picks[:count]:
        if r["predicted_rating"] is not None:
            r["predicted_rating"] = round(r["predicted_rating"], 1)
    out.update(picks_kind="rotten" if kind else "fresh", picks=picks[:count], picks_total=len(picks))
    return out


def _find_critic(m: Critics, query) -> tuple[Critic | None, dict]:
    critic, others, how = m.find(query)
    if critic is None:
        q = fold(str(query))
        close = difflib.get_close_matches(q, [fold(c.name) for c in m.critics.values()], n=8, cutoff=0.6)
        names = {fold(c.name): c.name for c in m.critics.values()}
        return None, {"ok": False, "action": "critics", "view": "critic", "error": f"no critic called '{query}'",
                      "suggestions": [names[k] for k in close]}
    if how == "id":
        return critic, {}
    return critic, {"matched": {"asked": str(query), "found": critic.name, "how": how, "id": critic.id,
                                "other_candidates": [c.name for c in others[:5]],
                                "other_ids": [c.id for c in others[:5]]}}


def film_verdicts(catalog: Catalog, film_key) -> dict:
    """What critics said about one film - your closest critics first - for the Film page. Never raises (bar a
    job being called off): an unknown film, or no reviews, is an answer too."""
    empty = {"summary": "", "closest": [], "others": []}
    try:
        f = catalog.films.get(str(film_key or "")) if catalog is not None else None
        if f is None:
            return dict(empty, ok=False, error=f"no film with key '{film_key}'")
        m = model(catalog)
        comparable = not m.problem
        rows = []
        for rv in m.by_film.get(f.key, ()):
            s = m.stats.get(rv.critic)
            rank = m.closest_rank.get(rv.critic) if comparable else None
            rows.append({"id": rv.critic, "name": m.critics[rv.critic].name, "publication": rv.publication,
                         "verdict": rv.verdict, "quote": rv.quote, "link": rv.link, "rank": rank,
                         "shared": s.shared if s else 0, "agreed": s.agreed if s else 0,
                         "agreement": round(s.agreed / s.shared, 3) if s else None,
                         "match": round(s.match, 3) if s else None})
        closest = sorted((r for r in rows if r["rank"]), key=lambda r: r["rank"])
        others = [r for r in rows if not r["rank"]]
        fresh = sum(1 for r in rows if r["verdict"] == "Fresh")
        overall = ""
        if rows:
            overall = (f"{fresh} of the {len(rows)} reviews Plex keeps {'is' if fresh == 1 else 'are'} Fresh"
                       if len(rows) > 1 else f"The one review Plex keeps is {rows[0]['verdict']}")
            overall += f" (Tomatometer {f.rt_critic}%)." if f.rt_critic is not None else "."
        if not rows:
            summary = "Plex has no critic reviews for this film."
        elif not comparable:
            summary = f"{overall} Rate films in Plex to find your closest critics."
        elif not closest:
            summary = f"None of your closest critics reviewed it. {overall}"
        else:
            k = len(closest)
            f_n = sum(1 for r in closest if r["verdict"] == "Fresh")
            if k == 1:
                summary = f"1 of your closest critics reviewed it ({closest[0]['name']}): {closest[0]['verdict']}."
            elif f_n in (0, k):
                word = "both" if k == 2 else "all"
                summary = f"{k} of your closest critics reviewed it - {word} {'Fresh' if f_n else 'Rotten'}."
            else:
                summary = f"{k} of your closest critics reviewed it: {f_n} Fresh, {k - f_n} Rotten."
            summary += " " + overall
        return {"ok": True, "action": "critics", "view": "film", **_film_brief(f), "summary": summary,
                "closest": closest, "others": others, "reviews": len(rows), "fresh": fresh, "rt_critic": f.rt_critic,
                "your_rating": f.owner_rating, "closest_count": len(m.closest) if comparable else 0,
                "stand_out": m.stand_out()["verdict"] if comparable else None}
    except jobs.Cancelled:
        raise
    except Exception as exc:                # noqa: BLE001 - the Film page mustn't break over this
        return dict(empty, ok=False, error=f"couldn't read the critics ({type(exc).__name__}: {exc})")


def critic_names(catalog: Catalog) -> list[dict]:
    """Every critic with a review in the collection, for a global search: [{id, name, publication, reviews,
    shared}] sorted by name. (The first call reads the reviews - about half a second.)"""
    m = model(catalog)
    return sorted(({"id": c.id, "name": c.name, "publication": c.publication, "reviews": c.reviews,
                    "shared": m.stats[c.id].shared if c.id in m.stats else 0} for c in m.critics.values()),
                  key=lambda r: (fold(r["name"]), r["id"]))


# ---------------------------------------------------------------------------------------------------------
# Your critics' picks: recommend-shaped results
# ---------------------------------------------------------------------------------------------------------
def picks(catalog: Catalog, request: dict) -> dict:
    """Films you haven't seen that more of your closest critics called Fresh than Rotten - best first - with
    Watch Next's filters (the same request keys as 'recommend'). Each result is a recommend result (Watch Next's
    prediction, its reasons and breakdown) plus 'critics': the verdicts behind it. matching_films counts the films
    that pass the filters and the critics; filter_matches the films the filters alone let through (0: nothing on
    the shelf matches them, whatever the critics said); left_out, when the request leaves out libraries or genres
    (exclude_libraries / exclude_genres), how many more picks there would be without that."""
    request = dict(request)
    count = R.number(request, "count", 30, 1, 500, whole=True)
    for key in ("decade", "min_year", "max_year", "min_runtime", "max_runtime"):
        request[key] = R.number(request, key, None, -1e6, 1e6, whole=True)
    for key in ("min_imdb", "max_imdb"):
        request[key] = R.number(request, key, None)
    per_director = R.number(request, "max_per_director", 2, 0, 1000, whole=True)
    try:
        also = R.seen_by(request)
    except ValueError as exc:
        return {"ok": False, "action": "critics", "view": "picks", "error": str(exc)}
    m = model(catalog)
    if m.problem:
        return _problem(m, "picks")
    try:
        rec = R.recommender(catalog)
    except ValueError as exc:                   # too few ratings to predict anything
        return {"ok": False, "action": "critics", "view": "picks", "error": str(exc)}
    notes = []
    seeds = []
    for q in R._as_list(request.get("like_key")):
        film = catalog.films.get(q)
        if film is None:
            return {"ok": False, "action": "critics", "view": "picks", "error": f"no film with key '{q}'"}
        seeds.append(film)
    for q in R._as_list(request.get("like")):
        film, others, how = catalog.find_film(q)
        if film is None:
            return {"ok": False, "action": "critics", "view": "picks", "error": f"no film matching '{q}'",
                    "suggestions": [f.label for f in others[:5]]}
        seeds.append(film)
        if how != "exact":
            notes.append(f"'{q}' taken as {film.label}")
    people = []
    for group in ("with", "directed_by"):
        for q in R._as_list(request.get(group + "_id")):
            person = catalog.people.get(q)
            if person is None:
                return {"ok": False, "action": "critics", "view": "picks", "error": f"no one with id '{q}'"}
            people.append((group, person))
        for q in R._as_list(request.get(group)):
            person, others, how = catalog.find_person(q)
            if person is None:
                return {"ok": False, "action": "critics", "view": "picks", "error": f"no one matching '{q}'",
                        "suggestions": [p.name for p in others[:5]]}
            people.append((group, person))
            if how != "exact":
                notes.append(f"'{q}' taken as {person.name}")
            same = [p for p in others if p.name == person.name]
            if same:
                notes.append(f"{len(same) + 1} people on your shelf are called {person.name}: this is the one with "
                             f"the most films ({person.film_count}); {group}_id picks another.")
    words = [w for w in fold(str(request.get("text", ""))).split() if len(w) > 2 and w not in R.STOP_WORDS]
    need = len(words) if len(words) <= 3 else math.ceil(len(words) * 2 / 3)
    include_watched = _flag(request.get("include_watched"), False)
    seed_vectors = [rec.vector(R.features(f)) for f in seeds]
    seed_keys = {f.key for f in seeds}
    closest = set(m.closest)
    candidates = []
    played_matches = 0
    filter_matches = 0          # films the filters alone let through, whatever the critics said
    left_out = 0                # picks left out only by exclude_libraries / exclude_genres
    for film in catalog.films.values():
        jobs.check()
        if film.owner_rating is not None or film.key in seed_keys:
            continue
        if not R._passes(film, request, people, leave_out=False):
            continue
        matched = []
        if words:
            haystack = fold(" ".join([film.title, film.tagline, film.summary] + film.genres))
            matched = [w for w in words if re.search(rf"\b{re.escape(w)}", haystack)]
            if len(matched) < need:
                continue
        x = likeness = None
        if seeds:
            x = R.features(film)
            likeness = max(R._cosine(rec.vector(x), s) for s in seed_vectors)
            if likeness < 0.1:
                continue                     # nothing in common with the films asked for
        played = film.seen(also) and not include_watched         # (not rated: skipped above)
        if R._left_out(film, request):
            pick = None if played else _pick(m, film.key, closest)
            if pick is not None and pick[1] > pick[2]:
                left_out += 1                # (a pick but for that)
            continue
        if not played:
            filter_matches += 1
        pick = _pick(m, film.key, closest)
        if pick is None or pick[1] <= pick[2]:
            continue
        if played:
            played_matches += 1
            continue
        if x is None:
            x = R.features(film)
        predicted, expected, kind, lift = rec.predict(film, x)
        candidates.append((len(matched), pick, predicted, expected, kind, lift, likeness, matched, film, x))
    candidates.sort(key=lambda c: (-c[0], -c[1][0], -c[1][1], -c[2], fold(c[8].title)))
    used = defaultdict(int)
    results = []
    for _n, pick, predicted, expected, kind, lift, likeness, matched, film, x in candidates:
        jobs.check()
        if per_director > 0 and any(used[d.person] >= per_director for d in film.directors):
            continue
        for d in film.directors:
            used[d.person] += 1
        r = R._result(rec, film, x, predicted, expected, kind, lift, likeness, matched)
        verdicts = sorted((rv for rv in m.by_film[film.key] if rv.critic in closest),
                          key=lambda rv: m.closest_rank[rv.critic])
        r["critics"] = {"score": round(pick[0], 3), "fresh": pick[1], "rotten": pick[2],
                        "verdicts": [{"id": rv.critic, "name": m.critics[rv.critic].name,
                                      "publication": rv.publication, "verdict": rv.verdict, "quote": rv.quote}
                                     for rv in verdicts]}
        results.append(r)
        if len(results) >= count:
            break
    notes.append(f"Films more of your {len(closest)} closest critics called Fresh than Rotten: the most "
                 "one-sided first (3 of 3 Fresh beats 1 of 1), then Watch Next's prediction.")
    # (Only what's always true: whether the critics would make Watch Next better is the critics test's to say -
    # evaluate's model_check - and it's run on request, not here.)
    notes.append(NOT_IN_WATCH_NEXT)
    if seeds:
        notes.append("Only films much like " + ", ".join(f.label for f in seeds) + " (shared people, studio, "
                     "genres...).")
    if words:
        notes.append("Films matching more of the words come first.")
    if per_director > 0:
        notes.append(f"At most {per_director} films per director (max_per_director 0 = no limit).")
    notes.append(R.UNSEEN_NOTE + (R.ALSO_SEEN_NOTE if also else ""))
    answer = {"ok": True, "action": "critics", "view": "picks", "sort": "critics", "results": results,
              "matching_films": len(candidates), "filter_matches": filter_matches, "closest_count": len(closest),
              "model": {"trained_on": len(rec.rated), "your_average": round(rec.average, 2),
                        "lambda": rec.model.lam, "scale": "your ratings, 0-10 (5 stars = 10)"},
              "notes": notes}
    if not include_watched:
        answer["played_matches"] = played_matches
    if R.leaves_out(request):
        answer["left_out"] = left_out
    return answer


# ---------------------------------------------------------------------------------------------------------
# How good a guide are they?
# ---------------------------------------------------------------------------------------------------------
def _folds(n: int) -> list[tuple[set, list]]:
    """recommend.evaluate's folds: FOLDS folds, REPEATS times over, from random.Random(1000 + repeat)."""
    out = []
    for rep in range(REPEATS):
        order = list(range(n))
        random.Random(1000 + rep).shuffle(order)
        for k in range(FOLDS):
            test = set(order[k::FOLDS])
            out.append((test, [i for i in range(n) if i not in test]))
    return out


def _random_panels(m: Critics, films, size: int, panels: int = RANDOM_PANELS, seed: int = 2026) -> dict | None:
    """The like-for-like yardstick for your closest critics: panels of as many critics picked at random from the
    listed ones, each film ranked by the Fresh share of the panel's verdicts (as _pick scores it) against your
    ratings. Picked without looking at your ratings, they need no leaving-out. Does choosing critics by how they
    agree with you beat choosing them blind? {panels, size, rank_agreement (their average), films_covered
    (average), scores} - or None when there aren't enough listed critics to choose from."""
    listed = list(m.ranked)
    if size < 1 or len(listed) <= size:
        return None
    pool = set(listed)
    per_film = []
    for f in films:
        verdicts = [(rv.critic, rv.fresh) for rv in m.by_film.get(f.key, ()) if rv.critic in pool]
        if verdicts:
            per_film.append((f.owner_rating, verdicts))
    rng = random.Random(seed)
    scores, covered = [], []
    for n in range(panels):
        if n % 10 == 0:
            jobs.check()
        panel = set(rng.sample(listed, size))
        xs, ys = [], []
        for rating, verdicts in per_film:
            fresh = rotten = 0
            for cid, is_fresh in verdicts:
                if cid in panel:
                    if is_fresh:
                        fresh += 1
                    else:
                        rotten += 1
            if fresh + rotten:
                xs.append((fresh + 1) / (fresh + rotten + 2))
                ys.append(rating)
        if len(xs) >= 10:
            scores.append(R._spearman(xs, ys))
            covered.append(len(xs))
    if not scores:
        return None
    return {"panels": len(scores), "size": size, "rank_agreement": round(statistics.mean(scores), 3),
            "films_covered": round(statistics.mean(covered)), "scores": scores}


def _with_critics(row: dict, pick) -> dict:
    row = dict(row)
    if pick is None:
        row["missing|critics"] = 1.0
    else:
        row["score|critics"] = (pick[0] - 0.6) / 0.15
    return row


def evaluate(catalog: Catalog, request: dict | None = None) -> dict:
    """Leave-one-out: each rated film a critic reviewed is judged by the closest critics picked without it - and
    so, for comparison, by all critics (allowing for how often each says Fresh), the Tomatometer, IMDb and Watch
    Next's own model (cross-validated as its accuracy test is) - and panels of critics picked at random
    (random_panels), the like-for-like yardstick. share_you_rated_8_plus is what the top picks are up against;
    verdicts_per_film how many verdicts the closest critics' ranking rests on. Then, with model_check (default
    true): would the closest critics make Watch Next's model better? The same test with and without them as one
    more thing it knows about a film, picked from each fold's training films only. A few seconds."""
    request = request or {}
    check_model = _flag(request.get("model_check"), True)
    started = time.perf_counter()
    m = model(catalog)
    if m.problem:
        return _problem(m, "evaluate")
    rated = [catalog.films[k] for k in m.rated]
    n = len(rated)
    y = [f.owner_rating for f in rated]
    rows = [R.features(f) for f in rated]
    folds = _folds(n)
    base = [0.0] * n
    for test, train in folds:
        jobs.check()
        fitted = R.fit([rows[i] for i in train], [y[i] for i in train], R.DEFAULT_LAMBDA)
        for i in test:
            base[i] += fitted.predict(rows[i]) / REPEATS
    tested = [i for i, f in enumerate(rated) if m.by_film.get(f.key)]
    picked = {}
    for i in tested:
        jobs.check()
        f = rated[i]
        picked[i] = _pick(m, f.key, set(_closest_without(m, f.key, f.owner_rating)))
    covered = [i for i in tested if picked[i] is not None]
    out = {"ok": True, "action": "critics", "view": "evaluate", "rated_films": n, "films_tested": len(tested),
           "films_covered": len(covered), "closest_count": len(m.closest)}
    if len(covered) < 10:
        return dict(out, ok=False, reason="few_covered",
                    error=f"only {len(covered)} of the films you rated were reviewed by your closest critics - too "
                          "few to test them")
    ys = [y[i] for i in covered]
    rt = [rated[i].rt_critic for i in tested if rated[i].rt_critic is not None]
    imdb = [rated[i].imdb_rating for i in tested if rated[i].imdb_rating is not None]
    rt_mean = statistics.mean(rt) if rt else 60.0
    imdb_mean = statistics.mean(imdb) if imdb else 6.5
    methods = {
        "Your closest critics": [picked[i][0] for i in covered],
        "All critics": [_adjusted_share(m, rated[i].key) for i in covered],
        "Rotten Tomatoes Tomatometer": [rated[i].rt_critic if rated[i].rt_critic is not None else rt_mean
                                        for i in covered],
        "IMDb rating": [rated[i].imdb_rating if rated[i].imdb_rating is not None else imdb_mean for i in covered],
        "Watch Next's model": [base[i] for i in covered],
    }
    out["methods"] = {name: {"rank_agreement": round(R._spearman(s, ys), 3),
                             "top_fifth_you_rated_8_plus": round(_top_fifth(s, ys), 3)}
                      for name, s in methods.items()}
    # what 'top picks rated 8+' is up against: the share of these films you rated 8+ at all
    out["share_you_rated_8_plus"] = round(sum(1 for v in ys if v >= TOP_AT) / len(ys), 3)
    # how many verdicts each ranking rests on: one verdict can only say Fresh or Rotten
    counts = [picked[i][1] + picked[i][2] for i in covered]
    out["verdicts_per_film"] = {
        "closest_one": sum(1 for k in counts if k == 1), "closest_two": sum(1 for k in counts if k == 2),
        "closest_more": sum(1 for k in counts if k > 2),
        "all_critics_average": round(statistics.mean(len(m.by_film[rated[i].key]) for i in covered), 1)}
    panels = _random_panels(m, [rated[i] for i in tested], len(m.closest))
    if panels is not None:
        scores = panels.pop("scores")
        closest_rank = out["methods"]["Your closest critics"]["rank_agreement"]
        panels["at_or_above_closest"] = round(sum(1 for s in scores if s >= closest_rank) / len(scores), 3)
        out["random_panels"] = panels

    def split(fresh_rotten) -> dict:
        yes = [y[i] for i, (f, r) in zip(covered, fresh_rotten) if f > r]
        no = [y[i] for i, (f, r) in zip(covered, fresh_rotten) if f < r]
        share = lambda v: round(sum(1 for x in v if m.liked(x)) / len(v), 3) if v else None
        mean = lambda v: round(statistics.mean(v), 2) if v else None
        return {"mostly_fresh": len(yes), "liked": share(yes), "average": mean(yes),
                "mostly_rotten": len(no), "liked_when_rotten": share(no), "average_when_rotten": mean(no)}

    everyone = []
    for i in covered:
        rvs = m.by_film[rated[i].key]
        fresh = sum(1 for rv in rvs if rv.fresh)
        everyone.append((fresh, len(rvs) - fresh))
    out["picks"] = {"closest": split([(picked[i][1], picked[i][2]) for i in covered]), "all": split(everyone),
                    "liked_overall": round(sum(1 for v in ys if m.liked(v)) / len(ys), 3)}
    out["liked_at"] = m.liked_at
    out["method"] = (f"Each of the {len(tested)} films you rated that critics reviewed was judged by your closest "
                     "critics as they'd be had you never rated it; the "
                     f"{len(covered)} that one of them reviewed are the test. All critics: every stored review, "
                     "each allowing for how often that critic says Fresh. Watch Next's model: cross-validated as "
                     f"its own accuracy test is ({FOLDS} parts, twice). Rank agreement is Spearman's (1 = your "
                     "order exactly, 0 = no better than chance); top fifth = the share of each method's top 20% "
                     "you rated 8 or more. random_panels: as many critics picked at random from the listed ones, "
                     f"{RANDOM_PANELS} times - whether picking them by how they agree with you beats picking "
                     "them blind.")
    if check_model:
        without = _scores(base, y)
        with_preds = [0.0] * n
        reviewed = set(tested)
        for test, train in folds:
            jobs.check()
            sub = m.subset([rated[i].key for i in train])
            fold_closest = set(sub.closest)
            feats = []
            for i in range(n):
                if i % 50 == 0:
                    jobs.check()
                if i not in reviewed:
                    feats.append(_with_critics(rows[i], None))
                    continue
                f = rated[i]
                if i in test:
                    near = fold_closest
                else:
                    near = set(_closest_without(sub, f.key, f.owner_rating))
                feats.append(_with_critics(rows[i], _pick(sub, f.key, near)))
            fitted = R.fit([feats[i] for i in train], [y[i] for i in train], R.DEFAULT_LAMBDA)
            for i in test:
                with_preds[i] += fitted.predict(feats[i]) / REPEATS
        with_ = _scores(with_preds, y)
        helps = (without["rms_error"] - with_["rms_error"] >= HELPS_RMS and
                 with_["rank_agreement"] - without["rank_agreement"] >= HELPS_RANK)
        out["model_check"] = {"without_critics": _rounded(without), "with_critics": _rounded(with_),
                              "helps": helps,
                              "rule": f"helps = the error (rms) drops by at least {HELPS_RMS} and rank agreement "
                                      f"rises by at least {HELPS_RANK}"}
    out["in_watch_next"] = False            # recommend.py doesn't use the critics (see model_check)
    out["seconds"] = round(time.perf_counter() - started, 2)
    return out


# ---------------------------------------------------------------------------------------------------------
def answer(catalog: Catalog, request: dict) -> dict:
    """The ask action 'critics'. {view: overview (default) - count (1-500, 15), min_shared (1-1000, 5), points};
    {critic: an id or a name, count (12), also_seen_by}; {film_key or title}: what critics said about one film;
    {view: picks, ...recommend's filters, also_seen_by too}; {view: evaluate, model_check (true)}; {view: names}."""
    request = request or {}
    view = str(request.get("view") or "").strip().lower()
    if view and view not in VIEWS:
        raise ValueError(f"view must be one of {', '.join(VIEWS)}")
    if view == "critic" or (not view and request.get("critic") not in (None, "")):
        count = R.number(request, "count", 12, 1, 500, whole=True)
        m = model(catalog)
        if not m.critics:
            return _problem(m, "critic")
        try:
            also = R.seen_by(request)
        except ValueError as exc:
            return {"ok": False, "action": "critics", "view": "critic", "error": str(exc)}
        critic, extra = _find_critic(m, request.get("critic", ""))
        if critic is None:
            return extra
        return dict(critic_detail(catalog, critic, count, also), **extra)
    if view == "film" or (not view and (request.get("film_key") or request.get("title"))):
        extra = {}
        key = request.get("film_key")
        if not key:
            title = str(request.get("title", ""))
            film, others, how = catalog.find_film(title)
            if film is None:
                return {"ok": False, "action": "critics", "view": "film", "error": f"no film matching '{title}'",
                        "suggestions": [f.label for f in others[:8]]}
            key = film.key
            extra["matched"] = {"asked": title, "found": film.label, "how": how,
                                "other_candidates": [f.label for f in others[:5]]}
        return dict(film_verdicts(catalog, key), **extra)
    if view == "picks":
        return picks(catalog, request)
    if view == "evaluate":
        return evaluate(catalog, request)
    if view == "names":
        m = model(catalog)
        return {"ok": True, "action": "critics", "view": "names", "critics": critic_names(catalog),
                "reviews": m.reviews}
    return overview(catalog, request)
