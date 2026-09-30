"""Tests for projectionist.filmpage: the Film page's answer (film_page), its hooks into the other backbones, the
collection-wide search, the start page's suggestions, and the ask actions 'film' and 'search'. Catalogs are built
in memory (make_catalog) or read from the fixture database."""

import os
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from projectionist import filmpage as FP  # noqa: E402
from projectionist import jobs  # noqa: E402
from test_projectionist import MATRIX_GUID, build_fixture, make_catalog  # noqa: E402

HOOK_MODULES = ("projectionist.critics", "projectionist.habits", "projectionist.doctor")


def without_hooks():
    """None of the other backbones exist (as before they're built)."""
    return mock.patch.dict(sys.modules, {name: None for name in HOOK_MODULES})


def fake_module(name, **functions):
    module = types.ModuleType(name)
    for k, v in functions.items():
        setattr(module, k, v)
    return module


def key_of(catalog, title, year=None):
    return next(f.key for f in catalog.films.values() if f.title == title and (year is None or f.year == year))


def search_catalog():
    usa, hk, jp = ["United States of America"], ["Hong Kong"], ["Japan"]
    return make_catalog([
        dict(title="Police Story", year=1985, cast=["Jackie Chan", "Maggie Cheung"], countries=hk,
             libraries=["Movies-World"],
             genres=["Action"], studio="Golden Harvest", imdb_rating=7.6, owner_rating=9.0, owner_plays=1),
        dict(title="Project A", year=1983, cast=["Jackie Chan", "Sammo Hung"], countries=hk, genres=["Action"],
             studio="Golden Harvest", imdb_rating=7.4),
        dict(title="Armour of God", year=1986, cast=["Jackie Chan"], countries=hk, genres=["Action"],
             studio="Golden Harvest", imdb_rating=7.0),
        dict(title="Fist of Legend", year=1994, cast=["Jet Li", "Jacky Cheung"], countries=hk, genres=["Action"]),
        dict(title="As Tears Go By", year=1988, cast=["Andy Lau", "Jacky Cheung"], countries=hk, genres=["Crime"]),
        dict(title="Charlie Chan at the Opera", year=1936, cast=["Warner Oland"], countries=usa, genres=["Mystery"],
             imdb_rating=6.6),
        dict(title="Alien", year=1979, cast=["Sigourney Weaver", "Tom Skerritt"], directors=["Ridley Scott"],
             countries=usa + ["United Kingdom"], genres=["Horror", "Science Fiction"],
             studio="Brandywine Productions", collections=["IMDB Top 250"], imdb_rating=8.4),
        dict(title="Aliens", year=1986, cast=["Sigourney Weaver", "Michael Biehn"], directors=["James Cameron"],
             countries=usa, genres=["Action", "Science Fiction"], imdb_rating=8.4),
        dict(title="Seven Samurai", year=1954, cast=["Toshiro Mifune"], directors=["Akira Kurosawa"], countries=jp,
             imdb_rating=8.6),
        dict(title="Rashomon", year=1950, cast=["Toshiro Mifune"], directors=["Akira Kurosawa"], countries=jp),
        dict(title="Zatoichi's Vengeance", year=1966, cast=["Shintarō Katsu"], countries=jp,
             collections=["Zatoichi the Blind Swordsman"]),
        dict(title="Zatoichi and the Chest of Gold", year=1964, cast=["Shintarō Katsu"], countries=jp,
             collections=["Zatoichi the Blind Swordsman"]),
        dict(title="Dracula", year=1931, cast=["Bela Lugosi"], countries=usa, imdb_rating=7.4, owner_rating=6.0),
        dict(title="Drácula", year=1931, cast=["Carlos Villarías"], countries=usa, imdb_rating=7.3),
        dict(title="The Five Venoms", year=1978, titles=["The Five Venoms", "五毒"], cast=["Chiang Sheng"],
             directors=["Chang Cheh"], studio="Shaw Brothers", countries=hk),
        dict(title="Ip Man", year=2008, cast=["Donnie Yen"], countries=hk, libraries=["Movies-World"]),
        dict(title="X-Men", year=2000, cast=["Hugh Jackman"], countries=usa),
        dict(title="2001: A Space Odyssey", year=1968, directors=["Stanley Kubrick"], countries=usa),
        dict(title="Hanging by a Thread", year=1979, cast=["Sam Groom"], countries=usa),
    ], libraries=("Movies", "Movies-World"))


# ---------------------------------------------------------------------------------------------------------
class FilmPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = make_catalog([
            dict(title="Main Film", year=1999, cast=["Ann", "Bob", "Cat"], directors=["Dee"], genres=["Drama"],
                 countries=["United States of America"], studio="Big Studio", collections=["Faves"],
                 imdb_rating=7.0, rt_critic=59, rt_audience=60, owner_rating=7.5, owner_plays=2,
                 titles=["Main Film", "Film Principal", "MAIN FILM"], tagline="A tagline.", runtime_min=117,
                 editions=["Criterion", "Arrow Video"], added_at=1718628256),
            dict(title="Other", year=2001, cast=["Bob"], directors=["Dee"], rt_critic=60),
            dict(title="Dee Directs", year=2003, cast=["Eve"], directors=["Dee"]),
            dict(title="Empty", year=2004),
        ])
        film = cls.catalog.films[key_of(cls.catalog, "Main Film")]
        film.plex_ids = [11, 12, 13]
        film.libraries = ["Movies", "Classics"]
        # a second person called John Smith, besides the one in the cast
        from projectionist.catalog import Credit, Person
        for pid, order in (("p:kd1", 4), ("p:kd2", 5)):
            c = Credit(pid, "John Smith", "Himself", order)
            film.cast.append(c)
            cls.catalog.people[pid] = Person(pid, "John Smith", acted={film.key: c})
        cls.catalog.people["p:kd1"].acted[key_of(cls.catalog, "Other")] = c
        cls.key = film.key

    def page(self, key=None, parts=None):
        with without_hooks():
            return FP.film_page(self.catalog, key or self.key, parts=parts)

    def test_unknown_key_is_an_answer(self):
        for key in ("no-such-film", "", None, "plex://movie/x"):
            answer = FP.film_page(self.catalog, key)
            self.assertFalse(answer["ok"])
            self.assertIn("no film with the key", answer["error"])

    def test_every_part(self):
        page = self.page()
        self.assertTrue(page["ok"])
        self.assertEqual(page["parts"], list(FP.PARTS))
        for part in FP.PARTS:
            self.assertIn(part, page)
            self.assertIn(part, page["took_ms"])
        film = page["film"]
        self.assertEqual((film["key"], film["label"], film["copies"]), (self.key, "Main Film (1999)", 3))
        self.assertEqual(film["titles"], ["Film Principal"])       # (a spelling of the main title isn't another)
        self.assertEqual((film["tagline"], film["runtime_min"], film["added"]), ("A tagline.", 117, "2024-06-17"))
        self.assertTrue(film["watched"])
        self.assertEqual(film["collections"], ["Faves"])
        for part in ("critics", "files", "issues"):
            self.assertEqual(page[part], {"available": False})
        self.assertEqual(page["history"]["plays"], 2)

    def test_choosing_parts(self):
        core = self.page(parts="core")
        self.assertEqual(core["parts"], list(FP.CORE))
        self.assertNotIn("similar", core)
        one = self.page(parts=["similar"])
        self.assertEqual(one["parts"], ["film", "similar"])      # the film always comes along
        self.assertEqual(self.page(parts="ratings, people")["parts"], ["film", "ratings", "people"])
        with self.assertRaises(ValueError):
            FP.film_page(self.catalog, self.key, parts=["film", "posters"])
        from projectionist.ask import handle
        answer = handle({"action": "film", "film_key": self.key, "parts": ["posters"]}, catalog=self.catalog)
        self.assertFalse(answer["ok"])
        self.assertIn("bad request", answer["error"])

    def test_ratings(self):
        r = self.page(parts="core")["ratings"]
        rows = {x["source"]: x for x in r["rows"]}
        self.assertEqual(list(rows), ["you", "imdb", "rt_critic", "rt_audience"])     # no TMDb score: left out
        self.assertEqual(r["missing"], ["tmdb"])
        self.assertEqual((rows["you"]["text"], rows["you"]["out_of_10"]), ("7.5 / 10", 7.5))
        self.assertEqual((rows["rt_critic"]["verdict"], rows["rt_critic"]["out_of_10"], rows["rt_critic"]["text"]),
                         ("Rotten", 5.9, "59%"))
        self.assertNotIn("verdict", rows["rt_audience"])
        self.assertEqual(rows["rt_audience"]["out_of_10"], 6.0)
        other = FP.film_page(self.catalog, key_of(self.catalog, "Other"), parts="core")["ratings"]
        self.assertEqual(other["rows"][0]["verdict"], "Fresh")                        # 60% is Fresh
        self.assertEqual(r["note"], "You gave it 7.5 - 0.5 above IMDb's 7. Plex has no score from TMDb for it.")
        self.assertEqual((r["yours"], r["your_average"], r["rated_films"]), (7.5, 7.5, 1))
        empty = FP.film_page(self.catalog, key_of(self.catalog, "Empty"), parts="core")["ratings"]
        self.assertEqual(empty["rows"], [])
        self.assertIn("You haven't rated it.", empty["note"])
        self.assertIn("IMDb, TMDb or Rotten Tomatoes", empty["note"])
        self.assertFalse(r["yours_from_earlier_edition"])
        self.assertNotIn("earlier_edition", rows["you"])

    def test_a_rating_left_on_an_earlier_edition_says_so(self):
        c = make_catalog([dict(title="Project A", year=1983, cast=["Jackie Chan"], owner_rating=8.0,
                               imdb_rating=7.4)])
        film = next(iter(c.films.values()))
        film.owner_rating_from_earlier_edition = True
        r = FP.film_page(c, film.key, parts="core")["ratings"]
        self.assertTrue(r["yours_from_earlier_edition"])
        self.assertTrue(r["rows"][0]["earlier_edition"])
        self.assertIn(FP.EARLIER_EDITION, r["note"])
        self.assertIn("Plex shows this copy as unrated", r["note"])

    def test_people_in_billing_order(self):
        people = self.page(parts="core")["people"]
        self.assertEqual([c["name"] for c in people["cast"]], ["Ann", "Bob", "Cat", "John Smith", "John Smith"])
        self.assertEqual([c["order"] for c in people["cast"]], [1, 2, 3, 4, 5])
        bob = people["cast"][1]
        self.assertEqual((bob["id"], bob["role"], bob["films_here"], bob["label"]), ("p:Bob", "role 2", 2, "Bob"))
        dee = people["directors"][0]
        self.assertEqual((dee["name"], dee["films_here"]), ("Dee", 3))
        self.assertTrue(dee["mostly_directs"])
        kevins = people["cast"][3:]
        self.assertEqual({k["id"] for k in kevins}, {"p:kd1", "p:kd2"})
        labels = [k["label"] for k in kevins]
        self.assertTrue(all(label.startswith("John Smith (") for label in labels), labels)
        self.assertEqual(len(set(labels)), 2)                     # two people, told apart
        self.assertEqual(people["cast_count"], 5)

    def test_copies_in_words(self):
        self.assertEqual(FP.copies_text(3, ["Criterion", "Arrow Video"]),
                         "3 copies: Criterion, Arrow Video and one without an edition name")
        self.assertEqual(FP.copies_text(2, ["Theatrical", "Director's Cut"]),
                         "2 copies: Theatrical and Director's Cut")
        self.assertEqual(FP.copies_text(1, []), "One copy")
        self.assertEqual(FP.copies_text(1, ["Criterion"]), "One copy (Criterion)")
        self.assertEqual(FP.copies_text(3, []), "3 copies, none with an edition name")
        self.assertEqual(FP.copies_text(4, ["A"]), "4 copies: A and 3 without edition names")

    def test_finding_a_film(self):
        c = search_catalog()
        film, how, _ = FP.find(c, key_of(c, "Alien"))
        self.assertEqual((film.title, how), ("Alien", "key"))
        film, how, _ = FP.find(c, None, "Drácula (1931)")
        self.assertEqual((film.title, how), ("Drácula", "exact"))
        film, how, _ = FP.find(c, None, "Dracula (1931)")
        self.assertEqual((film.title, how), ("Dracula", "exact"))
        film, how, _ = FP.find(c, None, "五毒 (1978)")                 # another title, with the year
        self.assertEqual((film.title, how), ("The Five Venoms", "exact"))
        film, how, _ = FP.find(c, None, "seven samurai")
        self.assertEqual(film.title, "Seven Samurai")
        self.assertEqual(FP.find(c, "nope", None)[0], None)
        self.assertEqual(FP.find(c, None, "Zzqxv (1901)")[0], None)


# ---------------------------------------------------------------------------------------------------------
class SimilarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        specs = []
        for i in range(20):
            specs.append(dict(title=f"Loved {i}", year=1975 + i, genres=["Action"], directors=["Ann Auteur"],
                              cast=["Star A", f"Extra {i}"], imdb_rating=7.0, owner_rating=9.0, runtime_min=95))
            specs.append(dict(title=f"Meh {i}", year=1975 + i, genres=["Drama"], directors=["Bob Bland"],
                              cast=["Star B", f"Other {i}"], imdb_rating=7.0, owner_rating=4.0, runtime_min=130))
        specs += [
            dict(title="New Ann", year=1999, genres=["Action"], directors=["Ann Auteur"], cast=["Star A"],
                 imdb_rating=7.0, runtime_min=90),
            dict(title="Second Ann", year=2001, genres=["Action"], directors=["Ann Auteur"], cast=["Star A"],
                 imdb_rating=6.9, runtime_min=92),
            dict(title="Third Ann", year=2002, genres=["Action"], directors=["Ann Auteur"], cast=["Star A"],
                 imdb_rating=6.8, runtime_min=93),
            dict(title="Fourth Ann", year=2004, genres=["Action"], directors=["Ann Auteur"], cast=["Star A"],
                 imdb_rating=6.7, runtime_min=94),
            dict(title="Seen Ann", year=2003, genres=["Action"], directors=["Ann Auteur"], cast=["Star A"],
                 imdb_rating=7.0, owner_plays=1),
            dict(title="Blank", year=2005),
        ]
        cls.specs = specs
        cls.catalog = make_catalog(specs)

    def similar(self, title, catalog=None):
        catalog = catalog or self.catalog
        return FP.film_page(catalog, key_of(catalog, title), parts=["similar"])["similar"]

    def test_from_watch_nexts_model(self):
        s = self.similar("New Ann")
        self.assertTrue(s["ok"])
        self.assertEqual(s["source"], "recommender")
        unseen = [x["title"] for x in s["unseen"]]
        self.assertIn("Second Ann", unseen)
        self.assertNotIn("Seen Ann", unseen)                      # played: not 'not seen yet'
        self.assertFalse(any(t.startswith(("Loved", "Meh")) for t in unseen))     # rated
        self.assertTrue(all(x["likeness"] >= 0.1 and x["predicted_rating"] is not None for x in s["unseen"]))
        self.assertGreaterEqual(len(s["seen"]), 3)
        self.assertTrue(all(x["title"].startswith("Loved") and x["your_rating"] == 9.0 for x in s["seen"][:3]))
        self.assertEqual(s["seen"], sorted(s["seen"], key=lambda x: -x["likeness"]))
        self.assertEqual(set(s["seen"][0]), {"key", "title", "year", "label", "likeness", "your_rating"})
        p = s["prediction"]
        self.assertGreater(p["predicted_rating"], p["your_average"])          # she's one you love
        self.assertEqual(p["trained_on"], 40)
        self.assertIsNone(self.similar("Loved 3")["prediction"])            # rated: no prediction

    def test_likeness_alone_with_too_few_ratings(self):
        few = make_catalog([dict(title=f"F{i}", year=1990, genres=["Action"], cast=["Star"], owner_rating=8.0)
                            for i in range(5)] + [dict(title="Target", year=1991, genres=["Action"], cast=["Star"]),
                                                  dict(title="Also", year=1992, genres=["Action"], cast=["Star"])]
                           + [dict(title=f"U{i}", year=2010, genres=["Drama"], cast=[f"U{i}"]) for i in range(3)])
        s = self.similar("Target", few)
        self.assertTrue(s["ok"])
        self.assertEqual(s["source"], "likeness")
        self.assertIsNone(s["prediction"])
        self.assertIn("Likeness only", s["note"])
        self.assertEqual([x["title"] for x in s["unseen"]], ["Also"])
        self.assertEqual(len(s["seen"]), 5)
        self.assertTrue(all(x["your_rating"] == 8.0 for x in s["seen"]))

    def test_nothing_to_compare(self):
        s = self.similar("Blank")
        self.assertEqual((s["unseen"], s["seen"], s["source"]), ([], [], "none"))
        self.assertIn("too little", s["note"])
        # a studio no other film has is nothing to compare either (only the decade and library would be left)
        c = make_catalog(list(self.specs) + [dict(title="Lonely", year=1999, studio="Only Mine Productions",
                                                  imdb_rating=7.0)])
        film = c.films[key_of(c, "Lonely")]
        self.assertFalse(FP.comparable(c, film))
        s = self.similar("Lonely", c)
        self.assertEqual((s["unseen"], s["seen"], s["source"], s["prediction"]), ([], [], "none", None))
        self.assertTrue(FP.comparable(c, c.films[key_of(c, "New Ann")]))

    def test_films_played_on_an_account_counted_as_yours(self):
        """also_seen_by (Settings > Your collection): 'Not seen yet' leaves out what those accounts played."""
        c = make_catalog([dict(s, played_by={7: 2}) if s["title"] == "Second Ann" else s for s in self.specs])
        key = key_of(c, "New Ann")
        unseen = lambda **kw: [x["title"] for x in FP.film_page(c, key, parts=["similar"], **kw)["similar"]["unseen"]]
        self.assertIn("Second Ann", unseen())
        self.assertNotIn("Second Ann", unseen(also_seen_by=[7]))
        self.assertIn("Third Ann", unseen(also_seen_by=[7]))
        self.assertIn("Second Ann", unseen(also_seen_by=[8]))
        # too few ratings for the model: likeness alone leaves them out too
        few = make_catalog([dict(title=f"F{i}", year=1990, genres=["Action"], cast=["Star"], owner_rating=8.0)
                            for i in range(5)] + [dict(title="Target", year=1991, genres=["Action"], cast=["Star"]),
                                                  dict(title="Also", year=1992, genres=["Action"], cast=["Star"],
                                                       played_by={7: 1})])
        s = FP.film_page(few, key_of(few, "Target"), parts=["similar"], also_seen_by=[7])["similar"]
        self.assertEqual((s["source"], s["unseen"]), ("likeness", []))

    def test_no_limit_per_director(self):
        # (Watch Next's two-per-director limit is for variety; a likeness list keeps the closest films, sequels by
        # the same director and all)
        unseen = [x["title"] for x in self.similar("New Ann")["unseen"]]
        for title in ("Second Ann", "Third Ann", "Fourth Ann"):
            self.assertIn(title, unseen)


# ---------------------------------------------------------------------------------------------------------
class HookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = make_catalog([dict(title="Hooked", year=2000, cast=["Ann"], owner_plays=3, owner_rating=8.0,
                                         last_played=1772600000)])
        cls.key = next(iter(cls.catalog.films))

    def part(self, name, **modules):
        with mock.patch.dict(sys.modules, {n: None for n in HOOK_MODULES}):
            sys.modules.update({f"projectionist.{k}": v for k, v in modules.items()})
            return FP.film_page(self.catalog, self.key, parts=[name])[name]

    def test_missing_modules_and_functions(self):
        self.assertEqual(self.part("critics"), {"available": False})
        self.assertEqual(self.part("files", doctor=fake_module("projectionist.doctor")), {"available": False})
        self.assertEqual(self.part("issues", doctor=fake_module("projectionist.doctor", film_issues="not callable")),
                         {"available": False})

    def test_answers_pass_through(self):
        verdicts = {"ok": True, "verdicts": [{"critic_id": "125", "critic": "Roger Ebert", "verdict": "Fresh"}]}
        got = self.part("critics", critics=fake_module("projectionist.critics",
                                                       film_verdicts=lambda c, k: dict(verdicts, asked=k)))
        self.assertEqual(got, dict(verdicts, asked=self.key, available=True))
        files = {"copies": [{"edition": "", "files": ["/a.mkv"]}]}
        got = self.part("files", doctor=fake_module("projectionist.doctor", film_files=lambda c, k: files))
        self.assertEqual(got, dict(files, ok=True, available=True))
        issues = [{"id": "weaker", "severity": "fix", "title": "Weaker copies", "why": "..."}]
        got = self.part("issues", doctor=fake_module("projectionist.doctor", film_issues=lambda c, k: issues))
        self.assertEqual(got, {"available": True, "ok": True, "issues": issues})

    def test_a_hook_that_raises(self):
        def boom(catalog, key):
            raise RuntimeError("the disk is on fire")
        got = self.part("critics", critics=fake_module("projectionist.critics", film_verdicts=boom))
        self.assertEqual((got["available"], got["ok"]), (True, False))
        self.assertIn("the disk is on fire", got["error"])

    def test_cancelled_jobs_stop(self):
        def cancelled(catalog, key):
            raise jobs.Cancelled()
        with mock.patch.dict(sys.modules, {n: None for n in HOOK_MODULES}):
            sys.modules["projectionist.critics"] = fake_module("projectionist.critics", film_verdicts=cancelled)
            with jobs.running(jobs.Job("film.critics")):
                with self.assertRaises(jobs.Cancelled):
                    FP.film_page(self.catalog, self.key, parts=["critics"])

    def test_history_always_has_the_catalogs_count(self):
        got = self.part("history")
        self.assertEqual((got["available"], got["plays"], got["rating"], got["note"]), (False, 3, 8.0, FP.PLAYS_NOTE))
        self.assertEqual(got["catalog"]["plays"], 3)
        self.assertTrue(got["last_played"].startswith("2026-03-0"))
        mine = {"ok": True, "plays": [{"at": "2026-03-03T21:14", "how": "played", "device": "TV"}],
                "plays_on_record": 1, "plex_count": 3}
        got = self.part("history", habits=fake_module("projectionist.habits", film_history=lambda c, k: mine))
        self.assertEqual(got["plays"], mine["plays"])            # the backbone's answer, as it gives it
        self.assertTrue(got["available"])
        self.assertEqual(got["catalog"]["plays"], 3)
        failing = self.part("history", habits=fake_module("projectionist.habits",
                                                          film_history=lambda c, k: {"ok": False, "error": "x"}))
        self.assertEqual((failing["plays"], failing["ok"], failing["error"]), (3, False, "x"))

    def test_a_module_that_fails_to_import_is_reported(self):
        def broken(name, *a, **k):
            if name == "projectionist.critics":
                raise ModuleNotFoundError("No module named 'yaml'", name="yaml")
            return original(name, *a, **k)
        import importlib
        original = importlib.import_module
        with mock.patch.object(importlib, "import_module", broken):
            got = FP.film_page(self.catalog, self.key, parts=["critics"])["critics"]
        self.assertEqual((got["available"], got["ok"]), (True, False))
        self.assertIn("yaml", got["error"])


# ---------------------------------------------------------------------------------------------------------
class SearchTests(unittest.TestCase):
    def setUp(self):
        self.catalog = search_catalog()
        self.patch = without_hooks()
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def search(self, q, **request):
        return FP.search(self.catalog, dict(request, q=q))

    def top(self, q, **request):
        a = self.search(q, **request)
        return (a["groups"][0]["kind"], a["top"]["label"]) if a["groups"] else (None, None)

    def labels(self, q, kind="film"):
        a = self.search(q)
        return next(([r["label"] for r in g["results"]] for g in a["groups"] if g["kind"] == kind), [])

    def test_accents_word_order_prefixes_and_typos(self):
        self.assertEqual(self.top("shintaro katsu"), ("person", "Shintarō Katsu"))
        self.assertEqual(self.top("chan jackie"), ("person", "Jackie Chan"))
        self.assertEqual(self.top("sig weav"), ("person", "Sigourney Weaver"))
        self.assertEqual(self.top("sigorney"), ("person", "Sigourney Weaver"))
        self.assertEqual(self.search("sigorney")["how"], "near")
        self.assertEqual(self.labels("jacky chan", "person")[0], "Jackie Chan")       # the better known first
        self.assertEqual(self.top("curosawa"), ("person", "Akira Kurosawa"))

    def test_short_words_years_and_partial_matches(self):
        self.assertEqual(self.top("ip man"), ("film", "Ip Man (2008)"))
        self.assertEqual(self.top("x men"), ("film", "X-Men (2000)"))
        self.assertEqual(self.labels("alien 1979"), ["Alien (1979)"])
        year = self.search("1979")
        self.assertEqual(year["how"], "year")
        self.assertEqual(sorted(self.labels("1979")), ["Alien (1979)", "Hanging by a Thread (1979)"])
        self.assertEqual(self.top("2001"), ("film", "2001: A Space Odyssey (1968)"))  # a number in a title
        self.assertEqual(self.top("2001 a space odyssey"), ("film", "2001: A Space Odyssey (1968)"))
        self.assertEqual(self.top("five deadly venoms"), ("film", "The Five Venoms (1978)"))
        self.assertEqual(self.search("five deadly venoms")["how"], "partial")
        self.assertEqual(self.top("五毒"), ("film", "The Five Venoms (1978)"))
        self.assertIn("also called 五毒", self.search("五毒")["top"]["detail"])

    def test_accents_as_typed(self):
        self.assertEqual(self.labels("drácula")[:2], ["Drácula (1931)", "Dracula (1931)"])
        self.assertEqual(self.labels("dracula")[:2], ["Dracula (1931)", "Drácula (1931)"])
        self.assertEqual(self.labels("drácula 1931")[0], "Drácula (1931)")

    def test_groups_and_their_order(self):
        a = self.search("chan")
        self.assertEqual(a["groups"][0]["kind"], "person")                 # Jackie Chan before Charlie Chan's film
        self.assertEqual(a["top"]["label"], "Jackie Chan")
        self.assertIn("film", [g["kind"] for g in a["groups"]])
        self.assertEqual(self.top("usa"), ("country", "United States"))
        usa = self.search("usa")["top"]
        self.assertEqual((usa["id"], usa["display"], usa["films"]), ("United States of America", "United States", 8))
        self.assertEqual(self.top("zatoichi the blind"), ("collection", "Zatoichi the Blind Swordsman"))
        self.assertEqual(self.top("shaw brothers"), ("studio", "Shaw Brothers"))
        self.assertEqual(self.top("movies world"), ("library", "Movies-World"))
        self.assertEqual(self.top("science fiction"), ("genre", "Science Fiction"))
        person = self.search("jackie chan")["top"]
        self.assertEqual((person["kind"], person["id"], person["films"], person["directs"]),
                         ("person", "p:Jackie Chan", 3, False))
        self.assertTrue(person["detail"].startswith("3 films  ·  e.g. "))
        director = self.search("kurosawa")["top"]
        self.assertTrue(director["directs"])
        self.assertTrue(director["detail"].startswith("directed 2 of your films"))
        film = self.search("police story")["top"]
        self.assertEqual((film["key"], film["year"], film["your_rating"], film["seen"]),
                         (key_of(self.catalog, "Police Story"), 1985, 9.0, True))
        self.assertIn("you rated it 9", film["detail"])

    def test_caps_totals_and_filters(self):
        a = self.search("a", count=1)
        self.assertEqual(a["groups"], [])
        self.assertEqual(a["note"], "Type at least 2 letters.")
        a = self.search("ja", count=1)
        self.assertTrue(a["groups"])
        self.assertTrue(all(len(g["results"]) == 1 for g in a["groups"]))
        people = next(g for g in a["groups"] if g["kind"] == "person")
        self.assertGreaterEqual(people["total"], 2)                           # (Jackie, Jacky, James...)
        self.assertEqual(self.search("alien", count=0)["groups"][0]["results"].__len__(), 1)    # clamped to 1
        self.assertLessEqual(len(self.search("an", count=500)["groups"][0]["results"]), 50)
        only = self.search("chan", groups=["films"])
        self.assertEqual([g["kind"] for g in only["groups"]], ["film"])
        self.assertEqual([g["kind"] for g in self.search("chan", groups="person")["groups"]], ["person"])
        with self.assertRaises(ValueError):
            self.search("chan", groups=["posters"])
        self.assertFalse(FP.search(self.catalog, {})["ok"])
        self.assertEqual(FP.search(self.catalog, {"q": "  "})["error"], "q: what to look for")
        self.assertEqual(FP.search(self.catalog, {"query": "alien"})["top"]["label"], "Alien (1979)")
        self.assertEqual(self.search("zzqxvw")["groups"], [])
        self.assertEqual(self.search("zzqxvw")["how"], "")

    def test_critics_only_when_the_critics_backbone_lists_them(self):
        self.assertNotIn("critic", [g["kind"] for g in self.search("roger ebert")["groups"]])
        self.assertFalse(self.search("roger")["index"]["critics"])
        catalog = search_catalog()
        names = [{"id": 125, "name": "Roger Ebert", "reviews": 893, "publication": "Chicago Sun-Times"},
                 {"id": 7, "name": "Janet Maslin", "reviews": 12}]
        sys.modules["projectionist.critics"] = fake_module("projectionist.critics", critic_names=lambda c: names)
        a = FP.search(catalog, {"q": "roger ebert"})
        self.assertTrue(a["index"]["critics"])
        top = a["top"]
        self.assertEqual((top["kind"], top["id"], top["label"], top["reviews"]), ("critic", "125", "Roger Ebert", 893))
        self.assertEqual(top["detail"], "893 reviews  ·  Chicago Sun-Times")
        ja = FP.search(catalog, {"q": "ja"})
        self.assertIn("critic", [g["kind"] for g in ja["groups"]])     # Janet Maslin...
        self.assertNotEqual(ja["groups"][0]["kind"], "critic")         # ...but critics lead only on a better match

    def test_collections_genres_countries_say_how_many_you_havent_seen(self):
        # (they open in Watch Next, which lists only the films you haven't played or rated)
        usa = self.search("usa")["top"]
        self.assertEqual((usa["films"], usa["unseen"], usa["detail"]), (8, 7, "8 films, 7 not seen yet"))
        zatoichi = self.search("zatoichi the blind")["top"]
        self.assertEqual(zatoichi["detail"], "2 films, none seen yet")
        self.assertEqual(FP.films_seen_text(27, 0), "27 films, all seen")
        self.assertEqual(FP.films_seen_text(1, 1), "1 film, not seen yet")
        self.assertEqual(FP.films_seen_text(1, 0), "1 film, seen")
        self.assertEqual(FP.films_seen_text(1500, 200), "1,500 films, 200 not seen yet")

    def test_the_index_is_built_once_per_catalog(self):
        self.assertIsNone(FP.search_index(self.catalog, build=False))
        waiting = self.search("alien", wait=False)
        self.assertEqual((waiting["ok"], waiting["ready"], waiting["groups"]), (True, False, []))
        first = FP.search_index(self.catalog)
        self.assertIs(FP.search_index(self.catalog), first)
        self.assertIs(FP.search_index(self.catalog, build=False), first)
        self.assertTrue(self.search("alien", wait=False)["ready"])
        other = search_catalog()
        self.assertIsNot(FP.search_index(other), first)
        warmed = search_catalog()
        self.assertIsNotNone(FP.warm(warmed))
        self.assertIn(("costars.namesake_labels",), warmed.cache)

    def test_a_cancelled_job_stops_building(self):
        job = jobs.Job("search.index")
        job.cancel()
        with jobs.running(job):
            with self.assertRaises(jobs.Cancelled):
                FP.search_index(self.catalog)
        self.assertIsNone(FP.search_index(self.catalog, build=False))
        self.assertIsNotNone(FP.search_index(self.catalog))       # (outside a job it builds)

    def test_suggestions_and_studios(self):
        c = self.catalog
        for f in c.films.values():
            f.added_at = 1700000000 + (f.year or 0)
        picks = FP.suggestions(c)
        self.assertEqual(picks["recently_added"][0]["label"], "Ip Man (2008)")
        self.assertEqual(len(picks["recently_added"]), 6)
        self.assertEqual([p["label"] for p in picks["favourites"]], ["Police Story (1985)"])
        self.assertEqual(picks["favourites"][0]["note"], "you rated it 9")
        self.assertEqual({p["label"] for p in picks["well_rated_unseen"]},
                         {"Alien (1979)", "Aliens (1986)", "Seven Samurai (1954)"})
        # what Watch Next never suggests (Settings), 'Well rated, not seen yet' leaves out too - and only that list
        fewer = FP.suggestions(c, exclude_genres=["horror"])
        self.assertEqual({p["label"] for p in fewer["well_rated_unseen"]}, {"Aliens (1986)", "Seven Samurai (1954)"})
        self.assertEqual((fewer["recently_added"], fewer["favourites"]), (picks["recently_added"], picks["favourites"]))
        samurai = c.films[key_of(c, "Seven Samurai")].libraries[0]
        self.assertNotIn("Seven Samurai (1954)", {p["label"] for p in FP.suggestions(
            c, exclude_libraries=[samurai])["well_rated_unseen"]})
        # a film played on an account counted as yours (Settings > Your collection) is seen
        alien = c.films[key_of(c, "Alien")]
        alien.played_by = {7: 1}
        try:
            self.assertEqual({p["label"] for p in FP.suggestions(c, also_seen_by=[7])["well_rated_unseen"]},
                             {"Aliens (1986)", "Seven Samurai (1954)"})
            self.assertEqual(FP.suggestions(c, also_seen_by=[8]), picks)          # (another account: as before)
            with self.assertRaises(ValueError):
                FP.suggestions(c, also_seen_by="everyone")
        finally:
            alien.played_by = {}
        # the notes' dates in the style Settings > Dates and times chooses
        from projectionist import formats
        with formats.styled(date="iso"):
            note = FP.suggestions(c)["recently_added"][0]["note"]
        self.assertRegex(note, r"^added \d{4}-\d{2}-\d{2}$")
        rows = FP.studio_films(c, "golden harvest")
        self.assertEqual([r["label"] for r in rows],
                         ["Project A (1983)", "Police Story (1985)", "Armour of God (1986)"])
        self.assertEqual((rows[1]["your_rating"], rows[1]["played"]), (9.0, True))
        self.assertEqual(FP.studio_films(c, "Nobody"), [])


# ---------------------------------------------------------------------------------------------------------
def ordering_catalog():
    """Names that several kinds of thing share, sequel numbers, and titles typed without their spaces."""
    bond = ["James Bond"]
    specs = [
        dict(title="Rambo III", year=1988, cast=["Sylvester Stallone"], imdb_rating=5.8),
        dict(title="Rambo: First Blood Part II", year=1985, cast=["Sylvester Stallone"], imdb_rating=6.5),
        dict(title="Smokey and the Bandit Part 3", year=1983, cast=["Jackie Gleason", "Dan Rambo"]),
        dict(title="Dr. No", year=1962, cast=["Sean Connery"], collections=bond, imdb_rating=7.2),
        dict(title="Goldfinger", year=1964, cast=["Sean Connery", "Honor Blackman"], collections=bond),
        dict(title="Thunderball", year=1965, cast=["Sean Connery"], collections=bond),
        dict(title="Skyfall", year=2012, cast=["Daniel Craig"], collections=bond, owner_rating=8.0),
        dict(title="The Godfather", year=1972, cast=["Marlon Brando", "Rudy Bond"], imdb_rating=9.2),
        dict(title="The Godfather Part II", year=1974, cast=["Al Pacino", "Robert De Niro"], imdb_rating=9.0),
        dict(title="12 Angry Men", year=1957, cast=["Henry Fonda", "Rudy Bond"]),
        dict(title="On the Waterfront", year=1954, cast=["Marlon Brando", "Rudy Bond"]),
        dict(title="Dracula", year=1931, studio="Universal Pictures", genres=["Horror"]),
        dict(title="Frankenstein", year=1931, studio="Universal Pictures", genres=["Horror"]),
        dict(title="The Mummy", year=1932, studio="Universal Pictures", genres=["Horror"]),
        dict(title="Shaft", year=2000, cast=["Samuel L. Jackson", "Universal"]),
        dict(title="Universal Horror", year=1998, genres=["Documentary"], owner_rating=6.0),
        dict(title="Jason and the Argonauts", year=1963, cast=["Honor Blackman", "Thomas Ebert"]),
        dict(title="Total Recall", year=1990, cast=["Arnold Schwarzenegger", "Thomas Ebert"]),
        dict(title="Fist of Fury", year=1972, cast=["Bruce Lee"]),
        dict(title="Enter the Dragon", year=1973, cast=["Bruce Lee"]),
        dict(title="The Big Boss", year=1971, cast=["Bruce Lee"]),
        dict(title="The Clones of Bruce Lee", year=1980, cast=["Dragon Lee"]),
        dict(title="Bruce Lee: The Legend", year=1984),
        dict(title="Bruce Lee and I", year=1976),
        dict(title="Bruce Lee's Secret", year=1977),
        dict(title="Rocky", year=1976, cast=["Sylvester Stallone"], imdb_rating=8.1),
        dict(title="Rocky II", year=1979, cast=["Sylvester Stallone"], imdb_rating=7.3),
        dict(title="Arthur 2: On the Rocks", year=1988, cast=["Dudley Moore"]),
        dict(title="Alien³", year=1992, cast=["Sigourney Weaver"]),
        dict(title="Home Alone 3", year=1997, cast=["Alex D. Linz"]),
        dict(title="Seven Samurai", year=1954, cast=["Toshiro Mifune"]),
        dict(title="Toy Story 2", year=1999, cast=["Tom Hanks"]),
        dict(title="Star Wars", year=1977, cast=["Mark Hamill"]),
        dict(title="X-Men", year=2000, cast=["Hugh Jackman"]),
        dict(title="Ghostbusters", year=1984, cast=["Bill Murray"]),
        dict(title="Taxi Driver", year=1976, cast=["Robert De Niro"]),
        dict(title="GoodFellas", year=1990, cast=["Robert De Niro"]),
        dict(title="An Evening With C.S. Lewis", year=2019),
    ]
    return make_catalog(specs)


class SearchOrderTests(unittest.TestCase):
    """Which group comes first - Enter opens its first match - and numbers and spaces as people type them."""

    def setUp(self):
        self.catalog = ordering_catalog()
        self.patch = without_hooks()
        self.patch.start()
        self.addCleanup(self.patch.stop)
        names = [{"id": 125, "name": "Roger Ebert", "reviews": 893}]
        sys.modules["projectionist.critics"] = fake_module("projectionist.critics", critic_names=lambda c: names)

    def top(self, q):
        a = FP.search(self.catalog, {"q": q})
        return (a["groups"][0]["kind"], a["top"]["label"]) if a["groups"] else (None, None)

    def kinds(self, q):
        return [g["kind"] for g in FP.search(self.catalog, {"q": q})["groups"]]

    def test_a_name_many_things_share(self):
        self.assertEqual(self.top("rambo")[0], "film")                          # not Dan Rambo, in one film
        self.assertEqual(self.kinds("rambo"), ["film", "person"])
        self.assertEqual(self.top("bond"), ("collection", "James Bond"))        # 4 films, before Rudy Bond's 3
        self.assertEqual(self.top("universal"), ("studio", "Universal Pictures"))    # not the bit part 'Universal'
        self.assertEqual(self.top("horror"), ("genre", "Horror"))
        self.assertEqual(self.top("horor"), ("genre", "Horror"))                # a typo: still the genre first
        self.assertLess(self.kinds("horor").index("genre"), self.kinds("horor").index("person"))
        self.assertEqual(self.top("ebert"), ("critic", "Roger Ebert"))          # not Thomas Ebert, in 2 films
        self.assertEqual(self.top("bruce lee"), ("person", "Bruce Lee"))
        self.assertEqual(self.top("brue lee"), ("person", "Bruce Lee"))         # a whole name but for a typo
        self.assertEqual(self.top("de niro"), ("person", "Robert De Niro"))

    def test_equal_counts_are_equal_percentiles(self):
        index = FP.search_index(self.catalog)
        ones = {index.pct[n] for n, e in enumerate(index.entries) if e[0] == "person" and e[4] == 1}
        self.assertEqual(len(ones), 1)

    def test_sequel_numbers(self):
        self.assertEqual(self.top("rocky 2"), ("film", "Rocky II (1979)"))
        self.assertEqual(self.top("rocky ii"), ("film", "Rocky II (1979)"))
        self.assertEqual(self.top("alien 3"), ("film", "Alien³ (1992)"))
        self.assertEqual(self.top("alien3"), ("film", "Alien³ (1992)"))
        self.assertEqual(self.top("godfather 2"), ("film", "The Godfather Part II (1974)"))
        self.assertEqual(self.top("7 samurai"), ("film", "Seven Samurai (1954)"))
        self.assertEqual(self.top("toy story ii"), ("film", "Toy Story 2 (1999)"))
        # a number that isn't there finds nothing, rather than a typo of the word before it ('rocks' + '7')
        for q in ("rocky 7", "alien 5"):
            with self.subTest(q=q):
                self.assertEqual(FP.search(self.catalog, {"q": q})["groups"], [])
        labels = [r["label"] for g in FP.search(self.catalog, {"q": "alien 3"})["groups"] for r in g["results"]]
        self.assertNotIn("Home Alone 3 (1997)", labels)

    def test_spaces_left_out_or_put_in(self):
        for q, expected in (("starwars", ("film", "Star Wars (1977)")), ("xmen", ("film", "X-Men (2000)")),
                            ("ghost busters", ("film", "Ghostbusters (1984)")),
                            ("deniro", ("person", "Robert De Niro")),
                            ("cs lewis", ("film", "An Evening With C.S. Lewis (2019)")),
                            ("starw", ("film", "Star Wars (1977)"))):              # (as it's typed)
            with self.subTest(q=q):
                self.assertEqual(self.top(q), expected)
        self.assertEqual(FP.search(self.catalog, {"q": "starwars"})["how"], "spacing")


# ---------------------------------------------------------------------------------------------------------
class AskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = os.path.join(cls.tmp.name, "com.plexapp.plugins.library.db-2026-09-25")
        build_fixture(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def ask(self, **request):
        from projectionist.ask import handle
        with without_hooks():
            return handle(request, self.db)

    def test_film_by_key_with_its_page(self):
        a = self.ask(action="film", film_key=MATRIX_GUID)
        self.assertTrue(a["ok"])
        self.assertEqual(a["matched"], {"asked": MATRIX_GUID, "found": "The Matrix (1999)", "how": "key",
                                        "other_candidates": []})
        self.assertEqual((a["film"]["title"], a["film"]["key"]), ("The Matrix", MATRIX_GUID))
        self.assertEqual(a["credits"]["stay_after_credits"], "Yes")
        page = a["page"]
        self.assertEqual(page["parts"], list(FP.PARTS))
        self.assertEqual(page["credits"]["stay_after_credits"], "Yes")
        self.assertEqual([r["source"] for r in page["ratings"]["rows"]], ["imdb", "tmdb", "rt_critic", "rt_audience"])
        self.assertEqual(page["people"]["directors"][0]["name"], "Lana Wachowski")
        self.assertEqual(self.ask(action="film", key=MATRIX_GUID)["film"]["title"], "The Matrix")

    def test_parts_and_misses(self):
        a = self.ask(action="film", title="matrix", parts="none")
        self.assertTrue(a["ok"])
        self.assertNotIn("page", a)
        core = self.ask(action="film", title="matrix", parts="core")
        self.assertEqual(core["page"]["parts"], list(FP.CORE))
        self.assertFalse(self.ask(action="film", film_key="plex://movie/nope")["ok"])
        missing = self.ask(action="film", title="Zzqxv")
        self.assertFalse(missing["ok"])
        # (the existing answers still come: a key that's gone falls back to the title)
        both = self.ask(action="film", film_key="plex://movie/nope", title="2001")
        self.assertEqual(both["film"]["title"], "2001: A Space Odyssey")

    def test_exact_label_beats_the_loose_match(self):
        from projectionist.ask import handle
        c = search_catalog()
        with without_hooks():
            a = handle({"action": "film", "title": "Drácula (1931)", "parts": "none"}, catalog=c)
            b = handle({"action": "film", "title": "Dracula (1931)", "parts": "none"}, catalog=c)
        self.assertEqual((a["film"]["title"], a["matched"]["how"]), ("Drácula", "exact"))
        self.assertEqual(b["film"]["title"], "Dracula")

    def test_search_action(self):
        a = self.ask(action="search", q="matrix")
        self.assertTrue(a["ok"])
        self.assertEqual(a["top"]["label"], "The Matrix (1999)")
        self.assertEqual(a["top"]["key"], MATRIX_GUID)
        self.assertFalse(self.ask(action="search")["ok"])
        self.assertIn("bad request", self.ask(action="search", q="x y", groups=["nope"])["error"])


if __name__ == "__main__":
    unittest.main()
