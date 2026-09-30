"""The desktop app's tabs and the pieces they share: looks, drawing, charts and widgets.

palettes  the four looks' colour tokens, as data (Light, Graphite - the default - Projection Booth, Velvet)
theme     the look in use: colour tokens, fonts and ttk styles, switched live (theme.apply)
paint     a small drawing surface with two back ends: a Tk canvas (the app) and Pillow (PNG previews and tests)
charts    the charts, drawn on either back end
widgets   ChartView, Table, SearchBox, ScrollFrame, Placeholder...
base      what a tab looks like to the main window
settings  the Settings tab, built from the preferences registry (projectionist/prefs.py)
"""
