"""Tests for the shared UI pieces' fixes: chart hover and clicks that leave nothing behind in Tk, the scatter's
keep_side option and hover by nearness, the waterfall's labels and sums, the timeline's axis and legend, the
network's names, the Table's double-click, a page's keyboard scrolling, the bars' track, items that can't be
clicked, the columns' axis format, the inner notebooks' style, the suggestion list's width, typed names that are
near spellings, drawing errors reaching the log, and the main window's status line, Ctrl+Tab, closing, Export
layout and responsiveness. Everything is headless - no window is ever shown."""

import json
import os
import sys
import tempfile
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from projectionist.ui import charts as C  # noqa: E402
from projectionist.ui import paint as P  # noqa: E402
from projectionist.ui import theme as T  # noqa: E402
from support import budget, wider  # noqa: E402

SAMPLE_BARS = [{"label": f"Person {i}", "value": 50 - i * 4, "key": i} for i in range(10)]


def hidden_root():
    import tkinter as tk
    from projectionist.ui import theme
    root = tk.Tk()
    root.withdraw()            # headless: never show a window
    theme.quiet_theme_changes(root)     # (Tk's first ThemeChanged may still be pending when a test closes it)
    return root


def fire(view, tag, sequence, x=0, y=0):
    """Run a canvas binding as Tk would for a pointer at (x, y) (hidden windows get no real pointer events)."""
    script = view.tag_bind(tag, sequence) if tag is not None else view.bind(sequence)
    if not script:
        raise AssertionError(f"nothing bound to {tag} {sequence}")
    view.tk.eval(script.replace("%x", str(int(round(x)))).replace("%y", str(int(round(y)))))


def commands(root) -> int:
    return len(root.tk.splitlist(root.tk.call("info", "commands")))


def texts_of(view, tag):
    return [view.itemcget(i, "text") for i in view.find_withtag(tag) if view.type(i) == "text"]


class Stub(P.Painter):
    """Draws nothing; records text boxes, circles and rectangles (text 6 px a character, lines 12 px)."""

    def __init__(self, w, h):
        super().__init__(w, h, 1.0)
        self.texts, self.circles, self.rects = [], [], []

    def text_width(self, text, font):
        return 6.0 * len(str(text))

    def line_height(self, font):
        return 12.0

    def text(self, x, y, text, font, fill=T.INK, anchor="w", tag=None):
        w = self.text_width(text, font)
        x0 = x - w / 2 if anchor in ("center", "n", "s") else x - w if anchor in ("e", "ne", "se") else x
        self.texts.append((x0, y - 6, x0 + w, y + 6, str(text), fill, tag))

    def circle(self, cx, cy, r, fill, ring=None, ring_width=2, tag=None):
        self.circles.append((cx, cy, r + (self.u(ring_width) if ring else 0), tag))

    def rect(self, x0, y0, x1, y1, fill, outline=None, width=1, tag=None):
        self.rects.append((x0, y0, x1, y1, fill, tag))

    def round_rect(self, *a, **k):
        pass

    def line(self, *a, **k):
        pass

    def polygon(self, *a, **k):
        pass


def overlaps(a, b, gap=0.0):
    return a[0] < b[2] + gap and b[0] < a[2] + gap and a[1] < b[3] + gap and b[1] < a[3] + gap


# ---------------------------------------------------------------------------------------------------------
class TkTestCase(unittest.TestCase):
    def setUp(self):
        try:
            self.root = hidden_root()
        except Exception as exc:
            self.skipTest(f"Tk unavailable: {exc}")
        from projectionist.ui import widgets
        self.widgets = widgets

    def tearDown(self):
        self.root.destroy()


class HoverAndClickTests(TkTestCase):
    def test_redraws_leave_no_tcl_commands_behind(self):
        clicked = []
        view = self.widgets.ChartView(self.root, lambda p: C.bars(p, SAMPLE_BARS, on_click=clicked.append))
        view.redraw(500, 300)
        before = commands(self.root)
        for _ in range(5):                                  # every resize redraws
            view.redraw(500, 300)
        self.assertEqual(commands(self.root), before)
        script = view.tag_bind("bar3", "<Button-1>")
        self.assertEqual(script.count("if {"), 1, script)   # one click, one callback
        fire(view, "bar3", "<Button-1>")
        self.assertEqual(clicked, [3])

    def test_hover_highlights_shows_a_tip_and_lets_go(self):
        view = self.widgets.ChartView(self.root, lambda p: C.bars(p, SAMPLE_BARS, on_click=lambda k: None))
        view.redraw(500, 300)
        bar = next(i for i in view.find_withtag("bar3") if view.type(i) in ("polygon", "rectangle")
                   and "hit" not in view.gettags(i))
        fill = view.itemcget(bar, "fill")
        x0, y0, x1, y1 = view.bbox(bar)
        x, y = (x0 + x1) / 2, (y0 + y1) / 2
        fire(view, "bar3", "<Enter>", x, y)
        self.assertTrue(view.find_withtag("tooltip"))
        self.assertIn("Person 3", [view.itemcget(i, "text") for i in view.find_withtag("tooltip")
                                   if view.type(i) == "text"])
        self.assertNotEqual(view.itemcget(bar, "fill"), fill)
        self.assertEqual(str(view.cget("cursor")), "hand2")
        fire(view, "chart", "<Leave>", x0 - 20 if x0 > 30 else x1 + 20, y)   # onto the row's hit area: stays
        self.assertTrue(view.find_withtag("tooltip"))
        fire(view, None, "<Leave>", -5, -5)                 # off the chart altogether
        self.assertFalse(view.find_withtag("tooltip"))
        self.assertEqual(view.itemcget(bar, "fill"), fill)
        self.assertEqual(str(view.cget("cursor")), "")

    def test_a_redraw_drops_bindings_it_no_longer_needs(self):
        clickable = [True]
        view = self.widgets.ChartView(self.root, lambda p: C.bars(
            p, SAMPLE_BARS, on_click=(lambda k: None) if clickable[0] else None))
        view.redraw(500, 300)
        self.assertTrue(view.tag_bind("bar3", "<Button-1>"))
        clickable[0] = False
        view.redraw(500, 300)
        self.assertFalse(view.tag_bind("bar3", "<Button-1>"))
        self.assertTrue(view.tag_bind("bar3", "<Enter>"))    # the tooltip stays
        view.clear("Nothing here")
        self.assertFalse(view.tag_bind("bar3", "<Enter>"))

    def test_a_drawing_error_goes_to_the_log(self):
        reported = []
        old = self.widgets.report_error
        self.widgets.report_error = reported.append
        try:
            view = self.widgets.ChartView(self.root, lambda p: 1 / 0)
            view.redraw(wider(self.root, 300), 120)
            view.redraw(wider(self.root, 320), 130)          # a resize: the same error isn't logged again
        finally:
            self.widgets.report_error = old
        self.assertEqual(len(reported), 1)
        self.assertIn("ZeroDivisionError", reported[0])
        shown = [view.itemcget(i, "text") for i in view.find_all() if view.type(i) == "text"]
        self.assertIn("This chart couldn't be drawn", shown)
        self.assertIn("The details are in the Export tab's log.", shown)


class ScatterTests(TkTestCase):
    def points(self):
        pts = []
        for i in range(24):         # a hair above and below IMDb, and five exactly on the line: every eighth film,
            imdb = round(5.5 + i * 0.1, 1)     # and IMDb 6.0 and 7.0, which round to your 6 and 7
            yours = float(round(imdb + (0.3 if i % 2 else -0.3))) if i % 8 else imdb
            pts.append({"x": imdb, "y": yours, "key": f"film {i}", "tip": f"Film {i}: you {yours}, IMDb {imdb}"})
        pts += [{"x": 7.3, "y": 8, "key": f"pile {i}", "tip": f"Pile {i}: you 8, IMDb 7.3"} for i in range(6)]
        pts.append({"x": 3.1, "y": 9, "key": "lone", "tip": "Lone: you 9, IMDb 3.1"})
        return pts

    def draw(self, pts, **kw):
        self.clicked = []
        view = self.widgets.ChartView(self.root, lambda p: C.scatter(p, pts, on_click=self.clicked.append, **kw))
        view.redraw(560, 320)
        return view

    @staticmethod
    def centre(view, n):
        oval = [i for i in view.find_withtag(f"pt{n}") if view.type(i) == "oval"][-1]
        x0, y0, x1, y1 = view.coords(oval)
        return (x0 + x1) / 2, (y0 + y1) / 2

    def test_keep_side_keeps_every_dot_on_its_side_of_the_line(self):
        pts = self.points()
        view = self.draw(pts, keep_side=True, jitter=0.3)
        diagonal = [view.coords(i) for i in view.find_withtag("chart") if view.type(i) == "line"
                    and abs(view.coords(i)[0] - view.coords(i)[2]) > 1 and abs(view.coords(i)[1] - view.coords(i)[3]) > 1]
        self.assertEqual(len(diagonal), 1)
        ax, ay, bx, by = diagonal[0]
        length = ((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5
        on_line = 0
        for n, pt in enumerate(pts):
            cx, cy = self.centre(view, n)
            below = ((bx - ax) * (cy - ay) - (by - ay) * (cx - ax)) / length     # negative: above the line
            if pt["y"] == pt["x"]:
                on_line += 1
                self.assertLess(abs(below), 1, pt["tip"])
            elif pt["y"] > pt["x"]:
                self.assertLess(below, 0, pt["tip"])
            else:
                self.assertGreater(below, 0, pt["tip"])
        self.assertEqual(on_line, 5)
        # films at the very same spot are spread out, not stacked
        pile = {tuple(round(v) for v in self.centre(view, n)) for n, pt in enumerate(pts) if pt["key"].startswith("pile")}
        self.assertEqual(len(pile), 6)

    def test_the_default_jitter_is_unchanged(self):
        pts = self.points()
        a, b = Stub(560, 320), Stub(560, 320)
        C.scatter(a, pts)
        C.scatter(b, pts, keep_side=False)
        self.assertEqual(a.circles, b.circles)

    def test_every_dot_can_be_hovered_and_clicked(self):
        pts = self.points()
        view = self.draw(pts, keep_side=True)
        ui = P.Interaction.of(view, create=False)
        # a dot on its own: its tip, and a click opens it
        lone = next(n for n, pt in enumerate(pts) if pt["key"] == "lone")
        x, y = self.centre(view, lone)
        self.assertEqual(len(ui.spots_near(x, y)), 1)
        fire(view, "chart", "<Motion>", x, y)
        self.assertEqual(texts_of(view, "tooltip"), ["Lone: you 9, IMDb 3.1"])
        fire(view, f"pt{lone}", "<Button-1>", x, y)
        self.assertEqual(self.clicked, ["lone"])
        fire(view, None, "<Leave>", -5, -5)
        self.assertFalse(view.find_withtag("tooltip"))
        # a pile: the tip lists the films there, and a click offers every one of them
        first = next(n for n, pt in enumerate(pts) if pt["key"] == "pile 0")
        x, y = self.centre(view, first + 3)                  # (the middle of the pile)
        fire(view, "chart", "<Motion>", x, y)
        tip = texts_of(view, "tooltip")
        self.assertRegex(tip[0], r"^\d+ films here$")
        self.assertIn("Click to choose one", tip)
        chosen = []
        ui._choose = lambda near: chosen.append({s[5] for s in near})        # (never pop a menu up in a test)
        fire(view, f"pt{first}", "<Button-1>", x, y)
        self.assertTrue({f"pile {i}" for i in range(2, 5)} <= chosen[0], chosen)
        # every dot is under the pointer somewhere, even one drawn over by others
        reachable = set()
        for n in range(len(pts)):
            reachable.update(s[5] for s in ui.spots_near(*self.centre(view, n)))
        self.assertEqual(reachable, {pt["key"] for pt in pts})
        # and a click from anywhere on a dot at least opens that dot (tests' clicks come from (0, 0))
        self.clicked.clear()
        fire(view, "pt0", "<Button-1>")
        self.assertEqual(self.clicked, ["film 0"])

    def test_a_hidden_chart_never_pops_its_menu_up(self):
        import tkinter as tk
        pts = self.points()
        view = self.draw(pts, keep_side=True)
        popped = []
        real = tk.Menu.tk_popup
        tk.Menu.tk_popup = lambda menu, *a, **k: popped.append(a)       # (in case: never show one in a test)
        try:
            first = next(n for n, pt in enumerate(pts) if pt["key"] == "pile 0")
            x, y = self.centre(view, first + 3)
            fire(view, f"pt{first}", "<Button-1>", x, y)                 # a pile: normally a menu
        finally:
            tk.Menu.tk_popup = real
        self.assertEqual(popped, [])
        self.assertEqual(self.clicked, [])


class WaterfallTests(TkTestCase):
    STEPS = [{"label": "IMDb 8.8", "value": 1.5}, {"label": "Audience 96%", "value": 0.487},
             {"label": "Critics 92%", "value": 0.213}, {"label": "Quentin Tarantino (director)", "value": 0.07},
             {"label": "Uma Thurman", "value": 0.068}, {"label": "Drama", "value": -0.061}]

    def draw(self, base, steps, total, rest, w=458, h=290, **kw):
        view = self.widgets.ChartView(self.root, lambda p: C.waterfall(
            p, base, steps, total, rest=rest, base_label="Starting point", total_label="Predicted for you", **kw))
        view.redraw(w, h)
        rows = []
        n = 0
        while view.find_withtag(f"wf{n}"):
            rows.append(texts_of(view, f"wf{n}"))
            n += 1
        return view, rows

    def test_the_bold_total_label_isnt_cut_short(self):
        # 'Predicted for you' as the longest label: measured in bold, so it fits
        _view, rows = self.draw(6.747, self.STEPS[:1] + self.STEPS[5:], 8.4, 0.214)
        self.assertEqual(rows[-1][0], "Predicted for you")
        self.assertEqual(rows[0][0], "Starting point")

    def test_the_printed_numbers_add_up(self):
        rest = 0.274
        total = 6.747 + sum(s["value"] for s in self.STEPS) + rest              # 9.298: printed 9.3
        view, rows = self.draw(6.747, self.STEPS, total, rest)
        printed = [float(r[1].replace("+", "")) for r in rows]
        self.assertEqual(rows[-2][0], "Everything else")
        self.assertAlmostEqual(sum(printed[:-1]), printed[-1], places=6)
        # ...and the tooltip says the rounding went there
        fire(view, f"wf{len(rows) - 2}", "<Enter>", 10, 10)
        self.assertIn("rounding", " ".join(texts_of(view, "tooltip")))

    def test_numbers_that_dont_reach_a_capped_total_are_left_alone(self):
        steps = [{"label": "IMDb 9.3", "value": 3.1}]
        _view, rows = self.draw(7.2, steps, 10.0, 0.4)                          # 10.7, capped at 10
        self.assertEqual(rows[-2], ["Everything else", "+0.40"])

    def test_a_start_note_goes_in_its_tooltip(self):
        view, _rows = self.draw(6.747, self.STEPS[:2], 8.7, 0.0, base_note="Where the model starts")
        fire(view, "wf0", "<Enter>", 10, 10)
        self.assertIn("Where the model starts", texts_of(view, "tooltip"))


class TimelineTests(unittest.TestCase):
    def test_axis_labels_never_run_together(self):
        for w in (300, 378, 423, 437, 560, 721):
            for credits_start in range(7680, 7740, 3):
                p = Stub(w, 200)
                C.timeline(p, credits_start + 400.5, [{"start_sec": credits_start, "end_sec": credits_start + 380}],
                           [], credits_start)
                axis = sorted(t for t in p.texts if t[5] == T.MUTED and ":" in t[4])
                for a, b in zip(axis, axis[1:]):
                    self.assertLessEqual(a[2] + 6, b[0], (w, credits_start, a[4], b[4]))
                self.assertTrue(all(t[0] >= 0 and t[2] <= w for t in axis))

    def test_the_legend_only_names_marks_that_are_drawn(self):
        def legend(stretches):
            p = Stub(560, 200)
            C.timeline(p, 6500, stretches, [], 6100, lead_in=60)
            return [t[4] for t in p.texts], [r for r in p.rects if (r[5] or "").startswith("cr")]
        before = {"start_sec": 5862, "end_sec": 5944, "counted": False}           # ends before the window
        inside = {"start_sec": 6200, "end_sec": 6260, "counted": False}
        credits = {"start_sec": 6100, "end_sec": 6180}
        shown, marks = legend([before, credits])
        self.assertNotIn("Not really credits", shown)
        self.assertEqual(len(marks), 1)                                            # and it isn't drawn either
        shown, marks = legend([before, credits, inside])
        self.assertIn("Not really credits", shown)
        self.assertEqual(len(marks), 2)


class TooltipTests(TkTestCase):
    """A tooltip is drawn on its chart, so every line wraps to fit it and the box stays inside it - nothing is cut
    off at an edge, however narrow the chart."""
    LONG = "Marked as credits - but the film carries on after it (on-screen text?)"

    def test_lines_wrap_to_fit_and_lose_nothing(self):
        p = Stub(200, 150)                                   # 6 px a character, 12 px a line
        room = 200 - 4 - 12
        lines = P.tip_lines(p, f"{self.LONG}\n1:52:10 - 1:53:00", 200, 150)
        self.assertTrue(all(p.text_width(t, f) <= room for t, f in lines), lines)
        heading = [t for t, f in lines if f == p.font(9, "bold")]
        self.assertGreater(len(heading), 1)                  # the heading wraps, and stays bold
        self.assertEqual(" ".join(heading).split(), self.LONG.split())
        self.assertEqual(lines[-1], ("1:52:10 - 1:53:00", p.font(9)))
        # a line on its own isn't a heading, and one that fits is left as it is
        self.assertEqual(P.tip_lines(p, "  Short", 200, 150), [("  Short", p.font(9))])
        # a word too long for any line is broken up, not cut short
        word = "x" * 50
        broken = P.tip_lines(p, word, 200, 150)
        self.assertEqual("".join(t for t, _ in broken), word)
        self.assertTrue(all(p.text_width(t, f) <= room for t, f in broken))

    def test_a_tip_taller_than_its_chart_ends_in_an_ellipsis(self):
        p = Stub(200, 80)
        lines = P.tip_lines(p, "\n".join(f"Line {i}" for i in range(20)), 200, 80)
        self.assertEqual(len(lines), (80 - 4 - 12) // 12)
        self.assertTrue(lines[-1][0].endswith("…"))

    def test_the_box_stays_inside_the_chart_wherever_the_pointer_is(self):
        for w, h in ((343, 170), (200, 120), (600, 300)):
            p = Stub(w, h)
            lines = P.tip_lines(p, f"{self.LONG}\n1:52:10 - 1:53:00", w, h)
            for x in range(0, w + 1, 7):
                for y in range(0, h + 1, 9):
                    (x0, y0, x1, y1), placed = P.tip_layout(p, x, y, lines, w, h)
                    self.assertTrue(2 <= x0 and x1 <= w - 2 and 2 <= y0 and y1 <= h - 2, (w, h, x, y))
                    for lx, ly, text, font in placed:
                        self.assertLessEqual(lx + p.text_width(text, font), x1)
                        self.assertLessEqual(ly + 12, y1)
            # below and right of the pointer when there's room, as before
            (x0, y0, _x1, _y1), _ = P.tip_layout(p, 5, 5, [("Tip", p.font(9))], w, h)
            self.assertEqual((x0, y0), (19, 19))

    def test_the_credits_timelines_tips_fit_the_smallest_window(self):
        # At the 900x620 minimum the Credits tab's timeline is about 343 px wide: its "marked as credits" tip
        # used to run off the right edge
        stretches = [{"start_sec": 6100, "end_sec": 6180}, {"start_sec": 6200, "end_sec": 6260, "counted": False}]
        scenes = [{"start_sec": 6300, "end_sec": 6330, "verdict": "Maybe", "kind": "Mid-credits scene",
                   "why": "very short - could be a title card or a caption rather than a scene"}]
        view = self.widgets.ChartView(self.root, lambda p: C.timeline(p, 6500, stretches, scenes, 6100, lead_in=60))
        width, height = 343, 170
        view.redraw(width, height)
        ui = P.Interaction.of(view, create=False)
        self.assertIn(self.LONG, [text.split("\n")[0] for text, _ in ui.tips.values()])
        for tag, (text, _) in list(ui.tips.items()):
            for x in (5, width / 2, width - 5):
                view.tk.call(ui.command, "enter", tag, x, 20)
                items = view.find_withtag("tooltip")
                boxes = [view.bbox(i) for i in items]
                self.assertTrue(boxes, tag)
                self.assertGreaterEqual(min(b[0] for b in boxes), 0, (tag, x))
                self.assertLessEqual(max(b[2] for b in boxes), width, (tag, x))
                self.assertLessEqual(max(b[3] for b in boxes), height, (tag, x))
                shown = " ".join(view.itemcget(i, "text") for i in items if view.type(i) == "text")
                self.assertEqual(shown.split(), text.split(), tag)      # every word, nothing cut off
                view.tk.call(ui.command, "out", "", 0, 0)

    def test_a_png_preview_can_show_a_tip(self):
        try:
            from PIL import Image, ImageChops
        except ImportError:
            self.skipTest("Pillow isn't installed")
        p = P.PilPainter(343, 170)
        P.draw_tip(p, 330, 20, f"{self.LONG}\n1:52:10 - 1:53:00")
        img = p.image.resize((343, 170))
        diff = ImageChops.difference(img, Image.new("RGB", img.size, T.SURFACE)).convert("L")
        drawn = diff.point(lambda v: 255 if v > 24 else 0).getbbox()          # (ignoring faint resampling)
        self.assertIsNotNone(drawn)
        self.assertTrue(drawn[0] >= 1 and drawn[2] <= 342, drawn)


class NetworkTests(unittest.TestCase):
    def test_names_dont_land_on_each_other_or_on_dots(self):
        nodes = [{"id": "c", "name": "Centre Person", "weight": 40, "center": True}]
        nodes += [{"id": str(i), "name": f"Somebody Longname {i}", "weight": 5 + i} for i in range(11)]
        edges = [{"a": "c", "b": str(i), "weight": 1 + i % 3} for i in range(11)]
        huddle = {n["id"]: (0.3 + 0.01 * (i % 3), 0.3 + 0.01 * (i // 3)) for i, n in enumerate(nodes)}
        huddle["c"] = (0.5, 0.5)
        for w in (465, 692):
            p = Stub(w, 400)
            C.network(p, nodes, edges, positions=huddle, max_links=None)
            names = [t for t in p.texts if (t[6] or "").startswith("node")]
            self.assertEqual(len(names), len(nodes))
            for i, a in enumerate(names):
                for b in names[i + 1:]:
                    self.assertFalse(overlaps(a, b), (w, a[4], b[4]))
                for cx, cy, r, tag in p.circles:
                    if tag != a[6]:
                        self.assertFalse(overlaps(a, (cx - r, cy - r, cx + r, cy + r)), (w, a[4], tag))
            self.assertTrue(all(0 <= t[0] and t[2] <= w and t[3] <= 400 for t in names))

    def test_a_chart_too_small_for_everyone_still_draws_at_once(self):
        nodes = [{"id": "c", "name": "Centre Person", "weight": 40, "center": True}]
        nodes += [{"id": str(i), "name": f"Somebody Longname {i}", "weight": 5 + i} for i in range(11)]
        edges = [{"a": "c", "b": str(i), "weight": 1} for i in range(11)]
        huddle = {n["id"]: (0.3, 0.3) for n in nodes}
        for w, h in ((160, 100), (300, 160)):
            p = Stub(w, h)
            started = time.perf_counter()
            C.network(p, nodes, edges, positions=huddle, max_links=None)
            self.assertLess(time.perf_counter() - started, budget(2.0), (w, h))
            self.assertEqual(len([t for t in p.texts if (t[6] or "").startswith("node")]), len(nodes))

    def test_dots_are_all_drawn_before_the_names(self):
        order = []

        class Order(Stub):
            def circle(self, *a, **k):
                order.append("dot")

            def text(self, *a, **k):
                order.append("name")
        nodes = [{"id": str(i), "name": f"P{i}", "weight": 3, "center": i == 0} for i in range(5)]
        C.network(Order(400, 300), nodes, [{"a": "0", "b": str(i), "weight": 1} for i in range(1, 5)])
        self.assertEqual(order, ["dot"] * 5 + ["name"] * 5)


class TableTests(TkTestCase):
    class Ev:
        def __init__(self, x, y):
            self.x, self.y = x, y

    def test_double_clicking_a_heading_opens_nothing(self):
        opened = []
        t = self.widgets.Table(self.root, [("title", "Title", 200, "w"), ("year", "Year", 60, "e")],
                               on_open=opened.append)
        t.set_rows([{"title": "A", "year": 1990}, {"title": "B", "year": 1991}, {"title": "C", "year": 1992}])
        t.tree.selection_set("0")
        region, row = ["heading"], [""]
        t.tree.identify_region = lambda x, y: region[0]
        t.tree.identify_row = lambda y: row[0]
        t._double_clicked(self.Ev(20, 5))                  # two quick clicks on 'Year' to flip the sort
        self.assertEqual(opened, [])
        region[0] = "nothing"                              # the empty space under the rows
        t._double_clicked(self.Ev(20, 200))
        self.assertEqual(opened, [])
        region[0], row[0] = "cell", "2"                    # a row: that row, whatever was selected
        t._double_clicked(self.Ev(20, 60))
        self.assertEqual(opened, [{"title": "C", "year": 1992}])
        self.assertEqual(t.tree.selection(), ("2",))
        t._opened()                                        # Enter opens the selected row
        self.assertEqual(opened[-1]["title"], "C")
        self.assertIn("_double_clicked", t.tree.bind("<Double-1>"))


class ScrollFrameTests(TkTestCase):
    class Ev:
        def __init__(self, widget):
            self.widget = widget

    def page(self):
        from tkinter import ttk
        sf = self.widgets.ScrollFrame(self.root)
        sf.pack(fill="both", expand=True)
        label = ttk.Label(sf.inner, text="Some text on a card")
        label.pack()
        entry = ttk.Entry(sf.inner)
        entry.pack()
        table = self.widgets.Table(sf.inner, [("title", "Title", 200, "w")])
        table.pack()
        ttk.Frame(sf.inner, height=2000, width=100).pack()             # taller than any window
        self.root.update()
        focused = []
        sf.canvas.focus_set = lambda: focused.append("page")         # (a hidden window can't take the focus)
        sf.canvas.winfo_viewable = lambda: True
        return sf, label, entry, table, focused

    def test_the_keys_scroll_the_page(self):
        sf, *_ = self.page()
        for key in ("<Prior>", "<Next>", "<Up>", "<Down>", "<Home>", "<End>"):
            self.assertTrue(sf.canvas.bind(key), key)                 # on the page itself...
            self.assertFalse(self.root.bind_all(key), key)            # ...not everywhere: a box keeps its keys
        self.assertEqual(str(sf.canvas.cget("takefocus")), "1")       # Tab reaches it too
        self.assertFalse(sf.fits())
        self.assertEqual(sf._key("moveto", 1), "break")               # End
        self.assertEqual(sf.canvas.yview()[1], 1.0)
        top = sf.canvas.yview()[0]
        sf._key("units", -1)                                          # Up
        self.assertLess(sf.canvas.yview()[0], top)
        sf._key("moveto", 0)                                          # Home
        self.assertEqual(sf.canvas.yview()[0], 0.0)
        sf.canvas.configure(yscrollincrement=20)     # (a hidden window is 1 px tall: a tenth of it rounds to 0)
        sf._key("units", 1)                                           # Down
        self.assertGreater(sf.canvas.yview()[0], 0.0)

    def test_a_click_in_the_page_gives_it_the_keys(self):
        sf, label, entry, table, focused = self.page()
        sf._clicked(self.Ev(label))                                   # somewhere on a card
        self.assertEqual(focused, ["page"])
        sf._clicked(self.Ev(entry))                                   # a box you type in keeps the focus
        sf._clicked(self.Ev(table.tree))                              # ...and so does a list
        sf._clicked(self.Ev(self.root))                               # outside the page
        sf._clicked(self.Ev(".some.tk.only.widget"))
        self.assertEqual(focused, ["page"])
        sf.canvas.winfo_viewable = lambda: False                      # a chart click that went to another tab
        sf._clicked(self.Ev(label))
        self.assertEqual(focused, ["page"])

    def test_ctrl_page_up_and_down_are_left_to_the_main_tabs(self):
        sf, *_ = self.page()
        key = type("Key", (), {})
        ctrl, plain = key(), key()
        ctrl.state, plain.state = 0x0004, 0
        self.assertIsNone(sf._key("pages", 1, ctrl))                 # the window's Ctrl+Page Down: next tab
        self.assertIsNone(sf._key("pages", -1, ctrl))
        self.assertEqual(sf._key("pages", 1, plain), "break")         # Page Down on its own scrolls the page
        self.assertEqual(sf._key("moveto", 1, ctrl), "break")         # (Ctrl+End still goes to the bottom)

    def test_a_page_that_fits_ignores_the_keys(self):
        sf, label, _entry, _table, focused = self.page()
        sf.fits = lambda: True
        before = sf.canvas.yview()
        self.assertIsNone(sf._key("moveto", 1))
        self.assertEqual(sf.canvas.yview(), before)
        sf._clicked(self.Ev(label))
        self.assertEqual(focused, [])

    def test_a_tab_not_yet_shown_is_laid_out_once(self):
        """On a notebook page that isn't on show the canvas is never sized: the inner page has a width of its own
        from the start, so labels that wrap to their card don't shrink it step by step (the app's start took
        hundreds of layouts and half a second before); once shown, the page is as wide as its canvas."""
        from tkinter import ttk
        nb = ttk.Notebook(self.root)
        nb.place(x=0, y=0, width=900, height=500)
        nb.add(ttk.Frame(nb), text="On show")
        self.root.update()
        page = ttk.Frame(nb)
        nb.add(page, text="Not yet")
        sf = self.widgets.ScrollFrame(page)
        sf.pack(fill="both", expand=True)
        cards = []
        for n in range(8):
            card = ttk.Frame(sf.inner, padding=12)
            card.pack(fill="x", padx=16, pady=6)
            label = ttk.Label(card, text="A hint that wraps to the card it's on, as the cards on the tabs do. " * 3,
                              wraplength=500)
            label.pack(anchor="w")
            card.bind("<Configure>", lambda e, lb=label: lb.configure(wraplength=max(e.width - 30, 120)), add="+")
            cards.append(card)
        widths = []
        sf.inner.bind("<Configure>", lambda e: widths.append(e.width), add="+")
        self.root.update_idletasks()
        self.assertLessEqual(len(set(widths)), 2, widths)           # a width or two, not a shrinking series
        self.assertEqual(sf.inner.winfo_width(), sf.canvas.winfo_width() if sf.canvas.winfo_width() > 50
                         else sf.canvas.winfo_reqwidth())
        # shown (a hidden window may never lay the page out): then as wide as its canvas
        sf.canvas.event_generate("<Configure>", width=700, height=400)
        self.root.update_idletasks()
        self.assertEqual(int(float(sf.canvas.itemcget(sf._window, "width"))), 700)


class LinkKeyboardTests(TkTestCase):
    """Every link can be reached with Tab and opened with Enter or Space; the focused one is underlined, and a
    scrolling page shows it."""

    def press(self, widget, sequence):
        """Run a widget's binding as Tk would (a hidden window never has the keyboard focus); in a loop, where the
        binding's 'break' is allowed."""
        import re
        script = widget.bind(sequence)
        self.assertTrue(script, f"nothing bound to {sequence}")
        script = re.sub(r"%[#bfhkstwxyENTXYD]", "0", script.replace("%W", str(widget)).replace("%A", "{}"))
        widget.tk.eval("foreach __once {1} {\n%s\n}" % script.replace("%K", "key"))

    def page(self, count=40):
        import tkinter as tk
        holder = tk.Frame(self.root)
        holder.place(x=0, y=0, width=400, height=300)        # laid out although the window is hidden
        sf = self.widgets.ScrollFrame(holder)
        sf.pack(fill="both", expand=True)
        opened = []
        links = [self.widgets.LinkLabel(sf.inner, f"Link {i}", lambda i=i: opened.append(i)) for i in range(count)]
        for link in links:
            link.pack(anchor="w")
        self.root.update()
        return sf, links, opened

    def underlined(self, widget) -> bool:
        from tkinter import font as tkfont
        spec = widget.cget("font")
        return bool(str(spec)) and bool(int(tkfont.Font(root=self.root, font=spec).actual("underline")))

    def test_tab_stops_on_a_link_and_enter_or_space_opens_it(self):
        _sf, links, opened = self.page()
        link = links[3]
        self.assertEqual(str(link.cget("takefocus")), "1")
        for key in ("<Return>", "<KP_Enter>", "<space>"):
            self.press(link, key)
        self.assertEqual(opened, [3, 3, 3])
        self.press(link, "<Button-1>")                               # the mouse, as before
        self.assertEqual(opened, [3, 3, 3, 3])
        self.assertEqual(str(self.widgets.LinkLabel(self.root, "x", print, takefocus=0).cget("takefocus")), "0")

    def test_the_focused_link_is_underlined(self):
        from tkinter import font as tkfont
        _sf, links, _ = self.page()
        link = links[0]
        self.assertEqual(str(link.cget("font")), "")                 # its style's font
        link.event_generate("<FocusIn>")
        self.assertTrue(self.underlined(link))
        self.assertFalse(self.underlined(links[1]))
        link.event_generate("<FocusOut>")
        self.assertEqual(str(link.cget("font")), "")                 # back to the style's
        # a link with a font of its own keeps it, underlined while focused
        big = self.widgets.LinkLabel(self.root, "Big", print, font=(T.FAMILY, 14, "bold"))
        big.event_generate("<FocusIn>")
        actual = tkfont.nametofont(str(big.cget("font")), root=self.root).actual()
        self.assertEqual((actual["size"], actual["weight"], actual["underline"]), (14, "bold", 1))
        big.event_generate("<FocusOut>")
        self.assertFalse(self.underlined(big))
        self.assertEqual(tkfont.Font(root=self.root, font=big.cget("font")).actual("size"), 14)
        # a link that opened a new page is gone by the time its focus leaves: nothing breaks
        link.event_generate("<FocusIn>")
        link.destroy()
        self.widgets.underline(link, False)

    def test_tab_scrolls_the_page_to_the_focused_link(self):
        sf, links, _ = self.page()

        def shows(w):
            top = sf.canvas.canvasy(0)
            return top <= w.winfo_y() and w.winfo_y() + w.winfo_height() <= top + sf.canvas.winfo_height()

        self.assertFalse(shows(links[35]))
        links[35].event_generate("<FocusIn>")                        # Tab down to it
        self.assertTrue(shows(links[35]))
        before = sf.canvas.yview()
        links[34].event_generate("<FocusIn>")                        # one already in view: the page stays put
        self.assertEqual(sf.canvas.yview(), before)
        links[1].event_generate("<FocusIn>")                         # Shift+Tab (or Tab round) back to the top
        self.assertTrue(shows(links[1]))

    def test_the_page_keys_still_scroll_it_while_a_link_has_the_focus(self):
        sf, links, _ = self.page()
        self.press(links[0], "<End>")
        self.assertEqual(sf.canvas.yview()[1], 1.0)
        self.press(links[0], "<Home>")
        self.assertEqual(sf.canvas.yview()[0], 0.0)

    def test_a_clickable_row_can_be_made_keyboard_reachable_too(self):
        from tkinter import ttk
        box = ttk.Frame(self.root)
        head, note = ttk.Label(box, text="Missing file"), ttk.Label(box, text="The file isn't there any more.")
        opened = []
        self.widgets.keyboard_link(box, lambda: opened.append("row"), marks=[head])
        self.assertEqual(str(box.cget("takefocus")), "1")
        self.press(box, "<Return>")
        self.assertEqual(opened, ["row"])
        box.event_generate("<FocusIn>")
        self.assertTrue(self.underlined(head))
        self.assertFalse(self.underlined(note))
        box.event_generate("<FocusOut>")
        self.assertFalse(self.underlined(head))


class ChartOptionTests(TkTestCase):
    def test_bars_can_show_a_track_to_100_percent(self):
        items = [{"label": "Movies", "value": 0.25, "key": "m"}, {"label": "Asia", "value": 0.75, "key": "a"}]
        view = self.widgets.ChartView(self.root, lambda p: C.bars(p, items, value_fmt=C.fmt_pct, max_value=1,
                                                                  track=True, on_click=lambda k: None))
        view.redraw(500, 120)
        for n, share in enumerate((0.25, 0.75)):
            marks = [i for i in view.find_withtag(f"bar{n}") if view.type(i) in ("polygon", "rectangle")]
            track = [i for i in marks if view.itemcget(i, "fill") == T.NEUTRAL]
            bar = [i for i in marks if "hit" not in view.gettags(i)]
            self.assertEqual((len(track), len(bar)), (1, 1))
            self.assertAlmostEqual((view.bbox(bar[0])[2] - view.bbox(bar[0])[0]) /
                                   (view.bbox(track[0])[2] - view.bbox(track[0])[0]), share, delta=0.02)
            self.assertIn("hit", view.gettags(track[0]))            # hovering the track is hovering the row
        view.show(lambda p: C.bars(p, items, value_fmt=C.fmt_pct, max_value=1))       # off unless asked for
        view.redraw(500, 120)
        self.assertFalse([i for i in view.find_all() if view.type(i) in ("polygon", "rectangle")
                          and view.itemcget(i, "fill") == T.NEUTRAL])

    def test_an_item_that_isnt_clickable_gets_no_click_or_hand(self):
        clicked = []
        items = [{"label": "Seen them all", "value": 5, "key": 0, "clickable": False},
                 {"label": "Some to see", "value": 3, "key": 1}]
        view = self.widgets.ChartView(self.root, lambda p: C.diverging(p, items, on_click=clicked.append))
        view.redraw(500, 120)
        self.assertFalse(view.tag_bind("div0", "<Button-1>"))
        self.assertTrue(view.tag_bind("div1", "<Button-1>"))
        fire(view, "div0", "<Enter>", 10, 10)
        self.assertTrue(view.find_withtag("tooltip"))               # the tip still says why
        self.assertEqual(str(view.cget("cursor")), "")
        fire(view, "div1", "<Enter>", 10, 40)
        self.assertEqual(str(view.cget("cursor")), "hand2")
        fire(view, "div1", "<Button-1>", 10, 40)
        self.assertEqual(clicked, [1])

    def test_columns_can_format_their_axis(self):
        items = [{"label": "1990s", "value": 12.5}, {"label": "2000s", "value": 31.0}]
        p = Stub(400, 200)
        C.columns(p, items, value_fmt=lambda v: f"{v:.0f}%", tick_fmt=lambda v: f"{v:.0f}%")
        ticks = [t[4] for t in p.texts if t[5] == T.MUTED and t[4] not in ("1990s", "2000s")]
        self.assertEqual(ticks, ["0%", "10%", "20%", "30%", "40%"])
        p = Stub(400, 200)
        C.columns(p, [{"label": "a", "value": 2500}])                      # the default is unchanged
        self.assertIn("1K", [t[4] for t in p.texts])

    def test_columns_of_counts_count_in_whole_numbers(self):
        """A handful of films (a new collection, a quiet month): the axis goes 0 1 2 3, never half a film.
        Anything with fractions in it keeps its own steps."""
        def axis(p, labels):
            return [t[4] for t in p.texts if t[5] == T.MUTED and t[4] not in labels]
        p = Stub(400, 200)
        C.columns(p, [{"label": "1990s", "value": 1}, {"label": "2000s", "value": 3}])
        self.assertEqual(axis(p, ("1990s", "2000s")), ["0", "1", "2", "3"])
        p = Stub(400, 200)
        C.columns(p, [{"label": "a", "value": 1}])
        self.assertEqual(axis(p, ("a",)), ["0", "1"])
        p = Stub(400, 200)
        C.columns(p, [{"label": "a", "value": 0.5}, {"label": "b", "value": 1.5}], value_fmt=str, tick_fmt=str)
        self.assertEqual(axis(p, ("a", "b")), ["0.0", "0.5", "1.0", "1.5"])
        p = Stub(400, 200)
        C.columns(p, [{"label": "a", "value": 695}])                      # (a big count: as before)
        self.assertEqual(axis(p, ("a",)), ["0", "200", "400", "600", "800"])

    def test_the_number_over_the_tallest_column_is_never_cut_off(self):
        """When the tallest column reaches the top line (1 film, 200 films), its number goes above the plot, and
        there's room for it there - in a taller font than Windows' own too (Linux's)."""
        class Tall(Stub):
            def line_height(self, font):
                return 20.0

            def text(self, x, y, text, font, fill=T.INK, anchor="w", tag=None):
                super().text(x, y, text, font, fill, anchor, tag)
                self.anchored.append((str(text), y, anchor))

        for value in (1, 200):
            p = Tall(400, 200)
            p.anchored = []
            C.columns(p, [{"label": "a", "value": value}])
            over = [y for text, y, anchor in p.anchored if anchor == "s" and text == str(value)]
            self.assertEqual(len(over), 1, p.anchored)
            self.assertGreaterEqual(over[0] - 20.0, 0, value)              # (its top is inside the chart)

    def test_inner_notebooks_have_their_own_smaller_style(self):
        from tkinter import ttk
        T.apply_styles(self.root)
        style = ttk.Style(self.root)
        self.assertIn("9", str(style.lookup("Inner.TNotebook.Tab", "font")))    # smaller than the main tabs (10)
        self.assertNotIn("9", str(style.lookup("TNotebook.Tab", "font")))
        self.assertEqual(style.lookup("Inner.TNotebook", "background"), T.PAGE)


class SearchBoxTests(TkTestCase):
    def test_the_list_is_as_wide_as_its_longest_suggestion(self):
        from tkinter import font as tkfont
        box = self.widgets.SearchBox(self.root, lambda text: [])
        s = T.scale(self.root)
        self.assertEqual(box.popup_width(["Ann", "Bo"]), int(220 * s))          # short ones: the usual width
        label = "John Smith (7 films, e.g. The Lighthouse Keepers and the Long Winter)"
        wide = tkfont.Font(root=self.root, font=box.LIST_FONT).measure(label)
        self.assertGreater(wide, 220 * s)
        self.assertGreaterEqual(box.popup_width(["Ann", label]), min(wide, int(480 * s)))
        self.assertLessEqual(box.popup_width([label * 3]), int(480 * s))       # but never huge


# ---------------------------------------------------------------------------------------------------------
class NameGuessTests(unittest.TestCase):
    def test_a_typo_of_a_shared_name_lands_on_the_better_known_one(self):
        from projectionist.catalog import _best

        class P:
            def __init__(self, name, films):
                self.name, self.films = name, films
        rare, known = P("Michael Moore", 1), P("Michael Moore", 9)
        other = P("Michael Morse", 30)
        index = {"michael moore": [rare, known], "michael morse": [other]}
        best, others, how = _best("Micheal Moore", index, lambda p: p.films, lambda p: p.name)
        self.assertEqual(how, "guess")
        self.assertIs(best, known)                    # the closest spelling first, and under it the most films
        self.assertEqual(others[0], rare)
        self.assertIn(other, others)


class ResponsivenessTests(unittest.TestCase):
    def test_the_window_gets_its_turn_quickly(self):
        from projectionist import gui
        old = sys.getswitchinterval()
        try:
            restore = gui._share_time_with_the_window()
            try:
                self.assertLess(sys.getswitchinterval(), 0.001)      # under Windows' 1 ms: "ask for a turn at once"
            finally:
                restore()
        finally:
            sys.setswitchinterval(old)


# ---------------------------------------------------------------------------------------------------------
class AppTests(unittest.TestCase):
    """The main window (built on a hidden root, with no database: the timer that looks for one is stopped)."""

    def setUp(self):
        try:
            self.root = hidden_root()
        except Exception as exc:
            self.skipTest(f"Tk unavailable: {exc}")
        from projectionist import gui
        self.gui = gui
        self.dir = tempfile.TemporaryDirectory()
        self.old = gui.SETTINGS_FILE, gui.SETTINGS_DIR
        gui.SETTINGS_FILE, gui.SETTINGS_DIR = os.path.join(self.dir.name, "settings.json"), self.dir.name

    def tearDown(self):
        try:
            if getattr(self, "app", None) is not None:
                self.app.shutdown()             # (before the settings file is put back: closing may save to it)
            else:
                self.root.destroy()
        except Exception:
            pass
        self.gui.SETTINGS_FILE, self.gui.SETTINGS_DIR = self.old
        self.dir.cleanup()

    def make_app(self, geometry=None):
        if geometry:
            with open(self.gui.SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump({"window_geometry": geometry}, f)
        self.app = self.gui.App(self.root, None)
        self.root.after_cancel(self.app._timers.pop("pick"))
        return self.app

    def pump(self, until, timeout=10):
        deadline = time.time() + timeout
        while not until():
            self.root.update()
            time.sleep(0.01)
            self.assertLess(time.time(), deadline)

    def test_run_clears_its_status_when_the_work_ends(self):
        app = self.make_app()
        status = app.app_status_var.get
        done = []
        app.run(lambda: 1, done.append, status="Working...")
        self.assertEqual(status(), "Working...")
        self.pump(lambda: done)
        self.pump(lambda: status() == "")
        # ...unless done() says something itself
        app.run(lambda: 2, lambda r: app.set_status(f"Found {r}"), status="Looking...")
        self.pump(lambda: status() == "Found 2")
        # ...or a newer job has put its own status up meanwhile
        go = threading.Event()
        finished = []
        app.run(lambda: go.wait(5), finished.append, status="Searching...")
        app.run(lambda: go.wait(5), finished.append, status="Searching...")
        go.set()
        self.pump(lambda: len(finished) == 2)
        self.root.update()
        self.assertEqual(status(), "")
        go.clear()
        app.run(lambda: go.wait(5), finished.append, status="Slow...")
        app.set_status("Something else")
        go.set()
        self.pump(lambda: len(finished) == 3)
        self.root.update()
        self.assertEqual(status(), "Something else")
        # a failure with no handler of its own says so, and that stays
        app.run(lambda: 1 / 0, None, None, status="Trying...")
        self.pump(lambda: status().startswith("Something went wrong"))
        self.root.update()
        self.assertIn("ZeroDivisionError", status())

    def test_ctrl_tab_switches_tabs_from_anywhere(self):
        app = self.make_app()
        self.assertIn("TLCycleTab", self.root.bind("<Control-Key-Tab>"))
        self.assertTrue(app.tabs)

    def test_closing_hides_the_window_first(self):
        app = self.make_app()
        calls = []
        real = self.root.withdraw
        self.root.withdraw = lambda: (calls.append("withdraw"), real())
        real_destroy = self.root.destroy
        self.root.destroy = lambda: (calls.append("destroy"), real_destroy())
        app.shutdown()
        self.app = None
        self.assertEqual(calls, ["withdraw", "destroy"])

    def test_chart_errors_are_logged_in_the_app(self):
        from projectionist.ui import widgets
        app = self.make_app()
        self.assertEqual(widgets.report_error, app._log)
        view = widgets.ChartView(self.root, lambda p: 1 / 0)
        view.redraw(300, 120)
        self.assertIn("ZeroDivisionError", app.log.get("1.0", "end"))

    def test_the_status_line_says_films_and_library_items(self):
        films = {"a": type("F", (), {"plex_ids": [1, 2]})(), "b": type("F", (), {"plex_ids": [3]})()}
        catalog = type("C", (), {"films": films})()
        self.assertEqual(self.gui.collection_size(catalog), "2 films (3 library items)")
        films.pop("a")
        self.assertEqual(self.gui.collection_size(catalog), "1 film")

    def test_the_export_progress_shows_at_the_smallest_window(self):
        from test_projectionist import build_fixture
        s = T.scale(self.root)
        app = self.make_app(f"{int(900 * s)}x{int(620 * s)}")
        db = os.path.join(self.dir.name, "com.plexapp.plugins.library.db-2026-09-25")
        build_fixture(db)
        app._load_catalog = lambda path: None               # (the library list is all this needs)
        app.load_db(db)
        app.set_status("-")                                 # (lays the hidden window out)
        for _ in range(5):
            self.root.update()
            time.sleep(0.02)
        page = self.root.nametowidget(app.notebook.tabs()[0])
        if page.winfo_height() < 100:
            self.skipTest("the hidden window wasn't laid out")

        def bottom(w):
            return w.winfo_rooty() - page.winfo_rooty() + w.winfo_height()
        self.assertLessEqual(bottom(app.progress), page.winfo_height())
        self.assertLessEqual(bottom(app.status_label), page.winfo_height())
        # six movie libraries (two rows of ticks): still the button, the progress and a
        # few lines of the log - the sheet list goes compact to make room
        from unittest import mock
        from projectionist import extract
        six = [extract.Library(i, f"Movies-{name}", 100 * i, folders=[f"/disk1/{name}"])
               for i, name in enumerate(("Asia", "Classics", "Documentaries", "Europe", "Kids", "USA"), 1)]
        with mock.patch.object(extract, "list_movie_libraries", return_value=six):
            app.load_db(db)
        app.set_status("-")
        for _ in range(5):
            self.root.update()
            time.sleep(0.02)
        self.assertEqual(len(app.lib_vars), 6)
        rows = {int(cb.grid_info()["row"]) for cb in app.lib_frame.winfo_children() if cb.grid_info()}
        self.assertGreater(len(rows), 1)                     # (two rows of libraries, or the test proves nothing)
        self.assertTrue(app._sheets_compact)
        for w in (app.export_btn, app.progress, app.status_label):
            self.assertLessEqual(bottom(w), page.winfo_height(), w)
        log_top = app.log.frame.winfo_rooty() - page.winfo_rooty()
        self.assertGreaterEqual(page.winfo_height() - log_top, int(40 * s))      # a few lines of log

    def test_a_long_progress_line_wraps_between_the_buttons(self):
        app = self.make_app()
        app.status_label.master.event_generate("<Configure>", width=300, height=40)
        self.assertEqual(int(str(app.status_label.cget("wraplength"))), 300)

    # -- calling jobs off -------------------------------------------------------------------------------------
    @staticmethod
    def slow_job():
        """work() that goes on until its job is called off, checking as the backbones' loops do."""
        from projectionist import jobs
        started, stopped = threading.Event(), threading.Event()

        def work():
            started.set()
            try:
                deadline = time.time() + 5
                while time.time() < deadline:
                    jobs.check()
                    time.sleep(0.002)
                return "went on to the end"
            finally:
                stopped.set()
        return work, started, stopped

    def settle(self, seconds=0.3):
        """Let anything still on its way back to the window arrive."""
        deadline = time.time() + seconds
        while time.time() < deadline:
            self.root.update()
            time.sleep(0.01)

    def test_a_newer_job_with_the_same_key_calls_off_the_older(self):
        from projectionist import jobs
        app = self.make_app()
        status = app.app_status_var.get
        got = []
        work, started, stopped = self.slow_job()
        first = app.run(work, lambda r: got.append(("first", r)), lambda m: got.append(("first failed", m)),
                        status="Searching...", key="tab.search")
        self.assertIsInstance(first, jobs.Job)
        self.assertTrue(started.wait(5))
        other_work, other_started, other_stopped = self.slow_job()
        other = app.run(other_work, lambda r: got.append(("other", r)), key="tab.other")   # another key: left be
        self.assertTrue(other_started.wait(5))
        second = app.run(lambda: 2, lambda r: got.append(("second", r)), status="Searching again...",
                         key="tab.search")
        self.assertTrue(first.cancelled)
        self.assertFalse(other.cancelled or second.cancelled)
        self.assertEqual(status(), "Searching again...")
        self.assertTrue(stopped.wait(5))                        # it stopped at its next check point
        self.pump(lambda: got)
        self.settle()
        self.assertEqual(got, [("second", 2)])                  # never done() or failed() for the first
        self.assertEqual(status(), "")
        self.assertFalse(other_stopped.is_set())
        other.cancel()
        self.assertTrue(other_stopped.wait(5))
        self.settle()
        self.assertEqual(got, [("second", 2)])
        self.assertNotIn("Cancelled", app.log.get("1.0", "end"))

    def test_a_job_called_off_takes_its_status_off_and_drops_its_answer(self):
        app = self.make_app()
        status = app.app_status_var.get
        got = []
        work, started, stopped = self.slow_job()
        job = app.run(work, got.append, got.append, status="Working...")
        self.assertTrue(started.wait(5))
        job.cancel()
        self.assertEqual(status(), "")                           # at once
        self.assertTrue(stopped.wait(5))
        # an answer already on its way back when the job is called off is dropped too
        quick = app.run(lambda: 7, got.append, got.append, status="Quick...")
        deadline = time.time() + 5
        while app.queue.empty():
            self.assertLess(time.time(), deadline)
            time.sleep(0.01)
        quick.cancel()
        self.settle()
        self.assertEqual(got, [])
        self.assertEqual(status(), "")
        # ...and an error it runs into once it's been called off is no one's concern
        from projectionist import jobs
        begun = threading.Event()

        def clumsy():
            begun.set()
            try:
                while True:
                    jobs.check()
                    time.sleep(0.002)
            except jobs.Cancelled:
                raise RuntimeError("tidying up went wrong") from None
        before = app.log.get("1.0", "end")
        job = app.run(clumsy, got.append, got.append)
        self.assertTrue(begun.wait(5))
        job.cancel()
        self.settle()
        self.assertEqual(got, [])
        self.assertEqual(app.log.get("1.0", "end"), before)
        self.assertFalse(status().startswith("Something went wrong"))

    def test_work_knows_its_job_and_a_job_started_from_done_is_left_be(self):
        from projectionist import jobs
        app = self.make_app()
        got = []

        def first_done(answer):
            got.append(answer)
            app.run(lambda: jobs.current(), got.append, key="tab.search")   # the same key, from done()
        job = app.run(lambda: jobs.current(), first_done, key="tab.search")
        self.pump(lambda: len(got) == 2)
        self.assertIs(got[0], job)
        self.assertIsInstance(got[1], jobs.Job)
        self.assertFalse(got[1].cancelled)
        self.assertTrue(job.ended and got[1].ended)

    def test_loading_a_database_or_closing_calls_off_every_job(self):
        app = self.make_app()
        got = []
        work_a, started_a, stopped_a = self.slow_job()
        work_b, started_b, stopped_b = self.slow_job()
        app.run(work_a, got.append, got.append, key="tab.search", status="Searching...")
        app.run(work_b, got.append, got.append)
        self.assertTrue(started_a.wait(5) and started_b.wait(5))
        app._load_catalog(os.path.join(self.dir.name, "not there.db"))     # (this collection can't be read)
        self.assertTrue(stopped_a.wait(5) and stopped_b.wait(5))
        self.pump(lambda: app.catalog_state == "error")
        self.settle()
        self.assertEqual(got, [])
        work_c, started_c, stopped_c = self.slow_job()
        app.run(work_c, got.append, got.append, status="Busy...")
        self.assertTrue(started_c.wait(5))
        app.shutdown()
        self.app = None
        self.assertTrue(stopped_c.wait(5))
        self.assertEqual(got, [])


if __name__ == "__main__":
    unittest.main()
