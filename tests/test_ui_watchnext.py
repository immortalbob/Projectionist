"""Tests for the Watch Next tab (projectionist/ui/watchnext.py). Everything is headless - every Tk root is withdrawn
and nothing is ever shown."""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from projectionist import jobs  # noqa: E402
from projectionist.ask import handle  # noqa: E402
from projectionist.ui import watchnext as W  # noqa: E402
from support import wider  # noqa: E402


def hidden_root():
    import tkinter as tk
    from projectionist.ui import theme
    root = tk.Tk()
    root.withdraw()            # headless: never show a window
    theme.apply_styles(root)
    return root


def specs():
    """40 rated films (an auteur you love, one you don't) and a handful you haven't seen."""
    out = []
    for i in range(20):
        out.append(dict(title=f"Loved {i}", year=1975 + i, genres=["Action"], directors=["Ann Auteur"],
                        cast=["Star A", f"Extra {i}"], imdb_rating=7.0, owner_rating=9.0, runtime_min=95,
                        countries=["Hong Kong"], collections=["Shaw Brothers"] if i % 2 else [],
                        libraries=["Asia"], studio="Shaw"))
        out.append(dict(title=f"Meh {i}", year=1975 + i, genres=["Drama"], directors=["Bob Bland"],
                        cast=["Star B", f"Other {i}"], imdb_rating=7.0, owner_rating=4.0, runtime_min=130,
                        countries=["France"], libraries=["Movies"], studio="Big Studio"))
    out += [
        dict(title="New Ann", year=1999, genres=["Action"], directors=["Ann Auteur"], cast=["Star A"],
             imdb_rating=7.0, runtime_min=90, summary="A lone swordsman seeks revenge.", libraries=["Asia"],
             countries=["Hong Kong"]),
        dict(title="New Bob", year=1999, genres=["Drama"], directors=["Bob Bland"], cast=["Star B"],
             imdb_rating=7.0, runtime_min=140, summary="A family drama.", libraries=["Movies"],
             countries=["France"]),
        dict(title="Second Ann", year=2001, genres=["Action"], directors=["Ann Auteur"], cast=["Star A"],
             imdb_rating=6.9, runtime_min=92, libraries=["Asia"], collections=["Shaw Brothers"]),
        dict(title="Third Ann", year=2002, genres=["Action"], directors=["Ann Auteur"], cast=["Star A"],
             imdb_rating=6.8, runtime_min=93, libraries=["Asia"]),
        dict(title="Seen Ann", year=2003, genres=["Action"], directors=["Ann Auteur"], cast=["Star A"],
             imdb_rating=7.0, owner_plays=1, libraries=["Asia"]),
        dict(title="Prairie", year=1965, genres=["Western"], directors=["Cy Cowboy"], cast=["Tex"],
             imdb_rating=6.0, runtime_min=100, libraries=["Movies"]),
    ]
    return out


def make(spec_list=None):
    from test_projectionist import make_catalog
    return make_catalog(spec_list if spec_list is not None else specs(), libraries=("Movies", "Asia"))


class FakeApp:
    """The main window's services. run() is synchronous unless deferred=True, when jobs wait in .jobs as
    (job, work, done, failed). As App.run, it returns the job, and a newer run with the same key calls the older
    one off: it stops, and never answers."""

    def __init__(self, catalog=None, deferred=False):
        self.catalog = catalog
        self.catalog_error = "file is not a database"
        self.deferred = deferred
        self.jobs = []
        self.keys = []
        self.gone = []
        self.statuses = []
        self.asked = []
        self.tab = None
        self._keyed = {}

    def ask(self, request):
        self.asked.append(request)
        return handle(request, catalog=self.catalog)

    def run(self, work, done=None, failed=None, status=None, key=None):
        if status:
            self.statuses.append(status)
        self.keys.append(key)
        if key is not None and key in self._keyed:
            self._keyed.pop(key).cancel()
        job = jobs.Job(key)
        if key is not None:
            self._keyed[key] = job
        held = (job, work, done, failed)
        if self.deferred:
            self.jobs.append(held)
        else:
            self.finish(held)
        return job

    def finish(self, held):
        job, work, done, failed = held
        if job.cancelled:
            return
        with jobs.running(job):
            try:
                result = work()
            except jobs.Cancelled:
                return
            except Exception as exc:          # noqa: BLE001 - mirrors App.run
                if failed and not job.cancelled:
                    failed(f"{type(exc).__name__}: {exc}")
                return
        if job.cancelled:
            return
        job.ended = True
        if self._keyed.get(job.key) is job:
            del self._keyed[job.key]
        if done:
            done(result)

    def goto(self, tab_title, /, **kwargs):
        self.gone.append((tab_title, kwargs))
        return object()

    def set_status(self, text):
        self.statuses.append(text)

    def current_tab(self):
        return self.tab


def build(app):
    from tkinter import ttk
    root = hidden_root()
    notebook = ttk.Notebook(root)
    notebook.pack(fill="both", expand=True)
    tab = W.Tab(app, notebook)
    notebook.add(tab.frame, text=tab.title)
    app.tab = tab
    return root, tab


def chart_items(view, w=520, h=320):
    view.redraw(wider(view, w), h)   # (the width chosen for Windows' font, grown for a wider one)
    return view.find_withtag("chart")


def texts(view):
    return [view.itemcget(i, "text") for i in view.find_all() if view.type(i) == "text"]


def titles(tab):
    return [tab.table.rows[i]["title"] for i in tab.table.tree.get_children()]


# ---------------------------------------------------------------------------------------------------------
class PlainFunctionTests(unittest.TestCase):
    def test_requests_from_the_filters(self):
        self.assertEqual(W.build_request(dict(W.DEFAULTS)),
                         {"action": "recommend", "sort": "predicted", "count": 30, "max_per_director": 2})
        req = W.build_request(dict(W.DEFAULTS, sort="personal", library="Asia", genre="Drama", decade="1990s",
                                   runtime="120 min", person="Star A", like="Loved 3 (1978)", words="sword",
                                   include_watched=True, variety=0, count=50), ("countries", "France"))
        self.assertEqual(req, {"action": "recommend", "sort": "personal", "count": 50, "max_per_director": 0,
                               "library": "Asia", "genre": "Drama", "decade": 1990, "max_runtime": 120,
                               "with": "Star A", "like": "Loved 3 (1978)", "text": "sword",
                               "include_watched": True, "countries": "France"})
        self.assertEqual([W.parse_decade(v) for v in (1994, "1990s", "The 1950s", "Any", None, "", "soon")],
                         [1990, 1990, 1950, None, None, None, None])
        # an exact person or film (from a link) goes by id, not by a name someone else might share
        req = W.build_request(dict(W.DEFAULTS, person="Michael Moore", person_id="p:2", like="Dracula (1931)",
                                   like_key="f7"))
        self.assertEqual((req.get("with_id"), req.get("like_key")), ("p:2", "f7"))
        self.assertNotIn("with", req)
        self.assertNotIn("like", req)

    def test_prediction_words_add_up(self):
        r = {"predicted_rating": 8.8, "expected_from_scores": 8.68, "kind_of_film": -0.12, "personal_lift": 0.26}
        self.assertEqual(W.prediction_words(r), "its IMDb/RT scores suggest 8.68, the kind of film takes off 0.12 "
                                                "and its people and studios add 0.26")
        r = {"predicted_rating": 7.0, "expected_from_scores": 7.0, "kind_of_film": 0.001, "personal_lift": -0.3}
        self.assertEqual(W.prediction_words(r), "its IMDb/RT scores suggest 7.00, the kind of film makes no "
                                                "difference and its people and studios take off 0.30")
        self.assertEqual(W.prediction_words({"predicted_rating": 7.0}), "")

    def test_confidence_sorts_by_rank_not_alphabet(self):
        rows = [W.result_row(n, {"title": str(n), "predicted_rating": 7.0, "confidence": c})
                for n, c in enumerate(("low", "high", "medium"))]
        self.assertEqual([str(r["confidence"]) for r in sorted(rows, key=lambda r: r["confidence"])],
                         ["High", "Medium", "Low"])

    def test_taste_labels_and_clicks(self):
        self.assertEqual(W.taste_filter("genre", "Drama"), ("genre", "Drama"))
        self.assertEqual(W.taste_filter("decade", "The 1950s"), ("decade", 1950))
        self.assertEqual(W.taste_filter("library", "Movies-World library"), ("library", "Movies-World"))
        self.assertEqual(W.taste_filter("director", "Ridley Scott (director)"), ("person", "Ridley Scott"))
        self.assertEqual(W.taste_filter("actor", "Tom Hanks"), ("person", "Tom Hanks"))
        self.assertEqual(W.taste_filter("collection", "'Godzilla' collection"), ("collection", "Godzilla"))
        self.assertEqual(W.taste_filter("country", "Japan"), ("country", "Japan"))
        self.assertIsNone(W.taste_filter("studio", "TOHO"))              # the recommender can't filter by studio
        self.assertEqual(W.display_label("collection", "'Godzilla' collection"), "Godzilla")
        self.assertEqual(W.display_label("country", "United States of America"), "United States")
        self.assertEqual(W.split_rows(3, 20, 10), (3, 7))                 # a short side lends its rows
        self.assertEqual(W.split_rows(20, 20, 10), (5, 5))
        group = {"above": [{"label": "A", "tilt": 1.0, "films": 5, "your_average": 8.0},
                           {"label": "B", "tilt": 0.5, "films": 5, "your_average": 7.5}],
                 "below": [{"label": "Z", "tilt": -0.9, "films": 5, "your_average": 5.0},
                           {"label": "Y", "tilt": -0.2, "films": 5, "your_average": 6.0}]}
        items = W.taste_items(group, "genre", 10)
        self.assertEqual([i["label"] for i in items], ["A (5)", "B (5)", "Y (5)", "Z (5)"])  # most negative last
        self.assertIn("Click", items[0]["tip"])
        self.assertNotIn("Click", W.taste_items(group, "studio", 10, clickable=False)[0]["tip"])
        # the answer's order picks which rows show (steadiest first); they're drawn biggest first
        steady = {"above": [{"label": "Many", "tilt": 0.5, "films": 60, "your_average": 7.0},
                            {"label": "Few", "tilt": 1.5, "films": 3, "your_average": 9.0},
                            {"label": "Fewer", "tilt": 1.9, "films": 3, "your_average": 9.0}], "below": []}
        self.assertEqual([i["label"] for i in W.taste_items(steady, "genre", 2)], ["Few (3)", "Many (60)"])
        # what a click can show: films you haven't seen, else ones you've played, else nothing
        self.assertEqual(W.taste_click("actor", {"unseen": 2, "played_not_rated": 0}),
                         (True, False, "Click to see the 2 films of theirs you haven't seen"))
        self.assertEqual(W.taste_click("genre", {"unseen": 0, "played_not_rated": 1}),
                         (True, True, "You've played the rest - click to see the 1 film you haven't rated"))
        self.assertEqual(W.taste_click("director", {"unseen": 0, "played_not_rated": 0}),
                         (False, False, "You've rated every film of theirs on your shelves"))
        # more than a click lists: it promises the best of them, not all
        self.assertEqual(W.taste_click("genre", {"unseen": 1072, "played_not_rated": 3})[2],
                         "Click to see the best of the 1,072 films you haven't seen")
        done = W.taste_items({"above": [dict(group["above"][0], unseen=0, played_not_rated=0)], "below": []},
                             "collection", 4)[0]
        self.assertFalse(done["clickable"])
        self.assertIn("You've rated every film in it", done["tip"])
        self.assertNotIn("haven't seen", done["tip"])

    def test_empty_hints_say_what_to_try(self):
        hint = W.empty_hint({"genre": "Western", "decade": 1920})
        self.assertEqual(hint, "Try setting genre, decade or library to 'Any', or including films you've played.")
        self.assertIn("Highest predicted rating", W.empty_hint({"sort": "personal", "include_watched": True}))
        # ...but not advice that can't help: including played films when none of them would match
        self.assertEqual(W.empty_hint({"genre": "Western", "decade": 1920}, {"played_matches": 0}),
                         "Try setting genre, decade or library to 'Any'.")
        # one person, and you've rated all their films: say so
        self.assertEqual(W.empty_message({"with_id": "p:1", "sort": "predicted"}, {"played_matches": 0})[0],
                         "You've rated every film of theirs")
        title, hint = W.empty_message({"collections": "Zatoichi"}, {"played_matches": 2})
        self.assertEqual(title, "You've seen every film in that collection")
        self.assertIn("see the 2 you've played", hint)
        self.assertEqual(W.empty_message({"with": "Ann", "genre": "Drama"}, {"played_matches": 0})[0],
                         "No films match all of these")

    def test_not_found_is_said_as_every_tab_says_it(self):
        heading, sub = W.problem_message({"ok": False, "error": "no one matching 'Jacky Chen'",
                                          "suggestions": ["Jackie Chan", "Jacky Cheung", "Jack Chen"]})
        self.assertEqual(heading, "No one called “Jacky Chen” in your collection")
        self.assertEqual(sub, "Did you mean Jackie Chan, Jacky Cheung or Jack Chen?")
        heading, sub = W.problem_message({"ok": False, "error": "no film matching 'Zzqx'"})
        self.assertEqual((heading, sub), ("No film called “Zzqx” in your collection",
                                          "Check the spelling - suggestions appear as you type."))
        self.assertEqual(W.problem_message({"ok": False, "error": "no film with key 'f9'"})[0],
                         "That film isn't in your collection")
        self.assertEqual(W.problem_message({"ok": False, "error": "rate at least 30 films first"}),
                         ("Not enough ratings yet", "Rate at least 30 films first."))
        # the status line: the same words, with no tab name (as the Credits and Six Degrees tabs say it)
        self.assertEqual(W.search_status({"ok": False, "error": "no one matching 'Zed'"}, {}, 0.1),
                         "No one called “Zed” in your collection.")
        self.assertEqual(W.search_status({"ok": False, "error": "no film with key 'f9'"}, {}, 0.1),
                         "That film isn't in your collection.")
        self.assertEqual(W.search_status({"ok": False, "error": "rate at least 30 films first"}, {}, 0.1),
                         "Watch Next: rate at least 30 films first")
        self.assertEqual(W.search_status({"ok": True, "results": [{}] * 3, "matching_films": 40}, {}, 0.12),
                         "Watch Next: 3 of 40 matching films (0.1 s)")
        self.assertEqual(W.search_status({"ok": True, "results": [], "matching_films": 0, "played_matches": 0},
                                         {"text": "unicorn"}, 0.1), "Watch Next: no films match all of these.")
        self.assertEqual(W.plain_note("'jacky chan' taken as Jackie Chan"), "“jacky chan” taken as Jackie Chan")

    def test_a_like_search_with_nothing_new_names_what_is_alike(self):
        # 'Les Rêveurs (1958)': the only films much like it are ones you've rated, so nothing new matches
        req = {"action": "recommend", "sort": "predicted", "like_key": "f9"}
        answer = {"ok": True, "results": [], "matching_films": 0, "played_matches": 0,
                  "like_film": "Les Rêveurs (1958)",
                  "closest_rated": [{"title": "Harbour Lights", "year": 1956},
                                    {"title": "The Long Wait", "year": 1954}, {"title": "Les Reveurs", "year": 1958}]}
        heading, sub = W.empty_message(req, answer)
        self.assertEqual(heading, "Nothing you haven't seen is much like Les Rêveurs (1958)")
        self.assertEqual(sub, "You've rated the films most like it already: Harbour Lights (1956), The "
                              "Long Wait (1954) and Les Reveurs (1958).")
        self.assertEqual(W.search_status(answer, req, 0.1),
                         "Watch Next: nothing you haven't seen is much like Les Rêveurs (1958).")
        answer["closest_rated"] = answer["closest_rated"][2:]
        self.assertIn("The only film much like it is Les Reveurs (1958)", W.empty_message(req, answer)[1])
        answer["closest_rated"] = []
        self.assertIn("No other film on your shelf shares enough", W.empty_message(req, answer)[1])
        self.assertEqual(W.empty_message(dict(req, include_watched=True), answer)[0],
                         "Nothing you haven't rated is much like Les Rêveurs (1958)")
        # other filters too, or films you've played that would match: the usual advice
        self.assertEqual(W.empty_message(dict(req, genre="Horror"), answer)[0], "No films match all of these")
        self.assertEqual(W.empty_message(req, dict(answer, played_matches=2))[0], "No films match all of these")

    def test_waterfall_steps_fold_into_everything_else(self):
        b = {"steps": [{"label": "a", "value": 1.0}, {"label": "b", "value": 0.02}, {"label": "c", "value": -0.5},
                       {"label": "d", "value": -0.01}], "everything_else": 0.1}
        steps, rest = W.fit_steps(b, 2)
        self.assertEqual([s["label"] for s in steps], ["a", "c"])
        self.assertAlmostEqual(rest, 0.11)
        self.assertEqual(W.fit_steps(b, 9), (b["steps"], 0.1))

    def test_accuracy_panels_and_an_honest_verdict(self):
        answer = SAMPLE_EVALUATE
        panels = W.accuracy_panels(answer)
        self.assertEqual([p["title"] for p in panels], ["Average miss", "Rank agreement", "Top picks you rated 8+"])
        rank = panels[1]["rows"]
        self.assertIsInstance(rank[0]["value"], W.NotApplicable)          # one guess for all can't rank
        self.assertEqual(panels[1]["fmt"](rank[0]["value"]), "n/a")
        self.assertEqual(panels[1]["fmt"](0.671), "0.67")
        self.assertTrue(rank[2]["emphasis"])
        self.assertIn("lower is better", panels[0]["note"])
        self.assertIn("higher is better", panels[1]["note"])
        self.assertIn("29%", panels[2]["note"])
        top = panels[2]["rows"]
        self.assertIsInstance(top[0]["value"], W.NotApplicable)           # ...nor pick favourites
        self.assertEqual(panels[2]["fmt"](top[0]["value"]), "n/a")
        self.assertEqual(panels[2]["fmt"](top[2]["value"]), "86%")
        headline, text = W.accuracy_verdict(answer)
        self.assertEqual(headline, "Your taste tracks the critics closely")
        self.assertIn("misses your rating by 0.72 points", text)
        self.assertIn("misses by 0.72 too", text)
        self.assertIn("mostly adds reasons", text)
        self.assertIn("0.97", text)
        # A model that really does better says so - and one that does worse doesn't pretend
        better = _with_model(answer, mean_error=0.59)
        self.assertEqual(W.accuracy_verdict(better)[0], "It adds something real to the IMDb/RT scores")
        worse = _with_model(answer, mean_error=0.79)
        self.assertEqual(W.accuracy_verdict(worse)[0], "No better than the scores alone")
        # a negative rank agreement is drawn as no bar, not as a positive one
        odd = _with_model(answer, rank_agreement=-0.2)
        self.assertEqual(W.accuracy_panels(odd)[1]["rows"][2]["value"], 0.0)
        # the small print: plain words, judged on the chart's own measure (average miss)
        self.assertEqual(W.method_text(answer, 6.4), "Other settings of the model did no better. Took 6.4 s.")
        worse_default = _with_model(answer, mean_error=0.79)
        self.assertIn("would have missed by 0.07 points less", W.method_text(worse_default))
        just_one = dict(answer, model_by_lambda={"32": answer["model_by_lambda"]["32"]})
        self.assertEqual(W.method_text(just_one, 1.2), "Took 1.2 s.")      # what the tab asks for
        for jargon in ("smoothing", "lambda", "cross-validation", "fold"):
            self.assertNotIn(jargon, W.method_text(answer, 6.4))


SAMPLE_EVALUATE = {   # an evaluate answer, made up for the tests: 212 ratings of an imaginary collection
    "ok": True, "action": "evaluate", "rated_films": 212, "your_average": 7.14, "share_you_rated_8_plus": 0.29,
    "method": "5-fold cross-validation, repeated 2 times: every rating is predicted by a model that never saw it. "
              "mean_error is in rating points; ...",
    "baselines": {"your average for everything": {"mean_error": 0.97, "rms_error": 1.26, "rank_agreement": -0.05,
                                                  "top_fifth_you_rated_8_plus": 0.27},
                  "IMDb / Rotten Tomatoes scores only": {"mean_error": 0.72, "rms_error": 0.93,
                                                         "rank_agreement": 0.664,
                                                         "top_fifth_you_rated_8_plus": 0.83}},
    "model_by_lambda": {"8": {"mean_error": 0.73, "rms_error": 0.94, "rank_agreement": 0.655,
                              "top_fifth_you_rated_8_plus": 0.84},
                        "16": {"mean_error": 0.72, "rms_error": 0.93, "rank_agreement": 0.668,
                               "top_fifth_you_rated_8_plus": 0.85},
                        "32": {"mean_error": 0.72, "rms_error": 0.92, "rank_agreement": 0.671,
                               "top_fifth_you_rated_8_plus": 0.86},
                        "64": {"mean_error": 0.72, "rms_error": 0.93, "rank_agreement": 0.669,
                               "top_fifth_you_rated_8_plus": 0.86}},
    "best_lambda": 16.0, "default_lambda": 32.0,
}


def _with_model(answer, **changes):
    import copy
    out = copy.deepcopy(answer)
    out["model_by_lambda"]["32"].update(changes)
    return out


# ---------------------------------------------------------------------------------------------------------
class TabTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = make()

    def setUp(self):
        try:
            self.app = FakeApp(self.catalog)
            self.root, self.tab = build(self.app)
        except Exception as exc:          # noqa: BLE001
            self.skipTest(f"Tk unavailable: {exc}")

    def tearDown(self):
        self.tab._cancel_timers()
        self.root.destroy()

    def ready(self):
        self.tab.catalog_changed(self.catalog, "ready")
        self.root.update()

    def test_placeholders_while_there_is_no_collection(self):
        tab = self.tab
        for state, words in (("loading", "Reading your collection..."), ("none", "No database yet"),
                             ("error", "Couldn't read the collection")):
            tab.catalog_changed(None, state)
            self.assertEqual(tab.placeholder.winfo_manager(), "grid", state)
            self.assertEqual(tab.views.winfo_manager(), "", state)          # the views are out of the way
            tab.placeholder.redraw(wider(tab.placeholder, 600), 300)
            self.assertIn(words, texts(tab.placeholder), state)
        self.assertIn("file is not a database", texts(tab.placeholder))
        tab.navigate(genre="Drama")                    # asked before there's a collection: remembered for later
        self.assertEqual(tab.vars["genre"].get(), "Drama")
        self.assertEqual(self.app.jobs, [])
        tab.catalog_changed(self.catalog, "ready")
        self.assertEqual(tab.views.winfo_manager(), "grid")
        self.assertEqual(str(tab.views.cget("style")), "Inner.TNotebook")    # smaller tabs than the main ones
        self.assertEqual(titles(tab), ["New Bob"])

    def test_recommendations_fill_in_when_the_collection_is_ready(self):
        self.ready()
        tab = self.tab
        self.assertEqual(self.app.statuses[0], "Learning your taste from your ratings...")
        found = titles(tab)
        self.assertEqual(set(found[:2]), {"New Ann", "Second Ann"})          # the auteur you love, 2 per director
        self.assertNotIn("Seen Ann", found)
        self.assertIn("of", tab.results_count.cget("text"))
        self.assertIn("40 ratings", tab.results_hint.cget("text"))
        self.assertIn("Haven't seen", tab.notes.cget("text"))
        self.assertNotIn("max_per_director", tab.notes.cget("text"))         # the panel's words, not the API's
        row = tab.table.rows[tab.table.tree.get_children()[0]]
        self.assertEqual(set(row) >= {"title", "year", "predicted", "lift", "imdb", "minutes", "library",
                                      "confidence"}, True)
        self.assertTrue(row["lift"].startswith(("+", "-")))
        # the details of the first film, and its waterfall
        self.assertIs(tab.selected, row["result"])
        info = tab.info.get("1.0", "end")
        for words in (row["title"], "Predicted", "Why", "Against", "Similar films you rated", "People"):
            self.assertIn(words, info)
        # the prediction's three parts, all from the one model: they add up, and the lift's sign agrees
        r = row["result"]
        self.assertIn(W.prediction_words(r), info)
        self.assertAlmostEqual(r["expected_from_scores"] + r["kind_of_film"] + r["personal_lift"],
                               r["predicted_rating"], delta=0.07)
        # the film's three actions are real buttons the keyboard can reach (Tab from the list)
        actions = {"Open the film page", "More like this", "Scenes after the credits?"}
        self.assertEqual(actions <= set(dict(tab.links)), True)
        buttons = [tab.info.nametowidget(w) for w in tab.info.window_names()]
        self.assertEqual({b.cget("text") for b in buttons}, actions)
        self.assertTrue(all(str(b.cget("takefocus")) != "0" for b in buttons))
        self.assertTrue(chart_items(tab.breakdown, 460, 280))
        self.assertIn("How the prediction adds up", texts(tab.breakdown))
        self.assertIn("Predicted for you", texts(tab.breakdown))
        tab.breakdown.redraw(wider(tab.breakdown, 260), 220)          # a small window: shorter labels
        self.assertIn("Predicted", texts(tab.breakdown))
        # picking another row shows that film
        second = tab.table.tree.get_children()[1]
        tab.table.tree.selection_set(second)
        self.root.update()
        self.assertEqual(tab.selected["title"], tab.table.rows[second]["title"])
        self.assertEqual(len(tab.info.window_names()), 3)                  # the old film's buttons are gone
        # ...from tkinter too, and the links leave no Tcl commands behind, film after film
        self.root.update()
        commands = len(self.root.tk.call("info", "commands"))
        for r in tab.answer["results"] * 5:
            tab.show_film(r)
        self.root.update()
        self.assertEqual(len(tab.info.children), 3)
        self.assertLessEqual(len(self.root.tk.call("info", "commands")), commands)
        tab.show_film(tab.table.rows[second]["result"])                    # (the selected row's film again)
        # Enter (or a double-click) on a film: its page on the Film tab, by its key
        picked = tab.selected
        self.assertIn("<Key-Return>", tab.table.tree.bind())
        tab.table._opened()                                   # what Enter and a double-click run
        self.assertEqual(self.app.gone[-1], ("Film", {"film_key": picked["key"],
                                                      "title": f"{picked['title']} ({picked['year']})"}))
        # ...and so does its button; More like this stays in the details
        dict(tab.links)["Open the film page"]()
        self.assertEqual(self.app.gone[-1][0], "Film")
        dict(tab.links)["More like this"]()
        self.assertEqual(tab.like_box.get(), f"{picked['title']} ({picked['year']})")
        self.assertEqual(tab.request.get("like_key"), picked["key"])       # exactly that film

    def test_filters_change_the_results(self):
        self.ready()
        tab = self.tab
        tab.vars["genre"].set("Drama")
        tab._changed(now=True)
        self.assertEqual(titles(tab), ["New Bob"])
        tab._reset()
        tab.vars["words"].set("swordsman revenge")
        tab._changed(now=True)
        self.assertEqual(titles(tab), ["New Ann"])
        self.assertIn("Matches: swordsman, revenge", tab.info.get("1.0", "end"))
        tab._reset()
        tab.vars["runtime"].set("90 min")
        tab._changed(now=True)
        self.assertEqual(titles(tab), ["New Ann"])
        tab._reset()
        tab.vars["variety"].set("0")
        tab.vars["include_watched"].set(True)
        tab._changed(now=True)
        self.assertIn("Seen Ann", titles(tab))
        self.assertIn("Third Ann", titles(tab))
        tab._reset()
        tab.vars["sort"].set("personal")
        tab._changed(now=True)
        self.assertNotIn("New Bob", titles(tab))
        self.assertEqual(tab.results_title.cget("text"), "Personal picks")
        for r in tab.answer["results"]:            # 'films you'd rate above their reputation' - as shown
            self.assertGreater(r["predicted_rating"], r["expected_from_scores"], r["title"])
            self.assertGreater(r["personal_lift"], 0, r["title"])
        tab._reset()
        tab.with_box.set("star b")
        tab._changed(now=True)
        self.assertEqual(titles(tab), ["New Bob"])
        # bad values in the spin boxes are put right, not sent
        tab._reset()
        tab.vars["count"].set("lots")
        tab.vars["variety"].set("99")
        self.assertEqual((tab.filters()["count"], tab.filters()["variety"]), (30, 5))
        self.assertEqual(tab.vars["count"].get(), "30")

    def test_empty_results_and_errors_say_what_to_do(self):
        self.ready()
        tab = self.tab
        tab.vars["words"].set("unicorn")
        tab._changed(now=True)
        self.assertEqual(tab.results_msg.winfo_manager(), "grid")
        self.assertEqual(tab.table.winfo_manager(), "")
        tab.results_msg.redraw(wider(tab.results_msg, 600), 200)
        self.assertIn("No films match all of these", texts(tab.results_msg))
        self.assertTrue(any("fewer words" in t for t in texts(tab.results_msg)))
        self.assertIn("Pick a film", tab.info.get("1.0", "end"))
        self.assertNotIn("including films you've played", " ".join(texts(tab.results_msg)))  # none would match
        tab._reset()
        tab.like_box.set("Zzqx Nothing")
        tab._changed(now=True)
        tab.results_msg.redraw(wider(tab.results_msg, 600), 200)
        # said as every tab says it (and not '...ing' left in the status line)
        self.assertIn("No film called “Zzqx Nothing” in your collection", texts(tab.results_msg))
        self.assertEqual(self.app.statuses[-1], "No film called “Zzqx Nothing” in your collection.")
        self.assertEqual(tab.results_count.cget("text"), "")
        tab._reset()
        tab.with_box.set("Zzqxv Blorp")
        tab._changed(now=True)
        tab.results_msg.redraw(wider(tab.results_msg, 600), 200)
        self.assertIn("No one called “Zzqxv Blorp” in your collection", texts(tab.results_msg))
        self.assertEqual(self.app.statuses[-1], "No one called “Zzqxv Blorp” in your collection.")
        tab._reset()
        self.assertEqual(tab.table.winfo_manager(), "grid")
        # someone whose films you've all rated: said plainly
        tab.navigate(person_id="p:Extra 3", person="Extra 3")
        tab.results_msg.redraw(wider(tab.results_msg, 600), 200)
        self.assertIn("You've rated every film of theirs", texts(tab.results_msg))

    def test_not_enough_ratings_is_said_calmly(self):
        small = make([dict(title=f"Film {i}", year=1990 + i, genres=["Drama"], imdb_rating=7.0,
                           owner_rating=7.0 if i < 5 else None) for i in range(12)])
        self.app.catalog = small
        self.tab.catalog_changed(small, "ready")
        tab = self.tab
        tab.results_msg.redraw(wider(tab.results_msg, 600), 200)
        shown = texts(tab.results_msg)
        self.assertIn("Not enough ratings yet", shown)
        self.assertTrue(any("rate at least 30" in t for t in shown), shown)
        tab.views.select(tab.taste_page)
        tab._load_taste()
        tab.taste_view.redraw(wider(tab.taste_view, 500), 300)
        self.assertIn("Not enough ratings yet", texts(tab.taste_view))
        tab._evaluate()
        tab.acc_view.redraw(wider(tab.acc_view, 600), 200)
        self.assertIn("Can't test it yet", texts(tab.acc_view))
        self.assertEqual(str(tab.eval_btn.cget("state")), "normal")

    def test_links_go_to_the_other_tabs(self):
        self.ready()
        tab = self.tab
        links = dict(tab.links)
        label = f"{tab.selected['title']} ({tab.selected['year']})"
        links["Scenes after the credits?"]()
        key = tab.selected["key"]
        self.assertEqual(self.app.gone[-1], ("Credits", {"title": label, "film_key": key}))
        person = tab.selected["people"][0]                    # the director: their profile counts what they
        self.assertEqual(person["role"], "director")            # directed (some act in more films than that)
        links[person["name"]]()
        self.assertEqual(self.app.gone[-1], ("Six Degrees", {"person_id": person["id"], "name": person["name"],
                                                             "directors": True}))
        actor = next(p for p in tab.selected["people"] if p["role"] != "director")
        links[actor["name"]]()                                  # an actor: just who they are
        self.assertEqual(self.app.gone[-1], ("Six Degrees", {"person_id": actor["id"], "name": actor["name"]}))
        # ...and the same through the text's own bindings: the link under the pointer is underlined and clicked
        t = tab.info
        start = next(t.tag_ranges(tag)[0] for tag in t.tag_names() if tag.startswith("link") and t.tag_ranges(tag)
                     and t.get(*t.tag_ranges(tag)[:2]) == person["name"])
        t.mark_set("current", f"{start} + 1 chars")
        tab._link_hover()
        tag = tab._hover_link
        self.assertTrue(tag and str(t.tag_cget(tag, "underline")) in ("1", "true"))
        self.assertEqual(str(t.cget("cursor")), "hand2")
        self.app.gone.clear()
        tab._link_clicked()
        self.assertEqual(self.app.gone, [("Six Degrees", {"person_id": person["id"], "name": person["name"],
                                                          "directors": True})])
        tab._link_left()
        self.assertEqual(str(t.cget("cursor")), "arrow")
        links["More like this"]()
        self.assertEqual(tab.like_box.get(), label)
        self.assertEqual(tab.request.get("like_key"), key)                  # that very film, not a namesake
        tab.like_box.set("Loved 3 (1978)")                   # typing another film's 'Title (Year)': that film,
        tab._changed(now=True)                                # by its key (as a suggestion picked would be)
        loved3 = next(f.key for f in self.catalog.films.values() if f.label == "Loved 3 (1978)")
        self.assertEqual((tab.request.get("like"), tab.request.get("like_key")), (None, loved3))
        tab.like_box.set("loved 3")                           # anything else: by title
        tab._changed(now=True)
        self.assertEqual((tab.request.get("like"), tab.request.get("like_key")), ("loved 3", None))

    def test_links_work_with_the_main_windows_goto(self):
        """App.goto(self, title, **kwargs) can't take title=... (the names clash): the tab gets there anyway."""
        self.ready()
        tab = self.tab

        class Credits:
            title = "Credits"
            frame = tab.frame
            asked = []

            def navigate(self, **kwargs):
                self.asked.append(kwargs)

        credits = Credits()

        class Notebook:
            selected = []

            def select(self, frame):
                self.selected.append(frame)

        def goto(title, **kwargs):             # the main window's signature
            return None

        self.app.goto = goto
        self.app.tabs = [credits]
        self.app.notebook = Notebook()
        dict(tab.links)["Scenes after the credits?"]()
        self.assertEqual(Credits.asked, [{"title": f"{tab.selected['title']} ({tab.selected['year']})",
                                          "film_key": tab.selected["key"]}])
        self.assertEqual(Notebook.selected, [tab.frame])
        self.app.tabs = []
        dict(tab.links)["Scenes after the credits?"]()
        self.assertEqual(self.app.statuses[-1], "The Credits tab isn't available.")

    def test_navigate_sets_just_those_filters_and_searches(self):
        self.ready()
        tab = self.tab
        tab.vars["words"].set("something old")
        tab.views.select(tab.acc_page)
        tab.navigate(genre="action", decade=1990, library="asia", unknown_key=1)
        self.assertEqual(tab._view(), "rec")
        self.assertEqual((tab.vars["genre"].get(), tab.vars["decade"].get(), tab.vars["library"].get()),
                         ("Action", "1990s", "Asia"))                      # the lists' own spelling
        self.assertEqual(tab.vars["words"].get(), "")                        # others cleared
        self.assertEqual(tab.request, {"action": "recommend", "sort": "predicted", "count": 30,
                                       "max_per_director": 2, "library": "Asia", "genre": "Action",
                                       "decade": 1990})
        self.assertEqual(titles(tab), ["New Ann"])
        tab.navigate(person="Star B")
        self.assertEqual((tab.with_box.get(), tab.vars["genre"].get()), ("Star B", "Any"))
        self.assertEqual(titles(tab), ["New Bob"])
        self.assertEqual(tab.vars["variety"].get(), "0")                     # all of one person's films
        tab.navigate(person_id="p:Ann Auteur", person="Ann Auteur")
        self.assertEqual((tab.request.get("with_id"), tab.request.get("with")), ("p:Ann Auteur", None))
        self.assertEqual(set(titles(tab)), {"New Ann", "Second Ann", "Third Ann"})
        tab.navigate(like="Loved 3 (1978)")
        self.assertEqual(tab.like_box.get(), "Loved 3 (1978)")
        self.assertEqual(self.catalog.films[tab.request["like_key"]].label, "Loved 3 (1978)")
        self.assertIn("likeness", tab.answer["results"][0])
        self.assertEqual(tab.vars["variety"].get(), "2")
        tab.navigate(country="France")
        self.assertEqual(tab.extra_row.winfo_manager(), "grid")
        self.assertEqual(tab.request.get("countries"), "France")
        self.assertEqual(titles(tab), ["New Bob"])
        tab._clear_extra()
        self.assertNotIn("countries", tab.request)
        self.assertEqual(tab.extra_row.winfo_manager(), "")
        # the filter's words use the everyday name the taste chart shows; the request keeps Plex's
        tab.navigate(country="Republic of Korea")
        self.assertEqual(tab.extra_label.cget("text"), "Also: From South Korea")
        self.assertEqual(tab.request.get("countries"), "Republic of Korea")

    def test_played_films_are_offered_only_when_they_would_help(self):
        self.ready()
        answer = handle({"action": "recommend", "with": "Ann Auteur", "max_per_director": 0}, catalog=self.catalog)
        self.assertEqual(answer["played_matches"], 1)                       # Seen Ann
        self.assertNotIn("played_matches", handle({"action": "recommend", "include_watched": True},
                                                  catalog=self.catalog))
        # a taste row whose films you've all seen, some played but not rated: the click shows those
        row = {"label": "Star A", "id": "p:Star A", "tilt": 0.5, "films": 20, "your_average": 9.0, "unseen": 0,
               "played_not_rated": 1}
        self.tab._taste_pick("actor", row)
        self.assertEqual((self.tab.request.get("with_id"), self.tab.request.get("include_watched")),
                         ("p:Star A", True))
        self.assertTrue(self.tab.vars["include_watched"].get())

    def test_namesakes_are_told_apart(self):
        """Two people called 'Star B': a link to the lesser-known one finds their films, not the other's."""
        from test_projectionist import make_catalog
        catalog = make_catalog(specs() + [dict(title="Twin Film", year=2005, genres=["Comedy"], cast=["Twin"],
                                               imdb_rating=6.0, runtime_min=88, libraries=["Movies"])],
                               libraries=("Movies", "Asia"))
        twin = catalog.people["p:Twin"]
        twin.name = twin.acted[next(iter(twin.acted))].name = "Star B"
        app = FakeApp(catalog)
        root, tab = build(app)
        try:
            tab.catalog_changed(catalog, "ready")
            tab.navigate(person_id="p:Twin", name="Star B")
            self.assertEqual(titles(tab), ["Twin Film"])
            self.assertEqual(tab.request.get("with_id"), "p:Twin")
            # which Star B it is - in the words Six Degrees uses for them too
            self.assertEqual(tab.with_box.get(), "Star B (1 film: Twin Film)")
            self.assertIn("Star B (1 film: Twin Film)", tab._suggest_people("star b"))
            self.assertIn("Star B (21 films, e.g. Meh 0)", tab._suggest_people("star b"))
            from projectionist.ui import degrees as D
            self.assertEqual(W.namesake_labels(catalog)["id"], D.people_directory(catalog)["ids"])
            tab._reset()
            tab.with_box.set("Star B")                                         # typed: the one with more films,
            tab._changed(now=True)                                             # and the list says there are two
            self.assertEqual(titles(tab), ["New Bob"])
            self.assertIn("2 people on your shelf are called Star B", tab.notes.cget("text"))
            self.assertIn("the suggestions in 'With' tell them apart", tab.notes.cget("text"))
            tab.with_box.set("Star B (21 films, e.g. Meh 0)")                  # picked from the suggestions
            tab._changed(now=True)
            self.assertEqual(tab.request.get("with_id"), "p:Star B")
            self.assertEqual(titles(tab), ["New Bob"])
        finally:
            tab._cancel_timers()
            root.destroy()

    def test_stale_answers_are_dropped(self):
        app = FakeApp(self.catalog, deferred=True)
        root, tab = build(app)
        try:
            tab.catalog_changed(self.catalog, "ready")
            self.assertEqual(len(app.jobs), 1)
            self.assertEqual(str(tab.find_btn.cget("state")), "disabled")     # while it's searching
            app.finish(app.jobs.pop())                                       # (the one that fits the model)
            tab.vars["genre"].set("Action")
            tab._changed(now=True)
            first = app.jobs.pop()
            tab.vars["genre"].set("Drama")
            tab._changed(now=True)
            second = app.jobs.pop()
            self.assertEqual(app.keys, ["watchnext.search"] * 3)
            self.assertTrue(first[0].cancelled)                              # called off: it stops working
            self.assertFalse(second[0].cancelled)
            app.finish(second)
            self.assertEqual(titles(tab), ["New Bob"])
            self.assertEqual(str(tab.find_btn.cget("state")), "normal")
            app.finish(first)                                                # older: ignored
            self.assertEqual(titles(tab), ["New Bob"])
            first[0].cancelled = False                                       # even if it did answer, the
            app.finish(first)                                                # token drops it
            self.assertEqual(titles(tab), ["New Bob"])
            # a new collection makes everything in flight stale
            tab._changed(now=True)
            job = app.jobs.pop()
            tab.catalog_changed(None, "loading")
            app.finish(job)
            self.assertEqual(tab.placeholder.winfo_manager(), "grid")
            self.assertEqual(str(tab.find_btn.cget("state")), "normal")
        finally:
            tab._cancel_timers()
            root.destroy()

    def test_newer_requests_call_older_ones_off(self):
        app = FakeApp(self.catalog, deferred=True)
        root, tab = build(app)
        try:
            tab.catalog_changed(self.catalog, "ready")
            tab._evaluate()
            self.assertEqual(app.jobs[-1][0].key, "watchnext.eval")
            app.finish(app.jobs.pop())
            self.assertTrue(tab.eval_answer["ok"])
            app.finish(app.jobs.pop())                                       # the first search: the model's fitted
            self.assertTrue(tab._model_ready)
            # now a search takes a moment of its own: a newer one calls the older one off
            tab.vars["genre"].set("Action")
            tab._changed(now=True)
            tab.vars["genre"].set("Drama")
            tab._changed(now=True)
            older, newer = app.jobs
            self.assertEqual((older[0].key, newer[0].key), ("watchnext.search", "watchnext.search"))
            self.assertTrue(older[0].cancelled)
            self.assertFalse(newer[0].cancelled)
            app.finish(older)
            app.finish(newer)
            self.assertEqual(titles(tab), ["New Bob"])
            self.assertEqual(str(tab.find_btn.cget("state")), "normal")
        finally:
            tab._cancel_timers()
            root.destroy()

    def test_what_needs_the_model_waits_for_the_job_fitting_it(self):
        """The first search fits the model, and so would the taste chart: asked for while that's working, they
        wait for it rather than call it off and start the fit over (one fit, however many clicks)."""
        app = FakeApp(self.catalog, deferred=True)
        root, tab = build(app)
        try:
            tab.catalog_changed(self.catalog, "ready")                        # the first search: it fits the model
            tab.views.select(tab.taste_page)
            tab._refresh()                                                    # the taste chart: waits
            tab.kind_var.set("director")
            tab._kind_changed()                                               # another kind: still waiting
            tab.vars["genre"].set("Drama")
            tab._changed(now=True)                                            # another search: waits too
            self.assertEqual([held[0].key for held in app.jobs], ["watchnext.search"])
            tab.taste_view.redraw(wider(tab.taste_view, 500), 300)
            self.assertIn("Learning your taste...", texts(tab.taste_view))
            self.assertEqual(str(tab.find_btn.cget("state")), "disabled")
            app.finish(app.jobs.pop())               # fitted: the old filters' answer isn't shown - it searches
            self.assertEqual(tab._fitting, None)     # again with the new ones (quick now), and the taste chart
            self.assertEqual({tab.taste_table.rows[i]["label"] for i in tab.taste_table.tree.get_children()},
                             {"Ann Auteur", "Bob Bland"})                     # is there at once, for directors
            self.assertEqual(len(app.jobs), 1)
            self.assertIsNone(tab.request.get("genre") if tab.request else None)
            app.finish(app.jobs.pop())
            self.assertEqual(tab.request.get("genre"), "Drama")
            self.assertEqual(titles(tab), ["New Bob"])
            self.assertEqual(str(tab.find_btn.cget("state")), "normal")
        finally:
            tab._cancel_timers()
            root.destroy()
        # the taste chart first: a second click waits for its fit, and so does a search
        app = FakeApp(self.catalog, deferred=True)
        root, tab = build(app)
        try:
            tab.views.select(tab.taste_page)
            tab.catalog_changed(self.catalog, "ready")
            self.assertEqual([held[0].key for held in app.jobs], ["watchnext.taste"])
            tab.kind_var.set("actor")
            tab._kind_changed()
            tab.navigate(genre="Action")                                      # (goes to Recommendations)
            self.assertEqual(len(app.jobs), 1)
            app.finish(app.jobs.pop())
            self.assertIn("actor", tab.taste_answer["groups"])                # the kind picked by then
            self.assertEqual([held[0].key for held in app.jobs], ["watchnext.search"])
            app.finish(app.jobs.pop())
            self.assertEqual(tab.request.get("genre"), "Action")
            self.assertTrue(titles(tab))
            self.assertIn("ui.watchnext.people", self.catalog.cache)          # the name look-ups got built too
        finally:
            tab._cancel_timers()
            root.destroy()

    def test_a_like_search_with_nothing_new_says_why(self):
        """'Drácula (1931)', the Spanish-language version: the only film much like it is the English one, which
        you've rated - so the search finds nothing new, and says that rather than '0 of 0 matching films'."""
        monsters = dict(genres=["Horror"], collections=["Monsters"], studio="Universal", countries=["USA"],
                        libraries=["Movies"], year=1931)
        catalog = make(specs() + [dict(monsters, title="Dracula", directors=["Tod Browning"], cast=["Bela Lugosi"],
                                       imdb_rating=7.3, owner_rating=8.0, runtime_min=75),
                                  dict(monsters, title="Drácula", directors=["George Melford"],
                                       cast=["Carlos Villarías"], imdb_rating=7.0, runtime_min=104)])
        english = next(f for f in catalog.films.values() if f.title == "Dracula")
        spanish = next(f for f in catalog.films.values() if f.title == "Drácula")
        app = FakeApp(catalog)
        root, tab = build(app)
        try:
            tab.catalog_changed(catalog, "ready")
            tab.navigate(like=spanish.label, film_key=spanish.key)             # (as the Overview's scatter does)
            self.assertEqual(tab.request.get("like_key"), spanish.key)
            self.assertEqual(titles(tab), [])
            tab.results_msg.redraw(wider(tab.results_msg, 600), 200)
            shown = " ".join(texts(tab.results_msg))
            self.assertIn("Nothing you haven't seen is much like Drácula (1931)", shown)
            self.assertIn("The only film much like it is Dracula (1931), and you've rated it already", shown)
            self.assertEqual(app.statuses[-1], "Watch Next: nothing you haven't seen is much like Drácula (1931).")
            self.assertEqual(tab.results_count.cget("text"), "")                  # not '0 of 0 matching films'
            # typed (or picked from the suggestions), 'Drácula (1931)' is that film - not the English one, which
            # the title search would take it for
            tab._reset()
            tab.like_box.set("Drácula (1931)")
            tab._changed(now=True)
            self.assertEqual(tab.request.get("like_key"), spanish.key)
            self.assertNotEqual(spanish.key, english.key)
        finally:
            tab._cancel_timers()
            root.destroy()

    def test_work_waits_until_the_tab_is_looked_at(self):
        app = FakeApp(self.catalog)
        root, tab = build(app)
        try:
            app.tab = None                        # another tab is in front
            tab.catalog_changed(self.catalog, "ready")
            self.assertEqual(tab.table.tree.get_children(), ())
            app.tab = tab
            tab.shown()
            self.assertTrue(tab.table.tree.get_children())
            tab.shown()                           # nothing to redo
            self.assertEqual(len([s for s in app.statuses if s.startswith("Watch Next:")]), 1)
        finally:
            tab._cancel_timers()
            root.destroy()

    def test_your_taste(self):
        self.ready()
        tab = self.tab
        tab.views.select(tab.taste_page)
        tab._refresh()
        self.assertTrue(tab.taste_answer["ok"])
        self.assertTrue(chart_items(tab.taste_view, 560, 420))
        shown = texts(tab.taste_view)
        self.assertIn("Genres you rate above or below their reputation", shown)
        self.assertIn("You rate higher", shown)
        self.assertIn("<Button-1>", tab.taste_view.tag_bind("div0"))           # bars are clickable
        for _ in range(3):                                                      # resizes redraw the chart...
            tab.taste_view.redraw(wider(tab.taste_view, 560), 420)
        script = tab.taste_view.tag_bind("div0", "<Button-1>")                  # ...but a click runs one command
        self.assertEqual(script.count("if {"), 1, script)
        rows = [tab.taste_table.rows[i] for i in tab.taste_table.tree.get_children()]
        self.assertEqual({r["label"] for r in rows}, {"Action", "Drama"})
        self.assertTrue(rows[0]["tilt"].startswith("+"))
        self.assertIn("All 2 genres", tab.taste_table_card.title_label.cget("text"))
        self.assertIn("2 points = one star", tab.tilt_help.cget("text"))
        # directors: the label loses '(director)', and a click filters the recommendations to them
        tab.kind_var.set("director")
        tab._kind_changed()
        self.assertEqual(tab.min_films_var.get(), "3")
        labels = {tab.taste_table.rows[i]["label"] for i in tab.taste_table.tree.get_children()}
        self.assertEqual(labels, {"Ann Auteur", "Bob Bland"})
        ann = next(r for r in tab.taste_answer["groups"]["director"]["above"] if "Ann" in r["label"])
        self.assertEqual((ann["id"], ann["unseen"], ann["played_not_rated"]), ("p:Ann Auteur", 3, 1))
        tab._taste_pick("director", ann)
        self.assertEqual(tab._view(), "rec")
        self.assertEqual(tab.request.get("with_id"), "p:Ann Auteur")          # the very person, not a namesake
        self.assertEqual(tab.with_box.get(), "Ann Auteur")
        self.assertEqual(len(titles(tab)), 3)                                  # all 3 you haven't seen
        # someone whose films you've all rated: their bar leads nowhere, and says why
        tab.views.select(tab.taste_page)
        tab.kind_var.set("actor")
        tab._kind_changed()
        tab.min_films_var.set("1")
        tab._taste_changed(now=True)
        extra = next(r for r in tab.taste_answer["groups"]["actor"]["above"] + tab.taste_answer["groups"]["actor"]
                     ["below"] if r["label"] == "Extra 3")
        self.assertEqual((extra["unseen"], extra["played_not_rated"]), (0, 0))
        tab._taste_pick("actor", extra)
        self.assertEqual(tab._view(), "taste")
        self.assertIn("you've rated every film of theirs", self.app.statuses[-1])
        tab.taste_view.redraw(wider(tab.taste_view, 560), 420)
        self.assertIn("(20)", " ".join(texts(tab.taste_view)))                # how many films, on the chart
        # a name too long for a narrow chart is shortened, not its count
        long = {"above": [{"label": "'Zatoichi the Blind Swordsman and All His Friends' collection", "tilt": 0.3,
                           "films": 27, "your_average": 7.0, "unseen": 3, "played_not_rated": 0}], "below": []}
        tab.taste_view.show(lambda p: W.draw_taste(p, long, "collection", 4, True, lambda row: None))
        tab.taste_view.redraw(wider(tab.taste_view, 300), 300)
        label = next(t for t in texts(tab.taste_view) if t.startswith("Zatoichi"))
        self.assertTrue(label.endswith("… (27)"), label)
        # studios can't filter the recommendations: their bars open the studio's films on the Film tab
        tab.views.select(tab.taste_page)
        tab.kind_var.set("studio")
        tab._kind_changed()
        tab.taste_view.redraw(wider(tab.taste_view, 560), 420)
        self.assertIn("<Button-1>", tab.taste_view.tag_bind("div0"))
        studio = tab.taste_table.rows[tab.taste_table.tree.get_children()[0]]["row"]["label"]
        tab.taste_table.tree.selection_set(tab.taste_table.tree.get_children()[0])
        tab.taste_table._opened()
        self.assertEqual(self.app.gone[-1], ("Film", {"studio": studio}))
        self.assertIn("Film tab", tab.taste_foot.cget("text"))
        # a minimum nobody meets
        tab.min_films_var.set("50")
        tab._taste_changed(now=True)
        tab.taste_view.redraw(wider(tab.taste_view, 560), 420)
        self.assertIn("No studios with 50+ films you rated", texts(tab.taste_view))
        # genre bars and table rows open the recommendations
        tab.kind_var.set("genre")
        tab._kind_changed()
        drama = next(r for r in tab.taste_table.rows.values() if r["label"] == "Drama")
        tab._taste_row_opened(drama)
        self.assertEqual((tab._view(), tab.vars["genre"].get()), ("rec", "Drama"))

    def test_how_accurate(self):
        self.ready()
        tab = self.tab
        tab.acc_view.redraw(wider(tab.acc_view, 700), 200)
        self.assertIn("Not tested yet", texts(tab.acc_view))
        tab._evaluate()
        self.assertTrue(tab.eval_answer["ok"])
        self.assertEqual(str(tab.eval_btn.cget("state")), "normal")
        self.assertTrue(chart_items(tab.acc_view, 800, 200))
        shown = texts(tab.acc_view)
        for words in ("Average miss", "Rank agreement", "Top picks you rated 8+", "This model", "n/a"):
            self.assertIn(words, shown)
        self.assertTrue(tab.verdict_title.cget("text"))
        self.assertIn("misses your rating by", tab.verdict.cget("text"))
        self.assertIn("Took", tab.method.cget("text"))
        self.assertEqual(list(tab.eval_answer["model_by_lambda"]), ["32"])     # just the model the app uses
        self.assertIn("Tested on your 40 ratings", tab.eval_status.cget("text"))
        tab.acc_view.redraw(wider(tab.acc_view, 700), 200)                  # narrow: the short method names
        self.assertIn("Your average", texts(tab.acc_view))
        tab.acc_view.redraw(wider(tab.acc_view, 1200), 200)
        self.assertIn("Your average for everything", texts(tab.acc_view))
        tab.catalog_changed(self.catalog, "ready")     # a reload forgets the old test
        self.assertIsNone(tab.eval_answer)

    def test_evaluation_shows_progress_and_blocks_a_second_run(self):
        app = FakeApp(self.catalog, deferred=True)
        root, tab = build(app)
        try:
            tab.catalog_changed(self.catalog, "ready")
            app.finish(app.jobs.pop())
            tab._evaluate()
            self.assertEqual(str(tab.eval_btn.cget("state")), "disabled")
            self.assertEqual(tab.eval_progress.winfo_manager(), "grid")
            self.assertIn("Testing on your 40 ratings", tab.eval_status.cget("text"))
            tab._evaluate()                                 # already running
            self.assertEqual(len(app.jobs), 1)
            app.finish(app.jobs.pop())
            self.assertEqual(tab.eval_progress.winfo_manager(), "")
            self.assertEqual(str(tab.eval_btn.cget("state")), "normal")
            self.assertNotIn("eval", tab._timers)
        finally:
            tab._cancel_timers()
            root.destroy()

    def test_layout_adapts_to_small_windows(self):
        import tkinter as tk
        tab = self.tab
        event = tk.Event()
        event.width, event.height = 500, 500
        tab._fit_columns(event)
        self.assertNotIn("library", tab.table.tree.tk.splitlist(tab.table.tree.cget("displaycolumns")))
        event.width = wider(tab.frame, 900)
        tab._fit_columns(event)
        self.assertIn("library", tab.table.tree.tk.splitlist(tab.table.tree.cget("displaycolumns")))
        event.height = 500
        tab._fit_height(event)
        self.assertEqual(tab.head.winfo_manager(), "")
        event.height = wider(tab.frame, 800)
        tab._fit_height(event)
        self.assertEqual(tab.head.winfo_manager(), "grid")


# ---------------------------------------------------------------------------------------------------------
def setUpModule():
    """A click on a pile of dots offers a menu - a real one, even from a withdrawn window, and it blocks: never."""
    import tkinter as tk
    from projectionist.ui import paint
    global _saved_choose, _saved_popup
    _saved_choose, _saved_popup = paint.Interaction._choose, tk.Menu.tk_popup
    paint.Interaction._choose = lambda self, near: None
    tk.Menu.tk_popup = lambda self, *args, **kwargs: None


def tearDownModule():
    import tkinter as tk
    from projectionist.ui import paint
    paint.Interaction._choose, tk.Menu.tk_popup = _saved_choose, _saved_popup


def spots_of(view, w=700, h=320):
    """The dots a chart registered for hover and click: (x, y, reach, tag, tip, key, on_click, noun)."""
    from projectionist.ui.paint import Interaction
    view.redraw(w, h)
    ui = Interaction.of(view, create=False)
    return list(ui.spots) if ui is not None else []


def critic_catalog():
    import test_critics
    return test_critics.critic_catalog()


class PlainCriticsFunctionTests(unittest.TestCase):
    def test_the_picks_request_and_row(self):
        req = W.build_request(dict(W.DEFAULTS, sort="critics", genre="Drama"))
        self.assertEqual(req, {"action": "critics", "view": "picks", "sort": "critics", "count": 30,
                               "max_per_director": 2, "genre": "Drama"})
        row = W.result_row(1, {"title": "X", "predicted_rating": 7.0, "personal_lift": 0.1,
                               "critics": {"score": 0.8, "fresh": 3, "rotten": 0}}, "critics")
        self.assertEqual(str(row["lift"]), "3 of 3")
        low = W.result_row(2, {"title": "Y", "predicted_rating": 7.0, "critics": {"score": 0.667, "fresh": 1,
                                                                                   "rotten": 0}}, "critics")
        self.assertGreater(row["lift"], low["lift"])                  # sorts by how one-sided, not alphabetically
        self.assertEqual(W.result_row(1, {"title": "X", "predicted_rating": 7.0, "personal_lift": 0.1})["lift"],
                         "+0.10")
        # an empty list says why, and what to try
        heading, sub = W.empty_message({"sort": "critics", "genre": "Western"}, {"played_matches": 0})
        self.assertEqual(heading, "None of your closest critics called these films Fresh")
        self.assertIn("Highest predicted rating", sub)
        # ...but not when the filters alone match nothing: then no sort would help
        heading, sub = W.empty_message({"sort": "critics", "text": "zzqxv"}, {"played_matches": 0,
                                                                              "filter_matches": 0})
        self.assertEqual((heading, sub), ("No films match all of these", "Try using fewer words."))
        self.assertEqual(W.search_status({"ok": True, "results": [], "filter_matches": 0, "played_matches": 0},
                                         {"sort": "critics", "text": "zzqxv"}, 0.1),
                         "Watch Next: no films match all of these.")
        self.assertEqual(W.empty_message({"sort": "critics", "text": "swordsman"}, {"filter_matches": 3})[0],
                         "None of your closest critics called these films Fresh")
        self.assertFalse(W._like_only({"sort": "critics", "like": "Alien (1979)"}))
        self.assertEqual(W.problem_message({"ok": False, "reason": "no_reviews", "error": "x"})[0],
                         "No critic reviews to go on")
        self.assertEqual(W.problem_message({"ok": False, "reason": "few_ratings",
                                            "error": "only 3 ... - rate at least 30 films critics reviewed"})[0],
                         "Not enough ratings yet")

    def test_words_from_the_numbers(self):
        ov = {"ok": True, "typical": {"agreement": 0.71, "chance": 0.583, "lift": 0.127, "when_fresh": 7.42,
                                      "when_rotten": 6.61},
              "stand_out": {"verdict": "luck", "tested": 87, "min_shared": 10}, "rated_with_reviews": 173,
              "rated_films": 212, "blind_spots": [{"library": "Movies-World", "rated": 64, "without_reviews": 31}],
              "liked_at": 7.0, "anti_twins": 0}
        title, body = W.critics_headline(ov)
        self.assertEqual(title, "Critics mostly agree with you - but none stands out yet")
        for words in ("71% of the films you've both judged", "would manage 58%", "Fresh you rate the film 7.4",
                      "Rotten, 6.6", "no bigger than luck", "87 critics share 10+ films", "closest so far",
                      "173 of your 212 rated films", "31 of the 64 you rated in Movies-World have none"):
            self.assertIn(words, body)
        real = dict(ov, stand_out={"verdict": "real", "tested": 40, "min_shared": 10})
        self.assertEqual(W.critics_headline(real)[0], "Some critics really are closer to you than others")
        self.assertIn("more than luck would explain", W.critics_headline(real)[1])
        few = dict(ov, stand_out={"verdict": "too few", "tested": 3, "min_shared": 10})
        self.assertEqual(W.critics_headline(few)[0], "Too few shared films to tell critics apart yet")
        flat = dict(ov, typical=dict(ov["typical"], lift=0.01))
        self.assertEqual(W.critics_headline(flat)[0], "Critics' verdicts barely track your ratings")
        self.assertEqual(W.critics_headline({"ok": False, "reason": "no_reviews", "error": "x"})[0],
                         "No critic reviews in this database")
        self.assertIn("No critic reliably disagrees", W.critics_list_hint(ov, "furthest"))
        self.assertIn("2 critics disagree with you", W.critics_list_hint(dict(ov, anti_twins=2), "furthest"))
        self.assertIn("(7+ out of 10)", W.critics_list_hint(ov, "closest"))
        self.assertEqual([W.match_text(v) for v in (0.133, -0.104, 0.001, None)], ["+13", "-10", "+0", ""])

    def test_an_honest_verdict_on_the_test(self):
        answer = {"films_covered": 140, "films_tested": 173,
                  "methods": {"Your closest critics": {"rank_agreement": 0.431, "top_fifth_you_rated_8_plus": 0.70},
                              "All critics": {"rank_agreement": 0.556, "top_fifth_you_rated_8_plus": 0.72},
                              "Rotten Tomatoes Tomatometer": {"rank_agreement": 0.624,
                                                              "top_fifth_you_rated_8_plus": 0.83},
                              "IMDb rating": {"rank_agreement": 0.684, "top_fifth_you_rated_8_plus": 0.88},
                              "Watch Next's model": {"rank_agreement": 0.693, "top_fifth_you_rated_8_plus": 0.92}},
                  "picks": {"closest": {"mostly_fresh": 88, "liked": 0.803}, "all": {"liked": 0.824},
                            "liked_overall": 0.68},
                  "model_check": {"without_critics": {"mean_error": 0.691, "rank_agreement": 0.689},
                                  "with_critics": {"mean_error": 0.687, "rank_agreement": 0.694}, "helps": False}}
        # behind every other way, not just level with critics in general: said so
        title, text = W.critics_verdict(answer)
        self.assertEqual(title, "Your closest critics are the weakest guide here")
        for words in ("rank agreement 0.43", "Critics as a whole: 0.56", "Watch Next's own model 0.69",
                      "you liked it 80% of the time", "from 0.691 to 0.687 points", "no measurable gain",
                      "Watch Next doesn't use them"):
            self.assertIn(words, text)
        # ...and why, when the answer says how many verdicts a film their order rests on; and whether choosing
        # critics by how they agree with you beats choosing them blind
        told = dict(answer, verdicts_per_film={"closest_one": 74, "closest_two": 41, "closest_more": 25,
                                               "all_critics_average": 14.6},
                    random_panels={"panels": 100, "size": 25, "rank_agreement": 0.322, "at_or_above_closest": 0.04})
        text = W.critics_verdict(told)[1]
        self.assertIn("Most of these films were reviewed by only one or two of your closest critics (74 by one, 41 by "
                      "two), while critics as a whole have about 15 reviews a film - and one verdict can only say "
                      "Fresh or Rotten.", text)
        self.assertIn("Critics picked at random, 25 at a time, manage 0.32 on average (4% of those panels do as well "
                      "as yours) - so choosing the ones in step with you does help.", text)
        lucky = dict(told, random_panels=dict(told["random_panels"], at_or_above_closest=0.3))
        self.assertIn("less, but 30% of those panels do as well as yours, so choosing them by how they agree with you "
                      "may not help", W.critics_verdict(lucky)[1])
        blind = dict(told, random_panels=dict(told["random_panels"], rank_agreement=0.44))
        self.assertIn("about the same, so choosing them by how they agree with you hasn't helped yet",
                      W.critics_verdict(blind)[1])
        blind["random_panels"]["rank_agreement"] = 0.55
        self.assertIn("better, so choosing them by how they agree with you doesn't help", W.critics_verdict(blind)[1])
        level = dict(answer, methods=dict(answer["methods"], **{"Your closest critics": {
            "rank_agreement": 0.56, "top_fifth_you_rated_8_plus": 0.72}}))
        self.assertEqual(W.critics_verdict(level)[0],
                         "Your closest critics are no better a guide than critics in general")
        self.assertNotIn("one or two of your closest", W.critics_verdict(dict(level, **{
            "verdicts_per_film": told["verdicts_per_film"]}))[1])       # (only when they trail critics in general)
        # a change of a thousandth reads as one, not a hundredth
        tiny = dict(answer, model_check={"without_critics": {"mean_error": 0.674, "rank_agreement": 0.699},
                                         "with_critics": {"mean_error": 0.675, "rank_agreement": 0.7},
                                         "helps": False})
        text = W.critics_verdict(tiny)[1]
        self.assertIn("changed its average miss from 0.674 to 0.675 points and its rank agreement from 0.699 to "
                      "0.700", text)
        self.assertNotIn("0.67 to 0.68", text)
        self.assertEqual((W._from_to(0.70, 0.66), W._from_to(0.674, 0.675)), ("from 0.70 to 0.66",
                                                                              "from 0.674 to 0.675"))
        better = dict(answer, methods=dict(answer["methods"], **{"Your closest critics": {
            "rank_agreement": 0.8, "top_fifth_you_rated_8_plus": 0.97}}))
        self.assertEqual(W.critics_verdict(better)[0], "Your closest critics are your best guide here")
        middling = dict(answer, methods=dict(answer["methods"], **{"Your closest critics": {
            "rank_agreement": 0.65, "top_fifth_you_rated_8_plus": 0.9}}))
        self.assertEqual(W.critics_verdict(middling)[0],
                         "Your closest critics beat critics in general - but not IMDb or Watch Next's model")
        helps = dict(answer, model_check=dict(answer["model_check"], helps=True))
        self.assertIn("a measurable gain", W.critics_verdict(helps)[1])
        panels = W.critics_eval_panels(answer)
        self.assertEqual([p["title"] for p in panels], ["Rank agreement", "Top picks you rated 8+"])
        self.assertEqual([r["label"] for r in panels[0]["rows"]],
                         ["Your closest critics", "All critics", "Tomatometer", "IMDb", "Watch Next's model"])
        self.assertTrue(panels[0]["rows"][0]["emphasis"])
        self.assertEqual(panels[1]["note"], "Each one's top fifth - higher is better")
        # the top picks' yardstick: how many of these films you rated 8+ at all
        based = W.critics_eval_panels(dict(answer, share_you_rated_8_plus=0.413))
        self.assertEqual(based[1]["note"], "Each one's top fifth - higher is better - 41% of these films are 8+")
        self.assertIn("Tested on 140 of the 173", W.critics_method_text(answer, 4.2))

    def test_how_a_match_adds_up(self):
        """A critic's Match from the numbers shown beside it: their own yardstick, what they managed, and the pull
        toward 0 with few films (made-up numbers)."""
        critic = {"shared": 60, "agreed": 42, "agreement": 0.70, "expected": 0.62, "chance": 0.51,
                  "match": 0.066, "fresh_share": 0.58, "rank": 17, "ranked": 180}
        self.assertEqual(W.match_words(critic),
                         "Match +7 (#17 of 180): a typical critic who says Fresh as often as they do (58% of the "
                         "time) would agree with you on 62% of these films; they managed 70% - 8 points above, "
                         "counted as +7 because 60 shared films still leave room for luck. (A coin tossed Fresh as "
                         "often would manage 51%.)")
        perfect = dict(critic, shared=8, agreed=8, agreement=1.0, expected=0.611, match=0.111, rank=3)
        self.assertIn("they managed 100% - 39 points above, counted as +11 because 8 shared films",
                      W.match_words(perfect))
        below = dict(critic, agreement=0.4, expected=0.721, match=-0.107, shared=10, rank=None)
        self.assertTrue(W.match_words(below).startswith("Match -11: "))
        self.assertIn("they managed 40% - 32 points below, counted as -11", W.match_words(below))
        many = dict(critic, shared=2000, agreement=0.736, expected=0.636, match=0.099)
        self.assertIn("they managed 74% - 10 points above.", W.match_words(many))    # (no pull worth a point)
        self.assertIn("Match +7 (#17 of 180): more in step with you than a typical critic",
                      W.match_words(dict(critic, expected=None)))                   # an answer without it


class CriticsViewTests(unittest.TestCase):
    """The Your critics view, on a collection with critics: Twin Tina agrees on every film, Anti Andy on none."""

    @classmethod
    def setUpClass(cls):
        cls.catalog = critic_catalog()

    def setUp(self):
        try:
            self.app = FakeApp(self.catalog)
            self.root, self.tab = build(self.app)
        except Exception as exc:          # noqa: BLE001
            self.skipTest(f"Tk unavailable: {exc}")

    def tearDown(self):
        self.tab._cancel_timers()
        self.root.destroy()

    def open_critics(self):
        tab = self.tab
        tab.catalog_changed(self.catalog, "ready")
        tab.views.select(tab.critics_page)
        self.root.update()

    def ids(self):
        from projectionist import critics
        return {c.name: c.id for c in critics.model(self.catalog).critics.values()}

    def list_names(self):
        t = self.tab.critic_table
        return [t.rows[i]["name"] for i in t.tree.get_children()]

    def test_the_views_in_order(self):
        tab = self.tab
        self.assertEqual([tab.views.tab(p, "text") for p in tab.views.tabs()],
                         ["Recommendations", "Your taste", "Your critics", "How accurate is this?"])
        tab.catalog_changed(self.catalog, "ready")
        tab.views.select(tab.critics_page)
        self.assertEqual(tab._view(), "critics")

    def test_the_critic_finder_goes_under_the_buttons_when_they_dont_all_fit(self):
        """A wider font (Linux's), or a larger text size, and the three lists' buttons plus 'Find' and its box are
        wider than the list: the finder goes on a line of its own under them, rather than being cut off (its 'Find'
        squeezed out) - and back beside them when there's room."""
        tab = self.tab
        finder = tab.critic_finder

        def row():
            return int(str(finder.grid_info()["row"]))
        need = sum(b.winfo_reqwidth() + 2 for b in tab.critic_list_buttons.values()) + finder.winfo_reqwidth()
        self.assertTrue(tab._fit_critic_bar(need // 2))
        self.assertEqual(row(), 1)
        self.assertTrue(tab._fit_critic_bar(1))                           # (not laid out: as it is)
        self.assertFalse(tab._fit_critic_bar(need + int(40 * tab.s)))
        self.assertEqual(row(), 0)
        self.assertEqual(finder.grid_info()["sticky"], "e")

    def test_your_critics(self):
        self.open_critics()
        tab, app = self.tab, self.app
        self.assertEqual(app.keys.count("watchnext.critics"), 1)            # one job did it all
        self.assertEqual(tab._critics_state, "ready")
        ov = tab.critics_answer
        self.assertEqual(tab.critics_title.cget("text"), W.critics_headline(ov)[0])
        self.assertIn("really", tab.critics_title.cget("text"))             # Tina and Andy are no accident
        self.assertIn("A typical critic agrees with you on", tab.critics_text.cget("text"))
        self.assertEqual(tab.critics_picks_link.winfo_manager(), "grid")
        self.assertEqual(self.list_names()[0], "Twin Tina")
        self.assertEqual(tab.critic_table.rows[tab.critic_table.tree.get_children()[0]]["agreement"], "100%")
        tab.critic_list_var.set("furthest")
        tab._critic_list_changed()
        self.assertEqual(self.list_names()[0], "Anti Andy")
        self.assertIn("disagree", tab.critic_list_hint.cget("text"))
        tab.critic_list_var.set("all")
        tab._critic_list_changed()
        self.assertEqual(len(self.list_names()), len(ov["points"]))
        self.assertIn(f"Everyone ({len(ov['points'])})", tab.critic_list_buttons["all"].cget("text"))
        # every critic's dot, the grey band, and the lines, with a legend
        self.assertTrue(chart_items(tab.funnel, 700, 320))
        shown = " ".join(texts(tab.funnel))
        for words in ("Every critic you share 5+ films with", "Closest to you", "Least in step", "a typical critic",
                      "a coin", "Films you've both judged"):
            self.assertIn(words, shown)
        # the closest critic is shown first
        info = tab.critic_info.get("1.0", "end")
        self.assertIn("Twin Tina", info)
        self.assertIn("Agreed with you on 40 of the 40 films you've both judged (100%)", info)
        self.assertIn("When they say Fresh you rate it 9.0 on average (20 films); Rotten, 5.0 (20).", info)
        self.assertIn("Where you agreed (40)", tab.critic_films_text.get("1.0", "end"))
        self.assertIn("Fresh from them, not played by you (8)", tab.critic_picks_text.get("1.0", "end"))
        self.assertTrue(chart_items(tab.critic_strip, 330, 210))
        self.assertIn("Your ratings of the 40 films you share", texts(tab.critic_strip))
        self.assertIn("Twin Tina", " ".join(texts(tab.funnel)))              # the chosen dot is named
        self.assertTrue(any("Watch Next: compared" in s for s in app.statuses))

    def test_choosing_a_critic(self):
        self.open_critics()
        tab = self.tab
        ids = self.ids()
        tab.critic_list_var.set("furthest")
        tab._critic_list_changed()
        first = tab.critic_table.tree.get_children()[0]
        tab.critic_table.tree.selection_set(first)
        self.root.update()
        self.assertEqual(tab.critic_answer["name"], "Anti Andy")
        info = tab.critic_info.get("1.0", "end")
        self.assertIn("Agreed with you on 0 of the 40", info)
        self.assertIn("They disagree with you more often than chance would", info)
        self.assertIn("Panned by them, not played by you (4)", tab.critic_picks_text.get("1.0", "end"))
        # a dot on the chart picks its critic
        spots = spots_of(tab.funnel)
        mia = next(s for s in spots if s[5] == ids["Mid Mia"])
        mia[6](mia[5])
        self.assertEqual(tab.critic_answer["name"], "Mid Mia")
        self.assertIn("Where you didn't (2)", tab.critic_films_text.get("1.0", "end"))
        self.assertIn("Mid Mia", " ".join(texts(tab.funnel)))
        # ...and so does 'Find'
        tab._critic_typed("Steady Stu")
        self.assertEqual(tab.critic_answer["name"], "Steady Stu")
        tab._critic_typed("steady")                                          # a name typed, not picked
        self.assertEqual(tab.critic_answer["name"], "Steady Stu")
        tab._critic_typed("Zzqx Blorp")
        self.assertEqual(self.app.statuses[-1], "No critic called “Zzqx Blorp” in your collection.")
        self.assertIn("Twin Tina", tab._suggest_critics("tw"))
        # a critic with no films in common still shows their picks
        tab._select_critic(ids["Lone Lou"])
        self.assertIn("You haven't rated any film they reviewed", tab.critic_info.get("1.0", "end"))
        self.assertIn("New 6", tab.critic_picks_text.get("1.0", "end"))
        self.assertEqual(tab.critic_table.tree.selection(), ())             # (not in the list)
        self.assertEqual(self.app.keys.count("watchnext.critic"), 0)         # quick: no job needed

    def test_films_open_their_page(self):
        self.open_critics()
        tab, app = self.tab, self.app
        links = dict(tab.critic_links)
        label = next(text for text in links if text.startswith("Liked"))
        links[label]()
        key = next(f.key for f in self.catalog.films.values() if f.label == label)
        self.assertEqual(app.gone[-1], ("Film", {"film_key": key, "title": label}))
        pick = next(text for text in links if text.startswith("New"))
        links[pick]()
        self.assertEqual(app.gone[-1][1]["title"], pick)
        # ...and through the text's own bindings: underlined under the pointer, then clicked
        linker = tab._critic_linkers[1]
        t = linker.text
        tag = next(tag for tag in t.tag_names() if tag in linker.commands)
        t.mark_set("current", f"{t.tag_ranges(tag)[0]} + 1 chars")
        linker._hover()
        self.assertEqual(str(t.cget("cursor")), "hand2")
        app.gone.clear()
        linker.clicked()
        self.assertEqual(app.gone[0][0], "Film")
        linker.left()
        self.assertEqual(str(t.cget("cursor")), "arrow")
        # a dot on the strip of your ratings opens that film
        spots = spots_of(tab.critic_strip, 330, 210)
        self.assertEqual(len(spots), 40)
        spots[0][6](spots[0][5])
        film = self.catalog.films[spots[0][5]]
        self.assertEqual(app.gone[-1], ("Film", {"film_key": film.key, "title": film.label}))
        self.assertIn("you rated it", spots[0][4])
        # refilling the details leaves nothing behind in Tk
        self.root.update()
        commands = len(self.root.tk.call("info", "commands"))
        for name in ("Anti Andy", "Mid Mia", "Twin Tina") * 3:
            tab._select_critic(self.ids()[name])
        self.root.update()
        self.assertLessEqual(len(self.root.tk.call("info", "commands")), commands + 5)

    def test_navigate_to_a_critic(self):
        tab, app = self.tab, self.app
        ids = self.ids()
        tab.catalog_changed(self.catalog, "ready")
        tab.views.select(tab.acc_page)
        tab.navigate(critic=ids["Mid Mia"])
        self.root.update()
        self.assertEqual(tab._view(), "critics")
        self.assertEqual(tab.critic_answer["name"], "Mid Mia")
        self.assertEqual(self.list_names()[tab.critic_table.tree.index(tab.critic_table.tree.selection()[0])],
                         "Mid Mia")
        tab.navigate(critic=ids["Twin Tina"])                                # the page is ready: at once
        self.assertEqual(tab.critic_answer["name"], "Twin Tina")
        tab.navigate(critic="99999")
        self.assertEqual(app.statuses[-1], "No critic numbered 99999 in your collection.")
        self.assertEqual(tab.critic_answer["name"], "Twin Tina")            # still showing the last one
        tab.navigate(critic="Zzqx Nobody")                                   # (said as every tab says it)
        self.assertEqual(app.statuses[-1], "No critic called “Zzqx Nobody” in your collection.")
        tab.navigate(critic="Anti Andy")                                     # a name works too
        self.assertEqual(tab.critic_answer["name"], "Anti Andy")
        # he isn't among the closest: the list switches to one he's in, and picks him out
        self.assertEqual(tab.critic_list_var.get(), "furthest")
        selected = tab.critic_table.tree.selection()
        self.assertEqual(tab.critic_table.rows[selected[0]]["name"], "Anti Andy")

    def test_navigate_before_the_collection_is_ready(self):
        tab, app = self.tab, self.app
        ids = self.ids()
        tab.catalog_changed(None, "loading")
        tab.navigate(critic=ids["Steady Stu"])                               # remembered...
        self.assertEqual(app.keys, [])
        tab.catalog_changed(self.catalog, "ready")                           # ...and shown once it's read
        self.root.update()
        self.assertEqual(tab._view(), "critics")
        self.assertEqual(tab.critic_answer["name"], "Steady Stu")
        self.assertIsNone(tab._pending_critic)
        # while the page is still being worked out: shown when it's done
        app2 = FakeApp(self.catalog, deferred=True)
        root2, tab2 = build(app2)
        try:
            tab2.catalog_changed(self.catalog, "ready")
            while app2.jobs:
                app2.finish(app2.jobs.pop(0))
            tab2.views.select(tab2.critics_page)
            root2.update()
            self.assertEqual([j[0].key for j in app2.jobs], ["watchnext.critics"])
            tab2.navigate(critic=ids["Five Fay"])
            app2.finish(app2.jobs.pop(0))
            self.assertEqual(tab2.critic_answer["name"], "Five Fay")
        finally:
            tab2._cancel_timers()
            root2.destroy()

    def test_your_critics_picks(self):
        tab = self.tab
        tab.catalog_changed(self.catalog, "ready")
        tab.vars["sort"].set("critics")
        tab._changed(now=True)
        self.assertEqual((tab.request["action"], tab.request["view"]), ("critics", "picks"))
        self.assertEqual(tab.results_title.cget("text"), "Your critics' picks")
        self.assertEqual(tab.table.tree.heading("lift", "text"), "Critics")
        first = tab.table.rows[tab.table.tree.get_children()[0]]
        self.assertEqual(str(first["lift"]), "2 of 2")
        # what's always true, not a test result it hasn't run: the test is Your critics' to report
        hint = tab.results_hint.cget("text")
        self.assertIn("Watch Next's own guess, which doesn't use these critics.", hint)
        self.assertNotIn("more accurate", hint)
        notes = tab.notes.cget("text")
        self.assertNotIn("don't use these critics", notes)                   # (the hint says it: not twice)
        self.assertNotIn("more accurate", notes)
        self.assertIn("the most one-sided first", notes)
        info = tab.info.get("1.0", "end")
        self.assertIn("Your critics", info)
        self.assertIn("Both of your closest critics who reviewed it called it Fresh.", info)
        self.assertIn("Twin Tina", info)
        dict(tab.links)["Twin Tina"]()                                       # a critic's name: that critic
        self.root.update()
        self.assertEqual(tab._view(), "critics")
        self.assertEqual(tab.critic_answer["name"], "Twin Tina")
        # back to the usual sort: the usual column
        tab.navigate(sort="predicted")
        self.assertEqual(tab.table.tree.heading("lift", "text"), "Lift")
        self.assertTrue(str(tab.table.rows[tab.table.tree.get_children()[0]]["lift"]).startswith(("+", "-")))
        # the details of any film now say what your critics said - without starting a job
        jobs_before = len(self.app.keys)
        tab.show_film(tab.answer["results"][0])
        self.assertEqual(len(self.app.keys), jobs_before)
        self.assertIn("Critics: ", tab.info.get("1.0", "end"))
        # the headline's link: your critics' picks, from anywhere
        tab.views.select(tab.critics_page)
        tab.critics_picks_link.event_generate("<Button-1>")
        self.assertEqual((tab._view(), tab.vars["sort"].get()), ("rec", "critics"))

    def test_nothing_to_compare(self):
        """A collection without critic reviews: calm messages, no errors."""
        plain = make()
        app = FakeApp(plain)
        root, tab = build(app)
        try:
            tab.catalog_changed(plain, "ready")
            tab.views.select(tab.critics_page)
            root.update()
            self.assertEqual(tab._critics_state, "problem")
            self.assertEqual(tab.critics_msg.winfo_manager(), "grid")
            self.assertEqual(tab.critics_scroll.winfo_manager(), "")                # (the page gives way to it)
            tab.critics_msg.redraw(wider(tab.critics_msg, 700), 180)
            self.assertIn("No critic reviews in this database", texts(tab.critics_msg))
            tab._critics_evaluate()                                          # nothing to test: nothing happens
            self.assertNotIn("watchnext.critics_eval", app.keys)
            tab.navigate(critic="1")
            self.assertIn("no critic reviews", app.statuses[-1])
            tab.navigate(sort="critics")
            tab.results_msg.redraw(wider(tab.results_msg, 600), 200)
            self.assertIn("No critic reviews to go on", texts(tab.results_msg))
            tab.show_film({"title": "X", "predicted_rating": 7.0, "key": "f0"})   # no 'Critics:' line, no error
            self.assertNotIn("Critics:", tab.info.get("1.0", "end"))
        finally:
            tab._cancel_timers()
            root.destroy()

    def test_a_critic_before_there_are_enough_ratings(self):
        """Too few rated films with reviews to compare: a critic asked for by a link or the search is still shown -
        their reviews and Fresh picks don't need your ratings - under the reason, without the list, the chart or
        the test (which need the comparison)."""
        import test_critics
        from projectionist import critics
        catalog = test_critics.plain_catalog()
        critics.use_reviews(catalog, [r for r in test_critics.review_rows(catalog, coins=0)
                                      if not catalog.films[r["film_key"]].title.startswith("Meh")])
        ids = {c.name: c.id for c in critics.model(catalog).critics.values()}
        app = FakeApp(catalog)
        root, tab = build(app)
        try:
            tab.catalog_changed(catalog, "ready")
            tab.views.select(tab.critics_page)
            root.update()
            self.assertEqual((tab._critics_state, tab.critics_answer["reason"]), ("problem", "few_ratings"))
            self.assertEqual(tab.critics_msg.winfo_manager(), "grid")         # nobody asked for: the reason alone
            tab.navigate(critic=ids["Twin Tina"])                              # (the global search, a Film link)
            root.update()
            self.assertEqual(tab.critic_answer["name"], "Twin Tina")
            self.assertEqual((tab.critics_scroll.winfo_manager(), tab.critics_msg.winfo_manager()), ("grid", ""))
            self.assertEqual(tab.critics_title.cget("text"), "Not enough ratings to compare yet")
            self.assertIn("rate at least 30", tab.critics_text.cget("text"))
            for widget in (tab.critics_row, tab.critics_eval_card, tab.critics_picks_link):
                self.assertEqual(widget.winfo_manager(), "")
            self.assertEqual(tab.critic_card.winfo_manager(), "grid")
            self.assertIn("Rate more films critics reviewed to see how in step they are with you",
                          tab.critic_info.get("1.0", "end"))
            self.assertIn("Shown once there are enough ratings", tab.critic_films_text.get("1.0", "end"))
            self.assertIn("Fresh from them, not played by you (8)", tab.critic_picks_text.get("1.0", "end"))
            tab.critic_strip.redraw(wider(tab.critic_strip, 330), 210)
            self.assertIn("Not compared yet", texts(tab.critic_strip))
            tab.navigate(critic="Anti Andy")                                   # another, by name
            self.assertEqual(tab.critic_answer["name"], "Anti Andy")
            tab.navigate(critic="99999")
            self.assertEqual(app.statuses[-1], "No critic numbered 99999 in your collection.")
            # asked for before the page was worked out: shown once it is
            tab.views.select(tab.acc_page)
            tab.catalog_changed(catalog, "ready")
            tab.navigate(critic=ids["Mid Mia"])
            root.update()
            self.assertEqual((tab._view(), tab.critic_answer["name"]), ("critics", "Mid Mia"))
            self.assertEqual(tab.critics_row.winfo_manager(), "")
            # enough ratings: the whole page is back
            app.catalog = self.catalog
            tab.catalog_changed(self.catalog, "ready")
            root.update()
            self.assertEqual(tab._critics_state, "ready")
            for widget in (tab.critics_row, tab.critics_eval_card, tab.critics_picks_link):
                self.assertEqual(widget.winfo_manager(), "grid")
        finally:
            tab._cancel_timers()
            root.destroy()

    def test_the_lines_words_stay_clear_of_the_chosen_critic(self):
        """The critic who shares the most films sits at the right end of the funnel - where 'a typical critic 68%'
        goes: the words move out of the way of the ring and the name rather than hide under them."""
        tab = self.tab
        ov = {"typical": {"agreement": 0.68, "chance": 0.57}, "min_shared": 5}
        points = [{"id": str(n), "name": f"Critic Number {n}", "publication": "", "shared": s, "agreed": a,
                   "match": 0.0} for n, (s, a) in enumerate([(75, 53), (40, 22), (12, 8), (6, 4), (5, 2), (20, 11)])]

        def boxes(tag):
            return [tab.funnel.bbox(i) for i in tab.funnel.find_withtag(tag)]

        def clash(a, b):
            return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]

        for selected in ("0", "1", "2", None):
            tab.funnel.show(lambda p, sel=selected: W.draw_critic_funnel(p, ov, points, ["0"], ["4"], sel))
            for w, h in ((700, 320), (420, 300)):
                tab.funnel.redraw(w, h)
                words = [tab.funnel.itemcget(i, "text") for i in tab.funnel.find_withtag("linelabel")
                         if tab.funnel.type(i) == "text"]
                self.assertEqual(sorted(words), ["a coin 57%", "a typical critic 68%"])
                self.assertEqual(bool(boxes("selmark")), selected is not None)
                for mine in boxes("selmark"):
                    for theirs in boxes("linelabel"):
                        self.assertFalse(clash(mine, theirs), (selected, w, mine, theirs))
        # choosing another critic redraws the mark and the words - once each
        self.open_critics()
        tab._select_critic(self.ids()["Anti Andy"])
        shown = texts(tab.funnel)
        self.assertEqual(sum(1 for t in shown if t.startswith("a typical critic")), 1)
        self.assertEqual(sum(1 for t in shown if t == "Anti Andy"), 1)

    def test_the_test_of_them(self):
        self.open_critics()
        tab, app = self.tab, self.app
        tab.critics_eval_view.redraw(wider(tab.critics_eval_view, 700), 196)
        self.assertIn("Not tested yet", texts(tab.critics_eval_view))
        tab._critics_evaluate()
        self.assertEqual(app.keys[-1], "watchnext.critics_eval")
        self.assertTrue(tab.critics_eval_answer["ok"])
        self.assertTrue(chart_items(tab.critics_eval_view, 900, 196))
        shown = " ".join(texts(tab.critics_eval_view))
        for words in ("Rank agreement", "Top picks you rated 8+", "Your closest critics", "Tomatometer", "IMDb"):
            self.assertIn(words, shown)
        self.assertTrue(tab.critics_verdict_title.cget("text"))
        verdict = tab.critics_verdict.cget("text")
        if not tab.critics_eval_answer["model_check"]["helps"]:
            self.assertIn("Watch Next doesn't use them", verdict)
        self.assertIn("Took", tab.critics_method.cget("text"))
        self.assertEqual(str(tab.critics_eval_btn.cget("state")), "normal")
        # a reload forgets it
        tab.catalog_changed(self.catalog, "ready")
        self.assertIsNone(tab.critics_eval_answer)

    def test_stale_answers_are_dropped(self):
        app = FakeApp(self.catalog, deferred=True)
        root, tab = build(app)
        try:
            tab.catalog_changed(self.catalog, "ready")
            while app.jobs:
                app.finish(app.jobs.pop(0))
            tab.views.select(tab.critics_page)
            root.update()
            app.finish(app.jobs.pop(0))                                      # the critics page
            tab._critics_evaluate()
            self.assertEqual(str(tab.critics_eval_btn.cget("state")), "disabled")
            self.assertEqual(tab.critics_eval_progress.winfo_manager(), "grid")
            self.assertIn("Testing on the 40 films", tab.critics_eval_status.cget("text"))
            tab._critics_evaluate()                                          # already running
            self.assertEqual(len(app.jobs), 1)
            held = app.jobs.pop(0)
            tab.catalog_changed(self.catalog, "ready")                       # a reload: the answer is stale
            app.finish(held)                                                 # (the token drops it)
            self.assertIsNone(tab.critics_eval_answer)
            self.assertEqual(str(tab.critics_eval_btn.cget("state")), "normal")
            self.assertEqual(tab.critics_eval_progress.winfo_manager(), "")
        finally:
            tab._cancel_timers()
            root.destroy()

    def test_layout_adapts(self):
        import tkinter as tk
        tab = self.tab
        tab._reflow_critics(700)
        self.assertEqual(tab.funnel_card.grid_info()["row"], 1)             # one above the other
        tab._reflow_critics(wider(tab.frame, 1400))
        self.assertEqual((tab.funnel_card.grid_info()["row"], tab.funnel_card.grid_info()["column"]), (0, 1))
        tab._reflow_critic_lists(500)
        self.assertEqual(int(tab.critic_picks_text.grid_info()["row"]), 1)
        tab._reflow_critic_lists(wider(tab.frame, 900))
        self.assertEqual(int(tab.critic_picks_text.grid_info()["column"]), 1)
        event = tk.Event()
        event.width = 380
        tab._fit_critic_columns(event)
        self.assertNotIn("publication", tab.critic_table.tree.tk.splitlist(
            tab.critic_table.tree.cget("displaycolumns")))
        event.width = wider(tab.frame, 600)
        tab._fit_critic_columns(event)
        self.assertIn("publication", tab.critic_table.tree.tk.splitlist(tab.critic_table.tree.cget("displaycolumns")))


# ---------------------------------------------------------------------------------------------------------
# Settings > Watch Next > 'Never suggest films from'
# ---------------------------------------------------------------------------------------------------------
class SettingsApp(FakeApp):
    """FakeApp with settings, as the main window keeps them: saved, and a change passed on to the tabs."""

    def __init__(self, catalog=None, settings=None, **kw):
        super().__init__(catalog, **kw)
        self.settings = dict(settings or {})
        self.saved = 0
        self.tabs = []

    def save_settings(self):
        self.saved += 1

    def preference_changed(self, key, value):
        for tab in self.tabs:
            tab.preference_changed(key, value)


class LeaveOutFunctionTests(unittest.TestCase):
    def test_the_setting(self):
        from projectionist import prefs
        p = prefs.pref(W.LEAVE_OUT)
        self.assertEqual((p.section, p.label, p.kind, p.live), ("Watch Next", "Never suggest films from",
                                                                 "leave_out", True))
        self.assertEqual(prefs.get(None, W.LEAVE_OUT), {"libraries": [], "genres": []})     # today's behaviour
        self.assertEqual([s.name for s in prefs.sections()].count("Watch Next"), 1)
        self.assertIn("Library or Genre", p.help)
        # kept tidy: each name once whatever its case, a lone name as a list; nonsense reads as nothing left out
        kept = {W.LEAVE_OUT: {"libraries": ["Concerts", " concerts ", ""], "genres": "Short"}}
        self.assertEqual(prefs.get(kept, W.LEAVE_OUT), {"libraries": ["Concerts"], "genres": ["Short"]})
        for bad in ("Short", 5, {"genres": [1, 2]}, {"libraries": {"a": 1}}):
            self.assertEqual(prefs.get({W.LEAVE_OUT: bad}, W.LEAVE_OUT), {"libraries": [], "genres": []}, bad)
        with self.assertRaises(ValueError):
            W.parse_leave_out(["Short"])
        # in words
        self.assertEqual(W.leave_out_words(["Concerts"], []), "the Concerts library")
        self.assertEqual(W.leave_out_words(["A", "B", "C"], ["Short", "TV Movie"]),
                         "the A, B and C libraries or the Short and TV Movie genres")
        self.assertEqual(W.leave_out_words(), "")
        self.assertEqual(p.words({"libraries": [], "genres": ["Short"]}), "Films in the Short genre")
        self.assertEqual(p.words({"libraries": [], "genres": []}), "Nothing left out")
        # the Film tab reads the same setting (without importing this tab)
        from projectionist.ui import film
        self.assertEqual(film.LEAVE_OUT, W.LEAVE_OUT)

    def test_requests_leave_them_out_unless_picked(self):
        left = {"libraries": ["Concerts", "Vault"], "genres": ["Short", "Music"]}
        req = W.build_request(dict(W.DEFAULTS), None, left)
        self.assertEqual((req["exclude_libraries"], req["exclude_genres"]),
                         (["Concerts", "Vault"], ["Short", "Music"]))
        # nothing left out: the request is as it always was
        self.assertEqual(W.build_request(dict(W.DEFAULTS), None, {"libraries": [], "genres": []}),
                         W.build_request(dict(W.DEFAULTS)))
        # an explicit pick wins: a library picked lifts the libraries, a genre picked that genre
        req = W.build_request(dict(W.DEFAULTS, library="Concerts", genre="music"), None, left)
        self.assertNotIn("exclude_libraries", req)
        self.assertEqual(req["exclude_genres"], ["Short"])
        # your critics' picks leave them out too (the same filters)
        req = W.build_request(dict(W.DEFAULTS, sort="critics"), ("countries", "Japan"), left)
        self.assertEqual((req["action"], req["exclude_genres"], req["countries"]), ("critics", ["Short", "Music"],
                                                                                     "Japan"))

    def test_what_the_results_say(self):
        req = {"exclude_libraries": ["Asia"], "exclude_genres": ["Short"]}
        self.assertEqual(W.left_out_line(req, {"left_out": 3}), "Left out, as you asked in Settings: films in the "
                                                                "Asia library or the Short genre - 3 more would match.")
        self.assertEqual(W.left_out_line({"exclude_genres": "Short"}, {"left_out": 0}),
                         "Left out, as you asked in Settings: films in the Short genre.")
        self.assertEqual(W.left_out_line({"genre": "Drama"}), "")
        # nothing new but what's left out: said so, and how to see them anyway
        heading, sub = W.empty_message(dict(req, with_id="p:1"), {"played_matches": 0, "left_out": 1})
        self.assertEqual(heading, "Every film that matches is left out")
        self.assertEqual(sub, "1 film would match, but Settings leaves out films in the Asia library or the Short "
                              "genre. Choose one of those in the Library or Genre box to see it anyway.")
        sub = W.empty_message({"exclude_genres": ["Short"]}, {"left_out": 4})[1]
        self.assertIn("4 films would match", sub)
        self.assertIn("in the Genre box to see them anyway", sub)
        # left out, but nothing that would have matched: the usual words
        self.assertEqual(W.empty_message(dict(req, with_id="p:1"), {"played_matches": 0, "left_out": 0})[0],
                         "You've rated every film of theirs")
        self.assertEqual(W.search_status({"ok": True, "results": [], "left_out": 2}, req, 0.1),
                         "Watch Next: every film that matches is left out.")


class LeaveOutBackboneTests(unittest.TestCase):
    """recommend's exclude_libraries / exclude_genres, and left_out: how many more films would match."""

    @classmethod
    def setUpClass(cls):
        cls.catalog = make()

    def ask(self, **request):
        return handle(dict(action="recommend", max_per_director=0, **request), catalog=self.catalog)

    def titles(self, answer):
        return sorted(r["title"] for r in answer["results"])

    def test_left_out(self):
        everything = self.ask()
        self.assertNotIn("left_out", everything)                     # (only said when something is left out)
        a = self.ask(exclude_libraries=["asia"])                      # any case
        self.assertEqual(self.titles(a), ["New Bob", "Prairie"])
        self.assertEqual(a["left_out"], 3)                            # New, Second and Third Ann (Seen Ann: played)
        self.assertEqual(a["played_matches"], 0)                      # (ticking 'played' wouldn't bring it back)
        self.assertEqual(a["model"]["trained_on"], everything["model"]["trained_on"])    # still learns from them
        self.assertEqual(self.ask(exclude_libraries="Asia", include_watched=True)["left_out"], 4)
        g = self.ask(exclude_genres=["Western", "Drama"])
        self.assertEqual(self.titles(g), ["New Ann", "Second Ann", "Third Ann"])
        self.assertEqual(g["left_out"], 2)
        both = self.ask(exclude_libraries=["Asia"], exclude_genres=["Western"])
        self.assertEqual((self.titles(both), both["left_out"]), (["New Bob"], 4))
        # the other filters first: left_out counts only films that would otherwise be results
        self.assertEqual(self.ask(exclude_libraries=["Asia"], text="swordsman")["left_out"], 1)
        self.assertEqual(self.ask(exclude_libraries=["Asia"], genre="Drama")["left_out"], 0)
        # a film in two libraries is left out if either is
        from test_projectionist import make_catalog
        two = make_catalog(specs() + [dict(title="Both", year=2004, genres=["Action"], cast=["Star A"],
                                           imdb_rating=7.0, libraries=["Movies", "Asia"])],
                           libraries=("Movies", "Asia"))
        answer = handle({"action": "recommend", "exclude_libraries": ["Asia"], "max_per_director": 0}, catalog=two)
        self.assertNotIn("Both", self.titles(answer))


class SeenByTests(unittest.TestCase):
    """Settings > Your collection's 'Also count as seen': films played on those accounts aren't suggested."""

    @classmethod
    def setUpClass(cls):
        cls.catalog = make([dict(s, played_by={7: 2}) if s["title"] == "New Ann" else s for s in specs()])

    def ask(self, **request):
        return handle(dict(action="recommend", max_per_director=0, **request), catalog=self.catalog)

    def test_the_backbone(self):
        mine = self.ask()
        self.assertIn("New Ann", [r["title"] for r in mine["results"]])
        seen = self.ask(also_seen_by=[7])
        self.assertNotIn("New Ann", [r["title"] for r in seen["results"]])
        self.assertEqual(seen["played_matches"], mine["played_matches"] + 1)     # (what 'include played' adds)
        self.assertTrue(any("also_seen_by" in n for n in seen["notes"]))
        self.assertIn("New Ann", [r["title"] for r in self.ask(also_seen_by=[7], include_watched=True)["results"]])
        self.assertEqual(self.ask(also_seen_by=["8"])["results"], mine["results"])     # (an account that didn't)
        bad = self.ask(also_seen_by="everyone")
        self.assertFalse(bad["ok"])
        self.assertIn("also_seen_by", bad["error"])
        # a film left out by Settings and seen: counted as seen, not as left out
        left = self.ask(also_seen_by=[7], exclude_libraries=["Asia"])
        self.assertEqual(left["left_out"], 2)                                    # Second and Third Ann
        from projectionist import ask
        self.assertIn("also_seen_by", ask.ACTIONS["recommend"])
        self.assertIn("also_seen_by", ask.ACTIONS["critics"])
        self.assertIn("also_seen_by", ask.ACTIONS["film"])

    def test_requests(self):
        req = W.build_request(dict(W.DEFAULTS), None, None, [7, 42])
        self.assertEqual(req["also_seen_by"], [7, 42])
        self.assertNotIn("also_seen_by", W.build_request(dict(W.DEFAULTS), None, None, []))
        req = W.build_request(dict(W.DEFAULTS, sort="critics"), None, None, [7])
        self.assertEqual((req["action"], req["also_seen_by"]), ("critics", [7]))
        self.assertEqual(W.empty_message({"with": "Ann", "also_seen_by": [7]}, {"played_matches": 2})[1],
                         "Tick 'Include films I've played but not rated' to see the 2 you've seen.")

    def test_the_tab_follows_the_setting(self):
        from projectionist import prefs
        try:
            app = SettingsApp(self.catalog, {W.SEEN_BY: [7]})
            root, tab = build(app)
        except Exception as exc:          # noqa: BLE001
            self.skipTest(f"Tk unavailable: {exc}")
        try:
            app.tabs.append(tab)
            tab.catalog_changed(self.catalog, "ready")
            root.update()
            self.assertEqual(tab.request["also_seen_by"], [7])
            self.assertNotIn("New Ann", titles(tab))
            self.assertEqual(tab._critic_request("c1")["also_seen_by"], [7])
            before = app.keys.count("watchnext.search")
            prefs.set(app, W.SEEN_BY, [])                                    # unticked: searched again at once
            self.assertEqual(app.keys.count("watchnext.search"), before + 1)
            self.assertNotIn("also_seen_by", tab.request)
            self.assertIn("New Ann", titles(tab))
            self.assertNotIn("also_seen_by", tab._critic_request("c1"))
        finally:
            tab._cancel_timers()
            root.destroy()


class FilterPanelTests(unittest.TestCase):
    """The Find films panel in a short window: its small print goes, so its buttons stay in view."""

    def test_small_print_makes_way_for_the_buttons(self):
        from tkinter import ttk
        try:
            root = hidden_root()
        except Exception as exc:          # noqa: BLE001
            self.skipTest(f"Tk unavailable: {exc}")
        app = FakeApp(make())
        notebook = ttk.Notebook(root)
        # (a window of a chosen size - for this computer's fonts - never shown)
        notebook.place(x=0, y=0, width=wider(root, 1000), height=wider(root, 700))
        tab = W.Tab(app, notebook)
        notebook.add(tab.frame, text=tab.title)
        app.tab = tab
        try:
            tab.catalog_changed(app.catalog, "ready")
            root.update()
            card = tab.filter_card

            def bottom_of(w):
                return w.winfo_rooty() + w.winfo_height() - card.winfo_rooty()
            self.assertEqual(len(tab.filter_hints), 2)
            self.assertTrue(all(h.winfo_manager() == "grid" for h in tab.filter_hints))
            full = card.winfo_reqheight()
            saved = 0                                                   # sizes where going saved the buttons
            for height in range(wider(root, 700), 440, -8):           # (the window made shorter and shorter)
                notebook.place_configure(height=height)
                root.update()
                root.update()
                have = card.winfo_height()
                shown = all(h.winfo_manager() == "grid" for h in tab.filter_hints)
                if have >= full:
                    self.assertTrue(shown, height)                      # (room for all of it: all of it)
                elif not shown and have >= card.winfo_reqheight():
                    self.assertLessEqual(bottom_of(tab.find_btn), have, height)     # (the buttons in view)
                    self.assertLessEqual(bottom_of(tab.reset_btn), have, height)
                    saved += 1
                self.assertFalse(shown and have < full, height)         # (never cut short with it showing)
            self.assertGreater(saved, 0)
            notebook.place_configure(height=wider(root, 700))          # (room again)
            root.update()
            root.update()
            self.assertTrue(all(h.winfo_manager() == "grid" for h in tab.filter_hints))
        finally:
            tab._cancel_timers()
            root.destroy()


class LeaveOutTabTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = make()

    def setUp(self):
        try:
            self.app = SettingsApp(self.catalog, {W.LEAVE_OUT: {"libraries": ["Asia"], "genres": []}})
            self.root, self.tab = build(self.app)
        except Exception as exc:          # noqa: BLE001
            self.skipTest(f"Tk unavailable: {exc}")
        self.app.tabs.append(self.tab)
        self.tab.catalog_changed(self.catalog, "ready")
        self.root.update()

    def tearDown(self):
        self.tab._cancel_timers()
        self.root.destroy()

    def searches(self):
        return self.app.keys.count("watchnext.search")

    def test_the_results_leave_them_out_and_say_so(self):
        from projectionist import prefs
        tab = self.tab
        self.assertEqual(sorted(titles(tab)), ["New Bob", "Prairie"])
        self.assertEqual(tab.request["exclude_libraries"], ["Asia"])
        self.assertEqual(tab.left_out_row.winfo_manager(), "grid")
        self.assertEqual(tab.left_out_label.cget("text"),
                         "Left out, as you asked in Settings: films in the Asia library - 3 more would match.")
        tab.left_out_link.command()                                           # 'Change': Settings, at its card
        self.assertEqual(self.app.gone[-1], ("Settings", {"section": "Watch Next"}))
        # an explicit pick wins - a library picked, or a taste bar clicked
        tab.navigate(library="asia")
        self.assertIn("New Ann", titles(tab))
        self.assertNotIn("exclude_libraries", tab.request)
        self.assertEqual(tab.left_out_row.winfo_manager(), "")                # (nothing was left out)
        # a change in Settings searches again at once, with the panel as it is
        tab._reset()
        before = self.searches()
        prefs.set(self.app, W.LEAVE_OUT, {"libraries": [], "genres": ["Drama", "western"]})
        self.assertEqual(self.searches(), before + 1)
        self.assertEqual(sorted(titles(tab)), ["New Ann", "Second Ann"])     # (two per director)
        self.assertEqual(tab.request["exclude_genres"], ["Drama", "western"])
        self.assertIn("the Drama and western genres", tab.left_out_label.cget("text"))
        tab.navigate(genre="drama")                                           # that genre picked: its films
        self.assertEqual(titles(tab), ["New Bob"])
        self.assertEqual(tab.request["exclude_genres"], ["western"])
        # nothing left out: as it always was
        prefs.set(self.app, W.LEAVE_OUT, W.nothing_left_out())
        self.assertNotIn("exclude_genres", tab.request)
        self.assertEqual(tab.left_out_row.winfo_manager(), "")
        # other settings don't search again
        before = self.searches()
        tab.preference_changed("csv", True)
        self.assertEqual(self.searches(), before)

    def test_nothing_new_but_what_is_left_out(self):
        tab = self.tab
        tab.navigate(person="Ann Auteur")                                     # all hers are in Asia
        tab.results_msg.redraw(wider(tab.results_msg, 600), 200)
        shown = texts(tab.results_msg)
        self.assertIn("Every film that matches is left out", shown)
        self.assertTrue(any("3 films would match" in t for t in shown), shown)
        self.assertEqual(self.app.statuses[-1], "Watch Next: every film that matches is left out.")

    def test_a_change_waits_until_the_tab_is_looked_at(self):
        from projectionist import prefs
        self.app.tab = None                                                   # another tab is in front
        before = self.searches()
        prefs.set(self.app, W.LEAVE_OUT, {"libraries": ["Movies"], "genres": []})
        self.assertEqual(self.searches(), before)
        self.app.tab = self.tab
        self.tab.shown()
        self.assertEqual(self.searches(), before + 1)
        self.assertEqual(sorted(titles(self.tab)), ["New Ann", "Second Ann"])


class LeaveOutSettingsRowTests(unittest.TestCase):
    """The Settings tab's row for it: a tick box for each library and genre in the collection."""

    def setUp(self):
        from tkinter import ttk

        from projectionist.ui import settings as S
        try:
            self.root = hidden_root()
        except Exception as exc:          # noqa: BLE001
            self.skipTest(f"Tk unavailable: {exc}")
        self.catalog = make()
        self.app = SettingsApp(self.catalog)
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True)
        self.settings = S.Tab(self.app, self.notebook)
        self.notebook.add(self.settings.frame, text=self.settings.title)
        self.app.tabs.append(self.settings)
        self.row = self.settings.controls[W.LEAVE_OUT]

    def tearDown(self):
        self.root.destroy()

    def box_texts(self):
        return {key: str(box.cget("text")) for key, box in self.row.boxes.items()}

    def test_ticking_saves_it(self):
        from tkinter import ttk
        row = self.row
        self.assertIsInstance(row, W.LeaveOutControl)
        self.assertEqual(self.box_texts(), {("libraries", "Movies"): "Movies  (22)",
                                            ("libraries", "Asia"): "Asia  (24)",
                                            ("genres", "Action"): "Action  (24)", ("genres", "Drama"): "Drama  (21)",
                                            ("genres", "Western"): "Western  (1)"})
        self.assertEqual([k for k in row.boxes if k[0] == "libraries"],
                         [("libraries", "Movies"), ("libraries", "Asia")])                # (the collection's order)
        self.assertTrue(all(isinstance(b, ttk.Checkbutton) and str(b.cget("takefocus")) not in ("0", "false")
                            for b in row.boxes.values()))
        self.assertTrue(row.clear.instate(["disabled"]))                      # (nothing to clear)
        row.boxes[("libraries", "Asia")].invoke()
        self.assertEqual(self.app.settings[W.LEAVE_OUT], {"libraries": ["Asia"], "genres": []})
        row.boxes[("genres", "Western")].invoke()
        self.assertEqual(self.app.settings[W.LEAVE_OUT], {"libraries": ["Asia"], "genres": ["Western"]})
        self.assertFalse(row.clear.instate(["disabled"]))
        row.boxes[("libraries", "Asia")].invoke()
        self.assertEqual(self.app.settings[W.LEAVE_OUT], {"libraries": [], "genres": ["Western"]})
        # a change made elsewhere shows here; 'Leave nothing out' clears it
        from projectionist import prefs
        prefs.set(self.app, W.LEAVE_OUT, {"libraries": ["movies"], "genres": []})
        self.assertTrue(row.vars[("libraries", "Movies")].get())
        self.assertFalse(row.vars[("genres", "Western")].get())
        row.clear.invoke()
        self.assertEqual(self.app.settings[W.LEAVE_OUT], {"libraries": [], "genres": []})
        self.assertFalse(any(v.get() for v in row.vars.values()))
        # and so does a reset
        prefs.set(self.app, W.LEAVE_OUT, {"libraries": ["Asia"], "genres": []})
        prefs.reset(self.app, section="Watch Next")
        self.assertNotIn(W.LEAVE_OUT, self.app.settings)

    def test_it_follows_the_collection(self):
        row = self.row
        # before a collection is read: only the names kept, and a word about the rest
        self.app.catalog = None
        self.app.settings[W.LEAVE_OUT] = {"libraries": ["Concerts"], "genres": []}
        row.refresh()
        self.assertEqual(self.box_texts(), {("libraries", "Concerts"): "Concerts"})
        self.assertTrue(row.vars[("libraries", "Concerts")].get())
        self.assertIn("once the collection has been read", row.note.cget("text"))
        # the Watch Next tab passes a newly read collection on: its libraries and genres, and a name kept that
        # it hasn't got, still ticked so it can be unticked
        self.app.catalog = self.catalog
        wn_tab = W.Tab(self.app, self.notebook)
        self.notebook.add(wn_tab.frame, text=wn_tab.title)
        try:
            wn_tab.catalog_changed(self.catalog, "ready")
        finally:
            wn_tab._cancel_timers()
        self.assertEqual(row.note.cget("text"), "")
        texts = self.box_texts()
        self.assertEqual(texts[("libraries", "Concerts")], "Concerts  (none on your shelf)")
        self.assertEqual(texts[("libraries", "Asia")], "Asia  (24)")
        row.boxes[("libraries", "Concerts")].invoke()
        self.assertEqual(self.app.settings[W.LEAVE_OUT], {"libraries": [], "genres": []})
        self.assertIn(("libraries", "Concerts"), row.boxes)        # (until the row is made again)
        row.refresh()
        self.assertNotIn(("libraries", "Concerts"), row.boxes)

    def test_help_and_keyboard(self):
        help_text = str(self.row.help.cget("text"))
        self.assertTrue(help_text.startswith("Watch Next's suggestions leave out films in the libraries and genres"))
        self.assertNotIn("next time the app opens", help_text)                 # (it applies at once)
        self.assertEqual(str(self.row.clear.cget("text")), "Leave nothing out")
        self.assertIn("Watch Next", self.settings.cards)


if __name__ == "__main__":
    unittest.main()
