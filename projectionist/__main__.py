"""Projectionist's command line:  python -m projectionist [DATABASE] [options]

With no arguments the desktop window opens. With a database path the export runs
straight from the command line - handy for scheduled jobs and scripts.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from . import APP_NAME, __version__
from . import sheets as S


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    for stream in (sys.stdout, sys.stderr):
        # Output redirected to a file uses the ANSI code page on Windows; a library or folder name outside
        # it (Japanese, Cyrillic...) must not crash a scheduled export.
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")
    if not argv:
        return _window()

    parser = argparse.ArgumentParser(
        prog="python -m projectionist",
        description=f"{APP_NAME}: export every movie in a Plex library database to an .xlsx spreadsheet "
                    "(opens in LibreOffice Calc or Excel).")
    parser.add_argument("database", nargs="?",
                        help="Plex library database file, or a folder (its newest database is used)")
    parser.add_argument("-o", "--output", help=f"spreadsheet to write (default: '{APP_NAME} Movies <date>.xlsx' "
                                               "next to the database)")
    parser.add_argument("-l", "--library", action="append", metavar="NAME_OR_ID",
                        help="only export this library (repeat for more; default: every movie library)")
    parser.add_argument("-s", "--sheets", metavar="LIST",
                        help="comma-separated extra sheets to include, 'all' (default) or 'none'. "
                             f"Choices: {', '.join(S.DETAIL_SHEET_NAMES)}")
    parser.add_argument("--csv", action="store_true", help="also write each sheet as a CSV file")
    parser.add_argument("--list-libraries", action="store_true", help="list the movie libraries and exit")
    parser.add_argument("--gui", action="store_true", help="open the desktop window with this database")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    args = parser.parse_args(argv)

    from .files import default_output_name, default_output_path, find_newest_database

    db = args.database
    if db and '"' in db:
        parser.error(f"the database path contains a quote: {db}\n"
                     "(a folder typed as \"D:\\Dumps\\\" - with a backslash before the closing quote - "
                     "swallows the rest of the command line; leave the trailing backslash off)")
    if db and os.path.isdir(db):
        found = find_newest_database(db)
        if not found:
            parser.error(f"no Plex database found in {db}")
        db = found
    if args.gui:
        return _window(db)
    if not db:
        parser.error("a database path is required (or run with no arguments for the window)")

    from .export import (OutputError, check_csv_writable, check_writable, csv_folder_for, invalid_file_name,
                         write_csv, write_xlsx)
    from .extract import PlexDBError, extract, list_movie_libraries

    try:
        libraries = list_movie_libraries(db)
    except PlexDBError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.list_libraries:
        for lib in libraries:
            print(f"{lib.id:>4}  {lib.name}  ({lib.movie_count:,} movies)")
        return 0
    try:
        import xlsxwriter  # noqa: F401  (the spreadsheet needs it: say so now, not in a traceback after the reading)
    except ImportError:
        from .needs import xlsxwriter_missing
        print(f"error: {xlsxwriter_missing(sys.executable)}", file=sys.stderr)
        return 1

    library_ids = None
    if args.library:
        wanted = {w.casefold() for w in args.library}
        library_ids = [lib.id for lib in libraries if lib.name.casefold() in wanted or str(lib.id) in wanted]
        unknown = wanted - {lib.name.casefold() for lib in libraries} - {str(lib.id) for lib in libraries}
        if unknown:
            print(f"error: unknown library: {', '.join(sorted(unknown))}", file=sys.stderr)
            return 2

    sheets = None
    if args.sheets:
        choice = args.sheets.strip().lower()
        if choice == "none":
            sheets = []
        elif choice != "all":
            by_name = {n.lower().replace(" ", ""): n for n in S.DETAIL_SHEET_NAMES}
            sheets = []
            for name in args.sheets.split(","):
                key = name.strip().lower().replace(" ", "")
                if key not in by_name:
                    print(f"error: unknown sheet '{name.strip()}'", file=sys.stderr)
                    return 2
                sheets.append(by_name[key])

    out = args.output
    if out and '"' in out:
        # A quote can't be part of a Windows path. It appears when a folder is typed as "D:\Exports\": the
        # backslash escapes the closing quote, and the rest of the command line is glued onto -o.
        print(f"error: the -o value contains a quote: {out}\n"
              "On Windows, a trailing backslash before the closing quote (\"D:\\Exports\\\") swallows the rest of "
              "the command line. Leave the trailing backslash off: -o \"D:\\Exports\"", file=sys.stderr)
        return 2
    if not out:
        out = default_output_path(db)
    elif os.path.isdir(out) or out.endswith(("/", "\\")):
        out = os.path.join(out, default_output_name(db))   # '-o some\folder\' means "save in there"
    out = os.path.abspath(out)
    if not out.lower().endswith(".xlsx"):
        out += ".xlsx"
    # Check any folders that would have to be created before creating them (so an unusable name,
    # e.g. one ending in a dot, isn't half-created and left behind).
    folder, missing = os.path.dirname(out), []
    while folder and not os.path.exists(folder) and os.path.dirname(folder) != folder:
        missing.append(os.path.basename(folder))
        folder = os.path.dirname(folder)
    for part in missing:
        problem = invalid_file_name(part)
        if problem:
            print(f"error: can't create the folder '{part}' - {problem}.", file=sys.stderr)
            return 2
    try:
        os.makedirs(os.path.dirname(out), exist_ok=True)
        check_writable(out)   # fail now, not after the extraction
        if args.csv:
            check_csv_writable(csv_folder_for(out))
    except OutputError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"error: can't create the folder {os.path.dirname(out)}: {exc.strerror or exc}", file=sys.stderr)
        return 2

    started = time.perf_counter()
    last = [""]

    def progress(fraction, message):
        if message != last[0]:
            last[0] = message
            print(f"  {message}", file=sys.stderr)

    try:
        result = extract(db, library_ids, sheets, progress=progress)
        write_xlsx(result, out, progress=lambda f, m: progress(f, m) if m.startswith(("Wrote", "Saving")) else None)
        print(f"Saved {out}")
        if args.csv:
            print(f"Saved CSV files in {write_csv(result, csv_folder_for(out))}")
    except (PlexDBError, OutputError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:   # anything else the file system throws - still a one-line error, not a traceback
        print(f"error: {exc.strerror or exc}", file=sys.stderr)
        return 2
    rows = ", ".join(f"{s.name} {len(s.rows):,}" for s in result.sheets)
    print(f"{result.movie_count:,} movies in {time.perf_counter() - started:.1f}s  ({rows})")
    return 0


def _window(db: str | None = None) -> int:
    """The desktop window - or, where this Python has no tkinter (on Linux it's a package of its own), what to
    install, in the terminal."""
    from .needs import is_tkinter_missing, tkinter_missing
    try:
        from .gui import main as gui_main
    except ImportError as exc:
        if not is_tkinter_missing(exc):
            raise
        print(tkinter_missing(), file=sys.stderr)
        return 1
    return gui_main(db) if db else gui_main()


if __name__ == "__main__":
    sys.exit(main())
