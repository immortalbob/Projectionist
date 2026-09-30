"""The Settings tab: every setting in one place, built from the preferences registry (projectionist/prefs.py).

Each section is a card, in the sections' order, and each setting shown is a row: its name, its control and its
help text. A change is saved at once and shows at once - the looks and the text size too (a setting that only
takes effect when the app next opens says so). Appearance comes first: the four looks and Follow Windows as small
pictures of the window, each drawn in its own colours with the Painter (so they render headless too), Graphite
marked as the default. Each card can be put back to its defaults, and so can everything; the line at the top says
where the settings are kept.

The other tabs change some of the same settings (the Export tab's ticks, the Library Doctor's languages): prefs.set
tells every tab, so this one keeps up (preference_changed), and the other way round.

A setting of a new kind needs a control here: add_control(kind, builder), builder(tab, parent, pref) -> a Control.
"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .. import APP_NAME, prefs
from . import theme as T
from .base import BaseTab
from .paint import Painter
from .widgets import Card, ChartView, ScrollFrame

PAD = 16                    # page margin (as on the other tabs)
GAP = 12                    # space between cards
TILE_W, TILE_H = 176, 116   # a look's picture (device-independent px)
TILE_GAP = 14
FROM_NEXT_START = "Takes effect the next time the app opens."
NATIVE_DIALOGS = ("Windows' own boxes - choosing files and folders, and messages like the 'Put back to their "
                  "defaults?' question - keep Windows' own colours whatever the look." if T.WINDOWS else
                  "The boxes for choosing files and folders, and messages like the 'Put back to their defaults?' "
                  "question, take the look's colours as near as Tk can draw them.")
NOT_SAVED = "Couldn't save this in the settings file - it lasts until the app closes."


# ---------------------------------------------------------------------------------------------------------
# A look, in miniature
# ---------------------------------------------------------------------------------------------------------
def _line(p: Painter, x, y, w, color, thick=None):
    """A line of 'text': a short bar."""
    p.rect(x, y, x + max(w, 1), y + (thick or max(2.0, p.u(2))), color)


def draw_window(p: Painter, t: dict, dark: bool, x: float, y: float, w: float, h: float):
    """The main window in miniature, in the tokens t: title bar, tabs (the middle one selected), a card on the page
    with a heading, text, a small chart, a link and the main button, and the status bar."""
    u = p.u
    title_h, tabs_h, status_h = u(12), u(13), u(9)
    # title bar: the app's name, and the window's buttons
    p.rect(x, y, x + w, y + title_h, t["TITLE_BG"])
    _line(p, x + u(5), y + title_h / 2 - u(1), min(w * 0.32, u(46)), t["TITLE_FG"])
    for i in range(3):
        bx = x + w - u(7) - i * u(9)
        p.rect(bx - u(2), y + title_h / 2 - u(1), bx + u(2), y + title_h / 2 + u(1), t["TITLE_FG"])
    # the tab strip
    ty = y + title_h
    p.rect(x, ty, x + w, ty + tabs_h, t["TAB_STRIP_BG"])
    tab_w = min(u(28), (w - u(8)) / 3.2)
    for i in range(3):
        x0 = x + u(4) + i * (tab_w + u(1))
        chosen = i == 1
        if chosen:
            p.rect(x0, ty + u(2), x0 + tab_w, ty + tabs_h, t["TAB_SELECTED_BG"],
                   outline=None if dark else t["TAB_BORDER"])
            if dark:
                p.rect(x0, ty + u(2), x0 + tab_w, ty + u(4), t["TAB_INDICATOR"])
        _line(p, x0 + tab_w * 0.2, ty + tabs_h / 2 + u(0.5), tab_w * 0.6,
              t["TAB_SELECTED_FG"] if chosen else t["TAB_FG"])
    # the page, and a card on it
    py = ty + tabs_h
    ph = h - title_h - tabs_h - status_h
    p.rect(x, py, x + w, py + ph, t["PAGE"])
    cx0, cy0, cx1, cy1 = x + u(6), py + u(5), x + w - u(6), py + ph - u(5)
    p.rect(cx0, cy0, cx1, cy1, t["CARD"], outline=t["BORDER"])
    cw = cx1 - cx0
    p.text(cx0 + u(6), cy0 + u(9), "Aa", p.font(9, "bold"), t["ACCENT"], "w")
    _line(p, cx0 + u(6), cy0 + u(19), cw * 0.42, t["INK"])
    _line(p, cx0 + u(6), cy0 + u(25), cw * 0.32, t["MUTED"])
    _line(p, cx0 + u(6), cy0 + u(31), cw * 0.22, t["LINK"])
    # the main button
    bw, bh = min(u(34), cw * 0.4), u(9)
    bx0, by0 = cx0 + u(6), cy1 - u(5) - bh
    p.rect(bx0, by0, bx0 + bw, by0 + bh, t["ACCENT_BUTTON_BG"], outline=t["BUTTON_BORDER"] if not dark else None)
    _line(p, bx0 + bw * 0.25, by0 + bh / 2 - u(1), bw * 0.5, t["ACCENT_BUTTON_FG"])
    # a small chart: gridlines, four columns in the first series colours, the baseline
    gx0, gx1 = cx0 + cw * 0.56, cx1 - u(6)
    gy0, gy1 = cy0 + u(8), cy1 - u(6)
    if gx1 - gx0 > u(16) and gy1 - gy0 > u(12):
        for f in (0.33, 0.66):
            gy = gy1 - (gy1 - gy0) * f
            p.rect(gx0, gy, gx1, gy + 1, t["GRID"])
        slot = (gx1 - gx0) / 4
        for i, share in enumerate((0.55, 0.9, 0.7, 0.4)):
            bx = gx0 + i * slot + slot * 0.2
            p.rect(bx, gy1 - (gy1 - gy0) * share, bx + slot * 0.6, gy1, t["SERIES"][i])
        p.rect(gx0, gy1, gx1, gy1 + 1, t["BASELINE"])
    # the status bar
    sy = y + h - status_h
    p.rect(x, sy, x + w, y + h, t["CHROME"])
    _line(p, x + u(5), sy + status_h / 2 - u(1), w * 0.3, t["HINT"])


def draw_look(p: Painter, key: str, dark_look: str = T.DEFAULT_DARK_LOOK, selected: bool = False):
    """A look's picture for the Settings tab, filling the painter: the window in that look's own colours - Follow
    Windows ('windows') as Light beside dark_look - with a ring in the look in use's focus colour when it's the
    one chosen."""
    u = p.u
    ring = u(2)
    x, y, w, h = ring + u(1), ring + u(1), p.width - 2 * (ring + u(1)), p.height - 2 * (ring + u(1))
    if key == T.FOLLOW_WINDOWS:
        half = w / 2
        draw_window(p, T.tokens("light"), False, x, y, half, h)
        draw_window(p, T.tokens(dark_look), True, x + half, y, w - half, h)
    else:
        draw_window(p, T.tokens(key), T.LOOKS[key].dark, x, y, w, h)
    edge = T.FOCUS if selected else T.BORDER
    width = ring if selected else 1
    p.rect(x - width / 2 - 0.5, y - width / 2 - 0.5, x + w + width / 2 + 0.5, y + h + width / 2 + 0.5, None,
           outline=edge, width=width)


def look_name(key: str, dark_look: str = T.DEFAULT_DARK_LOOK) -> str:
    if key == T.FOLLOW_WINDOWS:
        return T.FOLLOW_NAME
    return T.LOOKS[key].name + ("  (default)" if key == T.DEFAULT_LOOK else "")


def look_words(key: str, dark_look: str = T.DEFAULT_DARK_LOOK, windows_dark: bool | None = None) -> str:
    """One or two plain sentences about a choice of look (windows_dark: whether the system is dark now, if
    known)."""
    if key != T.FOLLOW_WINDOWS:
        return T.LOOKS[key].blurb
    dark = T.LOOKS.get(dark_look, T.LOOKS[T.DEFAULT_DARK_LOOK]).name
    if T.WINDOWS:
        words = f"Light while Windows' apps are light, {dark} while they're dark - checked each time you come back " \
                f"to the window."
        if windows_dark is not None:
            words += f" Windows' apps are {'dark' if windows_dark else 'light'} now."
        return words
    words = f"Light while your desktop is set to light, {dark} while it's set to dark - checked each time you come " \
            f"back to the window."
    if windows_dark is not None:
        words += f" Your desktop is set to {'dark' if windows_dark else 'light'} now."
    else:                                        # (chosen before, on a desktop that said; this one doesn't)
        words += " Your desktop isn't saying which just now, so it's Light."
    return words


# ---------------------------------------------------------------------------------------------------------
# Controls: one setting's row each
# ---------------------------------------------------------------------------------------------------------
class Control:
    """A setting's row on the Settings tab: its name, its control and its help text, in self.frame. A subclass
    builds its control in build() and puts a value in it in show(value)."""

    labelled = True                    # (a tick box carries its own name)

    def __init__(self, tab, parent, pref: prefs.Pref):
        self.tab, self.pref = tab, pref
        self.frame = ttk.Frame(parent, style="CardInner.TFrame")
        self.frame.columnconfigure(0, weight=1)
        row = 0
        if self.labelled:
            ttk.Label(self.frame, text=pref.label, style="Settings.Name.TLabel").grid(row=row, column=0, sticky="w")
            row += 1
        self.body = ttk.Frame(self.frame, style="CardInner.TFrame")
        self.body.grid(row=row, column=0, sticky="ew", pady=(2, 0))
        row += 1
        self.error = ttk.Label(self.frame, text="", style="Settings.Error.TLabel", justify="left")
        self.error.grid(row=row, column=0, sticky="w")
        self.error.grid_remove()
        tab.wraps.append(self.error)
        row += 1
        help_text = pref.help + ("  " + FROM_NEXT_START if not pref.live else "")
        if help_text.strip():
            self.help = ttk.Label(self.frame, text=help_text.strip(), style="CardHint.TLabel", justify="left")
            self.help.grid(row=row, column=0, sticky="w", pady=(2, 0))
            tab.wraps.append(self.help)
        self.build()
        self.show(self.value())

    # -- for subclasses ---------------------------------------------------------------------------------------
    def build(self):
        pass

    def show(self, value):
        pass

    def value(self):
        return prefs.get(self.tab.app, self.pref.key)

    def save(self, value) -> bool:
        """Keep a new value (saved and applied at once). -> False, with the reason shown, if it's no good. A value
        the settings file couldn't take is kept for now, and the row says so."""
        try:
            prefs.set(self.tab.app, self.pref.key, value)
        except (ValueError, TypeError) as exc:
            self.say(str(exc))
            return False
        self.say("" if prefs.save_ok(self.tab.app) else NOT_SAVED)
        return True

    def say(self, text: str):
        self.error.configure(text=text)
        if text:
            self.error.grid()
        else:
            self.error.grid_remove()


class BoolControl(Control):
    labelled = False

    def build(self):
        self.var = tk.BooleanVar(self.frame)
        self.check = ttk.Checkbutton(self.body, text=self.pref.label, variable=self.var, style="Card.TCheckbutton",
                                     command=lambda: self.save(self.var.get()))
        self.check.grid(row=0, column=0, sticky="w")

    def show(self, value):
        self.var.set(bool(value))


class ChoiceControl(Control):
    """Radio buttons for a few fixed choices; a drop-down for many, or for choices that depend on the computer
    (the spreadsheet programs found)."""

    RADIOS_UP_TO = 4

    def build(self):
        self.choices = self.pref.choices_for(self.tab.app) or []
        self.var = tk.StringVar(self.frame)
        self.buttons = []
        if len(self.choices) <= self.RADIOS_UP_TO and not callable(self.pref.choices):
            for n, (value, label) in enumerate(self.choices):
                rb = ttk.Radiobutton(self.body, text=label, value=str(value), variable=self.var,
                                     style="Card.TRadiobutton", command=lambda v=value: self.save(v))
                rb.grid(row=0, column=n, sticky="w", padx=(0, 18))
                self.buttons.append(rb)
            self.box = None
        else:
            self.box = ttk.Combobox(self.body, state="readonly", width=28, textvariable=self.var,
                                    values=[label for _v, label in self.choices])
            self.box.grid(row=0, column=0, sticky="w")
            self.box.bind("<<ComboboxSelected>>", lambda e: self._picked(), add="+")

    def _picked(self):
        label = self.var.get()
        value = next((v for v, lab in self.choices if lab == label), label)
        self.save(value)

    def show(self, value):
        if self.box is not None:
            self.var.set(next((lab for v, lab in self.choices if v == value), str(value or "")))
        else:
            self.var.set(str(value))

    def enable(self, on: bool):
        for w in self.buttons + ([self.box] if self.box is not None else []):
            try:
                w.state(["!disabled"] if on else ["disabled"])
            except tk.TclError:
                pass


class NumberControl(Control):
    """A spin box. A number typed outside the setting's range is refused (and says the range), never quietly
    brought inside it."""

    def build(self):
        p = self.pref
        self.var = tk.StringVar(self.frame)
        self.spin = ttk.Spinbox(self.body, from_=p.minimum if p.minimum is not None else -1e9,
                                to=p.maximum if p.maximum is not None else 1e9, increment=p.step or 1, width=6,
                                textvariable=self.var, command=self._typed)
        self.spin.grid(row=0, column=0, sticky="w")
        if p.unit:
            ttk.Label(self.body, text=p.unit, style="CardNote.TLabel").grid(row=0, column=1, sticky="w", padx=(6, 0))
        for sequence in ("<Return>", "<KP_Enter>", "<FocusOut>"):
            self.spin.bind(sequence, lambda e: self._typed(), add="+")

    def _typed(self):
        text = self.var.get().strip()
        if not text:
            self.say("")                       # (emptied: the value as it was, and no old complaint)
            self.show(self.value())
            return
        p = self.pref
        try:
            number = float(text)
        except (ValueError, OverflowError):
            number = None                      # (save() says it isn't a number)
        if number is not None and ((p.minimum is not None and number < p.minimum) or
                                   (p.maximum is not None and number > p.maximum)):
            self.say(f"{p.label}: {self.range_words()}, not {text}")
            return
        if self.save(text):
            self.show(self.value())            # (rounded)

    def range_words(self) -> str:
        p = self.pref
        unit = f" {p.unit}" if p.unit else ""
        if p.minimum is not None and p.maximum is not None:
            return f"from {p.minimum:g} to {p.maximum:g}{unit}"
        if p.minimum is not None:
            return f"{p.minimum:g}{unit} or more"
        return f"{p.maximum:g}{unit} or fewer"

    def show(self, value):
        self.var.set(str(value))


class TextControl(Control):
    """A box to type in (text, or a kind with its own words - 'languages'): kept on Enter or on leaving the box."""

    def build(self):
        self.var = tk.StringVar(self.frame)
        self.entry = ttk.Entry(self.body, textvariable=self.var, width=44)
        self.entry.grid(row=0, column=0, sticky="w")
        for sequence in ("<Return>", "<KP_Enter>", "<FocusOut>"):
            self.entry.bind(sequence, lambda e: self._typed(), add="+")

    def _typed(self):
        if self.var.get() == self.pref.words(self.value(), self.tab.app):
            self.say("")
            return                             # (unchanged)
        if self.save(self.var.get()):
            self.show(self.value())

    def show(self, value):
        self.var.set(self.pref.words(value, self.tab.app))


class FolderControl(Control):
    NONE = "None chosen"

    def build(self):
        self.var = tk.StringVar(self.frame)
        self.entry = ttk.Entry(self.body, textvariable=self.var, width=52, state="readonly")
        self.entry.grid(row=0, column=0, sticky="w")
        ttk.Button(self.body, text="Choose...", command=self._choose).grid(row=0, column=1, padx=(6, 0))
        self.clear = ttk.Button(self.body, text="Clear", style="Small.TButton", command=lambda: self.save(""))
        self.clear.grid(row=0, column=2, padx=(6, 0))

    def _choose(self):
        current = self.value()
        folder = filedialog.askdirectory(parent=self.frame, title=self.pref.label,
                                         initialdir=current if current and os.path.isdir(current) else None)
        if folder:
            self.save(os.path.normpath(folder))

    def show(self, value):
        self.var.set(value or self.NONE)
        self.clear.state(["!disabled"] if value else ["disabled"])


class LooksControl(Control):
    """The looks as pictures of the window, each with a radio button under it (Tab and the arrow keys reach
    them; a click on a picture chooses it too), and a line about the one chosen."""

    labelled = True

    def build(self):
        self.var = tk.StringVar(self.frame)
        self.keys = [k for k, _label in self.pref.choices_for(self.tab.app) or []]
        # Follow the system only where the desktop says whether it's light or dark (always on Windows) - or when
        # it's what's chosen, so the choice made shows
        if T.FOLLOW_WINDOWS in self.keys and self.value() != T.FOLLOW_WINDOWS and not T.follows_system():
            self.keys.remove(T.FOLLOW_WINDOWS)
        self.grid = ttk.Frame(self.body, style="CardInner.TFrame")
        self.grid.grid(row=0, column=0, sticky="w")
        self.tiles = {}
        s = self.tab.s
        for key in self.keys:
            cell = ttk.Frame(self.grid, style="CardInner.TFrame")
            view = ChartView(cell, height=TILE_H, width=int(TILE_W * s), background="CARD", cursor="hand2")
            view.look_preview = key                    # (drawn in its own look's colours, not the one in use)
            view.grid(row=0, column=0, sticky="w")
            view.show(lambda p, key=key: draw_look(p, key, self._dark_look(), self.var.get() == key))
            view.bind("<Button-1>", lambda e, key=key: self._pick(key), add="+")
            rb = ttk.Radiobutton(cell, text=look_name(key), value=key, variable=self.var, style="Card.TRadiobutton",
                                 command=lambda key=key: self._pick(key))
            rb.grid(row=1, column=0, sticky="w", pady=(4, 0))
            self.tiles[key] = (cell, view, rb)
        self.columns = 0
        self.lay_out(len(self.keys))
        self.words = ttk.Label(self.body, text="", style="CardNote.TLabel", justify="left")
        self.words.grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.tab.wraps.append(self.words)

    def _dark_look(self) -> str:
        return prefs.get(self.tab.app, "dark_look") if "dark_look" in prefs.PREFS else T.DEFAULT_DARK_LOOK

    def lay_out(self, columns: int):
        columns = max(1, min(columns, len(self.keys)))
        if columns == self.columns:
            return
        self.columns = columns
        for n, key in enumerate(self.keys):
            cell = self.tiles[key][0]
            cell.grid(row=n // columns, column=n % columns, sticky="nw", padx=(0, TILE_GAP), pady=(0, 10))

    def fit(self, width: int):
        """As many pictures to a row as the card has room for."""
        tile = int((TILE_W + TILE_GAP) * self.tab.s)
        self.lay_out(max(width // max(tile, 1), 1))

    def rescale(self):
        """Settings > Text size: the pictures' width in the new scale (their height, and the drawing, follow as
        every chart's do)."""
        for _cell, view, _rb in self.tiles.values():
            view.configure(width=int(TILE_W * self.tab.s))

    def _pick(self, key: str):
        self.var.set(key)
        self.save(key)

    def show(self, value):
        self.var.set(value)
        self.redraw()

    def redraw(self):
        dark = T.system_dark() if self.var.get() == T.FOLLOW_WINDOWS else None
        self.words.configure(text=look_words(self.var.get(), self._dark_look(), dark))
        for _cell, view, _rb in self.tiles.values():
            view.redraw()


CONTROLS = {"bool": BoolControl, "choice": ChoiceControl, "number": NumberControl, "text": TextControl,
            "languages": TextControl, "folder": FolderControl, "looks": LooksControl}


def add_control(kind: str, builder):
    """The control for settings of a kind (or with control=kind): builder(tab, parent, pref) -> a Control."""
    CONTROLS[kind] = builder


def _styles(style):
    style.configure("Settings.Name.TLabel", background=T.CARD, foreground=T.INK, font=T.font(style, 9, "bold"))
    style.configure("Settings.Error.TLabel", background=T.CARD, foreground=T.BAD_TEXT)


# ---------------------------------------------------------------------------------------------------------
class Tab(BaseTab):
    title = "Settings"

    def __init__(self, app, notebook):
        super().__init__(app, notebook)
        self.s = T.scale(self.frame)
        self.controls: dict[str, Control] = {}
        self.cards: dict[str, Card] = {}
        self.wraps: list = []                  # labels that wrap to the cards' width
        T.add_styles(self.frame, _styles)
        self._build()

    # -- layout -----------------------------------------------------------------------------------------------
    def _build(self):
        f = self.frame
        f.columnconfigure(0, weight=1)
        f.rowconfigure(1, weight=1)
        head = ttk.Frame(f, style="Page.TFrame", padding=(PAD, 12, PAD, 6))
        head.grid(row=0, column=0, sticky="ew")
        head.columnconfigure(0, weight=1)
        ttk.Label(head, text="Settings", style="PageTitle.TLabel").grid(row=0, column=0, sticky="w")
        self.reset_all = ttk.Button(head, text="Reset all to defaults", command=self.reset_everything)
        self.reset_all.grid(row=0, column=1, rowspan=2, sticky="e")
        self.where = ttk.Label(head, text=self._where_words(), style="PageHint.TLabel")
        self.where.grid(row=1, column=0, sticky="w", pady=(2, 0))

        self.scroll = ScrollFrame(f, background="PAGE", style="Page.TFrame", follow_focus=True)
        self.scroll.grid(row=1, column=0, sticky="nsew")
        inner = self.scroll.inner
        inner.columnconfigure(0, weight=1)
        row = 0
        for section in prefs.sections():
            card = Card(inner, section.name, section.hint or None, padding=(14, 10))
            card.grid(row=row, column=0, sticky="ew", padx=PAD, pady=(GAP if row == 0 else 0, GAP))
            card.body.columnconfigure(0, weight=1)
            self.cards[section.name] = card
            r = 0
            for pref in prefs.shown(section.name):
                builder = CONTROLS.get(pref.control or pref.kind) or CONTROLS.get(pref.kind) or TextControl
                try:
                    control = builder(self, card.body, pref)
                except Exception:                      # one broken control mustn't take the tab down
                    import traceback
                    log = getattr(self.app, "_log", None)
                    if callable(log):
                        log(f"The setting '{pref.label}' couldn't be shown:\n{traceback.format_exc().strip()}")
                    continue
                control.frame.grid(row=r, column=0, sticky="ew", pady=(0 if r == 0 else 12, 0))
                self.controls[pref.key] = control
                r += 1
            if section.name == "Appearance":
                note = ttk.Label(card.body, text=NATIVE_DIALOGS, style="CardSmall.TLabel", justify="left")
                note.grid(row=r, column=0, sticky="w", pady=(12, 0))
                self.wraps.append(note)
                r += 1
            foot = ttk.Frame(card.body, style="CardInner.TFrame")
            foot.grid(row=r, column=0, sticky="e", pady=(10, 0))
            ttk.Button(foot, text="Reset this section", style="Small.TButton",
                       command=lambda name=section.name: self.reset_section(name)).pack(side="right")
            row += 1
        self._follow_windows_state()
        self.scroll.canvas.bind("<Configure>", lambda e: self._reflow(e.width), add="+")
        self._reflow(int(900 * self.s))

    def _where_words(self) -> str:
        try:
            from .. import gui
            where = gui.SETTINGS_FILE
        except Exception:                              # (the tab shown without the main window: tests)
            where = "settings.json"
        return f"Changes are saved as you make them, in {where}"

    def _reflow(self, width: int):
        """The help lines wrap to the cards' width, and the looks' pictures fill the rows they have room for."""
        if width < 50:
            return
        inside = width - 2 * PAD - int(40 * self.s)
        wrap = max(inside, 200)
        for label in self.wraps:
            try:
                if str(label.cget("wraplength")) != str(wrap):
                    label.configure(wraplength=wrap)
            except tk.TclError:
                pass
        looks = self.controls.get("look")
        if isinstance(looks, LooksControl):
            looks.fit(inside)

    def text_size_changed(self) -> bool:
        """Settings > Text size (chosen here, most likely): re-measured in place, so the page stays where it is."""
        self.s = T.scale(self.frame)
        looks = self.controls.get("look")
        if isinstance(looks, LooksControl):
            looks.rescale()
        width = self.scroll.canvas.winfo_width()
        self._reflow(width if width >= 50 else int(900 * self.s))
        return True

    # -- keeping up with the settings ---------------------------------------------------------------------------
    def preference_changed(self, key: str, value):
        control = self.controls.get(key)
        if control is not None:
            control.show(value)
        if key in ("look", "dark_look"):
            looks = self.controls.get("look")
            if isinstance(looks, LooksControl):
                looks.redraw()                         # (Follow Windows' picture shows the dark look chosen)
            self._follow_windows_state()

    def _follow_windows_state(self):
        """The dark look for Follow Windows (the system) only matters while following it - and isn't shown at all
        where that isn't offered (a desktop that doesn't say whether it's light or dark)."""
        dark = self.controls.get("dark_look")
        if isinstance(dark, ChoiceControl):
            dark.enable(prefs.get(self.app, "look") == T.FOLLOW_WINDOWS)
            looks = self.controls.get("look")
            if isinstance(looks, LooksControl) and T.FOLLOW_WINDOWS not in looks.keys:
                dark.frame.grid_remove()

    def navigate(self, section: str | None = None, **_kwargs):
        card = self.cards.get(section or "")
        if card is not None:
            self.scroll.see(card)

    # -- putting things back ------------------------------------------------------------------------------------
    def reset_section(self, name: str):
        # (No is the button Enter presses: a slip of the key mustn't undo a whole card)
        if not messagebox.askyesno(APP_NAME, f"Put the {name} settings back to their defaults?",
                                   parent=self.frame, default=messagebox.NO):
            return
        self._reset(prefs.reset(self.app, section=name), name)

    def reset_everything(self):
        if not messagebox.askyesno(APP_NAME, "Put every setting on this tab back to its default?\n\n"
                                             f"The look goes back to {T.LOOKS[T.DEFAULT_LOOK].name}.",
                                   parent=self.frame, default=messagebox.NO):
            return
        self._reset(prefs.reset(self.app), None)

    def _reset(self, changed: list[str], name: str | None):
        for key, control in self.controls.items():   # (the unchanged ones too: a half-typed box goes back)
            control.say("")
            control.show(control.value())
        what = f"the {name} settings" if name else "every setting"
        say = getattr(self.app, "set_status", None)
        if callable(say):
            say(f"Settings: {what} back to the defaults." if changed else f"Settings: {what} were already the "
                                                                          f"defaults.")
