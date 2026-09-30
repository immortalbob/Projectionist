"""What Projectionist needs that may not be installed - tkinter (on Linux a package of its own) and XlsxWriter - and
what to tell someone who hasn't got it. Imports neither, so it works whichever is missing."""

from __future__ import annotations

import sys

TK_MODULES = ("tkinter", "_tkinter")


def _python(python: str) -> str:
    if python.lower().endswith("pythonw.exe"):
        python = python[:-len("pythonw.exe")] + "python.exe"
    return python


def tkinter_missing(python: str = sys.executable, platform: str = sys.platform) -> str:
    """What to say when tkinter (the window's toolkit) isn't there: how to install it, on this system."""
    start = f"Projectionist needs tkinter, which isn't installed for this copy of Python ({_python(python)}).\n\n"
    if platform == "win32":
        return start + ("Run the Python installer again, choose Modify, tick 'tcl/tk and IDLE', "
                        "then start the app again.")
    if platform == "darwin":
        return start + ("Python from python.org comes with it. With Homebrew's Python, open Terminal and run:\n\n"
                        "    brew install python-tk\n\nthen start the app again.")
    return start + ("Install it from a terminal - it's your distribution's package:\n\n"
                    "    Debian, Ubuntu, Mint:  sudo apt install python3-tk\n"
                    "    Fedora:  sudo dnf install python3-tkinter\n"
                    "    Arch:  sudo pacman -S tk\n"
                    "    openSUSE:  sudo zypper install python3-tk\n\n"
                    "then start the app again.")


def is_tkinter_missing(exc: BaseException) -> bool:
    """Whether an import failed for want of tkinter."""
    return isinstance(exc, ImportError) and getattr(exc, "name", None) in TK_MODULES


def xlsxwriter_missing(python: str, platform: str = sys.platform) -> str:
    """What the box says when XlsxWriter isn't installed: how to install it, on this system."""
    python = _python(python)
    start = ("This app needs the free XlsxWriter package, which isn't installed for this copy of "
             f"Python ({python}).\n\n")
    if platform == "win32":
        return start + ("Install it by opening a Command Prompt and running:\n\n"
                        f'    "{python}" -m pip install XlsxWriter\n\nthen start the app again.')
    if platform == "darwin":
        return start + ("Install it by opening Terminal and running:\n\n"
                        f'    "{python}" -m pip install XlsxWriter\n\nthen start the app again.')
    return start + ("Install it from a terminal - your distribution's package is the easiest:\n\n"
                    "    Debian, Ubuntu, Mint:  sudo apt install python3-xlsxwriter\n"
                    "    Fedora:  sudo dnf install python3-xlsxwriter\n"
                    "    Arch:  sudo pacman -S python-xlsxwriter\n\n"
                    "(or with pip, in a virtual environment: README.md, \"On Linux\", says how - newer "
                    "distributions refuse pip install outside one)\n\nthen start the app again.")
