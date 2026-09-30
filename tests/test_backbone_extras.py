"""Tests for the data added for the app's tabs: the overview, the taste profile, prediction breakdowns, credits
timelines, and people's circles / troupe pairs."""

import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_projectionist import build_fixture, make_catalog  # noqa: E402


def taste_catalog():
    specs = []
    for i in range(20):
        specs.append(dict(title=f"Loved {i}", year=1975 + i, genres=["Action"], directors=["Ann Auteur"],
                          cast=["Star A", f"Extra {i}"], imdb_rating=7.0, owner_rating=9.0, runtime_min=95))
        specs.append(dict(title=f"Meh {i}", year=1975 + i, genres=["Drama"], directors=["Bob Bland"],
                          cast=["Star B", f"Other {i}"], imdb_rating=7.0, owner_rating=4.0, runtime_min=130))
    specs.append(dict(title="New Ann", year=1999, genres=["Action"], directors=["Ann Auteur"], cast=["Star A"],
                      imdb_rating=7.0, runtime_min=90))
    return make_catalog(specs)


class OverviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from projectionist.catalog import load
        cls.tmp = tempfile.TemporaryDirectory()
        db = os.path.join(cls.tmp.name, "com.plexapp.plugins.library.db-2026-09-25")
        build_fixture(db)
        cls.catalog = load(db)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_overview(self):
        from projectionist.ask import handle
        o = handle({"action": "overview"}, catalog=self.catalog)
        self.assertTrue(o["ok"])
        self.assertEqual(o["totals"]["films"], 5)
        self.assertEqual(o["totals"]["rated_by_you"], 1)
        decades = [d["decade"] for d in o["by_decade"]]
        self.assertEqual(decades, list(range(decades[0], decades[-1] + 10, 10)))   # no gaps in the axis
        self.assertEqual(o["by_decade"][0]["label"], "1910s")
        self.assertEqual([r["label"] for r in o["resolutions"]], ["4K", "1080p", "SD"])   # natural order
        self.assertIsNone(o["agreement_with_imdb"])                                 # one rating: no correlation
        self.assertEqual(sum(r["films"] for r in o["your_ratings"]), 1)
        self.assertEqual(o["top_directors"][0]["films"], 1)
        self.assertFalse(handle({"action": "overview", "count": "x"}, catalog=self.catalog)["ok"])

    def test_credits_answer_has_what_a_timeline_needs(self):
        from projectionist.ask import handle
        c = handle({"action": "credits", "title": "The Matrix"}, catalog=self.catalog)["credits"]
        self.assertEqual(c["duration_sec"], 8178.7)
        self.assertEqual(c["credits_start_sec"], 7678.8)
        self.assertEqual([(s["start_sec"], s["end_sec"], s["counted"]) for s in c["stretches"]],
                         [(7678.8, 7712.8, True), (7762.8, 8178.7, True)])
        self.assertEqual((c["scenes"][0]["start_sec"], c["scenes"][0]["end_sec"]), (7712.8, 7762.8))


class TasteAndBreakdownTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = taste_catalog()

    def test_taste_profile(self):
        from projectionist.ask import handle
        t = handle({"action": "taste", "kinds": ["director", "genre"], "min_films": 5}, catalog=self.catalog)
        self.assertTrue(t["ok"], t)
        self.assertEqual(t["groups"]["director"]["above"][0]["label"], "Ann Auteur (director)")
        self.assertEqual(t["groups"]["director"]["below"][0]["label"], "Bob Bland (director)")
        # (runtime already explains much of the gap - the scores-only model knows runtimes - so the tilt is small)
        self.assertEqual(t["groups"]["genre"]["above"][0]["label"], "Action")
        self.assertGreater(t["groups"]["genre"]["above"][0]["tilt"], 0)
        self.assertEqual(t["groups"]["genre"]["above"][0]["your_average"], 9.0)
        self.assertFalse(handle({"action": "taste", "kinds": ["planets"]}, catalog=self.catalog)["ok"])

    def test_breakdown_adds_up(self):
        from projectionist.ask import handle
        r = handle({"action": "recommend", "count": 1}, catalog=self.catalog)["results"][0]
        b = r["breakdown"]
        total = b["base"] + sum(s["value"] for s in b["steps"]) + b["everything_else"]
        self.assertAlmostEqual(total, b["total_before_limits"], places=2)
        self.assertEqual(b["predicted"], r["predicted_rating"])
        self.assertTrue(any(s["label"] == "Ann Auteur (director)" for s in b["steps"]), b["steps"])
        self.assertEqual(r["people"][0], {"id": "p:Ann Auteur", "name": "Ann Auteur", "role": "director"})


class NetworkTests(unittest.TestCase):
    def test_circle_and_troupe_pairs(self):
        from projectionist.ask import handle
        catalog = make_catalog([dict(title=f"Gang {i}", year=2000 + i, cast=["Kuo", "Lu", "Chiang", "Sun"])
                                for i in range(5)] + [dict(title="Solo", year=2010, cast=["Kuo", "Zed"])])
        p = handle({"action": "person", "name": "Kuo", "circle": 3}, catalog=catalog)
        nodes = {n["name"]: n for n in p["circle"]["nodes"]}
        self.assertTrue(nodes["Kuo"]["center"])
        self.assertEqual(len(nodes), 4)                       # Kuo + 3 closest co-stars
        weights = {(e["a"], e["b"]): e["shared_films"] for e in p["circle"]["edges"]}
        self.assertEqual(weights[("p:Kuo", "p:Lu")], 5)
        t = handle({"action": "troupes", "min_shared": 5, "max_billing": 4}, catalog=catalog)["troupes"][0]
        self.assertEqual(t["members"], ["Chiang", "Kuo", "Lu", "Sun"])
        self.assertEqual(len(t["pairs"]), 6)
        self.assertTrue(all(pair["shared_films"] == 5 for pair in t["pairs"]))
        chain = handle({"action": "connect", "from": "Zed", "to": "Sun"}, catalog=catalog)
        self.assertEqual(chain["steps"][0]["from_id"], "p:Zed")


class ReadmeTests(unittest.TestCase):
    """The README keeps up with the app: every ask action has a row, every Library Doctor check is named, and the
    Movies sheet's column count is right."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as f:
            cls.text = f.read()

    def test_every_action_has_a_row(self):
        import re
        from projectionist.ask import handle
        actions = list(handle({"action": "help"})["actions"])
        rows = re.findall(r"^\| `(\w+)` \|", self.text, re.M)
        self.assertEqual(sorted(rows), sorted(actions))

    def test_every_library_doctor_check_is_named(self):
        from projectionist import doctor
        row = next(line for line in self.text.splitlines() if line.startswith("| **Library Doctor** |")).lower()
        words = {"duplicates": "duplicates", "weaker": "better copy", "large": "far bigger", "small": "far smaller",
                 "unavailable": "unavailable", "editions": "several editions", "subtitles": "no subtitles",
                 "audio": "no audio in your languages", "unknown_audio": "untagged soundtracks",
                 "upgrade": "below 1080p", "metadata": "missing details", "disk": "disk space"}
        self.assertEqual(sorted(words), sorted(doctor.ISSUE_IDS))
        self.assertIn(f"{len(doctor.ISSUE_IDS)} checks".replace("12", "twelve"), row)
        for issue, said in words.items():
            self.assertIn(said, row, issue)

    def test_the_movies_sheet_column_count(self):
        from projectionist import sheets
        self.assertIn(f"One row per movie with {len(sheets.MOVIE_COLUMNS)} columns", self.text)


if __name__ == "__main__":
    unittest.main()
