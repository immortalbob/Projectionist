"""Builds Projectionist for Windows with PyInstaller: the app as a folder that runs without Python, and a Setup.exe
that installs it for one user. A development tool: the app itself never needs it.

    python tools/build_exe.py                   builds both into dist/, then checks them
    python tools/build_exe.py --check-db FILE   ...and checks the built app against that Plex database too
    python tools/build_exe.py --no-setup        only the app's folder
    python tools/build_exe.py --out FOLDER      puts them in another folder than dist/
    python tools/build_exe.py --work FOLDER     PyInstaller's working files go there (default: a temporary folder,
                                                deleted afterwards); --keep-work keeps them

It needs Windows, Python 3.10 or newer, PyInstaller 6 (py -m pip install pyinstaller) and XlsxWriter. What it makes,
for version <v> (projectionist.__version__):

    dist/Projectionist-<v>-win64/          Projectionist.exe and its _internal folder (Python, Tk, the app, its
                                           icon files, XlsxWriter), with README.md and LICENSE: copy the folder
                                           anywhere and run the .exe - no Python needed
    dist/Projectionist-<v>-Setup.exe       one file with that folder inside: installs it for you only
                                           (tools/installer.py says how), with an Uninstall.exe

In three PyInstaller runs, from specs written here into the working folder: the app (one folder, no console, the
app's icon, Windows version details), Uninstall.exe and Setup.exe (one file each; tools/installer.py frozen, the
second with the app's folder and Uninstall.exe packed in as payload.zip). Pillow, the tests and the tools stay out.
Nothing is written into the project folder but the two results in dist/ (or --out); an older build of the same
version there is replaced. The same source, Python and PyInstaller give the same files, byte for byte: everything
is dated SOURCE_DATE_EPOCH, else the newest source file's date (as tools/make_sdist.py dates the tarball).

Then the checks, which show nothing on screen: the build holds every module of the app and none of what's left
out; each .exe carries its version details and asks for no administrator rights; Projectionist.exe --version and
--self-test (projectionist/selftest.py: the window built hidden, every tab visited, an export - against --check-db's
database when given); and Setup.exe --silent installing into the working folder - every place, the registry entry
included, pointed there - then the installed Uninstall.exe --silent removing it all again.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

TOOLS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TOOLS)
sys.path.insert(0, TOOLS)

import installer  # noqa: E402
import make_sdist  # noqa: E402

APP = installer.APP
PACKAGE = "projectionist"
ASSETS = os.path.join(ROOT, PACKAGE, "assets")
ICON = os.path.join(ASSETS, "projectionist.ico")
ENTRY = os.path.join(TOOLS, "exe_main.py")
INSTALLER = os.path.join(TOOLS, "installer.py")
EXTRA_FILES = ("README.md", "LICENSE")               # beside Projectionist.exe in its folder (when they're there)
PUBLISHER = installer.PUBLISHER
COPYRIGHT = f"Copyright (c) 2026 {PUBLISHER}. MIT License."
# Never in any of the three: they're not needed, and would only make the download bigger. (No secure connections
# are ever made - the app reads a file - so there's no OpenSSL: hashlib has its own sha256 and md5 without it.)
EXCLUDES = ["PIL", "numpy", "tests", "tools", "unittest", "pydoc", "doctest", "pdb", "lib2to3", "setuptools",
            "pkg_resources", "distutils", "idlelib", "turtledemo", "turtle", "test", "pytest", "_pytest", "IPython",
            "ssl", "_ssl", "_hashlib"]
# ...nor, in Setup.exe and Uninstall.exe, the app's own reading and writing, or the web
INSTALLER_EXCLUDES = EXCLUDES + ["sqlite3", "_sqlite3", "xlsxwriter", "projectionist.gui", "projectionist.catalog",
                                 "projectionist.extract", "projectionist.export", "http", "urllib.request", "email",
                                 "ftplib", "decimal", "_decimal", "statistics", "fractions"]
# Tcl's time-zone files: only Tcl's own clock command reads them, and nothing in the app uses it
TCL_LEFT_OUT = "_tcl_data/tzdata/"
# ...and Uninstall.exe asks through a Windows message box: no Tk, no looks
UNINSTALLER_EXCLUDES = INSTALLER_EXCLUDES + ["tkinter", "_tkinter", "projectionist.ui"]


class BuildError(Exception):
    pass


# ---------------------------------------------------------------------------------------------------------
# Specs
# ---------------------------------------------------------------------------------------------------------
def version_numbers(version: str) -> tuple:
    """'1.0.0' -> (1, 0, 0, 0); '1.2b3' -> (1, 2, 0, 0): Windows' four numbers, from the leading digits of each part."""
    numbers = []
    for part in version.split(".")[:4]:
        digits = re.match(r"\d*", part).group()
        numbers.append(min(int(digits or 0), 65535))
    return tuple(numbers + [0] * (4 - len(numbers)))


def version_info(version: str, description: str, filename: str) -> str:
    """A PyInstaller version file: the details Windows shows in a file's Properties > Details, and Task Manager's
    name for it (FileDescription)."""
    numbers = version_numbers(version)
    strings = [("CompanyName", PUBLISHER), ("FileDescription", description), ("FileVersion", version),
               ("InternalName", os.path.splitext(filename)[0]), ("LegalCopyright", COPYRIGHT),
               ("OriginalFilename", filename), ("ProductName", APP), ("ProductVersion", version)]
    table = ",\n".join(f"        StringStruct({k!r}, {v!r})" for k, v in strings)
    return f"""# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(filevers={numbers}, prodvers={numbers}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1,
                    subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
{table}])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def package_modules(root: str = ROOT) -> list[str]:
    """Every module in the projectionist package (read from the folder, not imported): the tabs are imported by
    name while the app runs, so PyInstaller has to be told about them."""
    base = os.path.join(root, PACKAGE)
    names = []
    for here, dirs, files in os.walk(base):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__" and not d.startswith(".")
                         and os.path.isfile(os.path.join(here, d, "__init__.py")))
        rel = os.path.relpath(here, root).replace(os.sep, ".")
        for name in sorted(files):
            if name.endswith(".py"):
                names.append(rel if name == "__init__.py" else f"{rel}.{name[:-3]}")
    return sorted(names)


def app_spec(version: str, version_file: str, root: str = ROOT) -> str:
    """The spec for the app: one folder, no console."""
    folder = f"{APP}-{version}-win64"
    return f"""# Written by tools/build_exe.py - PyInstaller spec for {APP} {version} (one folder)
a = Analysis([{ENTRY!r}], pathex=[{root!r}], binaries=[], datas=[({ASSETS!r}, 'projectionist/assets')],
             hiddenimports={package_modules(root)!r}, hookspath=[], runtime_hooks=[], excludes={EXCLUDES!r},
             noarchive=False, optimize=0)
a.datas = [d for d in a.datas if not d[0].replace('\\\\', '/').startswith({TCL_LEFT_OUT!r})]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name={APP!r}, debug=False, bootloader_ignore_signals=False,
          strip=False, upx=False, console=False, disable_windowed_traceback=False, icon=[{ICON!r}],
          version={version_file!r})
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name={folder!r})
"""


def onefile_spec(name: str, version_file: str, excludes: list, datas: list, root: str = ROOT) -> str:
    """The spec for Setup.exe or Uninstall.exe: tools/installer.py as one file, no console."""
    return f"""# Written by tools/build_exe.py - PyInstaller spec for {name}.exe (one file)
a = Analysis([{INSTALLER!r}], pathex=[{root!r}, {TOOLS!r}], binaries=[], datas={datas!r}, hiddenimports=[],
             hookspath=[], runtime_hooks=[], excludes={excludes!r}, noarchive=False, optimize=0)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name={name!r}, debug=False, bootloader_ignore_signals=False,
          strip=False, upx=False, runtime_tmpdir=None, console=False, disable_windowed_traceback=False,
          icon=[{ICON!r}], version={version_file!r})
"""


# ---------------------------------------------------------------------------------------------------------
# Running PyInstaller
# ---------------------------------------------------------------------------------------------------------
def pyinstaller_version() -> str | None:
    try:
        import PyInstaller
        return PyInstaller.__version__
    except ImportError:
        return None


def build_date() -> int:
    """The date every build of the same source carries (seconds since 1970): SOURCE_DATE_EPOCH, else the newest
    source file's - so the same source gives the same files, byte for byte."""
    epoch = os.environ.get("SOURCE_DATE_EPOCH", "").strip()
    return make_sdist.parse_mtime(epoch) if epoch else make_sdist.collect(ROOT).newest


def run_pyinstaller(spec: str, dist: str, work: str, log: str, when: int):
    """PyInstaller on a spec - its own process, writing nothing into the project (no __pycache__ either), with the
    .exe's own date set to `when` rather than now."""
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONHASHSEED="0", SOURCE_DATE_EPOCH=str(when))
    command = [sys.executable, "-B", "-m", "PyInstaller", spec, "--noconfirm", "--clean", "--log-level", "WARN",
               "--distpath", dist, "--workpath", work]
    with open(log, "w", encoding="utf-8") as out:
        done = subprocess.run(command, cwd=os.path.dirname(spec), env=env, stdout=out, stderr=subprocess.STDOUT,
                              stdin=subprocess.DEVNULL)
    if done.returncode != 0:
        with open(log, encoding="utf-8", errors="replace") as f:
            tail = f.read()[-3000:]
        raise BuildError(f"PyInstaller failed on {os.path.basename(spec)} (its output is in {log}):\n{tail}")


def pyz_modules(path: str) -> set[str]:
    """The Python modules packed in a PyInstaller PYZ archive."""
    from PyInstaller.archive.readers import ZlibArchiveReader
    return set(ZlibArchiveReader(path).toc)


def find_pyz(work: str, spec_name: str) -> str:
    folder = os.path.join(work, spec_name)
    found = [os.path.join(folder, n) for n in sorted(os.listdir(folder)) if n.endswith(".pyz")]
    if not found:
        raise BuildError(f"no .pyz archive in {folder}")
    return found[0]


def check_app_modules(modules: set[str], root: str = ROOT) -> list[str]:
    """What's wrong with the modules in the app's archive: any of the app's own missing, XlsxWriter missing, or
    anything that should have stayed out. -> problems ([] when it's right)."""
    problems = []
    missing = [m for m in package_modules(root) if m not in modules]
    if missing:
        problems.append("modules of the app missing: " + ", ".join(missing))
    if "xlsxwriter" not in modules:
        problems.append("XlsxWriter is missing")
    for name in ("PIL", "tests", "tools", "unittest"):
        stray = sorted(m for m in modules if m == name or m.startswith(name + "."))
        if stray:
            problems.append(f"{name} got in: {', '.join(stray[:5])}")
    return problems


def check_app_folder(folder: str) -> list[str]:
    problems = []
    for name in (f"{APP}.exe", "_internal") + EXTRA_FILES:
        if not os.path.exists(os.path.join(folder, name)):
            problems.append(f"{name} is missing")
    assets = os.path.join(folder, "_internal", PACKAGE, "assets")
    for name in os.listdir(ASSETS):
        if not os.path.isfile(os.path.join(assets, name)):
            problems.append(f"the icon file {name} is missing")
    for here, dirs, files in os.walk(folder):
        for d in dirs:
            if d in ("PIL", "tests", "tools", "tzdata") or d.lower().startswith("pillow"):
                problems.append(f"{os.path.relpath(os.path.join(here, d), folder)} got in")
        for name in files:
            if name.lower().startswith(("libssl", "libcrypto", "_ssl.", "_hashlib.")):
                problems.append(f"{os.path.relpath(os.path.join(here, name), folder)} got in")
    return problems


def exe_details(path: str) -> dict:
    """An .exe's version details and the administrator rights its manifest asks for - read with pefile (which
    PyInstaller brings with it)."""
    import pefile
    pe = pefile.PE(path)
    try:
        strings = {}
        for info in getattr(pe, "FileInfo", None) or []:
            for entry in info:
                for table in getattr(entry, "StringTable", None) or []:
                    strings.update({k.decode("utf-8", "replace"): v.decode("utf-8", "replace")
                                    for k, v in table.entries.items()})
        level = None
        resources = pe.DIRECTORY_ENTRY_RESOURCE.entries if hasattr(pe, "DIRECTORY_ENTRY_RESOURCE") else []
        for kind in resources:
            if kind.id != pefile.RESOURCE_TYPE["RT_MANIFEST"]:
                continue
            for entry in kind.directory.entries:
                for lang in entry.directory.entries:
                    data = pe.get_data(lang.data.struct.OffsetToData, lang.data.struct.Size)
                    found = re.search(rb'requestedExecutionLevel\s+level="([^"]+)"', data)
                    if found:
                        level = found.group(1).decode()
        return {"strings": strings, "level": level}
    finally:
        pe.close()


def check_exe(path: str, version: str, description: str) -> list[str]:
    details = exe_details(path)
    strings, problems = details["strings"], []
    want = {"ProductName": APP, "ProductVersion": version, "FileVersion": version, "FileDescription": description,
            "CompanyName": PUBLISHER}
    for key, value in want.items():
        if strings.get(key) != value:
            problems.append(f"{os.path.basename(path)}: {key} is {strings.get(key)!r}, not {value!r}")
    if details["level"] != "asInvoker":
        problems.append(f"{os.path.basename(path)} asks for {details['level']!r} rights, not asInvoker (none)")
    return problems


# ---------------------------------------------------------------------------------------------------------
# The payload: the app's folder and Uninstall.exe, for Setup.exe
# ---------------------------------------------------------------------------------------------------------
def make_payload(app_folder: str, uninstaller: str, out: str, version: str, mtime: int | None = None) -> dict:
    """A zip of app_folder's files and Uninstall.exe - forward slashes, in name order, every file dated `mtime` -
    with what it is as JSON in its comment (installer.Payload reads it). -> that JSON."""
    entries = []
    for here, dirs, files in os.walk(app_folder):
        dirs.sort()
        for name in sorted(files):
            path = os.path.join(here, name)
            entries.append((os.path.relpath(path, app_folder).replace(os.sep, "/"), path))
    entries.append((installer.UNINSTALLER, uninstaller))
    entries.sort(key=lambda e: e[0].split("/"))
    names = [n for n, _p in entries]
    if len(set(names)) != len(names) or not all(installer.safe_name(n) for n in names):
        raise BuildError("the app's folder has names that can't go in the payload")
    stamp = time.gmtime(max(mtime or 0, 315532800))[:6]                   # (zip dates start in 1980)
    info = {"name": APP, "version": version, "exe": installer.EXE, "uninstaller": installer.UNINSTALLER,
            "files": len(entries), "size": sum(os.path.getsize(p) for _n, p in entries)}
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for name, path in entries:
            item = zipfile.ZipInfo(name, stamp)
            item.compress_type = zipfile.ZIP_DEFLATED
            item.external_attr = 0o644 << 16
            with open(path, "rb") as f:
                z.writestr(item, f.read())
        z.comment = json.dumps(info, sort_keys=True).encode("utf-8")
    return info


# ---------------------------------------------------------------------------------------------------------
# Checking the built programs (nothing on screen)
# ---------------------------------------------------------------------------------------------------------
def check_app(exe: str, version: str, work: str, database: str | None = None) -> tuple[list[str], str]:
    """Projectionist.exe --version, and --self-test (with `database`). -> (problems, the self-test's report)."""
    problems = []
    done = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
    if done.returncode != 0 or done.stdout.strip() != f"{APP} {version}":
        problems.append(f"--version gave {done.stdout.strip()!r} (exit {done.returncode})")
    report = os.path.join(work, "self-test.txt")
    command = [exe, "--self-test", report] + (["--db", database] if database else [])
    done = subprocess.run(command, capture_output=True, text=True, timeout=1200, stdin=subprocess.DEVNULL)
    try:
        with open(report, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        text = ""
    if done.returncode != 0 or not text.rstrip().endswith("PASS"):
        problems.append(f"the self-test failed (exit {done.returncode}):\n{text or done.stderr}")
    return problems, text


def check_setup(setup: str, version: str, work: str) -> tuple[list[str], list[str]]:
    """Setup.exe --silent into `work` - the app's folder, the Start menu, the desktop, the registry entry (as a JSON
    file) and the temporary files all there - and the installed Uninstall.exe --silent after it.
    -> (problems, what was seen)."""
    trial = os.path.join(work, "setup-trial")
    shutil.rmtree(trial, ignore_errors=True)
    place = {k: os.path.join(trial, k) for k in ("app", "start-menu", "desktop", "temp")}
    for folder in place.values():
        os.makedirs(folder, exist_ok=True)
    registry_file, log = os.path.join(trial, "registry.json"), os.path.join(trial, "setup.log")
    env = dict(os.environ, TEMP=place["temp"], TMP=place["temp"])            # (its unpacking too)
    # every place given - never the real Start menu, desktop or registry
    common = ["--silent", "--start-menu-dir", place["start-menu"], "--desktop-dir", place["desktop"],
              "--registry-file", registry_file, "--temp-dir", place["temp"], "--log", log]
    problems, seen = [], []
    done = subprocess.run([setup, "--version"], capture_output=True, text=True, timeout=300, env=env,
                          stdin=subprocess.DEVNULL)
    if done.stdout.strip() != f"{APP} {version} Setup":
        problems.append(f"Setup.exe --version gave {done.stdout.strip()!r}")
    done = subprocess.run([setup, "--install-dir", place["app"], "--desktop"] + common, timeout=600, env=env,
                          capture_output=True, text=True, stdin=subprocess.DEVNULL)
    installed = installer.read_manifest(place["app"])
    if done.returncode != 0 or installed is None:
        problems.append(f"Setup.exe --silent failed (exit {done.returncode}): {_read(log)}")
        return problems, seen
    seen.append(f"installed {len(installed['files'])} files, {installer.EXE} "
                f"{os.path.getsize(os.path.join(place['app'], installer.EXE)):,} bytes")
    registry = installer.FileRegistry(registry_file)
    entry = registry.read(installer.UNINSTALL_KEY) or {}
    want = installer.uninstall_entry(place["app"], version, installed["size"], entry.get("InstallDate"))
    if entry != want:
        problems.append(f"the Installed-apps entry is {entry}, not {want}")
    kinds = registry.kinds(installer.UNINSTALL_KEY)
    if not all(kinds.get(k) == "REG_DWORD" for k in ("EstimatedSize", "NoModify", "NoRepair")):
        problems.append(f"the entry's numbers aren't all REG_DWORD: {kinds}")
    seen.append("the Installed-apps entry: " + ", ".join(f"{k}={v!r}" for k, v in sorted(entry.items())))
    links = installer.Shortcuts()
    exe = os.path.join(place["app"], installer.EXE)
    for folder in ("start-menu", "desktop"):
        link = os.path.join(place[folder], installer.SHORTCUT)
        if not os.path.isfile(link):
            problems.append(f"no shortcut in {folder}")
            continue
        found = links.read(link)
        if not installer.same_path(found["target"], exe) or found["app_id"] != installer.APP_ID:
            problems.append(f"the {folder} shortcut is {found}")
        seen.append(f"{folder} shortcut -> {found['target']} (app id {found['app_id']})")
    uninstaller = os.path.join(place["app"], installer.UNINSTALLER)
    done = subprocess.run([uninstaller] + common, timeout=300, env=env, capture_output=True, text=True,
                          stdin=subprocess.DEVNULL)
    if done.returncode != 0:
        problems.append(f"Uninstall.exe --silent failed (exit {done.returncode}): {_read(log)}")
    left = [os.path.join(h, n) for h, _d, names in os.walk(trial) for n in names]
    left = [p for p in left if not p.startswith(place["temp"]) and p not in (registry_file, log)]
    if os.path.exists(place["app"]) or left:
        problems.append(f"left after uninstalling: {left or place['app']}")
    if registry.read(installer.UNINSTALL_KEY) is not None:
        problems.append("the Installed-apps entry is still there after uninstalling")
    deadline = time.time() + 90
    while time.time() < deadline and any(n.endswith(".exe") for n in os.listdir(place["temp"])):
        time.sleep(0.5)                               # (the uninstaller deletes itself once it has ended)
    moved = [n for n in os.listdir(place["temp"]) if n.endswith(".exe")]
    if moved:
        problems.append(f"the uninstaller didn't delete itself: {moved}")
    seen.append("uninstalled: the folder, both shortcuts and the entry gone; the uninstaller deleted itself")
    return problems, seen


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def folder_size(folder: str) -> tuple[int, int]:
    """(files, bytes) in a folder."""
    files = total = 0
    for here, _dirs, names in os.walk(folder):
        for name in names:
            files += 1
            total += os.path.getsize(os.path.join(here, name))
    return files, total


# ---------------------------------------------------------------------------------------------------------
# The build
# ---------------------------------------------------------------------------------------------------------
def _replace(source: str, target: str):
    """Move a new build into place - over an older build of ours only."""
    if os.path.isdir(target):
        if not os.path.isfile(os.path.join(target, f"{APP}.exe")):
            raise BuildError(f"{target} is there already and isn't a build of {APP}: move it away first")
        shutil.rmtree(target)
    elif os.path.exists(target):
        os.remove(target)
    shutil.move(source, target)


def build(out: str | None = None, work: str | None = None, setup: bool = True, check: bool = True,
          database: str | None = None, say=print) -> dict:
    if sys.platform != "win32":
        raise BuildError("Windows programs are built on Windows.")
    if not pyinstaller_version():
        raise BuildError("PyInstaller isn't installed: py -m pip install pyinstaller")
    version = make_sdist.read_version(ROOT)
    out = os.path.abspath(out or os.path.join(ROOT, "dist"))
    work = os.path.abspath(work)
    stage = os.path.join(work, "dist")
    os.makedirs(stage, exist_ok=True)
    folder_name, setup_name = f"{APP}-{version}-win64", f"{APP}-{version}-Setup"
    result = {"version": version, "pyinstaller": pyinstaller_version(), "python": sys.version.split()[0]}

    def write(name, text):
        path = os.path.join(work, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def spec_file(name, text):
        return write(f"{name}.spec", text)

    def version_file(name, description, filename):
        return write(f"{name}-version.txt", version_info(version, description, filename))

    when = result["date"] = build_date()
    say(f"Building {APP} {version} with PyInstaller {result['pyinstaller']} (Python {result['python']})")
    say(f"  working folder: {work}")
    started = time.perf_counter()
    app_version = version_file("app", APP, f"{APP}.exe")
    run_pyinstaller(spec_file("app", app_spec(version, app_version)), stage, os.path.join(work, "build"),
                    os.path.join(work, "app.log"), when)
    app_folder = os.path.join(stage, folder_name)
    for name in EXTRA_FILES:
        if os.path.isfile(os.path.join(ROOT, name)):
            shutil.copyfile(os.path.join(ROOT, name), os.path.join(app_folder, name))
    problems = check_app_modules(pyz_modules(find_pyz(os.path.join(work, "build"), "app"))) + \
        check_app_folder(app_folder) + check_exe(os.path.join(app_folder, f"{APP}.exe"), version, APP)
    if problems:
        raise BuildError("the app's build isn't right:\n  " + "\n  ".join(problems))
    say(f"  the app: {time.perf_counter() - started:.0f} s")

    if setup:
        started = time.perf_counter()
        uninstall_version = version_file("uninstall", f"{APP} Uninstaller", installer.UNINSTALLER)
        run_pyinstaller(spec_file("uninstall", onefile_spec("Uninstall", uninstall_version, UNINSTALLER_EXCLUDES,
                                                            [])),
                        stage, os.path.join(work, "build"), os.path.join(work, "uninstall.log"), when)
        uninstaller = os.path.join(stage, installer.UNINSTALLER)
        payload = os.path.join(work, installer.PAYLOAD)
        result["payload"] = make_payload(app_folder, uninstaller, payload, version, when)
        setup_version = version_file("setup", f"{APP} Setup", f"{setup_name}.exe")
        datas = [(payload, "."), (ASSETS, "projectionist/assets")]
        run_pyinstaller(spec_file("setup", onefile_spec(setup_name, setup_version, INSTALLER_EXCLUDES, datas)),
                        stage, os.path.join(work, "build"), os.path.join(work, "setup.log"), when)
        setup_exe = os.path.join(stage, f"{setup_name}.exe")
        problems = check_exe(uninstaller, version, f"{APP} Uninstaller") + \
            check_exe(setup_exe, version, f"{APP} Setup")
        if problems:
            raise BuildError("Setup isn't right:\n  " + "\n  ".join(problems))
        say(f"  Uninstall.exe and Setup.exe: {time.perf_counter() - started:.0f} s")

    if check:
        started = time.perf_counter()
        problems, report = check_app(os.path.join(app_folder, f"{APP}.exe"), version, work, database)
        result["self_test"] = report
        if problems:
            raise BuildError("the built app failed its checks:\n  " + "\n  ".join(problems))
        say(f"  Projectionist.exe --version and --self-test: passed ({time.perf_counter() - started:.0f} s)")
        if setup:
            started = time.perf_counter()
            problems, seen = check_setup(setup_exe, version, work)
            result["setup_check"] = seen
            if problems:
                raise BuildError("Setup failed its check:\n  " + "\n  ".join(problems))
            say(f"  Setup.exe --silent, then Uninstall.exe --silent (all in the working folder): passed "
                f"({time.perf_counter() - started:.0f} s)")

    os.makedirs(out, exist_ok=True)
    target = os.path.join(out, folder_name)
    _replace(app_folder, target)
    files, size = folder_size(target)
    exe = os.path.join(target, f"{APP}.exe")
    result["folder"] = {"path": target, "files": files, "bytes": size, "exe": exe, "exe_bytes": os.path.getsize(exe),
                        "exe_sha256": sha256(exe)}
    if setup:
        target = os.path.join(out, f"{setup_name}.exe")
        _replace(setup_exe, target)
        result["setup"] = {"path": target, "bytes": os.path.getsize(target), "sha256": sha256(target)}
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=f"Build {APP} for Windows: its folder and Setup.exe (PyInstaller).")
    parser.add_argument("--out", help="the folder to put them in (default: dist in the project folder)")
    parser.add_argument("--work", help="PyInstaller's working folder (default: a temporary one)")
    parser.add_argument("--keep-work", action="store_true", help="don't delete the working folder afterwards")
    parser.add_argument("--no-setup", action="store_true", help="only the app's folder, no Setup.exe")
    parser.add_argument("--no-check", action="store_true", help="don't run the built programs to check them")
    parser.add_argument("--check-db", metavar="DATABASE", help="a Plex database for the app's self-test")
    args = parser.parse_args(argv)
    work = os.path.abspath(args.work) if args.work else tempfile.mkdtemp(prefix=f"{APP}-build-")
    if args.work and os.path.isdir(work) and os.listdir(work) and not os.path.isfile(os.path.join(work, "app.spec")):
        print(f"error: {work} has other things in it - choose an empty or new folder for --work", file=sys.stderr)
        return 2
    try:
        result = build(args.out, work, setup=not args.no_setup, check=not args.no_check, database=args.check_db)
    except BuildError as exc:
        print(f"error: {exc}", file=sys.stderr)
        print(f"(the working files are in {work})", file=sys.stderr)
        return 1
    if not args.keep_work:
        shutil.rmtree(work, ignore_errors=True)
    folder = result["folder"]
    when = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(result["date"]))
    print(f"Wrote {folder['path']}  (dated {when}, the newest source file's - or SOURCE_DATE_EPOCH)")
    print(f"  {folder['files']} files, {folder['bytes']:,} bytes")
    print(f"  {APP}.exe {folder['exe_bytes']:,} bytes, SHA-256 {folder['exe_sha256']}")
    if "setup" in result:
        print(f"Wrote {result['setup']['path']}")
        print(f"  {result['setup']['bytes']:,} bytes, SHA-256 {result['setup']['sha256']}")
    print("Neither is code-signed: Windows SmartScreen may warn before the first run (More info > Run anyway).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
