"""The looks: every palette complete and within the contrast gate the dark looks were designed to (the scratch
check_palettes gate, as a test), the theme engine (tokens, tints, the option database, ttk styles, the title bar,
Follow Windows), and the whole window switching looks live on a withdrawn root (never shown) - every tab takes each
look without an error and no colour of another look is left behind on the widgets sampled."""

import math
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from projectionist.ui import palettes as P  # noqa: E402
from projectionist.ui import theme as T  # noqa: E402

DOC_SERIES_DARK = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"]
DOC_RAMP_LIGHT = {100: "#cde2fb", 150: "#b7d3f6", 200: "#9ec5f4", 250: "#86b6ef", 300: "#6da7ec", 350: "#5598e7",
                  400: "#3987e5", 450: "#2a78d6", 500: "#256abf", 550: "#1c5cab", 600: "#184f95", 650: "#104281",
                  700: "#0d366b"}
DOC_RAMP_DARK = {k: DOC_RAMP_LIGHT[800 - k] for k in DOC_RAMP_LIGHT}          # dark flips the anchor
DOC_DARK = {"POSITIVE": "#3987e5", "NEGATIVE": "#e66767", "NEUTRAL": "#383835", "GOOD": "#0ca30c",
            "WARNING": "#fab219", "SERIOUS": "#ec835a", "CRITICAL": "#d03b3b", "GOOD_TEXT": "#0ca30c",
            "HEAT_ZERO": "#2c2c2a"}

# The contrast gate (scratch darkmode/check_palettes.py): text >= 4.5:1, control edges / focus / marks >= 3:1,
# layers a visible step apart (OKLCH L)
TEXT = [
    ("INK", "PAGE"), ("INK", "CARD"), ("INK", "CHROME"), ("INK_2", "PAGE"), ("INK_2", "CARD"),
    ("MUTED", "CARD"), ("MUTED", "SURFACE"), ("MUTED", "PAGE"), ("MUTED", "MENU_BG"), ("HINT", "CHROME"),
    ("HINT", "PAGE"), ("ACCENT", "PAGE"), ("ACCENT", "CARD"), ("ACCENT", "CHROME"), ("ACCENT", "MENU_BG"),
    ("LINK", "CARD"), ("LINK", "PAGE"), ("GOOD_TEXT", "CARD"), ("GOOD_TEXT", "SURFACE"), ("GOOD_LABEL", "PAGE"),
    ("GOOD_LABEL", "CARD"), ("BAD_TEXT", "CARD"), ("BAD_TEXT", "PAGE"),
    ("TAB_FG", "TAB_BG"), ("TAB_FG", "TAB_HOVER_BG"), ("TAB_SELECTED_FG", "TAB_SELECTED_BG"),
    ("BUTTON_FG", "BUTTON_BG"), ("BUTTON_FG", "BUTTON_ACTIVE"), ("BUTTON_FG", "BUTTON_PRESSED"),
    ("ACCENT_BUTTON_FG", "ACCENT_BUTTON_BG"), ("ACCENT_BUTTON_FG", "ACCENT_BUTTON_ACTIVE"),
    ("ACCENT_BUTTON_FG", "ACCENT_BUTTON_PRESSED"), ("ENTRY_FG", "ENTRY_BG"), ("ENTRY_SELECT_FG", "ENTRY_SELECT_BG"),
    ("TREE_FG", "TREE_BG"), ("TREE_FG", "TREE_ALT_BG"), ("TREE_SELECT_FG", "TREE_SELECT_BG"),
    ("TREE_HEADING_FG", "TREE_HEADING_BG"), ("LIST_SELECT_FG", "LIST_SELECT_BG"), ("MENU_FG", "MENU_BG"),
    ("MENU_ACTIVE_FG", "MENU_ACTIVE_BG"), ("TOOLTIP_FG", "TOOLTIP_BG"), ("TOOLTIP_FG", "CHART_TOOLTIP_BG"),
    ("LOG_FG", "LOG_BG"), ("INK", "SELECT"), ("INK_2", "SELECT"), ("INK", "HOVER"), ("TITLE_FG", "TITLE_BG"),
    ("ON_FILL", "BLUE"), ("INK", "RAMP150"),
    # captions on a hover wash (the Find list's hovered line, Your critics' 'you liked it' zone) and the small
    # print on a chosen Library Doctor tile
    ("MUTED", "HOVER"), ("INK_2", "SELECT"),
]
EDGES = [
    ("ENTRY_BORDER", "CARD"), ("ENTRY_BORDER", "PAGE"), ("ENTRY_BORDER", "CHROME"), ("BUTTON_BORDER", "CARD"),
    ("BUTTON_BORDER", "PAGE"), ("CHECK_BORDER", "CARD"), ("CHECK_BORDER", "PAGE"), ("CHECK_ON", "CARD"),
    ("CHECK_MARK", "CHECK_ON"), ("FOCUS", "CARD"), ("FOCUS", "PAGE"), ("FOCUS", "CHROME"), ("FOCUS", "ENTRY_BG"),
    ("TAB_INDICATOR", "TAB_SELECTED_BG"), ("TAB_INDICATOR", "TAB_BG"), ("TREE_SELECT_BG", "TREE_BG"),
    ("TREE_SELECT_BG", "TREE_ALT_BG"), ("LIST_SELECT_BG", "MENU_BG"), ("MENU_ACTIVE_BG", "MENU_BG"),
    ("SCROLL_THUMB", "SCROLL_TROUGH"), ("MENU_BORDER", "CARD"), ("MENU_BORDER", "PAGE"),
    ("ACCENT_BUTTON_BG", "CARD"), ("PROGRESS_BAR", "PROGRESS_TROUGH"), ("CHART_TOOLTIP_BORDER", "CARD"),
    # the data grey (de-emphasised bars, dots and spans) on the charts' surface and on cards; the chosen Library
    # Doctor tile's ring
    ("BASELINE", "SURFACE"), ("BASELINE", "CARD"), ("RAMP350", "CARD"),
]
STEPS = [("CHROME", "PAGE", 0.05), ("CARD", "PAGE", 0.035), ("MENU_BG", "CARD", 0.02),
         ("TAB_SELECTED_BG", "TAB_BG", 0.05), ("HOVER", "SURFACE", 0.03), ("HOVER", "CARD", 0.025),
         ("HOVER", "MENU_BG", 0.035), ("TREE_ALT_BG", "TREE_BG", 0.012),
         ("PROGRESS_TROUGH", "PAGE", 0.03)]          # (the Export tab's empty track stands off the page)


def oklab_l(color: str) -> float:
    """OKLab lightness of a #rrggbb colour."""
    def lin(v):
        v /= 255
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(int(color[i:i + 2], 16)) for i in (1, 3, 5))
    l_ = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    m_ = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s_ = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    return 0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_


def delta_e(a: str, b: str) -> float:
    """OKLab distance x100."""
    def lab(c):
        def lin(v):
            v /= 255
            return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
        r, g, bb = (lin(int(c[i:i + 2], 16)) for i in (1, 3, 5))
        l_ = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * bb) ** (1 / 3)
        m_ = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * bb) ** (1 / 3)
        s_ = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * bb) ** (1 / 3)
        return (0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_,
                1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_,
                0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_)
    return 100 * math.dist(lab(a), lab(b))


def is_hex(value) -> bool:
    return isinstance(value, str) and len(value) == 7 and value[0] == "#" and \
        all(c in "0123456789abcdef" for c in value[1:])


class PaletteTest(unittest.TestCase):
    def test_four_looks_graphite_the_default(self):
        self.assertEqual(P.ORDER, ["light", "graphite", "booth", "velvet"])
        self.assertEqual(P.DEFAULT_LOOK, "graphite")
        self.assertEqual(P.DARK_LOOKS, ["graphite", "booth", "velvet"])
        self.assertEqual([P.LOOKS[k].name for k in P.ORDER], ["Light", "Graphite", "Projection Booth", "Velvet"])
        self.assertFalse(P.LOOKS["light"].dark)

    def test_every_token_is_defined_in_every_palette(self):
        names = set(P.TOKEN_NAMES)
        for key in P.ORDER:
            with self.subTest(look=key):
                t = P.tokens(key)
                self.assertEqual(set(t), names, f"{key}: missing {names - set(t)}, extra {set(t) - names}")
                for name, value in t.items():
                    if name in ("FAMILY", "MONO"):
                        self.assertIsInstance(value, str)
                    elif name == "SERIES":
                        self.assertEqual(len(value), 8)
                        self.assertTrue(all(is_hex(c) for c in value), value)
                    elif name == "RAMP":
                        self.assertEqual(sorted(value), list(range(100, 701, 50)))
                        self.assertTrue(all(is_hex(c) for c in value.values()))
                    elif name == "VERDICT_COLORS":
                        self.assertEqual(set(value), {"Yes", "Maybe", "None found", "", "Likely"})
                    elif name == "VERDICT_ICONS":
                        self.assertEqual(value["Yes"], "✔")
                    else:
                        self.assertTrue(is_hex(value), f"{key}.{name} = {value!r}")
                self.assertEqual([t[s] for s in P.SLOT_NAMES], t["SERIES"])

    def test_tokens_are_copies(self):
        t = P.tokens("graphite")
        t["SERIES"][0] = "#000000"
        t["RAMP"][100] = "#000000"
        self.assertEqual(P.tokens("graphite")["SERIES"][0], "#3987e5")
        self.assertEqual(P.tokens("graphite")["RAMP"][100], "#0d366b")

    def test_light_is_the_original_colouring_with_the_shared_chart_fixes(self):
        t = P.tokens("light")
        original = {"SURFACE": "#fcfcfb", "PAGE": "#f9f9f7", "CARD": "#ffffff", "INK": "#0b0b0b",
                    "INK_2": "#52514e", "MUTED": "#898781", "GRID": "#e1e0d9", "BASELINE": "#c3c2b7",
                    "BORDER": "#e6e5df", "ACCENT": "#1f3a5f", "HOVER": "#eef3fb", "SELECT": "#dbe7f8",
                    "POSITIVE": "#2a78d6", "NEGATIVE": "#e34948", "NEUTRAL": "#f0efec", "GOOD_TEXT": "#006300",
                    "HINT": "#5f6b7a", "GOOD_LABEL": "#1e7b34", "BAD_TEXT": "#b3261e", "LINK": "#2a78d6",
                    "TOOLTIP_BG": "#ffffe1", "LOG_BG": "#f4f6f9", "TREE_ALT_BG": "#f6f7f9",
                    "CHART_TOOLTIP_BG": "#ffffff", "CHART_TOOLTIP_BORDER": "#c3c2b7", "WASH_BASE": "#ffffff"}
        for name, value in original.items():
            self.assertEqual(t[name], value, name)
        self.assertEqual(t["SERIES"][0], "#2a78d6")
        self.assertEqual(t["RAMP"], DOC_RAMP_LIGHT)
        # the three fixes every look shares: the film span a ramp step, the total its own token, the zero cell
        self.assertEqual(t["FILM_SPAN"], t["RAMP"][250])
        self.assertEqual(t["TOTAL"], t["ACCENT"])
        self.assertEqual(t["HEAT_ZERO"], t["NEUTRAL"])
        self.assertEqual((t["LIST_SELECT_BG"], t["LIST_SELECT_FG"]), (t["SELECT"], t["INK"]))

    def test_dark_looks_share_the_documented_data_colours(self):
        for key in P.DARK_LOOKS:
            with self.subTest(look=key):
                t = P.tokens(key)
                self.assertEqual(t["SERIES"], DOC_SERIES_DARK)
                self.assertEqual(t["RAMP"], DOC_RAMP_DARK)
                for name, want in DOC_DARK.items():
                    self.assertEqual(t[name], want, name)
                self.assertEqual(t["VERDICT_COLORS"]["Yes"], t["GOOD"])
                self.assertEqual(t["VERDICT_COLORS"]["Maybe"], t["WARNING"])


class ContrastGateTest(unittest.TestCase):
    """check_palettes' gate, for the dark looks (Light's controls are Windows' own)."""

    def tokens(self, key):
        t = P.tokens(key)
        t["RAMP150"], t["RAMP350"] = t["RAMP"][150], t["RAMP"][350]
        return t

    def test_text_is_at_least_4_5_to_1(self):
        for key in P.DARK_LOOKS:
            t = self.tokens(key)
            for a, b in TEXT:
                with self.subTest(look=key, pair=(a, b)):
                    self.assertGreaterEqual(T.contrast(t[a], t[b]), 4.5, f"{key}: {a} {t[a]} on {b} {t[b]}")

    def test_edges_marks_and_selection_are_at_least_3_to_1(self):
        for key in P.DARK_LOOKS:
            t = self.tokens(key)
            for a, b in EDGES:
                if (a, b) == ("CHECK_ON", "CARD") and t["CHECK_ON"] == t["CHECK_BG"]:
                    a = "CHECK_MARK"             # (Velvet: a mark in the well, not a filled box)
                with self.subTest(look=key, pair=(a, b)):
                    self.assertGreaterEqual(T.contrast(t[a], t[b]), 3.0, f"{key}: {a} {t[a]} on {b} {t[b]}")

    def test_layers_read_as_separate_planes(self):
        for key in P.DARK_LOOKS:
            t = self.tokens(key)
            for a, b, need in STEPS:
                with self.subTest(look=key, step=(a, b)):
                    self.assertGreaterEqual(abs(oklab_l(t[a]) - oklab_l(t[b])), need)
            self.assertGreaterEqual(T.contrast(t["LIST_SELECT_BG"], t["HOVER"]), 2.0, key)

    def test_chart_marks_clear_the_surface(self):
        for key in P.DARK_LOOKS:
            t = self.tokens(key)
            with self.subTest(look=key):
                for c in t["SERIES"]:
                    self.assertGreaterEqual(T.contrast(c, t["SURFACE"]), 3.0, c)
                self.assertGreaterEqual(T.contrast(t["TOTAL"], t["SURFACE"]), 3.0)
                self.assertGreaterEqual(T.contrast(t["FILM_SPAN"], t["SURFACE"]), 2.0)
                self.assertIn(t["TOTAL"], DOC_RAMP_LIGHT.values())
                self.assertIn(t["FILM_SPAN"], DOC_RAMP_LIGHT.values())
                for other in ("POSITIVE", "NEGATIVE", "WARNING", "YELLOW", "BASELINE"):
                    self.assertGreaterEqual(delta_e(t["TOTAL"], t[other]), 15, other)
                self.assertGreaterEqual(delta_e(t["FILM_SPAN"], t["BASELINE"]), 15)
                # the heatmap's empty cell sits between the surface and the ramp's first step
                ls, lz, l150 = oklab_l(t["SURFACE"]), oklab_l(t["HEAT_ZERO"]), oklab_l(t["RAMP"][150])
                self.assertTrue(ls < lz < l150 and l150 - lz >= 0.06, (ls, lz, l150))

    def test_light_text_reads(self):
        t = self.tokens("light")
        for a, b in (("INK", "PAGE"), ("INK", "CARD"), ("INK_2", "CARD"), ("HINT", "PAGE"), ("ACCENT", "CARD"),
                     ("BAD_TEXT", "CARD"), ("GOOD_TEXT", "CARD"), ("TOOLTIP_FG", "TOOLTIP_BG"),
                     ("LOG_FG", "LOG_BG"), ("LIST_SELECT_FG", "LIST_SELECT_BG")):
            with self.subTest(pair=(a, b)):
                self.assertGreaterEqual(T.contrast(t[a], t[b]), 4.5, f"{a} on {b}")
        # Light keeps the original series blue for links and under the chain's end names: 4.4:1, as it always was
        for a, b in (("LINK", "CARD"), ("ON_FILL", "BLUE")):
            self.assertGreaterEqual(T.contrast(t[a], t[b]), 4.4, f"{a} on {b}")


class TokensTest(unittest.TestCase):
    def tearDown(self):
        T.use("light")

    def test_use_sets_the_module_tokens(self):
        T.use("velvet")
        self.assertEqual((T.LOOK, T.DARK, T.NATIVE), ("velvet", True, False))
        self.assertEqual(T.CARD, "#241914")
        self.assertEqual(T.BLUE, "#3987e5")
        self.assertEqual(T.RAMP[250], "#1c5cab")
        self.assertEqual(T.ramp(0), T.RAMP[250])
        self.assertEqual(T.TOKENS["INK"], T.INK)
        T.use("light")
        # (Light is Windows' own controls on Windows; elsewhere it's drawn from its tokens, as the dark looks are)
        self.assertEqual((T.LOOK, T.DARK, T.NATIVE), ("light", False, sys.platform == "win32"))
        self.assertEqual(T.NATIVE_LIGHT, sys.platform == "win32")
        self.assertEqual(T.CARD, "#ffffff")
        self.assertEqual(T.use("no such look"), "graphite")

    def test_ink_on_a_fill_is_dark_ink_even_in_a_dark_look(self):
        for key in P.ORDER:
            T.use(key)
            self.assertEqual(T.ink_on("#fab219"), T.INK_ON_LIGHT)
            self.assertEqual(T.ink_on("#0d366b"), "#ffffff")
            self.assertGreaterEqual(T.contrast(T.ink_on("#fab219"), "#fab219"), 4.5)

    def test_live_tables_follow_the_look(self):
        table = T.live(lambda: {"fix": T.SERIOUS, "info": T.BLUE})
        T.use("light")
        self.assertEqual(table["info"], "#2a78d6")
        T.use("booth")
        self.assertEqual(table["info"], "#3987e5")
        self.assertEqual(table.get("nope", "x"), "x")
        self.assertEqual(dict(table), {"fix": T.SERIOUS, "info": T.BLUE})

    def test_value_and_token_for(self):
        T.use("graphite")
        self.assertEqual(T.value("CARD"), "#242629")
        self.assertEqual(T.value(lambda: "#123456"), "#123456")
        self.assertEqual(T.value("#abcdef"), "#abcdef")
        self.assertIsNone(T.value(None))
        self.assertEqual(T.token_for(T.PAGE), "PAGE")
        self.assertEqual(T.token_for("SURFACE"), "SURFACE")
        self.assertIsNone(T.token_for("#010203"))

    def test_resolve_and_follow_windows(self):
        self.assertEqual(T.resolve("booth"), "booth")
        self.assertEqual(T.resolve("nonsense"), "graphite")
        self.assertEqual(T.resolve("windows", "velvet", windows_dark=True), "velvet")
        self.assertEqual(T.resolve("windows", "velvet", windows_dark=False), "light")
        self.assertEqual(T.resolve("windows", "velvet", windows_dark=None), "light")
        self.assertEqual(T.resolve("windows", "light", windows_dark=True), "graphite")
        # the default for anyone who hasn't chosen (an existing settings file has no look)
        self.assertEqual(T.chosen_look({}), "graphite")
        self.assertEqual(T.chosen_look({"look": 42}), "graphite")
        self.assertEqual(T.chosen_look(None), "graphite")
        with mock.patch.object(T, "system_dark", return_value=True):
            self.assertEqual(T.chosen_look({"look": "windows", "dark_look": "booth"}), "booth")
            self.assertEqual(T.chosen_look({"look": "windows"}), "graphite")
        with mock.patch.object(T, "system_dark", return_value=False):
            self.assertEqual(T.chosen_look({"look": "windows", "dark_look": "booth"}), "light")
        with mock.patch.object(T, "system_dark", return_value=None):          # (nothing says: Light)
            self.assertEqual(T.chosen_look({"look": "windows", "dark_look": "booth"}), "light")
        # on Windows, the system is Windows' own setting for apps
        if sys.platform == "win32":
            with mock.patch.object(T, "windows_apps_dark", return_value=True):
                self.assertTrue(T.system_dark())

    def test_the_choice_is_named_for_the_system(self):
        from projectionist import prefs
        self.assertEqual(T.FOLLOW_NAME, "Follow Windows" if sys.platform == "win32" else "Follow the system")
        self.assertEqual(dict(prefs.pref("look").choices)["windows"], T.FOLLOW_NAME)
        self.assertEqual(prefs.pref("dark_look").label, f"Dark look for {T.FOLLOW_NAME}")
        if sys.platform == "win32":
            self.assertEqual(prefs.pref("dark_look").help, "The look Follow Windows uses while Windows' apps are dark.")
            self.assertTrue(T.follows_system())                   # (always offered on Windows, as it always was)

    @unittest.skipUnless(sys.platform == "win32", "Windows' registry")
    def test_windows_setting_is_only_read(self):
        value = T.windows_apps_dark()
        self.assertIn(value, (True, False, None))
        import winreg
        with mock.patch.object(winreg, "OpenKey", side_effect=OSError("no key")):
            self.assertIsNone(T.windows_apps_dark())
        # it opens the key for reading only (the default access): nothing is ever written
        with mock.patch.object(winreg, "OpenKey", wraps=winreg.OpenKey) as opened, \
                mock.patch.object(winreg, "SetValueEx", side_effect=AssertionError("written")):
            T.windows_apps_dark()
        for call in opened.call_args_list:
            self.assertEqual(len(call.args), 2)
            self.assertNotIn("access", call.kwargs)

    def test_tab_images_mark_the_selected_tab(self):
        for key in P.DARK_LOOKS:
            t = P.tokens(key)
            specs = T.tab_specs(t)
            rows = T.tab_pixels(specs["TNotebook.Tab"]["selected"])
            self.assertEqual(rows[0][5], t["TAB_INDICATOR"])
            self.assertEqual(rows[1][5], t["TAB_INDICATOR"])
            self.assertEqual(rows[6][5], t["TAB_SELECTED_BG"])
            self.assertEqual(rows[6][0], t["TAB_BORDER"])
            inner = T.tab_pixels(specs["Inner.TNotebook.Tab"]["selected"])
            self.assertEqual(inner[-1][5], t["TAB_INDICATOR"])
            self.assertEqual(inner[0][5], t["PAGE"])


def hidden_root():
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()                       # headless: never shown
    return root


class ApplyTest(unittest.TestCase):
    """theme.apply on a withdrawn root: styles, the option database, classic widgets, tints, listeners."""

    def setUp(self):
        try:
            self.root = hidden_root()
        except Exception as exc:          # noqa: BLE001
            self.skipTest(f"Tk unavailable: {exc}")

    def tearDown(self):
        T.use("light")
        self.root.destroy()

    def test_every_look_applies_and_its_styles_hold_its_tokens(self):
        from tkinter import ttk
        for key in P.ORDER + ["graphite", "light", "velvet"]:          # (and back: a second dark look, then light)
            with self.subTest(look=key):
                style = T.apply(self.root, key)
                self.assertEqual(style.theme_use(), "clam" if P.LOOKS[key].dark else
                                 ("vista" if "vista" in style.theme_names() else style.theme_use()))
                self.assertEqual(style.lookup("Card.TFrame", "background"), T.CARD)
                self.assertEqual(style.lookup("CardLink.TLabel", "foreground"), T.LINK)
                self.assertEqual(style.lookup("Chrome.TFrame", "background"), T.CHROME)
                self.assertEqual(style.lookup("ChromeHint.TLabel", "foreground"), T.HINT)
                self.assertEqual(style.lookup("Bad.TLabel", "foreground"), T.BAD_TEXT)
                self.assertEqual(style.lookup("Inner.TNotebook", "background"), T.PAGE)
                if not T.NATIVE:                             # (the dark looks - and Light, away from Windows)
                    self.assertEqual(style.lookup("TButton", "background"), T.BUTTON_BG)
                    self.assertEqual(style.lookup("Treeview", "fieldbackground"), T.TREE_BG)
                    self.assertEqual(style.lookup("TEntry", "fieldbackground"), T.ENTRY_BG)
                    self.assertIn("LookTNotebookTab", str(style.layout("TNotebook.Tab")))
                    # a spin box as tall as Windows' own (clam's arrows set its height: no row of padding)
                    self.assertEqual(str(style.lookup("TSpinbox", "padding")), "4 0")
                    self.assertEqual(str(style.lookup("TEntry", "padding")), "4 1")
                    # the progress bar's empty track: its own shade, in an edge like the boxes'
                    self.assertEqual(style.lookup("TProgressbar", "troughcolor"), T.PROGRESS_TROUGH)
                    self.assertEqual(style.lookup("TProgressbar", "bordercolor"), T.ENTRY_BORDER)
                    import tkinter as tk
                    listbox = tk.Listbox(self.root)          # (made now: the option database's colours)
                    self.assertEqual(str(listbox.cget("background")), T.MENU_BG)
                    self.assertEqual(str(listbox.cget("selectbackground")), T.LIST_SELECT_BG)
                    listbox.destroy()
                    self.assertEqual(str(self.root.cget("background")), T.CHROME)
                self.assertIsInstance(ttk.Style(self.root), ttk.Style)

    def test_classic_widgets_made_earlier_follow_and_their_own_colours_stay(self):
        import tkinter as tk
        T.apply(self.root, "graphite")
        text = tk.Text(self.root)                     # colours from the option database
        own = tk.Frame(self.root, background="#123456")
        menu = tk.Menu(self.root, tearoff=0)
        self.assertEqual(str(text.cget("background")), T.CARD)
        self.assertEqual(str(menu.cget("background")), T.MENU_BG)
        T.apply(self.root, "booth")
        self.assertEqual(str(text.cget("background")), T.CARD)
        self.assertEqual(str(text.cget("foreground")), T.INK)
        self.assertEqual(str(menu.cget("activebackground")), T.MENU_ACTIVE_BG)
        self.assertEqual(str(own.cget("background")), "#123456")
        T.apply(self.root, "light")                   # Light: Tk's own defaults (Windows' colours) again
        probe = tk.Text(self.root)
        self.assertEqual(str(text.cget("background")), str(probe.cget("background")))
        if T.NATIVE:
            self.assertEqual(str(text.cget("background")), str(tk.Text(self.root).configure("background")[3]))
        else:                                         # (away from Windows, Light's own tokens)
            self.assertEqual(str(text.cget("background")), T.CARD)
            self.assertEqual(str(menu.cget("activebackground")), T.MENU_ACTIVE_BG)
        self.assertEqual(str(own.cget("background")), "#123456")

    def test_tints_follow_every_change(self):
        import tkinter as tk
        from tkinter import ttk
        T.apply(self.root, "light")
        canvas = T.tint(tk.Canvas(self.root), background="SURFACE", highlightbackground=lambda: T.BORDER)
        text = tk.Text(self.root)
        T.tint_tag(text, "link", foreground="LINK")
        tree = ttk.Treeview(self.root)
        T.tint_tag(tree, "odd", background="TREE_ALT_BG")
        for key in ("velvet", "light", "booth"):
            T.apply(self.root, key)
            self.assertEqual(str(canvas.cget("background")), T.SURFACE)
            self.assertEqual(str(canvas.cget("highlightbackground")), T.BORDER)
            self.assertEqual(str(text.tag_cget("link", "foreground")), T.LINK)
            self.assertEqual(str(tree.tag_configure("odd", "background")), T.TREE_ALT_BG)
        canvas.destroy()
        T.apply(self.root, "graphite")                # (a destroyed widget is skipped)

    def test_module_styles_and_listeners_run_on_every_change(self):
        from tkinter import ttk
        seen = []

        def styles(style):
            style.configure("TestThing.TLabel", foreground=T.ACCENT)
        T.add_styles(self.root, styles)
        try:
            listener = T.on_change(lambda: seen.append(T.LOOK))
            T.apply(self.root, "velvet")
            self.assertEqual(ttk.Style(self.root).lookup("TestThing.TLabel", "foreground"), T.ACCENT)
            T.apply(self.root, "booth")
            self.assertEqual(ttk.Style(self.root).lookup("TestThing.TLabel", "foreground"), "#b1cef7")
            self.assertEqual(seen[-2:], ["velvet", "booth"])
        finally:
            T._STYLE_HOOKS.remove(styles)
            T._LISTENERS.remove(listener)

    def test_a_failing_listener_is_reported_not_raised(self):
        said = []

        def broken():
            raise RuntimeError("boom")
        T.on_change(broken)
        try:
            with mock.patch.object(T, "report", said.append):
                T.apply(self.root, "graphite")
            self.assertTrue(any("boom" in s for s in said), said)
        finally:
            T._LISTENERS.remove(broken)

    def test_title_bar_never_fails(self):
        import tkinter as tk
        self.assertFalse(T.title_bar(tk.Frame(self.root)))           # (not a window of its own)
        for dark in (True, False):
            self.assertIn(T.title_bar(self.root, dark), (True, False))   # (a withdrawn window: nothing shows)
        self.assertFalse(self.root.winfo_viewable())
        if sys.platform == "win32":
            with mock.patch("ctypes.WinDLL", side_effect=OSError("no dwmapi")):
                self.assertFalse(T.title_bar(self.root, True))
        else:                                                       # (Windows' title bars only: nothing here)
            self.assertFalse(T.title_bar(self.root, True))

    def test_light_without_windows_own_controls(self):
        """Away from Windows there's no 'vista' theme: Light is clam drawn from its tokens - which describe what
        Windows draws - so it looks as it does there: grey frames, grey buttons with an edge (the main one too),
        boxed tabs, and the classic widgets (menus, lists) in its colours rather than Tk's own."""
        import tkinter as tk
        with mock.patch.object(T, "NATIVE_LIGHT", False):
            try:
                style = T.apply(self.root, "light")
                self.assertFalse(T.NATIVE)
                self.assertEqual(style.theme_use(), "clam")
                self.assertEqual(style.lookup("TFrame", "background"), T.CHROME)
                self.assertEqual(style.lookup("TCheckbutton", "background"), T.CHROME)
                self.assertEqual(style.lookup("Page.TFrame", "background"), T.PAGE)
                self.assertEqual(style.lookup("TButton", "background"), T.BUTTON_BG)
                self.assertEqual(style.lookup("TButton", "bordercolor"), T.BUTTON_BORDER)
                self.assertEqual(style.lookup("Accent.TButton", "bordercolor"), T.BUTTON_BORDER)
                self.assertEqual(style.lookup("TEntry", "fieldbackground"), T.ENTRY_BG)
                self.assertIn("LookTNotebookTab", str(style.layout("TNotebook.Tab")))
                menu = tk.Menu(self.root, tearoff=0)
                self.assertEqual(str(menu.cget("background")), T.MENU_BG)
                self.assertEqual(str(menu.cget("activebackground")), T.MENU_ACTIVE_BG)
                menu.destroy()
                self.assertEqual(str(self.root.cget("background")), T.CHROME)
                # and the dark looks' filled main button has no edge of another colour
                style = T.apply(self.root, "graphite")
                self.assertEqual(style.lookup("Accent.TButton", "bordercolor"), T.ACCENT_BUTTON_BG)
            finally:
                T.use("light")
        T.apply(self.root, "light")                                 # (back to this system's own Light)
        self.assertEqual(T.NATIVE, sys.platform == "win32")

    def test_a_window_closed_before_its_look_arrived_says_nothing(self):
        """A change of look reaches ttk's widgets when Tk is next idle (ttk::ThemeChanged). If the window has
        closed by then it used to print "can't invoke "event" command: application has been destroyed" from the
        next window's idle time: now it passes quietly - and any other error still shows."""
        import tkinter as tk
        root = hidden_root()
        T.apply(root, "velvet")
        self.assertTrue(root.tk.eval("info procs ::ttk::ThemeChangedUnguarded"))
        root.destroy()
        root.tk.eval("::ttk::ThemeChanged")                 # (what Tk would run: quiet, no error)
        other = hidden_root()
        try:
            T.apply(other, "graphite")
            other.update_idletasks()                        # (its own change of look delivered first)
            other.tk.eval("rename ::ttk::ThemeChangedUnguarded ::ttk::KeptByTheTest\n"
                          "proc ::ttk::ThemeChangedUnguarded {} {error {a real problem}}")
            try:
                with self.assertRaises(tk.TclError):
                    other.tk.eval("::ttk::ThemeChanged")
            finally:
                other.tk.eval("rename ::ttk::ThemeChangedUnguarded {}\n"
                              "rename ::ttk::KeptByTheTest ::ttk::ThemeChangedUnguarded")
        finally:
            other.destroy()

    def test_a_combo_boxs_drop_down_list_follows(self):
        from tkinter import ttk
        T.apply(self.root, "graphite")
        box = ttk.Combobox(self.root, values=["a", "b"])
        popdown = self.root.tk.call("ttk::combobox::PopdownWindow", box)     # (made withdrawn, as ttk does)
        listbox = f"{popdown}.f.l"
        self.assertEqual(str(self.root.tk.call(listbox, "cget", "-background")), T.MENU_BG)
        for key in ("velvet", "booth"):
            T.apply(self.root, key)
            self.assertEqual(str(self.root.tk.call(listbox, "cget", "-background")), T.MENU_BG)
            self.assertEqual(str(self.root.tk.call(listbox, "cget", "-selectbackground")), T.LIST_SELECT_BG)

    def test_restyle_a_kept_menu(self):
        import tkinter as tk
        T.apply(self.root, "light")
        menu = tk.Menu(self.root, tearoff=0)
        T.apply(self.root, "velvet")
        menu2 = tk.Menu(self.root, tearoff=0)
        self.assertEqual(str(menu2.cget("background")), T.MENU_BG)
        T.restyle(menu)
        self.assertEqual(str(menu.cget("background")), T.MENU_BG)
        self.assertEqual(str(menu.cget("foreground")), T.MENU_FG)


class PainterDefaultsTest(unittest.TestCase):
    """Nothing keeps a colour from the look it was defined in: the painters' and charts' defaults are read when
    drawing."""

    def tearDown(self):
        T.use("light")

    def test_png_surface_and_text_follow_the_look(self):
        from projectionist.ui import charts as C
        from projectionist.ui.paint import PilPainter
        for key in ("light", "graphite", "velvet"):
            T.use(key)
            p = PilPainter(200, 80)
            p.text(10, 40, "Aa", p.font(20, "bold"))
            img = p.image.convert("RGB")
            self.assertEqual("#%02x%02x%02x" % img.getpixel((2, 2)), T.SURFACE)
            colours = {"#%02x%02x%02x" % c for _n, c in img.getcolors(100000)}
            self.assertIn(T.INK, colours)
            p = PilPainter(400, 200)
            C.bars(p, [{"label": "A", "value": 3}, {"label": "B", "value": 2, "color": "ORANGE"}])
            colours = {"#%02x%02x%02x" % c for _n, c in p.image.convert("RGB").getcolors(1000000)}
            self.assertIn(T.BLUE, colours)
            self.assertIn(T.ORANGE, colours)          # (a token's name as an item's colour)

    def test_chart_tooltip_in_the_look(self):
        from projectionist.ui.paint import PilPainter, draw_tip
        T.use("booth")
        p = PilPainter(300, 150)
        p.SUPERSAMPLE = 1
        draw_tip(p, 20, 20, "One\ntwo")
        colours = {"#%02x%02x%02x" % c for _n, c in p.image.convert("RGB").getcolors(1000000)}
        self.assertIn(T.CHART_TOOLTIP_BG, colours)


class GuiShowTest(unittest.TestCase):
    """main() builds the window hidden and shows it with its title bar already in the look: transparent first."""

    def test_show_order(self):
        from projectionist import gui
        calls = []

        class FakeRoot:
            def attributes(self, *a):
                calls.append(("alpha", a[1]))

            def deiconify(self):
                calls.append(("show",))

            def update_idletasks(self):
                calls.append(("idle",))

        with mock.patch.object(gui.theme, "title_bar", lambda root: calls.append(("title bar",))):
            gui._show(FakeRoot())
        self.assertEqual(calls, [("alpha", 0.0), ("show",), ("idle",), ("title bar",), ("alpha", 1.0)])


# ---------------------------------------------------------------------------------------------------------
class LiveSwitchTest(unittest.TestCase):
    """The real window (fixture database, scratch settings, withdrawn root): the default look for a settings file
    with none, every tab taking every look live without an error, and nothing of another look left behind."""

    @classmethod
    def setUpClass(cls):
        try:
            cls.root = hidden_root()
        except Exception as exc:          # noqa: BLE001
            raise unittest.SkipTest(f"Tk unavailable: {exc}")
        import tkinter as tk
        from tkinter import filedialog, messagebox
        from projectionist import gui
        from projectionist.ui import paint
        from test_projectionist import build_fixture
        cls.gui = gui
        cls.stubs = [mock.patch.object(paint.Interaction, "_choose", lambda *a, **k: None),
                     mock.patch.object(tk.Menu, "tk_popup", lambda *a, **k: None),
                     mock.patch.object(tk.Menu, "post", lambda *a, **k: None),
                     mock.patch.object(messagebox, "showerror", lambda *a, **k: None),
                     mock.patch.object(messagebox, "showwarning", lambda *a, **k: None),
                     mock.patch.object(messagebox, "askyesno", lambda *a, **k: True),
                     mock.patch.object(filedialog, "asksaveasfilename", lambda *a, **k: ""),
                     mock.patch.object(filedialog, "askdirectory", lambda *a, **k: "")]
        for s in cls.stubs:
            s.start()
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = os.path.join(cls.tmp.name, "com.plexapp.plugins.library.db-2026-09-25")
        build_fixture(cls.db)
        cls.old = gui.SETTINGS_FILE, gui.SETTINGS_DIR
        gui.SETTINGS_FILE, gui.SETTINGS_DIR = os.path.join(cls.tmp.name, "settings.json"), cls.tmp.name
        with open(gui.SETTINGS_FILE, "w", encoding="utf-8") as f:     # an existing file, with no look in it
            f.write('{"csv": true, "open_when_done": false}')
        cls.errors = []
        cls.app = gui.App(cls.root, cls.db)
        cls.root.report_callback_exception = lambda *exc: cls.errors.append(exc)
        import time
        deadline = time.time() + 60
        while cls.app.catalog_state != "ready" and time.time() < deadline:
            cls.root.update()
            time.sleep(0.01)
        for tab in cls.app.tabs:                  # every tab shown once, as if clicked through
            cls.app.notebook.select(tab.frame)
            cls.root.update()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.app.shutdown()
        except Exception:                 # noqa: BLE001
            pass
        for s in cls.stubs:
            s.stop()
        cls.gui.SETTINGS_FILE, cls.gui.SETTINGS_DIR = cls.old
        T.use("light")
        cls.tmp.cleanup()

    def settle(self):
        from projectionist.ui import widgets
        for _ in range(3):
            self.root.update()
        widgets.finish_redraws()
        self.root.update()

    def all_widgets(self, w=None):
        w = w or self.root
        out = [w]
        for c in w.winfo_children():
            out += self.all_widgets(c)
        return out

    def test_graphite_for_a_settings_file_without_a_look(self):
        self.assertEqual(self.app.catalog_state, "ready")
        from projectionist import prefs
        prefs.reset(self.app, keys=["look"])
        self.app.apply_look()
        self.assertEqual(T.LOOK, "graphite")
        self.assertTrue(self.app.csv_var.get())             # (the old keys read as they were)
        self.assertFalse(self.app.open_var.get())

    def test_every_tab_takes_every_look_live(self):
        from projectionist import prefs
        from projectionist.ui.widgets import ChartView
        for key in ["light", "booth", "velvet", "graphite", "light", "graphite"]:
            with self.subTest(look=key):
                prefs.set(self.app, "look", key)
                self.settle()
                self.assertEqual(T.LOOK, key)
                self.assertEqual(self.errors, [])
                self.assertNotIn("Traceback", self.app.log.get("1.0", "end"))
                self.assertEqual(str(self.app.log.cget("background")), T.LOG_BG)
                others = self.other_colours(key)
                left = []
                for w in self.all_widgets():
                    if getattr(w, "look_preview", None):
                        continue                          # (the Settings tab's pictures of every look)
                    left += self.stale(w, others)
                self.assertEqual(left, [], f"{key}: colours of other looks left behind")
                views = [w for w in self.all_widgets() if isinstance(w, ChartView)
                         and not getattr(w, "look_preview", None)]
                self.assertGreater(len(views), 10)
                for view in views:
                    if T.token_for(str(view.cget("background")), ("SURFACE", "PAGE", "CARD")) is None:
                        self.fail(f"{view} background {view.cget('background')} isn't a surface of {key}")

    @staticmethod
    def other_colours(key):
        mine = {v for v in T.tokens(key).values() if isinstance(v, str) and v.startswith("#")}
        mine |= set(T.tokens(key)["SERIES"]) | set(T.tokens(key)["RAMP"].values())
        others = set()
        for other in P.ORDER:
            t = T.tokens(other)
            others |= {v for v in t.values() if isinstance(v, str) and v.startswith("#")}
        return others - mine

    def stale(self, w, others):
        import tkinter as tk
        from tkinter import ttk
        found = []

        def check(what, value):
            if str(value).lower() in others:
                found.append(f"{w.winfo_class()} {w} {what} {value}")
        if not isinstance(w, ttk.Widget):
            for opt in ("background", "foreground", "highlightbackground", "selectbackground"):
                try:
                    # (a highlight ring 0 px wide shows no colour: Tk's own #d9d9d9 on Linux isn't Light's)
                    if opt == "highlightbackground" and int(w.cget("highlightthickness") or 0) == 0:
                        continue
                    check(opt, w.cget(opt))
                except (tk.TclError, ValueError):
                    pass
        if isinstance(w, tk.Text):
            for tag in w.tag_names():
                for opt in ("background", "foreground"):
                    check(f"tag {tag} {opt}", w.tag_cget(tag, opt))
        if isinstance(w, ttk.Treeview):
            check("tag odd", w.tag_configure("odd", "background"))
        if isinstance(w, tk.Canvas):
            for item in w.find_all():
                for opt in ("fill", "outline"):
                    try:
                        check(f"item {w.type(item)} {opt}", w.itemcget(item, opt))
                    except tk.TclError:
                        pass
        return found

    def test_the_find_list_and_menus_in_the_look(self):
        import tkinter as tk
        from projectionist import prefs
        bar = self.app.search_bar
        if bar.popup is None:
            bar._make_popup()
        for key in ("velvet", "light"):
            prefs.set(self.app, "look", key)
            self.settle()
            self.assertEqual(str(bar.listbox.cget("background")), T.MENU_BG)
            self.assertEqual(str(bar.listbox.tag_cget("current", "background")), T.LIST_SELECT_BG)
            self.assertEqual(str(bar.listbox.tag_cget("current", "foreground")), T.LIST_SELECT_FG)
            self.assertEqual(str(bar.listbox.tag_cget("header", "foreground")), T.ACCENT)
            menu = tk.Menu(self.root, tearoff=0)
            self.assertEqual(str(menu.cget("background")),
                             T.MENU_BG if not T.NATIVE else str(menu.configure("background")[3]))
            menu.destroy()

    def test_follow_windows_asks_again_when_the_window_is_activated(self):
        from projectionist import prefs
        with mock.patch.object(T, "system_dark", return_value=True):
            prefs.set(self.app, "dark_look", "velvet")
            prefs.set(self.app, "look", "windows")
            self.assertEqual(T.LOOK, "velvet")
        with mock.patch.object(T, "system_dark", return_value=False):
            self.app._look_checked = 0.0
            self.app._activated()
            self.assertEqual(T.LOOK, "light")
            self.app._activated()                          # (not again within the second)
        with mock.patch.object(T, "system_dark", return_value=True):
            self.app._look_checked = 0.0
            self.app._activated()
            self.assertEqual(T.LOOK, "velvet")
            prefs.set(self.app, "look", "booth")          # (not following Windows: activation changes nothing)
            self.app._look_checked = 0.0
            self.app._activated()
            self.assertEqual(T.LOOK, "booth")
        prefs.reset(self.app, keys=["look", "dark_look"])
        self.settle()

    def test_on_x11_the_focus_coming_back_from_another_app_asks_again(self):
        """X11 sends no <Activate>: there, the focus leaving for another app's window and coming back does what it
        does. The focus moving about inside the window asks nothing."""
        from projectionist import prefs
        app = self.app
        if self.root._windowingsystem == "x11":
            self.assertIn(str(app._focus_came.__name__), self.root.bind("<FocusIn>"))
            self.assertIn(str(app._focus_left.__name__), self.root.bind("<FocusOut>"))
        with mock.patch.object(T, "system_dark", return_value=True):
            prefs.set(app, "dark_look", "velvet")
            prefs.set(app, "look", "windows")
            self.assertEqual(T.LOOK, "velvet")
        try:
            with mock.patch.object(T, "system_dark", return_value=False):
                app._look_checked = 0.0
                with mock.patch.object(app, "_focus_elsewhere", return_value=False):
                    app._focus_left()                  # (to another widget here)
                    self.settle()
                    app._focus_came()
                self.assertEqual(T.LOOK, "velvet")
                with mock.patch.object(app, "_focus_elsewhere", return_value=True):
                    app._focus_left()                  # (to another app)
                    self.settle()
                self.assertTrue(app._away)
                app._focus_came()                      # ...and back
                self.assertEqual(T.LOOK, "light")
                self.assertFalse(app._away)
        finally:
            prefs.reset(app, keys=["look", "dark_look"])
            self.settle()


class DesktopSettingTest(unittest.TestCase):
    """Follow the system, away from Windows: the desktop's light or dark setting, read (never written) through the
    freedesktop portal, then GNOME's gsettings - a Mac's appearance on a Mac. The commands are stubbed: what each
    answers, and what's asked."""

    def setUp(self):
        self.silent = set(T._SILENT)
        T._SILENT.clear()
        bus = mock.patch.object(T, "session_bus", lambda: True)       # (a desktop session: see test_no_bus)
        bus.start()
        self.addCleanup(bus.stop)

    def tearDown(self):
        T._SILENT.clear()
        T._SILENT.update(self.silent)

    def test_no_bus_no_asking(self):
        """No desktop session's bus (started over ssh -X, or on a bare X server): nothing is asked - gdbus and
        gsettings would each start a bus of their own that outlives the app - and the choice isn't offered."""
        with mock.patch.object(T, "session_bus", lambda: False), \
                self.answers({"gdbus": (0, "(<<uint32 1>>,)\n", ""), "gsettings": (0, "'prefer-dark'\n", "")}):
            self.assertIsNone(T.desktop_dark())
            with mock.patch.object(T, "WINDOWS", False), mock.patch.object(sys, "platform", "linux"):
                self.assertFalse(T.follows_system())
        self.assertEqual(self.asked, [])
        self.assertEqual(T._SILENT, set())                   # (a bus that comes later is still asked)
        with self.answers({"gdbus": (0, "(<<uint32 1>>,)\n", "")}):
            self.assertIs(T.desktop_dark(), True)
        # where the bus is: the address given, else the usual one in the runtime folder
        with tempfile.TemporaryDirectory() as runtime:
            session_bus = mock.patch.object(T, "session_bus", self.real_session_bus)
            with session_bus, mock.patch.dict(os.environ, {"DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/x/bus"}):
                self.assertTrue(T.session_bus())
            env = {k: v for k, v in os.environ.items() if k != "DBUS_SESSION_BUS_ADDRESS"}
            with session_bus, mock.patch.dict(os.environ, dict(env, XDG_RUNTIME_DIR=runtime), clear=True):
                self.assertFalse(T.session_bus())
                open(os.path.join(runtime, "bus"), "w").close()
                self.assertTrue(T.session_bus())
            with session_bus, mock.patch.dict(os.environ, {k: v for k, v in env.items() if k != "XDG_RUNTIME_DIR"},
                                              clear=True):
                self.assertFalse(T.session_bus())

    real_session_bus = staticmethod(T.session_bus)

    def answers(self, table):
        self.asked = []

        def ask(command):
            self.asked.append(command[0])
            return table.get(command[0])
        return mock.patch.object(T, "_ask", ask)

    def test_the_portal_first(self):
        with self.answers({"gdbus": (0, "(<<uint32 1>>,)\n", "")}):
            self.assertIs(T.desktop_dark(), True)
        with self.answers({"gdbus": (0, "(<<uint32 2>>,)\n", ""), "gsettings": (0, "'prefer-dark'\n", "")}):
            self.assertIs(T.desktop_dark(), False)                   # (prefers light: gsettings isn't asked)
        self.assertEqual(self.asked, ["gdbus"])
        with self.answers({"gdbus": (0, "(<uint32 0>,)\n", "")}):       # (ReadOne's shape; no preference: light)
            self.assertIs(T.desktop_dark(), False)

    def test_then_gsettings_and_neither_is_asked_again_once_silent(self):
        with self.answers({"gdbus": (1, "", "Error: GDBus.Error:org.freedesktop.DBus.Error.UnknownMethod"),
                           "gsettings": (0, "'prefer-dark'\n", "")}):
            self.assertIs(T.desktop_dark(), True)
            self.assertIs(T.desktop_dark(), True)
        self.assertEqual(self.asked, ["gdbus", "gsettings", "gsettings"])     # (the portal only the once)
        # gsettings with nothing to read makes a setting up in memory, and says so: no answer
        with self.answers({"gsettings": (0, "'default'\n", "GLib-GIO-Message: Using the 'memory' GSettings "
                                                             "backend.  Your settings will not be saved")}):
            self.assertIsNone(T.desktop_dark())
        with self.answers({}):
            self.assertIsNone(T.desktop_dark())
        self.assertEqual(self.asked, [])

    def test_odd_answers_are_no_answer(self):
        with self.answers({"gdbus": (0, "()", ""), "gsettings": (0, "'high-contrast'\n", "")}):
            self.assertIsNone(T.desktop_dark())
        self.assertIsNone(T._ask(["projectionist-no-such-command"]))     # (not installed: no answer, no error)

    def test_a_mac(self):
        with self.answers({"defaults": (0, "Dark\n", "")}):
            self.assertIs(T._mac_dark(), True)
        with self.answers({"defaults": (1, "", "The domain/default pair of (kCFPreferencesAnyApplication, "
                                               "AppleInterfaceStyle) does not exist")}):
            self.assertIs(T._mac_dark(), False)

    def test_what_each_system_asks_and_whether_the_choice_is_offered(self):
        with mock.patch.object(T, "WINDOWS", False), mock.patch.object(sys, "platform", "linux"), \
                self.answers({"gdbus": (0, "(<<uint32 1>>,)", "")}):
            self.assertIs(T.system_dark(), True)
            self.assertTrue(T.follows_system())
        T._SILENT.clear()
        with mock.patch.object(T, "WINDOWS", False), mock.patch.object(sys, "platform", "linux"), self.answers({}):
            self.assertIsNone(T.system_dark())
            self.assertFalse(T.follows_system())                     # (nothing answers: not offered)
        T._SILENT.clear()
        with mock.patch.object(T, "WINDOWS", False), mock.patch.object(sys, "platform", "darwin"), \
                self.answers({"defaults": (0, "Dark\n", "")}):
            self.assertIs(T.system_dark(), True)
            self.assertEqual(self.asked, ["defaults"])


class FontFamilyTest(unittest.TestCase):
    """The looks name Windows' fonts; where they aren't installed the nearest the computer has is used."""

    def setUp(self):
        try:
            self.root = hidden_root()
        except Exception as exc:          # noqa: BLE001
            self.skipTest(f"Tk unavailable: {exc}")

    def tearDown(self):
        T.use("light")
        self.root.destroy()

    def test_the_nearest_installed(self):
        installed = {n.casefold(): n for n in ("DejaVu Sans", "DejaVu Sans Mono", "Ubuntu", "Cantarell")}
        with mock.patch.object(T, "WINDOWS", False):
            self.root.__dict__["_font_families"] = installed
            self.assertEqual(T.font_family(self.root), "Cantarell")                   # (before Ubuntu, DejaVu)
            self.assertEqual(T.font_family(self.root, "Consolas"), "DejaVu Sans Mono")
            self.assertEqual(T.font_family(self.root, "dejavu sans"), "DejaVu Sans")  # (as it's installed)
            self.root.__dict__["_font_families"] = {"segoe ui": "Segoe UI", "ubuntu": "Ubuntu"}
            self.assertEqual(T.font_family(self.root), "Segoe UI")                    # (there: itself)
            self.root.__dict__["_font_families"] = {}
            self.assertEqual(T.font_family(self.root), T.FAMILY)                      # (none known: Tk picks)
        with mock.patch.object(T, "WINDOWS", True):
            self.root.__dict__["_font_families"] = installed
            self.assertEqual(T.font_family(self.root), "Segoe UI")                    # (Windows has its own)
        del self.root.__dict__["_font_families"]

    def test_the_window_and_the_charts_draw_in_an_installed_font(self):
        from projectionist.ui.paint import TkPainter
        import tkinter as tk
        T.apply(self.root, "graphite")
        installed = T.installed_families(self.root)
        for name in (T.font(self.root, 9), T.font(self.root, 10, "bold"), "TkDefaultFont"):
            family = str(self.root.tk.call("font", "configure", name, "-family"))
            self.assertEqual(family, T.font_family(self.root))
            self.assertIn(family.casefold(), installed, name)
        log = T.font(self.root, 9, family=T.MONO)
        self.assertEqual(str(self.root.tk.call("font", "configure", log, "-family")),
                         T.font_family(self.root, T.MONO))
        painter = TkPainter(tk.Canvas(self.root), 200, 100, 1.0)
        self.assertEqual(painter._tkfont(painter.font(9)).actual("family").casefold(),
                         T.font_family(self.root).casefold())

    def test_light_tabs_are_boxed_as_windows_draws_them(self):
        t = P.tokens("light")
        specs = T.tab_specs(t)
        for style in ("TNotebook.Tab", "Inner.TNotebook.Tab"):
            for state, fill in (("normal", t["TAB_BG"]), ("active", t["TAB_HOVER_BG"]),
                                ("selected", t["TAB_SELECTED_BG"])):
                rows = T.tab_pixels(specs[style][state])
                self.assertEqual(rows[0][5], t["TAB_BORDER"], (style, state))            # the top edge
                self.assertEqual((rows[6][0], rows[6][-1]), (t["TAB_BORDER"],) * 2)      # the sides
                self.assertEqual(rows[6][5], fill, (style, state))
                self.assertEqual(rows[-1][5], fill)                                      # (open at the bottom)
        self.assertIsNone(T.tab_specs(P.tokens("graphite"))["TNotebook.Tab"]["normal"]["sides"])   # (dark: flat)


if __name__ == "__main__":
    unittest.main()
