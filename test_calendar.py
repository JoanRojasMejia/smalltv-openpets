import unittest

from calendar_source import MeetingReminder, filter_calendars, followed_calendars, reminder_text

E1 = {"uid": "a", "title": "Daily frontend", "start": 1000.0, "end": 2800.0, "local": "09:30"}
E2 = {"uid": "b", "title": "1:1 con Ana", "start": 5000.0, "end": 6800.0, "local": "10:36"}


class MeetingReminderTest(unittest.TestCase):
    def setUp(self):
        self.r = MeetingReminder(lead=300)
        self.r.events = [E2, E1]  # unsorted on purpose

    def test_nothing_before_the_lead_time(self):
        self.assertIsNone(self.r.poll(now=1000 - 301))

    def test_one_reminder_per_minute_left(self):
        self.assertEqual(self.r.poll(now=1000 - 300), (E1, 5))
        self.assertIsNone(self.r.poll(now=1000 - 290))  # same minute: already shown
        self.assertEqual(self.r.poll(now=1000 - 239), (E1, 4))
        self.assertEqual(self.r.poll(now=1000 - 59), (E1, 1))

    def test_now_at_start_then_quiet(self):
        self.assertEqual(self.r.poll(now=1000), (E1, 0))
        self.assertIsNone(self.r.poll(now=1030))
        self.assertIsNone(self.r.poll(now=1100))  # meeting running: no more reminders

    def test_late_start_jumps_to_current_minute(self):
        self.assertEqual(self.r.poll(now=1000 - 100), (E1, 2))

    def test_next_meeting_after_the_first(self):
        self.r.poll(now=1000)
        self.assertEqual(self.r.poll(now=5000 - 300), (E2, 5))


class ReminderTextTest(unittest.TestCase):
    def test_texts(self):
        self.assertEqual(reminder_text(E1, 5), ("IN 5 MIN", "09:30 DAILY FRONTEND"))
        self.assertEqual(reminder_text(E1, 1), ("IN 1 MIN", "09:30 DAILY FRONTEND"))
        self.assertEqual(reminder_text(E1, 0), ("STARTING NOW", "09:30 DAILY FRONTEND"))


class CalendarFilterTest(unittest.TestCase):
    def test_only_followed_calendars(self):
        events = [dict(E1, calendar="joan@work.com"), dict(E2, calendar="Feriados")]
        self.assertEqual(filter_calendars(events, {"joan@work.com"}), events[:1])
        self.assertEqual(filter_calendars(events, set()), events)

    def test_followed_calendars_file(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "calendars.txt"
            self.assertEqual(followed_calendars(f), set())
            f.write_text("# mine\njoan@work.com\n\nEquipo Front\n")
            self.assertEqual(followed_calendars(f), {"joan@work.com", "Equipo Front"})


if __name__ == "__main__":
    unittest.main()
