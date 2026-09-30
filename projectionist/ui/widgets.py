"""Widgets the tabs share: ChartView, Card, ScrollFrame, Table, SearchBox, LinkLabel (and keyboard_link, which
lets Tab reach anything else you click)."""

from __future__ import annotations

import re
import sys
import time
import tkinter as tk
import traceback
import types
import weakref
from tkinter import ttk

from . import charts
from . import theme as T
from .paint import TkPainter, drawing_done, forget_drawing

# Where a chart's drawing error goes: the main window sets this to its log (App._log). Without it (tests, previews)
# the details are printed to stderr, when there is one.
report_error = None


def _report(details: str):
    if report_error is not None:
        try:
            report_error(details)
            return
        except Exception:                     # the window may be closing
            pass
    if sys.stderr is not None:
        print(details, file=sys.stderr)


# ---------------------------------------------------------------------------------------------------------
class ChartView(tk.Canvas):
    """A canvas showing one chart; redraws itself whenever it's resized, and when the look changes.

    view.show(draw, height=None): draw(painter) draws the chart; height is in device-independent pixels.
    background: the surface it sits on - a token's name ("SURFACE", the default; "PAGE", "CARD"), or that token's
    colour (T.PAGE), which it then follows when the look changes.
    A drawing error shows a message instead of taking the app down (the details go to the Export tab's log).
    """

    def __init__(self, parent, draw=None, height: int = 240, background: str | None = None, **kw):
        self.s = T.scale(parent)
        self._height = height                 # device-independent px (rescale() sizes it again)
        token = T.token_for(background) if background is not None else "SURFACE"
        super().__init__(parent, height=int(height * self.s), background=T.value(token) if token else background,
                         highlightthickness=0, borderwidth=0, **kw)
        if token:
            T.tint(self, background=token)
        self._draw = draw
        self._pending = None
        self._reported = None             # the last drawing error sent to the log
        _VIEWS.add(self)
        self.bind("<Configure>", self._resized, add="+")
        self.bind("<Destroy>", self._destroyed, add="+")

    def _destroyed(self, event):
        if event.widget is self and self._pending is not None:
            try:
                self.after_cancel(self._pending)
            except tk.TclError:
                pass
            self._pending = None

    def show(self, draw, height: int | None = None):
        self._draw = draw
        if height is not None:
            self._height = height
            self.configure(height=int(height * self.s))
        self.redraw()

    def rescale(self):
        """Settings > Text size changed: the display scale again, and the height it was given in the new scale
        (the drawing follows - see redraw_all). A tab that sets a chart's height itself sets it again too."""
        try:
            self.s = T.scale(self)
            if self._height is not None:
                self.configure(height=int(self._height * self.s))
        except tk.TclError:
            pass

    def clear(self, text: str = "", sub: str | None = None):
        self.show((lambda p: charts.message(p, text, sub)) if text else None)

    def _resized(self, _event=None):
        if self._pending is not None:
            try:
                self.after_cancel(self._pending)
            except tk.TclError:
                pass
        self._pending = self.after(40, self.redraw)

    def redraw(self, width: int | None = None, height: int | None = None):
        """Draw now. width/height override the widget's size (tests draw on windows that are never shown)."""
        if self._pending is not None:       # a direct call supersedes a queued one (and nothing is left to fire late)
            try:
                self.after_cancel(self._pending)
            except tk.TclError:
                pass
            self._pending = None
        try:
            forget_drawing(self)            # the old drawing's tips and clicks go with its items
            self.delete("all")
        except tk.TclError:
            return
        try:
            self._paint(width or self.winfo_width(), height or self.winfo_height())
        finally:
            try:
                drawing_done(self)          # ...and the hover/click bindings of tags it no longer uses
            except tk.TclError:
                pass

    def _paint(self, w: int, h: int):
        if self._draw is None or w < 20 or h < 20:
            return
        painter = TkPainter(self, w, h, self.s)
        try:
            self._draw(painter)
            self._reported = None
        except Exception:             # a chart bug must never break the tab
            details = traceback.format_exc().strip()
            if details != self._reported:          # once, not again on every resize
                self._reported = details
                _report(f"A chart couldn't be drawn:\n{details}")
            try:
                forget_drawing(self)
                self.delete("all")
                charts.message(painter, "This chart couldn't be drawn", "The details are in the Export tab's log.")
            except tk.TclError:
                pass

    def save_png(self, path: str, width: int | None = None, height: int | None = None) -> str:
        """The same chart as a PNG (needs Pillow) - for previews and tests."""
        from .paint import render_png
        w = width or max(self.winfo_width(), 400)
        h = height or max(self.winfo_height(), int(self.cget("height")))
        return render_png(self._draw, path, w, h, self.s)


# -- every chart in the new colours when the look changes -----------------------------------------------------------
_VIEWS: "weakref.WeakSet[ChartView]" = weakref.WeakSet()
_STALE: list = []                          # weak refs to the charts still to redraw in the new look


def _alive(view) -> bool:
    try:
        return bool(view.winfo_exists())
    except tk.TclError:
        return False


def _look_changed():
    """The charts on screen redraw at once; the rest (other tabs, a scrolled-away card) one at a time just after,
    so a change of look never keeps the window busy for long."""
    views = [v for v in list(_VIEWS) if _alive(v)]
    later = []
    for view in views:
        try:
            shown = view.winfo_viewable()
        except tk.TclError:
            continue
        if shown:
            view.redraw()
        else:
            later.append(weakref.ref(view))
    _STALE[:] = later
    if later:
        _schedule_next(views[0]._root())


_NEXT: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()    # window -> its queued _redraw_next


def _schedule_next(root):
    """Queue the next hidden chart's redraw - dropped if the window closes first, so nothing fires after it."""
    if root in _NEXT:
        return

    def run():
        _NEXT.pop(root, None)
        _redraw_next()
    try:
        _NEXT[root] = root.after(1, run)
    except tk.TclError:
        return
    if not getattr(root, "_charts_queue_guard", False):
        root._charts_queue_guard = True

        def closing(event):
            if event.widget is root:
                job = _NEXT.pop(root, None)
                if job is not None:
                    try:
                        root.after_cancel(job)
                    except tk.TclError:
                        pass
        root.bind("<Destroy>", closing, add="+")


def _redraw_next():
    while _STALE:
        view = _STALE.pop(0)()
        if view is not None and _alive(view):
            try:
                view.redraw()
                if _STALE:
                    _schedule_next(view._root())
            except tk.TclError:
                continue
            return


def redraws_pending() -> int:
    """Charts still to be redrawn in the new look (they're done a moment after the change)."""
    return sum(1 for ref in _STALE if ref() is not None)


def finish_redraws():
    """Redraw every chart still waiting for the new look now (tests)."""
    while _STALE:
        view = _STALE.pop(0)()
        if view is not None and _alive(view):
            view.redraw()


T.on_change(_look_changed)


def rescale_charts(root=None):
    """Settings > Text size changed: every chart (in root's window) takes the new scale and its height in it. They're
    drawn again by redraw_all(), once the tabs have re-measured themselves."""
    for view in [v for v in list(_VIEWS) if _alive(v)]:
        if root is None or view._root() is root:
            view.rescale()


def redraw_all():
    """Every chart drawn again: those on screen now, the rest one at a time just after (as for a new look)."""
    _look_changed()


# ---------------------------------------------------------------------------------------------------------
class Card(ttk.Frame):
    """A white panel with an optional title and hint line; put content in .body."""

    def __init__(self, parent, title: str | None = None, hint: str | None = None, padding: int = 12, **kw):
        super().__init__(parent, style="Card.TFrame", padding=padding, **kw)
        self.columnconfigure(0, weight=1)
        row = 0
        if title:
            self.title_label = ttk.Label(self, text=title, style="CardTitle.TLabel")
            self.title_label.grid(row=row, column=0, sticky="w")
            row += 1
        if hint:
            self.hint_label = ttk.Label(self, text=hint, style="CardHint.TLabel", wraplength=560, justify="left")
            self.hint_label.grid(row=row, column=0, sticky="w", pady=(0, 6))
            row += 1
            pad = padding if isinstance(padding, (int, float)) else 12
            # the hint wraps to the card's width, however narrow the card gets
            self.bind("<Configure>", lambda e: e.widget is self and self.hint_label.configure(
                wraplength=max(e.width - 2 * pad - 6, 120)), add="+")
        self.body = ttk.Frame(self, style="Card.TFrame", padding=0)
        self.body.configure(relief="flat", borderwidth=0)
        self.body.grid(row=row, column=0, sticky="nsew")
        self.rowconfigure(row, weight=1)


# ---------------------------------------------------------------------------------------------------------
class ScrollFrame(ttk.Frame):
    """A vertically scrolling area: put content in .inner (which always fills the width).

    The wheel scrolls it while the pointer is over it; the keys do (Page Up / Page Down, the arrows, Home / End)
    once it has the focus - a click anywhere in it that isn't in a box you type in or a list gives it that - and
    while a link in it has the focus. Tab moving the focus to a link scrolls the link into view (see()), and with
    follow_focus=True (a page of controls: the Settings tab) to anything in it.

    Only the keyboard's focus moves scroll it. A click gives what it's on the focus too (a tick box, a button), and
    Tk tells every widget between the page and it that the focus came in: scrolling then - to the card's top, say -
    would move the control out from under the pointer before the button came up, and the click would be lost.
    So the focus that comes with a click never scrolls the page (pointer_down), and neither does the word passed
    to the widgets on the way (only the widget given the focus counts: FOCUS_DETAILS)."""

    TAKES_KEYS = (tk.Entry, ttk.Entry, tk.Text, tk.Listbox, tk.Spinbox, ttk.Treeview)   # (these keep the focus)
    KEYS = (("<Prior>", "pages", -1), ("<Next>", "pages", 1), ("<Up>", "units", -1), ("<Down>", "units", 1),
            ("<Home>", "moveto", 0), ("<End>", "moveto", 1))

    def __init__(self, parent, background: str | None = None, follow_focus: bool = False, **kw):
        super().__init__(parent, **kw)
        token = T.token_for(background) if background is not None else "PAGE"
        self.canvas = tk.Canvas(self, background=T.value(token) if token else background, highlightthickness=0,
                                borderwidth=0, takefocus=1)
        if token:
            T.tint(self.canvas, background=token)
        self.bar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.bar.grid(row=0, column=1, sticky="ns")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.inner = ttk.Frame(self.canvas, style="Page.TFrame")
        # (a width from the start: on a tab not yet shown the canvas is never sized, and an inner page as wide as it
        # asks to be goes round and round with labels that wrap to their card - hundreds of layouts at startup)
        self._window = self.canvas.create_window(0, 0, window=self.inner, anchor="nw",
                                                 width=self.canvas.winfo_reqwidth())
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._window, width=e.width))
        for key, how, amount in self.KEYS:
            self.canvas.bind(key, lambda e, how=how, amount=amount: self._key(how, amount, e))
        self.follow_focus = follow_focus                  # (Tab moving to a control below the view shows it)
        # The wheel scrolls whichever page the pointer is over, the keys the one that has the focus - a click in the
        # page gives it: clicks on the cards inside it never reach the canvas itself - and a focus the keyboard
        # moved shows: window-wide bindings, made once per window and passed to its pages (_share_bindings)
        _share_bindings(self)

    def _focused(self, event):
        """The focus has come to event.widget from the keyboard (Tab): show it, if it's in this page."""
        try:
            widget = event.widget
            if isinstance(widget, tk.Misc) and widget is not self.canvas and self._inside(widget):
                self.see(widget)
        except (tk.TclError, KeyError):
            return

    def fits(self) -> bool:
        """Everything shows without scrolling."""
        return tuple(self.canvas.yview()) == (0.0, 1.0)

    def _inside(self, widget) -> bool:
        while widget is not None:
            if widget is self:
                return True
            widget = getattr(widget, "master", None)
        return False

    def _clicked(self, event):
        try:
            widget = event.widget
            if not isinstance(widget, tk.Misc) or isinstance(widget, self.TAKES_KEYS):
                return                                    # (a Tk-only widget, or one that takes the keys itself)
            # (not when the click took us to another tab: this page is hidden by then)
            if self.winfo_exists() and self._inside(widget) and self.canvas.winfo_viewable() and not self.fits():
                self.canvas.focus_set()
        except (tk.TclError, KeyError):
            return

    def _key(self, how: str, amount: int, event=None):
        # Ctrl+Page Up / Page Down switch the main tabs (the window's own keys): leave those to it
        state = getattr(event, "state", 0)
        if how == "pages" and isinstance(state, int) and state & 0x0004:
            return None
        try:
            if self.fits():
                return None
            if how == "moveto":
                self.canvas.yview_moveto(amount)
            else:
                self.canvas.yview_scroll(amount, how)
        except tk.TclError:
            return None
        return "break"

    def _wheel(self, event):
        try:
            if not self.winfo_exists():
                return
            widget = self.winfo_containing(event.x_root, event.y_root)
            while widget is not None:
                if widget is self:
                    break
                if isinstance(widget, (ttk.Treeview, tk.Listbox, tk.Text)):
                    return                                # those scroll themselves
                widget = getattr(widget, "master", None)
            if widget is self and self.canvas.yview() != (0.0, 1.0):
                self.canvas.yview_scroll(wheel_units(event), "units")
        except (tk.TclError, KeyError):                   # gone, or the pointer's over another app's window
            return

    def to_top(self):
        self.canvas.yview_moveto(0)

    def see(self, widget, margin: int = 8):
        """Scroll just enough to show widget (something in .inner) - e.g. a link Tab has moved the focus to."""
        try:
            top, w = 0, widget
            while w is not None and w is not self.inner:
                top += w.winfo_y()
                w = getattr(w, "master", None)
            if w is None:
                return                                    # not in this page
            total, view = self.inner.winfo_height(), self.canvas.winfo_height()
            if total <= view or view <= 1:
                return                                    # everything shows (or nothing is laid out yet)
            pad = int(margin * T.scale(self))
            bottom = top + widget.winfo_height()
            first = self.canvas.canvasy(0)                # the page's y at the top of the view
            if top - pad < first:
                self.canvas.yview_moveto(max(top - pad, 0) / total)
            elif bottom + pad > first + view:             # (the top of a tall one first)
                self.canvas.yview_moveto(min(bottom + pad - view, top - pad, total - view) / total)
        except (tk.TclError, AttributeError):
            return


# -- the mouse wheel ------------------------------------------------------------------------------------------------
# Windows and a Mac send <MouseWheel> with a delta (120 a notch on Windows, a few units on a Mac); X11 - Linux - sends
# the wheel as buttons 4 (up) and 5 (down) under Tk 8.6, with Shift held for Shift+wheel. Tk 9 sends <MouseWheel>
# everywhere. bind_wheel binds whichever come, wheel_units says how far each one scrolls.
WHEEL_BUTTONS = ("<Button-4>", "<Button-5>")


def wheel_units(event) -> int:
    """Lines to scroll for a wheel event: - up, + down. (Windows: as the app always did it, a notch a line, part of
    a notch - a touchpad's - none; a Mac: its delta; X11: a notch a line.)"""
    num = getattr(event, "num", None)
    if num == 4:
        return -1
    if num == 5:
        return 1
    try:
        delta = int(getattr(event, "delta", 0) or 0)
    except (TypeError, ValueError):
        return 0
    if sys.platform == "darwin":
        return -delta
    return int(-delta / 120)


def bind_wheel(widget, callback, everywhere: bool = False, add: bool = True):
    """callback(event) for the mouse wheel over widget - or, everywhere=True, anywhere in its window (bind_all) -
    on every system: <MouseWheel>, and X11's buttons 4 and 5 (Shift+wheel too: those bindings take it as well, as
    <MouseWheel> does on Windows). Use wheel_units(event) for how far."""
    bind = widget.bind_all if everywhere else widget.bind
    for sequence in ("<MouseWheel>",) + (WHEEL_BUTTONS if widget._windowingsystem == "x11" else ()):
        bind(sequence, callback, add="+" if add else "")


# -- what the ScrollFrames of a window share ----------------------------------------------------------------------
# Tk sends FocusIn to the widget given the focus (detail NotifyAncestor, or NotifyNonlinear when the focus came from
# a widget that isn't its parent), and NotifyVirtual / NotifyNonlinearVirtual to each widget between it and where
# the focus was: a page, a card, a row. Only the first two mean "this widget has the focus now".
FOCUS_DETAILS = ("NotifyAncestor", "NotifyNonlinear")
POINTER_HELD = 5.0       # seconds: a press whose release never came (a dialog took it) no longer counts after this


def pointer_down(widget) -> bool:
    """Whether the (left) mouse button is down in widget's window - a click under way, so a focus change now is
    the click's doing, not the keyboard's."""
    try:
        pointer = widget._root().__dict__.get("_pointer")
    except (AttributeError, tk.TclError):
        return False
    return bool(pointer and pointer["down"] and time.monotonic() - pointer["at"] < POINTER_HELD)


def scroll_pages(root) -> list:
    """The ScrollFrames of root's window that are still there, in the order they were made."""
    refs = root.__dict__.get("_scroll_pages") or []
    live = [p for p in (r() for r in refs) if p is not None and _alive(p)]
    if len(live) != len(refs):
        refs[:] = [weakref.ref(p) for p in live]
    return live


def _share_bindings(page: ScrollFrame):
    """One binding each, for the whole window, for what every ScrollFrame in it needs to hear of: the wheel, a
    click and its end, and the focus moving - made with the window's first page and passed to its pages that are
    still there. (A binding per page would stay behind in Tk, and keep the page alive, after the page had gone.)"""
    root = page._root()
    refs = root.__dict__.get("_scroll_pages")
    if refs is not None:
        refs.append(weakref.ref(page))
        scroll_pages(root)                            # (drops the ones gone)
        return
    refs = root.__dict__["_scroll_pages"] = [weakref.ref(page)]      # (in the order made, as the bindings were)
    pointer = root.__dict__["_pointer"] = {"down": False, "at": 0.0}

    def each(method, event):
        for p in scroll_pages(root):
            getattr(p, method)(event)                 # (each one checks the event is about it)

    def pressed(event):
        pointer.update(down=True, at=time.monotonic())
        each("_clicked", event)

    def released(_event):
        pointer["down"] = False

    def came_back():
        pointer["back"] = False

    def focus_in(path, detail):
        try:
            widget = root.nametowidget(path)
        except KeyError:                              # (a widget of Tk's own: a drop-down's list)
            return
        if widget is widget.winfo_toplevel() and detail in ("NotifyNonlinear", "NotifyNonlinearVirtual"):
            # The focus came into the window from outside - back from another program, most likely by a click:
            # Tk gives it back to the widget that had it, before the click's own press arrives. That widget may
            # have been scrolled out of view since, and showing it now would move the page under the pointer.
            pointer["back"] = True
            root.after_idle(came_back)                # (the rest of this change of focus comes before then)
            return
        if detail not in FOCUS_DETAILS or pointer_down(root) or pointer.get("back"):
            return                                    # (a widget on the way to the focus, or a click's focus)
        event = types.SimpleNamespace(widget=widget)
        for p in scroll_pages(root):
            if p.follow_focus:
                p._focused(event)

    bind_wheel(root, lambda e: each("_wheel", e), everywhere=True)
    root.bind_all("<Button-1>", pressed, add="+")
    root.bind_all("<ButtonRelease-1>", released, add="+")
    # (tkinter's events don't carry the detail: bound in Tcl, with %d)
    root.tk.call("bind", "all", "<FocusIn>", f"+{root.register(focus_in)} %W %d")


# ---------------------------------------------------------------------------------------------------------
def _sort_key(value):
    if value is None or value == "":
        return (1, 0, "")
    if isinstance(value, (int, float)):
        return (0, value, "")
    text = str(value)
    m = re.fullmatch(r"[+-]?\d+(?:[.,]\d+)?%?", text.replace(",", ""))
    if m:
        return (0, float(text.replace(",", "").rstrip("%")), "")
    return (0, float("inf"), text.casefold())


class Table(ttk.Frame):
    """A sortable list (ttk.Treeview) of dict rows.

    columns: [(key, heading, width, anchor)] - anchor 'w' or 'e' (numbers). Click a heading to sort.
    on_select(row) fires when the selection changes, on_open(row) on double-click or Enter.
    """

    def __init__(self, parent, columns, height: int = 12, on_select=None, on_open=None, **kw):
        super().__init__(parent, **kw)
        self.columns = columns
        self.rows: dict[str, dict] = {}
        self.on_select, self.on_open = on_select, on_open
        self.sorted_by: tuple[str, bool] | None = None
        s = T.scale(parent)
        self.tree = ttk.Treeview(self, columns=[c[0] for c in columns], show="headings", height=height,
                                 selectmode="browse")
        for key, heading, width, anchor in columns:
            self.tree.heading(key, text=heading, anchor=anchor, command=lambda k=key: self.sort(k))
            self.tree.column(key, width=int(width * s), anchor=anchor, stretch=anchor == "w")
        bar = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=bar.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        bar.grid(row=0, column=1, sticky="ns")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        T.tint_tag(self.tree, "odd", background="TREE_ALT_BG")      # every other row
        self.tree.bind("<<TreeviewSelect>>", self._selected)
        self.tree.bind("<Double-1>", self._double_clicked)
        self.tree.bind("<Return>", self._opened)
        self.widened = self.fit_headings() if not T.WINDOWS else []  # (the columns made wider for their headings)
        self._width_job = None
        if self.widened:
            self.tree.bind("<Configure>", lambda e: self._fit_later(), add="+")

    HEADING_ROOM = 16            # px (device-independent) a heading needs besides its words: its padding and edges

    def fit_headings(self, cap: int = 180):
        """Widen the columns whose headings don't fit them, sort arrow and all, measured in the font the headings
        are drawn in. The widths are chosen for Windows' Segoe UI; the fonts of Linux desktops are mostly wider,
        so there ('Predicted', 'Confidence' and 'Credits start' were cut short) this runs as the table is made.
        A column is never made wider than `cap`. -> the columns widened."""
        from tkinter import font as tkfont
        try:
            font = tkfont.Font(root=self._root(), font=ttk.Style(self).lookup("Treeview.Heading", "font")
                               or "TkHeadingFont")
        except tk.TclError:
            return []
        s = T.scale(self)
        widened, columns = [], []
        for key, heading, width, anchor in self.columns:
            need = (font.measure(f"{heading} ▼") + self.HEADING_ROOM * s) / s     # (device-independent)
            if need > width:
                width = int(min(need, max(cap, width)) + 0.999)
                self.tree.column(key, width=int(width * s))
                widened.append(key)
            columns.append((key, heading, width, anchor))
        self.columns = columns
        return widened

    def _fit_later(self):
        if self._width_job is None:
            try:
                self._width_job = self.tree.after_idle(self.fit_width)
            except tk.TclError:
                pass

    def fit_width(self) -> bool:
        """(When fit_headings widened a column.) Every column shown fits the list's width: when they add up to
        more than it has, the stretching columns give up the difference - each in proportion to what it has above
        its least, never below it. (ttk's list only cuts off what doesn't fit.) Columns are only ever narrowed
        here, never widened: in a layout that follows the list's own size, widening would make it wider still,
        without end. Run once the list is laid out, after any change of its size or of the columns shown (a tab's
        own <Configure> may leave some out first). -> whether any width changed."""
        self._width_job = None
        tree = self.tree
        try:
            width = tree.winfo_width()
            if width < 50:
                return False
            shown = list(tree.tk.splitlist(tree.cget("displaycolumns")))
            if not shown or shown == ["#all"]:
                shown = [c[0] for c in self.columns]
            now = {k: int(tree.column(k, "width")) for k in shown}
            over = sum(now.values()) - (width - 4)                      # (the list's edges)
            stretch = [k for k in shown if int(tree.column(k, "stretch"))]
            if over <= 0 or not stretch:
                return False
            spare = {k: max(now[k] - int(tree.column(k, "minwidth")), 0) for k in stretch}
            total = sum(spare.values())
            if total <= 0:
                return False
            changed = False
            for k in stretch:
                give = min(spare[k], -(-over * spare[k] // total))     # (rounded up: all of it goes)
                if give > 0:
                    tree.column(k, width=now[k] - give)
                    changed = True
            return changed
        except (tk.TclError, ValueError, ZeroDivisionError):
            return False

    def set_rows(self, rows: list[dict], keep_sort: bool = True):
        self.tree.delete(*self.tree.get_children())
        self.rows = {}
        for i, row in enumerate(rows):
            iid = str(i)
            self.rows[iid] = row
            self.tree.insert("", "end", iid=iid, values=[self._cell(row.get(c[0])) for c in self.columns],
                             tags=("odd",) if i % 2 else ())
        if keep_sort and self.sorted_by:
            key, reverse = self.sorted_by
            self.sort(key, reverse)

    @staticmethod
    def _cell(value):
        if value is None:
            return ""
        if isinstance(value, float):
            return f"{value:.1f}"
        return value

    def sort(self, key: str, reverse: bool | None = None):
        if reverse is None:
            reverse = self.sorted_by == (key, False)
        items = list(self.tree.get_children())
        items.sort(key=lambda iid: _sort_key(self.rows[iid].get(key)), reverse=reverse)
        for n, iid in enumerate(items):
            self.tree.move(iid, "", n)
            self.tree.item(iid, tags=("odd",) if n % 2 else ())
        self.sorted_by = (key, reverse)
        for k, heading, _w, _a in self.columns:
            arrow = (" ▼" if reverse else " ▲") if k == key else ""
            self.tree.heading(k, text=heading + arrow)

    def set_heading(self, key: str, text: str):
        """Rename a column (keeps the sort arrow)."""
        self.columns = [(k, text if k == key else h, w, a) for k, h, w, a in self.columns]
        arrow = ""
        if self.sorted_by and self.sorted_by[0] == key:
            arrow = " ▼" if self.sorted_by[1] else " ▲"
        self.tree.heading(key, text=text + arrow)

    def selected(self) -> dict | None:
        sel = self.tree.selection()
        return self.rows.get(sel[0]) if sel else None

    def select_first(self):
        children = self.tree.get_children()
        if children:
            self.tree.selection_set(children[0])
            self.tree.focus(children[0])
            self.tree.see(children[0])

    def _selected(self, _event=None):
        if self.on_select:
            row = self.selected()
            if row is not None:
                self.on_select(row)

    def _opened(self, _event=None):
        """Enter: open the selected row."""
        if self.on_open:
            row = self.selected()
            if row is not None:
                self.on_open(row)

    def _double_clicked(self, event):
        """Open the row that was double-clicked - not whatever is selected: two quick clicks on a heading (to
        flip the sort) or on the empty space below the rows open nothing."""
        if not self.on_open or self.tree.identify_region(event.x, event.y) not in ("cell", "tree"):
            return
        iid = self.tree.identify_row(event.y)
        if iid in self.rows:
            if self.tree.selection() != (iid,):          # (the first click has normally selected it already)
                self.tree.selection_set(iid)
                self.tree.focus(iid)
            self.on_open(self.rows[iid])


# ---------------------------------------------------------------------------------------------------------
class SearchBox(ttk.Frame):
    """An entry with a drop-down of suggestions as you type.

    suggest(text) -> list of strings; on_pick(text) runs on Enter or when a suggestion is chosen.
    """

    @property
    def LIST_FONT(self) -> str:          # noqa: N802  (the drop-down's font)
        return T.font(self, 9)

    def __init__(self, parent, suggest, on_pick=None, width: int = 28, placeholder: str = "", **kw):
        super().__init__(parent, **kw)
        self.suggest, self.on_pick = suggest, on_pick
        self.var = tk.StringVar()
        self.entry = ttk.Entry(self, textvariable=self.var, width=width)
        self.entry.grid(row=0, column=0, sticky="ew")
        self.columnconfigure(0, weight=1)
        self.popup: tk.Toplevel | None = None
        self.listbox: tk.Listbox | None = None
        self._pending = None
        self.placeholder = placeholder
        self.entry.bind("<KeyRelease>", self._typed, add="+")
        self.entry.bind("<Down>", self._down, add="+")
        self.entry.bind("<Return>", self._enter, add="+")
        self.entry.bind("<Escape>", lambda e: self.hide(), add="+")
        self.entry.bind("<FocusOut>", lambda e: self.after(150, self._maybe_hide), add="+")

    def get(self) -> str:
        return self.var.get().strip()

    def set(self, text: str):
        self.var.set(text)
        self.hide()

    def _typed(self, event):
        if event.keysym in ("Down", "Up", "Return", "Escape", "Tab"):
            return
        if self._pending is not None:
            self.after_cancel(self._pending)
        self._pending = self.after(150, self._refresh)

    def _refresh(self):
        self._pending = None
        text = self.get()
        if len(text) < 2:
            self.hide()
            return
        try:
            options = list(self.suggest(text))[:12]
        except Exception:
            options = []
        if not options or (len(options) == 1 and options[0].casefold() == text.casefold()):
            self.hide()
            return
        self._show(options)

    def popup_width(self, options) -> int:
        """Wide enough for the longest suggestion (some say who a name is: 'John Smith (7 films, e.g. ...)'),
        at least as wide as the box, and at most about 480 px."""
        from tkinter import font as tkfont
        s = T.scale(self)
        try:
            measure = tkfont.Font(root=self._root(), font=self.LIST_FONT).measure
            longest = max((measure(str(o)) for o in options), default=0) + int(16 * s)   # (border and margins)
        except tk.TclError:
            longest = 0
        return int(min(max(self.entry.winfo_width(), longest, 220 * s), 480 * s))

    def _show(self, options):
        if not self.winfo_viewable():           # never pop anything up from a hidden window (tests)
            return
        if self.popup is None:
            self.popup = tk.Toplevel(self)
            self.popup.wm_overrideredirect(True)
            self.listbox = tk.Listbox(self.popup, activestyle="none", borderwidth=0, relief="flat",
                                      highlightthickness=1, font=self.LIST_FONT)
            T.tint(self.listbox, background="MENU_BG", foreground="MENU_FG", selectbackground="LIST_SELECT_BG",
                   selectforeground="LIST_SELECT_FG", highlightbackground="MENU_BORDER",
                   highlightcolor="MENU_BORDER")
            self.listbox.pack(fill="both", expand=True)
            self.listbox.bind("<ButtonRelease-1>", self._clicked)
            self.listbox.bind("<Return>", self._clicked)
            self.listbox.bind("<Escape>", lambda e: (self.hide(), self.entry.focus_set()))
        self.listbox.delete(0, "end")
        for o in options:
            self.listbox.insert("end", o)
        self.listbox.configure(height=len(options))
        width = self.popup_width(options)
        x = self.entry.winfo_rootx()
        y = self.entry.winfo_rooty() + self.entry.winfo_height()
        self.popup.wm_geometry(f"{width}x{self.listbox.winfo_reqheight()}+{x}+{y}")
        self.popup.deiconify()
        self.popup.lift()

    def _down(self, _event):
        if self.popup is not None and self.listbox is not None and self.listbox.size():
            self.listbox.focus_set()
            self.listbox.selection_clear(0, "end")
            self.listbox.selection_set(0)
            self.listbox.activate(0)
            return "break"

    def _clicked(self, _event=None):
        if self.listbox is None:
            return
        sel = self.listbox.curselection()
        if sel:
            self.var.set(self.listbox.get(sel[0]))
            self.hide()
            self.entry.focus_set()
            self.entry.icursor("end")
            if self.on_pick:
                self.on_pick(self.get())

    def _enter(self, _event=None):
        self.hide()
        if self.on_pick and self.get():
            self.on_pick(self.get())

    def _maybe_hide(self):
        try:
            focus = self.focus_get()
        except (tk.TclError, KeyError):
            focus = None
        if focus is not self.listbox:
            self.hide()

    def hide(self):
        if self.popup is not None:
            self.popup.withdraw()


# ---------------------------------------------------------------------------------------------------------
ACTIVATE_KEYS = ("<Return>", "<KP_Enter>", "<space>")


def keyboard_link(widget, command, marks=None):
    """Make something you click reachable from the keyboard too: Tab stops on it, Enter or Space opens it (runs
    command()), and while it has the focus the labels in marks (by default the widget itself) are underlined - the
    focus mark - and a scrolling page scrolls to show it.

    For a clickable row (a frame of labels) pass the frame, and its title label as the mark."""
    marks = [widget] if marks is None else list(marks)
    widget.configure(takefocus=1)

    def activate(_event=None):
        command()
        return "break"                        # (not the window's own Enter or Space as well)

    def focus_in(_event=None):
        for label in marks:
            underline(label, True)
        if not pointer_down(widget):          # (a click's focus: the page stays put under the pointer)
            for page in _scroll_frames(widget):
                page.see(widget)

    def focus_out(_event=None):
        for label in marks:
            underline(label, False)

    def scroll(how, amount, event):
        pages = _scroll_frames(widget)
        return pages[0]._key(how, amount, event) if pages else None

    for key in ACTIVATE_KEYS:
        widget.bind(key, activate, add="+")
    # the page's own keys still scroll it while a link on it has the focus
    for key, how, amount in ScrollFrame.KEYS:
        widget.bind(key, lambda e, how=how, amount=amount: scroll(how, amount, e), add="+")
    widget.bind("<FocusIn>", focus_in, add="+")
    widget.bind("<FocusOut>", focus_out, add="+")
    return widget


def underline(label, on: bool = True):
    """Underline a label's text (its own font or its style's), or put its font back."""
    try:
        if on:
            if getattr(label, "_plain_font", None) is None:
                label._plain_font = str(label.cget("font"))     # '' = its style's font
            label.configure(font=_underlined_font(label))
        elif getattr(label, "_plain_font", None) is not None:
            label.configure(font=label._plain_font)
            label._plain_font = None
    except tk.TclError:                       # gone (a link that opened a new page), or not a label
        pass


def _underlined_font(label):
    from tkinter import font as tkfont
    spec = str(label.cget("font") or "")
    if not spec:
        try:
            spec = str(ttk.Style(label).lookup(label.cget("style") or label.winfo_class(), "font") or "")
        except tk.TclError:
            spec = ""
    spec = spec or "TkDefaultFont"
    root = label._root()
    fonts = root.__dict__.setdefault("_underlined_fonts", {})    # (kept: a Font is deleted with its last reference)
    if spec not in fonts:
        fonts[spec] = tkfont.Font(root=root, font=spec)
        fonts[spec].configure(underline=1)
    return fonts[spec]


def _scroll_frames(widget):
    """The ScrollFrames a widget is inside, innermost first."""
    out, w = [], getattr(widget, "master", None)
    while w is not None:
        if isinstance(w, ScrollFrame):
            out.append(w)
        w = getattr(w, "master", None)
    return out


class LinkLabel(ttk.Label):
    """Clickable text - by mouse, or Tab to it and press Enter or Space (it's underlined while it has the focus)."""

    def __init__(self, parent, text: str, command, style: str = "Link.TLabel", **kw):
        super().__init__(parent, text=text, style=style, cursor="hand2", **kw)
        self.command = command
        self.bind("<Button-1>", lambda e: command(), add="+")
        keyboard_link(self, command)
        if "takefocus" in kw:                 # (a caller that keeps Tab off it)
            self.configure(takefocus=kw["takefocus"])


def suggester(names, limit: int = 12):
    """suggest(text) over a list of names: names starting with the text first, then ones containing it."""
    from ..catalog import fold
    folded = [(fold(n), n) for n in names]

    def suggest(text: str):
        q = fold(text)
        if not q:
            return []
        starts = [n for f, n in folded if f.startswith(q)]
        words = [n for f, n in folded if not f.startswith(q) and f" {q}" in f" {f}"]
        return (starts + words)[:limit]
    return suggest
