"""The Library Doctor tab: a to-do list from your films' files.

Twelve tiles along the top count what the checks found - films you own twice in the same cut, copies a better
copy makes redundant, oddly large or small files, missing subtitles, soundtracks in languages you don't speak,
films worth upgrading, missing details, disk use - and each opens its list: a sortable table of the films,
with why each one is there, a link to the film's page, and "Save this list" (an .xlsx). Disk use opens the
summary: where the space goes, and every file by size. Your languages (the two language checks) are ticked above
the tiles and remembered. Settings > Library Doctor has them too (as ticks: LanguagesControl), with what an upgrade
candidate is (rated 8 or more, below 1080p - or 4K), where lists are saved and whether a saved list opens.

Everything comes from one request, {"action": "doctor", "languages": [...]} (projectionist.doctor.answer): half a
second the first time - it reads every copy's files and tracks from the database - and instant after that.
"""

from __future__ import annotations

import math
import os
import time
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, ttk

from .. import doctor as DR
from .. import prefs
from . import charts as C
from . import settings as S
from . import theme as T
from .base import BaseTab
from .widgets import Card, ChartView, LinkLabel, ScrollFrame, Table


def _language_names(codes) -> str:
    return ", ".join(DR.lang_name(c) for c in codes)


# This tab's settings (Settings > Library Doctor). The ticks above the tiles change the languages too; the upgrade
# check and your languages are shared with the Film page's issues through the collection (see _share_settings).
RATINGS = [(10, "10 only"), (9, "9 or more"), (8, "8 or more"), (7, "7 or more")]
UPGRADE_TARGETS = [("1080p", "1080p (Full HD)"), ("4K", "4K (Ultra HD)")]
prefs.section("Library Doctor", order=50, hint="What the Library Doctor tab checks your files against.")
prefs.define("doctor_languages", "Library Doctor", "Your languages", kind="languages", control="language_ticks",
             default=list(DR.DEFAULT_LANGUAGES), parse=DR.parse_languages, format=_language_names,
             help="Audio and subtitles in these count as ones you understand (the two language checks, and the Film "
                  "page's issues). The languages most used in your files are offered as ticks once the Library "
                  "Doctor has read them; the list has every other language Plex tags.")
prefs.define("doctor_upgrade_rating", "Library Doctor", "Upgrade candidates: films rated", kind="choice",
             default=int(DR.HIGH_RATING), choices=RATINGS,
             help="Which films the Upgrade candidates check looks at: those you rated this highly (out of 10).")
prefs.define("doctor_upgrade_below", "Library Doctor", "Upgrade candidates: best copy below", kind="choice",
             default=DR.UPGRADE_BELOW, choices=UPGRADE_TARGETS,
             help="A film you rated that highly is listed when its best copy is below this. 4K lists the films "
                  "you love that you don't have in 4K yet.")
prefs.define("doctor_save_dir", "Library Doctor", "Save lists in", kind="folder", default="",
             help="Where 'Save this list' starts (saving a list in another folder makes that one the start). "
                  "None chosen: the database's folder.")
prefs.define("doctor_open_saved", "Library Doctor", "Open a list in your spreadsheet program after saving it",
             kind="bool", default=False,
             help="After 'Save this list', open the saved list at once, in the program the Export tab opens "
                  "spreadsheets in.")

PAD = 16                    # page margin
GAP = 12                    # space between cards
TILE_H = 80                 # one row of tiles (device-independent px)
TILE_GAP = 8
TILE_MIN_W = 180
TWO_COLUMNS_FROM = 940      # width of the card area from which the summary's charts sit two to a row
BAR_ROW = 26
TABLE_ROWS = 14
TOP_LANGUAGES = 6           # languages offered as ticks (the rest are in the drop-down)

SEVERITY_COLOR = T.live(lambda: {"fix": T.SERIOUS, "check": T.WARNING, "info": T.BLUE})   # (the look in use)
SEVERITY_WORDS = {"fix": "to fix", "check": "to check", "info": "for info"}
NUMERIC = {"int", "gb", "mbps", "ratio", "rating", "score", "minutes"}
# Columns: numbers and short words keep a fixed width; the text columns share what's left by weight (so every
# column shows at any window width - the Why and File columns give way most, as the details below show them whole)
KIND_WIDTH = {"int": 62, "gb": 74, "mbps": 74, "ratio": 88, "rating": 86, "score": 62, "minutes": 70,
              "date": 92}
FIXED_WIDTH = {"resolution": 82, "codec": 70, "drive": 80, "your_subtitles": 96, "best": 76, "usual_mbps": 96,
               "picture_mbps": 104}
TEXT_WEIGHT = {"film": 3.0, "edition": 2.0, "library": 1.4, "libraries": 1.6, "editions": 3.0, "soundtracks": 3.4,
               "subtitles": 2.0, "better": 2.6, "missing": 2.6, "why": 4.6, "path": 4.6}
TEXT_MIN = {"film": 150, "why": 120, "path": 120}

RESOLUTION_NAMES = {"8K": "8K", "4K": "4K Ultra HD", "1080p": "1080p Full HD", "720p": "720p HD",
                    "576p": "576p (PAL DVD)", "480p": "480p (NTSC DVD)", "SD": "Standard definition"}
DISK_CHARTS = [("library", "by_library", "Space by library", "Each of your Plex movie libraries."),
               ("resolution", "by_resolution", "Space by resolution", "Every copy counts, from 4K down."),
               ("drive", "by_drive", "Space by drive",
                "The top folder of each file's path, as the Plex server sees it."),
               ("codec", "by_codec", "Space by video codec", "How the pictures are compressed.")]
DISK_FILTER_WORDS = {"library": "in", "resolution": "at", "drive": "on", "codec": "in"}


# ---------------------------------------------------------------------------------------------------------
# Words and numbers
# ---------------------------------------------------------------------------------------------------------
def cell(kind: str, value):
    """How a value shows in a table cell (numbers stay sortable as numbers: '9.43', '1,234')."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if kind in NUMERIC and isinstance(value, (int, float)):
        if kind == "gb":
            return f"{value:.1f}" if value >= 10 else f"{value:.2f}"
        if kind in ("mbps", "score"):
            return f"{value:.1f}"
        if kind == "ratio":
            return f"{value:.1f}" if value >= 1 else f"{value:.2f}"
        if kind == "rating":
            return f"{value:g}"
        return f"{int(round(value)):,}"
    if isinstance(value, (list, tuple)):
        return ", ".join(map(str, value))
    return str(value)


def space_text(n) -> str:
    """Bytes for the space charts: '5.61 TB', '347 GB', '81.2 GB', '350 MB'."""
    n = n or 0
    if n >= 1e12:
        return f"{n / 1e12:.2f} TB"
    if n >= 1e11:
        return f"{n / 1e9:.0f} GB"
    if n >= 1e9:
        return f"{n / 1e9:.1f} GB"
    return f"{n / 1e6:.0f} MB"


def plural(n: int, one: str, many: str) -> str:
    return f"{n:,} {one if n == 1 else many}"


def _whole(value) -> int | None:
    """An id passed to navigate() (2607 or '2607') as a number; None for none or nonsense."""
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def row_noun(issue: str) -> tuple[str, str]:
    return DR.ROW_NOUNS.get(issue, ("copy", "copies"))


def empty_text(issue: str, answer: dict) -> str:
    codes = [x["code"] for x in answer.get("languages", [])]
    settings = answer.get("settings", {})
    rated = DR.rated_words(settings.get("min_rating", DR.HIGH_RATING))
    below = settings.get("upgrade_below", DR.UPGRADE_BELOW)
    return {
        "duplicates": "Nothing to do - no film is in your library twice in the same cut.",
        "weaker": "Nothing to do - no copy is beaten by another copy of the same cut.",
        "large": "Nothing to do - no file takes three times the usual space or more.",
        "small": "Nothing to do - no file takes under a third of the usual space.",
        "unavailable": "Nothing to do - Plex found every file.",
        "editions": "No film is kept in more than one edition.",
        "subtitles": f"Nothing to do - every copy in another language has {DR.subtitle_words(codes)}.",
        "audio": f"Every copy has a soundtrack in {DR.languages_words(codes, many='one of your languages')}.",
        "unknown_audio": "Nothing to do - every soundtrack has a language tag.",
        "upgrade": f"Nothing to do - every film you rated {rated} has a copy in {below} or better.",
        "metadata": "Nothing to do - every film is matched in Plex and has a year, genres and an IMDb rating.",
        "disk": "There are no files to show.",
    }.get(issue, "Nothing to show.")


def tile_items(answer: dict) -> list[dict]:
    """The tiles, from the answer's summary."""
    items = []
    for s in answer.get("summary", []):
        zero = not s["count"] and s["severity"] != "info"
        tip = s["explanation"] + ("\nClick for where the space goes, and every file by size." if s["id"] == "disk"
                                  else "\nClick to see the list.")
        items.append({"id": s["id"], "label": s["title"], "value": s["value"], "note": s["note"],
                      "severity": s["severity"], "zero": zero, "tip": f"{s['title']}\n{tip}"})
    return items


def tiles_per_row(width: float, count: int, s: float) -> int:
    """As many tiles to a row as fit at their narrowest - 12, 6, 4, 3, 2 or 1, so the rows come out even."""
    for per_row in (12, 6, 4, 3, 2, 1):
        if per_row > count:
            continue
        if (width - TILE_GAP * s * (per_row - 1)) / per_row >= TILE_MIN_W * s:
            return per_row
    return 1


def tiles_height(width: float, count: int, s: float) -> int:
    rows = math.ceil(count / tiles_per_row(width, count, s)) if count else 1
    return int(rows * TILE_H * s + (rows - 1) * TILE_GAP * s)


def draw_tiles(p, items, selected=None, on_click=None):
    """The issue tiles: a white card each with a status stripe (to fix / to check / for info - said in words
    too), the count, and a note; the open list's tile is shaded, in a ring, and its small print is darker (MUTED
    would fade into the shading). Hover for what the check looks for; click to open it."""
    if not items:
        return
    n = len(items)
    gap = p.u(TILE_GAP)
    per_row = tiles_per_row(p.width, n, p.s)
    rows = math.ceil(n / per_row)
    tile_w = (p.width - gap * (per_row - 1)) / per_row
    tile_h = (p.height - gap * (rows - 1)) / rows
    fl, fv, fn, fs = p.font(9, "bold"), p.font(17, "bold"), p.font(8), p.font(8)
    for i, it in enumerate(items):
        tx = (i % per_row) * (tile_w + gap)
        ty = (i // per_row) * (tile_h + gap)
        tag = f"tile{i}"
        chosen = it["id"] == selected
        p.round_rect(tx, ty, tx + tile_w - 1, ty + tile_h - 1, p.u(6), T.SELECT if chosen else T.CARD,
                     outline=T.RAMP[350] if chosen else T.BORDER, tag=tag + " hit")
        small = T.INK_2 if chosen else T.MUTED
        color = T.GOOD if it.get("zero") else SEVERITY_COLOR.get(it["severity"], T.BLUE)
        p.round_rect(tx, ty, tx + p.u(4), ty + tile_h - 1, p.u(4), color, corners=(True, False, False, True),
                     tag=tag)
        left = tx + p.u(14)
        room = tile_w - p.u(14) - p.u(8)
        top = ty + p.u(9)
        lh = p.line_height(fl)
        p.text(left, top + lh / 2, p.fit(it["label"], fl, room), fl, T.INK_2, "w", tag=tag)
        vy = top + lh + p.u(1) + p.line_height(fv) / 2
        value = str(it["value"])
        value_w = p.text_width(value, fv)
        p.text(left, vy, p.fit(value, fv, room), fv, T.GOOD_TEXT if it.get("zero") else T.INK, "w", tag=tag)
        # what kind of item it is, in words (the stripe's colour never says it alone)
        word = "all clear" if it.get("zero") else SEVERITY_WORDS.get(it["severity"], "")
        if word and value_w + p.text_width(word, fs) + p.u(12) <= room:
            p.text(tx + tile_w - p.u(10), vy + p.u(3), word, fs, small, "e", tag=tag)
        if it.get("note"):
            ny = top + lh + p.u(2) + p.line_height(fv) + p.line_height(fn) / 2
            p.text(left, ny, p.fit(it["note"], fn, room), fn, small, "w", tag=tag)
        if it.get("tip"):
            p.tip(tag, it["tip"])
        if on_click is not None:
            p.click(tag, lambda key=it["id"]: on_click(key))


def disk_items(answer: dict, kind: str) -> list[dict]:
    """One space chart's bars: bytes per library / resolution / drive / codec."""
    disk = answer.get("disk", {})
    total = disk.get("total_bytes", 0) or 1
    key = dict((k, field) for k, field, _t, _h in DISK_CHARTS)[kind]
    out = []
    for d in disk.get(key, []):
        name = RESOLUTION_NAMES.get(d["label"], d["label"]) if kind == "resolution" else d["label"]
        share = d["bytes"] / total
        share_words = "under 1%" if 0 < share < 0.005 else f"{share:.0%}"
        out.append({"label": d["label"], "value": d["bytes"], "key": d["label"],
                    "tip": f"{name}\n{space_text(d['bytes'])} in {plural(d['files'], 'file', 'files')} "
                           f"({share_words} of the space)\nClick to list these files"})
    return out


def column_widths(cols, room: float, s: float) -> dict:
    """{key: width} for a table `room` px wide: fixed widths for numbers and short words, the rest shared out
    between the text columns by weight (each at least its minimum)."""
    fixed = {}
    for key, _h, kind in cols:
        if key in FIXED_WIDTH:
            fixed[key] = int(FIXED_WIDTH[key] * s)
        elif kind in KIND_WIDTH:
            fixed[key] = int(KIND_WIDTH[kind] * s)
    text = [key for key, _h, _k in cols if key not in fixed]
    free = max(room - sum(fixed.values()), 0)
    total = sum(TEXT_WEIGHT.get(k, 1.5) for k in text) or 1
    out = dict(fixed)
    for key in text:
        out[key] = max(int(free * TEXT_WEIGHT.get(key, 1.5) / total), int(TEXT_MIN.get(key, 70) * s))
    return out


def bars_height(n: int) -> int:
    return max(n, 3) * BAR_ROW + 8


def initial_name(title: str, source: str) -> str:
    """'Library Doctor - Weaker copies - 2026-09-25.xlsx'."""
    from ..files import dump_date
    safe = "".join(ch for ch in title if ch not in '<>:"/\\|?*').strip()
    when = dump_date(source) if source and os.path.isfile(source) else datetime.now().strftime("%Y-%m-%d")
    return f"Library Doctor - {safe} - {when}.xlsx"


def backup_text(catalog) -> str:
    """Which database this is, as the Export and Overview tabs say it: 'Plex backup from 2026-09-25' (or 'Database
    from ...' for a file not named as Plex names its backups); '' with no file."""
    source = getattr(catalog, "source", "") or ""
    if not source or not os.path.isfile(source):
        return ""
    from ..files import dump_date
    when = dump_date(source)
    return f"Plex backup from {when}" if when in os.path.basename(source) else f"Database from {when}"


def doctor_answer(catalog, languages, min_rating=DR.HIGH_RATING, upgrade_below=DR.UPGRADE_BELOW) -> dict:
    """The doctor request (runs on a background thread: reads the database, touches no widgets)."""
    return DR.answer(catalog, {"languages": list(languages), "min_rating": min_rating,
                               "upgrade_below": upgrade_below})


def open_saved(app, path: str) -> str | None:
    """Open a saved list in the spreadsheet program the Export tab uses (Settings > Library Doctor: 'Open a list
    in your spreadsheet program after saving it'). -> why it couldn't be opened, or None."""
    opener = getattr(app, "open_spreadsheet", None)
    try:
        if callable(opener):                   # (the window's own: the program chosen in the settings)
            opener(path)
        else:
            from ..files import open_spreadsheet
            label = prefs.get(app, "open_with") if "open_with" in prefs.PREFS else ""
            open_spreadsheet(path, dict(getattr(app, "apps", None) or []).get(label))
    except OSError as exc:
        return str(exc) or type(exc).__name__
    return None


# ---------------------------------------------------------------------------------------------------------
class ListPanel:
    """A list and its details: the table (click a heading to sort), why the selected row is there, its file,
    a link to the film's page, and "Save this list"."""

    def __init__(self, tab: "Tab", parent, on_save):
        self.tab = tab
        self.issue = None
        self.columns: list[tuple] = []
        self.table: Table | None = None
        self.frame = f = ttk.Frame(parent, style="CardInner.TFrame")
        f.columnconfigure(0, weight=1)
        bar = ttk.Frame(f, style="CardInner.TFrame")
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        bar.columnconfigure(1, weight=1)
        self.count_label = ttk.Label(bar, text="", style="CardNote.TLabel")
        self.count_label.grid(row=0, column=0, sticky="w")
        self.filters = ttk.Frame(bar, style="CardInner.TFrame")
        self.filters.grid(row=0, column=1, sticky="w", padx=(16, 0))
        self.save_btn = ttk.Button(bar, text="Save this list...", command=on_save)
        self.save_btn.grid(row=0, column=2, sticky="e")
        # a save that failed says why right under the button
        self.error = ttk.Label(f, text="", style="DoctorBad.TLabel", justify="left", wraplength=600)
        self.error.grid(row=1, column=0, sticky="w", pady=(0, 6))
        self.error.grid_remove()
        self.table_slot = ttk.Frame(f, style="CardInner.TFrame")
        self.table_slot.grid(row=2, column=0, sticky="nsew")
        self.table_slot.columnconfigure(0, weight=1)
        self._fitted = None
        self.table_slot.bind("<Configure>", lambda e: self.fit_columns(e.width), add="+")
        self.empty = ttk.Label(f, text="", style="DoctorGood.TLabel", justify="left", wraplength=600)
        self.empty.grid(row=3, column=0, sticky="w", pady=(4, 4))
        self.empty.grid_remove()
        self.detail = d = ttk.Frame(f, style="CardInner.TFrame")
        d.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        d.columnconfigure(0, weight=1)
        self.why = ttk.Label(d, text="", style="CardNote.TLabel", justify="left", wraplength=600)
        self.why.grid(row=0, column=0, sticky="w")
        self.path = ttk.Label(d, text="", style="CardSmall.TLabel", justify="left", wraplength=600)
        self.path.grid(row=1, column=0, sticky="w", pady=(2, 0))
        self.open_link = LinkLabel(d, "Open the film page  ›", self.open_film, style="CardLink.TLabel")
        self.open_link.grid(row=2, column=0, sticky="w", pady=(4, 0))
        self.wrapped = [self.empty, self.why, self.path, self.error]
        self._clear_detail()

    # -- the rows -------------------------------------------------------------------------------------------
    def show(self, issue: str, columns: list[dict], rows: list[dict], empty: str, count_text: str):
        cols = [(c["key"], c["heading"], c.get("kind", "text")) for c in columns]
        if issue != self.issue or cols != self.columns or self.table is None:
            self._new_table(cols)
            self.issue = issue
        selected = self.selected()
        shown = [dict({k: cell(kind, r.get(k)) for k, _h, kind in cols}, _row=r) for r in rows]
        # a short list gets a short table (no tall empty box under two rows)
        height = min(max(len(rows), 4), TABLE_ROWS)
        if str(self.table.tree.cget("height")) != str(height):
            self.table.tree.configure(height=height)
        self.table.set_rows(shown, keep_sort=True)
        self.count_label.configure(text=count_text)
        if rows:
            self.empty.grid_remove()
            self.table_slot.grid()
            self.detail.grid()
        else:
            self.table_slot.grid_remove()
            self.detail.grid_remove()
            self.empty.configure(text=empty)
            self.empty.grid()
        # the same row stays selected when the list is worked out again (another language, say)
        if selected is None or not self.select(selected.get("film_key"), selected.get("media_id"),
                                               selected.get("plex_id")):
            self._clear_detail()

    def _new_table(self, cols):
        if self.table is not None:
            self.table.destroy()
        self.columns = cols
        spec = [(k, h, column_widths(cols, 1100, 1.0)[k], "e" if kind in NUMERIC else "w") for k, h, kind in cols]
        self.table = Table(self.table_slot, spec, height=TABLE_ROWS, on_select=self._selected,
                           on_open=lambda row: self.open_film(row))
        self.table.grid(row=0, column=0, sticky="nsew")
        self._fitted = None
        self.fit_columns(self.table_slot.winfo_width())

    def fit_columns(self, width: int):
        """Share the table's width out between its columns (see TEXT_WEIGHT)."""
        if self.table is None or width < 100 or width == self._fitted:
            return
        self._fitted = width
        s = T.scale(self.frame)
        bar = self.table.grid_slaves(row=0, column=1)
        room = width - (bar[0].winfo_reqwidth() if bar else int(17 * s)) - 4
        for key, w in column_widths(self.columns, room, s).items():
            self.table.tree.column(key, width=w)

    def rows_in_order(self) -> list[dict]:
        """The rows as the table shows them now (sorted as the user sorted them)."""
        if self.table is None:
            return []
        return [self.table.rows[iid]["_row"] for iid in self.table.tree.get_children()]

    def selected(self) -> dict | None:
        row = self.table.selected() if self.table is not None else None
        return row["_row"] if row else None

    def select(self, film_key, media_id=None, plex_id=None) -> bool:
        """Select (and scroll to) a film's row - its exact copy when media_id (a copy) or plex_id (a library
        item: one edition) says which, else the film's first row."""
        if self.table is None or not film_key:
            return False
        first = by_item = by_copy = None
        for iid in self.table.tree.get_children():
            row = self.table.rows[iid]["_row"]
            if row.get("film_key") != film_key:
                continue
            if first is None:
                first = iid
            if media_id is not None and row.get("media_id") == media_id:
                by_copy = iid
                break
            if by_item is None and plex_id is not None and row.get("plex_id") == plex_id:
                by_item = iid
        best = by_copy or by_item or first
        if best is None:
            return False
        tree = self.table.tree
        tree.selection_set(best)
        tree.focus(best)
        tree.see(best)
        self._selected(self.table.rows[best])
        return True

    # -- the selected row -------------------------------------------------------------------------------------
    def _selected(self, shown_row):
        row = shown_row.get("_row", shown_row)
        self.why.configure(text=f"Why: {row.get('why', '')}")
        paths = [p for p in (row.get("paths") or ([row["path"]] if row.get("path") else [])) if p]
        if len(paths) > 1:
            self.path.configure(text="Files on the Plex server:  " + "   ·   ".join(paths))
        elif paths:
            self.path.configure(text=f"File on the Plex server:  {paths[0]}")
        else:
            self.path.configure(text="")
        self.open_link.configure(text=f"Open the film page for {row.get('film', 'this film')}  ›")
        self.open_link.grid()

    def _clear_detail(self):
        self.why.configure(text="Select a film to see why it's on the list; double-click it (or press Enter) "
                                "to open its page.")
        self.path.configure(text="")
        self.open_link.grid_remove()

    def open_film(self, shown_row=None):
        row = shown_row.get("_row", shown_row) if shown_row is not None else self.selected()
        if row:
            self.tab.open_film(row)

    # -- saving ---------------------------------------------------------------------------------------------------
    def show_error(self, text: str):
        self.error.configure(text=" ".join(str(text).split()))       # one paragraph
        self.error.grid()

    def clear_error(self):
        self.error.configure(text="")
        self.error.grid_remove()

    def wrap(self, width: int):
        wrap = max(int(width) - 10, 160)
        for label in self.wrapped:
            if str(label.cget("wraplength")) != str(wrap):
                label.configure(wraplength=wrap)


# ---------------------------------------------------------------------------------------------------------
# Your languages on the Settings tab
# ---------------------------------------------------------------------------------------------------------
ADD_LANGUAGE = "Add a language..."
TICKS_PER_ROW = 6


def files_languages(catalog) -> list[dict]:
    """The languages in your files, most tracks first ([{code, name, tracks}]) - once they've been read (by this
    tab or a Film page's issues); [] before, or with no collection."""
    cache = getattr(catalog, "cache", None)
    data = cache.get("doctor.copies") if isinstance(cache, dict) else None
    if not isinstance(data, dict) or "copies" not in data:
        return []
    try:
        return DR.available_languages(data)
    except Exception:                                   # (the Settings tab mustn't break over it)
        return []


def language_choices(chosen, files) -> tuple[list[str], list[str]]:
    """(the languages offered as ticks, the names in the drop-down): ticks for the languages most used in your
    files and the ones chosen (as this tab shows them); the drop-down has every other language Plex tags, those in
    your files first."""
    ticks = [x["code"] for x in files[:TOP_LANGUAGES]]
    ticks += [c for c in chosen if c not in ticks]
    names, seen = [], {DR.lang_name(c) for c in ticks}
    rest = sorted(DR.LANGUAGES, key=lambda c: DR.lang_name(c).casefold())
    for code in [x["code"] for x in files] + rest:
        name = DR.lang_name(code)
        if code not in ticks and name not in seen:
            seen.add(name)
            names.append(name)
    return ticks, names


class LanguagesControl(S.Control):
    """Settings > Library Doctor > Your languages, as the Library Doctor tab shows them: a tick each, and 'Add a
    language...' for the rest. The ticks are worked out again each time the Settings tab is shown (the languages
    in your files are known once the Library Doctor has read them)."""

    def build(self):
        self.chosen: list[str] = []
        self.codes: list[str] = []
        self.vars: dict[str, tk.BooleanVar] = {}
        self.checks: dict[str, ttk.Checkbutton] = {}
        self.ticks = ttk.Frame(self.body, style="CardInner.TFrame")
        self.ticks.grid(row=0, column=0, sticky="w")
        self.add_var = tk.StringVar(self.frame, value=ADD_LANGUAGE)
        self.add_box = ttk.Combobox(self.body, textvariable=self.add_var, state="readonly", width=22, values=[])
        self.add_box.grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.add_box.bind("<<ComboboxSelected>>", lambda e: self._added(), add="+")
        notebook = getattr(self.tab, "notebook", None)          # (the window's tabs: each time it's shown)
        if notebook is not None:
            notebook.bind("<<NotebookTabChanged>>", self._tab_changed, add="+")

    def _tab_changed(self, event=None):
        try:
            if str(self.tab.notebook.select()) == str(self.tab.frame):
                self.refresh()
        except tk.TclError:                                     # (the window closing)
            pass

    def show(self, value):
        self.chosen = list(value or [])
        self.refresh()

    def refresh(self):
        catalog = getattr(self.tab.app, "catalog", None)
        codes, names = language_choices(self.chosen, files_languages(catalog))
        if codes != self.codes:
            for cb in self.checks.values():
                cb.destroy()
            self.vars, self.checks = {}, {}
            for n, code in enumerate(codes):
                var = tk.BooleanVar(self.frame)
                cb = ttk.Checkbutton(self.ticks, text=DR.lang_name(code), variable=var, style="Card.TCheckbutton",
                                     command=lambda c=code: self._ticked(c))
                cb.grid(row=n // TICKS_PER_ROW, column=n % TICKS_PER_ROW, sticky="w", padx=(0, 14))
                self.vars[code], self.checks[code] = var, cb
            self.codes = codes
        for code, var in self.vars.items():
            var.set(code in self.chosen)
        self.add_box.configure(values=names)
        self.add_var.set(ADD_LANGUAGE)
        self.add_box.selection_clear()      # (not the picked name's length of it, highlighted)

    def _ticked(self, code: str):
        ticked = [c for c in self.codes if self.vars[c].get()]
        if not ticked:
            self.vars[code].set(True)
            self.say("Keep at least one language: the language checks need to know what you speak.")
            return
        self._keep([c for c in self.chosen if c in ticked] + [c for c in ticked if c not in self.chosen])

    def _added(self):
        name = self.add_var.get()
        code = next((c for c in DR.LANGUAGES if DR.lang_name(c) == name), None)
        self.add_var.set(ADD_LANGUAGE)
        self.add_box.selection_clear()      # (not the picked name's length of it, highlighted)
        if code is not None and code not in self.chosen:
            self._keep(self.chosen + [code])

    def _keep(self, codes: list[str]):
        if self.save(codes):
            self.show(self.value())


S.add_control("language_ticks", LanguagesControl)


def _styles(style):
    """This tab's own styles (the card styles are the theme's), in the look in use."""
    style.configure("DoctorGood.TLabel", background=T.CARD, foreground=T.GOOD_TEXT)
    style.configure("DoctorBad.TLabel", background=T.CARD, foreground=T.BAD_TEXT)
    style.configure("DoctorStrong.TLabel", background=T.PAGE, foreground=T.INK, font=T.font(style, 9, "bold"))


# ---------------------------------------------------------------------------------------------------------
class Tab(BaseTab):
    title = "Library Doctor"

    def __init__(self, app, notebook):
        super().__init__(app, notebook)
        self.s = T.scale(self.frame)
        self.data: dict | None = None
        self.view = "disk"                      # the open list: an issue id ('disk' is the summary)
        self._filled_for = None                 # the catalog the tab shows
        self._asking_for = None                 # the catalog an answer is being worked out for
        self._token = 0                         # bumped on every catalog change and ask: late answers are dropped
        # a navigate() that came before the answer: (issue, film_key, plex_id, media_id)
        self._pending: tuple | None = None
        self._built = False                     # the page below the header is built when it's first needed
        self._flowed = None                     # how the language ticks were last laid out
        self._asked_languages: list[str] = []
        self.columns = 0                        # summary chart cards per row (1 or 2)
        self.timings: dict[str, float] = {}
        self.languages = self._saved_languages()
        self.language_vars: dict[str, tk.BooleanVar] = {}
        self.language_checks: dict[str, ttk.Checkbutton] = {}
        self._language_codes: list[str] = []
        self.only_unsubtitled = tk.BooleanVar(self.frame, value=False)
        self.disk_filter: tuple[str, str] | None = None     # ('library', 'Documentaries') from a bar click
        self._disk_shown = None                 # which disk rows the biggest-files table holds
        self.views: dict[str, ChartView] = {}
        self.cards: dict[str, Card] = {}
        T.add_styles(self.frame, _styles)
        self._build()
        self._show_state()

    # -- layout -----------------------------------------------------------------------------------------------
    def _build(self):
        """The header and the placeholder. The page itself - languages, tiles, summary, lists - is built when it's
        first needed (_ensure_built): laying it out costs a few hundred milliseconds, which a tab nobody has opened
        shouldn't add to the app's start."""
        f = self.frame
        f.columnconfigure(0, weight=1)
        f.rowconfigure(1, weight=1)
        head = ttk.Frame(f, style="Page.TFrame", padding=(PAD, 12, PAD, 8))
        head.grid(row=0, column=0, sticky="ew")
        head.columnconfigure(1, weight=1)
        ttk.Label(head, text="Library Doctor", style="PageTitle.TLabel").grid(row=0, column=0, sticky="w")
        self.source_label = ttk.Label(head, text="", style="PageHint.TLabel")
        self.source_label.grid(row=0, column=1, sticky="e", padx=(16, 0))

        self.placeholder = ChartView(f, height=320, background=T.PAGE)
        self.placeholder.grid(row=1, column=0, sticky="nsew")
        self.content = ttk.Frame(f, style="Page.TFrame")
        self.content.grid(row=1, column=0, sticky="nsew")
        self.content.columnconfigure(0, weight=1)
        self.content.rowconfigure(0, weight=1)

    def _ensure_built(self):
        if self._built:
            return
        self._built = True
        self.scroll = ScrollFrame(self.content, background=T.PAGE, style="Page.TFrame")
        self.scroll.grid(row=0, column=0, sticky="nsew")
        self.scroll.canvas.bind("<Configure>", lambda e: self._reflow(e.width), add="+")
        page = self.scroll.inner
        page.columnconfigure(0, weight=1)

        # Your languages: a tick for each language in your files, then a drop-down for the rest - on as many
        # lines as the page's width needs (see _flow_languages)
        langs = ttk.Frame(page, style="Page.TFrame")
        langs.grid(row=0, column=0, sticky="ew", padx=PAD, pady=(0, 8))
        langs.columnconfigure(1, weight=1)
        self.language_label = ttk.Label(langs, text="Your languages:", style="DoctorStrong.TLabel")
        self.language_label.grid(row=0, column=0, sticky="nw", padx=(0, 10))
        self.language_frame = ttk.Frame(langs, style="Page.TFrame", width=1, height=1)
        self.language_frame.grid(row=0, column=1, sticky="nw")
        self.add_var = tk.StringVar(self.frame, value="Add a language...")
        self.add_box = ttk.Combobox(self.language_frame, textvariable=self.add_var, state="readonly", width=18,
                                    values=[])
        self.add_box.bind("<<ComboboxSelected>>", lambda e: self._language_added(), add="+")
        self.language_note = ttk.Label(self.language_frame, text="for the subtitle and audio checks",
                                       style="PageHint.TLabel")
        langs.bind("<Configure>", lambda e: self._flow_languages(e.width), add="+")
        self.language_row = langs
        self._build_language_checks()

        # The tiles
        self.tiles_card = Card(page, padding=(14, 12))
        self.tiles_card.grid(row=1, column=0, sticky="ew", padx=PAD, pady=(0, GAP))
        body = self.tiles_card.body
        body.columnconfigure(0, weight=1)
        self.views["tiles"] = tiles = ChartView(body, height=TILE_H * 2 + TILE_GAP, width=1, background=T.CARD)
        tiles.grid(row=0, column=0, sticky="ew")
        tiles.bind("<Configure>", self._fit_tiles, add="+")
        self.note_label = ttk.Label(body, text="", style="CardSmall.TLabel", justify="left", wraplength=800)
        self.note_label.grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.tiles_card.bind("<Configure>", lambda e: self.note_label.configure(
            wraplength=max(e.width - 40, 200)), add="+")

        # The lower part: the summary, or one list
        self.lower = ttk.Frame(page, style="Page.TFrame")
        self.lower.grid(row=2, column=0, sticky="nsew")
        self.lower.columnconfigure(0, weight=1)
        self._build_summary()
        self._build_issue()
        if self.view != "disk":                  # (a list was asked for before the page was built)
            self.summary.grid_remove()
            self.issue_card.grid()

    def _build_summary(self):
        self.summary = ttk.Frame(self.lower, style="Page.TFrame")
        self.summary.grid(row=0, column=0, sticky="nsew")
        self.summary.columnconfigure(0, weight=1)
        self.chart_grid = ttk.Frame(self.summary, style="Page.TFrame")
        self.chart_grid.grid(row=0, column=0, sticky="ew")
        for kind, _field, title, hint in DISK_CHARTS:
            card = Card(self.chart_grid, title, hint)
            card.body.columnconfigure(0, weight=1)
            view = ChartView(card.body, height=bars_height(6), width=1)
            view.grid(row=0, column=0, sticky="nsew")
            self.cards[kind] = card
            self.views[kind] = view
        self.files_card = card = Card(self.summary, "Biggest files",
                                      "Every file, largest first. Click a bar above to list one library, "
                                      "resolution, drive or codec. Sizes are decimal (1 GB = 1,000,000,000 bytes, "
                                      "as on the spreadsheet), so Windows Explorer shows about 7% less.")
        card.grid(row=1, column=0, sticky="ew", padx=PAD, pady=(0, GAP))
        card.body.columnconfigure(0, weight=1)
        self.files = ListPanel(self, card.body, on_save=self.save_list)
        self.files.frame.grid(row=0, column=0, sticky="nsew")
        self.show_all_link = LinkLabel(self.files.filters, "show every file", self.clear_disk_filter,
                                       style="CardLink.TLabel")
        card.bind("<Configure>", lambda e: self.files.wrap(e.width - 30), add="+")
        self._reflow(0)

    def _build_issue(self):
        self.issue_card = card = Card(self.lower, padding=12)
        card.grid(row=0, column=0, sticky="nsew", padx=PAD, pady=(0, GAP))
        b = card.body
        b.columnconfigure(0, weight=1)
        top = ttk.Frame(b, style="CardInner.TFrame")
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(0, weight=1)
        self.issue_title = ttk.Label(top, text="", style="CardTitle.TLabel")
        self.issue_title.grid(row=0, column=0, sticky="w")
        self.summary_link = LinkLabel(top, "‹  Summary", lambda: self.open_issue("disk"), style="CardLink.TLabel")
        self.summary_link.grid(row=0, column=1, sticky="e")
        self.explanation = ttk.Label(b, text="", style="CardNote.TLabel", justify="left", wraplength=800)
        self.explanation.grid(row=1, column=0, sticky="w", pady=(2, 0))
        self.limits = ttk.Label(b, text="", style="CardSmall.TLabel", justify="left", wraplength=800)
        self.limits.grid(row=2, column=0, sticky="w", pady=(2, 10))
        self.issue_list = ListPanel(self, b, on_save=self.save_list)
        self.issue_list.frame.grid(row=3, column=0, sticky="nsew")
        self.subs_filter = ttk.Checkbutton(self.issue_list.filters, text="Only those without subtitles you read",
                                           variable=self.only_unsubtitled, style="Card.TCheckbutton",
                                           command=self._render_issue)
        card.bind("<Configure>", self._wrap_issue, add="+")
        card.grid_remove()

    def _wrap_issue(self, event):
        width = event.width - 30
        if width < 60:
            return
        for label in (self.explanation, self.limits):
            if str(label.cget("wraplength")) != str(width):
                label.configure(wraplength=width)
        self.issue_list.wrap(width)

    def _reflow(self, width: int | None = None):
        """The space charts two to a row when there's room, one when there isn't."""
        if not width:
            width = self.scroll.canvas.winfo_width()
        if width < 50 and self.columns:
            return
        cols = 2 if width / self.s >= TWO_COLUMNS_FROM else 1
        if cols == self.columns:
            return
        self.columns = cols
        for c in range(2):
            self.chart_grid.columnconfigure(c, weight=1 if c < cols else 0, uniform="doctor" if c < cols else "")
        for n, kind in enumerate(k for k, *_ in DISK_CHARTS):
            row, col = divmod(n, cols)
            padx = (PAD, PAD) if cols == 1 else ((PAD, GAP // 2) if col == 0 else (GAP // 2, PAD))
            self.cards[kind].grid(row=row, column=col, sticky="nsew", padx=padx, pady=(0, GAP))

    def _fit_tiles(self, event=None):
        """The tiles wrap onto more rows in a narrower window: make room for them."""
        view = self.views["tiles"]
        width = event.width if event is not None else view.winfo_width()
        if width < 50:
            return
        count = len(tile_items(self.data)) if self.data else len(DR.ISSUE_IDS)
        want = tiles_height(width, count, self.s)
        if int(view.cget("height")) != want:
            view.configure(height=want)

    # -- your languages ----------------------------------------------------------------------------------------
    def _saved_languages(self) -> list[str]:
        return list(prefs.get(self.app, "doctor_languages"))

    def preference_changed(self, key: str, value):
        if key == "doctor_languages":            # (changed on the Settings tab)
            self._set_languages(list(value), save=False)
        elif key in ("doctor_upgrade_rating", "doctor_upgrade_below"):
            self._share_settings(self.catalog, replace=True)
            # (a tab that hasn't asked yet asks with the new ones when it's shown)
            catalog = self.catalog
            if catalog is not None and self.state_message() is None and (self._filled_for is catalog
                                                                         or self._asking_for is catalog):
                self._fill(quiet=True)

    def _upgrade_settings(self) -> tuple[float, str]:
        """(the rating, the resolution) of Settings > Library Doctor > Upgrade candidates."""
        return float(prefs.get(self.app, "doctor_upgrade_rating")), prefs.get(self.app, "doctor_upgrade_below")

    def _share_settings(self, catalog, replace: bool = False):
        """Tell the backbone your languages and what an upgrade candidate is, so the Film page's issues
        (doctor.film_issues with no choices) use them even before this tab has been opened."""
        cache = getattr(catalog, "cache", None)
        if not isinstance(cache, dict):
            return
        rating, below = self._upgrade_settings()
        for key, value in (("doctor.languages", list(self.languages)), ("doctor.min_rating", rating),
                           ("doctor.upgrade_below", below)):
            if replace or key not in cache:
                cache[key] = value

    def _offered_languages(self) -> list[str]:
        """The ticks: the languages with the most tracks in your files, and any you've chosen."""
        available = (self.data or {}).get("available_languages", [])
        codes = [x["code"] for x in available[:TOP_LANGUAGES]]
        for code in self.languages:
            if code not in codes:
                codes.append(code)
        return codes

    def _build_language_checks(self):
        if not self._built:
            return                              # (built with the page)
        codes = self._offered_languages()
        if codes == self._language_codes:
            for code, var in self.language_vars.items():
                var.set(code in self.languages)
        else:
            for cb in self.language_checks.values():
                cb.destroy()
            self.language_vars, self.language_checks = {}, {}
            for code in codes:
                var = tk.BooleanVar(self.frame, value=code in self.languages)
                cb = ttk.Checkbutton(self.language_frame, text=DR.lang_name(code), variable=var,
                                     style="Page.TCheckbutton", command=lambda c=code: self._language_ticked(c))
                self.language_vars[code], self.language_checks[code] = var, cb
            self._language_codes = codes
            self._flowed = None
        available = (self.data or {}).get("available_languages", [])
        self.add_box.configure(values=[x["name"] for x in available if x["code"] not in codes])
        self.add_var.set("Add a language...")
        self.add_box.selection_clear()      # (not the picked name's length of it, highlighted)
        self._flow_languages()

    def language_layout(self, width: int) -> dict:
        """Where the language ticks, the drop-down and the hint go in a language row `width` px wide: as many to a
        line as fit, then the next line - {'spots': [(widget, x, y)...], 'note': (x, y) or None (no room for the
        hint: it only shows on the first line, after everything), 'width', 'height', 'label_pad'}."""
        gap, note_gap, line_gap = 10, 12, 4
        room = max(width - self.language_label.winfo_reqwidth() - 10, 1)
        items = [self.language_checks[c] for c in self._language_codes] + [self.add_box]
        sizes = [(w.winfo_reqwidth(), w.winfo_reqheight()) for w in items]
        line_h = max(h for _w, h in sizes)
        x = y = widest = 0
        spots = []
        for widget, (w, h) in zip(items, sizes):
            if x and x + w > room:                       # (the first on a line always goes, however narrow)
                x, y = 0, y + line_h + line_gap
            spots.append((widget, x, y + (line_h - h) // 2))
            widest = max(widest, x + w)
            x += w + gap
        note = None
        note_w, note_h = self.language_note.winfo_reqwidth(), self.language_note.winfo_reqheight()
        if y == 0 and widest + note_gap + note_w <= room:
            note = (widest + note_gap, (line_h - note_h) // 2)
            widest += note_gap + note_w
        label_pad = max((line_h - self.language_label.winfo_reqheight()) // 2, 0)
        return {"spots": spots, "note": note, "width": min(widest, room), "height": y + line_h,
                "label_pad": label_pad}

    def _flow_languages(self, width: int | None = None):
        """Lay the language row out for its width: the ticks and the drop-down wrap onto more lines in a narrow
        window (so the drop-down is never cut off), and the hint shows when there's room for it."""
        if not self._built:
            return
        width = width or self.language_row.winfo_width()
        if width < 50:
            return
        layout = self.language_layout(width)
        mark = ([(str(w), x, y) for w, x, y in layout["spots"]], layout["note"], layout["width"], layout["height"])
        if mark == self._flowed:
            return
        self._flowed = mark
        for widget, x, y in layout["spots"]:
            widget.place(x=x, y=y)
        if layout["note"] is not None:
            self.language_note.place(x=layout["note"][0], y=layout["note"][1])
        else:
            self.language_note.place_forget()
        self.language_frame.configure(width=layout["width"], height=layout["height"])
        self.language_label.grid_configure(pady=(layout["label_pad"], 0))

    def _language_ticked(self, code: str):
        ticked = [c for c in self._language_codes if self.language_vars[c].get()]
        if not ticked:
            self.language_vars[code].set(True)
            self.app.set_status("Keep at least one language: the language checks need to know what you speak.")
            return
        self._set_languages([c for c in self.languages if c in ticked] +
                            [c for c in ticked if c not in self.languages])

    def _language_added(self):
        name = self.add_var.get()
        available = (self.data or {}).get("available_languages", [])
        code = next((x["code"] for x in available if x["name"] == name), None)
        if code is None:
            self.add_var.set("Add a language...")
            self.add_box.selection_clear()      # (not the picked name's length of it, highlighted)
            return
        self._set_languages(self.languages + [code])

    def _set_languages(self, codes: list[str], save: bool = True):
        if codes == self.languages:
            return
        self.languages = list(codes)
        if save and prefs.settings_of(self.app) is not None:
            try:
                prefs.set(self.app, "doctor_languages", list(codes))     # (saved; the Settings tab hears of it)
            except ValueError:
                pass
        self._share_settings(self.catalog, replace=True)
        self._build_language_checks()
        if self.catalog is not None and self.state_message() is None:
            self._fill(quiet=True)

    # -- called by the main window -----------------------------------------------------------------------------
    def catalog_changed(self, catalog, state: str):
        super().catalog_changed(catalog, state)
        self._token += 1                          # whatever was being worked out is for the old collection
        self._share_settings(catalog)
        self.data = None
        self._filled_for = self._asking_for = None
        self._disk_shown = None
        self.disk_filter = None
        self._show_state()
        if self.state_message() is None and self._visible():
            self._fill()

    def shown(self):
        if (self.state_message() is None and self._filled_for is not self.catalog
                and self._asking_for is not self.catalog):
            self._fill()
        if self.state_message() is None:
            self._ensure_built()                   # (while the answer is worked out in the background)

    def keep(self) -> dict:
        """(Laid out again for a new text size:) the list on show."""
        return {"view": self.view}

    def restore(self, kept: dict):
        if kept.get("view") not in (None, "", "disk"):
            self._pending = (kept["view"], None, None, None)     # (opened once the lists are worked out)

    def navigate(self, issue=None, film_key=None, plex_id=None, media_id=None, **_kwargs):
        """Open a list (an issue id, or an alias: 'size', 'summary', 'missing'), and select a film in it - the
        row of one copy when media_id (a copy) or plex_id (a library item: one edition) says which, as the Film
        page's issues do. Before the answer has come, it's kept and done when it comes."""
        self._pending = (issue, film_key, plex_id, media_id)
        if self.data is not None and self._filled_for is self.catalog:
            self._apply_pending()
        else:
            self.shown()

    # -- content --------------------------------------------------------------------------------------------------
    def _visible(self) -> bool:
        current = getattr(self.app, "current_tab", None)
        try:
            return (callable(current) and current() is self) or bool(self.frame.winfo_ismapped())
        except tk.TclError:
            return False

    def _show_state(self):
        msg = self.state_message()
        if msg is not None:
            self._show_placeholder(*msg)
            self.source_label.configure(text="")

    def _show_placeholder(self, text: str, sub: str | None = None):
        self.content.grid_remove()
        self.placeholder.grid()
        self.show_message(self.placeholder, text, sub)

    def _show_content(self):
        self._ensure_built()
        self.placeholder.grid_remove()
        self.content.grid()

    def _fill(self, quiet: bool = False):
        """Work the lists out in the background: the first time for a collection it reads every copy's files and
        tracks (half a second for a few thousand copies); after that, another language is quicker still. quiet:
        keep showing the current lists meanwhile (a language change)."""
        catalog = self.catalog
        if catalog is None:
            return
        self._token += 1
        token = self._token
        self._asking_for = catalog
        languages = list(self.languages)
        rating, below = self._upgrade_settings()
        started = time.perf_counter()
        if not quiet or self.data is None:
            self._show_placeholder("Checking your files...", "Reading every copy's files and tracks from the "
                                   "database - a second or so the first time.")

        def done(answer):
            if token != self._token or catalog is not self.catalog:
                return                             # the collection (or the languages) changed meanwhile
            self._asking_for = None
            self.timings["ask"] = time.perf_counter() - started
            self._apply(answer, catalog, languages)

        def failed(message):
            if token != self._token:
                return
            self._asking_for = None
            self._filled_for = None                # (so showing the tab again tries again)
            self._show_placeholder("Couldn't check your files", message)

        self.app.run(lambda: doctor_answer(catalog, languages, rating, below), done, failed,
                     status="Checking your files...", key="doctor.answer")

    def _apply(self, answer: dict, catalog, languages):
        if not answer.get("ok"):
            self._filled_for = None
            error = answer.get("error") or "Try reloading the database on the Export tab."
            prefix = "Couldn't read your files: "
            if error.startswith(prefix):              # (the title says as much already)
                error = error[len(prefix)].upper() + error[len(prefix) + 1:]
            self._show_placeholder("Couldn't check your files", error)
            return
        started = time.perf_counter()
        self._ensure_built()
        self.data = answer
        self._filled_for = catalog
        self._asked_languages = list(languages)
        self.source_label.configure(text="   ·   ".join(x for x in (
            "Checks your files: copies, languages, quality and space", backup_text(catalog)) if x))
        self.note_label.configure(text=answer.get("note", ""))
        if not answer.get("totals", {}).get("films"):
            self._show_placeholder("There are no films in this collection",
                                   "Pick another Plex database on the Export tab.")
            return
        self._show_content()
        self._build_language_checks()
        self._fit_tiles()
        self._draw_tiles()
        if self.view == "disk":
            self._render_summary()
        else:
            self._render_issue()
        self.timings["draw"] = time.perf_counter() - started
        if self._pending is not None:
            self._apply_pending()

    def _apply_pending(self):
        issue, film_key, plex_id, media_id = self._pending
        self._pending = None
        if issue in (None, ""):
            target = "disk"
        else:
            try:
                target = DR.issue_id(issue)
            except ValueError:
                self.app.set_status(f"Library Doctor has no check called '{issue}'.")
                target = "disk"
        self.open_issue(target, scroll=False)
        if film_key:
            panel = self.files if target == "disk" else self.issue_list
            if not panel.select(film_key, _whole(media_id), _whole(plex_id)):
                film = self.catalog.films.get(film_key) if self.catalog is not None else None
                name = film.label if film is not None else "That film"
                self.app.set_status(f"{name} isn't on the list of {DR.TITLES[target].lower()}.")
        if target == "disk" and not film_key:
            self.scroll.to_top()
        else:
            self._scroll_to_lower(always=True)

    def _draw_tiles(self):
        items = tile_items(self.data or {})
        selected = self.view if self.view != "disk" else "disk"
        self.views["tiles"].show(lambda p: draw_tiles(p, items, selected, on_click=self.open_issue))

    # -- the lists ----------------------------------------------------------------------------------------------
    def open_issue(self, issue: str, scroll: bool = True):
        """Show one list (or the summary, for 'disk')."""
        issue = DR.issue_id(issue)
        changed = issue != self.view
        self.view = issue
        if changed:
            self.only_unsubtitled.set(False)
        if self.data is None:
            return
        self._draw_tiles()
        if issue == "disk":
            self.issue_card.grid_remove()
            self.summary.grid()
            self._render_summary()
        else:
            self.summary.grid_remove()
            self.issue_card.grid()
            self._render_issue()
        if scroll and changed:
            self._scroll_to_lower()

    def _scroll_to_lower(self, always: bool = False):
        """After a tile click, bring the list into view (it can be below the fold in a small window)."""
        try:
            self.frame.update_idletasks()
            total = self.scroll.inner.winfo_height()
            top = self.lower.winfo_y()
            visible = self.scroll.canvas.winfo_height()
            if total > 1 and visible > 1 and (always or top > visible * 0.6):
                self.scroll.canvas.yview_moveto(max(top - int(8 * self.s), 0) / total)
        except tk.TclError:
            pass

    def _render_issue(self):
        data = self.data or {}
        issue = self.view
        item = data.get("issues", {}).get(issue)
        if item is None:
            return
        self.issue_title.configure(text=f"{item['title']}  ·  {item['count']:,}")
        self.explanation.configure(text=item["explanation"])
        self.limits.configure(text=item.get("limits", ""))
        rows = item["rows"]
        one, many = row_noun(issue)
        count_text = plural(len(rows), one, many)
        if issue == "audio":
            self.subs_filter.grid(row=0, column=0, sticky="w")
            if self.only_unsubtitled.get():
                rows = [r for r in rows if r.get("your_subtitles") == "No"]
                count_text = f"{len(rows):,} of {plural(item['count'], one, many)} - those without subtitles " \
                             f"you read"
        else:
            self.subs_filter.grid_remove()
        empty = empty_text(issue, data)
        if issue == "audio" and self.only_unsubtitled.get() and item["rows"]:
            empty = "None - every copy on this list has subtitles you read."
        self.issue_list.clear_error()
        self.issue_list.show(issue, item["columns"], rows, empty, count_text)

    def _render_summary(self):
        data = self.data or {}
        for kind, _field, _title, _hint in DISK_CHARTS:
            items = disk_items(data, kind)
            chosen = {self.disk_filter[1]} if self.disk_filter and self.disk_filter[0] == kind else None
            self.views[kind].show(lambda p, items=items, kind=kind, chosen=chosen: C.bars(
                p, items, value_fmt=space_text, on_click=lambda key, kind=kind: self.filter_disk(kind, key),
                emphasis=chosen), height=bars_height(len(items)))
        self._fill_files()

    def _disk_rows(self) -> list[dict]:
        rows = (self.data or {}).get("issues", {}).get("disk", {}).get("rows", [])
        if self.disk_filter:
            kind, label = self.disk_filter
            rows = [r for r in rows if r.get(kind) == label]
        return rows

    def _fill_files(self):
        data = self.data or {}
        item = data.get("issues", {}).get("disk")
        if item is None:
            return
        rows = self._disk_rows()
        # (another language gives a new answer but the same files: don't fill 3,500 rows again for nothing)
        everything = item["rows"]
        mark = (len(everything), id(everything[0]) if everything else None, id(everything[-1]) if everything
                else None, self.disk_filter)
        if self._disk_shown == mark:
            return
        started = time.perf_counter()
        if self.disk_filter:
            kind, label = self.disk_filter
            count = f"{plural(len(rows), 'file', 'files')} {DISK_FILTER_WORDS[kind]} {label}  ·"
            self.show_all_link.grid(row=0, column=0, sticky="w")
        else:
            count = f"{plural(len(rows), 'file', 'files')}, largest first"
            self.show_all_link.grid_remove()
        self.files.clear_error()
        self.files.show("disk", item["columns"], rows, empty_text("disk", data), count)
        self._disk_shown = mark
        self.timings["files"] = time.perf_counter() - started

    def filter_disk(self, kind: str, label: str):
        """A bar on a space chart: list just those files."""
        self.disk_filter = None if self.disk_filter == (kind, label) else (kind, label)
        self._render_summary()

    def clear_disk_filter(self):
        self.disk_filter = None
        self._render_summary()

    # -- over to the film's page ---------------------------------------------------------------------------------
    def open_film(self, row: dict):
        key = row.get("film_key")
        if not key:
            return
        if self.app.goto("Film", film_key=key, title=row.get("film", "")) is None:
            self.app.set_status("The Film tab isn't available.")

    # -- saving a list ---------------------------------------------------------------------------------------------
    def save_list(self):
        """Save the list on show, in the order it's sorted, as an .xlsx wherever you choose - then opened in your
        spreadsheet program when Settings > Library Doctor says so (never otherwise)."""
        data = self.data
        if data is None:
            return
        issue = self.view
        panel = self.files if issue == "disk" else self.issue_list
        item = data["issues"][issue]
        rows = panel.rows_in_order()
        title = item["title"]
        if issue == "disk" and self.disk_filter:
            title = f"{title} - {self.disk_filter[1]}"
        elif issue == "audio" and self.only_unsubtitled.get():
            title = f"{title} - no subtitles you read"
        source = getattr(self.catalog, "source", "") or ""
        folder = prefs.get(self.app, "doctor_save_dir")
        if not (folder and os.path.isdir(folder)):
            folder = os.path.dirname(source) if source and os.path.isfile(source) else None
        path = filedialog.asksaveasfilename(parent=self.frame, title="Save this list",
                                            defaultextension=".xlsx", filetypes=[("Excel workbook", "*.xlsx")],
                                            initialdir=folder, initialfile=initial_name(title, source))
        if not path:
            return
        sheet = {"title": title, "explanation": item["explanation"], "note": item.get("limits", ""),
                 "columns": item["columns"], "rows": list(rows)}
        about = [("Your languages", ", ".join(x["name"] for x in data.get("languages", []))),
                 ("Database", os.path.basename(source)), ("Ratings", data.get("owner") or "the server owner"),
                 ("Saved", datetime.now().strftime("%Y-%m-%d %H:%M"))]
        one, many = row_noun(issue)
        count = len(rows)

        def work():
            from ..export import OutputError
            try:
                return {"ok": True, "path": DR.save_xlsx(path, [sheet], about)}
            except OutputError as exc:
                return {"ok": False, "error": str(exc)}

        def done(result):
            if not result.get("ok"):
                panel.show_error(result.get("error", "Couldn't save the list."))
                return
            panel.clear_error()
            if prefs.settings_of(self.app) is not None:
                prefs.set(self.app, "doctor_save_dir", os.path.dirname(result["path"]))
            saved = f"Saved {plural(count, one, many)} to {result['path']}"
            if not prefs.get(self.app, "doctor_open_saved"):
                self.app.set_status(saved)
                return
            problem = open_saved(self.app, result["path"])
            if problem:
                panel.show_error(f"Saved, but it couldn't be opened: {problem}")
                self.app.set_status(saved)
            else:
                self.app.set_status(f"{saved} - opening it.")

        def failed(message):
            panel.show_error(f"Couldn't save the list: {message}")

        self.app.run(work, done, failed, status="Saving the list...", key="doctor.save")
