"""The Credits tab - "Stay after the credits?": which films have a scene during or after the end credits.

Everything comes from Plex's credits markers, read by projectionist.credits (the ask action "credits"). Plex marks
each stretch of end credits it finds; footage between two stretches, or after the last one, is usually a mid- or
post-credits scene. Every film gets a verdict - Yes, Maybe, None found, or Not scanned (no markers to go on) -
and those verdicts are statuses, so they're the only things drawn in status colours. None found is never a
promise: a gag in the last few seconds, or outtakes running beside the credits, can hide under Plex's final
credits marker.

Layout (under the page title, like the other tabs): the verdicts across the collection (a stacked bar - click a
colour to list those films) beside the share of films with a scene by decade (click a decade to list it), the
filters, the list of films, and the selected film's details: its verdict and, for each copy, a timeline of how
the film ends, as wide as the details panel. Every time the tab shows is cut to the whole second (as
charts.clock writes it), so the timeline, the scene rows and the list always agree.
"""

from __future__ import annotations

import math
import tkinter as tk
from tkinter import ttk

from ..catalog import fold
from ..credits import MODERN_FROM_YEAR, span
from . import charts
from . import theme as T
from .base import BaseTab
from .paint import Painter, TkPainter
from .widgets import Card, ChartView, LinkLabel, ScrollFrame, SearchBox, Table, suggester

YES, MAYBE, NONE_FOUND, NOT_SCANNED = "Yes", "Maybe", "None found", "Not scanned"
VERDICTS = (YES, MAYBE, NONE_FOUND, NOT_SCANNED)
DEFAULT_VERDICTS = (YES, MAYBE)
ASKED_VERDICTS = [YES, MAYBE, NONE_FOUND]       # what the credits request can list (Not scanned comes from the catalog)
ANY = "Any"
# (worked out whenever it's read, so it has the look in use)
COLORS = T.live(lambda: {YES: T.VERDICT_COLORS["Yes"], MAYBE: T.VERDICT_COLORS["Maybe"],
                         NONE_FOUND: T.VERDICT_COLORS["None found"], NOT_SCANNED: T.VERDICT_COLORS[""]})
ICONS = {YES: T.VERDICT_ICONS["Yes"], MAYBE: T.VERDICT_ICONS["Maybe"], NONE_FOUND: T.VERDICT_ICONS["None found"],
         NOT_SCANNED: T.VERDICT_ICONS[""]}
RANK = {YES: 0, MAYBE: 1, NONE_FOUND: 2, NOT_SCANNED: 3}

# What each verdict means, for one film (details) and for a group of films (chart tooltips).
MEANINGS = {
    YES: "Stay for it: there's likely a scene during or after the credits (or outtakes).",
    MAYBE: "Possibly: there's footage after the credits start, but it might only be a title card, music or text.",
    NONE_FOUND: "Plex saw nothing after the credits. That isn't a guarantee: a gag in the last few seconds, or "
                "outtakes running beside the credits, can still slip past.",
    NOT_SCANNED: "Nothing to go on: Plex hasn't marked where the credits are in this film (or only marked "
                 "something it took for credits, with minutes more film after it).",
}
GROUP_MEANINGS = {
    YES: "A likely scene during or after the credits",
    MAYBE: "Footage after the credits that might only be a card or text",
    NONE_FOUND: "Nothing Plex could see after the credits (not a guarantee)",
    NOT_SCANNED: "Plex hasn't marked the credits - nothing to go on",
}
HINT = ("Yes: a likely scene during or after the credits. Maybe: footage that might only be a title card or text. "
        "None found: nothing Plex could see (not a guarantee - a last-second gag can slip past). "
        "Click a colour or a decade to list those films; double-click a film for its page.")
PAGE_HINT = "Which films have a scene during or after the end credits, from where Plex marked the credits."
OLD_RULE = (f"Films from before {MODERN_FROM_YEAR} are a Maybe at most: footage after their end titles is usually "
            "a 'The End' card, exit music or text.")
PAD = 16                             # page margin (as on the other tabs)
SHORT_WINDOW = 640                   # a tab shorter than this (device-independent px) drops its page title

# Table columns: (key, heading, width, anchor), the narrowest each can usefully be, and which go first when
# the list is narrow (the details panel still shows everything).
COLUMNS = [("title", "Title", 220, "w"), ("year", "Year", 50, "e"), ("verdict_text", "Verdict", 100, "w"),
           ("scenes", "Scenes", 240, "w"), ("credits_start", "Credits start", 88, "e"),
           ("library", "Library", 120, "w")]
MIN_WIDTHS = {"title": 130, "year": 46, "verdict_text": 92, "scenes": 170, "credits_start": 84, "library": 96}
DROP_ORDER = ("library", "credits_start", "year")
SORT_KEYS = {
    "title": lambda r: r["title_key"],
    "year": lambda r: (r["year"] or 0, r["title_key"]),
    "verdict_text": lambda r: (RANK[r["verdict"]], r["title_key"]),
    "scenes": lambda r: (r["scene_count"], -(r["credits_start_sec"] or 0)),
    "credits_start": lambda r: (r["credits_start_sec"] is None, r["credits_start_sec"] or 0),
    "library": lambda r: (fold(r["library"]), r["title_key"]),
}
DESCENDING_FIRST = {"scenes"}        # a first click on these puts the most at the top
# Blank cells stay at the bottom whichever way these are sorted.
BLANK = {"credits_start": lambda r: r["credits_start_sec"] is None, "year": lambda r: not r["year"]}
INSET = (12, 8)                      # left and right margins inside a timeline chart (device-independent pixels)


def ordered(rows, key: str, reverse: bool = False, row_of=lambda r: r) -> list:
    """rows sorted by a column (row_of gives the row dict for each), blanks last in both directions."""
    by = SORT_KEYS[key]
    out = sorted(rows, key=lambda r: by(row_of(r)), reverse=reverse)
    blank = BLANK.get(key)
    if blank is not None:
        filled = [r for r in out if not blank(row_of(r))]
        empty = [r for r in out if blank(row_of(r))]
        out = filled + sorted(empty, key=lambda r: row_of(r)["title_key"])
    return out


# ---------------------------------------------------------------------------------------------------------
# Times: whole seconds, the way the rest of the app writes them
# ---------------------------------------------------------------------------------------------------------
def whole(seconds) -> int | None:
    """Seconds cut to the whole second (2:05:11.6 is 2:05:11), as charts.clock writes them."""
    return None if seconds is None else int(math.floor(seconds + 1e-6))


def clock_of(seconds) -> str:
    return "" if seconds is None else charts.clock(whole(seconds))


def scene_times(s: dict) -> tuple[int, int, str]:
    """A scene's start and end (whole seconds) and its length as the difference of the two - so a row reading
    '1:44:04 – 1:44:32  ·  28 s' adds up, and says just what the timeline above it says."""
    start, end = whole(s["start_sec"]), whole(s["end_sec"])
    end = max(end, start)
    return start, end, span((end - start) * 1000)


def scene_summary(s: dict) -> str:
    """'2:05:11 mid-credits (28 s)', with '(maybe)' on the unsure ones - for the list's Scenes column."""
    start, _end, length = scene_times(s)
    text = f"{charts.clock(start)} {s.get('kind', 'Scene').lower()} ({length})"
    return text + (" (maybe)" if s.get("verdict") not in ("Likely", YES) else "")


# ---------------------------------------------------------------------------------------------------------
# The films, as rows
# ---------------------------------------------------------------------------------------------------------
def film_label(title: str, year) -> str:
    return f"{title} ({year})" if year else title


def make_row(title: str, year, plex_ids, libraries, credits: dict, key: str | None = None) -> dict:
    """One film's row: what the list shows, what it sorts and filters on, and its credits answer. key is the
    catalog's film key (Film.key), which other tabs can send the user here by."""
    verdict = credits.get("stay_after_credits") or NOT_SCANNED
    if verdict not in RANK:                   # a verdict this tab doesn't know: nothing it can promise
        verdict = NOT_SCANNED
    scenes = credits.get("scenes") or []
    label = film_label(title, year)
    start = credits.get("credits_start_sec") if verdict != NOT_SCANNED else None
    return {
        "id": tuple(plex_ids or ()) or (label,), "key": key, "title": title, "year": year, "label": label,
        "verdict": verdict, "verdict_text": f"{ICONS[verdict]} {verdict}",
        "scenes": "; ".join(scene_summary(s) for s in scenes), "scene_count": len(scenes),
        "credits_start": clock_of(start), "credits_start_sec": start,
        "libraries": list(libraries or []), "library": ", ".join(libraries or []),
        "decade": (year // 10 * 10) if year else None, "fold": fold(label),
        "title_key": (fold(title, drop_article=True), year or 0), "plex_ids": list(plex_ids or []),
        "credits": credits,
    }


def build_rows(answer: dict, catalog) -> list[dict]:
    """Every film: the scanned ones from the credits answer, the rest (Not scanned) from the catalog."""
    films = catalog.films.values() if catalog is not None else []
    keys = {tuple(film.plex_ids): film.key for film in films}
    rows, seen = [], set()
    for f in answer.get("films") or []:
        credits = {k: v for k, v in f.items() if k not in ("title", "year", "plex_ids", "libraries")}
        ids = tuple(f.get("plex_ids") or ())
        row = make_row(f.get("title", ""), f.get("year"), ids, f.get("libraries"), credits, keys.get(ids))
        rows.append(row)
        seen.add(row["id"])
    for film in films:
        if not film.credits_copies and tuple(film.plex_ids) not in seen:
            rows.append(make_row(film.title, film.year, film.plex_ids, film.libraries,
                                 {"stay_after_credits": ""}, film.key))
    rows.sort(key=SORT_KEYS["title"])
    return rows


def verdict_counts(rows) -> dict:
    counts = dict.fromkeys(VERDICTS, 0)
    for r in rows:
        counts[r["verdict"]] += 1
    return counts


def decade_shares(rows) -> list[dict]:
    """Per decade (from the first with a scanned film to the last): films, scanned films, Yes and Maybe."""
    by = {}
    for r in rows:
        if r["decade"] is None:
            continue
        d = by.setdefault(r["decade"], {"decade": r["decade"], "films": 0, "scanned": 0, YES: 0, MAYBE: 0})
        d["films"] += 1
        if r["verdict"] != NOT_SCANNED:
            d["scanned"] += 1
        if r["verdict"] in (YES, MAYBE):
            d[r["verdict"]] += 1
    scanned = [d for d in by if by[d]["scanned"]]
    if not scanned:
        return []
    out = []
    for dec in range(min(scanned), max(scanned) + 10, 10):
        d = by.get(dec, {"decade": dec, "films": 0, "scanned": 0, YES: 0, MAYBE: 0})
        d["share"] = d[YES] / d["scanned"] if d["scanned"] else 0.0
        out.append(d)
    return out


def copies_of(row: dict) -> list[dict]:
    """The film's scanned copies (each with its own credits result), the one the verdict is for first."""
    credits = row["credits"]
    if row["verdict"] == NOT_SCANNED:
        return []
    copies = credits.get("copies") or [credits]
    return sorted(copies, key=lambda c: RANK.get(c.get("stay_after_credits") or NOT_SCANNED, 3))


def timeline_scenes(copy: dict) -> list[dict]:
    """The copy's scenes as charts.timeline wants them. Starts and ends are cut to the whole second the way the
    rest of the app writes them (2:05:11.6 is '2:05:11'), so the chart's labels, lengths and tooltips say just
    what the list and the scene rows say."""
    out = []
    for s in copy.get("scenes") or []:
        start, end, _length = scene_times(s)
        out.append({"start_sec": start, "end_sec": end,
                    "verdict": s.get("verdict", ""), "kind": s.get("kind", "Scene"), "why": s.get("why_maybe", "")})
    return out


def timeline_stretches(copy: dict) -> list[dict]:
    return [dict(s, start_sec=whole(s["start_sec"]), end_sec=whole(s["end_sec"]))
            for s in copy.get("stretches") or []]


def lead_in_for(copy: dict, lead: float = 90.0) -> float:
    """Seconds of film to show before the credits. The time axis picks 'nice' minute steps for the span it
    shows, and spans of up to 3 minutes or of 12-15 minutes would get half- or 2.5-minute ticks labelled as
    whole minutes ('1:25, 1:25'); showing a little more film before the credits avoids those spans."""
    duration = copy.get("duration_sec") or 0
    starts = [copy.get("credits_start_sec") or 0] + [s["start_sec"] for s in copy.get("stretches") or []
                                                    if s.get("counted", True)]
    first = min(starts)
    span_min = (duration - max(0.0, first - lead)) / 60
    target = 3.1 if span_min <= 3.0 else 15.1 if 12.0 < span_min <= 15.0 else None
    if target is not None:
        lead = min(first, lead + (target - span_min) * 60)
    return lead


def first_scene_note(copy: dict) -> str:
    """'The likely scene starts at 2:05:12, 7 min 51 s after the credits begin.'"""
    scenes = copy.get("scenes") or []
    if not scenes:
        return ""
    likely = [s for s in scenes if s.get("verdict") in ("Likely", YES)]
    first = (likely or scenes)[0]
    noun = "likely scene" if likely else "footage"
    start = whole(first["start_sec"])
    after = max(start - whole(copy.get("credits_start_sec") or 0), 0)
    text = (f"The {'first ' if len(likely or scenes) > 1 else ''}{noun} starts at {charts.clock(start)}, "
            f"{span(after * 1000)} after the credits begin")
    if len(scenes) > 1:
        text += f" ({len(scenes)} stretches of footage in all)"
    return text + "."


def draw_timeline(p: Painter, copy: dict, title: str | None, subtitle: str | None):
    """charts.timeline for one copy, inset a little so the first and last time labels aren't cut off."""
    top = charts.header(p, p.u(INSET[0]), 0, p.width - p.u(INSET[0] + INSET[1]), title, subtitle)
    scenes = timeline_scenes(copy)
    for s in scenes:                     # (why a scene is only a maybe goes in its tooltip: wrap it to fit)
        if s["why"]:
            s["why"] = "\n".join(p.wrap(s["why"], p.font(9), tip_room(p), 8))
    charts.timeline(p, whole(copy["duration_sec"]), timeline_stretches(copy), scenes,
                    whole(copy.get("credits_start_sec") or 0), lead_in=lead_in_for(copy),
                    box=(p.u(INSET[0]), top, p.width - p.u(INSET[0] + INSET[1]), p.height - top))


def plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n:,} {one if n == 1 else (many or one + 's')}"


def not_found(title: str) -> str:
    """How every tab says a film isn't there: 'No film called “Zzqx” in your collection'."""
    return f"No film called “{title}” in your collection"


def wrapped_message(p: Painter, text: str, sub: str | None = None):
    """charts.message for a narrow panel: a heading too long for one line goes on to a second (and a third)
    rather than being cut short - 'No film called “...” in your collection' in the details panel."""
    f, fs = p.font(10, "bold"), p.font(9)
    heads = p.wrap(text, f, p.width * 0.9, 3) or [""]
    lines = p.wrap(sub, fs, p.width * 0.8, 3) if sub else []
    total = len(heads) * p.line_height(f) + len(lines) * p.line_height(fs) + (p.u(4) if lines else 0)
    top = (p.height - total) / 2
    for i, line in enumerate(heads):
        p.text(p.width / 2, top + (i + 0.5) * p.line_height(f), line, f, T.INK_2, "center")
    top += len(heads) * p.line_height(f) + p.u(4)
    for i, line in enumerate(lines):
        p.text(p.width / 2, top + (i + 0.5) * p.line_height(fs), line, fs, T.MUTED, "center")


def tip_room(p: Painter) -> float:
    """How wide a tooltip's lines can be: a tooltip is drawn on its chart, so a wider line would be cut off (in a
    small window the charts here are about 340 px wide)."""
    return max(p.width - p.u(28), p.u(140))


# ---------------------------------------------------------------------------------------------------------
# Charts that are exactly as tall as they need to be
# ---------------------------------------------------------------------------------------------------------
class _Measure(Painter):
    """Draws nothing; remembers how far down a chart reaches (using the real painter's text metrics)."""

    def __init__(self, real: Painter):
        super().__init__(real.width, 100_000, real.s)
        self.real = real
        self.bottom = 0.0

    def text_width(self, text, font):
        return self.real.text_width(text, font)

    def line_height(self, font):
        return self.real.line_height(font)

    def _reach(self, *ys):
        self.bottom = max(self.bottom, *ys)

    def rect(self, x0, y0, x1, y1, fill, outline=None, width=1, tag=None):
        self._reach(y0, y1)

    def round_rect(self, x0, y0, x1, y1, r, fill, corners=(True, True, True, True), outline=None, tag=None):
        self._reach(y0, y1)

    def line(self, points, fill, width=1, tag=None, arrow=False):
        self._reach(*(y for _, y in points))

    def circle(self, cx, cy, r, fill, ring=None, ring_width=2, tag=None):
        self._reach(cy + r + (self.u(ring_width) if ring else 0))

    def polygon(self, points, fill, tag=None):
        self._reach(*(y for _, y in points))

    def text(self, x, y, text, font, fill=None, anchor="w", tag=None):
        lh = self.line_height(font)
        self._reach(y + (lh if anchor in ("n", "nw", "ne") else 0 if anchor in ("s", "sw", "se") else lh / 2))


WIDTH_CHECK_MS = 60          # auto_height: how soon after drawing a chart checks it still has the width it drew at


def needed_height(painter: Painter, draw) -> int:
    """Pixels `draw` needs at the painter's width (for charts whose layout doesn't depend on the height)."""
    m = _Measure(painter)
    draw(m)
    return int(math.ceil(m.bottom + painter.u(4)))


def auto_height(view: ChartView, draw):
    """A draw function for `view` that also makes the view exactly as tall as the chart needs - at the width it
    ends up with: a view drawn while its page was still being laid out (narrower, its legend on more lines) is
    drawn and fitted again a moment later if it's been given another width since, whether or not a <Configure>
    reached it (one that isn't on screen yet doesn't get one) - so the first look and a later redraw agree."""
    guard(view)

    def fit(need):
        view._fit_job = None
        if view.winfo_exists():
            view.configure(height=need)

    def check(width):
        view._width_check = None
        try:
            if view.winfo_exists() and view.winfo_width() >= 20 and view.winfo_width() != width:
                view.redraw()
        except tk.TclError:
            pass

    def paint(p):
        if isinstance(p, TkPainter) and getattr(p, "canvas", None) is view:
            need = needed_height(p, draw)
            try:
                if abs(need - p.height) > 1:
                    if getattr(view, "_fit_job", None) is not None:
                        view.after_cancel(view._fit_job)
                    view._fit_job = view.after_idle(fit, need)
                if getattr(view, "_width_check", None) is not None:
                    view.after_cancel(view._width_check)
                view._width_check = view.after(WIDTH_CHECK_MS, check, p.width)
            except tk.TclError:
                pass
        draw(p)
    return paint


def guard(view: ChartView) -> ChartView:
    """Cancel a chart's pending redraw and resize when it's destroyed - otherwise Tk reports 'invalid command
    name' for the callbacks left behind (the details panel destroys its charts every time the film changes)."""
    if getattr(view, "_guarded", False):
        return view
    view._guarded = True

    def cancel(event):
        if event.widget is not view:
            return
        for name in ("_pending", "_fit_job", "_width_check"):
            job = getattr(view, name, None)
            if job is not None:
                try:
                    view.after_cancel(job)
                except (tk.TclError, ValueError):
                    pass
                setattr(view, name, None)
    view.bind("<Destroy>", cancel, add="+")
    return view


# ---------------------------------------------------------------------------------------------------------
# The list of films
# ---------------------------------------------------------------------------------------------------------
class FilmTable(Table):
    """Table with meaningful sorting (verdicts by strength, times by time, titles without 'The') and columns
    that step aside, least useful first, when the list is narrow."""

    def __init__(self, parent, **kw):
        super().__init__(parent, COLUMNS, **kw)
        self.s = T.scale(parent)
        # (a column made wider for its heading - a font wider than Windows' Segoe UI - needs that much room)
        self.min_widths = {key: max(MIN_WIDTHS[key], width) if key in self.widened else MIN_WIDTHS[key]
                           for key, _heading, width, _anchor in self.columns}
        for key, *_ in COLUMNS:
            self.tree.column(key, minwidth=int(self.min_widths[key] * self.s))
        self.shown_columns = [c[0] for c in COLUMNS]
        self.tree.bind("<Configure>", lambda e: self.fit_columns(e.width), add="+")

    def fit_columns(self, width: int):
        cols = [c[0] for c in COLUMNS]
        for key in DROP_ORDER:
            if sum(self.min_widths[c] for c in cols) * self.s <= width:
                break
            cols.remove(key)
        if cols != self.shown_columns:
            self.shown_columns = cols
            self.tree.configure(displaycolumns=cols)

    def set_rows(self, rows, keep_sort=True):
        if keep_sort and self.sorted_by:
            key, reverse = self.sorted_by
            rows = ordered(rows, key, reverse)
        super().set_rows(rows, keep_sort=False)
        self._arrows()

    def sort(self, key, reverse=None):
        if reverse is None:
            if self.sorted_by and self.sorted_by[0] == key:
                reverse = not self.sorted_by[1]
            else:
                reverse = key in DESCENDING_FIRST
        items = ordered(self.tree.get_children(), key, reverse, row_of=lambda iid: self.rows[iid])
        for n, iid in enumerate(items):
            self.tree.move(iid, "", n)
            self.tree.item(iid, tags=("odd",) if n % 2 else ())
        self.sorted_by = (key, reverse)
        self._arrows()
        sel = self.tree.selection()
        if sel:
            self.tree.see(sel[0])

    def _arrows(self):
        for k, heading, _w, _a in self.columns:
            arrow = ""
            if self.sorted_by and self.sorted_by[0] == k:
                arrow = " ▼" if self.sorted_by[1] else " ▲"
            self.tree.heading(k, text=heading + arrow)

    def select_id(self, film_id) -> bool:
        for iid, row in self.rows.items():
            if row["id"] == film_id:
                self.tree.selection_set(iid)
                self.tree.focus(iid)
                self.tree.see(iid)
                return True
        return False


# ---------------------------------------------------------------------------------------------------------
def _styles(st):
    """This tab's own styles, in the look in use (theme.add_styles runs this again when the look changes)."""
    st.configure("CreditsPlain.TFrame", background=T.CARD, borderwidth=0, relief="flat")
    st.configure("CreditsCard.TCheckbutton", background=T.CARD)
    st.configure("CreditsFilm.TLabel", background=T.CARD, foreground=T.INK, font=T.font(st, 13, "bold"))
    st.configure("CreditsVerdict.TLabel", background=T.CARD, foreground=T.INK, font=T.font(st, 10, "bold"))
    st.configure("CreditsStrong.TLabel", background=T.CARD, foreground=T.INK, font=T.font(st, 9, "bold"))
    st.configure("CreditsText.TLabel", background=T.CARD, foreground=T.INK)
    st.configure("CreditsNote.TLabel", background=T.CARD, foreground=T.INK_2)
    st.configure("CreditsLink.TLabel", background=T.CARD, foreground=T.LINK)


def _swatch(parent, color, s: float, size: int = 10) -> tk.Frame:
    """A small colour square. color: a token's name ("GOOD") or a function giving the colour - so it follows
    the look."""
    frame = tk.Frame(parent, width=int(size * s), height=int(size * s), highlightthickness=0, borderwidth=0)
    return T.tint(frame, background=color)


class Tab(BaseTab):
    title = "Credits"

    def __init__(self, app, notebook):
        super().__init__(app, notebook)
        self.s = T.scale(self.frame)
        T.add_styles(self.frame, _styles)
        self.rows: list[dict] = []           # every film, built once per catalog
        self.visible: list[dict] = []        # the ones passing the filters
        self.built_for = None                # the catalog self.rows came from
        self.decade: int | None = None
        self.shown_id = None                 # the film in the details panel
        self._suggest = suggester([])
        self._search_job = None
        self._pending = None                 # navigate() before the collection was ready: (title, film_key)
        self._status_text = None             # what this tab last put in the status line (to take it back)
        self._tips_scope = None              # the filters the verdict chart's tooltips were counted for
        self._content_top = 0                # space above the cards (more when a short window drops the title)
        self._wraps: list[tuple] = []
        self._build()
        self.frame.bind("<Destroy>", self._destroyed, add="+")
        self.catalog_changed(getattr(app, "catalog", None), getattr(app, "catalog_state", "none"))

    def _destroyed(self, event):
        if event.widget is not self.frame:
            return
        if self._search_job is not None:
            try:
                self.frame.after_cancel(self._search_job)
            except (tk.TclError, ValueError):
                pass
            self._search_job = None
        # The search box's trace calls this tab: a Tcl command that would keep the tab alive for good once it has
        # gone (a new text size builds the tab again and lets this one go), so it's taken off
        trace = getattr(self, "_search_trace", None)
        if trace is not None:
            try:
                self.search.var.trace_remove("write", trace)
            except (tk.TclError, ValueError):
                pass
            self._search_trace = None

    # -- layout -----------------------------------------------------------------------------------------------
    def _build(self):
        f = self.frame
        f.columnconfigure(0, weight=1)
        f.rowconfigure(1, weight=1)
        # The page title, as on the other tabs (a short window leaves it out, so the list keeps its room)
        self.head = head = ttk.Frame(f, style="Page.TFrame", padding=(PAD, 12, PAD, 6))
        head.grid(row=0, column=0, sticky="ew")
        head.columnconfigure(1, weight=1)
        title = ttk.Label(head, text="Stay after the credits?", style="PageTitle.TLabel")
        title.grid(row=0, column=0, sticky="w")
        hint = ttk.Label(head, text=PAGE_HINT, style="PageHint.TLabel", justify="left")
        hint.grid(row=0, column=1, sticky="w", padx=(14, 0))
        head.bind("<Configure>", lambda e: hint.configure(
            wraplength=max(e.width - title.winfo_reqwidth() - int((2 * PAD + 20) * self.s), 200)), add="+")
        f.bind("<Configure>", self._fit_height, add="+")

        # What shows instead of the tab while there's no collection (or its credits can't be read)
        self.placeholder = guard(ChartView(f, height=320, background=T.PAGE))
        self.placeholder.grid(row=1, column=0, sticky="nsew")
        self.content = c = ttk.Frame(f, style="Page.TFrame")
        c.grid(row=1, column=0, sticky="nsew", padx=PAD, pady=(0, PAD - 4))
        # The list and the details split the width 3:2 whatever the list's columns ask for (uniform), with room
        # for a readable timeline in the details even at the narrowest window.
        c.columnconfigure(0, weight=3, uniform="credits-split")
        c.columnconfigure(1, weight=2, uniform="credits-split", minsize=int(380 * self.s))
        c.rowconfigure(1, weight=1)

        # The collection at a glance, and the filters
        top = Card(c)
        top.grid(row=0, column=0, columnspan=2, sticky="ew")
        b = top.body
        b.columnconfigure(0, weight=3, uniform="credits-top")
        b.columnconfigure(1, weight=2, uniform="credits-top", minsize=int(250 * self.s))
        # (width=1: the charts take the width the card gives them rather than asking for Tk's default 10 cm)
        self.verdict_view = guard(ChartView(b, height=90, width=1))
        self.verdict_view.grid(row=0, column=0, sticky="new", padx=(0, 20))
        self.hint = ttk.Label(b, text=HINT, style="CardHint.TLabel", justify="left", wraplength=int(500 * self.s))
        self.hint.grid(row=1, column=0, sticky="nw", padx=(0, 20), pady=(4, 0))
        self.verdict_view.bind("<Configure>", lambda e: self.hint.configure(wraplength=max(e.width, 200)), add="+")
        self.decade_view = guard(ChartView(b, height=140, width=1))
        self.decade_view.grid(row=0, column=1, rowspan=2, sticky="nsew")

        filters = ttk.Frame(b, style="CreditsPlain.TFrame")
        filters.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        ttk.Label(filters, text="Show", style="CreditsStrong.TLabel").grid(row=0, column=0, padx=(0, 10))
        self.verdict_vars: dict[str, tk.BooleanVar] = {}
        self.verdict_checks: dict[str, ttk.Checkbutton] = {}
        for i, v in enumerate(VERDICTS):
            var = tk.BooleanVar(value=v in DEFAULT_VERDICTS)
            cb = ttk.Checkbutton(filters, text=f"{ICONS[v]} {v}", variable=var, style="CreditsCard.TCheckbutton",
                                 command=self._filters_changed)
            cb.grid(row=0, column=1 + i, padx=(0, 12))
            self.verdict_vars[v], self.verdict_checks[v] = var, cb
        filters.columnconfigure(5, weight=1)
        ttk.Label(filters, text="Library", style="CreditsStrong.TLabel").grid(row=0, column=6, padx=(12, 8))
        self.library_var = tk.StringVar(value=ANY)
        self.library_box = ttk.Combobox(filters, textvariable=self.library_var, state="readonly", width=18,
                                        values=[ANY])
        self.library_box.grid(row=0, column=7)
        self.library_box.bind("<<ComboboxSelected>>", lambda e: self._library_changed(), add="+")
        self.decade_chip = ttk.Button(filters, style="Small.TButton", command=self.clear_decade)
        self.decade_chip.grid(row=0, column=8, padx=(10, 0))
        self.decade_chip.grid_remove()

        # The list
        left = Card(c, padding=10)
        left.grid(row=1, column=0, sticky="nsew", pady=(12, 0), padx=(0, 12))
        lb = left.body
        lb.columnconfigure(1, weight=1)
        lb.rowconfigure(1, weight=1)
        ttk.Label(lb, text="Find a film", style="CreditsStrong.TLabel").grid(row=0, column=0, sticky="w",
                                                                            padx=(0, 8))
        self.search = SearchBox(lb, lambda text: self._suggest(text), on_pick=self._picked, width=28)
        self.search.grid(row=0, column=1, sticky="ew")
        # (taken off again when the tab goes: see _destroyed)
        self._search_trace = self.search.var.trace_add("write", lambda *_: self._search_typed())
        self.clear_btn = ttk.Button(lb, text="✕", width=3, style="Small.TButton", command=self.clear_search)
        self.clear_btn.grid(row=0, column=2, padx=(4, 0))
        self.count_label = ttk.Label(lb, text="", style="CreditsNote.TLabel")
        self.count_label.grid(row=0, column=3, sticky="e", padx=(12, 2))
        # (a click shows the film's timeline beside the list; Enter or a double-click opens its page on the Film tab)
        self.table = FilmTable(lb, height=12, on_select=self._row_selected, on_open=self.open_film_page)
        self.table.grid(row=1, column=0, columnspan=4, sticky="nsew", pady=(8, 0))
        self.table.sorted_by = ("title", False)
        self.table._arrows()
        # Shown over the list when nothing passes the filters
        self.empty = ttk.Frame(self.table, style="CreditsPlain.TFrame", padding=16)
        self.empty_label = ttk.Label(self.empty, text="", style="CreditsNote.TLabel", justify="center",
                                     wraplength=int(340 * self.s))
        self.empty_label.grid(row=0, column=0)
        self.empty_btn = ttk.Button(self.empty, text="Show everything", command=self.show_everything)
        self.empty_btn.grid(row=1, column=0, pady=(10, 0))

        # The selected film
        self.details = ScrollFrame(c)
        self.details.grid(row=1, column=1, sticky="nsew", pady=(12, 0))
        self.details.canvas.configure(width=1)        # the panel's width comes from the split, not from Tk's default
        self.details.inner.columnconfigure(0, weight=1)
        self.details.canvas.bind("<Configure>", lambda e: self._rewrap(e.width), add="+")

    def _fit_height(self, event=None):
        """In a short window the page title goes, so the list keeps its room."""
        height = event.height if event is not None else self.frame.winfo_height()
        if height < 50:
            return
        short = height < SHORT_WINDOW * self.s
        if short:
            self.head.grid_remove()
        else:
            self.head.grid()
        self._content_top = 12 if short else 0      # without the title, keep the cards off the tab strip
        if self.content.winfo_manager():            # (configuring a hidden grid widget would show it again)
            self.content.grid_configure(pady=(self._content_top, PAD - 4))

    # -- called by the main window ------------------------------------------------------------------------------
    def catalog_changed(self, catalog, state: str):
        super().catalog_changed(catalog, state)
        self.rows, self.visible, self.built_for = [], [], None
        self.shown_id = None
        msg = self.state_message()
        if msg is not None:
            if state != "loading" and self._pending is not None:
                self._pending = None              # no collection to show it from after all
                self._unsay()
            self._placeholder(*msg)
            return
        if self._visible() or self._pending:
            self.refresh()
        else:
            self._placeholder("Getting the credits ready...")

    def shown(self):
        if self.state == "ready" and self.catalog is not None and self.built_for is not self.catalog:
            self.refresh()

    def keep(self) -> dict:
        """(Laid out again for a new text size:) the film in the details, the verdicts ticked under Show, the
        library and the decade picked."""
        row = next((r for r in self.rows if r["id"] == self.shown_id), None) if self.shown_id else None
        return {"film": (row["label"], row["key"]) if row else None, "library": self.library_var.get(),
                "decade": self.decade, "verdicts": {v: bool(var.get()) for v, var in self.verdict_vars.items()}}

    def restore(self, kept: dict):
        for verdict, ticked in (kept.get("verdicts") or {}).items():
            if verdict in self.verdict_vars:
                self.verdict_vars[verdict].set(bool(ticked))
        if kept.get("library"):
            self.library_var.set(kept["library"])            # (kept if this collection has it)
        self.decade = kept.get("decade")
        if kept.get("film"):
            self._pending = tuple(kept["film"])             # (shown once the list is built)
        if self.rows:                                       # (the list was built already - the tab in front)
            self._decade_chip()
            self._draw_charts()
            self.apply_filters()

    def navigate(self, **kwargs):
        """Show one film: film_key (the catalog's Film.key) when the caller knows it - so 'Dracula (1931)' can't
        open 'Drácula (1931)' - else title ('Title (Year)', or a loose title)."""
        title, key = kwargs.get("title"), kwargs.get("film_key")
        if not title and not key:
            return
        title = str(title) if title else ""
        if self.state != "ready" or self.catalog is None:
            self._pending = (title, key)
            self._say("The collection is still loading - the film will be shown when it's ready.")
            return
        self.shown()
        if self.rows:
            self.reveal(title, key, from_elsewhere=True)

    # -- building ----------------------------------------------------------------------------------------------
    def _visible(self) -> bool:
        current = getattr(self.app, "current_tab", None)
        try:
            return current is not None and current() is self
        except Exception:
            return False

    def refresh(self):
        """Read every film's credits verdict (one quick request) and fill the tab."""
        catalog = self.catalog
        answer = self.app.ask({"action": "credits", "verdict": ASKED_VERDICTS, "count": 5000})
        if not answer.get("ok"):
            self._placeholder("Couldn't read the credits markers", answer.get("error", ""))
            if self._pending is not None:
                self._pending = None
                self._unsay()
            return
        self.rows = build_rows(answer, catalog)
        self.built_for = catalog
        self._show_content()
        if answer.get("matching_films", 0) > len(answer.get("films") or []):
            self._set_status(f"The Credits tab lists the first {len(answer['films']):,} of "
                             f"{answer['matching_films']:,} scanned films.")
        self._suggest = suggester([r["label"] for r in self.rows])
        order = {lib: i for i, lib in enumerate(getattr(catalog, "libraries", None) or [])}
        libraries = sorted({lib for r in self.rows for lib in r["libraries"] if lib},
                           key=lambda lib: (order.get(lib, len(order)), fold(lib)))
        self.library_box.configure(values=[ANY] + libraries)
        if self.library_var.get() not in libraries:
            self.library_var.set(ANY)
        if self.decade is not None and not any(r["decade"] == self.decade for r in self.rows):
            self.decade = None
        self._decade_chip()
        self._set_controls("normal")
        self._draw_charts()
        self.apply_filters()
        if self._pending is not None:
            (title, key), self._pending = self._pending, None
            self.reveal(title, key, from_elsewhere=True)

    def _placeholder(self, text: str, sub: str = ""):
        """One calm message in place of the whole tab (no collection yet, or its credits can't be read)."""
        self.verdict_view.clear()
        self.decade_view.clear()
        self.table.set_rows([])
        self.empty.place_forget()
        self.count_label.configure(text="")
        self._set_controls("disabled")
        self._clear_details()
        self._tips_scope = None
        self.content.grid_remove()
        self.placeholder.grid()
        self.show_message(self.placeholder, text, sub or None)

    def _show_content(self):
        self.placeholder.grid_remove()
        self.placeholder.clear()
        self.content.grid()
        self.content.grid_configure(pady=(self._content_top, PAD - 4))

    def _set_controls(self, state: str):
        for cb in self.verdict_checks.values():
            cb.configure(state=state)
        self.library_box.configure(state="readonly" if state == "normal" else "disabled")
        self.search.entry.configure(state=state)
        self.clear_btn.configure(state=state)

    def _set_status(self, text: str):
        setter = getattr(self.app, "set_status", None)
        if setter:
            setter(text)

    def _say(self, text: str):
        """A message in the status line that this tab takes back once it's out of date (_unsay)."""
        self._status_text = text
        self._set_status(text)

    def _unsay(self):
        """Clear this tab's last status message - unless something else has replaced it since."""
        mine, self._status_text = self._status_text, None
        if not mine:
            return
        var = getattr(self.app, "app_status_var", None)
        try:
            if var is not None and var.get() != mine:
                return
        except tk.TclError:
            return
        self._set_status("")

    # -- the charts ------------------------------------------------------------------------------------------
    def _library_rows(self) -> list[dict]:
        lib = self.library_var.get()
        return self.rows if lib == ANY else [r for r in self.rows if lib in r["libraries"]]

    def _draw_charts(self):
        rows = self._library_rows()
        self._draw_verdicts(rows)
        self._draw_decades(rows)

    def _scope(self) -> tuple:
        """The filters, other than the verdicts, that a click on the verdicts chart keeps."""
        return self.library_var.get(), self.decade, self.query()

    def _scope_words(self) -> str:
        """What the decade and the search narrow the list to: 'from the 2010s with “man” in the title'."""
        words = []
        if self.decade is not None:
            words.append(f"from the {self.decade}s")
        if self.query():
            words.append(f"with “{self.search.get()}” in the title")
        return " ".join(words)

    def _draw_verdicts(self, rows=None):
        """The stacked bar of verdicts across the library (or collection)."""
        rows = self._library_rows() if rows is None else rows
        lib = self.library_var.get()
        total = len(rows)
        subtitle = (f"All {plural(total, 'film')} in your collection" if lib == ANY
                    else f"The {plural(total, 'film')} in {lib}")
        self._tips_scope = self._scope()
        segments = self.verdict_segments(rows)
        # (paint wraps each tooltip to fit the chart: its heading too, and nothing is cut off)
        draw = lambda p: charts.stacked(p, segments, title="Every film's verdict", subtitle=subtitle,
                                        on_click=self.pick_verdict)
        self.verdict_view.show(auto_height(self.verdict_view, draw))

    def verdict_segments(self, rows=None) -> list[dict]:
        """The verdicts chart's colours. Each one's tooltip says how many films a click on it will list: the
        decade and the search stay as they are, so that can be fewer than the colour counts."""
        rows = self._library_rows() if rows is None else rows
        counts = verdict_counts(rows)
        total = len(rows)
        library, query = self.library_var.get(), self.query()
        narrowed = self.decade is not None or bool(query)
        listed = (verdict_counts([r for r in rows if self._passes(r, VERDICTS, library, self.decade, query)])
                  if narrowed else counts)
        where = self._scope_words()
        segments = []
        for v in VERDICTS:
            n, k = counts[v], listed[v]
            share = n / total if total else 0
            if not narrowed or k == n:
                click = "Click to list them"
            elif k:
                click = f"Click to list the {'one' if k == 1 else f'{k:,}'} {where}"
            else:
                click = f"Click to list them - there are none {where}"
            segments.append({"label": v, "value": n, "color": lambda v=v: COLORS[v], "key": v, "icon": ICONS[v],
                             "tip": f"{ICONS[v]} {v}: {plural(n, 'film')} ({share:.0%})\n{GROUP_MEANINGS[v]}\n"
                                    + click})
        return segments

    def _draw_decades(self, rows=None):
        data = decade_shares(self._library_rows() if rows is None else rows)
        if not data:
            self.decade_view.clear("No scanned films", "Plex hasn't marked credits on any of these films.")
            return
        items = []
        for d in data:
            tip = f"The {d['decade']}s\n"
            if d["scanned"]:
                tip += (f"{d[YES]:,} of {plural(d['scanned'], 'scanned film')} ({d['share']:.0%}) "
                        f"{'has' if d[YES] == 1 else 'have'} a likely scene"
                        + (f"\n{d[MAYBE]:,} more {'is a maybe' if d[MAYBE] == 1 else 'are maybes'}"
                           if d[MAYBE] else ""))
            else:
                tip += "No scanned films from this decade"
            if d["films"] - d["scanned"]:
                tip += f"\n{d['films'] - d['scanned']:,} not scanned"
            if d["decade"] < MODERN_FROM_YEAR:
                tip += "\n" + OLD_RULE
            items.append({"label": f"{d['decade']}s", "value": d["share"] * 100, "key": d["decade"],
                          "tip": tip + "\nClick to list this decade"})
        # The rule, not the data, keeps the older decades low: say so where the chart shows it.
        old = data[0]["decade"] < MODERN_FROM_YEAR
        subtitle = ("% of each decade's scanned films"
                    + (f". Films before {MODERN_FROM_YEAR} are a Maybe at most." if old else "."))
        emphasis = {self.decade} if self.decade is not None else None
        self.decade_view.show(lambda p: charts.columns(
            p, items,                                          # (paint wraps the tooltips to fit the chart)
            title="Films with a likely scene, by decade", subtitle=subtitle, value_fmt=lambda v: f"{v:.0f}%",
            tick_fmt=lambda v: f"{v:.0f}%", on_click=self.pick_decade, emphasis=emphasis))

    # -- filters ------------------------------------------------------------------------------------------------
    def verdicts(self) -> set[str]:
        return {v for v, var in self.verdict_vars.items() if var.get()}

    def query(self) -> str:
        return fold(self.search.get())

    def _passes(self, row, verdicts=None, library=None, decade=-1, query=None) -> bool:
        verdicts = self.verdicts() if verdicts is None else verdicts
        library = self.library_var.get() if library is None else library
        decade = self.decade if decade == -1 else decade
        query = self.query() if query is None else query
        return (row["verdict"] in verdicts and (library == ANY or library in row["libraries"])
                and (decade is None or row["decade"] == decade) and (not query or query in row["fold"]))

    def apply_filters(self, select=None):
        """Show the films passing the filters; keep the selected film if it's still there, else pick the first."""
        if not self.rows:
            return
        verdicts, library, query = self.verdicts(), self.library_var.get(), self.query()
        if self._scope() != self._tips_scope:
            self._draw_verdicts()            # its tooltips say how many films a click lists
        self.visible = [r for r in self.rows if self._passes(r, verdicts, library, self.decade, query)]
        self.table.set_rows(self.visible)
        n = len(self.visible)
        self.count_label.configure(text=plural(n, "film") if n else "No films")
        self._empty_state()
        if not self.visible:
            self._clear_details()
            self.shown_id = None
            self._details_message("No film to show", "Nothing in the list passes the filters.")
            return
        target = select if select is not None else self.shown_id
        if target is None or not self.table.select_id(target):
            self.table.select_first()
        row = self.table.selected()
        if row is None or row["id"] != self.shown_id or select is not None:
            self.show_film(row)

    def _empty_state(self):
        if self.visible:
            self.empty.place_forget()
            return
        q = self.search.get()
        button = True
        if not self.verdicts():
            text, button = "Tick at least one verdict above to list films.", False
        elif q:
            elsewhere = sum(1 for r in self.rows if self.query() in r["fold"])
            if elsewhere:
                text = (f"Nothing here matches “{q}”, but {plural(elsewhere, 'film')} with other verdicts or in "
                        f"other libraries or decades {'does' if elsewhere == 1 else 'do'}.")
            else:
                text = f"{not_found(q)}. Try part of the title, or check the spelling."
                button = False
        else:
            text = "No films with these verdicts here. Tick more verdicts, or try another library or decade."
        self.empty_label.configure(text=text)
        if button:
            self.empty_btn.grid()
        else:
            self.empty_btn.grid_remove()
        self.empty.place(in_=self.table, relx=0.5, rely=0.42, anchor="center")

    def _filters_changed(self):
        self.apply_filters()

    def _library_changed(self):
        self._draw_charts()
        self.apply_filters()

    def pick_verdict(self, verdict: str):
        """A click on the verdicts chart: list just that verdict."""
        for v, var in self.verdict_vars.items():
            var.set(v == verdict)
        self.apply_filters()

    def pick_decade(self, decade: int):
        """A click on the decades chart: list that decade (a second click lists every decade again)."""
        self.decade = None if self.decade == decade else decade
        self._decade_chip()
        self._draw_decades()
        self.apply_filters()

    def clear_decade(self):
        if self.decade is not None:
            self.pick_decade(self.decade)

    def _decade_chip(self):
        if self.decade is None:
            self.decade_chip.grid_remove()
        else:
            self.decade_chip.configure(text=f"{self.decade}s  ✕")
            self.decade_chip.grid()

    def clear_search(self):
        self.search.set("")
        self._run_search()

    def show_everything(self):
        """The empty list's button: every verdict, every library and decade (the search text stays)."""
        for var in self.verdict_vars.values():
            var.set(True)
        self.library_var.set(ANY)
        self.decade = None
        self._decade_chip()
        self._draw_charts()
        self._run_search()

    def _search_typed(self):
        if self._search_job is not None:
            try:
                self.frame.after_cancel(self._search_job)
            except tk.TclError:
                pass
        self._search_job = self.frame.after(120, self._run_search)

    def _run_search(self):
        if self._search_job is not None:
            try:
                self.frame.after_cancel(self._search_job)
            except tk.TclError:
                pass
        self._search_job = None
        self.apply_filters()

    def _picked(self, text: str):
        """A suggestion was chosen, or Enter pressed: go to that film."""
        exact = next((r for r in self.rows if r["label"] == text), None)
        if exact is not None:
            self.reveal_row(exact)
            return
        self._run_search()
        if self.visible:
            self.table.select_first()
            self.show_film(self.table.selected())
        else:
            self.reveal(text)

    # -- going to one film ---------------------------------------------------------------------------------------
    def find(self, title: str, film_key: str | None = None) -> dict | None:
        """The row for a film: by the catalog's film key when there is one, else 'Title (Year)' exactly, else
        the same title with accents or punctuation aside ('Leon (1994)' for 'Léon (1994)'), else the
        collection's loose match."""
        if film_key:
            row = next((r for r in self.rows if r["key"] == film_key), None)
            if row is not None:
                return row
        if not title:
            return None
        exact = next((r for r in self.rows if r["label"] == title), None)
        if exact is not None:
            return exact
        folded = fold(title)
        alike = [r for r in self.rows if r["fold"] == folded]
        if len(alike) == 1:
            return alike[0]
        # Several films fold alike ('Dracula (1931)' and 'Drácula (1931)'), or none do: the collection's own
        # title search knows exact from loose matches.
        answer = self.app.ask({"action": "credits", "title": title})
        film = answer.get("film") if answer.get("ok") else None
        if film:
            ids = tuple(film.get("plex_ids") or ())
            label = film_label(film.get("title", ""), film.get("year"))
            row = next((r for r in self.rows if r["id"] == ids), None) or \
                next((r for r in self.rows if r["label"] == label), None)
            if row is not None and (not alike or row in alike):
                return row
        return alike[0] if alike else None

    def reveal(self, title: str, film_key: str | None = None, from_elsewhere: bool = False) -> bool:
        """Go to a film. from_elsewhere: another tab sent the user here, so a film that can't be found says so
        in the details panel too, rather than leaving the last film there as if it were the one asked for."""
        if not self.rows:
            return False
        row = self.find(title, film_key)
        if row is None:
            # (said as every tab says it: 'No film called “X” in your collection.')
            heading = not_found(title) if title else "That film isn't in your collection"
            self._say(heading + ".")
            if from_elsewhere:
                self.table.tree.selection_remove(*self.table.tree.selection())
                self._clear_details()
                self.shown_id = None
                self._details_message(heading, "Pick one from the list, or search for it.")
            return False
        self.reveal_row(row)
        return True

    def reveal_row(self, row: dict):
        """Select a film, widening the filters just enough for it to be in the list."""
        if not self.verdict_vars[row["verdict"]].get():
            self.verdict_vars[row["verdict"]].set(True)
        if self.library_var.get() != ANY and self.library_var.get() not in row["libraries"]:
            self.library_var.set(ANY)
            self._draw_charts()
        if self.decade is not None and row["decade"] != self.decade:
            self.decade = None
            self._decade_chip()
            self._draw_decades()
        if self.query() and self.query() not in row["fold"]:
            self.search.set("")
        if self._search_job is not None:
            self.frame.after_cancel(self._search_job)
            self._search_job = None
        self.apply_filters(select=row["id"])

    # -- the details panel --------------------------------------------------------------------------------------
    def _row_selected(self, row):
        if row is not None and row["id"] != self.shown_id:
            self.show_film(row)

    def _clear_details(self):
        for w in self.details.inner.winfo_children():
            w.destroy()
        self._wraps = []

    def _wrap(self, label, less: int = 0):
        """Keep a label wrapped to the details panel's width (less `less` device-independent pixels)."""
        self._wraps.append((label, less))
        width = self.details.canvas.winfo_width()
        if width > 50:
            label.configure(wraplength=max(width - int(less * self.s), 120))
        return label

    def _rewrap(self, width: int):
        for label, less in self._wraps:
            try:
                label.configure(wraplength=max(width - int(less * self.s), 120))
            except tk.TclError:
                pass

    def _details_message(self, text: str, sub: str = ""):
        card = Card(self.details.inner)
        card.grid(row=0, column=0, sticky="ew")
        card.body.columnconfigure(0, weight=1)
        view = guard(ChartView(card.body, height=150, width=1, background=T.SURFACE))
        view.grid(row=0, column=0, sticky="ew")
        view.show(lambda p: wrapped_message(p, text, sub or None))     # (a long heading wraps: a narrow panel)

    def show_film(self, row: dict | None):
        self._clear_details()
        self.shown_id = row["id"] if row else None
        if row is None:
            if self.rows:
                self._details_message("No film selected", "Pick one from the list, or search for it by title.")
            return
        self._unsay()                          # a film is showing: an earlier 'no film matching' is out of date
        inner = self.details.inner
        inner.columnconfigure(0, weight=1)
        self._film_header(inner, row)
        copies = copies_of(row)
        # Name the edition whenever the film has more than one copy with names to tell them apart, so it's clear
        # which one each timeline (and the verdict) is for - also when only one of them has been scanned
        named = len(copies) > 1 or (len(row["plex_ids"]) > 1 and (any(c.get("edition") for c in copies)
                                                                  or bool(self._unscanned_editions(row, copies))))
        for n, copy in enumerate(copies):
            self._copy_card(inner, row, copy, n + 1, named)
        self.details.to_top()

    def _film_header(self, parent, row):
        verdict = row["verdict"]
        card = Card(parent, padding=14)
        card.grid(row=0, column=0, sticky="ew")
        body = card.body
        body.columnconfigure(0, weight=1)
        self._wrap(ttk.Label(body, text=row["label"], style="CreditsFilm.TLabel"), 40).grid(
            row=0, column=0, sticky="w")
        line = ttk.Frame(body, style="CreditsPlain.TFrame")
        line.grid(row=1, column=0, sticky="w", pady=(6, 0))
        _swatch(line, lambda v=verdict: COLORS[v], self.s, 12).grid(row=0, column=0, padx=(0, 8))
        ttk.Label(line, text=f"{ICONS[verdict]} {verdict}", style="CreditsVerdict.TLabel").grid(row=0, column=1)
        self._wrap(ttk.Label(body, text=MEANINGS[verdict], style="CreditsText.TLabel", justify="left"), 40).grid(
            row=2, column=0, sticky="w", pady=(4, 0))
        notes = self._film_notes(row)
        if notes:
            self._wrap(ttk.Label(body, text="\n".join(notes), style="CreditsNote.TLabel", justify="left"), 40).grid(
                row=3, column=0, sticky="w", pady=(8, 0))
        # (the film's key too, so Watch Next can't take 'Dracula (1931)' for 'Drácula (1931)')
        link = {"like": row["label"], **({"film_key": row["key"]} if row.get("key") else {})}
        links = ttk.Frame(body, style="CreditsPlain.TFrame")
        links.grid(row=4, column=0, sticky="w", pady=(10, 0))
        LinkLabel(links, text="Everything about this film  →", style="CreditsLink.TLabel",
                  command=lambda: self.open_film_page(row)).grid(row=0, column=0, sticky="w")
        LinkLabel(links, text="Find similar films to watch  →", style="CreditsLink.TLabel",
                  command=lambda: self.app.goto("Watch Next", **link)).grid(row=1, column=0, sticky="w", pady=(4, 0))

    def open_film_page(self, row: dict):
        """A film's page on the Film tab - by its key (another film can share its title and year)."""
        if not row:
            return
        if self.app.goto("Film", **({"film_key": row["key"]} if row.get("key") else {}), title=row["label"]) is None:
            self._say(f"{row['label']} - the Film tab isn't available.")

    def _film_notes(self, row) -> list[str]:
        notes = []
        copies = copies_of(row)
        if copies and first_scene_note(copies[0]):
            notes.append(first_scene_note(copies[0]))
        if len(copies) > 1:
            verdicts = {c.get("stay_after_credits") for c in copies}
            if len(verdicts) > 1:
                best = copies[0].get("edition") or "the one without an edition name"
                notes.append(f"Your {len(copies)} copies differ: the verdict is for the one with the most to stay "
                             f"for ({best}). Each is shown below.")
            else:
                notes.append(f"You have {len(copies)} copies, with the same verdict - each is shown below.")
        unscanned = len(row["plex_ids"]) - len(copies)
        if copies and unscanned > 0:
            names = self._unscanned_editions(row, copies)
            notes.append(f"{plural(unscanned, 'other copy', 'other copies')} of this film "
                         f"{'hasn' if unscanned == 1 else 'haven'}'t been scanned for credits"
                         + (f" ({' and '.join(names)})." if names else "."))
        if row["libraries"]:
            notes.append("In " + " and ".join(row["libraries"]) + ".")
        return notes

    def _unscanned_editions(self, row, copies) -> list[str]:
        """The edition names of the copies Plex hasn't scanned, as far as the catalog knows them."""
        film = getattr(self.catalog, "films", {}).get(row.get("key")) if row.get("key") else None
        scanned = {c.get("edition") or "" for c in copies}
        return [e for e in (getattr(film, "editions", None) or []) if e and e not in scanned]

    def _copy_card(self, parent, row, copy, n, named):
        card = Card(parent, padding=10)
        card.grid(row=n, column=0, sticky="ew", pady=(12, 0))
        body = card.body
        body.columnconfigure(0, weight=1)
        verdict = copy.get("stay_after_credits") or NOT_SCANNED
        title = f"{copy.get('edition') or 'Regular edition'}  ·  {verdict}" if named else "How the film ends"
        subtitle = f"Credits start at {clock_of(copy.get('credits_start_sec')) or '?'}"
        if copy.get("duration_sec"):
            subtitle += f"  ·  the file ends at {clock_of(copy['duration_sec'])}"
        scenes = timeline_scenes(copy)
        # width=1: the timeline takes the panel's width (and redraws as it changes) rather than Tk's default
        # 10 cm, which cut off the end of the film in a narrow window and left half the panel empty in a wide one
        view = guard(ChartView(body, height=charts.timeline_height(1, 1 if scenes else 0), width=1))
        view.grid(row=0, column=0, sticky="ew")
        if copy.get("duration_sec"):
            view.show(auto_height(view, lambda p: draw_timeline(p, copy, title, subtitle)))
        else:
            self.show_message(view, "No timeline for this copy", "Plex didn't record how long the file is.")
        view.film_copy = copy                    # for previews and tests
        lst = ttk.Frame(body, style="CreditsPlain.TFrame")
        lst.grid(row=1, column=0, sticky="ew", pady=(6, 2), padx=(int(INSET[0] * self.s), 0))
        lst.columnconfigure(2, weight=1)
        if not scenes:
            text = ("No footage after the credits start that Plex could see." if verdict == NONE_FOUND
                    else "No scenes.")
            ttk.Label(lst, text=text, style="CreditsNote.TLabel").grid(row=0, column=0, columnspan=3, sticky="w")
        for i, s in enumerate(copy.get("scenes") or []):
            likely = s.get("verdict") in ("Likely", YES)
            start, end, length = scene_times(s)
            r = 2 * i
            _swatch(lst, "GOOD" if likely else "WARNING", self.s).grid(row=r, column=0, padx=(0, 8), pady=(4, 0))
            ttk.Label(lst, text=f"{charts.clock(start)} – {charts.clock(end)}",
                      style="CreditsStrong.TLabel").grid(row=r, column=1, sticky="w", pady=(4, 0))
            what = f"{s.get('kind', 'Scene')}  ·  {length}  ·  " + ("✔ likely" if likely else "? maybe")
            self._wrap(ttk.Label(lst, text=what, style="CreditsText.TLabel", justify="left"), 190).grid(
                row=r, column=2, sticky="w", padx=(12, 0), pady=(4, 0))
            if s.get("why_maybe"):
                why = s["why_maybe"]
                self._wrap(ttk.Label(lst, text=f"Only a maybe: {why[0].upper()}{why[1:]}.",
                                     style="CreditsNote.TLabel", justify="left"), 80).grid(
                    row=r + 1, column=1, columnspan=2, sticky="w", pady=(1, 2))
        self._wrap_later()

    def _wrap_later(self):
        width = self.details.canvas.winfo_width()
        if width > 50:
            self._rewrap(width)

    # -- for previews and tests ----------------------------------------------------------------------------------
    def timeline_views(self) -> list[ChartView]:
        found = []

        def walk(w):
            for c in w.winfo_children():
                if isinstance(c, ChartView) and hasattr(c, "film_copy"):
                    found.append(c)
                walk(c)
        walk(self.details.inner)
        return found
