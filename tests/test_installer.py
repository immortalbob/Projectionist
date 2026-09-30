"""The Windows installer (tools/installer.py): the payload it unpacks, installing, updating an older install,
uninstalling (your settings kept), the Installed-apps entry, the shortcuts, the command line and Setup's window.

Everything happens in temporary folders: the real registry, Start menu and desktop are never touched - the
Installed-apps entry goes to a stand-in (installer.FileRegistry), and the tests fail at once if anything reaches
for the real ones. Shortcuts are real .lnk files on Windows (made in the temporary folders), stand-ins elsewhere.
Setup's window is built on a withdrawn root and driven by hand: it's never shown, and nothing is started."""

import contextlib
import hashlib
import io
import json
import os
import sys
import tempfile
import time
import unittest
import zipfile
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import build_exe  # noqa: E402
import installer as I  # noqa: E402

WINDOWS = sys.platform == "win32"
DEFAULT_PLACES, KNOWN_FOLDER = I.default_places, I.known_folder     # (the tests put guards in their places)


class FakeShortcuts:
    """Shortcuts as small JSON files (away from Windows, or where a test wants to see what was asked for)."""

    def __init__(self):
        self.made = []

    def make(self, path, target, **details):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"target": target, **details}, f)
        self.made.append(path)

    def read(self, path):
        with open(path, encoding="utf-8") as f:
            found = json.load(f)
        return {"target": found["target"], "workdir": found.get("workdir"), "icon": found.get("icon"),
                "description": found.get("description"), "app_id": found.get("app_id"), "arguments": ""}


def shortcut_maker():
    return I.Shortcuts() if WINDOWS else FakeShortcuts()


def make_app_folder(folder, version="1.0.0", extra=None):
    """A small stand-in for the built app's folder."""
    files = {"Projectionist.exe": b"MZ app " + version.encode(), "LICENSE": b"MIT",
             "_internal/python314.dll": b"MZ python", "_internal/projectionist/assets/projectionist.ico": b"\0\0\1\0",
             "_internal/_tcl_data/init.tcl": b"# tcl"}
    files.update(extra or {})
    for name, data in files.items():
        path = os.path.join(folder, *name.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
    return folder


class Sandbox(unittest.TestCase):
    """Temporary places for everything, and a guard that fails the test if the real ones are reached for."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="projectionist-setup-test-")
        self.addCleanup(self.tmp.cleanup)
        base = self.tmp.name
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(base)                              # (a relative path, wrongly taken, lands in here)
        self.places = I.Places(os.path.join(base, "Programs", "Projectionist"), os.path.join(base, "StartMenu"),
                               os.path.join(base, "Desktop"), os.path.join(base, "Temp"))
        os.makedirs(self.places.temp_dir)
        self.registry = I.FileRegistry()
        self.links = shortcut_maker()

        def real(*_a, **_k):
            raise AssertionError("a test reached for the real registry, Start menu or desktop")
        for name in ("WinRegistry", "default_places", "known_folder"):
            patch = mock.patch.object(I, name, real)
            patch.start()
            self.addCleanup(patch.stop)
        self.told = []
        patch = mock.patch.object(I, "message", lambda text, kind="info", title="": self.told.append((kind, text))
                                  or kind == "ask")
        patch.start()
        self.addCleanup(patch.stop)
        patch = mock.patch.object(I, "_launch", real)
        patch.start()
        self.addCleanup(patch.stop)

    def payload(self, version="1.0.0", extra=None, name=None):
        folder = make_app_folder(os.path.join(self.tmp.name, f"app-{version}-{len(extra or {})}"), version, extra)
        uninstaller = os.path.join(self.tmp.name, f"Uninstall-{version}.exe")
        with open(uninstaller, "wb") as f:
            f.write(b"MZ uninstaller")
        path = os.path.join(self.tmp.name, name or f"payload-{version}-{len(extra or {})}.zip")
        build_exe.make_payload(folder, uninstaller, path, version, 1790640000)
        return I.Payload(path)

    def install(self, payload=None, **kw):
        kw.setdefault("registry", self.registry)
        kw.setdefault("shortcuts", self.links)
        kw.setdefault("today", "20260930")
        return I.install(payload or self.payload(), kw.pop("places", self.places), **kw)

    def path(self, *parts):
        return os.path.join(self.places.install_dir, *parts)

    def files_in(self, folder):
        return sorted(os.path.relpath(os.path.join(h, n), folder).replace(os.sep, "/")
                      for h, _d, names in os.walk(folder) for n in names)


# ---------------------------------------------------------------------------------------------------------
class PayloadTests(Sandbox):
    def test_what_it_holds(self):
        payload = self.payload("1.2.3")
        self.assertEqual(payload.version, "1.2.3")
        self.assertIn("Projectionist.exe", payload.files)
        self.assertIn("Uninstall.exe", payload.files)
        self.assertIn("_internal/projectionist/assets/projectionist.ico", payload.files)
        self.assertEqual(payload.size, sum(size for _n, size in payload.entries))
        self.assertEqual(payload.info["files"], len(payload.files))
        with zipfile.ZipFile(payload.path) as z:                  # (forward slashes, in order, one date)
            names = z.namelist()
            self.assertEqual(names, sorted(names, key=lambda n: n.split("/")))
            self.assertEqual({i.date_time for i in z.infolist()}, {time.gmtime(1790640000)[:6]})

    def test_the_same_folder_gives_the_same_bytes(self):
        first, again = self.payload(name="a.zip"), self.payload(name="b.zip")

        def digest(p):
            with open(p.path, "rb") as f:
                return hashlib.sha256(f.read()).hexdigest()
        self.assertEqual(digest(first), digest(again))

    def test_only_its_own_payloads(self):
        def zip_with(names, comment):
            path = os.path.join(self.tmp.name, f"odd-{len(os.listdir(self.tmp.name))}.zip")
            with zipfile.ZipFile(path, "w") as z:
                for name in names:
                    z.writestr(name, b"x")
                z.comment = comment
            return path
        good = json.dumps({"name": "Projectionist", "version": "1.0.0"}).encode()
        for names, comment in [(["Projectionist.exe", "Uninstall.exe"], b""),                  # no description
                               (["Projectionist.exe", "Uninstall.exe"], b'{"name": "Other", "version": "1"}'),
                               (["Projectionist.exe"], good),                                    # no uninstaller
                               (["Projectionist.exe", "Uninstall.exe", "../outside.dll"], good),
                               (["Projectionist.exe", "Uninstall.exe", "C:/Windows/evil.dll"], good),
                               (["Projectionist.exe", "Uninstall.exe", I.MANIFEST], good)]:
            with self.assertRaises(I.InstallError, msg=names):
                I.Payload(zip_with(names, comment))
        with self.assertRaises(I.InstallError):
            I.Payload(os.path.join(self.tmp.name, "missing.zip"))
        self.assertTrue(I.safe_name("_internal/tcl/init.tcl"))
        for bad in ("", "/abs", "a//b", "a/./b", "a\\b", "..", "a/../b"):
            self.assertFalse(I.safe_name(bad), bad)


# ---------------------------------------------------------------------------------------------------------
class InstallTests(Sandbox):
    def test_a_first_install(self):
        payload = self.payload()
        manifest = self.install(payload)
        folder = self.places.install_dir
        self.assertEqual(self.files_in(folder), sorted(payload.files + [I.MANIFEST]))
        with open(self.path("Projectionist.exe"), "rb") as f:
            self.assertEqual(f.read(), b"MZ app 1.0.0")
        self.assertEqual(I.read_manifest(folder), manifest)
        self.assertEqual(manifest["version"], "1.0.0")
        self.assertEqual(manifest["files"], sorted(payload.files))
        # the Start-menu shortcut, and no desktop one
        start = os.path.join(self.places.start_menu_dir, "Projectionist.lnk")
        self.assertEqual(manifest["shortcuts"], [start])
        self.assertFalse(os.path.exists(os.path.join(self.places.desktop_dir, "Projectionist.lnk")))
        link = self.links.read(start)
        self.assertTrue(I.same_path(link["target"], self.path("Projectionist.exe")))
        self.assertTrue(I.same_path(link["workdir"], folder))
        self.assertEqual(link["app_id"], "Projectionist.App")
        # the Installed-apps entry
        entry = self.registry.read(I.UNINSTALL_KEY)
        exe, uninstaller = self.path("Projectionist.exe"), self.path("Uninstall.exe")
        self.assertEqual(entry, {
            "DisplayName": "Projectionist", "DisplayVersion": "1.0.0", "Publisher": "Projectionist contributors",
            "DisplayIcon": exe + ",0", "UninstallString": f'"{uninstaller}"',
            "QuietUninstallString": f'"{uninstaller}" --silent', "InstallLocation": folder,
            "InstallDate": "20260930", "EstimatedSize": (payload.size + 1023) // 1024, "NoModify": 1, "NoRepair": 1})
        self.assertEqual(self.registry.kinds(I.UNINSTALL_KEY)["EstimatedSize"], "REG_DWORD")
        self.assertEqual(self.registry.kinds(I.UNINSTALL_KEY)["NoRepair"], "REG_DWORD")
        self.assertEqual(self.registry.kinds(I.UNINSTALL_KEY)["UninstallString"], "REG_SZ")
        self.assertEqual(I.installed_folder(self.registry), folder)
        # nothing half-written is left beside the files
        self.assertFalse([n for n in self.files_in(folder) if n.endswith((".setup-new", ".tmp"))])

    def test_a_desktop_shortcut_when_asked_for(self):
        manifest = self.install(desktop=True)
        desktop = os.path.join(self.places.desktop_dir, "Projectionist.lnk")
        self.assertTrue(os.path.isfile(desktop))
        self.assertIn(desktop, manifest["shortcuts"])
        self.assertTrue(manifest["desktop"])
        self.assertTrue(I.same_path(self.links.read(desktop)["target"], self.path("Projectionist.exe")))

    def test_it_says_how_far_it_has_got(self):
        seen = []
        self.install(progress=lambda fraction, text: seen.append((fraction, text)))
        fractions = [f for f, _t in seen]
        self.assertEqual(fractions, sorted(fractions))
        self.assertEqual((fractions[0], fractions[-1]), (0.0, 1.0))
        self.assertTrue(any("Projectionist.exe" in t for _f, t in seen))

    def test_updating_an_older_install(self):
        old = self.payload("0.9.0", extra={"_internal/old_only/gone.pyd": b"old", "_internal/kept.dll": b"old"})
        self.install(old, desktop=True)
        mine = self.path("my notes.txt")                         # (something put there since: not Setup's)
        with open(mine, "w") as f:
            f.write("mine")
        new = self.payload("1.0.0", extra={"_internal/kept.dll": b"new", "_internal/new_only.pyd": b"new"})
        manifest = self.install(new)                              # (desktop=None: as the older install had it)
        self.assertEqual(manifest["version"], "1.0.0")
        self.assertFalse(os.path.exists(self.path("_internal", "old_only")))          # its file, and its folder
        with open(self.path("_internal", "kept.dll"), "rb") as f:
            self.assertEqual(f.read(), b"new")
        with open(self.path("Projectionist.exe"), "rb") as f:
            self.assertEqual(f.read(), b"MZ app 1.0.0")
        self.assertTrue(os.path.isfile(self.path("_internal", "new_only.pyd")))
        self.assertTrue(os.path.isfile(mine))
        self.assertTrue(manifest["desktop"])
        self.assertTrue(os.path.isfile(os.path.join(self.places.desktop_dir, "Projectionist.lnk")))
        self.assertEqual(self.registry.read(I.UNINSTALL_KEY)["DisplayVersion"], "1.0.0")
        self.assertEqual(self.files_in(self.places.install_dir), sorted(new.files + [I.MANIFEST, "my notes.txt"]))

    def test_updating_can_take_the_desktop_shortcut_away(self):
        self.install(desktop=True)
        manifest = self.install(desktop=False)
        self.assertFalse(os.path.exists(os.path.join(self.places.desktop_dir, "Projectionist.lnk")))
        self.assertTrue(os.path.isfile(os.path.join(self.places.start_menu_dir, "Projectionist.lnk")))
        self.assertEqual(len(manifest["shortcuts"]), 1)

    @unittest.skipUnless(WINDOWS, "read-only files are a Windows matter")
    def test_a_read_only_file_is_still_replaced(self):
        self.install(self.payload("0.9.0"))
        os.chmod(self.path("LICENSE"), 0o444)
        self.install(self.payload("1.0.0"))
        with open(self.path("Projectionist.exe"), "rb") as f:
            self.assertEqual(f.read(), b"MZ app 1.0.0")

    def test_folders_it_wont_install_in(self):
        crowded = os.path.join(self.tmp.name, "Documents")
        os.makedirs(crowded)
        with open(os.path.join(crowded, "letter.txt"), "w") as f:
            f.write("x")
        a_file = os.path.join(self.tmp.name, "a file")
        with open(a_file, "w") as f:
            f.write("x")
        drive = os.path.abspath(os.sep)
        for folder, words in ((crowded, "already has other things"), (a_file, "is a file"),
                              ("relative\\path", "full path"), ("", "full path"), (drive, "not the whole")):
            self.assertIn(words, I.folder_problem(folder) or "", folder)
            places = I.Places(folder, *(self.places.start_menu_dir, self.places.desktop_dir, self.places.temp_dir))
            with self.assertRaises(I.InstallError, msg=folder):
                self.install(places=places)
        self.assertEqual(os.listdir(crowded), ["letter.txt"])
        self.assertIsNone(self.registry.read(I.UNINSTALL_KEY))
        # an empty folder, a new one, and one it installed in before are all fine
        empty = os.path.join(self.tmp.name, "Empty")
        os.makedirs(empty)
        self.assertIsNone(I.folder_problem(empty))
        self.assertIsNone(I.folder_problem(os.path.join(self.tmp.name, "New", "Projectionist")))
        self.install()
        self.assertIsNone(I.folder_problem(self.places.install_dir))

    def test_not_while_the_app_is_running(self):
        self.install(self.payload("0.9.0"))
        with mock.patch.object(I, "app_running", lambda folder: True):
            with self.assertRaises(I.AppRunning):
                self.install(self.payload("1.0.0"))
            with self.assertRaises(I.AppRunning):
                I.uninstall(self.places.install_dir, registry=self.registry)
        self.assertEqual(I.read_manifest(self.places.install_dir)["version"], "0.9.0")
        with open(self.path("Projectionist.exe"), "rb") as f:
            self.assertEqual(f.read(), b"MZ app 0.9.0")

    @unittest.skipUnless(WINDOWS, "Windows' own file locks")
    def test_a_running_program_is_seen(self):
        """A running program's file is open for reading only, with writing shared by no one - as here."""
        import ctypes
        from ctypes import wintypes
        self.install()
        exe = self.path("Projectionist.exe")
        self.assertFalse(I.app_running(self.places.install_dir))
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                         wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel32.CreateFileW.restype = wintypes.HANDLE
        handle = kernel32.CreateFileW(exe, 0x80000000, 0x1 | 0x4, None, 3, 0x80, None)   # read; share read, delete
        self.assertNotEqual(handle, wintypes.HANDLE(-1).value)
        try:
            self.assertTrue(I.app_running(self.places.install_dir))
        finally:
            kernel32.CloseHandle(wintypes.HANDLE(handle))
        self.assertFalse(I.app_running(self.places.install_dir))
        self.assertFalse(I.app_running(os.path.join(self.tmp.name, "nowhere")))


# ---------------------------------------------------------------------------------------------------------
class UninstallTests(Sandbox):
    def test_everything_goes_but_your_settings(self):
        settings = os.path.join(self.tmp.name, "AppData", "Roaming", "Projectionist")     # (where they'd be)
        os.makedirs(settings)
        with open(os.path.join(settings, "settings.json"), "w") as f:
            f.write('{"look": "velvet"}')
        manifest = self.install(desktop=True)
        removal = I.uninstall(self.places.install_dir, registry=self.registry)
        self.assertFalse(os.path.exists(self.places.install_dir))
        for link in manifest["shortcuts"]:
            self.assertFalse(os.path.exists(link), link)
        self.assertIsNone(self.registry.read(I.UNINSTALL_KEY))
        self.assertEqual((removal.removed, removal.shortcuts, removal.entry, removal.folder_gone, removal.kept),
                         (len(manifest["files"]), 2, True, True, []))
        self.assertEqual(removal.version, "1.0.0")
        with open(os.path.join(settings, "settings.json")) as f:
            self.assertEqual(f.read(), '{"look": "velvet"}')
        self.assertTrue(os.path.isdir(self.places.start_menu_dir))         # (only its own shortcut goes)

    def test_what_isnt_its_own_stays(self):
        self.install()
        mine = self.path("_internal", "my plugin.txt")
        with open(mine, "w") as f:
            f.write("mine")
        removal = I.uninstall(self.places.install_dir, registry=self.registry)
        self.assertEqual(removal.kept, [mine])
        self.assertFalse(removal.folder_gone)
        self.assertEqual(self.files_in(self.places.install_dir), ["_internal/my plugin.txt"])

    def test_another_installs_entry_stays(self):
        self.install()
        other = {"DisplayName": "Projectionist", "InstallLocation": os.path.join(self.tmp.name, "Elsewhere")}
        self.registry.write(I.UNINSTALL_KEY, other)
        removal = I.uninstall(self.places.install_dir, registry=self.registry)
        self.assertFalse(removal.entry)
        self.assertEqual(self.registry.read(I.UNINSTALL_KEY), other)

    def test_only_where_it_was_installed(self):
        with self.assertRaises(I.InstallError):
            I.uninstall(os.path.join(self.tmp.name, "nothing here"), registry=self.registry)

    def test_the_running_uninstaller_moves_itself_out(self):
        """Uninstall.exe removes everything but itself, then moves itself to the temporary folder and has a hidden
        command prompt delete it once it has ended (here: the command is only looked at, never run)."""
        self.install()
        me = self.path("Uninstall.exe")
        removal = I.uninstall(self.places.install_dir, registry=self.registry, keep=me)
        self.assertEqual(removal.kept, [me])
        self.assertEqual(self.files_in(self.places.install_dir), ["Uninstall.exe"])
        commands = []
        here = os.getcwd()
        try:
            moved = I.move_self_out(me, self.places.install_dir, self.places.temp_dir)
            self.assertEqual(os.getcwd(), self.places.temp_dir)   # (out of the folder, so it can go)
        finally:
            os.chdir(here)
        self.assertFalse(os.path.exists(self.places.install_dir))
        self.assertTrue(os.path.isfile(moved))
        self.assertEqual(os.path.dirname(moved), self.places.temp_dir)
        self.assertTrue(os.path.basename(moved).startswith("~Projectionist-uninstall-"))
        I.delete_later(moved, None, self.places.temp_dir, spawn=commands.append)
        self.assertEqual(len(commands), 1)
        self.assertIn(f'if exist "{moved}"', commands[0])
        self.assertIn(f'del /f /q "{moved}"', commands[0])
        self.assertNotIn("rmdir", commands[0])                   # (the folder has gone already)

    def test_uninstalling_as_the_running_uninstaller(self):
        """Uninstall.exe as Windows starts it: from inside the folder. It asks, removes everything, moves itself
        out, says it's done - and only then has itself deleted."""
        registry_file = os.path.join(self.tmp.name, "registry.json")
        self.install(registry=I.FileRegistry(registry_file), desktop=True)
        me = self.path("Uninstall.exe")
        order = []
        told = lambda text, kind="info", title="": order.append(kind) or kind == "ask"      # noqa: E731
        with mock.patch.object(sys, "frozen", True, create=True), mock.patch.object(sys, "executable", me), \
                mock.patch.object(I, "message", told), \
                mock.patch.object(I, "delete_later", lambda path, folder, temp: order.append(("delete", path))):
            with contextlib.redirect_stdout(io.StringIO()):
                code = I.main(["--registry-file", registry_file, "--temp-dir", self.places.temp_dir,
                               "--start-menu-dir", self.places.start_menu_dir, "--desktop-dir", self.places.desktop_dir,
                               "--log", os.path.join(self.tmp.name, "log.txt")])
        self.assertEqual(code, I.EXIT_OK)
        self.assertEqual(order[:2], ["ask", "info"])
        self.assertEqual(order[2][0], "delete")
        self.assertEqual(os.path.dirname(order[2][1]), self.places.temp_dir)
        self.assertFalse(os.path.exists(self.places.install_dir))
        self.assertEqual(os.listdir(self.places.desktop_dir), [])
        self.assertIsNone(I.FileRegistry(registry_file).read(I.UNINSTALL_KEY))

    def test_the_command_that_deletes_it(self):
        command = I.deleter_command(r"C:\Temp\x y.exe", r"C:\Programs\Projectionist", comspec=r"C:\Windows\cmd.exe")
        self.assertTrue(command.startswith(r'"C:\Windows\cmd.exe" /d /q /s /c "for /l %i in (1,1,60) do @('))
        self.assertTrue(command.endswith('"'))
        self.assertIn(r'if exist "C:\Temp\x y.exe" (del /f /q "C:\Temp\x y.exe" >nul 2>&1 & ping -n 2 127.0.0.1 >nul)',
                      command)
        self.assertIn(r'else (rmdir "C:\Programs\Projectionist" >nul 2>&1 & exit)', command)
        self.assertNotIn("/s /q \"C:\\Programs", command)          # (never a removal of all that's in it)


# ---------------------------------------------------------------------------------------------------------
class CommandLineTests(Sandbox):
    def run_main(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = I.main(list(argv))
        return code, out.getvalue()

    def everywhere(self, registry_file):
        p = self.places
        return ["--start-menu-dir", p.start_menu_dir, "--desktop-dir", p.desktop_dir, "--registry-file", registry_file,
                "--temp-dir", p.temp_dir]

    def test_a_silent_install_and_uninstall(self):
        self.links = FakeShortcuts()                              # (to count what was asked for)
        payload = self.payload("1.0.0")
        registry_file = os.path.join(self.tmp.name, "registry.json")
        log = os.path.join(self.tmp.name, "setup.log")
        with mock.patch.object(I, "Shortcuts", lambda: self.links):
            code, out = self.run_main("--silent", "--payload", payload.path, "--install-dir", self.places.install_dir,
                                      "--desktop", "--log", log, *self.everywhere(registry_file))
            self.assertEqual(code, I.EXIT_OK, out)
            self.assertIn("Installed Projectionist 1.0.0 in", out)
            with open(log, encoding="utf-8") as f:
                self.assertIn("Shortcut: " + os.path.join(self.places.desktop_dir, "Projectionist.lnk"), f.read())
            entry = I.FileRegistry(registry_file).read(I.UNINSTALL_KEY)
            self.assertEqual(entry["InstallLocation"], self.places.install_dir)
            self.assertEqual(len(self.links.made), 2)
            # the same again: installed again, over itself
            code, _out = self.run_main("--silent", "--payload", payload.path, *self.everywhere(registry_file))
            self.assertEqual(code, I.EXIT_OK)                     # (it found the folder from the entry)
            self.assertEqual(len(self.links.made), 4)             # (the desktop one kept)
            code, out = self.run_main("--uninstall", "--silent", "--install-dir", self.places.install_dir,
                                      "--log", log, *self.everywhere(registry_file))
        self.assertEqual(code, I.EXIT_OK, out)
        self.assertIn("Removed Projectionist 1.0.0", out)
        self.assertFalse(os.path.exists(self.places.install_dir))
        self.assertIsNone(I.FileRegistry(registry_file).read(I.UNINSTALL_KEY))
        self.assertEqual(self.told, [])                           # (silent: nothing was ever shown)

    def test_the_exit_codes(self):
        payload = self.payload()
        registry_file = os.path.join(self.tmp.name, "registry.json")
        common = ["--silent", "--payload", payload.path, "--install-dir", self.places.install_dir,
                  *self.everywhere(registry_file)]
        with mock.patch.object(I, "Shortcuts", lambda: self.links):
            self.assertEqual(self.run_main(*common)[0], I.EXIT_OK)
            with mock.patch.object(I, "app_running", lambda folder: True):
                self.assertEqual(self.run_main(*common)[0], I.EXIT_RUNNING)
            crowded = os.path.join(self.tmp.name, "Crowded")
            os.makedirs(crowded)
            open(os.path.join(crowded, "x"), "w").close()
            self.assertEqual(self.run_main(*common, "--install-dir", crowded)[0], I.EXIT_FAILED)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.run_main("--no-such-option")[0], I.EXIT_USAGE)
            self.assertEqual(self.run_main("--desktop", "--no-desktop", "--payload", payload.path)[0], I.EXIT_USAGE)
        code, out = self.run_main("--version", "--payload", payload.path)
        self.assertEqual((code, out.strip()), (I.EXIT_OK, "Projectionist 1.0.0 Setup"))
        code, out = self.run_main("--uninstall", "--silent", "--install-dir", os.path.join(self.tmp.name, "none"),
                                  *self.everywhere(registry_file))
        self.assertEqual(code, I.EXIT_FAILED)
        self.assertIn("isn't installed", out)

    def test_uninstalling_asks_first_unless_silent(self):
        registry_file = os.path.join(self.tmp.name, "registry.json")
        self.install(registry=I.FileRegistry(registry_file))
        args = ["--uninstall", "--install-dir", self.places.install_dir, *self.everywhere(registry_file)]
        with mock.patch.object(I, "message", lambda text, kind="info", title="": self.told.append((kind, text))):
            code, _out = self.run_main(*args)                     # (answered No)
        self.assertEqual(code, I.EXIT_CANCELLED)
        self.assertTrue(os.path.isfile(self.path("Projectionist.exe")))
        self.assertEqual([k for k, _t in self.told], ["ask"])
        self.assertIn("Your settings are kept", self.told[0][1])
        self.told.clear()
        code, _out = self.run_main(*args)                         # (answered Yes)
        self.assertEqual(code, I.EXIT_OK)
        self.assertFalse(os.path.exists(self.places.install_dir))
        self.assertEqual([k for k, _t in self.told], ["ask", "info"])
        self.assertIn("has been removed", self.told[1][1])

    def test_setup_without_silent_opens_its_window(self):
        payload = self.payload()
        calls = []
        with mock.patch.object(I, "run_wizard", lambda *a, **k: calls.append((a, k)) or I.EXIT_OK):
            code, _out = self.run_main("--payload", payload.path, "--install-dir", self.places.install_dir,
                                       *self.everywhere(os.path.join(self.tmp.name, "registry.json")))
        self.assertEqual(code, I.EXIT_OK)
        (given_payload, places, registry), kwargs = calls[0]
        self.assertEqual(given_payload.version, "1.0.0")
        self.assertEqual(places, self.places)
        self.assertIsInstance(registry, I.FileRegistry)
        self.assertIsNone(kwargs["desktop"])

    def test_the_usual_places(self):
        """Read (never written): where Setup puts things for this user when it isn't told."""
        known = {I.FOLDERID_USER_PROGRAM_FILES: r"C:\Users\You\AppData\Local\Programs",
                 I.FOLDERID_PROGRAMS: r"C:\Users\You\AppData\Roaming\Microsoft\Windows\Start Menu\Programs",
                 I.FOLDERID_DESKTOP: r"C:\Users\You\OneDrive\Desktop"}
        with mock.patch.object(I, "known_folder", lambda guid, fallback: known[guid]):
            places = DEFAULT_PLACES({"USERPROFILE": r"C:\Users\You"})
        self.assertEqual(places.install_dir, os.path.join(known[I.FOLDERID_USER_PROGRAM_FILES], "Projectionist"))
        self.assertEqual(places.start_menu_dir, known[I.FOLDERID_PROGRAMS])
        self.assertEqual(places.desktop_dir, known[I.FOLDERID_DESKTOP])
        with mock.patch.object(I, "known_folder", lambda guid, fallback: fallback):      # (Windows didn't say)
            places = DEFAULT_PLACES({"USERPROFILE": "/home/you", "LOCALAPPDATA": "/local", "APPDATA": "/roaming"})
        self.assertEqual(places.install_dir, os.path.join("/local", "Programs", "Projectionist"))
        self.assertEqual(places.start_menu_dir, os.path.join("/roaming", "Microsoft", "Windows", "Start Menu",
                                                             "Programs"))
        self.assertEqual(places.desktop_dir, os.path.join("/home/you", "Desktop"))
        self.assertEqual(I.settings_folder({"APPDATA": "/roaming"}), os.path.join("/roaming", "Projectionist"))

    @unittest.skipUnless(WINDOWS, "Windows' known folders")
    def test_windows_known_folders_are_read(self):
        """Read only: the real folders' paths, compared with what the environment says."""
        start_menu = KNOWN_FOLDER(I.FOLDERID_PROGRAMS, "")
        self.assertTrue(start_menu.lower().endswith(os.path.join("start menu", "programs")), start_menu)
        self.assertEqual(KNOWN_FOLDER("{00000000-0000-0000-0000-000000000000}", "fallback"), "fallback")

@unittest.skipUnless(WINDOWS, "Windows shortcuts")
class ShortcutTests(Sandbox):
    def test_a_real_shortcut_made_and_read_back(self):
        target = os.path.join(self.tmp.name, "Program Files é", "Projectionist.exe")
        os.makedirs(os.path.dirname(target))
        with open(target, "wb") as f:
            f.write(b"MZ")
        link = os.path.join(self.tmp.name, "Start Menu", "Projectionist.lnk")
        I.Shortcuts().make(link, target, workdir=os.path.dirname(target), icon=target, description=I.DESCRIPTION,
                           app_id=I.APP_ID, arguments="--x")
        with open(link, "rb") as f:
            self.assertEqual(f.read(4), b"L\0\0\0")                   # (a shell link's header)
        found = I.Shortcuts().read(link)
        self.assertTrue(I.same_path(found["target"], target))
        self.assertTrue(I.same_path(found["workdir"], os.path.dirname(target)))
        self.assertTrue(I.same_path(found["icon"], target))
        self.assertEqual((found["icon_index"], found["arguments"], found["description"], found["app_id"]),
                         (0, "--x", I.DESCRIPTION, I.APP_ID))


class NoShortcutsElsewhereTests(Sandbox):
    """(Out of ShortcutTests, so it runs away from Windows too - where it matters.)"""

    def test_away_from_windows_there_are_none(self):
        with mock.patch.object(sys, "platform", "linux"):
            with self.assertRaises(I.InstallError):
                I.Shortcuts()


# ---------------------------------------------------------------------------------------------------------
class SetupWindowTests(Sandbox):
    """Setup's window on a withdrawn root: never shown, driven by hand."""

    def setUp(self):
        super().setUp()
        try:
            import tkinter as tk
            self.root = tk.Tk()
        except Exception as exc:                                  # noqa: BLE001
            self.skipTest(f"Tk unavailable: {exc}")
        self.root.withdraw()
        from projectionist.ui import theme
        self.theme, self.look = theme, theme.LOOK
        self.addCleanup(self._restore)
        self.launched = []

    def _restore(self):
        try:
            self.root.destroy()
        except Exception:                                         # noqa: BLE001
            pass
        self.theme.use(self.look)

    def window(self, payload=None, **kw):
        kw.setdefault("settings", {})
        kw.setdefault("shortcuts", self.links)
        kw.setdefault("launch", self.launched.append)
        return I.SetupWindow(self.root, payload or self.payload(), self.places, self.registry, **kw)

    def pump(self, until, seconds=30):
        deadline = time.time() + seconds
        while not until():
            self.root.update()
            time.sleep(0.01)
            self.assertLess(time.time(), deadline, "timed out")

    def test_install_then_open_it(self):
        w = self.window()
        self.assertEqual(w.look, "graphite")                      # (the app's own look, as nothing else was chosen)
        self.assertEqual(self.root.title(), "Projectionist 1.0.0 Setup")
        self.assertEqual(w.dir_var.get(), self.places.install_dir)
        self.assertFalse(w.desktop_var.get())
        self.assertEqual(str(w.go_button.cget("state")), "normal")
        self.assertEqual(self.root.state(), "withdrawn")
        w.desktop_var.set(True)
        w.go_button.invoke()
        self.assertEqual(w.state, "working")
        self.pump(lambda: w.state != "working")
        self.assertEqual(w.state, "done", w.error)
        self.assertEqual(w.go_button.cget("text"), "Finish")
        self.assertIn("Start menu and on your desktop", w.done_text.cget("text"))
        self.assertTrue(os.path.isfile(os.path.join(self.places.desktop_dir, "Projectionist.lnk")))
        self.assertEqual(self.registry.read(I.UNINSTALL_KEY)["DisplayVersion"], "1.0.0")
        self.assertEqual(self.root.state(), "withdrawn")          # (still never shown)
        w.go_button.invoke()                                      # Finish, with 'Open Projectionist now' ticked
        self.assertEqual(self.launched, [self.path("Projectionist.exe")])
        with self.assertRaises(Exception):
            self.root.winfo_exists()                              # (closed)

    def test_the_look_you_chose(self):
        self.assertEqual(self.window(settings={"look": "velvet"}).look, "velvet")

    def test_it_says_what_will_happen(self):
        self.install(self.payload("0.9.0"))
        w = self.window()
        self.assertIn("0.9.0 is installed there: it will be replaced by 1.0.0", w.note_var.get())
        self.assertIn("Your settings are kept", w.note_var.get())
        crowded = os.path.join(self.tmp.name, "Crowded")
        os.makedirs(crowded)
        open(os.path.join(crowded, "x"), "w").close()
        w.dir_var.set(crowded)
        self.assertIn("already has other things", w.note_var.get())
        self.assertEqual(str(w.go_button.cget("state")), "disabled")
        w.install()                                               # (Enter: still nothing)
        self.assertEqual(w.state, "choose")
        w.dir_var.set(os.path.join(self.tmp.name, "Fresh", "Projectionist"))
        self.assertEqual(w.note_var.get(), "")
        self.assertEqual(str(w.go_button.cget("state")), "normal")

    def test_a_running_app_sends_it_back(self):
        self.install(self.payload("0.9.0"))
        w = self.window()
        with mock.patch.object(I, "app_running", lambda folder: True):
            w.install()
            self.pump(lambda: w.state != "working")
        self.assertEqual(w.state, "choose")
        self.assertIn("is running. Close it", w.note_var.get())
        self.assertEqual(I.read_manifest(self.places.install_dir)["version"], "0.9.0")
        w.cancel()
        self.assertEqual(w.state, "closed")
        self.assertEqual(self.launched, [])

    def test_browse_puts_it_in_a_folder_of_its_own(self):
        from tkinter import filedialog
        w = self.window()
        chosen = os.path.join(self.tmp.name, "Apps")
        with mock.patch.object(filedialog, "askdirectory", lambda **k: chosen):
            w.browse()
        self.assertEqual(w.dir_var.get(), os.path.join(chosen, "Projectionist"))
        with mock.patch.object(filedialog, "askdirectory", lambda **k: ""):
            w.browse()                                            # (cancelled: nothing changes)
        self.assertEqual(w.dir_var.get(), os.path.join(chosen, "Projectionist"))


if __name__ == "__main__":
    unittest.main()
