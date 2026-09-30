"""How Projectionist.exe - the Windows build (tools/build_exe.py) - starts:

    Projectionist.exe                          the window
    Projectionist.exe DATABASE                 the window, with that Plex database open
    Projectionist.exe --version                prints "Projectionist <version>" (to a console or a pipe, if any)
    Projectionist.exe --self-test REPORT [--db DATABASE] [--no-export]
                                               checks the build with nothing on screen and writes what it found
                                               to REPORT (projectionist.selftest)

The .exe has no console, so a startup failure is shown in a message box rather than lost - except in a
self-test, which never shows anything: its failures go in its report.
"""

from __future__ import annotations

import sys
import traceback

from . import APP_NAME, __version__

USAGE = f"""{APP_NAME} {__version__}

  {APP_NAME}.exe                     open the window
  {APP_NAME}.exe DATABASE            open the window with that Plex database
  {APP_NAME}.exe --version           print the version
  {APP_NAME}.exe --self-test REPORT [--db DATABASE] [--no-export]
                                     check the app with nothing on screen; write what was found to REPORT
"""


def _say(text: str):
    """Print, when there's somewhere to print to (a windowed .exe started from Explorer has nowhere)."""
    if sys.stdout is None:
        return
    try:
        print(text)
        sys.stdout.flush()
    except (OSError, ValueError):
        pass


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    first = argv[0] if argv else ""
    if first in ("--version", "-V"):
        _say(f"{APP_NAME} {__version__}")
        return 0
    if first in ("--help", "-h", "/?"):
        _say(USAGE)
        return 0
    if first == "--self-test":
        from . import selftest
        return selftest.main(argv[1:], prog=f"{APP_NAME}.exe --self-test")
    database = first if first and not first.startswith("-") else None
    return window(database)


def window(database: str | None = None) -> int:
    """The window (with `database` open) - and, if it can't start, a box saying why."""
    try:
        from .gui import main as gui_main
        return gui_main(database) if database else gui_main()
    except SystemExit:
        raise
    except BaseException:                          # noqa: BLE001
        startup_failed(traceback.format_exc())
        return 1


def startup_failed(details: str):
    """What Projectionist.pyw shows when the app can't start: the reason, in a box of the app's own."""
    text = f"The app couldn't start:\n\n{details[-1500:]}"
    try:
        import tkinter
        from tkinter import messagebox

        from . import appicon
        appicon.set_app_id()
        root = tkinter.Tk()
        root.withdraw()
        appicon.apply(root)
        messagebox.showerror(APP_NAME, text)
        root.destroy()
        return
    except Exception:                              # noqa: BLE001  (then Windows' own box)
        pass
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, text, APP_NAME, 0x10)
        except Exception:                          # noqa: BLE001
            pass
