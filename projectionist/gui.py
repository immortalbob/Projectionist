"""Desktop window for Projectionist (tkinter - ships with Python, nothing extra to install)."""

from __future__ import annotations

import gc
import importlib
import json
import math
import os
import queue
import shutil
import sys
import threading
import time
import traceback
import tkinter as tk
import weakref
from dataclasses import dataclass
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from . import APP_NAME, __version__, appicon, formats, jobs, needs, prefs
from . import catalog as _catalog  # noqa: F401  (defines the setting 'Also count as seen')
from . import letterboxd  # (defines Settings > Letterboxd, for the Export tab's 'Export for Letterboxd...')
from . import sheets as S
from .files import (default_output_name, default_output_path, dump_date, find_newest_database,
                    find_spreadsheet_apps, is_newer, newest_backup, open_spreadsheet, same_file)
from .ui import settings as settings_tab
from .ui import theme

# The tabs after Export, in order: modules in projectionist/ui that define `class Tab`. Settings stays last.
TAB_MODULES = ["overview", "film", "watchnext", "habits", "credits", "degrees", "doctor", "settings"]


def app_folder(frozen: bool | None = None, executable: str | None = None) -> str:
    """The app's own folder, where it looks for Plex backups when no other folder is known: Projectionist.pyw's
    (the project folder) - or, in the Windows build (tools/build_exe.py), Projectionist.exe's, not the folder of
    Python files packed inside it."""
    frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    if frozen:
        return os.path.dirname(os.path.abspath(executable or sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


APP_DIR = app_folder()


def settings_places(platform: str = sys.platform, environ=None) -> tuple[str, list[str]]:
    """(the folder the settings are kept in, [earlier settings files to carry across, first found first]):
      Windows   %APPDATA%\\Projectionist - earlier, %APPDATA%\\PlexMovieExporter (the app's old name)
      a Mac     ~/Library/Application Support/Projectionist
      Linux...  $XDG_CONFIG_HOME/projectionist, which is ~/.config/projectionist unless the desktop says otherwise
    Away from Windows the earlier versions kept them in the home folder itself: ~/Projectionist, and before that
    ~/PlexMovieExporter."""
    environ = os.environ if environ is None else environ
    if platform == "win32":
        appdata = environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(appdata, "Projectionist"), [os.path.join(appdata, "PlexMovieExporter", "settings.json")]
    import posixpath as path                       # (the system's own paths: these are POSIX ones, whatever runs it)
    home = environ.get("HOME") or os.path.expanduser("~")
    earlier = [path.join(home, "Projectionist", "settings.json"), path.join(home, "PlexMovieExporter", "settings.json")]
    if platform == "darwin":
        return path.join(home, "Library", "Application Support", "Projectionist"), earlier
    config = environ.get("XDG_CONFIG_HOME") or ""
    if not path.isabs(config):                     # (the standard says to ignore a relative one)
        config = path.join(home, ".config")
    return path.join(config, "projectionist"), earlier


SETTINGS_DIR, _EARLIER = settings_places()
SETTINGS_FILE = os.path.join(SETTINGS_DIR, "settings.json")
# Where the settings were kept while the app was called Plex Movie Exporter (and, away from Windows, by version
# 1.0.0 in the home folder - EARLIER_SETTINGS_FILES, tried first): read once by migrate_settings, never changed.
OLD_SETTINGS_FILE = _EARLIER[-1]
EARLIER_SETTINGS_FILES = _EARLIER[:-1]


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
def migrate_settings() -> bool:
    """Carry the settings saved under the app's old name (window size, libraries and sheets left out, languages...)
    - or, away from Windows, where version 1.0.0 kept them - across to SETTINGS_FILE, when there are none there yet.
    They're copied: the old folder is left as it was. Called as the window opens. -> whether they were copied.
    Never raises - settings are a convenience."""
    if os.path.exists(SETTINGS_FILE):
        return False
    old = next((f for f in EARLIER_SETTINGS_FILES + [OLD_SETTINGS_FILE] if os.path.isfile(f)), None)
    if old is None:
        return False
    tmp = SETTINGS_FILE + ".tmp"
    try:
        os.makedirs(SETTINGS_DIR, exist_ok=True)
        shutil.copyfile(old, tmp)
        os.replace(tmp, SETTINGS_FILE)            # all or nothing: never half a settings file
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False
    return True


def load_settings() -> dict:
    """The saved settings ({} when there are none). A file saved with a byte-order mark (Notepad, PowerShell) reads
    as well as one without. One that's there but can't be read is copied to settings.json.bad first, so the next
    save - which writes a fresh file - doesn't lose what was in it for good."""
    try:
        with open(SETTINGS_FILE, encoding="utf-8-sig") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, RecursionError):
        pass
    _keep_unreadable()
    return {}


def _keep_unreadable():
    """A copy of a settings file that couldn't be read (settings.json.bad), for the curious or the careful."""
    try:
        if os.path.isfile(SETTINGS_FILE):
            shutil.copyfile(SETTINGS_FILE, SETTINGS_FILE + ".bad")
    except OSError:
        pass


def save_settings(data: dict) -> bool:
    """Write the settings (all or nothing). -> whether that worked: settings are a convenience, so a failure is
    never raised - an export mustn't fail over them - but the window says so (App.save_settings)."""
    tmp = SETTINGS_FILE + ".tmp"
    try:
        os.makedirs(os.path.dirname(SETTINGS_FILE) or SETTINGS_DIR, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, SETTINGS_FILE)
        return True
    except (OSError, TypeError, ValueError):
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False


# The settings this window keeps (projectionist/prefs.py - the Settings tab lists the shown ones). The JSON keys are
# the ones the app has always saved under, so nothing saved before is lost.
def _spreadsheet_programs(app):
    return [(label, label) for label, _path in getattr(app, "apps", None) or []]


def _first_program(app):
    programs = _spreadsheet_programs(app)
    return programs[0][0] if programs else ""


START_TABS = [("export", "The Export tab"), ("last", "The tab you last had open")]


def _start_tabs(app):
    """Open on: the Export tab, the tab last open, or any other of the window's tabs (by name)."""
    others = [t.title for t in getattr(app, "tabs", None) or [] if t.title not in ("Export", "Settings")]
    return START_TABS + [(title, title) for title in others]


def _backups_folder_chosen(app, folder):
    """A folder for Plex backups was chosen: open its newest database now, as the app will each time it opens."""
    look = getattr(app, "look_in_backups_folder", None)
    if folder and callable(look):
        look(folder)


TEXT_SIZES = [(100, "Normal (100%)"), (115, "Larger (115%)"), (130, "Largest (130%)")]


def _text_size_chosen(app, _size):
    """Text size changed: the window shows it at once (App.apply_text_size)."""
    apply = getattr(app, "apply_text_size", None)
    if callable(apply):
        apply()


prefs.define("text_size", "Appearance", "Text size", kind="choice", default=100, choices=TEXT_SIZES,
             help="Makes the writing bigger everywhere in the window - the tables, charts and spacing grow with it.",
             apply=_text_size_chosen)
prefs.section("Starting up", order=10, hint="What the window does when it opens.")
prefs.define("start_tab", "Starting up", "Open on", kind="choice", default="export", choices=_start_tabs,
             control="start_tab", help="Which tab is in front when the app opens.", apply=prefs.NEXT_START)
prefs.define("backups_folder", "Starting up", "Look for Plex backups in", kind="folder", default="",
             control="backups_folder",
             help="Each time the app opens, it looks in this folder for the newest Plex database - choose the one "
                  "Plex saves its automatic backups in, and there's no copying them across first (choosing it opens "
                  "its newest one now). None chosen: the folder of the database used last, then the app's own "
                  "folder.", apply=_backups_folder_chosen)
START_DATABASES = [("newest", "The newest backup in that folder"), ("last", "The backup I used last")]
prefs.define("start_database", "Starting up", "When the app opens, use", kind="choice", default="newest",
             choices=START_DATABASES,
             help="The newest backup: it opens in place of the one you used last when it's newer, and the status "
                  "bar says so. The backup I used last: it opens again for as long as it's there, then the newest.",
             apply=prefs.NEXT_START)
prefs.define("window_geometry", "Starting up", "Window size", kind="text", default="", shown=False)
prefs.define("last_tab", "Starting up", "The tab last open", kind="text", default="", shown=False)
prefs.section("Export", order=20,
              hint="The Export tab's choices, kept from one run to the next (changing them there changes them here).")
prefs.define("open_when_done", "Export", "Open the spreadsheet when it's finished", kind="bool", default=True,
             help="As soon as an export is saved, open it in the program below.")
prefs.define("open_with", "Export", "Open spreadsheets in", kind="choice", default=_first_program,
             choices=_spreadsheet_programs,
             help="Exports, and the lists the other tabs save, open in this. LibreOffice Calc comes first when it's "
                  "installed, whatever Windows opens spreadsheet files with.")
prefs.define("csv", "Export", "Also save every sheet as a CSV file", kind="bool", default=False,
             help="A folder of CSV files beside the spreadsheet, one per sheet - for other programs.")
prefs.define("out_dir", "Export", "Save spreadsheets in", kind="folder", default="",
             help="Where exports are saved unless you pick somewhere else. None chosen: next to the Plex database.")
prefs.define("db_path", "Export", "The last database", kind="text", default="", shown=False)
prefs.define("excluded_libraries", "Export", "Libraries left out", kind="list", default=[], shown=False)
prefs.define("excluded_sheets", "Export", "Sheets left out", kind="list", default=[], shown=False)


# The Settings tab's controls for three settings (ui/settings.py builds the rest by their kind): Open on, the
# folder for Plex backups, and 'Also count as seen' (catalog.py's).
class StartTabControl(settings_tab.Control):
    """Open on: the Export tab, the tab you last had open - or any other tab, picked from a drop-down."""

    FIXED = [value for value, _label in START_TABS]

    def build(self):
        choices = self.pref.choices_for(self.tab.app) or START_TABS
        self.labels = dict(choices)
        self.others = [(value, label) for value, label in choices if value not in self.FIXED]
        self.var = tk.StringVar(self.frame)
        self.buttons = []
        for value, label in START_TABS:
            rb = ttk.Radiobutton(self.body, text=label, value=value, variable=self.var, style="Card.TRadiobutton",
                                 command=lambda v=value: self.save(v))
            rb.grid(row=0, column=len(self.buttons), sticky="w", padx=(0, 18))
            self.buttons.append(rb)
        self.box = None
        if self.others:
            column = len(self.buttons)
            rb = ttk.Radiobutton(self.body, text="This tab:", value="other", variable=self.var,
                                 style="Card.TRadiobutton", command=self._other)
            rb.grid(row=0, column=column, sticky="w")
            self.buttons.append(rb)
            self.other_var = tk.StringVar(self.frame, value=self.others[0][1])
            self.box = ttk.Combobox(self.body, state="readonly", width=16, textvariable=self.other_var,
                                    values=[label for _value, label in self.others])
            self.box.grid(row=0, column=column + 1, sticky="w", padx=(4, 0))
            self.box.bind("<<ComboboxSelected>>", lambda e: self._other(), add="+")

    def _other(self):
        """'This tab:' chosen, or a tab picked from the drop-down."""
        label = self.other_var.get()
        value = next((v for v, lab in self.others if lab == label), self.others[0][0])
        self.var.set("other")
        self.save(value)

    def show(self, value):
        if value in self.FIXED or not self.others:
            self.var.set(str(value))
        else:
            self.var.set("other")
            self.other_var.set(self.labels.get(value, str(value)))


class AccountsControl(settings_tab.Control):
    """A tick box for each of the server's other accounts that has played a film, most films first - made once the
    collection has been read, and again whenever another database is opened."""

    COLUMNS = 3

    def build(self):
        self.vars: dict[int, tk.BooleanVar] = {}
        self.made_for = False                    # (the collection the tick boxes were made for; False: none yet)
        self.ticks = ttk.Frame(self.body, style="CardInner.TFrame")
        self.ticks.grid(row=0, column=0, sticky="w")
        self.note = ttk.Label(self.body, text="", style="CardNote.TLabel", justify="left")
        self.note.grid(row=1, column=0, sticky="w")
        self.tab.wraps.append(self.note)
        self.untick = ttk.Button(self.body, text="Untick all", style="Small.TButton", command=lambda: self.save([]))
        self.untick.grid(row=2, column=0, sticky="w", pady=(6, 0))
        watch = getattr(self.tab.app, "watch_catalog", None)
        if callable(watch):
            watch(self._catalog_changed)

    def _catalog_changed(self, _catalog, _state):
        self.show(self.value())

    def _make_ticks(self, catalog):
        for child in self.ticks.winfo_children():
            child.destroy()
        self.vars = {}
        self.made_for = catalog
        if catalog is None:
            return
        for n, (account, films) in enumerate(catalog.account_plays().items()):
            var = tk.BooleanVar(self.frame)
            text = f"{catalog.account_name(account)}  ({films:,} film{'' if films == 1 else 's'})"
            ttk.Checkbutton(self.ticks, text=text, variable=var, style="Card.TCheckbutton",
                            command=lambda a=account: self._ticked(a)).grid(
                row=n // self.COLUMNS, column=n % self.COLUMNS, sticky="w", padx=(0, 22), pady=1)
            self.vars[account] = var

    def _ticked(self, account):
        ids = set(self.value())
        if self.vars[account].get():
            ids.add(account)
        else:
            ids.discard(account)
        self.save(sorted(ids))

    def show(self, value):
        catalog = getattr(self.tab.app, "catalog", None)
        if catalog is not self.made_for:
            self._make_ticks(catalog)
        ticked = set(value or [])
        for account, var in self.vars.items():
            var.set(account in ticked)
        elsewhere = len(ticked - set(self.vars))
        if catalog is None:
            note = "The accounts on your Plex server are listed here once the collection has been read."
        elif not self.vars:
            note = "No other account on this server has played a film."
        elif elsewhere:
            note = (f"{elsewhere} ticked account{' is' if elsewhere == 1 else 's are'} not in this database "
                    f"(still ticked, for a database that has {'it' if elsewhere == 1 else 'them'}).")
        else:
            note = ""
        self.note.configure(text=note)
        if note:
            self.note.grid()
        else:
            self.note.grid_remove()
        self.untick.state(["!disabled"] if ticked else ["disabled"])


class BackupsFolderControl(settings_tab.FolderControl):
    """The folder for Plex backups, with a button that opens the newest database in it now (say a backup was saved
    there since the app opened)."""

    def build(self):
        super().build()
        self.look_now = ttk.Button(self.body, text="Open the newest now", style="Small.TButton", command=self._look)
        self.look_now.grid(row=0, column=3, padx=(6, 0))

    def _look(self):
        look = getattr(self.tab.app, "look_in_backups_folder", None)
        if callable(look):
            look()

    def show(self, value):
        super().show(value)
        can = bool(value) and callable(getattr(self.tab.app, "look_in_backups_folder", None))
        self.look_now.state(["!disabled"] if can else ["disabled"])


settings_tab.add_control("start_tab", StartTabControl)
settings_tab.add_control("accounts", AccountsControl)
settings_tab.add_control("backups_folder", BackupsFolderControl)


def collection_size(catalog) -> str:
    """'1,204 films (1,318 library items)'. The tabs count a film once however many libraries or editions it's
    in; the Export tab counts library items (the spreadsheet's rows), so say both when they differ."""
    films = len(catalog.films)
    items = sum(len(getattr(f, "plex_ids", None) or ()) or 1 for f in catalog.films.values())
    text = f"{films:,} film" if films == 1 else f"{films:,} films"
    return text + (f" ({items:,} library items)" if items != films else "")


# ---------------------------------------------------------------------------
# Small widgets
# ---------------------------------------------------------------------------
class Tooltip:
    def __init__(self, widget, text: str):
        self.widget, self.text, self.tip = widget, text, None
        widget.bind("<Enter>", self.show, add="+")
        widget.bind("<Leave>", self.hide, add="+")

    def show(self, _event=None):
        if self.tip or not self.text:
            return
        x = self.widget.winfo_rootx() + 16
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f"+{x}+{y}")
        tk.Label(self.tip, text=self.text, background=theme.TOOLTIP_BG, foreground=theme.TOOLTIP_FG, relief="flat",
                 borderwidth=0, highlightthickness=1, highlightbackground=theme.TOOLTIP_BORDER,
                 highlightcolor=theme.TOOLTIP_BORDER, padx=6, pady=3, justify="left").pack()

    def hide(self, _event=None):
        if self.tip:
            self.tip.destroy()
            self.tip = None


@dataclass
class _Job:
    db: str
    out: str
    library_ids: list[int]
    sheets: list[str]
    csv: bool


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------
class App:
    PAD = 14
    _start_note: tuple[str, str] | None = None     # (database, what the status bar says about opening it at start)

    def __init__(self, root: tk.Tk, initial_db: str | None = None):
        # Whatever windows made before this one left for the collector (the tests make dozens) is freed now, on this
        # thread - left for later, a job's thread could be the one to free it, and a Tk object freed on the wrong
        # thread takes the whole program down (see run)
        gc.collect()
        self.root = root
        self.settings = load_settings()
        self.settings_saved = True             # (False while the settings file can't be written: see save_settings)
        self._syncing = False                  # (preference_changed is setting the Export tab's controls)
        formats.follow(self)                   # dates and times as Settings > Dates and times says, on every tab
        self.queue: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.cancel_event = threading.Event()
        self.closing = False
        self.libraries = []
        self.lib_vars: dict[int, tk.BooleanVar] = {}
        self.last_output: str | None = None
        self.last_csv: str | None = None
        self.loaded_path: str | None = None   # database whose libraries are showing
        # The collection behind the other tabs, read in the background whenever a database is loaded.
        self.catalog = None
        self.catalog_state = "none"           # 'none' | 'loading' | 'ready' | 'error'
        self.catalog_error = ""
        self._catalog_token = 0
        self._jobs: set[jobs.Job] = set()      # background jobs still going (see run)
        self._keyed: dict = {}                 # key -> the latest job run with it
        self._held: dict = {}                  # job -> what the window holds for it until its thread ends (see run)
        self._old_tab_jobs: set = set()        # jobs that may hold a tab a new text size replaced (_free_old_tabs)
        self._old_tabs_job = None              # ...and the collection queued for when they've ended
        self.tabs = []
        self._catalog_watchers: list = []      # see watch_catalog
        self._suggested_out = None             # the path the Export tab last suggested saving to (see _suggest_out)
        self._look_shown = False               # (apply_look has put the chosen look on this window)
        self._look_checked = 0.0               # when Follow Windows last asked Windows (see _activated)

        # Remember what was switched off (not on), so sheets added in later versions start ticked.
        unticked = set(prefs.get(self, "excluded_sheets"))
        self.db_var = tk.StringVar()
        self.out_var = tk.StringVar()
        self.db_info_var = tk.StringVar(value="")
        self.status_var = tk.StringVar(value="Choose a Plex database to begin.")
        self.sheet_vars = {n: tk.BooleanVar(value=n not in unticked) for n in S.DETAIL_SHEET_NAMES}
        # Which program opens the finished spreadsheet - LibreOffice Calc first when it's installed,
        # rather than whatever Windows associates .xlsx files with.
        self.apps = find_spreadsheet_apps()
        self.csv_var = tk.BooleanVar(value=prefs.get(self, "csv"))
        self.open_var = tk.BooleanVar(value=prefs.get(self, "open_when_done"))
        self.open_with_var = tk.StringVar(value=prefs.get(self, "open_with"))
        # (the Settings tab changes the same settings: each keeps the other up to date - see preference_changed)
        for key, var in (("csv", self.csv_var), ("open_when_done", self.open_var), ("open_with", self.open_with_var)):
            var.trace_add("write", lambda *_a, key=key, var=var: self._control_changed(key, var))

        root.title(f"{APP_NAME} {__version__}")
        s = theme.scale(root)
        self._set_min_size(s)
        root.geometry(self._initial_geometry(s))
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.report_callback_exception = self._report_exception
        self.app_status_var = tk.StringVar(value="")
        self._status_serial = 0                  # bumped by every set_status (see run)
        self._style()                            # the chosen look, before anything is built or shown
        self._build()
        self._open_start_tab()
        root.bind("<Activate>", self._activated, add="+")     # (Follow Windows: has Windows changed meanwhile?)
        self._away = False                       # (X11: the focus is in another app's window - see _focus_left)
        if root._windowingsystem == "x11":       # (no <Activate> there: the focus coming back is the same thing)
            root.bind("<FocusOut>", self._focus_left, add="+")
            root.bind("<FocusIn>", self._focus_came, add="+")
        root.bind("<Map>", self._mapped, add="+")
        self._timers = {"pick": root.after(50, lambda: self._pick_initial_db(initial_db)),
                        "poll": root.after(100, self._poll)}

    # -- layout -------------------------------------------------------------------
    def _set_min_size(self, s: float):
        """The smallest the window goes, in the display's (and the text size's) scale - never more than the screen
        has room for (a large text size on a small screen)."""
        try:
            sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        except tk.TclError:
            sw = sh = 10 ** 5
        self.root.minsize(min(int(900 * s), sw - 40), min(int(620 * s), sh - 80))

    def _initial_geometry(self, s: float) -> str:
        saved = prefs.get(self, "window_geometry")
        width, height = int(1180 * s), int(820 * s)
        try:
            sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        except tk.TclError:
            sw, sh = width, height
        if isinstance(saved, str) and saved.count("x") == 1:
            try:
                w, h = (int(v) for v in saved.split("+")[0].split("x"))
                width, height = w, h
            except ValueError:
                pass
        return f"{min(width, sw - 40)}x{min(height, sh - 80)}"

    def _style(self):
        self.apply_look(force=True)

    # -- the look and the settings --------------------------------------------------------------------------------
    def apply_look(self, force: bool = False) -> str:
        """Show the look the settings ask for (Follow Windows asks the system now), live: the styles, the classic
        widgets, the charts and the title bar change at once, then every tab's theme_changed(). -> the look."""
        look = theme.chosen_look(self)
        if look == theme.LOOK and self._look_shown and not force:
            return look
        theme.apply(self.root, look)
        self._look_shown = True
        for tab in self.tabs:
            try:
                tab.theme_changed()
            except Exception:
                self._log(traceback.format_exc().strip())
        return look

    def apply_text_size(self) -> float:
        """Settings > Appearance > Text size, at once: Tk's scaling (so every font given in points grows, and so does
        theme.scale(), the unit the pages lay themselves out in), every font measured again, the styles (the
        tables' row heights) and every chart's scale; then the window's own parts, and each tab - one that can't
        re-measure itself (BaseTab.text_size_changed) is built again in its place, over the collection already
        read, showing what it showed. As the window would be if it had opened at that size. -> the factor."""
        from .ui import widgets
        started = time.perf_counter()
        before = float(self.root.tk.call("tk", "scaling"))
        factor = apply_text_size(self.root, self)
        if abs(float(self.root.tk.call("tk", "scaling")) - before) < 1e-9:
            return factor                            # (the size it is already)
        s = theme.scale(self.root)
        theme.text_size_changed(self.root)
        widgets.rescale_charts(self.root)
        self._set_min_size(s)
        try:
            self._export_page.rowconfigure(self._log_row, minsize=int(self.LOG_LEAST * s))
        except (AttributeError, tk.TclError):
            pass
        self._fit_export_later()
        current = self.current_tab()
        rebuilt = []
        for index, tab in enumerate(list(self.tabs)):
            try:
                live = bool(tab.text_size_changed())
            except Exception:
                self._log(traceback.format_exc().strip())
                live = False
            if not live and self._rebuild_tab(index, tab, current=tab is current) is not None:
                rebuilt.append(tab.title)
        if rebuilt:
            self._free_old_tabs()
        widgets.redraw_all()
        self._log(f"Text size now {round(factor * 100)}% ({time.perf_counter() - started:.2f} s"
                  + (f"; tabs laid out again: {', '.join(rebuilt)}" if rebuilt else "") + ").")
        return factor

    def _rebuild_tab(self, index: int, old, current: bool = False):
        """Build a tab again in its place: the new one over the same collection, showing what the old one did
        (keep / restore), in front if the old one was; the old one's background jobs (keyed '<module>.<what>')
        and timers called off before it goes. -> the new tab (None if it couldn't be built: the old one stays)."""
        try:
            kept = old.keep() or {}
        except Exception:
            self._log(traceback.format_exc().strip())
            kept = {}
        prefix = type(old).__module__.rsplit(".", 1)[-1] + "."
        for key, job in list(self._keyed.items()):    # (before the new tab starts jobs of its own)
            if isinstance(key, str) and key.startswith(prefix):
                job.cancel()
        # (those, and older ones of the tab's still running, can hold the old tab: see _free_old_tabs)
        self._old_tab_jobs.update(job for job in self._held if isinstance(job.key, str) and job.key.startswith(prefix))
        try:
            new = type(old)(self, self.notebook)
        except Exception:
            self._log(f"The {old.title} tab couldn't be laid out again:\n{traceback.format_exc().strip()}")
            try:
                old.catalog_changed(self.catalog, self.catalog_state)    # (its jobs were called off: start afresh)
            except Exception:
                pass
            return None
        self.notebook.insert(self.notebook.index(old.frame), new.frame, text=new.title)
        self.tabs[index] = new
        for step in (lambda: new.catalog_changed(self.catalog, self.catalog_state),
                     lambda: new.restore(kept) if kept else None):
            try:
                step()
            except Exception:
                self._log(traceback.format_exc().strip())
        if current:
            self.notebook.select(new.frame)          # (then shown(), as for any change of tab)
        _cancel_timers_under(old.frame)
        self.notebook.forget(old.frame)
        old.frame.destroy()
        return new

    def _free_old_tabs(self):
        """The tabs a new text size built again are garbage in reference cycles (widgets, and callbacks that call
        the tab): the collector frees them here, on the UI thread, once the old tabs' jobs have ended (_poll) - a
        job can hold its tab till it does. Left to itself the collector could run on a job's thread, and a Tk
        object freed there can take the program down (see run)."""
        if self._old_tab_jobs or self._old_tabs_job is not None:
            return

        def collect():
            self._old_tabs_job = None
            gc.collect()
        try:
            self._old_tabs_job = self.root.after_idle(collect)
        except tk.TclError:                          # (the window is closing)
            pass

    def _activated(self, event=None):
        """The window came to the front: with Follow Windows (Follow the system, elsewhere), show the look the
        system now asks for (at most once a second - the event comes once for every widget in the window)."""
        if prefs.get(self, "look") != theme.FOLLOW_WINDOWS:
            return
        now = time.monotonic()
        if now - self._look_checked < 1.0:
            return
        self._look_checked = now
        self.apply_look()

    # X11 (Linux) sends no <Activate> when the window comes to the front, only the focus moving: so a focus that
    # left for another app's window and comes back is taken as the window coming to the front
    def _focus_elsewhere(self) -> bool:
        """No window of the app has the focus: it's in another app's window (or on the desktop)."""
        try:
            return not self.root.tk.call("focus")
        except tk.TclError:
            return False

    def _focus_left(self, event=None):
        def check():                             # (once Tk has moved the focus: to another widget here, or away)
            if not self.closing:
                self._away = self._focus_elsewhere()
        try:
            self.root.after_idle(check)
        except tk.TclError:
            pass

    def _focus_came(self, event=None):
        if self._away:
            self._away = False
            self._activated()

    def _mapped(self, event=None):
        if event is None or event.widget is self.root:          # (the window itself, not a widget in it)
            theme.title_bar(self.root)

    def save_settings(self) -> bool:
        """Save the settings now. -> whether they could be written; the first time they can't, the log says why
        (the Settings tab says so under the setting changed)."""
        ok = save_settings(self.settings) is not False
        if not ok and self.settings_saved:
            try:
                self._log(f"The settings couldn't be saved in {SETTINGS_FILE} - changes last until the app closes.")
            except (AttributeError, tk.TclError):            # (before the log is built, or after it's gone)
                pass
        self.settings_saved = ok
        return ok

    def preference_changed(self, key: str, value):
        """A setting changed (prefs.set - the Settings tab, or a tab): the Export tab's own controls follow, then
        every tab hears of it (BaseTab.preference_changed)."""
        var = {"csv": self.csv_var, "open_when_done": self.open_var, "open_with": self.open_with_var}.get(key)
        if var is not None:
            self._syncing = True               # (not a change made there: a reset's default isn't saved back)
            try:
                if var.get() != value:
                    var.set(value)
            except tk.TclError:
                pass
            finally:
                self._syncing = False
        if key == "out_dir" and self.loaded_path:
            shown = self.out_var.get().strip()
            if not shown or shown == self._suggested_out:      # (a path of your own in the box is left alone)
                self._suggest_out(self.loaded_path)
        if key.startswith("letterboxd_") and getattr(self, "letterboxd_var", None) is not None:
            self._letterboxd_says(letterboxd.hint(self))      # (Settings > Letterboxd changed what goes in)
        for tab in self.tabs:
            try:
                tab.preference_changed(key, value)
            except Exception:
                self._log(traceback.format_exc().strip())

    def _control_changed(self, key: str, var):
        """One of the Export tab's own settings was changed there: keep it (and tell the Settings tab)."""
        if self._syncing:
            return
        try:
            prefs.set(self, key, var.get())
        except (ValueError, tk.TclError):
            pass

    def _open_start_tab(self):
        """Settings > Starting up > Open on: the Export tab (as the notebook opens anyway), the tab you last had open,
        or a tab of your choosing. A tab that's gone (renamed, or it couldn't be loaded) leaves Export in front.
        A tab shown before the collection is read shows its 'Reading your collection' page, and hears shown()
        again once it's ready (_set_catalog)."""
        choice = prefs.get(self, "start_tab")
        if choice == "export":
            return
        title = prefs.get(self, "last_tab") if choice == "last" else choice
        tab = next((t for t in self.tabs if t.title == title), None)
        if tab is not None:
            self.notebook.select(tab.frame)

    def _build(self):
        status = ttk.Frame(self.root, padding=(10, 3), style="Chrome.TFrame")
        status.pack(side="bottom", fill="x")
        ttk.Label(status, textvariable=self.app_status_var, style="ChromeHint.TLabel").pack(side="left")
        ttk.Separator(self.root, orient="horizontal").pack(side="bottom", fill="x")
        self.search_bar, search_error = self._build_search_bar()
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True)
        self.notebook.enable_traversal()        # Ctrl+Tab / Ctrl+Shift+Tab switch tabs wherever the focus is
        export = ttk.Frame(self.notebook, padding=self.PAD)
        self.notebook.add(export, text="Export")
        self._build_export(export)
        if search_error:
            self._log(f"The search box couldn't be loaded:\n{search_error}")
        try:
            from .ui import widgets
            widgets.report_error = self._log     # a chart that can't be drawn says why in the log below
        except Exception:                        # (a broken tab module is reported just below)
            pass
        for name in TAB_MODULES:
            try:
                module = importlib.import_module(f".ui.{name}", __package__)
                tab = module.Tab(self, self.notebook)
            except Exception:                  # one broken tab mustn't stop the app from opening
                self._log(f"The {name} tab couldn't be loaded:\n{traceback.format_exc().strip()}")
                continue
            self.notebook.add(tab.frame, text=tab.title)
            self.tabs.append(tab)
        self.notebook.bind("<<NotebookTabChanged>>", self._tab_changed, add="+")

    def _build_search_bar(self):
        """The search box above the tabs (ui/film.py's GlobalSearch); Ctrl+F or Ctrl+K goes to it from anywhere in
        the window. -> (the bar or None, the error that stopped it). A broken bar mustn't stop the app opening."""
        try:
            from .ui.film import GlobalSearch
            bar = GlobalSearch(self.root, self)
            bar.pack(side="top", fill="x")
        except Exception:
            return None, traceback.format_exc().strip()
        for sequence in ("<Control-f>", "<Control-F>", "<Control-k>", "<Control-K>"):
            self.root.bind(sequence, bar.shortcut, add="+")      # (this window's own keys: not bind_all)
        # Tk's text boxes take Ctrl+K for themselves - 'delete from the cursor to the end' - before the window
        # sees it: in this window's boxes it goes to the search box instead, and deletes nothing
        for cls in ("TEntry", "TCombobox", "Entry", "Spinbox", "TSpinbox"):
            for sequence in ("<Control-k>", "<Control-K>"):
                self.root.bind_class(cls, sequence, bar.shortcut)
        return bar, None

    def _build_export(self, outer):
        P = self.PAD
        outer.columnconfigure(0, weight=1)
        row = 0

        self._export_page = outer
        self._fit_job = None
        ttk.Label(outer, text=APP_NAME, style="Title.TLabel").grid(row=row, column=0, sticky="w")
        row += 1
        self._subtitle = ttk.Label(outer, style="Hint.TLabel",
                                   text="Turns a Plex library database (or one of Plex's automatic backups) into one "
                                        "big spreadsheet of your movies - and the other tabs explore the same "
                                        "collection.")
        self._subtitle.grid(row=row, column=0, sticky="w", pady=(0, 8))
        row += 1

        # 1. Database
        ttk.Label(outer, text="1   Plex database", style="Section.TLabel").grid(row=row, column=0, sticky="w")
        row += 1
        f = ttk.Frame(outer)
        f.grid(row=row, column=0, sticky="ew", pady=(2, 0))
        f.columnconfigure(0, weight=1)
        self.db_entry = ttk.Entry(f, textvariable=self.db_var)
        self.db_entry.grid(row=0, column=0, sticky="ew")
        self.db_entry.bind("<Return>", lambda e: self.load_db(self.db_var.get()))
        self.db_entry.bind("<FocusOut>", self._db_entry_left)
        self.db_browse = ttk.Button(f, text="Browse...", command=self.browse_db)
        self.db_browse.grid(row=0, column=1, padx=(6, 0))
        row += 1
        self.db_info = ttk.Label(outer, textvariable=self.db_info_var, style="Hint.TLabel")
        self.db_info.grid(row=row, column=0, sticky="w", pady=(2, 10))
        row += 1

        # 2. Libraries
        head = ttk.Frame(outer)
        head.grid(row=row, column=0, sticky="ew")
        head.columnconfigure(0, weight=1)
        ttk.Label(head, text="2   Movie libraries to include", style="Section.TLabel").grid(row=0, column=0,
                                                                                            sticky="w")
        ttk.Button(head, text="All", style="Small.TButton", width=5,
                   command=lambda: self._set_all(self.lib_vars.values(), True)).grid(row=0, column=1)
        ttk.Button(head, text="None", style="Small.TButton", width=5,
                   command=lambda: self._set_all(self.lib_vars.values(), False)).grid(row=0, column=2, padx=(4, 0))
        row += 1
        self.lib_frame = ttk.Frame(outer)
        self.lib_frame.grid(row=row, column=0, sticky="ew", pady=(2, 10))
        ttk.Label(self.lib_frame, text="(choose a database first)", style="Hint.TLabel").grid(row=0, column=0)
        row += 1

        # 3. Sheets
        head = ttk.Frame(outer)
        head.grid(row=row, column=0, sticky="ew")
        head.columnconfigure(0, weight=1)
        ttk.Label(head, text="3   Extra sheets  (the Movies sheet - one row per movie - is always included)",
                  style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Button(head, text="All", style="Small.TButton", width=5,
                   command=lambda: self._set_all(self.sheet_vars.values(), True)).grid(row=0, column=1)
        ttk.Button(head, text="None", style="Small.TButton", width=5,
                   command=lambda: self._set_all(self.sheet_vars.values(), False)).grid(row=0, column=2,
                                                                                         padx=(4, 0))
        row += 1
        sf = ttk.Frame(outer)
        sf.grid(row=row, column=0, sticky="ew", pady=(2, 10))
        self._sheet_items = []                   # (checkbox, its description)
        for name, desc in S.DETAIL_SHEETS:
            cb = ttk.Checkbutton(sf, text=name, variable=self.sheet_vars[name])
            self._sheet_items.append((cb, ttk.Label(sf, text=desc, style="Hint.TLabel")))
            Tooltip(cb, desc)
        self._sheets_compact = None
        self._lay_out_sheets(False)
        row += 1

        # 4. Output
        ttk.Label(outer, text="4   Save spreadsheet as", style="Section.TLabel").grid(row=row, column=0, sticky="w")
        row += 1
        f = ttk.Frame(outer)
        f.grid(row=row, column=0, sticky="ew", pady=(2, 0))
        f.columnconfigure(0, weight=1)
        ttk.Entry(f, textvariable=self.out_var).grid(row=0, column=0, sticky="ew")
        self.out_browse = ttk.Button(f, text="Browse...", command=self.browse_out)
        self.out_browse.grid(row=0, column=1, padx=(6, 0))
        row += 1
        f = ttk.Frame(outer)
        f.grid(row=row, column=0, sticky="w", pady=(4, 12))
        ttk.Checkbutton(f, text="Also save every sheet as a CSV file", variable=self.csv_var).grid(
            row=0, column=0, sticky="w", padx=(0, 18))
        ttk.Checkbutton(f, text="Open the spreadsheet when finished, in", variable=self.open_var).grid(
            row=0, column=1, sticky="w")
        self.open_with_box = ttk.Combobox(f, textvariable=self.open_with_var, state="readonly", width=20,
                                          values=[label for label, _ in self.apps])
        self.open_with_box.grid(row=0, column=2, sticky="w", padx=(4, 0))
        row += 1

        # Actions, with the progress bar and progress line between the buttons - so in a small window it's only
        # the log below that gives way
        f = ttk.Frame(outer)
        f.grid(row=row, column=0, sticky="ew")
        f.columnconfigure(2, weight=1)
        self.export_btn = ttk.Button(f, text="Export spreadsheet", style="Accent.TButton", command=self.start_export)
        self.export_btn.grid(row=0, column=0)
        self.cancel_btn = ttk.Button(f, text="Cancel", command=self.cancel_export, state="disabled")
        self.cancel_btn.grid(row=0, column=1, padx=(8, 0))
        middle = ttk.Frame(f)
        middle.grid(row=0, column=2, sticky="ew", padx=12)
        middle.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(middle, mode="determinate", maximum=100)
        self.progress.grid(row=0, column=0, sticky="ew")
        self.status_label = ttk.Label(middle, textvariable=self.status_var, style="Hint.TLabel")
        self.status_label.grid(row=1, column=0, sticky="w")
        # a long line wraps rather than being cut off between the buttons in a narrow window
        middle.bind("<Configure>", lambda e: self.status_label.configure(wraplength=max(e.width, 120)), add="+")
        self.open_btn = ttk.Button(f, text="Open spreadsheet", command=self.open_output, state="disabled")
        self.open_btn.grid(row=0, column=3)
        self.folder_btn = ttk.Button(f, text="Open folder", command=self.open_folder, state="disabled")
        self.folder_btn.grid(row=0, column=4, padx=(6, 0))
        row += 1
        # Letterboxd: your plays and ratings as a file its importer reads (letterboxd.py; Settings > Letterboxd)
        f = ttk.Frame(outer)
        f.grid(row=row, column=0, sticky="ew", pady=(8, 0))
        f.columnconfigure(1, weight=1)
        self.letterboxd_btn = ttk.Button(f, text="Export for Letterboxd...", command=self.export_letterboxd)
        self.letterboxd_btn.grid(row=0, column=0, sticky="w")
        self.letterboxd_var = tk.StringVar(value=letterboxd.hint(self))
        self.letterboxd_label = ttk.Label(f, textvariable=self.letterboxd_var, style="Hint.TLabel")
        self.letterboxd_label.grid(row=0, column=1, sticky="w", padx=(12, 0))
        f.bind("<Configure>", lambda e: self.letterboxd_label.configure(
            wraplength=max(e.width - self.letterboxd_btn.winfo_width() - 24, 120)), add="+")
        row += 1
        self.log = ScrolledText(outer, height=6, wrap="word", state="disabled",
                                font=theme.font(outer, 9, family=theme.MONO), relief="flat", borderwidth=1)
        theme.tint(self.log, background="LOG_BG", foreground="LOG_FG", insertbackground="INSERT",
                   selectbackground="ENTRY_SELECT_BG", selectforeground="ENTRY_SELECT_FG")
        # ScrolledText's own scroll bar is a classic one, which no look can colour: a ttk one, like the rest
        self.log.vbar.destroy()
        self.log.vbar = ttk.Scrollbar(self.log.frame, orient="vertical", command=self.log.yview)
        self.log.vbar.pack(side="right", fill="y", before=self.log._w)      # (str(ScrolledText) is its frame)
        self.log.configure(yscrollcommand=self.log.vbar.set)
        theme.report = self._log                 # a look a part of the window couldn't take says why here
        self.log.grid(row=row, column=0, sticky="nsew", pady=(10, 0))
        self._log_row = row                      # (apply_text_size sizes its minimum again)
        outer.rowconfigure(row, weight=1, minsize=int(self.LOG_LEAST * theme.scale(outer)))
        outer.bind("<Configure>", lambda e: e.widget is outer and self._fit_export_later(), add="+")

    # The log is where every tab sends the details of anything that goes wrong: in a short window the sheet list
    # gives up its descriptions (they're in its tooltips too) and the page its subtitle, so the log keeps a few lines
    # and the Export button stays whole.
    LOG_LEAST = 56                               # device-independent px of log kept visible
    COMPACT_SHEET_COLUMNS = 5

    def _lay_out_sheets(self, compact: bool):
        """The extra sheets beside their descriptions in two columns, or (compact) just their names, in more columns."""
        if compact == self._sheets_compact:
            return
        self._sheets_compact = compact
        per_col = math.ceil(len(self._sheet_items) / (self.COMPACT_SHEET_COLUMNS if compact else 2))
        for i, (cb, label) in enumerate(self._sheet_items):
            if compact:
                label.grid_remove()
                cb.grid(row=i % per_col, column=i // per_col, sticky="w", padx=(0, 18))
            else:
                cb.grid(row=i % per_col, column=(i // per_col) * 2, sticky="w", padx=(0, 6))
                label.grid(row=i % per_col, column=(i // per_col) * 2 + 1, sticky="w", padx=(0, 18))
        if compact:
            self._subtitle.grid_remove()
        else:
            self._subtitle.grid()

    def _fit_export_later(self):
        if self._fit_job is None:
            self._fit_job = self.root.after_idle(self._fit_export)

    def _fit_export(self):
        """The compact sheet list when the page is too short for the full one and a few lines of log."""
        try:
            outer = self._export_page
            outer.update_idletasks()                 # (the page's requested height, after any change to it)
            height = outer.winfo_height()
            if height < 50:
                return
            need = (outer.winfo_reqheight() - self.log.frame.winfo_reqheight()
                    + int(self.LOG_LEAST * theme.scale(outer)))
            if self._sheets_compact:                 # (what the full list and the subtitle would add)
                n = len(self._sheet_items)
                rows = math.ceil(n / 2) - math.ceil(n / self.COMPACT_SHEET_COLUMNS)
                row_h = max(max(cb.winfo_reqheight(), label.winfo_reqheight()) for cb, label in self._sheet_items)
                need += rows * row_h + self._subtitle.winfo_reqheight() + 8
            self._lay_out_sheets(height < need)
        except (tk.TclError, AttributeError):
            pass
        finally:
            self._fit_job = None

    # -- helpers --------------------------------------------------------------------
    @staticmethod
    def _set_all(variables, value: bool):
        for v in variables:
            v.set(value)

    def _log(self, text: str):
        self.log.configure(state="normal")
        self.log.insert("end", f"{time.strftime('%H:%M:%S')}  {text}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _set_db_info(self, text: str, style: str = "Hint.TLabel"):
        self.db_info_var.set(text)
        self.db_info.configure(style=style)

    def _remember(self, **values):
        self.settings.update(values)
        return self.save_settings()

    def _report_exception(self, exc, value, tb):
        details = "".join(traceback.format_exception(exc, value, tb))
        self._log(details.strip())
        messagebox.showerror(APP_NAME, f"Something went wrong:\n\n{value}")

    # -- services for the tabs (see ui/base.py) ------------------------------------------------------------
    def ask(self, request: dict) -> dict:
        """Answer a request (projectionist.ask) against the loaded collection - quick requests, on the UI thread."""
        from .ask import handle
        if self.catalog is None:
            return {"ok": False, "error": "The collection isn't loaded yet."}
        return handle(request, catalog=self.catalog)

    def run(self, work, done=None, failed=None, status: str | None = None, key=None) -> jobs.Job:
        """work() on a background thread; done(result) or failed(message) back on the UI thread.

        `status` shows in the status line while the work runs, and is cleared when it ends - unless something
        else has been shown there since (done() saying what it found, say, or a newer job's status).

        Returns the job (projectionist.jobs.Job): job.cancel() calls it off. Jobs are also called off for you when
        their answer can't be wanted any more:
          - a newer run() with the same `key` (any hashable - name it after the tab and the request, e.g.
            "watchnext.search") calls off the older job with that key; key=None jobs are never replaced,
          - loading another database calls off every job, and so does closing the window,
          - a tab built again in its place (a new text size: _rebuild_tab) has its jobs called off - the ones
            keyed '<its module>.<what>', as 'watchnext.search' is.
        A job that's called off stops at its next check point - jobs.check(), which the long loops in recommend,
        costars, insights and catalog call every few milliseconds - with nothing shown to the user: done() and
        failed() are never called, and its status comes off the status line. (One that finishes before it gets
        to a check point has its answer dropped all the same.) So a tab needn't keep counters to drop stale
        answers, and superseded work stops using the processor. Work of a tab's own with a long loop can call
        jobs.check() in it too; outside a job check() does nothing.

            def search(self, request):
                catalog = self.catalog
                self.app.run(lambda: handle(request, catalog=catalog), self._show_answer, self._failed,
                             status="Finding films...", key="watchnext.search")   # replaces a search still going
        """
        if key is not None:
            older = self._keyed.get(key)
            if older is not None:
                older.cancel()                   # (takes its status off before this one puts its own up)
        job = jobs.Job(key)
        self._jobs.add(job)
        if key is not None:
            self._keyed[key] = job
        mark = None
        if status:
            self.set_status(status)
            mark = self._status_serial
        # The job's thread only borrows work(): everything of the window's it needs - work, done and failed, and
        # whatever they keep alive (a tab, its Tk variables) - is held here, on the UI thread, until the thread says
        # it has finished, and let go of here too. Tk objects freed on another thread can take the whole program
        # down ("Tcl_AsyncDelete: async handler deleted by the wrong thread"), which is what would happen if a job
        # outlived the tab or window that started it and its thread let go of them last.
        app = weakref.ref(self)                  # (nor does the job keep the window alive)

        def called_off():                        # on the UI thread, when job.cancel() is called
            me = app()
            if me is not None:
                me._job_called_off(job)
        job._on_cancel = called_off
        borrowed, post = [work], self.queue.put

        def target():
            outcome = None
            with jobs.running(job):
                try:
                    if not job.cancelled:        # (called off before it started: nothing to do)
                        outcome = ("done", borrowed.pop()())
                except jobs.Cancelled:
                    pass                         # called off: nothing to say, nothing to call
                except Exception as exc:
                    if not job.cancelled:        # (an error after it was called off is no one's concern now)
                        post(("log", traceback.format_exc().strip()))
                        outcome = ("failed", f"{type(exc).__name__}: {exc}")
                finally:
                    borrowed.clear()
            post(("job", job, outcome))          # always, so the window lets go of what it holds for the job
        thread = threading.Thread(target=target, daemon=True, name=f"projectionist job {key or ''}".rstrip())
        self._held[job] = (work, done, failed, mark, thread)
        thread.start()
        return job

    def _let_go(self, job):
        self._jobs.discard(job)
        if job.key is not None and self._keyed.get(job.key) is job:
            del self._keyed[job.key]

    def _clear_job_status(self, mark):
        if mark is not None and self._status_serial == mark:
            try:
                self.set_status("")
            except tk.TclError:                  # (the window is already gone)
                pass

    def _job_called_off(self, job):
        """job.cancel() (on the UI thread): it's off the list and its status off the status line at once. What the
        window holds for it is let go of when its thread says it has finished (_job_ended)."""
        self._let_go(job)
        held = self._held.get(job)
        if held is not None:
            self._clear_job_status(held[3])

    def _job_ended(self, job, outcome):
        """A job's thread has finished (from _poll, on the UI thread): hand its answer to done() or failed() -
        unless it was called off meanwhile, or the window is closing - and let go of its callbacks."""
        held = self._held.pop(job, None)
        if held is None or job.cancelled or outcome is None:
            return
        _work, done, failed, mark, _thread = held
        job.ended, job._on_cancel = True, None
        self._let_go(job)                        # (before the callback, which may start the next job with this key)
        try:
            if self.closing:
                return
            which, value = outcome
            callback = done if which == "done" else (failed or self._task_failed)
            if callback is not None:
                callback(value)
        finally:
            self._clear_job_status(mark)

    def _wait_for_jobs(self, seconds: float = 3.0):
        """After the jobs are called off (the window is closing): wait a moment for their threads to stop - each
        stops at its next check point - so none is left running, holding the window's things, once it's gone."""
        deadline = time.perf_counter() + seconds
        for job, held in list(self._held.items()):
            thread = held[4]
            if thread.is_alive():
                thread.join(max(deadline - time.perf_counter(), 0))
            if not thread.is_alive():
                self._held.pop(job, None)        # (let go of here, on the UI thread)

    def cancel_jobs(self):
        """Call off every background job (another database is being loaded, or the window is closing)."""
        for job in list(self._jobs):
            job.cancel()
        self._jobs.clear()
        self._keyed.clear()

    def _task_failed(self, message: str):
        self.set_status(f"Something went wrong: {message}")

    def set_status(self, text: str):
        self._status_serial += 1
        self.app_status_var.set(text)

    def watch_catalog(self, fn):
        """fn(catalog, state) each time the collection is (re)loaded, as a tab's catalog_changed - for the parts of
        the window that aren't tabs (the Settings tab's list of the server's accounts). A bound method is held
        weakly: it goes with its object. -> fn"""
        if hasattr(fn, "__self__") and hasattr(fn, "__func__"):
            self._catalog_watchers.append(weakref.WeakMethod(fn))
        else:
            self._catalog_watchers.append(lambda: fn)
        return fn

    def open_spreadsheet(self, path: str):
        """Open a spreadsheet or CSV file - an export, or a list a tab saved - in the program chosen for spreadsheets
        (Settings > Export, or beside the Export tab's tick box), rather than whatever Windows opens the file type
        with. OSError when it can't be opened: the caller says so (and where the file is)."""
        open_spreadsheet(path, dict(self.apps).get(prefs.get(self, "open_with")))

    def goto(self, tab_title: str, /, **kwargs):
        """Bring a tab to the front and pass it kwargs, e.g. goto('Six Degrees', person_id=...) or
        goto('Credits', title='Alien (1979)') - the tab's name is positional-only so 'title' can be an argument."""
        for tab in self.tabs:
            if tab.title == tab_title:
                self.notebook.select(tab.frame)
                try:
                    tab.navigate(**kwargs)
                except Exception:
                    self._log(traceback.format_exc().strip())
                return tab
        return None

    def current_tab(self):
        try:
            selected = self.notebook.select()
        except tk.TclError:
            return None
        return next((t for t in self.tabs if str(t.frame) == selected), None)

    def _tab_changed(self, _event=None):
        tab = self.current_tab()
        if tab is not None:
            try:
                tab.shown()
            except Exception:
                self._log(traceback.format_exc().strip())

    def _load_catalog(self, path: str):
        """Read the collection behind the other tabs (a few seconds) without blocking the window."""
        from .catalog import load
        self.cancel_jobs()                               # nothing worked out from the old collection is wanted now
        self._catalog_token += 1
        token = self._catalog_token
        started = time.perf_counter()
        self._set_catalog(None, "loading")

        def done(catalog):
            if token != self._catalog_token:
                return                                   # a newer database was picked meanwhile
            self._set_catalog(catalog, "ready")
            note, self._start_note = self._start_note, None       # ('Opened the newest backup...', at start)
            self.set_status((f"{note[1]}   " if note and note[0] == path else "") +
                            f"{os.path.basename(path)}  -  {collection_size(catalog)}, "
                            f"{len(catalog.people):,} people  (read in {time.perf_counter() - started:.1f} s)")

        def failed(message):
            if token != self._catalog_token:
                return
            self.catalog_error = message
            self._set_catalog(None, "error")
            self.set_status(f"Couldn't read the collection: {message}")

        self.run(lambda: load(path), done, failed, status="Reading your collection for the other tabs...",
                 key="collection")

    def _set_catalog(self, catalog, state: str):
        self.catalog, self.catalog_state = catalog, state
        for tab in self.tabs:
            try:
                tab.catalog_changed(catalog, state)
            except Exception:
                self._log(traceback.format_exc().strip())
        if getattr(self, "search_bar", None) is not None:
            try:
                self.search_bar.catalog_changed(catalog, state)     # (builds its index in the background)
            except Exception:
                self._log(traceback.format_exc().strip())
        for ref in list(self._catalog_watchers):
            fn = ref()
            if fn is None:                               # (its object is gone)
                self._catalog_watchers.remove(ref)
                continue
            try:
                fn(catalog, state)
            except Exception:
                self._log(traceback.format_exc().strip())
        if state == "ready":
            self._tab_changed()

    # -- choosing the database ---------------------------------------------------------
    def _pick_initial_db(self, initial_db):
        """The database the window opens with: the one on the command line; else, as Settings > Starting up says,
        the newest Plex backup in the folder for backups (none chosen: in the last database's folder, then in the
        app's own) when it's newer than the one used last - the status bar says so - or the one used last."""
        if initial_db:
            self.load_db(initial_db)
            return
        backups = prefs.get(self, "backups_folder")
        if backups and not os.path.isdir(backups):
            self._log(f"Couldn't reach the folder for Plex backups ({backups}) - looked in the usual places instead.")
        saved = prefs.get(self, "db_path")
        last = saved if saved and os.path.isfile(saved) else ""
        tried = False                            # (a database was tried: its line says why it didn't open)
        self.root.configure(cursor="watch")   # checking a zip dump means unpacking it
        self.root.update_idletasks()
        try:
            if last and prefs.get(self, "start_database") == "last":
                self.load_db(last)
                if self.loaded_path:
                    return
                tried = True
            newest, folder = newest_backup([backups, os.path.dirname(saved) if saved else "", APP_DIR])
            if last and not tried and not (newest and is_newer(newest, last)):
                self.load_db(last)                  # (nothing newer than the one used last)
                if self.loaded_path:
                    return
                tried = True
            if newest and not (tried and same_file(newest, last)):
                self.load_db(newest)
                if self.loaded_path:
                    if last and is_newer(newest, last):
                        self._opened_newest(newest, last, folder)
                    elif saved and not same_file(newest, saved):
                        self._log(f"Picked the newest database in {folder}.")
                    return
                tried = True
            if not tried:
                self._set_db_info("Click Browse... and pick com.plexapp.plugins.library.db (or a backup of it).")
        finally:
            self.root.configure(cursor="")

    def _opened_newest(self, newest: str, last: str, folder: str):
        """The newest backup opened at start instead of the one used last: say so in the status bar (it stays there,
        before what the collection holds, once that's read) and the log."""
        new, old = dump_date(newest), dump_date(last)
        note = (f"Opened the newest backup, {new} - you last used {old}." if new != old else
                f"Opened the newest backup, {new} - saved later than the one you last used.")
        self._start_note = (newest, note)
        self.set_status(note)
        self._log(f"{note} ({os.path.basename(newest)}, in {folder})")

    def look_in_backups_folder(self, folder: str | None = None) -> str | None:
        """Open the newest Plex database in the folder for backups (Settings > Starting up; or `folder`) now, as
        the window does each time it opens - unless it's the one already open, or an export is running. The status
        line says what happened. -> the database opened, or None."""
        folder = prefs.get(self, "backups_folder") if folder is None else folder
        if not folder:
            return None
        if not os.path.isdir(folder):
            self.set_status(f"Couldn't find the folder {folder}.")
            return None
        if self.worker and self.worker.is_alive():
            self.set_status("An export is running - the newest database in that folder opens the next time the app "
                            "starts.")
            return None
        self.root.configure(cursor="watch")      # (checking a zip dump means unpacking it)
        self.root.update_idletasks()
        try:
            newest = find_newest_database(folder)
        finally:
            self.root.configure(cursor="")
        if not newest:
            self.set_status(f"No Plex database in {folder} yet - the app looks there each time it opens.")
            return None
        if os.path.normcase(os.path.abspath(newest)) == os.path.normcase(os.path.abspath(self.loaded_path or "")):
            self.set_status(f"{os.path.basename(newest)}, the newest Plex database in that folder, is open already.")
            return None
        self._log(f"Opening the newest database in {folder}: {os.path.basename(newest)}")
        self.load_db(newest)
        return newest if self.loaded_path == newest else None

    def browse_db(self):
        current = self.db_var.get().strip()
        start = next((f for f in (prefs.get(self, "backups_folder"), os.path.dirname(current) if current else "",
                                  os.path.dirname(prefs.get(self, "db_path"))) if f and os.path.isdir(f)), APP_DIR)
        path = filedialog.askopenfilename(
            parent=self.root, title="Choose a Plex library database",
            initialdir=start,
            filetypes=[("Plex library database", "com.plexapp.plugins.library.db*"),
                       ("Plex 'Download database' zip", "*.zip"),
                       ("SQLite database", "*.db *.sqlite *.sqlite3"), ("All files", "*.*")])
        if path:
            self.load_db(os.path.normpath(path))

    def _db_entry_left(self, _event):
        # Only follow a typed path once it points at a real file - a half-typed one shouldn't wipe the
        # library list. (Enter always loads, and reports errors.)
        typed = self.db_var.get().strip().strip('"')
        if typed and os.path.isfile(typed) and os.path.normcase(typed) != os.path.normcase(self.loaded_path or ""):
            self.load_db(typed)

    def load_db(self, path: str):
        from .extract import PlexDBError, list_movie_libraries

        if self.worker and self.worker.is_alive():   # e.g. Enter pressed in the path box mid-export
            return
        path = path.strip().strip('"')
        self.db_var.set(path)
        for child in self.lib_frame.winfo_children():
            child.destroy()
        self.lib_vars.clear()
        self.libraries = []
        self.loaded_path = None
        self.status_var.set("Choose a Plex database to begin.")
        if not path:
            return
        self.root.configure(cursor="watch")
        self.root.update_idletasks()
        try:
            libraries = list_movie_libraries(path)
        except PlexDBError as exc:
            self._set_db_info(str(exc).replace("\n\n", " "), "Bad.TLabel")
            ttk.Label(self.lib_frame, text="(no libraries)", style="Hint.TLabel").grid(row=0, column=0)
            return
        except Exception as exc:   # unexpected - show it rather than failing silently
            self._set_db_info(f"Couldn't read that file: {exc}", "Bad.TLabel")
            return
        finally:
            self.root.configure(cursor="")
        if not libraries:
            self._set_db_info("No movie libraries in this database. (Did you pick the '...blobs.db' file? "
                              "Choose com.plexapp.plugins.library.db instead.)", "Bad.TLabel")
            return
        self.status_var.set("Ready - click Export spreadsheet.")

        self.libraries = libraries
        excluded = set(prefs.get(self, "excluded_libraries"))
        cols = 3
        for i, lib in enumerate(libraries):
            var = tk.BooleanVar(value=lib.name not in excluded)
            self.lib_vars[lib.id] = var
            cb = ttk.Checkbutton(self.lib_frame, text=f"{lib.name}  ({lib.movie_count:,})", variable=var)
            cb.grid(row=i // cols, column=i % cols, sticky="w", padx=(0, 22), pady=1)
            if lib.folders:
                Tooltip(cb, "\n".join(lib.folders))
        self._fit_export_later()                 # (more rows of libraries: is there still room for everything?)

        total = sum(lib.movie_count for lib in libraries)
        name = os.path.basename(path)
        when = dump_date(path)
        kind = f"Plex backup from {when}" if when in name else f"Database modified {when}"
        # library items: a film in two libraries is two rows of the spreadsheet (the status line counts it once)
        self._set_db_info(f"OK  -  {kind}  -  {len(libraries)} movie libraries, {total:,} movies (library items)",
                          "Good.TLabel")
        self._suggest_out(path)
        self.loaded_path = path
        self._remember(db_path=path)
        self._load_catalog(path)

    def _suggest_out(self, db: str):
        """Put the usual name for db's spreadsheet, in the folder for exports (Settings > Export), in the 'Save
        spreadsheet as' box."""
        self._suggested_out = default_output_path(db, prefs.get(self, "out_dir") or None)
        self.out_var.set(self._suggested_out)

    def browse_out(self):
        current = self.out_var.get().strip()
        folder = os.path.dirname(current) if current else ""
        path = filedialog.asksaveasfilename(
            parent=self.root, title="Save spreadsheet as", defaultextension=".xlsx",
            initialdir=folder if os.path.isdir(folder) else None,
            initialfile=os.path.basename(current) if current else f"{APP_NAME} Movies.xlsx",
            filetypes=[("Spreadsheet (.xlsx - opens in LibreOffice Calc or Excel)", "*.xlsx")])
        if path:
            path = os.path.normpath(path)
            self._confirmed_overwrite = path   # the save dialog already asked about replacing it
            prefs.set(self, "out_dir", os.path.dirname(path))
            self.out_var.set(path)             # (after: a new folder changes the suggestion to the folder's)

    # -- export --------------------------------------------------------------------------
    def start_export(self):
        from .export import OutputError, check_csv_writable, check_writable, csv_folder_for

        if self.worker and self.worker.is_alive():
            return
        db = self.db_var.get().strip().strip('"')
        out = self.out_var.get().strip().strip('"')
        if not db or not os.path.isfile(db):
            messagebox.showwarning(APP_NAME, "Choose a Plex database file first.")
            return
        if not self.libraries or os.path.normcase(db) != os.path.normcase(self.loaded_path or ""):
            self.load_db(db)
            if not self.libraries:
                return
        library_ids = [lib_id for lib_id, var in self.lib_vars.items() if var.get()]
        if not library_ids:
            messagebox.showwarning(APP_NAME, "Tick at least one library to export.")
            return
        if not out:
            out = default_output_path(db, prefs.get(self, "out_dir") or None)
        elif os.path.isdir(out) or out.endswith(("/", "\\")):
            out = os.path.join(out, default_output_name(db))   # a folder means "save in there"
        if not out.lower().endswith(".xlsx"):
            out += ".xlsx"
        out = os.path.abspath(out)
        self.out_var.set(out)
        try:
            check_writable(out)
            if self.csv_var.get():
                check_csv_writable(csv_folder_for(out))
        except OutputError as exc:
            messagebox.showwarning(APP_NAME, str(exc))
            return
        if os.path.exists(out) and getattr(self, "_confirmed_overwrite", None) != out:
            if not messagebox.askyesno(APP_NAME, f"{os.path.basename(out)} already exists.\n\nReplace it?"):
                return

        sheets = [n for n in S.DETAIL_SHEET_NAMES if self.sheet_vars[n].get()]
        self._remember(
            excluded_sheets=[n for n in S.DETAIL_SHEET_NAMES if n not in sheets],
            csv=self.csv_var.get(), open_when_done=self.open_var.get(), open_with=self.open_with_var.get(),
            excluded_libraries=sorted(lib.name for lib in self.libraries if not self.lib_vars[lib.id].get()))
        job = _Job(db=db, out=out, library_ids=library_ids, sheets=sheets, csv=self.csv_var.get())

        self.cancel_event.clear()
        self._set_running(True)
        self.progress["value"] = 0
        self.status_var.set("Starting...")
        self._log(f"Exporting {len(library_ids)} of {len(self.libraries)} libraries from {os.path.basename(db)}")
        self.worker = threading.Thread(target=self._work, args=(job,), daemon=True)
        self.worker.start()

    def _work(self, job: _Job):
        from .export import OutputError, csv_folder_for, write_csv, write_xlsx
        from .extract import Cancelled, PlexDBError, extract

        post = self.queue.put
        started = time.perf_counter()

        def stage(lo, hi):
            return lambda fraction, message: post(("progress", lo + (hi - lo) * fraction, message))

        try:
            result = extract(job.db, job.library_ids, job.sheets, progress=stage(0, 30), cancel=self.cancel_event)
            post(("log", f"Read {result.movie_count:,} movies. Writing the spreadsheet..."))
            write_xlsx(result, job.out, progress=stage(30, 92 if job.csv else 100), cancel=self.cancel_event)
        except Cancelled:
            post(("cancelled",))
            return
        except (PlexDBError, OutputError) as exc:
            post(("error", str(exc), None))
            return
        except MemoryError:
            post(("error", "Ran out of memory. Try again with fewer extra sheets ticked.", None))
            return
        except Exception as exc:
            post(("error", f"{type(exc).__name__}: {exc}", traceback.format_exc()))
            return

        # The spreadsheet is saved - from here on, nothing can un-save it.
        post(("saved", job.out))
        counts = ", ".join(f"{s.name} {len(s.rows):,}" for s in result.sheets)
        post(("log", f"Saved {job.out}"))
        post(("log", f"Rows - {counts}"))
        csv_dir, csv_problem = None, None
        if job.csv:
            try:
                csv_dir = write_csv(result, csv_folder_for(job.out), progress=stage(92, 100),
                                    cancel=self.cancel_event)
                post(("log", f"CSV files saved in {csv_dir}"))
            except Cancelled:
                csv_problem = "the CSV files were cancelled part-way"
            except OutputError as exc:
                csv_problem = f"the CSV files couldn't be saved: {exc}"
            except Exception as exc:
                csv_problem = f"the CSV files couldn't be saved: {type(exc).__name__}: {exc}"
        post(("done", job.out, csv_dir, csv_problem, result.movie_count, time.perf_counter() - started))

    def _poll(self):
        try:
            while True:
                msg = self.queue.get_nowait()
                kind = msg[0]
                if kind == "progress":
                    self.progress["value"] = msg[1]
                    self.status_var.set(msg[2])
                elif kind == "log":
                    self._log(msg[1])
                elif kind == "job":                      # a background task for a tab has finished
                    try:
                        self._job_ended(msg[1], msg[2])
                    except Exception:
                        self._log(traceback.format_exc().strip())
                        self.set_status("Something went wrong - the details are in the Export tab's log.")
                    if msg[1] in self._old_tab_jobs:     # (the last to hold an old tab: free them now)
                        self._old_tab_jobs.discard(msg[1])
                        if not self._old_tab_jobs:
                            self._free_old_tabs()
                elif kind == "saved":
                    self.last_output = msg[1]
                elif kind == "done":
                    _, out, csv_dir, csv_problem, movies, seconds = msg
                    self.last_output, self.last_csv = out, csv_dir
                    self.progress["value"] = 100
                    if csv_problem:
                        self.status_var.set(f"Spreadsheet saved ({movies:,} movies), but {csv_problem.split(':')[0]}.")
                        self._log(f"Spreadsheet saved, but {csv_problem}")
                    else:
                        self.status_var.set(f"Done - {movies:,} movies exported in {seconds:.0f} seconds.")
                    self._set_running(False)
                    if csv_problem and not self.closing:
                        messagebox.showwarning(APP_NAME, f"The spreadsheet was saved:\n{out}\n\nBut {csv_problem}")
                    if self.open_var.get() and not self.closing:
                        self.open_output()
                elif kind == "cancelled":
                    self.progress["value"] = 0
                    self.status_var.set("Cancelled.")
                    self._log("Export cancelled - the previous spreadsheet (if any) was left as it was.")
                    self._set_running(False)
                elif kind == "error":
                    _, text, details = msg
                    self.progress["value"] = 0
                    self.status_var.set("Export failed.")
                    self._log(f"ERROR: {text}")
                    if details:
                        self._log(details.strip())
                    self._set_running(False)
                    if not self.closing:
                        messagebox.showerror(APP_NAME, text)
        except queue.Empty:
            pass
        if self.closing and not (self.worker and self.worker.is_alive()):
            self.shutdown()
            return
        self._timers["poll"] = self.root.after(100, self._poll)

    def _set_running(self, running: bool):
        state = "disabled" if running else "normal"
        for w in (self.export_btn, self.db_browse, self.out_browse, self.db_entry):
            w.configure(state=state)
        self.cancel_btn.configure(state="normal" if running else "disabled")
        done = not running and self.last_output and os.path.exists(self.last_output)
        self.open_btn.configure(state="normal" if done else "disabled")
        self.folder_btn.configure(state="normal" if done else "disabled")

    def cancel_export(self):
        if self.worker and self.worker.is_alive():
            self.cancel_event.set()
            self.status_var.set("Cancelling...")
            self.cancel_btn.configure(state="disabled")

    def open_output(self):
        if not (self.last_output and os.path.exists(self.last_output)):
            return
        try:
            self.open_spreadsheet(self.last_output)
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Couldn't open the spreadsheet in {prefs.get(self, 'open_with')}:\n"
                                           f"{exc}\n\nIt's saved at:\n{self.last_output}")

    def open_folder(self):
        if self.last_output:
            _open_path(os.path.dirname(self.last_output), select=self.last_output)

    # -- Letterboxd --------------------------------------------------------------------------
    def export_letterboxd(self):
        """Export for Letterboxd...: your plays and ratings as the CSV file Letterboxd's importer reads (letterboxd.py;
        Settings > Letterboxd says what goes in), saved in the background where the save box says. The line beside
        the button says what was written; it's opened afterwards only when 'Open the spreadsheet when it's
        finished' is on."""
        catalog = self.catalog
        if catalog is None:
            self._letterboxd_says("The collection is still being read - try again in a moment."
                                  if self.catalog_state == "loading" else "Choose a Plex database first.", bad=True)
            return
        db = self.loaded_path or catalog.source
        folder = next((f for f in (prefs.get(self, "letterboxd_dir"), prefs.get(self, "out_dir"), os.path.dirname(db))
                       if f and os.path.isdir(f)), None)
        path = filedialog.asksaveasfilename(
            parent=self.root, title="Save for Letterboxd as", defaultextension=".csv", initialdir=folder,
            initialfile=letterboxd.file_name(db), filetypes=[("CSV file for Letterboxd's importer", "*.csv")])
        if not path:
            return
        path = os.path.normpath(path)
        prefs.set(self, "letterboxd_dir", os.path.dirname(path))
        # the libraries unticked on this tab as they are now (as last saved, when none are showing)
        unticked = ([lib.name for lib in self.libraries if not self.lib_vars[lib.id].get()] if self.lib_vars
                    else prefs.get(self, "excluded_libraries"))
        request = letterboxd.options_for(self, unticked)

        def work():
            lines = letterboxd.rows(catalog, request)
            return lines, (letterboxd.write(path, lines) if lines else [])

        def done(result):
            lines, paths = result
            if paths:
                letterboxd.remember(self, lines)             # (where the next 'only what's new' starts)
            text = letterboxd.saved_words(lines, paths)
            self._letterboxd_says(text)
            self.set_status(text)
            self._log(text)
            for p in paths:
                self._log(f"    {p}")
            if paths and prefs.get(self, "open_when_done") and not self.closing:
                try:
                    self.open_spreadsheet(paths[0])
                except OSError as exc:
                    self._log(f"Couldn't open {paths[0]} in {prefs.get(self, 'open_with')}: {exc}")

        def failed(message):
            self._letterboxd_says(f"Couldn't save the file for Letterboxd: {message}", bad=True)
            self.set_status("Couldn't save the file for Letterboxd - the details are in the Export tab's log.")

        self.run(work, done, failed, status="Saving your plays and ratings for Letterboxd...", key="letterboxd")

    def _letterboxd_says(self, text: str, bad: bool = False):
        self.letterboxd_var.set(text)
        self.letterboxd_label.configure(style="Bad.TLabel" if bad else "Hint.TLabel")

    def on_close(self):
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno(APP_NAME, "An export is still running. Stop it and quit?"):
                return
            self.closing = True
            self.cancel_event.set()
            self.cancel_jobs()
            self.status_var.set("Stopping...")
            return   # _poll closes the window once the worker has cleaned up
        self.shutdown()

    def shutdown(self):
        """Close the window, cancelling its timers first so none fires after it's gone."""
        try:
            remember = {}
            state = self.root.state()
            if state != "withdrawn":                 # (a window never shown - tests - remembers nothing)
                current = self.current_tab()
                title = current.title if current is not None else "Export"
                if self.settings.get("last_tab") != title:       # (for 'Open on: the tab you last had open')
                    remember["last_tab"] = title
            if state == "normal":
                remember["window_geometry"] = self.root.geometry().split("+")[0]
            if state != "withdrawn":
                for tab in self.tabs:                    # (what a tab keeps only until the window closes)
                    try:
                        remember.update(tab.closing() or {})
                    except Exception:
                        pass
            if remember:
                self._remember(**remember)
        except tk.TclError:
            pass
        try:
            # Hide it at once: taking hundreds of widgets down can take a few seconds while a background job
            # is busy, and the window shouldn't sit there frozen meanwhile.
            self.root.withdraw()
        except tk.TclError:
            pass
        self.cancel_jobs()                      # ...and call the jobs off, so none is busy for long
        self._wait_for_jobs()
        try:
            from .ui import widgets
            if widgets.report_error == self._log:
                widgets.report_error = None
        except Exception:
            pass
        if theme.report == self._log:
            theme.report = None
        self.closing = True
        for timer in self._timers.values():
            try:
                self.root.after_cancel(timer)
            except tk.TclError:
                pass
        self.root.destroy()


def _open_path(path: str, select: str | None = None):
    """Show a folder (with a file selected) in the file manager."""
    try:
        if sys.platform == "win32":
            if select and os.path.exists(select):
                import subprocess
                subprocess.Popen(["explorer", "/select,", os.path.normpath(select)])
            else:
                os.startfile(path)   # type: ignore[attr-defined]
        else:
            from .files import launch
            launch("open" if sys.platform == "darwin" else "xdg-open", path)
    except OSError as exc:
        messagebox.showerror(APP_NAME, f"Couldn't open {path}:\n{exc}")


def _enable_dpi_awareness():
    if sys.platform != "win32":
        return
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def _share_time_with_the_window():
    """Keep the window responsive while a tab's background job runs.

    Those jobs are pure Python on a thread, and only one thread runs Python at a time. By default the window
    thread gets its turn back only after 5 ms, rounded up to Windows' 15.6 ms timer tick, so every Tk call it
    makes waits ~25 ms and a busy job made the whole window crawl (a chart redraw took as long as the whole job,
    clicks and tab switches seconds). Measured with a busy thread: 36 Tk calls a second by default; 490 with a
    1 ms switch interval and a 1 ms timer (a chart redraw still ~1 s); about 40,000 with an interval under 1 ms,
    which Windows turns into "ask for a turn at once" (the same redraw 10 ms, near its 6 ms when idle). The job
    loses nothing while the window is idle; while both are busy they trade turns quickly, and the one waiting
    keeps a processor core busy until its turn comes.
    The 1 ms timer is kept for Pythons that time that wait exactly. Returns a function that puts the timer back
    (called on the way out)."""
    try:
        sys.setswitchinterval(0.0005)
    except (AttributeError, ValueError):
        pass
    if sys.platform != "win32":
        return lambda: None
    try:
        import ctypes
        winmm = ctypes.windll.winmm
        if winmm.timeBeginPeriod(1) != 0:             # TIMERR_NOERROR
            return lambda: None
    except Exception:
        return lambda: None

    def restore():
        try:
            winmm.timeEndPeriod(1)
        except Exception:
            pass
    return restore


xlsxwriter_missing = needs.xlsxwriter_missing       # (what the box says when XlsxWriter isn't installed)


def _new_root() -> tk.Tk:
    """The app's Tk window. Away from Windows its class is Projectionist (WM_CLASS), so the desktop matches it to
    projectionist.desktop - the dock shows its name and icon, not Python's or Tk's."""
    root = tk.Tk() if sys.platform == "win32" else tk.Tk(className=APP_NAME)
    theme.quiet_theme_changes(root)              # (from the start: see there)
    return root


def main(initial_db: str | None = None) -> int:
    _enable_dpi_awareness()
    appicon.set_app_id()                         # Projectionist's own taskbar button and icon, not Python's
    try:
        import xlsxwriter  # noqa: F401
    except ImportError:
        root = _new_root()
        root.withdraw()
        appicon.apply(root)
        messagebox.showerror(APP_NAME, xlsxwriter_missing(sys.executable))
        root.destroy()
        return 1
    migrate_settings()                           # before the window reads them
    restore_timer = _share_time_with_the_window()
    try:
        root = _new_root()
        root.withdraw()                          # built unseen, already in its look: no white flash
        appicon.apply(root)                      # (and every window after it)
        apply_text_size(root, load_settings())   # (before anything is measured or built)
        App(root, initial_db)
        _show(root)
        root.mainloop()
    finally:
        restore_timer()
    return 0


def apply_text_size(root, source=None) -> float:
    """Settings > Appearance > Text size: Tk's scaling (pixels per point) grows by the chosen factor - on a new
    window before anything is built in it, or on the open window (App.apply_text_size then measures every font
    again and lays the tabs out anew). Every font given in points grows with it, and so does theme.scale() - the
    unit every page, table and chart lays itself out in - so everything grows in proportion. Calling it again
    starts from Tk's own scaling, not the last one. -> the factor."""
    factor = prefs.get(source, "text_size") / 100
    try:
        base = root.__dict__.setdefault("_text_size_base", float(root.tk.call("tk", "scaling")))
        root.tk.call("tk", "scaling", base * factor)
    except (tk.TclError, ValueError):
        return 1.0
    return factor


def _cancel_timers_under(widget) -> int:
    """Call off the Tk timers (after, after_idle) set on widget or anything in it - for a tab destroyed while the
    window stays open: a timer left behind would call a command that went with it. -> how many."""
    names, todo = set(), [widget]
    while todo:
        w = todo.pop()
        names.update(getattr(w, "_tclCommands", None) or ())
        todo.extend(getattr(w, "children", {}).values())
    call, count = widget.tk.call, 0
    for timer in widget.tk.splitlist(call("after", "info")) if names else ():
        try:
            script = widget.tk.splitlist(call("after", "info", timer))[0]
            if str(script).split()[:1] and str(script).split()[0] in names:
                call("after", "cancel", timer)
                count += 1
        except (tk.TclError, IndexError):
            pass
    return count


def _show(root):
    """Show the window App built while it was hidden. Windows only makes a window's title bar when it first shows
    it: so it's shown fully transparent, its title bar is put in the look, and then it's made solid."""
    try:
        root.attributes("-alpha", 0.0)
    except tk.TclError:
        pass
    root.deiconify()
    try:
        root.update_idletasks()                  # (the window is made and mapped - still transparent)
    except tk.TclError:
        pass
    appicon.apply(root, shown=True)              # (its own icon, now that Windows has made it: sharp at 16 px)
    theme.title_bar(root)
    try:
        root.attributes("-alpha", 1.0)
    except tk.TclError:
        pass
