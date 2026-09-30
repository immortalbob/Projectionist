"""Tests for the Viewing tab (projectionist/ui/habits.py). Fully headless: every Tk root is withdrawn and never shown;
charts are drawn with ChartView.redraw(width, height), clicks are fired through the canvas bindings the charts set
up, and pop-up menus are stubbed out."""

import os
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from projectionist import habits  # noqa: E402
from projectionist.catalog import load  # noqa: E402
from projectionist.ui import habits as V  # noqa: E402
from projectionist.ui import paint  # noqa: E402
from test_habits import HK_KEY, MATRIX, STORY, STORY_RATINGS, make_db  # noqa: E402
from test_projectionist import make_catalog  # noqa: E402
from test_ui_overview import FakeApp as _FakeApp  # noqa: E402
from test_ui_overview import click, hidden_root, texts  # noqa: E402

_saved = {}


def setUpModule():
    import tkinter as tk
    # a click on a pile of marks, or a menu, would pop up a real window even from a withdrawn root
    _saved["choose"] = paint.Interaction._choose
    _saved["popup"] = tk.Menu.tk_popup
    paint.Interaction._choose = lambda self, near: None
    tk.Menu.tk_popup = lambda *a, **k: None


def tearDownModule():
    import gc
    import tkinter as tk
    paint.Interaction._choose = _saved["choose"]
    tk.Menu.tk_popup = _saved["popup"]
    gc.collect()                  # (the windows made here: let go of on the main thread - see HabitsTestCase)


class FakeApp(_FakeApp):
    """The overview tests' fake app, with the real App.goto's positional-only tab name (the Film page takes a
    title= argument)."""

    def goto(self, tab_title, /, **kwargs):
        self.gotos.append((tab_title, kwargs))
        return object()


def tips(view) -> dict:
    ui = paint.Interaction.of(view, create=False)
    return {tag: text for tag, (text, _h) in (ui.tips.items() if ui else [])}


class HabitsTestCase(unittest.TestCase):
    """The story database from test_habits (and variants), loaded once per class."""
    db_kwargs = {"ratings": STORY_RATINGS, "extra_cast": True, "stats": [(1, 1, 1740787200, 1, 7200)]}
    views = STORY

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.catalog = load(make_db(cls.tmp.name, cls.views, **cls.db_kwargs))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        try:
            self.root = hidden_root()
        except Exception as exc:
            self.skipTest(f"Tk unavailable: {exc}")
        from tkinter import ttk
        from projectionist.ui import theme
        theme.apply_styles(self.root)
        self.notebook = ttk.Notebook(self.root)
        self.catalog.cache.clear()

    def tearDown(self):
        self.root.destroy()
        # A window and its widgets refer to each other (and a dark look's tab images hang on the window): collect
        # them here, on the main thread. Left for later, a collection on some background thread - the next test's
        # jobs - would delete Tcl objects from the wrong thread (a fatal Tcl error).
        import gc
        gc.collect()

    def make_tab(self, catalog=None, state="ready"):
        app = FakeApp(catalog, state)
        return app, V.Tab(app, self.notebook)

    def ready_tab(self, catalog=None):
        catalog = catalog or self.catalog
        app, tab = self.make_tab(catalog)
        tab.catalog_changed(catalog, "ready")
        tab.shown()
        return app, tab

    def draw(self, tab, key, w=1200, h=None):
        view = tab.views[key]
        view.redraw(w, h or int(view.cget("height")))
        return view


# ---------------------------------------------------------------------------------------------------------
class StateTests(HabitsTestCase):
    def test_placeholders(self):
        app, tab = self.make_tab(None, "none")
        self.assertEqual(tab.title, "Viewing")
        app.catalog_error = "database disk image is malformed"
        for state, expected in {"none": "No database yet", "loading": "Reading your collection...",
                                "error": "Couldn't read the collection"}.items():
            tab.catalog_changed(None, state)
            tab.shown()
            tab.placeholder.redraw(800, 300)
            self.assertIn(expected, texts(tab.placeholder), state)
            self.assertEqual(tab.nb.grid_info(), {})
        self.assertEqual(app.runs, 0)

    def test_filled_in_the_background(self):
        app, tab = self.make_tab(self.catalog)
        app.deferred = []
        tab.catalog_changed(self.catalog, "ready")
        tab.shown()
        tab.shown()                                         # already on its way: not asked twice
        self.assertEqual(app.runs, 1)
        self.assertEqual(app.keys, ["habits.answer"])
        self.assertIn("Reading your play history...", app.statuses)
        tab.placeholder.redraw(800, 300)
        self.assertIn("Reading your play history...", texts(tab.placeholder))
        app.finish()
        self.assertTrue(tab.nb.grid_info())
        self.assertEqual(tab.data["history"]["plays"], 10)
        self.assertIn("Plays by Owner", tab.hint_label.cget("text"))

    def test_a_late_answer_for_an_old_collection_is_dropped(self):
        app, tab = self.make_tab(self.catalog)
        app.deferred = []
        tab.catalog_changed(self.catalog, "ready")
        tab.shown()
        other = load(self.catalog.source)
        app.catalog = other
        tab.catalog_changed(other, "loading")
        tab.catalog_changed(other, "ready")
        pending, app.deferred = app.deferred, None
        app._finish(*pending[0])                            # the old collection's answer arrives late
        self.assertIsNone(tab.data)
        tab.shown()
        self.assertIsNotNone(tab.data)

    def test_background_jobs_hold_the_tab_weakly(self):
        # A job that ends after the window has gone must never be what lets go of the tab: its Tk variables may
        # only be let go of on the window's thread ("Tcl_AsyncDelete: async handler deleted by the wrong thread").
        app, tab = self.make_tab(self.catalog)
        app.deferred = []
        tab.catalog_changed(self.catalog, "ready")
        tab.shown()
        tab.navigate(year=2025)

        def reaches_tab(obj, seen):
            if id(obj) in seen:
                return False
            seen.add(id(obj))
            if obj is tab:
                return True
            if hasattr(obj, "__self__"):                      # a bound method
                return reaches_tab(obj.__self__, seen)
            for cell in getattr(obj, "__closure__", None) or ():
                try:
                    value = cell.cell_contents
                except ValueError:
                    continue
                if callable(value) or value is tab:
                    if reaches_tab(value, seen):
                        return True
            return False
        for _job, work, done, failed in app.deferred:
            for fn in (work, done, failed):
                self.assertFalse(reaches_tab(fn, set()), fn)
        app.finish()
        self.assertIsNotNone(tab.data)

    def test_an_unreadable_database(self):
        _app, tab = self.ready_tab(make_catalog([{"title": "A", "year": 2000}]))     # source 'memory'
        tab.placeholder.redraw(800, 300)
        shown = texts(tab.placeholder)
        self.assertIn("Couldn't read your play history", shown)
        self.assertTrue(any("Couldn't read Plex's play history" in t for t in shown), shown)

    def test_a_crash_is_reported_and_retried(self):
        app, tab = self.make_tab(self.catalog)
        real = habits.answer
        try:
            habits.answer = lambda catalog, request: 1 / 0
            tab.catalog_changed(self.catalog, "ready")
            tab.shown()
            tab.placeholder.redraw(800, 300)
            self.assertIn("ZeroDivisionError: division by zero", texts(tab.placeholder))
        finally:
            habits.answer = real
        tab.shown()
        self.assertTrue(tab.nb.grid_info())


class EmptyTests(HabitsTestCase):
    views = []
    db_kwargs = {"keep_gone": False}

    def test_no_plays(self):
        _app, tab = self.ready_tab()
        tab.placeholder.redraw(900, 300)
        self.assertIn("No plays on record yet", texts(tab.placeholder))
        self.assertEqual(tab.nb.grid_info(), {})


class NoClockTests(HabitsTestCase):
    """No statistics_media and no resume points: hours by running times, nothing stopped partway."""
    db_kwargs = {"sql": [("UPDATE metadata_item_settings SET view_offset = NULL WHERE account_id = 1", ())]}

    def test_running_times_and_nothing_stopped(self):
        _app, tab = self.ready_tab()
        tiles = {t["label"]: t for t in V.history_tiles(tab.data)}
        self.assertTrue(tiles["Hours"]["value"].startswith("~"))
        self.assertEqual(tiles["Hours"]["note"], "by running times")
        self.assertEqual(tab.month_toggle.grid_info(), {})                  # no playback clock to switch to
        self.assertEqual(tab.stopped_table.grid_info(), {})
        self.assertIn("Nothing left half-watched", texts(self.draw(tab, "stopped", 560, 110)))
        tab.navigate(year=2025)
        self.assertIn("about", tab.year_head.cget("text"))                  # 'about N hours'
        self.assertIn("running times", tab.year_foot.cget("text"))


# ---------------------------------------------------------------------------------------------------------
class ChartTests(HabitsTestCase):
    def test_every_chart_draws_wide_and_narrow(self):
        _app, tab = self.ready_tab()
        tab.navigate(year=2025)
        for key in tab.views:
            for w in (1200, 460):
                view = self.draw(tab, key, w)
                self.assertTrue(view.find_withtag("chart") or key == "stopped", f"{key} drew nothing at {w}")
                self.assertNotIn("This chart couldn't be drawn", texts(view), key)
        heat = texts(self.draw(tab, "heatmap", 1200))
        for label in ("midnight", "3 am", "noon", "3 pm", "9 pm", "Mon", "Sun"):
            self.assertIn(label, heat)
        narrow = texts(self.draw(tab, "heatmap", 460))
        self.assertIn("noon", narrow)
        self.assertNotIn("3 am", narrow)                     # every 6 hours when the squares are small
        self.assertEqual(tab.cards["months"].title_label.cget("text"), "Plays per month")
        tab.month_buttons["hours"].invoke()
        self.assertEqual(tab.month_mode.get(), "hours")
        self.assertEqual(tab.cards["months"].title_label.cget("text"), "Hours per month")    # says what it shows
        self.assertIn("Mar '25", texts(self.draw(tab, "months")))
        tab.month_buttons["films"].invoke()
        self.assertEqual(tab.cards["months"].title_label.cget("text"), "Plays per month")

    def test_charts_draw_to_png(self):
        try:
            import PIL  # noqa: F401
        except ImportError:
            self.skipTest("Pillow isn't installed")
        from projectionist.ui.paint import render_png
        a = habits.answer(self.catalog, {"year": 2025})
        y = a["year"]
        with tempfile.TemporaryDirectory() as d:
            for name, draw in (
                    ("heatmap", lambda p: V.heatmap(p, a["heatmap"]["cells"], a["heatmap"]["films"])),
                    ("calendar", lambda p: V.calendar(p, 2025, y["days"], y["from"], y["to"], y["longest_streak"])),
                    ("calendar_2023", lambda p: V.calendar(p, 2023, {}, None, None)),
                    ("heatmap_empty", lambda p: V.heatmap(p, [[0] * 24 for _ in range(7)]))):
                path = render_png(draw, os.path.join(d, f"{name}.png"), 900, 240, 1.25)
                self.assertTrue(os.path.getsize(path) > 500, name)

    def test_heatmap_tips_and_cells(self):
        app, tab = self.ready_tab()
        view = self.draw(tab, "heatmap")
        hm = tab.data["heatmap"]
        d, hr = next((d, hr) for d in range(7) for hr in range(24) if hm["cells"][d][hr])
        tip = tips(view)[f"hm{d}_{hr}"]
        self.assertTrue(tip.startswith(f"{V.DAY_NAMES[d]}s, {V.hour_span(hr)}"), tip)
        self.assertIn("started", tip)
        self.assertIn("Click to list them", tip)
        empty = next((d, hr) for d in range(7) for hr in range(24) if not hm["cells"][d][hr])
        self.assertIn("No films started", tips(view)[f"hm{empty[0]}_{empty[1]}"])
        self.assertFalse(view.tag_bind(f"hm{empty[0]}_{empty[1]}", "<Button-1>"))   # nothing to list
        self.assertRegex(tips(view)["hmday0"], r"^Mondays\n\d+ films? started \(\d+%\)$")
        click(view, f"hm{d}_{hr}")
        self.assertEqual(tab.cell, (d, hr))
        self.assertTrue(tab.cell_box.grid_info())
        rows = list(tab.cell_table.rows.values())
        self.assertEqual(len(rows), hm["cells"][d][hr])
        self.assertIn(f"on {V.DAY_NAMES[d]}s", tab.cell_label.cget("text"))
        tab.cell_table.tree.selection_set("0")
        tab.cell_table._opened()                            # Enter
        film = self.catalog.films[app.gotos[-1][1]["film_key"]]
        self.assertEqual(app.gotos[-1], ("Film", {"film_key": film.key, "title": film.label}))
        tab._clear_cell()
        self.assertEqual(tab.cell_box.grid_info(), {})


class ListTests(HabitsTestCase):
    def test_lists(self):
        app, tab = self.ready_tab()
        most = list(tab.most_table.rows.values())
        self.assertEqual([r["key"] for r in most][:2], [HK_KEY, MATRIX])
        self.assertEqual(most[1]["dates"], "Mar 3 '25, Apr 12 '25, Jan 6 '26")
        stopped = list(tab.stopped_table.rows.values())
        self.assertEqual(len(stopped), 1)
        self.assertEqual((stopped[0]["at"], stopped[0]["share"], stopped[0]["when"], stopped[0]["before"]),
                         ("1:02:03 of 2:16:15", "46%", "unknown", "played 3 times before"))   # 45.5%
        self.assertTrue(tab.stopped_table.grid_info())
        fav = list(tab.fav_table.rows.values())
        self.assertEqual(fav[0]["film"], "=Formula Looking Title (1912)")
        self.assertEqual(fav[0]["last"], "marked in bulk Apr 10, 2025")
        self.assertIn("since Sep 25, 2025", tab.fav_note.cget("text"))
        self.assertIn("a 'marked' date is when a film was ticked as played, not when you watched it",
                      tab.fav_note.cget("text"))
        self.assertIn("ticking off several cuts adds to its count; here that counts once", tab.most_note.cget("text"))
        for table in (tab.most_table, tab.stopped_table, tab.fav_table):
            table.tree.selection_set("0")
            table._opened()
            self.assertEqual(app.gotos[-1][0], "Film")
            self.assertIn(app.gotos[-1][1]["film_key"], self.catalog.films)
        # sorting a date column sorts by date, not by the words shown
        tab.most_table.sort("dates")
        order = [tab.most_table.rows[i]["key"] for i in tab.most_table.tree.get_children()]
        self.assertEqual(order[0], "plex://movie/sf75")     # last dated Jan 5 2026, the earliest of the four

    def test_forgotten_choices_ask_again(self):
        app, tab = self.ready_tab()
        tab.fav_rating.set("10 only")
        tab.fav_age.set("6 months")
        tab.fav_rating_box.event_generate("<<ComboboxSelected>>")
        self.assertEqual(app.keys[-1], "habits.forgotten")
        self.assertEqual(tab.data["forgotten_rule"], {"min_rating": 10.0, "days": 182, "since": "2026-03-27"})
        self.assertEqual({r["key"] for r in tab.fav_table.rows.values()}, {MATRIX, "imdb:tt0062622"})
        self.assertIn("rated 10 with no play", tab.fav_note.cget("text"))

    def test_forgotten_says_when_a_film_was_marked(self):
        _app, tab = self.ready_tab()
        film = {"key": MATRIX, "title": "The Matrix", "year": 1999, "in_library": True, "rating": 10.0,
                "last_played": None, "bulk_marked": None}
        tab.data["forgotten"] = [dict(film, plex_marked="2025-03-09T08:15", plex_count=1),    # counted, never logged
                                 dict(film, plex_marked=None, plex_count=1),                 # ...with no tick date
                                 dict(film, plex_marked=None, plex_count=0)]
        tab.data["forgotten"][1]["key"] = tab.data["forgotten"][2]["key"] = HK_KEY
        tab._fill_forgotten()
        self.assertEqual([r["last"] for r in tab.fav_table.rows.values()],
                         ["marked as played Mar 9, 2025", "played, no date", "no play on record"])
        self.assertIn("a 'marked' date is when a film was ticked as played", tab.fav_note.cget("text"))
        tab.data["forgotten"] = tab.data["forgotten"][1:]
        tab._fill_forgotten()
        self.assertNotIn("'marked' date", tab.fav_note.cget("text"))

    def test_marks_in_the_streaks_card(self):
        _app, tab = self.ready_tab()
        st = tab.data["streaks"]
        st["longest"]["marked_only"] = 1
        st["busiest_day"]["films"][0]["how"] = "marked"
        tab._draw_spells()
        words = [w.cget("text") for w in _descendants(tab.spells) if w.winfo_class() == "TLabel"]
        self.assertIn("On one of those days, only films marked as played.", words)
        at = V.nice_time(st["busiest_day"]["films"][0]["at"])
        self.assertIn(f"{at}, marked as played", words)                # the time it was ticked, not a viewing
        self.assertTrue(any(w.endswith(": 1 play") for w in words), words)          # busiest day: plays
        self.assertIn("Mar 3-9, 2025: 3 plays", words)
        st["busiest_day"]["films"][0]["how"] = "played"
        tab._draw_spells()
        words = [w.cget("text") for w in _descendants(tab.spells) if w.winfo_class() == "TLabel"]
        self.assertIn(at, words)

    def test_the_streaks_card_wraps_rather_than_cut_off(self):
        """A line of the card wider than the card (a long title, a wider font, a larger text size) goes on over a
        second line - the time under the film, 'list them' under the dates - rather than running off its edge."""
        from projectionist.ui.film import Flow
        _app, tab = self.ready_tab()
        st = tab.data["streaks"]
        st["busiest_day"]["films"][0]["how"] = "marked"
        tab._draw_spells()
        flows = [w for w in _descendants(tab.spells) if isinstance(w, Flow)]
        self.assertTrue(flows)
        for flow in flows:
            self.assertEqual(flow.grid_info()["sticky"], "ew")
            parts = flow.items
            flow._width = sum(p.winfo_reqwidth() for p in parts) + 1000         # room: one line
            flow.arrange()
            self.assertEqual({int(p.place_info()["y"]) for p in parts}, {0})
            if len(parts) > 1:
                flow._width = parts[0].winfo_reqwidth() + 5                        # no room: two lines
                flow.arrange()
                self.assertGreater(int(parts[-1].place_info()["y"]), 0)
                self.assertEqual(int(parts[-1].place_info()["x"]), 0)

    def test_streaks_card(self):
        app, tab = self.ready_tab()
        words = [w.cget("text") for w in _descendants(tab.spells) if w.winfo_class() == "TLabel"]
        self.assertIn("Longest streak", words)
        self.assertTrue(any(w.startswith("3 days in a row") for w in words), words)
        self.assertFalse(any("marked as played" in w for w in words), words)      # (no marks in that streak)
        link = next(w for w in _descendants(tab.spells) if w.winfo_class() == "TLabel" and w.cget("text") ==
                    "list them")
        link.event_generate("<Button-1>")
        self.assertEqual(tab._current_view(), "Year in review")
        self.assertEqual(tab.year, 2025)
        self.assertEqual(tab.year_filter[:2], ("2025-03-03", "2025-03-05"))
        self.assertEqual(len(tab.plays_table.rows), 3)
        self.assertIn("Only your longest streak: 3 plays", tab.list_label.cget("text"))


def _descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from _descendants(child)


# ---------------------------------------------------------------------------------------------------------
class YearTests(HabitsTestCase):
    def test_navigate_to_a_year(self):
        app, tab = self.ready_tab()
        tab.navigate(year=2025)
        self.assertEqual(tab._current_view(), "Year in review")
        self.assertEqual((tab.year, tab.year_var.get()), (2025, "2025"))
        self.assertEqual(app.keys[-1], "habits.year")        # 2025 wasn't in the first answer: asked for
        self.assertTrue(tab.year_head.cget("text").startswith("2025: 3 films"))
        tab.navigate(year="2026")                            # (as text too) - already known: not asked again
        self.assertEqual(tab.year, 2026)
        self.assertEqual(app.keys.count("habits.year"), 1)
        self.assertTrue(tab.year_head.cget("text").startswith("2026 so far: 4 films"))
        tab.navigate(year=2019)
        self.assertEqual(tab.year, 2023)                     # the nearest year with plays
        self.assertTrue(any("No plays on record in 2019" in x for x in app.statuses), app.statuses)
        tab.navigate()
        self.assertEqual(tab._current_view(), "Your habits")

    def test_navigate_before_the_answer(self):
        app, tab = self.make_tab(self.catalog)
        app.deferred = []
        tab.catalog_changed(self.catalog, "ready")
        tab.navigate(year=2025)
        self.assertIsNone(tab.data)
        app.finish()
        app.finish()                                         # (the year it then asked for)
        self.assertEqual((tab._current_view(), tab.year), ("Year in review", 2025))
        self.assertIn(2025, tab.years)

    def test_year_picker_and_arrows(self):
        app, tab = self.ready_tab()
        tab.navigate(year=2026)
        self.assertEqual(tab.year_box.cget("values"), ("2026", "2025", "2023") if isinstance(
            tab.year_box.cget("values"), tuple) else "2026 2025 2023")
        self.assertIn("disabled", tab.next_btn.state())
        self.assertEqual(tab.prev_btn.cget("text"), "←  2025")               # says where it goes
        tab.prev_btn.invoke()
        self.assertEqual(tab.year, 2025)
        tab.prev_btn.invoke()
        self.assertEqual(tab.year, 2023)
        self.assertIn("disabled", tab.prev_btn.state())
        tab.year_var.set("2026")
        tab.year_box.event_generate("<<ComboboxSelected>>")
        self.assertEqual(tab.year, 2026)
        self.assertIn("habits.year", app.keys)

    def test_year_contents(self):
        app, tab = self.ready_tab()
        tab.navigate(year=2025)
        self.assertEqual(tab.year_sub.cget("text"), "Jan 1 - Dec 31, 2025")
        shown = texts(self.draw(tab, "year_tiles"))
        for text in ("Films", "3", "5 plays", "New to you", "2 rewatches", "Busiest month", "March", "9.3"):
            self.assertIn(text, shown)
        self.assertIn("nothing earlier to compare", tab.compare_label.cget("text"))
        links = [w.cget("text") for w in _descendants(tab.first_last) if w.winfo_class() == "TLabel"]
        self.assertIn("The Matrix (1999)", links)
        self.assertIn("First film of 2025:", links)
        plays = list(tab.plays_table.rows.values())
        self.assertEqual(len(plays), 5)
        self.assertEqual(plays[0]["date"], "Mar 3, 9:00 pm")
        self.assertEqual([r["how"] for r in plays].count("Marked as played"), 1)
        self.assertIn("1 play was marked as played", tab.year_foot.cget("text"))
        self.assertIn("2 plays marked in bulk are left out", tab.year_foot.cget("text"))
        tab.navigate(year=2026)
        self.assertIn("Against 2025 (Jan 1 - Sep 25): 4 plays vs 5 (-20%)", tab.compare_label.cget("text"))
        cal = self.draw(tab, "calendar")
        self.assertIn("Outside Plex's history", tips(cal)["day2026-12-25"])
        self.assertIn("No films", tips(cal)["day2026-03-01"])

    def test_months_and_days_filter_the_list(self):
        app, tab = self.ready_tab()
        click(self.draw(tab, "months"), "col16")             # the whole history's months: Mar 2025 is the 17th
        self.assertEqual((tab.year, tab.year_filter[2]), (2025, "March 2025"))
        self.assertEqual(len(tab.plays_table.rows), 3)
        months = self.draw(tab, "year_months")
        self.assertIn("Click to list them", tips(months)["col3"])
        self.assertFalse(months.tag_bind("col4", "<Button-1>"))          # May 2025: nothing to list
        click(months, "col3")                                # April 2025
        self.assertEqual(tab.year_filter[2], "April")
        self.assertEqual(len(tab.plays_table.rows), 1)
        click(self.draw(tab, "year_months"), "col3")         # again: all of them
        self.assertIsNone(tab.year_filter)
        self.assertEqual(len(tab.plays_table.rows), 5)
        click(self.draw(tab, "calendar"), "day2025-03-04")
        self.assertEqual([r["key"] for r in tab.plays_table.rows.values()], [HK_KEY])
        self.assertTrue(tab.show_all_link.winfo_manager())
        tab._clear_filter()
        self.assertEqual(len(tab.plays_table.rows), 5)
        self.assertFalse(tab.show_all_link.winfo_manager())

    def test_clicks_on_the_old_year_while_the_next_is_worked_out(self):
        # The year before's review isn't worked out yet: the year on screen stays (and can be clicked) until it
        # arrives. A click on it then does nothing - no error box, and no filter of the old year's days.
        errors = []
        self.root.report_callback_exception = lambda *exc: errors.append(exc[1])
        app, tab = self.ready_tab()
        tab.navigate(year=2026)
        cal, months = self.draw(tab, "calendar"), self.draw(tab, "year_months")
        tab.years.pop(2025, None)
        app.deferred = []
        tab.prev_btn.invoke()                                # <- 2025: asked for
        self.assertEqual((tab.year, tab._year_drawn), (2025, (2026, None)))
        click(cal, "day2026-01-06")
        click(months, "col0")                                # 2026's January
        self.assertEqual(errors, [])
        self.assertIsNone(tab.year_filter)
        app.finish()                                         # 2025 arrives: all of it
        self.assertEqual(tab._year_drawn, (2025, None))
        self.assertEqual(len(tab.plays_table.rows), 5)
        self.assertEqual(tab.list_label.cget("text")[:7], "5 plays")
        # a filter of another year's days never outlives a change of year
        tab.year_filter = ("2026-02-01", "2026-02-28", "February")
        tab._show_year(2025)
        self.assertIsNone(tab.year_filter)
        tab.year_filter = ("2025-12-27", "2026-01-01", "your longest streak (its 2025 part)")     # over New Year
        tab._show_year(2025)
        self.assertEqual(tab.year_filter[0], "2025-12-27")

    def test_top_lists_go_to_the_other_tabs(self):
        app, tab = self.ready_tab()
        tab.navigate(year=2025)
        click(self.draw(tab, "top_actors", 400), "bar0")
        self.assertEqual(app.gotos[-1], ("Six Degrees", {"person_id": "p6", "name": "Keanu Reeves"}))
        click(self.draw(tab, "top_directors", 400), "bar0")
        self.assertEqual(app.gotos[-1], ("Six Degrees", {"person_id": "p1", "name": "Lana Wachowski",
                                                         "directors": True}))
        click(self.draw(tab, "top_genres", 400), "bar0")
        self.assertEqual(app.gotos[-1], ("Watch Next", {"genre": "Science Fiction"}))
        click(self.draw(tab, "top_decades", 400), "bar0")
        self.assertEqual(app.gotos[-1], ("Watch Next", {"decade": 1960}))
        countries = self.draw(tab, "top_countries", 400)
        self.assertIn("United States", texts(countries))      # the everyday name on the bar...
        click(countries, "bar0")
        self.assertEqual(app.gotos[-1], ("Watch Next", {"country": "United States of America"}))   # ...Plex's to go

    def test_films_deleted_since_and_missing_tabs(self):
        app, tab = self.ready_tab()
        tab.navigate(year=2023)
        row = next(iter(tab.plays_table.rows.values()))
        self.assertEqual(row["film"], "Deleted Movie - no longer in your library")
        tab.plays_table.tree.selection_set("0")
        n = len(app.gotos)
        tab.plays_table._opened()
        self.assertEqual(len(app.gotos), n)                  # nothing to open
        self.assertIn("no longer in your library", app.statuses[-1])
        self.assertIn("no longer in your library, so there are no genres", tab.tops_note.cget("text"))
        self.assertEqual(tab.tops_grid.grid_info(), {})
        self.assertTrue(tab.year_head.cget("text").startswith("2023: 1 film"))
        self.assertNotIn("hours", tab.year_head.cget("text"))            # a deleted film has no running time
        self.assertIn("Nothing to show", texts(self.draw(tab, "top_genres", 400)))
        app.goto = lambda tab_title, /, **kw: None
        tab._open_row({"key": MATRIX, "title": "The Matrix", "year": 1999})
        self.assertEqual(app.statuses[-1], "The Film tab isn't available.")


class SettingsApp(FakeApp):
    """The fake app with the main window's settings: prefs.set saves them and tells the tabs (as App does)."""

    def __init__(self, catalog=None, state="ready", settings=None):
        super().__init__(catalog, state)
        self.settings = dict(settings or {})
        self.tabs = []

    def save_settings(self):
        pass

    def preference_changed(self, key, value):
        for tab in self.tabs:
            tab.preference_changed(key, value)


def _words(tab):
    return [w.cget("text") for w in _descendants(tab.spells) if w.winfo_class() == "TLabel"]


class SettingsTests(HabitsTestCase):
    """Settings > Viewing (forgotten favourites) and Settings > Dates and times, live on the tab."""

    def setUp(self):
        super().setUp()
        from projectionist import formats
        self.addCleanup(formats.reset)

    def settings_tab(self, **settings):
        app = SettingsApp(self.catalog, settings=settings)
        tab = V.Tab(app, self.notebook)
        app.tabs.append(tab)
        tab.catalog_changed(self.catalog, "ready")
        tab.shown()
        return app, tab

    def test_forgotten_favourites_follow_the_settings(self):
        from projectionist import prefs
        app, tab = self.settings_tab(viewing_forgotten_rating=10, viewing_forgotten_days=182)
        self.assertEqual((tab.fav_rating.get(), tab.fav_age.get()), ("10 only", "6 months"))
        self.assertEqual(tab.data["forgotten_rule"], {"min_rating": 10.0, "days": 182, "since": "2026-03-27"})
        # the card's drop-down changes the setting (asked for once)
        asked = app.keys.count("habits.forgotten")
        tab.fav_rating.set("8 or more")
        tab.fav_rating_box.event_generate("<<ComboboxSelected>>")
        self.assertEqual(app.settings["viewing_forgotten_rating"], 8)
        self.assertEqual(app.keys.count("habits.forgotten"), asked + 1)
        self.assertEqual(tab.data["forgotten_rule"]["min_rating"], 8.0)
        # ...and a change on the Settings tab shows on the card, asked for again
        prefs.set(app, "viewing_forgotten_days", 730)
        self.assertEqual(tab.fav_age.get(), "2 years")
        self.assertEqual(app.keys.count("habits.forgotten"), asked + 2)
        self.assertEqual(tab.data["forgotten_rule"]["days"], 730)
        prefs.reset(app, section="Viewing")
        self.assertEqual((tab.fav_rating.get(), tab.fav_age.get()), ("9 or more", "a year"))
        # no settings to keep them in (the Viewing tab on its own): the drop-downs still ask
        _app, tab = self.ready_tab()
        self.assertEqual((tab.fav_rating.get(), tab.fav_age.get()), ("9 or more", "a year"))

    def test_dates_and_times_follow_the_settings(self):
        from projectionist import prefs
        app, tab = self.settings_tab()
        tab.navigate(year=2025)
        tab._day_clicked("2025-03-03")
        self.assertIn("Only Mar 3, 2025: 1 play", tab.list_label.cget("text"))
        self.assertIn("since Sep 25, 2025", tab.fav_note.cget("text"))
        self.assertIn("Mar 3-9, 2025: 3 plays", _words(tab))
        runs = app.runs
        prefs.set(app, "date_style", "dmy")                  # written again at once, with nothing asked again
        self.assertEqual(app.runs, runs)
        self.assertIn("Only 3 Mar 2025: 1 play", tab.list_label.cget("text"))
        self.assertIn("since 25 Sep 2025", tab.fav_note.cget("text"))
        self.assertIn("3-9 Mar 2025: 3 plays", _words(tab))
        most = list(tab.most_table.rows.values())
        self.assertEqual(most[1]["dates"], "3 Mar '25, 12 Apr '25, 6 Jan '26")
        self.assertRegex(next(iter(tab.plays_table.rows.values()))["date"], r"^\d+ [A-Z][a-z]{2}, \d+:\d\d [ap]m$")
        self.assertIn("Plex history", tab.hint_label.cget("text"))
        self.assertNotIn(", 20", tab.hint_label.cget("text"))
        prefs.set(app, "clock", "24h")
        self.assertRegex(next(iter(tab.plays_table.rows.values()))["date"], r"^\d+ [A-Z][a-z]{2}, \d\d:\d\d$")
        heat = texts(self.draw(tab, "heatmap", 1200))
        self.assertIn("12:00", heat)
        self.assertNotIn("noon", heat)
        cal = tips(self.draw(tab, "calendar", 1200))
        self.assertTrue(cal["day2025-03-03"].startswith("Monday, 3 Mar 2025"), cal["day2025-03-03"])
        prefs.set(app, "date_style", "iso")
        self.assertIn("2025-03-03 to 2025-03-09: 3 plays", _words(tab))

    def test_weeks_from_sunday(self):
        from projectionist import prefs
        app, tab = self.settings_tab()
        heat = texts(self.draw(tab, "heatmap", 1200))
        self.assertLess(heat.index("Mon"), heat.index("Sun"))
        prefs.set(app, "week_start", "sunday")
        self.assertEqual(app.keys[-1], "habits.streaks")     # the busiest week, asked for again
        self.assertIn("Mar 2-8, 2025: 3 plays", _words(tab))
        view = self.draw(tab, "heatmap", 1200)
        heat = texts(view)
        self.assertLess(heat.index("Sun"), heat.index("Mon"))
        # the squares are still the days they say (Monday-first in the answer): a click lists that day's films
        hm = tab.data["heatmap"]
        d, hr = next((d, hr) for d in range(7) for hr in range(24) if hm["cells"][d][hr])
        self.assertTrue(tips(view)[f"hm{d}_{hr}"].startswith(f"{V.DAY_NAMES[d]}s, "))
        click(view, f"hm{d}_{hr}")
        self.assertEqual(tab.cell, (d, hr))
        # the calendar's weeks run Sunday to Saturday: Sun Jan 5 and Mon Jan 6 share a column, Sunday on top
        tab.navigate(year=2025)
        cal = self.draw(tab, "calendar", 1200)
        sun, mon = cal.coords(cal.find_withtag("day2025-01-05")[0]), cal.coords(cal.find_withtag("day2025-01-06")[0])
        self.assertAlmostEqual(sun[0], mon[0])
        self.assertLess(sun[1], mon[1])
        self.assertEqual([t for t in texts(cal) if t in V.DAYS][:4], ["Sun", "Tue", "Thu", "Sat"])
        prefs.set(app, "week_start", "monday")
        self.assertIn("Mar 3-9, 2025: 3 plays", _words(tab))
        cal = self.draw(tab, "calendar", 1200)
        sun, mon = cal.coords(cal.find_withtag("day2025-01-05")[0]), cal.coords(cal.find_withtag("day2025-01-06")[0])
        self.assertLess(sun[0], mon[0])                     # (Sunday ends a week, Monday starts the next)

    def test_a_change_of_week_while_the_answer_is_on_its_way(self):
        from projectionist import prefs
        app = SettingsApp(self.catalog)
        app.deferred = []
        tab = V.Tab(app, self.notebook)
        app.tabs.append(tab)
        tab.catalog_changed(self.catalog, "ready")
        tab.shown()
        prefs.set(app, "week_start", "sunday")               # (the answer on its way has Monday weeks: asked again)
        app.finish()
        self.assertEqual(tab.data["streaks"]["busiest_week"]["from"], "2025-03-02")


class LayoutTests(HabitsTestCase):
    def test_cards_reflow(self):
        _app, tab = self.ready_tab()
        s = tab.s
        tab._reflow(int(1250 * s))
        self.assertEqual(tab.columns, 2)
        grid = [(int(c.grid_info()["row"]), int(c.grid_info()["column"]), int(c.grid_info()["columnspan"]))
                for c in tab.grid_cards]
        self.assertEqual(grid, [(0, 0, 1), (0, 1, 1), (1, 0, 2), (2, 0, 2)])
        tab._reflow(int(850 * s))
        self.assertEqual(tab.columns, 1)
        self.assertEqual([int(c.grid_info()["row"]) for c in tab.grid_cards], [0, 1, 2, 3])
        # a narrow stopped-partway list leaves out 'When', then 'How far'; short lists get short tables
        table = tab.stopped_table
        table.fit_columns(int(600 * s))
        self.assertEqual(table.shown_columns, ["film", "at", "share", "when", "before"])
        table.fit_columns(int(500 * s))
        self.assertEqual(table.shown_columns, ["film", "at", "share", "before"])
        table.fit_columns(int(300 * s))
        self.assertEqual(list(table.tree.tk.splitlist(table.tree.cget("displaycolumns"))), ["film", "at", "before"])
        self.assertEqual(int(tab.most_table.tree.cget("height")), 4)          # four films played twice or more
        self.assertEqual(int(tab.stopped_table.tree.cget("height")), 3)       # (never fewer than 3 rows)
        tab._reflow_tops(int(1300 * s))
        self.assertEqual(tab.top_columns, 3)
        tab._reflow_tops(int(500 * s))
        self.assertEqual(tab.top_columns, 1)

    def test_heatmap_and_calendar_fit_their_width(self):
        self.assertGreater(V.heatmap_height(1200, 1.0), V.heatmap_height(460, 1.0))
        self.assertLessEqual(V.heatmap_height(1900, 1.0), 7 * 26 + 60)
        self.assertGreater(V.calendar_height(1200, 1.0, 2025), V.calendar_height(460, 1.0, 2025))


# ---------------------------------------------------------------------------------------------------------
class WordTests(unittest.TestCase):
    def test_dates_and_hours(self):
        self.assertEqual(V.nice_date("2025-03-08"), "Mar 8, 2025")
        self.assertEqual(V.nice_date("2025-03-08T20:41", year=False), "Mar 8")
        self.assertEqual(V.nice_date(None), "")
        self.assertEqual(V.nice_time("2026-01-01T17:32"), "5:32 pm")
        self.assertEqual(V.nice_time("2026-01-01T00:05"), "12:05 am")
        self.assertEqual(V.nice_time("2026-01-01T12:00"), "12:00 pm")
        self.assertEqual(V.hour_label(0), "midnight")
        self.assertEqual(V.hour_label(12), "noon")
        self.assertEqual(V.hour_label(17), "5 pm")
        self.assertEqual(V.hour_label(1), "1 am")
        self.assertEqual(V.hour_label(24), "midnight")
        self.assertEqual(V.hour_span(18), "6-7 pm")
        self.assertEqual(V.hour_span(11, 2), "11 am - 1 pm")
        self.assertEqual(V.hour_span(22, 2), "10 pm - midnight")
        self.assertEqual(V.hour_span(23), "11 pm - midnight")
        self.assertEqual(V.hour_span(0), "midnight - 1 am")
        self.assertEqual(V.date_range("2025-12-27", "2026-01-01"), "Dec 27, 2025 - Jan 1, 2026")
        self.assertEqual(V.date_range("2025-11-10", "2025-11-16"), "Nov 10-16, 2025")
        self.assertEqual(V.date_range("2026-03-26", "2026-04-05"), "Mar 26 - Apr 5, 2026")
        self.assertEqual(V.date_range("2026-03-26", "2026-04-05", year=False), "Mar 26 - Apr 5")
        self.assertEqual(V.date_range("2026-03-16", "2026-03-16"), "Mar 16, 2026")
        self.assertEqual(V.short_date("2025-07-17"), "Jul 17 '25")
        self.assertEqual(V.plural(1, "film"), "1 film")
        self.assertEqual(V.plural(1234, "rewatch", "rewatches"), "1,234 rewatches")
        self.assertEqual(V.rating_text(10.0), "10")
        self.assertEqual(V.rating_text(7.5), "7.5")

    def test_heatmap_words(self):
        by_hour = [0] * 24
        by_hour[17], by_hour[18], by_hour[20], by_hour[9] = 70, 60, 20, 10
        cells = None
        hm = {"plays": 160, "by_hour": by_hour, "by_day": [10, 20, 20, 20, 20, 30, 40], "cells": cells}
        words = V.heatmap_words(hm)
        self.assertIn("early evening: 5-7 pm (81% of them)", words)
        self.assertIn("Sunday is your biggest day (40 films), then Saturday (30).", words)
        self.assertIn("No film started between 9 pm and 9 am.", words)      # the longest quiet stretch
        hm["by_day"] = [10, 20, 20, 20, 20, 40, 40]
        self.assertIn("Saturday and Sunday are your biggest days (40 films each)", V.heatmap_words(hm))
        self.assertIn("no start times", V.heatmap_words({"plays": 0}))
        self.assertIn("too few", V.heatmap_words({"plays": 3, "by_hour": by_hour, "by_day": [0] * 7}))

    def test_coverage_text(self):
        h = {"first": "2025-03-08T20:41", "as_of": "2026-09-25", "server_since": "2025-03-08", "days": 567,
             "undated_films": 64, "undated_plays": 71, "undated_first_week": 52, "marked": 23, "bulk_marked": 11,
             "bulk_bursts": 4, "double_logs": 17, "gone_plays": 3, "skipped": 0, "utc_offset": "+0100"}
        period, notes = V.coverage_text(h, "Alex")
        self.assertEqual(period, "Plex's dated history of Alex's plays runs from Mar 8, 2025 (when this "
                                 "server was set up) to the backup on Sep 25, 2026: 567 days.")
        for bit in ("Plex also counts 71 plays of 64 films that it never logged, so they have no date: 52 of "
                    "those films were marked as played in the history's first week, as this server was set up, so "
                    "most were likely seen before the history starts.", "23 plays were marked as played",
                    "11 plays marked in bulk (4 bursts", "17 repeat logs", "or another edition of it marked as "
                    "played later", "3 plays of films deleted since", "times are this PC's (UTC+01:00)"):
            self.assertIn(bit, notes)
        self.assertNotIn("libraries that aren't loaded", notes)
        # films ticked off later than the history's first week: it doesn't guess when they were seen
        later = V.coverage_text(dict(h, undated_first_week=20), "A")[1]
        self.assertIn("no date: they were marked as played, or synced from elsewhere.", later)
        elsewhere = V.coverage_text(dict(h, server_since="2024-01-01"), "A")
        self.assertNotIn("set up", elsewhere[0])
        self.assertIn("marked as played in the history's first week, so most", elsewhere[1])
        self.assertIn("no plays logged", V.coverage_text({}, "A")[0])

    def test_utc_month_note(self):
        self.assertEqual(V.utc_month_note("-0500"), "Plex adds its playback clock up by UTC month, so hours played "
                                                    "after about 7 pm on a month's last day count in the next month.")
        self.assertIn("after about 8:30 pm on a month's last day", V.utc_month_note("-0330"))
        self.assertIn("before about 10 am on the 1st count in the month before", V.utc_month_note("+1000"))
        for offset in ("+0000", "", None, "junk"):
            self.assertEqual(V.utc_month_note(offset), "", offset)
        y = {"year": 2025, "hours": {"measured": 176, "runtime": 203}}
        hours = {t["label"]: t for t in V.year_tiles(y, "-0500")}["Hours"]
        self.assertIn("after about 7 pm on a month's last day", hours["tip"])
        self.assertEqual(len(hours["tip"].split("\n")), 3)           # (a tile's tip has room for 3 lines)
        self.assertNotIn("UTC", {t["label"]: t for t in V.year_tiles(y)}["Hours"]["tip"])

    def test_streak_words(self):
        st = {"days": 12, "from": "2025-12-27", "to": "2026-01-07", "marked_only": 2}
        self.assertEqual(V.streak_tip(st), "12 days in a row with a film\nDec 27, 2025 - Jan 7, 2026\nOn 2 of those "
                                           "days, only films marked as played")
        self.assertEqual(V.streak_marks(dict(st, marked_only=1)), "On one of those days, only films marked as played")
        self.assertEqual(V.streak_tip(dict(st, marked_only=0)), "12 days in a row with a film\n"
                                                                "Dec 27, 2025 - Jan 7, 2026")
        self.assertEqual((V.streak_tip(None), V.streak_marks(None)), ("", ""))

    def test_year_headline_and_comparison(self):
        h = {"first": "2025-03-08T20:41", "as_of": "2026-09-25"}
        y26 = {"year": 2026, "from": "2026-01-01", "to": "2026-09-25", "plays": 104, "films": 97,
               "hours": {"measured": 163, "runtime": 181}}
        self.assertEqual(V.year_headline(y26, h), ("2026 so far: 97 films, 163 hours",
                                                    "Jan 1 - Sep 25, 2026 - the date of the backup"))
        y25 = {"year": 2025, "from": "2025-03-08", "to": "2025-12-31", "plays": 131, "films": 122,
               "hours": {"measured": None, "runtime": 208}}
        self.assertEqual(V.year_headline(y25, h), ("2025: 122 films, about 208 hours",
                                                    "Mar 8 - Dec 31, 2025 - Plex's history starts then"))
        self.assertEqual(V.year_headline({"year": 2019, "plays": 0}, h)[0], "2019: no films on record")
        vs = {"year": 2025, "from": "03-08", "to": "09-25", "plays": [88, 84], "films": [85, 80],
              "rating": [6.71, 7.26], "rated": [52, 61]}
        # the ratings are today's, of two different sets of films: it compares the films, not the rater
        self.assertEqual(V.compare_words(vs, 2026), "Against 2025 (Mar 8 - Sep 25): 88 plays vs 84 (+5%), "
                                                    "85 films vs 80; by your ratings now, the films average 6.7 "
                                                    "(52 of 85 rated) vs 7.3 (61 of 80).")
        for words in ("tougher", "generous"):
            self.assertNotIn(words, V.compare_words(dict(vs, rating=[7.5, 7.0]), 2026))
        self.assertTrue(V.compare_words({k: v for k, v in vs.items() if k != "rated"}, 2026).endswith(
            "the films average 6.7 vs 7.3."))                         # (an answer from before 'rated')
        self.assertIn("the whole year", V.compare_words(dict(vs, **{"from": "01-01", "to": "12-31"}), 2026))
        self.assertIn("nothing earlier to compare 2025", V.compare_words(None, 2025, h))
        self.assertIn("No stretch of 2025", V.compare_words(None, 2026, h, has_previous=True))
        self.assertEqual(V.change_text(88, 84), "+5%")
        self.assertEqual(V.change_text(5, 5), "the same")
        self.assertEqual(V.change_text(5, 0), "")

    def test_month_items(self):
        months = [{"month": "2025-03", "label": "Mar 2025", "plays": 9, "marked": 0, "hours": 9.6,
                   "partial": True},
                  {"month": "2025-04", "label": "Apr 2025", "plays": 0, "marked": 0, "hours": None,
                   "partial": False}]
        items = V.month_items(months)
        self.assertEqual([i["label"] for i in items], ["Mar '25", "Apr '25"])
        # (the colour is kept as something the chart works out when it draws, so it follows the look)
        from projectionist.ui import theme as T
        self.assertEqual(T.value(items[0]["color"]), V.PALE)
        self.assertEqual(T.value(items[1]["color"]), T.BLUE)
        self.assertIn("Only part of this month", items[0]["tip"])
        self.assertTrue(items[0]["tip"].startswith("Mar 2025\n9 plays\n"), items[0]["tip"])
        self.assertFalse(items[1]["clickable"])
        # a film watched twice in a month: plays, of fewer films
        tip = V.month_items([dict(months[0], plays=39, films=38, marked=5)])[0]["tip"]
        self.assertTrue(tip.startswith("Mar 2025\n39 plays of 38 films (5 marked as played)\n"), tip)
        hours = V.month_items(months, "hours")
        self.assertEqual(hours[0]["value"], 9.6)
        self.assertIn("No playback clock", hours[1]["tip"])


# ---------------------------------------------------------------------------------------------------------
class InTheAppTests(unittest.TestCase):
    def test_tab_in_the_main_window(self):
        try:
            root = hidden_root()
        except Exception as exc:
            self.skipTest(f"Tk unavailable: {exc}")
        from projectionist import gui
        with tempfile.TemporaryDirectory() as d:
            db = make_db(d, STORY, ratings=STORY_RATINGS)
            old = gui.SETTINGS_FILE, gui.SETTINGS_DIR
            gui.SETTINGS_FILE, gui.SETTINGS_DIR = os.path.join(d, "settings.json"), d
            app = None

            def wait_for(condition, seconds=15):
                deadline = time.time() + seconds
                while not condition():
                    root.update()
                    time.sleep(0.02)
                    self.assertLess(time.time(), deadline)
            try:
                app = gui.App(root, db)
                tab = next(t for t in app.tabs if t.title == "Viewing")
                self.assertIsInstance(tab, V.Tab)
                app._pick_initial_db(db)
                wait_for(lambda: app.catalog_state == "ready", 30)
                self.assertIs(app.goto("Viewing", year=2025), tab)      # read on a background thread...
                wait_for(lambda: tab.years.get(2025) is not None and tab.year == 2025)
                self.assertEqual(tab._current_view(), "Year in review")  # ...then that year's review
                self.assertEqual(tab.data["history"]["plays"], 10)
                app.goto("Viewing")
                root.update()
                self.assertEqual(tab._current_view(), "Your habits")
            finally:
                gui.SETTINGS_FILE, gui.SETTINGS_DIR = old
                if app is not None:
                    app.shutdown()
                else:
                    root.destroy()


if __name__ == "__main__":
    unittest.main()
