"""Finding Plex database dumps on disk, naming the spreadsheets made from them, and opening them."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import datetime

from . import APP_NAME
from .extract import is_candidate_name, pick_library_member

SQLITE_HEADER = b"SQLite format 3\x00"
_DATE_IN_NAME = re.compile(r"(\d{4}-\d{2}-\d{2})")
_SKIP_SUFFIXES = (".xlsx", ".csv", ".tmp", ".txt", ".log", ".py", ".pyw", ".json")
# What a file is called while it's still being copied or downloaded (browsers, download managers, WinSCP, torrent
# clients); rsync's and Office's half-written files start with '.' or '~$'
_PARTIAL_SUFFIXES = (".part", ".partial", ".crdownload", ".download", ".filepart", ".!qb", ".!ut", ".temp")
_PARTIAL_PREFIXES = (".", "~$")
STILL_WRITING = 120          # seconds: a file changed this recently that ends in empty space is still being copied


def is_candidate_database(name: str) -> bool:
    """Plex library databases, their automatic backups and 'Download database' zips - never the blobs database,
    SQLite's -wal / -shm side files, or a file still being copied or downloaded."""
    n = name.lower()
    if n.endswith(_SKIP_SUFFIXES + _PARTIAL_SUFFIXES) or n.startswith(_PARTIAL_PREFIXES):
        return False
    if n.endswith(".zip"):
        return "plex" in n
    return is_candidate_name(n)


def is_partial_copy(path: str) -> bool:
    """A database that isn't all there (yet): named as a file being copied or downloaded; shorter than the database
    its own header describes (a copy cut short, or still going); or changed in the last STILL_WRITING seconds and
    ending in empty space (a copy that sets the file's full size first, then fills it in). A zip cut short doesn't
    open at all (looks_like_library)."""
    name = os.path.basename(path).lower()
    if name.endswith(_PARTIAL_SUFFIXES) or name.startswith(_PARTIAL_PREFIXES):
        return True
    try:
        with open(path, "rb") as f:
            head = f.read(100)
            if head[:16] != SQLITE_HEADER:
                return False
            if len(head) < 100:
                return True
            size = os.fstat(f.fileno()).st_size
            page_size = int.from_bytes(head[16:18], "big")
            page_size = 65536 if page_size == 1 else page_size
            pages = int.from_bytes(head[28:32], "big")
            if pages and head[24:28] == head[92:96] and size < pages * page_size:    # (the page count is current)
                return True
            free_pages = int.from_bytes(head[36:40], "big")      # (a page on the free list may be blank)
            recent = time.time() - os.path.getmtime(path) < STILL_WRITING
            if recent and not free_pages and size >= 2 * page_size >= 1024:
                f.seek(size - page_size)
                if not f.read(page_size).strip(b"\0"):
                    return True
    except OSError:
        return False
    return False


def dump_date(path: str) -> str:
    """'YYYY-MM-DD' for a database file: from Plex's backup name if present, else its modified date."""
    m = _DATE_IN_NAME.search(os.path.basename(path))
    if m:
        return m.group(1)
    try:
        return datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d")
    except OSError:
        return datetime.now().strftime("%Y-%m-%d")


def looks_like_library(path: str) -> bool:
    """Quick look inside: an SQLite file that's all there (is_partial_copy), or a zip holding a Plex library
    database.

    Weeds out look-alikes such as Windows' Thumbs.db or the 'Download logs' zip that Plex saves
    alongside its 'Download database' zip, and copies still being made.
    """
    try:
        with open(path, "rb") as f:
            head = f.read(16)
    except OSError:
        return False
    if head == SQLITE_HEADER:
        return not is_partial_copy(path)
    if head[:4] == b"PK\x03\x04":
        try:
            with zipfile.ZipFile(path) as z:
                return pick_library_member(z.infolist()) is not None
        except Exception:   # damaged zip
            return False
    return False


def backup_order(path: str) -> tuple[str, float]:
    """How new a database is, for putting databases in order: the backup date in its name (else the day it was
    last changed), then when it was last changed."""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = 0.0
    return dump_date(path), mtime


def is_newer(path: str, than: str) -> bool:
    """Whether a database is newer than another, by backup_order."""
    return backup_order(path) > backup_order(than)


def same_file(a: str | None, b: str | None) -> bool:
    """Whether two paths are the same file (however they're spelt)."""
    if not a or not b:
        return False
    if os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b)):
        return True
    try:
        return os.path.samefile(a, b)
    except (OSError, ValueError):
        return False


def database_candidates(folder: str) -> list[str]:
    """Plausible Plex databases in a folder, newest first (by the date in the name, then modified time)."""
    try:
        entries = [e for e in os.scandir(folder) if e.is_file() and is_candidate_database(e.name)]
    except OSError:
        return []
    entries.sort(key=lambda e: backup_order(e.path), reverse=True)
    return [e.path for e in entries if looks_like_library(e.path)]


def find_newest_database(folder: str) -> str | None:
    """The newest dump in a folder that really opens as a Plex database with movie libraries.

    If the newest file turns out to be unusable (damaged, not Plex...), the next newest is tried.
    """
    import sqlite3

    from .extract import PlexDBError, list_movie_libraries
    for path in database_candidates(folder):
        try:
            if list_movie_libraries(path):
                return path
        except (PlexDBError, sqlite3.DatabaseError, OSError):   # damaged, unreadable... try the next one
            continue
    return None


def newest_backup(folders) -> tuple[str | None, str | None]:
    """(the newest Plex database in the first of the folders that has one - find_newest_database -, that folder).
    Empty entries, folders that aren't there and a folder given twice are passed over. (None, None): none has one."""
    seen = set()
    for folder in folders:
        if not folder or not os.path.isdir(folder):
            continue
        key = os.path.normcase(os.path.abspath(folder))
        if key in seen:
            continue
        seen.add(key)
        found = find_newest_database(folder)
        if found:
            return found, folder
    return None, None


# The last choice for opening spreadsheets: whatever the system opens .xlsx files with (xdg-open on Linux)
DEFAULT_APP = "Windows default app" if sys.platform == "win32" else "System default app"

# LibreOffice away from Windows: its programs on the PATH (distribution packages, the snap's /snap/bin, the
# libreoffice.org packages' 'libreoffice25.2'...), then the Flatpak's, then a Mac's app bundle
_CALC_COMMANDS = ("localc", "libreoffice", "soffice")
_FLATPAK_CALC = ("/var/lib/flatpak/exports/bin/org.libreoffice.LibreOffice",
                 "~/.local/share/flatpak/exports/bin/org.libreoffice.LibreOffice")
_MAC_CALC = ("/Applications/LibreOffice.app/Contents/MacOS/soffice",
             "~/Applications/LibreOffice.app/Contents/MacOS/soffice")


def find_spreadsheet_apps() -> list[tuple[str, str | None]]:
    """(label, program path) for spreadsheet apps on this PC, most likely wanted first.

    The last entry is always (DEFAULT_APP, None): whatever the system opens .xlsx files with.
    """
    apps = []
    if sys.platform == "win32":
        calc = _windows_app_path("scalc.exe") or _libreoffice_install() or _windows_app_path("soffice.exe")
        if calc:
            apps.append(("LibreOffice Calc", calc))
        excel = _windows_app_path("excel.exe")
        if excel:
            apps.append(("Microsoft Excel", excel))
    else:
        calc = find_libreoffice()
        if calc:
            apps.append(("LibreOffice Calc", calc))
        gnumeric = shutil.which("gnumeric")
        if gnumeric:
            apps.append(("Gnumeric", gnumeric))
    apps.append((DEFAULT_APP, None))
    return apps


def find_libreoffice(path: str | None = None) -> str | None:
    """LibreOffice on Linux or a Mac: a program on the PATH (or `path`, a PATH of its own), a versioned one from
    libreoffice.org's own packages ('libreoffice25.2'), the Flatpak, or the Mac app. Any of them opens an .xlsx in
    Calc. None when there's none."""
    for name in _CALC_COMMANDS:
        found = shutil.which(name, path=path)
        if found:
            return found
    folders = (path if path is not None else os.environ.get("PATH", "")).split(os.pathsep)
    for folder in folders:
        try:
            names = [n for n in os.listdir(folder or ".") if re.fullmatch(r"libreoffice\d+(\.\d+)*", n)]
        except OSError:
            continue
        for name in sorted(names, key=lambda n: [int(x) for x in n[len("libreoffice"):].split(".")], reverse=True):
            full = os.path.join(folder, name)                    # (the newest version first)
            if os.path.isfile(full) and os.access(full, os.X_OK):
                return full
    for candidate in _FLATPAK_CALC + (_MAC_CALC if sys.platform == "darwin" else ()):
        full = os.path.expanduser(candidate)
        if os.path.isfile(full) and os.access(full, os.X_OK):
            return full
    return None


def _windows_app_path(exe: str) -> str | None:
    """Where Windows' 'App Paths' registry says a program lives (how Run / Start find it)."""
    import winreg
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(root, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}") as key:
                path = os.path.expandvars(str(winreg.QueryValueEx(key, "")[0]).strip().strip('"'))
        except OSError:
            continue
        if os.path.isfile(path):
            return path
    return None


def _libreoffice_install() -> str | None:
    import winreg
    folders = []
    for root, sub in ((winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\LibreOffice\UNO\InstallPath"),
                      (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\LibreOffice\UNO\InstallPath"),
                      (winreg.HKEY_CURRENT_USER, r"SOFTWARE\LibreOffice\UNO\InstallPath")):
        try:
            with winreg.OpenKey(root, sub) as key:
                folders.append(str(winreg.QueryValueEx(key, "")[0]))
        except OSError:
            pass
    for env in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        if os.environ.get(env):
            folders.append(os.path.join(os.environ[env], "LibreOffice", "program"))
    for folder in folders:
        for exe in ("scalc.exe", "soffice.exe"):
            path = os.path.join(folder, exe)
            if os.path.isfile(path):
                return path
    return None


def open_spreadsheet(path: str, program: str | None = None):
    """Open a spreadsheet in the chosen program, or the system's default app for it (xdg-open on Linux). Away from
    Windows the program runs on by itself: closing the app (or the terminal it was started from) leaves it open.
    OSError when it can't be started."""
    if sys.platform == "win32":
        if program:
            subprocess.Popen([program, os.path.abspath(path)], close_fds=True)
        else:
            os.startfile(os.path.abspath(path))   # type: ignore[attr-defined]
        return
    launch(program or ("open" if sys.platform == "darwin" else "xdg-open"), os.path.abspath(path))


def launch(program: str, path: str):
    """Away from Windows: start a program on a file or folder, on its own (closing the app, or the terminal it was
    started from, leaves it open). A desktop without xdg-open (it comes with xdg-utils) is told so in plain words.
    OSError when it can't be started."""
    try:
        subprocess.Popen([program, path], close_fds=True, stdin=subprocess.DEVNULL, start_new_session=True)
    except FileNotFoundError:
        if program != "xdg-open":
            raise
        raise FileNotFoundError("xdg-open, which opens files in the desktop's own programs, isn't installed "
                                "(it comes with the xdg-utils package)") from None


def default_output_name(db_path: str) -> str:
    return f"{APP_NAME} Movies {dump_date(db_path)}.xlsx"


def default_output_path(db_path: str, out_dir: str | None = None) -> str:
    folder = out_dir if out_dir and os.path.isdir(out_dir) else os.path.dirname(os.path.abspath(db_path))
    return os.path.join(folder, default_output_name(db_path))
