"""Tests for the Six Degrees tab (projectionist/ui/degrees.py). Fully headless: every window is hidden, and the
app's background runner is replaced by one that runs the work straight away (or holds it, to test stale
results)."""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from projectionist import jobs  # noqa: E402
from projectionist.ui import degrees as D  # noqa: E402


def hidden_root():
    import tkinter as tk
    from projectionist.ui import theme
    root = tk.Tk()
    root.withdraw()            # headless: never show a window
    theme.apply_styles(root)
    return root


def small_catalog():
    """Two linked groups and an island: Ann-Bob-Cat (Asia) meet Dan-Eve (West) through Cat and Eve; Zed and Yan
    are on their own unless directors count (Zoe directed Four and Six); a gang of four makes five films."""
    from test_projectionist import make_catalog
    hk, us = ["Hong Kong"], ["United States of America"]
    return make_catalog([
        dict(title="One", year=1990, cast=["Ann", "Bob"], libraries=["Asia"], countries=hk, genres=["Action"]),
        dict(title="Two", year=1991, cast=["Bob", "Cat"], libraries=["Asia"], countries=hk, genres=["Action"]),
        dict(title="Three", year=1992, cast=["Cat", "Dan"], libraries=["West"], countries=us, genres=["Drama"]),
        dict(title="Four", year=1993, cast=["Eve", "Ann"], libraries=["Asia"], countries=hk, genres=["Action"],
             directors=["Zoe"]),
        dict(title="Five", year=1994, cast=["Dan", "Extra", "Eve"], libraries=["West"], countries=us,
             genres=["Drama"]),
        dict(title="Six", year=1995, cast=["Zed", "Yan"], libraries=["West"], countries=us, genres=["Comedy"],
             directors=["Zoe"]),
    ] + [dict(title=f"Gang {i}", year=2000 + i, cast=["Kuo", "Lu", "Chiang", "Sun"], libraries=["Asia"],
              countries=hk, genres=["Action"]) for i in range(5)], libraries=("Asia", "West"))


class FakeApp:
    """The main window's services. run() does the work at once unless hold=True (then release() finishes it)."""

    def __init__(self, catalog=None, state="ready", hold=False):
        self.catalog, self.catalog_state, self.catalog_error = catalog, state, "the file is locked"
        self.gone, self.status, self.hold, self.held, self.keys = [], "", hold, [], []
        self._keyed = {}

    def ask(self, request):
        from projectionist.ask import handle
        return handle(request, catalog=self.catalog)

    def run(self, work, done=None, failed=None, status=None, key=None):
        """As App.run: returns the job, and a newer run with the same key calls the older one off - it stops,
        and never answers. Held jobs wait in .held as (job, work, done, failed)."""
        if status:
            self.status = status
        self.keys.append(key)
        if key is not None and key in self._keyed:
            self._keyed.pop(key).cancel()
        job = jobs.Job(key)
        if key is not None:
            self._keyed[key] = job
        held = (job, work, done, failed)
        if self.hold:
            self.held.append(held)
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

    def goto(self, tab_title, /, **kwargs):
        self.gone.append((tab_title, kwargs))
        return object()

    def set_status(self, text):
        self.status = text


def quiet_destroy(root):
    """Destroy a window without its charts' pending redraws firing into the void ('invalid command name')."""
    from projectionist.ui.widgets import ChartView
    stack = [root]
    while stack:
        w = stack.pop()
        stack.extend(w.winfo_children())
        if isinstance(w, ChartView) and w._pending is not None:
            try:
                w.after_cancel(w._pending)
            except Exception:
                pass
    root.destroy()


def busy(button) -> bool:
    return button.instate(["disabled"])


def link_texts(frame) -> list[str]:
    """Every text in a notes frame: the lines, and the links on them."""
    return [w.cget("text") for row in frame.winfo_children() for w in row.winfo_children()]


def person_notes(tab) -> list[str]:
    return link_texts(tab.person_notes)


def links_in(frame) -> dict:
    """{link text: the LinkLabel} in a notes frame."""
    from projectionist.ui.widgets import LinkLabel
    return {w.cget("text"): w for row in frame.winfo_children() for w in row.winfo_children()
            if isinstance(w, LinkLabel)}


def click(link):
    link.event_generate("<Button-1>")


def with_namesake(catalog, pid, name):
    """Give someone the same name as someone else (make_catalog's IDs come from names, so they're unique)."""
    person = catalog.people[pid]
    person.name = name
    for film in catalog.films.values():
        for c in film.cast + film.directors:
            if c.person == pid:
                c.name = name
    return catalog


def texts(view, w=700, h=420):
    """Draw a chart view at a size and return its text items (drawn items are tagged 'chart')."""
    view.redraw(w, h)
    return [view.itemcget(i, "text") for i in view.find_withtag("chart") if view.type(i) == "text"]


class HelperTests(unittest.TestCase):
    def test_words(self):
        self.assertEqual(D.plural(1, "step"), "1 step")
        self.assertEqual(D.plural(1234, "person", "people"), "1,234 people")
        self.assertEqual(D.film_label({"title": "Rush Hour", "year": 1998}), "Rush Hour (1998)")
        self.assertEqual(D.film_label({"title": "Untitled", "year": None}), "Untitled")
        self.assertEqual(D.candidate_name("Jackie Chan (12 films)"), "Jackie Chan")
        self.assertEqual(D.candidate_name("Jacki R. Chan (1 films)"), "Jacki R. Chan")
        self.assertIsNone(D.match_note({"asked": "Ann", "found": "Ann", "how": "exact"}))
        self.assertEqual(D.match_note({"asked": "jackie chn", "found": "Jackie Chen", "how": "guess"}),
                         "No one called “jackie chn” in your collection - showing Jackie Chen, the closest spelling.")
        self.assertEqual(D.match_note({"asked": "de niro", "found": "Robert De Niro", "how": "partial"}),
                         "“de niro” matched Robert De Niro.")
        self.assertEqual(D.chain_summary({"degrees": 2, "shortest_chains": 25, "searched_ms": 81}),
                         "2 steps · 25 equally short chains · found in 81 ms")
        self.assertIn("the only chain", D.chain_summary({"degrees": 1, "shortest_chains": 1, "searched_ms": 3}))
        self.assertEqual(D.readable_role("billed 3"), "an unnamed role (billed 3)")
        self.assertEqual(D.readable_role("Lee (billed 1)"), "Lee (billed 1)")
        self.assertEqual([i["label"] for i in D.reach_items([5, 40, 2])], ["1 step", "2 steps", "3 steps"])
        self.assertEqual(D.group_name("decade", 1980), "1980s")
        self.assertEqual(D.group_name("country", "United States of America"), "United States")
        self.assertEqual(D.group_choice("library", "Movies", 1), "Movies  (1 film)")

    def test_strongest_links_keep_the_centre_and_the_strongest(self):
        nodes = [{"id": str(i), "center": i == 0} for i in range(4)]            # room for 10 links
        edges = [{"a": str(a), "b": str(b), "weight": a + b} for a in range(4) for b in range(a + 1, 4)]
        self.assertEqual(len(D.strongest_links(nodes, edges)), 6)               # fewer than room: all kept
        nodes = [{"id": str(i), "center": i == 0} for i in range(2)]            # room for 5
        many = [{"a": "0", "b": "1", "weight": 1}] + [{"a": "x", "b": f"y{i}", "weight": i} for i in range(9)]
        kept = D.strongest_links(nodes, many)
        self.assertEqual(len(kept), 5)
        self.assertIn(many[0], kept)                                            # the centre's own link
        self.assertEqual([e["weight"] for e in kept[1:]], [8, 7, 6, 5])

    def test_strongest_links_take_a_whole_strength_at_a_time(self):
        """Someone with one film: most pairs of their co-stars share just that film. Drawing a few of those at
        random would make the rest look unconnected - so a strength is drawn whole or not at all."""
        nodes = [{"id": "c", "center": True}] + [{"id": f"n{i}"} for i in range(12)]       # room for 32 links
        spokes = [{"a": "c", "b": f"n{i}", "weight": 1} for i in range(12)]
        pairs = [(i, j) for i in range(12) for j in range(i + 1, 12)]                    # 66 pairs
        weight = lambda n: 5 if n == 0 else 2 if n <= 17 else 1                           # 1 at 5, 17 at 2, 48 at 1
        others = [{"a": f"n{i}", "b": f"n{j}", "weight": weight(n)} for n, (i, j) in enumerate(pairs)]
        links = D.strongest_links(nodes, spokes + others)
        self.assertTrue(all(s in links for s in spokes))                                  # the centre's own lines
        self.assertEqual(sorted(e["weight"] for e in links if e not in spokes), [2] * 17 + [5])
        self.assertEqual(D.links_floor(nodes, spokes + others, links), (2, 48))
        crowded = [{"a": f"n{i}", "b": f"n{j}", "weight": 3} for i, j in pairs]           # one strength, too many
        links = D.strongest_links(nodes, spokes + crowded)
        self.assertEqual(links, spokes)
        self.assertEqual(D.links_floor(nodes, spokes + crowded, links), (None, 66))

    def test_names_that_are_shared_or_have_commas(self):
        self.assertEqual(D.split_names("Isaac C. Singleton, Jr., Ann; George Hawkins, Jr"),
                         ["Isaac C. Singleton, Jr.", "Ann", "George Hawkins, Jr"])
        self.assertEqual(D.split_names("John Smith (10 films, e.g. Paper Harbour), Bob,, "),
                         ["John Smith (10 films, e.g. Paper Harbour)", "Bob"])
        self.assertEqual(D.candidate_name("John Smith (10 films, e.g. Paper Harbour)"), "John Smith")
        self.assertEqual(D.candidate_name("Lee Harper (1 film: Harbour Lights)"), "Lee Harper")
        self.assertEqual(D.candidate_name("Marco Aurélio (Bruttus)"), "Marco Aurélio (Bruttus)")
        cat = with_namesake(small_catalog(), "p:Extra", "Dan")                          # two people called Dan
        self.assertEqual(D.person_label(cat, cat.people["p:Extra"]), "Dan (1 film: Five)")
        self.assertEqual(D.person_label(cat, cat.people["p:Dan"]), "Dan (2 films, e.g. Five)")
        directory = D.people_directory(cat)
        self.assertEqual(directory["shared"], {"Dan"})
        self.assertEqual(directory["ids"], {"Dan (2 films, e.g. Five)": "p:Dan", "Dan (1 film: Five)": "p:Extra"})
        self.assertIn("Ann", directory["labels"])                                        # not shared: just the name
        self.assertNotIn("Dan", directory["labels"])

    def test_balanced_links_leave_nobody_out(self):
        ids = [str(i) for i in range(8)]                                        # 28 pairs, room for 20
        nodes = [{"id": i} for i in ids]
        edges = [{"a": a, "b": b, "weight": 5 if "7" in (a, b) else 7} for n, a in enumerate(ids) for b in ids[n + 1:]]
        weakest = lambda links: sum("7" in (e["a"], e["b"]) for e in links)
        self.assertEqual(weakest(D.strongest_links(nodes, edges)), 0)           # by strength alone: left out
        kept = D.balanced_links(nodes, edges)
        self.assertEqual(len(kept), 20)
        self.assertGreaterEqual(weakest(kept), 2)                               # balanced: keeps two lines
        small = edges[:5]
        self.assertEqual(D.balanced_links(nodes, small), sorted(small, key=lambda e: -e["weight"]))

    def test_rows_that_fit(self):
        class P:
            height = 200

            @staticmethod
            def u(n):
                return n
        self.assertEqual(D.rows_that_fit(P, 20), 10)
        self.assertEqual(D.rows_that_fit(P, 3), 3)
        P.height = 40
        self.assertEqual(D.rows_that_fit(P, 20), 5)                             # never fewer than five

    def test_groups_and_defaults(self):
        cat = small_catalog()
        self.assertEqual(D.group_values(cat, "country"), [("Hong Kong", 8), ("United States of America", 3)])
        self.assertEqual([v for v, _ in D.group_values(cat, "decade")], [1990, 2000])
        values = {k: D.group_values(cat, k) for k in ("library", "country", "genre", "decade")}
        self.assertEqual(D.default_groups(values), (("country", "United States of America"), ("country", "Hong Kong")))
        self.assertIsNone(D.default_groups({"country": [("X", 1)], "library": [], "genre": [], "decade": []}))
        names = D.people_directory(cat)["labels"]
        self.assertEqual(names[:4], ["Chiang", "Kuo", "Lu", "Sun"])           # five films each come first
        self.assertEqual(len(names), len(set(names)))
        # The first example: the biggest star, then a big star in the same part of the shelf they've not worked with
        from projectionist.costars import graph_for
        a, b = D.default_ids(cat)
        self.assertEqual(a, "p:Kuo")
        linked = set(graph_for(cat).layers("p:Kuo"))
        self.assertEqual(D.default_ids(cat, linked), ("p:Kuo", "p:Lu"))      # the gang only links to itself


class TabTestCase(unittest.TestCase):
    def setUp(self):
        from tkinter import ttk
        try:
            self.root = hidden_root()
        except Exception as exc:
            self.skipTest(f"Tk unavailable: {exc}")
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True)

    def tearDown(self):
        quiet_destroy(self.root)

    def make(self, catalog=None, state="ready", hold=False):
        app = FakeApp(catalog, state, hold)
        tab = D.Tab(app, self.notebook)
        self.notebook.add(tab.frame, text=tab.title)
        return app, tab

    def ready(self, hold=False):
        cat = small_catalog()
        app, tab = self.make(cat, hold=hold)
        tab.catalog_changed(cat, "ready")
        return app, tab, cat


class PlaceholderTests(TabTestCase):
    def test_placeholders_until_the_collection_is_ready(self):
        for state, expected in (("none", "No database yet"), ("loading", "Reading your collection..."),
                                ("error", "Couldn't read the collection")):
            app, tab = self.make(None, state)
            tab.catalog_changed(None, state)
            self.assertEqual(tab.modes.winfo_manager(), "", state)            # the modes are hidden
            self.assertIn(expected, texts(tab.placeholder), state)
            tab.shown()                                                       # nothing to do, nothing breaks
            tab.navigate(name="Ann")                                          # kept for later
            self.assertEqual(tab._pending["name"], "Ann")
        self.assertIn("the file is locked", texts(tab.placeholder))

    def test_a_navigation_waits_for_the_collection(self):
        cat = small_catalog()
        app, tab = self.make(None, "loading")
        tab.catalog_changed(None, "loading")
        tab.navigate(person_id="p:Bob", name="Bob")
        tab.catalog_changed(cat, "ready")
        self.assertEqual(tab.modes.winfo_manager(), "grid")
        self.assertEqual(str(tab.modes.cget("style")), "Inner.TNotebook")     # smaller tabs than the main ones
        tab.shown()
        self.assertEqual(tab.current_mode(), "Person")
        self.assertEqual(tab.person["name"], "Bob")
        self.assertIsNone(tab._pending)


class ConnectTests(TabTestCase):
    def test_first_visit_shows_an_example_chain(self):
        app, tab, cat = self.ready()
        tab.shown()
        self.assertEqual((tab.from_box.get(), tab.to_box.get()), ("Kuo", "Lu"))
        self.assertTrue(tab.chain["connected"])
        self.assertIsNotNone(tab._suggest)                                    # suggestions built on the first visit
        self.assertEqual(tab._suggest("ch")[:1], ["Chiang"])

    def test_connect_draws_the_chain_and_where_they_sit(self):
        app, tab, cat = self.ready()
        tab.shown()
        tab.from_box.set("Ann")
        tab.to_box.set("dan")
        tab.connect()
        answer = tab.chain
        self.assertEqual(answer["degrees"], 2)
        self.assertIn("2 steps", tab.connect_head.cget("text"))
        drawn = texts(tab.chain_view, 600, D.C.chain_height(1, 2))
        for name in ("Ann", "Eve", "Dan", "Four (1993)", "Five (1994)"):
            self.assertIn(name, drawn)
        self.assertIn("<Button-1>", tab.chain_view.tag_bind("person1"))    # people and films are clickable
        self.assertIn("<Button-1>", tab.chain_view.tag_bind("film0"))
        # beside it: everyone Ann reaches, Dan's step in blue
        self.assertEqual(tab.far_view.winfo_manager(), "grid")
        far = texts(tab.far_view, 360, 250)
        self.assertIn("Everyone Ann reaches", far)
        self.assertTrue(any("Dan is one of" in t for t in far), far)
        # '"dan" matched Dan' isn't worth a note (exact), and Eve is offered as a way round
        links = [w.cget("text") for row in tab.connect_notes.winfo_children() for w in row.winfo_children()]
        self.assertIn("Eve", links)
        # clicking: a person opens their profile, a film its page on the Film tab
        tab._chain_person("p:Eve", "Eve")
        self.assertEqual((tab.current_mode(), tab.person["name"]), ("Person", "Eve"))
        tab.open_film(answer["steps"][0]["film"])
        self.assertEqual(app.gone[-1], ("Film", {"title": "Four (1993)", "film_key": "f3"}))   # by key too

    def test_avoid_options_and_swap(self):
        app, tab, cat = self.ready()
        tab.from_box.set("Ann")
        tab.to_box.set("Dan")
        tab.connect()
        tab._avoid("p:Eve")                                                  # the 'avoid' link (by ID)
        self.assertEqual(tab.avoid_var.get(), "Eve")
        self.assertEqual([s["to"] for s in tab.chain["steps"]], ["Bob", "Cat", "Dan"])
        tab.avoid_var.set("")
        tab.connect_billing.set("Top 3 billed")
        tab._connect_again()                                                 # an option change searches again
        self.assertEqual(tab.chain["degrees"], 2)
        tab.swap()
        self.assertEqual((tab.from_box.get(), tab.to_box.get()), ("Dan", "Ann"))
        self.assertEqual(tab.chain["from"], "Dan")

    def test_not_linked_unknown_and_guessed_names(self):
        app, tab, cat = self.ready()
        tab.from_box.set("Ann")
        tab.to_box.set("Zed")
        tab.connect()
        self.assertIn("aren't linked", tab.connect_head.cget("text"))
        self.assertIn("Directors count too", tab.connect_sub.cget("text"))
        self.assertEqual(tab.far_view.winfo_manager(), "")
        self.assertIn("Not connected", texts(tab.chain_view))
        tab.connect_directors.set(True)                                      # Zoe directed Four and Six
        tab._connect_again()
        self.assertEqual([s["to"] for s in tab.chain["steps"]], ["Zoe", "Zed"])
        tab.to_box.set("Zzqxv")
        tab.connect()
        self.assertIn("No one called “Zzqxv”", tab.connect_head.cget("text"))
        tab.to_box.set("Chaing")                                             # a near spelling
        tab.connect()
        notes = [w.cget("text") for row in tab.connect_notes.winfo_children() for w in row.winfo_children()]
        self.assertTrue(any("closest spelling" in t for t in notes), notes)
        tab.connect_billing.set("Top 10 billed")
        tab._connect_again()                                                 # the box still says 'Chaing'...
        notes = link_texts(tab.connect_notes)
        self.assertTrue(any("closest spelling" in t for t in notes), notes)  # ...so the note stays
        tab.connect_billing.set("All roles")
        tab.to_box.set("")
        tab.connect()
        self.assertEqual(tab.connect_head.cget("text"), "Pick two people")

    def test_navigate_fills_in_connect(self):
        app, tab, cat = self.ready()
        tab.navigate(connect_from="Ann", connect_to="Dan", something_else=1)
        self.assertEqual(tab.current_mode(), "Connect")
        self.assertEqual(tab.chain["degrees"], 2)
        tab.navigate(connect_from="Bob")
        self.assertEqual(tab.from_box.get(), "Bob")
        self.assertEqual(tab.chain["from"], "Bob")                           # To was still filled in


class PersonTests(TabTestCase):
    def test_profile(self):
        app, tab, cat = self.ready()
        tab.select_mode("Person")
        self.root.update()                                                   # the mode change primes it
        self.assertEqual(tab.person["name"], "Kuo")                          # the biggest star, first
        tab.open_person(name="eve")
        self.assertEqual(tab.person["name"], "Eve")
        self.assertEqual(tab.person_name.cget("text"), "Eve")
        self.assertIn("Films on your shelf", texts(tab.person_tiles, 700, 78))
        circle = texts(tab.circle_view)
        for name in ("Eve", "Ann", "Dan", "Extra"):
            self.assertIn(name, circle)
        self.assertIn("<Button-1>", tab.circle_view.tag_bind("node1"))
        self.assertIn("Dan", texts(tab.costar_view, 400, 200))
        self.assertIn("1 step", texts(tab.reach_view, 400, 236))
        rows = [tab.person_films.rows[i] for i in tab.person_films.tree.get_children()]
        self.assertEqual([r["title"] for r in rows], ["Four", "Five"])       # by year
        tab.person_films.on_open(rows[1])
        self.assertEqual(app.gone[-1], ("Film", {"title": "Five (1994)", "film_key": "f4"}))
        tab.watch_person()
        self.assertEqual(app.gone[-1], ("Watch Next", {"person": "Eve", "person_id": "p:Eve"}))

    def test_directors_ids_and_connect_to(self):
        app, tab, cat = self.ready()
        tab.open_person(name="Zoe")                                          # directed two films, acted in none
        self.assertEqual(tab.person["films"], 2)
        # someone who mostly directs: the casts of their films count, and the page says so
        self.assertTrue(tab.person_directors.get())
        self.assertEqual({c["name"] for c in tab.person["top_costars"]}, {"Ann", "Eve", "Zed", "Yan"})
        self.assertTrue(any("They only direct here" in t for t in person_notes(tab)), person_notes(tab))
        roles = {tab.person_films.rows[i]["role"] for i in tab.person_films.tree.get_children()}
        self.assertEqual(roles, {"Director"})
        self.assertIn("Six Degrees: Zoe - 2 films, 4 co-stars", app.status)
        tab.open_person("p:Ann")                                             # an actor: back to acting only
        self.assertFalse(tab.person_directors.get())
        # once the box is clicked it's the user's choice: unticked, a director's profile offers to count them
        tab.person_directors.set(False)
        tab.open_person(name="Zoe")
        self.assertFalse(tab.person_directors.get())
        self.assertIn("Count the films they directed too", person_notes(tab))
        self.assertIn("No co-stars on your shelf", texts(tab.costar_view))
        tab._count_directors()
        self.assertEqual({c["name"] for c in tab.person["top_costars"]}, {"Ann", "Eve", "Zed", "Yan"})
        tab.person_directors.set(False)
        tab._costar_clicked("p:Cat")                                         # by ID
        self.assertEqual(tab.person["name"], "Cat")
        tab._node_clicked("p:Cat", "Cat")                                   # the centre itself: nothing to do
        tab.to_box.set("Cat")
        tab.connect_from_person()
        self.assertEqual(tab.current_mode(), "Connect")
        self.assertEqual((tab.from_box.get(), tab.to_box.get()), ("Cat", ""))
        tab.open_person(name="Nobody Atall")
        self.assertIn("No one called “Nobody Atall” in your collection", texts(tab.circle_view))
        self.assertIsNone(tab.person)
        self.assertTrue(busy(tab.person_connect_btn))                       # nobody to connect from

    def test_film_clicks_work_even_when_goto_cannot_pass_a_title(self):
        """An App.goto(self, title, **kwargs) can't take title=... as well: the tab finds the Film tab itself."""
        from tkinter import ttk
        opened = []

        class Credits:
            title = "Film"

            def __init__(self, parent):
                self.frame = ttk.Frame(parent)

            def navigate(self, **kwargs):
                opened.append(kwargs)

        class ClashingApp(FakeApp):
            def goto(self, title, **kwargs):          # the signature in gui.py
                return None

        cat = small_catalog()
        app = ClashingApp(cat)
        tab = D.Tab(app, self.notebook)
        credits = Credits(self.notebook)
        self.notebook.add(tab.frame, text=tab.title)
        self.notebook.add(credits.frame, text="Film")
        app.tabs, app.notebook = [tab, credits], self.notebook
        tab.catalog_changed(cat, "ready")
        tab.open_film({"title": "Four", "year": 1993})
        self.assertEqual(opened, [{"title": "Four (1993)"}])
        self.assertEqual(self.notebook.select(), str(credits.frame))

    def test_navigate_opens_a_profile(self):
        app, tab, cat = self.ready()
        tab.navigate(person_id="p:Dan", name="whoever")
        self.assertEqual((tab.current_mode(), tab.person["name"]), ("Person", "Dan"))
        tab.navigate(name="Lu")
        self.assertEqual(tab.person["name"], "Lu")
        tab.navigate(person_id="p:gone", name="Ann")                         # an unknown ID falls back to the name
        self.assertEqual(tab.person["name"], "Ann")


def namesake_catalog():
    """small_catalog() with shared names: Kuo (5 films) is renamed Eve, so a typed 'Eve' finds him rather than the
    Eve who links Ann and Dan (2 films); and Extra (1 film) is renamed Dan."""
    return with_namesake(with_namesake(small_catalog(), "p:Kuo", "Eve"), "p:Extra", "Dan")


class AskByIdTests(unittest.TestCase):
    """ask's person and connect requests: two people can share a name, so they take (and give back) IDs."""

    def test_ids_in_and_out(self):
        from projectionist.ask import handle
        cat = namesake_catalog()
        ask = lambda **r: handle(r, catalog=cat)
        eve = ask(action="person", name="Eve")
        self.assertEqual((eve["id"], eve["matched"]["id"]), ("p:Kuo", "p:Kuo"))       # the one with more films
        self.assertEqual(eve["matched"]["other_ids"][0], "p:Eve")
        other = ask(action="person", id="p:Eve")
        self.assertEqual((other["name"], other["matched"]["other_ids"]), ("Eve", ["p:Kuo"]))   # namesakes listed
        self.assertEqual(ask(action="person", name="Evee")["id"], "p:Kuo")      # a guess: the most films first too
        chain = ask(action="connect", **{"from": "Ann", "to": "Dan", "to_id": "p:Dan"})
        self.assertEqual((chain["from_id"], chain["to_id"]), ("p:Ann", "p:Dan"))
        self.assertEqual([s["to_id"] for s in chain["steps"]], ["p:Eve", "p:Dan"])
        by_name = ask(action="connect", **{"from": "Ann", "to": "Dan", "avoid": ["Eve"]})
        self.assertEqual([s["to_id"] for s in by_name["steps"]], ["p:Eve", "p:Dan"])   # 'Eve' is Kuo: no help
        by_id = ask(action="connect", **{"from": "Ann", "to": "Dan", "avoid_ids": ["p:Eve"]})
        self.assertEqual([s["to"] for s in by_id["steps"]], ["Bob", "Cat", "Dan"])
        self.assertEqual(by_id["avoided"], [{"asked": "p:Eve", "found": "Eve", "how": "exact", "id": "p:Eve"}])
        loose = ask(action="connect", **{"from": "Ann", "to": "Dan", "avoid": ["Bo"]})
        self.assertEqual(loose["avoided"][0]["how"], "guess")
        missing = ask(action="connect", **{"from": "Ann", "to": "Zzqxv"})
        self.assertEqual((missing["ok"], missing["side"]), (False, "to"))
        self.assertIn("suggestion_ids", missing)
        self.assertFalse(ask(action="connect", **{"from": "Ann", "to": "Dan", "avoid_ids": ["p:gone"]})["ok"])
        film = chain["steps"][0]["film"]
        self.assertEqual((film["title"], film["key"]), ("Four", "f3"))                  # films carry their key

    def test_directors_auto(self):
        from projectionist.ask import handle
        cat = small_catalog()
        zoe = handle({"action": "person", "name": "Zoe", "include_directors": "auto"}, catalog=cat)
        self.assertEqual((zoe["include_directors"], zoe["directors_auto"], zoe["distinct_costars"]), (True, True, 4))
        ann = handle({"action": "person", "name": "Ann", "include_directors": "auto"}, catalog=cat)
        self.assertEqual((ann["include_directors"], ann["directors_auto"]), (False, True))
        plain = handle({"action": "person", "name": "Zoe"}, catalog=cat)
        self.assertEqual((plain["include_directors"], plain["distinct_costars"]), (False, 0))
        chain = handle({"action": "connect", "from": "Zoe", "to": "Dan", "include_directors": "auto"}, catalog=cat)
        self.assertTrue(chain["connected"] and chain["include_directors"])
        self.assertFalse(handle({"action": "connect", "from": "Zoe", "to": "Dan"}, catalog=cat)["connected"])


class NamesakeTests(TabTestCase):
    def ready_namesakes(self):
        cat = namesake_catalog()
        app, tab = self.make(cat)
        tab.catalog_changed(cat, "ready")
        tab.shown()                                    # builds the suggestions (at once, with FakeApp)
        return app, tab, cat

    def test_suggestions_tell_them_apart(self):
        app, tab, cat = self.ready_namesakes()
        self.assertEqual(set(tab._suggest("da")), {"Dan (2 films, e.g. Five)", "Dan (1 film: Five)"})
        tab.to_box.set("Dan (1 film: Five)")                                 # picked from the list
        self.assertEqual(tab._typed_person("to"), ("Dan", "p:Extra"))
        tab.from_box.set("Ann")
        tab.connect()
        self.assertEqual(tab.chain["to_id"], "p:Extra")

    def test_a_typed_shared_name_says_so_and_offers_the_other(self):
        app, tab, cat = self.ready_namesakes()
        tab.from_box.set("Ann")
        tab.to_box.set("Dan")
        tab.connect()
        self.assertEqual(tab.chain["to_id"], "p:Dan")
        notes = link_texts(tab.connect_notes)
        self.assertTrue(any(t.startswith("There are 2 people called Dan - this chain ends with the one in")
                            for t in notes), notes)
        click(links_in(tab.connect_notes)["Dan (1 film: Five)"])
        self.assertEqual((tab.to_box.get(), tab.chain["to_id"]), ("Dan (1 film: Five)", "p:Extra"))
        # the Person box too, and a near spelling offers the namesake by their films
        tab.person_box.set("Eve")
        tab.show_typed()
        self.assertEqual(tab.person["id"], "p:Kuo")
        self.assertEqual(tab.person_box.get(), "Eve (5 films, e.g. Gang 0)")
        click(links_in(tab.person_notes)["Eve (2 films, e.g. Five)"])
        self.assertEqual(tab.person["id"], "p:Eve")
        tab.person_box.set("Evee")
        tab.show_typed()
        self.assertIn("Did you mean", " ".join(person_notes(tab)))
        click(links_in(tab.person_notes)["Eve (2 films, e.g. Five)"])
        self.assertEqual(tab.person["id"], "p:Eve")

    def test_connect_to_watch_next_and_avoid_keep_the_person(self):
        app, tab, cat = self.ready_namesakes()
        tab.open_person("p:Eve", "Eve")                                      # the lesser-known Eve, by a click
        self.assertTrue(any("There are 2 people called Eve - this is the one in" in t for t in person_notes(tab)))
        tab.watch_person()
        self.assertEqual(app.gone[-1], ("Watch Next", {"person": "Eve", "person_id": "p:Eve"}))
        tab.connect_from_person()
        self.assertEqual(tab.from_box.get(), "Eve (2 films, e.g. Five)")
        tab.to_box.set("Bob")
        tab.connect()
        self.assertEqual(tab.chain["from_id"], "p:Eve")                      # not the Eve with more films
        tab.connect_billing.set("Top 10 billed")
        tab._connect_again()
        self.assertEqual(tab.chain["from_id"], "p:Eve")
        tab.swap()
        self.assertEqual((tab.chain["from_id"], tab.chain["to_id"]), ("p:Bob", "p:Eve"))
        # a chain through the lesser Eve: 'avoid' avoids that Eve, not the other one
        tab.from_box.set("Ann")
        tab.to_box.set("Dan")
        tab.connect()
        self.assertEqual([s["to_id"] for s in tab.chain["steps"]], ["p:Eve", "p:Dan"])
        click(links_in(tab.connect_notes)["Eve (2 films, e.g. Five)"])
        self.assertEqual(tab.avoid_var.get(), "Eve (2 films, e.g. Five)")
        self.assertEqual([s["to"] for s in tab.chain["steps"]], ["Bob", "Cat", "Dan"])
        tab.connect_billing.set("All roles")
        tab._connect_again()                                                 # still avoided, by ID
        self.assertNotIn("p:Eve", [s["to_id"] for s in tab.chain["steps"]])


class DirectorTests(TabTestCase):
    def test_a_director_opened_from_elsewhere_counts_the_films_they_directed(self):
        app, tab, cat = self.ready()
        tab.navigate(person_id="p:Zoe", name="Zoe")                          # as Overview's directors chart does
        self.assertEqual((tab.person["distinct_costars"], tab.person_directors.get()), (4, True))
        tab.navigate(person_id="p:Ann", name="Ann", directors=True)          # a caller can ask for it anyway
        self.assertTrue(tab.person["include_directors"])
        tab.navigate(person_id="p:Bob", name="Bob")
        self.assertFalse(tab.person_directors.get())                         # the next actor: acting only

    def test_connect_counts_directors_for_a_director(self):
        app, tab, cat = self.ready()
        tab.from_box.set("Zoe")
        tab.to_box.set("Dan")
        tab.connect()
        self.assertTrue(tab.chain["connected"])
        self.assertTrue(tab.connect_directors.get())
        self.assertIn("Directors count too, since Zoe mostly directs.", link_texts(tab.connect_notes))
        tab.connect_directors.set(False)                                     # the user unticks it
        tab.connect()
        self.assertFalse(tab.chain["connected"])
        self.assertIn("Zoe only directs here", texts(tab.chain_view))       # not 'separate corners'
        click(links_in(tab.connect_notes)["Count directors too"])
        self.assertTrue(tab.chain["connected"])


class LinkLineTests(TabTestCase):
    def test_links_that_would_run_off_the_edge_go_on_to_the_next_line(self):
        from tkinter import ttk
        from projectionist.ui.widgets import LinkLabel
        app, tab, cat = self.ready()
        frame = ttk.Frame(self.root)                   # never laid out: the tab takes it as 700 px wide
        names = [f"Somebody With A Long Name {i} (12 films, e.g. Something Long)" for i in range(5)]
        tab._link_line(frame, "Did you mean", [(n, lambda: None) for n in names])
        rows = frame.winfo_children()
        self.assertGreater(len(rows), 1)
        self.assertEqual([w.cget("text") for r in rows for w in r.winfo_children() if isinstance(w, LinkLabel)],
                         names)                                              # all there, in order
        for row in rows[1:]:
            self.assertIsInstance(row.winfo_children()[0], LinkLabel)      # a new line doesn't start with a dot


class StatusTests(TabTestCase):
    """Every answer leaves a finished status line - never 'Connecting...' or 'Looking up...'."""

    def test_errors_and_odd_answers_finish_the_status(self):
        app, tab, cat = self.ready()
        tab.open_person(name="Zzqxv Blorp")
        # said as every tab says it (no tab name: the Credits tab has none)
        self.assertEqual(app.status, "No one called “Zzqxv Blorp” in your collection.")
        tab.from_box.set("Ann")
        tab.to_box.set("ann")
        tab.connect()
        self.assertIn("pick two different people", app.status)
        tab.to_box.set("Zzqxv")
        tab.connect()
        self.assertEqual(app.status, "No one called “Zzqxv” in your collection.")
        self.assertEqual(tab.connect_head.cget("text"), "No one called “Zzqxv” in your collection")
        tab._start("person", lambda catalog: 1 / 0, tab._show_person)
        self.assertEqual(app.status, "Six Degrees: couldn't show that person.")
        tab._start("bridges", lambda catalog: 1 / 0, lambda answer: tab._show_bridges(answer, ("library", "Asia"),
                                                                                         ("library", "West")))
        self.assertEqual(app.status, "Six Degrees: couldn't find bridges.")


class OptionTests(TabTestCase):
    def test_the_clock_beside_most_connected_is_never_cut(self):
        """'Working it out... 12 s' is never cut. In a narrow window the progress bar gives way first (with
        Windows' Segoe UI, in the smallest window, 900 px, the bar is about 50 px short); where that isn't enough -
        fonts wider than Segoe UI, as Linux's usually are - the progress bar and the clock go on a line of their
        own under the controls. Measured with the fonts this computer has, at every width."""
        import tkinter as tk
        from tkinter import ttk
        holder = tk.Frame(self.root)                   # a fixed size, laid out although the window is hidden
        holder.place(x=0, y=0, width=900, height=620)
        notebook = ttk.Notebook(holder)
        notebook.pack(fill="both", expand=True)
        cat = small_catalog()
        app = FakeApp(cat, hold=True)
        tab = D.Tab(app, notebook)
        notebook.add(tab.frame, text=tab.title)
        tab.catalog_changed(cat, "ready")
        tab.select_mode("Most connected")
        tab.work_out_center()
        tab.center_wait.configure(text="Working it out... 12 s")
        clock, progress, bar = tab.center_wait, tab.center_progress, tab.center_bar
        full, least = int(110 * tab.s), int(tab.CENTER_LEAST_PROGRESS * tab.s)
        for width in (900, 960, 1040, 1120, 1280, 900):
            holder.place_configure(width=int(width * tab.s), height=int(620 * tab.s))
            self.root.update_idletasks()
            tab._fit_center_bar()
            self.root.update_idletasks()
            self.assertEqual(clock.winfo_width(), clock.winfo_reqwidth(), width)
            self.assertLessEqual(clock.winfo_x() + clock.winfo_width(), bar.winfo_width(), width)     # never cut
            if tab._center_wrapped:                                       # on a line of its own: its full length
                self.assertEqual(progress.winfo_width(), full, width)
                self.assertGreater(clock.winfo_y(), tab.center_btn.winfo_y(), width)
            else:                                                         # in the row: given way, never gone
                self.assertGreaterEqual(progress.winfo_width(), least, width)
                self.assertEqual(clock.winfo_y() > tab.center_btn.winfo_y() + tab.center_btn.winfo_height(), False)
        holder.place_configure(width=int(1280 * tab.s))                   # room to spare: its full length, in the row
        self.root.update_idletasks()
        tab._fit_center_bar()
        self.root.update_idletasks()
        self.assertFalse(tab._center_wrapped)
        self.assertEqual(progress.winfo_width(), full)
        tab._cancel("center")
        self.assertFalse(progress.winfo_manager())                        # (hidden again once it's done)

    def test_the_clock_at_900_px_as_on_windows(self):
        """With Windows' own font the smallest window keeps them in the row, the progress bar giving way."""
        if sys.platform != "win32":
            self.skipTest("Segoe UI's widths")
        import tkinter as tk
        from tkinter import ttk
        holder = tk.Frame(self.root)                   # a fixed size, laid out although the window is hidden
        holder.place(x=0, y=0, width=900, height=620)
        notebook = ttk.Notebook(holder)
        notebook.pack(fill="both", expand=True)
        cat = small_catalog()
        app = FakeApp(cat, hold=True)
        tab = D.Tab(app, notebook)
        notebook.add(tab.frame, text=tab.title)
        tab.catalog_changed(cat, "ready")
        holder.place_configure(width=int(900 * tab.s), height=int(620 * tab.s))
        tab.select_mode("Most connected")
        tab.work_out_center()
        tab.center_wait.configure(text="Working it out... 12 s")
        self.root.update_idletasks()
        clock, progress = tab.center_wait, tab.center_progress
        self.assertFalse(tab._center_wrapped)                                 # (in the row, as it always was)
        self.assertEqual(clock.winfo_width(), clock.winfo_reqwidth())
        self.assertLess(progress.winfo_width(), int(110 * tab.s))            # it gave way
        self.assertGreater(progress.winfo_width(), int(40 * tab.s))           # ...but it's still there
        holder.place_configure(width=int(1280 * tab.s))                       # room to spare: its full length
        self.root.update_idletasks()
        self.assertEqual(clock.winfo_width(), clock.winfo_reqwidth())
        self.assertEqual(progress.winfo_width(), int(110 * tab.s))
        tab._cancel("center")

    def test_most_connected_says_what_it_worked_out(self):
        app, tab, cat = self.ready(hold=True)
        tab.center_count.set("50")
        tab.work_out_center()
        tab.center_count.set("200")                     # changed while it works (as the box allows)
        app.finish(app.held.pop())
        self.assertTrue(tab.center_note.cget("text").startswith("Among the 50 people with the most films, "))
        tab.center_billing.set("Top 3 billed")
        tab._center_again()                              # an option change works it out again, as elsewhere
        self.assertTrue(busy(tab.center_btn))
        app.finish(app.held.pop())
        self.assertIn("Among the 200 people with the most films (counting roles billed in the top 3)",
                      tab.center_note.cget("text"))
        self.assertFalse(busy(tab.center_btn))

    def test_bridges_same_group_clears_the_page_and_the_answer_on_its_way(self):
        import tkinter.font as tkfont
        from tkinter import ttk
        app, tab, cat = self.ready(hold=True)
        tab.select_mode("Bridges")
        self.root.update()
        self.assertTrue(busy(tab.bridge_btn))
        app.finish(app.held.pop())                                          # United States vs Hong Kong
        self.assertIn("people have films on each side", tab.bridge_note.cget("text"))
        font = tkfont.Font(font=ttk.Style(self.root).lookup("Treeview.Heading", "font") or "TkHeadingFont")
        tree = tab.bridge_people.tree
        self.assertGreaterEqual(tree.column("a", "width"), font.measure("United States ▼"))   # heading fits
        # ...and in a narrow window the wider side columns squeeze 'For example', not the names
        self.assertGreaterEqual(int(tree.column("name", "minwidth")), int(120 * tab.s))
        self.assertLessEqual(int(tree.column("examples", "minwidth")), int(20 * tab.s) + 1)
        tab.bridge_billing.set("Top 3 billed")
        tab.find_bridges()                                                  # on its way...
        tab.group_value["b"].set(tab.group_value["a"].get())
        tab.find_bridges()                                                  # ...when both sides become the same
        self.assertTrue(app.held[-1][0].cancelled)                          # called off
        self.assertEqual(app.held[-1][0].key, "degrees.bridges")
        self.assertFalse(busy(tab.bridge_btn))
        self.assertEqual(tab.bridge_note.cget("text"), D.BRIDGE_INTRO)
        self.assertEqual(tab.bridge_people.rows, {})
        self.assertIn("pick two different groups", app.status)
        app.finish(app.held.pop())                                          # arrives late: dropped
        self.assertIsNone(tab.bridge)
        self.assertEqual(tab.bridge_note.cget("text"), D.BRIDGE_INTRO)
        # more people than the table holds: its title says so
        answer = {"ok": True, "people_in_both": 3, "films_only_in_a": 1, "films_only_in_b": 1,
                  "people": [{"id": "p:Cat", "name": "Cat", "films_in_a": 1, "films_in_b": 1}]}
        tab._show_bridges(answer, ("library", "Asia"), ("library", "West"))
        self.assertEqual(tab.bridge_people_title.cget("text"), "The first 1 of the 3 people with films on both sides")


class RolesApp(FakeApp):
    """FakeApp with settings, as the main window keeps them: a change is passed on to the tabs."""

    def __init__(self, catalog=None, settings=None, **kw):
        super().__init__(catalog, **kw)
        self.settings = dict(settings or {})
        self.tabs = []

    def save_settings(self):
        pass

    def preference_changed(self, key, value):
        for tab in self.tabs:
            tab.preference_changed(key, value)


class RolesSettingTests(TabTestCase):
    """Settings > Six Degrees > 'Roles that count, to start with': where the four modes' boxes start; a change sets
    them all and works out the mode on show again."""

    def make_with(self, settings=None):
        cat = small_catalog()
        app = RolesApp(cat, settings)
        tab = D.Tab(app, self.notebook)
        self.notebook.add(tab.frame, text=tab.title)
        app.tabs.append(tab)
        tab.catalog_changed(cat, "ready")
        self.asked = []
        ask = tab._ask

        def recorded(request):
            self.asked.append(request)
            return ask(request)
        tab._ask = recorded
        return app, tab

    @staticmethod
    def boxes(tab):
        return [var.get() for var in tab._role_boxes().values()]

    def test_the_setting(self):
        from projectionist import prefs
        p = prefs.pref(D.ROLES)
        self.assertEqual((p.section, p.label, p.kind, p.default, p.live),
                         ("Six Degrees", "Roles that count, to start with", "choice", 0, True))
        self.assertEqual(p.choices_for(None), [(0, "All roles"), (3, "Top 3 billed"), (5, "Top 5 billed"),
                                               (10, "Top 10 billed")])
        self.assertEqual(prefs.get(None, D.ROLES), 0)                     # all roles, as it always was
        for saved, read in ((5, 5), ("Top 3 billed", 3), ("10", 10), (None, 0), (7, 0), (True, 0), ("lots", 0)):
            self.assertEqual(prefs.get({D.ROLES: saved}, D.ROLES), read, saved)
        self.assertEqual([D.roles_label(v) for v in (0, 3, 10, 99)],
                         ["All roles", "Top 3 billed", "Top 10 billed", "All roles"])
        self.assertIn("Troupes keeps its own", p.help)
        self.assertEqual(set(D.ROLE_MODES), set(D.MODES) - {"Troupes"})

    def test_the_boxes_start_there(self):
        app, tab = self.make_with()
        self.assertEqual(self.boxes(tab), ["All roles"] * 4)
        app, tab = self.make_with({D.ROLES: 5})
        self.assertEqual(self.boxes(tab), ["Top 5 billed"] * 4)
        self.assertEqual(tab.troupe_billing.get(), "8")                   # Troupes keeps its own
        tab.shown()                                                       # Connect's example: the top 5 only
        self.assertEqual(tab.chain["max_billing"], 5)
        tab.open_person("p:Dan", "Dan")
        self.assertEqual(self.asked[-1]["max_billing"], 5)

    def test_a_change_sets_every_box_and_works_out_the_mode_on_show(self):
        from projectionist import prefs
        app, tab = self.make_with()
        tab.shown()                                                       # Connect: its example chain
        self.assertTrue(tab.chain)
        prefs.set(app, D.ROLES, 3)
        self.assertEqual(self.boxes(tab), ["Top 3 billed"] * 4)
        self.assertEqual(tab.chain["max_billing"], 3)                     # Connect again, at once
        self.assertEqual(tab._stale, {"Person", "Most connected", "Bridges"})
        # a mode not looked at yet simply starts with them
        tab.select_mode("Person")
        self.root.update()
        self.assertEqual((self.asked[-1]["action"], self.asked[-1]["max_billing"]), ("person", 3))
        self.assertNotIn("Person", tab._stale)
        # each mode's own box still changes its own mode (the setting stays as it was)
        tab.person_billing.set("All roles")
        tab._person_again()
        self.assertIsNone(self.asked[-1]["max_billing"])
        self.assertEqual(self.boxes(tab)[0], "Top 3 billed")
        self.assertEqual(app.settings[D.ROLES], 3)
        # ...until the setting changes again: the mode on show at once, the one left behind when it's back
        prefs.set(app, D.ROLES, 10)
        self.assertEqual((self.asked[-1]["action"], self.asked[-1]["max_billing"]), ("person", 10))
        self.assertIn("Connect", tab._stale)
        tab.select_mode("Connect")
        self.root.update()
        self.assertEqual(tab.chain["max_billing"], 10)
        self.assertNotIn("Connect", tab._stale)
        # Most connected and Bridges, once they've something on show
        for mode, action, first, then in (("Most connected", "center", 10, 3), ("Bridges", "bridges", 3, 5)):
            tab.select_mode(mode)
            self.root.update()
            self.assertEqual((self.asked[-1]["action"], self.asked[-1]["max_billing"]), (action, first), mode)
            prefs.set(app, D.ROLES, then)
            self.assertEqual((self.asked[-1]["action"], self.asked[-1]["max_billing"]), (action, then), mode)
        self.assertEqual(tab._center_asked[1], 3)
        # another setting changes nothing here
        count = len(self.asked)
        tab.preference_changed("csv", True)
        self.assertEqual(len(self.asked), count)

    def test_while_another_tab_is_in_front(self):
        from projectionist import prefs
        app, tab = self.make_with()
        tab.shown()
        app.current_tab = lambda: None                                    # (the Settings tab, say)
        prefs.set(app, D.ROLES, 5)
        self.assertEqual(self.boxes(tab), ["Top 5 billed"] * 4)
        self.assertIsNone(tab.chain.get("max_billing"))                  # (the example: all roles)
        app.current_tab = lambda: tab
        tab.shown()                                                       # back: Connect again with them
        self.assertEqual(tab.chain["max_billing"], 5)
        # a newly read collection starts afresh (with the setting)
        tab.catalog_changed(small_catalog(), "ready")
        self.assertEqual(tab._stale, set())
        self.assertEqual(self.boxes(tab), ["Top 5 billed"] * 4)

    def test_on_the_settings_tab(self):
        from projectionist import prefs
        from projectionist.ui import settings as S
        app, tab = self.make_with()
        settings = S.Tab(app, self.notebook)
        self.notebook.add(settings.frame, text=settings.title)
        app.tabs.append(settings)
        control = settings.controls[D.ROLES]
        self.assertIsInstance(control, S.ChoiceControl)
        self.assertEqual([b.cget("text") for b in control.buttons], list(D.BILLING))
        control.buttons[2].invoke()
        self.assertEqual(app.settings[D.ROLES], 5)
        self.assertEqual(self.boxes(tab), ["Top 5 billed"] * 4)
        prefs.reset(app, section="Six Degrees")
        self.assertEqual(self.boxes(tab), ["All roles"] * 4)
        self.assertEqual(control.var.get(), "0")


class CenterBridgesTroupesTests(TabTestCase):
    def test_most_connected(self):
        app, tab, cat = self.ready()
        tab.center_count.set("50")
        tab.work_out_center()
        people = tab.center["people"]
        self.assertEqual([p["name"] for p in people[:2]], ["Dan", "Eve"])
        drawn = texts(tab.center_view, 500, 300)
        self.assertIn("Dan", drawn)
        self.assertIn("1.40", drawn)                                         # two decimals
        rows = [tab.center_table.rows[i] for i in tab.center_table.tree.get_children()]
        self.assertEqual(rows[0]["avg"], "1.40")
        self.assertTrue(any("separate group" in r["name"] for r in rows))     # the gang and Zed's island
        self.assertFalse(busy(tab.center_btn))
        self.assertEqual(tab.center_progress.winfo_manager(), "")           # the progress bar is put away
        self.assertIn("best connected", tab.center_note.cget("text"))
        tab.center_table.on_open(rows[1])
        self.assertEqual(tab.person["name"], "Eve")

    def test_bridges(self):
        app, tab, cat = self.ready()
        tab.select_mode("Bridges")
        self.root.update()
        self.assertEqual(tab._group("a"), ("country", "United States of America"))
        self.assertEqual(tab._group("b"), ("country", "Hong Kong"))
        self.assertEqual({p["name"] for p in tab.bridge["people"]}, {"Cat", "Eve"})
        drawn = texts(tab.bridge_view, 500, 200)
        self.assertIn("United States", drawn)
        self.assertIn("Hong Kong", drawn)
        self.assertIn("3 United States films and 8 Hong Kong films", tab.bridge_note.cget("text"))
        self.assertEqual(tab.bridge_people.tree.heading("a", "text"), "United States")
        tab.bridge_people.on_open(tab.bridge_people.rows["0"])
        self.assertEqual(tab.current_mode(), "Person")
        # libraries; then the same group twice
        tab.group_kind["a"].set("Library")
        tab._kind_changed("a")
        tab.group_kind["b"].set("Library")
        tab._fill_values("b")
        self.assertNotEqual(tab._group("a"), tab._group("b"))
        tab.find_bridges()
        self.assertEqual({p["name"] for p in tab.bridge["people"]}, {"Cat", "Eve"})
        tab.group_value["b"].set(tab.group_value["a"].get())
        tab.find_bridges()
        self.assertIn("Pick two different groups", texts(tab.bridge_view))
        tab.group_kind["a"].set("Genre")
        tab._fill_values("a", prefer="Comedy")
        tab.group_kind["b"].set("Genre")
        tab._fill_values("b", prefer="Action")
        tab.find_bridges()
        self.assertIn("Nobody has films on both sides", texts(tab.bridge_view))

    def test_troupes(self):
        app, tab, cat = self.ready()
        tab.select_mode("Troupes")
        self.root.update()
        troupe = tab.troupes["troupes"][0]
        self.assertEqual(troupe["members"], ["Chiang", "Kuo", "Lu", "Sun"])
        self.assertEqual(tab.troupe_table.selected()["troupe"], troupe)     # the first is picked and shown
        self.root.update()
        self.assertIn("Kuo", texts(tab.troupe_view, 400, 260))
        films = [tab.troupe_films.rows[i]["title"] for i in tab.troupe_films.tree.get_children()]
        self.assertEqual(films, [f"Gang {i}" for i in range(5)])
        tab.troupe_films.on_open(tab.troupe_films.rows["0"])
        self.assertEqual(app.gone[-1], ("Film", {"title": "Gang 0 (2000)", "film_key": "f6"}))
        tab.troupe_shared.set("40")                                          # clamped to 15: nobody
        tab.find_troupes()
        self.assertEqual(tab.troupe_shared.get(), "15")
        self.assertIn("No troupes with these settings", texts(tab.troupe_view))
        tab._troupe_member("p:Lu", "Lu")
        self.assertEqual(tab.person["name"], "Lu")


class BackgroundTests(TabTestCase):
    def test_every_kind_of_request_has_its_own_key(self):
        app, tab, cat = self.ready()
        tab.shown()                                     # the name suggestions, and Connect's first example
        tab.open_person(name="Ann")
        tab.work_out_center()
        tab.select_mode("Bridges")
        tab.find_bridges()
        tab.find_troupes()
        self.assertEqual({k for k in app.keys if k}, {"degrees.connect", "degrees.person", "degrees.center",
                                                      "degrees.bridges", "degrees.troupes", "degrees.directory"})

    def test_the_directory_and_the_example_stop_when_called_off(self):
        cat = small_catalog()
        job = jobs.Job("degrees.directory")
        job.cancel()
        with jobs.running(job):
            self.assertRaises(jobs.Cancelled, D.people_directory, cat)
            self.assertRaises(jobs.Cancelled, D.default_ids, cat)
        self.assertNotIn(("degrees.directory",), cat.cache)                  # nothing half-built is kept
        self.assertEqual(D.default_ids(cat)[0], D.default_ids(cat)[0])         # (outside a job: as before)
        self.assertIn("labels", D.people_directory(cat))


    def test_stale_answers_are_dropped_and_buttons_come_back(self):
        app, tab, cat = self.ready(hold=True)
        tab.open_person(name="Ann")
        self.assertTrue(busy(tab.person_btn))                              # busy while it works
        tab.open_person(name="Bob")
        first, second = app.held[-2], app.held[-1]
        self.assertEqual(app.keys[-2:], ["degrees.person", "degrees.person"])
        self.assertTrue(first[0].cancelled)                               # called off: it stops working
        self.assertTrue(busy(tab.person_btn))                              # ...and the newer one keeps it busy
        app.finish(second)
        self.assertEqual(tab.person["name"], "Bob")
        self.assertFalse(busy(tab.person_btn))
        app.finish(first)                                                  # arrives late: ignored
        self.assertEqual(tab.person["name"], "Bob")
        first[0].cancelled = False                                         # even if it did answer, the token
        app.finish(first)                                                  # drops it
        self.assertEqual(tab.person["name"], "Bob")
        # the long one: a progress bar and a running clock while it works
        tab.work_out_center()
        self.assertTrue(busy(tab.center_btn))
        self.assertEqual(tab.center_progress.winfo_manager(), "grid")
        self.assertIn("Working it out", tab.center_wait.cget("text"))
        # a new collection drops whatever's still running, and frees the buttons
        tab.from_box.set("Ann")
        tab.to_box.set("Dan")
        tab.connect()
        self.assertTrue(busy(tab.connect_btn))
        running = list(app.held)
        tab.catalog_changed(small_catalog(), "ready")
        self.assertTrue(all(job.cancelled or job.ended for job, *_ in running))   # every one called off
        self.assertFalse(busy(tab.connect_btn))
        self.assertFalse(busy(tab.center_btn))
        self.assertEqual(tab.center_progress.winfo_manager(), "")
        for job in running:
            app.finish(job)
        self.assertIsNone(tab.chain)
        self.assertIsNone(tab.person)
        self.assertIsNone(tab.center)

    def test_a_failed_job_says_so(self):
        app, tab, cat = self.ready()
        tab._start("person", lambda catalog: 1 / 0, tab._show_person, busy=tab._button_busy(tab.person_btn))
        self.assertIn("ZeroDivisionError", texts(tab.circle_view)[1])
        self.assertFalse(busy(tab.person_btn))


if __name__ == "__main__":
    unittest.main()
