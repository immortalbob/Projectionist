"""The Windows build (tools/build_exe.py) and what the built Projectionist.exe runs: its command line
(projectionist/launch.py) and its self-test (projectionist/selftest.py) - the real window built hidden over the
fixture database, every tab visited, an export - and the app's own folder when it's built.

Nothing here runs PyInstaller or starts a program (tools/build_exe.py does both, and checks what it built); the
specs and version details it writes are checked, and read by PyInstaller's own code where that's installed. No
window is shown."""

import contextlib
import io
import os
import sys
import tempfile
import tkinter
import unittest
from tkinter import messagebox
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import build_exe as B  # noqa: E402
import projectionist  # noqa: E402
from projectionist import gui, launch, selftest  # noqa: E402

try:
    import PyInstaller  # noqa: F401
    HAVE_PYINSTALLER = True
except ImportError:
    HAVE_PYINSTALLER = False


class SpecTests(unittest.TestCase):
    def test_windows_version_numbers(self):
        self.assertEqual(B.version_numbers("1.0.0"), (1, 0, 0, 0))
        self.assertEqual(B.version_numbers("2.13"), (2, 13, 0, 0))
        self.assertEqual(B.version_numbers("1.2b3.4.5.6"), (1, 2, 4, 5))
        self.assertEqual(B.version_numbers("99999"), (65535, 0, 0, 0))

    def test_every_module_of_the_app_is_named(self):
        names = B.package_modules()
        for name in ("projectionist", "projectionist.gui", "projectionist.launch", "projectionist.selftest",
                     "projectionist.ui", "projectionist.ui.overview", "projectionist.ui.settings"):
            self.assertIn(name, names)
        for tab in gui.TAB_MODULES:                   # (imported by name while the app runs)
            self.assertIn(f"projectionist.ui.{tab}", names)
        self.assertFalse([n for n in names if "__pycache__" in n or n.startswith(("tests", "tools"))])
        self.assertEqual(names, sorted(names))

    def test_the_specs(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec = B.app_spec("1.0.0", os.path.join(tmp, "v.txt"))
            compile(spec, "app.spec", "exec")
            self.assertIn("name='Projectionist'", spec)
            self.assertIn("name='Projectionist-1.0.0-win64'", spec)
            self.assertIn("console=False", spec)
            self.assertIn("'projectionist/assets'", spec)
            for left_out in ("PIL", "tests", "tools", "ssl"):
                self.assertIn(repr(left_out), spec)
            self.assertIn("_tcl_data/tzdata/", spec)
            onefile = B.onefile_spec("Uninstall", os.path.join(tmp, "v.txt"), B.UNINSTALLER_EXCLUDES, [])
            compile(onefile, "uninstall.spec", "exec")
            self.assertIn("'tkinter'", onefile)
            self.assertIn("installer.py", onefile)
            self.assertNotIn("'tkinter'", B.onefile_spec("Setup", "v.txt", B.INSTALLER_EXCLUDES, []))

    def test_the_version_details(self):
        text = B.version_info("1.0.0", "Projectionist Setup", "Projectionist-1.0.0-Setup.exe")
        for part in ("filevers=(1, 0, 0, 0)", "'ProductName', 'Projectionist'", "'ProductVersion', '1.0.0'",
                     "'FileDescription', 'Projectionist Setup'", "'OriginalFilename', 'Projectionist-1.0.0-Setup.exe'",
                     "'CompanyName', 'Projectionist contributors'", "MIT License"):
            self.assertIn(part, text)

    @unittest.skipUnless(HAVE_PYINSTALLER, "PyInstaller isn't installed")
    def test_pyinstaller_reads_the_version_details(self):
        from PyInstaller.utils.win32.versioninfo import load_version_info_from_text_file
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "version.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write(B.version_info("1.2.3", "Projectionist", "Projectionist.exe"))
            info = load_version_info_from_text_file(path)
        text = str(info)
        self.assertIn("ProductVersion", text)
        self.assertIn("1.2.3", text)
        self.assertEqual(info.ffi.fileVersionMS, (1 << 16) | 2)

    def test_what_the_app_archive_must_hold(self):
        everything = set(B.package_modules()) | {"xlsxwriter", "xlsxwriter.workbook", "tkinter"}
        self.assertEqual(B.check_app_modules(everything), [])
        problems = B.check_app_modules(everything - {"projectionist.ui.doctor", "xlsxwriter"} |
                                       {"PIL.Image", "tests.test_x", "unittest"})
        self.assertEqual(len(problems), 5, problems)
        self.assertIn("projectionist.ui.doctor", problems[0])

    def test_the_payload(self):
        import installer
        with tempfile.TemporaryDirectory() as tmp:
            app = os.path.join(tmp, "Projectionist-1.0.0-win64")
            os.makedirs(os.path.join(app, "_internal"))
            for name in ("Projectionist.exe", "LICENSE", os.path.join("_internal", "python314.dll")):
                with open(os.path.join(app, name), "wb") as f:
                    f.write(b"x" * 10)
            uninstaller = os.path.join(tmp, "Uninstall.exe")
            with open(uninstaller, "wb") as f:
                f.write(b"u" * 5)
            info = B.make_payload(app, uninstaller, os.path.join(tmp, "payload.zip"), "1.0.0", 1790640000)
            self.assertEqual(info, {"name": "Projectionist", "version": "1.0.0", "exe": "Projectionist.exe",
                                    "uninstaller": "Uninstall.exe", "files": 4, "size": 35})
            payload = installer.Payload(os.path.join(tmp, "payload.zip"))
            self.assertEqual(payload.files,
                             ["LICENSE", "Projectionist.exe", "Uninstall.exe", "_internal/python314.dll"])

    def test_the_date_every_build_carries(self):
        with mock.patch.dict(os.environ):
            os.environ["SOURCE_DATE_EPOCH"] = "1790640000"
            self.assertEqual(B.build_date(), 1790640000)
            os.environ["SOURCE_DATE_EPOCH"] = "2026-09-29"
            self.assertEqual(B.build_date(), 1790640000)
            del os.environ["SOURCE_DATE_EPOCH"]
            self.assertEqual(B.build_date(), B.make_sdist.collect(ROOT).newest)    # (the newest source file's)

    def test_only_on_windows_with_pyinstaller(self):
        with mock.patch.object(sys, "platform", "linux"):
            with self.assertRaises(B.BuildError):
                B.build(work=tempfile.gettempdir(), say=lambda *_a: None)
        with mock.patch.object(sys, "platform", "win32"), mock.patch.object(B, "pyinstaller_version", lambda: None):
            with self.assertRaises(B.BuildError):
                B.build(work=tempfile.gettempdir(), say=lambda *_a: None)

    def test_a_build_only_replaces_a_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            new, old = os.path.join(tmp, "new"), os.path.join(tmp, "Projectionist-1.0.0-win64")
            for folder in (new, old):
                os.makedirs(folder)
                with open(os.path.join(folder, "Projectionist.exe"), "w") as f:
                    f.write(folder)
            B._replace(new, old)
            with open(os.path.join(old, "Projectionist.exe")) as f:
                self.assertEqual(f.read(), new)
            other = os.path.join(tmp, "Someone's folder")
            os.makedirs(other)
            with self.assertRaises(B.BuildError):
                B._replace(old, other)
            self.assertTrue(os.path.isdir(old))


# ---------------------------------------------------------------------------------------------------------
class LaunchTests(unittest.TestCase):
    def say(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = launch.main(list(argv))
        return code, out.getvalue()

    def test_version_and_help(self):
        self.assertEqual(self.say("--version"), (0, f"Projectionist {projectionist.__version__}\n"))
        code, text = self.say("--help")
        self.assertEqual(code, 0)
        self.assertIn("--self-test REPORT", text)
        with mock.patch.object(sys, "stdout", None):              # (a windowed .exe from Explorer: nowhere to print)
            self.assertEqual(launch.main(["--version"]), 0)

    def test_the_window_with_or_without_a_database(self):
        opened = []
        with mock.patch.object(launch, "window", lambda db=None: opened.append(db) or 0):
            self.assertEqual(launch.main([]), 0)
            self.assertEqual(launch.main([r"D:\Plex\com.plexapp.plugins.library.db"]), 0)
            self.assertEqual(launch.main(["--unknown"]), 0)
        self.assertEqual(opened, [None, r"D:\Plex\com.plexapp.plugins.library.db", None])

    def test_a_window_that_cant_start_says_why(self):
        shown = []

        def broken(*_a):
            raise RuntimeError("no display")
        with mock.patch.object(gui, "main", broken), mock.patch.object(launch, "startup_failed", shown.append):
            self.assertEqual(launch.window(), 1)
        self.assertIn("RuntimeError: no display", shown[0])

    def test_a_self_test_with_wrong_options_shows_nothing(self):
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(launch.main(["--self-test"]), 2)
        self.assertIn("--self-test", err.getvalue())

    def test_the_apps_own_folder(self):
        self.assertEqual(gui.app_folder(frozen=False), ROOT)
        built = os.path.join(os.sep, "Programs", "Projectionist", "Projectionist.exe")
        self.assertEqual(gui.app_folder(frozen=True, executable=built), os.path.dirname(os.path.abspath(built)))
        self.assertEqual(gui.APP_DIR, ROOT)


# ---------------------------------------------------------------------------------------------------------
class SelfTestTests(unittest.TestCase):
    """The self-test itself, from the source, over the fixture database - hidden, as in the built app."""

    @classmethod
    def setUpClass(cls):
        try:
            root = tkinter.Tk()
            root.destroy()
        except tkinter.TclError as exc:
            raise unittest.SkipTest(f"Tk unavailable: {exc}")
        from test_projectionist import build_fixture
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = os.path.join(cls.tmp.name, "com.plexapp.plugins.library.db-2026-09-25")
        build_fixture(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.settings = gui.SETTINGS_FILE, gui.SETTINGS_DIR
        gui.SETTINGS_FILE = os.path.join(self.tmp.name, "never.json")       # (belt and braces: it uses its own)
        gui.SETTINGS_DIR = self.tmp.name
        self.addCleanup(self._put_back)
        self.report = os.path.join(self.tmp.name, "report.txt")

    def _put_back(self):
        gui.SETTINGS_FILE, gui.SETTINGS_DIR = self.settings

    def read(self):
        with open(self.report, encoding="utf-8") as f:
            return f.read()

    def test_it_passes_and_says_what_it_saw(self):
        before = (messagebox.showerror, messagebox.askyesno, tkinter.Wm.deiconify, tkinter.Menu.tk_popup,
                  gui.App._log)
        code = selftest.run(self.report, self.db)
        text = self.read()
        self.assertEqual(code, 0, text)
        self.assertTrue(text.rstrip().endswith("PASS"), text)
        for tab in ["Export", "Overview", "Film", "Watch Next", "Viewing", "Credits", "Six Degrees",
                    "Library Doctor", "Settings"]:
            self.assertRegex(text, rf"\n  {tab} +\d+\.\d\d s +[\d,]+ widgets", tab)
        for line in ("XlsxWriter:", "Icon files:  all 5 there", "Modules:     all", "never shown", "Collection:",
                     "Export:      ", "yours are neither read nor changed"):
            self.assertIn(line, text)
        self.assertIn(self.db, text)
        # everything it swapped is put back, and it never touched the settings file it was given
        self.assertEqual(before, (messagebox.showerror, messagebox.askyesno, tkinter.Wm.deiconify,
                                  tkinter.Menu.tk_popup, gui.App._log))
        self.assertEqual(gui.SETTINGS_FILE, os.path.join(self.tmp.name, "never.json"))
        self.assertFalse(os.path.exists(gui.SETTINGS_FILE))

    def test_a_tab_that_fails_fails_it(self):
        from projectionist.ui import credits

        def broken(tab):
            raise RuntimeError("the credits tab broke")
        with mock.patch.object(credits.Tab, "shown", broken):
            code = selftest.run(self.report, self.db, export=False)
        text = self.read()
        self.assertEqual(code, 1)
        self.assertIn("FAILED: the Credits tab reported 1 error(s)", text)
        self.assertIn("RuntimeError: the credits tab broke", text)
        self.assertRegex(text.rstrip().splitlines()[-1], r"^FAIL \(\d+ problems\)$")
        self.assertNotIn("Export:      ", text)
        self.assertRegex(text, r"\n  Six Degrees +\d")              # (the other tabs are still visited)

    def test_no_such_database(self):
        code = selftest.main([self.report, "--db", os.path.join(self.tmp.name, "missing.db")])
        self.assertEqual(code, 1)
        self.assertIn("FAILED: there's no database at", self.read())

    def test_nothing_it_would_have_shown_gets_through(self):
        """What the app would show or open during the check is stopped and reported instead."""
        from support import may_show_windows
        from projectionist import files
        originals = (messagebox.showerror, tkinter.Toplevel.__init__, tkinter.Wm.deiconify, files.open_spreadsheet)
        said = []
        patches = selftest.Patches()
        selftest._nothing_on_screen(patches, said)
        try:
            self.assertEqual(messagebox.showerror("Projectionist", "boom"), "ok")
            self.assertFalse(messagebox.askyesno("Projectionist", "Quit?"))
            with self.assertRaises(OSError):
                files.open_spreadsheet(self.report)
            self.assertIsNot(tkinter.Toplevel.__init__, originals[1])
            self.assertIsNone(tkinter.Wm.deiconify(object()))       # (does nothing)
            if may_show_windows():                                    # (a virtual screen: a window can be tried)
                root = tkinter.Tk()
                try:
                    root.withdraw()
                    top = tkinter.Toplevel(root)
                    top.deiconify()
                    root.update()
                    self.assertEqual(top.state(), "withdrawn")
                finally:
                    root.destroy()
        finally:
            patches.undo()
        self.assertEqual(said[:2], [("showerror", "boom"), ("askyesno", "Quit?")])
        self.assertEqual(said[2][0], "open")
        self.assertEqual(originals, (messagebox.showerror, tkinter.Toplevel.__init__, tkinter.Wm.deiconify,
                                     files.open_spreadsheet))                # (all put back)


if __name__ == "__main__":
    unittest.main()
