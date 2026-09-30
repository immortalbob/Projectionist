"""Tests for the viewing-habits backbone (projectionist/habits.py).

Each test builds the fixture database from test_projectionist and adds the owner's plays to it. Times are written as
local times (datetime(...).timestamp()) and the expected hours and weekdays are worked out with
extract.local_datetime, so the tests pass in any time zone. The fixture's file name carries the backup date
(2026-09-25), which is the history's 'as of' date.
"""

import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from projectionist import habits, jobs  # noqa: E402
from projectionist.ask import handle, to_json  # noqa: E402
from projectionist.catalog import load  # noqa: E402
from projectionist.extract import local_datetime  # noqa: E402
from test_projectionist import MATRIX_GUID, build_fixture, make_catalog  # noqa: E402

MATRIX = MATRIX_GUID                                   # The Matrix (1999), 8,175,712 ms
HK = "plex://movie/hk1/edition/Extended"               # Hong Kong Action (1985), 5,400,000 ms; owner rating 8
HK_KEY = "plex://movie/hk1"
LEGACY = "com.plexapp.agents.imdb://tt0062622?lang=en"  # 2001: A Space Odyssey (1968), no running time
LEGACY_KEY = "imdb:tt0062622"
OLD = "plex://movie/old"                               # in the 'Classics' library (id 2)
SF = "plex://movie/sf75"                               # Only Assistant (1975), no running time
GONE_TS = 1700000000                                   # the fixture's play of a film deleted since
HK_THEATRICAL = "plex://movie/hk1/edition/Theatrical"  # a second edition of Hong Kong Action (EDITION_SQL adds it)
EDITION_SQL = ("INSERT INTO metadata_items (id, library_section_id, metadata_type, title, guid, edition_title, year) "
               "VALUES (701, 1, 1, 'Hong Kong Action', ?, 'Theatrical', 1985)", (HK_THEATRICAL,))


def ts(y, mo, d, hh=20, mm=0, ss=0) -> int:
    """A local wall-clock time as Unix time."""
    return int(datetime(y, mo, d, hh, mm, ss).timestamp())


def make_db(folder, views=(), *, view_type=True, stats=None, settings=(), ratings=None, name="2026-09-25",
            keep_gone=True, sections_created=None, extra_cast=False, sql=()):
    """The fixture database plus the owner's plays. views: (guid, unix time, view_type) or (guid, time, type,
    account, title, originally_available_at). ratings: {guid: owner rating}. Returns its path."""
    path = os.path.join(folder, f"com.plexapp.plugins.library.db-{name}")
    build_fixture(path)
    con = sqlite3.connect(path)
    if view_type:
        con.execute("ALTER TABLE metadata_item_views ADD COLUMN view_type INTEGER DEFAULT 0")
    con.execute("ALTER TABLE metadata_item_settings ADD COLUMN updated_at INTEGER")
    if not keep_gone:
        con.execute("DELETE FROM metadata_item_views WHERE account_id = 1")
    if sections_created is not None:
        con.execute("ALTER TABLE library_sections ADD COLUMN created_at INTEGER")
        con.execute("UPDATE library_sections SET created_at = ?", (sections_created,))
    for v in views:
        guid, at, vt = v[:3]
        account = v[3] if len(v) > 3 else 1
        title = v[4] if len(v) > 4 else "Logged Title"
        released = v[5] if len(v) > 5 else None
        cols = ["account_id", "guid", "metadata_type", "library_section_id", "title", "originally_available_at",
                "viewed_at", "device_id"]
        values = [account, guid, 1, 1, title, released, at, 7]
        if view_type:
            cols.append("view_type")
            values.append(vt)
        con.execute(f"INSERT INTO metadata_item_views ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                    values)
    if stats is not None:
        con.execute("CREATE TABLE statistics_media (id INTEGER PRIMARY KEY, account_id INTEGER, device_id INTEGER, "
                    "timespan INTEGER, at INTEGER, metadata_type INTEGER, count INTEGER, duration INTEGER)")
        for account, timespan, at, mtype, seconds in stats:
            con.execute("INSERT INTO statistics_media (account_id, device_id, timespan, at, metadata_type, count, "
                        "duration) VALUES (?, 7, ?, ?, ?, 1, ?)", (account, timespan, at, mtype, seconds))
    for row in settings:                      # (account, guid, view_offset, view_count, last_viewed_at)
        con.execute("INSERT INTO metadata_item_settings (account_id, guid, view_offset, view_count, last_viewed_at) "
                    "VALUES (?, ?, ?, ?, ?)", row)
    for guid, rating in (ratings or {}).items():
        if con.execute("SELECT 1 FROM metadata_item_settings WHERE account_id = 1 AND guid = ?", (guid,)).fetchone():
            con.execute("UPDATE metadata_item_settings SET rating = ? WHERE account_id = 1 AND guid = ?",
                        (rating, guid))
        else:
            con.execute("INSERT INTO metadata_item_settings (account_id, guid, rating, view_count) VALUES (1, ?, ?, 0)",
                        (guid, rating))
    if extra_cast:                            # a fourth-billed actor in The Matrix: not a star
        tag = con.execute("INSERT INTO tags (tag, tag_type, key) VALUES ('Fourth Person', 6, 'p30')").lastrowid
        con.execute("INSERT INTO taggings (metadata_item_id, tag_id, \"index\", text) VALUES (1799, ?, 3, 'Extra')",
                    (tag,))
    for statement, params in sql:
        con.execute(statement, params)
    con.commit()
    con.close()
    return path


# The main story: every rule shows up once.
STORY = [
    (MATRIX, ts(2025, 3, 3, 21, 0), 0),          # A  Monday
    (MATRIX, ts(2025, 3, 3, 21, 1), 0),          # B  a minute later: the same play logged twice
    (HK, ts(2025, 3, 4, 20, 0), 0),              # C  Tuesday
    (LEGACY, ts(2025, 3, 5, 20, 0), 0),          # D  no running time: no start time
    (OLD, ts(2025, 4, 10, 12, 0, 0), 1),         # E  two different films 30 s apart: marked in bulk
    (SF, ts(2025, 4, 10, 12, 0, 30), 1),         # F
    (MATRIX, ts(2025, 4, 12, 20, 0), 1),         # G  marked as played
    (MATRIX, ts(2025, 4, 14, 20, 0), 1),         # H  marked again two days later: the same play
    (HK, ts(2025, 6, 1, 18, 0), 1),              # I  marked...
    (HK, ts(2025, 6, 1, 21, 0), 0),              # J  ...then played three hours later: one play, at 21:00
    (SF, ts(2026, 1, 5, 20, 0), 0),              # K
    (MATRIX, ts(2026, 1, 6, 20, 0), 0),          # L
    (LEGACY, ts(2026, 1, 7, 20, 0), 0),          # M
    ("plex://movie/hk1/edition/Director's Cut", ts(2026, 2, 10, 20, 0), 0),   # O  an edition renamed since
]
STORY_RATINGS = {MATRIX: 10.0, LEGACY: 10.0, OLD: 9.0, SF: 9.0}


class StoryTestCase(unittest.TestCase):
    """The fixture with STORY's plays (and the fixture's own: a deleted film in Nov 2023, a friend's Matrix)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = make_db(cls.tmp.name, STORY, ratings=STORY_RATINGS, extra_cast=True,
                         stats=[(1, 1, 1740787200, 1, 7200),             # March 2025 (a UTC month): 2 hours
                                (42, 1, 1740787200, 1, 99999),          # someone else's
                                (1, 3, 1740787200, 1, 88888),           # a day bucket (not months)
                                (1, 1, 1740787200, 4, 77777)],          # episodes
                         settings=[(42, HK, 2000000, 0, None)])         # a friend's resume point
        cls.catalog = load(cls.db)
        cls.a = habits.answer(cls.catalog, {})

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def plays(self):
        return habits.history(self.catalog).plays


# ---------------------------------------------------------------------------------------------------------
class HistoryTests(StoryTestCase):
    def test_counts(self):
        h = self.a["history"]
        # 15 owner logs: 3 repeat logs merged, 2 marked in bulk, 1 marked, 9 played (the deleted film's too)
        self.assertEqual((h["plays"], h["played"], h["marked"], h["bulk_marked"], h["double_logs"]), (10, 9, 1, 2, 3))
        self.assertEqual(h["bulk_bursts"], 1)
        self.assertEqual(h["skipped"], 0)
        self.assertEqual(h["films"], 4 + 1)                       # Matrix, HK, 2001, Only Assistant + the deleted one
        self.assertEqual(h["gone_plays"], 1)
        self.assertEqual(h["first"], local_datetime(GONE_TS).isoformat(timespec="minutes"))
        self.assertEqual(h["as_of"], "2026-09-25")
        self.assertEqual(h["days"], (date(2026, 9, 25) - local_datetime(GONE_TS).date()).days + 1)
        self.assertEqual(h["measured_hours"], 2.0)                # the owner's movie month rows only
        self.assertEqual(h["plex_count"], 1)                      # HK's view_count
        self.assertIsNone(h["server_since"])                      # the fixture's libraries have no created_at
        self.assertRegex(h["utc_offset"], r"^[+-]\d{4}$")

    def test_owner_only(self):
        # the friend's Matrix play (and rating 9) and resume point don't count
        self.assertTrue(all(p.at != 1776911057 for p in self.plays()))
        self.assertEqual(self.catalog.films[MATRIX].owner_rating, 10.0)       # the owner's, not the friend's 9
        self.assertEqual(self.a["stopped"][0]["key"], MATRIX)     # the owner's Matrix, not the friend's HK
        self.assertEqual(len(self.a["stopped"]), 1)

    def test_mapping(self):
        by_at = {p.first_at: p for p in self.plays()}
        self.assertEqual(by_at[ts(2025, 3, 4, 20, 0)].key, HK_KEY)          # an edition's GUID: the film
        self.assertEqual(by_at[ts(2025, 3, 5, 20, 0)].key, LEGACY_KEY)       # a legacy agent: via its copy's id
        self.assertEqual(by_at[ts(2026, 2, 10, 20, 0)].key, HK_KEY)          # an edition renamed since
        gone = by_at[GONE_TS]
        self.assertEqual((gone.key, gone.title, gone.year, gone.how), (None, "Deleted Movie", None, "played"))
        films = {r["key"]: r for r in self.a["year"]["plays_list"]}
        self.assertTrue(all(r["in_library"] for r in films.values()))
        answer = habits.answer(self.catalog, {"year": 2023, "parts": ["year"]})["year"]
        self.assertEqual(answer["plays_list"][0]["title"], "Deleted Movie")
        self.assertFalse(answer["plays_list"][0]["in_library"])
        self.assertEqual(answer["gone_plays"], 1)
        self.assertEqual(answer["top_from"], 0)                   # a deleted film has no genres or cast

    def test_doubles_and_marks(self):
        by_at = {p.first_at: p for p in self.plays()}
        a = by_at[ts(2025, 3, 3, 21, 0)]
        self.assertEqual((a.logs, a.how), (2, "played"))
        g = by_at[ts(2025, 4, 12, 20, 0)]
        self.assertEqual((g.logs, g.how, g.at), (2, "marked", ts(2025, 4, 12, 20, 0)))
        i = by_at[ts(2025, 6, 1, 18, 0)]                          # the marked play took the played log's time
        self.assertEqual((i.logs, i.how, i.at), (2, "played", ts(2025, 6, 1, 21, 0)))
        self.assertEqual(by_at[ts(2025, 4, 10, 12, 0, 0)].how, "bulk")
        self.assertEqual(by_at[ts(2025, 4, 10, 12, 0, 30)].how, "bulk")

    def test_start_times(self):
        by_at = {p.first_at: p for p in self.plays()}
        a = by_at[ts(2025, 3, 3, 21, 0)]
        self.assertEqual(a.start, int(ts(2025, 3, 3, 21, 0) - 0.9 * 8175.712))       # that copy's running time
        o = by_at[ts(2026, 2, 10, 20, 0)]                         # no copy with that GUID: the film's running time
        self.assertEqual(o.start, int(ts(2026, 2, 10, 20, 0) - 0.9 * 90 * 60))
        self.assertIsNone(by_at[ts(2025, 3, 5, 20, 0)].start)     # 2001 has no running time
        self.assertIsNone(by_at[ts(2025, 4, 12, 20, 0)].start)    # marked: Plex didn't see it play
        self.assertIsNone(by_at[GONE_TS].start)


class HeatmapTests(StoryTestCase):
    def test_cells_are_estimated_starts(self):
        hm = self.a["heatmap"]
        expected = [[0] * 24 for _ in range(7)]
        for at, seconds in ((ts(2025, 3, 3, 21, 0), 8175.712), (ts(2025, 3, 4, 20, 0), 5400),
                            (ts(2025, 6, 1, 21, 0), 5400), (ts(2026, 1, 6, 20, 0), 8175.712),
                            (ts(2026, 2, 10, 20, 0), 5400)):
            s = local_datetime(int(at - 0.9 * seconds))
            expected[s.weekday()][s.hour] += 1
        self.assertEqual(hm["cells"], expected)
        self.assertEqual((hm["plays"], hm["left_out"]), (5, 5))  # marked, no running time, the deleted film
        self.assertEqual(sum(hm["by_day"]), 5)
        self.assertEqual(sum(hm["by_hour"]), 5)
        s = local_datetime(int(ts(2025, 3, 3, 21, 0) - 0.9 * 8175.712))
        films = hm["films"][f"{s.weekday()},{s.hour}"]
        self.assertIn("The Matrix", [f["title"] for f in films])
        self.assertTrue(all({"key", "title", "year", "in_library", "at"} <= set(f) for f in films))
        peak = hm["peak"]
        self.assertEqual(peak["plays"], max(max(r) for r in expected))
        self.assertEqual(peak["day"], habits.DAY_NAMES[peak["weekday"]])


class MonthsAndStreaksTests(StoryTestCase):
    def test_months_run_from_the_first_play_to_the_backup(self):
        months = self.a["months"]
        first = local_datetime(GONE_TS)
        self.assertEqual(months[0]["month"], first.strftime("%Y-%m"))
        self.assertEqual(months[-1]["month"], "2026-09")
        keys = [m["month"] for m in months]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(len(months), (2026 - first.year) * 12 + 9 - first.month + 1)   # no gaps
        self.assertTrue(months[0]["partial"] and months[-1]["partial"])
        self.assertFalse(any(m["partial"] for m in months[1:-1]))
        march = next(m for m in months if m["month"] == "2025-03")
        self.assertEqual((march["plays"], march["marked"], march["hours"]), (3, 0, 2.0))
        april = next(m for m in months if m["month"] == "2025-04")
        # (the bulk marks aren't in it)
        self.assertEqual((april["plays"], april["marked"], april["hours"]), (1, 1, None))
        self.assertEqual(march["label"], "Mar 2025")

    def test_streaks(self):
        st = self.a["streaks"]
        # two 3-day runs (Mar 3-5 2025, Jan 5-7 2026): the earlier one
        self.assertEqual(st["longest"], {"days": 3, "from": "2025-03-03", "to": "2025-03-05", "marked_only": 0})
        self.assertEqual(st["busiest_week"], {"from": "2025-03-03", "to": "2025-03-09", "plays": 3})
        self.assertEqual(st["busiest_month"]["month"], "2025-03")
        self.assertEqual(st["busiest_month"]["label"], "March 2025")
        self.assertEqual(st["busiest_day"]["plays"], 1)
        self.assertEqual(st["busiest_day"]["date"], local_datetime(GONE_TS).date().isoformat())   # ties: earliest
        self.assertEqual([f["how"] for f in st["busiest_day"]["films"]], ["played"])

    def test_marks_in_streaks_and_busy_days(self):
        # a mark counts on the day it was ticked: the streak says how many of its days have only marks, and the
        # busiest day's films say which were marked
        with tempfile.TemporaryDirectory() as d:
            views = [(MATRIX, ts(2025, 5, 1, 20), 0), (HK, ts(2025, 5, 2, 9), 1), (LEGACY, ts(2025, 5, 2, 20), 1),
                     (SF, ts(2025, 5, 3, 20), 0)]
            c = load(make_db(d, views, keep_gone=False))
            st = habits.answer(c, {"parts": ["streaks"]})["streaks"]
            self.assertEqual(st["longest"], {"days": 3, "from": "2025-05-01", "to": "2025-05-03", "marked_only": 1})
            self.assertEqual(st["busiest_day"]["date"], "2025-05-02")
            self.assertEqual([f["how"] for f in st["busiest_day"]["films"]], ["marked", "marked"])
            y = habits.answer(c, {"year": 2025, "parts": ["year"]})["year"]
            self.assertEqual(y["longest_streak"]["marked_only"], 1)

    def test_three_days_beat_two_and_weeks_start_on_monday(self):
        with tempfile.TemporaryDirectory() as d:
            views = [(MATRIX, ts(2025, 5, 3, 20), 0), (HK, ts(2025, 5, 4, 20), 0),          # Sat, Sun: 2 days
                     (LEGACY, ts(2025, 5, 6, 20), 0), (SF, ts(2025, 5, 6, 23), 0),         # Tuesday: 2 films
                     (MATRIX, ts(2025, 6, 10, 20), 0), (HK, ts(2025, 6, 11, 20), 0), (SF, ts(2025, 6, 12, 20), 0)]
            c = load(make_db(d, views, keep_gone=False))
            st = habits.answer(c, {"parts": ["streaks"]})["streaks"]
            self.assertEqual(st["longest"], {"days": 3, "from": "2025-06-10", "to": "2025-06-12", "marked_only": 0})
            self.assertEqual(st["busiest_day"]["date"], "2025-05-06")
            self.assertEqual([f["title"] for f in st["busiest_day"]["films"]],
                             ["2001: A Space Odyssey", "Only Assistant"])
            # weeks run Monday to Sunday: Sun May 4 goes with Sat May 3 (2), not with Tue May 6 (which would make
            # 3 and win, being earlier than Jun 9-15's 3)
            self.assertEqual(st["busiest_week"], {"from": "2025-06-09", "to": "2025-06-15", "plays": 3})
            # weeks from Sunday (Settings > Dates and times): Sun May 4 goes with Tue May 6 - 3 plays, and earlier
            for start in ("sunday", "Sun"):
                st = habits.answer(c, {"parts": ["streaks"], "week_start": start})["streaks"]
                self.assertEqual(st["busiest_week"], {"from": "2025-05-04", "to": "2025-05-10", "plays": 3})
            st = habits.answer(c, {"parts": ["streaks"], "week_start": "monday"})["streaks"]
            self.assertEqual(st["busiest_week"]["from"], "2025-06-09")
            with self.assertRaises(ValueError):
                habits.answer(c, {"parts": ["streaks"], "week_start": "wednesday"})
            self.assertEqual((habits.week_start(None), habits.week_start("MONDAY"), habits.week_start("sunday")),
                             (0, 0, 6))


class MostPlayedTests(StoryTestCase):
    def test_most_played(self):
        rows = self.a["most_played"]
        self.assertEqual([r["key"] for r in rows], [HK_KEY, MATRIX, LEGACY_KEY, "plex://movie/sf75"])
        hk = rows[0]
        self.assertEqual((hk["plays"], hk["dated"], hk["undated"], hk["plex_count"]), (3, 3, 0, 1))
        sf = rows[3]
        self.assertEqual((sf["plays"], sf["dated"], sf["undated"]), (2, 1, 1))       # one of them marked in bulk
        self.assertEqual(rows[1]["dates"], ["2025-03-03", "2025-04-12", "2026-01-06"])
        self.assertEqual(rows[1]["last"], "2026-01-06")
        self.assertEqual(self.a["most_played_total"], 4)

    def test_plex_counts_plays_it_never_logged(self):
        with tempfile.TemporaryDirectory() as d:
            db = make_db(d, [(HK, ts(2025, 3, 4, 20), 0)], keep_gone=False,
                         sql=[("UPDATE metadata_item_settings SET view_count = 4 WHERE guid = ?", (HK,)),
                              ("INSERT INTO metadata_item_settings (account_id, guid, view_count) VALUES (1, ?, 3)",
                               (SF,))])
            c = load(db)
            rows = {r["key"]: r for r in habits.answer(c, {"parts": ["most_played"]})["most_played"]}
            self.assertEqual((rows[HK_KEY]["plays"], rows[HK_KEY]["dated"], rows[HK_KEY]["undated"]), (4, 1, 3))
            # counted three times by Plex, never logged
            self.assertEqual((rows[SF]["plays"], rows[SF]["dated"], rows[SF]["last"]), (3, 0, None))
            self.assertEqual(list(rows), [HK_KEY, SF])

    def test_editions_ticked_off_together_count_once(self):
        # Plex counts each edition's GUID on its own: ticking off both cuts of a film you'd seen (10 s apart, never
        # logged) is one 'seen it', not two plays
        with tempfile.TemporaryDirectory() as d:
            c = load(make_db(d, [], keep_gone=False, sql=[
                EDITION_SQL,
                ("UPDATE metadata_item_settings SET last_viewed_at = ? WHERE guid = ?", (ts(2025, 2, 16, 12), HK)),
                ("INSERT INTO metadata_item_settings (account_id, guid, view_count, last_viewed_at) VALUES (1, ?, 1, ?)",
                 (HK_THEATRICAL, ts(2025, 2, 16, 12, 0, 10)))]))
            self.assertEqual(c.films[HK_KEY].owner_plays, 2)                # (Plex's count adds the editions up)
            h = habits.history(c)
            self.assertEqual(h.extra, {HK_KEY: 1})
            a = habits.answer(c, {"parts": ["most_played"]})
            self.assertEqual(a["most_played"], [])
            self.assertEqual((a["history"]["undated_films"], a["history"]["undated_plays"]), (1, 1))
            fh = habits.film_history(c, HK_KEY)
            self.assertEqual((fh["plays"], fh["plex_count"], fh["undated_plays"]), ([], 2, 1))

    def test_an_edition_ticked_off_then_another_played(self):
        # one cut ticked off (seen before), another played later and logged twice: 2 plays, and the play is a rewatch
        with tempfile.TemporaryDirectory() as d:
            c = load(make_db(d, [(HK, ts(2025, 12, 18, 20, 0, 0), 1), (HK, ts(2025, 12, 18, 20, 0, 12), 1)],
                             keep_gone=False, sql=[
                EDITION_SQL,
                ("INSERT INTO metadata_item_settings (account_id, guid, view_count, last_viewed_at) VALUES (1, ?, 1, ?)",
                 (HK_THEATRICAL, ts(2025, 2, 17, 6)))]))
            a = habits.answer(c, {"parts": ["most_played", "year"]})
            row = a["most_played"][0]
            self.assertEqual((row["key"], row["plays"], row["dated"], row["undated"], row["plex_count"]),
                             (HK_KEY, 2, 1, 1, 2))
            self.assertEqual((a["year"]["new"], a["year"]["rewatched"]), (0, 1))


class StoppedTests(unittest.TestCase):
    def test_resume_points(self):
        with tempfile.TemporaryDirectory() as d:
            db = make_db(d, [(HK, ts(2025, 3, 4, 20), 0)], settings=[
                (1, LEGACY, 90000, 0, None),                        # a peek: 90 s in
                (1, HK, 5400000 - 300000, 1, None),                 # 5 minutes from the end: as good as finished
                (1, SF, 1800000, 0, ts(2026, 3, 3, 21, 10))])       # half an hour in, no running time
            c = load(db)
            stopped = habits.answer(c, {"parts": ["stopped"]})["stopped"]
            # the latest first, then those with no date (the fixture's Matrix: 3,723,000 ms in)
            self.assertEqual([r["key"] for r in stopped], [SF, MATRIX])
            sf = stopped[0]
            self.assertEqual((sf["at_sec"], sf["runtime_sec"], sf["share"]), (1800, None, None))
            self.assertEqual(sf["stopped"], "2026-03-03T21:10")
            m = stopped[1]
            self.assertEqual((m["at_sec"], m["runtime_sec"], m["share"], m["stopped"]), (3723, 8175, 0.455, None))
            self.assertEqual((m["plays_before"], m["plex_count"]), (0, 0))
            fh = habits.film_history(c, MATRIX)
            self.assertEqual(fh["resume"], {"at_sec": 3723, "at": "1:02:03", "runtime_sec": 8175, "share": 0.455,
                                            "stopped": None})
            self.assertIsNone(habits.film_history(c, HK_KEY)["resume"])        # too close to the end


class ForgottenTests(StoryTestCase):
    def test_default_rule(self):
        rows = self.a["forgotten"]
        self.assertEqual([r["key"] for r in rows], [OLD])          # rated 9, only ever marked in bulk
        self.assertEqual((rows[0]["last_played"], rows[0]["bulk_marked"]), (None, "2025-04-10"))
        self.assertEqual(self.a["forgotten_rule"], {"min_rating": 9.0, "days": 365, "since": "2025-09-25"})

    def test_parameters_and_order(self):
        a = habits.answer(self.catalog, {"min_rating": 9, "forgotten_days": 200, "parts": ["forgotten"]})
        self.assertEqual([r["key"] for r in a["forgotten"]], [MATRIX, LEGACY_KEY, OLD, "plex://movie/sf75"])
        self.assertEqual(a["forgotten_total"], 4)
        a = habits.answer(self.catalog, {"min_rating": 10, "forgotten_days": 200, "count": 1,
                                         "parts": ["forgotten"]})
        self.assertEqual([r["key"] for r in a["forgotten"]], [MATRIX])     # count cuts the list...
        self.assertEqual(a["forgotten_total"], 2)                          # ...not the total
        a = habits.answer(self.catalog, {"min_rating": 8, "forgotten_days": 30, "parts": ["forgotten"]})
        self.assertIn(HK_KEY, [r["key"] for r in a["forgotten"]])
        self.assertEqual(a["forgotten_rule"]["since"], "2026-08-26")

    def test_counted_but_never_logged(self):
        # Plex counts a play it never logged: the row says when the film was marked as played (Plex keeps when a
        # film's watched tick was set), which isn't when it was watched; the history says how many of those films
        # were ticked off in its first week (before its first play too)
        with tempfile.TemporaryDirectory() as d:
            c = load(make_db(d, [(MATRIX, ts(2025, 2, 16, 20), 0)], keep_gone=False,
                             settings=[(1, SF, 0, 1, ts(2025, 2, 16, 13)), (1, LEGACY, 0, 1, ts(2025, 3, 20, 20))],
                             ratings={SF: 9.0, LEGACY: 10.0}))
            a = habits.answer(c, {"parts": ["forgotten"]})
            rows = {r["key"]: r for r in a["forgotten"]}
            self.assertEqual(list(rows), [LEGACY_KEY, SF])
            self.assertEqual((rows[SF]["last_played"], rows[SF]["bulk_marked"], rows[SF]["plex_marked"]),
                             (None, None, "2025-02-16T13:00"))
            self.assertEqual(rows[LEGACY_KEY]["plex_marked"], "2025-03-20T20:00")
            # (the fixture's Hong Kong Action is counted too: ticked in Nov 2023, before the history)
            h = a["history"]
            self.assertEqual((h["undated_films"], h["undated_plays"], h["undated_first_week"]), (3, 3, 2))
        self.assertIsNone(self.a["forgotten"][0]["plex_marked"])          # (marked in bulk: a log says when)


class YearInReviewTests(StoryTestCase):
    def test_years_and_default(self):
        self.assertEqual(self.a["years"], [{"year": 2023, "plays": 1}, {"year": 2025, "plays": 5},
                                           {"year": 2026, "plays": 4}])
        self.assertEqual(self.a["year"]["year"], 2026)                   # 'latest' is the default
        self.assertEqual(habits.answer(self.catalog, {"year": "latest", "parts": ["year"]})["year"]["year"], 2026)
        self.assertEqual(habits.answer(self.catalog, {"year": "2025", "parts": ["year"]})["year"]["year"], 2025)

    def test_2025(self):
        y = habits.answer(self.catalog, {"year": 2025, "parts": ["year"]})["year"]
        self.assertEqual((y["from"], y["to"], y["partial"]), ("2025-01-01", "2025-12-31", False))
        self.assertEqual((y["plays"], y["films"], y["new"], y["rewatched"], y["marked"], y["bulk_left_out"]),
                         (5, 3, 3, 2, 1, 2))
        self.assertEqual(y["first"]["title"], "The Matrix")
        self.assertEqual(y["first"]["at"], "2025-03-03T21:00")
        self.assertEqual((y["last"]["key"], y["last"]["at"]), (HK_KEY, "2025-06-01T21:00"))
        self.assertEqual(y["busiest_month"]["label"], "March")
        self.assertEqual(y["longest_streak"], {"days": 3, "from": "2025-03-03", "to": "2025-03-05", "marked_only": 0})
        self.assertEqual(y["rating"], {"average": 9.33, "rated": 3, "films": 3})
        self.assertEqual(y["hours"]["measured"], 2)
        self.assertEqual(y["hours"]["runtime"], round((136 + 90 + 136 + 90) / 60))   # 2001 has no running time
        self.assertIsNone(y["vs_previous"])                             # nothing in 2024
        self.assertEqual([m["plays"] for m in y["months"]], [0, 0, 3, 1, 0, 1, 0, 0, 0, 0, 0, 0])
        self.assertTrue(all(m["in_history"] for m in y["months"]))
        self.assertEqual(y["days"]["2025-03-03"], 1)
        how = {r["at"]: (r["how"], r["rewatch"]) for r in y["plays_list"]}
        self.assertEqual(how["2025-04-12T20:00"], ("marked", True))
        self.assertEqual(how["2025-03-03T21:00"], ("played", False))

    def test_top_lists(self):
        top = habits.answer(self.catalog, {"year": 2025, "parts": ["year"]})["year"]["top"]
        self.assertEqual(top["genres"], [{"label": "Science Fiction", "films": 2}, {"label": "Action", "films": 1},
                                         {"label": "Adventure", "films": 1}])
        self.assertEqual([d["label"] for d in top["decades"]], ["1960s", "1980s", "1990s"])
        self.assertEqual(top["countries"], [{"label": "United States of America", "films": 1}])
        # stars billed in the top 3 only, ties by name ('Fourth Person' is billed fourth)
        self.assertEqual([a["name"] for a in top["actors"]],
                         ["Keanu Reeves", "Keir Dullea", "Laurence Fishburne", "Ng Gam-Hung"])
        self.assertEqual(top["actors"][0]["id"], "p6")
        self.assertEqual([a["name"] for a in top["directors"]],
                         ["Lana Wachowski", "Lilly Wachowski", "Stanley Kubrick", "Yuen Woo-ping"])
        few = habits.answer(self.catalog, {"year": 2025, "top": 2, "parts": ["year"]})["year"]["top"]
        self.assertEqual(len(few["actors"]), 2)

    def test_2026_so_far_and_the_year_before(self):
        y = self.a["year"]
        self.assertEqual((y["from"], y["to"], y["partial"]), ("2026-01-01", "2026-09-25", True))
        # every 2026 play is a rewatch - Only Assistant's earlier play was marked in bulk
        self.assertEqual((y["plays"], y["new"], y["rewatched"]), (4, 0, 4))
        self.assertEqual([m["in_history"] for m in y["months"]], [True] * 9 + [False] * 3)
        self.assertTrue(y["months"][8]["partial"])
        # (ratings are as they are now, of the films each stretch played: how many of them are rated, too)
        self.assertEqual(y["vs_previous"], {"year": 2025, "from": "01-01", "to": "09-25", "plays": [4, 5],
                                            "films": [4, 3], "rating": [9.25, 9.33], "rated": [4, 3]})
        self.assertIsNone(y["hours"]["measured"])                       # no playback clock rows for 2026

    def test_a_year_without_plays(self):
        y = habits.answer(self.catalog, {"year": 2019, "parts": ["year"]})["year"]
        self.assertEqual((y["year"], y["plays"], y["films"], y["first"], y["longest_streak"]), (2019, 0, 0, None, None))
        self.assertFalse(y["in_history"])
        self.assertIsNone(y["from"])
        self.assertEqual(y["plays_list"], [])
        self.assertEqual(y["top"]["genres"], [])
        y = habits.answer(self.catalog, {"year": 2024, "parts": ["year"]})["year"]     # inside the history
        self.assertTrue(y["in_history"])
        self.assertEqual(y["plays"], 0)

    def test_new_or_rewatched_by_plex_count(self):
        with tempfile.TemporaryDirectory() as d:
            c = load(make_db(d, [(HK, ts(2025, 3, 4, 20), 0)], keep_gone=False))
            y = habits.answer(c, {"parts": ["year"]})["year"]
            self.assertEqual((y["new"], y["rewatched"]), (1, 0))          # Plex's count 1 = the one log
        with tempfile.TemporaryDirectory() as d:
            c = load(make_db(d, [(HK, ts(2025, 3, 4, 20), 0)], keep_gone=False,
                             sql=[("UPDATE metadata_item_settings SET view_count = 2 WHERE guid = ?", (HK,))]))
            y = habits.answer(c, {"parts": ["year"]})["year"]
            self.assertEqual((y["new"], y["rewatched"]), (0, 1))          # Plex knows of a play before it

    def test_leap_days(self):
        # history from Feb 29 2024; the backup on Feb 29 2028: every window lines up without an invalid date
        with tempfile.TemporaryDirectory() as d:
            views = [(MATRIX, ts(2024, 2, 29, 20), 0), (HK, ts(2024, 3, 2, 20), 0), (MATRIX, ts(2025, 2, 28, 20), 0),
                     (SF, ts(2027, 2, 28, 20), 0), (HK, ts(2028, 2, 29, 12), 0), (LEGACY, ts(2028, 2, 10, 12), 0)]
            c = load(make_db(d, views, keep_gone=False, name="2028-02-29"))
            y25 = habits.answer(c, {"year": 2025, "parts": ["year"]})["year"]
            self.assertEqual(y25["vs_previous"]["from"], "02-28")
            self.assertEqual(y25["vs_previous"]["plays"], [1, 2])         # Feb 28 onwards, both years
            y28 = habits.answer(c, {"year": 2028, "parts": ["year"]})["year"]
            self.assertEqual((y28["from"], y28["to"]), ("2028-01-01", "2028-02-29"))
            self.assertEqual(y28["vs_previous"]["to"], "02-28")
            self.assertEqual(y28["vs_previous"]["plays"], [2, 1])         # Feb 29 2028 is in it

    def test_no_overlap_with_the_year_before(self):
        with tempfile.TemporaryDirectory() as d:
            views = [(MATRIX, ts(2025, 11, 20, 20), 0), (HK, ts(2026, 3, 2, 20), 0)]
            c = load(make_db(d, views, keep_gone=False, name="2026-03-05"))
            y = habits.answer(c, {"year": 2026, "parts": ["year"]})["year"]
            self.assertIsNone(y["vs_previous"])            # 2026 so far is Jan-Mar; 2025's history is Nov-Dec


# ---------------------------------------------------------------------------------------------------------
class RuleTests(unittest.TestCase):
    def answer(self, views, **kw):
        with tempfile.TemporaryDirectory() as d:
            c = load(make_db(d, views, keep_gone=False, **kw))
            return c, habits.answer(c, {}), habits.history(c)

    def test_six_hour_doubles(self):
        _c, a, h = self.answer([(MATRIX, ts(2025, 5, 1, 12, 0), 0), (MATRIX, ts(2025, 5, 1, 17, 59), 0),
                                (MATRIX, ts(2025, 5, 2, 0, 1), 0)])
        # 5 h 59 m after the first: the same play; 6 h 2 m after that log (chained): another play
        self.assertEqual([p.logs for p in h.plays], [2, 1])
        self.assertEqual(a["history"]["double_logs"], 1)
        _c, a, h = self.answer([(MATRIX, ts(2025, 5, 1, 12, 0), 0), (MATRIX, ts(2025, 5, 1, 18, 1), 0)])
        self.assertEqual(len(h.plays), 2)                                  # 6 h 1 m: a rewatch

    def test_marked_after_a_play(self):
        _c, _a, h = self.answer([(MATRIX, ts(2025, 5, 1, 20), 0), (MATRIX, ts(2025, 5, 4, 20), 1)])
        self.assertEqual([(p.how, p.logs) for p in h.plays], [("played", 2)])    # 3 days later: the same play
        _c, _a, h = self.answer([(MATRIX, ts(2025, 5, 1, 20), 0), (MATRIX, ts(2025, 5, 6, 20), 1)])
        self.assertEqual([p.how for p in h.plays], ["played", "marked"])        # 5 days later: another

    def test_another_edition_marked_after_a_play(self):
        cut = "plex://movie/hk1/edition/Director's Cut"
        # one cut played, another marked as played a month later (keeping the cuts' ticks in step): one play
        _c, a, h = self.answer([(HK, ts(2025, 3, 4, 20), 0), (cut, ts(2025, 4, 3, 20), 1)])
        self.assertEqual([(p.how, p.logs, p.guid, p.at) for p in h.plays], [("played", 2, HK, ts(2025, 3, 4, 20))])
        self.assertEqual((a["history"]["plays"], a["history"]["double_logs"]), (1, 1))
        # ...but not after a play that was itself only marked,
        _c, _a, h = self.answer([(HK, ts(2025, 3, 4, 20), 1), (cut, ts(2025, 4, 3, 20), 1)])
        self.assertEqual([p.how for p in h.plays], ["marked", "marked"])
        # nor for an edition logged before (that's the same cut again),
        _c, _a, h = self.answer([(cut, ts(2025, 3, 1, 20), 0), (HK, ts(2025, 3, 11, 20), 0),
                                 (cut, ts(2025, 4, 10, 20), 1)])
        self.assertEqual([p.how for p in h.plays], ["played", "played", "marked"])
        # nor when the other cut is played rather than marked: that's a rewatch
        _c, _a, h = self.answer([(HK, ts(2025, 3, 4, 20), 0), (cut, ts(2025, 4, 3, 20), 0)])
        self.assertEqual([p.how for p in h.plays], ["played", "played"])

    def test_bulk(self):
        _c, a, h = self.answer([(MATRIX, ts(2025, 5, 1, 20, 0, 0), 0), (HK, ts(2025, 5, 1, 20, 0, 30), 0),
                                (LEGACY, ts(2025, 6, 1, 20), 0)])
        self.assertEqual([p.how for p in h.plays], ["bulk", "bulk", "played"])
        self.assertEqual(a["history"]["plays"], 1)
        self.assertEqual(a["history"]["bulk_marked"], 2)
        self.assertEqual(a["years"], [{"year": 2025, "plays": 1}])
        self.assertEqual(sum(m["plays"] for m in a["months"]), 1)
        self.assertEqual(a["heatmap"]["plays"] + a["heatmap"]["left_out"], 1)
        self.assertEqual(a["streaks"]["longest"]["from"], "2025-06-01")
        self.assertEqual((a["year"]["plays"], a["year"]["bulk_left_out"]), (1, 2))
        # the same film 30 s apart is one play logged twice, not a bulk mark
        _c, a, h = self.answer([(MATRIX, ts(2025, 5, 1, 20, 0, 0), 0), (MATRIX, ts(2025, 5, 1, 20, 0, 30), 0)])
        self.assertEqual([(p.how, p.logs) for p in h.plays], [("played", 2)])

    def test_bulk_marks_are_in_the_film_history_only(self):
        with tempfile.TemporaryDirectory() as d:
            c = load(make_db(d, [(MATRIX, ts(2025, 5, 1, 20, 0, 0), 1), (HK, ts(2025, 5, 1, 20, 0, 30), 1)],
                             keep_gone=False, ratings={MATRIX: 10}))
            fh = habits.film_history(c, MATRIX)
            self.assertEqual([p["how"] for p in fh["plays"]], ["bulk"])
            self.assertEqual((fh["plays_on_record"], fh["last_played"]), (0, None))
            fav = habits.answer(c, {"parts": ["forgotten"]})
            self.assertEqual(fav["forgotten"][0]["last_played"], None)
            self.assertEqual(fav["forgotten"][0]["bulk_marked"], "2025-05-01")
            self.assertTrue(fav["empty"])                                  # no dated plays at all
            self.assertIn("marked in bulk", fav["note"])

    def test_without_view_type_every_play_is_played(self):
        _c, a, h = self.answer([(MATRIX, ts(2025, 5, 1, 20), 1), (HK, ts(2025, 5, 3, 20), 1)], view_type=False)
        self.assertEqual([p.how for p in h.plays], ["played", "played"])
        self.assertEqual(a["history"]["marked"], 0)
        self.assertIsNone(a["history"]["measured_hours"])                  # no statistics_media either
        self.assertIsNone(a["year"]["hours"]["measured"])
        self.assertEqual(a["year"]["hours"]["runtime"], round((136 + 90) / 60))

    def test_a_library_that_isnt_loaded(self):
        with tempfile.TemporaryDirectory() as d:
            db = make_db(d, [(OLD, ts(2025, 5, 1, 20), 0), (MATRIX, ts(2025, 5, 2, 20), 0)], keep_gone=False)
            c = load(db, [1])                                  # 'Movies' only: the Classics film isn't loaded
            a = habits.answer(c, {})
            self.assertEqual((a["history"]["plays"], a["history"]["skipped"]), (1, 1))
            c = load(db)
            self.assertEqual(habits.answer(c, {})["history"]["skipped"], 0)

    def test_a_deleted_film_with_a_release_date(self):
        released = int(datetime(1994, 6, 1).timestamp())
        _c, a, h = self.answer([("plex://movie/deleted", ts(2025, 5, 1, 20), 0, 1, "Speed", released)])
        self.assertEqual((h.plays[0].title, h.plays[0].year, h.plays[0].key), ("Speed", 1994, None))
        self.assertEqual(a["year"]["plays_list"][0]["in_library"], False)
        self.assertEqual(a["most_played"], [])

    def test_no_plays(self):
        _c, a, _h = self.answer([])
        self.assertTrue(a["ok"])
        self.assertTrue(a["empty"])
        self.assertEqual(a["note"], "Plex has no plays logged for your account.")
        self.assertEqual((a["history"]["plays"], a["history"]["first"]), (0, None))
        self.assertEqual(a["years"], [])

    def test_server_since(self):
        _c, a, _h = self.answer([(MATRIX, ts(2025, 5, 1, 20), 0)], sections_created=ts(2025, 4, 30, 9))
        self.assertEqual(a["history"]["server_since"], "2025-04-30")


# ---------------------------------------------------------------------------------------------------------
class FilmHistoryTests(StoryTestCase):
    def test_one_film(self):
        fh = habits.film_history(self.catalog, HK_KEY)
        self.assertTrue(fh["ok"])
        self.assertEqual([(p["at"], p["how"], p["logs"]) for p in fh["plays"]],
                         [("2025-03-04T20:00", "played", 1), ("2025-06-01T21:00", "played", 2),
                          ("2026-02-10T20:00", "played", 1)])
        self.assertEqual(fh["plays"][0]["device"], "Living Room TV")
        self.assertEqual((fh["plays_on_record"], fh["plex_count"], fh["rating"]), (3, 1, 8.0))
        self.assertEqual((fh["first_played"], fh["last_played"]), ("2025-03-04T20:00", "2026-02-10T20:00"))
        self.assertIsNone(fh["resume"])
        self.assertEqual((fh["history_from"], fh["as_of"]), (local_datetime(GONE_TS).date().isoformat(), "2026-09-25"))
        self.assertEqual(habits.film_history(self.catalog, "plex://movie/sf75")["plays"][0]["how"], "bulk")

    def test_bad_keys_never_raise(self):
        for key in ("nope", None, "", 123):
            fh = habits.film_history(self.catalog, key)
            self.assertFalse(fh["ok"], key)
            self.assertIn("no film", fh["error"])

    def test_an_unreadable_database(self):
        c = make_catalog([{"title": "A", "year": 2000}])                 # source 'memory': no file
        fh = habits.film_history(c, "f0")
        self.assertFalse(fh["ok"])
        self.assertIn("Couldn't read Plex's play history", fh["error"])
        a = habits.answer(c, {})
        self.assertFalse(a["ok"])
        self.assertTrue(a["error"].startswith("Couldn't read Plex's play history: "))


class AskAndCacheTests(StoryTestCase):
    def test_through_ask(self):
        a = handle({"action": "habits"}, catalog=self.catalog)
        self.assertTrue(a["ok"])
        self.assertEqual(a["action"], "habits")
        bad = handle({"action": "habits", "min_rating": "x"}, catalog=self.catalog)
        self.assertFalse(bad["ok"])
        self.assertIn("bad request", bad["error"])
        self.assertIn("bad request", handle({"action": "habits", "parts": ["nonsense"]}, catalog=self.catalog)["error"])
        self.assertIn("bad request", handle({"action": "habits", "year": "soon"}, catalog=self.catalog)["error"])
        import json
        back = json.loads(to_json(a))
        self.assertEqual(back["history"]["plays"], a["history"]["plays"])
        self.assertEqual(back["year"]["plays_list"][0]["at"], a["year"]["plays_list"][0]["at"])

    def test_parts(self):
        a = habits.answer(self.catalog, {"year": 2025, "parts": ["year"]})
        self.assertEqual(set(a) - {"ok", "action", "owner", "history", "years", "year"}, set())
        self.assertEqual(a["year"]["year"], 2025)
        a = habits.answer(self.catalog, {"parts": "heatmap"})
        self.assertIn("heatmap", a)
        self.assertNotIn("year", a)

    def test_read_once_per_catalog(self):
        c = load(self.db)
        opened = []
        real = habits.PlexDatabase

        def counting(path):
            opened.append(path)
            return real(path)
        habits.PlexDatabase = counting
        try:
            habits.answer(c, {})
            habits.answer(c, {"year": 2025})
            habits.film_history(c, MATRIX)
        finally:
            habits.PlexDatabase = real
        self.assertEqual(len(opened), 1)

    def test_cancelled(self):
        c = load(self.db)
        job = jobs.Job("test")
        job.cancel()
        with jobs.running(job):
            with self.assertRaises(jobs.Cancelled):
                habits.history(c)
            with self.assertRaises(jobs.Cancelled):
                habits.film_history(c, MATRIX)
        self.assertNotIn(habits.CACHE_KEY, c.cache)


if __name__ == "__main__":
    unittest.main()
