"""The whole window at once: a film clicked on any tab opens its page on the Film tab, the Film page's links open
the right tabs, and the search box's every kind of match lands in the right place - in the real gui.App on a
withdrawn root (never shown), over the fixture database. Also App.run's hold on
a job's callbacks (let go of on the window's thread, never the job's) and the help text of the new ask actions."""

import gc
import os
import sys
import tempfile
import threading
import time
import unittest
import weakref
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from projectionist import ask  # noqa: E402
from test_projectionist import build_fixture  # noqa: E402


def hidden_root():
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()                       # headless: never shown
    return root


def widgets(w, cls=None):
    out = []
    for c in w.winfo_children():
        if cls is None or isinstance(c, cls):
            out.append(c)
        out += widgets(c, cls)
    return out


class WindowTestCase(unittest.TestCase):
    """The real main window over a database, pumped until the collection is read."""

    db = None                             # set by setUp (the fixture) or a subclass's own database

    def setUp(self):
        try:
            self.root = hidden_root()
        except Exception as exc:          # noqa: BLE001
            self.skipTest(f"Tk unavailable: {exc}")
        import tkinter as tk
        from tkinter import filedialog, messagebox
        from projectionist import gui
        from projectionist.ui import paint
        self.gui = gui
        # nothing may pop up: menus, message boxes and file dialogs are stubbed
        self.stubs = [mock.patch.object(paint.Interaction, "_choose", lambda *a, **k: None),
                      mock.patch.object(tk.Menu, "tk_popup", lambda *a, **k: None),
                      mock.patch.object(messagebox, "showerror", lambda *a, **k: None),
                      mock.patch.object(messagebox, "showwarning", lambda *a, **k: None),
                      mock.patch.object(filedialog, "asksaveasfilename", lambda *a, **k: "")]
        for s in self.stubs:
            s.start()
        self.tmp = tempfile.TemporaryDirectory()
        if self.db is None:
            self.db = os.path.join(self.tmp.name, "com.plexapp.plugins.library.db-2026-09-25")
            build_fixture(self.db)
        self.old = gui.SETTINGS_FILE, gui.SETTINGS_DIR
        gui.SETTINGS_FILE, gui.SETTINGS_DIR = os.path.join(self.tmp.name, "settings.json"), self.tmp.name
        self.app = None

    def tearDown(self):
        if self.app is not None:
            try:
                self.app.shutdown()
            except Exception:             # noqa: BLE001
                pass
        for s in self.stubs:
            s.stop()
        try:
            self.root.destroy()
        except Exception:                 # noqa: BLE001
            pass
        gc.collect()                      # (Tk leftovers are freed here, on the main thread)
        self.gui.SETTINGS_FILE, self.gui.SETTINGS_DIR = self.old
        self.tmp.cleanup()

    # -- driving the window ---------------------------------------------------------------------------------
    def open_app(self):
        self.app = self.gui.App(self.root, self.db)
        self.pump(lambda: self.app.catalog_state == "ready", 120)
        self.settle()
        self.tabs = {t.title: t for t in self.app.tabs}
        self.catalog = self.app.catalog
        return self.app

    def pump(self, until, timeout=60):
        deadline = time.time() + timeout
        while not until():
            self.root.update()
            time.sleep(0.005)
            self.assertLess(time.time(), deadline, "timed out")

    def settle(self):
        """Until no background job is left and nothing waits on the queue."""
        app = self.app
        for _ in range(2):
            self.pump(lambda: not app._held and app.queue.empty(), 120)
            for _ in range(3):
                self.root.update()

    def go(self, fn, *args, **kwargs):
        out = fn(*args, **kwargs)
        self.settle()
        return out

    def select(self, title):
        tab = self.tabs[title]
        self.go(self.app.notebook.select, tab.frame)
        return tab

    def current(self):
        return self.app.current_tab()

    def assertOnFilm(self, key, what=""):
        film = self.tabs["Film"]
        self.assertIs(self.current(), film, what)
        self.assertEqual(film.current(), ("film", key), what)

    def open_row(self, table, match=lambda r: True):
        for iid in table.tree.get_children():
            if match(table.rows[iid]):
                table.tree.selection_set(iid)
                table.tree.focus(iid)
                self.root.update()
                row = table.rows[iid]
                self.go(table._opened)
                return row
        return None

    def film(self, label):
        return next(f for f in self.catalog.films.values() if f.label == label)


# ---------------------------------------------------------------------------------------------------------
class FixtureWindowTests(WindowTestCase):
    def test_films_on_every_tab_open_the_film_page(self):
        self.open_app()
        matrix = self.film("The Matrix (1999)")
        # Watch Next's results and details
        wn = self.tabs["Watch Next"]
        self.go(wn._open_film, {"title": "The Matrix", "year": 1999, "key": matrix.key})
        self.assertOnFilm(matrix.key, "Watch Next")
        # Credits: Enter or a double-click on a film, and the details' link
        credits = self.select("Credits")
        self.go(credits.navigate, title=matrix.label, film_key=matrix.key)
        row = self.open_row(credits.table, lambda r: r.get("key") == matrix.key)
        self.assertIsNotNone(row)
        self.assertOnFilm(matrix.key, "Credits")
        self.select("Credits")
        from projectionist.ui.widgets import LinkLabel
        link = [w for w in widgets(credits.details.inner, LinkLabel)
                if w.cget("text").startswith("Everything about this film")]
        self.go(link[0].event_generate, "<Button-1>")
        self.assertOnFilm(matrix.key, "Credits' link")
        # Six Degrees: a film in a chain or a list
        degrees = self.select("Six Degrees")
        self.go(degrees.open_film, {"title": "The Matrix", "year": 1999, "key": matrix.key})
        self.assertOnFilm(matrix.key, "Six Degrees")
        # Viewing and the Library Doctor
        self.go(self.tabs["Viewing"]._open_row, {"title": "The Matrix", "year": 1999, "key": matrix.key})
        self.assertOnFilm(matrix.key, "Viewing")
        self.go(self.tabs["Library Doctor"].open_film, {"film_key": matrix.key, "film": matrix.label})
        self.assertOnFilm(matrix.key, "Library Doctor")
        # Overview: a dot on the ratings chart
        overview = self.select("Overview")
        if overview._film_labels:
            key = next(iter(overview._film_labels))
            self.go(overview._open_film, key)
            self.assertOnFilm(key, "Overview")

    def test_the_film_page_links_go_to_the_right_tabs(self):
        from tkinter import ttk
        from projectionist.ui.widgets import LinkLabel
        self.open_app()
        matrix = self.film("The Matrix (1999)")
        film = self.tabs["Film"]
        self.go(self.app.goto, "Film", film_key=matrix.key, title=matrix.label)
        self.assertOnFilm(matrix.key)

        def button(text):
            return next(b for b in widgets(film.page, ttk.Button) if b.cget("text") == text)

        def link(card, start):
            return next(w for w in widgets(film.cards[card], LinkLabel) if w.cget("text").startswith(start))
        self.go(button("More like this in Watch Next").invoke)
        wn = self.tabs["Watch Next"]
        self.assertIs(self.current(), wn)
        self.assertEqual(wn.like_box.get(), matrix.label)
        self.go(self.app.goto, "Film", film_key=matrix.key, title=matrix.label)
        self.go(button("Scenes after the credits?").invoke)
        credits = self.tabs["Credits"]
        self.assertIs(self.current(), credits)
        self.assertEqual(credits.table.selected()["key"], matrix.key)
        self.go(self.app.goto, "Film", film_key=matrix.key, title=matrix.label)
        director = matrix.directors[0]
        self.go(link("header", director.name).event_generate, "<Button-1>")
        degrees = self.tabs["Six Degrees"]
        self.assertIs(self.current(), degrees)
        self.assertEqual(degrees.person["id"], director.person)          # by id, not by name
        self.go(self.app.goto, "Film", film_key=matrix.key, title=matrix.label)
        row = self.open_row(film.tables["cast"])
        self.assertIs(self.current(), degrees)
        self.assertEqual(degrees.person["id"], row["id"])
        # a library-health issue: its list in the Library Doctor, with this film's row picked
        hk = self.film("Hong Kong Action (1985)")
        self.go(self.app.goto, "Film", film_key=hk.key, title=hk.label)
        head = next(w for w in widgets(film.cards["issues"], ttk.Label) if "Upgrade candidates" in w.cget("text"))
        self.go(head.event_generate, "<Button-1>")
        doctor = self.tabs["Library Doctor"]
        self.assertIs(self.current(), doctor)
        self.assertEqual(doctor.view, "upgrade")
        self.assertEqual(doctor.issue_list.selected()["film_key"], hk.key)

    def test_search_results_land_where_they_should(self):
        self.open_app()
        bar = self.app.search_bar
        self.pump(bar.ready, 60)
        seen = {}
        for q in ("matrix", "keanu", "wachowski", "top 250", "action", "movies", "classics", "village roadshow",
                  "hong kong", "united states", "clark"):
            for group in bar.query(q).get("groups", []):
                for r in group["results"][:1]:
                    seen.setdefault(r["kind"], r)
        self.assertEqual(set(seen), {"film", "person", "collection", "genre", "country", "studio", "library",
                                     "critic"})
        for kind, r in seen.items():
            tab_name, kwargs = bar.target(r)
            self.go(bar.open, r)
            self.assertEqual(self.current().title, tab_name, kind)
            if kind == "film":
                self.assertOnFilm(r["key"])
            elif kind == "person":
                self.assertEqual(self.tabs["Six Degrees"].person["id"], r["id"])
            elif kind == "studio":
                self.assertEqual(self.tabs["Film"].current(), ("studio", r["id"]))
            elif kind == "critic":            # (too few ratings here to compare critics: it's asked for)
                self.assertEqual(self.tabs["Watch Next"]._view(), "critics")
            elif kind in ("genre", "library"):
                self.assertEqual(self.tabs["Watch Next"].vars[kind].get(), r["id"])
            elif kind in ("country", "collection"):
                self.assertEqual(self.tabs["Watch Next"].extra[1], r["id"])


# ---------------------------------------------------------------------------------------------------------
class JobHoldTests(WindowTestCase):
    """App.run keeps what a job needs from the window - its work, done() and failed() and whatever they hold, a tab
    and its Tk variables - on the window's thread, and lets go of it there once the job's thread has finished: Tk
    objects freed on another thread can crash the whole program."""

    def make_app(self):
        self.app = self.gui.App(self.root, None)
        self.root.after_cancel(self.app._timers.pop("pick"))
        return self.app

    def test_callbacks_are_let_go_of_on_the_window_thread(self):
        app = self.make_app()

        class Owner:
            def done(self, result):
                pass
        freed = []
        for cancel in (False, True):
            owner = Owner()
            weakref.finalize(owner, lambda: freed.append(threading.current_thread() is threading.main_thread()))
            started, go = threading.Event(), threading.Event()

            def work():
                started.set()
                go.wait(5)
                return 1
            job = app.run(work, owner.done, key="test.hold")
            thread = app._held[job][4]
            del owner
            self.assertTrue(started.wait(5))
            if cancel:
                job.cancel()
            go.set()
            thread.join(5)
            self.assertFalse(thread.is_alive())
            gc.collect()
            self.assertEqual(len(freed), int(cancel))       # the thread has ended; the window still holds it
            self.pump(lambda: not app._held)
            gc.collect()
            self.assertEqual(freed, [True] * (1 + int(cancel)))     # ...and let go of it on its own thread

    def test_closing_waits_for_the_jobs_it_called_off(self):
        from projectionist import jobs
        app = self.make_app()
        started = threading.Event()

        def work():
            started.set()
            while True:
                jobs.check()
                time.sleep(0.002)
        job = app.run(work, lambda r: None, status="Working...")
        thread = app._held[job][4]
        self.assertTrue(started.wait(5))
        app.shutdown()
        self.app = None
        self.assertTrue(job.cancelled)
        self.assertFalse(thread.is_alive())                 # stopped before the window went
        self.assertEqual(app._held, {})


# ---------------------------------------------------------------------------------------------------------
class AskHelpTests(unittest.TestCase):
    def test_the_new_actions_say_what_they_take(self):
        actions = ask.handle({"action": "help"})["actions"]
        for action, words in (("film", ["film_key", "parts", "core", "similar", "critics", "history", "issues"]),
                              ("search", ["q", "count", "groups", "critics", "studios"]),
                              ("critics", ["view", "overview", "critic", "picks", "evaluate", "names", "min_shared"]),
                              ("habits", ["year", "latest", "min_rating", "forgotten_days", "parts", "most_played"]),
                              ("doctor", ["languages", "issue", "count", "min_rating", "film_key"])):
            for word in words:
                self.assertIn(word, actions[action], f"{action}: {word}")


if __name__ == "__main__":
    unittest.main()
