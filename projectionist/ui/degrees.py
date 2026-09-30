"""The Six Degrees tab: the co-star network of your shelf.

Two people are one step apart when they're in the same film. Everything is worked out from the films you own
(projectionist.costars, through projectionist.ask), in five modes:

    Connect          the chain of your films linking two people ("Kevin Bacon -> Apollo 13 -> Tom Hanks -> ...")
    Person           someone's circle, frequent co-stars, reach and films
    Most connected   who's at the centre of the shelf (a few seconds' work, in the background)
    Bridges          the people linking two parts of the collection - French and US films, two libraries...
    Troupes          groups of actors who keep turning up together

Every request runs on a background thread (app.run): the first search with new options builds a graph of the
whole collection (about half a second for a few thousand films), and a guessed name is matched against every
person.
A newer request of the same kind calls the older one off (app.run's key, 'degrees.connect' and so on), so it
stops working rather than finishing for nothing; results that arrive late all the same - or after the
collection changed - are dropped.

Hundreds of names belong to two or more people (John Smith the actor, and another John Smith who directs), so
whatever picks a person goes by their ID: clicks, the name boxes once they're filled with someone,
'avoid', 'Connect to...' and 'Their films in Watch Next'. Where a name is shared, it's shown with the person's
films ('John Smith (1 film: Harbour Lights)') so the two can be told apart. Someone who mostly directs has the
casts of the films they directed counted, since those are the people they've worked with.

Settings > Six Degrees > 'Roles that count, to start with' (defined here) is where the 'Roles that count' boxes of
Connect, Person, Most connected and Bridges start. A change there sets all four and works out the mode on show
again (the others when they're next looked at); each box still changes its own mode. Troupes keeps its own top-N.
"""

from __future__ import annotations

import re
import time
import tkinter as tk
import tkinter.font as tkfont
from collections import Counter
from tkinter import ttk

from .. import prefs
from . import charts as C
from . import theme as T
from .base import BaseTab
from .widgets import Card, ChartView, LinkLabel, ScrollFrame, SearchBox, Table, suggester

MODES = ("Connect", "Person", "Most connected", "Bridges", "Troupes")
PAD = 16                     # page margin
GAP = 12                     # space between cards
BAR_ROW = 24                 # one row of a bars chart (device-independent px)

# "Only count roles billed in the top N"
BILLING = {"All roles": None, "Top 3 billed": 3, "Top 5 billed": 5, "Top 10 billed": 10}

# Settings > Six Degrees > 'Roles that count, to start with': where the four modes with a 'Roles that count' box
# start (Troupes has its own top-N). Kept as the number of billing places, 0 for all roles.
ROLES = "sixdeg_roles"
ROLE_CHOICES = [(top or 0, label) for label, top in BILLING.items()]
ROLE_MODES = ("Connect", "Person", "Most connected", "Bridges")


def roles_label(value) -> str:
    """The 'Roles that count' box's words for the setting's value: 0 -> 'All roles', 5 -> 'Top 5 billed'."""
    return next((label for top, label in ROLE_CHOICES if top == value), ROLE_CHOICES[0][1])


def parse_roles(value):
    """The setting as it's kept - a number of billing places (0 = all roles) - from that number, its digits or the
    box's words ('Top 5 billed'). None is all roles. Anything else is left for the choices to turn down."""
    if value is None:
        return 0
    if isinstance(value, bool):
        raise ValueError(f"Roles that count: a number of billing places, not {value!r}")
    if isinstance(value, str):
        text = value.strip()
        if text in BILLING:
            return BILLING[text] or 0
        if text.isdigit():
            return int(text)
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


prefs.section("Six Degrees", order=47, hint="Where the Six Degrees tab starts.")
prefs.define(ROLES, "Six Degrees", "Roles that count, to start with", kind="choice", default=0, choices=ROLE_CHOICES,
             parse=parse_roles,
             help="Where Connect, Person, Most connected and Bridges start, and changing it here changes all four. "
                  "Most people on a shelf are bit parts and extras: counting only the top-billed roles makes chains "
                  "go through people you know. Each mode's own box still changes it there; Troupes keeps its own.")
CANDIDATES = ("50", "100", "200")
GROUP_KINDS = {"Library": "library", "Country": "country", "Genre": "genre", "Decade": "decade"}

CIRCLE = 12                  # people in someone's circle
TOP_COSTARS = 15
CENTER_BARS = 30             # at most; as many as fit at about 22 px a row
BRIDGE_BARS = 25
BRIDGE_PEOPLE = 500          # at most, in the Bridges table (ask's limit)
FIT_ROW = 22

CENTER_INTRO = ("For each person: the average number of steps to everyone they can reach through your films. Fewer "
                "steps = better connected. Working it out takes a few seconds - longer for 200 people.")
BRIDGE_INTRO = ("People with films on both sides link two parts of your collection. A film that's on both sides (a "
                "co-production, say) links nothing, so it's left out.")

# Plex uses ISO names for some countries: show the everyday name.
SHORT_COUNTRY = {
    "United States of America": "United States",
    "United Kingdom of Great Britain and Northern Ireland": "United Kingdom",
    "Taiwan, Province of China": "Taiwan",
    "Korea, Republic of": "South Korea",
    "Republic of Korea": "South Korea",
    "Korea, Democratic People's Republic of": "North Korea",
    "Russian Federation": "Russia",
    "Iran, Islamic Republic of": "Iran",
    "Viet Nam": "Vietnam",
    "Hong Kong SAR China": "Hong Kong",
}


# ---------------------------------------------------------------------------------------------------------
# Words and small helpers (plain functions, easy to test)
# ---------------------------------------------------------------------------------------------------------
def plural(n: int, word: str, many: str | None = None) -> str:
    return f"{n:,} {word}" if n == 1 else f"{n:,} {many or word + 's'}"


def film_label(film: dict) -> str:
    """'Rush Hour (1998)' - how the Film, Credits and Watch Next tabs look films up."""
    return f"{film['title']} ({film['year']})" if film.get("year") else str(film.get("title", ""))


def person_label(catalog, person) -> str:
    """A name with enough to tell two people called that apart: 'John Smith (10 films, e.g. Paper Harbour)',
    'John Smith (1 film: Harbour Lights)' (costars.person_label: Watch Next labels people the same way)."""
    from ..costars import person_label as label
    return label(catalog, person)


def short_title(title: str, most: int = 24) -> str:
    """'The Curious Case of Benjamin Button' -> 'The Curious Case of...'."""
    from ..costars import short_title as short
    return short(title, most)


def people_directory(catalog) -> dict:
    """What the name boxes suggest: everyone, the people with the most films first. A name two or more people
    share is listed once for each of them, labelled so they can be told apart ('John Smith (10 films, e.g.
    Paper Harbour)' and 'John Smith (1 film: Harbour Lights)' - costars.namesake_labels, as in Watch Next); 'ids'
    maps those labels to the person. Worked out once per collection (a fifth of a second for tens of thousands of
    people)."""
    key = ("degrees.directory",)
    if key not in catalog.cache:
        from ..costars import namesake_labels, namesakes
        from ..jobs import check
        shared = namesakes(catalog)
        named = namesake_labels(catalog)
        people = sorted(catalog.people.values(), key=lambda p: (-p.film_count, p.name.casefold(), p.id))
        labels, ids = [], {}
        for n, p in enumerate(people):
            if n % 4096 == 0:
                check()                                # (in a background job that's been called off: stop here)
            if not p.name:
                continue
            label = named.get(p.id)
            if label is not None:
                ids[label] = p.id
                labels.append(label)
            else:
                labels.append(p.name)
        catalog.cache[key] = {"labels": labels, "ids": ids, "shared": set(shared)}
    return catalog.cache[key]


_SUFFIX = re.compile(r"(jr|sr|ii|iii|iv|v)\.?", re.IGNORECASE)


def split_names(text: str) -> list[str]:
    """The Avoid box: names separated by commas (or semicolons). A comma inside brackets belongs to a label
    ('John Smith (10 films, e.g. Paper Harbour)'), and 'Jr.' or 'Sr.' after a comma belongs to the name before it
    ('Isaac C. Singleton, Jr.')."""
    parts, depth, current = [], 0, ""
    for ch in str(text or ""):
        if ch in ",;" and depth == 0:
            parts.append(current)
            current = ""
            continue
        if ch == "(":
            depth += 1
        elif ch == ")" and depth:
            depth -= 1
        current += ch
    parts.append(current)
    names = []
    for part in (p.strip() for p in parts):
        if not part:
            continue
        if names and _SUFFIX.fullmatch(part):
            names[-1] = f"{names[-1]}, {part}"
        else:
            names.append(part)
    return names


def default_ids(catalog, reachable=None) -> tuple[str | None, str | None]:
    """A good first example for Connect: the biggest star on the shelf (most films billed in the top 3), and the
    biggest star who's never shared a film with them - so the chain has something to show. `reachable` (the
    people linked to the first one at all) keeps the second one in the same part of the shelf."""
    from ..jobs import check
    lead = Counter()
    for film in catalog.films.values():
        check()                        # (a background job that's been called off stops here; elsewhere, nothing)
        for c in film.cast:
            if c.order <= 3:
                lead[c.person] += 1
    ranked = [pid for pid, _ in lead.most_common(400) if pid in catalog.people]
    if not ranked:
        ranked = [p.id for p in sorted(catalog.people.values(), key=lambda p: -p.film_count)[:400]]
    if not ranked:
        return None, None
    a = ranked[0]
    films_of = lambda pid: set(catalog.people[pid].acted) | catalog.people[pid].directed
    films_a = films_of(a)
    pool = [q for q in ranked[1:] if reachable is None or q in reachable]
    b = next((q for q in pool if not films_of(q) & films_a), pool[0] if pool else None)
    return a, b


def group_values(catalog, kind: str) -> list[tuple[object, int]]:
    """[(value, films)] for one kind of group: libraries, countries and genres by film count (most first),
    decades in order."""
    counts = Counter()
    for f in catalog.films.values():
        if kind == "library":
            counts.update(set(f.libraries))
        elif kind == "country":
            counts.update(set(f.countries))
        elif kind == "genre":
            counts.update(set(f.genres))
        elif kind == "decade" and f.year:
            counts[f.year // 10 * 10] += 1
    if kind == "decade":
        return sorted(counts.items())
    return sorted(counts.items(), key=lambda kv: (-kv[1], str(kv[0]).casefold()))


def group_name(kind: str, value) -> str:
    """How a group reads in labels: 'France', '1980s', 'Documentaries'."""
    if kind == "decade":
        return f"{value}s"
    if kind == "country":
        return SHORT_COUNTRY.get(str(value), str(value))
    return str(value)


def group_choice(kind: str, value, films: int) -> str:
    """How a group reads in its drop-down: 'France  (52 films)'."""
    return f"{group_name(kind, value)}  ({plural(films, 'film')})"


def default_groups(values: dict) -> tuple[tuple[str, object], tuple[str, object]] | None:
    """Two sensible groups to start Bridges with: the two biggest countries (the smaller one on the left - Hong
    Kong vs the United States, say), else the two biggest libraries, genres or decades."""
    for kind in ("country", "library", "genre", "decade"):
        rows = values.get(kind) or []
        if kind == "decade":
            rows = sorted(rows, key=lambda kv: -kv[1])
        if len(rows) >= 2:
            return (kind, rows[1][0]), (kind, rows[0][0])
    return None


def candidate_name(text: str) -> str:
    """'Tom Hanks (12 films)' -> 'Tom Hanks' (the way ask lists other candidates); the same for a label
    person_label() made: 'John Smith (10 films, e.g. Paper Harbour)' -> 'John Smith'."""
    return re.sub(r"\s+\(\d[\d,]* films?(?:[,:].*)?\)(?: #\d+)?$", "", str(text)).strip()


def match_note(matched: dict | None) -> str | None:
    """A line saying how a typed name was matched, when it wasn't exact."""
    if not matched or matched.get("how") in (None, "", "exact"):
        return None
    asked, found = matched.get("asked", ""), matched.get("found", "")
    if matched["how"] == "guess":
        return f"{not_found(asked)} - showing {found}, the closest spelling."
    return f"“{asked}” matched {found}."


def not_found(name: str) -> str:
    """How every tab says someone isn't there: 'No one called “Zed” in your collection'."""
    return f"No one called “{name}” in your collection"


def chain_summary(answer: dict) -> str:
    """'2 steps · 25 equally short chains · found in 81 ms'."""
    n = answer.get("degrees", 0)
    ways = answer.get("shortest_chains", 1) or 1
    chains = "the only chain that short" if ways == 1 else f"{ways:,} equally short chains"
    return f"{plural(n, 'step')} · {chains} · found in {answer.get('searched_ms', 0):,} ms"


def reach_items(counts: list[int]) -> list[dict]:
    return [{"label": plural(d, "step"), "value": n, "key": d,
             "tip": f"{plural(d, 'step')} away\n{plural(n, 'person', 'people')}"
                    + ("\nthey've shared a film with" if d == 1 else "")}
            for d, n in enumerate(counts, 1)]


def readable_role(role: str) -> str:
    """'billed 3' (no character name) -> 'an unnamed role (billed 3)', so the chain reads 'X as an unnamed role'."""
    return f"an unnamed role ({role})" if re.fullmatch(r"billed \d+", role or "") else (role or "")


def readable_steps(steps: list[dict]) -> list[dict]:
    return [dict(s, from_role=readable_role(s.get("from_role", "")), to_role=readable_role(s.get("to_role", "")))
            for s in steps]


def strongest_links(nodes: list[dict], edges: list[dict], per_person: float = 2.5) -> list[dict]:
    """The links a network chart has room for (about two and a half per person, the rule charts.network uses):
    all of the centre's, then the others strongest first - a whole strength at a time, so the lines drawn are
    exactly the pairs with at least so many films together (see links_floor), never an arbitrary few of many
    equal ones. Trimming them here keeps the layout stable and lets the card say what's drawn."""
    centre = next((n["id"] for n in nodes if n.get("center")), None)
    keep = [e for e in edges if centre is not None and centre in (e["a"], e["b"])]
    rest = [e for e in edges if centre is None or centre not in (e["a"], e["b"])]
    room = int(len(nodes) * per_person) - len(keep)
    for weight in sorted({e.get("weight", 1) for e in rest}, reverse=True):
        level = [e for e in rest if e.get("weight", 1) == weight]
        if len(level) > room:
            break
        keep += level
        room -= len(level)
    return keep


def links_floor(nodes: list[dict], edges: list[dict], links: list[dict]) -> tuple[int | None, int]:
    """For strongest_links(): (the fewest films a drawn pair of co-stars shares - None if no such line is drawn,
    how many pairs of co-stars aren't drawn)."""
    centre = next((n["id"] for n in nodes if n.get("center")), None)
    others = lambda es: [e for e in es if centre is None or centre not in (e["a"], e["b"])]
    drawn = others(links)
    return min((e.get("weight", 1) for e in drawn), default=None), len(others(edges)) - len(drawn)


def balanced_links(nodes: list[dict], edges: list[dict], per_person: float = 2.5, each: int = 2) -> list[dict]:
    """For a group where every pair qualifies (a troupe): as many links as the chart has room for, but everyone
    keeps at least `each` of their strongest - so nobody looks like an outsider just because their pairs are all
    at the minimum."""
    budget = int(len(nodes) * per_person)
    ranked = sorted(edges, key=lambda e: -e.get("weight", 1))
    if len(ranked) <= budget:
        return list(ranked)
    count = Counter()
    keep = []
    for e in ranked:
        if len(keep) < budget and (count[e["a"]] < each or count[e["b"]] < each):
            keep.append(e)
            count[e["a"]] += 1
            count[e["b"]] += 1
    for e in ranked:
        if len(keep) >= budget:
            break
        if e not in keep:
            keep.append(e)
    return keep


def rows_that_fit(p, n: int, top: float = 0, row: float = 19, least: int = 5) -> int:
    """How many rows of a bar chart fit the painter's height (so a short window shows fewer, readable rows)."""
    room = int((p.height - p.u(top)) // p.u(row))
    return max(min(n, room), min(n, least))


def spin_value(var, lo: int, hi: int, default: int) -> int:
    try:
        return min(max(int(float(var.get())), lo), hi)
    except (ValueError, tk.TclError):
        return default


def _card_frame(parent) -> ttk.Frame:
    """A frame that sits on a card without drawing a second border."""
    f = ttk.Frame(parent, style="Card.TFrame", padding=0)
    f.configure(relief="flat", borderwidth=0)
    return f


def _styles(style):
    """A few label styles the shared theme doesn't have: links and headings on a card (in the look in use - the
    theme runs this again when the look changes)."""
    style.configure("SixDeg.CardLink.TLabel", background=T.CARD, foreground=T.LINK)
    style.configure("SixDeg.Name.TLabel", background=T.CARD, foreground=T.INK, font=T.font(style, 15, "bold"))
    style.configure("SixDeg.Summary.TLabel", background=T.CARD, foreground=T.INK, font=T.font(style, 11, "bold"))
    style.configure("SixDeg.Section.TLabel", background=T.CARD, foreground=T.INK, font=T.font(style, 9, "bold"))
    style.configure("SixDeg.Note.TLabel", background=T.CARD, foreground=T.INK_2)


def _wrap_to(label, container, margin: int = 0):
    """Let a label wrap to its container's width (fixed wrap lengths get cut off in a narrow window)."""
    def resized(event):
        wrap = max(event.width - margin, 120)
        try:
            if int(str(label.cget("wraplength")) or 0) != wrap:
                label.configure(wraplength=wrap)
        except tk.TclError:
            pass
    container.bind("<Configure>", resized, add="+")


def _connect_work(request: dict):
    """work() for Connect: ask for the chain, then how far the first person reaches (to show where the second
    one sits among everyone they can reach)."""
    def work(catalog):
        from .. import costars
        from ..ask import handle
        answer = handle(request, catalog=catalog)
        steps = answer.get("steps") if answer.get("ok") else None
        if steps:
            graph = costars.graph_for(catalog, request.get("max_billing"), bool(answer.get("include_directors")))
            dist = graph.layers(steps[0]["from_id"], max_depth=12)
            counts = Counter(dist.values())
            reach = [counts[d] for d in range(1, max(counts) + 1)] if len(counts) > 1 else []
            answer = dict(answer, reach_from=reach, true_degrees=dist.get(steps[-1]["to_id"]))
        return answer
    return work


def _example_work(request: dict):
    """work() for Connect's first example: pick two people who are linked (default_ids, checked against the
    graph), then connect them."""
    def work(catalog):
        from .. import costars
        a, _ = default_ids(catalog)
        if a is None:
            return {"ok": False, "error": "nobody to connect", "defaults": (None, None)}
        # the example people are stars of the cast, so 'auto' counts no directors here
        graph = costars.graph_for(catalog, request.get("max_billing"), request.get("include_directors") is True)
        a, b = default_ids(catalog, set(graph.layers(a)))
        if b is None:
            return {"ok": False, "error": "nobody to connect", "defaults": (None, None)}
        names = (catalog.people[a].name, catalog.people[b].name)
        answer = _connect_work(dict(request, **{"from": names[0], "to": names[1], "from_id": a, "to_id": b}))(
            catalog)
        return dict(answer, defaults=names, default_ids=(a, b))
    return work


# ---------------------------------------------------------------------------------------------------------
class Tab(BaseTab):
    title = "Six Degrees"

    def __init__(self, app, notebook):
        super().__init__(app, notebook)
        self.s = T.scale(self.frame)
        T.add_styles(self.frame, _styles)
        self._suggest = None                  # suggest(text) over everyone, most films first (built lazily)
        self._suggest_for = None
        self._tokens: dict[str, int] = {}     # the newest request of each kind
        self._jobs: dict[str, object] = {}    # ...and its job (app.run's), to call it off
        self._busy: dict[str, object] = {}    # kind -> busy(on) for the request running now
        self._primed: set[str] = set()        # modes whose first content has been asked for
        self._stale: set[str] = set()         # modes whose roles Settings changed since their content was asked for
        self._roles = roles_label(prefs.get(app, ROLES))     # where the 'Roles that count' boxes start
        self._pending: dict | None = None     # a navigate() that came before the collection was ready
        self._defaults: tuple = (None, None)
        self._values: dict[str, list] = {}    # bridges: group kind -> [(value, films)]
        self._choices: dict[str, dict] = {"a": {}, "b": {}}
        self._kept_groups: dict | None = None  # bridges: the groups picked before a new text size (restore)
        self.person: dict | None = None       # the profile on show
        self.chain: dict | None = None        # the last Connect answer
        self.center: dict | None = None
        self.bridge: dict | None = None
        self.troupes: dict | None = None
        self._troupe_films: dict = {}
        self._center_started = 0.0
        self._center_asked = None             # (count, billing) of the last Work it out
        self._center_timer = None
        self.timings: dict[str, float] = {}
        # Two people can share a name, so the name boxes remember who they stand for: box -> (its text, person ID)
        # while the text is unchanged; and the Avoid names added by clicking a chain -> person ID.
        self._chosen: dict[str, tuple[str, str]] = {}
        self._avoid_ids: dict[str, str] = {}
        self._directory: dict | None = None   # people_directory(), once it's been built in the background
        # 'Directors count too' is decided for each search (ticked for someone who mostly directs) until it's
        # clicked; after that it's the user's. _quiet is set while the tab ticks it itself.
        self._directors_auto = {"person": True, "connect": True}
        self._quiet = False
        self._person_hint = None              # navigate(directors=True): count them for this profile
        self._build()
        self._show_state()

    # =========================================================================================================
    # Layout
    # =========================================================================================================
    def _build(self):
        f = self.frame
        f.columnconfigure(0, weight=1)
        f.rowconfigure(1, weight=1)
        head = ttk.Frame(f, style="Page.TFrame", padding=(PAD, 12, PAD, 6))
        head.grid(row=0, column=0, sticky="ew")
        head.columnconfigure(1, weight=1)
        ttk.Label(head, text="Six Degrees", style="PageTitle.TLabel").grid(row=0, column=0, sticky="w")
        hint = ttk.Label(head, style="PageHint.TLabel",
                         text="Everyone on your shelf is linked through the films they share: two people are one "
                              "step apart when they're in the same film.")
        hint.grid(row=0, column=1, sticky="w", padx=(14, 0))
        head.bind("<Configure>", lambda e: hint.configure(wraplength=max(e.width - int(170 * self.s), 200)),
                  add="+")

        self.placeholder = ChartView(f, height=320, background=T.PAGE)
        self.placeholder.grid(row=1, column=0, sticky="nsew")
        self.modes = ttk.Notebook(f, style="Inner.TNotebook")      # (smaller tabs than the main ones: theme.py)
        self.modes.grid(row=1, column=0, sticky="nsew", padx=PAD - 4, pady=(2, PAD - 4))
        self.pages = {}
        for name, build in zip(MODES, (self._build_connect, self._build_person, self._build_center,
                                       self._build_bridges, self._build_troupes)):
            page = build(self.modes)
            self.modes.add(page, text=name)
            self.pages[name] = page
        self.modes.bind("<<NotebookTabChanged>>", self._mode_changed, add="+")

    def _suggestions(self, text: str):
        return self._suggest(text) if self._suggest is not None else []

    def _card(self, parent, title=None, hint=None, padding=12) -> Card:
        card = Card(parent, title, hint, padding=padding)
        card.body.columnconfigure(0, weight=1)
        if hint:
            _wrap_to(card.hint_label, card, 2 * padding + 6)
        return card

    def _options(self, parent, var, on_change) -> ttk.Combobox:
        box = ttk.Combobox(parent, textvariable=var, values=list(BILLING), state="readonly", width=13)
        box.bind("<<ComboboxSelected>>", lambda e: on_change(), add="+")
        return box

    def _directors_box(self, parent, mode: str, on_change) -> tuple[tk.BooleanVar, ttk.Checkbutton]:
        """'Directors count too': the tab ticks it for someone who mostly directs, until it's clicked (or set)."""
        var = tk.BooleanVar(value=False)

        def written(*_):
            if not self._quiet:
                self._directors_auto[mode] = False
        trace = var.trace_add("write", written)
        box = ttk.Checkbutton(parent, text="Directors count too", variable=var, style="Page.TCheckbutton",
                              command=on_change)

        def gone(event):
            # (the trace's Tcl command would keep this tab alive for good once it has gone - a new text size builds
            # the tab again and lets this one go)
            if event.widget is box:
                try:
                    var.trace_remove("write", trace)
                except (tk.TclError, ValueError):
                    pass
        box.bind("<Destroy>", gone, add="+")
        return var, box

    def _set_quietly(self, var, value):
        """Set a checkbox from an answer (not the user's choice)."""
        self._quiet = True
        try:
            var.set(bool(value))
        finally:
            self._quiet = False

    @staticmethod
    def _fit_headings(table: Table, cap: int = 150):
        """Widen a table's fixed columns so their headings fit, sort arrow and all (stretching columns give way)."""
        try:
            font = tkfont.Font(font=ttk.Style(table).lookup("Treeview.Heading", "font") or "TkHeadingFont")
        except tk.TclError:
            return
        s = T.scale(table)
        columns = []
        for key, heading, width, anchor in table.columns:
            if anchor != "w":
                need = font.measure(f"{heading} ▼") + int(14 * s)
                width = max(width, min(int(need / s + 0.999), cap))
                table.tree.column(key, width=int(width * s), minwidth=int(min(need, cap * s)))
            columns.append((key, heading, width, anchor))
        table.columns = columns

    # -- Connect ------------------------------------------------------------------------------------------------
    def _build_connect(self, parent):
        page = ScrollFrame(parent, background=T.PAGE, style="Page.TFrame")
        inner = page.inner
        inner.columnconfigure(0, weight=1)
        self.connect_page = page

        bar = ttk.Frame(inner, style="Page.TFrame", padding=(PAD, 14, PAD, 0))
        bar.grid(row=0, column=0, sticky="ew")
        bar.columnconfigure(6, weight=1)
        ttk.Label(bar, text="From", style="Page.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.from_box = SearchBox(bar, self._suggestions, on_pick=self._picked_from, width=32)
        self.from_box.grid(row=0, column=1, sticky="w")
        self.swap_btn = ttk.Button(bar, text="⇄  Swap", style="Small.TButton", command=self.swap)
        self.swap_btn.grid(row=0, column=2, padx=10)
        ttk.Label(bar, text="To", style="Page.TLabel").grid(row=0, column=3, sticky="w", padx=(0, 6))
        self.to_box = SearchBox(bar, self._suggestions, on_pick=lambda _t: self.connect(), width=32)
        self.to_box.grid(row=0, column=4, sticky="w")
        self.connect_btn = ttk.Button(bar, text="Connect", style="Accent.TButton", command=self.connect)
        self.connect_btn.grid(row=0, column=5, padx=(14, 0))

        opts = ttk.Frame(bar, style="Page.TFrame")
        opts.grid(row=1, column=0, columnspan=7, sticky="w", pady=(10, 0))
        ttk.Label(opts, text="Roles that count", style="Page.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.connect_billing = tk.StringVar(value=self._roles)
        self._options(opts, self.connect_billing, self._connect_again).grid(row=0, column=1, sticky="w")
        self.connect_directors, box = self._directors_box(opts, "connect", self._connect_again)
        box.grid(row=0, column=2, sticky="w", padx=(16, 16))
        ttk.Label(opts, text="Avoid", style="Page.TLabel").grid(row=0, column=3, sticky="w", padx=(0, 6))
        self.avoid_var = tk.StringVar()
        self.avoid_entry = ttk.Entry(opts, textvariable=self.avoid_var, width=30)
        self.avoid_entry.grid(row=0, column=4, sticky="w")
        self.avoid_entry.bind("<Return>", lambda e: self.connect(), add="+")
        ttk.Label(opts, text="names, separated by commas", style="PageHint.TLabel").grid(row=0, column=5, sticky="w",
                                                                                         padx=(6, 0))

        card = self._card(inner, padding=16)
        card.grid(row=1, column=0, sticky="ew", padx=PAD, pady=(GAP, PAD))
        body = card.body
        body.columnconfigure(0, weight=3, uniform="connect")
        body.columnconfigure(1, weight=2, uniform="connect")
        self.connect_head = ttk.Label(body, text="", style="SixDeg.Summary.TLabel")
        self.connect_head.grid(row=0, column=0, columnspan=2, sticky="w")
        self.connect_sub = ttk.Label(body, text="", style="CardHint.TLabel", justify="left")
        self.connect_sub.grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 0))
        _wrap_to(self.connect_sub, body, 8)
        self.connect_notes = _card_frame(body)
        self.connect_notes.grid(row=2, column=0, columnspan=2, sticky="ew")
        self.chain_view = ChartView(body, height=220)
        self.chain_view.grid(row=3, column=0, columnspan=2, sticky="new", pady=(12, 0))
        # Beside the chain: everyone the first person reaches, step by step, with the second person's step in blue
        self.far_view = ChartView(body, height=250)
        self.far_view.grid(row=3, column=1, sticky="new", padx=(GAP, 0), pady=(12, 0))
        self._chain_message("Pick two people and click Connect",
                            "Start typing a name - the people with the most films are suggested first.")
        return page

    # -- Person -------------------------------------------------------------------------------------------------
    def _build_person(self, parent):
        page = ScrollFrame(parent, background=T.PAGE, style="Page.TFrame")
        inner = page.inner
        inner.columnconfigure(0, weight=3, uniform="person")
        inner.columnconfigure(1, weight=2, uniform="person")
        self.person_page = page

        bar = ttk.Frame(inner, style="Page.TFrame", padding=(PAD, 14, PAD, 0))
        bar.grid(row=0, column=0, columnspan=2, sticky="ew")
        bar.columnconfigure(6, weight=1)
        ttk.Label(bar, text="Who", style="Page.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.person_box = SearchBox(bar, self._suggestions, on_pick=lambda _t: self.show_typed(), width=32)
        self.person_box.grid(row=0, column=1, sticky="w")
        self.person_btn = ttk.Button(bar, text="Show", style="Accent.TButton", command=self.show_typed)
        self.person_btn.grid(row=0, column=2, padx=(10, 24))
        ttk.Label(bar, text="Roles that count", style="Page.TLabel").grid(row=0, column=3, sticky="w", padx=(0, 6))
        self.person_billing = tk.StringVar(value=self._roles)
        self._options(bar, self.person_billing, self._person_again).grid(row=0, column=4, sticky="w")
        self.person_directors, box = self._directors_box(bar, "person", self._person_again)
        box.grid(row=0, column=5, sticky="w", padx=(16, 0))

        head = self._card(inner, padding=14)
        head.grid(row=1, column=0, columnspan=2, sticky="ew", padx=PAD, pady=(GAP, 0))
        hb = head.body
        self.person_name = ttk.Label(hb, text="", style="SixDeg.Name.TLabel")
        self.person_name.grid(row=0, column=0, sticky="w")
        buttons = _card_frame(hb)
        buttons.grid(row=0, column=1, sticky="e")
        self.person_connect_btn = ttk.Button(buttons, text="Connect to...", command=self.connect_from_person,
                                             state="disabled")
        self.person_connect_btn.grid(row=0, column=0)
        self.person_watch_btn = ttk.Button(buttons, text="Their films in Watch Next", command=self.watch_person,
                                           state="disabled")
        self.person_watch_btn.grid(row=0, column=1, padx=(8, 0))
        self.person_notes = _card_frame(hb)
        self.person_notes.grid(row=1, column=0, columnspan=2, sticky="ew")
        self.person_tiles = ChartView(hb, height=78)
        self.person_tiles.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))

        card = self._card(inner, "Their circle", "The people they've made the most films with, and how often those "
                                                 "people work together - thicker lines, more films. Click anyone to "
                                                 "see their profile.")
        card.grid(row=2, column=0, sticky="nsew", padx=(PAD, GAP // 2), pady=(GAP, 0))
        self.circle_view = ChartView(card.body, height=400)
        self.circle_view.grid(row=0, column=0, sticky="nsew")
        self.circle_note = ttk.Label(card.body, text="", style="CardHint.TLabel")
        self.circle_note.grid(row=1, column=0, sticky="e")

        card = self._card(inner, "Most frequent co-stars", "Films they share. Click a name to see that person.")
        card.grid(row=2, column=1, sticky="nsew", padx=(GAP // 2, PAD), pady=(GAP, 0))
        self.costar_view = ChartView(card.body, height=400)
        self.costar_view.grid(row=0, column=0, sticky="nsew")

        card = self._card(inner, "Their films on your shelf", "Double-click a film to open its page.")
        card.grid(row=3, column=0, sticky="nsew", padx=(PAD, GAP // 2), pady=(GAP, PAD))
        self.person_films = Table(card.body, [("title", "Title", 230, "w"), ("year", "Year", 52, "e"),
                                              ("role", "Role", 190, "w"), ("billed", "Billed", 52, "e")],
                                  height=10, on_open=lambda row: self.open_film(row))
        self._fit_headings(self.person_films)
        self.person_films.grid(row=0, column=0, sticky="nsew")

        card = self._card(inner, "How far they reach", "How many people are 1, 2, 3... steps away - a step is "
                                                       "sharing a film.")
        card.grid(row=3, column=1, sticky="nsew", padx=(GAP // 2, PAD), pady=(GAP, PAD))
        self.reach_view = ChartView(card.body, height=236)
        self.reach_view.grid(row=0, column=0, sticky="nsew")
        self._clear_person("Who would you like to look at?", "Type a name above - or click anyone in another chart.")
        return page

    # -- Most connected -----------------------------------------------------------------------------------------
    def _build_center(self, parent):
        page = ttk.Frame(parent, style="Page.TFrame")
        page.columnconfigure(0, weight=1, uniform="center")
        page.columnconfigure(1, weight=1, uniform="center")
        page.rowconfigure(2, weight=1)

        bar = ttk.Frame(page, style="Page.TFrame", padding=(PAD, 14, PAD, 0))
        bar.grid(row=0, column=0, columnspan=2, sticky="ew")
        # While it works, the progress bar (column 6) gives way when room is short - in the smallest window - so
        # 'Working it out... 12 s' (column 7) is never cut; spare room goes to the empty column 8 (all but a
        # thousandth of it: otherwise the progress bar keeps its length).
        bar.columnconfigure(6, weight=1)
        bar.columnconfigure(8, weight=1000)
        ttk.Label(bar, text="Consider the", style="Page.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.center_count = tk.StringVar(value="100")
        count = ttk.Combobox(bar, textvariable=self.center_count, values=CANDIDATES, state="readonly", width=5)
        count.grid(row=0, column=1, sticky="w")
        count.bind("<<ComboboxSelected>>", lambda e: self._center_again(), add="+")
        ttk.Label(bar, text="people with the most films", style="Page.TLabel").grid(row=0, column=2, sticky="w",
                                                                                     padx=(6, 20))
        ttk.Label(bar, text="Roles that count", style="Page.TLabel").grid(row=0, column=3, sticky="w", padx=(0, 6))
        self.center_billing = tk.StringVar(value=self._roles)
        self._options(bar, self.center_billing, self._center_again).grid(row=0, column=4, sticky="w")
        self.center_btn = ttk.Button(bar, text="Work it out", style="Accent.TButton", command=self.work_out_center)
        self.center_btn.grid(row=0, column=5, padx=(16, 0))
        self.center_progress = ttk.Progressbar(bar, mode="indeterminate", length=int(110 * self.s))
        self.center_progress.grid(row=0, column=6, sticky="ew", padx=(12, 0))
        self.center_progress.grid_remove()
        self.center_wait = ttk.Label(bar, text="", style="PageHint.TLabel")
        self.center_wait.grid(row=0, column=7, sticky="w", padx=(8, 0))
        self.center_bar = bar
        self._center_wrapped = False          # (the progress bar and the clock on a line of their own: see below)
        bar.bind("<Configure>", lambda e: e.widget is bar and self._fit_center_bar(), add="+")

        self.center_note = ttk.Label(page, style="PageHint.TLabel", justify="left", text=CENTER_INTRO)
        self.center_note.grid(row=1, column=0, columnspan=2, sticky="ew", padx=PAD, pady=(8, 0))
        _wrap_to(self.center_note, page, 2 * PAD)

        card = self._card(page, "Best connected", "Average steps to everyone they reach. Click a name to see "
                                                  "their profile.")
        card.grid(row=2, column=0, sticky="nsew", padx=(PAD, GAP // 2), pady=(GAP, PAD))
        card.body.rowconfigure(0, weight=1)
        self.center_view = ChartView(card.body, height=300)
        self.center_view.grid(row=0, column=0, sticky="nsew")

        card = self._card(page, "Everyone considered", "Double-click someone to see their profile.")
        card.grid(row=2, column=1, sticky="nsew", padx=(GAP // 2, PAD), pady=(GAP, PAD))
        card.body.rowconfigure(0, weight=1)
        self.center_table = Table(card.body, [("rank", "#", 34, "e"), ("name", "Name", 170, "w"),
                                              ("avg", "Avg. steps", 72, "e"), ("reaches", "Reaches", 72, "e"),
                                              ("films", "Films", 50, "e")],
                                  height=8, on_open=lambda row: self.open_person(row["id"], row["name"]))
        self._fit_headings(self.center_table)
        self.center_table.grid(row=0, column=0, sticky="nsew")
        self.center_view.clear("Who's at the centre of your shelf?",
                               "Click Work it out to find the people everyone else is closest to.")
        return page

    # -- Bridges ------------------------------------------------------------------------------------------------
    def _build_bridges(self, parent):
        page = ttk.Frame(parent, style="Page.TFrame")
        page.columnconfigure(0, weight=1, uniform="bridges")
        page.columnconfigure(1, weight=1, uniform="bridges")
        page.rowconfigure(2, weight=1)

        bar = ttk.Frame(page, style="Page.TFrame", padding=(PAD, 14, PAD, 0))
        bar.grid(row=0, column=0, columnspan=2, sticky="ew")
        self.group_kind, self.group_value, self.group_value_box = {}, {}, {}
        for n, side in enumerate(("a", "b")):
            col = n * 4
            ttk.Label(bar, text="Between" if side == "a" else "and", style="Page.TLabel").grid(
                row=0, column=col, sticky="w", padx=(0 if side == "a" else 14, 6))
            self.group_kind[side] = tk.StringVar(value="Country")
            kind = ttk.Combobox(bar, textvariable=self.group_kind[side], values=list(GROUP_KINDS), state="readonly",
                                width=8)
            kind.grid(row=0, column=col + 1, sticky="w")
            kind.bind("<<ComboboxSelected>>", lambda e, s=side: self._kind_changed(s), add="+")
            self.group_value[side] = tk.StringVar()
            value = ttk.Combobox(bar, textvariable=self.group_value[side], state="readonly", width=26)
            value.grid(row=0, column=col + 2, sticky="w", padx=(6, 0))
            value.bind("<<ComboboxSelected>>", lambda e: self.find_bridges(), add="+")
            self.group_value_box[side] = value
        opts = ttk.Frame(bar, style="Page.TFrame")
        opts.grid(row=1, column=0, columnspan=8, sticky="w", pady=(10, 0))
        ttk.Label(opts, text="Roles that count", style="Page.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.bridge_billing = tk.StringVar(value=self._roles)
        self._options(opts, self.bridge_billing, self.find_bridges).grid(row=0, column=1, sticky="w")
        self.bridge_btn = ttk.Button(opts, text="Find bridges", style="Accent.TButton", command=self.find_bridges)
        self.bridge_btn.grid(row=0, column=2, padx=(16, 0))

        self.bridge_note = ttk.Label(page, style="PageHint.TLabel", justify="left", text=BRIDGE_INTRO)
        self.bridge_note.grid(row=1, column=0, columnspan=2, sticky="ew", padx=PAD, pady=(8, 0))
        _wrap_to(self.bridge_note, page, 2 * PAD)

        card = self._card(page, "Who links them", "Their films on each side - the people with the most films on "
                                                  "both sides come first (ranked by their smaller side). Click a "
                                                  "name to see their profile.")
        card.grid(row=2, column=0, sticky="nsew", padx=(PAD, GAP // 2), pady=(GAP, PAD))
        card.body.rowconfigure(0, weight=1)
        self.bridge_view = ChartView(card.body, height=300)
        self.bridge_view.grid(row=0, column=0, sticky="nsew")

        card = self._card(page, padding=12)
        card.grid(row=2, column=1, sticky="nsew", padx=(GAP // 2, PAD), pady=(GAP, PAD))
        body = card.body
        body.rowconfigure(1, weight=3)
        body.rowconfigure(3, weight=2)
        self.bridge_people_title = ttk.Label(body, text="Everyone with films on both sides", style="CardTitle.TLabel")
        self.bridge_people_title.grid(row=0, column=0, sticky="w")
        self.bridge_people = Table(body, [("name", "Name", 140, "w"), ("a", "Side 1", 64, "e"),
                                          ("b", "Side 2", 64, "e"), ("examples", "For example", 170, "w")],
                                   height=4, on_open=lambda row: self.open_person(row["id"], row["name"]))
        self._fit_headings(self.bridge_people)
        # In a narrow window the names keep their room and 'For example' gives way (the side columns are as wide
        # as the group names above them, so the two stretching columns share less).
        self.bridge_people.tree.column("name", minwidth=int(120 * self.s))
        self.bridge_people.grid(row=1, column=0, sticky="nsew", pady=(4, 10))
        ttk.Label(body, text="Films with the most of those people", style="CardTitle.TLabel").grid(
            row=2, column=0, sticky="w")
        self.bridge_films = Table(body, [("title", "Title", 200, "w"), ("year", "Year", 52, "e"),
                                         ("linking", "Linking people", 96, "e")],
                                  height=3, on_open=lambda row: self.open_film(row))
        self._fit_headings(self.bridge_films)
        self.bridge_films.grid(row=3, column=0, sticky="nsew", pady=(4, 0))
        self.bridge_view.clear("Pick two parts of your collection", "Then click Find bridges.")
        return page

    # -- Troupes ------------------------------------------------------------------------------------------------
    def _build_troupes(self, parent):
        page = ttk.Frame(parent, style="Page.TFrame")
        page.columnconfigure(0, weight=1, uniform="troupes")
        page.columnconfigure(1, weight=1, uniform="troupes")
        page.rowconfigure(2, weight=1)

        bar = ttk.Frame(page, style="Page.TFrame", padding=(PAD, 14, PAD, 0))
        bar.grid(row=0, column=0, columnspan=2, sticky="ew")
        self.troupe_shared = tk.StringVar(value="5")
        self.troupe_billing = tk.StringVar(value="8")
        self.troupe_size = tk.StringVar(value="3")
        col = 0
        for text, var, lo, hi, after in (("Every pair made at least", self.troupe_shared, 3, 15, "films together"),
                                         ("Roles billed in the top", self.troupe_billing, 3, 12, None),
                                         ("Groups of at least", self.troupe_size, 3, 8, "people")):
            ttk.Label(bar, text=text, style="Page.TLabel").grid(row=0, column=col, sticky="w",
                                                                 padx=(0 if col == 0 else 18, 6))
            spin = ttk.Spinbox(bar, from_=lo, to=hi, textvariable=var, width=4, command=self.find_troupes)
            spin.grid(row=0, column=col + 1, sticky="w")
            spin.bind("<Return>", lambda e: self.find_troupes(), add="+")
            col += 2
            if after:
                ttk.Label(bar, text=after, style="Page.TLabel").grid(row=0, column=col, sticky="w", padx=(6, 0))
                col += 1
        self.troupe_btn = ttk.Button(bar, text="Find troupes", style="Accent.TButton", command=self.find_troupes)
        self.troupe_btn.grid(row=0, column=col, padx=(18, 0))

        self.troupe_note = ttk.Label(page, style="PageHint.TLabel", justify="left",
                                     text="A troupe is a group of actors where every pair has made at least that "
                                          "many films together.")
        self.troupe_note.grid(row=1, column=0, columnspan=2, sticky="ew", padx=PAD, pady=(8, 0))
        _wrap_to(self.troupe_note, page, 2 * PAD)

        card = self._card(page, "Troupes", "Groups that have all been in one film come first, biggest first. "
                                           "Pick one to see it.")
        card.grid(row=2, column=0, sticky="nsew", padx=(PAD, GAP // 2), pady=(GAP, PAD))
        card.body.rowconfigure(0, weight=1)
        self.troupe_table = Table(card.body, [("members", "Members", 260, "w"), ("size", "People", 54, "e"),
                                              ("together", "Films with all", 88, "e"),
                                              ("weakest", "Every pair", 70, "e")],
                                  height=10, on_select=self._troupe_picked)
        self._fit_headings(self.troupe_table)
        self.troupe_table.grid(row=0, column=0, sticky="nsew")

        card = self._card(page, padding=12)
        card.grid(row=2, column=1, sticky="nsew", padx=(GAP // 2, PAD), pady=(GAP, PAD))
        body = card.body
        body.rowconfigure(1, weight=3)            # the network and the film list share the height 3:1
        body.rowconfigure(4, weight=1)
        self.troupe_head = ttk.Label(body, text="", style="CardTitle.TLabel", justify="left")
        self.troupe_head.grid(row=0, column=0, sticky="w")
        _wrap_to(self.troupe_head, body, 4)
        self.troupe_view = ChartView(body, height=130)
        self.troupe_view.grid(row=1, column=0, sticky="nsew", pady=(6, 0))
        self.troupe_link_note = ttk.Label(body, text="", style="CardHint.TLabel")
        self.troupe_link_note.grid(row=2, column=0, sticky="e", pady=(0, 6))
        self.troupe_films_label = ttk.Label(body, text="Films with all of them", style="SixDeg.Section.TLabel")
        self.troupe_films_label.grid(row=3, column=0, sticky="w")
        self.troupe_films = Table(body, [("title", "Title", 220, "w"), ("year", "Year", 52, "e")], height=2,
                                  on_open=lambda row: self.open_film(row))
        self._fit_headings(self.troupe_films)
        self.troupe_films.grid(row=4, column=0, sticky="nsew", pady=(4, 0))
        self.troupe_view.clear("Pick a troupe", "Its members and how often each pair has worked together show here.")
        return page

    # =========================================================================================================
    # Called by the main window
    # =========================================================================================================
    def catalog_changed(self, catalog, state: str):
        super().catalog_changed(catalog, state)
        self._drop_requests()
        self._primed.clear()
        self._stale.clear()
        self._suggest = None
        self._suggest_for = None
        self._defaults = (None, None)
        self._values = {}
        self._kept_groups = None
        self.person = self.chain = self.center = self.bridge = self.troupes = None
        self._troupe_films = {}
        self._chosen, self._avoid_ids, self._directory = {}, {}, None      # IDs belong to the old collection
        self._directors_auto = {"person": True, "connect": True}
        self._set_quietly(self.person_directors, False)
        self._set_quietly(self.connect_directors, False)
        self._person_hint = None
        self._show_state()
        if state == "ready":
            self._reset_views()

    def shown(self):
        if self.state != "ready" or self.catalog is None:
            return
        self._build_suggestions()
        if self._pending is not None:
            pending, self._pending = self._pending, None
            self._apply(pending)
        self._prime(self.current_mode())
        self._again_if_stale()

    def preference_changed(self, key: str, value):
        """'Roles that count, to start with' changed (Settings): every mode's box says it now, and the mode on
        show works itself out again with it - the others when they're next looked at."""
        if key != ROLES:
            return
        label = roles_label(value)
        self._roles = label
        for mode, var in self._role_boxes().items():
            if var.get() != label:
                var.set(label)
                self._stale.add(mode)
        self._again_if_stale()

    def _role_boxes(self) -> dict:
        return {"Connect": self.connect_billing, "Person": self.person_billing, "Most connected": self.center_billing,
                "Bridges": self.bridge_billing}

    def _visible(self) -> bool:
        current = getattr(self.app, "current_tab", None)
        try:
            if callable(current):
                return current() is self
            return self.notebook.select() == str(self.frame)
        except tk.TclError:
            return False

    def _again_if_stale(self):
        """The mode on show, if Settings changed its roles since it was worked out: again, with the new ones (as
        its own box does). A mode with nothing on show yet simply starts with them."""
        mode = self.current_mode()
        if mode not in self._stale or self.state != "ready" or self.catalog is None or not self._visible():
            return
        self._stale.discard(mode)
        if mode == "Connect":
            self._connect_again()
        elif mode == "Person":
            self._person_again()
        elif mode == "Most connected":
            self._center_again()
        elif mode == "Bridges" and "Bridges" in self._primed:
            self.find_bridges()

    def keep(self) -> dict:
        """(Laid out again for a new text size:) the mode on show, the person whose profile it is, what the
        Connect boxes say - and every mode's options: the roles that count, 'Directors count too', the names to
        avoid, how many Most connected considers, the two groups Bridges compares and the troupes' three numbers."""
        person = self.person if isinstance(self.person, dict) else None
        groups = None
        if self._values:                                   # (Bridges has filled its groups: keep the two picked)
            groups = {side: self._group(side) for side in ("a", "b")}
        return {"mode": self.current_mode(), "person": (person.get("id"), person.get("name")) if person else None,
                "from": self.from_box.get(), "to": self.to_box.get(),
                "roles": {mode: var.get() for mode, var in self._role_boxes().items()},
                "directors": {"connect": bool(self.connect_directors.get()),
                              "person": bool(self.person_directors.get())},
                "directors_auto": dict(self._directors_auto),
                "avoid": self.avoid_var.get(), "avoid_ids": dict(self._avoid_ids), "chosen": dict(self._chosen),
                "center_count": self.center_count.get(), "groups": groups, "person_hint": self._person_hint,
                "troupes": (self.troupe_shared.get(), self.troupe_billing.get(), self.troupe_size.get())}

    def _restore_options(self, kept: dict):
        """keep()'s options, on the new tab (the collection is the same one, so the people's IDs still hold)."""
        boxes = self._role_boxes()
        for mode, label in (kept.get("roles") or {}).items():
            if mode in boxes and label in BILLING:
                boxes[mode].set(label)
        for mode, var in (("connect", self.connect_directors), ("person", self.person_directors)):
            if mode in (kept.get("directors") or {}):
                self._set_quietly(var, kept["directors"][mode])
        self._directors_auto.update({k: bool(v) for k, v in (kept.get("directors_auto") or {}).items()
                                     if k in self._directors_auto})
        if kept.get("avoid"):
            self.avoid_var.set(kept["avoid"])
        self._avoid_ids.update(kept.get("avoid_ids") or {})
        self._chosen.update(kept.get("chosen") or {})
        if str(kept.get("center_count", "")) in CANDIDATES:
            self.center_count.set(str(kept["center_count"]))
        if kept.get("groups"):
            self._kept_groups = dict(kept["groups"])      # (set once Bridges has filled its groups: _fill_groups)
        for var, value in zip((self.troupe_shared, self.troupe_billing, self.troupe_size), kept.get("troupes") or ()):
            if str(value).strip():
                var.set(value)

    def restore(self, kept: dict):
        self._restore_options(kept)
        mode = kept.get("mode")
        if mode == "Person" and kept.get("person"):
            pid, name = kept["person"]
            self._pending = {"person_id": pid, "name": name,                        # (opened when shown)
                             "directors": kept.get("person_hint")}
        elif mode == "Connect" and kept.get("from") and kept.get("to"):
            self._pending = {"person_id": None, "name": None, "directors": None,
                             "connect_from": kept["from"], "connect_to": kept["to"]}
        elif mode in self.pages:
            self.select_mode(mode)

    def navigate(self, person_id=None, name=None, connect_from=None, connect_to=None, directors=None, role=None,
                 **_ignored):
        """Open someone's profile (person_id and/or name), or fill in Connect and run it. Someone who mostly
        directs gets the casts of the films they directed counted by themselves; directors=True (or
        role='director') counts them for anyone."""
        request = {"person_id": person_id, "name": name, "connect_from": connect_from, "connect_to": connect_to}
        if not any(request.values()):
            return
        request["directors"] = True if directors or str(role or "").lower() == "director" else None
        if self.state != "ready" or self.catalog is None:
            self._pending = request                 # done once the collection is ready
            return
        self._apply(request)

    def _apply(self, request: dict):
        if request.get("person_id") or request.get("name"):
            self.open_person(request.get("person_id"), request.get("name"), directors=request.get("directors"))
            return
        self._primed.add("Connect")
        self.select_mode("Connect")
        if request.get("connect_from"):
            self._set_box("from", text=request["connect_from"])
        if request.get("connect_to"):
            self._set_box("to", text=request["connect_to"])
        if self.from_box.get() and self.to_box.get():
            self.connect()
        else:
            (self.to_box if self.from_box.get() else self.from_box).entry.focus_set()

    # =========================================================================================================
    # State, modes and background requests
    # =========================================================================================================
    def _show_state(self):
        msg = self.state_message()
        if msg is not None:
            self.modes.grid_remove()
            self.placeholder.grid()
            self.show_message(self.placeholder, *msg)
        else:
            self.placeholder.grid_remove()
            self.modes.grid()

    def _reset_views(self):
        """Calm starting points for every mode (the content comes when a mode is first shown)."""
        self.from_box.set("")
        self.to_box.set("")
        self.avoid_var.set("")
        self._set_connect_text("", "")
        self._clear_links(self.connect_notes)
        self._chain_message("Pick two people and click Connect",
                            "Start typing a name - the people with the most films are suggested first.")
        self.person_box.set("")
        self._clear_person("Who would you like to look at?", "Type a name above - or click anyone in another chart.")
        self.center_view.clear("Who's at the centre of your shelf?",
                               "Click Work it out to find the people everyone else is closest to.")
        self.center_table.set_rows([])
        self.center_note.configure(text=CENTER_INTRO)
        self.bridge_view.clear("Pick two parts of your collection", "Then click Find bridges.")
        self._clear_bridge_lists()
        self.troupe_table.set_rows([])
        self._clear_troupe("Pick a troupe", "Its members and how often each pair has worked together show here.")

    def current_mode(self) -> str:
        try:
            selected = self.modes.select()
        except tk.TclError:
            return MODES[0]
        return next((name for name, page in self.pages.items() if str(page) == selected), MODES[0])

    def select_mode(self, name: str):
        if self.current_mode() != name:
            self.modes.select(self.pages[name])

    def _mode_changed(self, _event=None):
        # (a mode picked while the tab is hidden - its first, as it's made - starts when the tab is shown: shown())
        if self.state == "ready" and self.catalog is not None and self._visible():
            self._prime(self.current_mode())
            self._again_if_stale()

    def _prime(self, mode: str):
        """A mode's first content, asked for the first time it's shown."""
        if mode in self._primed or self.state != "ready" or self.catalog is None:
            return
        self._primed.add(mode)
        self._stale.discard(mode)                   # (asked for with the roles as they are now)
        if mode == "Connect":
            if self.from_box.get() and self.to_box.get():
                self.connect()
            elif not self.from_box.get() and not self.to_box.get():
                self._connect_example()
        elif mode == "Person":
            if self.person is None:
                name, pid = self._typed_person("from")
                if not name:
                    pid = self._first_star()
                if name or pid:
                    self.open_person(pid, name)
        elif mode == "Most connected":
            self.work_out_center()
        elif mode == "Bridges":
            self._fill_groups()
            self.find_bridges()
        elif mode == "Troupes":
            self.find_troupes()

    def _first_star(self) -> str | None:
        """The biggest star on the shelf (their ID) - who Person shows first."""
        if self._defaults[0] is None and self.catalog is not None:
            self._defaults = default_ids(self.catalog)
        return self._defaults[0]

    def _connect_example(self):
        """Connect's first content: two well-known people from the same part of the shelf, connected."""
        request = {"action": "connect", "avoid": [], "max_billing": BILLING.get(self.connect_billing.get()),
                   "include_directors": self._directors_wanted("connect")}

        def done(answer):
            a, b = answer.get("default_ids") or (None, None)
            if self.from_box.get() or self.to_box.get():
                self._set_connect_text("", "")           # the user started typing meanwhile: theirs wins
                self.app.set_status("")
                return
            if a and b:
                self._set_box("from", a)
                self._set_box("to", b)
                self._show_chain(dict(answer, max_billing=request["max_billing"]))   # (the roles it counted)
            else:
                self._chain_message("Pick two people and click Connect",
                                    "Start typing a name - the people with the most films are suggested first.")
                self._set_connect_text("", "")
                self.app.set_status("")
        self._set_connect_text("Finding an example...", "")
        self._start("connect", _example_work(request), done, status="Finding an example chain...",
                    busy=self._button_busy(self.connect_btn))

    def _build_suggestions(self):
        """Name suggestions over everyone (tens of thousands of people take a fifth of a second: done in the
        background)."""
        catalog = self.catalog
        if catalog is None or self._suggest_for is catalog:
            return
        self._suggest_for = catalog

        def work():
            directory = people_directory(catalog)
            return directory, suggester(directory["labels"])

        def done(result):
            if self.catalog is catalog:
                self._directory, self._suggest = result
        self.app.run(work, done, lambda _msg: None, key="degrees.directory")

    def _start(self, kind: str, work, done, status: str | None = None, busy=None):
        """work(catalog) on a background thread, then done(answer) here - unless a newer request of the same
        kind, or another collection, came along meanwhile: that calls this one off (app.run's key), so it stops
        working, and the token drops its answer should it come all the same. busy(True/False) greys out what
        started it - until the newest request of the kind is answered (a request called off never answers)."""
        token = self._tokens[kind] = self._tokens.get(kind, 0) + 1
        catalog = self.catalog
        if busy is not None and kind not in self._busy:
            busy(True)
            self._busy[kind] = busy
        started = time.perf_counter()

        def current() -> bool:
            return self._tokens.get(kind) == token and self.catalog is catalog

        def release():
            was = self._busy.pop(kind, None)
            if was is not None:
                was(False)

        def finished(answer):
            if not current():
                return
            self.timings[kind] = time.perf_counter() - started
            release()
            done(answer if isinstance(answer, dict) else {"ok": False, "error": "no answer"})

        def failed(message):
            if not current():
                return
            release()
            done({"ok": False, "error": message})

        self._jobs[kind] = self.app.run(lambda: work(catalog), finished, failed, status=status,
                                        key=f"degrees.{kind}")

    def _ask(self, request: dict):
        """work() for a plain ask request."""
        def work(catalog):
            from ..ask import handle
            return handle(request, catalog=catalog)
        return work

    def _call_off(self, kind: str):
        """Call off the job of this kind, if it's still running (the main window's run() returns it)."""
        job = self._jobs.pop(kind, None)
        if job is not None and hasattr(job, "cancel"):
            job.cancel()

    def _drop_requests(self):
        for kind in list(self._tokens):
            self._tokens[kind] += 1
        for kind in list(self._jobs):
            self._call_off(kind)
        for busy in list(self._busy.values()):
            busy(False)
        self._busy.clear()

    def _cancel(self, kind: str):
        """Call off the request of this kind that's running (an answer that comes all the same is dropped) and
        free its button."""
        self._tokens[kind] = self._tokens.get(kind, 0) + 1
        self._call_off(kind)
        busy = self._busy.pop(kind, None)
        if busy is not None:
            busy(False)

    @staticmethod
    def _button_busy(button):
        def busy(on):
            try:
                button.state(["disabled"] if on else ["!disabled"])
            except tk.TclError:
                pass
        return busy

    # =========================================================================================================
    # Shared bits
    # =========================================================================================================
    def open_film(self, film: dict):
        """A film's page on the Film tab (its credits scenes, similar films... are there) - by its catalog key when
        we have it (two films can share a title and year)."""
        label = film_label(film)
        if not label:
            return
        extra = {"film_key": film["key"]} if film.get("key") else {}
        if self._goto("Film", title=label, **extra) is None:
            self.app.set_status(f"{label} - the Film tab isn't available.")

    def _goto(self, tab_title: str, **kwargs):
        """app.goto(), which can't pass on a keyword called 'title' while its own first parameter has that name
        (goto(self, title, **kwargs)): then find the tab and call its navigate() here."""
        try:
            return self.app.goto(tab_title, **kwargs)
        except TypeError:
            if "title" not in kwargs:
                raise
        for tab in getattr(self.app, "tabs", []):
            if getattr(tab, "title", None) == tab_title:
                try:
                    self.app.notebook.select(tab.frame)
                except (AttributeError, tk.TclError):
                    pass
                tab.navigate(**kwargs)
                return tab
        return None

    def _clear_links(self, frame):
        for child in frame.winfo_children():
            child.destroy()

    def _link_line(self, frame, lead: str, links):
        """One line: some text, then clickable names separated by dots. links: [(text, command)]. Names that
        would run off the edge go on to the next line instead (a name that's cut off can't be clicked)."""
        try:
            font = tkfont.nametofont("TkDefaultFont")
            if self._visible():                   # (the layout settled first: a page just brought to the front, or
                frame.update_idletasks()          # a card just filled, can be narrower than it's about to be)
            room = frame.winfo_width() if frame.winfo_width() > 100 else int(700 * self.s)
        except tk.TclError:
            font, room = None, 10 ** 6
        measure = (lambda text: font.measure(text)) if font is not None else (lambda text: 0)
        row = _card_frame(frame)
        row.pack(anchor="w", fill="x", pady=(4, 0))
        used = 0
        if lead:
            ttk.Label(row, text=lead, style="SixDeg.Note.TLabel").pack(side="left")
            used = measure(lead)
        first, indent = True, int(6 * self.s) if lead else 0
        for text, command in links:
            width = measure(text)
            step = (indent if first else measure("·") + int(10 * self.s)) + width
            if used and used + step > room:
                row = _card_frame(frame)
                row.pack(anchor="w", fill="x")
                first, indent, used = True, int(24 * self.s), 0
                step = indent + width
            if not first:
                ttk.Label(row, text="·", style="CardHint.TLabel").pack(side="left", padx=5)
            LinkLabel(row, text, command, style="SixDeg.CardLink.TLabel").pack(side="left",
                                                                                padx=(indent if first else 0, 0))
            used += step
            first = False
        return row

    def _person_name(self, pid) -> str:
        person = self.catalog.people.get(pid) if self.catalog is not None and pid else None
        return person.name if person else ""

    # -- telling people with the same name apart ------------------------------------------------------------------
    def _shares_name(self, person) -> bool:
        if self._directory is not None:
            return person.name in self._directory["shared"]
        from ..costars import namesakes
        return person.name in namesakes(self.catalog)

    def _label(self, pid, always: bool = False) -> str:
        """How a person reads in a name box or a link: their name, with their films when someone else has the
        same name ('John Smith (1 film: Harbour Lights)')."""
        person = self.catalog.people.get(pid) if self.catalog is not None and pid else None
        if person is None:
            return ""
        if always or self._shares_name(person):
            from ..costars import namesake_labels
            return namesake_labels(self.catalog).get(pid) or person_label(self.catalog, person)
        return person.name

    def _labels(self, ids) -> list[tuple[str, str]]:
        """[(pid, label)] for links: labelled when the name is shared - or when two of the links read the same."""
        ids = [pid for pid in ids if self.catalog is not None and pid in self.catalog.people]
        names = Counter(self.catalog.people[pid].name for pid in ids)
        return [(pid, self._label(pid, always=names[self.catalog.people[pid].name] > 1)) for pid in ids]

    def _boxes(self) -> dict:
        return {"from": self.from_box, "to": self.to_box, "person": self.person_box}

    def _set_box(self, side: str, pid=None, text: str = ""):
        """Fill a name box with a person (remembered by ID while the text stays the same) or with typed text."""
        box = self._boxes()[side]
        if pid and self.catalog is not None and pid in self.catalog.people:
            text = self._label(pid)
            self._chosen[side] = (text, pid)
        else:
            self._chosen.pop(side, None)
        box.set(text)

    def _typed_person(self, side: str) -> tuple[str, str | None]:
        """(name, person ID or None) for what a name box says: the person it was filled with, a suggestion picked
        from the list, or else just the name typed (ask looks that up)."""
        text = self._boxes()[side].get()
        people = self.catalog.people if self.catalog is not None else {}
        chosen = self._chosen.get(side)
        pid = chosen[1] if chosen and chosen[0] == text else None
        if pid is None and self._directory is not None:
            pid = self._directory["ids"].get(text)
        if pid in people:
            return people[pid].name, pid
        return candidate_name(text), None

    def _namesake_line(self, frame, matched: dict, what: str, pick):
        """'There are 2 people called John Smith - this is the one in Paper Harbour (10 films). Or: John Smith (1 film:
        Harbour Lights)': when the person shown shares their name. pick(pid) switches to another of them."""
        people = self.catalog.people if self.catalog is not None else {}
        person = people.get(matched.get("id"))
        if person is None:
            return
        same = [pid for pid in matched.get("other_ids") or [] if pid in people and people[pid].name == person.name]
        if not same:
            return
        from ..costars import known_for, namesakes
        total = max(len(namesakes(self.catalog).get(person.name, [])), len(same) + 1)
        film = known_for(self.catalog, person)           # (the film their label names, in every tab)
        which = (f"the one in {short_title(film.title)} ({plural(person.film_count, 'film')})" if film is not None
                 else f"the one with {plural(person.film_count, 'film')}")
        self._link_line(frame, f"There are {total} people called {person.name} - {what} {which}. Or:",
                        [(label, lambda pid=pid: pick(pid)) for pid, label in self._labels(same)[:4]])

    # =========================================================================================================
    # Connect
    # =========================================================================================================
    def _picked_from(self, _text):
        if self.to_box.get():
            self.connect()
        else:
            self.to_box.entry.focus_set()

    def swap(self):
        a, b = self.from_box.get(), self.to_box.get()
        chosen = self._chosen.pop("from", None), self._chosen.pop("to", None)
        self.from_box.set(b)
        self.to_box.set(a)
        for side, pick in zip(("to", "from"), chosen):          # the people go with their names
            if pick:
                self._chosen[side] = pick
        if a and b and self.chain is not None:
            self.connect()

    def _connect_again(self):
        """An option changed: search again if there's something on show."""
        if self.chain is not None and self.from_box.get() and self.to_box.get():
            self.connect()

    def _directors_wanted(self, mode: str):
        """What to ask for 'Directors count too': the box's value once it's been clicked, else 'auto' (counted when
        someone mostly directs)."""
        var = self.person_directors if mode == "person" else self.connect_directors
        return "auto" if self._directors_auto[mode] else bool(var.get())

    def _avoid_names(self) -> list[str]:
        return split_names(self.avoid_var.get())

    def _avoid_request(self) -> tuple[list[str], list[str]]:
        """(names, IDs) to avoid: people added by clicking a chain (or picked from a list) go by ID - two people
        can share a name - and typed names by name."""
        people = self.catalog.people if self.catalog is not None else {}
        labels = (self._directory or {}).get("ids", {})
        names, ids = [], []
        for text in self._avoid_names():
            pid = self._avoid_ids.get(text) or labels.get(text)
            if pid in people:
                ids.append(pid)
            else:
                names.append(candidate_name(text))
        return names, ids

    def connect(self):
        if self.state != "ready" or self.catalog is None:
            return
        self._primed.add("Connect")
        self._stale.discard("Connect")
        self.select_mode("Connect")
        (a, a_id), (b, b_id) = self._typed_person("from"), self._typed_person("to")
        if not a or not b:
            self._set_connect_text("Pick two people", "Type a name in From and in To - suggestions appear as you "
                                                      "type.")
            self._clear_links(self.connect_notes)
            self._chain_message("No chain to show yet", "Pick someone in both boxes.")
            (self.to_box if a else self.from_box).entry.focus_set()
            return
        avoid, avoid_ids = self._avoid_request()
        billing = BILLING.get(self.connect_billing.get())
        request = {"action": "connect", "from": a, "to": b, "avoid": avoid, "avoid_ids": avoid_ids,
                   "max_billing": billing, "include_directors": self._directors_wanted("connect")}
        request.update({k: v for k, v in (("from_id", a_id), ("to_id", b_id)) if v})
        texts = {"from": self.from_box.get(), "to": self.to_box.get()}

        def done(answer):
            if answer.get("ok"):
                # the boxes now stand for the people found (while their text is the same), so a changed option
                # searches from the same people - unless the name was only a guess or part of one: that's looked
                # up the same way again, and keeps its note ('No one is called ... - showing ...')
                for side, matched in zip(("from", "to"), answer.get("matched") or [{}, {}]):
                    if (answer.get(f"{side}_id") and self._boxes()[side].get() == texts[side]
                            and (matched or {}).get("how") in (None, "", "exact")):
                        self._chosen[side] = (texts[side], answer[f"{side}_id"])
            self._show_chain(dict(answer, max_billing=billing))
        self._set_connect_text("Looking for the shortest chain...", "")
        self._start("connect", _connect_work(request), done, status=f"Connecting {a} and {b}...",
                    busy=self._button_busy(self.connect_btn))

    def _chain_message(self, text: str, sub: str):
        """A message where the chain goes (and nothing beside it)."""
        self.far_view.grid_remove()
        self.chain_view.grid_configure(columnspan=2)
        self.chain_view.show(lambda p: C.message(p, text, sub), height=160)

    def _set_connect_text(self, head: str, sub: str):
        self.connect_head.configure(text=head)
        self.connect_sub.configure(text=sub)
        if sub:
            self.connect_sub.grid()
        else:
            self.connect_sub.grid_remove()

    def _show_chain(self, answer: dict):
        self.chain = answer
        self._clear_links(self.connect_notes)
        if not answer.get("ok"):
            self._connect_problem(answer)
            return
        if "include_directors" in answer:
            self._set_quietly(self.connect_directors, answer["include_directors"])
        self._connect_notes(answer)
        a, b = answer.get("from", ""), answer.get("to", "")
        if not answer.get("connected"):
            self._not_connected(answer, a, b)
            return
        steps = readable_steps(answer.get("steps") or [])
        if not steps:
            self._set_connect_text("That's the same person", "Pick two different people.")
            self._chain_message(a, "Zero steps - they're the same person.")
            self.app.set_status(f"Six Degrees: {a} is in both From and To - pick two different people.")
            return
        self._set_connect_text(f"{a} to {b}:  " + chain_summary(answer),
                               "Of the equally short chains, this one goes through the biggest roles. Click a "
                               "person to see their profile, or a film to open its page.")
        middle = []
        for s in steps[:-1]:
            if s.get("to_id") and s["to_id"] not in middle:
                middle.append(s["to_id"])
        if middle:
            self._link_line(self.connect_notes, "Find another way round: avoid",
                            [(label, lambda pid=pid: self._avoid(pid)) for pid, label in self._labels(middle[:4])])
        height = C.chain_height(1, len(steps)) - 40 + 6        # no title on this chart: the card says it
        self.chain_view.show(lambda p: C.chain(p, steps, on_person=self._chain_person, on_film=self.open_film),
                             height=height)
        self._show_far(answer, a, b)
        self.connect_page.to_top()
        self.app.set_status(f"Six Degrees: {a} to {b} in {plural(answer.get('degrees', 0), 'step')}.")

    def _connect_notes(self, answer: dict):
        """The lines under the heading: how typed names were matched (and the other people they could mean),
        avoided names that were only a guess, and why directors count."""
        for side, matched in zip(("from", "to"), answer.get("matched") or []):
            pick = lambda pid, s=side: self._replace_name(s, pid)
            note = match_note(matched)
            if note:
                self._link_line(self.connect_notes, note, [])
                others = self._labels((matched.get("other_ids") or [])[:3])
                if others:
                    self._link_line(self.connect_notes, "    Did you mean",
                                    [(label, lambda pid=pid, pick=pick: pick(pid)) for pid, label in others])
            else:
                self._namesake_line(self.connect_notes, matched,
                                    "this chain starts with" if side == "from" else "this chain ends with", pick)
        for avoided in answer.get("avoided") or []:
            if avoided.get("how") not in (None, "", "exact"):
                self._link_line(self.connect_notes, f"Avoiding {avoided.get('found', '')}, the closest match to "
                                                    f"“{avoided.get('asked', '')}”.", [])
        if answer.get("include_directors") and answer.get("directors_auto") and self.catalog is not None:
            from ..costars import mostly_directs
            who = [p.name for p in (self.catalog.people.get(answer.get(k)) for k in ("from_id", "to_id"))
                   if p is not None and mostly_directs(p)]
            if who:
                self._link_line(self.connect_notes, f"Directors count too, since {' and '.join(who)} mostly "
                                                    f"direct{'s' if len(who) == 1 else ''}.", [])

    def _not_connected(self, answer: dict, a: str, b: str):
        avoided = bool(answer.get("avoided"))
        limited = answer.get("max_billing") is not None
        directors = bool(answer.get("include_directors"))
        people = self.catalog.people if self.catalog is not None else {}
        # someone in no cast at all can only be linked through the films they directed
        only_directing = [] if directors else [
            p.name for p in (people.get(answer.get("from_id")), people.get(answer.get("to_id")))
            if p is not None and p.directed and not p.acted]
        tips = ["tick Directors count too"] if only_directing else []
        if avoided:
            tips.append("avoid fewer people")
        if limited:
            tips.append("count all roles")
        if not directors and not only_directing:
            tips.append("tick Directors count too")
        self._set_connect_text(f"{a} and {b} aren't linked by your films",
                               "No chain of films on your shelf gets from one to the other"
                               + (" with those people avoided" if avoided else "") + "."
                               + (f" Try to {', or '.join(tips)}." if tips else ""))
        if only_directing:
            names = " and ".join(only_directing)
            self._chain_message(f"{names} only direct{'s' if len(only_directing) == 1 else ''} here",
                                "They aren't in the cast of any of your films, so only the films they directed can "
                                "link them.")
            self._link_line(self.connect_notes, "", [("Count directors too", self._count_connect_directors)])
        else:
            self._chain_message("Not connected", "They're in separate corners of your shelf.")
        self.app.set_status(f"Six Degrees: {a} and {b} aren't linked by your films.")

    def _count_connect_directors(self):
        self.connect_directors.set(True)          # as good as ticking the box
        self.connect()

    def _show_far(self, answer: dict, a: str, b: str):
        """Beside the chain: how many people are 1, 2, 3... steps from the first person, with the second
        person's step in blue - is two steps unusually close, or what everyone is?"""
        reach = answer.get("reach_from") or []
        k = answer.get("true_degrees")
        if not reach or not k or k > len(reach):
            self.far_view.grid_remove()
            self.chain_view.grid_configure(columnspan=2)
            return
        items = reach_items(reach)
        others = reach[k - 1]
        closer = sum(reach[:k - 1])
        if k == answer.get("degrees"):
            subtitle = (f"{b} is one of {others:,} people {plural(k, 'step')} away"
                        + (f"; {closer:,} are closer." if closer else "."))
        else:
            subtitle = (f"Without avoiding anyone, {b} is {plural(k, 'step')} away - one of {others:,} people "
                        "that far.")
        title = f"Everyone {a} reaches"
        self.chain_view.grid_configure(columnspan=1)
        self.far_view.grid()
        self.far_view.show(lambda p: C.columns(p, items, title=p.fit(title, p.font(10, "bold"), p.width),
                                               subtitle=subtitle, value_fmt=C.compact, label_values="all",
                                               emphasis={k}),
                           height=250)

    def _connect_problem(self, answer: dict):
        error = str(answer.get("error", "Something went wrong"))
        m = re.match(r"no one matching '(.*)'$", error)
        if m:
            asked = m.group(1)
            self._set_connect_text(not_found(asked), "Check the spelling - suggestions appear as you type.")
            side = answer.get("side") or self._side_of(asked)
            links = [(label, lambda pid=pid: self._replace_name(side, pid, asked))
                     for pid, label in self._labels((answer.get("suggestion_ids") or [])[:5])]
            if links:
                self._link_line(self.connect_notes, "Did you mean", links)
            self._chain_message("No chain to show", "Everyone in From, To and Avoid needs to be in your collection.")
            self.app.set_status(not_found(asked) + ".")
        else:
            self._set_connect_text("Couldn't find a chain", error)
            self._chain_message("No chain to show", "Try again, or try other names.")
            self.app.set_status("Six Degrees: couldn't find a chain.")

    def _side_of(self, asked: str) -> str:
        for side in ("from", "to"):
            if asked in (self._boxes()[side].get(), self._typed_person(side)[0]):
                return side
        return "avoid"

    def _replace_name(self, side: str, pid, asked: str | None = None):
        """A 'Did you mean' (or 'Or:') link: put that person in From, To or Avoid, and search again."""
        if side in ("from", "to"):
            self._set_box(side, pid)
        else:
            label = self._label(pid)
            names = [label if asked is not None and candidate_name(n) == asked else n for n in self._avoid_names()]
            if label not in names:
                names.append(label)
            self._avoid_ids[label] = pid
            self.avoid_var.set(", ".join(names))
        self.connect()

    def _avoid(self, pid):
        """'Find another way round: avoid X' - by ID, so it's the X in this chain even when two share the name."""
        label = self._label(pid)
        if not label:
            return
        names = self._avoid_names()
        if label not in names:
            names.append(label)
        self._avoid_ids[label] = pid
        self.avoid_var.set(", ".join(names))
        self.connect()

    def _chain_person(self, pid, name):
        self.open_person(pid, name)

    # =========================================================================================================
    # Person
    # =========================================================================================================
    def open_person(self, pid=None, name=None, directors=None):
        """Show someone's profile: by ID when we have one (two people can share a name), else by name. Someone
        who mostly directs gets the casts of the films they directed counted (unless 'Directors count too' has
        been clicked); directors=True counts them anyway."""
        if self.state != "ready" or self.catalog is None:
            self._pending = {"person_id": pid, "name": name, "directors": directors}
            return
        self._primed.add("Person")
        self._stale.discard("Person")
        self.select_mode("Person")
        if pid and pid not in self.catalog.people:
            pid = None
        name = (self._person_name(pid) if pid else "") or candidate_name((name or "").strip())
        if not name:
            self.person_box.entry.focus_set()
            return
        self._set_box("person", pid, name)
        self._person_hint = directors
        request = {"action": "person", "name": name, "max_billing": BILLING.get(self.person_billing.get()),
                   "include_directors": True if directors else self._directors_wanted("person"),
                   "count": TOP_COSTARS, "circle": CIRCLE}
        if pid:
            request["id"] = pid
        self._start("person", self._ask(request), self._show_person, status=f"Looking up {name}...",
                    busy=self._button_busy(self.person_btn))

    def show_typed(self):
        """Who's in the Who box (typed, or picked from the suggestions)."""
        name, pid = self._typed_person("person")
        self.open_person(pid, name)

    def _person_again(self):
        if self.person is not None:
            hint = self._person_hint if self._directors_auto["person"] else None
            self.open_person(self.person.get("id"), self.person.get("name"), directors=hint)

    def connect_from_person(self):
        """'Connect to...': Connect with this person (by ID) in From, ready for the To box."""
        if self.person is None:
            return
        pid, name = self.person["id"], self.person["name"]
        self._primed.add("Connect")
        to_name, to_id = self._typed_person("to")
        if to_id == pid or (to_id is None and to_name == name) or self._typed_person("from")[1] == pid:
            self._set_box("to", text="")
        self._set_box("from", pid)
        self.select_mode("Connect")
        self.to_box.entry.focus_set()
        self._set_connect_text(f"Connect {name} to...", "")
        self._clear_links(self.connect_notes)
        self._chain_message("Who should they be connected to?", "Type a name in To and press Enter.")

    def watch_person(self):
        if self.person is not None:
            if self._goto("Watch Next", person=self.person["name"], person_id=self.person["id"]) is None:
                self.app.set_status("The Watch Next tab isn't available.")

    def _clear_person(self, text: str, sub: str):
        self.person_name.configure(text="")
        self._clear_links(self.person_notes)
        for view in (self.person_tiles, self.circle_view, self.costar_view, self.reach_view):
            view.clear()
        self.circle_view.clear(text, sub)
        self.circle_note.configure(text="")
        self.person_films.set_rows([])
        for b in (self.person_connect_btn, self.person_watch_btn):
            b.state(["disabled"])

    def _show_person(self, answer: dict):
        if not answer.get("ok"):
            self.person = None
            error = str(answer.get("error", ""))
            m = re.match(r"no one matching '(.*)'$", error)
            if m:
                self._clear_person(not_found(m.group(1)), "Check the spelling - suggestions appear as you type.")
                links = [(label, lambda pid=pid: self.open_person(pid))
                         for pid, label in self._labels((answer.get("suggestion_ids") or [])[:5])]
                if links:
                    self._link_line(self.person_notes, "Did you mean", links)
                self.app.set_status(not_found(m.group(1)) + ".")
            else:
                self._clear_person("Couldn't show that person", error)
                self.app.set_status("Six Degrees: couldn't show that person.")
            return
        self.person = answer
        pid, name = answer["id"], answer["name"]
        if "include_directors" in answer:
            self._set_quietly(self.person_directors, answer["include_directors"])
        self._set_box("person", pid, name)
        self.person_name.configure(text=name)
        for b in (self.person_connect_btn, self.person_watch_btn):
            b.state(["!disabled"])
        self._person_notes(answer)

        # Headline numbers
        reach = answer.get("people_within_steps") or []
        top = (answer.get("top_costars") or [None])[0]
        directed = answer.get("directed") or []
        tiles = [{"label": "Films on your shelf", "value": f"{answer.get('films', 0):,}",
                  "note": f"directed {len(directed)}" if directed else "in the cast"},
                 {"label": "Different co-stars", "value": f"{answer.get('distinct_costars', 0):,}",
                  "note": "people they've shared a film with"},
                 {"label": "People within reach", "value": f"{sum(reach):,}",
                  "note": f"in up to {plural(len(reach), 'step')}" if reach else "nobody else"}]
        if top:
            tiles.append({"label": "Most films with", "value": top["name"],
                          "note": plural(top["shared_films"], "film") + " together"})
        self.person_tiles.show(lambda p: C.tiles(p, tiles))

        # Their circle
        costar_films = {c["id"]: c["shared_films"] for c in answer.get("top_costars") or []}
        circle = answer.get("circle") or {}
        nodes = []
        for n in circle.get("nodes") or []:
            if n.get("center"):
                tip = f"{n['name']}\n{plural(n.get('films', 0), 'film')} on your shelf"
            else:
                shared = costar_films.get(n["id"])
                tip = (f"{n['name']}\n" + (f"{plural(shared, 'film')} with {name} · " if shared else "")
                       + f"{plural(n.get('films', 0), 'film')} in all\nClick to see their profile")
            nodes.append({"id": n["id"], "name": n["name"], "weight": n.get("films", 1),
                          "center": bool(n.get("center")), "tip": tip})
        edges = [{"a": e["a"], "b": e["b"], "weight": e.get("shared_films", 1)} for e in circle.get("edges") or []]
        if len(nodes) > 1:
            links = strongest_links(nodes, edges)
            positions = C.layout(nodes, links)
            self.circle_view.show(lambda p: C.network(p, nodes, links, on_node=self._node_clicked,
                                                      positions=positions, max_links=None))
            floor, hidden = links_floor(nodes, edges, links)
            if not hidden:
                note = ""
            elif floor:
                note = f"Lines between their co-stars: the pairs with {plural(floor, 'film')} or more together."
            else:
                note = f"Only their own lines are drawn - the {hidden:,} between their co-stars are too many to show."
            self.circle_note.configure(text=note)
        else:
            self.circle_view.clear("No co-stars on your shelf", self._widen_hint())
            self.circle_note.configure(text="")

        # Most frequent co-stars
        items = [{"label": c["name"], "value": c["shared_films"], "key": c["id"],
                  "tip": f"{c['name']}\n{plural(c['shared_films'], 'film')} with {name}\nClick to see their profile"}
                 for c in answer.get("top_costars") or []]
        if items:
            self.costar_view.show(lambda p: C.bars(p, items, on_click=self._costar_clicked),
                                  height=max(len(items), 6) * BAR_ROW + 12)
        else:
            self.costar_view.clear("No co-stars on your shelf", self._widen_hint())

        # How far they reach
        reach_bars = reach_items(reach)
        if reach_bars:
            self.reach_view.show(lambda p: C.columns(p, reach_bars, value_fmt=C.compact, label_values="all"))
        else:
            self.reach_view.clear("They don't reach anyone", self._widen_hint())

        # Their films
        rows = {}
        for f in answer.get("acted_in") or []:
            rows[f.get("key") or (f["title"], f.get("year"))] = {
                "title": f["title"], "year": f.get("year"), "role": f.get("role") or "-", "billed": f.get("billed"),
                "key": f.get("key")}
        for f in directed:
            key = f.get("key") or (f["title"], f.get("year"))
            if key in rows:
                role = rows[key]["role"]
                rows[key]["role"] = "Director" + (f"; also plays {role}" if role and role != "-" else "")
            else:
                rows[key] = {"title": f["title"], "year": f.get("year"), "role": "Director", "billed": None,
                             "key": f.get("key")}
        self.person_films.set_rows(list(rows.values()))
        if self.person_films.sorted_by is None:
            self.person_films.sort("year", False)
        self.person_page.to_top()
        self.app.set_status(f"Six Degrees: {name} - {plural(answer.get('films', 0), 'film')}, "
                            f"{plural(answer.get('distinct_costars', 0), 'co-star')} on your shelf.")

    def _widen_hint(self) -> str:
        if BILLING.get(self.person_billing.get()) is not None:
            return "Try counting all roles, not just the top-billed ones."
        if not self.person_directors.get() and self.person and self.person.get("directed"):
            return "Tick Directors count too to include the casts of the films they directed."
        return "Nobody else on your shelf shares a film with them."

    def _person_notes(self, answer: dict):
        self._clear_links(self.person_notes)
        matched = answer.get("matched") or {}
        note = match_note(matched)
        if note:
            others = self._labels((matched.get("other_ids") or [])[:3])
            self._link_line(self.person_notes, note + (" Did you mean" if others else ""),
                            [(label, lambda pid=pid: self.open_person(pid)) for pid, label in others])
        else:
            self._namesake_line(self.person_notes, matched, "this is", self.open_person)
        directed = answer.get("directed") or []
        acted = answer.get("acted_in") or []
        counted = bool(answer.get("include_directors", self.person_directors.get()))
        if directed and counted and (self._directors_auto["person"] or self._person_hint):
            films = plural(len(directed), "film")
            if acted:
                self._link_line(self.person_notes, f"Counting the casts of the {films} they directed, as well as "
                                                   f"the {plural(len(acted), 'film')} they act in.",
                                [("Only count the films they act in", self._act_only)])
            else:
                self._link_line(self.person_notes, f"They only direct here, so their co-stars are the casts of the "
                                                   f"{films} they directed.", [])
        elif directed and not counted:
            self._link_line(self.person_notes, f"They directed {plural(len(directed), 'film')} here, but only "
                                               f"films they act in count now.",
                            [("Count the films they directed too", self._count_directors)])

    def _count_directors(self):
        self.person_directors.set(True)          # as good as ticking the box
        self._person_again()

    def _act_only(self):
        self.person_directors.set(False)         # as good as unticking the box
        self._person_again()

    def _node_clicked(self, pid, name):
        if self.person is not None and pid == self.person.get("id"):
            return
        self.open_person(pid, name)

    def _costar_clicked(self, pid):
        self.open_person(pid)

    # =========================================================================================================
    # Most connected
    # =========================================================================================================
    def work_out_center(self):
        if self.state != "ready" or self.catalog is None:
            return
        self._primed.add("Most connected")
        self._stale.discard("Most connected")
        count = spin_value(self.center_count, 5, 500, 100)
        billing = BILLING.get(self.center_billing.get())
        request = {"action": "center", "candidates": count, "count": count, "max_billing": billing}
        self.center_view.clear("Working it out...", f"Searching out from each of the {count} people with the most "
                                                    "films - a few seconds.")
        self._center_started = time.perf_counter()          # (the clock starts again if the options change)
        self._center_asked = (count, billing)
        self._start("center", self._ask(request), lambda answer: self._show_center(answer, count, billing),
                    status=f"Finding the best-connected of {count} people...", busy=self._center_busy)

    def _center_again(self):
        """An option changed: work it out again, as the other modes do (a setting already worked out comes back
        at once) - unless that's what's being worked out now."""
        asked = (spin_value(self.center_count, 5, 500, 100), BILLING.get(self.center_billing.get()))
        if "Most connected" in self._primed and not ("center" in self._busy and self._center_asked == asked):
            self.work_out_center()

    def _center_busy(self, on: bool):
        self._button_busy(self.center_btn)(on)
        if self._center_timer is not None:
            try:
                self.frame.after_cancel(self._center_timer)
            except tk.TclError:
                pass
            self._center_timer = None
        try:
            if on:
                self._center_started = time.perf_counter()
                self.center_progress.grid()
                self.center_progress.start(15)
                self._center_tick()
            else:
                self.center_progress.stop()
                self.center_progress.grid_remove()
                self.center_wait.configure(text="")
        except tk.TclError:
            pass

    def _center_tick(self):
        seconds = time.perf_counter() - self._center_started
        self.center_wait.configure(text=f"Working it out... {seconds:.0f} s")
        self._fit_center_bar()
        self._center_timer = self.frame.after(500, self._center_tick)

    # Where the progress bar and the clock go: in the row (the progress bar giving way first when room is short),
    # or - when even that isn't enough - on a line of their own under it
    CENTER_BUSY = {False: ({"row": 0, "column": 6, "columnspan": 1, "sticky": "ew", "padx": (12, 0), "pady": 0},
                           {"row": 0, "column": 7, "columnspan": 1, "sticky": "w", "padx": (8, 0), "pady": 0}),
                   True: ({"row": 1, "column": 0, "columnspan": 3, "sticky": "w", "padx": 0, "pady": (6, 0)},
                          {"row": 1, "column": 3, "columnspan": 5, "sticky": "w", "padx": (8, 0), "pady": (6, 0)})}
    CENTER_LEAST_PROGRESS = 40            # px (device-independent) the progress bar keeps in the row

    def _fit_center_bar(self):
        """Most connected's bar in a narrow window: while it works, the progress bar gives way to keep 'Working
        it out... 12 s' whole. Where the fonts are wider than Windows' Segoe UI - Linux's usually are - that may
        not be enough in the smallest window: then the progress bar and the clock go on a line of their own under
        the controls, rather than the clock being cut off at the edge."""
        bar = getattr(self, "center_bar", None)
        try:
            width = bar.winfo_width()
            if width < 50:
                return
            need = 2 * PAD                                   # (the bar's own padding, left and right)
            for child in bar.winfo_children():
                if child is self.center_progress:            # (it gives way down to its least)
                    need += int(self.CENTER_LEAST_PROGRESS * self.s) + 12
                    continue
                if child is not self.center_wait and not child.winfo_manager():
                    continue
                pads = child.grid_info().get("padx", 0) or 0
                if not isinstance(pads, (tuple, list)):
                    pads = bar.tk.splitlist(str(pads))
                need += child.winfo_reqwidth() + sum(int(float(str(p))) for p in pads)
            wrapped = need > width
            if wrapped == self._center_wrapped:
                return
            self._center_wrapped = wrapped
            progress, clock = self.CENTER_BUSY[wrapped]
            showing = bool(self.center_progress.winfo_manager())
            self.center_progress.grid_configure(**progress)     # (which shows it: hidden again unless it was)
            if not showing:
                self.center_progress.grid_remove()
            self.center_wait.grid_configure(**clock)
        except (tk.TclError, AttributeError, ValueError):
            return

    def _show_center(self, answer: dict, count: int | None = None, billing: int | None = None):
        """The answer for `count` people with roles billed up to `billing` - what was asked for, whatever the
        boxes say now."""
        self.center = answer
        if count is None:
            count = spin_value(self.center_count, 5, 500, 100)
        if not answer.get("ok"):
            self.center_view.clear("Couldn't work it out", str(answer.get("error", "")))
            self.center_table.set_rows([])
            self.center_note.configure(text=CENTER_INTRO)
            self.app.set_status("Six Degrees: couldn't work out who's best connected.")
            return
        people = answer.get("people") or []
        if not people:
            self.center_view.clear("Nobody to compare", "There are no shared films with these settings - try "
                                                        "counting all roles.")
            self.center_table.set_rows([])
            self.center_note.configure(text=CENTER_INTRO)
            self.app.set_status("Six Degrees: nobody to compare with these settings.")
            return
        biggest = max(p["reaches"] for p in people)
        items = []
        for r in people[:CENTER_BARS]:
            items.append({"label": r["name"], "value": r["average_steps"], "key": r["id"],
                          "tip": f"{r['name']}\n{r['average_steps']:.2f} steps on average\n"
                                 f"reaches {plural(r['reaches'], 'person', 'people')} · the furthest is "
                                 f"{plural(r['furthest'], 'step')} away\n{plural(r['films'], 'film')} on your shelf"
                                 f"\nClick to see their profile"})
        self.center_view.show(lambda p: C.bars(p, items[:rows_that_fit(p, len(items), row=FIT_ROW)],
                                               value_fmt=C.fmt_2, on_click=self._center_clicked))
        rows = []
        for n, r in enumerate(people, 1):
            rows.append({"rank": n, "name": r["name"] + ("" if r["reaches"] == biggest else "  (separate group)"),
                         "avg": f"{r['average_steps']:.2f}", "reaches": f"{r['reaches']:,}", "films": r["films"],
                         "id": r["id"]})
        self.center_table.set_rows(rows)
        best = people[0]
        roles = f" (counting roles billed in the top {billing})" if billing is not None else ""
        took = self.timings.get("center", 0)
        self.center_note.configure(
            text=f"Among the {count} people with the most films{roles}, {best['name']} is the best connected: "
                 f"{best['average_steps']:.2f} steps on average to the {best['reaches']:,} people they can reach. "
                 "Fewer steps = better connected." + (f" Worked out in {took:.1f} s." if took >= 0.1 else ""))
        self.app.set_status(f"Six Degrees: {best['name']} is the best connected ({best['average_steps']:.2f} steps "
                            "on average).")

    def _center_clicked(self, pid):
        self.open_person(pid)

    # =========================================================================================================
    # Bridges
    # =========================================================================================================
    def _fill_groups(self):
        if self._values or self.catalog is None:
            return
        self._values = {kind: group_values(self.catalog, kind) for kind in GROUP_KINDS.values()}
        start = default_groups(self._values)
        kept, self._kept_groups = self._kept_groups, None      # (the groups picked before a new text size)
        if kept:
            start = [kept.get(side) or (start[n] if start else None) for n, side in enumerate(("a", "b"))]
            if None in start:
                start = default_groups(self._values)
        if start is None:
            return
        for side, (kind, value) in zip(("a", "b"), start):
            label = next((k for k, v in GROUP_KINDS.items() if v == kind), None)
            if label is None:
                continue
            self.group_kind[side].set(label)
            self._fill_values(side, prefer=value)

    def _fill_values(self, side: str, prefer=None):
        kind = GROUP_KINDS[self.group_kind[side].get()]
        rows = self._values.get(kind) or []
        choices = {group_choice(kind, v, n): v for v, n in rows}
        self._choices[side] = choices
        self.group_value_box[side]["values"] = list(choices)
        pick = next((label for label, v in choices.items() if v == prefer), None)
        if pick is None:
            other = self._group("b" if side == "a" else "a")
            pick = next((label for label, v in choices.items() if (kind, v) != other), None)
        self.group_value[side].set(pick or "")

    def _kind_changed(self, side: str):
        self._fill_values(side)
        self.find_bridges()

    def _group(self, side: str):
        kind = GROUP_KINDS.get(self.group_kind[side].get())
        value = self._choices.get(side, {}).get(self.group_value[side].get())
        return (kind, value) if kind and value is not None else None

    def find_bridges(self):
        if self.state != "ready" or self.catalog is None:
            return
        self._primed.add("Bridges")
        self._stale.discard("Bridges")
        self._fill_groups()
        a, b = self._group("a"), self._group("b")
        if a is None or b is None or a == b:
            # nothing to compare: drop whatever's on show (and any answer still on its way) so the page agrees
            self._cancel("bridges")
            self.bridge = None
            if a == b and a is not None:
                self.bridge_view.clear("Pick two different groups", "Both sides are the same, so nobody links them.")
                self.app.set_status("Six Degrees: pick two different groups to see who links them.")
            else:
                self.bridge_view.clear("Pick two parts of your collection", "Choose a library, country, genre or "
                                                                            "decade for each side.")
                self.app.set_status("Six Degrees: pick two parts of your collection.")
            self.bridge_note.configure(text=BRIDGE_INTRO)
            self._clear_bridge_lists()
            return
        request = {"action": "bridges", "a": {a[0]: a[1]}, "b": {b[0]: b[1]}, "count": BRIDGE_PEOPLE,
                   "max_billing": BILLING.get(self.bridge_billing.get())}
        self._start("bridges", self._ask(request), lambda answer: self._show_bridges(answer, a, b),
                    status="Finding the people who link them...", busy=self._button_busy(self.bridge_btn))

    def _show_bridges(self, answer: dict, a, b):
        self.bridge = answer
        if not answer.get("ok"):
            self.bridge_view.clear("Couldn't find bridges", str(answer.get("error", "")))
            self.bridge_note.configure(text=BRIDGE_INTRO)
            self._clear_bridge_lists()
            self.app.set_status("Six Degrees: couldn't find bridges.")
            return
        la, lb = group_name(*a), group_name(*b)
        people = answer.get("people") or []
        n_people = answer.get("people_in_both", 0)
        self.bridge_note.configure(
            text=f"{plural(answer.get('films_only_in_a', 0), la + ' film')} and "
                 f"{plural(answer.get('films_only_in_b', 0), lb + ' film')}. "
                 + (f"{plural(answer.get('films_in_both_left_out', 0), 'film is', 'films are')} in both, so "
                    "they're left out - a film on both sides links nothing. "
                    if answer.get("films_in_both_left_out") else "")
                 + f"{plural(n_people, 'person has', 'people have')} films on each side.")
        for key, text in (("a", la), ("b", lb)):
            self._retitle(self.bridge_people, key, text)
        if not people:
            limited = BILLING.get(self.bridge_billing.get()) is not None
            self.bridge_view.clear("Nobody has films on both sides",
                                   "Try counting all roles." if limited else "Try two bigger groups.")
        else:
            items = []
            for r in people[:BRIDGE_BARS]:
                ex_a = ", ".join(film_label(f) for f in r.get("examples_a", [])[:3])
                ex_b = ", ".join(film_label(f) for f in r.get("examples_b", [])[:3])
                items.append({"label": r["name"], "left": r["films_in_a"], "right": r["films_in_b"], "key": r["id"],
                              "tip": f"{r['name']}\n{la}: {plural(r['films_in_a'], 'film')} - {ex_a}\n"
                                     f"{lb}: {plural(r['films_in_b'], 'film')} - {ex_b}\nClick to see their profile"})

            def draw(p, items=items):
                room = p.width * 0.31
                f = p.font(9, "bold")
                shown = items[:rows_that_fit(p, len(items), top=22, row=FIT_ROW)]
                C.butterfly(p, shown, left_title=p.fit(la, f, room), right_title=p.fit(lb, f, room),
                            on_click=self._bridge_clicked)
            self.bridge_view.show(draw)
        self.bridge_people_title.configure(
            text=f"The first {len(people):,} of the {n_people:,} people with films on both sides"
            if n_people > len(people) else "Everyone with films on both sides")
        self.bridge_people.set_rows([
            {"name": r["name"], "a": r["films_in_a"], "b": r["films_in_b"], "id": r["id"],
             "examples": " / ".join(film_label(x[0]) for x in (r.get("examples_a"), r.get("examples_b")) if x)}
            for r in people])
        self.bridge_films.set_rows([{"title": f["title"], "year": f.get("year"), "linking": f.get("linking_people"),
                                     "key": f.get("key")}
                                    for f in answer.get("films_with_the_most_linking_people") or []])
        self.app.set_status(f"Six Degrees: {plural(n_people, 'person', 'people')} link {la} and {lb}.")

    def _clear_bridge_lists(self):
        self.bridge_people.set_rows([])
        self.bridge_films.set_rows([])
        self.bridge_people_title.configure(text="Everyone with films on both sides")

    @staticmethod
    def _retitle(table: Table, key: str, text: str, least: int = 64, most: int = 130):
        """Rename a column to a group's name (kept when the table's re-sorted), as wide as the name needs -
        between `least` and `most` px; a longer name is shortened ('Documentaries and Sho...')."""
        s = T.scale(table)
        try:
            font = tkfont.Font(font=ttk.Style(table).lookup("Treeview.Heading", "font") or "TkHeadingFont")
            room = int(most * s) - int(14 * s)
            if font.measure(f"{text} ▼") > room:
                while len(text) > 4 and font.measure(f"{text}... ▼") > room:
                    text = text[:-1].rstrip()
                text += "..."
            width = min(max(least, int((font.measure(f"{text} ▼") + 14 * s) / s + 0.999)), most)
        except tk.TclError:
            width = least
        table.columns = [(k, text if k == key else h, width if k == key else w, a) for k, h, w, a in table.columns]
        table.tree.column(key, width=int(width * s))
        arrow = ""
        if table.sorted_by and table.sorted_by[0] == key:
            arrow = " ▼" if table.sorted_by[1] else " ▲"
        table.tree.heading(key, text=text + arrow)

    def _bridge_clicked(self, pid):
        self.open_person(pid)

    # =========================================================================================================
    # Troupes
    # =========================================================================================================
    def _troupe_options(self) -> tuple[int, int, int]:
        return (spin_value(self.troupe_shared, 3, 15, 5), spin_value(self.troupe_billing, 3, 12, 8),
                spin_value(self.troupe_size, 3, 8, 3))

    def find_troupes(self):
        if self.state != "ready" or self.catalog is None:
            return
        self._primed.add("Troupes")
        shared, billing, size = self._troupe_options()
        for var, value in ((self.troupe_shared, shared), (self.troupe_billing, billing), (self.troupe_size, size)):
            var.set(str(value))
        request = {"action": "troupes", "min_shared": shared, "max_billing": billing, "min_size": size, "count": 300}
        self._start("troupes", self._ask(request), lambda answer: self._show_troupes(answer, shared, billing, size),
                    status="Looking for troupes...", busy=self._button_busy(self.troupe_btn))

    def _show_troupes(self, answer: dict, shared: int, billing: int, size: int):
        self.troupes = answer
        self._troupe_films = {}
        if not answer.get("ok"):
            self.troupe_table.set_rows([])
            self._clear_troupe("Couldn't look for troupes", str(answer.get("error", "")))
            self.app.set_status("Six Degrees: couldn't look for troupes.")
            return
        troupes = answer.get("troupes") or []
        found = answer.get("found", len(troupes))
        rule = (f"every pair has made at least {plural(shared, 'film')} together, counting roles billed in the top "
                f"{billing}")
        if not troupes:
            self.troupe_note.configure(text=f"No groups of {size} or more where {rule}.")
            self.troupe_table.set_rows([])
            self._clear_troupe("No troupes with these settings",
                               "Try fewer shared films, more billed roles, or smaller groups.")
            self.app.set_status("Six Degrees: no troupes with these settings.")
            return
        self.troupe_note.configure(
            text=f"{plural(found, 'troupe')}: groups of {size} or more where {rule}."
                 + (f" Showing the first {len(troupes)}." if found > len(troupes) else ""))
        rows = [{"members": ", ".join(t["members"]), "size": t["size"], "together": t["films_with_all_of_them"],
                 "weakest": t["every_pair_shares_at_least"], "troupe": t, "billing": billing}
                for t in troupes]
        self.troupe_table.set_rows(rows, keep_sort=False)
        self.troupe_table.sorted_by = None
        for k, h, _w, _a in self.troupe_table.columns:
            self.troupe_table.tree.heading(k, text=h)
        self.troupe_table.select_first()
        first = self.troupe_table.selected()
        if first is not None:
            self._show_troupe(first)
        self.app.set_status(f"Six Degrees: {plural(found, 'troupe')} found.")

    def _troupe_picked(self, row: dict):
        self._show_troupe(row)

    def _films_together(self, member_ids, billing: int) -> list:
        key = (tuple(member_ids), billing)
        if key not in self._troupe_films:
            ids = set(member_ids)
            films = [f for f in self.catalog.films.values()
                     if ids <= {c.person for c in f.cast if c.order <= billing}]
            self._troupe_films[key] = sorted(films, key=lambda f: (f.year or 0, f.title.casefold()))
        return self._troupe_films[key]

    def _clear_troupe(self, text: str, sub: str):
        self.troupe_head.configure(text="")
        self.troupe_link_note.configure(text="")
        self.troupe_view.clear(text, sub)
        self.troupe_films.set_rows([])
        self.troupe_films_label.configure(text="Films with all of them")

    def _show_troupe(self, row: dict):
        t = row.get("troupe")
        if not t or self.catalog is None:
            return
        ids, names = t["member_ids"], t["members"]
        films = self._films_together(ids, row.get("billing", 8))
        self.troupe_head.configure(
            text=f"{plural(t['size'], 'person', 'people')} · every pair has made at least "
                 f"{plural(t['every_pair_shares_at_least'], 'film')} together")
        nodes = []
        for pid, name in zip(ids, names):
            person = self.catalog.people.get(pid)
            total = person.film_count if person else 1
            nodes.append({"id": pid, "name": name, "weight": total,
                          "tip": f"{name}\n{plural(total, 'film')} on your shelf\nClick to see their profile"})
        edges = [{"a": pr["a"], "b": pr["b"], "weight": pr["shared_films"]} for pr in t.get("pairs") or []]
        links = balanced_links(nodes, edges)
        positions = C.layout(nodes, edges)            # placed by every pair: they all qualify
        self.troupe_view.show(lambda p: C.network(p, nodes, links, on_node=self._troupe_member,
                                                  positions=positions))
        self.troupe_link_note.configure(
            text=f"Thicker lines, more films together. {len(links)} of the {len(edges)} pairs are drawn."
            if len(links) < len(edges) else "Thicker lines, more films together.")
        self.troupe_films_label.configure(text=f"Films with all of them ({len(films)})" if films else
                                          "They've never all been in one film - each pair has, though.")
        self.troupe_films.set_rows([{"title": f.title, "year": f.year, "key": f.key} for f in films])

    def _troupe_member(self, pid, name):
        self.open_person(pid, name)
