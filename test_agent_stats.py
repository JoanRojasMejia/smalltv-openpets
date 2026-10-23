import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_stats import AgentDay

TZ = timezone(timedelta(hours=-5))


def at(day, hour):
    return datetime(2026, 10, day, hour, tzinfo=TZ).timestamp()


def s(agent, session, state):
    return {"agent": agent, "session": session, "state": state}


class AgentDayTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "today.json"
        self.day = AgentDay(self.path, tz=TZ)

    def tearDown(self):
        self.tmp.cleanup()

    def test_counts_sessions_and_new_waits(self):
        self.day.observe([s("claude", "a", "working")], at(23, 9))
        self.day.observe([s("claude", "a", "waiting"), s("opencode", "b", "thinking")], at(23, 10))
        self.day.observe([s("claude", "a", "waiting"), s("opencode", "b", "waiting")], at(23, 10))  # a still waiting
        self.day.observe([s("claude", "a", "working"), s("opencode", "b", "waiting")], at(23, 11))
        self.day.observe([s("claude", "a", "waiting")], at(23, 12))
        self.assertEqual(self.day.snapshot(), {"sessions": 2, "asks": 3})

    def test_resets_at_local_midnight(self):
        self.day.observe([s("claude", "a", "waiting")], at(23, 23))
        self.day.observe([s("claude", "a", "waiting")], at(24, 0) + 60)
        self.assertEqual(self.day.snapshot(), {"sessions": 1, "asks": 0})

    def test_survives_restart(self):
        self.day.observe([s("claude", "a", "waiting")], at(23, 9))
        again = AgentDay(self.path, tz=TZ)
        again.observe([s("claude", "a", "waiting"), s("claude", "c", "idle")], at(23, 10))
        self.assertEqual(again.snapshot(), {"sessions": 2, "asks": 1})

    def test_a_new_day_after_restart_starts_from_zero(self):
        self.day.observe([s("claude", "a", "waiting")], at(23, 9))
        again = AgentDay(self.path, tz=TZ)
        again.observe([], at(24, 9))
        self.assertEqual(again.snapshot(), {"sessions": 0, "asks": 0})


class LeftoverTest(unittest.TestCase):
    def test_sessions_last_active_on_another_day_are_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            day = AgentDay(Path(d) / "t.json", tz=TZ)
            old = dict(s("claude", "old", "waiting"), ts=at(22, 18))
            day.observe([old, dict(s("claude", "new", "working"), ts=at(23, 9))], at(23, 9))
            self.assertEqual(day.snapshot(), {"sessions": 1, "asks": 0})


if __name__ == "__main__":
    unittest.main()
