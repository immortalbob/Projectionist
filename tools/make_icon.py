"""Draws Projectionist's icon. A development tool: Pillow is needed here, never by the app itself.

    python tools/make_icon.py                  writes projectionist/assets/projectionist.ico (16, 20, 24, 32, 40,
                                               48, 64, 128 and 256 px) and projectionist-16/32/48/256.png (the
                                               pictures Tk's iconphoto takes)
    python tools/make_icon.py --preview FILE   also a sheet of every size on light and dark backgrounds, with the
                                               small ones blown up pixel by pixel, for checking them by eye
    python tools/make_icon.py --out FOLDER     writes the icon files to another folder

The picture: a film projector - two reels on top, the lens at the front - throwing an amber beam, in pale steel on
a tile of the app's navy (#1F3A5F). The tile keeps it readable on a light taskbar and a dark one alike.

Each size is drawn for itself. From 20 px up, its shapes sit on whole pixels of that size and are drawn 8 times
over, then averaged down, so the edges stay sharp (20 and 24 px have layouts of their own, worked out pixel by
pixel); the 16 px one is drawn pixel by pixel, as anything drawn smoothly only blurs at that size.
"""

from __future__ import annotations

import argparse
import io
import math
import os
import struct
import sys

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "projectionist", "assets")
ICO_NAME = "projectionist.ico"
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
PNG_SIZES = (16, 32, 48, 256)
SS = 8                                      # each size is drawn this many times over, then averaged down

# -- colours --------------------------------------------------------------------------------------------------------
TILE_TOP, TILE_BOTTOM = (44, 79, 125), (23, 43, 72)        # around the app's navy, #1F3A5F
RIM = (255, 255, 255, 34)                   # a faint light edge on the tile (big sizes): it shows on a dark taskbar
STEEL = (230, 236, 243)                     # the reels and the body
STEEL_LIGHT = (246, 248, 251)
STEEL_SHADE = (178, 192, 209)
LENS = (116, 135, 161)
LENS_DARK = (82, 99, 124)
HUB = (21, 39, 66)
GLASS = (255, 222, 132)
BEAM_NEAR, BEAM_FAR = (255, 206, 100), (246, 170, 62)       # amber, warmest at the lens

# -- the picture, on a 32-unit grid (a unit is a pixel of the 32 px icon); boxes are (left, top, right, bottom) -----
TILE = (1, 1, 31, 31)
TILE_RADIUS = 5.5
REAR_REEL = (2, 4, 13, 15)              # the feed reel, at the back: the bigger one
FRONT_REEL = (14, 9, 21, 16)            # the take-up reel, over the lens
BODY = (4, 17, 19, 27)
BODY_RADIUS = 1.5
LENS_BOX = (19, 19, 22, 25)
GLASS_BOX = (22, 19, 23, 25)
BEAM = ((23, 20), (32, 12), (32, 32), (23, 24))        # (clipped to the tile)

# 20 and 24 px: laid out pixel by pixel - the grid scaled down would run the reels into each other and the body,
# and put their hubs between pixels. Their tiles fill the icon, as the 16 px one does: a pixel of tile always
# shows round the big reel (at 24 px a margin put the reel on the tile's edge, cutting into its outline)
HINTED = {
    20: dict(tile=(0, 0, 20, 20), radius=3.5, rear=(1, 2, 8, 9), front=(9, 5, 14, 10), body=(2, 11, 12, 17),
             body_radius=1, lens=(12, 12, 14, 16), glass=(14, 12, 15, 16),
             beam=((15, 12.5), (20, 8), (20, 20), (15, 15.5))),
    24: dict(tile=(0, 0, 24, 24), radius=4, rear=(1, 3, 10, 12), front=(11, 7, 16, 12), body=(3, 13, 14, 21),
             body_radius=1, lens=(14, 14, 16, 20), glass=(16, 14, 17, 20),
             beam=((17, 14.5), (24, 8.5), (24, 25.5), (17, 19.5))),
}

# 16 px, pixel by pixel. n: the tile  ,: its rounded corners  w: steel  v: a reel's rounded edge  h: a reel's hub
# s: steel's shade  L: the lens  g: its glass  a / b / c: the beam, strong / half / faint
PIXELS_16 = [
    ",nnnnnnnnnnnnnn,",
    "nnvwwvnnnnnnnnnn",
    "nvwwwwvnnnnnnnnn",
    "nwwhhwwnvwwvnnnn",
    "nwwhhwwnwwwwnnnn",
    "nvwwwwvnwwwwnnnn",
    "nnvwwvnnvwwvnnnc",
    "nnnnnnnnnnnnnncb",
    "nnwwwwwwwwnnnbaa",
    "nnwwwwwwwwLgaaaa",
    "nnwwwwwwwwLgaaaa",
    "nnwwwwwwwwLgaaaa",
    "nnwwwwwwwwLgaaaa",
    "nnssssssssnnnbaa",
    "nnnnnnnnnnnnnncb",
    ",nnnnnnnnnnnnnn,",
]


def _mix(a, b, t: float):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def _tile_colour(y: float, top: float, bottom: float):
    return _mix(TILE_TOP, TILE_BOTTOM, min(max((y - top) / max(bottom - top, 1), 0.0), 1.0))


def draw_16() -> Image.Image:
    """The 16 px icon, from PIXELS_16."""
    img = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    beam = {"a": 1.0, "b": 0.62, "c": 0.3}
    for y, row in enumerate(PIXELS_16):
        assert len(row) == 16, row
        tile = _tile_colour(y + 0.5, 0, 16)
        for x, ch in enumerate(row):
            if ch == ".":
                continue
            if ch == ",":
                colour = tile + (150,)
            elif ch == "n":
                colour = tile + (255,)
            elif ch == "v":
                colour = _mix(tile, STEEL, 0.5) + (255,)
            elif ch in beam:
                colour = _mix(tile, _mix(BEAM_NEAR, BEAM_FAR, (x - 11) / 4), beam[ch]) + (255,)
            else:
                colour = {"w": STEEL, "h": HUB, "s": STEEL_SHADE, "L": LENS, "g": GLASS}[ch] + (255,)
            img.putpixel((x, y), colour)
    return img


def layout(size: int) -> dict:
    """Where everything goes on the icon of that size, in its pixels (whole pixels, so edges stay sharp)."""
    if size in HINTED:
        return dict(HINTED[size])
    k = size / 32

    def q(v):
        return math.floor(v * k + 0.5)

    def box(b):
        return tuple(q(v) for v in b)

    def reel(b):
        """A reel's box: square, and below 64 px an odd number of pixels across, round a pixel's middle, so its
        hub is a pixel of its own (rounding each edge alone made 40 and 48 px reels ovals, 13 x 14 and 11 x 10)."""
        if size >= 64:
            return box(b)
        across = (b[2] - b[0]) * k
        n = round(across)
        if n % 2 == 0:
            n += 1 if across >= n else -1
        x0 = math.floor((b[0] + b[2]) / 2 * k - n / 2 + 0.5)
        y0 = math.floor((b[1] + b[3]) / 2 * k - n / 2 + 0.5)
        return x0, y0, x0 + n, y0 + n
    margin = q(TILE[0])                         # (the same all round: at 48 px it came out 2 px left, 1 px right)
    return dict(tile=(margin, margin, size - margin, size - margin), radius=TILE_RADIUS * k, rear=reel(REAR_REEL),
                front=reel(FRONT_REEL), body=box(BODY), body_radius=BODY_RADIUS * k, lens=box(LENS_BOX),
                glass=box(GLASS_BOX), beam=tuple((q(x), q(y)) for x, y in BEAM))


def _gradient(n: int, a, b, horizontal: bool, start: float, end: float) -> Image.Image:
    """An RGB (or L, for single numbers) ramp from a to b between start and end, across or down an n x n image."""
    single = not isinstance(a, tuple)
    line = Image.new("L" if single else "RGB", (n, 1) if horizontal else (1, n))
    for i in range(n):
        t = min(max((i + 0.5 - start) / max(end - start, 1), 0.0), 1.0)
        value = round(a + (b - a) * t) if single else _mix(a, b, t)
        line.putpixel((i, 0) if horizontal else (0, i), value)
    return line.resize((n, n), Image.NEAREST)


def _reel(img: Image.Image, box):
    """A reel: a steel disc with holes (the tile shows through) round a dark hub - fewer details as it gets small
    (by its own size in pixels: the front reel is the smaller)."""
    x0, y0, x1, y1 = box
    cx, cy, r = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2
    across = (x1 - x0) / SS                         # (pixels)
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.ellipse(box, fill=STEEL + (255,))
    if across >= 24:                                # a pale rim round the edge
        d.ellipse((x0 + r * 0.08, y0 + r * 0.08, x1 - r * 0.08, y1 - r * 0.08), outline=STEEL_LIGHT + (255,),
                  width=max(1, round(r * 0.05)))
    if across >= 16:
        holes, dist, hole_r, hub_r = 5, 0.56, 0.21, 0.16
    elif across >= 10:                              # (three bold holes: five only speckle at this size)
        holes, dist, hole_r, hub_r = 3, 0.5, 0.27, 0.16
    else:
        holes, dist, hole_r, hub_r = 0, 0, 0, 0.16
    for i in range(holes):
        angle = math.radians(-90 + 360 / holes * i)
        hx, hy = cx + math.cos(angle) * r * dist, cy + math.sin(angle) * r * dist
        d.ellipse((hx - r * hole_r, hy - r * hole_r, hx + r * hole_r, hy + r * hole_r), fill=(0, 0, 0, 0))
    hub = max(r * hub_r, SS * 0.5)                 # (at least a pixel across)
    d.ellipse((cx - hub, cy - hub, cx + hub, cy + hub), fill=HUB + (255,))
    if across >= 16:                                # the spindle
        pin = r * 0.06
        d.ellipse((cx - pin, cy - pin, cx + pin, cy + pin), fill=STEEL_SHADE + (255,))
    img.alpha_composite(layer)


def draw(size: int) -> Image.Image:
    """The icon at one size, as an RGBA image."""
    if size <= 16:
        return draw_16() if size == 16 else draw(32).resize((size, size), Image.LANCZOS)
    lay = layout(size)
    n = size * SS

    def s(v):
        return v * SS

    def box(b):                                     # (Pillow fills a box's right and bottom edges too)
        x0, y0, x1, y1 = b
        return x0 * SS, y0 * SS, x1 * SS - 1, y1 * SS - 1
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    # the tile: navy, lighter at the top
    tile_mask = Image.new("L", (n, n), 0)
    ImageDraw.Draw(tile_mask).rounded_rectangle(box(lay["tile"]), radius=s(lay["radius"]), fill=255)
    img.paste(_gradient(n, TILE_TOP, TILE_BOTTOM, False, s(lay["tile"][1]), s(lay["tile"][3])), (0, 0), tile_mask)
    if size >= 40:
        rim = Image.new("RGBA", (n, n), (0, 0, 0, 0))
        width = max(SS, round(s(size / 128)))
        ImageDraw.Draw(rim).rounded_rectangle(box(lay["tile"]), radius=s(lay["radius"]), outline=RIM, width=width)
        img.alpha_composite(rim)
    # the beam: amber, fading as it leaves the lens, inside the tile
    beam = Image.new("L", (n, n), 0)
    ImageDraw.Draw(beam).polygon([(s(x), s(y)) for x, y in lay["beam"]], fill=255)
    if size >= 64:
        beam = beam.filter(ImageFilter.GaussianBlur(s(size / 256)))
    start, end = s(lay["beam"][0][0]), s(lay["tile"][2])
    beam = ImageChops.multiply(beam, _gradient(n, 255, 190 if size >= 32 else 215, True, start, end))
    beam = ImageChops.multiply(beam, tile_mask)
    img.paste(_gradient(n, BEAM_NEAR, BEAM_FAR, True, start, end), (0, 0), beam)
    if size >= 64:                                  # a warm glow round the lens
        glow = Image.new("L", (n, n), 0)
        gx0, gy0, gx1, gy1 = box(lay["glass"])
        pad = s(size / 40)
        ImageDraw.Draw(glow).ellipse((gx0 - pad, gy0 - pad, gx1 + pad * 1.5, gy1 + pad), fill=110)
        glow = ImageChops.multiply(glow.filter(ImageFilter.GaussianBlur(pad)), tile_mask)
        img.paste(Image.new("RGB", (n, n), GLASS), (0, 0), glow)
    # the reels, joined to the body
    d = ImageDraw.Draw(img)
    if size >= 32:
        for reel in (lay["rear"], lay["front"]):
            cx = (reel[0] + reel[2]) / 2
            half = max(0.5, size / 64)
            d.rectangle((s(cx - half), s((reel[1] + reel[3]) / 2), s(cx + half) - 1, s(lay["body"][1] + 1)),
                        fill=STEEL_SHADE)
    for reel in (lay["rear"], lay["front"]):
        _reel(img, box(reel))
    # the body: steel, shaded along the bottom
    d = ImageDraw.Draw(img)
    bx0, by0, bx1, by1 = box(lay["body"])
    radius = s(lay["body_radius"])
    shade = max(SS, round(s(size / 32)))
    d.rounded_rectangle((bx0, by0, bx1, by1), radius=radius, fill=STEEL_SHADE)
    d.rounded_rectangle((bx0, by0, bx1, by1 - shade), radius=radius, fill=STEEL)
    if size >= 48:                                  # a highlight along the top, and vents
        d.rectangle((bx0 + radius, by0, bx1 - radius, by0 + max(SS, round(s(size / 96)))), fill=STEEL_LIGHT)
        if size >= 64:
            unit = s(size / 32)
            for i in range(3):
                vx = bx0 + unit * (2.2 + i * 1.6)
                d.rounded_rectangle((vx, by0 + unit * 2.6, vx + unit * 0.7, by1 - shade - unit * 2.2),
                                    radius=unit * 0.35, fill=STEEL_SHADE)
    # the lens, lit
    lx0, ly0, lx1, ly1 = box(lay["lens"])
    d.rectangle((lx0, ly0, lx1, ly1), fill=LENS)
    if size >= 48:
        d.rectangle((lx0, ly1 - shade + 1, lx1, ly1), fill=LENS_DARK)
    d.rectangle(box(lay["glass"]), fill=GLASS)
    return img.reduce(SS)


# -- the .ico file ------------------------------------------------------------------------------------------------
def _dib(img: Image.Image) -> bytes:
    """One frame of an .ico as a 32-bit bitmap - what Windows and Tk read at every size below 256."""
    w, h = img.size
    rows = img.tobytes("raw", "BGRA")
    xor = b"".join(rows[y * w * 4:(y + 1) * w * 4] for y in reversed(range(h)))      # bottom row first
    stride = (w + 31) // 32 * 4
    alpha = img.getchannel("A").load()
    mask = bytearray()
    for y in reversed(range(h)):
        line = bytearray(stride)
        for x in range(w):
            if alpha[x, y] == 0:                    # (the AND mask: fully see-through pixels)
                line[x // 8] |= 0x80 >> (x % 8)
        mask += line
    header = struct.pack("<IiiHHIIiiII", 40, w, h * 2, 1, 32, 0, len(xor) + len(mask), 0, 0, 0, 0)
    return header + xor + bytes(mask)


def ico_bytes(frames: dict[int, Image.Image]) -> bytes:
    """An .ico holding each frame: bitmaps below 256 px, a PNG at 256 (as Windows' own icons are)."""
    sizes = sorted(frames)
    blobs = []
    for size in sizes:
        img = frames[size].convert("RGBA")
        if size >= 256:
            buf = io.BytesIO()
            img.save(buf, "PNG", optimize=True)
            blobs.append(buf.getvalue())
        else:
            blobs.append(_dib(img))
    out = bytearray(struct.pack("<HHH", 0, 1, len(sizes)))
    offset = 6 + 16 * len(sizes)
    for size, blob in zip(sizes, blobs):
        side = 0 if size >= 256 else size           # (0 means 256)
        out += struct.pack("<BBBBHHII", side, side, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
    for blob in blobs:
        out += blob
    return bytes(out)


def write_icons(folder: str = ASSETS) -> dict[int, Image.Image]:
    """projectionist.ico and projectionist-<size>.png in the folder. -> the frames drawn."""
    os.makedirs(folder, exist_ok=True)
    frames = {size: draw(size) for size in ICO_SIZES}
    with open(os.path.join(folder, ICO_NAME), "wb") as f:
        f.write(ico_bytes(frames))
    for size in PNG_SIZES:
        frames[size].save(os.path.join(folder, f"projectionist-{size}.png"), optimize=True)
    return frames


# -- the preview sheet --------------------------------------------------------------------------------------------
def _font(size: int):
    for name in ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def preview(frames: dict[int, Image.Image], path: str):
    """Every size at its real size on light and dark backgrounds (Windows' and the app's looks), the small ones
    blown up pixel by pixel, and the icon where Windows shows it: a title bar and the taskbar."""
    backgrounds = [("White", (255, 255, 255)), ("Windows light", (243, 243, 243)), ("Graphite", (28, 29, 32)),
                   ("Windows dark", (32, 32, 32)), ("Booth", (11, 19, 32))]
    label, small = _font(15), _font(12)
    pad, band_h = 18, 290
    sizes = sorted(frames)
    zooms = [(16, 12), (20, 10), (24, 8), (32, 6), (48, 4)]
    zoom_h = max(z * s for s, z in zooms) + 50
    width = pad + 150 + sum(size + 26 for size in sizes) + pad
    height = pad + 40 + len(backgrounds) * (band_h + 10) + 2 * (zoom_h + 10) + 90 + pad
    sheet = Image.new("RGB", (max(width, 1480), height), (236, 236, 236))
    d = ImageDraw.Draw(sheet)
    d.text((pad, pad), "Projectionist - the icon at every size (tools/make_icon.py)", fill=(20, 20, 20), font=_font(20))
    y = pad + 40
    # every size, actual pixels, on each background
    for name, bg in backgrounds:
        d.rectangle((pad, y, sheet.width - pad, y + band_h), fill=bg)
        ink = (230, 230, 230) if sum(bg) < 300 else (30, 30, 30)
        d.text((pad + 8, y + 6), name, fill=ink, font=label)
        x = pad + 150
        for size in sizes:
            sheet.paste(frames[size], (x, y + band_h - 12 - size), frames[size])
            d.text((x, y + band_h - 12 - size - 18), str(size), fill=ink, font=small)
            x += size + 26
        y += band_h + 10
    # the small sizes, blown up
    for name, bg in (backgrounds[1], backgrounds[2]):
        d.rectangle((pad, y, sheet.width - pad, y + zoom_h), fill=bg)
        ink = (230, 230, 230) if sum(bg) < 300 else (30, 30, 30)
        d.text((pad + 8, y + 6), f"{name}, blown up", fill=ink, font=label)
        x = pad + 150
        for size, zoom in zooms:
            big = frames[size].resize((size * zoom, size * zoom), Image.NEAREST)
            sheet.paste(big, (x, y + 24), big)
            d.text((x, y + 24 + size * zoom + 2), f"{size} px x{zoom}", fill=ink, font=small)
            x += size * zoom + 30
        y += zoom_h + 10
    # where Windows shows it: title bars (16 px) and the taskbar (32 px drawn at 24, as Windows does)
    x = pad
    for name, bar, ink in (("Title bar, light", (255, 255, 255), (0, 0, 0)),
                           ("Title bar, Graphite", (15, 16, 18), (236, 238, 241)),
                           ("Taskbar, light", (243, 243, 243), (0, 0, 0)),
                           ("Taskbar, dark", (32, 32, 32), (255, 255, 255))):
        d.rectangle((x, y, x + 340, y + 48), fill=bar)
        if name.startswith("Title"):
            sheet.paste(frames[16], (x + 10, y + 16), frames[16])
            d.text((x + 34, y + 15), "Projectionist 1.0.0", fill=ink, font=small)
        else:
            icon = frames[32].resize((24, 24), Image.LANCZOS)
            for i, other in enumerate(((0, 120, 212), (230, 130, 40))):
                d.rounded_rectangle((x + 10 + i * 44, y + 12, x + 34 + i * 44, y + 36), radius=4, fill=other)
            sheet.paste(icon, (x + 98, y + 12), icon)
            d.rectangle((x + 100, y + 44, x + 120, y + 46), fill=(96, 205, 255))
        d.text((x, y + 52), name, fill=(40, 40, 40), font=small)
        x += 360
    sheet.save(path)
    return path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Draw Projectionist's icon (.ico and PNGs).")
    parser.add_argument("--out", default=ASSETS, help="folder to write the icon files to (default: %(default)s)")
    parser.add_argument("--preview", metavar="FILE", help="also save a preview sheet of every size (PNG)")
    args = parser.parse_args(argv)
    frames = write_icons(args.out)
    print(f"Wrote {ICO_NAME} ({', '.join(map(str, ICO_SIZES))} px) and "
          f"{', '.join(f'projectionist-{s}.png' for s in PNG_SIZES)} in {args.out}")
    if args.preview:
        print(f"Preview: {preview(frames, args.preview)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
