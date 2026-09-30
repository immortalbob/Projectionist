"""Read movie metadata out of a Plex Media Server library database.

This module only ever reads. The database is opened with SQLite's "immutable"
flag, or - when it has an un-checkpointed write-ahead log, or lives somewhere
SQLite can't open by URI (e.g. a network share) - copied to a temp folder first.
The file you point it at is never modified, so it is safe to use on one of
Plex's automatic backups, a downloaded dump, or even the live database.
"""

from __future__ import annotations

import json
import ntpath
import os
import posixpath
import re
import shutil
import sqlite3
import tempfile
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qsl, unquote, urlparse

from . import APP_NAME, __version__
from . import credits as credit_scenes
from . import sheets as S
from . import smart as smart_filters
from .sheets import LIST_SEP, Sheet

Progress = Callable[[float, str], None]


class PlexDBError(Exception):
    """The chosen file can't be used as a Plex library database."""


class Cancelled(Exception):
    """The user cancelled the export."""


# ---------------------------------------------------------------------------
# Plex constants
# ---------------------------------------------------------------------------
METADATA_MOVIE = 1
METADATA_EXTRA = 12
METADATA_COLLECTION = 18
SECTION_MOVIE = 1

# tags.tag_type values (same numbering as python-plexapi's utils.TAG_TYPES)
TAG_GENRE, TAG_COLLECTION, TAG_DIRECTOR, TAG_WRITER, TAG_ROLE, TAG_PRODUCER = 1, 2, 4, 5, 6, 7
TAG_COUNTRY, TAG_CHAPTER, TAG_REVIEW, TAG_LABEL, TAG_MARKER = 8, 9, 10, 11, 12
TAG_POSTER, TAG_ART, TAG_GUID, TAG_RATING, TAG_STUDIO = 312, 313, 314, 316, 318
TAG_CLEAR_LOGO, TAG_ADVISORY, TAG_SQUARE_ART = 323, 324, 325

HANDLED_TAG_TYPES = {
    TAG_GENRE, TAG_COLLECTION, TAG_DIRECTOR, TAG_WRITER, TAG_ROLE, TAG_PRODUCER, TAG_COUNTRY,
    TAG_CHAPTER, TAG_REVIEW, TAG_LABEL, TAG_MARKER, TAG_POSTER, TAG_ART, TAG_GUID, TAG_RATING,
    TAG_STUDIO, TAG_CLEAR_LOGO, TAG_ADVISORY, TAG_SQUARE_ART,
    42,    # mediaProcessingTarget - internal bookkeeping
}
OTHER_TAG_NAMES = {
    0: "Tag", 3: "Tag", 300: "Mood", 301: "Style", 302: "Format", 305: "Similar", 306: "Concert",
    311: "Banner", 317: "Theme", 319: "Network", 322: "Show Ordering", 400: "Location", 410: "Place",
}

EXTRA_TYPES = {
    1: "Trailer", 2: "Deleted Scene", 3: "Interview", 4: "Music Video", 5: "Behind the Scenes",
    6: "Scene", 7: "Live Music Video", 8: "Lyric Music Video", 9: "Concert", 10: "Featurette",
    11: "Short", 12: "Other",
}

STREAM_TYPES = {1: "Video", 2: "Audio", 3: "Subtitle", 4: "Lyrics"}

RATING_SOURCES = {"imdb": "IMDb", "rottentomatoes": "Rotten Tomatoes", "themoviedb": "TMDb", "tmdb": "TMDb"}
RATING_VERDICTS = {"ripe": "Fresh", "fresh": "Fresh", "rotten": "Rotten", "upright": "Upright",
                   "spilled": "Spilled", "certified": "Certified Fresh"}

LANGUAGES = {
    "af": "Afrikaans", "sq": "Albanian", "am": "Amharic", "ar": "Arabic", "hy": "Armenian", "az": "Azerbaijani",
    "eu": "Basque", "be": "Belarusian", "bn": "Bengali", "bs": "Bosnian", "bg": "Bulgarian", "my": "Burmese",
    "ca": "Catalan", "zh": "Chinese", "hr": "Croatian", "cs": "Czech", "da": "Danish", "nl": "Dutch",
    "en": "English", "eo": "Esperanto", "et": "Estonian", "fo": "Faroese", "fil": "Filipino", "fi": "Finnish",
    "fr": "French", "gl": "Galician", "ka": "Georgian", "de": "German", "el": "Greek", "gu": "Gujarati",
    "ht": "Haitian Creole", "ha": "Hausa", "he": "Hebrew", "hi": "Hindi", "hu": "Hungarian", "is": "Icelandic",
    "id": "Indonesian", "ga": "Irish", "it": "Italian", "ja": "Japanese", "kn": "Kannada", "kk": "Kazakh",
    "km": "Khmer", "ko": "Korean", "ku": "Kurdish", "ky": "Kyrgyz", "lo": "Lao", "la": "Latin", "lv": "Latvian",
    "lt": "Lithuanian", "lb": "Luxembourgish", "mk": "Macedonian", "ms": "Malay", "ml": "Malayalam",
    "mt": "Maltese", "mi": "Maori", "mr": "Marathi", "mn": "Mongolian", "ne": "Nepali", "no": "Norwegian",
    "nb": "Norwegian Bokmal", "nn": "Norwegian Nynorsk", "fa": "Persian", "pl": "Polish", "pt": "Portuguese",
    "pa": "Punjabi", "ro": "Romanian", "ru": "Russian", "sr": "Serbian", "si": "Sinhala", "sk": "Slovak",
    "sl": "Slovenian", "so": "Somali", "es": "Spanish", "sw": "Swahili", "sv": "Swedish", "tl": "Tagalog",
    "tg": "Tajik", "ta": "Tamil", "te": "Telugu", "th": "Thai", "bo": "Tibetan", "tr": "Turkish",
    "uk": "Ukrainian", "ur": "Urdu", "uz": "Uzbek", "vi": "Vietnamese", "cy": "Welsh", "xh": "Xhosa",
    "yi": "Yiddish", "yo": "Yoruba", "zu": "Zulu", "cn": "Cantonese", "yue": "Cantonese", "cmn": "Mandarin",
}
# ISO 639-2 three-letter codes (both bibliographic and terminology forms) -> two-letter code
_ISO3 = {
    "afr": "af", "alb": "sq", "sqi": "sq", "amh": "am", "ara": "ar", "arm": "hy", "hye": "hy", "aze": "az",
    "baq": "eu", "eus": "eu", "bel": "be", "ben": "bn", "bos": "bs", "bul": "bg", "bur": "my", "mya": "my",
    "cat": "ca", "chi": "zh", "zho": "zh", "hrv": "hr", "cze": "cs", "ces": "cs", "dan": "da", "dut": "nl",
    "nld": "nl", "eng": "en", "epo": "eo", "est": "et", "fao": "fo", "fin": "fi", "fre": "fr", "fra": "fr",
    "glg": "gl", "geo": "ka", "kat": "ka", "ger": "de", "deu": "de", "gre": "el", "ell": "el", "guj": "gu",
    "hat": "ht", "hau": "ha", "heb": "he", "hin": "hi", "hun": "hu", "ice": "is", "isl": "is", "ind": "id",
    "gle": "ga", "ita": "it", "jpn": "ja", "kan": "kn", "kaz": "kk", "khm": "km", "kor": "ko", "kur": "ku",
    "kir": "ky", "lao": "lo", "lat": "la", "lav": "lv", "lit": "lt", "ltz": "lb", "mac": "mk", "mkd": "mk",
    "may": "ms", "msa": "ms", "mal": "ml", "mlt": "mt", "mao": "mi", "mri": "mi", "mar": "mr", "mon": "mn",
    "nep": "ne", "nor": "no", "nob": "nb", "nno": "nn", "per": "fa", "fas": "fa", "pol": "pl", "por": "pt",
    "pan": "pa", "rum": "ro", "ron": "ro", "rus": "ru", "srp": "sr", "sin": "si", "slo": "sk", "slk": "sk",
    "slv": "sl", "som": "so", "spa": "es", "swa": "sw", "swe": "sv", "tgl": "tl", "tgk": "tg", "tam": "ta",
    "tel": "te", "tha": "th", "tib": "bo", "bod": "bo", "tur": "tr", "ukr": "uk", "urd": "ur", "uzb": "uz",
    "vie": "vi", "wel": "cy", "cym": "cy", "xho": "xh", "yid": "yi", "yor": "yo", "zul": "zu", "fil": "fil",
}

VIDEO_CODECS = {
    "hevc": "HEVC", "h265": "HEVC", "h264": "H.264", "avc": "H.264", "av1": "AV1", "vp9": "VP9", "vp8": "VP8",
    "mpeg2video": "MPEG-2", "mpeg1video": "MPEG-1", "mpeg4": "MPEG-4", "msmpeg4v3": "DivX 3",
    "msmpeg4v2": "MS MPEG-4 v2", "vc1": "VC-1", "wmv3": "WMV 9", "wmv2": "WMV 8", "h263": "H.263",
    "prores": "ProRes", "mjpeg": "Motion JPEG", "theora": "Theora", "rv40": "RealVideo 4",
}
AUDIO_CODECS = {
    "aac": "AAC", "ac3": "Dolby Digital", "eac3": "Dolby Digital Plus", "truehd": "Dolby TrueHD",
    "dca": "DTS", "dts": "DTS", "flac": "FLAC", "alac": "ALAC", "mp3": "MP3", "mp2": "MP2", "opus": "Opus",
    "vorbis": "Vorbis", "wmav2": "WMA", "wmapro": "WMA Pro", "wmav1": "WMA", "amr_nb": "AMR",
}
SUBTITLE_CODECS = {
    "srt": "SRT", "subrip": "SRT", "ass": "ASS", "ssa": "SSA", "pgs": "PGS", "hdmv_pgs_subtitle": "PGS",
    "vobsub": "VobSub", "dvd_subtitle": "VobSub", "mov_text": "MP4 Text", "webvtt": "WebVTT", "vtt": "WebVTT",
    "dvb_subtitle": "DVB", "eia_608": "CEA-608", "smi": "SAMI",
}


# ---------------------------------------------------------------------------
# Small value helpers
# ---------------------------------------------------------------------------
_ILLEGAL_CHARS = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f￾￿]")
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_WINDOWS_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")


def clean(value) -> str:
    """Text tidy-up: drop control characters Excel refuses, normalise newlines, trim."""
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    return _ILLEGAL_CHARS.sub("", value).strip()


def to_int(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError, OverflowError):
            return None


def to_float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def parse_timestamp(value) -> int | None:
    """Plex stores times as Unix seconds; very old databases used 'YYYY-MM-DD HH:MM:SS' text."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    n = to_int(text)
    if n is not None:
        return n
    for fmt, width in (("%Y-%m-%d %H:%M:%S", 19), ("%Y-%m-%dT%H:%M:%S", 19), ("%Y-%m-%d", 10)):
        try:
            dt = datetime.strptime(text[:width], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        return int((dt - _EPOCH).total_seconds())
    return None


def utc_date(value) -> date | None:
    """Release dates are stored as midnight UTC (negative numbers before 1970)."""
    ts = parse_timestamp(value)
    if ts is None:
        return None
    try:
        # Round to the nearest day so a date stored at local midnight still lands right.
        return (_EPOCH + timedelta(days=round(ts / 86400))).date()
    except OverflowError:
        return None


def local_datetime(value) -> datetime | None:
    """Unix time -> naive local datetime (Excel has no time zones). 0 means 'never'."""
    ts = parse_timestamp(value)
    if not ts:
        return None
    try:
        utc = _EPOCH + timedelta(seconds=ts)
    except OverflowError:
        return None
    try:
        return utc.astimezone().replace(tzinfo=None)
    except (OverflowError, OSError, ValueError):
        # Windows can't localise times before 1970 (or after 3000): use the UTC offset of the nearest
        # time it can, so these still line up with every other (local) time in the workbook.
        anchor = min(max(ts, 86400), 32503593600)
        try:
            offset = datetime.fromtimestamp(anchor) - (_EPOCH + timedelta(seconds=anchor)).replace(tzinfo=None)
            return utc.replace(tzinfo=None) + offset
        except (OverflowError, OSError, ValueError):
            return utc.replace(tzinfo=None)


def hms(ms) -> str:
    ms = to_int(ms)
    if ms is None or ms < 0:
        return ""
    s = ms // 1000
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def parse_extra(value) -> dict:
    """Plex's extra_data column: JSON today, a URL-encoded query string in older versions."""
    if not value:
        return {}
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    text = str(value).strip()
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if k != "url"}
    try:
        return dict(parse_qsl(text))
    except ValueError:
        return {}


def unique(items) -> list[str]:
    """Drop blanks and case-insensitive duplicates, keeping first-seen order."""
    seen, out = set(), []
    for item in items:
        item = clean(item)
        key = item.casefold()
        if item and key not in seen:
            seen.add(key)
            out.append(item)
    return out


def join(items) -> str:
    return LIST_SEP.join(unique(items))


def split_path(path: str) -> tuple[str, str]:
    mod = ntpath if _WINDOWS_PATH.match(path) else posixpath
    folder, name = mod.dirname(path), mod.basename(path)
    if len(folder) > 1 and folder.endswith(("/", "\\")) and not re.fullmatch(r"[A-Za-z]:[\\/]", folder):
        folder = folder.rstrip("/\\")   # '\\nas\share\' -> '\\nas\share'
    return folder, name


def language_name(code) -> str:
    code = clean(code)
    if not code:
        return ""
    base, _, region = code.replace("_", "-").partition("-")
    base = base.lower()
    base = _ISO3.get(base, base)
    name = LANGUAGES.get(base)
    if not name:
        return code
    return f"{name} ({region.upper()})" if region else name


def plex_resolution(width, height) -> str:
    """Plex's own resolution class ('4k', '1080', '720', '576', '480' or 'sd').

    The boundaries were fitted to Plex's own 4K / 1080P / 480p / SD smart collections and reproduce
    their exact members. The 4K, 480 and SD boundaries are fully pinned down by that data (480 needs a
    height of at least 464, so a 720x360 widescreen DVD rip is 'sd'); the 1080/720 boundary is a best
    fit - it matches Plex's count, but a handful of odd sizes near it (e.g. 1374x1036) couldn't be
    checked individually.
    """
    w, h = to_int(width) or 0, to_int(height) or 0
    if not w and not h:
        return ""
    if w >= 3200 or h >= 1800:
        return "4k"
    if w >= 1700 or h >= 1040:
        return "1080"
    if w >= 1100 or h >= 700:
        return "720"
    if h >= 540:
        return "576"
    if h >= 464:
        return "480"
    return "sd"


def resolution_label(width, height) -> str:
    w, h = to_int(width) or 0, to_int(height) or 0
    if w >= 6400 or h >= 3600:
        return "8K"
    cls = plex_resolution(w, h)
    return {"4k": "4K", "sd": "SD"}.get(cls, f"{cls}p" if cls else "")


def hdr_label(video_extra: dict, color_trc) -> str:
    trc = str(video_extra.get("ma:colorTrc") or color_trc or "").lower()
    base = {"smpte2084": "HDR10", "arib-std-b67": "HLG"}.get(trc, "SDR" if (trc or video_extra) else "")
    if str(video_extra.get("ma:DOVIPresent", "")) == "1":
        profile = str(video_extra.get("ma:DOVIProfile") or "").strip()
        compat = str(video_extra.get("ma:DOVIBLCompatID") or "").strip()
        label = "Dolby Vision"
        if profile:
            label += f" P{profile}" + (f".{compat}" if compat and compat != "0" else "")
        if base in ("HDR10", "HLG") and compat != "0":
            label += f" / {base}"
        return label
    return base


_NAMED_LAYOUTS = {"mono": "1.0", "stereo": "2.0", "downmix": "2.0", "binaural": "2.0", "quad": "4.0",
                  "quad(side)": "4.0", "hexagonal": "6.0", "octagonal": "8.0", "cube": "4.0.4",
                  "hexadecagonal": "16.0"}


def channel_layout(channels, layout) -> str:
    """Speaker layout in the usual n.n form (5.1, 7.1, 2.0, 5.1.2...), from FFmpeg's layout name."""
    layout = clean(layout).lower()
    if layout:
        numbered = re.match(r"^(\d{1,2}\.\d(?:\.\d)?)(\(.*\))?$", layout)   # '5.1', '5.1(side)', '7.1.4'
        if numbered:
            return numbered.group(1)
        if layout in _NAMED_LAYOUTS:
            return _NAMED_LAYOUTS[layout]
        speakers = re.match(r"^\d+ channels? \(([a-z0-9+]+)\)$", layout)  # FFmpeg's '2 channels (fc+lfe)'
        if speakers:
            names = speakers.group(1).split("+")
            lfe = sum(1 for s in names if s.startswith("lfe"))
            return f"{len(names) - lfe}.{lfe}"
    ch = to_int(channels)
    if not ch:
        return ""
    return {1: "1.0", 2: "2.0", 3: "2.1", 4: "4.0", 5: "5.0", 6: "5.1", 7: "6.1", 8: "7.1"}.get(ch, f"{ch}ch")


def audio_format(codec, profile, channels=None, layout=None) -> str:
    codec = clean(codec).lower()
    prof = clean(profile).lower()
    if not codec:
        return ""
    if codec in ("dca", "dts"):
        if "dts:x" in prof:
            name = "DTS:X" + (" IMAX Enhanced" if "imax" in prof else "")
        elif prof.startswith("ma"):
            name = "DTS-HD MA"
        elif prof == "hra":
            name = "DTS-HD HRA"
        elif prof == "es":
            name = "DTS-ES"
        elif "express" in prof:
            name = "DTS Express"
        else:
            name = "DTS"
    elif codec in ("truehd", "eac3"):
        name = AUDIO_CODECS[codec] + (" Atmos" if "atmos" in prof else "")
    elif codec == "aac":
        name = "HE-AAC" if prof.startswith("he") else "AAC"
    elif codec.startswith("pcm"):
        name = "PCM"
    else:
        name = AUDIO_CODECS.get(codec, codec.upper())
    return f"{name} {channel_layout(channels, layout)}".strip()


def rating_source(image: str) -> tuple[str, str]:
    """'rottentomatoes://image.rating.ripe' -> ('Rotten Tomatoes', 'Fresh')."""
    image = clean(image)
    if not image:
        return "", ""
    scheme, _, rest = image.partition("://")
    source = RATING_SOURCES.get(scheme.lower(), scheme or image)
    state = rest.rsplit(".", 1)[-1].lower() if rest.count(".") >= 2 else ""
    return source, RATING_VERDICTS.get(state, state.title())


def describe_rating_image(image: str) -> str:
    source, verdict = rating_source(image)
    return f"{source} ({verdict})" if verdict else source


def sort_key(text) -> str:
    return clean(text).casefold()


# ---------------------------------------------------------------------------
# Database access
# ---------------------------------------------------------------------------
@dataclass
class Library:
    id: int
    name: str
    movie_count: int
    agent: str = ""
    scanner: str = ""
    folders: list[str] = field(default_factory=list)


class PlexDatabase:
    """Read-only handle on a Plex library database file."""

    REQUIRED_TABLES = ("metadata_items", "library_sections", "tags", "taggings")

    def __init__(self, path):
        self.path = os.path.abspath(os.fspath(path))
        if not os.path.isfile(self.path):
            raise PlexDBError(f"File not found:\n{self.path}")
        self._tmpdir = None
        self._columns: dict[str, set[str]] = {}
        self.con = None
        self.zip_member = ""   # name of the database inside a zip, when the file is one
        try:
            self.con = self._connect()
            self.tables = {r[0] for r in self.con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        except sqlite3.DatabaseError as exc:
            self.close()
            raise PlexDBError(f"{os.path.basename(self.path)} isn't a readable SQLite database ({exc}).") from exc
        except PlexDBError:
            self.close()
            raise
        except Exception as exc:
            # Couldn't make the private copy: disk full, or a damaged, encrypted or unusually compressed zip
            # (zlib.error, RuntimeError, NotImplementedError, LZMAError...). Always clean up the partial copy.
            self.close()
            raise PlexDBError(f"Couldn't read {os.path.basename(self.path)}: {exc}") from exc
        except BaseException:   # e.g. Ctrl+C part-way through unpacking a big zip
            self.close()
            raise
        missing = [t for t in self.REQUIRED_TABLES if t not in self.tables]
        if missing:
            self.close()
            raise PlexDBError(
                f"{os.path.basename(self.path)} doesn't look like a Plex library database "
                f"(missing tables: {', '.join(missing)}).")

    # -- connection ----------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        with open(self.path, "rb") as f:
            if f.read(4) == b"PK\x03\x04":   # a zip archive rather than an SQLite file
                return self._connect_zip()
        wal = self.path + "-wal"
        if not (os.path.isfile(wal) and os.path.getsize(wal) > 0):
            con = None
            try:
                uri = Path(self.path).as_uri() + "?mode=ro&immutable=1"
                con = sqlite3.connect(uri, uri=True, check_same_thread=False)
                con.execute("SELECT count(*) FROM sqlite_master").fetchone()
                return self._configure(con)
            except sqlite3.DatabaseError as exc:
                if con is not None:
                    con.close()   # an open handle would keep the file locked on Windows
                if "not a database" in str(exc).lower() or "malformed" in str(exc).lower():
                    raise
                # e.g. a network path SQLite can't open as a URI -> fall back to a private copy
        return self._connect_copy()

    def _connect_copy(self) -> sqlite3.Connection:
        self._tmpdir = tempfile.mkdtemp(prefix="projectionist-")
        dest = os.path.join(self._tmpdir, "library.db")
        shutil.copyfile(self.path, dest)
        if os.path.isfile(self.path + "-wal"):
            shutil.copyfile(self.path + "-wal", dest + "-wal")
        con = sqlite3.connect(dest, check_same_thread=False)
        try:
            con.execute("SELECT count(*) FROM sqlite_master").fetchone()
        except sqlite3.DatabaseError:
            con.close()
            raise
        return self._configure(con)

    def _connect_zip(self) -> sqlite3.Connection:
        """The .zip Plex's 'Download database' button produces (library + blobs databases inside)."""
        with zipfile.ZipFile(self.path) as z:
            infos = z.infolist()
            member = pick_library_member(infos)
            if member is None:
                raise PlexDBError(f"{os.path.basename(self.path)} doesn't contain a Plex library database "
                                  "(com.plexapp.plugins.library.db).")
            self.zip_member = member.filename
            self._tmpdir = tempfile.mkdtemp(prefix="projectionist-")
            dest = os.path.join(self._tmpdir, "library.db")
            with z.open(member) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out, 1 << 20)
            wal = next((i for i in infos if i.filename.lower() == member.filename.lower() + "-wal"), None)
            if wal is not None:
                with z.open(wal) as src, open(dest + "-wal", "wb") as out:
                    shutil.copyfileobj(src, out, 1 << 20)
        con = sqlite3.connect(dest, check_same_thread=False)
        try:
            con.execute("SELECT count(*) FROM sqlite_master").fetchone()
        except sqlite3.DatabaseError:
            con.close()
            raise
        return self._configure(con)

    @staticmethod
    def _configure(con: sqlite3.Connection) -> sqlite3.Connection:
        con.row_factory = sqlite3.Row
        con.text_factory = lambda b: b.decode("utf-8", "replace")
        return con

    def close(self):
        con = getattr(self, "con", None)
        if con is not None:
            con.close()
            self.con = None
        if self._tmpdir:
            shutil.rmtree(self._tmpdir, ignore_errors=True)
            self._tmpdir = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- schema helpers --------------------------------------------------------
    def has_table(self, name: str) -> bool:
        return name in self.tables

    def columns(self, table: str) -> set[str]:
        if table not in self._columns:
            self._columns[table] = (
                {r[1] for r in self.con.execute(f'PRAGMA table_info("{table}")')} if table in self.tables else set())
        return self._columns[table]

    def select_list(self, alias: str, table: str, columns) -> str:
        """SELECT list that tolerates columns missing from older/newer Plex schemas.

        Each entry is 'column' or 'column>result_name'. Missing columns come back as NULL.
        """
        have = self.columns(table)
        parts = []
        for spec in columns:
            col, _, as_name = spec.partition(">")
            as_name = as_name or col
            parts.append(f'{alias}."{col}" AS "{as_name}"' if col in have else f'NULL AS "{as_name}"')
        return ", ".join(parts)

    def query(self, sql: str, params=()):
        return self.con.execute(sql, params)

    # -- summaries ---------------------------------------------------------------
    def movie_libraries(self) -> list[Library]:
        libs = []
        rows = self.query(f"""
            SELECT {self.select_list('s', 'library_sections', ['id', 'name', 'agent', 'scanner'])},
                   (SELECT count(*) FROM metadata_items m
                     WHERE m.library_section_id = s.id AND m.metadata_type = {METADATA_MOVIE}) AS movie_count
              FROM library_sections s
             WHERE s.section_type = {SECTION_MOVIE}""").fetchall()
        folders = defaultdict(list)
        if self.has_table("section_locations"):
            for r in self.query('SELECT library_section_id, root_path FROM section_locations ORDER BY id'):
                folders[r[0]].append(clean(r[1]))
        for r in rows:
            libs.append(Library(r["id"], clean(r["name"]) or f"Library {r['id']}", r["movie_count"] or 0,
                                clean(r["agent"]), clean(r["scanner"]), folders.get(r["id"], [])))
        libs.sort(key=lambda lib: sort_key(lib.name))
        return libs

    def schema_version(self) -> str:
        """The most recently applied schema migration (max(version) is meaningless: mixed numbering)."""
        if not self.has_table("schema_migrations"):
            return ""
        row = self.query("SELECT version FROM schema_migrations ORDER BY rowid DESC LIMIT 1").fetchone()
        return clean(row[0]) if row and row[0] is not None else ""


def is_candidate_name(name: str) -> bool:
    """A file name that looks like a Plex library database - never the blobs database or SQLite side files."""
    n = name.lower()
    if "blobs" in n or n.endswith(("-wal", "-shm", "-journal")):
        return False
    return n.startswith("com.plexapp.plugins.library") or n.endswith((".db", ".sqlite", ".sqlite3"))


_DATE_IN_NAME = re.compile(r"(\d{4}-\d{2}-\d{2})")


def pick_library_member(infos) -> zipfile.ZipInfo | None:
    """The library database inside a zip: the live file if there is one, otherwise the newest backup.

    Ties (e.g. the same name in two folders) go to the newest entry, then the biggest.
    """
    def name(info):
        return posixpath.basename(info.filename.replace("\\", "/")).lower()

    candidates = [i for i in infos if not i.is_dir() and is_candidate_name(name(i))]
    if not candidates:
        return None

    def rank(info):
        n = name(info)
        dated = _DATE_IN_NAME.search(n)
        return n == "com.plexapp.plugins.library.db", dated.group(1) if dated else "", info.date_time

    ranked = sorted(candidates, key=rank, reverse=True)
    if len(ranked) > 1 and rank(ranked[0]) == rank(ranked[1]):
        # Nothing says which is newer (e.g. the same name in two folders, saved at the same time).
        tied = [i.filename for i in ranked if rank(i) == rank(ranked[0])]
        raise PlexDBError("This zip holds more than one Plex library database and there's no telling which is "
                          f"newest:\n{chr(10).join(tied)}\n\nUnzip the one you want and choose that file instead.")
    return ranked[0]


def _read_error(path: str, exc: sqlite3.DatabaseError) -> PlexDBError:
    """A clear message for an SQLite error that only shows up once real data is read."""
    name = os.path.basename(path)
    corrupt = getattr(exc, "sqlite_errorcode", None) in (11, 26) or "malformed" in str(exc).lower()
    if corrupt:   # SQLITE_CORRUPT / SQLITE_NOTADB
        return PlexDBError(f"{name} is damaged ({exc}).\n\nUse a different backup of the database.")
    return PlexDBError(f"Couldn't read {name}: {exc}")


def list_movie_libraries(path) -> list[Library]:
    with PlexDatabase(path) as db:
        try:
            return db.movie_libraries()
        except sqlite3.DatabaseError as exc:
            raise _read_error(db.path, exc) from exc


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------
@dataclass
class ExportResult:
    sheets: list[Sheet]
    libraries: list[Library]
    info: list[tuple[str, object]]
    movie_count: int

    def sheet(self, name: str) -> Sheet | None:
        return next((s for s in self.sheets if s.name == name), None)


@dataclass
class _Tag:
    tag_id: int
    tag: str
    key: str
    photo: str
    tag_extra: dict
    index: int | None
    text: str
    start: int | None
    end: int | None
    thumb: str
    extra: dict
    order: tuple = ()


def main_directors(directors: list) -> list:
    """The real directors among a movie's directing credits - never assistants."""
    main = [d for d in directors if d.text.lower() in ("", "director", "co-director", "directed by")]
    if not main:
        # e.g. Hong Kong action films credited only an 'Action Director'.
        main = [d for d in directors if "director" in d.text.lower() and "assistant" not in d.text.lower()]
    if not main:
        # Plex sometimes files the real director under another job (Guardians of the Galaxy Vol. 2 has
        # James Gunn as 'Script Supervisor'); show its director credits rather than nothing - but never
        # assistants. Every credit, with its job, is on the Crew sheet.
        main = [d for d in directors if "assistant" not in d.text.lower()]
    return main


def parse_rating(tag) -> tuple[str, str, float, str] | None:
    """A rating tag -> (source, 'Critic'/'Audience'/'', value 0-10, verdict), or None when it has no value."""
    value = to_float(tag.text)
    if value is None:
        return None
    source, verdict = rating_source(tag.tag)
    kind = clean(tag.tag_extra.get("at:type")).title()
    if source == "Rotten Tomatoes" and not kind:
        kind = "Audience" if verdict in ("Upright", "Spilled") else "Critic"
    return source, kind, value, verdict


def headline_ratings(movie_tags: dict) -> dict:
    """IMDb / TMDb / Rotten Tomatoes scores - the first of each kind Plex lists, as on the Movies sheet."""
    out = {}
    for tag in movie_tags.get(TAG_RATING, []):
        parsed = parse_rating(tag)
        if parsed is None:
            continue
        source, kind, value, verdict = parsed
        if source == "IMDb":
            out.setdefault("imdb_rating", value)
        elif source == "TMDb":
            out.setdefault("tmdb_rating", value)
        elif source == "Rotten Tomatoes" and kind == "Critic":
            out.setdefault("rt_critic", round(value * 10))
            out.setdefault("rt_critic_verdict", verdict)
        elif source == "Rotten Tomatoes" and kind == "Audience":
            out.setdefault("rt_audience", round(value * 10))
            out.setdefault("rt_audience_verdict", verdict)
    return out


def external_ids(guid, movie_tags: dict) -> tuple[dict[str, str], list[str]]:
    """({'imdb': 'tt...', 'tmdb': ..., 'tvdb': ...}, ['other:id', ...]) from a movie's GUID tags."""
    ids, others = {}, []
    for g in movie_tags.get(TAG_GUID, []):
        scheme, sep, value = g.tag.partition("://")
        if not sep:
            continue
        scheme = scheme.lower()
        if scheme in ("imdb", "tmdb", "tvdb") and scheme not in ids:
            ids[scheme] = value
        else:
            others.append(f"{scheme}:{value}")
    # Movies matched by Plex's legacy agents keep the ID in the GUID itself.
    legacy = re.match(r"com\.plexapp\.agents\.(imdb|themoviedb|thetvdb)://([^?/]+)", clean(guid))
    if legacy:
        key = {"imdb": "imdb", "themoviedb": "tmdb", "thetvdb": "tvdb"}[legacy.group(1)]
        ids.setdefault(key, legacy.group(2))
    return ids, others


def plex_movie_id(guid) -> str:
    """The film's server-independent Plex ID: an edition's GUID without its '/edition/<name>' ending."""
    guid = clean(guid)
    return guid.split("/edition/")[0] if guid.startswith("plex://") else ""


# The Movies sheet's Owner Rating Source
RATED_THIS_COPY = "This copy"
RATED_EARLIER_EDITION = "An earlier edition"


def earlier_edition_ratings(db: "PlexDatabase", account_id) -> dict[str, float]:
    """{Plex movie ID: rating} for the ratings an account left on a Plex GUID that no item has any more.

    Plex keeps a rating on the GUID the copy had when it was rated. When Plex later gives the copy an edition, or
    renames its edition, the copy gets a new GUID and the rating stays behind on the old one - so Plex shows the
    copy you have now as unrated. Several such GUIDs of one film are averaged. Whoever uses these decides when:
    the app and the spreadsheet take one only for a film none of whose copies has a rating of its own."""
    if account_id is None or not db.has_table("metadata_item_settings") or \
            not {"account_id", "guid", "rating"} <= db.columns("metadata_item_settings"):
        return {}
    by_film = defaultdict(list)
    for r in db.query("SELECT guid, rating FROM metadata_item_settings "
                      "WHERE account_id = ? AND rating IS NOT NULL AND guid LIKE 'plex://movie/%' "
                      "AND guid NOT IN (SELECT guid FROM metadata_items WHERE guid IS NOT NULL)", (account_id,)):
        value = to_float(r["rating"])
        if value is not None:
            by_film[plex_movie_id(r["guid"])].append(value)
    return {key: round(sum(values) / len(values), 1) for key, values in by_film.items() if key}


def records_final_flags(db: "PlexDatabase") -> bool:
    """Whether this Plex version flags the last credits marker as 'final' (older versions never do).

    Checked across the whole database, so the answer doesn't depend on which libraries are being read.
    """
    if "extra_data" not in db.columns("taggings"):
        return False
    row = db.query(f"SELECT 1 FROM taggings tg JOIN tags t ON t.id = tg.tag_id "
                   f"WHERE t.tag_type = {TAG_MARKER} AND tg.extra_data LIKE '%pv:final%' LIMIT 1").fetchone()
    return row is not None


def credits_info(movie_tags: dict, versions: list, year, final_flags: bool) -> credit_scenes.CreditsInfo:
    """Scenes during/after the end credits of one movie, from its credits markers and file length."""
    marks = [mk for mk in movie_tags.get(TAG_MARKER, [])
             if mk.text.lower() == "credits" and mk.start is not None and mk.end is not None]
    markers = [credit_scenes.Marker(mk.start, mk.end,
                                    None if mk.extra.get("pv:final") is None else str(mk.extra["pv:final"]) == "1")
               for mk in marks]
    last_end = max((mk.end for mk in marks), default=None)
    return credit_scenes.analyse(markers, marker_timeline(versions, last_end), to_int(year), final_flags)


def marker_timeline(versions: list, last_end: int | None) -> int | None:
    """Length of the file a movie's markers were measured on.

    Markers belong to the movie, not to one of its versions: take the version whose length fits them
    best (the shortest one that's still long enough), falling back to the longest.
    """
    lengths = []
    for v in versions:
        total = to_int(v.get("duration")) or sum(to_int(p.get("duration")) or 0 for p in v.get("parts", []))
        if total:
            lengths.append(total)
    if not lengths:
        return None
    if last_end is None:
        return lengths[0]
    fitting = [d for d in lengths if d >= last_end - credit_scenes.END_SLACK_MS]
    return min(fitting) if fitting else max(lengths)


def extract(db_path, library_ids=None, detail_sheets=None, progress: Progress | None = None,
            cancel=None) -> ExportResult:
    """Read every movie in the chosen libraries into sheets of rows.

    library_ids:   iterable of library_sections ids to include (None = every movie library)
    detail_sheets: names from sheets.DETAIL_SHEET_NAMES to build (None = all of them)
    progress:      callback(fraction 0-1, message)
    cancel:        threading.Event - export stops with Cancelled when it is set
    """
    with PlexDatabase(db_path) as db:
        try:
            return _Extractor(db, library_ids, detail_sheets, progress, cancel).run()
        except sqlite3.DatabaseError as exc:   # e.g. a damaged table that only shows up part-way through
            raise _read_error(db.path, exc) from exc


class _Extractor:
    def __init__(self, db: PlexDatabase, library_ids, detail_sheets, progress, cancel):
        self.db = db
        self._progress = progress or (lambda fraction, message: None)
        self._cancel = cancel
        all_libs = db.movie_libraries()
        if not all_libs:
            raise PlexDBError("This database has no movie libraries.\n\n"
                              "(If you picked the '...blobs.db' file, choose "
                              "com.plexapp.plugins.library.db instead.)")
        wanted = None if library_ids is None else {int(i) for i in library_ids}
        self.libraries = [lib for lib in all_libs if wanted is None or lib.id in wanted]
        if not self.libraries:
            raise PlexDBError("None of the selected libraries are movie libraries in this database.")
        self.lib_names = {lib.id: lib.name for lib in self.libraries}
        self.lib_order = {lib.id: i for i, lib in enumerate(self.libraries)}
        self.detail = set(S.DETAIL_SHEET_NAMES if detail_sheets is None else detail_sheets)
        self.ids_sql = ",".join(str(lib.id) for lib in self.libraries)   # ints only - safe to inline
        self.earlier_ratings: dict[str, float] = {}     # see load_earlier_ratings

    def step(self, fraction: float, message: str):
        if self._cancel is not None and self._cancel.is_set():
            raise Cancelled()
        self._progress(fraction, message)

    # -- loaders -----------------------------------------------------------------
    def _movie_filter(self, alias="m") -> str:
        return f"{alias}.metadata_type = {METADATA_MOVIE} AND {alias}.library_section_id IN ({self.ids_sql})"

    def load_movies(self):
        cols = ["id", "library_section_id", "guid", "title", "title_sort", "original_title", "edition_title",
                "studio", "rating", "audience_rating", "tagline", "summary", "content_rating",
                "content_rating_age", "duration", "year", "originally_available_at", "added_at", "updated_at",
                "refreshed_at", "deleted_at", "user_thumb_url", "user_art_url", "user_clear_logo_url",
                "user_square_art_url", "extra_data", "slug", "user_fields", "tags_genre", "tags_director",
                "tags_writer", "tags_star", "tags_country", "tags_collection"]
        rows = self.db.query(f"SELECT {self.db.select_list('m', 'metadata_items', cols)} "
                             f"FROM metadata_items m WHERE {self._movie_filter()}").fetchall()
        movies = [dict(r) for r in rows]
        for m in movies:
            m["_sort"] = (self.lib_order.get(m["library_section_id"], 0),
                          sort_key(m["title_sort"] or m["title"]), to_int(m["year"]) or 0, m["id"])
        movies.sort(key=lambda m: m["_sort"])
        return movies

    def load_tags(self, tag_types=None):
        """{movie_id: {tag_type: [_Tag, ...]}} in Plex's order (every kind of tag, or just tag_types)."""
        db = self.db
        only = f" AND t.tag_type IN ({','.join(str(int(x)) for x in tag_types)})" if tag_types else ""
        sql = f"""
            SELECT tg.metadata_item_id AS mid, t.tag_type AS tag_type, t.id AS tag_id,
                   {db.select_list('t', 'tags', ['tag', 'key>t_key', 'user_thumb_url>t_thumb', 'extra_data>t_extra'])},
                   {db.select_list('tg', 'taggings', ['index>idx', 'text', 'time_offset', 'end_time_offset',
                                                      'thumb_url', 'extra_data>tg_extra', 'id>tg_id'])}
              FROM taggings tg
              JOIN tags t ON t.id = tg.tag_id
              JOIN metadata_items m ON m.id = tg.metadata_item_id
             WHERE {self._movie_filter()}{only}"""
        grouped: dict[int, dict[int, list]] = defaultdict(lambda: defaultdict(list))
        for r in db.query(sql):
            idx = to_int(r["idx"])
            grouped[r["mid"]][r["tag_type"]].append(_Tag(
                tag_id=r["tag_id"], tag=clean(r["tag"]), key=clean(r["t_key"]), photo=clean(r["t_thumb"]),
                tag_extra=parse_extra(r["t_extra"]), index=idx, text=clean(r["text"]),
                start=to_int(r["time_offset"]), end=to_int(r["end_time_offset"]), thumb=clean(r["thumb_url"]),
                extra=parse_extra(r["tg_extra"]), order=(idx if idx is not None else 1 << 30, r["tg_id"] or 0)))
        for by_type in grouped.values():
            for items in by_type.values():
                items.sort(key=lambda x: x.order)   # Plex's own order
        return grouped

    def load_media(self):
        """{movie_id: [version dict, ...]} with parts and streams attached."""
        db = self.db
        mi_cols = ["id", "metadata_item_id", "width", "height", "size", "duration", "bitrate", "container",
                   "video_codec", "audio_codec", "display_aspect_ratio", "frames_per_second", "audio_channels",
                   "created_at", "deleted_at", "extra_data", "color_trc"]
        items = db.query(f"SELECT {db.select_list('mi', 'media_items', mi_cols)} FROM media_items mi "
                         f"JOIN metadata_items m ON m.id = mi.metadata_item_id "
                         f"WHERE {self._movie_filter()}").fetchall() if db.has_table("media_items") else []
        versions = defaultdict(list)
        by_id = {}
        for r in items:
            v = dict(r)
            v["extra"] = parse_extra(v.pop("extra_data"))
            v["parts"], v["streams"] = [], []
            versions[v["metadata_item_id"]].append(v)
            by_id[v["id"]] = v
        if by_id and db.has_table("media_parts"):
            mp_cols = ["id", "media_item_id", "file", "size", "duration", "hash", "open_subtitle_hash",
                       "index>idx", "created_at", "deleted_at"]
            for r in db.query(f"SELECT {db.select_list('mp', 'media_parts', mp_cols)} FROM media_parts mp "
                              f"JOIN media_items mi ON mi.id = mp.media_item_id "
                              f"JOIN metadata_items m ON m.id = mi.metadata_item_id WHERE {self._movie_filter()}"):
                if r["media_item_id"] in by_id:
                    by_id[r["media_item_id"]]["parts"].append(dict(r))
        if by_id and db.has_table("media_streams"):
            ms_cols = ["id", "media_item_id", "media_part_id", "stream_type_id", "codec", "language",
                       "channels", "bitrate", "index>idx", "default>is_default", "forced", "url", "extra_data"]
            for r in db.query(f"SELECT {db.select_list('ms', 'media_streams', ms_cols)} FROM media_streams ms "
                              f"JOIN media_items mi ON mi.id = ms.media_item_id "
                              f"JOIN metadata_items m ON m.id = mi.metadata_item_id WHERE {self._movie_filter()}"):
                if r["media_item_id"] in by_id:
                    s = dict(r)
                    s["extra"] = parse_extra(s.pop("extra_data"))
                    by_id[r["media_item_id"]]["streams"].append(s)
        for vs in versions.values():
            # Available versions first, then Plex's own order.
            vs.sort(key=lambda v: (v["deleted_at"] is not None, v["id"]))
            for v in vs:
                v["parts"].sort(key=lambda p: (p["deleted_at"] is not None,
                                               to_int(p["idx"]) if p["idx"] is not None else 0, p["id"]))
                part_order = {p["id"]: i for i, p in enumerate(v["parts"])}
                v["streams"].sort(key=lambda s: (part_order.get(s["media_part_id"], 1 << 30),
                                                 s["idx"] is None, to_int(s["idx"]) or 0, s["id"]))
        return versions

    def load_accounts(self):
        names = {}
        if self.db.has_table("accounts"):
            for r in self.db.query("SELECT id, name FROM accounts"):
                names[r[0]] = clean(r[1])
        owner = 1 if 1 in names else min((i for i in names if i and i > 0), default=1)
        return names, owner

    def account_name(self, account_id) -> str:
        return self.accounts.get(account_id) or f"User {account_id}"

    def load_settings(self):
        """{guid: [settings row, ...]} - per-user play state."""
        out = defaultdict(list)
        if not self.db.has_table("metadata_item_settings"):
            return out
        cols = ["account_id", "guid", "rating", "view_offset", "view_count", "last_viewed_at"]
        sql = (f"SELECT {self.db.select_list('s', 'metadata_item_settings', cols)} FROM metadata_item_settings s "
               f"WHERE s.guid IN (SELECT m.guid FROM metadata_items m WHERE {self._movie_filter()})")
        for r in self.db.query(sql):
            out[r["guid"]].append(dict(r))
        return out

    def load_earlier_ratings(self, movies, settings) -> dict[str, float]:
        """{Plex movie ID: the owner's rating} left on an earlier edition of a film (see earlier_edition_ratings) -
        only for films none of whose copies here has a rating of its own. The same rule as the app's tabs."""
        rated = set()
        for m in movies:
            if m["guid"] and any(s["account_id"] == self.owner_id and to_float(s["rating"]) is not None
                                 for s in settings.get(m["guid"], [])):
                rated.add(plex_movie_id(m["guid"]))
        return {key: value for key, value in earlier_edition_ratings(self.db, self.owner_id).items()
                if key not in rated}

    def load_views(self):
        if not self.db.has_table("metadata_item_views"):
            return []
        cols = ["id", "account_id", "guid", "library_section_id", "title", "originally_available_at",
                "viewed_at", "device_id"]
        return [dict(r) for r in self.db.query(
            f"SELECT {self.db.select_list('v', 'metadata_item_views', cols)} FROM metadata_item_views v "
            f"WHERE v.metadata_type = {METADATA_MOVIE} ORDER BY v.viewed_at, v.id")]

    def load_devices(self):
        if not self.db.has_table("devices"):
            return {}
        cols = ["id", "name", "platform", "identifier"]
        return {r["id"]: clean(r["name"]) or clean(r["platform"]) or clean(r["identifier"])
                for r in self.db.query(f"SELECT {self.db.select_list('d', 'devices', cols)} FROM devices d")}

    def load_extras(self):
        """{movie_id: [extra dict, ...]}"""
        out = defaultdict(list)
        if not self.db.has_table("metadata_relations"):
            return out
        db = self.db
        sql = f"""
            SELECT r.metadata_item_id AS mid, r.relation_type AS relation_type,
                   {db.select_list('e', 'metadata_items', ['id', 'title', 'guid', 'duration', 'extra_data',
                                                           'index>idx', 'originally_available_at',
                                                           'content_rating', 'user_thumb_url'])}
              FROM metadata_relations r
              JOIN metadata_items e ON e.id = r.related_metadata_item_id
              JOIN metadata_items m ON m.id = r.metadata_item_id
             WHERE {self._movie_filter()} AND e.metadata_type = {METADATA_EXTRA}"""
        rows = [dict(r) for r in db.query(sql)]
        # Local extras keep their length and file on their own media rows.
        media = {}
        if rows and db.has_table("media_items"):
            sql = f"""
                SELECT mi.metadata_item_id AS eid, max(mi.duration) AS duration,
                       min({'mp.file' if db.has_table('media_parts') else 'NULL'}) AS file
                  FROM media_items mi
                  {'LEFT JOIN media_parts mp ON mp.media_item_id = mi.id' if db.has_table('media_parts') else ''}
                 WHERE mi.metadata_item_id IN (
                        SELECT r.related_metadata_item_id FROM metadata_relations r
                          JOIN metadata_items m ON m.id = r.metadata_item_id WHERE {self._movie_filter()})
                 GROUP BY mi.metadata_item_id"""
            media = {r["eid"]: dict(r) for r in db.query(sql)}
        for r in rows:
            extra = parse_extra(r["extra_data"])
            kind = to_int(extra.get("ex:extraType")) or to_int(r["relation_type"])
            guid = clean(r["guid"])
            local = media.get(r["id"], {})
            if guid.lower().startswith("file://"):
                source = "Local file"
                location = clean(local.get("file")) or unquote(urlparse(guid).path)
                if re.match(r"^/[A-Za-z]:/", location):
                    location = location[1:]
            else:
                source = "Online" if guid else ""
                location = guid
            thumb = clean(r["user_thumb_url"])
            out[r["mid"]].append({
                "extra_type": EXTRA_TYPES.get(kind, f"Type {kind}" if kind else "Extra"),
                "extra_title": clean(r["title"]),
                "order": to_int(r["idx"]),
                "duration_ms": to_int(r["duration"]) or to_int(local.get("duration")),
                "released": utc_date(r["originally_available_at"]),
                "explicit": "Yes" if clean(r["content_rating"]).lower() == "explicit" else "",
                "thumbnail_url": thumb if thumb.lower().startswith(("http://", "https://")) else "",
                "source": source,
                "location": location,
                "extra_id": r["id"],
            })
        for extras in out.values():
            extras.sort(key=lambda e: (e["order"] is None, e["order"] or 0, e["extra_id"]))
        return out

    def load_collections(self) -> list[dict]:
        """Every collection in the chosen libraries - regular ones and smart (saved-filter) ones."""
        cols = ["id", "library_section_id", "guid", "title", "title_sort", "summary", "extra_data"]
        records = []
        for r in self.db.query(f"SELECT {self.db.select_list('c', 'metadata_items', cols)} FROM metadata_items c "
                               f"WHERE c.metadata_type = {METADATA_COLLECTION} "
                               f"AND c.library_section_id IN ({self.ids_sql})"):
            extra = parse_extra(r["extra_data"])
            records.append({
                "id": r["id"], "library_id": r["library_section_id"], "guid": clean(r["guid"]),
                "title": clean(r["title"]), "sort": sort_key(r["title_sort"] or r["title"]),
                "summary": clean(r["summary"]), "smart": str(extra.get("at:smart", "")) == "1",
                "uri": clean(extra.get("pv:uri")), "plex_count": to_int(extra.get("at:childCount")),
            })
        return records

    def load_tag_names(self, tag_ids) -> dict[int, str]:
        ids = sorted({int(i) for i in tag_ids})
        names = {}
        for start in range(0, len(ids), 500):
            chunk = ids[start:start + 500]
            for r in self.db.query(f"SELECT id, tag FROM tags WHERE id IN ({','.join('?' * len(chunk))})", chunk):
                names[r[0]] = clean(r[1])
        return names

    def smart_collections(self, collections, movies, tags, media, settings):
        """Expand smart collections: {collection id: SmartResult}."""
        smart = [c for c in collections if c["smart"]]
        if not smart:
            return {}
        facts = {}
        for m in movies:
            mid = m["id"]
            versions = media.get(mid, [])
            audio, subs = set(), set()
            for v in versions:
                for s in v["streams"]:
                    code = clean(s["language"])
                    if code and s["stream_type_id"] in (2, 3):
                        base = code.lower().replace("_", "-").split("-")[0]
                        (audio if s["stream_type_id"] == 2 else subs).add(_ISO3.get(base, base))
            owner = next((s for s in settings.get(m["guid"], []) if s["account_id"] == self.owner_id), None) \
                if m["guid"] else None
            facts[mid] = smart_filters.MovieFacts(
                library_id=m["library_section_id"],
                tag_ids={x.tag_id for items in tags.get(mid, {}).values() for x in items},
                studio=clean(m["studio"]), edition=clean(m["edition_title"]), title=clean(m["title"]),
                content_rating=clean(m["content_rating"]), year=to_int(m["year"]),
                resolutions={plex_resolution(v["width"], v["height"]) for v in versions} - {""},
                audio_languages=audio, subtitle_languages=subs,
                owner_watched=bool(owner and (to_int(owner["view_count"]) or 0) > 0),
                owner_rating=to_float(owner["rating"]) if owner else None)
        referenced = set()
        for c in smart:
            referenced |= smart_filters.referenced_tag_ids(c["uri"])
        tag_names = self.load_tag_names(referenced)
        return {c["id"]: smart_filters.expand(c["uri"], c["plex_count"], facts, tag_names) for c in smart}

    # -- main --------------------------------------------------------------------
    def run(self) -> ExportResult:
        self.step(0.00, "Reading movies...")
        movies = self.load_movies()
        self.step(0.10, f"Found {len(movies):,} movies. Reading cast, crew, genres and ratings...")
        tags = self.load_tags()
        self.final_flags = records_final_flags(self.db)
        self.step(0.45, "Reading files and audio/subtitle tracks...")
        media = self.load_media()
        self.step(0.65, "Reading watch history...")
        self.accounts, self.owner_id = self.load_accounts()
        settings = self.load_settings()
        self.earlier_ratings = self.load_earlier_ratings(movies, settings)
        views = self.load_views() if S.WATCH_HISTORY in self.detail else []
        devices = self.load_devices() if views else {}
        self.step(0.72, "Reading extras and collections...")
        extras = self.load_extras()
        collections = self.load_collections()
        # A collection tag always belongs to a regular collection - smart ones never have tags - so a
        # smart collection that happens to share a regular one's name must not be matched here.
        regular = [c for c in collections if not c["smart"]]
        coll_by_guid = {c["guid"]: c for c in regular if c["guid"]}
        coll_by_title = {(c["library_id"], sort_key(c["title"])): c for c in regular}
        self.step(0.75, "Working out smart collections...")
        smart = self.smart_collections(collections, movies, tags, media, settings)
        smart_by_movie = defaultdict(list)
        for c in sorted(collections, key=lambda c: c["sort"]):
            result = smart.get(c["id"])
            if result and result.expanded:
                for mid in result.members:
                    smart_by_movie[mid].append(c)

        sheets = {name: Sheet(name, S.SHEET_COLUMNS[name], freeze_cols=S.FREEZE_COLS.get(name, 0),
                              desc=S.SHEET_DESCRIPTIONS.get(name, ""))
                  for name in [S.MOVIES] + [n for n in S.DETAIL_SHEET_NAMES if n in self.detail]}
        want = lambda name: name in sheets
        guid_to_movies = defaultdict(list)
        collection_rows = []
        exported_per_collection = Counter()
        orphans = {}

        self.step(0.78, "Building spreadsheet rows...")
        for n, m in enumerate(movies):
            if n % 250 == 0:
                self.step(0.78 + 0.22 * n / max(len(movies), 1), f"Building rows... {n:,}/{len(movies):,}")
            mid = m["id"]
            t = tags.get(mid, {})
            base = {"plex_id": mid, "title": clean(m["title"]), "year": to_int(m["year"]),
                    "library": self.lib_names.get(m["library_section_id"], "")}
            if m["guid"]:
                guid_to_movies[m["guid"]].append((m, base))
            row = dict(base)
            self._identity(row, m, t)
            self._people(row, m, t, base, sheets)
            self._ratings(row, m, t, base, sheets)
            self._ids(row, m, t)
            self._viewing(row, m, settings.get(m["guid"], []) if m["guid"] else [], base, sheets)
            self._media(row, m, media.get(mid, []), base, sheets)
            self._misc(row, m, t, extras.get(mid, []), base, sheets)
            self._credits(row, m, t, media.get(mid, []), base, sheets)
            # Collections: regular ones are stored as tags; smart ones were worked out above.
            memberships = []
            for tag in t.get(TAG_COLLECTION, []):
                rec = coll_by_guid.get(tag.tag_extra.get("at:guid")) or \
                    coll_by_title.get((m["library_section_id"], sort_key(tag.tag)))
                memberships.append((tag.tag, "Regular", rec))
            memberships += [(c["title"], "Smart", c) for c in smart_by_movie.get(mid, [])]
            row["collections"] = join([name for name, _, _ in memberships]) or row.get("collections", "")
            for name, kind, rec in memberships:
                if rec is None:   # a collection tag with no collection record of its own
                    key = (m["library_section_id"], sort_key(name))
                    rec = orphans.setdefault(key, {"id": key, "library_id": m["library_section_id"], "title": name,
                                                   "sort": sort_key(name), "summary": "", "smart": False,
                                                   "uri": "", "plex_count": None})
                exported_per_collection[rec["id"]] += 1
                if want(S.COLLECTIONS):
                    collection_rows.append(dict(base, collection=name, collection_type=kind,
                                                collection_summary=rec["summary"] if rec else ""))
            sheets[S.MOVIES].rows.append(row)

        if want(S.COLLECTIONS):
            collection_rows.sort(key=lambda r: (sort_key(r["collection"]), r["library"], r["year"] or 0,
                                                sort_key(r["title"])))
            sheets[S.COLLECTIONS].rows = collection_rows
        if want(S.COLLECTION_LIST):
            sheets[S.COLLECTION_LIST].rows = self._collection_list(
                collections + list(orphans.values()), smart, exported_per_collection)
        if want(S.WATCH_HISTORY):
            self._history(sheets[S.WATCH_HISTORY], views, devices, guid_to_movies)

        self.step(1.0, f"Read {len(movies):,} movies.")
        info = self._info(len(movies))
        ordered = [sheets[S.MOVIES]] + [sheets[n] for n in S.DETAIL_SHEET_NAMES if n in sheets]
        return ExportResult(ordered, self.libraries, info, len(movies))

    # -- row builders --------------------------------------------------------------
    @staticmethod
    def _names(tags: list[_Tag]) -> list[str]:
        return [x.tag for x in tags]

    def _identity(self, row, m, t):
        extra = parse_extra(m["extra_data"])
        m["_extra"] = extra
        advisory_tag = next((x for x in t.get(TAG_ADVISORY, []) if x.text or x.extra), None)
        advisory = advisory_tag.text if advisory_tag else ""
        stars = to_int(advisory_tag.extra.get("pv:ageRatingRating")) if advisory_tag else None
        duration = to_int(m["duration"])
        row.update(
            title_sort=clean(m["title_sort"]),
            original_title=clean(m["original_title"]),
            edition=clean(m["edition_title"]),
            release_date=utc_date(m["originally_available_at"]),
            content_rating=clean(m["content_rating"]),
            content_rating_age=to_int(m["content_rating_age"]),
            content_advisory=advisory,
            common_sense_rating=stars if stars and 1 <= stars <= 5 else None,
            runtime_min=round(duration / 60000) if duration else None,
            tagline=clean(m["tagline"]),
            summary=clean(m["summary"]),
            genres=join(self._names(t.get(TAG_GENRE, [])) or (m["tags_genre"] or "").split("|")),
            countries=join(self._names(t.get(TAG_COUNTRY, [])) or (m["tags_country"] or "").split("|")),
            studio=clean(m["studio"]),
            production_companies=join(self._names(t.get(TAG_STUDIO, []))),
            collections=join(self._names(t.get(TAG_COLLECTION, [])) or (m["tags_collection"] or "").split("|")),
            labels=join(self._names(t.get(TAG_LABEL, []))),
            added_at=local_datetime(m["added_at"]),
            updated_at=local_datetime(m["updated_at"]),
            refreshed_at=local_datetime(m["refreshed_at"]),
            unavailable_since=local_datetime(m["deleted_at"]),
        )

    def _people(self, row, m, t, base, sheets):
        cast = t.get(TAG_ROLE, [])
        directors = t.get(TAG_DIRECTOR, [])
        writers = t.get(TAG_WRITER, [])
        producers = t.get(TAG_PRODUCER, [])

        # Producer credits end in 'Producer' (Executive/Co-/Line/Assistant Producer...); a "Producer's
        # Assistant" or casting credit doesn't.
        main_producers = [p for p in producers if not p.text or re.search(r"producers?\s*$", p.text, re.I)]
        row.update(
            directors=join(self._names(main_directors(directors)) or
                           ([] if directors else (m["tags_director"] or "").split("|"))),
            writers=join(self._names(writers) or (m["tags_writer"] or "").split("|")),
            producers=join(self._names(main_producers)),
            cast=join(self._names(cast) or (m["tags_star"] or "").split("|")),
            cast_characters=LIST_SEP.join(f"{c.tag} ({c.text})" if c.text else c.tag for c in cast if c.tag),
            cast_count=len(cast) or None,
        )
        if S.CAST in sheets:
            rows = sheets[S.CAST].rows
            for i, c in enumerate(cast, 1):
                rows.append(dict(base, order=i, actor=c.tag, character=c.text, person_id=c.key, photo_url=c.photo))
        if S.CREW in sheets:
            rows = sheets[S.CREW].rows
            for dept, people in (("Directing", directors), ("Writing", writers), ("Production", producers)):
                for i, p in enumerate(people, 1):
                    rows.append(dict(base, department=dept, order=i, name=p.tag, job=p.text,
                                     person_id=p.key, photo_url=p.photo))

    def _ratings(self, row, m, t, base, sheets):
        extra = m["_extra"]
        row["critic_rating"] = to_float(m["rating"])
        row["audience_rating"] = to_float(m["audience_rating"])
        row["critic_rating_source"] = describe_rating_image(extra.get("at:ratingImage", ""))
        row["audience_rating_source"] = describe_rating_image(extra.get("at:audienceRatingImage", ""))
        row.update(headline_ratings(t))
        others = []
        for r in t.get(TAG_RATING, []):
            parsed = parse_rating(r)
            if parsed is None:
                continue
            source, kind, value, verdict = parsed
            if source not in ("IMDb", "TMDb") and not (source == "Rotten Tomatoes" and kind in ("Critic", "Audience")):
                others.append(f"{source}{f' ({kind})' if kind else ''}: {value:g}")
            if S.RATINGS in sheets:
                pct = round(value * 10)
                display = f"{pct}%" if source == "Rotten Tomatoes" else f"{value:g}/10"
                sheets[S.RATINGS].rows.append(dict(base, source=source, rating_type=kind, value=value,
                                                   display=display, verdict=verdict))
        row["other_ratings"] = LIST_SEP.join(others)
        reviews = t.get(TAG_REVIEW, [])
        row["critic_reviews"] = len(reviews) or None
        if S.REVIEWS in sheets:
            rows = sheets[S.REVIEWS].rows
            for i, r in enumerate(reviews, 1):
                rows.append(dict(base, order=i, critic=r.tag, publication=clean(r.extra.get("at:source")),
                                 verdict=rating_source(r.extra.get("at:image", ""))[1], review=r.text,
                                 link=clean(r.extra.get("at:link"))))

    def _ids(self, row, m, t):
        guid = clean(m["guid"])
        ids, others = external_ids(guid, t)
        imdb, tmdb, tvdb = ids.get("imdb", ""), ids.get("tmdb", ""), ids.get("tvdb", "")
        slug = clean(m["slug"])
        row.update(
            imdb_id=imdb, tmdb_id=tmdb, tvdb_id=tvdb, other_ids=LIST_SEP.join(others),
            imdb_url=f"https://www.imdb.com/title/{imdb}/" if imdb.startswith("tt") else "",
            tmdb_url=f"https://www.themoviedb.org/movie/{tmdb}" if tmdb.isdigit() else "",
            plex_guid=guid,
            # The GUID of an edition ends in this server's '/edition/<name>'; the movie itself is the part before.
            plex_movie_id=plex_movie_id(guid),
            plex_url=f"https://watch.plex.tv/movie/{slug}" if slug and guid.startswith("plex://") else "",
        )

    def _viewing(self, row, m, settings, base, sheets):
        owner = next((s for s in settings if s["account_id"] == self.owner_id), None)
        if owner:
            offset = to_int(owner["view_offset"])
            row.update(owner_plays=to_int(owner["view_count"]) or 0,
                       owner_last_played=local_datetime(owner["last_viewed_at"]),
                       owner_rating=to_float(owner["rating"]),
                       owner_resume_at=hms(offset) if offset and offset > 0 else "")
        else:
            row["owner_plays"] = 0
        if row.get("owner_rating") is not None:
            row["owner_rating_source"] = RATED_THIS_COPY
        elif m["guid"] and plex_movie_id(m["guid"]) in self.earlier_ratings:
            # Rated before Plex gave this copy its edition GUID: Plex shows the copy as unrated, but the rating is
            # the owner's all the same (the app's tabs count it too). Plex's own view per copy stays on Watch Status.
            row["owner_rating"] = self.earlier_ratings[plex_movie_id(m["guid"])]
            row["owner_rating_source"] = RATED_EARLIER_EDITION
        watched = [s for s in settings if (to_int(s["view_count"]) or 0) > 0]
        row["total_plays"] = sum(to_int(s["view_count"]) or 0 for s in settings)
        row["watched_by"] = LIST_SEP.join(sorted((self.account_name(s["account_id"]) for s in watched),
                                                 key=str.casefold))
        last = max((parse_timestamp(s["last_viewed_at"]) or 0 for s in settings), default=0)
        row["last_played"] = local_datetime(last)
        if S.WATCH_STATUS in sheets:
            rows = sheets[S.WATCH_STATUS].rows
            for s in sorted(settings, key=lambda s: self.account_name(s["account_id"]).casefold()):
                plays = to_int(s["view_count"]) or 0
                offset = to_int(s["view_offset"]) or 0
                rating = to_float(s["rating"])
                last_played = local_datetime(s["last_viewed_at"])
                if not (plays or offset > 0 or rating is not None or last_played):
                    continue
                rows.append(dict(base, user=self.account_name(s["account_id"]), plays=plays,
                                 last_played=last_played, resume_at=hms(offset) if offset > 0 else "",
                                 rating=rating))

    @staticmethod
    def _part_streams(v, part=None) -> list:
        """Tracks of one file of a version (its first file by default).

        Each file of a multi-part version (CD1, CD2...) carries its own copy of the tracks, so counting
        the whole version would double them. Tracks not tied to a file count for every part.
        """
        if not v["parts"]:
            return v["streams"]
        part_id = (part or v["parts"][0])["id"]
        return [s for s in v["streams"] if s["media_part_id"] in (part_id, None)]

    def _version_summary(self, v, streams) -> dict:
        video = [s for s in streams if s["stream_type_id"] == 1]
        audio = [s for s in streams if s["stream_type_id"] == 2]
        subs = [s for s in streams if s["stream_type_id"] == 3]
        vs = next((s for s in video if s["is_default"]), video[0] if video else None)
        vx = vs["extra"] if vs else {}
        main_audio = next((s for s in audio if s["is_default"]), audio[0] if audio else None)
        width = to_int(v["width"]) or to_int(vx.get("ma:width"))
        height = to_int(v["height"]) or to_int(vx.get("ma:height"))
        codec = clean(v["video_codec"] or (vs["codec"] if vs else "")).lower()
        bitrate = to_int(v["bitrate"])
        aspect = to_float(v["display_aspect_ratio"])
        return {
            "resolution": resolution_label(width, height),
            "width": width, "height": height,
            "aspect_ratio": round(aspect, 2) if aspect else None,
            "video_codec": VIDEO_CODECS.get(codec, codec.upper()),
            "video_profile": clean(v["extra"].get("ma:videoProfile") or vx.get("ma:profile")),
            "bit_depth": to_int(vx.get("ma:bitDepth")),
            "hdr": hdr_label(vx, v["color_trc"]) if (vs or v["color_trc"]) else "",
            "frame_rate": round(f, 3) if (f := to_float(v["frames_per_second"]) or
                                          to_float(vx.get("ma:frameRate"))) else None,
            "container": clean(v["container"]).upper(),
            "bitrate_mbps": round(bitrate / 1_000_000, 1) if bitrate else None,
            "audio": audio_format(main_audio["codec"], main_audio["extra"].get("ma:profile"),
                                  main_audio["channels"], main_audio["extra"].get("ma:audioChannelLayout"))
            if main_audio else "",
            "audio_tracks": len(audio),
            "audio_languages": join(language_name(s["language"]) or "Unknown" for s in audio),
            "subtitle_tracks": len(subs),
            "subtitle_languages": join(language_name(s["language"]) or "Unknown" for s in subs),
        }

    def _media(self, row, m, versions, base, sheets):
        row["versions"] = len(versions)
        total = sum(to_int(p["size"]) or 0 for v in versions for p in v["parts"]) or \
            sum(to_int(v["size"]) or 0 for v in versions)
        row["total_size_gb"] = round(total / 1e9, 2) if total else None
        if not row.get("runtime_min") and versions and to_int(versions[0]["duration"]):
            row["runtime_min"] = round(to_int(versions[0]["duration"]) / 60000)
        for vi, v in enumerate(versions, 1):
            paths = [clean(p["file"]) for p in v["parts"] if p["file"]]
            size = sum(to_int(p["size"]) or 0 for p in v["parts"]) or to_int(v["size"]) or 0
            if vi == 1:
                row.update(self._version_summary(v, self._part_streams(v)))
                folder, name = split_path(paths[0]) if paths else ("", "")
                row.update(file_path=" | ".join(paths), file_name=name, folder=folder,
                           file_size_gb=round(size / 1e9, 2) if size else None, parts=len(v["parts"]))
            if S.FILES in sheets:
                for pi, p in enumerate(v["parts"], 1):
                    path = clean(p["file"])
                    folder, name = split_path(path) if path else ("", "")
                    psize = to_int(p["size"])
                    pdur = to_int(p["duration"]) or (to_int(v["duration"]) if len(v["parts"]) == 1 else None)
                    sheets[S.FILES].rows.append(dict(
                        base, **self._version_summary(v, self._part_streams(v, p)), version=vi, part=pi,
                        file_path=path, file_name=name, folder=folder,
                        size_bytes=psize, size_gb=round(psize / 1e9, 2) if psize else None,
                        duration_min=round(pdur / 60000, 1) if pdur else None,
                        file_hash=clean(p["hash"]), opensubtitles_hash=clean(p["open_subtitle_hash"])))
            if S.STREAMS in sheets:
                part_no = {p["id"]: i for i, p in enumerate(v["parts"], 1)}
                for s in v["streams"]:
                    sheets[S.STREAMS].rows.append(dict(base, version=vi, part=part_no.get(s["media_part_id"]),
                                                       **self._stream_row(s, v)))

    @staticmethod
    def _stream_row(s, v) -> dict:
        x = s["extra"]
        kind = to_int(s["stream_type_id"])
        codec = clean(s["codec"]).lower()
        bitrate = to_int(s["bitrate"])
        row = {
            "stream_index": to_int(s["idx"]),
            "stream_type": STREAM_TYPES.get(kind, f"Type {kind}"),
            "codec": codec,
            "language_code": clean(s["language"]),
            "language": language_name(s["language"]),
            "track_title": clean(x.get("ma:title")),
            "bitrate_kbps": round(bitrate / 1000) if bitrate else None,
            "bit_depth": to_int(x.get("ma:bitDepth")),
            "default": "Yes" if s["is_default"] else "No",
            "forced": "Yes" if s["forced"] else "No",
            "external": "Yes" if s["url"] else "No",
        }
        if kind == 1:
            w, h = to_int(x.get("ma:width")) or to_int(v["width"]), to_int(x.get("ma:height")) or to_int(v["height"])
            hdr = hdr_label(x, v["color_trc"])
            fps = to_float(x.get("ma:frameRate")) or to_float(v["frames_per_second"])
            row.update(resolution=f"{w}x{h}" if w and h else "", hdr=hdr,
                       frame_rate=round(fps, 3) if fps else None,
                       format=" ".join(p for p in (VIDEO_CODECS.get(codec, codec.upper()),
                                                   resolution_label(w, h), hdr if hdr != "SDR" else "") if p))
        elif kind == 2:
            layout = channel_layout(s["channels"], x.get("ma:audioChannelLayout"))
            row.update(channels=to_int(s["channels"]), channel_layout=layout,
                       sampling_rate=to_int(x.get("ma:samplingRate")),
                       format=audio_format(codec, x.get("ma:profile"), s["channels"], x.get("ma:audioChannelLayout")))
        elif kind == 3:
            row["format"] = SUBTITLE_CODECS.get(codec, codec.upper())
        else:
            row["format"] = codec.upper()
        return row

    def _misc(self, row, m, t, extras, base, sheets):
        markers = []
        for mk in t.get(TAG_MARKER, []):
            if mk.start is None:
                continue
            label = (mk.text or "marker").title()
            markers.append(f"{label} {hms(mk.start)}-{hms(mk.end)}" if mk.end is not None else f"{label} {hms(mk.start)}")
        counts = Counter(e["extra_type"] for e in extras)
        row.update(
            chapters=len(t.get(TAG_CHAPTER, [])) or None,
            markers=LIST_SEP.join(markers),
            extras=LIST_SEP.join(f"{kind} x{n}" for kind, n in counts.most_common()),
            poster_url=self._artwork(t.get(TAG_POSTER, []), m["user_thumb_url"]),
            art_url=self._artwork(t.get(TAG_ART, []), m["user_art_url"]),
            logo_url=self._artwork(t.get(TAG_CLEAR_LOGO, []), m["user_clear_logo_url"]),
            square_art_url=self._artwork(t.get(TAG_SQUARE_ART, []), m["user_square_art_url"]),
        )
        other = []
        for tag_type, items in sorted(t.items()):
            if tag_type in HANDLED_TAG_TYPES:
                continue
            label = OTHER_TAG_NAMES.get(tag_type, f"Tag type {tag_type}")
            other.extend(f"{label}: {x.tag}" for x in items if x.tag)
        row["other_tags"] = LIST_SEP.join(other)
        locked = parse_extra(m["user_fields"]).get("lockedFields", "")
        row["locked_fields"] = LIST_SEP.join(x for x in clean(locked).split("|") if x)
        if S.CHAPTERS in sheets:
            for i, ch in enumerate(t.get(TAG_CHAPTER, []), 1):
                sheets[S.CHAPTERS].rows.append(dict(
                    base, chapter=ch.index if ch.index else i, name=ch.tag, start=hms(ch.start), end=hms(ch.end),
                    start_sec=ch.start / 1000 if ch.start is not None else None,
                    end_sec=ch.end / 1000 if ch.end is not None else None))
        if S.EXTRAS in sheets:
            primary = re.search(r"(\d+)$", str(m["_extra"].get("ex:primaryExtraKey", "")))
            primary = int(primary.group(1)) if primary else None
            for e in extras:
                dur = e["duration_ms"]
                sheets[S.EXTRAS].rows.append(dict(
                    base, extra_type=e["extra_type"], extra_title=e["extra_title"], order=e["order"],
                    main_trailer="Yes" if e["extra_id"] == primary else "",
                    duration_min=round(dur / 60000, 1) if dur else None, released=e["released"],
                    explicit=e["explicit"], source=e["source"], location=e["location"],
                    thumbnail_url=e["thumbnail_url"], extra_id=e["extra_id"]))

    def _credits(self, row, m, t, versions, base, sheets):
        info = credits_info(t, versions, m["year"], self.final_flags)
        if info.credits_start is None:
            return                     # no credits markers (or none that are really credits): nothing to say
        row.update(
            stay_after_credits=info.verdict,
            credits_scenes=len(info.scenes),
            credits_scene_times=LIST_SEP.join(credit_scenes.describe(s) for s in info.scenes),
            credits_start=credit_scenes.clock(info.credits_start),
            runtime_before_credits_min=round(info.credits_start / 60000),
        )
        if S.CREDITS_SCENES in sheets:
            for i, s in enumerate(info.scenes, 1):
                sheets[S.CREDITS_SCENES].rows.append(dict(
                    base, scene=i, kind=s.kind, verdict=s.verdict, starts_at=credit_scenes.clock(s.start),
                    ends_at=credit_scenes.clock(s.end), length_sec=round(s.length / 1000), why_maybe=s.reason,
                    credits_before=f"{credit_scenes.clock(s.credits_before[0])}-{credit_scenes.clock(s.credits_before[1])}",
                    starts_at_sec=round(s.start / 1000, 1)))

    @staticmethod
    def _artwork(candidates: list[_Tag], selected) -> str:
        """Web address of the artwork Plex has selected (blank for custom uploads)."""
        selected = clean(selected)
        if selected.lower().startswith(("http://", "https://")):
            return selected
        for c in candidates:
            if selected and c.thumb == selected and c.text.lower().startswith(("http://", "https://")):
                return c.text
        if not selected:
            return next((c.text for c in candidates if c.text.lower().startswith(("http://", "https://"))), "")
        return ""

    def _collection_list(self, collections, smart, exported) -> list[dict]:
        rows = []
        for c in sorted(collections, key=lambda c: (self.lib_order.get(c["library_id"], 0), c["sort"])):
            result = smart.get(c["id"])
            if c["smart"]:
                status = result.status if result else "Not expanded."
                filter_text = result.filter_text if result else ""
            else:
                status, filter_text = "Regular collection - members are stored by Plex.", ""
            rows.append({
                "collection": c["title"], "library": self.lib_names.get(c["library_id"], ""),
                "collection_type": "Smart" if c["smart"] else "Regular", "plex_count": c["plex_count"],
                "exported": exported.get(c["id"], 0), "status": status, "filter": filter_text,
                "collection_summary": c["summary"],
                "collection_id": c["id"] if isinstance(c["id"], int) else None,
            })
        return rows

    def _history(self, sheet, views, devices, guid_to_movies):
        for v in views:
            matches = guid_to_movies.get(v["guid"], []) if v["guid"] else []
            match = next((b for m, b in matches if m["library_section_id"] == v["library_section_id"]),
                         matches[0][1] if matches else None)
            if match is None:
                # Movie no longer in the library: keep the play if it was in a chosen library.
                if v["library_section_id"] not in self.lib_names:
                    continue
                released = utc_date(v["originally_available_at"])
                match = {"plex_id": None, "title": clean(v["title"]), "year": released.year if released else None,
                         "library": self.lib_names[v["library_section_id"]]}
            sheet.rows.append(dict(match, viewed_at=local_datetime(v["viewed_at"]),
                                   user=self.account_name(v["account_id"]),
                                   device=devices.get(v["device_id"], "")))

    def _info(self, movie_count: int) -> list[tuple[str, object]]:
        path = self.db.path
        member = self.db.zip_member
        backup = _DATE_IN_NAME.search(posixpath.basename(member.replace("\\", "/"))) if member else None
        backup = backup or _DATE_IN_NAME.search(os.path.basename(path))
        now = datetime.now().astimezone()
        offset = now.strftime("%z")
        offset = f"UTC{offset[:3]}:{offset[3:]}" if offset else "local time"
        return [
            ("Exported by", f"{APP_NAME} {__version__}"),
            ("Exported at", now.replace(tzinfo=None, microsecond=0)),
            ("Source database", path),
            *([("Database inside the zip", member)] if member else []),
            ("Database file modified", local_datetime(int(os.path.getmtime(path)))),
            ("Plex backup date (from file name)", backup.group(1) if backup else ""),
            ("Plex database schema version", self.db.schema_version()),
            ("Movies exported", movie_count),
            ("Libraries exported", LIST_SEP.join(lib.name for lib in self.libraries)),
            ("Server owner (for 'Owner' columns)", self.account_name(self.owner_id)),
            ("Times", f"Dates and times are in the time zone of the computer that ran the export ({offset})."),
            ("Lists", f"Cells holding several values separate them with '{LIST_SEP.strip()}'."),
            ("Linking sheets", "Every sheet except Collection List has a Plex ID column - use it to join a sheet "
                               "back to Movies. Collection List has one row per collection: match it to the "
                               "Collections sheet by Collection and Library (its Collection Plex ID is the "
                               "collection's own ID, not a movie's)."),
        ]
