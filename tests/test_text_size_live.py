"""Settings > Appearance > Text size shows at once, with no restart: Tk's scaling, every font (the Tk fonts, the
app's own named fonts - styles, text tags, the log - and the charts' font cache) measured again, the styles in the
new size (the tables' row heights), every chart's scale and height, the window's own parts, and each tab - the
Settings and Overview tabs re-measure themselves, the others are built again in their place over the collection
already read, showing what they showed (the Film page's film, Watch Next's filters), their old background jobs and
timers called off. The real window over the fixture database on a withdrawn root (never shown); message boxes,
dialogs, menus and opening files are stubbed, and the settings file is a temporary one."""

import gc
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from projectionist import prefs  # noqa: E402
from test_projectionist import build_fixture  # noqa: E402


def hidden_root():
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()                       # headless: never shown
    return root


def linespace(root, name) -> int:
    return int(root.tk.call("font", "metrics", name, "-linespace"))


# ---------------------------------------------------------------------------------------------------------
class NamedFontTest(unittest.TestCase):
    """theme.font(): named fonts, re-measured in place when Tk's scaling changes - a tuple font isn't."""

    # (On Windows, Tk's scaling is kept with the display, which every Tk root in the process shares: put it back.)
    def setUp(self):
        try:
            self.root = hidden_root()
        except Exception as exc:          # noqa: BLE001
            self.skipTest(f"Tk unavailable: {exc}")
        self.scaling = self.root.tk.call("tk", "scaling")

    def tearDown(self):
        self.root.tk.call("tk", "scaling", self.scaling)
        self.root.destroy()
        self.root = None
        gc.collect()

    def test_a_named_font_per_size_and_style_made_once(self):
        from tkinter import ttk
        from projectionist.ui import theme as T
        name = T.font(self.root, 9, "bold")
        self.assertEqual(name, "App9Bold")
        self.assertEqual(T.font(ttk.Style(self.root), 9, "bold"), name)          # (a style of the same window)
        self.assertIn(name, self.root.tk.splitlist(self.root.tk.call("font", "names")))
        actual = self.root.tk.splitlist(self.root.tk.call("font", "actual", name))
        actual = dict(zip(actual[::2], actual[1::2]))
        self.assertEqual((actual["-weight"], int(actual["-size"])), ("bold", 9))
        self.assertEqual(T.font(self.root, 10, "italic"), "App10Italic")
        self.assertEqual(T.font(self.root, 9, family="Consolas"), "AppConsolas9")
        self.assertEqual(self.root.__dict__["_app_fonts"]["AppConsolas9"], "Consolas")   # (its own family)

    def test_the_fonts_grow_with_the_scaling_in_place(self):
        from tkinter import font as tkfont
        from projectionist.ui import theme as T
        T.apply_styles(self.root)
        name = T.font(self.root, 9)
        painter_font = tkfont.Font(root=self.root, family="Segoe UI", size=9)       # (as the charts cache them)
        before = {n: linespace(self.root, n) for n in (name, "TkDefaultFont", painter_font.name)}
        # (why named: a font given as a tuple is cached by Tk at its old size for as long as something uses it)
        described = (T.FAMILY, 9)
        label = __import__("tkinter").Label(self.root, font=described)             # noqa: F841  (uses it)
        tuple_before = int(self.root.tk.call("font", "metrics", described, "-linespace"))
        self.root.tk.call("tk", "scaling", float(self.scaling) * 1.3)
        T.text_size_changed(self.root)
        for n, was in before.items():
            self.assertGreater(linespace(self.root, n), was, n)
        self.assertEqual(int(self.root.tk.call("font", "metrics", described, "-linespace")), tuple_before)

    def test_the_styles_follow(self):
        from tkinter import ttk
        from projectionist.ui import theme as T
        T.apply_styles(self.root)
        style = ttk.Style(self.root)
        rows = int(style.lookup("Treeview", "rowheight"))
        self.assertEqual(style.lookup("CardTitle.TLabel", "font"), "App10Bold")   # (named: never a tuple)
        self.root.tk.call("tk", "scaling", float(self.scaling) * 1.3)
        T.text_size_changed(self.root)
        self.assertEqual(int(style.lookup("Treeview", "rowheight")), int(22 * T.scale(self.root)))
        self.assertGreater(int(style.lookup("Treeview", "rowheight")), rows)

    def test_a_chart_takes_the_new_scale_and_height(self):
        from projectionist.ui import widgets as W
        from projectionist.ui import theme as T
        view = W.ChartView(self.root, lambda p: None, height=200)
        s = view.s
        self.assertEqual(int(view.cget("height")), int(200 * s))
        self.root.tk.call("tk", "scaling", float(self.scaling) * 1.3)
        W.rescale_charts(self.root)
        self.assertAlmostEqual(view.s, T.scale(self.root))
        self.assertGreater(view.s, s)
        self.assertEqual(int(view.cget("height")), int(200 * view.s))
        view.show(lambda p: None, height=100)                  # (a new height is in the new scale too)
        self.assertEqual(int(view.cget("height")), int(100 * view.s))

    def test_timers_under_a_widget_are_called_off(self):
        import tkinter as tk
        from projectionist import gui
        frame = tk.Frame(self.root)
        inner = tk.Frame(frame)
        fired = []
        timers = [frame.after(60000, lambda: fired.append(1)), inner.after(60000, lambda: fired.append(2)),
                  inner.after_idle(lambda: fired.append(3))]
        other = self.root.after(60000, lambda: None)             # (not in it: left alone)
        self.assertEqual(gui._cancel_timers_under(frame), 3)
        pending = self.root.tk.splitlist(self.root.tk.call("after", "info"))
        for t in timers:
            self.assertNotIn(t, pending)
        self.assertIn(other, pending)
        self.root.after_cancel(other)
        self.root.update()
        self.assertEqual(fired, [])


# ---------------------------------------------------------------------------------------------------------
class LiveTextSizeTest(unittest.TestCase):
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
        self.scaling = self.root.tk.call("tk", "scaling")
        self.shown = []
        self.stubs = [
            mock.patch.object(paint.Interaction, "_choose", lambda *a, **k: None),
            mock.patch.object(tk.Menu, "tk_popup", lambda *a, **k: None),
            mock.patch.object(tk.Menu, "post", lambda *a, **k: None),
            mock.patch.object(messagebox, "showerror", lambda *a, **k: self.shown.append(("error",) + a)),
            mock.patch.object(messagebox, "showwarning", lambda *a, **k: self.shown.append(("warning",) + a)),
            mock.patch.object(messagebox, "askyesno", lambda *a, **k: False),
            mock.patch.object(filedialog, "askopenfilename", lambda *a, **k: ""),
            mock.patch.object(filedialog, "asksaveasfilename", lambda *a, **k: ""),
            mock.patch.object(filedialog, "askdirectory", lambda *a, **k: ""),
            mock.patch.object(gui, "open_spreadsheet", lambda *a, **k: None),
            mock.patch.object(gui, "_open_path", lambda *a, **k: None),
        ]
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "com.plexapp.plugins.library.db-2026-09-20")
        build_fixture(self.db)
        self.stubs.append(mock.patch.multiple(gui, SETTINGS_FILE=os.path.join(self.tmp.name, "settings.json"),
                                              SETTINGS_DIR=self.tmp.name))
        for s in self.stubs:
            s.start()
        self.app = self.gui.App(self.root, None)
        self.root.after_cancel(self.app._timers.pop("pick"))      # (the tests pick the database themselves)
        self.app.load_db(self.db)
        self.pump(lambda: self.app.catalog_state == "ready")
        self.idle()

    def tearDown(self):
        # (Tk's scaling first, while the window is still there to set it on: on Windows every Tk root in the
        # process shares it, so a size left behind would make every later test's windows bigger)
        self.root.tk.call("tk", "scaling", self.scaling)
        try:
            self.app.shutdown()
        except Exception:                 # noqa: BLE001
            pass
        for s in reversed(self.stubs):
            s.stop()
        try:
            self.root.destroy()
        except Exception:                 # noqa: BLE001
            pass
        # Let go of the window and free it here: left for the collector, it could be freed on a later test's job
        # thread, and Tk freed on the wrong thread takes the whole run down
        self.app = self.root = None
        gc.collect()
        from projectionist.ui import theme as T
        T.use("light")
        self.tmp.cleanup()

    def pump(self, until, timeout=60):
        deadline = time.time() + timeout
        while not until():
            self.root.update()
            time.sleep(0.005)
            self.assertLess(time.time(), deadline, "timed out")

    def idle(self):
        self.pump(lambda: not self.app._held and self.app.queue.empty())
        self.root.update()

    def tab(self, title):
        return next(t for t in self.app.tabs if t.title == title)

    def test_the_setting_shows_at_once(self):
        p = prefs.pref("text_size")
        self.assertTrue(p.live)
        self.assertNotIn("next time", prefs.SECTIONS["Appearance"].hint)
        self.assertIn("text size shows at once", prefs.SECTIONS["Appearance"].hint)
        settings = self.tab("Settings")
        help_text = settings.controls["text_size"].help.cget("text")
        self.assertNotIn("next time the app opens", help_text)

    def test_a_new_size_everywhere_at_once(self):
        from tkinter import ttk
        from projectionist.ui import theme as T
        from projectionist.ui import widgets as W
        app, root = self.app, self.root
        s = T.scale(root)
        fonts = ("TkDefaultFont", "App9Bold", "App10Bold", "AppConsolas9")
        before = {n: linespace(root, n) for n in fonts}
        overview = self.tab("Overview")
        view = overview.views["decades"]
        view.redraw(500, 300)                                  # (the charts' own fonts, cached per window)
        chart_fonts = {k: f.metrics("linespace") for k, f in root.__dict__["_chart_fonts"].items()}
        self.assertTrue(chart_fonts)
        old = {t.title: t for t in app.tabs}
        app.notebook.select(self.tab("Viewing").frame)
        root.update()
        # the radio button on the Settings tab (a click invokes it)
        settings = self.tab("Settings")
        next(b for b in settings.controls["text_size"].buttons if str(b.cget("value")) == "130").invoke()
        self.idle()
        self.assertEqual(app.settings["text_size"], 130)
        self.assertAlmostEqual(T.scale(root), s * 1.3, places=2)
        for n, was in before.items():
            self.assertGreater(linespace(root, n), was, n)
        for k, f in root.__dict__["_chart_fonts"].items():
            if k in chart_fonts:
                self.assertGreater(f.metrics("linespace"), chart_fonts[k], k)
        self.assertEqual(int(ttk.Style(root).lookup("Treeview", "rowheight")), int(22 * T.scale(root)))
        # every chart in the new scale, at its height in it
        views = [v for v in W._VIEWS if W._alive(v) and v._root() is root]
        self.assertTrue(views)
        for v in views:
            self.assertAlmostEqual(v.s, T.scale(root), places=4)
        self.assertEqual(int(view.cget("height")), int(view._height * view.s))
        # Settings and Overview re-measured themselves; the rest were built again, in the same order
        now = {t.title: t for t in app.tabs}
        self.assertEqual(list(now), list(old))
        self.assertIs(now["Settings"], old["Settings"])
        self.assertIs(now["Overview"], old["Overview"])
        self.assertAlmostEqual(now["Overview"].s, T.scale(root), places=4)
        for title in ("Film", "Watch Next", "Viewing", "Credits", "Six Degrees", "Library Doctor"):
            self.assertIsNot(now[title], old[title], title)
            self.assertFalse(old[title].frame.winfo_exists(), title)
            self.assertEqual(now[title].catalog, app.catalog, title)          # (the collection already read)
        self.assertEqual([app.notebook.tab(t, "text") for t in app.notebook.tabs()][1:], list(now))
        self.assertIs(app.current_tab(), now["Viewing"])                     # (the tab in front stays in front)
        self.assertEqual(root.minsize(), (min(int(900 * T.scale(root)), root.winfo_screenwidth() - 40),
                                          min(int(620 * T.scale(root)), root.winfo_screenheight() - 80)))
        self.assertNotIn("Traceback", app.log.get("1.0", "end"))
        self.assertIn("Text size now 130%", app.log.get("1.0", "end"))
        # and back
        prefs.set(app, "text_size", 100)
        self.idle()
        self.assertAlmostEqual(T.scale(root), s, places=4)
        for n, was in before.items():
            self.assertEqual(linespace(root, n), was, n)
        self.assertNotIn("Traceback", app.log.get("1.0", "end"))
        self.assertEqual(self.shown, [])

    def test_the_tabs_built_again_show_what_they_showed(self):
        app, root = self.app, self.root
        film = next(iter(app.catalog.films.values()))
        app.goto("Film", film_key=film.key)
        self.idle()
        watch = self.tab("Watch Next")
        genres = watch.boxes["genre"].cget("values")
        genre = root.tk.splitlist(genres)[1]
        watch.vars["genre"].set(genre)
        watch.vars["include_watched"].set(True)
        history = list(self.tab("Film").history)
        prefs.set(app, "text_size", 115)
        self.idle()
        self.assertEqual(self.tab("Film").history, history)
        self.assertEqual(self.tab("Film").current(), ("film", film.key))
        self.assertIs(app.current_tab(), self.tab("Film"))
        filters = self.tab("Watch Next").filters()
        self.assertEqual((filters["genre"], filters["include_watched"]), (genre, True))
        self.assertNotIn("Traceback", app.log.get("1.0", "end"))

    def test_the_tabs_options_come_through_a_new_size(self):
        """What you'd set on a tab stays set: Credits' Show ticks, Six Degrees' roles, 'Directors count too', the
        names to avoid, Most connected's count, the groups Bridges compares and the troupes' numbers, and what
        Viewing's months card counts."""
        app, root = self.app, self.root
        app.notebook.select(self.tab("Viewing").frame)          # (its history read: the months card is drawn)
        viewing = self.tab("Viewing")
        self.pump(lambda: viewing.data is not None)
        self.idle()
        has_hours = any(m.get("hours") is not None for m in viewing.data.get("months", []))
        viewing.month_mode.set("hours")
        self.assertEqual(viewing.keep()["months"], "hours")
        credits = self.tab("Credits")
        credits.verdict_vars["Maybe"].set(not credits.verdict_vars["Maybe"].get())
        ticks = {v: var.get() for v, var in credits.verdict_vars.items()}
        degrees = self.tab("Six Degrees")
        roles = {"Connect": "Top 3 billed", "Person": "Top 5 billed", "Most connected": "Top 10 billed",
                 "Bridges": "Top 3 billed"}
        for mode, var in degrees._role_boxes().items():
            var.set(roles[mode])
        degrees.connect_directors.set(True)                      # (as a click on it: the user's now)
        degrees.avoid_var.set("Somebody, Someone Else")
        degrees.center_count.set("50")
        degrees.troupe_shared.set("4")
        degrees.troupe_billing.set("6")
        degrees.troupe_size.set("5")
        degrees._fill_groups()                                   # (Bridges looked at: its groups filled...)
        degrees.group_kind["a"].set("Decade")
        degrees._fill_values("a")                                # (...and one side changed)
        groups = (degrees._group("a"), degrees._group("b"))
        prefs.set(app, "text_size", 130)
        self.idle()
        viewing, credits, degrees = self.tab("Viewing"), self.tab("Credits"), self.tab("Six Degrees")
        self.assertIsNot(degrees, None)
        self.assertEqual(viewing.month_mode.get(), "hours" if has_hours else "films")
        if not has_hours:        # (no playback clock in this history, so plays: restore on its own, over the data)
            viewing.restore({"months": "hours"})
            self.assertEqual(viewing.month_mode.get(), "hours")
            self.assertEqual(viewing.cards["months"].title_label.cget("text"), "Hours per month")
        self.assertEqual({v: var.get() for v, var in credits.verdict_vars.items()}, ticks)
        self.assertEqual({mode: var.get() for mode, var in degrees._role_boxes().items()}, roles)
        self.assertTrue(degrees.connect_directors.get())
        self.assertFalse(degrees._directors_auto["connect"])     # (still the user's choice, not the tab's)
        self.assertEqual(degrees.avoid_var.get(), "Somebody, Someone Else")
        self.assertEqual(degrees.center_count.get(), "50")
        self.assertEqual((degrees.troupe_shared.get(), degrees.troupe_billing.get(), degrees.troupe_size.get()),
                         ("4", "6", "5"))
        degrees._fill_groups()                                   # (as Bridges does when it's looked at)
        self.assertEqual((degrees._group("a"), degrees._group("b")), groups)
        self.assertNotIn("Traceback", app.log.get("1.0", "end"))

    def test_the_old_tabs_jobs_are_called_off(self):
        import threading
        app = self.app
        release = threading.Event()
        called = []
        mine = app.run(lambda: release.wait(5), done=lambda r: called.append("watchnext"), key="watchnext.test")
        others = app.run(lambda: release.wait(5), done=lambda r: called.append("other"), key="other.test")
        prefs.set(app, "text_size", 115)
        self.assertTrue(mine.cancelled)                     # (a Watch Next job: its tab was built again)
        self.assertFalse(others.cancelled)
        release.set()
        self.idle()
        self.assertEqual(called, ["other"])

    def test_the_old_tabs_are_let_go_of_on_this_thread(self):
        """Every tab built again is freed - none kept alive for good by a trace's Tcl command that calls it (Credits'
        search box, Six Degrees' 'Directors count too') - and freed by the window itself, on the UI thread, once
        the old tabs' jobs have ended: never left for the collector, which could free them on a job's thread."""
        import threading
        import weakref
        app = self.app
        release = threading.Event()
        credits = self.tab("Credits")         # (a job of an old tab's, still going when the size changes, holds it)
        app.run(lambda: release.wait(5), done=lambda _r, tab=credits: tab, key="credits.test")
        old = {t.title: weakref.ref(t) for t in app.tabs}
        del credits
        gc.collect()
        gc.disable()                          # (from here only the window's own collection can free them)
        try:
            prefs.set(app, "text_size", 115)
            self.root.update()
            rebuilt = ("Film", "Watch Next", "Viewing", "Credits", "Six Degrees", "Library Doctor")
            self.assertIsNotNone(old["Credits"](), "freed while its job still held it")
            self.assertTrue(app._old_tab_jobs)
            release.set()
            self.idle()
            self.root.update()                # (the collection, queued for when the window is idle)
            self.assertEqual([t for t in rebuilt if old[t]() is not None], [])
            self.assertIsNotNone(old["Settings"]())               # (re-measured in place, not built again)
            self.assertEqual(app._old_tab_jobs, set())
        finally:
            gc.enable()
        self.assertNotIn("Traceback", app.log.get("1.0", "end"))

    def test_the_same_size_again_does_nothing(self):
        app = self.app
        tabs = list(app.tabs)
        app.apply_text_size()                               # (100, as it is)
        self.assertEqual(app.tabs, tabs)
        self.assertNotIn("Text size now", app.log.get("1.0", "end"))


if __name__ == "__main__":
    unittest.main()
