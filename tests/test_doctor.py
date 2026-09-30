"""Tests for the Library Doctor backbone (projectionist/doctor.py): every check on a purpose-built fixture database,
the request/answer contract, the Film page hooks and saving a list. Nothing here opens a window or a saved file."""

import json
import os
import re
import sys
import tempfile
import unittest
import zipfile
from xml.etree import ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from projectionist import doctor as D  # noqa: E402
from projectionist import jobs  # noqa: E402
from projectionist.ask import handle, to_json  # noqa: E402
from projectionist.export import OutputError, _umask  # noqa: E402
from test_projectionist import J, Fixture, make_catalog  # noqa: E402

MIN = 60_000
PART_DELETED_AT = 1_780_000_000          # 2026-05-28 UTC


def _slug(title):
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def add_version(f, mid, lib, v):
    """One copy (media item) of library item `mid`: v = {w, h, dur_ms, mbps or size, picture_mbps (the video
    track's own rate: none by default), codec, hdr_trc, audio: [(lang, channels, default)], subs: [(lang, forced)],
    streams, parts, path, hash, deleted, mi_deleted}."""
    w, h = v.get("w", 1920), v.get("h", 1080)
    dur = v.get("dur_ms", 100 * MIN)
    size = v.get("size") or round(v.get("mbps", 10) * 1e6 * (dur or 6_000_000) / 1000 / 8)
    bitrate = v.get("bitrate", round(size * 8 / (dur / 1000)) if dur else None)
    codec = v.get("codec", "hevc")
    mi = f.insert("media_items", library_section_id=lib, metadata_item_id=mid, width=w, height=h, size=size,
                  duration=dur, bitrate=bitrate, container="mkv", video_codec=codec, color_trc=v.get("hdr_trc"),
                  created_at=1_700_000_000, deleted_at=v.get("mi_deleted"))
    parts = v.get("parts", 1)
    path = v.get("path") or f"/disk1/Movies/{mid}/copy {mi}.mkv"
    part_ids = []
    for p in range(parts):
        file = path if parts == 1 else path.replace(".mkv", f" CD{p + 1}.mkv")
        part_ids.append(f.insert("media_parts", media_item_id=mi, file=file, size=size // parts,
                                 duration=(dur // parts) if dur else None, hash=v.get("hash"), index=p,
                                 created_at=1_700_000_000, deleted_at=v.get("deleted")))
    if v.get("streams", True):
        for pid in part_ids:          # a split copy repeats its tracks in every file
            picture = v.get("picture_mbps")
            f.insert("media_streams", stream_type_id=1, media_item_id=mi, media_part_id=pid, codec=codec,
                     index=0, default=1, bitrate=round(picture * 1e6) if picture else None,
                     extra_data=J({"ma:colorTrc": v["hdr_trc"]}) if v.get("hdr_trc") else None)
            for i, (lang, ch, default) in enumerate(v.get("audio", [("en", 6, 1)]), 1):
                f.insert("media_streams", stream_type_id=2, media_item_id=mi, media_part_id=pid, codec="ac3",
                         language=lang, index=i, default=default, forced=0, channels=ch)
            for i, (lang, forced) in enumerate(v.get("subs", [("en", 0)]), 10):
                f.insert("media_streams", stream_type_id=3, media_item_id=mi, media_part_id=pid, codec="srt",
                         language=lang, index=i, default=0, forced=forced)
    return mi


def movie(f, mid, title, year, lib=1, guid=None, edition="", rating=None, friend_rating=None,
          genres=("Drama",), imdb=7.0, versions=(), duration=None):
    """duration: how long Plex says the film runs (ms) - none by default."""
    guid = guid or f"plex://movie/{_slug(title)}" + (f"/edition/{edition}" if edition else "")
    f.insert("metadata_items", id=mid, library_section_id=lib, metadata_type=1, guid=guid, title=title,
             title_sort=title, year=year, edition_title=edition, added_at=1_700_000_000, duration=duration)
    for i, g in enumerate(genres):
        f.tag(mid, 1, g, index=i)
    if imdb is not None:
        f.tag(mid, 316, "imdb://image.rating", 0, str(imdb), tag_extra={"at:type": "audience"})
    if rating is not None:
        f.insert("metadata_item_settings", account_id=1, guid=guid, rating=rating, view_count=1,
                 last_viewed_at=1_750_000_000)
    if friend_rating is not None:
        f.insert("metadata_item_settings", account_id=42, guid=guid, rating=friend_rating, view_count=3)
    for v in versions:
        add_version(f, mid, lib, v)
    return guid


def doctor_fixture(path):
    """A small library with one example of everything the doctor looks for."""
    f = Fixture(path)
    f.insert("library_sections", id=1, name="Movies", section_type=1, agent="tv.plex.agents.movie")
    f.insert("library_sections", id=2, name="Movies-World", section_type=1, agent="tv.plex.agents.movie")
    f.insert("library_sections", id=5, name="TV", section_type=2)
    f.insert("section_locations", library_section_id=1, root_path="/disk1/Movies")
    f.insert("section_locations", library_section_id=2, root_path="/disk2/World")
    f.insert("accounts", id=1, name="Owner")
    f.insert("accounts", id=42, name="friend")
    both = [("en", 6, 1), ("zh", 2, 0)]
    # 1. Two releases of one cut: Eureka is the weaker (11.6 against 19.4 Mbit/s, same everything else)
    for mid, ed, rate in ((101, "Eureka", 11.6), (102, "Twilight Time", 19.4)):
        movie(f, mid, "Temple Kicks", 1978, lib=2, guid=f"plex://movie/temple-kicks/edition/{ed}", edition=ed,
              versions=[{"dur_ms": int(108.4 * MIN), "mbps": rate, "audio": both, "subs": [("en", 0)],
                         "path": f"/disk2/World/Temple Kicks ({ed}).mkv"}])
    # 2. Two cuts by name: never duplicates, whatever the resolutions
    movie(f, 111, "Alien", 1979, guid="plex://movie/alien/edition/Theatrical", edition="Theatrical",
          versions=[{"w": 3840, "h": 1600, "dur_ms": int(116.6 * MIN), "mbps": 30}])
    movie(f, 112, "Alien", 1979, guid="plex://movie/alien/edition/Director's Cut", edition="Director's Cut",
          versions=[{"dur_ms": int(115.8 * MIN), "mbps": 10}])
    # 3. An unnamed copy and a 'Special Edition' of the same length: a likely duplicate, neither weaker
    movie(f, 121, "The Garden Party", 1963, versions=[{"w": 720, "h": 400, "dur_ms": 115 * MIN, "mbps": 1.8}])
    movie(f, 122, "The Garden Party", 1963, edition="Special Edition",
          versions=[{"w": 720, "h": 400, "dur_ms": 115 * MIN, "mbps": 1.9}])
    # 4. 3D and 2D: a different format by name
    movie(f, 131, "Avengers", 2012, versions=[{"dur_ms": 143 * MIN, "mbps": 10}])
    movie(f, 132, "Avengers", 2012, edition="3D SBS", versions=[{"dur_ms": 143 * MIN, "mbps": 10, "codec": "h264"}])
    # 5. Same cut, but the lower copy has something the other lacks (or isn't worse enough)
    movie(f, 141, "Blocker Lang", 2001, versions=[{"w": 1280, "h": 720, "dur_ms": 90 * MIN, "mbps": 5,
                                                    "audio": [("en", 6, 1), ("fr", 2, 0)]}])
    movie(f, 142, "Blocker Lang", 2001, edition="Criterion",
          versions=[{"w": 1280, "h": 720, "dur_ms": 90 * MIN, "mbps": 8, "audio": [("en", 6, 1)]}])
    movie(f, 143, "Blocker HDR", 2002, versions=[{"dur_ms": 95 * MIN, "mbps": 10, "hdr_trc": "smpte2084"}])
    movie(f, 144, "Blocker HDR", 2002, edition="Arrow Video",
          versions=[{"w": 3840, "h": 2160, "dur_ms": 95 * MIN, "mbps": 20}])
    movie(f, 145, "Blocker Rate", 2003, edition="Eureka", versions=[{"dur_ms": 97 * MIN, "mbps": 9}])
    movie(f, 146, "Blocker Rate", 2003, edition="Criterion", versions=[{"dur_ms": 97 * MIN, "mbps": 10}])
    # 6. One library item with two versions: a DVD rip split over two files, and a 1080p file Plex never
    #    analysed (no running time, no tracks)
    movie(f, 151, "Two Versions", 1950, versions=[
        {"w": 720, "h": 480, "dur_ms": 60 * MIN, "size": 1_400_000_000, "parts": 2, "audio": [("en", 2, 1)],
         "subs": [("fr", 0)], "path": r"D:\Old\Two Versions.mkv"},
        {"dur_ms": None, "size": 4_000_000_000, "streams": False, "bitrate": None,
         "path": "\\\\nas\\share\\Films\\Two Versions 1080p.mkv"}])
    # 7. Languages ('jpn' and 'ja-JP' are Japanese)
    movie(f, 161, "Jp No Subs", 1960, lib=2, versions=[{"audio": [("jpn", 2, 1)], "subs": []}])
    movie(f, 162, "Jp Forced", 1961, lib=2, versions=[{"audio": [("ja", 2, 1)], "subs": [("en", 1)]}])
    movie(f, 163, "Jp Subbed", 1962, lib=2, versions=[{"audio": [("ja-JP", 2, 1)], "subs": [("en", 0), ("zh", 0)]}])
    movie(f, 164, "Jp Dub", 1963, lib=2, versions=[{"audio": [("ja", 2, 1), ("en", 2, 0)], "subs": []}])
    # 8. A soundtrack with no language tag
    movie(f, 171, "Blank Lang", 1990, versions=[{"audio": [("", 2, 1)], "subs": []}])
    # 9. Upgrade candidates: the owner's 8 on a DVD-quality film - not 7.5, not a friend's 10, not with a 1080p copy
    movie(f, 181, "Rated Eight", 1980, rating=8.0, versions=[{"w": 720, "h": 480, "mbps": 1.6}])
    movie(f, 182, "Rated Seven Half", 1981, rating=7.5, versions=[{"w": 720, "h": 480, "mbps": 1.6}])
    movie(f, 183, "Rated Eight Twice", 1982, rating=8.0, versions=[{"w": 720, "h": 480, "mbps": 1.6}])
    movie(f, 184, "Rated Eight Twice", 1982, edition="Extended", rating=8.0,
          versions=[{"dur_ms": 120 * MIN, "mbps": 10}])
    movie(f, 185, "Friend Ten", 1983, friend_rating=10.0, versions=[{"w": 720, "h": 480, "mbps": 1.6}])
    # 10. Sizes: 21 ordinary 1080p files at 10 Mbit/s make 10 the usual rate; three 720p files are too few to say
    for n in range(21):
        movie(f, 300 + n, f"Filler {n:02d}", 2000 + n, versions=[{"mbps": 10}])
    movie(f, 191, "Huge File", 1995, versions=[{"mbps": 40}])
    movie(f, 192, "Tiny File", 1996, versions=[{"mbps": 2}])
    movie(f, 193, "Tiny Short", 1997, versions=[{"mbps": 2, "dur_ms": 10 * MIN}])
    movie(f, 194, "Seven Twenty A", 1998, versions=[{"w": 1280, "h": 720, "mbps": 30}])
    # 11. Missing details: unmatched, no genres and no IMDb rating, no file at all
    movie(f, 201, "Local Film", 2010, guid="local://201", genres=(), imdb=None, versions=[{"mbps": 10}])
    movie(f, 202, "Bare Film", 2011, genres=(), imdb=None, versions=[{"mbps": 10}])
    movie(f, 203, "No File Film", 2012)
    movie(f, 204, "Full Film", 2013, genres=("Action", "Drama"), imdb=8.1, versions=[{"mbps": 10}])
    # 12. A copy whose file Plex has marked unavailable (the film's other copy is fine)
    movie(f, 211, "Gone Film", 2014, versions=[{"mbps": 10}, {"mbps": 40, "deleted": PART_DELETED_AT,
                                                            "path": "/disk3/Gone/Gone Film.mkv"}])
    # 13. Names with accents; one title and year, two films
    movie(f, 221, "Dracula", 1931, versions=[{"mbps": 10}])
    movie(f, 222, "Drácula", 1931, guid="plex://movie/dracula-spanish", versions=[{"mbps": 10}])
    movie(f, 223, "=Formula Looking Title", 1912, versions=[{"w": 1280, "h": 720, "mbps": 3}])
    # 14. The pictures' own data rates: 19.4 against 11.6 Mbit/s for the whole files (ten lossless soundtracks
    #     make the difference) but 13.22 against 10.87 for the pictures - not a fifth less, so not weaker
    for mid, ed, rate, picture in ((231, "Eureka", 11.6, 10.87), (232, "Twilight Time", 19.4, 13.22)):
        movie(f, mid, "Picture Close", 1979, lib=2, guid=f"plex://movie/picture-close/edition/{ed}", edition=ed,
              versions=[{"dur_ms": 111 * MIN, "mbps": rate, "picture_mbps": picture, "audio": both}])
    #     ...a much worse picture, but the only mono soundtrack (often the original mix): not weaker either
    movie(f, 233, "Mono Mix", 1964, versions=[{"mbps": 5, "picture_mbps": 4.8, "audio": [("en", 1, 1)]}])
    movie(f, 234, "Mono Mix", 1964, edition="Criterion",
          versions=[{"mbps": 10, "picture_mbps": 9.5, "audio": [("en", 6, 1)]}])
    #     ...nor with a second English subtitle track the better copy lacks
    movie(f, 235, "Sub Count", 1965, versions=[{"mbps": 6, "picture_mbps": 5.5, "subs": [("en", 0), ("en", 0)]}])
    movie(f, 236, "Sub Count", 1965, edition="Arrow Video", versions=[{"mbps": 10, "picture_mbps": 9.5}])
    # 15. Files far shorter than the film Plex matched them to: half the film is missing (or it's another film) -
    #     but not a short version by name, nor a short clip
    movie(f, 241, "Half Film", 1940, duration=120 * MIN, versions=[{"dur_ms": 55 * MIN, "mbps": 10}])
    movie(f, 242, "Short Cut", 1941, edition="Short", duration=76 * MIN, versions=[{"dur_ms": 27 * MIN, "mbps": 10}])
    movie(f, 243, "Whole Film", 1942, duration=120 * MIN, versions=[{"dur_ms": 119 * MIN, "mbps": 10}])
    # 16. A 1080p picture cropped at the sides (Plex calls it 720p) isn't worth upgrading; a real 720p one is
    movie(f, 251, "Cropped Nine", 2014, rating=9.0, versions=[{"w": 1374, "h": 1036, "mbps": 13}])
    movie(f, 252, "Real Seven Twenty", 2015, rating=7.5, versions=[{"w": 1280, "h": 720, "mbps": 5}])
    # 17. Rated before Plex gave the copy an edition: the rating stays on the old GUID, and Plex shows the copy as
    #     unrated - but not when the copy has a rating of its own too
    movie(f, 261, "Renamed Cut", 1986, guid="plex://movie/renamed-cut/edition/Director's Cut",
          edition="Director's Cut", versions=[{"mbps": 10}])
    f.insert("metadata_item_settings", account_id=1, guid="plex://movie/renamed-cut", rating=9.0, view_count=1)
    movie(f, 262, "Rerated Cut", 1987, guid="plex://movie/rerated-cut/edition/Extended", edition="Extended",
          rating=6.0, versions=[{"mbps": 10}])
    f.insert("metadata_item_settings", account_id=1, guid="plex://movie/rerated-cut", rating=9.0, view_count=1)
    # not a movie
    f.insert("metadata_items", id=900, library_section_id=5, metadata_type=2, title="Some Show", guid="plex://show/1")
    f.close()


def load_fixture(path):
    from projectionist.catalog import load
    doctor_fixture(path)
    return load(path)


def key_of(catalog, title, year=None):
    films = [f for f in catalog.films.values() if f.title == title and (year is None or f.year == year)]
    assert len(films) == 1, (title, [f.key for f in films])
    return films[0].key


class DoctorFixtureCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = os.path.join(cls.tmp.name, "com.plexapp.plugins.library.db-2026-09-25")
        cls.catalog = load_fixture(cls.db)
        cls.answer = D.answer(cls.catalog, {})

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def rows(self, issue, answer=None):
        return (answer or self.answer)["issues"][issue]["rows"]

    def films(self, issue, answer=None):
        return [r["film"] for r in self.rows(issue, answer)]


# ---------------------------------------------------------------------------------------------------------
class CopiesTests(DoctorFixtureCase):
    def test_every_copy_is_read(self):
        data = D.copies_data(self.catalog)
        by_title = {}
        for c in data["copies"]:
            by_title.setdefault(c["title"], []).append(c)
        self.assertEqual(len(by_title["Two Versions"]), 2)           # one item, two versions
        dvd, hd = by_title["Two Versions"]
        self.assertEqual((dvd["resolution"], len(dvd["files"]), dvd["size"]), ("480p", 2, 1_400_000_000))
        self.assertEqual([t["code"] for t in dvd["audio"]], ["en"])  # the tracks of one file, not both
        self.assertFalse(hd["analysed"])
        self.assertEqual(hd["duration_ms"], 0)
        self.assertNotIn("Some Show", by_title)
        self.assertNotIn("No File Film", by_title)
        self.assertEqual({c["code"] for t in by_title["Jp No Subs"] for c in t["audio"]}, {"ja"})
        self.assertEqual({c["code"] for t in by_title["Jp Subbed"] for c in t["audio"]}, {"ja"})
        gone = [c for c in by_title["Gone Film"] if c["unavailable"]]
        self.assertEqual(len(gone), 1)
        self.assertEqual(gone[0]["unavailable_since"], PART_DELETED_AT)
        hdr = by_title["Blocker HDR"][0]
        self.assertEqual(hdr["hdr"], "HDR10")

    def test_totals(self):
        t = self.answer["totals"]
        self.assertEqual(t["films"], len(self.catalog.films))
        data = D.copies_data(self.catalog)
        self.assertEqual(t["copies"], len(data["copies"]))
        self.assertEqual(t["available"], len(data["copies"]) - 1)
        sizes = sum(c["size"] for c in data["copies"] if not c["unavailable"])
        self.assertEqual(t["size_bytes"], sizes)
        self.assertEqual(self.answer["disk"]["total_bytes"], sizes)
        self.assertEqual(t["files"], sum(len(c["files"]) for c in data["copies"] if not c["unavailable"]))


class DuplicateTests(DoctorFixtureCase):
    def test_same_cut_twice(self):
        dups = {r["film"]: r for r in self.rows("duplicates")}
        self.assertEqual(set(dups), {"Temple Kicks (1978)", "The Garden Party (1963)", "Blocker Lang (2001)",
                                     "Blocker HDR (2002)", "Blocker Rate (2003)", "Two Versions (1950)",
                                     "Picture Close (1979)", "Mono Mix (1964)", "Sub Count (1965)"})
        dm = dups["Temple Kicks (1978)"]
        self.assertEqual(dm["copies"], 2)
        self.assertEqual(dm["keep"], "Twilight Time")
        self.assertIn("label or release wording", dm["why"])
        self.assertIn("both run 108 min", dm["why"])
        eureka = next(c for c in D.copies_data(self.catalog)["copies"]
                      if c["edition"] == "Eureka" and c["title"] == "Temple Kicks")
        self.assertEqual(dm["extra_gb"], round(eureka["size"] / 1e9, 2))      # all but the best copy
        # the Why says which copy the Extra GB is, and what makes the other the one to keep
        self.assertIn(f"The extra {D.size_text(eureka['size'])} is 'Eureka'; 'Twilight Time' has more data (19.4 "
                      f"against 11.6 Mbit/s).", dm["why"])
        pink = dups["The Garden Party (1963)"]
        self.assertIn("the copy with no edition name", pink["why"])
        # 1.9 against 1.8 Mbit/s and the same tracks: too close to call
        self.assertTrue(pink["why"].endswith("The two copies are too close to call: the extra 1.6 GB is the copy "
                                             "with no edition name."), pink["why"])
        two = dups["Two Versions (1950)"]
        self.assertIn("running time of one copy is unknown", two["why"])
        self.assertIn("Neither copy has an edition name", two["why"])
        self.assertIn("has a higher resolution (1080p against 480p)", two["why"])
        self.assertIn("'Twilight Time' has more picture data (13.2 against 10.9 Mbit/s).",
                      dups["Picture Close (1979)"]["why"])
        self.assertIn("The extra 3.8 GB is the copy with no edition name; 'Criterion' has more picture data (9.5 "
                      "against 4.8 Mbit/s), but the other has a mono English soundtrack.",
                      dups["Mono Mix (1964)"]["why"])
        self.assertIn("but the other has an extra English subtitle track.", dups["Sub Count (1965)"]["why"])
        self.assertTrue(dups["Blocker HDR (2002)"]["why"].endswith(
            "'Arrow Video' has a higher resolution (4K against 1080p), but the other has HDR10."))

    def test_editions_are_not_duplicates(self):
        editions = {r["film"]: r for r in self.rows("editions")}
        self.assertIn("Alien (1979)", editions)
        self.assertIn("Avengers (2012)", editions)
        self.assertIn("Rated Eight Twice (1982)", editions)
        self.assertNotIn("Alien (1979)", self.films("duplicates"))
        self.assertNotIn("Alien (1979)", self.films("weaker"))
        alien = editions["Alien (1979)"]
        self.assertEqual(alien["minutes"], "117 / 116")
        self.assertIn("Different cuts or formats by name (Theatrical, Director's Cut)", alien["why"])
        self.assertIn("3D SBS", editions["Avengers (2012)"]["why"])
        # the same title and year under two keys are two films, never copies of one
        self.assertNotIn("Dracula (1931)", self.films("editions") + self.films("duplicates"))
        self.assertNotIn("Drácula (1931)", self.films("editions") + self.films("duplicates"))

    def test_same_cut_rules(self):
        base = {"files": [{"path": "/a.mkv", "hash": ""}], "size": 1, "duration_ms": 100 * MIN}
        a = dict(base, edition="Arrow Video Theatrical")
        b = dict(base, edition="Theatrical", files=[{"path": "/b.mkv", "hash": ""}])
        self.assertEqual(D.same_cut(a, b), (True, "release"))
        c = dict(b, edition="Director's Cut")
        self.assertEqual(D.same_cut(a, c), (False, "names"))
        d = dict(b, edition="Arrow Video", duration_ms=103 * MIN)
        self.assertEqual(D.same_cut(dict(a, edition="Arrow Video"), d), (False, "runtime"))
        e = dict(b, edition="40th Anniversary Edition")
        self.assertEqual(D.same_cut(dict(a, edition=""), e)[0], True)
        self.assertEqual(D.same_cut(dict(a, edition="3D-SBS"), dict(b, edition=""))[1], "names")
        self.assertEqual(D.same_cut(a, dict(a, edition="Something Else"))[1], "same_path")
        self.assertEqual(D.edition_words("Arrow Video Director's Cut"), ["arrow", "video", "director's", "cut"])
        self.assertEqual(D.edition_words("Eureka!"), ["eureka"])


class WeakerTests(DoctorFixtureCase):
    def test_weaker_copy(self):
        weaker = {r["film"]: r for r in self.rows("weaker")}
        dm = weaker["Temple Kicks (1978)"]
        self.assertEqual(dm["edition"], "Eureka")
        self.assertTrue(dm["better"].startswith("Twilight Time · 1080p · 19.4 Mbit/s"))
        eureka = next(c for c in D.copies_data(self.catalog)["copies"]
                      if c["edition"] == "Eureka" and c["title"] == "Temple Kicks")
        self.assertEqual(dm["size_gb"], round(eureka["size"] / 1e9, 2))
        # (Plex recorded no picture rates here: the whole files' rates, and it says so)
        self.assertIn("19.4 against 11.6 Mbit/s at 1080p, whole-file rates", dm["why"])
        self.assertIn("every soundtrack and subtitle this one has", dm["why"])
        self.assertIn("removing this frees", dm["why"])
        self.assertTrue(dm["compared"])
        self.assertIsNone(dm["picture_mbps"])
        self.assertTrue(dm["better"].endswith("19.4 Mbit/s whole file"), dm["better"])
        self.assertIn("picture_mbps", [c["key"] for c in self.answer["issues"]["weaker"]["columns"]])
        self.assertNotIn("safe to remove", self.answer["issues"]["weaker"]["explanation"])

    def test_the_pictures_own_rates(self):
        """A file's rate counts its soundtracks too: the pictures' own rates are compared when Plex has them."""
        self.assertNotIn("Picture Close (1979)", self.films("weaker"))           # 13.22 against 10.87
        copies = [c for c in D.copies_data(self.catalog)["copies"] if c["title"] == "Picture Close"]
        eureka = next(c for c in copies if c["edition"] == "Eureka")
        tt = next(c for c in copies if c["edition"] == "Twilight Time")
        self.assertEqual((eureka["picture_bitrate"], tt["picture_bitrate"]), (10_870_000, 13_220_000))
        self.assertIsNone(D.weaker_than(eureka, tt))
        self.assertIsNotNone(D.weaker_than(dict(eureka, picture_bitrate=0), tt))  # (whole files: 11.6 against 19.4)
        worse = dict(eureka, picture_bitrate=9_000_000)
        self.assertEqual(D.weaker_than(worse, tt), ("13.2 against 9.0 Mbit/s of picture at 1080p", True))
        self.assertIsNone(D.weaker_than(tt, eureka))

    def test_nothing_the_better_copy_lacks(self):
        films = self.films("weaker")
        for blocked in ("Blocker Lang (2001)", "Blocker HDR (2002)", "Blocker Rate (2003)",
                        "The Garden Party (1963)", "Mono Mix (1964)", "Sub Count (1965)"):
            self.assertIn(blocked, self.films("duplicates"))
            self.assertNotIn(blocked, films)
        copies = D.copies_data(self.catalog)["copies"]
        mono = next(c for c in copies if c["title"] == "Mono Mix" and not c["edition"])
        crit = next(c for c in copies if c["title"] == "Mono Mix" and c["edition"])
        self.assertEqual(D._lacks(mono, crit), ["a mono English soundtrack"])
        self.assertEqual(D._lacks(crit, mono), [])

    def test_unanalysed_better_copy(self):
        row = next(r for r in self.rows("weaker") if r["film"] == "Two Versions (1950)")
        self.assertEqual(row["resolution"], "480p")
        self.assertIn("1080p against 480p", row["why"])
        self.assertIn("hasn't analysed the better copy", row["why"])
        self.assertFalse(row["compared"])


class LanguageTests(DoctorFixtureCase):
    def test_no_subtitles_you_read(self):
        subs = {r["film"]: r for r in self.rows("subtitles")}
        self.assertEqual(set(subs), {"Jp No Subs (1960)", "Jp Forced (1961)", "Jp Dub (1963)"})
        self.assertEqual(subs["Jp No Subs (1960)"]["why"],
                         "Its soundtrack is in Japanese and it has no English subtitles.")
        self.assertIn("only forced ones", subs["Jp Forced (1961)"]["why"])
        self.assertTrue(subs["Jp Dub (1963)"]["dubbed"])
        self.assertIn("dubbed", subs["Jp Dub (1963)"]["why"])
        self.assertFalse(subs["Jp No Subs (1960)"]["dubbed"])
        self.assertEqual(subs["Jp Forced (1961)"]["subtitles"], "English (forced)")

    def test_no_audio_in_your_languages(self):
        audio = {r["film"]: r for r in self.rows("audio")}
        self.assertEqual(set(audio), {"Jp No Subs (1960)", "Jp Forced (1961)", "Jp Subbed (1962)"})
        self.assertEqual(audio["Jp Subbed (1962)"]["your_subtitles"], "Yes")
        self.assertEqual(audio["Jp No Subs (1960)"]["your_subtitles"], "No")
        self.assertEqual(audio["Jp Forced (1961)"]["your_subtitles"], "No")
        self.assertEqual(audio["Jp Subbed (1962)"]["why"],
                         "Its only soundtrack is in Japanese; it has English subtitles.")
        self.assertIn("Japanese (Dolby Digital 2.0)", audio["Jp Subbed (1962)"]["soundtracks"])

    def test_untagged_audio(self):
        self.assertEqual(self.films("unknown_audio"), ["Blank Lang (1990)"])
        self.assertEqual(self.rows("unknown_audio")[0]["why"], "Soundtrack 1 of 1 (Dolby Digital 2.0) has no "
                                                               "language tag.")
        self.assertNotIn("Blank Lang (1990)", self.films("subtitles") + self.films("audio"))

    def test_your_languages(self):
        both = D.answer(self.catalog, {"languages": ["en", "ja"]})
        self.assertEqual(self.films("subtitles", both), [])
        self.assertEqual(self.films("audio", both), [])
        self.assertEqual([x["code"] for x in both["languages"]], ["en", "ja"])
        words = D.answer(self.catalog, {"languages": "English, jpn"})
        self.assertEqual([x["code"] for x in words["languages"]], ["en", "ja"])
        self.assertEqual(D.parse_languages(["ja-JP", "Japanese", "jpn"]), ["ja"])
        self.assertEqual(D.parse_languages(None), ["en"])
        self.assertEqual(D.parse_languages(""), ["en"])
        with self.assertRaises(ValueError):
            D.parse_languages(["Klingon"])
        japanese_only = D.answer(self.catalog, {"languages": ["ja"]})
        # a Japanese speaker is fine with the Japanese films but not with the English-only ones
        self.assertNotIn("Jp No Subs (1960)", self.films("subtitles", japanese_only))
        self.assertIn("Full Film (2013)", self.films("subtitles", japanese_only))
        self.assertIn("Japanese", japanese_only["summary"][ISSUE_INDEX["subtitles"]]["note"])

    def test_many_languages_still_read(self):
        """Over three languages aren't listed by name, and the sentences still read."""
        five = D.answer(self.catalog, {"languages": ["en", "de", "it", "th", "ko"]})
        notes = {s["id"]: s["note"] for s in five["summary"]}
        self.assertEqual(notes["subtitles"], "none in your 5 languages")
        self.assertEqual(notes["audio"], "1 has subtitles you read")
        subs = {r["film"]: r for r in self.rows("subtitles", five)}
        self.assertEqual(subs["Jp No Subs (1960)"]["why"],
                         "Its soundtrack is in Japanese and it has no subtitles in any of your languages.")
        self.assertIn("dubbed (English soundtrack)", subs["Jp Dub (1963)"]["why"])
        texts = [r["why"] for i in ("subtitles", "audio") for r in self.rows(i, five)] + list(notes.values())
        self.assertFalse([t for t in texts if "your languages subtitles" in t or "no your" in t])
        self.assertEqual(D.subtitle_words(["en"]), "English subtitles")
        self.assertEqual(D.subtitle_words(["en", "ja"], "and"), "English and Japanese subtitles")
        self.assertEqual(D.subtitle_words(["en", "ja", "zh", "fr"], "and"), "subtitles in 4 of your languages")
        self.assertEqual(D.subtitle_words(["en", "ja", "zh", "fr"]), "subtitles in one of your languages")

    def test_available_languages(self):
        codes = [x["code"] for x in self.answer["available_languages"]]
        self.assertEqual(codes[0], "en")
        self.assertIn("ja", codes)
        self.assertIn("zh", codes)
        tracks = [x["tracks"] for x in self.answer["available_languages"]]
        self.assertEqual(tracks, sorted(tracks, reverse=True))


ISSUE_INDEX = {k: i for i, k in enumerate(D.ISSUE_IDS)}


class UpgradeSizeMetadataTests(DoctorFixtureCase):
    def test_upgrade_candidates(self):
        rows = {r["film"]: r for r in self.rows("upgrade")}
        self.assertEqual(set(rows), {"Rated Eight (1980)"})
        r = rows["Rated Eight (1980)"]
        self.assertEqual((r["your_rating"], r["best"], r["plays"]), (8, "480p", 1))
        self.assertEqual(r["why"], "You rated it 8 out of 10, but your best copy is only 480p (720×480).")
        seven = D.answer(self.catalog, {"min_rating": 7})
        self.assertIn("Rated Seven Half (1981)", self.films("upgrade", seven))
        # a 1080p picture cropped at the sides (1374x1036: Plex calls it 720p) has no sharper release to get
        self.assertNotIn("Cropped Nine (2014)", self.films("upgrade", seven))
        real = next(r for r in self.rows("upgrade", seven) if r["film"] == "Real Seven Twenty (2015)")
        self.assertEqual(real["why"], "You rated it 7.5 out of 10, but your best copy is only 720p (1280×720).")
        cropped = next(c for c in D.copies_data(self.catalog)["copies"] if c["title"] == "Cropped Nine")
        self.assertEqual((cropped["resolution"], D._rank(cropped)), ("720p", D.RANK["1080p"]))
        self.assertIn("1374×1036", self.answer["issues"]["upgrade"]["limits"])
        self.assertIn("rated 7+", seven["summary"][ISSUE_INDEX["upgrade"]]["note"])
        self.assertIn("7 or more", seven["summary"][ISSUE_INDEX["upgrade"]]["explanation"])
        self.assertEqual(self.answer["settings"]["upgrade_below"], "1080p")

    def test_upgrade_candidates_below_4k(self):
        """Settings > Library Doctor: 'films I love that I don't have in 4K' - a cropped 1080p copy is below 4K
        (said as 1080p), and the words follow the choice."""
        four = D.answer(self.catalog, {"upgrade_below": "4k", "min_rating": 9})
        self.assertEqual(four["settings"]["upgrade_below"], "4K")
        rows = {r["film"]: r for r in self.rows("upgrade", four)}
        self.assertEqual(set(rows), {"Cropped Nine (2014)", "Renamed Cut (1986)"})
        cropped = rows["Cropped Nine (2014)"]
        self.assertEqual(cropped["best"], "1080p")
        self.assertEqual(cropped["why"], "You rated it 9 out of 10, but your best copy is only 1080p (1374×1036, "
                                         "cropped at the sides).")
        item = four["summary"][ISSUE_INDEX["upgrade"]]
        self.assertEqual(item["note"], "rated 9+ but below 4K")
        self.assertEqual(item["explanation"], "Films you rated 9 or more out of 10 whose best copy is below 4K.")
        ten = D.answer(self.catalog, {"upgrade_below": "4K", "min_rating": 10})["issues"]["upgrade"]
        self.assertEqual(ten["explanation"], "Films you rated 10 out of 10 whose best copy is below 4K.")
        self.assertEqual(ten["rows"], [])
        with self.assertRaises(ValueError):
            D.answer(self.catalog, {"upgrade_below": "IMAX"})
        self.assertEqual((D.upgrade_target(None), D.upgrade_target("8k")), ("1080p", "8K"))
        self.assertEqual(D.rated_words(7.5), "7.5 or more")
        # the Film page's issues (no choices given) use the ones last asked for
        renamed = key_of(self.catalog, "Renamed Cut")
        D.answer(self.catalog, {"upgrade_below": "4K", "min_rating": 9})
        self.assertIn("upgrade", [i["id"] for i in D.film_issues(self.catalog, renamed)])
        D.answer(self.catalog, {})
        self.assertNotIn("upgrade", [i["id"] for i in D.film_issues(self.catalog, renamed)])
        self.catalog.cache["doctor.upgrade_below"] = "4K"
        self.catalog.cache["doctor.min_rating"] = 9.0
        self.assertIn("upgrade", [i["id"] for i in D.film_issues(self.catalog, renamed)])
        self.assertNotIn("upgrade", [i["id"] for i in D.film_issues(self.catalog, renamed, None, 9, "1080p")])
        D.answer(self.catalog, {})                                   # (as the other tests expect it)

    def test_unusual_sizes(self):
        self.assertEqual(self.answer["typical_mbps"], {"1080p": 10.0})
        large = {r["film"]: r for r in self.rows("large")}
        self.assertEqual(set(large), {"Huge File (1995)"})
        self.assertEqual(large["Huge File (1995)"]["times"], 4.0)
        self.assertEqual(large["Huge File (1995)"]["usual_mbps"], 10.0)
        self.assertIn("4.0 times the usual rate for 1080p", large["Huge File (1995)"]["why"])
        small = {r["film"]: r for r in self.rows("small")}
        self.assertEqual(set(small), {"Tiny File (1996)"})
        self.assertIn("about a fifth of the usual rate", small["Tiny File (1996)"]["why"])
        self.assertNotIn("Seven Twenty A (1998)", large)          # 720p: too few files to say what's usual

    def test_missing_details(self):
        rows = {r["film"]: r for r in self.rows("metadata")}
        self.assertEqual(rows["Local Film (2010)"]["missing"], "not matched in Plex; genres; IMDb rating")
        self.assertEqual(rows["Local Film (2010)"]["why"], "Plex hasn't matched it, so it has no genres or IMDb "
                                                          "rating.")
        self.assertEqual(rows["Bare Film (2011)"]["missing"], "genres; IMDb rating")
        self.assertIn("no file", rows["No File Film (2012)"]["missing"])
        self.assertIn("file not analysed", rows["Two Versions (1950)"]["missing"])
        self.assertIn("hasn't analysed 1 of its 2 files", rows["Two Versions (1950)"]["why"])
        self.assertNotIn("Full Film (2013)", rows)
        self.assertEqual(self.answer["summary"][ISSUE_INDEX["metadata"]]["note"], "1 not matched in Plex")
        half = rows["Half Film (1940)"]
        self.assertEqual(half["missing"], "file runs 55 of 120 min")
        self.assertEqual(half["why"], "Its file runs 55 minutes, but Plex says the film runs 120: it may be "
                                      "incomplete, or matched to the wrong film.")
        self.assertEqual(len(half["paths"]), 1)
        self.assertNotIn("Short Cut (1941)", rows)                   # a short version by name
        self.assertNotIn("Whole Film (1942)", rows)
        self.assertIn("60%", self.answer["issues"]["metadata"]["limits"])
        # your only rating is on an earlier edition: Plex shows the copy as unrated
        renamed = rows["Renamed Cut (1986)"]
        self.assertEqual(renamed["missing"], "your rating (on an earlier edition)")
        self.assertTrue(renamed["earlier_rating"])
        self.assertEqual(renamed["why"], "You rated it 9 out of 10 on an earlier edition, so Plex shows the copy you "
                                         "have as unrated (and smart collections by rating leave it out): rate it "
                                         "again in Plex to carry the rating over.")
        self.assertNotIn("Rerated Cut (1987)", rows)                 # (rated again: the copy's own rating counts)
        self.assertIn("rated only on an earlier edition", self.answer["issues"]["metadata"]["explanation"])
        issues = D.film_issues(self.catalog, key_of(self.catalog, "Renamed Cut"))
        self.assertEqual([i["id"] for i in issues], ["metadata"])

    def test_unavailable(self):
        rows = self.rows("unavailable")
        self.assertEqual([r["film"] for r in rows], ["Gone Film (2014)"])
        from projectionist.extract import local_datetime
        self.assertEqual(rows[0]["since"], local_datetime(PART_DELETED_AT).strftime("%Y-%m-%d"))
        self.assertEqual(rows[0]["path"], "/disk3/Gone/Gone Film.mkv")
        # the unavailable copy (40 Mbit/s) is in no other list and takes no space in the totals
        self.assertNotIn("Gone Film (2014)", self.films("large") + self.films("duplicates"))
        self.assertNotIn("/disk3/Gone/Gone Film.mkv", [r["path"] for r in self.rows("disk")])
        self.assertNotIn("/disk3", [d["label"] for d in self.answer["disk"]["by_drive"]])

    def test_disk_use(self):
        disk = self.answer["disk"]
        drives = {d["label"]: d for d in disk["by_drive"]}
        self.assertEqual(set(drives), {"/disk1", "/disk2", "D:", "\\\\nas\\share"})
        self.assertEqual(drives["D:"]["files"], 2)                     # the split copy's two files
        self.assertEqual(sum(d["bytes"] for d in disk["by_drive"]), disk["total_bytes"])
        self.assertEqual(sum(d["bytes"] for d in disk["by_library"]), disk["total_bytes"])
        self.assertEqual([d["label"] for d in disk["by_resolution"]], ["4K", "1080p", "720p", "480p", "SD"])
        sizes = [r["size_gb"] for r in self.rows("disk")]
        self.assertEqual(sizes, sorted(sizes, reverse=True))
        self.assertEqual(self.answer["summary"][-1]["value"], D.size_text(disk["total_bytes"]))
        self.assertEqual(self.answer["totals"]["drives"], 4)
        self.assertIn("7% less for GB and 9% less for TB", self.answer["issues"]["disk"]["limits"])
        self.assertEqual(D.drive_of("/disk1/Movies/x.mkv"), "/disk1")
        self.assertEqual(D.drive_of("D:\\Films\\x.mkv"), "D:")
        self.assertEqual(D.drive_of("\\\\nas\\share\\Films\\x.mkv"), "\\\\nas\\share")
        self.assertEqual(D.drive_of("//nas/share/x.mkv"), "//nas/share")
        self.assertEqual(D.drive_of("/x.mkv"), "/")
        self.assertEqual(D.drive_of(""), "")


# ---------------------------------------------------------------------------------------------------------
class AnswerTests(DoctorFixtureCase):
    def test_summary(self):
        s = self.answer["summary"]
        self.assertEqual([x["id"] for x in s], D.ISSUE_IDS)
        self.assertEqual(len(s), 12)
        for item in s:
            text = item["explanation"]
            self.assertTrue(text.endswith("."), text)
            self.assertNotIn(". ", text, f"{item['id']}: more than one sentence")
            self.assertEqual(item["count"], len(self.answer["issues"][item["id"]]["rows"]))
            self.assertIn(item["severity"], ("fix", "check", "info"))
            self.assertTrue(item["note"])
        self.assertEqual(s[ISSUE_INDEX["unavailable"]]["value"], "1")
        self.assertEqual(s[ISSUE_INDEX["weaker"]]["severity"], "fix")

    def test_every_row_explains_itself(self):
        for issue, item in self.answer["issues"].items():
            keys = {c["key"] for c in item["columns"]}
            for row in item["rows"]:
                for field in ("film_key", "film", "title", "year", "plex_id", "edition", "library", "why"):
                    self.assertIn(field, row, (issue, row))
                self.assertIn(row["film_key"], self.catalog.films)
                self.assertTrue(row["why"].endswith("."), (issue, row["why"]))
                self.assertTrue(keys <= set(row), (issue, keys - set(row)))
            self.assertTrue(item["limits"])

    def test_json(self):
        text = to_json(self.answer)
        self.assertEqual(json.loads(text)["summary"][0]["id"], "duplicates")

    def test_one_issue_and_a_cap(self):
        one = D.answer(self.catalog, {"issue": "size", "count": 1})
        self.assertEqual(list(one["issues"]), ["large"])
        self.assertEqual(len(one["summary"]), 12)
        disk = D.answer(self.catalog, {"issue": "disk", "count": 3})
        self.assertEqual(len(disk["issues"]["disk"]["rows"]), 3)
        self.assertTrue(disk["issues"]["disk"]["truncated"])
        self.assertEqual(disk["issues"]["disk"]["count"], len(self.rows("disk")))
        self.assertEqual(list(D.answer(self.catalog, {"issue": "missing"})["issues"]), ["metadata"])
        self.assertEqual(list(D.answer(self.catalog, {"issue": "Summary"})["issues"]), ["disk"])

    def test_bad_requests(self):
        bad = handle({"action": "doctor", "languages": ["Klingon"]}, catalog=self.catalog)
        self.assertFalse(bad["ok"])
        self.assertIn("unknown language", bad["error"])
        bad = handle({"action": "doctor", "issue": "bogus"}, catalog=self.catalog)
        self.assertFalse(bad["ok"])
        self.assertIn("no check called 'bogus'", bad["error"])
        bad = handle({"action": "doctor", "count": "lots"}, catalog=self.catalog)
        self.assertFalse(bad["ok"])
        good = handle({"action": "doctor", "issue": "weaker", "languages": "en"}, catalog=self.catalog)
        self.assertTrue(good["ok"], good)
        self.assertEqual(good["action"], "doctor")

    def test_film_request(self):
        key = key_of(self.catalog, "Temple Kicks")
        got = D.answer(self.catalog, {"film_key": key})
        self.assertTrue(got["ok"])
        self.assertEqual(len(got["files"]["copies"]), 2)
        self.assertEqual([i["id"] for i in got["issues"]], ["duplicates", "weaker"])
        self.assertFalse(D.answer(self.catalog, {"film_key": "plex://movie/nope"})["ok"])

    def test_unreadable_database(self):
        memory = make_catalog([{"title": "A", "year": 2000}])
        got = handle({"action": "doctor"}, catalog=memory)
        self.assertFalse(got["ok"])
        self.assertIn("Couldn't read your files", got["error"])
        self.assertEqual(D.film_files(memory, "f0")["copies"], [])
        self.assertFalse(D.film_files(memory, "f0")["ok"])
        self.assertEqual(D.film_issues(memory, "f0"), [])
        from projectionist.catalog import Catalog
        gone = Catalog({}, {}, "Owner", os.path.join(self.tmp.name, "missing.db"), [])
        self.assertIn("isn't there any more", D.answer(gone, {})["error"])
        with tempfile.TemporaryDirectory() as d:
            junk = os.path.join(d, "junk.db")
            with open(junk, "wb") as fh:
                fh.write(b"not a database at all" * 100)
            broken = Catalog({}, {}, "Owner", junk, [])
            got = D.answer(broken, {})
            self.assertFalse(got["ok"])
            self.assertIn("junk.db", got["error"])


class FilmPageTests(DoctorFixtureCase):
    def test_film_files(self):
        key = key_of(self.catalog, "Temple Kicks")
        got = D.film_files(self.catalog, key)
        self.assertTrue(got["ok"])
        self.assertEqual(got["film"], "Temple Kicks (1978)")
        self.assertEqual([c["edition"] for c in got["copies"]], ["Eureka", "Twilight Time"])
        eureka = got["copies"][0]
        self.assertEqual(eureka["files"][0]["path"], "/disk2/World/Temple Kicks (Eureka).mkv")
        self.assertEqual(eureka["files"][0]["folder"], "/disk2/World")
        self.assertEqual(eureka["files"][0]["name"], "Temple Kicks (Eureka).mkv")
        self.assertEqual(eureka["audio_languages"], ["English", "Chinese"])
        self.assertEqual(eureka["subtitle_languages"], ["English"])
        self.assertEqual(eureka["resolution"], "1080p")
        self.assertEqual(eureka["bitrate_mbps"], 11.6)
        self.assertAlmostEqual(got["total_size_gb"], sum(c["size_gb"] for c in got["copies"]), places=1)
        json.dumps(got)
        missing = D.film_files(self.catalog, "plex://movie/nope")
        self.assertEqual((missing["ok"], missing["copies"]), (False, []))
        self.assertFalse(D.film_files(self.catalog, None)["ok"])

    def test_film_files_before_the_whole_library_is_read(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "com.plexapp.plugins.library.db-2026-09-25")
            catalog = load_fixture(db)
            key = key_of(catalog, "Two Versions")
            got = D.film_files(catalog, key)
            self.assertNotIn("doctor.copies", catalog.cache)          # just this film was read
            self.assertEqual(len(got["copies"]), 2)
            self.assertEqual([f["name"] for f in got["copies"][0]["files"]],
                             ["Two Versions CD1.mkv", "Two Versions CD2.mkv"])
            self.assertFalse(got["copies"][1]["analysed"])
            gone = D.film_files(catalog, key_of(catalog, "Gone Film"))
            self.assertEqual([c["unavailable"] for c in gone["copies"]], [False, True])   # available first
            self.assertTrue(gone["copies"][1]["unavailable_since"])

    def test_film_issues(self):
        key = key_of(self.catalog, "Temple Kicks")
        issues = D.film_issues(self.catalog, key)
        self.assertEqual([i["id"] for i in issues], ["duplicates", "weaker"])
        self.assertEqual(issues[1]["edition"], "Eureka")
        eureka = next(c for c in D.copies_data(self.catalog)["copies"]
                      if c["edition"] == "Eureka" and c["title"] == "Temple Kicks")
        self.assertEqual((issues[1]["plex_id"], issues[1]["media_id"]), (eureka["plex_id"], eureka["media_id"]))
        self.assertEqual(D.film_issues(self.catalog, "plex://movie/nope"), [])
        jp = key_of(self.catalog, "Jp No Subs")
        self.assertEqual([i["id"] for i in D.film_issues(self.catalog, jp, ["en"])], ["subtitles", "audio"])
        self.assertEqual(D.film_issues(self.catalog, jp, ["en", "ja"]), [])
        alien = D.film_issues(self.catalog, key_of(self.catalog, "Alien"))
        self.assertEqual([(i["id"], i["severity"]) for i in alien], [("editions", "info")])
        # fix first, then check, then info
        two = D.film_issues(self.catalog, key_of(self.catalog, "Two Versions"))
        order = [D.SEVERITY_ORDER[i["severity"]] for i in two]
        self.assertEqual(order, sorted(order))
        self.assertIn("metadata", [i["id"] for i in two])


class CacheAndCancelTests(unittest.TestCase):
    def test_the_database_is_read_once(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "com.plexapp.plugins.library.db-2026-09-25")
            catalog = load_fixture(db)
            opened = []
            real = D.PlexDatabase

            class Counting(real):
                def __init__(self, path):
                    opened.append(path)
                    super().__init__(path)
            D.PlexDatabase = Counting
            try:
                D.answer(catalog, {})
                D.answer(catalog, {"languages": ["en", "ja"]})
                D.film_files(catalog, key_of(catalog, "Alien"))
                D.film_issues(catalog, key_of(catalog, "Alien"))
            finally:
                D.PlexDatabase = real
            self.assertEqual(len(opened), 1)

    def test_a_cancelled_job_stops(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "com.plexapp.plugins.library.db-2026-09-25")
            catalog = load_fixture(db)
            job = jobs.Job("doctor.answer")
            job.cancel()
            with jobs.running(job):
                with self.assertRaises(jobs.Cancelled):
                    D.answer(catalog, {})
            self.assertNotIn("doctor.copies", catalog.cache)
            self.assertTrue(D.answer(catalog, {})["ok"])              # and it works afterwards


class SaveTests(DoctorFixtureCase):
    def read_sheet(self, path):
        with zipfile.ZipFile(path) as z:
            wb = ET.fromstring(z.read("xl/workbook.xml"))
            ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
            names = [s.get("name") for s in wb.find("m:sheets", ns)]
            sheet = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
            strings = []
            if "xl/sharedStrings.xml" in z.namelist():
                sst = ET.fromstring(z.read("xl/sharedStrings.xml"))
                strings = ["".join(t.text or "" for t in si.iter(f"{{{ns['m']}}}t")) for si in sst]
        cells = {}
        for c in sheet.iter(f"{{{ns['m']}}}c"):
            v = c.find("m:v", ns)
            f = c.find("m:f", ns)
            if c.get("t") == "s":
                cells[c.get("r")] = ("s", strings[int(v.text)])
            elif f is not None:
                cells[c.get("r")] = ("f", f.text)
            elif v is not None:
                cells[c.get("r")] = ("n", float(v.text))
        return names, cells

    def test_save_a_list(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "doctor.xlsx")
            sheet = D.sheet_for(self.answer, "disk")
            got = D.save_xlsx(path, [sheet], about=[("Languages", "English"), ("Database", "fixture")])
            self.assertEqual(got, path)
            names, cells = self.read_sheet(path)
            self.assertEqual(names, ["Disk use"])
            self.assertEqual(cells["A1"], ("s", "Disk use"))
            self.assertEqual(cells["A2"][1], D.EXPLANATIONS["disk"])
            self.assertIn("Languages: English", cells["A3"][1])
            self.assertEqual(cells["A5"], ("s", "Film"))
            headings = [c["heading"] for c in sheet["columns"]]
            size_col = "ABCDEFGHIJ"[headings.index("Size GB")]
            self.assertEqual(cells[f"{size_col}6"][0], "n")             # numbers stay numbers
            self.assertEqual(cells[f"{size_col}6"][1], sheet["rows"][0]["size_gb"])
            titles = [v for r, (kind, v) in cells.items() if r.startswith("A") and kind != "n"]
            self.assertIn("=Formula Looking Title (1912)", titles)     # text, never a formula
            self.assertFalse(any(kind == "f" for kind, _ in cells.values()))
            self.assertEqual([n for n in os.listdir(d) if n.startswith("~projectionist-")], [])
            if os.name != "nt":                       # a new file's usual permissions, not mkstemp's owner-only
                self.assertEqual(os.stat(path).st_mode & 0o777, 0o666 & ~_umask())

    def test_save_errors(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(OutputError):
                D.save_xlsx(os.path.join(d, "no such folder", "x.xlsx"), [D.sheet_for(self.answer, "weaker")])
            with self.assertRaises(OutputError):
                D.save_xlsx(d, [D.sheet_for(self.answer, "weaker")])     # a folder, not a file
            self.assertEqual(os.listdir(d), [])

    def test_sheet_names(self):
        self.assertEqual(D.sheet_name("A/B: [c]?"), "AB c")
        self.assertEqual(D.sheet_name("x" * 40), "x" * 31)
        self.assertEqual(D.sheet_name("Disk use", ["disk use"]), "Disk use (2)")


if __name__ == "__main__":
    unittest.main()
