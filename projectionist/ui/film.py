"""The Film tab - everything about one film on one page - and the search box above the main window's tabs.

The page (projectionist.filmpage.film_page does the work):
  - the film: title and other titles, year, running time, content rating, genres and countries (click one for
    Watch Next), tagline and summary, directors, studio, collections, libraries, your copies, when it was added
  - how it's rated: your rating beside IMDb, TMDb and Rotten Tomatoes' critics and audience, all on one 0-10
    scale; a film you haven't rated gets Watch Next's prediction instead
  - you and this film: your plays (from the viewing-history backbone when it's there, else Plex's play count)
  - stay after the credits? the Credits tab's verdict and a timeline for each copy
  - cast and director(s) in billing order: double-click someone for Six Degrees
  - similar films, not seen yet and ones you've rated (Watch Next's likeness): double-click to open one here
  - what critics said, your copies' files and library-health issues - once those backbones exist
The film, its ratings, credits scenes and people come at once (a fraction of a millisecond to work out); the rest
arrive from background jobs a moment later. Back and Forward (Alt+Left / Alt+Right) step through the pages opened
here, as in a browser; with no film chosen the tab shows a start page (films opened lately, a few suggestions).

The search box (GlobalSearch) sits above the tabs: Ctrl+F or Ctrl+K goes to it, and as you type a drop-down lists
the films, people, collections, genres, countries, studios, libraries and critics that match (filmpage.search:
accents aside, typos forgiven, a few milliseconds a keystroke). Enter or a click opens the match where it's most
useful: a film here, a person in Six Degrees, a genre, country, collection or library in Watch Next, a studio's films
here, a critic in Watch Next's Your critics.
"""

from __future__ import annotations

from datetime import datetime
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

from .. import filmpage as FP
from .. import formats
from .. import prefs
from . import charts
from . import credits as CR
from . import theme as T
from .base import BaseTab
from .paint import Painter
from .widgets import Card, ChartView, LinkLabel, ScrollFrame, Table, keyboard_link

PAD = 16                      # page margin (as on the other tabs)
GAP = 12                      # space between cards
TWO_COLUMNS_FROM = 940        # page width (device-independent px) from which half-width cards pair up
HEADER_SPLIT_FROM = 820       # header card width from which the facts sit beside the summary
SIDE_BY_SIDE_FROM = 820       # similar-films card width from which its two lists sit side by side
HISTORY_MAX = 50              # pages kept for Back / Forward
RECENT_MAX = 12               # films on the start page's 'Recently opened', unless the Settings tab says otherwise

# This tab's settings (the Settings tab lists the shown ones)
prefs.section("Film", order=28, hint="The Film tab's start page.")
RECENT_KEEP = 30              # films kept in the list (the most the start page can be set to show)
prefs.define("film_recent_count", "Film", "Recently opened films to list", kind="number", default=RECENT_MAX,
             minimum=0, maximum=RECENT_KEEP, step=1, unit="films",
             help="How many of the films you've opened the start page lists. 0 keeps no list: the films you open "
                  "aren't added, and the list is forgotten when the app closes.")
prefs.define("film_recent", "Film", "Recently opened films", kind="list", default=[], shown=False)
LEAVE_OUT = "watchnext_leave_out"   # Settings > Watch Next's 'Never suggest films from' (ui/watchnext.py): the
                                    # start page's 'Well rated, not seen yet' leaves those films out too
SEEN_BY = "seen_accounts"           # Settings > Your collection (projectionist/catalog.py): films played on those
                                    # accounts count as seen here too
CAST_ROWS = 12                # the cast list's height, in rows (it scrolls beyond)
RATING_ROW = 26               # one row of the ratings chart
PAGE_HINT = "Everything about one film. Alt+← / Alt+→ go back and forward."
START_TITLE = "Every film, one page"
LOADING_SIMILAR = "Finding similar films..."
SIMILAR_HINT = ("Films sharing its people, studio, genres and collections (rarer ones count for more) - the likeness "
                "Watch Next uses; 1 would be the same people, studio and genres. It knows nothing of plot or tone. "
                "Not seen yet is in Watch Next's order (likeness, then the rating it expects you'd give); You've "
                "rated is by likeness. Double-click one to open it here.")
RATINGS_HINT = ("Every score on the same 0-10 scale (Rotten Tomatoes' percentages divided by 10). IMDb, TMDb and "
                "Rotten Tomatoes are the scores Plex last fetched.")
CAST_HINT = "In billing order. Double-click someone to see them in Six Degrees."
MARKED = {"bulk": "marked as played in bulk", "marked": "marked as played"}
READING_PLAYS = "Reading your plays..."
_UNSET = object()
# Library-health severities: colour, icon, word - the Library Doctor's colours and words (ui/doctor.py's
# SEVERITY_COLOR and SEVERITY_WORDS), so an issue looks the same wherever you meet it
SEVERITY = T.live(lambda: {"critical": (T.CRITICAL, "!", "To fix"), "fix": (T.SERIOUS, "!", "To fix"),
                           "serious": (T.SERIOUS, "!", "To fix"), "check": (T.WARNING, "?", "To check"),
                           "warning": (T.WARNING, "?", "To check"), "info": (T.BLUE, "i", "For info")})
PLAYS_NEAR_HOURS = 3          # Plex's last-played moment this close to a play on record is that play
CRITICS_HINT = ("Closest: where the critic stands among your {closest} critics most in step with you - how much "
                "more often than a typical critic they agree with you, allowing for how often they say Fresh (Watch "
                "Next > Your critics). Agrees with you: how often their Fresh or Rotten matched your own rating, on "
                "the films you've both judged (left blank under {min} films - too few to tell). Only the reviews "
                "Plex keeps - at most 20 a film. Double-click a critic for the films they'd pick for you in Watch "
                "Next.")


# ---------------------------------------------------------------------------------------------------------
# Words and numbers
# ---------------------------------------------------------------------------------------------------------
def _when(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def day_text(value) -> str:
    """'2026-03-03' or '2026-03-03T21:14' -> 'Mar 3, 2026' - in the style Settings > Dates and times chooses, as
    every tab writes dates (formats.nice_date)."""
    d = _when(value)
    return formats.nice_date(d) if d else (str(value) if value else "")


def clock_text(d: datetime) -> str:
    """21:14 -> '9:14 pm' or '21:14' (Settings > Dates and times, as every tab writes times)."""
    return formats.time_text(d.hour, d.minute)


def moment_text(value) -> str:
    """'2026-03-03T21:14' -> 'Mar 3, 2026, 9:14 pm' ('Mar 3, 2026' when there's no time of day) - in the style
    Settings > Dates and times chooses (formats.moment)."""
    d = _when(value)
    if d is None:
        return str(value) if value else ""
    return formats.moment(d if "T" in str(value) else d.date())


def runtime_text(minutes) -> str:
    """117 -> '1 h 57 min', 45 -> '45 min', 120 -> '2 h'."""
    if not minutes:
        return ""
    h, m = divmod(int(minutes), 60)
    return f"{h} h {m} min" if h and m else f"{h} h" if h else f"{m} min"


def plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n:,} {one if n == 1 else (many or one + 's')}"


def meta_line(film: dict) -> str:
    """'1979  ·  1 h 57 min  ·  R  ·  4K'."""
    bits = [str(film.get("year") or ""), runtime_text(film.get("runtime_min")), film.get("content_rating") or "",
            film.get("resolution") or ""]
    return "  ·  ".join(b for b in bits if b)


def rating_rows(ratings: dict, prediction: dict | None = None) -> list[dict]:
    """The ratings chart's rows, yours (or Watch Next's prediction for a film you haven't rated) first, each with
    its value in its own units and a tooltip."""
    ratings = ratings or {}
    rows = []
    for r in ratings.get("rows") or []:
        if r.get("out_of_10") is None:
            continue
        row = dict(r)
        text = r.get("text") or (f"{r['value']:g}%" if r.get("scale") == 100 else f"{r['value']:g} / 10")
        if r.get("source") == "rt_critic" and r.get("verdict"):
            text += f"  {r['verdict']}"
        row.update(text=text, tip=_rating_tip(r, ratings))
        rows.append(row)
    p = (prediction or {}).get("predicted_rating")
    if ratings.get("yours") is None and p is not None:
        tip = f"Not rated yet: from your ratings Watch Next expects about {p:.1f}"
        e = prediction.get("expected_from_scores")
        if e is not None:
            tip += f" (its scores alone suggest {e:.1f})"
        tip += "\nA model's estimate - Watch Next's Accuracy view says how close it usually gets"
        rows.insert(0, {"source": "predicted", "label": "Predicted for you", "value": p, "scale": 10,
                        "out_of_10": p, "text": f"{p:.1f} / 10", "tip": tip})
    return rows


def _rating_tip(r: dict, ratings: dict) -> str:
    s, v = r.get("source"), r.get("value")
    if s == "you":
        tip = f"You gave it {v:g} out of 10"
        if r.get("earlier_edition") or ratings.get("yours_from_earlier_edition"):
            tip += ("\nRated on an earlier edition - Plex shows this copy as unrated; rate it again in Plex to "
                    "carry the rating over")
        elif float(v).is_integer():
            stars = v / 2
            tip += f" ({stars:g} {'star' if stars == 1 else 'stars'} in Plex)"
        else:
            tip += "\n(the average of your ratings of its copies)"
        if ratings.get("your_average") is not None:
            tip += f"\nYour average: {ratings['your_average']:.1f}, over {ratings.get('rated_films', 0):,} films"
        return tip
    if s in ("imdb", "tmdb"):
        return f"{'IMDb' if s == 'imdb' else 'TMDb'} users: {v:g} out of 10\nAs Plex last fetched it"
    if s == "rt_critic":
        verdict = r.get("verdict") or FP.fresh(v)
        rule = "60% or more" if verdict == "Fresh" else "under 60%"
        return (f"Rotten Tomatoes critics: {v:g}% liked it - {verdict} ({rule})\n"
                f"Drawn at {v / 10:g} on the 0-10 scale; as Plex last fetched it")
    if s == "rt_audience":
        return (f"Rotten Tomatoes audience: {v:g}% liked it\n"
                f"Drawn at {v / 10:g} on the 0-10 scale; as Plex last fetched it")
    return f"{r.get('label', s)}: {r.get('text', v)}"


def ratings_note(ratings: dict, prediction: dict | None = None) -> str:
    """The ratings in words: yours against IMDb's, or Watch Next's expectation for a film you haven't rated, and
    which scores Plex hasn't got."""
    ratings = ratings or {}
    note = ratings.get("note") or ""
    p = (prediction or {}).get("predicted_rating")
    if ratings.get("yours") is None and p is not None:
        avg = prediction.get("your_average") or ratings.get("your_average")
        said = f"You haven't rated it. From your ratings, Watch Next expects about {p:.1f} out of 10"
        said += f" (your average is {avg:.1f})." if avg is not None else "."
        note = note.replace("You haven't rated it.", said)
    return note


def ratings_height(n: int) -> int:
    """Device-independent pixels the ratings chart needs for n rows (and its scale)."""
    return max(n, 1) * RATING_ROW + 30


def draw_ratings(p: Painter, rows: list[dict]):
    """Each score on one shared 0-10 track, its value in its own units at the right ('8 / 10', '93%  Fresh'):
    yours in blue, Watch Next's prediction in a fainter blue, everyone else's in grey. Hairline gridlines at 0, 2,
    ... 10 under the bars; every row has a tooltip."""
    if not rows:
        charts.message(p, "No scores for this film", "Plex has no IMDb, TMDb or Rotten Tomatoes score for it, and "
                                                      "you haven't rated it.")
        return
    f, fb, fs = p.font(9), p.font(9, "bold"), p.font(8)
    mine = ("you", "predicted")
    label_w = min(max(p.text_width(r["label"], fb if r["source"] in mine else f) for r in rows) + p.u(14),
                  p.width * 0.45)
    value_w = max(p.text_width(r["text"], fb) for r in rows) + p.u(12)
    x0 = label_w
    w = max(p.width - label_w - value_w - p.u(4), p.u(40))
    row_h, thick, top = p.u(RATING_ROW), p.u(12), p.u(2)
    base = top + len(rows) * row_h
    for n, r in enumerate(rows):                       # the rows' hover areas, under everything
        p.rect(0, top + n * row_h, p.width, top + (n + 1) * row_h, T.SURFACE, tag=f"rating{n} hit")
    for t in range(0, 11, 2):                          # hairline grid, then the scale
        x = x0 + w * t / 10
        p.line([(x, top), (x, base)], T.GRID, 1)
        p.text(x, base + p.u(9), str(t), fs, T.MUTED, "center")
    for n, r in enumerate(rows):
        tag = f"rating{n}"
        cy = top + (n + 0.5) * row_h
        ours = r["source"] in mine
        color = T.BLUE if r["source"] == "you" else T.RAMP[250] if r["source"] == "predicted" else T.BASELINE
        value = min(max(float(r["out_of_10"]), 0.0), 10.0)
        p.text(0, cy, p.fit(r["label"], fb if ours else f, label_w - p.u(10)), fb if ours else f,
               T.INK if ours else T.INK_2, "w", tag=tag)
        p.bar(x0, cy - thick / 2, x0 + w, cy + thick / 2, T.NEUTRAL, "right", tag=tag + " hit")
        p.bar(x0, cy - thick / 2, x0 + w * value / 10, cy + thick / 2, color, "right", tag=tag)
        p.text(x0 + w + p.u(8), cy, r["text"], fb if ours else f, T.INK, "w", tag=tag)
        if r.get("tip"):
            p.tip(tag, r["tip"])


def history_lines(history: dict) -> dict:
    """What 'You and this film' says, from the viewing-history backbone's answer when it has one (else Plex's
    play count): {summary, rating, plays: [line], more, resume, year, notes: [line]}."""
    history = history or {}
    base = history.get("catalog") or history
    out = {"plays": [], "more": 0, "resume": "", "year": None, "notes": []}
    rating = history.get("rating", base.get("rating"))
    out["rating"] = f"Your rating: {rating:g} out of 10" if rating is not None else "Not rated by you"
    listed = history.get("plays") if isinstance(history.get("plays"), list) else None
    if history.get("available") and history.get("ok", True) and listed is not None:
        listed = [p for p in listed if isinstance(p, dict)]
        real = [p for p in listed if (p.get("how") or "played") == "played" and (p.get("at") or p.get("when"))]
        marked = [p for p in listed if (p.get("how") or "played") != "played"]
        dated = [p for p in listed if p.get("at") or p.get("when")]
        count = history.get("plays_on_record", len(real))
        last = history.get("last_played") or ((real[-1].get("at") or real[-1].get("when")) if real else None)
        plex = history.get("plex_count")
        if count:
            out["summary"] = f"Played {plural(count, 'time')}" + (f" - last on {day_text(last)}" if last else "")
        elif marked:
            out["summary"] = "Marked as played - no viewing of it on record"
        elif isinstance(plex, int) and plex > 0:
            out["summary"] = "Counted as played - with no date on record"
        else:
            out["summary"] = "Not played yet"
        for p in reversed(dated[-6:]):
            when = p.get("at") or p.get("when")
            line = moment_text(when)
            if p.get("device"):
                line += f"  ·  {p['device']}"
            if (p.get("how") or "played") != "played":
                line += f"  ·  {MARKED.get(p.get('how'), 'marked as played')}"
            out["plays"].append(line)
        out["more"] = max(len(dated) - 6, 0)
        if marked:
            out["notes"].append("Marked as played: Plex was told you'd seen it rather than seeing it play, so "
                                "those dates are when it was marked.")
        undated = history.get("undated_plays")
        if isinstance(undated, int) and not isinstance(undated, bool):
            # (the backbone's own count of plays Plex has but never logged, a film's editions counted once: Plex
            # counts each edition on its own, so ticking off several cuts at once reads as several plays there)
            extra, total = max(undated, 0), len(listed) + max(undated, 0)
        elif isinstance(plex, int) and plex > len(listed):
            extra, total = plex - len(listed), plex
        else:
            extra, total = 0, plex
        # Plex's own count can be higher than the plays counted here: it counts each edition's plays on its own
        # (ticking off several cuts, or marking a second cut after watching one, adds to it), and a play it logged
        # twice counts twice there
        inflated = isinstance(plex, int) and isinstance(total, int) and plex > total
        items = base.get("library_items") if base is not history else None
        several = isinstance(items, int) and items > 1
        why_more = ("" if not inflated else
                    f"Plex's own count is {plex}, as it counts each edition on its own." if several else
                    f"Plex's own count is {plex}: it logged a play more than once, counted once here.")
        if extra > 0:
            start = history.get("history_from")
            plex_last = base.get("last_played") if base is not history else None
            lead = (f"Plex counts {plural(total, 'play')} in all" if not inflated else
                    f"Counting its editions once: {plural(total, 'play')} in all" if several else
                    f"{plural(total, 'play')} in all")
            per_edition = f" {why_more}" if why_more else ""
            if _after_history_starts(plex_last, start, dated):
                # (Plex's own last-played moment is later than its history starts and isn't a play on record: so
                # not all the undated plays are from before then)
                which = ("it isn't" if total == 1 else "none of them is" if extra >= total else
                         "one of them isn't" if extra == 1 else f"{extra} of them aren't")
                out["notes"].append(f"{lead}, the last on {moment_text(plex_last)} - {which} in the play history "
                                    "(marked as played, perhaps)." + per_edition)
            else:
                out["notes"].append(f"{lead} - {'one' if extra == 1 else extra} with no record of when: marked as "
                                    "played, or from before its play history starts"
                                    + (f" ({day_text(start)})." if start else ".") + per_edition)
        elif why_more and (count or marked):
            out["notes"].append(why_more)          # (every play is on record: only why Plex's number is bigger)
        resume = history.get("resume") or ({"at_sec": history["resume_at_sec"]} if history.get("resume_at_sec")
                                           else None)
        if resume:
            at = resume.get("at") or charts.clock(resume.get("at_sec") or 0)
            share = resume.get("share")
            out["resume"] = (f"You stopped at {at}" + (f" ({share:.0%} of the way through)" if share else "")
                             + (f" on {day_text(resume['stopped'])}" if resume.get("stopped") else "")
                             + " - Plex will offer to carry on from there.")
        years = [_when(p.get("at") or p.get("when")) for p in real]
        years = [d.year for d in years if d]
        out["year"] = years[-1] if years else None
    else:
        plays, last = base.get("plays") or 0, base.get("last_played")
        if isinstance(plays, list):
            plays = len(plays)
        out["summary"] = (f"Played {plural(plays, 'time')}" + (f" - last on {day_text(last)}" if last else "")
                          if plays else "Not played yet")
        out["notes"].append(base.get("note") or FP.PLAYS_NOTE)
        if history.get("available") and history.get("ok") is False and history.get("error"):
            out["notes"].append(f"The viewing history couldn't be read: {history['error']}")
    return out


def _after_history_starts(plex_last, start, dated) -> bool:
    """Plex's last-played moment falls after the day its play history starts, and more than PLAYS_NEAR_HOURS from
    every play on record - so it's a play (or a 'mark as played') the history doesn't have."""
    last, begun = _when(plex_last), _when(start)
    if last is None or begun is None or last.date() <= begun.date():
        return False
    for p in dated:
        when = _when(p.get("at") or p.get("when"))
        try:
            if when is not None and abs((when - last).total_seconds()) <= PLAYS_NEAR_HOURS * 3600:
                return False
        except TypeError:                            # (one with a time zone, one without: can't tell)
            return False
    return True


def _critics_setting(name: str, default: int) -> int:
    """One of the critics backbone's numbers (MIN_SHARED: films you and a critic must both have judged before
    their agreement with you says anything; CLOSEST: how many are 'your closest critics')."""
    try:
        from .. import critics
        return int(getattr(critics, name, default))
    except Exception:                                # (not built, or broken: its own card says so)
        return default


def copy_lines(copy: dict) -> dict:
    """One copy's files in words: {title, video, audio, subtitles, paths, warning}."""
    edition = copy.get("edition") or "Regular edition"
    title = "  ·  ".join(x for x in (edition, copy.get("library") or "") if x)
    size = copy.get("size") or (f"{copy['size_gb']:,.1f} GB" if copy.get("size_gb") is not None else "")
    hdr = copy.get("hdr") if copy.get("hdr") not in (None, "", "SDR") else ""
    bits = [" ".join(x for x in (copy.get("resolution") or "", copy.get("video_codec") or "", hdr) if x),
            (copy.get("container") or "").upper(), size,
            f"{copy['bitrate_mbps']:g} Mb/s" if copy.get("bitrate_mbps") else "",
            runtime_text(round(copy["duration_min"])) if copy.get("duration_min") else ""]
    video = "  ·  ".join(b for b in bits if b)
    audio = copy.get("audio") or []
    langs = copy.get("audio_languages") or list(dict.fromkeys(a.get("language") or "Unknown" for a in audio))
    first = next((a for a in audio if a.get("default")), audio[0] if audio else None)
    audio_text = ", ".join(langs) if langs else "none listed"
    if audio:
        fmt = (first or {}).get("format") or (first or {}).get("codec") or ""
        audio_text += (f"  ({plural(len(audio), 'track')}" +
                       (f"; the default is {first.get('language') or 'unknown'} {fmt}".rstrip() if first else "") + ")")
    subs = copy.get("subtitles") or []
    sub_langs = copy.get("subtitle_languages") or list(dict.fromkeys(s.get("language") or "Unknown" for s in subs))
    sub_text = ", ".join(sub_langs) if sub_langs else "none"
    if subs:
        forced = sum(1 for s in subs if s.get("forced"))
        external = sum(1 for s in subs if s.get("external"))
        extra = [plural(len(subs), "track")] + ([f"{forced} forced"] if forced else []) + \
                ([f"{external} in separate files"] if external else [])
        sub_text += f"  ({', '.join(extra)})"
    paths = []
    for f in copy.get("files") or []:
        paths.append(f.get("path") if isinstance(f, dict) else str(f))
    warning = ""
    if copy.get("unavailable"):
        warning = "Plex can't find this file" + (f" (since {day_text(copy['unavailable_since'])})"
                                                 if copy.get("unavailable_since") else "")
    return {"title": title, "video": video, "audio": f"Audio: {audio_text}", "subtitles": f"Subtitles: {sub_text}",
            "paths": [p for p in paths if p], "warning": warning}


def result_line(result: dict) -> tuple[str, str]:
    """A search result as the drop-down shows it: (label, detail)."""
    return str(result.get("label") or result.get("id") or ""), str(result.get("detail") or "")


# ---------------------------------------------------------------------------------------------------------
# Small widgets
# ---------------------------------------------------------------------------------------------------------
def _styles(st):
    """This tab's own styles, in the look in use (theme.add_styles runs this again when the look changes)."""
    st.configure("FilmPlain.TFrame", background=T.CARD, borderwidth=0, relief="flat")
    st.configure("FilmTitle.TLabel", background=T.CARD, foreground=T.ACCENT, font=T.font(st, 15, "bold"))
    st.configure("FilmMeta.TLabel", background=T.CARD, foreground=T.INK_2, font=T.font(st, 10))
    st.configure("FilmTagline.TLabel", background=T.CARD, foreground=T.INK_2, font=T.font(st, 10, "italic"))
    st.configure("FilmText.TLabel", background=T.CARD, foreground=T.INK)
    st.configure("FilmNote.TLabel", background=T.CARD, foreground=T.INK_2)
    st.configure("FilmMuted.TLabel", background=T.CARD, foreground=T.MUTED)
    st.configure("FilmField.TLabel", background=T.CARD, foreground=T.MUTED)
    st.configure("FilmStrong.TLabel", background=T.CARD, foreground=T.INK, font=T.font(st, 9, "bold"))
    st.configure("FilmVerdict.TLabel", background=T.CARD, foreground=T.INK, font=T.font(st, 10, "bold"))
    st.configure("FilmLink.TLabel", background=T.CARD, foreground=T.LINK)
    st.configure("FilmSep.TLabel", background=T.CARD, foreground=T.BASELINE)
    st.configure("FilmPageLink.TLabel", background=T.PAGE, foreground=T.LINK)
    st.configure("FilmSource.TLabel", background=T.PAGE, foreground=T.MUTED)


def _swatch(parent, color, s: float, size: int = 10) -> tk.Frame:
    """A small colour square. color: a token's name ("GOOD") or a function giving the colour - so it follows
    the look."""
    frame = tk.Frame(parent, width=int(size * s), height=int(size * s), highlightthickness=0, borderwidth=0)
    return T.tint(frame, background=color)


class Flow(ttk.Frame):
    """Widgets side by side that wrap onto another line when they run out of width (links to genres, people...).
    Placed rather than gridded, so every line starts at the left whatever the lines above hold."""

    def __init__(self, parent, gap: int = 10, style: str = "FilmPlain.TFrame", **kw):
        super().__init__(parent, style=style, **kw)
        self.items: list[tk.Widget] = []
        self.s = T.scale(parent)
        self.gap = gap
        self._width = 0
        self.configure(width=1, height=1)
        self.bind("<Configure>", self._configured, add="+")

    def add(self, widget):
        self.items.append(widget)
        self.arrange()
        return widget

    def _configured(self, event):
        if event.width != self._width:
            self._width = event.width
            self.arrange()

    def arrange(self):
        width = self._width if self._width > 1 else 10 ** 6       # (not laid out yet: all on one line)
        gap = int(self.gap * self.s)
        x = y = line_h = 0
        wrap = False
        for n, w in enumerate(self.items):
            need, high = w.winfo_reqwidth(), w.winfo_reqheight()
            if getattr(w, "flow_divider", False):
                # a divider goes between two things on one line - never at a line's end ('Thriller  |' with
                # 'Hong Kong' below): where the line breaks, it's left out
                after = self.items[n + 1] if n + 1 < len(self.items) else None
                if not x or after is None or x + need + gap + after.winfo_reqwidth() > width:
                    w.place_forget()
                    wrap = bool(x)
                    continue
            if x and (wrap or x + need > width):
                x, y = 0, y + line_h + int(2 * self.s)
                line_h = 0
            wrap = False
            w.place(x=x, y=y)
            x += need + gap
            line_h = max(line_h, high)
        height = max(y + line_h, 1)
        if int(self.cget("height")) != height:
            self.configure(height=height)


def _links(parent, items, command, sep: str | None = None) -> Flow:
    """A Flow of LinkLabels: items are (text, value) pairs, command(value) on a click; a (text, None) item is a
    plain divider between groups of links (sep goes between the links of a group)."""
    flow = Flow(parent, gap=6 if sep else 10)
    previous = None

    def divider(text):
        label = ttk.Label(flow, text=text, style="FilmSep.TLabel")
        label.flow_divider = True
        flow.add(label)
    for text, value in items:
        if value is None:
            divider(text)
        else:
            if sep and previous is not None and previous[1] is not None:
                divider(sep)
            flow.add(LinkLabel(flow, text=text, command=lambda v=value: command(v), style="FilmLink.TLabel"))
        previous = (text, value)
    return flow


def _unbind(widget, sequence: str, funcid: str):
    """Take one function's binding off a widget, leaving any others on the same event (tkinter's unbind removes
    them all in older Pythons)."""
    try:
        script = widget.bind(sequence) or ""
        kept = "\n".join(line for line in script.split("\n") if funcid not in line)
        widget.bind(sequence, kept)
    except tk.TclError:
        pass
    try:
        widget.deletecommand(funcid)
    except (tk.TclError, ValueError):
        pass


# ---------------------------------------------------------------------------------------------------------
# The tab
# ---------------------------------------------------------------------------------------------------------
class Tab(BaseTab):
    title = "Film"

    def __init__(self, app, notebook):
        super().__init__(app, notebook)
        self.s = T.scale(self.frame)
        T.add_styles(self.frame, _styles)
        self.history: list[tuple] = []        # ('start',) | ('film', key) | ('studio', name); 'start' comes first
        self.position = -1
        self.cards: dict[str, Card] = {}
        self.slots: list[tuple[str, str]] = []   # the page's cards in order: (name, 'full' | 'half')
        self.parts: dict[str, dict] = {}      # the film's page parts, as they arrive
        self.views: dict[str, ChartView] = {}
        self.tables: dict[str, Table] = {}
        self.columns = 0
        self._built = None                    # (catalog, page entry) the widgets show
        self._pending = None                  # navigate() before the collection was ready
        self._start_job = None
        self._jobs: dict[str, object] = {}    # part -> its background job
        self._issue_languages = _UNSET        # the Library Doctor's languages the page's issues were worked out for
        self._wraps: list[tuple] = []         # (label, widget whose width it wraps to, less)
        self.staged = None                    # build a film page a card at a time: None = when on screen
        self._stage_job = None
        self._page_token = 0
        self._status_text = None
        # (all that's kept, however many the start page shows: showing fewer for a while loses none)
        self.recent: list[str] = [str(k) for k in prefs.get(app, "film_recent")][:RECENT_KEEP]
        self._build()
        self._bind_keys()
        self.frame.bind("<Destroy>", self._destroyed, add="+")
        self.catalog_changed(getattr(app, "catalog", None), getattr(app, "catalog_state", "none"))

    # -- layout ------------------------------------------------------------------------------------------------
    def _build(self):
        f = self.frame
        f.columnconfigure(0, weight=1)
        f.rowconfigure(1, weight=1)
        head = ttk.Frame(f, style="Page.TFrame", padding=(PAD, 10, PAD, 8))
        head.grid(row=0, column=0, sticky="ew")
        head.columnconfigure(3, weight=1)
        self.back_btn = ttk.Button(head, text="←  Back", style="Small.TButton", command=self.back)
        self.back_btn.grid(row=0, column=0, padx=(0, 4))
        self.forward_btn = ttk.Button(head, text="Forward  →", style="Small.TButton", command=self.forward)
        self.forward_btn.grid(row=0, column=1, padx=(0, 14))
        self.title_label = ttk.Label(head, text=START_TITLE, style="PageTitle.TLabel")
        self.title_label.grid(row=0, column=2, sticky="w")
        self.hint_label = ttk.Label(head, text=PAGE_HINT, style="PageHint.TLabel")
        self.hint_label.grid(row=0, column=3, sticky="w", padx=(14, 0))
        self.source_label = ttk.Label(head, text="", style="FilmSource.TLabel")
        self.source_label.grid(row=0, column=4, sticky="e", padx=(12, 0))
        head.bind("<Configure>", self._fit_head, add="+")
        self.head = head

        self.placeholder = CR.guard(ChartView(f, height=320, background=T.PAGE))
        self.placeholder.grid(row=1, column=0, sticky="nsew")
        self.scroll = ScrollFrame(f, background=T.PAGE, style="Page.TFrame")
        self.scroll.grid(row=1, column=0, sticky="nsew")
        self.scroll.canvas.bind("<Configure>", lambda e: self._reflow(e.width), add="+")
        self.page = self.scroll.inner
        self.page.columnconfigure(0, weight=1, uniform="film-cards")
        self.page.columnconfigure(1, weight=1, uniform="film-cards")
        self._buttons()

    def _fit_head(self, event=None):
        """A long film title leaves less room: drop the hint, then the 'Ratings and plays: <owner>' label, rather
        than squash the title or cut the label off at the window's edge (the title itself is shortened with an
        ellipsis only when even that isn't enough)."""
        width = event.width if event is not None else self.head.winfo_width()
        if width <= 50:
            return
        full = getattr(self, "_title_text", None) or str(self.title_label.cget("text"))
        font = self._title_font()
        fixed = sum(w.winfo_reqwidth() for w in (self.back_btn, self.forward_btn)) + int(24 * self.s)
        title_w = font.measure(full) + int(8 * self.s) if font is not None else self.title_label.winfo_reqwidth()
        hint_w = self.hint_label.winfo_reqwidth() + int(14 * self.s)
        source_w = self.source_label.winfo_reqwidth() + int(12 * self.s) if self.source_label.cget("text") else 0
        hint = fixed + title_w + hint_w + source_w <= width
        source = fixed + title_w + source_w <= width
        (self.hint_label.grid if hint else self.hint_label.grid_remove)()
        (self.source_label.grid if source else self.source_label.grid_remove)()
        room = width - fixed - int(8 * self.s)
        text = full if font is None or title_w <= room + int(8 * self.s) else _fit(font, full, max(room, 60))
        if str(self.title_label.cget("text")) != text:
            self.title_label.configure(text=text)

    def _set_title(self, text: str):
        """The page title in the bar at the top (cut short to fit, if need be: see _fit_head)."""
        self._title_text = text
        self.title_label.configure(text=text)
        self._fit_head()

    def _title_font(self):
        if getattr(self, "_title_font_obj", None) is None:
            try:
                desc = ttk.Style(self.frame).lookup("PageTitle.TLabel", "font") or "TkDefaultFont"
                self._title_font_obj = tkfont.Font(root=self.frame, font=desc)
            except tk.TclError:
                return None
        return self._title_font_obj

    def _reflow(self, width: int | None = None):
        width = width or self.scroll.canvas.winfo_width()
        if width < 50:
            return
        cols = 2 if width / self.s >= TWO_COLUMNS_FROM else 1
        if cols != self.columns:
            self.columns = cols
            self._layout()

    def _layout(self):
        """Grid the page's cards: full-width ones on a row of their own, half-width ones in pairs when there's
        room for two (a half card without a partner takes the whole row)."""
        cols = self.columns or 1
        shown = [(n, span) for n, span in self.slots if n in self.cards and getattr(self.cards[n], "shown", True)]
        for n, _span in self.slots:
            card = self.cards.get(n)
            if card is not None and not getattr(card, "shown", True):
                card.grid_remove()
        row = i = 0
        while i < len(shown):
            name, span = shown[i]
            card = self.cards[name]
            top = GAP if row else 0
            if cols == 2 and span == "half" and i + 1 < len(shown) and shown[i + 1][1] == "half":
                other = self.cards[shown[i + 1][0]]
                card.grid(row=row, column=0, columnspan=1, sticky="nsew", padx=(PAD, GAP // 2), pady=(top, 0))
                other.grid(row=row, column=1, columnspan=1, sticky="nsew", padx=(GAP // 2, PAD), pady=(top, 0))
                i += 2
            else:
                card.grid(row=row, column=0, columnspan=2, sticky="nsew", padx=(PAD, PAD), pady=(top, 0))
                i += 1
            row += 1
        if getattr(self, "_bottom", None) is None or not self._bottom.winfo_exists():
            self._bottom = ttk.Frame(self.page, style="Page.TFrame", height=PAD)
        self._bottom.grid(row=row, column=0, columnspan=2)

    # -- keys and buttons -----------------------------------------------------------------------------------------
    def _bind_keys(self):
        self._key_ids = []
        try:
            top = self.frame.winfo_toplevel()
            for seq, step in (("<Alt-Left>", -1), ("<Alt-Right>", 1)):
                self._key_ids.append((top, seq, top.bind(seq, lambda e, step=step: self._alt(step), add="+")))
        except tk.TclError:
            pass

    def _alt(self, step: int):
        if not self._is_current():
            return None
        if step < 0:
            self.back()
        else:
            self.forward()
        return "break"

    def _is_current(self) -> bool:
        current = getattr(self.app, "current_tab", None)
        if not callable(current):
            return True
        try:
            return current() is self
        except Exception:
            return False

    def _destroyed(self, event):
        if event.widget is not self.frame:
            return
        for top, seq, funcid in getattr(self, "_key_ids", []):
            _unbind(top, seq, funcid)
        self._key_ids = []
        if self._start_job is not None:
            try:
                self.frame.after_cancel(self._start_job)
            except (tk.TclError, ValueError):
                pass
            self._start_job = None
        self._cancel_stage()
        self._cancel_jobs()

    def _buttons(self):
        self.back_btn.configure(state="normal" if self.position > 0 else "disabled")
        self.forward_btn.configure(state="normal" if 0 <= self.position < len(self.history) - 1 else "disabled")

    # -- called by the main window ------------------------------------------------------------------------------
    def catalog_changed(self, catalog, state: str):
        super().catalog_changed(catalog, state)
        self._cancel_jobs()
        self._built = None
        self.parts = {}
        self.source_label.configure(text=f"Ratings and plays: {catalog.owner}" if catalog is not None
                                    and getattr(catalog, "owner", "") else "")
        self._fit_head()
        msg = self.state_message()
        if msg is not None:
            if state != "loading" and self._pending is not None:
                self._pending = None
                self._unsay()
            self._placeholder(*msg)
            return
        self._prune()
        if self._pending is not None:
            pending, self._pending = self._pending, None
            self._unsay()
            self.navigate(**pending)
        elif self._visible():
            self._show_current()

    def shown(self):
        if self.state_message() is not None:
            return
        if not self.history:
            self.history, self.position = [("start",)], 0
        if self._built == (self.catalog, self.current()):
            self._recheck_issues()
            return
        if self._start_job is None:          # after the tab has switched (a navigate() just after calls it off)
            self._start_job = self.frame.after_idle(self._shown_later)

    def _shown_later(self):
        self._start_job = None
        if self.state_message() is None and self._built != (self.catalog, self.current()):
            self._show_current()

    def navigate(self, film_key=None, title=None, studio=None, **_ignored):
        """Show a film (film_key says exactly which; title - 'Title (Year)' or a loose title - when there's no
        key) or a studio's films (studio=)."""
        if not film_key and not title and not studio:
            return
        self._cancel_start()
        if self.state != "ready" or self.catalog is None:
            self._pending = {"film_key": film_key, "title": title, "studio": studio}
            self._say("The collection is still loading - the film will be shown when it's ready.")
            return
        if studio and not film_key and not title:
            self._push(("studio", str(studio)))
            return
        film, how, _others = FP.find(self.catalog, film_key, title)
        if film is None:
            self._push(("missing", str(title or film_key)))
            return
        self._push(("film", film.key))
        if how not in ("key", "exact") and title:        # (after the page's jobs have put up their status)
            self._say(f"Showing {film.label} for “{title}”.")

    # -- history -----------------------------------------------------------------------------------------------
    def current(self) -> tuple | None:
        return self.history[self.position] if 0 <= self.position < len(self.history) else None

    def open_film(self, key: str):
        self._cancel_start()
        self._push(("film", str(key)))

    def open_studio(self, name: str):
        self._cancel_start()
        self._push(("studio", str(name)))

    def back(self):
        if self.position > 0:
            self.position -= 1
            self._show_current()

    def forward(self):
        if 0 <= self.position < len(self.history) - 1:
            self.position += 1
            self._show_current()

    def _push(self, entry: tuple):
        if not self.history:
            self.history, self.position = [("start",)], 0
        if self.current() != entry:
            del self.history[self.position + 1:]
            self.history.append(entry)
            if len(self.history) > HISTORY_MAX:
                del self.history[1:len(self.history) - HISTORY_MAX + 1]     # (the start page stays first)
            self.position = len(self.history) - 1
        self._show_current()

    def _prune(self):
        """A new collection: forget the films that aren't in it (and a missing-film page)."""
        films = self.catalog.films if self.catalog is not None else {}
        old, kept, where = self.history, [], {}
        for i, e in enumerate(old):
            if (e[0] == "film" and e[1] not in films) or e[0] == "missing":
                continue
            if kept and kept[-1] == e:
                where[i] = len(kept) - 1
                continue
            where[i] = len(kept)
            kept.append(e)
        if not kept or kept[0] != ("start",):
            kept.insert(0, ("start",))
            where = {i: n + 1 for i, n in where.items()}
        self.history = kept
        self.position = where.get(self.position, 0)
        self.recent = [k for k in self.recent if k in films]

    def _cancel_start(self):
        if self._start_job is not None:
            try:
                self.frame.after_cancel(self._start_job)
            except (tk.TclError, ValueError):
                pass
            self._start_job = None

    # -- showing a page --------------------------------------------------------------------------------------------
    def _visible(self) -> bool:
        current = getattr(self.app, "current_tab", None)
        try:
            return callable(current) and current() is self
        except Exception:
            return False

    def _placeholder(self, text: str, sub: str | None = None):
        self._clear_page()
        self.scroll.grid_remove()
        self.placeholder.grid()
        self.show_message(self.placeholder, text, sub or None)
        self._set_title(START_TITLE)
        self.back_btn.configure(state="disabled")
        self.forward_btn.configure(state="disabled")

    def _show_current(self, keep_place: bool = False):
        """Build the page for the current entry of the history (keep_place: at the same place on it - the same
        page drawn again, say with dates in another style - rather than at the top)."""
        self._cancel_start()
        if self.state_message() is not None:
            return
        try:
            place = self.scroll.canvas.yview()[0] if keep_place else 0.0
        except tk.TclError:
            place = 0.0
        if not self.history:
            self.history, self.position = [("start",)], 0
        entry = self.current()
        self._cancel_jobs()
        self._unsay()                                # (an earlier 'no film called...' is out of date now)
        self.parts = {}
        self.placeholder.grid_remove()
        self.scroll.grid()
        self._clear_page()
        self._built = (self.catalog, entry)          # (before the background parts start: they check it)
        kind = entry[0]
        if kind == "film" and entry[1] in self.catalog.films:
            self._film_page(entry[1])
        elif kind == "studio":
            self._studio_page(entry[1])
        elif kind == "missing" or kind == "film":
            self._missing_page(entry[1])
        else:
            self._start_page()
        self._buttons()
        self._layout()
        self.scroll.to_top()
        self._fit_head()
        if place:
            self.scroll.canvas.update_idletasks()
            self.scroll.canvas.yview_moveto(place)

    def _clear_page(self):
        self._cancel_stage()
        for w in self.page.winfo_children():
            w.destroy()
        self.cards, self.slots, self.views, self.tables, self._wraps = {}, [], {}, {}, []
        self._issue_languages = _UNSET               # (the languages this page's issues were worked out for)
        self._bottom = None
        self._head_parts = self._head_wide = None
        self._similar_panes, self._similar_side = (None, []), None
        self.timeline_views = []

    def _card(self, name: str, span: str, title: str | None = None, hint: str | None = None,
              shown: bool = True) -> Card:
        card = Card(self.page, title, hint, padding=14)
        card.body.columnconfigure(0, weight=1)
        card.shown = shown
        card.bind("<Configure>", lambda e, c=card: self._rewrap(c, e.width), add="+")
        self.cards[name] = card
        self.slots.append((name, span))
        return card

    def _wrap(self, label, within, less: int = 0):
        """Keep a label wrapped to a widget's width (less `less` device-independent pixels) as it changes."""
        self._wraps.append((label, within, less))
        if not getattr(within, "_film_wraps", False):
            within._film_wraps = True
            if not isinstance(within, Card):          # (cards already tell _rewrap: see _card)
                within.bind("<Configure>", lambda e, w=within: e.widget is w and self._rewrap(w, e.width),
                            add="+")
        width = within.winfo_width()
        # (until it's laid out, a moderate width: an unwrapped paragraph would ask for the whole line's width)
        label.configure(wraplength=max(width - int(less * self.s), 120) if width > 50 else int(480 * self.s))
        return label

    def _rewrap(self, within, width: int):
        if width < 50:
            return
        for label, w, less in self._wraps:
            if w is within:
                try:
                    want = max(width - int(less * self.s), 120)
                    if int(str(label.cget("wraplength")) or 0) != want:
                        label.configure(wraplength=want)
                except tk.TclError:
                    pass

    def _goto(self, tab: str, **kwargs):
        if self.app.goto(tab, **kwargs) is None:
            self.app.set_status(f"The {tab} tab isn't available.")

    def _say(self, text: str):
        """A status-line message this tab takes back once it's out of date."""
        self._status_text = text
        setter = getattr(self.app, "set_status", None)
        if setter:
            setter(text)

    def _unsay(self):
        mine, self._status_text = self._status_text, None
        if not mine:
            return
        var = getattr(self.app, "app_status_var", None)
        try:
            if var is not None and var.get() != mine:
                return
        except tk.TclError:
            return
        setter = getattr(self.app, "set_status", None)
        if setter:
            setter("")

    # -- the start page ------------------------------------------------------------------------------------------
    def _start_page(self):
        self._set_title(START_TITLE)
        card = self._card("which", "half", "Which film?")
        b = card.body
        text = ttk.Label(b, text="Search with the box at the top of the window (Ctrl+F) - films, people, "
                                 "collections, genres, countries, studios, libraries and critics - or pick one "
                                 "below.",
                         style="FilmNote.TLabel", justify="left")
        text.grid(row=0, column=0, sticky="w")
        self._wrap(text, card, 30)
        ttk.Button(b, text="Search  (Ctrl+F)", command=self._focus_search).grid(row=1, column=0, sticky="w",
                                                                              pady=(10, 0))
        card = self._card("recent", "half", "Recently opened")
        films = [self.catalog.films[k] for k in self.recent if k in self.catalog.films][:self._recent_max()]
        if films:
            lst = ttk.Frame(card.body, style="FilmPlain.TFrame")
            lst.grid(row=0, column=0, sticky="ew")
            for n, film in enumerate(films):
                LinkLabel(lst, text=film.label, style="FilmLink.TLabel",
                          command=lambda k=film.key: self.open_film(k)).grid(row=n, column=0, sticky="w", pady=1)
        else:
            ttk.Label(card.body, text="The films you open here will be listed here." if self._recent_max() else
                      "Not kept: the Settings tab can turn this list back on.",
                      style="FilmMuted.TLabel").grid(row=0, column=0, sticky="w")
        card = self._card("suggestions", "full", "Suggestions")
        left_out = self._left_out()
        picks = FP.suggestions(self.catalog, exclude_libraries=left_out["libraries"],
                               exclude_genres=left_out["genres"], also_seen_by=self._seen_by())
        cols = ttk.Frame(card.body, style="FilmPlain.TFrame")
        cols.grid(row=0, column=0, sticky="ew")
        links = []
        cols.bind("<Configure>", lambda e: e.widget is cols and self._elide(links, e.width // 3 - int(20 * self.s)),
                  add="+")
        for c, (key, heading) in enumerate((("recently_added", "Just added to Plex"),
                                            ("favourites", "Your favourites"),
                                            ("well_rated_unseen", "Well rated, not seen yet"))):
            cols.columnconfigure(c, weight=1, uniform="film-suggestions")
            col = ttk.Frame(cols, style="FilmPlain.TFrame")
            col.grid(row=0, column=c, sticky="nw", padx=(0, 16))
            ttk.Label(col, text=heading, style="FilmStrong.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 4))
            items = picks.get(key) or []
            if not items:
                ttk.Label(col, text={"favourites": "Rate films 9 or 10 in Plex and they'll show here.",
                                     "recently_added": "Nothing yet.",
                                     "well_rated_unseen": "You've seen every film IMDb rates 8 or more" + (
                                         ", apart from those Settings leaves out." if any(left_out.values())
                                         else ".")}[key],
                          style="FilmMuted.TLabel", wraplength=int(220 * self.s), justify="left").grid(
                    row=1, column=0, sticky="w")
            for n, item in enumerate(items):
                link = LinkLabel(col, text=item["label"], style="FilmLink.TLabel",
                                 command=lambda k=item["key"]: self.open_film(k))
                link.grid(row=1 + 2 * n, column=0, sticky="w")
                links.append((link, item["label"]))
                ttk.Label(col, text=item.get("note", ""), style="FilmField.TLabel").grid(
                    row=2 + 2 * n, column=0, sticky="w", pady=(0, 4))
        self._elide(links, int(260 * self.s))      # (until the card's laid out: then its real width)

    def _elide(self, links, width: int):
        """Cut long film names short with an ellipsis to fit a column (a three-column card on a narrow window)."""
        if width < 40:
            return
        try:
            font = tkfont.Font(root=self.frame, font=ttk.Style(self.frame).lookup("FilmLink.TLabel", "font")
                               or "TkDefaultFont")
        except tk.TclError:
            return
        for link, full in links:
            try:
                text = _fit(font, full, width)
                if str(link.cget("text")) != text:
                    link.configure(text=text)
            except tk.TclError:
                pass

    def _focus_search(self):
        bar = getattr(self.app, "search_bar", None)
        if bar is not None:
            bar.focus()

    # -- a studio's films -------------------------------------------------------------------------------------------
    def _studio_page(self, studio: str):
        rows = FP.studio_films(self.catalog, studio)
        self._set_title(f"Films from {studio}")
        card = self._card("studio", "full", f"Films from {studio}",
                          "Plex's main studio for each film (co-producers aren't counted). Double-click a film to "
                          "open it.")
        if not rows:
            view = CR.guard(ChartView(card.body, height=140, width=1, background=T.SURFACE))
            view.grid(row=0, column=0, sticky="ew")
            self.show_message(view, f"No films from {studio} in your collection",
                              "Search for another studio with the box at the top of the window.")
            return
        rated = [r for r in rows if r["your_rating"] is not None]
        played = sum(1 for r in rows if r["played"])
        summary = f"{plural(len(rows), 'film')}, {played:,} played"
        if rated:
            average = sum(r["your_rating"] for r in rated) / len(rated)
            summary += f"; you've rated {len(rated):,}, averaging {average:.1f}"
        ttk.Label(card.body, text=summary + ".", style="FilmNote.TLabel").grid(row=0, column=0, sticky="w",
                                                                          pady=(0, 8))
        table = Table(card.body, [("title", "Film", 320, "w"), ("year", "Year", 60, "e"),
                                  ("yours", "You gave", 80, "e"), ("imdb", "IMDb", 70, "e"),
                                  ("seen", "Played", 70, "w")],
                      height=min(max(len(rows), 3), 20), on_open=lambda r: self.open_film(r["key"]))
        table.grid(row=1, column=0, sticky="ew")
        table.set_rows([dict(r, yours=f"{r['your_rating']:g}" if r["your_rating"] is not None else "",
                             imdb=f"{r['imdb_rating']:g}" if r["imdb_rating"] is not None else "",
                             seen="✔ played" if r["played"] else "") for r in rows])
        self.tables["studio"] = table

    # -- a film that isn't there ----------------------------------------------------------------------------------
    def _missing_page(self, asked: str):
        self._set_title(START_TITLE)
        card = self._card("missing", "full")
        view = CR.guard(ChartView(card.body, height=150, width=1, background=T.SURFACE))
        view.grid(row=0, column=0, sticky="ew")
        text = CR.not_found(asked) if asked and not asked.startswith(("plex:", "plex://", "imdb:")) \
            else "That film isn't in your collection"
        view.show(lambda p: CR.wrapped_message(p, text, "Search for it with the box at the top of the window "
                                                         "(Ctrl+F)."))
        self.views["missing"] = view
        self._say(text + ".")

    # -- a film ------------------------------------------------------------------------------------------------------
    def _film_page(self, key: str):
        page = FP.film_page(self.catalog, key, parts="core")
        film = self.catalog.films[key]
        self.film = film
        self.parts = {k: page[k] for k in FP.CORE}
        self._set_title(film.label)
        self._header_card(page["film"], film)
        self._ratings_card(page["ratings"])
        self._history_card(None)            # (a moment until the viewing history answers: no count to take back)
        self._remember_recent(key)
        self._stage([lambda: self._credits_card(page["film"], page["credits"], film),
                     lambda: self._people_card(page["people"]),
                     lambda: self._similar_card(None),
                     self._hook_cards,
                     lambda: self._start_background(key)])        # (last: its answers fill the cards above)

    def _hook_cards(self):
        """The cards the other backbones fill, hidden until (and unless) their answers come."""
        for name, span, title in (("critics", "full", "What critics said"), ("files", "half", "Your copies"),
                                  ("issues", "half", "Library health")):
            self._card(name, span, title, shown=False)

    def _stage(self, steps):
        """Build the rest of the page a card at a time, letting the window breathe in between - creating and laying
        out a whole page of widgets at once can take a tenth of a second or more on a busy PC. A window that isn't
        on screen (tests, previews) builds it all at once. `staged`: True / False forces one way."""
        on_screen = self.staged
        if on_screen is None:
            try:
                on_screen = bool(self.frame.winfo_toplevel().winfo_viewable())
            except tk.TclError:
                on_screen = False
        if not on_screen:
            for step in steps:
                step()
            return
        token = self._page_token

        def run(i):
            self._stage_job = None
            if token != self._page_token:
                return                                  # another page since
            steps[i]()
            self._layout()
            if i + 1 < len(steps):
                self._stage_job = self.frame.after(1, run, i + 1)
        self._stage_job = self.frame.after(1, run, 0)

    def _cancel_stage(self):
        self._page_token += 1
        if self._stage_job is not None:
            try:
                self.frame.after_cancel(self._stage_job)
            except (tk.TclError, ValueError):
                pass
            self._stage_job = None

    def _recent_max(self) -> int:
        """How many recently opened films the start page lists (Settings > Film; 0 = no list)."""
        return prefs.get(self.app, "film_recent_count")

    def _left_out(self) -> dict:
        """What Settings > Watch Next never suggests ({'libraries': [...], 'genres': [...]}): the start page's 'Well
        rated, not seen yet' leaves those out too. (The Watch Next tab's module defines the setting; without it,
        nothing is left out.)"""
        if LEAVE_OUT not in prefs.PREFS:
            return {"libraries": [], "genres": []}
        return prefs.get(self.app, LEAVE_OUT)

    def _seen_by(self) -> list[int]:
        """The other accounts whose plays count as seen (Settings > Your collection): 'Well rated, not seen yet' and
        the similar films' 'Not seen yet' leave out what they've played."""
        return prefs.get(self.app, SEEN_BY)

    def preference_changed(self, key: str, value):
        if self.state_message() is not None or not self.history:
            return
        entry = self.current()
        if key in ("film_recent_count", LEAVE_OUT, SEEN_BY) and entry == ("start",):
            self._show_current()                             # (the start page is showing: as it says now)
        elif key == SEEN_BY and entry and entry[0] == "film" and "similar" in self.cards:
            self._similar_card(None)                         # (its 'Not seen yet' list, asked again)
            self._layout()
            self._start_part(entry[1], "similar")
        elif key in ("date_style", "clock"):
            self._show_current(keep_place=True)              # (dates and times are all over the page)

    def keep(self) -> dict:
        """(Laid out again for a new text size:) the page on show, and Back and Forward's history."""
        return {"history": list(self.history), "position": self.position, "recent": list(self.recent)}

    def restore(self, kept: dict):
        history = [tuple(entry) for entry in kept.get("history") or []]
        position = kept.get("position", -1)
        if history and isinstance(position, int) and 0 <= position < len(history):
            self.history, self.position = history, position
            self._buttons()
        if isinstance(kept.get("recent"), list):
            self.recent = list(kept["recent"])

    def closing(self) -> dict:
        """Set to list no recently opened films: the list is forgotten as the window closes (not the moment the
        count reaches 0 - one click too many on the box's arrow mustn't lose it)."""
        if self._recent_max() == 0 and (self.recent or prefs.get(self.app, "film_recent")):
            self.recent = []
            return {"film_recent": []}
        return {}

    def _remember_recent(self, key: str | None):
        if key is not None and self._recent_max() > 0:          # (set to keep no list: nothing is added)
            self.recent = [key] + [k for k in self.recent if k != key]
        del self.recent[RECENT_KEEP:]
        remember = getattr(self.app, "_remember", None)
        if callable(remember):
            try:
                remember(film_recent=list(self.recent))
            except Exception:
                pass

    def _start_background(self, key: str):
        for part in FP.BACKGROUND:
            self._start_part(key, part)

    def _start_part(self, key: str, part: str):
        catalog = self.catalog
        if part == "issues":
            self._issue_languages = self._doctor_languages()
        seen_by = self._seen_by() if part == "similar" else ()
        self._jobs[part] = self.app.run(
            lambda: FP.film_page(catalog, key, parts=[part], also_seen_by=seen_by)[part],
            lambda answer: self._part_done(catalog, key, part, answer),
            lambda message: self._part_done(catalog, key, part, {"available": True, "ok": False, "error": message}),
            status=LOADING_SIMILAR if part == "similar" else None, key=f"film.{part}")

    def _doctor_languages(self):
        """The languages the Library Doctor checks audio and subtitles against (its issues depend on them)."""
        cache = getattr(self.catalog, "cache", None)
        value = cache.get("doctor.languages") if isinstance(cache, dict) else None
        return tuple(value) if isinstance(value, (list, tuple)) else value

    def _recheck_issues(self):
        """Back on a film page after its library-health issues were worked out: languages changed in the Library
        Doctor since then change the issues too (No subtitles you read...), so ask again."""
        entry = self.current()
        if not entry or entry[0] != "film" or "issues" not in self.cards:
            return                                   # (one still being worked out, with other languages: again)
        if self._issue_languages is _UNSET or self._doctor_languages() == self._issue_languages:
            return
        self._start_part(entry[1], "issues")

    def _cancel_jobs(self):
        for job in list(self._jobs.values()):
            try:
                if job is not None and hasattr(job, "cancel"):
                    job.cancel()
            except Exception:
                pass
        self._jobs = {}

    def _part_done(self, catalog, key: str, part: str, answer):
        if catalog is not self.catalog or self.current() != ("film", key) or self._built != (catalog, ("film", key)):
            return                                   # another film (or collection) since
        self._jobs.pop(part, None)
        answer = answer if isinstance(answer, dict) else {"available": True, "ok": True, "value": answer}
        self.parts[part] = answer
        renderer = {"similar": self._similar_done, "critics": self._critics_done, "history": self._history_done,
                    "files": self._files_done, "issues": self._issues_done}[part]
        try:
            renderer(answer)
        except Exception:                            # an answer of a shape this tab doesn't know: show it plainly
            import traceback
            report = getattr(self.app, "_log", None)
            if callable(report):
                report(f"The Film tab couldn't show its {part} part:\n{traceback.format_exc().strip()}")
            card = self.cards.get(part)
            if card is not None:
                self._clear(card.body)
                self._generic(card.body, answer)
                card.shown = True
        self._layout()

    @staticmethod
    def _clear(frame):
        for w in frame.winfo_children():
            w.destroy()

    # -- the header --------------------------------------------------------------------------------------------------
    def _header_card(self, info: dict, film):
        card = self._card("header", "full")
        b = card.body
        b.columnconfigure(0, weight=3, uniform="film-head")
        b.columnconfigure(1, weight=2, uniform="film-head")
        left = ttk.Frame(b, style="FilmPlain.TFrame")
        left.columnconfigure(0, weight=1)
        right = ttk.Frame(b, style="FilmPlain.TFrame")
        right.columnconfigure(1, weight=1)
        self._head_parts = (b, left, right)
        row = 0
        title = ttk.Label(left, text=info["label"], style="FilmTitle.TLabel", justify="left")
        title.grid(row=row, column=0, sticky="w")
        self._wrap(title, left, 4)
        self.header_title = title
        row += 1
        if info.get("titles"):
            aka = ttk.Label(left, text="Also known as " + "  ·  ".join(info["titles"]), style="FilmNote.TLabel",
                            justify="left")
            aka.grid(row=row, column=0, sticky="w")
            self._wrap(aka, left, 4)
            row += 1
        meta = meta_line(info)
        if meta:
            ttk.Label(left, text=meta, style="FilmMeta.TLabel").grid(row=row, column=0, sticky="w", pady=(4, 0))
            row += 1
        genres = [(g, ("genre", g)) for g in info.get("genres") or []]
        countries = [(FP.country_name(c), ("country", c)) for c in info.get("countries") or []]
        tags = genres + ([("   |   ", None)] if genres and countries else []) + countries
        if tags:
            flow = _links(left, tags, lambda v: self._goto("Watch Next", **{v[0]: v[1]}), sep="·")
            flow.grid(row=row, column=0, sticky="ew", pady=(6, 0))
            row += 1
        if info.get("tagline"):
            tag = ttk.Label(left, text=info["tagline"], style="FilmTagline.TLabel", justify="left")
            tag.grid(row=row, column=0, sticky="w", pady=(10, 0))
            self._wrap(tag, left, 4)
            row += 1
        summary = ttk.Label(left, text=info.get("summary") or "Plex has no summary for this film.",
                            style="FilmText.TLabel" if info.get("summary") else "FilmMuted.TLabel", justify="left")
        summary.grid(row=row, column=0, sticky="w", pady=(6, 0))
        self._wrap(summary, left, 4)
        row += 1
        buttons = ttk.Frame(left, style="FilmPlain.TFrame")
        buttons.grid(row=row, column=0, sticky="w", pady=(12, 0))
        like = {"like": info["label"], "film_key": info["key"]}
        if FP.comparable(self.catalog, film):        # (else Watch Next would match on its decade and library alone)
            ttk.Button(buttons, text="More like this in Watch Next",
                       command=lambda: self._goto("Watch Next", **like)).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(buttons, text="Scenes after the credits?",
                   command=lambda: self._goto("Credits", film_key=info["key"], title=info["label"])).grid(
            row=0, column=1)

        facts = []
        if film.directors:
            facts.append(("Directed by", lambda p: _links(p, [(c.name, c) for c in film.directors],
                                                          self._open_director, sep="·")))
        if info.get("studio"):
            facts.append(("Studio", lambda p: _links(p, [(info["studio"], info["studio"])], self.open_studio)))
        if info.get("collections"):
            facts.append(("Collections", lambda p: _links(
                p, [(c, c) for c in info["collections"]], lambda c: self._goto("Watch Next", collection=c),
                sep="·")))
        if info.get("labels"):
            facts.append(("Labels", lambda p: self._value(p, ", ".join(info["labels"]))))
        if info.get("libraries"):
            facts.append(("Libraries", lambda p: _links(
                p, [(x, x) for x in info["libraries"]], lambda x: self._goto("Watch Next", library=x), sep="·")))
        facts.append(("Your copies", lambda p: self._value(p, FP.copies_text(info.get("copies"),
                                                                            info.get("editions")))))
        if info.get("added"):
            facts.append(("Added to Plex", lambda p: self._value(p, day_text(info["added"]))))
        if info.get("imdb_id"):
            facts.append(("IMDb", lambda p: self._value(p, info["imdb_id"])))
        for n, (name, make) in enumerate(facts):
            ttk.Label(right, text=name, style="FilmField.TLabel").grid(row=n, column=0, sticky="nw", padx=(0, 12),
                                                                       pady=(0 if n == 0 else 5, 0))
            value = make(right)
            value.grid(row=n, column=1, sticky="ew", pady=(0 if n == 0 else 5, 0))
            if isinstance(value, ttk.Label):
                self._wrap(value, right, 110)
        card.bind("<Configure>", self._split_header, add="+")
        self._split_header()

    def _value(self, parent, text: str) -> ttk.Label:
        return ttk.Label(parent, text=text, style="FilmText.TLabel", justify="left")

    def _split_header(self, event=None):
        """The facts beside the summary in a wide card, under it in a narrow one."""
        parts = getattr(self, "_head_parts", None)
        if not parts:
            return
        body, left, right = parts
        try:
            card = self.cards.get("header")
            width = event.width if event is not None else (card.winfo_width() if card is not None else 0)
            wide = width < 50 or width / self.s >= HEADER_SPLIT_FROM
            if wide == self._head_wide:
                return
            self._head_wide = wide
            if wide:
                left.grid(row=0, column=0, columnspan=1, sticky="nsew", padx=(0, 24))
                right.grid(row=0, column=1, columnspan=1, sticky="new", pady=(6, 0))
            else:
                left.grid(row=0, column=0, columnspan=2, sticky="nsew", padx=(0, 0))
                right.grid(row=1, column=0, columnspan=2, sticky="new", pady=(14, 0))
        except tk.TclError:
            pass

    def _open_director(self, credit):
        self._goto("Six Degrees", person_id=credit.person, name=credit.name, directors=True)

    # -- ratings -----------------------------------------------------------------------------------------------------
    def _ratings_card(self, ratings: dict, prediction: dict | None = None):
        card = self.cards.get("ratings") or self._card("ratings", "half", "How it's rated", RATINGS_HINT)
        rows = rating_rows(ratings, prediction)
        view = self.views.get("ratings")
        if view is None:
            view = CR.guard(ChartView(card.body, height=ratings_height(len(rows)), width=1))
            view.grid(row=0, column=0, sticky="ew")
            self.views["ratings"] = view
            self.ratings_note = ttk.Label(card.body, text="", style="FilmNote.TLabel", justify="left")
            self.ratings_note.grid(row=1, column=0, sticky="w", pady=(8, 0))
            self._wrap(self.ratings_note, card, 30)
        view.rows = rows
        view.show(lambda p: draw_ratings(p, rows), height=ratings_height(len(rows)))
        self.ratings_note.configure(text=ratings_note(ratings, prediction))

    # -- you and this film -------------------------------------------------------------------------------------------
    def _catalog_plays(self) -> dict:
        """Plex's own count of your plays of the film on show (history_lines' 'catalog')."""
        film = self.film
        return {"plays": film.owner_plays, "last_played": FP._local(film.last_played, minutes=True),
                "rating": film.owner_rating, "note": FP.PLAYS_NOTE, "library_items": len(film.plex_ids)}

    def _history_card(self, history: dict | None):
        """history: the viewing-history part's answer; None while it's on its way (the card says so, with your
        rating - rather than Plex's count, which the answer might then contradict)."""
        card = self.cards.get("history") or self._card("history", "half", "You and this film")
        b = card.body
        self._clear(b)
        film = getattr(self, "film", None)
        if history is None:
            rating = film.owner_rating if film is not None else None
            ttk.Label(b, text=READING_PLAYS, style="FilmMuted.TLabel").grid(row=0, column=0, sticky="w")
            ttk.Label(b, text=f"Your rating: {rating:g} out of 10" if rating is not None else "Not rated by you",
                      style="FilmText.TLabel").grid(row=1, column=0, sticky="w", pady=(2, 0))
            return
        if "catalog" not in history and film is not None:
            history = dict(history, catalog=self._catalog_plays())   # (a job that failed: Plex's count, and why)
        lines = history_lines(history)
        if lines["summary"] == "Not played yet" and film is not None and film.added_at:
            added = FP._local(film.added_at)
            if added:
                lines["summary"] += f" - in Plex since {day_text(added)}"
        ttk.Label(b, text=lines["summary"], style="FilmVerdict.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(b, text=lines["rating"], style="FilmText.TLabel").grid(row=1, column=0, sticky="w", pady=(2, 0))
        row = 2
        if lines["plays"]:
            ttk.Label(b, text="On record", style="FilmField.TLabel").grid(row=row, column=0, sticky="w",
                                                                           pady=(10, 2))
            row += 1
            for line in lines["plays"]:
                ttk.Label(b, text=line, style="FilmText.TLabel").grid(row=row, column=0, sticky="w")
                row += 1
            if lines["more"]:
                ttk.Label(b, text=f"and {plural(lines['more'], 'earlier play')}", style="FilmMuted.TLabel").grid(
                    row=row, column=0, sticky="w")
                row += 1
        if lines["resume"]:
            label = ttk.Label(b, text=lines["resume"], style="FilmText.TLabel", justify="left")
            label.grid(row=row, column=0, sticky="w", pady=(8, 0))
            self._wrap(label, card, 30)
            row += 1
        for note in lines["notes"]:
            label = ttk.Label(b, text=note, style="FilmMuted.TLabel", justify="left")
            label.grid(row=row, column=0, sticky="w", pady=(8, 0))
            self._wrap(label, card, 30)
            row += 1
        if lines["year"]:
            year = lines["year"]
            LinkLabel(b, text=f"See {year} in review  →", style="FilmLink.TLabel",
                      command=lambda: self._goto("Viewing", year=year)).grid(row=row, column=0, sticky="w",
                                                                              pady=(10, 0))
            row += 1

    def _history_done(self, answer: dict):
        self._history_card(answer)

    # -- stay after the credits? -------------------------------------------------------------------------------------
    def _credits_card(self, info: dict, credits: dict, film):
        card = self._card("credits", "full", "Stay after the credits?")
        b = card.body
        row_data = CR.make_row(film.title, film.year, film.plex_ids, film.libraries, credits, film.key)
        verdict = row_data["verdict"]
        line = ttk.Frame(b, style="FilmPlain.TFrame")
        line.grid(row=0, column=0, sticky="w")
        _swatch(line, lambda v=verdict: CR.COLORS[v], self.s, 12).grid(row=0, column=0, padx=(0, 8))
        ttk.Label(line, text=f"{CR.ICONS[verdict]} {verdict}", style="FilmVerdict.TLabel").grid(row=0, column=1)
        meaning = ttk.Label(b, text=CR.MEANINGS[verdict], style="FilmText.TLabel", justify="left")
        meaning.grid(row=1, column=0, sticky="w", pady=(4, 0))
        self._wrap(meaning, card, 30)
        copies = CR.copies_of(row_data)
        notes = []
        if copies and CR.first_scene_note(copies[0]):
            notes.append(CR.first_scene_note(copies[0]))
        if len(copies) > 1 and len({c.get("stay_after_credits") for c in copies}) > 1:
            notes.append(f"Your {len(copies)} copies differ: the verdict is for the one with the most to stay for.")
        unscanned = len(film.plex_ids) - len(copies)
        if copies and unscanned > 0:
            notes.append(f"{plural(unscanned, 'other copy', 'other copies')} "
                         f"{'hasn' if unscanned == 1 else 'haven'}'t been scanned for credits.")
        if notes:
            label = ttk.Label(b, text="\n".join(notes), style="FilmNote.TLabel", justify="left")
            label.grid(row=2, column=0, sticky="w", pady=(6, 0))
            self._wrap(label, card, 30)
        self.timeline_views = []
        named = len(copies) > 1
        for n, copy in enumerate(copies):
            cv = copy.get("stay_after_credits") or CR.NOT_SCANNED
            title = f"{copy.get('edition') or 'Regular edition'}  ·  {cv}" if named else "How the film ends"
            subtitle = f"Credits start at {CR.clock_of(copy.get('credits_start_sec')) or '?'}"
            if copy.get("duration_sec"):
                subtitle += f"  ·  the file ends at {CR.clock_of(copy['duration_sec'])}"
            view = CR.guard(ChartView(b, height=charts.timeline_height(1, 1), width=1))
            view.grid(row=3 + n, column=0, sticky="ew", pady=(10, 0))
            if copy.get("duration_sec"):
                view.show(CR.auto_height(view, lambda p, c=copy, t=title, s=subtitle: CR.draw_timeline(p, c, t, s)))
            else:
                self.show_message(view, "No timeline for this copy", "Plex didn't record how long the file is.")
            view.film_copy = copy
            self.timeline_views.append(view)
        LinkLabel(b, text="Open in the Credits tab  →", style="FilmLink.TLabel",
                  command=lambda: self._goto("Credits", film_key=info["key"], title=info["label"])).grid(
            row=3 + len(copies), column=0, sticky="w", pady=(10, 0))

    # -- cast and director -------------------------------------------------------------------------------------------
    def _people_card(self, people: dict):
        card = self._card("people", "full", "Cast and director" if len(people.get("directors") or []) <= 1
                          else "Cast and directors")
        b = card.body
        row = 0
        directors = people.get("directors") or []
        if directors:
            line = ttk.Frame(b, style="FilmPlain.TFrame")
            line.grid(row=row, column=0, sticky="ew")
            line.columnconfigure(1, weight=1)
            ttk.Label(line, text="Directed by", style="FilmField.TLabel").grid(row=0, column=0, sticky="nw",
                                                                               padx=(0, 10))
            _links(line, [(d["label"], d) for d in directors],
                   lambda d: self._goto("Six Degrees", person_id=d["id"], name=d["name"], directors=True),
                   sep="·").grid(row=0, column=1, sticky="ew")
            row += 1
        cast = people.get("cast") or []
        if not cast:
            ttk.Label(b, text="Plex hasn't recorded who's in it." if not directors else
                      "Plex hasn't recorded the cast.", style="FilmMuted.TLabel").grid(row=row, column=0, sticky="w",
                                                                                       pady=(8, 0))
            return
        hint = ttk.Label(b, text=CAST_HINT, style="FilmMuted.TLabel")
        hint.grid(row=row, column=0, sticky="w", pady=(8, 4))
        row += 1
        table = Table(b, [("order", "#", 44, "e"), ("label", "Name", 260, "w"), ("role", "Character", 260, "w"),
                          ("films_here", "Films here", 90, "e")],
                      height=min(len(cast), CAST_ROWS),
                      on_open=lambda r: self._goto("Six Degrees", person_id=r["id"], name=r["name"]))
        table.grid(row=row, column=0, sticky="ew")
        table.set_rows(cast)
        self.tables["cast"] = table

    # -- similar films -----------------------------------------------------------------------------------------------
    def _similar_card(self, answer: dict | None):
        card = self.cards.get("similar") or self._card("similar", "full", "Similar films", SIMILAR_HINT)
        b = card.body
        self._clear(b)
        for key in ("similar_unseen", "similar_seen"):   # (gone with the card's old content: drawn again below)
            self.tables.pop(key, None)
        if answer is None:
            ttk.Label(b, text=LOADING_SIMILAR, style="FilmMuted.TLabel").grid(row=0, column=0, sticky="w")
            return
        if not answer.get("ok", True):
            ttk.Label(b, text=f"Couldn't find similar films: {answer.get('error', '')}", style="FilmMuted.TLabel",
                      wraplength=int(600 * self.s), justify="left").grid(row=0, column=0, sticky="w")
            return
        unseen, seen = answer.get("unseen") or [], answer.get("seen") or []
        row = 0
        if answer.get("source") == "likeness":
            note = ttk.Label(b, text="Likeness only: Watch Next needs at least 30 of your ratings before it can "
                                     "predict how you'd rate them.", style="FilmNote.TLabel", justify="left")
            note.grid(row=row, column=0, sticky="w", pady=(0, 6))
            self._wrap(note, card, 30)
            row += 1
        if not unseen and not seen:
            text = answer.get("note") if answer.get("source") == "none" else \
                "Nothing on your shelf shares enough of its people, studio and genres."
            label = ttk.Label(b, text=text, style="FilmMuted.TLabel", justify="left")
            label.grid(row=row, column=0, sticky="w")
            self._wrap(label, card, 30)
            return
        lists = ttk.Frame(b, style="FilmPlain.TFrame")
        lists.grid(row=row, column=0, sticky="ew")
        panes = []
        for key, heading, cols, rows in (
                ("unseen", "Not seen yet", [("label", "Film", 260, "w"), ("likeness_text", "Likeness", 72, "e"),
                                            ("predicted", "Predicted", 76, "e")], unseen),
                ("seen", "You've rated", [("label", "Film", 260, "w"), ("likeness_text", "Likeness", 72, "e"),
                                          ("yours", "You gave", 72, "e")], seen)):
            pane = ttk.Frame(lists, style="FilmPlain.TFrame")
            pane.columnconfigure(0, weight=1)
            ttk.Label(pane, text=heading, style="FilmStrong.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 4))
            if rows:
                table = Table(pane, cols, height=min(max(len(unseen), len(seen), 3), 8),
                              on_open=lambda r: self.open_film(r["key"]))
                table.grid(row=1, column=0, sticky="ew")
                table.set_rows([dict(r, likeness_text=f"{r['likeness']:.2f}" if r.get("likeness") is not None
                                     else "",
                                     predicted=f"{r['predicted_rating']:.1f}" if r.get("predicted_rating")
                                     is not None else "",
                                     yours=f"{r['your_rating']:g}" if r.get("your_rating") is not None else "")
                                for r in rows])
                self.tables[f"similar_{key}"] = table
            else:
                ttk.Label(pane, text="None close enough." if key == "seen" else
                          "Nothing close that you haven't seen.", style="FilmMuted.TLabel").grid(
                    row=1, column=0, sticky="w")
            panes.append(pane)
        self._similar_panes = (lists, panes)
        lists.bind("<Configure>", self._split_similar, add="+")
        self._split_similar()
        link = {"like": self.film.label, "film_key": self.film.key}
        LinkLabel(b, text="More like this in Watch Next  →", style="FilmLink.TLabel",
                  command=lambda: self._goto("Watch Next", **link)).grid(row=row + 1, column=0, sticky="w",
                                                                         pady=(10, 0))

    def _split_similar(self, event=None):
        lists, panes = getattr(self, "_similar_panes", (None, []))
        if lists is None:
            return
        try:
            width = event.width if event is not None else lists.winfo_width()
            side = width < 50 or width / self.s >= SIDE_BY_SIDE_FROM
            if side == self._similar_side:
                return
            self._similar_side = side
            for c in range(2):
                lists.columnconfigure(c, weight=1 if (side or c == 0) else 0,
                                      uniform="film-similar" if side else "")
            for n, pane in enumerate(panes):
                if side:
                    pane.grid(row=0, column=n, sticky="nsew", padx=(0, 16) if n == 0 else (0, 0), pady=0)
                else:
                    pane.grid(row=n, column=0, columnspan=2, sticky="nsew", padx=0, pady=(0 if n == 0 else 12, 0))
        except tk.TclError:
            pass

    def _similar_done(self, answer: dict):
        self._similar_card(answer)
        prediction = answer.get("prediction") if answer.get("ok", True) else None
        if prediction and self.film.owner_rating is None:
            self._ratings_card(self.parts.get("ratings") or {}, prediction)

    # -- what critics said -------------------------------------------------------------------------------------------
    def _critics_done(self, answer: dict):
        card = self.cards["critics"]
        if not answer.get("available"):
            card.shown = False
            return
        card.shown = True
        b = card.body
        self._clear(b)
        if not answer.get("ok", True):
            self._problem(b, "Critics' verdicts", answer)
            return
        # (the critics backbone's shape: your closest critics, then the others; or one list of verdicts)
        if isinstance(answer.get("closest"), list) or isinstance(answer.get("others"), list):
            verdicts = list(answer.get("closest") or []) + list(answer.get("others") or [])
        else:
            verdicts = answer.get("verdicts")
        if not isinstance(verdicts, list):
            self._generic(b, answer)
            return
        summary = answer.get("summary") or answer.get("note") or ""
        if not verdicts:
            ttk.Label(b, text=summary or "Plex has no critics' reviews of this film.", style="FilmMuted.TLabel",
                      wraplength=int(600 * self.s), justify="left").grid(row=0, column=0, sticky="w")
            return
        if not summary:
            fresh = sum(1 for v in verdicts if v.get("verdict") == "Fresh")
            summary = f"{fresh} of {plural(len(verdicts), 'critic')} liked it."
        label = ttk.Label(b, text=summary, style="FilmNote.TLabel", justify="left")
        label.grid(row=0, column=0, sticky="w", pady=(0, 6))
        self._wrap(label, card, 30)
        rows = []
        least = _critics_setting("MIN_SHARED", 5)
        for v in verdicts:
            if not isinstance(v, dict):
                continue
            agree, shared, rank = v.get("agreement"), v.get("shared"), v.get("rank")
            # (agreement over a film or two says nothing: '100% of 1' beside a #1 at '86% of 29' only misleads)
            telling = isinstance(agree, (int, float)) and not isinstance(agree, bool) and (
                rank or not isinstance(shared, int) or shared >= least)
            rows.append(dict(v, critic_text=v.get("critic") or v.get("name") or "",
                             rank_text=f"#{rank}" if rank else "",
                             agree_text=(f"{agree:.0%}" + (f" of {shared}" if shared else "")) if telling else "",
                             verdict_text={"Fresh": "✔ Fresh", "Rotten": "✖ Rotten"}.get(v.get("verdict"),
                                                                                       v.get("verdict") or ""),
                             where=v.get("publication") or ""))
        quote = ttk.Label(b, text="", style="FilmText.TLabel", justify="left")

        def critic_id(r):
            value = r.get("critic_id", r.get("id"))
            return None if value in (None, "") else str(value)
        table = Table(b, [("critic_text", "Critic", 200, "w"), ("rank_text", "Closest", 64, "e"),
                          ("agree_text", "Agrees with you", 120, "e"), ("verdict_text", "Their verdict", 110, "w"),
                          ("where", "Where", 220, "w")],
                      height=min(max(len(rows), 3), 10),
                      on_select=lambda r: quote.configure(text=f"“{r['quote']}”  - {r['critic_text']}"
                                                          if r.get("quote") else ""),
                      on_open=lambda r: critic_id(r) and self._goto("Watch Next", critic=critic_id(r)))
        table.grid(row=1, column=0, sticky="ew")
        table.set_rows(rows)
        quote.grid(row=2, column=0, sticky="w", pady=(8, 0))
        self._wrap(quote, card, 30)
        closest = answer.get("closest_count") if isinstance(answer.get("closest_count"), int) else 0
        hint = ttk.Label(b, text=CRITICS_HINT.format(min=least, closest=closest or _critics_setting("CLOSEST", 25)),
                         style="FilmMuted.TLabel", justify="left")
        hint.grid(row=3, column=0, sticky="w", pady=(6, 0))
        self._wrap(hint, card, 30)
        self.tables["critics"] = table
        table.select_first()

    # -- your copies -------------------------------------------------------------------------------------------------
    def _files_done(self, answer: dict):
        card = self.cards["files"]
        if not answer.get("available"):
            card.shown = False
            return
        card.shown = True
        b = card.body
        self._clear(b)
        if not answer.get("ok", True):
            self._problem(b, "Your copies' files", answer)
            return
        copies = answer.get("copies")
        if not isinstance(copies, list):
            self._generic(b, answer)
            return
        if not copies:
            ttk.Label(b, text="Plex lists no files for this film.", style="FilmMuted.TLabel").grid(
                row=0, column=0, sticky="w")
            return
        row = 0
        for n, copy in enumerate(copies):
            lines = copy_lines(copy)
            ttk.Label(b, text=lines["title"], style="FilmStrong.TLabel").grid(row=row, column=0, sticky="w",
                                                                              pady=(0 if n == 0 else 12, 0))
            row += 1
            if lines["warning"]:
                line = ttk.Frame(b, style="FilmPlain.TFrame")
                line.grid(row=row, column=0, sticky="w")
                _swatch(line, "CRITICAL", self.s).grid(row=0, column=0, padx=(0, 6))
                ttk.Label(line, text="✖ " + lines["warning"], style="FilmText.TLabel").grid(row=0, column=1)
                row += 1
            for key in ("video", "audio", "subtitles"):
                if lines[key]:
                    label = ttk.Label(b, text=lines[key], style="FilmText.TLabel" if key == "video" else
                                      "FilmNote.TLabel", justify="left")
                    label.grid(row=row, column=0, sticky="w", pady=(2, 0))
                    self._wrap(label, card, 30)
                    row += 1
            for path in lines["paths"]:
                var = tk.StringVar(value=path)
                entry = ttk.Entry(b, textvariable=var, state="readonly")
                entry.var = var
                entry.grid(row=row, column=0, sticky="ew", pady=(3, 0))
                row += 1

    # -- library health ----------------------------------------------------------------------------------------------
    def _issues_done(self, answer: dict):
        card = self.cards["issues"]
        if not answer.get("available"):
            card.shown = False
            return
        card.shown = True
        b = card.body
        self._clear(b)
        if not answer.get("ok", True):
            self._problem(b, "Library health", answer)
            return
        issues = answer.get("issues")
        if not isinstance(issues, list):
            self._generic(b, answer)
            return
        if not issues:
            line = ttk.Frame(b, style="FilmPlain.TFrame")
            line.grid(row=0, column=0, sticky="w")
            _swatch(line, "GOOD", self.s).grid(row=0, column=0, padx=(0, 8))
            ttk.Label(line, text="✔ Nothing to fix for this film.", style="FilmText.TLabel").grid(row=0, column=1)
            return
        for n, issue in enumerate(issues):
            severity = str(issue.get("severity") or "").lower()
            _color, icon, word = SEVERITY.get(severity, (T.BASELINE, "i", ""))
            box = ttk.Frame(b, style="FilmPlain.TFrame", cursor="hand2")
            box.grid(row=n, column=0, sticky="ew", pady=(0 if n == 0 else 10, 0))
            box.columnconfigure(1, weight=1)
            _swatch(box, lambda k=severity: SEVERITY.get(k, (T.BASELINE,))[0], self.s, 12).grid(
                row=0, column=0, sticky="n", padx=(0, 8), pady=(3, 0))
            title = f"{icon}  {issue.get('title') or issue.get('id') or 'Issue'}"
            if word:
                title += f"  ·  {word}"
            if issue.get("edition"):
                title += f"  ·  {issue['edition']}"
            head = ttk.Label(box, text=title, style="FilmStrong.TLabel", cursor="hand2")
            head.grid(row=0, column=1, sticky="w")
            detail = issue.get("detail") or issue.get("why") or ""
            widgets = [box, head]
            if detail:
                label = ttk.Label(box, text=detail, style="FilmNote.TLabel", justify="left", cursor="hand2")
                label.grid(row=1, column=1, sticky="w")
                self._wrap(label, card, 60)
                widgets.append(label)
            if issue.get("id"):
                # (the Library Doctor opens that list with this film's row picked - the row of this very copy when
                # the issue says which: plex_id is its library item, media_id the copy itself)
                def open_issue(i=issue["id"], p=issue.get("plex_id"), m=issue.get("media_id")):
                    self._goto("Library Doctor", issue=i, film_key=self.film.key, plex_id=p, media_id=m)
                for w in widgets:
                    w.bind("<Button-1>", lambda e, go=open_issue: go(), add="+")
                keyboard_link(box, open_issue, marks=[head])       # (Tab to it, Enter or Space opens it)
        ttk.Label(b, text="Click one (or Tab to it and press Enter) to see it in the Library Doctor.",
                  style="FilmMuted.TLabel").grid(
            row=len(issues), column=0, sticky="w", pady=(10, 0))

    # -- odd answers -------------------------------------------------------------------------------------------------
    def _problem(self, parent, what: str, answer: dict):
        label = ttk.Label(parent, text=f"{what} couldn't be read: {answer.get('error') or 'no reason given'}",
                          style="FilmMuted.TLabel", justify="left", wraplength=int(500 * self.s))
        label.grid(row=0, column=0, sticky="w")

    def _generic(self, parent, answer: dict):
        """An answer of a shape this tab doesn't know: its plain values as 'Label: value', and its first list of
        records as a table."""
        skip = {"ok", "available", "key", "film_key", "title", "year", "film", "action"}
        row = 0
        table_rows = None
        for k, v in answer.items():
            if k in skip:
                continue
            if isinstance(v, (str, int, float)) and not isinstance(v, bool) and str(v):
                ttk.Label(parent, text=f"{k.replace('_', ' ').capitalize()}: {v}", style="FilmText.TLabel",
                          wraplength=int(600 * self.s), justify="left").grid(row=row, column=0, sticky="w")
                row += 1
            elif table_rows is None and isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
                table_rows = v
        if table_rows:
            keys = [k for k, x in table_rows[0].items() if isinstance(x, (str, int, float)) and not isinstance(x, bool)]
            keys = keys[:5]
            if keys:
                table = Table(parent, [(k, k.replace("_", " ").capitalize(), 140, "w") for k in keys],
                              height=min(len(table_rows), 8))
                table.grid(row=row, column=0, sticky="ew", pady=(6, 0))
                table.set_rows(table_rows)
                row += 1
        if not row:
            ttk.Label(parent, text="Nothing to show.", style="FilmMuted.TLabel").grid(row=0, column=0, sticky="w")

    # -- for previews and tests --------------------------------------------------------------------------------------
    def texts(self) -> list[str]:
        """Every label and link text on the page (visible cards only), top to bottom as built."""
        out = []

        def walk(w):
            for c in w.winfo_children():
                if isinstance(c, Card) and not getattr(c, "shown", True):
                    continue
                try:
                    t = "" if isinstance(c, (ttk.Entry, tk.Entry)) else c.cget("text")
                except tk.TclError:
                    t = ""
                if t:
                    out.append(str(t))
                walk(c)
        walk(self.page)
        return out


# ---------------------------------------------------------------------------------------------------------
# The search box above the tabs
# ---------------------------------------------------------------------------------------------------------
class GlobalSearch(ttk.Frame):
    """The main window's search: an entry above the tabs with a drop-down of matches grouped by kind.

    Typing searches 60 ms after the last key (filmpage.search, on the window's thread: a few milliseconds). The
    keys stay in the box: Down / Up move through the matches, Enter opens the highlighted one (the best match
    until you move), Escape closes the list and a second Escape clears the box. A click opens a match. Ctrl+Tab /
    Ctrl+Shift+Tab (or Ctrl+Page Down / Up) switch tabs from here as from anywhere else in the window.
    catalog_changed() builds the collection's search index in the background; until it's ready the list says so.
    """

    DEBOUNCE_MS = 60
    MAX_LINES = 18
    MIN_WIDTH = 560
    HINT = "Ctrl+F  ·  films, people, collections, genres, countries, studios, libraries, critics"

    @property
    def FONT(self) -> str:                               # noqa: N802  (the drop-down's font)
        return T.font(self, 9)

    def __init__(self, parent, app, **kw):
        kw.setdefault("padding", (10, 6, 10, 4))
        kw.setdefault("style", "Chrome.TFrame")          # (part of the window's chrome, with the status bar)
        super().__init__(parent, **kw)
        self.app = app
        self.s = T.scale(self)
        self.catalog, self.state = None, "none"
        self.var = tk.StringVar()
        self.answer: dict | None = None
        self.rows: list[tuple[dict, list[dict]]] = []     # the drop-down: [(group header, [results])]
        self.message = ""                                  # ...or a line saying why there's nothing
        self.lines: list[tuple[str, object]] = []          # what each line of the list is: ('header', g) etc.
        self.selected: int | None = None                   # the highlighted line
        self.popup: tk.Toplevel | None = None
        self.listbox: tk.Text | None = None
        self._pending = None
        self._hide_check = None
        self._warming = None
        ttk.Label(self, text="Find", style="ChromeSection.TLabel").grid(row=0, column=0, padx=(0, 8))
        self.entry = ttk.Entry(self, textvariable=self.var, width=48)
        self.entry.grid(row=0, column=1, sticky="w")
        self.hint = ttk.Label(self, text=self.HINT, style="ChromeHint.TLabel")
        self.hint.grid(row=0, column=2, sticky="w", padx=(10, 0))
        self.columnconfigure(3, weight=1)
        self.var.trace_add("write", lambda *_: self._typed())
        e = self.entry
        e.bind("<Down>", lambda ev: self._move(1), add="+")
        e.bind("<Up>", lambda ev: self._move(-1), add="+")
        e.bind("<Next>", lambda ev: self._move(8), add="+")
        e.bind("<Prior>", lambda ev: self._move(-8), add="+")
        e.bind("<Return>", self._enter, add="+")
        e.bind("<KP_Enter>", self._enter, add="+")
        e.bind("<Escape>", self._escape, add="+")
        e.bind("<Tab>", lambda ev: self.hide(), add="+")
        # The window's Ctrl+Tab and Ctrl+Page Up / Down switch tabs only from inside the tabs (this box is above
        # them), and Tab / Page Up / Down have bindings of their own here: switch tabs from the box too
        for seq, step in (("<Control-Tab>", 1), ("<Control-Shift-Tab>", -1), ("<Control-Next>", 1),
                          ("<Control-Prior>", -1)):
            e.bind(seq, lambda ev, step=step: self._cycle_tabs(step), add="+")
        try:
            e.bind("<Control-ISO_Left_Tab>", lambda ev: self._cycle_tabs(-1), add="+")
        except tk.TclError:                          # (a keysym only X11 knows)
            pass
        e.bind("<FocusOut>", lambda ev: self._later_check(), add="+")
        e.bind("<FocusIn>", lambda ev: self._focused(), add="+")
        self._watch_job = self.after_idle(self._watch_window)
        self.bind("<Destroy>", self._destroyed, add="+")

    # -- the window around it ------------------------------------------------------------------------------------
    def _watch_window(self):
        """Close the list when the tab changes or the window moves, resizes or is minimised."""
        self._watch_job = None
        try:
            notebook = getattr(self.app, "notebook", None)
            if notebook is not None:
                notebook.bind("<<NotebookTabChanged>>", lambda e: self.hide(), add="+")
            top = self.winfo_toplevel()
            # (every widget in the window has the window's bindings too, so these run for each one resized or
            # hidden: tell them apart in Tcl, rather than calling Python for every one of them)
            self._window_cmd = self.register(lambda: self.hide())
            for seq in ("<Configure>", "<Unmap>"):
                top.bind(seq, f'+if {{"%W" eq "{top}"}} {{catch {{{self._window_cmd}}}}}')
        except tk.TclError:
            pass

    def _destroyed(self, event):
        if event.widget is not self:
            return
        cmd = getattr(self, "_window_cmd", None)
        if cmd:                                      # (the command itself goes with this widget)
            try:
                top = self.winfo_toplevel()
                for seq in ("<Configure>", "<Unmap>"):
                    script = top.bind(seq) or ""
                    top.bind(seq, "\n".join(line for line in script.split("\n") if cmd not in line))
            except tk.TclError:
                pass
            self._window_cmd = None
        for name in ("_pending", "_hide_check", "_watch_job"):
            job = getattr(self, name)
            if job is not None:
                try:
                    self.after_cancel(job)
                except (tk.TclError, ValueError):
                    pass
                setattr(self, name, None)

    # -- the collection -------------------------------------------------------------------------------------------
    def catalog_changed(self, catalog, state: str):
        """A collection has loaded (or is loading, or failed): build its search index in the background."""
        self.catalog, self.state = catalog, state
        self.answer, self.rows, self.lines, self.selected = None, [], [], None
        self._warming = None
        if state == "ready" and catalog is not None:
            self._warm()
        if self.var.get().strip():
            self._refresh()

    def _warm(self):
        """Build the index in the background, unless it's built or being built."""
        catalog = self.catalog
        if catalog is None or FP.search_index(catalog, build=False) is not None:
            return
        job = self._warming
        if job is not None and not getattr(job, "cancelled", False) and not getattr(job, "ended", False):
            return

        def done(_index):
            if catalog is self.catalog and self.var.get().strip():
                self._refresh()

        def failed(message):
            if catalog is self.catalog:
                self.lines = [("message", f"The search couldn't get ready: {message}")]
                self._render()
        self._warming = self.app.run(lambda: FP.warm(catalog), done, failed, key="search.index")

    def ready(self) -> bool:
        return self.catalog is not None and FP.search_index(self.catalog, build=False) is not None

    # -- searching ------------------------------------------------------------------------------------------------
    def query(self, text: str) -> dict:
        """Search now and fill the drop-down's model (rows, message); returns the search answer."""
        text = str(text or "")
        self.rows, self.lines, self.selected, self.message = [], [], None, ""
        if self.state != "ready" or self.catalog is None:
            self.message = ("The collection is still loading..." if self.state == "loading"
                            else "The collection isn't loaded yet.")
            self.answer = {"ok": False, "error": self.message}
            self.lines = [("message", self.message)]     # (the drop-down says so as you type)
            return self.answer
        if not text.strip():
            self.answer = None
            return {"ok": True, "groups": [], "q": text}
        answer = FP.search(self.catalog, {"q": text, "wait": False})
        self.answer = answer
        if not answer.get("ok"):
            self.message = answer.get("error", "")
        elif answer.get("note") and not answer.get("groups") and answer.get("ready", True):
            self.message = "Keep typing..."
        elif not answer.get("ready", True):
            self.message = "Getting search ready..."
            self._warm()                              # (in case the job building it was called off)
        elif not answer.get("groups"):
            self.message = f"Nothing matches “{text.strip()}”"
        for g in answer.get("groups") or []:
            header = {k: g.get(k) for k in ("group", "kind", "title", "total")}
            self.rows.append((header, list(g.get("results") or [])))
        for header, results in self.rows:
            self.lines.append(("header", header))
            for r in results:
                self.lines.append(("result", r))
        if self.message:
            self.lines = [("message", self.message)]
        self.selected = next((i for i, (kind, _x) in enumerate(self.lines) if kind == "result"), None)
        return answer

    def results(self) -> list[dict]:
        return [r for _h, rs in self.rows for r in rs]

    def _typed(self):
        if self._pending is not None:
            try:
                self.after_cancel(self._pending)
            except (tk.TclError, ValueError):
                pass
        self._pending = self.after(self.DEBOUNCE_MS, self._refresh)

    def _refresh(self):
        if self._pending is not None:                # (called straight away: the timer isn't needed now)
            try:
                self.after_cancel(self._pending)
            except (tk.TclError, ValueError):
                pass
            self._pending = None
        text = self.var.get()
        if not text.strip():
            self.rows, self.lines, self.selected, self.message = [], [], None, ""
            self.hide()
            return
        self.query(text)
        self._render()

    # -- keys -----------------------------------------------------------------------------------------------------
    def shortcut(self, _event=None):
        """Ctrl+F / Ctrl+K anywhere in the window."""
        self.focus()
        return "break"

    def focus(self):
        try:
            self.entry.focus_set()
            self.entry.select_range(0, "end")
            self.entry.icursor("end")
        except tk.TclError:
            pass

    def _focused(self):
        if self.var.get().strip() and self.lines and self.popup is not None and not self._shown():
            self._render()

    def _move(self, step: int):
        if self._pending is not None:              # (typed, then straight to the arrows: search first)
            self._refresh()
        if not self._shown() and self.var.get().strip():
            if not self.lines:
                self.query(self.var.get())
            self._render()
        results = [i for i, (kind, _x) in enumerate(self.lines) if kind == "result"]
        if not results:
            return "break"
        if self.selected not in results:
            self.selected = results[0]
        else:
            n = results.index(self.selected) + step
            self.selected = results[min(max(n, 0), len(results) - 1)]
        self._highlight()
        return "break"

    def _cycle_tabs(self, step: int):
        """Ctrl+Tab / Ctrl+Shift+Tab (or Ctrl+Page Down / Up) in the box: the next or previous tab."""
        self.hide()
        notebook = getattr(self.app, "notebook", None)
        if notebook is not None:
            try:
                self.tk.call("ttk::notebook::CycleTab", str(notebook), step)
            except tk.TclError:
                pass
        return "break"

    def _enter(self, _event=None):
        if self._pending is not None:
            self._refresh()
        if not self.lines and self.var.get().strip():
            self.query(self.var.get())
        chosen = self.lines[self.selected][1] if self.selected is not None and self.selected < len(self.lines) \
            and self.lines[self.selected][0] == "result" else None
        if chosen is not None:
            self.open(chosen)
        else:
            self.open_top()
        return "break"

    def _escape(self, _event=None):
        if self._shown():
            self.hide()
        elif self.var.get():
            self.var.set("")
            self.rows, self.lines, self.selected, self.message = [], [], None, ""
        return "break"

    # -- opening a match ---------------------------------------------------------------------------------------------
    def target(self, result: dict) -> tuple[str, dict]:
        """Where a match opens: (tab, navigate's arguments)."""
        kind, id_ = result.get("kind"), result.get("id")
        if kind == "film":
            return "Film", {"film_key": result.get("key") or id_, "title": result.get("label")}
        if kind == "person":
            kw = {"person_id": id_, "name": result.get("name") or result.get("label")}
            if result.get("directs"):
                kw["directors"] = True
            return "Six Degrees", kw
        if kind in ("collection", "genre", "country", "library"):
            return "Watch Next", {kind: id_}
        if kind == "studio":
            return "Film", {"studio": id_}
        if kind == "critic":
            return "Watch Next", {"critic": id_}
        return "Film", {"title": result.get("label")}

    def open(self, result: dict):
        tab, kwargs = self.target(result)
        self.hide()
        try:
            self.entry.select_range(0, "end")        # typing again starts a new search
        except tk.TclError:
            pass
        if self.app.goto(tab, **kwargs) is None:
            self.app.set_status(f"The {tab} tab isn't available.")
        return tab, kwargs

    def open_top(self):
        results = self.results()
        if results:
            return self.open(results[0])
        if self.message:
            self.app.set_status(self.message)
        return None

    # -- the drop-down -----------------------------------------------------------------------------------------------
    def _shown(self) -> bool:
        try:
            return self.popup is not None and self.popup.winfo_viewable()
        except tk.TclError:
            return False

    def hide(self):
        if self.popup is not None:
            try:
                self.popup.withdraw()
            except tk.TclError:
                pass

    def _later_check(self):
        if self._hide_check is not None:
            try:
                self.after_cancel(self._hide_check)
            except (tk.TclError, ValueError):
                pass
        self._hide_check = self.after(150, self._maybe_hide)

    def _maybe_hide(self):
        self._hide_check = None
        try:
            focus = self.focus_get()
        except (tk.TclError, KeyError):
            focus = None
        if focus is not self.entry and focus is not self.listbox:
            self.hide()

    def _make_popup(self):
        self.popup = tk.Toplevel(self)
        self.popup.withdraw()
        self.popup.wm_overrideredirect(True)
        self.listbox = t = tk.Text(self.popup, wrap="none", cursor="arrow", borderwidth=0, relief="flat",
                                   highlightthickness=1, font=self.FONT, padx=6, pady=4, spacing1=2, spacing3=2,
                                   takefocus=0)
        # the list's own colours (a menu's: it drops over the page), its edge, and its lines' - in the look in use
        T.tint(t, background="MENU_BG", foreground="MENU_FG", highlightbackground="MENU_BORDER",
               highlightcolor="MENU_BORDER", insertbackground="INSERT", selectbackground="LIST_SELECT_BG",
               selectforeground="LIST_SELECT_FG")
        bar = ttk.Scrollbar(self.popup, orient="vertical", command=t.yview)
        t.configure(yscrollcommand=bar.set)
        t.grid(row=0, column=0, sticky="nsew")
        bar.grid(row=0, column=1, sticky="ns")
        self._bar = bar
        self.popup.columnconfigure(0, weight=1)
        self.popup.rowconfigure(0, weight=1)
        t.tag_configure("header", font=T.font(t, 9, "bold"), spacing1=6)
        T.tint_tag(t, "header", foreground="ACCENT")
        T.tint_tag(t, "detail", foreground="MUTED")
        T.tint_tag(t, "message", foreground="MUTED")
        T.tint_tag(t, "hover", background="HOVER")
        # the selected line in the selection's own ink, detail and all (muted text on it was too faint)
        T.tint_tag(t, "current", background="LIST_SELECT_BG", foreground="LIST_SELECT_FG")
        t.bind("<Button-1>", self._clicked)
        t.bind("<Motion>", self._hovered)
        t.bind("<Leave>", lambda e: t.tag_remove("hover", "1.0", "end"))

    def _render(self):
        """Show the drop-down for the current model - only on a window that's showing (never in tests)."""
        try:
            if not self.entry.winfo_viewable():
                return
        except tk.TclError:
            return
        if not self.lines:
            self.hide()
            return
        if self.popup is None:
            self._make_popup()
        t = self.listbox
        self.s = T.scale(self)                             # (Settings > Text size may have changed it)
        font = tkfont.Font(root=self._root(), font=self.FONT)
        width = max(self.entry.winfo_width(), int(self.MIN_WIDTH * self.s))
        room = width - int(40 * self.s)
        t.configure(state="normal")
        t.delete("1.0", "end")
        for n, (kind, item) in enumerate(self.lines):
            if n:
                t.insert("end", "\n")
            if kind == "header":
                total, shown = item.get("total") or 0, sum(1 for k, x in self.lines if k == "result"
                                                           and x.get("kind") == item.get("kind"))
                text = f"{item.get('title')}  ·  {total:,}" if total <= shown else \
                    f"{item.get('title')}  ·  {shown} of {total:,}"
                t.insert("end", text, ("header",))
            elif kind == "result":
                label, detail = result_line(item)
                label = _fit(font, "   " + label, room)
                t.insert("end", label, ())
                if detail:
                    left = room - font.measure(label + "    ")
                    if left > 40:
                        t.insert("end", "    " + _fit(font, detail, left), ("detail",))
            else:
                t.insert("end", "  " + str(item), ("message",))
        t.configure(state="disabled")
        lines = len(self.lines)
        line_h = font.metrics("linespace") + int(4 * self.s)
        headers = sum(1 for k, _x in self.lines if k == "header")
        height = min(lines, self.MAX_LINES) * line_h + headers * int(6 * self.s) + int(12 * self.s)
        if lines > self.MAX_LINES:
            self._bar.grid()
        else:
            self._bar.grid_remove()
        x = self.entry.winfo_rootx()
        y = self.entry.winfo_rooty() + self.entry.winfo_height() + 2
        self.popup.wm_geometry(f"{width}x{height}+{x}+{y}")
        self.popup.deiconify()
        self.popup.lift()
        # The list at the size just given before _highlight's see() works out what shows: the first time the list
        # opens it's still at Tk's first guess (a line high, say), and see() would scroll it a line or two down
        try:
            self.popup.update_idletasks()
        except tk.TclError:
            return
        t.yview_moveto(0)
        self._highlight()

    def _highlight(self):
        t = self.listbox
        if t is None:
            return
        t.tag_remove("current", "1.0", "end")
        if self.selected is not None:
            line = self.selected + 1
            t.tag_add("current", f"{line}.0", f"{line + 1}.0")
            t.see(f"{line}.0")

    def _line_at(self, event) -> int | None:
        try:
            line = int(self.listbox.index(f"@{event.x},{event.y}").split(".")[0]) - 1
        except (tk.TclError, ValueError):
            return None
        return line if 0 <= line < len(self.lines) else None

    def _hovered(self, event):
        t = self.listbox
        t.tag_remove("hover", "1.0", "end")
        n = self._line_at(event)
        if n is not None and self.lines[n][0] == "result":
            t.tag_add("hover", f"{n + 1}.0", f"{n + 2}.0")

    def _clicked(self, event):
        n = self._line_at(event)
        if n is not None:
            kind, item = self.lines[n]
            if kind == "result":
                self.selected = n
                self.open(item)
            elif kind == "header":               # a heading: the first match under it
                nxt = n + 1
                if nxt < len(self.lines) and self.lines[nxt][0] == "result":
                    self.selected = nxt
                    self._highlight()
        self.entry.focus_set()
        return "break"


def _fit(font, text: str, width: int) -> str:
    """The text cut short with an ellipsis to fit `width` pixels in a tkinter font."""
    if font.measure(text) <= width:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if font.measure(text[:mid].rstrip() + "…") <= width:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo].rstrip() + "…" if lo else ""
