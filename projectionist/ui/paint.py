"""A tiny drawing surface with two back ends, so every chart can be drawn in the app (TkPainter, on a Canvas)
and to a PNG without showing a window (PilPainter, needs Pillow - only for previews and tests).

Coordinates are pixels. `p.s` is the display scale (1.0 at 96 dpi): multiply fixed sizes by it, or use p.u(n).
Fonts are (size_in_points, weight) pairs from p.font(). Charts mark interactive items with tags and call
p.tip(tag, text) / p.click(tag, callback), or p.spots(...) for dots found by nearness; the PNG back end ignores
those (draw_tip draws a tooltip into a preview).
"""

from __future__ import annotations

import os

from . import theme

_ANCHORS_PIL = {"nw": "lt", "n": "mt", "ne": "rt", "w": "lm", "center": "mm", "e": "rm",
                "sw": "lb", "s": "mb", "se": "rb"}


class Painter:
    def __init__(self, width: int, height: int, s: float = 1.0):
        self.width, self.height, self.s = int(width), int(height), s

    # -- units and text ------------------------------------------------------------------------------------
    def u(self, n: float) -> float:
        return n * self.s

    @staticmethod
    def font(size: float = 9, weight: str = "normal") -> tuple:
        return (size, weight)

    def text_width(self, text: str, font) -> float:
        raise NotImplementedError

    def line_height(self, font) -> float:
        raise NotImplementedError

    def fit(self, text: str, font, width: float) -> str:
        """The text, cut short with an ellipsis if it's wider than `width`."""
        text = str(text)
        if width <= 0:
            return ""
        if self.text_width(text, font) <= width:
            return text
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.text_width(text[:mid].rstrip() + "\u2026", font) <= width:
                lo = mid
            else:
                hi = mid - 1
        return (text[:lo].rstrip() + "\u2026") if lo else ""

    def wrap(self, text: str, font, width: float, max_lines: int = 3) -> list[str]:
        """Word-wrap to `width`; the last allowed line ends in an ellipsis if there's more."""
        words, lines, line = str(text).split(), [], ""
        for w in words:
            trial = f"{line} {w}".strip()
            if self.text_width(trial, font) <= width or not line:
                line = trial
            else:
                lines.append(line)
                line = w
        if line:
            lines.append(line)
        if len(lines) > max_lines:
            lines = lines[:max_lines]
            lines[-1] = self.fit(lines[-1] + " \u2026", font, width)
        return [self.fit(x, font, width) for x in lines]

    def wrap_all(self, text: str, font, width: float) -> list[str]:
        """Word-wrap to `width` losing nothing: a word too long for a line of its own is broken across lines.
        Text that fits comes back as it is (leading spaces and all); wrapped lines keep a short indent."""
        text = str(text)
        if self.text_width(text, font) <= width:
            return [text]
        indent = text[:len(text) - len(text.lstrip())]
        if self.text_width(indent, font) > width / 3:
            indent = ""
        lines, line = [], indent
        for word in text.split():
            trial = f"{line} {word}" if line.strip() else line + word
            if self.text_width(trial, font) <= width:
                line = trial
                continue
            if line.strip():
                lines.append(line)
                line = indent
            while len(word) > 1 and self.text_width(line + word, font) > width:
                lo, hi = 1, len(word) - 1               # the most of the word that fits (at least a letter)
                while lo < hi:
                    mid = (lo + hi + 1) // 2
                    if self.text_width(line + word[:mid], font) <= width:
                        lo = mid
                    else:
                        hi = mid - 1
                lines.append(line + word[:lo])
                line, word = indent, word[lo:]
            line += word
        if line.strip() or not lines:
            lines.append(line)
        return lines

    # -- shapes (back ends implement these) -----------------------------------------------------------------
    def rect(self, x0, y0, x1, y1, fill, outline=None, width=1, tag=None):
        raise NotImplementedError

    def round_rect(self, x0, y0, x1, y1, r, fill, corners=(True, True, True, True), outline=None, tag=None):
        """corners = (top-left, top-right, bottom-right, bottom-left)."""
        raise NotImplementedError

    def line(self, points, fill, width=1, tag=None, arrow=False):
        raise NotImplementedError

    def circle(self, cx, cy, r, fill, ring=None, ring_width=2, tag=None):
        raise NotImplementedError

    def polygon(self, points, fill, tag=None):
        raise NotImplementedError

    def text(self, x, y, text, font, fill=None, anchor="w", tag=None):
        raise NotImplementedError

    # -- the data-end bar mark: rounded at the data end, square at the baseline ---------------------------
    def bar(self, x0, y0, x1, y1, fill, grows="right", tag=None):
        x0, x1 = min(x0, x1), max(x0, x1)
        y0, y1 = min(y0, y1), max(y0, y1)
        if x1 - x0 < 0.5 or y1 - y0 < 0.5:
            return
        thick = (y1 - y0) if grows in ("right", "left") else (x1 - x0)
        length = (x1 - x0) if grows in ("right", "left") else (y1 - y0)
        r = min(self.u(4), thick / 2, length)
        corners = {"right": (False, True, True, False), "left": (True, False, False, True),
                   "up": (True, True, False, False), "down": (False, False, True, True)}[grows]
        if r < 1:
            self.rect(x0, y0, x1, y1, fill, tag=tag)
        else:
            self.round_rect(x0, y0, x1, y1, r, fill, corners, tag=tag)

    # -- interaction (only the Tk back end does anything) -------------------------------------------------
    def tip(self, tag: str, text: str, highlight: bool = True):
        pass

    def click(self, tag: str, callback):
        pass

    def spots(self, spots, reach: float, on_click=None, noun: str = ""):
        """Hover and click by nearness rather than by whichever dot is drawn on top, so a dot hidden under others
        can still be reached. spots: [(x, y, tag, tip, key)]. When several lie within `reach` of the pointer, the
        tip lists them ("4 films here") and a click offers a menu of them; on_click(key) runs for the one
        chosen."""
        pass


# ---------------------------------------------------------------------------------------------------------
class TkPainter(Painter):
    """Draws on a tkinter Canvas; supports hover tooltips and clicks."""

    def __init__(self, canvas, width=None, height=None, s=None):
        self.canvas = canvas
        super().__init__(width or canvas.winfo_width(), height or canvas.winfo_height(),
                         s if s is not None else theme.scale(canvas))

    def _tkfont(self, font):
        from tkinter import font as tkfont
        root = self.canvas._root()                    # fonts belong to one Tk interpreter: cache them there
        cache = root.__dict__.setdefault("_chart_fonts", {})
        f = cache.get(font)
        if f is None:
            f = tkfont.Font(root=root, family=theme.font_family(root), size=int(round(font[0])),
                            weight="bold" if font[1] == "bold" else "normal")
            cache[font] = f
        return f

    def text_width(self, text, font):
        return self._tkfont(font).measure(str(text))

    def line_height(self, font):
        return self._tkfont(font).metrics("linespace")

    def _tags(self, tag):
        return ("chart",) + (tuple(tag.split()) if tag else ())     # "bar3 hit" -> two tags

    def rect(self, x0, y0, x1, y1, fill, outline=None, width=1, tag=None):
        return self.canvas.create_rectangle(x0, y0, x1, y1, fill=fill or "", outline=outline or "",
                                            width=width if outline else 0, tags=self._tags(tag))

    def round_rect(self, x0, y0, x1, y1, r, fill, corners=(True, True, True, True), outline=None, tag=None):
        tl, tr, br, bl = corners
        pts = []

        def corner(cx, cy, rounded, before, after):
            if rounded:
                pts.extend(before + before + [cx, cy] + after + after)
            else:
                pts.extend([cx, cy] * 3)
        corner(x0, y0, tl, [x0, y0 + r], [x0 + r, y0])
        corner(x1, y0, tr, [x1 - r, y0], [x1, y0 + r])
        corner(x1, y1, br, [x1, y1 - r], [x1 - r, y1])
        corner(x0, y1, bl, [x0 + r, y1], [x0, y1 - r])
        return self.canvas.create_polygon(pts, smooth=True, fill=fill, outline=outline or "",
                                          tags=self._tags(tag))

    def line(self, points, fill, width=1, tag=None, arrow=False):
        flat = [c for pt in points for c in pt]
        return self.canvas.create_line(*flat, fill=fill, width=max(width, 1), capstyle="round", joinstyle="round",
                                       arrow="last" if arrow else "none",
                                       arrowshape=(self.u(8), self.u(9), self.u(3)), tags=self._tags(tag))

    def circle(self, cx, cy, r, fill, ring=None, ring_width=2, tag=None):
        if ring:        # one item, the ring as its outline (drawn centred on the edge): half the items to draw
            w = self.u(ring_width)
            return self.canvas.create_oval(cx - r - w / 2, cy - r - w / 2, cx + r + w / 2, cy + r + w / 2,
                                           fill=fill, outline=ring, width=w, tags=self._tags(tag))
        return self.canvas.create_oval(cx - r, cy - r, cx + r, cy + r, fill=fill, outline="", tags=self._tags(tag))

    def polygon(self, points, fill, tag=None):
        flat = [c for pt in points for c in pt]
        return self.canvas.create_polygon(*flat, fill=fill, outline="", tags=self._tags(tag))

    def text(self, x, y, text, font, fill=None, anchor="w", tag=None):
        return self.canvas.create_text(x, y, text=str(text), font=self._tkfont(font), fill=fill or theme.INK,
                                       anchor=anchor, tags=self._tags(tag))

    # -- hover and click (kept per canvas by Interaction: see there) ---------------------------------------------
    def tip(self, tag, text, highlight=True):
        Interaction.of(self.canvas, self).tip(tag, text, highlight)

    def click(self, tag, callback):
        Interaction.of(self.canvas, self).click(tag, callback)

    def spots(self, spots, reach, on_click=None, noun=""):
        Interaction.of(self.canvas, self).add_spots(spots, reach, on_click, noun)


class Interaction:
    """A canvas's hover tips, highlights and clicks.

    Every binding is a short script calling one command, registered once per canvas, that looks the current
    drawing's tip or callback up by tag. So a redraw leaves nothing behind in Tk. (Binding each tag to a new Python
    function on every drawing, tkinter's way, left thousands of Tcl commands behind on every resize: unbinding a
    tag doesn't delete them.) Dots registered with spots() are found by nearness, so one hidden under others can
    still be hovered and clicked. The tooltip is drawn on the canvas, word-wrapped and placed to fit inside it
    (tip_lines / tip_layout), so a long tip in a narrow chart wraps instead of running off the edge.

    The owner of the canvas calls forget_drawing(canvas) before clearing it and drawing_done(canvas) after.
    """

    TIP_LINES = 6            # films listed in the tip of a pile of dots
    MENU_ENTRIES = 30        # ...and in the menu a click on it offers

    def __init__(self, canvas):
        self.canvas = canvas
        self.painter: TkPainter | None = None
        self.tips: dict[str, tuple[str, bool]] = {}
        self.clicks: dict[str, list] = {}
        self.spots: list = []        # (x, y, reach, tag, tip, key, on_click, noun)
        self.spot_at: dict[str, int] = {}
        self.active = None           # what the pointer is over: ("tag", tag) or ("spots", (tag, ...))
        self.fills: dict = {}        # item -> its fill before the hover highlight
        self.tip_items: list = []
        self._tip_lines = None       # (text, size...) -> the tooltip's wrapped lines, for the last text shown
        self.hand = False            # the hand cursor is ours to take off
        self.menu = None
        self.bound: set = set()      # (tag, sequence) bindings Tk holds for us...
        self.wanted: set = set()     # ...and the ones the current drawing uses
        self.command = canvas.register(self._event)
        canvas.tag_bind("chart", "<Motion>", self._script("motion"))
        canvas.tag_bind("chart", "<Leave>", self._script("leave"))
        canvas.bind("<Leave>", "+" + self._script("out"))

    @classmethod
    def of(cls, canvas, painter=None, create: bool = True):
        ui = canvas.__dict__.get("_chart_interaction")
        if ui is None and create:
            ui = canvas.__dict__["_chart_interaction"] = cls(canvas)
        if ui is not None and painter is not None:
            ui.painter = painter
        return ui

    # -- what a drawing registers ---------------------------------------------------------------------------------
    def _script(self, action, tag=""):
        return f'if {{"[{self.command} {action} {{{tag}}} %x %y]" == "break"}} break\n'

    def _bind(self, tag, sequence, action):
        if (tag, sequence) not in self.wanted:
            self.canvas.tag_bind(tag, sequence, self._script(action, tag))      # replaces an earlier drawing's
            self.bound.add((tag, sequence))
            self.wanted.add((tag, sequence))

    def tip(self, tag, text, highlight=True):
        self.tips[tag] = (str(text), highlight)
        self._bind(tag, "<Enter>", "enter")

    def click(self, tag, callback):
        self.clicks.setdefault(tag, []).append(callback)
        self._bind(tag, "<Enter>", "enter")
        self._bind(tag, "<Button-1>", "click")

    def add_spots(self, spots, reach, on_click=None, noun=""):
        for x, y, tag, tip, key in spots:
            self.spot_at[tag] = len(self.spots)
            self.spots.append((x, y, reach, tag, str(tip or ""), key, on_click, noun))
            if key is not None and on_click is not None:
                self._bind(tag, "<Button-1>", "click")

    def reset(self):
        """A new drawing is starting (the canvas is about to be cleared)."""
        from tkinter import TclError
        self.done()                  # (in case the last drawing wasn't finished)
        self.hide_tip()
        if self.hand:
            try:
                self.canvas.configure(cursor="")
            except TclError:
                pass
        self.tips, self.clicks, self.spots, self.spot_at = {}, {}, [], {}
        self.active, self.fills, self.hand, self.painter = None, {}, False, None
        self.wanted = set()

    def done(self):
        """The drawing is finished: unbind the tags earlier drawings used and this one doesn't."""
        from tkinter import TclError
        for tag, sequence in self.bound - self.wanted:
            try:
                self.canvas.tag_unbind(tag, sequence)
            except TclError:
                pass
        self.bound &= self.wanted

    # -- events -----------------------------------------------------------------------------------------------------
    def _event(self, action, tag, x, y):
        try:
            x, y = float(x), float(y)
        except ValueError:
            x = y = 0.0
        if action == "enter":
            self._enter(tag, x, y)
        elif action == "motion":
            self._motion(x, y)
        elif action == "leave":
            self._leave(x, y)
        elif action == "out":                # the pointer left the canvas
            if self.active is not None:
                self._drop_active()
        elif action == "click":
            self._click(tag, x, y)

    def _enter(self, tag, x, y):
        if self.active == ("tag", tag) or tag in self.spot_at:
            return
        self._drop_active()
        self.active = ("tag", tag)
        text, highlight = self.tips.get(tag, ("", False))
        if highlight:
            self._highlight(tag)
        if tag in self.clicks:
            self._hand(True)
        if text:
            self.show_tip(x, y, text)

    def _motion(self, x, y):
        if self.spots:
            near = self.spots_near(x, y)
            if near:
                self._show_spots(near, x, y)
                return
            if self.active is not None and self.active[0] == "spots":
                self._drop_active()
        if self.active is not None and self.active[0] == "tag":
            text = self.tips.get(self.active[1], ("", False))[0]
            if text:
                self.show_tip(x, y, text)

    def _leave(self, x, y):
        # Moving between the items of one mark (a bar's row, the bar and its label) keeps its highlight and tip.
        if self.active is None:
            return
        c = self.canvas
        top = [i for i in c.find_overlapping(x, y, x, y) if "chart" in c.gettags(i)]
        if not top or (self.active[0] == "tag" and self.active[1] not in c.gettags(top[-1])):
            self._drop_active()

    def _click(self, tag, x, y):
        if tag not in self.spot_at:
            for callback in list(self.clicks.get(tag, ())):
                callback()
            return
        near = [s for s in self.spots_near(x, y) if s[5] is not None and s[6] is not None]
        own = self.spots[self.spot_at[tag]]
        if own not in near and own[5] is not None and own[6] is not None:
            near.insert(0, own)
        if len(near) == 1:
            near[0][6](near[0][5])
        elif near:
            self._choose(near)

    # -- dots found by nearness -------------------------------------------------------------------------------
    def spots_near(self, x, y) -> list:
        """The spots within reach of (x, y), nearest first."""
        near = []
        for n, spot in enumerate(self.spots):
            d2 = (spot[0] - x) ** 2 + (spot[1] - y) ** 2
            if d2 <= spot[2] ** 2:
                near.append((d2, n, spot))
        return [spot for _, _, spot in sorted(near)]

    def spots_text(self, near) -> str:
        if len(near) == 1:
            return near[0][4]
        noun = near[0][7]
        lines = [f"{len(near)} {noun} here" if noun else f"{len(near)} here"]
        lines += [s[4].split("\n")[0] for s in near[:self.TIP_LINES] if s[4]]
        if len(near) > self.TIP_LINES:
            lines.append(f"+ {len(near) - self.TIP_LINES} more")
        if any(s[5] is not None and s[6] is not None for s in near):
            lines.append("Click to choose one")
        return "\n".join(lines)

    def _show_spots(self, near, x, y):
        which = ("spots", tuple(s[3] for s in near))
        if self.active != which:
            self._drop_active()
            self.active = which
            for s in near:
                self._highlight(s[3])
            self.canvas.tag_raise(near[0][3])            # the nearest dot comes out from under the others
            self._hand(any(s[5] is not None and s[6] is not None for s in near))
        text = self.spots_text(near)
        if text:
            self.show_tip(x, y, text)
        else:
            self.hide_tip()

    def _choose(self, near):
        """A pile of dots was clicked: offer them in a menu, at the pointer."""
        import tkinter as tk
        if not self.canvas.winfo_viewable():                # never pop anything up from a hidden window (tests)
            return
        if self.menu is None:
            self.menu = tk.Menu(self.canvas, tearoff=0)
        else:
            self.menu.delete(0, "end")                     # (which deletes the old entries' commands too)
        theme.restyle(self.menu)                           # (kept from last time: the look may have changed since)
        for s in near[:self.MENU_ENTRIES]:
            self.menu.add_command(label=s[4].split("\n")[0] or str(s[5]), command=lambda s=s: s[6](s[5]))
        self._drop_active()
        try:
            self.menu.tk_popup(self.canvas.winfo_pointerx(), self.canvas.winfo_pointery())
        finally:
            self.menu.grab_release()

    # -- highlight, cursor and tooltip ----------------------------------------------------------------------------
    def _highlight(self, tag):
        c = self.canvas
        for item in c.find_withtag(tag):
            if item in self.fills or c.type(item) not in ("rectangle", "polygon", "oval"):
                continue
            fill = c.itemcget(item, "fill")
            if fill and fill.startswith("#") and len(fill) == 7:
                self.fills[item] = fill
                hit = "hit" in c.gettags(item)           # the row's hit area gets a light wash
                c.itemconfigure(item, fill=theme.HOVER if hit else theme.mix(fill, theme.INK, 0.18))

    def _hand(self, on: bool):
        if on != self.hand:
            self.hand = on
            self.canvas.configure(cursor="hand2" if on else "")

    def _drop_active(self):
        from tkinter import TclError
        for item, fill in self.fills.items():
            try:
                self.canvas.itemconfigure(item, fill=fill)
            except TclError:
                pass
        self.fills = {}
        self.active = None
        self.hide_tip()
        self._hand(False)

    def hide_tip(self):
        for item in self.tip_items:
            self.canvas.delete(item)
        self.tip_items = []

    def show_tip(self, x, y, text):
        """The tooltip for the pointer at (x, y): wrapped to fit the canvas and kept inside it (see tip_lines)."""
        self.hide_tip()
        p = self.painter or TkPainter(self.canvas)
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        width = cw if cw > 1 else p.width                 # (a hidden window's canvas says 1: use the drawing's size)
        height = ch if ch > 1 else p.height
        key = (str(text), width, height, p.s)
        if self._tip_lines is None or self._tip_lines[0] != key:    # (the pointer moves; the text seldom changes)
            self._tip_lines = (key, tip_lines(p, text, width, height))
        box, placed = tip_layout(p, x, y, self._tip_lines[1], width, height)
        self.tip_items.append(self.canvas.create_rectangle(*box, fill=theme.CHART_TOOLTIP_BG,
                                                           outline=theme.CHART_TOOLTIP_BORDER, tags=("tooltip",)))
        for lx, ly, line, font in placed:
            self.tip_items.append(self.canvas.create_text(lx, ly, text=line, anchor="nw", fill=theme.INK,
                                                          font=p._tkfont(font), tags=("tooltip",)))


# -- tooltips: what they say and where they go (the same on either back end) -----------------------------------
TIP_FONT = 9


def tip_lines(p: Painter, text: str, width: float, height: float) -> list[tuple[str, tuple]]:
    """A tooltip's lines as [(text, font)] for a chart width x height. A tooltip is drawn on its chart, so every
    line is word-wrapped to fit inside it - nothing is cut off at the edge. The first line is a heading (bold)
    when there's more than one; if even so it's taller than the chart, it ends in an ellipsis where it has to."""
    pad, margin = p.u(6), 2
    normal, bold = p.font(TIP_FONT), p.font(TIP_FONT, "bold")
    room = max(width - 2 * margin - 2 * pad, p.u(40))
    raw = str(text).split("\n")
    lines = []
    for i, line in enumerate(raw):
        font = bold if i == 0 and len(raw) > 1 else normal
        lines += [(piece, font) for piece in p.wrap_all(line, font, room)]
    most = max(1, int((height - 2 * margin - 2 * pad) // p.line_height(normal)))
    if len(lines) > most:
        lines = lines[:most]
        last, font = lines[-1]
        lines[-1] = (p.fit(last + " …", font, room), font)
    return lines


def tip_layout(p: Painter, x: float, y: float, lines, width: float, height: float):
    """Where tip_lines' lines go for the pointer at (x, y): the box (x0, y0, x1, y1) and [(x, y, text, font)]
    anchored top left. Below and right of the pointer; on its other side where there's no room; and always
    inside the chart."""
    pad, margin = p.u(6), 2
    lh = p.line_height(p.font(TIP_FONT))
    w = max((p.text_width(t, f) for t, f in lines), default=0) + 2 * pad
    h = lh * len(lines) + 2 * pad
    tx, ty = x + p.u(14), y + p.u(14)
    if tx + w > width - margin:
        tx = x - w - p.u(10)
    if ty + h > height - margin:
        ty = y - h - p.u(10)
    tx = max(margin, min(tx, width - margin - w))
    ty = max(margin, min(ty, height - margin - h))
    return (tx, ty, tx + w, ty + h), [(tx + pad, ty + pad + i * lh, t, f) for i, (t, f) in enumerate(lines)]


def draw_tip(p: Painter, x: float, y: float, text: str):
    """Draw the tooltip the app would show for the pointer at (x, y) - for PNG previews and tests (the app's own
    tooltips are drawn by Interaction)."""
    box, placed = tip_layout(p, x, y, tip_lines(p, text, p.width, p.height), p.width, p.height)
    p.rect(*box, theme.CHART_TOOLTIP_BG, outline=theme.CHART_TOOLTIP_BORDER, tag="tooltip")
    for lx, ly, line, font in placed:
        p.text(lx, ly, line, font, theme.INK, "nw", tag="tooltip")


def forget_drawing(canvas):
    """Before a canvas is cleared for a new drawing: drop the old drawing's tips, clicks and tooltip."""
    ui = Interaction.of(canvas, create=False)
    if ui is not None:
        ui.reset()


def drawing_done(canvas):
    """After a canvas's drawing: remove the bindings of tags it no longer uses."""
    ui = Interaction.of(canvas, create=False)
    if ui is not None:
        ui.done()


# ---------------------------------------------------------------------------------------------------------
class PilPainter(Painter):
    """Draws into a Pillow image (supersampled for smooth edges). For previews and tests - the app itself never
    needs Pillow."""

    SUPERSAMPLE = 2

    def __init__(self, width, height, s=1.0, background=None):
        from PIL import Image, ImageDraw
        super().__init__(width, height, s)
        self.k = self.SUPERSAMPLE
        self.image = Image.new("RGB", (int(width * self.k), int(height * self.k)), background or theme.SURFACE)
        self.draw = ImageDraw.Draw(self.image)
        self._fonts = {}

    # The font files tried, nearest to the app's first: Windows' own, then the usual Linux ones (Pillow finds
    # those by name in the system's font folders)
    FONT_FILES = {"normal": ("segoeui.ttf", "arial.ttf", "NotoSans-Regular.ttf", "Ubuntu-R.ttf", "DejaVuSans.ttf",
                             "LiberationSans-Regular.ttf"),
                  "bold": ("segoeuib.ttf", "arialbd.ttf", "NotoSans-Bold.ttf", "Ubuntu-B.ttf", "DejaVuSans-Bold.ttf",
                           "LiberationSans-Bold.ttf")}

    def _pilfont(self, font):
        from PIL import ImageFont
        key = font
        if key not in self._fonts:
            px = max(1, round(font[0] * 96 / 72 * self.s * self.k))
            names = self.FONT_FILES["bold" if font[1] == "bold" else "normal"]
            windir = os.environ.get("WINDIR", r"C:\Windows")
            loaded = None
            for name in names:
                for path in (os.path.join(windir, "Fonts", name), name):
                    try:
                        loaded = ImageFont.truetype(path, px)
                        break
                    except OSError:
                        continue
                if loaded:
                    break
            if loaded is None:
                try:
                    loaded = ImageFont.load_default(px)
                except TypeError:                      # (Pillow before 10.1: one small bitmap font, no size)
                    loaded = ImageFont.load_default()
            self._fonts[key] = loaded
        return self._fonts[key]

    def _k(self, *values):
        return [v * self.k for v in values]

    def text_width(self, text, font):
        f = self._pilfont(font)
        if hasattr(f, "getlength"):
            return f.getlength(str(text)) / self.k
        return f.getsize(str(text))[0] / self.k               # (an older Pillow's bitmap font)

    def line_height(self, font):
        f = self._pilfont(font)
        if hasattr(f, "getmetrics"):
            ascent, descent = f.getmetrics()
        else:                                                  # (an older Pillow's bitmap font)
            ascent, descent = f.getsize("Ag")[1], 0
        return (ascent + descent) * 1.08 / self.k

    def rect(self, x0, y0, x1, y1, fill, outline=None, width=1, tag=None):
        box = self._k(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
        self.draw.rectangle(box, fill=fill or None, outline=outline, width=int(width * self.k) if outline else 0)

    def round_rect(self, x0, y0, x1, y1, r, fill, corners=(True, True, True, True), outline=None, tag=None):
        box = [round(v) for v in self._k(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))]
        radius = int(min(r * self.k, (box[2] - box[0]) / 2 - 1, (box[3] - box[1]) / 2 - 1))
        if radius < 1:
            self.draw.rectangle(box, fill=fill, outline=outline)
            return
        try:
            self.draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, corners=tuple(corners))
        except TypeError:                              # (an older Pillow rounds every corner or none)
            self.draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline)
            x0, y0, x1, y1 = box
            squares = ((x0, y0, x0 + radius, y0 + radius), (x1 - radius, y0, x1, y0 + radius),
                       (x1 - radius, y1 - radius, x1, y1), (x0, y1 - radius, x0 + radius, y1))
            for rounded, square in zip(corners, squares):
                if not rounded and fill:
                    self.draw.rectangle(square, fill=fill)      # (square that corner off again)

    def line(self, points, fill, width=1, tag=None, arrow=False):
        pts = [(x * self.k, y * self.k) for x, y in points]
        w = max(1, round(width * self.k))
        self.draw.line(pts, fill=fill, width=w, joint="curve")
        for x, y in (pts[0], pts[-1]):          # round caps
            self.draw.ellipse((x - w / 2, y - w / 2, x + w / 2, y + w / 2), fill=fill)
        if arrow and len(pts) >= 2:
            import math
            (xa, ya), (xb, yb) = pts[-2], pts[-1]
            ang = math.atan2(yb - ya, xb - xa)
            size = self.u(8) * self.k
            left = (xb - size * math.cos(ang - 0.45), yb - size * math.sin(ang - 0.45))
            right = (xb - size * math.cos(ang + 0.45), yb - size * math.sin(ang + 0.45))
            self.draw.polygon([(xb, yb), left, right], fill=fill)

    def circle(self, cx, cy, r, fill, ring=None, ring_width=2, tag=None):
        if ring:
            w = self.u(ring_width)
            self.draw.ellipse(self._k(cx - r - w, cy - r - w, cx + r + w, cy + r + w), fill=ring)
        self.draw.ellipse(self._k(cx - r, cy - r, cx + r, cy + r), fill=fill)

    def polygon(self, points, fill, tag=None):
        self.draw.polygon([(x * self.k, y * self.k) for x, y in points], fill=fill)

    def text(self, x, y, text, font, fill=None, anchor="w", tag=None):
        try:
            self.draw.text((x * self.k, y * self.k), str(text), font=self._pilfont(font), fill=fill or theme.INK,
                           anchor=_ANCHORS_PIL.get(anchor, "lm"))
        except ValueError:                             # (Pillow's bitmap font, the last resort, takes no anchor)
            self.draw.text((x * self.k, y * self.k), str(text), font=self._pilfont(font), fill=fill or theme.INK)

    def save(self, path: str):
        from PIL import Image
        img = self.image.resize((self.width, self.height), Image.LANCZOS) if self.k != 1 else self.image
        img.save(path)
        return path


def render_png(draw, path: str, width: int = 640, height: int = 360, s: float = 1.0) -> str:
    """Draw `draw(painter)` into a PNG - how charts are previewed and tested without a window."""
    p = PilPainter(width, height, s)
    draw(p)
    return p.save(path)
