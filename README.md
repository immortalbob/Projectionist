# Projectionist

Projectionist turns a Plex Media Server library database into one `.xlsx` spreadsheet of every movie:
titles, dates, summaries, cast and characters, directors, writers, producers, studios,
ratings, IMDb/TMDb IDs, file and codec details, watch history and more. The file works
in both LibreOffice Calc and Excel. The app's other tabs explore the same collection.

Projectionist was made to test out the viability of Claude Opus 5.5 in a semi-complicated project over the course of 3 days.

## Using it

1. Copy a fresh database dump into this folder. Plex's automatic backups are named like
   `com.plexapp.plugins.library.db-2026-09-25`. The `.zip` from Plex Web's
   *Settings > Troubleshooting > Download database* works too; no need to unzip it.
2. Double-click **`Projectionist.pyw`**. (On Linux, see [On Linux](#on-linux) below. On Windows you can install
   it instead, with no Python needed - the installer isn't code-signed: see [Windows installer](#windows-installer).)
3. The newest dump in the folder is picked automatically. Tick the libraries and extra sheets
   you want and click **Export spreadsheet**.

Each time it starts, the app opens the newest Plex backup in the folder chosen in *Settings > Starting up >
Look for Plex backups in* (none chosen: the last database's folder, then this one) when it's newer than the
one you used last, and the status bar says so ("Opened the newest backup, 2026-10-02 - you last used
2026-09-25."). *When the app opens, use: The backup I used last* keeps the one you had instead. The
`...blobs.db`, SQLite's `-wal`/`-shm` files and copies still being copied or downloaded are never picked.

The spreadsheet is saved next to the database as `Projectionist Movies <date>.xlsx`. When it's done it
opens in the program chosen next to *Open the spreadsheet when finished*. LibreOffice Calc is
chosen when it's installed; you can also pick Excel or Windows' default app for `.xlsx` files (on Linux:
LibreOffice, Gnumeric, or the desktop's default app).
The app remembers your choices for next time.

The database is only ever read: never modified or locked. Only `com.plexapp.plugins.library.db`
is needed. The `...blobs.db` file holds thumbnail data, not movie information.

## The other tabs

Once a database is picked, the app reads the collection in the background (a few seconds). The
other tabs then explore it; each chart has tooltips, and clicking a bar, person or film usually takes
you to the matching detail on another tab. A film, wherever you click it, opens its page on the Film tab.

The **Find** box above the tabs (Ctrl+F or Ctrl+K from anywhere) searches films, people, collections,
genres, countries, studios, libraries and critics as you type. Accents don't matter and small typos are
forgiven ("godfahter", "kurosowa"); a year narrows it ("alien 1979") or lists that year. Enter or a
click opens the match where it's most useful: a film on the Film tab, a person in Six Degrees, a
genre, country, collection or library in Watch Next, a studio's films on the Film tab, a critic in
Watch Next's *Your critics*.

| Tab | What it shows |
|---|---|
| **Overview** | The collection at a glance: films by decade, library, genre, country and resolution; the most-featured actors (all roles, or lead roles only) and directors; your ratings against IMDb; how much of each library you've played |
| **Film** | Everything about one film on one page: its details and other titles; your rating beside IMDb, TMDb and Rotten Tomatoes on one scale (or Watch Next's guess, if you haven't rated it); your plays of it; whether to stay after the credits; the cast and director; similar films, seen and not; what critics said; your copies' files; and anything the Library Doctor would fix. Back and Forward (Alt+Left / Alt+Right) step through the films you've opened, and with no film open it suggests some |
| **Watch Next** | Films you haven't played, ranked by the rating you'd probably give them, learned from your own star ratings, with the reasons behind each pick and a step-by-step breakdown. Also your taste profile (what you rate above or below its reputation), *Your critics* (below) and an honest accuracy test |
| **Viewing** | Your viewing habits from Plex's play history: when in the week you start films, month by month, streaks, the films you've played most, films you stopped partway, and forgotten favourites (films you rated highly but haven't played for a year). *Year in review* gives any year its own page: every day on a calendar, your top genres, decades, countries, stars and directors, and every film you watched |
| **Credits** | Which films have a scene during or after the end credits, when it starts, and a timeline of each film's ending |
| **Six Degrees** | The chain of your films linking any two people; anyone's circle of co-stars; the best-connected people on your shelf; who bridges two parts of the collection; and troupes of actors who keep appearing together |
| **Library Doctor** | A to-do list for the files behind your films, in twelve checks: likely duplicates, copies a better copy makes redundant, files far bigger or far smaller than usual, files Plex has marked unavailable, films you keep in several editions, films in another language with no subtitles you read, copies with no audio in your languages, untagged soundtracks, films you rate highly that you only have below 1080p, missing details (a film Plex hasn't matched, a file far shorter than the film, a rating left on an earlier edition), and where your disk space goes. Each list can be saved as an `.xlsx` |

A few things worth knowing:

- **Your critics** (in Watch Next) compares your ratings with the Fresh or Rotten verdicts of the
  critics whose reviews Plex keeps (up to 20 a film) and finds the ones most often in step with you -
  allowing for how often each says Fresh, so a critic who likes everything doesn't come top. It says
  plainly when the differences are no bigger than luck would make, which, on a few hundred ratings, is
  usual: then they're only the closest *so far*. *Your critics' picks* is a way to browse, not a better
  guess: in testing, adding the critics didn't make Watch Next's predictions more accurate, so they
  aren't part of its model (the *How good a guide are they?* test works this out afresh on your ratings).
- **Viewing** only knows what Plex logged: the play history starts when the server was set up, times
  are when Plex logged the play (near the end of a film, so start times are estimated by taking off
  its running time), films marked as played count on their day but not their hour, and a burst of
  films marked played together is left out of everything dated. Only the owner's account counts. The
  page says all this, with the numbers.
- **Library Doctor** reads the backup and changes nothing, in Plex or on your drives. Duplicates are
  judged from edition names and running times, so an unknown release label means a missed duplicate,
  never a false alarm. "Weaker" compares picture, HDR, sound channels and track languages, not encode
  quality. Language checks trust the tags in the files; tick the languages you read at the top. Sizes
  are decimal (1 GB = 1,000,000,000 bytes).

- **Films vs library items.** The tabs count a film once, however many libraries or editions it's in.
  The Export tab counts library items (the spreadsheet's rows), so its number is higher. The status bar
  shows both, e.g. "1,204 films (1,318 library items)".
- **Watch Next is honest about itself.** When your ratings mostly agree with the IMDb/Rotten Tomatoes
  scores, so do its predictions; your own ratings nudge the order rather than remake it. The *How
  accurate* view tests it against your ratings and says so plainly.
- **People who share a name** (hundreds do) are told apart by one of their films, e.g. "John Smith
  (Paper Harbour)", and every link between tabs goes by the exact person, not the name.
- **Directors.** Opening a director in Six Degrees counts the casts of the films they directed.
- **Keyboard.** Ctrl+Tab / Ctrl+Shift+Tab switch tabs from anywhere; Ctrl+F or Ctrl+K goes to the
  Find box; Alt+Left / Alt+Right go back and forward on the Film tab; Tab and Shift+Tab move between
  the links, buttons and tables of a page (the link with the focus is underlined) and Enter or Space
  opens a link; Page Up/Down, Home and End scroll a page once you've clicked in it or tabbed to a link
  on it; Enter opens the selected row of a table (a film opens its page).
- Heavy work (the accuracy test, *Most connected*, reading the play history, the files and the critics'
  reviews) runs in the background; starting a newer request or loading another database cancels the
  old one, and the window stays responsive meanwhile.

## What's in the workbook

| Sheet | Contents |
|---|---|
| **Movies** | One row per movie with 97 columns. This is the master table. |
| Cast | Every actor and character, in billing order |
| Crew | Directors, writers and producers with their exact credit |
| Files | One row per video file (handles multiple versions and CD1/CD2 splits) |
| Streams | Every video, audio and subtitle track |
| Chapters | Chapter names and start/end times |
| Credits Scenes | Scenes during or after the end credits, and when they start |
| Ratings | IMDb, Rotten Tomatoes, TMDb... |
| Reviews | Critic review excerpts |
| Collections | Which movies are in which collections, regular and smart |
| Collection List | Every collection, with each smart collection's filter in words |
| Extras | Trailers, featurettes, deleted scenes... |
| Watch Status | Per-user play count, last played, resume point, rating |
| Watch History | Every play Plex logged |
| About | Export details and a dictionary explaining every column |

Plex keeps no member list for **smart collections**, only a saved filter. Projectionist re-runs
each filter and includes the result only when it matches the member count Plex itself recorded.
The Collection List sheet shows any filter it couldn't reproduce, and why.

**Stay after the credits?** Plex's credits detection marks each stretch of end credits it finds, and
flags the last one. Any footage between or after those stretches is a mid- or post-credits scene (or
outtakes). The Movies sheet's *Stay After Credits?* column says Yes, Maybe or None found, and the
Credits Scenes sheet lists each scene with its start time. A few limits apply:

- The detector sometimes fires on on-screen text inside the film. Those cases, very short gaps, long
  stretches and pre-1977 films are marked *Maybe*.
- A gag in the last seconds, or outtakes shown beside the credits, can hide inside Plex's final marker.
- Movies Plex hasn't scanned for credits are left blank.

**Your ratings.** Plex keeps a rating on the ID a copy had when you rated it. When Plex later gives
that copy an edition name (or renames it), the rating stays behind on the old ID and Plex shows the
copy as unrated. The Movies sheet still gives such a film your rating, the same way the tabs do: only
when no copy of the film has a rating of its own, and marked *An earlier edition* in the *Owner Rating
Source* column. The Watch Status sheet and smart collections stay as Plex sees them. The Library
Doctor lists these films under *Missing details*: rate them again in Plex to carry the rating over.

Every sheet except Collection List has a **Plex ID** column, so it can be joined back to Movies.
Collection List has one row per collection; match it to Collections by collection name and library.
Cells with several values (genres, cast...) separate them with `; `.
If a sheet ever grows past Excel's limit of 1,048,576 rows, it continues on a second sheet
("Cast (2)" and so on), so nothing is dropped.
Tick **Also save every sheet as a CSV file** for easy loading into code or a database.

## Letterboxd

**Export for Letterboxd...** on the Export tab saves your plays and ratings as a CSV file in the format
Letterboxd's importer reads ([letterboxd.com/about/importing-data](https://letterboxd.com/about/importing-data/)).
The line beside the button then says what went in, e.g. "212 diary entries, 64 rated films without a date,
9 played films without a date, 3 films with no IMDb ID matched by title".

- Each play Plex logged becomes a **diary entry** on the day it started (a film that ran past midnight goes on
  the evening it began). A film you'd seen before - an earlier play, or plays Plex counted before its history
  began - is marked as a **rewatch**. Two plays of a film on one day are one entry.
- Films Plex counts as **played with no date** (from before its history, or ticked off in bulk) and films you
  **rated** without playing them are marked as watched, with no diary entry.
- Your **rating** goes on a film's latest diary entry, or on its dateless line. Plex's 1-10 is Letterboxd's
  half stars exactly (8 is four stars). A film whose copies you rated differently gets their average, rounded
  half up.
- Films are matched by **IMDb ID** - or by TMDB ID for a film Plex has no IMDb ID for. The title, year and
  directors go in too, for the few films Plex has neither for.
- Only **your own account** counts. *Also count as seen* is about what the app suggests you; a diary is yours.
- Every library counts, whatever the spreadsheet leaves out, unless you choose to leave the unticked ones out.
- **Only what's new**: after the first file, the next one has only the plays, ratings and films marked as played
  since the last one. When the settings put in more than the last file did (ratings or library tags turned on,
  a left-out library back in), the next file has everything again, so nothing the last one left out is missed.
  Letterboxd combines a film's entries on the same day, so saving everything again doubles nothing.
- *Settings > Letterboxd* has the choices: diary entries, films with no date, ratings, a tag with each film's Plex
  library (Letterboxd keeps tags only on diary entries), only what's new, and leaving out the unticked libraries.
- Letterboxd takes files of up to 1 MB. A bigger export is split into `name (2).csv` and so on, each with the
  header row (a thousand lines come to about 65 KB).

The file opens afterwards only when *Open the spreadsheet when finished* is ticked.

## Command line

```
python -m projectionist                                  # open the window
python -m projectionist <database-or-folder>             # export straight away
python -m projectionist <db> -o out.xlsx --csv           # choose output, add CSVs
python -m projectionist <db> --library Movies --sheets cast,crew
python -m projectionist <db> --list-libraries
```

## Asking questions

`projectionist.ask` answers questions about the collection: send a JSON request, get a JSON answer. It
doesn't care what front end asks (a web page, a script, a spreadsheet macro, a chat assistant).

```
python -m projectionist.ask '{"action": "help"}'
python -m projectionist.ask '{"action": "connect", "from": "Kevin Bacon", "to": "Tom Hanks"}'
python -m projectionist.ask '{"action": "recommend", "sort": "personal", "library": "Documentaries", "count": 10}'
python -m projectionist.ask '{"action": "search", "q": "kurosawa"}'
python -m projectionist.ask '{"action": "film", "title": "Alien (1979)", "parts": "core"}'
python -m projectionist.ask '{"action": "habits", "year": 2025, "parts": ["year"]}'
python -m projectionist.ask requests.json          # one request, or a list of them
python -m projectionist.ask --serve 8765           # POST requests to http://127.0.0.1:8765/
```

From Python, `from projectionist.ask import handle` and call `handle({"action": ...}, db_path)`, which
returns a dict. The database defaults to the newest dump in this folder; `--db` picks another.

| Action | What it answers |
|---|---|
| `help` | Every action, with the options each one takes |
| `info` | What's loaded: the database, the owner, the libraries, and how many films, people and ratings |
| `overview` | The Overview tab's figures: films by decade, library, genre, country and resolution; the most-featured actors and directors (`count` of each); your ratings against IMDb |
| `film` | Everything the Film tab shows about one film (`title` or `film_key`): details, credits scenes, ratings, people, similar films, critics' verdicts, your plays, files and library-health issues. `parts: "core"` for the quick ones only, or a list of the parts you want |
| `search` | Films, people, collections, genres, countries, studios, libraries and critics matching `q` (accents aside, typos forgiven), in groups. `count` per group, `groups` to limit it |
| `credits` | Which films have scenes during or after the credits, and when |
| `recommend` | What to watch next, from your own star ratings. Filters: library, genre, decade, runtime, people, `like` a title, `text`. `sort: "personal"` ranks films your taste rates above their reputation |
| `evaluate` | How well the recommender predicts your ratings (cross-validated, against baselines) |
| `taste` | Your taste profile, as in Watch Next: the genres, decades, studios, countries, libraries, collections, directors and actors you rate above or below their IMDb/Rotten Tomatoes scores. `kinds` picks which, `count` how many each way, `min_films` how many films of yours each needs |
| `connect` | The chain of your films linking two people (`avoid` people, limit to top billing) |
| `person` | Someone's films on your shelf, frequent co-stars, and how far their links reach |
| `center` | The best-connected people in your collection |
| `bridges` | People linking two parts of the collection (two libraries, countries, genres or decades) |
| `troupes` | Groups of actors who keep appearing together |
| `critics` | The critics whose Fresh/Rotten verdicts agree with your ratings (`view: "overview"`), one critic's record with you (`critic`: an ID or a name), what critics said about a film (`film_key` or `title`), your closest critics' picks (`view: "picks"`, with Watch Next's filters), and a test of them as a guide (`view: "evaluate"`) |
| `habits` | Your viewing habits: when you watch, month by month, streaks, most played, films stopped partway, forgotten favourites, and a year in review (`year`: a year or `"latest"`). `parts` picks the pieces |
| `doctor` | Library health: the twelve checks of the Library Doctor tab with their lists (`issue` for one list), in `languages` you read; `film_key` for one film's files and issues |
| `letterboxd` | Your plays and ratings as Letterboxd's import file: its lines (diary entries and dateless films) and a summary; `csv: true` adds the file's text, split at Letterboxd's 1 MB. The choices of *Settings > Letterboxd* (`diary`, `undated`, `ratings`, `library_tags`, `leave_out_libraries`), and `since` for only what's new (`covered_to` is the next one). Nothing is written to disk |

Names and titles are matched loosely ("de niro", "star wars 4"). Each answer says how a name
was matched (`exact`, `partial` or `guess`), so a front end can check with the user before trusting a
guess.

## On Linux

Projectionist runs on Linux from the same source tarball: unpack it anywhere in your home folder.

**What it needs:** Python 3.10 or newer with tkinter, and XlsxWriter. Your distribution has both:

| Distribution | Install |
|---|---|
| Debian, Ubuntu, Mint, Pop!_OS | `sudo apt install python3-tk python3-xlsxwriter` |
| Fedora | `sudo dnf install python3-tkinter python3-xlsxwriter` |
| Arch, Manjaro | `sudo pacman -S tk python-xlsxwriter` |
| openSUSE | `sudo zypper install python3-tk python3-XlsxWriter` |

(Or XlsxWriter with pip, in a virtual environment: `python3 -m venv ~/.venvs/projectionist` then
`~/.venvs/projectionist/bin/pip install XlsxWriter`, and start the app with that Python. tkinter still comes from
the distribution, and on Debian and Ubuntu so does venv: `sudo apt install python3-venv`.) Pillow is optional: only a
few tests and `tools/make_icon.py` use it.

Titles in Chinese, Japanese or Korean (a film's other titles, some names) need a font that has those letters.
Desktop installs usually come with one; if they show as empty boxes, install your distribution's Noto CJK fonts
(Debian and Ubuntu: `sudo apt install fonts-noto-cjk`).

**Starting it:** `python3 Projectionist.pyw` in the unpacked folder (or `python3 -m projectionist`). To put it in
your applications menu, with its icon: `python3 linux/install.py` - for you only, nothing system-wide.
`python3 linux/install.py --remove` takes it off again; run the install again if you move the folder.

**Your Plex database.** On a Linux Plex server installed from Plex's own package, the database and Plex's
automatic backups are in

```
/var/lib/plexmediaserver/Library/Application Support/Plex Media Server/Plug-in Support/Databases/
```

(the snap keeps them in `/var/snap/plexmediaserver/common/Library/Application Support/Plex Media Server/Plug-in
Support/Databases/`; in Docker, the same `Library/...` path is under the folder given to the container as
`/config`).

Use one of the **dated backups** there - `com.plexapp.plugins.library.db-2026-09-25` and so on, which Plex makes
every few days (*Settings > Scheduled Tasks > Backup database every three days*) - rather than
`com.plexapp.plugins.library.db` itself. Plex writes to that one all the time, so it can be read halfway through a
change; a backup is a complete copy that never changes. The folder belongs to the `plex` user, so copy a backup
somewhere of your own first:

```
mkdir -p ~/plex-backups
sudo cp "/var/lib/plexmediaserver/Library/Application Support/Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db-2026-09-25" ~/plex-backups/
sudo chown "$USER": ~/plex-backups/com.plexapp.plugins.library.db-2026-09-25
```

Then pick it with *Browse...*, or choose `~/plex-backups` in *Settings > Starting up > Look for Plex backups in*
and the newest backup there opens each time the app starts. The zip from Plex Web's *Download database* works as
well, and so does a backup copied across from a Plex server on another computer.

**What's different from Windows:**

- The settings are kept in `~/.config/projectionist/settings.json` (or under `$XDG_CONFIG_HOME`). (Windows:
  `%APPDATA%\Projectionist`; a Mac: `~/Library/Application Support/Projectionist`.)
- *Follow Windows* is *Follow the system*: Light while your desktop is set to light, your dark look while it's set
  to dark. It's read (never changed) from the desktop's own setting - through xdg-desktop-portal (GNOME, KDE and
  others) or GNOME's gsettings. A desktop that doesn't say isn't offered it.
- The looks use the fonts you have nearest to Windows' Segoe UI and Consolas (Noto Sans, Cantarell, Ubuntu,
  DejaVu Sans...). Light is drawn to look like Windows' own buttons, boxes and tabs.
- Spreadsheets open in LibreOffice (a distribution package, the snap or the Flatpak), Gnumeric, or whatever
  `xdg-open` opens `.xlsx` files with.

## Windows installer

`Projectionist-<version>-Setup.exe` installs Projectionist on Windows. It needs no Python: everything the app uses
is inside it.

> **The Windows programs are not code-signed.** `Setup.exe`, `Projectionist.exe` and the portable `.zip` carry no
> publisher signature, so Windows treats them as coming from an unknown publisher:
>
> - **SmartScreen** may say "Windows protected your PC" the first time. Click *More info*, then *Run anyway*.
> - **Smart App Control** (a Windows 11 setting, on for some PCs) may block them outright, with no way to run them
>   anyway. If that happens, run Projectionist from source instead: install [Python](https://www.python.org/)
>   3.10 or newer and XlsxWriter (`py -m pip install XlsxWriter`), then double-click `Projectionist.pyw` from the
>   source download. Python itself is signed, so Smart App Control lets it run.
>
> If you'd rather not run unsigned programs, the source route above works everywhere.

- **For you only.** Nothing is installed system-wide, and it doesn't ask for administrator rights. The app goes in
  `%LOCALAPPDATA%\Programs\Projectionist` (you can choose another folder), with a shortcut in the Start menu and,
  if you tick it, one on the desktop.
- **Uninstalling.** Setup adds Projectionist to *Settings > Apps > Installed apps*: choose it there, then
  *Uninstall* (or run `Uninstall.exe` in the app's folder). That removes the app's files, its shortcuts and its
  entry in Installed apps. Your settings in `%APPDATA%\Projectionist` are kept, for if you install it again.
- **Updating.** Run a newer Setup. It replaces the older version where it is and keeps your settings. Close
  Projectionist first.
- **Unsigned.** See the note above: SmartScreen may warn the first time, and Smart App Control may block it.
- **Without installing.** `Projectionist-<version>-win64.zip` is the same app as a plain folder. Unzip it first
  (it won't run from inside the zip), put the folder anywhere and run `Projectionist.exe`. The newest Plex backup
  next to it opens when it starts (or choose a folder in *Settings > Starting up*).
- **Unattended.** `Setup.exe --silent` installs with the defaults (`--desktop` adds the desktop shortcut,
  `--install-dir <folder>` chooses the folder). `Uninstall.exe --silent` removes it without asking.

`Projectionist.exe --version` prints the version. `Projectionist.exe --self-test report.txt --db <backup>` checks
the app without showing anything: it builds the window hidden, opens the backup, visits every tab and exports the
Movies sheet to a temporary folder, then writes what it found to `report.txt` (last line PASS or FAIL). Your own
settings aren't read or changed.

**Building it.** On Windows, with Python 3.10 or newer, XlsxWriter and PyInstaller (`py -m pip install
pyinstaller`):

```
python tools/build_exe.py                       # dist/Projectionist-<version>-win64 and dist/Projectionist-<version>-Setup.exe
python tools/build_exe.py --check-db <backup>   # ...and check the built app against a Plex backup too
```

It builds in a temporary folder and writes nothing into the project except the two results in `dist`. Pillow, the
tests and the tools stay out. Then it checks what it built, without showing anything: the app's version and its
self-test, and Setup run silently with every place pointed into the temporary folder (never your real Start menu,
desktop or registry), then the uninstaller. Last, it prints each file's size and SHA-256. The same source (with
the same Python and PyInstaller) always gives the same bytes. For the portable download, zip the
`dist/Projectionist-<version>-win64` folder (the folder itself at the top of the zip) as
`Projectionist-<version>-win64.zip`. On a PC with Smart App Control on, the check step can be blocked from running
the freshly built, unsigned `Projectionist.exe`.

## Requirements

Python 3.10+ with the XlsxWriter package (`py -m pip install XlsxWriter`). The window uses
tkinter, which comes with Python on Windows (on Linux it's a package of its own: see [On Linux](#on-linux)).
The Windows installer needs neither: see [Windows installer](#windows-installer).

The window has its own icon, a film projector (`projectionist/assets`), and its own taskbar button on
Windows rather than Python's. `python tools/make_icon.py --preview sheet.png` redraws the icon files; only that
tool needs Pillow.

## Tests

```
python -m unittest discover -s tests -v
```

The tests build small, made-up databases of their own, so they need no Plex backup. Timed steps allow for a
slower (or busier) computer than the one the limits were set on; sizes chosen for Windows' Segoe UI grow for a
wider font.

Tests that check one person's own Plex backup - its numbers, names and films - belong in `tests/local/`. They
run with the rest on that computer (`PROJECTIONIST_TEST_DB` points them at a backup elsewhere: the file, or its
folder) and skip when the backup isn't there. The folder is never put in the source tarball.

On Linux, run them on a virtual display, so no window reaches your screen:
`xvfb-run -a python3 -m unittest discover -s tests` (Debian and Ubuntu: `sudo apt install xvfb`). The few tests
that need a window on screen run only there; the Windows-only ones skip.

## Source tarball

`python tools/make_sdist.py` builds `dist/projectionist-<version>-src.tar.gz`: the `projectionist` package
(with its icon files), `tests`, `tools`, `linux` (the applications-menu entry and its installer),
`Projectionist.pyw`, this README, the `LICENSE` and a `requirements.txt` (XlsxWriter;
Pillow is optional), in one `projectionist-<version>` folder. Only source goes in, never the Plex backups,
spreadsheets, CSV files, `__pycache__` or `tests/local`. The same source always gives the same bytes; `--mtime`
sets the date every file gets (by default the newest source file's), and `--list` lists what went in.

## Licence

MIT - see `LICENSE`.
