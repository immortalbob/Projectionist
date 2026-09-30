"""What several test modules share (not a test module itself):

  budget(seconds)    how long a timed step may take here: the limit set on the owner's PC, times how much slower
                     this computer runs Python just now (never less than the limit itself)
  may_show_windows() whether a test may put a window on screen: only on a virtual X display (Xvfb, as xvfb-run
                     starts it) or when PROJECTIONIST_TEST_WINDOWS=1 says so - never on someone's desktop
  text_factor(root)  how much wider the app's writing is here than on the owner's PC (Windows' Segoe UI at 96
                     dpi), measured in the fonts this computer has (1.0 at least): the sizes the tests draw charts
                     and lay windows out at were chosen there, so a test grows them by this much

Every test here builds its own small database or catalog. Tests that read a real Plex backup live in tests/local/,
which stays on the computer it was written on (tools/make_sdist.py leaves it out of the source tarball).
"""

from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# The calibration below, timed on the owner's PC (Python 3.14): the limits in the tests were set there
REFERENCE_SECONDS = 0.0113
_FACTOR: list = []


def _workload():
    """Dicts, strings, sorting and float maths - what the app's backbones spend their time on."""
    counts, text = {}, []
    for i in range(120_000):
        key = i % 997
        counts[key] = counts.get(key, 0) + (i * 7 % 13) / 3.0
        if i % 50 == 0:
            text.append(f"{key:04d}-{i}".casefold())
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return len(ranked) + len(" ".join(text))


def speed_factor(fresh: bool = False) -> float:
    """How many times longer this computer takes than the owner's PC, just now (1.0 at least): the quickest of a
    few runs of a small workload. Measured once (fresh=True: again - a module that times its steps asks just
    before, so a busy moment is allowed for)."""
    if _FACTOR and not fresh:
        return _FACTOR[0]
    best = float("inf")
    for _ in range(5):
        started = time.perf_counter()
        _workload()
        best = min(best, time.perf_counter() - started)
    factor = max(1.0, best / REFERENCE_SECONDS)
    _FACTOR[:] = [factor]
    return factor


# 2000 reads of a 4-KB page at random places in the owner's backup, on the owner's PC (quickest of a few): what a
# SQLite query over it mostly does
REFERENCE_PAGE_READS = 0.0062
_DISK: dict = {}


def disk_factor(path: str) -> float:
    """How many times longer reading the database at `path` takes here than the owner's backup on the owner's PC
    (1.0 at least) - a network drive, or Windows' disk seen from WSL (/mnt/c), is many times slower."""
    if path in _DISK:
        return _DISK[path]
    import random
    try:
        size = os.path.getsize(path)
        rng = random.Random(7)
        offsets = [rng.randrange(0, max(size - 4096, 1)) // 4096 * 4096 for _ in range(2000)]
        best = float("inf")
        for _ in range(3):
            started = time.perf_counter()
            with open(path, "rb", buffering=0) as f:
                for at in offsets:
                    f.seek(at)
                    f.read(4096)
            best = min(best, time.perf_counter() - started)
        factor = max(1.0, best / REFERENCE_PAGE_READS)
    except OSError:
        factor = 1.0
    _DISK[path] = factor
    return factor


def budget(seconds: float, reads: str | None = None) -> float:
    """A time limit set on the owner's PC, for this computer as it is now - and, for a step that reads the database
    at `reads`, for the disk it's on."""
    factor = speed_factor()
    if reads:
        factor = max(factor, disk_factor(reads))
    return seconds * factor


# A line of text and its width in pixels in the app's fonts on the owner's PC (Segoe UI, 96 dpi), by (size, style)
SAMPLE = "The quick brown fox jumps over the lazy dog - 0123456789"
SEGOE_WIDTHS = {(8,): 300, (9,): 308, (9, "bold"): 331, (10, "bold"): 363}


def text_factor(where) -> float:
    """SAMPLE's width in the app's own fonts here over its width on the owner's PC - the widest of the sizes the
    charts and pages use - 1.0 at least. Everything counts: the font the computer has, and its dpi (the tests'
    canvas sizes are in pixels)."""
    from projectionist.ui import theme as T
    root = where._root()
    cached = root.__dict__.get("_test_text_factor")
    if cached is None:
        ratios = [float(root.tk.call("font", "measure", T.font(root, *spec), SAMPLE)) / width
                  for spec, width in SEGOE_WIDTHS.items()]
        cached = root.__dict__["_test_text_factor"] = max([1.0] + ratios)
    return cached


def wider(where, pixels: float) -> int:
    """pixels (a size chosen on the owner's PC) grown by text_factor, for the fonts here."""
    return int(round(pixels * text_factor(where)))


def safe_extract(tar, folder: str):
    """tar.extractall with Python's safe 'data' filter - which came with Python 3.10.12 (and 3.11.4, 3.12): on a
    Python before that, the same checks by hand (no absolute paths, no '..', only files and folders)."""
    import tarfile
    if hasattr(tarfile, "data_filter"):
        tar.extractall(folder, filter="data")
        return
    for member in tar.getmembers():
        parts = member.name.replace("\\", "/").split("/")
        if member.name.startswith(("/", "\\")) or ".." in parts or not (member.isfile() or member.isdir()):
            raise tarfile.TarError(f"not safe to unpack: {member.name}")
    tar.extractall(folder)


def may_show_windows() -> bool:
    """Whether windows may be mapped: a virtual display only (xvfb-run's XAUTHORITY is in its own xvfb-run.*
    folder), or PROJECTIONIST_TEST_WINDOWS=1. Never on Windows or a Mac, whose screens are someone's."""
    if os.environ.get("PROJECTIONIST_TEST_WINDOWS") == "1":
        return True
    if sys.platform in ("win32", "darwin"):
        return False
    return "xvfb-run" in os.environ.get("XAUTHORITY", "") and bool(os.environ.get("DISPLAY"))
