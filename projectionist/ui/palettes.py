"""The app's four looks, as data: every colour token of every look. theme.py turns a look into the window's
colours and styles; nothing else should need to read this module.

  Light             the app's original colouring (a warm off-white page, white cards, navy titles, Windows' own
                    buttons and boxes), with a few fixes all four looks share: the credits timeline's film span is a
                    ramp step (it was an 18% wash hardly darker than the chart), the waterfall's total has its own
                    colour (it borrowed the titles' navy), the Find list's selected line keeps one ink, the column
                    charts' and the waterfall's gridlines and baseline show (each column's hover area used to be
                    drawn over them), and the Library Doctor's chosen tile writes its small print in INK_2 inside a
                    RAMP[350] ring. Notes that named a light colour ('darker means more') now say 'stronger', which
                    holds in every look.
  Graphite          neutral charcoal, layered like Windows 11's own dark mode - THE DEFAULT
  Projection Booth  the app's navy as the window's chrome over a deep blue-black page
  Velvet            a darkened theatre: warm near-black, walnut cards, cream text, brass-gold titles

The three dark looks share one set of data colours (the documented dark series, ramp, diverging and status steps);
only the chrome, surfaces, text and controls differ. Each dark look was checked for contrast (text 4.5:1, control
edges and marks 3:1 - the data grey BASELINE on the charts' surface and the cards too - selection 3:1 against the
rows, MUTED captions 4.5:1 on a hover wash) and for surfaces that read as separate layers - the same gate runs in
tests/test_themes.py. Light keeps today's values (its controls are Windows' own), so its widget tokens
describe what Windows draws: they're used for the few things the app draws itself (the Export log, tooltips, the
Find list) and for the previews on the Settings tab.

Every look defines every token (a test checks it). VERDICT_COLORS and the named series slots (BLUE, ORANGE...) are
worked out from the tokens by tokens().
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field

FOLLOW_WINDOWS = "windows"          # the look setting's extra choice: Light, or a dark look while Windows is dark
DEFAULT_LOOK = "graphite"           # for anyone who hasn't chosen
DEFAULT_DARK_LOOK = "graphite"      # Follow Windows' dark look, unless another is chosen
SLOT_NAMES = ("BLUE", "ORANGE", "AQUA", "YELLOW", "MAGENTA", "GREEN", "VIOLET", "RED")
VERDICT_ICONS = {"Yes": "✔", "Likely": "✔", "Maybe": "?", "None found": "–", "": "·"}


@dataclass(frozen=True)
class Look:
    key: str
    name: str
    dark: bool
    blurb: str                      # one plain sentence for the Settings tab
    tokens: dict = field(repr=False)


# -- Light: the original colouring -------------------------------------------------------------------------------
LIGHT = {
    # surfaces and ink
    "CHROME": "#f0f0f0",            # Windows' own grey: the Find bar, the tab strip, the status bar
    "PAGE": "#f9f9f7",              # the plane behind cards
    "SURFACE": "#fcfcfb",           # chart surface
    "CARD": "#ffffff",
    "INK": "#0b0b0b",               # primary text
    "INK_2": "#52514e",             # secondary text
    "MUTED": "#898781",             # axis labels, captions
    "HINT": "#5f6b7a",              # hint lines under headings
    "GRID": "#e1e0d9",              # hairline gridlines
    "BASELINE": "#c3c2b7",          # axis / baseline, de-emphasised marks
    "BORDER": "#e6e5df",            # card outline
    "ACCENT": "#1f3a5f",            # the app's navy (titles)
    "HOVER": "#eef3fb",             # row / mark hover wash
    "SELECT": "#dbe7f8",
    # data: categorical slots (in this order only), the blue ramp (light -> dark), diverging, status
    "SERIES": ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"],
    "RAMP": {100: "#cde2fb", 150: "#b7d3f6", 200: "#9ec5f4", 250: "#86b6ef", 300: "#6da7ec", 350: "#5598e7",
             400: "#3987e5", 450: "#2a78d6", 500: "#256abf", 550: "#1c5cab", 600: "#184f95", 650: "#104281",
             700: "#0d366b"},
    "POSITIVE": "#2a78d6",
    "NEGATIVE": "#e34948",
    "NEUTRAL": "#f0efec",
    "GOOD": "#0ca30c",
    "WARNING": "#fab219",
    "SERIOUS": "#ec835a",
    "CRITICAL": "#d03b3b",
    "GOOD_TEXT": "#006300",
    "GOOD_LABEL": "#1e7b34",        # 'OK' lines (the Export tab's database line)
    "BAD_TEXT": "#b3261e",          # error lines
    # data jobs that used to borrow another token
    "HEAT_ZERO": "#f0efec",         # the heatmap's and calendar's empty cell (was NEUTRAL)
    "FILM_SPAN": "#86b6ef",         # the credits timeline's film span: ramp step 250 (was an 18% wash of BLUE)
    "TOTAL": "#1f3a5f",             # the waterfall's total bar (was ACCENT)
    "WASH_BASE": "#ffffff",         # what faded marks fade toward
    "EMPTY_CELL": "#ffffff",        # the calendar's days outside the history
    "ON_FILL": "#ffffff",           # text on a strong fill (the Six Degrees chain's end pills)
    "INK_ON_LIGHT": "#0b0b0b",      # ink_on(): text on a light fill
    "FAMILY": "Segoe UI",
    "MONO": "Consolas",
    # controls (Windows draws Light's own: these say what it draws)
    "LINK": "#2a78d6",
    "FOCUS": "#0078d7",
    "DISABLED_FG": "#6d6d6d",
    "ENTRY_BG": "#ffffff",
    "ENTRY_FG": "#000000",
    "ENTRY_BORDER": "#7a7a7a",
    "ENTRY_READONLY_BG": "#f0f0f0",
    "ENTRY_DISABLED_FG": "#6d6d6d",
    "ENTRY_SELECT_BG": "#0078d7",
    "ENTRY_SELECT_FG": "#ffffff",
    "INSERT": "#000000",
    "BUTTON_BG": "#e1e1e1",
    "BUTTON_FG": "#000000",
    "BUTTON_ACTIVE": "#e5f1fb",
    "BUTTON_PRESSED": "#cce4f7",
    "BUTTON_BORDER": "#adadad",
    "BUTTON_DISABLED_FG": "#6d6d6d",
    "ACCENT_BUTTON_BG": "#e1e1e1",
    "ACCENT_BUTTON_FG": "#000000",
    "ACCENT_BUTTON_ACTIVE": "#e5f1fb",
    "ACCENT_BUTTON_PRESSED": "#cce4f7",
    "TAB_STRIP_BG": "#f0f0f0",
    "TAB_BG": "#f0f0f0",
    "TAB_FG": "#000000",
    "TAB_HOVER_BG": "#d8eaf9",
    "TAB_SELECTED_BG": "#ffffff",
    "TAB_SELECTED_FG": "#000000",
    "TAB_BORDER": "#d9d9d9",
    "TAB_INDICATOR": "#ffffff",     # (Windows' tabs have no marker)
    "TREE_BG": "#ffffff",
    "TREE_FG": "#000000",
    "TREE_ALT_BG": "#f6f7f9",       # every other row of a table
    "TREE_SELECT_BG": "#0078d7",
    "TREE_SELECT_FG": "#ffffff",
    "TREE_HEADING_BG": "#ffffff",
    "TREE_HEADING_FG": "#000000",
    "TREE_HEADING_ACTIVE": "#d9ebf9",
    "LIST_SELECT_BG": "#dbe7f8",    # the Find list's and search boxes' selected line
    "LIST_SELECT_FG": "#0b0b0b",
    "SCROLL_TROUGH": "#f0f0f0",
    "SCROLL_THUMB": "#c2c2c2",
    "SCROLL_THUMB_ACTIVE": "#a8a8a8",
    "SCROLL_ARROW": "#606060",
    "CHECK_BG": "#ffffff",
    "CHECK_BORDER": "#333333",
    "CHECK_ON": "#ffffff",
    "CHECK_MARK": "#000000",
    "MENU_BG": "#ffffff",           # the Find list and the search boxes' suggestions
    "MENU_FG": "#0b0b0b",
    "MENU_ACTIVE_BG": "#0078d7",
    "MENU_ACTIVE_FG": "#ffffff",
    "MENU_DISABLED_FG": "#6d6d6d",
    "MENU_BORDER": "#000000",
    "TOOLTIP_BG": "#ffffe1",
    "TOOLTIP_FG": "#000000",
    "TOOLTIP_BORDER": "#000000",
    "CHART_TOOLTIP_BG": "#ffffff",
    "CHART_TOOLTIP_BORDER": "#c3c2b7",
    "LOG_BG": "#f4f6f9",
    "LOG_FG": "#000000",
    "PROGRESS_TROUGH": "#e6e6e6",
    "PROGRESS_BAR": "#06b025",
    "SEPARATOR": "#d0d0d0",
    "SASH": "#f0f0f0",
    "TITLE_BG": "#ffffff",
    "TITLE_FG": "#000000",
}

# -- the dark looks' shared data colours (the documented dark steps: series, ramp with the anchor flipped,
#    diverging, status) ------------------------------------------------------------------------------------------
DARK_DATA = {
    "SERIES": ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"],
    "RAMP": {100: "#0d366b", 150: "#104281", 200: "#184f95", 250: "#1c5cab", 300: "#256abf", 350: "#2a78d6",
             400: "#3987e5", 450: "#5598e7", 500: "#6da7ec", 550: "#86b6ef", 600: "#9ec5f4", 650: "#b7d3f6",
             700: "#cde2fb"},
    "POSITIVE": "#3987e5",
    "NEGATIVE": "#e66767",
    "NEUTRAL": "#383835",
    "GOOD": "#0ca30c",
    "WARNING": "#fab219",
    "SERIOUS": "#ec835a",
    "CRITICAL": "#d03b3b",
    "GOOD_TEXT": "#0ca30c",
    "GOOD_LABEL": "#0ca30c",
    "BASELINE": "#706f6b",          # the neutral data grey (de-emphasised bars, credits, axes): 3:1 on every
                                    # dark look's surface and cards
    "HEAT_ZERO": "#2c2c2a",         # below the ramp's first step, above the surface
    "TOTAL": "#9ec5f4",
    "FAMILY": "Segoe UI",
    "MONO": "Consolas",
}

# -- Graphite: neutral charcoal (the default) -----------------------------------------------------------------------
GRAPHITE = {
    **DARK_DATA,
    "CHROME": "#0f1012", "PAGE": "#1c1d20", "SURFACE": "#222427", "CARD": "#242629",
    "INK": "#eceef1", "INK_2": "#b3b8c0", "MUTED": "#9da3ac", "HINT": "#9aa2ad",
    "GRID": "#313338", "BORDER": "#383b41", "ACCENT": "#dde3ec", "HOVER": "#34373d", "SELECT": "#1f3a5f",
    "BAD_TEXT": "#ef7470",
    "FILM_SPAN": "#1c5cab", "WASH_BASE": "#222427", "EMPTY_CELL": "#222427",
    "ON_FILL": "#0b0b0b", "INK_ON_LIGHT": "#0b0b0b",
    "LINK": "#6da7ec", "FOCUS": "#6da7ec", "DISABLED_FG": "#737882",
    "ENTRY_BG": "#1a1b1e", "ENTRY_FG": "#eceef1", "ENTRY_BORDER": "#6f747e", "ENTRY_READONLY_BG": "#242629",
    "ENTRY_DISABLED_FG": "#737882", "ENTRY_SELECT_BG": "#2f74d2", "ENTRY_SELECT_FG": "#ffffff", "INSERT": "#eceef1",
    "BUTTON_BG": "#2e3035", "BUTTON_FG": "#eceef1", "BUTTON_ACTIVE": "#393c42", "BUTTON_PRESSED": "#26282c",
    "BUTTON_BORDER": "#6f747e", "BUTTON_DISABLED_FG": "#737882",
    "ACCENT_BUTTON_BG": "#6da7ec", "ACCENT_BUTTON_FG": "#0b0b0b", "ACCENT_BUTTON_ACTIVE": "#86b6ef",
    "ACCENT_BUTTON_PRESSED": "#5598e7",
    "TAB_STRIP_BG": "#0f1012", "TAB_BG": "#0f1012", "TAB_FG": "#b3b8c0", "TAB_HOVER_BG": "#1a1b1e",
    "TAB_SELECTED_BG": "#1c1d20", "TAB_SELECTED_FG": "#eceef1", "TAB_BORDER": "#383b41", "TAB_INDICATOR": "#6da7ec",
    "TREE_BG": "#242629", "TREE_FG": "#eceef1", "TREE_ALT_BG": "#28292d", "TREE_SELECT_BG": "#2f74d2",
    "TREE_SELECT_FG": "#ffffff", "TREE_HEADING_BG": "#2d3035", "TREE_HEADING_FG": "#eceef1",
    "TREE_HEADING_ACTIVE": "#393c42",
    "LIST_SELECT_BG": "#2f74d2", "LIST_SELECT_FG": "#ffffff",
    "SCROLL_TROUGH": "#1e2023", "SCROLL_THUMB": "#6b707a", "SCROLL_THUMB_ACTIVE": "#858a94",
    "SCROLL_ARROW": "#b3b8c0",
    "CHECK_BG": "#1a1b1e", "CHECK_BORDER": "#6f747e", "CHECK_ON": "#6da7ec", "CHECK_MARK": "#0b0b0b",
    "MENU_BG": "#2a2c31", "MENU_FG": "#eceef1", "MENU_ACTIVE_BG": "#2f74d2", "MENU_ACTIVE_FG": "#ffffff",
    "MENU_DISABLED_FG": "#737882", "MENU_BORDER": "#6f747e",
    "TOOLTIP_BG": "#2e3035", "TOOLTIP_FG": "#eceef1", "TOOLTIP_BORDER": "#6f747e",
    "CHART_TOOLTIP_BG": "#2e3035", "CHART_TOOLTIP_BORDER": "#6f747e",
    "LOG_BG": "#1a1b1e", "LOG_FG": "#b3b8c0",
    "PROGRESS_TROUGH": "#313338", "PROGRESS_BAR": "#6da7ec", "SEPARATOR": "#383b41", "SASH": "#1c1d20",
    "TITLE_BG": "#0f1012", "TITLE_FG": "#eceef1",
}

# -- Projection Booth: the brand navy as the chrome --------------------------------------------------------------
BOOTH = {
    **DARK_DATA,
    "CHROME": "#1f3a5f", "PAGE": "#0b1320", "SURFACE": "#131d2e", "CARD": "#152033",
    "INK": "#edf0f6", "INK_2": "#bac3d1", "MUTED": "#95a2b6", "HINT": "#a9b8cc",
    "GRID": "#223049", "BORDER": "#27354d", "ACCENT": "#b1cef7", "HOVER": "#26375a", "SELECT": "#1f3a5f",
    "BAD_TEXT": "#ed8b86",
    "FILM_SPAN": "#184f95", "WASH_BASE": "#131d2e", "EMPTY_CELL": "#131d2e",
    "ON_FILL": "#08101b", "INK_ON_LIGHT": "#08101b",
    "LINK": "#66b3ff", "FOCUS": "#66b3ff", "DISABLED_FG": "#6b7890",
    "ENTRY_BG": "#0a111c", "ENTRY_FG": "#edf0f6", "ENTRY_BORDER": "#6f89b0", "ENTRY_READONLY_BG": "#152033",
    "ENTRY_DISABLED_FG": "#6b7890", "ENTRY_SELECT_BG": "#7ea9e0", "ENTRY_SELECT_FG": "#08101b", "INSERT": "#edf0f6",
    "BUTTON_BG": "#1e2c42", "BUTTON_FG": "#edf0f6", "BUTTON_ACTIVE": "#273852", "BUTTON_PRESSED": "#17233a",
    "BUTTON_BORDER": "#6f89b0", "BUTTON_DISABLED_FG": "#6b7890",
    "ACCENT_BUTTON_BG": "#3466c6", "ACCENT_BUTTON_FG": "#ffffff", "ACCENT_BUTTON_ACTIVE": "#3a6ed0",
    "ACCENT_BUTTON_PRESSED": "#2c58ae",
    "TAB_STRIP_BG": "#1f3a5f", "TAB_BG": "#1f3a5f", "TAB_FG": "#bac3d1", "TAB_HOVER_BG": "#284874",
    "TAB_SELECTED_BG": "#0b1320", "TAB_SELECTED_FG": "#edf0f6", "TAB_BORDER": "#2f4c75", "TAB_INDICATOR": "#66b3ff",
    "TREE_BG": "#152033", "TREE_FG": "#edf0f6", "TREE_ALT_BG": "#18243a", "TREE_SELECT_BG": "#7ea9e0",
    "TREE_SELECT_FG": "#08101b", "TREE_HEADING_BG": "#1c2940", "TREE_HEADING_FG": "#edf0f6",
    "TREE_HEADING_ACTIVE": "#24344f",
    "LIST_SELECT_BG": "#7ea9e0", "LIST_SELECT_FG": "#08101b",
    "SCROLL_TROUGH": "#0e1726", "SCROLL_THUMB": "#5a7196", "SCROLL_THUMB_ACTIVE": "#7189ad",
    "SCROLL_ARROW": "#bac3d1",
    "CHECK_BG": "#0a111c", "CHECK_BORDER": "#6f89b0", "CHECK_ON": "#66b3ff", "CHECK_MARK": "#08101b",
    "MENU_BG": "#1b283d", "MENU_FG": "#edf0f6", "MENU_ACTIVE_BG": "#7ea9e0", "MENU_ACTIVE_FG": "#08101b",
    "MENU_DISABLED_FG": "#6b7890", "MENU_BORDER": "#6f89b0",
    "TOOLTIP_BG": "#22324b", "TOOLTIP_FG": "#edf0f6", "TOOLTIP_BORDER": "#6f89b0",
    "CHART_TOOLTIP_BG": "#22324b", "CHART_TOOLTIP_BORDER": "#6f89b0",
    "LOG_BG": "#0a111c", "LOG_FG": "#bac3d1",
    "PROGRESS_TROUGH": "#223049", "PROGRESS_BAR": "#66b3ff", "SEPARATOR": "#27354d", "SASH": "#0b1320",
    "TITLE_BG": "#1f3a5f", "TITLE_FG": "#edf0f6",
}

# -- Velvet: a darkened theatre ----------------------------------------------------------------------------------
VELVET = {
    **DARK_DATA,
    "CHROME": "#070403", "PAGE": "#18110d", "SURFACE": "#211712", "CARD": "#241914",
    "INK": "#f1e6d5", "INK_2": "#c7b8a5", "MUTED": "#a39585", "HINT": "#b0a292",
    "GRID": "#332721", "BORDER": "#3e2f27", "ACCENT": "#daaa72", "HOVER": "#3a2b21", "SELECT": "#3d2a1b",
    "BAD_TEXT": "#f3827a",
    "FILM_SPAN": "#184f95", "WASH_BASE": "#211712", "EMPTY_CELL": "#211712",
    "ON_FILL": "#130d0a", "INK_ON_LIGHT": "#130d0a",
    "LINK": "#6da7ec", "FOCUS": "#c7b8a5", "DISABLED_FG": "#85776a",
    "ENTRY_BG": "#150e0b", "ENTRY_FG": "#f1e6d5", "ENTRY_BORDER": "#857060", "ENTRY_READONLY_BG": "#241914",
    "ENTRY_DISABLED_FG": "#85776a", "ENTRY_SELECT_BG": "#9e6630", "ENTRY_SELECT_FG": "#ffffff", "INSERT": "#f1e6d5",
    "BUTTON_BG": "#3a2b22", "BUTTON_FG": "#f1e6d5", "BUTTON_ACTIVE": "#47362b", "BUTTON_PRESSED": "#2f231b",
    "BUTTON_BORDER": "#857060", "BUTTON_DISABLED_FG": "#85776a",
    "ACCENT_BUTTON_BG": "#daaa72", "ACCENT_BUTTON_FG": "#130d0a", "ACCENT_BUTTON_ACTIVE": "#e6bb85",
    "ACCENT_BUTTON_PRESSED": "#c99a63",
    "TAB_STRIP_BG": "#070403", "TAB_BG": "#070403", "TAB_FG": "#c7b8a5", "TAB_HOVER_BG": "#150e0b",
    "TAB_SELECTED_BG": "#18110d", "TAB_SELECTED_FG": "#f1e6d5", "TAB_BORDER": "#3e2f27", "TAB_INDICATOR": "#daaa72",
    "TREE_BG": "#241914", "TREE_FG": "#f1e6d5", "TREE_ALT_BG": "#2a1e18", "TREE_SELECT_BG": "#9e6630",
    "TREE_SELECT_FG": "#ffffff", "TREE_HEADING_BG": "#35271e", "TREE_HEADING_FG": "#f1e6d5",
    "TREE_HEADING_ACTIVE": "#423127",
    "LIST_SELECT_BG": "#9e6630", "LIST_SELECT_FG": "#ffffff",
    "SCROLL_TROUGH": "#19120f", "SCROLL_THUMB": "#76655a", "SCROLL_THUMB_ACTIVE": "#8e7b6c",
    "SCROLL_ARROW": "#c7b8a5",
    "CHECK_BG": "#150e0b", "CHECK_BORDER": "#857060", "CHECK_ON": "#150e0b", "CHECK_MARK": "#f1e6d5",
    "MENU_BG": "#2d211a", "MENU_FG": "#f1e6d5", "MENU_ACTIVE_BG": "#9e6630", "MENU_ACTIVE_FG": "#ffffff",
    "MENU_DISABLED_FG": "#85776a", "MENU_BORDER": "#857060",
    "TOOLTIP_BG": "#392a1f", "TOOLTIP_FG": "#f1e6d5", "TOOLTIP_BORDER": "#857060",
    "CHART_TOOLTIP_BG": "#392a1f", "CHART_TOOLTIP_BORDER": "#857060",
    "LOG_BG": "#150e0b", "LOG_FG": "#c7b8a5",
    "PROGRESS_TROUGH": "#332721", "PROGRESS_BAR": "#daaa72", "SEPARATOR": "#3e2f27", "SASH": "#18110d",
    "TITLE_BG": "#070403", "TITLE_FG": "#f1e6d5",
}

LOOKS: dict[str, Look] = {
    "light": Look("light", "Light", False,
                  "The original look: a warm off-white page, white cards, navy titles and Windows' own buttons "
                  "and boxes." if sys.platform == "win32" else
                  "The original look: a warm off-white page, white cards and navy titles, with grey buttons and "
                  "white boxes like Windows' own.", LIGHT),
    "graphite": Look("graphite", "Graphite", True,
                     ("Neutral charcoal, like Windows' own dark mode" if sys.platform == "win32" else
                      "Neutral charcoal, like a desktop's own dark mode")
                     + ", with one light blue for links, focus and the main button.", GRAPHITE),
    "booth": Look("booth", "Projection Booth", True,
                  "The app's navy becomes the window's frame, over a deep blue-black page, with sky-blue links.",
                  BOOTH),
    "velvet": Look("velvet", "Velvet", True,
                   "A darkened theatre: warm near-black, walnut cards, cream text and brass-gold titles.", VELVET),
}
ORDER = ["light", "graphite", "booth", "velvet"]
DARK_LOOKS = [k for k in ORDER if LOOKS[k].dark]
TOKEN_NAMES = tuple(sorted(set(LIGHT) | {"VERDICT_COLORS", "VERDICT_ICONS"} | set(SLOT_NAMES)))


def tokens(key: str) -> dict:
    """Every token of a look (a fresh copy: change it freely), with VERDICT_COLORS and the named series slots
    worked out. An unknown key is the default look."""
    look = LOOKS.get(key) or LOOKS[DEFAULT_LOOK]
    t = {name: (list(v) if isinstance(v, list) else dict(v) if isinstance(v, dict) else v)
         for name, v in look.tokens.items()}
    t.update(zip(SLOT_NAMES, t["SERIES"]))
    # the credits-scene verdicts are statuses
    t["VERDICT_COLORS"] = {"Yes": t["GOOD"], "Maybe": t["WARNING"], "None found": t["BASELINE"], "": t["GRID"],
                           "Likely": t["GOOD"]}
    t["VERDICT_ICONS"] = dict(VERDICT_ICONS)
    return t
