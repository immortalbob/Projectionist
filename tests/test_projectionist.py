"""Tests for Projectionist.

Run with:  python -m unittest discover -s tests -v

The tests build a small synthetic Plex database of their own.
"""

import csv
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from datetime import date, datetime, timedelta
from xml.etree import ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from projectionist import sheets as S  # noqa: E402
from projectionist.export import csv_folder_for, write_csv, write_xlsx  # noqa: E402
from projectionist.extract import (  # noqa: E402
    Cancelled, PlexDBError, audio_format, channel_layout, clean, extract, hdr_label, language_name,
    list_movie_libraries, local_datetime, parse_extra, parse_timestamp, rating_source, resolution_label,
    split_path, utc_date)
from projectionist.files import (  # noqa: E402
    default_output_name, dump_date, find_newest_database, is_candidate_database)

SCHEMA = """
CREATE TABLE library_sections (id INTEGER PRIMARY KEY, name TEXT, section_type INTEGER, agent TEXT, scanner TEXT);
CREATE TABLE section_locations (id INTEGER PRIMARY KEY, library_section_id INTEGER, root_path TEXT);
CREATE TABLE schema_migrations (version TEXT);
CREATE TABLE accounts (id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE devices (id INTEGER PRIMARY KEY, identifier TEXT, name TEXT, platform TEXT);
CREATE TABLE metadata_items (
    id INTEGER PRIMARY KEY, library_section_id INTEGER, parent_id INTEGER, metadata_type INTEGER, guid TEXT,
    title TEXT, title_sort TEXT, original_title TEXT, studio TEXT, rating REAL, tagline TEXT, summary TEXT,
    content_rating TEXT, content_rating_age INTEGER, "index" INTEGER, duration INTEGER, user_thumb_url TEXT,
    user_art_url TEXT, user_fields TEXT, originally_available_at INTEGER, year INTEGER, added_at INTEGER,
    updated_at INTEGER,
    refreshed_at INTEGER, deleted_at INTEGER, extra_data TEXT, audience_rating REAL, edition_title TEXT,
    slug TEXT, user_clear_logo_url TEXT, user_square_art_url TEXT, tags_genre TEXT, tags_director TEXT,
    tags_writer TEXT, tags_star TEXT, tags_country TEXT, tags_collection TEXT);
CREATE TABLE tags (id INTEGER PRIMARY KEY, metadata_item_id INTEGER, tag TEXT, tag_type INTEGER,
    user_thumb_url TEXT, tag_value INTEGER, extra_data TEXT, "key" TEXT);
CREATE TABLE taggings (id INTEGER PRIMARY KEY, metadata_item_id INTEGER, tag_id INTEGER, "index" INTEGER,
    text TEXT, time_offset INTEGER, end_time_offset INTEGER, thumb_url TEXT, extra_data TEXT);
CREATE TABLE media_items (id INTEGER PRIMARY KEY, library_section_id INTEGER, metadata_item_id INTEGER,
    width INTEGER, height INTEGER, size INTEGER, duration INTEGER, bitrate INTEGER, container TEXT,
    video_codec TEXT, audio_codec TEXT, display_aspect_ratio REAL, frames_per_second REAL,
    audio_channels INTEGER, created_at INTEGER, deleted_at INTEGER, extra_data TEXT, color_trc TEXT);
CREATE TABLE media_parts (id INTEGER PRIMARY KEY, media_item_id INTEGER, directory_id INTEGER, hash TEXT,
    open_subtitle_hash TEXT, file TEXT, "index" INTEGER, size INTEGER, duration INTEGER, created_at INTEGER,
    deleted_at INTEGER, extra_data TEXT);
CREATE TABLE media_streams (id INTEGER PRIMARY KEY, stream_type_id INTEGER, media_item_id INTEGER, url TEXT,
    codec TEXT, language TEXT, "index" INTEGER, media_part_id INTEGER, channels INTEGER, bitrate INTEGER,
    "default" INTEGER, forced INTEGER, extra_data TEXT);
CREATE TABLE metadata_item_settings (id INTEGER PRIMARY KEY, account_id INTEGER, guid TEXT, rating REAL,
    view_offset INTEGER, view_count INTEGER, last_viewed_at INTEGER);
CREATE TABLE metadata_item_views (id INTEGER PRIMARY KEY, account_id INTEGER, guid TEXT, metadata_type INTEGER,
    library_section_id INTEGER, title TEXT, originally_available_at INTEGER, viewed_at INTEGER,
    device_id INTEGER);
CREATE TABLE metadata_relations (id INTEGER PRIMARY KEY, metadata_item_id INTEGER,
    related_metadata_item_id INTEGER, relation_type INTEGER);
"""

J = json.dumps
MATRIX_GUID = "plex://movie/5d776827880197001ec90904"


def defaultdict_list(pairs):
    out = {}
    for key, value in pairs:
        out.setdefault(key, []).append(value)
    return out


class Fixture:
    """Builds a small but realistic Plex database."""

    def __init__(self, path, drop_columns=None):
        self.path = path
        self.con = sqlite3.connect(path)
        schema = SCHEMA
        for table, col in (drop_columns or []):
            schema, n = re.subn(rf'(CREATE TABLE {table} \((?:[^;])*?),\s*"?{col}"? TEXT', r"\1", schema)
            assert n == 1, (table, col)
        self.con.executescript(schema)
        self._tag_ids = {}

    def insert(self, table, **values):
        cols = ", ".join(f'"{c}"' for c in values)
        existing = {r[1] for r in self.con.execute(f"PRAGMA table_info({table})")}
        values = {k: v for k, v in values.items() if k in existing}
        cols = ", ".join(f'"{c}"' for c in values)
        cur = self.con.execute(f"INSERT INTO {table} ({cols}) VALUES ({','.join('?' * len(values))})",
                               list(values.values()))
        return cur.lastrowid

    def tag(self, movie_id, tag_type, tag, index=0, text=None, key=None, photo=None, tag_extra=None,
            extra=None, start=None, end=None, thumb=None):
        k = (tag_type, tag, key)
        if k not in self._tag_ids:
            self._tag_ids[k] = self.insert("tags", tag=tag, tag_type=tag_type, key=key, user_thumb_url=photo,
                                           extra_data=J(tag_extra) if tag_extra else None)
        self.insert("taggings", metadata_item_id=movie_id, tag_id=self._tag_ids[k], index=index, text=text,
                    time_offset=start, end_time_offset=end, thumb_url=thumb, extra_data=J(extra) if extra else None)

    def close(self):
        self.con.commit()
        self.con.close()


def build_fixture(path, drop_columns=None):
    f = Fixture(path, drop_columns)
    f.insert("library_sections", id=1, name="Movies", section_type=1, agent="tv.plex.agents.movie")
    f.insert("library_sections", id=2, name="Classics", section_type=1)
    f.insert("library_sections", id=5, name="Series", section_type=2)
    f.insert("section_locations", library_section_id=1, root_path="/disk1/Movies")
    for version in ("20240101000000", "500000000001.231", "20260901000000"):   # mixed numbering, like Plex's
        f.insert("schema_migrations", version=version)
    f.insert("accounts", id=1, name="Owner")
    f.insert("accounts", id=42, name="friend")
    f.insert("devices", id=7, name="Living Room TV", platform="Roku")

    # --- 1: The Matrix - the fully-populated movie ----------------------------------------
    m = f.insert("metadata_items", id=1799, library_section_id=1, metadata_type=1, guid=MATRIX_GUID,
                 title="The Matrix", title_sort="Matrix", original_title="", studio="Village Roadshow Pictures",
                 rating=8.3, audience_rating=8.5, tagline="Believe the unbelievable.",
                 summary="A hacker\r\nlearns the truth.\x07", content_rating="R", content_rating_age=15,
                 duration=8175712, year=1999, originally_available_at=922838400, added_at=1721426033,
                 updated_at=1788059777, refreshed_at=1788059777, slug="the-matrix", edition_title="",
                 user_fields="lockedFields=9|16",
                 user_thumb_url="metadata://posters/sel", user_art_url="metadata://art/sel",
                 user_clear_logo_url="upload://clearLogos/custom", user_square_art_url="",
                 extra_data=J({"at:ratingImage": "rottentomatoes://image.rating.ripe",
                               "at:audienceRatingImage": "rottentomatoes://image.rating.upright"}))
    f.tag(m, 1, "Science Fiction", index=1)
    f.tag(m, 1, "Action", index=0)
    f.tag(m, 2, "IMDB Top 250", tag_extra={"at:guid": "collection://top250"})
    f.tag(m, 4, "Lana Wachowski", 0, "Director", key="p1")
    f.tag(m, 4, "Lilly Wachowski", 1, "Director", key="p2")
    f.tag(m, 4, "Some Assistant", 2, "Assistant Director", key="p9")
    f.tag(m, 5, "Lana Wachowski", 0, "Screenplay", key="p1")
    f.tag(m, 5, "Lana Wachowski", 1, "Story", key="p1")
    f.tag(m, 7, "Joel Silver", 0, "Producer", key="p3")
    f.tag(m, 7, "Barrie Osborne", 1, "Executive Producer", key="p4")
    f.tag(m, 7, "Mali Finn", 2, "Casting", key="p5")
    f.tag(m, 6, "Laurence Fishburne", 1, "Morpheus", key="p7", photo="https://img/lf.jpg")
    f.tag(m, 6, "Keanu Reeves", 0, "Neo", key="p6", photo="https://img/kr.jpg")
    f.tag(m, 6, " \tNg Gam-Hung", 2, "")
    f.tag(m, 8, "United States of America")
    for i, name in enumerate(["Opening", "", "Finale"]):
        f.tag(m, 9, name, index=i + 1, start=i * 61500, end=(i + 1) * 61500)
    f.tag(m, 10, "Nora Clark", 0, "Clever enough.", extra={"at:image": "rottentomatoes://image.review.fresh",
                                                            "at:source": "USA Today", "at:link": "https://x/r"})
    # Plex's credits markers: the main-on-end titles, a 50-second mid-credits scene, then the final crawl.
    f.tag(m, 12, "", 0, "credits", start=7678819, end=7712819, extra={"pv:version": "5"})
    f.tag(m, 12, "", 1, "credits", start=7762819, end=8178688, extra={"pv:final": "1", "pv:version": "5"})
    f.tag(m, 312, "Thumb", 0, "https://img/wrong-poster.jpg", thumb="metadata://posters/other")
    f.tag(m, 312, "Thumb", 1, "https://img/poster.jpg", thumb="metadata://posters/sel")
    f.tag(m, 313, "Art", 0, "https://img/art.jpg", thumb="metadata://art/sel")
    f.tag(m, 323, "ClearLogo", 0, "https://img/logo.png", thumb="metadata://clearLogos/x")
    f.tag(m, 314, "imdb://tt0133093", 0)
    f.tag(m, 314, "tmdb://603", 1)
    f.tag(m, 314, "tvdb://169", 2)
    f.tag(m, 314, "letterboxd://the-matrix", 3)
    f.tag(m, 316, "imdb://image.rating", 0, "8.7", tag_extra={"at:type": "audience"})
    f.tag(m, 316, "rottentomatoes://image.rating.ripe", 1, "8.3", tag_extra={"at:type": "critic"})
    f.tag(m, 316, "rottentomatoes://image.rating.upright", 2, "8.5", tag_extra={"at:type": "audience"})
    f.tag(m, 316, "themoviedb://image.rating", 3, "8.2", tag_extra={"at:type": "audience"})
    f.tag(m, 316, "metacritic://image.rating", 4, "7.3", tag_extra={"at:type": "critic"})
    f.tag(m, 318, "Village Roadshow Pictures", 0)
    f.tag(m, 318, "Silver Pictures", 1)
    f.tag(m, 324, "", 0, "Intense sci-fi thriller has violence.",
          extra={"pv:ageRatingAge": "15", "pv:ageRatingRating": "4", "pv:ageRatingType": "official"})
    f.tag(m, 300, "Tense", 0)
    mi = f.insert("media_items", id=4112, library_section_id=1, metadata_item_id=m, width=3840, height=1600,
                  size=12526469002, duration=8178688, bitrate=12252790, container="mkv", video_codec="hevc",
                  audio_codec="truehd", display_aspect_ratio=2.4000000953674316,
                  frames_per_second=23.976024627685547, audio_channels=8, created_at=1721426033,
                  color_trc="smpte2084", extra_data=J({"ma:videoProfile": "main 10"}))
    mp = f.insert("media_parts", id=4112, media_item_id=mi, file="/disk1/Movies/Matrix, The (1999)/The.Matrix.mkv",
                  size=12526469002, duration=8178688, hash="abc", open_subtitle_hash="6b21", index=0,
                  created_at=1721426033)
    f.insert("media_streams", stream_type_id=1, media_item_id=mi, media_part_id=mp, codec="hevc", language="en",
             index=0, default=1, forced=0, bitrate=9564790,
             extra_data=J({"ma:DOVIPresent": "1", "ma:DOVIProfile": "8", "ma:DOVIBLCompatID": "1",
                           "ma:bitDepth": "10", "ma:colorTrc": "smpte2084", "ma:width": "3840",
                           "ma:height": "1600", "ma:frameRate": "23.976"}))
    f.insert("media_streams", stream_type_id=2, media_item_id=mi, media_part_id=mp, codec="ac3", language="fra",
             index=2, default=0, forced=0, channels=6, bitrate=448000,
             extra_data=J({"ma:audioChannelLayout": "5.1(side)", "ma:samplingRate": "48000"}))
    f.insert("media_streams", stream_type_id=2, media_item_id=mi, media_part_id=mp, codec="truehd",
             language="en", index=1, default=1, forced=0, channels=8,
             extra_data=J({"ma:profile": "dolby truehd + dolby atmos", "ma:audioChannelLayout": "7.1"}))
    f.insert("media_streams", stream_type_id=3, media_item_id=mi, media_part_id=mp, codec="srt", language="",
             index=None, default=0, forced=1, url="file:///disk1/Movies/Matrix.en.srt")
    f.insert("metadata_item_settings", account_id=42, guid=MATRIX_GUID, view_count=2, last_viewed_at=1776911057,
             rating=9.0)
    f.insert("metadata_item_settings", account_id=1, guid=MATRIX_GUID, view_count=0, view_offset=3723000)
    f.insert("metadata_item_views", account_id=42, guid=MATRIX_GUID, metadata_type=1, library_section_id=1,
             title="The Matrix", viewed_at=1776911057, device_id=7)
    f.insert("metadata_item_views", account_id=1, guid="plex://movie/gone", metadata_type=1,
             library_section_id=1, title="Deleted Movie", originally_available_at=0, viewed_at=1700000000)
    f.insert("metadata_item_views", account_id=1, guid="plex://episode/x", metadata_type=4,
             library_section_id=5, title="An Episode", viewed_at=1700000001)
    trailer = f.insert("metadata_items", metadata_type=12, title="The Matrix Trailer", duration=120000,
                       guid="iva://api.internetvideoarchive.com/2.0/DataService/VideoAssets(1)",
                       extra_data=J({"ex:extraType": "1"}), index=1, originally_available_at=915148800,
                       content_rating="explicit", user_thumb_url="https://img/trailer.jpg")
    f.insert("metadata_relations", metadata_item_id=m, related_metadata_item_id=trailer, relation_type=1)
    f.con.execute("UPDATE metadata_items SET extra_data = ? WHERE id = ?",
                  (J({"at:ratingImage": "rottentomatoes://image.rating.ripe",
                      "at:audienceRatingImage": "rottentomatoes://image.rating.upright",
                      "ex:primaryExtraKey": f"/library/metadata/{trailer}"}), m))
    feat = f.insert("metadata_items", metadata_type=12, title="Making Of", index=2,
                    guid="file:///disk1/Movies/Matrix%2C%20The%20(1999)/Featurettes/Making%20Of-featurette.mkv",
                    extra_data=J({"ex:extraType": "10"}))
    fmi = f.insert("media_items", metadata_item_id=feat, duration=600000)
    f.insert("media_parts", media_item_id=fmi, file="/disk1/Movies/Matrix, The (1999)/Featurettes/Making Of.mkv")
    f.insert("metadata_relations", metadata_item_id=m, related_metadata_item_id=feat, relation_type=10)
    f.insert("metadata_items", library_section_id=1, metadata_type=18, title="IMDB Top 250",
             guid="collection://top250", summary="The best of the best.", extra_data=J({"at:childCount": "1"}))

    # --- 2: pre-1970 movie, two versions, one split over two files -----------------------------
    m2 = f.insert("metadata_items", id=500, library_section_id=2, metadata_type=1, guid="plex://movie/old",
                  title="=Formula Looking Title", title_sort="", year=1912,
                  originally_available_at=-1813795200, duration=None, added_at=1600000000,
                  extra_data="at%3AaudienceRatingImage=imdb%3A%2F%2Fimage.rating")
    v1 = f.insert("media_items", metadata_item_id=m2, width=720, height=480, container="avi",
                  video_codec="mpeg4", duration=3600000, size=0)
    cd1 = f.insert("media_parts", media_item_id=v1, file=r"D:\Old\Movie CD1.avi", size=700_000_000, index=0,
                   duration=1800000)
    cd2 = f.insert("media_parts", media_item_id=v1, file=r"D:\Old\Movie CD2.avi", size=690_000_000, index=1,
                   duration=1800000)
    for part in (cd1, cd2):   # each file of a split movie carries its own copy of the tracks
        f.insert("media_streams", stream_type_id=2, media_item_id=v1, media_part_id=part, codec="mp3",
                 language="en", index=1, default=1, channels=2)
        f.insert("media_streams", stream_type_id=3, media_item_id=v1, media_part_id=part, codec="srt",
                 language="fr", index=2)
    v2 = f.insert("media_items", metadata_item_id=m2, width=1920, height=1080, container="mp4",
                  video_codec="h264", size=4_000_000_000)
    f.insert("media_parts", media_item_id=v2, file=r"D:\Old\Movie 1080p.mp4", size=4_000_000_000)

    # --- 3: legacy-agent movie with only the denormalised tag columns ----------------------------
    f.insert("metadata_items", id=600, library_section_id=1, metadata_type=1,
             guid="com.plexapp.agents.imdb://tt0062622?lang=en", title="2001: A Space Odyssey", year=1968,
             tags_genre="Science Fiction|Adventure", tags_director="Stanley Kubrick", tags_star="Keir Dullea")

    # --- 4: a Hong Kong edition: only an 'Action Director', odd producer credits, SD, owner's favourite -----
    hk = f.insert("metadata_items", id=700, library_section_id=1, metadata_type=1, title="Hong Kong Action",
                  guid="plex://movie/hk1/edition/Extended", edition_title="Extended", year=1985,
                  studio="Golden Harvest Company", duration=5400000)
    f.tag(hk, 4, "Yuen Woo-ping", 0, "Action Director", key="p20")
    f.tag(hk, 4, "Some Assistant", 1, "Assistant Director", key="p9")
    f.tag(hk, 7, "Helper Person", 0, "Producer's Assistant", key="p21")
    f.tag(hk, 7, "Real Producer", 1, "Assistant Producer", key="p22")
    hk_media = f.insert("media_items", metadata_item_id=hk, width=720, height=360, container="mkv",
                        video_codec="h264")
    hk_part = f.insert("media_parts", media_item_id=hk_media, file="/disk1/Movies/HK/hk.mkv", size=1_000_000_000)
    f.insert("media_streams", stream_type_id=2, media_item_id=hk_media, media_part_id=hk_part, codec="ac3",
             language="jpn", index=1, default=1, channels=2,
             extra_data=J({"ma:audioChannelLayout": "2 channels (FC+LFE)"}))
    f.insert("metadata_item_settings", account_id=1, guid="plex://movie/hk1/edition/Extended", view_count=1,
             last_viewed_at=1700000000, rating=8.0)

    # --- 5: sci-fi from 1975 whose only directing credit is an assistant ------------------------------
    old_sf = f.insert("metadata_items", id=710, library_section_id=1, metadata_type=1, title="Only Assistant",
                      guid="plex://movie/sf75", year=1975)
    f.tag(old_sf, 1, "Science Fiction", index=0)
    f.tag(old_sf, 4, "Some Assistant", 0, "Assistant Director", key="p9")

    # --- smart collections (saved filters) -------------------------------------------------------------
    lana = f._tag_ids[(4, "Lana Wachowski", "p1")]
    scifi = f._tag_ids[(1, "Science Fiction", None)]
    uri = "server://abc/com.plexapp.plugins.library/library/sections/{}/all?type=1&sort=titleSort&{}"
    for sec, title, query, count in [
        (1, "Directed by Lana", f"director={lana}", 1),
        (1, "Owner Favourites", "userRating%3E%3E=7", 1),                      # >>= : strictly above 7
        (1, "Old Sci-Fi", f"genre={scifi}&and=1&year%3C%3C=1980", 1),
        (1, "Golden or Extended", "push=1&studio=golden&or=1&editionTitle=extended&pop=1", 1),
        (1, "Unwatched", "unwatched=1", 3),
        (1, "SD Films", "resolution=sd", 1),
        (1, "Recently Added", "addedAt%3E%3E=-30d", 2),                        # relative date: not evaluated
        (1, "Wrong Count", f"director={lana}", 5),                            # Plex disagrees: not expanded
        (2, "Before 2000", "year%3C%3C=2000", 1),
    ]:
        f.insert("metadata_items", library_section_id=sec, metadata_type=18, title=title,
                 guid=f"collection://{title.lower().replace(' ', '-')}",
                 extra_data=J({"at:smart": "1", "at:childCount": str(count), "pv:uri": uri.format(sec, query)}))

    # --- not movies: a TV show in a TV library, and a movie in the TV library's id space ---------
    f.insert("metadata_items", library_section_id=5, metadata_type=2, title="Some Show", guid="plex://show/1")
    f.close()


class FixtureTestCase(unittest.TestCase):
    drop_columns = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = os.path.join(cls.tmp.name, "com.plexapp.plugins.library.db-2026-09-25")
        build_fixture(cls.db, cls.drop_columns)
        cls.result = extract(cls.db)
        cls.movies = {r["plex_id"]: r for r in cls.result.sheet(S.MOVIES).rows}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def rows(self, sheet, plex_id=None):
        rows = self.result.sheet(sheet).rows
        return [r for r in rows if plex_id is None or r.get("plex_id") == plex_id]


class ExtractTests(FixtureTestCase):
    def test_only_movies_from_movie_libraries(self):
        self.assertEqual(set(self.movies), {1799, 500, 600, 700, 710})
        self.assertEqual(self.result.movie_count, 5)
        self.assertEqual([lib.name for lib in self.result.libraries], ["Classics", "Movies"])

    def test_sorted_by_library_then_sort_title(self):
        order = [r["plex_id"] for r in self.result.sheet(S.MOVIES).rows]
        self.assertEqual(order, [500, 600, 700, 1799, 710])   # Classics first, then by sort title

    def test_identity_fields(self):
        m = self.movies[1799]
        self.assertEqual(m["title"], "The Matrix")
        self.assertEqual(m["library"], "Movies")
        self.assertEqual(m["release_date"], date(1999, 3, 31))
        self.assertEqual(m["runtime_min"], 136)
        self.assertEqual(m["summary"], "A hacker\nlearns the truth.")   # CRLF normalised, BEL removed
        self.assertEqual(m["content_advisory"], "Intense sci-fi thriller has violence.")
        self.assertEqual(m["genres"], "Action; Science Fiction")          # Plex's index order
        self.assertEqual(m["production_companies"], "Village Roadshow Pictures; Silver Pictures")
        self.assertEqual(m["collections"], "IMDB Top 250; Directed by Lana; Unwatched")   # regular, then smart
        self.assertEqual(m["other_tags"], "Mood: Tense")

    def test_people(self):
        m = self.movies[1799]
        self.assertEqual(m["directors"], "Lana Wachowski; Lilly Wachowski")   # assistant director left out
        self.assertEqual(m["writers"], "Lana Wachowski")                     # de-duplicated
        self.assertEqual(m["producers"], "Joel Silver; Barrie Osborne")       # casting left out
        self.assertEqual(m["cast"], "Keanu Reeves; Laurence Fishburne; Ng Gam-Hung")
        self.assertEqual(m["cast_characters"], "Keanu Reeves (Neo); Laurence Fishburne (Morpheus); Ng Gam-Hung")
        self.assertEqual(m["cast_count"], 3)
        cast = self.rows(S.CAST, 1799)
        self.assertEqual([(c["order"], c["actor"], c["character"]) for c in cast],
                         [(1, "Keanu Reeves", "Neo"), (2, "Laurence Fishburne", "Morpheus"), (3, "Ng Gam-Hung", "")])
        self.assertEqual(cast[0]["person_id"], "p6")
        self.assertEqual(cast[0]["photo_url"], "https://img/kr.jpg")
        crew = self.rows(S.CREW, 1799)
        self.assertIn(("Directing", "Some Assistant", "Assistant Director"),
                      [(c["department"], c["name"], c["job"]) for c in crew])
        self.assertIn(("Production", "Mali Finn", "Casting"), [(c["department"], c["name"], c["job"]) for c in crew])
        self.assertEqual(len(crew), 8)

    def test_ratings(self):
        m = self.movies[1799]
        self.assertEqual(m["critic_rating"], 8.3)
        self.assertEqual(m["critic_rating_source"], "Rotten Tomatoes (Fresh)")
        self.assertEqual(m["audience_rating_source"], "Rotten Tomatoes (Upright)")
        self.assertEqual(m["imdb_rating"], 8.7)
        self.assertEqual((m["rt_critic"], m["rt_critic_verdict"]), (83, "Fresh"))
        self.assertEqual((m["rt_audience"], m["rt_audience_verdict"]), (85, "Upright"))
        self.assertEqual(m["tmdb_rating"], 8.2)
        self.assertEqual(m["other_ratings"], "metacritic (Critic): 7.3")
        self.assertEqual(len(self.rows(S.RATINGS, 1799)), 5)
        review = self.rows(S.REVIEWS, 1799)[0]
        self.assertEqual((review["critic"], review["publication"], review["verdict"]), ("Nora Clark", "USA Today", "Fresh"))
        # extra_data in the older URL-encoded format
        self.assertEqual(self.movies[500]["audience_rating_source"], "IMDb")

    def test_ids_and_links(self):
        m = self.movies[1799]
        self.assertEqual((m["imdb_id"], m["tmdb_id"], m["tvdb_id"]), ("tt0133093", "603", "169"))
        self.assertEqual(m["other_ids"], "letterboxd:the-matrix")
        self.assertEqual(m["imdb_url"], "https://www.imdb.com/title/tt0133093/")
        self.assertEqual(m["tmdb_url"], "https://www.themoviedb.org/movie/603")
        self.assertEqual(m["plex_url"], "https://watch.plex.tv/movie/the-matrix")
        legacy = self.movies[600]
        self.assertEqual(legacy["imdb_id"], "tt0062622")
        self.assertEqual(legacy["plex_url"], "")

    def test_legacy_denormalised_tags_fallback(self):
        legacy = self.movies[600]
        self.assertEqual(legacy["genres"], "Science Fiction; Adventure")
        self.assertEqual(legacy["directors"], "Stanley Kubrick")
        self.assertEqual(legacy["cast"], "Keir Dullea")

    def test_artwork_uses_selected_image(self):
        m = self.movies[1799]
        self.assertEqual(m["poster_url"], "https://img/poster.jpg")
        self.assertEqual(m["art_url"], "https://img/art.jpg")
        self.assertEqual(m["logo_url"], "")   # custom upload has no web address

    def test_media(self):
        m = self.movies[1799]
        self.assertEqual(m["resolution"], "4K")
        self.assertEqual(m["hdr"], "Dolby Vision P8.1 / HDR10")
        self.assertEqual(m["audio"], "Dolby TrueHD Atmos 7.1")    # the default track, not the first
        self.assertEqual(m["audio_languages"], "English; French")  # ordered by track index
        self.assertEqual(m["subtitle_languages"], "Unknown")
        self.assertEqual(m["aspect_ratio"], 2.4)
        self.assertEqual(m["frame_rate"], 23.976)
        self.assertEqual(m["bitrate_mbps"], 12.3)
        self.assertEqual(m["file_name"], "The.Matrix.mkv")
        self.assertEqual(m["folder"], "/disk1/Movies/Matrix, The (1999)")
        self.assertEqual(m["file_size_gb"], 12.53)
        streams = self.rows(S.STREAMS, 1799)
        self.assertEqual([s["stream_type"] for s in streams], ["Video", "Audio", "Audio", "Subtitle"])
        self.assertEqual(streams[-1]["external"], "Yes")
        self.assertEqual(streams[-1]["forced"], "Yes")
        self.assertEqual(streams[0]["format"], "HEVC 4K Dolby Vision P8.1 / HDR10")

    def test_multiple_versions_and_parts(self):
        m = self.movies[500]
        self.assertEqual(m["versions"], 2)
        self.assertEqual(m["parts"], 2)
        self.assertEqual(m["file_path"], r"D:\Old\Movie CD1.avi | D:\Old\Movie CD2.avi")
        self.assertEqual(m["folder"], r"D:\Old")
        self.assertEqual(m["file_size_gb"], 1.39)
        self.assertEqual(m["total_size_gb"], 5.39)
        self.assertEqual(m["runtime_min"], 60)   # no metadata duration -> first version's length
        files = self.rows(S.FILES, 500)
        self.assertEqual([(f["version"], f["part"]) for f in files], [(1, 1), (1, 2), (2, 1)])
        self.assertEqual(files[2]["resolution"], "1080p")

    def test_pre_1970_dates(self):
        m = self.movies[500]
        self.assertEqual(m["release_date"], date(1912, 7, 11))
        self.assertEqual(m["title"], "=Formula Looking Title")

    def test_viewing(self):
        m = self.movies[1799]
        self.assertEqual(m["owner_plays"], 0)
        self.assertEqual(m["owner_resume_at"], "1:02:03")
        self.assertEqual(m["total_plays"], 2)
        self.assertEqual(m["watched_by"], "friend")
        self.assertIsInstance(m["last_played"], datetime)
        status = self.rows(S.WATCH_STATUS, 1799)
        self.assertEqual([(s["user"], s["plays"], s["rating"]) for s in status],
                         [("friend", 2, 9.0), ("Owner", 0, None)])
        history = self.result.sheet(S.WATCH_HISTORY).rows
        self.assertEqual(len(history), 2)          # the TV episode is excluded
        self.assertEqual(history[0]["title"], "Deleted Movie")
        self.assertIsNone(history[0]["plex_id"])
        self.assertEqual((history[1]["plex_id"], history[1]["device"]), (1799, "Living Room TV"))

    def test_extras_markers_collections(self):
        m = self.movies[1799]
        self.assertEqual(m["extras"], "Trailer x1; Featurette x1")
        self.assertEqual(m["markers"], "Credits 2:07:58-2:08:32; Credits 2:09:22-2:16:18")
        self.assertEqual(m["chapters"], 3)
        extras = self.rows(S.EXTRAS, 1799)
        self.assertEqual(extras[0]["source"], "Online")
        self.assertEqual(extras[0]["duration_min"], 2.0)
        self.assertEqual(extras[1]["source"], "Local file")
        self.assertEqual(extras[1]["location"], "/disk1/Movies/Matrix, The (1999)/Featurettes/Making Of.mkv")
        self.assertEqual(extras[1]["duration_min"], 10.0)
        coll = [c for c in self.rows(S.COLLECTIONS, 1799) if c["collection_type"] == "Regular"]
        self.assertEqual([(c["collection"], c["collection_summary"]) for c in coll],
                         [("IMDB Top 250", "The best of the best.")])

    def test_credits_scenes(self):
        m = self.movies[1799]
        self.assertEqual(m["stay_after_credits"], "Yes")
        self.assertEqual(m["credits_scenes"], 1)
        self.assertEqual(m["credits_scene_times"], "2:08:32 mid-credits (50 s)")
        self.assertEqual((m["credits_start"], m["runtime_before_credits_min"]), ("2:07:58", 128))
        rows = self.rows(S.CREDITS_SCENES, 1799)
        self.assertEqual([(r["scene"], r["kind"], r["verdict"], r["starts_at"], r["ends_at"], r["length_sec"],
                           r["credits_before"], r["starts_at_sec"]) for r in rows],
                         [(1, "Mid-credits", "Likely", "2:08:32", "2:09:22", 50, "2:07:58-2:08:32", 7712.8)])
        # No credits markers: nothing is claimed either way.
        self.assertNotIn("stay_after_credits", self.movies[600])
        self.assertEqual(self.rows(S.CREDITS_SCENES, 600), [])

    def test_smart_collections(self):
        listing = {r["collection"]: r for r in self.result.sheet(S.COLLECTION_LIST).rows}
        expanded = {"Directed by Lana": [1799], "Owner Favourites": [700], "Old Sci-Fi": [710],
                    "Golden or Extended": [700], "Unwatched": [600, 1799, 710], "SD Films": [700], "Before 2000": [500]}
        members = defaultdict_list((r["collection"], r["plex_id"]) for r in self.result.sheet(S.COLLECTIONS).rows)
        for name, ids in expanded.items():
            self.assertTrue(listing[name]["status"].startswith("Expanded"), (name, listing[name]["status"]))
            self.assertEqual(listing[name]["exported"], len(ids), name)
            self.assertEqual(sorted(members[name]), sorted(ids), name)
        self.assertIn("server owner", listing["Owner Favourites"]["status"])
        self.assertTrue(listing["Recently Added"]["status"].startswith("Not expanded"))
        self.assertIn("'addedAt'", listing["Recently Added"]["status"])
        self.assertEqual(listing["Recently Added"]["exported"], 0)
        self.assertIn("Plex counted 5", listing["Wrong Count"]["status"])
        self.assertEqual(listing["Old Sci-Fi"]["filter"], "Genre is Science Fiction and Year before 1980")
        self.assertEqual(listing["Golden or Extended"]["filter"], "(Studio contains 'golden' or Edition contains 'extended')")
        self.assertEqual((listing["IMDB Top 250"]["collection_type"], listing["IMDB Top 250"]["plex_count"]),
                         ("Regular", 1))
        self.assertEqual(self.movies[1799]["collections"], "IMDB Top 250; Directed by Lana; Unwatched")
        types = {(r["collection"], r["collection_type"]) for r in self.rows(S.COLLECTIONS, 1799)}
        self.assertEqual(types, {("IMDB Top 250", "Regular"), ("Directed by Lana", "Smart"), ("Unwatched", "Smart")})

    def test_directing_and_producing_credits(self):
        hk = self.movies[700]
        self.assertEqual(hk["directors"], "Yuen Woo-ping")        # 'Action Director' used when there's no Director
        self.assertEqual(hk["producers"], "Real Producer")        # 'Assistant Producer' yes, "Producer's Assistant" no
        self.assertEqual(self.movies[710]["directors"], "")      # never promote an assistant director
        self.assertEqual(len(self.rows(S.CREW, 710)), 1)         # ...who is still on the Crew sheet

    def test_common_sense_ids_and_audio_layout(self):
        m = self.movies[1799]
        self.assertEqual((m["content_rating_age"], m["common_sense_rating"]), (15, 4))
        self.assertEqual(m["plex_movie_id"], MATRIX_GUID)
        hk = self.movies[700]
        self.assertEqual(hk["plex_guid"], "plex://movie/hk1/edition/Extended")
        self.assertEqual(hk["plex_movie_id"], "plex://movie/hk1")
        self.assertEqual(hk["audio"], "Dolby Digital 1.1")       # '2 channels (FC+LFE)'
        self.assertEqual(hk["audio_languages"], "Japanese")
        self.assertEqual(hk["resolution"], "SD")                  # 720x360, as Plex classes it
        self.assertEqual(self.movies[600]["plex_movie_id"], "")
        self.assertIn(("Plex database schema version", "20260901000000"), self.result.info)

    def test_extras_details(self):
        trailer, featurette = self.rows(S.EXTRAS, 1799)
        self.assertEqual((trailer["main_trailer"], trailer["released"], trailer["explicit"], trailer["thumbnail_url"]),
                         ("Yes", date(1999, 1, 1), "Yes", "https://img/trailer.jpg"))
        self.assertEqual((featurette["main_trailer"], featurette["explicit"]), ("", ""))

    def test_split_movie_tracks_counted_once(self):
        m = self.movies[500]
        self.assertEqual((m["audio_tracks"], m["subtitle_tracks"]), (1, 1))
        files = self.rows(S.FILES, 500)
        self.assertEqual([(f["audio_tracks"], f["subtitle_tracks"]) for f in files], [(1, 1), (1, 1), (0, 0)])
        self.assertEqual(len(self.rows(S.STREAMS, 500)), 4)          # the Streams sheet lists every copy

    def test_chapters_and_locked_fields(self):
        chapters = self.rows(S.CHAPTERS, 1799)
        self.assertEqual([(c["chapter"], c["name"], c["start"], c["end"]) for c in chapters],
                         [(1, "Opening", "0:00:00", "0:01:01"), (2, "", "0:01:01", "0:02:03"),
                          (3, "Finale", "0:02:03", "0:03:04")])
        self.assertEqual((chapters[1]["start_sec"], chapters[1]["end_sec"]), (61.5, 123.0))
        self.assertEqual(self.movies[1799]["locked_fields"], "9; 16")
        self.assertEqual(self.movies[500]["locked_fields"], "")


class EdgeCaseDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "com.plexapp.plugins.library.db-2026-09-25")
        build_fixture(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def test_regular_and_smart_collection_with_the_same_name(self):
        f = Fixture.__new__(Fixture)
        f.con, f._tag_ids = sqlite3.connect(self.db), {}
        f.insert("metadata_items", library_section_id=1, metadata_type=18, title="Favourites",
                 guid="collection://fav-regular", summary="REGULAR summary", extra_data=J({"at:childCount": "2"}))
        f.tag(600, 2, "Favourites")          # regular membership, with no at:guid to match it by
        f.tag(710, 2, "Favourites")
        f.insert("metadata_items", library_section_id=1, metadata_type=18, title="Favourites",
                 guid="collection://fav-smart", summary="SMART summary",
                 extra_data=J({"at:smart": "1", "at:childCount": "1",
                               "pv:uri": "x/library/sections/1/all?type=1&studio=golden"}))
        f.close()
        result = extract(self.db)
        listing = [r for r in result.sheet(S.COLLECTION_LIST).rows if r["collection"] == "Favourites"]
        self.assertEqual(sorted((r["collection_type"], r["plex_count"], r["exported"]) for r in listing),
                         [("Regular", 2, 2), ("Smart", 1, 1)])
        rows = [r for r in result.sheet(S.COLLECTIONS).rows if r["collection"] == "Favourites"]
        self.assertEqual(sorted((r["plex_id"], r["collection_type"], r["collection_summary"]) for r in rows),
                         [(600, "Regular", "REGULAR summary"), (700, "Smart", "SMART summary"),
                          (710, "Regular", "REGULAR summary")])

    def test_director_filed_under_another_job(self):
        f = Fixture.__new__(Fixture)
        f.con, f._tag_ids = sqlite3.connect(self.db), {}
        gotg = f.insert("metadata_items", id=12812, library_section_id=1, metadata_type=1,
                        title="Guardians of the Galaxy Vol. 2", guid="plex://movie/gotg2", year=2017)
        f.tag(gotg, 4, "James Gunn", 0, "Script Supervisor")
        f.tag(gotg, 4, "Lars P. Winther", 1, "First Assistant Director")
        f.close()
        movie = next(m for m in extract(self.db).sheet(S.MOVIES).rows if m["plex_id"] == 12812)
        self.assertEqual(movie["directors"], "James Gunn")

    def test_damaged_database(self):
        """Header and schema read fine, but a table's pages are garbage - the usual kind of corruption."""
        bad = os.path.join(self.tmp.name, "com.plexapp.plugins.library.db-2026-09-26")
        shutil.copyfile(self.db, bad)
        con = sqlite3.connect(bad)
        page_size = con.execute("PRAGMA page_size").fetchone()[0]
        root = con.execute("SELECT rootpage FROM sqlite_master WHERE name = 'metadata_items'").fetchone()[0]
        con.close()
        with open(bad, "r+b") as fh:
            fh.seek((root - 1) * page_size)
            fh.write(b"\xff" * page_size)
        with self.assertRaises(PlexDBError) as ctx:
            list_movie_libraries(bad)
        self.assertIn("damaged", str(ctx.exception))
        with self.assertRaises(PlexDBError):
            extract(bad)
        # The newest dump is damaged, so the one before it is picked.
        self.assertEqual(os.path.basename(find_newest_database(self.tmp.name)),
                         "com.plexapp.plugins.library.db-2026-09-25")


class OldSchemaTests(FixtureTestCase):
    """Plex versions from before editions, slugs and square art - and tags without a 'key' column."""
    drop_columns = [("metadata_items", "edition_title"), ("metadata_items", "slug"),
                    ("metadata_items", "user_square_art_url"), ("tags", "key"),
                    ("media_items", "color_trc")]

    def test_still_exports(self):
        m = self.movies[1799]
        self.assertEqual(m["edition"], "")
        self.assertEqual(m["plex_url"], "")
        self.assertEqual(m["directors"], "Lana Wachowski; Lilly Wachowski")
        self.assertEqual(self.rows(S.CAST, 1799)[0]["person_id"], "")


class OptionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = os.path.join(cls.tmp.name, "com.plexapp.plugins.library.db")
        build_fixture(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_library_filter(self):
        result = extract(self.db, library_ids=[2])
        self.assertEqual([r["plex_id"] for r in result.sheet(S.MOVIES).rows], [500])
        self.assertEqual(result.sheet(S.WATCH_HISTORY).rows, [])

    def test_no_detail_sheets(self):
        result = extract(self.db, detail_sheets=[])
        self.assertEqual([s.name for s in result.sheets], [S.MOVIES])
        self.assertEqual(len(result.sheet(S.MOVIES).rows), 5)

    def test_list_libraries(self):
        libs = list_movie_libraries(self.db)
        self.assertEqual([(lib.id, lib.name, lib.movie_count) for lib in libs], [(2, "Classics", 1), (1, "Movies", 4)])
        self.assertEqual(libs[1].folders, ["/disk1/Movies"])

    def test_cancel(self):
        stop = threading.Event()
        stop.set()
        with self.assertRaises(Cancelled):
            extract(self.db, cancel=stop)

    def test_source_database_never_modified(self):
        before = (os.path.getsize(self.db), os.path.getmtime(self.db))
        extract(self.db)
        self.assertEqual(before, (os.path.getsize(self.db), os.path.getmtime(self.db)))
        self.assertFalse(os.path.exists(self.db + "-wal"))
        self.assertFalse(os.path.exists(self.db + "-journal"))


class BadInputTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_file(self):
        with self.assertRaises(PlexDBError):
            extract(os.path.join(self.tmp.name, "nope.db"))

    def test_not_sqlite(self):
        path = os.path.join(self.tmp.name, "junk.db")
        with open(path, "wb") as f:
            f.write(b"this is not a database" * 100)
        with self.assertRaises(PlexDBError):
            extract(path)

    def test_not_plex(self):
        path = os.path.join(self.tmp.name, "other.db")
        con = sqlite3.connect(path)
        con.execute("CREATE TABLE x (a)")
        con.close()
        with self.assertRaises(PlexDBError):
            extract(path)

    def test_blobs_database(self):
        """The blobs DB has the same tables but no libraries - give a helpful error."""
        path = os.path.join(self.tmp.name, "com.plexapp.plugins.library.blobs.db")
        con = sqlite3.connect(path)
        con.executescript(SCHEMA)
        con.close()
        with self.assertRaises(PlexDBError) as ctx:
            extract(path)
        self.assertIn("blobs", str(ctx.exception))

    def test_download_database_zip(self):
        """Plex's 'Download database' button gives a zip holding the library and blobs databases."""
        db = os.path.join(self.tmp.name, "library.db")
        build_fixture(db)
        blobs = os.path.join(self.tmp.name, "blobs.db")
        sqlite3.connect(blobs).close()
        path = os.path.join(self.tmp.name, "Plex Media Server Databases_2026-09-25_10-00-00.zip")
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(blobs, "Databases/com.plexapp.plugins.library.blobs.db")
            z.write(db, "Databases/com.plexapp.plugins.library.db")
        result = extract(path)
        self.assertEqual(result.movie_count, 5)
        self.assertIn(("Database inside the zip", "Databases/com.plexapp.plugins.library.db"), result.info)
        self.assertTrue(is_candidate_database(os.path.basename(path)))
        self.assertEqual(dump_date(path), "2026-09-25")
        empty = os.path.join(self.tmp.name, "Plex no db.zip")
        with zipfile.ZipFile(empty, "w") as z:
            z.write(blobs, "com.plexapp.plugins.library.blobs.db")
        with self.assertRaises(PlexDBError):
            extract(empty)
        leftovers = [n for n in os.listdir(tempfile.gettempdir()) if n.startswith("projectionist-")]
        self.assertEqual(leftovers, [])

    def test_damaged_or_encrypted_zip_is_a_clean_error(self):
        db = os.path.join(self.tmp.name, "library.db")
        build_fixture(db)
        good = os.path.join(self.tmp.name, "Plex good.zip")
        with zipfile.ZipFile(good, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(db, "com.plexapp.plugins.library.db")
        with open(good, "rb") as f:
            data = bytearray(f.read())
        cases = {}
        corrupt = bytearray(data)
        for i in range(len(corrupt) // 3, len(corrupt) // 3 + 64):   # scramble the compressed data
            corrupt[i] ^= 0x5A
        cases["corrupt"] = corrupt
        encrypted = bytearray(data)
        for header, flag_at in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):   # set the 'encrypted' flag bit
            pos = encrypted.find(header)
            encrypted[pos + flag_at] |= 0x01
        cases["encrypted"] = encrypted
        unsupported = bytearray(data)
        for header, method_at in ((b"PK\x03\x04", 8), (b"PK\x01\x02", 10)):  # Deflate64, as Windows makes
            pos = unsupported.find(header)
            unsupported[pos + method_at:pos + method_at + 2] = (9).to_bytes(2, "little")
        cases["deflate64"] = unsupported
        before = {n for n in os.listdir(tempfile.gettempdir()) if n.startswith("projectionist-")}
        for name, content in cases.items():
            path = os.path.join(self.tmp.name, f"Plex {name}.zip")
            with open(path, "wb") as f:
                f.write(content)
            with self.assertRaises(PlexDBError, msg=name):
                extract(path)
        after = {n for n in os.listdir(tempfile.gettempdir()) if n.startswith("projectionist-")}
        self.assertEqual(after - before, set())   # no half-unpacked copies left behind

    def test_zip_member_choice(self):
        from projectionist.extract import pick_library_member

        def info(name, size, when=(2026, 9, 1, 0, 0, 0)):
            i = zipfile.ZipInfo(name, when)
            i.file_size = size
            return i
        older_bigger = info("com.plexapp.plugins.library.db-2026-09-19", 9000)
        newer = info("com.plexapp.plugins.library.db-2026-09-25", 10)
        self.assertIs(pick_library_member([older_bigger, newer, info("blobs.db", 99999)]), newer)
        live = info("x/com.plexapp.plugins.library.db", 5)
        self.assertIs(pick_library_member([newer, live]), live)          # the live file beats backups
        old_copy = info("old/com.plexapp.plugins.library.db", 9000, (2026, 9, 19, 0, 0, 0))
        new_copy = info("new/com.plexapp.plugins.library.db", 10, (2026, 9, 25, 0, 0, 0))
        self.assertIs(pick_library_member([new_copy, old_copy]), new_copy)   # same name twice: newest
        twin = info("other/com.plexapp.plugins.library.db", 10, (2026, 9, 25, 0, 0, 0))
        with self.assertRaises(PlexDBError):                                 # ...and no telling which: ask
            pick_library_member([new_copy, twin])
        self.assertIsNone(pick_library_member([info("Plex Media Server.log", 5)]))

    def test_uncheckpointed_wal_is_read(self):
        path = os.path.join(self.tmp.name, "live.db")
        build_fixture(path)
        con = sqlite3.connect(path)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA wal_autocheckpoint=0")
        con.execute("UPDATE metadata_items SET title='Changed In WAL' WHERE id=1799")
        con.commit()
        try:
            self.assertGreater(os.path.getsize(path + "-wal"), 0)
            result = extract(path)
            titles = {r["plex_id"]: r["title"] for r in result.sheet(S.MOVIES).rows}
            self.assertEqual(titles[1799], "Changed In WAL")
        finally:
            con.close()


class WriterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = os.path.join(cls.tmp.name, "com.plexapp.plugins.library.db-2026-09-25")
        build_fixture(cls.db)
        cls.result = extract(cls.db)
        cls.xlsx = write_xlsx(cls.result, os.path.join(cls.tmp.name, "out.xlsx"))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_every_part_is_well_formed_xml(self):
        with zipfile.ZipFile(self.xlsx) as z:
            self.assertIsNone(z.testzip())
            for name in z.namelist():
                if name.endswith((".xml", ".rels")):
                    ET.fromstring(z.read(name))

    def test_sheets_in_order(self):
        with zipfile.ZipFile(self.xlsx) as z:
            wb = ET.fromstring(z.read("xl/workbook.xml"))
        ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        names = [s.get("name") for s in wb.find("m:sheets", ns)]
        self.assertEqual(names, [S.MOVIES] + S.DETAIL_SHEET_NAMES + [S.ABOUT])

    def test_formula_looking_text_is_not_a_formula(self):
        with zipfile.ZipFile(self.xlsx) as z:
            sheet1 = z.read("xl/worksheets/sheet1.xml").decode("utf-8")
        self.assertNotIn("<f>", sheet1)
        self.assertIn("=Formula Looking Title", sheet1)

    def test_no_temp_files_left(self):
        leftovers = [n for n in os.listdir(self.tmp.name) if n.startswith("~projectionist-")]
        self.assertEqual(leftovers, [])

    def test_the_workbook_names_the_app(self):
        from projectionist import __version__
        self.assertEqual(dict(self.result.info)["Exported by"], f"Projectionist {__version__}")
        with zipfile.ZipFile(self.xlsx) as z:
            core = z.read("docProps/core.xml").decode("utf-8")
            about = "".join(z.read(n).decode("utf-8") for n in z.namelist() if n.startswith("xl/worksheets/"))
        self.assertIn("<dc:title>Movie library</dc:title>", core)
        self.assertIn("Created by Projectionist", core)
        self.assertIn("Movie Library Export", about)
        self.assertIn(f"Projectionist {__version__}", about)
        self.assertNotIn("Exporter", core + about)

    def test_csv(self):
        folder = write_csv(self.result, csv_folder_for(self.xlsx))
        with open(os.path.join(folder, "Movies.csv"), encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 5)
        matrix = next(r for r in rows if r["Plex ID"] == "1799")
        self.assertEqual(matrix["Release Date"], "1999-03-31")
        self.assertEqual(matrix["Aspect Ratio"], "2.4")   # full precision, not display rounding
        self.assertEqual(matrix["Summary"], "A hacker\nlearns the truth.")
        self.assertTrue(os.path.exists(os.path.join(folder, "Watch History.csv")))

    def test_csv_folder_only_holds_this_export(self):
        from projectionist.export import OutputError, check_csv_writable
        folder = os.path.join(self.tmp.name, "refresh (CSV)")
        write_csv(self.result, folder)
        self.assertTrue(os.path.exists(os.path.join(folder, "Cast.csv")))
        write_csv(extract(self.db, detail_sheets=[]), folder)            # re-export with no extra sheets
        self.assertEqual(os.listdir(folder), ["Movies.csv"])             # last time's Cast.csv etc. are gone
        if sys.platform == "win32":
            with open(os.path.join(folder, "Movies.csv"), "rb"):         # open in a spreadsheet program
                with self.assertRaises(OutputError):
                    check_csv_writable(folder)

    def test_overwrites_existing_file(self):
        path = os.path.join(self.tmp.name, "again.xlsx")
        with open(path, "wb") as f:
            f.write(b"old")
        write_xlsx(self.result, path)
        with zipfile.ZipFile(path) as z:
            self.assertIn("xl/workbook.xml", z.namelist())

    @unittest.skipIf(os.name == "nt", "Linux and Macs: permissions")
    def test_saved_like_any_other_file(self):
        """Written through a temporary file (mkstemp's: its owner's alone), the spreadsheet still gets a new file's
        usual permissions (0644 with the usual umask) - or keeps those of the one it replaces."""
        old = os.umask(0o022)
        try:
            path = write_xlsx(self.result, os.path.join(self.tmp.name, "shared.xlsx"))
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o644)
            os.chmod(path, 0o640)
            write_xlsx(self.result, path)
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o640)
        finally:
            os.umask(old)

    def test_sheets_too_long_for_excel_continue_on_new_sheets(self):
        from projectionist import export
        saved = export.EXCEL_MAX_ROWS
        export.EXCEL_MAX_ROWS = 3   # 2 data rows per sheet
        try:
            path = write_xlsx(self.result, os.path.join(self.tmp.name, "split.xlsx"))
        finally:
            export.EXCEL_MAX_ROWS = saved
        with zipfile.ZipFile(path) as z:
            wb = z.read("xl/workbook.xml").decode()
        for name in ("Movies", "Movies (2)", "Cast", "Cast (2)"):
            self.assertIn(f'name="{name}"', wb)
        self.assertNotIn('name="Cast (3)"', wb)

    @unittest.skipUnless(sys.platform == "win32", "Windows file locking")
    def test_output_open_in_another_program_is_caught_up_front(self):
        from projectionist.export import OutputError, check_writable
        path = os.path.join(self.tmp.name, "open-elsewhere.xlsx")
        with open(path, "wb") as f:
            f.write(b"x")
        check_writable(path)   # not open anywhere: fine
        with open(path, "rb"):   # like Excel, Python's open() doesn't let others delete/replace the file
            with self.assertRaises(OutputError):
                check_writable(path)
            with self.assertRaises(OutputError):
                write_xlsx(self.result, path)

    def test_output_problems_are_named_correctly(self):
        from projectionist import export
        from projectionist.export import OutputError, check_writable
        readonly = os.path.join(self.tmp.name, "readonly.xlsx")
        open(readonly, "w").close()
        os.chmod(readonly, stat.S_IREAD)
        try:
            if sys.platform == "win32":
                with self.assertRaises(OutputError) as ctx:
                    check_writable(readonly)
                self.assertIn("read-only", str(ctx.exception))
            else:
                # Elsewhere a save puts a new file in the old one's place, which only needs permission to create
                # files in its folder, whoever runs it and whatever the old file's own permissions: no problem
                # to name, and the save works.
                check_writable(readonly)
                write_xlsx(self.result, readonly)
                with open(readonly, "rb") as f:
                    self.assertEqual(f.read(2), b"PK")
                self.assertEqual(stat.S_IMODE(os.stat(readonly).st_mode), stat.S_IREAD)   # (still read-only)
        finally:
            os.chmod(readonly, stat.S_IREAD | stat.S_IWRITE)
        folder = os.path.join(self.tmp.name, "folder.xlsx")
        os.makedirs(folder)
        with self.assertRaises(OutputError) as ctx:
            check_writable(folder)
        self.assertIn("is a folder", str(ctx.exception))
        # A folder we may not create files in is caught before any work is done.
        saved = export.tempfile.mkstemp
        export.tempfile.mkstemp = lambda *a, **k: (_ for _ in ()).throw(PermissionError(13, "Permission denied"))
        try:
            with self.assertRaises(OutputError) as ctx:
                check_writable(os.path.join(self.tmp.name, "new.xlsx"))
        finally:
            export.tempfile.mkstemp = saved
        self.assertIn("no permission", str(ctx.exception))

    def test_file_names_windows_refuses_are_caught_up_front(self):
        from projectionist.export import OutputError, check_writable
        names = ["x" * 260 + ".xlsx"]
        if sys.platform == "win32":
            names += ["bad<name>.xlsx", "what?.xlsx", "Movies: 2026.xlsx", 'q"uote.xlsx', "tab\there.xlsx"]
        for name in names:
            with self.assertRaises(OutputError, msg=name) as ctx:
                check_writable(os.path.join(self.tmp.name, name))
            self.assertIn("can't be used as a file name", str(ctx.exception))
        check_writable(os.path.join(self.tmp.name, "Fine name (2026) - copy.xlsx"))
        from projectionist.export import invalid_file_name
        self.assertIn("longer than", invalid_file_name("\U0001F3AC" * 128 + ".xlsx"))   # 261 UTF-16 units
        self.assertEqual(invalid_file_name("\U0001F3AC" * 100 + ".xlsx"), "")
        if sys.platform == "win32":   # (full paths lose a trailing dot anyway, as Windows itself does)
            self.assertIn("end with a space or a dot", invalid_file_name("name.xlsx."))
        # The pre-flight check leaves nothing behind when the target doesn't exist yet.
        check_writable(os.path.join(self.tmp.name, "probe-me.xlsx"))
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "probe-me.xlsx")))

    def test_temp_drive_full_while_writing_rows(self):
        import xlsxwriter.worksheet
        from projectionist.export import OutputError
        real = xlsxwriter.worksheet.Worksheet.write_string

        def fills_up(self, *args, **kwargs):
            raise OSError(28, "No space left on device")

        out_dir = os.path.join(self.tmp.name, "tempfull")
        os.makedirs(out_dir)
        xlsxwriter.worksheet.Worksheet.write_string = fills_up
        try:
            with self.assertRaises(OutputError) as ctx:
                write_xlsx(self.result, os.path.join(out_dir, "out.xlsx"))
        finally:
            xlsxwriter.worksheet.Worksheet.write_string = real
        self.assertIn("temporary files", str(ctx.exception))
        self.assertEqual(os.listdir(out_dir), [])

    def test_about_sheet_has_no_number_as_text_warnings(self):
        with zipfile.ZipFile(self.xlsx) as z:
            names = [n for n in z.namelist() if n.startswith("xl/worksheets/sheet")]
            flagged = [n for n in names if b'numberStoredAsText="1"' in z.read(n)]
        self.assertEqual(sorted(flagged), sorted(names))   # every sheet, About included

    def test_disk_full_while_saving_leaves_nothing_behind(self):
        import xlsxwriter.workbook
        from projectionist.export import OutputError
        real_zip = xlsxwriter.workbook.ZipFile

        class FillsUp(real_zip):
            writes = 0

            def write(self, *args, **kwargs):
                FillsUp.writes += 1
                if FillsUp.writes == 4:
                    raise OSError(28, "No space left on device")
                return super().write(*args, **kwargs)

        out_dir = os.path.join(self.tmp.name, "diskfull")
        os.makedirs(out_dir)
        xlsxwriter.workbook.ZipFile = FillsUp
        try:
            with self.assertRaises(OutputError) as ctx:
                write_xlsx(self.result, os.path.join(out_dir, "out.xlsx"))
        finally:
            xlsxwriter.workbook.ZipFile = real_zip
        self.assertIn("No space left", str(ctx.exception))
        self.assertEqual(os.listdir(out_dir), [])

    def test_leftovers_from_a_killed_export_are_swept(self):
        from projectionist.export import sweep_stale_temp_files
        out_dir = os.path.join(self.tmp.name, "sweep")
        os.makedirs(out_dir)
        old = time.time() - 7 * 3600
        stale_dir = tempfile.mkdtemp(prefix="projectionist-")
        fresh_dir = tempfile.mkdtemp(prefix="projectionist-")
        stale_old_dir = tempfile.mkdtemp(prefix="plexmovies-")   # from before the app was renamed
        stale_xlsx = os.path.join(out_dir, "~projectionist-abc.xlsx")
        stale_old_xlsx = os.path.join(out_dir, "~plexmovies-abc.xlsx")
        keep = os.path.join(out_dir, "~other-program.xlsx")
        for p in (stale_xlsx, stale_old_xlsx, keep):
            open(p, "w").close()
        for p in (stale_dir, stale_old_dir, stale_xlsx, stale_old_xlsx, keep):
            os.utime(p, (old, old))
        try:
            sweep_stale_temp_files(out_dir)
            self.assertFalse(os.path.exists(stale_dir))
            self.assertFalse(os.path.exists(stale_old_dir))
            self.assertFalse(os.path.exists(stale_xlsx))
            self.assertFalse(os.path.exists(stale_old_xlsx))
            self.assertTrue(os.path.exists(fresh_dir))    # could belong to an export running right now
            self.assertTrue(os.path.exists(keep))         # not ours
        finally:
            for p in (stale_dir, fresh_dir, stale_old_dir):
                shutil.rmtree(p, ignore_errors=True)

    def test_cancel_leaves_nothing_behind(self):
        stop = threading.Event()
        stop.set()
        path = os.path.join(self.tmp.name, "cancelled.xlsx")
        big = extract(self.db)
        big.sheet(S.CAST).rows *= 2000   # enough rows to reach a cancel check
        with self.assertRaises(Cancelled):
            write_xlsx(big, path, cancel=stop)
        self.assertFalse(os.path.exists(path))
        self.assertEqual([n for n in os.listdir(self.tmp.name) if n.startswith("~projectionist-")], [])


class HelperTests(unittest.TestCase):
    def test_dates(self):
        self.assertEqual(utc_date(922838400), date(1999, 3, 31))
        self.assertEqual(utc_date(-1813795200), date(1912, 7, 11))
        self.assertEqual(utc_date("1999-03-31 00:00:00"), date(1999, 3, 31))
        self.assertIsNone(utc_date(None))
        self.assertIsNone(local_datetime(0))
        self.assertIsInstance(local_datetime(-100000000), datetime)   # must not crash on Windows
        self.assertEqual(parse_timestamp("2012-05-04 10:00:00"), 1336125600)
        # Times Windows can't localise (before 1970) still get the local offset, so they stay in order
        # with everything else rather than jumping by the UTC offset.
        self.assertEqual(local_datetime(1) - local_datetime(-1), timedelta(seconds=2))
        local_offset = local_datetime(86400) - datetime(1970, 1, 2)
        self.assertEqual(local_datetime(-86400), datetime(1969, 12, 31) + local_offset)

    def test_resolution(self):
        cases = {(3840, 1600): "4K", (1920, 800): "1080p", (1280, 536): "720p", (720, 576): "576p",
                 (720, 480): "480p", (720, 360): "SD", (720, 464): "480p", (720, 462): "SD", (320, 240): "SD",
                 (7680, 4320): "8K", (2560, 1440): "1080p", (1374, 1036): "720p", (1920, 800): "1080p", (0, 0): ""}
        for (w, h), label in cases.items():
            self.assertEqual(resolution_label(w, h), label, (w, h))

    def test_hdr(self):
        self.assertEqual(hdr_label({"ma:colorTrc": "bt709"}, None), "SDR")
        self.assertEqual(hdr_label({}, "smpte2084"), "HDR10")
        self.assertEqual(hdr_label({"ma:colorTrc": "arib-std-b67"}, None), "HLG")
        self.assertEqual(hdr_label({"ma:DOVIPresent": "1", "ma:DOVIProfile": "5", "ma:DOVIBLCompatID": "0",
                                    "ma:colorTrc": "smpte2084"}, None), "Dolby Vision P5")
        self.assertEqual(hdr_label({}, None), "")

    def test_audio(self):
        self.assertEqual(audio_format("dca", "ma", 6, "5.1(side)"), "DTS-HD MA 5.1")
        self.assertEqual(audio_format("dca", "ma + dts:x imax", 8, None), "DTS:X IMAX Enhanced 7.1")
        self.assertEqual(audio_format("eac3", "dolby digital plus + dolby atmos", 6, None),
                         "Dolby Digital Plus Atmos 5.1")
        self.assertEqual(audio_format("aac", "lc", 2, "stereo"), "AAC 2.0")
        self.assertEqual(audio_format("pcm_s24le", None, 2, None), "PCM 2.0")
        self.assertEqual(channel_layout(None, "5.1(side)"), "5.1")
        self.assertEqual(channel_layout(2, "2 channels (FC+LFE)"), "1.1")
        self.assertEqual(channel_layout(3, "3 channels (FL+FR+LFE)"), "2.1")
        self.assertEqual(channel_layout(4, "quad(side)"), "4.0")
        self.assertEqual(channel_layout(8, "5.1.2"), "5.1.2")
        self.assertEqual(channel_layout(12, "7.1.4"), "7.1.4")
        self.assertEqual(channel_layout(8, "7.1(wide-side)"), "7.1")
        self.assertEqual(channel_layout(6, "hexagonal"), "6.0")
        self.assertEqual(channel_layout(6, "some new layout"), "5.1")   # unknown words: fall back to the count

    def test_languages(self):
        self.assertEqual(language_name("en"), "English")
        self.assertEqual(language_name("eng"), "English")
        self.assertEqual(language_name("ger"), "German")
        self.assertEqual(language_name("pt-BR"), "Portuguese (BR)")
        self.assertEqual(language_name("xx"), "xx")
        self.assertEqual(language_name(""), "")

    def test_misc(self):
        self.assertEqual(parse_extra('{"a":"1","url":"a=1"}'), {"a": "1"})
        self.assertEqual(parse_extra("at%3Atype=critic"), {"at:type": "critic"})
        self.assertEqual(parse_extra(None), {})
        self.assertEqual(clean("  a\x00b\r\nc  "), "ab\nc")
        self.assertEqual(rating_source("rottentomatoes://image.rating.spilled"), ("Rotten Tomatoes", "Spilled"))
        self.assertEqual(rating_source("imdb://image.rating"), ("IMDb", ""))
        self.assertEqual(split_path("/a/b/c.mkv"), ("/a/b", "c.mkv"))
        self.assertEqual(split_path(r"C:\a\c.mkv"), (r"C:\a", "c.mkv"))
        self.assertEqual(split_path(r"\\nas\share\c.mkv"), (r"\\nas\share", "c.mkv"))


class CreditsAnalysisTests(unittest.TestCase):
    """projectionist.credits: reading mid/post-credits scenes out of Plex's credits markers."""

    def run_case(self, blocks, duration, year=2015, final_flags=True):
        from projectionist.credits import Marker, analyse
        return analyse([Marker(s * 1000, e * 1000, f) for s, e, f in blocks], duration and duration * 1000, year,
                       final_flags)

    def scenes(self, info):
        return [(s.kind, s.verdict, s.start // 1000, s.length // 1000) for s in info.scenes]

    def test_nothing_to_go_on(self):
        info = self.run_case([], 6000)
        self.assertEqual((info.verdict, info.credits_start, info.scenes), ("", None, []))

    def test_only_final_credits(self):
        info = self.run_case([(5000, 6000, True)], 6000)
        self.assertEqual((info.verdict, info.credits_start, info.scenes), ("None found", 5_000_000, []))

    def test_mid_credits_scene(self):
        info = self.run_case([(5000, 5100, None), (5150, 6000, True)], 6000)
        self.assertEqual(info.verdict, "Yes")
        self.assertEqual(self.scenes(info), [("Mid-credits", "Likely", 5100, 50)])

    def test_scene_after_the_credits(self):
        # Pirates of the Caribbean: one stretch of credits that isn't final, then the monkey.
        info = self.run_case([(5000, 5540, None)], 5600)
        self.assertEqual(self.scenes(info), [("After the credits", "Likely", 5540, 60)])

    def test_moment_between_stretches_is_merged(self):
        info = self.run_case([(5000, 5100, None), (5103, 6000, True)], 6000)
        self.assertEqual((info.verdict, info.scenes), ("None found", []))
        overlapping = self.run_case([(5000, 5500, True), (5020, 5500, True)], 5500)   # Nope has two 'finals'
        self.assertEqual((overlapping.verdict, overlapping.credits_start), ("None found", 5_000_000))

    def test_very_short_footage_is_a_maybe(self):
        info = self.run_case([(5000, 5100, None), (5112, 6000, True)], 6000)
        self.assertEqual(self.scenes(info), [("Mid-credits", "Maybe", 5100, 12)])
        self.assertIn("title card", info.scenes[0].reason)
        self.assertEqual(info.verdict, "Maybe")

    def test_older_films_are_cautious(self):
        info = self.run_case([(5000, 5100, None), (5160, 6000, True)], 6000, year=1963)
        self.assertEqual(self.scenes(info), [("Mid-credits", "Maybe", 5100, 60)])
        self.assertIn("older film", info.scenes[0].reason)

    def test_text_inside_the_film_is_not_credits(self):
        # 'Credits' at 1:30, then ten more minutes of film, then the real end credits.
        info = self.run_case([(5400, 5430, None), (6030, 6600, True)], 6600)
        self.assertEqual((info.verdict, info.credits_start, info.ignored), ("None found", 6_030_000,
                                                                          [(5_400_000, 5_430_000)]))

    def test_long_stretch_is_a_maybe_on_modern_films_only(self):
        # Napoleon Dynamite: credits, a five-minute wedding, then a last bit of credits.
        info = self.run_case([(5215, 5383, None), (5683, 5694, True)], 5694, year=2004)
        self.assertEqual(self.scenes(info), [("Mid-credits", "Maybe", 5383, 300)])
        self.assertIn("long stretch", info.scenes[0].reason)
        self.assertEqual(info.credits_start, 5_215_000)         # never after a scene it reports
        old = self.run_case([(5215, 5383, None), (5683, 5694, True)], 5694, year=1956)
        self.assertEqual((old.verdict, old.scenes, old.credits_start), ("None found", [], 5_683_000))
        # ...and a long stretch after the last marker, with nothing after it
        tail = self.run_case([(5600, 5624, None)], 5883, year=1985)
        self.assertEqual(self.scenes(tail), [("After the credits", "Maybe", 5624, 259)])
        self.assertEqual(tail.credits_start, 5_600_000)
        self.assertEqual(self.run_case([(5600, 5624, None)], 5883, year=1956).verdict, "")

    def test_short_burst_then_a_long_stretch(self):
        # On-screen text taken for credits (Taxi Driver's newspaper clippings), then three minutes of film,
        # then the real credits.
        info = self.run_case([(6416, 6464, None), (6650, 6827, True)], 6827, year=2007)
        self.assertEqual(self.scenes(info), [("Mid-credits", "Maybe", 6464, 186)])
        self.assertIn("on-screen text", info.scenes[0].reason)
        # A short first stretch with a short scene after it is normal (Guardians Vol. 2).
        gotg = self.run_case([(7686, 7716, None), (7746, 8220, True)], 8220, year=2017)
        self.assertEqual(self.scenes(gotg), [("Mid-credits", "Likely", 7716, 30)])

    def test_plex_versions_without_final_flags(self):
        info = self.run_case([(5000, 5100, None), (5150, 5540, None)], 5600, final_flags=False)
        self.assertEqual(self.scenes(info), [("Mid-credits", "Likely", 5100, 50),
                                             ("After the credits", "Maybe", 5540, 60)])
        self.assertIn("doesn't say which credits are the last", info.scenes[1].reason)

    def test_unknown_duration_uses_the_markers(self):
        info = self.run_case([(5000, 5100, None), (5150, 6000, None)], None)
        self.assertEqual((info.duration, self.scenes(info)), (6_000_000, [("Mid-credits", "Likely", 5100, 50)]))

    def test_wording(self):
        from projectionist.credits import CreditsScene, clock, describe, span
        self.assertEqual((clock(7712819), clock(None), clock(-5)), ("2:08:32", "", ""))
        self.assertEqual((span(28_000), span(102_000), span(300_000)), ("28 s", "1 min 42 s", "5 min"))
        scene = CreditsScene("Mid-credits", 7_512_000, 7_540_000, "Maybe", "x")
        self.assertEqual(describe(scene), "2:05:12 mid-credits (28 s) (maybe)")

    def test_marker_timeline_picks_the_matching_version(self):
        from projectionist.extract import marker_timeline
        versions = [{"duration": 7_000_000, "parts": []}, {"duration": 6_100_000, "parts": []},
                    {"duration": None, "parts": [{"duration": 3_000_000}, {"duration": 3_050_000}]}]
        self.assertEqual(marker_timeline(versions, 6_040_000), 6_050_000)   # shortest one long enough
        self.assertEqual(marker_timeline(versions, 9_000_000), 7_000_000)   # none fit: the longest
        self.assertEqual(marker_timeline(versions, None), 7_000_000)        # no markers: the first version
        self.assertIsNone(marker_timeline([], 5))


class SmartFilterTests(unittest.TestCase):
    """Plex's saved-filter language, evaluated on made-up movies (no database needed)."""

    @staticmethod
    def movie(lib=1, tags=(), studio="", edition="", title="", year=None, res=(), audio=(), watched=False, rating=None):
        from projectionist.smart import MovieFacts
        return MovieFacts(library_id=lib, tag_ids=set(tags), studio=studio, edition=edition, title=title,
                          content_rating="", year=year, resolutions=set(res), audio_languages=set(audio),
                          subtitle_languages=set(), owner_watched=watched, owner_rating=rating)

    def setUp(self):
        self.movies = {
            1: self.movie(studio="Village Roadshow Pictures", title="The Matrix", year=1999, tags={10}),
            2: self.movie(studio="Golden Harvest Company", title="Hong Kong Action", year=1985, tags={11}),
            3: self.movie(studio="Golden Harvest", title="Golden Harvest Story", year=1975),
            4: self.movie(lib=2, studio="Golden Harvest", title="Other Library", year=1975),
        }

    def run_filter(self, query, count):
        from projectionist.smart import expand
        uri = f"server://x/com.plexapp.plugins.library/library/sections/1/all?type=1&sort=titleSort&{query}"
        return expand(uri, count, self.movies, {10: "Keanu Reeves"})

    def members(self, query, count):
        result = self.run_filter(query, count)
        return sorted(result.members) if result.expanded else result.status

    def test_text_operators(self):
        self.assertEqual(self.members("studio=golden", 2), [2, 3])                          # contains
        self.assertEqual(self.members("studio!=golden", 1), [1])                            # doesn't contain
        self.assertEqual(self.members("studio==Golden%20Harvest", 1), [3])                  # is (exactly)
        self.assertEqual(self.members("studio%3D=golden%20harvest", 1), [3])                # ...percent-encoded
        self.assertEqual(self.members("studio!==Golden%20Harvest", 2), [1, 2])              # is not
        self.assertEqual(self.members("title%3C=The", 1), [1])                              # begins with
        self.assertEqual(self.members("title>=Story", 1), [3])                              # ends with

    def test_mixed_filter_is_evaluated_as_plex_means_it(self):
        # (Studio is 'Golden Harvest Company' or Title contains 'Matrix') and Studio is not 'Village Roadshow
        # Pictures' - only movie 2. Reading '==' as 'contains' used to pick movie 1 with the same count.
        query = ("push=1&studio==Golden%20Harvest%20Company&or=1&title=Matrix&pop=1&and=1"
                 "&studio!==Village%20Roadshow%20Pictures")
        self.assertEqual(self.members(query, 1), [2])

    def test_numbers_are_strict_and_tags_by_id(self):
        self.assertEqual(self.members("year%3C%3C=1985", 1), [3])                           # strictly before
        self.assertEqual(self.members("year%3E%3E=1985", 1), [1])
        self.assertEqual(self.members("actor=10,11", 2), [1, 2])                            # any of
        self.assertEqual(self.members("actor!=10", 2), [2, 3])

    def test_unsupported_or_unverifiable_filters_are_not_expanded(self):
        for query, reason in [("addedAt%3E%3E=-30d", "'addedAt'"), ("year%3C=1990", "'<=' operator on 'year'"),
                              ("actor==10", "'==' operator on 'actor'"), ("studio%3C%3C%3D=x", "operator")]:
            result = self.run_filter(query, 1)
            self.assertFalse(result.expanded, query)
            self.assertIn(reason, result.status, query)
        result = self.run_filter("studio=golden", None)                                    # no count to check
        self.assertFalse(result.expanded)
        self.assertIn("didn't record", result.status)

    def test_never_guess(self):
        """Anything without a clear, documented meaning stays unexpanded (and says why)."""
        for query, reason in [
            ("title%3C%3C=Aliens", "'<<=' operator on 'title'"),          # strict comparison on text
            ("studio%3E%3E=x", "'>>=' operator on 'studio'"),
            ("year%3C%3C=1970,1980", "several 'year' values"),            # which bound would Plex use?
            ("pop=1&studio=x", "unbalanced brackets"),
            ("push=1&studio=x", "unbalanced brackets"),
            ("push=1&pop=1&studio=x", "empty bracket group"),
            ("studio==Brandywine%2C%20Inc.", "comma inside a value"),
            ("addedAt%3E%3E=-30d", "the 'addedAt' filter"),               # names the field, not the operator
            ("resolution=", "empty 'resolution' value"),                  # nothing, or everything?
            ("title=Zulu,", "empty 'title' value"),
            ("year=1_979", "'year' value"),                               # Python reads 1_979; Plex?
            ("year!=nan", "'year' value"),
        ]:
            result = self.run_filter(query, 1)
            self.assertFalse(result.expanded, query)
            self.assertIn(reason, result.status, query)

    def test_no_conditions_means_the_whole_library(self):
        from projectionist.smart import expand
        uri = "x/library/sections/1/all?type=1&sort=titleSort"
        result = expand(uri, 3, self.movies, {})
        self.assertEqual((sorted(result.members), result.filter_text), ([1, 2, 3], "All movies in the library"))
        self.assertFalse(expand("x/library/sections/1/all?type=2&sort=titleSort", 3, self.movies, {}).expanded)

    def test_more_descriptions(self):
        from projectionist.smart import describe, parse
        cases = {"addedAt%3E%3E=-30d": "Date added in the last 30 days",
                 "year%3C=1990": "Year <= 1990",                              # no invented meaning
                 "unwatched=1,0": "Unwatched is 1 or 0",
                 "unwatched%21=1": "Watched by the owner"}
        for query, text in cases.items():
            self.assertEqual(describe(parse(f"x/library/sections/1/all?{query}")[1], {}), text, query)

    def test_malformed_values_never_crash(self):
        for query in ("director=%C2%B2", "actor=99999999999999999999", "year=%C2%B2", "unwatched=maybe",
                      "push=1&push=1&studio=x", "director=", "%ZZ=%ZZ"):
            result = self.run_filter(query, 1)   # must return, not raise
            self.assertFalse(result.expanded, query)
            self.assertTrue(result.status.startswith("Not expanded"), (query, result.status))

    def test_descriptions(self):
        from projectionist.smart import describe, parse
        uri = "x/library/sections/2/all?type=1&actor=10&actor=11&and=1&and=1&director=12"
        text = describe(parse(uri)[1], {10: "Ti Lung", 11: "David Chiang", 12: "Chang Cheh"})
        self.assertEqual(text, "Actor is Ti Lung and Actor is David Chiang and (Plex's saved filter repeats 'and' "
                               "here) Director is Chang Cheh")
        uri = "x/library/sections/1/all?studio!==Shaw&and=1&title%3C=The&and=1&year%3E%3E=1990"
        self.assertEqual(describe(parse(uri)[1], {}),
                         "Studio is not 'Shaw' and Title begins with 'The' and Year after 1990")


class FileTests(unittest.TestCase):
    def test_finds_newest_dump_and_skips_look_alikes(self):
        with tempfile.TemporaryDirectory() as d:
            build_fixture(os.path.join(d, "com.plexapp.plugins.library.db-2026-09-19"))
            build_fixture(os.path.join(d, "com.plexapp.plugins.library.db-2026-09-25"))
            # Newer, but not usable:
            sqlite3.connect(os.path.join(d, "com.plexapp.plugins.library.blobs.db-2026-09-28")).close()
            for name in ["com.plexapp.plugins.library.db-shm", "Projectionist Movies 2026-09-25.xlsx",
                         "Plex Movies 2026-09-25.xlsx"]:                  # (as the app named them before)
                open(os.path.join(d, name), "w").close()
            with open(os.path.join(d, "Thumbs.db"), "wb") as f:           # Windows thumbnail cache, dated today
                f.write(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 504)
            with zipfile.ZipFile(os.path.join(d, "Plex Media Server Logs_2026-09-26_09-00-00.zip"), "w") as z:
                z.writestr("Plex Media Server.log", "log")                 # from Plex's 'Download logs'
            con = sqlite3.connect(os.path.join(d, "com.plexapp.plugins.library.db-2026-09-27"))
            con.execute("CREATE TABLE unrelated (x)")                      # SQLite, but not a Plex library
            con.close()
            self.assertEqual(os.path.basename(find_newest_database(d)), "com.plexapp.plugins.library.db-2026-09-25")
            with zipfile.ZipFile(os.path.join(d, "Plex Media Server Databases_2026-09-26_10-00-00.zip"), "w") as z:
                z.write(os.path.join(d, "com.plexapp.plugins.library.db-2026-09-25"),
                        "com.plexapp.plugins.library.db")
            self.assertEqual(os.path.basename(find_newest_database(d)),
                             "Plex Media Server Databases_2026-09-26_10-00-00.zip")
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "Thumbs.db"), "w").close()
            self.assertIsNone(find_newest_database(d))
        self.assertFalse(is_candidate_database("com.plexapp.plugins.library.db-wal"))
        self.assertTrue(is_candidate_database("com.plexapp.plugins.library.db"))
        self.assertEqual(default_output_name("x/com.plexapp.plugins.library.db-2026-09-25"),
                         "Projectionist Movies 2026-09-25.xlsx")
        self.assertEqual(dump_date("com.plexapp.plugins.library.db-2026-09-25"), "2026-09-25")


class SpreadsheetAppTests(unittest.TestCase):
    def test_detected_apps(self):
        from projectionist.files import DEFAULT_APP, find_spreadsheet_apps
        apps = find_spreadsheet_apps()
        self.assertEqual(apps[-1][1], None)                   # system default is always offered, last
        for label, program in apps[:-1]:
            self.assertTrue(os.path.isfile(program), (label, program))
        if sys.platform == "win32":
            self.assertEqual(apps[-1][0], DEFAULT_APP)
            calc = [p for label, p in apps if label == "LibreOffice Calc"]
            if os.path.isfile(r"C:\Program Files\LibreOffice\program\scalc.exe"):
                self.assertEqual(apps[0][0], "LibreOffice Calc")   # preferred over Excel
                self.assertTrue(calc[0].lower().endswith(("scalc.exe", "soffice.exe")))

    def test_open_spreadsheet_runs_the_chosen_program(self):
        from projectionist import files
        calls = []
        saved = files.subprocess.Popen
        files.subprocess.Popen = lambda args, **kw: calls.append(args)
        try:
            files.open_spreadsheet("out.xlsx", r"C:\LO\scalc.exe")
        finally:
            files.subprocess.Popen = saved
        self.assertEqual(calls, [[r"C:\LO\scalc.exe", os.path.abspath("out.xlsx")]])

    def test_the_last_choice_is_the_systems_own(self):
        from projectionist import files
        self.assertEqual(files.DEFAULT_APP, "Windows default app" if sys.platform == "win32" else
                         "System default app")
        self.assertEqual(files.find_spreadsheet_apps()[-1], (files.DEFAULT_APP, None))

    def test_away_from_windows_xdg_open_and_the_program_runs_on_by_itself(self):
        """Linux (and a Mac): the system's own app is xdg-open's (open's); a program - LibreOffice, say - runs in
        a session of its own, so closing the app, or the terminal it was started from, leaves the spreadsheet
        open. Nothing is really started: Popen is stubbed."""
        from unittest import mock
        from projectionist import files
        calls = []
        with mock.patch.object(files.subprocess, "Popen", lambda args, **kw: calls.append((args, kw))):
            for platform, program, first in (("linux", None, "xdg-open"), ("darwin", None, "open"),
                                             ("linux", "/usr/bin/libreoffice", "/usr/bin/libreoffice")):
                with mock.patch.object(sys, "platform", platform):
                    files.open_spreadsheet("out.xlsx", program)
                args, kw = calls.pop()
                self.assertEqual(args, [first, os.path.abspath("out.xlsx")], platform)
                self.assertTrue(kw.get("start_new_session"))
                self.assertIs(kw.get("stdin"), files.subprocess.DEVNULL)

    def test_no_xdg_open_is_said_plainly(self):
        """A Linux without xdg-open (a bare window manager, no desktop): the message says what's missing and where
        it comes from, not '[Errno 2] No such file or directory'."""
        from unittest import mock
        from projectionist import files, gui

        def missing(args, **kw):
            raise FileNotFoundError(2, "No such file or directory", args[0])
        with mock.patch.object(files.subprocess, "Popen", missing), mock.patch.object(sys, "platform", "linux"):
            with self.assertRaises(OSError) as caught:
                files.open_spreadsheet("out.xlsx")
            self.assertIn("xdg-open", str(caught.exception))
            self.assertIn("xdg-utils", str(caught.exception))
            self.assertNotIn("Errno", str(caught.exception))
            with self.assertRaises(FileNotFoundError) as caught:              # (a program chosen, and gone)
                files.open_spreadsheet("out.xlsx", "/usr/bin/localc")
            self.assertIn("/usr/bin/localc", str(caught.exception))
            said = []
            with mock.patch.object(gui.messagebox, "showerror", lambda title, text, **kw: said.append(text)):
                gui._open_path("/tmp", select="/tmp/out.xlsx")                # (Open folder)
            self.assertEqual(len(said), 1)
            self.assertIn("xdg-utils", said[0])

    @unittest.skipIf(sys.platform == "win32", "programs found on a Linux PATH (Windows looks in its registry)")
    def test_libreoffice_found_on_linux(self):
        """On the PATH as localc, libreoffice or soffice (distribution packages, the snap); a versioned one from
        libreoffice.org's packages, newest first; else the Flatpak's."""
        from unittest import mock
        from projectionist import files

        def program(folder, name):
            path = os.path.join(folder, name)
            with open(path, "w") as f:
                f.write("#!/bin/sh\n")
            os.chmod(path, 0o755)
            return path
        with tempfile.TemporaryDirectory() as d:
            empty = os.path.join(d, "empty")
            os.makedirs(empty)
            with mock.patch.object(files, "_FLATPAK_CALC", ()):
                self.assertIsNone(files.find_libreoffice(empty))
                bin_ = os.path.join(d, "bin")
                os.makedirs(bin_)
                program(bin_, "libreoffice7.6")
                newest = program(bin_, "libreoffice25.2")
                self.assertEqual(files.find_libreoffice(bin_), newest)          # (25.2 is newer than 7.6)
                soffice = program(bin_, "soffice")
                self.assertEqual(files.find_libreoffice(bin_), soffice)         # (the plain name first)
                self.assertEqual(files.find_libreoffice(os.pathsep.join([empty, bin_])), soffice)
            flatpak = program(d, "org.libreoffice.LibreOffice")
            with mock.patch.object(files, "_FLATPAK_CALC", ("/nowhere/org.libreoffice.LibreOffice", flatpak)):
                self.assertEqual(files.find_libreoffice(empty), flatpak)
            with mock.patch.object(files, "find_libreoffice", lambda: flatpak), \
                    mock.patch.object(files.shutil, "which", lambda name, **kw: None):
                self.assertEqual(files.find_spreadsheet_apps(), [("LibreOffice Calc", flatpak),
                                                                 (files.DEFAULT_APP, None)])


class CommandLineTests(unittest.TestCase):
    def test_cli_export(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "com.plexapp.plugins.library.db-2026-09-25")
            build_fixture(db)
            out = os.path.join(d, "cli.xlsx")
            run = subprocess.run([sys.executable, "-m", "projectionist", db, "-o", out, "--sheets", "cast,crew",
                                  "--library", "Movies"], cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            with zipfile.ZipFile(out) as z:
                wb = z.read("xl/workbook.xml").decode()
            self.assertIn('name="Cast"', wb)
            self.assertNotIn('name="Streams"', wb)
            run = subprocess.run([sys.executable, "-m", "projectionist", d, "--list-libraries"], cwd=ROOT,
                                 capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn("Classics", run.stdout)
            run = subprocess.run([sys.executable, "-m", "projectionist", db, "--library", "Nope"], cwd=ROOT,
                                 capture_output=True, text=True)
            self.assertEqual(run.returncode, 2)

    def test_cli_names_the_app(self):
        from projectionist import __version__
        run = subprocess.run([sys.executable, "-m", "projectionist", "--version"], cwd=ROOT, capture_output=True,
                             text=True)
        self.assertEqual((run.returncode, run.stdout.strip()), (0, f"Projectionist {__version__}"), run.stderr)
        for module in ("projectionist", "projectionist.ask"):
            run = subprocess.run([sys.executable, "-m", module, "--help"], cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn(f"usage: python -m {module} ", run.stdout)
            self.assertIn("Projectionist:", run.stdout)
            self.assertNotIn("plexmovies", run.stdout)
            self.assertNotIn("Exporter", run.stdout)

    def test_cli_output_paths(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "com.plexapp.plugins.library.db-2026-09-25")
            build_fixture(db)
            cli = [sys.executable, "-m", "projectionist", db, "--sheets", "none", "-o"]
            # An existing folder means "save in there", with the usual name.
            os.makedirs(os.path.join(d, "exports"))
            run = subprocess.run(cli + [os.path.join(d, "exports") + os.sep], cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertTrue(os.path.isfile(os.path.join(d, "exports", "Projectionist Movies 2026-09-25.xlsx")))
            self.assertFalse(os.path.exists(os.path.join(d, "exports.xlsx")))
            # A missing folder is created.
            run = subprocess.run(cli + [os.path.join(d, "new", "sub", "out.xlsx")], cwd=ROOT,
                                 capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            if sys.platform == "win32":
                # Windows normalises 'trailing dot.\' to 'trailing dot\' (like Explorer does); either a clean
                # save there or a clean one-line error is fine - never a late crash.
                for folder in ("trailing dot.", "trailing space. "):
                    run = subprocess.run(cli + [os.path.join(d, folder, "out.xlsx")], cwd=ROOT,
                                         capture_output=True, text=True)
                    self.assertIn(run.returncode, (0, 2), run.stderr)
                    self.assertNotIn("Traceback", run.stderr)
                    if run.returncode == 0:
                        saved = run.stdout.split("Saved ", 1)[1].splitlines()[0]
                        self.assertTrue(os.path.isfile(saved), saved)
            # Impossible locations give a one-line error, not a traceback.
            open(os.path.join(d, "afile.txt"), "w").close()
            bad_places = [os.path.join(d, "afile.txt", "out.xlsx")]
            if sys.platform == "win32":                     # (a | in a folder's name is fine elsewhere)
                bad_places.append(os.path.join(d, "bad|name", "out.xlsx"))
            for bad in bad_places:
                run = subprocess.run(cli + [bad], cwd=ROOT, capture_output=True, text=True)
                self.assertEqual(run.returncode, 2, run.stderr)
                self.assertTrue(run.stderr.startswith("error:"), run.stderr)
                self.assertNotIn("Traceback", run.stderr)

    def test_cli_trailing_backslash_quote(self):
        """-o "D:\\Exports\\" reaches Python as 'D:\\Exports"' with the rest of the command line glued on."""
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "com.plexapp.plugins.library.db-2026-09-25")
            build_fixture(db)
            for args in ([db, "-o", os.path.join(d, 'my exports"') + " -s none"], [os.path.join(d, 'dumps" -o x')]):
                run = subprocess.run([sys.executable, "-m", "projectionist"] + args, cwd=ROOT, capture_output=True,
                                     text=True)
                self.assertEqual(run.returncode, 2, run.stderr)
                self.assertIn("quote", run.stderr)
                self.assertNotIn("Traceback", run.stderr)
                self.assertNotIn("Reading movies", run.stderr)   # stopped before doing any work

    def test_cli_output_redirected_with_non_english_names(self):
        """A scheduled job's output goes to a file, in the ANSI code page on Windows - names must not crash it."""
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "com.plexapp.plugins.library.db-2026-09-25")
            build_fixture(db)
            con = sqlite3.connect(db)
            con.execute("UPDATE library_sections SET name = '映画 Кино' WHERE id = 1")
            con.commit()
            con.close()
            env = {k: v for k, v in os.environ.items() if k not in ("PYTHONIOENCODING", "PYTHONUTF8")}
            for extra in (["--list-libraries"], ["-o", os.path.join(d, "Фильмы.xlsx"), "--sheets", "none"]):
                run = subprocess.run([sys.executable, "-m", "projectionist", db] + extra, cwd=ROOT, env=env,
                                     capture_output=True)
                self.assertEqual(run.returncode, 0, run.stderr.decode("utf-8", "replace"))
            self.assertTrue(os.path.isfile(os.path.join(d, "Фильмы.xlsx")))


class GuiSmokeTest(unittest.TestCase):
    def test_window_loads_database(self):
        try:
            import tkinter as tk
            root = tk.Tk()
            root.withdraw()   # headless: never show a window
        except Exception as exc:   # no display available
            self.skipTest(f"Tk unavailable: {exc}")
        from projectionist import files as gui_files
        from projectionist import gui
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "com.plexapp.plugins.library.db-2026-09-25")
            build_fixture(db)
            old = gui.SETTINGS_FILE, gui.SETTINGS_DIR
            gui.SETTINGS_FILE = os.path.join(d, "settings.json")
            gui.SETTINGS_DIR = d
            try:
                root.withdraw()
                app = gui.App(root, db)
                app._pick_initial_db(db)
                root.update()
                self.assertEqual(sorted(app.lib_vars), [1, 2])
                self.assertTrue(app.out_var.get().endswith("Projectionist Movies 2026-09-25.xlsx"))
                self.assertIn("5 movies", app.db_info_var.get())
                self.assertEqual(app.status_var.get(), "Ready - click Export spreadsheet.")
                self.assertEqual(len(app.sheet_vars), len(S.DETAIL_SHEET_NAMES))
                app.db_var.set(db[:-6])        # half-typed path, then focus moves away
                app._db_entry_left(None)
                self.assertEqual(sorted(app.lib_vars), [1, 2])

                # The finished spreadsheet opens in the chosen program, not the .xlsx default app.
                launched = []
                saved_open = gui.open_spreadsheet
                gui.open_spreadsheet = lambda path, program: launched.append((path, program))
                try:
                    app.last_output = db   # any existing file
                    app.open_with_var.set(app.apps[0][0])
                    app.open_output()
                    self.assertEqual(launched, [(db, app.apps[0][1])])
                finally:
                    gui.open_spreadsheet = saved_open
                self.assertEqual(app.apps[-1], (gui_files.DEFAULT_APP, None))
                # ...and the choice is remembered by the next window
                app.open_with_var.set(gui_files.DEFAULT_APP)
                app._remember(open_with=app.open_with_var.get())
                second = tk.Toplevel(root)
                second.withdraw()
                app2 = gui.App(second, db)
                self.assertEqual(app2.open_with_var.get(), gui_files.DEFAULT_APP)
                app2.shutdown()
            finally:
                gui.SETTINGS_FILE, gui.SETTINGS_DIR = old
                app.shutdown()

    def test_export_flow(self):
        try:
            import tkinter as tk
            root = tk.Tk()
            root.withdraw()   # headless: never show a window
        except Exception as exc:
            self.skipTest(f"Tk unavailable: {exc}")
        from projectionist import export, gui
        from projectionist.export import OutputError
        shown = []
        patches = {"showwarning": lambda *a, **k: shown.append(("warning",) + a),
                   "showerror": lambda *a, **k: shown.append(("error",) + a),
                   "askyesno": lambda *a, **k: True}
        saved = {name: getattr(gui.messagebox, name) for name in patches}
        saved_csv = export.write_csv
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "com.plexapp.plugins.library.db-2026-09-25")
            build_fixture(db)
            junk = os.path.join(d, "junk.db")
            with open(junk, "wb") as f:
                f.write(b"not a database" * 50)
            old = gui.SETTINGS_FILE, gui.SETTINGS_DIR
            gui.SETTINGS_FILE, gui.SETTINGS_DIR = os.path.join(d, "settings.json"), d
            for name, fn in patches.items():
                setattr(gui.messagebox, name, fn)

            def failing_csv(*a, **k):
                raise OutputError("Movies.csv is open in another program")
            export.write_csv = failing_csv
            try:
                root.withdraw()
                app = gui.App(root, db)
                app._pick_initial_db(db)
                # A path that failed to load can be typed in again and is retried.
                app.load_db(junk)
                self.assertEqual(app.lib_vars, {})
                app.db_var.set(db)
                app._db_entry_left(None)
                self.assertEqual(sorted(app.lib_vars), [1, 2])
                # A folder in 'Save spreadsheet as' means "save in there".
                out_dir = os.path.join(d, "exports")
                os.makedirs(out_dir)
                app.out_var.set(out_dir)
                app.open_var.set(False)
                app.csv_var.set(True)
                app.start_export()
                deadline = time.time() + 60
                while (app.worker and app.worker.is_alive()) or not app.queue.empty():
                    root.update()
                    time.sleep(0.02)
                    self.assertLess(time.time(), deadline)
                root.update()
                expected = os.path.join(out_dir, "Projectionist Movies 2026-09-25.xlsx")
                # The CSV step failed, but the spreadsheet was saved - and the app says so.
                self.assertEqual(app.last_output, expected)
                self.assertTrue(os.path.isfile(expected))
                self.assertTrue(app.status_var.get().startswith("Spreadsheet saved"), app.status_var.get())
                self.assertEqual(str(app.open_btn["state"]), "normal")
                self.assertTrue(any(s[0] == "warning" and "was saved" in s[2] for s in shown), shown)
                self.assertFalse(any(s[0] == "error" for s in shown), shown)
            finally:
                export.write_csv = saved_csv
                for name, fn in saved.items():
                    setattr(gui.messagebox, name, fn)
                gui.SETTINGS_FILE, gui.SETTINGS_DIR = old
                app.shutdown()


class SettingsMigrationTests(unittest.TestCase):
    """The app was called Plex Movie Exporter and kept its settings in %APPDATA%\\PlexMovieExporter; as Projectionist
    it copies them across once. Every test uses a temporary APPDATA (or temporary paths) - never the real one."""

    SAVED = {"window_geometry": "1400x900", "excluded_sheets": ["Streams", "Chapters"],
             "excluded_libraries": ["Home Videos"], "doctor_languages": ["en", "ja"], "open_with": "LibreOffice Calc"}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.appdata = self.tmp.name                  # %APPDATA% on Windows; the home folder anywhere else
        self.old_file = os.path.join(self.appdata, "PlexMovieExporter", "settings.json")
        if sys.platform == "win32":
            self.new_file = os.path.join(self.appdata, "Projectionist", "settings.json")
        elif sys.platform == "darwin":
            self.new_file = os.path.join(self.appdata, "Library", "Application Support", "Projectionist",
                                         "settings.json")
        else:
            self.new_file = os.path.join(self.appdata, ".config", "projectionist", "settings.json")

    def tearDown(self):
        self.tmp.cleanup()

    def write_old(self, data):
        os.makedirs(os.path.dirname(self.old_file), exist_ok=True)
        with open(self.old_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        past = time.time() - 3 * 86400
        os.utime(self.old_file, (past, past))
        with open(self.old_file, "rb") as f:
            return f.read(), os.stat(self.old_file).st_mtime_ns

    def run_gui(self, code):
        """Run code in a fresh Python whose APPDATA - or away from Windows, whose home folder - is the temporary
        folder (the paths are worked out on import); -> what it printed, as JSON."""
        env = dict(os.environ, APPDATA=self.appdata)
        if sys.platform != "win32":
            env["HOME"] = self.appdata
            env.pop("XDG_CONFIG_HOME", None)
        run = subprocess.run([sys.executable, "-B", "-c", "import json; from projectionist import gui; " + code],
                             cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(run.returncode, 0, run.stderr)
        return json.loads(run.stdout)

    def test_old_settings_are_copied_across_once_and_left_in_place(self):
        before, mtime = self.write_old(self.SAVED)
        got = self.run_gui("print(json.dumps([gui.SETTINGS_DIR, gui.SETTINGS_FILE, gui.OLD_SETTINGS_FILE, "
                           "gui.migrate_settings(), gui.load_settings()]))")
        settings_dir, settings_file, old_file, migrated, loaded = got
        self.assertEqual(os.path.normcase(settings_dir), os.path.normcase(os.path.dirname(self.new_file)))
        self.assertEqual(os.path.normcase(settings_file), os.path.normcase(self.new_file))
        self.assertEqual(os.path.normcase(old_file), os.path.normcase(self.old_file))
        self.assertTrue(migrated)
        self.assertEqual(loaded, self.SAVED)                  # window size, sheets, libraries, languages...
        with open(self.new_file, "rb") as f:
            self.assertEqual(f.read(), before)                # an exact copy
        with open(self.old_file, "rb") as f:
            self.assertEqual(f.read(), before)                # the old folder is left as it was
        self.assertEqual(os.stat(self.old_file).st_mtime_ns, mtime)
        self.assertEqual(sorted(os.listdir(os.path.dirname(self.new_file))), ["settings.json"])   # no .tmp left
        # Once Projectionist has settings of its own, the old ones are never copied over them again.
        with open(self.new_file, "w", encoding="utf-8") as f:
            json.dump({"window_geometry": "1000x700"}, f)
        self.write_old({"window_geometry": "640x480"})
        got = self.run_gui("print(json.dumps([gui.migrate_settings(), gui.load_settings()]))")
        self.assertEqual(got, [False, {"window_geometry": "1000x700"}])

    def test_nothing_to_carry_over(self):
        self.assertEqual(self.run_gui("print(json.dumps([gui.migrate_settings(), gui.load_settings()]))"),
                         [False, {}])
        self.assertEqual(os.listdir(self.appdata), [])        # no empty Projectionist folder made either

    def test_where_each_system_keeps_them(self):
        """%APPDATA%\\Projectionist on Windows (as always); ~/.config/projectionist on Linux - or wherever
        XDG_CONFIG_HOME says, when that's a full path; ~/Library/Application Support/Projectionist on a Mac.
        Away from Windows, where 1.0.0 kept them (~/Projectionist) is carried across first, then the old name's."""
        from projectionist import gui
        folder, earlier = gui.settings_places("win32", {"APPDATA": r"C:\Users\x\AppData\Roaming"})
        self.assertEqual(os.path.basename(folder), "Projectionist")
        self.assertEqual([os.path.basename(os.path.dirname(f)) for f in earlier], ["PlexMovieExporter"])
        self.assertEqual(gui.settings_places("linux", {"HOME": "/home/x"}),
                         ("/home/x/.config/projectionist",
                          ["/home/x/Projectionist/settings.json", "/home/x/PlexMovieExporter/settings.json"]))
        self.assertEqual(gui.settings_places("linux", {"HOME": "/home/x", "XDG_CONFIG_HOME": "/data/cfg"})[0],
                         "/data/cfg/projectionist")
        self.assertEqual(gui.settings_places("linux", {"HOME": "/home/x", "XDG_CONFIG_HOME": "cfg"})[0],
                         "/home/x/.config/projectionist")          # (a relative one is ignored, as the standard says)
        self.assertEqual(gui.settings_places("darwin", {"HOME": "/Users/x"})[0],
                         "/Users/x/Library/Application Support/Projectionist")

    def test_the_home_folder_copy_comes_across_first(self):
        """Away from Windows, 1.0.0 kept its settings in ~/Projectionist: they're the ones carried across, before
        the old name's."""
        from unittest import mock
        from projectionist import gui
        self.write_old({"window_geometry": "640x480"})
        home_copy = os.path.join(self.appdata, "Projectionist", "settings.json")
        os.makedirs(os.path.dirname(home_copy), exist_ok=True)
        with open(home_copy, "w", encoding="utf-8") as f:
            json.dump({"window_geometry": "1400x900"}, f)
        new_file = os.path.join(self.appdata, "config", "projectionist", "settings.json")
        with mock.patch.multiple(gui, SETTINGS_DIR=os.path.dirname(new_file), SETTINGS_FILE=new_file,
                                 OLD_SETTINGS_FILE=self.old_file, EARLIER_SETTINGS_FILES=[home_copy]):
            self.assertTrue(gui.migrate_settings())
            self.assertEqual(gui.load_settings(), {"window_geometry": "1400x900"})
            self.assertFalse(gui.migrate_settings())               # (once only)
        with open(home_copy, encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"window_geometry": "1400x900"})      # (left where it was)

    def test_a_copy_that_fails_leaves_nothing_behind(self):
        from unittest import mock
        from projectionist import gui
        self.write_old(self.SAVED)
        new_dir = os.path.dirname(self.new_file)
        with mock.patch.multiple(gui, SETTINGS_DIR=new_dir, SETTINGS_FILE=self.new_file,
                                 OLD_SETTINGS_FILE=self.old_file, EARLIER_SETTINGS_FILES=[]), \
                mock.patch.object(gui.shutil, "copyfile", side_effect=PermissionError(13, "Access is denied")):
            self.assertFalse(gui.migrate_settings())
        self.assertEqual(os.listdir(new_dir), [])
        with mock.patch.multiple(gui, SETTINGS_DIR=new_dir, SETTINGS_FILE=self.new_file,
                                 OLD_SETTINGS_FILE=self.old_file, EARLIER_SETTINGS_FILES=[]):
            self.assertTrue(gui.migrate_settings())            # and the next start tries again
            self.assertEqual(gui.load_settings(), self.SAVED)

    def test_the_window_migrates_before_it_reads_its_settings(self):
        """gui.main (what Projectionist.pyw and 'python -m projectionist' open) copies the old settings before the
        window loads them. Tk is stubbed: no window is made."""
        import types
        from unittest import mock
        from projectionist import gui
        order = []
        root = mock.MagicMock()
        # main() shows a real error box when XlsxWriter can't be imported (e.g. a test run with APPDATA pointed
        # elsewhere hides the user's site-packages): make the import succeed and the box a stub, always.
        with mock.patch.object(gui, "migrate_settings", side_effect=lambda: order.append("migrate")), \
                mock.patch.object(gui, "App", side_effect=lambda *a: order.append("window")), \
                mock.patch.object(gui.tk, "Tk", return_value=root), \
                mock.patch.object(gui, "_enable_dpi_awareness"), \
                mock.patch.object(gui, "_share_time_with_the_window", return_value=lambda: None), \
                mock.patch.dict(sys.modules, {"xlsxwriter": sys.modules.get("xlsxwriter")
                                              or types.ModuleType("xlsxwriter")}), \
                mock.patch.object(gui, "messagebox") as box:
            self.assertEqual(gui.main(), 0)
        box.showerror.assert_not_called()
        self.assertEqual(order, ["migrate", "window"])
        root.mainloop.assert_called_once()


def make_catalog(specs, libraries=("Movies",)):
    """A Catalog built in memory: specs are dicts of Film fields, with 'cast'/'directors' as lists of names."""
    from projectionist.catalog import Catalog, Credit, Film, Person
    films, people = {}, {}
    for i, spec in enumerate(specs):
        spec = dict(spec)
        cast = [Credit(f"p:{n}", n, f"role {j}", j) for j, n in enumerate(spec.pop("cast", []), 1)]
        directors = [Credit(f"p:{n}", n, "Director", j) for j, n in enumerate(spec.pop("directors", []), 1)]
        film = Film(key=f"f{i}", plex_ids=[i + 1], libraries=spec.pop("libraries", [libraries[0]]),
                    cast=cast, directors=directors, **spec)
        films[film.key] = film
        for c in cast:
            people.setdefault(c.person, Person(c.person, c.name)).acted[film.key] = c
        for c in directors:
            people.setdefault(c.person, Person(c.person, c.name)).directed.add(film.key)
    return Catalog(films, people, "Owner", "memory", list(libraries))


class CatalogTests(FixtureTestCase):
    def setUp(self):
        from projectionist.catalog import load
        self.catalog = load(self.db)

    def test_one_film_per_movie(self):
        c = self.catalog
        self.assertEqual(len(c.films), 5)
        matrix = c.films[MATRIX_GUID]
        self.assertEqual((matrix.title, matrix.year, matrix.libraries, matrix.plex_ids),
                         ("The Matrix", 1999, ["Movies"], [1799]))
        self.assertEqual([d.name for d in matrix.directors], ["Lana Wachowski", "Lilly Wachowski"])
        self.assertEqual([(x.name, x.role, x.order) for x in matrix.cast[:2]],
                         [("Keanu Reeves", "Neo", 1), ("Laurence Fishburne", "Morpheus", 2)])
        self.assertEqual((matrix.imdb_rating, matrix.rt_critic, matrix.resolution), (8.7, 83, "4K"))
        self.assertEqual(matrix.credits.verdict, "Yes")
        self.assertIsNone(matrix.owner_rating)                       # the friend's 9.0 isn't the owner's
        self.assertIn("imdb:tt0062622", c.films)                      # legacy agent: keyed by IMDb ID
        hk = c.films["plex://movie/hk1"]                              # an edition: keyed by the film itself
        self.assertEqual((hk.editions, hk.owner_rating, hk.owner_plays), (["Extended"], 8.0, 1))
        self.assertEqual([d.name for d in hk.directors], ["Yuen Woo-ping"])
        self.assertTrue(hk.watched)

    def test_editions_merge_into_one_film(self):
        f = Fixture.__new__(Fixture)
        f.con, f._tag_ids = sqlite3.connect(self.db), {}
        f.insert("metadata_items", id=701, library_section_id=2, metadata_type=1, title="Hong Kong Action",
                 guid="plex://movie/hk1/edition/Theatrical", edition_title="Theatrical", year=1985)
        f.insert("metadata_item_settings", account_id=1, guid="plex://movie/hk1/edition/Theatrical",
                 view_count=2, rating=6.0)
        f.close()
        try:
            from projectionist.catalog import load
            hk = load(self.db).films["plex://movie/hk1"]
            self.assertEqual(sorted(hk.plex_ids), [700, 701])
            self.assertEqual(sorted(hk.editions), ["Extended", "Theatrical"])
            self.assertEqual(sorted(hk.libraries), ["Classics", "Movies"])
            self.assertEqual((hk.owner_rating, hk.owner_plays), (7.0, 3))   # ratings averaged, plays added up
        finally:
            con = sqlite3.connect(self.db)
            con.execute("DELETE FROM metadata_items WHERE id = 701")
            con.execute("DELETE FROM metadata_item_settings WHERE guid LIKE '%Theatrical'")
            con.commit()
            con.close()

    def test_a_rating_left_on_an_old_guid_still_counts(self):
        # Rated before Plex gave the copy an edition GUID: the rating stays on the old one, so the film would read as
        # unrated. It counts - but never over a rating on a GUID a copy still has, nor for someone else's account.
        f = Fixture.__new__(Fixture)
        f.con, f._tag_ids = sqlite3.connect(self.db), {}
        f.insert("metadata_item_settings", account_id=1, guid=MATRIX_GUID + "/edition/Old Cut", rating=9.0)
        f.insert("metadata_item_settings", account_id=1, guid="plex://movie/hk1", rating=4.0)
        f.insert("metadata_item_settings", account_id=42, guid="plex://movie/gone", rating=5.0)
        f.close()
        try:
            from projectionist.catalog import load
            c = load(self.db)
            self.assertEqual(c.films[MATRIX_GUID].owner_rating, 9.0)             # unrated -> the old GUID's 9
            self.assertEqual(c.films["plex://movie/hk1"].owner_rating, 8.0)      # its own 8 wins over the old 4
            # ...and the Film page can say where it came from (Plex shows that copy as unrated)
            self.assertTrue(c.films[MATRIX_GUID].owner_rating_from_earlier_edition)
            self.assertFalse(c.films["plex://movie/hk1"].owner_rating_from_earlier_edition)
            self.assertTrue(c.films[MATRIX_GUID].to_dict()["your_rating_from_earlier_edition"])
        finally:
            con = sqlite3.connect(self.db)
            con.execute("DELETE FROM metadata_item_settings WHERE guid IN (?, ?, ?)",
                        (MATRIX_GUID + "/edition/Old Cut", "plex://movie/hk1", "plex://movie/gone"))
            con.commit()
            con.close()

    def test_the_spreadsheet_counts_the_same_ratings_as_the_app(self):
        # A rating left on an edition's old GUID: the Movies sheet takes it by the app's rule - only for a film none
        # of whose copies has a rating of its own, then on every copy of it - and says where it came from. Plex's
        # own view stays where it was: Watch Status, and the smart collections Plex filters on ratings.
        f = Fixture.__new__(Fixture)
        f.con, f._tag_ids = sqlite3.connect(self.db), {}
        try:
            f.insert("metadata_items", id=1950, library_section_id=2, metadata_type=1, title="The Matrix",
                     guid=MATRIX_GUID + "/edition/4K", edition_title="4K", year=1999)     # a second copy, unrated
            f.insert("metadata_items", id=751, library_section_id=2, metadata_type=1, title="Hong Kong Action",
                     guid="plex://movie/hk1/edition/Theatrical", edition_title="Theatrical", year=1985)
            f.insert("metadata_item_settings", account_id=1, guid=MATRIX_GUID + "/edition/Old Cut", rating=9.0)
            f.insert("metadata_item_settings", account_id=1, guid="plex://movie/hk1", rating=4.0)
            f.insert("metadata_item_settings", account_id=42, guid="plex://movie/sf75/edition/Old", rating=5.0)
        finally:
            f.close()
        try:
            from projectionist.catalog import load
            c = load(self.db)
            result = extract(self.db)
            movies = {r["plex_id"]: r for r in result.sheet(S.MOVIES).rows}
            # every copy of the Matrix: the earlier edition's 9, marked as such
            for pid in (1799, 1950):
                self.assertEqual((movies[pid]["owner_rating"], movies[pid]["owner_rating_source"]),
                                 (9.0, "An earlier edition"), pid)
            # Hong Kong Action has a rating of its own (8, on copy 700): its other copy stays unrated, the old 4 unused
            self.assertEqual((movies[700]["owner_rating"], movies[700]["owner_rating_source"]), (8.0, "This copy"))
            self.assertIsNone(movies[751].get("owner_rating"))
            self.assertNotIn("owner_rating_source", movies[751])
            self.assertIsNone(movies[710].get("owner_rating"))                   # a friend's rating isn't the owner's
            # the sheet and the app count the same rated films, with the same ratings
            sheet = {}
            for r in movies.values():
                if r.get("owner_rating") is not None:
                    sheet.setdefault(r["plex_movie_id"] or f"plex:{r['plex_id']}", set()).add(r["owner_rating"])
            app = {k: {x.owner_rating} for k, x in c.films.items() if x.owner_rating is not None}
            self.assertEqual(sheet, app)
            # Plex's own view: Watch Status shows the copy as unrated...
            status = [s for s in result.sheet(S.WATCH_STATUS).rows if s["plex_id"] in (1799, 1950)]
            self.assertEqual([s["rating"] for s in status if s["user"] == "Owner"], [None])   # (the friend's 9 is theirs)
            # ...and a smart collection filtering on the owner's rating is re-run as Plex runs it (still just 700)
            members = defaultdict_list((r["collection"], r["plex_id"]) for r in result.sheet(S.COLLECTIONS).rows)
            self.assertEqual(members["Owner Favourites"], [700])
            # the column is explained in the About sheet's dictionary
            self.assertIn("owner_rating_source", [col.key for col in S.MOVIE_COLUMNS])
            self.assertIn("Plex shows this copy as unrated",
                          next(col.desc for col in S.MOVIE_COLUMNS if col.key == "owner_rating_source"))
        finally:
            con = sqlite3.connect(self.db)
            con.execute("DELETE FROM metadata_items WHERE id IN (1950, 751)")
            con.execute("DELETE FROM metadata_item_settings WHERE guid IN (?, ?, ?)",
                        (MATRIX_GUID + "/edition/Old Cut", "plex://movie/hk1", "plex://movie/sf75/edition/Old"))
            con.commit()
            con.close()

    def test_finding_people_and_films(self):
        c = self.catalog
        person, others, how = c.find_person("keanu")
        self.assertEqual((person.name, how), ("Keanu Reeves", "partial"))
        self.assertEqual(c.find_person("Fishburne Laurence")[0].name, "Laurence Fishburne")   # any word order
        self.assertEqual(c.find_person("Laurence Fishbourne")[2], "guess")
        self.assertEqual(c.find_person("nobody at all")[0], None)
        self.assertEqual(c.find_person("(.*")[0], None)                 # regex characters are just characters
        film, _, how = c.find_film("matrix")
        self.assertEqual((film.title, how), ("The Matrix", "exact"))    # a leading 'The' doesn't matter
        self.assertEqual(c.find_film("The Matrix (1999)")[0].year, 1999)
        self.assertIsNone(c.find_film("The Matrix (2003)")[0])

    def test_folding_keeps_every_alphabet(self):
        from projectionist.catalog import ascii_fold, fold
        self.assertEqual(fold("Shintarō Katsu"), "shintaro katsu")
        self.assertEqual(fold("Николь Арбур"), "николь арбур")
        self.assertEqual((fold("Søren"), ascii_fold("Søren")), ("søren", "soren"))
        self.assertEqual(fold("The Matrix", drop_article=True), "matrix")

    def test_every_copy_counts(self):
        """Each copy's title can be searched and keeps its own credits result; a shared GUID counts once."""
        f = Fixture.__new__(Fixture)
        f.con, f._tag_ids = sqlite3.connect(self.db), {}
        copy = f.insert("metadata_items", id=702, library_section_id=2, metadata_type=1,
                        title="Hong Kong Action: Uncut", guid="plex://movie/hk1/edition/Extended",
                        edition_title="Extended", year=1985)
        mi = f.insert("media_items", metadata_item_id=copy, duration=5_400_000)
        f.insert("media_parts", media_item_id=mi, file="/x/hk2.mkv", duration=5_400_000)
        f.tag(copy, 12, "", 0, "credits", start=5_000_000, end=5_100_000, extra={"pv:version": "5"})
        f.tag(copy, 12, "", 1, "credits", start=5_160_000, end=5_400_000, extra={"pv:final": "1"})
        f.close()
        try:
            from projectionist.catalog import load
            c = load(self.db)
            hk = c.films["plex://movie/hk1"]
            self.assertEqual(c.find_film("Hong Kong Action: Uncut")[0], hk)
            self.assertEqual((hk.owner_plays, hk.owner_rating), (1, 8.0))    # one settings row, counted once
            self.assertEqual([(x.plex_id, x.info.verdict) for x in hk.credits_copies], [(702, "Yes")])
            self.assertEqual(hk.credits.verdict, "Yes")
        finally:
            con = sqlite3.connect(self.db)
            con.execute("DELETE FROM metadata_items WHERE id = 702")
            con.execute("DELETE FROM taggings WHERE metadata_item_id = 702")
            con.commit()
            con.close()

    def test_assistants_never_become_directors(self):
        self.assertEqual(self.catalog.films["plex://movie/sf75"].directors, [])
        legacy = self.catalog.films["imdb:tt0062622"]          # no directing tags at all: Plex's plain list
        self.assertEqual([d.name for d in legacy.directors], ["Stanley Kubrick"])


class RecommenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        specs = []
        # 20 films by a director you love and 20 by one you don't, all with the same middling IMDb score.
        for i in range(20):
            specs.append(dict(title=f"Loved {i}", year=1975 + i, genres=["Action"], directors=["Ann Auteur"],
                              cast=["Star A", f"Extra {i}"], imdb_rating=7.0, owner_rating=9.0, runtime_min=95))
            specs.append(dict(title=f"Meh {i}", year=1975 + i, genres=["Drama"], directors=["Bob Bland"],
                              cast=["Star B", f"Other {i}"], imdb_rating=7.0, owner_rating=4.0, runtime_min=130))
        specs += [
            dict(title="New Ann", year=1999, genres=["Action"], directors=["Ann Auteur"], cast=["Star A"],
                 imdb_rating=7.0, runtime_min=90, summary="A lone swordsman seeks revenge."),
            dict(title="New Bob", year=1999, genres=["Drama"], directors=["Bob Bland"], cast=["Star B"],
                 imdb_rating=7.0, runtime_min=140, summary="A family drama."),
            dict(title="Second Ann", year=2001, genres=["Action"], directors=["Ann Auteur"], cast=["Star A"],
                 imdb_rating=6.9, runtime_min=92),
            dict(title="Third Ann", year=2002, genres=["Action"], directors=["Ann Auteur"], cast=["Star A"],
                 imdb_rating=6.8, runtime_min=93),
            dict(title="Seen Ann", year=2003, genres=["Action"], directors=["Ann Auteur"], cast=["Star A"],
                 imdb_rating=7.0, owner_plays=1),
        ]
        cls.catalog = make_catalog(specs)

    def ask(self, **request):
        from projectionist.recommend import recommend
        return recommend(self.catalog, request)

    def test_learns_your_taste(self):
        answer = self.ask(count=10, max_per_director=0)
        titles = [r["title"] for r in answer["results"]]
        self.assertEqual(titles[-1], "New Bob")
        self.assertEqual(set(titles[:3]), {"New Ann", "Second Ann", "Third Ann"})
        self.assertNotIn("Seen Ann", titles)                      # played already
        top = answer["results"][0]
        self.assertGreater(top["predicted_rating"], 7.5)
        self.assertLess(answer["results"][-1]["predicted_rating"], 5.5)
        self.assertTrue(any("Ann Auteur" in r and "above what their scores suggest" in r for r in top["reasons"]),
                        top["reasons"])
        self.assertEqual(top["confidence"], "high")
        self.assertEqual(top["similar_films_you_rated"][0]["your_rating"], 9.0)
        self.assertEqual(answer["model"]["trained_on"], 40)

    def test_filters_and_variety(self):
        self.assertEqual([r["title"] for r in self.ask(genres="drama")["results"]], ["New Bob"])
        self.assertEqual([r["title"] for r in self.ask(max_runtime=91)["results"]], ["New Ann"])
        self.assertEqual([r["title"] for r in self.ask(text="swordsman revenge")["results"]], ["New Ann"])
        self.assertEqual(self.ask(text="swordsman unicorn")["results"], [])        # every word must match
        self.assertEqual(len(self.ask()["results"]), 3)                             # 2 per director by default
        self.assertEqual({r["title"] for r in self.ask(directed_by="bob bland")["results"]}, {"New Bob"})
        self.assertIn("Seen Ann", [r["title"] for r in self.ask(include_watched=True, max_per_director=0)["results"]])
        personal = self.ask(sort="personal", max_per_director=0)
        self.assertNotIn("New Bob", [r["title"] for r in personal["results"]])      # below your average
        like = self.ask(like="Loved 3", max_per_director=0)
        self.assertEqual(like["results"][0]["title"][-3:], "Ann")
        self.assertIn("likeness", like["results"][0])
        self.assertFalse(self.ask(like="No Such Film")["ok"])
        self.assertFalse(self.ask(sort="sideways")["ok"])

    def test_evaluation_beats_guessing_your_average(self):
        from projectionist.recommend import evaluate
        report = evaluate(self.catalog, {"folds": 4, "repeats": 1, "lambdas": [8]})
        self.assertTrue(report["ok"])
        model = report["model_by_lambda"]["8"]
        self.assertLess(model["mean_error"], report["baselines"]["your average for everything"]["mean_error"])
        self.assertEqual(report["rated_films"], 40)

    def test_needs_enough_ratings(self):
        from projectionist.recommend import evaluate, recommend
        small = make_catalog([dict(title=f"T{i}", year=2000, owner_rating=7.0) for i in range(5)])
        self.assertFalse(recommend(small, {})["ok"])
        self.assertIn("rate at least", recommend(small, {})["error"])
        self.assertFalse(evaluate(small)["ok"])


class CostarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = make_catalog([
            dict(title="One", year=1990, cast=["Ann", "Bob"], libraries=["Asia"]),
            dict(title="Two", year=1991, cast=["Bob", "Cat"], libraries=["Asia"]),
            dict(title="Three", year=1992, cast=["Cat", "Dan"], libraries=["West"]),
            dict(title="Four", year=1993, cast=["Eve", "Ann"], libraries=["Asia"]),
            dict(title="Five", year=1994, cast=["Dan", "Extra", "Eve"], libraries=["West"]),
            dict(title="Six", year=1995, cast=["Zed", "Yan"], libraries=["West"]),
        ] + [dict(title=f"Gang {i}", year=2000 + i, cast=["Kuo", "Lu", "Chiang", "Sun"], libraries=["Asia"])
             for i in range(5)], libraries=("Asia", "West"))

    def graph(self, **kw):
        from projectionist.costars import Graph
        return Graph(self.catalog, **kw)

    def test_shortest_chain_through_the_biggest_roles(self):
        g = self.graph()
        path = g.connect("p:Ann", "p:Dan")
        self.assertEqual(path["degrees"], 2)                                  # Ann-Eve (Four), Eve-Dan (Five)
        self.assertEqual([(s["from"], s["film"]["title"], s["to"]) for s in path["steps"]],
                         [("Ann", "Four", "Eve"), ("Eve", "Five", "Dan")])
        self.assertEqual(path["shortest_chains"], 1)
        around = g.connect("p:Ann", "p:Dan", avoid={"p:Eve"})                  # the long way round
        self.assertEqual((around["degrees"], [s["to"] for s in around["steps"]]), (3, ["Bob", "Cat", "Dan"]))
        self.assertIsNone(g.connect("p:Ann", "p:Zed"))
        self.assertEqual(g.connect("p:Ann", "p:Ann")["degrees"], 0)
        self.assertEqual(self.graph(max_billing=2).connect("p:Ann", "p:Dan")["degrees"], 3)   # Eve billed 3rd

    def test_reach_center_troupes_bridges(self):
        from projectionist.costars import bridges, profile
        g = self.graph()
        self.assertEqual(g.reach("p:Ann"), [2, 3])            # Bob, Eve / then Cat, Dan and Extra
        center = g.center(candidates=20)
        self.assertEqual([(r["name"], r["reaches"], r["average_steps"]) for r in center[:2]],
                         [("Dan", 5, 1.4), ("Eve", 5, 1.4)])              # a tie, broken by name
        self.assertEqual(center[-1]["reaches"], 1)                           # Zed's island comes last
        troupes = g.troupes(min_shared=5, max_billing=4)
        self.assertEqual(troupes[0]["members"], ["Chiang", "Kuo", "Lu", "Sun"])
        self.assertEqual(troupes[0]["films_with_all_of_them"], 5)
        linked = bridges(self.catalog, {"library": "Asia"}, {"library": "West"})
        self.assertEqual({p["name"] for p in linked["people"]}, {"Cat", "Eve"})
        # Films in both groups link nothing: Asia vs the 1990s leaves One, Two and Four out.
        overlap = bridges(self.catalog, {"library": "Asia"}, {"decade": 1990})
        self.assertEqual((overlap["films_in_both_left_out"], overlap["people_in_both"]), (3, 0))
        with self.assertRaises(ValueError):
            bridges(self.catalog, {"planet": "Mars"}, {"library": "West"})
        with self.assertRaises(ValueError):
            bridges(self.catalog, {}, {"library": "West"})
        who = profile(self.catalog, g, self.catalog.people["p:Eve"])
        self.assertEqual((who["films"], who["distinct_costars"]), (2, 3))


class CancelAfter:
    """Stands in for a background job (projectionist.jobs) that is called off once it has been asked `n` times whether
    it has been - so a test can see a loop stop part-way, not just before it starts."""
    key = None

    def __init__(self, n):
        self.n, self.asked = n, 0

    @property
    def cancelled(self):
        self.asked += 1
        return self.asked > self.n


class JobTests(unittest.TestCase):
    def test_check_does_nothing_outside_a_job(self):
        from projectionist import jobs
        self.assertIsNone(jobs.current())
        jobs.check()
        self.assertFalse(jobs.cancelled())

    def test_a_cancelled_job_stops_at_its_next_check(self):
        from projectionist import jobs
        job = jobs.Job("tab.search")
        with jobs.running(job):
            self.assertIs(jobs.current(), job)
            jobs.check()
            job.cancel()
            self.assertTrue(jobs.cancelled())
            with self.assertRaises(jobs.Cancelled):
                jobs.check()
            self.assertFalse(issubclass(jobs.Cancelled, Exception))     # an `except Exception` lets it through
        self.assertIsNone(jobs.current())
        jobs.check()                                                     # the thread's next work isn't affected

    def test_a_job_belongs_to_its_thread_and_jobs_nest(self):
        from projectionist import jobs
        outer, inner = jobs.Job("outer"), jobs.Job("inner")
        seen = []
        with jobs.running(outer):
            outer.cancel()
            other = threading.Thread(target=lambda: (jobs.check(), seen.append(jobs.current())))
            other.start()
            other.join(5)
            with jobs.running(inner):
                jobs.check()                                            # the inner job is still going
            self.assertIs(jobs.current(), outer)
        self.assertEqual(seen, [None])                                  # another thread has no job

    def test_cancel_tidies_up_once_and_not_after_the_end(self):
        from projectionist import jobs
        calls = []
        job = jobs.Job()
        job._on_cancel = lambda: calls.append("tidied")
        job.cancel()
        job.cancel()
        self.assertEqual(calls, ["tidied"])
        finished = jobs.Job()
        finished._on_cancel = lambda: calls.append("too late")
        finished.ended = True
        finished.cancel()
        self.assertFalse(finished.cancelled)
        self.assertEqual(calls, ["tidied"])


class CancellationTests(unittest.TestCase):
    """The long loops in the backbones stop part-way when their job is called off - and nothing half-made is kept."""

    @classmethod
    def setUpClass(cls):
        specs = []
        for i in range(20):
            specs.append(dict(title=f"Loved {i}", year=1975 + i, genres=["Action"], directors=["Ann Auteur"],
                              cast=["Star A", f"Extra {i}"], imdb_rating=7.0, owner_rating=9.0, runtime_min=95))
            specs.append(dict(title=f"Meh {i}", year=1975 + i, genres=["Drama"], directors=["Bob Bland"],
                              cast=["Star B", f"Other {i}"], imdb_rating=7.0, owner_rating=4.0, runtime_min=130))
        # ...and a long chain of films, each sharing one actor with the next: a search goes 300 steps deep
        specs += [dict(title=f"Link {i}", year=2000, cast=[f"Link {i}", f"Link {i + 1}"]) for i in range(300)]
        cls.specs = specs

    def setUp(self):
        self.catalog = make_catalog(self.specs)

    def stops(self, fn, n=3):
        """fn() stops with Cancelled at the check after the n-th, having got that far."""
        from projectionist import jobs
        job = CancelAfter(n)
        with jobs.running(job):
            with self.assertRaises(jobs.Cancelled):
                fn()
        self.assertEqual(job.asked, n + 1)

    def test_the_recommender_stops_part_way(self):
        from projectionist import recommend as R
        rated = [f for f in self.catalog.films.values() if f.owner_rating is not None]
        self.stops(lambda: R.fit([R.features(f) for f in rated], [f.owner_rating for f in rated]), n=2)
        self.stops(lambda: R.Recommender(self.catalog), n=50)
        self.stops(lambda: R.evaluate(self.catalog, {"folds": 4, "repeats": 1, "lambdas": [8]}), n=10)
        self.stops(lambda: R.recommend(self.catalog, {"count": 5}), n=100)   # while fitting...
        self.assertEqual(self.catalog.cache, {})                             # ...and no half-fitted model is kept
        R.recommender(self.catalog)
        self.stops(lambda: R.recommend(self.catalog, {"count": 5}), n=20)    # while going through the films
        self.stops(lambda: R.taste_profile(self.catalog), n=1)

    def test_the_costar_searches_stop_part_way(self):
        from projectionist import costars as CS
        self.stops(lambda: CS.Graph(self.catalog), n=100)
        self.assertEqual(self.catalog.cache, {})
        g = CS.graph_for(self.catalog)
        self.assertEqual(g.connect("p:Link 0", "p:Link 300")["degrees"], 300)
        self.stops(lambda: g.layers("p:Link 0"), n=50)                       # a check a layer (or 2,048 people)
        self.stops(lambda: g.connect("p:Link 0", "p:Link 300"), n=250)
        self.stops(lambda: g.center(candidates=20), n=30)
        self.stops(lambda: g.troupes(min_shared=2, max_billing=3, min_size=2), n=40)
        self.stops(lambda: CS.bridges(self.catalog, {"genre": "Action"}, {"genre": "Drama"}), n=10)

    def test_the_overview_stops_part_way(self):
        from projectionist import insights
        self.stops(lambda: insights.overview(self.catalog), n=5)

    def test_a_job_that_goes_on_gets_the_same_answers(self):
        from projectionist import jobs
        from projectionist.ask import handle
        requests = [{"action": "evaluate", "folds": 4, "repeats": 1, "lambdas": [8]}, {"action": "overview"},
                    {"action": "connect", "from_id": "p:Link 0", "to_id": "p:Link 300"},
                    {"action": "center", "candidates": 10}, {"action": "recommend", "count": 5}]
        plain = [handle(r, catalog=make_catalog(self.specs)) for r in requests]
        with jobs.running(jobs.Job("never called off")):
            in_job = [handle(r, catalog=make_catalog(self.specs)) for r in requests]
        for a in plain + in_job:
            a.pop("searched_ms", None)
        self.assertTrue(all(a["ok"] for a in plain), plain)
        self.assertEqual(in_job, plain)


class CatalogCancelTests(FixtureTestCase):
    def test_a_load_called_off_stops_with_cancelled_not_a_read_error(self):
        from projectionist import catalog as K, jobs
        # between queries...
        with jobs.running(CancelAfter(2)):
            with self.assertRaises(jobs.Cancelled):
                K.load(self.db)
        # ...and in the middle of one: SQLite interrupts it, and that isn't taken for a damaged database
        old = K.CANCEL_STEPS
        K.CANCEL_STEPS = 1
        try:
            job = CancelAfter(3)
            with jobs.running(job):
                with self.assertRaises(jobs.Cancelled):
                    K.load(self.db)
            self.assertGreater(job.asked, 3)
            with jobs.running(jobs.Job()):                             # a job that goes on reads everything
                self.assertEqual(len(K.load(self.db).films), 5)
        finally:
            K.CANCEL_STEPS = old


class AskTests(FixtureTestCase):
    def ask(self, **request):
        from projectionist.ask import handle
        return handle(request, self.db)

    def test_answers(self):
        self.assertIn("connect", self.ask(action="help")["actions"])
        info = self.ask(action="info")
        self.assertEqual((info["films"], info["films_you_rated"]), (5, 1))
        film = self.ask(action="film", title="matrix")
        self.assertEqual((film["film"]["title"], film["credits"]["stay_after_credits"]), ("The Matrix", "Yes"))
        self.assertEqual(film["credits"]["scenes"][0]["starts_at"], "2:08:32")
        listing = self.ask(action="credits")
        self.assertEqual([f["title"] for f in listing["films"]], ["The Matrix"])
        linked = self.ask(action="connect", **{"from": "keanu", "to": "fishburne"})
        self.assertEqual((linked["degrees"], linked["steps"][0]["film"]["title"]), (1, "The Matrix"))
        self.assertEqual(linked["matched"][0]["how"], "partial")
        self.assertFalse(self.ask(action="recommend")["ok"])                  # only one rating in the fixture
        self.assertEqual(self.ask(action="person", name="Keanu Reeves")["distinct_costars"], 2)

    def test_bad_requests_are_answers_not_crashes(self):
        from projectionist.ask import handle, to_json
        self.assertFalse(self.ask(action="dance")["ok"])
        self.assertIn("suggestions", self.ask(action="connect", **{"from": "Zzqxv", "to": "Keanu"}))
        self.assertFalse(handle(["not", "a", "dict"], self.db)["ok"])
        self.assertFalse(handle({"action": "info"}, os.path.join(self.tmp.name, "missing.db"))["ok"])
        for bad in ({"action": "recommend", "count": "lots"}, {"action": "credits", "count": float("inf")},
                    {"action": "recommend", "lambda": -1}, {"action": "recommend", "lambda": "NaN"},
                    {"action": "connect", "from": "Keanu", "to": "Keanu", "max_billing": 1e999},
                    {"action": "bridges", "a": {"planet": "Mars"}, "b": "Movies"},
                    {"action": "bridges", "a": {}, "b": "Movies"}):
            answer = handle(bad, self.db)
            self.assertFalse(answer["ok"], bad)
            self.assertTrue(answer["error"].startswith("bad request"), answer)
        self.assertEqual(to_json({"x": float("nan"), "y": [float("inf")]}), '{"x": null, "y": [null]}')
        self.assertTrue(self.ask(action="troupes", min_shared=1, max_billing=99)["ok"])   # clamped, not refused

    def test_request_files_in_any_windows_encoding(self):
        import contextlib
        import io
        from projectionist.ask import main
        for encoding in ("utf-8", "utf-8-sig", "utf-16", "cp1252"):
            path = os.path.join(self.tmp.name, f"request-{encoding}.json")
            with open(path, "w", encoding=encoding) as fh:
                fh.write('{"action": "film", "title": "The Matrix"}')
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(["--db", self.db, path])
            self.assertEqual(code, 0, encoding)
            self.assertEqual(json.loads(out.getvalue())["film"]["title"], "The Matrix")

    def test_command_line(self):
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        out = subprocess.run([sys.executable, "-m", "projectionist.ask", "--db", self.db,
                              '[{"action": "info"}, {"action": "film", "title": "2001"}]'],
                             cwd=ROOT, capture_output=True, text=True, encoding="utf-8", env=env, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr)
        answers = json.loads(out.stdout)["answers"]
        self.assertEqual(answers[1]["film"]["title"], "2001: A Space Odyssey")
        bad = subprocess.run([sys.executable, "-m", "projectionist.ask", "--db", self.db, "{not json"],
                             cwd=ROOT, capture_output=True, text=True, encoding="utf-8", env=env, timeout=120)
        self.assertEqual(bad.returncode, 2)
        self.assertFalse(json.loads(bad.stdout)["ok"])


if __name__ == "__main__":
    unittest.main()
