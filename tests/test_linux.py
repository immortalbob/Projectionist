"""What the app needs away from Windows - Linux above all - tested on every system (Windows too: the Linux paths
are chosen by hand where they need to be): the mouse wheel as X11 sends it, the box that says XlsxWriter is
missing, the PNG previews on an older Pillow, a details text that isn't laid out yet (which on Linux could have
made the X server refuse to draw it, ending the app), the window's class, and the applications-menu entry
(linux/). Windows are withdrawn, never shown; nothing is written outside temporary folders."""

import configparser
import importlib.util
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from projectionist import gui  # noqa: E402
from projectionist.ui import theme as T  # noqa: E402
from projectionist.ui import widgets as W  # noqa: E402


def _load_installer():
    """linux/install.py (a script, not a module on the path)."""
    spec = importlib.util.spec_from_file_location("projectionist_linux_install",
                                                  os.path.join(ROOT, "linux", "install.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


desktop = _load_installer()


def hidden_root():
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()                       # headless: never shown
    T.apply_styles(root)
    return root


def wheel(num=None, delta=0):
    return types.SimpleNamespace(num=num if num is not None else "??", delta=delta, x_root=0, y_root=0)


# ---------------------------------------------------------------------------------------------------------
class WheelTests(unittest.TestCase):
    """X11 (Linux) sends the wheel as buttons 4 and 5, not <MouseWheel>: wherever the app binds the wheel it binds
    those too, and each notch scrolls a line, as on Windows."""

    def setUp(self):
        try:
            self.root = hidden_root()
        except Exception as exc:          # noqa: BLE001
            self.skipTest(f"Tk unavailable: {exc}")

    def tearDown(self):
        self.root.destroy()

    def test_how_far_each_wheel_event_scrolls(self):
        self.assertEqual(W.wheel_units(wheel(num=4)), -1)          # X11: up a line...
        self.assertEqual(W.wheel_units(wheel(num=5)), 1)           # ...down a line
        with mock.patch.object(sys, "platform", "win32"):          # Windows: as the app always did it
            self.assertEqual(W.wheel_units(wheel(delta=120)), -1)
            self.assertEqual(W.wheel_units(wheel(delta=-240)), 2)
            self.assertEqual(W.wheel_units(wheel(delta=60)), 0)    # (part of a notch: nothing)
        with mock.patch.object(sys, "platform", "darwin"):         # a Mac: its delta is lines already
            self.assertEqual(W.wheel_units(wheel(delta=3)), -3)
        self.assertEqual(W.wheel_units(types.SimpleNamespace()), 0)

    def test_the_buttons_are_bound_on_x11(self):
        import tkinter as tk
        frame = tk.Frame(self.root)
        W.bind_wheel(frame, lambda e: None)
        bound = frame.bind()
        self.assertIn("<MouseWheel>", bound)
        x11 = self.root._windowingsystem == "x11"
        self.assertEqual("<Button-4>" in bound and "<Button-5>" in bound, x11)
        W.ScrollFrame(self.root)                                    # (the window-wide binding for every page)
        everywhere = self.root.bind_class("all")
        self.assertIn("<MouseWheel>", everywhere)
        self.assertEqual("<Button-5>" in everywhere, x11)

    def test_a_page_scrolls_by_button_5_and_4(self):
        import tkinter as tk
        page = W.ScrollFrame(self.root)
        page.place(x=0, y=0, width=300, height=200)
        tk.Frame(page.inner, height=2000, width=280).pack()
        self.root.update()
        self.assertFalse(page.fits())
        with mock.patch.object(page, "winfo_containing", lambda *a: page.canvas):
            page._wheel(wheel(num=5))
            self.assertGreater(page.canvas.yview()[0], 0.0)
            page._wheel(wheel(num=4))
            self.assertEqual(page.canvas.yview()[0], 0.0)
            # Shift+wheel comes as the same buttons (with Shift held): the page scrolls, as it does on Windows
            page._wheel(types.SimpleNamespace(num=5, delta=0, x_root=0, y_root=0, state=0x0001))
            self.assertGreater(page.canvas.yview()[0], 0.0)

    def test_watch_next_s_critic_texts_pass_the_wheel_to_the_page(self):
        from projectionist.ui import watchnext
        from test_ui_watchnext import FakeApp
        from tkinter import ttk
        tab = watchnext.Tab(FakeApp(), ttk.Notebook(self.root))
        for t in tab._critic_texts:
            bound = t.bind()
            self.assertIn("<MouseWheel>", bound)
            self.assertEqual("<Button-4>" in bound, self.root._windowingsystem == "x11")
            self.assertEqual(tab._critic_wheel(wheel(num=5)), "break")        # (the text itself doesn't scroll)


# ---------------------------------------------------------------------------------------------------------
class TickBoxTests(unittest.TestCase):
    """Away from Windows the tick box is the app's own drawing - clam's is an X in Tk 8.6.12 and before (most Linux
    systems), which reads more like 'no' - in the look's colours and the display's scale."""

    def setUp(self):
        try:
            self.root = hidden_root()
        except Exception as exc:          # noqa: BLE001
            self.skipTest(f"Tk unavailable: {exc}")

    def tearDown(self):
        T.use("light")
        self.root.destroy()

    def test_the_drawing(self):
        t = T.tokens("graphite")
        fill, edge, tick = T.check_specs(t)["selected"]
        rows = T.check_pixels(fill, edge, tick, 13)
        self.assertEqual((len(rows), len(rows[0])), (13, 13))
        self.assertEqual(rows[0][0], edge)
        self.assertEqual(rows[6][1], fill)                                      # (inside the edge, off the tick)
        self.assertIn(tick, {c for r in rows for c in r})                        # the tick, at full strength
        empty = T.check_pixels(*T.check_specs(t)["normal"], 13)
        self.assertEqual({c for r in empty[1:-1] for c in r[1:-1]}, {t["CHECK_BG"]})   # (no tick when not ticked)
        self.assertEqual(len(T.check_pixels(fill, edge, tick, 26)), 26)          # (twice the size at 2x)
        self.assertEqual(T.check_pixels(fill, edge, tick, 26)[1][0], edge)      # (and a 2-px edge)

    def test_every_tick_box_style_uses_it_away_from_windows(self):
        def rgb(color):
            return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))
        with mock.patch.object(T, "WINDOWS", False), mock.patch.object(T, "NATIVE_LIGHT", False):
            for look in ("graphite", "velvet", "light"):
                style = T.apply(self.root, look)
                self.assertIn("LookCheck.indicator", str(style.layout("TCheckbutton")))
                images = self.root.__dict__["_look_check_images"]
                self.assertEqual(tuple(images["normal"].get(0, 0)), rgb(T.CHECK_BORDER), look)   # (repainted)
                self.assertEqual(tuple(images["selected"].get(1, 6)), rgb(T.CHECK_ON), look)
        other = hidden_root()                                   # (this system's own, in a window of its own)
        try:
            style = T.apply(other, "graphite")
            self.assertEqual("LookCheck.indicator" in str(style.layout("TCheckbutton")), sys.platform != "win32")
        finally:
            other.destroy()


# ---------------------------------------------------------------------------------------------------------
class TableHeadingTests(unittest.TestCase):
    """The tables' column widths were chosen for Windows' Segoe UI; away from Windows a column is widened when its
    heading (and the sort arrow) doesn't fit in the font the headings are drawn in - measured, not guessed."""

    def setUp(self):
        try:
            self.root = hidden_root()
        except Exception as exc:          # noqa: BLE001
            self.skipTest(f"Tk unavailable: {exc}")

    def tearDown(self):
        self.root.destroy()

    def test_headings_fit_away_from_windows(self):
        from tkinter import font as tkfont
        from tkinter import ttk
        columns = [("title", "Title", 200, "w"), ("predicted", "Predicted", 20, "e"), ("year", "Year", 300, "e")]
        with mock.patch.object(T, "WINDOWS", False):
            table = W.Table(self.root, columns)
        font = tkfont.Font(root=self.root, font=ttk.Style(self.root).lookup("Treeview.Heading", "font"))
        self.assertEqual(table.widened, ["predicted"])
        self.assertGreaterEqual(int(table.tree.column("predicted", "width")), font.measure("Predicted ▼"))
        self.assertEqual(int(table.tree.column("year", "width")), int(300 * T.scale(self.root)))   # (roomy: as it was)
        self.assertGreater(dict((k, w) for k, _h, w, _a in table.columns)["predicted"], 20)
        for sequence in ("<<TreeviewSelect>>", "<Double-Button-1>", "<Key-Return>"):       # (a table as ever)
            self.assertIn(sequence, table.tree.bind())
        with mock.patch.object(T, "WINDOWS", True):                                          # Windows: as it was
            table = W.Table(self.root, columns)
        self.assertEqual(table.widened, [])
        self.assertEqual(int(table.tree.column("predicted", "width")), int(20 * T.scale(self.root)))

    def test_every_column_shown_fits_the_list(self):
        """ttk's list cuts off what doesn't fit rather than narrowing its stretching columns: with a column made
        wider for its heading, the stretching ones share what's left - never below their least."""
        import tkinter as tk
        columns = [("title", "Title", 260, "w"), ("predicted", "Predicted", 20, "e"), ("scenes", "Scenes", 200, "w")]
        holder = tk.Frame(self.root)
        holder.place(x=0, y=0, width=420, height=200)
        with mock.patch.object(T, "WINDOWS", False):
            table = W.Table(holder, columns)
        table.tree.column("title", minwidth=100)
        table.pack(fill="both", expand=True)
        self.root.update()
        table.fit_width()
        tree = table.tree
        widths = {k: int(tree.column(k, "width")) for k, *_ in columns}
        self.assertLessEqual(sum(widths.values()), tree.winfo_width())
        self.assertGreaterEqual(widths["title"], 100)
        self.assertGreater(widths["title"], widths["scenes"])                   # (in proportion to their own)
        self.assertFalse(table.fit_width())                                      # (fitting: nothing more to do)
        # a list whose width follows its columns (nothing holds it) never has a column made wider - so it can't
        # grow without end (it once did: tens of thousands of pixels, which the X server refused to draw)
        free = tk.Frame(self.root)
        free.place(x=0, y=0)
        with mock.patch.object(T, "WINDOWS", False):
            loose = W.Table(free, columns)
        loose.pack()
        before = {k: int(loose.tree.column(k, "width")) for k, *_ in columns}
        for _ in range(5):
            self.root.update()
            loose.fit_width()
            after = {k: int(loose.tree.column(k, "width")) for k, *_ in columns}
            self.assertTrue(all(after[k] <= before[k] for k in before), (before, after))
        self.assertLess(loose.tree.winfo_width(), 2000)

    def test_credits_list_makes_room_for_its_wider_columns(self):
        from projectionist.ui import credits
        with mock.patch.object(T, "WINDOWS", False), mock.patch.object(credits, "MIN_WIDTHS",
                                                                         dict(credits.MIN_WIDTHS, credits_start=10)):
            table = credits.FilmTable(self.root)
        self.assertIn("credits_start", table.widened)
        self.assertGreater(table.min_widths["credits_start"], 10)      # (its heading's width, not the table's 10)


# ---------------------------------------------------------------------------------------------------------
class DetailsTextTests(unittest.TestCase):
    """Watch Next's details texts are as tall as what's in them - but not while they're a few pixels wide (before
    the page is laid out), when every word would be a line of its own: the page would have been made tens of
    thousands of pixels tall, and on Linux the X server refuses to draw that (BadAlloc), which ended the app."""

    def setUp(self):
        try:
            self.root = hidden_root()
        except Exception as exc:          # noqa: BLE001
            self.skipTest(f"Tk unavailable: {exc}")

    def tearDown(self):
        self.root.destroy()

    def test_not_fitted_until_it_has_its_width_and_never_too_tall(self):
        import tkinter as tk
        from projectionist.ui import watchnext

        class Host:
            FIT_LEAST_WIDTH = watchnext.Tab.FIT_LEAST_WIDTH
            FIT_MOST_LINES = watchnext.Tab.FIT_MOST_LINES
            s = 1.0
            _fit_text = watchnext.Tab._fit_text
        host = Host()
        holder = tk.Frame(self.root)
        holder.place(x=0, y=0, width=12, height=400)
        t = tk.Text(holder, wrap="word", height=6, width=30, font=T.font(self.root, 9))
        t.pack(fill="both", expand=True)
        t.insert("end", "A few words about a critic and the films you share with them. " * 12)
        self.root.update_idletasks()
        self.assertLess(t.winfo_width(), host.FIT_LEAST_WIDTH)
        host._fit_text(t)
        self.assertEqual(int(t.cget("height")), 6)                     # (a few pixels wide: left as it was)
        holder.place_configure(width=400)
        self.root.update_idletasks()
        host._fit_text(t)
        fitted = int(t.cget("height"))
        self.assertTrue(1 < fitted < 40, fitted)                       # (as tall as its lines)
        t.insert("end", "\n".join(str(n) for n in range(2000)))
        host._fit_text(t)
        self.assertEqual(int(t.cget("height")), host.FIT_MOST_LINES)   # (never taller than this)


# ---------------------------------------------------------------------------------------------------------
class PilPainterTests(unittest.TestCase):
    """The PNG previews on an older Pillow (Ubuntu 22.04 has 9.0): rounded_rectangle without 'corners', and
    load_default() without a size."""

    def setUp(self):
        try:
            import PIL  # noqa: F401
        except ImportError:
            self.skipTest("Pillow isn't installed (only the previews need it)")
        from projectionist.ui.paint import PilPainter
        self.PilPainter = PilPainter

    def test_square_corners_without_corners(self):
        p = self.PilPainter(40, 20, background="#000000")
        real = p.draw.rounded_rectangle

        def old_pillow(*args, **kwargs):
            if "corners" in kwargs:
                raise TypeError("rounded_rectangle() got an unexpected keyword argument 'corners'")
            return real(*args, **kwargs)
        p.draw.rounded_rectangle = old_pillow
        p.round_rect(0, 0, 40, 20, 8, "#ff0000", corners=(False, True, True, False))   # (a bar growing right)
        k = p.SUPERSAMPLE
        self.assertEqual(p.image.getpixel((0, 0)), (255, 0, 0))                   # top left: square
        self.assertEqual(p.image.getpixel((0, 20 * k - 1)), (255, 0, 0))          # bottom left: square
        self.assertEqual(p.image.getpixel((40 * k - 1, 0)), (0, 0, 0))            # top right: rounded
        self.assertEqual(p.image.getpixel((40 * k - 1, 20 * k - 1)), (0, 0, 0))   # bottom right: rounded

    def test_no_font_file_and_no_sized_default(self):
        from PIL import ImageFont
        real_default, real_truetype = ImageFont.load_default, ImageFont.truetype

        def old_default(*args):
            if args:
                raise TypeError("load_default() takes 0 positional arguments")
            return real_default()

        def no_font_files(font, *args, **kwargs):
            if isinstance(font, str):                                        # (a file: none of them there)
                raise OSError("cannot open resource")
            return real_truetype(font, *args, **kwargs)                      # (Pillow's own built-in one)
        with mock.patch.object(ImageFont, "truetype", no_font_files), \
                mock.patch.object(ImageFont, "load_default", old_default):
            p = self.PilPainter(120, 40)
            self.assertIsNotNone(p._pilfont(p.font(9)))
            p.text(10, 20, "Films", p.font(9), "#ffffff", "w")                  # (a bitmap font: no anchor)
            self.assertGreater(p.text_width("Films", p.font(9)), 0)


# ---------------------------------------------------------------------------------------------------------
class StartingUpTests(unittest.TestCase):
    def test_the_box_for_a_missing_xlsxwriter_says_how_on_each_system(self):
        windows = gui.xlsxwriter_missing(r"C:\Python314\pythonw.exe", "win32")
        self.assertEqual(windows, "This app needs the free XlsxWriter package, which isn't installed for this copy "
                                  "of Python (C:\\Python314\\python.exe).\n\nInstall it by opening a Command Prompt "
                                  "and running:\n\n    \"C:\\Python314\\python.exe\" -m pip install XlsxWriter\n\n"
                                  "then start the app again.")                   # (as it always said)
        linux = gui.xlsxwriter_missing("/usr/bin/python3", "linux")
        for words in ("sudo apt install python3-xlsxwriter", "sudo dnf install python3-xlsxwriter",
                      "sudo pacman -S python-xlsxwriter", "(/usr/bin/python3)", "in a virtual environment"):
            self.assertIn(words, linux)
        self.assertNotIn("--user", linux)      # (Debian 12, Ubuntu 23.04 and later refuse pip outside a venv)
        self.assertNotIn("Command Prompt", linux)
        self.assertIn("Terminal", gui.xlsxwriter_missing("/usr/local/bin/python3", "darwin"))

    def test_the_words_for_a_missing_tkinter(self):
        from projectionist import needs
        linux = needs.tkinter_missing("/usr/bin/python3", "linux")
        for words in ("isn't installed for this copy of Python (/usr/bin/python3)", "sudo apt install python3-tk",
                      "sudo dnf install python3-tkinter", "sudo pacman -S tk", "sudo zypper install python3-tk"):
            self.assertIn(words, linux)
        self.assertIn("tcl/tk and IDLE", needs.tkinter_missing(r"C:\Python314\pythonw.exe", "win32"))
        self.assertIn(r"(C:\Python314\python.exe)", needs.tkinter_missing(r"C:\Python314\pythonw.exe", "win32"))
        self.assertIn("brew install python-tk", needs.tkinter_missing("/usr/local/bin/python3", "darwin"))
        self.assertIs(gui.xlsxwriter_missing, needs.xlsxwriter_missing)
        self.assertTrue(needs.is_tkinter_missing(ModuleNotFoundError("No module named 'tkinter'", name="tkinter")))
        self.assertTrue(needs.is_tkinter_missing(ImportError("no _tkinter", name="_tkinter")))
        self.assertFalse(needs.is_tkinter_missing(ModuleNotFoundError("No module named 'x'", name="xlsxwriter")))
        self.assertFalse(needs.is_tkinter_missing(ValueError("tkinter")))

    def run_without(self, module, *args):
        """Run Python on the app with `module` not installed (a stand-in that fails to import, as a missing one
        does), in a separate process - no window can open without tkinter. -> (exit code, what it printed)."""
        import subprocess
        with tempfile.TemporaryDirectory() as fake:
            os.makedirs(os.path.join(fake, module))
            with open(os.path.join(fake, module, "__init__.py"), "w", encoding="utf-8") as f:
                f.write(f"raise ModuleNotFoundError(\"No module named '{module}'\", name={module!r})\n")
            env = dict(os.environ, PYTHONPATH=os.pathsep.join([fake, ROOT]), PYTHONDONTWRITEBYTECODE="1",
                       PYTHONIOENCODING="utf-8")
            done = subprocess.run([sys.executable, "-B", *args], cwd=ROOT, env=env, capture_output=True,
                                  text=True, encoding="utf-8", timeout=120, stdin=subprocess.DEVNULL)
        return done.returncode, done.stdout + done.stderr

    def test_without_tkinter_it_says_what_to_install(self):
        """A Linux Python without its tkinter package (Debian's and Ubuntu's come without it): both ways of
        starting the app say what to install - no traceback."""
        from projectionist import needs
        for args in ((os.path.join(ROOT, "Projectionist.pyw"),), ("-m", "projectionist")):
            code, said = self.run_without("tkinter", *args)
            self.assertEqual(code, 1, said)
            self.assertIn("Projectionist needs tkinter", said)
            self.assertIn(needs.tkinter_missing().splitlines()[-1], said)
            self.assertNotIn("Traceback", said)

    def test_the_command_line_without_xlsxwriter(self):
        """An export from the command line without XlsxWriter says how to install it, before reading anything;
        listing the libraries doesn't need it."""
        from test_projectionist import build_fixture
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "com.plexapp.plugins.library.db-2026-09-25")
            build_fixture(db)
            code, said = self.run_without("xlsxwriter", "-m", "projectionist", db, "-o", os.path.join(d, "x.xlsx"))
            self.assertEqual(code, 1, said)
            self.assertIn("XlsxWriter", said)
            self.assertIn("pip install", said)
            self.assertNotIn("Traceback", said)
            self.assertFalse(os.path.exists(os.path.join(d, "x.xlsx")))
            code, said = self.run_without("xlsxwriter", "-m", "projectionist", db, "--list-libraries")
            self.assertEqual(code, 0, said)

    @unittest.skipIf(sys.platform == "win32", "the window's class matters to X11 and Wayland desktops")
    def test_the_window_is_the_apps_own_class(self):
        """So the desktop matches it to projectionist.desktop (StartupWMClass): its name and icon in the dock."""
        try:
            root = gui._new_root()
        except Exception as exc:          # noqa: BLE001
            self.skipTest(f"Tk unavailable: {exc}")
        try:
            root.withdraw()
            self.assertEqual(root.winfo_class(), "Projectionist")
        finally:
            root.destroy()

    def test_windows_only_things_are_skipped_elsewhere(self):
        """The DPI awareness, the 1 ms timer, the taskbar name and the dark title bar are Windows' own: away from
        it they do nothing and never fail."""
        from projectionist import appicon
        interval = sys.getswitchinterval()
        try:
            with mock.patch.object(sys, "platform", "linux"):
                self.assertIsNone(gui._enable_dpi_awareness())
                restore = gui._share_time_with_the_window()
                restore()
                self.assertFalse(appicon.set_app_id())
                self.assertFalse(T.title_bar(object()))
        finally:
            sys.setswitchinterval(interval)


# ---------------------------------------------------------------------------------------------------------
class SavedFileTests(unittest.TestCase):
    """The spreadsheets are written to a temporary file first (tempfile.mkstemp: its owner's alone on Linux), then
    moved into place: they end up with a new file's usual permissions, or those of the file they replace."""

    def test_usual_permissions(self):
        from projectionist import export
        with tempfile.TemporaryDirectory() as d:
            fd, temp = tempfile.mkstemp(dir=d)
            os.close(fd)
            final = os.path.join(d, "Projectionist Movies.xlsx")
            if os.name == "nt":
                export.usual_permissions(temp, final)             # (nothing to do, and no error)
                self.assertTrue(os.access(temp, os.W_OK))
                return
            self.assertEqual(os.stat(temp).st_mode & 0o777, 0o600)
            old = os.umask(0o027)
            try:
                self.assertEqual(export._umask(), 0o027)
                export.usual_permissions(temp, final)             # a new file: 0666 less the umask
                self.assertEqual(os.stat(temp).st_mode & 0o777, 0o640)
            finally:
                os.umask(old)
            with open(final, "wb"):
                pass
            os.chmod(final, 0o664)
            export.usual_permissions(temp, final)                 # replacing one: its permissions
            self.assertEqual(os.stat(temp).st_mode & 0o777, 0o664)
            export.usual_permissions(os.path.join(d, "gone"), final)      # (never fails)


# ---------------------------------------------------------------------------------------------------------
class PythonTenTests(unittest.TestCase):
    """Python 3.10 is enough (Ubuntu 22.04's): what's newer than that has a stand-in."""

    def test_times_written_with_z(self):
        from projectionist import letterboxd
        self.assertEqual(letterboxd.since_time("2026-09-23T21:30Z"), letterboxd.since_time("2026-09-23T21:30+00:00"))
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        try:
            import make_sdist
        finally:
            sys.path.remove(os.path.join(ROOT, "tools"))
        self.assertEqual(make_sdist.parse_mtime("2026-09-29T00:00Z"), make_sdist.parse_mtime("2026-09-29"))

    def test_batched_stands_in(self):
        from projectionist import costars
        self.assertEqual([tuple(b) for b in costars.batched(range(5), 2)], [(0, 1), (2, 3), (4,)])

    def test_the_source_needs_nothing_newer(self):
        """Every source file parses as Python 3.10's grammar, whatever Python runs the tests (no 'except*', no
        'type X = ...', no generic 'def f[T]'...) - as far as the parser checks. (The suite run on 3.10 itself is
        the real test.)"""
        import ast
        with open(os.path.join(ROOT, "Projectionist.pyw"), encoding="utf-8") as f:
            ast.parse(f.read(), "Projectionist.pyw", feature_version=(3, 10))
        for folder in ("projectionist", "tests", "tools", "linux"):
            for here, dirs, files in os.walk(os.path.join(ROOT, folder)):
                dirs[:] = [d for d in dirs if d != "__pycache__"]
                for name in files:
                    if name.endswith((".py", ".pyw")):
                        path = os.path.join(here, name)
                        with open(path, encoding="utf-8") as f:
                            ast.parse(f.read(), path, feature_version=(3, 10))


# ---------------------------------------------------------------------------------------------------------
class DesktopEntryTests(unittest.TestCase):
    """linux/projectionist.desktop and linux/install.py, which puts it in the applications menu - for you only.
    Everything is written into a temporary 'home'."""

    def test_the_entry_is_well_formed(self):
        parser = configparser.ConfigParser(interpolation=None, strict=True)
        parser.optionxform = str
        with open(desktop.TEMPLATE, encoding="utf-8") as f:
            parser.read_string(f.read())
        e = parser["Desktop Entry"]
        self.assertEqual((e["Type"], e["Name"], e["Terminal"], e["StartupWMClass"]),
                         ("Application", "Projectionist", "false", "Projectionist"))
        self.assertTrue(e["Categories"].endswith(";") and e["Keywords"].endswith(";"))
        self.assertNotIn("Plex", e["Name"])                                  # (the app's name isn't Plex's)

    def test_exec_arguments_are_quoted_as_the_standard_says(self):
        self.assertEqual(desktop.quote("/usr/bin/python3"), "/usr/bin/python3")
        self.assertEqual(desktop.quote("/home/me/My Apps/Projectionist.pyw"), '"/home/me/My Apps/Projectionist.pyw"')
        self.assertEqual(desktop.quote('/a "b" $c`d'), '"/a \\"b\\" \\$c\\`d"')
        self.assertEqual(desktop.quote("/films/100%"), "/films/100%%")
        # the line is a string value too: its backslashes doubled again
        self.assertEqual(desktop.exec_line("/usr/bin/python3", "/x/it's here/P.pyw"),
                         '/usr/bin/python3 "/x/it\'s here/P.pyw"')
        self.assertEqual(desktop.exec_line("/a$b"), '"/a\\\\$b"')

    def test_install_and_remove_in_your_own_folders(self):
        with tempfile.TemporaryDirectory() as home:
            env = {"HOME": home}
            where = desktop.places(env)
            self.assertEqual(where["entry"], os.path.join(home, ".local", "share", "applications",
                                                          "projectionist.desktop"))
            written = desktop.install(env, python="/usr/bin/python3", refresh=False)
            self.assertIn(where["entry"], written)
            with open(where["entry"], encoding="utf-8") as f:
                text = f.read()
            self.assertIn("\nExec=/usr/bin/python3 " + desktop.quote(desktop.LAUNCHER).replace("\\", "\\\\") + "\n",
                          text)
            self.assertIn("\nIcon=projectionist\n", text)
            self.assertNotIn("/path/to", text)
            self.assertNotIn("\n#", text)
            for size, path in where["icons"].items():
                self.assertTrue(os.path.isfile(path), size)
                self.assertIn(f"{size}x{size}", path)
            self.assertEqual(sorted(desktop.remove(env, refresh=False)), sorted(written))
            self.assertFalse(os.path.exists(where["entry"]))
            self.assertEqual(desktop.remove(env, refresh=False), [])
        # $XDG_DATA_HOME, when it's a full path
        self.assertEqual(desktop.data_home({"HOME": "/home/x", "XDG_DATA_HOME": "/data/share"}), "/data/share")
        self.assertEqual(desktop.data_home({"HOME": "/home/x", "XDG_DATA_HOME": "share"}),
                         os.path.join("/home/x", ".local", "share"))

    def test_an_icon_cache_is_only_updated_never_made(self):
        """gtk-update-icon-cache makes a cache GTK then trusts: one made in ~/.local/share/icons/hicolor would hide
        the icons other apps put there later. update-desktop-database makes a mimeinfo.cache that --remove would
        leave behind (the entry opens no file types, so it never needs one). So each only updates a cache that's
        there already."""
        with tempfile.TemporaryDirectory() as share:
            ran = []
            with mock.patch.object(desktop.shutil, "which", lambda name: "/usr/bin/" + name), \
                    mock.patch.object(desktop.subprocess, "run", lambda command, **kw: ran.append(command[0])):
                desktop._refresh(share)
                self.assertEqual(ran, [])
                hicolor = os.path.join(share, "icons", "hicolor")
                os.makedirs(hicolor)
                open(os.path.join(hicolor, "icon-theme.cache"), "wb").close()
                desktop._refresh(share)
                self.assertEqual(ran, ["gtk-update-icon-cache"])
                applications = os.path.join(share, "applications")
                os.makedirs(applications)
                open(os.path.join(applications, "mimeinfo.cache"), "wb").close()
                ran.clear()
                desktop._refresh(share)
                self.assertEqual(ran, ["update-desktop-database", "gtk-update-icon-cache"])

    def test_nothing_is_left_behind_with_the_desktops_own_tools(self):
        """Installed, then removed, with update-desktop-database and gtk-update-icon-cache run for real wherever
        they're installed: the home folder has no files left at all."""
        with tempfile.TemporaryDirectory() as home:
            env = {"HOME": home}
            self.assertTrue(desktop.install(env, python="/usr/bin/python3"))
            desktop.remove(env)
            self.assertEqual([os.path.join(here, n) for here, _dirs, names in os.walk(home) for n in names], [])

    def test_the_entry_names_no_file_types(self):
        """(Why the menu's mimeinfo.cache is never made: an entry with a MimeType line would need it.)"""
        self.assertNotIn("MimeType=", desktop.entry("/usr/bin/python3", "/opt/p/Projectionist.pyw", "projectionist"))

    def test_the_readme_says_how(self):
        """README.md's On Linux: the packages for each distribution, starting it, the menu entry, where a Linux
        Plex server keeps its database and backups (and to use a dated backup), and where the settings go."""
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("## On Linux", text)
        for words in ("sudo apt install python3-tk python3-xlsxwriter", "sudo dnf install python3-tkinter",
                      "sudo pacman -S tk python-xlsxwriter", "python3 Projectionist.pyw", "python3 linux/install.py",
                      "/var/lib/plexmediaserver/Library/Application Support/Plex Media Server/Plug-in Support/"
                      "Databases/", "dated backups", "~/.config/projectionist/settings.json", "Follow the system",
                      "xvfb-run -a python3 -m unittest discover -s tests", "sudo apt install python3-venv",
                      "sudo apt install fonts-noto-cjk"):
            self.assertIn(words, text)
        with open(os.path.join(ROOT, "tools", "make_sdist.py"), encoding="utf-8") as f:
            self.assertIn("Python 3.10 or newer", f.read())

    @unittest.skipUnless(sys.platform == "win32", "what it says on Windows")
    def test_on_windows_it_says_to_double_click_instead(self):
        with mock.patch("sys.stderr"):
            self.assertEqual(desktop.main([]), 2)


if __name__ == "__main__":
    unittest.main()
