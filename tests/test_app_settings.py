"""The main window's own settings (projectionist/gui.py, catalog.py): Open on (any tab), the folder for Plex backups,
the folder for exports, the spreadsheet program (for every tab), 'Also count as seen' (the server's other
accounts), the text size - and Graphite as the look when none was chosen. The real window over the fixture database
on a withdrawn root (never shown); message boxes, file dialogs, menus and opening files are stubbed, and the
settings file is a temporary one."""

import gc
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from projectionist import prefs  # noqa: E402
from test_projectionist import MATRIX_GUID, build_fixture  # noqa: E402


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


# ---------------------------------------------------------------------------------------------------------
# The collection: the other accounts' plays, and whose plays count as seen
# ---------------------------------------------------------------------------------------------------------
class OtherAccountsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = os.path.join(cls.tmp.name, "com.plexapp.plugins.library.db-2026-09-25")
        build_fixture(cls.db)
        con = sqlite3.connect(cls.db)
        # a second copy of The Matrix (the same GUID, another library): its plays are the same plays, counted once
        con.execute("INSERT INTO metadata_items (id, library_section_id, metadata_type, guid, title, year) "
                    "VALUES (1900, 2, 1, ?, 'The Matrix', 1999)", (MATRIX_GUID,))
        # another account's play of the owner's favourite, an account with a settings row but no plays, account 0
        con.execute("INSERT INTO accounts (id, name) VALUES (77, 'partner'), (88, 'never played'), (0, '')")
        con.execute("INSERT INTO metadata_item_settings (account_id, guid, view_count) VALUES "
                    "(77, 'plex://movie/hk1/edition/Extended', 3), (88, 'plex://movie/sf75', 0), "
                    "(0, 'plex://movie/sf75', 4), (77, 'plex://movie/sf75', 1)")
        con.commit()
        con.close()
        from projectionist.catalog import load
        cls.catalog = load(cls.db)
        cls.films = {f.title: f for f in cls.catalog.films.values()}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_other_accounts_plays_are_kept_apart_from_the_owners(self):
        matrix, hk, sf = self.films["The Matrix"], self.films["Hong Kong Action"], self.films["Only Assistant"]
        self.assertEqual(matrix.played_by, {42: 2})                 # (two copies, one GUID: counted once)
        self.assertEqual(hk.played_by, {77: 3})                     # (the owner's play is owner_plays, not here)
        self.assertEqual(hk.owner_plays, 1)
        self.assertEqual(sf.played_by, {77: 1})                     # (no one's account 0; no plays, not listed)
        self.assertEqual(matrix.owner_plays, 0)                     # (the owner's rows only, as before)
        self.assertFalse(matrix.watched)
        self.assertEqual(self.catalog.owner_id, 1)
        self.assertEqual(self.catalog.accounts, {1: "Owner", 42: "friend", 77: "partner", 88: "never played"})
        self.assertEqual(self.catalog.account_plays(), {77: 2, 42: 1})          # films played, most first
        self.assertEqual(self.catalog.account_name(42), "friend")
        self.assertEqual(self.catalog.account_name(5), "User 5")

    def test_seen_counts_the_accounts_asked_for(self):
        matrix, hk = self.films["The Matrix"], self.films["Hong Kong Action"]
        self.assertFalse(matrix.seen())
        self.assertTrue(matrix.seen([42]))
        self.assertFalse(matrix.seen([77, 88]))
        self.assertTrue(hk.seen())                                  # (the owner's own)
        seen = {f.title for f in self.catalog.films.values() if f.seen([42, 77])}
        self.assertEqual(seen, {"The Matrix", "Hong Kong Action", "Only Assistant"})

    def test_account_ids_for_the_setting_and_requests(self):
        from projectionist.catalog import account_ids
        self.assertEqual(account_ids(None), [])
        self.assertEqual(account_ids([42, "7", 42]), [7, 42])
        self.assertEqual(account_ids((1,)), [1])
        for bad in ("42", 42, {"a": 1}, [True], ["friend"], [1.5], [None]):
            with self.assertRaises(ValueError, msg=repr(bad)):
                account_ids(bad)
        # the setting: a bad saved value reads as none
        self.assertEqual(prefs.get({}, "seen_accounts"), [])
        self.assertEqual(prefs.get({"seen_accounts": ["42", 7]}, "seen_accounts"), [7, 42])
        self.assertEqual(prefs.get({"seen_accounts": "everyone"}, "seen_accounts"), [])
        p = prefs.pref("seen_accounts")
        self.assertEqual((p.section, p.kind, p.live, p.shown), ("Your collection", "accounts", True, True))
        self.assertEqual(p.words([7, 42]), "2 accounts")

    def test_ask_info_lists_the_other_accounts(self):
        from projectionist.ask import handle
        info = handle({"action": "info"}, catalog=self.catalog)
        self.assertTrue(info["ok"])
        self.assertEqual(info["other_accounts"], [{"id": 77, "name": "partner", "films_played": 2},
                                                  {"id": 42, "name": "friend", "films_played": 1}])
        self.assertEqual(info["films_you_played"], 1)


# ---------------------------------------------------------------------------------------------------------
# Text size
# ---------------------------------------------------------------------------------------------------------
class TextSizeTest(unittest.TestCase):
    # (On Windows, Tk's scaling is kept with the display, which every Tk root in the process shares: each test puts
    # it back, or every window the later tests make would have bigger text.)
    def setUp(self):
        try:
            self.root = hidden_root()
        except Exception as exc:          # noqa: BLE001
            self.skipTest(f"Tk unavailable: {exc}")
        self.scaling = self.root.tk.call("tk", "scaling")

    def tearDown(self):
        self.root.tk.call("tk", "scaling", self.scaling)
        self.root.destroy()
        self.root = None
        gc.collect()

    def test_scaling_moves_the_fonts_and_the_layout_unit_together(self):
        from tkinter import font as tkfont
        from projectionist import gui
        from projectionist.ui import theme as T
        base = float(self.root.tk.call("tk", "scaling"))
        unit = T.scale(self.root)
        line = tkfont.Font(root=self.root, family="Segoe UI", size=9).metrics("linespace")
        self.assertEqual(gui.apply_text_size(self.root, {"text_size": 130}), 1.3)
        self.assertAlmostEqual(float(self.root.tk.call("tk", "scaling")), base * 1.3, places=2)
        self.assertAlmostEqual(T.scale(self.root), max(unit * 1.3, 1.0), places=2)
        self.assertGreater(tkfont.Font(root=self.root, family="Segoe UI", size=9).metrics("linespace"), line)
        # again: from Tk's own scaling, not the last one
        self.assertEqual(gui.apply_text_size(self.root, {"text_size": 115}), 1.15)
        self.assertAlmostEqual(float(self.root.tk.call("tk", "scaling")), base * 1.15, places=2)
        self.assertEqual(gui.apply_text_size(self.root, {}), 1.0)            # (the default: Normal)
        self.assertAlmostEqual(float(self.root.tk.call("tk", "scaling")), base, places=2)
        self.assertEqual(gui.apply_text_size(self.root, {"text_size": 250}), 1.0)   # (not a choice: Normal)

    def test_the_setting(self):
        from projectionist import gui  # noqa: F401
        p = prefs.pref("text_size")
        self.assertEqual((p.section, p.default, p.live), ("Appearance", 100, True))     # (shows at once)
        self.assertEqual([v for v, _l in p.choices], [100, 115, 130])
        self.assertEqual(p.words(115), "Larger (115%)")

    def test_main_scales_the_window_before_building_it(self):
        """gui.main with the window, the timer and showing it stubbed: the text size is on the root before App."""
        from projectionist import gui
        seen = {}
        root = self.root
        root.mainloop = lambda *a, **k: None

        def app(r, initial_db):
            seen["scaling"] = float(r.tk.call("tk", "scaling"))
            seen["db"] = initial_db
        with tempfile.TemporaryDirectory() as d:
            settings = os.path.join(d, "settings.json")
            with open(settings, "w", encoding="utf-8") as f:
                json.dump({"text_size": 115}, f)
            base = float(root.tk.call("tk", "scaling"))
            # main() shows a real error box when XlsxWriter can't be imported - e.g. when a test run points APPDATA
            # elsewhere and so hides the user's site-packages. Neither may ever reach the screen from a test.
            with mock.patch.multiple(gui, SETTINGS_FILE=settings, SETTINGS_DIR=d,
                                     OLD_SETTINGS_FILE=os.path.join(d, "old", "settings.json"),
                                     EARLIER_SETTINGS_FILES=[], App=app,
                                     _show=lambda r: None, _enable_dpi_awareness=lambda: None,
                                     _share_time_with_the_window=lambda: (lambda: None)), \
                    mock.patch.dict(sys.modules, {"xlsxwriter": sys.modules.get("xlsxwriter")
                                                  or types.ModuleType("xlsxwriter")}), \
                    mock.patch.object(gui, "messagebox") as box, \
                    mock.patch.object(gui.tk, "Tk", lambda *a, **k: root):
                self.assertEqual(gui.main("x.db"), 0)
            box.showerror.assert_not_called()
        self.assertAlmostEqual(seen["scaling"], base * 1.15, places=2)
        self.assertEqual(seen["db"], "x.db")


# ---------------------------------------------------------------------------------------------------------
# The real window
# ---------------------------------------------------------------------------------------------------------
class WindowSettingsTest(unittest.TestCase):
    """One window over the fixture database (loaded by hand, not from the folders), its settings in a temporary
    file."""

    def setUp(self):
        try:
            self.root = hidden_root()
        except Exception as exc:          # noqa: BLE001
            self.skipTest(f"Tk unavailable: {exc}")
        import tkinter as tk
        from tkinter import filedialog, messagebox
        from projectionist import gui
        from projectionist.ui import paint
        self.gui = gui
        self.shown = []                    # message boxes and dialogs asked for (all stubbed)
        self.opened = []                   # files 'opened' in a program
        self.dialog_answer = ""
        self.stubs = [
            mock.patch.object(paint.Interaction, "_choose", lambda *a, **k: None),
            mock.patch.object(tk.Menu, "tk_popup", lambda *a, **k: None),
            mock.patch.object(tk.Menu, "post", lambda *a, **k: None),
            mock.patch.object(messagebox, "showerror", lambda *a, **k: self.shown.append(("error",) + a)),
            mock.patch.object(messagebox, "showwarning", lambda *a, **k: self.shown.append(("warning",) + a)),
            mock.patch.object(messagebox, "askyesno", lambda *a, **k: True),
            mock.patch.object(filedialog, "askopenfilename", self._dialog),
            mock.patch.object(filedialog, "asksaveasfilename", self._dialog),
            mock.patch.object(filedialog, "askdirectory", self._dialog),
            mock.patch.object(gui, "open_spreadsheet", lambda path, program: self.opened.append((path, program))),
            mock.patch.object(gui, "_open_path", lambda *a, **k: None),
        ]
        for s in self.stubs:
            s.start()
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.db = os.path.join(self.dir, "dumps", "com.plexapp.plugins.library.db-2026-09-20")
        os.makedirs(os.path.dirname(self.db))
        build_fixture(self.db)
        self.stubs.append(mock.patch.multiple(gui, SETTINGS_FILE=os.path.join(self.dir, "settings.json"),
                                              SETTINGS_DIR=self.dir))
        self.stubs[-1].start()
        self.app = None

    def _dialog(self, *args, **kwargs):
        self.shown.append(("dialog", kwargs))
        return self.dialog_answer

    def tearDown(self):
        if self.app is not None:
            try:
                self.app.shutdown()
            except Exception:             # noqa: BLE001
                pass
        for s in reversed(self.stubs):
            s.stop()
        try:
            self.root.destroy()
        except Exception:                 # noqa: BLE001
            pass
        # Let go of the window, then free it here: left for the collector, it could be freed on a later test's
        # job thread, and Tk freed on the wrong thread takes the whole run down
        self.app = self.root = self.tabs = None
        gc.collect()
        from projectionist.ui import theme as T
        T.use("light")
        self.tmp.cleanup()

    def open_app(self, settings=None, db=None):
        if settings is not None:
            with open(self.gui.SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(settings, f)
        self.app = self.gui.App(self.root, db)
        self.root.after_cancel(self.app._timers.pop("pick"))      # (the tests pick the database themselves)
        self.tabs = {t.title: t for t in self.app.tabs}
        return self.app

    def pump(self, until, timeout=60):
        deadline = time.time() + timeout
        while not until():
            self.root.update()
            time.sleep(0.005)
            self.assertLess(time.time(), deadline, "timed out")

    def ready(self):
        self.pump(lambda: self.app.catalog_state == "ready")
        self.pump(lambda: not self.app._held and self.app.queue.empty())
        self.root.update()

    def saved(self):
        with open(self.gui.SETTINGS_FILE, encoding="utf-8") as f:
            return json.load(f)

    # -- the look -------------------------------------------------------------------------------------------------
    def test_graphite_when_no_look_was_chosen(self):
        from projectionist.ui import theme as T
        self.open_app({"csv": True})                         # a settings file from before the looks
        self.assertEqual((T.LOOK, T.DARK), ("graphite", True))
        self.assertEqual(self.tabs["Settings"].controls["look"].var.get(), "graphite")
        self.assertEqual(self.tabs["Settings"].controls["text_size"].var.get(), "100")

    def test_the_settings_are_on_the_settings_tab_in_their_sections(self):
        self.open_app()
        tab = self.tabs["Settings"]
        for key, section in (("text_size", "Appearance"), ("start_tab", "Starting up"),
                             ("backups_folder", "Starting up"), ("out_dir", "Export"), ("open_with", "Export"),
                             ("open_when_done", "Export"), ("seen_accounts", "Your collection")):
            self.assertEqual(prefs.pref(key).section, section, key)
            self.assertIn(key, tab.controls, key)
            self.assertIn(tab.controls[key].frame, widgets(tab.cards[section].body), key)
        names = [s.name for s in prefs.sections()]
        self.assertLess(names.index("Starting up"), names.index("Your collection"))
        self.assertIsInstance(tab.controls["start_tab"], self.gui.StartTabControl)
        self.assertIsInstance(tab.controls["seen_accounts"], self.gui.AccountsControl)

    # -- Open on --------------------------------------------------------------------------------------------------
    def test_open_on_any_tab(self):
        app = self.open_app({"start_tab": "Watch Next"})
        self.assertEqual(app.current_tab().title, "Watch Next")
        # a tab that's gone (or was never there) leaves Export in front
        app.notebook.select(app.notebook.tabs()[0])
        for gone in ("No Such Tab", "Settings", 7):
            app.settings["start_tab"] = gone
            self.assertEqual(prefs.get(app, "start_tab"), "export")
            app._open_start_tab()
            self.assertIsNone(app.current_tab(), gone)
        app.settings.update(start_tab="last", last_tab="Credits")
        app._open_start_tab()
        self.assertEqual(app.current_tab().title, "Credits")
        # the tab shown before the collection is read has its 'reading' page, then its content
        app.load_db(self.db)
        self.assertEqual(self.tabs["Credits"].state, "loading")
        self.ready()
        self.assertEqual(self.tabs["Credits"].state, "ready")

    def test_open_on_from_the_settings_tab(self):
        app = self.open_app()
        control = self.tabs["Settings"].controls["start_tab"]
        self.assertEqual(control.var.get(), "export")
        choices = [label for _v, label in control.others]
        self.assertEqual(choices, [t.title for t in app.tabs if t.title != "Settings"])
        self.assertEqual(choices[:3], ["Overview", "Film", "Watch Next"])
        control.other_var.set("Film")                       # a tab picked from the drop-down
        control._other()
        self.assertEqual((control.var.get(), self.saved()["start_tab"]), ("other", "Film"))
        control.buttons[1].invoke()                         # the tab you last had open
        self.assertEqual(self.saved()["start_tab"], "last")
        control.buttons[2].invoke()                         # 'This tab:' - the one showing in the drop-down
        self.assertEqual(self.saved()["start_tab"], "Film")
        prefs.set(app, "start_tab", "Library Doctor")       # changed elsewhere: shown here
        self.assertEqual((control.var.get(), control.other_var.get()), ("other", "Library Doctor"))
        with self.assertRaises(ValueError):
            prefs.set(app, "start_tab", "No Such Tab")
        self.assertIn("Takes effect the next time the app opens", control.help.cget("text"))

    # -- where the databases are ----------------------------------------------------------------------------------
    def test_the_newest_backup_in_the_backups_folder_opens(self):
        backups = os.path.join(self.dir, "Plex backups")
        os.makedirs(backups)
        shutil.copyfile(self.db, os.path.join(backups, "com.plexapp.plugins.library.db-2026-09-24"))
        newest = os.path.join(backups, "com.plexapp.plugins.library.db-2026-09-27")
        shutil.copyfile(self.db, newest)
        app = self.open_app({"backups_folder": backups, "db_path": self.db})
        app._pick_initial_db(None)
        self.assertEqual(app.loaded_path, newest)
        self.assertEqual(self.saved()["db_path"], newest)
        # an unreachable folder: one line in the log, and the usual places instead (the last database's folder)
        app.settings.update(backups_folder=os.path.join(self.dir, "unplugged drive"), db_path=self.db)
        app._pick_initial_db(None)
        self.assertEqual(app.loaded_path, self.db)
        self.assertEqual(app.log.get("1.0", "end").count("Couldn't reach the folder for Plex backups"), 1)
        # none chosen: as before
        app.settings["backups_folder"] = ""
        app._pick_initial_db(None)
        self.assertEqual(app.loaded_path, self.db)
        # the command line wins
        app.settings["backups_folder"] = backups
        app._pick_initial_db(self.db)
        self.assertEqual(app.loaded_path, self.db)
        self.ready()

    def test_choosing_the_backups_folder_opens_its_newest_now(self):
        backups = os.path.join(self.dir, "Plex backups")
        os.makedirs(backups)
        app = self.open_app()
        app.load_db(self.db)
        self.ready()
        control = self.tabs["Settings"].controls["backups_folder"]
        self.assertIsInstance(control, self.gui.BackupsFolderControl)
        self.assertTrue(control.look_now.instate(["disabled"]))    # (no folder chosen)
        self.dialog_answer = backups + os.sep                      # the Settings tab's Choose... button
        control._choose()                                          # (nothing in it yet)
        self.assertEqual(self.saved()["backups_folder"], os.path.normpath(backups))
        self.assertEqual(app.loaded_path, self.db)
        self.assertIn("No Plex database in", app.app_status_var.get())
        # a backup saved there meanwhile: 'Open the newest now'
        newest = os.path.join(backups, "com.plexapp.plugins.library.db-2026-09-27")
        shutil.copyfile(self.db, newest)
        self.assertTrue(control.look_now.instate(["!disabled"]))
        control.look_now.invoke()
        self.assertEqual(app.loaded_path, newest)
        self.ready()
        control.look_now.invoke()                                  # (again: it's open already)
        self.assertEqual(app.loaded_path, newest)
        self.assertIn("is open already", app.app_status_var.get())
        # choosing another folder opens its newest at once
        more = os.path.join(self.dir, "More backups")
        os.makedirs(more)
        other = os.path.join(more, "com.plexapp.plugins.library.db-2026-09-28")
        shutil.copyfile(self.db, other)
        self.dialog_answer = more
        control._choose()
        self.dialog_answer = ""
        self.assertEqual(app.loaded_path, other)
        self.ready()
        self.assertIsNone(app.look_in_backups_folder(os.path.join(self.dir, "nowhere")))
        self.assertIn("Couldn't find the folder", app.app_status_var.get())
        control.clear.invoke()                                     # none chosen: nothing is opened
        self.assertEqual(app.loaded_path, other)
        self.assertTrue(control.look_now.instate(["disabled"]))
        prefs.set(app, "backups_folder", backups)
        self.assertEqual(app.loaded_path, newest)
        self.ready()
        # Browse... starts in the backups folder when there is one
        prefs.set(app, "backups_folder", backups)
        app.browse_db()
        self.assertEqual(self.shown[-1][1]["initialdir"], os.path.normpath(backups))
        prefs.set(app, "backups_folder", "")
        app.browse_db()
        self.assertEqual(self.shown[-1][1]["initialdir"], backups)  # (the open database's folder)

    # -- where exports go -----------------------------------------------------------------------------------------
    def test_the_folder_for_exports_updates_the_suggestion_not_your_own_path(self):
        exports = os.path.join(self.dir, "exports")
        elsewhere = os.path.join(self.dir, "elsewhere")
        os.makedirs(exports)
        os.makedirs(elsewhere)
        app = self.open_app()
        app.load_db(self.db)
        name = "Projectionist Movies 2026-09-20.xlsx"
        self.assertEqual(app.out_var.get(), os.path.join(os.path.dirname(self.db), name))
        prefs.set(app, "out_dir", exports)                         # (the Settings tab): the suggestion follows
        self.assertEqual(app.out_var.get(), os.path.join(exports, name))
        own = os.path.join(self.dir, "mine.xlsx")
        app.out_var.set(own)                                       # a path of your own...
        prefs.set(app, "out_dir", elsewhere)
        self.assertEqual(app.out_var.get(), own)                   # ...is left alone
        app.out_var.set("")                                        # an empty box gets the suggestion
        prefs.set(app, "out_dir", exports)
        self.assertEqual(app.out_var.get(), os.path.join(exports, name))
        prefs.reset(app, ["out_dir"])                              # none chosen: next to the database
        self.assertEqual(app.out_var.get(), os.path.join(os.path.dirname(self.db), name))
        # the next database opened is suggested in the folder for exports
        prefs.set(app, "out_dir", exports)
        app.load_db(self.db)
        self.assertEqual(app.out_var.get(), os.path.join(exports, name))
        self.ready()

    # -- the spreadsheet program ----------------------------------------------------------------------------------
    def test_every_tab_opens_spreadsheets_in_the_chosen_program(self):
        from projectionist.files import DEFAULT_APP
        app = self.open_app()
        app.apps = [("LibreOffice Calc", r"C:\LO\scalc.exe"), ("Microsoft Excel", r"C:\XL\excel.exe"),
                    (DEFAULT_APP, None)]
        path = os.path.join(self.dir, "list.csv")
        open(path, "w").close()
        self.assertEqual(prefs.get(app, "open_with"), "LibreOffice Calc")     # (the first found)
        app.open_spreadsheet(path)
        prefs.set(app, "open_with", "Microsoft Excel")             # the Settings tab...
        self.assertEqual(app.open_with_var.get(), "Microsoft Excel")          # ...and the Export tab agree
        app.open_spreadsheet(path)
        app.open_with_var.set(DEFAULT_APP)                         # the Export tab's drop-down
        self.assertEqual(self.saved()["open_with"], DEFAULT_APP)
        app.last_output = path
        app.open_output()
        self.assertEqual(self.opened, [(path, r"C:\LO\scalc.exe"), (path, r"C:\XL\excel.exe"), (path, None)])

        def broken(p, program):
            raise OSError("not found")
        with mock.patch.object(self.gui, "open_spreadsheet", broken):
            with self.assertRaises(OSError):                       # (a tab says so in its own words)
                app.open_spreadsheet(path)
            self.assertEqual(self.shown, [])
            app.open_output()                                      # (the Export tab's button: a message)
        self.assertEqual(self.shown[-1][0], "error")
        self.assertIn(path, self.shown[-1][2])
        self.assertIn(DEFAULT_APP, self.shown[-1][2])
        # the Library Doctor opens its saved lists through the window, in the same program
        from projectionist.ui import doctor
        if hasattr(doctor, "open_saved"):
            prefs.set(app, "open_with", "Microsoft Excel")
            self.assertIsNone(doctor.open_saved(app, path))
            self.assertEqual(self.opened[-1], (path, r"C:\XL\excel.exe"))

    # -- also count as seen ---------------------------------------------------------------------------------------
    def test_the_accounts_to_count_as_seen(self):
        from tkinter import ttk
        app = self.open_app({"seen_accounts": [999]})              # (ticked for another database)
        control = self.tabs["Settings"].controls["seen_accounts"]
        self.assertEqual(control.vars, {})
        self.assertIn("once the collection has been read", control.note.cget("text"))
        self.assertTrue(control.untick.instate(["!disabled"]))
        app.load_db(self.db)
        self.ready()
        self.assertEqual(list(control.vars), [42])
        ticks = [w for w in widgets(control.ticks) if isinstance(w, ttk.Checkbutton)]
        self.assertEqual([str(w.cget("text")) for w in ticks], ["friend  (1 film)"])
        self.assertIn("1 ticked account is not in this database", control.note.cget("text"))
        heard = []
        for tab in app.tabs:                                       # (every tab hears of a change)
            tab.preference_changed = lambda key, value, tab=tab, was=tab.preference_changed: (
                heard.append((tab.title, key, value)), was(key, value))
        tick_count = len(heard)
        ticks[0].invoke()
        self.assertEqual(self.saved()["seen_accounts"], [42, 999])
        self.assertEqual({title for title, key, value in heard[tick_count:] if value == [42, 999]},
                         set(self.tabs))
        self.assertTrue(app.catalog.films[next(k for k, f in app.catalog.films.items()
                                               if f.title == "The Matrix")].seen(prefs.get(app, "seen_accounts")))
        ticks[0].invoke()
        self.assertEqual(self.saved()["seen_accounts"], [999])
        control.untick.invoke()
        self.assertEqual(self.saved()["seen_accounts"], [])
        self.assertTrue(control.untick.instate(["disabled"]))
        self.assertEqual(control.note.cget("text"), "")
        prefs.set(app, "seen_accounts", [42])                      # changed elsewhere: ticked here
        self.assertTrue(control.vars[42].get())
        # another database: the tick boxes are made again
        before = control.made_for
        app.load_db(self.db)
        self.ready()
        self.assertIsNot(control.made_for, before)
        self.assertTrue(control.vars[42].get())

    def test_catalog_watchers_are_held_weakly(self):
        app = self.open_app()
        calls = []

        class Watcher:
            def changed(self, catalog, state):
                calls.append(state)
        w = Watcher()
        app.watch_catalog(w.changed)
        app.watch_catalog(lambda catalog, state: calls.append("plain " + state))
        count = len(app._catalog_watchers)
        app._set_catalog(None, "loading")
        self.assertEqual(calls, ["loading", "plain loading"])
        del w
        gc.collect()
        app._set_catalog(None, "none")
        self.assertEqual(calls[2:], ["plain none"])
        self.assertEqual(len(app._catalog_watchers), count - 1)


if __name__ == "__main__":
    unittest.main()
