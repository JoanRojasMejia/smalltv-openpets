import unittest
from datetime import datetime, timedelta, timezone

from jira_source import Schedule, parse_issue, progress, summary_text, worked_hours

BOGOTA = timezone(timedelta(hours=-5))
S = Schedule()  # Mon-Fri 09:00-18:00, lunch 13:00-14:00 -> 8 h/day


def t(day, hour, minute=0):
    return datetime(2026, 10, day, hour, minute, tzinfo=BOGOTA)  # 2026-10-19 is a Monday


class WorkedHoursTest(unittest.TestCase):
    def test_same_morning(self):
        self.assertEqual(worked_hours(t(19, 9), t(19, 11), S), 2)

    def test_lunch_is_not_counted(self):
        self.assertEqual(worked_hours(t(19, 12), t(19, 15), S), 2)

    def test_outside_hours_and_full_days(self):
        self.assertEqual(worked_hours(t(19, 7), t(19, 20), S), 8)
        self.assertEqual(worked_hours(t(19, 9), t(21, 9), S), 16)

    def test_weekend_is_skipped(self):
        self.assertEqual(worked_hours(t(23, 17), t(26, 10), S), 2)  # Fri 17:00 -> Mon 10:00

    def test_start_after_end_or_in_lunch(self):
        self.assertEqual(worked_hours(t(19, 15), t(19, 10), S), 0)
        self.assertEqual(worked_hours(t(19, 13, 30), t(19, 14, 30), S), 0.5)

    def test_other_timezones_are_converted_to_local_schedule(self):
        start = datetime(2026, 10, 19, 12, 0, tzinfo=timezone(timedelta(hours=-3)))  # 10:00 Bogota
        self.assertEqual(worked_hours(start, t(19, 12), S, tz=BOGOTA), 2)


class ProgressTest(unittest.TestCase):
    def test_half_way(self):
        self.assertEqual(progress(points=1, worked=2), (50, 2.0))

    def test_over_budget(self):
        self.assertEqual(progress(points=1, worked=6), (150, -2.0))

    def test_no_points(self):
        self.assertIsNone(progress(points=None, worked=3))
        self.assertIsNone(progress(points=0, worked=3))

    def test_summary_text(self):
        task = {"key": "ABC-41", "points": 6, "summary": "FE - [web-app] Rename documents"}
        self.assertEqual(summary_text(task, worked=14), ("ABC-41 - 6 SP", 58, "10 H LEFT", "Rename documents"))
        self.assertEqual(summary_text(task, worked=25.5), ("ABC-41 - 6 SP", 106, "1.5 H OVER", "Rename documents"))
        self.assertEqual(summary_text(dict(task, points=None), worked=3)[1:3], (None, "NO STORY POINTS"))


class ParseIssueTest(unittest.TestCase):
    def test_parse(self):
        raw = {"key": "ABC-41", "fields": {"summary": "FE - x", "customfield_10105": 6,
                                             "statuscategorychangedate": "2026-10-20T12:11:29.769-0300"}}
        task = parse_issue(raw, "customfield_10105")
        self.assertEqual((task["key"], task["points"], task["summary"]), ("ABC-41", 6, "FE - x"))
        self.assertEqual(task["started"], datetime(2026, 10, 20, 15, 11, 29, 769000, tzinfo=timezone.utc))

    def test_missing_points_and_date(self):
        task = parse_issue({"key": "A-1", "fields": {"summary": "x"}}, "customfield_10105")
        self.assertIsNone(task["points"])
        self.assertIsNone(task["started"])


if __name__ == "__main__":
    unittest.main()
