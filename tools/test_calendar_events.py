"""Run with: uv run --with icalendar --with recurring-ical-events python -m unittest tools/test_calendar_events.py"""

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from calendar_events import upcoming  # noqa: E402

ICS = b"""BEGIN:VCALENDAR
VERSION:2.0
X-WR-CALNAME:joan@example.com
BEGIN:VEVENT
UID:daily
DTSTART:20261023T143000Z
DTEND:20261023T144500Z
RRULE:FREQ=DAILY;COUNT=5
SUMMARY:Daily frontend
END:VEVENT
BEGIN:VEVENT
UID:declined
DTSTART:20261023T150000Z
DTEND:20261023T153000Z
SUMMARY:Meeting I declined
ATTENDEE;PARTSTAT=DECLINED;CN=Joan:mailto:joan@example.com
END:VEVENT
BEGIN:VEVENT
UID:accepted
DTSTART:20261023T160000Z
DTEND:20261023T163000Z
SUMMARY:Planning
ATTENDEE;PARTSTAT=ACCEPTED;CN=Joan:mailto:joan@example.com
END:VEVENT
BEGIN:VEVENT
UID:allday
DTSTART;VALUE=DATE:20261023
DTEND;VALUE=DATE:20261024
SUMMARY:Holiday
END:VEVENT
BEGIN:VEVENT
UID:old
DTSTART:20261020T160000Z
DTEND:20261020T163000Z
SUMMARY:Old meeting
END:VEVENT
END:VCALENDAR
"""


class UpcomingTest(unittest.TestCase):
    def test_expands_recurrences_and_skips_declined_allday_and_past(self):
        now = datetime(2026, 10, 23, 14, 0, tzinfo=timezone.utc)
        events = upcoming(ICS, now, horizon_hours=24)
        self.assertEqual([e["title"] for e in events], ["Daily frontend", "Planning"])
        self.assertEqual(events[0]["start"], datetime(2026, 10, 23, 14, 30, tzinfo=timezone.utc).timestamp())
        self.assertEqual(events[0]["uid"], "daily")
        self.assertRegex(events[0]["local"], r"^\d\d:\d\d$")

    def test_recurrence_gives_tomorrows_instance(self):
        now = datetime(2026, 10, 23, 15, 0, tzinfo=timezone.utc)
        titles = [(e["title"], e["start"]) for e in upcoming(ICS, now, horizon_hours=24)]
        self.assertIn(("Daily frontend", datetime(2026, 10, 24, 14, 30, tzinfo=timezone.utc).timestamp()), titles)


if __name__ == "__main__":
    unittest.main()
