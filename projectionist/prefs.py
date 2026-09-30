"""Preferences: every setting the app keeps is DEFINED once and READ everywhere through here.

A module defines its own settings when it's imported, next to the code that uses them:

    from .. import prefs                                   # (from projectionist.ui; `from . import prefs` above it)
    prefs.section("Library Doctor", order=50, hint="What the Library Doctor tab checks for.")
    prefs.define("doctor_languages", "Library Doctor", "Your languages", kind="languages", default=["en"],
                 help="Audio and subtitles in these count as ones you understand.", parse=..., format=...)

and reads or changes it:

    prefs.get(app, "doctor_languages")        app: the main window, or a settings dict, or None (the default)
    prefs.set(app, "doctor_languages", ["en", "ja"])
                                              checks the value (ValueError if it's no good), saves it at once, runs
                                              the setting's own apply(app, value), and tells the tabs
                                              (app.preference_changed -> BaseTab.preference_changed)
    prefs.reset(app, section="Library Doctor")   or prefs.reset(app) for everything on the Settings tab

Settings are kept in App.settings (a dict the main window saves as settings.json) under their key - the same JSON
keys the app has always used, so nothing saved before is lost. A missing or unusable value reads as the default,
so a new setting needs nothing in the file, and a reset simply forgets the saved value.

The Settings tab (ui/settings.py) is built from the definitions: each section is a card (in `order`), each shown
setting a row with its label, its control and its help text. shown=False keeps a setting off the tab: things the
app remembers by itself, like the window's size or the last database.

kind       'choice'   choices: [(value, label)], or a function (app) -> that list
           'bool'
           'number'   minimum / maximum (values outside are brought inside), step, unit ('films'); whole numbers
                      when the default is a whole number
           'text'
           'folder'   a folder's path; '' = none chosen
           'list'     of text and numbers (anything else in a hand-edited file is dropped)
           or a module's own kind, with parse / format (e.g. 'languages'). The Settings tab needs a control for a
           new kind: settings.add_control(kind, builder); `control` picks a special one for a setting (the looks'
           picture picker is control='looks').
default    a value, or a function (app) -> value (e.g. the first spreadsheet program found)
parse      value -> the value kept (raise ValueError for a bad one); format: value -> the words shown for it
apply      a function (app, value) that makes a change show at once; NEXT_START when it only takes effect the
           next time the app opens (the Settings tab says so); None when whatever uses the setting reads it each
           time it needs it (which is live too).
"""

from __future__ import annotations

import math
import traceback
from dataclasses import dataclass
from typing import Any, Callable

NEXT_START = "next start"
_MISSING = object()


@dataclass
class Section:
    name: str
    order: int = 100
    hint: str = ""


@dataclass
class Pref:
    key: str
    section: str
    label: str
    kind: str = "text"
    default: Any = None
    choices: Any = None
    help: str = ""
    apply: Any = None
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    unit: str = ""
    shown: bool = True
    parse: Callable | None = None
    format: Callable | None = None
    control: str | None = None       # which control the Settings tab draws it with (by default: by its kind)
    order: int = 0

    @property
    def live(self) -> bool:
        """A change shows at once (not only from the next start)."""
        return self.apply != NEXT_START

    def default_for(self, app=None):
        value = self.default(app) if callable(self.default) else self.default
        return list(value) if isinstance(value, list) else dict(value) if isinstance(value, dict) else value

    def choices_for(self, app=None) -> list[tuple[Any, str]] | None:
        """[(value, label)] - None when they depend on the app and there's no app to ask."""
        choices = self.choices
        if callable(choices):
            if app is None:
                return None
            try:
                choices = choices(app)
            except Exception:                      # (a choice list that can't be worked out: take any value)
                return None
        if choices is None:
            return None
        return [c if isinstance(c, tuple) else (c, str(c)) for c in choices]

    def clean(self, value, app=None):
        """The value as it's kept, or ValueError."""
        if self.parse is not None:
            value = self.parse(value)
        kind = self.kind
        if kind == "bool":
            if isinstance(value, bool):
                return value
            if isinstance(value, int) and value in (0, 1):
                return bool(value)
            if isinstance(value, str) and value.strip().lower() in ("true", "false", "yes", "no", "1", "0"):
                return value.strip().lower() in ("true", "yes", "1")
            raise ValueError(f"{self.label}: yes or no, not {value!r}")
        if kind == "choice":
            allowed = self.choices_for(app)
            if allowed is not None and value not in [v for v, _ in allowed]:
                raise ValueError(f"{self.label}: {value!r} isn't one of the choices")
            return value
        if kind == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float, str)):
                raise ValueError(f"{self.label}: a number, not {value!r}")
            try:
                number = float(value)
            except (ValueError, OverflowError):                 # (an int too big for a float is no number here)
                raise ValueError(f"{self.label}: a number, not {value!r}") from None
            if not math.isfinite(number):
                raise ValueError(f"{self.label}: a number, not {value!r}")
            if self.minimum is not None:
                number = max(number, self.minimum)
            if self.maximum is not None:
                number = min(number, self.maximum)
            whole = isinstance(self.default_for(app), int) and not isinstance(self.default_for(app), bool)
            return int(round(number)) if whole else number
        if kind in ("text", "folder"):
            if not isinstance(value, str):
                raise ValueError(f"{self.label}: text, not {value!r}")
            return value.strip() if kind == "folder" else value
        if kind == "list":
            if not isinstance(value, (list, tuple)):
                raise ValueError(f"{self.label}: a list, not {value!r}")
            return [v for v in value if isinstance(v, (str, int, float)) and not isinstance(v, bool)]
        return value                              # a module's own kind: its parse() did the checking

    def words(self, value, app=None) -> str:
        """The value as the Settings tab says it."""
        if self.format is not None:
            try:
                return str(self.format(value))
            except Exception:
                pass
        if self.kind == "bool":
            return "On" if value else "Off"
        if self.kind == "choice":
            for v, label in self.choices_for(app) or []:
                if v == value:
                    return label
        if self.kind == "number":
            return f"{value}{' ' + self.unit if self.unit else ''}"
        if isinstance(value, list):
            return ", ".join(str(v) for v in value)
        return "" if value is None else str(value)


SECTIONS: dict[str, Section] = {}
PREFS: dict[str, Pref] = {}
_order = [0]


def section(name: str, order: int | None = None, hint: str | None = None) -> Section:
    """A group of settings (a card on the Settings tab). Defining it again updates what's given."""
    sec = SECTIONS.get(name)
    if sec is None:
        sec = SECTIONS[name] = Section(name)
    if order is not None:
        sec.order = order
    if hint is not None:
        sec.hint = hint
    return sec


def define(key: str, section_name: str, label: str, kind: str = "text", default=None, **options) -> Pref:
    """Define a setting (see the module's docstring for the options). Defining a key again replaces its
    definition but keeps its place."""
    if section_name not in SECTIONS:
        section(section_name)
    old = PREFS.get(key)
    if old is not None:
        order = old.order
    else:
        _order[0] += 1
        order = _order[0]
    p = Pref(key=key, section=section_name, label=label, kind=kind, default=default, order=order, **options)
    PREFS[key] = p
    return p


def pref(key: str) -> Pref:
    return PREFS[key]


def settings_of(source) -> dict | None:
    """The settings dict of an app (or the dict itself)."""
    if isinstance(source, dict):
        return source
    settings = getattr(source, "settings", None)
    return settings if isinstance(settings, dict) else None


def _app(source):
    return None if source is None or isinstance(source, dict) else source


def get(source, key: str):
    """The setting's value - the saved one when it's usable, otherwise the default. Never raises for a bad
    saved value (settings are a convenience)."""
    p = PREFS[key]
    app = _app(source)
    saved = settings_of(source)
    if saved is not None and key in saved:
        try:
            return p.clean(saved[key], app)
        except (ValueError, TypeError, OverflowError, RecursionError):
            pass
    return p.default_for(app)


def is_default(source, key: str) -> bool:
    return get(source, key) == PREFS[key].default_for(_app(source))


def set(app, key: str, value, notify: bool = True):         # noqa: A001  (prefs.set reads well at the call site)
    """Change a setting: checked (ValueError if it's no good), saved at once, applied and passed on to the tabs.
    -> the value kept. Setting what it already is does nothing."""
    p = PREFS[key]
    value = p.clean(value, app)
    saved = settings_of(app)
    if saved is None:
        raise TypeError("prefs.set needs the app (or a settings dict)")
    if key in saved and saved[key] == value:
        return value
    before = get(app, key)
    saved[key] = value
    _save(app)
    if notify and before != value:
        _changed(app, p, value)
    return value


def reset(app, keys=None, section: str | None = None) -> list[str]:
    """Back to the defaults: the given keys, or every setting shown in a section, or every setting shown on the
    Settings tab. The saved values are forgotten (so a later change of default applies). -> the keys whose value
    changed."""
    saved = settings_of(app)
    if saved is None:
        return []
    if keys is None:
        keys = [p.key for p in shown(section)]
    before = {key: get(app, key) for key in keys}
    removed = False
    for key in keys:
        if key in saved:
            del saved[key]
            removed = True
    if removed:
        _save(app)
    changed = [key for key in keys if get(app, key) != before[key]]
    for key in changed:
        _changed(app, PREFS[key], get(app, key))
    return changed


def shown(section: str | None = None) -> list[Pref]:
    """The settings the Settings tab lists (of one section), in the order they were defined."""
    return sorted((p for p in PREFS.values() if p.shown and (section is None or p.section == section)),
                  key=lambda p: p.order)


def sections() -> list[Section]:
    """The sections with something to show, in order."""
    names = {p.section for p in PREFS.values() if p.shown}
    return sorted((SECTIONS[n] for n in names), key=lambda s: (s.order, s.name))


def _save(app):
    """Save the settings through the app. -> False when the app says the file couldn't be written (the value
    lasts until the app closes: the Settings tab says so), else True."""
    for name in ("save_settings", "_remember"):
        save = getattr(app, name, None)
        if callable(save):
            return save() is not False
    return True


def save_ok(app) -> bool:
    """Whether the app's last save of its settings worked (a window without a settings file: True)."""
    return getattr(app, "settings_saved", True) is not False


def _changed(app, p: Pref, value):
    """Apply a change and tell the tabs. A failure is reported (the app's log), never raised: the value is saved
    either way."""
    if callable(p.apply):
        try:
            p.apply(app, value)
        except Exception:
            _report(app, f"The setting '{p.label}' couldn't be applied:\n{traceback.format_exc().strip()}")
    hook = getattr(app, "preference_changed", None)
    if callable(hook):
        try:
            hook(p.key, value)
        except Exception:
            _report(app, traceback.format_exc().strip())


def _report(app, text: str):
    log = getattr(app, "_log", None)
    if callable(log):
        try:
            log(text)
            return
        except Exception:
            pass
    import sys
    if sys.stderr is not None:
        print(text, file=sys.stderr)
