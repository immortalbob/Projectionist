"""Charts, drawn on any Painter (the app's canvas, or a PNG for previews and tests).

Every chart takes the painter, its data, and keyword options; `box=(x, y, w, h)` limits it to part of the
surface (default: all of it). Items are dicts - {"label", "value", "key", "tip", "color"} - and charts that
support it take on_click(key) for clicks. The house rules (from the reference palette): thin marks with a
rounded data end, hairline recessive grids, values at bar tips in ink (never in the series colour), a legend
whenever there are two or more series, one hue for one series, and status colours only for statuses.
"""

from __future__ import annotations

import math
import random

from . import theme as T
from .paint import Painter


# ---------------------------------------------------------------------------------------------------------
# Formatting and small helpers
# ---------------------------------------------------------------------------------------------------------
def fmt_int(v) -> str:
    return f"{int(round(v)):,}"


def fmt_1(v) -> str:
    return f"{v:.1f}"


def fmt_2(v) -> str:
    return f"{v:.2f}"


def fmt_signed(v) -> str:
    return f"{v:+.1f}" if abs(v) >= 0.05 else f"{v:+.2f}"


def fmt_pct(v) -> str:
    return f"{v * 100:.0f}%"


def compact(v) -> str:
    v = float(v)
    for div, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(v) >= div:
            x = v / div
            return f"{x:.1f}{suffix}".replace(".0" + suffix, suffix) if x < 100 else f"{x:.0f}{suffix}"
    return f"{v:,.0f}" if v == int(v) else f"{v:.1f}"


def clock(seconds: float) -> str:
    """h:mm:ss, truncated to the second (as the spreadsheet and the credits lists show times)."""
    s = int(seconds + 1e-6)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def nice_ticks(lo: float, hi: float, count: int = 5) -> list[float]:
    """Round tick values covering [lo, hi]: 0, 250, 500..."""
    if hi <= lo:
        hi = lo + 1
    raw = (hi - lo) / max(count, 1)
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    first = math.floor(lo / step) * step
    ticks, v = [], first
    while v <= hi + step * 1e-9:
        ticks.append(round(v, 10))
        v += step
    if ticks[-1] < hi:
        ticks.append(round(ticks[-1] + step, 10))
    return ticks


def _box(p: Painter, box):
    return box if box else (0, 0, p.width, p.height)


def _items(items) -> list[dict]:
    out = []
    for i, it in enumerate(items):
        if isinstance(it, dict):
            d = dict(it)
        else:
            d = {"label": it[0], "value": it[1]}
        d.setdefault("key", d.get("label", i))
        out.append(d)
    return out


def header(p: Painter, x, y, w, title=None, subtitle=None) -> float:
    """Title (and subtitle) at the top-left; returns the height used."""
    h = 0.0
    if title:
        f = p.font(10, "bold")
        p.text(x, y + p.line_height(f) / 2, p.fit(title, f, w), f, T.INK, "w")
        h += p.line_height(f)
    if subtitle:
        f = p.font(9)
        for line in p.wrap(subtitle, f, w, 2):
            p.text(x, y + h + p.line_height(f) / 2, line, f, T.MUTED, "w")
            h += p.line_height(f)
    return h + (p.u(8) if h else 0)


def message(p: Painter, text: str, sub: str | None = None, box=None):
    """A calm placeholder in the middle of the chart area."""
    x, y, w, h = _box(p, box)
    f, fs = p.font(10, "bold"), p.font(9)
    lines = p.wrap(sub, fs, w * 0.8, 3) if sub else []
    total = p.line_height(f) + len(lines) * p.line_height(fs) + (p.u(4) if lines else 0)
    top = y + (h - total) / 2
    p.text(x + w / 2, top + p.line_height(f) / 2, p.fit(text, f, w * 0.9), f, T.INK_2, "center")
    for i, line in enumerate(lines):
        p.text(x + w / 2, top + p.line_height(f) + p.u(4) + (i + 0.5) * p.line_height(fs), line, fs, T.MUTED,
               "center")


def _hit(p: Painter, x0, y0, x1, y1, tag):
    """An invisible hit area (the whole row), bigger than the mark, so hovering is easy."""
    p.rect(x0, y0, x1, y1, T.SURFACE, tag=tag)


def _wire(p: Painter, tag, item, on_click):
    """The item's tooltip, and its click - unless the item says clickable=False (nothing to open for it: then it
    doesn't get the hand pointer either)."""
    if item.get("tip"):
        p.tip(tag, item["tip"])
    if on_click is not None and item.get("clickable", True) is not False:
        p.click(tag, lambda key=item["key"]: on_click(key))


# ---------------------------------------------------------------------------------------------------------
# Horizontal bars - magnitude by category (one series)
# ---------------------------------------------------------------------------------------------------------
def bars(p: Painter, items, *, box=None, title=None, subtitle=None, value_fmt=fmt_int, color=None,
         max_value=None, on_click=None, label_share=0.42, emphasis=None, track=False):
    """items: [{label, value, key?, tip?, color?, clickable?}]. color: the bars' (BLUE by default). emphasis: keys
    drawn in that colour (others grey). track: a faint full-length bar behind each one, so it shows where max_value
    (say 100%) would reach. An item's colour may be a token's name ("BLUE") - so it follows the look."""
    color = T.value(color) or T.BLUE
    x, y, w, h = _box(p, box)
    items = _items(items)
    top = y + header(p, x, y, w, title, subtitle)
    if not items:
        message(p, "Nothing to show", box=(x, top, w, y + h - top))
        return
    f = p.font(9)
    vmax = max_value or max((abs(i["value"]) for i in items), default=1) or 1
    value_w = max(p.text_width(value_fmt(i["value"]), f) for i in items) + p.u(8)
    label_w = min(max(p.text_width(str(i["label"]), f) for i in items) + p.u(10), w * label_share)
    plot_x0 = x + label_w
    plot_w = max(w - label_w - value_w, p.u(20))
    row_h = min(p.u(26), (y + h - top) / len(items))
    thick = min(p.u(18), row_h * 0.62)
    for n, it in enumerate(items):
        ry = top + n * row_h
        cy = ry + row_h / 2
        tag = f"bar{n}"
        _hit(p, x, ry, x + w, ry + row_h, tag + " hit")
        p.text(x, cy, p.fit(str(it["label"]), f, label_w - p.u(10)), f, T.INK_2, "w", tag=tag)
        fill = T.value(it.get("color")) or (color if emphasis is None or it["key"] in emphasis else T.BASELINE)
        length = max(abs(it["value"]) / vmax * plot_w, 0)
        if track:           # (part of the row's hit area: hovering it washes it with the row)
            p.bar(plot_x0, cy - thick / 2, plot_x0 + plot_w, cy + thick / 2, T.NEUTRAL, "right", tag=tag + " hit")
        p.bar(plot_x0, cy - thick / 2, plot_x0 + length, cy + thick / 2, fill, "right", tag=tag)
        p.text(plot_x0 + length + p.u(6), cy, value_fmt(it["value"]), f, T.INK, "w", tag=tag)
        it.setdefault("tip", f"{it['label']}\n{value_fmt(it['value'])}")
        _wire(p, tag, it, on_click)
    p.line([(plot_x0, top), (plot_x0, top + row_h * len(items))], T.BASELINE, 1)


# ---------------------------------------------------------------------------------------------------------
# Columns - magnitude along an ordered axis (decades, rating 1-10)
# ---------------------------------------------------------------------------------------------------------
def columns(p: Painter, items, *, box=None, title=None, subtitle=None, value_fmt=fmt_int, color=None,
            on_click=None, label_values="max", emphasis=None, tick_fmt=compact):
    """items: [{label, value, key?, tip?, color?, clickable?}] in axis order. color: the columns' (BLUE by
    default). label_values: 'max' (label the tallest), 'all', 'emphasis' (label the emphasised columns), 'none'.
    tick_fmt: the axis numbers (e.g. 0% 10% 20%)."""
    color = T.value(color) or T.BLUE
    x, y, w, h = _box(p, box)
    items = _items(items)
    top = y + header(p, x, y, w, title, subtitle)
    if not items:
        message(p, "Nothing to show", box=(x, top, w, y + h - top))
        return
    f, fs = p.font(9), p.font(8)
    vmax = max((i["value"] for i in items), default=1) or 1
    ticks = nice_ticks(0, vmax, 4)
    if all(float(i["value"]).is_integer() for i in items) and not all(float(t).is_integer() for t in ticks):
        ticks = list(range(0, math.ceil(vmax) + 1))      # (counts of films or plays: never half a one on the axis)
    axis_w = max(p.text_width(tick_fmt(t), fs) for t in ticks) + p.u(8)
    base_y = y + h - p.line_height(f) - p.u(6)
    # (room for the number over a column that reaches the top line, whatever the font)
    plot_top = top + max(p.u(12), p.line_height(fs) + p.u(4))
    plot_h = max(base_y - plot_top, p.u(10))
    x0 = x + axis_w
    plot_w = w - axis_w
    slot = plot_w / len(items)
    for n in range(len(items)):             # the columns' hover areas first, so the gridlines show over them
        _hit(p, x0 + n * slot, plot_top, x0 + (n + 1) * slot, base_y, f"col{n} hit")
    for t in ticks:
        ty = base_y - t / ticks[-1] * plot_h
        p.line([(x0, ty), (x + w, ty)], T.GRID if t else T.BASELINE, 1)
        p.text(x0 - p.u(6), ty, tick_fmt(t), fs, T.MUTED, "e")
    thick = min(p.u(24), slot * 0.66)
    every = max(1, math.ceil(max(p.text_width(str(i["label"]), fs) for i in items) / (slot * 0.95)))
    peak = max(range(len(items)), key=lambda i: items[i]["value"])
    for n, it in enumerate(items):
        cx = x0 + (n + 0.5) * slot
        tag = f"col{n}"
        top_y = base_y - it["value"] / ticks[-1] * plot_h
        fill = T.value(it.get("color")) or (color if emphasis is None or it["key"] in emphasis else T.BASELINE)
        p.bar(cx - thick / 2, top_y, cx + thick / 2, base_y, fill, "up", tag=tag)
        if label_values == "all" or (label_values == "max" and n == peak) or \
                (label_values == "emphasis" and emphasis is not None and it["key"] in emphasis):
            p.text(cx, top_y - p.u(4), value_fmt(it["value"]), fs, T.INK, "s", tag=tag)
        if n % every == 0:
            p.text(cx, base_y + p.u(4) + p.line_height(fs) / 2, str(it["label"]), fs, T.MUTED, "center", tag=tag)
        it.setdefault("tip", f"{it['label']}\n{value_fmt(it['value'])}")
        _wire(p, tag, it, on_click)


# ---------------------------------------------------------------------------------------------------------
# Diverging bars - above/below a baseline (taste tilts)
# ---------------------------------------------------------------------------------------------------------
def diverging(p: Painter, items, *, box=None, title=None, subtitle=None, value_fmt=fmt_signed,
              pos_color=None, neg_color=None, on_click=None, max_abs=None, legend=None):
    """items: [{label, value, key?, tip?}] - positive bars go right in blue (POSITIVE), negative left in red
    (NEGATIVE). legend: optional (positive label, negative label)."""
    pos_color, neg_color = T.value(pos_color) or T.POSITIVE, T.value(neg_color) or T.NEGATIVE
    x, y, w, h = _box(p, box)
    items = _items(items)
    top = y + header(p, x, y, w, title, subtitle)
    f, fs = p.font(9), p.font(8)
    if legend:
        lx = x
        for label, col in ((legend[0], pos_color), (legend[1], neg_color)):
            p.bar(lx, top + p.u(3), lx + p.u(10), top + p.u(11), col, "right")
            p.text(lx + p.u(14), top + p.u(7), label, fs, T.INK_2, "w")
            lx += p.u(24) + p.text_width(label, fs) + p.u(10)
        top += p.u(18)
    if not items:
        message(p, "Nothing to show", box=(x, top, w, y + h - top))
        return
    m = max_abs or max(abs(i["value"]) for i in items) or 1
    label_w = min(max(p.text_width(str(i["label"]), f) for i in items) + p.u(10), w * 0.36)
    value_w = max(p.text_width(value_fmt(i["value"]), f) for i in items) + p.u(8)
    plot_x0 = x + label_w + value_w
    plot_w = w - label_w - 2 * value_w
    zero = plot_x0 + plot_w / 2
    half = plot_w / 2
    row_h = min(p.u(24), (y + h - top) / len(items))
    thick = min(p.u(16), row_h * 0.62)
    for n, it in enumerate(items):
        ry = top + n * row_h
        cy = ry + row_h / 2
        tag = f"div{n}"
        _hit(p, x, ry, x + w, ry + row_h, tag + " hit")
        p.text(x, cy, p.fit(str(it["label"]), f, label_w - p.u(10)), f, T.INK_2, "w", tag=tag)
        v = it["value"]
        length = min(abs(v) / m, 1) * half
        if v >= 0:
            p.bar(zero, cy - thick / 2, zero + length, cy + thick / 2, T.value(it.get("color")) or pos_color, "right",
                  tag=tag)
            p.text(zero + length + p.u(5), cy, value_fmt(v), f, T.INK, "w", tag=tag)
        else:
            p.bar(zero - length, cy - thick / 2, zero, cy + thick / 2, T.value(it.get("color")) or neg_color, "left",
                  tag=tag)
            p.text(zero - length - p.u(5), cy, value_fmt(v), f, T.INK, "e", tag=tag)
        it.setdefault("tip", f"{it['label']}\n{value_fmt(v)}")
        _wire(p, tag, it, on_click)
    p.line([(zero, top - p.u(2)), (zero, top + row_h * len(items) + p.u(2))], T.BASELINE, 1)


# ---------------------------------------------------------------------------------------------------------
# Waterfall - how a prediction adds up
# ---------------------------------------------------------------------------------------------------------
def _shown(value_fmt, v) -> float | None:
    """A value as the chart prints it (e.g. 0.487 -> 0.49), or None if the format isn't a plain number."""
    try:
        return float(value_fmt(v).replace("−", "-"))
    except (TypeError, ValueError):
        return None


def waterfall(p: Painter, base: float, steps, total: float, *, box=None, title=None, subtitle=None,
              base_label="Starting point", total_label="Predicted", rest=None, rest_label="Everything else",
              value_fmt=fmt_2, scale=(0, 10), base_note=None):
    """steps: [{label, value}] applied in order from `base`; drawn as floating bars on one rating axis.

    The start and total are printed to one decimal and the steps with value_fmt. When the steps do reach the
    total (it isn't capped), 'Everything else' also takes up the rounding, so the printed numbers add up to the
    printed total. base_note: an extra line for the starting point's tooltip (what it is)."""
    x, y, w, h = _box(p, box)
    top = y + header(p, x, y, w, title, subtitle)
    rows = [{"label": base_label, "start": scale[0], "end": base, "kind": "base"}]
    run = base
    for s in _items(steps):
        rows.append({"label": s["label"], "start": run, "end": run + s["value"], "kind": "step",
                     "value": s["value"]})
        run += s["value"]
    rest = rest or 0.0
    shown_rest = rest
    printed = [_shown(value_fmt, r["value"]) for r in rows if r["kind"] == "step"]
    if abs(run + rest - total) < 0.05 and None not in printed:
        # what 'Everything else' must say for the printed numbers to reach the printed total
        need = round(total, 1) - round(base, 1) - sum(printed)
        check = _shown(value_fmt, need)
        if check is not None and abs(check - need) < 0.005:
            shown_rest = need
    rounding = shown_rest - rest
    if abs(shown_rest) >= 0.005:
        rows.append({"label": rest_label, "start": run, "end": run + shown_rest, "kind": "step",
                     "value": shown_rest, "rounding": rounding})
    rows.append({"label": total_label, "start": scale[0], "end": total, "kind": "total"})
    f, fs, fb = p.font(9), p.font(8), p.font(9, "bold")
    lo = scale[0]
    hi = max(scale[1], max(max(r["start"], r["end"]) for r in rows))
    # the start and total rows are in bold, so measure them in bold - or 'Predicted for you' gets cut short
    label_w = min(max(p.text_width(r["label"], fb if r["kind"] != "step" else f) for r in rows) + p.u(10),
                  w * 0.4)
    value_w = p.text_width("+0.00", f) + p.u(10)
    plot_x0 = x + label_w
    plot_w = w - label_w - value_w
    axis_h = p.line_height(fs) + p.u(6)
    row_h = min(p.u(24), (y + h - top - axis_h) / len(rows))
    thick = min(p.u(16), row_h * 0.62)
    xs = lambda v: plot_x0 + (v - lo) / (hi - lo) * plot_w
    bottom = top + row_h * len(rows)
    for n in range(len(rows)):              # the rows' hover areas first, so the gridlines show over them
        _hit(p, x, top + n * row_h, x + w, top + (n + 1) * row_h, f"wf{n} hit")
    for t in nice_ticks(lo, hi, 5):
        if t > hi + 1e-9:
            continue
        p.line([(xs(t), top), (xs(t), bottom)], T.GRID, 1)
        p.text(xs(t), bottom + p.u(4) + p.line_height(fs) / 2, compact(t), fs, T.MUTED, "center")
    prev_end = None
    for n, r in enumerate(rows):
        cy = top + (n + 0.5) * row_h
        tag = f"wf{n}"
        bold = r["kind"] in ("base", "total")
        lf = fb if bold else f
        p.text(x, cy, p.fit(r["label"], lf, label_w - p.u(10)), lf, T.INK if bold else T.INK_2, "w", tag=tag)
        a, b = sorted((r["start"], r["end"]))
        if r["kind"] == "step":
            value = r["value"]
            color = T.POSITIVE if value >= 0 else T.NEGATIVE
            p.bar(xs(a), cy - thick / 2, xs(b) if xs(b) - xs(a) >= 1 else xs(a) + 1, cy + thick / 2, color,
                  "right" if value >= 0 else "left", tag=tag)
            text = value_fmt(value)
            if value >= 0:
                text = "+" + text.lstrip("+")
            p.text(xs(max(a, b)) + p.u(5), cy, text, f, T.INK, "w", tag=tag)
            note = "\nIncluding the rounding of the numbers above, so they add up" \
                if abs(r.get("rounding", 0)) >= 0.005 else ""
            p.tip(tag, f"{r['label']}\n{text} points{note}")
        else:
            p.bar(xs(a), cy - thick / 2, xs(b), cy + thick / 2, T.TOTAL if r["kind"] == "total" else T.BASELINE,
                  "right", tag=tag)
            p.text(xs(b) + p.u(5), cy, f"{r['end']:.1f}", fb, T.INK, "w", tag=tag)
            note = f"\n{base_note}" if base_note and r["kind"] == "base" else ""
            p.tip(tag, f"{r['label']}\n{r['end']:.2f}{note}")
        if prev_end is not None and r["kind"] != "total":      # connector from the previous bar's end
            p.line([(xs(prev_end), cy - row_h + thick / 2), (xs(prev_end), cy - thick / 2)], T.BASELINE, 1)
        prev_end = r["end"]


# ---------------------------------------------------------------------------------------------------------
# Stacked bar - part to whole (verdict shares), with a legend
# ---------------------------------------------------------------------------------------------------------
def stacked(p: Painter, segments, *, box=None, title=None, subtitle=None, on_click=None, value_fmt=fmt_int,
            bar_height=18):
    """segments: [{label, value, color, key?, tip?, icon?}] -> one 100% bar and a legend with counts."""
    x, y, w, h = _box(p, box)
    segs = [s for s in _items(segments) if s["value"] > 0]
    top = y + header(p, x, y, w, title, subtitle)
    total = sum(s["value"] for s in segs)
    if not total:
        message(p, "Nothing to show", box=(x, top, w, y + h - top))
        return
    thick = p.u(bar_height)
    gap = p.u(2)
    f, fb = p.font(9), p.font(9, "bold")
    cx = x
    for n, s in enumerate(segs):
        seg_w = s["value"] / total * w
        tag = f"stk{n}"
        x1 = cx + seg_w - (gap if n < len(segs) - 1 else 0)
        corners = (n == 0, n == len(segs) - 1, n == len(segs) - 1, n == 0)
        r = min(p.u(4), thick / 2, max(x1 - cx, 0) / 2)
        if x1 - cx >= 1:
            if r >= 1:
                p.round_rect(cx, top, x1, top + thick, r, T.value(s["color"]), corners, tag=tag)
            else:
                p.rect(cx, top, x1, top + thick, T.value(s["color"]), tag=tag)
        share = s["value"] / total
        s.setdefault("tip", f"{s['label']}\n{value_fmt(s['value'])} ({share:.0%})")
        _wire(p, tag, s, on_click)
        cx += seg_w
    # legend: swatch, label, count and share - never colour alone
    ly = top + thick + p.u(12)
    lx = x
    for n, s in enumerate(segs):
        tag = f"stk{n}"
        share = s["value"] / total
        label = f"{s.get('icon', '')} {s['label']}".strip()
        text = f"{value_fmt(s['value'])}  ({share:.0%})"
        need = p.u(16) + p.text_width(label, fb) + p.u(6) + p.text_width(text, f) + p.u(18)
        if lx + need > x + w and lx > x:
            lx = x
            ly += p.line_height(f) + p.u(6)
        p.round_rect(lx, ly - p.u(5), lx + p.u(10), ly + p.u(5), p.u(2), T.value(s["color"]), tag=tag)
        p.text(lx + p.u(16), ly, label, fb, T.INK, "w", tag=tag)
        tx = lx + p.u(16) + p.text_width(label, fb) + p.u(6)
        p.text(tx, ly, text, f, T.INK_2, "w", tag=tag)
        lx = tx + p.text_width(text, f) + p.u(18)


# ---------------------------------------------------------------------------------------------------------
# Small multiples of short bar panels - comparing methods on several measures
# ---------------------------------------------------------------------------------------------------------
def metric_panels(p: Painter, panels, *, box=None, title=None, subtitle=None):
    """panels: [{title, note, rows: [{label, value, emphasis}], fmt, max}] side by side; the emphasised row
    (the thing being judged) is in the accent colour, the rest grey."""
    x, y, w, h = _box(p, box)
    top = y + header(p, x, y, w, title, subtitle)
    if not panels:
        message(p, "Nothing to show", box=(x, top, w, y + h - top))
        return
    gap = p.u(18)
    pw = (w - gap * (len(panels) - 1)) / len(panels)
    for i, panel in enumerate(panels):
        px = x + i * (pw + gap)
        items = [{"label": r["label"], "value": r["value"], "key": r["label"],
                  "color": T.BLUE if r.get("emphasis") else T.BASELINE} for r in panel["rows"]]
        bars(p, items, box=(px, top, pw, y + h - top), title=panel["title"], subtitle=panel.get("note"),
             value_fmt=panel.get("fmt", fmt_2), max_value=panel.get("max"), label_share=0.5)


# ---------------------------------------------------------------------------------------------------------
# The end of a film: film, credits stretches and the scenes between them
# ---------------------------------------------------------------------------------------------------------
def _timeline_labels(p: Painter, scenes, xs, x, w):
    """Where each scene's label goes: rows of non-overlapping labels (row 0 sits just above the track)."""
    fb, fs = p.font(9, "bold"), p.font(8)
    placed = []
    for s in scenes:
        label = clock(s["start_sec"])
        length = s["end_sec"] - s["start_sec"]
        mins, secs = divmod(int(round(length)), 60)
        sub = f"{secs} s" if not mins else f"{mins} min" + (f" {secs} s" if secs else "")
        sub += " · " + ("likely" if s["verdict"] in ("Likely", "Yes") else "maybe")
        lw = max(p.text_width(label, fb), p.text_width(sub, fs))
        mid = (xs(s["start_sec"]) + xs(s["end_sec"])) / 2
        lx = min(max(mid, x + lw / 2 + p.u(2)), x + w - lw / 2 - p.u(4))
        row = 0
        while any(r == row and abs(lx - px) < (lw + pw) / 2 + p.u(8) for px, pw, r, *_ in placed):
            row += 1
        placed.append((lx, lw, row, label, sub, mid))
    return placed


def timeline(p: Painter, duration: float, stretches, scenes, credits_start: float, *, box=None, title=None,
             subtitle=None, lead_in: float = 90):
    """Seconds throughout. stretches: [{start_sec, end_sec, counted}]; scenes: [{start_sec, end_sec, verdict,
    kind?, why?}]. Shows the end of the film, from `lead_in` seconds before the credits to the end of the file:
    the film in a pale wash, the credits in grey, and each scene in its verdict colour with its start time."""
    x, y, w, h = _box(p, box)
    top = y + header(p, x, y, w, title, subtitle)
    if not duration:
        message(p, "No credits markers for this film", box=(x, top, w, y + h - top))
        return
    fb, fs = p.font(9, "bold"), p.font(8)
    starts = [credits_start] + [s["start_sec"] for s in stretches if s.get("counted", True)]
    start = max(0.0, min(starts) - lead_in)
    span_s = max(duration - start, 1.0)
    xs = lambda t: x + (min(max(t, start), duration) - start) / span_s * w
    placed = _timeline_labels(p, scenes, xs, x, w)
    rows = max((r for _, _, r, *_ in placed), default=-1) + 1
    label_h = p.line_height(fb) + p.line_height(fs) + p.u(6)
    track_y0 = top + rows * label_h + (p.u(8) if rows else 0)
    track_y1 = track_y0 + p.u(22)
    film_color = T.FILM_SPAN
    faint = T.mix(T.BASELINE, T.WASH_BASE, 0.55)          # credits that aren't really credits
    p.rect(xs(start), track_y0, xs(duration), track_y1, film_color, tag="film")
    p.tip("film", "The film", highlight=False)
    shown = lambda s: s["end_sec"] > start and s["start_sec"] < duration      # in the part of the film drawn
    for n, s in enumerate(stretches):
        if not shown(s):
            continue
        tag = f"cr{n}"
        counted = s.get("counted", True)
        color = T.BASELINE if counted else faint
        p.rect(xs(s["start_sec"]), track_y0, xs(s["end_sec"]), track_y1, color, tag=tag)
        what = "Credits" if counted else "Marked as credits - but the film carries on after it (on-screen text?)"
        p.tip(tag, f"{what}\n{clock(s['start_sec'])} - {clock(s['end_sec'])}")
    for n, (s, (lx, lw, row, label, sub, mid)) in enumerate(zip(scenes, placed)):
        tag = f"sc{n}"
        color = T.GOOD if s["verdict"] in ("Likely", "Yes") else T.WARNING
        x0 = xs(s["start_sec"])
        x1 = max(xs(s["end_sec"]), x0 + p.u(3))
        p.rect(x0, track_y0 - p.u(3), x1, track_y1 + p.u(3), color, tag=tag)
        band_top = top + (rows - 1 - row) * label_h
        p.text(lx, band_top + p.line_height(fb) / 2, label, fb, T.INK, "center", tag=tag)
        p.text(lx, band_top + p.line_height(fb) + p.line_height(fs) / 2, sub, fs, T.INK_2, "center", tag=tag)
        p.line([(lx, band_top + p.line_height(fb) + p.line_height(fs) + p.u(1)), (mid, track_y0 - p.u(4))],
               T.BASELINE, 1)
        p.tip(tag, f"{s.get('kind', 'Scene')} - {s['verdict']}\n{clock(s['start_sec'])} - {clock(s['end_sec'])}"
                   + (f"\n{s['why']}" if s.get("why") else ""))
    # time axis: ticks on whole seconds-steps that read cleanly (30 s, 1, 2, 5, 10 minutes...)
    axis_y = track_y1 + p.u(6)
    label_w = p.text_width("0:00:00", fs) + p.u(10)
    fits = max(int(w // label_w), 2)
    step = next((st for st in (30, 60, 120, 300, 600, 900, 1800, 3600) if span_s / st <= fits), 3600)
    show_seconds = step < 60
    t = math.ceil(start / step) * step
    marks = []                  # [centre, half width, text, pushed in from the edge]
    while t <= duration + 0.5:
        tx = xs(t)
        text = clock(t) if show_seconds else clock(t)[:-3]
        half = p.text_width(text, fs) / 2
        p.line([(tx, track_y1), (tx, track_y1 + p.u(3))], T.BASELINE, 1)
        cx = min(max(tx, x + half), x + w - half)          # labels at the very ends are pushed in to fit...
        marks.append([cx, half, text, abs(cx - tx) > 0.5])
        t += step
    kept = []                   # ...so one can land on its neighbour: then only one of them gets a label
    for m in marks:
        if kept and m[0] - m[1] < kept[-1][0] + kept[-1][1] + p.u(6):
            if kept[-1][3] and not m[3]:
                kept[-1] = m    # drop the one that was pushed off its tick
            continue
        kept.append(m)
    for cx, _half, text, _pushed in kept:
        p.text(cx, axis_y + p.line_height(fs) / 2, text, fs, T.MUTED, "center")
    # legend (only the keys this film needs), wrapping when narrow
    ly = axis_y + p.line_height(fs) + p.u(12)
    lx = x
    keys = [("Film", film_color), ("Credits", T.BASELINE)]
    if any(not s.get("counted", True) and shown(s) for s in stretches):
        keys.append(("Not really credits", faint))
    if any(s["verdict"] in ("Likely", "Yes") for s in scenes):
        keys.append(("Likely scene", T.GOOD))
    if any(s["verdict"] not in ("Likely", "Yes") for s in scenes):
        keys.append(("Maybe a scene", T.WARNING))
    for label, color in keys:
        need = p.u(15) + p.text_width(label, fs) + p.u(16)
        if lx + need > x + w and lx > x:
            lx = x
            ly += p.line_height(fs) + p.u(6)
        p.rect(lx, ly - p.u(5), lx + p.u(10), ly + p.u(5), color)
        p.text(lx + p.u(15), ly, label, fs, T.INK_2, "w")
        lx += need


def timeline_height(s: float, label_rows: int = 1, title: bool = True) -> int:
    """Height a timeline needs: title, rows of scene labels, the track, the axis and the legend."""
    return int(((38 if title else 0) + 34 * max(label_rows, 0) + 8 + 22 + 22 + 28) * s)


# ---------------------------------------------------------------------------------------------------------
# Six degrees: the chain of films linking two people
# ---------------------------------------------------------------------------------------------------------
def chain(p: Painter, steps, *, box=None, title=None, subtitle=None, on_person=None, on_film=None,
          film_tip="Click to open its page"):
    """steps: [{from, to, from_id, to_id, film: {title, year}, from_role, to_role}] - drawn top to bottom:
    person, the film that links them, the next person... film_tip: what a click on a film does (with on_film)."""
    x, y, w, h = _box(p, box)
    top = y + header(p, x, y, w, title, subtitle)
    if not steps:
        message(p, "No chain to show", box=(x, top, w, y + h - top))
        return
    people = [(steps[0]["from"], steps[0].get("from_id"))] + [(s["to"], s.get("to_id")) for s in steps]
    fb, f, fs = p.font(10, "bold"), p.font(9), p.font(8)
    pill_h = p.u(30)
    link_h = max(p.u(46), (y + h - top - pill_h * len(people)) / max(len(steps), 1))
    link_h = min(link_h, p.u(64))
    pill_w = min(max(p.text_width(n, fb) for n, _ in people) + p.u(34), w * 0.45)
    px = x + p.u(4)
    cx = px + pill_w / 2
    for i, (name, pid) in enumerate(people):
        py = top + i * (pill_h + link_h)
        end = i in (0, len(people) - 1)
        tag = f"person{i}"
        fill = T.BLUE if end else T.RAMP[150]
        p.round_rect(px, py, px + pill_w, py + pill_h, pill_h / 2, fill, tag=tag)
        p.text(cx, py + pill_h / 2, p.fit(name, fb, pill_w - p.u(20)), fb, T.ON_FILL if end else T.INK, "center",
               tag=tag)
        if on_person is not None and pid:
            p.click(tag, lambda pid=pid, name=name: on_person(pid, name))
            p.tip(tag, f"{name}\nClick to see their profile", highlight=True)
        if i < len(steps):
            s = steps[i]
            ly0, ly1 = py + pill_h, py + pill_h + link_h
            p.line([(cx, ly0 + p.u(2)), (cx, ly1 - p.u(2))], T.BASELINE, 2, arrow=True)
            film = s["film"]
            title_text = f"{film['title']} ({film['year']})" if film.get("year") else film["title"]
            tx = cx + p.u(16)
            avail = x + w - tx
            ftag = f"film{i}"
            p.text(tx, (ly0 + ly1) / 2 - p.line_height(f) * 0.55, p.fit(title_text, p.font(9, "bold"), avail),
                   p.font(9, "bold"), T.INK, "w", tag=ftag)
            def as_role(name, role):
                role = role or ""
                return f"{name} ({role})" if not role or role.startswith("billed") else f"{name} as {role}"
            roles = f"{as_role(s['from'], s.get('from_role'))}  ·  {as_role(s['to'], s.get('to_role'))}"
            p.text(tx, (ly0 + ly1) / 2 + p.line_height(fs) * 0.6, p.fit(roles, fs, avail), fs, T.MUTED, "w", tag=ftag)
            if on_film is not None:
                p.click(ftag, lambda film=film: on_film(film))
                p.tip(ftag, f"{title_text}\n{roles}" + (f"\n{film_tip}" if film_tip else ""))


def chain_height(s: float, steps: int) -> int:
    return int((40 + 30 * (steps + 1) + 54 * steps) * s)


# ---------------------------------------------------------------------------------------------------------
# A small network: someone's circle, or a troupe
# ---------------------------------------------------------------------------------------------------------
def layout(nodes, edges, iterations: int = 300, seed: int = 7) -> dict:
    """Force-directed positions in 0..1 (deterministic). nodes: [{id, center?}], edges: [{a, b, weight}]."""
    rng = random.Random(seed)
    ids = [n["id"] for n in nodes]
    if not ids:
        return {}
    pos = {}
    for i, nid in enumerate(ids):
        ang = 2 * math.pi * i / len(ids)
        pos[nid] = [0.5 + 0.35 * math.cos(ang) + rng.uniform(-0.02, 0.02),
                    0.5 + 0.35 * math.sin(ang) + rng.uniform(-0.02, 0.02)]
    centre = next((n["id"] for n in nodes if n.get("center")), None)
    if centre:
        pos[centre] = [0.5, 0.5]
    k = math.sqrt(1.0 / len(ids)) * 0.9
    wmax = max((e.get("weight", 1) for e in edges), default=1) or 1
    temp = 0.1
    for _ in range(iterations):
        disp = {nid: [0.0, 0.0] for nid in ids}
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                dx, dy = pos[a][0] - pos[b][0], pos[a][1] - pos[b][1]
                d = math.hypot(dx, dy) or 1e-4
                force = k * k / d
                disp[a][0] += dx / d * force
                disp[a][1] += dy / d * force
                disp[b][0] -= dx / d * force
                disp[b][1] -= dy / d * force
        for e in edges:
            a, b = e["a"], e["b"]
            if a not in pos or b not in pos:
                continue
            dx, dy = pos[a][0] - pos[b][0], pos[a][1] - pos[b][1]
            d = math.hypot(dx, dy) or 1e-4
            force = d * d / k * (0.4 + 0.6 * e.get("weight", 1) / wmax)
            disp[a][0] -= dx / d * force
            disp[a][1] -= dy / d * force
            disp[b][0] += dx / d * force
            disp[b][1] += dy / d * force
        for nid in ids:
            if nid == centre:
                continue
            dx, dy = disp[nid]
            d = math.hypot(dx, dy) or 1e-4
            pos[nid][0] += dx / d * min(d, temp)
            pos[nid][1] += dy / d * min(d, temp)
            pos[nid][0] = min(max(pos[nid][0], 0.0), 1.0)
            pos[nid][1] = min(max(pos[nid][1], 0.0), 1.0)
        temp = max(temp * 0.985, 0.002)
    # stretch to fill the unit square
    xs_ = [v[0] for v in pos.values()]
    ys_ = [v[1] for v in pos.values()]
    sx = (max(xs_) - min(xs_)) or 1
    sy = (max(ys_) - min(ys_)) or 1
    return {nid: ((v[0] - min(xs_)) / sx, (v[1] - min(ys_)) / sy) for nid, v in pos.items()}


def _spread(boxes, bounds, fixed=None, gap: float = 4.0, rounds: int = 150, order=None):
    """Nudge overlapping boxes apart, in pixels, keeping each inside bounds (x0, y0, x1, y1).

    boxes: [cx, cy, left, right, up, down] - a centre and how far the box reaches each way; the centres are
    moved in place. `fixed` is the index of a box that stays put (the chart's centre person).

    First every overlapping pair is pushed apart a little at a time, which keeps the picture's shape. A tight
    bunch can jam that way (they all go the same way and meet the edge), so then, going through the boxes in
    `order` (default: as given), one that still overlaps a box already settled moves to the nearest free spot."""
    x0, y0, x1, y1 = bounds

    def clamp(b):
        b[0] = min(max(b[0], x0 + b[2]), x1 - b[3]) if b[2] + b[3] <= x1 - x0 else (x0 + x1 - b[3] + b[2]) / 2
        b[1] = min(max(b[1], y0 + b[4]), y1 - b[5]) if b[4] + b[5] <= y1 - y0 else (y0 + y1 - b[5] + b[4]) / 2

    def overlap(a, b):
        """How far a and b overlap each way (counting the gap); both positive when they do."""
        return (min(a[0] + a[3], b[0] + b[3]) - max(a[0] - a[2], b[0] - b[2]) + gap,
                min(a[1] + a[5], b[1] + b[5]) - max(a[1] - a[4], b[1] - b[4]) + gap)

    def clashes(a, others):
        return any(ox > 0 and oy > 0 for ox, oy in (overlap(a, b) for b in others))

    for i, b in enumerate(boxes):
        if i != fixed:
            clamp(b)
    for _ in range(rounds):
        moved = False
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                a, b = boxes[i], boxes[j]
                ox, oy = overlap(a, b)
                if ox <= 0 or oy <= 0:
                    continue
                moved = True
                axis, need = (0, ox) if ox < oy else (1, oy)       # the shorter way out
                away = -1 if (a[axis], i) < (b[axis], j) else 1      # a goes this way, b the other
                share = 0.0 if i == fixed else 1.0 if j == fixed else 0.5
                a[axis] += away * need * share
                b[axis] -= away * need * (1 - share)
                for k in (i, j):
                    if k != fixed:
                        clamp(boxes[k])
        if not moved:
            return
    # Still jammed: settle the boxes one by one, moving any that clash with those settled before it.
    if sum((b[2] + b[3] + gap) * (b[4] + b[5] + gap) for b in boxes) > (x1 - x0) * (y1 - y0):
        return                                            # (there isn't room for everyone anyway)
    order = list(order if order is not None else range(len(boxes)))
    if fixed is not None:
        order = [fixed] + [i for i in order if i != fixed]
    settled = []
    for i in order:
        b = boxes[i]
        if i != fixed and clashes(b, settled):
            spot = _free_spot(b, settled, bounds, gap, clashes)
            if spot is not None:
                b[0], b[1] = spot
        settled.append(b)


def _free_spot(b, settled, bounds, gap, clashes):
    """The free centre for box b nearest to where it is now, or None. The nearest free spot is always where b is
    flush against a settled box's side or the chart's edge (or level with where it is now), so only those
    places are tried - nearest first."""
    x0, y0, x1, y1 = bounds
    lo_x, hi_x, lo_y, hi_y = x0 + b[2], x1 - b[3], y0 + b[4], y1 - b[5]
    if lo_x > hi_x or lo_y > hi_y:
        return None
    eps = 0.01
    xs = {min(max(b[0], lo_x), hi_x), lo_x, hi_x}
    ys = {min(max(b[1], lo_y), hi_y), lo_y, hi_y}
    for s in settled:
        xs.update((s[0] - s[2] - gap - b[3] - eps, s[0] + s[3] + gap + b[2] + eps))     # left or right of s
        ys.update((s[1] - s[4] - gap - b[5] - eps, s[1] + s[5] + gap + b[4] + eps))     # above or below it
    spots = sorted(((cx - b[0]) ** 2 + (cy - b[1]) ** 2, cx, cy)
                   for cx in xs if lo_x <= cx <= hi_x for cy in ys if lo_y <= cy <= hi_y)
    trial = list(b)
    for _d, cx, cy in spots:
        trial[0], trial[1] = cx, cy
        if not clashes(trial, settled):
            return cx, cy
    return None


def network(p: Painter, nodes, edges, *, box=None, title=None, subtitle=None, on_node=None, positions=None,
            edge_label="shared films", max_links: int | None = -1):
    """nodes: [{id, name, weight?, center?}], edges: [{a, b, weight}] - node size follows weight, line width
    follows edge weight. Pass `positions` (from layout()) to keep the picture stable between redraws.
    max_links: -1 (default) thins a dense picture to about 2.5 lines per person (always keeping the centre's own
    lines); None draws every line; a number draws at most that many."""
    x, y, w, h = _box(p, box)
    top = y + header(p, x, y, w, title, subtitle)
    if not nodes:
        message(p, "Nobody to show", box=(x, top, w, y + h - top))
        return
    fs, fb = p.font(8), p.font(8, "bold")
    names = {n["id"]: n["name"] for n in nodes}
    centre = next((n["id"] for n in nodes if n.get("center")), None)
    if max_links is None:
        keep = list(edges)
    else:
        limit = int(len(nodes) * 2.5) if max_links == -1 else max_links
        keep = [e for e in edges if centre is not None and centre in (e["a"], e["b"])]
        rest = sorted((e for e in edges if not (centre is not None and centre in (e["a"], e["b"]))),
                      key=lambda e: -e.get("weight", 1))
        keep += rest[:max(limit - len(keep), 0)]
    hidden = len(edges) - len(keep)
    if hidden:          # said at the top, clear of the node labels
        note = f"{hidden} weaker link{'s' if hidden != 1 else ''} not drawn"
        p.text(x + w, top + p.line_height(fs) / 2, note, fs, T.MUTED, "e")
        top += p.line_height(fs) + p.u(2)
    pos = positions or layout(nodes, keep)
    margin_x = min(p.u(70), w * 0.18)
    margin_y = p.u(18)
    label_h = p.line_height(fs) + p.u(4)
    area = (x + margin_x, top + margin_y, w - 2 * margin_x, y + h - top - 2 * margin_y - label_h)
    wmax = max((e.get("weight", 1) for e in keep), default=1) or 1
    nmax = max((n.get("weight", 1) for n in nodes), default=1) or 1
    # Each person is a dot with their name below it. The layout only knows who is linked to whom, so in a small
    # chart a tight group ends up with names on top of each other: nudge the dot-and-name boxes apart in pixels.
    order = sorted(nodes, key=lambda nd: nd.get("center", False))
    marks, boxes = [], []
    for node in order:
        r = p.u(5 + 9 * math.sqrt((node.get("weight", 1)) / nmax))
        font = fb if node.get("center") else fs
        label = p.fit(node["name"], font, margin_x * 2)
        lw, lh = p.text_width(label, font), p.line_height(font)
        side = max(r + p.u(2), lw / 2 + p.u(3))
        marks.append((node, r, font, label, lw, lh))
        boxes.append([area[0] + pos[node["id"]][0] * area[2], area[1] + pos[node["id"]][1] * area[3],
                      side, side, r + p.u(2), r + p.u(3) + lh])
    centre_at = next((i for i, (node, *_) in enumerate(marks) if node.get("center")), None)
    busiest = sorted(range(len(marks)), key=lambda i: -(marks[i][0].get("weight", 1)))   # keep their places first
    _spread(boxes, (x, top, x + w, y + h), fixed=centre_at, gap=p.u(3), order=busiest)
    at = {node["id"]: (b[0], b[1]) for (node, *_), b in zip(marks, boxes)}
    for n, e in enumerate(sorted(keep, key=lambda e: e.get("weight", 1))):
        if e["a"] in at and e["b"] in at:
            share = e.get("weight", 1) / wmax
            tag = f"edge{n}"
            p.line([at[e["a"]], at[e["b"]]], T.mix(T.GRID, T.INK_2, 0.15 + share * 0.45), 1 + share * 2.5 * p.s,
                   tag=tag)
            p.tip(tag, f"{names.get(e['a'])} & {names.get(e['b'])}\n{e.get('weight', 1)} {edge_label}",
                  highlight=False)
    # every dot first, then every name, so no dot is ever drawn over someone's name
    for n, (node, r, *_) in enumerate(marks):
        cx, cy = at[node["id"]]
        p.circle(cx, cy, r, T.BLUE if node.get("center") else T.RAMP[300], ring=T.SURFACE, tag=f"node{n}")
    for n, (node, r, font, label, lw, lh) in enumerate(marks):
        cx, cy = at[node["id"]]
        tag = f"node{n}"
        ly = cy + r + p.u(3) + lh / 2
        p.rect(cx - lw / 2 - p.u(3), ly - lh / 2, cx + lw / 2 + p.u(3), ly + lh / 2,
               T.SURFACE, tag=tag + " hit")          # a backing so lines don't run through the name
        p.text(cx, ly, label, font, T.INK if node.get("center") else T.INK_2, "center", tag=tag)
        tip = node.get("tip") or f"{node['name']}\n{node.get('weight', '')} films".strip()
        p.tip(tag, tip)
        if on_node is not None:
            p.click(tag, lambda nid=node["id"], name=node["name"]: on_node(nid, name))


# ---------------------------------------------------------------------------------------------------------
# Butterfly - two sides of the collection, per person
# ---------------------------------------------------------------------------------------------------------
def butterfly(p: Painter, items, *, box=None, title=None, subtitle=None, left_title="A", right_title="B",
              left_color=None, right_color=None, on_click=None, value_fmt=fmt_int):
    """items: [{label, left, right, key?, tip?}] - names in the middle, left bars grow left (BLUE), right bars
    right (ORANGE)."""
    left_color, right_color = T.value(left_color) or T.BLUE, T.value(right_color) or T.ORANGE
    x, y, w, h = _box(p, box)
    items = _items(items)
    top = y + header(p, x, y, w, title, subtitle)
    f, fb, fs = p.font(9), p.font(9, "bold"), p.font(8)
    label_w = min(max((p.text_width(str(i["label"]), f) for i in items), default=40) + p.u(16), w * 0.34)
    side_w = (w - label_w) / 2
    mid0, mid1 = x + side_w, x + side_w + label_w
    # the two sides' titles act as the legend
    for tx, text, color, anchor in ((mid0 - p.u(4), left_title, left_color, "e"),
                                    (mid1 + p.u(4), right_title, right_color, "w")):
        tw = p.text_width(text, fb)
        sx = tx - tw - p.u(16) if anchor == "e" else tx
        p.rect(sx, top + p.u(2), sx + p.u(10), top + p.u(12), color)
        p.text(sx + p.u(14), top + p.u(7), text, fb, T.INK, "w")
    top += p.u(22)
    if not items:
        message(p, "Nobody in both", box=(x, top, w, y + h - top))
        return
    vmax = max(max(i["left"], i["right"]) for i in items) or 1
    value_w = p.text_width(value_fmt(vmax), f) + p.u(8)
    row_h = min(p.u(24), (y + h - top) / len(items))
    thick = min(p.u(16), row_h * 0.62)
    for n, it in enumerate(items):
        cy = top + (n + 0.5) * row_h
        tag = f"bf{n}"
        _hit(p, x, cy - row_h / 2, x + w, cy + row_h / 2, tag + " hit")
        p.text((mid0 + mid1) / 2, cy, p.fit(str(it["label"]), f, label_w - p.u(8)), f, T.INK, "center", tag=tag)
        lw = it["left"] / vmax * (side_w - value_w)
        rw = it["right"] / vmax * (side_w - value_w)
        p.bar(mid0 - lw, cy - thick / 2, mid0, cy + thick / 2, left_color, "left", tag=tag)
        p.text(mid0 - lw - p.u(4), cy, value_fmt(it["left"]), f, T.INK_2, "e", tag=tag)
        p.bar(mid1, cy - thick / 2, mid1 + rw, cy + thick / 2, right_color, "right", tag=tag)
        p.text(mid1 + rw + p.u(4), cy, value_fmt(it["right"]), f, T.INK_2, "w", tag=tag)
        it.setdefault("tip", f"{it['label']}\n{left_title}: {value_fmt(it['left'])}   "
                             f"{right_title}: {value_fmt(it['right'])}")
        _wire(p, tag, it, on_click)


# ---------------------------------------------------------------------------------------------------------
# Scatter - two measures of the same films (your rating vs IMDb)
# ---------------------------------------------------------------------------------------------------------
def _side_offsets(points, amount: float, rng: random.Random) -> list[float]:
    """keep_side jitter: one offset per point, added to both x and y. Points at the very same spot are spread
    evenly over the range (each within its own slice of it), so a pile becomes a short line of separate dots."""
    piles: dict = {}
    for n, pt in enumerate(points):
        piles.setdefault((pt["x"], pt["y"]), []).append(n)
    offsets = [0.0] * len(points)
    for members in piles.values():
        k = len(members)
        for i, n in enumerate(members):
            where = rng.random() if k == 1 else rng.uniform(0.25, 0.75)
            offsets[n] = amount * (2 * (i + where) / k - 1)
    return offsets


def scatter(p: Painter, points, *, box=None, title=None, subtitle=None, x_label="", y_label="",
            x_range=(1, 10), y_range=(1, 10), diagonal=True, jitter=0.18, color=None, on_click=None,
            keep_side=False, noun="films"):
    """points: [{x, y, tip?, key?}]. Discrete values get a little (repeatable) jitter so they don't pile up;
    jitter is one amount for both axes or an (x, y) pair, e.g. (0, 0.18) when only y is whole numbers.

    keep_side=True moves each dot by one offset added to both x and y (the larger jitter amount at most), so a dot
    never crosses the x = y line and a dot on it stays on it; points at the very same spot are spread evenly.
    Hovering and clicking go by nearness, not by the dot drawn on top: where several dots are under the pointer
    the tip lists them ("3 films here") and a click offers a menu, so a dot hidden under others is still reachable.
    """
    jx_amount, jy_amount = jitter if isinstance(jitter, (tuple, list)) else (jitter, jitter)
    color = T.value(color) or T.BLUE
    x, y, w, h = _box(p, box)
    top = y + header(p, x, y, w, title, subtitle)
    fs = p.font(8)
    axis_w = p.text_width("10", fs) + p.u(10)
    axis_h = p.line_height(fs) * (2 if x_label else 1) + p.u(8)
    if y_label:
        p.text(x, top + p.line_height(fs) / 2, y_label, fs, T.INK_2, "w")
        top += p.line_height(fs) + p.u(6)
    x0, x1 = x + axis_w, x + w - p.u(6)
    y0, y1 = top + p.u(4), y + h - axis_h
    X = lambda v: x0 + (v - x_range[0]) / (x_range[1] - x_range[0]) * (x1 - x0)
    Y = lambda v: y1 - (v - y_range[0]) / (y_range[1] - y_range[0]) * (y1 - y0)
    for t in range(math.ceil(x_range[0]), int(x_range[1]) + 1):
        p.line([(X(t), y0), (X(t), y1)], T.GRID, 1)
        p.text(X(t), y1 + p.u(4) + p.line_height(fs) / 2, str(t), fs, T.MUTED, "center")
    for t in range(math.ceil(y_range[0]), int(y_range[1]) + 1):
        p.line([(x0, Y(t)), (x1, Y(t))], T.GRID, 1)
        p.text(x0 - p.u(5), Y(t), str(t), fs, T.MUTED, "e")
    p.line([(x0, y1), (x1, y1)], T.BASELINE, 1)
    p.line([(x0, y0), (x0, y1)], T.BASELINE, 1)
    if x_label:
        p.text((x0 + x1) / 2, y + h - p.line_height(fs) / 2, x_label, fs, T.INK_2, "center")
    if diagonal:
        lo = max(x_range[0], y_range[0])
        hi = min(x_range[1], y_range[1])
        p.line([(X(lo), Y(lo)), (X(hi), Y(hi))], T.BASELINE, 1)
    rng = random.Random(11)
    r = p.u(3.5)
    ring = 1.5
    points = list(points)
    side = _side_offsets(points, max(jx_amount, jy_amount), rng) if keep_side else None
    spots = []
    for n, pt in enumerate(points):
        if side is not None:
            jx = jy = side[n]
        else:
            jx = rng.uniform(-jx_amount, jx_amount) if jx_amount else 0.0
            jy = rng.uniform(-jy_amount, jy_amount) if jy_amount else 0.0
        cx, cy = X(pt["x"] + jx), Y(pt["y"] + jy)
        tag = f"pt{n}"
        p.circle(cx, cy, r, T.value(pt.get("color")) or color, ring=T.SURFACE, ring_width=ring, tag=tag)
        spots.append((cx, cy, tag, pt.get("tip") or "", pt.get("key") if on_click is not None else None))
    # hover and click by nearness: within the dot and its ring, plus a pixel of grace
    p.spots(spots, r + p.u(ring) + p.u(1), on_click=on_click, noun=noun)


# ---------------------------------------------------------------------------------------------------------
# Stat tiles - headline numbers
# ---------------------------------------------------------------------------------------------------------
def tiles(p: Painter, items, *, box=None):
    """items: [{label, value, note?}] - label (muted), value (large, proportional figures), optional note."""
    x, y, w, h = _box(p, box)
    items = list(items)
    if not items:
        return
    fl, fv, fn = p.font(9), p.font(17, "bold"), p.font(8)
    min_w = p.u(130)
    per_row = max(1, min(len(items), int(w // min_w)))
    rows = math.ceil(len(items) / per_row)
    tile_w = w / per_row
    tile_h = h / rows
    for i, it in enumerate(items):
        tx = x + (i % per_row) * tile_w
        ty = y + (i // per_row) * tile_h
        if it.get("tip"):
            tag = f"tile{i}"
            p.rect(tx, ty, tx + tile_w, ty + tile_h, T.SURFACE, tag=tag)
            p.tip(tag, it["tip"], highlight=False)
        if i % per_row:
            p.line([(tx, ty + p.u(6)), (tx, ty + tile_h - p.u(6))], T.GRID, 1)
        pad = p.u(12) if i % per_row else 0
        p.text(tx + pad, ty + p.line_height(fl) / 2 + p.u(2), p.fit(it["label"], fl, tile_w - pad - p.u(6)), fl,
               T.MUTED, "w")
        p.text(tx + pad, ty + p.line_height(fl) + p.u(4) + p.line_height(fv) / 2,
               p.fit(str(it["value"]), fv, tile_w - pad - p.u(6)), fv, T.INK, "w")
        if it.get("note"):
            p.text(tx + pad, ty + p.line_height(fl) + p.line_height(fv) + p.u(6) + p.line_height(fn) / 2,
                   p.fit(it["note"], fn, tile_w - pad - p.u(6)), fn, T.INK_2, "w")
