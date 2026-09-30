"""Builds Projectionist's source tarball. A development tool: the app itself never needs it.

    python tools/make_sdist.py                      writes dist/projectionist-<version>-src.tar.gz
    python tools/make_sdist.py --list               ...and lists every file in it
    python tools/make_sdist.py --mtime 2026-09-29   dates every file in it then (a date, an ISO time - UTC unless it
                                                    says otherwise - or seconds since 1970)
    python tools/make_sdist.py --out FOLDER         writes the tarball to another folder

Everything goes in one folder, projectionist-<version>/: the projectionist package (with its icon files in assets/),
tests/, tools/, linux/ (the applications-menu entry and its installer, when the project has them),
Projectionist.pyw, README.md, a LICENSE if there is one, and requirements.txt - the project's own if it has one,
else one written here (XlsxWriter; Pillow as optional). The version is projectionist.__version__, read from
projectionist/__init__.py without importing it.

Only source goes in. Nothing else in the project folder is looked at: not the Plex database backups or the
spreadsheets beside them, dist/ or .claude/. Inside those folders only files of the kinds in SOURCE_SUFFIXES are
taken, and never __pycache__ or .pyc files, hidden files, databases (com.plexapp..., -wal, -shm), spreadsheets or
CSV files, archives or temporary files. Nor tests/local/ (LOCAL_ONLY): tests that read one person's own Plex
backup and check its numbers and names stay on their computer. Anything left out there is named, with why.

The same source gives the same bytes every time: files in name order with forward slashes, owner and group 0 with no
names, 644 for files and 755 for folders, one modification time for all of them (--mtime, else SOURCE_DATE_EPOCH,
else the newest source file's), and no time or name in the gzip header.
"""

from __future__ import annotations

import argparse
import ast
import datetime
import gzip
import hashlib
import io
import os
import re
import sys
import tarfile
import tempfile
from dataclasses import dataclass, field

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE = "projectionist"
SOURCE_DIRS = (PACKAGE, "tests", "tools")           # taken whole: the source files in them
OPTIONAL_DIRS = ("linux",)                          # ...and these too, when the project has them
LOCAL_ONLY = ("tests/local",)                       # ...but never these: tests against someone's own Plex backup
TOP_FILES = ("Projectionist.pyw", "README.md")      # (must be there)
LICENSE_NAMES = ("LICENSE", "LICENSE.txt", "LICENSE.md", "COPYING")
REQUIREMENTS = "requirements.txt"
SOURCE_SUFFIXES = (".py", ".pyw", ".md", ".ico", ".png", ".desktop")
FILE_MODE, DIR_MODE = 0o644, 0o755

REQUIREMENTS_TEXT = """\
# Projectionist {version} needs Python 3.10 or newer, with tkinter (it comes with Python on Windows; on Linux it's
# your distribution's python3-tk - README.md, "On Linux", has the commands), and:
#     python -m pip install -r requirements.txt
XlsxWriter          # writes the .xlsx spreadsheets

# Optional. Only tools/make_icon.py (it redraws the icon) and the PNG previews a few tests draw use it, and those
# tests skip without it. The app itself never needs it:
# Pillow
"""

# never shipped, wherever they turn up: (test, why)
_NEVER = (
    (lambda n: n == "__pycache__" or n.endswith((".pyc", ".pyo")), "compiled Python"),
    (lambda n: n.startswith("."), "hidden"),
    (lambda n: n.startswith("com.plexapp"), "a Plex database"),
    (lambda n: n.endswith((".db", ".sqlite", ".sqlite3", "-wal", "-shm", "-journal")), "a database"),
    (lambda n: n.endswith((".xlsx", ".xlsm", ".xls", ".ods", ".csv", ".tsv")), "a spreadsheet or CSV file"),
    (lambda n: n.endswith((".zip", ".gz", ".tgz", ".tar", ".7z")), "an archive"),
    (lambda n: n in ("dist", "build") or n.endswith(".egg-info"), "build output"),
    (lambda n: n.startswith("~") or n.endswith((".tmp", ".bak", ".orig", ".rej", ".swp", "~")), "a temporary file"),
)


def never_ship(name: str) -> str | None:
    """Why a file or folder of this name never goes in a source tarball, or None."""
    low = name.lower()
    for test, why in _NEVER:
        if test(low):
            return why
    return None


def local_only(path: str) -> str | None:
    """Why a path in the project ('tests/local/test_x.py', forward slashes) never goes in a tarball because of
    the folder it's in (LOCAL_ONLY), or None."""
    low = path.lower().strip("/")
    if any(low == folder or low.startswith(folder + "/") for folder in LOCAL_ONLY):
        return "local only: tests of one person's own Plex backup"
    return None


def left_out(name: str) -> str | None:
    """Why a file of this name in one of SOURCE_DIRS stays out of the tarball, or None when it goes in."""
    why = never_ship(name)
    if why is None and not name.lower().endswith(SOURCE_SUFFIXES):
        why = "not source"
    return why


def read_version(root: str = ROOT) -> str:
    """projectionist.__version__, read from projectionist/__init__.py (importing it would write a __pycache__)."""
    path = os.path.join(root, PACKAGE, "__init__.py")
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), path)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__version__"
                                                for t in node.targets):
            version = ast.literal_eval(node.value)
            if not isinstance(version, str) or not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z.+_-]*", version):
                raise ValueError(f"{path}: __version__ isn't a version: {version!r}")
            return version
    raise ValueError(f"{path} has no __version__")


def parse_mtime(text: str) -> int:
    """A date ('2026-09-29'), an ISO time ('2026-09-29T15:30', UTC unless it gives an offset) or seconds since 1970
    -> whole seconds since 1970."""
    text = str(text).strip()
    if re.fullmatch(r"\d+", text):
        return int(text)
    if text[-1:] in ("Z", "z"):                     # (UTC written as Z: Python before 3.11 doesn't read it)
        text = text[:-1] + "+00:00"
    try:
        when = datetime.datetime.fromisoformat(text)
    except ValueError:
        raise ValueError(f"not a date, time or number of seconds: {text!r}") from None
    if when.tzinfo is None:
        when = when.replace(tzinfo=datetime.timezone.utc)
    seconds = int(when.timestamp())
    if seconds < 0:
        raise ValueError(f"before 1970: {text!r}")
    return seconds


@dataclass
class Sources:
    files: list            # [(path inside the top folder, source file)], in order
    left: list             # [(path in the project, why)] for what the source folders hold that stays out
    requirements: str | None = None     # the project's requirements.txt, or None (one is written)
    licence: str | None = None          # the LICENSE file, or None

    @property
    def newest(self) -> int:
        return max(int(os.stat(path).st_mtime) for _name, path in self.files)


def collect(root: str = ROOT) -> Sources:
    """What goes in the tarball, from the project folder `root`."""
    files, left = [], []
    for folder in SOURCE_DIRS + OPTIONAL_DIRS:
        base = os.path.join(root, folder)
        if not os.path.isdir(base):
            if folder in OPTIONAL_DIRS:
                continue
            raise FileNotFoundError(f"{base} isn't there")
        for here, dirs, names in os.walk(base):
            rel = os.path.relpath(here, root).replace(os.sep, "/")
            keep = []
            for name in sorted(dirs):
                why = never_ship(name) or ("a link" if os.path.islink(os.path.join(here, name)) else None) or \
                    local_only(f"{rel}/{name}")
                if why:
                    left.append((f"{rel}/{name}/", why))
                else:
                    keep.append(name)
            dirs[:] = keep
            for name in sorted(names):
                path = os.path.join(here, name)
                why = left_out(name) or ("a link" if os.path.islink(path) else None)
                if why:
                    left.append((f"{rel}/{name}", why))
                else:
                    files.append((f"{rel}/{name}", path))
    for name in TOP_FILES:
        path = os.path.join(root, name)
        if not os.path.isfile(path):
            raise FileNotFoundError(f"{path} isn't there")
        files.append((name, path))
    licence = next((os.path.join(root, n) for n in LICENSE_NAMES if os.path.isfile(os.path.join(root, n))), None)
    if licence:
        files.append((os.path.basename(licence), licence))
    requirements = os.path.join(root, REQUIREMENTS)
    requirements = requirements if os.path.isfile(requirements) else None
    if requirements:
        files.append((REQUIREMENTS, requirements))
    for name, _path in files:                      # (a last check, whatever the lists above come to say)
        why = next(filter(None, map(never_ship, name.split("/"))), None) or local_only(name)
        if why:
            raise ValueError(f"{name} would go in the tarball, but it's {why}")
    return Sources(files, left, requirements, licence)


def _order(name: str):
    return name.split("/")          # (a folder, then what's in it)


def _info(name: str, mtime: int, size: int = 0, folder: bool = False) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type = tarfile.DIRTYPE if folder else tarfile.REGTYPE
    info.mode = DIR_MODE if folder else FILE_MODE
    info.size = 0 if folder else size
    info.mtime = mtime
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    return info


def tarball_bytes(top: str, entries: list, mtime: int) -> bytes:
    """A .tar.gz of `entries` ([(path inside `top`, bytes)]) under the folder `top`, with every folder it needs."""
    folders = {top}
    for name, _data in entries:
        parts = name.split("/")[:-1]
        folders.update("/".join([top] + parts[:i]) for i in range(1, len(parts) + 1))
    members = sorted([(f, None) for f in folders] + [(f"{top}/{n}", d) for n, d in entries], key=lambda m: _order(m[0]))
    raw = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tar:
            for name, data in members:
                if data is None:
                    tar.addfile(_info(name, mtime, folder=True))
                else:
                    tar.addfile(_info(name, mtime, len(data)), io.BytesIO(data))
    return raw.getvalue()


@dataclass
class Build:
    path: str
    version: str
    size: int
    sha256: str
    mtime: int
    names: list                          # the files in it, as the tarball names them
    left: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    @property
    def file_count(self) -> int:
        return len(self.names)


def _umask() -> int:
    """The process's umask (read by setting it, then putting it back): what a file saved the usual way leaves out."""
    mask = os.umask(0o022)
    os.umask(mask)
    return mask


def build(root: str = ROOT, out: str | None = None, mtime: int | None = None) -> Build:
    """Writes <out>/projectionist-<version>-src.tar.gz (out: <root>/dist) from the project folder `root`."""
    version = read_version(root)
    sources = collect(root)
    if mtime is None:
        epoch = os.environ.get("SOURCE_DATE_EPOCH", "").strip()
        mtime = parse_mtime(epoch) if epoch else sources.newest
    entries = []
    for name, path in sources.files:
        with open(path, "rb") as f:
            entries.append((name, f.read()))
    if sources.requirements is None:
        entries.append((REQUIREMENTS, REQUIREMENTS_TEXT.format(version=version).encode("utf-8")))
    top = f"{PACKAGE}-{version}"
    data = tarball_bytes(top, entries, mtime)

    out = os.path.abspath(os.path.join(root, "dist") if out is None else out)
    os.makedirs(out, exist_ok=True)
    path = os.path.join(out, f"{top}-src.tar.gz")
    fd, temp = tempfile.mkstemp(prefix=f"~{top}-", suffix=".tmp", dir=out)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.chmod(temp, 0o666 & ~_umask())        # (mkstemp's file is the owner's alone: this one is to be shared)
        os.replace(temp, path)                   # (all or nothing)
    except BaseException:
        try:
            os.remove(temp)
        except OSError:
            pass
        raise
    notes = []
    if sources.licence is None:
        notes.append(f"There's no LICENSE file in {root}, so the tarball has none. Add one ({', '.join(LICENSE_NAMES)}) "
                     "to say how others may use the code.")
    if sources.requirements is None:
        notes.append(f"{REQUIREMENTS} is written here: XlsxWriter, with Pillow as optional.")
    names = sorted((f"{top}/{n}" for n, _d in entries), key=_order)
    return Build(path, version, len(data), hashlib.sha256(data).hexdigest(), mtime, names, sources.left, notes)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Build Projectionist's source tarball (.tar.gz).")
    parser.add_argument("--out", help="folder to write it to (default: dist in the project folder)")
    parser.add_argument("--mtime", metavar="WHEN",
                        help="the date every file gets: YYYY-MM-DD, an ISO time (UTC unless it says) or seconds since "
                             "1970 (default: SOURCE_DATE_EPOCH, else the newest source file's)")
    parser.add_argument("--list", action="store_true", help="list every file in it")
    args = parser.parse_args(argv)
    mtime = None
    if args.mtime is not None:
        try:
            mtime = parse_mtime(args.mtime)
        except ValueError as exc:
            parser.error(f"--mtime: {exc}")
    try:
        result = build(out=args.out, mtime=mtime)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    when = datetime.datetime.fromtimestamp(result.mtime, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"Wrote {result.path}")
    print(f"  {result.file_count} files, {result.size:,} bytes, every one dated {when}")
    print(f"  SHA-256 {result.sha256}")
    if args.list:
        for name in result.names:
            print(f"    {name}")
    for name, why in result.left:
        print(f"Left out: {name} ({why})")
    for note in result.notes:
        print(f"Note: {note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
