"""Tests for the app's shared UI pieces: charts (drawn to PNG and to a hidden Tk canvas), widgets, and the tabbed
main window's services. Everything is headless - no window is ever shown."""

import os
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from projectionist.ui import charts as C  # noqa: E402
from projectionist.ui import theme as T  # noqa: E402
from support import wider  # noqa: E402

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


SAMPLE_BARS = [{"label": f"Person {i}", "value": 50 - i * 4, "key": i} for i in range(10)]
TIMELINE = dict(duration=8146.2, credits_start=7683.6,
                stretches=[{"start_sec": 7683.6, "end_sec": 7715.6, "counted": True},
                           {"start_sec": 7743.6, "end_sec": 7871.6, "counted": True},
                           {"start_sec": 8073.6, "end_sec": 8113.6, "counted": True}],
                scenes=[{"start_sec": 7715.6, "end_sec": 7743.6, "verdict": "Likely", "kind": "Mid-credits"},
                        {"start_sec": 7871.6, "end_sec": 7925.6, "verdict": "Maybe", "why": "short"},
                        {"start_sec": 8113.6, "end_sec": 8146.2, "verdict": "Likely"}])


def every_chart():
    """(name, draw, width, height) for each chart type with representative data."""
    steps = [{"from": "Ann", "to": "Bob", "from_id": "a", "to_id": "b", "film": {"title": "One", "year": 1990},
              "from_role": "Hero (billed 1)", "to_role": "Villain (billed 2)"},
             {"from": "Bob", "to": "Cat", "from_id": "b", "to_id": "c", "film": {"title": "Two", "year": 1991},
              "from_role": "Cop (billed 3)", "to_role": "Thief (billed 1)"}]
    nodes = [{"id": str(i), "name": f"Actor {i}", "weight": 10 + i, "center": i == 0} for i in range(8)]
    edges = [{"a": str(i), "b": str(j), "weight": (i + j) % 5 + 1} for i in range(8) for j in range(i + 1, 8)]
    return [
        ("bars", lambda p: C.bars(p, SAMPLE_BARS, title="Bars", subtitle="sub"), 500, 320),
        ("columns", lambda p: C.columns(p, [{"label": f"{1920 + 10 * i}s", "value": v}
                                           for i, v in enumerate([3, 17, 58, 50, 105, 210, 420, 500, 630, 695])],
                                        title="Columns"), 500, 260),
        ("diverging", lambda p: C.diverging(p, [("Up", 1.1), ("Flat", 0.02), ("Down", -0.6)], title="Div",
                                            legend=("higher", "lower")), 500, 200),
        ("waterfall", lambda p: C.waterfall(p, 6.7, [{"label": "IMDb 8.8", "value": 1.5},
                                                    {"label": "Drama", "value": -0.06}], 8.4, rest=0.26,
                                            title="Waterfall"), 500, 260),
        ("stacked", lambda p: C.stacked(p, [{"label": "Yes", "value": 241, "color": T.GOOD},
                                           {"label": "Maybe", "value": 57, "color": T.WARNING},
                                           {"label": "None found", "value": 2092, "color": T.BASELINE}],
                                        title="Stacked"), 600, 110),
        ("panels", lambda p: C.metric_panels(p, [{"title": "Miss", "rows": [
            {"label": "Average", "value": 0.93}, {"label": "Model", "value": 0.68, "emphasis": True}]}] * 3,
            title="Panels"), 700, 180),
        ("timeline", lambda p: C.timeline(p, TIMELINE["duration"], TIMELINE["stretches"], TIMELINE["scenes"],
                                          TIMELINE["credits_start"], title="Timeline"), 700, 180),
        ("chain", lambda p: C.chain(p, steps, title="Chain"), 500, C.chain_height(1, 2)),
        ("network", lambda p: C.network(p, nodes, edges, title="Network"), 500, 380),
        ("butterfly", lambda p: C.butterfly(p, [{"label": "Jet Li", "left": 13, "right": 9},
                                               {"label": "Donnie Yen", "left": 17, "right": 5}],
                                            left_title="HK", right_title="US", title="Butterfly"), 500, 160),
        ("scatter", lambda p: C.scatter(p, [{"x": 7.5, "y": 8, "tip": "a"}, {"x": 5, "y": 4}], title="Scatter",
                                        x_label="IMDb", y_label="You"), 400, 320),
        ("tiles", lambda p: C.tiles(p, [{"label": "Films", "value": "1,204"}, {"label": "Hours", "value": "2,113",
                                                                             "note": "of film"}]), 500, 80),
        ("message", lambda p: C.message(p, "Nothing here", "Try another filter."), 300, 120),
        ("empty bars", lambda p: C.bars(p, [], title="Empty"), 300, 120),
    ]


class ChartTests(unittest.TestCase):
    def test_helpers(self):
        self.assertEqual(C.nice_ticks(0, 695, 4), [0, 200, 400, 600, 800])
        self.assertEqual(C.compact(41562), "41.6K")
        self.assertEqual(C.compact(1500000), "1.5M")
        self.assertEqual(C.clock(7715.6), "2:08:35")   # truncates: never past the real end
        self.assertEqual(T.ramp(0), T.RAMP[250])
        look = T.LOOK
        try:
            T.use("light")                                  # (another test's window may have left a dark look)
            self.assertEqual(T.ink_on(T.BLUE), "#ffffff")
            self.assertEqual(T.ink_on(T.YELLOW), T.INK)
            T.use("graphite")                               # dark ink on a light fill even where INK is light
            self.assertEqual(T.ink_on("#fab219"), T.INK_ON_LIGHT)
        finally:
            T.use(look)

    @unittest.skipUnless(HAVE_PIL, "Pillow isn't installed")
    def test_every_chart_draws_to_png(self):
        from PIL import Image
        from projectionist.ui.paint import render_png
        with tempfile.TemporaryDirectory() as d:
            for name, draw, w, h in every_chart():
                path = render_png(draw, os.path.join(d, f"{name}.png"), w, h, 1.25)
                img = Image.open(path).convert("RGB")
                self.assertEqual(img.size, (w, h), name)
                colours = img.getcolors(maxcolors=1 << 20)
                self.assertGreater(len(colours), 2, f"{name} drew nothing")

    def test_every_chart_draws_on_a_hidden_canvas(self):
        try:
            root = hidden_root()
        except Exception as exc:
            self.skipTest(f"Tk unavailable: {exc}")
        try:
            from projectionist.ui.widgets import ChartView
            for name, draw, w, h in every_chart():
                view = ChartView(root, draw, height=h)
                view.redraw(w, h)
                self.assertTrue(view.find_withtag("chart"), f"{name} drew nothing")
        finally:
            root.destroy()

    def test_a_broken_chart_shows_a_message_not_an_error(self):
        try:
            root = hidden_root()
        except Exception as exc:
            self.skipTest(f"Tk unavailable: {exc}")
        try:
            from projectionist.ui.widgets import ChartView
            view = ChartView(root, lambda p: 1 / 0)
            import io
            import contextlib
            with contextlib.redirect_stderr(io.StringIO()):
                view.redraw(wider(root, 300), 100)
            texts = [view.itemcget(i, "text") for i in view.find_all() if view.type(i) == "text"]
            self.assertIn("This chart couldn't be drawn", texts)
        finally:
            root.destroy()

    def test_clicks_and_tips_are_wired(self):
        try:
            root = hidden_root()
        except Exception as exc:
            self.skipTest(f"Tk unavailable: {exc}")
        try:
            from projectionist.ui.widgets import ChartView
            clicked = []
            view = ChartView(root, lambda p: C.bars(p, SAMPLE_BARS, on_click=clicked.append))
            view.redraw(500, 300)
            self.assertIn("<Button-1>", view.tag_bind("bar3"))
            self.assertIn("<Enter>", view.tag_bind("bar3"))
            # the row's hit area is part of the bar's tag, so hovering anywhere on the row works
            self.assertTrue(any("hit" in view.gettags(i) for i in view.find_withtag("bar3")))
        finally:
            root.destroy()


class WidgetTests(unittest.TestCase):
    def test_table_sorts_numbers_as_numbers(self):
        try:
            root = hidden_root()
        except Exception as exc:
            self.skipTest(f"Tk unavailable: {exc}")
        try:
            from projectionist.ui.widgets import Table
            picked = []
            t = Table(root, [("title", "Title", 200, "w"), ("n", "Films", 60, "e")], on_select=picked.append)
            t.set_rows([{"title": "b", "n": 9}, {"title": "a", "n": 10}, {"title": "c", "n": None}])
            t.sort("n")
            order = [t.rows[i]["title"] for i in t.tree.get_children()]
            self.assertEqual(order, ["b", "a", "c"])        # 9 < 10, blanks last
            t.sort("n")
            self.assertEqual([t.rows[i]["title"] for i in t.tree.get_children()][0], "c")
            t.select_first()
            root.update()
            self.assertTrue(picked)
        finally:
            root.destroy()

    def test_suggester(self):
        from projectionist.ui.widgets import suggester
        suggest = suggester(["Jackie Chan", "Chan Shen", "Robert De Niro", "Shintarō Katsu"])
        self.assertEqual(suggest("chan"), ["Chan Shen", "Jackie Chan"])     # starts-with first
        self.assertEqual(suggest("shintaro"), ["Shintarō Katsu"])
        self.assertEqual(suggest(""), [])


class AppShellTests(unittest.TestCase):
    def test_collection_loads_in_the_background(self):
        try:
            root = hidden_root()
        except Exception as exc:
            self.skipTest(f"Tk unavailable: {exc}")
        from test_projectionist import build_fixture
        from projectionist import gui
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "com.plexapp.plugins.library.db-2026-09-25")
            build_fixture(db)
            old = gui.SETTINGS_FILE, gui.SETTINGS_DIR
            gui.SETTINGS_FILE, gui.SETTINGS_DIR = os.path.join(d, "settings.json"), d
            app = None
            try:
                app = gui.App(root, db)
                app._pick_initial_db(db)
                deadline = time.time() + 30
                while app.catalog_state != "ready":
                    root.update()
                    time.sleep(0.02)
                    self.assertLess(time.time(), deadline, app.catalog_state)
                self.assertEqual(len(app.catalog.films), 5)
                self.assertIn("5 films", app.app_status_var.get())
                self.assertEqual(app.ask({"action": "info"})["films"], 5)
                # background work comes back on the UI thread
                results = []
                app.run(lambda: 6 * 7, results.append)
                app.run(lambda: 1 / 0, None, results.append)
                deadline = time.time() + 10
                while len(results) < 2:
                    root.update()
                    time.sleep(0.02)
                    self.assertLess(time.time(), deadline)
                self.assertEqual(results[0], 42)
                self.assertIn("ZeroDivisionError", results[1])
                self.assertIsNone(app.goto("No Such Tab"))
                self.assertEqual([t.title for t in app.tabs], [t.title for t in app.tabs if t.title])
            finally:
                gui.SETTINGS_FILE, gui.SETTINGS_DIR = old
                if app is not None:
                    app.shutdown()
                else:
                    root.destroy()


if __name__ == "__main__":
    unittest.main()
