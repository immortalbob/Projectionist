"""What a tab looks like to the main window.

A tab module defines `class Tab(BaseTab)` with a `title`. The main window builds it, then calls:

    catalog_changed(catalog, state)   whenever the collection is (re)loaded: state is 'loading', 'ready',
                                      'error' or 'none' (no database); catalog is a catalog.Catalog or None
    shown()                           each time the tab is brought to the front (do slow work lazily here)
    navigate(**kwargs)                when another tab sends the user here, e.g. navigate(person_id=...)
    theme_changed()                   after the look changed (the Settings tab): redo anything the tab drew with
                                      colours of its own. Most things follow by themselves - ttk styles (a
                                      module's own through theme.add_styles), classic widgets coloured with
                                      theme.tint / tint_tag, every ChartView (redrawn), colours kept as token names
                                      in chart items ("color": "BLUE")
    preference_changed(key, value)    a setting changed (prefs.set - the Settings tab, or another tab)
    text_size_changed() -> bool       Settings > Text size changed (see there): True when the tab has re-measured
                                      itself; False (the default) has the main window build it again in its place,
                                      carrying keep()'s dict across to the new one's restore()
    closing() -> dict                 the window is closing (after being on screen): settings to save as it goes
                                      ({key: value}; {} for none)

and the tab uses the app's services (self.app):

    app.catalog / app.catalog_state   the loaded collection and its state
    app.ask(request) -> dict          answer a JSON-style request (projectionist.ask) against the loaded catalog -
                                      quick ones only, on the UI thread
    app.run(work, done, failed=None, status=None)
                                      run work() on a background thread; done(result) / failed(message) are
                                      called back on the UI thread (never touch widgets from work()). `status`
                                      shows at the bottom while it runs and is cleared when it ends, unless
                                      something else (done() itself, a newer job) has been shown since
    app.goto(tab_title, **kwargs)     bring another tab to the front and call its navigate(**kwargs)
    app.set_status(text)              the line at the bottom of the window
    app.settings                      the saved settings: read and change them through projectionist.prefs
                                      (prefs.get(self.app, key) / prefs.set(self.app, key, value)), where each
                                      one is defined - a tab defines its own when its module is imported

Charts go in widgets.ChartView: it redraws on resize, drops the old drawing's tips and clicks (charts.py and
paint.py do the hover and click handling, so a tab needn't bind anything itself) and reports drawing errors to the
Export tab's log.
"""

from __future__ import annotations

from tkinter import ttk

from . import charts
from .widgets import ChartView


class BaseTab:
    title = "Tab"

    def __init__(self, app, notebook):
        self.app = app
        self.notebook = notebook
        self.frame = ttk.Frame(notebook, style="Page.TFrame", padding=0)
        self.catalog = None
        self.state = "none"

    # -- called by the main window -------------------------------------------------------------------------
    def catalog_changed(self, catalog, state: str):
        self.catalog, self.state = catalog, state

    def shown(self):
        pass

    def navigate(self, **kwargs):
        pass

    def theme_changed(self):
        pass

    def preference_changed(self, key: str, value):
        pass

    def text_size_changed(self) -> bool:
        """Settings > Text size changed, while the app runs. By now every font has been measured again (so every
        label, list and text grows by itself), the styles are in the new size (the tables' row heights) and every
        ChartView has its new scale and the height it was given, in it. A tab that sized anything else with
        theme.scale() - a wrap length, a column's width, a card's minimum - re-measures it here and returns True.
        False (the default): the main window builds the tab again in its place, over the collection already read,
        and passes what keep() returned to the new tab's restore(). Its background jobs (keyed '<module>.<what>',
        as App.run asks) and timers are called off first."""
        return False

    def keep(self) -> dict:
        """What the tab built in this one's place (see text_size_changed) should show: {} for nothing."""
        return {}

    def restore(self, kept: dict):
        """Show what keep() kept - called on the new tab after catalog_changed(), before it's shown."""

    def closing(self) -> dict:
        return {}

    # -- helpers ----------------------------------------------------------------------------------------------
    def state_message(self) -> tuple[str, str] | None:
        """What to show instead of content when there's no collection yet, else None."""
        if self.state == "loading":
            return "Reading your collection...", "This takes a few seconds."
        if self.state == "error":
            return "Couldn't read the collection", getattr(self.app, "catalog_error", "") or ""
        if self.state != "ready" or self.catalog is None:
            return "No database yet", "Pick your Plex database on the Export tab."
        return None

    @staticmethod
    def show_message(view: ChartView, text: str, sub: str | None = None):
        view.show(lambda p: charts.message(p, text, sub))
