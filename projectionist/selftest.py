"""A check of the app as it was built, with nothing on screen:

    Projectionist.exe --self-test REPORT [--db DATABASE] [--no-export]
    python -m projectionist.selftest REPORT [--db DATABASE] [--no-export]      (the same, from the source)

It builds the real window, hidden, with settings of its own in a temporary folder (yours are never read or
changed); opens the database (with no --db: whatever the window would open by itself - with no settings, the newest
Plex backup in the app's own folder, if there is one); waits for the collection to be read; brings every tab to the
front in turn; and exports the Movies sheet to the temporary folder, which goes when it ends. What it found goes in
REPORT, a plain-text file whose last line is PASS or FAIL. The exit code is 0 for PASS and 1 for FAIL (2: the
options were wrong).

Meanwhile nothing is shown or started: the window is never put on screen (nor is any other), message boxes and
file choosers answer by themselves, menus don't pop up, and no spreadsheet, folder or program is opened. The
database is only read, as it always is. tools/build_exe.py runs it to check a new Projectionist.exe.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import os
import platform
import sys
import tempfile
import time
import traceback
import zipfile

from . import APP_NAME, __version__

CATALOG_SECONDS = 300          # the longest the collection may take to be read
SETTLE_SECONDS = 120           # ...and a tab to finish what it does when it's brought to the front
TRACEBACK = "Traceback (most recent call last)"


class Report:
    """The lines of the report, and what failed."""

    def __init__(self):
        self.lines: list[str] = []
        self.failures: list[str] = []

    def say(self, text: str = ""):
        self.lines.append(text)

    def fail(self, text: str):
        self.failures.append(text)
        self.lines.append(f"FAILED: {text}")

    @property
    def passed(self) -> bool:
        return not self.failures

    def text(self) -> str:
        verdict = "PASS" if self.passed else f"FAIL ({len(self.failures)} problem{'s' * (len(self.failures) != 1)})"
        return "\n".join(self.lines + ["", verdict]) + "\n"


class Patches:
    """Attributes swapped for the length of the check, and put back afterwards (unittest.mock isn't in the built
    app)."""

    def __init__(self):
        self._undo = []

    def set(self, owner, name: str, value):
        here = vars(owner)
        self._undo.append((owner, name, name in here, here.get(name)))
        setattr(owner, name, value)

    def undo(self):
        while self._undo:
            owner, name, had, old = self._undo.pop()
            try:
                if had:
                    setattr(owner, name, old)
                else:
                    delattr(owner, name)
            except (AttributeError, TypeError):
                pass


def _nothing_on_screen(patches: Patches, said: list):
    """No window, box, menu or program while the check runs: each is answered or refused here instead, and what
    the app tried to show is kept in `said`."""
    import tkinter as tk
    from tkinter import filedialog, messagebox

    from . import files, gui
    from .ui import paint

    def box(kind, answer):
        def shown(*args, **kwargs):
            text = " ".join(str(a) for a in args[1:2]) or str(kwargs.get("message", ""))
            said.append((kind, text))
            return answer
        return shown

    for name in ("showerror", "showwarning", "showinfo"):
        patches.set(messagebox, name, box(name, "ok"))
    for name in ("askyesno", "askokcancel", "askretrycancel", "askyesnocancel"):
        patches.set(messagebox, name, box(name, False))
    patches.set(messagebox, "askquestion", box("askquestion", "no"))
    for name in ("askopenfilename", "asksaveasfilename", "askdirectory"):
        patches.set(filedialog, name, lambda *a, **k: "")
    patches.set(filedialog, "askopenfilenames", lambda *a, **k: ())
    patches.set(tk.Menu, "tk_popup", lambda *a, **k: None)
    patches.set(tk.Menu, "post", lambda *a, **k: None)
    patches.set(paint.Interaction, "_choose", lambda *a, **k: None)
    patches.set(tk.Wm, "wm_deiconify", lambda *a, **k: None)
    patches.set(tk.Wm, "deiconify", lambda *a, **k: None)
    made = tk.Toplevel.__init__

    def hidden_toplevel(self, *args, **kwargs):
        made(self, *args, **kwargs)
        try:
            self.withdraw()
        except tk.TclError:
            pass
    patches.set(tk.Toplevel, "__init__", hidden_toplevel)

    def refused(*args, **kwargs):
        said.append(("open", " ".join(str(a) for a in args)))
        raise OSError("nothing is opened during a self-test")
    patches.set(files, "open_spreadsheet", refused)
    patches.set(files, "launch", refused)
    patches.set(gui, "open_spreadsheet", refused)
    patches.set(gui, "_open_path", lambda *a, **k: said.append(("open", " ".join(str(x) for x in a))))
    if hasattr(os, "startfile"):
        patches.set(os, "startfile", refused)


def _about(report: Report):
    import sqlite3
    frozen = bool(getattr(sys, "frozen", False))
    report.say(f"{APP_NAME} {__version__} self-test, {time.strftime('%Y-%m-%d %H:%M:%S')}")
    report.say(f"Program:     {sys.executable}" + ("  (the built app)" if frozen else "  (Python, running the source)"))
    report.say(f"Python:      {platform.python_version()} ({platform.architecture()[0]}), on {platform.platform()}")
    report.say(f"SQLite:      {sqlite3.sqlite_version}")
    try:
        import tkinter
        report.say(f"Tk:          {tkinter.TkVersion}")
    except ImportError as exc:
        report.fail(f"tkinter can't be imported: {exc}")
    try:
        import xlsxwriter
        report.say(f"XlsxWriter:  {xlsxwriter.__version__}")
    except ImportError as exc:
        report.fail(f"XlsxWriter isn't there, so no spreadsheet can be written: {exc}")
    pillow = importlib.util.find_spec("PIL") is not None
    report.say(f"Pillow:      {'there (the app never needs it)' if pillow else 'not there (the app never needs it)'}")


def _assets(report: Report):
    from . import appicon
    missing = [p for p in [appicon.ICO] + [appicon.png(s) for s in appicon.PNG_SIZES] if not os.path.isfile(p)]
    if missing:
        report.fail("icon files missing: " + ", ".join(missing))
    else:
        report.say(f"Icon files:  all {1 + len(appicon.PNG_SIZES)} there ({appicon.ASSETS})")


def _modules(report: Report):
    from . import gui
    names = ["catalog", "recommend", "costars", "insights", "credits", "critics", "habits", "doctor", "filmpage",
             "letterboxd", "export", "extract", "ask"] + [f"ui.{m}" for m in gui.TAB_MODULES]
    broken = []
    for name in names:
        try:
            importlib.import_module(f"{__package__}.{name}")
        except Exception as exc:                 # noqa: BLE001
            broken.append(f"{name} ({exc})")
    if broken:
        report.fail("modules that can't be imported: " + ", ".join(broken))
    else:
        report.say(f"Modules:     all {len(names)} import")


def _count_widgets(widget) -> int:
    todo, count = [widget], 0
    while todo:
        w = todo.pop()
        count += 1
        todo.extend(w.winfo_children())
    return count


class _Window:
    """The real main window, hidden, pumped by hand."""

    def __init__(self, report: Report, db: str | None, errors: list):
        from . import appicon, gui
        self.report, self.errors = report, errors
        gui._enable_dpi_awareness()
        self.root = gui._new_root()
        self.root.withdraw()                         # never shown
        if not appicon.apply(self.root):
            report.fail("the window didn't take the app's icon")
        gui.apply_text_size(self.root, gui.load_settings())
        started = time.perf_counter()
        self.app = gui.App(self.root, db)
        report.say(f"Window:      built in {time.perf_counter() - started:.1f} s, never shown "
                   f"({len(self.app.tabs) + 1} tabs)")

    def pump(self, until, seconds: float) -> bool:
        deadline = time.perf_counter() + seconds
        while not until():
            self.root.update()
            if time.perf_counter() > deadline:
                return False
            time.sleep(0.005)
        return True

    def settle(self, seconds: float = SETTLE_SECONDS) -> bool:
        """Until no background job is left and nothing waits on the queue."""
        app = self.app
        for _ in range(2):
            if not self.pump(lambda: not app._held and app.queue.empty(), seconds):
                return False
            for _ in range(3):
                self.root.update()
        return True

    def open_collection(self, db: str | None) -> str | None:
        app, report = self.app, self.report
        self.pump(lambda: False, 0.3)                # (the window picks its database 50 ms after it's built)
        started = time.perf_counter()
        if not app.loaded_path:
            if db:
                report.fail(f"the database didn't open: {app.db_info_var.get()}")
            else:
                report.say("Database:    none given, and none found in the app's own folder - the tabs are "
                           "checked without one")
            return None
        report.say(f"Database:    {app.loaded_path}")
        report.say(f"             {app.db_info_var.get()}")
        if not self.pump(lambda: app.catalog_state in ("ready", "error"), CATALOG_SECONDS):
            report.fail(f"the collection wasn't read within {CATALOG_SECONDS} s")
            return app.loaded_path
        if app.catalog_state == "error":
            report.fail(f"the collection couldn't be read: {app.catalog_error}")
            return app.loaded_path
        films = len(getattr(app.catalog, "films", {}) or {})
        report.say(f"Collection:  {films:,} films, read in {time.perf_counter() - started:.1f} s")
        self.settle()
        return app.loaded_path

    def visit_tabs(self):
        app, report = self.app, self.report
        notebook = app.notebook
        titles = []
        for frame in notebook.tabs():
            title = notebook.tab(frame, "text")
            before = len(self.errors)
            started = time.perf_counter()
            notebook.select(frame)
            settled = self.settle()
            took = time.perf_counter() - started
            widget = app.root.nametowidget(frame)
            titles.append(title)
            line = f"  {title:<16} {took:5.2f} s  {_count_widgets(widget):5,} widgets"
            if not settled:
                report.fail(f"the {title} tab was still busy after {SETTLE_SECONDS} s")
            elif len(self.errors) > before:
                report.fail(f"the {title} tab reported {len(self.errors) - before} error(s)")
            else:
                report.say(line)
        if notebook.select() != notebook.tabs()[-1]:
            report.fail("the last tab didn't come to the front")
        return titles

    def close(self):
        try:
            self.app.shutdown()
        except Exception:                           # noqa: BLE001
            self.report.fail("the window didn't close cleanly:\n" + traceback.format_exc().strip())
        try:
            self.root.destroy()
        except Exception:                           # noqa: BLE001  (shutdown() has already)
            pass


def _export(report: Report, db: str, folder: str):
    """The Movies sheet (no extra sheets), as the Export tab and the command line write it."""
    from .export import write_xlsx
    from .extract import extract
    out = os.path.join(folder, f"{APP_NAME} self-test.xlsx")
    started = time.perf_counter()
    result = extract(db, None, [])
    write_xlsx(result, out)
    took = time.perf_counter() - started
    with zipfile.ZipFile(out) as workbook:
        names = set(workbook.namelist())
    if "xl/workbook.xml" not in names or not any(n.startswith("xl/worksheets/") for n in names):
        report.fail(f"the spreadsheet written isn't a workbook: {sorted(names)[:8]}")
        return
    report.say(f"Export:      {result.movie_count:,} movies, {os.path.getsize(out):,} bytes, in {took:.1f} s "
               "(the Movies sheet, to a temporary folder - deleted again)")


def check(report: Report, db: str | None = None, export: bool = True):
    """Everything above, in order, into `report`."""
    _about(report)
    _assets(report)
    _modules(report)
    if db and not os.path.isfile(db):
        report.fail(f"there's no database at {db}")
        return
    from . import gui
    patches, said, errors = Patches(), [], []
    with tempfile.TemporaryDirectory(prefix="projectionist-selftest-") as scratch:
        patches.set(gui, "SETTINGS_DIR", scratch)
        patches.set(gui, "SETTINGS_FILE", os.path.join(scratch, "settings.json"))
        logged = gui.App._log

        def log(app, text):
            if TRACEBACK in str(text):
                errors.append(str(text))
            return logged(app, text)
        patches.set(gui.App, "_log", log)
        failed = gui.App._task_failed

        def task_failed(app, message):
            errors.append(f"a background job failed: {message}")
            return failed(app, message)
        patches.set(gui.App, "_task_failed", task_failed)
        window = None
        try:
            _nothing_on_screen(patches, said)
            report.say("Settings:    a temporary folder of the check's own (yours are neither read nor changed)")
            window = _Window(report, db, errors)
            opened = window.open_collection(db)
            report.say("Tabs:")
            window.visit_tabs()
            window.close()
            window = None
            if opened and export and report.passed:
                _export(report, opened, scratch)
        finally:
            if window is not None:
                window.close()
            patches.undo()
    for text in errors:
        report.fail(f"an error was reported:\n{text.strip()}")
    for kind, text in said:
        if kind in ("showerror", "showwarning"):
            report.fail(f"the app would have shown a message ({kind}): {text}")
        elif kind == "open":
            report.fail(f"the app tried to open something: {text}")
        else:
            report.say(f"(the app would have asked or said: {text})")


def run(report_path: str, db: str | None = None, export: bool = True) -> int:
    """Check, and write the report to report_path. -> 0 (PASS) or 1 (FAIL)."""
    report = Report()
    started = time.perf_counter()
    try:
        check(report, db, export)
    except BaseException:                           # noqa: BLE001  (whatever stopped it goes in the report)
        report.fail("the self-test stopped:\n" + traceback.format_exc().strip())
    report.say(f"Took {time.perf_counter() - started:.1f} s in all.")
    folder = os.path.dirname(os.path.abspath(report_path))
    os.makedirs(folder, exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report.text())
    return 0 if report.passed else 1


def main(argv=None, prog: str = "python -m projectionist.selftest") -> int:
    parser = argparse.ArgumentParser(prog=prog, description=f"Check {APP_NAME} with nothing on screen, and write "
                                                            "what was found to a text file.")
    parser.add_argument("report", help="the text file to write the report to")
    parser.add_argument("--db", help="the Plex database to open (default: what the window would open by itself)")
    parser.add_argument("--no-export", action="store_true", help="don't export the Movies sheet")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:                       # (a bad option: the usage went to the console, if any)
        return int(exc.code or 0)
    return run(args.report, args.db, export=not args.no_export)


if __name__ == "__main__":
    sys.exit(main())
