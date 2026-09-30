"""Projectionist's Windows installer and uninstaller. tools/build_exe.py freezes this one script twice: into
Projectionist-<version>-Setup.exe, with the app packed inside it, and into Uninstall.exe, which Setup puts in the
installed folder.

    Projectionist-<version>-Setup.exe             a small window: where to install it, a desktop shortcut or not
    Projectionist-<version>-Setup.exe --silent    no window: installs with the defaults (or the options below)
    Uninstall.exe                                 (in the installed folder) asks, then removes Projectionist
    Uninstall.exe --silent                        ...without asking

Setup installs for you only - nothing system-wide, and no administrator rights. It puts the app in
%LOCALAPPDATA%\\Programs\\Projectionist, a shortcut in the Start menu (and one on the desktop, if you tick it), and an
entry in Windows' Settings > Apps > Installed apps (HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\
Projectionist) from which it can be uninstalled. A newer Setup run over an older install updates it in place. The
uninstaller removes the files, the shortcuts and the entry - never your settings (%APPDATA%\\Projectionist), which
are there if you install it again.

Every place can be pointed elsewhere, so all of it can be tried out without touching the real ones:
    --install-dir DIR        where the app goes (default: where it's installed already, else the folder above)
    --start-menu-dir DIR     the folder for the Start-menu shortcut (default: your Start menu's Programs)
    --desktop / --no-desktop a desktop shortcut or not (the window asks; --silent: as the install being updated
                             had it, else not)
    --desktop-dir DIR        the folder for the desktop shortcut (default: your desktop)
    --registry-file FILE     keep the Installed-apps entry in this JSON file instead of the registry
    --temp-dir DIR           where Uninstall.exe moves itself to be deleted once it has ended (default: %TEMP%)
    --log FILE               add what happened (and any error) to FILE
    --uninstall              remove instead (what Uninstall.exe does)
    --payload ZIP            the app to install, when this runs as a script rather than as Setup.exe
    --version                print the version
Exit codes: 0 done, 1 failed (the reason goes in --log), 2 wrong options, 3 Projectionist is running (close it
first), 4 cancelled.

Only the standard library: shortcuts are made through Windows' own ShellLink object (ctypes), and the uninstall
entry with winreg.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import stat
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import uuid
import zipfile
from dataclasses import dataclass

APP = "Projectionist"
EXE = "Projectionist.exe"
UNINSTALLER = "Uninstall.exe"
MANIFEST = "install-manifest.json"         # what Setup put where: the uninstaller removes exactly that
PAYLOAD = "payload.zip"                    # the app, packed inside Setup.exe (tools/build_exe.py makes it)
PUBLISHER = "Projectionist contributors"
SHORTCUT = f"{APP}.lnk"
DESCRIPTION = "Explore your Plex movie collection, and export it to a spreadsheet"
APP_ID = "Projectionist.App"               # the window's own taskbar ID (projectionist/appicon.py): the shortcuts
                                           # carry it too, so a pinned shortcut and the window are one button
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Projectionist"     # (under HKCU)

EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_RUNNING, EXIT_CANCELLED = 0, 1, 2, 3, 4

# Windows' known folders (SHGetKnownFolderPath)
FOLDERID_USER_PROGRAM_FILES = "{5CD7AEE2-2219-4A67-B85D-6C9CE15660CB}"      # %LOCALAPPDATA%\Programs
FOLDERID_PROGRAMS = "{A77F5D77-2E2B-44C3-A6A2-ABA601054A51}"                 # the Start menu's Programs
FOLDERID_DESKTOP = "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}"


class InstallError(Exception):
    """Why it can't be installed or removed, in words for the person doing it."""


class AppRunning(InstallError):
    """Projectionist is running from the folder: its files can't be replaced or removed until it's closed."""


# ---------------------------------------------------------------------------------------------------------
# Where things go
# ---------------------------------------------------------------------------------------------------------
@dataclass
class Places:
    install_dir: str
    start_menu_dir: str
    desktop_dir: str
    temp_dir: str


def known_folder(guid: str, fallback: str) -> str:
    """A Windows known folder's path (read only), or fallback where it can't be had."""
    if sys.platform != "win32":
        return fallback
    try:
        import ctypes
        from ctypes import wintypes
        shell32, ole32 = ctypes.WinDLL("shell32"), ctypes.WinDLL("ole32")
        get = shell32.SHGetKnownFolderPath
        get.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
        get.restype = ctypes.c_long
        folder_id = (ctypes.c_ubyte * 16).from_buffer_copy(uuid.UUID(guid).bytes_le)
        found = ctypes.c_void_p()
        hr = get(ctypes.byref(folder_id), 0, None, ctypes.byref(found))
        try:
            if hr == 0 and found.value:
                return ctypes.wstring_at(found.value) or fallback
        finally:
            if found.value:
                ole32.CoTaskMemFree(found)
    except Exception:                               # noqa: BLE001
        pass
    return fallback


def default_places(environ=None) -> Places:
    """The usual places, for this user."""
    environ = os.environ if environ is None else environ
    home = environ.get("USERPROFILE") or os.path.expanduser("~")
    local = environ.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
    roaming = environ.get("APPDATA") or os.path.join(home, "AppData", "Roaming")
    programs = known_folder(FOLDERID_USER_PROGRAM_FILES, os.path.join(local, "Programs"))
    start_menu = known_folder(FOLDERID_PROGRAMS, os.path.join(roaming, "Microsoft", "Windows", "Start Menu",
                                                              "Programs"))
    desktop = known_folder(FOLDERID_DESKTOP, os.path.join(home, "Desktop"))
    return Places(os.path.join(programs, APP), start_menu, desktop, tempfile.gettempdir())


def settings_folder(environ=None) -> str:
    """Where the app keeps your settings on Windows (projectionist.gui.settings_places): never touched here."""
    environ = os.environ if environ is None else environ
    return os.path.join(environ.get("APPDATA") or os.path.expanduser("~"), APP)


# ---------------------------------------------------------------------------------------------------------
# The Installed-apps entry
# ---------------------------------------------------------------------------------------------------------
class WinRegistry:
    """The real HKEY_CURRENT_USER - only ever this app's own Uninstall key is written or deleted."""

    def __init__(self):
        import winreg
        self.winreg = winreg

    def read(self, key: str) -> dict | None:
        w = self.winreg
        try:
            with w.OpenKey(w.HKEY_CURRENT_USER, key) as k:
                values, i = {}, 0
                while True:
                    try:
                        name, value, _kind = w.EnumValue(k, i)
                    except OSError:
                        return values
                    values[name] = value
                    i += 1
        except OSError:
            return None

    def write(self, key: str, values: dict):
        w = self.winreg
        self.delete(key)                            # (no values left over from an older version)
        with w.CreateKeyEx(w.HKEY_CURRENT_USER, key, 0, w.KEY_WRITE) as k:
            for name, value in values.items():
                if isinstance(value, int):
                    w.SetValueEx(k, name, 0, w.REG_DWORD, value)
                else:
                    w.SetValueEx(k, name, 0, w.REG_SZ, str(value))

    def delete(self, key: str):
        w = self.winreg
        try:
            w.DeleteKey(w.HKEY_CURRENT_USER, key)
        except FileNotFoundError:
            pass


class FileRegistry:
    """HKEY_CURRENT_USER as a JSON file ({key: {name: [type, value]}}), for trying Setup out - or, with no file, in
    memory (tests)."""

    def __init__(self, path: str | None = None):
        self.path = os.path.abspath(path) if path else None
        self._memory: dict = {}

    def load(self) -> dict:
        if self.path is None:
            return json.loads(json.dumps(self._memory))
        try:
            with open(self.path, encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return {}

    def _save(self, data: dict):
        if self.path is None:
            self._memory = data
            return
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        temp = self.path + ".tmp"
        with open(temp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        os.replace(temp, self.path)

    def read(self, key: str) -> dict | None:
        values = self.load().get(key)
        return None if values is None else {name: value for name, (_kind, value) in values.items()}

    def kinds(self, key: str) -> dict:
        """{value name: 'REG_SZ' or 'REG_DWORD'} - the kinds the real registry would have been given."""
        return {name: kind for name, (kind, _value) in (self.load().get(key) or {}).items()}

    def write(self, key: str, values: dict):
        data = self.load()
        data[key] = {name: ["REG_DWORD" if isinstance(v, int) else "REG_SZ", v if isinstance(v, int) else str(v)]
                     for name, v in values.items()}
        self._save(data)

    def delete(self, key: str):
        data = self.load()
        if data.pop(key, None) is not None:
            self._save(data)


def uninstall_entry(folder: str, version: str, size: int, today: str | None = None) -> dict:
    """The values of the Installed-apps entry (strings are REG_SZ, numbers REG_DWORD)."""
    uninstaller = os.path.join(folder, UNINSTALLER)
    return {
        "DisplayName": APP,
        "DisplayVersion": version,
        "Publisher": PUBLISHER,
        "DisplayIcon": os.path.join(folder, EXE) + ",0",
        "UninstallString": f'"{uninstaller}"',
        "QuietUninstallString": f'"{uninstaller}" --silent',
        "InstallLocation": folder,
        "InstallDate": today or time.strftime("%Y%m%d"),
        "EstimatedSize": max(1, (size + 1023) // 1024),        # (in KB)
        "NoModify": 1,
        "NoRepair": 1,
    }


# ---------------------------------------------------------------------------------------------------------
# Shortcuts
# ---------------------------------------------------------------------------------------------------------
class Shortcuts:
    """Windows shortcuts (.lnk), made and read through the shell's own ShellLink object - no extra packages."""

    CLSID_SHELL_LINK = "{00021401-0000-0000-C000-000000000046}"
    IID_ISHELL_LINK_W = "{000214F9-0000-0000-C000-000000000046}"
    IID_IPERSIST_FILE = "{0000010B-0000-0000-C000-000000000046}"
    IID_IPROPERTY_STORE = "{886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99}"
    PKEY_APP_USER_MODEL_ID = ("{9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3}", 5)
    VT_LPWSTR = 31

    def __init__(self):
        if sys.platform != "win32":
            raise InstallError("Shortcuts can only be made on Windows.")
        import ctypes
        self.ctypes = ctypes
        self.ole32 = ctypes.WinDLL("ole32")
        c = ctypes

        class PropertyKey(c.Structure):
            _fields_ = [("fmtid", c.c_ubyte * 16), ("pid", c.c_ulong)]

        class PropVariant(c.Structure):
            _fields_ = [("vt", c.c_ushort), ("r1", c.c_ushort), ("r2", c.c_ushort), ("r3", c.c_ushort),
                        ("value", c.c_void_p), ("more", c.c_void_p)]
        self.PropertyKey, self.PropVariant = PropertyKey, PropVariant

    # -- COM by hand ------------------------------------------------------------------------------------------
    def _guid(self, text: str):
        return (self.ctypes.c_ubyte * 16).from_buffer_copy(uuid.UUID(text).bytes_le)

    def _call(self, obj, index: int, argtypes, *args, restype=None):
        c = self.ctypes
        vtable = c.cast(obj, c.POINTER(c.POINTER(c.c_void_p)))[0]
        method = c.WINFUNCTYPE(restype or c.c_long, c.c_void_p, *argtypes)(vtable[index])
        result = method(obj, *args)
        if restype is None and result < 0:
            raise OSError(f"a shell call failed (HRESULT 0x{result & 0xFFFFFFFF:08X})")
        return result

    def _query(self, obj, iid: str):
        c = self.ctypes
        out = c.c_void_p()
        self._call(obj, 0, [c.c_void_p, c.POINTER(c.c_void_p)], c.byref(self._guid(iid)), c.byref(out))
        return out

    def _release(self, obj):
        if obj and obj.value:
            self._call(obj, 2, [], restype=self.ctypes.c_ulong)

    def _link(self):
        """(a new ShellLink object, whether COM was started here)"""
        c = self.ctypes
        self.ole32.CoInitializeEx.argtypes = [c.c_void_p, c.c_ulong]
        self.ole32.CoInitializeEx.restype = c.c_long
        started = self.ole32.CoInitializeEx(None, 0x2) in (0, 1)     # (S_OK, S_FALSE; else it's on already)
        link = c.c_void_p()
        create = self.ole32.CoCreateInstance
        create.argtypes = [c.c_void_p, c.c_void_p, c.c_ulong, c.c_void_p, c.POINTER(c.c_void_p)]
        create.restype = c.c_long
        hr = create(c.byref(self._guid(self.CLSID_SHELL_LINK)), None, 0x1, c.byref(self._guid(self.IID_ISHELL_LINK_W)),
                    c.byref(link))
        if hr < 0:
            if started:
                self.ole32.CoUninitialize()
            raise OSError(f"Windows' ShellLink object isn't available (HRESULT 0x{hr & 0xFFFFFFFF:08X})")
        return link, started

    def _done(self, link, started):
        self._release(link)
        if started:
            self.ole32.CoUninitialize()

    def _key(self):
        guid, pid = self.PKEY_APP_USER_MODEL_ID
        return self.PropertyKey(self._guid(guid), pid)

    # -- what the installer uses ------------------------------------------------------------------------------
    def make(self, path: str, target: str, workdir: str = "", icon: str = "", description: str = "",
             app_id: str | None = None, arguments: str = ""):
        """Write the shortcut `path` (.lnk) to `target`."""
        c = self.ctypes
        text = c.c_wchar_p
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        link, started = self._link()
        try:
            self._call(link, 20, [text], target)                        # SetPath
            self._call(link, 11, [text], arguments)                     # SetArguments
            self._call(link, 9, [text], workdir)                        # SetWorkingDirectory
            self._call(link, 7, [text], description)                    # SetDescription
            if icon:
                self._call(link, 17, [text, c.c_int], icon, 0)         # SetIconLocation
            if app_id:
                store = self._query(link, self.IID_IPROPERTY_STORE)
                try:
                    value = c.create_unicode_buffer(app_id)
                    variant = self.PropVariant(self.VT_LPWSTR, 0, 0, 0, c.addressof(value), None)
                    key = self._key()
                    self._call(store, 6, [c.c_void_p, c.c_void_p], c.byref(key), c.byref(variant))   # SetValue
                    self._call(store, 7, [])                                                       # Commit
                finally:
                    self._release(store)
            persist = self._query(link, self.IID_IPERSIST_FILE)
            try:
                self._call(persist, 6, [text, c.c_int], os.path.abspath(path), 1)                  # Save
            finally:
                self._release(persist)
        finally:
            self._done(link, started)

    def read(self, path: str) -> dict:
        """What a shortcut holds: {'target', 'arguments', 'workdir', 'icon', 'icon_index', 'description',
        'app_id'}."""
        c = self.ctypes
        text = c.c_wchar_p
        link, started = self._link()
        try:
            persist = self._query(link, self.IID_IPERSIST_FILE)
            try:
                self._call(persist, 5, [text, c.c_ulong], os.path.abspath(path), 0)              # Load (STGM_READ)
            finally:
                self._release(persist)

            def string(index, *extra):
                buffer = c.create_unicode_buffer(1024)
                self._call(link, index, [text, c.c_int] + [e[0] for e in extra], buffer, 1024,
                           *[e[1] for e in extra])
                return buffer.value
            icon_index = c.c_int(0)
            found = {
                "target": string(3, (c.c_void_p, None), (c.c_ulong, 0x4)),          # GetPath (SLGP_RAWPATH)
                "arguments": string(10),                                            # GetArguments
                "workdir": string(8),                                               # GetWorkingDirectory
                "description": string(6),                                           # GetDescription
                "icon": string(16, (c.POINTER(c.c_int), c.byref(icon_index))),      # GetIconLocation
            }
            found["icon_index"] = icon_index.value
            found["app_id"] = None
            store = self._query(link, self.IID_IPROPERTY_STORE)
            try:
                variant, key = self.PropVariant(), self._key()
                self._call(store, 5, [c.c_void_p, c.c_void_p], c.byref(key), c.byref(variant))     # GetValue
                if variant.vt == self.VT_LPWSTR and variant.value:
                    found["app_id"] = c.wstring_at(variant.value)
                self.ole32.PropVariantClear(c.byref(variant))
            finally:
                self._release(store)
            return found
        finally:
            self._done(link, started)


# ---------------------------------------------------------------------------------------------------------
# The app to install
# ---------------------------------------------------------------------------------------------------------
def safe_name(name: str) -> bool:
    """Whether a name in the payload is a plain relative path inside the folder (forward slashes, no '..')."""
    parts = name.split("/")
    return (bool(name) and "\\" not in name and ":" not in name and not name.startswith("/")
            and all(p not in ("", ".", "..") for p in parts) and name != MANIFEST)


class Payload:
    """The app, as tools/build_exe.py packs it: a zip of the installed folder's files, with what it is - {'name',
    'version', ...} - as JSON in the zip's comment."""

    def __init__(self, path: str):
        self.path = path
        try:
            with zipfile.ZipFile(path) as z:
                info = json.loads(z.comment.decode("utf-8") or "{}")
                self.entries = [(i.filename, i.file_size) for i in z.infolist() if not i.is_dir()]
        except (OSError, zipfile.BadZipFile, UnicodeDecodeError, ValueError) as exc:
            raise InstallError(f"The app to install ({path}) can't be read: {exc}") from None
        if not isinstance(info, dict) or info.get("name") != APP or not isinstance(info.get("version"), str):
            raise InstallError(f"{path} isn't {APP}.")
        bad = [n for n, _s in self.entries if not safe_name(n)]
        if bad:
            raise InstallError(f"{path} has files that would land outside the folder: {', '.join(bad[:5])}")
        names = {n for n, _s in self.entries}
        missing = [n for n in (EXE, UNINSTALLER) if n not in names]
        if missing:
            raise InstallError(f"{path} has no {' or '.join(missing)}.")
        self.info = info
        self.version = info["version"]

    @property
    def files(self) -> list[str]:
        return [n for n, _s in self.entries]

    @property
    def size(self) -> int:
        return sum(s for _n, s in self.entries)

    def extract_to(self, folder: str, progress=None):
        """Every file into folder, each one whole or not at all (written beside, then put in place)."""
        total, done = max(self.size, 1), 0
        with zipfile.ZipFile(self.path) as z:
            for name, size in self.entries:
                target = os.path.join(folder, *name.split("/"))
                os.makedirs(os.path.dirname(target), exist_ok=True)
                temp = target + ".setup-new"
                try:
                    with z.open(name) as source, open(temp, "wb") as out:
                        while True:
                            block = source.read(1 << 20)
                            if not block:
                                break
                            out.write(block)
                    if os.path.exists(target):
                        os.chmod(target, stat.S_IWRITE | stat.S_IREAD)      # (a read-only file can't be replaced)
                    os.replace(temp, target)
                except BaseException:
                    try:
                        os.remove(temp)
                    except OSError:
                        pass
                    raise
                done += size
                if progress:
                    progress(done / total, name)


# ---------------------------------------------------------------------------------------------------------
# Installing and uninstalling
# ---------------------------------------------------------------------------------------------------------
def read_manifest(folder: str) -> dict | None:
    """What Setup installed in folder, or None (nothing of ours is there)."""
    try:
        with open(os.path.join(folder, MANIFEST), encoding="utf-8") as f:
            manifest = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(manifest, dict) or manifest.get("name") != APP or not isinstance(manifest.get("files"), list):
        return None
    return manifest


def _write_manifest(folder: str, manifest: dict):
    path = os.path.join(folder, MANIFEST)
    temp = path + ".tmp"
    with open(temp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    os.replace(temp, path)


def same_path(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def inside(folder: str, path: str) -> bool:
    folder, path = (os.path.normcase(os.path.abspath(p)) for p in (folder, path))
    return path.startswith(folder.rstrip("\\/") + os.sep)


def app_running(folder: str) -> bool:
    """Whether Projectionist.exe in folder is running: Windows won't let a running program's file be opened for
    writing - a sharing violation (not 'access denied', which is a read-only file)."""
    exe = os.path.join(folder, EXE)
    if sys.platform != "win32" or not os.path.isfile(exe):
        return False
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create = kernel32.CreateFileW
        create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                           wintypes.DWORD, wintypes.HANDLE]
        create.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        # read and write, sharing everything: only a program that won't share writing stops it
        handle = create(exe, 0x80000000 | 0x40000000, 0x1 | 0x2 | 0x4, None, 3, 0x80, None)
        if handle in (None, wintypes.HANDLE(-1).value):
            return ctypes.get_last_error() == 32            # ERROR_SHARING_VIOLATION
        kernel32.CloseHandle(handle)
        return False
    except Exception:                               # noqa: BLE001
        return False


def folder_problem(folder: str) -> str | None:
    """Why the app can't go in folder, or None."""
    if not folder or not os.path.isabs(folder):
        return "Choose a full path for the folder, such as C:\\Users\\You\\AppData\\Local\\Programs\\Projectionist."
    folder = os.path.abspath(folder)
    if os.path.dirname(folder) == folder:
        return f"Choose a folder for the app, not the whole of {folder}."
    if os.path.exists(folder) and not os.path.isdir(folder):
        return f"{folder} is a file, not a folder."
    if os.path.isdir(folder) and read_manifest(folder) is None:
        try:
            if os.listdir(folder):
                return f"{folder} already has other things in it. Choose an empty folder, or a new one."
        except OSError as exc:
            return f"{folder} can't be opened: {exc.strerror or exc}"
    return None


def _prune(folder: str, paths):
    """Remove the folders the removed files were in, deepest first, while they're empty - never above `folder`."""
    folders = set()
    for path in paths:
        here = os.path.dirname(path)
        while inside(folder, here):
            folders.add(here)
            here = os.path.dirname(here)
    for here in sorted(folders, key=len, reverse=True):
        try:
            os.rmdir(here)
        except OSError:
            pass


def install(payload: Payload, places: Places, *, registry, desktop: bool | None = None, shortcuts=None,
            progress=None, today: str | None = None) -> dict:
    """Install (or update) the app in places.install_dir: the files, the Start-menu shortcut (and the desktop one,
    when desktop is True - None: as the install being updated had it), the Installed-apps entry, and the manifest
    the uninstaller works from. -> the manifest."""
    problem = folder_problem(places.install_dir)
    if problem:
        raise InstallError(problem)
    folder = os.path.abspath(places.install_dir)
    old = read_manifest(folder)
    if old is not None and app_running(folder):
        raise AppRunning(f"{APP} is running. Close it, then try again.")
    old_links = [p for p in old.get("shortcuts", []) if isinstance(p, str)] if old else []
    start_link = os.path.join(places.start_menu_dir, SHORTCUT)
    desktop_link = os.path.join(places.desktop_dir, SHORTCUT)
    if desktop is None:
        desktop = bool(old and old.get("desktop"))
    shortcuts = Shortcuts() if shortcuts is None else shortcuts

    def say(fraction, text):
        if progress:
            progress(fraction, text)
    say(0.0, "Copying files...")
    os.makedirs(folder, exist_ok=True)
    payload.extract_to(folder, lambda f, name: say(0.9 * f, f"Copying {name}"))
    new_files = set(payload.files)
    stale = [os.path.join(folder, *n.split("/")) for n in (old or {}).get("files", [])
             if isinstance(n, str) and safe_name(n) and n not in new_files]
    for path in stale:                                           # (what the older version had and this one hasn't)
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
    _prune(folder, stale)
    say(0.92, "Adding the shortcuts...")
    exe = os.path.join(folder, EXE)
    links = [start_link] + ([desktop_link] if desktop else [])
    for link in links:
        shortcuts.make(link, exe, workdir=folder, icon=exe, description=DESCRIPTION, app_id=APP_ID)
    for link in old_links:
        if not any(same_path(link, new) for new in links) and os.path.isfile(link):
            os.remove(link)                                      # (the desktop one, when it's no longer wanted)
    manifest = {"name": APP, "version": payload.version, "folder": folder, "files": sorted(new_files),
                "shortcuts": links, "desktop": bool(desktop), "size": payload.size,
                "installed": time.strftime("%Y-%m-%d %H:%M:%S"), "registry_key": "HKEY_CURRENT_USER\\" + UNINSTALL_KEY}
    _write_manifest(folder, manifest)
    say(0.96, "Adding it to Installed apps...")
    registry.write(UNINSTALL_KEY, uninstall_entry(folder, payload.version, payload.size, today))
    say(1.0, "Done")
    return manifest


@dataclass
class Removal:
    folder: str
    version: str
    removed: int                  # files
    shortcuts: int
    entry: bool                   # the Installed-apps entry was this install's, and is gone
    folder_gone: bool
    kept: list                    # files left (the running uninstaller itself; things you put there)


def uninstall(folder: str, *, registry, keep: str | None = None) -> Removal:
    """Remove what Setup installed in folder: its shortcuts, its files (not `keep` - the uninstaller, while it
    runs - nor anything else put there since), the Installed-apps entry if it's this install's, and the folder once
    it's empty. Your settings are never touched."""
    folder = os.path.abspath(folder)
    manifest = read_manifest(folder)
    if manifest is None:
        raise InstallError(f"{APP} isn't installed in {folder} (there's no {MANIFEST} there).")
    if app_running(folder):
        raise AppRunning(f"{APP} is running. Close it, then try again.")
    links = 0
    for link in manifest.get("shortcuts", []):
        if isinstance(link, str) and link.lower().endswith(".lnk") and os.path.isfile(link):
            os.remove(link)
            links += 1
    removed, kept, failed = 0, [], []
    paths = [os.path.join(folder, *n.split("/")) for n in manifest["files"] if isinstance(n, str) and safe_name(n)]
    for path in paths:
        if keep and same_path(path, keep):
            kept.append(path)
            continue
        try:
            os.remove(path)
            removed += 1
        except FileNotFoundError:
            pass
        except OSError as exc:
            failed.append(f"{path} ({exc.strerror or exc})")
    if failed:
        raise InstallError("These files couldn't be removed:\n" + "\n".join(failed[:10]))
    os.remove(os.path.join(folder, MANIFEST))
    entry = registry.read(UNINSTALL_KEY)
    ours = bool(entry) and same_path(entry.get("InstallLocation"), folder)
    if ours:
        registry.delete(UNINSTALL_KEY)
    _prune(folder, paths)
    try:
        os.rmdir(folder)
    except OSError:
        pass
    for here, _dirs, names in os.walk(folder):
        kept += [os.path.join(here, n) for n in names if not any(same_path(os.path.join(here, n), k) for k in kept)]
    return Removal(folder, str(manifest.get("version", "")), removed, links, ours, not os.path.exists(folder), kept)


def deleter_command(path: str, folder: str | None = None, comspec: str | None = None) -> str:
    """A hidden command prompt that deletes `path` (the uninstaller, once it has ended - it can't delete itself
    while it runs), trying every second for a minute, then removes `folder` if that's empty."""
    comspec = comspec or os.environ.get("COMSPEC") or "cmd.exe"
    after = f'rmdir "{folder}" >nul 2>&1 & ' if folder else ""
    loop = (f'for /l %i in (1,1,60) do @(if exist "{path}" (del /f /q "{path}" >nul 2>&1 & '
            f'ping -n 2 127.0.0.1 >nul) else ({after}exit))')
    return f'"{comspec}" /d /q /s /c "{loop}"'


def move_self_out(exe: str, folder: str, temp_dir: str) -> str:
    """Uninstall.exe runs from the folder it removes, and a running program's file can't be deleted - but it can
    be moved (on the same drive). So it's moved to temp_dir and the folder, empty now, removed; delete_later then
    deletes the moved file once this has ended. -> where the file is now."""
    try:
        os.chdir(temp_dir)                          # (a folder a program is in can't be removed)
    except OSError:
        pass
    moved = os.path.join(temp_dir, f"~{APP}-uninstall-{uuid.uuid4().hex[:8]}.exe")
    try:
        os.replace(exe, moved)
    except OSError:
        moved = exe                                 # (another drive: it's deleted where it is, then the folder)
    try:
        os.rmdir(folder)
    except OSError:
        pass
    return moved


def delete_later(path: str, folder: str | None, temp_dir: str, spawn=None):
    """Have a hidden command prompt delete `path` (and then remove `folder`, if it's empty) once this program has
    ended - the last thing the uninstaller does."""
    command = deleter_command(path, folder)
    if spawn is None:
        def spawn(cmd):
            subprocess.Popen(cmd, cwd=temp_dir, creationflags=0x08000000 | 0x00000200,   # no window, own group
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             close_fds=True)
    try:
        spawn(command)
    except OSError:
        pass


# ---------------------------------------------------------------------------------------------------------
# The window
# ---------------------------------------------------------------------------------------------------------
def user_settings(environ=None) -> dict:
    """Your saved settings (read only), for the look you chose - {} when there are none."""
    try:
        with open(os.path.join(settings_folder(environ), "settings.json"), encoding="utf-8-sig") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def apply_look(root, settings: dict | None = None) -> str | None:
    """Setup's window in the look the app shows (Graphite unless you chose another), with the app's icon.
    -> the look, or None where the app's looks aren't there (a plain window then)."""
    try:
        from projectionist import appicon
        appicon.apply(root)
    except Exception:                               # noqa: BLE001
        pass
    try:
        from projectionist.ui import theme
        theme.quiet_theme_changes(root)
        look = theme.chosen_look(user_settings() if settings is None else settings)
        theme.apply(root, look)
        return look
    except Exception:                               # noqa: BLE001
        return None


class SetupWindow:
    """Setup's window: where to install and whether to put a shortcut on the desktop, then the copying, then
    done (and, if ticked, the app opened). Built on a root the caller shows (run_wizard) - tests drive it hidden."""

    def __init__(self, root, payload: Payload, places: Places, registry, *, desktop: bool | None = None,
                 settings: dict | None = None, shortcuts=None, launch=None, log=None):
        import tkinter as tk
        from tkinter import ttk
        self.tk, self.ttk = tk, ttk
        self.root, self.payload, self.places, self.registry = root, payload, places, registry
        self.shortcuts, self.log = shortcuts, log
        self.launch = launch or _launch
        self.state = "choose"                       # choose | working | done | closed
        self.manifest = None
        self.error = ""
        self.look = apply_look(root, settings)
        self._queue: queue.Queue = queue.Queue()
        root.title(f"{APP} {payload.version} Setup")
        root.resizable(False, False)
        old = read_manifest(places.install_dir)
        self.dir_var = tk.StringVar(value=places.install_dir)
        self.desktop_var = tk.BooleanVar(value=bool(old and old.get("desktop")) if desktop is None else desktop)
        self.open_var = tk.BooleanVar(value=True)
        self.note_var = tk.StringVar()
        self.detail_var = tk.StringVar()
        self._build()
        self.dir_var.trace_add("write", lambda *_a: self.describe())
        self.describe()
        root.bind("<Return>", lambda _e: self.default_action())
        root.bind("<Escape>", lambda _e: self.cancel())
        root.protocol("WM_DELETE_WINDOW", self.cancel)

    def _scale(self) -> float:
        try:
            return max(1.0, float(self.root.tk.call("tk", "scaling")) / (96 / 72))
        except Exception:                           # noqa: BLE001
            return 1.0

    def _build(self):
        tk, ttk = self.tk, self.ttk
        s = self._scale()
        wrap = int(470 * s)
        outer = ttk.Frame(self.root, padding=int(18 * s))
        outer.pack(fill="both", expand=True)
        self.pages = {}

        choose = ttk.Frame(outer)
        ttk.Label(choose, text=f"Install {APP} {self.payload.version}", style="Title.TLabel").pack(anchor="w")
        ttk.Label(choose, wraplength=wrap, justify="left",
                  text=f"{APP} explores your Plex movie collection and exports it to a spreadsheet. It's installed "
                       "for you only - no administrator rights needed.").pack(anchor="w", pady=(6, 14))
        ttk.Label(choose, text="Install it in:").pack(anchor="w")
        row = ttk.Frame(choose)
        row.pack(fill="x", pady=(2, 10))
        self.dir_entry = ttk.Entry(row, textvariable=self.dir_var, width=60)
        self.dir_entry.pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Browse...", command=self.browse).pack(side="left", padx=(6, 0))
        ttk.Label(choose, text="A shortcut goes in the Start menu.").pack(anchor="w")
        ttk.Checkbutton(choose, text="Put a shortcut on the desktop too",
                        variable=self.desktop_var).pack(anchor="w", pady=(4, 0))
        self.note = ttk.Label(choose, textvariable=self.note_var, wraplength=wrap, justify="left")
        self.note.pack(anchor="w", pady=(12, 0))
        self.pages["choose"] = choose

        working = ttk.Frame(outer)
        ttk.Label(working, text=f"Installing {APP} {self.payload.version}...", style="Title.TLabel").pack(anchor="w")
        self.bar = ttk.Progressbar(working, mode="determinate", maximum=1.0, length=int(470 * s))
        self.bar.pack(fill="x", pady=(18, 6))
        ttk.Label(working, textvariable=self.detail_var, style="Hint.TLabel", wraplength=wrap).pack(anchor="w")
        self.pages["working"] = working

        done = ttk.Frame(outer)
        ttk.Label(done, text=f"{APP} is installed", style="Title.TLabel").pack(anchor="w")
        self.done_text = ttk.Label(done, wraplength=wrap, justify="left")
        self.done_text.pack(anchor="w", pady=(6, 14))
        ttk.Checkbutton(done, text=f"Open {APP} now", variable=self.open_var).pack(anchor="w")
        self.pages["done"] = done

        buttons = ttk.Frame(outer)
        buttons.pack(side="bottom", fill="x", pady=(18, 0))
        self.cancel_button = ttk.Button(buttons, text="Cancel", command=self.cancel)
        self.cancel_button.pack(side="right")
        self.go_button = ttk.Button(buttons, text="Install", command=self.default_action, default="active")
        self.go_button.pack(side="right", padx=(0, 6))
        self.show("choose")
        self.root.update_idletasks()                # (the first page's size is the window's: it stays put)
        self.root.minsize(self.root.winfo_reqwidth(), self.root.winfo_reqheight())

    def show(self, page: str):
        for name, frame in self.pages.items():
            if name == page:
                frame.pack(fill="both", expand=True)
            else:
                frame.pack_forget()

    def describe(self):
        """The line under the choices: what installing in that folder will do, or why it can't."""
        folder = self.dir_var.get().strip().strip('"')
        problem = folder_problem(folder)
        old = None if problem else read_manifest(folder)
        if problem:
            text, style = problem, "Bad.TLabel"
        elif old:
            was = old.get("version", "")
            text = (f"{APP} {was} is installed there: it will be replaced by {self.payload.version}."
                    if was != self.payload.version else f"{APP} {was} is installed there already: it will be "
                                                        "installed again.")
            text += " Your settings are kept."
            style = "TLabel"
        else:
            text, style = "", "TLabel"
        if self.error:
            text, style = self.error, "Bad.TLabel"
        self.note_var.set(text)
        self.note.configure(style=style)
        self.go_button.configure(state="disabled" if problem else "normal")
        return problem

    def browse(self):
        from tkinter import filedialog
        start = self.dir_var.get().strip()
        chosen = filedialog.askdirectory(parent=self.root, title=f"Install {APP} in",
                                         initialdir=os.path.dirname(start) if start else None, mustexist=False)
        if chosen:
            chosen = os.path.normpath(chosen)
            if os.path.basename(chosen).lower() != APP.lower() and read_manifest(chosen) is None:
                chosen = os.path.join(chosen, APP)          # (a folder of its own in the one chosen)
            self.error = ""
            self.dir_var.set(chosen)

    def default_action(self):
        if self.state == "choose":
            self.install()
        elif self.state == "done":
            self.finish()

    def install(self):
        """Start installing in the background; the window follows it (poll)."""
        self.error = ""
        if self.describe():
            return
        places = Places(os.path.abspath(self.dir_var.get().strip().strip('"')), self.places.start_menu_dir,
                        self.places.desktop_dir, self.places.temp_dir)
        desktop = bool(self.desktop_var.get())
        self.state = "working"
        self.show("working")
        self.go_button.configure(state="disabled")
        self.cancel_button.configure(state="disabled")
        self.bar["value"] = 0

        def work():
            try:
                manifest = install(self.payload, places, registry=self.registry, desktop=desktop,
                                   shortcuts=self.shortcuts,
                                   progress=lambda f, text: self._queue.put(("progress", f, text)))
                self._queue.put(("done", manifest))
            except InstallError as exc:
                self._queue.put(("failed", str(exc)))
            except Exception as exc:                # noqa: BLE001
                if self.log:
                    self.log.write(traceback.format_exc())
                self._queue.put(("failed", f"Couldn't install {APP}: {exc}"))
        threading.Thread(target=work, name="install", daemon=True).start()
        self.root.after(40, self.poll)

    def poll(self):
        if self.state != "working":
            return
        try:
            while True:
                message = self._queue.get_nowait()
                if message[0] == "progress":
                    self.bar["value"] = message[1]
                    self.detail_var.set(message[2])
                elif message[0] == "done":
                    self._installed(message[1])
                    return
                elif message[0] == "failed":
                    self._failed(message[1])
                    return
        except queue.Empty:
            pass
        self.root.after(40, self.poll)

    def _installed(self, manifest: dict):
        self.manifest = manifest
        self.state = "done"
        where = "the Start menu" + (" and on your desktop" if manifest.get("desktop") else "")
        self.done_text.configure(text=f"{APP} {manifest['version']} is in {manifest['folder']}. Find it in {where}.\n\n"
                                      "To remove it: Settings > Apps > Installed apps > Projectionist > Uninstall. "
                                      "Your settings are kept.")
        if self.log:
            self.log.write(f"Installed {APP} {manifest['version']} in {manifest['folder']}")
        self.show("done")
        self.go_button.configure(text="Finish", state="normal")
        self.cancel_button.pack_forget()

    def _failed(self, message: str):
        self.state = "choose"
        self.error = message
        if self.log:
            self.log.write(message)
        self.show("choose")
        self.cancel_button.configure(state="normal")
        self.describe()

    def finish(self):
        if self.state == "done" and self.open_var.get() and self.manifest:
            try:
                self.launch(os.path.join(self.manifest["folder"], EXE))
            except OSError:
                pass
        self.close()

    def cancel(self):
        if self.state == "working":
            return                                  # (copying: it only takes a moment)
        self.close()

    def close(self):
        if self.state != "done":
            self.state = "closed"
        try:
            self.root.destroy()
        except Exception:                           # noqa: BLE001
            pass


def _launch(exe: str):
    subprocess.Popen([exe], cwd=os.path.dirname(exe), close_fds=True, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def run_wizard(payload: Payload, places: Places, registry, desktop=None, log=None) -> int:
    import tkinter as tk
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:                               # noqa: BLE001
        pass
    root = tk.Tk()
    root.withdraw()
    window = SetupWindow(root, payload, places, registry, desktop=desktop, log=log)
    root.update_idletasks()
    width, height = root.winfo_reqwidth(), root.winfo_reqheight()
    root.geometry(f"+{max(0, (root.winfo_screenwidth() - width) // 2)}+"
                  f"{max(0, (root.winfo_screenheight() - height) // 3)}")
    root.deiconify()
    try:
        from projectionist.ui import theme
        theme.title_bar(root)
    except Exception:                               # noqa: BLE001
        pass
    root.mainloop()
    return EXIT_OK if window.state == "done" else EXIT_CANCELLED


# ---------------------------------------------------------------------------------------------------------
# The command line
# ---------------------------------------------------------------------------------------------------------
class Log:
    """What happened, added to --log's file (when given) and printed (when there's a console)."""

    def __init__(self, path: str | None = None):
        self.path = path
        self.lines: list[str] = []

    def write(self, text: str):
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {text.rstrip()}"
        self.lines.append(line)
        if self.path:
            try:
                os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError:
                pass
        if sys.stdout is not None:
            try:
                print(line)
            except (OSError, ValueError):
                pass


def _message(text: str, kind: str = "info", title: str = f"{APP} Setup") -> bool:
    """A Windows message box (no Tk needed: Uninstall.exe hasn't it). kind: 'info', 'error' or 'ask' (-> Yes)."""
    flags = {"info": 0x40, "error": 0x10, "ask": 0x4 | 0x20 | 0x100}[kind] | 0x10000      # (to the front)
    try:
        import ctypes
        return ctypes.windll.user32.MessageBoxW(None, text, title, flags) == 6          # IDYES
    except Exception:                               # noqa: BLE001
        if sys.stdout is not None:
            print(text)
        return False


message = _message                                  # (tests put their own here: nothing is ever shown by them)


def bundled_payload() -> str | None:
    """The app packed inside Setup.exe, or None (this is Uninstall.exe, or a script)."""
    folder = getattr(sys, "_MEIPASS", None)
    path = os.path.join(folder, PAYLOAD) if folder else None
    return path if path and os.path.isfile(path) else None


def installed_folder(registry) -> str | None:
    """Where the Installed-apps entry says the app is, when it's really there."""
    try:
        entry = registry.read(UNINSTALL_KEY) or {}
    except OSError:
        return None
    folder = entry.get("InstallLocation")
    return folder if isinstance(folder, str) and read_manifest(folder) is not None else None


def parse_args(argv, uninstaller: bool):
    parser = argparse.ArgumentParser(
        prog=UNINSTALLER if uninstaller else f"{APP}-Setup.exe",
        description=f"Uninstall {APP}." if uninstaller else f"Install {APP} for you only (no administrator rights).")
    parser.add_argument("--silent", action="store_true", help="no windows: do it with the defaults and the options")
    parser.add_argument("--uninstall", action="store_true", help=f"remove {APP} instead")
    parser.add_argument("--install-dir", help="the folder the app goes in (or is removed from)")
    parser.add_argument("--start-menu-dir", help="the folder for the Start-menu shortcut")
    parser.add_argument("--desktop-dir", help="the folder for the desktop shortcut")
    desk = parser.add_mutually_exclusive_group()
    desk.add_argument("--desktop", dest="desktop", action="store_true", default=None, help="a desktop shortcut too")
    desk.add_argument("--no-desktop", dest="desktop", action="store_false", help="no desktop shortcut")
    parser.add_argument("--registry-file", help="keep the Installed-apps entry in this JSON file, not the registry")
    parser.add_argument("--temp-dir", help="where the uninstaller moves itself to be deleted")
    parser.add_argument("--log", help="add what happened to this file")
    parser.add_argument("--payload", help="the app to install (a payload zip made by tools/build_exe.py)")
    parser.add_argument("--version", action="store_true", help="print the version")
    return parser.parse_args(argv)


def resolve_places(args, registry, uninstalling: bool) -> Places:
    """The places the options give, the usual ones for the rest (looked up only when needed)."""
    usual = None

    def default(name):
        nonlocal usual
        usual = usual or default_places()
        return getattr(usual, name)
    folder = args.install_dir
    if not folder:
        frozen_here = getattr(sys, "frozen", False) and uninstalling and \
            os.path.basename(sys.executable).lower() == UNINSTALLER.lower()
        folder = (os.path.dirname(os.path.abspath(sys.executable)) if frozen_here else None) or \
            installed_folder(registry) or default("install_dir")
    return Places(os.path.abspath(folder), args.start_menu_dir or default("start_menu_dir"),
                  args.desktop_dir or default("desktop_dir"), args.temp_dir or default("temp_dir"))


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    payload_path = None
    if "--payload" not in argv:
        payload_path = bundled_payload()
    try:
        args = parse_args(argv, uninstaller=payload_path is None and "--payload" not in argv)
    except SystemExit as exc:                       # (--help, or a wrong option: the console says, if there is one)
        return EXIT_OK if not exc.code else EXIT_USAGE
    payload_path = args.payload or payload_path
    uninstalling = args.uninstall or payload_path is None
    log = Log(args.log)
    try:
        if args.version:
            if uninstalling:
                here = read_manifest(os.path.dirname(os.path.abspath(sys.executable))) or {}
                text = f"{APP} uninstaller" + (f" ({APP} {here['version']})" if here.get("version") else "")
            else:
                text = f"{APP} {Payload(payload_path).version} Setup"
            if sys.stdout is not None:
                print(text)
            return EXIT_OK
        registry = FileRegistry(args.registry_file) if args.registry_file else WinRegistry()
        places = resolve_places(args, registry, uninstalling)
        if uninstalling:
            return _uninstall(args, places, registry, log)
        return _install(args, payload_path, places, registry, log)
    except BaseException as exc:                    # noqa: BLE001
        if isinstance(exc, SystemExit):
            raise
        log.write(traceback.format_exc())
        if not args.silent:
            message(f"Something went wrong:\n\n{exc}", "error")
        return EXIT_FAILED


def _install(args, payload_path: str, places: Places, registry, log: Log) -> int:
    try:
        payload = Payload(payload_path)
    except InstallError as exc:
        log.write(str(exc))
        if not args.silent:
            message(str(exc), "error")
        return EXIT_FAILED
    if not args.silent:
        return run_wizard(payload, places, registry, desktop=args.desktop, log=log)
    try:
        manifest = install(payload, places, registry=registry, desktop=args.desktop)
    except AppRunning as exc:
        log.write(str(exc))
        return EXIT_RUNNING
    except InstallError as exc:
        log.write(str(exc))
        return EXIT_FAILED
    log.write(f"Installed {APP} {manifest['version']} in {manifest['folder']} ({len(manifest['files'])} files)")
    for link in manifest["shortcuts"]:
        log.write(f"Shortcut: {link}")
    log.write(f"Installed-apps entry: HKEY_CURRENT_USER\\{UNINSTALL_KEY}" +
              (f" (kept in {registry.path})" if isinstance(registry, FileRegistry) and registry.path else ""))
    return EXIT_OK


def _uninstall(args, places: Places, registry, log: Log) -> int:
    folder = places.install_dir
    manifest = read_manifest(folder)
    title = f"Uninstall {APP}"
    if manifest is None:
        text = f"{APP} isn't installed in {folder}."
        log.write(text)
        if not args.silent:
            message(text, "error", title)
        return EXIT_FAILED
    if not args.silent and not message(
            f"Remove {APP} {manifest.get('version', '')} from this computer?\n\nIts files in {folder}, its shortcuts "
            f"and its entry in Installed apps are removed. Your settings are kept.", "ask", title):
        return EXIT_CANCELLED
    me = os.path.abspath(sys.executable) if getattr(sys, "frozen", False) and inside(folder, sys.executable) else None
    try:
        removal = uninstall(folder, registry=registry, keep=me)
    except AppRunning as exc:
        log.write(str(exc))
        if not args.silent:
            message(str(exc), "error", title)
        return EXIT_RUNNING
    except InstallError as exc:
        log.write(str(exc))
        if not args.silent:
            message(str(exc), "error", title)
        return EXIT_FAILED
    moved = None
    if me:
        moved = move_self_out(me, folder, places.temp_dir)
        removal.kept = [k for k in removal.kept if not same_path(k, me)]
        removal.folder_gone = not os.path.exists(folder)
        log.write(f"The uninstaller moved itself to {moved}, to be deleted once it has ended")
    log.write(f"Removed {APP} {removal.version} from {folder}: {removal.removed} files, {removal.shortcuts} shortcuts"
              + (", the Installed-apps entry" if removal.entry else ""))
    for path in removal.kept:
        log.write(f"Left (not {APP}'s): {path}")
    if not args.silent:
        message(f"{APP} has been removed.\n\nYour settings are still in {settings_folder()}, for if you install it "
                "again.", "info", title)
    if moved:
        delete_later(moved, folder if same_path(moved, me) else None, places.temp_dir)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
