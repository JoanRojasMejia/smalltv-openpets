import unittest
from datetime import datetime, timedelta, timezone

from status_board import Board, ago, summary_rows

TZ = timezone(timedelta(hours=-5))
NOW = datetime(2026, 10, 23, 10, 0, tzinfo=TZ).timestamp()


class AgoTest(unittest.TestCase):
    def test_buckets(self):
        self.assertEqual(ago(NOW - 20, NOW), "NOW")
        self.assertEqual(ago(NOW - 7 * 60, NOW), "5M")
        self.assertEqual(ago(NOW - 44 * 60, NOW), "40M")
        self.assertEqual(ago(NOW - 3 * 3600 - 100, NOW), "3H")
        self.assertEqual(ago(NOW - 2 * 86400, NOW), "2D")


class SummaryRowsTest(unittest.TestCase):
    def test_all_rows(self):
        board = Board()
        board.set("next_meeting", {"start": NOW + 3600, "local": "11:00", "title": "Daily frontend"})
        board.set("last_deploy", {"env": "SHOP-CI5", "status": "Succeeded", "ts": NOW - 600})
        board.set("last_message", {"sender": "Ana", "ts": NOW - 120})
        rows = summary_rows(board.snapshot(), NOW, tz=TZ)
        self.assertEqual(rows, [
            ("meeting", "11:00 DAILY FRONTEND"),
            ("deploy_ok", "10M SHOP-CI5"),
            ("slack", "NOW ANA"),
        ])

    def test_tomorrow_meeting_failed_deploy_and_missing_data(self):
        board = Board()
        board.set("next_meeting", {"start": NOW + 86400, "local": "09:30", "title": "Planning"})
        board.set("last_deploy", {"env": "SHOP-PROD", "status": "Failed", "ts": NOW - 7200})
        rows = summary_rows(board.snapshot(), NOW, tz=TZ)
        self.assertEqual(rows, [("meeting", "SAT 09:30 PLANNING"), ("deploy_failed", "2H SHOP-PROD")])
        self.assertEqual(summary_rows({}, NOW, tz=TZ), [])

    def test_past_meeting_is_dropped(self):
        board = Board()
        board.set("next_meeting", {"start": NOW - 60, "local": "09:59", "title": "Old"})
        self.assertEqual(summary_rows(board.snapshot(), NOW, tz=TZ), [])


class AgentsRowTest(unittest.TestCase):
    def test_agents_row_plural_and_singular(self):
        board = Board()
        board.set("agents_today", {"sessions": 4, "asks": 1})
        self.assertEqual(summary_rows(board.snapshot(), NOW, tz=TZ), [("agents", "4 AGENTS 1 ASK")])
        board.set("agents_today", {"sessions": 1, "asks": 0})
        self.assertEqual(summary_rows(board.snapshot(), NOW, tz=TZ), [("agents", "1 AGENT 0 ASKS")])
        board.set("agents_today", {"sessions": 0, "asks": 0})
        self.assertEqual(summary_rows(board.snapshot(), NOW, tz=TZ), [])


if __name__ == "__main__":
    unittest.main()
