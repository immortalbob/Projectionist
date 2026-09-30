"""The preferences registry (projectionist/prefs.py): defining, reading (defaults, bad saved values), changing
(checked, saved, applied, passed on), resetting, and the app's own settings - the same JSON keys as ever, so a
settings file saved before the registry reads exactly as it did."""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from projectionist import prefs  # noqa: E402


def shout(value) -> str:
    """A module's own kind of setting: letters only, kept in capitals."""
    if not str(value).isalpha():
        raise ValueError("letters only")
    return str(value).upper()


class FakeApp:
    def __init__(self, settings=None):
        self.settings = dict(settings or {})
        self.saved = 0
        self.changes = []
        self.logged = []

    def save_settings(self):
        self.saved += 1

    def preference_changed(self, key, value):
        self.changes.append((key, value))

    def _log(self, text):
        self.logged.append(text)


class RegistryTest(unittest.TestCase):
    def setUp(self):
        self.keys = set(prefs.PREFS)
        self.sections = set(prefs.SECTIONS)
        self.applied = []
        prefs.section("Test things", order=900, hint="For the tests.")
        prefs.define("t_bool", "Test things", "A tick", kind="bool", default=True, help="yes or no")
        prefs.define("t_choice", "Test things", "A choice", kind="choice", default="b",
                     choices=[("a", "Aye"), ("b", "Bee")], apply=lambda app, v: self.applied.append(v))
        prefs.define("t_number", "Test things", "A number", kind="number", default=5, minimum=0, maximum=10,
                     unit="films")
        prefs.define("t_text", "Test things", "Some text", kind="text", default="hello", apply=prefs.NEXT_START)
        prefs.define("t_folder", "Test things", "A folder", kind="folder", default="")
        prefs.define("t_list", "Test things", "A list", kind="list", default=["x"], shown=False)
        prefs.define("t_upper", "Test things", "Shouting", kind="shout", default="HI", parse=shout,
                     format=lambda v: v.lower())

    def tearDown(self):
        for key in set(prefs.PREFS) - self.keys:
            del prefs.PREFS[key]
        for name in set(prefs.SECTIONS) - self.sections:
            del prefs.SECTIONS[name]

    def test_defaults_when_nothing_is_saved(self):
        for source in (None, {}, FakeApp()):
            self.assertIs(prefs.get(source, "t_bool"), True)
            self.assertEqual(prefs.get(source, "t_choice"), "b")
            self.assertEqual(prefs.get(source, "t_number"), 5)
            self.assertEqual(prefs.get(source, "t_list"), ["x"])
        a = prefs.get(None, "t_list")
        a.append("y")
        self.assertEqual(prefs.get(None, "t_list"), ["x"])          # (a copy each time)

    def test_bad_saved_values_read_as_the_default(self):
        saved = {"t_bool": "perhaps", "t_choice": "z", "t_number": "lots", "t_text": 7, "t_list": "x",
                 "t_upper": "12"}
        self.assertIs(prefs.get(saved, "t_bool"), True)
        self.assertEqual(prefs.get(saved, "t_choice"), "b")
        self.assertEqual(prefs.get(saved, "t_number"), 5)
        self.assertEqual(prefs.get(saved, "t_text"), "hello")
        self.assertEqual(prefs.get(saved, "t_list"), ["x"])
        self.assertEqual(prefs.get(saved, "t_upper"), "HI")
        # and good ones as they are (coerced to the kind)
        self.assertIs(prefs.get({"t_bool": 0}, "t_bool"), False)
        self.assertEqual(prefs.get({"t_number": 99}, "t_number"), 10)            # (brought inside its range)
        self.assertEqual(prefs.get({"t_number": "3.6"}, "t_number"), 4)          # (a whole number, like the default)
        self.assertEqual(prefs.get({"t_upper": "abc"}, "t_upper"), "ABC")

    def test_hand_edited_values_never_raise(self):
        """Valid JSON a hand edit could leave: a number too big for a float, a list holding objects."""
        self.assertEqual(prefs.get({"t_number": int("9" * 400)}, "t_number"), 5)
        self.assertEqual(prefs.get({"t_number": "9" * 400}, "t_number"), 5)
        self.assertEqual(prefs.get({"t_list": [{"a": 1}, "y", 3, True, None, ["z"]]}, "t_list"), ["y", 3])
        with self.assertRaises(ValueError):
            prefs.set(FakeApp(), "t_number", int("9" * 400))

    def test_a_save_that_fails_is_known(self):
        app = FakeApp()
        self.assertTrue(prefs.save_ok(app))                    # (an app that doesn't say: saved)
        app.save_settings = lambda: False
        prefs.set(app, "t_choice", "a")
        self.assertEqual(app.settings["t_choice"], "a")        # (kept for now all the same)
        app.settings_saved = False
        self.assertFalse(prefs.save_ok(app))

    def test_set_checks_saves_applies_and_tells(self):
        app = FakeApp()
        self.assertEqual(prefs.set(app, "t_choice", "a"), "a")
        self.assertEqual(app.settings["t_choice"], "a")
        self.assertEqual(app.saved, 1)
        self.assertEqual(self.applied, ["a"])
        self.assertEqual(app.changes, [("t_choice", "a")])
        prefs.set(app, "t_choice", "a")                     # the same again: nothing happens
        self.assertEqual((app.saved, self.applied, len(app.changes)), (1, ["a"], 1))
        with self.assertRaises(ValueError):
            prefs.set(app, "t_choice", "z")
        with self.assertRaises(ValueError):
            prefs.set(app, "t_upper", "1a")
        self.assertEqual(prefs.set(app, "t_number", 42), 10)
        self.assertEqual(prefs.set(app, "t_folder", "  C:\\films  "), "C:\\films")
        with self.assertRaises(TypeError):
            prefs.set(object(), "t_bool", True)

    def test_a_failing_apply_is_reported_and_the_value_kept(self):
        prefs.define("t_broken", "Test things", "Broken", kind="bool", default=False,
                     apply=lambda app, v: 1 / 0)
        app = FakeApp()
        prefs.set(app, "t_broken", True)
        self.assertTrue(app.settings["t_broken"])
        self.assertTrue(any("ZeroDivisionError" in t for t in app.logged))
        self.assertEqual(app.changes, [("t_broken", True)])

    def test_reset_a_section_or_everything(self):
        app = FakeApp({"t_bool": False, "t_choice": "a", "t_list": ["kept"], "other": 1})
        changed = prefs.reset(app, section="Test things")
        self.assertEqual(sorted(changed), ["t_bool", "t_choice"])
        self.assertNotIn("t_bool", app.settings)
        self.assertEqual(app.settings["t_list"], ["kept"])          # (not on the Settings tab: not reset)
        self.assertEqual(app.settings["other"], 1)
        self.assertIn(("t_choice", "b"), app.changes)
        self.assertEqual(prefs.reset(app, section="Test things"), [])
        app.settings["t_number"] = 7
        self.assertIn("t_number", prefs.reset(app))

    def test_words_shown_and_sections(self):
        p = prefs.pref("t_choice")
        self.assertEqual(p.words("a"), "Aye")
        self.assertEqual(prefs.pref("t_bool").words(True), "On")
        self.assertEqual(prefs.pref("t_number").words(3), "3 films")
        self.assertEqual(prefs.pref("t_upper").words("HI"), "hi")
        self.assertFalse(prefs.pref("t_text").live)
        self.assertTrue(p.live)
        self.assertEqual([x.key for x in prefs.shown("Test things")],
                         ["t_bool", "t_choice", "t_number", "t_text", "t_folder", "t_upper"])
        self.assertIn("Test things", [s.name for s in prefs.sections()])

    def test_defining_again_keeps_its_place(self):
        order = prefs.pref("t_bool").order
        prefs.define("t_bool", "Test things", "A tick, renamed", kind="bool", default=False)
        self.assertEqual(prefs.pref("t_bool").order, order)
        self.assertEqual(prefs.pref("t_bool").label, "A tick, renamed")

    def test_dynamic_choices_and_defaults(self):
        prefs.define("t_dyn", "Test things", "Program", kind="choice",
                     default=lambda app: app.programs[0] if app else "", choices=lambda app: app.programs)
        app = FakeApp()
        app.programs = ["Calc", "Excel"]
        self.assertEqual(prefs.get(app, "t_dyn"), "Calc")
        app.settings["t_dyn"] = "Excel"
        self.assertEqual(prefs.get(app, "t_dyn"), "Excel")
        app.settings["t_dyn"] = "Numbers"                    # (not on this computer)
        self.assertEqual(prefs.get(app, "t_dyn"), "Calc")
        self.assertEqual(prefs.get({"t_dyn": "Numbers"}, "t_dyn"), "Numbers")   # (no app to ask: as saved)


class AppSettingsTest(unittest.TestCase):
    """The settings the app keeps, defined where they're used, under the keys it always saved them under."""

    @classmethod
    def setUpClass(cls):
        from projectionist import gui  # noqa: F401  (defines the window's settings)
        from projectionist.ui import doctor, film, settings, theme  # noqa: F401

    def test_the_registry_holds_the_apps_settings(self):
        for key in ("look", "dark_look", "start_tab", "open_when_done", "open_with", "csv", "out_dir",
                    "film_recent_count", "doctor_languages", "doctor_save_dir"):
            self.assertIn(key, prefs.PREFS)
            self.assertTrue(prefs.PREFS[key].shown, key)
            self.assertTrue(prefs.PREFS[key].help, key)
        for key in ("window_geometry", "db_path", "excluded_libraries", "excluded_sheets", "film_recent",
                    "last_tab"):
            self.assertFalse(prefs.PREFS[key].shown, key)
        names = [s.name for s in prefs.sections()]
        self.assertEqual(names[0], "Appearance")
        for name in ("Starting up", "Export", "Film", "Library Doctor"):
            self.assertIn(name, names)
        self.assertEqual(prefs.pref("look").default, "graphite")
        self.assertEqual(prefs.pref("look").control, "looks")
        self.assertEqual([v for v, _l in prefs.pref("look").choices],
                         ["light", "graphite", "booth", "velvet", "windows"])

    def test_a_settings_file_from_before_reads_as_it_did(self):
        """Keys saved by earlier versions (and by the app under its old name) mean what they meant; a file with no
        look gets Graphite; values the registry can't use read as the default."""
        old = {"csv": True, "open_when_done": False, "open_with": "Excel", "out_dir": "D:\\Exports",
               "doctor_languages": ["en", "ja"], "doctor_save_dir": "D:\\Lists", "film_recent": ["1", "2"],
               "window_geometry": "1200x800", "excluded_libraries": ["Movies-World"], "excluded_sheets": ["Cast"],
               "db_path": "D:\\Plex\\x.db"}
        self.assertIs(prefs.get(old, "csv"), True)
        self.assertIs(prefs.get(old, "open_when_done"), False)
        self.assertEqual(prefs.get(old, "open_with"), "Excel")
        self.assertEqual(prefs.get(old, "out_dir"), "D:\\Exports")
        self.assertEqual(prefs.get(old, "doctor_languages"), ["en", "ja"])
        self.assertEqual(prefs.get(old, "doctor_save_dir"), "D:\\Lists")
        self.assertEqual(prefs.get(old, "film_recent"), ["1", "2"])
        self.assertEqual(prefs.get(old, "window_geometry"), "1200x800")
        self.assertEqual(prefs.get(old, "excluded_libraries"), ["Movies-World"])
        self.assertEqual(prefs.get(old, "look"), "graphite")
        self.assertEqual(prefs.get(old, "film_recent_count"), 12)
        self.assertEqual(prefs.get({"doctor_languages": ["Klingon"]}, "doctor_languages"), ["en"])
        self.assertEqual(prefs.get({"doctor_languages": "English, Japanese"}, "doctor_languages"), ["en", "ja"])

    def test_the_settings_file_with_a_byte_order_mark_or_unreadable(self):
        """A file saved by Notepad or PowerShell (a byte-order mark first) reads as well as any; one that can't be
        read is kept as settings.json.bad before a save writes a fresh one; a save that fails says so."""
        from projectionist import gui
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(gui, "SETTINGS_FILE", os.path.join(d, "settings.json")), \
                mock.patch.object(gui, "SETTINGS_DIR", d):
            self.assertEqual(gui.load_settings(), {})                    # (none yet)
            self.assertFalse(os.path.exists(gui.SETTINGS_FILE + ".bad"))
            with open(gui.SETTINGS_FILE, "w", encoding="utf-8-sig") as f:
                json.dump({"look": "velvet", "csv": True}, f)
            self.assertEqual(gui.load_settings(), {"look": "velvet", "csv": True})
            with open(gui.SETTINGS_FILE, "w", encoding="utf-8") as f:
                f.write('{"look": "velvet", "csv": tru')
            self.assertEqual(gui.load_settings(), {})
            with open(gui.SETTINGS_FILE + ".bad", encoding="utf-8") as f:
                self.assertEqual(f.read(), '{"look": "velvet", "csv": tru')
            self.assertTrue(gui.save_settings({"csv": False}))
            self.assertTrue(os.path.exists(gui.SETTINGS_FILE + ".bad"))    # (still there after the save)
            blocked = os.path.join(d, "a file")
            with open(blocked, "w", encoding="utf-8") as f:
                f.write("x")
            with mock.patch.object(gui, "SETTINGS_FILE", os.path.join(blocked, "settings.json")):
                self.assertFalse(gui.save_settings({"csv": True}))      # (a file where the folder should be)

                class Window:
                    settings, settings_saved, logged = {"csv": True}, True, []

                    def _log(self, text):
                        self.logged.append(text)
                window = Window()
                self.assertFalse(gui.App.save_settings(window))
                self.assertFalse(gui.App.save_settings(window))
                self.assertFalse(window.settings_saved)
                self.assertEqual(len(window.logged), 1)                   # (said once, not on every change)
                self.assertIn("couldn't be saved", window.logged[0])

    def test_a_window_never_shown_saves_nothing_when_it_closes(self):
        """(Tests build the window hidden: closing one must never write a settings file - above all not the
        real one, which would also stop the old app's settings being carried across.)"""
        import tkinter as tk
        from projectionist import gui
        try:
            root = tk.Tk()
        except Exception as exc:          # noqa: BLE001
            self.skipTest(f"Tk unavailable: {exc}")
        root.withdraw()
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(gui, "SETTINGS_FILE", os.path.join(d, "settings.json")), \
                mock.patch.object(gui, "SETTINGS_DIR", d):
            app = gui.App(root, None)
            root.after_cancel(app._timers.pop("pick"))
            app.notebook.select(app.tabs[0].frame)
            app.shutdown()
            self.assertFalse(os.path.exists(gui.SETTINGS_FILE))
        from projectionist.ui import theme as T
        T.use("light")

    def test_the_window_reads_and_keeps_its_settings_through_the_registry(self):
        import tkinter as tk
        from tkinter import filedialog, messagebox
        from projectionist import gui
        from projectionist.ui import paint
        from projectionist.ui import theme as T
        from test_projectionist import build_fixture
        try:
            root = tk.Tk()
        except Exception as exc:          # noqa: BLE001
            self.skipTest(f"Tk unavailable: {exc}")
        root.withdraw()
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(gui, "SETTINGS_FILE", os.path.join(d, "settings.json")), \
                mock.patch.object(gui, "SETTINGS_DIR", d), \
                mock.patch.object(paint.Interaction, "_choose", lambda *a, **k: None), \
                mock.patch.object(tk.Menu, "tk_popup", lambda *a, **k: None), \
                mock.patch.object(messagebox, "showerror", lambda *a, **k: None), \
                mock.patch.object(filedialog, "asksaveasfilename", lambda *a, **k: ""):
            db = os.path.join(d, "com.plexapp.plugins.library.db-2026-09-25")
            build_fixture(db)
            with open(gui.SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump({"csv": True, "open_when_done": False, "film_recent": ["nope"], "start_tab": "last",
                           "last_tab": "Library Doctor", "date_style": "dmy",
                           # hand edits that once stopped the window, or its Film tab, from opening
                           "excluded_sheets": [{"a": 1}, "Cast"], "film_recent_count": int("9" * 400)}, f)
            app = gui.App(root, None)
            try:
                from projectionist import formats
                self.assertEqual(formats.STYLE["date"], "dmy")           # (the window follows the dates' style)
                formats.reset()
                self.assertEqual(prefs.get(app, "excluded_sheets"), ["Cast"])
                self.assertFalse(app.sheet_vars["Cast"].get())
                self.assertEqual(prefs.get(app, "film_recent_count"), 12)
                self.assertTrue(app.csv_var.get())
                self.assertFalse(app.open_var.get())
                self.assertEqual(T.LOOK, "graphite")
                self.assertEqual(app.current_tab().title, "Library Doctor")      # Open on: the tab last open
                # the Export tab's ticks and the setting are one and the same, both ways
                app.csv_var.set(False)
                self.assertIs(prefs.get(app, "csv"), False)
                with open(gui.SETTINGS_FILE, encoding="utf-8") as f:
                    self.assertIs(json.load(f)["csv"], False)
                prefs.set(app, "open_when_done", True)
                self.assertTrue(app.open_var.get())
                # a tab's own settings, changed on the Settings tab, reach the tab
                tabs = {t.title: t for t in app.tabs}
                prefs.set(app, "doctor_languages", ["ja", "en"])
                self.assertEqual(tabs["Library Doctor"].languages, ["ja", "en"])
                tabs["Film"].recent[:] = ["a", "b", "c"]
                app.settings["film_recent"] = ["a", "b", "c"]
                prefs.set(app, "film_recent_count", 2)          # (fewer shown for a while: none lost)
                self.assertEqual(tabs["Film"].recent, ["a", "b", "c"])
                prefs.set(app, "film_recent_count", 0)          # (no list: kept until the window closes...)
                self.assertEqual(app.settings["film_recent"], ["a", "b", "c"])
                self.assertEqual(tabs["Settings"].controls["film_recent_count"].var.get(), "0")
                # putting the Export section back forgets its values (the Export tab's own controls following
                # the change don't write them straight back)
                prefs.set(app, "csv", True)
                prefs.reset(app, section="Export")
                self.assertFalse(app.csv_var.get())
                with open(gui.SETTINGS_FILE, encoding="utf-8") as f:
                    kept = json.load(f)
                for key in ("csv", "open_when_done", "open_with"):
                    self.assertNotIn(key, kept, key)
                app.notebook.select(app.tabs[1].frame)
            finally:
                with mock.patch.object(root, "state", return_value="zoomed"):   # (as if it had been on screen)
                    app.shutdown()
            with open(gui.SETTINGS_FILE, encoding="utf-8") as f:
                saved = json.load(f)
            self.assertEqual(saved["last_tab"], "Film")
            self.assertEqual(saved["film_recent"], [])                    # (...and forgotten as it closes)
            self.assertEqual(saved["doctor_languages"], ["ja", "en"])
            T.use("light")


if __name__ == "__main__":
    unittest.main()
