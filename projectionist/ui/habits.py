"""The Viewing tab: your viewing habits, from Plex's own play history (the server owner's movie plays).

Two views:
  Your habits      headline numbers and what Plex's history covers; when you start films (an hour-by-weekday
                   heatmap); plays (or hours) per month; streaks and busy spells; the films you've played most; films you
                   stopped partway; forgotten favourites (rated highly, not played for a while)
  Year in review   one year: films and hours, new against rewatched, month by month, day by day, the genres,
                   decades, countries, stars and directors you watched, the first and last film, and every play

Everything comes from projectionist.habits (see there for how Plex's logs become plays), worked out on a background
thread when the tab is first shown. Films open on the Film page, people in Six Degrees, genres, decades and
countries in Watch Next. navigate(year=2025) opens that year's review.

Settings: dates, times and the week's first day as Settings > Dates and times says (projectionist/formats.py) -
changed there, the page is written again at once; what counts as a forgotten favourite (Settings > Viewing, and
the card's own drop-downs, which change the same settings).
"""

from __future__ import annotations

import math
import time
import tkinter as tk
import weakref
from datetime import date, timedelta
from tkinter import ttk

from .. import formats as F
from .. import prefs
from ..formats import as_date as _as_date
from ..formats import date_range, hour_label, hour_span, nice_date, nice_time, short_date  # noqa: F401
from . import charts as C
from . import theme as T
from .base import BaseTab
from .overview import SHORT_COUNTRY
from .widgets import Card, ChartView, LinkLabel, ScrollFrame, Table

TWO_COLUMNS_FROM = 940     # width of the page (device-independent px) from which cards sit two to a row
PAD = 16
GAP = 12
TILE_H = 78
TILE_MIN_W = 130
BAR_ROW = 26
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
               "November", "December"]
RATING_CHOICES = {"10 only": 10, "9 or more": 9, "8 or more": 8, "7 or more": 7}
AGE_CHOICES = {"6 months": 182, "a year": 365, "2 years": 730}
VIEWS = ("Your habits", "Year in review")
MONTH_TITLES = {"films": "Plays per month", "hours": "Hours per month"}     # the months card, by what it shows
# The settings the Viewing tab keeps (Settings > Viewing): the Forgotten favourites card's two drop-downs change
# the same ones. Dates, times and the week's first day are Settings > Dates and times (projectionist/formats.py).
FORGOTTEN_KEYS = {"rating": "viewing_forgotten_rating", "days": "viewing_forgotten_days"}
prefs.section("Viewing", order=35, hint="The Viewing tab's lists.")
prefs.define(FORGOTTEN_KEYS["rating"], "Viewing", "Forgotten favourites: films rated", kind="choice", default=9,
             choices=[(v, label) for label, v in RATING_CHOICES.items()],
             help="Which of your films the Forgotten favourites list keeps an eye on: those you rated this highly "
                  "(out of 10). The drop-downs on the list change it too.")
prefs.define(FORGOTTEN_KEYS["days"], "Viewing", "Forgotten favourites: not played for", kind="choice", default=365,
             choices=[(v, label) for label, v in AGE_CHOICES.items()],
             help="How long since you last played one of them before it counts as forgotten.")


def pale() -> str:
    """A month only partly in Plex's history: the month blue, faded toward the chart (in the look in use)."""
    return T.mix(T.BLUE, T.WASH_BASE, 0.55)


def _styles(style):
    """This tab's own label styles (the card styles are the theme's), in the look in use."""
    style.configure("Habits.Head.TLabel", background=T.CARD, foreground=T.INK, font=T.font(style, 12, "bold"))
    style.configure("Habits.Strong.TLabel", background=T.CARD, foreground=T.INK, font=T.font(style, 9, "bold"))
    style.configure("Habits.Page.TLabel", background=T.PAGE, foreground=T.INK_2)


def __getattr__(name):
    if name == "PALE":                      # (habits.PALE: pale() in the look in use)
        return pale()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# ---------------------------------------------------------------------------------------------------------
# Words (plain functions, so they're easy to test)
# ---------------------------------------------------------------------------------------------------------
# Dates and times - nice_date, date_range, short_date, nice_time, hour_label, hour_span - are written in the
# style chosen under Settings > Dates and times: they come from projectionist/formats.py (imported above).


def plural(n: int, word: str, words: str | None = None) -> str:
    return f"{n:,} {word}" if n == 1 else f"{n:,} {words or word + 's'}"


def film_label(title: str, year) -> str:
    return f"{title} ({year})" if year else str(title)


def rating_text(v) -> str:
    return "" if v is None else f"{v:g}"


def _part_of_day(h: int) -> str:
    if 5 <= h < 12:
        return "in the morning"
    if 12 <= h < 15:
        return "in the early afternoon"
    if 15 <= h < 17:
        return "in the late afternoon"
    if 17 <= h < 19:
        return "in the early evening"
    if 19 <= h < 22:
        return "in the evening"
    return "late at night"


def heatmap_words(hm: dict | None) -> str:
    """The heatmap's findings in plain words: when you usually start films, your biggest days, and the hours
    when you never do."""
    hm = hm or {}
    total = hm.get("plays", 0)
    if not total:
        return "Plex hasn't seen you play a film through yet, so there are no start times to show."
    by_hour, by_day = hm["by_hour"], hm["by_day"]
    if total < 20:
        return f"Only {plural(total, 'film has', 'films have')} a start time so far - too few to see a pattern yet."
    best = max(range(24), key=lambda h: (by_hour[h] + by_hour[(h + 1) % 24], -h))
    n = by_hour[best] + by_hour[(best + 1) % 24]
    words = [f"You mostly start films {_part_of_day(best)}: {hour_span(best, 2)} ({n / total:.0%} of them)."]
    first = F.first_weekday()                        # (a tie goes to the day earlier in your week)
    order = sorted(range(7), key=lambda d: (-by_day[d], (d - first) % 7))
    top, second = order[0], order[1]
    if by_day[top] == by_day[second]:
        words.append(f"{DAY_NAMES[top]} and {DAY_NAMES[second]} are your biggest days ({by_day[top]:,} films each).")
    else:
        words.append(f"{DAY_NAMES[top]} is your biggest day ({by_day[top]:,} films), then {DAY_NAMES[second]} "
                     f"({by_day[second]:,}).")
    # the longest run of hours (round the clock) when no film started
    quiet, run, best_run = [h for h in range(24) if not by_hour[h]], 0, (0, 0)
    if 0 < len(quiet) < 24 and total >= 50:          # (with only a few films, empty hours say nothing)
        for h in range(48):
            if not by_hour[h % 24]:
                run += 1
                if run > best_run[0] and run <= 24:
                    best_run = (run, (h - run + 1) % 24)
            else:
                run = 0
        length, start = best_run
        if length >= 2:
            words.append(f"No film started between {hour_label(start)} and {hour_label(start + length)}.")
    return " ".join(words)


def coverage_text(history: dict, owner: str = "") -> tuple[str, str]:
    """(what period the history covers, how its plays are counted) - said plainly on the page."""
    h = history or {}
    first, as_of = h.get("first"), h.get("as_of")
    whose = f"{owner}'s" if owner else "your"
    if not first:
        return (f"Plex has no plays logged for {owner or 'your account'}.", "")
    setup = ""
    since = _as_date(h.get("server_since"))
    if since is not None and 0 <= (_as_date(first) - since).days <= 7:
        setup = " (when this server was set up)"
    period = (f"Plex's dated history of {whose} plays runs from {nice_date(first)}{setup} to the backup on "
              f"{nice_date(as_of)}: {plural(h.get('days', 0), 'day')}.")
    notes = []
    if h.get("undated_films"):
        n, early = h["undated_films"], h.get("undated_first_week") or 0
        when = "in the history's first week" + (", as this server was set up" if setup else "")
        how = (f"{plural(early, 'of those films was', 'of those films were')} marked as played {when}, so most "
               "were likely seen before the history starts" if early and early * 2 >= n else
               "they were marked as played, or synced from elsewhere")
        notes.append(f"Plex also counts {plural(h.get('undated_plays', 0), 'play')} of {plural(n, 'film')} that it "
                     f"never logged, so they have no date: {how}. They aren't in anything over time (the most-played "
                     "list and forgotten favourites do count them).")
    if h.get("marked"):
        notes.append(f"{plural(h['marked'], 'play was', 'plays were')} marked as played rather than played "
                     "through Plex: they count on their day, but have no start time or playback hours.")
    if h.get("bulk_marked"):
        bursts = h.get("bulk_bursts") or 0
        notes.append(f"{plural(h['bulk_marked'], 'play')} marked in bulk"
                     + (f" ({plural(bursts, 'burst')} of different films within 2 minutes)" if bursts else "")
                     + " are left out of everything dated - they say you'd seen a film, not when.")
    if h.get("double_logs"):
        notes.append(f"{plural(h['double_logs'], 'repeat log')} of one play count once (the same film again "
                     "within 6 hours, marked as played up to 4 days after playing it, or another edition of it "
                     "marked as played later).")
    if h.get("gone_plays"):
        notes.append(f"{plural(h['gone_plays'], 'play')} of films deleted since count under the title Plex logged.")
    if h.get("skipped"):
        notes.append(f"{plural(h['skipped'], 'play')} of films in libraries that aren't loaded are left out.")
    offset = h.get("utc_offset") or ""
    zone = f" (UTC{offset[:3]}:{offset[3:]})" if len(offset) == 5 else ""
    notes.append(f"Only {owner or 'the server owner'}'s plays count; times are this PC's{zone}.")
    return period, " ".join(notes)


def history_hint(history: dict, owner: str) -> str:
    """The page header's hint: whose plays, and the period."""
    h = history or {}
    bits = [f"Plays by {owner}"] if owner else []
    if h.get("first"):
        bits.append(f"Plex history {nice_date(h['first'])} - {nice_date(h.get('as_of'))}")
    return "   ·   ".join(bits)


def _clock_label(minutes: int) -> str:
    """Minutes after midnight -> '5 pm', '8:30 pm', 'midnight' (or '17:00', '20:30', '00:00')."""
    h, m = divmod(minutes % (24 * 60), 60)
    return hour_label(h) if not m else F.time_text(h, m)


def utc_month_note(offset: str | None) -> str:
    """Plex adds its playback clock up by UTC month, so hours near the turn of a month can land in the one next to
    it: which ones, for this PC's UTC offset ('-0500'). '' in UTC (or with no offset)."""
    offset = offset or ""
    if len(offset) != 5 or offset[0] not in "+-" or not offset[1:].isdigit():
        return ""
    minutes = int(offset[1:3]) * 60 + int(offset[3:])
    if not minutes:
        return ""
    if offset[0] == "-":           # UTC midnight is the evening before here
        when = f"after about {_clock_label(24 * 60 - minutes)} on a month's last day count in the next month"
    else:                          # ...or the morning after
        when = f"before about {_clock_label(minutes)} on the 1st count in the month before"
    return f"Plex adds its playback clock up by UTC month, so hours played {when}."


def hours_text(hours: dict | None) -> tuple[str, str]:
    """(value, note) for a year's hours: Plex's playback clock when there is one, else the running times."""
    hours = hours or {}
    if hours.get("measured") is not None:
        return f"{hours['measured']:,}", "Plex's playback clock"
    if not hours.get("runtime"):
        return "-", "no running times known"
    return f"~{hours.get('runtime', 0):,}", "by running times"


def whole_ticks(v) -> str:
    """Axis numbers for counts: whole numbers only (a chart of 0-1 films gets no '0.5')."""
    return f"{int(v):,}" if float(v).is_integer() else ""


def year_headline(year: dict, history: dict | None = None) -> tuple[str, str]:
    """('2026 so far: 97 films, 163 hours', 'Jan 1 - Sep 25, 2026 - the date of the backup')."""
    y, h = year.get("year"), history or {}
    if not year.get("plays"):
        span = f"Plex's history covers {nice_date(h.get('first'))} - {nice_date(h.get('as_of'))}." \
            if h.get("first") else ""
        return f"{y}: no films on record", span
    so_far = year.get("to") == h.get("as_of") and not year.get("to", "").endswith("12-31")
    value, _note = hours_text(year.get("hours"))
    hours = "" if value == "-" else f", {value} hours" if not value.startswith("~") else f", about {value[1:]} hours"
    head = f"{y}{' so far' if so_far else ''}: {plural(year['films'], 'film')}{hours}"
    sub = date_range(year.get("from"), year.get("to"))
    if so_far:
        sub += " - the date of the backup"
    if h.get("first") and year.get("from") == h["first"][:10]:
        sub += " - Plex's history starts then"
    return head, sub


def change_text(a: int, b: int) -> str:
    if not b:
        return ""
    pct = (a - b) / b * 100
    return "the same" if abs(pct) < 0.5 else f"{pct:+.0f}%"


def compare_words(vs: dict | None, year: int, history: dict | None = None, has_previous: bool = False) -> str:
    """The year against the same stretch of the year before, in words."""
    if not vs:
        h = history or {}
        if not has_previous and h.get("first"):
            return f"Plex's history starts {nice_date(h['first'])} - nothing earlier to compare {year} with."
        return f"No stretch of {year - 1} lines up with {year}'s to compare."
    lo, hi = (date(2001, *map(int, vs["from"].split("-"))), date(2001, *map(int, vs["to"].split("-"))))
    stretch = "the whole year" if (vs["from"], vs["to"]) == ("01-01", "12-31") else date_range(lo, hi, year=False)
    (pa, pb), (fa, fb) = vs["plays"], vs["films"]
    change = change_text(pa, pb)
    text = (f"Against {vs['year']} ({stretch}): {plural(pa, 'play')} vs {pb:,}"
            + (f" ({change})" if change and change != "the same" else "") + f", {plural(fa, 'film')} vs {fb:,}")
    # Your ratings are as they are now (Plex keeps no rating history), so this compares the films, not the rater
    ra, rb = vs.get("rating") or (None, None)
    if ra is not None and rb is not None:
        na, nb = vs.get("rated") or (None, None)
        of_a = f" ({na:,} of {fa:,} rated)" if na is not None else ""
        of_b = f" ({nb:,} of {fb:,})" if nb is not None else ""
        text += f"; by your ratings now, the films average {ra:.1f}{of_a} vs {rb:.1f}{of_b}"
    return text + "."


# ---------------------------------------------------------------------------------------------------------
# Chart data (plain functions)
# ---------------------------------------------------------------------------------------------------------
def streak_marks(st: dict | None) -> str:
    """'On 2 of those days, only films marked as played', or '' when every day has a film played through (a mark
    counts on the day it was ticked, which needn't be the day you watched)."""
    marked = (st or {}).get("marked_only") or 0
    if not marked:
        return ""
    return f"On {marked} of those days, only films marked as played" if marked > 1 else \
        "On one of those days, only films marked as played"


def streak_tip(st: dict | None) -> str:
    """A longest streak's tooltip: how long, when, and how many of its days have only films marked as played."""
    if not st:
        return ""
    marks = streak_marks(st)
    return f"{st['days']} days in a row with a film\n{date_range(st['from'], st['to'])}" + (
        f"\n{marks}" if marks else "")


def history_tiles(data: dict) -> list[dict]:
    h = data.get("history", {})
    st = (data.get("streaks") or {}).get("longest")
    if h.get("measured_hours") is not None:
        hours, hours_note = f"{round(h['measured_hours']):,}", "Plex's playback clock"
        hours_tip = (f"{h['measured_hours']:,} hours by Plex's own playback clock\nIt counts what actually played, "
                     f"stopped films too.\nThe films' running times add up to {round(h.get('runtime_hours', 0)):,}.")
    else:
        hours, hours_note = f"~{round(h.get('runtime_hours', 0)):,}", "by running times"
        hours_tip = "Added up from the films' running times\n(this database has no playback clock)"
    first = nice_date(h.get("first"))
    return [
        {"label": "Plays on record", "value": f"{h.get('plays', 0):,}", "note": f"since {first}",
         "tip": f"{plural(h.get('plays', 0), 'dated play')} since {first}\n{h.get('played', 0):,} played through "
                f"Plex, {h.get('marked', 0):,} marked as played\nPlex's own count is {h.get('plex_count', 0):,}, "
                "plays with no date included"},
        {"label": "Films", "value": f"{h.get('films', 0):,}", "note": "different films",
         "tip": f"{plural(h.get('films', 0), 'different film')} played since {first}"},
        {"label": "Hours", "value": hours, "note": hours_note, "tip": hours_tip},
        {"label": "Active days", "value": f"{h.get('active_days', 0):,}", "note": f"of {h.get('days', 0):,}",
         "tip": f"Days with at least one film: {h.get('active_days', 0):,} of the {h.get('days', 0):,} days "
                "in Plex's history"},
        {"label": "Longest streak", "value": plural(st["days"], "day") if st else "-",
         "note": date_range(st["from"], st["to"], year=False) if st else "", "tip": streak_tip(st)},
        {"label": "Played twice or more", "value": f"{data.get('most_played_total', 0):,}", "note": "films",
         "tip": "Films played at least twice, counting the plays Plex has with no date\n(ticking off several "
                "editions of a film counts once)"},
    ]


def year_tiles(year: dict, utc_offset: str | None = None) -> list[dict]:
    """A year's headline tiles; utc_offset ('-0700', the history's) says how Plex's UTC months shift its hours."""
    y = year.get("year")
    hours, hours_note = hours_text(year.get("hours"))
    st, bm, r = year.get("longest_streak"), year.get("busiest_month"), year.get("rating") or {}
    measured = (year.get("hours") or {}).get("measured")
    utc = utc_month_note(utc_offset)
    return [
        {"label": "Films", "value": f"{year.get('films', 0):,}", "note": plural(year.get("plays", 0), "play"),
         "tip": f"{plural(year.get('films', 0), 'different film')} in {y}\n{plural(year.get('plays', 0), 'play')} "
                f"({year.get('marked', 0):,} marked as played)"},
        {"label": "Hours", "value": hours, "note": hours_note,
         "tip": (f"{measured:,} hours by Plex's playback clock\nThe films' running times add up to "
                 f"{year['hours']['runtime']:,}" + (f"\n{utc}" if utc else "") if measured is not None else
                 "Added up from the films' running times (no playback clock for this year)")},
        {"label": "New to you", "value": f"{year.get('new', 0):,}",
         "note": plural(year.get("rewatched", 0), "rewatch", "rewatches"),
         "tip": "New: no earlier play on record and nothing extra in Plex's count\nRewatch: played (or marked) "
                "before, or Plex counts more plays than it logged"},
        {"label": "Longest streak", "value": plural(st["days"], "day") if st else "-",
         "note": date_range(st["from"], st["to"], year=False) if st else "", "tip": streak_tip(st)},
        {"label": "Busiest month", "value": bm["label"] if bm else "-",
         "note": plural(bm["plays"], "play") if bm else "",
         "tip": f"{bm['label']} {y}: {plural(bm['plays'], 'play')}" if bm else ""},
        {"label": "Your average", "value": f"{r['average']:.1f}" if r.get("average") is not None else "-",
         "note": f"of {r.get('rated', 0):,} rated, out of 10" if r.get("rated") else "none of them rated",
         "tip": f"Your ratings (as they are now) of the {plural(r.get('rated', 0), 'film')} you rated among "
                f"the {plural(r.get('films', 0), 'film')} you watched in {y}"},
    ]


def month_items(months: list, mode: str = "films") -> list[dict]:
    """The whole history's months as columns: plays ('films': a film watched twice is two) or hours (Plex's
    playback clock)."""
    items = []
    for m in months:
        label = m["label"][:4] + "'" + m["label"][-2:]
        if mode == "hours":
            value = m["hours"] or 0
            tip = f"{m['label']}\n" + (f"{m['hours']:,} hours on Plex's playback clock" if m["hours"] is not None
                                       else "No playback clock for this month")
        else:
            value = m["plays"]
            films = f" of {plural(m['films'], 'film')}" if m.get("films") not in (None, m["plays"]) else ""
            tip = f"{m['label']}\n{plural(m['plays'], 'play')}{films}" + (
                f" ({m['marked']} marked as played)" if m.get("marked") else "")
            if m.get("hours") is not None:
                tip += f"\n{m['hours']:,} hours on Plex's playback clock"
        if m.get("partial"):
            tip += "\nOnly part of this month is in Plex's history"
        items.append({"label": label, "value": value, "key": m["month"], "color": pale if m.get("partial") else "BLUE",
                      "tip": tip + (f"\nClick to see it in {m['month'][:4]}'s review" if m["plays"] else ""),
                      "clickable": bool(m["plays"])})
    return items


def year_month_items(year: dict, selected: str | None = None) -> list[dict]:
    y = year.get("year")
    items = []
    for m in year.get("months", []):
        if not m["in_history"]:
            tip = f"{m['name']} {y}\nOutside Plex's history"
        else:
            tip = f"{m['name']} {y}\n{plural(m['plays'], 'play')}" + (
                f" ({m['marked']} marked as played)" if m.get("marked") else "")
            if m.get("hours") is not None:
                tip += f"\n{m['hours']:,} hours on Plex's playback clock"
            if m.get("partial"):
                tip += "\nOnly part of this month is in Plex's history"
            if m["plays"]:
                tip += "\nClick to list them"
        items.append({"label": m["label"], "value": m["plays"], "key": m["key"],
                      "color": (pale if m.get("partial") else "BLUE") if not selected or selected == m["key"]
                      else "BASELINE", "tip": tip, "clickable": bool(m["plays"])})
    return items


def top_items(year: dict, kind: str) -> list[dict]:
    """A year's top genres / decades / countries / actors / directors as bars."""
    rows = (year.get("top") or {}).get(kind, [])
    y, n = year.get("year"), year.get("top_from", 0)
    out = []
    for r in rows:
        if kind in ("actors", "directors"):
            what = "as a star (billed in the top 3)" if kind == "actors" else "directed"
            out.append({"label": r["name"], "value": r["films"], "key": r["id"],
                        "tip": f"{r['name']}\n{plural(r['films'], 'film')} {what} that you watched in {y}"
                               "\nClick to see their profile in Six Degrees"})
        elif kind == "decades":
            out.append({"label": r["label"], "value": r["films"], "key": r["decade"],
                        "tip": f"The {r['label']}\n{r['films']:,} of the {plural(n, 'film')} you watched in {y}"
                               f"\nClick for films to watch next from the {r['label']}"})
        else:
            label = SHORT_COUNTRY.get(r["label"], r["label"]) if kind == "countries" else r["label"]
            nxt = f"{r['label'].lower()} films" if kind == "genres" else "films from there"
            out.append({"label": label, "value": r["films"], "key": r["label"],
                        "tip": f"{r['label']}\n{r['films']:,} of the {plural(n, 'film')} you watched in {y}"
                               f"\nClick for {nxt} to watch next"})
    return out


def bars_height(n: int, title: bool = True) -> int:
    return max(n, 3) * BAR_ROW + 8 + (26 if title else 0)


def top_chart(p, items, title: str, on_click=None):
    """One of a year's top-5 lists as bars, or a calm note when there's nothing in it."""
    if items:
        C.bars(p, items, title=title, on_click=on_click)
        return
    top = C.header(p, 0, 0, p.width, title)
    C.message(p, "Nothing to show", "Plex has no details for these films.", box=(0, top, p.width, p.height - top))


# ---------------------------------------------------------------------------------------------------------
# Two charts of this tab's own, on the app's Painter (so they draw in the app and to PNG)
# ---------------------------------------------------------------------------------------------------------
def _heat_geometry(p, w):
    f, fs = p.font(9), p.font(8)
    label_w = max(p.text_width(d, f) for d in DAYS) + p.u(10)
    total_w = p.u(40) + p.text_width("999", fs) + p.u(6)
    cw = max(w - label_w - total_w - p.u(8), p.u(48)) / 24
    rh = min(p.u(26), max(cw * 1.05, p.u(15)))
    return label_w, total_w, cw, rh


def heatmap_height(width_px: float, s: float) -> int:
    """The height (device-independent px) that fits the heatmap at this width."""
    from .paint import Painter

    class Measure(Painter):          # sizes without a surface: Segoe UI at 9 pt is about 7 px a letter
        def text_width(self, text, font):
            return len(str(text)) * font[0] * 0.75 * self.s

        def line_height(self, font):
            return font[0] * 1.9 * self.s
    p = Measure(width_px, 100, s)
    _l, _t, _cw, rh = _heat_geometry(p, width_px)
    return int(math.ceil((7 * rh + p.u(56)) / s))


def heatmap(p, cells, films=None, *, box=None, on_click=None, selected=None, first_day=None):
    """Films started per hour of the week: 7 rows x 24 hours, one hue light to dark, each day's total at the right.
    The rows start on first_day (0: Monday, 6: Sunday; None: as Settings > Dates and times says); cells and the
    days given to on_click((day, hour)) and selected are Monday-first whatever the order drawn. films: {'d,h':
    [film...]} names a few in each square's tip."""
    x, y, w, h = C._box(p, box)
    f, fs = p.font(9), p.font(8)
    total = sum(map(sum, cells)) if cells else 0
    if not total:
        C.message(p, "No start times to show", "Plex hasn't seen you play a film through yet.", box=(x, y, w, h))
        return
    label_w, total_w, cw, rh = _heat_geometry(p, w)
    axis_h = p.line_height(fs) + p.u(6)
    rh = min(rh, (h - axis_h - p.line_height(fs) - p.u(14)) / 7)
    gap = max(1.0, p.u(2))
    vmax = max(max(r) for r in cells) or 1
    by_day = [sum(r) for r in cells]
    dmax = max(by_day) or 1
    gx = x + label_w
    for row, d in enumerate(F.week_days(first_day)):
        ry = y + row * rh
        p.text(x, ry + rh / 2, DAYS[d], f, T.INK_2, "w")
        for hr in range(24):
            n = cells[d][hr]
            cx = gx + hr * cw
            tag = f"hm{d}_{hr}"
            fill = T.HEAT_ZERO if not n else T.ramp(n / vmax, 150, 700)
            p.round_rect(cx + gap / 2, ry + gap / 2, cx + cw - gap / 2, ry + rh - gap / 2,
                         min(p.u(3), (cw - gap) / 3), fill, tag=tag)
            if selected == (d, hr):
                p.rect(cx + gap / 2, ry + gap / 2, cx + cw - gap / 2, ry + rh - gap / 2, None, outline=T.INK,
                       width=max(1, round(p.u(2))), tag=tag)
            names = [film_label(x_["title"], x_.get("year")) for x_ in reversed((films or {}).get(f"{d},{hr}", []))]
            tip = f"{DAY_NAMES[d]}s, {hour_span(hr)}\n" + (f"{plural(n, 'film')} started" if n else "No films started")
            if names:
                tip += "\ne.g. " + ", ".join(names[:3]) + (f" and {len(names) - 3} more" if len(names) > 3 else "")
            if n and on_click is not None:
                tip += "\nClick to list them"
                p.click(tag, lambda d=d, hr=hr: on_click((d, hr)))
            p.tip(tag, tip)
        # the day's total, as a short grey bar
        bx = gx + 24 * cw + p.u(8)
        bl = (total_w - p.text_width(str(dmax), fs) - p.u(12)) * by_day[d] / dmax
        tag = f"hmday{d}"
        p.rect(bx - p.u(4), ry, bx + total_w - p.u(4), ry + rh, T.SURFACE, tag=tag + " hit")
        p.bar(bx, ry + rh * 0.3, bx + bl, ry + rh * 0.7, T.BASELINE, "right", tag=tag)
        p.text(bx + bl + p.u(4), ry + rh / 2, f"{by_day[d]:,}", fs, T.INK_2, "w", tag=tag)
        p.tip(tag, f"{DAY_NAMES[d]}s\n{plural(by_day[d], 'film')} started ({by_day[d] / total:.0%})")
    # hours along the bottom: every 3 hours, or 6 when the squares are small
    ay = y + 7 * rh + p.u(4) + p.line_height(fs) / 2
    widest = max(p.text_width(hour_label(hr), fs) for hr in range(0, 24, 3))
    step = 3 if 3 * cw >= widest + p.u(6) else 6 if 6 * cw >= widest + p.u(6) else 12
    for hr in range(0, 24, step):
        p.text(gx + hr * cw + gap / 2, ay, hour_label(hr), fs, T.MUTED, "w")
    # the legend: light to dark
    ly = ay + p.line_height(fs) + p.u(6)
    lx = gx
    p.text(lx, ly, "fewer", fs, T.MUTED, "w")
    lx += p.text_width("fewer", fs) + p.u(6)
    for frac in (0, 0.2, 0.4, 0.6, 0.8, 1.0):
        p.round_rect(lx, ly - p.u(5), lx + p.u(14), ly + p.u(5), p.u(2), T.HEAT_ZERO if not frac else
                     T.ramp(frac, 150, 700))
        lx += p.u(16)
    p.text(lx + p.u(4), ly, f"more (up to {plural(vmax, 'film')} in one hour of the week)", fs, T.MUTED, "w")


def _cal_geometry(p, year, w, first_day=None):
    """(label width, the first column's first day, weeks, cell size): weeks start on first_day (0: Monday, 6:
    Sunday; None: as Settings > Dates and times says)."""
    fs = p.font(8)
    label_w = p.text_width("Wed", fs) + p.u(8)
    jan1 = date(year, 1, 1)
    first_day = F.first_weekday() if first_day is None else first_day % 7
    start = jan1 - timedelta(days=(jan1.weekday() - first_day) % 7)
    weeks = (date(year, 12, 31) - start).days // 7 + 1
    cs = min((w - label_w) / weeks, p.u(20))
    return label_w, start, weeks, cs


def calendar_height(width_px: float, s: float, year: int, first_day=None) -> int:
    from .paint import Painter

    class Measure(Painter):
        def text_width(self, text, font):
            return len(str(text)) * font[0] * 0.75 * self.s

        def line_height(self, font):
            return font[0] * 1.9 * self.s
    p = Measure(width_px, 100, s)
    _l, _s, _w, cs = _cal_geometry(p, year, width_px, first_day)
    return int(math.ceil((7 * cs + p.u(52)) / s))


def calendar(p, year: int, days: dict, first, last, streak=None, *, titles=None, box=None, on_click=None,
             selected=None, first_day=None):
    """A year day by day: weeks across, Monday to Sunday down (or Sunday to Saturday: first_day, as the heatmap),
    one hue light to dark for the films each day.
    Days outside Plex's history (before `first`, after `last`) are empty cells (EMPTY_CELL); the longest streak is
    outlined.
    days: {'YYYY-MM-DD': films}; titles: {'YYYY-MM-DD': [titles]} for the tips; on_click('YYYY-MM-DD')."""
    x, y, w, h = C._box(p, box)
    fs = p.font(8)
    label_w, start, weeks, cs = _cal_geometry(p, year, w, first_day)
    month_h = p.line_height(fs) + p.u(6)
    cs = min(cs, (h - month_h - p.line_height(fs) - p.u(14)) / 7)
    gap = max(1.0, p.u(1.5))
    first, last = _as_date(first), _as_date(last)
    vmax = max(days.values(), default=0) or 1
    s0, s1 = (_as_date(streak["from"]), _as_date(streak["to"])) if streak else (None, None)
    for d in (0, 2, 4, 6):                   # (every other row's day: Mon, Wed, Fri, Sun - or Sun, Tue, Thu, Sat)
        p.text(x, y + (d + 0.5) * cs, DAYS[(start.weekday() + d) % 7], fs, T.MUTED, "w")
    ring_w = max(1, round(p.u(1.5)))
    for wk in range(weeks):
        for d in range(7):
            day = start + timedelta(days=wk * 7 + d)
            if day.year != year:
                continue
            cx, cy = x + label_w + wk * cs, y + d * cs
            iso = day.isoformat()
            n = days.get(iso, 0)
            outside = first is None or day < first or day > last
            fill = T.EMPTY_CELL if outside else (T.HEAT_ZERO if not n else T.ramp(n / vmax, 250, 700))
            if selected and n and not selected[0] <= iso <= selected[1]:
                fill = T.mix(fill, T.WASH_BASE, 0.65)          # the days listed below stand out; the rest step back
            tag = f"day{iso}"
            box_ = (cx + gap / 2, cy + gap / 2, cx + cs - gap / 2, cy + cs - gap / 2)
            p.round_rect(*box_, min(p.u(2), cs / 4), fill, outline=T.GRID if outside else None, tag=tag)
            if s0 and s0 <= day <= s1:
                p.rect(*box_, None, outline=T.ORANGE, width=ring_w, tag=tag)
            when = f"{DAY_NAMES[day.weekday()]}, {nice_date(day)}"
            if outside:
                tip = f"{when}\nOutside Plex's history"
            elif not n:
                tip = f"{when}\nNo films"
            else:
                names = (titles or {}).get(iso, [])
                tip = f"{when}\n{plural(n, 'film')}" + (": " + ", ".join(names[:4]) if names else "") + (
                    f" and {len(names) - 4} more" if len(names) > 4 else "")
                if on_click is not None:
                    tip += "\nClick to list them"
                    p.click(tag, lambda iso=iso: on_click(iso))
            p.tip(tag, tip)
            if day.day == 1:
                p.text(cx, y + 7 * cs + p.u(4) + p.line_height(fs) / 2, MONTHS[day.month - 1], fs, T.MUTED, "w")
    # the legend
    ly = y + 7 * cs + month_h + p.line_height(fs) / 2 + p.u(4)
    lx = x + label_w
    p.text(lx, ly, "fewer", fs, T.MUTED, "w")
    lx += p.text_width("fewer", fs) + p.u(6)
    for frac in (0, 0.34, 0.67, 1.0):
        p.round_rect(lx, ly - p.u(5), lx + p.u(10), ly + p.u(5), p.u(2), T.HEAT_ZERO if not frac else
                     T.ramp(frac, 250, 700))
        lx += p.u(13)
    p.text(lx + p.u(3), ly, "more", fs, T.MUTED, "w")
    lx += p.u(3) + p.text_width("more", fs) + p.u(18)
    if streak:
        p.rect(lx, ly - p.u(5), lx + p.u(10), ly + p.u(5), T.HEAT_ZERO, outline=T.ORANGE, width=ring_w)
        text = f"your longest streak ({plural(streak['days'], 'day')})"
        p.text(lx + p.u(14), ly, text, fs, T.MUTED, "w")
        lx += p.u(14) + p.text_width(text, fs) + p.u(16)
    if first is None or first > date(year, 1, 1) or last < date(year, 12, 31):
        if lx + p.u(14) + p.text_width("outside Plex's history", fs) > x + w:
            return
        p.round_rect(lx, ly - p.u(5), lx + p.u(10), ly + p.u(5), p.u(2), T.EMPTY_CELL, outline=T.GRID)
        p.text(lx + p.u(14), ly, "outside Plex's history", fs, T.MUTED, "w")


# ---------------------------------------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------------------------------------
def _order(value):
    if value is None or value == "":
        return (1, 0.0, "")
    if isinstance(value, (int, float)):
        return (0, float(value), "")
    return (0, float("inf"), str(value).casefold())


class FilmTable(Table):
    """A Table of films that sorts each column by its real value (rows carry them in '_sort': dates shown as
    'Mar 8, 2025' sort as dates) and opens a film on double-click or Enter. Only the `stretch` columns take up
    spare width (the film's title, by default)."""

    def __init__(self, parent, columns, stretch=("film",), drop=(), **kw):
        """drop: columns to leave out, first to last, when the list is too narrow for them all; the height (in
        rows) shrinks to fit a short list, up to the height given."""
        super().__init__(parent, columns, **kw)
        self.max_rows = kw.get("height", 12)
        self.drop = list(drop)
        self.shown_columns = [c[0] for c in columns]
        self.s = T.scale(parent)
        for key, *_ in columns:
            self.tree.column(key, stretch=key in stretch)
        if drop:
            self.tree.bind("<Configure>", lambda e: self.fit_columns(e.width), add="+")

    def fit_columns(self, width: int):
        """Leave out the least useful columns when the list is too narrow to show them all."""
        if width < 40:
            return
        widths = {c[0]: c[2] for c in self.columns}
        keep = [c[0] for c in self.columns]
        for key in self.drop:
            if sum(widths[k] for k in keep) * self.s <= width:
                break
            keep.remove(key)
        if keep != self.shown_columns:
            self.shown_columns = keep
            self.tree.configure(displaycolumns=keep)

    def set_rows(self, rows, keep_sort=True):
        super().set_rows(rows, keep_sort)
        self.tree.configure(height=max(3, min(len(rows), self.max_rows)))

    def sort(self, key, reverse=None):
        if reverse is None:
            reverse = self.sorted_by == (key, False)
        items = list(self.tree.get_children())
        items.sort(key=lambda iid: _order(self.rows[iid].get("_sort", {}).get(key, self.rows[iid].get(key))),
                   reverse=reverse)
        for n, iid in enumerate(items):
            self.tree.move(iid, "", n)
            self.tree.item(iid, tags=("odd",) if n % 2 else ())
        self.sorted_by = (key, reverse)
        for k, heading, _w, _a in self.columns:
            self.tree.heading(k, text=heading + ((" ▼" if reverse else " ▲") if k == key else ""))


def _wrap(label, container, margin: int = 0):
    """Let a label wrap to its container's width."""
    def resized(event):
        if event.widget is not container:
            return
        wrap = max(event.width - margin, 120)
        try:
            if int(str(label.cget("wraplength")) or 0) != wrap:
                label.configure(wraplength=wrap)
        except tk.TclError:
            pass
    container.bind("<Configure>", resized, add="+")


def _label(choices: dict, value) -> str:
    """A drop-down's words for a value: ({'9 or more': 9, ...}, 9) -> '9 or more'."""
    return next((label for label, v in choices.items() if v == value), next(iter(choices)))


def _film_row(r: dict) -> dict:
    """The film columns every table shares."""
    label = film_label(r["title"], r.get("year"))
    if not r.get("in_library", r.get("key") is not None):
        label += " - no longer in your library"
    return {"key": r.get("key"), "title": r["title"], "year": r.get("year"), "in_library": r.get("key") is not None,
            "film": label}


# ---------------------------------------------------------------------------------------------------------
class Tab(BaseTab):
    title = "Viewing"

    def __init__(self, app, notebook):
        super().__init__(app, notebook)
        self.s = T.scale(self.frame)
        self.data: dict | None = None          # the whole answer (habits.answer) for self.catalog
        self.years: dict[int, dict] = {}       # year -> its review, as they're asked for
        self.year: int | None = None           # the year on show in Year in review
        self.year_filter = None                # (from, to, words): the year's list shows just those days
        self.cell = None                       # (day, hour): the heatmap square whose films are listed
        self._filled_for = None
        self._asking_for = None
        self._token = 0
        self._pending_year = None              # navigate(year=...) before the answer arrived
        self._year_drawn = None                # (year, filter) the year page shows
        self.columns = 0
        self.top_columns = 0
        self.timings: dict[str, float] = {}
        self.views: dict[str, ChartView] = {}
        self.cards: dict[str, Card] = {}
        self.month_mode = tk.StringVar(value="films")
        self.year_var = tk.StringVar()
        F.follow(app)                            # (dates and times as Settings > Dates and times says)
        # Forgotten favourites: the card's drop-downs start from the settings, and change them
        self.fav_rating = tk.StringVar(value=_label(RATING_CHOICES, prefs.get(app, FORGOTTEN_KEYS["rating"])))
        self.fav_age = tk.StringVar(value=_label(AGE_CHOICES, prefs.get(app, FORGOTTEN_KEYS["days"])))
        self._styles()
        self._build()
        self._show_state()

    def _styles(self):
        T.add_styles(self.frame, _styles)

    # -- layout -----------------------------------------------------------------------------------------------
    def _build(self):
        f = self.frame
        f.columnconfigure(0, weight=1)
        f.rowconfigure(1, weight=1)
        head = ttk.Frame(f, style="Page.TFrame", padding=(PAD, 12, PAD, 6))
        head.grid(row=0, column=0, sticky="ew")
        head.columnconfigure(1, weight=1)
        ttk.Label(head, text="Your viewing habits", style="PageTitle.TLabel").grid(row=0, column=0, sticky="w")
        self.hint_label = ttk.Label(head, text="", style="PageHint.TLabel")
        self.hint_label.grid(row=0, column=1, sticky="e", padx=(16, 0))

        self.placeholder = ChartView(f, height=320, background=T.PAGE)
        self.placeholder.grid(row=1, column=0, sticky="nsew")
        self.nb = ttk.Notebook(f, style="Inner.TNotebook")
        self.nb.grid(row=1, column=0, sticky="nsew", padx=PAD - 4, pady=(0, 10))
        self.habits_page = self._build_habits(self.nb)
        self.year_page = self._build_year(self.nb)
        self.nb.add(self.habits_page, text=VIEWS[0])
        self.nb.add(self.year_page, text=VIEWS[1])
        self.nb.bind("<<NotebookTabChanged>>", lambda e: self._view_changed(), add="+")

    def _card(self, parent, title=None, hint=None, padding=12) -> Card:
        card = Card(parent, title, hint, padding=padding)
        card.body.columnconfigure(0, weight=1)
        return card

    def _note(self, parent, row, style="CardNote.TLabel", card=None, pady=(0, 6)) -> ttk.Label:
        label = ttk.Label(parent, text="", style=style, justify="left", wraplength=500)
        label.grid(row=row, column=0, sticky="w", pady=pady)
        # (the card's padding and edge, 30 px, and a few more: the label's own padding, and room for a line that
        # fills its wrap length exactly - drawn, on Linux, a pixel or two wider than it measures)
        _wrap(label, card or parent, 38 if card is not None else 12)
        return label

    # Your habits ------------------------------------------------------------------------------------------------
    def _build_habits(self, parent):
        page = ScrollFrame(parent, background=T.PAGE, style="Page.TFrame")
        page.canvas.bind("<Configure>", lambda e: self._reflow(e.width), add="+")
        inner = page.inner
        inner.columnconfigure(0, weight=1)
        self.habits_scroll = page

        card = self._card(inner, padding=(14, 10))
        card.grid(row=0, column=0, sticky="ew", padx=PAD, pady=(GAP, GAP))
        self.views["tiles"] = v = ChartView(card.body, height=TILE_H)
        v.grid(row=0, column=0, sticky="ew")
        v.bind("<Configure>", lambda e: self._fit_tiles(e, "tiles"), add="+")
        self.period_label = self._note(card.body, 1, card=card, pady=(6, 0))
        self.coverage_label = self._note(card.body, 2, style="CardHint.TLabel", card=card, pady=(4, 0))
        self.cards["summary"] = card

        card = self._card(inner, "When you start films")
        card.grid(row=1, column=0, sticky="ew", padx=PAD, pady=(0, GAP))
        self.heat_words = self._note(card.body, 0, card=card)
        self.views["heatmap"] = v = ChartView(card.body, height=230)
        v.grid(row=1, column=0, sticky="ew")
        v.bind("<Configure>", self._fit_heatmap, add="+")
        self.heat_hint = self._note(card.body, 2, style="CardHint.TLabel", card=card, pady=(6, 0))
        self.cell_box = ttk.Frame(card.body, style="CardInner.TFrame")
        self.cell_box.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        self.cell_box.columnconfigure(0, weight=1)
        bar = ttk.Frame(self.cell_box, style="CardInner.TFrame")
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        self.cell_label = ttk.Label(bar, text="", style="Habits.Strong.TLabel")
        self.cell_label.pack(side="left")
        LinkLabel(bar, "close", self._clear_cell, style="CardLink.TLabel").pack(side="left", padx=(10, 0))
        self.cell_table = FilmTable(self.cell_box, [("film", "Film", 300, "w"), ("date", "Date", 110, "w"),
                                                    ("start", "Started about", 110, "w")],
                                    height=6, on_open=self._open_row)
        self.cell_table.grid(row=1, column=0, sticky="ew")
        self.cell_box.grid_remove()
        self.cards["heatmap"] = card

        card = self._card(inner, MONTH_TITLES["films"])
        card.grid(row=2, column=0, sticky="ew", padx=PAD, pady=(0, GAP))
        self.month_toggle = toggle = ttk.Frame(card.body, style="CardInner.TFrame")
        toggle.grid(row=0, column=0, sticky="w", pady=(0, 4))
        self.month_buttons = {}
        for n, (value, text) in enumerate((("films", "Plays"), ("hours", "Hours (Plex's playback clock)"))):
            rb = ttk.Radiobutton(toggle, text=text, value=value, variable=self.month_mode, style="Card.TRadiobutton",
                                 command=self._draw_months)
            rb.grid(row=0, column=n, sticky="w", padx=(0, 16))
            self.month_buttons[value] = rb
        self.views["months"] = v = ChartView(card.body, height=210)
        v.grid(row=1, column=0, sticky="ew")
        self.months_hint = self._note(card.body, 2, style="CardHint.TLabel", card=card, pady=(4, 0))
        self.cards["months"] = card

        self.card_grid = ttk.Frame(inner, style="Page.TFrame")
        self.card_grid.grid(row=3, column=0, sticky="nsew")
        self.grid_cards = []

        card = self._card(self.card_grid, "Streaks and busy spells")
        self.spells = ttk.Frame(card.body, style="CardInner.TFrame")
        self.spells.grid(row=0, column=0, sticky="nsew")
        self.spells.columnconfigure(1, weight=1)
        self.grid_cards.append(card)
        self.cards["streaks"] = card

        card = self._card(self.card_grid, "Stopped partway")
        card.body.rowconfigure(1, weight=1)
        self.stopped_note = self._note(card.body, 0, card=card)
        self.stopped_table = FilmTable(card.body, [("film", "Film", 170, "w"), ("at", "Stopped at", 112, "w"),
                                                   ("share", "How far", 56, "e"), ("when", "When", 86, "w"),
                                                   ("before", "Before", 128, "w")], height=5, on_open=self._open_row,
                                       drop=("when", "share"))
        self.stopped_table.grid(row=1, column=0, sticky="nsew")
        self.views["stopped"] = v = ChartView(card.body, height=110)     # the calm note when there's nothing
        v.grid(row=1, column=0, sticky="nsew")
        self.grid_cards.append(card)
        self.cards["stopped"] = card

        card = self._card(self.card_grid, "Most-played films")
        self.most_table = FilmTable(card.body, [("film", "Film", 260, "w"), ("plays", "Plays", 48, "e"),
                                                ("dated", "On record", 70, "e"), ("plex", "Plex's count", 84, "e"),
                                                ("dates", "Dates on record", 220, "w")],
                                    height=10, on_open=self._open_row, stretch=("film", "dates"))
        self.most_table.grid(row=0, column=0, sticky="nsew")
        self.most_note = self._note(card.body, 1, style="CardHint.TLabel", card=card, pady=(6, 0))
        self.grid_cards.append(card)
        self.cards["most"] = card

        card = self._card(self.card_grid, "Forgotten favourites")
        bar = ttk.Frame(card.body, style="CardInner.TFrame")
        bar.grid(row=0, column=0, sticky="w", pady=(0, 6))
        ttk.Label(bar, text="Rated", style="CardField.TLabel").pack(side="left")
        self.fav_rating_box = ttk.Combobox(bar, textvariable=self.fav_rating, values=list(RATING_CHOICES),
                                           state="readonly", width=10)
        self.fav_rating_box.pack(side="left", padx=(6, 14))
        ttk.Label(bar, text="Not played for", style="CardField.TLabel").pack(side="left")
        self.fav_age_box = ttk.Combobox(bar, textvariable=self.fav_age, values=list(AGE_CHOICES), state="readonly",
                                        width=9)
        self.fav_age_box.pack(side="left", padx=(6, 0))
        for box in (self.fav_rating_box, self.fav_age_box):
            box.bind("<<ComboboxSelected>>", lambda e: self._forgotten_picked(), add="+")
        self.fav_table = FilmTable(card.body, [("film", "Film", 260, "w"), ("rating", "Your rating", 80, "e"),
                                               ("last", "Last played", 200, "w"), ("plex", "Plex's count", 84, "e")],
                                   height=10, on_open=self._open_row)
        self.fav_table.grid(row=1, column=0, sticky="nsew")
        self.fav_note = self._note(card.body, 2, style="CardHint.TLabel", card=card, pady=(6, 0))
        self.grid_cards.append(card)
        self.cards["forgotten"] = card
        return page

    # Year in review ---------------------------------------------------------------------------------------------
    def _build_year(self, parent):
        page = ScrollFrame(parent, background=T.PAGE, style="Page.TFrame")
        page.canvas.bind("<Configure>", lambda e: self._reflow_tops(e.width), add="+")
        inner = page.inner
        inner.columnconfigure(0, weight=1)
        self.year_scroll = page

        bar = ttk.Frame(inner, style="Page.TFrame", padding=(PAD, GAP, PAD, GAP))
        bar.grid(row=0, column=0, sticky="ew")
        bar.columnconfigure(4, weight=1)
        ttk.Label(bar, text="Year", style="Habits.Page.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.year_box = ttk.Combobox(bar, textvariable=self.year_var, state="readonly", width=7)
        self.year_box.grid(row=0, column=1, sticky="w")
        self.year_box.bind("<<ComboboxSelected>>", lambda e: self._year_picked(), add="+")
        self.prev_btn = ttk.Button(bar, text="←", style="Small.TButton", command=lambda: self._step_year(-1))
        self.prev_btn.grid(row=0, column=2, sticky="w", padx=(8, 2))
        self.next_btn = ttk.Button(bar, text="→", style="Small.TButton", command=lambda: self._step_year(1))
        self.next_btn.grid(row=0, column=3, sticky="w")
        self.year_span_label = ttk.Label(bar, text="", style="PageHint.TLabel")
        self.year_span_label.grid(row=0, column=4, sticky="e", padx=(16, 0))

        card = self._card(inner, padding=(14, 10))
        card.grid(row=1, column=0, sticky="ew", padx=PAD, pady=(0, GAP))
        self.year_head = ttk.Label(card.body, text="", style="Habits.Head.TLabel")
        self.year_head.grid(row=0, column=0, sticky="w")
        self.year_sub = self._note(card.body, 1, style="CardHint.TLabel", card=card, pady=(0, 6))
        self.views["year_tiles"] = v = ChartView(card.body, height=TILE_H)
        v.grid(row=2, column=0, sticky="ew")
        v.bind("<Configure>", lambda e: self._fit_tiles(e, "year_tiles"), add="+")
        self.first_last = ttk.Frame(card.body, style="CardInner.TFrame")
        self.first_last.grid(row=3, column=0, sticky="ew", pady=(6, 0))
        self.compare_label = self._note(card.body, 4, card=card, pady=(6, 0))
        self.cards["year_summary"] = card

        card = self._card(inner, "Month by month")
        card.grid(row=2, column=0, sticky="ew", padx=PAD, pady=(0, GAP))
        self.views["year_months"] = v = ChartView(card.body, height=190)
        v.grid(row=0, column=0, sticky="ew")
        self.cards["year_months"] = card

        card = self._card(inner, "Day by day")
        card.grid(row=3, column=0, sticky="ew", padx=PAD, pady=(0, GAP))
        self.views["calendar"] = v = ChartView(card.body, height=180)
        v.grid(row=0, column=0, sticky="ew")
        v.bind("<Configure>", self._fit_calendar, add="+")
        self.cards["calendar"] = card

        card = self._card(inner, "What you watched")
        card.grid(row=4, column=0, sticky="ew", padx=PAD, pady=(0, GAP))
        self.tops_note = self._note(card.body, 0, style="CardHint.TLabel", card=card)
        self.tops_grid = ttk.Frame(card.body, style="CardInner.TFrame")
        self.tops_grid.grid(row=1, column=0, sticky="ew")
        self.top_views = {}
        for kind in ("genres", "decades", "countries", "actors", "directors"):
            self.views[f"top_{kind}"] = self.top_views[kind] = ChartView(self.tops_grid, height=bars_height(5))
        self.cards["tops"] = card

        card = self._card(inner, "Every film you watched")
        card.grid(row=5, column=0, sticky="ew", padx=PAD, pady=(0, GAP))
        self.list_card = card
        bar = ttk.Frame(card.body, style="CardInner.TFrame")
        bar.grid(row=0, column=0, sticky="w", pady=(0, 6))
        self.list_label = ttk.Label(bar, text="", style="CardNote.TLabel")
        self.list_label.pack(side="left")
        self.show_all_link = LinkLabel(bar, "show them all", self._clear_filter, style="CardLink.TLabel")
        self.show_all_link.pack(side="left", padx=(10, 0))
        self.plays_table = FilmTable(card.body, [("date", "Date", 120, "w"), ("film", "Film", 300, "w"),
                                                 ("how", "How", 130, "w"), ("rewatch", "Rewatch", 70, "w"),
                                                 ("rating", "Your rating", 80, "e")], height=14,
                                     on_open=self._open_row)
        self.plays_table.grid(row=1, column=0, sticky="nsew")
        self.year_foot = self._note(card.body, 2, style="CardHint.TLabel", card=card, pady=(6, 0))
        self.cards["plays"] = card
        return page

    # -- layout as the window changes -----------------------------------------------------------------------------
    def _reflow(self, width: int | None = None):
        """Two cards to a row when there's room, one when there isn't."""
        if not width:
            width = self.habits_scroll.canvas.winfo_width()
        if width < 50 and self.columns:
            return
        cols = 2 if width / self.s >= TWO_COLUMNS_FROM else 1
        if cols == self.columns:
            return
        self.columns = cols
        for c in range(2):
            self.card_grid.columnconfigure(c, weight=1 if c < cols else 0, uniform="cards" if c < cols else "")
        # streaks and stopped-partway side by side; the two lists full width, where their columns have room
        places = [(0, 0, 1), (0, 1, 1), (1, 0, 2), (2, 0, 2)] if cols == 2 else [(n, 0, 1) for n in range(4)]
        for card, (row, col, span) in zip(self.grid_cards, places):
            padx = (PAD, PAD) if span == 2 or cols == 1 else (PAD, GAP // 2) if col == 0 else (GAP // 2, PAD)
            card.grid(row=row, column=col, columnspan=span, sticky="nsew", padx=padx, pady=(0, GAP))

    def _reflow_tops(self, width: int | None = None):
        if not width:
            width = self.year_scroll.canvas.winfo_width()
        if width < 50 and self.top_columns:
            return
        dip = width / self.s
        cols = 3 if dip >= 1100 else 2 if dip >= 640 else 1
        if cols == self.top_columns:
            return
        self.top_columns = cols
        for c in range(3):
            self.tops_grid.columnconfigure(c, weight=1 if c < cols else 0, uniform="tops" if c < cols else "")
        for n, view in enumerate(self.top_views.values()):
            row, col = divmod(n, cols)
            view.grid(row=row, column=col, sticky="ew", padx=(0 if col == 0 else GAP, 0), pady=(0, GAP))

    def _fit_tiles(self, event, key):
        width = event.width
        if width < 50:
            return
        per_row = max(1, min(6, int(width // (TILE_MIN_W * self.s))))
        want = int(math.ceil(6 / per_row) * TILE_H * self.s)
        view = self.views[key]
        if int(view.cget("height")) != want:
            view.configure(height=want)

    def _fit_heatmap(self, event=None):
        view = self.views["heatmap"]
        width = event.width if event is not None else view.winfo_width()
        if width < 50:
            return
        want = int(heatmap_height(width, self.s) * self.s)
        if int(view.cget("height")) != want:
            view.configure(height=want)

    def _fit_calendar(self, event=None):
        view = self.views["calendar"]
        width = event.width if event is not None else view.winfo_width()
        if width < 50 or self.year is None:
            return
        want = int(calendar_height(width, self.s, self.year) * self.s)
        if int(view.cget("height")) != want:
            view.configure(height=want)

    # -- called by the main window --------------------------------------------------------------------------------
    def catalog_changed(self, catalog, state: str):
        super().catalog_changed(catalog, state)
        self._token += 1                            # whatever was being worked out is for the old collection
        self.data = None
        self.years = {}
        self.year = None
        self.year_filter = self.cell = None
        self._year_drawn = None
        self._filled_for = self._asking_for = None
        self._show_state()
        if self.state_message() is None and self._visible():
            self._fill()

    def shown(self):
        if (self.state_message() is None and self._filled_for is not self.catalog
                and self._asking_for is not self.catalog):
            self._fill()

    def keep(self) -> dict:
        """(Laid out again for a new text size:) the year in review on show, if it's that view that is, and what
        the months card counts (plays or hours)."""
        return {"year": self.year if self._current_view() == VIEWS[1] else None, "months": self.month_mode.get()}

    def restore(self, kept: dict):
        if kept.get("months") in MONTH_TITLES:
            self.month_mode.set(kept["months"])             # (plays again if this history has no playback clock)
            if self.data is not None:
                self._draw_months()
        if kept.get("year") is not None:
            self._pending_year = kept["year"]               # (opened once the history has been read)

    def navigate(self, year=None, **_ignored):
        """year=2025 (or '2025'): that year's review; nothing: Your habits, from the top."""
        if year not in (None, ""):
            try:
                year = int(str(year).strip())
            except ValueError:
                year = None
        if year is None or year == "":
            self._pending_year = None
            self.shown()
            if self.data is not None:
                self.nb.select(self.habits_page)
                self.habits_scroll.to_top()
            return
        if self.data is None or self._filled_for is not self.catalog:
            self._pending_year = year               # opened once the history has been read
            self.shown()
            return
        self._open_year(year)

    def preference_changed(self, key: str, value):
        """Settings > Dates and times: write every date and time again (and a new first day of the week reorders
        the heatmap and the calendar, and asks for the busiest week again). Settings > Viewing: the Forgotten
        favourites card's drop-downs follow, and it asks again."""
        if key in ("date_style", "clock"):
            self._redraw_words()
        elif key == "week_start":
            self._redraw_words()
            self._ask_streaks()
        elif key in FORGOTTEN_KEYS.values():
            var, choices = ((self.fav_rating, RATING_CHOICES) if key == FORGOTTEN_KEYS["rating"]
                            else (self.fav_age, AGE_CHOICES))
            label = _label(choices, value)
            if var.get() != label:               # (a change on the card itself is already there: asked for)
                var.set(label)
                self._ask_forgotten()

    def _redraw_words(self):
        """Dates and times are written again in the style now chosen (the answer is the same: no asking)."""
        data = self.data
        if data is None or self.catalog is None or self._filled_for is not self.catalog:
            return
        self.hint_label.configure(text=history_hint(data.get("history"), data.get("owner") or self.catalog.owner))
        if data.get("empty"):
            return
        cell = self.cell
        self._draw_habits()
        if cell is not None:                        # (the squares' films stay listed)
            self._cell_clicked(cell)
        flt = self.year_filter                      # (the year's list of one day, or a week: said again)
        if flt and flt[0] == flt[1]:
            self.year_filter = (flt[0], flt[1], nice_date(flt[0]))
        elif flt and flt[2].startswith("the week of "):
            part = flt[2].partition(" (its ")
            self.year_filter = (flt[0], flt[1], f"the week of {nice_date(flt[0])}{part[1]}{part[2]}")
        self._year_drawn = None                     # (the year page is drawn again when it's next shown)
        if self._current_view() == VIEWS[1] and self.year in self.years:
            self._draw_year(self.years[self.year])

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
            self.hint_label.configure(text="")

    def _show_placeholder(self, text: str, sub: str | None = None):
        self.nb.grid_remove()
        self.placeholder.grid()
        self.show_message(self.placeholder, text, sub)

    def _show_content(self):
        self.placeholder.grid_remove()
        self.nb.grid()

    def _fill(self):
        """Read the play history in the background (a zipped backup has to be unpacked again: seconds), then
        draw Your habits."""
        catalog = self.catalog
        if catalog is None:
            return
        self._token += 1
        token = self._token
        self._asking_for = catalog
        started = time.perf_counter()
        if self._filled_for is not catalog:
            self._show_placeholder("Reading your play history...")
        # (read on this thread: work() mustn't touch Tk)
        request = dict(self._forgotten_request(), week_start=prefs.get(self.app, "week_start"))

        def work():
            from .. import habits
            return habits.answer(catalog, request)

        def done(tab, answer):
            if token != tab._token or catalog is not tab.catalog:
                return                               # the collection changed meanwhile
            tab._asking_for = None
            tab.timings["ask"] = time.perf_counter() - started
            tab._apply(answer, catalog)

        def failed(tab, message):
            if token != tab._token:
                return
            tab._asking_for = None
            tab._show_placeholder("Couldn't read your play history", message)

        self._run(work, done, failed, status="Reading your play history...", key="habits.answer")

    def _run(self, work, done, failed=None, status=None, key=None):
        """app.run, with done(tab, result) and failed(tab, message) given this tab - which the job holds only
        weakly, so a job ending after the tab has gone (the window closing) is never what lets go of it: its Tk
        variables may only be let go of on the window's thread. work() mustn't touch the tab at all."""
        me = weakref.ref(self)

        def on_done(result):
            tab = me()
            if tab is not None:
                done(tab, result)

        def on_failed(message):
            tab = me()
            if tab is not None and failed is not None:
                failed(tab, message)
        return self.app.run(work, on_done, on_failed if failed is not None else None, status=status, key=key)

    def _forgotten_request(self) -> dict:
        return {"min_rating": RATING_CHOICES.get(self.fav_rating.get(), 9),
                "forgotten_days": AGE_CHOICES.get(self.fav_age.get(), 365), "count": 200}

    def _forgotten_picked(self):
        """A drop-down on the Forgotten favourites card: kept as the setting (Settings > Viewing), and asked for."""
        if prefs.settings_of(self.app) is not None:
            for key, var, choices in ((FORGOTTEN_KEYS["rating"], self.fav_rating, RATING_CHOICES),
                                      (FORGOTTEN_KEYS["days"], self.fav_age, AGE_CHOICES)):
                if var.get() in choices:
                    try:
                        prefs.set(self.app, key, choices[var.get()])
                    except (ValueError, TypeError):
                        pass
        self._ask_forgotten()

    def _apply(self, answer: dict, catalog):
        self._filled_for = catalog
        if not answer.get("ok"):
            self._show_placeholder("Couldn't read your play history",
                                   answer.get("error") or "Try reloading the database on the Export tab.")
            return
        self.data = answer
        self.hint_label.configure(text=history_hint(answer.get("history"), answer.get("owner") or catalog.owner))
        if answer.get("empty"):
            self._show_placeholder("No plays on record yet", answer.get("note", "") + " Play a film through (or "
                                   "mark it as played) and it shows up here after the next backup.")
            return
        if answer.get("year"):
            self.years[answer["year"]["year"]] = answer["year"]
        self.year_box.configure(values=[str(y["year"]) for y in reversed(answer.get("years", []))])
        self._show_content()
        started = time.perf_counter()
        self._draw_habits()
        self.timings["draw"] = time.perf_counter() - started
        if self._pending_year is not None:
            year, self._pending_year = self._pending_year, None
            self._open_year(year)
        elif self._current_view() == VIEWS[1]:
            self._show_year(self.year or self._latest_year())

    # Your habits ------------------------------------------------------------------------------------------------
    def _draw_habits(self):
        data = self.data or {}
        owner = data.get("owner") or (self.catalog.owner if self.catalog is not None else "")
        tiles = history_tiles(data)
        self.views["tiles"].show(lambda p: C.tiles(p, tiles))
        period, notes = coverage_text(data.get("history"), owner)
        self.period_label.configure(text=period)
        self.coverage_label.configure(text=notes)
        hm = data.get("heatmap") or {}
        self.heat_words.configure(text=heatmap_words(hm))
        left = hm.get("left_out", 0)
        self.heat_hint.configure(
            text="Each square is an hour of the week; the stronger the colour, the more films started then. Plex logs "
                 "a play when you pass about 90% of a film, so start times are worked back from each film's running "
                 "time (one sitting assumed: pauses aren't known)."
                 + (f" {plural(left, 'play')} with no start time - marked as played, or with no running time - "
                    "aren't in it." if left else "") + " Click a square to list its films.")
        self._draw_heatmap()
        self._clear_cell()
        has_hours = any(m.get("hours") is not None for m in data.get("months", []))
        if not has_hours:                           # no playback clock: films are all there is to show
            self.month_mode.set("films")
            self.month_toggle.grid_remove()
        else:
            self.month_toggle.grid()
        utc = utc_month_note((data.get("history") or {}).get("utc_offset"))
        self.months_hint.configure(
            text="Plays count a film each time you watch it. Fainter columns: only part of the month is in Plex's "
                 "history. Hours are Plex's own playback clock (what actually played, stopped films too)."
                 + (f" {utc}" if utc else "") + " Click a month to see it in that year's review."
            if has_hours else "Plays count a film each time you watch it. Fainter columns: only part of the month is "
                              "in Plex's history. Click a month to see it in that year's review.")
        self._draw_months()
        self._draw_spells()
        self._fill_stopped()
        self._fill_most()
        self._fill_forgotten()
        self._reflow()

    def _draw_heatmap(self):
        hm = (self.data or {}).get("heatmap") or {}
        cells, films, selected = hm.get("cells"), hm.get("films"), self.cell
        self.views["heatmap"].show(lambda p: heatmap(p, cells, films, on_click=self._cell_clicked,
                                                     selected=selected))

    def _cell_clicked(self, cell):
        self.cell = tuple(cell)
        d, hr = self.cell
        films = ((self.data or {}).get("heatmap") or {}).get("films", {}).get(f"{d},{hr}", [])
        rows = []
        for r in reversed(films):                   # the latest first
            start = r.get("start") or r.get("at")
            rows.append(dict(_film_row(r), date=nice_date(start), start=nice_time(start),
                             _sort={"date": start, "start": start[11:] if start else None}))
        self.cell_label.configure(text=f"{plural(len(rows), 'film')} started on {DAY_NAMES[d]}s, {hour_span(hr)}")
        self.cell_table.set_rows(rows)
        self.cell_box.grid()
        self._draw_heatmap()

    def _clear_cell(self):
        was = self.cell
        self.cell = None
        self.cell_box.grid_remove()
        if was is not None:
            self._draw_heatmap()

    def _draw_months(self):
        months = (self.data or {}).get("months", [])
        mode = self.month_mode.get()
        self.cards["months"].title_label.configure(text=MONTH_TITLES.get(mode, MONTH_TITLES["films"]))
        items = month_items(months, mode)
        ticks = whole_ticks if mode == "films" else C.compact
        self.views["months"].show(lambda p: C.columns(p, items, on_click=self._month_clicked, value_fmt=C.fmt_int,
                                                      tick_fmt=ticks)
                                  if items else C.message(p, "No months to show"))

    def _month_clicked(self, key):
        year, month = map(int, str(key).split("-"))
        last = (date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1))
        self._open_year(year, (date(year, month, 1).isoformat(), last.isoformat(),
                               f"{MONTH_NAMES[month - 1]} {year}"))

    def _draw_spells(self):
        from .film import Flow        # (what doesn't fit beside the rest goes on a line of its own, never cut off)
        for child in self.spells.winfo_children():
            child.destroy()
        st = (self.data or {}).get("streaks") or {}
        row = 0

        def line(label, text, command=None, link="list them"):
            nonlocal row
            ttk.Label(self.spells, text=label, style="Habits.Strong.TLabel").grid(row=row, column=0, sticky="nw",
                                                                                  padx=(0, 12), pady=(0, 4))
            cell = Flow(self.spells, gap=10, style="CardInner.TFrame")
            cell.grid(row=row, column=1, sticky="ew", pady=(0, 4))
            cell.add(ttk.Label(cell, text=text, style="CardNote.TLabel"))
            if command is not None:
                cell.add(LinkLabel(cell, link, command, style="CardLink.TLabel"))
            row += 1

        longest = st.get("longest")
        if longest:
            line("Longest streak", f"{plural(longest['days'], 'day')} in a row with a film: "
                                   f"{date_range(longest['from'], longest['to'])}",
                 lambda: self._open_range(longest["from"], longest["to"], "your longest streak"))
            if longest.get("marked_only"):             # (a line of its own: the card is often half the page wide)
                ttk.Label(self.spells, text=streak_marks(longest) + ".", style="CardHint.TLabel").grid(
                    row=row, column=1, sticky="w", pady=(0, 4))
                row += 1
        day = st.get("busiest_day")
        if day:
            line("Busiest day", f"{nice_date(day['date'])}: {plural(day['plays'], 'play')}")
            for film in day.get("films", []):
                cell = Flow(self.spells, gap=8, style="CardInner.TFrame")
                cell.grid(row=row, column=1, sticky="ew")
                cell.add(self._film_link(cell, film))
                when = nice_time(film.get("at"))
                if film.get("how") == "marked":        # the time it was ticked, not a viewing
                    when += ", marked as played"
                cell.add(ttk.Label(cell, text=when, style="CardHint.TLabel"))
                row += 1
            ttk.Frame(self.spells, style="CardInner.TFrame", height=int(4 * self.s)).grid(row=row, column=1)
            row += 1
        week = st.get("busiest_week")
        if week:
            line("Busiest week", f"{date_range(week['from'], week['to'])}: {plural(week['plays'], 'play')}",
                 lambda: self._open_range(week["from"], week["to"], f"the week of {nice_date(week['from'])}"))
        month = st.get("busiest_month")
        if month:
            line("Busiest month", f"{month['label']}: {plural(month['plays'], 'play')}",
                 lambda: self._month_clicked(month["month"]))
        if not row:
            ttk.Label(self.spells, text="No dated plays yet.", style="CardNote.TLabel").grid(row=0, column=0)

    def _film_link(self, parent, film: dict):
        label = film_label(film["title"], film.get("year"))
        if not film.get("key"):
            return ttk.Label(parent, text=label + " (no longer in your library)", style="CardNote.TLabel")
        return LinkLabel(parent, label, lambda f=dict(film): self._open_row(f), style="CardLink.TLabel")

    def _fill_stopped(self):
        rows = []
        for r in (self.data or {}).get("stopped", []):
            at = C.clock(r["at_sec"])
            # the plays on record and those Plex counted with no date (as the most-played list counts them)
            before = (r.get("plays_before") or 0) + (r.get("undated_before") or 0)
            rows.append(dict(_film_row(r), at=f"{at} of {C.clock(r['runtime_sec'])}" if r.get("runtime_sec") else at,
                             share=f"{r['share']:.0%}" if r.get("share") is not None else "",
                             when=nice_date(r["stopped"]) if r.get("stopped") else "unknown",
                             before=("never finished" if not before else "played once before" if before == 1
                                     else f"played {before} times before"),
                             _sort={"at": r["at_sec"], "share": r.get("share"), "when": r.get("stopped"),
                                    "before": before}))
        self.stopped_table.set_rows(rows)
        if rows:
            self.stopped_note.configure(text=f"{plural(len(rows), 'film')} you started and stopped, with no finish "
                                             "since. Plex keeps a resume point until you finish a film or mark it "
                                             "played; peeks under 2 minutes and stops in the last 10 minutes aren't "
                                             "counted.")
            self.stopped_note.grid()
            self.stopped_table.grid()
            self.views["stopped"].grid_remove()
        else:
            self.stopped_note.grid_remove()
            self.stopped_table.grid_remove()
            self.views["stopped"].grid()
            self.views["stopped"].show(lambda p: C.message(
                p, "Nothing left half-watched",
                "Plex keeps a resume point when you stop a film partway, and forgets it once you finish the film or "
                "mark it played - none of your films has one right now."))

    def _fill_most(self):
        data = self.data or {}
        rows = []
        for r in data.get("most_played", []):
            rows.append(dict(_film_row(r), plays=r["plays"], dated=r["dated"],
                             plex="" if r.get("plex_count") is None else r["plex_count"],
                             dates=", ".join(short_date(d) for d in r.get("dates", [])) or "no dated plays",
                             _sort={"dates": (r.get("dates") or [None])[-1]}))
        self.most_table.set_rows(rows)
        total = data.get("most_played_total", 0)
        self.most_note.configure(
            text=(f"{plural(total, 'film')} played twice or more. " if total else "No film played twice yet. ")
            + "Plays = plays on record + plays Plex counts with no date (films marked as played that it never "
              "logged, or marked in bulk). Plex counts each edition of a film on its own, so ticking off several "
              "cuts adds to its count; here that counts once. Plex's count also drops when you mark a film "
              "unplayed. Double-click a film to open it.")

    def _ask_streaks(self):
        """The busiest week again, for weeks starting on another day (the rest of the answer doesn't change)."""
        catalog = self.catalog
        if catalog is None:
            return
        if self.data is None:
            if self._asking_for is catalog:
                self._fill()                        # (the answer on its way has the old weeks: ask again)
            return
        if self.data.get("empty"):
            return
        request = {"parts": ["streaks"], "week_start": prefs.get(self.app, "week_start")}
        token = self._token

        def work():
            from .. import habits
            return habits.answer(catalog, request)

        def done(tab, answer):
            if token != tab._token or catalog is not tab.catalog or not answer.get("ok") or tab.data is None:
                return
            tab.data["streaks"] = answer.get("streaks")
            tab._draw_spells()

        self._run(work, done, key="habits.streaks")

    def _ask_forgotten(self):
        catalog = self.catalog
        if catalog is None or self.data is None:
            return
        request = dict(self._forgotten_request(), parts=["forgotten"])
        token = self._token

        def work():
            from .. import habits
            return habits.answer(catalog, request)

        def done(tab, answer):
            if token != tab._token or catalog is not tab.catalog or not answer.get("ok") or tab.data is None:
                return
            for k in ("forgotten", "forgotten_total", "forgotten_rule"):
                tab.data[k] = answer.get(k)
            tab._fill_forgotten()

        self._run(work, done, key="habits.forgotten")

    def _fill_forgotten(self):
        data = self.data or {}
        first = (data.get("history") or {}).get("first")
        rows = []
        marks = False
        for r in data.get("forgotten") or []:
            if r.get("last_played"):
                last = nice_date(r["last_played"])
            elif r.get("bulk_marked"):
                last = f"marked in bulk {nice_date(r['bulk_marked'])}"
                marks = True
            elif r.get("plex_marked"):                   # counted by Plex, never logged: when it was ticked
                last = f"marked as played {nice_date(r['plex_marked'])}"
                marks = True
            elif r.get("plex_count"):
                last = "played, no date"
            else:
                last = "no play on record"
            rows.append(dict(_film_row(r), rating=rating_text(r["rating"]), last=last, plex=r.get("plex_count", 0),
                             _sort={"rating": r["rating"], "last": r.get("last_played") or ""}))
        self.fav_table.set_rows(rows)
        rule = data.get("forgotten_rule") or {}
        total = data.get("forgotten_total", 0)
        since = nice_date(rule.get("since"))
        what = f"rated {rating_text(rule.get('min_rating'))}" + (" or more" if (rule.get("min_rating") or 10) < 10
                                                                  else "")
        self.fav_note.configure(
            text=(f"{plural(total, 'film')} you {what}" if total else f"No films {what}")
            + f" with no play on record since {since}. Plex's history starts {nice_date(first)} - earlier plays "
              "have no date"
            + ("; a 'marked' date is when a film was ticked as played, not when you watched it" if marks else "")
            + ". Your ratings are out of 10.")

    # Year in review ---------------------------------------------------------------------------------------------
    def _current_view(self) -> str:
        try:
            return self.nb.tab(self.nb.select(), "text")
        except tk.TclError:
            return VIEWS[0]

    def _view_changed(self):
        if self._current_view() == VIEWS[1] and self.data is not None and not self.data.get("empty"):
            self._show_year(self.year or self._latest_year())

    def _latest_year(self) -> int | None:
        years = (self.data or {}).get("years") or []
        return years[-1]["year"] if years else None

    def _year_list(self) -> list[int]:
        return [y["year"] for y in (self.data or {}).get("years") or []]

    def _open_year(self, year: int, date_filter=None):
        """Year in review for `year` (the nearest year with plays if it has none), optionally showing just some
        days in its list."""
        years = self._year_list()
        if not years:
            return
        if year not in years:
            nearest = min(years, key=lambda y: (abs(y - year), -y))
            h = (self.data or {}).get("history") or {}
            self.app.set_status(f"No plays on record in {year} - Plex's history covers {nice_date(h.get('first'))}"
                                f" - {nice_date(h.get('as_of'))}. Showing {nearest}.")
            year, date_filter = nearest, None
        self.year_filter = date_filter
        self._year_drawn = None
        if self._current_view() != VIEWS[1]:
            self.year = year                      # (so the tab change shows this year, not the last one)
            self.nb.select(self.year_page)
        self._show_year(year)
        self.year_scroll.to_top()

    def _open_range(self, lo: str, hi: str, words: str):
        year = _as_date(lo).year
        if _as_date(hi).year != year:                   # (a streak over New Year: this year's part of it)
            words += f" (its {year} part)"
        self._open_year(year, (lo, hi, words))
        if self.year_filter:
            self._scroll_to_list()

    def _scroll_to_list(self):
        try:
            self.year_scroll.update_idletasks()
            total = self.year_scroll.inner.winfo_height()
            if total > 0:
                self.year_scroll.canvas.yview_moveto(self.list_card.winfo_y() / total)
        except tk.TclError:
            pass

    def _year_picked(self):
        try:
            year = int(self.year_var.get())
        except ValueError:
            return
        self.year_filter = None
        self._show_year(year)

    def _step_year(self, step: int):
        years = self._year_list()
        if self.year not in years:
            return
        i = years.index(self.year) + step
        if 0 <= i < len(years):
            self.year_filter = None
            self._show_year(years[i])

    def _show_year(self, year: int | None):
        """Draw a year's review, asking for it first if it hasn't been worked out yet."""
        if year is None or self.catalog is None:
            return
        flt = self.year_filter
        if flt and not (flt[0] <= f"{year}-12-31" and flt[1] >= f"{year}-01-01"):
            self.year_filter = None                 # days of another year: nothing of this one to list
        self.year = year
        self.year_var.set(str(year))
        years = self._year_list()
        before = [y for y in years if y < year]
        after = [y for y in years if y > year]
        self.prev_btn.configure(text=f"←  {before[-1]}" if before else "←")          # where each one goes
        self.next_btn.configure(text=f"{after[0]}  →" if after else "→")
        self.prev_btn.state(["!disabled"] if before else ["disabled"])
        self.next_btn.state(["!disabled"] if after else ["disabled"])
        if year in self.years:
            if self._year_drawn != (year, self.year_filter):
                self._draw_year(self.years[year])
            return
        catalog, token = self.catalog, self._token

        def work():
            from .. import habits
            return habits.answer(catalog, {"year": year, "parts": ["year"]})

        def done(tab, answer):
            if token != tab._token or catalog is not tab.catalog:
                return
            if not answer.get("ok"):
                tab.app.set_status(answer.get("error") or f"Couldn't put {year} together.")
                return
            tab.years[year] = answer["year"]
            if tab.year == year:
                tab._draw_year(answer["year"])

        def failed(tab, message):
            if token == tab._token:
                tab.app.set_status(f"Couldn't put {year} together: {message}")

        self._run(work, done, failed, status=f"Putting {year} together...", key="habits.year")

    def _draw_year(self, yr: dict):
        started = time.perf_counter()
        data = self.data or {}
        h = data.get("history") or {}
        y = yr["year"]
        self._year_drawn = (y, self.year_filter)
        head, sub = year_headline(yr, h)
        self.year_head.configure(text=head)
        self.year_sub.configure(text=sub)
        self.year_span_label.configure(text=f"Plex's history: {date_range(h.get('first'), h.get('as_of'))}"
                                       if h.get("first") else "")
        tiles = year_tiles(yr, h.get("utc_offset"))
        self.views["year_tiles"].show(lambda p: C.tiles(p, tiles))
        self._draw_first_last(yr)
        has_previous = (y - 1) in self._year_list()
        self.compare_label.configure(text=compare_words(yr.get("vs_previous"), y, h, has_previous))
        selected = self.year_filter
        month_key = None
        if selected and selected[0][:7] == selected[1][:7] and selected[0].endswith("-01"):
            month_key = selected[0][:7]
        items = year_month_items(yr, month_key)
        inside = {m["key"] for m in yr.get("months", []) if m["in_history"]}     # (no '0' over months outside it)
        self.views["year_months"].show(lambda p: C.columns(p, items, label_values="emphasis", emphasis=inside,
                                                           on_click=self._year_month_clicked, tick_fmt=whole_ticks))
        self._draw_calendar(yr)
        if yr.get("top_from"):
            self.tops_note.configure(
                text=f"Among the {plural(yr['top_from'], 'film')} you watched in {y}"
                     + (" that are still in your library" if yr.get("gone_plays") else "")
                     + " (each counts once, however often you watched it). Stars are billed in the top 3. Click a "
                       "bar to take it further.")
            self.tops_grid.grid()
        else:
            self.tops_note.configure(
                text=f"The films you watched in {y} are no longer in your library, so there are no genres, decades, "
                     "countries or cast to show." if yr.get("plays") else f"No films on record in {y}.")
            self.tops_grid.grid_remove()
        titles = {"genres": "Genres", "decades": "Decades", "countries": "Countries",
                  "actors": "Stars (billed in the top 3)", "directors": "Directors"}
        clicks = {"genres": self._open_genre, "decades": self._open_decade, "countries": self._open_country,
                  "actors": self._open_actor, "directors": self._open_director}
        for kind, view in self.top_views.items():
            items_k = top_items(yr, kind)
            view.show(lambda p, items_k=items_k, kind=kind: top_chart(p, items_k, titles[kind], clicks[kind]),
                      height=bars_height(len(items_k)))
        self._reflow_tops()
        self._fill_plays(yr)
        self.timings["year"] = time.perf_counter() - started

    def _draw_first_last(self, yr: dict):
        for child in self.first_last.winfo_children():
            child.destroy()
        y = yr["year"]
        so_far = (self.data or {}).get("history", {}).get("as_of", "") == yr.get("to") and \
            not str(yr.get("to", "")).endswith("12-31")
        for n, (which, words) in enumerate((("first", f"First film of {y}:"),
                                            ("last", f"Last film {'so far' if so_far else f'of {y}'}:"))):
            film = yr.get(which)
            if not film:
                continue
            row = ttk.Frame(self.first_last, style="CardInner.TFrame")
            row.grid(row=n, column=0, sticky="w")
            ttk.Label(row, text=words, style="CardField.TLabel").pack(side="left", padx=(0, 6))
            self._film_link(row, film).pack(side="left")
            when = f"  {F.moment(film['at'])}"
            if film.get("how") == "marked":
                when = f"  {nice_date(film['at'])} (marked as played)"
            ttk.Label(row, text=when, style="CardHint.TLabel").pack(side="left")

    def _draw_calendar(self, yr: dict):
        y = yr["year"]
        days = dict(yr.get("days") or {})
        titles = {}
        for r in yr.get("plays_list", []):
            titles.setdefault(r["at"][:10], []).append(film_label(r["title"], r.get("year")))
        first, last, streak = yr.get("from"), yr.get("to"), yr.get("longest_streak")
        selected = self.year_filter
        self._fit_calendar()
        self.views["calendar"].show(lambda p: calendar(p, y, days, first, last, streak, titles=titles,
                                                       on_click=self._day_clicked, selected=selected))

    def _showing(self, year: int) -> bool:
        """Is `year` the year drawn, and the one asked for? Not while the next year is being worked out: its
        predecessor's charts are still on screen (and clickable) until it arrives."""
        drawn = self._year_drawn
        return drawn is not None and drawn[0] == year == self.year and year in self.years

    def _year_month_clicked(self, key):
        year, month = map(int, str(key).split("-"))
        if not self._showing(year):
            return
        last = date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1)
        rng = (date(year, month, 1).isoformat(), last.isoformat(), MONTH_NAMES[month - 1])
        self.year_filter = None if self.year_filter == rng else rng        # a second click shows them all again
        self._draw_year(self.years[year])

    def _day_clicked(self, iso: str):
        if not self._showing(_as_date(iso).year):
            return
        rng = (iso, iso, nice_date(iso))
        self.year_filter = None if self.year_filter == rng else rng
        self._draw_year(self.years[self.year])

    def _clear_filter(self):
        if self.year_filter is not None and self.year in self.years:
            self.year_filter = None
            self._draw_year(self.years[self.year])

    def _fill_plays(self, yr: dict):
        y = yr["year"]
        flt = self.year_filter
        rows = []
        for r in yr.get("plays_list", []):
            day = r["at"][:10]
            if flt and not flt[0] <= day <= flt[1]:
                continue
            rows.append(dict(_film_row(r), date=F.moment(r["at"], year=False),
                             how="Played" if r["how"] == "played" else "Marked as played",
                             rewatch="yes" if r.get("rewatch") else "", rating=rating_text(r.get("rating")),
                             _sort={"date": r["at"], "rating": r.get("rating"),
                                    "rewatch": 1 if r.get("rewatch") else 0}))
        self.plays_table.set_rows(rows)
        self.list_card.title_label.configure(text=f"Every film you watched in {y}")
        if flt:
            self.list_label.configure(text=f"Only {flt[2]}: {plural(len(rows), 'play')}")
            self.show_all_link.pack(side="left", padx=(10, 0))
        else:
            self.list_label.configure(text=plural(len(rows), "play") + " - the latest last. Double-click one to "
                                                                        "open the film.")
            self.show_all_link.pack_forget()
        marked, bulk = yr.get("marked", 0), yr.get("bulk_left_out", 0)
        first = nice_date((self.data or {}).get("history", {}).get("first"))
        runtime = (yr.get("hours") or {}).get("runtime")
        bits = []
        if marked:
            bits.append(f"{plural(marked, 'play was', 'plays were')} marked as played rather than played through "
                        "Plex.")
        if bulk:
            bits.append(f"{plural(bulk, 'play')} marked in bulk {'is' if bulk == 1 else 'are'} left out.")
        bits.append(f"New = no earlier play on record and nothing extra in Plex's count; Plex's history starts "
                    f"{first}, so a film you saw before then and Plex never counted shows as new.")
        if (yr.get("hours") or {}).get("measured") is not None:
            bits.append(f"Hours are Plex's own playback clock; the films' running times add up to {runtime:,}.")
            utc = utc_month_note(((self.data or {}).get("history") or {}).get("utc_offset"))
            if utc:
                bits.append(utc)
        else:
            bits.append("Hours are added up from the films' running times.")
        bits.append("Your average uses your ratings as they are now: Plex keeps no rating history.")
        if yr.get("gone_plays"):
            bits.append(f"{plural(yr['gone_plays'], 'play')} of films deleted since count under the title Plex "
                        "logged; they aren't in the top lists.")
        self.year_foot.configure(text=" ".join(bits))

    # -- clicks: over to the other tabs ---------------------------------------------------------------------------
    def _goto(self, tab: str, **kwargs):
        if self.app.goto(tab, **kwargs) is None:
            self.app.set_status(f"The {tab} tab isn't available.")

    def _open_row(self, row: dict):
        """A film: its page. A film deleted since has none."""
        label = film_label(row.get("title", ""), row.get("year"))
        if not row.get("key"):
            self.app.set_status(f"{label} is no longer in your library, so there's no page for it.")
            return
        self._goto("Film", film_key=row["key"], title=label)

    def _open_genre(self, genre):
        self._goto("Watch Next", genre=genre)

    def _open_decade(self, decade):
        self._goto("Watch Next", decade=int(decade))

    def _open_country(self, country):
        self._goto("Watch Next", country=country)

    def _person_name(self, pid) -> str:
        yr = self.years.get(self.year) or {}
        for kind in ("actors", "directors"):
            for r in (yr.get("top") or {}).get(kind, []):
                if r["id"] == pid:
                    return r["name"]
        return ""

    def _open_actor(self, person_id):
        self._goto("Six Degrees", person_id=person_id, name=self._person_name(person_id))

    def _open_director(self, person_id):
        self._goto("Six Degrees", person_id=person_id, name=self._person_name(person_id), directors=True)
