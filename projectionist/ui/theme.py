"""The app's looks: one set of colour tokens and fonts for every screen and chart, in four looks - Light (the
original colouring), Graphite (the default), Projection Booth and Velvet - switched while the app runs.

Colours follow a validated, colour-blind-safe reference palette: categorical hues are used in this fixed order
(never cycled), magnitude is one hue light-to-dark, above/below-a-baseline is blue vs red with a grey middle, and
the status colours (good / warning / serious / critical) only ever mean status. Text is always an ink colour,
never a series colour. ui/palettes.py holds every look's tokens.

Using it
  T.INK, T.CARD, T.BLUE ...  the tokens of the look in use. Read a token when you draw or configure something -
                             never keep one in a module constant or a default argument, or that thing stays in the
                             look it was read in.
  T.live(build)              a dict worked out afresh whenever it's read, for tables of colours:
                             SEVERITY = T.live(lambda: {"fix": T.SERIOUS, "check": T.WARNING})
  T.tint(widget, background="CARD", ...)
                             a classic Tk widget's colours by token name (or a function giving the value): set now
                             and again whenever the look changes. T.tint_tag(widget, tag, ...) the same for a Text's
                             or a Treeview's tag. (ttk widgets take their colours from styles.)
  T.add_styles(widget, fn)   fn(style) configures a module's own ttk styles from the tokens: now, and again after
                             every change of look
  T.on_change(fn)            fn() after every change of look (ChartViews redraw themselves this way; the tabs get
                             BaseTab.theme_changed() from the main window)
  T.apply(root, look)        switch a window to a look: the tokens, the ttk styles, the classic widgets, the title bar
  T.use(look)                the tokens only, without Tk (PNG previews and tests)
  T.font(widget, 9, "bold")  a font for a style, a widget or a Text tag - never a tuple like (T.FAMILY, 9, "bold"):
                             it's a named Tk font, so Settings > Text size can re-measure it while the app runs
                             (text_size_changed)
Light uses Windows' own ("vista") ttk theme on Windows, as the app always has. Everywhere else - and in the dark
looks - it's ttk's "clam" with every widget class the app uses styled from the tokens (Light's describe what Windows
draws, so it looks the same), the notebook tabs drawn as small images (the dark looks: a 2-px marker on the
selected tab; Light: boxed tabs, as Windows draws them), and the classic Tk widgets coloured through the option
database. On Windows a dark look's title bar is dark too.

Fonts: the looks name Windows' fonts (Segoe UI, Consolas). Where those aren't installed (Linux, a Mac),
font_family() picks the nearest the computer has - Noto Sans, Cantarell, Ubuntu, DejaVu Sans... and a monospaced
one for the log.

Follow Windows (FOLLOW_WINDOWS) is 'Follow the system' away from Windows: the desktop's own light or dark setting,
read (never written) through the freedesktop portal or GNOME's gsettings on Linux, and the system's appearance on a
Mac. Where nothing answers, the choice isn't offered (follows_system).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import weakref
from collections.abc import Mapping

import tkinter as tk
from tkinter import ttk

from .. import prefs
from . import palettes
from .palettes import DEFAULT_DARK_LOOK, DEFAULT_LOOK, FOLLOW_WINDOWS, LOOKS, ORDER, DARK_LOOKS  # noqa: F401

WINDOWS = sys.platform == "win32"
NATIVE_LIGHT = WINDOWS      # Light in Windows' own controls (the "vista" theme): only Windows has them
FOLLOW_NAME = "Follow Windows" if WINDOWS else "Follow the system"      # (the look setting's extra choice)
SYSTEM_IS = "Windows' apps are" if WINDOWS else "your desktop is set to"  # ('... light', '... dark')

LOOK = "light"              # the look in use
DARK = False                # ...is a dark one
NATIVE = NATIVE_LIGHT       # ...uses Windows' own controls (Light, on Windows)
TOKENS: dict = {}           # every token of the look in use (the module's T.INK etc. are the same values)

# Where a failure to apply a look (a tab's styles, a listener) is reported: the main window sets this to its log.
report = None


def use(look: str) -> str:
    """Make `look` the one in use: its tokens become this module's (T.INK, T.SERIES, T.RAMP...). No Tk is
    involved. An unknown look is the default one. -> the look's key."""
    global LOOK, DARK, NATIVE, TOKENS
    key = look if look in LOOKS else DEFAULT_LOOK
    TOKENS = palettes.tokens(key)
    globals().update(TOKENS)
    LOOK, DARK = key, LOOKS[key].dark
    NATIVE = not DARK and NATIVE_LIGHT
    return key


# At import: the original colours, so previews and tests drawn without the window are as they always were. The main
# window switches to the chosen look (Graphite unless you've chosen) before it first shows.
use("light")


def tokens(look: str | None = None) -> dict:
    """A look's tokens (a copy) - the look in use by default."""
    return palettes.tokens(look or LOOK)


# ---------------------------------------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------------------------------------
def ramp(fraction: float, lo: int = 250, hi: int = 650) -> str:
    """A step of the blue ramp for a 0-1 magnitude (ordered marks: never lighter than step 250)."""
    steps = [k for k in TOKENS["RAMP"] if lo <= k <= hi]
    fraction = min(max(fraction, 0.0), 1.0)
    return TOKENS["RAMP"][steps[round(fraction * (len(steps) - 1))]]


def mix(color: str, other: str, amount: float) -> str:
    """Blend two #rrggbb colours (amount 0 = color, 1 = other)."""
    a = [int(color[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(other[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * amount):02x}" for x, y in zip(a, b))


def ink_on(fill: str) -> str:
    """Dark ink or white - whichever reads on a coloured fill (dark ink even in a dark look: INK is light there)."""
    r, g, b = (int(fill[i:i + 2], 16) / 255 for i in (1, 3, 5))
    lum = 0.2126 * r ** 2.2 + 0.7152 * g ** 2.2 + 0.0722 * b ** 2.2
    return TOKENS["INK_ON_LIGHT"] if lum > 0.35 else "#ffffff"


def luminance(color: str) -> float:
    """WCAG relative luminance of a #rrggbb colour."""
    def channel(v):
        v /= 255
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(a: str, b: str) -> float:
    """WCAG contrast ratio of two #rrggbb colours (1 to 21)."""
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def scale(widget) -> float:
    """Pixels per device-independent pixel (1.0 at 96 dpi, 1.5 at 144 dpi...)."""
    try:
        return max(widget.winfo_fpixels("1i") / 96.0, 1.0)
    except tk.TclError:
        return 1.0


class live(Mapping):                                   # noqa: N801  (reads like the function it stands for)
    """A dict worked out from the tokens whenever it's read, so it always has the look in use:
    SEVERITY_COLOR = T.live(lambda: {"fix": T.SERIOUS, "check": T.WARNING, "info": T.BLUE})."""

    __slots__ = ("_build",)

    def __init__(self, build):
        self._build = build

    def __getitem__(self, key):
        return self._build()[key]

    def __iter__(self):
        return iter(self._build())

    def __len__(self):
        return len(self._build())

    def __repr__(self):
        return f"live({self._build()!r})"


# ---------------------------------------------------------------------------------------------------------
# What changes with the look: classic widgets by token, a module's own ttk styles, listeners
# ---------------------------------------------------------------------------------------------------------
_TINTS: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()       # widget -> {option: spec}
_TAG_TINTS: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()   # widget -> {tag: {option: spec}}
_STYLE_HOOKS: list = []                                                 # fn(style), in the order added
_LISTENERS: list = []                                                   # callables, or weak refs to bound methods


def value(spec):
    """What a tint spec means now: a token's name -> its value in the look in use, a function -> what it gives,
    anything else as it is."""
    if callable(spec):
        return spec()
    if isinstance(spec, str) and spec.isupper() and spec in TOKENS:
        return TOKENS[spec]
    return spec


def token_for(color, among=("SURFACE", "PAGE", "CARD", "CHROME", "MENU_BG")) -> str | None:
    """The name of the token (of those in `among`) whose value in the look in use is `color`, else None - how a
    widget given T.PAGE's value knows to follow PAGE when the look changes."""
    if not isinstance(color, str):
        return None
    if color.isupper() and color in TOKENS:
        return color
    for name in among:
        if str(TOKENS.get(name, "")).lower() == color.lower():
            return name
    return None


def tint(widget, **options):
    """Colour a classic Tk widget by token: tint(canvas, background="SURFACE", highlightbackground="CARD"). Each
    value is a token's name, a function giving the value, or a plain value. Set now, and again whenever the look
    changes. -> the widget."""
    try:
        widget.configure(**{k: value(v) for k, v in options.items()})
    except tk.TclError:
        return widget
    _TINTS.setdefault(widget, {}).update(options)
    return widget


def tint_tag(widget, tag: str, **options):
    """tint() for a tag of a Text or a Treeview: tint_tag(text, "link", foreground="LINK")."""
    try:
        widget.tag_configure(tag, **{k: value(v) for k, v in options.items()})
    except tk.TclError:
        return widget
    _TAG_TINTS.setdefault(widget, {}).setdefault(tag, {}).update(options)
    return widget


def add_styles(widget, fn):
    """fn(style) configures ttk styles of a module's own from the tokens: run now (for widget's window), and
    again whenever the look changes. Pass a module-level function (it's kept for the life of the program)."""
    if fn not in _STYLE_HOOKS:
        _STYLE_HOOKS.append(fn)
    fn(ttk.Style(widget))
    return fn


def on_change(fn):
    """fn() after every change of look. A bound method is held weakly (it goes with its object). -> fn"""
    if hasattr(fn, "__self__") and hasattr(fn, "__func__"):
        _LISTENERS.append(weakref.WeakMethod(fn))
    else:
        _LISTENERS.append(fn)
    return fn


def _report(text: str):
    if report is not None:
        try:
            report(text)
            return
        except Exception:
            pass
    if sys.stderr is not None:
        print(text, file=sys.stderr)


def _retint():
    """Every tinted widget and tag in the look in use (the ones that still exist)."""
    for widget, options in list(_TINTS.items()):
        try:
            if widget.winfo_exists():
                widget.configure(**{k: value(v) for k, v in options.items()})
        except tk.TclError:
            pass
    for widget, tags in list(_TAG_TINTS.items()):
        try:
            if widget.winfo_exists():
                for tag, options in tags.items():
                    widget.tag_configure(tag, **{k: value(v) for k, v in options.items()})
        except tk.TclError:
            pass


def _notify():
    import traceback
    for entry in list(_LISTENERS):
        fn = entry() if isinstance(entry, weakref.WeakMethod) else entry
        if fn is None:
            _LISTENERS.remove(entry)
            continue
        try:
            fn()
        except Exception:
            _report(f"Part of the window couldn't change its look:\n{traceback.format_exc().strip()}")


# ---------------------------------------------------------------------------------------------------------
# Switching a window's look
# ---------------------------------------------------------------------------------------------------------
def apply(root, look: str | None = None):
    """Switch a window to a look (the one in use when look is None), live: the tokens, the ttk styles (a module's
    own too), the option database and the classic widgets already made, every tinted widget, the title bar - then
    the listeners (the charts redraw). -> the ttk Style."""
    if look is not None:
        use(look)
    style = apply_styles(root)
    _classic(root)
    _retint()
    title_bar(root)
    _notify()
    return style


# ---------------------------------------------------------------------------------------------------------
# Fonts. Every font the app gives a size to is a NAMED Tk font (font()): Tk works a font's pixels out once, when
# it's first used, and keeps it cached by its description while anything uses it - so a font given as a tuple,
# ("Segoe UI", 9, "bold"), would keep its old size after Settings > Text size changes Tk's scaling, even for
# widgets made afterwards. A named font is measured again in place (remeasure_fonts) and everything using it
# follows at once.
# ---------------------------------------------------------------------------------------------------------
STYLES = ("bold", "italic", "underline")

# What to use where a look's font isn't installed, nearest first: font_family() takes the first the computer has
SANS_FAMILIES = ("Segoe UI", "Noto Sans", "Cantarell", "Ubuntu", "DejaVu Sans", "Liberation Sans", "Helvetica Neue",
                 "Arial", "Helvetica", "FreeSans")
MONO_FAMILIES = ("Consolas", "DejaVu Sans Mono", "Noto Sans Mono", "Ubuntu Mono", "Liberation Mono", "Menlo",
                 "Monaco", "Courier New", "FreeMono")


def _root_of(where):
    master = where.master if isinstance(where, ttk.Style) else where
    return master._root()


def installed_families(where) -> dict:
    """{family, case-folded: its name} for the fonts Tk can use in where's window (asked once per window)."""
    root = _root_of(where)
    known = root.__dict__.get("_font_families")
    if known is None:
        try:
            names = root.tk.splitlist(root.tk.call("font", "families"))
        except tk.TclError:
            names = ()
        known = root.__dict__["_font_families"] = {str(n).casefold(): str(n) for n in names}
    return known


def font_family(where, wanted: str | None = None) -> str:
    """The font family to draw `wanted` (the look's FAMILY by default) in, in where's window: wanted itself when
    it's installed, else the first of SANS_FAMILIES the computer has - or of MONO_FAMILIES, for the look's MONO -
    else wanted as it is (Tk then picks something like it). Windows always has the looks' own fonts, so there
    it's always wanted."""
    wanted = wanted or TOKENS["FAMILY"]
    if WINDOWS:
        return wanted
    known = installed_families(where)
    if wanted.casefold() in known:
        return known[wanted.casefold()]
    mono = wanted == TOKENS.get("MONO") or wanted in MONO_FAMILIES
    for name in MONO_FAMILIES if mono else SANS_FAMILIES:
        if name.casefold() in known:
            return known[name.casefold()]
    return wanted


def font(where, size: int = 9, *style: str, family: str | None = None) -> str:
    """The app's font at `size` points - 'bold', 'italic' and 'underline' in style - for a style's or a widget's
    font= (or a Text tag's). `where`: a widget, or a ttk.Style, of the window it's for (fonts belong to one Tk
    interpreter). family: a family of its own (T.MONO for the log); by default the look's, T.FAMILY, which it
    follows when the look changes. -> the name of the Tk font, made the first time it's asked for."""
    root = _root_of(where)
    size = int(round(size))
    name = "App" + (family or "").replace(" ", "") + str(size) + "".join(s.title() for s in STYLES if s in style)
    fonts = root.__dict__.setdefault("_app_fonts", {})          # name -> its own family (None: the look's)
    if name not in fonts:
        options = ("-family", font_family(root, family), "-size", size,
                   "-weight", "bold" if "bold" in style else "normal",
                   "-slant", "italic" if "italic" in style else "roman", "-underline", int("underline" in style))
        try:
            root.tk.call("font", "create", name, *options)
        except tk.TclError:                                     # (there already: this window made it before)
            root.tk.call("font", "configure", name, *options)
        fonts[name] = family
    return name


def _font_of(style):
    """The style functions' shorthand: F(10, "bold") is font(style, 10, "bold")."""
    return lambda size, *how: font(style, size, *how)


def remeasure_fonts(root) -> int:
    """Tk's scaling (pixels per point) has changed - Settings > Text size: every named font given in points is
    measured again, in place, so the widgets, styles, text tags and chart items using it grow or shrink at once.
    (A size in pixels, given below 0, stays as it is.) -> how many fonts."""
    call, count = root.tk.call, 0
    for name in root.tk.splitlist(call("font", "names")):
        try:
            size = int(call("font", "configure", name, "-size"))
            if size > 0:
                call("font", "configure", name, "-size", size)      # (the same size: Tk measures it again)
                count += 1
        except (tk.TclError, ValueError):
            pass
    return count


def _fonts(root):
    """The Tk fonts the widgets use by default, and the app's own (font()), in the look's family (or the nearest
    one installed: font_family)."""
    root = root._root()
    family, call = font_family(root), root.tk.call

    def set_font(name, **options):
        try:
            if any(str(call("font", "configure", name, f"-{k}")) != str(v) for k, v in options.items()):
                call("font", "configure", name, *[x for k, v in options.items() for x in (f"-{k}", v)])
        except tk.TclError:
            pass
    set_font("TkDefaultFont", family=family, size=9)
    for name in ("TkTextFont", "TkMenuFont", "TkHeadingFont"):
        set_font(name, family=family)
    for name, own in list(root.__dict__.get("_app_fonts", {}).items()):
        if own is None:
            set_font(name, family=family)


ENTRY_CLASSES = ("TEntry", "TCombobox", "TSpinbox")


def text_size_changed(root):
    """Settings > Text size, once Tk's scaling has changed: every font measured again, then the styles again (the
    tables' row heights go by scale()). A ttk entry (and a combo box, a spin box) keeps its text laid out in the
    font as it was - drawn a pixel or two off, its cursor out of step - until its font is set: so it's set again,
    to the same font."""
    remeasure_fonts(root)
    apply_styles(root)
    call = root.tk.call
    for path in _windows(root):
        try:
            if call("winfo", "class", path) in ENTRY_CLASSES:
                call(path, "configure", "-font", call(path, "cget", "-font"))
        except tk.TclError:
            pass


def apply_styles(root) -> ttk.Style:
    """The ttk styles of the look in use, on root's interpreter: Windows' own theme for Light on Windows, "clam"
    styled from the tokens otherwise; then the app's own styles and every module's (add_styles)."""
    style = ttk.Style(root)
    quiet_theme_changes(root)
    try:
        _fonts(root)
    except (tk.TclError, RuntimeError):
        pass
    if NATIVE:
        _native_styles(style, root)
    else:
        _clam_styles(style, root)
    _app_styles(style, root)
    import traceback
    for fn in list(_STYLE_HOOKS):
        try:
            fn(style)
        except Exception:
            _report(f"Some styles couldn't change their look:\n{traceback.format_exc().strip()}")
    return style


# Any change to the styles has ttk tell every widget, once Tk is next idle (ttk::ThemeChanged). Tcl keeps one idle
# queue for all its windows, so when a window is closed before that happens, the next window to go idle runs it for
# the one that's gone - and Tk prints "can't invoke "event" command: application has been destroyed". There's
# nothing left to tell then: ThemeChanged is wrapped so that one case says nothing (any other error still shows).
_THEME_GUARD = """
if {[llength [info procs ::ttk::ThemeChanged]] && ![llength [info procs ::ttk::ThemeChangedUnguarded]]} {
    rename ::ttk::ThemeChanged ::ttk::ThemeChangedUnguarded
    proc ::ttk::ThemeChanged {} {
        if {[catch {::ttk::ThemeChangedUnguarded} message options]
                && ![string match "*application has been destroyed*" $message]} {
            return -options $options $message
        }
    }
}
"""


def quiet_theme_changes(root):
    """ttk's ThemeChanged, quiet when its window has gone (see _THEME_GUARD). Once per Tk interpreter."""
    root = root._root()
    if root.__dict__.get("_theme_guarded"):
        return
    root.__dict__["_theme_guarded"] = True
    try:
        root.tk.eval(_THEME_GUARD)
    except tk.TclError:
        pass


def _native_styles(style, root):
    """Light on Windows: Windows' own controls, as the app always had them."""
    if "vista" in style.theme_names():
        style.theme_use("vista")
    F = _font_of(style)
    style.configure("Accent.TButton", font=F(10, "bold"), padding=(18, 6))
    style.configure("Small.TButton", padding=(6, 1))
    style.configure("Treeview", rowheight=int(22 * scale(root)))
    style.configure("Treeview.Heading", font=F(9, "bold"))
    style.configure("TNotebook.Tab", padding=(14, 5), font=F(10))
    # A tab's own views (Watch Next's lists, Six Degrees' modes): smaller tabs on the page, so they don't read as a
    # second row of the main tabs
    style.configure("Inner.TNotebook", background=TOKENS["PAGE"])
    style.configure("Inner.TNotebook.Tab", padding=(12, 3), font=F(9))


def _app_styles(style, root):
    """The app's own styles (both kinds of look), from the tokens."""
    t, F = TOKENS, _font_of(style)
    edge = {} if NATIVE else dict(bordercolor=t["BORDER"], lightcolor=t["BORDER"], darkcolor=t["BORDER"])
    style.configure("Title.TLabel", font=F(15, "bold"), foreground=t["ACCENT"])
    style.configure("Heading.TLabel", font=F(12, "bold"), foreground=t["INK"])
    style.configure("Section.TLabel", font=F(10, "bold"), foreground=t["ACCENT"])
    style.configure("Hint.TLabel", foreground=t["HINT"])
    style.configure("Muted.TLabel", foreground=t["MUTED"])
    style.configure("Good.TLabel", foreground=t["GOOD_LABEL"])
    style.configure("Bad.TLabel", foreground=t["BAD_TEXT"])
    style.configure("Link.TLabel", foreground=t["LINK"])
    # Cards: panels on the page plane
    style.configure("Page.TFrame", background=t["PAGE"])
    style.configure("Card.TFrame", background=t["CARD"], relief="solid", borderwidth=1, **edge)
    style.configure("Card.TLabel", background=t["CARD"])
    style.configure("CardTitle.TLabel", background=t["CARD"], font=F(10, "bold"), foreground=t["INK"])
    style.configure("CardHint.TLabel", background=t["CARD"], foreground=t["MUTED"])
    style.configure("CardValue.TLabel", background=t["CARD"], font=F(18, "bold"), foreground=t["INK"])
    style.configure("Card.TRadiobutton", background=t["CARD"])
    style.configure("Card.TCheckbutton", background=t["CARD"])
    style.configure("CardInner.TFrame", background=t["CARD"], borderwidth=0, relief="flat")
    style.configure("CardNote.TLabel", background=t["CARD"], foreground=t["INK_2"])
    style.configure("CardField.TLabel", background=t["CARD"], foreground=t["INK_2"])
    style.configure("CardSmall.TLabel", background=t["CARD"], foreground=t["MUTED"], font=F(8))
    style.configure("CardLink.TLabel", background=t["CARD"], foreground=t["LINK"])
    style.configure("Page.TLabel", background=t["PAGE"])
    style.configure("PageHint.TLabel", background=t["PAGE"], foreground=t["HINT"])
    style.configure("PageTitle.TLabel", background=t["PAGE"], font=F(13, "bold"), foreground=t["ACCENT"])
    style.configure("PageLink.TLabel", background=t["PAGE"], foreground=t["LINK"])
    style.configure("Page.TCheckbutton", background=t["PAGE"])
    style.configure("Page.TRadiobutton", background=t["PAGE"])
    # the window's chrome: the Find bar and the status bar
    style.configure("Chrome.TFrame", background=t["CHROME"])
    style.configure("ChromeHint.TLabel", background=t["CHROME"], foreground=t["HINT"])
    style.configure("ChromeSection.TLabel", background=t["CHROME"], foreground=t["ACCENT"], font=F(10, "bold"))


def _clam_styles(style, root):
    """A dark look, or Light away from Windows: ttk's "clam", every widget class the app uses styled from the
    tokens (paddings chosen so the pages lay out as they do under Windows' own theme). Light's tokens describe what
    Windows draws - grey #f0f0f0 frames, grey buttons with a darker edge, white boxes, boxed tabs - so it looks as
    it does there."""
    t, F = TOKENS, _font_of(style)
    plain = t["PAGE"] if DARK else t["CHROME"]      # a frame's own background (Windows' grey, in Light)
    if style.theme_use() != "clam":
        style.theme_use("clam")
    style.configure(".", background=plain, foreground=t["INK"], bordercolor=t["BORDER"],
                    lightcolor=plain, darkcolor=plain, troughcolor=t["SCROLL_TROUGH"],
                    fieldbackground=t["ENTRY_BG"], selectbackground=t["ENTRY_SELECT_BG"],
                    selectforeground=t["ENTRY_SELECT_FG"], insertcolor=t["INSERT"], focuscolor=t["FOCUS"],
                    arrowcolor=t["INK_2"], font="TkDefaultFont")
    style.map(".", background=[], foreground=[("disabled", t["DISABLED_FG"])],
              selectbackground=[("!focus", t["SELECT"])], selectforeground=[("!focus", t["INK"])])
    style.configure("TFrame", background=plain)
    style.configure("TLabel", background=plain, foreground=t["INK"])

    # buttons
    style.configure("TButton", background=t["BUTTON_BG"], foreground=t["BUTTON_FG"], bordercolor=t["BUTTON_BORDER"],
                    lightcolor=t["BUTTON_BG"], darkcolor=t["BUTTON_BG"], relief="raised", padding=(8, 2),
                    focuscolor=t["FOCUS"], anchor="center")
    states = [("disabled", t["BUTTON_BG"]), ("pressed", t["BUTTON_PRESSED"]), ("active", t["BUTTON_ACTIVE"])]
    style.map("TButton", background=states, lightcolor=states, darkcolor=states,
              foreground=[("disabled", t["BUTTON_DISABLED_FG"])], bordercolor=[("focus", t["FOCUS"])])
    # (Light's main button is a plain one in bold, edge and all, as Windows draws it; a dark look's is filled)
    style.configure("Accent.TButton", font=F(10, "bold"), padding=(18, 7), background=t["ACCENT_BUTTON_BG"],
                    foreground=t["ACCENT_BUTTON_FG"],
                    bordercolor=t["ACCENT_BUTTON_BG"] if DARK else t["BUTTON_BORDER"],
                    lightcolor=t["ACCENT_BUTTON_BG"], darkcolor=t["ACCENT_BUTTON_BG"])
    states = [("disabled", t["BUTTON_BG"]), ("pressed", t["ACCENT_BUTTON_PRESSED"]),
              ("active", t["ACCENT_BUTTON_ACTIVE"])]
    style.map("Accent.TButton", background=states, lightcolor=states, darkcolor=states,
              bordercolor=[("disabled", t["BUTTON_BORDER"]), ("focus", t["FOCUS"])],
              foreground=[("disabled", t["BUTTON_DISABLED_FG"])])
    style.configure("Small.TButton", padding=(6, 2))
    # toggle buttons (Watch Next's "Show genres / countries..." and the critics' list)
    style.configure("Toolbutton", background=t["PAGE"], foreground=t["INK_2"], bordercolor=t["BORDER"],
                    lightcolor=t["PAGE"], darkcolor=t["PAGE"], relief="flat", padding=(8, 3))
    style.map("Toolbutton", relief=[("selected", "solid"), ("pressed", "solid")],
              background=[("selected", t["SELECT"]), ("pressed", t["SELECT"]), ("active", t["HOVER"])],
              lightcolor=[("selected", t["SELECT"]), ("active", t["HOVER"])],
              darkcolor=[("selected", t["SELECT"]), ("active", t["HOVER"])],
              bordercolor=[("selected", t["FOCUS"])],
              foreground=[("disabled", t["DISABLED_FG"]), ("selected", t["INK"]), ("active", t["INK"])])

    # check and radio buttons (away from Windows the tick box is drawn by the app: _check_elements)
    ring_on = t["CHECK_ON"] if t["CHECK_ON"] != t["CHECK_BG"] else t["CHECK_BORDER"]
    if not WINDOWS:
        _check_elements(style, root)
    for cls in ("TCheckbutton", "TRadiobutton"):
        style.configure(cls, background=plain, foreground=t["INK"], indicatorbackground=t["CHECK_BG"],
                        indicatorforeground=t["CHECK_MARK"], upperbordercolor=t["CHECK_BORDER"],
                        lowerbordercolor=t["CHECK_BORDER"], indicatormargin=(1, 1, 5, 1), indicatorsize=12,
                        focuscolor=t["FOCUS"])
        # (ticked but unavailable: a grey mark in the empty well - the mark's own colour would vanish in it)
        style.map(cls, background=[], foreground=[("disabled", t["DISABLED_FG"])],
                  indicatorbackground=[("disabled", t["CHECK_BG"]), ("pressed", t["CHECK_BG"]),
                                       ("selected", t["CHECK_ON"])],
                  indicatorforeground=[("disabled", t["DISABLED_FG"])],
                  upperbordercolor=[("focus", t["FOCUS"]), ("selected", ring_on)],
                  lowerbordercolor=[("focus", t["FOCUS"]), ("selected", ring_on)])

    # entries, combo boxes, spin boxes
    for cls in ("TEntry", "TCombobox", "TSpinbox"):
        style.configure(cls, fieldbackground=t["ENTRY_BG"], foreground=t["ENTRY_FG"], bordercolor=t["ENTRY_BORDER"],
                        lightcolor=t["ENTRY_BG"], darkcolor=t["ENTRY_BG"], insertcolor=t["INSERT"],
                        selectbackground=t["ENTRY_SELECT_BG"], selectforeground=t["ENTRY_SELECT_FG"],
                        arrowcolor=t["INK_2"], background=t["BUTTON_BG"],
                        # (a spin box's two arrows set its height under clam: without the row of padding it's as tall
                        # as Windows' own, 20 px, like the entries and combo boxes beside it)
                        padding=(4, 0) if cls == "TSpinbox" else (4, 1))
        style.map(cls,
                  fieldbackground=[("disabled", t["ENTRY_READONLY_BG"]),
                                   ("readonly", t["ENTRY_BG"] if cls == "TCombobox" else t["ENTRY_READONLY_BG"])],
                  foreground=[("disabled", t["ENTRY_DISABLED_FG"]), ("readonly", t["ENTRY_FG"])],
                  bordercolor=[("focus", t["FOCUS"]), ("hover", mix(t["ENTRY_BORDER"], t["INK"], .25))],
                  lightcolor=[("focus", t["FOCUS"])], darkcolor=[("focus", t["FOCUS"])],
                  background=[("pressed", t["BUTTON_PRESSED"]), ("active", t["BUTTON_ACTIVE"])],
                  arrowcolor=[("disabled", t["DISABLED_FG"])],
                  selectbackground=[("!focus", t["SELECT"])], selectforeground=[("!focus", t["INK"])])

    # notebooks: the tabs are image elements (see _tab_elements)
    style.configure("TNotebook", background=t["TAB_STRIP_BG"], bordercolor=t["TAB_BORDER"],
                    lightcolor=t["TAB_SELECTED_BG"], darkcolor=t["TAB_SELECTED_BG"], tabmargins=(4, 4, 4, 0))
    style.configure("TNotebook.Tab", padding=(14, 4), font=F(10), background=t["TAB_BG"], foreground=t["TAB_FG"],
                    bordercolor=t["TAB_BORDER"])
    style.map("TNotebook.Tab",
              background=[("selected", t["TAB_SELECTED_BG"]), ("active", t["TAB_HOVER_BG"])],
              foreground=[("selected", t["TAB_SELECTED_FG"]), ("active", t["INK"])],
              padding=[("selected", (14, 4))], expand=[("selected", (0, 2, 0, 1))])
    style.configure("Inner.TNotebook", background=t["PAGE"], bordercolor=t["BORDER"], lightcolor=t["PAGE"],
                    darkcolor=t["PAGE"], tabmargins=(0, 2, 0, 0))
    style.configure("Inner.TNotebook.Tab", padding=(12, 3), font=F(9), background=t["PAGE"],
                    foreground=t["INK_2"])
    style.map("Inner.TNotebook.Tab", padding=[("selected", (12, 3))], expand=[("selected", (0, 0, 0, 0))],
              background=[("selected", t["PAGE"])],
              foreground=[("selected", t["INK"]), ("active", t["INK"])])
    _tab_elements(style, root)

    # tables
    style.configure("Treeview", background=t["TREE_BG"], fieldbackground=t["TREE_BG"], foreground=t["TREE_FG"],
                    bordercolor=t["BORDER"], lightcolor=t["TREE_BG"], darkcolor=t["TREE_BG"],
                    rowheight=int(22 * scale(root)))
    style.map("Treeview", background=[("selected", t["TREE_SELECT_BG"])],
              foreground=[("selected", t["TREE_SELECT_FG"]), ("disabled", t["DISABLED_FG"])],
              bordercolor=[("focus", t["BORDER"])])
    style.configure("Treeview.Heading", font=F(9, "bold"), background=t["TREE_HEADING_BG"],
                    foreground=t["TREE_HEADING_FG"], bordercolor=t["BORDER"], lightcolor=t["TREE_HEADING_BG"],
                    darkcolor=t["TREE_HEADING_BG"], relief="flat", padding=(4, 3))
    style.map("Treeview.Heading", background=[("active", t["TREE_HEADING_ACTIVE"])],
              lightcolor=[("active", t["TREE_HEADING_ACTIVE"])], darkcolor=[("active", t["TREE_HEADING_ACTIVE"])])

    # scrollbars: a thumb in a trough, no arrow buttons (clam's arrows would take the thumb's colour)
    for orient, sticky in (("Vertical", "ns"), ("Horizontal", "ew")):
        style.layout(f"{orient}.TScrollbar", [(f"{orient}.Scrollbar.trough", {"sticky": sticky, "children": [
            (f"{orient}.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
    style.configure("TScrollbar", background=t["SCROLL_THUMB"], troughcolor=t["SCROLL_TROUGH"],
                    bordercolor=t["SCROLL_TROUGH"], lightcolor=t["SCROLL_THUMB"], darkcolor=t["SCROLL_THUMB"],
                    arrowcolor=t["SCROLL_ARROW"], gripcount=0, arrowsize=12)
    states = [("pressed", t["SCROLL_THUMB_ACTIVE"]), ("active", t["SCROLL_THUMB_ACTIVE"])]
    style.map("TScrollbar", background=states, lightcolor=states, darkcolor=states)

    # progress, separators, paned windows
    # (the track shows how much is left: a trough that stands off the page, in an edge like the boxes')
    style.configure("TProgressbar", troughcolor=t["PROGRESS_TROUGH"], background=t["PROGRESS_BAR"],
                    bordercolor=t["ENTRY_BORDER"], lightcolor=t["PROGRESS_BAR"], darkcolor=t["PROGRESS_BAR"])
    style.configure("TSeparator", background=t["SEPARATOR"])
    style.configure("TPanedwindow", background=t["PAGE"])
    style.configure("Sash", background=t["SASH"], bordercolor=t["SASH"], lightcolor=t["SASH"],
                    darkcolor=t["SASH"], gripcount=0, sashthickness=6)


# -- notebook tabs as image elements (clam's own tab can only draw a 1-px coloured edge). Each state's image is a
#    small 9-slice: TAB_SLICES pixels at each edge are kept, the middle stretches. The renderer the QA uses draws
#    the tabs from the same tab_specs(). -------------------------------------------------------------------------
TAB_W, TAB_H = 12, 12
TAB_SLICES = {"TNotebook.Tab": (1, 3, 1, 1), "Inner.TNotebook.Tab": (1, 1, 1, 3)}   # (not TAB_BORDER: a token)


def tab_specs(t: dict | None = None, boxed: bool | None = None) -> dict:
    """{style: {state: spec}} for a look's tokens (the look in use by default); spec = dict(fill, sides, marker,
    marker_w, marker_at). A dark look: the selected main tab takes the page's colour with a 2-px marker on top; the
    inner notebooks' selected tab is underlined. Light (boxed - a light page, by default): every tab in a 1-px box,
    the selected one white, as Windows draws them."""
    t = t or TOKENS
    if boxed is None:
        boxed = luminance(t["PAGE"]) > 0.5
    if boxed:
        edge = dict(sides=t["TAB_BORDER"], marker=t["TAB_BORDER"], marker_w=1, marker_at="top")
        box = {"normal": dict(fill=t["TAB_BG"], **edge), "active": dict(fill=t["TAB_HOVER_BG"], **edge),
               "selected": dict(fill=t["TAB_SELECTED_BG"], **edge)}
        return {"TNotebook.Tab": box, "Inner.TNotebook.Tab": dict(box)}
    main = {
        "normal": dict(fill=t["TAB_BG"], sides=None, marker=None),
        "active": dict(fill=t["TAB_HOVER_BG"], sides=None, marker=None),
        "selected": dict(fill=t["TAB_SELECTED_BG"], sides=t["TAB_BORDER"], marker=t["TAB_INDICATOR"], marker_w=2,
                         marker_at="top"),
    }
    hover = t["HOVER"] if contrast(t["HOVER"], t["PAGE"]) < 1.25 else mix(t["PAGE"], t["INK"], .05)
    inner = {
        "normal": dict(fill=t["PAGE"], sides=None, marker=None),
        "active": dict(fill=hover, sides=None, marker=None),
        "selected": dict(fill=t["PAGE"], sides=None, marker=t["TAB_INDICATOR"], marker_w=2, marker_at="bottom"),
    }
    return {"TNotebook.Tab": main, "Inner.TNotebook.Tab": inner}


def tab_pixels(spec: dict, w: int = TAB_W, h: int = TAB_H) -> list[list[str]]:
    """A tab spec as rows of '#rrggbb' - what its image holds."""
    rows = [[spec["fill"]] * w for _ in range(h)]
    top = 0
    if spec.get("marker"):
        mw = spec.get("marker_w", 2)
        at_top = spec.get("marker_at", "top") == "top"
        for y in (range(0, mw) if at_top else range(h - mw, h)):
            rows[y] = [spec["marker"]] * w
        top = mw if at_top else 0
    if spec.get("sides"):
        for y in range(top, h):
            rows[y][0] = rows[y][w - 1] = spec["sides"]
    return rows


def _tab_elements(style, root):
    """The tabs' image elements under clam. Made once per window; a later dark look repaints their images in place
    (an element can't be made twice)."""
    images = root.__dict__.setdefault("_look_tab_images", {})
    for sty, states in tab_specs().items():
        name = "Look" + sty.replace(".", "")                  # LookTNotebookTab, LookInnerTNotebookTab
        imgs = {}
        for state, spec in states.items():
            img = images.get((sty, state))
            if img is None:
                img = images[(sty, state)] = tk.PhotoImage(master=root, width=TAB_W, height=TAB_H)
            img.put(" ".join("{" + " ".join(r) + "}" for r in tab_pixels(spec)), to=(0, 0))
            imgs[state] = img
        try:
            style.element_create(name, "image", imgs["normal"], ("selected", imgs["selected"]),
                                 ("active", imgs["active"]), border=TAB_SLICES[sty], sticky="nsew")
        except tk.TclError:
            pass                                              # (made before: its images were just repainted)
        style.layout(sty, [(name, {"sticky": "nsew", "children": [
            ("Notebook.padding", {"side": "top", "sticky": "nsew", "children": [
                ("Notebook.focus", {"side": "top", "sticky": "nsew", "children": [
                    ("Notebook.label", {"side": "top", "sticky": ""})]})]})]})])


# -- the tick box, away from Windows: clam's own is an X in the box in the Tk most Linux systems have (8.6.12 and
#    before), which reads more like "no" than "yes". So there the box is drawn here, with a tick like Windows' own,
#    in the look's colours and the display's scale - the same on every Linux. -----------------------------------
CHECK_SIZE = 13             # px at 96 dpi: the box (clam's is 12, and its edge)
CHECK_GAP = 5               # ...and the room between it and the words


def check_specs(t: dict | None = None) -> dict:
    """{state: (fill, edge, tick or None)} for a look's tokens - as clam's own box is coloured (_clam_styles)."""
    t = t or TOKENS
    ring_on = t["CHECK_ON"] if t["CHECK_ON"] != t["CHECK_BG"] else t["CHECK_BORDER"]
    return {"normal": (t["CHECK_BG"], t["CHECK_BORDER"], None),
            "selected": (t["CHECK_ON"], ring_on, t["CHECK_MARK"]),
            "focus": (t["CHECK_BG"], t["FOCUS"], None),
            "focus selected": (t["CHECK_ON"], t["FOCUS"], t["CHECK_MARK"]),
            "disabled": (t["CHECK_BG"], t["CHECK_BORDER"], None),
            "disabled selected": (t["CHECK_BG"], t["CHECK_BORDER"], t["DISABLED_FG"])}


def check_pixels(fill: str, edge: str, tick: str | None, size: int) -> list[list[str]]:
    """The tick box as rows of '#rrggbb': a 1-px edge (2 from 2x), the fill, and a tick drawn smooth."""
    edge_w = max(1, round(size / CHECK_SIZE))
    rows = [[fill] * size for _ in range(size)]
    for y in range(size):
        for x in range(size):
            if x < edge_w or y < edge_w or x >= size - edge_w or y >= size - edge_w:
                rows[y][x] = edge
    if tick:
        points = [(0.24 * size, 0.52 * size), (0.43 * size, 0.71 * size), (0.77 * size, 0.30 * size)]
        half = max(0.8, size * 0.075)                        # (half the stroke)

        def distance(px, py, a, b):
            (ax, ay), (bx, by) = a, b
            dx, dy = bx - ax, by - ay
            k = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
            return ((px - ax - k * dx) ** 2 + (py - ay - k * dy) ** 2) ** 0.5
        for y in range(edge_w, size - edge_w):
            for x in range(edge_w, size - edge_w):
                d = min(distance(x + 0.5, y + 0.5, points[0], points[1]),
                        distance(x + 0.5, y + 0.5, points[1], points[2]))
                cover = max(0.0, min(1.0, half + 0.5 - d))
                if cover > 0:
                    rows[y][x] = mix(fill, tick, cover)
    return rows


def _check_elements(style, root):
    """The tick box as an image element for TCheckbutton (and so every Card./Page. checkbutton style), made once per
    window; a later look or text size repaints its images in place."""
    images = root.__dict__.setdefault("_look_check_images", {})
    size = max(CHECK_SIZE, round(CHECK_SIZE * scale(root)))
    gap = round(CHECK_GAP * scale(root))
    made = {}
    for state, (fill, edge, tick) in check_specs().items():
        img = images.get(state)
        if img is None:
            img = images[state] = tk.PhotoImage(master=root, width=size + gap, height=size)
        elif int(img.cget("width")) != size + gap:
            img.blank()
            img.configure(width=size + gap, height=size)
        img.put(" ".join("{" + " ".join(r) + "}" for r in check_pixels(fill, edge, tick, size)), to=(0, 0))
        made[state] = img                                     # (the gap on the right stays see-through)
    try:
        style.element_create("LookCheck.indicator", "image", made["normal"],
                             ("disabled", "selected", made["disabled selected"]), ("disabled", made["disabled"]),
                             ("focus", "selected", made["focus selected"]), ("selected", made["selected"]),
                             ("focus", made["focus"]), sticky="")
    except tk.TclError:
        pass                                                  # (made before: its images were just repainted)
    style.layout("TCheckbutton", [("Checkbutton.padding", {"sticky": "nswe", "children": [
        ("LookCheck.indicator", {"side": "left", "sticky": ""}),
        ("Checkbutton.focus", {"side": "left", "sticky": "w", "children": [
            ("Checkbutton.label", {"sticky": "nswe"})]})]})])


# ---------------------------------------------------------------------------------------------------------
# Classic Tk widgets (Text, Listbox, Menu, Canvas...): the option database gives the ones made from now on the
# look's colours; on a change of look, those already made that still have the old look's colour for an option get
# the new one (ones given a colour of their own keep it - and tint() looks after those). Light's are Tk's own
# defaults (Windows' system colours).
# ---------------------------------------------------------------------------------------------------------
CLASSIC = {
    "Text": {"background": "CARD", "foreground": "INK", "insertbackground": "INSERT",
             "selectbackground": "ENTRY_SELECT_BG", "selectforeground": "ENTRY_SELECT_FG",
             "inactiveselectbackground": "SELECT", "highlightbackground": "BORDER", "highlightcolor": "FOCUS"},
    "Listbox": {"background": "MENU_BG", "foreground": "MENU_FG", "selectbackground": "LIST_SELECT_BG",
                "selectforeground": "LIST_SELECT_FG", "highlightbackground": "MENU_BORDER",
                "highlightcolor": "FOCUS", "disabledforeground": "DISABLED_FG"},
    "Menu": {"background": "MENU_BG", "foreground": "MENU_FG", "activebackground": "MENU_ACTIVE_BG",
             "activeforeground": "MENU_ACTIVE_FG", "disabledforeground": "MENU_DISABLED_FG",
             "selectcolor": "MENU_FG", "relief": "flat", "activeborderwidth": 0},
    "Toplevel": {"background": "PAGE"},
    "Frame": {"background": "PAGE"},
    "Label": {"background": "PAGE", "foreground": "INK"},
    "Canvas": {"background": "PAGE", "highlightbackground": "PAGE"},
    "Entry": {"background": "ENTRY_BG", "foreground": "ENTRY_FG", "insertbackground": "INSERT",
              "selectbackground": "ENTRY_SELECT_BG", "selectforeground": "ENTRY_SELECT_FG"},
    "Button": {"background": "BUTTON_BG", "foreground": "BUTTON_FG", "activebackground": "BUTTON_ACTIVE",
               "activeforeground": "BUTTON_FG"},
    "Scrollbar": {"background": "SCROLL_THUMB", "troughcolor": "SCROLL_TROUGH"},
}
_DB_NAMES = {"insertbackground": "insertBackground", "selectbackground": "selectBackground",
             "selectforeground": "selectForeground", "inactiveselectbackground": "inactiveSelectBackground",
             "highlightbackground": "highlightBackground", "highlightcolor": "highlightColor",
             "disabledforeground": "disabledForeground", "activebackground": "activeBackground",
             "activeforeground": "activeForeground", "selectcolor": "selectColor", "troughcolor": "troughColor",
             "activeborderwidth": "activeBorderWidth"}
_PROBES = {"Text": tk.Text, "Listbox": tk.Listbox, "Menu": tk.Menu, "Toplevel": tk.Frame, "Frame": tk.Frame,
           "Label": tk.Label, "Canvas": tk.Canvas, "Entry": tk.Entry, "Button": tk.Button, "Scrollbar": tk.Scrollbar}


def _tk_defaults(root) -> dict:
    """Tk's own defaults for CLASSIC's options, asked of a throwaway widget of each class (never shown)."""
    cached = root.__dict__.get("_look_tk_defaults")
    if cached is not None:
        return cached
    out = {}
    for cls, options in CLASSIC.items():
        try:
            probe = _PROBES[cls](root)
        except tk.TclError:
            continue
        try:
            out[cls] = {opt: probe.configure(opt)[3] for opt in options}
        except (tk.TclError, TypeError, IndexError):
            pass
        finally:
            probe.destroy()
    root.__dict__["_look_tk_defaults"] = out
    return out


def classic_colors(root, cls: str) -> dict:
    """A classic widget class's options in the look in use: {option: value}."""
    if NATIVE:
        return dict(_tk_defaults(root).get(cls, {}))
    return {opt: value(spec) for opt, spec in CLASSIC.get(cls, {}).items()}


def restyle(widget):
    """Give a classic widget made earlier (a menu kept for next time, say) the look in use for its class."""
    try:
        widget.configure(**classic_colors(widget._root(), widget.winfo_class()))
    except tk.TclError:
        pass
    return widget


def _windows(root) -> list[str]:
    """Every Tk window under root - Tcl's own too (a combo box's drop-down list)."""
    out, todo = [], [str(root)]
    while todo:
        path = todo.pop()
        try:
            kids = root.tk.splitlist(root.tk.call("winfo", "children", path))
        except tk.TclError:
            continue
        out.extend(kids)
        todo.extend(kids)
    return out


def _same(a, b) -> bool:
    return str(a).lower() == str(b).lower()


def _classic(root):
    new = {cls: classic_colors(root, cls) for cls in CLASSIC}
    old = root.__dict__.get("_look_classic")
    if not WINDOWS:
        # X11's Tk rings a text box with a 1-px highlight Windows' doesn't have (the Export tab's log): none, as
        # on Windows. (A widget given a highlight of its own keeps it.)
        root.option_add("*Text.highlightThickness", 0, "widgetDefault")
    for cls, options in new.items():
        for opt, val in options.items():
            name = _DB_NAMES.get(opt, opt)
            root.option_add(f"*{cls}.{name}", val)
            if cls == "Listbox":                                  # (a combo box's drop-down list)
                root.option_add(f"*TCombobox*Listbox.{name}", val)
    if old:
        call = root.tk.call
        for path in _windows(root):
            try:
                cls = call("winfo", "class", path)
            except tk.TclError:
                continue
            options, was = new.get(cls), old.get(cls)
            if not options or not was:
                continue
            for opt, val in options.items():
                try:
                    if _same(call(path, "cget", "-" + opt), was.get(opt)):
                        call(path, "configure", "-" + opt, val)
                except tk.TclError:
                    pass
    root.__dict__["_look_classic"] = new
    try:                                                          # the window's own background
        root.configure(background=TOKENS["CHROME"] if not NATIVE else
                       _tk_defaults(root).get("Frame", {}).get("background", "SystemButtonFace"))
    except tk.TclError:
        pass


# ---------------------------------------------------------------------------------------------------------
# Windows: the title bar, and whether Windows' apps are dark (for Follow Windows)
# ---------------------------------------------------------------------------------------------------------
def _colorref(color: str) -> int:
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    return r | (g << 8) | (b << 16)


def title_bar(window, dark: bool | None = None) -> bool:
    """One of the app's windows' title bar in the look: dark while a dark look is in use (on Windows 11 in the
    look's own title colours), Windows' own light one otherwise. -> whether Windows took it. Never fails, and never
    changes any Windows setting: only this window's own title bar."""
    if dark is None:
        dark = DARK
    if sys.platform != "win32" or not hasattr(window, "wm_frame"):
        return False
    try:
        import ctypes
        from ctypes import wintypes
        hwnd = int(str(window.wm_frame()), 16)
        if not hwnd:
            return False
        dwm = ctypes.WinDLL("dwmapi")
        dwm.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        dwm.DwmSetWindowAttribute.restype = ctypes.c_long

        def put(attribute, number):
            v = ctypes.c_uint(number)
            return dwm.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(v), ctypes.sizeof(v)) == 0

        ok = put(20, 1 if dark else 0) or put(19, 1 if dark else 0)   # DWMWA_USE_IMMERSIVE_DARK_MODE (19: older 10)
        # Windows 11: the look's own caption and title text colours; 0xFFFFFFFF puts Windows' own back
        put(35, _colorref(TOKENS["TITLE_BG"]) if dark else 0xFFFFFFFF)          # DWMWA_CAPTION_COLOR
        put(36, _colorref(TOKENS["TITLE_FG"]) if dark else 0xFFFFFFFF)          # DWMWA_TEXT_COLOR
        if window.winfo_viewable():                                  # repaint the frame of a window on screen
            user32 = ctypes.WinDLL("user32")
            user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND] + [ctypes.c_int] * 4 + [wintypes.UINT]
            user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020 | 0x0200)
        return ok
    except Exception:
        return False


def windows_apps_dark() -> bool | None:
    """Whether Windows is set to dark for apps (Settings > Personalisation > Colours), read from the registry -
    read only. None when it can't be told (not Windows, or no such setting)."""
    try:
        import winreg
    except ImportError:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            light, _kind = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return int(light) == 0
    except (OSError, ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------------------------------------
# Elsewhere: whether the desktop is set to dark (for Follow the system) - read only, never changed
# ---------------------------------------------------------------------------------------------------------
ASK_SECONDS = 1.0           # the longest a desktop setting is waited for
_SILENT: set = set()        # the ways of asking that didn't answer: not tried again while the app runs


def _ask(command: list[str]) -> tuple[int, str, str] | None:
    """Run a small command that reads a setting -> (exit code, what it printed, its errors); None when it isn't
    installed or doesn't answer within ASK_SECONDS."""
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=ASK_SECONDS,
                              stdin=subprocess.DEVNULL, env=dict(os.environ, LC_ALL="C"))
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return done.returncode, done.stdout or "", done.stderr or ""


def _portal_dark() -> bool | None:
    """The freedesktop portal's colour scheme (GNOME, KDE, Xfce and others that run xdg-desktop-portal): 1 prefer
    dark, 2 prefer light, 0 no preference (light)."""
    got = _ask(["gdbus", "call", "--session", "--timeout", str(max(int(ASK_SECONDS), 1)),
                "--dest", "org.freedesktop.portal.Desktop", "--object-path", "/org/freedesktop/portal/desktop",
                "--method", "org.freedesktop.portal.Settings.Read", "org.freedesktop.appearance", "color-scheme"])
    if got is None:
        return None
    found = re.search(r"uint32\s+(\d+)", got[1])        # '(<<uint32 1>>,)'
    if got[0] != 0 or found is None:
        return None
    return int(found.group(1)) == 1


def _gsettings_dark() -> bool | None:
    """GNOME's own setting (Settings > Appearance), for a desktop without the portal's."""
    got = _ask(["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"])
    # (with no settings store to read, gsettings makes one up in memory and says so: that's no answer)
    if got is None or got[0] != 0 or "memory" in got[2].lower():
        return None
    value = got[1].strip().strip("'\"").lower()
    if value not in ("default", "prefer-dark", "prefer-light"):
        return None
    return value == "prefer-dark"


def _mac_dark() -> bool | None:
    """A Mac's appearance: AppleInterfaceStyle is Dark in dark mode, and isn't set at all in light mode."""
    got = _ask(["defaults", "read", "-g", "AppleInterfaceStyle"])
    if got is None:
        return None
    if got[0] == 0:
        return got[1].strip().lower() == "dark"
    return False if "does not exist" in got[2] else None


def session_bus() -> bool:
    """Whether the desktop session's message bus is there to ask (the address in DBUS_SESSION_BUS_ADDRESS, or the
    usual one, $XDG_RUNTIME_DIR/bus). Without it - the app started over ssh -X, or on a bare X server - there's no
    desktop to ask, and gdbus or gsettings would start a bus of their own (dbus-launch), which outlives the app."""
    if os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        return True
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    return bool(runtime) and os.path.exists(os.path.join(runtime, "bus"))


def desktop_dark() -> bool | None:
    """Whether a Linux (or other Unix) desktop is set to dark: the portal first, then gsettings. Each is asked with
    a short timeout, and one that doesn't answer isn't asked again. None when neither answers - or there's no
    desktop session's bus to ask them through (session_bus)."""
    if not session_bus():
        return None
    for name, ask in (("portal", _portal_dark), ("gsettings", _gsettings_dark)):
        if name in _SILENT:
            continue
        answer = ask()
        if answer is not None:
            return answer
        _SILENT.add(name)
    return None


def system_dark() -> bool | None:
    """Whether the system is set to dark - Windows' apps, a Mac's appearance, a Linux desktop's colour scheme.
    Read only. None when it can't be told."""
    if WINDOWS:
        return windows_apps_dark()
    if sys.platform == "darwin":
        return None if "mac" in _SILENT else _remember_silence("mac", _mac_dark())
    return desktop_dark()


def _remember_silence(name: str, answer):
    if answer is None:
        _SILENT.add(name)
    return answer


def follows_system() -> bool:
    """Whether Follow Windows / Follow the system is offered: always on Windows (as it always was); elsewhere only
    when the desktop says whether it's light or dark."""
    return WINDOWS or system_dark() is not None


def resolve(choice: str, dark_choice: str = DEFAULT_DARK_LOOK, windows_dark: bool | None = None) -> str:
    """The look to show for the setting: a look's key as it is; Follow Windows (the system) -> Light while it's
    light (or it can't be told), else the chosen dark look."""
    if choice == FOLLOW_WINDOWS:
        if not windows_dark:
            return "light"
        return dark_choice if dark_choice in DARK_LOOKS else DEFAULT_DARK_LOOK
    return choice if choice in LOOKS else DEFAULT_LOOK


def chosen_look(source) -> str:
    """The look the settings ask for (source: the app or its settings dict), the system asked when it's to
    follow."""
    choice = prefs.get(source, "look")
    dark = system_dark() if choice == FOLLOW_WINDOWS else None
    return resolve(choice, prefs.get(source, "dark_look"), dark)


# -- the settings --------------------------------------------------------------------------------------------------
def _look_changed(app, _value):
    apply_look = getattr(app, "apply_look", None)
    if callable(apply_look):
        apply_look()


prefs.section("Appearance", order=0,
              hint="How the window looks. A new look or text size shows at once, everywhere in the window.")
prefs.define("look", "Appearance", "Look", kind="choice", control="looks", default=DEFAULT_LOOK,
             choices=[(k, LOOKS[k].name) for k in ORDER] + [(FOLLOW_WINDOWS, FOLLOW_NAME)],
             help="Graphite unless you choose another: click a picture, or the button under it.",
             apply=_look_changed)
prefs.define("dark_look", "Appearance", f"Dark look for {FOLLOW_NAME}", kind="choice", default=DEFAULT_DARK_LOOK,
             choices=[(k, LOOKS[k].name) for k in DARK_LOOKS],
             help=f"The look {FOLLOW_NAME} uses while {SYSTEM_IS} dark.", apply=_look_changed)
