"""Tests for the Library Doctor tab (projectionist/ui/doctor.py). Fully headless: every Tk root is withdrawn and
never shown, charts are drawn with ChartView.redraw(width, height), clicks are fired through the bindings the
charts set up, the save dialog is replaced, and no saved file is ever opened."""

import gc
import os
import re
import sys
import tempfile
import time
import unittest
import zipfile
from xml.etree import ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from projectionist import doctor as DR  # noqa: E402
from projectionist import jobs  # noqa: E402
from projectionist.ask import handle  # noqa: E402
from projectionist.ui import doctor as D  # noqa: E402
from test_doctor import doctor_fixture, key_of  # noqa: E402
from test_projectionist import make_catalog  # noqa: E402

try:
    import PIL  # noqa: F401
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False


def hidden_root():
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()            # headless: never show a window
    return root


class FakeApp:
    """The services a tab uses (see ui/base.py), with the settings the main window keeps."""

    def __init__(self, catalog=None, state="ready", settings=True):
        self.catalog, self.catalog_state, self.catalog_error = catalog, state, ""
        self.gotos, self.statuses, self.runs, self.keys, self.remembered = [], [], 0, [], []
        self.deferred = None             # set to a list to hold background work back until finish()
        self._keyed = {}
        if settings:
            self.settings = {}

            def remember(**values):
                self.remembered.append(values)
                self.settings.update(values)
            self._remember = remember

    def ask(self, request):
        return handle(request, catalog=self.catalog)

    def run(self, work, done=None, failed=None, status=None, key=None):      # synchronous in tests
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


def texts(view):
    return [view.itemcget(i, "text") for i in view.find_withtag("chart") if view.type(i) == "text"]


def click(view, tag):
    """Fire a chart item's click binding (hidden windows get no real pointer events)."""
    script = view.tag_bind(tag, "<Button-1>")
    if not script:
        raise AssertionError(f"{tag} isn't clickable")
    view.tk.eval(re.sub(r"%[#\w]", "0", script))


class DoctorTestCase(unittest.TestCase):
    catalog = None

    @classmethod
    def setUpClass(cls):
        from projectionist.catalog import load
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = os.path.join(cls.tmp.name, "com.plexapp.plugins.library.db-2026-09-25")
        doctor_fixture(cls.db)
        cls.catalog = load(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        try:
            self.root = hidden_root()
        except Exception as exc:
            self.skipTest(f"Tk unavailable: {exc}")
        from tkinter import messagebox, ttk
        import tkinter as tk
        from projectionist.ui import paint, theme
        theme.apply_styles(self.root)
        self.notebook = ttk.Notebook(self.root)
        # nothing may pop up: no message boxes, no menus
        self._saved = {}
        for name in ("showerror", "showinfo", "showwarning", "askyesno", "askokcancel"):
            self._saved[(messagebox, name)] = getattr(messagebox, name)
            setattr(messagebox, name, self._popup)
        self._saved[(paint.Interaction, "_choose")] = paint.Interaction._choose
        paint.Interaction._choose = lambda ui, near: self.fail("a menu would pop up")
        self._saved[(tk.Menu, "tk_popup")] = tk.Menu.tk_popup
        tk.Menu.tk_popup = lambda *a, **k: self.fail("a menu would pop up")
        if hasattr(os, "startfile"):
            self._saved[(os, "startfile")] = os.startfile
            os.startfile = lambda *a, **k: self.fail("a file would be opened")

    def _popup(self, *args, **kwargs):
        self.fail(f"a message box would pop up: {args}")

    def tearDown(self):
        for (owner, name), value in self._saved.items():
            setattr(owner, name, value)
        self.root.destroy()
        # The tab and its Tk variables form reference cycles: collect them here, on the main thread. Left for later,
        # a collection on some background thread would delete Tcl objects from the wrong thread (a fatal Tcl error).
        gc.collect()

    def make_tab(self, catalog=None, state="ready", settings=True):
        app = FakeApp(catalog, state, settings)
        tab = D.Tab(app, self.notebook)
        return app, tab

    def ready_tab(self, catalog=None, settings=True):
        catalog = catalog or self.catalog
        app, tab = self.make_tab(catalog, settings=settings)
        tab.catalog_changed(catalog, "ready")
        tab.shown()
        return app, tab

    def tiles(self, tab, width=1200):
        view = tab.views["tiles"]
        view.redraw(width, D.tiles_height(width, 12, tab.s))
        return view

    def shown_rows(self, panel):
        return panel.rows_in_order()


# ---------------------------------------------------------------------------------------------------------
class StateTests(DoctorTestCase):
    def test_placeholders(self):
        app, tab = self.make_tab(None, "none")
        self.assertEqual(tab.title, "Library Doctor")
        app.catalog_error = "database disk image is malformed"
        for state, expected in {"none": "No database yet", "loading": "Reading your collection...",
                                "error": "Couldn't read the collection"}.items():
            tab.catalog_changed(None, state)
            tab.shown()
            tab.placeholder.redraw(800, 300)
            self.assertIn(expected, texts(tab.placeholder), state)
            self.assertEqual(tab.content.grid_info(), {})
        self.assertEqual(app.runs, 0)

    def test_a_collection_without_a_database(self):
        memory = make_catalog([{"title": "A", "year": 2000}])
        app, tab = self.ready_tab(memory)
        tab.placeholder.redraw(800, 300)
        shown = texts(tab.placeholder)
        self.assertIn("Couldn't check your files", shown)
        self.assertEqual(tab.content.grid_info(), {})

    def test_asked_once_in_the_background(self):
        app, tab = self.make_tab(self.catalog)
        app.deferred = []
        tab.catalog_changed(self.catalog, "ready")
        tab.shown()
        tab.shown()                                 # already on its way: not asked twice
        self.assertEqual(app.keys, ["doctor.answer"])
        self.assertIn("Checking your files...", app.statuses)
        tab.placeholder.redraw(800, 300)
        self.assertIn("Checking your files...", texts(tab.placeholder))
        app.finish()
        self.assertTrue(tab.content.grid_info())
        tab.shown()                                 # the same collection: not asked again
        self.assertEqual(app.runs, 1)

    def test_a_late_answer_is_dropped(self):
        app, tab = self.make_tab(self.catalog)
        app.deferred = []
        tab.catalog_changed(self.catalog, "ready")
        tab.shown()
        pending = app.deferred
        app.deferred = None
        tab.catalog_changed(None, "loading")          # another database is being loaded
        app._finish(*pending[0])                       # the old collection's answer arrives late
        self.assertIsNone(tab.data)
        tab.placeholder.redraw(800, 300)
        self.assertIn("Reading your collection...", texts(tab.placeholder))


class TileTests(DoctorTestCase):
    def test_twelve_tiles(self):
        _app, tab = self.ready_tab()
        view = self.tiles(tab)
        tags = {t for i in view.find_withtag("chart") for t in view.gettags(i) if re.fullmatch(r"tile\d+", t)}
        self.assertEqual(len(tags), 12)
        shown = texts(view)
        for text in ("Likely duplicates", "Weaker copies", "No audio in your languages", "Disk use", "to fix",
                     "to check", "for info"):
            self.assertIn(text, shown)
        self.assertFalse([t for t in shown if t.endswith("…")], "cut short at 1200 px")
        self.assertEqual(D.tiles_per_row(1200, 12, 1.0), 6)
        self.assertEqual(D.tiles_per_row(800, 12, 1.0), 4)
        self.assertEqual(D.tiles_per_row(1900, 12, 1.0), 6)
        self.assertEqual(D.tiles_per_row(300, 12, 1.0), 1)
        self.assertTrue(view.tag_bind("tile0", "<Enter>"))           # every tile has its tooltip

    def test_nothing_to_do_is_all_clear(self):
        from projectionist.ui import theme
        _app, tab = self.ready_tab()
        answer = dict(tab.data)
        answer["summary"] = [dict(s, count=0, value="0", note="nothing to do") if s["id"] == "weaker" else s
                             for s in tab.data["summary"]]
        items = D.tile_items(answer)
        self.assertTrue(items[1]["zero"])
        self.assertFalse(items[5]["zero"])                            # (info tiles are never 'all clear')
        view = tab.views["tiles"]
        view.show(lambda p: D.draw_tiles(p, items))
        view.redraw(1200, D.tiles_height(1200, 12, tab.s))
        self.assertIn("all clear", texts(view))
        zero = [i for i in view.find_withtag("tile1") if view.type(i) == "text" and view.itemcget(i, "text") == "0"]
        self.assertEqual(view.itemcget(zero[0], "fill"), theme.GOOD_TEXT)

    def test_a_tile_opens_its_list(self):
        _app, tab = self.ready_tab()
        view = self.tiles(tab)
        click(view, "tile1")                                          # Weaker copies
        self.assertEqual(tab.view, "weaker")
        self.assertTrue(tab.issue_card.grid_info())
        self.assertEqual(tab.summary.grid_info(), {})
        item = tab.data["issues"]["weaker"]
        self.assertTrue(tab.issue_title.cget("text").startswith("Weaker copies"))
        self.assertEqual(tab.explanation.cget("text"), item["explanation"])
        self.assertEqual(len(tab.issue_list.table.tree.get_children()), item["count"])
        self.assertIn("Temple Kicks (1978)", [r["film"] for r in self.shown_rows(tab.issue_list)])
        # the open list's tile is shaded
        from projectionist.ui import theme
        view = self.tiles(tab)
        fills = [view.itemcget(i, "fill") for i in view.find_withtag("tile1") if view.type(i) == "polygon"]
        self.assertIn(theme.SELECT, fills)
        # ...in a ring that clears the card, and its small print darker than MUTED (which fades into the shade)
        outlines = [view.itemcget(i, "outline") for i in view.find_withtag("tile1") if view.type(i) == "polygon"]
        self.assertIn(theme.RAMP[350], outlines)
        small = {view.itemcget(i, "fill") for i in view.find_withtag("tile1") if view.type(i) == "text"
                 and view.itemcget(i, "text") in ("to fix", "to check", "for info")}
        self.assertEqual(small, {theme.INK_2})
        others = {view.itemcget(i, "fill") for i in view.find_withtag("tile2") if view.type(i) == "text"
                  and view.itemcget(i, "text") in ("to fix", "to check", "for info")}
        self.assertEqual(others, {theme.MUTED})
        click(view, "tile11")                                         # Disk use: the summary
        self.assertEqual(tab.view, "disk")
        self.assertTrue(tab.summary.grid_info())

    def test_an_empty_list_says_so(self):
        _app, tab = self.ready_tab()
        click(self.tiles(tab), "tile4")                               # Unavailable files: one here
        self.assertTrue(tab.issue_list.table_slot.grid_info())
        empty = DR.answer(self.catalog, {"languages": ["en", "ja"]})
        self.assertEqual(empty["issues"]["subtitles"]["count"], 0)
        tab.languages = ["en", "ja"]
        tab._fill(quiet=True)
        tab.open_issue("subtitles")
        self.assertEqual(tab.issue_list.table_slot.grid_info(), {})
        self.assertIn("Nothing to do", tab.issue_list.empty.cget("text"))


class ListTests(DoctorTestCase):
    def test_sort_select_and_open(self):
        app, tab = self.ready_tab()
        tab.navigate(issue="duplicates")
        panel = tab.issue_list
        panel.table.sort("extra_gb")
        extras = [r["extra_gb"] for r in self.shown_rows(panel)]
        self.assertEqual(extras, sorted(extras))
        panel.table.sort("extra_gb")                                  # again: largest first
        self.assertEqual([r["extra_gb"] for r in self.shown_rows(panel)], sorted(extras, reverse=True))
        first = panel.table.tree.get_children()[0]
        panel.table.tree.selection_set(first)
        self.root.update()
        row = panel.selected()
        self.assertEqual(panel.why.cget("text"), f"Why: {row['why']}")
        self.assertIn("File", panel.path.cget("text"))
        self.assertTrue(panel.open_link.grid_info())
        panel.table._opened()                                         # Enter
        self.assertEqual(app.gotos[-1], ("Film", {"film_key": row["film_key"], "title": row["film"]}))
        panel.open_film()                                             # the link
        self.assertEqual(len(app.gotos), 2)
        app.goto = lambda tab_title, /, **kw: None
        panel.open_film()
        self.assertEqual(app.statuses[-1], "The Film tab isn't available.")

    def test_navigate(self):
        app, tab = self.ready_tab()
        tab.navigate(issue="weaker")
        self.assertEqual(tab.view, "weaker")
        tab.navigate(issue="size")
        self.assertEqual(tab.view, "large")
        tab.navigate(issue="bogus")
        self.assertEqual(tab.view, "disk")
        self.assertEqual(app.statuses[-1], "Library Doctor has no check called 'bogus'.")
        key = key_of(self.catalog, "Jp Forced")
        tab.navigate(issue="audio", film_key=key)
        self.assertEqual(tab.issue_list.selected()["film_key"], key)
        tab.navigate(issue="weaker", film_key=key)                   # not on that list
        self.assertIn("isn't on the list of weaker copies", app.statuses[-1])
        tab.navigate(film_key=key)                                    # the summary, with the film's file selected
        self.assertEqual(tab.view, "disk")
        self.assertEqual(tab.files.selected()["film_key"], key)

    def test_navigate_to_one_copy(self):
        """The Film page's issues say which copy (plex_id: an edition; media_id: a version): its row is picked,
        not just the film's first."""
        app, tab = self.ready_tab()
        copies = DR.copies_data(self.catalog)["copies"]
        dm = key_of(self.catalog, "Temple Kicks")
        eureka = next(c for c in copies if c["title"] == "Temple Kicks" and c["edition"] == "Eureka")
        tab.navigate(film_key=dm)                                     # the summary: the biggest file first
        self.assertEqual(tab.files.selected()["edition"], "Twilight Time")
        tab.navigate(film_key=dm, plex_id=eureka["plex_id"])
        self.assertEqual(tab.files.selected()["plex_id"], eureka["plex_id"])
        self.assertIn("Eureka", tab.files.path.cget("text"))
        tab.navigate(film_key=dm, plex_id=str(eureka["plex_id"]))    # (as text too)
        self.assertEqual(tab.files.selected()["plex_id"], eureka["plex_id"])
        # two versions of one library item: only media_id tells them apart
        tv = key_of(self.catalog, "Two Versions")
        dvd = next(c for c in copies if c["title"] == "Two Versions" and c["resolution"] == "480p")
        tab.navigate(film_key=tv, plex_id=dvd["plex_id"], media_id=dvd["media_id"])
        self.assertEqual(tab.files.selected()["media_id"], dvd["media_id"])
        # an id that isn't on the list: the film's first row, as before
        tab.navigate(issue="weaker", film_key=dm, plex_id=999999, media_id=999999)
        self.assertEqual(tab.issue_list.selected()["edition"], "Eureka")
        # the Film page's issue carries the ids to pass
        issue = next(i for i in DR.film_issues(self.catalog, dm) if i["id"] == "weaker")
        self.assertEqual((issue["plex_id"], issue["media_id"]), (eureka["plex_id"], eureka["media_id"]))

    def test_navigate_to_one_copy_before_the_answer(self):
        app, tab = self.make_tab(self.catalog)
        app.deferred = []
        tab.catalog_changed(self.catalog, "ready")
        copies = DR.copies_data(self.catalog)["copies"]
        eureka = next(c for c in copies if c["title"] == "Temple Kicks" and c["edition"] == "Eureka")
        tab.navigate(film_key=key_of(self.catalog, "Temple Kicks"), plex_id=eureka["plex_id"],
                     media_id=eureka["media_id"])
        app.finish()
        self.assertEqual(tab.view, "disk")
        self.assertEqual(tab.files.selected()["media_id"], eureka["media_id"])

    def test_navigate_before_the_answer(self):
        app, tab = self.make_tab(self.catalog)
        app.deferred = []
        tab.catalog_changed(self.catalog, "ready")
        key = key_of(self.catalog, "Rated Eight")
        tab.navigate(issue="upgrade", film_key=key)
        self.assertEqual(tab.view, "disk")                            # nothing to show yet
        app.finish()
        self.assertEqual(tab.view, "upgrade")
        self.assertEqual(tab.issue_list.selected()["film_key"], key)

    def test_audio_filter(self):
        _app, tab = self.ready_tab()
        tab.open_issue("audio")
        everything = len(self.shown_rows(tab.issue_list))
        self.assertTrue(tab.subs_filter.grid_info())
        tab.only_unsubtitled.set(True)
        tab._render_issue()
        rows = self.shown_rows(tab.issue_list)
        self.assertLess(len(rows), everything)
        self.assertTrue(all(r["your_subtitles"] == "No" for r in rows))
        self.assertIn(f"of {everything}", tab.issue_list.count_label.cget("text"))
        tab.open_issue("weaker")
        self.assertEqual(tab.subs_filter.grid_info(), {})
        self.assertFalse(tab.only_unsubtitled.get())


class LanguageTests(DoctorTestCase):
    def test_ticking_a_language(self):
        app, tab = self.ready_tab()
        self.assertEqual(tab.languages, ["en"])
        before = tab.data["issues"]["subtitles"]["count"]
        self.assertGreater(before, 0)
        self.assertIn("ja", tab.language_checks)                      # among the languages in your files
        tab.language_checks["ja"].invoke()
        self.assertEqual(tab.languages, ["en", "ja"])
        self.assertEqual(tab.data["issues"]["subtitles"]["count"], 0)
        self.assertEqual(app.settings["doctor_languages"], ["en", "ja"])
        self.assertEqual(app.keys[-1], "doctor.answer")
        # the language's own tick can't be the last one to go
        tab.language_checks["ja"].invoke()
        tab.language_checks["en"].invoke()
        self.assertEqual(tab.languages, ["en"])
        self.assertTrue(tab.language_vars["en"].get())
        self.assertIn("Keep at least one language", app.statuses[-1])

    def test_adding_a_language_from_the_list(self):
        real = D.TOP_LANGUAGES
        D.TOP_LANGUAGES = 2                                           # (the fixture has only a few languages)
        self.addCleanup(setattr, D, "TOP_LANGUAGES", real)
        _app, tab = self.ready_tab()
        names = list(tab.add_box.cget("values"))
        self.assertNotIn("English", names)
        name = names[0]
        tab.add_var.set(name)
        tab.add_box.selection_range(0, "end")                       # (as the drop-down leaves it, picked)
        tab._language_added()
        code = next(x["code"] for x in tab.data["available_languages"] if x["name"] == name)
        self.assertEqual(tab.languages, ["en", code])
        self.assertIn(code, tab.language_checks)
        self.assertEqual(tab.add_var.get(), "Add a language...")
        self.assertFalse(tab.add_box.selection_present())          # (no stray highlight on part of the words)

    def test_saved_languages_are_used(self):
        app = FakeApp(self.catalog)
        app.settings["doctor_languages"] = ["en", "ja"]
        tab = D.Tab(app, self.notebook)
        tab.catalog_changed(self.catalog, "ready")
        tab.shown()
        self.assertEqual([x["code"] for x in tab.data["languages"]], ["en", "ja"])
        app = FakeApp(self.catalog)
        app.settings["doctor_languages"] = ["Klingon"]                # a broken setting: English
        self.assertEqual(D.Tab(app, self.notebook).languages, ["en"])

    def test_the_film_page_gets_your_saved_languages(self):
        """doctor.film_issues with no languages (as the Film page calls it) uses the saved ones, even before the
        Doctor tab is shown, and a change of languages in the tab reaches it too."""
        from projectionist.catalog import load
        catalog = load(self.db)                                      # a fresh one: nothing asked of it yet
        jp = key_of(catalog, "Jp No Subs")
        app = FakeApp(catalog)
        app.settings["doctor_languages"] = ["en", "ja"]
        tab = D.Tab(app, self.notebook)
        tab.catalog_changed(catalog, "ready")                        # never shown
        self.assertEqual(app.keys, [])
        self.assertEqual(DR.film_issues(catalog, jp), [])            # Japanese is yours: nothing to say
        tab.shown()
        tab.language_checks["ja"].invoke()                           # untick Japanese
        self.assertEqual(tab.languages, ["en"])
        self.assertEqual([i["id"] for i in DR.film_issues(catalog, jp)], ["subtitles", "audio"])

    def test_without_settings(self):
        app, tab = self.ready_tab(settings=False)
        tab.language_checks["ja"].invoke()                           # kept for the session
        self.assertEqual(tab.languages, ["en", "ja"])
        self.assertFalse(hasattr(app, "settings"))

    def lay_out(self, tab, width, height=620):
        """Put the tab in the (withdrawn) window at a size and let Tk lay it out."""
        self.notebook.add(tab.frame, text=tab.title)
        self.notebook.place(x=0, y=0, width=width, height=height)
        for _ in range(4):
            self.root.update_idletasks()

    def test_the_language_row_wraps_in_a_narrow_window(self):
        """Ten languages at the app's narrowest (900 px): the ticks go onto more lines, and the drop-down stays
        whole inside the row instead of being cut off at the edge."""
        _app, tab = self.ready_tab()
        tab._set_languages(["en", "ja", "zh", "fr", "de", "it", "th", "ko", "es", "pt"])
        self.assertEqual(len(tab.language_checks), 10)
        self.lay_out(tab, 900)
        row, frame = tab.language_row, tab.language_frame
        width = row.winfo_width()
        self.assertGreater(width, 700)
        parts = list(tab.language_checks.values()) + [tab.add_box]
        for w in parts:
            self.assertTrue(w.place_info(), w)
            self.assertLessEqual(frame.winfo_x() + w.winfo_x() + w.winfo_reqwidth(), width, w.cget("text")
                                 if w is not tab.add_box else "the drop-down")
        lines = {w.winfo_y() for w in parts}
        self.assertGreater(len(lines), 1)
        self.assertEqual(tab.language_note.place_info(), {})        # no room for the hint
        self.assertGreaterEqual(frame.winfo_height(), max(w.winfo_y() + w.winfo_reqheight() for w in parts))
        # wide enough, everything goes on one line (and the hint after it when there's room)
        layout = tab.language_layout(4000)
        self.assertEqual({y for _w, _x, y in layout["spots"] if _w is not tab.add_box},
                         {layout["spots"][0][2]})
        self.assertIsNotNone(layout["note"])
        # one tick per line in a very narrow row: never a line with nothing on it
        narrow = tab.language_layout(60)
        self.assertEqual(len({y for _w, _x, y in narrow["spots"]}), len(parts))

    def test_many_languages_read_well(self):
        _app, tab = self.ready_tab()
        tab._set_languages(["en", "de", "it", "th", "ko"])
        notes = {s["id"]: s["note"] for s in tab.data["summary"]}
        self.assertEqual(notes["subtitles"], "none in your 5 languages")
        empty = D.empty_text("subtitles", tab.data)
        self.assertEqual(empty, "Nothing to do - every copy in another language has subtitles in one of your "
                                "languages.")
        self.assertEqual(D.empty_text("audio", tab.data), "Every copy has a soundtrack in one of your languages.")
        self.assertEqual(D.empty_text("subtitles", {"languages": [{"code": "en"}]}),
                         "Nothing to do - every copy in another language has English subtitles.")


class SettingsApp(FakeApp):
    """The fake app telling every tab of a change of setting, as App.preference_changed does."""

    def __init__(self, catalog=None, state="ready", settings=None):
        super().__init__(catalog, state)
        self.settings.update(settings or {})
        self.tabs = []

    def preference_changed(self, key, value):
        for tab in self.tabs:
            tab.preference_changed(key, value)


class SettingsTests(DoctorTestCase):
    """Settings > Library Doctor: what an upgrade candidate is, and your languages as ticks."""

    def settings_tab(self, catalog=None, show=True, **settings):
        catalog = catalog or self.catalog
        app = SettingsApp(catalog, settings=settings)
        tab = D.Tab(app, self.notebook)
        app.tabs.append(tab)
        tab.catalog_changed(catalog, "ready")
        if show:
            tab.shown()
        return app, tab

    def upgrade_films(self, tab):
        return {r["film"] for r in tab.data["issues"]["upgrade"]["rows"]}

    def test_upgrade_candidates_follow_the_settings(self):
        from projectionist import prefs
        from projectionist.catalog import load
        self.assertEqual((prefs.get(None, "doctor_upgrade_rating"), prefs.get(None, "doctor_upgrade_below")),
                         (8, "1080p"))                            # (today's check)
        catalog = load(self.db)
        app, tab = self.settings_tab(catalog, doctor_upgrade_rating=9, doctor_upgrade_below="4K")
        self.assertEqual(self.upgrade_films(tab), {"Cropped Nine (2014)", "Renamed Cut (1986)"})
        tile = next(s for s in tab.data["summary"] if s["id"] == "upgrade")
        self.assertEqual(tile["note"], "rated 9+ but below 4K")
        # shared with the Film page's issues
        self.assertEqual((catalog.cache["doctor.min_rating"], catalog.cache["doctor.upgrade_below"]), (9.0, "4K"))
        renamed = next(f.key for f in catalog.films.values() if f.title == "Renamed Cut")
        self.assertIn("upgrade", [i["id"] for i in DR.film_issues(catalog, renamed)])
        # changed on the Settings tab: asked again, and the list says what it looks for
        prefs.set(app, "doctor_upgrade_below", "1080p")
        self.assertEqual(app.keys[-1], "doctor.answer")
        self.assertEqual(self.upgrade_films(tab), set())
        self.assertEqual(D.empty_text("upgrade", tab.data), "Nothing to do - every film you rated 9 or more has a "
                                                            "copy in 1080p or better.")
        self.assertNotIn("upgrade", [i["id"] for i in DR.film_issues(catalog, renamed)])
        prefs.set(app, "doctor_upgrade_rating", 10)
        self.assertEqual(D.empty_text("upgrade", tab.data), "Nothing to do - every film you rated 10 has a copy "
                                                            "in 1080p or better.")
        prefs.reset(app, section="Library Doctor")
        self.assertEqual(self.upgrade_films(tab), {"Rated Eight (1980)"})

    def test_a_tab_never_shown_asks_nothing(self):
        from projectionist import prefs
        from projectionist.catalog import load
        catalog = load(self.db)
        app, tab = self.settings_tab(catalog, show=False)
        prefs.set(app, "doctor_upgrade_below", "4K")
        self.assertEqual(app.keys, [])                           # (it asks with them when it's first shown)
        self.assertEqual(catalog.cache["doctor.upgrade_below"], "4K")
        tab.shown()
        self.assertIn("Renamed Cut (1986)", self.upgrade_films(tab))

    def test_languages_as_ticks_on_the_settings_tab(self):
        from projectionist import prefs
        from projectionist.catalog import load
        from projectionist.ui import settings as S
        catalog = load(self.db)                                  # (its files not read yet)
        app = SettingsApp(catalog)
        settings = S.Tab(app, self.notebook)
        app.tabs.append(settings)
        lang = settings.controls["doctor_languages"]
        self.assertIsInstance(lang, D.LanguagesControl)
        self.assertEqual(lang.codes, ["en"])                      # (only yours until your files are read)
        names = list(lang.add_box.cget("values"))
        self.assertEqual(names[:2], ["Afrikaans", "Albanian"])   # every other language Plex tags
        self.assertNotIn("English", names)
        # the Library Doctor reads your files: their languages come first, the next time the Settings tab is shown
        self.notebook.add(settings.frame, text=settings.title)
        _app2, tab = self.settings_tab(catalog)
        self.assertEqual(lang.codes, ["en"])
        self.notebook.add(tab.frame, text=tab.title)
        self.notebook.select(tab.frame)
        self.notebook.select(settings.frame)
        self.root.update()                                        # (<<NotebookTabChanged>>)
        self.assertIn("ja", lang.codes)
        self.assertEqual(lang.codes[:len(tab._offered_languages())], tab._offered_languages())
        lang.checks["ja"].invoke()
        self.assertEqual(app.settings["doctor_languages"], ["en", "ja"])
        lang.checks["en"].invoke()
        lang.checks["ja"].invoke()                                # (the last one stays: says why)
        self.assertEqual(app.settings["doctor_languages"], ["ja"])
        self.assertTrue(lang.vars["ja"].get())
        self.assertIn("Keep at least one language", lang.error.cget("text"))
        lang.add_var.set("French")
        lang._added()
        self.assertEqual(app.settings["doctor_languages"], ["ja", "fr"])
        self.assertIn("fr", lang.codes)
        self.assertEqual(lang.add_var.get(), D.ADD_LANGUAGE)
        prefs.set(app, "doctor_languages", ["de"])                # (changed on the Library Doctor tab, say)
        self.assertEqual([c for c, v in lang.vars.items() if v.get()], ["de"])

    def test_language_choices(self):
        files = [{"code": "ja", "name": "Japanese", "tracks": 9}, {"code": "en", "name": "English", "tracks": 5},
                 {"code": "th", "name": "Thai", "tracks": 1}]
        real = D.TOP_LANGUAGES
        D.TOP_LANGUAGES = 2
        self.addCleanup(setattr, D, "TOP_LANGUAGES", real)
        ticks, names = D.language_choices(["en", "fr"], files)
        self.assertEqual(ticks, ["ja", "en", "fr"])
        self.assertEqual(names[0], "Thai")                        # (in your files: first)
        self.assertEqual(names[1:3], ["Afrikaans", "Albanian"])
        self.assertEqual(len(names), len(set(names)))
        self.assertFalse({"Japanese", "English", "French"} & set(names))
        self.assertEqual(D.files_languages(None), [])


class LazyPageTests(DoctorTestCase):
    def test_the_page_is_built_when_first_needed(self):
        """A tab nobody has opened adds nothing to the app's start: its page is built when it's first shown."""
        app = FakeApp(self.catalog)
        app.settings["doctor_languages"] = ["en", "ja"]
        tab = D.Tab(app, self.notebook)
        tab.catalog_changed(self.catalog, "ready")                    # not the tab on show: not asked yet
        self.assertFalse(tab._built)
        self.assertFalse(hasattr(tab, "scroll"))
        self.assertEqual(tab.content.winfo_children(), [])
        self.assertEqual(app.runs, 0)
        tab.open_issue("weaker")                                      # asked for before there's an answer
        self.assertFalse(tab._built)
        tab.shown()
        self.assertTrue(tab._built)
        self.assertEqual(tab.view, "weaker")
        self.assertTrue(tab.issue_card.grid_info())
        self.assertEqual(tab.summary.grid_info(), {})
        self.assertEqual([x["code"] for x in tab.data["languages"]], ["en", "ja"])
        self.assertTrue(tab.language_vars["ja"].get())
        # without a collection there's only the placeholder
        _app, empty = self.make_tab(None, "none")
        empty.shown()
        self.assertFalse(empty._built)


class SaveTests(DoctorTestCase):
    def patch_dialog(self, answer):
        asked = []
        real = D.filedialog.asksaveasfilename

        def fake(**kwargs):
            asked.append(kwargs)
            return answer
        D.filedialog.asksaveasfilename = fake
        self.addCleanup(setattr, D.filedialog, "asksaveasfilename", real)
        return asked

    def sheet_films(self, path):
        with zipfile.ZipFile(path) as z:
            ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
            strings = []
            if "xl/sharedStrings.xml" in z.namelist():
                sst = ET.fromstring(z.read("xl/sharedStrings.xml"))
                strings = ["".join(t.text or "" for t in si.iter(f"{ns}t")) for si in sst]
            sheet = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
        films = []
        for c in sheet.iter(f"{ns}c"):
            m = re.fullmatch(r"A(\d+)", c.get("r"))
            if m and int(m.group(1)) >= 6 and c.get("t") == "s":
                films.append(strings[int(c.find(f"{ns}v").text)])
        return films

    def test_save_this_list(self):
        app, tab = self.ready_tab()
        tab.open_issue("disk")
        tab.files.table.sort("size_gb")                               # smallest first
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "list.xlsx")
            asked = self.patch_dialog(path)
            tab.files.save_btn.invoke()
            self.assertTrue(os.path.isfile(path))
            self.assertEqual(asked[0]["defaultextension"], ".xlsx")
            self.assertEqual(asked[0]["initialfile"], "Library Doctor - Disk use - 2026-09-25.xlsx")
            self.assertEqual(asked[0]["initialdir"], os.path.dirname(self.db))
            films = self.sheet_films(path)
            self.assertEqual(films, [r["film"] for r in self.shown_rows(tab.files)])
            self.assertTrue(app.statuses[-1].startswith(f"Saved {len(films)} files to "))
            self.assertEqual(app.settings["doctor_save_dir"], d)
            # the next save starts in the same folder
            tab.open_issue("weaker")
            other = os.path.join(d, "weaker.xlsx")
            asked = self.patch_dialog(other)
            tab.issue_list.save_btn.invoke()
            self.assertEqual(asked[0]["initialdir"], d)
            self.assertEqual(self.sheet_films(other), ["Temple Kicks (1978)", "Two Versions (1950)"])
            self.assertIn("Saved 2 copies", app.statuses[-1])

    def test_open_a_saved_list(self):
        """Settings > Library Doctor > 'Open a list in your spreadsheet program after saving it': off by default
        (nothing opened - os.startfile would fail this test); on, the window opens it in the chosen program."""
        app, tab = self.ready_tab()
        tab.open_issue("weaker")
        opened = []
        with tempfile.TemporaryDirectory() as d:
            self.patch_dialog(os.path.join(d, "a.xlsx"))
            tab.issue_list.save_btn.invoke()
            self.assertTrue(app.statuses[-1].startswith("Saved 2 copies to "))
            app.settings["doctor_open_saved"] = True
            app.open_spreadsheet = opened.append
            path = os.path.join(d, "b.xlsx")
            self.patch_dialog(path)
            tab.issue_list.save_btn.invoke()
            self.assertEqual(opened, [path])
            self.assertTrue(app.statuses[-1].endswith(" - opening it."))

            def broken(p):
                raise OSError("no such program")
            app.open_spreadsheet = broken
            self.patch_dialog(os.path.join(d, "c.xlsx"))
            tab.issue_list.save_btn.invoke()
            self.assertTrue(os.path.isfile(os.path.join(d, "c.xlsx")))
            self.assertIn("Saved, but it couldn't be opened: no such program", tab.issue_list.error.cget("text"))
            # a window without its own opener: the program chosen for the Export tab's spreadsheets
            from projectionist import files, gui  # noqa: F401  (gui: the window's own settings - open_with)
            del app.open_spreadsheet
            app.apps = [("LibreOffice Calc", "scalc.exe"), ("Excel", "excel.exe")]
            app.settings["open_with"] = "Excel"
            real = files.open_spreadsheet
            files.open_spreadsheet = lambda p, program=None: opened.append((p, program))
            self.addCleanup(setattr, files, "open_spreadsheet", real)
            self.assertIsNone(D.open_saved(app, "x.xlsx"))
            self.assertEqual(opened[-1], ("x.xlsx", "excel.exe"))

    def test_cancel_writes_nothing(self):
        app, tab = self.ready_tab()
        with tempfile.TemporaryDirectory() as d:
            self.patch_dialog("")
            runs = app.runs
            tab.files.save_btn.invoke()
            self.assertEqual(app.runs, runs)
            self.assertEqual(os.listdir(d), [])

    def test_a_save_that_fails_says_why_in_the_tab(self):
        _app, tab = self.ready_tab()
        tab.open_issue("weaker")
        with tempfile.TemporaryDirectory() as d:
            self.patch_dialog(d)                                      # a folder, not a file
            tab.issue_list.save_btn.invoke()
            self.assertTrue(tab.issue_list.error.grid_info())
            self.assertIn("is a folder", tab.issue_list.error.cget("text"))
            self.patch_dialog(os.path.join(d, "fine.xlsx"))
            tab.issue_list.save_btn.invoke()
            self.assertEqual(tab.issue_list.error.grid_info(), {})


class SummaryTests(DoctorTestCase):
    def test_summary(self):
        _app, tab = self.ready_tab()
        self.assertEqual(tab.view, "disk")
        self.assertTrue(tab.summary.grid_info())
        for kind in ("library", "resolution", "drive", "codec"):
            view = tab.views[kind]
            view.redraw(560, int(view.cget("height")))
            self.assertTrue(view.find_withtag("chart"), kind)
            self.assertTrue(any(t.endswith(("GB", "MB", "TB")) for t in texts(view)), kind)
        sizes = [r["size_bytes"] for r in self.shown_rows(tab.files)]
        self.assertEqual(sizes, sorted(sizes, reverse=True))
        self.assertEqual(len(sizes), tab.data["totals"]["available"])
        # a bar lists just its files
        view = tab.views["library"]
        view.redraw(560, int(view.cget("height")))
        click(view, "bar0")
        library = D.disk_items(tab.data, "library")[0]["label"]
        rows = self.shown_rows(tab.files)
        self.assertTrue(rows and all(r["library"] == library for r in rows))
        self.assertIn(f"in {library}", tab.files.count_label.cget("text"))
        self.assertTrue(tab.show_all_link.grid_info())
        tab.clear_disk_filter()
        self.assertEqual(len(self.shown_rows(tab.files)), len(sizes))

    def test_layout(self):
        _app, tab = self.ready_tab()
        tab.columns = 0
        tab._reflow(int(1250 * tab.s))
        self.assertEqual(tab.columns, 2)
        tab._reflow(int(800 * tab.s))
        self.assertEqual(tab.columns, 1)
        event = type("Event", (), {"width": int(700 * tab.s)})()
        tab._fit_tiles(event)
        tall = int(tab.views["tiles"].cget("height"))
        event.width = int(1250 * tab.s)
        tab._fit_tiles(event)
        self.assertLess(int(tab.views["tiles"].cget("height")), tall)

    @unittest.skipUnless(HAVE_PIL, "Pillow isn't installed")
    def test_png(self):
        from PIL import Image
        from projectionist.ui.paint import render_png
        _app, tab = self.ready_tab()
        items = D.tile_items(tab.data)
        with tempfile.TemporaryDirectory() as d:
            for name, draw, w, h in (
                    ("tiles", lambda p: D.draw_tiles(p, items, "weaker"), 1200, D.tiles_height(1200, 12, 1.0)),
                    ("library", lambda p: D.C.bars(p, D.disk_items(tab.data, "library"),
                                                   value_fmt=D.space_text), 560, 120)):
                path = render_png(draw, os.path.join(d, f"{name}.png"), w, h)
                img = Image.open(path).convert("RGB")
                self.assertEqual(img.size, (w, h))
                self.assertGreater(len(img.getcolors(maxcolors=1 << 20)), 2, name)


class WordingTests(unittest.TestCase):
    def test_cells(self):
        self.assertEqual(D.cell("gb", 9.37), "9.37")
        self.assertEqual(D.cell("gb", 66.9), "66.9")
        self.assertEqual(D.cell("rating", 8), "8")
        self.assertEqual(D.cell("rating", 7.5), "7.5")
        self.assertEqual(D.cell("score", 8.0), "8.0")                   # IMDb's scores keep their decimal
        self.assertEqual(D.cell("ratio", 0.19), "0.19")
        self.assertEqual(D.cell("int", 1234), "1,234")
        self.assertIsNone(D.cell("text", None))
        self.assertEqual(D.space_text(5.61e12), "5.61 TB")
        self.assertEqual(D.space_text(347e9), "347 GB")
        self.assertEqual(D.space_text(1.5e9), "1.5 GB")
        self.assertEqual(D.initial_name("A/B: c", ""), f"Library Doctor - AB c - {time.strftime('%Y-%m-%d')}.xlsx")


class InTheAppTests(unittest.TestCase):
    def test_tab_in_the_main_window(self):
        try:
            root = hidden_root()
        except Exception as exc:
            self.skipTest(f"Tk unavailable: {exc}")
        from projectionist import gui
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "com.plexapp.plugins.library.db-2026-09-25")
            doctor_fixture(db)
            old = gui.SETTINGS_FILE, gui.SETTINGS_DIR
            gui.SETTINGS_FILE, gui.SETTINGS_DIR = os.path.join(d, "settings.json"), d
            app = None
            try:
                app = gui.App(root, db)
                tab = next(t for t in app.tabs if t.title == "Library Doctor")
                self.assertIsInstance(tab, D.Tab)
                app._pick_initial_db(db)
                deadline = time.time() + 30
                while app.catalog_state != "ready":
                    root.update()
                    time.sleep(0.02)
                    self.assertLess(time.time(), deadline, app.catalog_state)
                self.assertIs(app.goto("Library Doctor", issue="upgrade"), tab)
                deadline = time.time() + 20
                while tab.data is None:
                    root.update()
                    time.sleep(0.02)
                    self.assertLess(time.time(), deadline)
                root.update()
                self.assertEqual(tab.view, "upgrade")
                self.assertEqual([r["film"] for r in tab.issue_list.rows_in_order()], ["Rated Eight (1980)"])
                tab.language_checks["ja"].invoke()                    # remembered in the settings file
                deadline = time.time() + 10
                while tab.data["languages"][-1]["code"] != "ja":
                    root.update()
                    time.sleep(0.02)
                    self.assertLess(time.time(), deadline)
                self.assertEqual(gui.load_settings().get("doctor_languages"), ["en", "ja"])
            finally:
                gui.SETTINGS_FILE, gui.SETTINGS_DIR = old
                if app is not None:
                    app.shutdown()
                else:
                    root.destroy()
                gc.collect()                          # (on the main thread: see DoctorTestCase.tearDown)


if __name__ == "__main__":
    unittest.main()
