"""The Watch Next tab: what to watch from your own shelves, predicted from your own star ratings.

Three views over the recommender (projectionist/recommend.py, through the ask actions 'recommend', 'taste' and
'evaluate'):

  Recommendations          filters on the left; the films on the right; for the selected film, how its
                           prediction splits (its scores, the kind of film, its people and studios - parts of the
                           one model, so they add up), why it's suggested, what counts against it, similar films
                           you rated, its people (links to Six Degrees) and a waterfall of how it adds up
  Your taste               the kinds of film you rate above or below their IMDb/RT reputation (a diverging
                           chart); clicking a bar filters the recommendations to that kind
  Your critics             the Rotten Tomatoes critics whose Fresh/Rotten verdicts agree with your ratings
                           (projectionist/critics.py, the ask action 'critics'): the closest and the least in step,
                           every critic against the range luck gives a typical one, one critic's record with you
                           film by film and what they liked that you haven't seen, and an honest test of them as a
                           guide. Their picks are also a sort of the recommendations ('Your critics' picks')
  How accurate is this?    the recommender tested on your own ratings against two simple ways of guessing

The recommender is honest about its limits and so is this tab: when your ratings track IMDb closely, the scores
do most of the predicting and what the model adds are the reasons and the 'Personal picks' sort. The accuracy
view says that in numbers rather than claiming more. The critics are honest too: on a few hundred ratings no
critic may stand out from luck, and in testing adding them didn't make Watch Next's predictions measurably
better - so Watch Next doesn't use them. Your critics says both from the numbers - the second once its test is
run - and nothing else repeats them as fixed text (a later backup, or someone else's ratings, may say otherwise).

People and films picked by a link (another tab, a taste bar, 'More like this') go to the recommender by id, so
a namesake or a film with the same title and year can't stand in for them.

Settings > Watch Next > 'Never suggest films from' (defined here, with its row of tick boxes) leaves libraries and
genres out of the recommendations and your critics' picks - a concert library, say, or shorts - unless one is
picked in the Library or Genre box (an explicit pick wins). The results say what was left out, with a link to
change it; a change searches again at once. The model still learns from the films left out.

Requests run on a background thread (app.run): the first builds the model (~0.3 s on a few thousand films), later
ones take a tenth of a second or so, and the accuracy test a second or two. A newer request of the same kind (a
search, a taste chart, the accuracy test) calls the older one off, so it stops working rather than finishing
for nothing; a newly loaded collection calls them all off. An answer that arrives late all the same is dropped.
The one exception is the job that fits the model: a search or taste chart asked for while it works needs that
same model, so it waits for it (and is then quick) rather than calling it off and starting the fit again.
"""

from __future__ import annotations

import math
import random
import re
import time
import tkinter as tk
import weakref
import zlib
from collections import Counter
from tkinter import font as tkfont
from tkinter import ttk

from .. import critics as CR
from .. import prefs
from ..ask import handle
from ..catalog import fold
from ..jobs import check
from ..recommend import DEFAULT_LAMBDA, TASTE_KINDS
from . import charts
from . import settings as settings_tab
from . import theme as T
from .base import BaseTab
from .widgets import Card, ChartView, LinkLabel, ScrollFrame, SearchBox, Table, bind_wheel, suggester, wheel_units

PAD = 16                    # page margin (as on the Overview tab)
GAP = 10                    # space between panels
FILTER_WIDTH = 262          # the filter panel (device-independent px)
SHORT_WINDOW = 640          # a tab shorter than this (device-independent px) drops its page title
ACCURACY_HEIGHT = 196       # the accuracy chart: its title, three panel headings and three rows each
ANY = "Any"
RUNTIMES = (ANY, "90 min", "100 min", "120 min", "150 min")
DEFAULTS = {"sort": "predicted", "library": ANY, "genre": ANY, "decade": ANY, "runtime": ANY, "person": "",
            "like": "", "words": "", "include_watched": False, "variety": 2, "count": 30}
VARIETY = (0, 5)
COUNT = (10, 200)
DEBOUNCE_MS = 250           # a burst of filter changes makes one search

RESULT_COLUMNS = [("title", "Title", 200, "w"), ("year", "Year", 46, "e"), ("predicted", "Predicted", 66, "e"),
                  ("lift", "Lift", 48, "e"), ("imdb", "IMDb", 46, "e"), ("minutes", "Minutes", 58, "e"),
                  ("library", "Library", 110, "w"), ("confidence", "Confidence", 76, "w")]
NARROW_LIST = 600           # a results list narrower than this (device-independent px) leaves out Library

# The taste kinds in the order the selector shows them: (key, plural, singular)
KINDS = [("genre", "Genres", "Genre"), ("decade", "Decades", "Decade"), ("studio", "Studios", "Studio"),
         ("country", "Countries", "Country"), ("director", "Directors", "Director"), ("actor", "Actors", "Actor"),
         ("collection", "Collections", "Collection"), ("library", "Libraries", "Library")]
KIND_NAMES = {k: (plural, singular) for k, plural, singular in KINDS}
TASTE_SHOWN = 8             # bars each way in the chart (the table lists them all)

TILT_HELP = ("Tilt is how much higher (+) or lower (-) you rate a kind of film than its IMDb and Rotten Tomatoes "
             "scores would predict, in points on your 0-10 scale (2 points = one star). +0.5 means you rate "
             "them about a quarter of a star above their reputation. Small groups swing more, so the chart favours "
             "tilts seen across more films, and shows in brackets how many films you rated.")

CONFIDENCE = {
    "high": "High confidence - you've rated films with several of its people before.",
    "medium": "Medium confidence - you've rated films with one of its people, or one much like it.",
    "low": "Low confidence - little of your own history to go on, so this leans on the IMDb/RT scores.",
}

CREDITS_VERDICT = T.live(lambda: {     # icon, colour (status colours: credits verdicts only), words
    "Yes": ("✔", T.GOOD, "Plex found a likely scene"),
    "Maybe": ("?", T.WARNING, "maybe - worth a look"),
    "None found": ("–", T.MUTED, "nothing found"),
    "": ("·", T.MUTED, "no credits markers for this film"),
})

ACCURACY_HELP = ("It hides a fifth of your ratings, predicts them from the rest, and repeats that until every "
                 "rating has been predicted - twice - by a model that never saw it. Then it compares those guesses "
                 "with two simple ways of guessing: your average for everything, and the IMDb/RT scores alone.")

# The Recommendations sort: (value, words, hint under it). (No hint for your critics' picks: the filter panel has
# no room to spare at the window's smallest, and the results' own hint says what they are.)
SORT_CHOICES = (("predicted", "Highest predicted rating", None),
                ("personal", "Personal picks", "films you'd rate above their reputation"),
                ("critics", "Your critics' picks", None))

# (Only what's always true: whether the critics would make Watch Next more accurate is for the test in Your critics
# to say, from the numbers - it isn't run for the picks. The picks' note saying the same is left out under it.)
CRITICS_RESULTS_HINT = ("Critics: how many of your closest critics who reviewed it called it Fresh.  Predicted: "
                        "Watch Next's own guess, which doesn't use these critics.")

# Your critics
CRITIC_COLUMNS = [("name", "Critic", 140, "w"), ("publication", "Publication", 124, "w"), ("shared", "Films", 44, "e"),
                  ("agreement", "Agreed", 54, "e"), ("match", "Match", 50, "e")]
CRITIC_LISTS = 25           # critics in the Closest / Least in step lists
CRITICS_SHOWN = 6           # films in each list of one critic's details
TWO_CARDS_FROM = 980        # the list and the chart side by side from this page width (device-independent px)
TWO_LISTS_FROM = 700        # one critic's two lists side by side from this card width
NARROW_CRITICS = 425        # a critics list narrower than this leaves out Publication
FUNNEL_HEIGHT = 300
STRIP_WIDTH, STRIP_HEIGHT = 330, 210
CRITICS_EVAL_HEIGHT = 208
CRITIC_METHODS = [("Your closest critics", "Your closest critics"), ("All critics", "All critics"),
                  ("Rotten Tomatoes Tomatometer", "Tomatometer"), ("IMDb rating", "IMDb"),
                  ("Watch Next's model", "Watch Next's model")]
CRITICS_EVAL_HELP = ("For every film you rated that critics reviewed, it picks your closest critics as if you'd "
                     "never rated that film, and sees how well their verdicts put your films in order - against "
                     "critics in general, the Tomatometer, IMDb and Watch Next's own model. Then it tries adding "
                     "them to Watch Next's model, to see if its guesses get better. It takes a few seconds.")

# Settings > Watch Next > 'Never suggest films from': libraries and genres the suggestions leave out (and the Film
# tab's 'Well rated, not seen yet'). An explicit pick wins: a library chosen in the Library box - or from a taste
# bar - lifts the libraries left out, and a genre chosen lifts that genre. Watch Next still learns from your ratings
# of films in them.
LEAVE_OUT = "watchnext_leave_out"
SEEN_BY = "seen_accounts"           # Settings > Your collection (projectionist/catalog.py): their plays count as seen
LEAVE_OUT_PARTS = ("libraries", "genres")
GENRE_COLUMNS = 3           # the Settings row's genre tick boxes, in this many columns
LEFT_OUT_CHANGE = "Change"


# ---------------------------------------------------------------------------------------------------------
# Plain functions (easy to test)
# ---------------------------------------------------------------------------------------------------------
def parse_decade(value) -> int | None:
    """1994, '1990s', 'The 1990s' -> 1990; Any / '' / None -> None."""
    if value is None or value == "" or value == ANY:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value) // 10 * 10
    m = re.search(r"\d{4}", str(value))
    return int(m.group()) // 10 * 10 if m else None


def parse_runtime(value) -> int | None:
    m = re.match(r"\s*(\d+)", str(value or ""))
    return int(m.group(1)) if m else None


def nothing_left_out() -> dict:
    return {part: [] for part in LEAVE_OUT_PARTS}


def parse_leave_out(value) -> dict:
    """What 'Never suggest films from' keeps: {'libraries': [names], 'genres': [names]} - each name once, whatever
    its case, in the order given. A missing part is an empty one; anything else is a ValueError."""
    if not isinstance(value, dict):
        raise ValueError(f"Never suggest films from: libraries and genres, not {value!r}")
    out = {}
    for part in LEAVE_OUT_PARTS:
        names = value.get(part) or []
        if isinstance(names, str):
            names = [names]
        if not isinstance(names, (list, tuple)) or not all(isinstance(n, str) for n in names):
            raise ValueError(f"Never suggest films from: {part} are names, not {names!r}")
        kept, seen = [], set()
        for name in (n.strip() for n in names):
            if name and fold(name) not in seen:
                seen.add(fold(name))
                kept.append(name)
        out[part] = kept
    return out


def _the(names, one: str, many: str) -> str:
    """'the Short genre', 'the Short and TV Movie genres' ('' for none)."""
    names = [str(n) for n in names or []]
    if not names:
        return ""
    return f"the {_either(names, 'and')} {one if len(names) == 1 else many}"


def leave_out_words(libraries=(), genres=()) -> str:
    """What's left out, to follow 'films in': 'the Concerts library or the Short and TV Movie genres'
    ('' for nothing)."""
    return " or ".join(w for w in (_the(libraries, "library", "libraries"), _the(genres, "genre", "genres")) if w)


def leave_out_summary(value: dict) -> str:
    """The setting in words: 'Films in the Short genre' / 'Nothing left out'."""
    words = leave_out_words(value.get("libraries"), value.get("genres"))
    return f"Films in {words}" if words else "Nothing left out"


def request_left_out(req: dict) -> tuple[list[str], list[str]]:
    """(libraries, genres) a request leaves out."""
    def names(key):
        value = req.get(key) or []
        return [value] if isinstance(value, str) else [str(v) for v in value]
    return names("exclude_libraries"), names("exclude_genres")


def left_out_line(req: dict, answer: dict | None = None) -> str:
    """The results' line about what the search left out as Settings asks ('' when nothing was): 'Left out, as
    you asked in Settings: films in the Short genre - 3 more would match.'"""
    words = leave_out_words(*request_left_out(req))
    if not words:
        return ""
    more = (answer or {}).get("left_out") or 0
    return f"Left out, as you asked in Settings: films in {words}" + (f" - {more:,} more would match." if more else ".")


prefs.section("Watch Next", order=30, hint="What the Watch Next tab suggests.")
prefs.define(LEAVE_OUT, "Watch Next", "Never suggest films from", kind="leave_out",
             default=lambda _app: nothing_left_out(), parse=parse_leave_out, format=leave_out_summary,
             help="Watch Next's suggestions leave out films in the libraries and genres ticked here, and so does the "
                  "Film tab's 'Well rated, not seen yet' list. Choosing one of them in Watch Next's Library or Genre "
                  "box still finds its films, and your ratings of them still teach Watch Next your taste.")


def build_request(f: dict, extra: tuple | None = None, leave_out: dict | None = None, also_seen_by=None) -> dict:
    """The 'recommend' request for the filter panel's values (see DEFAULTS); extra = (request key, value).
    person_id / like_key, when the panel knows exactly who or which film is meant, go instead of the name.
    Sorted by your critics' picks, it's the 'critics' action's picks view instead, with the same filters.
    leave_out = what Settings leaves out ({'libraries': [...], 'genres': [...]}): exclude_libraries /
    exclude_genres - but a library picked in the panel lifts the libraries, and a genre picked, that genre.
    also_seen_by = the accounts Settings > Your collection counts as yours (their plays count as seen)."""
    sort = f.get("sort", "predicted")
    if sort == "critics":
        req = {"action": "critics", "view": "picks", "sort": "critics"}
    else:
        req = {"action": "recommend", "sort": sort}
    req.update(count=f.get("count", 30), max_per_director=f.get("variety", 2))
    for key in ("library", "genre"):
        if f.get(key) and f[key] != ANY:
            req[key] = f[key]
    decade = parse_decade(f.get("decade"))
    if decade is not None:
        req["decade"] = decade
    runtime = parse_runtime(f.get("runtime"))
    if runtime:
        req["max_runtime"] = runtime
    if f.get("person_id"):
        req["with_id"] = f["person_id"]
    elif f.get("person"):
        req["with"] = f["person"]
    if f.get("like_key"):
        req["like_key"] = f["like_key"]
    elif f.get("like"):
        req["like"] = f["like"]
    if f.get("words"):
        req["text"] = f["words"]
    if f.get("include_watched"):
        req["include_watched"] = True
    if extra:
        req[extra[0]] = extra[1]
    if leave_out:
        libraries = [] if req.get("library") else list(leave_out.get("libraries") or [])
        picked = fold(str(req.get("genre") or ""))
        genres = [g for g in leave_out.get("genres") or [] if fold(g) != picked]
        if libraries:
            req["exclude_libraries"] = libraries
        if genres:
            req["exclude_genres"] = genres
    if also_seen_by:
        req["also_seen_by"] = list(also_seen_by)
    return req


class Ranked(int):
    """A word that sorts by its rank rather than alphabetically - High, Medium, Low - while the list shows the
    word (a table sorts numbers as numbers and shows them as text)."""

    def __new__(cls, rank: int, word: str):
        obj = super().__new__(cls, rank)
        obj.word = word
        return obj

    def __str__(self):
        return self.word


CONFIDENCE_RANK = {"high": 0, "medium": 1, "low": 2}


def critics_cell(r: dict):
    """Your critics' picks' column: '3 of 3' (called it Fresh, of your closest critics who reviewed it), sorting
    by how one-sided they were."""
    c = r.get("critics") or {}
    fresh, rotten = c.get("fresh", 0), c.get("rotten", 0)
    return Ranked(round(c.get("score", 0.0) * 1000), f"{fresh} of {fresh + rotten}")


def result_row(rank: int, r: dict, sort: str | None = None) -> dict:
    """One row of the results table (the full result rides along under 'result'). Sorted by your critics' picks,
    the Lift column holds how many of your closest critics called it Fresh."""
    confidence = str(r.get("confidence", ""))
    lift = critics_cell(r) if sort == "critics" else f"{r.get('personal_lift', 0.0):+.2f}"
    return {"rank": rank, "title": r["title"], "year": r.get("year") or "", "predicted": r["predicted_rating"],
            "lift": lift, "imdb": r.get("imdb_rating"),
            "minutes": r.get("runtime_min"), "library": ", ".join(r.get("libraries") or []),
            "confidence": Ranked(CONFIDENCE_RANK[confidence], confidence.capitalize())
            if confidence in CONFIDENCE_RANK else confidence.capitalize(), "result": r}


def film_label(title: str, year) -> str:
    return f"{title} ({year})" if year else str(title)


def plain_note(note: str) -> str:
    """The recommender's notes, in this panel's words rather than the request's."""
    note = re.sub(r"^'(.*)' taken as ", r"“\1” taken as ", note)      # (quoted as the other tabs quote)
    return note.replace("(max_per_director 0 = no limit)", "(Variety 0 = no limit)").replace(
        "; with_id picks another.", " - the suggestions in 'With' tell them apart.")


def _either(words: list[str], last: str = "or") -> str:
    """'A', 'A or B', 'A, B or C'."""
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + f" {last} {words[-1]}"


def problem_message(answer: dict) -> tuple[str, str]:
    """(heading, what to try) for a search the recommender couldn't do. Someone or a film that isn't there is
    said as every tab says it: 'No one called “X” in your collection' / 'No film called “X” in your collection'."""
    error = str(answer.get("error") or "Something went wrong.")
    suggestions = [str(s) for s in answer.get("suggestions") or []][:4]
    m = re.fullmatch(r"no (one|film) matching '(.*)'", error)
    if m:
        heading = f"{'No one' if m.group(1) == 'one' else 'No film'} called “{m.group(2)}” in your collection"
        return heading, (f"Did you mean {_either(suggestions)}?" if suggestions else
                         "Check the spelling - suggestions appear as you type.")
    if re.fullmatch(r"no film with key '.*'", error):
        return "That film isn't in your collection", "Pick another film in 'Like'."
    if re.fullmatch(r"no one with id '.*'", error):
        return "That person isn't in your collection", "Pick someone else in 'With'."
    if answer.get("reason") == "no_reviews":
        return ("No critic reviews to go on", "Plex keeps Rotten Tomatoes reviews for films its movie agent "
                "matched, and there are none here. Sort by 'Highest predicted rating' instead.")
    sub = error[:1].upper() + error[1:] + ("" if error.endswith(".") else ".")
    if answer.get("reason") == "flat_ratings":
        return "Nothing to compare yet", sub
    return ("Not enough ratings yet" if "rate at least" in error else "Couldn't search"), sub


def search_status(answer: dict, req: dict, seconds: float) -> str:
    """The status line after a search: how many films it found - or, when there are none, why, in the words the
    results panel uses ('Watch Next: nothing you haven't seen is much like Harbour Lights (1956).'). Someone or a film
    that isn't there is said as every tab says it, without the tab's name: 'No one called “X” in your
    collection.'"""
    if not answer.get("ok"):
        heading, _sub = problem_message(answer)
        if heading.endswith("in your collection"):       # 'No one called “X” in your collection.'
            return heading + "."
        return "Watch Next: " + str(answer.get("error") or "couldn't search")
    results = answer.get("results") or []
    if not results:
        heading, _sub = empty_message(req, answer)
        return f"Watch Next: {heading[:1].lower()}{heading[1:]}."
    return f"Watch Next: {len(results)} of {answer.get('matching_films', 0):,} matching films ({seconds:.1f} s)"


def _like_only(req: dict) -> bool:
    """Only 'Like' narrows the search (the sort is the usual one - not Personal picks or your critics')."""
    return (bool(req.get("like") or req.get("like_key")) and _narrowed_by(req) <= {"like", "like_key"}
            and req.get("sort") not in ("personal", "critics"))


def _change(value: float, what: str, plural: bool = False) -> str:
    """'its people and studios add 0.26' / 'the kind of film takes off 0.12' / '... makes no difference'."""
    if abs(value) < 0.005:
        return f"{what} {'make' if plural else 'makes'} no difference"
    if value > 0:
        return f"{what} {'add' if plural else 'adds'} {value:.2f}"
    return f"{what} {'take' if plural else 'takes'} off {-value:.2f}"


def prediction_words(r: dict) -> str:
    """How a prediction splits, in words: 'its IMDb/RT scores suggest 8.68, the kind of film takes off 0.12 and
    its people and studios add 0.26'. The three are parts of the one model, so they add up to the prediction
    (give or take rounding: they're shown to two decimals, the prediction to one)."""
    expected = r.get("expected_from_scores")
    if expected is None:
        return ""
    parts = [f"its IMDb/RT scores suggest {expected:.2f}"]
    if r.get("kind_of_film") is not None:
        parts.append(_change(r["kind_of_film"], "the kind of film"))
    parts.append(_change(r.get("personal_lift", 0.0), "its people and studios", plural=True))
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def _narrowed_by(req: dict) -> set[str]:
    return {k for k in ("genre", "decade", "library", "max_runtime", "text", "like", "like_key", "with",
                        "with_id", "countries", "collections") if req.get(k)}


def empty_message(req: dict, answer: dict | None = None) -> tuple[str, str]:
    """(heading, what to try) when nothing matches. Asked for one person or collection and you've rated every
    film of theirs, it says so, rather than suggesting things that can't help. Films that would match but are in
    a library or genre Settings leaves out are said first: they're why."""
    left = (answer or {}).get("left_out") or 0
    if left:
        libraries, genres = request_left_out(req)
        box = _either([name for name, parts in (("Library", libraries), ("Genre", genres)) if parts] or ["Library"])
        return ("Every film that matches is left out",
                f"{_films(left)} would match, but Settings leaves out films in "
                f"{leave_out_words(libraries, genres) or 'some libraries or genres'}. Choose one of those in the "
                f"{box} box to see {'it' if left == 1 else 'them'} anyway.")
    played = (answer or {}).get("played_matches")
    like = (answer or {}).get("like_film")
    if like and _like_only(req) and (req.get("include_watched") or played == 0):
        # Nothing new is alike enough (the films that are, you've rated already): say so, and name them, rather
        # than 'no films match' as if a filter were to blame.
        close = [film_label(f["title"], f.get("year")) for f in (answer or {}).get("closest_rated") or []]
        seen = "rated" if req.get("include_watched") else "seen"
        if len(close) == 1:
            sub = f"The only film much like it is {close[0]}, and you've rated it already."
        elif close:
            sub = f"You've rated the films most like it already: {_either(close, 'and')}."
        else:
            sub = "No other film on your shelf shares enough of its people, studio and genres. Try another film " \
                  "in 'Like'."
        return f"Nothing you haven't {seen} is much like {like}", sub
    if req.get("sort") == "critics":
        # The critics are to blame only if the filters let some films through (the answer says how many; one
        # that doesn't say is taken to)
        if (answer or {}).get("filter_matches") != 0:
            return "None of your closest critics called these films Fresh", empty_hint(req, answer)
        return "No films match all of these", empty_hint(req, answer)
    narrowed = _narrowed_by(req)
    whose = None
    if narrowed and narrowed <= {"with", "with_id"}:
        whose = "of theirs"
    elif narrowed == {"collections"}:
        whose = "in that collection"
    if whose and req.get("sort") != "personal":
        if req.get("include_watched") or played == 0:
            return (f"You've rated every film {whose}",
                    "They all have your stars in Plex already, so there's nothing new to suggest.")
        if played:
            return (f"You've seen every film {whose}",
                    f"Tick 'Include films I've played but not rated' to see the {played} you've "
                    f"{'seen' if req.get('also_seen_by') else 'played'}.")
    return "No films match all of these", empty_hint(req, answer)


def empty_hint(req: dict, answer: dict | None = None) -> str:
    """What to try when nothing matches."""
    tips = []
    if req.get("like") or req.get("like_key"):
        tips.append("picking a different film in 'Like'")
    if req.get("text"):
        tips.append("using fewer words")
    if any(k in req for k in ("genre", "decade", "library")):
        tips.append("setting genre, decade or library to 'Any'")
    if req.get("max_runtime"):
        tips.append("allowing a longer runtime")
    if req.get("with") or req.get("with_id"):
        tips.append("picking someone else in 'With'")
    if req.get("sort") == "personal":
        tips.append("sorting by 'Highest predicted rating' (Personal picks only lists films predicted above "
                    "their reputation, and at or above your average)")
    if req.get("sort") == "critics" and (answer or {}).get("filter_matches") != 0:    # (0: no sort would help)
        tips.append("sorting by 'Highest predicted rating' (Your critics' picks only lists films more of your "
                    "closest critics called Fresh than Rotten)")
    if any(k in req for k in ("countries", "collections")):
        tips.append("clearing the extra filter")
    # Only when it would help: the answer says how many films you've played would match.
    if not req.get("include_watched") and (answer or {}).get("played_matches", 1):
        tips.append("including films you've played")
    if not tips:
        return "Every film here is one you've already rated."
    tips = tips[:3]
    if len(tips) == 1:
        return f"Try {tips[0]}."
    return "Try " + ", ".join(tips[:-1]) + f", or {tips[-1]}."


def closest_rated(catalog, film, count: int = 3) -> list:
    """The films you've rated that are most like `film`, as 'Like' measures it (likeness 0.1 or more, the
    closest first): what a Like search that finds nothing new can point to instead."""
    from ..recommend import _cosine, features, recommender
    try:
        rec = recommender(catalog, DEFAULT_LAMBDA)       # (fitted already: the search just used it)
    except ValueError:
        return []
    seed = rec.vector(features(film))
    close = sorted(((_cosine(seed, vector), other) for vector, other in rec.rated_vectors if other.key != film.key),
                   key=lambda t: (-t[0], t[1].title))
    return [other for likeness, other in close[:count] if likeness >= 0.1]


def with_closest(answer: dict, request: dict, catalog) -> dict:
    """A Like search that found nothing new: the answer, plus which film was meant ('like_film') and the films
    you've rated that are most like it ('closest_rated') - for empty_message(). Other answers as they are."""
    if not answer.get("ok") or answer.get("results") or not _like_only(request):
        return answer
    film = catalog.films.get(str(request.get("like_key") or ""))
    if film is None and request.get("like"):
        film = catalog.find_film(str(request["like"]))[0]
    if film is None:
        return answer
    return dict(answer, like_film=film.label,
                closest_rated=[{"title": f.title, "year": f.year, "your_rating": f.owner_rating, "key": f.key}
                               for f in closest_rated(catalog, film)])


def film_keys(catalog) -> dict:
    """'Title (Year)' -> the one film with that label (a label two films share is left out: the title search
    tells those apart). Built once per collection."""
    key = "ui.watchnext.film_keys"
    keys = catalog.cache.get(key)
    if keys is None:
        by_label: dict[str, list] = {}
        for f in catalog.films.values():
            by_label.setdefault(f.label, []).append(f.key)
        keys = catalog.cache[key] = {label: ks[0] for label, ks in by_label.items() if len(ks) == 1}
    return keys


def taste_filter(kind: str, label: str) -> tuple[str, object] | None:
    """What clicking a taste bar filters the recommendations by: (navigate key, value), or None (studios: the
    recommender can't filter by studio)."""
    if kind == "genre":
        return "genre", label
    if kind == "decade":
        decade = parse_decade(label)
        return ("decade", decade) if decade is not None else None
    if kind == "library":
        return "library", re.sub(r" library$", "", label)
    if kind == "director":
        return "person", re.sub(r" \(director\)$", "", label)
    if kind == "actor":
        return "person", label
    if kind == "country":
        return "country", label
    if kind == "collection":
        m = re.fullmatch(r"'(.*)' collection", label)
        return ("collection", m.group(1)) if m else None
    return None


# Plex's ISO names for some countries, in everyday words (the tooltip keeps Plex's)
SHORT_COUNTRY = {"United States of America": "United States", "Taiwan, Province of China": "Taiwan",
                 "Republic of Korea": "South Korea", "Korea, Republic of": "South Korea",
                 "United Kingdom of Great Britain and Northern Ireland": "United Kingdom",
                 "Russian Federation": "Russia", "Iran, Islamic Republic of": "Iran", "Viet Nam": "Vietnam",
                 "Hong Kong SAR China": "Hong Kong"}


def display_label(kind: str, label: str) -> str:
    """A taste label without what the heading already says: 'Ridley Scott (director)' -> 'Ridley Scott',
    "'Godzilla' collection" -> 'Godzilla', 'Documentaries library' -> 'Documentaries'."""
    if kind == "director":
        return re.sub(r" \(director\)$", "", label)
    if kind == "library":
        return re.sub(r" library$", "", label)
    if kind == "collection":
        m = re.fullmatch(r"'(.*)' collection", label)
        return m.group(1) if m else label
    if kind == "country":
        return SHORT_COUNTRY.get(label, label)
    return label


def split_rows(above: int, below: int, room: int) -> tuple[int, int]:
    """How many of the biggest tilts to show each way in `room` rows: half each, and a side with fewer lends
    its spare rows to the other."""
    a = min(above, room // 2)
    b = min(below, room - a)
    a = min(above, room - b)
    return a, b


def _films(n: int) -> str:
    return "1 film" if n == 1 else f"{n:,} films"


def taste_click(kind: str, row: dict) -> tuple[bool, bool, str]:
    """What clicking a taste row does: (clickable, include films you've played, the words for its tip).
    Rows say how many films of theirs you haven't rated; one with none has nothing to show."""
    person = kind in ("director", "actor")
    unseen, played = row.get("unseen"), row.get("played_not_rated") or 0
    if kind == "studio":                    # (the recommender can't filter by studio: the Film tab lists its films)
        return True, False, "Click to see every film of theirs on the Film tab"
    if unseen is None:                      # (an answer that doesn't say)
        return True, False, "Click to see films of this kind you haven't seen"

    def which(n):                           # a click lists the first DEFAULTS['count'], best first
        return f"the best of the {_films(n)}" if n > DEFAULTS["count"] else f"the {_films(n)}"

    if unseen:
        return True, False, f"Click to see {which(unseen)} {'of theirs ' if person else ''}you haven't seen"
    if played:
        return True, True, f"You've played the rest - click to see {which(played)} you haven't rated"
    whose = "of theirs" if person else "in it" if kind == "collection" else "of this kind"
    return False, False, f"You've rated every film {whose} on your shelves"


def taste_items(group: dict, kind: str = "genre", room: int = 2 * TASTE_SHOWN, clickable: bool = True) -> list[dict]:
    """Chart items for one taste group: the steadiest tilts each way (the answer's order: a tilt seen across many
    films before one from a few), drawn biggest first and most negative last, with how many films you rated."""
    above, below = group.get("above", []), group.get("below", [])
    a, b = split_rows(len(above), len(below), room)
    rows = sorted(above[:a], key=lambda r: -r["tilt"]) + sorted(below[:b], key=lambda r: -r["tilt"])
    items = []
    for n, r in enumerate(rows):
        t = r["tilt"]
        tip = (f"{r['label']}\nYou rate these {abs(t):.2f} points {'above' if t >= 0 else 'below'} what their "
               f"scores suggest\n{_films(r['films'])} you rated - your average {r['your_average']:.1f}")
        can, _played, words = taste_click(kind, r) if clickable else (False, False, "")
        if words:
            tip += "\n" + words
        items.append({"label": f"{display_label(kind, r['label'])} ({r['films']})", "value": t, "key": n,
                      "tip": tip, "row": r, "clickable": can})
    return items


class NotApplicable(float):
    """A measure a method can't have (one guess for every film can't rank them): drawn as no bar, 'n/a'."""


def _na_fmt(fmt):
    def f(v):
        if isinstance(v, NotApplicable):
            return "n/a"
        return fmt(v)
    return f


def model_scores(answer: dict) -> dict:
    """The model the app actually uses (the default strength), else the best one tried."""
    by = answer.get("model_by_lambda", {})
    for lam in (answer.get("default_lambda"), answer.get("best_lambda")):
        if lam is not None and f"{lam:g}" in by:
            return by[f"{lam:g}"]
    return next(iter(by.values()))


METHOD_LABELS = [("your average for everything", "Your average for everything", "Your average"),
                 ("IMDb / Rotten Tomatoes scores only", "IMDb/RT scores only", "IMDb/RT only")]
MODEL_LABEL = "This model"


def accuracy_panels(answer: dict, short: bool = False) -> list[dict]:
    """metric_panels data: average miss (lower is better), rank agreement and top picks rated 8+ (higher is
    better). A single guess can't rank anything, so your average's rank agreement is n/a, not a bar."""
    base = answer["baselines"]
    methods = [(s if short else long_, base[key], key == "your average for everything")
               for key, long_, s in METHOD_LABELS] + [(MODEL_LABEL, model_scores(answer), False)]
    share = answer.get("share_you_rated_8_plus")

    def rank_value(scores, constant):
        if constant:
            return NotApplicable(0.0)
        return max(scores["rank_agreement"], 0.0)       # below 0 = no better than chance: no bar

    def top_value(scores, constant):
        # One guess for every film can't pick favourites either: its 'top fifth' is just whichever films the
        # test happened to give a slightly higher average.
        return NotApplicable(0.0) if constant else scores["top_fifth_you_rated_8_plus"]

    return [
        {"title": "Average miss", "note": "Points off your rating - lower is better", "fmt": charts.fmt_2,
         "rows": [{"label": lab, "value": s["mean_error"], "emphasis": lab == MODEL_LABEL}
                  for lab, s, _c in methods]},
        {"title": "Rank agreement", "note": "1 = exactly your order - higher is better", "max": 1.0,
         "fmt": _na_fmt(charts.fmt_2),
         "rows": [{"label": lab, "value": rank_value(s, c), "emphasis": lab == MODEL_LABEL}
                  for lab, s, c in methods]},
        {"title": "Top picks you rated 8+",
         "note": "Higher is better" + (f" - {share:.0%} of all your ratings are 8+" if share is not None else ""),
         "max": 1.0, "fmt": _na_fmt(charts.fmt_pct),
         "rows": [{"label": lab, "value": top_value(s, c), "emphasis": lab == MODEL_LABEL}
                  for lab, s, c in methods]},
    ]


def _order_words(r: float) -> str:
    if r >= 0.8:
        return "very close"
    if r >= 0.6:
        return "fairly close"
    if r >= 0.4:
        return "loosely similar"
    if r >= 0.2:
        return "only roughly similar"
    return "hardly related"


def accuracy_verdict(answer: dict) -> tuple[str, str]:
    """(headline, paragraph) in plain English, from the numbers - never claiming more than they show."""
    model = model_scores(answer)
    avg = answer["baselines"]["your average for everything"]
    imdb = answer["baselines"]["IMDb / Rotten Tomatoes scores only"]
    m, i, a = model["mean_error"], imdb["mean_error"], avg["mean_error"]
    gain = round(i - m, 2)
    parts = [f"On films it hadn't seen, it misses your rating by {m:.2f} points on average "
             "(1 point = half a star)."]
    if gain >= 0.10:
        headline = "It adds something real to the IMDb/RT scores"
        parts.append(f"Going by the IMDb/RT scores alone misses by {i:.2f}, so knowing which people, studios and "
                     "genres you rate differently makes it clearly better.")
    elif gain >= 0.03:
        headline = "A little better than the scores alone"
        parts.append(f"Going by the IMDb/RT scores alone misses by {i:.2f}, so knowing which people, studios and "
                     "genres you rate differently helps a little.")
    elif gain > -0.03:
        headline = "Your taste tracks the critics closely"
        too = "too" if abs(gain) < 0.005 else "- about the same"
        parts.append(f"Going by the IMDb/RT scores alone misses by {i:.2f} {too}: your taste tracks the critics "
                     "and audiences closely, so the model mostly adds reasons, and the Personal picks sort.")
    else:
        headline = "No better than the scores alone"
        parts.append(f"Going by the IMDb/RT scores alone misses by {i:.2f} - slightly less - so treat its "
                     "personal touches as hints rather than facts.")
    your_avg = answer.get("your_average")
    guess = f"your average ({your_avg:.1f})" if your_avg is not None else "your average"
    if m >= a - 0.02:
        parts.append(f"It's no better than guessing {guess} for every film, which misses by {a:.2f}.")
    else:
        parts.append(f"Guessing {guess} for every film would miss by {a:.2f}.")
    r = model["rank_agreement"]
    parts.append(f"It puts your films in order {_order_words(r)} to how you rated them (rank agreement {r:.2f}, "
                 f"where 1 would be perfect and 0 no better than chance; the scores alone: "
                 f"{imdb['rank_agreement']:.2f}).")
    top, share = model["top_fifth_you_rated_8_plus"], answer.get("share_you_rated_8_plus")
    if share is not None:
        parts.append(f"Of the films it ranked in your top fifth, you'd rated {top:.0%} at 8 or more (the scores "
                     f"alone: {imdb['top_fifth_you_rated_8_plus']:.0%}), against {share:.0%} of everything "
                     "you've rated.")
    parts.append("It's measured on films you chose to watch and rate; films you haven't seen may be harder to "
                 "call.")
    return headline, " ".join(parts)


def method_text(answer: dict, seconds: float | None = None) -> str:
    """The small print under the verdict, in plain words (the help above already says how it's tested): how
    long it took, and - when the answer tried other settings of the model (the ask API can) - whether any of
    them did better, by the same measure as the chart."""
    bits = []
    by = answer.get("model_by_lambda", {})
    default = answer.get("default_lambda")
    if len(by) > 1 and default is not None and f"{default:g}" in by:
        gap = by[f"{default:g}"]["mean_error"] - min(s["mean_error"] for s in by.values())
        if gap < 0.005:
            bits.append("Other settings of the model did no better.")
        else:
            bits.append(f"Another setting of the model would have missed by {gap:.2f} points less.")
    if seconds is not None:
        bits.append(f"Took {seconds:.1f} s.")
    return " ".join(bits)


# -- Your critics: words from the numbers ------------------------------------------------------------------
def match_text(match) -> str:
    """A critic's match in points: '+13', '-10', '+0'."""
    if match is None:
        return ""
    return f"{round(match * 100):+d}"


def match_words(d: dict) -> str:
    """How one critic's Match comes from the numbers beside it (a critic answer): their own yardstick - a typical
    critic who says Fresh as often as they do, on the films you share - what they managed, and the pull toward 0
    when you share few films. 'Match +7 (#17 of 180): a typical critic who says Fresh as often as they do (58% of
    the time) would agree with you on 62% of these films; they managed 70% - 8 points above, counted as +7 because
    60 shared films still leave room for luck. (A coin tossed Fresh as often would manage 51%.)' The gap is the
    difference of the percentages shown, so it adds up on the page."""
    m = round(d["match"] * 100)
    rank = f" (#{d['rank']} of {d['ranked']:,})" if d.get("rank") else ""
    coin = f"(A coin tossed Fresh as often would manage {d['chance']:.0%}.)" if d.get("chance") is not None else ""
    if d.get("expected") is None or d.get("agreement") is None:       # (an answer without the yardstick)
        how = ("more in step with you than" if m > 0 else "less in step with you than" if m < 0 else
               "about as in step with you as")
        return f"Match {m:+d}{rank}: {how} a typical critic, allowing for how often they say Fresh. {coin}".strip()
    got, want = round(d["agreement"] * 100), round(d["expected"] * 100)
    edge = got - want
    often = f" ({d['fresh_share']:.0%} of the time)" if d.get("fresh_share") is not None else ""
    text = (f"Match {m:+d}{rank}: a typical critic who says Fresh as often as they do{often} would agree with you on "
            f"{want}% of these films; they managed {got}%")
    if edge:
        text += f" - {abs(edge)} point{'s' if abs(edge) != 1 else ''} {'above' if edge > 0 else 'below'}"
    else:
        text += " - just that"
    if m != edge:
        shared = d.get("shared", 0)
        text += (f", counted as {m:+d} because {shared:,} shared film{'s' if shared != 1 else ''} still "
                 f"leave{'s' if shared == 1 else ''} room for luck")
    return f"{text}. {coin}".strip()


def _pct(v) -> str:
    return f"{v:.0%}" if v is not None else "?"


def critic_not_found(asked) -> str:
    """A critic asked for (by a link, the search or navigate) who isn't there, said as every tab says it: 'No
    critic called “X” in your collection.' - or, for a critic's number, 'No critic numbered 99999 ...'."""
    text = str(asked or "").strip()
    if text.isdigit():
        return f"No critic numbered {text} in your collection."
    return f"No critic called “{text}” in your collection."


def critics_headline(ov: dict) -> tuple[str, str]:
    """(title, paragraph) at the top of Your critics: how critics in general go with you (the real finding),
    whether any really stand out (from the test, never assumed), and where critics are blind to your shelves."""
    if not ov.get("ok"):
        reason = ov.get("reason")
        error = str(ov.get("error") or "")
        sentence = error[:1].upper() + error[1:] + ("" if error.endswith(".") else ".")
        if reason == "no_reviews":
            return ("No critic reviews in this database",
                    "Plex keeps up to 20 Rotten Tomatoes reviews for each film its movie agent matched - none of "
                    "your films have any, so there are no critics to compare with your ratings." +
                    (f" ({sentence})" if ov.get("read_error") else ""))
        if reason == "few_ratings":
            return "Not enough ratings to compare yet", sentence + " Then this page finds the critics in step " \
                                                                   "with you."
        if reason == "flat_ratings":
            return "Nothing to compare yet", sentence + " Rate films you liked higher than ones you didn't."
        return "Couldn't compare the critics", sentence
    t = ov["typical"]
    test = ov.get("stand_out") or {}
    verdict = test.get("verdict")
    agree = t["lift"] >= 0.03
    if verdict == "real":
        title = ("Some critics really are closer to you than others" if agree else
                 "Critics barely track your ratings - but some really do")
    elif verdict == "too few":
        title = "Too few shared films to tell critics apart yet"
    else:
        title = ("Critics mostly agree with you - but none stands out yet" if agree else
                 "Critics' verdicts barely track your ratings")
    parts = [f"A typical critic agrees with you on {_pct(t['agreement'])} of the films you've both judged (a coin "
             f"tossed as often Fresh as they are would manage {_pct(t['chance'])})."]
    if t.get("when_fresh") is not None and t.get("when_rotten") is not None:
        parts.append(f"When a critic says Fresh you rate the film {t['when_fresh']:.1f} on average; Rotten, "
                     f"{t['when_rotten']:.1f}.")
    tested, least = test.get("tested", 0), test.get("min_shared", 10)
    if verdict == "real":
        parts.append(f"Some agree with you clearly more often than others - more than luck would explain "
                     f"({tested} critics share {least}+ films with you).")
    elif verdict == "too few":
        parts.append(f"Only {tested} critics share {least}+ films with you - too few to tell whether any really are "
                     "closer than others, so 'closest' means closest so far.")
    else:
        parts.append(f"The differences between critics are no bigger than luck would make ({tested} critics share "
                     f"{least}+ films with you), so 'closest' means closest so far.")
    coverage = f"Critics reviewed {ov['rated_with_reviews']:,} of your {ov['rated_films']:,} rated films"
    spots = ov.get("blind_spots") or []
    if spots:
        coverage += "; " + _either([f"{s['without_reviews']} of the {s['rated']} you rated in {s['library']}"
                                    for s in spots[:3]], "and") + " have none"
    parts.append(coverage + ".")
    return title, " ".join(parts)


def critics_list_hint(ov: dict, which: str) -> str:
    """The small print under the list of critics."""
    at = ov.get("liked_at")
    liked = f"{at:g}+ out of 10" if at is not None else "the ones you rated higher"
    text = (f"Films: films you rated that they reviewed. Agreed: how often their Fresh/Rotten matched whether you "
            f"liked it ({liked}). Match: points more (+) or less (-) often than a typical critic, allowing for how "
            "often they say Fresh; with few shared films it's pulled toward 0 (pick a critic to see how it adds "
            "up).")
    if which == "furthest":
        anti = ov.get("anti_twins", 0)
        if anti:                            # (beyond luck with every listed critic tested: critics.anti_twin)
            text += (f"  {anti} critic{'s disagree' if anti != 1 else ' disagrees'} with you more often than chance "
                     "would: their Rotten may be your Fresh.")
        else:
            text += ("  No critic reliably disagrees with you: the least in step agree less often than most, but "
                     "no more than luck could explain.")
    return text


def critic_rows(ov: dict, which: str) -> list[dict]:
    """The list's rows: your closest critics, the least in step, or everyone listed (from the answer's points)."""
    if which == "all":
        source = [dict(p, agreement=p["agreed"] / p["shared"]) for p in ov.get("points") or [] if p["shared"]]
    else:
        source = ov.get("closest" if which == "closest" else "furthest") or []
    return [{"id": r["id"], "name": r["name"], "publication": r.get("publication") or "", "shared": r["shared"],
             "agreement": f"{r['agreement']:.0%}", "match": match_text(r["match"]), "row": r} for r in source]


def critics_eval_panels(answer: dict) -> list[dict]:
    """metric_panels data for the critics test: rank agreement and top picks rated 8+ (higher is better)."""
    methods = answer.get("methods", {})
    rows = [(short, methods[key]) for key, short in CRITIC_METHODS if key in methods]
    if not rows:
        return []
    share = answer.get("share_you_rated_8_plus")          # (what a top fifth picked blind would get)
    return [
        {"title": "Rank agreement", "note": "1 = exactly your order - higher is better", "max": 1.0,
         "fmt": charts.fmt_2,
         "rows": [{"label": lab, "value": max(s["rank_agreement"], 0.0), "emphasis": lab == CRITIC_METHODS[0][1]}
                  for lab, s in rows]},
        {"title": "Top picks you rated 8+",
         "note": "Each one's top fifth - higher is better" + (f" - {share:.0%} of these films are 8+"
                                                               if share is not None else ""),
         "max": 1.0, "fmt": charts.fmt_pct,
         "rows": [{"label": lab, "value": s["top_fifth_you_rated_8_plus"], "emphasis": lab == CRITIC_METHODS[0][1]}
                  for lab, s in rows]},
    ]


def _from_to(a: float, b: float) -> str:
    """'from 0.70 to 0.66' - or to three places when they differ by less than 0.01, so a change of 0.001 doesn't
    read as 0.01 ('from 0.674 to 0.675', not 'from 0.67 to 0.68')."""
    places = 3 if abs(a - b) < 0.01 else 2
    return f"from {a:.{places}f} to {b:.{places}f}"


def critics_verdict(answer: dict) -> tuple[str, str]:
    """(headline, paragraph) for the critics test, from its numbers - never claiming more than they show: how
    well your closest critics put your films in order against the other four ways, against critics picked at
    random (whether choosing them by how they agree with you helps at all), and - when they trail critics in
    general - how few verdicts a film their order rests on."""
    ms = answer["methods"]
    rank = {key: ms[key]["rank_agreement"] for key, _short in CRITIC_METHODS if key in ms}
    c, a = rank["Your closest critics"], rank["All critics"]
    t, i, w = rank["Rotten Tomatoes Tomatometer"], rank["IMDb rating"], rank["Watch Next's model"]
    better = [name for name, v in (("the Tomatometer", t), ("IMDb", i), ("Watch Next's model", w)) if v > c + 0.02]
    if not better and c >= a + 0.05:
        headline = "Your closest critics are your best guide here"
    elif c >= a + 0.05:
        headline = f"Your closest critics beat critics in general - but not {_either(better)}"
    elif c < min(a, t, i, w) - 0.02:
        headline = "Your closest critics are the weakest guide here"
    else:
        headline = "Your closest critics are no better a guide than critics in general"
    covered = answer.get("films_covered", 0)
    parts = [f"Ranked by your closest critics' verdicts - each film judged by critics picked without it - the "
             f"{covered} films you rated that one of them reviewed come out in an order {_order_words(c)} to yours "
             f"(rank agreement {c:.2f}, where 1 would be perfect and 0 no better than chance). Critics as a whole: "
             f"{a:.2f}; the Tomatometer {t:.2f}; IMDb {i:.2f}; Watch Next's own model {w:.2f}."]
    panels = answer.get("random_panels") or {}
    if panels.get("rank_agreement") is not None:
        r, size = panels["rank_agreement"], panels.get("size", 0)
        beaten = panels.get("at_or_above_closest")          # the share of the random panels doing as well
        blind = f"Critics picked at random, {size} at a time, manage {r:.2f} on average"
        if c > r + 0.02 and (beaten is None or beaten <= 0.1):
            parts.append(blind + (f" ({beaten:.0%} of those panels do as well as yours)" if beaten is not None else "")
                         + " - so choosing the ones in step with you does help.")
        elif c > r + 0.02:
            parts.append(blind + f" - less, but {beaten:.0%} of those panels do as well as yours, so choosing them by "
                                 "how they agree with you may not help.")
        elif c >= r - 0.02:
            parts.append(blind + " - about the same, so choosing them by how they agree with you hasn't helped "
                                 "yet.")
        else:
            parts.append(blind + " - better, so choosing them by how they agree with you doesn't help on your "
                                 "ratings.")
    per_film = answer.get("verdicts_per_film") or {}
    if c < a - 0.02 and per_film.get("closest_one") is not None:
        one, two = per_film["closest_one"], per_film.get("closest_two", 0)
        few = "Most" if 2 * (one + two) > covered else f"{one + two:,}"
        text = (f"{few} of these films were reviewed by only one or two of your closest critics ({one:,} by one, "
                f"{two:,} by two)")
        if per_film.get("all_critics_average") is not None:
            text += f", while critics as a whole have about {per_film['all_critics_average']:.0f} reviews a film"
        parts.append(text + " - and one verdict can only say Fresh or Rotten.")
    picks = answer.get("picks", {})
    near, everyone = picks.get("closest", {}), picks.get("all", {})
    if near.get("liked") is not None:
        text = (f"When most of them called a film Fresh you liked it {near['liked']:.0%} of the time "
                f"({near['mostly_fresh']} films)")
        if everyone.get("liked") is not None:
            text += f"; when most critics did, {everyone['liked']:.0%}"
        if picks.get("liked_overall") is not None:
            text += f" - against {picks['liked_overall']:.0%} of these films overall"
        parts.append(text + ".")
    check = answer.get("model_check")
    if check:
        wo, wi = check["without_critics"], check["with_critics"]
        miss = f"{_from_to(wo['mean_error'], wi['mean_error'])} points"
        order = _from_to(wo["rank_agreement"], wi["rank_agreement"])
        if check.get("helps"):
            verb = "cut" if wi["mean_error"] < wo["mean_error"] else "changed"     # ('helps' goes by the rms error)
            parts.append(f"Adding them to Watch Next's model {verb} its average miss {miss} and put your films in a "
                         f"better order (rank agreement {order}) - a measurable gain on your ratings. Watch Next "
                         "doesn't use them yet; 'Your critics' picks' is the way to follow them.")
        else:
            parts.append(f"Adding them to Watch Next's model changed its average miss {miss} and its rank "
                         f"agreement {order} - no measurable gain - so Watch Next doesn't use them: 'Your critics' "
                         "picks' is a different way in, not a better guess.")
    parts.append("It's measured on films you chose to watch and rate.")
    return headline, " ".join(parts)


def critics_method_text(answer: dict, seconds: float | None = None) -> str:
    bits = [f"Tested on {answer.get('films_covered', 0)} of the {answer.get('films_tested', 0)} films you rated that "
            "critics reviewed (the rest weren't reviewed by one of your closest critics)."]
    if seconds is not None:
        bits.append(f"Took {seconds:.1f} s.")
    return " ".join(bits)


def namesake_labels(catalog) -> dict:
    """{'label': {id: label}, 'id': {label: id}} for everyone who shares their name, labelled as Six Degrees
    labels them ('John Smith (10 films, e.g. Paper Harbour)' - costars.namesake_labels), so following someone from one
    tab to the other shows the same words. Quick once the collection's namesakes are known (a few hundredths of
    a second, once per collection)."""
    key = "ui.watchnext.namesakes"
    index = catalog.cache.get(key)
    if index is None:
        from ..costars import namesake_labels as labels
        label = dict(labels(catalog))
        index = catalog.cache[key] = {"label": label, "id": {text: pid for pid, text in label.items()}}
    return index


def _people(catalog) -> dict:
    """Everyone on the shelf as the With box shows them - their name, or when two people share it the label
    namesake_labels() gives them - with suggest(text) over them, most films first: {'suggest', 'label': {id:
    label}, 'id': {label: id}} for namesakes. Built once per collection."""
    key = "ui.watchnext.people"
    index = catalog.cache.get(key)
    if index is None:
        named = namesake_labels(catalog)
        people = sorted(catalog.people.values(), key=lambda p: (-p.film_count, p.name))
        names = [named["label"].get(p.id, p.name) for p in people]
        index = catalog.cache[key] = dict(named, suggest=suggester(list(dict.fromkeys(names))))
    return index


def _people_suggester(catalog):
    """suggest(text) over everyone on the shelf, most films first (namesakes told apart by a film of theirs)."""
    return _people(catalog)["suggest"]


def _film_suggester(catalog):
    """suggest(text) over every film as 'Title (Year)': films you've seen first, then the best rated."""
    key = "ui.watchnext.film_suggester"
    s = catalog.cache.get(key)
    if s is None:
        films = sorted(catalog.films.values(), key=lambda f: (not f.watched, -(f.imdb_rating or 0), f.title))
        s = catalog.cache[key] = suggester(list(dict.fromkeys(f.label for f in films)))
    return s


def _autowrap(label, pad: int = 4):
    """Wrap a label to whatever width it's given."""
    def fit(event):
        if event.width < 40:                    # not laid out yet
            return
        want = max(event.width - pad, 80)
        if str(label.cget("wraplength")) != str(want):
            label.configure(wraplength=want)
    label.bind("<Configure>", fit, add="+")
    return label


# ---------------------------------------------------------------------------------------------------------
class TextLinks:
    """Clickable words in a tk.Text, each with its own tag: underlined with the hand pointer while the pointer is on
    it. One set of bindings on the shared 'link' tag serves them all (binding each link's tag would leave Tcl
    commands behind every time the text is refilled). .links lists (words, command) for keyboard-free tests."""

    _count = 0

    def __init__(self, text: tk.Text):
        self.text = text
        self.commands: dict[str, object] = {}
        self.links: list = []
        self.hover: str | None = None
        text.tag_bind("link", "<Enter>", self._hover)
        text.tag_bind("link", "<Motion>", self._hover)
        text.tag_bind("link", "<Leave>", self.left)
        text.tag_bind("link", "<Button-1>", self.clicked)

    def clear(self):
        """Empty the text (left editable, for filling) and forget its links."""
        self.left()
        self.commands, self.links = {}, []
        t = self.text
        t.configure(state="normal")
        t.delete("1.0", "end")
        for tag in t.tag_names():
            if re.fullmatch(r"xlink\d+", tag):
                t.tag_delete(tag)

    def add(self, words: str, command, tags=()):
        TextLinks._count += 1
        tag = f"xlink{TextLinks._count}"
        self.text.insert("end", words, ("link", tag) + tuple(tags))
        self.commands[tag] = command
        self.links.append((words, command))

    def at_pointer(self) -> str | None:
        try:
            tags = self.text.tag_names("current")
        except tk.TclError:
            return None
        return next((tag for tag in tags if tag in self.commands), None)

    def _hover(self, _event=None):
        tag = self.at_pointer()
        if tag != self.hover:
            self.left()
            if tag is not None:
                self.hover = tag
                self.text.tag_configure(tag, underline=True)
                self.text.configure(cursor="hand2")

    def left(self, _event=None):
        if self.hover is not None:
            try:
                self.text.tag_configure(self.hover, underline=False)
            except tk.TclError:
                pass
            self.hover = None
        try:
            self.text.configure(cursor="arrow")
        except tk.TclError:
            pass

    def clicked(self, _event=None):
        tag = self.at_pointer()
        if tag is not None:
            self.commands[tag]()


# The details panels' text colours, by tag - token names, so they follow the look (see _colour_details)
DETAIL_COLOURS = {"title": "INK", "year": "MUTED", "meta": "INK_2", "big": "INK", "soft": "INK_2", "muted": "MUTED",
                  "h": "ACCENT", "summary": "INK", "quote": "MUTED", "link": "LINK", "small": "MUTED"}


def _colour_details(t: tk.Text):
    """A details panel's colours - the text's own and its tags' (those it has) - in the look in use, and again
    whenever the look changes."""
    T.tint(t, background="CARD", foreground="INK")
    have = set(t.tag_names())
    for tag, token in DETAIL_COLOURS.items():
        if tag in have:
            T.tint_tag(t, tag, foreground=token)


def _style_details(t: tk.Text):
    """The text styles of the details panels."""
    bullet = int(t.tk.call("font", "measure", T.font(t, 9), "•  "))      # (in the font the text is in)
    t.tag_configure("title", font=T.font(t, 13, "bold"))
    t.tag_configure("meta")
    t.tag_configure("big", font=T.font(t, 10, "bold"), spacing1=4)
    t.tag_configure("soft")
    t.tag_configure("muted")
    t.tag_configure("h", font=T.font(t, 9, "bold"), spacing1=6, spacing3=2)
    t.tag_configure("bullet", lmargin1=2, lmargin2=2 + bullet, tabs=(2 + bullet,))
    t.tag_configure("quote", lmargin1=2 + bullet, lmargin2=2 + bullet, spacing3=3)
    t.tag_configure("link")
    t.tag_configure("small", font=T.font(t, 8), spacing1=4)
    _colour_details(t)


def _styles(style):
    """This tab's own styles (the card styles are the theme's), in the look in use."""
    style.configure("Page.TPanedwindow", background=T.PAGE)
    style.configure("Kind.Toolbutton", padding=(8, 3))
    style.configure("CriticsHead.TLabel", background=T.PAGE, foreground=T.INK, font=T.font(style, 10, "bold"))


# ---------------------------------------------------------------------------------------------------------
# Settings > Watch Next > 'Never suggest films from'
# ---------------------------------------------------------------------------------------------------------
def leave_out_choices(catalog, value: dict | None = None) -> dict:
    """What the Settings row offers: {'libraries': [(name, films)], 'genres': [(name, films)]} - the collection's
    libraries in its own order and its genres A-Z, each with how many films it has - then any name kept that the
    collection hasn't got (films None), so it can still be unticked. No collection yet: just the names kept."""
    out = {part: [] for part in LEAVE_OUT_PARTS}
    if catalog is not None:
        key = "ui.watchnext.leave_out_choices"
        counted = catalog.cache.get(key)
        if counted is None:
            films = list(catalog.films.values())
            libraries = Counter(name for f in films for name in dict.fromkeys(f.libraries) if name)
            genres = Counter(name for f in films for name in dict.fromkeys(f.genres) if name)
            order = list(dict.fromkeys(list(catalog.libraries) + sorted(libraries, key=str.casefold)))
            counted = catalog.cache[key] = {
                "libraries": [(name, libraries.get(name, 0)) for name in order],
                "genres": sorted(genres.items(), key=lambda kv: kv[0].casefold())}
        out = {part: list(counted[part]) for part in LEAVE_OUT_PARTS}
    for part in LEAVE_OUT_PARTS:
        have = {fold(name) for name, _n in out[part]}
        for name in (value or {}).get(part) or []:
            if fold(name) not in have:
                have.add(fold(name))
                out[part].append((name, None))
    return out


_LEAVE_OUT_CONTROLS = weakref.WeakSet()           # the Settings rows on show (a newly read collection redoes them)


class LeaveOutControl(settings_tab.Control):
    """The Settings row for 'Never suggest films from': a tick box for each library and each genre in the
    collection, with how many films it has, and a button that leaves nothing out. The boxes follow the collection:
    they're made again when another is read (the Watch Next tab passes that on) and when the Settings tab comes to
    the front."""

    def build(self):
        self.choices: dict | None = None
        self.vars: dict[tuple[str, str], tk.BooleanVar] = {}
        self.boxes: dict[tuple[str, str], ttk.Checkbutton] = {}
        self.lists = ttk.Frame(self.body, style="CardInner.TFrame")
        self.lists.grid(row=0, column=0, sticky="w")
        self.note = ttk.Label(self.body, text="", style="CardSmall.TLabel", justify="left")
        self.note.grid(row=1, column=0, sticky="w", pady=(2, 0))
        wraps = getattr(self.tab, "wraps", None)
        if isinstance(wraps, list):
            wraps.append(self.note)
        self.clear = ttk.Button(self.body, text="Leave nothing out", style="Small.TButton",
                                command=lambda: self.save(nothing_left_out()))
        self.clear.grid(row=2, column=0, sticky="w", pady=(6, 0))
        _LEAVE_OUT_CONTROLS.add(self)
        frame = getattr(self.tab, "frame", None)
        if frame is not None:
            frame.bind("<Map>", lambda e: self.refresh(), add="+")

    def _catalog(self):
        return getattr(self.tab.app, "catalog", None)

    def refresh(self):
        """The boxes for the collection loaded now, ticked as the setting is."""
        try:
            self.show(self.value(), fresh=True)
        except tk.TclError:                            # (the Settings tab has gone)
            _LEAVE_OUT_CONTROLS.discard(self)

    def show(self, value, fresh: bool = False):
        value = value or nothing_left_out()
        catalog = self._catalog()
        choices = leave_out_choices(catalog, value)
        names = {(part, name) for part in LEAVE_OUT_PARTS for name, _n in choices[part]}
        # (a name unticked that the collection hasn't got keeps its box until the row is next made again)
        if self.choices is None or not names <= set(self.vars) or (fresh and choices != self.choices):
            self._lay_out(choices, catalog is not None)
        for (part, name), var in self.vars.items():
            var.set(fold(name) in {fold(v) for v in value.get(part) or []})
        self.clear.state(["!disabled"] if any(value.get(part) for part in LEAVE_OUT_PARTS) else ["disabled"])
        self.note.configure(text="" if catalog is not None else
                            "Your libraries and genres are listed here once the collection has been read.")
        if self.note.cget("text"):
            self.note.grid()
        else:
            self.note.grid_remove()

    def _lay_out(self, choices: dict, have_collection: bool):
        self.choices = choices
        for child in self.lists.winfo_children():
            child.destroy()
        self.vars, self.boxes = {}, {}
        column = 0
        for part, heading in (("libraries", "Libraries"), ("genres", "Genres")):
            names = choices[part]
            if not names:
                continue
            frame = ttk.Frame(self.lists, style="CardInner.TFrame")
            frame.grid(row=0, column=column, sticky="nw", padx=(0, 28))
            column += 1
            ttk.Label(frame, text=heading, style="CardField.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 2))
            rows = len(names) if part == "libraries" else math.ceil(len(names) / GENRE_COLUMNS)
            for n, (name, films) in enumerate(names):
                var = tk.BooleanVar(self.frame, value=False)
                text = name if not have_collection else f"{name}  ({films:,})" if films is not None else \
                    f"{name}  (none on your shelf)"
                box = ttk.Checkbutton(frame, text=text, variable=var, style="Card.TCheckbutton",
                                      command=self._ticked)
                box.grid(row=1 + n % rows, column=n // rows, sticky="w", padx=(0, 14))
                self.vars[(part, name)] = var
                self.boxes[(part, name)] = box

    def _ticked(self):
        value = nothing_left_out()
        for (part, name), var in self.vars.items():
            if var.get():
                value[part].append(name)
        self.save(value)


settings_tab.add_control("leave_out", LeaveOutControl)


# ---------------------------------------------------------------------------------------------------------
class Tab(BaseTab):
    title = "Watch Next"

    def __init__(self, app, notebook):
        super().__init__(app, notebook)
        self.s = T.scale(self.frame)
        self._style()
        self.answer: dict | None = None             # the recommend answer on show
        self.request: dict | None = None            # ...and the request behind it
        self.selected: dict | None = None           # the result in the details
        self.extra: tuple | None = None             # (request key, value, words) from a taste click
        # The exact person / film behind the With and Like boxes' text, when a link said which one was meant
        # (two people can share a name, two films a title): (id or key, the text shown). Typing drops it.
        self._person: tuple[str, str] | None = None
        self._like: tuple[str, str] | None = None
        self.taste_answer: dict | None = None
        self.eval_answer: dict | None = None
        # Your critics: the overview on show, the critic in the details, the test's answer
        self.critics_answer: dict | None = None
        self.critic_answer: dict | None = None
        self.critics_eval_answer: dict | None = None
        self._critics_state: str | None = None      # None (not loaded), 'loading', 'ready' or 'problem'
        self._critic_id: str | None = None          # the critic in the details
        self._pending_critic: str | None = None     # asked for (navigate) before the page could show them
        self._critic_names: dict[str, str] = {}     # 'Find a critic' suggestions -> critic id
        self._critics_suggest = None
        self.timings: dict[str, float] = {}
        self._tokens = {"search": 0, "taste": 0, "eval": 0, "critics": 0, "critic": 0, "critics_eval": 0}
        self._busy = {"search": False, "taste": False, "eval": False, "critics_eval": False}
        self._model_ready = False                   # the recommender is fitted for this collection
        # Until it is, one job fits it - the first search, or the taste chart ('search' / 'taste'). A search or a
        # taste chart asked for meanwhile needs the same model, so it waits for that job (_waiting) rather than
        # calling it off and starting the fit over: one fit, however many clicks.
        self._fitting: str | None = None
        self._waiting: set[str] = set()
        self._need = {"search": False, "taste": False, "critics": False}
        self._timers: dict[str, str] = {}
        self.frame.bind("<Destroy>", lambda e: self._cancel_timers() if e.widget is self.frame else None, add="+")
        self._links = 0
        self._link_commands: dict[str, object] = {}     # the details' links: their tag -> what a click runs
        self._hover_link: str | None = None
        self._buttons: list = []                        # the details' buttons (destroyed with the details)
        self._films_by_id: dict = {}
        self.rated_count = 0
        self._build()
        self._show_state()

    def _style(self):
        T.add_styles(self.frame, _styles)

    # -- layout -------------------------------------------------------------------------------------------
    def _build(self):
        f = self.frame
        f.columnconfigure(0, weight=1)
        f.rowconfigure(1, weight=1)
        self.head = head = ttk.Frame(f, style="Page.TFrame", padding=(PAD, 10, PAD, 6))
        head.grid(row=0, column=0, sticky="ew")
        head.columnconfigure(1, weight=1)
        ttk.Label(head, text="What to watch next", style="PageTitle.TLabel").grid(row=0, column=0, sticky="w")
        self.model_label = ttk.Label(head, text="", style="PageHint.TLabel")
        self.model_label.grid(row=0, column=1, sticky="e", padx=(16, 0))
        f.bind("<Configure>", self._fit_height, add="+")

        self.placeholder = ChartView(f, height=320, background=T.PAGE)
        self.placeholder.grid(row=1, column=0, sticky="nsew")
        self.views = ttk.Notebook(f, style="Inner.TNotebook")
        self.views.grid(row=1, column=0, sticky="nsew", padx=PAD, pady=(0, 12))
        self.rec_page = self._build_recommendations(self.views)
        self.taste_page = self._build_taste(self.views)
        self.critics_page = self._build_critics(self.views)
        self.acc_page = self._build_accuracy(self.views)
        self.views.add(self.rec_page, text="Recommendations")
        self.views.add(self.taste_page, text="Your taste")
        self.views.add(self.critics_page, text="Your critics")
        self.views.add(self.acc_page, text="How accurate is this?")
        # (a view picked while the tab is hidden - its first, as it's made, or one restore() puts back - is filled
        # when the tab is shown: shown() refreshes it)
        self.views.bind("<<NotebookTabChanged>>", lambda e: self._refresh() if self._visible() else None, add="+")

    def _fit_columns(self, event=None):
        """A narrow results list leaves out the Library column (the details below still say it)."""
        width = event.width if event is not None else self.table.tree.winfo_width()
        if width < 40:
            return
        keys = [c[0] for c in RESULT_COLUMNS]
        narrow = NARROW_LIST
        if self.table.widened:              # (headings wider than Windows' font makes them: all the columns' room)
            narrow = max(narrow, sum(w for k, _h, w, _a in self.table.columns if k != "title") + 120 + 30)
        if width < narrow * self.s:
            keys.remove("library")
        if tuple(self.table.tree.tk.splitlist(self.table.tree.cget("displaycolumns"))) != tuple(keys):
            self.table.tree.configure(displaycolumns=keys)

    def _fit_height(self, event=None):
        """In a short window the page title goes, so the lists and charts keep their room."""
        height = event.height if event is not None else self.frame.winfo_height()
        if height < 50:
            return
        if height < SHORT_WINDOW * self.s:
            self.head.grid_remove()
        else:
            self.head.grid()

    # Recommendations ------------------------------------------------------------------------------------------
    def _build_recommendations(self, parent):
        page = ttk.Frame(parent, style="Page.TFrame", padding=(0, GAP, 0, 0))
        page.columnconfigure(1, weight=1)
        page.rowconfigure(0, weight=1)
        page.columnconfigure(0, minsize=int(FILTER_WIDTH * self.s))
        self.filter_card = self._build_filters(page)
        self.filter_card.grid(row=0, column=0, sticky="nsew", padx=(0, GAP))
        self.panes = ttk.Panedwindow(page, orient="vertical", style="Page.TPanedwindow")
        self.panes.grid(row=0, column=1, sticky="nsew")
        self.panes.add(self._build_results(self.panes), weight=3)
        self.panes.add(self._build_details(self.panes), weight=2)
        return page

    def _build_filters(self, parent) -> Card:
        card = Card(parent, title="Find films", padding=(12, 10))
        b = card.body
        b.columnconfigure(1, weight=1)
        self.vars = v = {
            "sort": tk.StringVar(value=DEFAULTS["sort"]), "library": tk.StringVar(value=ANY),
            "genre": tk.StringVar(value=ANY), "decade": tk.StringVar(value=ANY), "runtime": tk.StringVar(value=ANY),
            "words": tk.StringVar(value=""), "include_watched": tk.BooleanVar(value=False),
            "variety": tk.StringVar(value=str(DEFAULTS["variety"])),
            "count": tk.StringVar(value=str(DEFAULTS["count"])),
        }
        r = 0
        ttk.Label(b, text="Sort by", style="CardField.TLabel").grid(row=r, column=0, columnspan=2, sticky="w",
                                                                     pady=(4, 0))
        r += 1
        self.sort_buttons = {}
        self.filter_hints = []                  # the small print a short window leaves out (see _fit_filters)
        for n, (value, text, hint) in enumerate(SORT_CHOICES):
            rb = ttk.Radiobutton(b, text=text, value=value, variable=v["sort"], style="Card.TRadiobutton",
                                 command=lambda: self._changed(now=True))
            rb.grid(row=r, column=0, columnspan=2, sticky="w")
            self.sort_buttons[value] = rb
            r += 1
            if hint:
                label = ttk.Label(b, text=hint, style="CardSmall.TLabel")
                label.grid(row=r, column=0, columnspan=2, sticky="w", padx=(21, 0), pady=(0, 1))
                self.filter_hints.append(label)
                r += 1
            if n == len(SORT_CHOICES) - 1:
                rb.grid_configure(pady=(0, 6))
        self.boxes = {}
        for key, label in (("library", "Library"), ("genre", "Genre"), ("decade", "Decade"),
                           ("runtime", "Max runtime")):
            ttk.Label(b, text=label, style="CardField.TLabel").grid(row=r, column=0, sticky="w", padx=(0, 8),
                                                                    pady=2)
            box = ttk.Combobox(b, textvariable=v[key], state="readonly", width=16,
                               values=list(RUNTIMES) if key == "runtime" else [ANY])
            box.grid(row=r, column=1, sticky="ew", pady=2)
            box.bind("<<ComboboxSelected>>", lambda e: self._changed(now=True), add="+")
            self.boxes[key] = box
            r += 1
        ttk.Label(b, text="With", style="CardField.TLabel").grid(row=r, column=0, sticky="w", padx=(0, 8), pady=2)
        # A pick (Enter, or a suggestion) searches; so does Enter in an emptied box. Both go through the
        # debounce, so Enter on a name makes one search, not two.
        self.with_box = SearchBox(b, self._suggest_people, on_pick=lambda _t: self._changed(), width=16)
        self.with_box.grid(row=r, column=1, sticky="ew", pady=2)
        r += 1
        ttk.Label(b, text="Like", style="CardField.TLabel").grid(row=r, column=0, sticky="w", padx=(0, 8), pady=2)
        self.like_box = SearchBox(b, self._suggest_films, on_pick=lambda _t: self._changed(), width=16)
        self.like_box.grid(row=r, column=1, sticky="ew", pady=2)
        for box in (self.with_box, self.like_box):
            box.entry.bind("<Return>", lambda e: self._changed(), add="+")
        r += 1
        ttk.Label(b, text="Words", style="CardField.TLabel").grid(row=r, column=0, sticky="w", padx=(0, 8), pady=2)
        self.words_entry = ttk.Entry(b, textvariable=v["words"], width=16)
        self.words_entry.grid(row=r, column=1, sticky="ew", pady=2)
        self.words_entry.bind("<Return>", lambda e: self._changed(now=True), add="+")
        r += 1
        label = ttk.Label(b, text="in the title, tagline or summary", style="CardSmall.TLabel")
        label.grid(row=r, column=1, sticky="w", pady=(0, 2))
        self.filter_hints.append(label)
        r += 1
        self.watched_check = ttk.Checkbutton(b, text="Include films I've played\nbut not rated",
                                             variable=v["include_watched"], style="Card.TCheckbutton",
                                             command=lambda: self._changed(now=True))
        self.watched_check.grid(row=r, column=0, columnspan=2, sticky="w", pady=(4, 4))
        r += 1
        self.spins = {}
        for key, label, (lo, hi), step, after in (("variety", "Variety", VARIETY, 1, "max per director"),
                                                   ("count", "How many", COUNT, 10, "films")):
            ttk.Label(b, text=label, style="CardField.TLabel").grid(row=r, column=0, sticky="w", padx=(0, 8),
                                                                    pady=2)
            row = ttk.Frame(b, style="CardInner.TFrame")
            row.grid(row=r, column=1, sticky="w", pady=2)
            spin = ttk.Spinbox(row, from_=lo, to=hi, increment=step, width=4, textvariable=v[key],
                               command=self._changed)
            spin.grid(row=0, column=0, sticky="w")
            spin.bind("<Return>", lambda e: self._changed(now=True), add="+")
            ttk.Label(row, text=after, style="CardSmall.TLabel").grid(row=0, column=1, sticky="w", padx=(6, 0))
            self.spins[key] = spin
            r += 1
        # An extra filter from a taste click (a country or a collection) - hidden until there is one
        self.extra_row = ttk.Frame(b, style="CardInner.TFrame")
        self.extra_row.grid(row=r, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        self.extra_row.columnconfigure(0, weight=1)
        self.extra_label = ttk.Label(self.extra_row, text="", style="Card.TLabel")
        self.extra_label.grid(row=0, column=0, sticky="w")
        LinkLabel(self.extra_row, "clear", self._clear_extra, style="CardLink.TLabel").grid(row=0, column=1,
                                                                                             sticky="e")
        self.extra_row.grid_remove()
        r += 1
        buttons = ttk.Frame(b, style="CardInner.TFrame")
        buttons.grid(row=r, column=0, columnspan=2, sticky="w", pady=(10, 0))
        self.find_btn = ttk.Button(buttons, text="Find films", style="Accent.TButton",
                                   command=lambda: self._changed(now=True))
        self.find_btn.grid(row=0, column=0)
        self.reset_btn = ttk.Button(buttons, text="Reset", command=self._reset)
        self.reset_btn.grid(row=0, column=1, padx=(8, 0))
        self._hints_shown = True
        card.bind("<Configure>", lambda e: self._fit_filters(e.height), add="+")
        return card

    def _fit_filters(self, height: int | None = None):
        """In a window too short for the whole panel (the smallest the window goes, say), its two lines of small
        print go, so the Find films and Reset buttons at its foot stay in view; they come back when there's room."""
        card = self.filter_card if hasattr(self, "filter_card") else None
        if card is None:
            return
        try:
            have = card.winfo_height() if height is None else height
            if have < 50:
                return                                  # (not laid out yet)
            hints = sum(h.winfo_reqheight() for h in self.filter_hints) + 3            # (+ their padding)
            need = card.winfo_reqheight() + (0 if self._hints_shown else hints)     # (the panel with them)
            show = need <= have
            if show != self._hints_shown:
                self._hints_shown = show
                for h in self.filter_hints:
                    if show:
                        h.grid()
                    else:
                        h.grid_remove()
        except tk.TclError:
            pass

    def _build_results(self, parent) -> Card:
        card = Card(parent, padding=(12, 10))
        b = card.body
        b.columnconfigure(0, weight=1)
        b.rowconfigure(3, weight=1)
        head = ttk.Frame(b, style="CardInner.TFrame")
        head.grid(row=0, column=0, sticky="ew")
        head.columnconfigure(1, weight=1)
        self.results_title = ttk.Label(head, text="Films you haven't seen", style="CardTitle.TLabel")
        self.results_title.grid(row=0, column=0, sticky="w")
        self.results_count = ttk.Label(head, text="", style="CardHint.TLabel")
        self.results_count.grid(row=0, column=1, sticky="e", padx=(12, 0))
        self.results_hint = _autowrap(ttk.Label(b, text="", style="CardHint.TLabel", justify="left",
                                                wraplength=500))
        self.results_hint.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        # What Settings leaves out of the search, and a way there - shown only when something was
        self.left_out_row = ttk.Frame(b, style="CardInner.TFrame")
        self.left_out_row.grid(row=2, column=0, sticky="ew", pady=(0, 6))
        self.left_out_row.columnconfigure(0, weight=1)
        self.left_out_label = _autowrap(ttk.Label(self.left_out_row, text="", style="CardHint.TLabel",
                                                  justify="left", wraplength=500))
        self.left_out_label.grid(row=0, column=0, sticky="ew")
        self.left_out_link = LinkLabel(self.left_out_row, LEFT_OUT_CHANGE, self._change_left_out,
                                       style="CardLink.TLabel")
        self.left_out_link.grid(row=0, column=1, sticky="ne", padx=(8, 0))
        self.left_out_row.grid_remove()
        # Enter or a double-click on a film: its page on the Film tab (more like it, and its credits scenes, are
        # buttons in the details)
        self.table = Table(b, RESULT_COLUMNS, height=6, on_select=self._film_selected,
                           on_open=lambda row: self._open_film(row["result"]) if row.get("result") else None)
        self.table.grid(row=3, column=0, sticky="nsew")
        tree = self.table.tree
        for key in ("library", "confidence"):          # the title takes up any spare width
            tree.column(key, stretch=False)
        tree.column("title", minwidth=int(120 * self.s))
        tree.bind("<Configure>", self._fit_columns, add="+")
        self.results_msg = ChartView(b, height=160)
        self.results_msg.grid(row=3, column=0, sticky="nsew")
        self.results_msg.grid_remove()
        self.notes = _autowrap(ttk.Label(b, text="", style="CardSmall.TLabel", justify="left", wraplength=500))
        self.notes.grid(row=4, column=0, sticky="ew", pady=(6, 0))
        return card

    def _build_details(self, parent) -> Card:
        card = Card(parent, padding=(12, 10))
        b = card.body
        b.columnconfigure(0, weight=1, uniform="details")
        b.columnconfigure(2, weight=1, uniform="details")
        b.rowconfigure(0, weight=1)
        self.info = tk.Text(b, wrap="word", borderwidth=0, highlightthickness=0, font=T.font(b, 9), padx=2,
                            pady=0, cursor="arrow", height=9, width=30, spacing3=1, takefocus=0)
        bar = ttk.Scrollbar(b, orient="vertical", command=self.info.yview)
        self.info.configure(yscrollcommand=bar.set)
        self.info.grid(row=0, column=0, sticky="nsew")
        bar.grid(row=0, column=1, sticky="ns", padx=(2, 12))
        self.breakdown = ChartView(b, height=230, width=int(240 * self.s))
        self.breakdown.grid(row=0, column=2, sticky="nsew")
        t = self.info
        bullet = int(t.tk.call("font", "measure", T.font(t, 9), "•  "))  # (in the font the text is in)
        t.tag_configure("title", font=T.font(t, 13, "bold"))
        t.tag_configure("year", font=T.font(t, 13))
        t.tag_configure("meta")
        t.tag_configure("big", font=T.font(t, 10, "bold"))
        t.tag_configure("muted")
        t.tag_configure("soft")
        t.tag_configure("h", font=T.font(t, 9, "bold"), spacing1=8, spacing3=2)
        t.tag_configure("summary", spacing1=6, spacing3=2)
        t.tag_configure("bullet", lmargin1=2, lmargin2=2 + bullet, tabs=(2 + bullet,))
        t.tag_configure("link")
        t.tag_configure("small", font=T.font(t, 8))
        _colour_details(t)
        # One set of bindings serves every link: they look up which link the pointer is on. (Binding each link's
        # own tag would leave its Tcl commands behind when the details change - a few more for every film shown.)
        t.tag_bind("link", "<Enter>", self._link_hover)
        t.tag_bind("link", "<Motion>", self._link_hover)
        t.tag_bind("link", "<Leave>", self._link_left)
        t.tag_bind("link", "<Button-1>", self._link_clicked)
        for verdict in CREDITS_VERDICT:
            tag = f"verdict{verdict.replace(' ', '')}"
            t.tag_configure(tag, font=T.font(t, 9, "bold"))
            T.tint_tag(t, tag, foreground=lambda v=verdict: CREDITS_VERDICT[v][1])
        self._clear_details()
        return card

    # Your taste ---------------------------------------------------------------------------------------------
    def _build_taste(self, parent):
        page = ttk.Frame(parent, style="Page.TFrame", padding=(0, GAP, 0, 0))
        page.columnconfigure(0, weight=3, uniform="taste")
        page.columnconfigure(1, weight=2, uniform="taste")
        page.rowconfigure(2, weight=1)
        bar = ttk.Frame(page, style="Page.TFrame")
        bar.grid(row=0, column=0, columnspan=2, sticky="ew")
        bar.columnconfigure(len(KINDS) + 1, weight=1)
        ttk.Label(bar, text="Show", style="Page.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.kind_var = tk.StringVar(value="genre")
        self.kind_buttons = {}
        for n, (key, plural, _singular) in enumerate(KINDS):
            rb = ttk.Radiobutton(bar, text=plural, value=key, variable=self.kind_var, style="Kind.Toolbutton",
                                 command=self._kind_changed)
            rb.grid(row=0, column=n + 1, sticky="w", padx=(0, 2))
            self.kind_buttons[key] = rb
        least = ttk.Frame(bar, style="Page.TFrame")
        least.grid(row=0, column=len(KINDS) + 2, sticky="e", padx=(12, 0))
        ttk.Label(least, text="At least", style="Page.TLabel").grid(row=0, column=0, padx=(0, 4))
        self.min_films_var = tk.StringVar(value=str(TASTE_KINDS["genre"]))
        self.min_films_spin = ttk.Spinbox(least, from_=1, to=100, increment=1, width=4,
                                          textvariable=self.min_films_var, command=self._taste_changed)
        self.min_films_spin.grid(row=0, column=1)
        self.min_films_spin.bind("<Return>", lambda e: self._taste_changed(now=True), add="+")
        ttk.Label(least, text="films you rated", style="Page.TLabel").grid(row=0, column=2, padx=(4, 0))
        self.tilt_help = _autowrap(ttk.Label(page, text=TILT_HELP, style="PageHint.TLabel", justify="left",
                                             wraplength=700))
        self.tilt_help.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 8))
        chart_card = Card(page, padding=(12, 10))
        chart_card.grid(row=2, column=0, sticky="nsew", padx=(0, GAP))
        chart_card.body.columnconfigure(0, weight=1)
        chart_card.body.rowconfigure(0, weight=1)
        self.taste_view = ChartView(chart_card.body, height=380)
        self.taste_view.grid(row=0, column=0, sticky="nsew")
        self.taste_table_card = table_card = Card(page, title="All of them", padding=(12, 10))
        table_card.grid(row=2, column=1, sticky="nsew")
        tb = table_card.body
        tb.columnconfigure(0, weight=1)
        tb.rowconfigure(0, weight=1)
        self.taste_columns = [("label", "Genre", 150, "w"), ("tilt", "Tilt", 56, "e"), ("films", "Films", 50, "e"),
                              ("your_average", "Your avg", 66, "e")]
        self.taste_table = Table(tb, self.taste_columns, height=12, on_open=self._taste_row_opened)
        self.taste_table.grid(row=0, column=0, sticky="nsew")
        self.taste_foot = _autowrap(ttk.Label(tb, text="", style="CardSmall.TLabel", justify="left",
                                              wraplength=300))
        self.taste_foot.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        return page

    # Your critics -----------------------------------------------------------------------------------------------
    def _build_critics(self, parent):
        """A scrolling page: the headline, the list of critics beside every critic's dot, one critic's record with
        you, and the test of them as a guide."""
        page = ttk.Frame(parent, style="Page.TFrame", padding=(0, GAP, 0, 0))
        page.columnconfigure(0, weight=1)
        page.rowconfigure(0, weight=1)
        # what shows instead of the page while the critics are compared, or when they can't be
        self.critics_msg = ChartView(page, height=320, background=T.PAGE)
        self.critics_msg.grid(row=0, column=0, sticky="nsew")
        scroll = self.critics_scroll = ScrollFrame(page, background=T.PAGE, style="Page.TFrame")
        scroll.grid(row=0, column=0, sticky="nsew")
        scroll.canvas.bind("<Configure>", lambda e: self._reflow_critics(e.width), add="+")
        inner = scroll.inner
        inner.columnconfigure(0, weight=1)
        # 1) the headline, on the page plane
        head = self.critics_head = ttk.Frame(inner, style="Page.TFrame")
        head.grid(row=0, column=0, sticky="ew", pady=(0, GAP))
        head.columnconfigure(0, weight=1)
        self.critics_title = ttk.Label(head, text="", style="CriticsHead.TLabel")
        self.critics_title.grid(row=0, column=0, sticky="w")
        self.critics_text = _autowrap(ttk.Label(head, text="", style="PageHint.TLabel", justify="left",
                                                wraplength=700), pad=8)
        self.critics_text.grid(row=1, column=0, sticky="ew", pady=(2, 0))
        self.critics_picks_link = LinkLabel(head, "See your critics' picks", lambda: self.navigate(sort="critics"),
                                            style="PageLink.TLabel")
        self.critics_picks_link.grid(row=2, column=0, sticky="w", pady=(4, 0))
        # 2) the list of critics, and every critic against the range luck gives
        row = self.critics_row = ttk.Frame(inner, style="Page.TFrame")
        row.grid(row=2, column=0, sticky="ew", pady=(0, GAP))
        self.critics_list_card = card = Card(row, padding=(12, 10))
        b = card.body
        b.columnconfigure(0, weight=1)
        b.rowconfigure(1, weight=1)
        bar = ttk.Frame(b, style="CardInner.TFrame")
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        bar.columnconfigure(3, weight=1)
        self.critic_list_var = tk.StringVar(value="closest")
        self.critic_list_buttons = {}
        for n, (value, text) in enumerate((("closest", "Closest to you"), ("furthest", "Least in step"),
                                           ("all", "Everyone"))):
            rb = ttk.Radiobutton(bar, text=text, value=value, variable=self.critic_list_var,
                                 style="Kind.Toolbutton", command=self._critic_list_changed)
            rb.grid(row=0, column=n, sticky="w", padx=(0, 2))
            self.critic_list_buttons[value] = rb
        # 'Find' and its box: beside the buttons where there's room, else on a line of their own under them
        self.critic_finder = finder = ttk.Frame(bar, style="CardInner.TFrame")
        ttk.Label(finder, text="Find", style="CardField.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 4))
        self.critic_find = SearchBox(finder, self._suggest_critics, on_pick=self._critic_typed, width=16)
        self.critic_find.grid(row=0, column=1, sticky="w")
        finder.grid(row=0, column=4, sticky="e", padx=(8, 0))
        bar.bind("<Configure>", lambda e: self._fit_critic_bar(e.width), add="+")
        self.critic_table = Table(b, CRITIC_COLUMNS, height=10, on_select=self._critic_row_selected,
                                  on_open=self._critic_row_selected)
        self.critic_table.grid(row=1, column=0, sticky="nsew")
        tree = self.critic_table.tree
        for key in ("shared", "agreement", "match"):
            tree.column(key, stretch=False)
        tree.column("name", minwidth=int(110 * self.s))
        tree.bind("<Configure>", self._fit_critic_columns, add="+")
        self.critic_list_hint = _autowrap(ttk.Label(b, text="", style="CardSmall.TLabel", justify="left",
                                                    wraplength=400))
        self.critic_list_hint.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        self.funnel_card = Card(row, padding=(12, 10))
        self.funnel_card.body.columnconfigure(0, weight=1)
        self.funnel_card.body.rowconfigure(0, weight=1)
        self.funnel = ChartView(self.funnel_card.body, height=FUNNEL_HEIGHT)
        self.funnel.grid(row=0, column=0, sticky="nsew")
        self._critics_cols = 0
        # 3) one critic's record with you: who they are and how they've gone with you (beside the strip of your
        # ratings of the films you share), then where you agreed and didn't, and their picks - side by side
        self.critic_card = card = Card(inner, padding=(12, 10))
        card.grid(row=3, column=0, sticky="ew", pady=(0, GAP))
        b = card.body
        b.columnconfigure(0, weight=1)

        def text(parent):
            t = tk.Text(parent, wrap="word", borderwidth=0, highlightthickness=0, font=T.font(parent, 9), padx=2,
                        pady=0, cursor="arrow", height=6, width=30, spacing3=1, takefocus=0)
            _style_details(t)
            # As tall as what it holds (the page scrolls, not the text): the wheel over it scrolls the page
            bind_wheel(t, self._critic_wheel, add=False)
            t.bind("<Configure>", lambda e, t=t: self._fit_text(t), add="+")
            return t

        self.critic_info = text(b)
        self.critic_info.grid(row=0, column=0, sticky="new", padx=(0, 12))
        self.critic_strip = ChartView(b, height=STRIP_HEIGHT, width=int(STRIP_WIDTH * self.s))
        self.critic_strip.grid(row=0, column=1, sticky="ne")
        lists = self.critic_lists = ttk.Frame(b, style="CardInner.TFrame")
        lists.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        self.critic_films_text = text(lists)
        self.critic_picks_text = text(lists)
        self._critic_texts = [self.critic_info, self.critic_films_text, self.critic_picks_text]
        self._critic_linkers = [TextLinks(t) for t in self._critic_texts]
        self._critic_list_cols = 0
        b.bind("<Configure>", lambda e: self._reflow_critic_lists(e.width), add="+")
        self._reflow_critic_lists(0)
        # 4) how good a guide they are
        self.critics_eval_card = card = Card(inner, title="How good a guide are they?", padding=(14, 12))
        card.grid(row=4, column=0, sticky="ew", pady=(0, GAP))
        b = card.body
        b.columnconfigure(0, weight=1)
        self.critics_eval_help = _autowrap(ttk.Label(b, text=CRITICS_EVAL_HELP, style="CardHint.TLabel",
                                                     justify="left", wraplength=700))
        self.critics_eval_help.grid(row=0, column=0, sticky="ew")
        bar = ttk.Frame(b, style="CardInner.TFrame")
        bar.grid(row=1, column=0, sticky="ew", pady=(10, 10))
        bar.columnconfigure(2, weight=1)
        self.critics_eval_btn = ttk.Button(bar, text="Test it on my ratings", style="Accent.TButton",
                                           command=self._critics_evaluate)
        self.critics_eval_btn.grid(row=0, column=0, sticky="w")
        self.critics_eval_progress = ttk.Progressbar(bar, mode="indeterminate", length=int(140 * self.s))
        self.critics_eval_progress.grid(row=0, column=1, sticky="w", padx=(12, 0))
        self.critics_eval_progress.grid_remove()
        self.critics_eval_status = ttk.Label(bar, text="", style="CardHint.TLabel")
        self.critics_eval_status.grid(row=0, column=2, sticky="w", padx=(12, 0))
        self.critics_eval_view = ChartView(b, height=CRITICS_EVAL_HEIGHT)
        self.critics_eval_view.grid(row=2, column=0, sticky="ew")
        self.critics_verdict_title = ttk.Label(b, text="", style="CardTitle.TLabel")
        self.critics_verdict_title.grid(row=3, column=0, sticky="w", pady=(10, 2))
        self.critics_verdict = _autowrap(ttk.Label(b, text="", style="Card.TLabel", justify="left", wraplength=700))
        self.critics_verdict.grid(row=4, column=0, sticky="ew")
        self.critics_method = _autowrap(ttk.Label(b, text="", style="CardSmall.TLabel", justify="left",
                                                  wraplength=700))
        self.critics_method.grid(row=5, column=0, sticky="ew", pady=(8, 0))
        self._reflow_critics(0)
        self._clear_critics()
        return page

    def _reflow_critics(self, width: int | None = None):
        """The list and the chart side by side when there's room, one above the other when there isn't."""
        if not width:
            width = self.critics_scroll.canvas.winfo_width()
        if width < 50 and self._critics_cols:          # not laid out yet: keep what we have
            return
        cols = 2 if width / self.s >= TWO_CARDS_FROM else 1
        if cols == self._critics_cols:
            return
        self._critics_cols = cols
        row = self.critics_row
        if cols == 2:
            row.columnconfigure(0, weight=2, uniform="critics")
            row.columnconfigure(1, weight=3, uniform="critics")
            self.critics_list_card.grid(row=0, column=0, sticky="nsew", padx=(0, GAP // 2))
            self.funnel_card.grid(row=0, column=1, sticky="nsew", padx=(GAP // 2, 0))
        else:
            row.columnconfigure(0, weight=1, uniform="")
            row.columnconfigure(1, weight=0, uniform="")
            self.critics_list_card.grid(row=0, column=0, sticky="nsew", padx=0, pady=(0, GAP))
            self.funnel_card.grid(row=1, column=0, sticky="nsew", padx=0)

    def _fit_critic_bar(self, width: int) -> bool:
        """The critic finder beside the three lists' buttons when they all fit the list's width (a wider font or
        a larger text size may not), else under them - never cut off. -> whether it's under them."""
        finder = self.critic_finder
        now = int(str(finder.grid_info().get("row", 0) or 0)) > 0
        if width <= 1:
            return now
        need = sum(b.winfo_reqwidth() + 2 for b in self.critic_list_buttons.values()) + \
            finder.winfo_reqwidth() + int(8 * self.s)
        below = need > width
        if below != now:
            if below:
                finder.grid(row=1, column=0, columnspan=5, sticky="w", padx=0, pady=(6, 0))
            else:
                finder.grid(row=0, column=4, columnspan=1, sticky="e", padx=(8, 0), pady=0)
        return below

    def _reflow_critic_lists(self, width: int):
        """One critic's two lists (where you agreed and didn't; their picks) side by side when there's room."""
        if width < 50 and self._critic_list_cols:
            return
        cols = 2 if width / self.s >= TWO_LISTS_FROM else 1
        if cols == self._critic_list_cols:
            return
        self._critic_list_cols = cols
        lists = self.critic_lists
        films, picks = self.critic_films_text, self.critic_picks_text
        if cols == 2:
            lists.columnconfigure(0, weight=1, uniform="lists")
            lists.columnconfigure(1, weight=1, uniform="lists")
            films.grid(row=0, column=0, sticky="new", padx=(0, 16))
            picks.grid(row=0, column=1, sticky="new")
        else:
            lists.columnconfigure(0, weight=1, uniform="")
            lists.columnconfigure(1, weight=0, uniform="")
            films.grid(row=0, column=0, sticky="new", padx=0)
            picks.grid(row=1, column=0, sticky="new", pady=(4, 0))

    def _fit_critic_columns(self, event=None):
        """A narrow list of critics leaves out Publication (the details say it)."""
        width = event.width if event is not None else self.critic_table.tree.winfo_width()
        if width < 40:
            return
        keys = [c[0] for c in CRITIC_COLUMNS]
        if width < NARROW_CRITICS * self.s:
            keys.remove("publication")
        tree = self.critic_table.tree
        if tuple(tree.tk.splitlist(tree.cget("displaycolumns"))) != tuple(keys):
            tree.configure(displaycolumns=keys)

    # How accurate is this? ------------------------------------------------------------------------------------
    def _build_accuracy(self, parent):
        page = ttk.Frame(parent, style="Page.TFrame", padding=(0, GAP, 0, 0))
        page.columnconfigure(0, weight=1)
        page.rowconfigure(0, weight=1)
        card = Card(page, title="How accurate is this?", padding=(14, 12))
        card.grid(row=0, column=0, sticky="nsew")
        b = card.body
        b.columnconfigure(0, weight=1)
        b.rowconfigure(6, weight=1)                 # spare room goes below the verdict, not into the chart
        self.acc_help = _autowrap(ttk.Label(b, text=ACCURACY_HELP, style="CardHint.TLabel", justify="left",
                                            wraplength=700))
        self.acc_help.grid(row=0, column=0, sticky="ew")
        row = ttk.Frame(b, style="CardInner.TFrame")
        row.grid(row=1, column=0, sticky="ew", pady=(10, 10))
        row.columnconfigure(2, weight=1)
        self.eval_btn = ttk.Button(row, text="Test it on my ratings", style="Accent.TButton", command=self._evaluate)
        self.eval_btn.grid(row=0, column=0, sticky="w")
        self.eval_progress = ttk.Progressbar(row, mode="indeterminate", length=int(140 * self.s))
        self.eval_progress.grid(row=0, column=1, sticky="w", padx=(12, 0))
        self.eval_progress.grid_remove()
        self.eval_status = ttk.Label(row, text="", style="CardHint.TLabel")
        self.eval_status.grid(row=0, column=2, sticky="w", padx=(12, 0))
        self.acc_view = ChartView(b, height=ACCURACY_HEIGHT)
        self.acc_view.grid(row=2, column=0, sticky="ew")
        self.verdict_title = ttk.Label(b, text="", style="CardTitle.TLabel")
        self.verdict_title.grid(row=3, column=0, sticky="w", pady=(10, 2))
        self.verdict = _autowrap(ttk.Label(b, text="", style="Card.TLabel", justify="left", wraplength=700))
        self.verdict.grid(row=4, column=0, sticky="ew")
        self.method = _autowrap(ttk.Label(b, text="", style="CardSmall.TLabel", justify="left", wraplength=700))
        self.method.grid(row=5, column=0, sticky="ew", pady=(8, 0))
        return page

    # -- called by the main window -----------------------------------------------------------------------------
    def catalog_changed(self, catalog, state: str):
        super().catalog_changed(catalog, state)
        for control in list(_LEAVE_OUT_CONTROLS):  # Settings' 'Never suggest films from': this collection's
            if control.tab.app is self.app:        # libraries and genres
                control.refresh()
        for key in self._tokens:                  # anything still running is for the old collection
            self._tokens[key] += 1
        self._cancel_timers()
        self._set_busy("search", False)
        self._set_busy("taste", False)
        self._set_busy("eval", False)
        self._set_busy("critics_eval", False)
        self._model_ready = False
        self._fitting, self._waiting = None, set()
        self.answer = self.request = self.selected = None
        self.taste_answer = self.eval_answer = None
        self._clear_critics()                     # (a critic asked for before the collection was ready stays asked)
        self._show_state()
        if self.state_message() is not None:
            return
        started = time.perf_counter()
        self._fill_choices()
        self._clear_results()
        self._clear_taste()
        self._clear_accuracy()
        self._need = {"search": True, "taste": True, "critics": True}
        self.timings["catalog_changed"] = time.perf_counter() - started
        if self._visible():
            self._refresh()

    def shown(self):
        self._refresh()

    def keep(self) -> dict:
        """(Laid out again for a new text size:) the filters, the view on show and the critic picked."""
        try:
            filters = {key: value for key, value in self.filters().items() if key in DEFAULTS}
        except (tk.TclError, AttributeError):
            filters = None
        return {"filters": filters, "extra": self.extra, "person": self._person, "like": self._like,
                "view": self._view(), "critic": self._critic_id}

    def restore(self, kept: dict):
        self.extra, self._person, self._like = kept.get("extra"), kept.get("person"), kept.get("like")
        if kept.get("filters"):
            self._set_filters(dict(DEFAULTS, **kept["filters"]))
        if kept.get("critic"):
            self._pending_critic = kept["critic"]             # (shown when Your critics has its list again)
        page = {"taste": self.taste_page, "critics": self.critics_page, "acc": self.acc_page}.get(kept.get("view"))
        if page is not None:
            self.views.select(page)

    def preference_changed(self, key: str, value):
        """'Never suggest films from' or 'Also count as seen' changed (Settings): the recommendations search again -
        now if they're on show, else when they're next looked at. The critic on show in Your critics is asked
        again too (what they liked that you haven't seen)."""
        if key not in (LEAVE_OUT, SEEN_BY) or self.state_message() is not None:
            return
        self._need["search"] = True
        if self._visible() and self._view() == "rec":
            self._search()
        if key == SEEN_BY and self._critic_id and self._critics_state not in (None, "loading"):
            self._select_critic(self._critic_id)

    def navigate(self, **kwargs):
        """like='Title (Year)' (film_key=... says exactly which film), person=name (person_id=... says exactly
        who: two people can share a name), genre=, decade=, library=, include_watched= - and country= or
        collection= (from the taste chart): show Recommendations with just those filters, and search.
        One person's or collection's films come without the two-per-director limit. sort= picks the order
        ('predicted', 'personal' or 'critics': your critics' picks).

        critic=<critic id> instead: that critic in Your critics - where you agreed, where you didn't, and what they
        liked that you haven't seen (a name works too)."""
        if kwargs.get("critic") not in (None, ""):
            self._navigate_critic(str(kwargs["critic"]))
            return
        values = dict(DEFAULTS)
        if str(kwargs.get("sort") or "") in {v for v, _t, _h in SORT_CHOICES}:
            values["sort"] = str(kwargs["sort"])
        self._person = self._like = None
        cat = self.catalog
        like = kwargs.get("like") or kwargs.get("title")
        film = cat.films.get(str(kwargs["film_key"])) if kwargs.get("film_key") and cat is not None else None
        if film is not None:
            like = like or film.label
            self._like = (film.key, str(like))
        if like:
            values["like"] = str(like)
        person = kwargs.get("person") or kwargs.get("name")
        p = cat.people.get(str(kwargs["person_id"])) if kwargs.get("person_id") and cat is not None else None
        if p is not None:
            person = namesake_labels(cat)["label"].get(p.id, p.name)     # labelled if the name is shared
            self._person = (p.id, person)
        if person:
            values["person"] = str(person)
            values["variety"] = 0
        if kwargs.get("genre"):
            values["genre"] = self._matching(self.boxes["genre"], str(kwargs["genre"]))
        decade = parse_decade(kwargs.get("decade"))
        if decade is not None:
            values["decade"] = f"{decade}s"
        if kwargs.get("library"):
            values["library"] = self._matching(self.boxes["library"], str(kwargs["library"]))
        if kwargs.get("include_watched"):
            values["include_watched"] = True
        self.extra = None
        if kwargs.get("country"):
            country = str(kwargs["country"])
            self.extra = ("countries", country, f"From {display_label('country', country)}")
        elif kwargs.get("collection"):
            self.extra = ("collections", str(kwargs["collection"]), f"In your '{kwargs['collection']}' collection")
            values["variety"] = 0
        self._set_filters(values)
        try:
            self.views.select(self.rec_page)
        except tk.TclError:
            pass
        self._search()

    # -- state ---------------------------------------------------------------------------------------------------
    def _visible(self) -> bool:
        current = getattr(self.app, "current_tab", None)
        try:
            if callable(current):
                return current() is self
            return self.notebook.select() == str(self.frame)
        except tk.TclError:
            return False

    def _view(self) -> str:
        try:
            current = self.views.select()
        except tk.TclError:
            return ""
        return {str(self.rec_page): "rec", str(self.taste_page): "taste", str(self.critics_page): "critics",
                str(self.acc_page): "acc"}.get(current, "")

    def _show_state(self):
        msg = self.state_message()
        if msg is not None:
            self.views.grid_remove()
            self.placeholder.grid()
            self.show_message(self.placeholder, *msg)
            self.model_label.configure(text="")
        else:
            self.placeholder.grid_remove()
            self.views.grid()

    def _refresh(self):
        """Fill whichever view is showing, if it's out of date (the others wait until they're looked at)."""
        if self.state_message() is not None:
            return
        view = self._view()
        if view == "rec" and self._need["search"]:
            self._search()
        elif view == "taste" and self._need["taste"]:
            self._load_taste()
        elif view == "critics" and self._need["critics"]:
            self._load_critics()

    def _set_busy(self, what: str, busy: bool):
        self._busy[what] = busy
        if what == "search":
            self.find_btn.configure(state="disabled" if busy else "normal")
            if busy:
                self.results_count.configure(text="Finding films...")
        elif what in ("eval", "critics_eval"):
            button, progress = ((self.eval_btn, self.eval_progress) if what == "eval" else
                                (self.critics_eval_btn, self.critics_eval_progress))
            button.configure(state="disabled" if busy else "normal")
            if busy:
                progress.grid()
                progress.start(12)
            else:
                progress.stop()
                progress.grid_remove()

    def _after(self, name: str, ms: int, fn):
        self._cancel_timer(name)
        self._timers[name] = self.frame.after(ms, lambda: (self._timers.pop(name, None), fn()))

    def _cancel_timer(self, name: str):
        timer = self._timers.pop(name, None)
        if timer is not None:
            try:
                self.frame.after_cancel(timer)
            except tk.TclError:
                pass

    def _cancel_timers(self):
        for name in list(self._timers):
            self._cancel_timer(name)

    def _fill_choices(self):
        """The filter lists for this collection, and the look-ups the details need."""
        cat = self.catalog
        genres = sorted({g for f in cat.films.values() for g in f.genres if g}, key=str.casefold)
        decades = sorted({f.year // 10 * 10 for f in cat.films.values() if f.year}, reverse=True)
        self.boxes["library"].configure(values=[ANY] + list(cat.libraries))
        self.boxes["genre"].configure(values=[ANY] + genres)
        self.boxes["decade"].configure(values=[ANY] + [f"{d}s" for d in decades])
        self._films_by_id = {pid: f for f in cat.films.values() for pid in f.plex_ids}
        rated = sum(1 for f in cat.films.values() if f.owner_rating is not None)
        self.rated_count = rated
        self.model_label.configure(text=f"Learns from the {rated:,} films you've rated in Plex" if rated else
                                   "Rate films in Plex (the stars) and this tab learns your taste")

    @staticmethod
    def _matching(box, value: str) -> str:
        """The list's own spelling of a value ('drama' -> 'Drama'), or the value as given."""
        for option in box.tk.splitlist(box.cget("values")):
            if str(option).casefold() == value.casefold():
                return str(option)
        return value

    def _suggest_people(self, text: str):
        return _people_suggester(self.catalog)(text) if self.catalog is not None else []

    def _suggest_films(self, text: str):
        return _film_suggester(self.catalog)(text) if self.catalog is not None else []

    # -- the filters ----------------------------------------------------------------------------------------------
    def _int(self, key: str, lo: int, hi: int) -> int:
        try:
            value = int(float(self.vars[key].get()))
        except (TypeError, ValueError):
            value = DEFAULTS[key]
        value = min(max(value, lo), hi)
        if self.vars[key].get() != str(value):
            self.vars[key].set(str(value))
        return value

    def filters(self) -> dict:
        v = self.vars
        person, like = self.with_box.get(), self.like_box.get()
        return {"sort": v["sort"].get(), "library": v["library"].get() or ANY, "genre": v["genre"].get() or ANY,
                "decade": v["decade"].get() or ANY, "runtime": v["runtime"].get() or ANY,
                "person": person, "person_id": self._person_id(person), "like": like, "like_key": self._like_key(like),
                "words": v["words"].get().strip(), "include_watched": bool(v["include_watched"].get()),
                "variety": self._int("variety", *VARIETY), "count": self._int("count", *COUNT)}

    def _person_id(self, text: str) -> str | None:
        """Who exactly the With box means: the person a link or a taste bar picked, while the box still shows
        them, or a namesake picked from the suggestions ('John Smith (1 film: Harbour Lights)')."""
        if not text or self.catalog is None:
            return None
        if self._person and self._person[1] == text:
            pid = self._person[0]
        else:
            pid = namesake_labels(self.catalog)["id"].get(text)
        return pid if pid in self.catalog.people else None

    def _like_key(self, text: str) -> str | None:
        """Which film exactly the Like box means: the one a link picked, while the box still shows it, or the one
        film whose 'Title (Year)' it says (a suggestion picked, say) - so 'Drácula (1931)' can't be taken for
        'Dracula (1931)', as the title search would. Anything else goes by title."""
        if not text or self.catalog is None:
            return None
        if self._like and self._like[1] == text:
            key = self._like[0]
        else:
            key = film_keys(self.catalog).get(text)
        return key if key in self.catalog.films else None

    def _set_filters(self, values: dict):
        for key in ("sort", "library", "genre", "decade", "runtime", "words"):
            self.vars[key].set(values[key])
        self.vars["include_watched"].set(bool(values["include_watched"]))
        self.vars["variety"].set(str(values["variety"]))
        self.vars["count"].set(str(values["count"]))
        self.with_box.set(values["person"])
        self.like_box.set(values["like"])
        self._show_extra()

    def _show_extra(self):
        if self.extra:
            self.extra_label.configure(text=f"Also: {self.extra[2]}")
            self.extra_row.grid()
        else:
            self.extra_row.grid_remove()
        self._after("fit", 0, self._fit_filters)         # (a row more or less: room for the small print?)

    def _clear_extra(self):
        self.extra = None
        self._show_extra()
        self._changed(now=True)

    def _reset(self):
        self.extra = None
        self._person = self._like = None
        self._set_filters(DEFAULTS)
        self._changed(now=True)

    def _changed(self, now: bool = False):
        """A filter changed: search (after a moment, so a burst of changes makes one search)."""
        if now:
            self._cancel_timer("search")
            self._search()
        else:
            self._after("search", DEBOUNCE_MS, self._search)

    # -- searching ------------------------------------------------------------------------------------------------
    def _request(self) -> dict:
        """The request for the panel as it is, leaving out what Settings says (read now: it can change)."""
        return build_request(self.filters(), self.extra, prefs.get(self.app, LEAVE_OUT), self._seen_by())

    def _seen_by(self) -> list[int]:
        """The other accounts whose plays count as seen (Settings > Your collection)."""
        return prefs.get(self.app, SEEN_BY)

    def _critic_request(self, cid) -> dict:
        request = {"action": "critics", "critic": cid, "count": CRITICS_SHOWN}
        seen_by = self._seen_by()
        if seen_by:
            request["also_seen_by"] = seen_by
        return request

    def _search(self):
        if self.state_message() is not None:
            self._need["search"] = True
            return
        self._cancel_timer("search")
        self._need["search"] = False
        if not self._model_ready and self._fitting is not None:
            self._waiting.add("search")         # searched as soon as the model is fitted, with the filters then
            self._set_busy("search", True)
            return
        request = self._request()
        self._tokens["search"] += 1
        token, catalog = self._tokens["search"], self.catalog
        first = not self._model_ready
        if first:
            self._fitting = "search"
        started = time.perf_counter()
        self._set_busy("search", True)

        def work():
            answer = handle(request, catalog=catalog)
            # while we're at it: the name look-ups for the search boxes (once per collection)
            for key, build in (("ui.watchnext.people", _people_suggester),
                               ("ui.watchnext.film_suggester", _film_suggester)):
                if key not in catalog.cache:
                    check()                     # (unless a newer search has called this one off)
                    build(catalog)
            return with_closest(answer, request, catalog)

        def done(answer):
            if token != self._tokens["search"] or catalog is not self.catalog:
                return                          # a newer search, or another collection, took over
            self._set_busy("search", False)
            self._model_ready = True
            if self._fitting == "search":
                self._fitting = None
            if "search" in self._waiting and self._request() != request:
                self._resume()                  # the filters changed while it learned: search again (quick now)
                return
            self._waiting.discard("search")
            self.timings["search"] = time.perf_counter() - started
            shown = time.perf_counter()
            self._show_answer(answer, request)
            self.timings["show"] = time.perf_counter() - shown
            self.app.set_status(search_status(answer, request, self.timings["search"]))    # (replaces 'Finding...')
            self._resume()                      # the taste chart, if it waited for the model

        def failed(message):
            if token != self._tokens["search"] or catalog is not self.catalog:
                return
            self._set_busy("search", False)
            if self._fitting == "search":
                self._fitting = None
            self._waiting.discard("search")
            self._show_results_message("Couldn't find films", message)
            self.app.set_status(f"Watch Next couldn't search: {message}")
            self._resume()

        # key: a newer search calls this one off (it stops, and never answers) - once the model is fitted; the
        # search that fits it is waited for instead (above). The token is a second guard.
        self.app.run(work, done, failed,
                     status="Learning your taste from your ratings..." if first else "Finding films...",
                     key="watchnext.search")

    def _resume(self):
        """The job fitting the model has finished: do what waited for it (quick now the model is fitted)."""
        waiting, self._waiting = self._waiting, set()
        if "search" in waiting:
            self._search()
        if "taste" in waiting:
            self._load_taste()

    def _show_answer(self, answer: dict, request: dict):
        self.request = request
        if not answer.get("ok"):
            self.answer = None
            self._show_results_message(*problem_message(answer))
            self.results_count.configure(text="")
            self.results_hint.configure(text="")
            self._show_left_out("")
            self.notes.configure(text="")
            return
        self._show_left_out(left_out_line(request, answer))
        self.answer = answer
        results = answer.get("results", [])
        model = answer.get("model", {})
        sort = answer.get("sort")
        if sort == "personal":
            heading = "Personal picks"
        elif sort == "critics":
            heading = "Your critics' picks"
        else:
            heading = "Films you haven't rated" if request.get("include_watched") else "Films you haven't seen"
        self.results_title.configure(text=heading)
        self.results_count.configure(text=f"{len(results)} of {answer.get('matching_films', 0):,} matching films")
        if sort == "critics":
            hint = CRITICS_RESULTS_HINT
        else:
            hint = (f"Predicted: the score out of 10 it expects you'd give, learned from your "
                    f"{model.get('trained_on', 0):,} ratings (your average: {model.get('your_average', 0):.1f}).  "
                    "Lift: how much its people, studios and collections raise (+) or lower (-) that - the details "
                    "below show the rest.")
        self.results_hint.configure(text=hint + "  Double-click a film for its page.")
        notes = [n for n in answer.get("notes", []) if n != CR.NOT_IN_WATCH_NEXT]     # (the hint says it)
        self.notes.configure(text="  ".join(plain_note(n) for n in notes))
        self.table.set_heading("lift", "Critics" if sort == "critics" else "Lift")
        if not results:
            self.results_count.configure(text="")        # (not '0 of 0 matching films': the message says why)
            self._show_results_message(*empty_message(request, answer))
            return
        self.results_msg.grid_remove()
        self.table.grid()
        self.table.set_rows([result_row(n, r, sort) for n, r in enumerate(results, 1)], keep_sort=False)
        self.table.sorted_by = None
        for key, heading, _w, _a in self.table.columns:
            self.table.tree.heading(key, text=heading)
        self.table.select_first()
        self.show_film(results[0])

    def _show_results_message(self, text: str, sub: str):
        self.table.set_rows([], keep_sort=False)
        self.table.grid_remove()
        self.results_msg.grid()
        self.show_message(self.results_msg, text, sub)
        self._clear_details()

    def _clear_results(self):
        self.table.set_rows([], keep_sort=False)
        self.results_msg.grid_remove()
        self.table.grid()
        self.results_count.configure(text="")
        self.results_hint.configure(text="")
        self._show_left_out("")
        self.notes.configure(text="")
        self._clear_details()

    def _show_left_out(self, line: str):
        """The line saying what Settings left out of the search ('' hides it)."""
        self.left_out_label.configure(text=line)
        if line:
            self.left_out_row.grid()
        else:
            self.left_out_row.grid_remove()

    def _change_left_out(self):
        """'Change': Settings, at Watch Next's card."""
        self._goto("Settings", section="Watch Next")

    # -- the selected film ----------------------------------------------------------------------------------------
    def _film_selected(self, row: dict):
        result = row.get("result")
        if result is not None and result is not self.selected:
            self.show_film(result)

    def _clear_details(self, text: str = "Pick a film in the list to see why it's suggested."):
        self.selected = None
        self._empty_details()
        t = self.info
        t.insert("end", text, "muted")
        t.configure(state="disabled")
        self.breakdown.clear()

    def _empty_details(self):
        """Empty the details' text (left editable, for filling), with its links and buttons. The buttons are
        destroyed here: deleting the text around one takes it off the screen, but leaves tkinter holding on to
        it and its command - two more for every film shown."""
        for button in self._buttons:
            try:
                button.destroy()
            except tk.TclError:
                pass
        self._buttons = []
        self._link_left()
        self._link_commands = {}
        self.links = []                         # (text, command) of each link and button in the details
        t = self.info
        t.configure(state="normal")
        t.delete("1.0", "end")
        for tag in t.tag_names():
            if re.fullmatch(r"link\d+", tag):
                t.tag_delete(tag)

    def _link(self, text: str, command, extra_tags=()):
        self._links += 1
        tag = f"link{self._links}"
        self.info.insert("end", text, ("link", tag) + tuple(extra_tags))
        self.links.append((text, command))
        self._link_commands[tag] = command

    def _link_at_pointer(self) -> str | None:
        try:
            tags = self.info.tag_names("current")
        except tk.TclError:
            return None
        return next((tag for tag in tags if tag in self._link_commands), None)

    def _link_hover(self, _event=None):
        """The link under the pointer is underlined, with the hand pointer."""
        tag = self._link_at_pointer()
        if tag != self._hover_link:
            self._link_left()
            if tag is not None:
                self._hover_link = tag
                self.info.tag_configure(tag, underline=True)
                self.info.configure(cursor="hand2")

    def _link_left(self, _event=None):
        if self._hover_link is not None:
            try:
                self.info.tag_configure(self._hover_link, underline=False)
            except tk.TclError:
                pass
            self._hover_link = None
        try:
            self.info.configure(cursor="arrow")
        except tk.TclError:
            pass

    def _link_clicked(self, _event=None):
        tag = self._link_at_pointer()
        if tag is not None:
            self._link_commands[tag]()

    def _button(self, text: str, command):
        """A real button in the details' text, for the film's main actions: Tab reaches it, Space presses it
        (the text itself takes no focus, so its links need the mouse). Clearing the details destroys it."""
        button = ttk.Button(self.info, text=text, style="Small.TButton", command=command, cursor="hand2")
        self.info.window_create("end", window=button, padx=1, pady=3)
        self._buttons.append(button)
        self.links.append((text, command))

    def _more_like(self, r: dict):
        self.navigate(like=film_label(r["title"], r.get("year")), film_key=r.get("key"))

    def _open_film(self, r: dict):
        """A film's page on the Film tab - by its key (another film can share its title and year)."""
        label = film_label(r["title"], r.get("year"))
        self._goto("Film", **({"film_key": r["key"]} if r.get("key") else {}), title=label)

    def show_film(self, r: dict):
        """The details for one result: what it is, why it's suggested, and how its prediction adds up."""
        self.selected = r
        self._empty_details()
        t = self.info
        label = film_label(r["title"], r.get("year"))
        t.insert("end", r["title"], "title")
        if r.get("year"):
            t.insert("end", f"  {r['year']}", "year")
        t.insert("end", "\n")
        scores = []
        if r.get("imdb_rating") is not None:
            scores.append(f"IMDb {r['imdb_rating']:g}")
        if r.get("rt_critic") is not None:
            scores.append(f"critics {r['rt_critic']}%")
        meta = [" / ".join(r.get("genres", [])[:4]), f"{r['runtime_min']} min" if r.get("runtime_min") else "",
                ", ".join(scores), ", ".join(r.get("libraries") or []), r.get("resolution", "")]
        t.insert("end", "   ·   ".join(m for m in meta if m) + "\n", "meta")
        t.insert("end", f"Predicted {r['predicted_rating']:.1f}", "big")
        split = prediction_words(r)             # three parts of the one model: they add up to the prediction
        t.insert("end", " for you" + (f": {split}." if split else "") + "\n", "soft")
        t.insert("end", CONFIDENCE.get(r.get("confidence"), "") + "\n", "muted")
        if r.get("likeness") is not None:
            t.insert("end", f"Likeness to the film in 'Like': {r['likeness']:.2f} (1 = the same people, studio "
                            "and genres)\n", "muted")
        if r.get("matched_words"):
            t.insert("end", "Matches: " + ", ".join(r["matched_words"]) + "\n", "muted")
        credits = {"title": label}
        if r.get("key"):
            credits["film_key"] = r["key"]      # exactly this film (another can share its title and year)
        self._button("Open the film page", lambda: self._open_film(r))
        t.insert("end", " ")
        self._button("More like this", lambda: self._more_like(r))
        t.insert("end", " ")
        self._button("Scenes after the credits?", lambda: self._goto("Credits", **credits))
        verdict = self._credits_verdict(r)
        icon, _color, words = CREDITS_VERDICT.get(verdict, CREDITS_VERDICT[""])
        t.insert("end", "  ")
        t.insert("end", icon, f"verdict{verdict.replace(' ', '')}")
        t.insert("end", f" {words}\n", "soft")
        summary = " ".join(str(r.get("summary") or "").split())
        if summary:
            if len(summary) > 420:
                summary = summary[:420].rsplit(" ", 1)[0].rstrip(",;:") + "…"
            t.insert("end", summary + "\n", "summary")
        if r.get("critics"):
            self._critics_section(r["critics"])
        elif r.get("key") and CR.ready(self.catalog):
            # What critics said, in a line - only when the critics are already worked out (it's instant then:
            # showing a film never starts a job)
            said = CR.film_verdicts(self.catalog, r["key"])
            if said.get("ok") and said.get("reviews"):
                t.insert("end", "Critics: ", "muted")
                t.insert("end", said["summary"] + "\n", "soft")
        self._bullets("Why", r.get("reasons", []), "Nothing stands out - it's mostly its scores.")
        self._bullets("Against", r.get("against", []), "Nothing notable.")
        similar = r.get("similar_films_you_rated", [])
        t.insert("end", "Similar films you rated\n", "h")
        if similar:
            for s in similar:
                t.insert("end", "•\t", "bullet")
                if s.get("key"):                 # (its page on the Film tab)
                    self._link(film_label(s["title"], s.get("year")), lambda s=s: self._open_film(s), ("bullet",))
                else:
                    t.insert("end", film_label(s["title"], s.get("year")), "bullet")
                t.insert("end", f"   you rated it {s['your_rating']:g}\n", ("bullet", "muted"))
        else:
            t.insert("end", "None close enough to compare.\n", "muted")
        people = r.get("people", [])
        if people:
            t.insert("end", "People\n", "h")
            for n, p in enumerate(people):
                if n:
                    t.insert("end", "  ·  ", "muted")
                role = p.get("role") or ""
                # a director's profile counts the films they directed (some act in more films than they direct)
                self._link(p["name"], lambda p=p, d=role == "director": self._goto(
                    "Six Degrees", person_id=p["id"], name=p["name"], **({"directors": True} if d else {})))
                if role == "director":
                    t.insert("end", " (director)", "muted")
                elif role and role != "actor":
                    t.insert("end", f" as {role}", "muted")
            t.insert("end", "\n")
            t.insert("end", "Click a name to see their profile in Six Degrees.\n", "small")
        t.configure(state="disabled")
        t.yview_moveto(0)
        if r.get("breakdown"):
            self.breakdown.show(lambda p, r=r: draw_breakdown(p, r))
        else:
            self.breakdown.clear("No breakdown for this film")

    def _critics_section(self, c: dict):
        """Your critics' picks: who of your closest critics reviewed it, what they said - each name a link to that
        critic in Your critics."""
        t = self.info
        fresh, rotten = c.get("fresh", 0), c.get("rotten", 0)
        total = fresh + rotten
        t.insert("end", "Your critics\n", "h")
        if total == 1:
            said = "The one of your closest critics who reviewed it called it Fresh."
        elif fresh == total:
            said = (f"{'Both' if total == 2 else f'All {total}'} of your closest critics who reviewed it called it "
                    "Fresh.")
        else:
            said = f"{fresh} of the {total} of your closest critics who reviewed it called it Fresh."
        t.insert("end", said + "\n", "soft")
        for v in c.get("verdicts", []):
            t.insert("end", "•\t", "bullet")
            self._link(v["name"], lambda cid=v["id"]: self.navigate(critic=cid), ("bullet",))
            where = f" ({v['publication']})" if v.get("publication") else ""
            t.insert("end", f"{where} - {v['verdict']}", ("bullet", "soft"))
            if v.get("quote"):
                t.insert("end", f": “{v['quote']}”", ("bullet", "muted"))
            t.insert("end", "\n", "bullet")
        t.insert("end", "Click a critic to see where they've agreed with you.\n", "small")

    def _bullets(self, heading: str, lines, empty: str):
        t = self.info
        t.insert("end", heading + "\n", "h")
        if not lines:
            t.insert("end", empty + "\n", "muted")
            return
        for line in lines:
            head, sep, rest = str(line).partition(" - ")
            t.insert("end", "•\t" + head, "bullet")
            if sep:
                t.insert("end", " - " + rest, ("bullet", "soft"))
            t.insert("end", "\n", "bullet")

    def _goto(self, tab_title: str, **kwargs):
        """app.goto(tab_title, **kwargs). App.goto calls its first parameter 'title', so a title=... keyword
        clashes with it (a TypeError before anything happens) - then go there by hand."""
        try:
            found = self.app.goto(tab_title, **kwargs)
        except TypeError:
            found = next((t for t in getattr(self.app, "tabs", []) if getattr(t, "title", None) == tab_title), None)
            if found is not None:
                try:
                    self.app.notebook.select(found.frame)
                    found.navigate(**kwargs)
                except Exception as exc:          # noqa: BLE001 - another tab's problem mustn't break this one
                    self.app.set_status(f"The {tab_title} tab couldn't open that: {exc}")
                    return found
        if found is None:
            self.app.set_status(f"The {tab_title} tab isn't available.")
        return found

    def _credits_verdict(self, r: dict) -> str:
        for pid in r.get("plex_ids") or []:
            film = self._films_by_id.get(pid)
            if film is not None:
                info = film.credits
                return info.verdict if info is not None else ""
        return ""

    # -- your taste -----------------------------------------------------------------------------------------------
    def _kind_changed(self):
        kind = self.kind_var.get()
        self.min_films_var.set(str(TASTE_KINDS.get(kind, 3)))
        self._taste_changed(now=True)

    def _taste_changed(self, now: bool = False):
        if now:
            self._cancel_timer("taste")
            self._load_taste()
        else:
            self._after("taste", DEBOUNCE_MS, self._load_taste)

    def _min_films(self) -> int:
        try:
            value = int(float(self.min_films_var.get()))
        except (TypeError, ValueError):
            value = TASTE_KINDS.get(self.kind_var.get(), 3)
        value = min(max(value, 1), 100)
        if self.min_films_var.get() != str(value):
            self.min_films_var.set(str(value))
        return value

    def _load_taste(self):
        if self.state_message() is not None:
            self._need["taste"] = True
            return
        self._need["taste"] = False
        kind, least = self.kind_var.get(), self._min_films()
        request = {"action": "taste", "kinds": [kind], "count": 100, "min_films": least}
        if self._model_ready:                   # the model is fitted: a few milliseconds
            started = time.perf_counter()
            self._show_taste(self.app.ask(request), kind, least)
            self.timings["taste"] = time.perf_counter() - started
            return
        self.show_message(self.taste_view, "Learning your taste...", "Fitting the model to your ratings.")
        if self._fitting is not None:           # a search (or an earlier click here) is fitting it: wait for that,
            self._waiting.add("taste")          # then show whichever kind is picked by then
            return
        self._fitting = "taste"
        self._tokens["taste"] += 1
        token, catalog = self._tokens["taste"], self.catalog
        self._set_busy("taste", True)

        def done(answer):
            if token != self._tokens["taste"] or catalog is not self.catalog:
                return
            self._set_busy("taste", False)
            self._model_ready = True
            if self._fitting == "taste":
                self._fitting = None
            self._waiting.discard("taste")
            # (replaces 'Learning your taste...')
            self.app.set_status(f"Watch Next: learned your taste from your {answer.get('rated_films', 0):,} "
                                "ratings" if answer.get("ok") else
                                "Watch Next: " + str(answer.get("error") or "couldn't work out your taste"))
            if (self.kind_var.get(), self._min_films()) != (kind, least):
                self._load_taste()              # the selection moved on while the model was fitted (quick now)
            else:
                self._show_taste(answer, kind, least)
            self._resume()                      # a search that waited for the model

        def failed(message):
            if token != self._tokens["taste"] or catalog is not self.catalog:
                return
            self._set_busy("taste", False)
            if self._fitting == "taste":
                self._fitting = None
            self._waiting.discard("taste")
            self.show_message(self.taste_view, "Couldn't work out your taste", message)
            self.app.set_status(f"Watch Next couldn't work out your taste: {message}")
            self._resume()

        # key: another taste job would call this one off - though while this one fits the model, a newer kind or
        # minimum waits for it instead (above), since it needs the same model; a new collection calls it off too
        self.app.run(lambda: handle(request, catalog=catalog), done, failed, status="Learning your taste...",
                     key="watchnext.taste")

    def _clear_taste(self):
        self.taste_answer = None
        self.taste_table.set_rows([], keep_sort=False)
        self.taste_view.clear()
        self.taste_foot.configure(text="")

    def _show_taste(self, answer: dict, kind: str, least: int):
        self.taste_answer = answer
        plural, singular = KIND_NAMES.get(kind, (kind, kind))
        self.taste_columns[0] = ("label", singular, 150, "w")
        self.taste_table.columns = self.taste_columns
        self.taste_table.tree.heading("label", text=singular)
        if not answer.get("ok"):
            error = str(answer.get("error") or "")
            self.show_message(self.taste_view, "Not enough ratings yet" if "rate at least" in error else
                              "Couldn't work out your taste", error[:1].upper() + error[1:])
            self.taste_table.set_rows([], keep_sort=False)
            self.taste_foot.configure(text="")
            return
        group = answer["groups"][kind]
        clickable = True                     # (a studio opens its list of films on the Film tab)
        rated = answer.get("rated_films", 0)
        if group.get("above") or group.get("below"):
            self.taste_view.show(lambda p: draw_taste(p, group, kind, least, clickable,
                                                      lambda row: self._taste_pick(kind, row)))
        else:
            self.show_message(self.taste_view, f"No {plural.lower()} with {least}+ films you rated",
                              "Try a lower minimum above.")
        rows = sorted(group.get("above", []) + group.get("below", []), key=lambda r: -r["tilt"])
        self.taste_table.set_rows([{"label": display_label(kind, r["label"]), "tilt": f"{r['tilt']:+.2f}",
                                    "films": r["films"], "your_average": float(r["your_average"]), "row": r}
                                   for r in rows], keep_sort=False)
        qualifying = group.get("qualifying", len(rows))
        shown = f"All {len(rows)}" if len(rows) >= qualifying else f"{len(rows)} of {qualifying:,}"
        self.taste_table_card.title_label.configure(text=f"{shown} {plural.lower()}")
        self.taste_table.sorted_by = None
        for key, heading, _w, _a in self.taste_table.columns:
            self.taste_table.tree.heading(key, text=heading)
        foot = f"Out of the {rated:,} films you rated; your average is {answer.get('your_average', 0):.2f}."
        if clickable and rows:
            foot += (" Double-click a row to see every film of theirs on the Film tab." if kind == "studio" else
                     " Double-click a row to see films of that kind.")
        self.taste_foot.configure(text=foot)

    def _taste_row_opened(self, row: dict):
        if row.get("row"):
            self._taste_pick(self.kind_var.get(), row["row"])

    def _taste_pick(self, kind: str, row: dict):
        """The recommendations for a taste row's kind of film: the films you haven't seen, or - if you've seen
        them all - the ones you've played but not rated. A person goes by their id (namesakes)."""
        clickable, played, words = taste_click(kind, row)
        if not clickable:
            if words:                           # e.g. every film of theirs is rated: nothing to show
                self.app.set_status(f"Watch Next: {display_label(kind, row['label'])} - {words[:1].lower()}"
                                    f"{words[1:]}.")
            return
        if kind == "studio":                    # (no studio filter here: the Film tab lists a studio's films)
            self._goto("Film", studio=row["label"])
            return
        extra = {"include_watched": True} if played else {}
        if kind in ("director", "actor") and row.get("id"):
            self.navigate(person_id=row["id"], person=display_label(kind, row["label"]), **extra)
            return
        picked = taste_filter(kind, row["label"])
        if picked is None:
            return
        key, value = picked
        self.navigate(**{key: value}, **extra)

    # -- how accurate ---------------------------------------------------------------------------------------------
    def _clear_accuracy(self):
        self.eval_answer = None
        n = getattr(self, "rated_count", 0)
        self.show_message(self.acc_view, "Not tested yet",
                          f"Click 'Test it on my ratings' - it re-trains the model 10 times on your {n:,} "
                          "ratings, which takes a few seconds.")
        self.eval_status.configure(text="")
        self.verdict_title.configure(text="")
        self.verdict.configure(text="")
        self.method.configure(text="")

    def _evaluate(self):
        if self.state_message() is not None or self._busy["eval"]:
            return
        self._tokens["eval"] += 1
        token, catalog = self._tokens["eval"], self.catalog
        started = time.perf_counter()
        self._set_busy("eval", True)
        self._tick(token, started)

        def done(answer):
            if token != self._tokens["eval"] or catalog is not self.catalog:
                return
            self._cancel_timer("eval")
            self._set_busy("eval", False)
            self.timings["evaluate"] = seconds = time.perf_counter() - started
            self._show_accuracy(answer, seconds)
            self.app.set_status(f"Watch Next: tested on your ratings in {seconds:.1f} s" if answer.get("ok") else
                                "Watch Next can't test it yet: " + str(answer.get("error") or ""))

        def failed(message):
            if token != self._tokens["eval"] or catalog is not self.catalog:
                return
            self._cancel_timer("eval")
            self._set_busy("eval", False)
            self.eval_status.configure(text="")
            self.show_message(self.acc_view, "The test didn't finish", message)
            self.app.set_status(f"Watch Next's test didn't finish: {message}")

        # Just the model the app uses: the ask API can also compare other settings, but that takes six times
        # as long and only matters to someone tuning it.
        request = {"action": "evaluate", "lambdas": [DEFAULT_LAMBDA]}
        self.app.run(lambda: handle(request, catalog=catalog), done, failed,
                     status="Testing the recommender on your ratings...", key="watchnext.eval")

    def _tick(self, token: int, started: float):
        """The running clock while the test runs."""
        if token != self._tokens["eval"] or not self._busy["eval"]:
            return
        n = getattr(self, "rated_count", 0)
        self.eval_status.configure(text=f"Testing on your {n:,} ratings...  {time.perf_counter() - started:.0f} s")
        self._after("eval", 500, lambda: self._tick(token, started))

    def _show_accuracy(self, answer: dict, seconds: float | None = None):
        self.eval_answer = answer
        if not answer.get("ok"):
            error = str(answer.get("error") or "")
            self.show_message(self.acc_view, "Can't test it yet", error[:1].upper() + error[1:] +
                              ". Rate more films in Plex (the stars) and try again.")
            self.eval_status.configure(text="")
            self.verdict_title.configure(text="")
            self.verdict.configure(text="")
            self.method.configure(text="")
            return
        n = answer.get("rated_films", 0)
        self.acc_view.show(lambda p: draw_accuracy(p, answer))
        headline, text = accuracy_verdict(answer)
        self.verdict_title.configure(text=headline)
        self.verdict.configure(text=text)
        self.method.configure(text=method_text(answer, seconds))
        self.eval_status.configure(text=f"Tested on your {n:,} ratings.")

    # -- your critics ---------------------------------------------------------------------------------------------
    def _critics_cards(self, show: bool):
        """The page, or (while the critics are compared, or when they can't be) the message instead."""
        if show:
            self.critics_msg.grid_remove()
            self.critics_scroll.grid()
        else:
            self.critics_scroll.grid_remove()
            self.critics_msg.grid()

    def _clear_critics(self):
        self.critics_answer = self.critic_answer = None
        self._critics_state = None
        self._critic_id = None
        self._critic_names, self._critics_suggest = {}, None
        self.critics_title.configure(text="")
        self.critics_text.configure(text="")
        self.critics_picks_link.grid_remove()
        self.critic_table.set_rows([], keep_sort=False)
        self.critic_list_hint.configure(text="")
        self.funnel.clear()
        self._empty_critic_details()
        for t in self._critic_texts:
            t.configure(state="disabled")
        self.critic_strip.clear()
        self._clear_critics_eval()
        self._critics_cards(False)
        self.critics_msg.clear()

    def _load_critics(self):
        """Compare every critic with your ratings (a second or so, the first time: the reviews are read from the
        database), then show the closest - or the critic asked for."""
        if self.state_message() is not None:
            self._need["critics"] = True
            return
        self._need["critics"] = False
        self._tokens["critics"] += 1
        token, catalog = self._tokens["critics"], self.catalog
        wanted = self._pending_critic
        self._critics_state = "loading"
        self._critics_cards(False)
        self.critics_picks_link.grid_remove()
        self.critics_title.configure(text="")
        self.critics_text.configure(text="")
        self.show_message(self.critics_msg, "Comparing critics with your ratings...",
                          "Reading the critic reviews Plex keeps, and setting each critic's Fresh and Rotten against "
                          "the films you rated.")
        started = time.perf_counter()
        critic_request = self._critic_request

        def work():
            overview = handle({"action": "critics", "points": True, "count": CRITIC_LISTS}, catalog=catalog)
            names = CR.critic_names(catalog) if CR.ready(catalog) else []
            asked = None
            if wanted:
                asked = handle(critic_request(wanted), catalog=catalog)
            detail = asked if asked is not None and asked.get("ok") else None
            if detail is None and overview.get("ok"):
                first = (overview.get("closest") or overview.get("points") or [None])[0]
                if first is not None:
                    detail = handle(critic_request(first["id"]), catalog=catalog)
            if overview.get("ok"):
                check()
                CR._recommender(catalog)        # (their picks' predictions: later clicks are quick)
            return overview, detail, asked, names

        def done(result):
            if token != self._tokens["critics"] or catalog is not self.catalog:
                return                          # another collection took over
            overview, detail, asked, names = result
            self.timings["critics_job"] = time.perf_counter() - started
            shown = time.perf_counter()
            if self._pending_critic == wanted:
                self._pending_critic = None
            self._index_critic_names(names)
            self._show_critics(overview)
            if detail is not None and detail.get("ok") and self._critics_browsable():
                self._show_critic(detail, scroll=asked is not None and asked.get("ok"))
            self.timings["critics_show"] = time.perf_counter() - shown
            if wanted and (asked is None or not asked.get("ok")):
                self.app.set_status(critic_not_found(wanted))
            elif overview.get("ok"):
                self.app.set_status(f"Watch Next: compared {overview['critics_compared']:,} critics with your "
                                    f"ratings ({self.timings['critics_job']:.1f} s)")
            else:
                title = critics_headline(overview)[0]
                self.app.set_status(f"Watch Next: {title[:1].lower()}{title[1:]}.")
            if self._pending_critic:            # asked for while this worked
                self._navigate_critic(self._pending_critic)

        def failed(message):
            if token != self._tokens["critics"] or catalog is not self.catalog:
                return
            self._critics_state = "problem"
            self._critics_cards(False)
            self.show_message(self.critics_msg, "Couldn't compare the critics", message)
            self.app.set_status(f"Watch Next couldn't compare the critics: {message}")

        # key: a newer load (another collection) calls this one off
        self.app.run(work, done, failed, status="Comparing critics with your ratings...", key="watchnext.critics")

    def _index_critic_names(self, names: list[dict]):
        """'Find a critic': every critic by name (a name two critics share says whose publication it is)."""
        counts = {}
        for r in names:
            counts[r["name"]] = counts.get(r["name"], 0) + 1
        labels = {}
        for r in sorted(names, key=lambda r: (-r["shared"], -r["reviews"])):
            label = r["name"] if counts[r["name"]] == 1 else f"{r['name']} ({r['publication'] or r['id']})"
            labels.setdefault(label, r["id"])
        self._critic_names = labels
        self._critics_suggest = suggester(list(labels)) if labels else None

    def _suggest_critics(self, text: str):
        return self._critics_suggest(text) if self._critics_suggest is not None else []

    def _critic_typed(self, text: str):
        text = text.strip()
        if not text or self._critics_state != "ready":
            return
        self._select_critic(self._critic_names.get(text, text),
                            not_found=f"No critic called “{text}” in your collection.")

    def _show_critics(self, ov: dict):
        """The overview: the headline, the list and every critic's dot (or, if they can't be compared, why)."""
        self.critics_answer = ov
        title, body = critics_headline(ov)
        if not ov.get("ok"):
            self._critics_state = "problem"
            self._critics_cards(False)
            self.show_message(self.critics_msg, title, body)
            return
        self._critics_state = "ready"
        self.critics_title.configure(text=title)
        self.critics_text.configure(text=body)
        self.critics_row.grid()                 # (a critic shown while they couldn't be compared hid these)
        self.critics_eval_card.grid()
        if ov.get("closest"):
            self.critics_picks_link.grid()
        else:
            self.critics_picks_link.grid_remove()
        n = len(ov.get("points") or [])
        self.critic_list_buttons["all"].configure(text=f"Everyone ({n:,})")
        self._critics_cards(True)
        self._fill_critic_table()
        self._draw_funnel()

    def _critic_list_changed(self):
        if self.critics_answer is not None and self.critics_answer.get("ok"):
            self._fill_critic_table()

    def _fill_critic_table(self):
        ov = self.critics_answer
        which = self.critic_list_var.get()
        rows = critic_rows(ov, which)
        table = self.critic_table
        table.set_rows(rows, keep_sort=False)
        table.sorted_by = None
        for key, heading, _w, _a in table.columns:
            table.tree.heading(key, text=heading)
        self.critic_list_hint.configure(text=critics_list_hint(ov, which))
        self._select_in_table(self._critic_id)

    def _select_in_table(self, cid, switch: bool = False):
        """Select the critic's row. switch: if they aren't in the list on show, show a list they're in (closest,
        least in step, everyone) - a critic reached from a dot, a link or 'Find' is then picked out in the list."""
        table = self.critic_table
        for iid, row in table.rows.items():
            if row["id"] == cid:
                if table.tree.selection() != (iid,):
                    table.tree.selection_set(iid)
                table.tree.see(iid)
                return
        ov = self.critics_answer or {}
        if switch and cid:
            for which, rows in (("closest", ov.get("closest")), ("furthest", ov.get("furthest")),
                                ("all", ov.get("points"))):
                if any(r["id"] == cid for r in rows or []):
                    self.critic_list_var.set(which)
                    self._fill_critic_table()            # (selects them)
                    return
        if table.tree.selection():
            table.tree.selection_remove(*table.tree.selection())

    def _draw_funnel(self):
        ov = self.critics_answer
        if ov is None or not ov.get("ok"):
            return
        closest = [r["id"] for r in ov.get("closest", [])]
        furthest = [r["id"] for r in ov.get("furthest", [])]
        points = ov.get("points") or []
        placed = self._funnel_placed = {}

        def draw(p):
            placed.clear()
            draw_critic_funnel(p, ov, points, closest, furthest, self._critic_id, self._select_critic, placed)
            placed["painter"] = p

        self.funnel.show(draw)            # (a redraw - on a resize - marks whoever is chosen by then)

    def _mark_funnel(self):
        """Choosing another critic: move the mark on the funnel, rather than drawing its hundreds of dots again (on the
        screen that takes a good part of a tenth of a second)."""
        placed = getattr(self, "_funnel_placed", None)
        p = (placed or {}).get("painter")
        if not placed or p is None or getattr(p, "canvas", None) is not self.funnel:
            self._draw_funnel()
            return
        try:
            self.funnel.delete("selmark")
            self.funnel.delete("linelabel")          # (they move out of the mark's way)
            mark_critic(p, placed["dots"].get(self._critic_id), placed["right"], placed.get("lines", ()))
        except tk.TclError:
            pass

    def _critic_row_selected(self, row: dict):
        if row.get("id") and row["id"] != self._critic_id:
            self._select_critic(row["id"])

    def _select_critic(self, cid: str, scroll: bool = False,
                       not_found: str = "That critic has no reviews in your collection."):
        """Show one critic (an id, or a name): at once when the critics are worked out (a few milliseconds),
        else in the background."""
        if self._critics_state in (None, "loading"):
            self._pending_critic = cid
            return
        if not self._critics_browsable():
            return
        catalog = self.catalog
        request = self._critic_request(cid)
        quick = CR.ready(catalog) and (("recommend.recommender", DEFAULT_LAMBDA) in catalog.cache or
                                       self.rated_count < 30)
        if quick:
            started = time.perf_counter()
            answer = self.app.ask(request)
            self.timings["critic_ask"] = time.perf_counter() - started
            self._critic_answered(answer, scroll, not_found)
            self.timings["critic"] = time.perf_counter() - started
            return
        self._tokens["critic"] += 1
        token = self._tokens["critic"]

        def done(answer):
            if token == self._tokens["critic"] and catalog is self.catalog:
                self._critic_answered(answer, scroll, not_found)

        self.app.run(lambda: handle(request, catalog=catalog), done, None, status="Looking the critic up...",
                     key="watchnext.critic")

    def _critics_browsable(self) -> bool:
        """Can a critic be shown? When the critics are compared - and when they can't be only for want of ratings
        (too few, or all the same): a critic's reviews and Fresh picks don't need your ratings, so one asked for
        by a link or the search is shown under the reason, rather than leading nowhere."""
        if self._critics_state == "ready":
            return True
        return (self._critics_state == "problem" and
                (self.critics_answer or {}).get("reason") in ("few_ratings", "flat_ratings"))

    def _critics_problem_page(self):
        """The page for a critic shown while the critics can't be compared: the reason as its headline, then that
        critic's card - without the list, the chart or the test, which need the comparison."""
        title, body = critics_headline(self.critics_answer or {})
        self.critics_title.configure(text=title)
        self.critics_text.configure(text=body)
        self.critics_picks_link.grid_remove()
        self.critics_row.grid_remove()
        self.critics_eval_card.grid_remove()
        self._critics_cards(True)

    def _critic_answered(self, answer: dict, scroll: bool, not_found: str):
        if not answer.get("ok"):
            self.app.set_status(not_found)
            return
        self._show_critic(answer, scroll)

    def _navigate_critic(self, cid: str):
        """navigate(critic=...): Your critics, on that critic - now, or once the page (or the collection) is
        ready."""
        self._pending_critic = cid
        try:
            self.views.select(self.critics_page)
        except tk.TclError:
            pass
        if self.state_message() is not None:
            return                              # (the collection isn't read yet: shown once it is)
        if self._critics_browsable():
            self._pending_critic = None
            self._select_critic(cid, scroll=True, not_found=critic_not_found(cid))
        elif self._critics_state == "problem":
            self._pending_critic = None
            answer = self.critics_answer or {}
            self.app.set_status("Watch Next: " + str(answer.get("error") or "the critics can't be compared yet"))
        elif self._critics_state is None:
            self._load_critics()

    # the details of one critic
    @property
    def critic_links(self) -> list:
        """(words, command) of every link in the critic's details."""
        return [link for linker in self._critic_linkers for link in linker.links]

    def _show_critic(self, d: dict, scroll: bool = False):
        """One critic's record with you: how often they agreed, film by film, and their picks among the films you
        haven't played - every film a link to its page - beside a strip of your ratings of the films you share."""
        self.critic_answer = d
        self._critic_id = d["id"]
        if self._critics_state != "ready":
            self._critics_problem_page()
        self._empty_critic_details()
        head, films_text, picks_text = self._critic_texts
        _head_links, film_links, pick_links = self._critic_linkers
        t = head
        t.insert("end", d["name"] + "\n", "title")
        pubs = d.get("publications") or []
        meta = []
        if pubs:
            meta.append(pubs[0])
            if len(pubs) > 1:
                meta.append("also " + (_either(pubs[1:], "and") if len(pubs) <= 3 else
                                       f"{pubs[1]}, {pubs[2]} and {len(pubs) - 3} more"))
        n = d.get("reviews", 0)
        meta.append(f"{n:,} review{'s' if n != 1 else ''} in your library" +
                    (f", {d['fresh_share']:.0%} Fresh" if d.get("fresh_share") is not None else ""))
        t.insert("end", "   ·   ".join(meta) + "\n", "meta")
        shared = d.get("shared", 0)
        comparable = d.get("comparable", True)
        if not comparable:
            flat = (self.critics_answer or {}).get("reason") == "flat_ratings"
            t.insert("end", ("Rate the films you liked higher than the rest" if flat else "Rate more films critics "
                             "reviewed") + " to see how in step they are with you\n", "big")
            t.insert("end", "Meanwhile, here's what they thought of films you haven't played.\n", "soft")
        elif not shared:
            t.insert("end", "You haven't rated any film they reviewed\n", "big")
            t.insert("end", "So there's nothing to compare yet - but here's what they thought of films you haven't "
                            "seen.\n", "soft")
        else:
            a = d["agreed"]
            t.insert("end", f"Agreed with you on {a} of the {_films(shared)} you've both judged ({a / shared:.0%})\n",
                     "big")
            if shared < CR.MIN_SHARED:
                t.insert("end", f"You share only {_films(shared)} - too few to say how in step they are.\n", "soft")
            else:
                t.insert("end", match_words(d) + "\n", "soft")
            wf, wr = d.get("when_fresh") or {}, d.get("when_rotten") or {}
            bits = []
            if wf.get("films"):
                bits.append(f"When they say Fresh you rate it {wf['your_average']:.1f} on average "
                            f"({_films(wf['films'])})")
            if wr.get("films"):
                bits.append(f"Rotten, {wr['your_average']:.1f} ({wr['films']})" if bits else
                            f"When they say Rotten you rate it {wr['your_average']:.1f} on average "
                            f"({_films(wr['films'])})")
            if bits:
                t.insert("end", "; ".join(bits) + ".\n", "soft")
            if d.get("anti_twin"):
                t.insert("end", "They disagree with you more often than chance would - their Rotten may be your "
                                "Fresh.\n", "soft")
            elif shared >= CR.MIN_SHARED and d.get("stand_out") in ("luck", "too few"):
                t.insert("end", "On your ratings no critic stands out from luck yet, so this is how they've done so "
                                "far.\n", "muted")
        t.insert("end", "Click a film for its page.", "small")
        # where you agreed and didn't
        t = films_text
        if shared:
            self._critic_films(film_links, f"Where you agreed ({d.get('agreed_total', 0)})", d.get("agreed_on", []),
                               d.get("agreed_total", 0), "Nowhere yet.")
            self._critic_films(film_links, f"Where you didn't ({d.get('disagreed_total', 0)})",
                               d.get("disagreed_on", []), d.get("disagreed_total", 0), "Nowhere yet.")
        elif not comparable:
            t.insert("end", "Films you've both judged\n", "h")
            t.insert("end", "Shown once there are enough ratings to compare.\n", "muted")
        else:
            t.insert("end", "Films you've both judged\n", "h")
            t.insert("end", "None yet: you haven't rated any film they reviewed.\n", "muted")
        # their picks among the films you haven't played
        t = picks_text
        kind = d.get("picks_kind", "fresh")
        total = d.get("picks_total", 0)
        t.insert("end", ("Fresh from them" if kind == "fresh" else "Panned by them") +
                 f", not played by you ({total:,})\n", "h")
        picks = d.get("picks") or []
        if not picks:
            t.insert("end", "None - you've played every film they " + ("liked.\n" if kind == "fresh" else
                                                                        "panned.\n"), "muted")
        for r in picks:
            t.insert("end", "•\t", "bullet")
            pick_links.add(r["label"], lambda r=r: self._goto("Film", film_key=r["film_key"], title=r["label"]),
                           ("bullet",))
            bits = []
            if r.get("predicted_rating") is not None:
                bits.append(f"Watch Next predicts {r['predicted_rating']:.1f}")
            if r.get("imdb_rating") is not None:
                bits.append(f"IMDb {r['imdb_rating']:g}")
            if bits:
                t.insert("end", " - " + " · ".join(bits), ("bullet", "soft"))
            t.insert("end", "\n", "bullet")
            if r.get("quote"):
                t.insert("end", f"“{r['quote']}”\n", "quote")
        if total > len(picks):
            t.insert("end", f"…and {total - len(picks):,} more\n", "quote")
        if kind == "rotten":
            t.insert("end", "They reliably disagree with you, so these are the films they panned.", "small")
        for t in self._critic_texts:
            t.configure(state="disabled")
            self._fit_text(t)
        self.critic_strip.show(lambda p, d=d: draw_critic_strip(p, d, self._strip_film))
        self._select_in_table(d["id"], switch=True)
        self._mark_funnel()
        if scroll and self._critics_state == "ready":       # (on the page without the list it's right under the reason)
            self._after("critics_scroll", 1, self._scroll_to_critic)

    def _critic_films(self, links: TextLinks, heading: str, rows, total: int, empty: str):
        t = links.text
        t.insert("end", heading + "\n", "h")
        if not rows:
            t.insert("end", empty + "\n", "muted")
            return
        for r in rows:
            t.insert("end", "•\t", "bullet")
            links.add(r["label"], lambda r=r: self._goto("Film", film_key=r["film_key"], title=r["label"]),
                      ("bullet",))
            t.insert("end", f" - {r['verdict']} · you rated it {r['your_rating']:g}", ("bullet", "soft"))
            t.insert("end", "\n", "bullet")
            if r.get("quote"):
                t.insert("end", f"“{r['quote']}”\n", "quote")
        if total > len(rows):
            t.insert("end", f"…and {total - len(rows):,} more\n", "quote")

    def _strip_film(self, key):
        d = self.critic_answer or {}
        row = next((r for r in d.get("films", []) if r["film_key"] == key), None)
        if row is not None:
            self._goto("Film", film_key=row["film_key"], title=row["label"])

    def _scroll_to_critic(self):
        """Bring the critic's details into view (after a link from another tab)."""
        try:
            self.critics_scroll.update_idletasks()
            height = self.critics_scroll.inner.winfo_height()
            if height > 0 and not self.critics_scroll.fits():
                self.critics_scroll.canvas.yview_moveto(max(self.critic_card.winfo_y() - GAP, 0) / height)
        except tk.TclError:
            pass

    FIT_LEAST_WIDTH = 60        # px: a details text narrower than this hasn't been given its width yet
    FIT_MOST_LINES = 300        # ...and none is ever made taller than this many lines

    def _fit_text(self, t: tk.Text):
        """A details text as tall as what's in it (the page scrolls, not the text). Measured in pixels: a Text's
        height counts lines of its own font, and the title's bigger font and the headings' spacing are taller.
        Not while it's still a few pixels wide - before the page is laid out, when every word would be a line of
        its own: the page would be made tens of thousands of pixels tall, and on Linux the X server refuses to
        draw anything that big (BadAlloc), which ends the app. It fits itself once it has its width."""
        try:
            if t.winfo_width() < self.FIT_LEAST_WIDTH * self.s:
                return
            pixels = t.count("1.0", "end", "update", "ypixels")
        except tk.TclError:
            return
        if isinstance(pixels, tuple):
            pixels = pixels[0] if pixels else 0
        line = self.__dict__.get("_detail_line")
        if line is None:                            # (measured once: every details text has the same font)
            line = self._detail_line = tkfont.Font(font=t.cget("font")).metrics("linespace")
        lines = min(max(math.ceil(int(pixels or 0) / max(line, 1)), 1), self.FIT_MOST_LINES)
        if int(t.cget("height")) != lines:
            t.configure(height=lines)

    def _critic_wheel(self, event):
        scroll = self.critics_scroll
        try:
            if not scroll.fits():
                scroll.canvas.yview_scroll(wheel_units(event), "units")
        except tk.TclError:
            pass
        return "break"

    def _empty_critic_details(self):
        for linker in self._critic_linkers:
            linker.clear()

    # the test of them as a guide
    def _clear_critics_eval(self):
        self.critics_eval_answer = None
        self.show_message(self.critics_eval_view, "Not tested yet",
                          "Click 'Test it on my ratings' - it takes a few seconds.")
        self.critics_eval_status.configure(text="")
        self._critics_verdict_words("", "", "")

    def _critics_verdict_words(self, headline: str, text: str, method: str):
        """The verdict under the test's chart - its three lines hidden while there's none (an empty label still
        takes a line's room)."""
        for label, words in ((self.critics_verdict_title, headline), (self.critics_verdict, text),
                             (self.critics_method, method)):
            label.configure(text=words)
            if words:
                label.grid()
            else:
                label.grid_remove()

    def _critics_evaluate(self):
        if self.state_message() is not None or self._busy["critics_eval"] or self._critics_state != "ready":
            return
        self._tokens["critics_eval"] += 1
        token, catalog = self._tokens["critics_eval"], self.catalog
        started = time.perf_counter()
        self._set_busy("critics_eval", True)
        self._critics_tick(token, started)

        def done(answer):
            if token != self._tokens["critics_eval"] or catalog is not self.catalog:
                return
            self._cancel_timer("critics_eval")
            self._set_busy("critics_eval", False)
            self.timings["critics_eval"] = seconds = time.perf_counter() - started
            self._show_critics_eval(answer, seconds)
            self.app.set_status(f"Watch Next: tested your critics on your ratings in {seconds:.1f} s"
                                if answer.get("ok") else
                                "Watch Next can't test your critics yet: " + str(answer.get("error") or ""))

        def failed(message):
            if token != self._tokens["critics_eval"] or catalog is not self.catalog:
                return
            self._cancel_timer("critics_eval")
            self._set_busy("critics_eval", False)
            self.critics_eval_status.configure(text="")
            self.show_message(self.critics_eval_view, "The test didn't finish", message)
            self.app.set_status(f"Watch Next's critics test didn't finish: {message}")

        self.app.run(lambda: handle({"action": "critics", "view": "evaluate"}, catalog=catalog), done, failed,
                     status="Testing your critics on your ratings...", key="watchnext.critics_eval")

    def _critics_tick(self, token: int, started: float):
        """The running clock while the test runs."""
        if token != self._tokens["critics_eval"] or not self._busy["critics_eval"]:
            return
        n = (self.critics_answer or {}).get("rated_with_reviews", 0)
        self.critics_eval_status.configure(text=f"Testing on the {n:,} films you rated that critics reviewed...  "
                                                f"{time.perf_counter() - started:.0f} s")
        self._after("critics_eval", 500, lambda: self._critics_tick(token, started))

    def _show_critics_eval(self, answer: dict, seconds: float | None = None):
        self.critics_eval_answer = answer
        if not answer.get("ok"):
            error = str(answer.get("error") or "")
            self.show_message(self.critics_eval_view, "Can't test them yet", error[:1].upper() + error[1:] + ".")
            self.critics_eval_status.configure(text="")
            self._critics_verdict_words("", "", "")
            return
        self.critics_eval_view.show(lambda p: draw_critics_eval(p, answer))
        headline, text = critics_verdict(answer)
        self._critics_verdict_words(headline, text, critics_method_text(answer, seconds))
        self.critics_eval_status.configure(text=f"Tested on {answer.get('films_covered', 0):,} films.")


# ---------------------------------------------------------------------------------------------------------
# The charts
# ---------------------------------------------------------------------------------------------------------
def fit_steps(breakdown: dict, room: int) -> tuple[list[dict], float]:
    """At most `room` steps (the biggest pushes, in their order); the smaller ones join 'Everything else'."""
    steps = list(breakdown.get("steps", []))
    rest = breakdown.get("everything_else") or 0.0
    if len(steps) > room:
        keep = {id(s) for s in sorted(steps, key=lambda s: -abs(s["value"]))[:max(room, 0)]}
        rest += sum(s["value"] for s in steps if id(s) not in keep)
        steps = [s for s in steps if id(s) in keep]
    return steps, rest


def draw_breakdown(p, r: dict):
    """The waterfall: where the model starts, the biggest pushes up and down, the rest, and the prediction.
    In a small space it keeps rows readable: fewer steps (the rest join 'Everything else'), shorter labels."""
    b = r["breakdown"]
    narrow = p.width < p.u(360)
    sub = "From the model's starting point" if narrow else \
        "The model's starting point, then the biggest pushes up and down"
    total = b.get("total_before_limits")
    if total is not None and abs(total - b["predicted"]) >= 0.05:
        sub = f"The steps add up to {total:.1f}; ratings stop at {b['predicted']:.1f}"
    head = p.line_height(p.font(10, "bold")) + (p.line_height(p.font(9)) if sub else 0) + p.u(8)
    axis = p.line_height(p.font(8)) + p.u(6)
    rows = int((p.height - head - axis) // p.u(19))
    steps, rest = fit_steps(b, max(rows - 3, 2))
    charts.waterfall(p, b["base"], steps, b["predicted"], rest=rest, title="How the prediction adds up",
                     subtitle=sub, base_label="Start" if narrow else "Starting point",
                     total_label="Predicted" if narrow else "Predicted for you",
                     base_note="Where the model starts for every film - not your average: the bars below (its "
                               "scores, the kind of film, its people) move each film from here.")


def draw_taste(p, group: dict, kind: str, least: int, clickable: bool, pick):
    """The diverging chart of one taste group, with as many rows each way as fit (4 to 15 a side)."""
    plural = KIND_NAMES.get(kind, (kind, kind))[0]
    title = f"{plural} you rate above or below their reputation"
    sub = (f"The clearest tilts each way, of {group.get('qualifying', 0):,} with {least}+ films you rated "
           "(how many in brackets)")
    if clickable:
        sub += " - click a bar to see films"
    head = p.line_height(p.font(10, "bold")) + 2 * p.line_height(p.font(9)) + p.u(8) + p.u(18)
    room = min(max(int((p.height - head) // p.u(24)), 8), 30)
    items = taste_items(group, kind, room, clickable)
    # A name too long for the label column is shortened, not its film count (charts.diverging gives labels at
    # most 36% of the width, and would cut the end off).
    f = p.font(9)
    most = p.width * 0.36 - p.u(10)
    for it in items:
        if p.text_width(it["label"], f) > most:
            count = f" ({it['row']['films']})"
            it["label"] = p.fit(it["label"][:-len(count)], f, most - p.text_width(count, f)) + count

    def click(key):
        if items[key]["clickable"]:             # (a row with nothing left to see says so in its tip)
            pick(items[key]["row"])

    # Two decimals, as in the table beside it
    charts.diverging(p, items, title=title, subtitle=sub, legend=("You rate higher", "You rate lower"),
                     value_fmt=lambda v: f"{v:+.2f}", on_click=click if clickable else None)


def draw_accuracy(p, answer: dict):
    gap = p.u(18)
    panel_w = (p.width - 2 * gap) / 3
    short = p.text_width(METHOD_LABELS[0][1], p.font(9)) + p.u(10) > panel_w * 0.5
    n = answer.get("rated_films", 0)
    charts.metric_panels(p, accuracy_panels(answer, short), title="How close its guesses come to your own ratings",
                         subtitle=f"Each of your {n:,} ratings predicted by a model that never saw it; "
                                  "this model is in blue")


def _jitter(key: str, salt: int = 0) -> random.Random:
    """The same little offsets for the same dot, drawing after drawing (a redraw mustn't shuffle the dots)."""
    return random.Random(zlib.crc32(f"{salt}:{key}".encode("utf-8")))


def _dot_legend(p, x, y, keys, font) -> float:
    """A row of dot swatches with their words: [(label, colour)]; returns the height used."""
    lx = x
    r = p.u(3.5)
    for label, color in keys:
        need = r * 2 + p.u(5) + p.text_width(label, font) + p.u(14)
        if lx + need > x + p.width and lx > x:
            lx = x
            y += p.line_height(font) + p.u(4)
        p.circle(lx + r, y, r, color)
        p.text(lx + 2 * r + p.u(5), y, label, font, T.INK_2, "w")
        lx += need
    return y


def _label_on(p, x, y, text, font, color, anchor, tag=None):
    """Text on a backing of the chart's surface, so a line or dot under it doesn't cross it out."""
    w, h = p.text_width(text, font), p.line_height(font)
    x0 = {"e": x - w, "w": x}.get(anchor, x - w / 2)
    p.rect(x0 - p.u(2), y - h / 2, x0 + w + p.u(2), y + h / 2, T.SURFACE, tag=tag)
    p.text(x, y, text, font, color, anchor, tag=tag)


def _label_box(p, x, y, text, font, anchor) -> tuple:
    """The box _label_on() covers."""
    w, h = p.text_width(text, font), p.line_height(font)
    x0 = {"e": x - w, "w": x}.get(anchor, x - w / 2)
    return x0 - p.u(2), y - h / 2, x0 + w + p.u(2), y + h / 2


def _overlaps(a, b, pad: float = 0) -> bool:
    """Two boxes (x0, y0, x1, y1) overlap - or come within pad of each other."""
    return a[0] < b[2] + pad and b[0] < a[2] + pad and a[1] < b[3] + pad and b[1] < a[3] + pad


def mark_critic(p, dot, right: float, lines=()):
    """The chosen critic on the funnel - their dot ringed in ink, a size up, with their name beside it - and the
    lines' words ('a typical critic 68%', 'a coin 57%'), kept clear of it: the critic who shares the most films
    sits at the right end, where the words go, so the words move (to the other side of their line, else to the
    left of the mark) rather than hide under the name. dot: (x, y, colour, name), or None for nobody chosen;
    lines: [(y, words, colour, side)], side -1 = above the line by default, +1 below. Tagged 'selmark' and
    'linelabel', so choosing another critic redraws just these (the dots stay: see Tab._mark_funnel)."""
    fs = p.font(8)
    right_x = right - p.u(2)
    gap = p.u(2)                                        # (clear by a little, not just not touching)

    def spot(y, side):
        return y + side * p.u(8)

    def clear(box, boxes):
        return not any(_overlaps(box, other, gap) for other in boxes)

    defaults = [_label_box(p, right_x, spot(y, side), words, fs, "e") for y, words, _c, side in lines]
    taken = []
    if dot is not None:
        cx, cy, color, name = dot
        r = p.u(3.2) * 1.45
        p.circle(cx, cy, r, color, ring=T.INK, ring_width=2, tag="selmark")
        taken.append((cx - r - p.u(1), cy - r - p.u(1), cx + r + p.u(1), cy + r + p.u(1)))
        fb = p.font(8, "bold")
        name = p.fit(name, fb, p.u(160))
        fits_right = cx + p.u(10) + p.text_width(name, fb) < right
        nx, anchor = (cx + p.u(9), "w") if fits_right else (cx - p.u(9), "e")
        ny = cy - p.u(9)                                 # above the dot - below, if that's where the words go
        if not clear(_label_box(p, nx, ny, name, fb, anchor), defaults):
            below = cy + p.u(9)
            if clear(_label_box(p, nx, below, name, fb, anchor), defaults):
                ny = below
        _label_on(p, nx, ny, name, fb, T.INK, anchor, tag="selmark")
        taken.append(_label_box(p, nx, ny, name, fb, anchor))
    left_x = min((box[0] for box in taken), default=right_x) - p.u(4)      # (left of the mark)
    for y, words, color, side in lines:
        tries = [(right_x, spot(y, side)), (right_x, spot(y, -side)), (left_x, spot(y, side)),
                 (left_x, spot(y, -side))]
        x, ly = next(((x, ly) for x, ly in tries if clear(_label_box(p, x, ly, words, fs, "e"), taken)), tries[0])
        _label_on(p, x, ly, words, fs, color, "e", tag="linelabel")
        taken.append(_label_box(p, x, ly, words, fs, "e"))


def draw_critic_funnel(p, ov: dict, points, closest=(), furthest=(), selected=None, on_critic=None, placed=None):
    """Every critic you share enough films with: films you've both judged (across, doubling at each step) and how
    often they agreed with you (up). The grey band is where a typical critic lands 19 times in 20 by luck alone -
    wide for a few films, narrow for many - so a dot outside it agrees with you more (or less) than luck explains.
    Your closest critics are blue, the least in step red; click a dot for that critic. placed (a dict), if given,
    is filled with where everything went - {'dots': {id: (x, y, colour, name)}, 'right', 'lines', 'size'} - so the
    chosen critic can be marked afresh (and the lines' words moved out of their way) without drawing it all
    again."""
    typical, chance = ov["typical"]["agreement"], ov["typical"]["chance"]
    least = max(int(ov.get("min_shared") or CR.MIN_SHARED), 1)
    pts = [pt for pt in points if pt["shared"] >= least]
    if not pts:
        charts.message(p, f"No critic shares {least} of your rated films yet",
                       "Rate more films critics reviewed and they'll show here.")
        return

    def band(n):
        return 1.96 * math.sqrt(typical * (1 - typical) / n)

    outside = sum(1 for pt in pts if abs(pt["agreed"] / pt["shared"] - typical) > band(pt["shared"]))
    title = f"Every critic you share {least}+ films with"
    sub = (f"The grey band is where a typical critic lands by luck alone: {outside} of {len(pts)} are outside it "
           f"(luck alone would put about {round(0.05 * len(pts))} there)")
    top = charts.header(p, 0, 0, p.width, title, sub)
    fs = p.font(8)
    closest, furthest = set(closest), set(furthest) - set(closest)
    ids = {pt["id"] for pt in pts}
    keys = [(f"Closest to you ({len(closest & ids)})", T.POSITIVE)]
    if furthest & ids:
        keys.append(("Least in step", T.NEGATIVE))
    keys.append(("Everyone else", T.BASELINE))
    ly = _dot_legend(p, 0, top + p.u(4), keys, fs)
    top = ly + p.line_height(fs) / 2 + p.u(6)
    p.text(0, top + p.line_height(fs) / 2, "How often they agreed with you", fs, T.INK_2, "w")
    top += p.line_height(fs) + p.u(2)
    axis_w = p.text_width("100%", fs) + p.u(10)
    axis_h = 2 * p.line_height(fs) + p.u(8)
    x0, x1 = axis_w, p.width - p.u(8)
    y0, y1 = top + p.u(4), p.height - axis_h
    if y1 - y0 < p.u(40):
        y0 = y1 - p.u(40)
    n_max = max(max(pt["shared"] for pt in pts), least * 4)
    lo, hi = math.log(least * 0.9), math.log(n_max * 1.1)

    def X(n):
        return x0 + (math.log(n) - lo) / (hi - lo) * (x1 - x0)

    def Y(v):
        return y1 - v * (y1 - y0)

    ns = [math.exp(lo + (hi - lo) * i / 80) for i in range(81)]
    upper = [(X(n), Y(min(1.0, typical + band(n)))) for n in ns]
    lower = [(X(n), Y(max(0.0, typical - band(n)))) for n in reversed(ns)]
    p.polygon(upper + lower, T.NEUTRAL, tag="band")
    p.tip("band", f"Where a typical critic ({typical:.0%}) lands 19 times in 20 by luck alone - wide when you share "
                  "a few films, narrow when you share many", highlight=False)
    for v in (0, 0.25, 0.5, 0.75, 1.0):
        p.line([(x0, Y(v)), (x1, Y(v))], T.GRID if v else T.BASELINE, 1)
        p.text(x0 - p.u(5), Y(v), f"{v:.0%}", fs, T.MUTED, "e")
    tick = least
    ticks = []
    while tick <= n_max * 1.1:
        ticks.append(tick)
        tick *= 2
    for n in ticks:
        p.line([(X(n), y0), (X(n), y1)], T.GRID, 1)
        p.text(X(n), y1 + p.u(4) + p.line_height(fs) / 2, str(n), fs, T.MUTED, "center")
    p.text((x0 + x1) / 2, p.height - p.line_height(fs) / 2, "Films you've both judged", fs, T.INK_2, "center")
    p.line([(x0, Y(typical)), (x1, Y(typical))], T.INK_2, 1)
    p.line([(x0, Y(chance)), (x1, Y(chance))], T.BASELINE, 1)
    # dots: everyone else first, then the least in step, and the closest on top
    rank = lambda pt: (pt["id"] in closest, pt["id"] in furthest)
    r = p.u(3.2)
    spots = []
    dots = {}
    for n, pt in enumerate(sorted(pts, key=rank)):
        rng = _jitter(pt["id"], 1)
        agreement = pt["agreed"] / pt["shared"]
        cx = X(pt["shared"]) + rng.uniform(-p.u(2.5), p.u(2.5))
        cy = Y(min(max(agreement + rng.uniform(-0.008, 0.008), 0.0), 1.0))
        color = T.POSITIVE if pt["id"] in closest else T.NEGATIVE if pt["id"] in furthest else T.BASELINE
        tag = f"c{n}"
        dots[pt["id"]] = (cx, cy, color, pt["name"])
        p.circle(cx, cy, r, color, ring=T.SURFACE, ring_width=1.2, tag=tag)
        pub = f" · {pt['publication']}" if pt.get("publication") else ""
        tip = (f"{pt['name']}{pub}\nAgreed with you on {pt['agreed']} of {pt['shared']} films ({agreement:.0%})"
               f"\nMatch {match_text(pt['match'])} vs a typical critic" +
               ("\nClick to see where you agreed" if on_critic is not None else ""))
        spots.append((cx, cy, tag, tip, pt["id"] if on_critic is not None else None))
    # the two lines' words, clear of the dots' busiest part (the right, where few critics share that many films) -
    # and of the chosen critic's mark
    lines = [(Y(typical), f"a typical critic {typical:.0%}", T.INK_2, -1), (Y(chance), f"a coin {chance:.0%}",
                                                                            T.MUTED, 1)]
    mark_critic(p, dots.get(selected), x1, lines)
    if placed is not None:
        placed.update(dots=dots, right=x1, lines=lines, size=(p.width, p.height))
    p.spots(spots, r + p.u(3), on_click=on_critic, noun="critics")


def draw_critic_strip(p, d: dict, on_film=None):
    """The films you share with one critic: your rating (across) in a Fresh row and a Rotten row. Blue: you
    agreed (Fresh on a film you liked, Rotten on one you didn't); red: you didn't. The shaded part is where you
    liked the film. Click a dot for the film's page."""
    rows = d.get("films") or []
    liked_at = d.get("liked_at") or 7.0
    title = f"Your ratings of the {_films(len(rows))} you share"
    if not d.get("comparable", True):
        charts.header(p, 0, 0, p.width, "Your ratings of the films you share")
        charts.message(p, "Not compared yet", "Once there are enough ratings to compare, your ratings of the "
                       "films you share show here.", box=(0, p.u(20), p.width, p.height - p.u(20)))
        return
    if not rows:
        charts.header(p, 0, 0, p.width, "Your ratings of the films you share")
        charts.message(p, "No films in common", "You haven't rated any film they reviewed.",
                       box=(0, p.u(20), p.width, p.height - p.u(20)))
        return
    top = charts.header(p, 0, 0, p.width, title)
    f, fs = p.font(9), p.font(8)
    ly = _dot_legend(p, 0, top + p.u(2), [("You agreed", T.POSITIVE), ("You didn't", T.NEGATIVE)], fs)
    top = ly + p.line_height(fs) / 2 + p.u(8)
    label_w = p.text_width("Rotten", f) + p.u(12)
    x0, x1 = label_w, p.width - p.u(10)
    axis_h = 2 * p.line_height(fs) + p.u(8)
    y1 = p.height - axis_h
    lo_r, hi_r = 0.5, 10.5

    def X(v):
        return x0 + (min(max(v, lo_r), hi_r) - lo_r) / (hi_r - lo_r) * (x1 - x0)

    line = liked_at - (0.5 if liked_at == int(liked_at) else 0.25)
    p.rect(X(line), top, X(hi_r), y1, T.HOVER, tag="liked")
    p.tip("liked", f"You liked it: you rated it {liked_at:g} or more (the middle of your ratings)", highlight=False)
    p.text(X(hi_r) - p.u(4), top + p.line_height(fs) / 2 + p.u(1), "you liked it", fs, T.MUTED, "e")
    band = (y1 - top) / 2
    for i, verdict in enumerate(("Fresh", "Rotten")):
        cy = top + band * (i + 0.5)
        p.text(0, cy, verdict, f, T.INK_2, "w")
        p.line([(x0, cy), (x1, cy)], T.GRID, 1)
    every = 1 if (x1 - x0) / 10 >= p.text_width("10", fs) + p.u(6) else 2
    for v in range(1, 11):
        if v % every == 0 or every == 1:
            p.text(X(v), y1 + p.u(4) + p.line_height(fs) / 2, str(v), fs, T.MUTED, "center")
    p.line([(x0, y1), (x1, y1)], T.BASELINE, 1)
    p.text((x0 + x1) / 2, p.height - p.line_height(fs) / 2, "Your rating (out of 10)", fs, T.INK_2, "center")
    r = p.u(3.6)
    spots = []
    for n, row in enumerate(rows):
        rng = _jitter(row["film_key"], 2)
        i = 0 if row["verdict"] == "Fresh" else 1
        cy = top + band * (i + 0.5) + rng.uniform(-band * 0.3, band * 0.3)
        cx = X(row["your_rating"] + rng.uniform(-0.28, 0.28))
        tag = f"s{n}"
        p.circle(cx, cy, r, T.POSITIVE if row["agreed"] else T.NEGATIVE, ring=T.SURFACE, ring_width=1.2, tag=tag)
        quote = row.get("quote") or ""
        if len(quote) > 110:
            quote = quote[:110].rsplit(" ", 1)[0] + "…"
        tip = (f"{row['label']}\n{row['verdict']} · you rated it {row['your_rating']:g} - "
               f"{'you agreed' if row['agreed'] else 'you disagreed'}" + (f"\n“{quote}”" if quote else "") +
               ("\nClick for the film's page" if on_film is not None else ""))
        spots.append((cx, cy, tag, tip, row["film_key"] if on_film is not None else None))
    p.spots(spots, r + p.u(3), on_click=on_film, noun="films")


def draw_critics_eval(p, answer: dict):
    covered = answer.get("films_covered", 0)
    charts.metric_panels(p, critics_eval_panels(answer), title="How well each one puts the films you rated in order",
                         subtitle=f"The {covered:,} films you rated that one of your closest critics reviewed - "
                                  "each judged by critics picked without it; your closest critics are in blue")
