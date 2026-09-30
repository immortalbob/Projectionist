"""The Settings tab (ui/settings.py): built from the preferences registry - a card per section, a row per setting,
Appearance first with a picture of each look (Graphite marked as the default) - and every change saved and applied
at once, the other way round too (a setting changed elsewhere shows here), with the resets asking first. On a
withdrawn root, never shown; message boxes and file dialogs stubbed."""

import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from projectionist import prefs  # noqa: E402


def hidden_root():
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()                       # headless: never shown
    return root


def widgets(w, cls=None):
    out = []
    for c in w.winfo_children():
        if cls is None or isinstance(c, cls):
            out.append(c)
        out += widgets(c, cls)
    return out


class FakeApp:
    """What the Settings tab uses of the main window: its settings, saving them, the look, the status line - and
    telling the tabs of a change, as App.preference_changed does."""

    def __init__(self, settings=None):
        self.settings = dict(settings or {})
        self.saved = 0
        self.looks = []
        self.status = ""
        self.tabs = []
        self.apps = [("LibreOffice Calc", "scalc.exe"), ("Excel", "excel.exe")]

    def save_settings(self):
        self.saved += 1

    def apply_look(self):
        from projectionist.ui import theme as T
        self.looks.append(T.chosen_look(self))

    def preference_changed(self, key, value):
        for tab in self.tabs:
            tab.preference_changed(key, value)

    def set_status(self, text):
        self.status = text


class SettingsTabTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from projectionist import gui  # noqa: F401  (the window's own settings)
        from projectionist.ui import doctor, film  # noqa: F401  (theirs)
        from projectionist.ui import settings as S
        from projectionist.ui import theme as T
        cls.S, cls.T = S, T

    def setUp(self):
        from tkinter import filedialog, messagebox, ttk
        try:
            self.root = hidden_root()
        except Exception as exc:          # noqa: BLE001
            self.skipTest(f"Tk unavailable: {exc}")
        self.T.apply_styles(self.root)
        self.asked = []
        self.answer = True
        # (the system says light: Follow Windows / the system is offered, and nothing real is asked)
        self.stubs = [mock.patch.object(messagebox, "askyesno", self._ask),
                      mock.patch.object(filedialog, "askdirectory", lambda *a, **k: self.folder),
                      mock.patch.object(self.T, "system_dark", lambda: False)]
        for s in self.stubs:
            s.start()
        self.folder = ""
        self.app = FakeApp()
        nb = ttk.Notebook(self.root)
        self.tab = self.S.Tab(self.app, nb)
        self.app.tabs.append(self.tab)
        nb.add(self.tab.frame, text=self.tab.title)
        self.root.update_idletasks()

    def _ask(self, *args, **kwargs):
        self.asked.append(args)
        self.asked_how = kwargs
        return self.answer

    def tearDown(self):
        for s in self.stubs:
            s.stop()
        self.root.destroy()
        self.T.use("light")

    def test_built_from_the_registry(self):
        self.assertEqual(self.tab.title, "Settings")
        shown = [p.key for p in prefs.shown()]
        self.assertEqual(sorted(self.tab.controls), sorted(shown))
        self.assertEqual(list(self.tab.cards)[0], "Appearance")
        self.assertEqual(list(self.tab.cards), [s.name for s in prefs.sections()])
        # each setting's help text is on the tab
        texts = " ".join(str(w.cget("text")) for w in widgets(self.tab.frame) if w.winfo_class() == "TLabel")
        for key in shown:
            self.assertIn(prefs.pref(key).help.split(".")[0], texts, key)
        self.assertIn("settings.json", self.tab.where.cget("text"))
        self.assertIn("Takes effect the next time the app opens", texts)        # (Open on: from the next start)

    def test_the_looks_as_pictures_graphite_the_default(self):
        looks = self.tab.controls["look"]
        self.assertIsInstance(looks, self.S.LooksControl)
        self.assertEqual(list(looks.tiles), ["light", "graphite", "booth", "velvet", "windows"])
        names = [str(rb.cget("text")) for _c, _v, rb in looks.tiles.values()]
        self.assertEqual(names, ["Light", "Graphite  (default)", "Projection Booth", "Velvet",
                                 "Follow Windows" if sys.platform == "win32" else "Follow the system"])
        self.assertEqual(looks.var.get(), "graphite")
        self.assertIn("charcoal", looks.words.cget("text"))
        # every picture draws (and so renders headless, too)
        for _cell, view, _rb in looks.tiles.values():
            view.redraw(width=176, height=116)
            self.assertGreater(len(view.find_all()), 20)

    def test_pictures_render_headless_in_their_own_colours(self):
        from projectionist.ui.paint import PilPainter
        T, S = self.T, self.S
        with tempfile.TemporaryDirectory() as d:
            for key in T.ORDER + [T.FOLLOW_WINDOWS]:
                p = PilPainter(176, 116)
                p.SUPERSAMPLE = 1
                S.draw_look(p, key, "velvet", selected=key == "booth")
                p.save(os.path.join(d, f"{key}.png"))
                colours = {"#%02x%02x%02x" % c for _n, c in p.image.convert("RGB").getcolors(1000000)}
                shown = ["light", "velvet"] if key == T.FOLLOW_WINDOWS else [key]
                for look in shown:
                    t = T.tokens(look)
                    for name in ("PAGE", "CARD", "TITLE_BG", "ACCENT_BUTTON_BG"):
                        self.assertIn(t[name], colours, f"{key}: {look}.{name}")
                if key == "booth":
                    self.assertIn(T.FOCUS, colours)             # (the one chosen: ringed)

    def test_choosing_a_look_saves_and_applies_it(self):
        looks = self.tab.controls["look"]
        looks.tiles["velvet"][2].invoke()                   # its radio button
        self.assertEqual(self.app.settings["look"], "velvet")
        self.assertEqual(self.app.looks[-1], "velvet")
        self.assertIn("theatre", looks.words.cget("text"))
        looks._pick("booth")                                # a click on the picture
        self.assertEqual(self.app.settings["look"], "booth")
        # the dark look for Follow Windows only matters when following Windows
        dark = self.tab.controls["dark_look"]
        self.assertTrue(all(b.instate(["disabled"]) for b in dark.buttons))
        looks.tiles["windows"][2].invoke()
        self.assertFalse(any(b.instate(["disabled"]) for b in dark.buttons))
        dark.buttons[2].invoke()
        self.assertEqual(self.app.settings["dark_look"], "velvet")
        if sys.platform == "win32":
            self.assertIn("Velvet while they're dark", looks.words.cget("text"))
            self.assertIn("Windows' apps are light now", looks.words.cget("text"))
        else:
            self.assertIn("Velvet while it's set to dark", looks.words.cget("text"))
            self.assertIn("Your desktop is set to light now", looks.words.cget("text"))

    def test_follow_the_system_only_where_the_desktop_says(self):
        """Away from Windows, a desktop that doesn't say whether it's light or dark (no portal, no gsettings)
        isn't offered Follow the system - nor its dark look. One already chosen still shows, so it can be seen."""
        from tkinter import ttk
        for chosen, offered in (("graphite", False), ("windows", True)):
            with self.subTest(chosen=chosen), mock.patch.object(self.T, "follows_system", return_value=False):
                app = FakeApp({"look": chosen})
                tab = self.S.Tab(app, ttk.Notebook(self.root))
                looks = tab.controls["look"]
                self.assertEqual("windows" in looks.tiles, offered)
                self.assertEqual(bool(tab.controls["dark_look"].frame.winfo_manager()), offered)
        self.assertIn("windows", self.tab.controls["look"].tiles)          # (offered where the system says)
        self.assertTrue(self.tab.controls["dark_look"].frame.winfo_manager())
        # chosen on a desktop that said, and now it doesn't: what that means, in words
        with mock.patch.object(self.T, "WINDOWS", False):
            self.assertIn("Your desktop isn't saying which just now, so it's Light.",
                          self.S.look_words("windows", "booth", None))
            self.assertIn("Your desktop is set to dark now.", self.S.look_words("windows", "booth", True))
            self.assertNotIn("isn't saying", self.S.look_words("windows", "booth", False))

    def test_each_kind_of_control_saves_what_it_shows(self):
        c = self.tab.controls
        c["open_when_done"].check.invoke()                  # a tick box (on by default)
        self.assertIs(self.app.settings["open_when_done"], False)
        c["start_tab"].buttons[1].invoke()                  # radio buttons
        self.assertEqual(self.app.settings["start_tab"], "last")
        box = c["open_with"]                                # a drop-down (the programs found)
        box.var.set("Excel")
        box._picked()
        self.assertEqual(self.app.settings["open_with"], "Excel")
        n = c["film_recent_count"]                          # a number
        n.var.set("20")
        n._typed()
        self.assertEqual(self.app.settings["film_recent_count"], 20)
        self.assertEqual(n.var.get(), "20")
        for typed in ("99", "-1"):                          # outside its range: refused, and says the range
            n.var.set(typed)
            n._typed()
            self.assertEqual(self.app.settings["film_recent_count"], 20)
            self.assertTrue(n.error.winfo_manager())
            self.assertIn("from 0 to 30 films", n.error.cget("text"))
        n.var.set("lots")
        n._typed()
        self.assertEqual(self.app.settings["film_recent_count"], 20)
        self.assertTrue(n.error.winfo_manager())            # (says why)
        n.var.set("")                                       # emptied: the value back, the complaint gone
        n._typed()
        self.assertEqual(n.var.get(), "20")
        self.assertFalse(n.error.winfo_manager())
        lang = c["doctor_languages"]                        # a module's own control: languages as ticks
        self.assertEqual([code for code, var in lang.vars.items() if var.get()], ["en"])
        lang.add_var.set("Japanese")                        # ('Add a language...')
        lang._added()
        self.assertEqual(self.app.settings["doctor_languages"], ["en", "ja"])
        self.assertTrue(lang.vars["ja"].get())
        lang.checks["en"].invoke()
        lang.checks["ja"].invoke()                          # (the last one stays: says why)
        self.assertEqual(self.app.settings["doctor_languages"], ["ja"])
        self.assertIn("at least one language", lang.error.cget("text"))
        folder = c["out_dir"]                               # a folder: chosen, or cleared
        self.assertEqual(folder.var.get(), "None chosen")
        self.folder = ROOT
        folder._choose()
        self.assertEqual(self.app.settings["out_dir"], os.path.normpath(ROOT))
        folder.clear.invoke()
        self.assertEqual(self.app.settings["out_dir"], "")

    def test_a_change_made_elsewhere_shows_here(self):
        prefs.set(self.app, "csv", True)
        self.assertTrue(self.tab.controls["csv"].var.get())
        prefs.set(self.app, "doctor_languages", ["fr"])
        lang = self.tab.controls["doctor_languages"]
        self.assertEqual([code for code, var in lang.vars.items() if var.get()], ["fr"])
        prefs.set(self.app, "look", "velvet")
        self.assertEqual(self.tab.controls["look"].var.get(), "velvet")

    def test_reset_asks_first(self):
        prefs.set(self.app, "csv", True)
        prefs.set(self.app, "look", "booth")
        self.answer = False
        self.tab.reset_section("Export")
        self.assertTrue(self.app.settings["csv"])
        self.answer = True
        self.tab.reset_section("Export")
        self.assertNotIn("csv", self.app.settings)
        self.assertFalse(self.tab.controls["csv"].var.get())
        self.assertEqual(self.app.settings["look"], "booth")             # (another section: kept)
        self.assertIn("Export settings back to the defaults", self.app.status)
        self.tab.reset_everything()
        self.assertNotIn("look", self.app.settings)
        self.assertEqual(self.tab.controls["look"].var.get(), "graphite")
        self.assertEqual(self.app.looks[-1], "graphite")
        self.assertEqual(len(self.asked), 3)
        self.assertIn("Graphite", self.asked[-1][1])
        self.assertEqual(self.asked_how.get("default"), "no")              # (Enter by mistake resets nothing)

    def test_a_setting_the_file_couldnt_take_says_so(self):
        csv = self.tab.controls["csv"]
        self.app.settings_saved = False                     # (the settings file couldn't be written)
        csv.check.invoke()
        self.assertTrue(self.app.settings["csv"])           # (kept until the app closes)
        self.assertEqual(csv.error.cget("text"), self.S.NOT_SAVED)
        self.assertTrue(csv.error.winfo_manager())
        self.app.settings_saved = True
        csv.check.invoke()
        self.assertFalse(csv.error.winfo_manager())

    def test_tabbing_to_a_setting_below_scrolls_it_into_view(self):
        from tkinter import ttk
        self.root.geometry("900x600")
        self.tab.frame.master.pack(fill="both", expand=True)
        self.root.update()
        canvas = self.tab.scroll.canvas
        self.assertEqual(canvas.yview()[0], 0.0)
        last = [w for w in widgets(self.tab.cards[list(self.tab.cards)[-1]].body) if isinstance(w, ttk.Button)][-1]
        last.event_generate("<FocusIn>")                    # (as Tab moving the focus there does)
        self.root.update()
        self.assertGreater(canvas.yview()[0], 0.5)
        first = self.tab.controls["look"].tiles["light"][2]
        first.event_generate("<FocusIn>")                   # (Shift+Tab back to the top)
        self.root.update()
        self.assertLess(canvas.yview()[0], 0.1)             # (just enough to show it)

    def test_keyboard_reachable(self):
        from tkinter import ttk
        for key, control in self.tab.controls.items():
            focusable = [w for w in widgets(control.frame) if isinstance(w, (ttk.Checkbutton, ttk.Radiobutton,
                         ttk.Combobox, ttk.Spinbox, ttk.Entry, ttk.Button))]
            self.assertTrue(focusable, key)
            for w in focusable:
                self.assertNotIn(str(w.cget("takefocus")), ("0", "false"), f"{key}: {w}")
        buttons = [w for w in widgets(self.tab.frame) if isinstance(w, ttk.Button)]
        texts = [str(b.cget("text")) for b in buttons]
        self.assertIn("Reset all to defaults", texts)
        self.assertEqual(texts.count("Reset this section"), len(self.tab.cards))

    def test_wording(self):
        from projectionist.ui import watchnext  # noqa: F401  (its card)
        hint = prefs.SECTIONS["Appearance"].hint
        self.assertIn("look or text size shows at once", hint)          # (Text size too: no restart)
        self.assertNotIn("next time the app opens", hint)
        self.assertNotIn("Follow Windows", prefs.pref("look").help)      # (said under the pictures instead)
        self.assertIn("Put back to their defaults?", self.S.NATIVE_DIALOGS)
        names = [s.name for s in prefs.sections()]
        self.assertLess(names.index("Film"), names.index("Watch Next"))  # (the cards in the tabs' order)

    def test_navigate_to_a_section(self):
        self.tab.navigate(section="Library Doctor")          # (scrolls to it; nothing to show when not laid out)
        self.tab.navigate(section="No such section")


if __name__ == "__main__":
    unittest.main()
