"""How the app writes dates and times (projectionist/formats.py): the three date styles, the two clocks and the
week's first day chosen under Settings > Dates and times. No windows."""

import os
import sys
import unittest
from datetime import date, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from projectionist import formats as F  # noqa: E402
from projectionist import prefs  # noqa: E402


class FormatsTest(unittest.TestCase):
    def tearDown(self):
        F.reset()

    def test_the_default_is_the_way_the_app_always_wrote_them(self):
        self.assertEqual(F.STYLE, {"date": "mdy", "clock": "12h", "week": "monday"})
        self.assertEqual(F.nice_date("2026-03-03"), "Mar 3, 2026")
        self.assertEqual(F.nice_date(date(2025, 2, 16), year=False), "Feb 16")
        self.assertEqual(F.nice_date(None), "")
        self.assertEqual(F.date_range("2025-12-27", "2026-01-01"), "Dec 27, 2025 - Jan 1, 2026")
        self.assertEqual(F.date_range("2025-11-10", "2025-11-16"), "Nov 10-16, 2025")
        self.assertEqual(F.date_range("2026-03-26", "2026-04-05"), "Mar 26 - Apr 5, 2026")
        self.assertEqual(F.date_range("2025-11-10", "2025-11-16", year=False), "Nov 10-16")
        self.assertEqual(F.date_range("2025-11-10", None), "Nov 10, 2025")
        self.assertEqual(F.short_date("2025-07-17"), "Jul 17 '25")
        self.assertEqual(F.nice_time("2026-01-01T17:32"), "5:32 pm")
        self.assertEqual(F.nice_time(datetime(2026, 1, 1, 0, 5)), "12:05 am")
        self.assertEqual([F.hour_label(h) for h in (0, 9, 12, 17)], ["midnight", "9 am", "noon", "5 pm"])
        self.assertEqual([F.hour_span(18), F.hour_span(11, 2), F.hour_span(22, 2)],
                         ["6-7 pm", "11 am - 1 pm", "10 pm - midnight"])
        self.assertEqual(F.moment("2026-03-03T21:14"), "Mar 3, 2026, 9:14 pm")
        self.assertEqual(F.moment("2026-03-03T21:14", year=False), "Mar 3, 9:14 pm")
        self.assertEqual(F.moment("2026-03-03"), "Mar 3, 2026")          # (no time of day)
        self.assertEqual(F.week_days(), [0, 1, 2, 3, 4, 5, 6])

    def test_day_first(self):
        F.use(date="dmy")
        self.assertEqual(F.nice_date("2026-03-03"), "3 Mar 2026")
        self.assertEqual(F.nice_date("2026-03-03", year=False), "3 Mar")
        self.assertEqual(F.date_range("2025-11-10", "2025-11-16"), "10-16 Nov 2025")
        self.assertEqual(F.date_range("2026-03-26", "2026-04-05"), "26 Mar - 5 Apr 2026")
        self.assertEqual(F.date_range("2025-12-27", "2026-01-01"), "27 Dec 2025 - 1 Jan 2026")
        self.assertEqual(F.date_range("2026-03-26", "2026-04-05", year=False), "26 Mar - 5 Apr")
        self.assertEqual(F.short_date("2025-07-17"), "17 Jul '25")
        self.assertEqual(F.moment("2026-03-03T21:14"), "3 Mar 2026, 9:14 pm")

    def test_iso_dates(self):
        F.use(date="iso")
        self.assertEqual(F.nice_date("2026-03-03"), "2026-03-03")
        self.assertEqual(F.nice_date("2026-03-03", year=False), "03-03")
        self.assertEqual(F.date_range("2025-11-10", "2025-11-16"), "2025-11-10 to 2025-11-16")
        self.assertEqual(F.date_range("2025-11-10", "2025-11-16", year=False), "11-10 to 11-16")
        self.assertEqual(F.short_date("2025-07-17"), "2025-07-17")
        self.assertEqual(F.moment("2026-03-03T21:14"), "2026-03-03 9:14 pm")

    def test_24_hour_clock(self):
        F.use(clock="24h")
        self.assertEqual(F.nice_time("2026-01-01T17:32"), "17:32")
        self.assertEqual(F.nice_time("2026-01-01T09:05"), "09:05")
        self.assertEqual([F.hour_label(h) for h in (0, 9, 12, 17, 24)], ["00:00", "09:00", "12:00", "17:00", "00:00"])
        self.assertEqual([F.hour_span(18), F.hour_span(23), F.hour_span(22, 3)],
                         ["18:00-19:00", "23:00-24:00", "22:00-01:00"])
        self.assertEqual(F.time_text(20, 30), "20:30")
        with F.styled(date="iso"):
            self.assertEqual(F.moment("2026-03-03T21:14"), "2026-03-03 21:14")
        self.assertEqual(F.moment("2026-03-03T21:14"), "Mar 3, 2026, 21:14")      # (put back after)

    def test_weeks_from_sunday(self):
        F.use(week="sunday")
        self.assertEqual(F.first_weekday(), 6)
        self.assertEqual(F.week_days(), [6, 0, 1, 2, 3, 4, 5])
        self.assertEqual(F.week_days(0), [0, 1, 2, 3, 4, 5, 6])

    def test_a_style_there_isnt(self):
        with self.assertRaises(ValueError):
            F.use(date="dd/mm/yyyy")
        with self.assertRaises(ValueError):
            F.use(clock="13h")
        self.assertEqual(F.STYLE["date"], "mdy")

    def test_the_settings(self):
        """Settings > Dates and times: each a choice with the default of today's style; changing one changes the
        style at once (its apply), and a bad saved value reads as the default."""
        sec = [p.key for p in prefs.shown("Dates and times")]
        self.assertEqual(sec, ["date_style", "clock", "week_start"])
        self.assertEqual({k: prefs.get(None, k) for k in sec},
                         {"date_style": "mdy", "clock": "12h", "week_start": "monday"})
        for key in sec:
            p = prefs.pref(key)
            self.assertEqual(p.kind, "choice")
            self.assertTrue(p.live)
            self.assertTrue(p.help)
        self.assertEqual(prefs.pref("date_style").words("dmy"), "3 Mar 2026")

        class App:
            def __init__(self):
                self.settings, self.heard = {}, []

            def save_settings(self):
                pass

            def preference_changed(self, key, value):
                self.heard.append((key, value, dict(F.STYLE)))
        app = App()
        prefs.set(app, "clock", "24h")
        self.assertEqual(F.STYLE["clock"], "24h")
        self.assertEqual(app.heard[-1][2]["clock"], "24h")        # (the style is already changed when tabs hear)
        prefs.set(app, "date_style", "iso")
        self.assertEqual(F.nice_date("2026-03-03", year=False), "03-03")
        prefs.reset(app, section="Dates and times")
        self.assertEqual(F.STYLE, F.DEFAULTS)
        F.follow({"date_style": "nonsense", "week_start": "sunday"})
        self.assertEqual(F.STYLE, {"date": "mdy", "clock": "12h", "week": "sunday"})
        F.follow(None)
        self.assertEqual(F.STYLE, F.DEFAULTS)


if __name__ == "__main__":
    unittest.main()
