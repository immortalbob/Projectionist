"""The Overview tab: the collection at a glance.

Headline numbers along the top, then a grid of small charts - films by decade and library, the top genres and
countries, the most-featured actors and directors, your ratings against IMDb, how much of each library you've
played, and the resolutions of your copies. Bars and dots are clickable where another tab can take it further:
a decade, library, genre or country opens Watch Next, a person opens their Six Degrees profile, a rated film opens
its page on the Film tab (where "More like this in Watch Next" is a click away).

Everything comes from one request, {"action": "overview"} (a tenth of a second or two for a collection of a few
thousand films), worked out on a background thread when the tab is first shown after a collection loads.
"""

from __future__ import annotations

import math
import os
import time
import tkinter as tk
from tkinter import ttk

from . import charts as C
from . import theme as T
from .base import BaseTab
from .widgets import Card, ChartView, ScrollFrame

TWO_COLUMNS_FROM = 940     # width of the card area (device-independent px) from which cards sit two to a row
PAD = 16                   # page margin
GAP = 12                   # space between cards
BAR_ROW = 26               # one row of charts.bars (device-independent px)
TILE_H = 78                # one row of headline tiles
TILE_MIN_W = 130           # charts.tiles' narrowest tile
TOP_COUNTRIES = 10

# Plex uses ISO names for some countries; show the everyday name (the tooltip keeps Plex's).
SHORT_COUNTRY = {
    "United States of America": "United States",
    "United Kingdom of Great Britain and Northern Ireland": "United Kingdom",
    "Taiwan, Province of China": "Taiwan",
    "Korea, Republic of": "South Korea",
    "Korea, Democratic People's Republic of": "North Korea",
    "Russian Federation": "Russia",
    "Iran, Islamic Republic of": "Iran",
    "Viet Nam": "Vietnam",
    "Czechia": "Czech Republic",
    "Hong Kong SAR China": "Hong Kong",
}

RESOLUTION_NAMES = {"8K": "8K", "4K": "4K Ultra HD", "1080p": "1080p Full HD", "720p": "720p HD",
                    "576p": "576p (PAL DVD)", "480p": "480p (NTSC DVD)", "SD": "Standard definition"}


# ---------------------------------------------------------------------------------------------------------
# Words and numbers
# ---------------------------------------------------------------------------------------------------------
def films_text(n: int) -> str:
    return f"{n:,} film" if n == 1 else f"{n:,} films"


def share_text(n: float, total: float) -> str:
    """'53%', or 'under 1%' for a small non-zero share."""
    if not total:
        return "0%"
    share = n / total
    if 0 < share < 0.005:
        return "under 1%"
    return f"{share:.0%}"


def agreement_words(r: float | None) -> str | None:
    """A correlation in plain words."""
    if r is None:
        return None
    if r >= 0.8:
        return f"Your scores follow IMDb's very closely (correlation {r:.2f})."
    if r >= 0.6:
        return f"Your scores follow IMDb's fairly closely (correlation {r:.2f})."
    if r >= 0.4:
        return f"Your scores loosely follow IMDb's (correlation {r:.2f})."
    if r >= 0.2:
        return f"Your scores only slightly follow IMDb's (correlation {r:.2f})."
    if r > -0.2:
        return f"Your scores and IMDb's have little to do with each other (correlation {r:.2f})."
    return f"You tend to like what IMDb doesn't, and vice versa (correlation {r:.2f})."


def averages_words(yours: float | None, imdb: float | None, count: int) -> str | None:
    if yours is None or imdb is None or not count:
        return None
    diff = yours - imdb
    head = (f"On the {films_text(count)} you and IMDb have both rated, you average {yours:.1f} and IMDb "
            f"{imdb:.1f}")
    if abs(diff) < 0.05:
        return head + " - just the same."
    return head + f" - you're {abs(diff):.1f} points {'more generous' if diff > 0 else 'tougher'}."


def film_label(title: str, year) -> str:
    return f"{title} ({year})" if year else str(title)


def _rating(v) -> str:
    return f"{v:g}" if isinstance(v, (int, float)) else str(v)


# ---------------------------------------------------------------------------------------------------------
# Chart data from the overview answer (plain functions, so they're easy to test)
# ---------------------------------------------------------------------------------------------------------
def kpi_items(data: dict) -> list[dict]:
    t = data.get("totals", {})
    films = t.get("films", 0)
    libraries = t.get("libraries", 0)
    hours = t.get("hours", 0)
    rated = t.get("rated_by_you", 0)
    played = t.get("played_by_you", 0)
    people = t.get("people", 0)
    credits = t.get("credits_scanned", 0)
    yours, imdb = t.get("your_average"), t.get("imdb_average_of_those")
    if rated and yours is not None and imdb is not None:
        rated_note = f"avg {yours:.1f} (IMDb {imdb:.1f})"
        rated_tip = (f"You've rated {films_text(rated)}\nYour average score: {yours:.2f} out of 10\n"
                     f"IMDb's average for the same films: {imdb:.2f}")
    elif rated and yours is not None:
        rated_note = f"avg {yours:.1f} out of 10"
        rated_tip = f"You've rated {films_text(rated)}\nYour average score: {yours:.2f} out of 10"
    else:
        rated_note = "none yet"
        rated_tip = "You haven't rated any films yet\nRate films in Plex and they'll show up here."
    days = hours / 24
    items = t.get("library_items") or films
    if items > films:
        films_tip = (f"{films_text(films)} ({items:,} library items)\nA film in several libraries, or with "
                     f"several copies, counts once.\nThe Export tab counts every library item.")
    else:
        films_tip = f"{films_text(films)}\nA film in several libraries, or with several copies, counts once."
    return [
        {"label": "Films", "value": f"{films:,}",
         "note": f"in {libraries} {'library' if libraries == 1 else 'libraries'}", "tip": films_tip},
        {"label": "Hours of film", "value": f"{hours:,}",
         "note": f"{days:,.0f} days non-stop" if days >= 2 else "back to back",
         "tip": f"{hours:,} hours of film\nThat's {days:,.0f} days of watching, day and night."},
        {"label": "Rated by you", "value": f"{rated:,}", "note": rated_note, "tip": rated_tip},
        {"label": "Played by you", "value": f"{played:,}", "note": f"{share_text(played, films)} of your films",
         "tip": f"Played by you: {films_text(played)}\nFilms your Plex account has played at least once."},
        {"label": "People", "value": f"{people:,}", "note": "cast and directors",
         "tip": f"{people:,} people\nEveryone credited in a cast or as a director."},
        {"label": "Credits markers", "value": f"{credits:,}", "note": f"{share_text(credits, films)} of your films",
         "tip": f"{films_text(credits)} with credits markers\nPlex has marked where the end credits run - "
                "the Credits tab checks them for scenes after the credits."},
    ]


def decade_items(data: dict) -> list[dict]:
    total = data.get("totals", {}).get("films", 0)
    return [{"label": d["label"], "value": d["films"], "key": d["decade"],
             "tip": f"The {d['label']}\n{films_text(d['films'])} ({share_text(d['films'], total)} of your films)"
                    f"\nClick for films to watch next from the {d['label']}"}
            for d in data.get("by_decade", [])]


def library_items(data: dict) -> list[dict]:
    return [{"label": d["label"], "value": d["films"], "key": d["label"],
             "tip": f"{d['label']}\n{films_text(d['films'])} (each counts once, however many copies)"
                    f"\nClick for films to watch next from this library"}
            for d in data.get("by_library", [])]


def genre_items(data: dict, count: int = 12) -> list[dict]:
    total = data.get("totals", {}).get("films", 0)
    return [{"label": d["label"], "value": d["films"], "key": d["label"],
             "tip": f"{d['label']}\n{films_text(d['films'])} ({share_text(d['films'], total)} of your films)"
                    f"\nClick for {d['label'].lower()} films to watch next"}
            for d in data.get("genres", [])[:count]]


def country_items(data: dict, count: int = TOP_COUNTRIES) -> list[dict]:
    total = data.get("totals", {}).get("films", 0)
    # the key stays Plex's name: it's what Watch Next filters on
    return [{"label": SHORT_COUNTRY.get(d["label"], d["label"]), "value": d["films"], "key": d["label"],
             "tip": f"{d['label']}\n{films_text(d['films'])} ({share_text(d['films'], total)} of your films)"
                    f"\nClick for films to watch next from this country"}
            for d in data.get("countries", [])[:count]]


def people_items(rows: list[dict], what: str) -> list[dict]:
    """rows: [{id, name, films}]; what: how the count reads in the tooltip ('films', 'films as a lead'...)."""
    return [{"label": r["name"], "value": r["films"], "key": r["id"],
             "tip": f"{r['name']}\n{r['films']:,} {what}\nClick to see their profile in Six Degrees"}
            for r in rows]


def played_items(data: dict) -> list[dict]:
    rows = sorted(data.get("watched_by_library", []), key=lambda d: (-d.get("share", 0), d["label"].casefold()))
    return [{"label": d["label"], "value": d.get("share", 0), "key": d["label"],
             "tip": f"{d['label']}\nplayed {d.get('played', 0):,} of {d['films']:,}"
                    f"\nClick for films to watch next from this library"}
            for d in rows]


def resolution_items(data: dict) -> list[dict]:
    rows = data.get("resolutions", [])
    total = sum(d["films"] for d in rows)
    return [{"label": d["label"], "value": d["films"], "key": d["label"],
             "tip": f"{RESOLUTION_NAMES.get(d['label'], d['label'])}\n{films_text(d['films'])} "
                    f"({share_text(d['films'], total)} of your films)"}
            for d in rows]                               # already in natural order: 4K, 1080p, ... SD


def rating_items(data: dict) -> list[dict]:
    return [{"label": str(d["rating"]), "value": d["films"], "key": d["rating"],
             "tip": f"{d['rating']} out of 10\nYou gave {films_text(d['films'])} this score"}
            for d in data.get("your_ratings", [])]


def scatter_points(data: dict) -> list[dict]:
    points = []
    for d in data.get("ratings_vs_imdb", []):
        if d.get("yours") is None or d.get("imdb") is None:
            continue
        label = film_label(d["title"], d.get("year"))
        # the key is the film's own (another film can share its title and year); the label goes with it
        points.append({"x": d["imdb"], "y": d["yours"], "key": d.get("key") or label, "label": label,
                       "tip": f"{label}: you {_rating(d['yours'])}, IMDb {_rating(d['imdb'])}"
                              f"\nClick to open its page"})
    return points


def scatter_subtitle(points: list[dict], agreement: float | None) -> str:
    """The findings in plain words: the two averages over the plotted films, and how closely they agree."""
    count = len(points)
    yours = sum(p["y"] for p in points) / count if count else None
    imdb = sum(p["x"] for p in points) / count if count else None
    parts = [averages_words(yours, imdb, count), agreement_words(agreement) if count >= 3 else None]
    return " ".join(p for p in parts if p)


def scores_note(data: dict) -> str:
    """Your scoring habits in plain words: the score you give most, and the part of the scale you use."""
    used = [(d["rating"], d["films"]) for d in data.get("your_ratings", []) if d.get("films")]
    if not used:
        return ""
    total = sum(n for _, n in used)
    top, top_n = max(used, key=lambda rn: (rn[1], rn[0]))
    lo, hi = min(r for r, _ in used), max(r for r, _ in used)
    text = f"Your most common score is {top} ({films_text(top_n)}, {share_text(top_n, total)} of your ratings)."
    if lo == hi:
        return text
    if lo > 1 and hi < 10:
        return text + f" You only use {lo} to {hi}."
    if lo > 1:
        return text + f" You've never given less than {lo}."
    if hi < 10:
        return text + f" You've never given more than {hi}."
    return text + " You use the whole scale, 1 to 10."


def source_text(catalog) -> str:
    """Whose ratings 'you' means, and which database this is: 'Ratings and plays: Ann  ·  Plex backup from ...'."""
    bits = []
    owner = getattr(catalog, "owner", "") or ""
    if owner:
        bits.append(f"Ratings and plays: {owner}")
    source = getattr(catalog, "source", "") or ""
    if source and os.path.isfile(source):
        from ..files import dump_date
        when = dump_date(source)
        bits.append(f"Plex backup from {when}" if when in os.path.basename(source) else f"Database from {when}")
    return "   ·   ".join(bits)


def overview_answer(catalog) -> dict:
    """The overview request (runs on a background thread: reads the catalog, touches no widgets)."""
    from ..ask import handle
    return handle({"action": "overview", "count": 12}, catalog=catalog)


def bars_height(n: int) -> int:
    """Height (device-independent px) that fits n rows of charts.bars."""
    return max(n, 3) * BAR_ROW + 8


# ---------------------------------------------------------------------------------------------------------
class Tab(BaseTab):
    title = "Overview"

    def __init__(self, app, notebook):
        super().__init__(app, notebook)
        self.s = T.scale(self.frame)
        self.data: dict | None = None
        self._filled_for = None            # the catalog the charts show
        self._asking_for = None            # the catalog an overview is being worked out for
        self._token = 0                    # bumped on every catalog change, so late answers are dropped
        self.columns = 0                   # cards per row (1 or 2), set by _reflow
        self.timings: dict[str, float] = {}
        self.views: dict[str, ChartView] = {}
        self.cards: dict[str, Card] = {}
        self._film_labels: dict[str, str] = {}     # the ratings scatter's dots: film key -> 'Title (Year)'
        self.cast_var = tk.StringVar(value="lead")
        self._build()
        self._show_state()

    # -- layout -----------------------------------------------------------------------------------------------
    def _build(self):
        f = self.frame
        f.columnconfigure(0, weight=1)
        f.rowconfigure(1, weight=1)

        head = ttk.Frame(f, style="Page.TFrame", padding=(PAD, 12, PAD, 8))
        head.grid(row=0, column=0, sticky="ew")
        head.columnconfigure(1, weight=1)
        ttk.Label(head, text="Your collection at a glance", style="PageTitle.TLabel").grid(row=0, column=0,
                                                                                           sticky="w")
        self.source_label = ttk.Label(head, text="", style="PageHint.TLabel")
        self.source_label.grid(row=0, column=1, sticky="e", padx=(16, 0))

        # What shows instead of the charts while there's no collection
        self.placeholder = ChartView(f, height=320, background=T.PAGE)
        self.placeholder.grid(row=1, column=0, sticky="nsew")

        self.content = ttk.Frame(f, style="Page.TFrame")
        self.content.grid(row=1, column=0, sticky="nsew")
        self.content.columnconfigure(0, weight=1)
        self.content.rowconfigure(0, weight=1)

        # The headline tiles scroll with the charts, so a short window keeps its height for the cards
        self.scroll = ScrollFrame(self.content, background=T.PAGE, style="Page.TFrame")
        self.scroll.grid(row=0, column=0, sticky="nsew")
        self.scroll.canvas.bind("<Configure>", lambda e: self._reflow(e.width), add="+")
        page = self.scroll.inner
        page.columnconfigure(0, weight=1)
        self.kpi_card = Card(page, padding=(14, 10))
        self.kpi_card.grid(row=0, column=0, sticky="ew", padx=PAD, pady=(0, GAP))
        self.kpi_card.body.columnconfigure(0, weight=1)
        self.views["kpi"] = kpi = ChartView(self.kpi_card.body, height=TILE_H)
        kpi.grid(row=0, column=0, sticky="ew")
        kpi.bind("<Configure>", self._fit_kpi, add="+")
        self.card_grid = ttk.Frame(page, style="Page.TFrame")      # the chart cards, one or two to a row
        self.card_grid.grid(row=1, column=0, sticky="nsew")

        self._chart_card("decades", "Films by decade",
                         "When your films came out. Click a decade for films to watch next from it.", 210)
        self._chart_card("libraries", "Films per library",
                         "Your Plex movie libraries. A film counts once per library, however many copies you have "
                         "- the Export tab counts every copy (library items), so its numbers can be higher. Click "
                         "one for films to watch next from it.", bars_height(6))
        self._chart_card("genres", "Top genres",
                         "A film can have several genres, so these add up to more than your total. "
                         "Click a genre for films to watch next in it.", bars_height(12))
        self._chart_card("countries", "Top countries",
                         "Where your films were made - a co-production counts for each of its countries. "
                         "Click a country for films to watch next from it.",
                         bars_height(TOP_COUNTRIES))
        card = self._chart_card("actors", "Most-featured actors",
                                "All roles counts every appearance, so it's topped by prolific supporting "
                                "players. Lead roles counts only films where they're billed in the top 3 - the "
                                "stars your collection is built around. Click a name to see their profile.",
                                bars_height(12), chart_row=1)
        toggle = ttk.Frame(card.body, style="Card.TFrame", padding=0)
        toggle.configure(relief="flat", borderwidth=0)
        toggle.grid(row=0, column=0, sticky="w", pady=(0, 6))
        self.cast_buttons = {}
        for n, (value, text) in enumerate((("all", "All roles"), ("lead", "Lead roles (top 3 billed)"))):
            rb = ttk.Radiobutton(toggle, text=text, value=value, variable=self.cast_var, style="Card.TRadiobutton",
                                 command=self._draw_actors)
            rb.grid(row=0, column=n, sticky="w", padx=(0, 16))
            self.cast_buttons[value] = rb
        self._chart_card("directors", "Most-featured directors",
                         "The directors with the most films on your shelves. Click a name to see their profile.",
                         bars_height(12))
        card = self._chart_card("ratings", "Your ratings vs IMDb",
                                "Each dot is a film, placed by IMDb's rating (across) and yours (up). Dots above "
                                "the grey line are films you rate higher than IMDb does. Click one to open its "
                                "page.", 320, chart_row=1)
        # the findings in plain words (a label rather than the chart's subtitle, so it wraps to any width)
        self.ratings_note = ttk.Label(card.body, text="", style="CardNote.TLabel", justify="left", wraplength=500)
        self.ratings_note.grid(row=0, column=0, sticky="w", pady=(0, 6))
        card.wrap_labels.append(self.ratings_note)
        card = self._chart_card("scores", "How you rate", "How many films you've given each score out of 10.", 210,
                                chart_row=1)
        self.scores_note = ttk.Label(card.body, text="", style="CardNote.TLabel", justify="left", wraplength=500)
        self.scores_note.grid(row=0, column=0, sticky="w", pady=(0, 6))
        card.wrap_labels.append(self.scores_note)
        self._chart_card("played", "How much you've played, per library",
                         "The share of each library you've played at least once - the faint track behind each bar "
                         "is every film in it. Click one for films to watch next from it.", bars_height(6))
        self._chart_card("resolutions", "Resolutions",
                         "The best copy of each film, from 4K down to standard definition.", bars_height(6))
        self._reflow(0)

    def _chart_card(self, key, title, hint, height, chart_row=0) -> Card:
        card = Card(self.card_grid, title, hint)
        card.body.columnconfigure(0, weight=1)
        card.body.rowconfigure(chart_row, weight=1)
        view = ChartView(card.body, height=height)
        view.grid(row=chart_row, column=0, sticky="nsew")
        card.wrap_labels = [card.hint_label] if hasattr(card, "hint_label") else []
        card.bind("<Configure>", lambda e, c=card: self._wrap_labels(c, e.width), add="+")
        self.cards[key] = card
        self.views[key] = view
        return card

    @staticmethod
    def _wrap_labels(card: Card, width: int):
        """Card hints wrap at a fixed width; wrap them to the card instead, so nothing's cut off."""
        if width < 60:
            return
        wrap = max(width - 30, 120)
        for label in card.wrap_labels:
            if int(str(label.cget("wraplength")) or 0) != wrap:
                label.configure(wraplength=wrap)

    def _reflow(self, width: int | None = None):
        """Two cards to a row when there's room, one when there isn't."""
        if not width:
            width = self.scroll.canvas.winfo_width()
        cols = 2 if width / self.s >= TWO_COLUMNS_FROM else 1
        if width < 50 and self.columns:          # not laid out yet: keep what we have
            return
        if cols == self.columns:
            return
        self.columns = cols
        for c in range(2):
            self.card_grid.columnconfigure(c, weight=1 if c < cols else 0, uniform="cards" if c < cols else "",
                                           minsize=0)
        for n, card in enumerate(self.cards.values()):
            row, col = divmod(n, cols)
            if cols == 1:
                padx = (PAD, PAD)
            else:
                padx = (PAD, GAP // 2) if col == 0 else (GAP // 2, PAD)
            card.grid(row=row, column=col, sticky="nsew", padx=padx, pady=(0, GAP))

    def _fit_kpi(self, event=None):
        """Tiles wrap onto a second row in a narrow window: make room for it."""
        view = self.views["kpi"]
        width = event.width if event is not None else view.winfo_width()
        if width < 50:
            return
        count = 6
        per_row = max(1, min(count, int(width // (TILE_MIN_W * self.s))))
        want = int(math.ceil(count / per_row) * TILE_H * self.s)
        if int(view.cget("height")) != want:
            view.configure(height=want)

    # -- called by the main window -----------------------------------------------------------------------------
    def catalog_changed(self, catalog, state: str):
        super().catalog_changed(catalog, state)
        self._token += 1                          # whatever was being worked out is for the old collection
        self.data = None
        self._filled_for = self._asking_for = None
        self._show_state()
        if self.state_message() is None and self._visible():
            self._fill()

    def shown(self):
        if (self.state_message() is None and self._filled_for is not self.catalog
                and self._asking_for is not self.catalog):
            self._fill()

    def navigate(self, **kwargs):
        self.shown()
        self.scroll.to_top()

    def text_size_changed(self) -> bool:
        """Settings > Text size: re-measured in place (the charts keep what they show; one or two cards to a row,
        and the tiles' rows, worked out again in the new scale)."""
        self.s = T.scale(self.frame)
        self.columns = 0
        self._reflow()
        self._fit_kpi()
        return True

    # -- content --------------------------------------------------------------------------------------------------
    def _visible(self) -> bool:
        current = getattr(self.app, "current_tab", None)
        try:
            return (callable(current) and current() is self) or bool(self.frame.winfo_ismapped())
        except tk.TclError:
            return False

    def _show_state(self):
        """The placeholder while there's no collection (loading, error, none)."""
        msg = self.state_message()
        if msg is not None:
            self._show_placeholder(*msg)
            self.source_label.configure(text="")

    def _show_placeholder(self, text: str, sub: str | None = None):
        self.content.grid_remove()
        self.placeholder.grid()
        self.show_message(self.placeholder, text, sub)

    def _show_content(self):
        self.placeholder.grid_remove()
        self.content.grid()

    def _fill(self):
        """Work out the overview in the background (a tenth of a second or two for a big collection - enough
        to make switching tabs stutter on the UI thread), then draw every chart."""
        catalog = self.catalog
        if catalog is None:
            return
        self._token += 1
        token = self._token
        self._asking_for = catalog
        started = time.perf_counter()
        if self._filled_for is not catalog:
            self._show_placeholder("Putting the overview together...")

        def done(answer):
            if token != self._token or catalog is not self.catalog:
                return                             # the collection changed meanwhile
            self._asking_for = None
            self.timings["ask"] = time.perf_counter() - started
            self._apply(answer, catalog)

        def failed(message):
            if token != self._token:
                return
            self._asking_for = None
            self._show_placeholder("Couldn't put the overview together", message)

        # key: a newer overview (another collection) calls this one off - the tokens above drop a late answer too
        self.app.run(lambda: overview_answer(catalog), done, failed, key="overview.ask")

    def _apply(self, answer: dict, catalog):
        if not answer.get("ok"):
            self._show_placeholder("Couldn't put the overview together",
                                   answer.get("error") or "Try reloading the database on the Export tab.")
            return
        self.data = answer
        self._filled_for = catalog
        films = answer.get("totals", {}).get("films", 0)
        self.source_label.configure(text=source_text(catalog))
        if not films:
            self._show_placeholder("There are no films in this collection",
                                   "Pick another Plex database on the Export tab.")
            return
        self._show_content()
        started = time.perf_counter()
        self._draw_all()
        self.timings["draw"] = time.perf_counter() - started

    def _draw_all(self):
        data = self.data or {}
        v = self.views
        kpis = kpi_items(data)
        v["kpi"].show(lambda p: C.tiles(p, kpis))

        decades = decade_items(data)
        v["decades"].show(lambda p: _or_message(p, decades, lambda: C.columns(p, decades, on_click=self._open_decade),
                                                "No release years to show",
                                                "Plex doesn't know when these films came out."))
        libraries = library_items(data)
        v["libraries"].show(lambda p: C.bars(p, libraries, on_click=self._open_library),
                            height=bars_height(len(libraries)))
        genres = genre_items(data)
        v["genres"].show(lambda p: _or_message(p, genres, lambda: C.bars(p, genres, on_click=self._open_genre),
                                               "No genres to show", "Plex hasn't matched genres for these films."),
                         height=bars_height(len(genres)))
        countries = country_items(data)
        v["countries"].show(lambda p: _or_message(p, countries,
                                                  lambda: C.bars(p, countries, on_click=self._open_country),
                                                  "No countries to show",
                                                  "Plex hasn't recorded where these films were made."),
                            height=bars_height(len(countries)))
        self._draw_actors()
        directors = people_items(data.get("top_directors", []), "films directed")
        v["directors"].show(lambda p: _or_message(p, directors,
                                                  lambda: C.bars(p, directors, on_click=self._open_director),
                                                  "No directors to show",
                                                  "Plex hasn't recorded who directed these films."),
                            height=bars_height(len(directors)))
        points = scatter_points(data)
        self._film_labels = {p["key"]: p["label"] for p in points}
        self._note(self.ratings_note, scatter_subtitle(points, data.get("agreement_with_imdb")))
        self._note(self.scores_note, scores_note(data))
        # keep_side: the small nudge that keeps stacked dots apart never carries a film across the grey line, so a
        # dot above it always means you rate that film higher than IMDb does
        v["ratings"].show(lambda p: _or_message(
            p, points, lambda: C.scatter(p, points, x_label="IMDb rating", y_label="Your rating",
                                         on_click=self._open_film, keep_side=True),
            "No films rated by you and IMDb yet",
            "Rate films in Plex (the stars) and they'll show up here next to IMDb's rating."))
        scores = rating_items(data)
        has_scores = any(d["value"] for d in scores)
        v["scores"].show(lambda p: _or_message(p, scores if has_scores else [], lambda: C.columns(p, scores),
                                               "You haven't rated any films yet",
                                               "Rate films in Plex (the stars) to see how you score them."))
        played = played_items(data)
        # shares of each library, so a bar's length is out of 100% (not out of the most-played library)
        v["played"].show(lambda p: C.bars(p, played, value_fmt=C.fmt_pct, max_value=1, track=True,
                                          on_click=self._open_library),
                         height=bars_height(len(played)))
        resolutions = resolution_items(data)
        v["resolutions"].show(lambda p: _or_message(p, resolutions, lambda: C.bars(p, resolutions),
                                                    "No resolutions to show",
                                                    "Plex hasn't analysed these files yet."),
                              height=bars_height(len(resolutions)))

    @staticmethod
    def _note(label: ttk.Label, text: str):
        label.configure(text=text)
        if text:
            label.grid()
        else:
            label.grid_remove()

    def _draw_actors(self):
        data = self.data or {}
        lead = self.cast_var.get() == "lead"
        rows = data.get("top_lead_actors" if lead else "top_actors", [])
        items = people_items(rows, "films in a lead role (billed in the top 3)" if lead else "films in any role")
        self.views["actors"].show(lambda p: _or_message(p, items, lambda: C.bars(p, items, on_click=self._open_person),
                                                        "No cast to show",
                                                        "Plex hasn't recorded who's in these films."),
                                  height=bars_height(len(items)))

    # -- clicks: over to the other tabs ------------------------------------------------------------------------
    def _goto(self, tab: str, **kwargs):
        if self.app.goto(tab, **kwargs) is None:
            self.app.set_status(f"The {tab} tab isn't available.")

    def _open_decade(self, decade):
        self._goto("Watch Next", decade=int(decade))

    def _open_library(self, library):
        self._goto("Watch Next", library=library)

    def _open_genre(self, genre):
        self._goto("Watch Next", genre=genre)

    def _open_country(self, country):
        self._goto("Watch Next", country=country)

    def _open_film(self, key):
        """A scatter dot: the film's page, by its key (the title alone could be another film's). Films like it are a
        click away there."""
        label = self._film_labels.get(key, key)
        self._goto("Film", **({"film_key": key} if key != label else {}), title=label)

    def _open_person(self, person_id, directors: bool = False):
        data = self.data or {}
        name = next((r["name"] for key in ("top_lead_actors", "top_actors", "top_directors")
                     for r in data.get(key, []) if r["id"] == person_id), "")
        self._goto("Six Degrees", person_id=person_id, name=name, **({"directors": True} if directors else {}))

    def _open_director(self, person_id):
        """A bar on the directors chart: their profile with the films they directed counted too - some of them
        act in more films than they direct (Clint Eastwood, say), and would otherwise open as actors."""
        self._open_person(person_id, directors=True)


def _or_message(p, items, draw, text: str, sub: str):
    """Draw the chart, or a calm note saying why there's nothing to draw."""
    if items:
        draw()
    else:
        C.message(p, text, sub)
