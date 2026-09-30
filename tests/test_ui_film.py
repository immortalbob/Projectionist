"""Tests for the Film tab and the main window's search box (projectionist/ui/film.py). Fully headless: every window
is hidden, and the app's background runner is replaced by one that runs the work straight away (or holds it, to
test answers that come late). Nothing is ever shown: no drop-down, menu or window is deiconified."""

import os
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from projectionist import filmpage as FP  # noqa: E402
from projectionist import jobs  # noqa: E402
from projectionist.ui import film as F  # noqa: E402
from test_projectionist import build_fixture, make_catalog  # noqa: E402
from support import may_show_windows  # noqa: E402

HOOK_MODULES = ("projectionist.critics", "projectionist.habits", "projectionist.doctor")


def hidden_root():
    import tkinter as tk
    from projectionist.ui import theme
    root = tk.Tk()
    root.withdraw()            # headless: never show a window
    theme.apply_styles(root)
    return root


def fake_module(name, **functions):
    module = types.ModuleType(name)
    for k, v in functions.items():
        setattr(module, k, v)
    return module


def catalog():
    """Films with and without ratings, several copies, credits scenes, a studio and a shared cast."""
    from projectionist.catalog import CopyCredits
    from projectionist.credits import CreditsInfo, CreditsScene
    usa = ["United States of America"]
    specs = [dict(title=f"Rated {i}", year=1980 + i, genres=["Action"], directors=["Ann Auteur"],
                  cast=["Star A", f"Extra {i}"], imdb_rating=7.0, owner_rating=8.0 if i % 2 else 5.0,
                  studio="Big Studio", countries=usa) for i in range(34)]
    specs += [
        dict(title="Alpha", year=2001, genres=["Action", "Horror"], countries=usa + ["Hong Kong"],
             directors=["Ann Auteur"], cast=["Star A", "Bob", "Cat"], studio="Big Studio",
             collections=["Faves"], imdb_rating=7.9, tmdb_rating=7.5, rt_critic=91, rt_audience=88,
             titles=["Alpha", "阿爾法"], tagline="It begins.", summary="A film about beginnings.", runtime_min=117,
             content_rating="R", resolution="4K", editions=["Theatrical", "Director's Cut"], added_at=1718628256),
        dict(title="Beta", year=2002, genres=["Action"], directors=["Ann Auteur"], cast=["Star A", "Bob"],
             imdb_rating=7.0, owner_rating=9.0, owner_plays=2, last_played=1772600000, studio="Big Studio"),
        dict(title="Gamma", year=2003, genres=["Drama"], cast=["Cat"], imdb_rating=6.0),
        dict(title="Delta", year=2004, genres=["Drama"], cast=["Dan"], imdb_rating=6.5),
        dict(title="Silent", year=1925),
    ]
    c = make_catalog(specs, libraries=("Movies", "Classics"))
    alpha = next(f for f in c.films.values() if f.title == "Alpha")
    alpha.plex_ids = [101, 102]
    for plex_id, edition in ((101, "Theatrical"), (102, "Director's Cut")):
        info = CreditsInfo(credits_start=6_000_000, duration=6_400_000, scenes=[
            CreditsScene(kind="Mid-credits", start=6_100_000, end=6_150_000, verdict="Likely")],
            stretches=[(6_000_000, 6_100_000), (6_150_000, 6_400_000)])
        alpha.credits_copies.append(CopyCredits(plex_id, edition, info))
    return c


def key(c, title):
    return next(f.key for f in c.films.values() if f.title == title)


class FakeApp:
    """The main window's services. run() does the work at once unless hold=True (then release() finishes it)."""

    def __init__(self, catalog=None, state="ready", hold=False):
        self.catalog, self.catalog_state, self.catalog_error = catalog, state, "the file is locked"
        self.gone, self.status, self.hold, self.held, self.keys = [], "", hold, [], []
        self.settings, self.remembered, self.logs = {}, [], []
        self._keyed = {}
        self.tab = None
        self.search_bar = None

    def _remember(self, **values):
        self.settings.update(values)
        self.remembered.append(values)

    def _log(self, text):
        self.logs.append(text)

    def ask(self, request):
        from projectionist.ask import handle
        return handle(request, catalog=self.catalog)

    def run(self, work, done=None, failed=None, status=None, key=None):
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

    def release(self):
        held, self.held = self.held, []
        for h in held:
            self.finish(h)

    def goto(self, tab_title, /, **kwargs):
        self.gone.append((tab_title, kwargs))
        return object()

    def set_status(self, text):
        self.status = text

    def current_tab(self):
        return self.tab


def all_widgets(w):
    out = []
    for c in w.winfo_children():
        out.append(c)
        out.extend(all_widgets(c))
    return out


def links(w) -> dict:
    from projectionist.ui.widgets import LinkLabel
    return {x.cget("text"): x for x in all_widgets(w) if isinstance(x, LinkLabel)}


def buttons(w) -> dict:
    from tkinter import ttk
    return {x.cget("text"): x for x in all_widgets(w) if isinstance(x, ttk.Button)}


class TabTestCase(unittest.TestCase):
    hooks = {}

    def setUp(self):
        try:
            self.root = hidden_root()
        except Exception as exc:
            self.skipTest(f"Tk unavailable: {exc}")
        self.patch = mock.patch.dict(sys.modules, {n: None for n in HOOK_MODULES})
        self.patch.start()
        sys.modules.update(self.hooks)
        from projectionist.ui import paint
        import tkinter as tk
        # (a click on a pile of dots or a menu would show a real menu even on a hidden window)
        self.stubs = [mock.patch.object(paint.Interaction, "_choose", lambda *a, **k: None),
                      mock.patch.object(tk.Menu, "tk_popup", lambda *a, **k: None)]
        for s in self.stubs:
            s.start()
        self.catalog = catalog()

    def tearDown(self):
        for s in self.stubs:
            s.stop()
        self.patch.stop()
        try:
            self.root.destroy()
        except Exception:
            pass

    def make(self, catalog=None, state="ready", hold=False, current=True):
        app = FakeApp(catalog if catalog is not None else self.catalog, state, hold)
        tab = F.Tab(app, self.root)
        tab.frame.pack(fill="both", expand=True)
        if current:
            app.tab = tab
        self.root.update()
        return app, tab

    def settle(self):
        for _ in range(3):
            self.root.update()


# ---------------------------------------------------------------------------------------------------------
class PlaceholderTests(TabTestCase):
    def test_states(self):
        for state, words in (("none", "No database yet"), ("loading", "Reading your collection"),
                             ("error", "Couldn't read the collection")):
            app, tab = self.make(None, state)
            self.assertTrue(tab.placeholder.winfo_manager())
            self.assertFalse(tab.scroll.winfo_manager())
            tab.placeholder.redraw(600, 300)
            texts = [tab.placeholder.itemcget(i, "text") for i in tab.placeholder.find_all()
                     if tab.placeholder.type(i) == "text"]
            self.assertTrue(any(words in t for t in texts), texts)
            tab.frame.destroy()

    def test_navigate_while_loading_waits(self):
        app, tab = self.make(None, "loading")
        tab.navigate(film_key=key(self.catalog, "Alpha"), title="Alpha (2001)")
        self.assertIn("still loading", app.status)
        self.assertIsNone(tab.current())
        tab.catalog_changed(self.catalog, "ready")
        self.assertEqual(tab.current(), ("film", key(self.catalog, "Alpha")))
        self.assertEqual(tab.header_title.cget("text"), "Alpha (2001)")
        self.assertTrue(tab.scroll.winfo_manager())


class StartPageTests(TabTestCase):
    @classmethod
    def setUpClass(cls):
        from projectionist.ui import watchnext  # noqa: F401  (defines Settings > Watch Next, which the page reads)

    def test_start_page(self):
        app, tab = self.make()
        tab.shown()
        self.settle()
        self.assertEqual(tab.current(), ("start",))
        texts = tab.texts()
        for words in ("Which film?", "Recently opened", "The films you open here will be listed here.",
                      "Suggestions", "Just added to Plex", "Your favourites", "Well rated, not seen yet"):
            self.assertIn(words, texts)
        self.assertIn("Beta (2002)", texts)                           # a favourite (you gave it 9)
        self.assertEqual(tab.title_label.cget("text"), F.START_TITLE)
        self.assertIn("Search  (Ctrl+F)", buttons(tab.page))

    def test_well_rated_leaves_out_what_watch_next_never_suggests(self):
        """Settings > Watch Next > 'Never suggest films from' holds for 'Well rated, not seen yet' too - at once."""
        from projectionist import prefs
        self.assertIn(F.LEAVE_OUT, prefs.PREFS)                  # (the Watch Next tab's: setUpClass)
        alpha = next(f for f in self.catalog.films.values() if f.title == "Alpha")
        alpha.imdb_rating = 8.2
        from projectionist.ui.widgets import LinkLabel

        def alphas():                        # Alpha's links: 'Just added to Plex', and 'Well rated, not seen yet'
            return [w for w in all_widgets(tab.cards["suggestions"])
                    if isinstance(w, LinkLabel) and w.cget("text") == "Alpha (2001)"]
        app, tab = self.make()
        app.preference_changed = tab.preference_changed
        tab.shown()
        self.settle()
        self.assertEqual(len(alphas()), 2)
        prefs.set(app, F.LEAVE_OUT, {"libraries": [], "genres": ["horror"]})
        self.settle()
        self.assertEqual(len(alphas()), 1)                                    # (just added: as it was)
        self.assertIn("Beta (2002)", links(tab.cards["suggestions"]))          # (your favourites too)
        self.assertIn("You've seen every film IMDb rates 8 or more, apart from those Settings leaves out.",
                      tab.texts())
        prefs.set(app, F.LEAVE_OUT, {"libraries": [], "genres": []})
        self.settle()
        self.assertEqual(len(alphas()), 2)

    def test_films_played_on_an_account_counted_as_yours(self):
        """Settings > Your collection > 'Also count as seen': 'Well rated, not seen yet' leaves out what those
        accounts played - at once."""
        from projectionist import prefs
        alpha = next(f for f in self.catalog.films.values() if f.title == "Alpha")
        alpha.imdb_rating, alpha.played_by = 8.2, {7: 1}
        from projectionist.ui.widgets import LinkLabel

        def alphas():
            return [w for w in all_widgets(tab.cards["suggestions"])
                    if isinstance(w, LinkLabel) and w.cget("text") == "Alpha (2001)"]
        app, tab = self.make()
        app.preference_changed = tab.preference_changed
        tab.shown()
        self.settle()
        self.assertEqual(len(alphas()), 2)
        prefs.set(app, F.SEEN_BY, [7])
        self.settle()
        self.assertEqual(len(alphas()), 1)                                    # (just added: as it was)
        prefs.set(app, F.SEEN_BY, [])
        self.settle()
        self.assertEqual(len(alphas()), 2)

    def test_a_list_of_none_is_forgotten_as_the_window_closes(self):
        """0 films to list: nothing opened is added, and the list goes when the window closes - not the moment the
        box reaches 0 (one click too many on its arrow), and showing fewer for a while loses none."""
        from projectionist import prefs
        app, tab = self.make()
        app.preference_changed = tab.preference_changed
        app.save_settings = lambda: True
        a, b, g = (key(self.catalog, t) for t in ("Alpha", "Beta", "Gamma"))
        tab.open_film(a)
        tab.open_film(b)
        prefs.set(app, "film_recent_count", 1)
        tab.open_film(g)
        self.assertEqual(app.settings["film_recent"], [g, b, a])            # (kept; the start page shows 1)
        prefs.set(app, "film_recent_count", 0)
        tab.open_film(a)
        self.assertEqual(app.settings["film_recent"], [g, b, a])            # (nothing added, nothing lost yet)
        prefs.set(app, "film_recent_count", 12)                              # (changed their mind: all still there)
        self.assertEqual(tab.closing(), {})
        prefs.set(app, "film_recent_count", 0)
        self.assertEqual(tab.closing(), {"film_recent": []})                 # (the window saves that as it closes)
        self.assertEqual(tab.recent, [])

    def test_long_names_are_cut_short_to_fit_a_column(self):
        from tkinter import font as tkfont
        app, tab = self.make()
        tab.shown()
        self.settle()
        link = links(tab.cards["suggestions"])["Beta (2002)"]
        long_name = "The Ultimate Video Collection of Everything Ever Made (2003)"
        tab._elide([(link, long_name)], 150)
        text = link.cget("text")
        self.assertTrue(text.endswith("…") and long_name.startswith(text[:-1]), text)
        self.assertLessEqual(tkfont.Font(root=self.root, font="TkDefaultFont").measure(text), 150)
        tab._elide([(link, long_name)], 2000)                 # (a wider window: the whole name again)
        self.assertEqual(link.cget("text"), long_name)
        link.event_generate("<Button-1>")                     # (still opens its film)
        self.assertEqual(tab.current(), ("film", key(self.catalog, "Beta")))

    def test_recently_opened(self):
        app, tab = self.make()
        tab.open_film(key(self.catalog, "Alpha"))
        tab.open_film(key(self.catalog, "Gamma"))
        self.assertEqual(app.settings["film_recent"], [key(self.catalog, "Gamma"), key(self.catalog, "Alpha")])
        tab.back()                                        # (seeing Alpha again makes it the latest)
        tab.back()
        self.assertEqual(tab.current(), ("start",))
        found = links(tab.cards["recent"])
        self.assertEqual(list(found), ["Alpha (2001)", "Gamma (2003)"])
        found["Alpha (2001)"].event_generate("<Button-1>")
        self.assertEqual(tab.current(), ("film", key(self.catalog, "Alpha")))
        # a new window remembers them (keys no longer in the collection are left out)
        app2 = FakeApp(self.catalog)
        app2.settings = {"film_recent": [key(self.catalog, "Delta"), "plex://movie/gone"]}
        tab2 = F.Tab(app2, self.root)
        tab2.shown()
        self.root.update()
        self.assertEqual(list(links(tab2.cards["recent"])), ["Delta (2004)"])


# ---------------------------------------------------------------------------------------------------------
class FilmPageTests(TabTestCase):
    def test_a_film(self):
        app, tab = self.make()
        tab.navigate(film_key=key(self.catalog, "Alpha"), title="Alpha (2001)")
        self.settle()
        self.assertEqual(tab.title_label.cget("text"), "Alpha (2001)")
        self.assertEqual(tab.header_title.cget("text"), "Alpha (2001)")
        texts = tab.texts()
        self.assertIn("Also known as 阿爾法", texts)
        self.assertIn("2001  ·  1 h 57 min  ·  R  ·  4K", texts)
        self.assertIn("It begins.", texts)
        self.assertIn("2 copies: Theatrical and Director's Cut", texts)
        self.assertIn("Jun 17, 2024", texts)
        self.assertIn("Ratings and plays: Owner", tab.source_label.cget("text"))
        # the ratings chart: predicted first (it's unrated), then the scores
        rows = tab.views["ratings"].rows
        self.assertEqual([r["source"] for r in rows], ["predicted", "imdb", "tmdb", "rt_critic", "rt_audience"])
        self.assertEqual(rows[3]["text"], "91%  Fresh")
        self.assertIn("Watch Next expects about", tab.ratings_note.cget("text"))
        with tempfile.TemporaryDirectory() as d:
            path = tab.views["ratings"].save_png(os.path.join(d, "ratings.png"), 520, F.ratings_height(len(rows)))
            self.assertGreater(os.path.getsize(path), 1000)
        # the cast, in billing order, and the director
        cast = tab.tables["cast"]
        self.assertEqual([r["name"] for r in cast.rows.values()], ["Star A", "Bob", "Cat"])
        self.assertIn("Ann Auteur", links(tab.cards["people"]))
        # a timeline for each scanned copy, named by edition
        self.assertEqual(len(tab.timeline_views), 2)
        self.assertIn("Stay for it", " ".join(texts))
        # the similar films arrived (the runner here is immediate)
        self.assertIn("similar_unseen", tab.tables)
        self.assertEqual(app.status, F.LOADING_SIMILAR)
        # hooks not built yet: those cards stay hidden; the history card uses Plex's count
        for name in ("critics", "files", "issues"):
            self.assertFalse(tab.cards[name].shown)
            self.assertFalse(tab.cards[name].winfo_manager())
        self.assertIn(FP.PLAYS_NOTE, texts)
        self.assertIn("Not played yet - in Plex since Jun 17, 2024", texts)

    def test_dates_and_times_in_the_style_chosen(self):
        """Settings > Dates and times holds on the Film tab too: the page is drawn again, where it was."""
        from projectionist import formats
        app, tab = self.make()
        self.addCleanup(formats.reset)
        tab.navigate(film_key=key(self.catalog, "Alpha"), title="Alpha (2001)")
        self.settle()
        self.assertIn("Jun 17, 2024", tab.texts())
        formats.use(date="iso")
        tab.preference_changed("date_style", "iso")
        self.settle()
        texts = tab.texts()
        self.assertIn("2024-06-17", texts)
        self.assertIn("Not played yet - in Plex since 2024-06-17", texts)
        self.assertEqual(tab.current(), ("film", key(self.catalog, "Alpha")))
        formats.use(date="dmy", clock="24h")
        self.assertEqual(F.day_text("2026-03-03"), "3 Mar 2026")
        self.assertEqual(F.moment_text("2026-03-03T21:14"), "3 Mar 2026, 21:14")
        self.assertEqual(F.moment_text("2026-03-03"), "3 Mar 2026")
        formats.reset()
        self.assertEqual(F.moment_text("2026-03-03T21:14"), "Mar 3, 2026, 9:14 pm")
        self.assertEqual(F.day_text(""), "")

    def test_similar_films_played_on_an_account_counted_as_yours(self):
        from projectionist import prefs
        alpha = next(f for f in self.catalog.films.values() if f.title == "Alpha")
        app, tab = self.make()
        app.preference_changed = tab.preference_changed
        tab.navigate(film_key=key(self.catalog, "Beta"), title="Beta (2002)")
        self.settle()

        def unseen():
            table = tab.tables.get("similar_unseen")
            return [r["label"] for r in table.rows.values()] if table is not None else []
        self.assertIn("Alpha (2001)", unseen())
        alpha.played_by = {7: 1}
        prefs.set(app, F.SEEN_BY, [7])                                        # (asked again at once)
        self.settle()
        self.assertNotIn("Alpha (2001)", unseen())
        self.assertEqual(tab.current(), ("film", key(self.catalog, "Beta")))

    def test_a_page_on_screen_is_built_a_card_at_a_time(self):
        app, tab = self.make()
        tab.staged = True                                 # (as on a window that's showing)
        a, g = key(self.catalog, "Alpha"), key(self.catalog, "Gamma")
        tab.open_film(a)
        self.assertEqual(set(tab.cards), {"header", "ratings", "history"})
        self.assertEqual(app.keys, [])                    # the background parts wait for their cards
        tab.open_film(g)                                  # another film before the first is finished
        deadline = time.time() + 10
        while "similar_unseen" not in tab.tables:
            self.root.update()
            time.sleep(0.005)
            self.assertLess(time.time(), deadline)
        self.assertEqual([n for n, _span in tab.slots],
                         ["header", "ratings", "history", "credits", "people", "similar", "critics", "files",
                          "issues"])
        self.assertEqual(tab.header_title.cget("text"), "Gamma (2003)")
        self.assertEqual([r["name"] for r in tab.tables["cast"].rows.values()], ["Cat"])
        self.assertEqual(sorted(k for k in app.keys if k), sorted(f"film.{p}" for p in FP.BACKGROUND))

    def test_a_film_without_markers_or_people(self):
        app, tab = self.make()
        tab.open_film(key(self.catalog, "Silent"))
        texts = tab.texts()
        from projectionist.ui import credits as CR
        self.assertIn(CR.MEANINGS[CR.NOT_SCANNED], texts)
        self.assertEqual(tab.timeline_views, [])
        self.assertIn("Plex hasn't recorded who's in it.", texts)
        self.assertNotIn("cast", tab.tables)
        self.assertIn("Plex has no summary for this film.", texts)

    def test_rated_film(self):
        app, tab = self.make()
        tab.open_film(key(self.catalog, "Beta"))
        rows = tab.views["ratings"].rows
        self.assertEqual(rows[0]["source"], "you")
        self.assertIn("4.5 stars in Plex", rows[0]["tip"])
        self.assertTrue(tab.ratings_note.cget("text").startswith("You gave it 9 - 2 above IMDb's 7."))
        self.assertIn("Played 2 times - last on Mar 3, 2026", tab.texts())

    def test_a_rating_left_on_an_earlier_edition(self):
        beta = self.catalog.films[key(self.catalog, "Beta")]
        beta.owner_rating_from_earlier_edition = True
        app, tab = self.make()
        tab.open_film(beta.key)
        tip = tab.views["ratings"].rows[0]["tip"]
        self.assertIn("Rated on an earlier edition - Plex shows this copy as unrated", tip)
        self.assertNotIn("stars in Plex", tip)
        self.assertIn("rate it again in Plex", tab.ratings_note.cget("text"))

    def test_more_like_this_only_when_theres_something_to_compare(self):
        app, tab = self.make()
        tab.open_film(key(self.catalog, "Silent"))           # (nothing but its decade and library)
        self.assertNotIn("More like this in Watch Next", buttons(tab.cards["header"]))
        self.assertIn("Scenes after the credits?", buttons(tab.cards["header"]))
        tab.open_film(key(self.catalog, "Alpha"))
        self.assertIn("More like this in Watch Next", buttons(tab.cards["header"]))

    def test_a_long_title_leaves_the_head_row_whole(self):
        import types as _types
        app, tab = self.make()
        tab.open_film(key(self.catalog, "Alpha"))
        long = "The Lighthouse Keepers: A Devotion to the Sea, the Harbour Story Told in Full at Last (2008)"
        tab._set_title(long)
        tab._fit_head(_types.SimpleNamespace(width=4000))
        self.assertEqual(tab.title_label.cget("text"), long)
        self.assertTrue(tab.hint_label.winfo_manager() and tab.source_label.winfo_manager())
        tab._fit_head(_types.SimpleNamespace(width=int(700 * tab.s)))
        self.assertFalse(tab.hint_label.winfo_manager())          # the hint goes first...
        self.assertFalse(tab.source_label.winfo_manager())        # ...then 'Ratings and plays: Owner'
        shown = tab.title_label.cget("text")
        self.assertTrue(shown.endswith("…") and long.startswith(shown[:-1]), shown)
        tab._fit_head(_types.SimpleNamespace(width=4000))         # (room again: all of it back)
        self.assertEqual(tab.title_label.cget("text"), long)
        self.assertTrue(tab.source_label.winfo_manager())

    def test_a_title_that_is_only_a_guess_says_so(self):
        app, tab = self.make()
        tab.navigate(title="gamma")                       # the title exactly (but for case): nothing to say
        self.assertEqual(tab.current(), ("film", key(self.catalog, "Gamma")))
        self.assertNotIn("Showing", app.status)
        tab.navigate(title="gamm")                        # a guess says so
        self.assertEqual(tab.current(), ("film", key(self.catalog, "Gamma")))
        self.assertIn("Showing Gamma (2003) for “gamm”", app.status)
        tab.navigate(title="Zzqx (1901)")
        self.assertEqual(tab.current(), ("missing", "Zzqx (1901)"))
        self.assertIn("No film called “Zzqx (1901)” in your collection", app.status)

    def test_background_parts_come_late(self):
        app, tab = self.make(hold=True)
        a, g = key(self.catalog, "Alpha"), key(self.catalog, "Gamma")
        tab.open_film(a)
        self.assertEqual(app.status, F.LOADING_SIMILAR)
        self.assertIn(F.LOADING_SIMILAR, tab.texts())
        # (your plays wait for the viewing history too, rather than showing Plex's count and then another)
        self.assertIn(F.READING_PLAYS, tab.texts())
        self.assertNotIn(FP.PLAYS_NOTE, tab.texts())
        self.assertEqual({k for k in app.keys if k}, {f"film.{p}" for p in FP.BACKGROUND})
        self.assertEqual([r["source"] for r in tab.views["ratings"].rows][0], "imdb")    # no prediction yet
        first = list(app.held)
        tab.open_film(g)                       # another film: the first one's jobs are called off
        self.assertTrue(all(job.cancelled for job, *_ in first))
        for held in first:
            app.finish(held)                   # (a late answer is dropped)
        self.assertEqual(tab.current(), ("film", g))
        self.assertIn(F.LOADING_SIMILAR, tab.texts())
        # an answer for a film no longer shown is ignored even if it gets through
        tab._part_done(self.catalog, a, "similar", {"ok": True, "unseen": [], "seen": []})
        self.assertIn(F.LOADING_SIMILAR, tab.texts())
        app.release()
        self.assertNotIn(F.LOADING_SIMILAR, tab.texts())
        self.assertEqual(tab.views["ratings"].rows[0]["source"], "predicted")
        self.assertNotIn(F.READING_PLAYS, tab.texts())
        self.assertIn(FP.PLAYS_NOTE, tab.texts())

    def test_clicks_go_to_the_right_places(self):
        app, tab = self.make()
        a = key(self.catalog, "Alpha")
        tab.open_film(a)
        people = [r for r in tab.tables["cast"].rows.values()]
        tab.tables["cast"].on_open(people[1])
        self.assertEqual(app.gone[-1], ("Six Degrees", {"person_id": "p:Bob", "name": "Bob"}))
        links(tab.cards["people"])["Ann Auteur"].event_generate("<Button-1>")
        self.assertEqual(app.gone[-1], ("Six Degrees", {"person_id": "p:Ann Auteur", "name": "Ann Auteur",
                                                        "directors": True}))
        head = links(tab.cards["header"])
        for text, expected in (("Horror", ("Watch Next", {"genre": "Horror"})),
                               ("United States", ("Watch Next", {"country": "United States of America"})),
                               ("Hong Kong", ("Watch Next", {"country": "Hong Kong"})),
                               ("Faves", ("Watch Next", {"collection": "Faves"})),
                               ("Movies", ("Watch Next", {"library": "Movies"}))):
            head[text].event_generate("<Button-1>")
            self.assertEqual(app.gone[-1], expected)
        buttons(tab.cards["header"])["Scenes after the credits?"].invoke()
        self.assertEqual(app.gone[-1], ("Credits", {"film_key": a, "title": "Alpha (2001)"}))
        buttons(tab.cards["header"])["More like this in Watch Next"].invoke()
        self.assertEqual(app.gone[-1], ("Watch Next", {"like": "Alpha (2001)", "film_key": a}))
        links(tab.cards["credits"])["Open in the Credits tab  →"].event_generate("<Button-1>")
        self.assertEqual(app.gone[-1][0], "Credits")
        links(tab.cards["similar"])["More like this in Watch Next  →"].event_generate("<Button-1>")
        self.assertEqual(app.gone[-1], ("Watch Next", {"like": "Alpha (2001)", "film_key": a}))
        # a similar film opens here, and joins the history
        before = len(tab.history)
        similar = next(iter(tab.tables["similar_unseen"].rows.values()))
        tab.tables["similar_unseen"].on_open(similar)
        self.assertEqual(tab.current(), ("film", similar["key"]))
        self.assertEqual(len(tab.history), before + 1)
        # the studio: its films, here
        tab.back()
        links(tab.cards["header"])["Big Studio"].event_generate("<Button-1>")
        self.assertEqual(tab.current(), ("studio", "Big Studio"))
        self.assertEqual(tab.title_label.cget("text"), "Films from Big Studio")
        studio = tab.tables["studio"]
        self.assertEqual(len(studio.rows), 36)
        row = next(r for r in studio.rows.values() if r["title"] == "Beta")
        studio.on_open(row)
        self.assertEqual(tab.current(), ("film", key(self.catalog, "Beta")))


# ---------------------------------------------------------------------------------------------------------
class HistoryTests(TabTestCase):
    def test_back_and_forward(self):
        app, tab = self.make()
        a, b, c, d = (key(self.catalog, t) for t in ("Alpha", "Beta", "Gamma", "Delta"))
        tab.open_film(a)
        self.assertTrue(tab.back_btn.instate(["!disabled"]))            # back to the start page
        self.assertTrue(tab.forward_btn.instate(["disabled"]))
        tab.open_film(b)
        tab.open_film(c)
        tab.back()
        self.assertEqual(tab.current(), ("film", b))
        tab.back()
        self.assertEqual(tab.current(), ("film", a))
        tab.forward()
        self.assertEqual(tab.current(), ("film", b))
        self.assertTrue(tab.forward_btn.instate(["!disabled"]))
        tab.open_film(d)                                  # a new page cuts the forward history, like a browser
        self.assertEqual(tab.history, [("start",), ("film", a), ("film", b), ("film", d)])
        self.assertTrue(tab.forward_btn.instate(["disabled"]))
        for _ in range(3):
            tab.back()
        self.assertEqual(tab.current(), ("start",))
        self.assertTrue(tab.back_btn.instate(["disabled"]))
        tab.back()                                        # nothing before the start page
        self.assertEqual(tab.position, 0)
        tab.open_film(a)
        tab.open_film(a)                                  # the same page again isn't another entry
        self.assertEqual(tab.history, [("start",), ("film", a)])

    def test_history_is_capped(self):
        app, tab = self.make(hold=True)
        keys = [f.key for f in self.catalog.films.values()]
        opened = [keys[i % len(keys)] for i in range(F.HISTORY_MAX + 5)]
        for k in opened:
            tab.open_film(k)
        self.assertEqual(len(tab.history), F.HISTORY_MAX)
        self.assertEqual(tab.history[0], ("start",))
        self.assertEqual(tab.history[1:], [("film", k) for k in opened[-(F.HISTORY_MAX - 1):]])
        self.assertEqual(tab.current(), ("film", opened[-1]))

    def test_alt_arrows(self):
        app, tab = self.make()
        a, b = key(self.catalog, "Alpha"), key(self.catalog, "Beta")
        tab.open_film(a)
        tab.open_film(b)
        self.assertIn(" ", self.root.bind("<Alt-Left>"))                 # bound on the window itself
        self.assertTrue(self.root.bind("<Alt-Right>"))
        self.assertEqual(tab._alt(-1), "break")
        self.assertEqual(tab.current(), ("film", a))
        self.assertEqual(tab._alt(1), "break")
        self.assertEqual(tab.current(), ("film", b))
        app.tab = object()                                # another tab is showing: the keys aren't ours
        self.assertIsNone(tab._alt(-1))
        self.assertEqual(tab.current(), ("film", b))
        app.tab = tab
        self.root.event_generate("<Alt-Left>")
        self.root.update()
        tab.frame.destroy()                               # a destroyed tab takes its bindings with it
        self.assertEqual(self.root.bind("<Alt-Left>").strip(), "")

    def test_a_new_collection_prunes_the_history(self):
        app, tab = self.make()
        a, b = key(self.catalog, "Alpha"), key(self.catalog, "Beta")
        tab.open_film(a)
        tab.open_film(b)
        smaller = catalog()
        del smaller.films[b]
        app.catalog = smaller
        tab.catalog_changed(smaller, "ready")
        self.assertEqual(tab.history, [("start",), ("film", a)])
        self.assertEqual(tab.current(), ("start",))                     # its film is gone
        self.assertIn("Which film?", tab.texts())
        tab.forward()
        self.assertEqual(tab.current(), ("film", a))


# ---------------------------------------------------------------------------------------------------------
def fake_critics(catalog, film_key):
    return {"ok": True, "summary": "2 of your closest critics reviewed it: 1 Fresh, 1 Rotten.",
            "closest": [{"id": 125, "name": "Roger Ebert", "publication": "Chicago Sun-Times", "verdict": "Fresh",
                         "quote": "A triumph.", "rank": 1, "shared": 30, "agreement": 0.8}],
            "others": [{"id": 7, "name": "Janet Maslin", "publication": "New York Times", "verdict": "Rotten",
                        "quote": "", "rank": None, "shared": 0, "agreement": None},
                       {"id": 9, "name": "Elston Brooks", "publication": "Star-Telegram", "verdict": "Fresh",
                        "quote": "", "rank": None, "shared": 1, "agreement": 1.0}]}


def fake_history(catalog, film_key):
    return {"ok": True, "plays": [{"at": "2025-03-08T10:00", "how": "bulk", "device": ""},
                                  {"at": "2026-03-03T21:14", "how": "played", "device": "Living Room TV"}],
            "plays_on_record": 1, "plex_count": 3, "last_played": "2026-03-03T21:14",
            "resume": {"at_sec": 3723, "at": "1:02:03", "share": 0.54, "stopped": "2026-03-04T20:00"},
            "rating": None, "history_from": "2025-03-08"}


def fake_files(catalog, film_key):
    return {"ok": True, "copies": [
        {"edition": "Theatrical", "library": "Movies", "resolution": "4K", "video_codec": "HEVC", "hdr": "HDR10",
         "container": "mkv", "size_gb": 21.2, "duration_min": 116.6,
         "audio": [{"language": "English", "format": "TrueHD 7.1", "default": True},
                   {"language": "French", "format": "AC3 5.1"}],
         "subtitles": [{"language": "English", "forced": True}],
         "files": [{"path": "/disk1/Movies/Alpha (2001)/Alpha.mkv"}]},
        {"edition": "", "library": "Classics", "resolution": "1080p", "files": ["/old/alpha.mkv"],
         "unavailable": True, "unavailable_since": "2026-01-02"}]}


def fake_issues(catalog, film_key):
    return [{"id": "weaker", "severity": "fix", "title": "Weaker copies", "why": "The 1080p copy is weaker.",
             "edition": "Director's Cut", "plex_id": 2608, "media_id": 3001},
            {"id": "editions", "severity": "info", "title": "Several editions", "why": "Two cuts."}]


class HookTests(TabTestCase):
    hooks = {"projectionist.critics": fake_module("projectionist.critics", film_verdicts=fake_critics),
             "projectionist.habits": fake_module("projectionist.habits", film_history=fake_history),
             "projectionist.doctor": fake_module("projectionist.doctor", film_files=fake_files,
                                                 film_issues=fake_issues)}

    def test_the_other_backbones_answers(self):
        app, tab = self.make()
        tab.open_film(key(self.catalog, "Alpha"))
        self.settle()
        for name in ("critics", "files", "issues"):
            self.assertTrue(tab.cards[name].shown, name)
            self.assertTrue(tab.cards[name].winfo_manager(), name)
        texts = tab.texts()
        critics = tab.tables["critics"]
        self.assertEqual([r["critic_text"] for r in critics.rows.values()],
                         ["Roger Ebert", "Janet Maslin", "Elston Brooks"])
        first = critics.rows["0"]
        self.assertEqual((first["rank_text"], first["agree_text"], first["verdict_text"]),
                         ("#1", "80% of 30", "✔ Fresh"))
        # ('100% of 1' beside a #1 at '80% of 30' would only mislead: too few films to tell)
        self.assertEqual(critics.rows["2"]["agree_text"], "")
        hint = next(t for t in texts if t.startswith("Closest: "))
        self.assertIn("most in step with you", hint)
        self.assertIn("allowing for how often they say Fresh", hint)
        self.assertNotIn("in order of how often", hint)
        self.assertIn("“A triumph.”  - Roger Ebert", texts)                  # the selected critic's quote
        critics.on_open(first)
        self.assertEqual(app.gone[-1], ("Watch Next", {"critic": "125"}))
        self.assertIn("Theatrical  ·  Movies", texts)
        self.assertIn("4K HEVC HDR10  ·  MKV  ·  21.2 GB  ·  1 h 57 min", texts)
        self.assertTrue(any(t.startswith("Audio: English, French  (2 tracks; the default is English TrueHD 7.1)")
                            for t in texts), texts)
        self.assertIn("Subtitles: English  (1 track, 1 forced)", texts)
        self.assertIn("✖ Plex can't find this file (since Jan 2, 2026)", texts)
        from tkinter import ttk
        paths = [w.get() for w in all_widgets(tab.cards["files"]) if isinstance(w, ttk.Entry)]
        self.assertEqual(paths, ["/disk1/Movies/Alpha (2001)/Alpha.mkv", "/old/alpha.mkv"])
        self.assertIn("!  Weaker copies  ·  To fix  ·  Director's Cut", texts)
        self.assertIn("i  Several editions  ·  For info", texts)       # (the Library Doctor's words)
        heads = [w for w in all_widgets(tab.cards["issues"]) if isinstance(w, ttk.Label)
                 and w.cget("text").startswith("!  Weaker")]
        heads[0].event_generate("<Button-1>")
        # (the Library Doctor opens that list with this film's row picked - this copy's row)
        opened = ("Library Doctor", {"issue": "weaker", "film_key": tab.film.key, "plex_id": 2608, "media_id": 3001})
        self.assertEqual(app.gone[-1], opened)
        # the keyboard too: Tab stops on the issue, its title is underlined while focused, Enter opens it
        box = heads[0].master
        self.assertEqual(str(box.cget("takefocus")), "1")
        app.gone.clear()
        import re
        script = re.sub(r"%[#bfhkstwxyENTXYDAK]", "0", box.bind("<Return>").replace("%W", str(box)))
        box.tk.eval("foreach __once {1} {\n%s\n}" % script)
        self.assertEqual(app.gone, [opened])
        box.event_generate("<FocusIn>")
        from tkinter import font as tkfont
        self.assertEqual(tkfont.Font(root=box, font=heads[0].cget("font")).actual("underline"), 1)
        box.event_generate("<FocusOut>")
        others = [w for w in all_widgets(tab.cards["issues"]) if isinstance(w, ttk.Label)
                  and w.cget("text").startswith("i  Several")]
        others[0].event_generate("<Button-1>")          # (no copy named: the film's first row)
        self.assertEqual(app.gone[-1], ("Library Doctor", {"issue": "editions", "film_key": tab.film.key,
                                                           "plex_id": None, "media_id": None}))
        # your plays, from the viewing history
        self.assertIn("Played 1 time - last on Mar 3, 2026", texts)
        self.assertIn("Mar 3, 2026, 9:14 pm  ·  Living Room TV", texts)
        self.assertIn("Mar 8, 2025, 10:00 am  ·  marked as played in bulk", texts)
        self.assertTrue(any("You stopped at 1:02:03 (54% of the way through)" in t for t in texts))
        self.assertTrue(any(t.startswith("Plex counts 3 plays in all - one with no record of when") for t in texts),
                        texts)
        links(tab.cards["history"])["See 2026 in review  →"].event_generate("<Button-1>")
        self.assertEqual(app.gone[-1], ("Viewing", {"year": 2026}))

    def test_new_languages_in_the_library_doctor_refresh_the_issues(self):
        def issues(catalog, film_key):
            if "ja" in (catalog.cache.get("doctor.languages") or ["en"]):
                return []
            return [{"id": "subtitles", "severity": "fix", "title": "No subtitles you read", "why": "Japanese only."}]
        sys.modules["projectionist.doctor"] = fake_module("projectionist.doctor", film_files=fake_files,
                                                          film_issues=issues)
        app, tab = self.make()
        tab.open_film(key(self.catalog, "Alpha"))
        self.assertTrue(any(t.startswith("!  No subtitles you read") for t in tab.texts()))
        tab.shown()                                      # back on the tab, nothing changed: nothing asked again
        asked = app.keys.count("film.issues")
        self.catalog.cache["doctor.languages"] = ["en", "ja"]           # (Japanese ticked in the Library Doctor)
        tab.shown()
        self.assertEqual(app.keys.count("film.issues"), asked + 1)
        self.assertIn("✔ Nothing to fix for this film.", tab.texts())
        self.assertFalse(any(t.startswith("!  No subtitles you read") for t in tab.texts()))
        tab.shown()
        self.assertEqual(app.keys.count("film.issues"), asked + 1)

    def test_nothing_to_fix(self):
        sys.modules["projectionist.doctor"] = fake_module("projectionist.doctor", film_files=fake_files,
                                                       film_issues=lambda c, k: [])
        app, tab = self.make()
        tab.open_film(key(self.catalog, "Alpha"))
        self.assertIn("✔ Nothing to fix for this film.", tab.texts())

    def test_hooks_that_fail(self):
        def boom(catalog, film_key):
            raise RuntimeError("the disk is on fire")
        sys.modules["projectionist.critics"] = fake_module("projectionist.critics", film_verdicts=boom)
        sys.modules["projectionist.habits"] = fake_module("projectionist.habits", film_history=boom)
        sys.modules["projectionist.doctor"] = fake_module("projectionist.doctor", film_files=lambda c, k: {"weird": 1},
                                                       film_issues=boom)
        app, tab = self.make()
        tab.open_film(key(self.catalog, "Beta"))
        texts = tab.texts()
        self.assertTrue(any(t.startswith("Critics' verdicts couldn't be read") and "on fire" in t for t in texts))
        self.assertTrue(any(t.startswith("Library health couldn't be read") for t in texts))
        self.assertIn("Weird: 1", texts)                                 # an answer of an unknown shape: shown plainly
        self.assertIn("Played 2 times - last on Mar 3, 2026", texts)      # Plex's count, and why
        self.assertTrue(any("viewing history couldn't be read" in t for t in texts))


class RealBackbonesTests(unittest.TestCase):
    """The page with the other features' real backbones (critics, habits, doctor) over the fixture database: their
    answers must fit the cards, whatever shapes they grow into."""

    def setUp(self):
        try:
            self.root = hidden_root()
        except Exception as exc:
            self.skipTest(f"Tk unavailable: {exc}")
        from projectionist.ui import paint
        import tkinter as tk
        self.stubs = [mock.patch.object(paint.Interaction, "_choose", lambda *a, **k: None),
                      mock.patch.object(tk.Menu, "tk_popup", lambda *a, **k: None)]
        for s in self.stubs:
            s.start()
        self.tmp = tempfile.TemporaryDirectory()
        db = os.path.join(self.tmp.name, "fixture.db")
        build_fixture(db)
        from projectionist.catalog import load
        self.catalog = load(db)

    def tearDown(self):
        for s in self.stubs:
            s.stop()
        try:
            self.root.destroy()
        except Exception:
            pass
        self.tmp.cleanup()

    def open(self, title):
        from tkinter import ttk
        app = FakeApp(self.catalog)
        tab = F.Tab(app, self.root)
        tab.frame.pack(fill="both", expand=True)
        app.tab = tab
        tab.open_film(key(self.catalog, title))
        for _ in range(3):
            self.root.update()
        self.assertEqual(app.logs, [])                               # every answer fitted its card
        texts = tab.texts()
        self.assertFalse([t for t in texts if "couldn't be read" in t], texts)
        paths = [w.get() for w in all_widgets(tab.page) if isinstance(w, ttk.Entry)]
        return app, tab, texts, paths

    def test_the_matrix(self):
        app, tab, texts, paths = self.open("The Matrix")
        for name in ("critics", "files", "issues"):
            self.assertTrue(tab.cards[name].shown, name)
        self.assertEqual([r["critic_text"] for r in tab.tables["critics"].rows.values()], ["Nora Clark"])
        self.assertIn("/disk1/Movies/Matrix, The (1999)/The.Matrix.mkv", paths)
        self.assertIn("✔ Nothing to fix for this film.", texts)
        self.assertTrue(any(t.startswith("You stopped at 1:02:03") for t in texts), texts)

    def test_a_film_with_issues_and_no_reviews(self):
        app, tab, texts, paths = self.open("Hong Kong Action")
        self.assertIn("Plex has no critic reviews for this film.", texts)
        self.assertIn("/disk1/Movies/HK/hk.mkv", paths)
        self.assertTrue(any(t.startswith("!  No subtitles you read") for t in texts), texts)
        self.assertTrue(any(t.startswith("Counted as played") or t.startswith("Played") for t in texts), texts)


# ---------------------------------------------------------------------------------------------------------
class HistoryLinesTests(unittest.TestCase):
    def answer(self, plays, plex, plex_last):
        return {"available": True, "ok": True, "plays": plays, "plays_on_record": len(plays), "plex_count": plex,
                "last_played": plays[-1]["at"] if plays else None, "history_from": "2025-03-08",
                "catalog": {"plays": plex, "last_played": plex_last, "rating": 7.0, "note": FP.PLAYS_NOTE}}

    def test_plexs_own_last_play_when_the_history_hasnt_got_it(self):
        kodi = [{"at": "2026-03-31T19:13", "how": "played", "device": "Kodi"}]
        lines = F.history_lines(self.answer(kodi, 2, "2026-04-01T04:09"))
        self.assertEqual(lines["summary"], "Played 1 time - last on Mar 31, 2026")
        self.assertEqual(lines["notes"][-1], "Plex counts 2 plays in all, the last on Apr 1, 2026, 4:09 am - one of "
                                             "them isn't in the play history (marked as played, perhaps).")
        lines = F.history_lines(self.answer([], 1, "2025-09-12T16:53"))
        self.assertEqual(lines["summary"], "Counted as played - with no date on record")
        self.assertIn("the last on Sep 12, 2025, 4:53 pm - it isn't in the play history", lines["notes"][-1])
        lines = F.history_lines(self.answer([], 3, "2025-09-12T16:53"))
        self.assertIn("- none of them is in the play history", lines["notes"][-1])

    def test_undated_plays_from_before_the_history(self):
        # Plex's moment on the day its history starts (the import), or beside a play on record: as before
        kodi = [{"at": "2026-03-31T19:13", "how": "played", "device": "Kodi"}]
        for plays, plex_last in (([], "2025-03-08T11:51"), (kodi, "2026-03-31T20:30"), ([], None)):
            with self.subTest(plex_last=plex_last):
                note = F.history_lines(self.answer(plays, 2, plex_last))["notes"][-1]
                self.assertIn("marked as played, or from before its play history starts (Mar 8, 2025).", note)

    def test_editions_ticked_off_together_count_once(self):
        # a film in 4 cuts, all ticked off as played at setup, none ever logged - Plex counts 4, the viewing
        # history's backbone 1 (its undated_plays counts a film's editions once)
        answer = dict(self.answer([], 4, "2025-03-08T11:51"), undated_plays=1)
        answer["catalog"]["library_items"] = 4
        lines = F.history_lines(answer)
        self.assertEqual(lines["summary"], "Counted as played - with no date on record")
        self.assertEqual(lines["notes"][-1], "Counting its editions once: 1 play in all - one with no record of when: "
                                             "marked as played, or from before its play history starts (Mar 8, "
                                             "2025). Plex's own count is 4, as it counts each edition on its own.")
        # one logged play and one more undated: as Plex counts them, no word about editions
        kodi = [{"at": "2026-03-31T19:13", "how": "played", "device": "Kodi"}]
        note = F.history_lines(dict(self.answer(kodi, 2, "2026-03-31T20:30"), undated_plays=1))["notes"][-1]
        self.assertTrue(note.startswith("Plex counts 2 plays in all - one with no record of when"), note)
        self.assertNotIn("edition", note)
        # a film's Theatrical cut played, its Director's Cut marked as played hours later - one viewing to
        # the history, 2 to Plex: every play is on record, so the note only says why Plex's number is bigger
        answer = dict(self.answer([dict(kodi[0], logs=2)], 2, "2026-04-01T04:09"), undated_plays=0)
        answer["catalog"]["library_items"] = 2
        self.assertEqual(F.history_lines(answer)["notes"], ["Plex's own count is 2, as it counts each edition on "
                                                            "its own."])
        # one library item: a play Plex logged twice
        answer["catalog"]["library_items"] = 1
        self.assertEqual(F.history_lines(answer)["notes"], ["Plex's own count is 2: it logged a play more than "
                                                            "once, counted once here."])
        # nothing undated and Plex's count no bigger: no note
        notes = F.history_lines(dict(self.answer(kodi, 1, "2026-03-31T19:13"), undated_plays=0))["notes"]
        self.assertEqual(notes, [])

    def test_dates_as_the_viewing_tab_writes_them(self):
        self.assertEqual(F.day_text("2025-03-08"), "Mar 8, 2025")
        self.assertEqual(F.moment_text("2026-03-03T21:14"), "Mar 3, 2026, 9:14 pm")
        self.assertEqual(F.moment_text("2026-03-03T00:05"), "Mar 3, 2026, 12:05 am")
        self.assertEqual(F.moment_text("2026-03-03"), "Mar 3, 2026")

    def test_severities_look_as_in_the_library_doctor(self):
        from projectionist.ui import theme as T
        self.assertEqual(F.SEVERITY["info"][0], T.BLUE)
        self.assertEqual([F.SEVERITY[k][2] for k in ("fix", "check", "info")], ["To fix", "To check", "For info"])


class FlowTests(TabTestCase):
    def test_a_divider_never_ends_a_line(self):
        flow = F._links(self.root, [("Comedy", 1), ("Thriller", 2), ("   |   ", None), ("Hong Kong", 3)],
                        lambda v: None, sep="·")
        flow.pack()
        self.root.update()
        comedy, dot, thriller, bar, hong_kong = flow.items
        gap = int(flow.gap * flow.s)
        # room for 'Comedy · Thriller  |' but not for Hong Kong after it
        flow._width = sum(w.winfo_reqwidth() + gap for w in (comedy, dot, thriller, bar)) + 2
        flow.arrange()
        self.assertEqual(bar.winfo_manager(), "")                     # left out where the line breaks
        self.assertEqual(int(hong_kong.place_info()["x"]), 0)
        self.assertGreater(int(hong_kong.place_info()["y"]), 0)
        self.assertEqual(int(thriller.place_info()["y"]), 0)
        flow._width = 10 ** 5                                          # all on one line: the divider is back
        flow.arrange()
        self.assertEqual(bar.winfo_manager(), "place")
        self.assertEqual(int(hong_kong.place_info()["y"]), 0)


# ---------------------------------------------------------------------------------------------------------
class SearchBoxTests(TabTestCase):
    def make_bar(self, hold=False, state="ready"):
        app = FakeApp(self.catalog, state, hold)
        bar = F.GlobalSearch(self.root, app)
        bar.pack(fill="x")
        self.root.update()
        return app, bar

    def test_getting_ready(self):
        app, bar = self.make_bar(hold=True)
        bar.catalog_changed(self.catalog, "ready")
        self.assertEqual(app.keys, ["search.index"])
        bar.query("alpha")
        self.assertEqual(bar.message, "Getting search ready...")
        self.assertEqual(bar.lines, [("message", "Getting search ready...")])
        app.release()                                     # built: the next search finds it
        bar.query("alpha")
        self.assertEqual(bar.rows[0][0]["kind"], "film")
        bar.catalog_changed(None, "loading")
        bar.query("alpha")
        self.assertEqual(bar.message, "The collection is still loading...")
        self.assertEqual(bar.lines, [("message", "The collection is still loading...")])   # (the list says so)
        bar.catalog_changed(None, "none")
        bar.query("alpha")
        self.assertEqual(bar.message, "The collection isn't loaded yet.")
        self.assertEqual(bar.lines, [("message", "The collection isn't loaded yet.")])

    @unittest.skipUnless(may_show_windows(), "puts a window on screen: only on a virtual display (xvfb-run)")
    def test_the_list_first_opens_at_its_top_at_every_text_size(self):
        """The first time the Find list opens it shows its first line (a heading), whatever the text size. At
        115% and 130% it used to open a line down: its see() of the highlighted match worked the view out before
        the list had the size just given it."""
        import tkinter as tk
        from projectionist.ui import theme as T
        for factor in (1.0, 1.15, 1.3):
            with self.subTest(factor=factor):
                root = tk.Tk()
                try:
                    root.tk.call("tk", "scaling", float(root.tk.call("tk", "scaling")) * factor)
                    T.apply(root, "graphite")
                    root.geometry("1100x300+0+0")
                    app = FakeApp(self.catalog, "ready", False)
                    bar = F.GlobalSearch(root, app)
                    bar.pack(fill="x")
                    root.update()
                    bar.catalog_changed(self.catalog, "ready")
                    bar.var.set("star a")
                    bar.query("star a")
                    self.assertEqual(bar.selected, 1)                              # (under its heading)
                    bar._render()
                    self.assertTrue(bar._shown())
                    self.assertEqual(bar.listbox.yview()[0], 0.0)                 # its first line at the top
                    self.assertIsNotNone(bar.listbox.bbox(f"{bar.selected + 1}.0"))   # ...and the match showing
                    bar.hide()
                finally:
                    root.destroy()

    def test_the_drop_down_model(self):
        app, bar = self.make_bar()
        bar.catalog_changed(self.catalog, "ready")
        answer = bar.query("star a")
        self.assertTrue(answer["ok"])
        header, results = bar.rows[0]
        self.assertEqual((header["kind"], header["title"]), ("person", "People"))
        self.assertEqual(results[0]["label"], "Star A")
        self.assertEqual(bar.lines[0], ("header", header))
        self.assertEqual(bar.selected, 1)                 # the best match is highlighted
        bar.query("b")
        self.assertEqual(bar.message, "Keep typing...")
        bar.query("zzqxvw")
        self.assertEqual(bar.message, "Nothing matches “zzqxvw”")
        self.assertIsNone(bar.popup)                      # never shown on a hidden window

    def test_where_each_match_opens(self):
        app, bar = self.make_bar()
        bar.catalog_changed(self.catalog, "ready")
        a = key(self.catalog, "Alpha")
        cases = [
            ({"kind": "film", "id": a, "key": a, "label": "Alpha (2001)"},
             ("Film", {"film_key": a, "title": "Alpha (2001)"})),
            ({"kind": "person", "id": "p:Bob", "name": "Bob", "label": "Bob", "directs": False},
             ("Six Degrees", {"person_id": "p:Bob", "name": "Bob"})),
            ({"kind": "person", "id": "p:Ann Auteur", "name": "Ann Auteur", "label": "Ann Auteur", "directs": True},
             ("Six Degrees", {"person_id": "p:Ann Auteur", "name": "Ann Auteur", "directors": True})),
            ({"kind": "collection", "id": "Faves", "label": "Faves"}, ("Watch Next", {"collection": "Faves"})),
            ({"kind": "genre", "id": "Horror", "label": "Horror"}, ("Watch Next", {"genre": "Horror"})),
            ({"kind": "country", "id": "United States of America", "label": "United States"},
             ("Watch Next", {"country": "United States of America"})),
            ({"kind": "library", "id": "Classics", "label": "Classics"}, ("Watch Next", {"library": "Classics"})),
            ({"kind": "studio", "id": "Big Studio", "label": "Big Studio"}, ("Film", {"studio": "Big Studio"})),
            ({"kind": "critic", "id": "125", "label": "Roger Ebert"}, ("Watch Next", {"critic": "125"})),
        ]
        for result, expected in cases:
            bar.open(result)
            self.assertEqual(app.gone[-1], expected)
        bar.var.set("alpha")
        bar._refresh()
        bar.open_top()
        self.assertEqual(app.gone[-1], ("Film", {"film_key": a, "title": "Alpha (2001)"}))
        bar._move(1)                                      # Down to the next match, Enter opens it
        chosen = bar.lines[bar.selected][1]
        bar._enter()
        self.assertEqual(app.gone[-1], bar.target(chosen))
        app.goto = lambda *a, **k: None                   # a tab that isn't there says so
        bar.open({"kind": "genre", "id": "Horror", "label": "Horror"})
        self.assertEqual(app.status, "The Watch Next tab isn't available.")

    def test_keys(self):
        app, bar = self.make_bar()
        bar.catalog_changed(self.catalog, "ready")
        bar.var.set("alpha")
        bar._refresh()
        self.assertEqual(bar._escape(), "break")          # (no list showing) Escape clears the box
        self.assertEqual(bar.var.get(), "")
        with mock.patch.object(bar.entry, "focus_set") as focus:
            self.assertEqual(bar.shortcut(), "break")
            focus.assert_called_once()
        bar.var.set("alph")                                # typing searches a moment later
        self.assertIsNotNone(bar._pending)
        time.sleep(0.1)
        self.root.update()
        self.assertEqual(bar.rows[0][1][0]["label"], "Alpha (2001)")
        self.assertIsNone(bar.popup)

    def test_ctrl_tab_from_the_box_switches_tabs(self):
        from tkinter import ttk
        app, bar = self.make_bar()
        notebook = ttk.Notebook(self.root)
        for text in ("One", "Two", "Three"):
            notebook.add(ttk.Frame(notebook), text=text)
        notebook.pack()
        app.notebook = notebook
        self.root.update()
        for seq in ("<Control-Tab>", "<Control-Shift-Tab>", "<Control-Next>", "<Control-Prior>"):
            self.assertTrue(bar.entry.bind(seq), seq)
        self.assertEqual(bar._cycle_tabs(1), "break")                 # ('break': not the box's own Tab / Page keys)
        self.assertEqual(notebook.index("current"), 1)
        bar._cycle_tabs(-1)
        bar._cycle_tabs(-1)
        self.assertEqual(notebook.index("current"), 2)

    def test_only_the_window_itself_moving_closes_the_list(self):
        from tkinter import ttk
        app, bar = self.make_bar()
        calls = []
        bar.hide = lambda: calls.append("hide")
        child = ttk.Frame(self.root)
        child.pack()
        self.root.update()
        calls.clear()
        child.event_generate("<Configure>")          # (a widget in the window laid out: no Python called for it)
        self.assertEqual(calls, [])
        self.root.event_generate("<Configure>")      # the window moved or resized: the list closes
        self.assertEqual(calls, ["hide"])
        bar.destroy()                                # (and its binding goes with it)
        self.assertNotIn("catch", self.root.bind("<Configure>"))
        self.root.event_generate("<Configure>")

    def test_the_film_tabs_search_button(self):
        app, tab = self.make()
        bar = F.GlobalSearch(self.root, app)
        app.search_bar = bar
        tab.shown()
        self.root.update()
        with mock.patch.object(bar, "focus") as focus:
            buttons(tab.page)["Search  (Ctrl+F)"].invoke()
            focus.assert_called_once()


# ---------------------------------------------------------------------------------------------------------
class MainWindowTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = hidden_root()
        except Exception as exc:
            self.skipTest(f"Tk unavailable: {exc}")
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "com.plexapp.plugins.library.db-2026-09-25")
        build_fixture(self.db)
        from projectionist import gui
        self.gui = gui
        self.old = gui.SETTINGS_FILE, gui.SETTINGS_DIR
        gui.SETTINGS_FILE, gui.SETTINGS_DIR = os.path.join(self.tmp.name, "settings.json"), self.tmp.name

    def tearDown(self):
        self.gui.SETTINGS_FILE, self.gui.SETTINGS_DIR = self.old
        self.tmp.cleanup()

    def test_the_search_box_above_the_tabs(self):
        app = self.gui.App(self.root, self.db)
        try:
            self.assertIsInstance(app.search_bar, F.GlobalSearch)
            for seq in ("<Control-f>", "<Control-k>", "<Control-F>", "<Control-K>"):
                self.assertTrue(self.root.bind(seq), seq)
            # a text box's own Ctrl+K ('delete to the end') would run first: here it goes to the search box instead
            import re
            from tkinter import ttk
            for cls in ("TEntry", "TCombobox"):
                script = self.root.bind_class(cls, "<Control-Key-k>")
                self.assertNotIn("delete", script, cls)
                self.assertIn("if {", script, cls)
            entry = ttk.Entry(self.root)
            entry.insert(0, "Jackie Chan")
            entry.icursor(3)
            script = self.root.bind_class("TEntry", "<Control-Key-k>")      # (as Tk runs it for Ctrl+K in a box)
            with mock.patch.object(app.search_bar, "focus") as focus:
                self.root.tk.eval("catch {" + re.sub(r"%(\S)", lambda m: entry._w if m.group(1) == "W" else "0",
                                                     script) + "}")
                focus.assert_called_once()
            self.assertEqual(entry.get(), "Jackie Chan")
            self.assertIn("Film", [t.title for t in app.tabs])
            app._pick_initial_db(self.db)
            deadline = time.time() + 30
            while not (app.catalog_state == "ready" and app.search_bar.ready()):
                self.root.update()
                time.sleep(0.02)
                self.assertLess(time.time(), deadline, app.catalog_state)
            app.search_bar.query("matrix")
            self.assertEqual(app.search_bar.results()[0]["label"], "The Matrix (1999)")
            app.search_bar.open_top()
            self.assertIs(app.current_tab().__class__, F.Tab)
            self.assertEqual(app.current_tab().header_title.cget("text"), "The Matrix (1999)")
            self.assertIsNone(app.search_bar.popup)
        finally:
            app.shutdown()

    def test_the_export_page_keeps_its_button_and_log_in_a_short_window(self):
        import json
        from projectionist.ui import theme
        s = theme.scale(self.root)
        with open(self.gui.SETTINGS_FILE, "w", encoding="utf-8") as f:          # (the smallest window)
            json.dump({"window_geometry": f"{int(900 * s)}x{int(620 * s)}"}, f)
        app = self.gui.App(self.root, None)
        self.root.after_cancel(app._timers.pop("pick"))
        try:
            page = self.root.nametowidget(app.notebook.tabs()[0])

            def settle():
                app.set_status("-")                             # (lays the hidden window out)
                for _ in range(5):
                    self.root.update()
                    time.sleep(0.02)

            def top(w):
                y = 0
                while w is not page:
                    y, w = y + w.winfo_y(), w.master
                return y
            app._load_catalog = lambda path: None               # (the library list is all this needs)
            app.load_db(self.db)
            settle()
            if page.winfo_height() < 100:
                self.skipTest("the hidden window wasn't laid out")
            self.assertTrue(app._sheets_compact)                # names only: the descriptions are in tooltips
            self.assertFalse(app._subtitle.winfo_manager())
            self.assertLessEqual(top(app.export_btn) + app.export_btn.winfo_height(), page.winfo_height())
            self.assertGreaterEqual(page.winfo_height() - top(app.log.frame), int(40 * s))   # a few lines of log
            with mock.patch.object(page, "winfo_height", return_value=int(900 * s)):
                app._fit_export()                               # a taller window: the full list again
                self.assertFalse(app._sheets_compact)
                self.assertTrue(app._subtitle.winfo_manager())
            app._fit_export()
            self.assertTrue(app._sheets_compact)
        finally:
            app.shutdown()

    def test_a_broken_search_box_doesnt_stop_the_app(self):
        with mock.patch.dict(sys.modules, {"projectionist.ui.film": None}):
            app = self.gui.App(self.root, None)
        try:
            self.assertIsNone(app.search_bar)
            log = app.log.get("1.0", "end")
            self.assertIn("The search box couldn't be loaded", log)
            self.assertNotIn("Film", [t.title for t in app.tabs])
            self.assertIn("Credits", [t.title for t in app.tabs])
        finally:
            app.shutdown()


if __name__ == "__main__":
    unittest.main()
