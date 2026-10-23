import json
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import daily_digest as dd

TZ = timezone(timedelta(hours=-5))


class EngramTest(unittest.TestCase):
    def test_reads_that_days_work_memories_in_utc(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "engram.db"
            con = sqlite3.connect(db)
            con.execute("create table observations (type text, project text, title text, created_at text, deleted_at text)")
            rows = [("decision", "web-app", "Use React Query", "2026-10-22 15:00:00", None),   # 10:00 local
                    ("bugfix", "personal", "Fix my dotfiles", "2026-10-22 16:00:00", None),     # excluded project
                    ("discovery", "web-app", "Deleted one", "2026-10-22 16:00:00", "x"),        # deleted
                    ("config", "web-app", "Late night", "2026-10-23 04:30:00", None),           # 23:30 local, same day
                    ("config", "web-app", "Next day", "2026-10-23 06:00:00", None)]             # 01:00 local next day
            con.executemany("insert into observations values (?,?,?,?,?)", rows)
            con.commit()
            con.close()
            got = dd.engram_memories(date(2026, 10, 22), db, exclude={"personal"}, tz=TZ)
            self.assertEqual(got, [("decision", "web-app", "Use React Query"), ("config", "web-app", "Late night")])
            self.assertEqual(dd.engram_memories(date(2026, 10, 22), Path(d) / "missing.db", set(), tz=TZ), [])


class NotesTest(unittest.TestCase):
    def test_add_and_read_notes(self):
        with tempfile.TemporaryDirectory() as d:
            notes = Path(d)
            dd.add_note("ABC-41: finished the form validation", notes, now=datetime(2026, 10, 22, 11, 5, tzinfo=TZ), cwd="/x/web-app")
            dd.add_note("second", notes, now=datetime(2026, 10, 22, 16, 0, tzinfo=TZ), cwd="")
            self.assertEqual(dd.read_notes(date(2026, 10, 22), notes),
                             ["11:05 [web-app] ABC-41: finished the form validation", "16:00 second"])
            self.assertEqual(dd.read_notes(date(2026, 10, 21), notes), [])


class ParseTest(unittest.TestCase):
    def test_extracts_json_from_model_output(self):
        out = 'Claro:\n```json\n{"ayer": ["a", "b"], "hoy": ["c"], "bloqueos": []}\n```'
        self.assertEqual(dd.parse_digest(out), {"ayer": ["a", "b"], "hoy": ["c"], "bloqueos": []})

    def test_bad_output(self):
        self.assertIsNone(dd.parse_digest("no json here"))
        self.assertIsNone(dd.parse_digest('{"ayer": "not a list"}'))

    def test_trims_items(self):
        got = dd.parse_digest(json.dumps({"ayer": ["x" * 200, "b", "c", "d"], "hoy": [], "bloqueos": ["z"]}))
        self.assertEqual(len(got["ayer"]), 3)
        self.assertLessEqual(len(got["ayer"][0]), dd.MAX_ITEM)


class FallbackTest(unittest.TestCase):
    def test_without_the_model(self):
        data = {"commits": [["web-app", "[ABC-41] feat(form): validation"], ["web-app", "fix: typo"]],
                "jira_changes": [["ABC-41", "Form validation", "En Progreso"]],
                "task": {"key": "ABC-41", "percent": 95, "left": "1.2 H LEFT"},
                "blocked": [["ABC-35", "Waiting for backend"]], "deploys": [["SHOP-CI5", "Succeeded"]],
                "memories": [], "notes": [], "agents": {"sessions": 3, "asks": 1}}
        got = dd.fallback_digest(data)
        self.assertEqual(got["ayer"][0], "2 commits: ABC-41 validation")
        self.assertIn("SHOP-CI5 desplegado", got["ayer"])
        self.assertEqual(got["hoy"], ["ABC-41 al 95% (1.2 H LEFT)"])
        self.assertEqual(got["bloqueos"], ["ABC-35 Waiting for backend"])


class ScheduleTest(unittest.TestCase):
    def test_previous_workday(self):
        self.assertEqual(dd.previous_workday(date(2026, 10, 26)), date(2026, 10, 23))  # Monday -> Friday
        self.assertEqual(dd.previous_workday(date(2026, 10, 22)), date(2026, 10, 21))

    def test_generation_time(self):
        def at(day, h, m=0):
            return datetime(2026, 10, day, h, m, tzinfo=TZ)
        self.assertEqual(dd.digest_due(at(22, 17, 49), have={date(2026, 10, 21)}), None)
        self.assertEqual(dd.digest_due(at(22, 17, 50), have=set()), date(2026, 10, 22))
        self.assertEqual(dd.digest_due(at(22, 18, 30), have={date(2026, 10, 22)}), None)
        # Mac was off yesterday evening: catch up in the morning
        self.assertEqual(dd.digest_due(at(23, 8, 0), have=set()), date(2026, 10, 22))
        self.assertEqual(dd.digest_due(at(24, 18, 0), have=set()), date(2026, 10, 23))  # Saturday: Friday's

    def test_cards_before_the_daily(self):
        start = datetime(2026, 10, 23, 10, 0, tzinfo=TZ).timestamp()
        self.assertEqual(dd.daily_slot(start - 11 * 60, start), None)
        self.assertEqual(dd.daily_slot(start - 10 * 60, start), "10")
        self.assertEqual(dd.daily_slot(start - 3 * 60, start), "3")
        self.assertEqual(dd.daily_slot(start + 30, start), None)


class LongVersionTest(unittest.TestCase):
    DATA = {"commits": [["web-app", "[ABC-41] feat(form): validation"], ["web-app", "[ABC-41] fix: typo"]],
            "jira_changes": [["ABC-41", "Form validation", "En Progreso"]],
            "task": {"key": "ABC-41", "summary": "Form validation", "percent": 95, "left": "1.2 H LEFT"},
            "blocked": [["ABC-35", "Waiting for backend"]], "deploys": [["SHOP-CI5", "Succeeded", "ABC-41 validation"]],
            "memories": [], "notes": ["11:05 [web-app] ABC-41: form done"], "agents": {"sessions": 3, "asks": 1}}

    def test_detail_is_optional_and_trimmed(self):
        got = dd.parse_digest(json.dumps({"ayer": ["a"], "hoy": [], "bloqueos": [], "detalle": "x" * 5000}))
        self.assertEqual(len(got["detalle"]), dd.MAX_DETAIL)
        self.assertNotIn("detalle", dd.parse_digest('{"ayer": [], "hoy": [], "bloqueos": []}'))

    def test_fallback_detail_lists_every_fact(self):
        text = dd.fallback_detail(self.DATA)
        for expected in ("ABC-41", "validation", "SHOP-CI5", "ABC-35", "95%", "form done", "3 sesiones"):
            self.assertIn(expected, text)

    def test_message_text(self):
        record = {"day": "2026-10-22", "ayer": ["a1"], "hoy": ["h1"], "bloqueos": [], "detalle": "Detalle largo."}
        text = dd.message_text(record)
        self.assertTrue(text.startswith("Daily - jueves 22/10"))
        self.assertIn("AYER\n- a1", text)
        self.assertIn("BLOQUEOS\n- Ninguno", text)
        self.assertTrue(text.endswith("Detalle largo."))

    def test_send_time(self):
        nine = datetime(2026, 10, 23, 9, 0, tzinfo=TZ)
        daily_at = lambda h, m: datetime(2026, 10, 23, h, m, tzinfo=TZ).timestamp()
        self.assertFalse(dd.message_due(nine.replace(hour=8, minute=59), [daily_at(10, 0)]))
        self.assertTrue(dd.message_due(nine, [daily_at(10, 0)]))
        self.assertTrue(dd.message_due(nine.replace(hour=8, minute=46), [daily_at(9, 0)]))  # early daily: 15 min before
        self.assertFalse(dd.message_due(datetime(2026, 10, 24, 10, 0, tzinfo=TZ), []))      # Saturday


class DeliveryTest(unittest.TestCase):
    def make_db(self, rows):
        d = tempfile.mkdtemp()
        db = Path(d) / "chat.db"
        con = sqlite3.connect(db)
        con.execute("create table handle (ROWID integer primary key, id text)")
        con.execute("create table message (ROWID integer primary key, handle_id int, is_from_me int, date int, "
                    "is_sent int, is_delivered int, error int)")
        con.execute("insert into handle values (1, '+15550001')")
        con.executemany("insert into message (handle_id, is_from_me, date, is_sent, is_delivered, error) "
                        "values (1, 1, ?, ?, ?, ?)", rows)
        con.commit()
        con.close()
        return db

    def apple(self, unix):  # Messages stores nanoseconds since 2001-01-01
        return int((unix - 978307200) * 1e9)

    def test_statuses(self):
        db = self.make_db([(self.apple(1000), 1, 1, 0)])
        self.assertEqual(dd.delivery_status("+15550001", since=990, db=db), "delivered")
        db = self.make_db([(self.apple(1000), 0, 0, 33)])
        self.assertEqual(dd.delivery_status("+15550001", since=990, db=db), "failed (error 33)")
        db = self.make_db([(self.apple(1000), 1, 0, 0)])
        self.assertEqual(dd.delivery_status("+15550001", since=990, db=db), "sent")

    def test_unknown_when_nothing_new_or_unreadable(self):
        db = self.make_db([(self.apple(900), 1, 1, 0)])
        self.assertEqual(dd.delivery_status("+15550001", since=990, db=db), "unknown")
        self.assertEqual(dd.delivery_status("+15550001", since=0, db=Path("/nonexistent/chat.db")), "unknown")


class PagesTest(unittest.TestCase):
    def test_pages_and_empty_sections(self):
        from daily_notifier import digest_pages

        pages = digest_pages({"ayer": ["a"], "hoy": [], "bloqueos": []}, date(2026, 10, 22))
        self.assertEqual(pages, [("AYER (JUE 22)", ["a"]), ("HOY", ["Sin pendientes registrados"]),
                                 ("BLOQUEOS", ["Ninguno"])])


if __name__ == "__main__":
    unittest.main()
