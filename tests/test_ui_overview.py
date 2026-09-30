"""Tests for the Overview tab (projectionist/ui/overview.py). Fully headless: every Tk root is withdrawn and
never shown; charts are drawn with ChartView.redraw(width, height) and clicks are fired through the canvas
bindings the charts set up."""

import os
import re
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from projectionist import jobs  # noqa: E402
from projectionist.ask import handle  # noqa: E402
from projectionist.ui import overview as O  # noqa: E402
from test_projectionist import build_fixture, make_catalog  # noqa: E402


# Every chart and the size it gets in a 1280x800 window (two cards to a row)
CHART_SIZES = {"kpi": (1200, 78), "decades": (583, 250), "libraries": (583, 250), "genres": (583, 320),
               "countries": (583, 320), "actors": (583, 320), "directors": (583, 320), "ratings": (583, 320),
               "scores": (583, 320), "played": (583, 170), "resolutions": (583, 170)}


def hidden_root():
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()            # headless: never show a window
    return root


class FakeApp:
    """The services a tab uses (see ui/base.py), answering against an in-memory catalog."""

    def __init__(self, catalog=None, state="ready"):
        self.catalog, self.catalog_state, self.catalog_error = catalog, state, ""
        self.gotos, self.statuses, self.runs, self.keys = [], [], 0, []
        self.deferred = None             # set to a list to hold background work back until finish()
        self._keyed = {}

    def ask(self, request):
        return handle(request, catalog=self.catalog)

    def run(self, work, done=None, failed=None, status=None, key=None):      # synchronous in tests
        """As App.run: returns the job; a newer run with the same key calls the older one off (it never
        answers)."""
        self.runs += 1
        self.keys.append(key)
        if status:
            self.statuses.append(status)
        if key is not None and key in self._keyed:
            self._keyed.pop(key).cancel()
        job = jobs.Job(key)
        if key is not None:
            self._keyed[key] = job
        if self.deferred is not None:
            self.deferred.append((job, work, done, failed))
            return job
        self._finish(job, work, done, failed)
        return job

    def _finish(self, job, work, done, failed):
        if job.cancelled:
            return
        with jobs.running(job):
            try:
                result = work()
            except jobs.Cancelled:
                return
            except Exception as exc:
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

    def finish(self):
        held, self.deferred = self.deferred or [], None
        for job in held:
            self._finish(*job)

    def goto(self, tab_title, /, **kwargs):             # (as App.goto: 'title' can be an argument)
        self.gotos.append((tab_title, kwargs))
        return object()

    def set_status(self, text):
        self.statuses.append(text)


def films():
    """A small collection with everything the overview shows: years, libraries, genres, countries, cast,
    directors, your ratings and plays, and resolutions."""
    specs = []
    for i in range(12):
        specs.append({
            "title": f"Film {i}", "year": 1960 + i * 5, "libraries": ["Movies"] if i % 3 else ["Movies-World"],
            "genres": ["Action", "Drama"] if i % 2 else ["Comedy"],
            "countries": ["United States of America"] if i % 4 else ["Hong Kong", "Taiwan, Province of China"],
            # 'Extra Person' is billed fourth in everything: top of 'all roles', never a lead
            "cast": (["Jackie Chan", "Sidekick A", "Sidekick B"] if i < 5 else
                     ["Bruce Willis", "Sidekick C", "Sidekick D"]) + ["Extra Person"],
            "directors": ["John Woo"] if i < 4 else ["Chang Cheh"],
            "runtime_min": 100, "imdb_rating": 5.0 + i * 0.3,
            "owner_rating": float(4 + i % 6) if i < 9 else None, "owner_plays": 1 if i < 7 else 0,
            "resolution": ["4K", "1080p", "480p", "SD"][i % 4],
        })
    specs.append({"title": "Undated", "year": None, "libraries": ["Movies"]})
    return make_catalog(specs, libraries=("Movies", "Movies-World"))


def texts(view):
    return [view.itemcget(i, "text") for i in view.find_withtag("chart") if view.type(i) == "text"]


def click(view, tag):
    """Fire a chart item's click binding (hidden windows get no real pointer events)."""
    script = view.tag_bind(tag, "<Button-1>")
    if not script:
        raise AssertionError(f"{tag} isn't clickable")
    view.tk.eval(re.sub(r"%[#\w]", "0", script))


class OverviewTestCase(unittest.TestCase):
    def setUp(self):
        try:
            self.root = hidden_root()
        except Exception as exc:
            self.skipTest(f"Tk unavailable: {exc}")
        from tkinter import ttk
        from projectionist.ui import theme
        theme.apply_styles(self.root)
        self.notebook = ttk.Notebook(self.root)

    def tearDown(self):
        self.root.destroy()

    def make_tab(self, catalog=None, state="ready"):
        app = FakeApp(catalog, state)
        tab = O.Tab(app, self.notebook)
        return app, tab

    def ready_tab(self, catalog=None):
        catalog = catalog or films()
        app, tab = self.make_tab(catalog)
        tab.catalog_changed(catalog, "ready")
        tab.shown()
        return app, tab

    def draw(self, tab, key):
        w, h = CHART_SIZES[key]
        view = tab.views[key]
        view.redraw(w, h)
        return view


# ---------------------------------------------------------------------------------------------------------
class PlaceholderTests(OverviewTestCase):
    def test_placeholders_for_loading_none_and_error(self):
        app, tab = self.make_tab(None, "none")
        cases = {"none": "No database yet", "loading": "Reading your collection...",
                 "error": "Couldn't read the collection"}
        app.catalog_error = "database disk image is malformed"
        for state, expected in cases.items():
            tab.catalog_changed(None, state)
            tab.shown()                                   # nothing to fill: must not ask
            tab.placeholder.redraw(800, 300)
            shown = texts(tab.placeholder)
            self.assertIn(expected, shown, state)
            self.assertEqual(tab.content.grid_info(), {}, f"charts showing while {state}")
            self.assertTrue(tab.placeholder.grid_info())
        self.assertIn("database disk image is malformed", texts(tab.placeholder))
        self.assertEqual(app.runs, 0)

    def test_a_new_tab_starts_with_a_placeholder(self):
        _app, tab = self.make_tab(None, "none")
        tab.placeholder.redraw(800, 300)
        self.assertIn("No database yet", texts(tab.placeholder))
        self.assertEqual(tab.title, "Overview")

    def test_empty_collection(self):
        app, tab = self.ready_tab(make_catalog([]))
        tab.placeholder.redraw(800, 300)
        self.assertIn("There are no films in this collection", texts(tab.placeholder))
        self.assertEqual(tab.content.grid_info(), {})

    def test_a_failed_request_says_so(self):
        app, tab = self.make_tab(films())
        real = O.overview_answer
        try:
            O.overview_answer = lambda catalog: {"ok": False, "error": "something broke"}
            tab.catalog_changed(app.catalog, "ready")
            tab.shown()
            tab.placeholder.redraw(800, 300)
            self.assertIn("Couldn't put the overview together", texts(tab.placeholder))
            self.assertIn("something broke", texts(tab.placeholder))

            def crash(catalog):
                raise RuntimeError("worse")
            O.overview_answer = crash
            tab.shown()                                   # it tries again next time it's shown
            tab.placeholder.redraw(800, 300)
            self.assertIn("RuntimeError: worse", texts(tab.placeholder))
        finally:
            O.overview_answer = real
        tab.shown()
        self.assertTrue(tab.content.grid_info())
        self.assertIsNotNone(tab.data)

    def test_working_it_out_in_the_background(self):
        app, tab = self.make_tab(films())
        app.deferred = []
        tab.catalog_changed(app.catalog, "ready")
        tab.shown()
        tab.shown()                                       # already on its way: not started twice
        self.assertEqual(app.runs, 1)
        tab.placeholder.redraw(800, 300)
        self.assertIn("Putting the overview together...", texts(tab.placeholder))
        app.finish()
        self.assertTrue(tab.content.grid_info())
        self.assertEqual(tab.data["totals"]["films"], 13)

    def test_a_late_answer_for_an_old_collection_is_dropped(self):
        app, tab = self.make_tab(films())
        app.deferred = []
        tab.catalog_changed(app.catalog, "ready")
        tab.shown()
        newer = make_catalog([{"title": "Only One", "year": 2020, "genres": ["Horror"]}])
        app.catalog = newer
        tab.catalog_changed(newer, "loading")
        tab.catalog_changed(newer, "ready")
        pending = app.deferred
        app.deferred = None
        app._finish(*pending[0])                          # the old collection's answer arrives late
        self.assertIsNone(tab.data)
        tab.shown()
        self.assertEqual(tab.data["totals"]["films"], 1)

    def test_a_newer_overview_calls_the_older_one_off(self):
        app, tab = self.make_tab(films())
        app.current_tab = lambda: tab                     # on show: a new collection is worked out at once
        app.deferred = []
        tab.catalog_changed(app.catalog, "ready")
        first = app.deferred[0][0]
        newer = make_catalog([{"title": "Only One", "year": 2020, "genres": ["Horror"]}])
        app.catalog = newer
        tab.catalog_changed(newer, "ready")
        self.assertEqual(app.keys, ["overview.ask", "overview.ask"])
        self.assertTrue(first.cancelled)                  # it stops, and never answers
        self.assertFalse(app.deferred[1][0].cancelled)
        app.finish()
        self.assertEqual(tab.data["totals"]["films"], 1)
        self.assertTrue(tab.content.grid_info())


# ---------------------------------------------------------------------------------------------------------
class ContentTests(OverviewTestCase):
    def test_every_chart_draws(self):
        app, tab = self.ready_tab()
        self.assertEqual(app.runs, 1)
        self.assertEqual(app.statuses, [])                  # the status line keeps the app's own message
        self.assertTrue(tab.content.grid_info())
        self.assertEqual(tab.placeholder.grid_info(), {})
        for key in CHART_SIZES:
            view = self.draw(tab, key)
            self.assertTrue(view.find_withtag("chart"), f"{key} drew nothing")
            self.assertNotIn("This chart couldn't be drawn", texts(view), key)

    def test_headline_numbers(self):
        _app, tab = self.ready_tab()
        shown = texts(self.draw(tab, "kpi"))
        for text in ("Films", "13", "Hours of film", "Rated by you", "9", "Played by you", "7", "People",
                     "Credits markers", "in 2 libraries"):
            self.assertIn(text, shown)
        items = {it["label"]: it for it in O.kpi_items(tab.data)}
        self.assertIn("IMDb", items["Rated by you"]["note"])
        self.assertEqual(items["Played by you"]["note"], "54% of your films")
        self.assertEqual(items["Credits markers"]["value"], "0")

    def test_chart_contents(self):
        _app, tab = self.ready_tab()
        self.assertIn("1960s", texts(self.draw(tab, "decades")))
        self.assertIn("Movies-World", texts(self.draw(tab, "libraries")))
        self.assertIn("Action", texts(self.draw(tab, "genres")))
        countries = texts(self.draw(tab, "countries"))
        self.assertIn("United States", countries)           # the everyday name...
        self.assertIn("Taiwan", countries)
        self.assertNotIn("United States of America", countries)
        # ...while the tooltip keeps Plex's
        self.assertTrue(any(it["tip"].startswith("United States of America") for it in O.country_items(tab.data)))
        self.assertIn("John Woo", texts(self.draw(tab, "directors")))
        # resolutions stay in their natural order, not sorted by size
        self.assertEqual([it["label"] for it in O.resolution_items(tab.data)], ["4K", "1080p", "480p", "SD"])
        res = [t for t in texts(self.draw(tab, "resolutions")) if t in ("4K", "1080p", "480p", "SD")]
        self.assertEqual(res, ["4K", "1080p", "480p", "SD"])
        # the played chart is a share, with 'played N of M' tooltips, most-played library first
        played = O.played_items(tab.data)
        self.assertEqual(played[0]["label"], "Movies-World")
        self.assertIn("played 3 of 4", played[0]["tip"])
        self.assertTrue(any(t.endswith("%") for t in texts(self.draw(tab, "played"))))

    def test_ratings_scatter_and_notes(self):
        _app, tab = self.ready_tab()
        view = self.draw(tab, "ratings")
        dots = {t for i in view.find_withtag("chart") for t in view.gettags(i) if t.startswith("pt")}
        self.assertEqual(len(dots), 9)                     # the nine films you rated (all have IMDb ratings)
        points = O.scatter_points(tab.data)
        self.assertTrue(all(re.fullmatch(r".+ \(\d{4}\): you [\d.]+, IMDb [\d.]+\nClick to open its page", p["tip"])
                            for p in points), points[0]["tip"])
        note = tab.ratings_note.cget("text")
        self.assertIn("On the 9 films you and IMDb have both rated", note)
        self.assertIn("correlation", note)
        self.assertTrue(tab.ratings_note.grid_info())
        scores = tab.scores_note.cget("text")
        self.assertIn("Your most common score is", scores)
        self.assertIn("You only use 4 to 9", scores)
        self.assertIn("9", texts(self.draw(tab, "scores")))

    def test_played_bars_are_out_of_100_percent(self):
        # Movies-World is 75% played: its bar is three quarters of the way to a full one, not the whole width
        _app, tab = self.ready_tab()
        w, _h = CHART_SIZES["played"]
        view = self.draw(tab, "played")
        bar = [i for i in view.find_withtag("bar0")
               if view.type(i) in ("polygon", "rectangle") and "hit" not in view.gettags(i)]
        self.assertEqual(len(bar), 1)
        x0, _y0, x1, _y1 = view.bbox(bar[0])
        self.assertAlmostEqual(O.played_items(tab.data)[0]["value"], 0.75)
        full = (x1 - x0) / 0.75                                # where a 100% bar would end
        self.assertLess(x0 + full, w)
        # ...which the pale track behind it shows
        from projectionist.ui import theme
        track = [i for i in view.find_withtag("bar0") if view.type(i) in ("polygon", "rectangle")
                 and view.itemcget(i, "fill") == theme.NEUTRAL]
        self.assertEqual(len(track), 1)
        tx0, _ty0, tx1, _ty1 = view.bbox(track[0])
        self.assertAlmostEqual(tx1 - tx0, full, delta=3)

    def test_dots_stay_on_their_side_of_the_line(self):
        # "above the grey line" must mean you rate it higher than IMDb, however the dots are nudged apart:
        # whole-number scores against IMDb's tenths: many films a hair above or below IMDb, three exactly on it
        specs = []
        for i in range(31):
            imdb = round(5.5 + i * 0.1, 1)
            specs.append({"title": f"Film {i}", "year": 1990 + i, "imdb_rating": imdb,
                          "owner_rating": float(round(imdb + (0.3 if i % 2 else -0.3)))})
        _app, tab = self.ready_tab(make_catalog(specs))
        view = self.draw(tab, "ratings")
        diagonal = [view.coords(i) for i in view.find_withtag("chart") if view.type(i) == "line"
                    and len(view.coords(i)) == 4 and abs(view.coords(i)[0] - view.coords(i)[2]) > 1
                    and abs(view.coords(i)[1] - view.coords(i)[3]) > 1]
        self.assertEqual(len(diagonal), 1)
        ax, ay, bx, by = diagonal[0]
        length = ((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5
        points = O.scatter_points(tab.data)
        self.assertEqual(len(points), 31)
        on_line = 0
        for n, pt in enumerate(points):
            ovals = [i for i in view.find_withtag(f"pt{n}") if view.type(i) == "oval"]
            x0, y0, x1, y1 = view.coords(ovals[-1])            # the dot (drawn after its ring)
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            below = ((bx - ax) * (cy - ay) - (by - ay) * (cx - ax)) / length   # px; negative above the line
            gap = pt["y"] - pt["x"]
            if abs(gap) < 1e-9:
                on_line += 1
                self.assertLess(abs(below), 1, pt["tip"])
            elif gap > 0:
                self.assertLess(below, 0, pt["tip"])
            else:
                self.assertGreater(below, 0, pt["tip"])
        self.assertEqual(on_line, 3)

    def test_films_and_library_items(self):
        # the Export tab counts library items (every copy); here a film counts once
        catalog = films()
        catalog.films["f0"].plex_ids = [1, 100]              # two copies of one film
        _app, tab = self.ready_tab(catalog)
        self.assertEqual((tab.data["totals"]["films"], tab.data["totals"]["library_items"]), (13, 14))
        self.assertIn("13 films (14 library items)", O.kpi_items(tab.data)[0]["tip"])
        self.assertIn("Export tab", tab.cards["libraries"].hint_label.cget("text"))
        self.assertIn("however many copies", O.library_items(tab.data)[0]["tip"])
        # one copy of everything: nothing to explain
        self.assertNotIn("library items", O.kpi_items(handle({"action": "overview"}, catalog=films()))[0]["tip"])

    def test_no_ratings_yet(self):
        catalog = make_catalog([{"title": "A", "year": 1999, "genres": ["Drama"], "imdb_rating": 7.0},
                                {"title": "B", "year": 2001, "genres": ["Drama"]}])
        _app, tab = self.ready_tab(catalog)
        self.assertIn("No films rated by you and IMDb yet", texts(self.draw(tab, "ratings")))
        self.assertIn("You haven't rated any films yet", texts(self.draw(tab, "scores")))
        self.assertEqual(tab.ratings_note.grid_info(), {})
        self.assertEqual(tab.scores_note.grid_info(), {})
        self.assertIn("none yet", [it["note"] for it in O.kpi_items(tab.data)])
        self.assertIn("No countries to show", texts(self.draw(tab, "countries")))
        self.assertIn("No resolutions to show", texts(self.draw(tab, "resolutions")))

    def test_actor_toggle(self):
        _app, tab = self.ready_tab()
        self.assertEqual(tab.cast_var.get(), "lead")         # lead roles first: who the shelf is built around
        lead = texts(self.draw(tab, "actors"))
        self.assertIn("Jackie Chan", lead)
        tab.cast_buttons["all"].invoke()
        self.assertEqual(tab.cast_var.get(), "all")
        every = texts(self.draw(tab, "actors"))
        self.assertNotIn("Extra Person", lead)
        self.assertEqual(every[0], "Extra Person")           # billed fourth in all 12 films: tops 'all roles'
        tips = [it["tip"] for it in O.people_items(tab.data["top_actors"], "films in any role")]
        self.assertTrue(all("Click to see their profile" in t for t in tips))

    def test_catalog_changes(self):
        app, tab = self.ready_tab()
        first = tab.data
        tab.shown()                                          # same collection: not asked again
        self.assertEqual(app.runs, 1)
        tab.catalog_changed(None, "loading")
        self.assertIsNone(tab.data)
        self.assertEqual(tab.content.grid_info(), {})
        other = make_catalog([{"title": "Only One", "year": 2020, "genres": ["Horror"]}])
        app.catalog = other
        tab.catalog_changed(other, "ready")                  # not visible: waits until shown
        tab.shown()
        self.assertIsNot(tab.data, first)
        self.assertEqual(tab.data["totals"]["films"], 1)
        self.assertIn("Horror", texts(self.draw(tab, "genres")))


# ---------------------------------------------------------------------------------------------------------
class NavigationTests(OverviewTestCase):
    def test_clicks_open_the_other_tabs(self):
        app, tab = self.ready_tab()
        click(self.draw(tab, "decades"), "col0")
        self.assertEqual(app.gotos[-1], ("Watch Next", {"decade": 1960}))
        self.assertIsInstance(app.gotos[-1][1]["decade"], int)
        click(self.draw(tab, "libraries"), "bar1")
        self.assertEqual(app.gotos[-1][0], "Watch Next")
        self.assertIn(app.gotos[-1][1]["library"], ("Movies", "Movies-World"))
        click(self.draw(tab, "genres"), "bar0")
        self.assertEqual(app.gotos[-1], ("Watch Next", {"genre": O.genre_items(tab.data)[0]["label"]}))
        click(self.draw(tab, "actors"), "bar0")
        first = tab.data["top_lead_actors"][0]
        self.assertEqual(app.gotos[-1], ("Six Degrees", {"person_id": first["id"], "name": first["name"]}))
        # a director opens with the films they directed counted (some act in more films than they direct)
        click(self.draw(tab, "directors"), "bar0")
        first = tab.data["top_directors"][0]
        self.assertEqual(app.gotos[-1], ("Six Degrees", {"person_id": first["id"], "name": first["name"],
                                                         "directors": True}))
        # a rated film opens its page on the Film tab (films like it are a click away there)
        click(self.draw(tab, "ratings"), "pt0")
        self.assertEqual(app.gotos[-1][0], "Film")
        self.assertRegex(app.gotos[-1][1]["title"], r"^Film \d+ \(\d{4}\)$")
        film = app.catalog.films[app.gotos[-1][1]["film_key"]]             # ...and exactly which film
        self.assertEqual(film.label, app.gotos[-1][1]["title"])
        click(self.draw(tab, "played"), "bar0")
        self.assertEqual(app.gotos[-1], ("Watch Next", {"library": "Movies-World"}))
        # a country goes over by Plex's own name (the bar shows the everyday one)
        click(self.draw(tab, "countries"), "bar0")
        self.assertEqual(app.gotos[-1], ("Watch Next", {"country": "United States of America"}))
        self.assertIn("Click for films to watch next", O.country_items(tab.data)[0]["tip"])
        # resolutions have nowhere to go: tooltips only
        self.assertFalse(self.draw(tab, "resolutions").tag_bind("bar0", "<Button-1>"))
        self.assertTrue(tab.views["resolutions"].tag_bind("bar0", "<Enter>"))

    def test_a_missing_tab_is_reported(self):
        app, tab = self.ready_tab()
        app.goto = lambda title, **kw: None
        tab._open_genre("Action")
        self.assertEqual(app.statuses[-1], "The Watch Next tab isn't available.")

    def test_navigate(self):
        app, tab = self.make_tab(films())
        tab.catalog_changed(app.catalog, "ready")
        tab.navigate(anything="ignored")                    # unknown keys are fine; it fills if needed
        self.assertIsNotNone(tab.data)


# ---------------------------------------------------------------------------------------------------------
class LayoutTests(OverviewTestCase):
    def test_cards_reflow(self):
        _app, tab = self.ready_tab()
        s = tab.s
        tab._reflow(int(1250 * s))
        self.assertEqual(tab.columns, 2)
        cards = list(tab.cards.values())
        self.assertEqual(len(cards), 10)
        self.assertEqual([(int(c.grid_info()["row"]), int(c.grid_info()["column"])) for c in cards[:4]],
                         [(0, 0), (0, 1), (1, 0), (1, 1)])
        tab._reflow(int(850 * s))
        self.assertEqual(tab.columns, 1)
        self.assertEqual([int(c.grid_info()["row"]) for c in cards], list(range(10)))
        self.assertTrue(all(int(c.grid_info()["column"]) == 0 for c in cards))
        tab._reflow(10)                                     # not laid out yet: keep the current layout
        self.assertEqual(tab.columns, 1)

    def test_hints_wrap_to_the_card(self):
        _app, tab = self.ready_tab()
        card = tab.cards["ratings"]
        tab._wrap_labels(card, 460)
        self.assertEqual(int(str(card.hint_label.cget("wraplength"))), 430)
        self.assertEqual(int(str(tab.ratings_note.cget("wraplength"))), 430)

    def test_headline_tiles_make_room_for_a_second_row(self):
        _app, tab = self.ready_tab()
        s = tab.s
        event = type("Event", (), {})
        event.width = int(1000 * s)
        tab._fit_kpi(event)
        one_row = int(tab.views["kpi"].cget("height"))
        event.width = int(400 * s)
        tab._fit_kpi(event)
        self.assertAlmostEqual(int(tab.views["kpi"].cget("height")), 2 * one_row, delta=1)

    def test_headline_tiles_scroll_with_the_charts(self):
        # pinned above the scrolling page they'd leave a short window too little room for the tallest cards
        _app, tab = self.ready_tab()
        page = str(tab.scroll.inner)
        self.assertTrue(str(tab.kpi_card).startswith(page + "."))
        self.assertTrue(all(str(card).startswith(page + ".") for card in tab.cards.values()))
        self.assertLess(int(tab.kpi_card.grid_info()["row"]), int(tab.card_grid.grid_info()["row"]))

    def test_bar_charts_are_sized_to_their_rows(self):
        _app, tab = self.ready_tab()
        self.assertEqual(int(tab.views["libraries"].cget("height")), int(O.bars_height(2) * tab.s))
        self.assertEqual(int(tab.views["genres"].cget("height")), int(O.bars_height(3) * tab.s))


# ---------------------------------------------------------------------------------------------------------
class WordingTests(unittest.TestCase):
    def test_numbers_in_words(self):
        self.assertEqual(O.films_text(1), "1 film")
        self.assertEqual(O.films_text(1204), "1,204 films")
        self.assertEqual(O.share_text(4, 1204), "under 1%")
        self.assertEqual(O.share_text(0, 10), "0%")
        self.assertEqual(O.share_text(241, 1204), "20%")
        self.assertEqual(O.share_text(1, 0), "0%")

    def test_agreement_in_words(self):
        self.assertIn("fairly closely", O.agreement_words(0.66))
        self.assertIn("very closely", O.agreement_words(0.9))
        self.assertIn("little to do", O.agreement_words(0.05))
        self.assertIn("like what IMDb doesn't", O.agreement_words(-0.5))
        self.assertIsNone(O.agreement_words(None))
        self.assertIn("more generous", O.averages_words(7.0, 6.7, 515))
        self.assertIn("tougher", O.averages_words(6.0, 6.7, 515))
        self.assertIn("just the same", O.averages_words(6.71, 6.7, 515))
        self.assertIsNone(O.averages_words(None, 6.7, 3))

    def test_scores_note(self):
        hist = lambda counts: {"your_ratings": [{"rating": r, "films": counts.get(r, 0)} for r in range(1, 11)]}
        self.assertIn("never given less than 4", O.scores_note(hist({4: 1, 7: 5, 10: 2})))
        self.assertIn("never given more than 8", O.scores_note(hist({1: 1, 8: 3})))
        self.assertIn("whole scale", O.scores_note(hist({1: 1, 10: 3})))
        self.assertEqual(O.scores_note(hist({})), "")
        self.assertIn("most common score is 7 (5 films, 62%", O.scores_note(hist({4: 1, 7: 5, 10: 2})))

    def test_source_text(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "com.plexapp.plugins.library.db-2026-09-25")
            open(db, "wb").close()
            catalog = type("C", (), {"owner": "Ann", "source": db})()
            self.assertEqual(O.source_text(catalog), "Ratings and plays: Ann   ·   Plex backup from 2026-09-25")
        self.assertEqual(O.source_text(type("C", (), {"owner": "", "source": "memory"})()), "")


# ---------------------------------------------------------------------------------------------------------
class InTheAppTests(unittest.TestCase):
    def test_tab_in_the_main_window(self):
        try:
            root = hidden_root()
        except Exception as exc:
            self.skipTest(f"Tk unavailable: {exc}")
        from projectionist import gui
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "com.plexapp.plugins.library.db-2026-09-25")
            build_fixture(db)
            old = gui.SETTINGS_FILE, gui.SETTINGS_DIR
            gui.SETTINGS_FILE, gui.SETTINGS_DIR = os.path.join(d, "settings.json"), d
            app = None
            try:
                app = gui.App(root, db)
                tab = next(t for t in app.tabs if t.title == "Overview")
                self.assertIsInstance(tab, O.Tab)
                app._pick_initial_db(db)
                deadline = time.time() + 30
                while app.catalog_state != "ready":
                    root.update()
                    time.sleep(0.02)
                    self.assertLess(time.time(), deadline, app.catalog_state)
                status = app.app_status_var.get()
                self.assertIs(app.goto("Overview"), tab)       # worked out on a background thread...
                deadline = time.time() + 10
                while tab.data is None:
                    root.update()
                    time.sleep(0.02)
                    self.assertLess(time.time(), deadline)
                self.assertEqual(tab.data["totals"]["films"], 5)
                self.assertEqual(app.app_status_var.get(), status)   # ...without touching the status line
                self.assertTrue(tab.content.grid_info())
                self.assertIn("Owner", tab.source_label.cget("text"))
            finally:
                gui.SETTINGS_FILE, gui.SETTINGS_DIR = old
                if app is not None:
                    app.shutdown()
                else:
                    root.destroy()


if __name__ == "__main__":
    unittest.main()
