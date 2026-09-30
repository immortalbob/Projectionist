"""Projectionist's icon on its windows, and a taskbar button of its own on Windows.

The pictures are in projectionist/assets, drawn by tools/make_icon.py (only that tool needs Pillow): an .ico with
every size Windows asks for, and PNGs for Tk's iconphoto. They're found beside this file, so they come along
wherever the projectionist folder goes (the app's own folder, an unpacked source tarball, an installed copy).

Nothing here ever fails: a window without its icon is still a window.
"""

from __future__ import annotations

import os
import sys

ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
ICO = os.path.join(ASSETS, "projectionist.ico")
PNG_SIZES = (256, 48, 32, 16)              # biggest first, as iconphoto likes them
APP_ID = "Projectionist.App"               # Windows' name for the app (its AppUserModelID)


def png(size: int) -> str:
    """The PNG of the icon at a size (one of PNG_SIZES)."""
    return os.path.join(ASSETS, f"projectionist-{size}.png")


def set_app_id(app_id: str = APP_ID) -> bool:
    """Tell Windows this process is Projectionist rather than Python, so its window gets a taskbar button of its own
    with the app's icon - instead of being put with Python's, under Python's icon. Called before the first window
    is made. -> whether Windows took it (False anywhere else)."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        set_id = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
        set_id.argtypes = [ctypes.c_wchar_p]
        set_id.restype = ctypes.c_long                                  # (an HRESULT: 0 is S_OK)
        return set_id(app_id) == 0
    except Exception:
        return False


def apply(window, shown: bool = False) -> bool:
    """Put the icon on a Tk window, and make it every window's made after it (tk.Toplevel, and the dialogs): the
    PNGs through iconphoto everywhere, then on Windows the .ico, which wins there. -> whether the window took any.

    Call it again with shown=True once the window is on screen. On Windows, Tk can only give a window that has
    never been shown the app-wide icon, and there only the big (32 px) one: Windows shrinks that for the title
    bar. Once it's shown, the window's own icon is set too - the .ico's 16 px one for the title bar, its 32 px
    one for the taskbar and Alt+Tab - and every later window's small icon as well."""
    import tkinter as tk
    took = False
    images = []
    for size in PNG_SIZES:
        path = png(size)
        if os.path.isfile(path):
            try:
                images.append(tk.PhotoImage(master=window, file=path))
            except Exception:                      # (a picture Tk can't read, a window that's gone...)
                pass
    if images:
        try:
            window.iconphoto(True, *images)   # (Tk copies the pictures: they can go when this returns)
            took = True
        except Exception:
            pass
    if sys.platform == "win32" and os.path.isfile(ICO):
        for how in ({"default": ICO}, {"bitmap": ICO}) if shown else ({"default": ICO},):
            try:
                window.iconbitmap(**how)
                took = True
            except Exception:
                pass
    return took
