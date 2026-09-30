"""Tests for the Credits tab ("Stay after the credits?"). Fully headless: every Tk root is withdrawn and nothing is
ever shown."""

import os
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from projectionist.ask import handle  # noqa: E402
from projectionist.catalog import CopyCredits  # noqa: E402
from projectionist.credits import Marker, analyse  # noqa: E402


def hidden_root():
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()            # headless: never show a window
    return root


def copy(plex_id, blocks, duration, year, edition=""):
    """A copy's credits result from (start_s, end_s, final) marker blocks."""
    info = analyse([Marker(a * 1000, b * 1000, f) for a, b, f in blocks], duration * 1000, year)
    return CopyCredits(plex_id, edition, info)


def credits_catalog():
    """Seven films covering every verdict, two cuts that differ, an unscanned copy and one unscanned film."""
    from test_projectionist import make_catalog
    specs = [
        dict(title="Iron Man", year=2008, credits_copies=[
            copy(1, [(7000, 7470, None), (7500, 7560, True)], 7560, 2008)]),
        dict(title="Old Picture", year=1950, credits_copies=[
            copy(2, [(5000, 5100, None), (5200, 5300, True)], 5300, 1950)]),
        dict(title="The Plain Film", year=1999, credits_copies=[copy(3, [(6000, 6300, True)], 6300, 1999)]),
        dict(title="Unscanned Hero", year=2014, libraries=["Classics"]),
        dict(title="Two Cuts", year=2011, credits_copies=[
            copy(5, [(5800, 5830, None), (5900, 6200, True)], 6200, 2011, "Theatrical"),
            copy(50, [(6100, 6400, True)], 6400, 2011, "Unrated")]),
        dict(title="Blink", year=1990, credits_copies=[
            copy(6, [(4000, 4200, None), (4210, 4400, True)], 4400, 1990)]),
        dict(title="Anthology", year=2021, credits_copies=[
            copy(7, [(3000, 3100, None), (3130, 3200, None), (3240, 3300, None), (3330, 3400, True)], 3400, 2021)]),
    ]
    cat = make_catalog(specs, libraries=("Movies", "Classics"))
    cat.films["f4"].plex_ids = [5, 50, 51]             # a third copy Plex never scanned
    return cat


class FakeApp:
    """What a tab sees of the main window, answering from an in-memory catalog; run() is synchronous."""

    def __init__(self, catalog=None, state="ready"):
        self.catalog, self.catalog_state, self.catalog_error = catalog, state, ""
        self.tab = None
        self.visible = True
        self.gone, self.status = [], []
        self.asked = []

    def ask(self, request):
        self.asked.append(request)
        if self.catalog is None:
            return {"ok": False, "error": "The collection isn't loaded yet."}
        return handle(request, catalog=self.catalog)

    def run(self, work, done=None, failed=None, status=None, key=None):
        try:
            result = work()
        except Exception as exc:
            if failed:
                failed(str(exc))
            return
        if done:
            done(result)

    def goto(self, tab_title, /, **kwargs):             # (as App.goto: 'title' can be an argument)
        self.gone.append((tab_title, kwargs))

    def set_status(self, text):
        self.status.append(text)

    def current_tab(self):
        return self.tab if self.visible else None


def canvas_texts(view):
    return [view.itemcget(i, "text") for i in view.find_withtag("chart") if view.type(i) == "text"]


def widget_texts(widget):
    out = []
    for child in widget.winfo_children():
        try:
            text = child.cget("text")
            if text:
                out.append(str(text))
        except Exception:
            pass
        out += widget_texts(child)
    return out


class CreditsTabTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.root = hidden_root()
        except Exception as exc:
            raise unittest.SkipTest(f"Tk unavailable: {exc}")
        from projectionist.ui import theme
        theme.apply_styles(cls.root)
        from tkinter import ttk
        cls.notebook = ttk.Notebook(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def make_tab(self, catalog=None, state="ready", visible=True):
        from projectionist.ui import credits as C
        app = FakeApp(catalog, state)
        app.visible = visible
        tab = C.Tab.__new__(C.Tab)
        app.tab = tab
        C.Tab.__init__(tab, app, self.notebook)
        self.addCleanup(tab.frame.destroy)
        return app, tab


class PlaceholderTests(CreditsTabTestCase):
    def test_loading_none_and_error_show_calm_messages(self):
        # As on the other tabs: the page title, and one message in place of everything else
        app, tab = self.make_tab(None, "loading")
        self.assertIn("Stay after the credits?", widget_texts(tab.head))
        tab.placeholder.redraw(600, 300)
        self.assertIn("Reading your collection...", canvas_texts(tab.placeholder))
        self.assertEqual(tab.content.winfo_manager(), "")             # no empty list or disabled filters around it
        self.assertEqual(tab.placeholder.winfo_manager(), "grid")
        self.assertEqual(str(tab.search.entry.cget("state")), "disabled")
        self.assertEqual(tab.table.tree.get_children(), ())

        tab.catalog_changed(None, "none")
        tab.placeholder.redraw(600, 300)
        self.assertIn("No database yet", canvas_texts(tab.placeholder))

        app.catalog_error = "database disk image is malformed"
        tab.catalog_changed(None, "error")
        tab.placeholder.redraw(600, 300)
        texts = canvas_texts(tab.placeholder)
        self.assertIn("Couldn't read the collection", texts)
        self.assertTrue(any("malformed" in t for t in texts), texts)
        self.assertEqual(tab.content.winfo_manager(), "")

        app.catalog, app.catalog_state = credits_catalog(), "ready"
        tab.catalog_changed(app.catalog, "ready")                      # visible: filled straight away
        self.assertEqual(tab.content.winfo_manager(), "grid")
        self.assertEqual(tab.placeholder.winfo_manager(), "")

    def test_a_failed_request_says_so(self):
        app, tab = self.make_tab(credits_catalog(), "ready")
        app.ask = lambda request: {"ok": False, "error": "no such table"}
        tab.catalog_changed(app.catalog, "ready")
        tab.placeholder.redraw(600, 300)
        self.assertIn("Couldn't read the credits markers", canvas_texts(tab.placeholder))
        self.assertEqual(tab.content.winfo_manager(), "")

    def test_short_window_drops_the_page_title(self):
        from types import SimpleNamespace
        from projectionist.ui.credits import PAD, SHORT_WINDOW
        app, tab = self.make_tab(credits_catalog(), "ready")
        self.assertEqual(str(tab.head.cget("style")), "Page.TFrame")
        self.assertEqual(tab.content.grid_info()["padx"], PAD)            # the other tabs' page margin
        tab._fit_height(SimpleNamespace(height=int((SHORT_WINDOW - 60) * tab.s)))
        self.assertEqual(tab.head.winfo_manager(), "")
        pady = tab.content.grid_info()["pady"]                            # (Tk gives one number for (12, 12))
        self.assertEqual(pady if isinstance(pady, int) else pady[0], 12)  # the cards stay off the tab strip
        tab._fit_height(SimpleNamespace(height=int((SHORT_WINDOW + 60) * tab.s)))
        self.assertEqual(tab.head.winfo_manager(), "grid")
        tab.catalog_changed(None, "loading")                             # a hidden list stays hidden
        tab._fit_height(SimpleNamespace(height=int((SHORT_WINDOW - 60) * tab.s)))
        self.assertEqual(tab.content.winfo_manager(), "")


class ContentTests(CreditsTabTestCase):
    def setUp(self):
        self.app, self.tab = self.make_tab(credits_catalog(), "ready")

    def labels(self):
        return [r["label"] for r in self.tab.visible]

    def test_builds_lazily_and_lists_yes_and_maybe_by_default(self):
        app, tab = self.make_tab(credits_catalog(), "ready", visible=False)
        self.assertEqual(tab.rows, [])                               # not visible: nothing built yet
        self.assertFalse(app.asked)
        tab.shown()
        self.assertEqual(len(tab.rows), 7)                           # every film, the unscanned one too
        self.assertEqual(app.asked, [{"action": "credits", "verdict": ["Yes", "Maybe", "None found"],
                                      "count": 5000}])
        tab.shown()
        self.assertEqual(len(app.asked), 1)                          # once per catalog
        by = {r["label"]: r["verdict"] for r in tab.rows}
        self.assertEqual(by, {"Iron Man (2008)": "Yes", "Old Picture (1950)": "Maybe", "The Plain Film (1999)": "None found",
                              "Unscanned Hero (2014)": "Not scanned", "Two Cuts (2011)": "Yes", "Blink (1990)": "Maybe",
                              "Anthology (2021)": "Yes"})
        # Default filter: Yes and Maybe, sorted by title without 'The'
        self.assertEqual([r["label"] for r in tab.visible],
                         ["Anthology (2021)", "Blink (1990)", "Iron Man (2008)", "Old Picture (1950)",
                          "Two Cuts (2011)"])
        self.assertEqual(tab.count_label.cget("text"), "5 films")
        values = tab.table.tree.item(tab.table.tree.get_children()[2], "values")
        self.assertEqual(list(values), ["Iron Man", "2008", "✔ Yes", "2:04:30 mid-credits (30 s)", "1:56:40",
                                        "Movies"])
        self.assertEqual(tab.table.tree.heading("title", "text"), "Title ▲")
        # the first film is selected and shown
        self.assertEqual(tab.table.selected()["label"], "Anthology (2021)")
        self.assertIn("Anthology (2021)", widget_texts(tab.details.inner))
        self.assertEqual(tab.library_box.cget("values"), ("Any", "Movies", "Classics"))

    def test_verdict_chart_counts_every_film_and_clicks_filter(self):
        tab = self.tab
        tab.verdict_view.redraw(700, 90)
        self.assertTrue(tab.verdict_view.find_withtag("chart"))
        texts = canvas_texts(tab.verdict_view)
        self.assertIn("All 7 films in your collection", texts)
        for label, count in (("✔ Yes", "3  (43%)"), ("? Maybe", "2  (29%)"), ("– None found", "1  (14%)"),
                             ("· Not scanned", "1  (14%)")):
            self.assertIn(label, texts)
            self.assertIn(count, texts)
        self.assertIn("<Button-1>", tab.verdict_view.tag_bind("stk2"))   # segments are clickable
        tab.pick_verdict("None found")
        self.assertEqual(self.labels(), ["The Plain Film (1999)"])
        self.assertEqual(tab.verdicts(), {"None found"})
        tab.pick_verdict("Not scanned")
        self.assertEqual(self.labels(), ["Unscanned Hero (2014)"])
        texts = widget_texts(tab.details.inner)
        self.assertTrue(any("Nothing to go on" in t for t in texts), texts)
        self.assertEqual(tab.timeline_views(), [])                    # nothing to draw for it

    def test_decade_chart_shares_and_clicks(self):
        from projectionist.ui.credits import decade_shares
        shares = {d["decade"]: (d["scanned"], d["Yes"], round(d["share"], 2)) for d in decade_shares(self.tab.rows)}
        self.assertEqual(shares[2000], (1, 1, 1.0))
        self.assertEqual(shares[2010], (1, 1, 1.0))                   # the unscanned 2014 film isn't counted
        self.assertEqual(shares[1960], (0, 0, 0.0))                   # gaps are filled in
        self.assertEqual(min(shares), 1950)
        tab = self.tab
        tab.decade_view.redraw(700, 140)
        self.assertTrue(tab.decade_view.find_withtag("chart"))
        texts = canvas_texts(tab.decade_view)
        self.assertIn("Films with a likely scene, by decade", texts)
        # it's a share of the scanned films, and the rule that keeps older films at Maybe is said, not left to look
        # like a finding
        self.assertTrue(any(t.startswith("% of each decade's scanned films") for t in texts), texts)
        self.assertTrue(any("before 1977" in t for t in texts), texts)
        self.assertIn("0%", texts)                                    # the axis says it's a percentage too
        self.assertIn("100%", texts)
        tab.pick_decade(1990)
        self.assertEqual(self.labels(), ["Blink (1990)"])
        self.assertEqual(tab.decade_chip.cget("text"), "1990s  ✕")
        self.assertTrue(tab.decade_chip.grid_info())
        tab.pick_decade(1990)                                         # a second click lists every decade
        self.assertIsNone(tab.decade)
        self.assertFalse(tab.decade_chip.grid_info())
        self.assertEqual(len(tab.visible), 5)

    def test_library_and_search_filters(self):
        tab = self.tab
        tab.verdict_vars["Not scanned"].set(True)
        tab.library_var.set("Classics")
        tab._library_changed()
        self.assertEqual(self.labels(), ["Unscanned Hero (2014)"])
        tab.verdict_view.redraw(700, 90)
        self.assertIn("The 1 film in Classics", canvas_texts(tab.verdict_view))
        tab.library_var.set("Any")
        tab._library_changed()
        tab.search.var.set("two")
        tab._run_search()
        self.assertEqual(self.labels(), ["Two Cuts (2011)"])
        self.assertEqual(tab.count_label.cget("text"), "1 film")
        # a title with another verdict: the list says where it is, and the button shows it
        tab.pick_verdict("Yes")
        tab.search.var.set("plain")
        tab._run_search()
        self.assertEqual(self.labels(), [])
        self.assertEqual(tab.empty.winfo_manager(), "place")
        self.assertIn("1 film with other verdicts", tab.empty_label.cget("text"))
        self.assertTrue(tab.empty_btn.grid_info())
        tab.show_everything()
        self.assertEqual(self.labels(), ["The Plain Film (1999)"])
        self.assertEqual(tab.empty.winfo_manager(), "")
        # nothing anywhere
        tab.search.var.set("zzqx")
        tab._run_search()
        self.assertEqual(tab.empty_label.cget("text"),
                         "No film called “zzqx” in your collection. Try part of the title, or check the spelling.")
        self.assertFalse(tab.empty_btn.grid_info())
        tab.clear_search()
        self.assertEqual(len(tab.visible), 7)
        for v in tab.verdict_vars.values():
            v.set(False)
        tab.apply_filters()
        self.assertIn("Tick at least one verdict", tab.empty_label.cget("text"))

    def test_typing_filters_after_a_pause_and_picking_goes_to_the_film(self):
        tab = self.tab
        tab.search.var.set("iron")
        self.assertIsNotNone(tab._search_job)                        # debounced
        deadline = time.time() + 5
        while tab._search_job is not None and time.time() < deadline:
            self.root.update()
            time.sleep(0.01)
        self.assertEqual(self.labels(), ["Iron Man (2008)"])
        self.assertEqual(tab._suggest("plai"), ["The Plain Film (1999)"])     # any film, whatever its verdict
        self.assertEqual(tab._suggest("i"), ["Iron Man (2008)"])
        tab.search.var.set("The Plain Film (1999)")
        tab._picked("The Plain Film (1999)")                         # a suggestion chosen: widen and select
        self.assertIn("None found", tab.verdicts())
        self.assertEqual(tab.table.selected()["label"], "The Plain Film (1999)")
        tab.search.var.set("anth")
        tab._picked("anth")                                          # Enter on part of a title
        self.assertEqual(tab.table.selected()["label"], "Anthology (2021)")

    def test_sorting(self):
        tab = self.tab
        for v in tab.verdict_vars.values():
            v.set(True)
        tab.apply_filters()
        order = lambda: [tab.table.rows[i]["label"] for i in tab.table.tree.get_children()]
        tab.table.sort("verdict_text")
        self.assertEqual([tab.table.rows[i]["verdict"] for i in tab.table.tree.get_children()],
                         ["Yes", "Yes", "Yes", "Maybe", "Maybe", "None found", "Not scanned"])
        self.assertEqual(tab.table.tree.heading("verdict_text", "text"), "Verdict ▲")
        tab.table.sort("scenes")                                     # most scenes first on the first click
        self.assertEqual(order()[0], "Anthology (2021)")
        self.assertEqual(tab.table.tree.heading("scenes", "text"), "Scenes ▼")
        tab.table.sort("credits_start")
        self.assertEqual(order()[0], "Anthology (2021)")             # 0:50:00
        self.assertEqual(order()[-1], "Unscanned Hero (2014)")        # blank last
        tab.table.sort("credits_start")                              # a second click: the latest credits first...
        self.assertEqual(tab.table.tree.heading("credits_start", "text"), "Credits start ▼")
        self.assertEqual(order()[0], "Iron Man (2008)")              # 1:56:40
        self.assertEqual(order()[-1], "Unscanned Hero (2014)")        # ...and still no blank rows above them
        tab.pick_verdict("Not scanned")
        tab.verdict_vars["Yes"].set(True)
        tab.apply_filters()                                          # (new rows keep the order, blanks last)
        self.assertEqual(order()[-1], "Unscanned Hero (2014)")
        tab.table.sort("year")
        tab.table.sort("year")                                       # newest first
        self.assertEqual(order()[0], "Anthology (2021)")
        tab.table.sort("title")
        self.assertEqual(order()[:3], ["Anthology (2021)", "Iron Man (2008)", "Two Cuts (2011)"])
        tab.pick_verdict("Maybe")                                    # the sort survives new rows
        self.assertEqual(order(), ["Blink (1990)", "Old Picture (1950)"])

    def test_columns_step_aside_when_narrow(self):
        t = self.tab.table
        t.fit_columns(int(1000 * t.s))
        self.assertEqual(t.shown_columns, ["title", "year", "verdict_text", "scenes", "credits_start", "library"])
        t.fit_columns(int(600 * t.s))
        self.assertEqual(t.shown_columns, ["title", "year", "verdict_text", "scenes", "credits_start"])
        t.fit_columns(int(450 * t.s))
        self.assertEqual(t.shown_columns, ["title", "year", "verdict_text", "scenes"])
        t.fit_columns(int(300 * t.s))
        self.assertEqual(t.shown_columns, ["title", "verdict_text", "scenes"])

    def test_details_for_two_cuts(self):
        tab = self.tab
        tab.navigate(title="Two Cuts (2011)")
        self.assertEqual(tab.table.selected()["label"], "Two Cuts (2011)")
        texts = widget_texts(tab.details.inner)
        joined = "\n".join(texts)
        self.assertIn("Two Cuts (2011)", texts)
        self.assertIn("✔ Yes", texts)
        self.assertIn("Your 2 copies differ", joined)
        self.assertIn("(Theatrical)", joined)
        self.assertIn("1 other copy of this film hasn't been scanned", joined)
        self.assertIn("The likely scene starts at 1:37:10, 30 s after the credits begin.", joined)
        self.assertIn("No footage after the credits start that Plex could see.", joined)
        views = tab.timeline_views()
        self.assertEqual([v.film_copy["edition"] for v in views], ["Theatrical", "Unrated"])  # the verdict's copy first
        for v in views:
            v.redraw(460, 170)
            self.assertTrue(v.find_withtag("chart"))
        self.assertIn("Theatrical  ·  Yes", canvas_texts(views[0]))
        self.assertIn("Unrated  ·  None found", canvas_texts(views[1]))
        self.assertIn("1:37:10", canvas_texts(views[0]))             # the scene's label agrees with the list

    def test_scene_rows_explain_maybes(self):
        tab = self.tab
        tab.navigate(title="Blink")
        joined = "\n".join(widget_texts(tab.details.inner))
        self.assertIn("? Maybe", joined)
        self.assertIn("1:10:00 – 1:10:10", joined)
        self.assertIn("Mid-credits  ·  10 s  ·  ? maybe", joined)
        self.assertIn("Only a maybe: Very short", joined)
        tab.navigate(title="Anthology (2021)")
        views = tab.timeline_views()
        self.assertEqual(len(views), 1)
        views[0].redraw(460, 200)
        self.assertEqual(sum(1 for t in canvas_texts(views[0]) if t.endswith("likely")), 3)

    def test_the_link_opens_watch_next(self):
        from projectionist.ui.widgets import LinkLabel
        tab = self.tab
        tab.navigate(title="Iron Man (2008)")

        def links(w):
            found = [c for c in w.winfo_children() if isinstance(c, LinkLabel)]
            for c in w.winfo_children():
                found += links(c)
            return found
        link = links(tab.details.inner)
        self.assertEqual([x.cget("text") for x in link], ["Everything about this film  →",
                                                          "Find similar films to watch  →"])
        link[1].event_generate("<Button-1>")
        # with the film's key, so Watch Next opens exactly this film (another can share its title and year)
        key = tab.table.selected()["key"]
        self.assertTrue(key)
        self.assertEqual(self.app.gone, [("Watch Next", {"like": "Iron Man (2008)", "film_key": key})])
        # ...and the film's own page, on the Film tab
        link[0].event_generate("<Button-1>")
        self.assertEqual(self.app.gone[-1], ("Film", {"film_key": key, "title": "Iron Man (2008)"}))

    def test_enter_or_a_double_click_opens_the_film_page(self):
        tab = self.tab
        tab.navigate(title="Iron Man (2008)")
        self.assertIn("<Key-Return>", tab.table.tree.bind())
        self.app.gone.clear()
        tab.table._opened()                                   # what Enter and a double-click run
        key = tab.table.selected()["key"]
        self.assertEqual(self.app.gone, [("Film", {"film_key": key, "title": "Iron Man (2008)"})])
        self.assertEqual(tab.shown_id, tab.table.selected()["id"])      # (a click still shows the timeline here)

    def test_navigate_widens_filters(self):
        tab = self.tab
        tab.library_var.set("Classics")
        tab._library_changed()
        tab.pick_decade(1950)
        tab.search.var.set("old")
        tab._run_search()
        tab.navigate(title="The Plain Film (1999)")
        self.assertEqual(tab.table.selected()["label"], "The Plain Film (1999)")
        self.assertIn("None found", tab.verdicts())
        self.assertEqual(tab.library_var.get(), "Any")
        self.assertIsNone(tab.decade)
        self.assertEqual(tab.search.get(), "")
        tab.navigate(title="unscanned hero")                          # loose titles work too
        self.assertEqual(tab.table.selected()["label"], "Unscanned Hero (2014)")
        tab.navigate(foo="bar")                                       # unknown keys are ignored

    def test_a_film_that_isnt_there_says_so_until_one_is_shown(self):
        tab, app = self.tab, self.app
        tab.navigate(title="Iron Man (2008)")
        tab.navigate(title="No Such Film Anywhere")
        # said as every tab says it: 'No film called “X” in your collection.'
        self.assertEqual(app.status[-1], "No film called “No Such Film Anywhere” in your collection.")
        # the details don't go on showing Iron Man as if it were the film asked for
        self.assertIsNone(tab.table.selected())
        self.assertNotIn("Iron Man (2008)", widget_texts(tab.details.inner))
        views = [c for c in tab.details.inner.winfo_children()[0].body.winfo_children()]
        views[0].redraw(600, 150)
        self.assertIn("No film called “No Such Film Anywhere” in your collection", canvas_texts(views[0]))
        views[0].redraw(340, 150)                                     # a narrow panel: the heading wraps, whole
        self.assertEqual(" ".join(canvas_texts(views[0])[:2]),
                         "No film called “No Such Film Anywhere” in your collection")
        tab.navigate(title="Blink (1990)")                            # found: the stale message goes
        self.assertEqual(app.status[-1], "")
        self.assertEqual(tab.table.selected()["label"], "Blink (1990)")
        n = len(app.status)
        tab.navigate(title="Iron Man (2008)")
        self.assertEqual(len(app.status), n)                          # nothing more to take back
        # Enter on a title nobody has: the empty list explains, and the message goes once a film is shown again
        tab.search.var.set("zzqx")
        tab._picked("zzqx")
        self.assertEqual(app.status[-1], "No film called “zzqx” in your collection.")
        tab.clear_search()
        self.assertEqual(app.status[-1], "")
        # only a film key, and no film has it: said plainly (no quoted title to show)
        tab.navigate(film_key="no-such-key")
        self.assertEqual(app.status[-1], "That film isn't in your collection.")
        self.assertIsNone(tab.table.selected())

    def test_film_key_beats_a_title_that_folds_alike(self):
        from test_projectionist import make_catalog
        cat = make_catalog([
            dict(title="Drácula", year=1931, credits_copies=[copy(1, [(4000, 4200, True)], 4200, 1931)]),
            dict(title="Dracula", year=1931),                        # Plex never scanned this one
            dict(title="Nosferatu", year=1922, credits_copies=[copy(3, [(5000, 5100, True)], 5100, 1922)])])
        app, tab = self.make_tab(cat, "ready")
        for asked, key, found in (("Dracula (1931)", None, "Dracula (1931)"),       # the exact title first
                                  ("Drácula (1931)", None, "Drácula (1931)"),
                                  ("whatever the label", "f0", "Drácula (1931)"),  # the key wins
                                  ("Dracula (1931)", "f0", "Drácula (1931)"),
                                  (None, "f1", "Dracula (1931)"),
                                  ("nosferatu 1922", None, "Nosferatu (1922)")):
            tab.navigate(title=asked, film_key=key)
            self.assertEqual(tab.table.selected()["label"], found, (asked, key))
            self.assertEqual(tab.table.selected()["key"], {"Drácula (1931)": "f0", "Dracula (1931)": "f1",
                                                           "Nosferatu (1922)": "f2"}[found])

    def test_navigate_before_the_collection_is_ready(self):
        app, tab = self.make_tab(None, "loading")
        tab.navigate(title="Iron Man (2008)", film_key="f0")
        self.assertIn("still loading", app.status[-1])
        app.catalog, app.catalog_state = credits_catalog(), "ready"
        app.visible = False
        tab.catalog_changed(app.catalog, "ready")
        self.assertEqual(tab.table.selected()["label"], "Iron Man (2008)")
        self.assertEqual(app.status[-1], "")                          # the 'still loading' message is taken back
        # a load that fails takes it back too
        app, tab = self.make_tab(None, "loading")
        tab.navigate(title="Iron Man (2008)")
        tab.catalog_changed(None, "error")
        self.assertEqual(app.status[-1], "")
        self.assertIsNone(tab._pending)

    def test_charts_are_as_tall_as_they_need(self):
        from projectionist.ui import charts
        from projectionist.ui.credits import needed_height
        from projectionist.ui.paint import TkPainter
        tab = self.tab
        tab.navigate(title="Anthology (2021)")
        view = tab.timeline_views()[0]
        p = TkPainter(view, 260, 100, view.s)
        narrow = needed_height(p, view._draw)
        wide = needed_height(TkPainter(view, 900, 100, view.s), view._draw)
        self.assertGreater(narrow, wide)                              # close scene labels stack when narrow
        self.assertGreaterEqual(wide, charts.timeline_height(view.s, 1) - 12 * view.s)
        view.redraw(260, 100)
        self.root.update_idletasks()
        self.assertEqual(int(view.cget("height")), narrow)

    def test_a_chart_given_another_width_is_fitted_again(self):
        """A chart first drawn while its page was still being laid out (narrow: its labels on more lines) is drawn
        and fitted again at the width it ends up with - even when no <Configure> reaches it (a window not yet on
        screen gets none), so a fresh start and a later redraw (a change of look) give the same height."""
        import time
        from projectionist.ui.credits import auto_height, needed_height
        from projectionist.ui.paint import TkPainter
        tab = self.tab
        tab.navigate(title="Anthology (2021)")
        view = tab.timeline_views()[0]
        draw = view._draw
        widths = []
        view.show(auto_height(view, lambda p: (widths.append(p.width), draw(p))[1]))
        wide = needed_height(TkPainter(view, 900, 100, view.s), draw)
        view.redraw(260, 100)                                         # (drawn narrow, and fitted to that)
        self.root.update_idletasks()
        self.assertGreater(int(view.cget("height")), wide)
        view.winfo_width = lambda: 900                                 # (laid out wider since - and no <Configure>)
        view.winfo_height = lambda: int(view.cget("height"))
        deadline = time.time() + 2
        while int(view.cget("height")) != wide and time.time() < deadline:
            self.root.update()
            time.sleep(0.01)
        self.assertEqual(int(view.cget("height")), wide)
        self.assertEqual(widths[-1], 900)

    def test_verdict_tooltips_promise_what_a_click_lists(self):
        tab = self.tab
        tips = {s["key"]: s["tip"] for s in tab.verdict_segments()}
        self.assertTrue(tips["Yes"].startswith("✔ Yes: 3 films (43%)"), tips["Yes"])
        self.assertTrue(tips["Yes"].endswith("Click to list them"), tips["Yes"])
        tab.pick_decade(2000)                                        # the decade stays when a colour is clicked
        tips = {s["key"]: s["tip"] for s in tab.verdict_segments()}
        self.assertTrue(tips["Yes"].startswith("✔ Yes: 3 films"))       # the bar still counts the whole collection
        self.assertTrue(tips["Yes"].endswith("Click to list the one from the 2000s"), tips["Yes"])
        self.assertTrue(tips["Maybe"].endswith("Click to list them - there are none from the 2000s"), tips["Maybe"])
        self.assertEqual(tab._tips_scope, tab._scope())               # the chart was redrawn with them
        tab.pick_verdict("Yes")
        self.assertEqual(self.labels(), ["Iron Man (2008)"])
        tab.clear_decade()
        tab.search.var.set("an")
        tab._run_search()
        tips = {s["key"]: s["tip"] for s in tab.verdict_segments()}
        self.assertTrue(tips["Yes"].endswith("Click to list the 2 with “an” in the title"), tips["Yes"])
        self.assertEqual(tab._tips_scope, tab._scope())
        tab.pick_verdict("Yes")
        self.assertEqual(self.labels(), ["Anthology (2021)", "Iron Man (2008)"])

    def test_tooltips_fit_a_narrow_chart(self):
        # A tooltip is drawn on its chart, and in the smallest window the decade chart and the timelines are only
        # about 340 px wide: long lines (the before-1977 rule, why a scene is only a maybe) wrap, not get cut off
        from projectionist.ui import paint as P
        tab = self.tab

        def widest_tip(view, width, height):
            view.redraw(width, height)
            ui = P.Interaction.of(view, create=False)
            widest = 0
            for tag in list(ui.tips):
                view.tk.call(ui.command, "enter", tag, 10, 10)
                boxes = [view.bbox(i) for i in view.find_withtag("tooltip")]
                if boxes:
                    widest = max(widest, max(b[2] for b in boxes) - min(b[0] for b in boxes))
                view.tk.call(ui.command, "out", "", 0, 0)
            return widest, [text.replace("\n", " ") for text, _ in ui.tips.values()]

        widest, tips = widest_tip(tab.decade_view, 336, 156)
        self.assertTrue(any("Films from before 1977 are a Maybe at most" in t for t in tips), tips)
        self.assertLessEqual(widest, 336 - 4)
        widest, tips = widest_tip(tab.verdict_view, 300, 110)
        self.assertTrue(any(t.endswith("Click to list them") for t in tips), tips)
        self.assertLessEqual(widest, 300 - 4)
        # the tips go to the chart whole: paint wraps them to fit, heading and all, so nothing is cut short
        ui = P.Interaction.of(tab.verdict_view, create=False)
        self.assertEqual(sorted(text for text, _ in ui.tips.values()),
                         sorted(s["tip"] for s in tab.verdict_segments()))
        tab.navigate(title="Blink (1990)")
        widest, tips = widest_tip(tab.timeline_views()[0], 300, 170)
        self.assertTrue(any("very short - could be a title card" in t for t in tips), tips)
        self.assertLessEqual(widest, 300 - 4)

    def test_every_time_agrees_to_the_second(self):
        # Credits from 1:23:20.96, a scene from 1:25:00.97 to 1:25:30.02: rounding to a tenth of a second would
        # show the chart one second later than the list (1:23:21, 1:25:01)
        from test_projectionist import make_catalog
        from projectionist.ui.credits import scene_times, timeline_scenes, timeline_stretches
        cat = make_catalog([dict(title="Close Call", year=2015, credits_copies=[
            copy(1, [(5000.96, 5100.97, None), (5130.02, 5400, True)], 5400, 2015)])])
        app, tab = self.make_tab(cat, "ready")
        answer = handle({"action": "credits", "title": "Close Call"}, catalog=cat)["credits"]
        self.assertEqual((answer["credits_start"], answer["scenes"][0]["starts_at"], answer["scenes"][0]["ends_at"]),
                         ("1:23:20", "1:25:00", "1:25:30"))
        self.assertEqual(answer["credits_start_sec"], 5000.9)          # never rounded up into the next second
        row = tab.rows[0]
        self.assertEqual(row["credits_start"], "1:23:20")
        self.assertEqual(row["scenes"], "1:25:00 mid-credits (30 s)")
        copy_ = row["credits"]
        self.assertEqual(scene_times(copy_["scenes"][0]), (5100, 5130, "30 s"))
        self.assertEqual([(s["start_sec"], s["end_sec"]) for s in timeline_scenes(copy_)], [(5100, 5130)])
        self.assertEqual(timeline_stretches(copy_)[0]["start_sec"], 5000)
        tab.navigate(title="Close Call (2015)")
        joined = "\n".join(widget_texts(tab.details.inner))
        self.assertIn("1:25:00 – 1:25:30", joined)
        self.assertIn("Mid-credits  ·  30 s  ·  ✔ likely", joined)
        self.assertIn("starts at 1:25:00, 1 min 40 s after the credits begin", joined)
        view = tab.timeline_views()[0]
        view.redraw(600, 180)
        texts = canvas_texts(view)
        self.assertIn("1:25:00", texts)
        self.assertIn("30 s · likely", texts)
        self.assertTrue(any(t.startswith("Credits start at 1:23:20") for t in texts), texts)

    def test_the_timeline_names_the_edition_when_only_one_copy_was_scanned(self):
        from test_projectionist import make_catalog
        cat = make_catalog([
            dict(title="Planet Terror", year=2007, editions=["Theatrical", "Extended"],
                 credits_copies=[copy(12, [(6000, 6300, True)], 6300, 2007, "Extended")]),
            dict(title="Twin Copies", year=2010, credits_copies=[copy(2, [(5000, 5300, True)], 5300, 2010)])])
        cat.films["f0"].plex_ids = [11, 12]
        cat.films["f1"].plex_ids = [2, 20]                            # two copies, neither with an edition name
        app, tab = self.make_tab(cat, "ready")
        tab.navigate(title="Planet Terror (2007)")
        joined = "\n".join(widget_texts(tab.details.inner))
        self.assertIn("1 other copy of this film hasn't been scanned for credits (Theatrical).", joined)
        view = tab.timeline_views()[0]
        view.redraw(500, 150)
        self.assertIn("Extended  ·  None found", canvas_texts(view))
        tab.navigate(title="Twin Copies (2010)")
        joined = "\n".join(widget_texts(tab.details.inner))
        self.assertIn("1 other copy of this film hasn't been scanned for credits.", joined)
        view = tab.timeline_views()[0]
        view.redraw(500, 150)
        self.assertIn("How the film ends", canvas_texts(view))         # no names to tell them apart by

    def test_catalog_change_rebuilds(self):
        tab = self.tab
        tab.catalog_changed(None, "loading")
        self.assertEqual(tab.rows, [])
        other = credits_catalog()
        del other.films["f0"]
        self.app.catalog = other
        tab.catalog_changed(other, "ready")
        self.assertEqual(len(tab.rows), 6)
        self.assertNotIn("Iron Man (2008)", [r["label"] for r in tab.rows])


class LayoutTests(CreditsTabTestCase):
    """Real geometry on a withdrawn window: a frame placed at a window's size lays out without being shown."""

    def make_placed_tab(self, width, height):
        import tkinter as tk
        from projectionist.ui import credits as C
        holder = tk.Frame(self.root)
        holder.place(x=0, y=0, width=width, height=height)
        self.addCleanup(holder.destroy)
        app = FakeApp(credits_catalog(), "ready")
        tab = C.Tab.__new__(C.Tab)
        app.tab = tab
        C.Tab.__init__(tab, app, holder)
        tab.frame.pack(fill="both", expand=True)
        return holder, tab

    def test_timelines_fill_the_details_panel_at_any_window_size(self):
        from projectionist.ui import theme
        s = theme.scale(self.root)
        holder, tab = self.make_placed_tab(int(900 * s), int(620 * s))
        tab.navigate(title="Two Cuts (2011)")
        self.root.update()
        views = tab.timeline_views()
        self.assertEqual(len(views), 2)
        narrow = [v.winfo_width() for v in views]
        for v in views:
            self.assertEqual(v.winfo_width(), v.master.winfo_width())    # all of the card, never more
            self.assertGreater(v.winfo_width(), 250 * s)
        self.assertEqual(narrow[0], narrow[1])                         # both copies drawn to the same scale
        # at 900 wide the panel is narrower than Tk's default canvas width of 10 cm, which used to cut off the end
        self.assertLess(narrow[0], int(self.root.winfo_fpixels("10c")))
        holder.place_configure(width=int(1920 * s), height=int(1080 * s))
        self.root.update()
        for v in views:
            self.assertEqual(v.winfo_width(), v.master.winfo_width())
            self.assertGreater(v.winfo_width(), narrow[0] + 300 * s)   # a wide window: a wide timeline
        # the message card fills the panel too
        tab.navigate(title="No Such Film")
        self.root.update()
        card = tab.details.inner.winfo_children()[0]
        view = card.body.winfo_children()[0]
        self.assertEqual(view.winfo_width(), card.body.winfo_width())


class HelperTests(unittest.TestCase):
    def test_lead_in_avoids_fractional_minute_ticks(self):
        from projectionist.ui.charts import nice_ticks
        from projectionist.ui.credits import lead_in_for
        for credits_len in (20, 60, 100, 150, 400, 600, 700, 800, 1000):
            c = {"duration_sec": 6000 + credits_len, "credits_start_sec": 6000.0,
                 "stretches": [{"start_sec": 6000.0, "end_sec": 6000.0 + credits_len, "counted": True}]}
            lead = lead_in_for(c)
            self.assertGreaterEqual(lead, 90)
            start = 6000 - lead
            ticks = nice_ticks(start / 60, c["duration_sec"] / 60, 6)
            steps = {round(b - a, 6) for a, b in zip(ticks, ticks[1:])}
            self.assertTrue(all(s == int(s) for s in steps), (credits_len, steps))

    def test_first_scene_note(self):
        from projectionist.ui.credits import first_scene_note
        c = {"credits_start_sec": 100.0, "scenes": [
            {"start_sec": 130.0, "starts_at": "0:02:10", "verdict": "Maybe"},
            {"start_sec": 200.0, "starts_at": "0:03:20", "verdict": "Likely"}]}
        self.assertEqual(first_scene_note(c), "The likely scene starts at 0:03:20, 1 min 40 s after the credits "
                                              "begin (2 stretches of footage in all).")
        self.assertEqual(first_scene_note({"scenes": []}), "")


if __name__ == "__main__":
    unittest.main()
