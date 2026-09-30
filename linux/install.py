"""Puts Projectionist in your desktop's applications menu (Linux, and other desktops that follow freedesktop.org):
for you only, nothing system-wide, no root needed.

    python3 linux/install.py            add it (again, after moving the app's folder)
    python3 linux/install.py --remove   take it off again
    python3 linux/install.py --print    show the menu entry it would write, and write nothing

It writes projectionist.desktop - this folder's, with the two lines that can't be known in advance filled in: the
Python running this script and where Projectionist.pyw is (Exec), and the app's icon (Icon) - into
$XDG_DATA_HOME/applications (~/.local/share/applications unless your desktop says otherwise), and the icon into
the hicolor icon theme beside it. The window's class is Projectionist (StartupWMClass), so the dock or taskbar shows
the app's own name and icon while it runs.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(HERE)
TEMPLATE = os.path.join(HERE, "projectionist.desktop")
LAUNCHER = os.path.join(APP_DIR, "Projectionist.pyw")
ASSETS = os.path.join(APP_DIR, "projectionist", "assets")
ICON_SIZES = (16, 32, 48, 256)
ENTRY_NAME = "projectionist.desktop"
ICON_NAME = "projectionist"


def data_home(environ=None) -> str:
    """$XDG_DATA_HOME, or ~/.local/share (a relative one is ignored, as the standard says)."""
    environ = os.environ if environ is None else environ
    home = environ.get("HOME") or os.path.expanduser("~")
    folder = environ.get("XDG_DATA_HOME") or ""
    return folder if folder.startswith("/") else os.path.join(home, ".local", "share")


def quote(argument: str) -> str:
    """One argument of a desktop entry's Exec line, as the Desktop Entry Specification has it: in double quotes
    when it has a reserved character, with ", `, $ and \\ escaped inside; % written %%. (The line as a whole is
    then a string value, whose own backslashes are doubled: exec_line.)"""
    argument = argument.replace("%", "%%")
    if argument and not any(c in argument for c in ' \t\n"\'\\><~|&;$*?#()`'):
        return argument
    return '"' + "".join("\\" + c if c in '"`$\\' else c for c in argument) + '"'


def exec_line(*arguments: str) -> str:
    """The Exec key's value for a command: each argument quoted, and backslashes doubled for the file."""
    return " ".join(quote(a) for a in arguments).replace("\\", "\\\\")


def entry(python: str = sys.executable, launcher: str = LAUNCHER, icon: str = ICON_NAME,
          template: str = TEMPLATE) -> str:
    """The menu entry: the template, its Exec and Icon lines filled in."""
    with open(template, encoding="utf-8") as f:
        lines = f.read().splitlines()
    out = []
    for line in lines:
        if line.startswith("Exec="):
            line = "Exec=" + exec_line(python, launcher)
        elif line.startswith("Icon="):
            line = "Icon=" + icon
        elif line.startswith("#"):
            continue                       # (the template's note on the two lines above)
        out.append(line)
    return "\n".join(out) + "\n"


def places(environ=None) -> dict:
    """Where install() writes: {'entry': path, 'icons': {size: path}}."""
    share = data_home(environ)
    return {"entry": os.path.join(share, "applications", ENTRY_NAME),
            "icons": {size: os.path.join(share, "icons", "hicolor", f"{size}x{size}", "apps", ICON_NAME + ".png")
                      for size in ICON_SIZES}}


def _refresh(share: str):
    """Tell the desktop the menu and the icons changed, where it has the tools (it notices by itself anyway). Each
    cache is only brought up to date where there's one already, never made here. An icon cache made here would hide
    the icons other apps put in the same folder later without updating it (xdg-utils leaves it alone for that
    reason). The menu's mimeinfo.cache only lists which apps open which file types, and this entry opens none, so
    a new one would only be left behind after --remove."""
    commands = []
    applications = os.path.join(share, "applications")
    if os.path.isfile(os.path.join(applications, "mimeinfo.cache")):
        commands.append(["update-desktop-database", "-q", applications])
    icons = os.path.join(share, "icons", "hicolor")
    if os.path.isfile(os.path.join(icons, "icon-theme.cache")):
        commands.append(["gtk-update-icon-cache", "-q", "-t", icons])
    for command in commands:
        if shutil.which(command[0]):
            try:
                subprocess.run(command, timeout=20, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, check=False)
            except (OSError, subprocess.SubprocessError):
                pass


def install(environ=None, python: str = sys.executable, refresh: bool = True) -> list[str]:
    """Write the menu entry and the icons. -> the files written."""
    where = places(environ)
    written = []
    for size, path in where["icons"].items():
        source = os.path.join(ASSETS, f"projectionist-{size}.png")
        if os.path.isfile(source):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            shutil.copyfile(source, path)
            written.append(path)
    # the icon by its theme name when it went into the theme, else by its full path
    icon = ICON_NAME if written else os.path.join(ASSETS, "projectionist-256.png")
    os.makedirs(os.path.dirname(where["entry"]), exist_ok=True)
    tmp = where["entry"] + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(entry(python, LAUNCHER, icon))
    os.chmod(tmp, 0o644)
    os.replace(tmp, where["entry"])
    written.append(where["entry"])
    if refresh:
        _refresh(data_home(environ))
    return written


def remove(environ=None, refresh: bool = True) -> list[str]:
    """Take the menu entry and the icons off again. -> the files removed."""
    where = places(environ)
    removed = []
    for path in [where["entry"]] + list(where["icons"].values()):
        if os.path.isfile(path):
            os.remove(path)
            removed.append(path)
    if refresh and removed:
        _refresh(data_home(environ))
    return removed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python3 linux/install.py",
                                     description="Put Projectionist in your applications menu (for you only).")
    parser.add_argument("--remove", action="store_true", help="take it off the menu again")
    parser.add_argument("--print", action="store_true", help="show the menu entry, and write nothing")
    args = parser.parse_args(argv)
    if sys.platform == "win32":
        print("This is for Linux desktops. On Windows, double-click Projectionist.pyw.", file=sys.stderr)
        return 2
    if args.print:
        print(entry(), end="")
        return 0
    if args.remove:
        removed = remove()
        print("Removed:\n  " + "\n  ".join(removed) if removed else "Projectionist wasn't in the menu.")
        return 0
    if not os.path.isfile(LAUNCHER):
        print(f"Projectionist.pyw isn't in {APP_DIR} - run this from the unpacked app's folder.", file=sys.stderr)
        return 1
    for path in install():
        print(f"Wrote {path}")
    print("Projectionist is in your applications menu. Moved the app's folder? Run this again.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
