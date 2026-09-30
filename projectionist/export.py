"""Write an ExportResult to an .xlsx workbook (and optionally a folder of CSV files)."""

from __future__ import annotations

import csv
import os
import shutil
import stat
import sys
import tempfile
import time
import traceback
from datetime import date, datetime

from . import APP_NAME
from . import sheets as S
from .extract import Cancelled, ExportResult, Progress

EXCEL_CELL_LIMIT = 32767          # characters per cell
EXCEL_URL_LIMIT = 65530           # hyperlinks per worksheet
EXCEL_URL_LENGTH = 2079           # characters per hyperlink
EXCEL_MAX_ROWS = 1_048_576
TRUNCATED = " [...truncated]"


class OutputError(Exception):
    """The spreadsheet can't be saved where asked: open in another program, read-only, no permission..."""


def _umask() -> int:
    """The umask: what a new file's permissions leave out. Read from Linux's /proc (setting it to read it would
    change it, for a moment, for every thread), else by setting it and putting it back."""
    try:
        with open("/proc/self/status", encoding="ascii", errors="replace") as f:
            for line in f:
                if line.startswith("Umask:"):
                    return int(line.split()[1], 8)
    except (OSError, ValueError, IndexError):
        pass
    mask = os.umask(0o022)
    os.umask(mask)
    return mask


def usual_permissions(temporary: str, final: str) -> None:
    """A file written through tempfile.mkstemp is its owner's alone (0600) on Linux and Macs. Before it's moved to
    `final`, give it the permissions of the file it replaces - or, a new file, the ones any program's new file gets
    (0666 less the umask: 0644 as a rule), so a spreadsheet saved in a shared folder is readable there like any
    other. Nothing on Windows. Never fails: at worst the file stays its owner's alone."""
    if os.name == "nt":
        return
    try:
        try:
            mode = stat.S_IMODE(os.stat(final).st_mode)
        except OSError:
            mode = 0o666 & ~_umask()
        os.chmod(temporary, mode)
    except OSError:
        pass


def check_writable(path: str):
    """Fail early - before minutes of work - if the spreadsheet can't be saved at this path."""
    path = os.path.abspath(path)
    name = os.path.basename(path)
    folder = os.path.dirname(path) or "."
    if not os.path.isdir(folder):
        raise OutputError(f"The folder doesn't exist:\n{folder}")
    if os.path.isdir(path):
        raise OutputError(f"{name} is a folder. Choose a file name for the spreadsheet instead.")
    problem = invalid_file_name(name)
    if problem:
        raise OutputError(f"'{name}' can't be used as a file name - {problem}. Choose a different name.")
    if os.path.exists(path):
        if getattr(os.stat(path), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_READONLY:
            raise OutputError(f"{name} is marked read-only, so it can't be replaced.\n\n"
                              "Choose a different file name, or clear 'Read-only' in the file's Properties.")
        if sys.platform == "win32":
            try:
                with open(path, "r+b"):
                    pass
                locked = _locked_against_replace(path)
            except PermissionError as exc:
                if getattr(exc, "winerror", None) not in (32, 33):   # sharing / lock violation
                    raise OutputError(f"You don't have permission to replace {name}.\n\n"
                                      "Choose a different file name or folder.") from None
                locked = True
            if locked:
                raise OutputError(
                    f"{name} is open in another program (probably LibreOffice Calc or Excel).\n\n"
                    "Close it and try again, or choose a different file name.")
        # Elsewhere, replacing a file only needs permission to create files in its folder - checked next.
    try:
        fd, probe = tempfile.mkstemp(prefix="~projectionist-", suffix=".xlsx", dir=folder)
    except OSError as exc:
        raise OutputError(f"Can't save in {folder} - no permission to create files there.\n\n"
                          f"Choose a different folder. ({exc.strerror or exc})") from None
    os.close(fd)
    try:
        if not os.path.exists(path):
            # Try the exact move the save will make, so anything else Windows rejects about the name
            # shows up now rather than after minutes of work.
            try:
                os.replace(probe, path)
            except OSError as exc:
                raise OutputError(f"'{name}' can't be saved there: {exc.strerror or exc}. "
                                  "Choose a different name.") from None
            os.remove(path)
    finally:
        if os.path.exists(probe):
            os.remove(probe)


_FORBIDDEN_CHARS = set('<>:"|?*') | {chr(c) for c in range(32)}


def invalid_file_name(name: str) -> str:
    """Why Windows would refuse this file name, or '' if it's fine."""
    if len(name.encode("utf-16-le")) // 2 > 255:   # Windows counts UTF-16 units (an emoji is two)
        return "it's longer than the 255 characters Windows allows"
    if sys.platform == "win32":
        bad = sorted(set(name) & _FORBIDDEN_CHARS)
        if bad:
            shown = " ".join(c if c.isprintable() else f"(character {ord(c)})" for c in bad)
            return f"Windows doesn't allow {shown} in file names"
        if name != name.rstrip(" ."):
            return "Windows doesn't allow a file name to end with a space or a dot"
    return ""


def _locked_against_replace(path: str) -> bool:
    """True if another program holds the file open in a way that stops it being replaced.

    Excel and LibreOffice let others read (even write) an open workbook but not delete it, and replacing
    a file needs delete access - so ask Windows for exactly that, while sharing everything ourselves.
    """
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                     wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    DELETE, SHARE_ALL, OPEN_EXISTING, ERROR_SHARING_VIOLATION = 0x00010000, 0x7, 3, 32
    handle = kernel32.CreateFileW(path, DELETE, SHARE_ALL, None, OPEN_EXISTING, 0, None)
    if handle is None or handle == wintypes.HANDLE(-1).value:
        return ctypes.get_last_error() == ERROR_SHARING_VIOLATION
    kernel32.CloseHandle(handle)
    return False


def _fit(text: str) -> str:
    if len(text) > EXCEL_CELL_LIMIT:
        return text[:EXCEL_CELL_LIMIT - len(TRUNCATED)] + TRUNCATED
    return text


def write_xlsx(result: ExportResult, path: str, progress: Progress | None = None, cancel=None) -> str:
    """Write every sheet to an .xlsx workbook (opens in LibreOffice Calc and Excel). Returns the final path."""
    import xlsxwriter   # imported here so the GUI can explain how to install it if missing

    progress = progress or (lambda fraction, message: None)
    path = os.path.abspath(path)
    check_writable(path)
    folder = os.path.dirname(path)
    sweep_stale_temp_files(folder)
    try:
        fd, tmp_path = tempfile.mkstemp(prefix="~projectionist-", suffix=".xlsx", dir=folder)
    except OSError as exc:
        raise OutputError(f"Can't save in {folder}: {exc.strerror or exc}") from None
    os.close(fd)
    work_dir = tempfile.mkdtemp(prefix="projectionist-")   # xlsxwriter's scratch files, removed afterwards

    total_rows = sum(len(s.rows) for s in result.sheets) or 1
    done_rows = 0
    wb = None
    try:
        wb = xlsxwriter.Workbook(tmp_path, {
            "constant_memory": True,      # stream rows to disk - keeps memory flat for big libraries
            "tmpdir": work_dir,
            "strings_to_numbers": False,
            "strings_to_formulas": False,  # a title like '=Wonder' must stay text
            "strings_to_urls": False,
            "nan_inf_to_errors": True,
        })
        wb.set_properties({"title": "Movie library", "comments": f"Created by {APP_NAME}"})
        fmt = _Formats(wb)

        pages = _pages(result.sheets)
        for title, sheet, rows in pages:
            if title != sheet.name:
                progress(done_rows / total_rows, f"{sheet.name} has more rows than fit on one Excel sheet "
                                                 f"- continuing on '{title}'")
            ws = wb.add_worksheet(title)
            # IDs such as '603' are deliberately text; don't flag every one with Excel's green triangle.
            ws.ignore_errors({"number_stored_as_text": "A1:XFD1048576"})
            cols = sheet.columns
            for c, col in enumerate(cols):
                ws.set_column(c, c, col.width)
            ws.set_row(0, 30)
            for c, col in enumerate(cols):
                ws.write_string(0, c, col.header, fmt.header)
            urls = 0
            for r, row in enumerate(rows, 1):
                for c, col in enumerate(cols):
                    value = row.get(col.key)
                    if value is None or value == "":
                        continue
                    kind = col.kind
                    if kind == "url":
                        value = str(value)
                        if urls < EXCEL_URL_LIMIT and len(value) <= EXCEL_URL_LENGTH and \
                                ws.write_url(r, c, value, fmt.link, string=value) == 0:
                            urls += 1
                        else:
                            ws.write_string(r, c, _fit(value))
                    elif kind == "date" and isinstance(value, (date, datetime)):
                        if isinstance(value, date) and not isinstance(value, datetime):
                            value = datetime(value.year, value.month, value.day)
                        if value.year < 1900:   # Excel can't show dates before 1900
                            ws.write_string(r, c, value.strftime("%Y-%m-%d"))
                        else:
                            ws.write_datetime(r, c, value, fmt.date)
                    elif kind == "datetime" and isinstance(value, datetime):
                        if value.year < 1900:
                            ws.write_string(r, c, value.strftime("%Y-%m-%d %H:%M"))
                        else:
                            ws.write_datetime(r, c, value, fmt.datetime)
                    elif kind in fmt.numbers and isinstance(value, (int, float)) and not isinstance(value, bool):
                        ws.write_number(r, c, value, fmt.numbers[kind])
                    else:
                        ws.write_string(r, c, _fit(str(value)))
                done_rows += 1
                if done_rows % 2000 == 0:
                    if cancel is not None and cancel.is_set():
                        raise Cancelled()
                    progress(done_rows / total_rows, f"Writing {title}... {r:,}/{len(rows):,} rows")
            ws.freeze_panes(1, sheet.freeze_cols)
            ws.autofilter(0, 0, max(len(rows), 1), len(cols) - 1)
            progress(done_rows / total_rows, f"Wrote {title} ({len(rows):,} rows)")

        _write_about(wb, fmt, result, pages)
        wb.worksheets()[0].activate()
        progress(1.0, "Saving workbook...")
        try:
            wb.close()
        except Exception as exc:   # xlsxwriter's FileCreateError etc. - e.g. the disk filled up
            raise OutputError(f"Couldn't save the spreadsheet: {exc}") from exc
        if cancel is not None and cancel.is_set():   # last chance: the old file is still untouched
            raise Cancelled()
        usual_permissions(tmp_path, path)
        try:
            os.replace(tmp_path, path)
        except PermissionError:
            raise OutputError(
                f"Couldn't replace {os.path.basename(path)} - it's open in another program "
                "(probably LibreOffice Calc or Excel).\n\n"
                "Close it and export again.") from None
        except OSError as exc:
            raise OutputError(f"Couldn't save {os.path.basename(path)}: {exc.strerror or exc}") from None
        return path
    except BaseException as exc:
        if wb is not None:
            _abandon(wb)
        _release_frames(exc)
        if isinstance(exc, OSError):
            # Rows stream into scratch files in the temp folder (maybe another drive) - e.g. it filled up.
            raise OutputError(f"Couldn't write the spreadsheet's temporary files in {tempfile.gettempdir()}: "
                              f"{exc.strerror or exc}") from None
        raise
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        shutil.rmtree(work_dir, ignore_errors=True)


STALE_AFTER_SECONDS = 6 * 3600
# Scratch names: 'projectionist-...' folders in the temp folder, '~projectionist-....xlsx' beside the spreadsheet.
# 'plexmovies-' ones were left by the app before it was renamed (it was Plex Movie Exporter) - swept the same way.
TEMP_PREFIXES = ("projectionist-", "plexmovies-")


def sweep_stale_temp_files(output_folder: str | None = None):
    """Remove scratch files left by an export that was killed outright (e.g. from Task Manager).

    Normal finishes, errors and cancels clean up after themselves; this catches the rest. Only our own
    prefixed items older than a few hours are touched, so a running export is never disturbed.
    """
    now = time.time()
    places = [(tempfile.gettempdir(), prefix, True) for prefix in TEMP_PREFIXES]
    if output_folder:
        places += [(output_folder, "~" + prefix, False) for prefix in TEMP_PREFIXES]
    for folder, prefix, is_dir in places:
        try:
            entries = [e for e in os.scandir(folder) if e.name.startswith(prefix)]
        except OSError:
            continue
        for e in entries:
            try:
                if now - e.stat().st_mtime < STALE_AFTER_SECONDS:
                    continue
                if is_dir and e.is_dir():
                    shutil.rmtree(e.path, ignore_errors=True)
                elif not is_dir and e.is_file() and e.name.endswith(".xlsx"):
                    os.remove(e.path)
            except OSError:
                pass


def _pages(sheets) -> list[tuple[str, object, list]]:
    """(worksheet title, sheet, rows) - a sheet too long for Excel continues on 'Name (2)', 'Name (3)'..."""
    per_page = EXCEL_MAX_ROWS - 1   # one row is the header
    pages = []
    for sheet in sheets:
        if len(sheet.rows) <= per_page:
            pages.append((sheet.name, sheet, sheet.rows))
            continue
        for n, start in enumerate(range(0, len(sheet.rows), per_page), 1):
            title = sheet.name if n == 1 else f"{sheet.name} ({n})"
            pages.append((title, sheet, sheet.rows[start:start + per_page]))
    return pages


def _release_frames(exc: BaseException):
    """Let go of the finished library frames an exception keeps alive.

    If saving fails part-way (disk full, network share dropped), xlsxwriter's zip writer - which still
    has the half-written file open - lives on in the traceback, and Windows then won't delete the file.
    """
    pending, seen = [exc], set()
    while pending:
        e = pending.pop()
        if e is None or id(e) in seen:
            continue
        seen.add(id(e))
        if e.__traceback__ is not None:
            traceback.clear_frames(e.__traceback__)   # skips frames that are still running
        pending += [e.__cause__, e.__context__]


def _abandon(wb):
    """Give up on a half-written workbook without leaving files behind."""
    # Otherwise xlsxwriter's destructor calls close() and writes out the half-finished file.
    wb.fileclosed = True
    for ws in wb.worksheets():
        fh = getattr(ws, "row_data_fh", None)   # constant_memory scratch file - must be closed to delete
        if fh is not None and not fh.closed:
            fh.close()


class _Formats:
    def __init__(self, wb):
        self.header = wb.add_format({"bold": True, "font_color": "#FFFFFF", "bg_color": "#1F3A5F",
                                     "text_wrap": True, "valign": "vcenter", "border": 1,
                                     "border_color": "#40597F"})
        self.date = wb.add_format({"num_format": "yyyy-mm-dd", "align": "left"})
        self.datetime = wb.add_format({"num_format": "yyyy-mm-dd hh:mm", "align": "left"})
        self.link = wb.add_format({"font_color": "#0B57D0", "underline": 1})
        self.numbers = {
            "int": wb.add_format({"num_format": "0"}),
            "float1": wb.add_format({"num_format": "0.0"}),
            "float2": wb.add_format({"num_format": "0.00"}),
            "float3": wb.add_format({"num_format": "0.000"}),
        }
        self.title = wb.add_format({"bold": True, "font_size": 16, "font_color": "#1F3A5F"})
        self.section = wb.add_format({"bold": True, "font_size": 12, "font_color": "#1F3A5F", "bottom": 1})
        self.key = wb.add_format({"bold": True, "valign": "top"})
        self.wrap = wb.add_format({"text_wrap": True, "valign": "top"})
        self.plain = wb.add_format({"valign": "top"})


def _write_about(wb, fmt: _Formats, result: ExportResult, pages):
    ws = wb.add_worksheet(S.ABOUT)
    ws.ignore_errors({"number_stored_as_text": "A1:XFD1048576"})   # e.g. the schema version
    ws.set_column(0, 0, 30)
    ws.set_column(1, 1, 22)
    ws.set_column(2, 2, 12)
    ws.set_column(3, 3, 90)
    r = 0
    ws.write_string(r, 0, "Movie Library Export", fmt.title)
    r += 2
    for key, value in result.info:
        ws.write_string(r, 0, key, fmt.key)
        if isinstance(value, datetime):
            ws.write_datetime(r, 1, value, fmt.datetime)
        elif isinstance(value, (int, float)):
            ws.write_number(r, 1, value)
        else:
            ws.write_string(r, 1, _fit(str(value or "")))
        r += 1

    r += 1
    ws.write_string(r, 0, "Libraries", fmt.section)
    r += 1
    for c, h in enumerate(["Library", "Library ID", "Movies", "Folders"]):
        ws.write_string(r, c, h, fmt.header)
    r += 1
    for lib in result.libraries:
        ws.write_string(r, 0, lib.name)
        ws.write_number(r, 1, lib.id)
        ws.write_number(r, 2, lib.movie_count)
        ws.write_string(r, 3, _fit(S.LIST_SEP.join(lib.folders)))
        r += 1

    r += 1
    ws.write_string(r, 0, "Sheets", fmt.section)
    r += 1
    for c, h in enumerate(["Sheet", "Rows", "", "What it holds"]):
        ws.write_string(r, c, h, fmt.header)
    r += 1
    for title, sheet, rows in pages:
        ws.write_string(r, 0, title)
        ws.write_number(r, 1, len(rows))
        ws.write_string(r, 3, sheet.desc if title == sheet.name else
                        f"Continuation of {sheet.name} - more rows than fit on one Excel sheet.")
        r += 1

    r += 1
    ws.write_string(r, 0, "Column dictionary", fmt.section)
    r += 1
    for c, h in enumerate(["Sheet", "Column", "Type", "Meaning"]):
        ws.write_string(r, c, h, fmt.header)
    r += 1
    kinds = {"text": "Text", "int": "Number", "float1": "Number", "float2": "Number", "float3": "Number",
             "date": "Date", "datetime": "Date & time", "url": "Link"}
    for sheet in result.sheets:
        for col in sheet.columns:
            ws.write_string(r, 0, sheet.name, fmt.plain)
            ws.write_string(r, 1, col.header, fmt.key)
            ws.write_string(r, 2, kinds.get(col.kind, col.kind), fmt.plain)
            ws.write_string(r, 3, col.desc, fmt.wrap)
            r += 1


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------
def csv_folder_for(xlsx_path: str) -> str:
    base, _ = os.path.splitext(os.path.abspath(xlsx_path))
    return base + " (CSV)"


def _csv_value(value, kind):
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float):
        return repr(value)   # full precision - rounding for display is only the workbook's number format
    return value


def _known_csv_files(folder: str) -> list[str]:
    """Paths of every CSV this app writes, whether or not it exists yet."""
    return [os.path.join(folder, f"{name}.csv") for name in S.SHEET_COLUMNS]


def check_csv_writable(folder: str):
    """Fail before the export starts if a CSV from last time is open in another program."""
    for path in _known_csv_files(folder):
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r+b"):
                pass
            locked = _locked_against_replace(path)
        except PermissionError:
            locked = True
        if locked:
            raise OutputError(f"{os.path.basename(path)} in\n{folder}\nis open in another program (or read-only).\n\n"
                              "Close it and try again, or untick 'Also save every sheet as a CSV file'.")


def write_csv(result: ExportResult, folder: str, progress: Progress | None = None, cancel=None) -> str:
    """One UTF-8 CSV per sheet (with a BOM so Excel detects the encoding). Returns the folder.

    CSVs of sheets not in this export (left from an earlier run with more sheets ticked) are removed,
    so the folder never mixes tables from different exports.
    """
    progress = progress or (lambda fraction, message: None)
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError as exc:
        raise OutputError(f"Couldn't create the CSV folder {folder}: {exc.strerror or exc}") from None
    check_csv_writable(folder)
    current = {os.path.normcase(os.path.join(folder, f"{s.name}.csv")) for s in result.sheets}
    for path in _known_csv_files(folder):
        if os.path.normcase(path) not in current and os.path.isfile(path):
            try:
                os.remove(path)
            except OSError as exc:
                raise OutputError(f"Couldn't remove the old {os.path.basename(path)}: {exc.strerror or exc}") from None
    total = len(result.sheets)
    for i, sheet in enumerate(result.sheets):
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        progress(i / total, f"Writing {sheet.name}.csv...")
        path = os.path.join(folder, f"{sheet.name}.csv")
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow([c.header for c in sheet.columns])
                for row in sheet.rows:
                    w.writerow([_csv_value(row.get(c.key), c.kind) for c in sheet.columns])
        except PermissionError:
            raise OutputError(f"Couldn't write {path} - it's open in another program or read-only.\n\n"
                              "Close it and try again.") from None
        except OSError as exc:
            raise OutputError(f"Couldn't write {path}: {exc.strerror or exc}") from None
    progress(1.0, "CSV files written.")
    return folder
