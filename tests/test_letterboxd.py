"""Tests for the Letterboxd export (projectionist/letterboxd.py, the ask action and the Export tab's button).

The backbone is checked on test_habits' fixture: its STORY of plays (a film logged twice, plays marked as played,
films marked in bulk, an edition renamed since) and the fixture's own play of a film deleted since. Times are local
wall-clock times, so the tests pass in any time zone. The window runs on a withdrawn root (never shown), with the
save dialog, message boxes, menus and opening files stubbed, and its settings in a temporary file.
"""

import csv
import gc
import io
import json
import os
import re
import sys
import tempfile
import time
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from projectionist import letterboxd as L  # noqa: E402
from projectionist import prefs  # noqa: E402
from projectionist.ask import ACTIONS, handle  # noqa: E402
from projectionist.catalog import load  # noqa: E402
from projectionist.extract import local_datetime  # noqa: E402
from test_habits import HK, HK_KEY, LEGACY, LEGACY_KEY, MATRIX, OLD, SF, STORY, STORY_RATINGS, make_db, ts  # noqa: E402

GONE_DAY = local_datetime(1700000000).date()          # the fixture's play of a film deleted since
HEADER = "imdbID,tmdbID,Title,Year,Directors,WatchedDate,Rewatch,Rating10"
HK_ITEM = 700                                         # Hong Kong Action's copy in the fixture (no IMDb ID)
TMDB_SQL = [("INSERT INTO tags (id, tag, tag_type) VALUES (9001, 'tmdb://4321', 314)", ()),
            ("INSERT INTO taggings (metadata_item_id, tag_id, \"index\") VALUES (?, 9001, 0)", (HK_ITEM,))]


def read_back(text: str) -> list[list[str]]:
    """The file as a reader that knows Letterboxd's escaping (a backslash before a quote) reads it."""
    return list(csv.reader(io.StringIO(text, newline=""), doublequote=False, escapechar="\\"))


class StoryTest(unittest.TestCase):
    """The STORY fixture, with its ratings (The Matrix 10, 2001 10, the 1912 film 9, Only Assistant 9 - and Hong
    Kong Action's own 8)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = make_db(cls.tmp.name, STORY, ratings=STORY_RATINGS)
        cls.catalog = load(cls.db)
        cls.lines = L.rows(cls.catalog, {})

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def diary(self, lines=None):
        return [(x.title, x.watched, x.rewatch, x.rating) for x in (lines or self.lines) if x.kind == "diary"]

    def test_each_dated_play_is_a_diary_entry_oldest_first(self):
        self.assertEqual(self.diary(), [
            ("Deleted Movie", GONE_DAY, False, None),                    # deleted since: as Plex logged it
            ("The Matrix", date(2025, 3, 3), False, None),               # (logged twice: one play)
            ("Hong Kong Action", date(2025, 3, 4), False, None),
            ("2001: A Space Odyssey", date(2025, 3, 5), False, None),
            ("The Matrix", date(2025, 4, 12), True, None),               # marked as played (twice: one play)
            ("Hong Kong Action", date(2025, 6, 1), True, None),          # marked, then played: one play
            ("Only Assistant", date(2026, 1, 5), True, 9),               # seen before: marked in bulk
            ("The Matrix", date(2026, 1, 6), True, 10),                  # the rating on the latest entry only
            ("2001: A Space Odyssey", date(2026, 1, 7), True, 10),
            ("Hong Kong Action", date(2026, 2, 10), True, 8),            # (an edition renamed since)
        ])

    def test_films_marked_in_bulk_with_no_other_play_have_no_date(self):
        undated = [x for x in self.lines if x.kind == "undated"]
        self.assertEqual([(x.title, x.year, x.watched, x.rating) for x in undated],
                         [("=Formula Looking Title", 1912, None, 9)])
        self.assertEqual(undated[0].cells()["WatchedDate"], "")
        self.assertEqual(undated[0].cells()["Rewatch"], "")
        self.assertEqual(self.lines.counts["bulk_marked"], 1)

    def test_matching_details(self):
        by_title = {}
        for x in self.lines:
            by_title.setdefault(x.title, x)
        matrix, space, hk = by_title["The Matrix"], by_title["2001: A Space Odyssey"], by_title["Hong Kong Action"]
        self.assertEqual((matrix.imdb, matrix.year, matrix.directors),
                         ("tt0133093", 1999, ["Lana Wachowski", "Lilly Wachowski"]))
        self.assertEqual((space.imdb, space.directors, space.key), ("tt0062622", ["Stanley Kubrick"], LEGACY_KEY))
        self.assertEqual((hk.imdb, hk.directors, hk.key), ("", ["Yuen Woo-ping"], HK_KEY))   # by title
        self.assertEqual(by_title["Only Assistant"].directors, [])                          # (no assistants)
        self.assertEqual((by_title["Deleted Movie"].key, by_title["Deleted Movie"].year), (None, None))

    def test_counts_and_summary(self):
        c = self.lines.counts
        self.assertEqual((c["diary"], c["rewatches"], c["rated_undated"], c["played_undated"], c["rated"]),
                         (10, 6, 1, 0, 5))
        self.assertEqual((c["films"], c["no_imdb"], c["deleted_since"]), (6, 4, 1))
        self.assertEqual(L.summary(self.lines),
                         "10 diary entries, 1 rated film without a date, 4 films with no IMDb ID matched by title")
        self.assertEqual(L.summary(L.Rows()), "nothing")
        self.assertEqual(self.lines.columns, HEADER.split(","))
        self.assertIsNone(self.lines.since)

    def test_covered_to_is_the_newest_thing_read(self):
        self.assertEqual(L.since_time(self.lines.covered_to), ts(2026, 2, 10, 20, 0))
        self.assertRegex(self.lines.covered_to, r"^2026-02-10T20:00:00[+-]\d\d:\d\d$")

    def test_the_file(self):
        text = L.csv_texts(self.lines)
        self.assertEqual(len(text), 1)
        rows = text[0].split("\r\n")
        self.assertEqual(rows[0], HEADER)
        self.assertEqual(rows[2], 'tt0133093,,The Matrix,1999,"Lana Wachowski, Lilly Wachowski",2025-03-03,false,')
        self.assertEqual(rows[8], 'tt0133093,,The Matrix,1999,"Lana Wachowski, Lilly Wachowski",2026-01-06,true,10')
        self.assertEqual(rows[-2], ",,=Formula Looking Title,1912,,,,9")
        self.assertEqual(rows[-1], "")                                  # (every line ends in CRLF)
        for row in rows:                                                # no space after a comma between columns
            self.assertNotRegex(re.sub(r'"[^"]*"', "", row), r",\s", row)
        parsed = read_back(text[0])
        self.assertEqual(len(parsed), 12)
        self.assertEqual(parsed[1][:6], ["", "", "Deleted Movie", "", "", GONE_DAY.isoformat()])
        self.assertEqual(parsed[2][4], "Lana Wachowski, Lilly Wachowski")

    def test_write_saves_utf8_without_a_byte_order_mark(self):
        path = os.path.join(self.tmp.name, "out", "Letterboxd.csv")
        os.makedirs(os.path.dirname(path))
        self.assertEqual(L.write(path, self.lines), [path])
        with open(path, "rb") as f:
            data = f.read()
        self.assertTrue(data.startswith(HEADER.encode() + b"\r\n"))
        self.assertEqual(data.decode("utf-8"), L.csv_texts(self.lines)[0])
        self.assertEqual(os.listdir(os.path.dirname(path)), ["Letterboxd.csv"])     # (no .tmp left behind)
        with self.assertRaises(OSError):
            L.write(os.path.join(self.tmp.name, "no such folder", "x.csv"), self.lines)

    def test_split_over_the_size_limit_with_the_header_in_each_file(self):
        whole = L.csv_texts(self.lines)[0]
        limit = len(HEADER) + 2 + 150
        texts = L.csv_texts(self.lines, max_bytes=limit)
        self.assertGreater(len(texts), 3)
        body = []
        for t in texts:
            self.assertTrue(t.startswith(HEADER + "\r\n"))
            self.assertLessEqual(len(t.encode("utf-8")), limit)
            body += t.split("\r\n")[1:-1]
        self.assertEqual(body, whole.split("\r\n")[1:-1])               # every line, once, in order
        folder = os.path.join(self.tmp.name, "split")
        os.makedirs(folder)
        paths = L.write(os.path.join(folder, "Diary.csv"), self.lines, max_bytes=limit)
        self.assertEqual(paths[:3], [os.path.join(folder, n) for n in ("Diary.csv", "Diary (2).csv", "Diary (3).csv")])
        self.assertEqual(len(paths), len(texts))
        self.assertEqual(L.part_path("a.csv", 1), "a.csv")
        # a line longer than the limit still goes in (alone), and no lines is just the header
        self.assertEqual(len(L.csv_texts(self.lines[:2], max_bytes=10)), 2)
        self.assertEqual(L.csv_texts(L.Rows([], columns=["Title"])), ["Title\r\n"])

    # -- the options ----------------------------------------------------------------------------------------------
    def test_no_diary_entries(self):
        lines = L.rows(self.catalog, {"diary": False})
        self.assertEqual(lines.columns, ["imdbID", "tmdbID", "Title", "Year", "Directors", "Rating10"])
        self.assertEqual({x.kind for x in lines}, {"undated"})
        self.assertEqual(sorted((x.title, x.rating) for x in lines),
                         [("2001: A Space Odyssey", 10), ("=Formula Looking Title", 9), ("Deleted Movie", None),
                          ("Hong Kong Action", 8), ("Only Assistant", 9), ("The Matrix", 10)])
        self.assertEqual(L.summary(lines), "5 rated films without a date, 1 played film without a date, "
                                           "4 films with no IMDb ID matched by title")
        self.assertEqual(L.csv_texts(lines)[0].split("\r\n")[0], "imdbID,tmdbID,Title,Year,Directors,Rating10")

    def test_no_ratings(self):
        lines = L.rows(self.catalog, {"ratings": False})
        self.assertNotIn("Rating10", lines.columns)
        self.assertTrue(all(x.rating is None for x in lines))
        self.assertEqual((lines.counts["diary"], lines.counts["played_undated"]), (10, 1))   # (the 1912 film: watched)

    def test_no_undated_films(self):
        lines = L.rows(self.catalog, {"undated": False})
        self.assertEqual([x.title for x in lines if x.kind == "undated"], ["=Formula Looking Title"])  # (rated)
        lines = L.rows(self.catalog, {"undated": False, "ratings": False})
        self.assertEqual([x.kind for x in lines], ["diary"] * 10)

    def test_library_tags_go_on_diary_entries_only(self):
        lines = L.rows(self.catalog, {"library_tags": True})
        self.assertEqual(lines.columns[-1], "Tags")
        matrix = next(x for x in lines if x.title == "The Matrix")
        self.assertEqual(matrix.cells()["Tags"], "Movies")
        self.assertEqual(next(x for x in lines if x.kind == "undated").cells()["Tags"], "")
        self.assertEqual(next(x for x in lines if x.title == "Deleted Movie").tags, [])
        self.assertNotIn("Tags", L.rows(self.catalog, {"library_tags": True, "diary": False}).columns)

    def test_leaving_out_libraries(self):
        lines = L.rows(self.catalog, {"leave_out_libraries": ["classics"]})
        self.assertNotIn("=Formula Looking Title", [x.title for x in lines])
        self.assertEqual(lines.counts["left_out"], 1)
        self.assertIn("Deleted Movie", [x.title for x in lines])        # (no library: not left out)
        self.assertEqual([x.title for x in L.rows(self.catalog, {"leave_out_libraries": "Movies"})],
                         ["Deleted Movie", "=Formula Looking Title"])
        self.assertEqual(len(L.rows(self.catalog, {"leave_out_libraries": []})), 11)

    def test_only_whats_new(self):
        lines = L.rows(self.catalog, {"since": "2026-01-06"})           # from the start of that day
        self.assertEqual(self.diary(lines), [("The Matrix", date(2026, 1, 6), True, 10),
                                             ("2001: A Space Odyssey", date(2026, 1, 7), True, 10),
                                             ("Hong Kong Action", date(2026, 2, 10), True, 8)])
        self.assertEqual(len(lines), 3)                                 # (the 1912 film was marked long before)
        self.assertEqual(lines.counts["older"], 8)                      # (7 diary entries and the 1912 film)
        self.assertEqual(lines.since, "2026-01-06")
        self.assertEqual(L.rows(self.catalog, {"since": self.lines.covered_to}), [])
        just_before = L.iso_time(ts(2026, 2, 10, 20, 0) - 1)
        self.assertEqual([x.title for x in L.rows(self.catalog, {"since": just_before})], ["Hong Kong Action"])

    def test_only_whats_new_after_settings_that_put_more_in(self):
        """A file saved without ratings (or without a library), then ratings turned on (or the library back in):
        only what's new would never send the older ratings (or films), so the next file has everything."""
        for first, change in (({"letterboxd_ratings": False}, {"letterboxd_ratings": True}),
                              ({"letterboxd_leave_out": True}, {"letterboxd_leave_out": False})):
            settings = dict(first)
            one = L.rows(self.catalog, L.options_for(settings, ["Classics"]))
            L.remember(settings, one)
            self.assertEqual(L.rows(self.catalog, L.options_for(settings, ["Classics"])), [])   # (nothing new)
            settings.update(change)
            request = L.options_for(settings, ["Classics"])
            self.assertEqual((request["since"], request["wider"]), (None, True), first)
            two = L.rows(self.catalog, request)
            self.assertEqual(len(two), len(self.lines), first)                             # everything
            self.assertEqual(two.counts["rated"], 5)
            self.assertTrue(two.wider)
            self.assertIn("(everything, as Settings > Letterboxd now puts in more than the last file did)",
                          L.saved_words(two, ["a.csv"]))
            L.remember(settings, two)
            self.assertEqual(L.rows(self.catalog, L.options_for(settings, ["Classics"])), [])   # (nothing new again)

    def test_bad_options(self):
        for bad in ({"diary": "maybe"}, {"ratings": 2}, {"since": "yesterday"}, {"since": 5},
                    {"leave_out_libraries": 5}, {"leave_out_libraries": [1]}, {"undated": [True]}):
            with self.assertRaises(ValueError, msg=repr(bad)):
                L.rows(self.catalog, bad)
        self.assertTrue(L.options({"diary": "yes", "ratings": 0, "undated": None})["diary"])
        self.assertFalse(L.options({"ratings": 0})["ratings"])
        self.assertTrue(L.options({"undated": None})["undated"])          # (None: the default)

    # -- the ask action -------------------------------------------------------------------------------------------
    def test_ask(self):
        self.assertIn("letterboxd", ACTIONS)
        for word in ("diary", "undated", "ratings", "library_tags", "since", "leave_out_libraries", "csv", "count"):
            self.assertIn(word, ACTIONS["letterboxd"], word)
        a = handle({"action": "letterboxd", "count": 2}, catalog=self.catalog)
        self.assertTrue(a["ok"], a)
        self.assertEqual((a["lines_total"], len(a["lines"]), a["files"]), (11, 2, 1))
        self.assertEqual(a["lines"][1], {"kind": "diary", "key": MATRIX, "imdbID": "tt0133093", "tmdbID": "",
                                         "Title": "The Matrix", "Year": "1999",
                                         "Directors": "Lana Wachowski, Lilly Wachowski", "WatchedDate": "2025-03-03",
                                         "Rewatch": "false", "Rating10": "", "Tags": ""})
        self.assertEqual(a["summary"], L.summary(self.lines))
        self.assertEqual(a["covered_to"], self.lines.covered_to)
        self.assertEqual(a["scope"], {"diary": True, "undated": True, "ratings": True, "library_tags": False,
                                      "leave_out": []})
        self.assertNotIn("csv", a)
        self.assertEqual(a["format"], "https://letterboxd.com/about/importing-data/")
        a = handle({"action": "letterboxd", "csv": True, "since": "2026-01-06"}, catalog=self.catalog)
        self.assertEqual(a["csv"], L.csv_texts(L.rows(self.catalog, {"since": "2026-01-06"})))
        self.assertEqual(a["since"], "2026-01-06")
        json.dumps(a)
        bad = handle({"action": "letterboxd", "since": "last week"}, catalog=self.catalog)
        self.assertFalse(bad["ok"])
        self.assertIn("since", bad["error"])
        self.assertFalse(handle({"action": "letterboxd", "csv": "please"}, catalog=self.catalog)["ok"])


class SpecialCasesTest(unittest.TestCase):
    """A play that ran past midnight, one marked as played just after midnight, two plays in a day, plays Plex
    counted before its history, a film Plex counts as played with no play logged, a deleted film logged under an
    old agent's GUID, a rating of two copies' average, and ratings changed since the last export."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        views = [
            (HK, ts(2026, 3, 1, 0, 30), 0),                   # 90 minutes, logged at 00:30: began Feb 28
            (MATRIX, ts(2026, 3, 2, 0, 30), 1),               # marked as played: no start, the day it was marked
            (MATRIX, ts(2026, 3, 9, 12, 0), 0),               # two plays on one day (8 hours apart): one entry
            (MATRIX, ts(2026, 3, 9, 20, 0), 0),
            (LEGACY, ts(2026, 3, 5, 20, 0), 0),               # Plex counts 3 plays, 1 logged: seen before
            ("com.plexapp.agents.imdb://tt7654321?lang=en", ts(2026, 3, 6, 20, 0), 0, 1, "Gone Legacy",
             int(datetime(1988, 6, 1, tzinfo=timezone.utc).timestamp())),
            ("com.plexapp.agents.themoviedb://5555?lang=en", ts(2026, 3, 7, 20, 0), 0, 1, "Gone TMDB", None),
        ]
        cls.db = make_db(cls.tmp.name, views, keep_gone=False,
                         settings=[(1, LEGACY, 0, 3, ts(2026, 3, 5, 20, 0)),
                                   (1, SF, 0, 2, ts(2025, 5, 1, 20, 0))],        # counted, never logged
                         ratings={OLD: 7.5, MATRIX: 6.0},
                         sql=[("ALTER TABLE metadata_item_settings ADD COLUMN last_rated_at INTEGER", ()),
                              ("UPDATE metadata_item_settings SET last_rated_at = ? WHERE account_id = 1 AND guid = ?",
                               (ts(2026, 4, 1, 9, 0), MATRIX)),
                              ("UPDATE metadata_item_settings SET last_rated_at = ? WHERE account_id = 1 AND guid = ?",
                               (ts(2024, 1, 1, 9, 0), OLD))] + TMDB_SQL)          # (Hong Kong Action's TMDB ID)
        cls.catalog = load(cls.db)
        cls.lines = L.rows(cls.catalog, {})
        cls.by = {}
        for x in cls.lines:
            cls.by.setdefault(x.title, []).append(x)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_a_film_that_ran_past_midnight_goes_on_the_evening_it_began(self):
        self.assertEqual([x.watched for x in self.by["Hong Kong Action"]], [date(2026, 2, 28)])

    def test_a_film_marked_as_played_goes_on_the_day_it_was_marked(self):
        self.assertEqual(self.by["The Matrix"][0].watched, date(2026, 3, 2))

    def test_two_plays_on_one_day_are_one_entry(self):
        self.assertEqual([(x.watched, x.rewatch, x.rating) for x in self.by["The Matrix"]],
                         [(date(2026, 3, 2), False, None), (date(2026, 3, 9), True, 6)])
        self.assertEqual(self.lines.counts["same_day"], 1)

    def test_plays_plex_counted_before_its_history_make_a_rewatch(self):
        self.assertEqual([x.rewatch for x in self.by["2001: A Space Odyssey"]], [True])

    def test_counted_but_never_logged_is_watched_with_no_date(self):
        sf = self.by["Only Assistant"]
        self.assertEqual([(x.kind, x.watched, x.rating, x.at) for x in sf], [("undated", None, None,
                                                                               ts(2025, 5, 1, 20, 0))])
        self.assertEqual(self.lines.counts["played_undated"], 1)

    def test_a_deleted_film_keeps_the_imdb_id_it_was_logged_under(self):
        gone = self.by["Gone Legacy"][0]
        self.assertEqual((gone.key, gone.imdb, gone.tmdb, gone.year, gone.kind), (None, "tt7654321", "", 1988, "diary"))
        gone = self.by["Gone TMDB"][0]                                  # (an old TMDB agent's GUID)
        self.assertEqual((gone.key, gone.imdb, gone.tmdb, gone.year), (None, "", "5555", None))

    def test_a_film_with_no_imdb_id_goes_by_its_tmdb_id(self):
        hk = self.by["Hong Kong Action"][0]
        self.assertEqual((hk.imdb, hk.tmdb, hk.cells()["tmdbID"]), ("", "4321", "4321"))
        matrix = self.by["The Matrix"][0]                               # (it has TMDB 603 too: the IMDb ID does)
        self.assertEqual((matrix.imdb, matrix.tmdb, matrix.cells()["tmdbID"]), ("tt0133093", "", ""))
        c = self.lines.counts
        self.assertEqual((c["by_tmdb"], c["no_imdb"]), (2, 2))          # (by title: Only Assistant, the 1912 film)
        self.assertIn("2 films with no IMDb ID matched by title", L.summary(self.lines))
        row = next(r for r in read_back(L.csv_texts(self.lines)[0]) if r[2] == "Hong Kong Action")
        self.assertEqual(row[:4], ["", "4321", "Hong Kong Action", "1985"])

    def test_an_average_rating_is_rounded_half_up(self):
        self.assertEqual(self.by["=Formula Looking Title"][0].rating, 8)
        self.assertEqual(self.lines.counts["ratings_rounded"], 1)
        self.assertEqual([L.rating10(v) for v in (None, 0, 0.4, 1, 6.4, 6.5, 7.5, 10, 11, "x", float("nan"))],
                         [None, None, None, 1, 6, 7, 8, 10, 10, None, None])

    def test_a_rating_changed_since_goes_in_on_its_own(self):
        lines = L.rows(self.catalog, {"since": "2026-03-20"})
        self.assertEqual([(x.title, x.kind, x.rating) for x in lines], [("The Matrix", "undated", 6)])
        self.assertEqual(lines.counts["ratings_changed"], 1)
        self.assertEqual(L.since_time(self.lines.covered_to), ts(2026, 4, 1, 9, 0))   # (the rating is the newest)
        self.assertEqual(L.rows(self.catalog, {"since": self.lines.covered_to}), [])
        # without ratings there's nothing new to say about it
        self.assertEqual(L.rows(self.catalog, {"since": "2026-03-20", "ratings": False}), [])
        # a dateless film counts as new when it was marked (or rated) since
        lines = L.rows(self.catalog, {"since": "2025-04-30"})
        self.assertIn(("Only Assistant", "undated"), [(x.title, x.kind) for x in lines])
        self.assertNotIn("=Formula Looking Title", [x.title for x in lines])       # (rated in 2024)

    def test_rated_at_reads_plex_last_rated_at(self):
        self.assertEqual(L.rated_at(self.catalog, 1), {MATRIX: ts(2026, 4, 1, 9, 0), OLD: ts(2024, 1, 1, 9, 0)})


class ValuesTest(unittest.TestCase):
    def test_a_cell_as_letterboxd_reads_it(self):
        self.assertEqual(L.cell("Alien"), "Alien")
        self.assertEqual(L.cell(1979), "1979")
        self.assertEqual(L.cell(None), "")
        self.assertEqual(L.cell("Paris, Texas"), '"Paris, Texas"')
        self.assertEqual(L.cell('"Crocodile" Dundee'), '"\\"Crocodile\\" Dundee"')
        self.assertEqual(L.cell("back\\slash"), '"back\\\\slash"')
        self.assertEqual(L.cell("  two\r\nlines  "), "two lines")                  # one line, no spaces around
        line = L.Line("diary", "k", 'Say "Hi", Bob', 2001, directors=["A, B", "C"], watched=date(2026, 1, 2),
                      rewatch=True, rating=7, tags=["Movies", "Films"])
        text = L.csv_texts(L.Rows([line]))[0]
        self.assertEqual(read_back(text)[1], ["", "", 'Say "Hi", Bob', "2001", "A, B, C", "2026-01-02", "true", "7",
                                              "Movies, Films"])
        # a TMDB ID goes in only for a film with no IMDb ID (Letterboxd matches either exactly)
        self.assertEqual(L.Line("undated", "k", "A", 2001, tmdb="603").cells()["tmdbID"], "603")
        self.assertEqual(L.Line("undated", "k", "A", 2001, imdb="tt0133093", tmdb="603").cells()["tmdbID"], "")
        self.assertEqual([L._tmdb_id(v) for v in ("603", " 603 ", "tv/603", "", None, "12a")],
                         ["603", "603", "", "", "", ""])
        self.assertEqual(L._tag(" Movies, Asia "), "Movies Asia")

    def test_since_and_times(self):
        self.assertEqual(L.since_time("2026-09-23"), datetime(2026, 9, 23).timestamp())
        self.assertEqual(L.since_time("2026-09-23T21:30"), datetime(2026, 9, 23, 21, 30).timestamp())
        self.assertEqual(L.since_time("2026-09-23T21:30:00+00:00"),
                         datetime(2026, 9, 23, 21, 30, tzinfo=timezone.utc).timestamp())
        self.assertIsNone(L.since_time(None))
        self.assertIsNone(L.since_time(""))
        for bad in ("soon", "2026-13-01", 1790000000, True, ["2026-01-01"]):
            with self.assertRaises(ValueError, msg=repr(bad)):
                L.since_time(bad)
        for moment in (1790158783, 1741500000, 1762074000):            # (the latter two: either side of DST)
            self.assertEqual(L.since_time(L.iso_time(moment)), moment)
        self.assertIsNone(L.iso_time(None))
        self.assertEqual(L.latest(None, "", "2026-01-01", L.iso_time(ts(2026, 3, 1, 9, 0)), "2025-12-31"),
                         L.iso_time(ts(2026, 3, 1, 9, 0)))
        self.assertIsNone(L.latest(None, ""))

    def test_file_name(self):
        self.assertEqual(L.file_name(r"C:\x\com.plexapp.plugins.library.db-2026-09-25"),
                         "Projectionist Letterboxd 2026-09-25.csv")
        self.assertEqual(L.file_name(None), "Projectionist Letterboxd.csv")

    def test_saved_words(self):
        lines = L.Rows([L.Line("diary", "k", "A", 2000)], counts={"diary": 212, "rated_undated": 64, "no_imdb": 3})
        self.assertEqual(L.saved_words(lines, ["a.csv"]), "Saved for Letterboxd: 212 diary entries, 64 rated films "
                                                          "without a date, 3 films with no IMDb ID matched by title.")
        self.assertIn(", in 2 files", L.saved_words(lines, ["a.csv", "a (2).csv"]))
        lines.since = "2026-09-20T21:05:12+01:00"
        self.assertIn("(new since ", L.saved_words(lines, ["a.csv"]))
        self.assertIn("2026", L.saved_words(lines, ["a.csv"]))
        self.assertTrue(L.saved_words(lines, []).startswith("Nothing new for Letterboxd since "))
        self.assertTrue(L.saved_words(L.Rows(), []).startswith("Nothing to save for Letterboxd"))
        self.assertEqual(L.summary(L.Rows(counts={"diary": 1, "played_undated": 1, "no_imdb": 1})),
                         "1 diary entry, 1 played film without a date, 1 film with no IMDb ID matched by title")


class SettingsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from projectionist import gui  # noqa: F401  (its sections: Export)

    def test_the_letterboxd_section(self):
        names = [s.name for s in prefs.sections()]
        self.assertLess(names.index("Export"), names.index("Letterboxd"))
        self.assertLess(names.index("Letterboxd"), names.index("Your collection"))
        shown = [p.key for p in prefs.shown("Letterboxd")]
        self.assertEqual(shown, ["letterboxd_diary", "letterboxd_undated", "letterboxd_ratings", "letterboxd_tags",
                                 "letterboxd_only_new", "letterboxd_leave_out"])
        for key in shown:
            self.assertEqual(prefs.pref(key).kind, "bool")
            self.assertTrue(prefs.pref(key).help, key)
        self.assertEqual({key: prefs.get({}, key) for key in shown},
                         {"letterboxd_diary": True, "letterboxd_undated": True, "letterboxd_ratings": True,
                          "letterboxd_tags": False, "letterboxd_only_new": True, "letterboxd_leave_out": False})
        for key in ("letterboxd_since", "letterboxd_scope", "letterboxd_dir"):
            self.assertFalse(prefs.pref(key).shown, key)
        self.assertEqual(prefs.get({"letterboxd_since": "whenever"}, "letterboxd_since"), "")   # (unusable: none)
        self.assertEqual(prefs.get({"letterboxd_since": "2026-09-23"}, "letterboxd_since"), "2026-09-23")
        for bad in ("{", "[1]", 5, '"x"'):                                  # (unusable: not known)
            self.assertEqual(prefs.get({"letterboxd_scope": bad}, "letterboxd_scope"), "", bad)
            self.assertIsNone(L.last_scope({"letterboxd_scope": bad}))

    def test_what_puts_more_in_than_the_last_export(self):
        base = L.scope(L.options({"ratings": False, "leave_out_libraries": ["Classics", "Asia"]}))
        self.assertEqual(base, {"diary": True, "undated": True, "ratings": False, "library_tags": False,
                                "leave_out": ["asia", "classics"]})
        same_or_less = [{"ratings": False, "leave_out_libraries": ["classics", "ASIA"]},
                        {"ratings": False, "undated": False, "leave_out_libraries": ["Classics", "Asia", "Kids"]},
                        {"ratings": False, "diary": False, "leave_out_libraries": ["Classics", "Asia"]}]
        more = [{"leave_out_libraries": ["Classics", "Asia"]},                       # ratings on
                {"ratings": False, "leave_out_libraries": ["Classics"]},              # Asia back in
                {"ratings": False, "library_tags": True, "leave_out_libraries": ["Classics", "Asia"]}]
        for request in same_or_less:
            self.assertFalse(L.wider(L.scope(L.options(request)), base), request)
        for request in more:
            self.assertTrue(L.wider(L.scope(L.options(request)), base), request)
        self.assertFalse(L.wider(L.scope(L.options({})), None))             # (the last one's not known)
        self.assertFalse(L.wider(L.scope(L.options({})), {"leave_out": "x"}))

    def test_the_last_exports_scope_is_remembered(self):
        settings = {"letterboxd_since": "2026-09-20T21:05:12+01:00"}
        self.assertEqual(L.options_for(settings)["since"], "2026-09-20T21:05:12+01:00")   # (scope not known)
        narrow = L.scope(L.options({"ratings": False}))
        L.remember(settings, L.Rows(covered_to="2026-09-21T10:00:00+01:00", scope=narrow))
        self.assertEqual(L.last_scope(settings), narrow)
        settings["letterboxd_ratings"] = False
        self.assertEqual(L.options_for(settings)["since"], "2026-09-21T10:00:00+01:00")
        self.assertIn("since the last file", L.hint(settings))
        settings["letterboxd_ratings"] = True                                # ratings back on: everything
        self.assertEqual(L.options_for(settings), {"diary": True, "undated": True, "ratings": True,
                                                   "library_tags": False, "since": None, "leave_out_libraries": [],
                                                   "wider": True})
        self.assertTrue(L.hint(settings).startswith("All your plays and ratings this time"), L.hint(settings))
        settings["letterboxd_only_new"] = False                              # (everything anyway: nothing to say)
        self.assertNotIn("wider", L.options_for(settings))
        # an older backup's file doesn't change what the next one is measured against
        L.remember(settings, L.Rows(covered_to="2025-01-01T00:00:00+00:00", scope=L.scope(L.options({}))))
        self.assertEqual(L.last_scope(settings), narrow)
        self.assertEqual(settings["letterboxd_since"], "2026-09-21T10:00:00+01:00")

    def test_the_request_the_settings_make(self):
        self.assertEqual(L.options_for({}, ["Classics"]),
                         {"diary": True, "undated": True, "ratings": True, "library_tags": False, "since": None,
                          "leave_out_libraries": []})
        settings = {"letterboxd_since": "2026-09-20T21:05:12+01:00", "letterboxd_leave_out": True,
                    "letterboxd_tags": True, "letterboxd_ratings": False}
        self.assertEqual(L.options_for(settings, ["Classics"]),
                         {"diary": True, "undated": True, "ratings": False, "library_tags": True,
                          "since": "2026-09-20T21:05:12+01:00", "leave_out_libraries": ["Classics"]})
        settings["letterboxd_only_new"] = False
        self.assertIsNone(L.options_for(settings)["since"])
        self.assertIn("since the last file", L.hint({"letterboxd_since": "2026-09-23"}))
        self.assertNotIn("since", L.hint({"letterboxd_since": "2026-09-23", "letterboxd_only_new": False}))
        self.assertIn("Settings > Letterboxd", L.hint({}))

    def test_remember_never_goes_back(self):
        class App:
            settings = {}
        app = App()
        L.remember(app, L.Rows(covered_to="2026-09-20T21:05:12+01:00"))
        self.assertEqual(app.settings["letterboxd_since"], "2026-09-20T21:05:12+01:00")
        L.remember(app, L.Rows(covered_to="2025-01-01T00:00:00+00:00"))          # (an older backup)
        self.assertEqual(app.settings["letterboxd_since"], "2026-09-20T21:05:12+01:00")
        L.remember(app, L.Rows())                                                   # (nothing read)
        self.assertEqual(app.settings["letterboxd_since"], "2026-09-20T21:05:12+01:00")


# ---------------------------------------------------------------------------------------------------------
# The Export tab's button, in the real window (withdrawn: never shown)
# ---------------------------------------------------------------------------------------------------------
class ExportButtonTest(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from tkinter import filedialog, messagebox
        try:
            self.root = tk.Tk()
            self.root.withdraw()                  # headless: never shown
        except Exception as exc:                  # noqa: BLE001
            self.skipTest(f"Tk unavailable: {exc}")
        from projectionist import gui
        from projectionist.ui import paint
        self.gui = gui
        self.shown, self.opened, self.answers = [], [], []
        self.stubs = [
            mock.patch.object(paint.Interaction, "_choose", lambda *a, **k: None),
            mock.patch.object(tk.Menu, "tk_popup", lambda *a, **k: None),
            mock.patch.object(tk.Menu, "post", lambda *a, **k: None),
            mock.patch.object(messagebox, "showerror", lambda *a, **k: self.shown.append(("error",) + a)),
            mock.patch.object(messagebox, "showwarning", lambda *a, **k: self.shown.append(("warning",) + a)),
            mock.patch.object(messagebox, "showinfo", lambda *a, **k: self.shown.append(("info",) + a)),
            mock.patch.object(messagebox, "askyesno", lambda *a, **k: True),
            mock.patch.object(filedialog, "askopenfilename", lambda *a, **k: ""),
            mock.patch.object(filedialog, "askdirectory", lambda *a, **k: ""),
            mock.patch.object(filedialog, "asksaveasfilename", self._save_dialog),
            mock.patch.object(gui, "open_spreadsheet", lambda path, program: self.opened.append((path, program))),
            mock.patch.object(gui, "_open_path", lambda *a, **k: None),
        ]
        for s in self.stubs:
            s.start()
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.db = make_db(self.dir, STORY, ratings=STORY_RATINGS)
        self.out = os.path.join(self.dir, "saved")
        os.makedirs(self.out)
        s = mock.patch.multiple(gui, SETTINGS_FILE=os.path.join(self.dir, "settings.json"), SETTINGS_DIR=self.dir)
        s.start()
        self.stubs.append(s)
        self.app = None

    def _save_dialog(self, *args, **kwargs):
        self.shown.append(("dialog", kwargs))
        return self.answers.pop(0) if self.answers else ""

    def tearDown(self):
        if self.app is not None:
            try:
                self.app.shutdown()
            except Exception:                     # noqa: BLE001
                pass
        for s in reversed(self.stubs):
            s.stop()
        try:
            self.root.destroy()
        except Exception:                         # noqa: BLE001
            pass
        self.app = self.root = None
        gc.collect()                              # (Tk freed here, on this thread)
        from projectionist.ui import theme as T
        T.use("light")
        self.tmp.cleanup()

    def open_app(self, settings=None, load_db=True):
        if settings is not None:
            with open(self.gui.SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(settings, f)
        self.app = self.gui.App(self.root, None)
        self.root.after_cancel(self.app._timers.pop("pick"))       # (the database is loaded here, by hand)
        if load_db:
            self.app.load_db(self.db)
            self.settle()
        return self.app

    def pump(self, until, timeout=60):
        deadline = time.time() + timeout
        while not until():
            self.root.update()
            time.sleep(0.005)
            self.assertLess(time.time(), deadline, "timed out")

    def settle(self):
        self.pump(lambda: self.app.catalog_state in ("ready", "error"))
        self.pump(lambda: not self.app._held and self.app.queue.empty())
        self.root.update()

    def saved(self):
        with open(self.gui.SETTINGS_FILE, encoding="utf-8") as f:
            return json.load(f)

    def test_export_for_letterboxd(self):
        app = self.open_app()
        self.assertEqual(str(app.letterboxd_btn.cget("text")), "Export for Letterboxd...")
        self.assertIn("Settings > Letterboxd", app.letterboxd_var.get())
        path = os.path.join(self.out, "Diary.csv")
        self.answers.append(path)
        app.export_letterboxd()
        self.settle()
        dialog = [kw for kind, kw in (x for x in self.shown if x[0] == "dialog")]
        self.assertEqual(len(dialog), 1)
        self.assertEqual(dialog[0]["initialfile"], "Projectionist Letterboxd 2026-09-25.csv")
        self.assertEqual(dialog[0]["defaultextension"], ".csv")
        self.assertEqual(os.path.normcase(dialog[0]["initialdir"]), os.path.normcase(self.dir))   # (the database's)
        with open(path, encoding="utf-8", newline="") as f:
            self.assertEqual(f.read(), L.csv_texts(L.rows(app.catalog, {}))[0])
        words = ("Saved for Letterboxd: 10 diary entries, 1 rated film without a date, 4 films with no IMDb ID "
                 "matched by title.")
        self.assertEqual(app.letterboxd_var.get(), words)
        self.assertEqual(str(app.letterboxd_label.cget("style")), "Hint.TLabel")
        self.assertEqual(app.app_status_var.get(), words)
        self.assertIn(path, app.log.get("1.0", "end"))
        saved = self.saved()
        self.assertEqual(L.since_time(saved["letterboxd_since"]), ts(2026, 2, 10, 20, 0))
        self.assertEqual(json.loads(saved["letterboxd_scope"]), {"diary": True, "undated": True, "ratings": True,
                                                                 "library_tags": False, "leave_out": []})
        self.assertEqual(os.path.normcase(saved["letterboxd_dir"]), os.path.normcase(self.out))
        self.assertEqual([p for p, _program in self.opened], [path])        # (Open when finished is on)
        self.assertEqual(self.shown[1:], [])                                 # (no message box)

        # again: nothing new since, so no file - and the save box starts in the folder saved in last
        os.remove(path)
        self.answers.append(path)
        app.export_letterboxd()
        self.settle()
        self.assertEqual(os.path.normcase(self.shown[-1][1]["initialdir"]), os.path.normcase(self.out))
        self.assertFalse(os.path.exists(path))
        self.assertTrue(app.letterboxd_var.get().startswith("Nothing new for Letterboxd since "), app.letterboxd_var.get())
        self.assertEqual(len(self.opened), 1)

        # everything again, with 'only what's new' off (Settings > Letterboxd): the hint follows the setting
        prefs.set(app, "letterboxd_only_new", False)
        self.assertNotIn("since", app.letterboxd_var.get())
        prefs.set(app, "open_when_done", False)
        self.answers.append(path)
        app.export_letterboxd()
        self.settle()
        self.assertTrue(os.path.exists(path))
        self.assertTrue(app.letterboxd_var.get().startswith("Saved for Letterboxd: 10 diary entries"))
        self.assertEqual(len(self.opened), 1)                                # (not opened this time)

    def test_the_save_box_cancelled_and_no_collection_yet(self):
        app = self.open_app(load_db=False)
        app.export_letterboxd()
        self.assertEqual(app.letterboxd_var.get(), "Choose a Plex database first.")
        self.assertEqual(str(app.letterboxd_label.cget("style")), "Bad.TLabel")
        self.assertEqual(self.shown, [])                                     # (no save box)
        app.load_db(self.db)
        self.settle()
        app.export_letterboxd()                                              # cancelled: nothing happens
        self.settle()
        self.assertEqual(len(self.shown), 1)
        self.assertEqual(os.listdir(self.out), [])
        self.assertNotIn("letterboxd_since", self.saved())

    def test_unticked_libraries_left_out_when_asked_and_a_failed_save(self):
        app = self.open_app({"letterboxd_leave_out": True, "open_when_done": False})
        classics = next(lib.id for lib in app.libraries if lib.name == "Classics")
        app.lib_vars[classics].set(False)
        path = os.path.join(self.out, "Diary.csv")
        self.answers.append(path)
        app.export_letterboxd()
        self.settle()
        with open(path, encoding="utf-8") as f:
            self.assertNotIn("Formula Looking Title", f.read())
        self.assertEqual(app.letterboxd_var.get(), "Saved for Letterboxd: 10 diary entries, 3 films with no IMDb ID "
                                                   "matched by title.")
        # a folder that isn't there: the line says it couldn't be saved, and the log has the details
        self.answers.append(os.path.join(self.dir, "gone", "x.csv"))
        prefs.set(app, "letterboxd_only_new", False)
        app.export_letterboxd()
        self.settle()
        self.assertTrue(app.letterboxd_var.get().startswith("Couldn't save the file for Letterboxd: "))
        self.assertEqual(str(app.letterboxd_label.cget("style")), "Bad.TLabel")
        self.assertEqual(self.opened, [])

    def test_ratings_turned_on_after_an_export_the_next_file_has_everything(self):
        app = self.open_app({"letterboxd_ratings": False, "open_when_done": False})
        path = os.path.join(self.out, "Diary.csv")
        self.answers.append(path)
        app.export_letterboxd()
        self.settle()
        with open(path, encoding="utf-8", newline="") as f:
            self.assertEqual(f.readline(), "imdbID,tmdbID,Title,Year,Directors,WatchedDate,Rewatch\r\n")
        prefs.set(app, "letterboxd_ratings", True)                           # (Settings > Letterboxd)
        self.assertTrue(app.letterboxd_var.get().startswith("All your plays and ratings this time"),
                        app.letterboxd_var.get())
        self.answers.append(path)
        app.export_letterboxd()
        self.settle()
        with open(path, encoding="utf-8", newline="") as f:
            self.assertEqual(f.read(), L.csv_texts(L.rows(app.catalog, {}))[0])
        self.assertTrue(app.letterboxd_var.get().endswith(
            "(everything, as Settings > Letterboxd now puts in more than the last file did)."),
            app.letterboxd_var.get())
        self.assertTrue(json.loads(self.saved()["letterboxd_scope"])["ratings"])
        self.assertEqual(self.opened, [])

    def test_the_settings_tab_has_the_letterboxd_card(self):
        app = self.open_app(load_db=False)
        tab = next(t for t in app.tabs if t.title == "Settings")
        self.assertIn("Letterboxd", tab.cards)
        for key in ("letterboxd_diary", "letterboxd_undated", "letterboxd_ratings", "letterboxd_tags",
                    "letterboxd_only_new", "letterboxd_leave_out"):
            self.assertIn(key, tab.controls, key)
        self.assertNotIn("letterboxd_since", tab.controls)


if __name__ == "__main__":
    unittest.main()
