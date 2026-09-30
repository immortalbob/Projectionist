"""Polish: the app's icon (projectionist/appicon.py, projectionist/assets, tools/make_icon.py) and opening the newest
Plex backup when the app starts (projectionist/files.py, gui.App._pick_initial_db, Settings > Starting up > When the
app opens, use).

Everything runs headless: Tk roots are withdrawn and never shown; message boxes, file dialogs, menus and opening
files are stubbed; the settings file, the app's own folder and every backup folder are temporary ones."""

import gc
import io
import json
import os
import shutil
import sqlite3
import struct
import subprocess
import sys
import tarfile
import tempfile
import time
import types
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from projectionist import appicon, files, prefs  # noqa: E402
from projectionist.files import (  # noqa: E402
    backup_order, find_newest_database, is_candidate_database, is_newer, is_partial_copy, newest_backup, same_file)
from test_projectionist import build_fixture  # noqa: E402
from support import safe_extract  # noqa: E402

DB = "com.plexapp.plugins.library.db-{}"
HOUR = 3600


def hidden_root():
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()                       # headless: never shown
    return root


def age(path, seconds):
    """Make a file look last changed `seconds` ago."""
    when = time.time() - seconds
    os.utime(path, (when, when))
    return path


class Backups:
    """A folder of fake Plex backups, all copies of one small fixture database (with no free pages, so its last
    page is never blank)."""

    _source = None

    @classmethod
    def source(cls):
        if cls._source is None:
            cls._dir = tempfile.TemporaryDirectory()
            cls._source = os.path.join(cls._dir.name, "fixture.db")
            build_fixture(cls._source)
            con = sqlite3.connect(cls._source)
            con.execute("VACUUM")
            con.close()
        return cls._source

    @classmethod
    def add(cls, folder, date, seconds_ago=HOUR, name=None):
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, name or DB.format(date))
        shutil.copyfile(cls.source(), path)
        return age(path, seconds_ago)


# ---------------------------------------------------------------------------------------------------------
# Finding the newest backup (files.py)
# ---------------------------------------------------------------------------------------------------------
class NewestBackupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def data(self):
        with open(Backups.source(), "rb") as f:
            return f.read()

    def test_never_the_blobs_database_side_files_or_partial_copies(self):
        good = Backups.add(self.dir, "2026-09-25")
        Backups.add(self.dir, "2026-09-19")
        data = self.data()
        # all of these are newer, and all but the cut-short ones would open as a Plex database
        Backups.add(self.dir, None, name="com.plexapp.plugins.library.blobs.db-2026-10-05")
        Backups.add(self.dir, None, name=DB.format("2026-10-05") + "-shm")
        Backups.add(self.dir, None, name=DB.format("2026-10-05") + "-wal")
        Backups.add(self.dir, None, name=DB.format("2026-10-06") + ".crdownload")
        Backups.add(self.dir, None, name=DB.format("2026-10-06") + ".part")
        Backups.add(self.dir, None, name="." + DB.format("2026-10-06") + ".Xy12Ab")        # rsync, mid-copy
        Backups.add(self.dir, None, name="~$" + DB.format("2026-10-06"))
        with open(os.path.join(self.dir, DB.format("2026-10-01")), "wb") as f:           # a copy cut short
            f.write(data[:len(data) // 2])
        with open(os.path.join(self.dir, DB.format("2026-10-02")), "wb") as f:           # a copy still going
            f.write(data[:len(data) // 2] + b"\0" * (len(data) - len(data) // 2))
        self.assertEqual(find_newest_database(self.dir), good)
        self.assertEqual(newest_backup([self.dir]), (good, self.dir))
        for name in ("com.plexapp.plugins.library.blobs.db-2026-10-05", DB.format("2026-10-05") + "-wal",
                     DB.format("2026-10-05") + "-shm", DB.format("x") + ".crdownload", DB.format("x") + ".partial",
                     DB.format("x") + ".download", DB.format("x") + ".filepart", "." + DB.format("x"),
                     "~$" + DB.format("x")):
            self.assertFalse(is_candidate_database(name), name)
        self.assertTrue(is_candidate_database(DB.format("2026-10-05")))
        self.assertTrue(is_candidate_database("Plex Media Server Databases_2026-10-05_10-00-00.zip"))
        self.assertFalse(is_candidate_database("Plex Media Server Databases_2026-10-05_10-00-00.zip.crdownload"))

    def test_partial_copies(self):
        data = self.data()
        page_size = int.from_bytes(data[16:18], "big")
        whole = Backups.add(self.dir, "2026-09-25", seconds_ago=0)                      # (just saved: still whole)
        self.assertFalse(is_partial_copy(whole))
        cut = os.path.join(self.dir, DB.format("2026-10-01"))
        for length in (len(data) - page_size, len(data) // 2, 100, 40):                   # shorter than it says
            with open(cut, "wb") as f:
                f.write(data[:length])
            self.assertTrue(is_partial_copy(cut), length)
        going = os.path.join(self.dir, DB.format("2026-10-02"))                           # full size, filling in
        with open(going, "wb") as f:
            f.write(data[:len(data) // 2] + b"\0" * (len(data) - len(data) // 2))
        self.assertTrue(is_partial_copy(going))
        self.assertTrue(is_partial_copy(age(going, files.STILL_WRITING - 30)))
        # not changed for a while: a blank end is no sign of a copy still going (a copy that stopped part-way
        # long ago fails when it's opened, and the one before it is used)
        self.assertFalse(is_partial_copy(age(going, files.STILL_WRITING + 30)))
        self.assertTrue(is_partial_copy(whole + ".part"))                               # (by its name)
        self.assertTrue(is_partial_copy(os.path.join(self.dir, "." + DB.format("2026-10-02"))))
        # not SQLite at all (a zip, a text file): not a partial database - other checks turn those away
        other = os.path.join(self.dir, "notes.db")
        with open(other, "wb") as f:
            f.write(b"PK\x03\x04" + b"\0" * 200)
        self.assertFalse(is_partial_copy(other))
        self.assertFalse(is_partial_copy(os.path.join(self.dir, "not there")))

    def test_a_blank_page_on_the_free_list_is_not_a_partial_copy(self):
        """A database just saved whose last page is a blank free page (secure_delete) is whole."""
        path = Backups.add(self.dir, "2026-09-27", seconds_ago=0)
        con = sqlite3.connect(path)
        con.execute("PRAGMA secure_delete = ON")
        con.execute("CREATE TABLE spare (x BLOB)")
        con.execute("INSERT INTO spare VALUES (zeroblob(40000))")
        con.commit()
        con.execute("DROP TABLE spare")
        con.commit()
        con.close()
        with open(path, "rb") as f:
            data = f.read()
        page_size = int.from_bytes(data[16:18], "big")
        self.assertGreater(int.from_bytes(data[36:40], "big"), 0)          # (pages on the free list...)
        self.assertFalse(data[-page_size:].strip(b"\0"))                   # (...and the last one blank)
        self.assertFalse(is_partial_copy(path))
        self.assertEqual(find_newest_database(self.dir), path)

    def test_newest_by_the_date_in_the_name_then_the_time_changed(self):
        old = Backups.add(self.dir, "2026-09-25", seconds_ago=60)
        new = Backups.add(self.dir, "2026-10-02", seconds_ago=5 * HOUR)
        self.assertTrue(is_newer(new, old))
        self.assertFalse(is_newer(old, new))
        self.assertFalse(is_newer(new, new))
        self.assertEqual(backup_order(new)[0], "2026-10-02")
        # the same date: the one changed later
        copy = Backups.add(os.path.join(self.dir, "copy"), "2026-10-02", seconds_ago=60)
        self.assertTrue(is_newer(copy, new))
        # no date in the name: the day it was changed (today - after 2026-09-25, before the one dated 2026-10-02)
        live = Backups.add(os.path.join(self.dir, "live"), None, seconds_ago=0, name="com.plexapp.plugins.library.db")
        Backups.add(os.path.join(self.dir, "live"), "2026-09-25", seconds_ago=60)
        self.assertEqual(backup_order(live)[0], time.strftime("%Y-%m-%d"))
        self.assertTrue(is_newer(live, old))
        self.assertEqual(find_newest_database(os.path.join(self.dir, "live")), live)
        self.assertEqual(find_newest_database(self.dir), new)
        self.assertEqual(backup_order(os.path.join(self.dir, "gone"))[1], 0.0)

    def test_same_file(self):
        path = Backups.add(self.dir, "2026-09-25")
        self.assertTrue(same_file(path, os.path.join(self.dir, ".", os.path.basename(path))))
        if sys.platform == "win32":
            self.assertTrue(same_file(path, path.upper()))
        self.assertFalse(same_file(path, Backups.add(self.dir, "2026-09-26")))
        self.assertFalse(same_file(path, None))
        self.assertFalse(same_file("", ""))

    def test_the_first_folder_with_a_backup(self):
        first, second = os.path.join(self.dir, "first"), os.path.join(self.dir, "second")
        os.makedirs(first)
        with open(os.path.join(first, "com.plexapp.plugins.library.blobs.db-2026-10-09"), "wb") as f:
            f.write(self.data())                                    # (only the blobs database: none here)
        found = Backups.add(second, "2026-09-20")
        Backups.add(self.dir, "2026-10-01")
        self.assertEqual(newest_backup(["", os.path.join(self.dir, "unplugged"), first, second, self.dir]),
                         (found, second))
        self.assertEqual(newest_backup([self.dir, self.dir + os.sep]), (os.path.join(self.dir, DB.format("2026-10-01")),
                                                                        self.dir))
        self.assertEqual(newest_backup([first, None, ""]), (None, None))


# ---------------------------------------------------------------------------------------------------------
# Opening the newest backup when the app starts (the real window, never shown)
# ---------------------------------------------------------------------------------------------------------
class StartUpTest(unittest.TestCase):
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
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.backups = os.path.join(self.dir, "Plex backups")
        self.elsewhere = os.path.join(self.dir, "elsewhere")
        self.shown = []
        self.stubs = [
            mock.patch.object(paint.Interaction, "_choose", lambda *a, **k: None),
            mock.patch.object(tk.Menu, "tk_popup", lambda *a, **k: None),
            mock.patch.object(tk.Menu, "post", lambda *a, **k: None),
            mock.patch.object(messagebox, "showerror", lambda *a, **k: self.shown.append(a)),
            mock.patch.object(messagebox, "showwarning", lambda *a, **k: self.shown.append(a)),
            mock.patch.object(messagebox, "askyesno", lambda *a, **k: True),
            mock.patch.object(filedialog, "askopenfilename", lambda *a, **k: ""),
            mock.patch.object(filedialog, "asksaveasfilename", lambda *a, **k: ""),
            mock.patch.object(filedialog, "askdirectory", lambda *a, **k: ""),
            mock.patch.object(gui, "open_spreadsheet", lambda *a, **k: None),
            mock.patch.object(gui, "_open_path", lambda *a, **k: None),
            # the settings file, and the app's own folder (the real one may hold a real Plex backup)
            mock.patch.multiple(gui, SETTINGS_FILE=os.path.join(self.dir, "settings.json"), SETTINGS_DIR=self.dir,
                                APP_DIR=os.path.join(self.dir, "app")),
        ]
        for s in self.stubs:
            s.start()
        os.makedirs(os.path.join(self.dir, "app"))
        self.app = None

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
        self.app = self.root = None
        gc.collect()                      # (here, on this thread: never on a later test's job thread)
        from projectionist.ui import theme as T
        T.use("light")
        self.tmp.cleanup()

    def open_app(self, **settings):
        with open(self.gui.SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f)
        self.app = self.gui.App(self.root, None)
        self.root.after_cancel(self.app._timers.pop("pick"))      # (each test picks when it's ready)
        return self.app

    def start(self, **settings):
        """The window's start-up choice, with these settings. -> the database it opened."""
        self.app.settings.clear()
        self.app.settings.update(settings)
        self.app._start_note = None
        self.app.set_status("")
        self.app._pick_initial_db(None)
        return self.app.loaded_path

    def ready(self, timeout=60):
        deadline = time.time() + timeout
        while not (self.app.catalog_state == "ready" and not self.app._held and self.app.queue.empty()):
            self.root.update()
            time.sleep(0.005)
            self.assertLess(time.time(), deadline, "timed out")
        self.root.update()

    def log(self):
        return self.app.log.get("1.0", "end")

    def test_the_newest_backup_opens_when_its_newer_than_the_one_used_last(self):
        last = Backups.add(self.backups, "2026-09-25")
        newest = Backups.add(self.backups, "2026-10-02")
        Backups.add(self.backups, "2026-09-19")
        app = self.open_app()
        self.assertEqual(self.start(backups_folder=self.backups, db_path=last), newest)
        note = "Opened the newest backup, 2026-10-02 - you last used 2026-09-25."
        self.assertEqual(app.app_status_var.get(), note)
        self.assertIn(note, self.log())
        self.assertEqual(app.settings["db_path"], newest)                 # (the one used last, from now on)
        self.ready()
        status = app.app_status_var.get()                                 # still there once the collection's read
        self.assertTrue(status.startswith(note), status)
        self.assertIn(os.path.basename(newest), status)
        self.assertIn("films", status)
        self.assertIsNone(app._start_note)
        # the next start: it's the one used last now, so nothing to say
        self.assertEqual(self.start(backups_folder=self.backups, db_path=newest), newest)
        self.assertNotIn("Opened the newest", app.app_status_var.get())
        self.assertEqual(self.log().count("Opened the newest backup"), 1)
        self.ready()
        # no folder for backups chosen: the last database's folder is looked in
        self.assertEqual(self.start(db_path=last), newest)
        self.assertIn("you last used 2026-09-25", app.app_status_var.get())
        # the newest has the same date, saved later (a second copy): said so, without two dates the same
        other = Backups.add(self.elsewhere, "2026-10-02", seconds_ago=60)
        self.assertEqual(self.start(backups_folder=self.elsewhere, db_path=newest), other)
        self.assertEqual(app.app_status_var.get(),
                         "Opened the newest backup, 2026-10-02 - saved later than the one you last used.")
        self.ready()
        self.assertEqual(self.shown, [])

    def test_the_one_used_last_when_nothing_is_newer(self):
        Backups.add(self.backups, "2026-09-19")
        newest = Backups.add(self.backups, "2026-09-25")
        last = Backups.add(self.elsewhere, "2026-09-28")                  # (picked by hand, from elsewhere)
        app = self.open_app()
        self.assertEqual(self.start(backups_folder=self.backups, db_path=last), last)
        self.assertNotIn("Opened the newest", app.app_status_var.get())
        # the newest is the one used last
        self.assertEqual(self.start(backups_folder=self.backups, db_path=newest), newest)
        self.assertNotIn("Opened the newest", app.app_status_var.get())
        self.assertNotIn("Picked the newest", self.log())
        # newer files that are no Plex backup, or not all there, are passed over
        Backups.add(self.backups, None, name="com.plexapp.plugins.library.blobs.db-2026-10-05")
        Backups.add(self.backups, None, name=DB.format("2026-10-05") + "-wal")
        Backups.add(self.backups, None, name=DB.format("2026-10-05") + ".crdownload")
        with open(Backups.source(), "rb") as f:
            data = f.read()
        with open(os.path.join(self.backups, DB.format("2026-10-06")), "wb") as f:
            f.write(data[:len(data) // 2] + b"\0" * (len(data) - len(data) // 2))       # (a copy still going)
        self.assertEqual(self.start(backups_folder=self.backups, db_path=newest), newest)
        self.assertNotIn("Opened the newest", app.app_status_var.get())
        # the one used last is gone: the newest, with nothing to compare it with
        self.ready()                                                      # (so no one has it open)
        os.remove(last)
        self.assertEqual(self.start(backups_folder=self.backups, db_path=last), newest)
        self.assertNotIn("Opened the newest", app.app_status_var.get())
        self.assertIn("Picked the newest database in", self.log())
        self.ready()

    def test_the_backup_used_last_when_the_setting_says_so(self):
        last = Backups.add(self.backups, "2026-09-25")
        newest = Backups.add(self.backups, "2026-10-02")
        app = self.open_app()
        self.assertEqual(self.start(backups_folder=self.backups, db_path=last, start_database="last"), last)
        self.assertNotIn("Opened the newest", app.app_status_var.get())
        # gone, or it no longer opens: the newest instead
        self.ready()                                                      # (so no one has it open)
        os.remove(last)
        self.assertEqual(self.start(backups_folder=self.backups, db_path=last, start_database="last"), newest)
        with open(last, "wb") as f:
            f.write(b"not a database at all, just text" * 100)
        self.assertEqual(self.start(backups_folder=self.backups, db_path=last, start_database="last"), newest)
        self.ready()

    def test_nothing_to_open(self):
        app = self.open_app()
        self.assertIsNone(self.start(backups_folder=self.backups))
        self.assertIn("Click Browse", app.db_info_var.get())
        # the one used last no longer opens, and there's nothing else: its line says why (not 'Click Browse')
        os.makedirs(self.elsewhere)
        broken = os.path.join(self.elsewhere, DB.format("2026-09-25"))
        with open(broken, "wb") as f:
            f.write(b"not a database at all, just text" * 100)
        self.assertIsNone(self.start(db_path=broken))
        self.assertNotIn("Click Browse", app.db_info_var.get())
        self.assertTrue(app.db_info_var.get())

    def test_the_setting(self):
        p = prefs.pref("start_database")
        self.assertEqual((p.section, p.label, p.kind, p.default, p.live),
                         ("Starting up", "When the app opens, use", "choice", "newest", False))
        self.assertEqual(p.choices, [("newest", "The newest backup in that folder"),
                                     ("last", "The backup I used last")])
        for _key, label in p.choices:                                   # (the help says what each choice does)
            self.assertIn(label.replace(" in that folder", "") + ": it opens", p.help)
        keys = [q.key for q in prefs.shown("Starting up")]
        self.assertEqual(keys.index("start_database"), keys.index("backups_folder") + 1)     # ('that folder')
        self.assertEqual(prefs.get({}, "start_database"), "newest")
        self.assertEqual(prefs.get({"start_database": "oldest"}, "start_database"), "newest")
        app = self.open_app()
        tab = next(t for t in app.tabs if t.title == "Settings")
        control = tab.controls["start_database"]
        self.assertEqual([b.cget("text") for b in control.buttons],
                         ["The newest backup in that folder", "The backup I used last"])
        self.assertEqual(control.var.get(), "newest")
        control.buttons[1].invoke()
        with open(self.gui.SETTINGS_FILE, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["start_database"], "last")
        self.assertIn("Takes effect the next time the app opens", control.help.cget("text"))


# ---------------------------------------------------------------------------------------------------------
# The icon
# ---------------------------------------------------------------------------------------------------------
def ico_frames(data: bytes) -> dict:
    """{size: the frame's first bytes} from an .ico file."""
    reserved, kind, count = struct.unpack("<HHH", data[:6])
    assert (reserved, kind) == (0, 1)
    frames = {}
    for i in range(count):
        w, h, _c, _r, planes, bits, length, offset = struct.unpack("<BBBBHHII", data[6 + 16 * i:22 + 16 * i])
        assert (w or 256) == (h or 256) and bits == 32 and offset + length <= len(data)
        frames[w or 256] = data[offset:offset + length]
    return frames


def png_size(path) -> tuple:
    with open(path, "rb") as f:
        head = f.read(24)
    assert head[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", head[16:24])


class IconFilesTest(unittest.TestCase):
    def test_the_ico_has_every_size_windows_asks_for(self):
        with open(appicon.ICO, "rb") as f:
            frames = ico_frames(f.read())
        self.assertEqual(sorted(frames), [16, 20, 24, 32, 40, 48, 64, 128, 256])
        for size, frame in frames.items():
            if size < 256:                     # bitmaps, as Tk reads them: a 40-byte header, 32 bits, height x2
                size_, w, h, _planes, bits = struct.unpack("<IiiHH", frame[:16])
                self.assertEqual((size_, w, h, bits), (40, size, size * 2, 32), size)
            else:
                self.assertEqual(frame[:8], b"\x89PNG\r\n\x1a\n")

    def test_the_pngs_for_iconphoto(self):
        self.assertEqual(appicon.PNG_SIZES, (256, 48, 32, 16))
        for size in appicon.PNG_SIZES:
            self.assertEqual(png_size(appicon.png(size)), (size, size))
        self.assertEqual(os.path.dirname(appicon.ICO), appicon.ASSETS)
        self.assertEqual(appicon.ASSETS, os.path.join(ROOT, "projectionist", "assets"))

    def test_found_from_an_unpacked_source_tarball(self):
        """The icon files travel with the package: a tarball of the source, unpacked anywhere, finds them."""
        with tempfile.TemporaryDirectory() as d:
            tarball = os.path.join(d, "projectionist-src.tar.gz")
            with tarfile.open(tarball, "w:gz") as tar:
                tar.add(os.path.join(ROOT, "projectionist"), arcname="Projectionist/projectionist",
                        filter=lambda info: None if "__pycache__" in info.name else info)
                tar.add(os.path.join(ROOT, "Projectionist.pyw"), arcname="Projectionist/Projectionist.pyw")
            unpacked = os.path.join(d, "unpacked")
            with tarfile.open(tarball) as tar:
                safe_extract(tar, unpacked)
            folder = os.path.join(unpacked, "Projectionist")
            code = ("import json, os, sys; sys.path.insert(0, sys.argv[1]); from projectionist import appicon as a; "
                    "print(json.dumps([a.ICO, [os.path.isfile(a.png(s)) for s in a.PNG_SIZES], "
                    "os.path.isfile(a.ICO)]))")
            out = subprocess.run([sys.executable, "-B", "-c", code, folder], capture_output=True, text=True,
                                 timeout=60, cwd=d)
            self.assertEqual(out.returncode, 0, out.stderr)
            ico, pngs, ico_there = json.loads(out.stdout)
            self.assertTrue(os.path.normcase(ico).startswith(os.path.normcase(folder)), ico)
            self.assertEqual((pngs, ico_there), ([True] * 4, True))

    def test_the_launcher_names_the_app_to_windows_too(self):
        with open(os.path.join(ROOT, "Projectionist.pyw"), encoding="utf-8") as f:
            source = f.read()
        compile(source, "Projectionist.pyw", "exec")
        self.assertLess(source.index("appicon.set_app_id()"), source.index("tkinter.Tk()"))
        self.assertIn("appicon.apply(root)", source)


class IconOnWindowsTest(unittest.TestCase):
    def setUp(self):
        try:
            self.root = hidden_root()
        except Exception as exc:          # noqa: BLE001
            self.skipTest(f"Tk unavailable: {exc}")

    def tearDown(self):
        self.root.destroy()
        self.root = None
        gc.collect()

    def test_apply_puts_it_on_every_window(self):
        self.assertTrue(appicon.apply(self.root))                 # (for real, on a window never shown)
        calls = []
        root = self.root
        with mock.patch.object(root, "iconphoto", lambda *a: calls.append(("iconphoto",) + a)), \
                mock.patch.object(root, "iconbitmap", lambda *a, **k: calls.append(("iconbitmap", a, k))):
            self.assertTrue(appicon.apply(root))
        self.assertEqual(calls[0][0], "iconphoto")
        self.assertIs(calls[0][1], True)                            # (the default for every later window)
        self.assertEqual([(img.width(), img.height()) for img in calls[0][2:]],
                         [(s, s) for s in appicon.PNG_SIZES])
        if sys.platform == "win32":                                 # the .ico last: it wins on Windows
            self.assertEqual(calls[1:], [("iconbitmap", (), {"default": appicon.ICO})])
        # once the window is on screen: its own icon too (Windows' title bar gets the .ico's own 16 px picture)
        calls.clear()
        with mock.patch.object(root, "iconphoto", lambda *a: calls.append(("iconphoto",) + a)), \
                mock.patch.object(root, "iconbitmap", lambda *a, **k: calls.append(("iconbitmap", a, k))):
            self.assertTrue(appicon.apply(root, shown=True))
        self.assertEqual(calls[0][:2], ("iconphoto", True))
        if sys.platform == "win32":
            self.assertEqual(calls[1:], [("iconbitmap", (), {"default": appicon.ICO}),
                                         ("iconbitmap", (), {"bitmap": appicon.ICO})])

    def test_shown_windows_get_their_own_icon_before_the_title_bar_is_drawn(self):
        from projectionist import gui
        calls = []

        class FakeRoot:
            def attributes(self, *a):
                calls.append(("alpha", a[1]))

            def deiconify(self):
                calls.append(("show",))

            def update_idletasks(self):
                calls.append(("idle",))
        with mock.patch.object(gui.theme, "title_bar", lambda root: calls.append(("title bar",))), \
                mock.patch.object(gui.appicon, "apply", lambda root, shown=False: calls.append(("icon", shown))):
            gui._show(FakeRoot())
        self.assertEqual(calls, [("alpha", 0.0), ("show",), ("idle",), ("icon", True), ("title bar",),
                                 ("alpha", 1.0)])
        calls.clear()
        with mock.patch.object(gui.theme, "title_bar", lambda root: calls.append(("title bar",))):
            gui._show(FakeRoot())                                   # (the real one, on a window it can't use)
        self.assertEqual(calls, [("alpha", 0.0), ("show",), ("idle",), ("title bar",), ("alpha", 1.0)])

    def test_never_fails(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.multiple(appicon, ASSETS=d, ICO=os.path.join(d, "projectionist.ico")):
                self.assertFalse(appicon.apply(self.root))          # (no pictures: the window as it was)
            with open(os.path.join(d, "projectionist-16.png"), "wb") as f:
                f.write(b"not a picture")
            with mock.patch.multiple(appicon, ASSETS=d, ICO=os.path.join(d, "projectionist.ico")):
                self.assertFalse(appicon.apply(self.root))          # (a picture Tk can't read: passed over)
            with open(os.path.join(d, "projectionist.ico"), "wb") as f:
                f.write(b"not an icon")
            with mock.patch.multiple(appicon, ASSETS=d, ICO=os.path.join(d, "projectionist.ico")):
                self.assertIn(appicon.apply(self.root), (True, False))     # (whatever Tk makes of it: no error)
            with mock.patch.object(self.root, "iconphoto", side_effect=RuntimeError("main thread is not in main loop")):
                self.assertIn(appicon.apply(self.root), (True, False))

    def test_the_taskbar_name(self):
        self.assertEqual(appicon.APP_ID, "Projectionist.App")
        if sys.platform == "win32":
            import ctypes
            self.assertTrue(appicon.set_app_id())                  # (this test process's own name: harmless)
            got = ctypes.c_wchar_p()
            get = ctypes.windll.shell32.GetCurrentProcessExplicitAppUserModelID
            get.argtypes = [ctypes.POINTER(ctypes.c_wchar_p)]
            self.assertEqual(get(ctypes.byref(got)), 0)
            self.assertEqual(got.value, "Projectionist.App")
            ctypes.windll.ole32.CoTaskMemFree(got)

            class Broken:
                def __getattr__(self, name):
                    raise OSError("no shell32 here")
            with mock.patch.object(ctypes, "windll", Broken()):
                self.assertFalse(appicon.set_app_id())
        with mock.patch.object(sys, "platform", "linux"):
            self.assertFalse(appicon.set_app_id())

    def test_main_names_the_app_before_its_window_and_puts_the_icon_on_it(self):
        """gui.main with the window, the timer and showing it stubbed."""
        from projectionist import gui
        order = []
        root = self.root
        root.mainloop = lambda *a, **k: None

        made = {}

        def make_root(*args, **kwargs):
            order.append("Tk")
            made.update(kwargs)
            return root
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.multiple(gui, SETTINGS_FILE=os.path.join(d, "settings.json"), SETTINGS_DIR=d,
                                     OLD_SETTINGS_FILE=os.path.join(d, "old", "settings.json"),
                                     EARLIER_SETTINGS_FILES=[],
                                     App=lambda r, db: order.append("App"), _show=lambda r: None,
                                     _enable_dpi_awareness=lambda: None,
                                     _share_time_with_the_window=lambda: (lambda: None)), \
                    mock.patch.dict(sys.modules, {"xlsxwriter": sys.modules.get("xlsxwriter")
                                                  or types.ModuleType("xlsxwriter")}), \
                    mock.patch.object(gui, "messagebox") as box, \
                    mock.patch.object(gui.appicon, "set_app_id", lambda: order.append("app id")), \
                    mock.patch.object(gui.appicon, "apply", lambda r: order.append(("icon", r is root))), \
                    mock.patch.object(gui.tk, "Tk", make_root):
                self.assertEqual(gui.main(), 0)
            box.showerror.assert_not_called()
        self.assertEqual(order, ["app id", "Tk", ("icon", True), "App"])
        # away from Windows the window's class is the app's (WM_CLASS), so projectionist.desktop is matched to it
        self.assertEqual(made, {} if sys.platform == "win32" else {"className": "Projectionist"})


class MakeIconTest(unittest.TestCase):
    """tools/make_icon.py (it needs Pillow, which the app itself never does)."""

    @classmethod
    def setUpClass(cls):
        try:
            import PIL  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("Pillow isn't installed (only the icon tool needs it)")
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import make_icon
        cls.M = make_icon

    def test_every_size(self):
        M = self.M
        for size in M.ICO_SIZES:
            img = M.draw(size)
            self.assertEqual((img.size, img.mode), ((size, size), "RGBA"), size)
            self.assertLess(img.getpixel((0, 0))[3], 255, size)                  # rounded corners
            self.assertEqual(img.getpixel((size // 2, size // 2))[3], 255, size)
        self.assertTrue(all(len(row) == 16 for row in M.PIXELS_16) and len(M.PIXELS_16) == 16)

    def test_the_small_sizes_are_drawn_on_whole_pixels(self):
        """16 px is drawn pixel by pixel; from 20 to 48 px the reels are round (square boxes) and an odd number of
        pixels across, so each hub is a pixel of its own; the reels never touch each other or the body, a pixel of
        tile shows round the big reel, and the tile sits in the middle."""
        M = self.M
        img = M.draw(16)
        alphas = [img.getpixel((x, y))[3] for x in range(16) for y in range(16)]
        self.assertTrue(all(a in (0, 150, 255) for a in alphas))
        for size in (20, 24, 32, 40, 48):
            lay = M.layout(size)
            tile, rear, front, body = lay["tile"], lay["rear"], lay["front"], lay["body"]
            self.assertTrue(all(float(v).is_integer() for v in rear + front + body + lay["lens"] + lay["glass"]))
            for reel in (rear, front):
                self.assertEqual(reel[2] - reel[0], reel[3] - reel[1], (size, reel))     # round, not an oval
                self.assertEqual((reel[2] - reel[0]) % 2, 1, (size, reel))
            self.assertLess(rear[2], front[0], size)                              # a gap between the reels
            self.assertLess(max(rear[3], front[3]), body[1], size)               # ...and above the body
            self.assertGreater(rear[0], tile[0], size)                           # (not on the tile's edge)
            self.assertGreater(rear[1], tile[1], size)
            self.assertEqual((tile[0], tile[1]), (size - tile[2], size - tile[3]), size)   # centred
        for size in (24, 40, 48):                                   # (the pictures drawn: the reel's edge is tile)
            img = M.draw(size)
            rear = M.layout(size)["rear"]
            y = (rear[1] + rear[3]) // 2
            self.assertEqual(img.getpixel((rear[0] - 1, y))[3], 255, size)

    def test_writes_the_ico_and_pngs(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            frames = self.M.write_icons(d)
            with open(os.path.join(d, "projectionist.ico"), "rb") as f:
                data = f.read()
            self.assertEqual(sorted(ico_frames(data)), sorted(frames))
            self.assertEqual(sorted(Image.open(io.BytesIO(data)).info["sizes"]),
                             [(s, s) for s in self.M.ICO_SIZES])
            for size in self.M.PNG_SIZES:
                self.assertEqual(png_size(os.path.join(d, f"projectionist-{size}.png")), (size, size))
            preview = self.M.preview(frames, os.path.join(d, "preview.png"))
            with Image.open(preview) as sheet:
                self.assertGreater(sheet.size[0], 1000)


if __name__ == "__main__":
    unittest.main()
