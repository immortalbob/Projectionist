"""How the app writes dates and times: one style for every tab, chosen on the Settings tab (Dates and times).

    nice_date(d)        'Mar 3, 2026'      '3 Mar 2026'      '2026-03-03'    (year=False: 'Mar 3', '3 Mar', '03-03')
    date_range(a, b)    'Mar 3-9, 2026'    '3-9 Mar 2026'    '2026-03-03 to 2026-03-09'
    short_date(d)       "Mar 3 '26"        "3 Mar '26"       '2026-03-03'
    nice_time(t)        '9:14 pm'  or  '21:14'
    hour_label(h)       '9 pm', 'noon', 'midnight'  or  '21:00', '12:00', '00:00'
    hour_span(h, n)     '9-10 pm', '11 am - 1 pm'  or  '21:00-22:00', '23:00-24:00'
    moment(t)           'Mar 3, 2026, 9:14 pm'  '3 Mar 2026, 21:14'  '2026-03-03 21:14' (just the date when there's
                        no time of day)
    week_days()         the weekdays (0 = Monday) in the order a week is drawn: Monday first, or Sunday first

They're plain functions of a date (or an ISO string) and this module's STYLE, which follow(app) sets from the
settings - the window's tabs call it as they're built, and each of these settings calls it when it changes; tests
and PNG renderers use use() / styled(). Only what's shown changes: the backbones' answers and the spreadsheet keep
ISO dates (2026-03-03).
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime

from . import prefs

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
DATE_STYLES = [("mdy", "Mar 3, 2026"), ("dmy", "3 Mar 2026"), ("iso", "2026-03-03")]
CLOCKS = [("12h", "12-hour (9:14 pm)"), ("24h", "24-hour (21:14)")]
WEEK_STARTS = [("monday", "Monday"), ("sunday", "Sunday")]
FIRST_DAY = {"monday": 0, "sunday": 6}           # date.weekday() of the week's first day
DEFAULTS = {"date": "mdy", "clock": "12h", "week": "monday"}
STYLE = dict(DEFAULTS)                            # the style in use
KEYS = {"date": "date_style", "clock": "clock", "week": "week_start"}      # (the settings behind it)
_ALLOWED = {"date": [v for v, _l in DATE_STYLES], "clock": [v for v, _l in CLOCKS],
            "week": [v for v, _l in WEEK_STARTS]}


# ---------------------------------------------------------------------------------------------------------
# The style
# ---------------------------------------------------------------------------------------------------------
def use(date: str | None = None, clock: str | None = None, week: str | None = None):     # noqa: A002
    """Write dates, times and weeks this way from now on (None: as they are). ValueError for a style there isn't."""
    changes = {k: v for k, v in (("date", date), ("clock", clock), ("week", week)) if v is not None}
    for part, value in changes.items():
        if value not in _ALLOWED[part]:
            raise ValueError(f"{part}: {value!r} isn't one of {_ALLOWED[part]}")
    STYLE.update(changes)


def reset():
    STYLE.clear()
    STYLE.update(DEFAULTS)


def follow(source=None):
    """Use the style the settings ask for (source: the app, a settings dict, or None for the defaults)."""
    use(**{part: prefs.get(source, key) for part, key in KEYS.items()})


@contextmanager
def styled(**style):
    """with styled(date='dmy', clock='24h'): ... - for tests and pictures; the style is put back after."""
    before = dict(STYLE)
    use(**style)
    try:
        yield
    finally:
        STYLE.clear()
        STYLE.update(before)


def _changed(app, _value):
    follow(app)


# The settings (Settings > Dates and times). The tabs redraw on preference_changed.
prefs.section("Dates and times", order=15,
              hint="How dates and times are written on every tab. The spreadsheet always writes dates as 2026-03-03.")
prefs.define("date_style", "Dates and times", "Dates", kind="choice", default=DEFAULTS["date"], choices=DATE_STYLES,
             apply=_changed, help="How dates are written in the lists, notes and charts' tips.")
prefs.define("clock", "Dates and times", "Times of day", kind="choice", default=DEFAULTS["clock"], choices=CLOCKS,
             apply=_changed, help="How times are written: when a film was started or played, and the hours along "
                                  "the Viewing tab's charts.")
prefs.define("week_start", "Dates and times", "Weeks start on", kind="choice", default=DEFAULTS["week"],
             choices=WEEK_STARTS, apply=_changed,
             help="The Viewing tab's weeks: the top row of 'When you start films' and of the year's calendar, and "
                  "the busiest week.")


# ---------------------------------------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------------------------------------
def as_date(value) -> date | None:
    """A date from a date, a datetime or an ISO string ('2026-03-03', '2026-03-03T21:14'); None for nothing."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def nice_date(value, year: bool = True) -> str:
    """'Mar 3, 2026' / '3 Mar 2026' / '2026-03-03' (without the year: 'Mar 3' / '3 Mar' / '03-03')."""
    d = as_date(value)
    if d is None:
        return ""
    style, month = STYLE["date"], MONTHS[d.month - 1]
    if style == "iso":
        return d.isoformat() if year else f"{d.month:02d}-{d.day:02d}"
    if style == "dmy":
        return f"{d.day} {month} {d.year}" if year else f"{d.day} {month}"
    return f"{month} {d.day}, {d.year}" if year else f"{month} {d.day}"


def date_range(a, b, year: bool = True) -> str:
    """'Dec 27, 2025 - Jan 1, 2026', 'Nov 10-16, 2025', 'Mar 26 - Apr 5, 2026' - or '10-16 Nov 2025', or
    '2025-11-10 to 2025-11-16' (without the year: 'Nov 10-16', '10-16 Nov', '11-10 to 11-16')."""
    a, b = as_date(a), as_date(b)
    if a is None or b is None:
        return nice_date(a or b, year)
    if a == b:
        return nice_date(a, year)
    style = STYLE["date"]
    if style == "iso":                            # (a hyphen between dates full of hyphens reads badly)
        return f"{nice_date(a, year)} to {nice_date(b, year)}"
    if a.year != b.year:
        return f"{nice_date(a, year)} - {nice_date(b, year)}"
    if style == "dmy":
        tail = f" {b.year}" if year else ""
        if a.month == b.month:
            return f"{a.day}-{b.day} {MONTHS[a.month - 1]}{tail}"
        return f"{nice_date(a, False)} - {nice_date(b, False)}{tail}"
    tail = f", {b.year}" if year else ""
    if a.month == b.month:
        return f"{MONTHS[a.month - 1]} {a.day}-{b.day}{tail}"
    return f"{nice_date(a, False)} - {nice_date(b, False)}{tail}"


def short_date(value) -> str:
    """Compact, for lists of dates: "Jul 17 '25" / "17 Jul '25" / '2025-07-17'."""
    d = as_date(value)
    if d is None:
        return ""
    style = STYLE["date"]
    if style == "iso":
        return d.isoformat()
    if style == "dmy":
        return f"{d.day} {MONTHS[d.month - 1]} '{d.year % 100:02d}"
    return f"{MONTHS[d.month - 1]} {d.day} '{d.year % 100:02d}"


# ---------------------------------------------------------------------------------------------------------
# Times of day
# ---------------------------------------------------------------------------------------------------------
def time_text(hour: int, minute: int = 0) -> str:
    """17, 32 -> '5:32 pm' / '17:32'."""
    hour %= 24
    if STYLE["clock"] == "24h":
        return f"{hour:02d}:{minute:02d}"
    return f"{hour % 12 or 12}:{minute:02d} {'am' if hour < 12 else 'pm'}"


def nice_time(value) -> str:
    """'5:32 pm' / '17:32' from an ISO date and time ('2026-01-01T17:32') or a datetime."""
    if not value:
        return ""
    t = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return time_text(t.hour, t.minute)


def hour_label(h: int) -> str:
    """0 -> 'midnight', 12 -> 'noon', 17 -> '5 pm' - or '00:00', '12:00', '17:00'."""
    h %= 24
    if STYLE["clock"] == "24h":
        return f"{h:02d}:00"
    if h == 0:
        return "midnight"
    if h == 12:
        return "noon"
    return f"{(h - 1) % 12 + 1} {'am' if h < 12 else 'pm'}"


def hour_span(h: int, hours: int = 1) -> str:
    """'6-7 pm', '11 am - 1 pm', '10 pm - midnight' - or '18:00-19:00', '22:00-24:00'."""
    a, b = h % 24, (h + hours) % 24
    if STYLE["clock"] == "24h":
        return f"{a:02d}:00-{b or 24:02d}:00"
    la, lb = hour_label(a), hour_label(b)
    if a < b and la[-2:] == lb[-2:] and la[-2:] in ("am", "pm"):
        return f"{la[:-3]}-{lb}"
    return f"{la} - {lb}"


def moment(value, year: bool = True) -> str:
    """A date and time: 'Mar 3, 2026, 9:14 pm' / '3 Mar 2026, 21:14' / '2026-03-03 21:14' - just the date for a
    value with no time of day ('2026-03-03')."""
    if value is None or value == "":
        return ""
    day = nice_date(value, year)
    has_time = isinstance(value, datetime) or (isinstance(value, str) and "T" in value)
    if not has_time:
        return day
    return day + (" " if STYLE["date"] == "iso" else ", ") + nice_time(value)


# ---------------------------------------------------------------------------------------------------------
# Weeks
# ---------------------------------------------------------------------------------------------------------
def first_weekday() -> int:
    """The week's first day as date.weekday() counts: 0 (Monday) or 6 (Sunday)."""
    return FIRST_DAY.get(STYLE["week"], 0)


def week_days(first: int | None = None) -> list[int]:
    """The weekdays (0 = Monday) in the order a week is drawn, from `first` (default: the style's first day)."""
    first = first_weekday() if first is None else first % 7
    return [(first + i) % 7 for i in range(7)]
