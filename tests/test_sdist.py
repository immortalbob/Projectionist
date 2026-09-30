"""The source tarball (tools/make_sdist.py): what goes in and what never does, the same bytes from the same source,
requirements.txt and LICENSE, and a tarball of the real project that unpacks and runs. Every build goes to a
temporary folder; nothing here writes to dist/. No windows."""

import contextlib
import hashlib
import io
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import projectionist  # noqa: E402
import make_sdist as M  # noqa: E402
from support import safe_extract  # noqa: E402

WHEN = 1790640000                           # 2026-09-29 00:00 UTC
TOP = "projectionist-9.8.7"

SOURCE = {                                  # a small project folder like the real one...
    "projectionist/__init__.py": '"""Projectionist."""\n\n__version__ = "9.8.7"\n',
    "projectionist/gui.py": "x = 1\n",
    "projectionist/ui/__init__.py": "",
    "projectionist/ui/theme.py": "y = 2\r\n",               # (bytes kept as they are)
    "projectionist/assets/projectionist.ico": b"\0\0\1\0",
    "projectionist/assets/projectionist-16.png": b"\x89PNG\r\n",
    "tests/test_x.py": "import unittest\n",
    "tools/make_x.py": "print('x')\n",
    "Projectionist.pyw": "import projectionist\n",
    "README.md": "# Projectionist\n",
}
NOT_SOURCE = {                              # ...with everything that must stay out of a tarball beside it
    "projectionist/__pycache__/gui.cpython-314.pyc": b"pyc",
    "projectionist/ui/__pycache__/theme.cpython-314.pyc": b"pyc",
    "projectionist/stray.pyc": b"pyc",
    "projectionist/Plex Movies 2026-09-25.xlsx": b"PK",
    "projectionist/assets/~$lock.png": b"",
    "tests/com.plexapp.plugins.library.db-2026-09-25": b"SQLite format 3\0",
    "tests/scratch.db-wal": b"",
    "tests/out.csv": "a,b\n",
    "tests/notes.txt": "to do\n",
    "tests/.pytest_cache/v/x": "",
    "tests/local/__init__.py": '"""Tests of my own backup."""\n',       # (source, but one person's own)
    "tests/local/test_my_backup.py": "import unittest\n",
    "tests/local/more/test_deeper.py": "import unittest\n",
    "tools/dist/projectionist-9.8.6-src.tar.gz": b"",
    "tools/sheet.png.bak": b"",
    "com.plexapp.plugins.library.db-2026-09-25": b"SQLite format 3\0",
    "com.plexapp.plugins.library.db-2026-09-25-wal": b"",
    "com.plexapp.plugins.library.db-2026-09-25-shm": b"",
    "com.plexapp.plugins.library.blobs.db-2026-09-25": b"",
    "Plex Movies 2026-09-25.xlsx": b"PK",
    "Projectionist Letterboxd 2026-09-25.csv": "x\n",
    "dist/projectionist-9.8.6-src.tar.gz": b"",
    ".claude/settings.json": "{}",
    "notes.txt": "mine\n",
}


def make_project(folder, files):
    for name, data in files.items():
        path = os.path.join(folder, *name.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data.encode("utf-8") if isinstance(data, str) else data)
    return folder


def members(path):
    with tarfile.open(path) as tar:
        return tar.getmembers()


def read(path, name):
    with tarfile.open(path) as tar:
        return tar.extractfile(name).read()


class SdistTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = make_project(os.path.join(self.tmp.name, "project"), {**SOURCE, **NOT_SOURCE})
        self.out = os.path.join(self.tmp.name, "out")
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("SOURCE_DATE_EPOCH", None)

    def build(self, **kw):
        kw.setdefault("out", self.out)
        kw.setdefault("mtime", WHEN)
        return M.build(self.project, **kw)

    def test_only_source_goes_in(self):
        result = self.build()
        self.assertEqual(result.path, os.path.join(self.out, f"{TOP}-src.tar.gz"))
        self.assertEqual(result.version, "9.8.7")
        expected = sorted(f"{TOP}/{n}" for n in list(SOURCE) + ["requirements.txt"])
        self.assertEqual(sorted(result.names), expected)
        self.assertEqual(result.file_count, len(SOURCE) + 1)
        files = [m.name for m in members(result.path) if m.isfile()]
        self.assertEqual(sorted(files), expected)
        folders = [m.name for m in members(result.path) if m.isdir()]
        self.assertEqual(folders, [TOP, f"{TOP}/projectionist", f"{TOP}/projectionist/assets",
                                   f"{TOP}/projectionist/ui", f"{TOP}/tests", f"{TOP}/tools"])
        for name, data in SOURCE.items():                     # (byte for byte, line endings and all)
            data = data.encode("utf-8") if isinstance(data, str) else data
            self.assertEqual(read(result.path, f"{TOP}/{name}"), data, name)
        # what the source folders held that stayed out is named, with why; the rest of the project isn't looked at
        left = dict(result.left)
        self.assertEqual(left, {
            "projectionist/__pycache__/": "compiled Python",
            "projectionist/stray.pyc": "compiled Python",
            "projectionist/Plex Movies 2026-09-25.xlsx": "a spreadsheet or CSV file",
            "projectionist/assets/~$lock.png": "a temporary file",
            "projectionist/ui/__pycache__/": "compiled Python",
            "tests/.pytest_cache/": "hidden",
            "tests/local/": "local only: tests of one person's own Plex backup",
            "tests/com.plexapp.plugins.library.db-2026-09-25": "a Plex database",
            "tests/scratch.db-wal": "a database",
            "tests/out.csv": "a spreadsheet or CSV file",
            "tests/notes.txt": "not source",
            "tools/dist/": "build output",
            "tools/sheet.png.bak": "a temporary file",
        })

    def test_every_entry_is_the_same_whoever_builds_it(self):
        result = self.build()
        for m in members(result.path):
            self.assertIn(m.type, (tarfile.REGTYPE, tarfile.DIRTYPE), m.name)
            self.assertEqual((m.uid, m.gid, m.uname, m.gname), (0, 0, "", ""), m.name)
            self.assertEqual(m.mode, 0o755 if m.isdir() else 0o644, m.name)
            self.assertEqual(m.mtime, WHEN, m.name)
            self.assertEqual(m.pax_headers, {}, m.name)
            self.assertTrue(m.name == TOP or m.name.startswith(TOP + "/"), m.name)
            self.assertNotIn("\\", m.name)
            self.assertNotIn("..", m.name.split("/"))
        with open(result.path, "rb") as f:
            head = f.read(10)
        self.assertEqual(head[:3], b"\x1f\x8b\x08")
        self.assertEqual(head[3], 0)                    # no file name in the gzip header...
        self.assertEqual(head[4:8], b"\0\0\0\0")        # ...and no time
        # it unpacks with Python's safe 'data' filter, as it is
        unpacked = os.path.join(self.tmp.name, "unpacked")
        with tarfile.open(result.path) as tar:
            safe_extract(tar, unpacked)
        self.assertEqual(int(os.stat(os.path.join(unpacked, TOP, "README.md")).st_mtime), WHEN)

    def test_the_same_source_gives_the_same_bytes(self):
        first = self.build()
        with open(first.path, "rb") as f:
            data = f.read()
        self.assertEqual(first.size, len(data))
        self.assertEqual(first.sha256, hashlib.sha256(data).hexdigest())
        again = self.build(out=os.path.join(self.tmp.name, "again"))
        self.assertEqual(again.sha256, first.sha256)
        self.assertEqual(os.listdir(self.out), [os.path.basename(first.path)])      # (no temporary file left)
        # another date: other bytes, and the old tarball is replaced
        later = self.build(mtime=WHEN + 86400)
        self.assertNotEqual(later.sha256, first.sha256)
        self.assertEqual(os.listdir(self.out), [os.path.basename(first.path)])
        self.assertEqual({m.mtime for m in members(later.path)}, {WHEN + 86400})

    def test_the_tarball_is_saved_like_any_other_file(self):
        """Written through a temporary file (mkstemp's: its owner's alone), it still gets the usual permissions -
        on Linux, others can read it, as they can any file saved there - never a private file."""
        result = self.build()
        mode = os.stat(result.path).st_mode & 0o777
        if os.name == "nt":
            self.assertTrue(os.access(result.path, os.W_OK))       # (not left read-only)
            return
        old = os.umask(0o022)
        try:
            self.assertEqual(os.stat(self.build(out=os.path.join(self.tmp.name, "u022")).path).st_mode & 0o777, 0o644)
            os.umask(0o077)
            self.assertEqual(os.stat(self.build(out=os.path.join(self.tmp.name, "u077")).path).st_mode & 0o777, 0o600)
        finally:
            os.umask(old)
        self.assertEqual(mode, 0o666 & ~old)

    def test_the_date_it_gives_every_file(self):
        for i, name in enumerate(SOURCE):
            os.utime(os.path.join(self.project, *name.split("/")), (WHEN - 1000 + i, WHEN - 1000 + i))
        os.utime(os.path.join(self.project, "notes.txt"), (WHEN + 5000, WHEN + 5000))     # (not in it: no say)
        result = self.build(mtime=None)                            # the newest source file's
        self.assertEqual(result.mtime, WHEN - 1000 + len(SOURCE) - 1)
        self.assertEqual({m.mtime for m in members(result.path)}, {result.mtime})
        self.assertEqual(self.build(mtime=None).sha256, result.sha256)
        os.environ["SOURCE_DATE_EPOCH"] = str(WHEN)                # the reproducible-builds convention
        self.assertEqual(self.build(mtime=None).mtime, WHEN)
        self.assertEqual(self.build(mtime=WHEN + 60).mtime, WHEN + 60)      # (--mtime beats it)
        os.environ["SOURCE_DATE_EPOCH"] = "soon"
        with self.assertRaises(ValueError):
            self.build(mtime=None)

    def test_parse_mtime(self):
        self.assertEqual(M.parse_mtime("2026-09-29"), WHEN)
        self.assertEqual(M.parse_mtime(" 2026-09-29T01:30 "), WHEN + 5400)            # (UTC)
        self.assertEqual(M.parse_mtime("2026-09-29T02:00:00+02:00"), WHEN)
        self.assertEqual(M.parse_mtime(str(WHEN)), WHEN)
        for bad in ("yesterday", "", "1969-12-31", "-5", "29/09/2026"):
            with self.assertRaises(ValueError, msg=bad):
                M.parse_mtime(bad)

    def test_requirements(self):
        result = self.build()
        text = read(result.path, f"{TOP}/requirements.txt").decode("utf-8")
        needed = [line.split("#")[0].strip() for line in text.splitlines() if line.split("#")[0].strip()]
        self.assertEqual(needed, ["XlsxWriter"])                   # (what pip installs)
        self.assertIn("# Pillow", text)                            # optional: named, not installed
        self.assertIn("tools/make_icon.py", text)
        self.assertIn("9.8.7", text)
        self.assertTrue(any("requirements.txt is written here" in n for n in result.notes))
        self.assertFalse(os.path.exists(os.path.join(self.project, "requirements.txt")))    # (only in the tarball)
        # the project's own, when it has one
        make_project(self.project, {"requirements.txt": "XlsxWriter>=3.0\n"})
        result = self.build()
        self.assertEqual(read(result.path, f"{TOP}/requirements.txt"), b"XlsxWriter>=3.0\n")
        self.assertFalse(any("requirements.txt" in n for n in result.notes))
        self.assertEqual(result.file_count, len(SOURCE) + 1)

    def test_licence(self):
        result = self.build()
        self.assertFalse(any(n.split("/")[-1].startswith("LICENSE") for n in result.names))
        self.assertTrue(any("no LICENSE file" in n for n in result.notes), result.notes)
        make_project(self.project, {"LICENSE.txt": "Copyright\n"})
        result = self.build()
        self.assertIn(f"{TOP}/LICENSE.txt", result.names)
        self.assertEqual(read(result.path, f"{TOP}/LICENSE.txt"), b"Copyright\n")
        self.assertFalse(any("LICENSE" in n for n in result.notes))

    def test_version(self):
        self.assertEqual(M.read_version(ROOT), projectionist.__version__)
        self.assertEqual(M.read_version(self.project), "9.8.7")
        init = os.path.join(self.project, "projectionist", "__init__.py")
        for text in ('APP_NAME = "Projectionist"\n', '__version__ = "1.0 beta"\n', "__version__ = 1.0\n",
                     '__version__ = "../1"\n'):
            with open(init, "w", encoding="utf-8") as f:
                f.write(text)
            with self.assertRaises(ValueError, msg=text):
                self.build()
        self.assertFalse(os.path.exists(self.out))

    def test_missing_pieces_stop_it(self):
        os.remove(os.path.join(self.project, "README.md"))
        with self.assertRaises(FileNotFoundError):
            self.build()
        make_project(self.project, {"README.md": "# Projectionist\n"})
        os.rename(os.path.join(self.project, "tools"), os.path.join(self.project, "tools-old"))
        with self.assertRaises(FileNotFoundError):
            self.build()
        self.assertFalse(os.path.exists(self.out))

    def test_a_last_check_whatever_the_lists_say(self):
        with mock.patch.object(M, "TOP_FILES", M.TOP_FILES + ("Plex Movies 2026-09-25.xlsx",)):
            with self.assertRaisesRegex(ValueError, "spreadsheet"):
                self.build()
        with mock.patch.object(M, "OPTIONAL_DIRS", M.OPTIONAL_DIRS + ("tests/local",)):
            with self.assertRaisesRegex(ValueError, "local only"):
                self.build()
        self.assertFalse(os.path.exists(self.out))

    def test_local_tests_never_go_in(self):
        """tests/local/ holds tests of one person's own Plex backup: its numbers, names and films. However it's
        spelt, nothing in it goes in a tarball, and the rest of tests/ does."""
        result = self.build()
        self.assertFalse([n for n in result.names if "/local/" in n or n.endswith("/local")], result.names)
        self.assertIn(f"{TOP}/tests/test_x.py", result.names)
        for path in ("tests/local", "tests/local/", "tests/local/test_x.py", "Tests/Local/x/y.py", "/tests/local"):
            self.assertTrue(M.local_only(path), path)
        for path in ("tests", "tests/localish.py", "tests/test_local.py", "local/test_x.py", "tools/local/x.py"):
            self.assertIsNone(M.local_only(path), path)
        self.assertIsNone(M.never_ship("local"))                   # (only that folder, not the word)

    def test_never_ship_and_left_out(self):
        for name, why in (("__pycache__", "compiled Python"), ("x.pyc", "compiled Python"), (".git", "hidden"),
                          ("com.plexapp.plugins.library.db", "a Plex database"), ("my.sqlite", "a database"),
                          ("x.db-shm", "a database"), ("Out.XLSX", "a spreadsheet or CSV file"),
                          ("a.csv", "a spreadsheet or CSV file"), ("Download.zip", "an archive"),
                          ("dist", "build output"), ("x.egg-info", "build output"), ("~$book.py", "a temporary file"),
                          ("gui.py~", "a temporary file")):
            self.assertEqual(M.never_ship(name), why, name)
            self.assertEqual(M.left_out(name), why, name)
        for name in ("gui.py", "Projectionist.pyw", "README.md", "projectionist.ico", "Icon-16.PNG"):
            self.assertIsNone(M.left_out(name), name)
        self.assertEqual(M.left_out("notes.txt"), "not source")
        self.assertIsNone(M.never_ship("LICENSE"))


class RealProjectTest(unittest.TestCase):
    """A tarball of this very project: the source and nothing else, and it runs where it's unpacked."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        with mock.patch.dict(os.environ):
            os.environ.pop("SOURCE_DATE_EPOCH", None)
            cls.result = M.build(ROOT, out=cls.tmp.name, mtime=WHEN)
        cls.top = f"projectionist-{projectionist.__version__}"

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_what_is_in_it(self):
        top, names = self.top, self.result.names
        self.assertEqual(os.path.basename(self.result.path), f"{top}-src.tar.gz")
        for name in ("Projectionist.pyw", "README.md", "requirements.txt", "projectionist/__init__.py",
                     "projectionist/__main__.py", "projectionist/ui/theme.py", "projectionist/assets/projectionist.ico",
                     "projectionist/assets/projectionist-16.png", "projectionist/assets/projectionist-256.png",
                     "tests/test_sdist.py", "tests/test_projectionist.py", "tests/support.py", "tools/make_icon.py",
                     "tools/make_sdist.py", "tools/build_exe.py", "tools/exe_main.py", "tools/installer.py",
                     "linux/projectionist.desktop", "linux/install.py"):
            self.assertIn(f"{top}/{name}", names)
        if os.path.isfile(os.path.join(ROOT, "LICENSE")):                    # (how others may use the code)
            self.assertIn(f"{top}/LICENSE", names)
        local = os.path.join(ROOT, "tests", "local")
        for folder in ("projectionist", "tests", "tools", "linux"):    # every source file there...
            for here, dirs, files in os.walk(os.path.join(ROOT, folder)):
                dirs[:] = [d for d in dirs if d != "__pycache__" and os.path.join(here, d) != local]
                for name in files:
                    if name.endswith((".py", ".ico", ".png", ".desktop")):
                        rel = os.path.relpath(os.path.join(here, name), ROOT).replace(os.sep, "/")
                        self.assertIn(f"{top}/{rel}", names)
        for name in names:
            low = name.lower()
            self.assertNotIn("__pycache__", low)
            self.assertFalse(low.endswith((".pyc", ".xlsx", ".csv", ".db", "-wal", "-shm", ".zip")), name)
            self.assertNotIn("com.plexapp", low)
            self.assertNotIn("/dist/", low)
            self.assertNotIn("/.", name)
            self.assertFalse(low.startswith(f"{top}/tests/local/".lower()), name)   # ...but tests/local's
        self.assertEqual(len(names), len(set(names)))
        if os.path.isdir(local):
            self.assertIn(("tests/local/", M.local_only("tests/local")), self.result.left)

    def test_it_runs_where_it_is_unpacked(self):
        unpacked = os.path.join(self.tmp.name, "unpacked")
        with tarfile.open(self.result.path) as tar:
            safe_extract(tar, unpacked)
        folder = os.path.join(unpacked, self.top)
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        run = subprocess.run([sys.executable, "-B", "-m", "projectionist", "--version"], cwd=folder, env=env,
                             capture_output=True, text=True, timeout=120)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(run.stdout.strip(), f"{projectionist.APP_NAME} {projectionist.__version__}")
        run = subprocess.run([sys.executable, "-B", "-c", "import projectionist; print(projectionist.__file__)"],
                             cwd=folder, env=env, capture_output=True, text=True, timeout=120)
        self.assertTrue(os.path.normcase(run.stdout.strip()).startswith(os.path.normcase(folder)), run.stdout)
        self.assertFalse(any(os.path.basename(p) == "__pycache__" for p, _d, _f in os.walk(folder)))

    def test_the_command(self):
        out = os.path.join(self.tmp.name, "command")
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            self.assertEqual(M.main(["--out", out, "--mtime", "2026-09-29", "--list"]), 0)
        text = printed.getvalue()
        path = os.path.join(out, f"{self.top}-src.tar.gz")
        with open(path, "rb") as f:
            sha = hashlib.sha256(f.read()).hexdigest()
        count = sum(m.isfile() for m in members(path))
        self.assertIn(f"Wrote {path}", text)
        self.assertIn(f"SHA-256 {sha}", text)
        self.assertIn(f"  {count} files, {os.path.getsize(path):,} bytes", text)
        self.assertIn("every one dated 2026-09-29 00:00:00 UTC", text)
        self.assertIn(f"    {self.top}/README.md", text)
        # a date it can't read, and a build that fails
        with contextlib.redirect_stderr(io.StringIO()) as err, self.assertRaises(SystemExit) as stop:
            M.main(["--out", out, "--mtime", "someday"])
        self.assertEqual(stop.exception.code, 2)
        self.assertIn("--mtime", err.getvalue())
        with mock.patch.object(M, "build", side_effect=OSError("disk full")), \
                contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(M.main(["--out", out]), 1)
        self.assertIn("disk full", err.getvalue())


if __name__ == "__main__":
    unittest.main()
