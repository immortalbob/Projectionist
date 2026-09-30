"""Tests for the critics backbone (projectionist/critics.py): which critics' Fresh/Rotten verdicts agree with your
ratings, their picks, the Film page's verdicts, the honest evaluation, and reading reviews from a database."""

import os
import random
import shutil
import sqlite3
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from projectionist import critics as C  # noqa: E402
from projectionist import jobs  # noqa: E402
from projectionist import recommend as R  # noqa: E402
from projectionist.ask import handle  # noqa: E402


# ---------------------------------------------------------------------------------------------------------
# The fixture: 40 rated films (20 you rated 9, 20 you rated 5), 12 you haven't seen, 2 played but not rated
# ---------------------------------------------------------------------------------------------------------
def film_specs():
    out = []
    for i in range(20):
        out.append(dict(title=f"Liked {i}", year=1980 + i, genres=["Action"], directors=[f"Dir L{i % 5}"],
                        cast=[f"Hero {i}"], imdb_rating=7.5 + (i % 3) * 0.1, rt_critic=80, owner_rating=9.0,
                        runtime_min=100, libraries=["Movies"], owner_plays=1))
    for i in range(20):
        out.append(dict(title=f"Meh {i}", year=1980 + i, genres=["Drama"], directors=[f"Dir M{i % 5}"],
                        cast=[f"Bore {i}"], imdb_rating=6.5 + (i % 3) * 0.1, rt_critic=50, owner_rating=5.0,
                        runtime_min=120, libraries=["Movies"], owner_plays=1))
    for j in range(12):
        out.append(dict(title=f"New {j}", year=1990 + j, genres=["Action" if j % 2 == 0 else "Drama"],
                        directors=["Dir U0"] if j < 4 else [f"Dir U{j}"], cast=[f"Newbie {j}"], imdb_rating=7.0,
                        rt_critic=70, runtime_min=95, libraries=["Asia"] if j % 3 == 0 else ["Movies"],
                        summary="A lone swordsman seeks revenge." if j == 2 else "A story."))
    for k in range(2):
        out.append(dict(title=f"Played {k}", year=2005 + k, genres=["Action"], directors=[f"Dir P{k}"],
                        cast=[f"Watcher {k}"], imdb_rating=7.0, rt_critic=70, owner_plays=2, libraries=["Movies"]))
    return out


def keys_by_title(catalog):
    return {f.title: f.key for f in catalog.films.values()}


def review_rows(catalog, named=True, coins=20, seed=7, coin_films=15, coin_fresh=0.6):
    k = keys_by_title(catalog)
    liked = [k[f"Liked {i}"] for i in range(20)]
    meh = [k[f"Meh {i}"] for i in range(20)]
    new = [k[f"New {j}"] for j in range(12)]
    played = [k[f"Played {i}"] for i in range(2)]
    rows = []

    def add(critic, key, fresh, pub="The Daily", cid=None):
        row = {"critic": critic, "film_key": key, "verdict": "Fresh" if fresh else "Rotten", "publication": pub,
               "quote": f"{critic} on {catalog.films[key].title}: {'yes' if fresh else 'no'}."}
        if cid:
            row["critic_id"] = cid
        rows.append(row)

    if named:
        for key in liked:
            add("Twin Tina", key, True, "Tina Times")
        for key in meh:
            add("Twin Tina", key, False, "Tina Times")
        for j, key in enumerate(new):
            add("Twin Tina", key, j < 8, "Tina Times")
        add("Twin Tina", played[0], True, "Tina Times")
        for key in liked:
            add("Anti Andy", key, False, "Andy Weekly")
        for key in meh:
            add("Anti Andy", key, True, "Andy Weekly")
        for j, key in enumerate(new[:6]):
            add("Anti Andy", key, j >= 4, "Andy Weekly")
        for key in liked[:18] + meh[:2] + new[:6] + played[:1]:     # 18 of 20 agree - but Fresh on everything
            add("Generous Gus", key, True, "Gus Gazette")
        add("Lone Lou", played[1], True, "Lou Ledger")               # shares none of your rated films
        add("Lone Lou", new[6], True, "Lou Ledger")
        add("Small Sam", liked[0], True)
        add("Small Sam", liked[1], True)
        add("Small Sam", meh[0], False)
        for i in range(5):                                             # 8 of 10
            add("Mid Mia", liked[i], i < 4, "Mia Monthly")
            add("Mid Mia", meh[i], i >= 4, "Mia Monthly")
        for key in liked[5:8]:                                         # 5 of 5
            add("Five Fay", key, True)
        for key in meh[5:7]:
            add("Five Fay", key, False)
        for key in liked[:16]:                                         # 30 of 32
            add("Steady Stu", key, True, "Stu Standard")
        for i, key in enumerate(meh[:16]):
            add("Steady Stu", key, i >= 14, "Stu Standard")
        add("Steady Stu", new[9], True, "Stu Standard")
    rng = random.Random(seed)
    rated = liked + meh
    for c in range(coins):
        for key in rng.sample(rated, coin_films):
            add(f"Coin {c:02d}", key, rng.random() < coin_fresh, "Coin Courier")
    return rows


def critic_catalog(named=True, coins=20, seed=7, **kw):
    from test_projectionist import make_catalog
    catalog = make_catalog(film_specs(), libraries=("Movies", "Asia"))
    C.use_reviews(catalog, review_rows(catalog, named, coins, seed, **kw))
    return catalog


def in_sample_rank(catalog) -> float:
    """What the closest critics' rank agreement would be if they were picked knowing every rating - the leak the
    leave-one-out test avoids."""
    m = C.model(catalog)
    closest = set(m.closest)
    ys, scores = [], []
    for key, rating in m.rated.items():
        pick = C._pick(m, key, closest)
        if pick is not None:
            ys.append(rating)
            scores.append(pick[0])
    return R._spearman(scores, ys)


def ids_by_name(catalog):
    return {c.name: c.id for c in C.model(catalog).critics.values()}


def plain_catalog(specs=None):
    from test_projectionist import make_catalog
    return make_catalog(specs if specs is not None else film_specs(), libraries=("Movies", "Asia"))


# ---------------------------------------------------------------------------------------------------------
class OverviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = critic_catalog()
        cls.ov = C.overview(cls.catalog, {"points": True, "count": 30})
        cls.ids = ids_by_name(cls.catalog)

    def rank(self, name):
        return [p["name"] for p in self.ov["points"]].index(name)

    def test_closest_and_least_in_step(self):
        ov = self.ov
        self.assertTrue(ov["ok"])
        self.assertEqual(ov["closest"][0]["name"], "Twin Tina")
        self.assertEqual(ov["closest"][0]["agreed"], 40)
        self.assertEqual(ov["furthest"][0]["name"], "Anti Andy")
        andy = ov["furthest"][0]
        self.assertTrue(andy["anti_twin"])                                  # below chance, beyond luck
        self.assertLess(andy["score"], andy["chance"])
        self.assertFalse(ov["closest"][0]["anti_twin"])
        # only critics who really fall short of chance are called anti-twins, not every coin that dips below it -
        # and not the one coin in twenty that dips 1-in-20 far (with the fixture's seed, Coin 06 does)
        self.assertEqual(ov["anti_twins"], 1)
        self.assertEqual(ov["closest_count"], len(C.model(self.catalog).closest))
        self.assertTrue(all(r["match"] > 0 for r in ov["closest"]))
        self.assertFalse({r["id"] for r in ov["closest"]} & {r["id"] for r in ov["furthest"]})

    def test_reliably_disagreeing_allows_for_testing_every_critic(self):
        """'Reliably disagrees' is 1 in 20 over every listed critic at once, not for each: critics tossing coins
        are never flagged, whatever the seed (a 1-in-20 cut for each flagged some in most seeds)."""
        m = C.model(self.catalog)
        self.assertAlmostEqual(m.anti_cut, C.statistics.NormalDist().inv_cdf(0.05 / len(m.ranked)), places=9)
        self.assertLess(m.anti_cut, -2.8)
        for seed in range(8):
            coins = C.model(critic_catalog(named=False, coins=20, seed=seed))
            self.assertEqual([c for c in coins.ranked if coins.anti_twin(c)], [], seed)
        self.assertTrue(m.anti_twin(self.ids["Anti Andy"]))                    # 0 of 40: far beyond it

    def test_allowing_for_how_often_a_critic_says_fresh(self):
        """Generous Gus agrees on 18 of 20 (90%) - but he calls everything Fresh and you liked most of the films he
        reviewed, so a coin would do nearly as well. Mid Mia agrees on 8 of 10 (80%) of a balanced set: she ranks
        higher."""
        gus = next(p for p in self.ov["points"] if p["name"] == "Generous Gus")
        mia = next(p for p in self.ov["points"] if p["name"] == "Mid Mia")
        self.assertGreater(gus["agreed"] / gus["shared"], mia["agreed"] / mia["shared"])
        self.assertLess(self.rank("Mid Mia"), self.rank("Generous Gus"))
        self.assertLess(gus["match"], mia["match"])

    def test_few_shared_films_are_pulled_toward_typical(self):
        """5 of 5 is a perfect record, 30 of 32 isn't - but 30 of 32 says far more."""
        self.assertLess(self.rank("Steady Stu"), self.rank("Five Fay"))
        fay = next(p for p in self.ov["points"] if p["name"] == "Five Fay")
        self.assertEqual((fay["agreed"], fay["shared"]), (5, 5))
        self.assertLess(fay["match"], 0.2)

    def test_listing_needs_enough_shared_films(self):
        names = {p["name"] for p in self.ov["points"]}
        self.assertNotIn("Small Sam", names)                                # 3 shared films
        self.assertNotIn("Lone Lou", names)                                 # none
        wider = C.overview(self.catalog, {"points": True, "min_shared": 3, "count": 50})
        self.assertIn("Small Sam", {p["name"] for p in wider["points"]})
        self.assertEqual(wider["min_shared"], 3)
        self.assertGreater(wider["critics_listed"], self.ov["critics_listed"])

    def test_the_numbers_behind_it(self):
        ov = self.ov
        self.assertEqual(ov["liked_at"], 7.0)                               # the middle of your ratings
        self.assertEqual(ov["liked_words"], "7 or more out of 10 (3½ stars)")
        self.assertEqual((ov["rated_films"], ov["rated_with_reviews"]), (40, 40))
        self.assertEqual(ov["critics_in_library"], 28)
        t = ov["typical"]
        self.assertGreater(t["agreement"], t["chance"])
        self.assertAlmostEqual(t["lift"], t["agreement"] - t["chance"], delta=0.002)
        self.assertGreater(t["when_fresh"], t["when_rotten"])
        self.assertEqual(ov["critics_compared"], 27)                        # everyone who reviewed a rated film
        self.assertNotIn("points", C.overview(self.catalog, {}))            # only when asked for
        self.assertEqual(len(C.overview(self.catalog, {"count": 3})["closest"]), 3)
        self.assertTrue(any("Match" in n for n in ov["notes"]))
        self.assertEqual(ov["blind_spots"], [])

    def test_blind_spots(self):
        """Libraries where many rated films have no reviews are named."""
        catalog = plain_catalog()
        moved = {f"Meh {i}" for i in range(10, 20)}            # 10 of the Meh films move to 'Asia'
        rows = [r for r in review_rows(catalog) if catalog.films[r["film_key"]].title not in moved]
        for f in catalog.films.values():
            if f.title in moved:
                f.libraries = ["Asia"]
        C.use_reviews(catalog, rows)
        spots = C.overview(catalog, {})["blind_spots"]
        self.assertEqual(spots, [{"library": "Asia", "rated": 10, "without_reviews": 10}])

    def test_liked_line_when_most_ratings_are_the_lowest(self):
        self.assertEqual(C._liked_at([8, 8, 8, 8, 9]), 9.0)                 # not 'everything liked'
        self.assertEqual(C._liked_at([5, 9, 9, 9]), 9.0)
        self.assertEqual(C._liked_at([4, 6, 7, 8]), 6.5)
        self.assertIsNone(C._liked_at([7, 7]))
        self.assertEqual(C.liked_words(8.0), "8 or more out of 10 (4 stars)")


class StandOutTests(unittest.TestCase):
    def test_real_differences(self):
        test = C.model(critic_catalog()).stand_out()
        self.assertEqual(test["verdict"], "real")                          # Tina and Andy are no accident
        self.assertLess(test["p"], 0.05)
        self.assertGreater(test["outside_luck"], test["expected_by_luck"])

    def test_luck(self):
        """Critics tossing coins: however the list comes out, the test says it's luck."""
        m = C.model(critic_catalog(named=False, coins=20, seed=7, coin_films=15))
        test = m.stand_out()
        self.assertEqual(test["verdict"], "luck")
        self.assertGreater(test["p"], 0.05)
        self.assertEqual((test["tested"], test["df"]), (20, 19))
        ov = C.overview(m.catalog, {})
        self.assertTrue(any("no bigger than luck" in n for n in ov["notes"]))

    def test_too_few_to_tell(self):
        m = C.model(critic_catalog(named=False, coins=12, seed=3, coin_films=8))
        self.assertIsNone(m.problem)
        test = m.stand_out()
        self.assertEqual(test["verdict"], "too few")                       # nobody shares 10 films
        self.assertEqual(test["tested"], 0)

    def test_chi_squared_tail(self):
        self.assertAlmostEqual(C.chi2_sf(3.841, 1), 0.05, places=3)
        self.assertAlmostEqual(C.chi2_sf(18.307, 10), 0.05, places=3)
        self.assertAlmostEqual(C.chi2_sf(124.342, 100), 0.05, places=3)
        self.assertEqual(C.chi2_sf(0, 5), 1.0)
        self.assertAlmostEqual(C.chi2_sf(10, 10), 0.4405, places=3)


class ProblemTests(unittest.TestCase):
    def test_no_reviews(self):
        catalog = plain_catalog()                                          # a memory catalog: no database
        ov = C.overview(catalog, {})
        self.assertEqual((ov["ok"], ov["reason"]), (False, "no_reviews"))
        self.assertIn("Plex keeps them", ov["error"])
        key = next(iter(catalog.films))
        fv = C.film_verdicts(catalog, key)
        self.assertTrue(fv["ok"])
        self.assertEqual((fv["reviews"], fv["closest"], fv["others"]), (0, [], []))
        self.assertEqual(fv["summary"], "Plex has no critic reviews for this film.")
        for view in ("picks", "evaluate"):
            self.assertEqual(C.answer(catalog, {"view": view})["reason"], "no_reviews", view)
        self.assertFalse(C.answer(catalog, {"critic": "anyone"})["ok"])
        self.assertEqual(C.critic_names(catalog), [])
        self.assertFalse(handle({"action": "critics"}, catalog=catalog)["ok"])

    def test_too_few_rated_films_with_reviews(self):
        catalog = plain_catalog()
        rows = [r for r in review_rows(catalog, coins=0) if not catalog.films[r["film_key"]].title.startswith("Meh")]
        C.use_reviews(catalog, rows)
        ov = C.overview(catalog, {})
        self.assertEqual(ov["reason"], "few_ratings")
        self.assertIn("rate at least 30", ov["error"])
        self.assertEqual(ov["rated_with_reviews"], 20)
        # the Film page still gets the reviews, and a critic their picks
        fv = C.film_verdicts(catalog, keys_by_title(catalog)["New 0"])
        self.assertTrue(fv["ok"])
        self.assertEqual(fv["closest"], [])
        self.assertTrue(fv["summary"].endswith("Rate films in Plex to find your closest critics."))
        d = C.answer(catalog, {"critic": "Twin Tina"})
        self.assertTrue(d["ok"])
        self.assertFalse(d["comparable"])
        self.assertEqual(d["shared"], 0)
        self.assertTrue(d["picks"])

    def test_all_ratings_the_same(self):
        specs = film_specs()
        for s in specs:
            if s.get("owner_rating") is not None:
                s["owner_rating"] = 7.0
        catalog = plain_catalog(specs)
        C.use_reviews(catalog, review_rows(catalog))
        ov = C.overview(catalog, {})
        self.assertEqual(ov["reason"], "flat_ratings")
        self.assertIn("all your ratings are the same", ov["error"])

    def test_bad_requests(self):
        catalog = critic_catalog()
        for request in ({"count": "x"}, {"min_shared": "lots"}, {"view": "picks", "count": "x"},
                        {"view": "sideways"}):
            answer = handle(dict(request, action="critics"), catalog=catalog)
            self.assertFalse(answer["ok"], request)
            self.assertTrue(answer["error"].startswith("bad request"), answer)


class CriticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = critic_catalog()
        cls.ids = ids_by_name(cls.catalog)

    def detail(self, who, **kw):
        return C.answer(self.catalog, dict(critic=who, **kw))

    def test_a_twin(self):
        d = self.detail(self.ids["Twin Tina"])
        self.assertEqual((d["ok"], d["view"], d["name"], d["publication"]), (True, "critic", "Twin Tina", "Tina Times"))
        self.assertEqual((d["shared"], d["agreed"], d["agreed_total"], d["disagreed_total"]), (40, 40, 40, 0))
        self.assertEqual((d["rank"], d["listed"], d["closest"], d["anti_twin"]), (1, True, True, False))
        self.assertEqual(d["when_fresh"], {"films": 20, "your_average": 9.0})
        self.assertEqual(d["when_rotten"], {"films": 20, "your_average": 5.0})
        self.assertEqual(len(d["films"]), 40)                                  # every shared film, for the chart
        self.assertEqual(len(d["agreed_on"]), 12)                               # count defaults to 12
        # her picks: Fresh, and neither rated nor played (Played 0, which she liked, is left out)
        titles = [r["title"] for r in d["picks"]]
        self.assertEqual(sorted(titles), [f"New {j}" for j in range(8)])
        self.assertEqual((d["picks_kind"], d["picks_total"]), ("fresh", 8))
        self.assertTrue(all(r["verdict"] == "Fresh" and r["predicted_rating"] is not None for r in d["picks"]))
        predicted = [r["predicted_rating"] for r in d["picks"]]
        self.assertEqual(predicted, sorted(predicted, reverse=True))          # Watch Next's best first
        self.assertEqual(len(self.detail("Twin Tina", count=3)["picks"]), 3)
        row = d["films"][0]
        self.assertEqual(set(row), {"film_key", "title", "year", "label", "verdict", "your_rating", "agreed", "quote",
                                    "link", "publication"})

    def test_the_yardstick_behind_the_match(self):
        """'expected': how often a typical critic who says Fresh as often as they do would agree with you on the
        films you share. The match is their edge over it, pulled toward 0 by PRIOR_FILMS films' worth - so the
        page can show how it adds up."""
        for name in ("Twin Tina", "Mid Mia", "Five Fay", "Generous Gus", "Anti Andy", "Steady Stu"):
            d = self.detail(name)
            want = (d["agreed"] - d["shared"] * d["expected"]) / (d["shared"] + C.PRIOR_FILMS)
            self.assertAlmostEqual(d["match"], want, delta=0.002, msg=name)
            self.assertGreater(d["expected"], d["chance"], name)                # a typical critic beats a coin
        self.assertIsNone(self.detail("Lone Lou")["expected"])

    def test_strongest_feelings_first(self):
        d = self.detail("Mid Mia")
        self.assertEqual((d["agreed_total"], d["disagreed_total"]), (8, 2))
        # you rated these 9 or 5 - 9 is further from the line between liked and not (6.5) than 5 is
        self.assertEqual([r["your_rating"] for r in d["agreed_on"]], [9.0] * 4 + [5.0] * 4)
        self.assertEqual({(r["title"], r["verdict"]) for r in d["disagreed_on"]}, {("Liked 4", "Rotten"),
                                                                                   ("Meh 4", "Fresh")})
        self.assertEqual(d["when_fresh"], {"films": 5, "your_average": 8.2})

    def test_an_anti_twin_offers_what_they_panned(self):
        d = self.detail("Anti Andy")
        self.assertTrue(d["anti_twin"])
        self.assertEqual(d["picks_kind"], "rotten")
        self.assertEqual(sorted(r["title"] for r in d["picks"]), ["New 0", "New 1", "New 2", "New 3"])
        self.assertTrue(all(r["verdict"] == "Rotten" for r in d["picks"]))
        self.assertEqual(d["agreed"], 0)

    def test_by_name(self):
        d = self.detail("tina")
        self.assertEqual(d["name"], "Twin Tina")
        self.assertEqual(d["matched"]["how"], "partial")
        self.assertEqual(self.detail("Twin Tina")["matched"]["how"], "exact")
        self.assertEqual(self.detail("Twin Tinna")["name"], "Twin Tina")        # a near spelling
        self.assertNotIn("matched", self.detail(self.ids["Twin Tina"]))        # by id: nothing to check
        for unknown in ("Zzqx Blorp", "99999"):
            d = self.detail(unknown)
            self.assertFalse(d["ok"])
            self.assertEqual(d["error"], f"no critic called '{unknown}'")

    def test_no_films_in_common(self):
        d = self.detail("Lone Lou")
        self.assertTrue(d["ok"])
        self.assertEqual((d["shared"], d["agreement"], d["rank"], d["films"]), (0, None, None, []))
        self.assertEqual([r["title"] for r in d["picks"]], ["New 6"])          # (Played 1 you've played)
        small = self.detail("Small Sam")
        self.assertEqual((small["shared"], small["listed"], small["rank"]), (3, False, None))


class PicksTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = critic_catalog()
        cls.keys = keys_by_title(cls.catalog)

    def picks(self, **request):
        return C.answer(self.catalog, dict(view="picks", **request))

    def titles(self, answer):
        return [r["title"] for r in answer["results"]]

    def test_films_your_closest_critics_called_fresh(self):
        a = self.picks()
        self.assertTrue(a["ok"])
        self.assertEqual((a["view"], a["sort"]), ("picks", "critics"))
        m = C.model(self.catalog)
        for r in a["results"]:
            c = r["critics"]
            self.assertGreater(c["fresh"], c["rotten"], r["title"])
            self.assertEqual(c["fresh"] + c["rotten"], len(c["verdicts"]))
            self.assertTrue(all(v["id"] in m.closest for v in c["verdicts"]))
            ranks = [m.closest_rank[v["id"]] for v in c["verdicts"]]
            self.assertEqual(ranks, sorted(ranks))
            self.assertIsNone(self.catalog.films[r["key"]].owner_rating)
            self.assertFalse(self.catalog.films[r["key"]].owner_plays)
            # a recommend result, plus the critics
            for key in ("predicted_rating", "expected_from_scores", "personal_lift", "breakdown", "reasons",
                        "confidence", "people", "key", "title", "year", "libraries"):
                self.assertIn(key, r)
        scores = [r["critics"]["score"] for r in a["results"]]
        self.assertEqual(scores, sorted(scores, reverse=True))                 # the most one-sided first
        self.assertEqual(a["matching_films"], 8)                               # New 0-7 (New 9: 1 Fresh, 1 Rotten)
        self.assertEqual(len(a["results"]), 6)                                 # 2 of New 0-3 (one director)
        self.assertEqual(a["played_matches"], 1)                               # Played 0
        self.assertEqual(a["closest_count"], len(m.closest))
        self.assertEqual(set(a["model"]), {"trained_on", "your_average", "lambda", "scale"})
        self.assertTrue(any("don't use these critics" in n for n in a["notes"]))
        # only what's always true: whether they'd make Watch Next better is the critics test's to say
        self.assertIn(C.NOT_IN_WATCH_NEXT, a["notes"])
        self.assertFalse(any("accurate" in n or "stands out" in n for n in a["notes"]), a["notes"])
        self.assertEqual(a["filter_matches"], 12)                               # New 0-11 (Played 0-1: played)

    def test_films_the_filters_alone_let_through(self):
        """filter_matches: how many films pass the filters whatever the critics said - 0 when nothing on the
        shelf matches them (so it's the filters, not the critics, that leave the list empty)."""
        nothing = self.picks(text="zzqxv")
        self.assertEqual((nothing["results"], nothing["matching_films"], nothing["filter_matches"]), ([], 0, 0))
        panned = self.picks(genre="Drama", decade=2000)                          # New 11: Rotten from Tina
        self.assertEqual((panned["results"], panned["filter_matches"]), ([], 1))
        self.assertEqual(self.picks(text="swordsman")["filter_matches"], 1)
        self.assertEqual(self.picks(include_watched=True)["filter_matches"], 14)  # and the two played
        liked = self.picks(like="New 4")
        self.assertGreaterEqual(liked["filter_matches"], liked["matching_films"])

    def test_libraries_and_genres_left_out(self):
        """exclude_libraries / exclude_genres (Watch Next's 'Never suggest films from'): never a pick; left_out
        says how many picks they took away, and filter_matches doesn't count what they left out."""
        a = self.picks(exclude_libraries=["asia"], max_per_director=0)
        self.assertEqual(sorted(self.titles(a)), ["New 1", "New 2", "New 4", "New 5", "New 7"])
        self.assertEqual((a["left_out"], a["filter_matches"]), (3, 8))           # New 0, 3, 6 (and New 9)
        g = self.picks(exclude_genres="Drama", max_per_director=0)
        self.assertEqual(sorted(self.titles(g)), ["New 0", "New 2", "New 4", "New 6"])
        self.assertEqual(g["left_out"], 4)
        self.assertNotIn("left_out", self.picks())                               # (only when something is)
        gone = self.picks(exclude_libraries=["Asia", "Movies"])
        self.assertEqual((gone["results"], gone["filter_matches"], gone["left_out"]), ([], 0, 8))
        self.assertEqual(gone["played_matches"], 0)                              # (Played 0 is left out too)
        # the model behind the predictions still learns from them
        self.assertEqual(gone["model"]["trained_on"], self.picks()["model"]["trained_on"])

    def test_films_played_on_an_account_counted_as_yours(self):
        """also_seen_by (Settings > Your collection): a film played on one of those accounts is seen - not a pick,
        and not in a critic's 'liked that you haven't seen' either."""
        new1 = self.catalog.films[self.keys["New 1"]]
        lone = self.catalog.films[self.keys["New 6"]]
        new1.played_by, lone.played_by = {5: 1}, {5: 2}
        try:
            mine = self.picks(max_per_director=0)
            seen = self.picks(max_per_director=0, also_seen_by=[5])
            self.assertIn("New 1", self.titles(mine))
            self.assertNotIn("New 1", self.titles(seen))
            self.assertNotIn("New 6", self.titles(seen))
            self.assertEqual(seen["played_matches"], mine["played_matches"] + 2)          # (New 1 and New 6)
            self.assertEqual(self.titles(self.picks(max_per_director=0, also_seen_by=[6])), self.titles(mine))
            self.assertIn("New 1", self.titles(self.picks(max_per_director=0, also_seen_by=[5],
                                                          include_watched=True)))
            self.assertFalse(self.picks(also_seen_by="all")["ok"])
            lou = C.answer(self.catalog, {"critic": "Lone Lou"})
            self.assertEqual([r["title"] for r in lou["picks"]], ["New 6"])
            lou = C.answer(self.catalog, {"critic": "Lone Lou", "also_seen_by": [5]})
            self.assertEqual(lou["picks"], [])
            self.assertFalse(C.answer(self.catalog, {"critic": "Lone Lou", "also_seen_by": {"a": 5}})["ok"])
        finally:
            new1.played_by, lone.played_by = {}, {}

    def test_filters(self):
        self.assertEqual(sorted(self.titles(self.picks(genre="Drama", max_per_director=0))),
                         ["New 1", "New 3", "New 5", "New 7"])
        self.assertEqual(sorted(self.titles(self.picks(library="Asia", max_per_director=0))), ["New 0", "New 3",
                                                                                               "New 6"])
        self.assertEqual(sorted(self.titles(self.picks(decade=1990, max_per_director=0))),
                         sorted(f"New {j}" for j in range(8)))
        self.assertEqual(self.titles(self.picks(decade=2000)), [])
        self.assertEqual(self.titles(self.picks(with_id="p:Newbie 5")), ["New 5"])
        self.assertEqual(self.titles(self.picks(text="swordsman")), ["New 2"])
        watched = self.titles(self.picks(include_watched=True, max_per_director=0))
        self.assertIn("Played 0", watched)
        self.assertNotIn("played_matches", self.picks(include_watched=True))
        self.assertEqual(len(self.titles(self.picks(max_per_director=0))), 8)
        self.assertEqual(len(self.picks(count=1)["results"]), 1)
        self.assertEqual(len(self.picks(count=0)["results"]), 1)                 # clamped
        liked = self.picks(like="New 4")                                        # only films much like it
        self.assertNotIn("New 4", self.titles(liked))
        self.assertTrue(all("likeness" in r and r["likeness"] >= 0.1 for r in liked["results"]))

    def test_errors_say_what_recommend_says(self):
        for request in ({"like": "Zzqx Nothing"}, {"with": "Nobody Atall"}, {"like_key": "nope"},
                        {"with_id": "nope"}):
            ours = self.picks(**request)
            theirs = R.recommend(self.catalog, request)
            self.assertFalse(ours["ok"], request)
            self.assertEqual(ours["error"], theirs["error"], request)

    def test_through_ask(self):
        a = handle({"action": "critics", "view": "picks", "count": 2}, catalog=self.catalog)
        self.assertTrue(a["ok"])
        self.assertEqual((a["action"], len(a["results"])), ("critics", 2))


class FilmVerdictTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = critic_catalog()
        cls.keys = keys_by_title(cls.catalog)

    def verdicts(self, title):
        return C.film_verdicts(self.catalog, self.keys[title])

    def test_summaries(self):
        both = self.verdicts("New 0")
        self.assertEqual(both["summary"], "2 of your closest critics reviewed it - both Fresh. 2 of the 3 reviews "
                                          "Plex keeps are Fresh (Tomatometer 70%).")
        one = self.verdicts("New 8")
        self.assertEqual(one["summary"], "1 of your closest critics reviewed it (Twin Tina): Rotten. The one review "
                                         "Plex keeps is Rotten (Tomatometer 70%).")
        mixed = self.verdicts("New 9")
        self.assertEqual(mixed["summary"], "2 of your closest critics reviewed it: 1 Fresh, 1 Rotten. 1 of the 2 "
                                           "reviews Plex keeps is Fresh (Tomatometer 70%).")
        none = self.verdicts("Played 1")
        self.assertEqual(none["summary"], "None of your closest critics reviewed it. The one review Plex keeps is "
                                          "Fresh (Tomatometer 70%).")
        self.assertEqual((none["closest"], [r["name"] for r in none["others"]]), ([], ["Lone Lou"]))

    def test_shape_and_order(self):
        v = self.verdicts("New 9")
        self.assertTrue(v["ok"])
        self.assertEqual((v["title"], v["label"], v["reviews"], v["fresh"], v["rt_critic"], v["your_rating"]),
                         ("New 9", "New 9 (1999)", 2, 1, 70, None))
        self.assertEqual([r["name"] for r in v["closest"]], ["Twin Tina", "Steady Stu"])      # by rank
        self.assertEqual([r["rank"] for r in v["closest"]], [1, 2])
        self.assertEqual(set(v["closest"][0]), {"id", "name", "publication", "verdict", "quote", "link", "rank",
                                                "shared", "agreed", "agreement", "match"})
        self.assertEqual(v["stand_out"], "real")
        rated = self.verdicts("Liked 3")
        self.assertEqual(rated["your_rating"], 9.0)
        self.assertEqual(rated["closest"][0]["name"], "Twin Tina")
        others = [r["rank"] for r in rated["others"]]
        self.assertTrue(all(r is None for r in others))

    def test_never_raises(self):
        for key in ("nope", None, "", 42):
            v = C.film_verdicts(self.catalog, key)
            self.assertFalse(v["ok"])
            self.assertEqual((v["summary"], v["closest"], v["others"]), ("", [], []))
        self.assertEqual(C.film_verdicts(self.catalog, "nope")["error"], "no film with key 'nope'")
        self.assertFalse(C.film_verdicts(None, "f1")["ok"])

    def test_through_ask(self):
        a = handle({"action": "critics", "title": "new 9"}, catalog=self.catalog)
        self.assertTrue(a["ok"])
        self.assertEqual((a["view"], a["title"]), ("film", "New 9"))
        self.assertEqual(a["matched"]["found"], "New 9 (1999)")
        by_key = handle({"action": "critics", "film_key": self.keys["New 9"]}, catalog=self.catalog)
        self.assertEqual(by_key["summary"], a["summary"])
        missing = handle({"action": "critics", "title": "Zzqx Nothing"}, catalog=self.catalog)
        self.assertEqual(missing["error"], "no film matching 'Zzqx Nothing'")


class NamesTests(unittest.TestCase):
    def test_every_critic_once_in_order(self):
        catalog = critic_catalog()
        names = C.critic_names(catalog)
        self.assertEqual(len(names), 28)
        self.assertEqual(len({n["id"] for n in names}), 28)
        self.assertEqual([n["name"] for n in names], sorted((n["name"] for n in names), key=str.casefold))
        tina = next(n for n in names if n["name"] == "Twin Tina")
        self.assertEqual((tina["publication"], tina["reviews"], tina["shared"]), ("Tina Times", 53, 40))
        for n in names:                                                     # every id leads to that critic
            d = C.answer(catalog, {"critic": n["id"]})
            self.assertEqual((d["ok"], d["name"]), (True, n["name"]))
        view = handle({"action": "critics", "view": "names"}, catalog=catalog)
        self.assertEqual(view["critics"], names)

    def test_ids_given_and_accents(self):
        catalog = plain_catalog()
        k = keys_by_title(catalog)
        C.use_reviews(catalog, [
            {"critic": "Zoë Àlvarez", "critic_id": "501", "film_key": k["Liked 0"], "verdict": "Fresh"},
            {"critic": "Zoë Àlvarez", "critic_id": "501", "film_key": k["Liked 0"], "verdict": "Rotten"},   # twice
            {"critic": "Zoë Àlvarez", "critic_id": "777", "film_key": k["Meh 0"], "verdict": "Rotten"},     # a namesake
            {"critic": "No Verdict", "film_key": k["Meh 0"], "verdict": "Upright"},                        # skipped
            {"critic": "Ghost", "film_key": "not-a-film", "verdict": "Fresh"},                             # skipped
        ])
        names = C.critic_names(catalog)
        self.assertEqual([(n["id"], n["name"]) for n in names], [("501", "Zoë Àlvarez"), ("777", "Zoë Àlvarez")])
        m = C.model(catalog)
        self.assertEqual(m.reviews, 2)
        self.assertTrue(m.by_film[k["Liked 0"]][0].fresh)                     # the first review of a film is kept
        self.assertEqual(C.answer(catalog, {"critic": "zoe alvarez"})["id"], "501")   # accents folded


class EvaluateTests(unittest.TestCase):
    def test_no_peeking(self):
        """Critics tossing coins can't predict you. Picked with every rating known, the 'closest' coins would seem
        to (their luck on each film is part of why they were picked); picked without the film being judged, as
        evaluate does, they don't."""
        loo, peeking = [], []
        for seed in range(6):
            catalog = critic_catalog(named=False, coins=40, seed=seed, coin_films=6)
            answer = C.evaluate(catalog, {"model_check": False})
            self.assertTrue(answer["ok"], answer)
            loo.append(answer["methods"]["Your closest critics"]["rank_agreement"])
            peeking.append(in_sample_rank(catalog))
        mean = lambda v: sum(v) / len(v)
        self.assertLess(mean(loo), 0.25, loo)
        self.assertGreater(mean(peeking), 0.4, peeking)
        self.assertGreater(mean(peeking) - mean(loo), 0.25)

    def test_shape(self):
        catalog = critic_catalog(named=False, coins=20, seed=7, coin_films=15)
        a = C.evaluate(catalog, {})
        self.assertTrue(a["ok"])
        self.assertLessEqual(a["films_covered"], a["films_tested"])
        self.assertEqual(a["rated_films"], 40)
        self.assertEqual(list(a["methods"]), ["Your closest critics", "All critics", "Rotten Tomatoes Tomatometer",
                                              "IMDb rating", "Watch Next's model"])
        for scores in a["methods"].values():
            self.assertEqual(set(scores), {"rank_agreement", "top_fifth_you_rated_8_plus"})
            self.assertTrue(-1 <= scores["rank_agreement"] <= 1)
            self.assertTrue(0 <= scores["top_fifth_you_rated_8_plus"] <= 1)
        self.assertEqual(set(a["picks"]), {"closest", "all", "liked_overall"})
        # the yardsticks: the 8+ share of the films tested, how many verdicts a film, critics picked blind
        covered = a["films_covered"]
        self.assertIn(round(a["share_you_rated_8_plus"] * covered), range(covered + 1))
        per_film = a["verdicts_per_film"]
        self.assertEqual(per_film["closest_one"] + per_film["closest_two"] + per_film["closest_more"],
                         a["films_covered"])
        self.assertGreater(per_film["all_critics_average"], 1)
        panels = a["random_panels"]
        self.assertEqual((panels["panels"], panels["size"]), (C.RANDOM_PANELS, a["closest_count"]))
        self.assertTrue(-1 <= panels["rank_agreement"] <= 1)
        self.assertTrue(0 <= panels["at_or_above_closest"] <= 1)
        self.assertEqual(C.evaluate(catalog, {"model_check": False})["random_panels"], panels)   # the same draw
        self.assertEqual(set(a["picks"]["closest"]), {"mostly_fresh", "liked", "average", "mostly_rotten",
                                                      "liked_when_rotten", "average_when_rotten"})
        check = a["model_check"]
        self.assertFalse(check["helps"])                                    # coins don't help the model
        self.assertEqual(set(check["with_critics"]), {"mean_error", "rms_error", "rank_agreement",
                                                      "top_fifth_you_rated_8_plus"})
        self.assertFalse(a["in_watch_next"])
        self.assertGreaterEqual(a["seconds"], 0)
        self.assertNotIn("model_check", C.evaluate(catalog, {"model_check": False}))
        self.assertNotIn("model_check", C.evaluate(catalog, {"model_check": "false"}))
        # Watch Next's model is tested exactly as its own accuracy test does it (the same folds)
        theirs = R.evaluate(catalog, {"lambdas": [R.DEFAULT_LAMBDA]})["model_by_lambda"]["32"]
        self.assertAlmostEqual(check["without_critics"]["mean_error"], theirs["mean_error"], delta=0.006)
        self.assertAlmostEqual(check["without_critics"]["rank_agreement"], theirs["rank_agreement"], delta=0.001)

    def test_top_fifth_shares_ties(self):
        self.assertEqual(C._top_fifth([3, 2, 1, 0, 0], [9, 5, 5, 5, 5]), 1.0)
        # five films tied for first, one slot: a fifth of a chance each - 2 of them rated 8+
        self.assertAlmostEqual(C._top_fifth([1, 1, 1, 1, 1], [9, 9, 5, 5, 5]), 0.4)

    def test_twins_are_found(self):
        a = C.evaluate(critic_catalog(), {"model_check": False})
        self.assertGreater(a["methods"]["Your closest critics"]["rank_agreement"], 0.6)
        self.assertGreaterEqual(a["picks"]["closest"]["liked"], 0.9)
        self.assertEqual((a["films_covered"], a["share_you_rated_8_plus"]), (40, 0.5))   # 20 rated 9, 20 rated 5
        # ...and choosing them by how they agree with you beats choosing critics blind
        panels = a["random_panels"]
        self.assertLess(panels["rank_agreement"], a["methods"]["Your closest critics"]["rank_agreement"] - 0.2)
        self.assertLess(panels["at_or_above_closest"], 0.1)

    def test_too_few_covered(self):
        catalog = plain_catalog()
        k = keys_by_title(catalog)
        rows = [{"critic": f"Solo {i}", "film_key": k[f"{'Liked' if i < 20 else 'Meh'} {i % 20}"],
                 "verdict": "Fresh" if i < 20 else "Rotten"} for i in range(40)]
        C.use_reviews(catalog, rows)                                    # one review each: nobody is listed
        a = C.evaluate(catalog, {})
        self.assertFalse(a["ok"])
        self.assertEqual(a["reason"], "few_covered")


class DatabaseTests(unittest.TestCase):
    """Reviews read from a (fixture) Plex database."""

    @classmethod
    def setUpClass(cls):
        import json
        from test_projectionist import MATRIX_GUID, Fixture, build_fixture
        cls.tmp = tempfile.mkdtemp(prefix="critics-test-")
        cls.db = os.path.join(cls.tmp, "library.db")
        build_fixture(cls.db)
        f = Fixture.__new__(Fixture)
        f.con, f._tag_ids = sqlite3.connect(cls.db), {}
        review = lambda image, source, link=None: {k: v for k, v in (("at:image", image), ("at:source", source),
                                                                        ("at:link", link)) if v is not None}
        # a second copy of The Matrix, in Classics, carrying the same review (and a critic of its own)
        copy = f.insert("metadata_items", id=1850, library_section_id=2, metadata_type=1, guid=MATRIX_GUID,
                        title="The Matrix", year=1999)
        nora = f.con.execute("SELECT id FROM tags WHERE tag = 'Nora Clark' AND tag_type = 10 ORDER BY id").fetchone()[0]
        f.insert("taggings", metadata_item_id=copy, tag_id=nora, index=0, text="Clever enough.",
                 extra_data=json.dumps(review("rottentomatoes://image.review.fresh", "USA Today")))
        f.insert("tags", id=901, tag="Classic Critic", tag_type=10)
        f.insert("taggings", metadata_item_id=copy, tag_id=901, index=1, text="Still holds up.",
                 extra_data=json.dumps(review("rottentomatoes://image.review.fresh", "Old Paper", "https://x/c")))
        # Hong Kong Action (Movies): a Rotten one, an old URL-encoded one, one with no verdict, one with no link
        f.insert("tags", id=902, tag="Rotten Rita", tag_type=10)
        f.insert("taggings", metadata_item_id=700, tag_id=902, index=0, text="Nope.",
                 extra_data=json.dumps(review("rottentomatoes://image.review.rotten", "Daily Pan", "https://x/r")))
        f.insert("tags", id=903, tag="Old Olga", tag_type=10)
        f.insert("taggings", metadata_item_id=700, tag_id=903, index=1, text="Fine.",
                 extra_data="at%3Aimage=rottentomatoes%3A%2F%2Fimage.review.fresh&at%3Asource=Old%20Times")
        f.insert("tags", id=904, tag="Blank Bob", tag_type=10)
        f.insert("taggings", metadata_item_id=700, tag_id=904, index=2, text="?",
                 extra_data=json.dumps({"at:source": "Nowhere"}))
        f.con.commit()
        f.con.close()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_reading(self):
        from projectionist.catalog import load
        catalog = load(self.db)
        m = C.model(catalog)
        self.assertTrue(C.ready(catalog))
        names = {c.name for c in m.critics.values()}
        self.assertEqual(names, {"Nora Clark", "Classic Critic", "Rotten Rita", "Old Olga"})   # Blank Bob: no verdict
        matrix = C.film_verdicts(catalog, "plex://movie/5d776827880197001ec90904")
        self.assertEqual([r["name"] for r in matrix["others"]], ["Nora Clark", "Classic Critic"])  # once, in order
        self.assertEqual(matrix["others"][0]["publication"], "USA Today")
        self.assertEqual(matrix["others"][0]["link"], "https://x/r")
        hk = C.film_verdicts(catalog, "plex://movie/hk1")
        by_name = {r["name"]: r for r in hk["others"]}
        self.assertEqual((by_name["Rotten Rita"]["verdict"], by_name["Old Olga"]["verdict"]), ("Rotten", "Fresh"))
        self.assertEqual((by_name["Old Olga"]["publication"], by_name["Old Olga"]["link"]), ("Old Times", ""))
        self.assertEqual(hk["your_rating"], 8.0)
        ids = {r["name"]: r["id"] for r in C.critic_names(catalog)}
        self.assertEqual(ids["Rotten Rita"], "902")                          # Plex's tag id
        self.assertEqual(C.answer(catalog, {"critic": "902"})["name"], "Rotten Rita")
        self.assertEqual(C.answer(catalog, {})["reason"], "few_ratings")      # one rated film: nothing to compare

    def test_a_library_choice_gets_its_own_reviews(self):
        from projectionist.catalog import load
        catalog = load(self.db, library_ids=[2])
        names = {c.name for c in C.model(catalog).critics.values()}
        self.assertEqual(names, {"Nora Clark", "Classic Critic"})            # not Hong Kong Action's (Movies)

    def test_a_missing_or_broken_database(self):
        from projectionist.catalog import load
        catalog = load(self.db)
        gone = os.path.join(self.tmp, "gone.db")
        catalog.source = gone
        answer = C.overview(catalog, {})
        self.assertEqual(answer["reason"], "no_reviews")
        self.assertIn("isn't there any more", answer["read_error"])
        broken = os.path.join(self.tmp, "broken.db")
        with open(broken, "wb") as fh:
            fh.write(b"not a database at all" * 100)
        catalog = load(self.db)
        catalog.source = broken
        answer = C.overview(catalog, {})
        self.assertEqual(answer["reason"], "no_reviews")
        self.assertTrue(answer["read_error"])
        self.assertEqual(C.film_verdicts(catalog, "plex://movie/hk1")["summary"],
                         "Plex has no critic reviews for this film.")

    def test_a_cancelled_job_stops(self):
        from projectionist.catalog import load
        catalog = load(self.db)
        job = jobs.Job("test")
        job.cancel()
        with jobs.running(job):
            with self.assertRaises(jobs.Cancelled):
                C.model(catalog)
        self.assertFalse(C.ready(catalog))                                   # nothing half-built is kept
        self.assertNotIn(C.RAW, catalog.cache)
        self.assertTrue(C.model(catalog).critics)                            # and it builds fine afterwards


if __name__ == "__main__":
    unittest.main()
